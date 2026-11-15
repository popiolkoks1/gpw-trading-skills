"""Catalog of ETFs listed on GPW (Warsaw Stock Exchange), keyed by GPW short name.

Compiled on ``CATALOG_DATE`` from the GPW ETF archive (instrument type 241)
and the funds' public names. It is a *map for beginners*, not a fund
database: every entry must be verified in the fund's KID ("KID / KIID",
kluczowe informacje) and statute/prospectus before investing.

Fields
------
issuer        - fund brand / manager; ``None`` where the short name alone does
                not identify it reliably ("verify").
underlying    - what the fund tracks, PL and EN; entries marked
                ``confidence="name"`` are inferred from the name only.
category      - one of ``CATEGORIES``.
currency      - main currency exposure the investor bears: "PLN",
                "PLN-hedged" (foreign assets with the currency risk hedged to
                PLN), "USD", "EUR", "JPY", "multi" (many currencies, mostly USD).
equity_share  - fraction of the fund in shares (1.0 equity ETF, 0.6 a 60/40
                mixed fund, 0 bonds/cash/commodities/crypto) - used for the
                equity vs bonds/cash split of a portfolio.
oki_hint      - "likely_qualified" ONLY where the underlying is Polish shares
                or PLN bonds/cash held directly (the OKI act's >= 70% qualified
                assets test, art. 26 Dz.U. 2026 poz. 1098); everything else is
                "not_qualified". Always "verify in the fund statute".
TER is deliberately NOT stored: costs change and must come from the KID
(the CLI accepts ``--ter NAME=0.5``).
"""

from __future__ import annotations

import re
from typing import Dict, Optional

CATALOG_DATE = "2026-11-15"

CATEGORIES = ("pl_equity", "global_equity", "us_equity", "other_equity", "mixed", "pl_bonds", "cash",
              "gold_commodity", "crypto", "thematic", "leveraged", "short", "uncategorised")

CATEGORY_LABELS = {
    "pl_equity": {"pl": "akcje polskie", "en": "Polish equities"},
    "global_equity": {"pl": "akcje globalne", "en": "global equities"},
    "us_equity": {"pl": "akcje USA", "en": "US equities"},
    "other_equity": {"pl": "akcje innych rynków", "en": "other equity markets"},
    "mixed": {"pl": "mieszany (akcje + obligacje)", "en": "mixed (equities + bonds)"},
    "pl_bonds": {"pl": "obligacje polskie", "en": "Polish bonds"},
    "cash": {"pl": "gotówkowy / krótkie obligacje", "en": "cash-like / short bonds"},
    "gold_commodity": {"pl": "złoto / surowce", "en": "gold / commodities"},
    "crypto": {"pl": "kryptowaluty", "en": "crypto"},
    "thematic": {"pl": "tematyczny", "en": "thematic"},
    "leveraged": {"pl": "LEWAROWANY", "en": "LEVERAGED"},
    "short": {"pl": "ODWROTNY (short)", "en": "INVERSE (short)"},
    "uncategorised": {"pl": "nieskategoryzowany", "en": "uncategorised"},
}

# Categories counted as bonds/cash for the equity-vs-safety split.
SAFE_CATEGORIES = ("pl_bonds", "cash")


def _e(issuer, cat, cur, eq, under_pl, under_en, oki="not_qualified", conf="known", lev=1.0):
    return {"issuer": issuer, "category": cat, "currency": cur, "equity_share": eq,
            "underlying_pl": under_pl, "underlying_en": under_en, "oki_hint": oki,
            "confidence": conf, "leverage": lev}


LQ = "likely_qualified"

CATALOG: Dict[str, Dict] = {
    # --- Polish equities (Beta ETF, TFI PZU)
    "ETFBW20TR": _e("Beta ETF", "pl_equity", "PLN", 1.0, "WIG20TR — 20 największych spółek GPW (z dywidendami)",
                    "WIG20TR — 20 largest GPW companies (total return)", LQ),
    "ETFBM40TR": _e("Beta ETF", "pl_equity", "PLN", 1.0, "mWIG40TR — 40 średnich spółek GPW",
                    "mWIG40TR — 40 mid-cap GPW companies", LQ),
    "ETFBS80TR": _e("Beta ETF", "pl_equity", "PLN", 1.0, "sWIG80TR — 80 małych spółek GPW",
                    "sWIG80TR — 80 small-cap GPW companies", LQ),
    "ETFBDIVPL": _e("Beta ETF", "pl_equity", "PLN", 1.0, "polskie spółki dywidendowe (sprawdź indeks)",
                    "Polish dividend stocks (verify index)", LQ, "name"),
    "ETFPZUW20M40": _e("TFI PZU (inPZU)", "pl_equity", "PLN", 1.0, "duże i średnie spółki GPW (WIG20 + mWIG40)",
                       "large and mid GPW caps (WIG20 + mWIG40)", LQ, "name"),
    # --- global / US / other equities
    "ETFIWDA": _e("iShares (BlackRock)", "global_equity", "multi", 1.0, "MSCI World — ok. 1 400 spółek z krajów rozwiniętych",
                  "MSCI World — about 1,400 developed-market companies"),
    "ETFPZUWORLD": _e("TFI PZU (inPZU)", "global_equity", "multi", 1.0, "akcje krajów rozwiniętych (sprawdź indeks)",
                      "developed-market equities (verify index)", conf="name"),
    "ETFSP500": _e("Beta ETF", "us_equity", "USD", 1.0, "S&P 500 — 500 dużych spółek USA (bez zabezpieczenia walutowego)",
                   "S&P 500 — 500 large US companies (currency unhedged)"),
    "ETFBSPXPL": _e("Beta ETF", "us_equity", "PLN-hedged", 1.0, "S&P 500 z zabezpieczeniem kursu USD/PLN",
                    "S&P 500 hedged to PLN"),
    "ETFBNDXPL": _e("Beta ETF", "us_equity", "PLN-hedged", 1.0, "Nasdaq-100 (technologia USA) z zabezpieczeniem do PLN",
                    "Nasdaq-100 (US tech) hedged to PLN"),
    "ETFDAX": _e("Beta ETF", "other_equity", "EUR", 1.0, "DAX — 40 największych spółek niemieckich",
                 "DAX — 40 largest German companies"),
    "ETFEUNM": _e("iShares (BlackRock)", "other_equity", "multi", 1.0, "MSCI Emerging Markets — rynki wschodzące (sprawdź)",
                  "MSCI Emerging Markets (verify)", conf="name"),
    "ETFISIJPA": _e("iShares (BlackRock)", "other_equity", "JPY", 1.0, "akcje japońskie (sprawdź indeks)",
                    "Japanese equities (verify index)", conf="name"),
    "ETFV60A": _e("Vanguard", "mixed", "multi", 0.6, "LifeStrategy 60% — ok. 60% akcji globalnych, 40% obligacji (sprawdź)",
                  "LifeStrategy 60% — about 60% global equities, 40% bonds (verify)", conf="name"),
    # --- bonds / cash
    "ETFBTBSP": _e("Beta ETF", "pl_bonds", "PLN", 0.0, "polskie obligacje skarbowe (indeks TBSP)",
                   "Polish treasury bonds (TBSP index)", LQ),
    "ETFPZULONGDUR": _e("TFI PZU (inPZU)", "pl_bonds", "PLN", 0.0, "długoterminowe polskie obligacje (sprawdź)",
                        "long-duration Polish bonds (verify)", LQ, "name"),
    "ETFBCASH": _e("Beta ETF", "cash", "PLN", 0.0, "krótkoterminowe obligacje / rynek pieniężny w PLN",
                   "short-term PLN bonds / money market", LQ),
    "ETFPZUCASH": _e("TFI PZU (inPZU)", "cash", "PLN", 0.0, "rynek pieniężny w PLN (sprawdź)",
                     "PLN money market (verify)", LQ, "name"),
    # --- gold / commodities / crypto
    "ETFPZUGOLD": _e("TFI PZU (inPZU)", "gold_commodity", "USD", 0.0, "złoto (sprawdź, czy fizyczne czy kontrakty)",
                     "gold (verify: physical or futures)", conf="name"),
    "ETFSLVR": _e(None, "gold_commodity", "USD", 0.0, "srebro lub spółki wydobywcze srebra (sprawdź)",
                  "silver or silver miners (verify)", conf="name"),
    "ETFBTCPL": _e("Beta ETF", "crypto", "PLN-hedged", 0.0, "bitcoin (przez kontrakty terminowe, sprawdź)",
                   "bitcoin (via futures, verify)", conf="name"),
    # --- thematic
    "ETFNATO": _e(None, "thematic", "multi", 1.0, "spółki zbrojeniowe / obronne (sprawdź)", "defence companies (verify)", conf="name"),
    "ETFAIFS": _e(None, "thematic", "multi", 1.0, "sztuczna inteligencja / półprzewodniki (sprawdź)",
                  "artificial intelligence / semiconductors (verify)", conf="name"),
    "ETFHANARMY": _e("HANetf", "thematic", "multi", 1.0, "zbrojeniówka (sprawdź)", "defence (verify)", conf="name"),
    "ETFHANCPPR": _e("HANetf", "thematic", "multi", 1.0, "spółki wydobywcze miedzi (sprawdź)", "copper miners (verify)", conf="name"),
    "ETFHANDRON": _e("HANetf", "thematic", "multi", 1.0, "drony (sprawdź)", "drones (verify)", conf="name"),
    "ETFHANESGO": _e("HANetf", "thematic", "multi", 1.0, "temat do sprawdzenia w KID", "theme — check the KID", conf="name"),
    "ETFHANGR8": _e("HANetf", "thematic", "multi", 1.0, "temat do sprawdzenia w KID", "theme — check the KID", conf="name"),
    "ETFHANMMLP": _e("HANetf", "thematic", "multi", 1.0, "temat do sprawdzenia w KID", "theme — check the KID", conf="name"),
    "ETFHANUKRN": _e("HANetf", "thematic", "multi", 1.0, "Ukraina / odbudowa (sprawdź)", "Ukraine / reconstruction (verify)", conf="name"),
    "ETFHANURNM": _e("HANetf", "thematic", "multi", 1.0, "spółki wydobywcze uranu (sprawdź)", "uranium miners (verify)", conf="name"),
    "ETFHANYMAG": _e("HANetf", "thematic", "USD", 1.0, "strategia opcyjna na dużych spółkach USA (sprawdź — złożony produkt)",
                     "option-income strategy on large US stocks (verify — complex product)", conf="name"),
    # --- leveraged / inverse (derivatives; daily reset)
    "ETFBW20LV": _e("Beta ETF", "leveraged", "PLN", 1.0, "WIG20 z dźwignią x2 (dzienna)", "WIG20 2x daily leverage", lev=2.0),
    "ETFBW20ST": _e("Beta ETF", "short", "PLN", 1.0, "odwrotność WIG20 (zarabia, gdy WIG20 spada)",
                    "inverse WIG20 (gains when WIG20 falls)", lev=-1.0),
    "ETFBM40LV": _e("Beta ETF", "leveraged", "PLN", 1.0, "mWIG40 z dźwignią (dzienna, sprawdź mnożnik)",
                    "mWIG40 with daily leverage (verify multiple)", conf="name", lev=2.0),
    "ETFBM40ST": _e("Beta ETF", "short", "PLN", 1.0, "odwrotność mWIG40", "inverse mWIG40", conf="name", lev=-1.0),
    "ETFBNQ2ST": _e("Beta ETF", "short", "PLN-hedged", 1.0, "odwrotność Nasdaq-100 x2 (dzienna)", "Nasdaq-100 2x inverse (daily)", lev=-2.0),
    "ETFBNQ3LV": _e("Beta ETF", "leveraged", "PLN-hedged", 1.0, "Nasdaq-100 z dźwignią x3 (dzienna)", "Nasdaq-100 3x daily leverage", lev=3.0),
}

# Leveraged / inverse funds are recognised from the name too, so that a newly
# listed "...LV" / "...2ST" fund is flagged even before it is catalogued.
_LEV_RE = re.compile(r"([2-5])?(LV|ST)$")


def name_leverage(name: str) -> Optional[float]:
    """Leverage implied by a GPW short-name suffix (LV / 3LV / ST / 2ST), else None."""
    m = _LEV_RE.search(name.upper())
    if not m or not name.upper().startswith("ETF"):
        return None
    k = float(m.group(1)) if m.group(1) else (2.0 if m.group(2) == "LV" else 1.0)
    return k if m.group(2) == "LV" else -k


def classify(name: str) -> Dict:
    """Catalog entry for ``name`` (copy, with ``name`` and ``in_catalog``)."""
    entry = CATALOG.get(name)
    if entry is not None:
        out = dict(entry, name=name, in_catalog=True)
    else:
        out = _e(None, "uncategorised", "unknown", None, "brak w katalogu — sprawdź KID", "not in the catalog — check the KID",
                 conf="none")
        out.update(name=name, in_catalog=False)
        lev = name_leverage(name)
        if lev is not None:
            out.update(category="leveraged" if lev > 0 else "short", leverage=lev, confidence="name")
    out["is_leveraged_or_short"] = out["category"] in ("leveraged", "short")
    return out
