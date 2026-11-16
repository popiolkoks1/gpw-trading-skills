"""Net short positions from the KNF public register (pure engine + fetch helpers).

Source (verified 2026-11-16, KNF robots.txt allows it)::

    POST https://rss.knf.gov.pl/rss_pub/JSON
    request={"cmd":"get","search":[],"limit":1000,"offset":0,"method":"Default"|"RssHTable",
             "sort":[],"searchLogic":"AND","searchValue":""}

``Default`` = current register (positions >= 0.5% now), ``RssHTable`` =
history of every published disclosure since 2012-11-01 (~2 300 rows). Row::

    {"HOLDER_FULL_NAME", "ISSUER_NAME", "ISIN", "NET_SHORT_POSITION_O" ("0.61"),
     "POSITION_DATE" (calculation date), "MODIFY_DATE" (publication), "recid"}

Legal frame (Regulation (EU) No 236/2012, SSR): a net short position in
shares must be notified to the competent authority from 0.1% of issued share
capital (art. 5(2) as amended by Commission Delegated Regulation (EU) 2022/27)
and DISCLOSED TO THE PUBLIC from 0.5% and each 0.1% above (art. 6(2)).
Positions below 0.5% are therefore invisible to us; the register is a LOWER
BOUND of total short interest. The KNF history keeps only published rows: when
a holder falls below 0.5% the pair simply disappears from the current list, so
the exact closing date is unknown (between the last disclosure and the first
snapshot without it). Local snapshots (state dir) make it exact from the first
run on.
"""

from __future__ import annotations

import datetime as dt
import html as _html
import json
import re
from typing import Dict, Iterable, List, Optional, Tuple

KNF_URL = "https://rss.knf.gov.pl/rss_pub/JSON"
KNF_PAGE = "https://rss.knf.gov.pl/rss_pub/rss.html"
KNF_HIST_PAGE = "https://rss.knf.gov.pl/rss_pub/rssH.html"
PAGE_LIMIT = 1000
MAX_PAGES = 20
CURRENT_TTL_S = 6 * 3600     # KNF publishes once a trading day (by 15:30, SSR art. 9(2))
HISTORY_TTL_S = 24 * 3600

PUBLIC_THRESHOLD_PCT = 0.5   # SSR art. 6(2)
# Crowding thresholds (see references/methodology.md):
# * Desai et al. (2002) call Nasdaq stocks with short interest >= 2.5% of shares
#   "heavily shorted" and find -0.76% to -1.13% monthly abnormal returns. The
#   KNF register is a lower bound (only >= 0.5% holders), so 2.0% disclosed is
#   our proxy for the "heavily shorted" bucket.
# * >= 3 independent disclosed holders: each one already holds >= 0.5%, so
#   three is a consensus of large funds (Jones, Reed & Waller 2016 find that
#   disclosed positions and the number of disclosers predict negative returns).
CROWDED_TOTAL_PCT = 2.0
CROWDED_HOLDERS = 3
# Days to cover >= 5: practitioner rule of thumb for squeeze-prone names; Hong
# et al. (2016) show days-to-cover is a better crowding measure than SI alone.
HIGH_DTC_DAYS = 5.0
CHANGE_WINDOW_SESSIONS = 20  # ~1 month of sessions, the contract's change_20s_pp


def request_form(method: str, offset: int = 0, limit: int = PAGE_LIMIT) -> Dict[str, str]:
    if method not in ("Default", "RssHTable"):
        raise ValueError("method must be Default or RssHTable")
    payload = {"cmd": "get", "search": [], "limit": limit, "offset": offset, "method": method,
               "sort": [], "searchLogic": "AND", "searchValue": ""}
    return {"request": json.dumps(payload, separators=(",", ":"))}


def _pct(text) -> Optional[float]:
    if text is None:
        return None
    s = str(text).strip().replace(",", ".").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def _date(text) -> Optional[str]:
    s = (text or "").strip()[:10]
    try:
        return dt.date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def parse_records(payload: Dict) -> List[Dict]:
    """KNF JSON payload -> normalised rows (bad rows dropped)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("KNF response has no 'records' list")
    out = []
    for r in payload["records"]:
        if not isinstance(r, dict):
            continue
        pct = _pct(r.get("NET_SHORT_POSITION_O"))
        pdate = _date(r.get("POSITION_DATE"))
        isin = (r.get("ISIN") or "").strip().upper()
        holder = " ".join(_html.unescape(r.get("HOLDER_FULL_NAME") or "").split())
        if pct is None or not pdate or not holder or not isin:
            continue
        out.append({
            "holder": holder,
            "issuer": " ".join(_html.unescape(r.get("ISSUER_NAME") or "").split()),
            "isin": isin,
            "pct": pct,
            "position_date": pdate,
            "published": _date(r.get("MODIFY_DATE")),
        })
    return out


def _key(row: Dict) -> Tuple[str, str]:
    return (row["holder"].upper().rstrip(" .,"), row["isin"])


def fetch(method: str, http_mod, cache_dir=None, offline: bool = False, ttl: Optional[float] = None) -> Dict:
    """All pages of one register -> {rows, total, retrieved_at, from_cache}."""
    ttl = ttl if ttl is not None else (CURRENT_TTL_S if method == "Default" else HISTORY_TTL_S)
    rows: List[Dict] = []
    total, retrieved, from_cache = None, [], True
    for page in range(MAX_PAGES):
        resp = http_mod.post(KNF_URL, request_form(method, page * PAGE_LIMIT), cache_dir=cache_dir,
                             ttl_seconds=ttl, offline=offline)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ValueError("KNF returned non-JSON data: {}".format(exc))
        got = parse_records(payload)
        total = payload.get("total", total)
        retrieved.append(resp.retrieved_at)
        from_cache = from_cache and resp.from_cache
        rows.extend(got)
        n_raw = len(payload.get("records") or [])
        if n_raw < PAGE_LIMIT or (isinstance(total, int) and (page + 1) * PAGE_LIMIT >= total):
            break
    return {"rows": rows, "total": total, "retrieved_at": max(retrieved) if retrieved else None,
            "from_cache": from_cache}


# --------------------------------------------------------------------------
# Reconstruction and aggregation
# --------------------------------------------------------------------------

def pair_histories(history: Iterable[Dict], current: Iterable[Dict]) -> Dict[Tuple[str, str], List[Dict]]:
    """{(holder, isin): disclosures sorted by position_date} (current rows merged in)."""
    out: Dict[Tuple[str, str], List[Dict]] = {}
    seen = set()
    for r in list(history) + list(current):
        sig = (_key(r), r["position_date"], r["pct"])
        if sig in seen:
            continue
        seen.add(sig)
        out.setdefault(_key(r), []).append(r)
    for v in out.values():
        v.sort(key=lambda r: (r["position_date"], r.get("published") or ""))
    return out


def open_at(pairs: Dict, current_keys: set, day: str, snapshot_keys: Optional[set] = None) -> Dict[Tuple[str, str], Dict]:
    """Best estimate of positions open on ``day`` -> {key: latest row <= day}.

    A pair counts as open on ``day`` when it has a disclosure on or before
    ``day`` and EITHER it is still in the current register, OR it had another
    disclosure after ``day`` (so it was still >= 0.5% then), OR it was in a
    local snapshot taken on ``day`` (``snapshot_keys``). Pairs whose last
    disclosure is <= ``day`` and that have since left the register are
    ambiguous and are NOT counted (lower bound).
    """
    out = {}
    for k, rows in pairs.items():
        before = [r for r in rows if r["position_date"] <= day]
        if not before:
            continue
        later = any(r["position_date"] > day for r in rows)
        if k in current_keys or later or (snapshot_keys is not None and k in snapshot_keys):
            out[k] = before[-1]
    return out


def first_date_of_episode(rows: List[Dict], gap_days: int = 365) -> str:
    """Start of the latest continuous run of disclosures (gaps > ``gap_days``
    are treated as a new episode, because a pair that left the register and
    came back much later is a new position)."""
    start = rows[-1]["position_date"]
    for prev, cur in zip(reversed(rows[:-1]), reversed(rows)):
        d0, d1 = dt.date.fromisoformat(prev["position_date"]), dt.date.fromisoformat(cur["position_date"])
        if (d1 - d0).days > gap_days:
            break
        start = prev["position_date"]
    return start


def _close_on_or_before(series, day: str) -> Optional[Tuple[str, float]]:
    if series is None:
        return None
    idx = None
    for i, d in enumerate(series.dates):
        if d <= day:
            idx = i
        else:
            break
    return (series.dates[idx], series.close[idx]) if idx is not None else None


def price_change_since(series, day: str) -> Optional[float]:
    """% change of the adjusted close from the session on/before ``day`` to the last one."""
    base = _close_on_or_before(series, day)
    if base is None or not series.close:
        return None
    if base[1] <= 0:
        return None
    return (series.close[-1] / base[1] - 1.0) * 100.0


def median_volume_shares(series, n: int = 20) -> Optional[float]:
    """Median daily volume in shares over ``n`` sessions = turnover / close (approximation:
    the archive gives turnover in thousand PLN, not share volume)."""
    if series is None or len(series) == 0:
        return None
    vols = []
    for t, c in zip(series.turnover_k[-n:], series.close[-n:]):
        if c and c > 0 and t is not None:
            vols.append(t * 1000.0 / c)
    if not vols:
        return None
    vols.sort()
    m = len(vols) // 2
    return vols[m] if len(vols) % 2 else (vols[m - 1] + vols[m]) / 2


def session_before(dates: List[str], end: str, n: int) -> Optional[str]:
    """The session ``n`` sessions before ``end`` in ``dates`` (sorted)."""
    idx = [i for i, d in enumerate(dates) if d <= end]
    if not idx:
        return None
    i = idx[-1] - n
    return dates[i] if i >= 0 else None


def aggregate(current: List[Dict], history: List[Dict], as_of: str, *, ticker_of=None,
              panel=None, shares_of=None, session_dates: Optional[List[str]] = None,
              since: Optional[str] = None, prev_snapshot: Optional[Dict] = None,
              snapshot_20s: Optional[Dict] = None) -> Dict:
    """Per-issuer aggregation of the current register.

    ``ticker_of(isin, issuer_name) -> ticker|None``; ``panel`` {ticker: Series};
    ``shares_of(ticker, isin) -> shares outstanding|None``; ``session_dates``
    sorted session dates (for the 20-session look-back); ``since`` optional
    ISO date for a second change window; ``prev_snapshot`` / ``snapshot_20s``:
    local snapshots ``{"date", "rows"}`` (exact new/closed events).
    """
    ticker_of = ticker_of or (lambda isin, name: None)
    panel = panel or {}
    shares_of = shares_of or (lambda t, i: None)
    pairs = pair_histories(history, current)
    cur_keys = {_key(r) for r in current}
    dates = session_dates or []
    d20 = session_before(dates, as_of, CHANGE_WINDOW_SESSIONS) if dates else None
    if d20 is None:
        d20 = (dt.date.fromisoformat(as_of) - dt.timedelta(days=28)).isoformat()
    snap20_keys = {_key(r) for r in snapshot_20s["rows"]} if snapshot_20s else None
    past = open_at(pairs, cur_keys, d20, snap20_keys)
    if snapshot_20s and snapshot_20s.get("date") == d20:
        past = {_key(r): r for r in snapshot_20s["rows"]}
    past_since = open_at(pairs, cur_keys, since) if since else None

    by_isin: Dict[str, List[Dict]] = {}
    for r in current:
        by_isin.setdefault(r["isin"], []).append(r)
    issuers = []
    for isin, rows in by_isin.items():
        rows = sorted(rows, key=lambda r: -r["pct"])
        name = rows[0]["issuer"]
        ticker = ticker_of(isin, name)
        series = panel.get(ticker) if ticker else None
        total = round(sum(r["pct"] for r in rows), 4)
        holders = []
        for r in rows:
            hist = pairs.get(_key(r), [r])
            start = first_date_of_episode(hist)
            holders.append({
                "holder": r["holder"], "pct": r["pct"], "position_date": r["position_date"],
                "published": r.get("published"), "first_disclosed": start,
                "days_in_register": (dt.date.fromisoformat(as_of) - dt.date.fromisoformat(start)).days,
                "disclosures": len(hist),
                "price_change_since_first_pct": _r(price_change_since(series, start)),
                "new_in_window": start > d20,
            })
        first = min(h["first_disclosed"] for h in holders)
        past_total = sum(v["pct"] for k, v in past.items() if k[1] == isin)
        since_total = sum(v["pct"] for k, v in past_since.items() if k[1] == isin) if past_since is not None else None
        shares = shares_of(ticker, isin) if ticker else None
        short_shares = total / 100.0 * shares if shares else None
        mvol = median_volume_shares(series)
        dtc = short_shares / mvol if (short_shares and mvol) else None
        crowded = total >= CROWDED_TOTAL_PCT or len(rows) >= CROWDED_HOLDERS
        issuers.append({
            "ticker": ticker, "isin": isin, "issuer": name,
            "total_pct": total, "holders": len(rows),
            "change_20s_pp": round(total - past_total, 4),
            "total_20s_ago_pct": round(past_total, 4), "window_start": d20,
            "change_since_pp": round(total - since_total, 4) if since_total is not None else None,
            "largest_holder": rows[0]["holder"], "largest_pct": rows[0]["pct"],
            "first_disclosed": first,
            "days_in_register": (dt.date.fromisoformat(as_of) - dt.date.fromisoformat(first)).days,
            "price_change_since_first_pct": _r(price_change_since(series, first)),
            "price_change_20s_pct": _r(price_change_since(series, d20)),
            "shares_outstanding": shares,
            "short_shares_est": int(round(short_shares)) if short_shares else None,
            "median_volume_20s_est": int(round(mvol)) if mvol else None,
            "days_to_cover": _r(dtc, 2),
            "high_days_to_cover": bool(dtc is not None and dtc >= HIGH_DTC_DAYS),
            "crowded": crowded,
            "crowded_reasons": ([r for r, ok in (("total_pct>={}".format(CROWDED_TOTAL_PCT), total >= CROWDED_TOTAL_PCT),
                                                  ("holders>={}".format(CROWDED_HOLDERS), len(rows) >= CROWDED_HOLDERS)) if ok]),
            "holder_list": holders,
        })
    issuers.sort(key=lambda x: (-x["total_pct"], x["isin"]))

    events = _events(pairs, cur_keys, d20, as_of, ticker_of, prev_snapshot)
    return {"by_issuer": issuers, "events": events, "window_start": d20,
            "positions": len(current), "issuers": len(issuers),
            "history_rows": len(history)}


def _events(pairs, cur_keys, d20, as_of, ticker_of, prev_snapshot) -> List[Dict]:
    ev = []
    for k, rows in pairs.items():
        last = rows[-1]
        start = first_date_of_episode(rows)
        if k in cur_keys and start > d20:
            ev.append({"type": "new_position", "date": start, "holder": last["holder"], "isin": last["isin"],
                       "ticker": ticker_of(last["isin"], last["issuer"]), "issuer": last["issuer"],
                       "pct": last["pct"], "exact_date": True})
        elif k not in cur_keys and last["position_date"] > d20:
            ev.append({"type": "closed_position", "date": None, "last_disclosed": last["position_date"],
                       "last_pct": last["pct"], "holder": last["holder"], "isin": last["isin"],
                       "ticker": ticker_of(last["isin"], last["issuer"]), "issuer": last["issuer"],
                       "exact_date": False,
                       "note": "fell below 0.5% (or was withdrawn) after the last disclosure; exact date not published"})
    if prev_snapshot:
        prev_keys = {_key(r): r for r in prev_snapshot.get("rows", [])}
        for k, r in prev_keys.items():
            if k not in cur_keys:
                hit = next((e for e in ev if e["type"] == "closed_position" and (e["holder"].upper().rstrip(" .,"), e["isin"]) == k), None)
                window = "between {} and {}".format(prev_snapshot.get("date"), as_of)
                if hit:
                    hit.update({"exact_date": False, "closed_between": [prev_snapshot.get("date"), as_of],
                                "note": "left the register " + window + " (local snapshots)"})
                else:
                    ev.append({"type": "closed_position", "date": None, "last_disclosed": r["position_date"],
                               "last_pct": r["pct"], "holder": r["holder"], "isin": r["isin"],
                               "ticker": ticker_of(r["isin"], r["issuer"]), "issuer": r["issuer"],
                               "exact_date": False, "closed_between": [prev_snapshot.get("date"), as_of],
                               "note": "left the register " + window + " (local snapshots)"})
    ev.sort(key=lambda e: (e.get("date") or e.get("last_disclosed") or ""), reverse=True)
    return ev


def _r(x, d: int = 2):
    return round(x, d) if isinstance(x, (int, float)) else None


def issuer_history(history: List[Dict], current: List[Dict], isin: str, step_days: int = 7) -> List[Dict]:
    """Weekly reconstructed total disclosed % for one issuer (lower bound, see open_at)."""
    pairs = {k: v for k, v in pair_histories(history, current).items() if k[1] == isin}
    if not pairs:
        return []
    cur_keys = {_key(r) for r in current if r["isin"] == isin}
    start = dt.date.fromisoformat(min(r["position_date"] for v in pairs.values() for r in v))
    end = dt.date.fromisoformat(max([r["position_date"] for v in pairs.values() for r in v]))
    end = max(end, dt.date.fromisoformat(max((r["position_date"] for r in current), default=end.isoformat())))
    out, day = [], start
    while day <= end:
        o = open_at(pairs, cur_keys, day.isoformat())
        out.append({"date": day.isoformat(), "total_pct": round(sum(v["pct"] for v in o.values()), 4),
                    "holders": len(o)})
        day += dt.timedelta(days=step_days)
    return out


def match_issuer_name(name: str, tickers: Iterable[str]) -> Optional[str]:
    """KNF issuer names are usually GPW short names (e.g. 'GRUPAAZOTY', 'KETY')."""
    n = re.sub(r"[^A-Z0-9]", "", (name or "").upper())
    if not n:
        return None
    ts = {re.sub(r"[^A-Z0-9]", "", t.upper()): t for t in tickers}
    return ts.get(n)
