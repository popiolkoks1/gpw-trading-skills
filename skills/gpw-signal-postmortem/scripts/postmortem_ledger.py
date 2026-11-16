"""Signal ledger for gpw-signal-postmortem (pure: no market data, no network).

Turns JSON reports of other GPW skills into normalised *signal records*,
deduplicates them by ``(source, ticker, date, kind)`` and keeps them in one
JSON ledger under ``--state-dir`` (default ``~/.gpw-trading-skills/signals``).

Adapters
--------
* ``gpw-trend-screener`` (mode ``screen``): watchlist items -> ``long`` signals
  (kind ``vcp_setup`` when the VCP is valid, else ``trend_leader``); other
  Trend-Template candidates -> ``skip`` near misses (kind ``near_miss``), used
  only to measure missed opportunities. Ticker / history modes are not screens
  and are rejected.
* ``gpw-market-regime``: every state transition of every analysed index in the
  report's history (WIG, WIG20, mWIG40, sWIG80) becomes an index signal
  (``ftd`` long, ``regime_confirmed_uptrend`` long, ``distribution_cluster`` /
  ``regime_uptrend_under_pressure`` exit, ``regime_correction`` /
  ``regime_bear`` avoid; a rally attempt is "wait", so it is not a signal).
  The WIG history is also stored as a regime *timeline* used to tag every
  signal with the regime state on its date.
* ``gpw-market-breadth`` / ``gpw-sector-trend``: the composite score becomes an
  exposure signal on WIG (score >= 60 long, < 40 avoid; 40-60 is the neutral
  zone of both skills and makes no directional claim). Sector-trend also emits
  one signal per sector index (trend up -> long, trend down -> avoid).
* Any other report with a top-level ``signals[]`` array: the generic contract
  (see :data:`GENERIC_CONTRACT` and references/signal-contract.md).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SKILL = "gpw-signal-postmortem"
LEDGER_SCHEMA = 1
LEDGER_FILE = "ledger.json"
DIRECTIONS = ("long", "avoid", "exit")
INTERNAL_DIRECTIONS = DIRECTIONS + ("skip",)  # "skip" = screened but not selected (near miss)

GENERIC_CONTRACT = {
    "required": {
        "ticker": "GPW short name as in the GPW archive (e.g. PKOBP) or an index name (WIG, WIG20, WIG-BANKI)",
        "date": "YYYY-MM-DD, the session whose close produced the signal; must not be after the report's as_of",
        "kind": "lower-case slug [a-z0-9_]{1,40}, e.g. insider_cluster_buy, earnings_strong_negative",
        "direction": "long | avoid | exit",
    },
    "optional": {
        "score": "number 0-100, higher = stronger conviction (bucketed into quintiles)",
        "price": "close on the signal date as the emitting skill saw it (unadjusted) - used to rescale levels",
        "pivot": "price level that confirms the signal (unadjusted, same basis as price)",
        "invalidation": "price level that invalidates the signal (stop; unadjusted)",
        "tags": "object of short string/number/bool values, kept for reference (not used as dimensions)",
    },
}

_KIND_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_TICKER_RE = re.compile(r"^[A-Za-z0-9.\-]{1,20}$")

# Composite-score cut-offs for breadth / sector-trend exposure signals: both
# skills use 20-point zones and call 40-60 "neutral" (breadth_engine.ZONES,
# sector_engine.ZONES), so only scores outside that band make a claim.
EXPOSURE_LONG_MIN = 60.0
EXPOSURE_AVOID_BELOW = 40.0

REGIME_KIND_DIRECTION = {
    "confirmed_uptrend": ("regime_confirmed_uptrend", "long"),
    "uptrend_under_pressure": ("regime_uptrend_under_pressure", "exit"),
    "correction": ("regime_correction", "avoid"),
    "bear": ("regime_bear", "avoid"),
}


class LedgerError(ValueError):
    """Ledger file unreadable or a report cannot be ingested."""


# --------------------------------------------------------------------- utils

def signal_id(source: str, ticker: str, date: str, kind: str) -> str:
    key = "|".join([source, ticker.upper(), date, kind])
    return "sig_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _date(val) -> Optional[str]:
    if not isinstance(val, str) or len(val) < 10:
        return None
    try:
        return dt.date.fromisoformat(val[:10]).isoformat()
    except ValueError:
        return None


def _num(val) -> Optional[float]:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    f = float(val)
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def report_as_of(data: Dict) -> Optional[str]:
    for val in (data.get("as_of"), (data.get("data_provenance") or {}).get("as_of")
                if isinstance(data.get("data_provenance"), dict) else None,
                (data.get("regime") or {}).get("as_of") if isinstance(data.get("regime"), dict) else None):
        d = _date(val)
        if d:
            return d
    return None


def make_signal(source: str, ticker: str, date: str, kind: str, direction: str, score=None,
                attrs: Optional[Dict] = None, report: Optional[Dict] = None) -> Dict:
    return {
        "id": signal_id(source, ticker, date, kind),
        "source": source,
        "ticker": ticker.strip(),
        "date": date,
        "kind": kind,
        "direction": direction,
        "score": _num(score),
        "attrs": attrs or {},
        "report": report or {},
    }


# ------------------------------------------------------------------ adapters

def _screener(data: Dict, rep: Dict, near_misses: bool) -> Tuple[List[Dict], List[Dict]]:
    mode = data.get("mode")
    if mode != "screen":
        return [], [{"reason": "screener_mode_not_screen", "detail": str(mode)}]
    watch = {w.get("name") for w in data.get("watchlist") or [] if isinstance(w, dict)}
    out, rejected = [], []
    items = list(data.get("candidates") or [])
    seen = {c.get("name") for c in items if isinstance(c, dict)}
    items += [w for w in data.get("watchlist") or [] if isinstance(w, dict) and w.get("name") not in seen]
    default_date = report_as_of(data)
    for c in items:
        if not isinstance(c, dict) or not c.get("name"):
            rejected.append({"reason": "bad_item"})
            continue
        date = _date(c.get("date")) or default_date
        if not date:
            rejected.append({"reason": "no_date", "ticker": c.get("name")})
            continue
        vcp = c.get("vcp") or {}
        ex = c.get("execution") or {}
        risk = c.get("risk") or {}
        on_wl = c["name"] in watch
        if on_wl:
            kind, direction = ("vcp_setup" if vcp.get("valid") else "trend_leader"), "long"
        elif near_misses:
            kind, direction = "near_miss", "skip"
        else:
            continue
        attrs = {
            "execution_state": ex.get("state"),
            "pct_vs_pivot": _num(ex.get("pct_vs_pivot")),
            "vcp_valid": bool(vcp.get("valid")),
            "vcp_quality": _num(vcp.get("quality")),
            "rs_rating": _num((c.get("rs") or {}).get("rating")),
            "template_pass": c.get("template_pass"),
            "band": c.get("band"),
            "on_watchlist": on_wl,
            "price": _num(c.get("close")),
            "pivot": _num(vcp.get("pivot")) if vcp.get("pivot") is not None else _num(risk.get("entry_pivot")),
            "invalidation": _num(risk.get("stop_structure")) if risk.get("stop_structure") is not None
            else _num(risk.get("stop_atr")),
            "risk_pct": _num(risk.get("risk_pct_structure")),
        }
        out.append(make_signal("gpw-trend-screener", c["name"], date, kind, direction, c.get("score"), attrs, rep))
    return out, rejected


def regime_timeline(data: Dict, index: str = "WIG") -> Optional[Dict]:
    """State change points of one index from a gpw-market-regime report."""
    hist = data.get("history") or {}
    reg = data.get("regime") or {}
    idx = (data.get("indices") or {}).get(index) or {}
    end = _date(idx.get("as_of")) or _date(reg.get("as_of")) or report_as_of(data)
    start = _date(hist.get("from"))
    trans = idx.get("transitions")
    if trans is None and index == hist.get("index", "WIG"):
        trans = hist.get("transitions")
    if not start or not end or trans is None:
        return None
    if trans:
        first_state = trans[0].get("from")
    else:
        first_state = idx.get("state") or (reg.get("state") if index == "WIG" else None)
    if index == hist.get("index", "WIG") and hist.get("state_before"):
        first_state = hist["state_before"] if not trans else first_state
    points = [{"date": start, "state": first_state}]
    for t in trans:
        d = _date(t.get("date"))
        if d and t.get("to"):
            points.append({"date": d, "state": t["to"]})
    return {"index": index, "start": start, "end": end, "points": points}


def _regime(data: Dict, rep: Dict) -> Tuple[List[Dict], List[Dict]]:
    out = []
    indices = data.get("indices") or {}
    if not indices and isinstance(data.get("history"), dict):
        indices = {data["history"].get("index", "WIG"): {"transitions": data["history"].get("transitions") or []}}
    for name, idx in indices.items():
        for t in (idx or {}).get("transitions") or []:
            date = _date(t.get("date"))
            to = t.get("to")
            if not date:
                continue
            if t.get("event") == "follow_through_day":
                kind, direction = "ftd", "long"
            elif to == "uptrend_under_pressure" and "distribution_high" in (t.get("why") or []):
                kind, direction = "distribution_cluster", "exit"
            elif to in REGIME_KIND_DIRECTION:
                kind, direction = REGIME_KIND_DIRECTION[to]
            else:
                continue  # rally_attempt = wait for a follow-through day, no claim
            attrs = {"from_state": t.get("from"), "to_state": to, "event": t.get("event"),
                     "why": t.get("why") or [], "dd_count": t.get("dd_count"),
                     "drawdown_pct": _num(t.get("drawdown_pct"))}
            out.append(make_signal("gpw-market-regime", name, date, kind, direction, None, attrs, rep))
    return out, []


def _exposure(source: str, prefix: str, data: Dict, rep: Dict) -> List[Dict]:
    comp = data.get("composite") or {}
    score = _num(comp.get("score"))
    date = report_as_of(data)
    if score is None or not date:
        return []
    if score >= EXPOSURE_LONG_MIN:
        kind, direction = prefix + "_supportive", "long"
    elif score < EXPOSURE_AVOID_BELOW:
        kind, direction = prefix + "_weak", "avoid"
    else:
        return []
    attrs = {"zone": comp.get("zone"), "exposure": comp.get("exposure")}
    return [make_signal(source, "WIG", date, kind, direction, score, attrs, rep)]


def _breadth(data: Dict, rep: Dict) -> Tuple[List[Dict], List[Dict]]:
    return _exposure("gpw-market-breadth", "breadth", data, rep), []


def _sector(data: Dict, rep: Dict) -> Tuple[List[Dict], List[Dict]]:
    out = _exposure("gpw-sector-trend", "sector_trend", data, rep)
    date = report_as_of(data)
    for s in data.get("sectors") or []:
        if not isinstance(s, dict) or not s.get("key") or not date or s.get("low_sample"):
            continue
        trend = s.get("trend")
        if trend == "up":
            kind, direction = "sector_uptrend", "long"
        elif trend == "down":
            kind, direction = "sector_downtrend", "avoid"
        else:
            continue
        ratio = _num(s.get("ratio"))
        attrs = {"status": s.get("status"), "group": s.get("group"), "ratio": ratio,
                 "vs_wig_pp": _num(s.get("vs_wig_pp"))}
        out.append(make_signal("gpw-sector-trend", s["key"], date, kind, direction,
                               ratio * 100 if ratio is not None else None, attrs, rep))
    return out, []


def validate_generic(item, as_of: Optional[str]) -> Optional[str]:
    """Return None when ``item`` satisfies the generic contract, else a reason code."""
    if not isinstance(item, dict):
        return "not_an_object"
    t = item.get("ticker")
    if not isinstance(t, str) or not _TICKER_RE.match(t):
        return "bad_ticker"
    d = _date(item.get("date"))
    if not d or len(str(item.get("date"))) != 10:
        return "bad_date"
    if as_of and d > as_of:
        return "date_after_as_of"
    k = item.get("kind")
    if not isinstance(k, str) or not _KIND_RE.match(k):
        return "bad_kind"
    if item.get("direction") not in DIRECTIONS:
        return "bad_direction"
    if "score" in item and item["score"] is not None:
        s = _num(item["score"])
        if s is None or not 0 <= s <= 100:
            return "bad_score"
    for f in ("price", "pivot", "invalidation"):
        if f in item and item[f] is not None:
            v = _num(item[f])
            if v is None or v <= 0:
                return "bad_" + f
    if "tags" in item and item["tags"] is not None and not isinstance(item["tags"], dict):
        return "bad_tags"
    return None


def _generic(data: Dict, rep: Dict) -> Tuple[List[Dict], List[Dict]]:
    source = data.get("skill")
    if not isinstance(source, str) or not source:
        return [], [{"reason": "generic_report_without_skill"}]
    as_of = report_as_of(data)
    out, rejected = [], []
    for i, item in enumerate(data.get("signals") or []):
        why = validate_generic(item, as_of)
        if why:
            rejected.append({"index": i, "reason": why,
                             "ticker": item.get("ticker") if isinstance(item, dict) else None})
            continue
        tags = {k: v for k, v in (item.get("tags") or {}).items()
                if isinstance(v, (str, int, float, bool)) and len(str(v)) <= 80}
        attrs = {"price": _num(item.get("price")), "pivot": _num(item.get("pivot")),
                 "invalidation": _num(item.get("invalidation")), "tags": tags}
        out.append(make_signal(source, item["ticker"], item["date"][:10], item["kind"], item["direction"],
                               item.get("score"), attrs, rep))
    return out, rejected


ADAPTERS = {
    "gpw-trend-screener": "screener",
    "gpw-market-regime": "regime",
    "gpw-market-breadth": "breadth",
    "gpw-sector-trend": "sector",
}


def extract(data: Dict, file_name: str = "", near_misses: bool = True) -> Dict:
    """Signals from one report -> ``{source, as_of, signals, rejected, timeline}``."""
    if not isinstance(data, dict):
        raise LedgerError("report is not a JSON object")
    skill = data.get("skill")
    if skill == SKILL:
        raise LedgerError("this is a gpw-signal-postmortem report, not a signal source")
    as_of = report_as_of(data)
    rep = {"file": file_name, "as_of": as_of}
    timeline = None
    if skill == "gpw-trend-screener":
        sig, rej = _screener(data, rep, near_misses)
    elif skill == "gpw-market-regime":
        sig, rej = _regime(data, rep)
        timeline = regime_timeline(data, "WIG")
    elif skill == "gpw-market-breadth":
        sig, rej = _breadth(data, rep)
    elif skill == "gpw-sector-trend":
        sig, rej = _sector(data, rep)
    elif isinstance(data.get("signals"), list):
        sig, rej = _generic(data, rep)
    else:
        raise LedgerError("unsupported report (skill={!r}): no adapter and no top-level signals[] array".format(skill))
    # A generic signals[] block in a known skill's report is honoured too.
    if skill in ADAPTERS and isinstance(data.get("signals"), list):
        s2, r2 = _generic(data, rep)
        sig, rej = sig + s2, rej + r2
    return {"source": skill, "as_of": as_of, "signals": sig, "rejected": rej, "timeline": timeline}


# -------------------------------------------------------------------- ledger

def default_state_dir() -> Path:
    return Path.home() / ".gpw-trading-skills" / "signals"


def empty_ledger() -> Dict:
    return {"schema": LEDGER_SCHEMA, "skill": SKILL, "signals": [], "regime_timelines": [], "ingested": []}


def load_ledger(state_dir: Path) -> Dict:
    p = Path(state_dir).expanduser() / LEDGER_FILE
    if not p.exists():
        return empty_ledger()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LedgerError("cannot read ledger {}: {}".format(p, exc))
    if not isinstance(data, dict) or not isinstance(data.get("signals"), list):
        raise LedgerError("ledger {} is not a gpw-signal-postmortem ledger".format(p))
    for k, v in empty_ledger().items():
        data.setdefault(k, v)
    return data


def save_ledger(state_dir: Path, ledger: Dict) -> Path:
    d = Path(state_dir).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    p = d / LEDGER_FILE
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return p


def dedupe_key(sig: Dict) -> Tuple[str, str, str, str]:
    return (sig["source"], sig["ticker"].upper(), sig["date"], sig["kind"])


def merge(ledger: Dict, extracted: Dict, file_path: str = "", recorded_at: Optional[str] = None) -> Dict:
    """Add new signals; duplicates (same source, ticker, date, kind) are skipped."""
    recorded_at = recorded_at or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    have = {dedupe_key(s) for s in ledger["signals"]}
    new = dup = 0
    for s in extracted["signals"]:
        k = dedupe_key(s)
        if k in have:
            dup += 1
            continue
        have.add(k)
        s = dict(s, recorded_at=recorded_at)
        ledger["signals"].append(s)
        new += 1
    tl = extracted.get("timeline")
    if tl:
        key = (tl["index"], tl["start"], tl["end"])
        if not any((t["index"], t["start"], t["end"]) == key for t in ledger["regime_timelines"]):
            ledger["regime_timelines"].append(tl)
    ledger["signals"].sort(key=lambda s: (s["date"], s["source"], s["ticker"], s["kind"]))
    entry = {"file": file_path, "source": extracted["source"], "as_of": extracted["as_of"], "new": new,
             "duplicates": dup, "rejected": len(extracted["rejected"]), "recorded_at": recorded_at}
    ledger["ingested"].append(entry)
    return entry


# ---------------------------------------------------------------- timelines

def regime_at(timelines: List[Dict], date: str, index: str = "WIG") -> Optional[str]:
    """Regime state on ``date`` from the newest timeline that covers it."""
    best = None
    for tl in timelines:
        if tl.get("index") != index or not (tl["start"] <= date <= tl["end"]):
            continue
        if best is None or tl["end"] > best["end"]:
            best = tl
    if best is None:
        return None
    state = None
    for p in best["points"]:
        if p["date"] <= date:
            state = p["state"]
        else:
            break
    return state
