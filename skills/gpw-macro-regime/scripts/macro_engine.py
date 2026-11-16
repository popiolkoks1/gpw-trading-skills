"""gpw-macro-regime engine: deterministic macro state rules (pure, no I/O).

Inputs are plain series (see macro_sources: lists of ``(key, value)`` sorted
by key; daily keys are ``datetime.date``, monthly keys ``"YYYY-MM"``).
Every threshold is explained next to its constant and in
references/methodology.md. Nothing here forecasts: the states describe the
last few months, the sector tilts are historical tendencies.
"""

from __future__ import annotations

import bisect
import datetime as dt
import math
from typing import Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# Thresholds
# --------------------------------------------------------------------------

# Monetary cycle. The MPC (RPP) meets about 11 times a year. A move within
# the last 120 days (~4 meetings) means the cycle is active; after that, three
# or more "no change" decisions in a row read as a pause ("on hold").
HOLD_AFTER_DAYS = 120
# Real (ex-post) rate = NBP reference rate - HICP y/y. Above +2 pp policy is
# clearly restrictive (the neutral real rate for Poland is usually estimated
# at 0-2 %, see methodology); below 0 it is accommodative.
REAL_RATE_RESTRICTIVE = 2.0

# Inflation: the NBP target is 2.5 % CPI with a +/-1 pp tolerance band
# (NBP, "Założenia polityki pieniężnej"). A 3-month change of at least 0.3 pp
# counts as a trend: Eurostat rounds to 0.1 pp and month-to-month noise in
# the y/y rate is usually 0.1-0.2 pp.
INFLATION_TARGET = 2.5
INFLATION_BAND = 1.0
INFLATION_TREND_PP = 0.3

# PLN: the pair must be more than 1 % away from its 200-fixing average and
# have moved the same way over 3 months. EURPLN annualised volatility is
# typically 3-6 %, i.e. ~1-1.7 % a month, so 1 % filters out ordinary noise
# while still reacting within weeks. EUR weighs 2/3: the euro area is
# Poland's main trading partner (over half of exports; GUS foreign trade).
FX_BAND = 0.01
FX_SMA = 200
FX_WEIGHTS = {"EUR": 2.0 / 3.0, "USD": 1.0 / 3.0}
FX_REGIME_CUTOFF = 0.5
# FX volatility: 63-fixing (~3 months) annualised volatility above 1.5x the
# 252-fixing (~1 year) value = "elevated" (a doubling of variance).
FX_VOL_ELEVATED = 1.5

# Spread and curve: a 3-month change of 15 bp or more is a trend. The monthly
# PL-DE 10Y spread moves ~10-20 bp per month in normal times, so 15 bp is
# about one month's typical move.
SPREAD_TREND_BP = 15.0
CURVE_TREND_BP = 15.0
# Curve shape (10Y - 2Y). An inverted curve has preceded most recessions
# (Estrella & Mishkin 1998); 50 bp / 150 bp separate flat / normal / steep.
CURVE_FLAT_BP = 50.0
CURVE_STEEP_BP = 150.0

# 10Y yield level: a 3-month change of 25 bp or more is a material move
# (larger than the 15 bp spread threshold because the outright yield also
# carries the global (Bund) component).
YIELD_TREND_BP = 25.0

LOOKBACK_3M = 91
LOOKBACK_6M = 182
LOOKBACK_12M = 365

# Publication lags for point-in-time history (days from the 1st of the month
# of the observation until it is normally public): Eurostat HICP ~mid M+1,
# ECB long-term rates in the first days of M+1.
PUB_LAG_DAYS = {"hicp": 46, "ecb": 40}

# Composite: weighted mean of component scores in [-1, 1]. Monetary policy
# leads (discount rate and credit), inflation and the risk premium next, PLN
# last. >= +0.25 supportive, <= -0.25 headwind: a single component at full
# strength (weight 0.20-0.30) alone tips the balance only if nothing offsets it.
WEIGHTS = {"monetary": 0.30, "inflation": 0.25, "spread": 0.25, "pln": 0.20}
COMPOSITE_CUTOFF = 0.25
CURVE_INVERTED_PENALTY = 0.10

MONETARY_SCORE = {"easing": 1.0, "hold_after_easing": 0.5, "hold_after_tightening": -0.5, "tightening": -1.0}
# inflation score by (trend, band)
INFLATION_SCORE = {
    ("falling", "above"): 0.5, ("falling", "within"): 1.0, ("falling", "below"): 0.5,
    ("stable", "above"): -0.5, ("stable", "within"): 0.5, ("stable", "below"): 0.0,
    ("rising", "above"): -1.0, ("rising", "within"): -0.5, ("rising", "below"): 0.0,
}
SPREAD_SCORE = {"narrowing": 1.0, "stable": 0.0, "widening": -1.0}
PLN_SCORE = {"strong": 1.0, "neutral": 0.0, "weak": -1.0}

TILT_POSITIVE = 0.5
TILT_SLIGHT = 0.25


# --------------------------------------------------------------------------
# Series helpers
# --------------------------------------------------------------------------

def upto(series: Sequence[Tuple], key) -> List[Tuple]:
    keys = [k for k, _ in series]
    return list(series[:bisect.bisect_right(keys, key)])


def value_on(series: Sequence[Tuple], key) -> Optional[Tuple]:
    """Last (key, value) with key <= ``key``."""
    keys = [k for k, _ in series]
    i = bisect.bisect_right(keys, key)
    return series[i - 1] if i else None


def month_add(key: str, n: int) -> str:
    y, m = int(key[:4]), int(key[5:7])
    t = y * 12 + (m - 1) + n
    return "{:04d}-{:02d}".format(t // 12, t % 12 + 1)


def month_key(d: dt.date) -> str:
    return "{:04d}-{:02d}".format(d.year, d.month)


def month_start(key: str) -> dt.date:
    return dt.date(int(key[:4]), int(key[5:7]), 1)


def month_end(key: str) -> dt.date:
    return month_start(month_add(key, 1)) - dt.timedelta(days=1)


def published(series: Sequence[Tuple[str, float]], as_of: dt.date, lag_days: int) -> List[Tuple[str, float]]:
    """Monthly observations that were public on ``as_of`` (point in time)."""
    return [(k, v) for k, v in series if month_start(k) + dt.timedelta(days=lag_days) <= as_of]


def monthly_mean(daily: Sequence[Tuple[dt.date, float]]) -> List[Tuple[str, float]]:
    acc: Dict[str, List[float]] = {}
    for d, v in daily:
        acc.setdefault(month_key(d), []).append(v)
    return sorted((k, sum(v) / len(v)) for k, v in acc.items())


def _r(x: Optional[float], n: int = 4) -> Optional[float]:
    return None if x is None else round(x, n)


def _pct_change(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b in (None, 0):
        return None
    return (a / b - 1) * 100


def _vol(values: Sequence[float], n: int) -> Optional[float]:
    vals = list(values[-(n + 1):])
    if len(vals) < n + 1:
        return None
    rets = [math.log(vals[i] / vals[i - 1]) for i in range(1, len(vals)) if vals[i - 1] > 0 and vals[i] > 0]
    if len(rets) < 2:
        return None
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252) * 100


# --------------------------------------------------------------------------
# Monetary policy
# --------------------------------------------------------------------------

def monetary_state(changes: Sequence[Tuple[dt.date, float]], as_of: dt.date) -> Optional[Dict]:
    """NBP reference-rate change points -> cycle state on ``as_of``."""
    pts = upto(changes, as_of)
    if len(pts) < 2:
        return None
    moves = [(pts[i][0], round(pts[i][1] - pts[i - 1][1], 4)) for i in range(1, len(pts))]
    last_d, last_mv = moves[-1]
    prev = moves[-2] if len(moves) > 1 else None
    cut = last_mv < 0
    days = (as_of - last_d).days
    if days <= HOLD_AFTER_DAYS:
        state = "easing" if cut else "tightening"
    else:
        state = "hold_after_easing" if cut else "hold_after_tightening"
    # the current cycle = consecutive moves in the same direction
    cycle = [moves[-1]]
    for mv in reversed(moves[:-1]):
        if (mv[1] < 0) == cut:
            cycle.insert(0, mv)
        else:
            break
    rate = pts[-1][1]
    year_ago = value_on(changes, as_of - dt.timedelta(days=LOOKBACK_12M))
    chg_12m = rate - year_ago[1] if year_ago else None
    return {
        "state": state,
        "simple": "easing" if state == "easing" else ("tightening" if state == "tightening" else "neutral"),
        "rate": rate,
        "last_change_date": last_d.isoformat(),
        "last_move_bp": round(last_mv * 100),
        "previous_move_bp": round(prev[1] * 100) if prev else None,
        "previous_move_date": prev[0].isoformat() if prev else None,
        "last_two_moves": [("cut" if m[1] < 0 else "hike") for m in moves[-2:]],
        "turn": bool(prev and (prev[1] < 0) != cut),
        "days_since_last_change": days,
        "cycle_start": cycle[0][0].isoformat(),
        "cycle_moves": len(cycle),
        "cycle_total_bp": round(sum(m[1] for m in cycle) * 100),
        "change_12m_bp": round(chg_12m * 100) if chg_12m is not None else None,
        "trend": "falling" if (chg_12m or 0) < 0 else ("rising" if (chg_12m or 0) > 0 else "flat"),
    }


# --------------------------------------------------------------------------
# Inflation and real rate
# --------------------------------------------------------------------------

def inflation_band(v: float) -> str:
    if v > INFLATION_TARGET + INFLATION_BAND:
        return "above"
    if v < INFLATION_TARGET - INFLATION_BAND:
        return "below"
    return "within"


def inflation_state(yoy: Sequence[Tuple[str, float]], as_of: dt.date, point_in_time: bool = False) -> Optional[Dict]:
    pts = published(yoy, as_of, PUB_LAG_DAYS["hicp"]) if point_in_time else upto(yoy, month_key(as_of))
    if not pts:
        return None
    key, val = pts[-1]
    earlier = value_on(pts, month_add(key, -3))
    if earlier is None or earlier[0] != month_add(key, -3):
        earlier = None
    change = round(val - earlier[1], 4) if earlier else None  # avoid 3.7 - 4.0 = -0.2999...
    if change is None:
        trend = None
    elif change <= -INFLATION_TREND_PP:
        trend = "falling"
    elif change >= INFLATION_TREND_PP:
        trend = "rising"
    else:
        trend = "stable"
    peak12 = max(v for k, v in pts if k > month_add(key, -12))
    return {"value": val, "period": key, "change_3m_pp": _r(change, 2), "trend": trend,
            "band": inflation_band(val), "target": INFLATION_TARGET, "band_pp": INFLATION_BAND,
            "max_12m": peak12, "history_12m": [{"period": k, "value": v} for k, v in pts[-12:]]}


def real_rate_state(rate: Optional[float], cpi: Optional[float]) -> Optional[Dict]:
    if rate is None or cpi is None:
        return None
    rr = rate - cpi
    if rr > REAL_RATE_RESTRICTIVE:
        stance = "restrictive"
    elif rr < 0:
        stance = "accommodative"
    else:
        stance = "moderate"
    return {"value": round(rr, 2), "sign": "positive" if rr > 0 else ("negative" if rr < 0 else "zero"),
            "stance": stance}


# --------------------------------------------------------------------------
# PLN
# --------------------------------------------------------------------------

def fx_metrics(series: Sequence[Tuple[dt.date, float]], as_of: dt.date) -> Optional[Dict]:
    pts = upto(series, as_of)
    if len(pts) < FX_SMA:
        return None
    vals = [v for _, v in pts]
    last_d, last = pts[-1]
    sma = sum(vals[-FX_SMA:]) / FX_SMA
    dev = (last / sma - 1)

    def back(days):
        p = value_on(pts, as_of - dt.timedelta(days=days))
        return p[1] if p else None

    c3, c6, c12 = _pct_change(last, back(LOOKBACK_3M)), _pct_change(last, back(LOOKBACK_6M)), _pct_change(last, back(LOOKBACK_12M))
    if dev < -FX_BAND and (c3 or 0) < 0:
        signal = "strong"  # PLN strong = the pair is falling
    elif dev > FX_BAND and (c3 or 0) > 0:
        signal = "weak"
    else:
        signal = "neutral"
    v63, v252 = _vol(vals, 63), _vol(vals, 252)
    return {"value": last, "as_of": last_d.isoformat(), "sma200": _r(sma, 4), "dev_sma200_pct": _r(dev * 100, 2),
            "change_3m_pct": _r(c3, 2), "change_6m_pct": _r(c6, 2), "change_12m_pct": _r(c12, 2),
            "vol_3m_ann_pct": _r(v63, 2), "vol_1y_ann_pct": _r(v252, 2),
            "vol_elevated": bool(v63 and v252 and v63 > FX_VOL_ELEVATED * v252),
            "pln_signal": signal,
            "trend": "rising" if (c3 or 0) > 0 else ("falling" if (c3 or 0) < 0 else "flat")}


def pln_state(eur: Optional[Dict], usd: Optional[Dict]) -> Optional[Dict]:
    """Weighted PLN regime from per-pair signals (strong = -1, weak = +1)."""
    pairs = {"EUR": eur, "USD": usd}
    num = den = 0.0
    for ccy, m in pairs.items():
        if m is None:
            continue
        s = {"strong": -1.0, "neutral": 0.0, "weak": 1.0}[m["pln_signal"]]
        num += FX_WEIGHTS[ccy] * s
        den += FX_WEIGHTS[ccy]
    if den == 0:
        return None
    score = num / den
    regime = "weak" if score >= FX_REGIME_CUTOFF else ("strong" if score <= -FX_REGIME_CUTOFF else "neutral")
    return {"regime": regime, "score": round(score, 3), "pairs_used": [c for c, m in pairs.items() if m],
            "vol_elevated": any(m and m["vol_elevated"] for m in pairs.values())}


# --------------------------------------------------------------------------
# Spread and curve
# --------------------------------------------------------------------------

def classify_change(change_bp: Optional[float], threshold: float, down: str, up: str) -> Optional[str]:
    if change_bp is None:
        return None
    change_bp = round(change_bp, 6)  # float noise must not decide a boundary case
    if change_bp <= -threshold:
        return down
    if change_bp >= threshold:
        return up
    return "stable"


def spread_monthly(pl10: Sequence[Tuple[str, float]], bund_daily: Sequence[Tuple[dt.date, float]]) -> List[Tuple[str, float]]:
    """PL 10Y (ECB monthly average) minus Bund 10Y monthly average, in bp."""
    bund_m = dict(monthly_mean(bund_daily))
    return [(k, round((v - bund_m[k]) * 100, 1)) for k, v in pl10 if k in bund_m]


def spread_state_daily(pl_now: Optional[Tuple[dt.date, float]], pl_then: Optional[Tuple[dt.date, float]],
                       bund_daily: Sequence[Tuple[dt.date, float]]) -> Optional[Dict]:
    """PL-DE spread from two BondSpot 10Y fixings and the Bund on the same days."""
    if not pl_now or not pl_then:
        return None
    b_now, b_then = value_on(bund_daily, pl_now[0]), value_on(bund_daily, pl_then[0])
    if not b_now or not b_then or (pl_now[0] - b_now[0]).days > 5 or (pl_then[0] - b_then[0]).days > 5:
        return None
    now = (pl_now[1] - b_now[1]) * 100
    then = (pl_then[1] - b_then[1]) * 100
    ch = now - then
    return {"value_bp": round(now, 1), "as_of": pl_now[0].isoformat(), "previous_bp": round(then, 1),
            "previous_as_of": pl_then[0].isoformat(), "change_3m_bp": round(ch, 1),
            "trend": classify_change(ch, SPREAD_TREND_BP, "narrowing", "widening"), "basis": "daily"}


def spread_state_monthly(spread_m: Sequence[Tuple[str, float]], as_of: dt.date, point_in_time: bool = False) -> Optional[Dict]:
    pts = published(spread_m, as_of, PUB_LAG_DAYS["ecb"]) if point_in_time else upto(spread_m, month_key(as_of))
    if len(pts) < 4:
        return None
    key, now = pts[-1]
    then = dict(pts).get(month_add(key, -3))
    if then is None:
        return None
    ch = now - then
    return {"value_bp": now, "as_of": key, "previous_bp": then, "previous_as_of": month_add(key, -3),
            "change_3m_bp": round(ch, 1), "trend": classify_change(ch, SPREAD_TREND_BP, "narrowing", "widening"),
            "basis": "monthly"}


def curve_shape(slope_bp: float) -> str:
    if slope_bp < 0:
        return "inverted"
    if slope_bp < CURVE_FLAT_BP:
        return "flat"
    if slope_bp <= CURVE_STEEP_BP:
        return "normal"
    return "steep"


def curve_state(now: Optional[Dict], then: Optional[Dict]) -> Optional[Dict]:
    """``now``/``then`` = {"date", "y2": bond, "y10": bond} from BondSpot."""
    if not now or not now.get("y2") or not now.get("y10"):
        return None
    slope = (now["y10"]["yield"] - now["y2"]["yield"]) * 100
    out = {"value_bp": round(slope, 1), "as_of": now["date"].isoformat(), "shape": curve_shape(slope),
           "y2": now["y2"]["yield"], "y2_bond": now["y2"]["name"], "y10": now["y10"]["yield"], "y10_bond": now["y10"]["name"],
           "change_3m_bp": None, "trend": None}
    if then and then.get("y2") and then.get("y10"):
        prev = (then["y10"]["yield"] - then["y2"]["yield"]) * 100
        ch = slope - prev
        out.update({"previous_bp": round(prev, 1), "previous_as_of": then["date"].isoformat(),
                    "change_3m_bp": round(ch, 1),
                    "trend": classify_change(ch, CURVE_TREND_BP, "flattening", "steepening")})
    return out


# --------------------------------------------------------------------------
# Composite
# --------------------------------------------------------------------------

def component_scores(mon: Optional[Dict], infl: Optional[Dict], real: Optional[Dict], pln: Optional[Dict],
                     spread: Optional[Dict], curve: Optional[Dict]) -> Dict[str, Optional[float]]:
    scores: Dict[str, Optional[float]] = {k: None for k in WEIGHTS}
    if mon:
        s = MONETARY_SCORE[mon["state"]]
        if real:  # restrictive / accommodative real rate shifts the monetary reading
            s += -0.25 if real["stance"] == "restrictive" else (0.25 if real["stance"] == "accommodative" else 0.0)
        scores["monetary"] = max(-1.0, min(1.0, s))
    if infl and infl.get("trend"):
        scores["inflation"] = INFLATION_SCORE[(infl["trend"], infl["band"])]
    if spread and spread.get("trend"):
        scores["spread"] = SPREAD_SCORE[spread["trend"]]
    if pln:
        scores["pln"] = PLN_SCORE[pln["regime"]]
    return scores


def composite(scores: Dict[str, Optional[float]], curve: Optional[Dict] = None) -> Dict:
    avail = {k: v for k, v in scores.items() if v is not None}
    if not avail:
        return {"score": None, "label": None, "components_used": [], "curve_penalty": 0.0}
    w = sum(WEIGHTS[k] for k in avail)
    score = sum(WEIGHTS[k] * v for k, v in avail.items()) / w
    penalty = CURVE_INVERTED_PENALTY if curve and curve.get("shape") == "inverted" else 0.0
    score -= penalty
    label = "supportive" if score >= COMPOSITE_CUTOFF else ("headwind" if score <= -COMPOSITE_CUTOFF else "mixed")
    return {"score": round(score, 3), "label": label, "components_used": sorted(avail), "curve_penalty": penalty}


def confidence(missing_components: Sequence[str], stale_sources: Sequence[str]) -> str:
    n = len(missing_components) + len(stale_sources)
    return "high" if n == 0 else ("medium" if n == 1 else "low")


def build_state(mon, infl, real, pln, spread, curve) -> Dict:
    scores = component_scores(mon, infl, real, pln, spread, curve)
    comp = composite(scores, curve)
    state = {
        "monetary": mon["state"] if mon else None,
        "monetary_simple": mon["simple"] if mon else None,
        "real_rate": real["sign"] if real else None,
        "pln": pln["regime"] if pln else None,
        "spread": spread["trend"] if spread else None,
        "curve": curve["shape"] if curve else None,
        "inflation": infl["trend"] if infl else None,
        "inflation_band": infl["band"] if infl else None,
        "composite": comp["label"],
    }
    if mon and pln:
        state["regime_label"] = "{}_x_pln_{}".format(mon["simple"], pln["regime"])
    return {"state": state, "scores": scores, "composite": comp}


# --------------------------------------------------------------------------
# Sector tilts (historical tendencies, not forecasts)
# --------------------------------------------------------------------------

SECTOR_GROUPS = {
    "banks": ["WIG-BANKI"],
    "exporters": ["WIG-INFO", "WIG-GRY", "WIG-GORNIC", "WIG-CHEMIA"],
    "importers_retail": ["WIG-ODZIEZ", "WIG-SPOZYW"],
    "utilities_energy": ["WIG-ENERG"],
    "developers": ["WIG-NRCHOM"],
}


def _tilt_label(s: float) -> str:
    if s >= TILT_POSITIVE:
        return "positive"
    if s >= TILT_SLIGHT:
        return "slightly_positive"
    if s <= -TILT_POSITIVE:
        return "negative"
    if s <= -TILT_SLIGHT:
        return "slightly_negative"
    return "neutral"


def sector_tilts(mon: Optional[Dict], infl: Optional[Dict], real: Optional[Dict], pln: Optional[Dict],
                 pl10_change_bp: Optional[float], perf: Optional[Dict[str, Dict]] = None) -> Dict[str, Dict]:
    """Score each sector group from the macro state. Drivers are listed so
    the report can explain every point; ``perf`` (index returns vs WIG) is a
    market check only and never changes the score."""
    ms = mon["state"] if mon else None
    # an active cycle counts fully; a pause keeps a quarter of the effect
    # (the level of rates still matters, the direction no longer adds to it)
    rate_dir = {"easing": 1.0, "hold_after_easing": 0.25, "hold_after_tightening": -0.25, "tightening": -1.0}.get(ms)
    pl = pln["regime"] if pln else None
    it = infl["trend"] if infl else None
    out: Dict[str, Dict] = {}

    def add(key, drivers):
        used = [d for d in drivers if d[1] is not None]
        score = max(-1.0, min(1.0, sum(d[1] for d in used))) if used else None
        out[key] = {"tilt": _tilt_label(score) if score is not None else "unknown", "score": _r(score, 2),
                    "drivers": [{"driver": d[0], "contribution": d[1]} for d in used],
                    "indices": SECTOR_GROUPS[key], "kind": "historical_tendency"}

    # Banks: Polish banks are asset-sensitive (mostly floating-rate WIBOR/
    # WIRON loans), so cuts compress net interest income.
    add("banks", [("monetary:" + str(ms), -rate_dir if rate_dir is not None else None)])
    # Exporters: revenue in EUR/USD, costs largely in PLN.
    add("exporters", [("pln:" + str(pl), {"weak": 1.0, "neutral": 0.0, "strong": -1.0}.get(pl))])
    # Importers / retail: a strong PLN lowers import costs; falling
    # inflation lifts real wages and consumption.
    add("importers_retail", [("pln:" + str(pl), {"weak": -0.5, "neutral": 0.0, "strong": 0.5}.get(pl)),
                             ("inflation:" + str(it), {"falling": 0.5, "stable": 0.0, "rising": -0.5}.get(it))])
    # Utilities: heavy capex and debt make them bond-like; regulation (URE
    # tariffs, windfall levies) usually dominates, so the tilt is capped at
    # "slightly". 25 bp in 3 months = a material move in the 10Y yield.
    y = classify_change(pl10_change_bp, YIELD_TREND_BP, "falling", "rising") if pl10_change_bp is not None else None
    util = {"falling": 0.25, "stable": 0.0, "rising": -0.25}.get(y) if y else None
    add("utilities_energy", [("pl10y_3m:" + str(y), util)])
    # Developers: mortgage affordability follows rates; a negative real rate
    # historically pushed savings into housing.
    add("developers", [("monetary:" + str(ms), rate_dir),
                       ("real_rate:" + (real["sign"] if real else "None"),
                        (0.25 if real["sign"] == "negative" else 0.0) if real else None)])
    for key, t in out.items():
        rel = [perf[i]["vs_wig_3m_pp"] for i in t["indices"] if perf and i in perf and perf[i].get("vs_wig_3m_pp") is not None]
        avg = sum(rel) / len(rel) if rel else None
        agrees = None
        if avg is not None and t["score"] is not None and abs(t["score"]) >= TILT_SLIGHT:
            agrees = (avg > 0) == (t["score"] > 0)
        t["market_check"] = {"vs_wig_3m_pp": _r(avg, 2), "agrees_with_tilt": agrees}
    return out


# --------------------------------------------------------------------------
# Equity side (GPW index cache)
# --------------------------------------------------------------------------

def index_perf(closes: Dict[str, Sequence[Tuple[dt.date, float]]], as_of: dt.date) -> Dict[str, Dict]:
    """3/6-month (63/126 sessions) returns of each index and vs WIG in pp."""
    out: Dict[str, Dict] = {}
    for name, ser in closes.items():
        pts = upto(ser, as_of)
        if len(pts) < 64:
            continue
        last = pts[-1][1]
        r3 = _pct_change(last, pts[-64][1])
        r6 = _pct_change(last, pts[-127][1]) if len(pts) >= 127 else None
        out[name] = {"as_of": pts[-1][0].isoformat(), "close": last, "ret_3m_pct": _r(r3, 2), "ret_6m_pct": _r(r6, 2)}
    wig = out.get("WIG")
    for name, o in out.items():
        o["vs_wig_3m_pp"] = _r(o["ret_3m_pct"] - wig["ret_3m_pct"], 2) if wig and o["ret_3m_pct"] is not None else None
        o["vs_wig_6m_pp"] = (_r(o["ret_6m_pct"] - wig["ret_6m_pct"], 2)
                             if wig and o["ret_6m_pct"] is not None and wig["ret_6m_pct"] is not None else None)
    return out


def wig_in_usd(wig: Sequence[Tuple[dt.date, float]], usd: Sequence[Tuple[dt.date, float]],
               as_of: dt.date) -> Optional[Dict]:
    """WIG divided by the USDPLN fixing of the same (or previous) day."""
    series = []
    for d, v in upto(wig, as_of):
        fx = value_on(usd, d)
        if fx and (d - fx[0]).days <= 5:
            series.append((d, v / fx[1]))
    if len(series) < 64:
        return None
    vals = [v for _, v in series]
    last_d, last = series[-1]

    def back(days):
        p = value_on(series, as_of - dt.timedelta(days=days))
        return p[1] if p else None

    sma = sum(vals[-200:]) / len(vals[-200:])
    c3 = _pct_change(last, back(LOOKBACK_3M))
    return {"value": _r(last, 2), "as_of": last_d.isoformat(), "change_3m_pct": _r(c3, 2),
            "change_6m_pct": _r(_pct_change(last, back(LOOKBACK_6M)), 2),
            "change_12m_pct": _r(_pct_change(last, back(LOOKBACK_12M)), 2) if (as_of - series[0][0]).days >= LOOKBACK_12M else None,
            "dev_sma200_pct": _r((last / sma - 1) * 100, 2), "sma_sessions": len(vals[-200:]),
            "trend": "rising" if (c3 or 0) > 0 else ("falling" if (c3 or 0) < 0 else "flat")}


# --------------------------------------------------------------------------
# History and changes
# --------------------------------------------------------------------------

def history(months: int, as_of: dt.date, rates, hicp, eur, usd, spread_m) -> List[Dict]:
    """Monthly composite for the last ``months`` month-ends (point in time:
    monthly data only once normally published). The curve needs BondSpot
    history and is left out of the monthly history."""
    rows = []
    key = month_key(as_of)
    for i in range(months - 1, -1, -1):
        k = month_add(key, -i)
        end = min(month_end(k), as_of)
        mon = monetary_state(rates, end) if rates else None
        infl = inflation_state(hicp, end, point_in_time=True) if hicp else None
        real = real_rate_state(mon["rate"] if mon else None, infl["value"] if infl else None)
        pln = pln_state(fx_metrics(eur, end) if eur else None, fx_metrics(usd, end) if usd else None)
        spr = spread_state_monthly(spread_m, end, point_in_time=True) if spread_m else None
        st = build_state(mon, infl, real, pln, spr, None)
        rows.append({"month": k, "date": end.isoformat(), "monetary": st["state"]["monetary"],
                     "pln": st["state"]["pln"], "spread": st["state"]["spread"],
                     "inflation": st["state"]["inflation"], "inflation_value": infl["value"] if infl else None,
                     "rate": mon["rate"] if mon else None, "composite": st["composite"]["label"],
                     "score": st["composite"]["score"]})
    return rows


DIFF_FIELDS = ("monetary", "real_rate", "pln", "spread", "curve", "inflation", "inflation_band", "composite")


def changes_since(prev: Optional[Dict], state: Dict, score: Optional[float]) -> Optional[Dict]:
    """Compare with the previous gpw-macro-regime report's ``macro`` block."""
    if not prev:
        return None
    pm = prev.get("macro") or {}
    ps = pm.get("state") or {}
    changes = [{"field": f, "from": ps.get(f), "to": state.get(f)} for f in DIFF_FIELDS
               if f in ps and ps.get(f) != state.get(f)]
    pscore = pm.get("score")
    return {"changes": changes, "previous_score": pscore,
            "score_change": _r(score - pscore, 3) if isinstance(pscore, (int, float)) and score is not None else None}


# --------------------------------------------------------------------------
# What to watch (deterministic checklist; the CLI renders the texts)
# --------------------------------------------------------------------------

def watch_items(mon, infl, real, pln, eur, usd, spread, curve) -> List[Dict]:
    items: List[Dict] = []
    if mon and infl and mon["state"] in ("easing", "hold_after_easing") and (infl["band"] == "above" or infl["trend"] == "rising"):
        items.append({"id": "hawkish_risk", "cpi": infl["value"], "rate": mon["rate"]})
    if mon and infl and mon["state"] in ("tightening", "hold_after_tightening") and infl["trend"] == "falling" \
            and infl["band"] != "above":
        items.append({"id": "easing_room", "cpi": infl["value"], "rate": mon["rate"]})
    if real and real["sign"] == "negative":
        items.append({"id": "negative_real_rate", "value": real["value"]})
    # an FX pair within 1.5x the band of its 200-fixing average can flip the
    # PLN regime with an ordinary month's move
    for pair, m in (("EURPLN", eur), ("USDPLN", usd)):
        if m and abs(m["dev_sma200_pct"]) <= FX_BAND * 100 * 1.5:
            items.append({"id": "fx_near_threshold", "pair": pair, "dev": m["dev_sma200_pct"], "sma": m["sma200"]})
    if pln and pln.get("vol_elevated"):
        items.append({"id": "fx_vol"})
    if spread and spread.get("trend") == "stable" and spread.get("change_3m_bp") is not None \
            and abs(spread["change_3m_bp"]) >= SPREAD_TREND_BP * 2 / 3:
        items.append({"id": "spread_near_threshold", "change": spread["change_3m_bp"]})
    if curve and curve["shape"] in ("inverted", "flat"):
        items.append({"id": "curve_" + curve["shape"], "value": curve["value_bp"]})
    if infl:
        items.append({"id": "next_cpi", "period": month_add(infl["period"], 1)})
    return items
