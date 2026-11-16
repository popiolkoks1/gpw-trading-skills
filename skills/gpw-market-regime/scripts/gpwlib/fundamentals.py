"""Company fundamentals: gpw.pl company card, a local history store, xBRL / ESEF.

Sources (verified 2026-11-16)
-----------------------------
Indicators tab of the gpw.pl company card (one small HTML table)::

    https://www.gpw.pl/ajaxindex.php?start=indicatorsTab&format=html&action=GPWListaSp&gls_isin=<ISIN>&lang=PL

ISIN, market/segment, sector, shares issued, market value (mln zł), book
value (mln zł), P/BV (C/WK), P/E (C/Z), dividend yield (%).

Notoria tab of the same card (latest period only)::

    https://www.gpw.pl/ajaxindex.php?start=showNotoria&action=GPWListaSp&code=<TICKER>&isin=<ISIN>&format=html&lang=PL

Headline P&L, balance sheet and cash-flow lines for ONE period, labelled like
``I-III kw. 2026``. Units and semantics, as printed under the table:

* "Dane finansowe w tys. PLN" - values are thousands of PLN; this module
  multiplies by 1 000 and returns **PLN**. (The figures are in practice
  rounded to whole millions: ``30 081 000,00`` tys. = 30.081 bn PLN.)
* "Dane skonsolidowane" / "Dane jednostkowe" -> ``consolidated`` True/False.
* P&L and cash-flow lines are **cumulative year-to-date** for the label
  (``I-III kw.`` = 9 months); balance-sheet lines are at the period end.
* Banks and insurers use the same labels but different economics (e.g.
  "Przychody ze sprzedaży" of a bank is not revenue in the industrial sense);
  consumers must branch on sector.

Licence: data on gpw.pl (including the Notoria tab) is for information;
it stays in the user's local cache and must not be redistributed.

filings.xbrl.org (XBRL International, open data)::

    https://filings.xbrl.org/api/filings?filter=[{"name":"country","op":"eq","val":"PL"}]

JSON:API list of ESEF filings; each has ``json_url`` (xBRL-JSON, OIM). Polish
coverage is thin: annual reports for FY2020-2023 and a few Q1 2024 filings
(876 filings in Nov 2026, none for FY2025). User-supplied ESEF packages (zip
with inline XBRL) are parsed by :func:`parse_esef_zip`.

Ticker -> ISIN
--------------
The archive rows have names (tickers, "Nazwa" in the quotes archive) only.
:func:`isin_map` covers every listed share from the official quotes tables
(verified 2026-11-16; robots.txt of both hosts disallows one unrelated file
only)::

    https://www.gpw.pl/ajaxindex.php?action=GPWQuotations&start=showTable&tab=all&lang=PL&type=&full=1&format=html
    https://www.gpw.pl/ajaxindex.php?action=GPWQuotations&start=listSingle&tab=all&lang=PL&type=fix1&full=1&format=html
    https://www.gpw.pl/ajaxindex.php?action=GPWQuotations&start=listSingle&tab=all&lang=PL&type=fix2&full=1&format=html
    https://newconnect.pl/ajaxindex.php?action=NCExternalDataFrontController&start=showTable&tab=all&lang=PL&type=ALL&full=1&system_type=&format=html

These are the tables behind https://www.gpw.pl/akcje (continuous trading +
single-price auctions with one and two fixings) and
https://newconnect.pl/notowania. Each row has "Nazwa" (the short name used
in the archive, e.g. ``11BIT``), "ISIN" and "Skrót" (3-letter code). Four
requests, cached 7 days (:data:`LISTING_TTL_S`). Nov 2026: 402 Main Market
shares (369 continuous + 3 fix1 + 30 fix2) and 329 NewConnect shares.

:func:`resolve_isin` uses, in order: an explicit ISIN (``--isin``), the
user's override file ``<cache>/fundamentals/isin_overrides.json``, the quotes
tables above, then the GPW Benchmark index portfolios
(``constituents.fetch_portfolio``, cached 7 days) as a fallback, then the
pre-rename tickers of ``panel.RENAMES`` (``CCC`` -> MODIVO's ISIN). Only
the current listing is covered: a delisted company needs ``--isin``;
:func:`remember_isin` stores it for next time. :func:`ticker_for_company_name`
matches ESPI company names ("GRUPA AZOTY ZAKŁADY AZOTOWE PUŁAWY SPÓŁKA
AKCYJNA") to tickers when a row has no ISIN.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

from . import constituents, http
from .gpw_archive import GPWFetchError, default_cache_dir, parse_pl_number

INDICATORS_URL = "https://www.gpw.pl/ajaxindex.php"
INDICATORS_PARAMS = {"start": "indicatorsTab", "format": "html", "action": "GPWListaSp", "lang": "PL"}
NOTORIA_PARAMS = {"start": "showNotoria", "action": "GPWListaSp", "format": "html", "lang": "PL"}
CARD_URL = "https://www.gpw.pl/spolka?isin={}"
FILINGS_API = "https://filings.xbrl.org/api/filings"
FILINGS_BASE = "https://filings.xbrl.org"
SNAPSHOT_TTL_S = 86400          # card data changes at most daily
FILINGS_TTL_S = 7 * 86400
HISTORY_SCHEMA = 1
ISIN_INDICES = ["WIG20", "MWIG40", "SWIG80"]  # + sector indices + WIG + NCIndex below
WIG_ISIN = "PL9999999995"
NCINDEX_ISIN = "PL9999999565"


class FundamentalsError(RuntimeError):
    """Data missing or not parseable."""


# --------------------------------------------------------------------------
# Small table parser (th/td pairs)
# --------------------------------------------------------------------------

class _PairTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pairs: List[Tuple[str, str]] = []
        self.h3: List[str] = []
        self.text: List[str] = []
        self._cell: Optional[List[str]] = None
        self._kind = None
        self._row: List[Tuple[str, str]] = []
        self._in_h3 = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("th", "td"):
            self._cell, self._kind = [], tag
        elif tag == "h3":
            self._in_h3 = True
            self.h3.append("")

    def handle_endtag(self, tag):
        if tag in ("th", "td") and self._cell is not None:
            self._row.append((self._kind, " ".join("".join(self._cell).split())))
            self._cell = None
        elif tag == "tr":
            ths = [t for k, t in self._row if k == "th"]
            tds = [t for k, t in self._row if k == "td"]
            if ths and tds:
                self.pairs.append((ths[0], tds[0]))
            self._row = []
        elif tag == "h3":
            self._in_h3 = False

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        elif self._in_h3:
            self.h3[-1] += data
        else:
            self.text.append(data)


def _num(text: str) -> Optional[float]:
    return parse_pl_number(text) if text is not None else None


# --------------------------------------------------------------------------
# Indicators tab
# --------------------------------------------------------------------------

INDICATOR_LABELS = {
    "isin": "isin",
    "rynek/segment": "market_segment",
    "sektor": "sector",
    "liczba wyemitowanych akcji": "shares_issued",
    "wartość rynkowa (mln zł)": "market_cap_pln",
    "wartość księgowa (mln zł)": "book_value_pln",
    "c/wk": "pbv",
    "c/z": "pe",
    "stopa dywidendy (%)": "dividend_yield_pct",
}
_MLN = {"market_cap_pln", "book_value_pln"}
_TEXT_FIELDS = {"isin", "market_segment", "sector"}


def parse_indicators_html(html: str) -> Dict:
    """Indicators tab -> ``{isin, market_segment, sector, shares_issued,
    market_cap_pln, book_value_pln, pbv, pe, dividend_yield_pct, other{}}``.

    ``*_pln`` values are converted from mln zł to PLN. Missing / "-" -> None.
    Raises :class:`FundamentalsError` when the table is absent.
    """
    p = _PairTable()
    p.feed(html or "")
    if not p.pairs:
        raise FundamentalsError("indicators tab: no data table")
    out: Dict = {k: None for k in INDICATOR_LABELS.values()}
    out["other"] = {}
    for label, val in p.pairs:
        key = INDICATOR_LABELS.get(label.rstrip(":").strip().lower())
        if key is None:
            out["other"][label] = val
        elif key in _TEXT_FIELDS:
            out[key] = val.strip() or None
        else:
            v = _num(val)
            out[key] = v * 1e6 if (v is not None and key in _MLN) else v
    if out["shares_issued"] is not None:
        out["shares_issued"] = int(round(out["shares_issued"]))
    return out


# --------------------------------------------------------------------------
# Notoria tab
# --------------------------------------------------------------------------

NOTORIA_LABELS = {
    "przychody ze sprzedaży": "revenue",
    "zysk/strata ze sprzedaży": "profit_on_sales",
    "pozostałe przychody operacyjne": "other_operating_income",
    "pozostałe koszty operacyjne": "other_operating_costs",
    "zysk/strata z działalności operacyjnej": "operating_profit",
    "przychody finansowe": "financial_income",
    "koszty finansowe": "financial_costs",
    "zysk/strata brutto": "pretax_profit",
    "zysk/strata netto": "net_profit",
    "zysk/strata netto udziałowców jednostki dominującej": "net_profit_parent",
    "amortyzacja (noty)": "depreciation_amortization",
    "ebitda": "ebitda",
    "aktywa": "total_assets",
    "aktywa trwałe": "noncurrent_assets",
    "aktywa obrotowe": "current_assets",
    "kapitał własny udziałowców podmiotu dominującego": "equity_parent",
    "kapitał własny": "equity",
    "kapitał podstawowy": "share_capital",
    "zobowiązania długoterminowe": "noncurrent_liabilities",
    "zobowiązania krótkoterminowe": "current_liabilities",
    "przepływy operacyjne": "cfo",
    "przepływy inwestycyjne": "cfi",
    "nabycie rzeczowych aktywów trwałych oraz wartości niematerialnych": "capex",
    "przepływy finansowe": "cff",
    "przepływy pieniężne netto": "net_cash_flow",
}
FLOW_ITEMS = {"revenue", "profit_on_sales", "other_operating_income", "other_operating_costs", "operating_profit",
              "financial_income", "financial_costs", "pretax_profit", "net_profit", "net_profit_parent",
              "depreciation_amortization", "ebitda", "cfo", "cfi", "capex", "cff", "net_cash_flow"}
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4}


def parse_period_label(label: str) -> Dict:
    """``I-III kw. 2026`` -> {year: 2026, first_quarter: 1, last_quarter: 3,
    months: 9, cumulative: True, period_end: '2026-09-30'}. ``IV kw. 2025`` is
    a single quarter; ``2025`` / ``I-IV kw. 2025`` = full year. Unknown -> {}."""
    text = " ".join((label or "").split())
    m = re.match(r"^(I{1,3}V?|IV)(?:\s*-\s*(I{1,3}V?|IV))?\s*kw\.?\s*(\d{4})$", text, re.I)
    if m:
        q1 = _ROMAN.get(m.group(1).upper())
        q2 = _ROMAN.get((m.group(2) or m.group(1)).upper())
        year = int(m.group(3))
        if q1 and q2 and q1 <= q2:
            end_month = 3 * q2
            end = dt.date(year, end_month, [31, 30, 30, 31][q2 - 1])
            return {"year": year, "first_quarter": q1, "last_quarter": q2, "months": 3 * (q2 - q1 + 1),
                    "cumulative": q1 == 1 and q2 > 1, "period_end": end.isoformat()}
    m = re.match(r"^(\d{4})$", text)
    if m:
        y = int(m.group(1))
        return {"year": y, "first_quarter": 1, "last_quarter": 4, "months": 12, "cumulative": True,
                "period_end": dt.date(y, 12, 31).isoformat()}
    return {}


def parse_notoria_html(html: str) -> Optional[Dict]:
    """Notoria tab -> ``{period_label, period{}, consolidated, unit_source,
    multiplier, values{key: PLN}, raw{label: PLN}, unmapped[]}`` or None when the
    tab carries no table (e.g. funds, some foreign issuers).

    ``values`` uses the keys of :data:`NOTORIA_LABELS`; all numbers are PLN.
    """
    p = _PairTable()
    p.feed(html or "")
    if not p.pairs:
        return None
    footer = " ".join(" ".join(p.text).split()).lower()
    if "tys. pln" in footer or "tys. zł" in footer:
        mult, unit = 1e3, "thousand PLN"
    elif "mln pln" in footer or "mln zł" in footer:
        mult, unit = 1e6, "million PLN"
    else:
        mult, unit = 1e3, "thousand PLN (assumed - footer missing)"
    consolidated: Optional[bool] = None
    if "skonsolidowane" in footer:
        consolidated = True
    elif "jednostkowe" in footer:
        consolidated = False
    label = " ".join((p.h3[0] if p.h3 else "").split())
    values, raw, unmapped = {}, {}, []
    for lab, val in p.pairs:
        v = _num(val)
        v = None if v is None else v * mult
        raw[lab] = v
        key = NOTORIA_LABELS.get(lab.strip().lower())
        if key:
            values[key] = v
        else:
            unmapped.append(lab)
    return {"period_label": label, "period": parse_period_label(label), "consolidated": consolidated,
            "unit_source": unit, "multiplier": mult, "values": values, "raw": raw, "unmapped": unmapped,
            "source_note": "Notoria (via gpw.pl)"}


# --------------------------------------------------------------------------
# ISIN helpers
# --------------------------------------------------------------------------

def isin_is_valid(isin: str) -> bool:
    """ISO 6166 format + Luhn check digit."""
    s = (isin or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", s):
        return False
    digits = "".join(str(int(c, 36)) for c in s[:-1])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10 == int(s[-1])


def _overrides_path(cache_dir: Optional[Path]) -> Path:
    return Path(cache_dir or default_cache_dir()) / "fundamentals" / "isin_overrides.json"


def _read_json(path: Path) -> Dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def remember_isin(ticker: str, isin: str, cache_dir: Optional[Path] = None) -> None:
    """Store a user-confirmed ticker -> ISIN pair in the override file."""
    if not isin_is_valid(isin):
        raise ValueError("Invalid ISIN: {}".format(isin))
    path = _overrides_path(cache_dir)
    data = _read_json(path)
    data[ticker.strip().upper()] = isin.strip().upper()
    _write_json(path, data)


def _index_isins() -> List[str]:
    out = [constituents.SIZE_INDICES[k] for k in ISIN_INDICES]
    out += [v[0] for v in constituents.SECTOR_INDICES.values()]
    return out + [WIG_ISIN, NCINDEX_ISIN]


# Official quotes tables listing every share with "Nazwa" (archive short
# name), "ISIN" and "Skrót" - see the module docstring.
LISTING_SOURCES = [
    ("main", "https://www.gpw.pl/ajaxindex.php",
     {"action": "GPWQuotations", "start": "showTable", "tab": "all", "lang": "PL", "type": "", "full": "1",
      "format": "html"}),
    ("main", "https://www.gpw.pl/ajaxindex.php",
     {"action": "GPWQuotations", "start": "listSingle", "tab": "all", "lang": "PL", "type": "fix1", "full": "1",
      "format": "html"}),
    ("main", "https://www.gpw.pl/ajaxindex.php",
     {"action": "GPWQuotations", "start": "listSingle", "tab": "all", "lang": "PL", "type": "fix2", "full": "1",
      "format": "html"}),
    ("newconnect", "https://newconnect.pl/ajaxindex.php",
     {"action": "NCExternalDataFrontController", "start": "showTable", "tab": "all", "lang": "PL", "type": "ALL",
      "full": "1", "system_type": "", "format": "html"}),
]
LISTING_TTL_S = 7 * 86400
LISTING_SCHEMA = 1


class _ListingParser(HTMLParser):
    """Rows of a quotes table: the ``spolka?isin=`` link text + every cell."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: List[Dict] = []
        self._row: Optional[Dict] = None
        self._cell: Optional[List[str]] = None
        self._link: Optional[List[str]] = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = {"cells": [], "link_isin": None, "link_text": None}
        elif tag == "td" and self._row is not None:
            self._cell = []
        elif tag == "a" and self._row is not None:
            href = dict(attrs).get("href") or ""
            m = re.search(r"spolka\?isin=([A-Za-z0-9]{12})", href)
            if m and self._row["link_isin"] is None:
                self._row["link_isin"] = m.group(1).upper()
                self._link = []

    def handle_endtag(self, tag):
        if tag == "a" and self._link is not None and self._row is not None:
            self._row["link_text"] = " ".join("".join(self._link).split())
            self._link = None
        elif tag == "td" and self._cell is not None and self._row is not None:
            self._row["cells"].append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row["link_isin"]:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._link is not None:
            self._link.append(data)


def parse_listing_html(html: str, market: str = "main") -> List[Dict]:
    """``[{ticker, isin, code, market}]`` from a gpw.pl / newconnect.pl quotes
    table. ``ticker`` is the "Nazwa" link text (the archive name, e.g.
    ``11BIT``), ``code`` the 3-letter "Skrót" (the cell after the ISIN)."""
    parser = _ListingParser()
    parser.feed(html or "")
    out, seen = [], set()
    for r in parser.rows:
        # the link text carries status flags after the name: "PBG /Z /6,/7", "PKPCARGO /9"
        isin, name = r["link_isin"], re.sub(r"\s*/.*$", "", (r["link_text"] or "")).strip().upper()
        if not name or not isin_is_valid(isin) or isin in seen:
            continue
        cells = r["cells"]
        code = None
        if isin in cells:
            i = cells.index(isin)
            if i + 1 < len(cells) and re.fullmatch(r"[A-Z0-9]{2,5}", cells[i + 1] or ""):
                code = cells[i + 1]
        seen.add(isin)
        out.append({"ticker": name, "isin": isin, "code": code, "market": market})
    return out


def _listing_path(cache_dir: Optional[Path]) -> Path:
    return Path(cache_dir or default_cache_dir()) / "fundamentals" / "listed_companies.json"


def listed_companies(cache_dir: Optional[Path] = None, offline: bool = False, refresh: bool = False,
                     include_newconnect: bool = True, ttl_seconds: float = LISTING_TTL_S) -> Dict:
    """Every share listed today: ``{retrieved_at, sources[], companies[], counts{}, complete}``.

    ``companies`` items are ``{ticker, isin, code, market}`` (``market`` is
    ``main`` or ``newconnect``). Parsed result cached in
    ``<cache>/fundamentals/listed_companies.json`` for ``ttl_seconds``
    (7 days); ``refresh=True`` refetches, ``offline=True`` uses whatever is
    cached (any age) and never touches the network. A source that fails is
    skipped (``complete`` False); a total failure falls back to the stale
    file or returns an empty list - this function never raises on network
    errors.
    """
    path = _listing_path(cache_dir)
    stored = _read_json(path)
    fresh = False
    if stored.get("schema") == LISTING_SCHEMA and stored.get("companies"):
        ts = http._parse_ts(stored.get("retrieved_at") or "")
        fresh = bool(ts) and (http._now_utc() - ts).total_seconds() < ttl_seconds
    want = [src for src in LISTING_SOURCES if include_newconnect or src[0] != "newconnect"]

    def _filter(d: Dict) -> Dict:
        if include_newconnect:
            return d
        comps = [c for c in d.get("companies", []) if c.get("market") != "newconnect"]
        return dict(d, companies=comps)

    covers = stored.get("include_newconnect", True) or not include_newconnect
    if stored.get("companies") and (offline or (fresh and not refresh and covers)):
        return _filter(dict(stored, from_cache=True))
    companies: List[Dict] = []
    sources, failed = [], []
    seen = set()
    for market, url, params in want:
        try:
            resp = http.get(url, params, cache_dir=cache_dir, ttl_seconds=0 if refresh else ttl_seconds,
                            offline=offline)
            rows = parse_listing_html(resp.text, market)
        except (http.HTTPFetchError, ValueError):
            rows = []
        if not rows:
            failed.append(http.build_url(url, params))
            continue
        sources.append(http.build_url(url, params))
        for r in rows:
            if r["isin"] not in seen:
                seen.add(r["isin"])
                companies.append(r)
    if not companies:
        if stored.get("companies"):
            return _filter(dict(stored, from_cache=True, stale=True))
        return {"retrieved_at": None, "sources": [], "companies": [], "counts": {}, "complete": False,
                "failed": failed, "from_cache": False}
    counts: Dict[str, int] = {}
    for c in companies:
        counts[c["market"]] = counts.get(c["market"], 0) + 1
    out = {"schema": LISTING_SCHEMA, "retrieved_at": http._now_utc().isoformat(timespec="seconds"),
           "sources": sources, "failed": failed, "companies": companies, "counts": counts,
           "complete": not failed, "include_newconnect": include_newconnect}
    # a partial refresh (one page down) must not shrink a good cached listing
    if not failed or len(companies) >= 0.9 * len(stored.get("companies") or []):
        _write_json(path, out)
    return dict(out, from_cache=False)


def _benchmark_map(cache_dir: Optional[Path], offline: bool) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for idx in _index_isins():
        try:
            members = constituents.fetch_portfolio(idx, cache_dir, offline)["members"]
        except (GPWFetchError, http.HTTPFetchError):
            continue
        for m in members:
            if m.get("name") and m.get("isin"):
                out.setdefault(m["name"].upper(), m["isin"])
    return out


def _rename_aliases(current: Dict[str, str]) -> Dict[str, str]:
    """Old ticker -> ISIN of its newest name (panel.RENAMES), for names that
    are not a current ticker themselves."""
    from . import panel  # local import: panel does not depend on fundamentals
    out: Dict[str, str] = {}
    for new in panel.RENAMES:
        isin = current.get(new)
        if not isin:
            continue
        stack = [new]
        while stack:
            n = stack.pop()
            for old, _last in panel.RENAMES.get(n, []):
                if old not in current and old not in out:
                    out[old] = isin
                    stack.append(old)
    return out


def isin_sources(cache_dir: Optional[Path] = None, offline: bool = False,
                 refresh: bool = False) -> Dict[str, Tuple[str, str]]:
    """``{ticker: (ISIN, source)}`` with source ``override``, ``gpw_listing``,
    ``gpwbenchmark`` or ``rename_alias`` (see :func:`isin_map`)."""
    out: Dict[str, Tuple[str, str]] = {}
    listing = listed_companies(cache_dir, offline=offline, refresh=refresh)
    for c in listing.get("companies", []):
        out.setdefault(c["ticker"].upper(), (c["isin"], "gpw_listing"))
    for t, i in _benchmark_map(cache_dir, offline).items():
        out.setdefault(t, (i, "gpwbenchmark"))
    for t, i in _rename_aliases({k: v[0] for k, v in out.items()}).items():
        out.setdefault(t, (i, "rename_alias"))
    for k, v in _read_json(_overrides_path(cache_dir)).items():
        out[k.upper()] = (v, "override")
    return out


def isin_map(cache_dir: Optional[Path] = None, offline: bool = False, refresh: bool = False) -> Dict[str, str]:
    """``{ticker: ISIN}`` for every listed share.

    Order of precedence: user overrides, the official gpw.pl / newconnect.pl
    quotes tables (:func:`listed_companies`, ~400 Main Market + ~330
    NewConnect shares, cached 7 days), the GPW Benchmark portfolios (cached),
    then pre-rename tickers from ``panel.RENAMES``. ``refresh=True`` refetches
    the quotes tables.
    """
    return {t: v[0] for t, v in isin_sources(cache_dir, offline=offline, refresh=refresh).items()}


def resolve_isin(ticker: str, cache_dir: Optional[Path] = None, offline: bool = False,
                 explicit: Optional[str] = None) -> Dict:
    """``{ticker, isin, source}``; raises :class:`FundamentalsError` (message
    suggests ``--isin``) when the ticker is unknown. ``source`` is one of
    ``explicit``, ``override``, ``gpw_listing``, ``gpwbenchmark``,
    ``rename_alias``."""
    t = (ticker or "").strip().upper()
    if explicit:
        if not isin_is_valid(explicit):
            raise FundamentalsError("Invalid ISIN {!r} (expected e.g. PLPKO0000016)".format(explicit))
        return {"ticker": t, "isin": explicit.strip().upper(), "source": "explicit"}
    over = _read_json(_overrides_path(cache_dir))
    if t in {k.upper() for k in over}:
        isin = {k.upper(): v for k, v in over.items()}[t]
        return {"ticker": t, "isin": isin, "source": "override"}
    # cached data first (no network), then refresh online if still unknown
    passes = [True] if offline else [True, False]
    for off in passes:
        m = isin_sources(cache_dir, offline=off)
        if t in m:
            return {"ticker": t, "isin": m[t][0], "source": m[t][1]}
    raise FundamentalsError(
        "Unknown ticker {!r}: not listed on the GPW Main Market or NewConnect today (gpw.pl quotes tables). "
        "Pass its ISIN with --isin (shown on https://www.gpw.pl company card).".format(t))


def ticker_for_isin(isin: str, cache_dir: Optional[Path] = None, offline: bool = True) -> Optional[str]:
    """Reverse lookup (e.g. for ESPI rows, which carry ISINs). Offline by
    default (cached quotes tables / portfolios). Returns the current ticker,
    never a pre-rename alias."""
    isin = (isin or "").strip().upper()
    best = None
    for t, (i, src) in isin_sources(cache_dir, offline=offline).items():
        if i == isin:
            if src != "rename_alias":
                return t
            best = best or t
    return best


def isin_to_ticker_map(cache_dir: Optional[Path] = None, offline: bool = True) -> Dict[str, str]:
    """``{ISIN: current ticker}`` (one call for many ESPI rows)."""
    out: Dict[str, str] = {}
    for t, (i, src) in isin_sources(cache_dir, offline=offline).items():
        if src == "rename_alias":
            continue
        out.setdefault(i, t)
    return out


# Company-name matching (fallback when a row carries no ISIN)
_LEGAL_FORMS = [
    "spolka akcyjna w restrukturyzacji w likwidacji", "spolka akcyjna w restrukturyzacji",
    "spolka akcyjna w likwidacji", "spolka akcyjna w upadlosci", "spolka akcyjna", "spolka europejska",
    "spolka komandytowo akcyjna", "s a", "sa", "se", "s k a", "ska", "n v", "nv", "plc", "ag", "ab", "as",
    "corp", "inc", "ltd", "limited", "group", "grupa", "holding", "w restrukturyzacji", "w likwidacji",
]
_PL_FOLD = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def _strip_forms(name: str) -> str:
    import unicodedata
    s = (name or "").translate(_PL_FOLD).lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    text = " " + s + " "
    for form in sorted(_LEGAL_FORMS, key=len, reverse=True):
        while " " + form + " " in text:
            text = text.replace(" " + form + " ", " ")
    return text.strip().upper()


def normalize_company_name(name: str) -> str:
    """Upper-case ASCII letters and digits only, Polish letters folded, legal
    forms ("SPÓŁKA AKCYJNA", "S.A.", "SA", "SE", "N.V.", ...) and the words
    GRUPA / GROUP / HOLDING removed: ``"Grupa Azoty Zakłady Azotowe Puławy
    S.A."`` -> ``"AZOTYZAKLADYAZOTOWEPULAWY"``."""
    return re.sub(r"\s+", "", _strip_forms(name))


def ticker_for_company_name(name: str, cache_dir: Optional[Path] = None, offline: bool = True,
                            tickers: Optional[Iterable[str]] = None, min_len: int = 4) -> Optional[str]:
    """Best-effort ticker for a full company name (e.g. an ESPI row without
    ISIN); ``None`` unless exactly one ticker fits.

    The name is normalised (:func:`normalize_company_name`) and compared with
    the listed short names: an exact match wins; otherwise the candidates are
    short names that (a) start the name ("ASSECOPOL" for ASSECO POLAND),
    (b) equal one or more whole words of it, (c) are an in-order
    abbreviation of it (``_abbreviates``: "ERSTEPL" for ERSTE BANK POLSKA,
    "VIGOPHOTN" for VIGO PHOTONICS) or (d) contain the whole name ("ORLEN"
    -> "PKNORLEN"). Several candidates -> ``None``
    (e.g. ENERGOAPARATURA fits both ENAP and ENERGA). ``tickers`` overrides
    the candidate list (default: the current listing, offline). Prefer ISIN
    lookups whenever an ISIN is available.
    """
    target = normalize_company_name(name)
    if not target:
        return None
    words = _norm_words(name)
    pool = list(tickers) if tickers is not None else [
        t for t, (_, src) in isin_sources(cache_dir, offline=offline).items() if src != "rename_alias"]
    norm: Dict[str, set] = {}
    for t in pool:
        k = re.sub(r"[^A-Z0-9]", "", t.upper())
        if k:
            norm.setdefault(k, set()).add(t.upper())
    if target in norm:
        return next(iter(norm[target])) if len(norm[target]) == 1 else None
    spans = {"".join(words[i:j]) for i in range(len(words)) for j in range(i + 1, len(words) + 1)}
    cands = set()
    for k in norm:
        if len(k) < min_len:
            continue
        if (target.startswith(k) or k in spans or _abbreviates(k, target, words)
                or (len(target) >= 5 and target in k)):  # "ORLEN" -> "PKNORLEN"
            cands.add(k)
    if len(cands) == 1:
        k = next(iter(cands))
        if len(norm[k]) == 1:
            return next(iter(norm[k]))
    return None


def _norm_words(name: str) -> List[str]:
    return [w for w in re.split(r"\s+", _strip_forms(name)) if w]


def _abbreviates(k: str, target: str, words: List[str]) -> bool:
    """``k`` shares at least 4 leading characters with the name and its other
    characters follow in order - either the shared part already crosses a
    word boundary ("PLAZAC|NTR" for PLAZA CENTERS, "VIGOPHOT|N" for VIGO
    PHOTONICS) or the rest starts at a later word ("ERSTE|PL" for ERSTE BANK
    POLSKA). "ENERG|A" for ENERGOAPARATURA (one word) is not accepted."""
    n = 0
    while n < min(len(k), len(target)) and k[n] == target[n]:
        n += 1
    if n < 4 or n >= len(k):
        return False
    rest = k[n:]
    starts, pos = [], 0
    for w in words:
        starts.append(pos)
        pos += len(w)
    if len(words) > 1 and n > starts[1]:
        return _subsequence(rest, target[n:])
    return any(st >= n and target[st] == rest[0] and _subsequence(rest, target[st:]) for st in starts)


def _subsequence(needle: str, hay: str) -> bool:
    it = iter(hay)
    return all(ch in it for ch in needle)


# --------------------------------------------------------------------------
# Snapshot (network)
# --------------------------------------------------------------------------

def indicators_url(isin: str) -> str:
    return http.build_url(INDICATORS_URL, dict(INDICATORS_PARAMS, gls_isin=isin))


def notoria_url(ticker: str, isin: str) -> str:
    return http.build_url(INDICATORS_URL, dict(NOTORIA_PARAMS, code=ticker, isin=isin))


def snapshot(ticker: str, isin: Optional[str] = None, cache_dir: Optional[Path] = None,
             offline: bool = False, ttl_seconds: float = SNAPSHOT_TTL_S,
             ttl_indicators: Optional[float] = None, ttl_notoria: Optional[float] = None) -> Dict:
    """Indicators + Notoria for one company.

    Returns ``{ticker, isin, isin_source, retrieved_at, indicators{}, notoria{}|None,
    sources[], warnings[]}``. Two polite requests, each cached ``ttl_seconds``.
    ``ttl_indicators`` / ``ttl_notoria`` override the TTL of one tab (``None``
    = use ``ttl_seconds``): the indicators tab moves with the price every
    session, while the Notoria tab only changes when a new periodic report is
    published, so a screener can keep Notoria for weeks and refresh prices
    daily.
    """
    ttl_ind = ttl_seconds if ttl_indicators is None else ttl_indicators
    ttl_noto = ttl_seconds if ttl_notoria is None else ttl_notoria
    res = resolve_isin(ticker, cache_dir, offline, explicit=isin)
    t, i = res["ticker"], res["isin"]
    warnings: List[str] = []
    r1 = http.get(indicators_url(i), cache_dir=cache_dir, ttl_seconds=ttl_ind, offline=offline)
    try:
        ind = parse_indicators_html(r1.text)
    except FundamentalsError:
        raise FundamentalsError("gpw.pl returned no indicators for {} ({}); check the ticker/ISIN".format(t, i))
    if ind.get("isin") and ind["isin"] != i:
        warnings.append("Indicators tab reports ISIN {} (requested {})".format(ind["isin"], i))
    r2 = http.get(notoria_url(t, i), cache_dir=cache_dir, ttl_seconds=ttl_noto, offline=offline)
    noto = parse_notoria_html(r2.text)
    if noto is None:
        warnings.append("No Notoria financial data on the gpw.pl card for {}".format(t))
    elif noto["unmapped"]:
        warnings.append("Unmapped Notoria lines kept in raw: {}".format(", ".join(noto["unmapped"])))
    return {
        "ticker": t, "isin": i, "isin_source": res["source"],
        "retrieved_at": max(r1.retrieved_at, r2.retrieved_at),
        # per-tab retrieval times (the two tabs may come from caches of different age)
        "indicators_retrieved_at": r1.retrieved_at, "notoria_retrieved_at": r2.retrieved_at,
        "from_cache": bool(r1.from_cache and r2.from_cache),
        "indicators": ind, "notoria": noto,
        "sources": [CARD_URL.format(i), r1.url, r2.url],
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# Local history store
# --------------------------------------------------------------------------

def history_dir(store_dir: Optional[Path] = None) -> Path:
    return Path(store_dir) if store_dir else default_cache_dir() / "fundamentals" / "history"


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is b
    try:
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=0.5)
    except (TypeError, ValueError):
        return a == b


def _diff(old: Dict, new: Dict) -> Dict[str, list]:
    keys = set(old) | set(new)
    return {k: [old.get(k), new.get(k)] for k in sorted(keys) if not _same(old.get(k), new.get(k))}


def load_history(ticker: str, store_dir: Optional[Path] = None) -> Optional[Dict]:
    path = history_dir(store_dir) / "{}.json".format(ticker.strip().upper())
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FundamentalsError("Corrupt history file {}: {}".format(path, exc))
    return data if isinstance(data, dict) else None


def save_snapshot(snap: Dict, store_dir: Optional[Path] = None) -> Dict:
    """Merge a :func:`snapshot` into ``<store>/<TICKER>.json``.

    Periods are keyed by the Notoria label. Re-seeing a period with identical
    values only updates ``retrieved_at``. Different values (a restatement or
    a correction on the site) never overwrite silently: the new values become
    current, ``restated`` is set and the previous values are appended to
    ``revisions`` with what changed. Indicators are kept one per retrieval
    date (latest wins that day).

    Returns ``{path, period, status: new|unchanged|restated|no_notoria, changed{}}``.
    """
    t = snap["ticker"].strip().upper()
    path = history_dir(store_dir) / "{}.json".format(t)
    hist = load_history(t, store_dir) or {"schema": HISTORY_SCHEMA, "ticker": t, "isin": snap.get("isin"),
                                           "periods": {}, "indicators": []}
    when = snap.get("retrieved_at") or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    status, changed, label = "no_notoria", {}, None
    noto = snap.get("notoria")
    if noto and noto.get("period_label"):
        label = noto["period_label"]
        cur = hist["periods"].get(label)
        entry_vals = {k: v for k, v in noto["values"].items() if v is not None}
        if cur is None:
            hist["periods"][label] = {"period": noto.get("period") or {}, "consolidated": noto.get("consolidated"),
                                      "unit_source": noto.get("unit_source"), "values": entry_vals,
                                      "first_retrieved_at": when, "retrieved_at": when,
                                      "restated": False, "revisions": []}
            status = "new"
        else:
            changed = _diff(cur["values"], entry_vals)
            if cur.get("consolidated") != noto.get("consolidated"):
                changed["consolidated"] = [cur.get("consolidated"), noto.get("consolidated")]
            if changed:
                cur["revisions"].append({"replaced_at": when, "previous_retrieved_at": cur["retrieved_at"],
                                         "previous_values": cur["values"],
                                         "previous_consolidated": cur.get("consolidated"), "changed": changed})
                cur["values"] = entry_vals
                cur["consolidated"] = noto.get("consolidated")
                cur["restated"] = True
                status = "restated"
            else:
                status = "unchanged"
            cur["retrieved_at"] = max(cur["retrieved_at"], when)
    ind = snap.get("indicators") or {}
    if ind:
        day = when[:10]
        row = {"date": day, "retrieved_at": when,
               **{k: v for k, v in ind.items() if k != "other"}}
        hist["indicators"] = [r for r in hist.get("indicators", []) if r.get("date") != day] + [row]
        hist["indicators"].sort(key=lambda r: r["date"])
    hist["isin"] = snap.get("isin") or hist.get("isin")
    hist["updated_at"] = when
    _write_json(path, hist)
    return {"path": str(path), "period": label, "status": status, "changed": changed}


def period_sort_key(label: str, info: Optional[Dict] = None) -> Tuple:
    p = (info or {}).get("period") or parse_period_label(label)
    return (p.get("year", 0), p.get("last_quarter", 0), p.get("months", 0), label)


def history_series(hist: Dict, key: str) -> List[Dict]:
    """``[{label, period_end, months, value}]`` for one Notoria key, oldest first."""
    out = []
    for label, e in sorted((hist or {}).get("periods", {}).items(), key=lambda kv: period_sort_key(*kv)):
        if key in e.get("values", {}):
            p = e.get("period") or {}
            out.append({"label": label, "period_end": p.get("period_end"), "months": p.get("months"),
                        "value": e["values"][key], "restated": e.get("restated", False)})
    return out


# --------------------------------------------------------------------------
# xBRL: curated IFRS concepts
# --------------------------------------------------------------------------
# key -> ifrs-full concept names tried in order (first one reported wins).
# Names are from the IFRS Taxonomy (ifrs-full, 2022/2023 editions used by ESEF).
IFRS_CONCEPTS: Dict[str, List[str]] = {
    "Revenue": ["Revenue", "RevenueFromContractsWithCustomers", "RevenueFromSaleOfGoods"],
    "InterestRevenue": ["RevenueFromInterest", "InterestRevenueCalculatedUsingEffectiveInterestMethod"],
    "InterestExpense": ["InterestExpense"],
    "FeeAndCommissionIncome": ["FeeAndCommissionIncome"],
    "GrossProfit": ["GrossProfit"],
    "ProfitLossFromOperatingActivities": ["ProfitLossFromOperatingActivities"],
    "FinanceCosts": ["FinanceCosts"],
    "ProfitLossBeforeTax": ["ProfitLossBeforeTax"],
    "IncomeTaxExpense": ["IncomeTaxExpenseContinuingOperations"],
    "ProfitLoss": ["ProfitLoss"],
    "ProfitLossAttributableToOwnersOfParent": ["ProfitLossAttributableToOwnersOfParent"],
    "BasicEarningsLossPerShare": ["BasicEarningsLossPerShare"],
    "DilutedEarningsLossPerShare": ["DilutedEarningsLossPerShare"],
    "DepreciationAndAmortisation": ["DepreciationAndAmortisationExpense",
                                    "AdjustmentsForDepreciationAndAmortisationExpense",
                                    "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss"],
    "Assets": ["Assets"],
    "NoncurrentAssets": ["NoncurrentAssets"],
    "CurrentAssets": ["CurrentAssets"],
    "CashAndCashEquivalents": ["CashAndCashEquivalents"],
    "Inventories": ["Inventories"],
    "TradeAndOtherCurrentReceivables": ["TradeAndOtherCurrentReceivables"],
    "Liabilities": ["Liabilities"],
    "CurrentLiabilities": ["CurrentLiabilities"],
    "NoncurrentLiabilities": ["NoncurrentLiabilities"],
    "Equity": ["Equity"],
    "EquityAttributableToOwnersOfParent": ["EquityAttributableToOwnersOfParent"],
    "IssuedCapital": ["IssuedCapital"],
    "Borrowings": ["Borrowings"],
    "CurrentBorrowings": ["CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings", "ShorttermBorrowings"],
    "NoncurrentBorrowings": ["NoncurrentPortionOfNoncurrentBorrowings", "LongtermBorrowings"],
    "LeaseLiabilities": ["LeaseLiabilities"],
    "CashFlowsFromUsedInOperatingActivities": ["CashFlowsFromUsedInOperatingActivities"],
    "CashFlowsFromUsedInInvestingActivities": ["CashFlowsFromUsedInInvestingActivities"],
    "CashFlowsFromUsedInFinancingActivities": ["CashFlowsFromUsedInFinancingActivities"],
    "Capex": ["PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwillInvestmentPropertyAndOtherNoncurrentAssets",
              "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
              "PurchaseOfPropertyPlantAndEquipment"],
    "DividendsPaid": ["DividendsPaidClassifiedAsFinancingActivities",
                      "DividendsPaidToEquityHoldersOfParentClassifiedAsFinancingActivities", "DividendsPaid"],
    "DividendsPerShare": ["DividendsRecognisedAsDistributionsToOwnersPerShare"],
    "SharesOutstanding": ["NumberOfSharesOutstanding"],
    "WeightedAverageShares": ["WeightedAverageShares"],
}
def _xbrl_date(text: str, is_end: bool) -> Optional[dt.date]:
    """xBRL-JSON dates: 'YYYY-MM-DDT00:00:00' at an end/instant means the end of
    the previous day (OIM convention); a plain date is inclusive."""
    text = (text or "").strip()
    try:
        if "T" in text:
            d, t = text.split("T", 1)
            day = dt.date.fromisoformat(d)
            if is_end and t.startswith("00:00:00"):
                day -= dt.timedelta(days=1)
            return day
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _months(start: dt.date, end: dt.date) -> int:
    return int(round(((end - start).days + 1) / 30.4375))


def _collect(facts: Iterable[Dict], concepts: Dict[str, List[str]]) -> Dict[str, Dict]:
    """Common reducer. ``facts``: {concept, start|None, end, unit, decimals, value}."""
    key_of = {}
    for k, names in concepts.items():
        for rank, n in enumerate(names):
            key_of[n if ":" in n else "ifrs-full:" + n] = (k, rank)
    best: Dict[Tuple[str, str], Tuple] = {}
    currency: Dict[str, Dict[str, int]] = {}
    for f in facts:
        hit = key_of.get(f["concept"])
        if hit is None or f["value"] is None or f["end"] is None:
            continue
        key, rank = hit
        months = _months(f["start"], f["end"]) if f.get("start") else 0
        dec = f.get("decimals")
        try:
            dec = 99 if str(dec).upper() == "INF" else (-99 if dec in (None, "") else int(dec))
        except ValueError:
            dec = -99
        pe = f["end"].isoformat()
        # prefer: higher-priority concept, then longest duration, then most precise
        score = (-rank, months, dec)
        cur = best.get((pe, key))
        if cur is None or score > cur[0]:
            best[(pe, key)] = (score, f["value"], months, f.get("unit"))
        unit = f.get("unit") or ""
        m = re.match(r"iso4217:([A-Z]{3})$", unit)
        if m:
            currency.setdefault(pe, {}).setdefault(m.group(1), 0)
            currency[pe][m.group(1)] += 1
    out: Dict[str, Dict] = {}
    for (pe, key), (_score, val, months, _unit) in sorted(best.items()):
        row = out.setdefault(pe, {"_months": {}})
        row[key] = val
        if months:
            row["_months"][key] = months
    for pe, row in out.items():
        cur = currency.get(pe)
        row["_currency"] = max(cur, key=cur.get) if cur else None
    return out


def extract_ifrs_facts(xbrl_json: Dict, concepts: Optional[Dict[str, List[str]]] = None) -> Dict[str, Dict]:
    """xBRL-JSON (OIM) -> ``{period_end: {key: value, _months: {key: n}, _currency}}``.

    Only facts without extra dimensions (no axis members) are used, so values
    are the consolidated/entity totals. For flow concepts the longest duration
    ending on a date wins (annual over quarterly); ``_months`` tells which.
    Keys are those of :data:`IFRS_CONCEPTS`. Values are floats in the
    reporting currency (``_currency``; PLN for most GPW issuers) - EPS and DPS
    per share, share counts in shares.
    """
    facts = []
    for f in (xbrl_json or {}).get("facts", {}).values():
        dims = f.get("dimensions") or {}
        if set(dims) - {"concept", "entity", "period", "unit", "language"}:
            continue
        period = dims.get("period") or ""
        if "/" in period:
            s, e = period.split("/", 1)
            start, end = _xbrl_date(s, False), _xbrl_date(e, True)
        else:
            start, end = None, _xbrl_date(period, True)
        try:
            val = float(f.get("value")) if f.get("value") is not None else None
        except (TypeError, ValueError):
            continue
        facts.append({"concept": dims.get("concept"), "start": start, "end": end, "unit": dims.get("unit"),
                      "decimals": f.get("decimals"), "value": val})
    return _collect(facts, concepts or IFRS_CONCEPTS)


def list_pl_filings(cache_dir: Optional[Path] = None, offline: bool = False, page_size: int = 100,
                    max_pages: int = 20, ttl_seconds: float = FILINGS_TTL_S) -> List[Dict]:
    """All Polish filings on filings.xbrl.org -> ``[{fxo_id, lei, period_end,
    json_url, package_url, report_url, viewer_url, error_count, date_added}]``
    (URLs absolute), newest period first. Follows ``links.next``."""
    flt = json.dumps([{"name": "country", "op": "eq", "val": "PL"}], separators=(",", ":"))
    params = {"filter": flt, "page[size]": page_size, "sort": "-period_end"}
    out: List[Dict] = []
    for page in range(1, max_pages + 1):
        resp = http.get(FILINGS_API, dict(params, **{"page[number]": page}), cache_dir=cache_dir,
                        ttl_seconds=ttl_seconds, offline=offline)
        data = resp.json()
        for item in data.get("data", []):
            a = item.get("attributes", {})
            fxo = a.get("fxo_id") or ""
            out.append({
                "fxo_id": fxo, "lei": fxo.split("-", 1)[0] if fxo else None, "period_end": a.get("period_end"),
                "json_url": FILINGS_BASE + a["json_url"] if a.get("json_url") else None,
                "package_url": FILINGS_BASE + a["package_url"] if a.get("package_url") else None,
                "report_url": FILINGS_BASE + a["report_url"] if a.get("report_url") else None,
                "viewer_url": FILINGS_BASE + a["viewer_url"] if a.get("viewer_url") else None,
                "error_count": a.get("error_count"), "date_added": a.get("date_added"),
            })
        if not (data.get("links") or {}).get("next") or not data.get("data"):
            break
    return out


def load_xbrl_json(path_or_url: Union[str, Path], cache_dir: Optional[Path] = None, offline: bool = False) -> Dict:
    """Read xBRL-JSON from a local file or a URL (filings are immutable -> cached forever)."""
    s = str(path_or_url)
    if s.startswith("/") and "/ESEF/" in s and not Path(s).exists():
        s = FILINGS_BASE + s  # relative json_url from the API (/<LEI>/<date>/ESEF/...)
    if s.startswith("http://") or s.startswith("https://"):
        return http.get(s, cache_dir=cache_dir, ttl_seconds=None, offline=offline, timeout=60).json()
    try:
        return json.loads(Path(s).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FundamentalsError("Cannot read xBRL-JSON {}: {}".format(s, exc))


# --------------------------------------------------------------------------
# ESEF (inline XBRL in a zip)
# --------------------------------------------------------------------------

_CTX_FIELDS = {"xbrli:startdate": "start", "xbrli:enddate": "end", "xbrli:instant": "instant"}


class _IXParser(HTMLParser):
    """Collects xbrli contexts/units and ix:nonFraction facts (html.parser
    lower-cases tag and attribute names)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.contexts: Dict[str, Dict] = {}
        self.units: Dict[str, str] = {}
        self.facts: List[Dict] = []
        self._ctx: Optional[Dict] = None
        self._ctx_field: Optional[str] = None
        self._unit: Optional[str] = None
        self._unit_parts: List[str] = []
        self._in_measure = False
        self._measure: List[str] = []
        self._in_divide = False
        self._stack: List[Dict] = []
        self._text: List[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "xbrli:context":
            self._ctx = {"id": a.get("id"), "start": None, "end": None, "instant": None, "dims": False}
        elif self._ctx is not None and tag in _CTX_FIELDS:
            self._ctx_field, self._text = tag, []
        elif self._ctx is not None and tag in ("xbrli:segment", "xbrli:scenario", "xbrldi:explicitmember",
                                               "xbrldi:typedmember"):
            self._ctx["dims"] = True
        elif tag == "xbrli:unit":
            self._unit, self._unit_parts = a.get("id"), []
        elif tag == "xbrli:measure":
            self._in_measure, self._measure = True, []
        elif tag == "xbrli:unitdenominator":
            self._in_divide = True
        elif tag == "ix:nonfraction":
            self._stack.append({"name": a.get("name"), "context": a.get("contextref"), "unit": a.get("unitref"),
                                "decimals": a.get("decimals"), "scale": a.get("scale"), "sign": a.get("sign"),
                                "format": a.get("format") or "", "nil": (a.get("xsi:nil") or "").lower() == "true",
                                "text": []})

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "xbrli:context" and self._ctx is not None:
            self.contexts[self._ctx["id"]] = self._ctx
            self._ctx = None
        elif self._ctx_field and tag == self._ctx_field:
            self._ctx[_CTX_FIELDS[tag]] = "".join(self._text).strip()
            self._ctx_field = None
        elif tag == "xbrli:measure":
            self._in_measure = False
            m = "".join(self._measure).strip()
            self._unit_parts.append(("/" if self._in_divide else "") + m)
        elif tag == "xbrli:unitdenominator":
            self._in_divide = False
        elif tag == "xbrli:unit" and self._unit:
            self.units[self._unit] = "".join(self._unit_parts)
            self._unit = None
        elif tag == "ix:nonfraction" and self._stack:
            f = self._stack.pop()
            f["text"] = "".join(f["text"])
            if self._stack:  # nested fact: its text is also the parent's text
                self._stack[-1]["text"].append(f["text"])
            self.facts.append(f)

    def handle_data(self, data):
        if self._ctx_field:
            self._text.append(data)
        if self._in_measure:
            self._measure.append(data)
        if self._stack:
            self._stack[-1]["text"].append(data)


def ix_number(text: str, fmt: str = "", scale: Optional[str] = None, sign: Optional[str] = None) -> Optional[float]:
    """Inline-XBRL transform: ``ixt:num-dot-decimal`` (1,234.5), ``num-comma-decimal``
    (1.234,5 / 1 234,5), ``fixed-zero`` / ``zerodash`` ("-" = 0); then x 10^scale
    and the ``sign="-"`` attribute."""
    t = (text or "").strip().replace("\xa0", " ")
    f = (fmt or "").lower()
    if "zero" in f or t in ("-", "–", "—"):
        val = 0.0
    else:
        if "comma" in f:
            t = re.sub(r"[\s.']", "", t).replace(",", ".")
        else:
            t = re.sub(r"[\s,']", "", t)
        t = t.strip("()")
        if not re.fullmatch(r"\d+(\.\d+)?", t):
            return None
        val = float(t)
    if scale not in (None, ""):
        try:
            val *= 10 ** int(scale)
        except ValueError:
            return None
    if sign == "-":
        val = -val
    return val


def parse_ixbrl_html(text: str) -> List[Dict]:
    """Facts of one inline-XBRL document in the reducer format (dimensional
    contexts skipped)."""
    p = _IXParser()
    p.feed(text or "")
    out = []
    for f in p.facts:
        ctx = p.contexts.get(f["context"])
        if ctx is None or ctx["dims"] or f["nil"]:
            continue
        val = ix_number(f["text"], f["format"], f["scale"], f["sign"])
        if val is None:
            continue
        start = _xbrl_date(ctx["start"], False) if ctx.get("start") else None
        end = _xbrl_date(ctx.get("end") or ctx.get("instant") or "", False)
        unit = p.units.get(f["unit"] or "", f["unit"])
        out.append({"concept": f["name"], "start": start, "end": end, "unit": unit,
                    "decimals": f["decimals"], "value": val})
    return out


def parse_esef_zip(path: Union[str, Path], concepts: Optional[Dict[str, List[str]]] = None) -> Dict:
    """User-supplied ESEF package -> ``{facts: {period_end: {...}}, files[]}``
    with the same keys as :func:`extract_ifrs_facts`. Reads every .xhtml/.html
    inside the zip (``reports/`` folder in the ESEF layout)."""
    p = Path(path).expanduser()
    try:
        zf = zipfile.ZipFile(p)
    except (OSError, zipfile.BadZipFile) as exc:
        raise FundamentalsError("Not a readable ESEF zip {}: {}".format(p, exc))
    facts, files = [], []
    with zf:
        names = [n for n in zf.namelist() if n.lower().endswith((".xhtml", ".html", ".htm"))]
        reports = [n for n in names if "/reports/" in "/" + n.lower()] or names
        for n in reports:
            raw = zf.read(n)
            text, _enc = http.decode(raw)
            got = parse_ixbrl_html(text)
            if got:
                files.append(n)
                facts.extend(got)
    if not facts:
        raise FundamentalsError("No inline-XBRL facts found in {}".format(p))
    return {"facts": _collect(facts, concepts or IFRS_CONCEPTS), "files": files}
