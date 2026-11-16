"""Synthetic market builders shared by the gpw-backtester tests."""
import datetime as dt

import backtester_data as bd


def sessions(n, start=dt.date(2021, 1, 4)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def flat_wig(days, level=100.0):
    return [(d, level, level, level, level, 1e9) for d in days]


def market(stocks, days, wig=None):
    return bd.MarketData.from_rows(stocks, wig=wig or flat_wig(days), calendar=list(days))


def bars_from_closes(days, closes, turnover=1e7, opens=None, lows=None, highs=None):
    rows = []
    for k, d in enumerate(days):
        c = closes[k]
        o = opens[k] if opens and opens[k] is not None else c
        lo = lows[k] if lows and lows[k] is not None else min(o, c)
        hi = highs[k] if highs and highs[k] is not None else max(o, c)
        rows.append((d, o, hi, lo, c, turnover))
    return rows
