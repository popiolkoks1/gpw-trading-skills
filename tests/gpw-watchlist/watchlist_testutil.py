"""Synthetic panel helpers for the gpw-watchlist tests (unique module name)."""

import datetime as dt

from gpwlib.panel import Series  # noqa: E402


def trading_days(end: dt.date, n: int):
    """n weekday dates ending at ``end`` (inclusive), oldest first."""
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return list(reversed(out))


def make_series(name, closes, end=dt.date(2026, 11, 13), turnover=None, highs=None, lows=None):
    """Synthetic panel series: high/low = close +/- 1% unless given."""
    s = Series(name)
    days = trading_days(end, len(closes))
    s.dates = [d.isoformat() for d in days]
    s.close = list(closes)
    s.high = list(highs) if highs else [c * 1.01 for c in closes]
    s.low = list(lows) if lows else [c * 0.99 for c in closes]
    s.open = list(closes)
    s.volume = [None] * len(closes)
    s.turnover_k = list(turnover) if turnover else [1000.0] * len(closes)
    return s
