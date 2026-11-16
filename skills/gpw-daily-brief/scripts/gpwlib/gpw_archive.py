"""Fetch, parse and cache daily end-of-day quotes from the official GPW archive.

Source: https://www.gpw.pl/archiwum-notowan (Warsaw Stock Exchange, "Archiwum
notowań"). One HTTP request returns every instrument of a given type for one
session. No API key is needed.

    https://www.gpw.pl/archiwum-notowan?fetch=0&type=10&instrument=&date=DD-MM-YYYY

``type=10`` - shares (Główny Rynek), ``type=1`` - indices, ``type=241`` - ETFs,
``type=35`` - futures (the instrument-type ids come from the archive page's
own drop-down; the full list is ``TYPE_LABELS_PL``).

Futures rows carry an extra ``settlement`` field (``Kurs rozliczeniowy``, the
daily settlement price used for variation margin). Contracts without trades
that day show open/high/low as 0 and close = settlement. See
:func:`parse_futures_symbol` for the symbol grammar (``FW20Z2620`` = WIG20,
December 2026, multiplier 20 PLN per index point).

Design rules
------------
* stdlib only (urllib + html.parser) so the skills run anywhere Python 3.9+ runs;
* polite: one request at a time, minimum interval between requests, retries
  with back-off, an identifying User-Agent;
* cached per session on disk; a day without a session is cached as such;
  today's data is cached only after it is final (18:00 Warsaw time);
* every value keeps its provenance (source URL, retrieval time).

Data licence: GPW market data is published for information purposes. The
skills download it on the user's own machine for personal analysis; do not
redistribute the cache. See docs/DATA_SOURCES.md.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional

from . import __version__, plcal

BASE_URL = "https://www.gpw.pl/archiwum-notowan"
TYPE_SHARES = 10
TYPE_INDICES = 1
TYPE_ETF = 241
TYPE_FUTURES = 35
# Instrument types of the archive drop-down (verified 2026-11-16). The English
# slug is also the cache sub-directory name, so existing slugs must not change.
TYPE_NAMES = {
    1: "indices",
    10: "shares",
    13: "treasury_bonds",
    17: "subscription_rights",
    35: "futures",
    37: "allotment_certificates",
    48: "investment_certificates",
    53: "warrants",
    54: "index_participation_units",
    66: "options",
    161: "structured_products",
    241: "etf",
    301: "ipo_allotment",
    537: "bank_securities",
    560: "etc",
    561: "etn",
}
TYPE_LABELS_PL = {
    1: "indeksy",
    10: "akcje",
    13: "obligacje skarbowe",
    17: "prawa poboru",
    35: "kontrakty terminowe",
    37: "prawa do akcji (PDA)",
    48: "certyfikaty inwestycyjne",
    53: "warranty",
    54: "jednostka indeksowa",
    66: "opcje",
    161: "produkty strukturyzowane",
    241: "ETF",
    301: "IPO / przydział akcji",
    537: "Bankowe Papiery Wartościowe",
    560: "ETC",
    561: "ETN",
}
USER_AGENT = "gpw-trading-skills/{} (educational market-analysis skills)".format(__version__)
MIN_INTERVAL_S = 0.6
CACHE_SCHEMA = 1

_last_request_at = 0.0


class GPWFetchError(RuntimeError):
    """Raised when the archive cannot be reached or parsed."""


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def parse_pl_number(text: str) -> Optional[float]:
    """Parse Polish number formatting: '1&nbsp;234,56' / '1 234,56' / '-0,26'."""
    if text is None:
        return None
    cleaned = (
        text.replace("\xa0", "")
        .replace("&nbsp;", "")
        .replace(" ", "")
        .replace(" ", "")
        .strip()
    )
    if cleaned in ("", "-", "--", "x"):
        return None
    cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


class _QuotesTableParser(HTMLParser):
    """Collects header and rows of the ``table footable`` quotes table."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.table_depth = 0
        self.headers: List[str] = []
        self.rows: List[List[str]] = []
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None
        self._cell_is_header = False
        self.found = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            if self.in_table:
                self.table_depth += 1
            elif "footable" in (attrs.get("class") or ""):
                self.in_table = True
                self.found = True
                self.table_depth = 1
            return
        if not self.in_table:
            return
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._cell_is_header = tag == "th"

    def handle_endtag(self, tag):
        if not self.in_table:
            return
        if tag == "table":
            self.table_depth -= 1
            if self.table_depth == 0:
                self.in_table = False
            return
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._cell_is_header and not self.headers:
                self.headers = self._row
            elif self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell_is_header = False

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


HEADER_MAP = {
    "nazwa": "name",
    "waluta": "currency",
    "kurs otwarcia": "open",
    "kurs maksymalny": "high",
    "kurs minimalny": "low",
    "kurs zamknięcia": "close",
    "kurs rozliczeniowy": "settlement",
    "zmiana kursu %": "change_pct",
    "wartość obrotu (w tys.)": "turnover_k",
    # not in the 2026 archive layout; mapped so a future layout with them feeds
    # panel.Series.volume instead of being dropped
    "wolumen": "volume",
    "liczba transakcji": "trades",
}
NUMERIC = ("open", "high", "low", "close", "settlement", "change_pct", "turnover_k", "volume", "trades")


def _norm_header(h: str) -> str:
    """'Kurs rozliczeniowy  *' / 'Zmiana kursu % **' -> footnote marks removed."""
    return " ".join(h.replace("*", " ").split()).strip().lower()


def parse_archive_html(html: str) -> Optional[List[Dict]]:
    """Return a list of quote dicts, or None when the page holds no quotes table
    (no session on that date)."""
    parser = _QuotesTableParser()
    parser.feed(html)
    if not parser.found or not parser.headers:
        return None
    keys = [HEADER_MAP.get(_norm_header(h)) for h in parser.headers]
    if "name" not in keys or "close" not in keys:
        raise GPWFetchError(
            "Unexpected GPW archive layout - headers: {}".format(parser.headers)
        )
    out = []
    for raw in parser.rows:
        if len(raw) != len(keys):
            continue
        rec = {}
        for key, val in zip(keys, raw):
            if key is None:
                continue
            rec[key] = parse_pl_number(val) if key in NUMERIC else val.strip()
        if not rec.get("name"):
            continue
        out.append(rec)
    if not out:
        return None
    return out


# Futures symbols: F + underlying + month code + 2-digit year [+ 2-digit
# multiplier]. Month codes are the standard futures letters (GPW uses
# H/M/U/Z for the quarterly index series). Index futures carry the
# multiplier in the symbol since the 2020s series (FW20...20 = 20 PLN per
# WIG20 point); single-stock futures (FPKOZ26) have no multiplier suffix -
# their contract size comes from the GPW contract specification.
FUTURES_MONTHS = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
                  "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}
FUTURES_UNDERLYINGS = {"W20": "WIG20", "W40": "MWIG40", "W80": "SWIG80", "WIG": "WIG"}
_FUT_RE = re.compile(r"^F([A-Z0-9]+?)([FGHJKMNQUVXZ])(\d{2})(\d{2})?$")


def parse_futures_symbol(symbol: str) -> Optional[Dict]:
    """``FW20Z2620`` -> {underlying_code: 'W20', underlying: 'WIG20', month: 12,
    year: 2026, multiplier: 20, expiry: date (3rd Friday)}; None if not a futures symbol."""
    m = _FUT_RE.match((symbol or "").strip().upper())
    if not m:
        return None
    code, mon, yy, mult = m.groups()
    month, year = FUTURES_MONTHS[mon], 2000 + int(yy)
    first = dt.date(year, month, 1)
    third_friday = first + dt.timedelta(days=(4 - first.weekday()) % 7 + 14)
    return {
        "symbol": symbol.strip().upper(),
        "underlying_code": code,
        "underlying": FUTURES_UNDERLYINGS.get(code, code),
        "month": month,
        "year": year,
        "multiplier": int(mult) if mult else None,
        "expiry": third_friday,  # GPW: 3rd Friday of the expiry month (moved if not a session day)
    }


def futures_rows(rows: List[Dict], underlying: str = "WIG20") -> List[Dict]:
    """Rows of one underlying with parsed symbol info, nearest expiry first."""
    out = []
    for r in rows or []:
        info = parse_futures_symbol(r.get("name", ""))
        if info and info["underlying"] == underlying:
            out.append(dict(r, **{k: v for k, v in info.items() if k != "symbol"}))
    out.sort(key=lambda r: (r["year"], r["month"]))
    return out


# --------------------------------------------------------------------------
# Fetching + cache
# --------------------------------------------------------------------------

def default_cache_dir() -> Path:
    env = os.environ.get("GPW_SKILLS_CACHE")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cache" / "gpw-trading-skills"


def archive_url(day: dt.date, type_id: int) -> str:
    return "{}?fetch=0&type={}&instrument=&date={}".format(
        BASE_URL, type_id, day.strftime("%d-%m-%Y")
    )


def _http_get(url: str, retries: int = 3, timeout: int = 30) -> str:
    global _last_request_at
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return raw.decode("utf-8", errors="replace")
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            last_err = exc
            time.sleep(2 ** attempt * 1.5)
    raise GPWFetchError("GPW archive unreachable ({}): {}".format(url, last_err))


def _cache_path(cache_dir: Path, day: dt.date, type_id: int) -> Path:
    return cache_dir / "archive" / TYPE_NAMES.get(type_id, str(type_id)) / "{}.json".format(
        day.isoformat()
    )


def fetch_session(
    day: dt.date,
    type_id: int = TYPE_SHARES,
    cache_dir: Optional[Path] = None,
    offline: bool = False,
) -> Optional[Dict]:
    """Return ``{"date", "rows", "source_url", "retrieved_at"}`` or None if no session.

    ``offline=True`` never touches the network (cache only; missing => None).
    """
    cache_dir = cache_dir or default_cache_dir()
    path = _cache_path(cache_dir, day, type_id)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema") == CACHE_SCHEMA:
                return payload if payload.get("session") else None
        except (ValueError, OSError):
            pass  # corrupt cache entry -> refetch
    if offline:
        return None
    if day > plcal.today_warsaw():
        return None
    url = archive_url(day, type_id)
    rows = parse_archive_html(_http_get(url))
    payload = {
        "schema": CACHE_SCHEMA,
        "date": day.isoformat(),
        "type": TYPE_NAMES.get(type_id, str(type_id)),
        "session": rows is not None,
        "rows": rows or [],
        "source_url": url,
        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    if plcal.session_is_final(day):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    return payload if payload["session"] else None


# Key set on a loaded session payload that follows probable sessions missing
# from the archive (not cached and not fetched - typically an ``offline`` run
# on an incomplete cache). ``panel.build_panel`` reads it and does not infer
# price adjustments across the gap; :func:`session_gaps` collects it.
MISSING_BEFORE = "missing_before"


def _is_missing(day: dt.date, type_id: int, cache_dir: Optional[Path]) -> bool:
    """A probable session with no cache entry at all (a cached "no session"
    day - an extra exchange holiday - is not missing)."""
    return not _cache_path(cache_dir or default_cache_dir(), day, type_id).exists()


def load_sessions(
    n_sessions: int,
    end: Optional[dt.date] = None,
    type_id: int = TYPE_SHARES,
    cache_dir: Optional[Path] = None,
    offline: bool = False,
    progress: bool = True,
) -> List[Dict]:
    """Load the last ``n_sessions`` sessions up to ``end`` (inclusive), oldest first.

    Online, a probable session missing from the cache is fetched. When it
    cannot be (``offline=True``, or today's session that is not final yet and
    so never cached), and it lies *between* two loaded sessions, the later
    session gets ``payload["missing_before"] = [ISO dates]`` (see
    :data:`MISSING_BEFORE`, :func:`session_gaps`). Missing days after the
    newest loaded session are not gaps (the data simply ends there).
    """
    end = end or plcal.today_warsaw()
    sessions: List[Dict] = []
    fetched = 0
    pending: List[str] = []  # missing days newer than the next (older) session found
    max_days = int(n_sessions * 1.6) + 30
    for day in plcal.previous_probable_sessions(end, max_days):
        cache_hit = _cache_path(cache_dir or default_cache_dir(), day, type_id).exists()
        payload = fetch_session(day, type_id, cache_dir=cache_dir, offline=offline)
        if not cache_hit and not offline:
            fetched += 1
            if progress and fetched % 25 == 0:
                print(
                    "  ... downloaded {} sessions from GPW archive (now at {})".format(
                        fetched, day.isoformat()
                    ),
                    file=sys.stderr,
                )
        if payload:
            if pending and sessions:
                sessions[-1][MISSING_BEFORE] = sorted(pending)
            pending = []
            sessions.append(payload)
            if len(sessions) >= n_sessions:
                break
        elif sessions and _is_missing(day, type_id, cache_dir):
            pending.append(day.isoformat())
    sessions.reverse()
    return sessions


def iter_sessions(
    start: dt.date,
    end: dt.date,
    type_id: int = TYPE_SHARES,
    cache_dir: Optional[Path] = None,
    offline: bool = False,
    missing: Optional[List[str]] = None,
    progress=None,
) -> Iterator[Dict]:
    """Yield the sessions in ``[start, end]`` oldest first, one at a time
    (streaming: a 7-year window never sits in memory as a list).

    Same fetch/cache rules as :func:`load_sessions`. Probable sessions that
    are neither cached nor fetchable are appended to ``missing`` (when given)
    and, when a later session follows, recorded on it as
    ``payload["missing_before"]``. ``progress(n_days_scanned, day)`` is called
    every 250 probable days.
    """
    pending: List[str] = []
    n = 0
    day = start
    while day <= end:
        if plcal.is_probable_session(day):
            n += 1
            if progress and n % 250 == 0:
                progress(n, day)
            payload = fetch_session(day, type_id, cache_dir=cache_dir, offline=offline)
            if payload:
                if pending:
                    payload[MISSING_BEFORE] = list(pending)
                    pending = []
                yield payload
            elif _is_missing(day, type_id, cache_dir):
                pending.append(day.isoformat())
                if missing is not None:
                    missing.append(day.isoformat())
        day += dt.timedelta(days=1)


def session_gaps(sessions: Iterable[Dict]) -> List[str]:
    """ISO dates of probable sessions missing between the given sessions
    (from the ``missing_before`` markers), minus any date that is present in
    the list itself (e.g. topped up later from another load)."""
    have, gaps = set(), set()
    for s in sessions:
        have.add(s.get("date"))
        gaps.update(s.get(MISSING_BEFORE) or ())
    return sorted(gaps - have)


def find_gaps(dates: Iterable[str], type_id: int = TYPE_SHARES, cache_dir: Optional[Path] = None) -> List[str]:
    """Probable sessions (``plcal``) strictly between the first and last of
    ``dates`` that are not in ``dates`` and have no cache entry - for callers
    that assemble session lists themselves. A day cached as "no session"
    (an exchange holiday missing from ``plcal``) is not a gap."""
    ds = sorted({d for d in dates if d})
    if len(ds) < 2:
        return []
    have = set(ds)
    out = []
    day = dt.date.fromisoformat(ds[0]) + dt.timedelta(days=1)
    last = dt.date.fromisoformat(ds[-1])
    while day < last:
        iso = day.isoformat()
        if iso not in have and plcal.is_probable_session(day) and _is_missing(day, type_id, cache_dir):
            out.append(iso)
        day += dt.timedelta(days=1)
    return out
