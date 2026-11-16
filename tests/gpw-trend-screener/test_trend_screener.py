import json
import math

import pytest

import trend_screener_engine as eng
import gpw_trend_screener as cli
from trend_screener_fixtures import make_series, path_through, trading_dates, vcp_closes, vcp_turnover

CFG = eng.DEFAULTS


def _sd(closes, turnover=None, **kw):
    return eng.StockData(make_series("T", closes, turnover, **kw))


# --------------------------------------------------------------------------- pre-filter / RS
def test_prefilter_reasons():
    n = 300
    ok = _sd([10.0] * n, [500.0] * n)
    assert eng.prefilter(ok, n - 1, CFG)["ok"]
    illiquid = _sd([10.0] * n, [150.0] * n)
    assert eng.prefilter(illiquid, n - 1, CFG)["reasons"] == ["liquidity"]
    penny = _sd([0.8] * n, [500.0] * n)
    assert eng.prefilter(penny, n - 1, CFG)["reasons"] == ["price"]
    young = _sd([10.0] * 150, [500.0] * 150)
    pf = eng.prefilter(young, 149, CFG)
    assert "history" in pf["reasons"] and pf["short_history"]
    mid = _sd([10.0] * 230, [500.0] * 230)
    pf = eng.prefilter(mid, 229, CFG)
    assert pf["ok"] and pf["short_history"]  # 221-251 sessions: screened, flagged as short history


def test_rs_raw_weights_recent_quarter_double():
    # each quarter +10% exactly -> 2*10 + 10 + 10 + 10 = 50
    closes = [100 * (1.1 ** (k / 63)) for k in range(253)]
    raw, q = eng.rs_raw(closes, 252)
    assert q == 4 and raw == pytest.approx(50.0, rel=1e-6)
    # recent strength counts double: same 12m return, different path
    late = path_through([(0, 100.0), (189, 100.0), (63, 146.41)])
    early = path_through([(0, 100.0), (63, 146.41), (189, 146.41)])
    assert eng.rs_raw(late, 252)[0] > eng.rs_raw(early, 252)[0]


def test_rs_raw_partial_history_rescaled_and_too_short():
    closes = [100 * (1.1 ** (k / 63)) for k in range(130)]
    raw, q = eng.rs_raw(closes, 129)
    assert q == 2 and raw == pytest.approx(50.0, rel=1e-6)  # (2*10+10)/3*5
    assert eng.rs_raw(closes[:100], 99) == (None, 1)


def test_rs_ratings_range_and_order():
    raws = {"N{}".format(k): float(k) for k in range(200)}
    r = eng.rs_ratings(raws)
    assert min(r.values()) == 1 and max(r.values()) == 99
    assert r["N199"] > r["N100"] > r["N0"]
    assert eng.rating_against(sorted(raws.values()), 1000.0) == 99
    assert eng.rating_against([], 5.0) is None


def test_rs_vs_wig_uses_same_dates():
    n = 300
    dates = trading_dates(n)
    sd = _sd([100 * 1.001 ** k for k in range(n)], dates=dates)
    wig = eng.RefSeries(dates, [100.0] * n)
    v = eng.rs_vs_wig(sd, n - 1, wig)
    assert v["63"] == pytest.approx((1.001 ** 63 - 1) * 100)
    assert eng.rs_vs_wig(sd, n - 1, None)["252"] is None
    late_wig = eng.RefSeries(dates[200:], [100.0] * 100)  # benchmark history too short
    assert eng.rs_vs_wig(sd, n - 1, late_wig)["252"] is None


# --------------------------------------------------------------------------- Trend Template
def test_trend_template_uptrend_passes_downtrend_fails():
    n = 320
    up = _sd([50 * 1.003 ** k for k in range(n)])
    tt = eng.trend_template(up, n - 1, 90, CFG)
    assert tt["available"] and tt["passed"] == 8
    tt_low_rs = eng.trend_template(up, n - 1, 60, CFG)
    assert tt_low_rs["passed"] == 7
    assert [c["key"] for c in tt_low_rs["criteria"] if not c["pass"]] == ["rs_rating"]
    down = _sd([50 * 0.998 ** k for k in range(n)])
    assert eng.trend_template(down, n - 1, 10, CFG)["passed"] <= 1


def test_trend_template_unavailable_without_sma200_slope():
    sd = _sd([10.0] * 210)
    assert eng.trend_template(sd, 209, 50, CFG)["available"] is False


def test_trend_template_52w_rules():
    # strong advance then -30% from the high: fails "within 25% of high"
    closes = [50 * 1.004 ** k for k in range(300)]
    closes += path_through([(0, closes[-1]), (15, closes[-1] * 0.70)])[1:]
    sd = _sd(closes)
    tt = eng.trend_template(sd, len(sd) - 1, 90, CFG)
    near = next(c for c in tt["criteria"] if c["key"] == "near_52w_high")
    assert near["pass"] is False and tt["pct_from_high"] < -25


# --------------------------------------------------------------------------- VCP
def test_swing_points_alternate():
    closes = path_through([(0, 10.0), (10, 20.0), (10, 15.0), (10, 19.0), (10, 16.0), (10, 18.0)])
    s = make_series("Z", closes)
    zz = eng.swing_points(s.high, s.low, 0, len(closes) - 1, 5, 3)
    kinds = [p[1] for p in zz]
    assert all(a != b for a, b in zip(kinds, kinds[1:]))
    assert [p[0] for p in zz if p[1] == "H"] == [10, 30]  # the high at bar 50 is the last bar, not yet confirmed
    # outside bars / plateau: first bar of a plateau wins
    flat = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 6.0, 5.0, 4.0, 3.0, 2.0]
    s2 = make_series("F", flat)
    zz2 = eng.swing_points(s2.high, s2.low, 0, len(flat) - 1, 5, 3)
    assert [p for p in zz2 if p[1] == "H"][0][0] == 5


def test_detect_vcp_textbook(vcp_series):
    sd = eng.StockData(vcp_series)
    v = eng.detect_vcp(sd, len(sd) - 1, CFG)
    assert v["valid"], v["reasons"]
    depths = [c["depth_pct"] for c in v["contractions"]]
    assert len(depths) == 3 and depths[0] > depths[1] > depths[2]
    assert depths[0] == pytest.approx(20.5, abs=0.5) and depths[-1] < 6
    assert v["pivot"] == pytest.approx(97 * 1.003, abs=0.01)
    assert v["last_low"] == pytest.approx(92.2 * 0.997, abs=0.01)
    assert v["higher_lows"] is True
    assert v["dryup_ratio"] == pytest.approx(0.5)
    assert 80 <= v["quality"] <= 100


def test_detect_vcp_expanding_pullbacks_invalid():
    up = [50 * 2 ** (k / 259) for k in range(260)]
    base = path_through([(0, up[-1]), (8, up[-1] * 0.95), (10, up[-1] * 0.99), (10, up[-1] * 0.85), (10, up[-1] * 0.97)])
    sd = _sd(up + base[1:])
    v = eng.detect_vcp(sd, len(sd) - 1, CFG)
    assert not v["valid"]
    assert "too_few_contractions" in v["reasons"] or "last_too_loose" in v["reasons"]


def test_detect_vcp_fresh_high_is_base_too_short():
    sd = _sd([50 * 1.003 ** k for k in range(300)] + path_through([(0, 50 * 1.003 ** 299), (5, 50 * 1.003 ** 299 * 0.96)])[1:])
    v = eng.detect_vcp(sd, len(sd) - 1, CFG)
    assert not v["valid"]


def test_detect_vcp_does_not_look_ahead(vcp_series):
    """Pattern at i must not change when later bars are appended."""
    sd_full = eng.StockData(vcp_series)
    i = len(sd_full) - 4
    trunc = make_series("T", vcp_series.close[: i + 1], vcp_series.turnover_k[: i + 1])
    a = eng.detect_vcp(sd_full, i, CFG)
    b = eng.detect_vcp(eng.StockData(trunc), i, CFG)
    assert a["contractions"] == b["contractions"] and a["pivot"] == b["pivot"]


def test_merge_noise_removes_tiny_dip():
    up = [50 * 2 ** (k / 259) for k in range(260)]
    top = up[-1]
    # 20% pullback, rally with a ~1% wobble on the way, then 8% pullback
    base = path_through([(0, top), (10, top * 0.80), (8, top * 0.90), (6, top * 0.89), (8, top * 0.97),
                         (8, top * 0.89), (8, top * 0.95)])
    sd = _sd(up + base[1:])
    v = eng.detect_vcp(sd, len(sd) - 1, CFG)
    assert all(c["depth_pct"] >= CFG["min_contraction_pct"] for c in v["contractions"][:-1])
    assert v["contractions"][0]["depth_pct"] > 15


# --------------------------------------------------------------------------- state / score / band
def _state_for(final, turnover_last=None, extra=None):
    c = vcp_closes(final)
    t = vcp_turnover(len(c))
    if extra:
        c = c + extra
        t = t + [t[-1]] * len(extra)
    if turnover_last is not None:
        t[-1] = turnover_last
    sd = _sd(c, t)
    i = len(sd) - 1
    v = eng.detect_vcp(sd, i, CFG)
    return eng.execution_state(sd, i, v, CFG), v


def test_states_pre_breakout_forming_breakout():
    st, v = _state_for(95.0)
    assert st["state"] == "pre_breakout"
    st, _ = _state_for(92.6)  # inside last contraction, > 5% below pivot? 92.6/97.29 = -4.8% -> still pre_breakout
    assert st["state"] == "pre_breakout"
    st, _ = _state_for(99.0, turnover_last=3000.0)
    assert st["state"] == "breakout" and st["volume_ratio"] >= 1.4
    st, _ = _state_for(99.0, turnover_last=900.0)
    assert st["state"] == "breakout_unconfirmed"


def test_states_extended_and_damaged():
    st, _ = _state_for(104.0, turnover_last=3000.0)  # > 5% above the pivot
    assert st["state"] == "extended"
    st, v = _state_for(95.0, extra=[94.0, 91.0])  # close below the last contraction low
    assert st["state"] == "damaged"


def test_state_invalid_without_pattern():
    sd = _sd([50 * 1.002 ** k for k in range(300)])
    i = len(sd) - 1
    v = eng.detect_vcp(sd, i, CFG)
    assert eng.execution_state(sd, i, v, CFG)["state"] in ("invalid", "extended")
    assert eng.execution_state(_sd([10.0] * 30), 29, {}, CFG)["state"] == "invalid"


def test_band_caps():
    assert eng.band_for(92, "pre_breakout") == {"band_raw": "textbook", "band": "textbook", "capped": False}
    assert eng.band_for(92, "extended")["band"] == "watchlist"
    assert eng.band_for(65, "forming")["band"] == "watchlist"
    assert eng.band_for(95, "damaged")["band"] == "skip"
    assert eng.band_for(55, "breakout")["band"] == "skip"
    assert eng.band_for(75, "breakout")["band"] == "good"


def test_score_bounds_and_monotonic(vcp_series):
    sd = eng.StockData(vcp_series)
    i = len(sd) - 1
    tt = eng.trend_template(sd, i, 95, CFG)
    v = eng.detect_vcp(sd, i, CFG)
    st = eng.execution_state(sd, i, v, CFG)
    good = eng.score(tt, 95, v, st, 3000.0)
    weak = eng.score(tt, 71, v, st, 210.0)
    none = eng.score(tt, 95, {}, {}, 3000.0)
    assert 0 <= weak["score"] < good["score"] <= 100
    assert none["components"]["contraction"] == 0 and none["components"]["pivot"] == 0
    assert sum(eng.WEIGHTS.values()) == 100


def test_risk_plan_and_sizer_command(vcp_series):
    sd = eng.StockData(vcp_series)
    i = len(sd) - 1
    v = eng.detect_vcp(sd, i, CFG)
    rp = eng.risk_plan(sd, i, v, CFG)
    assert rp["stop_structure"] == pytest.approx(v["last_low"] * 0.995, abs=0.01)
    assert rp["risk_pct_structure"] == pytest.approx((v["pivot"] - rp["stop_structure"]) / v["pivot"] * 100, abs=0.02)
    assert rp["stop_atr"] < v["pivot"] and rp["atr14"] > 0
    assert rp["wide_structure"] is False
    cmd = eng.sizer_command("VCPCO", rp, None, 1.0, "pl")
    assert cmd.startswith("python3 scripts/position_sizer.py --ticker VCPCO --entry 97.29 --stop ")
    assert "--account-size ACCOUNT_PLN" in cmd
    assert "--account-size 50000" in eng.sizer_command("VCPCO", rp, 50000.0, 0.5, "en")
    assert eng.sizer_command("X", {"entry_pivot": None}, None, 1, "pl") is None
    assert eng.risk_plan(sd, i, {}, CFG)["stop_structure"] is None


# --------------------------------------------------------------------------- screen / backtest
def test_screen_finds_vcp_candidate(universe):
    data = eng.prepare(universe)
    dates = universe["VCPCO"].dates
    wig = eng.RefSeries(dates, [100.0 + 0.01 * k for k in range(len(dates))])
    sc = eng.screen_at(data, dates[-1], wig, CFG)
    assert sc["universe"] == 12
    names = [r["name"] for r in sc["candidates"]]
    assert names[0] == "VCPCO"
    top = sc["candidates"][0]
    assert top["rs"]["rating"] >= 90 and top["template_pass"]
    assert top["execution"]["state"] == "pre_breakout" and eng.is_flagged(top)
    assert top["band"] in ("good", "strong", "textbook")


def test_screen_respects_liquidity_filter(universe):
    data = eng.prepare(universe)
    cfg = dict(CFG, min_turnover_k=5000.0)
    sc = eng.screen_at(data, universe["VCPCO"].dates[-1], None, cfg)
    assert sc["universe"] == 0 and sc["candidates"] == []


def test_backtest_structure(universe):
    data = eng.prepare(universe)
    dates = universe["VCPCO"].dates
    wig = eng.RefSeries(dates, [100.0] * len(dates))
    cfg = dict(CFG, full_history=200)
    bt = eng.backtest(data, dates, wig, cfg, lookback=100, step=5, horizons=(5, 20))
    assert bt["screen_dates"] > 0
    u = bt["stats"]["universe"]["5"]
    assert u["n"] > 0 and 0 <= u["hit_rate"] <= 100
    assert u["mean_excess"] == pytest.approx(u["mean"], abs=1e-6)  # flat WIG -> excess = return
    assert set(bt["stats"]) == {"flagged", "template", "universe"}


def test_forward_return_and_stopped_trading():
    dates = trading_dates(30)
    sd = _sd([10.0] * 20 + [12.0] * 5, dates=dates[:25])
    r, stopped = eng.forward_return(sd, 0, dates[29])
    assert r == pytest.approx(20.0) and stopped is True
    r, stopped = eng.forward_return(sd, 0, dates[22])
    assert r == pytest.approx(20.0) and stopped is False


def test_summarize_empty_and_values():
    assert eng.summarize([]) == {"n": 0}
    s = eng.summarize([(10.0, 2.0), (-4.0, 1.0), (6.0, None)])
    assert s["n"] == 3 and s["hit_rate"] == pytest.approx(66.7, abs=0.1)
    assert s["mean_excess"] == pytest.approx(1.5) and s["beat_wig_rate"] == 50.0


# --------------------------------------------------------------------------- regime
def test_parse_regime_contract():
    doc = {"skill": "gpw-market-regime", "regime": {"state": "correction", "top_risk_score": 70,
                                                     "distribution_days": 6, "as_of": "2026-11-13"}}
    r = eng.parse_regime(doc)
    assert r["state"] == "correction" and r["warning_level"] == "high"
    assert eng.parse_regime(dict(doc, regime={"state": "confirmed_uptrend"}))["warning_level"] == "none"
    assert eng.parse_regime({"skill": "gpw-market-breadth"}) is None
    assert eng.parse_regime(dict(doc, regime={"state": "boom"})) is None
    assert eng.sessions_between(["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"], "2026-11-10", "2026-11-13") == 3


# --------------------------------------------------------------------------- CLI
def test_cli_argument_errors():
    for argv in (["--top", "0"], ["--ticker", "X", "--history"], ["--as-of", "2026-13-01"], ["--sessions", "100"],
                 ["--min-rs", "0"], ["--min-criteria", "5"], ["--risk-pct", "20"], ["--lang", "de"]):
        with pytest.raises(SystemExit) as e:
            cli.parse_args(argv)
        assert e.value.code == 2


@pytest.fixture
def fake_data(universe, monkeypatch):
    data = eng.prepare(universe)
    dates = universe["VCPCO"].dates
    wig = eng.RefSeries(dates, [100.0 + 0.02 * k for k in range(len(dates))])
    monkeypatch.setattr(cli, "load_data", lambda args: (data, dates, wig, {"VCPCO": "SWIG80"}, {}, len(dates), None))
    return data


def test_cli_screen_writes_files(fake_data, tmp_path, capsys):
    rc = cli.main(["--output-dir", str(tmp_path), "--lang", "en", "--input-dir", str(tmp_path), "--account-size", "40000"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "VCPCO" in out and "pre_breakout" in out and "Educational material" in out
    assert "--account-size 40000" in out
    js = list(tmp_path.glob("gpw_trend_screener_*.json"))
    assert len(js) == 1 and len(list(tmp_path.glob("gpw_trend_screener_*.md"))) == 1
    doc = json.loads(js[0].read_text(encoding="utf-8"))
    assert doc["skill"] == "gpw-trend-screener" and doc["version"] == "1.0.0" and doc["lang"] == "en"
    assert doc["data_provenance"]["survivorship_free"] is True
    assert doc["watchlist"][0]["name"] == "VCPCO"
    assert doc["regime"] is None


def test_cli_regime_warning_autodiscovered(fake_data, tmp_path, capsys):
    reg = {"skill": "gpw-market-regime", "regime": {"state": "bear", "top_risk_score": 80, "distribution_days": 7,
                                                     "as_of": fake_data["VCPCO"].dates[-1]}}
    (tmp_path / "gpw_market_regime_2026-01-01_000000.json").write_text(json.dumps(reg), encoding="utf-8")
    rc = cli.main(["--no-files", "--input-dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "bessa" in out and "⚠️" in out


def test_cli_bad_regime_file_is_error(fake_data, tmp_path, capsys):
    p = tmp_path / "x.json"
    p.write_text("{\"skill\": \"other\"}", encoding="utf-8")
    assert cli.main(["--no-files", "--regime-json", str(p)]) == 1
    assert "gpw-market-regime" in capsys.readouterr().err


def test_cli_ticker_modes(fake_data, capsys):
    assert cli.main(["--no-files", "--ticker", "vcpco", "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert "Trend Template: **8/8**" in out and "Valid pattern: 3 contractions" in out
    assert cli.main(["--no-files", "--ticker", "VCPC"]) == 1
    assert "VCPCO" in capsys.readouterr().err  # suggestion


def test_cli_history_mode(fake_data, capsys, monkeypatch):
    monkeypatch.setitem(eng.DEFAULTS, "full_history", 200)
    assert cli.main(["--no-files", "--history", "--history-sessions", "100", "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert "historical test" in out and "survivorship-free" in out


def test_cli_offline_empty_cache_exit_1(tmp_path, capsys):
    rc = cli.main(["--offline", "--cache-dir", str(tmp_path), "--no-files", "--as-of", "2026-11-13", "--sessions", "260"])
    assert rc == 1
    assert "ERROR" in capsys.readouterr().err
