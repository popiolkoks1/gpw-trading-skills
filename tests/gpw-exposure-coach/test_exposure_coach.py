"""Tests for gpw-exposure-coach: input parsing, weighted combination, regime
caps, circuit breaker, portfolio heat and the CLI/JSON contract."""

import datetime as dt
import json

import pytest

import exposure_engine as ee
import gpw_exposure_coach as cli

TODAY = dt.date(2026, 11, 16)


# --------------------------------------------------------------------------
# Fixtures in the producers' real schemas
# --------------------------------------------------------------------------

def regime_json(state="confirmed_uptrend", risk=20.0, dd=2, as_of="2026-11-13"):
    return {"skill": "gpw-market-regime", "version": "1.0.0",
            "regime": {"state": state, "top_risk_score": risk, "distribution_days": dd, "as_of": as_of, "index": "WIG"}}


def breadth_json(score=70.0, as_of="2026-11-13"):
    return {"skill": "gpw-market-breadth", "composite": {"score": score, "zone": "healthy", "exposure": "75–90%"},
            "data_provenance": {"as_of": as_of}}


def sector_json(score=70.0, as_of="2026-11-13", warnings=None):
    return {"skill": "gpw-sector-trend", "composite": {"score": score, "zone": "bull", "warnings": warnings or []},
            "components": {"sector_rotation": {"signal_key": "s3_risk_on"}}, "data_provenance": {"as_of": as_of}}


def norm(name, data, stale=False):
    d = ee.PARSERS[name](data)
    d["stale"] = stale
    return d


def all_inputs(state="confirmed_uptrend", risk=20.0, b=70.0, s=70.0):
    return {"regime": norm("regime", regime_json(state, risk)), "breadth": norm("breadth", breadth_json(b)),
            "sector": norm("sector", sector_json(s))}


def closed(*pnls, start=dt.date(2026, 10, 1)):
    return [{"thesis_id": str(i), "ticker": "T{}".format(i), "closed_at": (start + dt.timedelta(days=i)).isoformat(),
             "net_pnl": p, "r_multiple": round(p / 500.0, 2), "followed_plan": True} for i, p in enumerate(pnls)]


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def test_parse_regime_score():
    d = ee.parse_regime(regime_json("confirmed_uptrend", 20))
    assert d["score"] == pytest.approx(0.6 * 85 + 0.4 * 80)
    assert d["state"] == "confirmed_uptrend" and d["as_of"] == "2026-11-13"


@pytest.mark.parametrize("name,data", [
    ("regime", {"skill": "gpw-market-breadth"}),
    ("regime", {"skill": "gpw-market-regime", "regime": {"state": "panic", "top_risk_score": 10}}),
    ("regime", {"skill": "gpw-market-regime", "regime": {"state": "bear"}}),
    ("breadth", {"skill": "gpw-market-breadth", "composite": {}}),
    ("sector", {"skill": "gpw-market-breadth", "composite": {"score": 10}}),
])
def test_parse_rejects_wrong_or_incomplete(name, data):
    with pytest.raises(ee.InputError):
        ee.PARSERS[name](data)


# --------------------------------------------------------------------------
# Combination and posture
# --------------------------------------------------------------------------

def test_combine_weights_and_renormalisation():
    c = ee.combine(all_inputs())
    assert c["effective_weights"] == {"regime": 0.4, "breadth": 0.35, "sector": 0.25}
    assert c["confidence"] == 1.0 and c["confidence_level"] == "high"
    only = ee.combine({"regime": None, "breadth": norm("breadth", breadth_json(40)), "sector": norm("sector", sector_json(60))})
    assert only["effective_weights"]["breadth"] == pytest.approx(0.583, abs=0.001)
    assert only["score"] == pytest.approx(40 * 0.35 / 0.6 + 60 * 0.25 / 0.6, abs=0.1)
    assert only["confidence"] == 0.6 and only["confidence_level"] == "medium"


def test_confidence_lower_when_stale():
    inp = all_inputs()
    inp["regime"]["stale"] = True
    c = ee.combine(inp)
    assert c["confidence"] == pytest.approx(0.8) and c["confidence_level"] == "high"
    inp["breadth"]["stale"] = True
    assert ee.combine(inp)["confidence_level"] == "medium"
    assert ee.combine({"regime": None, "breadth": None, "sector": norm("sector", sector_json(), True)})["confidence_level"] == "low"


def test_posture_and_ceiling_bands():
    assert [ee.posture_from_score(x) for x in (85, 70, 69.9, 50, 49.9, 30, 29.9, 0)] == [
        "offensive", "offensive", "normal", "normal", "defensive", "defensive", "capital_preservation", "capital_preservation"]
    assert ee.ceiling_from_score(0) == 10 and ee.ceiling_from_score(30) == 30
    assert ee.ceiling_from_score(50) == 60 and ee.ceiling_from_score(70) == 85 and ee.ceiling_from_score(95) == 100
    assert ee.ceiling_from_score(60) % 5 == 0


def test_decide_offensive_in_strong_market():
    r = ee.decide(all_inputs("confirmed_uptrend", 10, 85, 85))
    assert r["posture"] == "offensive"
    lim = r["limits"]
    assert lim == {"exposure_ceiling_pct": 100.0, "max_risk_per_trade_pct": 1.0,
                   "max_new_positions_per_week": 5, "circuit_breaker": "ok"}
    assert isinstance(lim["exposure_ceiling_pct"], float) and isinstance(lim["max_new_positions_per_week"], int)


@pytest.mark.parametrize("state,cap,posture", [
    ("uptrend_under_pressure", 80.0, "normal"),
    ("rally_attempt", 40.0, "defensive"),
    ("correction", 25.0, "defensive"),
    ("bear", 15.0, "capital_preservation"),
])
def test_regime_is_a_hard_cap(state, cap, posture):
    # breadth and sectors perfect: the regime still caps exposure and posture
    r = ee.decide(all_inputs(state, 10, 100, 100))
    assert r["limits"]["exposure_ceiling_pct"] == cap
    assert r["posture"] == posture
    codes = [c["code"] for c in r["caps_applied"]]
    assert "regime_cap" in codes


def test_regime_cap_not_applied_when_score_lower():
    r = ee.decide(all_inputs("uptrend_under_pressure", 50, 20, 20))
    assert r["limits"]["exposure_ceiling_pct"] < 80
    assert "regime_cap" not in [c["code"] for c in r["caps_applied"]]


def test_top_risk_cap():
    r = ee.decide(all_inputs("confirmed_uptrend", 72, 100, 100))
    assert r["limits"]["exposure_ceiling_pct"] == 60.0
    r = ee.decide(all_inputs("confirmed_uptrend", 90, 100, 100))
    assert r["limits"]["exposure_ceiling_pct"] == 40.0
    assert any(c["code"] == "top_risk_cap" for c in r["caps_applied"])


def test_missing_regime_noted_and_no_inputs_raises():
    r = ee.decide({"regime": None, "breadth": norm("breadth", breadth_json(60)), "sector": None})
    assert any(x["code"] == "missing_regime" for x in r["reasons"])
    with pytest.raises(ee.InputError):
        ee.decide({"regime": None, "breadth": None, "sector": None})


def test_base_risk_scaling():
    r = ee.decide(all_inputs("confirmed_uptrend", 30, 50, 50), base_risk_pct=0.8)
    assert r["posture"] == "normal"
    assert r["limits"]["max_risk_per_trade_pct"] == 0.6


# --------------------------------------------------------------------------
# Circuit breaker
# --------------------------------------------------------------------------

def test_streak_counts_from_latest():
    assert ee.losing_streak(closed(100, -1, -1)) == 2
    assert ee.losing_streak(closed(-1, -1, 100)) == 0
    assert ee.losing_streak([]) == 0
    # missing net_pnl falls back to R
    assert ee.losing_streak([{"net_pnl": None, "r_multiple": -0.5}]) == 1


def test_breaker_ok_and_reduce_on_three_losses():
    assert ee.circuit_breaker(closed(300, -100, -100), today=TODAY)["status"] == "ok"
    cb = ee.circuit_breaker(closed(300, -100, -100, -100), today=TODAY)
    assert cb["status"] == "reduce" and cb["losing_streak"] == 3
    r = ee.decide(all_inputs("confirmed_uptrend", 10, 85, 85), cb)
    assert r["limits"]["circuit_breaker"] == "reduce"
    assert r["limits"]["max_risk_per_trade_pct"] == 0.5          # halved
    assert r["limits"]["max_new_positions_per_week"] == 2         # 5 // 2


def test_breaker_pause_on_five_losses_then_reduce_after_pause():
    trades = closed(-100, -100, -100, -100, -100, start=dt.date(2026, 11, 1))   # last close 2026-11-05
    cb = ee.circuit_breaker(trades, today=dt.date(2026, 11, 10), pause_days=10)
    assert cb["status"] == "pause" and cb["pause_until"] == "2026-11-15"
    r = ee.decide(all_inputs(), cb)
    assert r["limits"]["max_risk_per_trade_pct"] == 0.0 and r["limits"]["max_new_positions_per_week"] == 0
    assert r["limits"]["circuit_breaker"] == "pause"
    cb2 = ee.circuit_breaker(trades, today=dt.date(2026, 11, 15), pause_days=10)
    assert cb2["status"] == "reduce" and cb2["pause_until"] is None
    assert cb2["reasons"][-1]["code"] == "pause_elapsed"


def test_win_resets_streak():
    cb = ee.circuit_breaker(closed(-100, -100, -100, -100, -100, 50), today=TODAY)
    assert cb["losing_streak"] == 0


def test_breaker_drawdown_pct_of_account():
    # +2000 then -6000: peak 2000, current -4000, drawdown 6000 PLN
    trades = closed(2000, -3000, 500, -3500)
    soon = dt.date(2026, 10, 6)   # within the pause window after the last close (2026-10-04)
    cb = ee.circuit_breaker(trades, account_size=54000, today=soon)
    assert cb["drawdown_pln"] == 6000
    assert cb["drawdown_pct"] == pytest.approx(10.0)      # 6000 / (54000 + 6000)
    assert cb["status"] == "pause"
    cb = ee.circuit_breaker(trades, account_size=100000, today=TODAY)
    assert cb["drawdown_pct"] == pytest.approx(5.66, abs=0.01) and cb["status"] == "reduce"
    cb = ee.circuit_breaker(trades, account_size=200000, today=TODAY)
    assert cb["status"] == "ok"


def test_breaker_drawdown_in_r_without_account():
    trades = closed(1000, -1500, 500, -2000)   # R = 2, -3, 1, -4 -> peak 2, current -4, dd 6R
    cb = ee.circuit_breaker(trades, today=TODAY)
    assert cb["drawdown_r"] == 6.0 and cb["status"] == "reduce" and cb["drawdown_pct"] is None
    cb = ee.circuit_breaker(closed(1000, -3000, 500, -3500), today=dt.date(2026, 10, 6))   # dd 13R
    assert cb["status"] == "pause" and cb["pause_until"] == "2026-10-14"


def test_breaker_empty_journal_ok():
    cb = ee.circuit_breaker([], account_size=50000, today=TODAY)
    assert cb["status"] == "ok" and cb["drawdown_pct"] == 0.0


def test_reduce_keeps_at_least_one_position_and_zero_stays_zero():
    cb = {"status": "reduce"}
    r = ee.decide(all_inputs("correction", 50, 40, 40), cb)       # defensive: 1/week
    assert r["limits"]["max_new_positions_per_week"] == 1
    r = ee.decide(all_inputs("bear", 90, 10, 10), cb)              # capital preservation: 0
    assert r["limits"]["max_new_positions_per_week"] == 0


# --------------------------------------------------------------------------
# Heat
# --------------------------------------------------------------------------

POS = [{"ticker": "PKOBP", "shares": 100, "avg_price": 60.0, "stop": 55.0},
       {"ticker": "ORLEN", "shares": 50, "avg_price": 80.0, "stop": 85.0},     # stop above price: profit locked
       {"ticker": "KGHM", "shares": 10, "avg_price": 150.0, "stop": None}]


def test_heat_from_last_close_or_entry():
    h = ee.portfolio_heat(POS, {"PKOBP": 58.0}, account_size=50000)
    rows = {r["ticker"]: r for r in h["positions"]}
    assert rows["PKOBP"]["risk_pln"] == 300.0 and rows["PKOBP"]["price_source"] == "last_close"
    assert rows["ORLEN"]["risk_pln"] == 0.0 and rows["ORLEN"]["price_source"] == "avg_price"
    assert rows["KGHM"]["risk_pln"] is None and h["no_stop"] == ["KGHM"]
    assert h["open_risk_pln"] == 300.0 and h["heat_pct"] == 0.6
    assert h["exposure_pct"] == pytest.approx(100 * (5800 + 4000 + 1500) / 50000, abs=0.1)
    assert ee.portfolio_heat(POS)["heat_pct"] is None


def test_heat_full_blocks_new_positions():
    pos = [{"ticker": "A", "shares": 1000, "avg_price": 50.0, "stop": 46.0}]   # 4000 PLN risk
    h = ee.portfolio_heat(pos, {}, account_size=50000)                           # 8% heat, 100% exposure
    r = ee.decide(all_inputs("confirmed_uptrend", 30, 50, 50), None, h)          # normal: cap 6%
    assert r["limits"]["max_new_positions_per_week"] == 0
    assert any(c["code"] == "heat_full" for c in r["caps_applied"])


def test_exposure_above_ceiling_blocks_new_positions():
    pos = [{"ticker": "A", "shares": 700, "avg_price": 50.0, "stop": 49.5}]     # 70% exposure, low heat
    h = ee.portfolio_heat(pos, {}, account_size=50000)
    r = ee.decide(all_inputs("correction", 30, 50, 50), None, h)                 # ceiling 25%
    assert r["limits"]["exposure_ceiling_pct"] == 25.0
    assert r["limits"]["max_new_positions_per_week"] == 0
    assert any(c["code"] == "above_ceiling" for c in r["caps_applied"])


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _write(d, name, data):
    p = d / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _journal(tmp_path, results=(-100, -100, -100), positions=True):
    th_dir = tmp_path / "journal" / "theses"
    th_dir.mkdir(parents=True)
    for i, p in enumerate(results):
        th = {"id": "C{}".format(i), "ticker": "T{}".format(i), "status": "CLOSED", "fills": [], "plan": {"stop": 1},
              "outcome": {"closed_at": "2026-10-{:02d}".format(i + 1), "net_pnl": p, "r_multiple": p / 100.0, "followed_plan": True}}
        _write(th_dir, th["id"] + ".json", th)
    if positions:
        th = {"id": "A1", "ticker": "NOSUCHTICKER", "status": "ACTIVE", "plan": {"stop": 45.0}, "outcome": None,
              "fills": [{"date": "2026-11-02", "side": "buy", "shares": 100, "price": 50.0, "fee": 5.0}]}
        _write(th_dir, "A1.json", th)
    return tmp_path / "journal"


def _inputs_dir(tmp_path, as_of="2026-11-13"):
    d = tmp_path / "in"
    d.mkdir()
    _write(d, "gpw_market_regime_2026-11-13_100000.json", regime_json("uptrend_under_pressure", 45, 6, as_of))
    _write(d, "gpw_market_regime_2026-11-01_100000.json", regime_json("bear", 95, 9, "2026-10-30"))   # older file
    _write(d, "gpw_breadth_2026-11-13_100000.json", breadth_json(55, as_of))
    _write(d, "gpw_sector_trend_2026-11-13_100000.json", sector_json(45, as_of, ["high_spread"]))
    return d


def _args(tmp_path, *extra):
    return ["--output-dir", str(tmp_path / "out"), "--as-of", "2026-11-16", "--cache-dir", str(tmp_path / "nocache")] + list(extra)


def test_cli_full_run_contract(tmp_path, capsys):
    d = _inputs_dir(tmp_path)
    j = _journal(tmp_path)
    rc = cli.main(_args(tmp_path, "--input-dir", str(d), "--state-dir", str(j), "--account-size", "50000", "--lang", "en"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Posture:" in out
    files = sorted((tmp_path / "out").glob("gpw_exposure_coach_*.json"))
    assert len(files) == 1 and files[0].with_suffix(".md").exists()
    r = json.loads(files[0].read_text(encoding="utf-8"))
    assert r["skill"] == "gpw-exposure-coach" and r["as_of"] == "2026-11-13"
    assert r["posture"] in ee.POSTURES
    lim = r["limits"]
    assert set(lim) == {"exposure_ceiling_pct", "max_risk_per_trade_pct", "max_new_positions_per_week", "circuit_breaker"}
    assert lim["circuit_breaker"] == "reduce"                    # 3 losses
    assert r["inputs"]["regime"]["state"] == "uptrend_under_pressure"   # newest file picked
    assert r["inputs"]["regime"]["sessions_behind"] == 1
    assert r["heat"]["positions"][0]["risk_pln"] == 500.0       # (50 - 45) x 100 from avg entry
    assert r["confidence"]["level"] == "high"
    assert lim["exposure_ceiling_pct"] <= 80.0


def test_cli_markdown_summary_first(tmp_path, capsys):
    d = _inputs_dir(tmp_path)
    for lang, head in (("pl", "## W skrócie"), ("en", "## Summary")):
        rc = cli.main(_args(tmp_path, "--input-dir", str(d), "--no-journal", "--no-files", "--lang", lang))
        assert rc == 0
        md = capsys.readouterr().out
        assert md.index(head) < md.index("##", md.index(head) + 3)   # summary is the first section
        assert cli.DISCLAIMER[lang] in md
        if lang == "en":   # the only "buy" allowed is the "not buy or sell signals" disclaimer line
            assert md.lower().count("buy") == md.lower().count("not buy or sell")


def test_cli_zero_inputs_exit_1(tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    rc = cli.main(_args(tmp_path, "--input-dir", str(tmp_path / "empty"), "--no-journal"))
    assert rc == 1
    assert "cannot assess" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_cli_explicit_missing_or_bad_file_exit_1(tmp_path):
    assert cli.main(_args(tmp_path, "--regime-json", str(tmp_path / "nope.json"), "--no-journal")) == 1
    bad = _write(tmp_path, "bad.json", {"skill": "gpw-market-breadth"})
    assert cli.main(_args(tmp_path, "--regime-json", str(bad), "--no-journal")) == 1


def test_cli_stale_and_future_inputs(tmp_path, capsys):
    d = tmp_path / "in"
    d.mkdir()
    _write(d, "gpw_breadth_2026-11-01_100000.json", breadth_json(60, "2026-10-30"))     # 11 sessions before 2026-11-16
    _write(d, "gpw_sector_trend_2026-11-20_100000.json", sector_json(60, "2026-11-20"))  # after --as-of: ignored
    rc = cli.main(_args(tmp_path, "--input-dir", str(d), "--no-journal", "--no-files", "--lang", "en"))
    assert rc == 0
    md = capsys.readouterr().out
    assert "stale" in md and "No Market regime report" in md
    rc = cli.main(_args(tmp_path, "--input-dir", str(d), "--no-journal"))
    r = json.loads(sorted((tmp_path / "out").glob("*.json"))[-1].read_text(encoding="utf-8"))
    assert r["inputs"]["breadth"]["stale"] is True
    assert r["inputs"]["sector"] == {"used": False, "reason": "future", "file": str(d / "gpw_sector_trend_2026-11-20_100000.json"),
                                     "as_of": "2026-11-20"}
    assert r["confidence"]["level"] == "low"                    # only breadth, and stale
    assert r["data_provenance"]["freshness"]["is_stale"] is True


def test_accepts_real_market_regime_output():
    """Contract test: a report produced by gpw-market-regime's own code is accepted."""
    import importlib
    import sys
    from pathlib import Path
    regime_dir = Path(__file__).resolve().parents[2] / "skills" / "gpw-market-regime" / "scripts"
    sys.path.insert(0, str(regime_dir))
    try:
        mr = importlib.import_module("gpw_market_regime")
    finally:
        sys.path.remove(str(regime_dir))
    day, sessions, closes = dt.date(2025, 1, 1), [], {"WIG": 80000.0, "WIG20": 2500.0, "MWIG40": 6000.0, "SWIG80": 24000.0}
    for i in range(300):
        while day.weekday() >= 5:
            day += dt.timedelta(days=1)
        f = 1.006 if i % 2 == 0 else 0.9975
        closes = {k: v * f for k, v in closes.items()}
        sessions.append({"date": day.isoformat(), "rows": [
            {"name": k, "currency": "", "close": v, "high": v, "low": v, "change_pct": 0.0,
             "turnover_k": 1100.0 if i % 2 == 0 else 900.0} for k, v in closes.items()]})
        day += dt.timedelta(days=1)
    rep = json.loads(json.dumps(mr.build_report(sessions, "WIG", 250, None, "en")))
    n = ee.parse_regime(rep)
    assert n["state"] == rep["regime"]["state"] == "confirmed_uptrend"
    r = ee.decide({"regime": dict(n, stale=False), "breadth": None, "sector": None})
    assert r["posture"] in ee.POSTURES


@pytest.mark.parametrize("argv", [["--account-size", "0"], ["--base-risk-pct", "5"], ["--pause-days", "0"],
                                  ["--as-of", "16-11-2026"], ["--lang", "de"]])
def test_cli_argument_errors(argv):
    with pytest.raises(SystemExit) as e:
        cli.parse_args(argv)
    assert e.value.code == 2
