import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

import pre_trade_gate as cli
import pretrade_gate_engine as ge
from gpw_fake_cache import standard_cache

ROOT = Path(__file__).resolve().parents[2]
TODAY = dt.date(2026, 11, 15)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


journal = _load("ptg_journal_mod", ROOT / "skills" / "gpw-trade-journal" / "scripts" / "journal.py")
sizer = _load("ptg_sizer_mod", ROOT / "skills" / "gpw-position-sizer" / "scripts" / "position_sizer.py")


def trade(**kw):
    t = {"ticker": "BANKA", "entry": 100.0, "stop": 95.0, "shares": 40, "target": None, "account_size": 50000.0,
         "account_type": "regular", "fee_pct": 0.39, "fee_min": 5.0, "thesis_id": None}
    t.update(kw)
    return t


def thesis(ticker="BANKA", status="READY", kills=("close below 90",), stop=95.0, target=None, tid=None, fills=None):
    return {"id": tid or "th_{}_20261101_abcd".format(ticker.lower()), "ticker": ticker, "status": status,
            "kill_criteria": list(kills), "plan": {"entry": 100.0, "stop": stop, "target": target},
            "fills": fills or [], "thesis": "x"}


GOOD_MARKET = {"last_close": 100.0, "last_date": "2026-11-13", "median_turnover_pln": 5_000_000.0,
               "is_stale": False, "sessions_behind": 0}


def ctx(**kw):
    c = {"market": dict(GOOD_MARKET), "sector": "WIG-BANKI", "sector_map": {"BANKA": "WIG-BANKI"},
         "theses": [thesis()], "journal_ok": True, "open_positions": [], "closed_results": [], "prices": {},
         "today": TODAY}
    c.update(kw)
    return c


def by_id(res, cid):
    return next(c for c in res["checks"] if c["id"] == cid)


# --------------------------------------------------------------------------
# arithmetic + verdict
# --------------------------------------------------------------------------

def test_trade_numbers_with_min_fee():
    n = ge.trade_numbers(trade(target=115.0))
    assert n["value"] == 4000.0
    assert n["fee_entry"] == 15.6 and n["fee_exit_stop"] == 14.82
    assert n["risk_total"] == pytest.approx(200 + 15.6 + 14.82)
    fee_t = round(40 * 115 * 0.0039, 2)
    assert n["reward_net"] == pytest.approx(600 - 15.6 - fee_t)
    assert n["rr"] == round(n["reward_net"] / n["risk_total"], 2)
    small = ge.trade_numbers(trade(shares=1))
    assert small["fee_entry"] == 5.0 and small["fee_exit_stop"] == 5.0


def test_clean_trade_is_go():
    res = ge.evaluate(trade(target=115.0), ctx())
    assert res["verdict"] == "GO", [c for c in res["checks"] if c["status"] not in ("PASS", "SKIP")]
    assert res["counts"]["FAIL"] == 0 and res["counts"]["WARN"] == 0
    assert len(res["checks"]) == 13


def test_verdict_with_warn_only():
    res = ge.evaluate(trade(target=108.0), ctx())  # ~1.3R
    assert by_id(res, "reward_risk")["status"] == "WARN"
    assert res["verdict"] == "GO WITH CONDITIONS"


@pytest.mark.parametrize("kw", [{"entry": 0}, {"account_size": -1}, {"shares": 0}, {"shares": 1.5}, {"ticker": ""},
                                {"target": 90.0}])
def test_invalid_trade(kw):
    with pytest.raises(ge.GateError):
        ge.evaluate(trade(**kw), ctx())


# --------------------------------------------------------------------------
# individual checks
# --------------------------------------------------------------------------

def test_stop_missing_and_above_entry():
    r = ge.evaluate(trade(stop=None), ctx())
    assert by_id(r, "stop")["status"] == "FAIL" and by_id(r, "risk")["status"] == "FAIL"
    assert r["verdict"] == "NO-GO"
    r = ge.evaluate(trade(stop=101.0), ctx())
    assert by_id(r, "stop")["reason"] == "stop_not_below"


def test_risk_limit_boundary():
    # 1% of 50k = 500 PLN; 90 shares x 5 = 450 + fees 35.1 + 33.35 = 518.45 -> FAIL
    assert by_id(ge.evaluate(trade(shares=90), ctx()), "risk")["status"] == "FAIL"
    assert by_id(ge.evaluate(trade(shares=86), ctx()), "risk")["status"] == "PASS"


def test_risk_limit_sources():
    ex = {"limits": {"max_risk_per_trade_pct": 0.5}}
    assert ge.risk_limit(ge.DEFAULTS, None, None) == (1.0, "default")
    assert ge.risk_limit(ge.DEFAULTS, ex, None) == (0.5, "exposure")
    assert ge.risk_limit(ge.DEFAULTS, ex, 2.0) == (0.5, "exposure")  # user cannot loosen the coach
    assert ge.risk_limit(ge.DEFAULTS, ex, 0.25) == (0.25, "user")
    assert ge.risk_limit(ge.DEFAULTS, None, 2.0) == (2.0, "user")
    r = ge.evaluate(trade(shares=60), ctx(exposure=ex))
    assert by_id(r, "risk")["status"] == "FAIL" and r["risk_limit_pct"] == 0.5


def test_position_size_limit():
    r = ge.evaluate(trade(shares=130, stop=99.0), ctx())  # 13k = 26%
    assert by_id(r, "position")["status"] == "FAIL"
    r = ge.evaluate(trade(shares=600, stop=99.9, account_size=50000.0), ctx())
    assert by_id(r, "position")["reason"] == "position_cash"


def test_liquidity():
    m = dict(GOOD_MARKET, median_turnover_pln=30000.0)  # 10% = 3000 < 4000 order
    assert by_id(ge.evaluate(trade(), ctx(market=m)), "liquidity")["status"] == "FAIL"
    m = dict(GOOD_MARKET, median_turnover_pln=0.0)
    assert by_id(ge.evaluate(trade(), ctx(market=m)), "liquidity")["reason"] == "liq_zero"
    r = ge.evaluate(trade(), ctx(market=None))
    assert by_id(r, "liquidity")["status"] == "WARN" and by_id(r, "data_freshness")["status"] == "WARN"


def test_heat_with_open_positions():
    opens = [{"ticker": "X", "shares": 100, "avg_price": 50.0, "stop": 30.0},  # risk at price 55: 2500
             {"ticker": "Y", "shares": 10, "avg_price": 10.0, "stop": None}]
    r = ge.evaluate(trade(), ctx(open_positions=opens, prices={"X": 55.0}))
    h = by_id(r, "heat")
    assert h["params"]["existing"] == 2500.0
    assert h["status"] == "WARN" and h["reason"] == "heat_no_stop"
    opens[0]["stop"] = 20.0  # risk 3500 + 230 > 3000 (6%)
    assert by_id(ge.evaluate(trade(), ctx(open_positions=opens[:1], prices={"X": 55.0})), "heat")["status"] == "FAIL"
    # stop above current price contributes nothing
    opens = [{"ticker": "X", "shares": 100, "avg_price": 50.0, "stop": 60.0}]
    assert by_id(ge.evaluate(trade(), ctx(open_positions=opens, prices={"X": 55.0})), "heat")["params"]["existing"] == 0


def test_heat_without_journal_warns():
    r = ge.evaluate(trade(), ctx(journal_ok=False, theses=[]))
    assert by_id(r, "heat")["reason"] == "heat_no_journal"


def test_sector_concentration():
    opens = [{"ticker": "BANKB", "shares": 300, "avg_price": 50.0, "stop": 45.0}]
    smap = {"BANKA": "WIG-BANKI", "BANKB": "WIG-BANKI"}
    r = ge.evaluate(trade(), ctx(open_positions=opens, prices={"BANKB": 50.0}, sector_map=smap))
    s = by_id(r, "sector")
    assert s["status"] == "FAIL" and s["params"]["pct"] == pytest.approx((15000 + 4000) / 50000 * 100)
    r = ge.evaluate(trade(), ctx(sector=None))
    assert by_id(r, "sector")["reason"] == "sector_unclassified" and by_id(r, "sector")["status"] == "PASS"


def test_thesis_required():
    r = ge.evaluate(trade(), ctx(theses=[]))
    assert by_id(r, "thesis")["status"] == "FAIL" and by_id(r, "thesis")["reason"] == "thesis_missing"
    assert r["verdict"] == "NO-GO"


def test_thesis_variants():
    t_ok = thesis()
    assert by_id(ge.evaluate(trade(), ctx(theses=[t_ok])), "thesis")["params"]["how"] == "auto"
    r = ge.evaluate(trade(thesis_id="th_banka_2026"), ctx(theses=[t_ok]))
    assert by_id(r, "thesis")["status"] == "PASS" and by_id(r, "thesis")["params"]["how"] == "id"
    assert by_id(ge.evaluate(trade(thesis_id="nope"), ctx()), "thesis")["reason"] == "thesis_not_found"
    two = [thesis(tid="th_banka_1"), thesis(tid="th_banka_2")]
    assert by_id(ge.evaluate(trade(), ctx(theses=two)), "thesis")["reason"] == "thesis_ambiguous"
    other = thesis(ticker="GAMES")
    assert by_id(ge.evaluate(trade(thesis_id=other["id"]), ctx(theses=[other])), "thesis")["reason"] == "thesis_ticker"
    closed = thesis(status="CLOSED")
    assert by_id(ge.evaluate(trade(thesis_id=closed["id"]), ctx(theses=[closed])), "thesis")["reason"] == "thesis_terminal"
    # a closed thesis is ignored by auto-discovery
    assert by_id(ge.evaluate(trade(), ctx(theses=[closed])), "thesis")["reason"] == "thesis_missing"
    nok = thesis(kills=("  ",))
    assert by_id(ge.evaluate(trade(thesis_id=nok["id"]), ctx(theses=[nok])), "thesis")["reason"] == "thesis_no_kill"
    diff = thesis(stop=90.0)
    c = by_id(ge.evaluate(trade(), ctx(theses=[diff])), "thesis")
    assert c["status"] == "WARN" and c["reason"] == "thesis_stop_differs"


def test_reward_risk_bands_and_thesis_target():
    assert by_id(ge.evaluate(trade(), ctx()), "reward_risk")["status"] == "SKIP"
    assert by_id(ge.evaluate(trade(target=120.0), ctx()), "reward_risk")["status"] == "PASS"
    assert by_id(ge.evaluate(trade(target=108.0), ctx()), "reward_risk")["status"] == "WARN"
    assert by_id(ge.evaluate(trade(target=104.0), ctx()), "reward_risk")["status"] == "FAIL"
    r = ge.evaluate(trade(), ctx(theses=[thesis(target=120.0)]))
    c = by_id(r, "reward_risk")
    assert c["status"] == "PASS" and c["params"]["src"] == "thesis" and r["trade"]["target"] == 120.0


def test_circuit_breaker_from_journal():
    def closed(pnls):
        return [{"net_pnl": p, "closed_at": "2026-10-{:02d}".format(i + 1)} for i, p in enumerate(pnls)]
    assert by_id(ge.evaluate(trade(), ctx(closed_results=closed([100, -50, -50]))), "circuit_breaker")["status"] == "PASS"
    c = by_id(ge.evaluate(trade(), ctx(closed_results=closed([100, -50, -50, -50]))), "circuit_breaker")
    assert c["status"] == "WARN" and c["params"]["streak"] == 3
    c = by_id(ge.evaluate(trade(), ctx(closed_results=closed([-10] * 5))), "circuit_breaker")
    assert c["status"] == "FAIL" and c["reason"] == "cb_pause"
    # drawdown: peak equity 50000 + 6000 = 56000 -> account 50000 -> 10.7%
    c = by_id(ge.evaluate(trade(), ctx(closed_results=closed([6000, -6000, 100]))), "circuit_breaker")
    assert c["status"] == "FAIL" and c["params"]["dd"] >= 10
    assert by_id(ge.evaluate(trade(), ctx(closed_results=[])), "circuit_breaker")["reason"] == "cb_no_history"


def test_consecutive_losses_and_drawdown_helpers():
    rows = [{"net_pnl": 5}, {"net_pnl": 0}, {"net_pnl": -1}, {"net_pnl": -2}]
    assert ge.consecutive_losses(rows) == 2
    assert ge.consecutive_losses([{"net_pnl": -1}, {"net_pnl": 0}]) == 0
    assert ge.realised_drawdown_pct([{"net_pnl": 1000}, {"net_pnl": -1000}], 10000) == pytest.approx(1000 / 11000 * 100, abs=0.01)
    assert ge.realised_drawdown_pct([{"net_pnl": 500}], 10000) == 0.0


@pytest.mark.parametrize("cb,status", [("ok", "PASS"), ("reduce", "WARN"), ("pause", "FAIL"), ("weird", "WARN")])
def test_circuit_breaker_from_exposure_takes_precedence(cb, status):
    ex = {"posture": "normal", "limits": {"circuit_breaker": cb}}
    c = by_id(ge.evaluate(trade(), ctx(exposure=ex, closed_results=[{"net_pnl": -1}] * 6)), "circuit_breaker")
    assert c["status"] == status


@pytest.mark.parametrize("state,top,status", [
    ("confirmed_uptrend", 20, "PASS"), ("uptrend_under_pressure", 50, "PASS"), ("rally_attempt", 40, "PASS"),
    ("confirmed_uptrend", 85, "WARN"), ("correction", 60, "WARN"), ("bear", 70, "WARN"), ("bear", 80, "FAIL"),
    ("sideways", 10, "WARN")])
def test_regime(state, top, status):
    reg = {"skill": "gpw-market-regime", "regime": {"state": state, "top_risk_score": top, "as_of": "2026-11-13"}}
    assert by_id(ge.evaluate(trade(), ctx(regime=reg)), "regime")["status"] == status


def test_regime_missing_is_skip_and_override_recorded():
    assert by_id(ge.evaluate(trade(), ctx()), "regime")["status"] == "SKIP"
    reg = {"regime": {"state": "bear", "top_risk_score": 90}}
    r = ge.evaluate(trade(), ctx(regime=reg, override="Hedged with put options, half size"))
    c = by_id(r, "regime")
    assert c["status"] == "WARN" and c["reason"] == "regime_overridden"
    assert c["params"]["override"].startswith("Hedged") and r["override"].startswith("Hedged")
    assert r["verdict"] == "GO WITH CONDITIONS"


def test_exposure_ceiling_and_new_positions():
    ex = {"posture": "defensive", "limits": {"exposure_ceiling_pct": 10.0, "max_new_positions_per_week": 1}}
    opens = [{"ticker": "X", "shares": 10, "avg_price": 200.0, "stop": 190.0}]
    recent = thesis(ticker="X", status="ACTIVE", tid="th_x_1",
                    fills=[{"date": "2026-11-12", "side": "buy", "shares": 10, "price": 200.0, "fee": 5}])
    r = ge.evaluate(trade(), ctx(exposure=ex, open_positions=opens, theses=[thesis(), recent]))
    assert by_id(r, "exposure_ceiling")["status"] == "WARN"  # (2000 + 4000) / 50000 = 12% > 10%
    np_ = by_id(r, "new_positions")
    assert np_["status"] == "WARN" and np_["params"]["n"] == 2
    # adding to an ACTIVE thesis is not a new position
    active = thesis(status="ACTIVE", fills=[{"date": "2026-10-01", "side": "buy", "shares": 1, "price": 100.0, "fee": 5}])
    r = ge.evaluate(trade(), ctx(exposure=ex, theses=[active]))
    assert by_id(r, "new_positions")["status"] == "PASS"
    assert by_id(ge.evaluate(trade(), ctx()), "exposure_ceiling")["status"] == "SKIP"


def test_new_positions_window():
    th = [{"fills": [{"date": "2026-11-09", "side": "buy"}]}, {"fills": [{"date": "2026-11-08", "side": "buy"}]},
          {"fills": [{"date": "2026-11-15", "side": "buy"}], "paper": True}]
    assert ge.new_positions_in_window(th, TODAY, 7) == 1


def test_freshness_issues():
    m = dict(GOOD_MARKET, is_stale=True, sessions_behind=4, last_close=80.0)
    inputs = [{"skill": "gpw-market-regime", "as_of": "2026-11-01", "sessions_behind": 9}]
    c = by_id(ge.evaluate(trade(), ctx(market=m, inputs=inputs)), "data_freshness")
    codes = [i["code"] for i in c["params"]["issues"]]
    assert c["status"] == "WARN" and codes == ["prices_stale", "entry_far", "input_stale"]
    ok_inputs = [{"skill": "gpw-market-regime", "as_of": "2026-11-12", "sessions_behind": 1}]
    assert by_id(ge.evaluate(trade(), ctx(inputs=ok_inputs)), "data_freshness")["status"] == "PASS"


# --------------------------------------------------------------------------
# CLI end-to-end (synthetic cache + real journal module)
# --------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path):
    cache = tmp_path / "cache"
    data = standard_cache(cache)
    state = tmp_path / "journal"
    return {"cache": cache, "state": state, "last": data["shares"]["BANKA"][-1], "games": data["shares"]["GAMES"][-1],
            "tmp": tmp_path}


def _run(env, *extra, capsys=None):
    argv = ["--offline", "--cache-dir", str(env["cache"]), "--state-dir", str(env["state"]),
            "--input-dir", str(env["tmp"] / "inputs"), "--output-dir", str(env["tmp"] / "out")] + list(extra)
    return cli.main(argv)


def _latest_json(env):
    return json.loads(sorted((env["tmp"] / "out").glob("gpw_pre_trade_gate_*.json"))[-1].read_text(encoding="utf-8"))


def test_cli_no_thesis_is_no_go_with_journal_command(env, capsys):
    e = round(env["last"], 2)
    rc = _run(env, "--ticker", "BANKA", "--entry", str(e), "--stop", str(round(e * 0.95, 2)), "--shares", "20",
              "--account-size", "50000")
    assert rc == 0
    out = capsys.readouterr().out
    assert "NO-GO" in out and "journal.py add --ticker BANKA" in out and "--kill" in out
    js = _latest_json(env)
    assert js["skill"] == "gpw-pre-trade-gate" and js["version"] == "1.0.0" and js["verdict"] == "NO-GO"
    assert js["data_provenance"]["as_of"] == "2026-11-13"
    assert js["sector"] == "WIG-BANKI"


def test_cli_with_journal_thesis_go(env, capsys):
    e = round(env["last"], 2)
    s = round(e * 0.95, 2)
    th = journal.new_thesis(ticker="BANKA", thesis="Margin expansion", pillars=["NII up"], kill_criteria=["close < stop"],
                            entry=e, stop=s, target=round(e * 1.15, 2), today=TODAY)
    journal.save(env["state"], th)
    # an older closed winner and an active position in another sector
    gp = round(env["games"], 2)
    g = journal.new_thesis(ticker="GAMES", thesis="Launch", pillars=["release"], kill_criteria=["delay"],
                           entry=gp, stop=round(gp * 0.9, 2), today=dt.date(2026, 9, 1))
    journal.add_fill(g, "buy", 5, gp, 5.0, dt.date(2026, 9, 1))
    journal.save(env["state"], g)
    w = journal.new_thesis(ticker="BANKB", thesis="w", pillars=["p"], kill_criteria=["k"], entry=70.0, stop=65.0,
                           today=dt.date(2026, 8, 1))
    journal.add_fill(w, "buy", 10, 70.0, 5.0, dt.date(2026, 8, 1))
    journal.close(w, day=dt.date(2026, 9, 1), exit_reason="target", followed_entry=True, followed_stop=True,
                  followed_size=True, price=80.0, fee=5.0)
    journal.save(env["state"], w)
    rc = _run(env, "--ticker", "banka", "--entry", str(e), "--stop", str(s), "--shares", "20", "--account-size", "50000",
              "--lang", "en")
    assert rc == 0
    js = _latest_json(env)
    checks = {c["id"]: c for c in js["checks"]}
    assert checks["thesis"]["status"] == "PASS" and js["thesis"]["id"] == th["id"]
    assert checks["reward_risk"]["params"]["src"] == "thesis"
    assert checks["heat"]["params"]["existing"] == pytest.approx(5 * (gp - round(gp * 0.9, 2)), abs=0.05)
    assert js["verdict"] in ("GO", "GO WITH CONDITIONS")
    assert js["counts"]["FAIL"] == 0
    assert "Verdict" in capsys.readouterr().out


def test_cli_regime_and_exposure_autodiscovery_and_override(env, capsys):
    e = round(env["last"], 2)
    s = round(e * 0.95, 2)
    journal.save(env["state"], journal.new_thesis(ticker="BANKA", thesis="t", pillars=["p"], kill_criteria=["k"],
                                                  entry=e, stop=s, today=TODAY))
    inp = env["tmp"] / "inputs"
    inp.mkdir()
    (inp / "gpw_market_regime_2026-11-13.json").write_text(json.dumps(
        {"skill": "gpw-market-regime", "regime": {"state": "bear", "top_risk_score": 88, "distribution_days": 7,
                                                  "as_of": "2026-11-13"}}), encoding="utf-8")
    (inp / "gpw_exposure_coach_2026-11-13.json").write_text(json.dumps(
        {"skill": "gpw-exposure-coach", "posture": "capital_preservation", "as_of": "2026-11-13",
         "limits": {"exposure_ceiling_pct": 20.0, "max_risk_per_trade_pct": 0.25, "max_new_positions_per_week": 1,
                    "circuit_breaker": "ok"}}), encoding="utf-8")
    (inp / "gpw_unrelated.json").write_text("not json", encoding="utf-8")
    base = ["--ticker", "BANKA", "--entry", str(e), "--stop", str(s), "--shares", "20", "--account-size", "50000"]
    assert _run(env, *base) == 0
    js = _latest_json(env)
    checks = {c["id"]: c for c in js["checks"]}
    assert checks["regime"]["status"] == "FAIL" and js["risk_limit_pct"] == 0.25
    assert {i["skill"] for i in js["inputs"]} == {"gpw-market-regime", "gpw-exposure-coach"}
    assert _run(env, *(base + ["--override", "Small starter position, thesis independent of macro"])) == 0
    js = _latest_json(env)
    assert {c["id"]: c for c in js["checks"]}["regime"]["status"] == "WARN"
    assert js["override"].startswith("Small starter")
    assert "Small starter position" in capsys.readouterr().out


def test_cli_sizer_json(env, capsys):
    e = round(env["last"], 2)
    res = sizer.calculate({"account_size": 40000.0, "ticker": "BANKA", "entry": e, "stop": round(e * 0.96, 2),
                           "target": round(e * 1.12, 2), "broker": "bossa", "max_position_pct": 25.0, "account_type": "ike"})
    p = env["tmp"] / "gpw_position_size_x.json"
    p.write_text(json.dumps(res), encoding="utf-8")
    assert _run(env, "--sizer-json", str(p)) == 0
    js = _latest_json(env)
    assert js["trade"]["shares"] == res["shares"] and js["trade"]["account_size"] == 40000.0
    assert js["trade"]["fee_pct"] == 0.38 and js["trade"]["account_type"] == "ike"
    risk = {c["id"]: c for c in js["checks"]}["risk"]
    assert risk["status"] == "PASS" and risk["params"]["risk"] == pytest.approx(res["risk"]["total"], abs=0.01)
    assert js["numbers"]["rr"] == pytest.approx(res["reward"]["r_multiple"], abs=0.01)
    # CLI value overrides the sizer's
    assert _run(env, "--sizer-json", str(p), "--shares", "1") == 0
    assert _latest_json(env)["trade"]["shares"] == 1


def test_cli_bad_sizer_and_regime_files(env):
    bad = env["tmp"] / "x.json"
    bad.write_text(json.dumps({"skill": "gpw-market-breadth"}), encoding="utf-8")
    assert _run(env, "--sizer-json", str(bad)) == 1
    assert _run(env, "--ticker", "BANKA", "--entry", "50", "--stop", "45", "--shares", "1", "--account-size", "1000",
                "--regime-json", str(bad)) == 1


def test_cli_unknown_ticker(env, capsys):
    assert _run(env, "--ticker", "BANKZ", "--entry", "50", "--stop", "45", "--shares", "1", "--account-size", "1000") == 1
    err = capsys.readouterr().err
    assert "BANKZ" in err and "BANKA" in err


def test_cli_etf_ticker(env):
    assert _run(env, "--ticker", "ETFW20", "--entry", "100", "--stop", "95", "--shares", "5", "--account-size", "50000") == 0
    js = _latest_json(env)
    assert js["market"]["kind"] == "etf" and js["sector"] is None


def test_cli_no_market_data(env, tmp_path):
    rc = cli.main(["--offline", "--cache-dir", str(tmp_path / "empty"), "--state-dir", str(env["state"]),
                   "--input-dir", str(tmp_path), "--ticker", "BANKA", "--entry", "50", "--stop", "45", "--shares", "1",
                   "--account-size", "1000", "--no-files"])
    assert rc == 0


@pytest.mark.parametrize("argv", [
    [],
    ["--ticker", "X", "--entry", "10", "--shares", "1"],
    ["--ticker", "X", "--entry", "10", "--stop", "9", "--shares", "1.5", "--account-size", "100"],
    ["--ticker", "X", "--entry", "-1", "--stop", "9", "--shares", "1", "--account-size", "100"],
    ["--ticker", "X", "--entry", "10", "--stop", "9", "--shares", "1", "--account-size", "100", "--override", "yolo"],
    ["--ticker", "X", "--entry", "10", "--stop", "9", "--shares", "1", "--account-size", "100", "--broker", "foo"],
])
def test_cli_argument_errors(argv):
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert e.value.code == 2


def test_cli_stop_above_entry_is_no_go_not_crash(env):
    assert _run(env, "--ticker", "BANKA", "--entry", "50", "--stop", "55", "--shares", "1", "--account-size", "1000") == 0
    js = _latest_json(env)
    assert js["verdict"] == "NO-GO" and {c["id"]: c for c in js["checks"]}["stop"]["reason"] == "stop_not_below"
