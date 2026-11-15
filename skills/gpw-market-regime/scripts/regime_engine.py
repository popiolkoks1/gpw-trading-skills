"""GPW market regime engine (pure functions, no I/O).

GPW adaptation of three IBD / William O'Neil tools (via tradermonty's FTD
Detector, IBD Distribution Day Monitor and Market Top Detector):

* distribution days  - institutional selling: index down >= 0.2% on higher
  turnover than the previous session, counted over 25 sessions, expiring
  after 25 sessions or once the index closes >= 5% above that day's close;
* follow-through day - O'Neil's confirmation of a new uptrend: day 4+ of a
  rally attempt, gain >= a volatility-scaled threshold, on higher turnover;
* trend context      - SMA50/SMA200, SMA200 slope, drawdown from the 52-week
  high and WIG20-vs-sWIG80 leadership divergence;
* a daily state machine that turns the above into one of five regimes and a
  0-100 top-risk score.

Every constant is explained next to it and in references/methodology.md.
The engine works on plain lists (dates, close, high, low, turnover_k), so it
is tested with synthetic data and knows nothing about files or the network.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Dict, List, Optional, Sequence

# --------------------------------------------------------------------------
# Constants (justified in references/methodology.md)
# --------------------------------------------------------------------------

# IBD: a distribution day is a decline of at least 0.2% on volume higher than
# the previous session (IBD "Market Pulse" methodology; O'Neil, How to Make
# Money in Stocks, 4th ed., ch. 9). Kept unscaled: it is a "not a flat day"
# filter, not a volatility measure.
DD_MIN_DECLINE = -0.002
# IBD: distribution days drop out of the count after 25 sessions ...
DD_WINDOW = 25
# ... or when the index has risen 5% above the distribution day's close.
DD_EXPIRY_GAIN = 0.05

# Distribution-count bands for 25 sessions. IBD treats 4-5 days as a
# warning and 5-6+ as the typical level at which uptrends come under
# pressure / end. Calibrated on WIG Dec 2024 - Nov 2026 (cache, 450 windows):
# median 3, 75th pct 5, 90th pct 6, 95th pct 7. Note the pure-chance base
# rate: on GPW ~19% of sessions qualify (down >= 0.2% ~37% x higher turnover
# ~50%), i.e. ~4.7 per 25 sessions before expiries - so counts below 4 are
# noise-level and only 6+ is clearly abnormal.
DD_BANDS = [(8, "SEVERE"), (6, "HIGH"), (4, "CAUTION"), (0, "NORMAL")]

# Volatility scaling. US thresholds were designed for the S&P 500 / Nasdaq.
# S&P 500 daily return st.dev. 1990-2024 is ~1.1% (~17.5% annualised).
# GPW thresholds are scaled by sigma_index / US_REF_SIGMA (trailing 250
# sessions). WIG 2025-26: ~1.16% -> ratio ~1.05; sWIG80 ~0.72% -> ~0.66.
US_REF_SIGMA_PCT = 1.10
SIGMA_WINDOW = 250
SIGMA_MIN_OBS = 60
# IBD's modern minimum FTD gain is 1.25% (raised from O'Neil's original 1%
# in the 2000s; IBD uses ~1.25-1.7% depending on index volatility).
FTD_BASE_GAIN_PCT = 1.25
FTD_GAIN_CLAMP = (0.8, 2.2)
# O'Neil: a follow-through comes on day 4 or later of a rally attempt
# (most valid ones on days 4-7; later ones are weaker but still count).
FTD_MIN_DAY = 4
FTD_LATE_DAY = 10
# Correction: drawdown from the 52-week closing high. 8% for an S&P-like
# volatility (between the 5-10% "pullback" zone and the 10% newspaper
# "correction"; IBD usually declares a correction after 7-10% falls with
# distribution). Scaled by volatility and clamped to 6-12%.
CORRECTION_BASE_PCT = 8.0
CORRECTION_CLAMP = (6.0, 12.0)
# Bear market: the conventional 20% fall, scaled, clamped 15-25%.
BEAR_BASE_PCT = 20.0
BEAR_CLAMP = (15.0, 25.0)
HIGH_WINDOW = 250          # 52 weeks of GPW sessions
SMA_FAST, SMA_SLOW = 50, 200
SLOPE_WINDOW = 20          # SMA200 "rising for at least one month" (Minervini trend template)
# After a follow-through IBD watches the FTD day's low: closing below it in
# the first weeks is the first sign of failure.
POST_FTD_WATCH = 25
POST_FTD_EARLY_DD = (3, 15)  # >= 3 distribution days within 15 sessions = weak follow-through
# Uptrend under pressure: IBD's 5-6 distribution days; 6 = our HIGH band.
# Recovery needs the count back to <= 4 (CAUTION) - a 2-day hysteresis band
# so the state does not flip every time one distribution day expires.
PRESSURE_DD = 6
RECOVER_DD = 4
# Distribution-driven correction (no big drawdown yet): HIGH distribution,
# close under SMA50 and at least half of the correction drawdown.
CORR_DD_FRACTION = 0.5
# If an index has turnover on fewer than 80% of sessions, volume rules are
# switched off for it (price-only mode) and the report says so.
MIN_TURNOVER_COVERAGE = 0.80

STATES = ("confirmed_uptrend", "uptrend_under_pressure", "rally_attempt", "correction", "bear")

# Top-risk score weights (Market Top Detector adaptation). Breadth is
# optional; when missing the remaining weights are renormalised.
RISK_WEIGHTS = {"distribution": 0.30, "trend": 0.25, "drawdown": 0.15,
                "leadership": 0.15, "breadth": 0.15}
RISK_BANDS = [(80, "very_high"), (60, "high"), (40, "elevated"), (20, "moderate"), (0, "low")]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _sma(values: Sequence[float], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= n:
            acc -= values[i - n]
        if i >= n - 1:
            out[i] = acc / n
    return out


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _piecewise(x: float, pts: Sequence) -> float:
    if x <= pts[0][0]:
        return float(pts[0][1])
    for (x0, s0), (x1, s1) in zip(pts, pts[1:]):
        if x <= x1:
            return s0 + (s1 - s0) * (x - x0) / (x1 - x0) if x1 != x0 else float(s0)
    return float(pts[-1][1])


def _r(x, d=2):
    return None if x is None else round(x, d)


def dd_band(count: int) -> str:
    for lo, name in DD_BANDS:
        if count >= lo:
            return name
    return "NORMAL"


def risk_band(score: float) -> str:
    for lo, name in RISK_BANDS:
        if score >= lo:
            return name
    return "low"


def is_expiry_day(day: str) -> bool:
    """Third Friday of Mar/Jun/Sep/Dec: WIG20 futures expiry and index
    rebalancing on GPW - turnover is inflated by mechanical trades."""
    d = dt.date.fromisoformat(day)
    return d.month in (3, 6, 9, 12) and d.weekday() == 4 and 15 <= d.day <= 21


def daily_returns(close: Sequence[float]) -> List[Optional[float]]:
    return [None] + [(close[i] / close[i - 1] - 1) if close[i - 1] else None for i in range(1, len(close))]


def rolling_sigma_pct(rets: Sequence[Optional[float]], t: int, window: int = SIGMA_WINDOW) -> Optional[float]:
    xs = [r for r in rets[max(1, t - window + 1): t + 1] if r is not None]
    if len(xs) < SIGMA_MIN_OBS:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) * 100


def thresholds(sigma_pct: Optional[float]) -> Dict:
    """Volatility-scaled FTD gain, correction and bear thresholds (in %)."""
    ratio = (sigma_pct / US_REF_SIGMA_PCT) if sigma_pct else 1.0
    return {
        "sigma_pct": _r(sigma_pct, 3),
        "vol_ratio": round(ratio, 3),
        "ftd_gain_pct": round(_clamp(FTD_BASE_GAIN_PCT * ratio, *FTD_GAIN_CLAMP), 2),
        "correction_pct": round(_clamp(CORRECTION_BASE_PCT * ratio, *CORRECTION_CLAMP), 2),
        "bear_pct": round(_clamp(BEAR_BASE_PCT * ratio, *BEAR_CLAMP), 2),
    }


def volume_mode(turnover: Sequence[Optional[float]]) -> Dict:
    have = sum(1 for v in turnover if v)
    cov = have / len(turnover) if turnover else 0.0
    return {"mode": "turnover" if cov >= MIN_TURNOVER_COVERAGE else "price_only",
            "coverage": round(cov, 3), "missing_sessions": len(turnover) - have}


def higher_turnover(turnover: Sequence[Optional[float]], t: int, mode: str) -> Optional[bool]:
    """True/False if comparable; None if turnover is missing for t or t-1."""
    if mode == "price_only":
        return True
    a, b = turnover[t - 1] if t >= 1 else None, turnover[t]
    if not a or not b:
        return None
    return b > a


def distribution_flags(close: Sequence[float], turnover: Sequence[Optional[float]], mode: str) -> List[bool]:
    rets = daily_returns(close)
    out = [False] * len(close)
    for t in range(1, len(close)):
        if rets[t] is not None and rets[t] <= DD_MIN_DECLINE and higher_turnover(turnover, t, mode) is True:
            out[t] = True
    return out


def active_distribution(dates: Sequence[str], close: Sequence[float], flags: Sequence[bool], t: int,
                        window: int = DD_WINDOW, expiry_gain: float = DD_EXPIRY_GAIN) -> Dict:
    """Distribution days still 'alive' at session t (inclusive)."""
    active, expired_gain = [], []
    for d in range(max(1, t - window + 1), t + 1):
        if not flags[d]:
            continue
        later = close[d + 1: t + 1]
        if later and max(later) >= close[d] * (1 + expiry_gain):
            expired_gain.append(dates[d])
            continue
        active.append({"date": dates[d], "change_pct": round((close[d] / close[d - 1] - 1) * 100, 2),
                       "expires_in_sessions": window - (t - d), "expiry_day": is_expiry_day(dates[d])})
    return {"count": len(active), "days": active, "expired_by_gain": expired_gain}


def active_count_series(dates, close, flags) -> List[int]:
    return [active_distribution(dates, close, flags, t)["count"] if t >= 1 else 0 for t in range(len(close))]


# --------------------------------------------------------------------------
# Index context
# --------------------------------------------------------------------------

class IndexData:
    """Precomputed series for one index."""

    def __init__(self, name: str, dates, close, high=None, low=None, turnover=None):
        if not (len(dates) == len(close)):
            raise ValueError("dates and close must have the same length")
        self.name = name
        self.dates = list(dates)
        self.close = [float(c) for c in close]
        n = len(close)
        self.high = list(high) if high else list(self.close)
        self.low = list(low) if low else list(self.close)
        self.turnover = list(turnover) if turnover else [None] * n
        self.vol = volume_mode(self.turnover)
        self.rets = daily_returns(self.close)
        self.sma50 = _sma(self.close, SMA_FAST)
        self.sma200 = _sma(self.close, SMA_SLOW)
        self.flags = distribution_flags(self.close, self.turnover, self.vol["mode"])
        self.dd_counts = active_count_series(self.dates, self.close, self.flags)
        self.high52: List[float] = []
        self.high52_idx: List[int] = []
        for t in range(n):
            lo = max(0, t - HIGH_WINDOW + 1)
            seg = self.close[lo: t + 1]
            m = max(seg)
            self.high52.append(m)
            self.high52_idx.append(lo + max(i for i, v in enumerate(seg) if v == m))
        self._thr_cache: Dict[int, Dict] = {}

    def __len__(self):
        return len(self.dates)

    def thr(self, t: int) -> Dict:
        if t not in self._thr_cache:
            self._thr_cache[t] = thresholds(rolling_sigma_pct(self.rets, t))
        return self._thr_cache[t]

    def drawdown_pct(self, t: int) -> float:
        return (1 - self.close[t] / self.high52[t]) * 100

    def slope200_pct(self, t: int) -> Optional[float]:
        a = self.sma200[t]
        b = self.sma200[t - SLOPE_WINDOW] if t >= SLOPE_WINDOW else None
        return (a / b - 1) * 100 if a and b else None

    def ret_pct(self, t: int, window: int) -> Optional[float]:
        return (self.close[t] / self.close[t - window] - 1) * 100 if t >= window else None

    def bear_structure(self, t: int) -> bool:
        s50, s200, sl = self.sma50[t], self.sma200[t], self.slope200_pct(t)
        return bool(s50 and s200 and sl is not None and self.close[t] < s200 and sl < 0 and s50 < s200)


def trend_context(ix: IndexData, t: int) -> Dict:
    c, s50, s200 = ix.close[t], ix.sma50[t], ix.sma200[t]
    return {
        "close": round(c, 2),
        "change_pct": _r(ix.rets[t] * 100 if ix.rets[t] is not None else None),
        "sma50": _r(s50), "sma200": _r(s200),
        "above_sma50": (c > s50) if s50 else None,
        "above_sma200": (c > s200) if s200 else None,
        "sma50_above_sma200": (s50 > s200) if (s50 and s200) else None,
        "dist_sma50_pct": _r((c / s50 - 1) * 100) if s50 else None,
        "dist_sma200_pct": _r((c / s200 - 1) * 100) if s200 else None,
        "sma200_slope_20s_pct": _r(ix.slope200_pct(t), 3),
        "high_52w": round(ix.high52[t], 2), "high_52w_date": ix.dates[ix.high52_idx[t]],
        "drawdown_pct": round(ix.drawdown_pct(t), 2),
        "history_sessions": min(t + 1, HIGH_WINDOW),
        "ret_20s_pct": _r(ix.ret_pct(t, 20)), "ret_60s_pct": _r(ix.ret_pct(t, 60)),
    }


# --------------------------------------------------------------------------
# State machine
# --------------------------------------------------------------------------

UP, CORR = "UP", "CORR"


def _below50(ix: IndexData, t: int) -> bool:
    return bool(ix.sma50[t] and ix.close[t] < ix.sma50[t])


def pressure_conditions(ix: IndexData, t: int, ftd: Optional[Dict]) -> List[str]:
    """Reasons an uptrend is 'under pressure' at session t (IBD wording).
    One close under SMA50 is noise on GPW; two in a row count."""
    why: List[str] = []
    if ix.dd_counts[t] >= PRESSURE_DD:
        why.append("distribution_high")
    if t >= 1 and _below50(ix, t) and _below50(ix, t - 1):
        why.append("below_sma50")
    if ftd and ftd.get("active") and t - ftd["idx"] <= POST_FTD_WATCH and ix.close[t] < ftd["day_low"]:
        why.append("below_ftd_low")
    return why


def recovered(ix: IndexData, t: int, ftd: Optional[Dict]) -> bool:
    """Hysteresis: leave 'under pressure' only when distribution has eased
    to <= RECOVER_DD and the index closed above SMA50 twice in a row."""
    above = all(ix.sma50[k] is None or ix.close[k] > ix.sma50[k] for k in (t - 1, t) if k >= 0)
    below_ftd = bool(ftd and ftd.get("active") and t - ftd["idx"] <= POST_FTD_WATCH and ix.close[t] < ftd["day_low"])
    return ix.dd_counts[t] <= RECOVER_DD and above and not below_ftd


def _correction_state(ix: IndexData, t: int, attempt: Optional[Dict]) -> (str, List[str]):
    th = ix.thr(t)
    why: List[str] = []
    if ix.drawdown_pct(t) >= th["bear_pct"]:
        why.append("drawdown_bear")
    if ix.bear_structure(t):
        why.append("bear_structure")
    if why:
        return "bear", why
    if attempt:
        return "rally_attempt", ["rally_day_{}".format(attempt["day"])]
    return "correction", ["no_rally_attempt"]


def run_state_machine(ix: IndexData, start: Optional[int] = None) -> Dict:
    """Walk the index day by day from ``start`` and return daily states,
    transitions, follow-through days and rally attempts.

    Default start: the first session with an SMA50 and >= SIGMA_MIN_OBS
    returns (SMA200-based rules switch on by themselves once 200 sessions
    exist). The first sessions are a warm-up; reports show the last ~250.
    """
    n = len(ix)
    if start is None:
        start = max(SMA_FAST - 1, SIGMA_MIN_OBS)
    if n <= start:
        raise ValueError("{}: need more than {} sessions, have {}".format(ix.name, start, n))
    phase: Optional[str] = None
    pressure = False
    attempt: Optional[Dict] = None
    low_close = low_intraday = None
    low_date = None
    up_peak = None  # highest close since the current uptrend phase began
    ftd: Optional[Dict] = None
    ftds: List[Dict] = []
    failed_attempts: List[Dict] = []
    daily: List[Dict] = []
    transitions: List[Dict] = []
    prev_state = None

    def enter_correction(t):
        nonlocal phase, attempt, low_close, low_intraday, low_date, pressure
        phase, attempt, pressure = CORR, None, False
        low_close, low_intraday, low_date = ix.close[t], ix.low[t], ix.dates[t]

    for t in range(start, n):
        c, th = ix.close[t], ix.thr(t)
        event = None
        if phase is None:
            s50 = ix.sma50[t]
            if s50 and c > s50 and ix.drawdown_pct(t) < th["correction_pct"]:
                phase, event, up_peak = UP, "init_uptrend", ix.high52[t]
            else:
                enter_correction(t)
                event = "init_correction"
        elif phase == UP:
            up_peak = max(up_peak, c)
            # Drawdown is measured from the peak of the *current* uptrend, not
            # the 52-week high: after a follow-through the index is often still
            # far below its old high, and that must not undo the new uptrend.
            up_dd = (1 - c / up_peak) * 100
            if ftd and ftd.get("active") and c < ftd["rally_low"]:
                ftd["failed_on"] = ix.dates[t]
                event = "ftd_failed"
            elif up_dd >= th["correction_pct"]:
                event = "drawdown"
            elif (_below50(ix, t) and ix.dd_counts[t] >= PRESSURE_DD
                  and up_dd >= CORR_DD_FRACTION * th["correction_pct"]):
                event = "distribution_below_sma50"
            if event:
                if ftd:
                    ftd["active"] = False
                enter_correction(t)
        else:  # CORR
            if ix.drawdown_pct(t) <= 0.0:
                # Thin-turnover adaptation: a new 52-week closing high ends the
                # correction even if no qualifying FTD was printed.
                phase, attempt, event, ftd, up_peak = UP, None, "new_high_without_ftd", None, c
            elif attempt is None:
                low_intraday = min(low_intraday, ix.low[t])
                if c <= low_close:
                    low_close, low_date = c, ix.dates[t]
                elif ix.rets[t] is not None and ix.rets[t] > 0:
                    attempt = {"start_idx": t, "start_date": ix.dates[t], "day": 1,
                               "rally_low": low_intraday, "low_date": low_date}
                    event = "rally_attempt_started"
            else:
                attempt["day"] += 1
                if c < attempt["rally_low"]:
                    failed_attempts.append({"start_date": attempt["start_date"], "failed_on": ix.dates[t],
                                            "days": attempt["day"], "rally_low": round(attempt["rally_low"], 2)})
                    attempt = None
                    low_close, low_intraday, low_date = c, ix.low[t], ix.dates[t]
                    event = "rally_attempt_failed"
                else:
                    gain = ix.rets[t] * 100 if ix.rets[t] is not None else 0.0
                    vol_up = higher_turnover(ix.turnover, t, ix.vol["mode"])
                    if attempt["day"] >= FTD_MIN_DAY and gain >= th["ftd_gain_pct"] and vol_up is True:
                        prev_t = ix.turnover[t - 1]
                        ftd = {"idx": t, "date": ix.dates[t], "day_number": attempt["day"],
                               "gain_pct": round(gain, 2), "threshold_pct": th["ftd_gain_pct"],
                               "turnover_change_pct": (round((ix.turnover[t] / prev_t - 1) * 100, 1)
                                                       if ix.vol["mode"] == "turnover" and prev_t else None),
                               "volume_confirmed": ix.vol["mode"] == "turnover",
                               "late": attempt["day"] > FTD_LATE_DAY,
                               "expiry_day": is_expiry_day(ix.dates[t]),
                               "rally_low": attempt["rally_low"], "rally_start": attempt["start_date"],
                               "low_date": attempt["low_date"], "close": round(c, 2),
                               "day_low": ix.low[t], "active": True}
                        ftds.append(ftd)
                        phase, attempt, event, up_peak = UP, None, "follow_through_day", c
        if phase == UP:
            why = pressure_conditions(ix, t, ftd)
            if not pressure and why:
                pressure = True
            elif pressure and not why and recovered(ix, t, ftd):
                pressure = False
            if pressure and not why:
                why = ["not_yet_recovered"]
            state = "uptrend_under_pressure" if pressure else "confirmed_uptrend"
        else:
            state, why = _correction_state(ix, t, attempt)
        rec = {"date": ix.dates[t], "state": state, "phase": phase, "dd_count": ix.dd_counts[t],
               "drawdown_pct": round(ix.drawdown_pct(t), 2), "why": why}
        if event:
            rec["event"] = event
        daily.append(rec)
        if state != prev_state:
            transitions.append({"date": ix.dates[t], "from": prev_state, "to": state, "event": event, "why": why,
                                "close": round(c, 2), "dd_count": ix.dd_counts[t],
                                "drawdown_pct": round(ix.drawdown_pct(t), 2)})
            prev_state = state
    return {"daily": daily, "transitions": transitions, "ftds": ftds, "failed_attempts": failed_attempts,
            "attempt": attempt, "ftd": ftd, "phase": phase, "start": start}


def ftd_health(ix: IndexData, ftd: Optional[Dict], t: Optional[int] = None) -> Optional[Dict]:
    if not ftd:
        return None
    t = len(ix) - 1 if t is None else t
    i = ftd["idx"]
    since = t - i
    dds = sum(1 for k in range(i + 1, t + 1) if ix.flags[k])
    early = sum(1 for k in range(i + 1, min(t, i + POST_FTD_EARLY_DD[1]) + 1) if ix.flags[k])
    reasons = []
    if ftd.get("failed_on"):
        status = "failed"
        reasons.append("closed_below_rally_low")
    elif not ftd.get("active", True):
        status = "ended"
        reasons.append("uptrend_ended")
    else:
        status = "healthy" if since <= POST_FTD_WATCH else "established"
        if since <= POST_FTD_WATCH and ix.close[t] < ftd["day_low"]:
            status = "under_pressure"
            reasons.append("below_ftd_day_low")
        if early >= POST_FTD_EARLY_DD[0]:
            status = "weak" if status == "healthy" else status  # 'established' keeps the note only
            reasons.append("early_distribution")
        if ftd.get("late"):
            reasons.append("late_ftd")
    return {k: v for k, v in {
        "date": ftd["date"], "day_number": ftd["day_number"], "gain_pct": ftd["gain_pct"],
        "threshold_pct": ftd["threshold_pct"], "turnover_change_pct": ftd["turnover_change_pct"],
        "volume_confirmed": ftd["volume_confirmed"], "late": ftd["late"], "expiry_day": ftd["expiry_day"],
        "rally_start": ftd["rally_start"], "low_date": ftd["low_date"], "rally_low": round(ftd["rally_low"], 2),
        "ftd_day_low": round(ftd["day_low"], 2), "ftd_close": ftd["close"],
        "sessions_since": since, "gain_since_pct": round((ix.close[t] / ftd["close"] - 1) * 100, 2),
        "distribution_days_since": dds, "health": status, "health_reasons": reasons,
        "failed_on": ftd.get("failed_on"),
    }.items()}


def attempt_info(ix: IndexData, attempt: Optional[Dict]) -> Optional[Dict]:
    if not attempt:
        return None
    t = len(ix) - 1
    return {"start_date": attempt["start_date"], "day": attempt["day"], "low_date": attempt["low_date"],
            "rally_low": round(attempt["rally_low"], 2),
            "gain_from_low_pct": round((ix.close[t] / attempt["rally_low"] - 1) * 100, 2),
            "ftd_possible_from_day": FTD_MIN_DAY, "ftd_window_open": attempt["day"] + 1 >= FTD_MIN_DAY,
            "ftd_gain_needed_pct": ix.thr(t)["ftd_gain_pct"]}


def state_summary(daily: List[Dict], last_n: int = 250) -> Dict:
    tail = daily[-last_n:]
    out = {s: 0 for s in STATES}
    for d in tail:
        out[d["state"]] += 1
    return {"sessions": len(tail), "from": tail[0]["date"] if tail else None,
            "share_pct": {s: round(100.0 * v / len(tail), 1) if tail else 0.0 for s, v in out.items()}}


# --------------------------------------------------------------------------
# Leadership and top-risk score
# --------------------------------------------------------------------------

def leadership(large: Optional[IndexData], small: Optional[IndexData], mid: Optional[IndexData] = None) -> Dict:
    """WIG20 (large caps) vs sWIG80 (small caps). Generals leading while the
    soldiers lag is the classic narrow-rally / topping pattern."""
    if large is None or small is None:
        return {"available": False}
    out = {"available": True}
    for w in (20, 60):
        a, b = large.ret_pct(len(large) - 1, w), small.ret_pct(len(small) - 1, w)
        m = mid.ret_pct(len(mid) - 1, w) if mid is not None else None
        out["w{}".format(w)] = {"wig20_pct": _r(a), "swig80_pct": _r(b), "mwig40_pct": _r(m),
                                "spread_pp": _r(a - b) if a is not None and b is not None else None}
    s20, s60 = out["w20"]["spread_pp"], out["w60"]["spread_pp"]
    # spread thresholds: the 60-session WIG20-sWIG80 return spread averaged
    # +5 pp with a st.dev. of ~6 pp on 2025-26 data; 10 pp ~ +1 sigma.
    sc60 = _piecewise(s60, [(0, 0), (5, 40), (10, 80), (15, 100)]) if s60 is not None else None
    sc20 = _piecewise(s20, [(0, 0), (3, 40), (6, 80), (9, 100)]) if s20 is not None else None
    if sc60 is not None and sc20 is not None:
        score = 0.6 * sc60 + 0.4 * sc20
    else:
        score = sc60 if sc60 is not None else sc20
    out["risk_score"] = _r(score, 1)
    if s60 is None:
        out["pattern"] = "unknown"
    elif s60 >= 5 and (out["w60"]["swig80_pct"] or 0) < (out["w60"]["wig20_pct"] or 0):
        out["pattern"] = "large_caps_lead"
    elif s60 <= -5:
        out["pattern"] = "small_caps_lead"
    else:
        out["pattern"] = "balanced"
    return out


def breadth_risk(breadth: Optional[Dict]) -> Optional[Dict]:
    """Optional input: a gpw-market-breadth JSON. Risk = 100 - its
    index-divergence score (10 = bearish divergence), blended 50/50 with
    100 - composite score so a broad deterioration also counts."""
    if not breadth:
        return None
    comp = (breadth.get("composite") or {}).get("score")
    div = ((breadth.get("components") or {}).get("index_divergence") or {})
    dscore = div.get("score") if div.get("data_available") else None
    parts = [100 - x for x in (dscore, comp) if isinstance(x, (int, float))]
    if not parts:
        return None
    return {"risk_score": round(sum(parts) / len(parts), 1), "breadth_composite": comp,
            "divergence_score": dscore, "divergence_signal": div.get("signal_key")}


def top_risk(ix: IndexData, lead: Dict, breadth: Optional[Dict] = None, t: Optional[int] = None) -> Dict:
    t = len(ix) - 1 if t is None else t
    th = ix.thr(t)
    comps: Dict[str, Dict] = {}
    cnt = ix.dd_counts[t]
    comps["distribution"] = {"score": round(_piecewise(cnt, [(0, 0), (3, 25), (5, 50), (7, 80), (9, 100)]), 1),
                             "value": cnt}
    pts, why = 0, []
    if ix.sma50[t] and ix.close[t] < ix.sma50[t]:
        pts += 35
        why.append("below_sma50")
    if ix.sma200[t] and ix.close[t] < ix.sma200[t]:
        pts += 35
        why.append("below_sma200")
    if ix.sma50[t] and ix.sma200[t] and ix.sma50[t] < ix.sma200[t]:
        pts += 15
        why.append("sma50_below_sma200")
    sl = ix.slope200_pct(t)
    if sl is not None and sl < 0:
        pts += 15
        why.append("sma200_falling")
    comps["trend"] = {"score": float(pts), "value": why}
    dd = ix.drawdown_pct(t)
    comps["drawdown"] = {"score": round(_piecewise(dd, [(0, 0), (3, 20), (th["correction_pct"], 70), (th["bear_pct"], 100)]), 1),
                         "value": round(dd, 2)}
    if lead.get("available") and lead.get("risk_score") is not None:
        comps["leadership"] = {"score": lead["risk_score"], "value": lead.get("pattern")}
    br = breadth_risk(breadth)
    if br:
        comps["breadth"] = {"score": br["risk_score"], "value": br}
    tw = sum(RISK_WEIGHTS[k] for k in comps)
    score = sum(RISK_WEIGHTS[k] * comps[k]["score"] for k in comps) / tw
    for k in comps:
        comps[k]["weight"] = round(RISK_WEIGHTS[k] / tw, 3)
    return {"score": round(score, 1), "band": risk_band(score), "components": comps,
            "missing": [k for k in RISK_WEIGHTS if k not in comps]}


# --------------------------------------------------------------------------
# One index, end to end
# --------------------------------------------------------------------------

def analyze_index(ix: IndexData, history_sessions: int = 250) -> Dict:
    sm = run_state_machine(ix)
    t = len(ix) - 1
    dist = active_distribution(ix.dates, ix.close, ix.flags, t)
    hist_from = ix.dates[max(sm["start"], len(ix) - history_sessions)]
    return {
        "name": ix.name,
        "as_of": ix.dates[t],
        "state": sm["daily"][-1]["state"],
        "state_reasons": sm["daily"][-1]["why"],
        "volume": ix.vol,
        "thresholds": ix.thr(t),
        "trend": trend_context(ix, t),
        "distribution": {"count": dist["count"], "band": dd_band(dist["count"]), "days": dist["days"],
                         "expired_by_gain": dist["expired_by_gain"], "window": DD_WINDOW},
        "rally_attempt": attempt_info(ix, sm["attempt"]),
        "follow_through": ftd_health(ix, sm["ftds"][-1]) if sm["ftds"] else None,
        "ftd_history": [{"date": f["date"], "day_number": f["day_number"], "gain_pct": f["gain_pct"],
                         "failed_on": f.get("failed_on")} for f in sm["ftds"] if f["date"] >= hist_from],
        "failed_attempts": [a for a in sm["failed_attempts"] if a["failed_on"] >= hist_from],
        "transitions": [tr for tr in sm["transitions"] if tr["date"] >= hist_from],
        "state_before_history": next((tr["to"] for tr in reversed(sm["transitions"]) if tr["date"] < hist_from), None),
        "state_share": state_summary(sm["daily"], history_sessions),
        "model_start": ix.dates[sm["start"]],
        "_sm": sm,
    }
