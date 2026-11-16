"""Assembly engine of gpw-daily-brief on trimmed real reports (no network)."""

import copy
import datetime as dt
import json
import shutil

import daily_brief_engine as eng
import daily_brief_inputs as inp
from gpwlib import journal_io

REF = dt.date(2026, 11, 16)

PRICES = {"PKOBP": {"close": 119.9, "date": "2026-11-13"}, "KGHM": {"close": 276.0, "date": "2026-11-13"},
          "TORPOL": {"close": 57.0, "date": "2026-11-13"}, "PZU": {"close": 75.6, "date": "2026-11-13"},
          "KETY": {"close": 1337.0, "date": "2026-11-13"}, "AUTOPARTN": {"close": 29.6, "date": "2026-11-13"},
          "DINOPL": {"close": 38.77, "date": "2026-11-13"}, "BNPPPL": {"close": 189.0, "date": "2026-11-13"},
          "PEKAO": {"close": 267.1, "date": "2026-11-13"}}


def _positions(fixtures):
    return journal_io.open_positions(journal_io.load_theses(fixtures / "journal"))


def _watch(fixtures):
    return inp.load_watchlist(fixtures / "watchlist.json")


def _brief(report_dir, fixtures, **kw):
    collected = inp.collect(report_dir, reference=kw.pop("reference", REF), lang=kw.get("lang", "pl"))
    kw.setdefault("positions", _positions(fixtures))
    kw.setdefault("watch", _watch(fixtures))
    kw.setdefault("prices", PRICES)
    return collected, eng.build_brief(collected, **kw)


# ------------------------------------------------------------------ inputs
def test_collect_current_and_previous(report_dir):
    c = inp.collect(report_dir, reference=REF)
    assert c["regime"]["current"]["as_of"] == "2026-11-13"
    assert c["regime"]["previous"]["as_of"] == "2026-11-12"
    # one skill, two modes -> two inputs
    assert c["shorts"]["current"]["mode"] == "shorts"
    assert c["insiders"]["current"]["mode"] == "insiders"
    assert c["calendar"]["current"]["mode"] == "upcoming"
    assert c["calendar_portfolio"]["current"]["mode"] == "portfolio"
    assert c["previous_brief"]["current"] is None
    assert not c["regime"]["current"]["stale"]


def test_collect_reference_ignores_later_reports(report_dir):
    c = inp.collect(report_dir, reference=dt.date(2026, 11, 12))
    assert c["regime"]["current"]["as_of"] == "2026-11-12"
    assert c["regime"]["previous"] is None
    assert c["macro"]["current"] is None  # macro report is dated 2026-11-16


def test_collect_never_reads_rrg_report_as_sector_trend(report_dir, tmp_path):
    # integration fix 1.2: the RRG report shares "skill": "gpw-sector-trend"
    d = tmp_path / "rrg_collision"
    shutil.copytree(report_dir, d)
    base = inp.collect(d, reference=REF)["sector"]["current"]
    assert base is not None
    rrg = {"skill": "gpw-sector-trend", "version": "1.0.0", "mode": "rrg", "as_of": "2026-11-16",
           "rrg": {"items": [{"id": "WIG-BANKI", "quadrant": "leading"}]}}
    (d / "gpw_sector_rrg_2026-11-16_180000.json").write_text(json.dumps(rrg), encoding="utf-8")
    (d / "gpw_sector_trend_2026-11-16_180000.json").write_text(json.dumps(rrg), encoding="utf-8")
    cur = inp.collect(d, reference=REF)["sector"]["current"]
    assert cur["path"] == base["path"] and cur["mode"] is None and "composite" in cur["data"]


def test_collect_missing_dir_is_empty(tmp_path):
    c = inp.collect(tmp_path / "nope", reference=REF)
    assert all(v["current"] is None for v in c.values())


def test_load_watchlist_tolerates_absence_and_corruption(tmp_path):
    assert inp.load_watchlist(tmp_path / "missing.json") is None
    bad = tmp_path / "w.json"
    bad.write_text("{not json", encoding="utf-8")
    w = inp.load_watchlist(bad)
    assert w["items"] == [] and w["error"]
    lst = tmp_path / "l.json"
    lst.write_text(json.dumps([{"ticker": "KETY", "stage": "watch"}, {"no": "ticker"}]), encoding="utf-8")
    assert [i["ticker"] for i in inp.load_watchlist(lst)["items"]] == ["KETY"]


# ------------------------------------------------------------------ headline / diffs
def test_headline_has_three_lines_with_changes(report_dir, fixtures):
    _, b = _brief(report_dir, fixtures)
    assert [h["key"] for h in b["headline"]] == ["regime", "exposure", "breadth"]
    reg = b["headline"][0]["text"]
    assert "potwierdzony trend wzrostowy" in reg and "(bez zmian)" in reg
    assert "dni dystrybucji 4 (było: 5)" in reg
    assert "do 60% w akcjach" in b["headline"][1]["text"]
    assert "vs 2026-11-12" in b["headline"][2]["text"]
    assert b["as_of"] == "2026-11-13"


def test_changes_vs_previous_report(report_dir, fixtures):
    _, b = _brief(report_dir, fixtures)
    dd = [c for c in b["changes"] if c["field"] == "distribution_days"]
    assert dd and dd[0]["from"] == 5 and dd[0]["to"] == 4 and dd[0]["direction"] == "better"
    assert dd[0]["source"] == "gpw-market-regime" and dd[0]["since"] == "2026-11-12"
    assert not b["first_report"]


def test_regime_and_posture_downgrade_become_attention(report_dir, fixtures):
    c = inp.collect(report_dir, reference=REF)
    c = copy.deepcopy(c)
    c["regime"]["current"]["data"]["regime"]["state"] = "correction"
    c["exposure"]["current"]["data"]["posture"] = "defensive"
    c["exposure"]["current"]["data"]["limits"]["exposure_ceiling_pct"] = 25.0
    b = eng.build_brief(c, positions=[], watch=None, prices={})
    fields = {ch["field"]: ch for ch in b["changes"]}
    assert fields["regime_state"]["direction"] == "worse" and fields["regime_state"]["severity"] == "high"
    assert fields["posture"]["direction"] == "worse"
    assert fields["exposure_ceiling_pct"]["to"] == 25.0
    kinds = [a["kind"] for a in b["attention"]]
    assert kinds[:2] == ["regime_downgrade", "posture_downgrade"]
    assert "korekta" in b["attention"][0]["text"] and "25%" in b["attention"][0]["text"]


def test_change_thresholds_ignore_noise():
    cur = {"breadth_score": 36.0, "breadth_zone": "weakening", "top_risk_score": 38.0}
    prev = {"breadth_score": 35.5, "breadth_zone": "weakening", "top_risk_score": 36.0}
    assert eng.changes(cur, prev, {}, {}, "pl") == []
    cur2 = dict(cur, breadth_score=42.0, breadth_zone="neutral", top_risk_score=50.0)
    fields = {c["field"] for c in eng.changes(cur2, prev, {}, {}, "pl")}
    assert fields == {"breadth_score", "top_risk_score"}


def test_sector_leader_change(report_dir, fixtures):
    c = copy.deepcopy(inp.collect(report_dir, reference=REF))
    secs = c["sector"]["previous"]["data"]["sectors"]
    for s in secs:
        if s["key"] == "WIG-MOTO":
            s["ratio"] = 0.0
    b = eng.build_brief(c, positions=[], watch=None, prices={})
    ch = [x for x in b["changes"] if x["field"] == "sector_leaders"]
    assert ch and "Motoryzacja" in ch[0]["text"].split(";")[0]


def test_baseline_falls_back_to_previous_brief(report_dir, fixtures):
    # only current reports + a previous brief carrying a snapshot
    for p in (fixtures / "prev").glob("*.json"):
        (report_dir / p.name).unlink()
    prev = {"skill": "gpw-daily-brief", "mode": "daily", "as_of": "2026-11-12",
            "snapshot": {"regime_state": "uptrend_under_pressure", "posture": "normal", "distribution_days": 6}}
    (report_dir / "gpw_daily_brief_2026-11-12_180000.json").write_text(json.dumps(prev), encoding="utf-8")
    _, b = _brief(report_dir, fixtures)
    f = {c["field"]: c for c in b["changes"]}
    assert f["regime_state"]["from"] == "uptrend_under_pressure" and f["regime_state"]["direction"] == "better"
    assert b["baseline"]["previous_brief_as_of"] == "2026-11-12"
    assert b["baseline"]["new_signals_since"] == "2026-11-12"


def test_first_report_without_any_baseline(tmp_path, fixtures):
    for p in (fixtures / "cur").glob("*.json"):
        shutil.copy(p, tmp_path / p.name)
    _, b = _brief(tmp_path, fixtures)
    assert b["first_report"] and b["changes"] == []
    md = eng.render_markdown(b, "pl")
    assert "Pierwszy raport" in md


# ------------------------------------------------------------------ positions
def test_positions_price_vs_stop_events_earnings(report_dir, fixtures):
    _, b = _brief(report_dir, fixtures)
    pos = {p["ticker"]: p for p in b["positions"]["items"]}
    assert set(pos) == {"PKOBP", "KGHM", "TORPOL", "PZU"}
    assert pos["TORPOL"]["flags"][:2] == ["below_stop", "earnings_strong_negative"]
    assert pos["TORPOL"]["earnings"]["label"] == "strong_negative"
    assert pos["PKOBP"]["flags"] == ["near_stop"]
    assert pos["PKOBP"]["dist_to_stop_pct"] == round((119.9 - 117.5) / 119.9 * 100, 2)
    pzu = [e for e in pos["PZU"]["events"] if e["type"] == "periodic_report"]
    assert pzu and pzu[0]["date"] == "2026-11-19" and pzu[0]["sessions_ahead"] == 4
    assert "gpw-event-calendar" in pos["PZU"]["sources"]
    # ordering: below stop first, then near stop
    assert [p["ticker"] for p in b["positions"]["items"]][:2] == ["TORPOL", "PKOBP"]
    assert b["positions"]["circuit_breaker"]["state"] == "ok"


def test_position_ownership_flags(report_dir, fixtures):
    c = copy.deepcopy(inp.collect(report_dir, reference=REF))
    c["shorts"]["current"]["data"]["shorts"]["by_issuer"].append(
        {"ticker": "KGHM", "total_pct": 2.4, "holders": 3, "change_20s_pp": 0.4, "crowded": True})
    c["insiders"]["current"]["data"]["insiders"]["by_ticker"].append(
        {"ticker": "KGHM", "cluster_sell": True, "cluster_buy": False, "last_date": "2026-11-13", "notices": 3})
    b = eng.build_brief(c, positions=_positions(fixtures), watch=None, prices=PRICES)
    k = [p for p in b["positions"]["items"] if p["ticker"] == "KGHM"][0]
    assert "crowded_short" in k["flags"] and "insider_cluster_sell" in k["flags"]
    kinds = {a["kind"] for a in b["attention"] if a.get("ticker") == "KGHM"}
    assert kinds == {"crowded_short", "insider_cluster_sell"}


def test_no_journal_and_empty_journal(report_dir, fixtures):
    _, b = _brief(report_dir, fixtures, positions=None)
    assert not b["positions"]["available"]
    _, b2 = _brief(report_dir, fixtures, positions=[])
    assert b2["positions"]["available"] and b2["positions"]["items"] == []
    assert "Brak otwartych pozycji" in eng.render_markdown(b2, "pl")


def test_imminent_event_on_held_stock():
    as_of = dt.date(2026, 11, 13)
    inputs = {"calendar": {"spec": inp.SPEC_BY_KEY["calendar"], "current": {"data": {"events": [
        {"id": "x", "ticker": "PZU", "type": "periodic_report", "date": "2026-11-17", "date_kind": "confirmed",
         "importance": "high", "title": "Q3"}]}, "as_of": "2026-11-13"}, "previous": None}}
    sec = eng.positions_section([{"ticker": "PZU", "stop": 55, "avg_price": 60}], PRICES, inputs, as_of, 10, "pl")
    assert sec["items"][0]["events"][0]["sessions_ahead"] == 2
    assert "event_imminent" in sec["items"][0]["flags"]


# ------------------------------------------------------------------ watchlist
def test_watchlist_status_and_order(report_dir, fixtures):
    _, b = _brief(report_dir, fixtures)
    wl = b["watchlist"]
    st = {w["ticker"]: w["status"] for w in wl["items"]}
    assert st == {"DINOPL": "invalidated", "AUTOPARTN": "breakout", "KETY": "breakout", "PEKAO": "near_pivot",
                  "BNPPPL": "extended"}
    assert wl["dropped"] == 1 and "LPP" not in st
    assert [w["ticker"] for w in wl["items"]][0] == "DINOPL"
    bnp = [w for w in wl["items"] if w["ticker"] == "BNPPPL"][0]
    assert bnp["alerts"] == []  # low-severity alerts are left out
    kinds = [a["kind"] for a in b["attention"] if a["source"] == "gpw-watchlist"]
    assert kinds[0] == "watchlist_invalidated"


def test_watchlist_uses_own_market_snapshot_without_prices():
    w = {"items": [{"ticker": "KETY", "stage": "watch", "pivot": 1281, "stop": 1229,
                    "market": {"close": 1300.0, "date": "2026-11-13"}},
                   {"ticker": "XYZ", "stage": "watch", "pivot": 10}],
         "path": "w.json"}
    sec = eng.watchlist_section(w, {}, {})
    st = {r["ticker"]: r["status"] for r in sec["items"]}
    assert st == {"KETY": "breakout", "XYZ": "no_price"}


def test_watchlist_absent_or_disabled(report_dir, fixtures):
    _, b = _brief(report_dir, fixtures, watch=None, watch_path="/x/watchlist.json")
    assert not b["watchlist"]["available"]
    assert "/x/watchlist.json" in eng.render_markdown(b, "pl")
    _, b2 = _brief(report_dir, fixtures, watch=None, watch_disabled=True)
    assert "pominięta" in eng.render_markdown(b2, "pl")


# ------------------------------------------------------------------ signals
def test_signals_ranked_by_tier_then_strength(report_dir, fixtures):
    _, b = _brief(report_dir, fixtures)
    items = b["signals"]["items"]
    assert items[0]["ticker"] == "TORPOL" and items[0]["tier"] == 0  # held position first
    assert [s["ticker"] for s in items[1:3]] == ["KETY", "AUTOPARTN"]  # watchlist next
    tiers = [s["tier"] for s in items]
    assert tiers == sorted(tiers)
    for a, z in zip(items, items[1:]):
        if a["tier"] == z["tier"]:
            assert a["strength"] >= z["strength"]
    assert [s["rank"] for s in items] == list(range(1, len(items) + 1))
    assert all(s["source"].startswith("gpw-") for s in items)


def test_signal_rank_tie_breaks_are_deterministic():
    a = {"tier": 2, "strength": 50.0, "kind": "vcp_new", "ticker": "B"}
    b = {"tier": 2, "strength": 50.0, "kind": "earnings_strong_negative", "ticker": "C"}
    c = {"tier": 2, "strength": 50.0, "kind": "vcp_new", "ticker": "A"}
    assert [x["ticker"] for x in sorted([a, b, c], key=eng.rank_signal_key)] == ["C", "A", "B"]


def test_screener_only_new_names(report_dir):
    c = inp.collect(report_dir, reference=REF)
    prev_names = {w["name"] for w in c["screener"]["previous"]["data"]["watchlist"]}
    sig = eng.signals_section(c, dt.date(2026, 11, 12), [], [], "pl")
    vcp = {s["ticker"] for s in sig["items"] if s["kind"] == "vcp_new"}
    assert vcp and not (vcp & prev_names)


def test_signal_window_excludes_older(report_dir):
    c = inp.collect(report_dir, reference=REF)
    sig = eng.signals_section(c, dt.date(2026, 11, 12), [], [], "pl")
    assert all(s["date"] > "2026-11-12" for s in sig["items"])
    wide = eng.signals_section(c, dt.date(2026, 11, 1), [], [], "pl")
    assert len(wide["items"]) > len(sig["items"])


def test_shorts_crowded_flip_and_insider_cluster(report_dir):
    c = copy.deepcopy(inp.collect(report_dir, reference=REF))
    for b in c["shorts"]["previous"]["data"]["shorts"]["by_issuer"]:
        if b["ticker"] == "KRUK":
            b["crowded"] = False
    c["insiders"]["current"]["data"]["insiders"]["by_ticker"].append(
        {"ticker": "XTB", "cluster_buy": True, "cluster_sell": False, "last_date": "2026-11-13",
         "max_distinct_buyers_30d": 3, "basis": "parsed_transactions"})
    c["insiders"]["current"]["data"]["insiders"]["by_ticker"].append(
        {"ticker": "ABC", "cluster_buy": False, "cluster_sell": True, "last_date": "2026-11-13"})
    sig = eng.signals_section(c, dt.date(2026, 11, 12), [], [], "pl")
    kinds = {(s["kind"], s["ticker"]) for s in sig["items"]}
    assert ("short_newly_crowded", "KRUK") in kinds
    assert ("insider_cluster_buy", "XTB") in kinds
    assert ("insider_cluster_sell", "ABC") not in kinds  # sells only for held/watched names
    sig2 = eng.signals_section(c, dt.date(2026, 11, 12), ["ABC"], [], "pl")
    assert ("insider_cluster_sell", "ABC") in {(s["kind"], s["ticker"]) for s in sig2["items"]}


def test_per_source_cap_in_markdown():
    items = [{"source": "gpw-earnings-reaction", "rank": i, "ticker": str(i)} for i in range(6)] + \
            [{"source": "gpw-trend-screener", "rank": 7, "ticker": "V"}]
    chosen, more = eng.select_signals(items, 8, eng.PER_SOURCE_CAP)
    assert sum(1 for s in chosen if s["source"] == "gpw-earnings-reaction") == eng.PER_SOURCE_CAP
    assert chosen[-1]["ticker"] == "V" and more == 2


# ------------------------------------------------------------------ calendar
def test_calendar_next_five_sessions(report_dir, fixtures):
    _, b = _brief(report_dir, fixtures)
    cal = b["calendar"]
    assert cal["start"] == "2026-11-16" and cal["end"] == "2026-11-20"
    assert all(cal["start"] <= e["date"] <= cal["end"] for e in cal["items"])
    pzu = [e for e in cal["items"] if e["ticker"] == "PZU"]
    assert pzu and pzu[0]["relevance"] == "held"
    assert all(e["source"] == "gpw-event-calendar" for e in cal["items"])


def test_session_helpers_skip_holidays():
    # 2026-11-11 is a Polish holiday (Independence Day)
    assert eng.nth_session_after(dt.date(2026, 11, 10), 1) == dt.date(2026, 11, 12)
    assert eng.nth_session_before(dt.date(2026, 11, 12), 1) == dt.date(2026, 11, 10)
    assert eng.sessions_until(dt.date(2026, 11, 13), dt.date(2026, 11, 19)) == 4


# ------------------------------------------------------------------ freshness
def test_missing_inputs_listed_with_command(tmp_path, fixtures):
    for p in (fixtures / "cur").glob("gpw_market_regime_*.json"):
        shutil.copy(p, tmp_path / p.name)
    c = inp.collect(tmp_path, reference=REF)
    b = eng.build_brief(c, positions=None, watch=None, prices={})
    rows = {r["input"]: r for r in b["inputs"]}
    assert rows["regime"]["status"] == "ok"
    assert rows["breadth"]["status"] == "missing"
    assert rows["breadth"]["command"].startswith("cd ../gpw-market-breadth && python3 scripts/gpw_breadth.py")
    assert "macro" not in rows  # weekly input not needed in a daily brief
    md = eng.render_markdown(b, "pl")
    assert "Brak wejść" in md and "BRAK" in md
    assert "**Ekspozycja:** brak raportu" in md
    assert any(a["kind"] == "inputs_stale" for a in b["attention"])


def test_stale_inputs_flagged_first(report_dir, fixtures):
    _, b = _brief(report_dir, fixtures, reference=dt.date(2026, 12, 4))
    stale = [r for r in b["inputs"] if r["status"] == "stale"]
    assert {"regime", "breadth", "exposure"} <= {r["input"] for r in stale}
    md = eng.render_markdown(b, "pl").splitlines()
    assert md[2].startswith("> ⚠️ Dane nieaktualne")
    assert any("NIEAKTUALNE" in line for line in md)


def test_weekly_inputs_missing_only_in_weekly(report_dir, fixtures):
    for p in report_dir.glob("gpw_macro_regime_*.json"):
        p.unlink()
    _, d = _brief(report_dir, fixtures)
    assert "macro" not in {r["input"] for r in d["inputs"]}
    _, w = _brief(report_dir, fixtures, weekly=True)
    assert {r["input"]: r["status"] for r in w["inputs"]}["macro"] == "missing"


# ------------------------------------------------------------------ weekly
def test_weekly_sections(report_dir, fixtures):
    _, b = _brief(report_dir, fixtures, weekly=True)
    w = b["weekly"]
    assert w["macro"]["composite"] == "headwind" and w["macro"]["source"] == "gpw-macro-regime"
    assert w["portfolio"]["review_count"] == 3 and w["portfolio"]["var_pln"] > 0
    assert w["risk"]["worst"]["pln_loss"] > 0
    assert w["postmortem"]["signals_total"] > 0 and all(h["n"] for h in w["postmortem"]["headline"])
    assert w["trade_review"] is None
    assert "trade_review" not in {r["input"] for r in b["inputs"]}  # a pointer, never "missing"
    md = eng.render_markdown(b, "pl")
    assert "## Przegląd tygodniowy" in md and "trade_review.py --month 2026-11" in md
    assert b["mode"] == "weekly" and md.startswith("# Tygodniowy brief GPW")


# ------------------------------------------------------------------ markdown
def test_markdown_is_short_cited_and_ends_with_disclaimer(report_dir, fixtures):
    _, b = _brief(report_dir, fixtures)
    md = eng.render_markdown(b, "pl")
    lines = md.strip().splitlines()
    assert len(lines) < 80
    assert lines[-1].startswith("_Materiał edukacyjny")
    order = [md.index(h) for h in ("## W skrócie", "**Wymaga uwagi:**", "## Co się zmieniło", "## Twoje pozycje",
                                   "## Watchlista", "## Nowe sygnały", "## Kalendarz", "## Świeżość danych",
                                   "## Źródła / Sources")]
    assert order == sorted(order)
    att = md.split("**Wymaga uwagi:**")[1].split("##")[0]
    for line in [l for l in att.splitlines() if l[:2] in ("1.", "2.", "3.")]:
        assert line.rstrip().endswith("]")
    full = eng.render_markdown(b, "pl", full=True)
    assert len(full.splitlines()) >= len(lines)
    for word in ("kup ", "sprzedaj", " buy ", " sell "):
        assert word not in md.lower()


def test_english_rendering(report_dir, fixtures):
    _, b = _brief(report_dir, fixtures, lang="en")
    md = eng.render_markdown(b, "en")
    assert md.startswith("# GPW daily brief — 2026-11-13")
    assert "confirmed uptrend" in md and "## Needs attention" not in md and "**Needs attention:**" in md
    assert md.strip().splitlines()[-1].startswith("_Educational material")


def test_json_contract_keys(report_dir, fixtures):
    _, b = _brief(report_dir, fixtures)
    for k in ("skill", "version", "lang", "mode", "as_of", "headline", "attention", "changes", "positions",
              "watchlist", "signals", "calendar", "inputs", "snapshot", "ranking_rules"):
        assert k in b
    assert b["skill"] == "gpw-daily-brief" and b["version"] == "1.0.0"
    for r in b["inputs"]:
        assert {"input", "skill", "file", "as_of", "sessions_behind", "stale", "status", "command"} <= set(r)
    json.dumps(b)  # serialisable
