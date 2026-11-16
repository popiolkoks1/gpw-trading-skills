"""Tests for gpw-research-dossier (collector, staleness, runner, debate guardrail, thesis command, CLI).

Fixtures in fixtures/reports/ are trimmed copies of real offline runs of the
producer skills for KETY and PKOBP (data as of 2026-11-13, macro/events
2026-11-16). No network is used anywhere.
"""

import copy
import datetime as dt
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import dossier_debate as dd
import dossier_engine as de
import dossier_runner as dr
import gpw_research_dossier as cli

FIX = Path(__file__).resolve().parent / "fixtures" / "reports"
REPO = Path(__file__).resolve().parents[2]
REF = "2026-11-16"


def build(tmp_path, ticker, *extra, input_dir=None, state=True):
    args = ["build", ticker, "--input-dir", str(input_dir or FIX), "--no-files", "--json", "--as-of", REF]
    if state:
        args += ["--state-dir", str(tmp_path / "nojournal"), "--watchlist", str(tmp_path / "nowatch.json")]
    args += list(extra)
    ns = cli.parse_args(args)
    return cli.build(ns)


def facts(d):
    return {f["id"]: f["value"] for f in d["facts"]}


# --------------------------------------------------------------------------
# Collection from real-output fixtures
# --------------------------------------------------------------------------

def test_kety_collects_all_relevant_sections(tmp_path):
    d = build(tmp_path, "KETY")
    assert d["skill"] == "gpw-research-dossier" and d["kind"] == "dossier" and d["ticker"] == "KETY"
    assert set(d["sections"]) >= {"gpw-trend-screener", "gpw-quality-screener", "gpw-valuation",
                                  "gpw-dividend-analyzer", "gpw-ownership", "gpw-event-calendar",
                                  "gpw-earnings-reaction", "gpw-risk-lab", "gpw-market-regime",
                                  "gpw-exposure-coach", "gpw-market-breadth", "gpw-sector-trend",
                                  "gpw-macro-regime"}
    f = facts(d)
    assert f["trend.template_passed"] == 8
    assert f["trend.execution_state"] == "breakout"
    assert f["valuation.implied_growth_pct"] == pytest.approx(10.24, abs=0.01)
    assert f["ownership.short_total_pct"] == pytest.approx(1.5)
    assert f["ownership.short_in_register"] is True
    assert f["quality.roe_pct"] == pytest.approx(37.0, abs=0.1)
    assert f["dividend.payout_ratio_pct"] == pytest.approx(61.0)
    assert f["regime.state"] == "confirmed_uptrend"
    assert f["exposure.max_risk_per_trade_pct"] == 0.75
    # KETY belongs to no GPW sector index -> pairs not applicable, no sector-row facts
    assert d["sector"]["key"] is None
    assert any(m["skill"] == "gpw-pair-trading" and m["reason"] == "no_sector" for m in d["not_applicable"])
    assert "sector.uptrend_ratio_pct" not in f
    assert d["missing"] and all(m["optional"] for m in d["missing"])


def test_pkobp_sector_tilt_pairs_and_no_shorts(tmp_path):
    d = build(tmp_path, "PKOBP")
    f = facts(d)
    assert d["sector"] == {"key": "WIG-BANKI", "source": "gpw-valuation"}
    assert f["sector.rank"] == 1 and f["sector.trend"] == "up"
    assert f["macro.sector_tilt_group"] == "banks"
    assert f["macro.sector_tilt"] == "slightly_negative"
    assert f["pairs.pairs_with_ticker"] == 9 and f["pairs.cointegrated"] == 0
    assert f["pairs.best_peer"] == "ALIOR"
    # not in the KNF register -> 0, explicitly marked
    assert f["ownership.short_total_pct"] == 0.0 and f["ownership.short_in_register"] is False
    assert f["valuation.implied_roe_pct"] == pytest.approx(30.08, abs=0.01)
    assert "ROE" in d["sections"]["gpw-valuation"]["summary"]


def test_fact_ids_unique_and_well_formed(tmp_path):
    for t in ("KETY", "PKOBP"):
        d = build(tmp_path, t)
        ids = [f["id"] for f in d["facts"]]
        assert len(ids) == len(set(ids))
        assert all(i.count(".") == 1 and i.split(".")[0] in de.SHORT_TO_SKILL for i in ids)
        assert all(f["section"] in d["sections"] for f in d["facts"])
        assert len(ids) > 100


def test_contract_fields(tmp_path):
    d = build(tmp_path, "KETY")
    for k in ("skill", "version", "lang", "as_of", "ticker", "sections", "missing", "debate", "facts",
              "data_provenance"):
        assert k in d
    for s in d["sections"].values():
        for k in ("skill", "as_of", "stale", "summary", "key_fields", "source_file"):
            assert k in s
        assert isinstance(s["summary"], str) and s["summary"]
    assert set(d["debate"]) >= {"bull", "bear", "verdict_inputs"}
    dp = d["data_provenance"]
    assert dp["as_of_min"] == "2026-11-13" and dp["as_of_max"] == "2026-11-16"
    assert dp["stale_sections"] == []


def test_summaries_are_deterministic_and_bilingual(tmp_path):
    a = build(tmp_path, "KETY")
    b = build(tmp_path, "KETY")
    assert [s["summary"] for s in a["sections"].values()] == [s["summary"] for s in b["sections"].values()]
    en = build(tmp_path, "KETY", "--lang", "en")
    assert "price implies FCF growth" in en["sections"]["gpw-valuation"]["summary"]
    assert "cena zakłada wzrost FCF" in a["sections"]["gpw-valuation"]["summary"]


# --------------------------------------------------------------------------
# Staleness and missing commands
# --------------------------------------------------------------------------

def test_staleness_per_section_limit_and_order(tmp_path):
    ns = cli.parse_args(["build", "KETY", "--input-dir", str(FIX), "--no-files", "--as-of", "2026-11-20",
                         "--state-dir", str(tmp_path / "j"), "--watchlist", str(tmp_path / "w.json")])
    d = cli.build(ns)
    secs = d["sections"]
    # 2026-11-13 -> 2026-11-20 = 4-5 sessions (the reference day counts once its session is final,
    # gpwlib.provenance): daily skills (limit 3) stale, fundamentals (limit 10) fresh
    assert secs["gpw-market-regime"]["stale"] and secs["gpw-market-regime"]["sessions_behind"] in (4, 5)
    assert secs["gpw-trend-screener"]["stale"]
    assert not secs["gpw-quality-screener"]["stale"]
    assert not secs["gpw-macro-regime"]["stale"]           # as of 11-16: 4 sessions, limit 5
    order = list(secs)
    first_fresh = next(i for i, k in enumerate(order) if not secs[k]["stale"])
    assert all(secs[k]["stale"] for k in order[:first_fresh])
    assert all(not secs[k]["stale"] for k in order[first_fresh:])
    assert d["refresh"] and all("python3 scripts/" in r["command"] for r in d["refresh"])
    stale_facts = [f for f in d["facts"] if f["stale"]]
    assert stale_facts and all(f["section"] in d["stale_sections"] for f in stale_facts)
    md = cli.render_dossier(d, "pl")
    assert md.index("Nieaktualne sekcje") < md.index("## Sekcje")


def test_max_age_override(tmp_path):
    d = build(tmp_path, "KETY", "--max-age-sessions", "0")
    assert d["sections"]["gpw-valuation"]["max_sessions_behind"] == 0


def test_report_without_as_of_is_stale():
    spec = de.SPEC_BY_SKILL["gpw-market-regime"]
    sec = de.build_section(spec, {"as_of": None, "path": "x.json", "data": {}}, {"summary": "s", "key_fields": {}},
                           dt.date(2026, 11, 16), None)
    assert sec["stale"] is True and sec["sessions_behind"] is None


def test_empty_dir_lists_every_gap_with_exact_command(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    d = build(tmp_path, "PKOBP", "--offline", "--sector", "WIG-BANKI", input_dir=empty)
    assert d["sections"] == {} and d["facts"] == []
    req = {m["skill"]: m for m in d["missing"] if not m["optional"]}
    assert set(req) == {s["skill"] for s in de.SPECS if not s.get("optional")}
    v = req["gpw-valuation"]["command"]
    assert "gpw-valuation" in v and "scripts/gpw_valuation.py PKOBP" in v and "--offline" in v
    assert "--input-dir {}".format(empty) in v or "--input-dir {}".format(empty.resolve()) in v
    assert "--offline" not in req["gpw-exposure-coach"]["command"]     # coach has no --offline flag
    assert "ticker PKOBP" in req["gpw-quality-screener"]["command"]
    pairs = next(m for m in d["missing"] if m["skill"] == "gpw-pair-trading")
    assert "--sector WIG-BANKI" in pairs["command"]
    assert any(m["skill"] == "gpw-risk-lab" for m in d["not_applicable"])   # no journal position


def test_ticker_not_in_reports(tmp_path):
    d = build(tmp_path, "CDPROJEKT")
    reasons = {m["skill"]: m["reason"] for m in d["missing"]}
    assert reasons["gpw-trend-screener"] == "ticker_not_in_report"
    assert reasons["gpw-valuation"] == "ticker_not_in_report"
    assert "gpw-market-regime" in d["sections"]        # market-level sections still apply


def test_broken_report_is_recorded_not_fatal(tmp_path):
    folder = tmp_path / "in"
    shutil.copytree(FIX, folder)
    (folder / "broken_quality.json").write_text(json.dumps(
        {"skill": "gpw-quality-screener", "as_of": "2026-11-14", "companies": {"KETY": 1}}), encoding="utf-8")
    (folder / "corrupt.json").write_text("{not json", encoding="utf-8")
    d = build(tmp_path, "KETY", input_dir=folder)
    assert any(e["skill"] == "gpw-quality-screener" for e in d["errors"])
    assert "gpw-quality-screener" in d["sections"]   # older valid report used instead


def test_newest_covering_report_wins(tmp_path):
    folder = tmp_path / "in"
    shutil.copytree(FIX, folder)
    old = json.loads((FIX / "gpw_valuation_KETY.json").read_text(encoding="utf-8"))
    old["as_of"] = "2026-11-05"
    old["implied_growth_pct"] = 1.0
    (folder / "old_val.json").write_text(json.dumps(old), encoding="utf-8")
    d = build(tmp_path, "KETY", input_dir=folder)
    assert facts(d)["valuation.implied_growth_pct"] == pytest.approx(10.24, abs=0.01)


def test_screen_mode_trend_entry_and_others(tmp_path):
    rep = {"skill": "gpw-trend-screener", "mode": "screen", "candidates": [],
           "watchlist": [{"name": "XYZ", "close": 10, "template": {"passed": 8, "total": 8}, "rs": {"rating": 90},
                          "execution": {"state": "pre_breakout", "pct_vs_pivot": -2.0}, "vcp": {"valid": True, "pivot": 10.2},
                          "risk": {"stop_structure": 9.5, "risk_pct_structure": 6.9}}],
           "others": [{"name": "ABC", "state": "extended", "rs": 81, "score": 40, "vcp_reasons": ["no_base"]}]}
    r = de.ex_trend(rep, "XYZ", {}, "en")
    assert r["key_fields"]["found_in"] == "watchlist" and r["key_fields"]["pivot"] == 10.2
    r2 = de.ex_trend(rep, "ABC", {}, "en")
    assert r2["key_fields"]["execution_state"] == "extended" and r2["key_fields"]["rs_rating"] == 81
    assert de.ex_trend(rep, "NOPE", {}, "en") is None


def _rrg_report(as_of="2026-11-13", mode="indices", sector=None, items=None):
    items = items if items is not None else [
        {"id": "WIG-BANKI", "label": "Banks", "quadrant": "weakening", "rs_ratio": 101.4, "rs_momentum": 99.2,
         "heading_deg": 250.0, "distance": 1.6, "concentrated": True,
         "transitions": [{"date": "2026-11-06", "from": "leading", "to": "weakening", "clockwise": True}]},
        {"id": "WIG-ODZIEZ", "label": "Apparel", "quadrant": "improving", "rs_ratio": 98.1, "rs_momentum": 101.0,
         "heading_deg": 40.0, "distance": 2.2, "concentrated": False, "transitions": []}]
    return {"skill": "gpw-sector-trend", "version": "1.0.0", "mode": "rrg", "as_of": as_of,
            "rrg": {"benchmark": "WIG", "as_of": as_of, "mode": mode, "sector": sector, "items": items}}


def test_rrg_read_from_real_schema():
    # integration fix 1.2: the RRG report is gpw_sector_rrg_*.json with "mode": "rrg"
    data = json.loads((FIX / "gpw_sector_trend_2026-11-13.json").read_text(encoding="utf-8"))
    rrg = [{"path": "gpw_sector_rrg_x.json", "as_of": "2026-11-13", "data": _rrg_report()},
           {"path": "gpw_sector_rrg_s.json", "as_of": "2026-11-13",
            "data": _rrg_report(mode="stocks", sector="WIG-BANKI",
                                items=[{"id": "PKOBP", "quadrant": "leading", "rs_ratio": 102.0, "rs_momentum": 100.5,
                                        "transitions": []}])}]
    r = de.ex_sector(data, "PKOBP", {"sector": "WIG-BANKI", "rrg": rrg}, "en")
    kf = r["key_fields"]
    assert kf["rrg_quadrant"] == "weakening" and kf["rrg_rs_ratio"] == 101.4 and kf["rrg_rs_momentum"] == 99.2
    assert kf["rrg_last_transition"] == "leading -> weakening (2026-11-06)" and kf["rrg_concentrated"] is True
    assert kf["rrg_stock_quadrant"] == "leading" and "RRG weakening" in r["summary"]
    # no RRG report -> no quadrant, and the old guessed layouts are not read any more
    data["sectors"][0]["rrg"] = {"quadrant": "leading"}
    assert de.ex_sector(data, "PKOBP", {"sector": "WIG-BANKI"}, "en")["key_fields"]["rrg_quadrant"] is None


def test_rrg_report_never_read_as_classic_sector_trend(tmp_path):
    # both reports carry "skill": "gpw-sector-trend"; the RRG one is newer
    classic = json.loads((FIX / "gpw_sector_trend_2026-11-13.json").read_text(encoding="utf-8"))
    classic["as_of"] = "2026-11-12"
    (tmp_path / "gpw_sector_trend_2026-11-12.json").write_text(json.dumps(classic), encoding="utf-8")
    (tmp_path / "gpw_sector_rrg_2026-11-13.json").write_text(json.dumps(_rrg_report()), encoding="utf-8")
    by = cli.load_reports(str(tmp_path), [], None)
    assert [r["file"] for r in by["gpw-sector-trend"]] == ["gpw_sector_trend_2026-11-12.json"]
    assert [r["file"] for r in by[de.RRG_KEY]] == ["gpw_sector_rrg_2026-11-13.json"]
    col = de.collect("PKOBP", by, dt.date(2026, 11, 13), "en", sector="WIG-BANKI")
    sec = col["sections"]["gpw-sector-trend"]
    assert sec["key_fields"]["market_score"] is not None and sec["key_fields"]["rrg_quadrant"] == "weakening"
    # an explicit --report of the RRG file is filed as RRG too
    by2 = cli.load_reports(str(tmp_path / "none"), [str(tmp_path / "gpw_sector_rrg_2026-11-13.json")], None)
    assert de.RRG_KEY in by2 and "gpw-sector-trend" not in by2


def test_ownership_insiders_mode_and_other_ticker():
    rep = {"skill": "gpw-ownership", "mode": "insiders",
           "insiders": {"by_ticker": [{"ticker": "KRUK", "buys": 3, "sells": 0, "distinct_insiders": 3,
                                       "cluster_buy": True, "basis": "parsed_transactions", "net_value_pln": 1.2e6}]}}
    r = de.ex_ownership(rep, "KRUK", {}, "pl")
    assert r["key_fields"]["insider_cluster_buy"] is True and "short_total_pct" not in r["key_fields"]
    assert de.ex_ownership(rep, "PKOBP", {}, "pl") is None
    assert de.ex_ownership({"mode": "ticker", "ticker": "KRUK"}, "PKOBP", {}, "pl") is None


def test_sessions_until():
    assert de.sessions_until(dt.date(2026, 11, 13), dt.date(2026, 11, 20)) == 5
    assert de.sessions_until(dt.date(2026, 11, 20), dt.date(2026, 11, 13)) == -5
    assert de.sessions_until(dt.date(2026, 11, 13), dt.date(2026, 11, 13)) == 0


# --------------------------------------------------------------------------
# Personal sections
# --------------------------------------------------------------------------

def _write_journal(state: Path):
    (state / "theses").mkdir(parents=True)
    th = {"id": "th_kety_20261110_abcd", "ticker": "KETY", "status": "READY", "thesis": "x", "pillars": ["a"],
          "kill_criteria": ["close below 1200"], "plan": {"entry": 1290, "stop": 1235, "target": 1500},
          "review_date": "2026-12-10", "conviction": "M", "fills": [], "created": "2026-11-10"}
    (state / "theses" / "th_kety.json").write_text(json.dumps(th), encoding="utf-8")


def test_journal_and_watchlist_sections(tmp_path):
    _write_journal(tmp_path / "journal")
    wl = tmp_path / "watchlist.json"
    wl.write_text(json.dumps({"schema_version": "1.0", "items": [
        {"ticker": "KETY", "stage": "ready", "pivot": 1281, "stop": 1229, "added": "2026-11-01",
         "sources": [{"skill": "gpw-trend-screener", "kind": "vcp", "date": "2026-11-13"}], "tags": ["vcp"]}]}),
        encoding="utf-8")
    ns = cli.parse_args(["build", "KETY", "--input-dir", str(FIX), "--no-files", "--as-of", REF,
                         "--state-dir", str(tmp_path / "journal"), "--watchlist", str(wl)])
    d = cli.build(ns)
    f = facts(d)
    assert f["journal.status"] == "READY" and f["journal.kill_criteria_count"] == 1
    assert f["watchlist.stage"] == "ready" and f["watchlist.pivot"] == 1281
    assert "gpw-trend-screener:vcp:2026-11-13" in f["watchlist.sources"]
    sz = d["next_steps"]["position_sizer"]
    # the user's own plan (journal) wins over computed levels
    assert sz["entry"] == 1290 and sz["stop"] == 1235 and "journal.entry" in sz["fact_ids"]
    assert sz["risk_pct"] == 0.75 and "--risk-pct 0.75" in sz["command"]
    assert d["missing"] == []


def test_watchlist_section_follows_contract():
    # integration fix 1.2: fields of skills/gpw-watchlist/references/watchlist-contract.md
    items = [{"ticker": "CCC", "stage": "entered", "added": "2026-01-10", "updated": "2026-11-13", "source": "user",
              "sources": [], "pivot": 120.0, "stop": 105.0, "levels_source": "user", "levels_date": "2026-11-01",
              "thesis_id": "T1", "thesis_status": "ACTIVE", "tags": ["vcp", "short_crowded"],
              "alerts": [{"code": "ticker_renamed", "severity": "medium", "origin": "refresh",
                          "params": {"new_ticker": "MODIVO"}},
                         {"code": "stop_breached", "severity": "high", "origin": "refresh"}],
              "market": {"date": "2026-11-13", "close": 104.0, "pivot_state": "below", "dist_to_pivot_pct": -13.3,
                         "dist_to_stop_pct": -1.0},
              "screener": {"score": 71, "state": "damaged"}, "upcoming_events": [{"type": "agm"}],
              "future_field": {"ignored": True}}]
    r = de.watchlist_section(items, "MODIVO", "en")
    kf = r["key_fields"]
    assert kf["stage"] == "entered" and kf["active"] is True and kf["stored_ticker"] == "CCC"
    assert kf["high_alerts"] == ["stop_breached"] and kf["risk_tags"] == ["short_crowded"]
    assert kf["thesis_status"] == "ACTIVE" and kf["levels_source"] == "user" and kf["pivot_state"] == "below"
    assert kf["upcoming_events"] == 1 and kf["screener_state"] == "damaged"
    assert "urgent alerts: stop_breached" in r["summary"]
    # minimal item: missing optional fields read as null / []
    m = de.watchlist_section([{"ticker": "KETY", "stage": "watch"}], "KETY", "pl")["key_fields"]
    assert m["pivot"] is None and m["tags"] == [] and m["high_alerts"] == [] and m["updated"] is None


def test_watchlist_location_and_schema_major(tmp_path, monkeypatch):
    d = tmp_path / "wl"
    d.mkdir()
    (d / "watchlist.json").write_text(json.dumps({"schema_version": "1.3", "items": [{"ticker": "KETY"}]}),
                                      encoding="utf-8")
    monkeypatch.setenv("GPW_WATCHLIST_DIR", str(d))
    items, path, warn = cli.load_watchlist(None)
    assert items == [{"ticker": "KETY"}] and path.endswith("watchlist.json") and warn is None
    assert cli.load_watchlist(str(d))[0] == [{"ticker": "KETY"}]  # a state dir is accepted too
    (d / "watchlist.json").write_text(json.dumps({"schema_version": "2.0", "items": [{"ticker": "KETY"}]}),
                                      encoding="utf-8")
    items, _, warn = cli.load_watchlist(None)
    assert items is None and "schema_version 2.0" in warn


def test_watchlist_unreadable_is_a_warning(tmp_path):
    wl = tmp_path / "w.json"
    wl.write_text("{broken", encoding="utf-8")
    ns = cli.parse_args(["build", "KETY", "--input-dir", str(FIX), "--no-files", "--as-of", REF,
                         "--state-dir", str(tmp_path / "j"), "--watchlist", str(wl)])
    d = cli.build(ns)
    assert d["warnings"] and "watchlist" in d["warnings"][0]


# --------------------------------------------------------------------------
# Runner with a fake sibling skill
# --------------------------------------------------------------------------

FAKE_REGIME = r'''
import json, sys, time, os
a = sys.argv
out = a[a.index("--output-dir") + 1]
if os.environ.get("NEVER"): pass
json.dump({"skill": "gpw-market-regime", "version": "1.0.0", "as_of": "2026-11-13",
           "regime": {"state": "correction", "top_risk_score": 71.0, "distribution_days": 7, "as_of": "2026-11-13"}},
          open(os.path.join(out, "gpw_market_regime_fake.json"), "w"))
print("ok", " ".join(a[1:]))
'''


def _fake_skills(root: Path):
    for skill, body in (("gpw-market-regime", FAKE_REGIME),
                        ("gpw-market-breadth", "import sys\nsys.stderr.write('boom: no cache')\nsys.exit(1)\n"),
                        ("gpw-sector-trend", "import time\ntime.sleep(5)\n")):
        sd = root / skill / "scripts"
        sd.mkdir(parents=True)
        (sd / dr.CLI[skill]["script"].split("/")[-1]).write_text(body, encoding="utf-8")


def test_runner_runs_fake_sibling_and_survives_failures(tmp_path):
    root = tmp_path / "skills"
    _fake_skills(root)
    inp = tmp_path / "in"
    inp.mkdir()
    d = build(tmp_path, "KETY", "--run", "--offline", "--skills-dir", str(root), "--timeout", "1",
              "--only", "gpw-market-regime,gpw-market-breadth,gpw-sector-trend,gpw-macro-regime", input_dir=inp)
    st = {r["skill"]: r for r in d["runs"]}
    assert st["gpw-market-regime"]["status"] == "ok"
    assert st["gpw-market-breadth"]["status"] == "failed" and "boom" in st["gpw-market-breadth"]["stderr_tail"]
    assert st["gpw-sector-trend"]["status"] == "timeout"
    assert st["gpw-macro-regime"]["status"] == "not_installed"
    # order: breadth, sector before regime (regime reads breadth)
    order = [r["skill"] for r in d["runs"]]
    assert order.index("gpw-market-breadth") < order.index("gpw-market-regime")
    assert "--offline" in st["gpw-market-regime"]["command"]
    assert d["sections"]["gpw-market-regime"]["key_fields"]["state"] == "correction"
    assert facts(d)["regime.state"] == "correction"
    assert "gpw-market-breadth" in {m["skill"] for m in d["missing"]}


def test_runner_skips_non_runnable_and_reports_display():
    c = dr.command_for("gpw-pair-trading", "KETY", "/x", sector=None)
    assert c["runnable"] is False and c["reason"] == "no_sector"
    log = dr.run_commands([c], 1.0)
    assert log[0]["status"] == "skipped"
    c2 = dr.command_for("gpw-valuation", "KETY", "/x y", lang="en", offline=True, as_of="2026-11-13",
                        python=sys.executable)
    line = dr.display(c2)
    assert line.split(" && ")[1].startswith("python3 scripts/gpw_valuation.py KETY")
    assert "'/x y'" in line and "--as-of 2026-11-13" in line
    assert dr.command_for("gpw-unknown", "KETY", "/x") is None


def test_sibling_dirs_resolve_in_repo():
    for skill, spec in dr.CLI.items():
        assert (dr.skill_dir(skill) / spec["script"]).is_file(), skill


# --------------------------------------------------------------------------
# Debate: template, validation, thesis
# --------------------------------------------------------------------------

@pytest.fixture
def dossier(tmp_path):
    return build(tmp_path, "KETY")


def filled(dossier):
    t = dd.make_template(dossier, "/tmp/dossier.json", "pl")
    t["bear"] = [
        {"claim": "Cena wymaga wzrostu FCF ok. 10% rocznie przez dekadę", "fact_ids": ["valuation.implied_growth_pct"]},
        {"claim": "Dywidenda była cięta dwa razy w ostatnich latach", "fact_ids": ["dividend.cuts", "dividend.trap_flags"]},
        {"claim": "Szeroki rynek słabnie, makro jest przeciwnym wiatrem", "fact_ids": ["breadth.score", "macro.composite"]},
    ]
    t["bull"] = [
        {"claim": "Pełny Trend Template i wybicie z bazy VCP", "fact_ids": ["trend.template_passed", "trend.execution_state"]},
        {"claim": "Wysokie ROE i ROIC bez czerwonych flag", "fact_ids": ["quality.roe_pct", "quality.red_flag_count"]},
        {"claim": "Potwierdzony trend wzrostowy rynku i postawa normalna", "fact_ids": ["regime.state", "exposure.posture"]},
    ]
    t["risk_manager"]["kill_criteria"] = [{"text": "Zamknięcie poniżej 1229 zł", "fact_ids": ["trend.stop_structure"]}]
    t["risk_manager"]["change_view"] = [{"text": "Zmiana reżimu WIG na korektę", "fact_ids": ["regime.state"]}]
    t["pre_mortem"] = [{"text": "Rynek przeszedł w korektę, a wycena nie miała marginesu", "fact_ids": []}]
    t["verdict_inputs"].update({"summary": "Mocny trend i jakość kontra wymagająca wycena",
                                "evidence_balance": "balanced"})
    t["thesis"]["thesis"] = "Lider jakości w trendzie wzrostowym"
    t["thesis"]["setup"] = "breakout"
    return t


def _ids(d):
    return {f["id"] for f in d["facts"]}


def test_template_skeleton(dossier):
    t = dd.make_template(dossier, "/tmp/d.json", "en")
    assert t["kind"] == "debate" and t["ticker"] == "KETY"
    assert len(t["bear"]) == 3 and len(t["bull"]) == 3
    assert set(t["available_fact_ids"]) == _ids(dossier)
    assert t["risk_manager"]["position_sizing"]["command"].endswith("--lang pl")
    assert t["thesis"]["plan"]["entry"] == 1281
    assert {u["section"] for u in t["unknowns"]} == {m["skill"] for m in dossier["missing"]}
    res = dd.validate(t, _ids(dossier))
    assert not res["valid"]
    assert {"placeholder_left", "no_citation"} <= {e["code"] for e in res["errors"]}


def test_valid_debate(dossier):
    res = dd.validate(filled(dossier), _ids(dossier), dossier_missing=dossier["missing"])
    assert res["valid"], res["errors"]
    assert res["stats"]["bear_points"] == 3 and res["stats"]["distinct_facts_cited"] >= 10


def test_missing_citation_unknown_id_too_few(dossier):
    t = filled(dossier)
    t["bull"][0]["fact_ids"] = []
    t["bear"][1]["fact_ids"] = ["valuation.made_up"]
    t["bear"].pop()
    res = dd.validate(t, _ids(dossier))
    codes = {(e["code"], e["path"]) for e in res["errors"]}
    assert ("no_citation", "bull[0].fact_ids") in codes
    assert ("unknown_fact_id", "bear[1].fact_ids") in codes
    assert ("too_few_points", "bear") in codes
    assert not res["valid"]


def test_too_many_points_and_short_claims(dossier):
    t = filled(dossier)
    t["bull"] = t["bull"] * 2
    t["bear"][0]["claim"] = "drogo"
    res = dd.validate(t, _ids(dossier))
    codes = {e["code"] for e in res["errors"]}
    assert {"too_many_points", "claim_too_short"} <= codes
    assert any(w["code"] == "duplicate_claim" for w in res["warnings"])


def test_unknowns_required_when_dossier_has_gaps(dossier):
    t = filled(dossier)
    t["unknowns"] = []
    gaps = [{"skill": "gpw-valuation", "reason": "no_report"}]
    assert "unknowns_empty_with_gaps" in {e["code"] for e in dd.validate(t, _ids(dossier), dossier_missing=gaps)["errors"]}
    assert dd.validate(t, _ids(dossier), dossier_missing=[])["valid"]


def test_verdict_inputs_rules(dossier):
    t = filled(dossier)
    t["verdict_inputs"]["not_a_recommendation"] = False
    t["verdict_inputs"]["evidence_balance"] = "strong buy"
    t["verdict_inputs"]["summary"] = "Kupuj, bo trend jest mocny i jakość wysoka"
    res = dd.validate(t, _ids(dossier))
    codes = {e["code"] for e in res["errors"]}
    assert {"must_not_be_recommendation", "bad_evidence_balance"} <= codes
    assert any(w["code"] == "call_to_action_language" for w in res["warnings"])


def test_kill_criteria_and_premortem_required(dossier):
    t = filled(dossier)
    t["risk_manager"]["kill_criteria"] = []
    t["pre_mortem"] = []
    res = dd.validate(t, _ids(dossier))
    paths = {e["path"] for e in res["errors"] if e["code"] == "too_few_items"}
    assert {"risk_manager.kill_criteria", "pre_mortem"} <= paths


def test_stale_citation_and_single_section_warnings(dossier):
    t = filled(dossier)
    t["bull"] = [{"claim": "Pełny Trend Template, wszystkie 8 kryteriów", "fact_ids": ["trend.template_passed"]},
                 {"claim": "Stan wybicia z bazy VCP nad pivotem", "fact_ids": ["trend.execution_state"]},
                 {"claim": "Cena blisko rocznego maksimum, mocny trend", "fact_ids": ["trend.pct_from_52w_high"]}]
    res = dd.validate(t, _ids(dossier), stale_ids={"trend.template_passed"})
    assert res["valid"]
    codes = {w["code"] for w in res["warnings"]}
    assert {"cites_stale_fact", "single_section_side"} <= codes


def test_thesis_placeholder_is_only_a_warning(dossier):
    t = filled(dossier)
    t["thesis"]["thesis"] = "<< x >>"
    res = dd.validate(t, _ids(dossier))
    assert res["valid"] and any(w["code"] == "thesis_not_filled" for w in res["warnings"])
    with pytest.raises(ValueError):
        dd.thesis_command(t, dossier, "/j")


def test_thesis_command(dossier):
    out = dd.thesis_command(filled(dossier), dossier, "/repo/skills/gpw-trade-journal", lang="pl")
    argv = out["argv"]
    assert argv[:3] == ["python3", "scripts/journal.py", "add"]
    assert argv[argv.index("--ticker") + 1] == "KETY"
    assert argv.count("--pillar") == 3 and argv.count("--kill") == 1 and argv.count("--risk") == 3
    assert "Zamknięcie poniżej 1229 zł [trend.stop_structure]" in argv
    assert argv[argv.index("--entry") + 1] == "1281" and argv[argv.index("--stop") + 1] == "1228.83"
    assert "--target" not in argv and "no_target" in out["notes"]
    assert any(c.startswith("2027-04-30") for c in out["catalysts"])
    assert out["executed"] is False
    cmd = out["command"]
    assert cmd.startswith("cd /repo/skills/gpw-trade-journal && ")
    assert shlex.split(cmd.split(" && ", 1)[1]) == argv


def test_thesis_command_validation_errors(dossier):
    t = filled(dossier)
    t["thesis"]["plan"] = {"entry": 100, "stop": 120, "target": None}
    with pytest.raises(ValueError, match="stop"):
        dd.thesis_command(t, dossier, "/j")
    t = filled(dossier)
    t["thesis"]["setup"] = "yolo"
    with pytest.raises(ValueError, match="setup"):
        dd.thesis_command(t, dossier, "/j")
    t = filled(dossier)
    t["risk_manager"]["kill_criteria"] = []
    with pytest.raises(ValueError, match="kill"):
        dd.thesis_command(t, dossier, "/j")
    t = filled(dossier)
    t["thesis"]["pillars"] = ["p{} jest dość długi".format(i) for i in range(7)]
    out = dd.thesis_command(t, dossier, "/j")
    assert out["argv"].count("--pillar") == 5 and "pillars_truncated_to_5" in out["notes"]


def test_thesis_command_is_accepted_by_the_real_journal(dossier, tmp_path):
    jdir = REPO / "skills" / "gpw-trade-journal"
    out = dd.thesis_command(filled(dossier), dossier, str(jdir))
    argv = [sys.executable] + out["argv"][1:] + ["--state-dir", str(tmp_path / "jstate")]
    cp = subprocess.run(argv, cwd=str(jdir), capture_output=True, text=True, timeout=60)
    assert cp.returncode == 0, cp.stderr
    saved = list((tmp_path / "jstate" / "theses").glob("*.json"))
    assert len(saved) == 1
    th = json.loads(saved[0].read_text(encoding="utf-8"))
    assert th["ticker"] == "KETY" and len(th["kill_criteria"]) == 1 and th["status"] == "IDEA"


# --------------------------------------------------------------------------
# CLI end to end and errors
# --------------------------------------------------------------------------

def test_cli_end_to_end(tmp_path, capsys):
    out = tmp_path / "out"
    base = ["--state-dir", str(tmp_path / "j"), "--watchlist", str(tmp_path / "w.json")]
    assert cli.main(["build", "KETY", "--input-dir", str(FIX), "--output-dir", str(out), "--as-of", REF] + base) == 0
    md = capsys.readouterr().out
    assert md.startswith("# Dossier spółki KETY") and "Materiał edukacyjny" in md
    assert len(list(out.glob("gpw_research_dossier_KETY_*.json"))) == 1
    assert cli.main(["debate-template", "KETY", "--input-dir", str(out), "--output-dir", str(out)]) == 0
    capsys.readouterr()
    tpl_path = next(out.glob("gpw_research_debate_KETY_*.json"))
    assert cli.main(["validate-debate", str(tpl_path)]) == 1          # unfilled template is invalid
    capsys.readouterr()
    dossier = json.loads(next(out.glob("gpw_research_dossier_KETY_*.json")).read_text(encoding="utf-8"))
    f = filled(dossier)
    f["dossier_file"] = str(next(out.glob("gpw_research_dossier_KETY_*.json")))
    deb = tmp_path / "deb.json"
    deb.write_text(json.dumps(f, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["validate-debate", str(deb), "--json"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["valid"] and res["dossier_file"]
    assert cli.main(["to-thesis", str(deb), "--status", "READY"]) == 0
    txt = capsys.readouterr().out
    assert "scripts/journal.py add --ticker KETY" in txt and "--status READY" in txt and "NIE została uruchomiona" in txt


def test_validate_falls_back_to_embedded_ids(tmp_path, dossier, capsys):
    f = filled(dossier)
    f["dossier_file"] = str(tmp_path / "gone.json")
    p = tmp_path / "deb.json"
    p.write_text(json.dumps(f, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["validate-debate", str(p), "--input-dir", str(tmp_path)]) == 0
    assert "nie znaleziono dossier" in capsys.readouterr().out


def test_to_thesis_refuses_invalid_debate(tmp_path, dossier, capsys):
    f = filled(dossier)
    f["bull"] = f["bull"][:1]
    p = tmp_path / "deb.json"
    p.write_text(json.dumps(f, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["to-thesis", str(p), "--input-dir", str(tmp_path)]) == 1
    assert "too_few_points" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["build", "bad ticker!"], ["build"], [], ["build", "KETY", "--timeout", "0"],
                                  ["build", "KETY", "--as-of", "2026-13-01"], ["to-thesis", "x", "--status", "DONE"]])
def test_cli_argument_errors_exit_2(argv):
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert e.value.code == 2


def test_cli_data_errors_exit_1(tmp_path, capsys):
    assert cli.main(["build", "KETY", "--input-dir", str(tmp_path / "nope"), "--no-files"]) == 1
    assert "input dir not found" in capsys.readouterr().err
    assert cli.main(["build", "KETY", "--input-dir", str(FIX), "--no-files", "--report", str(tmp_path / "x.json"),
                     "--state-dir", str(tmp_path / "j")]) == 1
    assert cli.main(["debate-template", "KETY", "--input-dir", str(tmp_path), "--no-files"]) == 1
    assert "build KETY" in capsys.readouterr().err
    notdeb = tmp_path / "n.json"
    notdeb.write_text(json.dumps({"skill": "gpw-valuation"}), encoding="utf-8")
    assert cli.main(["validate-debate", str(notdeb)]) == 1
    assert cli.main(["validate-debate", str(tmp_path / "missing.json")]) == 1


def test_explicit_report_is_added(tmp_path):
    extra = tmp_path / "x" / "regime.json"
    extra.parent.mkdir()
    extra.write_text(json.dumps({"skill": "gpw-market-regime", "as_of": "2026-11-16",
                                 "regime": {"state": "bear", "top_risk_score": 90, "distribution_days": 9}}),
                     encoding="utf-8")
    d = build(tmp_path, "KETY", "--report", str(extra))
    assert facts(d)["regime.state"] == "bear"
