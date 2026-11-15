"""Fetch, parse and cache daily end-of-day quotes from the official GPW archive.

Source: https://www.gpw.pl/archiwum-notowan (Warsaw Stock Exchange, "Archiwum
notowań"). One HTTP request returns every instrument of a given type for one
session. No API key is needed.

    https://www.gpw.pl/archiwum-notowan?fetch=0&type=10&instrument=&date=DD-MM-YYYY

``type=10`` - shares (Główny Rynek), ``type=1`` - indices.

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
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional

from . import plcal

BASE_URL = "https://www.gpw.pl/archiwum-notowan"
TYPE_SHARES = 10
TYPE_INDICES = 1
TYPE_NAMES = {TYPE_SHARES: "shares", TYPE_INDICES: "indices"}
USER_AGENT = "gpw-trading-skills/1.0 (educational market-analysis skills)"
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
    "zmiana kursu %": "change_pct",
    "wartość obrotu (w tys.)": "turnover_k",
}
NUMERIC = ("open", "high", "low", "close", "change_pct", "turnover_k")


def parse_archive_html(html: str) -> Optional[List[Dict]]:
    """Return a list of quote dicts, or None when the page holds no quotes table
    (no session on that date)."""
    parser = _QuotesTableParser()
    parser.feed(html)
    if not parser.found or not parser.headers:
        return None
    keys = [HEADER_MAP.get(h.strip().lower()) for h in parser.headers]
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


def load_sessions(
    n_sessions: int,
    end: Optional[dt.date] = None,
    type_id: int = TYPE_SHARES,
    cache_dir: Optional[Path] = None,
    offline: bool = False,
    progress: bool = True,
) -> List[Dict]:
    """Load the last ``n_sessions`` sessions up to ``end`` (inclusive), oldest first."""
    end = end or plcal.today_warsaw()
    sessions: List[Dict] = []
    fetched = 0
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
            sessions.append(payload)
            if len(sessions) >= n_sessions:
                break
    sessions.reverse()
    return sessions
