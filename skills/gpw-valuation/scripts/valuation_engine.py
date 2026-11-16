"""Pure valuation math for gpw-valuation (stdlib only, no I/O).

Everything here is deterministic and unit-tested with hand calculations
(tests/gpw-valuation/test_valuation_engine.py). Rates are decimals
(0.095 = 9.5%), money is PLN, discounting is end-of-year.

Models
------
* Reverse DCF (Mauboussin & Rappaport, "Expectations Investing", 2001/2021):
  the constant FCF growth over ``REVERSE_YEARS`` years that, with a Gordon
  terminal value, makes the DCF equal today's enterprise value. Bisection.
* 2-stage FCFF DCF (Damodaran, "Investment Valuation", 3rd ed., ch. 15;
  Koller, Goedhart & Wessels, "Valuation" (McKinsey), 7th ed., ch. 10):
  explicit revenue/margin/capex drivers for ``STAGE1_YEARS`` years, then a
  Gordon growth terminal value on FCFF of year N+1.
* Excess-return / justified P/BV for banks (Damodaran ch. 21; Gordon 1962):
  P/BV = (ROE - g) / (COE - g).
* Gordon dividend discount model (Gordon 1962) as a cross-check for stable
  payers.
* CAPM cost of equity, WACC with market-value weights, OLS beta with the
  Blume (1971) adjustment, Damodaran synthetic rating -> default spread.
* Comparable-company statistics: median and interquartile range.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# Constants (each one justified; see references/methodology.md)
# --------------------------------------------------------------------------

# Polish CIT standard rate: ustawa o podatku dochodowym od osób prawnych,
# art. 19 ust. 1 pkt 1 (19%). The 9% small-taxpayer rate (pkt 2) applies to
# revenue below EUR 2m, i.e. not to listed companies of meaningful size.
CIT_RATE = 0.19

# Blume (1971, J. Finance 26(1)): betas regress toward 1; the industry
# convention (Bloomberg "adjusted beta") is 2/3 raw + 1/3 * 1.0.
BLUME_RAW_WEIGHT = 2.0 / 3.0

# Plausibility bounds for the adjusted beta. Damodaran's industry betas
# (Jan 2026, global) span roughly 0.4-1.7; a Blume-adjusted beta outside
# 0.3-2.0 on GPW is almost always thin trading or a one-off event, not risk.
BETA_BOUNDS = (0.3, 2.0)
# Fewer than one year of weekly returns is not a regression worth using.
MIN_BETA_WEEKS = 52
# R^2 below this means the market explains < 5% of the stock's weekly moves:
# the beta is noisy (typical for illiquid GPW small caps, Dimson 1979).
LOW_R2 = 0.05

# Reverse DCF horizon: 10 years is the usual "competitive advantage period"
# used by Mauboussin & Rappaport and in Damodaran's spreadsheets.
REVERSE_YEARS = 10
# Bisection search range for the implied growth (per year).
REVERSE_G_BOUNDS = (-0.50, 1.00)

# Explicit forecast period of the 2-stage DCF (McKinsey: 5-10 years; we use 5
# because only the latest year of fundamentals is available for free).
STAGE1_YEARS = 5

# Default perpetual growth: the NBP inflation target (2.5%, i.e. zero real
# growth for ever - conservative, Damodaran's advice for mature firms) and it
# must never exceed the risk-free rate (Damodaran, ch. 12).
DEFAULT_TERMINAL_G = 0.025

# Default stage-1 revenue growth when there is no year-ago figure: a proxy
# for Polish nominal GDP growth (NBP 2.5% inflation target + ~2.5% potential
# real growth, NBP / European Commission estimates). User-overridable.
DEFAULT_REVENUE_GROWTH = 0.05
# History-based growth is clipped to this band before use as a 5-year rate:
# one exceptional year must not be extrapolated (McKinsey ch. 10 on fading
# growth; Chan, Karceski & Lakonishok 2003 on growth persistence).
GROWTH_CLIP = (-0.10, 0.20)

# Scenario shifts (symmetric, illustrative; overridable in the assumptions
# JSON). +/-1 pp of WACC is about the spread of reasonable ERP/beta choices;
# +/-3 pp of growth and +/-15% of margin span a typical cycle for GPW
# industrials; terminal g moves +/-0.5 pp around the inflation target.
SCENARIOS = {
    "bear": {"growth_pp": -3.0, "margin_mult": 0.85, "wacc_pp": 1.0, "tg_pp": -0.5},
    "base": {"growth_pp": 0.0, "margin_mult": 1.0, "wacc_pp": 0.0, "tg_pp": 0.0},
    "bull": {"growth_pp": 3.0, "margin_mult": 1.15, "wacc_pp": -1.0, "tg_pp": 0.5},
}
# Banks: ROE -3 / +2 pp (one credit-cycle swing for Polish banks 2015-2025;
# smaller upside because 2024-26 ROE sits near a cyclical high), COE +/-1 pp,
# g +/-0.5 pp.
BANK_SCENARIOS = {
    "bear": {"roe_pp": -3.0, "coe_pp": 1.0, "tg_pp": -0.5},
    "base": {"roe_pp": 0.0, "coe_pp": 0.0, "tg_pp": 0.0},
    "bull": {"roe_pp": 2.0, "coe_pp": -1.0, "tg_pp": 0.5},
}

# Sensitivity grid: WACC base -2..+2 pp in 1 pp steps x terminal g 0..4%.
SENS_WACC_STEPS_PP = (-2.0, -1.0, 0.0, 1.0, 2.0)
SENS_G_PCT = (0.0, 1.0, 2.0, 3.0, 4.0)

# Comps: fewer peers than this and the median is not representative.
MIN_PEERS = 5
# Multiples outside these bounds are excluded as not meaningful (negative
# earnings, near-zero denominators); common practice in comps work
# (Koller et al. ch. 18; Damodaran "Damodaran on Valuation" ch. 7).
MULTIPLE_BOUNDS = {
    "pe": (0.0, 100.0),
    "pbv": (0.0, 30.0),
    "ev_ebitda": (0.0, 50.0),
    "ev_sales": (0.0, 30.0),
}


class ValuationInputError(ValueError):
    """Inconsistent or impossible valuation inputs (e.g. g >= discount rate)."""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def quantile(xs: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolation quantile (same as Excel QUARTILE.INC / numpy default)."""
    s = sorted(x for x in xs if x is not None)
    if not s:
        return None
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def stats_summary(xs: Sequence[float]) -> Dict:
    s = sorted(x for x in xs if x is not None)
    if not s:
        return {"n": 0, "median": None, "q1": None, "q3": None, "min": None, "max": None}
    return {"n": len(s), "median": quantile(s, 0.5), "q1": quantile(s, 0.25), "q3": quantile(s, 0.75),
            "min": s[0], "max": s[-1]}


def discount(cf: float, rate: float, t: float) -> float:
    return cf / (1.0 + rate) ** t


# --------------------------------------------------------------------------
# Beta (OLS + Blume) and weekly returns
# --------------------------------------------------------------------------

def weekly_closes(dates: Sequence[str], closes: Sequence[float]) -> Dict[Tuple[int, int], float]:
    """Last close of each ISO week -> {(iso_year, iso_week): close}."""
    out: Dict[Tuple[int, int], float] = {}
    for d, c in zip(dates, closes):
        if c is None or c <= 0:
            continue
        y, w, _ = dt.date.fromisoformat(d).isocalendar()
        out[(y, w)] = c  # dates are oldest-first, so the last one of a week wins
    return out


def weekly_returns_aligned(stock: Dict[Tuple[int, int], float],
                           market: Dict[Tuple[int, int], float]) -> Tuple[List[float], List[float]]:
    """Returns over consecutive common weeks (a gap in either series skips that pair)."""
    weeks = sorted(set(stock) & set(market))
    rs, rm = [], []
    for a, b in zip(weeks, weeks[1:]):
        rs.append(stock[b] / stock[a] - 1.0)
        rm.append(market[b] / market[a] - 1.0)
    return rs, rm


def ols_beta(asset: Sequence[float], market: Sequence[float]) -> Optional[Dict]:
    """OLS of asset on market returns -> {beta, alpha, r2, se_beta, n}."""
    n = min(len(asset), len(market))
    if n < 3:
        return None
    a, m = list(asset[-n:]), list(market[-n:])
    ma, mm = sum(a) / n, sum(m) / n
    sxx = sum((x - mm) ** 2 for x in m)
    if sxx <= 0:
        return None
    sxy = sum((x - mm) * (y - ma) for x, y in zip(m, a))
    beta = sxy / sxx
    alpha = ma - beta * mm
    ss_res = sum((y - alpha - beta * x) ** 2 for x, y in zip(m, a))
    ss_tot = sum((y - ma) ** 2 for y in a)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    se = math.sqrt(ss_res / (n - 2) / sxx) if n > 2 else None
    return {"beta": beta, "alpha": alpha, "r2": r2, "se_beta": se, "n": n}


def blume(raw_beta: float) -> float:
    """Blume (1971) adjustment: 2/3 * raw + 1/3 * 1.0."""
    return BLUME_RAW_WEIGHT * raw_beta + (1.0 - BLUME_RAW_WEIGHT)


def adjusted_beta(raw_beta: float, bounds: Tuple[float, float] = BETA_BOUNDS) -> Dict:
    adj = blume(raw_beta)
    bounded = clamp(adj, *bounds)
    return {"raw": raw_beta, "blume": adj, "value": bounded, "bounded": bounded != adj}


# --------------------------------------------------------------------------
# Discount rates
# --------------------------------------------------------------------------

def capm(rf: float, beta: float, erp: float) -> float:
    return rf + beta * erp


def synthetic_spread(coverage: Optional[float], table: Sequence[Dict]) -> Dict:
    """Interest coverage -> Damodaran synthetic rating and default spread.

    ``table`` rows: {lo, hi, rating, spread} (decimal spread). ``None``
    coverage (no net interest cost) -> best rating.
    """
    rows = sorted(table, key=lambda r: r["lo"])
    if coverage is None or coverage == math.inf:
        r = rows[-1]
        return {"rating": r["rating"], "spread": r["spread"], "coverage": None}
    pick = rows[0]
    for r in rows:  # last band whose lower bound the coverage reaches
        if coverage >= r["lo"]:
            pick = r
    return {"rating": pick["rating"], "spread": pick["spread"], "coverage": coverage}


def wacc(ke: float, kd_pre_tax: float, tax: float, equity_value: float, debt_value: float) -> Dict:
    """WACC with market-value weights. Negative debt (net cash) gets weight 0."""
    if equity_value <= 0:
        raise ValuationInputError("market value of equity must be positive for WACC weights")
    d = max(0.0, debt_value or 0.0)
    v = equity_value + d
    we, wd = equity_value / v, d / v
    kd_after = kd_pre_tax * (1.0 - tax)
    return {"wacc": we * ke + wd * kd_after, "we": we, "wd": wd, "ke": ke, "kd_pre_tax": kd_pre_tax,
            "kd_after_tax": kd_after, "tax": tax}


# --------------------------------------------------------------------------
# Terminal value and DCF building blocks
# --------------------------------------------------------------------------

def check_rates(rate: float, g: float, what: str = "WACC") -> None:
    if rate <= -1:
        raise ValuationInputError("{} must be greater than -100%".format(what))
    if g >= rate:
        raise ValuationInputError(
            "terminal growth {:.2%} must be below the discount rate ({}) {:.2%}: the Gordon formula "
            "has no finite value when g >= r".format(g, what, rate))


def gordon(cf_next: float, rate: float, g: float, what: str = "WACC") -> float:
    """Value at t of a cash flow cf_next at t+1 growing at g for ever."""
    check_rates(rate, g, what)
    return cf_next / (rate - g)


def reverse_dcf_ev(fcf0: float, growth: float, rate: float, tg: float, years: int = REVERSE_YEARS) -> float:
    """EV of FCF0 growing at ``growth`` for ``years`` then at ``tg`` for ever."""
    check_rates(rate, tg)
    ev, cf = 0.0, fcf0
    for t in range(1, years + 1):
        cf *= 1.0 + growth
        ev += discount(cf, rate, t)
    tv = cf * (1.0 + tg) / (rate - tg)
    return ev + discount(tv, rate, years)


def solve_implied_growth(target_ev: float, fcf0: float, rate: float, tg: float, years: int = REVERSE_YEARS,
                         bounds: Tuple[float, float] = REVERSE_G_BOUNDS, tol: float = 1e-9,
                         max_iter: int = 200) -> Dict:
    """Bisection for the growth g with reverse_dcf_ev(g) == target_ev.

    EV is strictly increasing in g when FCF0 > 0, so bisection is exact.
    Returns {growth|None, status: ok|below_range|above_range|not_solvable, iterations, ev_at_solution}.
    """
    check_rates(rate, tg)
    if fcf0 is None or fcf0 <= 0 or target_ev is None or target_ev <= 0:
        return {"growth": None, "status": "not_solvable", "iterations": 0, "ev_at_solution": None}
    lo, hi = bounds
    f_lo = reverse_dcf_ev(fcf0, lo, rate, tg, years) - target_ev
    f_hi = reverse_dcf_ev(fcf0, hi, rate, tg, years) - target_ev
    if f_lo > 0:
        return {"growth": lo, "status": "below_range", "iterations": 0, "ev_at_solution": f_lo + target_ev}
    if f_hi < 0:
        return {"growth": hi, "status": "above_range", "iterations": 0, "ev_at_solution": f_hi + target_ev}
    it = 0
    while hi - lo > tol and it < max_iter:
        mid = (lo + hi) / 2.0
        if reverse_dcf_ev(fcf0, mid, rate, tg, years) - target_ev > 0:
            hi = mid
        else:
            lo = mid
        it += 1
    g = (lo + hi) / 2.0
    return {"growth": g, "status": "ok", "iterations": it, "ev_at_solution": reverse_dcf_ev(fcf0, g, rate, tg, years)}


def implied_fcf_no_growth(target_ev: float, rate: float) -> Optional[float]:
    """Constant perpetual FCF that justifies the EV with zero growth (EV * r)."""
    if target_ev is None or rate <= 0:
        return None
    return target_ev * rate


def driver_dcf(revenue0: float, growth: float, margin: float, da_pct: float, capex_pct: float, nwc_pct: float,
               tax: float, rate: float, tg: float, years: int = STAGE1_YEARS) -> Dict:
    """2-stage FCFF DCF.

    Year t (1..N): Rev_t = Rev_{t-1}(1+growth); EBIT = Rev*margin; NOPAT = EBIT(1-tax);
    FCFF = NOPAT + Rev*da_pct - Rev*capex_pct - nwc_pct*(Rev_t - Rev_{t-1}).
    Terminal: FCFF_{N+1} with Rev growing at tg, TV_N = FCFF_{N+1}/(rate - tg).
    """
    check_rates(rate, tg)
    if revenue0 is None or revenue0 <= 0:
        raise ValuationInputError("revenue must be positive for the driver DCF")

    def fcff(rev_prev: float, rev: float) -> Dict:
        ebit = rev * margin
        nopat = ebit * (1.0 - tax)
        da, capex, dnwc = rev * da_pct, rev * capex_pct, nwc_pct * (rev - rev_prev)
        return {"revenue": rev, "ebit": ebit, "nopat": nopat, "da": da, "capex": capex, "delta_nwc": dnwc,
                "fcff": nopat + da - capex - dnwc}

    rows, pv_sum, rev_prev = [], 0.0, revenue0
    for t in range(1, years + 1):
        rev = rev_prev * (1.0 + growth)
        r = fcff(rev_prev, rev)
        r["year"] = t
        r["discount_factor"] = 1.0 / (1.0 + rate) ** t
        r["pv"] = r["fcff"] * r["discount_factor"]
        pv_sum += r["pv"]
        rows.append(r)
        rev_prev = rev
    term = fcff(rev_prev, rev_prev * (1.0 + tg))
    tv = term["fcff"] / (rate - tg)
    pv_tv = discount(tv, rate, years)
    ev = pv_sum + pv_tv
    reinvest = 1.0 - term["fcff"] / term["nopat"] if term["nopat"] > 0 else None
    ronic = (tg / reinvest) if (reinvest is not None and reinvest > 0 and tg > 0) else None
    return {"years": rows, "pv_explicit": pv_sum, "terminal_fcff": term["fcff"], "terminal_value": tv,
            "pv_terminal": pv_tv, "ev": ev, "terminal_share": pv_tv / ev if ev > 0 else None,
            "terminal_reinvestment_rate": reinvest, "implied_ronic": ronic}


def equity_per_share(ev: float, net_debt: float, shares: float) -> Optional[float]:
    if not shares or shares <= 0:
        return None
    return (ev - (net_debt or 0.0)) / shares


# --------------------------------------------------------------------------
# Banks, dividends
# --------------------------------------------------------------------------

def justified_pbv(roe: float, coe: float, g: float) -> float:
    """Excess-return model in multiple form: P/BV = (ROE - g) / (COE - g)."""
    check_rates(coe, g, "COE")
    return (roe - g) / (coe - g)


def implied_roe(pbv: float, coe: float, g: float) -> float:
    """Sustainable ROE priced in: ROE = g + P/BV * (COE - g)."""
    check_rates(coe, g, "COE")
    return g + pbv * (coe - g)


def implied_bank_growth(pbv: float, roe: float, coe: float) -> Optional[float]:
    """g solving P/BV = (ROE - g)/(COE - g)  ->  g = (P/BV*COE - ROE)/(P/BV - 1)."""
    if pbv is None or abs(pbv - 1.0) < 1e-9:
        return None
    return (pbv * coe - roe) / (pbv - 1.0)


def ddm_value(dps0: float, coe: float, g: float) -> float:
    """Gordon DDM: D0(1+g)/(COE - g)."""
    return gordon(dps0 * (1.0 + g), coe, g, "COE")


# --------------------------------------------------------------------------
# Sensitivity, scenarios, range position
# --------------------------------------------------------------------------

def sensitivity_grid(value_fn: Callable[[float, float], Optional[float]], base_rate: float,
                     rate_steps_pp: Sequence[float] = SENS_WACC_STEPS_PP,
                     g_pct: Sequence[float] = SENS_G_PCT) -> Dict:
    """value_fn(rate, g) -> value per share. Cells with g >= rate are None."""
    rates = [base_rate + s / 100.0 for s in rate_steps_pp]
    gs = [x / 100.0 for x in g_pct]
    grid = []
    for r in rates:
        row = []
        for g in gs:
            if g >= r or r <= 0:
                row.append(None)
                continue
            try:
                row.append(value_fn(r, g))
            except ValuationInputError:
                row.append(None)
        grid.append(row)
    return {"rates_pct": [r * 100.0 for r in rates], "g_pct": list(g_pct), "values": grid}


def fair_range(values: Sequence[Optional[float]]) -> Dict:
    """low / base / high from (bear, base, bull) values, sorted so low <= high."""
    bear, base, bull = values
    known = [v for v in (bear, base, bull) if v is not None]
    if not known:
        return {"low": None, "base": None, "high": None}
    return {"low": min(known), "base": base, "high": max(known)}


def range_position(price: float, rng: Dict) -> Dict:
    """Neutral description of where the price sits vs the range."""
    lo, base, hi = rng.get("low"), rng.get("base"), rng.get("high")
    def rel(v):
        return (v / price - 1.0) * 100.0 if (v is not None and price) else None
    if lo is None or hi is None or not price:
        pos = "unknown"
    elif price < lo:
        pos = "below_range"
    elif price > hi:
        pos = "above_range"
    else:
        pos = "within_range"
    return {"position": pos, "to_low_pct": rel(lo), "to_base_pct": rel(base), "to_high_pct": rel(hi)}


# --------------------------------------------------------------------------
# Comps
# --------------------------------------------------------------------------

def valid_multiple(kind: str, x: Optional[float]) -> bool:
    lo, hi = MULTIPLE_BOUNDS[kind]
    return x is not None and lo < x <= hi


def comps_implied(kind: str, stats: Dict, metric: Optional[float], net_debt: float, shares: float) -> Optional[Dict]:
    """Implied value per share from peer Q1 / median / Q3 of one multiple.

    Equity multiples (pe, pbv): equity = multiple * metric.
    EV multiples (ev_ebitda, ev_sales): equity = multiple * metric - net debt.
    """
    if stats.get("n", 0) == 0 or metric is None or metric <= 0 or not shares:
        return None
    is_ev = kind.startswith("ev_")

    def per_share(mult):
        if mult is None:
            return None
        eq = mult * metric - ((net_debt or 0.0) if is_ev else 0.0)
        return eq / shares

    return {"low": per_share(stats["q1"]), "base": per_share(stats["median"]), "high": per_share(stats["q3"])}
