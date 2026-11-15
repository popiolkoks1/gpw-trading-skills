"""Small, dependency-free technical indicator helpers (pure functions)."""

from __future__ import annotations

import math
from typing import List, Optional, Sequence


def sma(values: Sequence[float], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if n <= 0:
        return out
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= n:
            acc -= values[i - n]
        if i >= n - 1:
            out[i] = acc / n
    return out


def ema(values: Sequence[Optional[float]], span: int) -> List[Optional[float]]:
    """EMA with alpha = 2/(span+1), adjust=False (pandas convention). Leading Nones skipped."""
    alpha = 2.0 / (span + 1)
    out: List[Optional[float]] = []
    prev: Optional[float] = None
    for v in values:
        if v is None:
            out.append(prev)
            continue
        prev = v if prev is None else alpha * v + (1 - alpha) * prev
        out.append(prev)
    return out


def true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> List[float]:
    out = []
    for i in range(len(close)):
        if i == 0:
            out.append(high[i] - low[i])
        else:
            pc = close[i - 1]
            out.append(max(high[i] - low[i], abs(high[i] - pc), abs(low[i] - pc)))
    return out


def atr(high, low, close, n: int = 14) -> Optional[float]:
    """Wilder ATR of the latest bar (None if not enough data)."""
    if len(close) < n + 1:
        return None
    tr = true_range(high, low, close)
    a = sum(tr[1 : n + 1]) / n
    for v in tr[n + 1 :]:
        a = (a * (n - 1) + v) / n
    return a


def pct_change(a: float, b: float) -> Optional[float]:
    if a is None or b is None or a == 0:
        return None
    return (b / a - 1) * 100.0


def median(values: Sequence[float]) -> Optional[float]:
    vals = sorted(v for v in values if v is not None and not math.isnan(v))
    if not vals:
        return None
    m = len(vals) // 2
    return vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2


def percentile_rank(history: Sequence[float], value: float) -> Optional[float]:
    vals = [v for v in history if v is not None]
    if not vals or value is None:
        return None
    below = sum(1 for v in vals if v < value)
    equal = sum(1 for v in vals if v == value)
    return 100.0 * (below + 0.5 * equal) / len(vals)


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def lerp_score(x: float, x0: float, x1: float, s0: float, s1: float) -> float:
    """Linear map of x in [x0, x1] onto [s0, s1], clamped to the segment."""
    if x1 == x0:
        return s0
    t = (x - x0) / (x1 - x0)
    t = max(0.0, min(1.0, t))
    return s0 + t * (s1 - s0)


def piecewise(x: float, points: Sequence) -> float:
    """Piecewise-linear score. points: ascending [(x, score), ...]."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, s0), (x1, s1) in zip(points, points[1:]):
        if x <= x1:
            return lerp_score(x, x0, x1, s0, s1)
    return points[-1][1]
