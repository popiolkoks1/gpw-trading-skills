"""GPW sector uptrend analysis: uptrend ratios overall and per sector + 5-component score.

Adapted from tradermonty/claude-trading-skills ``uptrend-analyzer`` (MIT).
The upstream reads Monty's pre-computed US uptrend-ratio CSV. Here the ratio
is computed from raw GPW quotes with the same per-stock uptrend definition
(gpwlib.universe.is_uptrend) and GPW Benchmark sector indices as sectors.
Every threshold is documented in references/methodology.md.
"""

from __future__ import annotations

import statistics
from typing import Dict, List, Optional

from gpwlib.constituents import SECTOR_INDICES
from gpwlib.indicators import ema, percentile_rank, piecewise, sma
from gpwlib.universe import StockView, eligible, is_uptrend

MA_WINDOW = 10            # same as upstream dashboard
OVERBOUGHT = 0.37         # upstream dashboard thresholds (same uptrend definition)
OVERSOLD = 0.097
MIN_SECTOR_MEMBERS = 3    # fewer eligible stocks -> ratio too noisy to score

WEIGHTS = {
    "market_breadth": 0.30,
    "sector_participation": 0.25,
    "sector_rotation": 0.15,
    "momentum": 0.20,
    "historical_context": 0.10,
}

ZONES = [
    (80, "strong_bull", "100%"),
    (60, "bull", "80–100%"),
    (40, "neutral", "60–80%"),
    (20, "cautious", "30–60%"),
    (0, "bear", "0–30%"),
]


def ratio_series(views: Dict[str, StockView], dates: List[str], cfg: Dict,
                 sector_map: Dict[str, str]) -> Dict[str, List[Dict]]:
    """Return {"all": [...], "<SECTOR>": [...]} with rows {date, count, total, ratio}."""
    out: Dict[str, List[Dict]] = {"all": []}
    for k in SECTOR_INDICES:
        out[k] = []
    for d in dates:
        cnt: Dict[str, int] = {}
        tot: Dict[str, int] = {}
        for name, v in views.items():
            i = v.pos.get(d)
            if i is None or not eligible(v, i, cfg):
                continue
            up = is_uptrend(v, i)
            if up is None:
                continue
            for key in ("all", sector_map.get(name)):
                if key is None:
                    continue
                tot[key] = tot.get(key, 0) + 1
                cnt[key] = cnt.get(key, 0) + bool(up)
        for key in out:
            if tot.get(key):
                out[key].append({"date": d, "count": cnt[key], "total": tot[key], "ratio": cnt[key] / tot[key]})
    for key, rows in out.items():
        _derive(rows)
    return out


def _derive(rows: List[Dict]) -> None:
    ratios = [r["ratio"] for r in rows]
    ma = sma(ratios, MA_WINDOW)
    slope = [None] + [
        (ma[i] - ma[i - 1]) if ma[i] is not None and ma[i - 1] is not None else None
        for i in range(1, len(ma))
    ]
    smooth = ema(slope, 3)
    for i, r in enumerate(rows):
        r["ma10"] = ma[i]
        r["slope"] = slope[i] if rows else None
        r["slope_smooth"] = smooth[i]
        r["trend"] = "up" if (slope[i] or 0) > 0 else "down"


def status_of(ratio: float) -> str:
    if ratio > OVERBOUGHT:
        return "overbought"
    if ratio < OVERSOLD:
        return "oversold"
    return "normal"


def _unavailable(reason):
    return {"score": None, "data_available": False, "signal_key": "no_data", "details": {"reason": reason}}


def sector_snapshot(series: Dict[str, List[Dict]]) -> List[Dict]:
    snap = []
    for key, (isin, en, pl, group) in SECTOR_INDICES.items():
        rows = series.get(key) or []
        if not rows or rows[-1]["ma10"] is None:
            continue
        last = rows[-1]
        snap.append({
            "key": key, "label_en": en, "label_pl": pl, "group": group,
            "count": last["count"], "total": last["total"],
            "ratio": last["ratio"], "ma10": last["ma10"], "slope": last["slope"],
            "trend": last["trend"], "status": status_of(last["ratio"]),
            "low_sample": last["total"] < MIN_SECTOR_MEMBERS,
        })
    return snap


def c1_market_breadth(all_rows: List[Dict]) -> Dict:
    if len(all_rows) < MA_WINDOW + 2:
        return _unavailable("history")
    last = all_rows[-1]
    r = last["ratio"]
    band = piecewise(r, [(0.0, 0), (OVERSOLD, 10), (0.25, 40), (OVERBOUGHT, 70), (0.50, 90), (0.65, 100)])
    adj = 5 if last["trend"] == "up" and (last["slope"] or 0) > 0 else -5 if (last["slope"] or 0) < 0 else 0
    score = max(0, min(100, band + adj))
    return {"score": round(score, 1), "data_available": True,
            "signal_key": "s1_" + status_of(r),
            "details": {"ratio": round(r, 4), "ma10": round(last["ma10"], 4), "trend": last["trend"],
                        "count": last["count"], "total": last["total"], "trend_adjustment": adj}}


def c2_participation(snap: List[Dict]) -> Dict:
    usable = [s for s in snap if not s["low_sample"]]
    if len(usable) < 5:
        return _unavailable("sectors")
    up = sum(1 for s in usable if s["trend"] == "up")
    count_score = 100.0 * up / len(usable)
    ratios = [s["ratio"] for s in usable]
    spread = max(ratios) - min(ratios)
    spread_score = piecewise(spread, [(0.15, 90), (0.40, 30), (0.60, 10)])
    score = 0.6 * count_score + 0.4 * spread_score
    return {"score": round(score, 1), "data_available": True,
            "signal_key": "s2_broad" if up / len(usable) >= 0.6 else "s2_narrow" if up / len(usable) < 0.4 else "s2_mixed",
            "details": {"sectors_up": up, "sectors_scored": len(usable), "spread": round(spread, 4),
                        "overbought": [s["key"] for s in usable if s["status"] == "overbought"],
                        "oversold": [s["key"] for s in usable if s["status"] == "oversold"]}}


def _group_stats(snap, group):
    vals = [s for s in snap if s["group"] == group and not s["low_sample"]]
    if not vals:
        return None
    ratios = [s["ratio"] for s in vals]
    trends = [s["trend"] for s in vals]
    majority = max(set(trends), key=trends.count)
    return {
        "avg": sum(ratios) / len(ratios),
        "std": statistics.pstdev(ratios) if len(ratios) > 1 else 0.0,
        "spread": max(ratios) - min(ratios),
        "dissenters": [s["key"] for s in vals if s["trend"] != majority],
        "n": len(vals),
    }


def c3_rotation(snap: List[Dict]) -> Dict:
    cyc, dfn, com = (_group_stats(snap, g) for g in ("cyclical", "defensive", "commodity"))
    if not cyc or not dfn:
        return _unavailable("sectors")
    diff = cyc["avg"] - dfn["avg"]
    score = piecewise(diff, [(-0.15, 10), (-0.05, 45), (0.0, 57), (0.05, 70), (0.15, 95)])
    late_cycle = bool(com and com["avg"] > cyc["avg"] and com["avg"] > dfn["avg"])
    if late_cycle:
        score -= 5
    divergence = any(g["std"] > 0.08 or g["spread"] > 0.20 or g["dissenters"] for g in (cyc, dfn))
    if divergence:
        score -= 5
    return {"score": round(max(0, min(100, score)), 1), "data_available": True,
            "signal_key": "s3_risk_on" if diff > 0.05 else "s3_risk_off" if diff < -0.05 else "s3_balanced",
            "details": {"cyclical_avg": round(cyc["avg"], 4), "defensive_avg": round(dfn["avg"], 4),
                        "commodity_avg": round(com["avg"], 4) if com else None,
                        "cyclical_minus_defensive": round(diff, 4), "late_cycle": late_cycle,
                        "intra_group_divergence": divergence,
                        "dissenters": (cyc["dissenters"] + dfn["dissenters"])}}


def c4_momentum(all_rows: List[Dict], snap: List[Dict]) -> Dict:
    sm = [r["slope_smooth"] for r in all_rows if r["slope_smooth"] is not None]
    if len(sm) < 10:
        return _unavailable("history")
    slope_score = piecewise(sm[-1], [(-0.02, 0), (0.0, 50), (0.02, 100)])
    w = 10 if len(sm) >= 20 else 5
    recent, prior = sum(sm[-w:]) / w, sum(sm[-2 * w:-w]) / w
    accel = recent - prior
    accel_score = piecewise(accel, [(-0.01, 10), (0.0, 50), (0.01, 90)])
    usable = [s for s in snap if not s["low_sample"] and s["slope"] is not None]
    sec_pos = (sum(1 for s in usable if s["slope"] > 0) / len(usable)) if usable else 0.5
    score = 0.5 * slope_score + 0.3 * accel_score + 0.2 * sec_pos * 100
    return {"score": round(score, 1), "data_available": True,
            "signal_key": "s4_improving" if sm[-1] > 0 and accel >= 0 else "s4_fading" if sm[-1] > 0 else "s4_worsening",
            "details": {"slope_smooth": round(sm[-1], 5), "acceleration": round(accel, 5),
                        "accel_window": "{}v{}".format(w, w), "sectors_positive_slope_share": round(sec_pos, 3)}}


def c5_history(all_rows: List[Dict]) -> Dict:
    ratios = [r["ratio"] for r in all_rows]
    if len(ratios) < 60:
        return _unavailable("history")
    p = percentile_rank(ratios, ratios[-1])
    n = len(ratios)
    size_pts = 3 if n >= 1000 else 2 if n >= 500 else 1 if n >= 200 else 0
    regime_pts = int(min(ratios) < 0.10) + int(max(ratios) > 0.40)
    recent = ratios[-90:]
    rng = (max(ratios) - min(ratios)) or 1e-9
    rec_pts = int((max(recent) - min(recent)) / rng >= 0.30)
    total = size_pts + regime_pts + rec_pts
    conf = "high" if total >= 5 else "moderate" if total >= 3 else "low" if total >= 1 else "very_low"
    return {"score": round(p, 1), "data_available": True, "signal_key": "s5_" + conf,
            "details": {"percentile": round(p, 1), "sessions": n, "min": round(min(ratios), 4),
                        "max": round(max(ratios), 4), "confidence": conf}}


def zone_for(score):
    for m, k, e in ZONES:
        if score >= m:
            return k, e
    return ZONES[-1][1], ZONES[-1][2]


def composite(comps: Dict[str, Dict]) -> Dict:
    avail = {k: c for k, c in comps.items() if c["data_available"]}
    tw = sum(WEIGHTS[k] for k in avail)
    eff = {k: WEIGHTS[k] / tw for k in avail} if tw else {}
    score = sum(avail[k]["score"] * eff[k] for k in avail) if tw else 50.0
    warnings = []
    rot = comps["sector_rotation"]["details"] if comps["sector_rotation"]["data_available"] else {}
    part = comps["sector_participation"]["details"] if comps["sector_participation"]["data_available"] else {}
    if rot.get("late_cycle"):
        warnings.append(("late_cycle", 5))
    if part.get("spread", 0) > 0.40:
        warnings.append(("high_spread", 3))
    if rot.get("intra_group_divergence"):
        warnings.append(("divergence", 3))
    penalty = min(10, sum(p for _, p in warnings))
    if len(warnings) >= 2:
        penalty -= 1  # overlapping causes: soften stacking (upstream rule)
    adjusted = max(0.0, score - penalty)
    zone, exposure = zone_for(adjusted)
    return {"raw_score": round(score, 1), "score": round(adjusted, 1), "penalty": penalty,
            "warnings": [w for w, _ in warnings], "zone": zone, "exposure": exposure,
            "components_available": len(avail), "effective_weights": {k: round(v, 3) for k, v in eff.items()}}


def index_returns(indices_panel, names: List[str], window: int = 20) -> Dict[str, Optional[float]]:
    """% change over ``window`` sessions for each index present in the indices panel."""
    out = {}
    for n in names:
        s = indices_panel.get(n)
        if s is None or len(s) <= window:
            out[n] = None
            continue
        out[n] = (s.close[-1] / s.close[-1 - window] - 1) * 100
    return out
