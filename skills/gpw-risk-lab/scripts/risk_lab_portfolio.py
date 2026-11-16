"""gpw-risk-lab - portfolio input (pure, no network).

Parsing is copied from gpw-portfolio-check (skills never import each other's
scripts, CONVENTIONS rule 11) and adapted: the average purchase price is
optional here, because stress tests and hedges only need today's holdings.

Accepted inputs
---------------
* broker CSV: ``ticker,shares[,avg_price][,account_type]`` with ';' / tab /
  ',' separators, decimal commas, Polish headers (``Walor;Ilość;Średnia cena``),
  ``#`` comments, UTF-8 or Windows-1250;
* journal: ``gpwlib.journal_io.open_positions`` rows (FIFO remaining lots);
* inline: ``--positions PKOBP:100,KGHM:30``.
"""

from __future__ import annotations

import csv
import difflib
import io
import math
import re
from typing import Dict, Iterable, List, Optional, Tuple


class PortfolioError(ValueError):
    """Bad portfolio input (CSV, unknown ticker, empty portfolio)."""


ACCOUNT_ALIASES = {
    "regular": "regular", "zwykly": "regular", "zwykły": "regular", "standard": "regular",
    "standardowy": "regular", "maklerski": "regular", "rm": "regular", "normalny": "regular",
    "ike": "ike", "ikze": "ikze", "oki": "oki",
}

HEADER_ALIASES = {
    "ticker": {"ticker", "symbol", "walor", "instrument", "nazwa", "papier", "spolka", "spółka",
               "name", "kod", "nazwa_waloru", "skrot", "skrót"},
    "shares": {"shares", "ilosc", "ilość", "liczba", "liczba_akcji", "quantity", "qty", "szt",
               "sztuk", "ilosc_szt", "ilość_szt", "liczba_sztuk"},
    "avg_price": {"avg_price", "cena", "srednia_cena", "średnia_cena", "cena_srednia", "cena_średnia",
                  "kurs_sredni", "kurs_średni", "avg", "price", "cena_zakupu", "average_price",
                  "sredni_kurs", "średni_kurs", "cena_nabycia"},
    "account_type": {"account_type", "rachunek", "konto", "account", "typ_rachunku", "typ_konta"},
}

# Common 3-letter GPW tickers -> archive short names (same table as
# gpw-portfolio-check). Applied only if the target exists in the data.
TICKER_ALIASES = {
    "PKO": "PKOBP", "PKN": "PKNORLEN", "ORLEN": "PKNORLEN", "KGH": "KGHM", "CDR": "CDPROJEKT",
    "PEO": "PEKAO", "DNP": "DINOPL", "DINO": "DINOPL", "ALE": "ALLEGRO", "MBK": "MBANK",
    "KTY": "KETY", "ALR": "ALIOR", "PCO": "PEPCO", "BDX": "BUDIMEX", "TPE": "TAURONPE",
    "KRU": "KRUK", "OPL": "ORANGEPL", "CPS": "CYFRPLSAT", "ACP": "ASSECOPOL", "MIL": "MILLENNIUM",
    "ING": "INGBSK", "BHW": "HANDLOWY", "ENA": "ENEA", "ATT": "GRUPAAZOTY",
}


def parse_number(text) -> Optional[float]:
    """'1 234,56' / '1.234,56' / '1,234.56' / '12,5 zł' -> float; '' -> None."""
    if text is None:
        return None
    s = str(text).strip().replace("\xa0", "").replace(" ", "").replace(" ", "")
    for unit in ("zł", "PLN", "pln", "zl"):
        s = s.replace(unit, "")
    if s in ("", "-", "--"):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        raise PortfolioError("not a number: {!r}".format(text))
    if not math.isfinite(v):
        raise PortfolioError("not a finite number: {!r}".format(text))
    return v


def _norm_header(h: str) -> str:
    return h.strip().lstrip("﻿").strip().lower().replace(" ", "_").replace("-", "_").replace(".", "")


def _whole_shares(v: Optional[float], where: str) -> int:
    if v is None or v <= 0:
        raise PortfolioError("{}: shares must be a positive number".format(where))
    if abs(v - round(v)) > 1e-9:
        raise PortfolioError("{}: shares must be a whole number (GPW lot = 1 share)".format(where))
    return int(round(v))


def parse_holdings_csv(text: str) -> List[Dict]:
    """Parse a broker CSV (header required; ``avg_price`` optional)."""
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise PortfolioError("the holdings CSV is empty")
    first = lines[0]
    delim = ";" if ";" in first else ("\t" if "\t" in first else ",")
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)
    header = [_norm_header(h) for h in next(reader)]
    col: Dict[str, int] = {}
    for i, h in enumerate(header):
        for key, aliases in HEADER_ALIASES.items():
            if h in aliases and key not in col:
                col[key] = i
    missing = [k for k in ("ticker", "shares") if k not in col]
    if missing:
        raise PortfolioError("CSV header must contain ticker and shares (missing: {}; got: {})".format(
            ", ".join(missing), ", ".join(header)))
    rows = []
    for n, rec in enumerate(reader, start=2):
        if not any(c.strip() for c in rec):
            continue
        if len(rec) > len(header):
            raise PortfolioError(
                "line {}: more fields than columns - with ',' as separator, decimal commas must be quoted "
                "(\"12,50\") or use ';' as separator".format(n))
        rec = rec + [""] * (len(header) - len(rec))
        ticker = rec[col["ticker"]].strip().upper()
        if not ticker:
            raise PortfolioError("line {}: empty ticker".format(n))
        shares = _whole_shares(parse_number(rec[col["shares"]]), "line {}".format(n))
        price = parse_number(rec[col["avg_price"]]) if "avg_price" in col else None
        if price is not None and price <= 0:
            raise PortfolioError("line {}: avg_price must be a positive number".format(n))
        acct = "regular"
        if "account_type" in col and rec[col["account_type"]].strip():
            raw = rec[col["account_type"]].strip().lower()
            if raw not in ACCOUNT_ALIASES:
                raise PortfolioError("line {}: account_type must be regular, ike, ikze or oki (got {!r})".format(n, raw))
            acct = ACCOUNT_ALIASES[raw]
        rows.append({"ticker": ticker, "shares": shares, "avg_price": price, "account_type": acct})
    if not rows:
        raise PortfolioError("the holdings CSV has a header but no positions")
    return merge_rows(rows)


def parse_positions_arg(text: str) -> List[Dict]:
    """``PKOBP:100,KGHM:30`` (also ``;`` or spaces between items, ``=`` instead of ``:``)."""
    items = [x for x in re.split(r"[,;\s]+", (text or "").strip()) if x]
    if not items:
        raise PortfolioError("--positions is empty; use TICKER:SHARES,TICKER:SHARES")
    rows = []
    for it in items:
        m = re.match(r"^([A-Za-z0-9.\-_]+)[:=](\S+)$", it)
        if not m:
            raise PortfolioError("--positions item {!r} must look like TICKER:SHARES".format(it))
        rows.append({"ticker": m.group(1).upper(),
                     "shares": _whole_shares(parse_number(m.group(2)), "--positions {}".format(it)),
                     "avg_price": None, "account_type": "regular"})
    return merge_rows(rows)


def holdings_from_journal(open_rows: List[Dict]) -> List[Dict]:
    """Convert gpwlib.journal_io.open_positions rows (FIFO remaining lots)."""
    rows = [{"ticker": r["ticker"].upper(), "shares": int(r["shares"]),
             "avg_price": float(r["avg_price"]) if r.get("avg_price") else None,
             "account_type": r.get("account_type") or "regular"} for r in open_rows]
    if not rows:
        raise PortfolioError("the journal has no ACTIVE positions")
    return merge_rows(rows)


def merge_rows(rows: List[Dict]) -> List[Dict]:
    """Merge rows per ticker; keeps shares per account type."""
    out: Dict[str, Dict] = {}
    for r in rows:
        h = out.setdefault(r["ticker"], {"ticker": r["ticker"], "shares": 0, "cost": 0.0,
                                         "cost_known": True, "accounts": {}})
        h["shares"] += r["shares"]
        if r.get("avg_price") is None:
            h["cost_known"] = False
        else:
            h["cost"] += r["shares"] * r["avg_price"]
        acct = r.get("account_type") or "regular"
        h["accounts"][acct] = h["accounts"].get(acct, 0) + r["shares"]
    res = []
    for h in out.values():
        h["avg_price"] = round(h["cost"] / h["shares"], 4) if h["cost_known"] else None
        h["cost"] = round(h["cost"], 2) if h["cost_known"] else None
        res.append(h)
    return res


def resolve_ticker(ticker: str, names: Iterable[str]) -> Tuple[Optional[str], Optional[str]]:
    """Return (archive name, alias note) or (None, None)."""
    upper = {n.upper(): n for n in names}
    t = ticker.strip().upper()
    if t in upper:
        return upper[t], None
    alias = TICKER_ALIASES.get(t)
    if alias and alias in upper:
        return upper[alias], "{} -> {}".format(t, upper[alias])
    return None, None


def close_matches(ticker: str, names: Iterable[str], n: int = 6) -> List[str]:
    t = ticker.strip().upper()
    pool = sorted({x.upper() for x in names})
    sub = [x for x in pool if t in x or (len(x) >= 3 and x in t)]
    out: List[str] = []
    for x in sub + difflib.get_close_matches(t, pool, n=n, cutoff=0.6):
        if x not in out:
            out.append(x)
    return out[:n]
