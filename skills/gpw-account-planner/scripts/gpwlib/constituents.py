"""Index constituents and sector membership from GPW Benchmark.

GPW Benchmark (the official index administrator, gpwbenchmark.pl) publishes the
current portfolio of every index. Membership of the 14 sector indices gives a
sector classification for WIG constituents.

Point-in-time caveat: the endpoint returns the *current* portfolio only. Using
it for past dates introduces mild look-ahead / survivorship bias in
sector-level history (not in market-wide breadth, which uses the full daily
archive universe). Reports state this explicitly.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from . import gpw_archive
from .gpw_archive import GPWFetchError, _http_get, default_cache_dir, parse_pl_number

PORTFOLIO_URL = (
    "https://gpwbenchmark.pl/ajaxindex.php?action=GPWIndexes&start=ajaxPortfolio"
    "&format=html&lang=PL&isin={isin}"
)

SIZE_INDICES = {
    "WIG20": "PL9999999987",
    "MWIG40": "PL9999999912",
    "SWIG80": "PL9999999979",
}

# 14 sector indices published by GPW Benchmark (verified 2026-11; WIG-TELKOM
# no longer exists). key -> (ISIN, English label, Polish label, group)
SECTOR_INDICES = {
    "WIG-BANKI": ("PL9999999904", "Banks", "Banki", "cyclical"),
    "WIG-BUDOW": ("PL9999999896", "Construction", "Budownictwo", "cyclical"),
    "WIG-INFO": ("PL9999999771", "IT", "Informatyka", "cyclical"),
    "WIG-GRY": ("PL9999998385", "Video games", "Gry", "cyclical"),
    "WIG-MOTO": ("PL9999999243", "Automotive", "Motoryzacja", "cyclical"),
    "WIG-ODZIEZ": ("PL9999999268", "Apparel", "Odzież", "cyclical"),
    "WIG-NRCHOM": ("PL9999999706", "Real estate", "Nieruchomości", "cyclical"),
    "WIG-MEDIA": ("PL9999999755", "Media", "Media", "cyclical"),
    "WIG-SPOZYW": ("PL9999999888", "Food", "Spożywczy", "defensive"),
    "WIG-LEKI": ("PL9999999250", "Pharma", "Leki", "defensive"),
    "WIG-ENERG": ("PL9999999516", "Energy utilities", "Energetyka", "defensive"),
    "WIG-GORNIC": ("PL9999999466", "Mining", "Górnictwo", "commodity"),
    "WIG-PALIWA": ("PL9999999722", "Fuels", "Paliwa", "commodity"),
    "WIG-CHEMIA": ("PL9999999847", "Chemicals", "Chemia", "commodity"),
}

CACHE_TTL_DAYS = 7


def _parse_portfolio(html: str) -> List[Dict]:
    parser = gpw_archive._QuotesTableParser()
    parser.feed(html)
    out = []
    for row in parser.rows:
        if len(row) < 5:
            continue
        name, isin = row[0].strip(), row[1].strip()
        if not name or len(isin) != 12:  # ISINs are 12 chars; foreign issuers (e.g. AT...) allowed
            continue
        out.append({"name": name, "isin": isin, "weight_pct": parse_pl_number(row[4])})
    return out


def fetch_portfolio(isin: str, cache_dir: Optional[Path] = None, offline: bool = False) -> Dict:
    cache_dir = cache_dir or default_cache_dir()
    path = cache_dir / "constituents" / "{}.json".format(isin)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            age = dt.date.today() - dt.date.fromisoformat(payload["retrieved_on"])
            if offline or age.days <= CACHE_TTL_DAYS:
                return payload
        except (ValueError, KeyError, OSError):
            pass
    if offline:
        return {"isin": isin, "members": [], "retrieved_on": None, "source_url": None}
    url = PORTFOLIO_URL.format(isin=isin)
    members = _parse_portfolio(_http_get(url))
    if not members:
        raise GPWFetchError("GPW Benchmark returned no constituents for {}".format(isin))
    payload = {
        "isin": isin,
        "members": members,
        "retrieved_on": dt.date.today().isoformat(),
        "source_url": url,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return payload


def size_membership(cache_dir=None, offline=False) -> Dict[str, str]:
    """name -> 'WIG20' | 'MWIG40' | 'SWIG80'."""
    out: Dict[str, str] = {}
    for idx, isin in SIZE_INDICES.items():
        for m in fetch_portfolio(isin, cache_dir, offline)["members"]:
            out.setdefault(m["name"], idx)
    return out


def sector_membership(cache_dir=None, offline=False) -> Dict[str, str]:
    """name -> sector index key (first match wins; GPW sector indices do not overlap)."""
    out: Dict[str, str] = {}
    for key, (isin, *_rest) in SECTOR_INDICES.items():
        for m in fetch_portfolio(isin, cache_dir, offline)["members"]:
            out.setdefault(m["name"], key)
    return out
