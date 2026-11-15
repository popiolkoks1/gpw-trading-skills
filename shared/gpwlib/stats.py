"""Return and risk statistics on price series (pure functions, stdlib only).

Conventions: ``closes`` are oldest-first adjusted closes; one GPW year is
``SESSIONS_PER_YEAR`` = 252 sessions; returns are simple (not log) unless noted.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

SESSIONS_PER_YEAR = 252


def returns(closes: Sequence[float]) -> List[float]:
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]


def total_return(closes: Sequence[float]) -> Optional[float]:
    if len(closes) < 2 or closes[0] <= 0:
        return None
    return closes[-1] / closes[0] - 1


def cagr(closes: Sequence[float], periods_per_year: int = SESSIONS_PER_YEAR) -> Optional[float]:
    tr = total_return(closes)
    if tr is None or tr <= -1:
        return None
    years = (len(closes) - 1) / periods_per_year
    if years <= 0:
        return None
    return (1 + tr) ** (1 / years) - 1


def mean(xs: Sequence[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def stdev(xs: Sequence[float]) -> Optional[float]:
    """Sample standard deviation."""
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def volatility(closes: Sequence[float], periods_per_year: int = SESSIONS_PER_YEAR) -> Optional[float]:
    """Annualised volatility of simple returns."""
    sd = stdev(returns(closes))
    return sd * math.sqrt(periods_per_year) if sd is not None else None


def max_drawdown(closes: Sequence[float]) -> Dict:
    """Largest peak-to-trough fall: {'mdd': -0.23, 'peak_i', 'trough_i', 'current': drawdown now}."""
    peak, peak_i = None, 0
    best = {"mdd": 0.0, "peak_i": 0, "trough_i": 0}
    for i, c in enumerate(closes):
        if peak is None or c > peak:
            peak, peak_i = c, i
        dd = c / peak - 1 if peak else 0.0
        if dd < best["mdd"]:
            best = {"mdd": dd, "peak_i": peak_i, "trough_i": i}
    best["current"] = (closes[-1] / max(closes) - 1) if closes else 0.0
    return best


def correlation(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    n = min(len(a), len(b))
    if n < 3:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return cov / math.sqrt(va * vb)


def beta(asset_rets: Sequence[float], market_rets: Sequence[float]) -> Optional[float]:
    n = min(len(asset_rets), len(market_rets))
    if n < 3:
        return None
    a, m = asset_rets[-n:], market_rets[-n:]
    ma, mm = sum(a) / n, sum(m) / n
    cov = sum((x - ma) * (y - mm) for x, y in zip(a, m)) / (n - 1)
    var = sum((y - mm) ** 2 for y in m) / (n - 1)
    return cov / var if var > 0 else None


def historical_var(rets: Sequence[float], level: float = 0.95) -> Optional[float]:
    """Historical one-period Value at Risk as a positive loss fraction (e.g. 0.031 = 3.1%)."""
    if len(rets) < 20:
        return None
    s = sorted(rets)
    k = max(0, _tail_count(len(s), level) - 1)   # empirical quantile: the ceil(n*alpha)-th worst return
    return max(0.0, -s[k])


def expected_shortfall(rets: Sequence[float], level: float = 0.95) -> Optional[float]:
    if len(rets) < 20:
        return None
    s = sorted(rets)
    k = _tail_count(len(s), level)               # mean of the ceil(n*alpha) worst returns
    return max(0.0, -sum(s[:k]) / k)


def _tail_count(n: int, level: float) -> int:
    return max(1, int(math.ceil(round((1 - level) * n, 9))))


def align(series: Dict[str, Dict[str, float]]) -> Dict:
    """Align {name: {date: close}} on common dates. Returns {'dates': [...], 'closes': {name: [...]}}."""
    if not series:
        return {"dates": [], "closes": {}}
    common = None
    for m in series.values():
        common = set(m) if common is None else common & set(m)
    dates = sorted(common or [])
    return {"dates": dates, "closes": {k: [m[d] for d in dates] for k, m in series.items()}}


def hhi(weights: Sequence[float]) -> float:
    """Herfindahl-Hirschman index of weights that sum to 1 (1/N = perfectly equal)."""
    return sum(w * w for w in weights)
