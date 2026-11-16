"""gpw-pre-trade-gate - context checks fed by the v1.2 skills (pure, no I/O).

Seven checks read other skills' JSON reports (already loaded by the CLI with
``gpwlib.reports.find_report``) and say what they mean for ONE planned long
entry:

==================  =========================  ==========================================
check id            input skill                can produce
==================  =========================  ==========================================
event_window        gpw-event-calendar          PASS / INFO / WARN / FAIL (only --strict-events)
                    (+ dividend-analyzer, earnings-reaction as fallbacks / gap history)
crowded_short       gpw-ownership (shorts)      PASS / WARN
insider_selling     gpw-ownership (insiders)    PASS / WARN
earnings_reaction   gpw-earnings-reaction       PASS / WARN
quality_red_flags   gpw-quality-screener        PASS / WARN (negative equity = prominent WARN)
macro_context       gpw-macro-regime            INFO only (slow context, never blocks)
stress_test         gpw-risk-lab                PASS / INFO / WARN
==================  =========================  ==========================================

A missing input (or a report that does not cover the ticker) gives
``UNKNOWN`` with the command that produces it. UNKNOWN and INFO never change
the verdict; only a check listed in ``require`` turns UNKNOWN into FAIL.
Only ``event_window`` under ``strict_events`` can FAIL on its own. Every
threshold is in ``CONTEXT_DEFAULTS`` and justified in references/gate_rules.md.

Each report argument is the dict returned by ``gpwlib.reports.find_report``
(keys ``data, file, path, as_of, sessions_behind, stale``) or None.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, Iterable, List, Optional, Sequence

from gpwlib import plcal

PASS, WARN, FAIL, SKIP, UNKNOWN, INFO = "PASS", "WARN", "FAIL", "SKIP", "UNKNOWN", "INFO"

CONTEXT_CHECKS = ("event_window", "crowded_short", "insider_selling", "earnings_reaction",
                  "quality_red_flags", "macro_context", "stress_test")

# input key -> producing skill
INPUT_SKILLS = {
    "events": "gpw-event-calendar",
    "ownership": "gpw-ownership",
    "earnings": "gpw-earnings-reaction",
    "quality": "gpw-quality-screener",
    "macro": "gpw-macro-regime",
    "dividend": "gpw-dividend-analyzer",
    "risk_lab": "gpw-risk-lab",
}

CONTEXT_DEFAULTS = {
    "holding_days": 20,             # planned holding window in sessions (~1 month; gpw-event-calendar window default)
    "event_buffer_sessions": 5,     # earnings within 5 sessions after the window still warns (one trading week of slack)
    "strict_event_sessions": 2,     # --strict-events: a report in sessions 1-2 means the gap hits before you can react
    "insider_window_days": 60,      # cluster sells older than ~2 months are stale news (Lakonishok & Lee 2001 horizon)
    "reaction_window_sessions": 20,  # drift after negative surprises builds over ~1 month (Jaglinski 2020; Bernard & Thomas 1989)
    # how old an input may be (sessions) before it is flagged stale - follows each skill's run cadence
    "max_age": {
        "events": 3,      # dates get announced any day; the cross-skill 3-session rule
        "ownership": 5,   # KNF register changes daily, crowding slowly; weekly run
        "earnings": 5,    # weekly scan during a reporting season (ROADMAP workflow)
        "quality": 65,    # data changes only with a new quarterly period (~65 sessions)
        "dividend": 22,   # ex-dates change with AGM resolutions; monthly run
        "macro": 10,      # weekly macro run + one week of slack; slow context
        "risk_lab": 5,    # weekly stress run (ROADMAP workflow)
    },
}

EARNINGS_TYPES = ("periodic_report",)
INFO_COMPANY_TYPES = ("ex_dividend", "agm", "dividend_record", "dividend_payment")
MARKET_TYPES = ("mpc_meeting", "index_revision", "msci_review", "macro_release", "other")
NEGATIVE_TILTS = ("negative", "slightly_negative")


def _chk(cid: str, status: str, key: str, source: Optional[Dict] = None, command: Optional[str] = None,
         **params) -> Dict:
    out = {"id": cid, "status": status, "reason": key, "params": params, "source": source}
    if command:
        out["command"] = command
    return out


def source_of(rep: Optional[Dict]) -> Optional[Dict]:
    if not rep:
        return None
    return {"skill": rep.get("skill") or (rep.get("data") or {}).get("skill"), "file": rep.get("file"),
            "as_of": rep.get("as_of"), "sessions_behind": rep.get("sessions_behind"), "stale": bool(rep.get("stale"))}


def commands(ticker: str, holding_days: int, out_dir: str = "<input-dir>") -> Dict[str, str]:
    """Commands (run from the gate's skill dir) that produce each missing input."""
    t = ticker
    return {
        "events": "python3 ../gpw-event-calendar/scripts/gpw_event_calendar.py window --ticker {} --days {} --output-dir {}".format(t, holding_days, out_dir),
        "ownership": "python3 ../gpw-ownership/scripts/gpw_ownership.py ticker {} --output-dir {}".format(t, out_dir),
        "earnings": "python3 ../gpw-earnings-reaction/scripts/gpw_earnings_reaction.py ticker {} --history --output-dir {}".format(t, out_dir),
        "quality": "python3 ../gpw-quality-screener/scripts/gpw_quality_screener.py ticker {} --output-dir {}".format(t, out_dir),
        "macro": "python3 ../gpw-macro-regime/scripts/gpw_macro_regime.py --output-dir {}".format(out_dir),
        "dividend": "python3 ../gpw-dividend-analyzer/scripts/gpw_dividend_analyzer.py ticker {} --output-dir {}".format(t, out_dir),
        "risk_lab": "python3 ../gpw-risk-lab/scripts/gpw_risk_lab.py stress --journal --max-loss-pct 20 --output-dir {}".format(out_dir),
    }


# --------------------------------------------------------------------------
# Identity and coverage
# --------------------------------------------------------------------------

def _norm(x) -> str:
    return str(x or "").strip().upper()


def matches(row: Dict, ids: Dict) -> bool:
    """Row belongs to the ticker: same ticker, issuer/company short name or ISIN.

    ISIN matters: a company can be listed under a new short name while another
    feed still uses the old one (e.g. MODIVO, ISIN PLCCC0000016, formerly CCC).
    """
    if not isinstance(row, dict):
        return False
    names = ids["names"]
    if _norm(row.get("ticker")) in names or _norm(row.get("issuer")) in names:
        return True
    isin = _norm(row.get("isin"))
    return bool(isin) and isin in ids["isins"]


def identity(ticker: str, isins: Iterable[str] = ()) -> Dict:
    return {"ticker": _norm(ticker), "names": {_norm(ticker)}, "isins": {_norm(i) for i in isins if i}}


def harvest_isins(ticker: str, reports: Dict[str, Optional[Dict]]) -> List[str]:
    """ISINs other reports attach to this ticker (no network)."""
    t, out = _norm(ticker), set()
    for rep in reports.values():
        d = (rep or {}).get("data") or {}
        if _norm(d.get("ticker")) == t and d.get("isin"):
            out.add(_norm(d["isin"]))
        bt = d.get("by_ticker")
        if isinstance(bt, dict) and isinstance(bt.get(t), dict) and bt[t].get("isin"):
            out.add(_norm(bt[t]["isin"]))
        for key in ("reactions", "companies", "events"):
            for r in d.get(key) or []:
                if isinstance(r, dict) and _norm(r.get("ticker")) == t and r.get("isin"):
                    out.add(_norm(r["isin"]))
    return sorted(out)


def covers(key: str, rep: Optional[Dict], ids: Dict) -> bool:
    """Does this report say anything about the ticker (so 'nothing found' means 'clear')?"""
    if not rep:
        return False
    d = rep.get("data") or {}
    if key == "events":
        if d.get("mode") == "upcoming":
            return True
        tick = {_norm(x) for x in d.get("tickers") or []} | {_norm(x) for x in (d.get("by_ticker") or {})}
        return bool(tick & ids["names"]) or any(matches(e, ids) for e in d.get("events") or [])
    if key == "ownership":
        if d.get("mode") == "ticker":
            return _norm(d.get("ticker")) in ids["names"] or (_norm(d.get("isin")) in ids["isins"] if d.get("isin") else False)
        return True
    if key == "earnings":
        if any(matches(r, ids) for r in d.get("reactions") or []):
            return True
        if ids["names"] & {_norm(k) for k in d.get("tickers") or {}}:
            return True
        return d.get("mode") == "scan" and not any(matches(s, ids) for s in d.get("skipped") or [])
    if key in ("quality", "dividend"):
        return any(matches(c, ids) for c in d.get("companies") or [])
    return True  # macro, risk_lab: market / portfolio level


# --------------------------------------------------------------------------
# Session arithmetic (Polish exchange calendar)
# --------------------------------------------------------------------------

def window_start(reference: dt.date, after_close: bool) -> dt.date:
    """First session of the holding window: the next session after the data date.

    ``after_close`` = the reference date's session is over (``--as-of`` points at
    the last data session), so the entry is the next one; otherwise today counts.
    """
    d = reference + dt.timedelta(days=1) if after_close else reference
    while not plcal.is_probable_session(d):
        d += dt.timedelta(days=1)
    return d


def session_dates(start: dt.date, n: int) -> List[dt.date]:
    out, d = [], start
    while len(out) < n:
        if plcal.is_probable_session(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def session_no(day: dt.date, sessions: Sequence[dt.date]) -> Optional[int]:
    """1-based number of sessions from the window start up to and including ``day``
    (an event on a weekend counts as the session before it). None if before the
    start or beyond the listed sessions."""
    if not sessions or day < sessions[0] or day > sessions[-1]:
        return None
    return sum(1 for s in sessions if s <= day)


def sessions_between(a: dt.date, b: dt.date) -> int:
    """Sessions after ``a`` up to and including ``b`` (0 if b <= a)."""
    n, d = 0, a + dt.timedelta(days=1)
    while d <= b:
        if plcal.is_probable_session(d):
            n += 1
        d += dt.timedelta(days=1)
    return n


def _date(x) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(x)[:10]) if x else None
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Earnings gap history
# --------------------------------------------------------------------------

def _median(xs: List[float]) -> Optional[float]:
    s = sorted(xs)
    if not s:
        return None
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def earnings_gap(rep: Optional[Dict], ids: Dict) -> Optional[Dict]:
    """Typical |opening gap| on the stock's report reaction days.

    Only reports published outside the session (pre_open / after_close /
    non_session) produce an overnight gap a stop cannot protect against; an
    intraday report lets the stop fill near its price. If there are none, all
    events are used (flagged ``all_alignments``).
    """
    if not rep:
        return None
    rows = [r for r in (rep.get("data") or {}).get("reactions") or [] if matches(r, ids)
            and isinstance(r.get("gap_pct"), (int, float))]
    seen, uniq = set(), []
    for r in rows:  # preliminary + periodic of one period share a reaction day
        k = r.get("reaction_date")
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    outside = [r for r in uniq if r.get("session_alignment") != "intraday"]
    use = outside or uniq
    if not use:
        return None
    gaps = [abs(r["gap_pct"]) for r in use]
    return {"median_abs_gap_pct": round(_median(gaps), 2), "max_abs_gap_pct": round(max(gaps), 2), "n": len(gaps),
            "all_alignments": not outside}


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def _event(e: Dict, kind: str, origin: str, **extra) -> Dict:
    out = {"kind": kind, "type": e.get("type"), "date": str(e.get("date") or e.get("ex_date"))[:10],
           "date_kind": e.get("date_kind"), "title": e.get("title"), "origin": origin}
    out.update(extra)
    return out


def collect_events(ids: Dict, events_rep: Optional[Dict], dividend_rep: Optional[Dict],
                   earnings_rep: Optional[Dict], ev_covers: bool) -> List[Dict]:
    out: List[Dict] = []
    d = (events_rep or {}).get("data") or {}
    for e in d.get("events") or []:
        if not isinstance(e, dict) or not e.get("date"):
            continue
        t = e.get("type")
        if e.get("ticker") or e.get("isin"):
            if not matches(e, ids):
                continue
            if t in EARNINGS_TYPES:
                out.append(_event(e, "earnings", "gpw-event-calendar"))
            elif t in INFO_COMPANY_TYPES:
                dps = (e.get("details") or {}).get("dps")
                out.append(_event(e, t, "gpw-event-calendar", dps=dps if isinstance(dps, (int, float)) else None))
            else:
                out.append(_event(e, "company_other", "gpw-event-calendar"))
        elif t in MARKET_TYPES or t in EARNINGS_TYPES:
            if t in EARNINGS_TYPES:
                continue  # market-wide statutory deadlines, not this company
            out.append(_event(e, t, "gpw-event-calendar", importance=e.get("importance")))
    bt = d.get("by_ticker") or {}
    for k, v in bt.items():
        if _norm(k) in ids["names"] and isinstance(v, dict):
            for fld, kind in (("next_earnings", "earnings"), ("next_ex_dividend", "ex_dividend"), ("next_agm", "agm")):
                e = v.get(fld)
                if isinstance(e, dict) and e.get("date"):
                    out.append(_event(dict(e, type=kind), kind, "gpw-event-calendar"))
    dd = (dividend_rep or {}).get("data") or {}
    for u in dd.get("upcoming") or []:
        if matches(u, ids) and u.get("ex_date"):
            out.append(_event({"date": u["ex_date"], "date_kind": u.get("date_kind"), "type": "ex_dividend",
                               "title": u.get("source")}, "ex_dividend", "gpw-dividend-analyzer",
                              dps=u.get("dps") if isinstance(u.get("dps"), (int, float)) else None))
    if not ev_covers:  # fallback: earnings-reaction's next expected report date
        ed = (earnings_rep or {}).get("data") or {}
        for k, v in (ed.get("tickers") or {}).items():
            if _norm(k) in ids["names"] and isinstance(v, dict) and isinstance(v.get("next_expected"), dict):
                ne = v["next_expected"]
                if ne.get("date"):
                    out.append(_event({"date": ne["date"], "date_kind": ne.get("date_kind"), "type": "periodic_report",
                                       "title": ne.get("basis")}, "earnings", "gpw-earnings-reaction"))
    # dedupe: same kind + date (keep the first = calendar; merge DPS)
    seen: Dict[tuple, Dict] = {}
    for e in out:
        k = (e["kind"], e["date"])
        if k in seen:
            if seen[k].get("dps") is None and e.get("dps") is not None:
                seen[k]["dps"] = e["dps"]
            continue
        seen[k] = e
    return sorted(seen.values(), key=lambda e: (e["date"], e["kind"]))


def check_event_window(trade: Dict, nums: Dict, ids: Dict, reports: Dict, cfg: Dict, start: dt.date,
                       strict: bool, cmd: str, gap_cmd: Optional[str] = None) -> Dict:
    ev_rep, dv_rep, er_rep = reports.get("events"), reports.get("dividend"), reports.get("earnings")
    hd, buf, strict_n = cfg["holding_days"], cfg["event_buffer_sessions"], cfg["strict_event_sessions"]
    sessions = session_dates(start, hd + buf)
    window = {"start": sessions[0].isoformat(), "end": sessions[hd - 1].isoformat(),
              "buffer_end": sessions[-1].isoformat(), "sessions": hd, "buffer_sessions": buf}
    ev_cov = covers("events", ev_rep, ids)
    evs = []
    for e in collect_events(ids, ev_rep, dv_rep, er_rep, ev_cov):
        d = _date(e["date"])
        n = session_no(d, sessions) if d else None
        if n is not None:
            evs.append(dict(e, session=n, in_window=n <= hd))
    gap = earnings_gap(er_rep, ids)
    stop_dist = nums.get("stop_distance_pct")
    src = source_of(ev_rep) or source_of(dv_rep) or source_of(er_rep)
    earn = [e for e in evs if e["kind"] == "earnings"]
    infos = [e for e in evs if e["kind"] != "earnings"]
    for e in infos:
        if e["kind"] == "ex_dividend" and e.get("dps") and trade.get("entry"):
            e["dps_pct_of_entry"] = round(e["dps"] / trade["entry"] * 100, 2)
    company_end = _date(((ev_rep or {}).get("data") or {}).get("horizon", {}).get("company_end")) if ev_rep else None
    partial = bool(ev_cov and company_end and company_end < sessions[-1])
    base = dict(window=window, events=evs, earnings_gap=gap, stop_distance_pct=stop_dist,
                calendar_covers_ticker=ev_cov, coverage_end=company_end.isoformat() if company_end else None)
    if earn:
        first = earn[0]
        gap_exceeds = (gap is not None and stop_dist is not None and gap["median_abs_gap_pct"] > stop_dist)
        p = dict(base, date=first["date"], session=first["session"], date_kind=first["date_kind"],
                 in_window=first["in_window"], gap_exceeds_stop=gap_exceeds,
                 gap=gap["median_abs_gap_pct"] if gap else None, stop_dist=stop_dist)
        if strict and first["session"] <= strict_n:
            if gap is None or stop_dist is None:
                return _chk("event_window", FAIL, "event_strict_gap_unknown", src, gap_cmd if gap is None else None,
                            limit=strict_n, **p)
            if gap_exceeds:
                return _chk("event_window", FAIL, "event_strict_gap_exceeds", src, limit=strict_n, **p)
            return _chk("event_window", WARN, "event_strict_gap_ok", src, limit=strict_n, **p)
        key = "event_earnings_in_window" if first["in_window"] else "event_earnings_near_window"
        return _chk("event_window", WARN, key, src, **p)
    if not ev_cov:
        return _chk("event_window", UNKNOWN, "event_no_calendar", src, cmd, **base)
    if infos:
        return _chk("event_window", INFO, "event_info_only", src, **base)
    if partial:
        return _chk("event_window", UNKNOWN, "event_partial_coverage", src, cmd, **base)
    return _chk("event_window", PASS, "event_clear", src, **base)


def check_crowded_short(ids: Dict, rep: Optional[Dict], cmd: str) -> Dict:
    d = (rep or {}).get("data") or {}
    sh = d.get("shorts")
    if not rep or not isinstance(sh, dict) or not covers("ownership", rep, ids):
        return _chk("crowded_short", UNKNOWN, "short_missing", source_of(rep), cmd)
    row = next((r for r in sh.get("by_issuer") or [] if matches(r, ids)), None)
    src = source_of(rep)
    if row is None:
        return _chk("crowded_short", PASS, "short_none", src)
    p = dict(total_pct=row.get("total_pct"), holders=row.get("holders"), change_20s_pp=row.get("change_20s_pp"),
             days_to_cover=row.get("days_to_cover"), high_days_to_cover=row.get("high_days_to_cover"),
             days_in_register=row.get("days_in_register"), largest_holder=row.get("largest_holder"),
             crowded_reasons=row.get("crowded_reasons") or [], register_ticker=row.get("ticker"))
    if row.get("crowded"):
        return _chk("crowded_short", WARN, "short_crowded", src, **p)
    return _chk("crowded_short", PASS, "short_present", src, **p)


def check_insider_selling(ids: Dict, rep: Optional[Dict], reference: dt.date, cfg: Dict, cmd: str) -> Dict:
    d = (rep or {}).get("data") or {}
    ins = d.get("insiders")
    if not rep or not isinstance(ins, dict) or not covers("ownership", rep, ids):
        return _chk("insider_selling", UNKNOWN, "insider_missing", source_of(rep), cmd)
    src = source_of(rep)
    win = cfg["insider_window_days"]
    p0 = dict(period_start=ins.get("start"), period_end=ins.get("end"), window_days=win)
    pstart = _date(ins.get("start"))
    p0["covered_days"] = (reference - pstart).days if pstart else None
    row = next((r for r in ins.get("by_ticker") or [] if matches(r, ids)), None)
    # A cluster needs >= 3 insiders within 30 days (gpw-ownership rule), so a report covering
    # fewer days cannot show its absence: "nothing found" becomes UNKNOWN, not PASS.
    min_cov = cfg.get("insider_min_coverage_days", 30)
    short_cov = p0["covered_days"] is not None and p0["covered_days"] < min_cov
    if row is None:
        if short_cov:
            return _chk("insider_selling", UNKNOWN, "insider_short_coverage", src, cmd, min_days=min_cov, **p0)
        return _chk("insider_selling", PASS, "insider_none", src, **p0)
    sells = [_date(t.get("date")) for t in row.get("transactions") or []
             if isinstance(t, dict) and str(t.get("side", "")).lower() in ("sell", "s", "sprzedaz", "sprzedaż")]
    sells = [x for x in sells if x]
    last = max(sells) if sells else _date(row.get("last_date"))
    age = (reference - last).days if last else None
    p = dict(p0, buys=row.get("buys"), sells=row.get("sells"), distinct_insiders=row.get("distinct_insiders"),
             net_value_pln=row.get("net_value_pln"), cluster_buy=bool(row.get("cluster_buy")),
             cluster_sell=bool(row.get("cluster_sell")), last_date=last.isoformat() if last else None, age_days=age,
             basis=row.get("basis"), flags=row.get("flags") or [])
    if p["cluster_sell"] and (age is None or age <= win):
        return _chk("insider_selling", WARN, "insider_cluster_sell", src, **p)
    if p["cluster_buy"]:
        return _chk("insider_selling", PASS, "insider_cluster_buy", src, **p)
    if p["cluster_sell"]:
        return _chk("insider_selling", PASS, "insider_cluster_sell_old", src, **p)
    if short_cov:
        return _chk("insider_selling", UNKNOWN, "insider_short_coverage", src, cmd, min_days=min_cov, **p)
    return _chk("insider_selling", PASS, "insider_no_cluster", src, **p)


def check_earnings_reaction(ids: Dict, rep: Optional[Dict], reference: dt.date, cfg: Dict, cmd: str) -> Dict:
    if not rep or not covers("earnings", rep, ids):
        return _chk("earnings_reaction", UNKNOWN, "reaction_missing", source_of(rep), cmd)
    src = source_of(rep)
    rows = [r for r in (rep.get("data") or {}).get("reactions") or [] if matches(r, ids) and _date(r.get("reaction_date"))]
    rows.sort(key=lambda r: (r["reaction_date"], r.get("report_type") != "preliminary_results"), reverse=True)
    win = cfg["reaction_window_sessions"]
    if not rows:
        return _chk("earnings_reaction", PASS, "reaction_none", src, window=win)
    r = rows[0]
    n = sessions_between(_date(r["reaction_date"]), reference)
    p = dict(label=r.get("label"), report_date=r.get("report_date"), reaction_date=r.get("reaction_date"),
             report_type=r.get("report_type"), car=r.get("car_3d_pct"), gap=r.get("gap_pct"),
             abnormal_turnover=r.get("abnormal_turnover"), drift_20s_pct=r.get("drift_20s_pct"),
             provisional=bool(r.get("provisional")), sessions_since=n, window=win)
    if n > win:
        return _chk("earnings_reaction", PASS, "reaction_old", src, **p)
    if r.get("label") == "strong_negative":
        return _chk("earnings_reaction", WARN, "reaction_strong_negative", src, **p)
    if r.get("label") == "strong_positive":
        return _chk("earnings_reaction", PASS, "reaction_strong_positive", src, **p)
    return _chk("earnings_reaction", PASS, "reaction_recent", src, **p)


def check_quality(ids: Dict, rep: Optional[Dict], cmd: str) -> Dict:
    if not rep or not covers("quality", rep, ids):
        return _chk("quality_red_flags", UNKNOWN, "quality_missing", source_of(rep), cmd)
    src = source_of(rep)
    c = next(x for x in (rep.get("data") or {}).get("companies") or [] if matches(x, ids))
    flags = [f for f in c.get("red_flags") or [] if isinstance(f, dict) and f.get("code")]
    fsp = c.get("f_score_partial") or {}
    p = dict(flags=[f["code"] for f in flags], high=[f["code"] for f in flags if f.get("severity") == "high"],
             red_flags=flags, quality_score=c.get("quality_score"), f_score=c.get("f_score"),
             f_display=fsp.get("display"), f_coverage_pct=fsp.get("coverage_pct"), sector_type=c.get("sector_type"),
             period=c.get("period"), stale_period=bool(c.get("stale_period")),
             data_checks=[x.get("code") for x in c.get("data_checks") or [] if isinstance(x, dict)])
    if any(f["code"] == "negative_equity" for f in flags):
        return _chk("quality_red_flags", WARN, "quality_negative_equity", src, prominent=True, **p)
    if flags:
        return _chk("quality_red_flags", WARN, "quality_flags", src, prominent=bool(p["high"]), **p)
    return _chk("quality_red_flags", PASS, "quality_ok", src, **p)


def sector_tilt(macro: Dict, sector: Optional[str]) -> Optional[Dict]:
    if not sector:
        return None
    for name, t in (macro.get("sector_tilts") or {}).items():
        if isinstance(t, dict) and sector in (t.get("indices") or []):
            return {"group": name, "tilt": t.get("tilt"), "score": t.get("score"),
                    "drivers": [x.get("driver") for x in t.get("drivers") or [] if isinstance(x, dict)],
                    "market_check": t.get("market_check")}
    return None


def check_macro(rep: Optional[Dict], sector: Optional[str], cmd: str) -> Dict:
    d = (rep or {}).get("data") or {}
    state = ((d.get("macro") or {}).get("state")) if isinstance(d.get("macro"), dict) else None
    if not rep or not isinstance(state, dict):
        return _chk("macro_context", UNKNOWN, "macro_missing", source_of(rep), cmd)
    tilt = sector_tilt(d, sector)
    p = dict(composite=state.get("composite"), regime_label=state.get("regime_label"),
             monetary=state.get("monetary"), pln=state.get("pln"), inflation=state.get("inflation"),
             score=(d.get("macro") or {}).get("score"), sector=sector, tilt=tilt)
    neg = bool(tilt and tilt.get("tilt") in NEGATIVE_TILTS)
    if state.get("composite") == "headwind" and neg:
        key = "macro_headwind_negative_tilt"
    elif tilt:
        key = "macro_with_tilt"
    else:
        key = "macro_no_tilt"
    return _chk("macro_context", INFO, key, source_of(rep), **p)


def check_stress(rep: Optional[Dict], ticker_ids: Dict, cmd: str) -> Dict:
    d = (rep or {}).get("data") or {}
    st = d.get("stress")
    if not rep or not isinstance(st, dict):
        return _chk("stress_test", UNKNOWN, "stress_missing", source_of(rep), cmd)
    scen = [s for s in st.get("scenarios") or [] if isinstance(s, dict) and not s.get("skipped")
            and isinstance(s.get("portfolio_return_pct"), (int, float))]
    worst = min(scen, key=lambda s: s["portfolio_return_pct"]) if scen else None
    tol = st.get("tolerance") if isinstance(st.get("tolerance"), dict) else {}
    holds = any(matches(p, ticker_ids) for p in (d.get("portfolio") or {}).get("positions") or [])
    p = dict(worst_id=worst.get("id") if worst else None,
             worst_label=(worst.get("label") if worst else None),
             worst_loss_pct=round(-worst["portfolio_return_pct"], 2) if worst else None,
             worst_pln_loss=worst.get("pln_loss") if worst else None,
             max_loss_pct=tol.get("max_loss_pct"), breaches=len(tol.get("breaches") or []),
             tolerance_ok=tol.get("ok"), ticker_in_portfolio=holds, scenarios=len(scen))
    if worst is None:
        return _chk("stress_test", UNKNOWN, "stress_empty", source_of(rep), cmd, **p)
    if tol.get("max_loss_pct") is None or tol.get("ok") is None:
        return _chk("stress_test", INFO, "stress_no_tolerance", source_of(rep), **p)
    if not tol["ok"]:
        return _chk("stress_test", WARN, "stress_breach", source_of(rep), **p)
    return _chk("stress_test", PASS, "stress_ok", source_of(rep), **p)


def apply_require(checks: List[Dict], require: Iterable[str]) -> None:
    """A required check that is UNKNOWN fails closed (the user asked for it)."""
    req = set(require or ())
    for c in checks:
        if c["id"] in req and c["status"] == UNKNOWN:
            c["params"] = dict(c["params"], original_reason=c["reason"])
            c["status"], c["reason"] = FAIL, "required_unknown"
            c["required"] = True
        elif c["id"] in req:
            c["required"] = True


def run_context_checks(trade: Dict, nums: Dict, context: Dict) -> Dict:
    """Evaluate the seven context checks. ``context`` keys:

    reports {events, ownership, earnings, quality, macro, dividend, risk_lab} -> find_report dict | None;
    optional ownership_shorts / ownership_insiders override ``ownership`` per check
    reference_date (date), after_close (bool), sector, isins[], strict_events, require[], cfg overrides,
    out_dir (for commands)
    """
    cfg = dict(CONTEXT_DEFAULTS, **(context.get("cfg") or {}))
    reports = context.get("reports") or {}
    ref = context.get("reference_date") or plcal.today_warsaw()
    ids = identity(trade["ticker"], list(context.get("isins") or []) + harvest_isins(trade["ticker"], reports))
    cmds = commands(ids["ticker"], cfg["holding_days"], context.get("out_dir") or "<input-dir>")
    start = window_start(ref, bool(context.get("after_close")))
    checks = [
        check_event_window(trade, nums, ids, reports, cfg, start, bool(context.get("strict_events")), cmds["events"],
                           cmds["earnings"]),
        # one gpw-ownership report per mode: shorts and insiders may come from different files
        check_crowded_short(ids, reports.get("ownership_shorts") or reports.get("ownership"), cmds["ownership"]),
        check_insider_selling(ids, reports.get("ownership_insiders") or reports.get("ownership"), ref, cfg,
                              cmds["ownership"]),
        check_earnings_reaction(ids, reports.get("earnings"), ref, cfg, cmds["earnings"]),
        check_quality(ids, reports.get("quality"), cmds["quality"]),
        check_macro(reports.get("macro"), context.get("sector"), cmds["macro"]),
        check_stress(reports.get("risk_lab"), ids, cmds["risk_lab"]),
    ]
    apply_require(checks, context.get("require") or ())
    missing = [{"input": k, "skill": INPUT_SKILLS[k], "command": cmds[k]} for k in INPUT_SKILLS if not reports.get(k)]
    ew = checks[0]["params"]
    summary = {
        "reference_date": ref.isoformat(),
        "holding_window": ew.get("window"),
        "events_in_window": ew.get("events"),
        "earnings_gap": ew.get("earnings_gap"),
        "isins": sorted(ids["isins"]),
        "strict_events": bool(context.get("strict_events")),
        "require": sorted(set(context.get("require") or ())),
        "missing_inputs": missing,
        "thresholds": cfg,
    }
    for c in checks[1:]:
        summary[c["id"]] = {"status": c["status"], "reason": c["reason"],
                            **{k: v for k, v in c["params"].items() if k not in ("red_flags",)}}
    return {"checks": checks, "context": summary}
