"""GPW exposure coach engine (pure functions, no I/O).

GPW adaptation of tradermonty's Exposure Coach and Drawdown Circuit Breaker.
It turns three market reports and the user's own trade journal into a
posture and four limits:

    market inputs  -> weighted score 0-100 -> posture + exposure ceiling
    regime         -> hard cap on exposure and posture (a correction caps it)
    journal        -> circuit breaker (losing streak, drawdown) -> overrides
                      risk per trade and new positions
    open positions -> portfolio heat (risk to stops) -> may stop new entries

Every constant is justified here and in references/methodology.md. The
engine never names a stock and never says buy or sell: it sets limits for a
human decision.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Dict, List, Optional

POSTURES = ("offensive", "normal", "defensive", "capital_preservation")
_RANK = {p: i for i, p in enumerate(POSTURES)}  # higher index = more defensive

# Weights of the market inputs. The regime (index price/volume behaviour,
# O'Neil's "M" - three of four stocks follow the market) gets the most;
# breadth (participation of all liquid shares) next; sector trend last,
# because it is computed from the same share universe as breadth and
# partly overlaps with it.
WEIGHTS = {"regime": 0.40, "breadth": 0.35, "sector": 0.25}

# Regime state -> 0-100 favourability (midpoints of IBD's suggested exposure
# for each market status, see methodology).
STATE_SCORE = {"confirmed_uptrend": 85, "uptrend_under_pressure": 55, "rally_attempt": 35,
               "correction": 20, "bear": 5}
# Inside the regime input, the state counts 60% and the inverted top-risk
# score 40% (top risk is a leading warning, the state a confirmed fact).
STATE_SHARE = 0.6

# Hard caps from the regime (IBD Market Pulse guidance: confirmed uptrend
# 80-100% invested, under pressure 40-80%, market in correction 0-20%; a
# rally attempt before a follow-through is still a correction, but pilot
# positions are allowed once the FTD window opens -> 40%; correction 25% =
# IBD's 0-20% + 5 pp for long-term IKE/IKZE core holdings common on GPW).
REGIME_CAP = {"confirmed_uptrend": 100.0, "uptrend_under_pressure": 80.0, "rally_attempt": 40.0,
              "correction": 25.0, "bear": 15.0}
REGIME_MAX_POSTURE = {"confirmed_uptrend": "offensive", "uptrend_under_pressure": "normal",
                      "rally_attempt": "defensive", "correction": "defensive", "bear": "capital_preservation"}
# Top-risk caps: a high top-risk score (many warnings at once) limits
# exposure even while the trend is still "confirmed".
TOP_RISK_CAPS = [(85.0, 40.0), (70.0, 60.0)]

# Score -> posture (equal 20-point bands around the neutral 50, top band
# from 70 like the breadth/sector "bull" zones).
POSTURE_BANDS = [(70.0, "offensive"), (50.0, "normal"), (30.0, "defensive"), (0.0, "capital_preservation")]
# Score -> exposure ceiling (%). Anchored to the gpw-market-breadth zones
# (critical 25-40, weakening 40-60, neutral 60-75, healthy 75-90, strong
# 90-100) and slightly more conservative at the bottom.
CEILING_POINTS = [(0, 10), (30, 30), (50, 60), (70, 85), (85, 100)]

# Risk per trade = user's base risk x multiplier. Base default 1% (O'Neil /
# Minervini: risk 0.5-1.25% of capital per trade; Van Tharp: 1% for most
# traders). Defensive postures risk less per idea: a poor market lowers the
# win rate, so the same stop distance should cost less.
POSTURE_RISK_MULT = {"offensive": 1.0, "normal": 0.75, "defensive": 0.5, "capital_preservation": 0.25}
# New positions per week: pacing - limits clustered entries when the market
# is poor. ~180 liquid GPW shares (gpw-market-breadth universe) rarely
# offer more than a handful of valid setups per week.
POSTURE_NEW_POSITIONS = {"offensive": 5, "normal": 3, "defensive": 1, "capital_preservation": 0}
# Portfolio heat cap (sum of open risk to stops, % of account). A. Elder's
# "6% rule" (The New Trading for a Living, 2014) for a normal posture,
# Van Tharp-style 8% when offensive, tighter when defensive.
HEAT_CAP = {"offensive": 8.0, "normal": 6.0, "defensive": 4.0, "capital_preservation": 2.0}

# Circuit breaker. With a 40-50% win rate, 3 losses in a row happen often
# (0.6^3 = 22% of any 3-trade window) - halving risk is a cheap precaution;
# 5 in a row (0.6^5 = 8%) suggests a mismatch between method and market or
# an execution problem -> pause. Drawdown 5% / 10% of the account mirror
# the reduce / stop levels used by prop-trading risk desks and Elder's 6%
# monthly stop. In R units (no account size): 5R / 10R, i.e. the same at
# 1% risk per trade.
STREAK_REDUCE, STREAK_PAUSE = 3, 5
DD_REDUCE_PCT, DD_PAUSE_PCT = 5.0, 10.0
DD_REDUCE_R, DD_PAUSE_R = 5.0, 10.0
DEFAULT_PAUSE_DAYS = 10  # two trading weeks: time to review the journal, not a punishment
REDUCE_RISK_FACTOR = 0.5  # "halve risk" (tradermonty Drawdown Circuit Breaker)

# Inputs older than this many sessions count half towards confidence.
STALE_SESSIONS = 3
CONFIDENCE_BANDS = [(0.8, "high"), (0.5, "medium"), (0.0, "low")]


def _piecewise(x: float, pts) -> float:
    if x <= pts[0][0]:
        return float(pts[0][1])
    for (x0, s0), (x1, s1) in zip(pts, pts[1:]):
        if x <= x1:
            return s0 + (s1 - s0) * (x - x0) / (x1 - x0)
    return float(pts[-1][1])


def _round5(x: float) -> float:
    return float(5 * math.floor(x / 5 + 0.5))


def worst(*postures: str) -> str:
    return max(postures, key=lambda p: _RANK[p])


def posture_from_score(score: float) -> str:
    for lo, p in POSTURE_BANDS:
        if score >= lo:
            return p
    return "capital_preservation"


def ceiling_from_score(score: float) -> float:
    return _round5(_piecewise(score, CEILING_POINTS))


# --------------------------------------------------------------------------
# Input normalisation (schemas of the producing skills)
# --------------------------------------------------------------------------

class InputError(ValueError):
    pass


def parse_regime(data: Dict) -> Dict:
    """gpw-market-regime JSON -> normalised input."""
    if data.get("skill") != "gpw-market-regime":
        raise InputError("not a gpw-market-regime JSON")
    g = data.get("regime") or {}
    state, risk = g.get("state"), g.get("top_risk_score")
    if state not in STATE_SCORE or not isinstance(risk, (int, float)):
        raise InputError("gpw-market-regime JSON lacks regime.state / regime.top_risk_score")
    score = STATE_SHARE * STATE_SCORE[state] + (1 - STATE_SHARE) * (100 - float(risk))
    return {"score": round(score, 1), "as_of": g.get("as_of"), "state": state, "top_risk_score": float(risk),
            "distribution_days": g.get("distribution_days"), "index": g.get("index", "WIG")}


def parse_breadth(data: Dict) -> Dict:
    if data.get("skill") != "gpw-market-breadth":
        raise InputError("not a gpw-market-breadth JSON")
    c = data.get("composite") or {}
    if not isinstance(c.get("score"), (int, float)):
        raise InputError("gpw-market-breadth JSON lacks composite.score")
    return {"score": float(c["score"]), "zone": c.get("zone"), "exposure_hint": c.get("exposure"),
            "as_of": (data.get("data_provenance") or {}).get("as_of"),
            "weakest": c.get("weakest"), "strongest": c.get("strongest")}


def parse_sector(data: Dict) -> Dict:
    if data.get("skill") != "gpw-sector-trend":
        raise InputError("not a gpw-sector-trend JSON")
    c = data.get("composite") or {}
    if not isinstance(c.get("score"), (int, float)):
        raise InputError("gpw-sector-trend JSON lacks composite.score")
    rot = ((data.get("components") or {}).get("sector_rotation") or {})
    return {"score": float(c["score"]), "zone": c.get("zone"), "exposure_hint": c.get("exposure"),
            "as_of": (data.get("data_provenance") or {}).get("as_of"), "warnings": c.get("warnings") or [],
            "rotation": rot.get("signal_key")}


PARSERS = {"regime": parse_regime, "breadth": parse_breadth, "sector": parse_sector}


# --------------------------------------------------------------------------
# Market combination
# --------------------------------------------------------------------------

def combine(inputs: Dict[str, Optional[Dict]]) -> Dict:
    """inputs: name -> normalised dict with 'score' and 'stale' (or None).
    Returns the weighted score (renormalised over available inputs) and the
    confidence (share of the total weight that is available and fresh)."""
    avail = {k: v for k, v in inputs.items() if v is not None}
    if not avail:
        return {"score": None, "effective_weights": {}, "confidence": 0.0, "confidence_level": "none"}
    tw = sum(WEIGHTS[k] for k in avail)
    eff = {k: WEIGHTS[k] / tw for k in avail}
    score = sum(avail[k]["score"] * eff[k] for k in avail)
    conf = sum(WEIGHTS[k] * (0.5 if avail[k].get("stale") else 1.0) for k in avail)
    level = next(lvl for lo, lvl in CONFIDENCE_BANDS if conf >= lo - 1e-9)
    return {"score": round(score, 1), "effective_weights": {k: round(v, 3) for k, v in eff.items()},
            "confidence": round(conf, 2), "confidence_level": level}


# --------------------------------------------------------------------------
# Journal: circuit breaker and heat
# --------------------------------------------------------------------------

def _is_loss(r: Dict) -> bool:
    if r.get("net_pnl") is not None:
        return r["net_pnl"] < 0
    return r.get("r_multiple") is not None and r["r_multiple"] < 0


def losing_streak(closed: List[Dict]) -> int:
    n = 0
    for r in reversed(closed):
        if _is_loss(r):
            n += 1
        else:
            break
    return n


def drawdown_series(values: List[float]) -> Dict:
    """Peak-to-current drawdown of a cumulative sum starting at 0."""
    cum = peak = 0.0
    for v in values:
        cum += v
        peak = max(peak, cum)
    return {"current": round(cum, 2), "peak": round(peak, 2), "drawdown": round(peak - cum, 2)}


def circuit_breaker(closed: List[Dict], account_size: Optional[float] = None, today: Optional[dt.date] = None,
                    pause_days: int = DEFAULT_PAUSE_DAYS) -> Dict:
    """closed: gpwlib.journal_io.closed_results() rows (oldest first)."""
    today = today or dt.date.today()
    streak = losing_streak(closed)
    rs = [r["r_multiple"] for r in closed if r.get("r_multiple") is not None]
    pnls = [r["net_pnl"] for r in closed if r.get("net_pnl") is not None]
    ddr = drawdown_series(rs)
    ddp = drawdown_series(pnls)
    dd_pct = None
    if account_size and account_size > 0:
        # account_size = current equity; peak equity = current + drawdown.
        dd_pct = round(100.0 * ddp["drawdown"] / (account_size + ddp["drawdown"]), 2)
    reasons: List[Dict] = []
    level = "ok"
    if streak >= STREAK_PAUSE:
        level = "pause"
        reasons.append({"code": "streak_pause", "params": {"n": streak, "limit": STREAK_PAUSE}})
    elif streak >= STREAK_REDUCE:
        level = "reduce"
        reasons.append({"code": "streak_reduce", "params": {"n": streak, "limit": STREAK_REDUCE}})
    if dd_pct is not None:
        if dd_pct >= DD_PAUSE_PCT:
            level = "pause"
            reasons.append({"code": "dd_pct_pause", "params": {"pct": dd_pct, "limit": DD_PAUSE_PCT}})
        elif dd_pct >= DD_REDUCE_PCT:
            level = "pause" if level == "pause" else "reduce"
            reasons.append({"code": "dd_pct_reduce", "params": {"pct": dd_pct, "limit": DD_REDUCE_PCT}})
    elif rs:
        if ddr["drawdown"] >= DD_PAUSE_R:
            level = "pause"
            reasons.append({"code": "dd_r_pause", "params": {"r": ddr["drawdown"], "limit": DD_PAUSE_R}})
        elif ddr["drawdown"] >= DD_REDUCE_R:
            level = "pause" if level == "pause" else "reduce"
            reasons.append({"code": "dd_r_reduce", "params": {"r": ddr["drawdown"], "limit": DD_REDUCE_R}})
    pause_until = None
    if level == "pause":
        last = max((r["closed_at"] for r in closed if r.get("closed_at")), default=None)
        start = dt.date.fromisoformat(last[:10]) if last else today
        until = start + dt.timedelta(days=pause_days)
        if today >= until:
            # The pause has run its course: trade again, but at reduced risk
            # until a win breaks the streak / the drawdown heals.
            level = "reduce"
            reasons.append({"code": "pause_elapsed", "params": {"until": until.isoformat()}})
        else:
            pause_until = until.isoformat()
    return {"status": level, "losing_streak": streak, "closed_trades": len(closed),
            "drawdown_r": ddr["drawdown"], "cum_r": ddr["current"], "drawdown_pln": ddp["drawdown"],
            "cum_pnl_pln": ddp["current"], "drawdown_pct": dd_pct, "pause_until": pause_until,
            "pause_days": pause_days, "reasons": reasons,
            "last_results": [{"ticker": r.get("ticker"), "closed_at": r.get("closed_at"), "net_pnl": r.get("net_pnl"),
                              "r_multiple": r.get("r_multiple")} for r in closed[-5:]]}


def portfolio_heat(positions: List[Dict], prices: Optional[Dict[str, float]] = None,
                   account_size: Optional[float] = None) -> Dict:
    """positions: gpwlib.journal_io.open_positions() rows. Risk to stop is
    measured from the latest known close (cache) or, if unknown, the average
    entry. A stop at or above the price locks in profit: risk 0."""
    prices = prices or {}
    rows, total_risk, total_value, no_stop = [], 0.0, 0.0, []
    for p in positions:
        ref = prices.get(p["ticker"])
        src = "last_close" if ref else "avg_price"
        ref = ref or p["avg_price"]
        value = p["shares"] * ref
        stop = p.get("stop")
        risk = None if stop is None else max(0.0, p["shares"] * (ref - stop))
        if risk is None:
            no_stop.append(p["ticker"])
        else:
            total_risk += risk
        total_value += value
        rows.append({"ticker": p["ticker"], "shares": p["shares"], "price": round(ref, 4), "price_source": src,
                     "stop": stop, "value_pln": round(value, 2), "risk_pln": None if risk is None else round(risk, 2)})
    out = {"positions": rows, "open_risk_pln": round(total_risk, 2), "value_pln": round(total_value, 2),
           "no_stop": no_stop, "heat_pct": None, "exposure_pct": None}
    if account_size and account_size > 0:
        out["heat_pct"] = round(100.0 * total_risk / account_size, 2)
        out["exposure_pct"] = round(100.0 * total_value / account_size, 1)
    return out


# --------------------------------------------------------------------------
# Decision
# --------------------------------------------------------------------------

def decide(inputs: Dict[str, Optional[Dict]], breaker: Optional[Dict] = None, heat: Optional[Dict] = None,
           base_risk_pct: float = 1.0) -> Dict:
    comb = combine(inputs)
    if comb["score"] is None:
        raise InputError("no market input available")
    score = comb["score"]
    reasons: List[Dict] = []
    caps: List[Dict] = []
    posture = posture_from_score(score)
    ceiling = ceiling_from_score(score)
    reasons.append({"input": "combined", "code": "score", "params": {"score": score, "posture": posture,
                                                                       "ceiling": ceiling}})
    reg = inputs.get("regime")
    if reg:
        cap = REGIME_CAP[reg["state"]]
        if cap < ceiling:
            caps.append({"source": "regime", "code": "regime_cap", "params": {"state": reg["state"], "cap": cap}})
            ceiling = cap
        rp = REGIME_MAX_POSTURE[reg["state"]]
        if _RANK[rp] > _RANK[posture]:
            caps.append({"source": "regime", "code": "regime_posture", "params": {"state": reg["state"], "posture": rp}})
            posture = rp
        for lim, cap in TOP_RISK_CAPS:
            if reg["top_risk_score"] >= lim:
                if cap < ceiling:
                    caps.append({"source": "regime", "code": "top_risk_cap",
                                 "params": {"risk": reg["top_risk_score"], "limit": lim, "cap": cap}})
                    ceiling = cap
                break
    else:
        reasons.append({"input": "regime", "code": "missing_regime", "params": {}})
    risk = round(base_risk_pct * POSTURE_RISK_MULT[posture], 2)
    new_pos = POSTURE_NEW_POSITIONS[posture]
    cb = (breaker or {}).get("status", "ok")
    if cb == "reduce":
        risk = round(risk * REDUCE_RISK_FACTOR, 2)
        new_pos = max(1, new_pos // 2) if new_pos > 0 else 0
        caps.append({"source": "journal", "code": "breaker_reduce", "params": {}})
    elif cb == "pause":
        risk, new_pos = 0.0, 0
        caps.append({"source": "journal", "code": "breaker_pause", "params": {"until": breaker.get("pause_until")}})
    if heat and heat.get("heat_pct") is not None:
        hc = HEAT_CAP[posture]
        if heat["heat_pct"] >= hc and new_pos > 0:
            new_pos = 0
            caps.append({"source": "journal", "code": "heat_full", "params": {"heat": heat["heat_pct"], "cap": hc}})
        if heat.get("exposure_pct") is not None and heat["exposure_pct"] >= ceiling and new_pos > 0:
            new_pos = 0
            caps.append({"source": "journal", "code": "above_ceiling",
                         "params": {"exposure": heat["exposure_pct"], "ceiling": ceiling}})
    return {
        "posture": posture,
        "limits": {"exposure_ceiling_pct": float(ceiling), "max_risk_per_trade_pct": float(risk),
                   "max_new_positions_per_week": int(new_pos), "circuit_breaker": cb},
        "score": score, "effective_weights": comb["effective_weights"],
        "confidence": {"score": comb["confidence"], "level": comb["confidence_level"]},
        "heat_cap_pct": HEAT_CAP[posture],
        "reasons": reasons, "caps_applied": caps,
    }
