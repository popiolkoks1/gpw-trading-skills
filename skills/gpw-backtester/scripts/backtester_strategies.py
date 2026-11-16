"""GPW Backtester - indicators, built-in strategies and the declarative rule format.

Every strategy decides at the CLOSE of session t using data up to and including
t; the engine fills at the OPEN of the next session in which the share trades.
Nothing here looks at t+1.

Built-in strategies (rationale and sources in references/methodology.md):

* ``trend_template``   - Minervini Trend Template (8 criteria incl. RS >= 70) entry,
                         exit on close below SMA50, 8% initial stop (O'Neil 7-8% rule).
* ``sma_cross``        - golden cross SMA50/SMA200, exit on death cross, ATR stop.
* ``breakout_52w``     - close above the prior 52-week high on above-average turnover,
                         exit below the 20-session low (Donchian/turtle exit), ATR stop.
* ``momentum_rotation``- top-N by 12-1 month momentum, monthly rebalance
                         (Jegadeesh & Titman 1993; on WSE: Zaremba & Konieczka 2016).
* ``dca_buy_hold``     - lump sum + optional monthly PLN into a WIG proxy or a ticker.
* ``rules``            - user JSON rules (see RULES_SPEC); parsed and validated, never eval'd.
"""

from __future__ import annotations

import json
import math
from bisect import bisect_left, bisect_right, insort
from typing import Dict, List, Optional, Tuple

from gpwlib.indicators import ema as _ema, sma as _sma, true_range

# ---------------------------------------------------------------------------
# Indicator series (full length, None during warm-up), cached on the Bars object
# ---------------------------------------------------------------------------


def _cached(b, key, fn):
    c = b._cache
    if key not in c:
        c[key] = fn()
    return c[key]


def field(b, name: str) -> List[float]:
    if name == "close":
        return b.close
    if name == "open":
        return b.open
    if name == "high":
        return b.high
    if name == "low":
        return b.low
    if name == "turnover":
        return b.turn
    raise ValueError("unknown field {!r}".format(name))


def sma_s(b, n: int, of: str = "close"):
    return _cached(b, ("sma", n, of), lambda: _sma(field(b, of), n))


def ema_s(b, n: int, of: str = "close"):
    def f():
        out = _ema(field(b, of), n)
        return [None if i < n - 1 else v for i, v in enumerate(out)]  # same warm-up as SMA
    return _cached(b, ("ema", n, of), f)


def roc_s(b, n: int, of: str = "close"):
    def f():
        x = field(b, of)
        return [None if i < n or x[i - n] <= 0 else (x[i] / x[i - n] - 1) * 100.0 for i in range(len(x))]
    return _cached(b, ("roc", n, of), f)


def highest_s(b, n: int, of: str = "high"):
    def f():
        x = field(b, of)
        out, dq = [None] * len(x), []  # monotonic deque of indices
        for i, v in enumerate(x):
            while dq and x[dq[-1]] <= v:
                dq.pop()
            dq.append(i)
            if dq[0] <= i - n:
                dq.pop(0)
            if i >= n - 1:
                out[i] = x[dq[0]]
        return out
    return _cached(b, ("highest", n, of), f)


def lowest_s(b, n: int, of: str = "low"):
    def f():
        x = field(b, of)
        out, dq = [None] * len(x), []
        for i, v in enumerate(x):
            while dq and x[dq[-1]] >= v:
                dq.pop()
            dq.append(i)
            if dq[0] <= i - n:
                dq.pop(0)
            if i >= n - 1:
                out[i] = x[dq[0]]
        return out
    return _cached(b, ("lowest", n, of), f)


def rsi_s(b, n: int = 14, of: str = "close"):
    """Wilder RSI."""
    def f():
        x = field(b, of)
        out = [None] * len(x)
        if len(x) <= n:
            return out
        gains = losses = 0.0
        for i in range(1, n + 1):
            d = x[i] - x[i - 1]
            gains += max(d, 0.0)
            losses += max(-d, 0.0)
        ag, al = gains / n, losses / n
        out[n] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
        for i in range(n + 1, len(x)):
            d = x[i] - x[i - 1]
            ag = (ag * (n - 1) + max(d, 0.0)) / n
            al = (al * (n - 1) + max(-d, 0.0)) / n
            out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
        return out
    return _cached(b, ("rsi", n, of), f)


def atr_s(b, n: int = 14):
    """Wilder ATR series (same smoothing as gpwlib.indicators.atr)."""
    def f():
        tr = true_range(b.high, b.low, b.close)
        out = [None] * len(tr)
        if len(tr) < n + 1:
            return out
        a = sum(tr[1:n + 1]) / n
        out[n] = a
        for i in range(n + 1, len(tr)):
            a = (a * (n - 1) + tr[i]) / n
            out[i] = a
        return out
    return _cached(b, ("atr", n), f)


def median_s(b, n: int = 20, of: str = "turnover"):
    """Rolling median (sorted sliding window, O(len * log n))."""
    def f():
        x = field(b, of)
        out, win = [None] * len(x), []
        for i, v in enumerate(x):
            insort(win, v)
            if i >= n:
                win.pop(bisect_left(win, x[i - n]))
            if i >= n - 1:
                m = len(win)
                out[i] = win[m // 2] if m % 2 else 0.5 * (win[m // 2 - 1] + win[m // 2])
        return out
    return _cached(b, ("median", n, of), f)


def vol_s(b, n: int = 20):
    """Rolling stdev of daily simple returns (used by the square-root impact model)."""
    def f():
        c = b.close
        r = [0.0] + [c[i] / c[i - 1] - 1 if c[i - 1] > 0 else 0.0 for i in range(1, len(c))]
        out, s, s2 = [None] * len(c), 0.0, 0.0
        for i in range(len(c)):
            s += r[i]
            s2 += r[i] * r[i]
            if i >= n:
                s -= r[i - n]
                s2 -= r[i - n] * r[i - n]
            if i >= n:
                var = max(0.0, (s2 - s * s / n) / (n - 1))
                out[i] = math.sqrt(var)
        return out
    return _cached(b, ("vol", n), f)


# ---------------------------------------------------------------------------
# Strategy base
# ---------------------------------------------------------------------------
UNIVERSE_DEFAULTS = {
    "min_price": 1.0,        # sub-1 zł "groszówki": huge % ticks (same as gpw-trend-screener)
    "min_turnover_k": 200.0,  # 200k zł/day median (gpw-trend-screener): below it a 10-20k order is > 5-10% of ADV
    "max_positions": 10,      # 10 slots of 10%: common retail diversification, ~1% risk each at an 8-10% stop
    "cooldown": 10,           # sessions before re-entering a name that was just exited (avoids churn)
}


class Strategy:
    name = "base"
    kind = "signal"  # signal | rotation | dca
    description = {"pl": "", "en": ""}
    PARAMS: Dict = {}
    GRID: Dict = {}

    def __init__(self, params: Optional[Dict] = None):
        p = dict(UNIVERSE_DEFAULTS)
        p.update(self.PARAMS)
        for k, v in (params or {}).items():
            if k not in p:
                raise ValueError("unknown parameter {!r} for strategy {} (allowed: {})".format(
                    k, self.name, ", ".join(sorted(p))))
            p[k] = _coerce(p[k], v, k)
        self.params = p
        self.validate()
        self.md = None
        self.entries_by_date: Dict[str, List[Tuple[float, str]]] = {}
        self.exit_flags: Dict[str, List[bool]] = {}

    def validate(self):
        p = self.params
        if p["max_positions"] < 1:
            raise ValueError("max_positions must be >= 1")
        if p["min_price"] < 0 or p["min_turnover_k"] < 0:
            raise ValueError("min_price / min_turnover_k must be >= 0")

    # -- helpers ----------------------------------------------------------
    def eligible_flags(self, b) -> List[bool]:
        med = median_s(b, 20)
        mp, mt = self.params["min_price"], self.params["min_turnover_k"] * 1000.0
        return [med[i] is not None and b.close[i] >= mp and med[i] >= mt for i in range(len(b))]

    def prepare(self, md) -> None:
        self.md = md

    def entry_candidates(self, date: str) -> List[Tuple[float, str]]:
        """[(score, name)] best first, decided at the close of ``date``."""
        return self.entries_by_date.get(date, [])

    def exit_signal(self, name: str, i: int) -> bool:
        f = self.exit_flags.get(name)
        return bool(f and f[i])

    def stop_for(self, b, i: int, price: float) -> Optional[float]:
        """Initial stop for an entry at ``price`` decided with data up to bar i."""
        p = self.params
        if p.get("stop_pct"):
            return price * (1 - p["stop_pct"] / 100.0)
        if p.get("stop_atr"):
            a = atr_s(b, 14)[i]
            if a:
                s = price - p["stop_atr"] * a
                return s if s > 0 else None
        return None

    def trail_pct(self) -> Optional[float]:
        return self.params.get("trail_pct") or None

    def max_hold(self) -> Optional[int]:
        return self.params.get("max_hold") or None

    def rebalance_targets(self, date: str) -> Optional[List[str]]:
        return None

    def _index_entries(self, name: str, flags: List[bool], score: List[Optional[float]], b) -> None:
        for i, f in enumerate(flags):
            if f:
                self.entries_by_date.setdefault(b.dates[i], []).append((score[i] if score[i] is not None else 0.0, name))

    def _sort_entries(self):
        for d in self.entries_by_date:
            self.entries_by_date[d].sort(key=lambda x: (-x[0], x[1]))

    def describe(self, lang: str) -> str:
        return self.description.get(lang) or self.description.get("en", "")


def _coerce(default, v, key):
    try:
        if isinstance(default, bool):
            return _to_bool(v)
        if isinstance(default, int):
            f = float(v)
            if f != int(f):
                raise ValueError
            return int(f)
        if isinstance(default, float):
            f = float(v)
            if not math.isfinite(f):
                raise ValueError
            return f
        return str(v)
    except (TypeError, ValueError):
        raise ValueError("parameter {} has an invalid value {!r}".format(key, v))


def _to_bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y", "tak")


# ---------------------------------------------------------------------------
# trend_template
# ---------------------------------------------------------------------------
QUARTER = 63


def _rs_raw(close, i):
    """IBD-style weighted 12-month performance 2*Q1+Q2+Q3+Q4 (as in gpw-trend-screener)."""
    qs = []
    for q in range(4):
        a, bb = i - (q + 1) * QUARTER, i - q * QUARTER
        if a < 0:
            break
        if close[a] <= 0:
            return None
        qs.append((close[bb] / close[a] - 1) * 100.0)
    if len(qs) < 2:
        return None
    w = [2.0] + [1.0] * (len(qs) - 1)
    return sum(wi * qi for wi, qi in zip(w, qs)) / sum(w) * 5.0


class TrendTemplate(Strategy):
    name = "trend_template"
    description = {
        "pl": "Wejście, gdy spółka spełnia 8 kryteriów Trend Template Minerviniego (kurs > SMA150/200, SMA150 > SMA200, "
              "SMA200 rośnie od miesiąca, SMA50 > SMA150/200, kurs > SMA50, ≥30% nad dołkiem i ≤25% pod szczytem 52 tyg., "
              "RS ≥ 70); kolejność wg RS. Wyjście: zamknięcie poniżej SMA50 lub stop początkowy 8%.",
        "en": "Enter when a share passes Minervini's 8-point Trend Template (close > SMA150/200, SMA150 > SMA200, SMA200 "
              "rising for a month, SMA50 > SMA150/200, close > SMA50, >= 30% above the 52-week low and within 25% of the "
              "high, RS >= 70), ranked by RS. Exit on a close below SMA50 or an 8% initial stop.",
    }
    PARAMS = {"min_rs": 70, "exit_sma": 50, "stop_pct": 8.0, "trail_pct": 0.0, "max_hold": 0}
    GRID = {"stop_pct": [6.0, 8.0, 10.0], "exit_sma": [20, 50], "min_rs": [70, 80]}

    def validate(self):
        super().validate()
        if not 1 <= self.params["min_rs"] <= 99:
            raise ValueError("min_rs must be 1-99")
        if self.params["exit_sma"] < 2:
            raise ValueError("exit_sma must be >= 2")

    def prepare(self, md):
        super().prepare(md)
        p = self.params
        raws: Dict[str, List[Optional[float]]] = {}
        elig: Dict[str, List[bool]] = {}
        for name, b in md.stocks.items():
            if len(b) < 221:
                continue
            raws[name] = _cached(b, ("rsraw",), lambda b=b: [_rs_raw(b.close, i) for i in range(len(b))])
            elig[name] = self.eligible_flags(b)
        # cross-sectional RS rating per date among eligible names
        by_date: Dict[str, List[Tuple[float, str]]] = {}
        for name, r in raws.items():
            b = md.stocks[name]
            e = elig[name]
            for i, v in enumerate(r):
                if v is not None and e[i]:
                    by_date.setdefault(b.dates[i], []).append((v, name))
        rating: Dict[Tuple[str, str], int] = {}
        for d, lst in by_date.items():
            vals = sorted(v for v, _ in lst)
            n = len(vals)
            for v, name in lst:
                below = bisect_left(vals, v)
                eq = bisect_right(vals, v) - below
                pr = 100.0 * (below + 0.5 * eq) / n
                rating[(d, name)] = int(max(1, min(99, round(pr))))
        for name in raws:
            b = md.stocks[name]
            c = b.close
            s50, s150, s200 = sma_s(b, 50), sma_s(b, 150), sma_s(b, 200)
            sx = sma_s(b, p["exit_sma"])
            hi, lo = highest_s(b, 252, "high"), lowest_s(b, 252, "low")
            e = elig[name]
            flags, score = [False] * len(b), [None] * len(b)
            for i in range(len(b)):
                if i < 221 or not e[i]:
                    continue
                rs = rating.get((b.dates[i], name))
                if rs is None or rs < p["min_rs"]:
                    continue
                a50, a150, a200, a200p = s50[i], s150[i], s200[i], s200[i - 21]
                if a200p is None:
                    continue
                h52 = hi[i] if hi[i] is not None else max(b.high[:i + 1])
                l52 = lo[i] if lo[i] is not None else min(b.low[:i + 1])
                ok = (c[i] > a150 and c[i] > a200 and a150 > a200 and a200 > a200p and a50 > a150 and a50 > a200
                      and c[i] > a50 and c[i] >= 1.30 * l52 and c[i] >= 0.75 * h52)
                if ok:
                    flags[i] = True
                    score[i] = float(rs)
            self._index_entries(name, flags, score, b)
            self.exit_flags[name] = [sx[i] is not None and c[i] < sx[i] for i in range(len(b))]
        self._sort_entries()


# ---------------------------------------------------------------------------
# sma_cross
# ---------------------------------------------------------------------------
class SmaCross(Strategy):
    name = "sma_cross"
    description = {
        "pl": "Złoty krzyż: wejście, gdy SMA50 przecina SMA200 od dołu; wyjście przy krzyżu śmierci (SMA50 < SMA200). "
              "Stop początkowy 3×ATR(14). Kolejność wg zmiany kursu z 126 sesji.",
        "en": "Golden cross: enter when SMA50 crosses above SMA200; exit on the death cross (SMA50 < SMA200). "
              "Initial stop 3x ATR(14). Ranked by 126-session rate of change.",
    }
    PARAMS = {"fast": 50, "slow": 200, "stop_atr": 3.0, "stop_pct": 0.0, "trail_pct": 0.0, "max_hold": 0}
    GRID = {"fast": [20, 50], "slow": [100, 150, 200], "stop_atr": [2.0, 3.0]}

    def validate(self):
        super().validate()
        if not 2 <= self.params["fast"] < self.params["slow"]:
            raise ValueError("sma_cross needs 2 <= fast < slow")

    def prepare(self, md):
        super().prepare(md)
        p = self.params
        for name, b in md.stocks.items():
            if len(b) < p["slow"] + 2:
                continue
            f, s, r = sma_s(b, p["fast"]), sma_s(b, p["slow"]), roc_s(b, 126)
            e = self.eligible_flags(b)
            n = len(b)
            flags = [False] * n
            for i in range(1, n):
                if e[i] and s[i - 1] is not None and f[i] > s[i] and f[i - 1] <= s[i - 1]:
                    flags[i] = True
            self._index_entries(name, flags, r, b)
            self.exit_flags[name] = [s[i] is not None and f[i] < s[i] for i in range(n)]
        self._sort_entries()


# ---------------------------------------------------------------------------
# breakout_52w
# ---------------------------------------------------------------------------
class Breakout52w(Strategy):
    name = "breakout_52w"
    description = {
        "pl": "Wybicie: zamknięcie powyżej najwyższego kursu z poprzednich 252 sesji przy obrocie ≥ 1,5× mediany z 50 sesji "
              "(O'Neil: obrót wybicia ≥ 40–50% powyżej średniej). Wyjście: zamknięcie poniżej minimum z 20 sesji "
              "(wyjście Donchiana/żółwi). Stop 2×ATR(14).",
        "en": "Breakout: close above the highest high of the previous 252 sessions on turnover >= 1.5x the 50-session "
              "median (O'Neil: breakout volume 40-50% above average). Exit on a close below the 20-session low "
              "(Donchian/turtle exit). Stop 2x ATR(14).",
    }
    PARAMS = {"lookback": 252, "vol_mult": 1.5, "exit_low": 20, "stop_atr": 2.0, "stop_pct": 0.0,
              "trail_pct": 0.0, "max_hold": 0}
    GRID = {"exit_low": [10, 20, 50], "stop_atr": [2.0, 3.0], "vol_mult": [1.0, 1.5]}

    def validate(self):
        super().validate()
        if self.params["lookback"] < 20 or self.params["exit_low"] < 2:
            raise ValueError("breakout_52w needs lookback >= 20 and exit_low >= 2")

    def prepare(self, md):
        super().prepare(md)
        p = self.params
        L = p["lookback"]
        for name, b in md.stocks.items():
            if len(b) < L + 2:
                continue
            hi = highest_s(b, L, "high")
            lo = lowest_s(b, p["exit_low"], "low")
            m50 = median_s(b, 50)
            e = self.eligible_flags(b)
            n = len(b)
            flags, score = [False] * n, [None] * n
            for i in range(L, n):
                if not e[i] or hi[i - 1] is None or m50[i] is None or m50[i] <= 0:
                    continue
                ratio = b.turn[i] / m50[i]
                if b.close[i] > hi[i - 1] and ratio >= p["vol_mult"]:
                    flags[i] = True
                    score[i] = ratio
            self._index_entries(name, flags, score, b)
            self.exit_flags[name] = [i > 0 and lo[i - 1] is not None and b.close[i] < lo[i - 1] for i in range(n)]
        self._sort_entries()


# ---------------------------------------------------------------------------
# momentum_rotation
# ---------------------------------------------------------------------------
def month_end_dates(calendar: List[str]) -> set:
    out = set()
    for k in range(len(calendar)):
        if k + 1 == len(calendar) or calendar[k + 1][:7] != calendar[k][:7]:
            out.add(calendar[k])
    return out


class MomentumRotation(Strategy):
    name = "momentum_rotation"
    kind = "rotation"
    description = {
        "pl": "Rotacja momentum: na ostatniej sesji miesiąca ranking płynnych spółek wg stopy zwrotu 12-1 (od 252 do 21 sesji "
              "wstecz, z pominięciem ostatniego miesiąca – Jegadeesh i Titman 1993), trzymamy top-N równymi wagami. Spółka "
              "zostaje, dopóki jest w top N×bufor (ogranicza obrót).",
        "en": "Momentum rotation: on the last session of each month rank liquid shares by 12-1 month return (252 to 21 "
              "sessions back, skipping the last month - Jegadeesh & Titman 1993) and hold the top N equally weighted. A "
              "holding stays while it ranks within top N x buffer (limits turnover).",
    }
    PARAMS = {"lookback": 252, "skip": 21, "top_n": 10, "buffer": 1.5, "abs_filter": False,
              "stop_pct": 0.0, "trail_pct": 0.0, "max_hold": 0}
    GRID = {"lookback": [126, 252], "skip": [0, 21], "top_n": [5, 10, 15]}

    def validate(self):
        super().validate()
        p = self.params
        if p["lookback"] <= p["skip"] or p["skip"] < 0:
            raise ValueError("momentum_rotation needs lookback > skip >= 0")
        if p["top_n"] < 1 or p["buffer"] < 1:
            raise ValueError("top_n >= 1 and buffer >= 1 required")
        p["max_positions"] = p["top_n"]

    def prepare(self, md):
        super().prepare(md)
        p = self.params
        self.rebal = month_end_dates(md.calendar)
        self.mom: Dict[str, Dict[str, float]] = {}
        L, S = p["lookback"], p["skip"]
        for name, b in md.stocks.items():
            if len(b) < L + 1:
                continue
            e = self.eligible_flags(b)
            c = b.close
            for i in range(L, len(b)):
                d = b.dates[i]
                if d not in self.rebal or not e[i] or c[i - L] <= 0:
                    continue
                self.mom.setdefault(d, {})[name] = (c[i - S] / c[i - L] - 1) * 100.0

    def ranking(self, date: str) -> List[Tuple[str, float]]:
        m = self.mom.get(date) or {}
        r = sorted(m.items(), key=lambda kv: (-kv[1], kv[0]))
        if self.params["abs_filter"]:
            r = [x for x in r if x[1] > 0]
        return r

    def rebalance_targets(self, date: str, held: Optional[List[str]] = None) -> Optional[List[str]]:
        if date not in self.rebal or date not in self.mom:
            return None
        p = self.params
        ranked = [n for n, _ in self.ranking(date)]
        keep_cut = int(math.ceil(p["top_n"] * p["buffer"]))
        keep = [n for n in (held or []) if n in ranked[:keep_cut]]
        out = list(keep)
        for n in ranked:
            if len(out) >= p["top_n"]:
                break
            if n not in out:
                out.append(n)
        return out[:max(p["top_n"], len(keep))]


# ---------------------------------------------------------------------------
# dca_buy_hold
# ---------------------------------------------------------------------------
WIG_PROXY = "WIG_PROXY"


class DcaBuyHold(Strategy):
    name = "dca_buy_hold"
    kind = "dca"
    description = {
        "pl": "Kup i trzymaj: cały kapitał na pierwszej sesji, opcjonalnie stała kwota co miesiąc, w syntetyczny fundusz na WIG "
              "(jednostka = WIG/1000, opłata roczna TER 0,5%) albo w wskazany walor. Punkt odniesienia z realnymi kosztami.",
        "en": "Buy and hold: the whole capital on the first session plus an optional fixed monthly amount, into a synthetic "
              "WIG fund (unit = WIG/1000, 0.5% yearly TER) or a given ticker. A benchmark with real costs.",
    }
    PARAMS = {"monthly": 0.0, "ticker": "", "ter_pct": 0.5}
    GRID: Dict = {}

    def validate(self):
        super().validate()
        if self.params["monthly"] < 0 or self.params["ter_pct"] < 0:
            raise ValueError("monthly and ter_pct must be >= 0")
        self.params["max_positions"] = 1

    def instrument(self) -> str:
        return self.params["ticker"] or WIG_PROXY


# ---------------------------------------------------------------------------
# Declarative rules (JSON) - parsed into a small AST, evaluated without eval()
# ---------------------------------------------------------------------------
RULE_INDICATORS = {
    # name: (needs_n, default_of, allowed_of)
    "close": (False, None, None), "open": (False, None, None), "high": (False, None, None),
    "low": (False, None, None), "turnover": (False, None, None),
    "sma": (True, "close", ("close", "high", "low", "open", "turnover")),
    "ema": (True, "close", ("close", "high", "low", "open", "turnover")),
    "roc": (True, "close", ("close", "turnover")),
    "rsi": (True, "close", ("close",)),
    "highest": (True, "high", ("close", "high", "low", "turnover")),
    "lowest": (True, "low", ("close", "high", "low", "turnover")),
    "atr": (True, None, None),
    "median": (True, "turnover", ("close", "turnover")),
}
RULE_OPS = (">", ">=", "<", "<=", "crosses_above", "crosses_below")
RULE_TOP_KEYS = {"name", "description", "entry", "exit", "stop", "trail_pct", "max_hold", "rank", "params"}
RULE_PARAM_KEYS = {"max_positions", "min_turnover_k", "min_price", "cooldown"}
MAX_N, MAX_SHIFT, MAX_CONDITIONS, MAX_DEPTH = 400, 60, 30, 3


class RuleError(ValueError):
    pass


def _operand(x, path):
    if isinstance(x, bool):
        raise RuleError("{}: booleans are not valid operands".format(path))
    if isinstance(x, (int, float)):
        if not math.isfinite(x):
            raise RuleError("{}: number must be finite".format(path))
        return {"const": float(x)}
    if not isinstance(x, dict):
        raise RuleError("{}: operand must be a number or an object with 'ind'".format(path))
    bad = set(x) - {"ind", "n", "of", "shift", "mult"}
    if bad:
        raise RuleError("{}: unknown key(s) {}".format(path, ", ".join(sorted(bad))))
    ind = x.get("ind")
    if ind not in RULE_INDICATORS:
        raise RuleError("{}: unknown indicator {!r} (allowed: {})".format(path, ind, ", ".join(sorted(RULE_INDICATORS))))
    needs_n, dof, allowed = RULE_INDICATORS[ind]
    n = x.get("n")
    if needs_n:
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_N:
            raise RuleError("{}: '{}' needs an integer n in 1..{}".format(path, ind, MAX_N))
    elif n is not None:
        raise RuleError("{}: '{}' takes no n".format(path, ind))
    of = x.get("of", dof)
    if allowed is None and "of" in x:
        raise RuleError("{}: '{}' takes no 'of'".format(path, ind))
    if allowed is not None and of not in allowed:
        raise RuleError("{}: 'of' must be one of {}".format(path, ", ".join(allowed)))
    shift = x.get("shift", 0)
    if not isinstance(shift, int) or isinstance(shift, bool) or not 0 <= shift <= MAX_SHIFT:
        raise RuleError("{}: shift must be an integer 0..{}".format(path, MAX_SHIFT))
    mult = x.get("mult", 1.0)
    if isinstance(mult, bool) or not isinstance(mult, (int, float)) or not mult > 0:
        raise RuleError("{}: mult must be a positive number".format(path))
    return {"ind": ind, "n": n, "of": of, "shift": shift, "mult": float(mult)}


def _cond(x, path, depth, counter):
    if not isinstance(x, dict):
        raise RuleError("{}: condition must be an object".format(path))
    if "all" in x or "any" in x:
        if len(x) != 1:
            raise RuleError("{}: a group has exactly one key 'all' or 'any'".format(path))
        if depth >= MAX_DEPTH:
            raise RuleError("{}: groups nested deeper than {}".format(path, MAX_DEPTH))
        key = "all" if "all" in x else "any"
        items = x[key]
        if not isinstance(items, list) or not items:
            raise RuleError("{}.{}: must be a non-empty list".format(path, key))
        return {key: [_cond(c, "{}.{}[{}]".format(path, key, k), depth + 1, counter) for k, c in enumerate(items)]}
    bad = set(x) - {"left", "op", "right"}
    if bad or not {"left", "op", "right"} <= set(x):
        raise RuleError("{}: condition needs exactly 'left', 'op', 'right'".format(path))
    if x["op"] not in RULE_OPS:
        raise RuleError("{}: op must be one of {}".format(path, ", ".join(RULE_OPS)))
    counter[0] += 1
    if counter[0] > MAX_CONDITIONS:
        raise RuleError("too many conditions (max {})".format(MAX_CONDITIONS))
    return {"left": _operand(x["left"], path + ".left"), "op": x["op"], "right": _operand(x["right"], path + ".right")}


def parse_rules(doc) -> Dict:
    """Validate a rule document (dict or JSON text) and return the normalised AST."""
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except ValueError as exc:
            raise RuleError("rules file is not valid JSON: {}".format(exc))
    if not isinstance(doc, dict):
        raise RuleError("rules must be a JSON object")
    bad = set(doc) - RULE_TOP_KEYS
    if bad:
        raise RuleError("unknown top-level key(s): {} (allowed: {})".format(", ".join(sorted(bad)), ", ".join(sorted(RULE_TOP_KEYS))))
    if "entry" not in doc:
        raise RuleError("'entry' is required")
    counter = [0]
    out = {"name": str(doc.get("name") or "custom_rules")[:60], "description": str(doc.get("description") or "")[:500]}
    out["entry"] = _cond(doc["entry"], "entry", 0, counter)
    out["exit"] = _cond(doc["exit"], "exit", 0, counter) if doc.get("exit") is not None else None
    stop = doc.get("stop")
    if stop is not None:
        if not isinstance(stop, dict) or set(stop) - {"pct", "atr_mult"} or len(stop) != 1:
            raise RuleError("stop must be {\"pct\": X} or {\"atr_mult\": X}")
        v = list(stop.values())[0]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= (50 if "pct" in stop else 10):
            raise RuleError("stop value out of range (pct 0-50, atr_mult 0-10)")
    out["stop"] = stop
    for k, hi in (("trail_pct", 50), ("max_hold", 2000)):
        v = doc.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= hi):
            raise RuleError("{} must be a number in (0, {}]".format(k, hi))
        out[k] = v
    out["rank"] = _operand(doc["rank"], "rank") if doc.get("rank") is not None else _operand({"ind": "roc", "n": 126}, "rank")
    params = doc.get("params") or {}
    if not isinstance(params, dict) or set(params) - RULE_PARAM_KEYS:
        raise RuleError("params may only contain: {}".format(", ".join(sorted(RULE_PARAM_KEYS))))
    out["params"] = params
    if out["exit"] is None and not stop and not out["trail_pct"] and not out["max_hold"]:
        raise RuleError("a rule set needs at least one way out: exit, stop, trail_pct or max_hold")
    return out


def _series(b, op):
    if "const" in op:
        return None
    ind, n, of = op["ind"], op["n"], op["of"]
    if ind in ("close", "open", "high", "low", "turnover"):
        base = field(b, ind)
    elif ind == "sma":
        base = sma_s(b, n, of)
    elif ind == "ema":
        base = ema_s(b, n, of)
    elif ind == "roc":
        base = roc_s(b, n, of)
    elif ind == "rsi":
        base = rsi_s(b, n, of)
    elif ind == "highest":
        base = highest_s(b, n, of)
    elif ind == "lowest":
        base = lowest_s(b, n, of)
    elif ind == "atr":
        base = atr_s(b, n)
    else:  # median
        base = median_s(b, n, of)
    return base


def _val(b, op, i):
    if "const" in op:
        return op["const"]
    j = i - op["shift"]
    if j < 0:
        return None
    v = _series(b, op)[j]
    return None if v is None else v * op["mult"]


def eval_cond(b, node, i) -> bool:
    if "all" in node:
        return all(eval_cond(b, c, i) for c in node["all"])
    if "any" in node:
        return any(eval_cond(b, c, i) for c in node["any"])
    l, r = _val(b, node["left"], i), _val(b, node["right"], i)
    if l is None or r is None:
        return False
    op = node["op"]
    if op == ">":
        return l > r
    if op == ">=":
        return l >= r
    if op == "<":
        return l < r
    if op == "<=":
        return l <= r
    lp, rp = _val(b, node["left"], i - 1) if i > 0 else None, _val(b, node["right"], i - 1) if i > 0 else None
    if lp is None or rp is None:
        return False
    if op == "crosses_above":
        return l > r and lp <= rp
    return l < r and lp >= rp


class RuleStrategy(Strategy):
    name = "rules"
    description = {"pl": "Własne reguły z pliku JSON.", "en": "Custom rules from a JSON file."}
    PARAMS = {"stop_pct": 0.0, "stop_atr": 0.0, "trail_pct": 0.0, "max_hold": 0}

    def __init__(self, rules: Dict, params: Optional[Dict] = None):
        self.rules = rules
        merged = dict(rules.get("params") or {})
        st = rules.get("stop") or {}
        if "pct" in st:
            merged["stop_pct"] = float(st["pct"])
        if "atr_mult" in st:
            merged["stop_atr"] = float(st["atr_mult"])
        if rules.get("trail_pct"):
            merged["trail_pct"] = float(rules["trail_pct"])
        if rules.get("max_hold"):
            merged["max_hold"] = int(rules["max_hold"])
        merged.update(params or {})
        super().__init__(merged)
        self.name = "rules:" + rules["name"]
        if rules.get("description"):
            self.description = {"pl": rules["description"], "en": rules["description"]}

    def prepare(self, md):
        super().prepare(md)
        for name, b in md.stocks.items():
            if len(b) < 25:
                continue
            e = self.eligible_flags(b)
            n = len(b)
            flags = [e[i] and eval_cond(b, self.rules["entry"], i) for i in range(n)]
            score = [_val(b, self.rules["rank"], i) for i in range(n)]
            self._index_entries(name, flags, score, b)
            if self.rules["exit"] is not None:
                self.exit_flags[name] = [eval_cond(b, self.rules["exit"], i) for i in range(n)]
        self._sort_entries()


RULES_SPEC = {
    "top_level": sorted(RULE_TOP_KEYS),
    "operand": "number | {ind, n?, of?, shift?, mult?}",
    "indicators": sorted(RULE_INDICATORS),
    "ops": list(RULE_OPS),
    "limits": {"n": MAX_N, "shift": MAX_SHIFT, "conditions": MAX_CONDITIONS, "depth": MAX_DEPTH},
}

REGISTRY = {cls.name: cls for cls in (TrendTemplate, SmaCross, Breakout52w, MomentumRotation, DcaBuyHold)}


def make(name: str, params: Optional[Dict] = None, rules: Optional[Dict] = None) -> Strategy:
    if rules is not None:
        return RuleStrategy(rules, params)
    if name not in REGISTRY:
        raise ValueError("unknown strategy {!r}; choose one of {}".format(name, ", ".join(sorted(REGISTRY))))
    return REGISTRY[name](params)
