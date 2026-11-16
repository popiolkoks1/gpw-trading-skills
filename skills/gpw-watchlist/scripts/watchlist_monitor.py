"""gpw-watchlist refresh + review: price/event alerts and housekeeping.

``refresh`` compares each followed item with the newest GPW quotes (a
``gpwlib.panel`` series, dividend/split adjusted) and the newest
gpw-event-calendar report and produces alerts with a severity:

* ``high``   - act or decide now (stop breached, a confirmed pivot breakout of
  a ready item, a strong negative earnings reaction);
* ``medium`` - needs attention soon (near the stop, extended past the buy
  range, an event inside the next 10 sessions for a ready/entered item);
* ``low``    - for information (near the pivot, new 52-week high, turnover
  surge, event for a candidate).

``review`` never changes anything: it lists suggestions (drop, demote, write
a thesis, refresh) for the user to accept.

All thresholds are explained next to the constant and in
references/methodology.md. Pure functions only.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, Iterable, List, Optional

from gpwlib import indicators, plcal, provenance

# Minervini (2013, "Trade Like a Stock Market Wizard", ch. 10) and O'Neil
# (CAN SLIM): the buy range ends 5% above the pivot; beyond that the stock is
# "extended" and chasing it worsens reward/risk. The same 5% band below the
# pivot is gpw-trend-screener's pre_breakout_pct.
BUY_RANGE_PCT = 5.0
NEAR_PIVOT_PCT = 5.0
# O'Neil: breakout volume at least 40-50% above the 50-day average; GPW
# publishes turnover (PLN) not shares, so the ratio is on turnover - the same
# 1.4 x and 50-session base as gpw-trend-screener (breakout_vol_mult, dryup_long).
TURNOVER_SURGE = 1.4
TURNOVER_BASE = 50
# 52 weeks of sessions (~252 on GPW).
HIGH_LOOKBACK = 252
# Wilder ATR(14). A stop closer than half an average daily range can be hit by
# an ordinary day's noise.
ATR_N = 14
NEAR_STOP_ATR = 0.5
# Events within the next 10 sessions (two trading weeks: the usual window
# between "ready" and an entry / the first days of a swing trade).
EVENT_HORIZON_SESSIONS = 10
# Quotes older than this many sessions: suspended or delisted instrument.
STALE_QUOTE_SESSIONS = 3
# review: an item without any update (user action or new producer evidence)
# for 30 days is stale - the same default review period as gpw-trade-journal
# (DEFAULT_REVIEW_DAYS = 30).
STALE_DAYS = 30

EVENT_SEVERE_TYPES = {"periodic_report", "dividend_record", "ex_dividend", "agm", "dividend_payment"}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _last_idx(series, as_of: Optional[dt.date]) -> Optional[int]:
    if not series or not len(series.dates):
        return None
    if as_of is None:
        return len(series.dates) - 1
    a = as_of.isoformat()
    for i in range(len(series.dates) - 1, -1, -1):
        if series.dates[i] <= a:
            return i
    return None


def market_snapshot(series, as_of: Optional[dt.date], pivot: Optional[float], stop: Optional[float]) -> Optional[Dict]:
    """Numbers for one item from one panel series (None if no quotes)."""
    i = _last_idx(series, as_of)
    if i is None:
        return None
    close = series.close[: i + 1]
    high = series.high[: i + 1]
    low = series.low[: i + 1]
    turn = series.turnover_k[: i + 1]
    c = close[-1]
    prev = close[-2] if len(close) > 1 else None
    atr = indicators.atr(high, low, close, ATR_N)
    base = turn[-(TURNOVER_BASE + 1):-1]
    med = indicators.median(base) if len(base) >= 20 else None  # 20 = minimum for a stable median
    tr = (turn[-1] / med) if med else None
    prior_high = max(high[-HIGH_LOOKBACK:-1]) if len(high) > 1 else None
    snap = {
        "date": series.dates[i],
        "close": round(c, 4),
        "change_pct": round((c / prev - 1) * 100, 2) if prev else None,
        "atr14": round(atr, 4) if atr else None,
        "turnover_k": round(turn[-1], 1),
        "turnover_ratio": round(tr, 2) if tr is not None else None,
        "high_52w": round(max(high[-HIGH_LOOKBACK:]), 4),
        "new_52w_high": bool(prior_high is not None and len(high) > 20 and high[-1] > prior_high),
        "pivot": pivot,
        "stop": stop,
        "dist_to_pivot_pct": round((c / pivot - 1) * 100, 2) if pivot else None,
        "dist_to_stop_pct": round((c / stop - 1) * 100, 2) if stop else None,
        "pivot_state": None,
        "crossed_pivot_today": bool(pivot and prev is not None and prev <= pivot < c),
        "below_stop": bool(stop and c <= stop),
        "adjustments": [a for a in (series.adjustments or []) if a.get("date", "") <= series.dates[i]],
    }
    if pivot:
        d = snap["dist_to_pivot_pct"]
        if d > BUY_RANGE_PCT:
            snap["pivot_state"] = "extended"
        elif d > 0:
            snap["pivot_state"] = "buy_range"
        elif d >= -NEAR_PIVOT_PCT:
            snap["pivot_state"] = "near"
        else:
            snap["pivot_state"] = "below"
    return snap


def next_sessions(as_of: dt.date, n: int) -> List[dt.date]:
    out, d = [], as_of
    while len(out) < n:
        d += dt.timedelta(days=1)
        if plcal.is_probable_session(d):
            out.append(d)
    return out


def events_for(events: Iterable[Dict], ticker: str, as_of: dt.date, horizon: int = EVENT_HORIZON_SESSIONS) -> List[Dict]:
    """Company events for ``ticker`` from (as_of, as_of + horizon sessions]."""
    end = next_sessions(as_of, horizon)[-1]
    out = []
    for e in events or []:
        if not isinstance(e, dict) or str(e.get("ticker") or "").upper() != ticker:
            continue
        try:
            d = dt.date.fromisoformat(str(e.get("date"))[:10])
        except ValueError:
            continue
        if as_of < d <= end:
            out.append(e)
    out.sort(key=lambda e: e.get("date"))
    return out


def _alert(code, severity, as_of, **params) -> Dict:
    return {"code": code, "severity": severity, "date": as_of, "origin": "refresh",
            "params": {k: v for k, v in params.items() if v is not None}}


def item_alerts(item: Dict, snap: Optional[Dict], events: List[Dict], as_of: dt.date,
                effective_stop: Optional[float] = None) -> List[Dict]:
    """Refresh alerts for one item (stage candidate/watch/ready/entered)."""
    a = as_of.isoformat()
    stage = item["stage"]
    out: List[Dict] = []
    if snap is None:
        return [_alert("no_price_data", "medium", a)]
    behind = provenance.sessions_behind(dt.date.fromisoformat(snap["date"]), as_of)
    if behind > STALE_QUOTE_SESSIONS:
        out.append(_alert("no_recent_quotes", "medium", a, last_date=snap["date"], sessions=behind))
    # For an open position the journal plan's stop is authoritative.
    if stage == "entered" and effective_stop:
        stop = effective_stop
    else:
        stop = item.get("stop") if item.get("stop") is not None else effective_stop
    c = snap["close"]
    if stop:
        if c <= stop:
            out.append(_alert("stop_breached", "high", a, close=c, stop=stop,
                              dist_pct=round((c / stop - 1) * 100, 2)))
        elif snap.get("atr14") and c - stop <= NEAR_STOP_ATR * snap["atr14"]:
            out.append(_alert("near_stop", "medium", a, close=c, stop=stop,
                              dist_pct=round((c / stop - 1) * 100, 2), atr14=snap["atr14"]))
    surge = snap.get("turnover_ratio") is not None and snap["turnover_ratio"] >= TURNOVER_SURGE
    used_surge = False
    if stage != "entered" and item.get("pivot") and not (stop and c <= stop):
        st, d = snap["pivot_state"], snap["dist_to_pivot_pct"]
        ready = stage == "ready"
        if snap["crossed_pivot_today"] and st == "buy_range":
            out.append(_alert("pivot_breakout", "high" if (surge and ready) else "medium", a, close=c,
                              pivot=item["pivot"], dist_pct=d, turnover_ratio=snap.get("turnover_ratio"),
                              volume_confirmed=surge))
            used_surge = True
        elif st == "buy_range":
            out.append(_alert("in_buy_range", "medium" if ready else "low", a, close=c, pivot=item["pivot"], dist_pct=d))
        elif st == "extended":
            out.append(_alert("extended", "medium" if ready else "low", a, close=c, pivot=item["pivot"], dist_pct=d))
        elif st == "near":
            out.append(_alert("near_pivot", "medium" if ready else "low", a, close=c, pivot=item["pivot"], dist_pct=d))
    if snap.get("new_52w_high"):
        out.append(_alert("new_52w_high", "low", a, close=c, high_52w=snap["high_52w"]))
    if surge and not used_surge:
        out.append(_alert("turnover_surge", "low", a, turnover_ratio=snap["turnover_ratio"],
                          change_pct=snap.get("change_pct")))
    ld = item.get("levels_date")
    if ld and (item.get("pivot") or item.get("stop")):
        later = [x for x in snap.get("adjustments") or [] if x.get("date", "") > ld]
        if later:
            out.append(_alert("levels_pre_adjustment", "low", a, levels_date=ld, adjustment_date=later[-1]["date"],
                              factor=later[-1].get("factor")))
    for e in events:
        sev = "medium" if (stage in ("ready", "entered") and e.get("type") in EVENT_SEVERE_TYPES) else "low"
        out.append(_alert("event_soon", sev, a, event_type=e.get("type"), event_date=e.get("date"),
                          date_kind=e.get("date_kind"), title=e.get("title"), source=e.get("source")))
    return out


def refresh(state: Dict, panel: Dict, as_of: dt.date, events: Optional[List[Dict]] = None,
            thesis_stops: Optional[Dict[str, float]] = None) -> Dict:
    """Update ``item.market`` and replace refresh-origin alerts in place.

    Returns ``{"alerts": [...flat, with ticker], "checked": n, "missing": [tickers]}``.
    """
    flat, missing, checked = [], [], 0
    thesis_stops = thesis_stops or {}
    aliases = rename_aliases(panel)
    for it in state["items"]:
        if it["stage"] == "dropped":
            continue
        checked += 1
        s = panel.get(it["ticker"])
        renamed_to = None
        if s is None and it["ticker"] in aliases:
            renamed_to = aliases[it["ticker"]]
            s = panel.get(renamed_to)
        raw = market_snapshot(s, as_of, it.get("pivot"), it.get("stop")) if s is not None else None
        snap = None
        if raw is None:
            missing.append(it["ticker"])
        else:
            snap = {k: v for k, v in raw.items() if k != "adjustments"}
        evs = events_for(events or [], it["ticker"], as_of)
        alerts = item_alerts(it, raw, evs, as_of, thesis_stops.get(it["ticker"]))
        if renamed_to:
            alerts.append(_alert("ticker_renamed", "medium", as_of.isoformat(), new_ticker=renamed_to))
        it["market"] = snap
        it["alerts"] = [x for x in it.get("alerts") or [] if x.get("origin") != "refresh"] + alerts
        it["upcoming_events"] = [{"type": e.get("type"), "date": e.get("date"), "date_kind": e.get("date_kind"),
                                  "title": e.get("title")} for e in evs]
    state["last_refresh"] = {"as_of": as_of.isoformat(), "at": _now_iso(), "checked": checked, "missing": missing}
    for it in state["items"]:
        for x in it.get("alerts") or []:
            flat.append(dict(x, ticker=it["ticker"], stage=it["stage"]))
    return {"alerts": sort_alerts(flat), "checked": checked, "missing": missing}


def rename_aliases(panel: Dict) -> Dict[str, str]:
    """{old ticker: current ticker} from series stitched by gpwlib.panel (RENAMES)."""
    out = {}
    for name, s in panel.items():
        for r in getattr(s, "renamed_from", None) or []:
            if r.get("name") and r["name"] not in panel:
                out[r["name"]] = name
    return out


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sort_alerts(alerts: List[Dict]) -> List[Dict]:
    return sorted(alerts, key=lambda x: (SEVERITY_ORDER.get(x.get("severity"), 9), x.get("ticker") or "",
                                         x.get("code") or ""))


def all_alerts(state: Dict) -> List[Dict]:
    out = []
    for it in state["items"]:
        if it["stage"] == "dropped":
            continue
        for x in it.get("alerts") or []:
            out.append(dict(x, ticker=it["ticker"], stage=it["stage"]))
    return sort_alerts(out)


# --------------------------------------------------------------------------
# Review
# --------------------------------------------------------------------------

def review(state: Dict, today: dt.date, stale_days: int = STALE_DAYS) -> List[Dict]:
    """Suggestions ``{ticker, stage, action, to|None, reason, details{}}``; never mutates."""
    out = []
    for it in state["items"]:
        st = it["stage"]
        if st == "dropped":
            continue
        mk = it.get("market") or {}
        codes = {a.get("code") for a in it.get("alerts") or []}

        def add(action, reason, to=None, **details):
            out.append({"ticker": it["ticker"], "stage": st, "action": action, "to": to, "reason": reason,
                        "details": {k: v for k, v in details.items() if v is not None}})

        if st == "entered":
            if it.get("thesis_status") in ("CLOSED", "INVALIDATED"):
                add("demote", "position_closed", to="watch", thesis_id=it.get("thesis_id"),
                    thesis_status=it.get("thesis_status"))
            elif "stop_breached" in codes:
                add("check_journal", "stop_breached", thesis_id=it.get("thesis_id"), close=mk.get("close"),
                    stop=it.get("stop"))
            continue
        if mk.get("below_stop") or "stop_breached" in codes:
            add("drop", "invalidated", to="dropped", close=mk.get("close"), stop=it.get("stop"))
            continue
        if "setup_damaged" in codes:
            add("drop", "setup_damaged", to="dropped")
            continue
        last_src = max((s.get("date") or "" for s in it.get("sources") or []), default="")
        last = max(it.get("updated") or "", last_src)
        try:
            age = (today - dt.date.fromisoformat(last[:10])).days
        except ValueError:
            age = None
        if age is not None and age > stale_days:
            add("drop", "stale", to="dropped", days=age, last_update=last[:10])
            continue
        high = [a for a in it.get("alerts") or [] if a.get("severity") == "high" and a.get("origin") == "ingest"]
        if high and st in ("candidate", "watch"):
            add("drop", "risk_alert", to="dropped", codes=sorted({a["code"] for a in high}))
        if st == "ready":
            if mk.get("pivot_state") == "extended":
                add("demote", "extended", to="watch", dist_pct=mk.get("dist_to_pivot_pct"))
            if not it.get("thesis_id"):
                add("write_thesis", "ready_without_thesis")
            m_date = mk.get("date")
            if not m_date or provenance.sessions_behind(dt.date.fromisoformat(m_date), today) > STALE_QUOTE_SESSIONS:
                add("refresh", "no_recent_refresh", last_date=m_date)
        if st == "watch" and it.get("pivot") is None:
            add("set_levels", "watch_without_levels")
    order = {"drop": 0, "demote": 1, "check_journal": 2, "write_thesis": 3, "refresh": 4, "set_levels": 5}
    out.sort(key=lambda s: (order.get(s["action"], 9), s["ticker"]))
    return out
