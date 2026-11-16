"""Earnings-reaction engine (pure functions, no I/O).

How the market reacted to each company's periodic report on GPW, whether the
reaction drifted afterwards, and what it means for a held or watched stock.

There is no free consensus of analyst forecasts for Polish companies, so the
"surprise" is measured by the market itself: the earnings announcement return
(EAR), i.e. the cumulative abnormal return CAR[0,+2] around the reaction
session, scaled by the stock's own volatility and confirmed by abnormal
turnover. Every threshold is justified in a comment and in
references/methodology.md.

Conventions
-----------
* Day 0 = reaction session (see :func:`align_event`). Windows ``[a, b]`` are
  inclusive and counted in the stock's own observations (sessions on which the
  stock appears in the GPW archive).
* Returns are simple returns in fractions inside the engine; the public
  dictionaries report percentages (``*_pct``) rounded for display.
* Abnormal return (market-adjusted): ``AR_t = r_i,t - r_WIG,t`` where both
  returns span the same calendar interval (handles days without a quote).
  ``CAR[a,b] = sum AR_t`` (MacKinlay 1997, "Event studies in economics and
  finance", J. Econ. Lit. 35(1)).
* Market model (for comparison): ``AR_t = r_i,t - (alpha + beta * r_WIG,t)``
  with OLS alpha/beta on the estimation window [-250, -30].
"""

from __future__ import annotations

import bisect
import datetime as dt
import math
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from gpwlib import plcal

# --------------------------------------------------------------------------
# Configuration (every value justified; see references/methodology.md)
# --------------------------------------------------------------------------

DEFAULTS = {
    # Session hours, Warsaw time (GPW WATS trading rules, Oct 2026): opening
    # auction ends 09:00, closing auction 17:00, trading-at-last until 17:05.
    # A report published before 09:00 is priced at the open (day 0 = same
    # session); between 09:00 and 17:05 it is priced intraday (day 0 = same
    # session, flagged because part of day 0 precedes the news); after 17:05
    # the first price is the next session's open.
    "open_time": "09:00",
    "close_time": "17:05",
    # Estimation window [-250, -30] (about one year ending six weeks before
    # the event) - the standard choice in daily event studies (MacKinlay
    # 1997; Brown & Warner 1985, J. Fin. Econ. 14) - it keeps the previous
    # report and the pre-event run-up out of the volatility estimate.
    "est_start": -250,
    "est_end": -30,
    # Minimum observations: 60 for the volatility of abnormal returns (about
    # three months - below that the standard error of sigma exceeds ~9%),
    # 100 for the market-model beta (OLS beta on thinly traded WSE stocks is
    # very noisy below ~100 days; Dimson 1979 thin-trading bias).
    "min_obs_sigma": 60,
    "min_obs_beta": 100,
    # Short fallback window for recently listed stocks: at least 30 returns
    # from [-(available), -6]. Flagged as sigma_source = "short_window".
    "min_obs_sigma_short": 30,
    # Turnover baseline: median of [-60, -6] (three months, ending a week
    # before the event so pre-announcement positioning does not inflate the
    # baseline). Median, not mean, because GPW turnover is heavy-tailed
    # (block trades). Needs 20 non-null days.
    "turn_start": -60,
    "turn_end": -6,
    "min_obs_turnover": 20,
    # Labels: z = CAR[0,+2] / (sigma_AR * sqrt(3)). |z| >= 1.96 is the 5%
    # two-sided significance level of the standard event-study test (Brown &
    # Warner 1985) - rounded to 2.0; |z| >= 1 is a one-sigma move (roughly the
    # top / bottom 16% of 3-day moves under normality).
    "z_strong": 2.0,
    "z_normal": 1.0,
    # Turnover confirmation: a strong label needs day-0 turnover >= 1.5x its
    # median (O'Neil/IBD use volume 40-50% above average to confirm a price
    # move; tradermonty earnings-trade-analyzer scores volume the same way;
    # informed reactions come with abnormal volume - Beaver 1968, Kim &
    # Verrecchia 1991). A normal label needs at least normal turnover (1.0x).
    "at_strong": 1.5,
    "at_normal": 1.0,
    # Drift windows: CAR[+3,+20] (about one month, the horizon reported for
    # PEAD by Bernard & Thomas 1989 and Chordia et al. 2009) and CAR[+3,+60]
    # (one quarter, up to the next report). Starting at +3 keeps the 3-day
    # announcement window out of the drift.
    "drift_short": (3, 20),
    "drift_long": (3, 60),
    "run_up": (-20, -1),
    # A periodic report published up to 60 calendar days after preliminary
    # results for the same period belongs to the same information event.
    "prelim_merge_days": 60,
    # Up to 5 sessions forward to find the first quote when the stock did not
    # trade on the reaction session (thin stocks); flagged.
    "max_shift_sessions": 5,
}

# Baseline universe: stocks with median turnover >= 50k zł/day over the last
# 60 sessions (gpwlib.universe.DEFAULTS floor) and quoted on the last session.
BASELINE_MIN_TURNOVER_K = 50.0

LABELS = ("strong_positive", "positive", "neutral", "negative", "strong_negative")
EVENT_CLASSES = ("periodic_q", "periodic_h", "periodic_y", "preliminary_results")
PERIODIC_CLASSES = ("periodic_q", "periodic_h", "periodic_y")


def _hm(text: str) -> dt.time:
    h, m = text.split(":")
    return dt.time(int(h), int(m))


# --------------------------------------------------------------------------
# Session alignment
# --------------------------------------------------------------------------

def is_session(day: dt.date, known_sessions: Sequence[str], is_probable=None) -> bool:
    """A day is a session if it is in the archive's (sorted) session list; days
    after the last archived session fall back to the calendar (``is_probable``,
    normally ``gpwlib.plcal.is_probable_session``; weekdays if None). The
    archive is authoritative: a day without quotes was not a session."""
    iso = day.isoformat()
    if known_sessions and iso <= known_sessions[-1]:
        i = bisect.bisect_left(known_sessions, iso)
        return i < len(known_sessions) and known_sessions[i] == iso
    if is_probable is None:
        return day.weekday() < 5
    return is_probable(day)


def next_session(day: dt.date, known_sessions: Sequence[str], is_probable=None, max_days: int = 15) -> dt.date:
    """First session strictly after ``day``."""
    d = day
    for _ in range(max_days):
        d = d + dt.timedelta(days=1)
        if is_session(d, known_sessions, is_probable):
            return d
    return d


def align_event(published: dt.datetime, known_sessions: Sequence[str], is_probable=None,
                cfg: Optional[Dict] = None) -> Dict:
    """Reaction session for a report published at ``published`` (naive Warsaw time).

    Returns ``{reaction_date, alignment, intraday}`` where ``alignment`` is
    ``pre_open`` (before 09:00, same session), ``intraday`` (09:00-17:05, same
    session, flagged), ``after_close`` (from 17:05, next session) or
    ``non_session`` (weekend/holiday, next session).
    """
    cfg = cfg or DEFAULTS
    open_t, close_t = _hm(cfg["open_time"]), _hm(cfg["close_time"])
    day = published.date()
    if not is_session(day, known_sessions, is_probable):
        return {"reaction_date": next_session(day, known_sessions, is_probable).isoformat(),
                "alignment": "non_session", "intraday": False}
    t = published.time()
    if t < open_t:
        return {"reaction_date": day.isoformat(), "alignment": "pre_open", "intraday": False}
    if t < close_t:
        return {"reaction_date": day.isoformat(), "alignment": "intraday", "intraday": True}
    return {"reaction_date": next_session(day, known_sessions, is_probable).isoformat(),
            "alignment": "after_close", "intraday": False}


# --------------------------------------------------------------------------
# Reporting period inference
# --------------------------------------------------------------------------
# Polish periodic-report deadlines (Rozporządzenie Ministra Finansów z dnia
# 6 czerwca 2025 r. w sprawie informacji bieżących i okresowych, Dz.U. 2025
# poz. 755, § 83, amended by Dz.U. 2026 poz. 1205; it replaced the repealed
# Dz.U. 2018 poz. 757): quarterly report within 60 days after the quarter end
# (ust. 1), no quarterly report for the second and last quarter (ust. 2),
# half-year report within 3 months (ust. 5), annual report within 4 months
# (ust. 7); a deadline on a Saturday or public holiday moves to the next
# working day (ust. 11). Hence for calendar fiscal years:
# Q1 -> published Apr-May, H1 -> Jul-Sep, Q3 -> Oct-Nov, annual -> Jan-Apr.
# Some issuers still publish a Q4 quarterly report voluntarily (Jan-Mar).
# Periods are inferred from the publication month (titles of quarterly
# reports are usually empty) and refined from the title when it names one.

_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4}
_TITLE_Q = [
    re.compile(r"\b(I{1,3}V?|IV)\s*(?:kw\.|kw\b|kwarta\w*)\s*(?:20)?(\d{2})\b", re.I),
    re.compile(r"\b([1-4])\s*(?:Q|kw\.|kw\b|kwarta\w*)\s*(?:20)?(\d{2})\b", re.I),
    re.compile(r"\bQ([1-4])\s*(?:20)?(\d{2})\b", re.I),
]
_TITLE_H = re.compile(r"\b(?:I|1)\s*półrocz\w*\s*(?:20)?(\d{2})\b|\bH1\s*(?:20)?(\d{2})\b|\bhalf[- ]year\w*\s*(?:20)?(\d{2})\b", re.I)
_TITLE_Y = re.compile(r"\b(?:rok\w*|year)\s*(?:obrotowy\s*)?(20\d{2})\b|\b(20\d{2})\s*(?:rok|r\.)", re.I)


def _period_from_month(cls: str, pub: dt.date) -> Tuple[str, int]:
    m, y = pub.month, pub.year
    if cls == "periodic_y":
        return "Q4", y - 1
    if cls == "periodic_h":
        return ("H1", y) if m >= 7 else ("H1", y - 1)
    if m <= 3:
        return "Q4", y - 1
    if m <= 6:
        return "Q1", y
    if m <= 9:
        return "H1", y
    return "Q3", y


def infer_period(cls: str, pub: dt.date, title: str = "") -> Dict:
    """``{key, label, source}``; key like ``2026Q3``. Q4 and the annual report
    share the key ``<year>Q4`` and H1 and Q2 share ``<year>H1``, because they
    carry the same new information (the fourth / second quarter)."""
    text = title or ""
    kind, year, source = None, None, "publication_month"
    if cls != "periodic_y":
        for rx in _TITLE_Q:
            mt = rx.search(text)
            if mt:
                q = mt.group(1).upper()
                qn = _ROMAN.get(q) or int(q)
                kind, year = {1: "Q1", 2: "H1", 3: "Q3", 4: "Q4"}[qn], 2000 + int(mt.group(2)[-2:])
                source = "title"
                break
        if kind is None:
            mh = _TITLE_H.search(text)
            if mh:
                yy = next(g for g in mh.groups() if g)
                kind, year, source = "H1", 2000 + int(yy[-2:]), "title"
    if kind is None and cls in ("periodic_y", "preliminary_results"):
        my = _TITLE_Y.search(text)
        if my and pub.month <= 6:
            yy = int(next(g for g in my.groups() if g))
            if yy == pub.year - 1:
                kind, year, source = "Q4", yy, "title"
    if kind is None:
        kind, year = _period_from_month(cls, pub)
    # sanity: a period cannot end after publication; fall back to the month rule
    end_month = {"Q1": 3, "H1": 6, "Q3": 9, "Q4": 12}[kind]
    if dt.date(year, end_month, 1) > pub:
        kind, year = _period_from_month(cls, pub)
        source = "publication_month"
    label = {"Q1": "Q1 {y}", "H1": "H1/Q2 {y}", "Q3": "Q3 {y}", "Q4": "Q4/FY {y}"}[kind].format(y=year)
    return {"key": "{}{}".format(year, kind), "label": label, "source": source, "kind": kind, "year": year}


def season_for(as_of: dt.date) -> Dict:
    """The reporting season running on ``as_of`` (same month rule as quarterly
    reports): Oct-Dec -> Q3, Jan-Mar -> Q4/FY of last year, Apr-Jun -> Q1,
    Jul-Sep -> H1."""
    p = _period_from_month("periodic_q", as_of)
    kind, year = p
    label = {"Q1": "Q1 {y}", "H1": "H1/Q2 {y}", "Q3": "Q3 {y}", "Q4": "Q4/FY {y}"}[kind].format(y=year)
    return {"key": "{}{}".format(year, kind), "label": label}


LEGAL_BASIS = "Dz.U. 2025 poz. 755, § 83 (amended Dz.U. 2026 poz. 1205)"


def statutory_deadline(period_key: str) -> Optional[str]:
    """Latest legal publication date for the period (Dz.U. 2025 poz. 755 § 83).

    ``<y>Q1`` / ``<y>Q3``: quarter end + 60 days (ust. 1); ``<y>H1``: the
    half-year report, 3 months (ust. 5); ``<y>Q4``: the annual report,
    4 months (ust. 7) - there is no Q2/Q4 quarterly-report obligation
    (ust. 2). A deadline on a Saturday, Sunday or public holiday moves to the
    next working day (ust. 11): Q3 2026 nominal 29.11.2026 (Sunday) ->
    30.11.2026.
    """
    m = re.match(r"^(\d{4})(Q1|H1|Q3|Q4)$", period_key or "")
    if not m:
        return None
    y, k = int(m.group(1)), m.group(2)
    if k == "Q1":
        nominal = dt.date(y, 3, 31) + dt.timedelta(days=60)
    elif k == "Q3":
        nominal = dt.date(y, 9, 30) + dt.timedelta(days=60)
    elif k == "H1":
        nominal = dt.date(y, 9, 30)
    else:
        nominal = dt.date(y + 1, 4, 30)  # annual report (4 months)
    return plcal.roll_to_working_day(nominal).isoformat()


def next_period_key(key: str) -> str:
    y, k = int(key[:4]), key[4:]
    order = ["Q1", "H1", "Q3", "Q4"]
    i = order.index(k)
    return "{}{}".format(y + (1 if i == 3 else 0), order[(i + 1) % 4])


# --------------------------------------------------------------------------
# Events from ESPI rows
# --------------------------------------------------------------------------

def relevant_rows(rows: Iterable[Dict]) -> Tuple[List[Dict], Dict[str, int]]:
    """Keep periodic reports and preliminary results; drop corrections.

    Returns (rows, counts) where counts has ``kept``, ``corrections_ignored``
    and ``other_classes``.
    """
    kept, corr, other = [], 0, 0
    for r in rows:
        if r.get("class") not in EVENT_CLASSES:
            other += 1
            continue
        if r.get("correction"):
            corr += 1
            continue
        kept.append(r)
    return kept, {"kept": len(kept), "corrections_ignored": corr, "other_classes": other}


def build_events(rows: Iterable[Dict], isin_to_ticker: Dict[str, str], known_sessions: Sequence[str],
                 is_probable=None, cfg: Optional[Dict] = None) -> Dict:
    """Group ESPI rows into information events, one per (ticker, period).

    The earliest publication of a period is the information event: when
    preliminary results precede the periodic report (common for banks and
    large caps), the preliminary release is day 0 and the report is attached
    as ``periodic_report``. Several filings for the same period (consolidated
    and standalone, same-day duplicates) collapse into one event.

    Returns ``{events[], unmapped[{company, isin, n}], counts{}}``.
    """
    cfg = cfg or DEFAULTS
    kept, counts = relevant_rows(rows)
    unmapped: Dict[str, Dict] = {}
    groups: Dict[Tuple[str, str], List[Dict]] = {}
    for r in kept:
        ticker = isin_to_ticker.get((r.get("isin") or "").upper())
        if not ticker:
            u = unmapped.setdefault(r.get("isin") or r.get("company") or "?",
                                    {"company": r.get("company"), "isin": r.get("isin"), "n": 0})
            u["n"] += 1
            continue
        pub = dt.datetime.fromisoformat(r["datetime"])
        per = infer_period(r["class"], pub.date(), r.get("title") or "")
        groups.setdefault((ticker, per["key"]), []).append(dict(r, _pub=pub, _period=per))
    events = []
    for (ticker, key), grp in groups.items():
        grp.sort(key=lambda x: x["_pub"])
        first = grp[0]
        periodic = [g for g in grp if g["class"] in PERIODIC_CLASSES]
        prelim = [g for g in grp if g["class"] == "preliminary_results"]
        info = first
        merged_prelim = False
        if first["class"] == "preliminary_results" and periodic:
            gap = (periodic[0]["_pub"] - first["_pub"]).days
            if gap > cfg["prelim_merge_days"]:  # too far apart - treat the report as the event
                info = periodic[0]
            else:
                merged_prelim = True
        al = align_event(info["_pub"], known_sessions, is_probable, cfg)
        report = periodic[0] if periodic else None
        events.append({
            "ticker": ticker,
            "company": info.get("company"),
            "isin": info.get("isin"),
            "period": key,
            "period_label": info["_period"]["label"],
            "period_source": info["_period"]["source"],
            "report_type": report["class"] if report else "preliminary_results",
            "information_event": info["class"],
            "report_date": info["_pub"].date().isoformat(),
            "published_at": info["_pub"].isoformat(),
            "reaction_date": al["reaction_date"],
            "session_alignment": al["alignment"],
            "intraday": al["intraday"],
            "preliminary_first": merged_prelim,
            "periodic_report": ({"date": report["_pub"].date().isoformat(), "published_at": report["_pub"].isoformat(),
                                 "class": report["class"], "url": report.get("url")} if report and report is not info else None),
            "filings": [{"published_at": g["_pub"].isoformat(), "class": g["class"], "type": g.get("type"),
                         "title": g.get("title") or "", "url": g.get("url")} for g in grp],
            "url": info.get("url"),
            "n_preliminary": len(prelim),
        })
    events.sort(key=lambda e: (e["reaction_date"], e["ticker"]))
    counts.update({"events": len(events), "unmapped_rows": sum(u["n"] for u in unmapped.values())})
    return {"events": events, "unmapped": sorted(unmapped.values(), key=lambda u: -u["n"]), "counts": counts}


# --------------------------------------------------------------------------
# Price helpers
# --------------------------------------------------------------------------

def gaps_from_sessions(sessions: Iterable[Dict], names: Optional[set] = None) -> Dict[str, Dict[str, float]]:
    """Opening gap per stock and session, in %: ``open / reference - 1``.

    The reference price is the previous close as GPW adjusted it for that
    session (``close / (1 + change_pct/100)``), so the gap is free of
    dividend and split jumps without a separate adjustment layer. Falls back
    to None when ``open`` is missing or zero (no trades at the open).
    """
    out: Dict[str, Dict[str, float]] = {}
    for sess in sessions:
        d = sess["date"]
        for row in sess["rows"]:
            name = row.get("name")
            if names is not None and name not in names:
                continue
            o, c, chg = row.get("open"), row.get("close"), row.get("change_pct")
            if not o or not c or chg is None or chg <= -100:
                continue
            ref = c / (1 + chg / 100.0)
            if ref > 0:
                out.setdefault(name, {})[d] = (o / ref - 1) * 100.0
    return out


def _mean(xs: Sequence[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def _sd(xs: Sequence[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _median(xs: Sequence[float]) -> Optional[float]:
    v = sorted(xs)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def ols(y: Sequence[float], x: Sequence[float]) -> Optional[Tuple[float, float, float]]:
    """(alpha, beta, residual sd) of y = alpha + beta x."""
    n = len(y)
    if n < 3 or n != len(x):
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx <= 0:
        return None
    beta = sum((a - mx) * (b - my) for a, b in zip(x, y)) / sxx
    alpha = my - beta * mx
    res = [b - alpha - beta * a for a, b in zip(x, y)]
    sd = math.sqrt(sum(r * r for r in res) / (n - 2)) if n > 2 else None
    return alpha, beta, sd


class ReturnPanel:
    """Daily stock and market returns for one stock, indexed by the stock's
    own observations. ``r[k]`` is the return from observation k-1 to k (None
    for k=0); ``m[k]`` the WIG return over the same calendar span."""

    def __init__(self, dates: Sequence[str], close: Sequence[float], market: Dict[str, float]):
        self.dates = list(dates)
        self.pos = {d: i for i, d in enumerate(self.dates)}
        self.r: List[Optional[float]] = [None]
        self.m: List[Optional[float]] = [None]
        for k in range(1, len(self.dates)):
            c0, c1 = close[k - 1], close[k]
            m0, m1 = market.get(self.dates[k - 1]), market.get(self.dates[k])
            self.r.append(c1 / c0 - 1 if c0 and c0 > 0 else None)
            self.m.append(m1 / m0 - 1 if m0 and m1 and m0 > 0 else None)

    def ar(self, k: int) -> Optional[float]:
        if k <= 0 or k >= len(self.r) or self.r[k] is None or self.m[k] is None:
            return None
        return self.r[k] - self.m[k]

    def window(self, t0: int, a: int, b: int) -> List[int]:
        return [k for k in range(t0 + a, t0 + b + 1) if 0 < k < len(self.r)]


def _car(rp: ReturnPanel, t0: int, a: int, b: int, alpha=None, beta=None) -> Tuple[Optional[float], int]:
    """Sum of abnormal returns over [a, b] -> (car, days used). Days beyond the
    last observation are not counted (the caller decides whether it is pending)."""
    s, n = 0.0, 0
    for k in rp.window(t0, a, b):
        if rp.r[k] is None or rp.m[k] is None:
            continue
        if beta is None:
            s += rp.r[k] - rp.m[k]
        else:
            s += rp.r[k] - (alpha + beta * rp.m[k])
        n += 1
    return (s if n else None), n


def label_for(z: Optional[float], at: Optional[float], cfg: Optional[Dict] = None) -> Tuple[str, List[str]]:
    """Five-level label from the volatility-scaled CAR (z) and abnormal turnover (at).

    strong_*: |z| >= z_strong and at >= at_strong; positive/negative:
    |z| >= z_normal and at >= at_normal (or a strong move whose turnover is
    between at_normal and at_strong, or unknown); neutral otherwise.
    Returns (label, flags).
    """
    cfg = cfg or DEFAULTS
    flags: List[str] = []
    if z is None:
        return "neutral", ["no_volatility_estimate"]
    sign = "positive" if z > 0 else "negative"
    az = abs(z)
    if az >= cfg["z_strong"]:
        if at is not None and at >= cfg["at_strong"]:
            return "strong_" + sign, flags
        flags.append("turnover_unconfirmed" if at is not None else "turnover_unknown")
        if at is None or at >= cfg["at_normal"]:
            return sign, flags
        return "neutral", flags
    if az >= cfg["z_normal"]:
        if at is None:
            return sign, ["turnover_unknown"]
        if at >= cfg["at_normal"]:
            return sign, flags
        return "neutral", ["turnover_unconfirmed"]
    return "neutral", flags


def _r(x: Optional[float], d: int = 2) -> Optional[float]:
    return None if x is None else round(x, d)


def event_metrics(dates: Sequence[str], close: Sequence[float], high: Sequence[float], low: Sequence[float],
                  turnover_k: Sequence[float], market: Dict[str, float], reaction_date: str,
                  gaps: Optional[Dict[str, float]] = None, cfg: Optional[Dict] = None,
                  rp: Optional[ReturnPanel] = None) -> Dict:
    """All per-event metrics for one stock series (adjusted prices).

    Returns ``{status, ...}``: status ``ok``; ``pending`` (reaction session
    not in the data yet); ``skipped`` with ``reason`` (no quote within
    ``max_shift_sessions``, or too little history for a volatility estimate).
    """
    cfg = cfg or DEFAULTS
    rp = rp or ReturnPanel(dates, close, market)
    n = len(dates)
    if n == 0 or reaction_date > dates[-1]:
        return {"status": "pending", "reason": "reaction_session_not_in_data"}
    t0 = rp.pos.get(reaction_date)
    shifted = 0
    if t0 is None:
        later = [i for i, d in enumerate(dates) if d > reaction_date]
        if not later:
            return {"status": "pending", "reason": "reaction_session_not_in_data"}
        t0 = later[0]
        shifted = 1
        lag = (dt.date.fromisoformat(dates[t0]) - dt.date.fromisoformat(reaction_date)).days
        # max_shift_sessions sessions are at most ~1.4x as many calendar days (+ holidays)
        if lag > cfg["max_shift_sessions"] * 7 // 5 + 3:
            return {"status": "skipped", "reason": "no_quote_near_reaction_day"}
    if t0 < 1:
        return {"status": "skipped", "reason": "no_prior_close"}
    last = n - 1
    # --- volatility of abnormal returns (estimation window)
    est = [k for k in range(t0 + cfg["est_start"], t0 + cfg["est_end"] + 1) if k >= 1]
    ars = [rp.ar(k) for k in est]
    ars = [a for a in ars if a is not None]
    sigma_source = "estimation_window"
    if len(ars) < cfg["min_obs_sigma"]:
        short = [rp.ar(k) for k in range(1, t0 - 5)]
        short = [a for a in short if a is not None]
        if len(short) < cfg["min_obs_sigma_short"]:
            return {"status": "skipped", "reason": "insufficient_history", "observations": len(short)}
        ars, sigma_source = short, "short_window"
    sigma = _sd(ars)
    # --- market model
    xy = [(rp.m[k], rp.r[k]) for k in est if rp.r[k] is not None and rp.m[k] is not None]
    mm = ols([b for _, b in xy], [a for a, _ in xy]) if len(xy) >= cfg["min_obs_beta"] else None
    # --- announcement window
    car3, days3 = _car(rp, t0, 0, 2)
    car3_mm = _car(rp, t0, 0, 2, mm[0], mm[1])[0] if mm else None
    z = car3 / (sigma * math.sqrt(days3)) if (car3 is not None and sigma and days3) else None
    z_mm = (car3_mm / (mm[2] * math.sqrt(days3))) if (mm and mm[2] and car3_mm is not None and days3) else None
    # --- turnover
    base = [turnover_k[k] for k in range(max(0, t0 + cfg["turn_start"]), max(0, t0 + cfg["turn_end"] + 1))
            if turnover_k[k] is not None]
    med = _median(base) if len(base) >= cfg["min_obs_turnover"] else None
    at = (turnover_k[t0] / med) if med and med > 0 else None
    t3 = [turnover_k[k] for k in range(t0, min(t0 + 3, n))]
    at3 = (sum(t3) / len(t3) / med) if med and med > 0 and t3 else None
    label, flags = label_for(z, at, cfg)
    if shifted:
        flags.append("reaction_day_shifted")
    if sigma_source == "short_window":
        flags.append("short_estimation_window")
    provisional = days3 < 3
    if provisional:
        flags.append("provisional_window")
    # --- run-up and drift
    ru, ru_n = _car(rp, t0, *cfg["run_up"])
    raw_ru = (close[t0 - 1] / close[t0 + cfg["run_up"][0] - 1] - 1) if t0 + cfg["run_up"][0] - 1 >= 0 else None

    def drift(win):
        a, b = win
        if t0 + b > last:
            return None, "pending", t0 + b - last
        v, _ = _car(rp, t0, a, b)
        return v, "complete", 0

    d20, d20s, d20left = drift(cfg["drift_short"])
    d60, d60s, d60left = drift(cfg["drift_long"])
    # --- context on day 0
    rng = high[t0] - low[t0]
    clv = (close[t0] - low[t0]) / rng if rng > 0 else None
    sma50 = sum(close[t0 - 49:t0 + 1]) / 50 if t0 >= 49 else None
    sma200 = sum(close[t0 - 199:t0 + 1]) / 200 if t0 >= 199 else None
    day0 = rp.r[t0]
    gap = (gaps or {}).get(dates[t0])
    return {
        "status": "ok",
        "reaction_date_used": dates[t0],
        "sessions_since": last - t0,
        "gap_pct": _r(gap),
        "day0_return_pct": _r(day0 * 100 if day0 is not None else None),
        "day0_abnormal_pct": _r(rp.ar(t0) * 100 if rp.ar(t0) is not None else None),
        "car_3d_pct": _r(car3 * 100 if car3 is not None else None),
        "car_window_days": days3,
        "car_3d_mm_pct": _r(car3_mm * 100 if car3_mm is not None else None),
        "beta": _r(mm[1], 3) if mm else None,
        "beta_obs": len(xy),
        "sigma_daily_pct": _r(sigma * 100 if sigma else None, 3),
        "sigma_obs": len(ars),
        "sigma_source": sigma_source,
        "z": _r(z),
        "z_mm": _r(z_mm),
        "abnormal_turnover": _r(at),
        "abnormal_turnover_3d": _r(at3),
        "turnover_day0_k": _r(turnover_k[t0], 1),
        "median_turnover_k": _r(med, 1),
        "run_up_car_pct": _r(ru * 100 if ru is not None else None),
        "run_up_return_pct": _r(raw_ru * 100 if raw_ru is not None else None),
        "drift_20s_pct": _r(d20 * 100 if d20 is not None else None),
        "drift_20s_status": d20s,
        "drift_20s_sessions_left": d20left,
        "drift_60s_pct": _r(d60 * 100 if d60 is not None else None),
        "drift_60s_status": d60s,
        "drift_60s_sessions_left": d60left,
        "close_location": _r(clv, 3),
        "close": _r(close[t0], 4),
        "vs_sma50_pct": _r((close[t0] / sma50 - 1) * 100 if sma50 else None),
        "vs_sma200_pct": _r((close[t0] / sma200 - 1) * 100 if sma200 else None),
        "above_sma50": (close[t0] > sma50) if sma50 else None,
        "above_sma200": (close[t0] > sma200) if sma200 else None,
        "label": label,
        "provisional": provisional,
        "flags": flags,
    }


# --------------------------------------------------------------------------
# Fundamental surprise (optional, never invented)
# --------------------------------------------------------------------------

def fundamental_surprise(history: Optional[Dict], period_key: str) -> Dict:
    """YoY change of net profit for the event's period from the local
    fundamentals history (gpwlib.fundamentals.save_snapshot store).

    Seasonal random walk: expected profit = same period last year (Foster
    1977; Bernard & Thomas 1989 use the same benchmark for SUE). Without
    several years of history the change cannot be standardised, so this is a
    raw YoY change, not a full SUE. Returns ``{value: None, reason}`` when two
    comparable periods are not available - never a guess, never a consensus.
    """
    m = re.match(r"^(\d{4})(Q1|H1|Q3|Q4)$", period_key or "")
    if not history or not m:
        return {"yoy_net_profit_pct": None, "reason": "no_local_fundamentals_history"}
    year, kind = int(m.group(1)), m.group(2)
    months = {"Q1": 3, "H1": 6, "Q3": 9, "Q4": 12}[kind]
    found: Dict[int, Dict] = {}
    for label, e in (history.get("periods") or {}).items():
        p = e.get("period") or {}
        if p.get("months") != months or p.get("first_quarter", 1) != 1:
            continue
        v = (e.get("values") or {}).get("net_profit")
        if v is None:
            continue
        found[p.get("year")] = {"label": label, "value": v, "consolidated": e.get("consolidated")}
    cur, prev = found.get(year), found.get(year - 1)
    if not cur or not prev:
        return {"yoy_net_profit_pct": None, "reason": "two_comparable_periods_not_in_history",
                "periods_available": sorted(v["label"] for v in found.values())}
    if cur.get("consolidated") != prev.get("consolidated"):
        return {"yoy_net_profit_pct": None, "reason": "consolidation_basis_differs"}
    if prev["value"] == 0:
        return {"yoy_net_profit_pct": None, "reason": "base_period_zero"}
    yoy = (cur["value"] - prev["value"]) / abs(prev["value"]) * 100
    return {"yoy_net_profit_pct": round(yoy, 1), "current": cur, "previous": prev,
            "method": "seasonal random walk, YoY change of year-to-date net profit (not standardised)",
            "reason": None}


# --------------------------------------------------------------------------
# Aggregates
# --------------------------------------------------------------------------

def tstat(xs: Sequence[float]) -> Optional[float]:
    sd = _sd(xs)
    if sd is None or sd == 0:
        return None
    return (sum(xs) / len(xs)) / (sd / math.sqrt(len(xs)))


def _stats(xs: List[float]) -> Dict:
    return {"n": len(xs), "mean_pct": _r(_mean(xs)), "median_pct": _r(_median(xs)),
            "t_stat": _r(tstat(xs)), "share_positive": _r(sum(1 for x in xs if x > 0) / len(xs), 3) if xs else None}


def season_stats(reactions: Sequence[Dict], size_map: Dict[str, str], season_key: str,
                 min_turnover_k: float = 0.0) -> Dict:
    """Market-wide reaction statistics for one reporting season."""
    rs = [r for r in reactions if r.get("period") == season_key and r.get("car_3d_pct") is not None]
    out = {"key": season_key, "events": len(rs)}
    cars = [r["car_3d_pct"] for r in rs]
    out.update({
        "share_positive_car": _r(sum(1 for c in cars if c > 0) / len(cars), 3) if cars else None,
        "mean_car_3d_pct": _r(_mean(cars)),
        "median_car_3d_pct": _r(_median(cars)),
        "t_stat_mean_car": _r(tstat(cars)),
        "labels": {lab: sum(1 for r in rs if r["label"] == lab) for lab in LABELS},
        "intraday_share": _r(sum(1 for r in rs if r.get("intraday")) / len(rs), 3) if rs else None,
        "preliminary_first": sum(1 for r in rs if r.get("preliminary_first")),
    })
    seg = {}
    for key in ("WIG20", "MWIG40", "SWIG80", "other"):
        sub = [r["car_3d_pct"] for r in rs if (size_map.get(r["ticker"]) or "other") == key]
        seg[key] = {"n": len(sub), "mean_car_3d_pct": _r(_mean(sub)), "median_car_3d_pct": _r(_median(sub)),
                    "share_positive": _r(sum(1 for c in sub if c > 0) / len(sub), 3) if sub else None}
    out["by_segment"] = seg
    liquid = [r for r in rs if (r.get("median_turnover_k") or 0) >= min_turnover_k]
    out["liquid_events"] = len(liquid)
    return out


def welch_t(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Welch t-statistic of mean(a) - mean(b)."""
    if len(a) < 2 or len(b) < 2:
        return None
    va, vb = _sd(a) ** 2, _sd(b) ** 2
    se = math.sqrt(va / len(a) + vb / len(b))
    return ((sum(a) / len(a)) - (sum(b) / len(b))) / se if se > 0 else None


def baseline_daily_ar(panels: Iterable["ReturnPanel"], start: str, end: str) -> Dict:
    """Equal-weighted mean daily abnormal return (stock - WIG) over all
    stock-days with start <= date <= end, in %.

    WIG is cap-weighted: when large caps lead, the average stock has a
    negative market-adjusted return on any day, report or not. This baseline
    lets the season average be read against "a normal day for the average
    stock" instead of zero.
    """
    tot, n = 0.0, 0
    for rp in panels:
        for k in range(1, len(rp.dates)):
            if start <= rp.dates[k] <= end:
                a = rp.ar(k)
                if a is not None and abs(a) < 0.5:  # drop >50% days (data errors, re-listings)
                    tot += a
                    n += 1
    return {"mean_daily_ar_pct": _r(tot / n * 100, 4) if n else None, "stock_days": n}


def drift_study(reactions: Sequence[Dict], liquidity_split_k: Optional[float] = None) -> Dict:
    """Average post-event drift CAR[+3,+20] (and [+3,+60]) by reaction label,
    with n and t-stat. Only events whose drift window is complete enter.

    ``liquidity_split_k``: median-turnover threshold for the liquid / illiquid
    split (default: the sample median), to show where drift lives (Chordia et
    al. 2009).
    """
    done = [r for r in reactions if r.get("drift_20s_pct") is not None]
    neutral = [r["drift_20s_pct"] for r in done if r["label"] == "neutral"]
    by_label = []
    for lab in LABELS:
        xs = [r["drift_20s_pct"] for r in done if r["label"] == lab]
        ys = [r["drift_60s_pct"] for r in reactions if r["label"] == lab and r.get("drift_60s_pct") is not None]
        row = {"label": lab}
        row.update(_stats(xs))
        row["drift_60s"] = _stats(ys)
        # control group: neutral reactions over (roughly) the same calendar
        # windows absorb the market-wide drift of the average stock vs WIG
        if lab != "neutral" and xs and neutral:
            row["vs_neutral_pp"] = _r(_mean(xs) - _mean(neutral))
            row["t_vs_neutral"] = _r(welch_t(xs, neutral))
        else:
            row["vs_neutral_pp"], row["t_vs_neutral"] = None, None
        row["low_n"] = len(xs) < 30
        by_label.append(row)
    turns = [r.get("median_turnover_k") for r in done if r.get("median_turnover_k") is not None]
    split = liquidity_split_k if liquidity_split_k is not None else _median(turns)
    by_liq = []
    if split is not None:
        for grp, labs in (("negative", ("negative", "strong_negative")), ("positive", ("positive", "strong_positive"))):
            for bucket in ("liquid", "illiquid"):
                xs = [r["drift_20s_pct"] for r in done if r["label"] in labs and r.get("median_turnover_k") is not None
                      and ((r["median_turnover_k"] >= split) == (bucket == "liquid"))]
                row = {"group": grp, "bucket": bucket}
                row.update(_stats(xs))
                by_liq.append(row)
    pending = sum(1 for r in reactions if r.get("drift_20s_status") == "pending")
    return {"events_with_drift": len(done), "events_pending": pending, "by_label": by_label,
            "liquidity_split_k": _r(split, 1), "by_liquidity": by_liq}


def ticker_profile(reactions: Sequence[Dict]) -> Dict:
    """How one stock usually reacts: mean |CAR|, share positive, label counts."""
    rs = [r for r in reactions if r.get("car_3d_pct") is not None]
    cars = [r["car_3d_pct"] for r in rs]
    d20 = [r["drift_20s_pct"] for r in rs if r.get("drift_20s_pct") is not None]
    return {
        "events": len(rs),
        "mean_car_3d_pct": _r(_mean(cars)),
        "mean_abs_car_3d_pct": _r(_mean([abs(c) for c in cars])),
        "share_positive": _r(sum(1 for c in cars if c > 0) / len(cars), 3) if cars else None,
        "mean_abnormal_turnover": _r(_mean([r["abnormal_turnover"] for r in rs if r.get("abnormal_turnover") is not None])),
        "mean_drift_20s_pct": _r(_mean(d20)),
        "labels": {lab: sum(1 for r in rs if r["label"] == lab) for lab in LABELS},
        "reaction_drift_same_sign": (_r(sum(1 for r in rs if r.get("drift_20s_pct") is not None and r["car_3d_pct"] * r["drift_20s_pct"] > 0)
                                        / len(d20), 3) if d20 else None),
    }


def next_expected(events: Sequence[Dict], as_of: dt.date, known_sessions: Sequence[str] = (),
                  is_probable=None) -> Optional[Dict]:
    """Estimate the next report date of a stock from last year's publication
    of the same period (+1 year, moved to the nearest following session).

    Returns ``{period, date, date_kind: estimated|statutory_deadline, basis}``.
    """
    if not events:
        return None
    by_key = {e["period"]: e for e in events}
    latest = max(events, key=lambda e: e["report_date"])
    nxt = next_period_key(latest["period"])
    prev_year_key = "{}{}".format(int(nxt[:4]) - 1, nxt[4:])
    deadline = statutory_deadline(nxt)
    base = by_key.get(prev_year_key)
    if base:
        d = dt.date.fromisoformat(base["report_date"])
        try:
            cand = d.replace(year=d.year + 1)
        except ValueError:  # 29 Feb
            cand = d + dt.timedelta(days=365)
        # keep the same weekday pattern: move to the nearest session on or after
        for _ in range(10):
            if is_session(cand, known_sessions, is_probable) and cand.weekday() < 5:
                break
            cand += dt.timedelta(days=1)
        if cand > as_of:
            return {"period": nxt, "date": cand.isoformat(), "date_kind": "estimated", "basis_kind": "last_year",
                    "base_date": base["report_date"],
                    "basis": "same period last year published {}".format(base["report_date"]),
                    "statutory_deadline": deadline}
    if deadline and dt.date.fromisoformat(deadline) > as_of:
        # Q4: the only deadline is the annual report (4 months, § 83 ust. 7);
        # since 2025 there is no Q4 (or Q2) quarterly-report obligation
        # (§ 83 ust. 2), so no earlier Q4 date is implied.
        return {"period": nxt, "date": deadline, "date_kind": "statutory_deadline", "basis_kind": "statutory",
                "base_date": None, "basis": "legal deadline ({})".format(LEGAL_BASIS)
                + ("; annual report - no Q4 quarterly report is required (§ 83 ust. 2)" if nxt.endswith("Q4") else ""),
                "statutory_deadline": deadline}
    return None


def make_signals(reactions: Sequence[Dict], held: Iterable[str] = (),
                 raw_close: Optional[Dict[str, Dict[str, float]]] = None) -> List[Dict]:
    """Generic signals for the postmortem ledger.

    Only strong labels emit a signal. A strong negative reaction is ``exit``
    for a held stock and ``avoid`` otherwise; a strong positive one is
    ``long`` (a watchlist candidate, the weaker signal on WSE - Jagliński
    2020 finds drift after negative surprises). ``score`` = 0-100 from |z|
    (z = 2 -> 50, z >= 4 -> 100), +10 when turnover >= 3x median, capped.
    Follows the gpw-signal-postmortem generic contract: ``price`` is the
    unadjusted close on the signal date (from ``raw_close``), details in ``tags``.
    """
    held = {h.upper() for h in held}
    out = []
    for r in reactions:
        lab = r.get("label")
        if lab not in ("strong_negative", "strong_positive") or r.get("z") is None:
            continue
        score = min(100.0, abs(r["z"]) / 4.0 * 100.0)
        if (r.get("abnormal_turnover") or 0) >= 3.0:
            score = min(100.0, score + 10.0)
        if lab == "strong_negative":
            direction = "exit" if r["ticker"].upper() in held else "avoid"
        else:
            direction = "long"
        day = r.get("reaction_date_used") or r["reaction_date"]
        sig = {"ticker": r["ticker"], "date": day, "kind": "earnings_" + lab,
               "direction": direction, "score": round(score, 1),
               "tags": {"car_3d_pct": r.get("car_3d_pct"), "z": r.get("z"), "period": r.get("period"),
                        "abnormal_turnover": r.get("abnormal_turnover"), "provisional": bool(r.get("provisional")),
                        "held": direction == "exit"}}
        px = ((raw_close or {}).get(r["ticker"]) or {}).get(day)
        if px:
            sig["price"] = px
        out.append(sig)
    out.sort(key=lambda s: (s["date"], s["ticker"]))
    return out
