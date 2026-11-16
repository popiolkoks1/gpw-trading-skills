"""Managers' transactions (MAR art. 19): detection, ESMA-form parsing, aggregation.

Pure functions only; network access goes through ``gpwlib.espi`` in the CLI.

Legal frame (verified 2026-11-16)
---------------------------------
* Regulation (EU) No 596/2014 (MAR) art. 19(1): persons discharging managerial
  responsibilities (PDMR) and persons closely associated with them notify the
  issuer and the competent authority (KNF) of every own-account transaction in
  the issuer's shares, debt instruments or linked derivatives, promptly and no
  later than three business days after the transaction.
* Art. 19(8) as amended by the Listing Act (Regulation (EU) 2024/2809): the
  obligation starts once transactions reach EUR 20 000 within a calendar year
  (previously EUR 5 000); art. 19(9) lets authorities raise it to EUR 50 000
  or lower it to EUR 10 000. ESMA's list ESMA70-154-1020 (19 January 2026)
  names Malta (10k), Denmark, Germany and France (50k); Poland is not on it,
  so the EUR 20 000 default applies to GPW issuers. Small trades below the
  threshold are therefore invisible.
* Art. 19(11) closed period: 30 calendar days before the announcement of an
  interim or year-end report.
* Notification template: Commission Implementing Regulation (EU) 2016/523
  (sections 1 name, 2 position/status, 3 issuer, 4 a-f transaction details).

Volumes and prices are only in the PDF attachment, which scripts cannot
download from gpw.pl (WAF). They come exclusively from text the user pastes;
nothing here estimates or invents them.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Dict, Iterable, List, Optional, Tuple

from gpwlib import espi

# Cohen, Malloy & Pomorski (2012) / openinsider.com "cluster buys": at least
# three distinct insiders buying within a short window.
CLUSTER_MIN_INSIDERS = 3
CLUSTER_WINDOW_DAYS = 30
# MAR art. 19(11)
CLOSED_PERIOD_DAYS = 30
# Cohen, Malloy & Pomorski (2012): an insider is "routine" when he traded in
# the same calendar month in each of the three preceding years.
ROUTINE_YEARS = 3

_I = re.I | re.U

# --------------------------------------------------------------------------
# Detection from the ESPI list / report body
# --------------------------------------------------------------------------

_BUY = re.compile(r"\b(nabyci\w*|nabył\w*|zakup\w*|kupn\w*|objęci\w*|purchas\w*|acquisi\w*|acquir\w*|buy\w*|bought)\b", _I)
_SELL = re.compile(r"\b(zbyci\w*|zbył\w*|sprzeda\w*|sale|sold|sell\w*|dispos\w*)\b", _I)
_ROLES = [
    ("ceo", re.compile(r"prezes\w*\s+zarządu|\bCEO\b|president\s+of\s+the\s+management\s+board|chief\s+executive", _I)),
    ("vice_president", re.compile(r"wiceprezes\w*|vice[- ]president", _I)),
    ("supervisory_board", re.compile(r"(członk\w*|przewodnicz\w*|wiceprzewodnicz\w*)\s+rady\s+nadzorczej|rady\s+nadzorczej|supervisory\s+board", _I)),
    ("management_board", re.compile(r"członk\w*\s+zarządu|management\s+board|board\s+member", _I)),
    ("proxy", re.compile(r"prokurent\w*|proxy|procurator", _I)),
    ("cfo", re.compile(r"dyrektor\w*\s+finansow\w*|\bCFO\b|chief\s+financial", _I)),
]
_ASSOC = re.compile(r"blisko\s+związan\w*|closely\s+associated", _I)
_ENTITY_WORDS = re.compile(r"\b(sp\.\s*z\s*o\.?\s*o|spółk\w*|fundacj\w*|fundusz\w*|limited|holding\w*|trust|"
                           r"foundation|company|corporation|towarzystw\w*|stowarzyszeni\w*)", _I)
_ENTITY_ABBR = re.compile(r"(?:^|\s)(S\.A\.?|SA|Ltd\.?|LLC|L\.P\.|LP|GmbH|AG|SE|N\.V\.|NV|B\.V\.|BV|Inc\.?|TFI|ASI|"
                          r"sp\.\s*k\.?|sp\.\s*j\.?|S\.K\.A\.?|PLC|plc|S\.à\s?r\.l\.)(?=$|[\s,.;])", re.U)


def is_entity(name: str) -> bool:
    """Legal person (company, foundation, fund) rather than a natural person."""
    return bool(name) and bool(_ENTITY_WORDS.search(name) or _ENTITY_ABBR.search(name))
_UP = "A-ZŁŚŻŹĆÓĘĄŃ"
_LOW = "a-ząćęłńóśźż"
_ENTITY_HINT = re.compile(
    r"(?:przez|od)\s+(?:spółk\w+\s+|podmiot\w*\s+)?([" + _UP + r"0-9][\w\-.&\" ]{1,70}?"
    r"(?:S\.A\.|SA\b|Sp\.\s*z\s*o\.\s*o\.|sp\.\s*z\s*o\.\s*o\.|Fundacj\w*\s+Rodzinn\w*|Inc\.|Ltd\.?|Limited|LLC|SE\b|S\.K\.A\.))")
_PAN = re.compile(r"Pan(?:a|i|ią)?\s+([" + _UP + r"][" + _LOW + r"]+(?:[- ][" + _UP + r"][" + _LOW + r"]+){1,2})")
_PERSON = re.compile(
    r"(?:przez|od)\s+([" + _UP + r"][" + _LOW + r"]+(?:[- ][" + _UP + r"][" + _LOW + r"]+){1,2})")
_ROLE_WORD = re.compile(r"^(Prezes|Wiceprezes|Członk|Przewodnicz|Wiceprzewodnicz|Rad|Zarząd|Prokurent|Dyrektor|"
                        r"Osob|Spółk|Emitent|Akcjonariusz|Pan|Pani)", re.U)
_HOLDING_NOTICE = re.compile(r"art\.?\s*69\b|art\.?\s*70\b|o\s+ofercie\s+publicznej", _I)


def person_hint(body: str) -> Tuple[Optional[str], bool]:
    """(name as written - may be in Polish genitive -, is_entity) from a report body."""
    b = " ".join((body or "").split())
    m = _ENTITY_HINT.search(b)
    if m:
        return m.group(1).strip(), True
    m = _PAN.search(b)
    if m:
        return m.group(1), False
    for m in _PERSON.finditer(b):
        if not _ROLE_WORD.match(m.group(1)):
            return m.group(1), False
    return None, False


def detect_direction(text: str) -> Optional[str]:
    b, s = bool(_BUY.search(text or "")), bool(_SELL.search(text or ""))
    if b and not s:
        return "buy"
    if s and not b:
        return "sell"
    if b and s:
        return "mixed"
    return None


def detect_roles(text: str) -> List[str]:
    return [name for name, rx in _ROLES if rx.search(text or "")]


def mentions_managers_transaction(text: str) -> bool:
    """Text evidence of a MAR art. 19 notice. Needed because some issuers tag
    every report with the ESAP code TRANSD (seen on GPW in 2026-11), so the
    code alone is not proof. Delegates to the shared rule in
    ``gpwlib.espi.mentions_managers_transaction`` (also used by
    ``espi.classify``), so both stay equivalent."""
    return espi.mentions_managers_transaction(text)


def _non_market(text: str) -> Optional[str]:
    """Excluded nature (pledge, option exercise, ...) named in a title/body, else None."""
    n = classify_nature(text)
    return n if n in EXCLUDED_NATURES else None


def notice_from_row(row: Dict, body: Optional[Dict] = None) -> Dict:
    """ESPI list row (+ optional parsed report page) -> a detected notice.

    Only what the text says: direction and role are keyword hints; the person
    is a hint extracted from "przez <Name>" / "od <Name>" (Polish genitive, so
    it may be inflected, e.g. "Waldemara Lipkę").
    """
    title = row.get("title") or ""
    text = title + " " + ((body or {}).get("body_text") or "")
    person, entity = person_hint((body or {}).get("body_text") or "")
    holding = bool(_HOLDING_NOTICE.search((body or {}).get("body_text") or ""))
    nature = "holding_notice" if holding else _non_market(text)
    return {
        "date": row.get("date"), "time": row.get("time"), "company": row.get("company"),
        "isin": row.get("isin"), "title": title, "geru_id": row.get("geru_id"), "url": row.get("url"),
        "category": row.get("category"), "correction": bool(row.get("correction")),
        "direction_hint": None if nature else detect_direction(text),
        "nature_hint": nature,
        "entity_hint": entity,
        "roles_hint": detect_roles(text),
        "closely_associated_hint": bool(_ASSOC.search(text)),
        "person_hint": person,
        "esap_codes": (body or {}).get("esap_codes") or [],
        "attachments": [a.get("name") for a in (body or {}).get("attachments") or []],
        "details_fetched": body is not None,
    }


# --------------------------------------------------------------------------
# ESMA notification form parser (Implementing Regulation (EU) 2016/523)
# --------------------------------------------------------------------------

# Each field: list of label regexes (PL / EN variants seen on GPW notices)
_LABELS = [
    # section headers only terminate the previous value
    ("_section", [r"powód\s+powiadomienia|dane\s+dotyczące\s+emitenta[^\n]*|szczegółowe\s+informacje\s+dotyczące\s+transakcji[^\n]*|"
                  r"dane\s+(?:osoby|dotyczące\s+osoby)\s+pełniącej\s+obowiązki\s+zarządcze[^\n]*",
                  r"reason\s+for\s+the\s+notification|details\s+of\s+the\s+issuer[^\n]*|details\s+of\s+the\s+transaction[^\n]*|"
                  r"details\s+of\s+the\s+person\s+discharging[^\n]*"]),
    ("name", [r"imi[eę]\s+i\s+nazwisko\s*(?:/\s*nazwa)?|nazwa\s*/\s*imi[eę]\s+i\s+nazwisko|nazwa(?!\s+arkusza)",
              r"\bname\b(?!\s+of\s+the\s+issuer)(?:\s*\(full\s+name[^)]*\))?"]),
    ("position", [r"stanowisko\s*/\s*status|stanowisko|status", r"position\s*/\s*status|position"]),
    ("initial", [r"pierwotne\s+powiadomienie\s*/?\s*(?:zmiana)?|zmiana\s+powiadomienia",
                 r"initial\s+notification\s*/?\s*(?:amendment)?"]),
    ("issuer", [r"nazwa\s+(?:emitenta|podmiotu)|pełna\s+nazwa\s+podmiotu",
                r"name\s+of\s+the\s+issuer|full\s+name\s+of\s+the\s+entity|\bname\b\s*\(full"]),
    ("lei", [r"\bLEI\b"]),
    ("instrument", [r"opis\s+instrumentu\s+finansowego.*?(?:kod\s+identyfikacyjny|$)",
                    r"description\s+of\s+the\s+financial\s+instrument.*?(?:identification\s+code|$)"]),
    ("nature", [r"rodzaj\s+transakcji|charakter\s+transakcji", r"nature\s+of\s+the\s+transaction"]),
    ("prices", [r"cen[ay](?:\s*\(-?y\))?\s+i\s+wolumen(?:\s*\(-?y\))?|ceny?\s*\(?\w*\)?\s+i\s+wolumen\w*",
                r"price\s*\(?s?\)?\s+and\s+volume\s*\(?s?\)?"]),
    ("aggregated", [r"informacje\s+zbiorcze|łączny\s+wolumen", r"aggregated\s+information|aggregated\s+volume"]),
    ("date", [r"data\s+transakcji", r"date\s+of\s+the\s+transaction"]),
    ("place", [r"miejsce\s+transakcji|miejsce\s+zawarcia\s+transakcji", r"place\s+of\s+the\s+transaction"]),
]
_LABEL_RX = [(f, re.compile(r"(?:^|\n|\s)(?:\d\s*)?(?:[a-f]\)\s*)?(" + "|".join(v) + r")\s*[:\-–]?", _I)) for f, v in _LABELS]
_HEADER_RX = re.compile(
    r"dane\s+(?:osoby|dotyczące\s+osoby)\s+pełniącej\s+obowiązki\s+zarządcze|"
    r"details\s+of\s+the\s+person\s+discharging\s+managerial\s+responsibilities", _I)

_MONTHS = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6, "lipca": 7, "sierpnia": 8,
    "września": 9, "października": 10, "listopada": 11, "grudnia": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

_NATURE_RULES = [
    # exclusions first - not open-market decisions (Cohen-Malloy-Pomorski / openinsider
    # count only open-market purchases and sales)
    ("option_exercise", re.compile(r"wykonani\w*\s+(opcj|warrant|praw)|realizacj\w*\s+(opcj|warrant)|"
                                   r"exercise\s+of\s+(an?\s+)?(option|warrant|right)|option\s+exercise|warrant", _I)),
    ("incentive_plan", re.compile(r"program\w*\s+motywacyjn|plan\w*\s+motywacyjn|incentive|share[- ]based|"
                                  r"wynagrodzeni|remuneration|award|przydział|allotment|grant\b|"
                                  r"akcj\w*\s+fantomow|LTIP|objęci\w*\s+akcji\s+(serii|w\s+ramach)", _I)),
    ("gift", re.compile(r"darowizn|nieodpłatn|gift|donation|spadk|dziedzicz|inherit", _I)),
    ("pledge", re.compile(r"zastaw|pledge|zabezpieczeni\w*|collateral|lending|pożyczk\w*\s+papier", _I)),
    ("subscription", re.compile(r"zapis\w*\s+na\s+akcje|subskrypcj|subscription|emisj\w*\s+akcji|"
                                r"prawa?\s+poboru|rights\s+issue|ofert\w*\s+publiczn", _I)),
    ("tender", re.compile(r"wezwani|tender\s+offer|skup\w*\s+akcji\s+własnych|buy-?back", _I)),
    ("sell", re.compile(r"zbyci|sprzeda|\bsale\b|\bsell|dispos|\bsold\b", _I)),
    ("buy", re.compile(r"nabyci|kupn|zakup|purchase|acquisi|\bbuy\b|bought", _I)),
]
EXCLUDED_NATURES = {"option_exercise", "incentive_plan", "gift", "pledge", "subscription", "tender"}


def classify_nature(text: str) -> str:
    for name, rx in _NATURE_RULES:
        if rx.search(text or ""):
            return name
    return "other"


def _norm_text(text: str) -> str:
    t = (text or "").replace("\r", "\n").replace(" ", " ").replace(" ", " ").replace("\t", " ")
    t = re.sub(r"[ ]+", " ", t)
    return t


def parse_number(tok: str, english: bool = False) -> Optional[float]:
    """'1 000,50' / '1.000,50' / '1,000.50' / '45,2' -> float."""
    s = (tok or "").strip().replace(" ", "").replace(" ", "")
    if not s or not re.search(r"\d", s):
        return None
    s = re.sub(r"[^\d,.\-]", "", s)
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if english and all(len(p) == 3 for p in parts[1:]):
            s = s.replace(",", "")
        elif len(parts) > 2:
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    elif "." in s and not english:
        # Polish text: '1.000' is a thousands separator only when exactly 3 digits follow
        a, b = s.split(".")
        if len(b) == 3 and len(a) <= 3 and a not in ("0", "-0"):
            s = a + b
    try:
        return float(s)
    except ValueError:
        return None


_NUM_TOKEN = r"\d{1,3}(?:[ .]\d{3})+(?:,\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?"
_CCY = r"(?:PLN|zł|zl|EUR|USD|GBP|CHF|SEK|NOK|CZK|HUF)"


def parse_price_volume(seg: str, english: bool = False) -> List[Dict]:
    """Price/volume pairs from field 4c. Supports 'Cena: X PLN Wolumen: Y',
    'X PLN Y', tables 'X | Y' and 'Price(s) Volume(s) X Y' rows."""
    seg = seg or ""
    out: List[Dict] = []
    ccy_m = re.search(_CCY, seg, _I)
    ccy = ccy_m.group(0).upper().replace("ZŁ", "PLN").replace("ZL", "PLN") if ccy_m else None
    # 1) explicit labels
    lab = re.findall(r"(?:cena|price)\s*(?:jednostkowa|per\s+share)?\s*[:=]?\s*(" + _NUM_TOKEN + r")\s*" + _CCY + r"?"
                     r"[^0-9]{0,40}?(?:wolumen|volume|liczba\s+akcji|ilość|quantity)\s*[:=]?\s*(" + _NUM_TOKEN + r")",
                     seg, _I)
    lab_rev = re.findall(r"(?:wolumen|volume|liczba\s+akcji|ilość|quantity)\s*[:=]?\s*(" + _NUM_TOKEN + r")"
                         r"[^0-9]{0,40}?(?:cena|price)\s*[:=]?\s*(" + _NUM_TOKEN + r")", seg, _I)
    if lab:
        pairs = [(p, v) for p, v in lab]
    elif lab_rev:
        pairs = [(p, v) for v, p in lab_rev]
    else:
        # 2) "X PLN Y" rows (price followed by currency then volume)
        pairs = re.findall(r"(" + _NUM_TOKEN + r")\s*" + _CCY + r"\s*[|;/\-–]?\s*(" + _NUM_TOKEN + r")", seg, _I)
        if not pairs:
            # 3) bare rows: strip labels, take numbers in pairs per line
            pairs = []
            for line in seg.split("\n"):
                line = re.sub(r"(?i)cen[ay](\s*\(-?y\))?|wolumen(\s*\(-?y\))?|price\(?s?\)?|volume\(?s?\)?|\bi\b|\band\b", " ", line)
                nums = re.findall(_NUM_TOKEN, line)
                if len(nums) >= 2 and len(nums) % 2 == 0:
                    pairs += [(nums[i], nums[i + 1]) for i in range(0, len(nums), 2)]
    for p, v in pairs:
        price, vol = parse_number(p, english), parse_number(v, english)
        if price is None or vol is None or price <= 0 or vol <= 0:
            continue
        out.append({"price": price, "volume": vol, "currency": ccy})
    return out


def parse_date(seg: str) -> Optional[str]:
    s = seg or ""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", s)
    if m:
        return _iso(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.search(r"(\d{1,2})\s+([A-Za-zżźćńółęąśŻŹĆŃÓŁĘĄŚ]+)\s+(\d{4})", s)
    if m and m.group(2).lower() in _MONTHS:
        return _iso(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m.group(1).lower() in _MONTHS:
        return _iso(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
    return None


def _iso(y, mo, d) -> Optional[str]:
    try:
        return dt.date(y, mo, d).isoformat()
    except ValueError:
        return None


def _place_kind(seg: str) -> Optional[str]:
    s = seg or ""
    if re.search(r"poza\s+(systemem|obrotem|rynkiem)|outside\s+(a|the)\s+trading\s+venue|transakcj\w*\s+pakietow|OTC|"
                 r"poza\s+miejscem\s+obrotu|off[- ]market", s, _I):
        return "off_venue"
    if re.search(r"newconnect|XNCO", s, _I):
        return "newconnect"
    if re.search(r"giełd\w*\s+papierów|GPW|XWAR|Warsaw\s+Stock\s+Exchange|rynek\s+regulowany|regulated\s+market|WSE", s, _I):
        return "gpw"
    return "other" if s.strip() else None


def _segments(block: str) -> Dict[str, List[str]]:
    hits: List[Tuple[int, int, str]] = []
    for field, rx in _LABEL_RX:
        for m in rx.finditer(block):
            hits.append((m.start(1), m.end(), field))
    hits.sort()
    # drop overlaps (e.g. "status" inside "Stanowisko/status")
    clean: List[Tuple[int, int, str]] = []
    for h in hits:
        if clean and h[0] < clean[-1][1]:
            continue
        clean.append(h)
    seg: Dict[str, List[str]] = {}
    in_issuer = False
    for i, (s, e, f) in enumerate(clean):
        if f == "_section":
            label = block[s:e].lower()
            in_issuer = ("emitent" in label or "issuer" in label)
            continue
        if f == "name" and in_issuer:
            f = "issuer"
        nxt = clean[i + 1][0] if i + 1 < len(clean) else len(block)
        val = block[e:nxt]
        # strip the next row's ordinal like "\n3\n" or " b)" left at the end
        val = re.sub(r"(\s+\d{1,2}|\s+[a-f]\))\s*$", "", val.strip())
        seg.setdefault(f, []).append(val.strip(" :;-–\n"))
    return seg


def _first(seg, key) -> str:
    v = [x for x in seg.get(key, []) if x.strip()]
    return v[0].strip() if v else ""


def _clean_value(v: str, maxlen: int = 160) -> str:
    v = " ".join(v.split())
    v = re.sub(r"^(?:\d\s+)?(?:[a-f]\)\s*)", "", v)
    return v[:maxlen].strip(" :;-–")


def parse_notification(text: str) -> List[Dict]:
    """Pasted ESMA notification form text -> list of transactions.

    Several notifications (pasted together) and several transactions per
    notification (repeated section 4) are supported. Each transaction:
    ``{person, position, closely_associated, entity, issuer, lei, isin,
    instrument, nature_text, nature, direction, excluded, prices[],
    volume, avg_price, value, currency, aggregated{volume, price}|None,
    date, place, place_kind, amendment, warnings[]}``. Numbers come only from
    the text; when a value cannot be read it is ``None`` with a warning.
    """
    t = _norm_text(text)
    if not t.strip():
        return []
    starts = [m.start() for m in _HEADER_RX.finditer(t)]
    # bilingual forms repeat the header in EN right after PL; split only on headers
    # that are followed by a "name" label (start of a new notification)
    blocks = []
    if starts:
        bounds = starts + [len(t)]
        blocks = [t[bounds[i]:bounds[i + 1]] for i in range(len(starts))]
        if t[:starts[0]].strip() and re.search(r"rodzaj\s+transakcji|nature\s+of\s+the\s+transaction", t[:starts[0]], _I):
            blocks.insert(0, t[:starts[0]])
    else:
        blocks = [t]
    english = len(re.findall(r"\b(the|of|and|transaction)\b", t, _I)) > len(re.findall(r"\b(oraz|transakcji|i|na)\b", t, _I))
    txs: List[Dict] = []
    for block in blocks:
        seg = _segments(block)
        if not seg.get("nature") and not seg.get("prices"):
            continue
        person = _clean_value(_first(seg, "name"), 120)
        position = _clean_value(_first(seg, "position"))
        issuer = _clean_value(_first(seg, "issuer"), 120)
        lei_m = re.search(r"\b([A-Z0-9]{18}\d{2})\b", " ".join(seg.get("lei", [])) + " " + block)
        isin_m = re.search(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b", " ".join(seg.get("instrument", [])) + " " + block)
        amendment = bool(re.search(r"zmian|korekt|amendment", _first(seg, "initial"), _I)) and not re.search(
            r"pierwotne\s+powiadomienie\s*$|^\s*initial", _first(seg, "initial"), _I)
        associated = bool(_ASSOC.search(position))
        entity = is_entity(person)
        natures = seg.get("nature", [])
        prices = seg.get("prices", [])
        aggs = seg.get("aggregated", [])
        dates = seg.get("date", [])
        places = seg.get("place", [])
        n = max(len(natures), 1)
        for i in range(n):
            nature_text = _clean_value(natures[i] if i < len(natures) else "", 200)
            pv = parse_price_volume(prices[i] if i < len(prices) else "", english)
            agg = None
            if i < len(aggs):
                agg = _parse_aggregated(aggs[i], english)
            warnings = []
            vol = sum(x["volume"] for x in pv) if pv else None
            value = sum(x["volume"] * x["price"] for x in pv) if pv else None
            if not pv and agg and agg.get("volume") and agg.get("price"):
                vol, value = agg["volume"], agg["volume"] * agg["price"]
                warnings.append("price/volume rows not readable; used aggregated information (4d)")
            if vol is None:
                warnings.append("volume not found in the pasted text - left empty")
            if agg and agg.get("volume") and vol and abs(agg["volume"] - vol) > 0.5:
                warnings.append("sum of rows ({}) differs from aggregated volume ({})".format(vol, agg["volume"]))
            nature = classify_nature(nature_text)
            direction = "buy" if nature == "buy" else "sell" if nature == "sell" else (
                detect_direction(nature_text) if nature in EXCLUDED_NATURES else None)
            date = parse_date(dates[i] if i < len(dates) else (dates[-1] if dates else ""))
            if date is None:
                warnings.append("transaction date not found")
            place = _clean_value(places[i] if i < len(places) else (places[-1] if places else ""), 120)
            pk = _place_kind(place)
            currency = pv[0]["currency"] if pv and pv[0]["currency"] else None
            if pv and currency is None and pk in ("gpw", "newconnect"):
                currency = "PLN"
                warnings.append("currency not stated; PLN assumed for a GPW trade")
            tx = {
                "person": person or None, "position": position or None,
                "closely_associated": associated, "entity": entity,
                "issuer": issuer or None, "lei": lei_m.group(1) if lei_m else None,
                "isin": isin_m.group(1) if isin_m else None,
                "instrument": _clean_value(_first(seg, "instrument"), 120) or None,
                "nature_text": nature_text or None, "nature": nature, "direction": direction,
                "excluded": nature in EXCLUDED_NATURES or nature == "other",
                "prices": pv, "volume": vol,
                "avg_price": round(value / vol, 6) if (value is not None and vol) else None,
                "value": round(value, 2) if value is not None else None,
                "currency": currency,
                "aggregated": agg, "date": date, "place": place or None, "place_kind": pk,
                "amendment": amendment, "roles": detect_roles(position), "warnings": warnings,
            }
            tx["id"] = tx_id(tx)
            txs.append(tx)
    return dedupe(txs)


def _parse_aggregated(seg: str, english: bool) -> Optional[Dict]:
    vol = price = None
    m = re.search(r"(?:wolumen|volume|liczba)\w*\s*[:=]?\s*(" + _NUM_TOKEN + r")", seg, _I)
    if m:
        vol = parse_number(m.group(1), english)
    if vol is None:
        m = re.search(r"(" + _NUM_TOKEN + r")\s*(?:akcji|shares|szt)", seg, _I)
        if m:
            vol = parse_number(m.group(1), english)
    m = re.search(r"(?:cena|price)\w*\s*(?:średni\w*|average|weighted)?\s*[:=]?\s*(" + _NUM_TOKEN + r")", seg, _I)
    if m:
        price = parse_number(m.group(1), english)
    if vol is None and price is None:
        nums = re.findall(_NUM_TOKEN, seg)
        if len(nums) == 2:
            a, b = parse_number(nums[0], english), parse_number(nums[1], english)
            vol, price = (a, b) if (a or 0) > (b or 0) else (b, a)
    if vol is None and price is None:
        return None
    return {"volume": vol, "price": price}


def tx_id(tx: Dict) -> str:
    sig = "|".join(str(tx.get(k)) for k in ("person", "isin", "issuer", "date", "nature", "volume", "value"))
    return hashlib.sha1(sig.lower().encode("utf-8")).hexdigest()[:16]


def dedupe(txs: Iterable[Dict]) -> List[Dict]:
    """Drop exact duplicates (bilingual forms list each transaction twice)."""
    out, seen = [], set()
    for t in txs:
        sig = (t.get("date"), t.get("direction"), t.get("volume"), t.get("value"), (t.get("isin") or ""))
        if t.get("volume") is None:
            sig = sig + (t.get("nature_text"),)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(t)
    return out


# --------------------------------------------------------------------------
# Flags and aggregation
# --------------------------------------------------------------------------

def in_closed_period(tx_date: str, report_dates: Iterable[str]) -> Optional[bool]:
    """True when a periodic report was published within 30 days AFTER the
    transaction (MAR art. 19(11)); None when no report dates are known."""
    rd = sorted(set(d for d in report_dates if d))
    if not tx_date or not rd:
        return None
    d0 = dt.date.fromisoformat(tx_date)
    for r in rd:
        d1 = dt.date.fromisoformat(r)
        if d0 < d1 <= d0 + dt.timedelta(days=CLOSED_PERIOD_DAYS):
            return True
    if dt.date.fromisoformat(rd[-1]) <= d0:
        return None  # the next report is not known yet
    return False


def routine_flag(person: str, ticker: str, date: str, history: Iterable[Dict]) -> Optional[bool]:
    """Cohen-Malloy-Pomorski routine trader: same calendar month in each of the
    3 preceding years. None = not enough stored history to decide."""
    if not person or not date:
        return None
    d = dt.date.fromisoformat(date)
    mine = [h for h in history if (h.get("person") or "").lower() == person.lower()
            and (h.get("ticker") or "") == (ticker or "") and h.get("date")]
    if not mine:
        return None
    earliest = min(dt.date.fromisoformat(h["date"]) for h in mine)
    if earliest.year > d.year - ROUTINE_YEARS:
        return None
    months = {(dt.date.fromisoformat(h["date"]).year, dt.date.fromisoformat(h["date"]).month) for h in mine}
    return all((d.year - k, d.month) in months for k in range(1, ROUTINE_YEARS + 1))


def _cluster(events: List[Tuple[str, str]], as_of: str) -> Tuple[bool, int, Optional[str]]:
    """events: (date, insider_key). Cluster if >= 3 distinct keys in any 30-day
    window ending within the analysis period. Returns (flag, max distinct, window end)."""
    ev = sorted((dt.date.fromisoformat(d), k) for d, k in events if d and k)
    best, best_end = 0, None
    for i, (d_end, _k) in enumerate(ev):
        keys = {k for d, k in ev if d_end - dt.timedelta(days=CLUSTER_WINDOW_DAYS - 1) <= d <= d_end}
        if len(keys) > best:
            best, best_end = len(keys), d_end.isoformat()
    return best >= CLUSTER_MIN_INSIDERS, best, best_end


def _insider_key(tx_or_notice: Dict) -> Optional[str]:
    p = tx_or_notice.get("person") or tx_or_notice.get("person_hint")
    if not p:
        return None
    return " ".join(p.lower().split())


def aggregate(notices: List[Dict], transactions: List[Dict], as_of: str, *,
              report_dates: Optional[Dict[str, List[str]]] = None,
              history: Optional[List[Dict]] = None) -> List[Dict]:
    """Per-ticker summary (contract ``insiders.by_ticker[]``).

    ``notices``: detected ESPI notices with ``ticker``; ``transactions``:
    parsed transactions with ``ticker`` (from the store, already filtered to the
    period); ``report_dates`` {ticker: [periodic report dates]};
    ``history``: all stored transactions (routine flag).
    """
    report_dates = report_dates or {}
    history = history or []
    tickers = sorted({n.get("ticker") or n.get("isin") or n.get("company") for n in notices} |
                     {t.get("ticker") or t.get("isin") or t.get("issuer") for t in transactions} - {None})
    out = []
    for tk in tickers:
        ns = [n for n in notices if (n.get("ticker") or n.get("isin") or n.get("company")) == tk and not n.get("correction")]
        ts = [t for t in transactions if (t.get("ticker") or t.get("isin") or t.get("issuer")) == tk]
        if not ns and not ts:
            continue  # only corrections of earlier notices
        flags = []
        for t in ts:
            t["closed_period"] = in_closed_period(t.get("date"), report_dates.get(tk, []))
            t["routine"] = routine_flag(t.get("person"), t.get("ticker"), t.get("date"),
                                        [h for h in history if h.get("id") != t.get("id")])
        open_mkt = [t for t in ts if not t.get("excluded") and t.get("direction") in ("buy", "sell")]
        if ts:
            buys = sum(1 for t in open_mkt if t["direction"] == "buy")
            sells = sum(1 for t in open_mkt if t["direction"] == "sell")
            basis = "parsed_transactions"
        else:
            buys = sum(1 for n in ns if n.get("direction_hint") == "buy")
            sells = sum(1 for n in ns if n.get("direction_hint") == "sell")
            basis = "notice_hints"
        # net value only from parsed details, PLN only, never estimated
        vals = [t for t in open_mkt if t.get("value") is not None and (t.get("currency") in (None, "PLN"))]
        net = None
        if vals:
            net = round(sum(t["value"] if t["direction"] == "buy" else -t["value"] for t in vals), 2)
        # cluster: distinct natural persons (closely associated legal entities excluded)
        def _events(direction):
            ev = [(t.get("date"), _insider_key(t)) for t in open_mkt
                  if t["direction"] == direction and not t.get("entity")]
            ev += [(n.get("date"), _insider_key(n)) for n in ns
                   if n.get("direction_hint") == direction and _insider_key(n) and not n.get("entity_hint") and not ts]
            return ev
        cb, nb, _ = _cluster(_events("buy"), as_of)
        cs, nsell, _ = _cluster(_events("sell"), as_of)
        # parsed names are nominative, body hints often genitive: never mix the two
        persons = ({_insider_key(t) for t in ts if _insider_key(t)} if ts
                   else {_insider_key(n) for n in ns if _insider_key(n)})
        unknown_identity = sum(1 for n in ns if not _insider_key(n)) if not ts else 0
        buy_notices = sum(1 for n in ns if n.get("direction_hint") == "buy")
        sell_notices = sum(1 for n in ns if n.get("direction_hint") == "sell")
        if any(t.get("excluded") and t.get("nature") in EXCLUDED_NATURES for t in ts):
            flags.append("plan_or_non_market_excluded")
        if any(t.get("entity") for t in ts) or (not ts and any(n.get("entity_hint") for n in ns)):
            flags.append("closely_associated_entity")
        if any(t.get("closed_period") for t in open_mkt):
            flags.append("trade_in_closed_period")
        if any(t.get("routine") for t in open_mkt):
            flags.append("routine_trader")
        if any(t.get("place_kind") == "off_venue" for t in ts):
            flags.append("off_venue_trade")
        if not ts and ns:
            flags.append("details_missing_paste_pdf")
        if not ts and any(n.get("nature_hint") for n in ns):
            flags.append("non_market_notice:" + ",".join(sorted({n["nature_hint"] for n in ns if n.get("nature_hint")})))
        if not cb and buy_notices >= CLUSTER_MIN_INSIDERS and unknown_identity:
            flags.append("possible_cluster_buy_identity_unknown")
        if not cs and sell_notices >= CLUSTER_MIN_INSIDERS and unknown_identity:
            flags.append("possible_cluster_sell_identity_unknown")
        dates = [n.get("date") for n in ns if n.get("date")] + [t.get("date") for t in ts if t.get("date")]
        first = ns[0] if ns else (ts[0] if ts else {})
        out.append({
            "ticker": first.get("ticker") if first.get("ticker") else (tk if re.fullmatch(r"[A-Z0-9\-]{2,15}", str(tk)) else None),
            "isin": first.get("isin"), "company": first.get("company") or first.get("issuer"),
            "buys": buys, "sells": sells,
            "distinct_insiders": len(persons),
            "net_value_pln": net,
            "cluster_buy": cb, "cluster_sell": cs,
            "max_distinct_buyers_30d": nb, "max_distinct_sellers_30d": nsell,
            "last_date": max(dates) if dates else None,
            "notices": len(ns), "buy_notices": buy_notices, "sell_notices": sell_notices,
            "notices_identity_unknown": unknown_identity,
            "parsed_transactions": len(ts), "open_market_transactions": len(open_mkt),
            "basis": basis, "flags": flags,
            "transactions": [_tx_public(t) for t in ts],
            "notice_list": [{k: n.get(k) for k in ("date", "title", "direction_hint", "roles_hint",
                                                   "closely_associated_hint", "person_hint", "url")} for n in ns],
        })
    out.sort(key=lambda r: (-(r["cluster_buy"] or 0), -(r["buys"] + r["sells"]), str(r["ticker"] or r["company"])))
    return out


def _tx_public(t: Dict) -> Dict:
    keys = ("id", "date", "person", "position", "closely_associated", "entity", "nature", "direction", "excluded",
            "volume", "avg_price", "value", "currency", "place_kind", "closed_period", "routine", "warnings")
    return {k: t.get(k) for k in keys}
