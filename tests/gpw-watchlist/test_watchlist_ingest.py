"""Ingest rules on trimmed real producer outputs (tests/gpw-watchlist/fixtures, 2026-11-13/16)."""

import copy
import datetime as dt
import json

import pytest

from gpwlib import reports

import watchlist_ingest as wi
import watchlist_state as ws

# Saturday after the 2026-11-13 session: deterministic session counting
# (provenance.sessions_behind treats only the real "today" specially).
REF = dt.date(2026, 11, 14)
RULES = wi.merge_rules(None)


def load(fixtures_dir, name):
    return json.loads((fixtures_dir / name).read_text(encoding="utf-8"))


def rep(data, file="x.json", mtime=0):
    from gpwlib.reports import report_as_of
    a = report_as_of(data)
    return {"skill": data["skill"], "file": file, "path": file, "data": data,
            "as_of": a.isoformat() if a else None, "mtime": mtime}


def run(state, *datas):
    return wi.ingest(state, [rep(d, "r{}.json".format(i), i) for i, d in enumerate(datas)], RULES, REF)


def item(state, t):
    return ws.find_item(state, t)


def test_trend_screener_adds_watch_and_candidates(fixtures_dir):
    st = ws.empty_state()
    res = run(st, load(fixtures_dir, "trend_screener.json"))
    kety, auto, pko = item(st, "KETY"), item(st, "AUTOPARTN"), item(st, "PKOBP")
    # band good (score 72) -> watch, with the screener's pivot and structural stop
    assert kety["stage"] == "watch" and kety["pivot"] == 1281.0 and kety["stop"] == 1228.83
    assert kety["levels_source"] == "gpw-trend-screener" and "vcp" in kety["tags"]
    assert auto["stage"] == "watch"
    # band "watchlist" -> candidate
    assert pko["stage"] == "candidate" and pko["pivot"] == 118.62
    # trend-template passers outside the watchlist -> candidates without levels
    pge = item(st, "PGE")
    assert pge["stage"] == "candidate" and pge["pivot"] is None and "trend_template" in pge["tags"]
    assert kety["screener"]["score"] == 72.2 and kety["screener"]["state"] == "breakout"
    assert not any(i["stage"] == "ready" for i in st["items"])  # never auto-ready
    assert res["reports"][0]["skill"] == "gpw-trend-screener"
    assert kety["sources"][0]["report_file"] == "r0.json"


def test_reingest_is_idempotent(fixtures_dir):
    st = ws.empty_state()
    run(st, load(fixtures_dir, "trend_screener.json"))
    before = copy.deepcopy(st)
    res = run(st, load(fixtures_dir, "trend_screener.json"))
    assert res["changes"] == [] and st == before


def test_candidate_upgraded_to_watch_but_never_further(fixtures_dir):
    st = ws.empty_state()
    st["items"].append(ws.new_item("KETY", REF, "candidate"))
    res = run(st, load(fixtures_dir, "trend_screener.json"))
    assert item(st, "KETY")["stage"] == "watch"
    assert any(c["change"] == "stage" and c["to"] == "watch" for c in res["changes"])
    assert item(st, "KETY")["history"][-1]["by"] == "gpw-trend-screener"


def test_user_levels_and_ready_levels_not_overwritten(fixtures_dir):
    st = ws.empty_state()
    a = ws.new_item("KETY", REF, "watch")
    ws.set_levels(a, REF, pivot=1300, stop=1250, by="user")
    b = ws.new_item("AUTOPARTN", REF, "watch")
    ws.set_levels(b, REF, pivot=30, stop=27, by="gpw-trend-screener")
    ws.transition(b, "ready", REF, "plan")
    st["items"] += [a, b]
    run(st, load(fixtures_dir, "trend_screener.json"))
    assert (a["pivot"], a["stop"]) == (1300.0, 1250.0)
    assert (b["pivot"], b["stop"], b["stage"]) == (30.0, 27.0, "ready")


def test_dropped_item_only_records_source(fixtures_dir):
    st = ws.empty_state()
    it = ws.new_item("KETY", REF, "watch")
    ws.transition(it, "dropped", REF, "not my style")
    st["items"].append(it)
    res = run(st, load(fixtures_dir, "trend_screener.json"))
    assert it["stage"] == "dropped" and it["tags"] == [] and it["pivot"] is None
    assert len(it["sources"]) == 1
    assert any(c["change"] == "seen_while_dropped" and c["ticker"] == "KETY" for c in res["changes"])


def test_ownership_short_crowded_tags_existing_only_and_clears(fixtures_dir):
    st = ws.empty_state()
    st["items"].append(ws.new_item("CCC", REF, "watch"))
    sh = load(fixtures_dir, "ownership_shorts.json")
    run(st, sh)
    assert "short_crowded" in item(st, "CCC")["tags"]
    assert item(st, "KRUK") is None  # avoid signals never add tickers
    assert item(st, "CCC")["tag_meta"]["short_crowded"]["skill"] == "gpw-ownership"
    # a newer shorts report where CCC is no longer crowded clears the tag
    newer = copy.deepcopy(sh)
    newer["as_of"] = newer["data_provenance"]["as_of"] = "2026-11-14"
    newer["signals"] = [s for s in newer["signals"] if s["ticker"] != "CCC"]
    res = wi.ingest(st, [rep(sh, "old.json", 1), rep(newer, "new.json", 2)], RULES, REF)
    assert res["reports"][0]["file"] == "new.json"  # newest per (skill, mode)
    assert "short_crowded" not in item(st, "CCC")["tags"]
    assert any(c["change"] == "tag_cleared" for c in res["changes"])


def test_ownership_short_new_alert_low(fixtures_dir):
    sh = load(fixtures_dir, "ownership_shorts.json")
    new = [s for s in sh["signals"] if s["kind"] == "short_new"]
    assert new
    st = ws.empty_state()
    st["items"].append(ws.new_item(new[0]["ticker"], REF, "watch"))
    run(st, sh)
    al = [a for a in item(st, new[0]["ticker"])["alerts"] if a["code"] == "short_new"]
    assert al and al[0]["severity"] == "low" and al[0]["origin"] == "ingest"


def test_insider_cluster_buy_adds_candidate_via_contract_fallback(fixtures_dir):
    ins = load(fixtures_dir, "ownership_insiders.json")
    assert ins["signals"] == []  # real run: no cluster in the window
    st = ws.empty_state()
    run(st, ins)
    assert st["items"] == []
    ins2 = copy.deepcopy(ins)
    del ins2["signals"]  # older report without signals[] -> ROADMAP contract fallback
    ins2["insiders"]["by_ticker"][0].update(cluster_buy=True, last_date="2026-11-12")
    ins2["insiders"]["by_ticker"][1].update(cluster_sell=True, last_date="2026-11-12")
    t_buy, t_sell = ins2["insiders"]["by_ticker"][0]["ticker"], ins2["insiders"]["by_ticker"][1]["ticker"]
    st["items"].append(ws.new_item(t_sell, REF, "watch"))
    run(st, ins2)
    assert item(st, t_buy)["stage"] == "candidate" and "insider_cluster_buy" in item(st, t_buy)["tags"]
    assert item(st, t_buy)["source"] == "gpw-ownership"
    sell = item(st, t_sell)
    assert "insider_cluster_sell" in sell["tags"]
    assert [a["severity"] for a in sell["alerts"] if a["code"] == "insider_cluster_sell"] == ["medium"]


def test_earnings_strong_negative_alert_and_age_limit(fixtures_dir):
    er = load(fixtures_dir, "earnings_reaction.json")
    old = [s for s in er["signals"] if s["date"] < "2026-09-01"]
    assert old
    st = ws.empty_state()
    for t in ("TORPOL", "ALLEGRO", old[0]["ticker"]):
        st["items"].append(ws.new_item(t, REF, "watch"))
    run(st, er)
    tor = item(st, "TORPOL")
    assert "earnings_strong_negative" in tor["tags"]
    assert [a["severity"] for a in tor["alerts"]] == ["high"]
    assert "earnings_strong_positive" in item(st, "ALLEGRO")["tags"] and item(st, "ALLEGRO")["alerts"] == []
    if old[0]["ticker"] not in ("TORPOL", "ALLEGRO"):
        assert item(st, old[0]["ticker"])["tags"] == []  # older than 20 sessions -> ignored
    assert item(st, "BOS") is None  # earnings never add tickers


def test_quality_tags_alerts_and_coverage(fixtures_dir):
    q = load(fixtures_dir, "quality_screener.json")
    st = ws.empty_state()
    for t in ("XTB", "DOMDEV", "KETY"):
        st["items"].append(ws.new_item(t, REF, "watch"))
    run(st, q)
    assert "quality_high" in item(st, "XTB")["tags"]
    dom = item(st, "DOMDEV")
    assert "quality_red_flags" in dom["tags"] and dom["alerts"][0]["severity"] == "medium"
    # KETY is outside the WIG20+mWIG40 quality universe? either way no tag
    assert "quality_high" not in item(st, "KETY")["tags"]
    # a newer screen without XTB in companies[] keeps its tag (not covered)
    q2 = copy.deepcopy(q)
    q2["as_of"] = q2["data_provenance"]["as_of"] = "2026-11-14"
    q2["companies"] = [c for c in q2["companies"] if c["ticker"] != "XTB"]
    q2["signals"] = [s for s in q2["signals"] if s["ticker"] != "XTB"]
    run(st, q2)
    assert "quality_high" in item(st, "XTB")["tags"]


def test_dividend_cut_risk_tag(fixtures_dir):
    dv = load(fixtures_dir, "dividend_analyzer.json")
    st = ws.empty_state()
    st["items"].append(ws.new_item("LPP", REF, "candidate"))
    run(st, dv)
    lpp = item(st, "LPP")
    assert "dividend_cut_risk" in lpp["tags"] and lpp["alerts"][0]["code"] == "dividend_cut_risk"


def test_pair_dislocation_tags_cheap_leg(fixtures_dir):
    pr = load(fixtures_dir, "pair_trading.json")
    pr["signals"] = [{"ticker": "INGBSK", "date": "2026-11-12", "kind": "pair_dislocation", "direction": "long",
                      "score": 80.0, "tags": {"pair": "BNPPPL/INGBSK", "rich_leg": "BNPPPL", "z": 2.3}}]
    st = ws.empty_state()
    st["items"].append(ws.new_item("INGBSK", REF, "candidate"))
    run(st, pr)
    it = item(st, "INGBSK")
    assert "pair_dislocation" in it["tags"] and it["sources"][0]["kind"] == "pair_dislocation"
    assert item(st, "BNPPPL") is None


def test_stale_report_skipped(fixtures_dir):
    ts = load(fixtures_dir, "trend_screener.json")
    ts["data_provenance"]["as_of"] = ts["summary"]["date"] = "2026-11-03"
    st = ws.empty_state()
    res = run(st, ts)
    assert st["items"] == [] and res["stale"][0]["sessions_behind"] > 3 and res["reports"] == []


def test_only_filter_and_unknown_producer(fixtures_dir):
    st = ws.empty_state()
    res = wi.ingest(st, [rep(load(fixtures_dir, "trend_screener.json"))], RULES, REF, only={"gpw-ownership"})
    assert res["reports"] == [] and st["items"] == []
    assert wi.resolve_skill("trend-screener") == "gpw-trend-screener"
    with pytest.raises(ws.WatchlistError):
        wi.resolve_skill("market-breadth")


def test_rules_override(fixtures_dir):
    rules = wi.merge_rules({"screener_candidate": None, "_comment": "x",
                            "screener_watchlist": {"add": "watch", "tag": "vcp_low"}})
    st = ws.empty_state()
    wi.ingest(st, [rep(load(fixtures_dir, "trend_screener.json"))], rules, REF)
    assert item(st, "PGE") is None
    assert item(st, "PKOBP")["stage"] == "watch" and "vcp_low" in item(st, "PKOBP")["tags"]


@pytest.mark.parametrize("bad", [{"x": {"upgrade_to": "ready"}}, {"x": {"add": "ready"}}, {"x": {"colour": 1}},
                                 {"x": {"alert": {"code": "a", "severity": "urgent"}}}, {"x": 5}])
def test_rules_validation(bad):
    with pytest.raises(ws.WatchlistError):
        wi.merge_rules(bad)


def test_screener_damaged_alert_on_existing():
    data = {"skill": "gpw-trend-screener", "mode": "screen", "summary": {"date": "2026-11-13"},
            "data_provenance": {"as_of": "2026-11-13"}, "watchlist": [],
            "candidates": [{"name": "KETY", "date": "2026-11-13", "score": 40, "band": "skip",
                            "execution": {"state": "damaged"}, "rs": {"rating": 80}}], "others": []}
    st = ws.empty_state()
    st["items"].append(ws.new_item("KETY", REF, "watch"))
    run(st, data)
    it = item(st, "KETY")
    assert it["alerts"][0]["code"] == "setup_damaged" and it["alerts"][0]["severity"] == "high"
    assert "setup_damaged" in it["tags"]


def test_alerts_expire_after_ttl():
    st = ws.empty_state()
    it = ws.new_item("KETY", REF, "watch")
    it["alerts"] = [{"code": "x", "date": "2026-10-01", "origin": "ingest"},
                    {"code": "y", "date": "2026-11-12", "origin": "ingest"},
                    {"code": "z", "date": "2026-10-01", "origin": "refresh"}]
    st["items"].append(it)
    assert wi.expire_alerts(st, REF) == 1
    assert [a["code"] for a in it["alerts"]] == ["y", "z"]


def test_iter_reports_on_fixture_dir(fixtures_dir):
    """The CLI path: gpwlib.reports.iter_reports + ingest over all fixtures."""
    found = []
    for skill in wi.PRODUCERS:
        found += reports.iter_reports(fixtures_dir, skill=skill)
    assert {r["skill"] for r in found} == set(wi.PRODUCERS)
    st = ws.empty_state()
    res = wi.ingest(st, found, RULES, REF)
    assert len(res["reports"]) == 7  # ownership shorts + insiders are separate modes
    assert item(st, "KETY")["stage"] == "watch"
