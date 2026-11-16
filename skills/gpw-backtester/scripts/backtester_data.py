"""GPW Backtester - market data container (pure, no network unless asked).

``MarketData`` holds daily bars for every PLN share that traded in the window
plus the WIG index, in compact per-instrument columns (``array("d")`` prices):

* opens, closes, highs and lows come from the streaming
  ``gpwlib.panel.build_panel(adjust=True, fields=...)`` (dividend- and
  split-adjusted, backward adjustment - see panel.py; the open gets the same
  factor as that day's close, exact because GPW adjusts the whole day's
  prices by one factor; no adjustment is inferred across sessions missing
  from the cache);
* turnover is in PLN (archive ``turnover_k`` * 1000).

A share that has no row on a session (no trade, suspension, or a session
halted at a static price threshold) simply has no bar that day. The engine
treats that as "cannot trade today" - a conservative proxy for suspensions and
price-limit days.

The universe is survivorship-free: each day's archive snapshot lists every
share that traded that day, including companies that were later delisted.
"""

from __future__ import annotations

import datetime as dt
from bisect import bisect_left, bisect_right
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from gpwlib import gpw_archive, panel as gpanel, plcal

KEEP = ("name", "currency", "open", "high", "low", "close", "change_pct", "turnover_k")


class Bars:
    """Daily bars of one instrument (oldest first)."""

    __slots__ = ("name", "dates", "open", "high", "low", "close", "turn", "adjustments", "_cache")

    def __init__(self, name: str, dates: List[str], o: List[float], h: List[float], l: List[float],
                 c: List[float], turn: List[float], adjustments: Optional[List[Dict]] = None):
        if not (len(dates) == len(o) == len(h) == len(l) == len(c) == len(turn)):
            raise ValueError("bars of {} have unequal lengths".format(name))
        self.name = name
        self.dates = dates
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.turn = turn  # PLN
        self.adjustments = adjustments or []
        self._cache: Dict = {}

    def __len__(self) -> int:
        return len(self.dates)

    def at(self, date: str) -> Optional[int]:
        """Index of the bar on ``date`` or None (bisect: no per-bar dict, saves memory)."""
        k = bisect_left(self.dates, date)
        return k if k < len(self.dates) and self.dates[k] == date else None

    def on_or_before(self, date: str) -> Optional[int]:
        k = bisect_right(self.dates, date) - 1
        return k if k >= 0 else None


class MarketData:
    """Calendar + share bars + WIG bars."""

    def __init__(self, stocks: Dict[str, Bars], wig: Optional[Bars], calendar: Optional[List[str]] = None,
                 meta: Optional[Dict] = None):
        self.stocks = stocks
        self.wig = wig
        if calendar is None:
            cal = set()
            for b in stocks.values():
                cal.update(b.dates)
            calendar = sorted(cal)
        self.calendar = calendar
        self.cal_pos = {d: i for i, d in enumerate(calendar)}
        self.meta = meta or {}

    @classmethod
    def from_rows(cls, stocks: Dict[str, Sequence[Tuple]], wig: Optional[Sequence[Tuple]] = None,
                  calendar: Optional[List[str]] = None) -> "MarketData":
        """Synthetic constructor for tests: rows are (date, open, high, low, close, turnover_pln)."""
        bars = {}
        for name, rows in stocks.items():
            rows = sorted(rows)
            bars[name] = Bars(name, [r[0] for r in rows], [float(r[1]) for r in rows], [float(r[2]) for r in rows],
                              [float(r[3]) for r in rows], [float(r[4]) for r in rows], [float(r[5]) for r in rows])
        wb = None
        if wig:
            rows = sorted(wig)
            wb = Bars("WIG", [r[0] for r in rows], [float(r[1]) for r in rows], [float(r[2]) for r in rows],
                      [float(r[3]) for r in rows], [float(r[4]) for r in rows], [float(r[5]) for r in rows])
        return cls(bars, wb, calendar)

    def window(self, start: str, end: str) -> List[str]:
        a, b = bisect_left(self.calendar, start), bisect_right(self.calendar, end)
        return self.calendar[a:b]


# ---------------------------------------------------------------------------
# Loading from the GPW archive cache (streaming: one session in memory at a time)
# ---------------------------------------------------------------------------
def probable_sessions(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    d = start
    while d <= end:
        if plcal.is_probable_session(d):
            yield d
        d += dt.timedelta(days=1)


def load_range(start: dt.date, end: dt.date, type_id: int, cache_dir=None, offline: bool = False,
               progress=None, sink=None) -> Tuple[List[Dict], List[str]]:
    """Archive sessions in [start, end], oldest first, and the probable sessions missing
    from the cache (never silent). With ``sink`` each payload is handed to it and not kept.
    Thin wrapper over ``gpwlib.gpw_archive.iter_sessions``."""
    out: List[Dict] = []
    missing: List[str] = []
    for payload in gpw_archive.iter_sessions(start, end, type_id, cache_dir=cache_dir, offline=offline,
                                             missing=missing, progress=progress):
        if sink is not None:
            sink(payload)
        else:
            row = {"date": payload["date"], "rows": [{k: r.get(k) for k in KEEP} for r in payload["rows"]]}
            if payload.get(gpw_archive.MISSING_BEFORE):
                row[gpw_archive.MISSING_BEFORE] = payload[gpw_archive.MISSING_BEFORE]
            out.append(row)
    return out, missing


# Columns the engine needs; ``volume`` is not in the archive and is dropped.
PANEL_FIELDS = ("open", "high", "low", "turnover_k")


def _bars_from_sessions(sessions: Iterable[Dict], calendar: List[str]) -> Dict[str, Bars]:
    """Adjusted bars through the public, streaming ``gpwlib.panel.build_panel``
    (dividend/split adjustment, no inference across missing sessions, open
    adjusted with the same factor as the close and clamped to high/low).
    Renamed tickers are *not* merged: the backtester trades the listing that
    existed on each day. ``calendar`` collects the session dates."""
    def stream():
        for sess in sessions:
            calendar.append(sess["date"])
            yield sess
    pnl = gpanel.build_panel(stream(), currency="PLN", adjust=True, merge_renames=False, fields=PANEL_FIELDS,
                             compact=True)
    stocks = {}
    for name in list(pnl):
        s = pnl.pop(name)
        stocks[name] = Bars(name, s.dates, s.open, s.high, s.low, s.close,
                            [t * 1000.0 for t in s.turnover_k], s.adjustments)
    return stocks


def index_bars(payloads_or_rows, index_name: str = "WIG") -> Optional[Bars]:
    d, o, h, l, c, t = [], [], [], [], [], []
    for sess in payloads_or_rows:
        for row in sess["rows"]:
            if row.get("name") == index_name and row.get("close"):
                d.append(sess["date"])
                c.append(row["close"])
                o.append(row.get("open") or row["close"])
                h.append(row.get("high") or row["close"])
                l.append(row.get("low") or row["close"])
                t.append((row.get("turnover_k") or 0.0) * 1000.0)
                break
    return Bars(index_name, d, o, h, l, c, t) if d else None


def build_market(share_sessions: List[Dict], index_sessions: List[Dict], index_name: str = "WIG") -> MarketData:
    """Adjusted bars from in-memory archive sessions (tests, small windows)."""
    cal: List[str] = []
    stocks = _bars_from_sessions(share_sessions, cal)
    return MarketData(stocks, index_bars(index_sessions, index_name), sorted(set(cal)))


def load_market(start: dt.date, end: dt.date, cache_dir=None, offline: bool = False, progress=None,
                index_name: str = "WIG") -> Tuple[MarketData, List[str], List[str]]:
    """Stream the archive into a MarketData without holding every session in memory
    (``gpw_archive.iter_sessions`` -> ``panel.build_panel``)."""
    miss_s: List[str] = []
    cal: List[str] = []
    stocks = _bars_from_sessions(gpw_archive.iter_sessions(start, end, gpw_archive.TYPE_SHARES, cache_dir=cache_dir,
                                                           offline=offline, missing=miss_s, progress=progress), cal)
    wig_rows: List[Dict] = []

    def keep_index(p):
        for row in p["rows"]:
            if row.get("name") == index_name:
                wig_rows.append({"date": p["date"], "rows": [row]})
                break
    _, miss_i = load_range(start, end, gpw_archive.TYPE_INDICES, cache_dir, offline, None, sink=keep_index)
    md = MarketData(stocks, index_bars(wig_rows, index_name), sorted(set(cal)))
    md.meta = {"sessions_loaded": len(cal)}
    return md, miss_s, miss_i
