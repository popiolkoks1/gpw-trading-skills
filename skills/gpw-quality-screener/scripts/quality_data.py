"""Data layer of gpw-quality-screener (network, local history, xBRL, ESEF).

What the free sources give (checked 2026-11-16, see references/methodology.md):

* gpw.pl card, Notoria tab (``start=showNotoria``): ONE period only - the
  latest. The endpoint ignores every period-like parameter (``okres``,
  ``rok``, ``year``, ``period``, ``type``, ``kw``, ``nr``) and ``sektor``
  (the card passes ``sektor=<name>``, the response is byte-identical), so
  previous periods are not available from gpw.pl.
* The prior comparable period therefore comes from, in order:
  1. the local history store (``gpwlib.fundamentals.save_snapshot`` on every
     run; the same YTD label one year earlier appears after a year of runs);
  2. a user-supplied ESEF annual report (``--esef``), which tags the
     current and the prior financial year;
  3. filings.xbrl.org (FY2020-2023 only, stale - labelled as such).
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from gpwlib import constituents, fundamentals, gpw_archive, http, plcal

import quality_engine as qe

NOTORIA_TTL_S = 7 * 86400      # Notoria changes once a quarter
INDICATORS_TTL_S = 86400       # market value / P/E change every session
ALL_TTL_S = 7 * 86400          # --all: ~400 companies x 2 requests, reuse a week

INDEX_ISINS = {
    "WIG20": constituents.SIZE_INDICES["WIG20"],
    "MWIG40": constituents.SIZE_INDICES["MWIG40"],
    "SWIG80": constituents.SIZE_INDICES["SWIG80"],
    "WIG": fundamentals.WIG_ISIN,
}
DEFAULT_INDICES = ("WIG20", "MWIG40")


class DataError(RuntimeError):
    """Input or data problem reported to the user (exit code 1)."""


# --------------------------------------------------------------------------
# Universe
# --------------------------------------------------------------------------

def normalise_index(name: str) -> str:
    key = (name or "").strip().upper().replace("-", "")
    if key not in INDEX_ISINS:
        raise DataError("Unknown index {!r}; use WIG20, mWIG40, sWIG80 or WIG".format(name))
    return key


def resolve_universe(tickers: Optional[List[str]] = None, indices: Optional[List[str]] = None,
                     all_main: bool = False, cache_dir: Optional[Path] = None,
                     offline: bool = False, isin: Optional[str] = None) -> Tuple[List[Dict], List[Dict]]:
    """(``[{ticker, isin, origin}]``, ``[{ticker, error}]``)."""
    out: List[Dict] = []
    errors: List[Dict] = []
    seen = set()

    def add(t, i, origin):
        t = t.strip().upper()
        if t and t not in seen:
            seen.add(t)
            out.append({"ticker": t, "isin": i, "origin": origin})

    if tickers:
        for t in tickers:
            try:
                res = fundamentals.resolve_isin(t, cache_dir, offline, explicit=isin if len(tickers) == 1 else None)
                add(res["ticker"], res["isin"], "tickers")
            except fundamentals.FundamentalsError as exc:
                errors.append({"ticker": t.strip().upper(), "error": str(exc)})
        return out, errors
    if all_main:
        listing = fundamentals.listed_companies(cache_dir, offline=offline, include_newconnect=False)
        for c in listing.get("companies", []):
            if c.get("market") == "main":
                add(c["ticker"], c["isin"], "all")
        if not out:
            raise DataError("The GPW listing is not available{}".format(" in the cache (offline)" if offline else ""))
        return out, errors
    for idx in indices or DEFAULT_INDICES:
        key = normalise_index(idx)
        try:
            members = constituents.fetch_portfolio(INDEX_ISINS[key], cache_dir, offline)["members"]
        except (gpw_archive.GPWFetchError, http.HTTPFetchError) as exc:
            raise DataError("Index portfolio {} not available: {}".format(key, exc))
        if not members:
            raise DataError("Index portfolio {} is empty{}".format(key, " (offline cache)" if offline else ""))
        for m in members:
            add(m["name"], m["isin"], key)
    return out, errors


def bank_members(cache_dir: Optional[Path] = None, offline: bool = False) -> List[str]:
    isin = constituents.SECTOR_INDICES["WIG-BANKI"][0]
    try:
        return [m["name"] for m in constituents.fetch_portfolio(isin, cache_dir, offline)["members"]]
    except (gpw_archive.GPWFetchError, http.HTTPFetchError):
        return []


# --------------------------------------------------------------------------
# Snapshot (two TTLs: indicators daily, Notoria weekly)
# --------------------------------------------------------------------------

def fetch_snapshot(ticker: str, isin: str, cache_dir: Optional[Path] = None, offline: bool = False,
                   ttl_indicators: float = INDICATORS_TTL_S, ttl_notoria: float = NOTORIA_TTL_S) -> Dict:
    """``fundamentals.snapshot`` with separate cache TTLs (market value moves
    daily, Notoria quarterly; gpwlib 1.2 ``ttl_indicators`` / ``ttl_notoria``).
    The ISIN comes from the resolved universe; a missing Notoria tab is
    flagged with the ``no_notoria`` code."""
    snap = fundamentals.snapshot(ticker, isin=isin, cache_dir=cache_dir, offline=offline,
                                 ttl_indicators=ttl_indicators, ttl_notoria=ttl_notoria)
    snap["isin_source"] = "universe"
    if snap.get("notoria") is None:
        snap["warnings"] = ["no_notoria"] + [w for w in snap.get("warnings") or [] if "No Notoria" not in w]
    return snap


def history_period_as_notoria(label: str, entry: Dict) -> Dict:
    return {"period_label": label, "period": entry.get("period") or fundamentals.parse_period_label(label),
            "consolidated": entry.get("consolidated"), "values": entry.get("values") or {}}


def point_in_time_snapshot(ticker: str, hist: Optional[Dict], as_of: dt.date) -> Optional[Dict]:
    """Snapshot as it was known on ``as_of``, rebuilt from the history store:
    the newest period first retrieved on or before ``as_of`` and the newest
    indicators row dated on or before it. None when the store has neither."""
    if not hist:
        return None
    cutoff = as_of.isoformat()
    periods = [(lab, e) for lab, e in (hist.get("periods") or {}).items()
               if (e.get("first_retrieved_at") or "9999")[:10] <= cutoff]
    periods.sort(key=lambda kv: fundamentals.period_sort_key(*kv))
    inds = [r for r in hist.get("indicators") or [] if (r.get("date") or "9999") <= cutoff]
    if not periods and not inds:
        return None
    noto = None
    if periods:
        lab, e = periods[-1]
        vals = e.get("values") or {}
        # a restatement after as_of must not leak: use the values valid then
        for rev in e.get("revisions") or []:
            if (rev.get("replaced_at") or "")[:10] > cutoff:
                vals = rev.get("previous_values") or vals
                break
        noto = history_period_as_notoria(lab, dict(e, values=vals))
    ind = dict(inds[-1]) if inds else {}
    return {"ticker": ticker, "isin": hist.get("isin"), "isin_source": "history",
            "retrieved_at": (ind.get("retrieved_at") or cutoff), "indicators_retrieved_at": ind.get("retrieved_at"),
            "indicators": ind, "notoria": noto, "sources": [], "warnings": ["point_in_time_from_history"],
            "from_cache": True}


# --------------------------------------------------------------------------
# History helpers
# --------------------------------------------------------------------------

def load_history(ticker: str, store_dir: Optional[Path]) -> Optional[Dict]:
    try:
        return fundamentals.load_history(ticker, store_dir)
    except fundamentals.FundamentalsError:
        return None


def history_statements(hist: Optional[Dict], as_of: Optional[dt.date] = None) -> List[Dict]:
    """Every stored period as a normalised statement (oldest first)."""
    out = []
    for lab, e in sorted(((hist or {}).get("periods") or {}).items(), key=lambda kv: fundamentals.period_sort_key(*kv)):
        if as_of and (e.get("first_retrieved_at") or "9999")[:10] > as_of.isoformat():
            continue
        st = qe.statement_from_notoria(history_period_as_notoria(lab, e), source="history")
        if st:
            st["first_retrieved_at"] = e.get("first_retrieved_at")
            out.append(st)
    return out


def prior_from_history(cur: Optional[Dict], stored: List[Dict]) -> Dict:
    """``{prior, prev_fy, prev_ytd}``: the same YTD window a year earlier,
    the previous full year and (for TTM) that same-window prior."""
    if not cur:
        return {"prior": None, "prev_fy": None}
    prior = next((s for s in stored if qe.same_period(cur, s)), None)
    prev_fy = next((s for s in stored if s.get("months") == 12 and s.get("year") == (cur.get("year") or 0) - 1), None)
    return {"prior": prior, "prev_fy": prev_fy}


def shares_year_ago(hist: Optional[Dict], when: dt.date, window_days: int = 45) -> Optional[float]:
    """Shares issued from the indicators row closest to one year before ``when``."""
    target = when - dt.timedelta(days=365)
    best, best_gap = None, None
    for r in (hist or {}).get("indicators") or []:
        try:
            d = dt.date.fromisoformat(r.get("date"))
        except (TypeError, ValueError):
            continue
        gap = abs((d - target).days)
        if gap <= window_days and r.get("shares_issued") and (best_gap is None or gap < best_gap):
            best, best_gap = float(r["shares_issued"]), gap
    return best


def first_seen(hist: Optional[Dict], label: Optional[str]) -> Optional[str]:
    e = ((hist or {}).get("periods") or {}).get(label or "")
    return (e or {}).get("first_retrieved_at")


# --------------------------------------------------------------------------
# Sessions and prices (offline archive cache only)
# --------------------------------------------------------------------------

def last_final_session(day: dt.date) -> dt.date:
    today = plcal.today_warsaw()
    d = min(day, today)
    for _ in range(15):
        if plcal.is_probable_session(d) and (d < today or plcal.session_is_final(d)):
            return d
        d -= dt.timedelta(days=1)
    return d


def session_known_at(ts_iso: str, final_hour: int = 18) -> Optional[dt.date]:
    """Last GPW session whose close was final at the moment ``ts_iso`` (UTC
    ISO timestamp, e.g. a history ``first_retrieved_at``). Deterministic, so
    every later run maps the same retrieval to the same session - the
    signal date stays stable and gpw-signal-postmortem deduplicates it."""
    try:
        ts = dt.datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    local = ts.astimezone(plcal.WARSAW) if plcal.WARSAW is not None else ts + dt.timedelta(hours=1)
    d = local.date()
    if not (plcal.is_probable_session(d) and local.hour >= final_hour):
        d -= dt.timedelta(days=1)
        while not plcal.is_probable_session(d):
            d -= dt.timedelta(days=1)
    return d


def session_on_or_after(day: dt.date, cap: dt.date) -> dt.date:
    d = day
    for _ in range(10):
        if plcal.is_probable_session(d):
            break
        d += dt.timedelta(days=1)
    return min(d, cap)


def close_on(day: dt.date, ticker: str, cache_dir: Optional[Path] = None) -> Optional[float]:
    """Unadjusted close from the cached archive (never fetches)."""
    try:
        payload = gpw_archive.fetch_session(day, gpw_archive.TYPE_SHARES, cache_dir=cache_dir, offline=True)
    except Exception:  # noqa: BLE001 - a corrupt cache entry must not break the screen
        return None
    for row in (payload or {}).get("rows") or []:
        if (row.get("name") or "").upper() == ticker.upper() and row.get("close"):
            return float(row["close"])
    return None


# --------------------------------------------------------------------------
# ESEF (user file) and filings.xbrl.org
# --------------------------------------------------------------------------

def esef_pair(path: str) -> Dict:
    """``{current, prior, files, path}`` from a user-supplied ESEF zip."""
    p = Path(path).expanduser()
    if not p.exists():
        raise DataError("ESEF file not found: {}".format(p))
    try:
        parsed = fundamentals.parse_esef_zip(p)
    except fundamentals.FundamentalsError as exc:
        raise DataError(str(exc))
    cur, prior = qe.ifrs_fy_pair(parsed["facts"], "esef")
    if cur is None:
        raise DataError("No full-year statement found in the ESEF file {}".format(p))
    return {"current": cur, "prior": prior, "files": parsed["files"], "path": str(p)}


_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4}|\d{8}|\d{4}")
_STOP = {"pl", "en", "esef", "ssf", "sf", "za", "rok", "wg", "skonsolidowane", "sprawozdanie", "finansowe",
         "sa", "gk", "mssf", "grupa", "kapitalowa", "kapitałowa", "spolka", "spółka", "akcyjna", "group", "zip"}


def package_name_key(url: Optional[str]) -> str:
    """``.../GrupaBudimex-2022-12-31-pl.zip`` -> ``budimex``;
    ``cdprojektsa-2022-12-31-pl.zip`` -> ``cdprojekt``;
    ``esef_SSF_GRUPAKRUK-2021-12-31.zip`` -> ``kruk``."""
    if not url:
        return ""
    name = url.rsplit("/", 1)[-1]
    name = re.sub(r"\.(zip|json|xhtml|html)$", "", name, flags=re.I)
    name = _DATE_RE.sub(" ", name)
    tokens = [t for t in re.split(r"[^0-9a-ząćęłńóśźż]+", name.lower()) if t and t not in _STOP and not t.isdigit()]
    key = "".join(tokens)
    return _strip_affixes(key)


def package_name_keys(url: Optional[str]) -> set:
    """Normalised name with and without the "grupa"/"gk" prefix."""
    if not url:
        return set()
    name = url.rsplit("/", 1)[-1]
    name = re.sub(r"\.(zip|json|xhtml|html)$", "", name, flags=re.I)
    name = _DATE_RE.sub(" ", name)
    raw = [t for t in re.split(r"[^0-9a-ząćęłńóśźż]+", name.lower()) if t and not t.isdigit()]
    joined_all = "".join(t for t in raw if t not in (_STOP - {"grupa", "gk"}))
    return {k for k in (package_name_key(url), joined_all, _strip_affixes(joined_all, prefixes=("esef",))) if k}


def _strip_affixes(key: str, prefixes=("grupa", "gk", "esef")) -> str:
    for pre in prefixes:
        if key.startswith(pre) and len(key) > len(pre) + 2:
            key = key[len(pre):]
    if key.endswith("sa") and len(key) > 5:
        key = key[:-2]
    return key


def match_lei(ticker: str, filings: List[Dict], code: Optional[str] = None) -> Optional[str]:
    """Best-effort ticker -> LEI from the package file names of filings.xbrl.org
    (there is no ticker or name field in the filings list). An exact match of
    the normalised file name (:func:`package_name_key`) with the ticker wins
    when it points to one LEI; otherwise None and the user passes ``--lei``."""
    wanted = {re.sub(r"[^a-z0-9]", "", ticker.lower())}
    if code:
        wanted.add(code.lower())
    leis = {f["lei"] for f in filings if f.get("lei") and package_name_keys(f.get("package_url")) & wanted}
    return next(iter(leis)) if len(leis) == 1 else None


def xbrl_pair(ticker: str, lei: Optional[str], cache_dir: Optional[Path] = None, offline: bool = False,
              code: Optional[str] = None) -> Dict:
    """``{current, prior, lei, lei_source, filing}`` from the newest annual
    filing of the company on filings.xbrl.org (FY <= 2023: stale)."""
    try:
        filings = fundamentals.list_pl_filings(cache_dir=cache_dir, offline=offline)
    except (http.HTTPFetchError, ValueError) as exc:
        raise DataError("filings.xbrl.org not available: {}".format(exc))
    src = "explicit"
    if not lei:
        lei, src = match_lei(ticker, filings, code), "package_name_match"
    if not lei:
        return {"current": None, "prior": None, "lei": None, "lei_source": None, "filing": None,
                "note": "lei_not_found"}
    own = [f for f in filings if f.get("lei") == lei and f.get("json_url")]
    own.sort(key=lambda f: f.get("period_end") or "", reverse=True)
    if not own:
        return {"current": None, "prior": None, "lei": lei, "lei_source": src, "filing": None,
                "note": "no_annual_filing"}
    cur = prior = filing = None
    for cand in own[:2]:  # newest first; a Q1 filing (rare) has no 12-month flows -> try the next
        try:
            data = fundamentals.load_xbrl_json(cand["json_url"], cache_dir=cache_dir, offline=offline)
        except (http.HTTPFetchError, fundamentals.FundamentalsError, ValueError) as exc:
            raise DataError("Cannot load xBRL-JSON {}: {}".format(cand["json_url"], exc))
        cur, prior = qe.ifrs_fy_pair(fundamentals.extract_ifrs_facts(data), "xbrl")
        if cur:
            filing = cand
            break
    if not cur:
        return {"current": None, "prior": None, "lei": lei, "lei_source": src, "filing": own[0],
                "note": "no_annual_filing"}
    return {"current": cur, "prior": prior, "lei": lei, "lei_source": src, "filing": filing, "note": None}
