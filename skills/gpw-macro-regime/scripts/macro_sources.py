"""Macro data sources for gpw-macro-regime: parsers (pure) and fetchers (gpwlib.http).

Every endpoint below was fetched and checked on 2026-11-16 (see
references/data_sources.md). Parsers take the raw text and return plain
Python lists so they can be tested with trimmed real fixtures; fetchers only
add caching, chunking and the "which source failed" bookkeeping.

Series convention: a list of ``(key, value)`` tuples sorted by key, where the
key is a ``datetime.date`` (daily data) or a ``"YYYY-MM"`` string (monthly).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from gpwlib import http, plcal

# --------------------------------------------------------------------------
# Source registry: URL, frequency and how old the newest observation may be
# before we call that source stale. The lag limits follow each publisher's
# normal release rhythm (references/data_sources.md, "Publication lags").
# --------------------------------------------------------------------------

NBP_RATES_ARCHIVE_URL = "https://static.nbp.pl/dane/stopy/stopy_procentowe_archiwum.xml"
NBP_RATES_CURRENT_URL = "https://static.nbp.pl/dane/stopy/stopy_procentowe.xml"
NBP_FX_URL = "https://api.nbp.pl/api/exchangerates/rates/a/{code}/{start}/{end}/"
ECB_PL10Y_URL = "https://data-api.ecb.europa.eu/service/data/IRS/M.PL.L.L40.CI.0000.PLN.N.Z"
BUBA_BUND10Y_URL = ("https://api.statistiken.bundesbank.de/rest/data/BBSIS/"
                    "D.I.ZAR.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A")
EUROSTAT_HICP_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr"
BONDSPOT_FIXING_URL = "https://www.bondspot.pl/fixing_obligacji"

SOURCES = {
    # max_lag_days = calendar days between the newest observation and the
    # report date after which the source is flagged stale.
    "nbp_rates": {"name": "NBP - stopy procentowe (archiwum + bieżące)",
                  "url": NBP_RATES_CURRENT_URL, "frequency": "event",
                  # MPC meets ~11 times a year; a copy older than ~5 weeks may
                  # miss one decision. "as_of" = when the file was retrieved.
                  "max_lag_days": 35},
    "nbp_fx": {"name": "NBP API - kursy średnie, tabela A",
               "url": "https://api.nbp.pl/api/exchangerates/rates/a/{eur,usd,chf}/", "frequency": "daily",
               # daily fixing on Polish business days; 5 days covers a long
               # weekend or a holiday bridge.
               "max_lag_days": 5},
    "bundesbank": {"name": "Deutsche Bundesbank - rentowność Bund 10Y (dzienna)",
                   "url": BUBA_BUND10Y_URL, "frequency": "daily", "max_lag_days": 5},
    "bondspot": {"name": "BondSpot - fixing obligacji skarbowych (dzienny)",
                 "url": BONDSPOT_FIXING_URL, "frequency": "daily", "max_lag_days": 5},
    "ecb_pl10y": {"name": "ECB - PL 10Y long-term interest rate (convergence), miesięczna",
                  "url": ECB_PL10Y_URL, "frequency": "monthly",
                  # month M is published in the first weeks of M+1; measured
                  # from the 1st of month M: ~30 days + up to ~45 days of lag.
                  "max_lag_days": 75},
    "eurostat_hicp": {"name": "Eurostat - HICP Polska (prc_hicp_minr), miesięczna",
                      "url": EUROSTAT_HICP_URL, "frequency": "monthly",
                      # Eurostat releases month M around the middle of M+1
                      # (sometimes later), measured from the 1st of month M.
                      "max_lag_days": 80},
    "gpw_indices": {"name": "GPW - archiwum notowań (indeksy)",
                    "url": "https://www.gpw.pl/archiwum-notowan", "frequency": "daily", "max_lag_days": 5},
}

# NBP API: "a single inquiry cannot cover a period longer than 93 days"
# (https://api.nbp.pl/en.html). We stay at or under that limit.
NBP_MAX_DAYS = 93

# Cache lifetimes (seconds). Past data is immutable; the newest part changes.
TTL_EVENT = 6 * 3600          # NBP rate files: MPC decisions are announced, not intraday
TTL_DAILY = 3 * 3600          # current FX chunk, Bundesbank, today's fixing
TTL_MONTHLY = 24 * 3600       # ECB, Eurostat
IMMUTABLE_AFTER_DAYS = 7      # a chunk that ended a week ago will not change


class SourceError(RuntimeError):
    """A macro source could not be fetched or parsed."""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def pl_float(text: Optional[str]) -> Optional[float]:
    """'3,75' / '3.75' / '-' / '.' -> float or None."""
    if text is None:
        return None
    s = str(text).strip().replace("\xa0", "").replace(" ", "")
    if s in ("", "-", ".", "—", "NaN", "nan"):
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _date(text: str) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(text.strip()[:10])
    except (ValueError, AttributeError):
        return None


def month_key(d: dt.date) -> str:
    return "{:04d}-{:02d}".format(d.year, d.month)


def month_start(key: str) -> dt.date:
    y, m = key.split("-")
    return dt.date(int(y), int(m), 1)


# --------------------------------------------------------------------------
# NBP reference rate (XML)
# --------------------------------------------------------------------------

def parse_nbp_rates_archive(text: str, rate_id: str = "ref") -> List[Tuple[dt.date, float]]:
    """Archive XML -> [(valid_from, rate)] for ``rate_id`` (default: reference rate).

    The archive lists every decision that changed any NBP rate, so the
    reference rate can repeat; consecutive equal values are collapsed.
    """
    root = ET.fromstring(text.lstrip("﻿").encode("utf-8"))
    out: List[Tuple[dt.date, float]] = []
    for block in root.iter("pozycje"):
        d = _date(block.get("obowiazuje_od", ""))
        if d is None:
            continue
        for pos in block.iter("pozycja"):
            if pos.get("id") == rate_id:
                val = pl_float(pos.get("oprocentowanie"))
                if val is not None:
                    out.append((d, val))
    return dedupe_rate_changes(out)


def parse_nbp_rates_current(text: str, rate_id: str = "ref") -> Optional[Dict]:
    """Current XML -> {"rate", "valid_from", "published", "trend"} or None."""
    root = ET.fromstring(text.lstrip("﻿").encode("utf-8"))
    for pos in root.iter("pozycja"):
        if pos.get("id") == rate_id:
            val = pl_float(pos.get("oprocentowanie"))
            d = _date(pos.get("obowiazuje_od", ""))
            if val is None or d is None:
                return None
            return {"rate": val, "valid_from": d, "published": _date(root.get("data_publikacji", "")),
                    "trend": (pos.get("trend") or "").strip() or None}
    return None


def dedupe_rate_changes(points: Sequence[Tuple[dt.date, float]]) -> List[Tuple[dt.date, float]]:
    out: List[Tuple[dt.date, float]] = []
    for d, v in sorted(points):
        if out and out[-1][0] == d:
            out[-1] = (d, v)
        elif not out or abs(out[-1][1] - v) > 1e-9:
            out.append((d, v))
    return out


def merge_rate_history(archive: Sequence[Tuple[dt.date, float]], current: Optional[Dict]) -> List[Tuple[dt.date, float]]:
    pts = list(archive)
    if current:
        pts.append((current["valid_from"], current["rate"]))
    return dedupe_rate_changes(pts)


# --------------------------------------------------------------------------
# NBP FX (JSON, chunked)
# --------------------------------------------------------------------------

def date_chunks(start: dt.date, end: dt.date, max_days: int = NBP_MAX_DAYS) -> List[Tuple[dt.date, dt.date]]:
    """Split [start, end] (inclusive) into windows of at most ``max_days`` days."""
    if end < start:
        return []
    if max_days < 1:
        raise ValueError("max_days must be >= 1")
    out = []
    cur = start
    step = dt.timedelta(days=max_days - 1)
    while cur <= end:
        stop = min(end, cur + step)
        out.append((cur, stop))
        cur = stop + dt.timedelta(days=1)
    return out


def quarter_chunks(start: dt.date, end: dt.date) -> List[Tuple[dt.date, dt.date]]:
    """Split [start, end] at calendar-quarter boundaries.

    Quarters last 90-92 days, so every window respects the NBP 93-day limit,
    and the windows (hence the cache keys) are the same whatever the start
    date or ``--as-of`` of a run - an offline re-run finds them all.
    """
    out = []
    cur = start
    while cur <= end:
        q_end_month = ((cur.month - 1) // 3) * 3 + 3
        nxt = dt.date(cur.year + (1 if q_end_month == 12 else 0), 1 if q_end_month == 12 else q_end_month + 1, 1)
        stop = min(end, nxt - dt.timedelta(days=1))
        out.append((cur, stop))
        cur = stop + dt.timedelta(days=1)
    return out


def parse_nbp_fx(text: str) -> List[Tuple[dt.date, float]]:
    data = json.loads(text)
    out = []
    for r in data.get("rates", []):
        d, v = _date(r.get("effectiveDate", "")), r.get("mid")
        if d is not None and isinstance(v, (int, float)):
            out.append((d, float(v)))
    return sorted(out)


# --------------------------------------------------------------------------
# ECB (SDMX csvdata), Bundesbank (csv), Eurostat (JSON-stat)
# --------------------------------------------------------------------------

def parse_ecb_csv(text: str) -> List[Tuple[str, float]]:
    """ECB csvdata -> [("YYYY-MM", value)]."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames or "TIME_PERIOD" not in reader.fieldnames or "OBS_VALUE" not in reader.fieldnames:
        raise SourceError("ECB response is not SDMX csvdata (missing TIME_PERIOD/OBS_VALUE)")
    out = []
    for row in reader:
        key, val = (row.get("TIME_PERIOD") or "").strip(), pl_float(row.get("OBS_VALUE"))
        if re.match(r"^\d{4}-\d{2}$", key) and val is not None:
            out.append((key, val))
    return sorted(out)


def parse_bundesbank_csv(text: str) -> List[Tuple[dt.date, float]]:
    """Bundesbank time-series CSV (';' separated, German decimals, '.' = no value)."""
    out = []
    for line in text.lstrip("﻿").splitlines():
        parts = line.split(";") if ";" in line else line.split(",")  # German or English variant (Accept-Language)
        if len(parts) < 2:
            continue
        d = _date(parts[0]) if re.match(r"^\d{4}-\d{2}-\d{2}$", parts[0].strip()) else None
        if d is None:
            continue
        v = pl_float(parts[1])
        if v is not None:
            out.append((d, v))
    if not out and "Dezimalstellen" not in text and "BBSIS" not in text:
        raise SourceError("Bundesbank response is not the expected CSV")
    return sorted(out)


def parse_eurostat_hicp(text: str, geo: str = "PL") -> Dict[str, List[Tuple[str, float]]]:
    """JSON-stat 2.0 from prc_hicp_minr -> {"yoy": [...], "index": [...]}.

    Works whatever the order of dimensions; the all-items category is
    ``TOTAL`` (ECOICOP ver. 2, since 2026) or ``CP00`` (older releases).
    ``index`` uses the 2015=100 base (I15) when present, else 2025=100 (I25).
    """
    data = json.loads(text)
    if "id" not in data or "value" not in data:
        err = data.get("error")
        raise SourceError("Eurostat error: {}".format(err[0].get("label") if isinstance(err, list) and err else err))
    ids, sizes, dims = data["id"], data["size"], data["dimension"]
    strides = [1] * len(ids)
    for i in range(len(ids) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]

    def cat(dim: str) -> Dict[str, int]:
        return dims[dim]["category"]["index"]

    fixed = {}
    for dim in ids:
        if dim in ("time", "unit"):
            continue
        idx = cat(dim)
        if dim == "geo":
            if geo not in idx:
                raise SourceError("Eurostat response has no geo={}".format(geo))
            fixed[dim] = idx[geo]
        elif dim.startswith("coicop"):
            key = "TOTAL" if "TOTAL" in idx else ("CP00" if "CP00" in idx else None)
            if key is None:
                raise SourceError("Eurostat response has no all-items HICP category")
            fixed[dim] = idx[key]
        else:
            fixed[dim] = 0  # single-category dimension (freq=M)
    times = cat("time")
    units = cat("unit")
    values = data["value"]

    def series(unit: str) -> List[Tuple[str, float]]:
        if unit not in units:
            return []
        out = []
        for period, t_i in times.items():
            pos = 0
            for i, dim in enumerate(ids):
                k = t_i if dim == "time" else (units[unit] if dim == "unit" else fixed[dim])
                pos += k * strides[i]
            v = values.get(str(pos)) if isinstance(values, dict) else (values[pos] if pos < len(values) else None)
            if isinstance(v, (int, float)):
                out.append((period, float(v)))
        return sorted(out)

    index = series("I15") or series("I25")
    return {"yoy": series("RCH_A"), "index": index}


def yoy_from_index(index: Sequence[Tuple[str, float]]) -> List[Tuple[str, float]]:
    """Annual rate of change (%) from a monthly index, rounded like Eurostat (0.1)."""
    by = dict(index)
    out = []
    for key, v in index:
        y, m = key.split("-")
        prev = by.get("{:04d}-{}".format(int(y) - 1, m))
        if prev:
            out.append((key, round((v / prev - 1) * 100, 1)))
    return out


def combine_hicp(parsed: Dict[str, List[Tuple[str, float]]]) -> List[Tuple[str, float]]:
    """Published y/y where available, filled from the index otherwise."""
    merged = dict(yoy_from_index(parsed.get("index", [])))
    merged.update(dict(parsed.get("yoy", [])))
    return sorted(merged.items())


# --------------------------------------------------------------------------
# BondSpot fixing (HTML)
# --------------------------------------------------------------------------

_BS_DATE = re.compile(r"FIXING[^<]*Data:\s*(\d{4}-\d{2}-\d{2})")
_BS_ROW = re.compile(r"<tr>\s*<td>\s*\d+\s*</td>\s*<td>\s*([A-Z]{2}\d{4})\s*</td>\s*<td>\s*(PL[0-9A-Z]{10})\s*</td>(.*?)</tr>",
                     re.S)
_BS_CELL = re.compile(r"<td[^>]*>\s*(.*?)\s*</td>", re.S)
# Fixed-coupon / zero-coupon Polish Treasury bonds. WZ (WIBOR floaters),
# NZ (POLSTR floaters) and IZ (CPI-linked) have no comparable nominal yield.
FIXED_PREFIXES = ("OK", "PS", "DS", "WS")


def bond_maturity(name: str) -> Optional[dt.date]:
    """'PS0729' -> 2029-07-25. Polish Treasury bonds mature on the 25th of the month."""
    m = re.match(r"^[A-Z]{2}(\d{2})(\d{2})$", name or "")
    if not m:
        return None
    month, year = int(m.group(1)), 2000 + int(m.group(2))
    if not 1 <= month <= 12:
        return None
    return dt.date(year, month, 25)


def parse_bondspot_fixing(html: str) -> Dict:
    """Fixing page -> {"date": date|None, "bonds": [...]}; no session -> bonds == []."""
    m = _BS_DATE.search(html)
    bonds = []
    for name, isin, rest in _BS_ROW.findall(html):
        cells = [unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in _BS_CELL.findall(rest)]
        # columns: bid price, ask price, bid yield, ask yield, fixing price, fixing yield
        if len(cells) < 6:
            continue
        bonds.append({"name": name, "isin": isin, "price": pl_float(cells[4]), "yield": pl_float(cells[5]),
                      "maturity": bond_maturity(name), "fixed": name[:2] in FIXED_PREFIXES})
    return {"date": _date(m.group(1)) if m else None, "bonds": bonds}


def pick_benchmark(bonds: Sequence[Dict], on: dt.date, target_years: float, max_gap_years: float) -> Optional[Dict]:
    """Fixed-rate bond whose remaining maturity is nearest ``target_years``.

    ``max_gap_years`` rejects a bond too far from the target (e.g. no 10-year
    bond in the table); ties go to the more liquid, longer-established series
    (the one listed first is not guaranteed, so we break ties by ISIN).
    """
    best = None
    for b in bonds:
        if not b.get("fixed") or b.get("yield") is None or b.get("maturity") is None:
            continue
        tenor = (b["maturity"] - on).days / 365.25
        gap = abs(tenor - target_years)
        if gap > max_gap_years:
            continue
        key = (gap, b["isin"])
        if best is None or key < best[0]:
            best = (key, dict(b, tenor_years=round(tenor, 2)))
    return best[1] if best else None


# --------------------------------------------------------------------------
# Fetchers
# --------------------------------------------------------------------------

class Fetcher:
    """Fetches every macro source politely through ``gpwlib.http``.

    ``getter`` is injectable for tests (same signature as ``http.get``).
    """

    def __init__(self, cache_dir: Optional[Path] = None, offline: bool = False,
                 getter: Callable = None, today: Optional[dt.date] = None):
        self.cache_dir = cache_dir
        self.offline = offline
        self.get = getter or http.get
        self.today = today or plcal.today_warsaw()
        self.calls: List[str] = []

    def _get(self, url: str, params=None, ttl: Optional[float] = TTL_DAILY):
        self.calls.append(http.build_url(url, params))
        return self.get(url, params, cache_dir=self.cache_dir, ttl_seconds=ttl, offline=self.offline)

    def _ttl_for_end(self, end: dt.date, live_ttl: float) -> Optional[float]:
        return None if end <= self.today - dt.timedelta(days=IMMUTABLE_AFTER_DAYS) else live_ttl

    # -- NBP rates --------------------------------------------------------
    def nbp_rates(self) -> Dict:
        arch = self._get(NBP_RATES_ARCHIVE_URL, ttl=TTL_EVENT)
        cur = self._get(NBP_RATES_CURRENT_URL, ttl=TTL_EVENT)
        try:
            history = merge_rate_history(parse_nbp_rates_archive(arch.text), parse_nbp_rates_current(cur.text))
        except ET.ParseError as exc:
            raise SourceError("NBP rate XML could not be parsed: {}".format(exc))
        if not history:
            raise SourceError("NBP rate XML contained no reference-rate history")
        return {"series": history, "retrieved_at": cur.retrieved_at, "from_cache": cur.from_cache,
                "urls": [NBP_RATES_ARCHIVE_URL, NBP_RATES_CURRENT_URL]}

    # -- NBP FX -----------------------------------------------------------
    def nbp_fx(self, code: str, start: dt.date, end: dt.date) -> Dict:
        series: List[Tuple[dt.date, float]] = []
        retrieved, cached = None, True
        q0 = dt.date(start.year, ((start.month - 1) // 3) * 3 + 1, 1)  # stable cache keys
        for a, b in quarter_chunks(q0, min(end, self.today)):
            url = NBP_FX_URL.format(code=code.lower(), start=a.isoformat(), end=b.isoformat())
            try:
                resp = self._get(url, {"format": "json"}, ttl=self._ttl_for_end(b, TTL_DAILY))
            except http.OfflineMiss:
                resp = self._offline_open_chunk(code, a, b)
                if resp is None:
                    raise
            except http.HTTPFetchError as exc:
                if exc.status == 404:  # "Brak danych": no fixing in that window
                    continue
                raise
            series.extend(parse_nbp_fx(resp.text))
            retrieved = max(retrieved or resp.retrieved_at, resp.retrieved_at)
            cached = cached and resp.from_cache
        if not series:
            raise SourceError("NBP API returned no {} fixings between {} and {}".format(code.upper(), start, end))
        return {"series": sorted(set(series)), "retrieved_at": retrieved, "from_cache": cached,
                "urls": [NBP_FX_URL.format(code=code.lower(), start="…", end="…")]}

    def _offline_open_chunk(self, code: str, a: dt.date, b: dt.date):
        """Offline: the open (current-quarter) window was cached by an earlier
        run with an earlier end date - look for it day by day (cache only)."""
        if not self.offline:
            return None
        d = b - dt.timedelta(days=1)
        while d >= a:
            url = NBP_FX_URL.format(code=code.lower(), start=a.isoformat(), end=d.isoformat())
            try:
                return self.get(url, {"format": "json"}, cache_dir=self.cache_dir, offline=True)
            except http.HTTPFetchError:
                d -= dt.timedelta(days=1)
        return None

    # -- ECB / Bundesbank / Eurostat -------------------------------------
    # Start periods are floored to 1 January so the cache key does not change
    # with every --as-of (an offline re-run must find the same entry).
    def ecb_pl10y(self, start: dt.date) -> Dict:
        resp = self._get(ECB_PL10Y_URL, {"format": "csvdata", "startPeriod": "{}-01".format(start.year)}, ttl=TTL_MONTHLY)
        series = parse_ecb_csv(resp.text)
        if not series:
            raise SourceError("ECB returned no PL 10Y observations")
        return {"series": series, "retrieved_at": resp.retrieved_at, "from_cache": resp.from_cache,
                "urls": [ECB_PL10Y_URL + "?format=csvdata"]}

    def bund10y(self, start: dt.date) -> Dict:
        resp = self._get(BUBA_BUND10Y_URL, {"format": "csv", "startPeriod": "{}-01-01".format(start.year)}, ttl=TTL_DAILY)
        series = parse_bundesbank_csv(resp.text)
        if not series:
            raise SourceError("Bundesbank returned no Bund 10Y observations")
        return {"series": series, "retrieved_at": resp.retrieved_at, "from_cache": resp.from_cache,
                "urls": [BUBA_BUND10Y_URL + "?format=csv"]}

    def hicp(self, start: dt.date) -> Dict:
        base = [("geo", "PL"), ("unit", "RCH_A"), ("unit", "I15"), ("sinceTimePeriod", "{}-01".format(start.year))]
        try:
            resp = self._get(EUROSTAT_HICP_URL, base + [("coicop18", "TOTAL")], ttl=TTL_MONTHLY)
        except http.HTTPFetchError as exc:
            if exc.status != 400 or isinstance(exc, http.OfflineMiss):
                raise
            # older dataset structure used "coicop=CP00"
            resp = self._get(EUROSTAT_HICP_URL, base + [("coicop", "CP00")], ttl=TTL_MONTHLY)
        series = combine_hicp(parse_eurostat_hicp(resp.text))
        if not series:
            raise SourceError("Eurostat returned no HICP observations for Poland")
        return {"series": series, "retrieved_at": resp.retrieved_at, "from_cache": resp.from_cache,
                "urls": [EUROSTAT_HICP_URL + "?geo=PL&coicop18=TOTAL&unit=RCH_A&unit=I15"]}

    # -- BondSpot ---------------------------------------------------------
    def bondspot(self, day: dt.date, max_back: int = 7) -> Dict:
        """Newest fixing on or before ``day`` (walks back over weekends/holidays)."""
        for k in range(max_back + 1):
            d = day - dt.timedelta(days=k)
            if d.weekday() >= 5 or d > self.today:
                continue
            params = {"date": d.strftime("%Y%m%d"), "month": d.month, "year": d.year}
            ttl = None if d < self.today else TTL_DAILY
            try:
                resp = self._get(BONDSPOT_FIXING_URL, params, ttl=ttl)
            except http.OfflineMiss:
                continue  # offline: try the previous day that may be cached
            parsed = parse_bondspot_fixing(resp.text)
            if parsed["bonds"]:
                return {"date": parsed["date"] or d, "bonds": parsed["bonds"], "retrieved_at": resp.retrieved_at,
                        "from_cache": resp.from_cache, "urls": [http.build_url(BONDSPOT_FIXING_URL, params)]}
        raise SourceError("No BondSpot fixing found in the {} days up to {}".format(max_back + 1, day))
