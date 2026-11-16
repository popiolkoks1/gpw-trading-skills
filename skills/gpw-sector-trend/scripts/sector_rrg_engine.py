"""Relative Rotation Graph (RRG) engine for GPW indices and stocks - pure functions.

Open approximation of Julius de Kempenaer's JdK RS-Ratio and JdK RS-Momentum.
The original formulas are proprietary (RRG Research / StockCharts); what is
public is the concept: a normalised, smoothed relative-strength *level*
(RS-Ratio, x axis) and its normalised *rate of change* (RS-Momentum, y axis),
both centred on 100, plotted weekly with a short tail. See
references/methodology.md section 7 for the exact formulas, the parameter
choices and the sources.

    RS_t          = 100 * price_t / benchmark_t
    S_t           = EMA(RS, smooth)_t                      (smooth = 10 weeks)
    RS-Ratio_t    = 100 + (S_t - mean(S, norm)) / std(S, norm)          (norm = 52 weeks)
    ROC_t         = EMA(100 * (RS-Ratio_t / RS-Ratio_{t-roc} - 1), mom_smooth)   (roc = 1 week, mom_smooth = 3)
    RS-Momentum_t = 100 + ROC_t / rms(ROC, mom_norm)                    (mom_norm = 26 weeks)

rms = root mean square, i.e. a z-score around zero (mean fixed at 0, see rrg_series).

One unit on either axis is therefore one standard deviation of the
item's own recent history, which puts typical readings in the 96-104 band
that StockCharts RRG charts show.

Everything here is stdlib-only and deterministic; the CLI
(gpw_sector_trend.py --rrg) does the data loading and rendering.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Dict, List, Optional, Sequence, Tuple

from gpwlib import plcal
from gpwlib.indicators import ema

# ---------------------------------------------------------------- parameters
# Each default is justified in references/methodology.md section 7.2.
SMOOTH_WEEKS = 10       # EMA span of RS: ~one quarter of weekly closes, the usual choice in open RRG approximations
NORM_WEEKS = 52         # one year: removes the secular RS level drift (re-ratings), covers a full seasonal cycle
MOM_NORM_WEEKS = 26     # momentum is a faster quantity; half a year keeps it responsive and fits ~2 years of data
ROC_WEEKS = 1           # week-over-week change of an already EMA-smoothed series (smoothing done upstream)
MOM_SMOOTH_WEEKS = 3    # EMA span of the ROC: on GPW indices (2026 sample) it cuts one-week quadrant flip-flops
                        # from ~14 to ~8 per item and year at the cost of ~1 week of lag (methodology 7.2)
TAIL_WEEKS = 8          # two months of path: long enough to see the curve, short enough to stay readable
TRANSITION_WEEKS = 4    # "recent" quadrant changes: last four weekly steps (one month)
CENTER = 100.0

# Rotation direction: sum of the change of the position angle around (100,100)
# along the tail. Steps where a point lies within ROT_MIN_RADIUS of the centre
# are skipped because the angle is undefined/noisy there (0.25 sigma is inside
# normal week-to-week noise). |sum| below ROT_MIN_DEG (~1/18 of a turn) is
# reported as "none" (no clear rotation).
ROT_MIN_RADIUS = 0.25
ROT_MIN_DEG = 20.0

# Concentration of an index: one name >= 50% of the index or the two largest
# >= 70%. Then one or two companies explain most of the index's moves and the
# RRG point is really a single-stock (or pair) chart (e.g. WIG-ODZIEZ ~ LPP,
# WIG-PALIWA ~ ORLEN, WIG-GRY ~ CD PROJEKT). Without weights: <= 3 members
# (two names then carry at least 2/3 even when equally weighted).
CONC_TOP1_PCT = 50.0
CONC_TOP2_PCT = 70.0
CONC_MAX_MEMBERS = 3
# Stock mode: a stock with >= 50% weight in its own sector index *is* most of
# the benchmark, so its RS vs that index is close to flat by construction.
DOMINANT_WEIGHT_PCT = 50.0

QUADRANTS = ("leading", "weakening", "lagging", "improving")
# Normal (clockwise) order of the rotation.
CLOCKWISE_NEXT = {"improving": "leading", "leading": "weakening", "weakening": "lagging", "lagging": "improving"}


class RRGError(ValueError):
    """Not enough data / bad inputs for an RRG computation."""


# ---------------------------------------------------------------- weekly series

def _iso_week(d: dt.date) -> Tuple[int, int]:
    y, w, _ = d.isocalendar()
    return y, w


def weekly_last_sessions(dates: Sequence[str]) -> List[int]:
    """Indices of the last session of each ISO week in ``dates`` (sorted ISO strings).

    Session dates come from the GPW archive, so holidays are handled by the
    data itself: a week whose Friday is a holiday (Good Friday, 11 November ...)
    ends on Thursday.
    """
    out: List[int] = []
    prev_week = None
    for i, s in enumerate(dates):
        wk = _iso_week(dt.date.fromisoformat(s))
        if prev_week is not None and wk != prev_week:
            out.append(i - 1)
        prev_week = wk
    if dates:
        out.append(len(dates) - 1)
    return out


def week_is_complete(last_date: str) -> bool:
    """True when no probable GPW session remains in the ISO week of ``last_date`` (plcal calendar)."""
    d = dt.date.fromisoformat(last_date)
    day = d + dt.timedelta(days=1)
    while _iso_week(day) == _iso_week(d):
        if plcal.is_probable_session(day):
            return False
        day += dt.timedelta(days=1)
    return True


def align(dates_a: Sequence[str], close_a: Sequence[float],
          dates_b: Sequence[str], close_b: Sequence[float]) -> Tuple[List[str], List[float], List[float]]:
    """Common dates of two daily series (both sorted ascending)."""
    mb = {d: c for d, c in zip(dates_b, close_b) if c}
    dd, aa, bb = [], [], []
    for d, c in zip(dates_a, close_a):
        if c and d in mb:
            dd.append(d)
            aa.append(c)
            bb.append(mb[d])
    return dd, aa, bb


def weekly_pair(dates_a, close_a, dates_b, close_b) -> Tuple[List[str], List[float], List[float]]:
    """Align two daily series and keep the last common session of each week."""
    dd, aa, bb = align(dates_a, close_a, dates_b, close_b)
    idx = weekly_last_sessions(dd)
    return [dd[i] for i in idx], [aa[i] for i in idx], [bb[i] for i in idx]


# ---------------------------------------------------------------- RS-Ratio / RS-Momentum

def _rolling_z(values: Sequence[Optional[float]], window: int, demean: bool = True) -> List[Optional[float]]:
    """z-score of each value against the trailing ``window`` values (inclusive, population std).

    ``demean=False`` fixes the mean at 0 and scales by the root mean square
    (a z-score around zero): the sign of the input is preserved, which is what
    RS-Momentum needs (see rrg_series).
    """
    out: List[Optional[float]] = [None] * len(values)
    for i in range(len(values)):
        if i < window - 1:
            continue
        win = values[i - window + 1:i + 1]
        if any(v is None for v in win):
            continue
        if demean:
            mu = sum(win) / window
            sd = statistics.pstdev(win)
        else:
            mu = 0.0
            sd = math.sqrt(sum(v * v for v in win) / window)
        out[i] = 0.0 if sd < 1e-12 else (values[i] - mu) / sd
    return out


def min_weeks(smooth: int = SMOOTH_WEEKS, norm: int = NORM_WEEKS, mom_norm: int = MOM_NORM_WEEKS,
              roc: int = ROC_WEEKS, tail: int = TAIL_WEEKS, mom_smooth: int = MOM_SMOOTH_WEEKS) -> int:
    """Weekly observations needed for a full tail of ``tail`` points."""
    first_ratio = (smooth - 1) + (norm - 1)
    first_mom = first_ratio + roc + (mom_smooth - 1) + (mom_norm - 1)
    return first_mom + tail


def rrg_series(rs: Sequence[float], smooth: int = SMOOTH_WEEKS, norm: int = NORM_WEEKS,
               mom_norm: int = MOM_NORM_WEEKS, roc: int = ROC_WEEKS,
               mom_smooth: int = MOM_SMOOTH_WEEKS) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    """(RS-Ratio, RS-Momentum) lists aligned with ``rs`` (None during warm-up).

    The first ``smooth - 1`` EMA values are discarded (EMA warm-up), so the
    smoothing window is fully "loaded" before normalisation starts.
    """
    if min(smooth, norm, mom_norm, roc, mom_smooth) < 1 or norm < 2 or mom_norm < 2:
        raise RRGError("RRG windows must be >= 1 (normalisation windows >= 2)")
    sm = ema(list(rs), smooth)
    sm = [None if i < smooth - 1 else v for i, v in enumerate(sm)]
    zr = _rolling_z(sm, norm)
    ratio = [None if z is None else CENTER + z for z in zr]
    rocs: List[Optional[float]] = [None] * len(ratio)
    for i in range(roc, len(ratio)):
        a, b = ratio[i], ratio[i - roc]
        if a is not None and b is not None and b != 0:
            rocs[i] = 100.0 * (a / b - 1.0)
    if mom_smooth > 1:
        first = next((i for i, v in enumerate(rocs) if v is not None), None)
        if first is not None:
            sm_roc = ema(rocs[first:], mom_smooth)
            rocs = [None] * first + [None if k < mom_smooth - 1 else v for k, v in enumerate(sm_roc)]
    # RS-Momentum is scaled around zero, not de-meaned: a persistent decline of
    # RS-Ratio must keep RS-Momentum below 100 (a de-meaned z-score would drift
    # back to 100 after ~mom_norm weeks of steady decline and break the
    # "momentum > 100 <=> RS-Ratio rising" reading of the chart).
    zm = _rolling_z(rocs, mom_norm, demean=False)
    mom = [None if z is None else CENTER + z for z in zm]
    return ratio, mom


def quadrant(rs_ratio: float, rs_momentum: float) -> str:
    """leading (>100,>100), weakening (>100,<=100), lagging (<=100,<=100), improving (<=100,>100).

    Points exactly on an axis are assigned to the weaker side (ties are
    practically impossible with real data).
    """
    if rs_ratio > CENTER:
        return "leading" if rs_momentum > CENTER else "weakening"
    return "improving" if rs_momentum > CENTER else "lagging"


def heading_deg(prev: Tuple[float, float], cur: Tuple[float, float]) -> Optional[float]:
    """Direction of the last step in degrees, 0 = east (RS-Ratio rising), 90 = north
    (RS-Momentum rising), counter-clockwise, range [0, 360). None if no movement."""
    dx, dy = cur[0] - prev[0], cur[1] - prev[1]
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return None
    return math.degrees(math.atan2(dy, dx)) % 360.0


def heading_sector(h: Optional[float]) -> Optional[str]:
    """Compass octant of a heading: 0-90 'NE' (both rising, the strongest direction) etc."""
    if h is None:
        return None
    if h < 90:
        return "NE"
    if h < 180:
        return "NW"
    if h < 270:
        return "SW"
    return "SE"


def distance(rs_ratio: float, rs_momentum: float) -> float:
    """Euclidean distance from (100, 100): strength of the relative trend in sigma units."""
    return math.hypot(rs_ratio - CENTER, rs_momentum - CENTER)


def rotation(points: Sequence[Tuple[float, float]]) -> Dict:
    """Net angular travel of the tail around the centre.

    Negative angle change = clockwise (the normal RRG rotation improving ->
    leading -> weakening -> lagging). Returns {direction, angle_deg}.
    """
    total = 0.0
    used = 0
    for a, b in zip(points, points[1:]):
        ax, ay = a[0] - CENTER, a[1] - CENTER
        bx, by = b[0] - CENTER, b[1] - CENTER
        if math.hypot(ax, ay) < ROT_MIN_RADIUS or math.hypot(bx, by) < ROT_MIN_RADIUS:
            continue
        d = math.atan2(by, bx) - math.atan2(ay, ax)
        while d > math.pi:
            d -= 2 * math.pi
        while d < -math.pi:
            d += 2 * math.pi
        total += d
        used += 1
    deg = math.degrees(total)
    if used == 0 or abs(deg) < ROT_MIN_DEG:
        direction = "none"
    else:
        direction = "clockwise" if deg < 0 else "counterclockwise"
    return {"direction": direction, "angle_deg": round(deg, 1)}


def transitions(tail: Sequence[Dict], weeks: int = TRANSITION_WEEKS) -> List[Dict]:
    """Quadrant changes among the last ``weeks`` weekly steps of a tail."""
    pts = list(tail)[-(weeks + 1):]
    out = []
    for a, b in zip(pts, pts[1:]):
        qa, qb = quadrant(a["rs_ratio"], a["rs_momentum"]), quadrant(b["rs_ratio"], b["rs_momentum"])
        if qa != qb:
            out.append({"date": b["date"], "from": qa, "to": qb,
                        "clockwise": CLOCKWISE_NEXT[qa] == qb})
    return out


def weeks_in_quadrant(ratio: Sequence[Optional[float]], mom: Sequence[Optional[float]]) -> int:
    """How many consecutive latest weeks the item has been in its current quadrant."""
    n = 0
    q = None
    for r, m in zip(reversed(ratio), reversed(mom)):
        if r is None or m is None:
            break
        qq = quadrant(r, m)
        if q is None:
            q = qq
        elif qq != q:
            break
        n += 1
    return n


def concentration(weights_pct: Optional[Sequence[Optional[float]]], n_members: Optional[int] = None) -> Dict:
    """Concentration of an index portfolio from GPW Benchmark weights (in %)."""
    w = sorted([x for x in (weights_pct or []) if x is not None and x > 0], reverse=True)
    if w:
        total = sum(w)
        w = [x * 100.0 / total for x in w]  # renormalise (published weights may not sum to exactly 100)
        hhi = sum(x * x for x in w)
        top1, top2 = w[0], sum(w[:2])
        conc = top1 >= CONC_TOP1_PCT or top2 >= CONC_TOP2_PCT
        return {"concentrated": conc, "basis": "weights", "top1_pct": round(top1, 1), "top2_pct": round(top2, 1),
                "hhi": round(hhi), "effective_n": round(10000.0 / hhi, 1), "members": len(w)}
    if n_members:
        return {"concentrated": n_members <= CONC_MAX_MEMBERS, "basis": "member_count", "members": n_members,
                "top1_pct": None, "top2_pct": None, "hhi": None, "effective_n": None}
    return {"concentrated": None, "basis": "unknown", "members": None, "top1_pct": None, "top2_pct": None,
            "hhi": None, "effective_n": None}


def compute_item(item_id: str, dates: Sequence[str], price: Sequence[float], bench: Sequence[float], *,
                 smooth: int = SMOOTH_WEEKS, norm: int = NORM_WEEKS, mom_norm: int = MOM_NORM_WEEKS,
                 roc: int = ROC_WEEKS, tail: int = TAIL_WEEKS, mom_smooth: int = MOM_SMOOTH_WEEKS) -> Dict:
    """RRG point, tail and derived fields for one weekly price series vs a weekly benchmark."""
    if not (len(dates) == len(price) == len(bench)):
        raise RRGError("dates/price/benchmark lengths differ")
    if tail < 2:
        raise RRGError("tail must be >= 2")
    need = min_weeks(smooth, norm, mom_norm, roc, tail, mom_smooth)
    if len(dates) < need:
        raise RRGError("{}: {} weekly observations, need {} (smooth {} + norm {} + roc {} + momentum smooth {} + "
                       "momentum norm {} + tail {})".format(item_id, len(dates), need, smooth, norm, roc, mom_smooth,
                                                            mom_norm, tail))
    if any(b <= 0 for b in bench) or any(p <= 0 for p in price):
        raise RRGError("{}: non-positive prices".format(item_id))
    rs = [100.0 * p / b for p, b in zip(price, bench)]
    ratio, mom = rrg_series(rs, smooth, norm, mom_norm, roc, mom_smooth)
    pts = [{"date": d, "rs_ratio": round(r, 3), "rs_momentum": round(m, 3)}
           for d, r, m in zip(dates, ratio, mom) if r is not None and m is not None]
    tl = pts[-tail:]
    cur, prev = tl[-1], tl[-2]
    r, m = cur["rs_ratio"], cur["rs_momentum"]
    h = heading_deg((prev["rs_ratio"], prev["rs_momentum"]), (r, m))
    return {
        "id": item_id,
        "quadrant": quadrant(r, m),
        "rs_ratio": r,
        "rs_momentum": m,
        "heading_deg": None if h is None else round(h, 1),
        "heading_sector": heading_sector(h),
        "distance": round(distance(r, m), 3),
        "rotation": rotation([(p["rs_ratio"], p["rs_momentum"]) for p in tl]),
        "weeks_in_quadrant": weeks_in_quadrant([p["rs_ratio"] for p in pts], [p["rs_momentum"] for p in pts]),
        "tail": tl,
        "transitions": transitions(tl, min(TRANSITION_WEEKS, len(tl) - 1)),
        "rs_last": round(rs[-1], 4),
    }


# ---------------------------------------------------------------- summary & signals

def quadrant_counts(items: Sequence[Dict]) -> Dict[str, int]:
    out = {q: 0 for q in QUADRANTS}
    for it in items:
        out[it["quadrant"]] += 1
    return out


def rotation_summary(items: Sequence[Dict]) -> List[Dict]:
    """Structured summary lines: recent transitions first (newest first), then
    items about to change quadrant (heading toward the next clockwise quadrant
    and within 0.5 sigma of the boundary they are heading to)."""
    out = []
    for it in items:
        if it["transitions"]:
            t = it["transitions"][-1]
            out.append({"id": it["id"], "kind": "transition", "from": t["from"], "to": t["to"], "date": t["date"],
                        "clockwise": t["clockwise"]})
    out.sort(key=lambda x: x["date"], reverse=True)
    seen = {x["id"] for x in out}
    for it in items:
        if it["id"] in seen:
            continue
        q, h = it["quadrant"], it["heading_deg"]
        if h is None:
            continue
        r, m = it["rs_ratio"] - CENTER, it["rs_momentum"] - CENTER
        # boundary crossed by the clockwise move out of each quadrant
        approaching = (
            (q == "improving" and 0 <= h < 90 and abs(r) < 0.5) or
            (q == "leading" and 270 <= h < 360 and abs(m) < 0.5) or
            (q == "weakening" and 180 <= h < 270 and abs(r) < 0.5) or
            (q == "lagging" and 90 <= h < 180 and abs(m) < 0.5)
        )
        if approaching:
            out.append({"id": it["id"], "kind": "approaching", "from": q, "to": CLOCKWISE_NEXT[q],
                        "date": it["tail"][-1]["date"], "clockwise": True})
    return out


SIGNAL_KINDS = {
    ("improving", "leading"): ("rrg_improving_to_leading", "long"),
    ("leading", "weakening"): ("rrg_leading_to_weakening", "exit"),
}


def signals(items: Sequence[Dict], as_of: str, stock_mode: bool = False) -> List[Dict]:
    """Generic signals[] (gpw-signal-postmortem contract v1) from recent transitions.

    One signal per transition, dated at the weekly close where it happened
    (deduplicated downstream by (skill, ticker, date, kind)). Stock mode uses
    distinct kinds because the benchmark there is the sector index, not WIG.
    """
    out = []
    for it in items:
        for t in it["transitions"]:
            key = SIGNAL_KINDS.get((t["from"], t["to"]))
            if not key or t["date"] > as_of:
                continue
            kind, direction = key
            if stock_mode:
                kind = kind.replace("rrg_", "rrg_stock_", 1)
            pt = next(p for p in it["tail"] if p["date"] == t["date"])
            out.append({
                "ticker": it["id"], "date": t["date"], "kind": kind, "direction": direction,
                "score": round(min(100.0, distance(pt["rs_ratio"], pt["rs_momentum"]) * 25.0), 1),
                "tags": {"rs_ratio": pt["rs_ratio"], "rs_momentum": pt["rs_momentum"],
                         "benchmark": it.get("benchmark", ""), "concentrated": bool(it.get("concentrated"))},
            })
    return out


# ---------------------------------------------------------------- rendering helpers (pure)

def _half_range(values: Sequence[float], floor: float = 1.0, pad: float = 1.1) -> float:
    """Symmetric axis half-range around 100: largest deviation + 10% margin (keeps
    points off the frame and the corner labels), rounded up to 0.5, at least ``floor``."""
    m = max([abs(v - CENTER) * pad for v in values] + [floor])
    return math.ceil(m * 2.0) / 2.0


def ascii_scatter(items: Sequence[Dict], labels: Dict[str, str], width: int = 41, height: int = 21,
                  quadrant_names: Optional[Dict[str, str]] = None) -> str:
    """Text RRG: x = RS-Ratio, y = RS-Momentum, centre lines through (100,100).

    Each item is drawn with its one-character code from ``labels``; when two
    items fall into the same cell the cell shows '*'. Width/height should be
    odd so that the centre lines fall on a column/row.
    """
    if width < 11 or height < 7:
        raise RRGError("grid too small")
    qn = quadrant_names or {q: q.upper() for q in QUADRANTS}
    hx = _half_range([it["rs_ratio"] for it in items])
    hy = _half_range([it["rs_momentum"] for it in items])
    cx, cy = width // 2, height // 2
    grid = [[" "] * width for _ in range(height)]
    for y in range(height):
        grid[y][cx] = "|"
    for x in range(width):
        grid[cy][x] = "-"
    grid[cy][cx] = "+"

    def put(text, row, col):
        for k, ch in enumerate(text):
            if 0 <= col + k < width:
                grid[row][col + k] = ch

    put(qn["improving"][:cx - 1], 0, 0)
    put(qn["leading"][:cx - 1], 0, width - min(len(qn["leading"]), cx - 1))
    put(qn["lagging"][:cx - 1], height - 1, 0)
    put(qn["weakening"][:cx - 1], height - 1, width - min(len(qn["weakening"]), cx - 1))
    placed: Dict[Tuple[int, int], str] = {}
    for it in items:
        col = int(round(cx + (it["rs_ratio"] - CENTER) / hx * cx))
        row = int(round(cy - (it["rs_momentum"] - CENTER) / hy * cy))
        col = max(0, min(width - 1, col))
        row = max(0, min(height - 1, row))
        ch = labels.get(it["id"], "?")
        placed[(row, col)] = "*" if (row, col) in placed else ch
    for (row, col), ch in placed.items():
        grid[row][col] = ch
    lines = ["{:>7} ┌{}┐".format("", "─" * width)]
    for y, row in enumerate(grid):
        tick = ""
        if y == 0:
            tick = "{:.1f}".format(CENTER + hy)
        elif y == cy:
            tick = "{:.1f}".format(CENTER)
        elif y == height - 1:
            tick = "{:.1f}".format(CENTER - hy)
        lines.append("{:>7} │{}│".format(tick, "".join(row)))
    lines.append("{:>7} └{}┘".format("", "─" * width))
    left, mid, right = "{:.1f}".format(CENTER - hx), "{:.1f}".format(CENTER), "{:.1f}".format(CENTER + hx)
    axis = [" "] * (width + 2)
    for text, pos in ((left, 0), (mid, cx + 1 - len(mid) // 2), (right, width + 2 - len(right))):
        for k, ch in enumerate(text):
            if 0 <= pos + k < len(axis):
                axis[pos + k] = ch
    lines.append("{:>7} {}".format("", "".join(axis)))
    return "\n".join(lines)


def _xml(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


SVG_QUADRANT_FILL = {"leading": "#e3f4e6", "weakening": "#fbf3d9", "lagging": "#fbe4e4", "improving": "#e3ecfb"}
SVG_PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#bcbd22",
               "#ff7f0e", "#393b79", "#637939", "#843c39", "#7b4173", "#3182bd", "#e6550d", "#31a354",
               "#756bb1", "#636363"]


def svg_chart(items: Sequence[Dict], title: str, subtitle: str = "",
              quadrant_names: Optional[Dict[str, str]] = None, axis_names: Tuple[str, str] = ("RS-Ratio", "RS-Momentum"),
              size: int = 720) -> str:
    """Standalone SVG of the RRG with tails (stdlib string building)."""
    qn = quadrant_names or {q: q.capitalize() for q in QUADRANTS}
    xs = [p["rs_ratio"] for it in items for p in it["tail"]]
    ys = [p["rs_momentum"] for it in items for p in it["tail"]]
    hx, hy = _half_range(xs), _half_range(ys)
    ml, mr, mt, mb = 60, 20, 60, 50
    w = h = size
    pw, ph = w - ml - mr, h - mt - mb

    def X(v):
        return ml + (v - (CENTER - hx)) / (2 * hx) * pw

    def Y(v):
        return mt + ((CENTER + hy) - v) / (2 * hy) * ph

    cxp, cyp = X(CENTER), Y(CENTER)
    o = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
         'font-family="Helvetica, Arial, sans-serif" font-size="12">'.format(w=w, h=h),
         '<rect x="0" y="0" width="{}" height="{}" fill="#ffffff"/>'.format(w, h),
         '<text x="{}" y="24" font-size="16" font-weight="bold">{}</text>'.format(ml, _xml(title)),
         '<text x="{}" y="42" fill="#555">{}</text>'.format(ml, _xml(subtitle))]
    quads = [("improving", ml, mt, cxp - ml, cyp - mt), ("leading", cxp, mt, ml + pw - cxp, cyp - mt),
             ("lagging", ml, cyp, cxp - ml, mt + ph - cyp), ("weakening", cxp, cyp, ml + pw - cxp, mt + ph - cyp)]
    for q, x0, y0, ww, hh in quads:
        o.append('<rect x="{:.1f}" y="{:.1f}" width="{:.1f}" height="{:.1f}" fill="{}"/>'.format(
            x0, y0, ww, hh, SVG_QUADRANT_FILL[q]))
    o.append('<text x="{:.1f}" y="{:.1f}" fill="#666" font-weight="bold">{}</text>'.format(ml + 6, mt + 16, _xml(qn["improving"])))
    o.append('<text x="{:.1f}" y="{:.1f}" fill="#666" font-weight="bold" text-anchor="end">{}</text>'.format(
        ml + pw - 6, mt + 16, _xml(qn["leading"])))
    o.append('<text x="{:.1f}" y="{:.1f}" fill="#666" font-weight="bold">{}</text>'.format(ml + 6, mt + ph - 8, _xml(qn["lagging"])))
    o.append('<text x="{:.1f}" y="{:.1f}" fill="#666" font-weight="bold" text-anchor="end">{}</text>'.format(
        ml + pw - 6, mt + ph - 8, _xml(qn["weakening"])))
    o.append('<rect x="{}" y="{}" width="{}" height="{}" fill="none" stroke="#999"/>'.format(ml, mt, pw, ph))
    o.append('<line x1="{:.1f}" y1="{}" x2="{:.1f}" y2="{}" stroke="#777"/>'.format(cxp, mt, cxp, mt + ph))
    o.append('<line x1="{}" y1="{:.1f}" x2="{}" y2="{:.1f}" stroke="#777"/>'.format(ml, cyp, ml + pw, cyp))
    for k in range(5):
        vx = CENTER - hx + k * hx / 2
        vy = CENTER - hy + k * hy / 2
        o.append('<text x="{:.1f}" y="{}" text-anchor="middle" fill="#444">{:.1f}</text>'.format(X(vx), mt + ph + 16, vx))
        o.append('<text x="{}" y="{:.1f}" text-anchor="end" fill="#444">{:.1f}</text>'.format(ml - 6, Y(vy) + 4, vy))
    o.append('<text x="{:.1f}" y="{}" text-anchor="middle" fill="#222">{} →</text>'.format(ml + pw / 2, h - 12, _xml(axis_names[0])))
    o.append('<text x="16" y="{:.1f}" text-anchor="middle" fill="#222" transform="rotate(-90 16 {:.1f})">{} →</text>'.format(
        mt + ph / 2, mt + ph / 2, _xml(axis_names[1])))
    for n, it in enumerate(items):
        col = SVG_PALETTE[n % len(SVG_PALETTE)]
        pts = [(X(p["rs_ratio"]), Y(p["rs_momentum"])) for p in it["tail"]]
        o.append('<g><title>{}</title>'.format(_xml("{} {} ({:.2f}, {:.2f})".format(
            it.get("label", it["id"]), it["quadrant"], it["rs_ratio"], it["rs_momentum"]))))
        o.append('<polyline fill="none" stroke="{}" stroke-width="1.6" stroke-opacity="0.8" points="{}"/>'.format(
            col, " ".join("{:.1f},{:.1f}".format(x, y) for x, y in pts)))
        for x, y in pts[:-1]:
            o.append('<circle cx="{:.1f}" cy="{:.1f}" r="2.2" fill="{}" fill-opacity="0.6"/>'.format(x, y, col))
        x, y = pts[-1]
        o.append('<circle cx="{:.1f}" cy="{:.1f}" r="5" fill="{}" stroke="#fff"/>'.format(x, y, col))
        o.append('<text x="{:.1f}" y="{:.1f}" fill="{}" font-weight="bold">{}</text>'.format(x + 7, y - 5, col, _xml(it["id"])))
        o.append('</g>')
    o.append('</svg>')
    return "\n".join(o) + "\n"
