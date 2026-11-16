"""Context checks of gpw-pre-trade-gate 1.1 (fed by the v1.2 skills).

Fixtures in fixtures/ are trimmed from real offline runs of gpw-event-calendar,
gpw-ownership, gpw-earnings-reaction, gpw-quality-screener, gpw-macro-regime,
gpw-dividend-analyzer and gpw-risk-lab on 2026-11-16 (data to 2026-11-13).
"""

import copy
import datetime as dt
import json
from pathlib import Path

import pytest

import pre_trade_gate as cli
import pretrade_context_checks as cc
import pretrade_gate_engine as ge
from gpwlib import reports
from gpw_fake_cache import standard_cache

FIX = Path(__file__).resolve().parent / "fixtures"
REF = dt.date(2026, 11, 13)  # last data session; entry window starts Mon 2026-11-16 (after_close)


def fx(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def rep(data, key, ref=REF, name="x.json"):
    """Wrap data like gpwlib.reports.find_report does."""
    return reports.describe(Path(name), data, data["skill"], ref, cc.CONTEXT_DEFAULTS["max_age"][key])


def modivo_reports(**over):
    r = {"events": rep(fx("events_modivo_window.json"), "events"),
         "ownership": rep(fx("ownership_modivo.json"), "ownership"),
         "earnings": rep(fx("earnings_modivo.json"), "earnings"),
         "quality": rep(fx("quality_trim.json"), "quality"),
         "macro": rep(fx("macro_trim.json"), "macro"),
         "dividend": None, "risk_lab": None}
    r.update(over)
    return r


def trade(ticker="MODIVO", entry=112.5, stop=104.0, shares=30, target=135.0):
    return {"ticker": ticker, "entry": entry, "stop": stop, "shares": shares, "target": target,
            "account_size": 100000.0, "account_type": "regular", "fee_pct": 0.39, "fee_min": 5.0, "thesis_id": None}


def run(t=None, reps=None, **ctx):
    t = t or trade()
    c = {"reports": reps if reps is not None else modivo_reports(), "reference_date": REF, "after_close": True,
         "sector": "WIG-ODZIEZ", "isins": [], "strict_events": False, "require": []}
    c.update(ctx)
    res = cc.run_context_checks(t, ge.trade_numbers(t), c)
    return {x["id"]: x for x in res["checks"]}, res["context"]


# --------------------------------------------------------------------------
# sessions / identity helpers
# --------------------------------------------------------------------------

def test_window_start_and_session_numbers():
    assert cc.window_start(REF, after_close=True) == dt.date(2026, 11, 16)
    assert cc.window_start(dt.date(2026, 11, 16), after_close=False) == dt.date(2026, 11, 16)
    assert cc.window_start(dt.date(2026, 11, 14), after_close=False) == dt.date(2026, 11, 16)  # Saturday
    s = cc.session_dates(dt.date(2026, 11, 16), 25)
    assert len(s) == 25 and dt.date(2026, 11, 21) not in s  # no weekends
    assert cc.session_no(dt.date(2026, 11, 30), s) == 11  # same numbering as gpw-event-calendar
    assert cc.session_no(dt.date(2026, 11, 22), s) == 5   # Sunday counts as the session before
    assert cc.session_no(dt.date(2026, 11, 13), s) is None
    assert cc.sessions_between(dt.date(2026, 11, 4), REF) == 6  # 11 Nov = Independence Day, no session


def test_identity_matches_isin_when_register_uses_old_name():
    ids = cc.identity("MODIVO", cc.harvest_isins("MODIVO", modivo_reports()))
    assert "PLCCC0000016" in ids["isins"]
    row = fx("ownership_modivo.json")["shorts"]["by_issuer"][0]
    assert row["ticker"] == "CCC" and cc.matches(row, ids)
    assert not cc.matches({"ticker": "KETY", "isin": "PLKETY000011"}, ids)


# --------------------------------------------------------------------------
# event_window
# --------------------------------------------------------------------------

def test_event_window_earnings_inside_window_warns_with_gap_context():
    c, cx = run()
    ew = c["event_window"]
    assert ew["status"] == "WARN" and ew["reason"] == "event_earnings_in_window"
    p = ew["params"]
    assert p["date"] == "2026-11-30" and p["session"] == 11 and p["date_kind"] == "statutory_deadline"
    assert p["window"]["start"] == "2026-11-16" and p["window"]["end"] == "2026-12-11"
    assert p["earnings_gap"]["median_abs_gap_pct"] == pytest.approx(3.44) and p["earnings_gap"]["n"] == 3
    assert p["gap_exceeds_stop"] is False
    kinds = {e["kind"] for e in p["events"]}
    assert {"earnings", "mpc_meeting", "index_revision", "msci_review"} <= kinds
    assert ew["source"]["skill"] == "gpw-event-calendar" and ew["source"]["stale"] is False
    assert cx["holding_window"]["sessions"] == 20


def test_event_window_market_events_only_is_info():
    t = trade("KETY", 1337.0, 1270.0, 3, 1480.0)
    reps = {"events": rep(fx("events_kety_window.json"), "events"), "earnings": rep(fx("earnings_kety.json"), "earnings")}
    c, _ = run(t, reps, sector=None)
    ew = c["event_window"]
    assert ew["status"] == "INFO" and ew["reason"] == "event_info_only"
    assert all(e["kind"] != "earnings" for e in ew["params"]["events"])


@pytest.mark.parametrize("hd,status,reason", [
    (5, "INFO", "event_info_only"),            # session 11 > 5 + 5
    (6, "WARN", "event_earnings_near_window"),  # 11 <= 6 + 5 buffer
    (11, "WARN", "event_earnings_in_window"),
])
def test_event_window_holding_days_and_buffer(hd, status, reason):
    c, _ = run(cfg={"holding_days": hd})
    assert (c["event_window"]["status"], c["event_window"]["reason"]) == (status, reason)


def _imminent_calendar(date="2026-11-17", kind="confirmed"):
    ev = fx("events_modivo_window.json")
    for e in ev["events"]:
        if e["type"] == "periodic_report":
            e["date"], e["date_kind"] = date, kind
    ev["by_ticker"]["MODIVO"]["next_earnings"] = None
    return rep(ev, "events")


def test_strict_events_gap_exceeds_stop_fails():
    t = trade(stop=110.25)  # stop 2% below entry, typical gap 3.44%
    reps = modivo_reports(events=_imminent_calendar())
    c, _ = run(t, reps, strict_events=True)
    ew = c["event_window"]
    assert ew["status"] == "FAIL" and ew["reason"] == "event_strict_gap_exceeds"
    assert ew["params"]["session"] == 2 and ew["params"]["gap_exceeds_stop"] is True
    # without --strict-events the same trade only warns
    c2, _ = run(t, reps)
    assert c2["event_window"]["status"] == "WARN"


def test_strict_events_gap_within_stop_warns_and_unknown_gap_fails():
    reps = modivo_reports(events=_imminent_calendar())
    c, _ = run(trade(stop=100.0), reps, strict_events=True)  # stop 11% away > 3.44% gap
    assert (c["event_window"]["status"], c["event_window"]["reason"]) == ("WARN", "event_strict_gap_ok")
    c, _ = run(trade(stop=110.25), modivo_reports(events=_imminent_calendar(), earnings=None), strict_events=True)
    assert (c["event_window"]["status"], c["event_window"]["reason"]) == ("FAIL", "event_strict_gap_unknown")
    assert "gpw_earnings_reaction.py ticker MODIVO" in c["event_window"]["command"]
    # report in session 3: strict does not apply
    c, _ = run(trade(stop=110.25), modivo_reports(events=_imminent_calendar("2026-11-18")), strict_events=True)
    assert c["event_window"]["status"] == "WARN"


def test_earnings_gap_prefers_out_of_session_reports():
    er = fx("earnings_modivo.json")
    g = cc.earnings_gap({"data": er}, cc.identity("MODIVO"))
    # 05-20 preliminary + periodic share one reaction day -> counted once; 2025-11-27 has no gap
    assert g["n"] == 3 and g["max_abs_gap_pct"] == pytest.approx(6.87) and g["all_alignments"] is False
    for r in er["reactions"]:
        r["session_alignment"] = "intraday"
    assert cc.earnings_gap({"data": er}, cc.identity("MODIVO"))["all_alignments"] is True
    assert cc.earnings_gap(None, cc.identity("MODIVO")) is None


def test_ex_dividend_from_dividend_analyzer_is_info_with_dps():
    ev = fx("events_kety_window.json")
    ev["tickers"] = ["DOMDEV"]
    ev["by_ticker"] = {"DOMDEV": dict(ev["by_ticker"]["KETY"], isin=None)}
    t = trade("DOMDEV", 250.0, 230.0, 10, 290.0)
    reps = {"events": rep(ev, "events"), "dividend": rep(fx("dividend_screen_trim.json"), "dividend")}
    c, _ = run(t, reps, sector="WIG-NRCHOM")
    ew = c["event_window"]
    assert ew["status"] == "INFO"
    exd = [e for e in ew["params"]["events"] if e["kind"] == "ex_dividend"][0]
    assert exd["date"] == "2026-11-30" and exd["dps"] == 7.35 and exd["dps_pct_of_entry"] == pytest.approx(2.94)
    assert exd["origin"] == "gpw-dividend-analyzer"
    text = cli.reason_text(ew, "pl")
    assert "dzień odcięcia dywidendy" in text and "7,35" in text


def test_no_calendar_is_unknown_but_earnings_fallback_warns():
    c, cx = run(reps=modivo_reports(events=None))
    # earnings-reaction next_expected (2027-05-20) is outside the window -> unknown, not clear
    assert c["event_window"]["status"] == "UNKNOWN" and c["event_window"]["reason"] == "event_no_calendar"
    assert "gpw_event_calendar.py window --ticker MODIVO --days 20" in c["event_window"]["command"]
    assert any(m["input"] == "events" for m in cx["missing_inputs"])
    er = fx("earnings_modivo.json")
    er["tickers"]["MODIVO"]["next_expected"]["date"] = "2026-11-25"
    c, _ = run(reps=modivo_reports(events=None, earnings=rep(er, "earnings")))
    ew = c["event_window"]
    assert ew["status"] == "WARN" and ew["params"]["events"][0]["origin"] == "gpw-earnings-reaction"


def test_calendar_for_another_ticker_or_partial_coverage_is_unknown():
    c, _ = run(trade("KETY", 1337.0, 1270.0, 3), {"events": rep(fx("events_modivo_window.json"), "events")}, sector=None)
    assert c["event_window"]["status"] == "UNKNOWN"
    ev = fx("events_kety_window.json")
    ev["events"] = []
    ev["horizon"]["company_end"] = "2026-11-27"
    c, _ = run(trade("KETY", 1337.0, 1270.0, 3), {"events": rep(ev, "events")}, sector=None)
    assert (c["event_window"]["status"], c["event_window"]["reason"]) == ("UNKNOWN", "event_partial_coverage")
    ev["horizon"]["company_end"] = "2027-01-30"
    c, _ = run(trade("KETY", 1337.0, 1270.0, 3), {"events": rep(ev, "events")}, sector=None)
    assert c["event_window"]["status"] == "PASS"


# --------------------------------------------------------------------------
# crowded_short / insider_selling
# --------------------------------------------------------------------------

def test_crowded_short_modivo_warns_via_isin():
    c, cx = run()
    cs = c["crowded_short"]
    assert cs["status"] == "WARN" and cs["reason"] == "short_crowded"
    assert cs["params"]["total_pct"] == 4.12 and cs["params"]["holders"] == 3
    assert cs["params"]["register_ticker"] == "CCC"
    text = cli.reason_text(cs, "en")
    assert "squeeze" in text and "negative drift" in text and "4.12%" in text
    assert cx["crowded_short"]["status"] == "WARN"


def test_crowded_short_present_none_and_missing():
    t = trade("KETY", 1337.0, 1270.0, 3)
    c, _ = run(t, {"ownership": rep(fx("ownership_kety.json"), "ownership")}, sector=None)
    assert (c["crowded_short"]["status"], c["crowded_short"]["reason"]) == ("PASS", "short_present")
    assert c["crowded_short"]["params"]["days_to_cover"] == 7.4
    shorts = fx("ownership_modivo.json")
    shorts["mode"], shorts["ticker"] = "shorts", None
    c, _ = run(trade("PKOBP", 120.0, 113.0, 10), {"ownership": rep(shorts, "ownership")}, sector="WIG-BANKI")
    assert (c["crowded_short"]["status"], c["crowded_short"]["reason"]) == ("PASS", "short_none")
    # an ownership report about another company does not cover this ticker
    c, _ = run(t, {"ownership": rep(fx("ownership_modivo.json"), "ownership")}, sector=None)
    assert c["crowded_short"]["status"] == "UNKNOWN" and c["insider_selling"]["status"] == "UNKNOWN"
    c, _ = run(t, {}, sector=None)
    assert c["crowded_short"]["status"] == "UNKNOWN" and "gpw_ownership.py ticker KETY" in c["crowded_short"]["command"]


def _insiders(**row):
    d = fx("ownership_kety.json")
    d["insiders"]["by_ticker"][0].update(row)
    return {"ownership": rep(d, "ownership")}


def test_insider_cluster_sell_recent_warns_old_passes():
    t = trade("KETY", 1337.0, 1270.0, 3)
    c, _ = run(t, _insiders(cluster_sell=True, sells=3, last_date="2026-10-20"), sector=None)
    assert (c["insider_selling"]["status"], c["insider_selling"]["reason"]) == ("WARN", "insider_cluster_sell")
    assert c["insider_selling"]["params"]["age_days"] == 24
    c, _ = run(t, _insiders(cluster_sell=True, sells=3, last_date="2026-08-20"), sector=None)
    assert (c["insider_selling"]["status"], c["insider_selling"]["reason"]) == ("PASS", "insider_cluster_sell_old")
    # dated parsed transactions beat the notice date
    c, _ = run(t, _insiders(cluster_sell=True, last_date="2026-11-10",
                            transactions=[{"side": "sell", "date": "2026-08-01"}]), sector=None)
    assert c["insider_selling"]["reason"] == "insider_cluster_sell_old"


def test_insider_cluster_buy_is_positive_note():
    t = trade("KETY", 1337.0, 1270.0, 3)
    c, _ = run(t, _insiders(cluster_buy=True, buys=4, distinct_insiders=3), sector=None)
    ins = c["insider_selling"]
    assert (ins["status"], ins["reason"]) == ("PASS", "insider_cluster_buy")
    assert "Lakonishok" in cli.reason_text(ins, "en")
    c, _ = run(t, {"ownership": rep(fx("ownership_kety.json"), "ownership")}, sector=None)
    assert c["insider_selling"]["reason"] == "insider_no_cluster"


# --------------------------------------------------------------------------
# earnings_reaction
# --------------------------------------------------------------------------

def test_strong_negative_reaction_within_20_sessions_warns():
    scan = rep(fx("earnings_scan_trim.json"), "earnings")
    c, _ = run(trade("ENERGOINS", 10.0, 9.2, 100, 12.0), {"earnings": scan}, sector=None)
    er = c["earnings_reaction"]
    assert (er["status"], er["reason"]) == ("WARN", "reaction_strong_negative")
    assert er["params"]["sessions_since"] == 6 and er["params"]["car"] == pytest.approx(-15.05)
    assert "Jagliński 2020" in cli.reason_text(er, "pl")
    later = rep(fx("earnings_scan_trim.json"), "earnings", ref=dt.date(2026, 12, 10))
    c, _ = run(trade("ENERGOINS", 10.0, 9.2, 100, 12.0), {"earnings": later}, sector=None, reference_date=dt.date(2026, 12, 10))
    assert (c["earnings_reaction"]["status"], c["earnings_reaction"]["reason"]) == ("PASS", "reaction_old")


def test_reaction_positive_none_and_unknown():
    c, _ = run()
    assert (c["earnings_reaction"]["status"], c["earnings_reaction"]["reason"]) == ("PASS", "reaction_strong_positive")
    # a scan covers every reporting company: absent ticker = no reaction
    c, _ = run(trade("BANKA", 50.0, 47.0, 10), {"earnings": rep(fx("earnings_scan_trim.json"), "earnings")}, sector=None)
    assert (c["earnings_reaction"]["status"], c["earnings_reaction"]["reason"]) == ("PASS", "reaction_none")
    # a ticker-mode report of another company does not
    c, _ = run(trade("BANKA", 50.0, 47.0, 10), {"earnings": rep(fx("earnings_kety.json"), "earnings")}, sector=None)
    assert c["earnings_reaction"]["status"] == "UNKNOWN"


# --------------------------------------------------------------------------
# quality / macro / stress
# --------------------------------------------------------------------------

def test_quality_negative_equity_is_prominent_warn_not_fail():
    q = rep(fx("quality_trim.json"), "quality")
    c, _ = run(trade("GRUPAAZOTY", 16.28, 15.0, 100, 20.0), {"quality": q}, sector="WIG-CHEMIA")
    qc = c["quality_red_flags"]
    assert (qc["status"], qc["reason"]) == ("WARN", "quality_negative_equity")
    assert qc["params"]["prominent"] is True and "negative_equity" in qc["params"]["flags"]
    assert qc["params"]["data_checks"] == ["equity_near_zero"]
    assert "UJEMNY KAPITAŁ" in cli.reason_text(qc, "pl")
    c, _ = run(trade("NEWAG", 30.0, 28.0, 50), {"quality": q}, sector=None)
    assert (c["quality_red_flags"]["status"], c["quality_red_flags"]["reason"]) == ("WARN", "quality_flags")
    assert c["quality_red_flags"]["params"]["high"] == ["cfo_negative_with_profit"]


def test_quality_ok_stale_period_and_missing():
    c, _ = run()
    qc = c["quality_red_flags"]
    assert qc["status"] == "PASS" and qc["params"]["stale_period"] is True and qc["params"]["f_display"] == "3/3"
    assert "stale" in cli.reason_text(qc, "en")
    c, _ = run(trade("BANKA", 50.0, 47.0, 10), {"quality": rep(fx("quality_trim.json"), "quality")}, sector=None)
    assert c["quality_red_flags"]["status"] == "UNKNOWN"
    assert "gpw_quality_screener.py ticker BANKA" in c["quality_red_flags"]["command"]


def test_macro_is_info_only():
    c, _ = run()
    m = c["macro_context"]
    assert (m["status"], m["reason"]) == ("INFO", "macro_headwind_negative_tilt")
    assert m["params"]["tilt"]["group"] == "importers_retail" and m["params"]["composite"] == "headwind"
    c, _ = run(sector=None)
    assert (c["macro_context"]["status"], c["macro_context"]["reason"]) == ("INFO", "macro_no_tilt")
    c, _ = run(sector="WIG-INFO")
    assert c["macro_context"]["reason"] == "macro_with_tilt"
    c, _ = run(reps=modivo_reports(macro=None))
    assert c["macro_context"]["status"] == "UNKNOWN"


def test_stress_breach_ok_no_tolerance_missing():
    d = fx("risklab_stress_trim.json")
    c, _ = run(reps=modivo_reports(risk_lab=rep(d, "risk_lab")))
    s = c["stress_test"]
    assert (s["status"], s["reason"]) == ("WARN", "stress_breach")
    assert s["params"]["worst_id"] == "wig_-40" and s["params"]["worst_loss_pct"] == pytest.approx(42.59)
    assert s["params"]["breaches"] == 7 and s["params"]["max_loss_pct"] == 15.0
    ok = copy.deepcopy(d)
    ok["stress"]["tolerance"] = {"max_loss_pct": 50.0, "breaches": [], "ok": True}
    c, _ = run(reps=modivo_reports(risk_lab=rep(ok, "risk_lab")))
    assert c["stress_test"]["status"] == "PASS"
    ok["stress"]["tolerance"] = {}
    c, _ = run(reps=modivo_reports(risk_lab=rep(ok, "risk_lab")))
    assert c["stress_test"]["status"] == "INFO"
    c, _ = run()
    assert c["stress_test"]["status"] == "UNKNOWN"


# --------------------------------------------------------------------------
# require / stale / verdict
# --------------------------------------------------------------------------

def test_require_turns_unknown_into_fail_only_for_listed_checks():
    c, cx = run(reps={}, require=["event_window"])
    assert c["event_window"]["status"] == "FAIL" and c["event_window"]["reason"] == "required_unknown"
    assert c["event_window"]["params"]["original_reason"] == "event_no_calendar"
    assert c["quality_red_flags"]["status"] == "UNKNOWN"
    assert "Required check" in cli.reason_text(c["event_window"], "en")
    assert cx["require"] == ["event_window"]
    c, _ = run(require=["crowded_short"])  # present -> require has no effect on WARN
    assert c["crowded_short"]["status"] == "WARN" and c["crowded_short"]["required"] is True


def test_stale_inputs_flagged_with_per_skill_limits():
    ref = dt.date(2026, 11, 27)  # 10 sessions after the reports
    events = rep(fx("events_modivo_window.json"), "events", ref=ref)
    quality = rep(fx("quality_trim.json"), "quality", ref=ref)
    assert events["stale"] is True and quality["stale"] is False  # 3 vs 65 sessions
    c, _ = run(reps=modivo_reports(events=events, quality=quality), reference_date=ref)
    assert c["event_window"]["source"]["stale"] is True
    assert "sessions old" in cli.reason_text(c["event_window"], "en")
    inputs = [{"skill": "gpw-event-calendar", "as_of": events["as_of"], "sessions_behind": events["sessions_behind"],
               "max_sessions_behind": 3},
              {"skill": "gpw-quality-screener", "as_of": quality["as_of"], "sessions_behind": quality["sessions_behind"],
               "max_sessions_behind": 65}]
    fr = ge.check_freshness({"last_close": 112.5, "is_stale": False}, trade(), inputs, ge.DEFAULTS)
    assert fr["status"] == "WARN" and [i["skill"] for i in fr["params"]["issues"]] == ["gpw-event-calendar"]


def _gate_ctx(reps, **kw):
    c = {"reports": reps, "reference_date": REF, "after_close": True, "sector": "WIG-ODZIEZ", "isins": [],
         "strict_events": False, "require": []}
    c.update(kw)
    return {"market": {"last_close": 112.5, "last_date": "2026-11-13", "median_turnover_pln": 50_000_000.0,
                       "is_stale": False, "sessions_behind": 0},
            "sector": "WIG-ODZIEZ", "sector_map": {}, "journal_ok": True, "open_positions": [], "closed_results": [],
            "prices": {}, "today": dt.date(2026, 11, 15),
            "theses": [{"id": "th_modivo_1", "ticker": "MODIVO", "status": "READY", "kill_criteria": ["close < 100"],
                        "plan": {"stop": 104.0}, "fills": []}],
            "context": c}


def test_verdicts_with_context():
    # only macro (INFO) + unknowns -> GO
    res = ge.evaluate(trade(), _gate_ctx({"macro": rep(fx("macro_trim.json"), "macro")}))
    assert res["verdict"] == "GO" and len(res["checks"]) == 20
    assert res["counts"]["INFO"] == 1 and res["counts"]["UNKNOWN"] == 6 and res["counts"]["FAIL"] == 0
    assert set(res["counts"]) == set(ge.STATUSES) and "context" in res
    # crowded short + earnings in window -> conditions
    res = ge.evaluate(trade(), _gate_ctx(modivo_reports()))
    assert res["verdict"] == "GO WITH CONDITIONS"
    assert {c["id"] for c in res["checks"] if c["status"] == "WARN"} == {"event_window", "crowded_short"}
    # strict events + imminent report with gap > stop -> NO-GO
    res = ge.evaluate(trade(stop=110.25), _gate_ctx(modivo_reports(events=_imminent_calendar()), strict_events=True))
    assert res["verdict"] == "NO-GO" and [c["id"] for c in res["checks"] if c["status"] == "FAIL"] == ["event_window"]
    # required but missing -> NO-GO
    res = ge.evaluate(trade(), _gate_ctx({}, require=["quality_red_flags"]))
    assert res["verdict"] == "NO-GO"
    # negative equity is a WARN, never a FAIL
    res = ge.evaluate(trade("GRUPAAZOTY", 16.28, 15.4, 100, 19.0),
                      dict(_gate_ctx({"quality": rep(fx("quality_trim.json"), "quality")}),
                           theses=[{"id": "th_az", "ticker": "GRUPAAZOTY", "status": "READY", "kill_criteria": ["k"],
                                    "plan": {}, "fills": []}],
                           market={"last_close": 16.28, "median_turnover_pln": 9e6, "is_stale": False}))
    assert res["verdict"] == "GO WITH CONDITIONS"


def test_engine_without_context_keeps_13_checks():
    res = ge.evaluate(trade(), dict(_gate_ctx({}), context=None))
    assert len(res["checks"]) == 13 and "context" not in res and set(res["counts"]) == {"PASS", "WARN", "FAIL", "SKIP"}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _as_banka(name, key):
    """A fixture re-labelled from MODIVO/CCC to the synthetic BANKA ticker."""
    txt = (FIX / name).read_text(encoding="utf-8").replace('"MODIVO"', '"BANKA"').replace('"CCC"', '"BANKA"')
    txt = txt.replace("PLCCC0000016", "PLBANKA00001")
    return json.loads(txt)


@pytest.fixture
def env(tmp_path):
    cache = tmp_path / "cache"
    data = standard_cache(cache)
    inp = tmp_path / "inputs"
    inp.mkdir()
    return {"cache": cache, "state": tmp_path / "journal", "inp": inp, "out": tmp_path / "out", "tmp": tmp_path,
            "last": data["shares"]["BANKA"][-1]}


def _cli(env, *extra):
    e = round(env["last"], 2)
    argv = ["--offline", "--cache-dir", str(env["cache"]), "--state-dir", str(env["state"]), "--input-dir", str(env["inp"]),
            "--output-dir", str(env["out"]), "--as-of", "2026-11-16", "--ticker", "BANKA", "--entry", str(e),
            "--stop", str(round(e * 0.95, 2)), "--shares", "20", "--account-size", "50000"] + list(extra)
    return cli.main(argv)


def _latest(env):
    return json.loads(sorted(env["out"].glob("gpw_pre_trade_gate_*.json"))[-1].read_text(encoding="utf-8"))


def _write(env, name, data):
    (env["inp"] / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_cli_autodiscovers_v12_inputs_and_reports_them(env, capsys):
    _write(env, "gpw_event_calendar_a.json", _as_banka("events_modivo_window.json", "events"))
    _write(env, "gpw_ownership_a.json", _as_banka("ownership_modivo.json", "ownership"))
    _write(env, "gpw_earnings_reaction_a.json", _as_banka("earnings_modivo.json", "earnings"))
    _write(env, "gpw_macro_regime_a.json", fx("macro_trim.json"))
    assert _cli(env, "--lang", "en") == 0
    js = _latest(env)
    assert js["version"] == "1.1.0" and len(js["checks"]) == 20
    ch = {c["id"]: c for c in js["checks"]}
    assert ch["event_window"]["status"] == "WARN" and ch["event_window"]["params"]["session"] == 11
    assert ch["crowded_short"]["status"] == "WARN"
    assert ch["macro_context"]["status"] == "INFO" and ch["macro_context"]["params"]["tilt"]["group"] == "banks"
    assert ch["quality_red_flags"]["status"] == "UNKNOWN" and ch["stress_test"]["status"] == "UNKNOWN"
    skills = {i["skill"]: i for i in js["inputs"]}
    assert set(skills) == {"gpw-event-calendar", "gpw-ownership", "gpw-earnings-reaction", "gpw-macro-regime"}
    assert all("stale" in i and "as_of" in i for i in js["inputs"])
    assert {m["input"] for m in js["context"]["missing_inputs"]} == {"quality", "dividend", "risk_lab"}
    assert js["context"]["holding_window"]["start"] == "2026-11-16"
    assert js["data_provenance"]["input_reports"]
    out = capsys.readouterr().out
    assert "Not checked (UNKNOWN)" in out and "gpw_quality_screener.py ticker BANKA" in out
    assert "Trade context" in out and "Holding window" in out


def test_cli_explicit_paths_strict_and_holding_days(env):
    ev = _as_banka("events_modivo_window.json", "events")
    for e in ev["events"]:
        if e["type"] == "periodic_report":
            e["date"], e["date_kind"] = "2026-11-17", "confirmed"
    ev["by_ticker"]["BANKA"]["next_earnings"] = None
    p = env["tmp"] / "cal.json"
    p.write_text(json.dumps(ev), encoding="utf-8")
    er = env["tmp"] / "er.json"
    er.write_text(json.dumps(_as_banka("earnings_modivo.json", "earnings")), encoding="utf-8")
    e = round(env["last"], 2)
    assert _cli(env, "--events-json", str(p), "--earnings-json", str(er), "--strict-events",
                "--stop", str(round(e * 0.98, 2))) == 0
    js = _latest(env)
    ew = {c["id"]: c for c in js["checks"]}["event_window"]
    assert ew["status"] == "FAIL" and ew["reason"] == "event_strict_gap_exceeds" and js["verdict"] == "NO-GO"
    assert js["context"]["strict_events"] is True
    assert _cli(env, "--events-json", str(p), "--holding-days", "1", "--event-buffer-sessions", "0") == 0
    ew = {c["id"]: c for c in _latest(env)["checks"]}["event_window"]
    assert ew["status"] == "PASS" and ew["reason"] == "event_clear" and ew["params"]["window"]["sessions"] == 1


def test_cli_prefers_newest_report_covering_the_ticker(env):
    own_other = fx("ownership_kety.json")
    own_other["as_of"] = "2026-11-16"  # newer, but about KETY
    _write(env, "gpw_ownership_new.json", own_other)
    _write(env, "gpw_ownership_old.json", _as_banka("ownership_modivo.json", "ownership"))
    assert _cli(env) == 0
    js = _latest(env)
    assert {c["id"]: c for c in js["checks"]}["crowded_short"]["status"] == "WARN"
    assert [i["file"] for i in js["inputs"] if i["skill"] == "gpw-ownership"] == ["gpw_ownership_old.json"]


def test_cli_stale_input_warns_in_freshness(env):
    old = _as_banka("ownership_modivo.json", "ownership")
    old["as_of"] = "2026-10-20"
    _write(env, "gpw_ownership_old.json", old)
    assert _cli(env) == 0
    js = _latest(env)
    row = [i for i in js["inputs"] if i["skill"] == "gpw-ownership"][0]
    assert row["stale"] is True and row["max_sessions_behind"] == 5
    fr = {c["id"]: c for c in js["checks"]}["data_freshness"]
    assert fr["status"] == "WARN" and any(i.get("skill") == "gpw-ownership" for i in fr["params"]["issues"])


def test_cli_require_missing_is_no_go(env):
    assert _cli(env, "--require", "event_window,quality_red_flags") == 0
    js = _latest(env)
    fails = {c["id"] for c in js["checks"] if c["status"] == "FAIL"}
    assert {"event_window", "quality_red_flags"} <= fails and js["verdict"] == "NO-GO"


def test_cli_bad_explicit_inputs_exit_1(env, tmp_path):
    wrong = tmp_path / "w.json"
    wrong.write_text(json.dumps({"skill": "gpw-market-breadth", "as_of": "2026-11-13"}), encoding="utf-8")
    assert _cli(env, "--quality-json", str(wrong)) == 1
    assert _cli(env, "--events-json", str(tmp_path / "missing.json")) == 1
    corrupt = tmp_path / "c.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert _cli(env, "--macro-json", str(corrupt)) == 1


@pytest.mark.parametrize("extra", [
    ["--require", "nonsense"],
    ["--require", ""],
    ["--holding-days", "0"],
    ["--holding-days", "x"],
    ["--event-buffer-sessions", "-1"],
])
def test_cli_new_argument_errors(extra):
    with pytest.raises(SystemExit) as e:
        cli.main(["--ticker", "X", "--entry", "10", "--stop", "9", "--shares", "1", "--account-size", "100"] + extra)
    assert e.value.code == 2


# --------------------------------------------------------------------------
# integration fix 1.2: canonical ownership tickers; shorts and insiders from separate mode reports
# --------------------------------------------------------------------------

def test_crowded_short_with_canonical_ticker_from_ownership():
    own = fx("ownership_modivo.json")
    for r in own["shorts"]["by_issuer"]:
        if r.get("isin") == "PLCCC0000016":
            r["register_name"], r["ticker"], r["days_to_cover"] = r.get("issuer"), "MODIVO", 8.88
    c, _ = run(reps=modivo_reports(ownership=rep(own, "ownership")))
    cs = c["crowded_short"]
    assert cs["status"] == "WARN" and cs["params"]["register_ticker"] == "MODIVO"
    assert cs["params"]["days_to_cover"] == 8.88


def test_ownership_shorts_and_insiders_from_separate_mode_reports(tmp_path):
    base = fx("ownership_modivo.json")
    shorts = copy.deepcopy(base)
    shorts.update({"mode": "shorts", "as_of": "2026-11-13"})
    shorts.pop("insiders", None)
    shorts.pop("ticker", None)
    ins = copy.deepcopy(base)
    ins.update({"mode": "insiders", "as_of": "2026-11-13"})
    ins.pop("shorts", None)
    ins.pop("ticker", None)
    (tmp_path / "gpw_ownership_shorts_1.json").write_text(json.dumps(shorts), encoding="utf-8")
    (tmp_path / "gpw_ownership_insiders_2.json").write_text(json.dumps(ins), encoding="utf-8")
    ids = cc.identity("MODIVO", ["PLCCC0000016"])
    newest = cli.load_input("ownership", None, tmp_path, REF, "en", ids)
    parts = cli.load_ownership_parts(newest, None, tmp_path, REF, "en", ids)
    assert parts["shorts"]["file"] == "gpw_ownership_shorts_1.json"
    assert parts["insiders"]["data"]["mode"] == "insiders"
    c, _ = run(reps=modivo_reports(ownership=newest, ownership_shorts=parts["shorts"],
                                   ownership_insiders=parts["insiders"]), isins=["PLCCC0000016"])
    assert c["crowded_short"]["status"] == "WARN"
    assert c["insider_selling"]["status"] != "UNKNOWN"


def test_insider_short_coverage_is_unknown_not_pass():
    import datetime as _dt
    import pretrade_context_checks as pcc
    rep = {"skill": "gpw-ownership", "file": "x.json", "as_of": "2026-11-16", "stale": False,
           "data": {"skill": "gpw-ownership", "mode": "insiders",
                    "insiders": {"start": "2026-11-14", "end": "2026-11-16", "by_ticker": []}}}
    ids = {"ticker": "KETY", "names": {"KETY"}, "isins": set()}
    res = pcc.check_insider_selling(ids, rep, _dt.date(2026, 11, 16), {"insider_window_days": 60}, "cmd")
    assert res["status"] == pcc.UNKNOWN and res["reason"] == "insider_short_coverage"
    assert res["params"]["covered_days"] == 2 and res["params"]["min_days"] == 30
    rep["data"]["insiders"]["start"] = "2026-09-17"
    res = pcc.check_insider_selling(ids, rep, _dt.date(2026, 11, 16), {"insider_window_days": 60}, "cmd")
    assert res["status"] == pcc.PASS and res["reason"] == "insider_none"
