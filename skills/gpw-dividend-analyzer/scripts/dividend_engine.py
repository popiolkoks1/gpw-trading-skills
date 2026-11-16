"""Pure engine of gpw-dividend-analyzer: reconstruction, parsing, metrics, scores.

Nothing here touches the network or the clock (``as_of`` is always passed
in), so every rule is unit-tested. Sources and the reasoning behind each
threshold are in references/methodology.md; the short version:

* Dividend from the archive - GPW cuts the reference price on the ex-date by
  the dividend (Regulamin Giełdy / Szczegółowe Zasady Obrotu Giełdowego,
  reference-price adjustment). The archive publishes the raw close and the
  % change versus the reference price, so the reference price is
  ``close / (1 + change% / 100)`` and the gross dividend per share is
  ``previous close - reference price`` (``gpwlib.panel`` reports the ratio
  of the two as an adjustment factor). ``panel`` only reports cuts larger
  than 0.5% of the price (``panel.ADJ_TOLERANCE``), so smaller dividends are
  invisible in the archive and come only from ESPI (or biznesradar).
* Ex-date (KDPW, T+2 since 6.10.2014): last session with the right =
  record date - 2 sessions; ex-date = record date - 1 session (KSH art. 348
  § 4 sets the record date 5 days - 3 months after the resolution).
* Belka tax on dividends: 19% flat, withheld by the payer (art. 30a ust. 1
  pkt 4 and art. 41 ust. 4 ustawy o PIT); the withheld tax is rounded to
  whole złoty (art. 63 § 1 Ordynacji podatkowej - the grosze rule of § 1a
  covers interest and discount only). IKE: no tax when paid out under the
  act's conditions; IKZE: no tax inside, 10% flat on the withdrawal
  (``gpwlib.tax``).

Parsing of ESPI dividend reports (dates, DPS, stage) is a copy of the
gpw-event-calendar parser (skills never import each other's code), extended
with total amount, share count, special-dividend wording and several DPS
figures in one report.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import statistics
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from gpwlib import plcal, tax

D = dt.date

# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------


def is_session(day: D) -> bool:
    return plcal.is_probable_session(day)


def shift_sessions(day: D, n: int) -> D:
    """The n-th session after (n>0) or before (n<0) ``day`` (``day`` not counted)."""
    step = 1 if n > 0 else -1
    left = abs(n)
    cur = day
    while left:
        cur += dt.timedelta(days=step)
        if is_session(cur):
            left -= 1
    return cur


def exdiv_from_record(record: D) -> Dict:
    """Record date D -> last session with right (D-2 sessions) and ex-date (D-1 session)."""
    return {"record_date": record.isoformat(), "last_cum_date": shift_sessions(record, -2).isoformat(),
            "ex_date": shift_sessions(record, -1).isoformat()}


def exdiv_from_last_cum(last_cum: D) -> Dict:
    """Last session with the right L -> ex-date (L+1 session), record date (L+2 sessions)."""
    return {"last_cum_date": last_cum.isoformat(), "ex_date": shift_sessions(last_cum, 1).isoformat(),
            "record_date": shift_sessions(last_cum, 2).isoformat()}


def cache_gap_dates(dates: Sequence[str]) -> set:
    """Dates of cached sessions that follow a *missing* probable session.

    A missing session (e.g. 2022-01-03 absent from the cache) makes the next
    day's change% refer to a close we do not have, so ``panel`` reports a
    spurious adjustment there (seen on ~320 tickers at once). Such dates are
    never treated as ex-dates.
    """
    out = set()
    for i in range(1, len(dates)):
        a, b = D.fromisoformat(dates[i - 1]), D.fromisoformat(dates[i])
        day = a + dt.timedelta(days=1)
        while day < b:
            if is_session(day):
                out.add(dates[i])
                break
            day += dt.timedelta(days=1)
    return out


# --------------------------------------------------------------------------
# Reconstruction from the price archive
# --------------------------------------------------------------------------

# Factor = reference price / previous close. Ordinary GPW dividends are
# 0.5-12% of the price (WIG20 payers 2019-2026: 1-10%); a cut above 25%
# (factor < 0.75) is a split, a rights issue or a spin-off; factors above 1
# are reverse splits or data artefacts. Upper bound 0.995 = panel.ADJ_TOLERANCE.
DIVIDEND_FACTOR_MIN = 0.75
DIVIDEND_FACTOR_MAX = 0.995
# Precision of a reconstructed DPS. Change% is published with 2 decimals, so
# the implied reference price is known to +-0.005 pp (= 5e-5 of the price);
# GPW also rounds the reference price to the tick, and GPW ticks are about
# 0.01-0.1% of the price (tick table in the Szczegółowe Zasady Obrotu
# Giełdowego) - we use half of a 0.1% tick as the bound.
CHANGE_ROUNDING = 5e-5
HALF_TICK_SHARE = 0.0005


def raw_closes(dates: Sequence[str], adj_close: Sequence[float], adjustments: Sequence[Dict]) -> List[float]:
    """Undo ``panel``'s backward adjustment: raw close = adjusted close / product of
    the factors of every adjustment dated after that session."""
    facs = sorted(((a["date"], a["factor"]) for a in adjustments), key=lambda x: x[0])
    out = [0.0] * len(dates)
    prod = 1.0
    j = len(facs) - 1
    for i in range(len(dates) - 1, -1, -1):
        out[i] = adj_close[i] / prod if prod else adj_close[i]
        while j >= 0 and facs[j][0] == dates[i]:
            prod *= facs[j][1]
            j -= 1
    return out


def reconstruct_dividends(dates: Sequence[str], raw_close: Sequence[float], adjustments: Sequence[Dict],
                          gap_dates: Optional[set] = None) -> List[Dict]:
    """Ex-dates and gross DPS from ``panel`` adjustments (oldest first).

    Each item: ``ex_date, last_cum_date, prev_close, ref_price, factor, dps,
    cut_pct, precision_pln, kind`` where ``kind`` is ``dividend`` or
    ``corporate_action`` (split / rights / artefact - not a dividend).
    """
    gap_dates = gap_dates or set()
    idx = {d: i for i, d in enumerate(dates)}
    out = []
    for a in sorted(adjustments, key=lambda x: x["date"]):
        i = idx.get(a["date"])
        if i is None or i == 0:
            continue
        f = float(a["factor"])
        prev = raw_close[i - 1]
        ref = prev * f
        if a["date"] in gap_dates:
            kind = "cache_gap"
        elif DIVIDEND_FACTOR_MIN <= f < DIVIDEND_FACTOR_MAX:
            kind = "dividend"
        else:
            kind = "corporate_action"
        dps = prev - ref
        out.append({"ex_date": a["date"], "last_cum_date": dates[i - 1], "prev_close": round(prev, 4),
                    "ref_price": round(ref, 4), "factor": f,
                    # dividends are set in whole grosze; the raw estimate is kept for the cross-check
                    "dps": round(dps, 2) if kind == "dividend" else None,
                    "dps_raw": round(dps, 4) if kind == "dividend" else None,
                    "cut_pct": round((1 - f) * 100, 3),
                    "precision_pln": round(prev * CHANGE_ROUNDING + ref * HALF_TICK_SHARE, 4),
                    "kind": kind, "source": "archive"})
    return out


# --------------------------------------------------------------------------
# Polish (and English) dates - copied from gpw-event-calendar
# --------------------------------------------------------------------------

_MONTHS = {
    "stycznia": 1, "styczeń": 1, "styczen": 1, "sty": 1, "lutego": 2, "luty": 2, "lut": 2,
    "marca": 3, "marzec": 3, "mar": 3, "kwietnia": 4, "kwiecień": 4, "kwiecien": 4, "kwi": 4,
    "maja": 5, "maj": 5, "czerwca": 6, "czerwiec": 6, "cze": 6, "lipca": 7, "lipiec": 7, "lip": 7,
    "sierpnia": 8, "sierpień": 8, "sierpien": 8, "sie": 8, "września": 9, "wrzesnia": 9, "wrzesień": 9,
    "wrzesien": 9, "wrz": 9, "października": 10, "pazdziernika": 10, "październik": 10, "paź": 10, "paz": 10,
    "listopada": 11, "listopad": 11, "lis": 11, "grudnia": 12, "grudzień": 12, "grudzien": 12, "gru": 12,
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "april": 4, "apr": 4, "may": 5,
    "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_MNAMES = "|".join(sorted((re.escape(k) for k in _MONTHS), key=len, reverse=True))
_RX_WORD = re.compile(r"(?<!\d)(\d{1,2})(?:\s*-?\s*(?:go|ego))?\s*(?:\.\s*)?(" + _MNAMES + r")\.?\s*(\d{4})(?!\d)",
                      re.I | re.U)
_RX_EN = re.compile(r"\b(" + _MNAMES + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)
_RX_NUM = re.compile(r"(?<![\d.])(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?![\d])")
_RX_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


def normalize_text(text: str) -> str:
    t = (text or "").replace(" ", " ").replace("–", "-").replace("—", "-")
    t = t.replace("_", " ")
    return " ".join(t.split())


def _mk(y: int, m: int, d: int) -> Optional[D]:
    try:
        return D(y, m, d)
    except ValueError:
        return None


def find_dates(text: str) -> List[Tuple[D, int, int]]:
    """All dates in ``text`` as ``(date, start, end)`` in text order, no overlaps.
    "5 marca 2026 r.", "10 December 2026", "05.03.2026", "2026-03-05" (day-first)."""
    t = text or ""
    found: List[Tuple[D, int, int]] = []
    for m in _RX_WORD.finditer(t):
        mon = _MONTHS.get(m.group(2).lower())
        d = _mk(int(m.group(3)), mon, int(m.group(1))) if mon else None
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
    found.sort(key=lambda x: (x[1], -(x[2] - x[1])))
    out: List[Tuple[D, int, int]] = []
    for item in found:
        if out and item[1] < out[-1][2]:
            continue
        out.append(item)
    return out


# --------------------------------------------------------------------------
# ESPI dividend reports
# --------------------------------------------------------------------------

_AMOUNT = r"(\d{1,3}(?:[ . ]\d{3})+(?:,\d{1,4})?|\d+(?:[,.]\d{1,4})?)"
_CUR = r"(zł|złotych|zlotych|złote|PLN|gr|groszy|EUR|euro)"
_PER_SHARE = r"(?:na\s+(?:jedną\s+|każdą\s+|1\s+)?akcj|za\s+(?:jedną\s+|1\s+)?akcj|per\s+share|/\s*akcj)"
_RX_DPS = [
    re.compile(_AMOUNT + r"\s*" + _CUR + r"(?![a-ząćęłńóśźż])(?=[^.;]{0,60}?" + _PER_SHARE + r")", re.I),
    re.compile(r"(?:" + _PER_SHARE + r"\w*|dywidend\w*\s+jednostkow\w*|DPS)[^.;\d]{0,50}?"
               + _AMOUNT + r"\s*" + _CUR + r"(?![a-ząćęłńóśźż])", re.I),
    re.compile(r"\b(PLN|EUR)\s*" + _AMOUNT + r"[^.;]{0,30}?per\s+share", re.I),
]
_RX_RECORD = re.compile(r"dzie[nń]\w*\s+dywidendy|dniem\s+dywidendy|dzie[nń]\w*\s+ustalenia\s+praw|"
                        r"ustalenia\s+prawa?\s+do\s+dywidendy|list\w*\s+akcjonariuszy\s+uprawnionych|"
                        r"record\s+date|dividend\s+date", re.I)
# strong payment keywords first; the weak ones ("wypłacona w dniu") also
# describe earlier interim payments and are used only when nothing else matches
_RX_PAY_STRONG = re.compile(r"termin\w*\s+wypłat\w*|dzie[nń]\w*\s+wypłat\w*|dniem\s+wypłat\w*|data\s+wypłat\w*|"
                            r"payment\s+date", re.I)
# a date after "z dnia" is a resolution or statute date, not a payment date
_RX_PAY_STOP = re.compile(r"dzie[nń]\w*\s+dywidendy|dniem\s+dywidendy|list\w*\s+akcjonariuszy|\bz\s+dnia\b|"
                          r"podstaw\w*\s+prawn|\bustaw\w*|record\s+date", re.I)
_RX_PAY = re.compile(r"termin\w*\s+wypłat\w*|dzie[nń]\w*\s+wypłat\w*|dniem\s+wypłat\w*|data\s+wypłat\w*|"
                     r"wypłat\w*\s+(?:dywidendy\s+|zaliczki\s+)?(?:nastąpi|zostanie\s+dokonana|odbędzie|będzie)|"
                     r"wypłacon\w*\s+(?:w\s+dniu|dnia)|payment\s+date|(?:will\s+be\s+)?paid\s+(?:out\s+)?on", re.I)
_RX_LAST_CUM = re.compile(r"ostatni\w*\s+(?:dzień\s+)?notowani\w*\s+z\s+prawem|last\s+(?:trading\s+)?day\s+with", re.I)
_RX_TOTAL = re.compile(r"(?:na\s+wypłatę\s+dywidendy[^.;\d]{0,60}?|dywidend\w*[^.;\d]{0,40}?w\s+(?:łącznej\s+)?kwocie\s+)"
                       + _AMOUNT + r"\s*(?:zł|złotych|PLN)", re.I)
# "pozostająca do wypłaty dywidenda ... X zł na jedną akcję": the tranche still to be paid
_RX_REMAINING = re.compile(r"pozostając\w*\s+do\s+wypłaty|pozostał\w*\s+(?:do\s+wypłaty\s+)?częś\w*\s+dywidend", re.I)
_RX_SHARES = re.compile(r"(?:liczba\s+akcji\s+objętych\s+dywidendą(?:\s+wynosi)?|dywidendą\s+objęt\w*\s+(?:jest\s+)?)"
                        r"[:\s]*" + _AMOUNT + r"(?:\s*(?:sztuk|akcji))?", re.I)
# Wording of a one-off payment ("dywidenda nadzwyczajna/specjalna/jednorazowa")
_RX_SPECIAL = re.compile(r"nadzwyczajn\w*\s+dywidend|dywidend\w*\s+nadzwyczajn|specjaln\w*\s+dywidend|"
                         r"dywidend\w*\s+specjaln|jednorazow\w*\s+dywidend|special\s+dividend|one-?off", re.I)
_RX_NO_DIV = re.compile(r"niewypłacani\w*\s+dywidend|nie\s+wypłac\w*\s+dywidend|nie\s+przeznacz\w*\s+.{0,40}dywidend|"
                        r"rezygnacj\w*\s+z\s+wypłaty|zysk\w*\s+.{0,60}w\s+całości\s+na\s+kapitał|"
                        r"no\s+dividend|not\s+to\s+pay", re.I)


def _num(s: str) -> Optional[float]:
    s = s.replace(" ", "").replace(" ", "")
    if s.count(".") >= 1 and "," in s:      # 6.091.904,00
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:                   # 6.091.904
        s = s.replace(".", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}\.\d{3}", s):  # 404.000 (thousands)
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_dps_all(text: str) -> List[Dict]:
    """Every per-share amount in ``text`` (text order, de-duplicated by position)."""
    t = normalize_text(text)
    hits: List[Tuple[int, Dict]] = []
    taken: List[Tuple[int, int]] = []
    for i, rx in enumerate(_RX_DPS):
        for m in rx.finditer(t):
            if any(not (m.end() <= s or m.start() >= e) for s, e in taken):
                continue
            if i == 2:
                cur, amt = m.group(1), m.group(2)
            else:
                amt, cur = m.group(1), m.group(2)
            if re.fullmatch(r"\d{1,3}(?:[ . ]\d{3})+", amt.strip()):
                continue  # 6.091.904 - a share count or a total, not a per-share amount
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
            taken.append((m.start(), m.end()))
            hits.append((m.start(), {"dps": round(val, 4), "currency": cur_n, "pos": m.start()}))
    hits.sort(key=lambda x: x[0])
    return [h for _p, h in hits]


def parse_dps(text: str) -> Optional[Dict]:
    allp = parse_dps_all(text)
    if not allp:
        return None
    return {"dps": allp[0]["dps"], "currency": allp[0]["currency"]}


# A date right after "rok obrotowy zakończony (w dniu)" is a period end, not
# the record or payment date ("...za rok obrotowy zakończony dnia 31 grudnia
# 2025 r., został ustalony na 17 września 2026 r.").
_RX_PERIOD_END_BEFORE = re.compile(r"(?:zakończon\w*|kończąc\w*|obrotow\w*|okres\w*\s+od|od\s+dnia|do\s+dnia)"
                                   r"(?:\s+(?:w|z))?(?:\s+dni[ua])?\s*$", re.I)
# "18 czerwca 2026 roku jako dzień dywidendy": the date stands before the keyword
_RX_JAKO_AFTER = re.compile(r"^\s*(?:r\.|roku|r)?\s*(?:jako|-|–|:)?\s*$", re.I)


def _dates_after(dates: List[Tuple[D, int, int]], pos: int, span: int, text: str,
                 stop: Optional[re.Pattern] = None, limit: int = 1) -> List[D]:
    end = pos + span
    if stop is not None:
        sm = stop.search(text, pos)
        if sm and sm.start() < end:
            end = sm.start()
    out = []
    for d, s, _e in dates:
        if pos <= s < end and not _RX_PERIOD_END_BEFORE.search(text[max(0, s - 40):s]):
            out.append(d)
    return out[:limit]


def _date_before(dates: List[Tuple[D, int, int]], kw_start: int, text: str) -> Optional[D]:
    """The date written just before a keyword followed by 'jako' ("18 czerwca 2026 roku jako dzień dywidendy")."""
    for d, s, e in reversed(dates):
        if e <= kw_start and kw_start - e <= 20 and re.search(r"jako", text[e:kw_start], re.I) \
                and _RX_JAKO_AFTER.match(text[e:kw_start].replace("jako", "")):
            return d
        if e <= kw_start:
            break
    return None


def dividend_stage(title: str, body: str = "") -> str:
    """``interim`` | ``policy`` | ``proposal`` | ``resolution`` | ``no_dividend`` | ``other``."""
    tt = normalize_text(title).lower()
    tb = normalize_text(body).lower()
    both = tt + " " + tb[:700]
    if re.search(r"zaliczk|interim\s+dividend|advance\s+(?:payment|dividend)", tt):
        return "interim"
    if re.search(r"polityk\w*\s+dywidend|dividend\s+policy|polityk\w*\s+zarządzania\s+kapitałem", tt):
        return "policy"
    if _RX_NO_DIV.search(both):
        return "no_dividend"
    if re.search(r"rekomendac|wnios\w*\s+zarząd|wniosek|propozycj|zamiar|propon|zaproponow|intencj|recommend|propos", tt):
        return "proposal"
    if re.search(r"zaliczk", tb[:400]):
        return "interim"
    if re.search(r"uchwał|podjęł|zdecydowa|postanowi|decyzj|walne\s+zgromadzenie\s+.{0,40}(?:przeznacz|wypłat)|resolv|adopt",
                 both):
        return "resolution"
    if re.search(r"rekomendac|wnios\w*\s+zarząd|propozycj|propon|zaproponuje|recommend", tb[:700]):
        return "proposal"
    return "other"


def parse_dividend_text(title: str, body: str = "") -> Dict:
    """DPS, record/payment dates, stage, totals and special wording of a report.

    Dates are taken only within 100-160 characters after their keyword, so a
    date of the AGM or of the report itself is not mistaken for the record
    date. Ex-date and last day with right follow from the record date (T+2).
    When a report names several per-share amounts (e.g. an ordinary and an
    extraordinary dividend) ``dps_parts`` lists them and ``dps`` is the sum
    only when the text calls one of them extraordinary/special; otherwise
    ``dps`` is the first amount.
    """
    text = normalize_text((title or "") + ". " + (body or ""))
    dates = find_dates(text)
    out: Dict = {"stage": dividend_stage(title, body), "dps": None, "currency": None, "dps_parts": [],
                 "record_date": None, "payment_dates": [], "last_cum_date": None, "ex_date": None,
                 "total_pln": None, "shares": None, "special": bool(_RX_SPECIAL.search(text)),
                 "fiscal_year": None}
    parts = parse_dps_all(text)
    if parts:
        # the same figure repeated ("1,10 zł ... tj. 1,10 zł") is one amount
        uniq: List[Dict] = []
        for p in parts:
            if not any(abs(p["dps"] - u["dps"]) < 1e-9 for u in uniq):
                uniq.append(p)
        out["dps_parts"] = [p["dps"] for p in uniq]
        out["currency"] = uniq[0]["currency"]
        rem = _RX_REMAINING.search(text)
        after = [p for p in uniq if rem and p["pos"] > rem.end()]
        if out["special"] and len(uniq) > 1:
            out["dps"] = round(sum(p["dps"] for p in uniq[:2]), 4)
        elif after:
            out["dps"] = after[0]["dps"]
            out["dps_total_year"] = uniq[0]["dps"]
        else:
            out["dps"] = uniq[0]["dps"]
    for m in _RX_RECORD.finditer(text):
        before = _date_before(dates, m.start(), text)
        got = [before] if before else _dates_after(dates, m.end(), 110, text, stop=_RX_PAY)
        if got:
            out["record_date"] = got[0].isoformat()
            break
    pays: List[D] = []
    for rx in (_RX_PAY_STRONG, _RX_PAY):
        for m in rx.finditer(text):
            before = _date_before(dates, m.start(), text)
            for d in ([before] if before else _dates_after(dates, m.end(), 160, text, stop=_RX_PAY_STOP, limit=3)):
                if d not in pays:
                    pays.append(d)
            if pays:
                break
        if pays:
            break
    if out["record_date"]:
        # payment is never before the record date (KSH art. 348 § 4)
        pays = [d for d in pays if d.isoformat() >= out["record_date"]]
    out["payment_dates"] = [d.isoformat() for d in sorted(pays)]
    for m in _RX_LAST_CUM.finditer(text):
        got = _dates_after(dates, m.end(), 80, text)
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
    tm = _RX_TOTAL.search(text)
    if tm:
        out["total_pln"] = _num(tm.group(1))
    sm = _RX_SHARES.search(text)
    if sm:
        v = _num(sm.group(1))
        out["shares"] = int(v) if v else None
    fy = re.search(r"za\s+(?:rok\s+(?:obrotowy\s+)?)?(20\d\d)(?!/)|(?:for|financial\s+year)\s+(20\d\d)", text, re.I)
    out["fiscal_year"] = int(fy.group(1) or fy.group(2)) if fy else None
    return out


# --------------------------------------------------------------------------
# Merging archive + ESPI (+ optional biznesradar)
# --------------------------------------------------------------------------

# ESPI DPS and archive DPS agree when within 2% or 0.02 zł (reconstruction
# precision, see CHANGE_ROUNDING / HALF_TICK_SHARE, plus tick rounding).
MATCH_REL = 0.02
MATCH_ABS = 0.02
# An ESPI ex-date off by more than a session but within 10 sessions (two
# weeks) of an archive cut of the same size is treated as the same payment.
SHIFT_SESSIONS = 10


def _near(a: str, b: str, sessions: int = 1) -> bool:
    da, db = D.fromisoformat(a), D.fromisoformat(b)
    if da == db:
        return True
    lo, hi = min(da, db), max(da, db)
    return shift_sessions(lo, sessions) >= hi


# An archive cut above 10% of the price is unusual for an ordinary dividend
# (WIG20 payers 2019-2026: 1-10%); inside a scanned ESPI window such a cut
# with no matching dividend report is more likely a rights issue, spin-off or
# data error, so it is not counted as a dividend (reported in crosscheck).
UNCONFIRMED_CUT_FACTOR = 0.90


def merge_history(archive: Sequence[Dict], espi_items: Sequence[Dict], as_of: D,
                  extra: Sequence[Dict] = (), espi_range: Optional[Tuple[str, str]] = None) -> Dict:
    """Combine archive ex-dates with ESPI-parsed dividends (and ``extra`` rows,
    e.g. biznesradar ``{ex_date, dps, source}``).

    Returns ``{events[], upcoming[], crosscheck[]}``. ``events`` are past
    ex-dates (<= as_of) with ``dps``, ``source`` (archive / espi /
    archive+espi / biznesradar ...) and ``special``. An ESPI amount replaces
    the archive estimate when both name the same ex-date (+-1 session).
    """
    events = [dict(e) for e in archive if e["kind"] == "dividend"]
    crosscheck = []
    upcoming = []
    asof_s = as_of.isoformat()
    seen = set()
    for it in sorted(espi_items, key=lambda x: (x.get("published") or "")):
        key = (it.get("ex_date"), it.get("dps"))
        if it.get("ex_date") and key in seen:
            continue                      # the same resolution reported twice (e.g. PL + EN report)
        seen.add(key)
        if it.get("stage") in ("policy", "no_dividend", "other") or not it.get("dps"):
            continue
        if it.get("currency") not in (None, "PLN"):
            continue
        ex = it.get("ex_date")
        if ex and ex > asof_s:
            upcoming.append(dict(it))
            continue
        if not ex:
            continue
        match = next((e for e in events if _near(e["ex_date"], ex)), None)
        shifted = False
        if match is None:
            # a mis-parsed date: an archive dividend of the same size within ~2 weeks is the same payment
            match = next((e for e in events if e.get("dps") and _near(e["ex_date"], ex, SHIFT_SESSIONS)
                          and abs(e["dps"] - it["dps"]) <= max(MATCH_ABS, MATCH_REL * it["dps"])), None)
            shifted = match is not None
        if match is not None:
            diff = it["dps"] - (match.get("dps_archive", match["dps"]) or 0)
            ok = abs(diff) <= max(MATCH_ABS, MATCH_REL * it["dps"])
            crosscheck.append({"ex_date": match["ex_date"], "espi_dps": it["dps"],
                               "archive_dps": round(match.get("dps_archive", match["dps"]), 4),
                               "diff": round(diff, 4),
                               "status": ("match_date_shifted" if shifted else "match") if ok else "mismatch",
                               "espi_ex_date": ex,
                               "url": it.get("url")})
            match.setdefault("dps_archive", match["dps"])
            match["dps"] = it["dps"]
            match["source"] = "archive+espi"
            match["special"] = match.get("special") or bool(it.get("special"))
            match["url"] = it.get("url")
        else:
            crosscheck.append({"ex_date": ex, "espi_dps": it["dps"], "archive_dps": None, "diff": None,
                               "status": "not_in_archive", "url": it.get("url")})
            events.append({"ex_date": ex, "dps": it["dps"], "source": "espi", "special": bool(it.get("special")),
                           "url": it.get("url"), "kind": "dividend"})
    if espi_range:
        lo, hi = espi_range
        keep = []
        for e in events:
            if (e.get("source") == "archive" and e.get("factor") is not None and e["factor"] < UNCONFIRMED_CUT_FACTOR
                    and lo <= e["ex_date"] <= hi and not any(x.get("ex_date") and _near(x["ex_date"], e["ex_date"])
                                                            for x in extra)):
                crosscheck.append({"ex_date": e["ex_date"], "espi_dps": None, "archive_dps": e["dps"], "diff": None,
                                   "status": "archive_only_large_cut_excluded", "url": None,
                                   "cut_pct": e.get("cut_pct")})
                continue
            keep.append(e)
        events = keep
    for x in extra:
        if not x.get("ex_date") or not x.get("dps"):
            continue
        if x["ex_date"] > asof_s:
            if not any(u.get("ex_date") and _near(u["ex_date"], x["ex_date"]) for u in upcoming):
                upcoming.append(dict(x))
            continue
        if not any(_near(e["ex_date"], x["ex_date"]) for e in events):
            events.append({"ex_date": x["ex_date"], "dps": x["dps"], "source": x.get("source", "extra"),
                           "special": False, "kind": "dividend"})
    events.sort(key=lambda e: e["ex_date"])
    upcoming.sort(key=lambda e: e.get("ex_date") or "9999")
    return {"events": events, "upcoming": upcoming, "crosscheck": crosscheck}


# --------------------------------------------------------------------------
# Annual history and metrics
# --------------------------------------------------------------------------

# A series is observed for a full calendar year when it starts within the
# first 10 calendar days of the year (first session after New Year + slack).
FULL_YEAR_START_SLACK_DAYS = 10
# A year-over-year fall of DPS by more than 10% is a cut. Smaller changes are
# within the noise of reconstruction and of PLN-rounded DPS policies; Lintner
# (1956) managers move DPS gradually, so a >10% fall is a deliberate signal.
CUT_THRESHOLD = 0.10


def annual_history(events: Sequence[Dict], first_date: str, as_of: D) -> List[Dict]:
    """Calendar-year (of the ex-date) DPS sums from the first fully observed
    year to ``as_of.year``. ``complete`` is False for the running year."""
    fd = D.fromisoformat(first_date)
    start_year = fd.year if fd <= D(fd.year, 1, FULL_YEAR_START_SLACK_DAYS) else fd.year + 1
    by_year: Dict[int, Dict] = {}
    for e in events:
        y = int(e["ex_date"][:4])
        if y < start_year or e["ex_date"] > as_of.isoformat():
            continue
        r = by_year.setdefault(y, {"dps": 0.0, "n": 0, "sources": set(), "special": False})
        r["dps"] += e["dps"] or 0.0
        r["n"] += 1
        r["sources"].add(e.get("source", "archive"))
        r["special"] = r["special"] or bool(e.get("special"))
    out = []
    for y in range(start_year, as_of.year + 1):
        r = by_year.get(y, {"dps": 0.0, "n": 0, "sources": set(), "special": False})
        out.append({"year": y, "dps": round(r["dps"], 4), "payments": r["n"], "sources": sorted(r["sources"]),
                    "special": r["special"], "complete": y < as_of.year})
    return out


def _final_years(annual: Sequence[Dict]) -> List[Dict]:
    """Years usable for comparisons: complete years, plus the running year when
    it already paid and the company pays once a year (max one payment in
    every earlier year) - a two-tranche payer's year is not final yet."""
    complete = [a for a in annual if a["complete"]]
    cur = [a for a in annual if not a["complete"]]
    single = all(a["payments"] <= 1 for a in complete)
    if cur and cur[0]["dps"] > 0 and single:
        return complete + cur
    return complete


def cagr(a: float, b: float, years: int) -> Optional[float]:
    if a is None or b is None or a <= 0 or b <= 0 or years <= 0:
        return None
    return (b / a) ** (1.0 / years) - 1


def history_metrics(annual: Sequence[Dict]) -> Dict:
    fin = _final_years(annual)
    observed = len(fin)
    paid = sum(1 for a in fin if a["dps"] > 0)
    streak = 0
    for a in reversed(fin):
        if a["dps"] > 0:
            streak += 1
        else:
            break
    cuts = []
    for prev, cur in zip(fin, fin[1:]):
        if prev["dps"] > 0 and cur["dps"] == 0:
            cuts.append({"year": cur["year"], "from": prev["dps"], "to": 0.0, "change_pct": -100.0,
                         "kind": "suspension"})
        elif prev["dps"] > 0 and cur["dps"] < prev["dps"] * (1 - CUT_THRESHOLD):
            cuts.append({"year": cur["year"], "from": prev["dps"], "to": cur["dps"],
                         "change_pct": round((cur["dps"] / prev["dps"] - 1) * 100, 1), "kind": "cut"})
    last = fin[-1] if fin else None
    out = {"years_observed": observed, "years_paid": paid, "consecutive_years": streak, "cuts": cuts,
           "n_cuts": sum(1 for c in cuts if c["kind"] == "cut"),
           "n_suspensions": sum(1 for c in cuts if c["kind"] == "suspension"),
           "last_final_year": last["year"] if last else None,
           "dps_cagr_3y_pct": None, "dps_cagr_5y_pct": None}
    for n in (3, 5):
        if len(fin) > n:
            g = cagr(fin[-1 - n]["dps"], fin[-1]["dps"], n)
            out["dps_cagr_{}y_pct".format(n)] = None if g is None else round(g * 100, 2)
    return out


def ttm_dps(events: Sequence[Dict], as_of: D, days: int = 365) -> float:
    lo = (as_of - dt.timedelta(days=days)).isoformat()
    return round(sum(e["dps"] or 0.0 for e in events if lo < e["ex_date"] <= as_of.isoformat()), 4)


# --------------------------------------------------------------------------
# Yields and tax
# --------------------------------------------------------------------------

def yield_pct(dps: Optional[float], price: Optional[float]) -> Optional[float]:
    if dps is None or not price or price <= 0:
        return None
    return round(dps / price * 100, 3)


def net_yields(gross_pct: Optional[float]) -> Dict:
    """Regular account: gross x (1 - 19%). IKE: gross (no tax when the IKE
    conditions are met). IKZE: gross inside the account; the 10% flat tax is
    charged on the whole withdrawal, so a dividend reinvested to the end is
    worth gross x 0.9 then."""
    if gross_pct is None:
        return {"regular": None, "ike": None, "ikze_inside": None, "ikze_after_exit_tax": None}
    return {"regular": round(gross_pct * (1 - tax.BELKA_RATE), 3), "ike": round(gross_pct, 3),
            "ikze_inside": round(gross_pct, 3),
            "ikze_after_exit_tax": round(gross_pct * (1 - tax.IKZE_WITHDRAWAL_RATE), 3)}


def round_tax_pln(amount: float) -> float:
    """Art. 63 § 1 Ordynacji podatkowej: under 50 gr dropped, 50 gr and more up."""
    return float(math.floor(amount + 0.5)) if amount > 0 else 0.0


def dividend_cash(dps: float, shares: int, account: str = "regular") -> Dict:
    """Cash from one payment for ``shares`` shares. Regular account: 19% withheld
    by the payer, rounded to whole złoty. IKE/IKZE: no withholding."""
    gross = round(dps * shares, 2)
    if account == "regular":
        t = round_tax_pln(gross * tax.BELKA_RATE)
    else:
        t = 0.0
    return {"gross": gross, "tax": t, "net": round(gross - t, 2), "account": account}


def ike_vs_regular(price: float, dps: float, shares: int, years: int = 10, growth_pct: float = 0.0,
                   commission_pct: float = 0.0, commission_min: float = 0.0) -> Dict:
    """Hold ``shares`` for ``years`` with dividends reinvested once a year in
    whole shares at the then price (commission per buy, leftover cash kept),
    price and DPS growing ``growth_pct`` a year. Regular: dividend net of 19%
    (rounded), 19% on the capital gain at the sale. IKE: no tax (conditions
    met). IKZE: 10% of the whole withdrawal. Same start for all accounts."""
    from gpwlib import brokers  # local import keeps the engine importable in tests without brokers

    g = growth_pct / 100.0
    res = {}
    for acc in ("regular", "ike", "ikze"):
        n, cash, cost_basis = shares, 0.0, shares * price
        p, d = price, dps
        tax_paid = fees = 0.0
        for _y in range(years):
            p *= (1 + g)
            d *= (1 + g)
            cf = dividend_cash(d, n, "regular" if acc == "regular" else "ike")
            tax_paid += cf["tax"]
            cash += cf["net"]
            k = int(cash // (p * (1 + commission_pct / 100.0))) if p > 0 else 0
            while k > 0:
                fee = brokers.commission(k * p, commission_pct, commission_min)
                if k * p + fee <= cash + 1e-9:
                    break
                k -= 1
            if k > 0:
                fee = brokers.commission(k * p, commission_pct, commission_min)
                cash -= k * p + fee
                fees += fee
                cost_basis += k * p + fee
                n += k
        value = n * p + cash
        if acc == "regular":
            gain = n * p - cost_basis
            exit_tax = round_tax_pln(gain * tax.BELKA_RATE) if gain > 0 else 0.0
        elif acc == "ikze":
            exit_tax = round_tax_pln(value * tax.IKZE_WITHDRAWAL_RATE)
        else:
            exit_tax = 0.0
        res[acc] = {"shares_end": n, "cash_end": round(cash, 2), "value_before_exit": round(value, 2),
                    "dividend_tax_paid": round(tax_paid, 2), "fees": round(fees, 2),
                    "exit_tax": round(exit_tax, 2), "value_after_exit": round(value - exit_tax, 2)}
    res["ike_minus_regular"] = round(res["ike"]["value_after_exit"] - res["regular"]["value_after_exit"], 2)
    res["assumptions"] = {"years": years, "growth_pct": growth_pct, "start_price": price, "start_dps": dps,
                          "shares": shares, "commission_pct": commission_pct, "commission_min": commission_min}
    return res


# --------------------------------------------------------------------------
# Ex-date drop and dividend capture
# --------------------------------------------------------------------------

def exdate_drops(events: Sequence[Dict], dates: Sequence[str], raw_open: Sequence[float],
                 raw_close: Sequence[float], index_ret: Optional[Dict[str, float]] = None) -> Dict:
    """How much of the DPS the price actually lost on the ex-date.

    ratio_open = (last cum close - ex-date open) / DPS; ratio_close likewise
    with the ex-date close; ``ratio_close_mkt`` removes the index move that
    day (prev close x index return). 1.0 = the price fell by exactly the
    dividend (Elton & Gruber 1970 for the theory; taxes and costs push it
    below 1)."""
    idx = {d: i for i, d in enumerate(dates)}
    rows = []
    for e in events:
        i = idx.get(e["ex_date"])
        if i is None or i == 0 or not e.get("dps"):
            continue
        pc = raw_close[i - 1]
        dps = e["dps"]
        ro = (pc - raw_open[i]) / dps
        rc = (pc - raw_close[i]) / dps
        mk = None
        if index_ret and e["ex_date"] in index_ret:
            mk = (pc * (1 + index_ret[e["ex_date"]]) - raw_close[i]) / dps
        rows.append({"ex_date": e["ex_date"], "dps": dps, "prev_close": round(pc, 4), "open": round(raw_open[i], 4),
                     "close": round(raw_close[i], 4), "ratio_open": round(ro, 3), "ratio_close": round(rc, 3),
                     "ratio_close_mkt": None if mk is None else round(mk, 3)})

    def med(key):
        v = [r[key] for r in rows if r[key] is not None]
        return round(statistics.median(v), 3) if v else None
    return {"events": rows, "n": len(rows), "median_ratio_open": med("ratio_open"),
            "median_ratio_close": med("ratio_close"), "median_ratio_close_mkt": med("ratio_close_mkt")}


def capture_example(amount: float, price: float, dps: float, drop_ratio: Optional[float],
                    commission_pct: float, commission_min: float) -> Dict:
    """Buy at the last cum-dividend close, sell at the ex-date price on a
    regular account. The dividend is taxed 19% at source; the price loss is a
    capital loss that only offsets other capital gains in PIT-38 (art. 30b),
    never the flat dividend tax (art. 30a) - so we show the result with and
    without that later offset."""
    from gpwlib import brokers

    if price <= 0 or dps <= 0:
        raise ValueError("price and dps must be positive")
    shares = int(amount // price)
    ratio = 1.0 if drop_ratio is None else drop_ratio
    sell_price = price - dps * ratio
    buy_fee = brokers.commission(shares * price, commission_pct, commission_min)
    sell_fee = brokers.commission(shares * sell_price, commission_pct, commission_min)
    cash = dividend_cash(dps, shares, "regular")
    price_loss = shares * (price - sell_price)
    result = cash["net"] - price_loss - buy_fee - sell_fee
    loss_for_pit38 = price_loss + buy_fee + sell_fee
    offset = round(loss_for_pit38 * tax.BELKA_RATE, 2) if loss_for_pit38 > 0 else 0.0
    return {"amount": amount, "shares": shares, "buy_price": round(price, 4), "sell_price": round(sell_price, 4),
            "drop_ratio_used": round(ratio, 3), "dividend_gross": cash["gross"], "dividend_tax": cash["tax"],
            "dividend_net": cash["net"], "price_loss": round(price_loss, 2), "fees": round(buy_fee + sell_fee, 2),
            "result": round(result, 2), "result_if_loss_offsets_other_gains": round(result + offset, 2),
            "breakeven_drop_ratio": round((cash["net"] - buy_fee - sell_fee) / (shares * dps), 3) if shares else None}


# --------------------------------------------------------------------------
# Stability score and trap flags
# --------------------------------------------------------------------------

# Weights (sum 100). Consistency first: Lintner (1956) and Brav, Graham,
# Harvey & Michaely (2005) show managers treat the dividend level as a
# commitment and cut only reluctantly, so the paying record and the absence of
# cuts carry most information; markets punish cuts and omissions (Michaely,
# Thaler & Womack 1995). Growth and payout room are the forward-looking part.
SCORE_WEIGHTS = {"consistency": 35, "streak": 15, "no_cuts": 20, "growth": 15, "payout": 15}
STREAK_FULL_YEARS = 7        # the archive covers 2019-2026: 7 full years max
CUT_PENALTY = 8              # points of the 20 lost per cut
SUSPENSION_PENALTY = 12      # an omission is worse than a cut
PAYOUT_COMFORT = 60.0        # % of earnings - room for a bad year (Lintner target-payout logic)
PAYOUT_LIMIT = 100.0         # above it the dividend is paid from more than the profit


def _growth_points(cagr_pct: Optional[float]) -> float:
    w = SCORE_WEIGHTS["growth"]
    if cagr_pct is None:
        return w / 3.0          # unknown: below neutral, history too short
    if cagr_pct <= -10:
        return 0.0
    if cagr_pct <= 0:
        return w / 2 * (cagr_pct + 10) / 10
    return min(w, w / 2 + w / 2 * cagr_pct / 10)


def _payout_points(payout_pct: Optional[float]) -> float:
    w = SCORE_WEIGHTS["payout"]
    if payout_pct is None:
        return w / 2.0          # unknown: neutral
    if payout_pct <= 0:
        return 0.0              # dividend with a loss
    if payout_pct <= PAYOUT_COMFORT:
        return float(w)
    if payout_pct <= PAYOUT_LIMIT:
        return w - (w * 2 / 3) * (payout_pct - PAYOUT_COMFORT) / (PAYOUT_LIMIT - PAYOUT_COMFORT)
    return 0.0


def stability_score(m: Dict, payout_pct: Optional[float]) -> Dict:
    """0-100 from :data:`SCORE_WEIGHTS` (see references/methodology.md)."""
    w = SCORE_WEIGHTS
    obs = m.get("years_observed") or 0
    if obs == 0:
        return {"score": 0, "parts": {}, "confidence": "none"}
    parts = {
        "consistency": w["consistency"] * m["years_paid"] / obs,
        "streak": w["streak"] * min(m["consecutive_years"], STREAK_FULL_YEARS) / STREAK_FULL_YEARS,
        "no_cuts": max(0.0, w["no_cuts"] - CUT_PENALTY * m["n_cuts"] - SUSPENSION_PENALTY * m["n_suspensions"])
        if m["years_paid"] else 0.0,
        "growth": _growth_points(m.get("dps_cagr_5y_pct") if m.get("dps_cagr_5y_pct") is not None
                                 else m.get("dps_cagr_3y_pct")) if m["years_paid"] else 0.0,
        "payout": _payout_points(payout_pct) if m["years_paid"] else 0.0,
    }
    score = int(round(sum(parts.values())))
    conf = "high" if obs >= 6 and payout_pct is not None else ("medium" if obs >= 4 else "low")
    return {"score": max(0, min(100, score)), "parts": {k: round(v, 1) for k, v in parts.items()}, "confidence": conf}


# Trap thresholds
YIELD_SECTOR_MULT = 2.0      # yield > 2x the sector median -> price fell or one-off (kanchi-dividend-sop yield trap)
SPECIAL_MULT = 2.0           # a year's DPS > 2x the higher of the two previous paid years -> likely one-off
EPS_FALL = 0.10              # profit down > 10% y/y on the same period -> falling earnings
FCF_COVER_MIN = 1.0          # free cash flow below the dividend paid
MIN_SECTOR_PAYERS = 3        # fewer payers -> use the market median


def median_or_none(vals: Iterable[float]) -> Optional[float]:
    v = [x for x in vals if x is not None]
    return statistics.median(v) if v else None


def special_dividend_check(annual: Sequence[Dict]) -> Optional[Dict]:
    fin = [a for a in _final_years(annual) if a["dps"] > 0]
    if len(fin) < 3:
        return None
    last = fin[-1]
    # the higher of the two previous paying years: a fast-growing payer (PKO BP
    # 2022-2025) is not flagged, a single jump far above recent levels is
    base = max(a["dps"] for a in fin[-3:-1])
    if base > 0 and last["dps"] > SPECIAL_MULT * base:
        return {"year": last["year"], "dps": last["dps"], "max_prev_two": round(base, 4)}
    return None


def trap_flags(*, yield_gross: Optional[float], sector_median: Optional[float], payout_pct: Optional[float],
               profit_change_pct: Optional[float], net_profit: Optional[float], annual: Sequence[Dict],
               special_text: bool, state_controlled: Optional[Dict], is_bank: bool, fcf_cover: Optional[float],
               metrics: Dict) -> List[Dict]:
    """List of ``{code, severity, detail{}}``. Codes are stable slugs."""
    flags: List[Dict] = []
    if yield_gross and sector_median and yield_gross > YIELD_SECTOR_MULT * sector_median:
        flags.append({"code": "yield_above_2x_sector_median", "severity": "high",
                      "detail": {"yield_pct": yield_gross, "sector_median_pct": round(sector_median, 3)}})
    if payout_pct is not None and payout_pct > PAYOUT_LIMIT:
        flags.append({"code": "payout_above_100pct", "severity": "high", "detail": {"payout_pct": payout_pct}})
    if net_profit is not None and net_profit < 0:
        flags.append({"code": "net_loss", "severity": "high", "detail": {"net_profit_pln": net_profit}})
    if profit_change_pct is not None and profit_change_pct < -EPS_FALL * 100:
        flags.append({"code": "falling_eps", "severity": "medium", "detail": {"profit_change_pct": profit_change_pct}})
    sp = special_dividend_check(annual)
    if sp or special_text:
        flags.append({"code": "one_off_special_dividend", "severity": "medium",
                      "detail": dict(sp or {}, espi_wording=special_text)})
    if fcf_cover is not None and fcf_cover < FCF_COVER_MIN:
        flags.append({"code": "fcf_below_dividend", "severity": "medium", "detail": {"fcf_cover": fcf_cover}})
    last_cut = metrics.get("cuts", [])[-1:] if metrics.get("cuts") else []
    if last_cut and metrics.get("last_final_year") and last_cut[0]["year"] >= metrics["last_final_year"] - 1:
        flags.append({"code": "recent_cut_or_suspension", "severity": "medium", "detail": last_cut[0]})
    if state_controlled:
        flags.append({"code": "state_controlled_policy_risk", "severity": "info", "detail": state_controlled})
    if is_bank:
        flags.append({"code": "bank_knf_dividend_policy", "severity": "info", "detail": {}})
    return flags


# High-severity flags that make a "cut risk" signal. Two independent warnings
# are required (one alone is often benign, e.g. a temporary loss year).
CUT_RISK_MIN_FLAGS = 2
_CUT_RISK_CODES = {"payout_above_100pct", "net_loss", "falling_eps", "fcf_below_dividend",
                   "yield_above_2x_sector_median"}


def cut_risk(flags: Sequence[Dict]) -> Optional[Dict]:
    hits = [f["code"] for f in flags if f["code"] in _CUT_RISK_CODES]
    if len(hits) < CUT_RISK_MIN_FLAGS:
        return None
    return {"codes": hits, "score": min(100, 40 + 20 * (len(hits) - 1))}


# --------------------------------------------------------------------------
# Next dividend and income calendar
# --------------------------------------------------------------------------

# Annual dividends recur at about the same time of year (AGM season); the
# last ex-date + 52 weeks (same weekday) +- 10 sessions covered the 2025->2026
# shifts of the WIG20 payers checked in gpw-event-calendar.
NEXT_WINDOW_SESSIONS = 10


def estimate_next(events: Sequence[Dict], annual: Sequence[Dict], as_of: D) -> List[Dict]:
    """Estimated payments in the next 12 months: each ex-date of the last 12
    months moved by 52 weeks, with last year's DPS (no growth assumed)."""
    out = []
    lo = as_of - dt.timedelta(days=365)
    for e in events:
        d = D.fromisoformat(e["ex_date"])
        if not (lo < d <= as_of):
            continue
        est = d + dt.timedelta(weeks=52)
        while est <= as_of:
            est += dt.timedelta(weeks=52)
        while not is_session(est):
            est += dt.timedelta(days=1)
        out.append({"ex_date": est.isoformat(), "dps": e["dps"], "date_kind": "estimated",
                    "based_on": e["ex_date"], "window_start": shift_sessions(est, -NEXT_WINDOW_SESSIONS).isoformat(),
                    "window_end": shift_sessions(est, NEXT_WINDOW_SESSIONS).isoformat(),
                    "source": "history (last ex-date + 52 weeks)"})
    return out


def income_calendar(items: Sequence[Dict], as_of: D, horizon_days: int = 365) -> Dict:
    """Sum of expected payments per month for the next ``horizon_days``.
    ``items``: ``{ticker, ex_date, dps, shares, date_kind}``."""
    end = (as_of + dt.timedelta(days=horizon_days)).isoformat()
    rows, months = [], {}
    for it in items:
        if not it.get("ex_date") or not (as_of.isoformat() < it["ex_date"] <= end):
            continue
        reg = dividend_cash(it["dps"], it["shares"], "regular")
        r = dict(it, gross=reg["gross"], tax_regular=reg["tax"], net_regular=reg["net"])
        rows.append(r)
        m = months.setdefault(it["ex_date"][:7], {"gross": 0.0, "net_regular": 0.0})
        m["gross"] = round(m["gross"] + reg["gross"], 2)
        m["net_regular"] = round(m["net_regular"] + reg["net"], 2)
    rows.sort(key=lambda r: r["ex_date"])
    return {"rows": rows, "by_month": dict(sorted(months.items())),
            "total_gross": round(sum(r["gross"] for r in rows), 2),
            "total_net_regular": round(sum(r["net_regular"] for r in rows), 2)}
