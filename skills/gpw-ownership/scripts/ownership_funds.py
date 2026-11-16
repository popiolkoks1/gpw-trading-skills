"""OFE / PPK annual portfolio structure: parsing and aggregation (pure engine).

Data (verified 2026-11-16)
--------------------------
Pension funds (OFE) and PPK funds publish once a year the "Roczna struktura
aktywów" (art. 194 of the Act on pension funds): every holding with its
value in PLN and its % of fund assets at the year-end valuation date. There
are NO share counts. Files:

* analizy.pl document store: ``https://dokumenty.analizy.pl/pobierz/fe/<CODE>/RR/YYYY-12-31``
  (``ENNE1`` = Nationale-Nederlanden OFE, confirmed; other codes must be
  configured by the user - see references/data_sources.md);
* the PTE's own website (PDF), or a table the user copied to CSV/XLSX.

Two PDF layouts are handled: ``<name> <pct> <value>`` (NN OFE) and
``<name> <value> <pct>%`` (PPK funds). Only the section "Akcje spółek
notowanych na rynku regulowanym na terytorium Rzeczypospolitej Polskiej"
(Polish listed shares) is read by default.

Approximations (always labelled in the output):
* ``approx_shares = value / year-end close`` (raw, unadjusted close of the
  last session on or before the valuation date). Funds value at the closing
  price, so this is close, but rights, pre-IPO stakes and bonds convertible
  into shares in the same category add noise.
* Year-over-year flows: ``implied_flow_pln = value_now - value_prev x
  price_now / price_prev`` (price-only change; dividends received in cash do
  not change the holding, so the RAW price ratio is used, corrected for splits
  detected in the panel).
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from typing import Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree as ET

# Category headings of the "Roczna struktura aktywów" table
_PL_EQUITY = re.compile(r"akcje\s+spółek\s+notowan\w*\s+na\s+rynku\s+regulowanym\s+na\s+terytorium\s+"
                        r"(?:rzeczypospolitej|rp\b|polski)", re.I)
_NC_EQUITY = re.compile(r"akcje\s+spółek\s+notowan\w*\s+(?:w|na)\s+alternatywnym\s+systemie\s+obrotu", re.I)
_CATEGORY = re.compile(
    r"^(?:\d{1,2}\.?\s*)?(akcje|obligacje|depozyty|certyfikaty|tytuły|jednostki|listy\s+zastawne|kwity|inne\s+papiery|"
    r"środki\s+pieniężne|należności|dłużne|pożyczki|prawa\s+poboru|bankowe\s+papiery|instrumenty\s+pochodne|"
    r"lokaty|razem|suma|aktywa\s+razem)\b", re.I)
_NUM = r"\d{1,3}(?:[  ]\d{3})*,\d{2}"
_ROW_PCT_FIRST = re.compile(r"^(?P<name>.*?\D)\s+(?P<pct>\d{1,3},\d{2})\s+(?P<val>" + _NUM + r")$")
_ROW_VAL_FIRST = re.compile(r"^(?P<name>.*?\D)\s+(?P<val>" + _NUM + r")\s+(?P<pct>\d{1,3},\d{2})\s*%$")
_DATE = re.compile(r"data\s+wyceny\s*:?\s*(\d{2})[./-](\d{2})[./-](\d{4})", re.I)
_DATE2 = re.compile(r"(\d{1,2})\s+grudnia\s+(\d{4})", re.I)

# Approximate-flow thresholds for "top buys / sells": ignore changes smaller
# than 5% of the previous position or 1 mln PLN - below that the price-based
# decomposition is dominated by rounding of year-end prices and by corporate
# actions (see methodology).
FLOW_MIN_REL = 0.05
FLOW_MIN_PLN = 1_000_000
# A split/reverse split between two year-ends shows as a panel adjustment
# factor far from 1 (dividends are a few %).
SPLIT_FACTOR_DEV = 0.2
# "fund_accumulation" signal: implied net buying >= 20% of the previous
# position across all funds, by at least 2 funds (one fund alone is noise).
ACCUM_MIN_REL = 0.20
ACCUM_MIN_FUNDS = 2


class FundsParseError(ValueError):
    pass


def _num(s: str) -> Optional[float]:
    t = (s or "").replace(" ", " ").replace(" ", "").replace("%", "")
    if not t:
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    else:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


_FUND_RX = [
    re.compile(r"((?:[A-ZŁŚŻŹĆ][\w\-.&]*\s+){1,6}Otwarty\s+Fundusz\s+Emerytalny)"),
    re.compile(r"(PPK\s+[\w\-.]+(?:\s+[\w\-.]+){0,4}?\s+(?:DFE|SFIO|FIO))\b"),
    re.compile(r"((?:[A-ZŁŚŻŹĆ][\w\-.&]*\s+){1,5}(?:DFE|SFIO|Dobrowolny\s+Fundusz\s+Emerytalny))\b"),
]


def fund_name(text: str) -> Optional[str]:
    for rx in _FUND_RX:
        m = rx.search(text or "")
        if m:
            return " ".join(m.group(1).split())
    return None


def parse_lines(lines: Iterable[str], include_newconnect: bool = False) -> Dict:
    """Text lines of a fund report -> ``{fund, valuation_date, holdings[], section_found}``."""
    lines = [" ".join((ln or "").replace(" ", " ").split()) for ln in lines]
    fund = None
    vdate = None
    fund = fund_name(" ".join(lines[:40]))
    for ln in lines[:40]:
        m = _DATE.search(ln)
        if m and vdate is None:
            vdate = "{}-{}-{}".format(m.group(3), m.group(2), m.group(1))
    if vdate is None:
        for ln in lines[:40]:
            m = _DATE2.search(ln)
            if m:
                vdate = "{}-12-{:02d}".format(m.group(2), int(m.group(1)))
                break
    holdings: List[Dict] = []
    in_eq = False
    section_found = False
    pending = ""
    for ln in lines:
        if _PL_EQUITY.search(ln) or (include_newconnect and _NC_EQUITY.search(ln)):
            in_eq, section_found, pending = True, True, ""
            continue
        if in_eq and _CATEGORY.match(ln) and not _ROW_PCT_FIRST.match(ln) and not _ROW_VAL_FIRST.match(ln):
            # continuation of the heading ("tych spółek, a także ...") vs a new category
            if re.match(r"^(tych|na\s+akcje|obrotu|a\s+także)", ln, re.I):
                continue
            in_eq = False
            continue
        if in_eq and re.match(r"^\d{1,2}\.?\s+\D.*\d,\d{2}\s*%?$", ln) and _CATEGORY.match(re.sub(r"^\d{1,2}\.?\s*", "", ln)):
            in_eq = False
            continue
        if not in_eq:
            continue
        m = _ROW_PCT_FIRST.match(ln)
        kind = "pct_first"
        if not m:
            m = _ROW_VAL_FIRST.match(ln)
            kind = "val_first"
        if not m:
            # names wrapped over two lines
            # names wrapped over two lines; heading continuations start in lower case
            if not re.search(r"\d,\d{2}", ln) and len(ln) < 80 and not ln[:1].islower():
                pending = (pending + " " + ln).strip()
            else:
                pending = ""
            continue
        name = (pending + " " + m.group("name")).strip() if pending else m.group("name").strip()
        pending = ""
        if re.match(r"^\d{1,2}$", name) or re.match(r"^(tych|razem|suma)\b", name, re.I):
            continue
        holdings.append({"name": name, "value_pln": _num(m.group("val")), "pct_of_fund": _num(m.group("pct")),
                         "layout": kind})
    return {"fund": fund, "valuation_date": vdate, "holdings": holdings, "section_found": section_found}


# --------------------------------------------------------------------------
# XLSX / CSV
# --------------------------------------------------------------------------

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def xlsx_rows(data: bytes, max_rows: int = 20000) -> List[List[str]]:
    """First worksheet of an .xlsx as rows of cell strings (stdlib zipfile + XML)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise FundsParseError("not an .xlsx file (bad zip)")
    names = z.namelist()
    shared: List[str] = []
    if "xl/sharedStrings.xml" in names:
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", _NS):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % _NS["m"])))
    sheets = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    if not sheets:
        raise FundsParseError("xlsx has no worksheet")
    root = ET.fromstring(z.read(sheets[0]))
    rows: List[List[str]] = []
    for row in root.iter("{%s}row" % _NS["m"]):
        cells: Dict[int, str] = {}
        for c in row.findall("m:c", _NS):
            ref = c.get("r") or ""
            col = 0
            for ch in re.match(r"[A-Z]*", ref).group(0):
                col = col * 26 + (ord(ch) - 64)
            col = col - 1 if col else len(cells)
            t = c.get("t")
            v = c.find("m:v", _NS)
            if t == "s" and v is not None:
                val = shared[int(v.text)] if v.text and int(v.text) < len(shared) else ""
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter("{%s}t" % _NS["m"]))
            else:
                val = v.text if v is not None and v.text is not None else ""
            cells[col] = val
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
        if len(rows) >= max_rows:
            break
    return rows


def csv_rows(text: str) -> List[List[str]]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    return [r for r in csv.reader(io.StringIO(text), dialect)]


def _cell_num(s) -> Optional[float]:
    s = str(s or "").strip()
    if not s or not re.search(r"\d", s) or re.search(r"[A-Za-z]{3,}", s):
        return None
    if re.fullmatch(r"-?\d+(\.\d+)?(E[+-]?\d+)?", s, re.I):  # raw xlsx number
        try:
            return float(s)
        except ValueError:
            return None
    return _num(s)


def parse_table(rows: List[List[str]], include_newconnect: bool = False) -> Dict:
    """Rows from CSV/XLSX -> same structure as :func:`parse_lines`.

    With a header row (columns like nazwa/spółka/emitent/name, wartość/value,
    udział/%/share/pct, optional ticker) those columns are used; otherwise
    heading/row detection works like the PDF parser on the joined row text.
    """
    hdr_i, cols = None, {}
    for i, r in enumerate(rows[:30]):
        low = [str(c).strip().lower() for c in r]
        name_c = next((j for j, c in enumerate(low) if re.search(r"^(nazwa|spółka|emitent|name|company|issuer|papier)", c)), None)
        val_c = next((j for j, c in enumerate(low) if re.search(r"wartość|value|pln", c)), None)
        pct_c = next((j for j, c in enumerate(low) if re.search(r"udział|%|pct|share\s+of|weight", c) and j != val_c), None)
        if name_c is not None and val_c is not None:
            hdr_i = i
            cols = {"name": name_c, "value": val_c, "pct": pct_c,
                    "ticker": next((j for j, c in enumerate(low) if c in ("ticker", "kod", "symbol", "skrót")), None)}
            break
    fund, vdate = None, None
    for r in rows[:15]:
        line = " ".join(str(c) for c in r if c)
        m = _DATE.search(line)
        if m and not vdate:
            vdate = "{}-{}-{}".format(m.group(3), m.group(2), m.group(1))
        if not fund and re.search(r"fundusz\s+emerytaln|\bOFE\b|\bPPK\b", line, re.I):
            fund = line.strip()[:120]
    if hdr_i is not None:
        holdings = []
        for r in rows[hdr_i + 1:]:
            def g(k):
                j = cols.get(k)
                return r[j] if j is not None and j < len(r) else ""
            name = str(g("name")).strip()
            val = _cell_num(g("value"))
            if not name or val is None:
                continue
            pct = _cell_num(g("pct")) if cols.get("pct") is not None else None
            if pct is not None and pct <= 1.0 and str(g("pct")).strip().replace(".", "", 1).isdigit():
                pct = pct * 100.0 if "%" not in str(g("pct")) and pct < 1.0 else pct
            h = {"name": name, "value_pln": val, "pct_of_fund": pct, "layout": "table"}
            if cols.get("ticker") is not None and str(g("ticker")).strip():
                h["ticker"] = str(g("ticker")).strip().upper()
            holdings.append(h)
        return {"fund": fund, "valuation_date": vdate, "holdings": holdings, "section_found": True}
    # no header: treat rows as text lines
    lines = []
    for r in rows:
        cells = [str(c).strip() for c in r if str(c).strip()]
        if not cells:
            continue
        nums = [c for c in cells if _cell_num(c) is not None]
        texts = [c for c in cells if _cell_num(c) is None]
        if texts and len(nums) >= 2:
            a, b = _cell_num(nums[-2]), _cell_num(nums[-1])
            pct, val = (a, b) if a <= b else (b, a)
            lines.append("{} {} {}".format(" ".join(texts), _fmt(pct), _fmt(val)))
        else:
            lines.append(" ".join(cells))
    return parse_lines(lines, include_newconnect)


def _fmt(x: float) -> str:
    s = "{:,.2f}".format(x)
    return s.replace(",", " ").replace(".", ",")


# --------------------------------------------------------------------------
# Name -> ticker matching
# --------------------------------------------------------------------------

_LEGAL = re.compile(r"\b(s\.?\s?a\.?|spółka\s+akcyjna|se|n\.?\s?v\.?|plc|ag|s\.?p\.?a\.?|a\.?s\.?|ltd|limited|inc|corp|"
                    r"holdings?|group|grupa|asi|spółka|sa)\b\.?", re.I)

# Hand-checked aliases for names that no automatic rule can map (full name on
# fund reports -> GPW short name). Kept small; users can add more with
# --name-map. Checked against the GPW Benchmark portfolios (2026-11).
ALIASES = {
    "PKO BANK POLSKI": "PKOBP", "POWSZECHNY ZAKLAD UBEZPIECZEN": "PZU", "BANK POLSKA KASA OPIEKI": "PEKAO",
    "ORLEN": "PKNORLEN", "POLSKI KONCERN NAFTOWY ORLEN": "PKNORLEN", "BANK HANDLOWY W WARSZAWIE": "HANDLOWY",
    "BANK MILLENNIUM": "MILLENNIUM", "SANTANDER BANK POLSKA": "ERSTEPL", "BANCO SANTANDER": "SANTANDER", "GRUPA PRACUJ": "GRUPRACUJ",
    "IMC": "IMCOMPANY", "KOGENERACJA": "KOGENERA", "ZEW KOGENERACJA": "KOGENERA", "MCI CAPITAL": "MCI", "OCTAVA": "08OCTAVA", "ING BANK SLASKI": "INGBSK",
    "KGHM POLSKA MIEDZ": "KGHM", "PGE POLSKA GRUPA ENERGETYCZNA": "PGE", "TAURON POLSKA ENERGIA": "TAURONPE",
    "ORANGE POLSKA": "ORANGEPL", "CD PROJEKT": "CDPROJEKT", "DINO POLSKA": "DINOPL", "ASSECO POLAND": "ASSECOPOL",
    "GIELDA PAPIEROW WARTOSCIOWYCH W WARSZAWIE": "GPW", "ALLEGRO.EU": "ALLEGRO", "ALLEGROEU": "ALLEGRO",
    "INTER CARS": "INTERCARS", "GRUPA KETY": "KETY", "KETY": "KETY", "BNP PARIBAS BANK POLSKA": "BNPPPL",
    "ALIOR BANK": "ALIOR", "BANK PEKAO": "PEKAO", "BUDIMEX": "BUDIMEX", "ENEA": "ENEA", "JASTRZEBSKA SPOLKA WEGLOWA": "JSW",
    "LPP": "LPP", "MBANK": "MBANK", "ERSTE BANK POLSKA": "ERSTEPL",
    "ASSECO SOUTH EASTERN EUROPE": "ASSECOSEE", "ASSECO BUSINESS SOLUTIONS": "ASSECOBS", "AUTO PARTNER": "AUTOPARTN",
    "ZABKA GROUP": "ZABKA", "PEPCO GROUP": "PEPCO", "AMREST HOLDINGS": "AMREST", "BUDIMEX SA": "BUDIMEX",
    "GRUPA AZOTY": "GRUPAAZOTY", "11 BIT STUDIOS": "11BIT", "TEXT": "TEXT", "XTB": "XTB",
    "X-TRADE BROKERS DOM MAKLERSKI": "XTB", "KRUK": "KRUK",
    "WIRTUALNA POLSKA HOLDING": "WIRTUALNA", "BENEFIT SYSTEMS": "BENEFIT", "CYFROWY POLSAT": "CYFRPLSAT",
    "NEUCA": "NEUCA", "MODIVO": "MODIVO", "CCC": "MODIVO", "EUROCASH": "EUROCASH", "DEVELIA": "DEVELIA",
    "DOM DEVELOPMENT": "DOMDEV", "ATAL": "ATAL", "MIRBUD": "MIRBUD", "TORPOL": "TORPOL", "NEWAG": "NEWAG",
    "STALPRODUKT": "STALPROD", "STALEXPORT AUTOSTRADY": "STALEXP", "MENNICA POLSKA": "MENNICA",
    "RAINBOW TOURS": "RAINBOW", "TARCZYNSKI": "TARCZYNSKI", "SYNEKTIK": "SYNEKTIK", "SELVITA": "SELVITA",
    "RYVU THERAPEUTICS": "RYVU", "DIAGNOSTYKA": "DIAG", "SANOK RUBBER COMPANY": "SANOK", "T.C. DEBICA": "DEBICA",
    "TC DEBICA": "DEBICA", "UNICREDIT": "UNICREDIT", "ARCHICOM": "ARCHICOM", "MURAPOL": "MURAPOL",
    "MLP GROUP": "MLPGROUP", "PCC ROKITA": "PCCROKITA", "KINO POLSKA TV": "KINOPOL", "AGORA": "AGORA",
    "AMICA": "AMICA", "APATOR": "APATOR", "ATM GRUPA": "ATMGRUPA", "AB": "ABPL", "AC": "ACAUTOGAZ",
}


def norm_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", (name or "").replace("ł", "l").replace("Ł", "L"))
    s = "".join(c for c in s if not unicodedata.combining(c)).upper()
    s = re.sub(r"\(.*?\)", " ", s)
    s = s.replace("SPOLKA AKCYJNA", " ").replace("SPÓŁKA AKCYJNA", " ")
    s = re.sub(r"\b(S\.?\s?A|SE|N\.?\s?V|PLC|AG|S\.?P\.?A|A\.?S|LTD|LIMITED|INC|CORP|ASI|SA|SPOLKA)\b\.?", " ", s)
    s = re.sub(r"[\"'„”«»]", " ", s)
    return " ".join(s.replace(",", " ").split()).strip(" .-")


def match_ticker(name: str, tickers: Iterable[str], extra: Optional[Dict[str, str]] = None,
                 espi_names: Optional[Dict[str, str]] = None) -> Tuple[Optional[str], str]:
    """Fund holding name -> (ticker | None, method).

    Order: user map, ESPI company names seen locally (normalised full name ->
    ticker), built-in aliases, then automatic rules against GPW short names
    (compact name == ticker; first word == ticker when >= 4 letters; acronym).
    """
    tick = {t.upper() for t in tickers}
    n = norm_name(name)
    if not n:
        return None, "none"
    for src, table in (("user_map", extra or {}), ("espi_names", espi_names or {}), ("alias", ALIASES)):
        nt = {norm_name(k): v.upper() for k, v in table.items()}
        if n in nt and (not tick or nt[n] in tick or src == "user_map"):
            return nt[n], src
    compact = re.sub(r"[^A-Z0-9]", "", n)
    if compact in tick:
        return compact, "auto_compact"
    n2 = re.sub(r"^(GRUPA\s+KAPITALOWA|GRUPA|FABRYKI?|FFIL|ZAKLADY?|PRZEDSIEBIORSTWO)\s+", "", n)
    words = [w for w in re.split(r"[\s\-./_]+", n2) if w]
    if words:
        w0 = re.sub(r"[^A-Z0-9]", "", words[0])
        if len(w0) >= 4 and w0 in tick:
            return w0, "auto_first_word"
        two = re.sub(r"[^A-Z0-9]", "", "".join(words[:2]))
        if len(words) >= 2 and two in tick:
            return two, "auto_two_words"
        acro = "".join(w[0] for w in words if w[0].isalnum())
        if len(acro) >= 3 and acro in tick:
            return acro, "auto_acronym"
        joined = re.sub(r"[^A-Z0-9]", "", "".join(words))
        if joined in tick:
            return joined, "auto_compact"
        for w in words:
            ww = re.sub(r"[^A-Z0-9]", "", w)
            if len(ww) >= 5 and ww in tick:
                return ww, "auto_word"
        # unique ticker starting with the first word (GPW short names are often
        # truncated to 9-10 letters, e.g. AUTOPARTN, CAPTORTX)
        if len(w0) >= 5:
            cands = [t for t in tick if t.startswith(w0[:9])]
            if len(cands) == 1:
                return cands[0], "auto_prefix"
    return None, "none"


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def enrich(holdings: List[Dict], year_end_close: Dict[str, float]) -> None:
    """Add ``approx_shares`` (value / raw year-end close) in place."""
    for h in holdings:
        px = year_end_close.get(h.get("ticker") or "")
        h["year_end_close"] = px
        h["approx_shares"] = int(round(h["value_pln"] / px)) if (px and h.get("value_pln")) else None


def compare(cur: List[Dict], prev: List[Dict], split_factor: Dict[str, float]) -> Dict[str, Dict]:
    """Per ticker YoY change for ONE fund.

    ``split_factor[ticker]`` = product of split-like PRICE adjustment factors
    between the two dates as reported by ``panel`` (old prices are multiplied
    by it; a 1:10 split gives 0.1). Then the split-consistent previous price
    is ``price_prev x factor`` and the previous share count in today's units
    is ``shares_prev / factor``.
    """
    p = {h["ticker"]: h for h in prev if h.get("ticker")}
    c = {h["ticker"]: h for h in cur if h.get("ticker")}
    out = {}
    for t in sorted(set(p) | set(c)):
        a, b = p.get(t), c.get(t)
        va = a["value_pln"] if a else 0.0
        vb = b["value_pln"] if b else 0.0
        pa = a.get("year_end_close") if a else None
        pb = b.get("year_end_close") if b else None
        f = split_factor.get(t, 1.0) or 1.0
        sh_a = a.get("approx_shares") if a else None
        sh_b = b.get("approx_shares") if b else None
        if sh_a is not None and f != 1.0:
            sh_a = int(round(sh_a / f))
        flow = None
        if a and b and pa and pb:
            flow = vb - va * pb / (pa * f)
        elif b and not a:
            flow = vb
        elif a and not b:
            flow = -va
        out[t] = {"value_prev_pln": va, "value_pln": vb, "value_change_pln": vb - va,
                  "approx_shares_prev": sh_a if a else 0, "approx_shares": sh_b if b else 0,
                  "approx_shares_change": (sh_b - sh_a) if (a and b and sh_a is not None and sh_b is not None) else None,
                  "split_factor": f if f != 1.0 else None,
                  "implied_flow_pln": round(flow, 2) if flow is not None else None,
                  "status": "new" if b and not a else "exited" if a and not b else "held"}
    return out


def aggregate(funds: List[Dict], comparisons: Dict[str, Dict[str, Dict]]) -> Dict:
    """``funds``: [{fund, valuation_date, holdings[]}] (latest year per fund);
    ``comparisons``: {fund: compare(...)} -> contract ``funds.by_ticker[]``."""
    by: Dict[str, Dict] = {}
    for f in funds:
        for h in f["holdings"]:
            t = h.get("ticker")
            if not t:
                continue
            e = by.setdefault(t, {"ticker": t, "names": set(), "value_pln": 0.0, "approx_shares": 0,
                                  "approx_shares_complete": True, "funds_holding": 0, "per_fund": []})
            e["names"].add(h["name"])
            e["value_pln"] += h["value_pln"] or 0.0
            if h.get("approx_shares") is None:
                e["approx_shares_complete"] = False
            else:
                e["approx_shares"] += h["approx_shares"]
            e["funds_holding"] += 1
            e["per_fund"].append({"fund": f["fund"], "valuation_date": f["valuation_date"],
                                  "value_pln": h["value_pln"], "pct_of_fund": h.get("pct_of_fund"),
                                  "approx_shares": h.get("approx_shares")})
    for fund, comp in comparisons.items():
        for t, c in comp.items():
            e = by.setdefault(t, {"ticker": t, "names": set(), "value_pln": 0.0, "approx_shares": 0,
                                  "approx_shares_complete": True, "funds_holding": 0, "per_fund": []})
            e.setdefault("yoy", []).append(dict(c, fund=fund))
    rows = []
    for t, e in by.items():
        yoy = e.pop("yoy", [])
        flows = [y["implied_flow_pln"] for y in yoy if y.get("implied_flow_pln") is not None]
        prev_val = sum(y["value_prev_pln"] for y in yoy)
        net_flow = round(sum(flows), 2) if flows else None
        buyers = sum(1 for y in yoy if (y.get("implied_flow_pln") or 0) > 0 and _material(y))
        sellers = sum(1 for y in yoy if (y.get("implied_flow_pln") or 0) < 0 and _material(y))
        sh_changes = [y["approx_shares_change"] for y in yoy if y.get("approx_shares_change") is not None]
        rel = (net_flow / prev_val) if (net_flow is not None and prev_val > 0) else None
        rows.append({
            "ticker": t, "names": sorted(e["names"]),
            "value_pln": round(e["value_pln"], 2),
            "approx_shares": e["approx_shares"] if e["approx_shares_complete"] and e["funds_holding"] else None,
            "funds_holding": e["funds_holding"],
            "avg_pct_of_fund": round(sum((p.get("pct_of_fund") or 0) for p in e["per_fund"]) / len(e["per_fund"]), 4) if e["per_fund"] else None,
            "value_prev_pln": round(prev_val, 2) if yoy else None,
            "value_change_pln": round(e["value_pln"] - prev_val, 2) if yoy else None,
            "implied_flow_pln": net_flow,
            "implied_flow_rel": round(rel, 4) if rel is not None else None,
            "approx_shares_change": sum(sh_changes) if sh_changes else None,
            "funds_buying": buyers, "funds_selling": sellers,
            "fund_accumulation": bool(rel is not None and rel >= ACCUM_MIN_REL and buyers >= ACCUM_MIN_FUNDS),
            "approximate": True,
            "per_fund": e["per_fund"],
        })
    rows.sort(key=lambda r: -r["value_pln"])
    with_flow = [r for r in rows if r["implied_flow_pln"] is not None and _material_total(r)]
    top_buys = sorted([r for r in with_flow if r["implied_flow_pln"] > 0], key=lambda r: -r["implied_flow_pln"])[:10]
    top_sells = sorted([r for r in with_flow if r["implied_flow_pln"] < 0], key=lambda r: r["implied_flow_pln"])[:10]
    return {"by_ticker": rows,
            "top_buys": [{"ticker": r["ticker"], "implied_flow_pln": r["implied_flow_pln"], "funds_buying": r["funds_buying"]} for r in top_buys],
            "top_sells": [{"ticker": r["ticker"], "implied_flow_pln": r["implied_flow_pln"], "funds_selling": r["funds_selling"]} for r in top_sells]}


def _material(y: Dict) -> bool:
    f = abs(y.get("implied_flow_pln") or 0)
    base = max(y.get("value_prev_pln") or 0, 1.0)
    return f >= FLOW_MIN_PLN and (y["status"] != "held" or f / base >= FLOW_MIN_REL)


def _material_total(r: Dict) -> bool:
    f = abs(r["implied_flow_pln"] or 0)
    base = max(r.get("value_prev_pln") or 0, 1.0)
    return f >= FLOW_MIN_PLN and (r.get("value_prev_pln") in (0, None) or f / base >= FLOW_MIN_REL)
