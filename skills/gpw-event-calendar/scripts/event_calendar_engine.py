"""Pure engine of gpw-event-calendar: dates, parsers, rules, window check, iCalendar.

Nothing here touches the network or the clock (``today`` is always passed in),
so every rule is unit-tested. Legal and market rules (verified 2026-11-16,
details and quotes in references/methodology.md):

* Periodic reports - Rozporządzenie Ministra Finansów z 6 czerwca 2025 r. w
  sprawie informacji bieżących i okresowych (Dz.U. 2025 poz. 755, amended by
  Dz.U. 2026 poz. 1205, which does not touch the deadlines). It replaced the
  regulation of 29.03.2018 (Dz.U. 2018 poz. 757) on 25.06.2025.
  - § 83 ust. 1: quarterly report not later than 60 days after the quarter;
  - § 83 ust. 2: no obligation to publish a report for the 2nd and the last
    quarter (except funds);
  - § 83 ust. 5: half-year report within three months after the half-year;
  - § 83 ust. 7: annual report not later than four months after the year end;
  - § 83 ust. 11: a deadline on a statutory holiday or Saturday moves to the
    next working day;
  - § 84 ust. 1: fixed dates for the year are published in a current report
    by the end of the first month of the financial year (January);
  - § 84 ust. 2: a change of date is published at least two days before.
* AGM - KSH art. 395 § 1: the ordinary general meeting within six months
  after the financial year (30 June for a calendar year).
* Dividend - KSH art. 348 § 4: the record date ("dzień dywidendy") falls 5
  days to 3 months after the resolution; without one it is the 5th day.
* Ex-dividend - KDPW, T+2 settlement since 6.10.2014: to be a shareholder on
  record date D a buyer must trade at the latest 2 settlement days before D,
  so the last session with the right is D-2 sessions and the first session
  without it (ex-date, GPW cuts the reference price) is D-1 session.
  Cross-checked on the 2026 GPW archive: PKOBP, PZU, KETY, PEKAO, PKNORLEN
  price adjustments fall exactly on "last day with right + 1 session".
* FW20 futures - GPW standard (WATS, 06.2026): last trading day is the third
  Friday of the expiry month (March, June, September, December); if there is
  no session that day, the preceding session.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from gpwlib import plcal

D = dt.date

EVENT_TYPES = ("periodic_report", "agm", "dividend_record", "dividend_payment", "ex_dividend",
               "index_revision", "mpc_meeting", "macro_release", "msci_review", "other")
DATE_KINDS = ("confirmed", "estimated", "statutory_deadline")
IMPORTANCE = ("high", "medium", "low")
IMP_RANK = {"high": 3, "medium": 2, "low": 1}

# --------------------------------------------------------------------------
# Working days and sessions
# --------------------------------------------------------------------------


def statutory_holidays(year: int) -> set:
    """Polish public holidays (ustawa o dniach wolnych od pracy).

    Built from ``plcal.exchange_holidays`` minus the two exchange-only
    holidays (Good Friday, 31 December). 24 December is a public holiday from
    2025 (amendment of 2024); before that it was an exchange-only holiday.
    """
    days = set(plcal.exchange_holidays(year))
    easter = plcal.easter_sunday(year)
    days.discard(easter - dt.timedelta(days=2))
    days.discard(D(year, 12, 31))
    if year < 2025:
        days.discard(D(year, 12, 24))
    return days


def is_working_day(day: D) -> bool:
    return day.weekday() < 5 and day not in statutory_holidays(day.year)


def roll_to_working_day(day: D) -> D:
    """§ 83 ust. 11: a deadline on a holiday / Saturday / Sunday moves forward."""
    while not is_working_day(day):
        day += dt.timedelta(days=1)
    return day


def is_session(day: D) -> bool:
    return plcal.is_probable_session(day)


def shift_sessions(day: D, n: int) -> D:
    """The n-th session after (n>0) or before (n<0) ``day`` (``day`` itself not counted)."""
    step = 1 if n > 0 else -1
    left = abs(n)
    cur = day
    while left:
        cur += dt.timedelta(days=step)
        if is_session(cur):
            left -= 1
    return cur


def next_sessions(start: D, n: int) -> List[D]:
    """``n`` sessions beginning with the first session on or after ``start``."""
    out, cur = [], start
    while len(out) < n:
        if is_session(cur):
            out.append(cur)
        cur += dt.timedelta(days=1)
    return out


def add_months(day: D, months: int) -> D:
    """Calendar months, clamped to the month end (KC art. 112: 31.12 + 4 m = 30.04)."""
    y, m = divmod(day.month - 1 + months, 12)
    y += day.year
    m += 1
    last = (D(y + (m // 12), m % 12 + 1, 1) - dt.timedelta(days=1)).day
    return D(y, m, min(day.day, last))


def third_friday(year: int, month: int) -> D:
    first = D(year, month, 1)
    offset = (4 - first.weekday()) % 7
    return first + dt.timedelta(days=offset + 14)


def last_business_day(year: int, month: int) -> D:
    day = add_months(D(year, month, 1), 1) - dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day


def last_working_day_of_month(year: int, month: int) -> D:
    day = add_months(D(year, month, 1), 1) - dt.timedelta(days=1)
    while not is_working_day(day):
        day -= dt.timedelta(days=1)
    return day


# --------------------------------------------------------------------------
# Statutory deadlines
# --------------------------------------------------------------------------

LEGAL_REPORTS = "Rozporządzenie MF z 6.06.2025 (Dz.U. 2025 poz. 755) § 83"
LEGAL_AGM = "KSH art. 395 § 1"


def periodic_deadlines(fiscal_year: int) -> List[Dict]:
    """Main-market deadlines for a calendar financial year (§ 83 ust. 1, 5, 7, 11).

    No deadline for Q2 and Q4 (§ 83 ust. 2: no obligation). Annual report of
    ``fiscal_year`` is due in ``fiscal_year + 1``.
    """
    y = fiscal_year
    raw = [
        ("Q1", D(y, 3, 31), D(y, 3, 31) + dt.timedelta(days=60), "§ 83 ust. 1"),
        ("H1", D(y, 6, 30), add_months(D(y, 6, 30), 3), "§ 83 ust. 5"),
        ("Q3", D(y, 9, 30), D(y, 9, 30) + dt.timedelta(days=60), "§ 83 ust. 1"),
        ("FY", D(y, 12, 31), add_months(D(y, 12, 31), 4), "§ 83 ust. 7"),
    ]
    out = []
    for period, end, nominal, para in raw:
        out.append({"period": period, "fiscal_year": y, "period_end": end.isoformat(),
                    "nominal_deadline": nominal.isoformat(),
                    "deadline": roll_to_working_day(nominal).isoformat(),
                    "legal_basis": "{} {} (+ ust. 11)".format(LEGAL_REPORTS.split(" §")[0], para)})
    return out


def agm_deadline(fiscal_year: int) -> D:
    """KSH art. 395 § 1: within six months after the financial year."""
    return add_months(D(fiscal_year, 12, 31), 6)


def deadlines_between(start: D, end: D) -> List[Dict]:
    out = []
    for fy in range(start.year - 1, end.year + 1):
        for d in periodic_deadlines(fy):
            if start <= D.fromisoformat(d["deadline"]) <= end:
                out.append(d)
    return out


def period_of_report(published: D, report_type: Optional[str] = None) -> Tuple[str, int]:
    """Best guess of the period of a periodic report from its publication date.

    Used when ESPI titles are empty (quarterly reports). Calendar FY assumed.
    """
    m, y = published.month, published.year
    if report_type == "R":
        return "FY", y - 1
    if report_type == "P":
        return "H1", y
    if m <= 3:
        return ("Q4" if report_type == "Q" else "FY"), y - 1
    if m <= 6:
        return ("Q1", y) if report_type in (None, "Q") else ("FY", y - 1)
    if m <= 9:
        return ("H1" if report_type != "Q" else "Q2"), y
    return "Q3", y


# --------------------------------------------------------------------------
# Ex-dividend arithmetic
# --------------------------------------------------------------------------

def exdiv_from_record(record: D) -> Dict:
    """Record date D -> last session with right (D-2) and ex-date (D-1)."""
    return {"record_date": record.isoformat(), "last_cum_date": shift_sessions(record, -2).isoformat(),
            "ex_date": shift_sessions(record, -1).isoformat(), "record_is_session": is_session(record)}


def exdiv_from_last_cum(last_cum: D) -> Dict:
    """Last session with the right L -> ex-date (L+1) and record date (L+2 sessions)."""
    return {"last_cum_date": last_cum.isoformat(), "ex_date": shift_sessions(last_cum, 1).isoformat(),
            "record_date": shift_sessions(last_cum, 2).isoformat(), "record_is_session": True}


# Panel adjustment factor = implied previous close / previous close. Ordinary
# dividends on GPW are 1-10% of the price (2025-26 WIG20 payers: 3.8-9.8%);
# factors below 0.75 (cut >25%) are splits or rights issues, factors above 1
# reverse splits. Lower bound 0.995 = panel.ADJ_TOLERANCE (rounding noise).
DIVIDEND_FACTOR_MIN = 0.75
DIVIDEND_FACTOR_MAX = 0.995


def classify_adjustment(factor: float) -> str:
    if DIVIDEND_FACTOR_MIN <= factor < DIVIDEND_FACTOR_MAX:
        return "dividend_likely"
    return "corporate_action"


def exdiv_history(adjustments: Sequence[Dict], prev_close: Optional[Dict[str, float]] = None) -> List[Dict]:
    """Past ex-dates from ``panel`` adjustments (newest first).

    ``prev_close`` maps ex-date -> raw close of the session before; with it
    the implied gross dividend per share is ``prev_close * (1 - factor)``.
    """
    out = []
    for a in adjustments:
        kind = classify_adjustment(a["factor"])
        pc = (prev_close or {}).get(a["date"])
        out.append({"ex_date": a["date"], "factor": a["factor"], "kind": kind,
                    "cut_pct": round((1 - a["factor"]) * 100, 2),
                    "implied_dps": round(pc * (1 - a["factor"]), 2) if (pc and kind == "dividend_likely") else None})
    out.sort(key=lambda r: r["ex_date"], reverse=True)
    return out


# Annual dividends recur at about the same time of year (AGM season). We
# project the last ex-date by 52 weeks (same weekday) and give a +-10 session
# window (two weeks each way), which covered the 2025->2026 shifts of the
# five WIG20 payers checked (0-6 weeks; PEKAO moved 5 weeks -> outside).
NEXT_EXDATE_WINDOW_SESSIONS = 10


def estimate_next_exdate(history: Sequence[Dict], today: D) -> Optional[Dict]:
    divs = [h for h in history if h["kind"] == "dividend_likely"]
    if not divs:
        return None
    last = D.fromisoformat(divs[0]["ex_date"])
    est = last
    while est <= today:
        est = est + dt.timedelta(weeks=52)
    while not is_session(est):
        est += dt.timedelta(days=1)
    years = sorted({h["ex_date"][:4] for h in divs})
    return {"estimated_ex_date": est.isoformat(),
            "window_start": shift_sessions(est, -NEXT_EXDATE_WINDOW_SESSIONS).isoformat(),
            "window_end": shift_sessions(est, NEXT_EXDATE_WINDOW_SESSIONS).isoformat(),
            "based_on": divs[0]["ex_date"], "years_seen": years,
            "confidence": "medium" if len(years) >= 2 else "low"}


def validate_exdate(ex_date: str, history: Sequence[Dict]) -> Optional[bool]:
    """True if the archive shows a dividend-like price cut on ``ex_date``;
    False if the date is past/covered and no cut was seen; None if unknown."""
    for h in history:
        if h["ex_date"] == ex_date:
            return h["kind"] == "dividend_likely"
    return None


# --------------------------------------------------------------------------
# Polish (and English) date extraction
# --------------------------------------------------------------------------

_MONTHS = {
    # genitive (used in dates), nominative, 3-letter abbreviations
    "stycznia": 1, "styczeń": 1, "styczen": 1, "sty": 1,
    "lutego": 2, "luty": 2, "lut": 2,
    "marca": 3, "marzec": 3, "mar": 3,
    "kwietnia": 4, "kwiecień": 4, "kwiecien": 4, "kwi": 4,
    "maja": 5, "maj": 5,
    "czerwca": 6, "czerwiec": 6, "cze": 6,
    "lipca": 7, "lipiec": 7, "lip": 7,
    "sierpnia": 8, "sierpień": 8, "sierpien": 8, "sie": 8,
    "września": 9, "wrzesnia": 9, "wrzesień": 9, "wrzesien": 9, "wrz": 9,
    "października": 10, "pazdziernika": 10, "październik": 10, "paź": 10, "paz": 10,
    "listopada": 11, "listopad": 11, "lis": 11,
    "grudnia": 12, "grudzień": 12, "grudzien": 12, "gru": 12,
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "april": 4, "apr": 4, "may": 5,
    "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
          "XI": 11, "XII": 12}
_MNAMES = "|".join(sorted((re.escape(k) for k in _MONTHS), key=len, reverse=True))
_RX_WORD = re.compile(r"(?<!\d)(\d{1,2})(?:\s*-?\s*(?:go|ego))?\s*(?:\.\s*)?(" + _MNAMES + r")\.?\s*(\d{4}|\d{2})(?!\d)",
                      re.I | re.U)
_RX_EN = re.compile(r"\b(" + _MNAMES + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)
_RX_NUM = re.compile(r"(?<![\d.])(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?![\d])")
_RX_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_RX_ROMAN = re.compile(r"(?<!\d)(\d{1,2})\s*[. ]\s*(XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)\s*[. ]\s*(\d{4})(?!\d)")


def normalize_text(text: str) -> str:
    t = (text or "").replace("\u00a0", " ").replace("\u2013", "-").replace("\u2014", "-")
    t = t.replace("_", " ")
    return " ".join(t.split())


def _mk(y: int, m: int, d: int) -> Optional[D]:
    try:
        return D(y, m, d)
    except ValueError:
        return None


def find_dates(text: str, allow_two_digit_year: bool = False) -> List[Tuple[D, int, int]]:
    """All dates in ``text`` as ``(date, start, end)``, in text order, no overlaps.

    Formats: "5 marca 2026 r.", "05 marca 2026", "5-go maja 2026", "13 lip 26"
    (two-digit year only with ``allow_two_digit_year``), "10 December 2026",
    "December 10, 2026", "05.03.2026", "5-03-2026", "5/3/2026", "2026-03-05",
    "5.III.2026". Ambiguous numeric dates are read day-first (Polish usage).
    """
    t = text or ""
    found: List[Tuple[D, int, int]] = []
    for m in _RX_WORD.finditer(t):
        mon = _MONTHS.get(m.group(2).lower())
        ys = m.group(3)
        if len(ys) == 2:
            if not allow_two_digit_year:
                continue
            y = 2000 + int(ys)
        else:
            y = int(ys)
        d = _mk(y, mon, int(m.group(1))) if mon else None
        if d:
            found.append((d, m.start(), m.end()))
    for m in _RX_EN.finditer(t):
        mon = _MONTHS.get(m.group(1).lower())
        d = _mk(int(m.group(3)), mon, int(m.group(2))) if mon else None
        if d:
            found.append((d, m.start(), m.end()))
    for m in _RX_ISO.finditer(t):
        d = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            found.append((d, m.start(), m.end()))
    for m in _RX_NUM.finditer(t):
        d = _mk(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if d:
            found.append((d, m.start(), m.end()))
    for m in _RX_ROMAN.finditer(t):
        d = _mk(int(m.group(3)), _ROMAN[m.group(2)], int(m.group(1)))
        if d:
            found.append((d, m.start(), m.end()))
    found.sort(key=lambda x: (x[1], -(x[2] - x[1])))
    out: List[Tuple[D, int, int]] = []
    for item in found:
        if out and item[1] < out[-1][2]:
            continue
        out.append(item)
    return out


_RX_TIME = re.compile(r"(?:godz(?:\.|inie|inę|iny)?|o\s+godz\.?|\bat\b)\s*(\d{1,2})[:.](\d{2})", re.I)


def find_time(text: str) -> Optional[str]:
    m = _RX_TIME.search(text or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return "{:02d}:{:02d}".format(h, mi)


# --------------------------------------------------------------------------
# Periodic-report schedule ("Terminy przekazywania raportów okresowych")
# --------------------------------------------------------------------------

_ROMAN_Q = {"I": 1, "II": 2, "III": 3, "IV": 4}
_WORD_Q = {"pierwsz": 1, "drug": 2, "trzec": 3, "trzech": 3, "trzy": 3, "czwart": 4,
           "first": 1, "second": 2, "third": 3, "fourth": 4}
_RX_HALF = re.compile(r"półroczn|polroczn|(?<!\w)I\s*półrocz|(?<!\w)1\s*półrocz|\bH1\b|\b1H\b|\bPSr?\b|"
                      r"half[- ]?year|semi-?annual|interim\s+management", re.I)
_RX_Q = re.compile(
    r"(?<![\w])(IV|I{1,3}|[1-4])\s*(?:\.|-?go|-?ego)?\s*(?:kw\b\.?|kw\.|kwarta\w*)|"
    r"(pierwsz|drug|trzec|trzech|trzy|czwart|first|second|third|fourth)\w*\s+(?:kwarta\w*|quarter)|"
    r"\bQ([1-4])\b|\b([1-4])\s?Q\b|\b(IV|I{1,3})\s?Q\b|"
    r"(kwartaln\w*|quarterly)", re.I)
_RX_FY = re.compile(r"roczn|\bS?RR\b|\bannual\b|\bza\s+(?:rok\s+(?:obrotowy\s+)?)?20\d\d(?:\s*(?:rok|r\.))?|"
                    r"full[- ]year", re.I)


def _q_number(m: re.Match) -> Optional[int]:
    g = m.groups()
    if g[0]:
        return int(g[0]) if g[0].isdigit() else _ROMAN_Q.get(g[0].upper())
    if g[1]:
        return _WORD_Q.get(g[1].lower())
    if g[2]:
        return int(g[2])
    if g[3]:
        return int(g[3])
    if g[4]:
        return _ROMAN_Q.get(g[4].upper())
    return None


def _period_mentions(seg: str) -> List[Tuple[int, str]]:
    """(position, period) for every period mention in ``seg``."""
    out: List[Tuple[int, str]] = []
    half_spans = []
    for m in _RX_HALF.finditer(seg):
        out.append((m.start(), "H1"))
        half_spans.append((m.start(), m.end()))
    for m in _RX_Q.finditer(seg):
        if any(a <= m.start() < b for a, b in half_spans):
            continue
        n = _q_number(m)
        out.append((m.start(), "Q{}".format(n) if n else "Q"))
    for m in _RX_FY.finditer(seg):
        if any(a <= m.start() < b for a, b in half_spans):
            continue
        # "półroczny" contains "roczn": skip when it is part of a half-year word
        if seg[max(0, m.start() - 3):m.start()].lower().endswith(("pół", "pol")):
            continue
        out.append((m.start(), "FY"))
    out.sort()
    return out


def _fiscal_year(period: str, report_date: D) -> int:
    if period == "FY":
        return report_date.year - 1
    if period == "Q4" and report_date.month <= 4:
        return report_date.year - 1
    return report_date.year


def parse_schedule_text(text: str, published: Optional[D] = None) -> List[Dict]:
    """Report dates from a schedule or date-change report body.

    For each date the period is the last period mention between the previous
    date and this one (Polish lists put the period first: "raport roczny za
    2025 r. - 5 marca 2026 r."), else the first mention after it ("5 marca
    2026 r. - raport roczny"). Dates before ``published`` (references to
    earlier reports, "z dnia ...") are dropped. One date per period: in
    change reports the new date comes after "na dzień" / "nowy termin", so
    for duplicated periods the date following such a phrase wins, else the
    last one.
    """
    t = normalize_text(text)
    dates = find_dates(t)
    found: Dict[str, Dict] = {}
    # layout: "21.04.2026 - raport roczny; 27.05.2026 - raport za 1Q" (date first)
    # is recognised when the first date has no period before it but one after
    date_first = False
    valid = [i for i, x in enumerate(dates) if not published or x[0] >= published]
    if valid:
        i0 = valid[0]
        _d0, s0, e0 = dates[i0]
        start0 = dates[i0 - 1][2] if i0 > 0 else 0
        n0 = dates[i0 + 1][1] if i0 + 1 < len(dates) else len(t)
        date_first = not _period_mentions(t[start0:s0]) and bool(_period_mentions(t[e0:n0]))
    prev_end = 0
    for i, (d, s, e) in enumerate(dates):
        if published and d < published:
            prev_end = e
            continue
        if date_first:
            nxt = dates[i + 1][1] if i + 1 < len(dates) else len(t)
            after = _period_mentions(t[e:nxt])
            spec = [p for p in after if p[1] != "Q"]
            period = (spec or after)[0][1] if after else None
            before = []
        else:
            before = _period_mentions(t[prev_end:s])
            specific = [p for p in before if p[1] != "Q"]
            period = (specific or before)[-1][1] if before else None
        if period is None and not date_first:
            nxt = dates[i + 1][1] if i + 1 < len(dates) else len(t)
            after = _period_mentions(t[e:nxt])
            period = after[0][1] if after else None
        if period is None:
            # change notices name the period once, before the reference date
            # ("raport za III kwartał ..., z dnia 20 stycznia ... Nowy termin: ...")
            earlier = [p for p in _period_mentions(t[:s]) if p[1] != "Q"] or _period_mentions(t[:s])
            period = earlier[-1][1] if earlier else None
        prev_end = e
        if not period:
            continue
        lead = t[max(0, s - 40):s].lower()
        is_new = bool(re.search(r"na\s+dzień|nowy\s+termin|nowym\s+terminem|nowa\s+data|zmienia\w*\s+.*na\b|"
                                r"\bto\b|\bon\b|przesun\w*\s+.*na", lead))
        is_old = bool(re.search(r"pierwotn|dotychczas|zamiast|poprzedni|wcześniej\s+wskaz|z\s+dnia\s*$|"
                                r"originally|previously", lead))
        key = period
        rec = {"period": period, "date": d.isoformat(), "fiscal_year": _fiscal_year(period, d),
               "new": is_new, "old": is_old}
        cur = found.get(key)
        if rec["old"]:
            if cur is None:
                found[key] = rec
        elif cur is None or cur["old"] or rec["new"] or not cur["new"]:
            found[key] = rec
    # a period seen only with an "old/original" date is not reported
    out = [dict(period=r["period"], date=r["date"], fiscal_year=r["fiscal_year"]) for r in found.values()
           if not r["old"]]
    out.sort(key=lambda r: r["date"])
    return out


# --------------------------------------------------------------------------
# Dividends
# --------------------------------------------------------------------------

_AMOUNT = r"(\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,4})?|\d+(?:[,.]\d{1,4})?)"
_RX_DPS = [
    re.compile(_AMOUNT + r"\s*(zł|złotych|zlotych|PLN|gr|groszy|EUR|euro)\b[^.;]{0,60}?(?:na\s+(?:jedną\s+|każdą\s+|1\s+)?akcj|per\s+share|/\s*akcj)", re.I),
    re.compile(r"(?:na\s+(?:jedną\s+|każdą\s+|1\s+)?akcj\w*|per\s+share|dywidend\w*\s+jednostkow\w*|DPS)[^.;\d]{0,50}?"
               + _AMOUNT + r"\s*(zł|złotych|zlotych|PLN|gr|groszy|EUR|euro)\b", re.I),
    re.compile(r"\b(PLN|EUR)\s*" + _AMOUNT + r"[^.;]{0,30}?per\s+share", re.I),
]
_RX_RECORD = re.compile(r"dzie[nń]\w*\s+dywidendy|dniem\s+dywidendy|dzie[nń]\w*\s+ustalenia\s+prawa|"
                        r"ustalenia\s+prawa\s+do\s+dywidendy|record\s+date|dividend\s+date", re.I)
_RX_PAY = re.compile(r"termin\w*\s+wypłat\w*|dzie[nń]\w*\s+wypłat\w*|dniem\s+wypłat\w*|data\s+wypłat\w*|"
                     r"wypłat\w*\s+(?:dywidendy\s+|zaliczki\s+)?(?:nastąpi|zostanie\s+dokonana|odbędzie|będzie)|"
                     r"wypłacon\w*\s+(?:w\s+dniu|dnia)|payment\s+date|(?:will\s+be\s+)?paid\s+(?:out\s+)?on", re.I)
_RX_LAST_CUM = re.compile(r"ostatni\w*\s+(?:dzień\s+)?notowani\w*\s+z\s+prawem|last\s+(?:trading\s+)?day\s+with", re.I)


def _num(s: str) -> Optional[float]:
    try:
        return float(s.replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except ValueError:
        return None


def parse_dps(text: str) -> Optional[Dict]:
    t = normalize_text(text)
    for i, rx in enumerate(_RX_DPS):
        for m in rx.finditer(t):
            if i == 2:
                cur, amt = m.group(1), m.group(2)
            else:
                amt, cur = m.group(1), m.group(2)
            val = _num(amt)
            if val is None:
                continue
            cur_l = cur.lower()
            if cur_l in ("gr", "groszy"):
                val, cur_n = val / 100.0, "PLN"
            elif cur_l in ("eur", "euro"):
                cur_n = "EUR"
            else:
                cur_n = "PLN"
            if val <= 0 or val > 10000:  # a per-share amount, not a total payout
                continue
            return {"dps": round(val, 4), "currency": cur_n}
    return None


def _dates_after(t: str, dates: List[Tuple[D, int, int]], pos: int, span: int, stop: Optional[re.Pattern] = None,
                 limit: int = 1) -> List[D]:
    end = pos + span
    if stop is not None:
        sm = stop.search(t, pos)
        if sm and sm.start() < end:
            end = sm.start()
    return [d for d, s, _e in dates if pos <= s < end][:limit]


def dividend_stage(title: str, body: str = "") -> str:
    tt = normalize_text(title).lower()
    tb = normalize_text(body).lower()
    both = tt + " " + tb[:600]
    if re.search(r"zaliczk|interim\s+dividend|advance\s+(?:payment|dividend)", both):
        return "interim"
    if re.search(r"polityk\w*\s+dywidend|dividend\s+policy", tt):
        return "policy"
    if re.search(r"rekomendac|wnios\w*\s+zarząd|propozycj|zamiar|propon|zaproponow|recommend|propos", tt):
        return "proposal"
    if re.search(r"uchwał|podjęł|zdecydowa|postanowi|walne\s+zgromadzenie\s+.{0,40}(?:przeznacz|wypłat)|resolv|adopt", both):
        return "resolution"
    if re.search(r"rekomendac|wnios\w*\s+zarząd|propozycj|propon|recommend", tb[:600]):
        return "proposal"
    return "other"


def parse_dividend_text(title: str, body: str = "", published: Optional[D] = None) -> Dict:
    """DPS, record date, payment date(s) and stage from a dividend report.

    Dates are taken only within 100-160 characters after their keyword, so a
    date of the AGM or of the report itself is not mistaken for the record
    date. Ex-date and last day with right are derived from the record date
    (T+2, see module docstring).
    """
    text = normalize_text((title or "") + ". " + (body or ""))
    dates = find_dates(text)
    out: Dict = {"stage": dividend_stage(title, body), "dps": None, "currency": None, "record_date": None,
                 "payment_dates": [], "last_cum_date": None, "ex_date": None}
    dps = parse_dps(text)
    if dps:
        out.update(dps)
    for m in _RX_RECORD.finditer(text):
        got = _dates_after(text, dates, m.end(), 100, stop=_RX_PAY)
        if got:
            out["record_date"] = got[0].isoformat()
            break
    pays: List[D] = []
    for m in _RX_PAY.finditer(text):
        for d in _dates_after(text, dates, m.end(), 160, stop=_RX_RECORD, limit=3):
            if d not in pays:
                pays.append(d)
        if pays:
            break
    out["payment_dates"] = [d.isoformat() for d in sorted(pays)]
    for m in _RX_LAST_CUM.finditer(text):
        got = _dates_after(text, dates, m.end(), 80)
        if got:
            out["last_cum_date"] = got[0].isoformat()
            break
    if out["record_date"]:
        x = exdiv_from_record(D.fromisoformat(out["record_date"]))
        out["ex_date"] = x["ex_date"]
        out["last_cum_date"] = out["last_cum_date"] or x["last_cum_date"]
    elif out["last_cum_date"]:
        x = exdiv_from_last_cum(D.fromisoformat(out["last_cum_date"]))
        out["ex_date"], out["record_date"] = x["ex_date"], x["record_date"]
    fy = re.search(r"za\s+(?:rok\s+(?:obrotowy\s+)?)?(20\d\d)|(?:for|financial\s+year)\s+(20\d\d)", text, re.I)
    out["fiscal_year"] = int(fy.group(1) or fy.group(2)) if fy else None
    return out


# --------------------------------------------------------------------------
# General meetings
# --------------------------------------------------------------------------

_RX_AGM_LEAD = re.compile(r"(?:na\s+dzień|zwołuje\w*|zwołan\w*\s+(?:na\s+)?(?:dzień\s+)?|odbędzie\s+się\s+(?:w\s+dniu\s+)?|"
                          r"w\s+dniu|\bna\b|held\s+on|to\s+be\s+held\s+on|convened\s+(?:for|on)|\bon\b)\s*$", re.I)


def parse_agm_text(title: str, body: str = "", published: Optional[D] = None) -> Dict:
    """Meeting date (+ time) of a general-meeting call, from the title first.

    A date counts when it follows a lead phrase ("na dzień", "zwołuje na",
    "odbędzie się w dniu", "held on") or, failing that, is the first date on
    or after the publication day. KSH art. 402^1: a listed company calls the
    meeting at least 26 days ahead, so a date before publication is never
    the meeting date.
    """
    kind_src = normalize_text(title + " " + body[:300]).lower()
    if re.search(r"nadzwyczajn|\bnwz|extraordinary", kind_src) and not re.search(r"zwyczajne\s+i\s+nadzw|annual\s+and", kind_src):
        kind = "extraordinary"
    elif re.search(r"zwyczajn|\bzwz|annual", kind_src):
        kind = "annual"
    else:
        kind = "general"
    for src in (title or "", body or ""):
        t = normalize_text(src)
        dates = find_dates(t)
        cand = [x for x in dates if not published or x[0] >= published]
        best = None
        for d, s, e in cand:
            if _RX_AGM_LEAD.search(t[max(0, s - 40):s]):
                best = (d, s, e)
                break
        if best is None and cand:
            best = cand[0]
        if best:
            tm = find_time(t[best[2]:best[2] + 60])
            return {"date": best[0].isoformat(), "time": tm, "kind": kind}
    return {"date": None, "time": None, "kind": kind}


# --------------------------------------------------------------------------
# GPW Benchmark "Kalendarium indeksów"
# --------------------------------------------------------------------------

_RX_HELP = re.compile(r"<h2>([^<]*)</h2>\s*<table class=\"tab09\">(.*?)</table>", re.S | re.I)
_RX_ITEM = re.compile(r"<tr>\s*<td>\s*<img[^>]*src=\"([^\"]*)\"[^>]*>\s*</td>\s*<td[^>]*>(.*?)</td>\s*</tr>", re.S | re.I)
_RX_HREF = re.compile(r"href=\"([^\"]+)\"")


def _strip(fragment: str) -> str:
    import html as _h
    return " ".join(_h.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def classify_index_item(text: str) -> Dict:
    t = text.lower()
    if "bez sesji" in t:
        cat = "no_session"
    elif "obligacji" in t:
        cat = "bonds"
    elif "mwo" in t or "wskaźnika obrotu" in t:
        cat = "turnover_threshold"
    elif t.startswith("ranking"):
        cat = "ranking"
    elif "ogłoszenie zmian" in t:
        cat = "announcement"
    elif "rewizja roczna" in t:
        cat = "annual_revision"
    elif "rewizja kwartalna" in t:
        cat = "quarterly_revision"
    elif "korekta kwartalna" in t:
        cat = "quarterly_adjustment"
    else:
        cat = "other"
    if "wig20" in t:
        fam = "size"          # WIG20 / mWIG40 / sWIG80 / WIG30
    elif "wigdiv" in t:
        fam = "dividend"
    elif "ncindex" in t or "ncfindex" in t:
        fam = "newconnect"
    elif re.search(r"indeks\w*\s+wig\b|indeksów\s+wig,", t):
        fam = "wig"
    elif "cee" in t or "gpwb-centr" in t:
        fam = "cee"
    else:
        fam = "other"
    # Importance: index funds and FW20 track WIG20/mWIG40/sWIG80, so changes
    # of those portfolios move flows (announcement and effective date = high);
    # the ranking is only the data cut-off (medium). WIG-family revisions
    # matter for small caps (medium); niche families are low.
    if fam == "size" and cat in ("announcement", "annual_revision", "quarterly_revision", "quarterly_adjustment"):
        imp = "high"
    elif (fam == "size" and cat == "ranking") or (fam == "wig" and cat in ("announcement", "quarterly_revision",
                                                                           "annual_revision")):
        imp = "medium"
    else:
        imp = "low"
    return {"category": cat, "family": fam, "importance": imp}


def parse_index_calendar_html(html: str) -> List[Dict]:
    """Items of gpwbenchmark.pl/kalendarium-indeksow: ``[{date, text, category, family, importance, href}]``.

    Each day with events carries a hidden help box ``<h2>03 grudnia 2026</h2>``
    followed by a ``tab09`` table with one row per event (icon + text,
    sometimes a link to the announcement). Bond-index items are kept with
    category ``bonds`` so callers can drop them.
    """
    out, seen = [], set()
    for h2, body in _RX_HELP.findall(html or ""):
        ds = find_dates(h2)
        if not ds:
            continue
        day = ds[0][0]
        for _src, cell in _RX_ITEM.findall(body):
            text = _strip(cell)
            if not text:
                continue
            key = (day, text)
            if key in seen:
                continue
            seen.add(key)
            hm = _RX_HREF.search(cell)
            item = {"date": day.isoformat(), "text": text, "href": hm.group(1) if hm else None}
            item.update(classify_index_item(text))
            out.append(item)
    out.sort(key=lambda r: (r["date"], r["text"]))
    return out


# --------------------------------------------------------------------------
# biznesradar.pl /dywidendy/ (optional, personal local use)
# --------------------------------------------------------------------------

_RX_BR_ROW = re.compile(r"<tr>\s*<td>\s*<a href=\"/dywidenda/([^\"]+)\"[^>]*>([^<]*)</a>\s*</td>(.*?)</tr>", re.S)
_RX_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def parse_biznesradar_dividends(html: str) -> List[Dict]:
    """Rows of biznesradar.pl/dywidendy/: ticker (GPW short name), year, AGM
    date, last session with the right, payment date, DPS, status."""
    out = []
    for code, label, rest in _RX_BR_ROW.findall(html or ""):
        cells = [_strip(c) for c in _RX_TD.findall(rest)]
        if len(cells) < 5:
            continue
        m = re.search(r"\(([^)]+)\)", label)
        name = (m.group(1) if m else label).strip().upper()

        def _d(s):
            ds = find_dates(s, allow_two_digit_year=True)
            return ds[0][0].isoformat() if ds else None
        dps = None
        dm = re.search(r"(\d+(?:[,.]\d+)?)\s*(PLN|EUR|USD)", cells[4])
        if dm:
            dps = {"dps": _num(dm.group(1)), "currency": dm.group(2)}
        status = cells[-1].strip().lower() if cells else ""
        out.append({"br_code": code.strip(), "ticker": name, "fiscal_year": int(cells[0]) if cells[0].isdigit() else None,
                    "agm_date": _d(cells[1]), "last_cum_date": _d(cells[2]), "payment_date": _d(cells[3]),
                    "dps": dps["dps"] if dps else None, "currency": dps["currency"] if dps else None,
                    "status": status or None})
    return out


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------

def make_event(*, type: str, date, title: str, source: str, date_kind: str = "confirmed",
               ticker: Optional[str] = None, isin: Optional[str] = None, importance: str = "medium",
               time: Optional[str] = None, subtype: Optional[str] = None, source_url: Optional[str] = None,
               company: Optional[str] = None, details: Optional[Dict] = None) -> Dict:
    if type not in EVENT_TYPES:
        raise ValueError("unknown event type {}".format(type))
    if date_kind not in DATE_KINDS:
        raise ValueError("unknown date_kind {}".format(date_kind))
    ds = date.isoformat() if isinstance(date, D) else str(date)
    D.fromisoformat(ds)
    key = "|".join([ticker or "-", type, subtype or "", ds, title[:60]])
    return {
        "id": hashlib.sha1(key.encode("utf-8")).hexdigest()[:12],
        "ticker": ticker, "isin": isin, "company": company, "type": type, "subtype": subtype,
        "date": ds, "time": time, "date_kind": date_kind, "importance": importance,
        "title": title, "source": source, "source_url": source_url, "details": details or {},
    }


_KIND_RANK = {"confirmed": 3, "estimated": 1, "statutory_deadline": 2}


def dedupe_events(events: Iterable[Dict]) -> List[Dict]:
    """One event per (ticker, type, subtype, period/date); confirmed beats deadline beats estimate.

    Periodic reports are keyed by period (``details.period`` + fiscal year)
    so a confirmed date replaces the statutory deadline of the same report.
    """
    best: Dict[tuple, Dict] = {}
    for e in events:
        det = e.get("details") or {}
        if e["type"] == "periodic_report" and det.get("period"):
            key = (e["ticker"], e["type"], det.get("period"), det.get("fiscal_year"))
        elif e["type"] in ("ex_dividend", "dividend_record", "dividend_payment") and e["date_kind"] == "estimated":
            key = (e["ticker"], e["type"], "est", e["date"][:4])
        else:
            key = (e["ticker"], e["type"], e.get("subtype"), e["date"], e["title"][:40])
        cur = best.get(key)
        if cur is None:
            best[key] = e
            continue
        a = (_KIND_RANK[e["date_kind"]], IMP_RANK[e["importance"]], len(e.get("details") or {}))
        b = (_KIND_RANK[cur["date_kind"]], IMP_RANK[cur["importance"]], len(cur.get("details") or {}))
        if a > b:
            best[key] = e
    out = list(best.values())
    # an estimated dividend window is dropped when a confirmed date of the same kind exists that year
    conf = {(e["ticker"], e["type"], e["date"][:4]) for e in out
            if e["type"] in ("ex_dividend", "dividend_record", "dividend_payment") and e["date_kind"] == "confirmed"}
    out = [e for e in out if not (e["date_kind"] == "estimated" and (e["ticker"], e["type"], e["date"][:4]) in conf)]
    return sort_events(out)


def sort_events(events: Iterable[Dict]) -> List[Dict]:
    return sorted(events, key=lambda e: (e["date"], e.get("time") or "99", -IMP_RANK[e["importance"]],
                                         e.get("ticker") or "", e["type"]))


def filter_events(events: Iterable[Dict], start: Optional[D] = None, end: Optional[D] = None,
                  min_importance: str = "low", tickers: Optional[Iterable[str]] = None,
                  include_market: bool = True) -> List[Dict]:
    tick = {t.upper() for t in tickers} if tickers is not None else None
    out = []
    for e in events:
        d = D.fromisoformat(e["date"])
        if start and d < start:
            continue
        if end and d > end:
            continue
        if IMP_RANK[e["importance"]] < IMP_RANK[min_importance]:
            continue
        if tick is not None:
            if e["ticker"] is None:
                if not include_market:
                    continue
            elif e["ticker"].upper() not in tick:
                continue
        out.append(e)
    return out


# --------------------------------------------------------------------------
# Holding-window check (consumed by gpw-pre-trade-gate)
# --------------------------------------------------------------------------

WINDOW_FLAGS = {
    "earnings": ("periodic_report",),
    "ex_dividend": ("ex_dividend", "dividend_record"),
    "agm": ("agm",),
    "mpc": ("mpc_meeting",),
    "expiry": (),
    "index_revision": ("index_revision", "msci_review"),
    "macro": ("macro_release",),
}


def holding_window_check(events: Sequence[Dict], ticker: str, start: D, n_sessions: int,
                         market_min_importance: str = "medium") -> Dict:
    """Does a holding window of ``n_sessions`` from ``start`` contain events?

    Company events of ``ticker`` count at any importance; market events
    (ticker null) from ``market_min_importance`` up. ``verdict``:
    ``event_risk`` - a confirmed or deadline earnings report, or a confirmed
    ex-dividend date, falls inside; ``caution`` - only estimated company
    events or high-importance market events; ``clear`` - nothing relevant.
    """
    sessions = next_sessions(start, n_sessions)
    w0, w1 = sessions[0], sessions[-1]
    t = ticker.upper()
    inside = []
    for e in events:
        d = D.fromisoformat(e["date"])
        if not (w0 <= d <= w1):
            continue
        if e["ticker"] is not None and e["ticker"].upper() != t:
            continue
        if e["ticker"] is None and IMP_RANK[e["importance"]] < IMP_RANK[market_min_importance]:
            continue
        inside.append(e)
    flags = {k: False for k in WINDOW_FLAGS}

    def counts(e):
        # an index *ranking* is only the data cut-off, not a portfolio change
        return not (e["type"] == "index_revision" and e.get("subtype") in ("ranking", "turnover_threshold"))
    for e in inside:
        if not counts(e):
            continue
        for k, types in WINDOW_FLAGS.items():
            if e["type"] in types:
                flags[k] = True
        if e["type"] == "other" and (e.get("subtype") or "").endswith("expiry"):
            flags["expiry"] = True
    hard = [e for e in inside if e["ticker"] is not None and (
        (e["type"] == "periodic_report" and e["date_kind"] in ("confirmed", "statutory_deadline"))
        or (e["type"] in ("ex_dividend",) and e["date_kind"] == "confirmed"))]
    soft = [e for e in inside if e not in hard and (e["ticker"] is not None or e["importance"] == "high")]
    verdict = "event_risk" if hard else ("caution" if soft else "clear")
    first = {}
    for e in sort_events(inside):
        if not counts(e):
            continue
        for k, types in WINDOW_FLAGS.items():
            if (e["type"] in types or (k == "expiry" and e["type"] == "other" and (e.get("subtype") or "").endswith("expiry"))) and k not in first:
                first[k] = {"date": e["date"], "date_kind": e["date_kind"], "title": e["title"],
                            "sessions_from_start": sessions.index(D.fromisoformat(e["date"])) + 1
                            if D.fromisoformat(e["date"]) in sessions else None}
    return {
        "ticker": t, "start": w0.isoformat(), "end": w1.isoformat(), "sessions": n_sessions,
        "contains": {"has_" + k: v for k, v in flags.items()},
        "first": first,
        "verdict": verdict,
        "event_ids": [e["id"] for e in sort_events(inside)],
        "n_events": len(inside),
    }


# --------------------------------------------------------------------------
# iCalendar (RFC 5545), stdlib only
# --------------------------------------------------------------------------

def _ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> List[str]:
    """Fold at 75 octets (RFC 5545 3.1), never splitting a UTF-8 character."""
    out, cur, size = [], "", 0
    limit = 75
    for ch in line:
        b = len(ch.encode("utf-8"))
        if size + b > limit:
            out.append(cur)
            cur, size, limit = " " + ch, 1 + b, 75
        else:
            cur += ch
            size += b
    out.append(cur)
    return out


KIND_LABEL_ICS = {"confirmed": "", "estimated": "[szac.] ", "statutory_deadline": "[termin ustawowy] "}


def to_ics(events: Sequence[Dict], stamp: dt.datetime, calname: str = "GPW events") -> str:
    """All-day VEVENTs (timed when ``time`` is known, Europe/Warsaw local)."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//gpw-trading-skills//gpw-event-calendar//PL",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:" + _ics_escape(calname),
             "X-WR-TIMEZONE:Europe/Warsaw",
             # minimal VTIMEZONE so TZID=Europe/Warsaw is self-contained (EU DST rules)
             "BEGIN:VTIMEZONE", "TZID:Europe/Warsaw",
             "BEGIN:DAYLIGHT", "TZOFFSETFROM:+0100", "TZOFFSETTO:+0200", "TZNAME:CEST",
             "DTSTART:19700329T020000", "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU", "END:DAYLIGHT",
             "BEGIN:STANDARD", "TZOFFSETFROM:+0200", "TZOFFSETTO:+0100", "TZNAME:CET",
             "DTSTART:19701025T030000", "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU", "END:STANDARD",
             "END:VTIMEZONE"]
    st = stamp.strftime("%Y%m%dT%H%M%SZ")
    for e in events:
        d = D.fromisoformat(e["date"])
        lines.append("BEGIN:VEVENT")
        lines.append("UID:{}@gpw-trading-skills".format(e["id"]))
        lines.append("DTSTAMP:" + st)
        if e.get("time"):
            hh, mm = e["time"].split(":")[:2]
            lines.append("DTSTART;TZID=Europe/Warsaw:{}T{}{}00".format(d.strftime("%Y%m%d"), hh, mm))
            lines.append("DURATION:PT1H")
        else:
            lines.append("DTSTART;VALUE=DATE:" + d.strftime("%Y%m%d"))
            lines.append("DTEND;VALUE=DATE:" + (d + dt.timedelta(days=1)).strftime("%Y%m%d"))
        summ = "{}{}{}".format(KIND_LABEL_ICS.get(e["date_kind"], ""), (e["ticker"] + ": ") if e.get("ticker") else "",
                               e["title"])
        lines.append("SUMMARY:" + _ics_escape(summ))
        desc = "type={} date_kind={} importance={} source={}".format(e["type"], e["date_kind"], e["importance"],
                                                                     e["source"])
        if e.get("source_url"):
            desc += " " + e["source_url"]
        lines.append("DESCRIPTION:" + _ics_escape(desc))
        lines.append("CATEGORIES:" + _ics_escape(e["type"]))
        lines.append("TRANSP:TRANSPARENT")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    folded = []
    for ln in lines:
        folded.extend(_fold(ln))
    return "\r\n".join(folded) + "\r\n"
