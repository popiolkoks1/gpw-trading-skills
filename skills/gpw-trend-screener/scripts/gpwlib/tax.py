"""Polish tax rules for retail investors: Belka tax, IKE, IKZE, OKI, PIT scale.

Every constant cites its legal source. Values that change yearly (contribution
limits, OKI rate) are kept per year; unknown years raise ``KeyError`` instead of
guessing, and the CLIs let the user pass the value explicitly.

Sources
-------
* Belka tax 19%: art. 30a ust. 1 pkt 7 (fund units) and art. 30b ust. 1
  (securities) ustawy o PIT; FIFO: art. 24 ust. 10.
* IKE / IKZE: ustawa z 20 kwietnia 2004 r. o IKE i IKZE (Dz.U. 2026 poz. 91):
  IKE limit = 3 x forecast average monthly wage (art. 13), IKZE = 1.2 x
  (1.8 x for the self-employed, art. 13a). IKZE contributions are deductible
  from income (art. 26 ust. 1 pkt 2b ustawy o PIT); IKZE withdrawal taxed at a
  flat 10% (art. 30 ust. 1 pkt 10a ustawy o PIT).
  2026 limits: obwieszczenia MRPiPS of Nov 2025. 2027 limits: obwieszczenia
  MRPiPS of 27 and 30 Oct 2026 (M.P. 2026 poz. 1062 and 1081 per press
  reports - verify on monitorpolski.gov.pl).
* OKI: ustawa z 3 lipca 2026 r. o osobistych kontach inwestycyjnych
  (Dz.U. 2026 poz. 1098), in force from 1 Jan 2027. Income from OKI assets is
  outside PIT (art. 32 -> art. 2 ust. 1 pkt 10 ustawy o PIT). Instead there is
  an annual tax on the average value of OKI assets (art. 22-23): rate 0.85% in
  2027 (art. 43), later 19% of the NBP reference rate on 31 Oct of the prior
  year, min 0.1%, rounded down to 0.01 pp (art. 25). Exemptions (art. 26):
  up to 25 000 zł of "savings" assets (PLN cash, savings bonds, PLN T-bills,
  risk-1 funds with >= 70% qualified assets) and up to 100 000 zł of
  "qualified" assets (PLN bonds, shares of companies with PLN share capital,
  funds with >= 70% qualified assets); total exemption <= 100 000 zł, savings
  first. Foreign shares and funds that do not hold >= 70% qualified assets
  (e.g. global / S&P 500 ETFs) are taxed from the first zloty. Amounts indexed
  by CPI from 2030 (art. 26 ust. 5, art. 44).
* PIT scale 2026: 12% up to 120 000 zł, 32% above, tax-free amount 30 000 zł
  (art. 27 ust. 1); linear tax 19% (art. 30c).
"""

from __future__ import annotations

import math
from typing import Dict, Optional

BELKA_RATE = 0.19
IKZE_WITHDRAWAL_RATE = 0.10

# year -> limits in PLN
IKE_LIMITS = {2026: 28_260.00, 2027: 30_099.00}
IKZE_LIMITS = {2026: 11_304.00, 2027: 12_039.60}
IKZE_LIMITS_SELF_EMPLOYED = {2026: 16_956.00, 2027: 18_059.40}
LIMITS_SOURCE = {
    2026: "obwieszczenia MRPiPS (listopad 2025)",
    2027: "obwieszczenia MRPiPS z 27 i 30.10.2026 (M.P. 2026 poz. 1062, 1081)",
}

IKE_TAX_FREE_AGE = 60          # or 55 with early-retirement rights (art. 34)
IKZE_WITHDRAWAL_AGE = 65       # art. 34a
MIN_CONTRIBUTION_YEARS = 5     # IKE: 5 calendar years (or >50% paid >= 5 y before); IKZE: 5 years

OKI_START_YEAR = 2027
OKI_RATE_2027_PCT = 0.85
OKI_RATE_MIN_PCT = 0.10
OKI_RATE_SHARE_OF_NBP = 0.19
OKI_EXEMPT_SAVINGS = 25_000.0
OKI_EXEMPT_QUALIFIED = 100_000.0
OKI_EXEMPT_TOTAL = 100_000.0
OKI_LAW = "Dz.U. 2026 poz. 1098"

PIT_SCALE_THRESHOLD = 120_000.0
PIT_SCALE_LOW = 0.12
PIT_SCALE_HIGH = 0.32
PIT_LINEAR = 0.19

MARGINAL_RATES = {"scale12": PIT_SCALE_LOW, "scale32": PIT_SCALE_HIGH, "linear": PIT_LINEAR, "none": 0.0}


def ike_limit(year: int) -> float:
    return IKE_LIMITS[year]


def ikze_limit(year: int, self_employed: bool = False) -> float:
    return (IKZE_LIMITS_SELF_EMPLOYED if self_employed else IKZE_LIMITS)[year]


def latest_limit_year() -> int:
    return max(IKE_LIMITS)


def oki_rate_pct(year: int, nbp_reference_rate_pct: Optional[float] = None) -> float:
    """OKI asset-tax rate in % for ``year`` (art. 25 and 43).

    For years after 2027 the NBP reference rate in force on 31 October of the
    previous year is needed; without it a ``KeyError`` is raised.
    """
    if year < OKI_START_YEAR:
        raise KeyError("OKI starts in {}".format(OKI_START_YEAR))
    if year == OKI_START_YEAR:
        return OKI_RATE_2027_PCT
    if nbp_reference_rate_pct is None:
        raise KeyError("OKI rate for {} needs the NBP reference rate of 31 Oct {}".format(year, year - 1))
    raw = OKI_RATE_SHARE_OF_NBP * nbp_reference_rate_pct
    rate = math.floor(raw * 100 + 1e-9) / 100.0
    return max(OKI_RATE_MIN_PCT, rate)


def oki_tax(avg_qualified: float, avg_savings: float = 0.0, avg_other: float = 0.0,
            rate_pct: float = OKI_RATE_2027_PCT) -> Dict:
    """Annual OKI asset tax from average asset values per group (art. 23, 26).

    avg_qualified: Polish shares / PLN bonds / funds with >= 70% qualified assets.
    avg_savings:   PLN cash, savings bonds, PLN T-bills, risk-1 qualified funds.
    avg_other:     everything else (foreign shares, global ETFs, FX cash...).
    """
    for v in (avg_qualified, avg_savings, avg_other):
        if v < 0:
            raise ValueError("average asset values must be >= 0")
    ex_sav = min(avg_savings, OKI_EXEMPT_SAVINGS)
    ex_qual = min(avg_qualified, OKI_EXEMPT_QUALIFIED, OKI_EXEMPT_TOTAL - ex_sav)
    base = avg_qualified + avg_savings + avg_other
    taxable = max(0.0, base - ex_sav - ex_qual)
    return {
        "base": round(base, 2),
        "exempt_savings": round(ex_sav, 2),
        "exempt_qualified": round(ex_qual, 2),
        "taxable": round(taxable, 2),
        "rate_pct": rate_pct,
        "tax": round(taxable * rate_pct / 100.0, 2),
    }


def ikze_refund(contribution: float, marginal_rate: float) -> float:
    """Tax saved by deducting an IKZE contribution at ``marginal_rate`` (0-1)."""
    return round(max(0.0, contribution) * marginal_rate, 2)


def belka(gain: float) -> float:
    """19% tax on a positive gain (no refund of losses)."""
    return round(gain * BELKA_RATE, 2) if gain > 0 else 0.0
