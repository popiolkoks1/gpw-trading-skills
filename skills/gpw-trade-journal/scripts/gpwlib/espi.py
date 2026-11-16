"""ESPI / EBI company reports from gpw.pl: list, classify, read a report page.

Sources (verified 2026-11-16)
-----------------------------
List (the AJAX endpoint behind https://www.gpw.pl/komunikaty)::

    POST https://www.gpw.pl/ajaxindex.php
    action=GPWEspiReportUnion&start=ajaxSearch&page=komunikaty&format=html&lang=PL
    &letter=&offset=0&limit=100&categoryRaports[]=ESPI&categoryRaports[]=EBI
    &typeRaports[]=RB&typeRaports[]=P&typeRaports[]=Q&typeRaports[]=O&typeRaports[]=R
    &searchText=&date=DD-MM-YYYY

``categoryRaports[]``: ``ESPI`` (Main Market, regulated) and ``EBI``
(NewConnect). ``typeRaports[]``: ``RB`` bieżący (current), ``P`` półroczny,
``Q`` kwartalny, ``O`` okresowy, ``R`` roczny (labels from the page's own
filter). The response is a bare list of ``<li>`` items, newest first; an empty
page is a few whitespace bytes. Pagination: ``offset`` += ``limit`` until a
page returns fewer rows than ``limit`` (the site's own page uses limit=15;
limit=100 is honoured). Quarterly reports often have an EMPTY title and an
empty number (``/2026``); classification then relies on the report type.

Report page: ``https://www.gpw.pl/komunikat?geru_id=<N>`` (HTML, PAP layout:
"Temat", "Podstawa prawna", "Treść raportu", attachments, and an
"METADANE ESAP" sheet whose first rows hold ESAP information-type codes,
e.g. ``TRANSD`` = "Transakcja dokonywana przez osoby pełniące obowiązki
zarządcze", i.e. a MAR art. 19 managers' transaction).

Attachments (PDF) are served under ``attachment/...``; the GPW WAF refuses
scripted downloads. This module only returns their names and links - it never
tries to fetch them (no browser impersonation). Users paste the PDF text when
details such as volumes and prices are needed.

Caching: a past day's list is immutable -> cached for 30 days, but only if it
was retrieved after that day ended (+30 min); today's list is cached for 10
minutes. Report pages are cached for a year.
"""

from __future__ import annotations

import datetime as dt
import html as _html
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from . import http, plcal

LIST_URL = "https://www.gpw.pl/ajaxindex.php"
REPORT_URL = "https://www.gpw.pl/komunikat?geru_id={}"
BASE = "https://www.gpw.pl/"
CATEGORIES = ("ESPI", "EBI")
TYPES = ("RB", "P", "Q", "O", "R")
TYPE_LABELS = {"bieżący": "RB", "półroczny": "P", "kwartalny": "Q", "okresowy": "O", "roczny": "R"}
PAGE_LIMIT = 100
MAX_PAGES = 30          # 3 000 reports per day - far above the busiest day
PAST_DAY_TTL_S = 30 * 86400
TODAY_TTL_S = 600
REPORT_TTL_S = 365 * 86400

CLASSES = (
    "insider", "periodic_q", "periodic_h", "periodic_y", "periodic_schedule", "preliminary_results",
    "agm_call", "agm_resolutions", "agm_other", "dividend", "holding_change", "buyback", "other",
)
# Coarse groups matching the names in docs/ROADMAP_v1.2.md
GROUPS = {
    "insider": "insider", "periodic_q": "periodic", "periodic_h": "periodic", "periodic_y": "periodic",
    "periodic_schedule": "periodic_schedule", "preliminary_results": "periodic",
    "agm_call": "agm", "agm_resolutions": "agm", "agm_other": "agm", "dividend": "dividend",
    "holding_change": "holding_5pct", "buyback": "buyback", "other": "other",
}


# --------------------------------------------------------------------------
# List parsing
# --------------------------------------------------------------------------

_LI = re.compile(r"<li\b[^>]*>(.*?)</li>", re.S | re.I)
_DATE = re.compile(r'<span class="date[^"]*">\s*([^<]*?)\s*</span>', re.S)
_NAME = re.compile(r'<strong class="name">\s*<a href="([^"]*)"[^>]*>(.*?)</a>', re.S)
_P = re.compile(r"<p>(.*?)</p>", re.S)
_GERU = re.compile(r"geru_id=(\d+)")
_COMPANY_ISIN = re.compile(r"^(.*?)\s*\(([A-Z]{2}[A-Z0-9]{9}\d)\)\s*$")


def _text(fragment: str) -> str:
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse_list_html(html: str) -> List[Dict]:
    """Rows of a ``GPWEspiReportUnion`` response, in page order (newest first).

    Each row: ``time`` ('HH:MM:SS'), ``datetime`` (naive, Warsaw local time,
    ISO string), ``date``, ``category`` (ESPI/EBI), ``type`` (RB/P/Q/O/R),
    ``type_label`` (as printed), ``number`` ('59/2026' or None when the site
    prints only '/2026'), ``company``, ``isin`` (None if absent), ``title``
    (may be ''), ``geru_id`` (int), ``url``, ``class``, ``correction`` (bool).
    """
    rows: List[Dict] = []
    for li in _LI.findall(html or ""):
        dm = _DATE.search(li)
        nm = _NAME.search(li)
        if not dm or not nm:
            continue
        parts = [p.strip() for p in _html.unescape(dm.group(1)).split("|")]
        if len(parts) < 4:
            continue
        stamp, type_label, category, number = parts[0], parts[1], parts[2].upper(), parts[3]
        try:
            when = dt.datetime.strptime(stamp, "%d-%m-%Y %H:%M:%S")
        except ValueError:
            continue
        href = _html.unescape(nm.group(1))
        gm = _GERU.search(href)
        if not gm:
            continue
        company_raw = _text(nm.group(2))
        cm = _COMPANY_ISIN.match(company_raw)
        company, isin = (cm.group(1), cm.group(2)) if cm else (company_raw, None)
        pm = _P.search(li)
        title = _text(pm.group(1)) if pm else ""
        rtype = TYPE_LABELS.get(type_label.lower(), type_label.upper()[:2] or None)
        num = number if re.match(r"^\d+\s*/\s*\d{4}$", number) else None
        geru = int(gm.group(1))
        rows.append({
            "time": when.strftime("%H:%M:%S"),
            "datetime": when.isoformat(),
            "date": when.date().isoformat(),
            "category": category,
            "type": rtype,
            "type_label": type_label,
            "number": num.replace(" ", "") if num else None,
            "company": company,
            "isin": isin,
            "title": title,
            "geru_id": geru,
            "url": REPORT_URL.format(geru),
            "class": classify(title, report_type=rtype),
            "correction": is_correction(title),
        })
    return rows


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------
# Rules are tried in order; the first match wins. Polish titles are free text
# written by issuers, so patterns are deliberately loose (stems, optional
# words) and were tested on real titles from the gpw.pl list (tests/shared).
# Order matters: "Zmiana terminu publikacji raportu kwartalnego" is a
# schedule, not a quarterly report; "Uchwała ... zaliczki na poczet
# dywidendy" is a dividend, not an AGM resolution.

_I = re.I | re.U
RULES = [
    # MAR art. 19 managers' transactions (PDMR / insider notices)
    ("insider", re.compile(
        r"art\.?\s*19\b[^.]{0,40}\bMAR\b|\bMAR\b[^.]{0,20}art\.?\s*19|"
        r"rozporz\w*\s+MAR|"
        r"os[oó]b\w*\s+pełni\w*\s+obowiązk\w*\s+zarządcz|"
        r"osob\w*\s+blisko\s+związan|"
        r"powiadomieni\w*\s+o\s+transakcj|"
        r"zawiadomieni\w*\s+o\s+(zawarciu\s+)?transakcj|"
        r"informacj\w*\s+o\s+transakcj\w*\s+(na\s+akcjach|w\s+trybie)|"
        r"(nabyci\w*|zbyci\w*|sprzedaż\w*)\s+akcji\s+przez\s+(prezes\w*|wiceprezes\w*|członk\w*|przewodnicz\w*|osob)|"
        r"\bPDMR\b|managers?'?\s+transactions?|director\s*/?\s*PDMR\s+transaction", _I)),
    ("buyback", re.compile(
        r"akcji\s+własnych|akcje\s+własne|skup\w*\s+akcji|"
        r"treasury\s+shares|own\s+shares|share\s+buy-?\s?back|buy-?\s?back|repurchase\s+of\s+shares", _I)),
    # Art. 69 of the Public Offering Act - thresholds 5%, 10%, ... of votes
    ("holding_change", re.compile(
        r"zmian\w*\s+(stanu\s+posiadania|udziału\s+w\s+ogólnej\s+liczbie\s+głosów)|"
        r"(zmniejszeni\w*|zwiększeni\w*|przekroczeni\w*|spadk\w*)\s+(stanu\s+posiadania|udziału|progu)|"
        r"(powyżej|poniżej)\s+progu\s+\d+\s*%|"
        r"art\.?\s*69\b|"
        r"zawiadomieni\w*\s+(akcjonariusz\w*\s+)?o\s+(nabyciu|zbyciu)\s+akcji|"
        r"major\s+holding|notification\s+of\s+(the\s+)?(change|acquisition)\s+of\s+(shares|holding|voting)", _I)),
    ("periodic_schedule", re.compile(
        r"termin\w*\s+(przekazywania|publikacji)\s+(\w+\s+){0,4}raport|"
        r"harmonogram\w*\s+(\w+\s+){0,2}(publikacji|przekazywania)\s+raport|"
        r"zmian\w*\s+termin\w*\s+(publikacji|przekazania)|"
        r"date\s+for\s+.{0,40}results\s+publication|financial\s+calendar|reporting\s+calendar", _I)),
    ("periodic_y", re.compile(
        r"raport\w*\s+roczn|sprawozdani\w*\s+roczn|\bRS\s*\d{4}\b|\bSRS?\b\s*\d{4}|"
        r"annual\s+(financial\s+)?report", _I)),
    ("periodic_h", re.compile(
        r"raport\w*\s+półroczn|sprawozdani\w*\s+półroczn|\bPSr?\b\s*\d{4}|"
        r"half[- ]year(ly)?\s+(financial\s+)?report|semi-?annual\s+report", _I)),
    ("periodic_q", re.compile(
        r"raport\w*\s+kwartaln|\bQSr?\b\s*\d|raport\w*\s+za\s+(I{1,3}V?|IV|[1-4])\s*(kw|kwartał|Q)|"
        r"interim\s+report|quarterly\s+report", _I)),
    ("preliminary_results", re.compile(
        r"wstępn\w*\s+(\w+\s+){0,3}(wynik|dan)|"
        r"szacunkow\w*\s+(\w+\s+){0,3}(wynik|dan)|"
        r"wybran\w*\s+(\w+\s+){0,2}dan\w*\s+finansow|"
        r"preliminary\s+(financial\s+)?(results|data)|financial\s+results\s+(in|for)\s+(Q|\d)", _I)),
    ("dividend", re.compile(
        r"dywidend|podział\w*\s+zysku|wypłat\w*\s+zysku|zaliczk\w*\s+na\s+poczet|dividend", _I)),
    ("agm_resolutions", re.compile(
        r"uchwał\w*\s+podjęt|podjęt\w*\s+(przez\s+\w+\s+)?uchwał|^\s*uchwały\s+(\w+\s+){0,3}walne|"
        r"treść\s+uchwał|resolutions\s+(adopted|passed)", _I)),
    # shareholder requests / agenda changes mention draft resolutions but are not the call itself
    ("agm_other", re.compile(
        r"(żądani|zgłoszeni|rozszerzeni|uzupełnieni)\w*\s+.{0,120}(porządk\w*\s+obrad|walne\w*\s+zgromadzeni|walnym)", _I)),
    ("agm_call", re.compile(
        r"zwołani\w*\s+(\w+\s+){0,4}(walne|zgromadzeni|NWZA?|ZWZA?)|"
        r"\b(NWZA?|ZWZA?)\b.{0,40}zwoł|"
        r"zawiadomieni\w*\s+o\s+zwołaniu|ogłoszeni\w*\s+o\s+zwołaniu|"
        r"projekt\w*\s+uchwał|"
        r"notice\s+(of\s+call|to\s+the\s+shareholders|of\s+(the\s+)?(annual|extraordinary)?\s*general\s+meeting)|"
        r"proposed\s+resolutions", _I)),
    ("agm_other", re.compile(
        r"walne\w*\s+zgromadzeni|walnym\s+zgromadzeniu|\bNWZA?\b|\bZWZA?\b|general\s+meeting", _I)),
]
_CORRECTION = re.compile(r"\bkorekt\w*|\bsprostowani\w*|\bcorrection\b|\bcorrigendum\b", _I)
_TYPE_CLASS = {"Q": "periodic_q", "P": "periodic_h", "R": "periodic_y"}
_ESAP_CLASS = {"TRANSD": "insider"}  # only codes seen in real reports (2026-11)

# Text evidence of a MAR art. 19 managers' transaction notice. Some issuers
# (e.g. KRUK, seen on gpw.pl in 2026-11) tag *every* current report with the
# ESAP code TRANSD, so the code alone is not proof: an ``insider`` class from
# TRANSD also needs this wording in the title or the report body. Same rule
# gpw-ownership worked around it with, minus two alternatives that also match
# every inside-information report ("art. 17 ust. 1 MAR", "(UE) nr 596/2014"):
# a bare "MAR" or the regulation number is not managers'-transaction wording.
MAR_TEXT = re.compile(
    r"art\.?\s*19\b|obowiązk\w*\s+zarządcz|blisko\s+związan|"
    r"transakcj\w*\s+(na\s+akcjach|nabycia|zbycia|sprzedaży)|managerial\s+responsibilit|"
    r"closely\s+associated|PDMR", _I)


def mentions_managers_transaction(text: str) -> bool:
    """True when ``text`` names a MAR art. 19 / managers' transaction."""
    return bool(MAR_TEXT.search(text or ""))


def is_correction(title: str) -> bool:
    return bool(_CORRECTION.search(title or ""))


def classify(title: str, body_meta: Optional[Dict] = None, report_type: Optional[str] = None) -> str:
    """Return one of :data:`CLASSES`.

    ``body_meta`` (from :func:`parse_report_html`) adds ESAP codes and the
    body text: ``TRANSD`` gives ``insider`` only together with MAR art. 19 /
    managers'-transaction wording in the title or body
    (:func:`mentions_managers_transaction`), because some issuers put TRANSD
    on every report; otherwise the title rules decide. ``report_type`` (``Q``/``P``/``R`` from the
    list) classifies periodic reports whose title is empty or unhelpful when
    no title rule matches.
    """
    codes = [c.upper() for c in ((body_meta or {}).get("esap_codes") or [])]
    text = " ".join((title or "").split())
    if body_meta and not text:
        text = " ".join((body_meta.get("title") or "").split())
    for c in codes:
        if c in _ESAP_CLASS:
            evidence = text + " " + ((body_meta or {}).get("body_text") or "")
            if _ESAP_CLASS[c] != "insider" or mentions_managers_transaction(evidence):
                return _ESAP_CLASS[c]
    for name, rx in RULES:
        if rx.search(text):
            return name
    if report_type and report_type.upper() in _TYPE_CLASS:
        return _TYPE_CLASS[report_type.upper()]
    return "other"


# --------------------------------------------------------------------------
# Fetching the list
# --------------------------------------------------------------------------

def list_form(day: dt.date, categories: Sequence[str] = CATEGORIES, types: Sequence[str] = TYPES,
              offset: int = 0, limit: int = PAGE_LIMIT) -> List[tuple]:
    form = [("action", "GPWEspiReportUnion"), ("start", "ajaxSearch"), ("page", "komunikaty"),
            ("format", "html"), ("lang", "PL"), ("letter", ""), ("offset", str(offset)), ("limit", str(limit))]
    form += [("categoryRaports[]", c) for c in categories]
    form += [("typeRaports[]", t) for t in types]
    form += [("searchText", ""), ("date", day.strftime("%d-%m-%Y"))]
    return form


def _day_cache_policy(day: dt.date):
    """(ttl_seconds, valid_after_utc) for a day's list."""
    today = plcal.today_warsaw()
    if day >= today:
        return TODAY_TTL_S, None
    end_local = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0, 30))
    if plcal.WARSAW is not None:
        end_utc = end_local.replace(tzinfo=plcal.WARSAW).astimezone(dt.timezone.utc)
    else:  # pragma: no cover
        end_utc = (end_local - dt.timedelta(hours=1)).replace(tzinfo=dt.timezone.utc)
    return PAST_DAY_TTL_S, end_utc


def fetch_day(day: dt.date, categories: Sequence[str] = CATEGORIES, types: Sequence[str] = TYPES,
              cache_dir: Optional[Path] = None, offline: bool = False, limit: int = PAGE_LIMIT) -> Dict:
    """All reports published on ``day`` -> ``{date, rows, pages, source_url, retrieved_at, from_cache}``.

    Pages are requested until one returns fewer than ``limit`` rows. Rows are
    de-duplicated by ``geru_id`` and rows dated on another day are dropped
    (defensive; not observed). ``offline=True`` raises ``http.OfflineMiss``
    when the first page is not cached.
    """
    ttl, valid_after = _day_cache_policy(day)
    rows: List[Dict] = []
    seen = set()
    retrieved, from_cache, pages = [], True, 0
    for page in range(MAX_PAGES):
        resp = http.post(LIST_URL, list_form(day, categories, types, page * limit, limit),
                         cache_dir=cache_dir, ttl_seconds=ttl, valid_after=valid_after, offline=offline)
        pages += 1
        retrieved.append(resp.retrieved_at)
        from_cache = from_cache and resp.from_cache
        got = parse_list_html(resp.text)
        new = 0
        for r in got:
            if r["geru_id"] in seen:
                continue
            seen.add(r["geru_id"])
            new += 1
            if r["date"] == day.isoformat():
                rows.append(r)
        if len(got) < limit or new == 0:
            break
    return {
        "date": day.isoformat(),
        "rows": rows,
        "pages": pages,
        "source_url": LIST_URL + " (POST action=GPWEspiReportUnion, date={})".format(day.strftime("%d-%m-%Y")),
        "retrieved_at": max(retrieved) if retrieved else None,
        "from_cache": from_cache,
    }


def fetch_range(start: dt.date, end: dt.date, categories: Sequence[str] = CATEGORIES,
                types: Sequence[str] = TYPES, cache_dir: Optional[Path] = None, offline: bool = False,
                include_non_sessions: bool = False, skip_missing: bool = True) -> Dict:
    """Reports from ``start`` to ``end`` inclusive, oldest day first.

    Weekends and exchange holidays (``plcal``) are skipped by default to save
    requests; issuers occasionally publish on such days, so pass
    ``include_non_sessions=True`` for a complete scan (``fetch_day`` itself
    accepts any date). In offline mode days missing from the cache are listed
    in ``missing_days`` (or raise when ``skip_missing=False``).
    """
    if end < start:
        start, end = end, start
    days, rows, missing, seen = [], [], [], set()
    day = start
    while day <= end:
        if include_non_sessions or plcal.is_probable_session(day):
            try:
                res = fetch_day(day, categories, types, cache_dir=cache_dir, offline=offline)
            except http.OfflineMiss:
                if not skip_missing:
                    raise
                missing.append(day.isoformat())
            else:
                days.append({"date": res["date"], "count": len(res["rows"]), "pages": res["pages"],
                             "from_cache": res["from_cache"]})
                for r in reversed(res["rows"]):  # oldest first within the day
                    if r["geru_id"] not in seen:
                        seen.add(r["geru_id"])
                        rows.append(r)
        day += dt.timedelta(days=1)
    return {"start": start.isoformat(), "end": end.isoformat(), "days": days, "rows": rows,
            "missing_days": missing}


# --------------------------------------------------------------------------
# Report page
# --------------------------------------------------------------------------

class _ReportParser(HTMLParser):
    """Flattens the PAP report layout into sheets -> rows -> cells."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sheets: List[Dict] = [{"name": "", "rows": []}]
        self.attachments: List[Dict] = []
        self.header_date = None
        self.h1s: List[str] = []
        self.h1: List[str] = []
        self._in_h1 = False
        self._in_h4 = False
        self._h4: List[str] = []
        self._row: Optional[List[Dict]] = None
        self._cell: Optional[List[str]] = None
        self._bold = False
        self._a_href: Optional[str] = None
        self._a_text: List[str] = []
        self._in_date = False
        self._date: List[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "h1":
            self._in_h1, self.h1 = True, []
        elif tag == "h4":
            self._in_h4, self._h4 = True, []
        elif tag == "span" and "date" in (a.get("class") or "") and self.header_date is None:
            self._in_date, self._date = True, []
        elif tag == "tr":
            self._row = []
        elif tag == "td":
            self._cell = []
            self._bold = "bold" in (a.get("class") or "").lower()
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")
        elif tag == "a" and (a.get("href") or "").startswith("attachment/"):
            self._a_href, self._a_text = a["href"], []

    def handle_endtag(self, tag):
        if tag == "h1":
            self._in_h1 = False
            self.h1s.append(" ".join("".join(self.h1).split()))
        elif tag == "h4" and self._in_h4:
            self._in_h4 = False
            raw = "".join(self._h4)
            if "Nazwa arkusza" in raw:
                name = " ".join(raw.replace("Nazwa arkusza:", "").split())
                self.sheets.append({"name": name, "rows": []})
            else:  # page chrome (quotes widgets, footer) - collected but ignored
                self.sheets.append({"name": "", "rows": [], "ignore": True})
        elif tag == "span" and self._in_date:
            self._in_date = False
            self.header_date = " ".join("".join(self._date).split())
        elif tag == "td" and self._cell is not None and self._row is not None:
            txt = "".join(self._cell)
            txt = "\n".join(" ".join(line.split()) for line in txt.split("\n")).strip()
            if txt:
                self._row.append({"text": txt, "bold": self._bold})
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.sheets[-1]["rows"].append(self._row)
            self._row = None
        elif tag == "a" and self._a_href:
            name = " ".join("".join(self._a_text).split())
            if not any(x["href"] == self._a_href for x in self.attachments):
                self.attachments.append({"name": name, "href": self._a_href, "url": BASE + self._a_href})
            self._a_href = None

    def handle_data(self, data):
        if self._in_h1:
            self.h1.append(data)
        if self._in_h4:
            self._h4.append(data)
        if self._in_date:
            self._date.append(data)
        if self._cell is not None:
            self._cell.append(data)
        if self._a_href:
            self._a_text.append(data)


def _after(rows: List[List[Dict]], label: str) -> Optional[str]:
    """Text of the row following the row whose first cell starts with ``label``."""
    for i, row in enumerate(rows):
        if row and row[0]["text"].rstrip(":").strip().lower() == label.lower():
            if len(row) > 1 and row[1]["bold"]:
                return row[1]["text"]
            if i + 1 < len(rows) and rows[i + 1]:
                return rows[i + 1][0]["text"]
    return None


def _strip_title(h1: str, title: Optional[str]):
    """Remove the report title from the front of the ``<h1>``.

    A long title wraps inside the "Temat" table cell, so it holds line breaks
    (or double spaces) that the ``<h1>`` does not: compare whitespace- and
    case-insensitively. Returns ``(rest, stripped)``.
    """
    nh = " ".join((h1 or "").split())
    nt = " ".join((title or "").split())
    if nt and nh.lower().startswith(nt.lower()):
        return nh[len(nt):].strip(), True
    return nh, False


def _trailing_upper_name(text: str) -> str:
    """Fallback when the title could not be matched (e.g. truncated): the
    ``<h1>`` company is the issuer's full legal name in capitals ("BUDIMEX
    SPÓŁKA AKCYJNA"), so keep the trailing run of upper-case words."""
    words = text.split()
    k = len(words)
    while k > 0 and not any(ch.islower() for ch in words[k - 1]):
        k -= 1
    tail = words[k:]
    # a title ending in a year or number ("... za rok 2025") must not leak in
    while tail and not any(ch.isalpha() for ch in tail[0]):
        tail = tail[1:]
    if tail:
        return " ".join(tail)
    return text


def parse_report_html(html: str) -> Dict:
    """Parse a ``komunikat?geru_id=`` page.

    Returns ``{title, company, isin, header, legal_basis, body_text, esap_codes[],
    esap[], attachments[], sheets[]}`` where ``esap`` is
    ``[{code, description}]`` from the "METADANE ESAP" sheet and
    ``attachments`` is ``[{name, href, url}]`` (links only - not downloaded).
    """
    p = _ReportParser()
    p.feed(html or "")
    main = next((s for s in p.sheets if s["name"].upper().startswith("RAPORT")), None)
    rows = main["rows"] if main else [r for s in p.sheets for r in s["rows"]]
    title = _after(rows, "Temat")
    esap: List[Dict] = []
    meta = next((s for s in p.sheets if "ESAP" in s["name"].upper()), None)
    if meta:
        started = False
        for row in meta["rows"]:
            first = row[0]["text"]
            if first.lower().startswith("ramy prawne"):
                started = True
                continue
            if not started:
                continue
            if first in ("RegulatoryData",) or not row[0]["bold"]:
                break
            if re.fullmatch(r"[A-Z][A-Z0-9_]{2,15}", first):
                esap.append({"code": first, "description": " ".join(row[1]["text"].split()) if len(row) > 1 else ""})
    # the last <h1> is "<title> <COMPANY NAME (ISIN)>"
    h1 = p.h1s[-1] if p.h1s else ""
    h1, stripped = _strip_title(h1, title)
    cm = re.search(r"([^()]*?)\s*\(([A-Z]{2}[A-Z0-9]{9}\d)\)\s*$", h1)
    company, isin = (cm.group(1).strip(), cm.group(2)) if cm else (h1 or None, None)
    if company and not stripped and any(ch.islower() for ch in company):
        company = _trailing_upper_name(company)
    if not title:
        for s in p.sheets:
            for row in s["rows"]:
                if row and row[0]["bold"] and row[0]["text"].upper().startswith("RB"):
                    title = row[0]["text"].split(":", 1)[-1].strip()
                    break
            if title:
                break
    return {
        "title": title or "",
        "company": company,
        "isin": isin,
        "header": p.header_date,
        "legal_basis": _after(rows, "Podstawa prawna"),
        "body_text": _after(rows, "Treść raportu") or "",
        "esap_codes": [e["code"] for e in esap],
        "esap": esap,
        "attachments": p.attachments,
        "sheets": [s["name"] for s in p.sheets if s["name"] and not s.get("ignore")],
    }


def fetch_report(geru_id: int, cache_dir: Optional[Path] = None, offline: bool = False) -> Dict:
    """Fetch and parse one report page (cached for a year: reports are immutable;
    corrections are published as new reports)."""
    url = REPORT_URL.format(int(geru_id))
    resp = http.get(url, cache_dir=cache_dir, ttl_seconds=REPORT_TTL_S, offline=offline)
    out = parse_report_html(resp.text)
    out.update({"geru_id": int(geru_id), "url": url, "retrieved_at": resp.retrieved_at})
    out["class"] = classify(out["title"], out)
    return out


def summarize(rows: Iterable[Dict]) -> Dict[str, int]:
    """Count rows per class (all classes present, zero when absent)."""
    out = {c: 0 for c in CLASSES}
    for r in rows:
        out[r.get("class", "other")] = out.get(r.get("class", "other"), 0) + 1
    return out
