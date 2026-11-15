"""GPW market breadth: daily breadth series + 6-component composite score.

Adapted from tradermonty/claude-trading-skills ``market-breadth-analyzer``
(MIT). The upstream skill consumes a pre-computed S&P 500 breadth CSV; for the
Warsaw Stock Exchange no such dataset exists, so this module computes breadth
from raw daily quotes of every GPW share (see gpwlib.universe for the
universe definition). Methodology and every threshold are documented in
references/methodology.md.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from gpwlib.indicators import ema, pct_change, percentile_rank, piecewise
from gpwlib.panel import Series
from gpwlib.universe import StockView, eligible

FAST_SPAN = 8      # same as upstream: short-term breadth signal
SLOW_SPAN = 100    # upstream uses 200; GPW history built from the archive is shorter, 100 ≈ 5 months
TREND_LOOKBACK = 10
NHNL_LOOKBACK_FULL = 250
NHNL_LOOKBACK_MIN = 120

WEIGHTS = {
    "breadth_level_trend": 0.25,
    "ma_crossover": 0.20,
    "advance_decline": 0.15,
    "new_highs_lows": 0.15,
    "size_participation": 0.10,
    "index_divergence": 0.15,
}

ZONES = [  # (min_score, key, exposure_range)
    (80, "strong", "90–100%"),
    (60, "healthy", "75–90%"),
    (40, "neutral", "60–75%"),
    (20, "weakening", "40–60%"),
    (0, "critical", "25–40%"),
]


# --------------------------------------------------------------------------
# Daily series
# --------------------------------------------------------------------------

def daily_breadth(
    views: Dict[str, StockView],
    dates: List[str],
    cfg: Dict,
    size_map: Optional[Dict[str, str]] = None,
    nhnl_lookback: int = NHNL_LOOKBACK_FULL,
) -> List[Dict]:
    """One row per session with cross-sectional breadth statistics."""
    rows = []
    for d in dates:
        n = a200 = a50 = a20 = adv = dec = nh = nl = 0
        size_tot: Dict[str, int] = {}
        size_up: Dict[str, int] = {}
        for name, v in views.items():
            i = v.pos.get(d)
            if i is None or not eligible(v, i, cfg):
                continue
            c = v.s.close[i]
            n += 1
            above200 = c > v.sma200[i]
            a200 += above200
            a50 += v.sma50[i] is not None and c > v.sma50[i]
            a20 += v.sma20[i] is not None and c > v.sma20[i]
            if i > 0:
                prev = v.s.close[i - 1]
                if c > prev * 1.0005:
                    adv += 1
                elif c < prev * 0.9995:
                    dec += 1
            hi, lo = v.high_low(i, nhnl_lookback)
            if hi is not None:
                nh += c > hi
                nl += c < lo
            if size_map:
                grp = size_map.get(name)
                if grp:
                    size_tot[grp] = size_tot.get(grp, 0) + 1
                    size_up[grp] = size_up.get(grp, 0) + above200
        if n == 0:
            continue
        rows.append(
            {
                "date": d,
                "eligible": n,
                "pct_above_200": a200 / n,
                "pct_above_50": a50 / n,
                "pct_above_20": a20 / n,
                "advancers": adv,
                "decliners": dec,
                "new_highs": nh,
                "new_lows": nl,
                "size": {
                    g: {"n": size_tot[g], "pct_above_200": size_up[g] / size_tot[g]}
                    for g in size_tot
                },
            }
        )
    # derived series
    raw = [r["pct_above_200"] for r in rows]
    fast = ema(raw, FAST_SPAN)
    slow = ema(raw, SLOW_SPAN)
    rana = [
        1000.0 * (r["advancers"] - r["decliners"]) / max(1, r["advancers"] + r["decliners"])
        for r in rows
    ]
    e19, e39 = ema(rana, 19), ema(rana, 39)
    ad_line = 0
    for k, r in enumerate(rows):
        ad_line += r["advancers"] - r["decliners"]
        r["breadth_fast"] = fast[k]
        r["breadth_slow"] = slow[k]
        r["mcclellan"] = e19[k] - e39[k]
        r["ad_line"] = ad_line
    return rows


# --------------------------------------------------------------------------
# Components (each returns dict with score 0-100, data_available, signal, details)
# --------------------------------------------------------------------------

def _unavailable(reason: str) -> Dict:
    return {"score": None, "data_available": False, "signal_key": "no_data", "details": {"reason": reason}}


def c1_level_trend(rows: List[Dict]) -> Dict:
    if len(rows) < SLOW_SPAN // 2 + TREND_LOOKBACK:
        return _unavailable("history")
    last = rows[-1]
    fast, slow = last["breadth_fast"], last["breadth_slow"]
    slow_prev = rows[-1 - TREND_LOOKBACK]["breadth_slow"]
    fast_prev = rows[-6]["breadth_fast"]
    slope = slow - slow_prev
    trend = "up" if slope > 0.002 else "down" if slope < -0.002 else "flat"
    level = piecewise(fast, [(0.15, 5), (0.25, 20), (0.40, 38), (0.50, 50), (0.60, 65), (0.70, 82), (0.80, 95)])
    trend_score = {"up": 80, "flat": 50, "down": 30}[trend]
    mod = 0
    fast_dir = fast - fast_prev
    if fast_dir < 0 and fast > 0.60:
        mod = -10
    elif fast_dir < 0 and fast < 0.40:
        mod = 5
    elif fast_dir > 0 and fast < 0.60:
        mod = 5
    score = max(0, min(100, 0.7 * level + 0.3 * trend_score + mod))
    return {
        "score": round(score, 1),
        "data_available": True,
        "signal_key": "c1_" + trend,
        "details": {
            "breadth_fast": round(fast, 4),
            "breadth_slow": round(slow, 4),
            "slow_trend": trend,
            "fast_5d_change": round(fast_dir, 4),
            "direction_modifier": mod,
        },
    }


def c2_crossover(rows: List[Dict]) -> Dict:
    if len(rows) < SLOW_SPAN // 2 + 6:
        return _unavailable("history")
    last = rows[-1]
    gap = last["breadth_fast"] - last["breadth_slow"]
    fast_dir = last["breadth_fast"] - rows[-6]["breadth_fast"]
    score = piecewise(gap, [(-0.15, 5), (-0.05, 30), (0.0, 50), (0.05, 70), (0.15, 95)])
    mod = 0
    if gap < 0 and fast_dir > 0:
        mod = 10   # early recovery below the slow line
    elif gap > 0 and fast_dir < 0:
        mod = -10  # early deterioration above the slow line
    pink = last["breadth_slow"] < rows[-1 - TREND_LOOKBACK]["breadth_slow"] and gap < 0
    return {
        "score": round(max(0, min(100, score + mod)), 1),
        "data_available": True,
        "signal_key": "c2_above" if gap >= 0 else "c2_below",
        "details": {"gap": round(gap, 4), "direction_modifier": mod, "pink_zone": pink},
    }


def c3_advance_decline(rows: List[Dict]) -> Dict:
    if len(rows) < 40:
        return _unavailable("history")
    last = rows[-1]
    osc = last["mcclellan"]
    osc_score = piecewise(osc, [(-120, 5), (-60, 22), (0, 50), (60, 78), (120, 95)])
    ad_change = last["ad_line"] - rows[-11]["ad_line"]
    avg_n = sum(r["eligible"] for r in rows[-10:]) / 10
    ad_norm = ad_change / max(1.0, avg_n)  # net advances per eligible stock over 10 sessions
    ad_score = piecewise(ad_norm, [(-1.5, 10), (-0.5, 35), (0, 50), (0.5, 65), (1.5, 90)])
    score = 0.7 * osc_score + 0.3 * ad_score
    return {
        "score": round(score, 1),
        "data_available": True,
        "signal_key": "c3_pos" if osc >= 0 else "c3_neg",
        "details": {
            "mcclellan": round(osc, 1),
            "ad_line_10d_change": ad_change,
            "advancers_today": last["advancers"],
            "decliners_today": last["decliners"],
        },
    }


def c4_new_highs_lows(rows: List[Dict], lookback_used: int) -> Dict:
    if len(rows) < 10:
        return _unavailable("history")
    nh = sum(r["new_highs"] for r in rows[-10:])
    nl = sum(r["new_lows"] for r in rows[-10:])
    if nh + nl < 3:
        score, key = 50.0, "c4_quiet"
    else:
        share = nh / (nh + nl)
        score = piecewise(share, [(0.0, 5), (0.2, 20), (0.5, 50), (0.8, 80), (1.0, 95)])
        key = "c4_highs" if share >= 0.5 else "c4_lows"
    return {
        "score": round(score, 1),
        "data_available": True,
        "signal_key": key,
        "details": {"new_highs_10d": nh, "new_lows_10d": nl, "lookback_sessions": lookback_used},
    }


def c5_size_participation(rows: List[Dict]) -> Dict:
    size = rows[-1].get("size") or {}
    groups = {g: v for g, v in size.items() if v["n"] >= 5}
    if len(groups) < 2:
        return _unavailable("membership")
    vals = {g: v["pct_above_200"] for g, v in groups.items()}
    avg = sum(vals.values()) / len(vals)
    score = piecewise(avg, [(0.15, 5), (0.3, 25), (0.5, 50), (0.7, 80), (0.85, 95)])
    large, small = vals.get("WIG20"), vals.get("SWIG80")
    narrow = large is not None and small is not None and large - small > 0.30
    if narrow:
        score -= 10
    return {
        "score": round(max(0, min(100, score)), 1),
        "data_available": True,
        "signal_key": "c5_narrow" if narrow else "c5_broad" if avg >= 0.5 else "c5_weak",
        "details": {
            "pct_above_200_by_index": {g: round(v, 3) for g, v in vals.items()},
            "large_minus_small": round(large - small, 3) if large is not None and small is not None else None,
        },
    }


def _divergence_window(idx: Series, rows: List[Dict], window: int) -> Optional[Dict]:
    if len(rows) <= window:
        return None
    pos = {d: i for i, d in enumerate(idx.dates)}
    d_now, d_then = rows[-1]["date"], rows[-1 - window]["date"]
    if d_now not in pos or d_then not in pos:
        return None
    idx_chg = pct_change(idx.close[pos[d_then]], idx.close[pos[d_now]])
    b_chg = rows[-1]["breadth_fast"] - rows[-1 - window]["breadth_fast"]
    if abs(idx_chg) < 0.5 and abs(b_chg) < 0.01:
        return {"window": window, "index_pct": idx_chg, "breadth_change": b_chg, "pattern": "flat", "score": 50}
    if idx_chg >= 0 and b_chg >= 0:
        pattern, score = "healthy", 70
    elif idx_chg >= 0 and b_chg < 0:
        pattern = "bearish_divergence"
        score = 10 if b_chg < -0.08 else 18 if b_chg < -0.04 else 25
    elif idx_chg < 0 and b_chg >= 0:
        pattern = "bullish_divergence"
        score = 80 if b_chg > 0.04 else 65
    else:
        pattern, score = "consistent_decline", 30
    return {"window": window, "index_pct": idx_chg, "breadth_change": b_chg, "pattern": pattern, "score": score}


def c6_divergence(idx: Optional[Series], rows: List[Dict]) -> Dict:
    if idx is None or len(idx) == 0:
        return _unavailable("index")
    w60 = _divergence_window(idx, rows, 60)
    w20 = _divergence_window(idx, rows, 20)
    if not w60 and not w20:
        return _unavailable("history")
    if w60 and w20:
        score = 0.6 * w60["score"] + 0.4 * w20["score"]
    else:
        score = (w60 or w20)["score"]
    early_warning = bool(w20 and w60 and w20["score"] <= 25 and w60["score"] >= 50)
    main = (w60 or w20)["pattern"]
    for w in (w20, w60):
        if w:
            w["index_pct"] = round(w["index_pct"], 2)
            w["breadth_change"] = round(w["breadth_change"], 4)
    return {
        "score": round(score, 1),
        "data_available": True,
        "signal_key": "c6_" + main,
        "details": {"index": idx.name, "w20": w20, "w60": w60, "early_warning": early_warning},
    }


# --------------------------------------------------------------------------
# Composite
# --------------------------------------------------------------------------

def zone_for(score: float):
    for min_s, key, expo in ZONES:
        if score >= min_s:
            return key, expo
    return ZONES[-1][1], ZONES[-1][2]


def composite(components: Dict[str, Dict]) -> Dict:
    avail = {k: c for k, c in components.items() if c["data_available"]}
    total_w = sum(WEIGHTS[k] for k in avail)
    if total_w == 0:
        score = 50.0
        eff = {}
    else:
        eff = {k: WEIGHTS[k] / total_w for k in avail}
        score = sum(avail[k]["score"] * eff[k] for k in avail)
    n = len(avail)
    quality = "complete" if n == 6 else "partial" if n >= 4 else "limited"
    zone, exposure = zone_for(score)
    caution = any(
        components[k]["data_available"] and components[k]["details"].get("direction_modifier", 0) < 0
        for k in ("breadth_level_trend", "ma_crossover")
    )
    ranked = sorted(avail.items(), key=lambda kv: kv[1]["score"])
    return {
        "score": round(score, 1),
        "zone": zone,
        "exposure": exposure,
        "data_quality": quality,
        "components_available": n,
        "effective_weights": {k: round(v, 3) for k, v in eff.items()},
        "caution": caution,
        "strongest": ranked[-1][0] if ranked else None,
        "weakest": ranked[0][0] if ranked else None,
    }


def history_context(rows: List[Dict]) -> Dict:
    fast_hist = [r["breadth_fast"] for r in rows]
    last = rows[-1]
    return {
        "sessions": len(rows),
        "first_date": rows[0]["date"],
        "fast_percentile": round(percentile_rank(fast_hist, last["breadth_fast"]), 1),
        "fast_min": round(min(fast_hist), 4),
        "fast_max": round(max(fast_hist), 4),
    }
