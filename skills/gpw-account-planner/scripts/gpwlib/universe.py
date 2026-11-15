"""Per-session cross-sectional statistics over the GPW share universe.

The universe on each session is every PLN-quoted share in the GPW archive
that day which passes the eligibility filter below. Because the archive is a
daily snapshot, delisted companies are included while they traded - the
market-wide statistics are therefore survivorship-free.

Eligibility (defaults, all overridable)
---------------------------------------
* traded on that session (it appears in that day's archive),
* at least ``min_history`` sessions of history (200 - needed for the SMA200),
* close >= ``min_price`` PLN (1.00): sub-1 zł "groszówki" move in large
  percentage ticks and distort participation counts,
* median turnover over the last 20 sessions >= ``min_turnover_k`` thousand PLN
  (50 = 50 000 zł/day): filters out shares that barely trade, whose closes are
  often stale single trades. ~50k zł/day keeps the investable part of sWIG80
  and the liquid end of the rest of the market.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .indicators import median, sma
from .panel import Series

DEFAULTS = {"min_history": 200, "min_price": 1.0, "min_turnover_k": 50.0}


class StockView:
    """Precomputed rolling values for one stock, indexed by its own observations."""

    __slots__ = ("s", "pos", "sma20", "sma50", "sma200", "med_turn20")

    def __init__(self, s: Series):
        self.s = s
        self.pos = {d: i for i, d in enumerate(s.dates)}
        self.sma20 = sma(s.close, 20)
        self.sma50 = sma(s.close, 50)
        self.sma200 = sma(s.close, 200)
        self.med_turn20 = [
            median(s.turnover_k[max(0, i - 19) : i + 1]) if i >= 19 else None
            for i in range(len(s))
        ]

    def high_low(self, i: int, lookback: int):
        """Max/min close of the ``lookback`` observations *before* i (exclusive)."""
        if i < lookback:
            return None, None
        window = self.s.close[i - lookback : i]
        return max(window), min(window)


def build_views(panel: Dict[str, Series]) -> Dict[str, StockView]:
    return {name: StockView(s) for name, s in panel.items()}


def eligible(v: StockView, i: int, cfg: Dict) -> bool:
    if i + 1 < cfg["min_history"]:
        return False
    if v.s.close[i] < cfg["min_price"]:
        return False
    mt = v.med_turn20[i]
    return mt is not None and mt >= cfg["min_turnover_k"]


def is_uptrend(v: StockView, i: int, low_lookback: int = 250) -> Optional[bool]:
    """Uptrend definition adapted from Monty's Uptrend Ratio (Finviz) screen:

    close > SMA20, close > SMA200, SMA50 > SMA200, close >= 1.3 x lowest close
    of the last ``low_lookback`` sessions (fewer if history is shorter, min 120),
    and positive 20-session (~4 weeks) return.
    """
    c = v.s.close[i]
    s20, s50, s200 = v.sma20[i], v.sma50[i], v.sma200[i]
    if None in (s20, s50, s200) or i < 20:
        return None
    lb = min(low_lookback, i)
    if lb < 120:
        return None
    low = min(v.s.close[i - lb : i + 1])
    return (
        c > s20
        and c > s200
        and s50 > s200
        and c >= 1.3 * low
        and c > v.s.close[i - 20]
    )


def session_dates(panel: Dict[str, Series]) -> List[str]:
    return sorted({d for s in panel.values() for d in s.dates})
