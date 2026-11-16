"""refresh alerts on a synthetic panel + review suggestions."""

import datetime as dt

import pytest

from watchlist_testutil import make_series

import watchlist_monitor as wm
import watchlist_state as ws

AS_OF = dt.date(2026, 11, 13)


def flat(n=260, price=100.0):
    return [price] * n


def it_(stage="watch", pivot=None, stop=None, ticker="KETY"):
    it = ws.new_item(ticker, AS_OF, "watch" if stage in ("ready", "entered") else stage)
    if pivot or stop:
        ws.set_levels(it, AS_OF, pivot=pivot, stop=stop)
    if stage in ("ready",):
        ws.transition(it, "ready", AS_OF, "plan")
    if stage == "entered":
        ws.transition(it, "entered", AS_OF, "journal", thesis_id="th1")
    return it


def codes(alerts):
    return {a["code"]: a for a in alerts}


def alerts_for(item, closes, turnover=None, events=(), stop_override=None, **kw):
    s = make_series(item["ticker"], closes, turnover=turnover, **kw)
    snap = wm.market_snapshot(s, AS_OF, item.get("pivot"), item.get("stop"))
    return snap, wm.item_alerts(item, snap, list(events), AS_OF, stop_override)


def test_breakout_with_turnover_surge_is_high_for_ready():
    closes = flat(259) + [103.0]
    turn = [1000.0] * 259 + [2000.0]
    snap, al = alerts_for(it_("ready", pivot=101, stop=95), closes, turn)
    c = codes(al)
    assert snap["crossed_pivot_today"] and snap["pivot_state"] == "buy_range"
    assert c["pivot_breakout"]["severity"] == "high" and c["pivot_breakout"]["params"]["volume_confirmed"]
    assert "turnover_surge" not in c  # folded into the breakout alert
    assert snap["turnover_ratio"] == 2.0 and round(snap["dist_to_pivot_pct"], 2) == 1.98


def test_breakout_without_volume_is_medium_and_watch_medium():
    closes = flat(259) + [103.0]
    _, al = alerts_for(it_("ready", pivot=101, stop=95), closes)
    assert codes(al)["pivot_breakout"]["severity"] == "medium"
    _, al = alerts_for(it_("watch", pivot=101, stop=95), closes, [1000.0] * 259 + [2000.0])
    assert codes(al)["pivot_breakout"]["severity"] == "medium"


@pytest.mark.parametrize("close,state,code", [(104.0, "buy_range", "in_buy_range"), (106.0, "extended", "extended"),
                                              (97.0, "near", "near_pivot"), (90.0, "below", None)])
def test_pivot_states(close, state, code):
    closes = flat(258) + [close, close]  # no crossing today
    snap, al = alerts_for(it_("watch", pivot=100, stop=80), closes)
    assert snap["pivot_state"] == state
    if code:
        assert codes(al)[code]["severity"] == "low"
    else:
        assert not {"in_buy_range", "extended", "near_pivot"} & set(codes(al))


def test_buy_range_boundary_exactly_5pct_is_buy_range():
    snap, _ = alerts_for(it_("watch", pivot=100, stop=80), flat(258) + [105.0, 105.0])
    assert snap["pivot_state"] == "buy_range"


def test_ready_extended_is_medium():
    _, al = alerts_for(it_("ready", pivot=100, stop=90), flat(258) + [108.0, 108.0])
    assert codes(al)["extended"]["severity"] == "medium"


def test_stop_breached_high_and_suppresses_pivot_alerts():
    _, al = alerts_for(it_("ready", pivot=110, stop=101), flat(259) + [100.0])
    c = codes(al)
    assert c["stop_breached"]["severity"] == "high" and c["stop_breached"]["params"]["dist_pct"] < 0
    assert "near_pivot" not in c


def test_near_stop_uses_half_atr():
    # ATR = 2 (high/low +/-1% of 100) -> half ATR = 1.0
    _, al = alerts_for(it_("watch", pivot=110, stop=99.2), flat(260))
    assert codes(al)["near_stop"]["severity"] == "medium"
    _, al = alerts_for(it_("watch", pivot=110, stop=97.0), flat(260))
    assert "near_stop" not in codes(al)


def test_entered_uses_journal_stop_and_no_pivot_alerts():
    it = it_("entered", pivot=99, stop=80)
    _, al = alerts_for(it, flat(259) + [100.5], stop_override=101.0)
    c = codes(al)
    assert "stop_breached" in c and c["stop_breached"]["params"]["stop"] == 101.0
    assert not {"in_buy_range", "pivot_breakout", "extended"} & set(c)


def test_new_52w_high_and_turnover_surge():
    closes = [100.0 + i * 0.01 for i in range(260)]
    _, al = alerts_for(it_("candidate"), closes, [1000.0] * 259 + [1500.0])
    c = codes(al)
    assert "new_52w_high" in c and c["turnover_surge"]["params"]["turnover_ratio"] == 1.5
    _, al = alerts_for(it_("candidate"), closes, [1000.0] * 259 + [1300.0])
    assert "turnover_surge" not in codes(al)  # 1.3x < 1.4x


def test_no_data_and_stale_quotes():
    assert wm.item_alerts(it_(), None, [], AS_OF)[0]["code"] == "no_price_data"
    s = make_series("KETY", flat(100), end=dt.date(2026, 11, 3))
    snap = wm.market_snapshot(s, AS_OF, None, None)
    al = codes(wm.item_alerts(it_(), snap, [], AS_OF))
    assert al["no_recent_quotes"]["params"]["sessions"] > wm.STALE_QUOTE_SESSIONS


def test_levels_pre_adjustment():
    it = it_("watch", pivot=100, stop=90)
    it["levels_date"] = "2026-11-01"
    s = make_series("KETY", flat(260))
    s.adjustments = [{"date": "2026-11-05", "factor": 0.97}]
    snap = wm.market_snapshot(s, AS_OF, 100, 90)
    al = codes(wm.item_alerts(it, snap, [], AS_OF))
    assert al["levels_pre_adjustment"]["params"]["adjustment_date"] == "2026-11-05"


def test_events_window_and_severity():
    ev = [{"ticker": "KETY", "type": "periodic_report", "date": "2026-11-20", "date_kind": "confirmed", "title": "Q3"},
          {"ticker": "KETY", "type": "agm", "date": "2026-12-15", "title": "late"},   # beyond 10 sessions
          {"ticker": "PKOBP", "type": "agm", "date": "2026-11-18"},
          {"ticker": None, "type": "mpc_meeting", "date": "2026-11-18"}]
    got = wm.events_for(ev, "KETY", AS_OF)
    assert [e["date"] for e in got] == ["2026-11-20"]
    assert wm.next_sessions(AS_OF, 10)[-1] == dt.date(2026, 11, 27)
    _, al = alerts_for(it_("ready", pivot=120, stop=90), flat(260), events=got)
    assert codes(al)["event_soon"]["severity"] == "medium"
    _, al = alerts_for(it_("candidate"), flat(260), events=got)
    assert codes(al)["event_soon"]["severity"] == "low"


def test_refresh_replaces_refresh_alerts_keeps_ingest():
    st = ws.empty_state()
    a = it_("watch", pivot=100, stop=80)
    a["alerts"] = [{"code": "old", "origin": "refresh", "date": "2026-11-01", "severity": "low"},
                   {"code": "earnings_strong_negative", "origin": "ingest", "date": "2026-11-12", "severity": "high"}]
    b = it_("candidate", ticker="NOPE")
    c = it_("watch", ticker="GONE")
    ws.transition(c, "dropped", AS_OF, "x")
    st["items"] += [a, b, c]
    pnl = {"KETY": make_series("KETY", flat(258) + [104.0, 104.0])}
    res = wm.refresh(st, pnl, AS_OF, [], {})
    got = {x["code"] for x in a["alerts"]}
    assert "old" not in got and {"earnings_strong_negative", "in_buy_range"} <= got
    assert res["missing"] == ["NOPE"] and res["checked"] == 2
    assert a["market"]["close"] == 104.0 and "adjustments" not in a["market"]
    assert st["last_refresh"]["as_of"] == "2026-11-13"
    assert res["alerts"][0]["severity"] == "high"  # sorted by severity
    assert c["market"] is None and c["alerts"] == []


# ---------------------------------------------------------------- review

def _review(items, today=dt.date(2026, 11, 14)):
    st = ws.empty_state()
    st["items"] = items
    return {(s["ticker"], s["action"], s["reason"]) for s in wm.review(st, today)}


def test_review_stale_and_invalidated():
    old = it_("candidate", ticker="OLD")
    old["updated"] = "2026-10-01"
    inv = it_("watch", pivot=100, stop=90, ticker="INV")
    inv["market"] = {"close": 89.0, "below_stop": True, "date": "2026-11-13"}
    fresh = it_("watch", pivot=100, stop=90, ticker="FRESH")
    got = _review([old, inv, fresh])
    assert ("OLD", "drop", "stale") in got and ("INV", "drop", "invalidated") in got
    assert not any(t == "FRESH" for t, _, _ in got)


def test_review_stale_not_when_recent_source():
    it = it_("candidate", ticker="SRC")
    it["updated"] = "2026-09-01"
    it["sources"] = [{"skill": "s", "kind": "k", "date": "2026-11-10"}]
    assert not _review([it])


def test_review_ready_checks():
    r = it_("ready", pivot=100, stop=90, ticker="RDY")
    r["market"] = {"pivot_state": "extended", "dist_to_pivot_pct": 8.0, "date": "2026-11-13"}
    got = _review([r])
    assert ("RDY", "demote", "extended") in got and ("RDY", "write_thesis", "ready_without_thesis") in got
    r2 = it_("ready", pivot=100, stop=90, ticker="R2")
    r2["thesis_id"] = "th"
    assert ("R2", "refresh", "no_recent_refresh") in _review([r2])


def test_review_entered_closed_and_risk_alert():
    e = it_("entered", ticker="ENT")
    e["thesis_status"] = "CLOSED"
    w = it_("watch", ticker="RISK", pivot=10, stop=9)
    w["alerts"] = [{"code": "earnings_strong_negative", "severity": "high", "origin": "ingest", "date": "2026-11-13"}]
    got = _review([e, w])
    assert ("ENT", "demote", "position_closed") in got and ("RISK", "drop", "risk_alert") in got


def test_review_watch_without_levels():
    assert ("W", "set_levels", "watch_without_levels") in _review([it_("watch", ticker="W")])


def test_renamed_ticker_resolved_via_panel_alias():
    st = ws.empty_state()
    st["items"].append(it_("watch", ticker="CCC", pivot=110, stop=90))
    s = make_series("MODIVO", flat(260))
    s.renamed_from = [{"name": "CCC", "first_date": "2025-11-01", "last_date": "2026-02-17"}]
    res = wm.refresh(st, {"MODIVO": s}, AS_OF, [], {})
    al = {a["code"]: a for a in st["items"][0]["alerts"]}
    assert res["missing"] == [] and al["ticker_renamed"]["params"]["new_ticker"] == "MODIVO"
    assert st["items"][0]["market"]["close"] == 100.0
