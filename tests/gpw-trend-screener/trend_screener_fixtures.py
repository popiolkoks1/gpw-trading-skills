"""Synthetic data helpers for the gpw-trend-screener tests."""
import datetime as dt
import math
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "gpw-trend-screener" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gpwlib.panel import Series  # noqa: E402


def trading_dates(n, start=dt.date(2024, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def path_through(points):
    """Piecewise-linear closes through [(n_bars, target), ...] starting at points[0][1]."""
    closes = [points[0][1]]
    for n, target in points[1:]:
        a = closes[-1]
        for k in range(1, n + 1):
            closes.append(a + (target - a) * k / n)
    return closes


def make_series(name, closes, turnover=None, dates=None, spread=0.003):
    s = Series(name)
    s.dates = list(dates or trading_dates(len(closes)))
    s.close = list(closes)
    s.high = [c * (1 + spread) for c in closes]
    s.low = [c * (1 - spread) for c in closes]
    s.turnover_k = list(turnover) if turnover is not None else [1000.0] * len(closes)
    return s


def vcp_closes(final=95.0):
    """~260-bar advance 50 -> 100, then a 3-contraction VCP (~20% -> ~10% -> ~5%)."""
    up = [50 * (2 ** (k / 259)) * (1 + 0.004 * math.sin(k / 2.0)) for k in range(260)]
    up[-1] = 100.0
    base = path_through([(0, 100.0), (10, 80.0), (15, 98.0), (8, 88.0), (12, 97.0), (6, 92.2), (6, final)])
    return up + base[1:]


def vcp_turnover(n, dry=True):
    t = [1000.0] * n
    if dry:
        for k in range(n - 10, n):
            t[k] = 500.0
    return t
