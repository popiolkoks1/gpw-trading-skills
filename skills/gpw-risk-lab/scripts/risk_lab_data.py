"""gpw-risk-lab - data loading (GPW archive, NBP, ECB) through gpwlib only.

* GPW archive (``gpwlib.gpw_archive``): shares, indices, ETFs, futures (type 35).
  Index history back to 2007 is read from the local cache; it is downloaded
  only with ``allow_history_fetch`` (``--fetch-history``), because the first
  backfill is ~3 000 requests (~1 hour at the archive's polite rate).
  Shares before 2019 are never requested (the skill uses index proxies there).
* NBP table A EUR mid rates (``api.nbp.pl``), 93-day limit per query, so the
  history is fetched in calendar-quarter chunks (the same URLs as
  gpw-macro-regime, so the HTTP cache is shared).
* ECB long-term interest rate for convergence purposes, Poland (monthly
  average 10-year government bond yield), one CSV request.

Everything goes through ``gpwlib.http`` (rate limit, identifying User-Agent, cache).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from gpwlib import gpw_archive, http, plcal

HISTORY_START = dt.date(2007, 1, 2)
SHARES_MIN_DATE = dt.date(2019, 1, 1)   # policy: no share backfill before 2019
NBP_URL = "https://api.nbp.pl/api/exchangerates/rates/a/eur/{start}/{end}/?format=json"
ECB_PL10Y_URL = ("https://data-api.ecb.europa.eu/service/data/IRS/M.PL.L.L40.CI.0000.PLN.N.Z"
                 "?format=csvdata&startPeriod=2007-01")
SOURCES = {
    "archive": "GPW — Archiwum notowań (akcje, indeksy, ETF, kontrakty terminowe type=35): https://www.gpw.pl/archiwum-notowan",
    "benchmark": "GPW Benchmark — portfele indeksów (sektory, WIG20/mWIG40/sWIG80): https://gpwbenchmark.pl",
    "nbp": "NBP — kursy średnie EUR (tabela A): https://api.nbp.pl",
    "ecb": "ECB Data Portal — IRS M.PL.L.L40.CI.0000.PLN.N.Z (rentowność 10-letnich obligacji PL, średnia miesięczna): https://data.ecb.europa.eu",
    "fw20_spec": "GPW — Standard kontraktów terminowych na WIG20 (06.2026): https://www.gpw.pl/pub/GPW/files/PDF/standardy_pl/WATS/Standard_kontrakty_WIG20_WATS_06.2026.pdf",
}


def probable_sessions(start: dt.date, end: dt.date) -> List[dt.date]:
    out, d = [], start
    while d <= end:
        if plcal.is_probable_session(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def load_range(start: dt.date, end: dt.date, type_id: int, cache_dir: Optional[Path], offline: bool,
               fetch_before: Optional[dt.date] = None, progress: bool = True) -> Tuple[List[Dict], int]:
    """Sessions in [start, end], oldest first. Days before ``fetch_before`` are
    read from the cache only. Returns (sessions, number of probable days missing)."""
    out, missing, fetched = [], 0, 0
    cdir = cache_dir or gpw_archive.default_cache_dir()
    for day in probable_sessions(start, end):
        cache_only = offline or (fetch_before is not None and day < fetch_before)
        if type_id == gpw_archive.TYPE_SHARES and day < SHARES_MIN_DATE:
            cache_only = True
        hit = gpw_archive._cache_path(cdir, day, type_id).exists()
        if not hit and cache_only:
            missing += 1
            continue
        payload = gpw_archive.fetch_session(day, type_id, cache_dir=cache_dir, offline=cache_only)
        if not hit:
            fetched += 1
            if progress and fetched % 25 == 0:
                print("  ... downloaded {} {} sessions (now at {})".format(
                    fetched, gpw_archive.TYPE_NAMES.get(type_id), day), file=sys.stderr)
        if payload:
            out.append(payload)
    return out, missing


def index_maps(sessions: List[Dict]) -> Dict[str, Dict[str, float]]:
    """{INDEX NAME (upper case): {date: close}} - the archive changed the case of
    some names over the years ('WIG-gry' -> 'WIG-GRY'), so names are upper-cased."""
    out: Dict[str, Dict[str, float]] = {}
    for s in sessions:
        for r in s["rows"]:
            c = r.get("close")
            if c and c > 0:
                out.setdefault(r["name"].upper(), {})[s["date"]] = c
    return out


def futures_sessions(sessions: List[Dict], underlying: str = "WIG20") -> List[Tuple[str, List[Dict]]]:
    return [(s["date"], gpw_archive.futures_rows(s["rows"], underlying)) for s in sessions]


# --------------------------------------------------------------------------
# NBP / ECB
# --------------------------------------------------------------------------

def _quarters(start: dt.date, end: dt.date):
    q = dt.date(start.year, 3 * ((start.month - 1) // 3) + 1, 1)
    while q <= end:
        nm = q.month + 3
        nxt = dt.date(q.year + (nm > 12), (nm - 1) % 12 + 1, 1)
        yield q, min(nxt - dt.timedelta(days=1), end)
        q = nxt


def nbp_eurpln(start: dt.date, end: dt.date, cache_dir: Optional[Path], offline: bool) -> Tuple[Dict[str, float], List[str]]:
    """{date: EURPLN mid} and a list of problems (missing chunks)."""
    out: Dict[str, float] = {}
    problems: List[str] = []
    today = plcal.today_warsaw()
    for a, b in _quarters(start, min(end, today)):
        url = NBP_URL.format(start=a.isoformat(), end=b.isoformat())
        past = b < today - dt.timedelta(days=3)
        try:
            resp = http.get(url, cache_dir=cache_dir, ttl_seconds=None if past else 12 * 3600, offline=offline)
            for r in resp.json().get("rates", []):
                out[r["effectiveDate"]] = float(r["mid"])
        except http.OfflineMiss:
            problems.append("{}..{} not cached".format(a, b))
        except (http.HTTPFetchError, ValueError, KeyError) as exc:
            problems.append("{}..{}: {}".format(a, b, exc))
    return out, problems


def parse_ecb_csv(text: str) -> Dict[str, float]:
    """ECB SDMX csvdata -> {'YYYY-MM-01': yield in %}."""
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        tp, v = row.get("TIME_PERIOD"), row.get("OBS_VALUE")
        if tp and v not in (None, ""):
            try:
                out[tp[:7] + "-01"] = float(v)
            except ValueError:
                continue
    return out


def ecb_pl10y(cache_dir: Optional[Path], offline: bool) -> Tuple[Dict[str, float], List[str]]:
    try:
        resp = http.get(ECB_PL10Y_URL, cache_dir=cache_dir, ttl_seconds=7 * 86400, offline=offline)
        data = parse_ecb_csv(resp.text)
        return data, ([] if data else ["ECB CSV had no observations"])
    except http.OfflineMiss:
        return {}, ["ECB 10Y yield not cached"]
    except http.HTTPFetchError as exc:
        return {}, ["ECB 10Y yield: {}".format(exc)]


def load_scenarios(path: Optional[str]) -> Dict:
    p = Path(path).expanduser() if path else Path(__file__).resolve().parent / "risk_lab_scenarios.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("cannot read scenarios file {}: {}".format(p, exc))
    except ValueError as exc:
        raise ValueError("scenarios file {} is not valid JSON: {}".format(p, exc))
    validate_scenarios(data)
    data["_path"] = str(p)
    return data


def validate_scenarios(data: Dict) -> None:
    if not isinstance(data, dict):
        raise ValueError("scenarios file must be a JSON object with 'historical' and/or 'factor' lists")
    seen = set()
    for group in ("historical", "factor"):
        for s in data.get(group, []) or []:
            sid = s.get("id")
            if not sid:
                raise ValueError("every scenario needs an 'id'")
            if sid in seen:
                raise ValueError("duplicate scenario id {!r}".format(sid))
            seen.add(sid)
            k = s.get("kind")
            if group == "historical":
                need = {"fixed": ("start", "end"), "peak_to_trough": ("from", "to"), "worst_days": ("days",)}
            else:
                need = {"market": ("shock_pct",), "sector": ("index", "shock_pct"), "fx": ("shock_pct",),
                        "rates": ("shock_bp",)}
            if k not in need:
                raise ValueError("scenario {!r}: kind must be one of {}".format(sid, ", ".join(need)))
            for f in need[k]:
                if f not in s:
                    raise ValueError("scenario {!r}: missing field {!r}".format(sid, f))
            for f in ("start", "end", "from", "to"):
                if f in s:
                    try:
                        dt.date.fromisoformat(s[f])
                    except (TypeError, ValueError):
                        raise ValueError("scenario {!r}: {} must be YYYY-MM-DD".format(sid, f))
    if not seen:
        raise ValueError("scenarios file defines no scenarios")
