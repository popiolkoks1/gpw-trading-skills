"""GPW Trend Screener - pure engine (no I/O, no network).

Minervini Trend Template + IBD-style RS rating + VCP (volatility contraction
pattern) detection on the GPW share universe. Every threshold is documented in
``references/methodology.md``; the short reason is repeated next to each
constant below.

All functions work on per-stock *observation* indices (``i`` = position in the
stock's own adjusted series). Nothing here reads files or the network, so the
engine is fully testable on synthetic data.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from statistics import mean as _mean
from typing import Dict, List, Optional, Tuple

from gpwlib.indicators import atr, median, piecewise, sma

# ---------------------------------------------------------------------------
# Configuration (see references/methodology.md for the full rationale)
# ---------------------------------------------------------------------------
DEFAULTS: Dict = {
    # Pre-filter
    "min_turnover_k": 200.0,  # 200k zł/day median: below that a 10-20k zł position is already >5% of ADV
    "min_price": 1.0,          # sub-1 zł "groszówki" move in huge % ticks
    "min_history": 221,        # SMA200 + 21 sessions to measure its slope
    "full_history": 252,       # one trading year for 52-week high/low and 4-quarter RS
    # Trend Template (Minervini, "Trade Like a Stock Market Wizard", ch. 5)
    "sma200_slope_lag": 21,    # "200-day MA trending up for at least 1 month" (~21 sessions)
    "above_low_mult": 1.30,    # at least 30% above the 52-week low
    "near_high_mult": 0.75,    # within 25% of the 52-week high
    "min_rs": 70,              # RS rating no less than 70 (Minervini; IBD prefers 80+)
    "min_criteria": 8,         # all 8 criteria (Minervini's rule); 7 only as a looser watchlist
    # VCP (Minervini, ch. 10)
    "vcp_lookback": 120,       # ~6 months of base; Minervini: VCPs form in 3 to 65 weeks
    "swing_left": 5,           # a swing high/low is the extreme of the previous 5 sessions (1 week) ...
    "swing_right": 3,          # ... and of the next 3 (short right side keeps the last pivot fresh)
    "min_contraction_pct": 2.0,  # dips < 2% are noise, merged into the surrounding move
    "min_contractions": 2,     # Minervini: typically 2 to 6 "T"s; the brief caps the count at 5
    "max_contractions": 5,
    "max_first_depth_pct": 40.0,  # O'Neil/Minervini: bases deeper than ~35-40% fail more often
    "max_last_depth_pct": 12.0,   # last contraction tight: Minervini shows 3-10%, we allow 12%
    "min_base_len": 15,        # ~3 weeks, Minervini's shortest VCP
    "max_pivot_below_base_pct": 15.0,  # right side must recover most of the left side
    "dryup_short": 10,         # volume dry-up: last 10 sessions median turnover ...
    "dryup_long": 50,          # ... vs 50-session median (O'Neil's 50-day average volume)
    # Execution state
    "pre_breakout_pct": 5.0,   # within 5% below the pivot = actionable watch zone
    "breakout_max_pct": 5.0,   # O'Neil: do not chase more than 5% above the pivot
    "breakout_vol_mult": 1.4,  # O'Neil: breakout volume at least 40-50% above average
    "extended_sma50_pct": 25.0,  # > 25% above SMA50 = extended (mean-reversion risk)
    # Risk plan
    "stop_buffer_pct": 0.5,    # stop a bit below the contraction low, not exactly on it
    "atr_mult": 2.0,           # same default as gpw-position-sizer --atr-multiplier
    "max_risk_pct": 8.0,       # O'Neil 7-8% loss rule; Minervini: average loss far below 10%
}

QUARTER = 63  # sessions per quarter (252 / 4)

TEMPLATE_KEYS = [
    "close_above_sma150_sma200",
    "sma150_above_sma200",
    "sma200_rising",
    "sma50_above_sma150_sma200",
    "close_above_sma50",
    "above_52w_low",
    "near_52w_high",
    "rs_rating",
]

WEIGHTS = {  # composite score weights (sum = 100)
    "trend": 25,
    "rs": 25,
    "contraction": 20,
    "dryup": 10,
    "pivot": 10,
    "liquidity": 10,
}

BANDS = [(90, "textbook"), (80, "strong"), (70, "good"), (60, "watchlist"), (0, "skip")]

ACTIONABLE = ("pre_breakout", "breakout")


# ---------------------------------------------------------------------------
# Per-stock precomputation
# ---------------------------------------------------------------------------
class StockData:
    __slots__ = ("name", "dates", "close", "high", "low", "turn", "pos", "sma50", "sma150", "sma200")

    def __init__(self, s):
        self.name = s.name
        self.dates = s.dates
        self.close = s.close
        self.high = s.high
        self.low = s.low
        self.turn = s.turnover_k
        self.pos = {d: i for i, d in enumerate(s.dates)}
        self.sma50 = sma(s.close, 50)
        self.sma150 = sma(s.close, 150)
        self.sma200 = sma(s.close, 200)

    def __len__(self):
        return len(self.dates)

    def index_at(self, date: str) -> Optional[int]:
        """Observation index on ``date`` or None if the stock did not trade that day."""
        return self.pos.get(date)

    def index_on_or_before(self, date: str) -> Optional[int]:
        k = bisect_right(self.dates, date) - 1
        return k if k >= 0 else None


def prepare(panel: Dict) -> Dict[str, StockData]:
    return {name: StockData(s) for name, s in panel.items()}


class RefSeries:
    """Benchmark (WIG) closes with as-of lookup by date."""

    def __init__(self, dates: List[str], closes: List[float]):
        self.dates = list(dates)
        self.closes = list(closes)

    def close_on_or_before(self, date: str) -> Optional[float]:
        k = bisect_right(self.dates, date) - 1
        return self.closes[k] if k >= 0 else None

    def ret(self, d0: str, d1: str) -> Optional[float]:
        a, b = self.close_on_or_before(d0), self.close_on_or_before(d1)
        if a is None or b is None or a <= 0 or self.dates[0] > d0:
            return None
        return (b / a - 1) * 100.0


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------
def med_turnover(sd: StockData, i: int, n: int) -> Optional[float]:
    if i + 1 < n:
        return None
    return median(sd.turn[i - n + 1 : i + 1])


def prefilter(sd: StockData, i: int, cfg: Dict) -> Dict:
    reasons = []
    hist = i + 1
    if hist < cfg["min_history"]:
        reasons.append("history")
    if sd.close[i] < cfg["min_price"]:
        reasons.append("price")
    mt = med_turnover(sd, i, 20)
    if mt is None or mt < cfg["min_turnover_k"]:
        reasons.append("liquidity")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "history": hist,
        "short_history": hist < cfg["full_history"],
        "med_turnover20_k": mt,
    }


def rs_raw(close: List[float], i: int) -> Tuple[Optional[float], int]:
    """IBD-style weighted 12-month performance: 2*Q1 + Q2 + Q3 + Q4 (in %).

    Q1 is the most recent 63-session quarter. With 2-3 complete quarters the
    available ones are used and rescaled to the same total weight (5), so a
    recent IPO is not ranked artificially low or high. Returns (value, quarters).
    """
    qs = []
    for q in range(4):
        a, b = i - (q + 1) * QUARTER, i - q * QUARTER
        if a < 0:
            break
        if close[a] <= 0:
            return None, 0
        qs.append((close[b] / close[a] - 1) * 100.0)
    if len(qs) < 2:
        return None, len(qs)
    w = [2.0] + [1.0] * (len(qs) - 1)
    raw = sum(wi * qi for wi, qi in zip(w, qs)) / sum(w) * 5.0
    return raw, len(qs)


def rs_ratings(raws: Dict[str, float]) -> Dict[str, int]:
    """Percentile rank of each raw value in the universe, mapped to 1-99 (IBD scale)."""
    vals = sorted(raws.values())
    n = len(vals)
    out = {}
    for name, v in raws.items():
        below = bisect_left(vals, v)
        equal = bisect_right(vals, v) - below
        out[name] = rank_to_rating(100.0 * (below + 0.5 * equal) / n) if n else None
    return out


def rank_to_rating(pr: float) -> int:
    return int(max(1, min(99, round(pr))))


def rating_against(vals_sorted: List[float], v: float) -> Optional[int]:
    """RS rating of a value against an (external) sorted universe of raws."""
    if not vals_sorted or v is None:
        return None
    below = bisect_left(vals_sorted, v)
    equal = bisect_right(vals_sorted, v) - below
    return rank_to_rating(100.0 * (below + 0.5 * equal) / len(vals_sorted))


def rs_vs_wig(sd: StockData, i: int, wig: Optional[RefSeries], periods=(63, 126, 252)) -> Dict[str, Optional[float]]:
    """Stock return minus WIG return (percentage points) over the same dates."""
    out = {}
    for n in periods:
        key = str(n)
        if wig is None or i - n < 0:
            out[key] = None
            continue
        d0, d1 = sd.dates[i - n], sd.dates[i]
        r_s = (sd.close[i] / sd.close[i - n] - 1) * 100.0
        r_w = wig.ret(d0, d1)
        out[key] = None if r_w is None else r_s - r_w
    return out


def high_low_52w(sd: StockData, i: int, n: int = 252) -> Tuple[float, float, int]:
    """Intraday 52-week high and low (adjusted), fewer sessions if history is shorter."""
    a = max(0, i - n + 1)
    return max(sd.high[a : i + 1]), min(sd.low[a : i + 1]), i + 1 - a


def trend_template(sd: StockData, i: int, rs_rating: Optional[int], cfg: Dict) -> Dict:
    c = sd.close[i]
    s50, s150, s200 = sd.sma50[i], sd.sma150[i], sd.sma200[i]
    lag = cfg["sma200_slope_lag"]
    s200_prev = sd.sma200[i - lag] if i - lag >= 0 else None
    if None in (s50, s150, s200) or s200_prev is None:
        return {"available": False, "criteria": [], "passed": 0, "total": len(TEMPLATE_KEYS)}
    hi52, lo52, n52 = high_low_52w(sd, i)
    vals = {
        "close_above_sma150_sma200": (c > s150 and c > s200, c, max(s150, s200)),
        "sma150_above_sma200": (s150 > s200, s150, s200),
        "sma200_rising": (s200 > s200_prev, s200, s200_prev),
        "sma50_above_sma150_sma200": (s50 > s150 and s50 > s200, s50, max(s150, s200)),
        "close_above_sma50": (c > s50, c, s50),
        "above_52w_low": (c >= cfg["above_low_mult"] * lo52, c, cfg["above_low_mult"] * lo52),
        "near_52w_high": (c >= cfg["near_high_mult"] * hi52, c, cfg["near_high_mult"] * hi52),
        "rs_rating": (rs_rating is not None and rs_rating >= cfg["min_rs"], rs_rating, cfg["min_rs"]),
    }
    crit = [{"key": k, "pass": bool(vals[k][0]), "value": _r(vals[k][1]), "threshold": _r(vals[k][2])}
            for k in TEMPLATE_KEYS]
    return {
        "available": True,
        "criteria": crit,
        "passed": sum(1 for x in crit if x["pass"]),
        "total": len(crit),
        "sma50": _r(s50), "sma150": _r(s150), "sma200": _r(s200),
        "sma200_slope_pct": _r((s200 / s200_prev - 1) * 100.0, 3),
        "high_52w": _r(hi52), "low_52w": _r(lo52), "window_52w": n52,
        "pct_from_high": _r((c / hi52 - 1) * 100.0),
        "pct_above_low": _r((c / lo52 - 1) * 100.0),
        "pct_above_sma50": _r((c / s50 - 1) * 100.0),
    }


# ---------------------------------------------------------------------------
# VCP detection
# ---------------------------------------------------------------------------
def swing_points(high: List[float], low: List[float], start: int, end: int, left: int, right: int) -> List[Tuple[int, str, float]]:
    """Fractal swing highs/lows confirmed by ``end`` (inclusive).

    Bar k is a swing high if high[k] is strictly above the previous ``left``
    highs and not below the next ``right`` highs (first bar of a plateau wins);
    swing lows mirror this. A bar that is both (outside bar) counts as a high.
    The result is reduced to an alternating H/L zigzag: of two consecutive
    highs the higher survives, of two consecutive lows the lower.
    """
    pts: List[Tuple[int, str, float]] = []
    for k in range(max(start, left), end - right + 1):
        h, lw = high[k], low[k]
        is_h = all(high[j] < h for j in range(k - left, k)) and all(high[j] <= h for j in range(k + 1, k + right + 1))
        is_l = all(low[j] > lw for j in range(k - left, k)) and all(low[j] >= lw for j in range(k + 1, k + right + 1))
        if is_h:
            pts.append((k, "H", h))
        elif is_l:
            pts.append((k, "L", lw))
    zz: List[Tuple[int, str, float]] = []
    for p in pts:
        if zz and zz[-1][1] == p[1]:
            if (p[1] == "H" and p[2] > zz[-1][2]) or (p[1] == "L" and p[2] < zz[-1][2]):
                zz[-1] = p
        else:
            zz.append(p)
    return zz


def _contractions(sd: StockData, highs: List[int], i: int) -> List[Dict]:
    out = []
    for n, hk in enumerate(highs):
        end = highs[n + 1] if n + 1 < len(highs) else i + 1
        seg = range(hk + 1, end)
        if not seg:
            continue
        lk = min(seg, key=lambda j: sd.low[j])
        h, lw = sd.high[hk], sd.low[lk]
        out.append({
            "high_idx": hk, "high_date": sd.dates[hk], "high": _r(h),
            "low_idx": lk, "low_date": sd.dates[lk], "low": _r(lw),
            "depth_pct": _r((h - lw) / h * 100.0),
            "_h": h, "_l": lw,
        })
    return out


def merge_noise(sd: StockData, hidx: List[int], i: int, min_depth: float) -> List[Dict]:
    """Drop dips shallower than ``min_depth`` % (except the latest, still-forming one).

    A tiny dip between two swing highs is part of one move: of the two highs
    around it the lower one is removed (the later one on a tie), so the
    contraction keeps the true local top as its starting point.
    """
    hidx = list(hidx)
    cons = _contractions(sd, hidx, i)
    while True:
        tiny = next((n for n, c in enumerate(cons[:-1]) if c["depth_pct"] < min_depth), None)
        if tiny is None or tiny + 1 >= len(hidx):
            return cons
        a, b = hidx[tiny], hidx[tiny + 1]
        hidx.remove(b if sd.high[b] <= sd.high[a] else a)
        cons = _contractions(sd, hidx, i)


def detect_vcp(sd: StockData, i: int, cfg: Dict) -> Dict:
    """Detect a volatility contraction pattern ending at observation i.

    1. Swing highs/lows (fractals, see ``swing_points``) in the last
       ``vcp_lookback`` sessions, confirmed with data up to i only.
    2. The base starts at the highest swing high of the window; every later
       swing high starts a contraction whose low is the lowest low before the
       next swing high (the last one: lowest low up to today).
    3. Contractions shallower than ``min_contraction_pct`` (except the last)
       are noise and merged into the neighbouring move.
    4. The pattern = longest run of successively shallower contractions that
       ends with the latest one (at most ``max_contractions``).
    5. Valid if 2+ contractions, first <= 40%, last <= 12%, base >= 15
       sessions and the pivot (high of the last contraction) within 15% of
       the base high.
    """
    lb = cfg["vcp_lookback"]
    start = max(0, i - lb + 1)
    res = {"valid": False, "reasons": [], "contractions": [], "pivot": None, "last_low": None,
           "base_high": None, "base_start": None, "base_len": None, "dryup_ratio": None,
           "higher_lows": None, "quality": 0.0}
    res["dryup_ratio"] = _dryup(sd, i, cfg)
    if i - start + 1 < cfg["min_base_len"] + cfg["swing_right"]:
        res["reasons"].append("short_window")
        return res
    zz = [p for p in swing_points(sd.high, sd.low, start, i, cfg["swing_left"], cfg["swing_right"]) if p[0] >= start]
    highs = [p for p in zz if p[1] == "H"]
    if not highs:
        res["reasons"].append("no_swing_high")
        return res
    base = max(highs, key=lambda p: (p[2], -p[0]))
    hidx = [p[0] for p in highs if p[0] >= base[0]]
    cons = merge_noise(sd, hidx, i, cfg["min_contraction_pct"])
    if not cons:
        res["reasons"].append("no_contraction")
        return res
    # longest run of strictly decreasing depths ending at the last contraction
    s = len(cons) - 1
    while s > 0 and cons[s - 1]["depth_pct"] > cons[s]["depth_pct"]:
        s -= 1
    seq = cons[s:][-cfg["max_contractions"]:]
    base_high = base[2]
    pivot = seq[-1]["_h"]
    last_low = seq[-1]["_l"]
    res.update({
        "contractions": [{k: v for k, v in c.items() if not k.startswith("_")} for c in seq],
        "all_contractions": len(cons),
        "pivot": _r(pivot), "pivot_date": seq[-1]["high_date"],
        "last_low": _r(last_low), "last_low_date": seq[-1]["low_date"],
        "base_high": _r(base_high), "base_start": sd.dates[seq[0]["high_idx"]],
        "base_len": i - seq[0]["high_idx"] + 1,
        "higher_lows": all(seq[k]["_l"] >= seq[k - 1]["_l"] for k in range(1, len(seq))),
    })
    reasons = []
    if len(seq) < cfg["min_contractions"]:
        reasons.append("too_few_contractions")
    if seq[0]["depth_pct"] > cfg["max_first_depth_pct"]:
        reasons.append("first_too_deep")
    if seq[-1]["depth_pct"] > cfg["max_last_depth_pct"]:
        reasons.append("last_too_loose")
    if res["base_len"] < cfg["min_base_len"]:
        reasons.append("base_too_short")
    if (base_high - pivot) / base_high * 100.0 > cfg["max_pivot_below_base_pct"]:
        reasons.append("pivot_far_below_base")
    res["reasons"] = reasons
    res["valid"] = not reasons
    res["quality"] = _r(contraction_quality(res), 1) if res["valid"] else 0.0
    return res


def _dryup(sd: StockData, i: int, cfg: Dict) -> Optional[float]:
    short = med_turnover(sd, i, cfg["dryup_short"])
    long_ = med_turnover(sd, i, cfg["dryup_long"])
    if short is None or not long_:
        return None
    return _r(short / long_, 3)


def contraction_quality(v: Dict) -> float:
    """0-100: more contractions, a tighter last one, strong progression, higher lows."""
    seq = v["contractions"]
    n = len(seq)
    if n < 2:
        return 0.0
    count = {2: 60.0, 3: 85.0}.get(n, 100.0)
    tight = piecewise(seq[-1]["depth_pct"], [(3, 100), (6, 90), (9, 70), (12, 45), (15, 0)])
    ratio = seq[-1]["depth_pct"] / seq[0]["depth_pct"] if seq[0]["depth_pct"] else 1.0
    prog = piecewise(ratio, [(0.2, 100), (0.4, 85), (0.6, 60), (0.9, 30), (1.0, 0)])
    hl = 100.0 if v.get("higher_lows") else 50.0
    return (count + tight + prog + hl) / 4.0


# ---------------------------------------------------------------------------
# Execution state, score, risk plan
# ---------------------------------------------------------------------------
def execution_state(sd: StockData, i: int, vcp: Dict, cfg: Dict) -> Dict:
    """Where the price is relative to the setup. Checked before the rating.

    Order: invalid (no SMA50) -> damaged -> extended -> invalid (no VCP) ->
    breakout / breakout_unconfirmed -> pre_breakout -> forming.
    """
    c = sd.close[i]
    s50 = sd.sma50[i]
    out = {"state": "invalid", "pct_vs_pivot": None, "pct_above_sma50": None, "volume_ratio": None}
    if s50 is None:
        return out
    out["pct_above_sma50"] = _r((c / s50 - 1) * 100.0)
    med50 = med_turnover(sd, i, 50)
    out["volume_ratio"] = _r(sd.turn[i] / med50, 2) if med50 else None
    pivot = vcp.get("pivot") if vcp else None
    if pivot:
        out["pct_vs_pivot"] = _r((c / pivot - 1) * 100.0)
    last_low = vcp.get("last_low") if vcp else None
    if c < s50 or (vcp and vcp.get("valid") and last_low is not None and c < last_low):
        out["state"] = "damaged"
        return out
    if out["pct_above_sma50"] > cfg["extended_sma50_pct"]:
        out["state"] = "extended"
        return out
    if not (vcp and vcp.get("valid")):
        out["state"] = "invalid"
        return out
    d = out["pct_vs_pivot"]
    if d > cfg["breakout_max_pct"]:
        out["state"] = "extended"
    elif d > 0:
        vr = out["volume_ratio"]
        out["state"] = "breakout" if vr is not None and vr >= cfg["breakout_vol_mult"] else "breakout_unconfirmed"
    elif d >= -cfg["pre_breakout_pct"]:
        out["state"] = "pre_breakout"
    else:
        out["state"] = "forming"
    return out


def score(tt: Dict, rs_rating: Optional[int], vcp: Dict, state: Dict, med20_k: Optional[float]) -> Dict:
    comps = {}
    if tt.get("available"):
        frac = tt["passed"] / tt["total"]
        near = piecewise(-(tt["pct_from_high"] or 0.0), [(0, 100), (5, 90), (15, 50), (25, 0)])
        comps["trend"] = 0.6 * frac * 100 + 0.4 * near
    else:
        comps["trend"] = 0.0
    comps["rs"] = piecewise(rs_rating or 0, [(50, 0), (70, 40), (80, 65), (90, 90), (99, 100)])
    comps["contraction"] = vcp.get("quality", 0.0) if vcp and vcp.get("valid") else 0.0
    dr = vcp.get("dryup_ratio") if vcp else None
    comps["dryup"] = piecewise(dr, [(0.5, 100), (0.7, 80), (1.0, 30), (1.3, 0)]) if dr is not None else 0.0
    d = state.get("pct_vs_pivot")
    comps["pivot"] = (piecewise(d, [(-12, 0), (-5, 60), (-2, 100), (0, 100), (2, 90), (5, 50), (10, 0)])
                      if d is not None and vcp and vcp.get("valid") else 0.0)
    comps["liquidity"] = piecewise(med20_k or 0.0, [(200, 30), (500, 60), (2000, 90), (5000, 100)])
    total = sum(WEIGHTS[k] * comps[k] / 100.0 for k in WEIGHTS)
    return {"score": _r(total, 1), "components": {k: _r(v, 1) for k, v in comps.items()}}


def band_for(score_value: float, state: str) -> Dict:
    """Rating band; the execution state caps it (watchlist priority, never a buy call)."""
    raw = next(name for lo, name in BANDS if score_value >= lo)
    capped = raw
    if state in ("damaged", "invalid"):
        capped = "skip"
    elif state in ("extended", "forming", "breakout_unconfirmed"):
        order = [b for _, b in BANDS]
        if order.index(raw) < order.index("watchlist"):
            capped = "watchlist"
    return {"band_raw": raw, "band": capped, "capped": capped != raw}


def risk_plan(sd: StockData, i: int, vcp: Dict, cfg: Dict) -> Dict:
    a0 = max(0, i - 120)
    a = atr(sd.high[a0 : i + 1], sd.low[a0 : i + 1], sd.close[a0 : i + 1], 14)
    out = {"atr14": _r(a), "entry_pivot": None, "stop_structure": None, "stop_atr": None,
           "risk_pct_structure": None, "risk_pct_atr": None, "wide_structure": None}
    if not vcp or not vcp.get("valid") or not vcp.get("pivot") or vcp.get("last_low") is None:
        return out  # no valid pattern -> no pivot to measure risk from
    pivot = vcp["pivot"]
    s1 = vcp["last_low"] * (1 - cfg["stop_buffer_pct"] / 100.0)
    out["entry_pivot"] = _r(pivot)
    out["stop_structure"] = _r(s1)
    out["risk_pct_structure"] = _r((pivot - s1) / pivot * 100.0)
    if a:
        s2 = pivot - cfg["atr_mult"] * a
        if s2 > 0:
            out["stop_atr"] = _r(s2)
            out["risk_pct_atr"] = _r((pivot - s2) / pivot * 100.0)
    out["wide_structure"] = out["risk_pct_structure"] > cfg["max_risk_pct"]
    return out


def sizer_command(name: str, rp: Dict, account_size: Optional[float], risk_pct: float, lang: str) -> Optional[str]:
    if not rp.get("entry_pivot") or not rp.get("stop_structure"):
        return None
    acct = "{:g}".format(account_size) if account_size else "ACCOUNT_PLN"
    return ("python3 scripts/position_sizer.py --ticker {} --entry {} --stop {} --account-size {} "
            "--risk-pct {:g} --lang {}".format(name, _fmt_price(rp["entry_pivot"]), _fmt_price(rp["stop_structure"]),
                                               acct, risk_pct, lang))


def _fmt_price(x: float) -> str:
    return "{:.2f}".format(x).rstrip("0").rstrip(".") if x < 1000 else "{:.0f}".format(x)


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------
def rs_universe(data: Dict[str, StockData], date: str, cfg: Dict) -> Tuple[Dict[str, Tuple[int, Dict]], Dict[str, float]]:
    """Eligible stocks on ``date`` -> (index, prefilter), and their RS raws."""
    elig, raws = {}, {}
    for name, sd in data.items():
        i = sd.index_at(date)
        if i is None:
            continue
        pf = prefilter(sd, i, cfg)
        if not pf["ok"]:
            continue
        raw, _q = rs_raw(sd.close, i)
        if raw is None:
            continue
        elig[name] = (i, pf)
        raws[name] = raw
    return elig, raws


def analyze(sd: StockData, i: int, rs_rating: Optional[int], wig: Optional[RefSeries], cfg: Dict,
            pf: Optional[Dict] = None, full: bool = True) -> Dict:
    pf = pf or prefilter(sd, i, cfg)
    tt = trend_template(sd, i, rs_rating, cfg)
    vcp = detect_vcp(sd, i, cfg) if (full or tt["passed"] >= cfg["min_criteria"]) else None
    st = execution_state(sd, i, vcp or {}, cfg)
    sc = score(tt, rs_rating, vcp or {}, st, pf["med_turnover20_k"])
    bd = band_for(sc["score"], st["state"])
    raw, q = rs_raw(sd.close, i)
    out = {
        "name": sd.name, "date": sd.dates[i], "close": _r(sd.close[i]),
        "prefilter": {k: (_r(v) if isinstance(v, float) else v) for k, v in pf.items()},
        "rs": {"rating": rs_rating, "raw": _r(raw), "quarters": q, "vs_wig_pp": {k: _r(v) for k, v in rs_vs_wig(sd, i, wig).items()}},
        "template": tt,
        "template_pass": tt["available"] and tt["passed"] >= cfg["min_criteria"],
        "vcp": vcp,
        "execution": st,
        "score": sc["score"], "components": sc["components"],
        "band": bd["band"], "band_raw": bd["band_raw"], "band_capped": bd["capped"],
        "risk": risk_plan(sd, i, vcp or {}, cfg),
    }
    return out


def screen_at(data: Dict[str, StockData], date: str, wig: Optional[RefSeries], cfg: Dict, full: bool = True) -> Dict:
    """Run the full screen as of ``date`` using data up to that date only."""
    elig, raws = rs_universe(data, date, cfg)
    ratings = rs_ratings(raws)
    cands = []
    for name, (i, pf) in elig.items():
        sd = data[name]
        tt = trend_template(sd, i, ratings[name], cfg)
        if not (tt["available"] and tt["passed"] >= cfg["min_criteria"]):
            continue
        cands.append(analyze(sd, i, ratings[name], wig, cfg, pf=pf, full=full))
    cands.sort(key=lambda r: (_BAND_ORDER[r["band"]], -r["score"], r["name"]))
    states: Dict[str, int] = {}
    for r in cands:
        states[r["execution"]["state"]] = states.get(r["execution"]["state"], 0) + 1
    return {
        "date": date,
        "universe": len(elig),
        "traded": sum(1 for sd in data.values() if sd.index_at(date) is not None),
        "template_passed": len(cands),
        "valid_vcp": sum(1 for r in cands if r["vcp"] and r["vcp"]["valid"]),
        "states": states,
        "candidates": cands,
        "rs_sorted": sorted(raws.values()),
        "ratings": ratings,
    }


_BAND_ORDER = {b: n for n, (_, b) in enumerate(BANDS)}


def is_flagged(r: Dict) -> bool:
    """Backtest 'signal': actionable state and at least the watchlist band."""
    return r["execution"]["state"] in ACTIONABLE and r["band"] != "skip"


# ---------------------------------------------------------------------------
# Backtest of the screen
# ---------------------------------------------------------------------------
def forward_return(sd: StockData, i: int, fwd_date: str) -> Tuple[Optional[float], bool]:
    """Return from close at i to the close on/before fwd_date; flag if the stock stopped trading."""
    k = sd.index_on_or_before(fwd_date)
    if k is None or k < i:
        return None, False
    stopped = sd.dates[k] != fwd_date and k == len(sd) - 1
    return (sd.close[k] / sd.close[i] - 1) * 100.0, stopped


def summarize(rets: List[Tuple[float, Optional[float]]]) -> Dict:
    """rets: list of (stock_return, wig_return) pairs."""
    if not rets:
        return {"n": 0}
    r = [a for a, _ in rets]
    ex = [a - b for a, b in rets if b is not None]
    return {
        "n": len(r),
        "hit_rate": _r(sum(1 for x in r if x > 0) / len(r) * 100.0, 1),
        "mean": _r(_mean(r)), "median": _r(median(r)),
        "mean_excess": _r(_mean(ex)) if ex else None,
        "median_excess": _r(median(ex)) if ex else None,
        "beat_wig_rate": _r(sum(1 for x in ex if x > 0) / len(ex) * 100.0, 1) if ex else None,
    }


def backtest(data: Dict[str, StockData], dates: List[str], wig: Optional[RefSeries], cfg: Dict,
             lookback: int = 250, step: int = 5, horizons=(20, 60)) -> Dict:
    """Run the screen every ``step`` sessions over the last ``lookback`` sessions
    (where at least the shortest forward horizon exists) and measure forward
    returns of flagged stocks, all template passers and the eligible universe vs WIG."""
    last = len(dates) - 1
    first = max(cfg["full_history"], last - lookback)
    hmin = min(horizons)
    points = list(range(first, last - hmin + 1, step))
    groups = {g: {h: [] for h in horizons} for g in ("flagged", "template", "universe")}
    per_date = []
    stopped = 0
    tickers = set()
    for t in points:
        d = dates[t]
        sc = screen_at(data, d, wig, cfg, full=False)
        flagged = [r for r in sc["candidates"] if is_flagged(r)]
        tickers.update(r["name"] for r in flagged)
        row = {"date": d, "universe": sc["universe"], "template": sc["template_passed"], "flagged": len(flagged),
               "flagged_names": [r["name"] for r in flagged][:10]}
        elig, _raws = rs_universe(data, d, cfg)
        cand_names = {r["name"] for r in sc["candidates"]}
        flag_names = {r["name"] for r in flagged}
        for h in horizons:
            if t + h > last:
                continue
            fd = dates[t + h]
            wr = wig.ret(d, fd) if wig else None
            for name, (i, _pf) in elig.items():
                fr, stp = forward_return(data[name], i, fd)
                if fr is None:
                    continue
                groups["universe"][h].append((fr, wr))
                if name in cand_names:
                    groups["template"][h].append((fr, wr))
                if name in flag_names:
                    groups["flagged"][h].append((fr, wr))
                    stopped += 1 if stp else 0
        per_date.append(row)
    stats = {g: {str(h): summarize(groups[g][h]) for h in horizons} for g in groups}
    return {
        "screen_dates": len(points), "first_date": dates[points[0]] if points else None,
        "last_date": dates[points[-1]] if points else None, "step": step, "horizons": list(horizons),
        "stats": stats, "distinct_flagged_tickers": len(tickers), "flagged_stopped_trading": stopped,
        "per_date": per_date,
    }


# ---------------------------------------------------------------------------
# Market regime (contract: gpw-market-regime JSON)
# ---------------------------------------------------------------------------
REGIME_STATES = ("confirmed_uptrend", "uptrend_under_pressure", "rally_attempt", "correction", "bear")


def parse_regime(doc: Dict) -> Optional[Dict]:
    if not isinstance(doc, dict) or doc.get("skill") != "gpw-market-regime":
        return None
    reg = doc.get("regime") or {}
    state = reg.get("state")
    if state not in REGIME_STATES:
        return None
    level = {"correction": "high", "bear": "high", "uptrend_under_pressure": "medium",
             "rally_attempt": "medium"}.get(state, "none")
    return {"state": state, "as_of": reg.get("as_of") or doc.get("as_of"),
            "top_risk_score": reg.get("top_risk_score"), "distribution_days": reg.get("distribution_days"),
            "warning_level": level}


def sessions_between(dates: List[str], older: str, newer: str) -> int:
    """Number of sessions in ``dates`` after ``older`` up to and including ``newer``."""
    return max(0, bisect_right(dates, newer) - bisect_right(dates, older))


# ---------------------------------------------------------------------------
def _r(x, d: int = 2):
    if isinstance(x, float):
        return round(x, d)
    return x
