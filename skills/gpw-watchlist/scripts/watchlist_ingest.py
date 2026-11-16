"""gpw-watchlist ingest: turn other skills' JSON reports into watchlist changes.

Pipeline: report -> facts (extractor per producer) -> rules table -> state.

* A *fact* is ``{ticker, kind, date, score, price, extra{}}``. Most producers
  already emit the generic ``signals[]`` array (skills/gpw-signal-postmortem/
  references/signal-contract.md), so the generic extractor reads that; the
  trend screener (no ``signals[]``) and older reports without ``signals[]``
  have dedicated extractors working on their ROADMAP section-4 contracts.
* The *rules table* (:data:`DEFAULT_RULES`, overridable with a JSON file)
  says what a fact kind does: add the ticker (as candidate or watch),
  upgrade candidate -> watch, set a tag, raise an alert, copy pivot/stop.
  No rule can move an item to ``ready``: that needs a human plan.
* Every producer also reports its *coverage* (the tickers it looked at, or
  ``None`` = the whole market). An automatic tag owned by a producer is
  cleared when a newer report of that producer covers the ticker and no
  longer asserts the tag - tags describe the newest evidence, the
  ``sources[]`` list keeps the history.

All functions are pure (state dicts in, change records out).
"""

from __future__ import annotations

import copy
import datetime as dt
from typing import Dict, List, Optional, Set, Tuple

from gpwlib import provenance

import watchlist_state as ws

# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------
# Rule fields:
#   tag            tag set on the item (None = no tag)
#   add            "candidate" | "watch" | None - add the ticker when absent
#   upgrade_to     "watch" | None - automatic candidate -> watch
#   levels         copy pivot/stop from the fact unless the user set them
#   alert          {"code", "severity": high|medium|low} on items already listed
#   max_fact_age_sessions  facts older than this (vs the report's as_of) are
#                  ignored. Default 20 sessions = the primary horizon of
#                  gpw-signal-postmortem (signal-contract.md), i.e. the period
#                  over which the producers' own signals are judged.
#   risk           True -> a warning tag (shown with a warning marker)
DEFAULT_FACT_AGE = 20

DEFAULT_RULES: Dict[str, Dict] = {
    # gpw-trend-screener. "High priority" = the screener's own bands good /
    # strong / textbook (score >= 70, Minervini-style setup with a valid VCP;
    # trend-screener references/methodology.md section 7).
    "screener_watchlist_high": {"tag": "vcp", "add": "watch", "upgrade_to": "watch", "levels": True},
    "screener_watchlist": {"tag": "vcp", "add": "candidate", "levels": True},
    # Trend Template passers without a clean VCP: Stage-2 stocks to watch for
    # a base (Minervini 2013, ch. 5) - candidates only, no levels.
    "screener_candidate": {"tag": "trend_template", "add": "candidate"},
    # The screener says the setup broke (close below the last contraction low).
    "screener_damaged": {"alert": {"code": "setup_damaged", "severity": "high"}, "risk": True,
                         "tag": "setup_damaged"},
    # gpw-quality-screener
    "quality_high": {"tag": "quality_high"},
    "quality_red_flags": {"tag": "quality_red_flags", "risk": True,
                          "alert": {"code": "quality_red_flags", "severity": "medium"}},
    # gpw-ownership. Cluster buying by several insiders is the stronger
    # insider signal (Lakonishok & Lee 2001; Cohen, Malloy & Pomorski 2012),
    # so it may add a candidate; selling only warns.
    "insider_cluster_buy": {"tag": "insider_cluster_buy", "add": "candidate"},
    "insider_cluster_sell": {"tag": "insider_cluster_sell", "risk": True,
                             "alert": {"code": "insider_cluster_sell", "severity": "medium"}},
    "short_crowded": {"tag": "short_crowded", "risk": True},
    "short_new": {"alert": {"code": "short_new", "severity": "low"}},
    "fund_accumulation": {"tag": "fund_accumulation"},
    # gpw-earnings-reaction. On the WSE the drift follows mainly negative
    # surprises (Jaglinski 2020) - so a strong negative reaction is an alert.
    "earnings_strong_negative": {"tag": "earnings_strong_negative", "risk": True,
                                 "alert": {"code": "earnings_strong_negative", "severity": "high"}},
    "earnings_strong_positive": {"tag": "earnings_strong_positive"},
    # gpw-dividend-analyzer
    "dividend_cut_risk": {"tag": "dividend_cut_risk", "risk": True,
                          "alert": {"code": "dividend_cut_risk", "severity": "low"}},
    # gpw-pair-trading: the item is the cheap leg of a dislocated pair.
    "pair_dislocation": {"tag": "pair_dislocation"},
}

RULE_KEYS = {"tag", "add", "upgrade_to", "levels", "alert", "max_fact_age_sessions", "risk"}

# Reports older than this many sessions are not ingested (warning instead).
# 3 = the cross-skill freshness rule (docs/CONVENTIONS.md) for the daily
# screener; 5 = one trading week for skills run weekly (ROADMAP section 3);
# 10 for fundamentals-based screens (data changes quarterly); ownership funds
# come from annual OFE PDFs (one valuation date per year).
DEFAULT_MAX_REPORT_AGE = 5
PRODUCERS: Dict[str, Dict] = {
    "gpw-trend-screener": {"max_report_age_sessions": 3, "order": 0},
    "gpw-ownership": {"max_report_age_sessions": 5, "order": 1, "modes": {"funds": {"max_report_age_sessions": 300}}},
    "gpw-quality-screener": {"max_report_age_sessions": 10, "order": 2},
    "gpw-earnings-reaction": {"max_report_age_sessions": 5, "order": 3},
    "gpw-dividend-analyzer": {"max_report_age_sessions": 10, "order": 4},
    "gpw-pair-trading": {"max_report_age_sessions": 5, "order": 5},
}

# Ingest alerts expire after two trading weeks: they are prompts to look, the
# evidence stays in sources[].
ALERT_TTL_SESSIONS = 10

HIGH_BANDS = {"good", "strong", "textbook"}


def resolve_skill(name: str) -> str:
    n = (name or "").strip().lower()
    if not n.startswith("gpw-"):
        n = "gpw-" + n
    if n not in PRODUCERS:
        raise ws.WatchlistError("unknown producer {!r} (known: {})".format(
            name, ", ".join(s[4:] for s in PRODUCERS)))
    return n


def merge_rules(overrides: Optional[Dict]) -> Dict[str, Dict]:
    """Defaults updated with a user rules file ``{kind: {field: value}|null}``.

    ``null`` disables a kind. Unknown fields raise WatchlistError.
    """
    rules = copy.deepcopy(DEFAULT_RULES)
    for kind, r in (overrides or {}).items():
        if kind.startswith("_"):
            continue  # comments
        if r is None:
            rules.pop(kind, None)
            continue
        if not isinstance(r, dict):
            raise ws.WatchlistError("rule {!r} must be an object or null".format(kind))
        bad = set(r) - RULE_KEYS
        if bad:
            raise ws.WatchlistError("rule {!r}: unknown field(s) {}".format(kind, ", ".join(sorted(bad))))
        if r.get("add") not in (None, "candidate", "watch"):
            raise ws.WatchlistError("rule {!r}: add must be candidate, watch or null".format(kind))
        if r.get("upgrade_to") not in (None, "watch"):
            raise ws.WatchlistError("rule {!r}: upgrade_to can only be watch (never ready)".format(kind))
        al = r.get("alert")
        if al is not None and (not isinstance(al, dict) or al.get("severity") not in ("high", "medium", "low")
                               or not al.get("code")):
            raise ws.WatchlistError("rule {!r}: alert needs code and severity high|medium|low".format(kind))
        rules.setdefault(kind, {}).update(r)
    return rules


# --------------------------------------------------------------------------
# Extractors
# --------------------------------------------------------------------------

def _num(x) -> Optional[float]:
    return float(x) if isinstance(x, (int, float)) and x == x else None


def _fact(ticker, kind, date, score=None, price=None, **extra) -> Optional[Dict]:
    if not ticker or not kind or not isinstance(date, str) or len(date) < 10:
        return None
    try:
        t = ws.norm_ticker(str(ticker))
    except ws.WatchlistError:
        return None
    return {"ticker": t, "kind": kind, "date": date[:10], "score": _num(score), "price": _num(price),
            "extra": {k: v for k, v in extra.items() if v is not None}}


def extract_trend_screener(data: Dict) -> Tuple[List[Dict], Optional[Set[str]]]:
    if data.get("mode") not in (None, "screen"):
        return [], set()
    facts = []
    as_of = (data.get("summary") or {}).get("date") or data.get("as_of") or \
        (data.get("data_provenance") or {}).get("as_of")
    seen = set()
    for row in data.get("watchlist") or []:
        vcp, risk, ex = row.get("vcp") or {}, row.get("risk") or {}, row.get("execution") or {}
        kind = "screener_watchlist_high" if row.get("band") in HIGH_BANDS else "screener_watchlist"
        f = _fact(row.get("name"), kind, row.get("date") or as_of, row.get("score"), row.get("close"),
                  pivot=_num(vcp.get("pivot")), stop=_num(risk.get("stop_structure")), band=row.get("band"),
                  state=ex.get("state"), pct_vs_pivot=_num(ex.get("pct_vs_pivot")),
                  rs=(row.get("rs") or {}).get("rating"))
        if f:
            facts.append(f)
            seen.add(f["ticker"])
    for row in data.get("candidates") or []:
        name = str(row.get("name") or "").upper()
        if name in seen:
            continue
        ex = row.get("execution") or {}
        kind = "screener_damaged" if ex.get("state") == "damaged" else "screener_candidate"
        f = _fact(name, kind, row.get("date") or as_of, row.get("score"), row.get("close"), band=row.get("band"),
                  state=ex.get("state"), rs=(row.get("rs") or {}).get("rating"))
        if f:
            facts.append(f)
            seen.add(f["ticker"])
    for row in data.get("others") or []:
        name = str(row.get("name") or "").upper()
        if name and name not in seen and row.get("state") == "damaged":
            f = _fact(name, "screener_damaged", as_of, row.get("score"), state="damaged")
            if f:
                facts.append(f)
    return facts, None  # the screen covers the whole liquid universe


def extract_signals(data: Dict) -> List[Dict]:
    out = []
    for s in data.get("signals") or []:
        if not isinstance(s, dict):
            continue
        tags = s.get("tags") if isinstance(s.get("tags"), dict) else {}
        f = _fact(s.get("ticker"), s.get("kind"), s.get("date"), s.get("score"), s.get("price"),
                  direction=s.get("direction"), **{k: v for k, v in tags.items() if isinstance(v, (str, int, float))})
        if f:
            out.append(f)
    return out


def _tickers(rows, *keys) -> Set[str]:
    out = set()
    for r in rows or []:
        if isinstance(r, dict):
            for k in keys:
                if r.get(k):
                    out.add(str(r[k]).upper())
    return out


def extract_ownership(data: Dict) -> Tuple[List[Dict], Optional[Set[str]]]:
    mode = data.get("mode")
    facts = extract_signals(data)
    if "signals" not in data:  # fallback on the ROADMAP contract
        as_of = data.get("as_of")
        for r in (data.get("shorts") or {}).get("by_issuer") or []:
            if r.get("crowded"):
                facts.append(_fact(r.get("ticker"), "short_crowded", as_of, min(100.0, 25.0 * (r.get("total_pct") or 0))))
        for r in (data.get("insiders") or {}).get("by_ticker") or []:
            if r.get("cluster_buy"):
                facts.append(_fact(r.get("ticker"), "insider_cluster_buy", r.get("last_date") or as_of))
            if r.get("cluster_sell"):
                facts.append(_fact(r.get("ticker"), "insider_cluster_sell", r.get("last_date") or as_of))
        facts = [f for f in facts if f]
    if mode == "funds":
        return facts, _tickers((data.get("funds") or {}).get("by_ticker"), "ticker")
    if mode == "ticker":
        return facts, {str(data.get("ticker") or "").upper()} - {""} or set()
    return facts, None  # shorts register / insider notices cover the whole market


def extract_earnings(data: Dict) -> Tuple[List[Dict], Optional[Set[str]]]:
    facts = extract_signals(data)
    if "signals" not in data:
        for r in data.get("reactions") or []:
            lab = r.get("label")
            if lab in ("strong_negative", "strong_positive"):
                f = _fact(r.get("ticker"), "earnings_" + lab, r.get("report_date"), car_3d_pct=r.get("car_3d_pct"))
                if f:
                    facts.append(f)
    cov = _tickers(data.get("reactions"), "ticker")
    if isinstance(data.get("tickers"), (list, dict)):  # list of names or {ticker: ...}
        cov |= {str(t).upper() for t in data["tickers"] if isinstance(t, str)}
    return facts, cov


def extract_companies(data: Dict) -> Tuple[List[Dict], Optional[Set[str]]]:
    return extract_signals(data), _tickers(data.get("companies"), "ticker")


def extract_pairs(data: Dict) -> Tuple[List[Dict], Optional[Set[str]]]:
    return extract_signals(data), _tickers(data.get("pairs"), "a", "b")


EXTRACTORS = {
    "gpw-trend-screener": extract_trend_screener,
    "gpw-ownership": extract_ownership,
    "gpw-quality-screener": extract_companies,
    "gpw-earnings-reaction": extract_earnings,
    "gpw-dividend-analyzer": extract_companies,
    "gpw-pair-trading": extract_pairs,
}


# --------------------------------------------------------------------------
# Report selection
# --------------------------------------------------------------------------

def max_report_age(skill: str, mode: Optional[str]) -> int:
    p = PRODUCERS.get(skill, {})
    m = (p.get("modes") or {}).get(mode or "", {})
    return int(m.get("max_report_age_sessions", p.get("max_report_age_sessions", DEFAULT_MAX_REPORT_AGE)))


def select_reports(reports: List[Dict], reference: dt.date) -> Tuple[List[Dict], List[Dict]]:
    """Newest report per (skill, mode); split into (usable, skipped-as-stale).

    ``reports`` are gpwlib.reports.iter_reports rows (``skill, file, path,
    data, as_of, mtime``). A producer that writes one file per mode (e.g.
    gpw-ownership shorts / insiders / funds) contributes each mode.
    """
    newest: Dict[Tuple[str, str], Dict] = {}
    for r in reports:
        if r.get("skill") not in PRODUCERS:
            continue
        key = (r["skill"], str((r.get("data") or {}).get("mode") or ""))
        cur = newest.get(key)
        if cur is None or (r.get("as_of") or "", r.get("mtime") or 0) >= (cur.get("as_of") or "", cur.get("mtime") or 0):
            newest[key] = r
    use, stale = [], []
    for (skill, mode), r in sorted(newest.items(), key=lambda kv: (PRODUCERS[kv[0][0]]["order"], kv[0][1])):
        a = r.get("as_of")
        behind = provenance.sessions_behind(dt.date.fromisoformat(a), reference) if a else None
        row = dict(r, mode=mode or None, sessions_behind=behind, max_age=max_report_age(skill, mode or None))
        if behind is None or behind > row["max_age"]:
            stale.append(row)
        else:
            use.append(row)
    return use, stale


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------

def _age_ok(fact: Dict, report_as_of: Optional[str], rule: Dict) -> bool:
    if not report_as_of:
        return True
    try:
        d = dt.date.fromisoformat(fact["date"])
        ref = dt.date.fromisoformat(report_as_of)
    except ValueError:
        return False
    if d > ref:
        return False
    return provenance.sessions_behind(d, ref) <= int(rule.get("max_fact_age_sessions", DEFAULT_FACT_AGE))


def apply_report(state: Dict, report: Dict, rules: Dict[str, Dict], today: dt.date) -> Dict:
    """Apply one selected report. Returns ``{"changes": [...], "facts": n, "used": n, "ignored": n}``."""
    skill, mode = report["skill"], report.get("mode")
    data = report.get("data") or {}
    facts, coverage = EXTRACTORS[skill](data)
    changes: List[Dict] = []
    used = ignored = 0
    asserted: Dict[str, Set[str]] = {}
    for f in facts:
        rule = rules.get(f["kind"])
        if rule is None or not _age_ok(f, report.get("as_of"), rule):
            ignored += 1
            continue
        res = _apply_fact(state, f, rule, skill, mode, report, today, changes)
        if res == "ignored":
            ignored += 1
            continue
        used += 1
        if rule.get("tag"):
            asserted.setdefault(f["ticker"], set()).add(rule["tag"])
    # clear automatic tags this producer no longer asserts for covered tickers
    for it in state["items"]:
        if it["stage"] == "dropped" or (coverage is not None and it["ticker"] not in coverage):
            continue
        for tag, meta in list((it.get("tag_meta") or {}).items()):
            if meta.get("origin") != "auto" or meta.get("skill") != skill:
                continue
            if (meta.get("mode") or None) != (mode or None):
                continue
            if tag in asserted.get(it["ticker"], set()):
                continue
            if meta.get("date") and report.get("as_of") and meta["date"] > report["as_of"]:
                continue  # tag comes from a newer report than this one
            ws.remove_tag(it, tag)
            it["updated"] = today.isoformat()
            changes.append({"ticker": it["ticker"], "change": "tag_cleared", "tag": tag, "skill": skill})
    return {"changes": changes, "facts": len(facts), "used": used, "ignored": ignored}


def _apply_fact(state, f, rule, skill, mode, report, today, changes) -> str:
    t = f["ticker"]
    item = ws.find_item(state, t)
    src = {"skill": skill, "mode": mode, "kind": f["kind"], "date": f["date"], "score": f["score"],
           "report_file": report.get("file"), "report_as_of": report.get("as_of")}
    if item is None:
        if not rule.get("add"):
            return "ignored"
        item = ws.new_item(t, today, rule["add"], source=skill)
        item["history"][-1]["reason"] = "ingest: {} ({})".format(f["kind"], f["date"])
        state["items"].append(item)
        changes.append({"ticker": t, "change": "added", "to": rule["add"], "skill": skill, "kind": f["kind"]})
    is_new = ws.add_source(item, src)
    if item["stage"] == "dropped":
        if is_new:
            changes.append({"ticker": t, "change": "seen_while_dropped", "skill": skill, "kind": f["kind"]})
        return "used"
    touched = is_new
    up = rule.get("upgrade_to")
    if up and ws.can_transition(item["stage"], up, auto=True):
        frm = item["stage"]
        ws.transition(item, up, today, "ingest: {} ({})".format(f["kind"], f["date"]), by=skill, auto=True)
        changes.append({"ticker": t, "change": "stage", "from": frm, "to": up, "skill": skill, "kind": f["kind"]})
        touched = True
    if rule.get("tag") and ws.add_tag(item, rule["tag"], skill=skill, date=f["date"], mode=mode):
        changes.append({"ticker": t, "change": "tag", "tag": rule["tag"], "skill": skill})
        touched = True
    ex = f["extra"]
    # levels follow the producer only while the user has not set them and the
    # item is not yet ready (a ready plan's pivot/stop are frozen)
    if rule.get("levels") and item["stage"] in ("candidate", "watch") and item.get("levels_source") in (None, skill) \
            and ex.get("pivot") and ex.get("stop") \
            and ex["stop"] < ex["pivot"]:
        before = (item.get("pivot"), item.get("stop"))
        if ws.set_levels(item, dt.date.fromisoformat(f["date"]), pivot=ex["pivot"], stop=ex["stop"], by=skill):
            changes.append({"ticker": t, "change": "levels", "skill": skill, "from": list(before),
                            "to": [item["pivot"], item["stop"]]})
            touched = True
    if skill == "gpw-trend-screener":
        item["screener"] = {"date": f["date"], "score": f["score"], "band": ex.get("band"), "state": ex.get("state"),
                            "rs": ex.get("rs"), "pct_vs_pivot": ex.get("pct_vs_pivot"), "kind": f["kind"]}
    al = rule.get("alert")
    if al and is_new and _sessions_since(f["date"], today) <= ALERT_TTL_SESSIONS:
        alert = {"code": al["code"], "severity": al["severity"], "date": f["date"], "origin": "ingest",
                 "skill": skill, "kind": f["kind"], "score": f["score"]}
        if ws.add_alert(item, alert):
            changes.append({"ticker": t, "change": "alert", "code": al["code"], "severity": al["severity"],
                            "skill": skill})
            touched = True
    if touched:
        item["updated"] = today.isoformat()
    return "used"


def _sessions_since(date: str, today: dt.date) -> int:
    try:
        return provenance.sessions_behind(dt.date.fromisoformat(date[:10]), today)
    except ValueError:
        return 10 ** 6


def expire_alerts(state: Dict, today: dt.date, ttl_sessions: int = ALERT_TTL_SESSIONS) -> int:
    """Drop ingest alerts older than ``ttl_sessions`` sessions. Returns count removed."""
    n = 0
    for it in state["items"]:
        keep = []
        for a in it.get("alerts") or []:
            try:
                d = dt.date.fromisoformat(str(a.get("date"))[:10])
            except ValueError:
                d = None
            if a.get("origin") == "ingest" and (d is None or provenance.sessions_behind(d, today) > ttl_sessions):
                n += 1
                continue
            keep.append(a)
        it["alerts"] = keep
    return n


def ingest(state: Dict, reports: List[Dict], rules: Dict[str, Dict], today: dt.date,
           only: Optional[Set[str]] = None) -> Dict:
    """Select the newest usable report per producer/mode and apply them.

    Returns ``{"reports": [...], "stale": [...], "changes": [...]}``.
    """
    if only:
        reports = [r for r in reports if r.get("skill") in only]
    use, stale = select_reports(reports, today)
    all_changes, used_rows = [], []
    for r in use:
        res = apply_report(state, r, rules, today)
        all_changes.extend(res["changes"])
        used_rows.append({"skill": r["skill"], "mode": r.get("mode"), "file": r.get("file"), "as_of": r.get("as_of"),
                          "sessions_behind": r.get("sessions_behind"), "facts": res["facts"], "used": res["used"],
                          "ignored": res["ignored"], "changes": len(res["changes"])})
    expired = expire_alerts(state, today)
    if expired:
        all_changes.append({"ticker": None, "change": "alerts_expired", "count": expired})
    return {"reports": used_rows,
            "stale": [{"skill": r["skill"], "mode": r.get("mode"), "file": r.get("file"), "as_of": r.get("as_of"),
                       "sessions_behind": r.get("sessions_behind"), "max_age": r.get("max_age")} for r in stale],
            "changes": all_changes}
