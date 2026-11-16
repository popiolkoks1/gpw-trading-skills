"""Pure engine of gpw-quality-screener: statements, F-score, metrics, scores.

No network and no file access here: every function takes plain dicts and
returns plain dicts, so the whole engine is unit-tested on synthetic
statements. Data plumbing lives in ``quality_data.py``; the CLI and the
reports in ``gpw_quality_screener.py``.

Sources of every definition (details and the data-limitations table are in
``references/methodology.md``):

* Piotroski, J. (2000), "Value Investing: The Use of Historical Financial
  Statement Information to Separate Winners from Losers", Journal of
  Accounting Research 38 (Supplement), 1-41 - the nine F-score signals.
* Sloan, R. (1996), "Do Stock Prices Fully Reflect Information in Accruals
  and Cash Flows about Future Earnings?", The Accounting Review 71(3) -
  accruals; cash-flow form (NI - CFO) / average assets as in Hribar &
  Collins (2002), Journal of Accounting Research 40(1).
* Novy-Marx, R. (2013), "The Other Side of Value: The Gross Profitability
  Premium", Journal of Financial Economics 108(1) - gross profit / assets.
* Asness, Frazzini & Pedersen (2019), "Quality Minus Junk", Review of
  Accounting Studies 24(1) - quality = profitability + safety + payout,
  built from cross-sectional ranks (the composite below follows that idea).
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# Constants (each one justified; see references/methodology.md)
# --------------------------------------------------------------------------

#: Polish statutory CIT rate (ustawa o CIT art. 19 ust. 1 pkt 1) - used to
#: turn EBIT into NOPAT for the ROIC approximation.
CIT_RATE = 0.19

#: Notoria prints values in thousands of PLN but rounds them to whole
#: millions in practice (gpwlib.fundamentals docstring). A share-capital
#: change smaller than this is rounding, not an issue of shares.
NOTORIA_ROUNDING_PLN = 1_000_000.0

#: Relative tolerance for "share count did not grow" (F-score EQ_OFFER):
#: employee option exercises of a few basis points are not a capital raise.
#: 0.5 % keeps the test close to Piotroski's "did not issue common equity".
SHARE_ISSUE_TOLERANCE = 0.005

#: Red flag "dilution": share count up more than 5 % in a year. Pontiff &
#: Woodgate (2008, Journal of Finance 63(2)) find returns fall monotonically
#: with net issuance; 5 % marks a material raise rather than option noise.
DILUTION_RED_FLAG = 0.05

#: Red flag "high accruals": (NI - CFO) / average assets above 10 %.
#: Sloan (1996) - the top accrual decile averages roughly +10-12 % of
#: assets and earns the lowest subsequent returns.
ACCRUALS_RED_FLAG = 0.10

#: Red flag "leverage": net debt / EBITDA above 3.5x - the level at which
#: bank covenants and rating agencies typically treat a corporate borrower
#: as highly levered (sub-investment-grade territory, Moody's / S&P
#: leverage grids; most Polish loan covenants sit at 3.0-3.5x).
NET_DEBT_EBITDA_RED_FLAG = 3.5

#: A Notoria period older than this (days between period end and as-of)
#: is "stale": a company that has not been updated for two reporting
#: rounds. Quarterly reports are due 60 days after quarter end (Q1-Q3),
#: H1 90 days, annual 120 days (Rozporządzenie MF z 29.03.2018 r., par. 79);
#: 270 days = the latest normal period plus one missed update.
STALE_PERIOD_DAYS = 270

#: Piotroski (2000) "high" and "low" F-score groups.
F_HIGH = 8
F_LOW = 1

#: Composite: below this share of weights with data the score is withheld.
MIN_QUALITY_COVERAGE = 50.0
#: Signals are emitted only when at least this share of weights had data.
SIGNAL_MIN_COVERAGE = 70.0
#: "quality_high" signal threshold: top quintile of the composite.
SIGNAL_QUALITY_HIGH = 80.0

#: Percentile ranks need a peer group; with fewer companies the score uses
#: the absolute anchors below instead (ranks of 2-4 companies are noise).
MIN_GROUP_FOR_RANKS = 5
#: Sector-relative percentile only for sectors with at least 3 companies.
MIN_SECTOR_FOR_RANKS = 3
#: OLS of P/BV on ROE across banks needs at least 4 banks.
MIN_BANKS_FOR_REGRESSION = 4

SECTOR_TYPES = ("industrial", "bank", "insurer")

# GPW sector names (indicators tab "Sektor", lower-case substrings).
BANK_SECTOR_KEYS = ("bank",)
INSURER_SECTOR_KEYS = ("ubezpiecz", "insur")
OTHER_FINANCIAL_KEYS = ("wierzytelno", "giełdy i biura maklerskie", "biura maklerskie", "leasing",
                        "fundusze", "rynek kapitałowy", "pozostałe finanse", "usługi finansowe")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _f(x) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def safe_div(a, b) -> Optional[float]:
    a, b = _f(a), _f(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


def _avg(a, b) -> Optional[float]:
    a, b = _f(a), _f(b)
    if a is None:
        return b
    if b is None:
        return a
    return (a + b) / 2.0


def piecewise(x: float, points: Sequence[Tuple[float, float]]) -> float:
    """Piecewise-linear score; ``points`` sorted by x ascending."""
    if x <= points[0][0]:
        return float(points[0][1])
    for (x0, s0), (x1, s1) in zip(points, points[1:]):
        if x <= x1:
            return float(s0 + (s1 - s0) * (x - x0) / (x1 - x0)) if x1 != x0 else float(s1)
    return float(points[-1][1])


def percentile_rank(values: Sequence[Optional[float]], value: Optional[float],
                    higher_is_better: bool = True) -> Optional[float]:
    """Mid-rank percentile 0-100 of ``value`` among ``values`` (ties count half)."""
    vals = [v for v in values if v is not None]
    if not vals or value is None:
        return None
    below = sum(1 for v in vals if v < value)
    equal = sum(1 for v in vals if v == value)
    p = 100.0 * (below + 0.5 * equal) / len(vals)
    return p if higher_is_better else 100.0 - p


# --------------------------------------------------------------------------
# Sector type
# --------------------------------------------------------------------------

def classify_sector(sector: Optional[str], ticker: str = "", bank_members: Iterable[str] = (),
                    statement: Optional[Dict] = None) -> Dict:
    """``{sector_type, financial_other, reason}``.

    Banks: GPW sector contains "bank" or the ticker is in WIG-BANKI.
    Insurers: sector contains "ubezpiecz". Other financials (debt collectors,
    brokers, exchanges, leasing) stay "industrial" for the contract but are
    marked ``financial_other`` because Piotroski excluded nothing explicitly
    yet their balance sheets (receivables portfolios, client cash) make
    liquidity and leverage signals less meaningful.
    """
    s = (sector or "").strip().lower()
    t = (ticker or "").strip().upper()
    if any(k in s for k in INSURER_SECTOR_KEYS):
        return {"sector_type": "insurer", "financial_other": False, "reason": "sector:" + s}
    if any(k in s for k in BANK_SECTOR_KEYS):
        return {"sector_type": "bank", "financial_other": False, "reason": "sector:" + s}
    if t and t in {m.upper() for m in bank_members}:
        return {"sector_type": "bank", "financial_other": False, "reason": "WIG-BANKI member"}
    other = any(k in s for k in OTHER_FINANCIAL_KEYS)
    return {"sector_type": "industrial", "financial_other": other,
            "reason": ("sector:" + s) if s else "default"}


# --------------------------------------------------------------------------
# Normalised statements
# --------------------------------------------------------------------------

STATEMENT_FIELDS = (
    "revenue", "gross_profit", "profit_on_sales", "operating_profit", "pretax_profit", "net_income",
    "depreciation_amortization", "ebitda", "cfo", "capex", "total_assets", "current_assets",
    "current_liabilities", "noncurrent_liabilities", "total_liabilities", "equity", "share_capital",
    "shares", "cash", "borrowings", "lease_liabilities", "finance_costs",
)
FLOW_FIELDS = {"revenue", "gross_profit", "profit_on_sales", "operating_profit", "pretax_profit", "net_income",
               "depreciation_amortization", "ebitda", "cfo", "capex", "finance_costs"}


def statement_from_notoria(noto: Dict, source: str = "notoria") -> Optional[Dict]:
    """Notoria tab (``fundamentals.parse_notoria_html`` output or a history
    period entry with ``values``) -> normalised statement or None.

    Net income: "zysk netto" (total) if printed, else the parent's share
    (Notoria on gpw.pl prints only the parent line for most groups);
    ``net_income_basis`` records which. Same for equity.
    """
    if not noto:
        return None
    v = noto.get("values") or {}
    if not v:
        return None
    p = noto.get("period") or {}
    ni_key = "net_profit" if v.get("net_profit") is not None else "net_profit_parent"
    eq_key = "equity" if v.get("equity") is not None else "equity_parent"
    ebit, da = _f(v.get("operating_profit")), _f(v.get("depreciation_amortization"))
    ebitda = _f(v.get("ebitda"))
    if ebitda is None and ebit is not None and da is not None:
        ebitda = ebit + da
    ta, eq = _f(v.get("total_assets")), _f(v.get(eq_key))
    cl, ncl = _f(v.get("current_liabilities")), _f(v.get("noncurrent_liabilities"))
    if cl is not None and ncl is not None:
        tl, tl_basis = cl + ncl, "current + non-current liabilities"
    elif ta is not None and eq is not None:
        tl, tl_basis = ta - eq, "total assets - parent equity (includes minority interests)"
    else:
        tl, tl_basis = None, None
    st = {
        "source": source,
        "label": noto.get("period_label") or noto.get("label"),
        "months": p.get("months"),
        "year": p.get("year"),
        "first_quarter": p.get("first_quarter"),
        "last_quarter": p.get("last_quarter"),
        "period_end": p.get("period_end"),
        "consolidated": noto.get("consolidated"),
        "revenue": _f(v.get("revenue")),
        "gross_profit": None,  # not printed by Notoria
        "profit_on_sales": _f(v.get("profit_on_sales")),
        "operating_profit": ebit,
        "pretax_profit": _f(v.get("pretax_profit")),
        "net_income": _f(v.get(ni_key)),
        "net_income_basis": "total" if ni_key == "net_profit" else "parent",
        "depreciation_amortization": da,
        "ebitda": ebitda,
        "cfo": _f(v.get("cfo")),
        "capex": _f(v.get("capex")),  # negative = outflow (Notoria sign)
        "total_assets": ta,
        "current_assets": _f(v.get("current_assets")),
        "current_liabilities": cl,
        "noncurrent_liabilities": ncl,
        "total_liabilities": tl,
        "total_liabilities_basis": tl_basis,
        "equity": eq,
        "equity_basis": "total" if eq_key == "equity" else "parent",
        "share_capital": _f(v.get("share_capital")),
        "shares": None,
        "cash": None, "borrowings": None, "lease_liabilities": None, "finance_costs": None,
        "rounding_pln": NOTORIA_ROUNDING_PLN,
    }
    return st


def _ifrs_months(row: Dict, keys: Sequence[str]) -> Optional[int]:
    m = row.get("_months") or {}
    for k in keys:
        if m.get(k):
            return int(m[k])
    return None


def statement_from_ifrs(facts: Dict[str, Dict], period_end: str, source: str = "esef") -> Optional[Dict]:
    """``fundamentals.extract_ifrs_facts`` / ``parse_esef_zip()['facts']`` row
    for ``period_end`` -> normalised statement (full year when the flows are
    12 months). Capex is turned negative (IFRS "Purchase of PP&E" is a
    positive outflow) so it matches the Notoria sign."""
    row = facts.get(period_end)
    if not row:
        return None
    g = lambda k: _f(row.get(k))  # noqa: E731
    months = _ifrs_months(row, ["Revenue", "ProfitLoss", "CashFlowsFromUsedInOperatingActivities",
                                "ProfitLossFromOperatingActivities"])
    ebit, da = g("ProfitLossFromOperatingActivities"), g("DepreciationAndAmortisation")
    ni = g("ProfitLoss")
    ni_basis = "total"
    if ni is None:
        ni, ni_basis = g("ProfitLossAttributableToOwnersOfParent"), "parent"
    eq = g("Equity")
    eq_basis = "total"
    if eq is None:
        eq, eq_basis = g("EquityAttributableToOwnersOfParent"), "parent"
    ta, cl, ncl = g("Assets"), g("CurrentLiabilities"), g("NoncurrentLiabilities")
    tl = g("Liabilities")
    if tl is None and cl is not None and ncl is not None:
        tl = cl + ncl
    borrowings = g("Borrowings")
    if borrowings is None and (g("CurrentBorrowings") is not None or g("NoncurrentBorrowings") is not None):
        borrowings = (g("CurrentBorrowings") or 0.0) + (g("NoncurrentBorrowings") or 0.0)
    capex = g("Capex")
    if capex is not None:
        capex = -abs(capex)
    shares = g("SharesOutstanding") or g("WeightedAverageShares")
    year = int(period_end[:4]) if period_end else None
    return {
        "source": source, "label": "FY{} ({})".format(year, period_end) if months == 12 else period_end,
        "months": months, "year": year, "first_quarter": 1 if months == 12 else None,
        "last_quarter": 4 if months == 12 else None, "period_end": period_end, "consolidated": None,
        "revenue": g("Revenue"), "gross_profit": g("GrossProfit"), "profit_on_sales": None,
        "operating_profit": ebit, "pretax_profit": g("ProfitLossBeforeTax"), "net_income": ni,
        "net_income_basis": ni_basis, "depreciation_amortization": da,
        "ebitda": (ebit + da) if (ebit is not None and da is not None) else None,
        "cfo": g("CashFlowsFromUsedInOperatingActivities"), "capex": capex,
        "total_assets": ta, "current_assets": g("CurrentAssets"), "current_liabilities": cl,
        "noncurrent_liabilities": ncl, "total_liabilities": tl,
        "total_liabilities_basis": "IFRS Liabilities" if g("Liabilities") is not None else "current + non-current",
        "equity": eq, "equity_basis": eq_basis, "share_capital": g("IssuedCapital"), "shares": shares,
        "cash": g("CashAndCashEquivalents"), "borrowings": borrowings, "lease_liabilities": g("LeaseLiabilities"),
        "finance_costs": g("FinanceCosts"), "currency": row.get("_currency"), "rounding_pln": 0.0,
    }


def ifrs_fy_pair(facts: Dict[str, Dict], source: str) -> Tuple[Optional[Dict], Optional[Dict]]:
    """Latest full-year statement and the one a year earlier (an annual ESEF
    report tags the prior-year comparatives too)."""
    years = []
    for pe in sorted(facts, reverse=True):
        st = statement_from_ifrs(facts, pe, source)
        if st and st.get("months") == 12 and st.get("total_assets") is not None:
            years.append(st)
    if not years:
        return None, None
    cur = years[0]
    prior = next((s for s in years[1:] if s["year"] == cur["year"] - 1), None)
    return cur, prior


def same_period(a: Optional[Dict], b: Optional[Dict]) -> bool:
    """``b`` is the same YTD window one year before ``a``."""
    if not a or not b:
        return False
    return (a.get("months") == b.get("months") and a.get("last_quarter") == b.get("last_quarter")
            and a.get("year") is not None and b.get("year") == a["year"] - 1)


def annualisation_factor(st: Optional[Dict]) -> Optional[float]:
    m = (st or {}).get("months")
    return 12.0 / m if m else None


def annualised(st: Dict, field: str, ttm: Optional[Dict] = None) -> Optional[float]:
    """Trailing-12-month value when ``ttm`` holds it, else YTD x 12/months.

    YTD x 12/m assumes no seasonality; it is the standard fallback and is
    stated in every report (``annualisation`` field)."""
    if ttm and ttm.get(field) is not None:
        return ttm[field]
    v, k = _f(st.get(field)), annualisation_factor(st)
    return None if v is None or k is None else v * k


def ttm_from(cur: Dict, prev_fy: Optional[Dict], prev_ytd: Optional[Dict]) -> Optional[Dict]:
    """TTM = YTD(t) + FY(t-1) - YTD(t-1) for every flow field all three have."""
    if not cur or cur.get("months") == 12 or not prev_fy or not prev_ytd:
        return None
    if prev_fy.get("months") != 12 or not same_period(cur, prev_ytd) or prev_fy.get("year") != cur.get("year", 0) - 1:
        return None
    out = {}
    for f in FLOW_FIELDS:
        a, b, c = _f(cur.get(f)), _f(prev_fy.get(f)), _f(prev_ytd.get(f))
        if a is not None and b is not None and c is not None:
            out[f] = a + b - c
    return out or None


# --------------------------------------------------------------------------
# Piotroski F-score
# --------------------------------------------------------------------------

F_CRITERIA = (
    ("roa_positive", "profitability"), ("cfo_positive", "profitability"), ("roa_improving", "profitability"),
    ("accruals", "profitability"), ("leverage_down", "leverage_liquidity"),
    ("liquidity_up", "leverage_liquidity"), ("no_equity_issue", "leverage_liquidity"),
    ("margin_up", "efficiency"), ("turnover_up", "efficiency"),
)


def _crit(cid, status, cur=None, prior=None, proxy=None, note=None, inputs=None):
    group = dict(F_CRITERIA)[cid]
    return {"id": cid, "group": group, "status": status, "current": cur, "prior": prior,
            "proxy": proxy, "note": note, "inputs": inputs or {}}


def _cmp(cid, a, b, higher_better=True, proxy=None, inputs=None, note=None):
    if a is None or b is None:
        return _crit(cid, "unknown", a, b, proxy, note or "missing_input", inputs)
    ok = a > b if higher_better else a < b
    return _crit(cid, "pass" if ok else "fail", a, b, proxy, note, inputs)


def _shares_pair(cur: Dict, prior: Dict, shares_now: Optional[float], shares_then: Optional[float]):
    """(current, prior, basis, tolerance_abs)."""
    if shares_now and shares_then:
        return shares_now, shares_then, "shares_issued", shares_then * SHARE_ISSUE_TOLERANCE
    a, b = _f(cur.get("shares")), _f(prior.get("shares"))
    if a and b:
        return a, b, "shares_outstanding", b * SHARE_ISSUE_TOLERANCE
    a, b = _f(cur.get("share_capital")), _f(prior.get("share_capital"))
    if a is not None and b is not None:
        tol = max(b * SHARE_ISSUE_TOLERANCE, max(cur.get("rounding_pln") or 0, prior.get("rounding_pln") or 0))
        return a, b, "share_capital", tol
    return None, None, None, None


def piotroski(cur: Optional[Dict], prior: Optional[Dict] = None, shares_now: Optional[float] = None,
              shares_then: Optional[float] = None) -> Dict:
    """The nine binary signals of Piotroski (2000), each pass / fail / unknown.

    ``prior`` must be the same YTD window one year earlier (or the previous
    full year); otherwise every change-based signal is unknown. Ratios use
    period-end total assets for both years (Piotroski scales by beginning
    or average assets; the store rarely holds the asset base two years
    back, so one consistent denominator is used - the sign of the change is
    what the signal reads). YTD flows are compared with YTD flows of the
    same months, so annualisation cancels out.
    """
    crits: List[Dict] = []
    if not cur:
        crits = [_crit(cid, "unknown", note="no_statement") for cid, _ in F_CRITERIA]
        return _summarise(crits, prior_source=None)
    comparable = same_period(cur, prior) or (
        prior is not None and cur.get("months") == 12 and prior.get("months") == 12
        and prior.get("year") == (cur.get("year") or 0) - 1)
    pr = prior if comparable else None
    cons_note = None
    if pr is not None and cur.get("consolidated") is not None and pr.get("consolidated") is not None \
            and cur["consolidated"] != pr["consolidated"]:
        pr, cons_note = None, "consolidation_changed"
    miss = cons_note or ("no_comparable_prior_period" if prior is None or not comparable else None)

    ni, cfo, ta = _f(cur.get("net_income")), _f(cur.get("cfo")), _f(cur.get("total_assets"))
    # 1 ROA > 0  (assets > 0, so the sign of NI decides)
    crits.append(_crit("roa_positive", "unknown" if ni is None else ("pass" if ni > 0 else "fail"),
                       safe_div(ni, ta), None, inputs={"net_income": ni, "total_assets": ta}))
    # 2 CFO > 0
    crits.append(_crit("cfo_positive", "unknown" if cfo is None else ("pass" if cfo > 0 else "fail"),
                       safe_div(cfo, ta), None, inputs={"cfo": cfo, "total_assets": ta}))
    # 4 ACCRUAL: CFO/A > ROA  <=>  CFO > NI (same denominator)
    if ni is None or cfo is None:
        acc = _crit("accruals", "unknown", note="missing_input", inputs={"net_income": ni, "cfo": cfo})
    else:
        acc = _crit("accruals", "pass" if cfo > ni else "fail", safe_div(cfo, ta), safe_div(ni, ta),
                    inputs={"cfo": cfo, "net_income": ni})
    # 3 dROA > 0
    if pr is None:
        droa = _crit("roa_improving", "unknown", note=miss)
    else:
        droa = _cmp("roa_improving", safe_div(ni, ta), safe_div(pr.get("net_income"), pr.get("total_assets")),
                    inputs={"ni": [ni, pr.get("net_income")], "assets": [ta, pr.get("total_assets")]})
    crits.insert(2, droa)
    crits.append(acc)
    # 5 dLEVER < 0: long-term debt / assets fell
    if pr is None:
        crits.append(_crit("leverage_down", "unknown", note=miss))
    else:
        if cur.get("borrowings") is not None and pr.get("borrowings") is not None:
            # IFRS: non-current borrowings unknown separately -> total borrowings
            a, b, proxy = safe_div(cur["borrowings"], ta), safe_div(pr["borrowings"], pr.get("total_assets")), \
                "borrowings / total assets"
        else:
            a = safe_div(cur.get("noncurrent_liabilities"), ta)
            b = safe_div(pr.get("noncurrent_liabilities"), pr.get("total_assets"))
            proxy = "non-current liabilities / total assets (no debt line in the data)"
        if a is not None and b is not None and abs(a) < 1e-12 and abs(b) < 1e-12:
            crits.append(_crit("leverage_down", "pass", a, b, proxy, "no_long_term_debt_either_year"))
        else:
            crits.append(_cmp("leverage_down", a, b, higher_better=False, proxy=proxy))
    # 6 dLIQUID > 0: current ratio rose
    if pr is None:
        crits.append(_crit("liquidity_up", "unknown", note=miss))
    else:
        crits.append(_cmp("liquidity_up", safe_div(cur.get("current_assets"), cur.get("current_liabilities")),
                          safe_div(pr.get("current_assets"), pr.get("current_liabilities"))))
    # 7 EQ_OFFER: no common equity issued
    if pr is None and not (shares_now and shares_then):
        crits.append(_crit("no_equity_issue", "unknown", note=miss))
    else:
        a, b, basis, tol = _shares_pair(cur, pr or {}, shares_now, shares_then)
        if a is None:
            crits.append(_crit("no_equity_issue", "unknown", note="missing_input"))
        else:
            ok = a <= b + tol
            crits.append(_crit("no_equity_issue", "pass" if ok else "fail", a, b,
                               None if basis == "shares_issued" else basis, None,
                               {"basis": basis, "tolerance": tol}))
    # 8 dMARGIN > 0: gross margin rose (proxy: margin on sales)
    if pr is None:
        crits.append(_crit("margin_up", "unknown", note=miss))
    else:
        if cur.get("gross_profit") is not None and pr.get("gross_profit") is not None:
            field, proxy = "gross_profit", None
        else:
            field, proxy = "profit_on_sales", "profit on sales / revenue (gross profit not available)"
        crits.append(_cmp("margin_up", safe_div(cur.get(field), cur.get("revenue")),
                          safe_div(pr.get(field), pr.get("revenue")), proxy=proxy))
    # 9 dTURN > 0: asset turnover rose
    if pr is None:
        crits.append(_crit("turnover_up", "unknown", note=miss))
    else:
        crits.append(_cmp("turnover_up", safe_div(cur.get("revenue"), ta),
                          safe_div(pr.get("revenue"), pr.get("total_assets"))))
    order = [cid for cid, _ in F_CRITERIA]
    crits.sort(key=lambda c: order.index(c["id"]))
    return _summarise(crits, prior_source=(pr or {}).get("source"), prior_label=(pr or {}).get("label"),
                      current_label=cur.get("label"))


def _summarise(crits: List[Dict], prior_source=None, prior_label=None, current_label=None) -> Dict:
    passed = sum(1 for c in crits if c["status"] == "pass")
    failed = sum(1 for c in crits if c["status"] == "fail")
    unknown = sum(1 for c in crits if c["status"] == "unknown")
    available = passed + failed
    lo, hi = passed, passed + unknown
    if available == 9:
        band = "high" if passed >= F_HIGH else ("low" if passed <= F_LOW else "middle")
    elif lo >= F_HIGH:
        band = "high"
    elif hi <= F_LOW:
        band = "low"
    else:
        band = "undetermined"
    return {
        "applicable": True,
        "score": passed if available == 9 else None,
        "passed": passed, "failed": failed, "unknown": unknown, "available": available,
        "ratio": round(passed / available, 4) if available else None,
        "coverage_pct": round(100.0 * available / 9, 1),
        "lower": lo, "upper": hi, "band": band,
        "display": "{}/{}".format(passed, available) if available else "—",
        "current_label": current_label, "prior_label": prior_label, "prior_source": prior_source,
        "criteria": crits,
    }


def not_applicable_f(reason: str) -> Dict:
    return {"applicable": False, "score": None, "passed": None, "failed": None, "unknown": None,
            "available": 0, "ratio": None, "coverage_pct": None, "lower": None, "upper": None,
            "band": "not_applicable", "display": "n/a", "reason": reason, "criteria": []}


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def _round(x, n=4):
    return None if x is None else round(x, n)


def compute_metrics(cur: Optional[Dict], prior: Optional[Dict], market: Dict, sector_type: str,
                    ttm: Optional[Dict] = None, net_debt_override: Optional[Dict] = None) -> Dict:
    """Ratios for one company. ``market``: ``{market_cap, pe_gpw, pbv_gpw,
    dividend_yield_pct, book_value, shares_now, shares_then}``.

    Ratios are fractions (0.12 = 12 %). Flows are annualised (TTM when
    available, else YTD x 12/months). Averages of assets / equity use the
    same period one year earlier when available, else period-end values.
    ``net_debt_override``: ``{net_debt, ebitda, period_end, source}`` from an
    ESEF / xBRL annual report (Notoria prints no cash or debt).
    """
    m: Dict = {"annualisation": None, "period": None, "months": None}
    mc = _f(market.get("market_cap"))
    m.update({"market_cap": mc, "pe_gpw": _f(market.get("pe_gpw")), "pbv_gpw": _f(market.get("pbv_gpw")),
              "dividend_yield_pct": _f(market.get("dividend_yield_pct"))})
    if not cur:
        return m
    comparable = same_period(cur, prior)
    pr = prior if comparable else None
    m["period"], m["months"] = cur.get("label"), cur.get("months")
    m["annualisation"] = "ttm" if ttm else ("fy" if cur.get("months") == 12 else
                                            "ytd_x_12/{}".format(cur.get("months")) if cur.get("months") else None)
    ann = lambda f: annualised(cur, f, ttm)  # noqa: E731
    ni, rev, ebit, ebitda = ann("net_income"), ann("revenue"), ann("operating_profit"), ann("ebitda")
    cfo, capex, pos, pretax = ann("cfo"), ann("capex"), ann("profit_on_sales"), ann("pretax_profit")
    gp = ann("gross_profit")
    ta, eq = _f(cur.get("total_assets")), _f(cur.get("equity"))
    avg_ta = _avg(ta, (pr or {}).get("total_assets"))
    avg_eq = _avg(eq, (pr or {}).get("equity"))
    m.update({
        "revenue_ann": rev, "net_income_ann": ni, "ebit_ann": ebit, "ebitda_ann": ebitda, "cfo_ann": cfo,
        "net_income_basis": cur.get("net_income_basis"), "equity_basis": cur.get("equity_basis"),
        "total_assets": ta, "equity": eq, "averages_use_prior_year": pr is not None,
        "roe": safe_div(ni, avg_eq) if (avg_eq or 0) > 0 else None,
        "roa": safe_div(ni, avg_ta),
        "equity_to_assets": safe_div(eq, ta),
    })
    if sector_type == "industrial":
        cl = _f(cur.get("current_liabilities"))
        cap_employed = (ta - cl) if (ta is not None and cl is not None) else None
        nopat = ebit * (1 - CIT_RATE) if ebit is not None else None
        fcf = (cfo + capex) if (cfo is not None and capex is not None) else None
        tl = _f(cur.get("total_liabilities"))
        # net finance cost proxy: EBIT - pretax (finance costs - finance income - share of associates)
        nfc = (ebit - pretax) if (ebit is not None and pretax is not None) else None
        cov = None
        if nfc is not None and ebit is not None:
            if nfc > 0:
                cov = ebit / nfc
        m.update({
            "roic_approx": safe_div(nopat, cap_employed) if (cap_employed or 0) > 0 else None,
            "capital_employed": cap_employed,
            "operating_margin": safe_div(ebit, rev) if (rev or 0) > 0 else None,
            "net_margin": safe_div(ni, rev) if (rev or 0) > 0 else None,
            "ebitda_margin": safe_div(ebitda, rev) if (rev or 0) > 0 else None,
            "gross_profitability": safe_div(gp if gp is not None else pos, avg_ta),
            "gross_profitability_basis": "gross_profit" if gp is not None else "profit_on_sales (proxy)",
            "fcf_ann": fcf,
            "fcf_ytd": (_f(cur.get("cfo")) + _f(cur.get("capex"))) if (cur.get("cfo") is not None and cur.get("capex") is not None) else None,
            "fcf_margin": safe_div(fcf, rev) if (rev or 0) > 0 else None,
            "cash_conversion": safe_div(cfo, ni) if (ni or 0) > 0 else None,
            "accruals_ratio": safe_div(ni - cfo, avg_ta) if (ni is not None and cfo is not None) else None,
            "liabilities_to_assets": safe_div(tl, ta),
            "liabilities_basis": cur.get("total_liabilities_basis"),
            "current_ratio": safe_div(cur.get("current_assets"), cur.get("current_liabilities")),
            "net_finance_cost_ann": nfc,
            "interest_coverage_proxy": cov,
            "interest_coverage_note": ("net_financial_income" if (nfc is not None and nfc <= 0) else None),
        })
        nd = None
        if cur.get("cash") is not None and cur.get("borrowings") is not None:
            nd = {"net_debt": cur["borrowings"] + (cur.get("lease_liabilities") or 0.0) - cur["cash"],
                  "ebitda": ebitda, "period_end": cur.get("period_end"), "source": cur.get("source")}
        elif net_debt_override:
            nd = net_debt_override
        if nd and nd.get("net_debt") is not None:
            m["net_debt"] = nd["net_debt"]
            m["net_debt_period_end"] = nd.get("period_end")
            m["net_debt_source"] = nd.get("source")
            m["net_debt_ebitda"] = safe_div(nd["net_debt"], nd.get("ebitda")) if (nd.get("ebitda") or 0) > 0 else None
            ev = (mc + nd["net_debt"]) if mc is not None else None
            m["ev"] = ev
            m["ev_ebitda"] = safe_div(ev, ebitda) if (ebitda or 0) > 0 else None
            m["ev_sales"] = safe_div(ev, rev) if (rev or 0) > 0 else None
        else:
            m.update({"net_debt": None, "net_debt_ebitda": None, "ev": None, "ev_ebitda": None, "ev_sales": None,
                      "net_debt_note": "not_in_notoria"})
        m["fcf_yield"] = safe_div(fcf, mc) if mc else None
    else:
        m["cost_income"] = None
        m["cost_income_note"] = "not_derivable_from_notoria"
    m["pe_calc"] = safe_div(mc, ni) if (mc and (ni or 0) > 0) else None
    m["pbv_calc"] = safe_div(mc, eq) if (mc and (eq or 0) > 0) else None
    m["earnings_yield"] = safe_div(ni, mc) if mc else None
    # share count change (dilution)
    sn, st_ = _f(market.get("shares_now")), _f(market.get("shares_then"))
    if sn and st_:
        m["share_change_pct"], m["share_change_basis"] = (sn / st_ - 1) * 100, "shares_issued"
    elif pr and _f(cur.get("share_capital")) is not None and _f(pr.get("share_capital")):
        a, b = cur["share_capital"], pr["share_capital"]
        tol = max(cur.get("rounding_pln") or 0, pr.get("rounding_pln") or 0)
        m["share_change_pct"] = 0.0 if abs(a - b) <= tol else (a / b - 1) * 100
        m["share_change_basis"] = "share_capital"
    else:
        m["share_change_pct"], m["share_change_basis"] = None, None
    for k, v in list(m.items()):
        if isinstance(v, float):
            m[k] = round(v, 6)
    return m


# --------------------------------------------------------------------------
# Red flags
# --------------------------------------------------------------------------

def red_flags(cur: Optional[Dict], metrics: Dict, sector_type: str) -> List[Dict]:
    """``[{code, severity, value, threshold}]`` - only flags whose inputs are known."""
    out: List[Dict] = []
    if not cur:
        return out
    eq = _f(cur.get("equity"))
    if eq is not None and eq <= 0:
        out.append({"code": "negative_equity", "severity": "high", "value": eq, "threshold": 0})
    ni = _f(cur.get("net_income"))
    if ni is not None and ni < 0:
        out.append({"code": "loss_making", "severity": "medium", "value": ni, "threshold": 0})
    if sector_type == "industrial":
        cfo = _f(cur.get("cfo"))
        if cfo is not None and ni is not None and cfo < 0 < ni:
            out.append({"code": "cfo_negative_with_profit", "severity": "high", "value": cfo, "threshold": 0})
        acc = metrics.get("accruals_ratio")
        if acc is not None and acc > ACCRUALS_RED_FLAG:
            out.append({"code": "high_accruals", "severity": "medium", "value": acc, "threshold": ACCRUALS_RED_FLAG})
        nde = metrics.get("net_debt_ebitda")
        if nde is not None and nde > NET_DEBT_EBITDA_RED_FLAG:
            out.append({"code": "net_debt_ebitda_high", "severity": "high", "value": nde,
                        "threshold": NET_DEBT_EBITDA_RED_FLAG})
        if metrics.get("net_debt") is not None and (metrics.get("ebitda_ann") or 0) <= 0 and metrics["net_debt"] > 0:
            out.append({"code": "net_debt_with_negative_ebitda", "severity": "high", "value": metrics["net_debt"],
                        "threshold": 0})
    sc = metrics.get("share_change_pct")
    if sc is not None and sc > DILUTION_RED_FLAG * 100:
        out.append({"code": "dilution", "severity": "medium", "value": sc, "threshold": DILUTION_RED_FLAG * 100})
    return out


#: Data-sanity thresholds. They never change a number; they mark a company
#: whose source data look inconsistent so that the reader checks the report
#: and no signal is emitted.
#: |equity| below 0.5 % of assets: a near-zero book value is far more often a
#: data issue (or a one-off restructuring) than an economic state.
EQUITY_NEAR_ZERO = 0.005
#: Net income above 1.5x pretax profit: only a large tax credit,
#: discontinued operations or a data error produce that.
NI_ABOVE_PRETAX = 1.5
#: Balance check. Notoria equity is the parent's share, so assets -
#: (parent equity + liabilities) = minority interests, which are >= 0 and
#: can be large in listed holdings (Asseco Poland ~20 % of assets). A gap
#: below -5 % (liabilities + equity exceed assets) or above +50 % cannot be
#: explained by minorities, so the lines probably do not add up.
BALANCE_GAP_MIN = -0.05
BALANCE_GAP_MAX = 0.50


def data_checks(cur: Optional[Dict], sector_type: str) -> List[Dict]:
    """``[{code, value}]`` - inconsistencies in the source data (not red flags)."""
    out: List[Dict] = []
    if not cur:
        return out
    ta, eq = _f(cur.get("total_assets")), _f(cur.get("equity"))
    ni, pt = _f(cur.get("net_income")), _f(cur.get("pretax_profit"))
    if ta and eq is not None and abs(eq) < EQUITY_NEAR_ZERO * ta:
        out.append({"code": "equity_near_zero", "value": round(eq / ta, 6)})
    if ni is not None and pt is not None and ((pt > 0 and ni > NI_ABOVE_PRETAX * pt) or (pt < 0 < ni)):
        out.append({"code": "net_income_above_pretax", "value": round(ni / pt, 3) if pt else None})
    cl, ncl = _f(cur.get("current_liabilities")), _f(cur.get("noncurrent_liabilities"))
    if sector_type == "industrial" and ta and eq is not None and cl is not None and ncl is not None:
        gap = (ta - eq - cl - ncl) / ta
        if gap < BALANCE_GAP_MIN or gap > BALANCE_GAP_MAX:
            out.append({"code": "balance_gap", "value": round(gap, 4)})
    return out


# --------------------------------------------------------------------------
# Composite quality score
# --------------------------------------------------------------------------

# (metric, weight, higher_is_better, absolute anchors used when the peer group is small)
INDUSTRIAL_COMPONENTS = (
    # profitability (QMJ "profitability"; Novy-Marx gross profitability)
    ("roic_approx", 0.20, True, ((0.0, 0), (0.08, 50), (0.20, 100))),
    ("roa", 0.10, True, ((0.0, 0), (0.05, 50), (0.12, 100))),
    ("gross_profitability", 0.10, True, ((0.0, 0), (0.10, 50), (0.30, 100))),
    # earnings quality (Sloan accruals, FCF)
    ("accruals_ratio", 0.15, False, ((-0.10, 100), (0.0, 60), (0.10, 0))),
    ("fcf_margin", 0.10, True, ((-0.05, 0), (0.05, 50), (0.15, 100))),
    # safety (QMJ "safety": leverage, coverage)
    ("liabilities_to_assets", 0.10, False, ((0.30, 100), (0.60, 50), (0.90, 0))),
    ("interest_coverage_score_input", 0.10, True, ((1.0, 0), (3.0, 50), (10.0, 100))),
    # Piotroski F (absolute: passed / available x 100; weight x available/9)
    ("f_ratio", 0.15, True, None),
)
BANK_COMPONENTS = (
    ("roe", 0.45, True, ((0.0, 0), (0.10, 50), (0.20, 100))),
    ("roa", 0.20, True, ((0.0, 0), (0.01, 50), (0.02, 100))),
    ("equity_to_assets", 0.35, True, ((0.05, 0), (0.09, 50), (0.13, 100))),
)
INSURER_COMPONENTS = (
    ("roe", 0.60, True, ((0.0, 0), (0.10, 50), (0.20, 100))),
    ("equity_to_assets", 0.40, True, ((0.05, 0), (0.15, 50), (0.25, 100))),
)
COMPONENTS = {"industrial": INDUSTRIAL_COMPONENTS, "bank": BANK_COMPONENTS, "insurer": INSURER_COMPONENTS}
#: Cap for "coverage" when net finance cost <= 0 (net financial income):
#: such a company is at least as safe as one covering costs 50x.
COVERAGE_CAP = 50.0


def score_inputs(metrics: Dict, f: Dict) -> Dict:
    """Values that enter the composite (adds the coverage cap and F ratio)."""
    x = dict(metrics)
    cov = metrics.get("interest_coverage_proxy")
    ebit = metrics.get("ebit_ann")
    if cov is not None:
        x["interest_coverage_score_input"] = min(cov, COVERAGE_CAP)
    elif metrics.get("interest_coverage_note") == "net_financial_income" and (ebit or 0) > 0:
        x["interest_coverage_score_input"] = COVERAGE_CAP
    elif ebit is not None and ebit <= 0 and metrics.get("net_finance_cost_ann") is not None:
        x["interest_coverage_score_input"] = 0.0
    else:
        x["interest_coverage_score_input"] = None
    x["f_ratio"] = f.get("ratio") if f.get("applicable") else None
    x["f_available"] = f.get("available") or 0
    return x


def composite_scores(rows: List[Dict]) -> None:
    """Fill ``quality`` for every row in place.

    ``rows`` items: ``{ticker, sector_type, sector, inputs{}}``. Each
    component is a percentile rank within the sector-type group (industrial /
    bank / insurer) when the group has >= MIN_GROUP_FOR_RANKS companies with
    that metric, else an absolute anchor score. The F component is absolute.
    Missing components are dropped and the remaining weights renormalised;
    ``coverage_pct`` = share of the full weight that had data; the score is
    withheld below MIN_QUALITY_COVERAGE.
    """
    for stype, comps in COMPONENTS.items():
        group = [r for r in rows if r["sector_type"] == stype]
        for r in group:
            parts, wsum, wfull = [], 0.0, 0.0
            for metric, w, hib, anchors in comps:
                v = r["inputs"].get(metric)
                weff = w
                if metric == "f_ratio":
                    weff = w * (r["inputs"].get("f_available") or 0) / 9.0
                    wfull += w
                    if v is None or weff == 0:
                        parts.append({"metric": metric, "value": v, "score": None, "weight": w, "basis": None})
                        continue
                    s, basis = 100.0 * v, "absolute"
                else:
                    wfull += w
                    if v is None:
                        parts.append({"metric": metric, "value": None, "score": None, "weight": w, "basis": None})
                        continue
                    peers = [g["inputs"].get(metric) for g in group if g["inputs"].get(metric) is not None]
                    if len(peers) >= MIN_GROUP_FOR_RANKS:
                        s, basis = percentile_rank(peers, v, hib), "rank_{}".format(stype)
                    else:
                        s, basis = piecewise(v, anchors), "absolute"
                parts.append({"metric": metric, "value": round(v, 6), "score": round(s, 1), "weight": round(weff, 4),
                              "basis": basis})
                wsum += weff
            cov = 100.0 * wsum / wfull if wfull else 0.0
            score = sum(p["score"] * p["weight"] for p in parts if p["score"] is not None) / wsum if wsum else None
            r["quality"] = {
                "score": round(score, 1) if (score is not None and cov >= MIN_QUALITY_COVERAGE) else None,
                "raw_score": round(score, 1) if score is not None else None,
                "coverage_pct": round(cov, 1), "components": parts,
            }
    # ranks within the universe (same sector type) and within the GPW sector
    for stype in SECTOR_TYPES:
        group = [r for r in rows if r["sector_type"] == stype and r.get("quality", {}).get("score") is not None]
        scores = [r["quality"]["score"] for r in group]
        ordered = sorted(group, key=lambda r: (-r["quality"]["score"], r["ticker"]))
        for i, r in enumerate(ordered, 1):
            r["quality"]["rank"] = i
            r["quality"]["rank_of"] = len(ordered)
            r["quality"]["pct_universe"] = round(percentile_rank(scores, r["quality"]["score"]), 1)
        by_sector: Dict[str, List[Dict]] = {}
        for r in group:
            by_sector.setdefault((r.get("sector") or "").lower(), []).append(r)
        for sec, members in by_sector.items():
            sc = [m["quality"]["score"] for m in members]
            for r in members:
                r["quality"]["sector_n"] = len(members)
                r["quality"]["pct_sector"] = (round(percentile_rank(sc, r["quality"]["score"]), 1)
                                              if sec and len(members) >= MIN_SECTOR_FOR_RANKS else None)
    for r in rows:
        r.setdefault("quality", {"score": None, "raw_score": None, "coverage_pct": 0.0, "components": []})
        r["quality"].setdefault("rank", None)
        r["quality"].setdefault("pct_universe", None)
        r["quality"].setdefault("pct_sector", None)


def sector_metric_ranks(rows: List[Dict], metrics=("roe", "roic_approx", "operating_margin", "fcf_margin")) -> None:
    """Percentile of selected metrics within the GPW sector (>= 3 companies)."""
    by_sector: Dict[str, List[Dict]] = {}
    for r in rows:
        by_sector.setdefault(((r.get("sector") or "").lower(), r["sector_type"]), []).append(r)
    for (sec, _t), members in by_sector.items():
        for r in members:
            out = {}
            for k in metrics:
                vals = [m["inputs"].get(k) for m in members if m["inputs"].get(k) is not None]
                v = r["inputs"].get(k)
                out[k] = (round(percentile_rank(vals, v), 1)
                          if sec and v is not None and len(vals) >= MIN_SECTOR_FOR_RANKS else None)
            r["sector_ranks"] = out


# --------------------------------------------------------------------------
# Banks: P/BV vs ROE
# --------------------------------------------------------------------------

def ols(xs: Sequence[float], ys: Sequence[float]) -> Optional[Dict]:
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = my - b * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    return {"intercept": a, "slope": b, "r2": (1 - ss_res / ss_tot) if ss_tot else None, "n": n}


def bank_pbv_regression(rows: List[Dict]) -> Optional[Dict]:
    """P/BV = a + b x ROE across banks (justified P/BV = (ROE - g)/(COE - g)
    is linear in ROE). Residual < 0: cheaper than peers for its ROE. Fills
    ``r['pbv_vs_roe']`` for banks and returns the fit (None if < 4 banks)."""
    pts = []
    for r in rows:
        if r["sector_type"] != "bank":
            continue
        roe, pbv = r["inputs"].get("roe"), r["inputs"].get("pbv_gpw") or r["inputs"].get("pbv_calc")
        if roe is not None and pbv is not None:
            pts.append((r, roe, pbv))
    if len(pts) < MIN_BANKS_FOR_REGRESSION:
        for r, _x, _y in pts:
            r["pbv_vs_roe"] = None
        return None
    fit = ols([p[1] for p in pts], [p[2] for p in pts])
    if not fit:
        return None
    for r, roe, pbv in pts:
        fitted = fit["intercept"] + fit["slope"] * roe
        r["pbv_vs_roe"] = {"pbv": round(pbv, 3), "fitted": round(fitted, 3), "residual": round(pbv - fitted, 3),
                           "residual_pct": round(100 * (pbv / fitted - 1), 1) if fitted > 0 else None}
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in fit.items()}


# --------------------------------------------------------------------------
# Filters and signals
# --------------------------------------------------------------------------

def passes_filters(row: Dict, min_f: Optional[int] = None, max_pe: Optional[float] = None,
                   min_roe: Optional[float] = None) -> Tuple[bool, Optional[str]]:
    """(keep, reason). ``min_f`` keeps a company only when its F lower bound
    (passed criteria) reaches it; an upper bound below it excludes it; in
    between it is 'undetermined' and excluded (reason ``f_undetermined``).
    ``max_pe`` uses GPW's P/E, else the computed one; loss-makers (no P/E)
    fail. ``min_roe`` in percent."""
    f = row.get("f") or {}
    if min_f is not None:
        if not f.get("applicable"):
            return False, "f_not_applicable"
        if f.get("lower") is None or f["upper"] < min_f:
            return False, "f_below"
        if f["lower"] < min_f:
            return False, "f_undetermined"
    m = row.get("metrics") or {}
    if max_pe is not None:
        pe = m.get("pe_gpw") or m.get("pe_calc")
        if pe is None or pe <= 0 or pe > max_pe:
            return False, "pe"
    if min_roe is not None:
        roe = m.get("roe")
        if roe is None or roe * 100 < min_roe:
            return False, "roe"
    return True, None


def signal_strength_red(flags: List[Dict]) -> int:
    """30 points per high-severity and 15 per medium flag, capped at 100."""
    return int(min(100, sum(30 if f["severity"] == "high" else 15 for f in flags)))


def build_signals(rows: List[Dict], as_of: str) -> List[Dict]:
    """Generic ``signals[]`` (gpw-signal-postmortem contract v1).

    * ``quality_high`` / long: quality score >= 80, coverage >= 70 %, period
      not stale, no high-severity red flag (and for industrials at least the
      three level-based F criteria known).
    * ``quality_red_flags`` / avoid: >= 2 red flags or one high-severity flag,
      period not stale.

    No signal for a company with a stale period or a failed data check.

    The signal date is the session when the current reporting period first
    appeared in the local history (``signal_date``), so re-running the
    screener on later days repeats the same (ticker, date, kind) and the
    post-mortem deduplicates it.
    """
    out = []
    for r in rows:
        if (r.get("status") != "ok" or r.get("stale_period") or not r.get("signal_date")
                or r.get("data_checks")):
            continue
        q = r.get("quality") or {}
        flags = r.get("red_flags") or []
        high = [f for f in flags if f["severity"] == "high"]
        date = min(r["signal_date"], as_of)
        base = {"ticker": r["ticker"], "date": date}
        if r.get("signal_price"):
            base["price"] = r["signal_price"]
        tags = {"sector_type": r["sector_type"], "period": (r.get("metrics") or {}).get("period") or ""}
        f = r.get("f") or {}
        if (q.get("score") is not None and q["score"] >= SIGNAL_QUALITY_HIGH
                and q.get("coverage_pct", 0) >= SIGNAL_MIN_COVERAGE and not high
                and (r["sector_type"] != "industrial" or (f.get("available") or 0) >= 3)):
            s = dict(base, kind="quality_high", direction="long", score=round(q["score"], 1),
                     tags=dict(tags, f=f.get("display") or "n/a"))
            out.append(s)
        if (len(flags) >= 2 or high) and q.get("coverage_pct", 0) >= SIGNAL_MIN_COVERAGE:
            out.append(dict(base, kind="quality_red_flags", direction="avoid", score=signal_strength_red(flags),
                            tags=dict(tags, flags=",".join(f["code"] for f in flags)[:80])))
    return out
