"""Engine tests: statements, F-score (incl. unknowns), annualisation, metrics,
red flags, composite ranks, bank regression, filters and signals. No network."""

import json
import math

import pytest

import quality_engine as qe
from gpwlib import fundamentals
from quality_screener_fixtures import FIXTURES, prior_stmt, read, stmt

ALL_IDS = [cid for cid, _ in qe.F_CRITERIA]


def by_id(f):
    return {c["id"]: c for c in f["criteria"]}


# --------------------------------------------------------------------------
# Statements from real card fixtures
# --------------------------------------------------------------------------

def test_statement_from_notoria_kety_uses_parent_lines():
    st = qe.statement_from_notoria(fundamentals.parse_notoria_html(read("card_notoria_KETY.html")))
    assert st["label"] == "I-III kw. 2026" and st["months"] == 9 and st["period_end"] == "2026-09-30"
    assert st["net_income"] == pytest.approx(594e6) and st["net_income_basis"] == "parent"
    assert st["equity"] == pytest.approx(2140e6) and st["equity_basis"] == "parent"
    assert st["ebitda"] == pytest.approx(968e6)
    assert st["capex"] == pytest.approx(-175e6)  # Notoria sign: outflow negative
    assert st["total_liabilities"] == pytest.approx(690e6 + 1794e6)
    assert st["cash"] is None and st["borrowings"] is None and st["gross_profit"] is None


def test_statement_from_notoria_bank_has_no_current_split():
    st = qe.statement_from_notoria(fundamentals.parse_notoria_html(read("card_notoria_PKOBP.html")))
    assert st["current_assets"] is None and st["current_liabilities"] is None
    # liabilities fall back to assets - parent equity
    assert st["total_liabilities"] == pytest.approx(619866e6 - 57149e6)
    assert "minority" in st["total_liabilities_basis"]


def test_statement_none_for_empty():
    assert qe.statement_from_notoria(None) is None
    assert qe.statement_from_notoria({"values": {}}) is None


def test_ebitda_derived_when_missing():
    noto = {"period_label": "2025", "period": fundamentals.parse_period_label("2025"),
            "values": {"operating_profit": 10.0, "depreciation_amortization": 5.0}}
    st = qe.statement_from_notoria(noto)
    assert st["ebitda"] == 15.0 and st["months"] == 12


# --------------------------------------------------------------------------
# Sector type
# --------------------------------------------------------------------------

@pytest.mark.parametrize("sector,ticker,members,expected,other", [
    ("banki komercyjne", "PKOBP", [], "bank", False),
    ("firmy ubezpieczeniowe", "PZU", [], "insurer", False),
    ("", "MBANK", ["MBANK", "PKOBP"], "bank", False),
    ("giełdy i biura maklerskie", "XTB", [], "industrial", True),
    ("Wierzytelności", "KRUK", [], "industrial", True),
    ("hutnictwo metali nieżelaznych", "KETY", ["PKOBP"], "industrial", False),
    (None, "XYZ", [], "industrial", False),
])
def test_classify_sector(sector, ticker, members, expected, other):
    c = qe.classify_sector(sector, ticker, members)
    assert c["sector_type"] == expected and c["financial_other"] is other


# --------------------------------------------------------------------------
# Piotroski F-score
# --------------------------------------------------------------------------

def test_fscore_without_prior_marks_change_criteria_unknown_not_zero():
    f = qe.piotroski(stmt(), None)
    c = by_id(f)
    assert [c[i]["status"] for i in ("roa_positive", "cfo_positive", "accruals")] == ["pass"] * 3
    for i in ("roa_improving", "leverage_down", "liquidity_up", "no_equity_issue", "margin_up", "turnover_up"):
        assert c[i]["status"] == "unknown" and c[i]["note"] == "no_comparable_prior_period"
    assert f["passed"] == 3 and f["available"] == 3 and f["unknown"] == 6
    assert f["score"] is None                      # never a 0-9 integer when incomplete
    assert f["display"] == "3/3" and f["ratio"] == 1.0
    assert f["coverage_pct"] == pytest.approx(33.3, abs=0.1)
    assert (f["lower"], f["upper"]) == (3, 9) and f["band"] == "undetermined"


def test_fscore_full_nine_with_improving_prior():
    f = qe.piotroski(stmt(), prior_stmt())
    assert f["available"] == 9 and f["score"] == 9 and f["band"] == "high"
    assert all(c["status"] == "pass" for c in f["criteria"])
    assert [c["id"] for c in f["criteria"]] == ALL_IDS
    assert f["prior_label"] == "I-III kw. 2025"


def test_fscore_each_change_criterion_can_fail():
    cur = stmt(net_income=60.0, cfo=50.0, noncurrent_liabilities=300.0, current_assets=300.0,
               share_capital=60_000_000.0, profit_on_sales=90.0, revenue=700.0)
    f = qe.piotroski(cur, prior_stmt())
    c = by_id(f)
    assert c["roa_positive"]["status"] == "pass" and c["cfo_positive"]["status"] == "pass"
    for i in ("roa_improving", "accruals", "leverage_down", "liquidity_up", "no_equity_issue", "margin_up",
              "turnover_up"):
        assert c[i]["status"] == "fail", i
    assert f["score"] == 2 and f["band"] == "middle"


def test_fscore_low_band_and_loss():
    cur = stmt(net_income=-50.0, cfo=-20.0, noncurrent_liabilities=300.0, current_assets=300.0,
               share_capital=60_000_000.0, profit_on_sales=50.0, revenue=700.0)
    f = qe.piotroski(cur, prior_stmt())
    # CFO (-20) > NI (-50) -> accrual signal still passes (Piotroski: CFO/A > ROA)
    assert by_id(f)["accruals"]["status"] == "pass"
    assert f["score"] == 1 and f["band"] == "low"


def test_fscore_missing_cfo_is_unknown():
    f = qe.piotroski(stmt(cfo=None), prior_stmt())
    c = by_id(f)
    assert c["cfo_positive"]["status"] == "unknown" and c["accruals"]["status"] == "unknown"
    assert f["available"] == 7 and f["score"] is None
    assert f["display"] == "{}/7".format(f["passed"])


def test_fscore_prior_with_other_window_is_not_comparable():
    f = qe.piotroski(stmt(), prior_stmt(months=6, last_quarter=2, label="I-II kw. 2025"))
    assert f["available"] == 3 and by_id(f)["roa_improving"]["note"] == "no_comparable_prior_period"


def test_fscore_consolidation_change_blocks_comparison():
    f = qe.piotroski(stmt(consolidated=True), prior_stmt(consolidated=False))
    assert f["available"] == 3 and by_id(f)["turnover_up"]["note"] == "consolidation_changed"


def test_fscore_full_years_compare():
    cur = stmt(months=12, label="2025", year=2025, last_quarter=4)
    pr = prior_stmt(months=12, label="2024", year=2024, last_quarter=4)
    assert qe.piotroski(cur, pr)["available"] == 9


def test_equity_issue_rounding_tolerance_and_shares_issued():
    # +0.5 m PLN share capital = Notoria rounding -> not an issue
    f = qe.piotroski(stmt(share_capital=50_500_000.0), prior_stmt())
    assert by_id(f)["no_equity_issue"]["status"] == "pass"
    assert by_id(f)["no_equity_issue"]["proxy"] == "share_capital"
    # exact share counts win over share capital
    f = qe.piotroski(stmt(), prior_stmt(), shares_now=1_030_000, shares_then=1_000_000)
    assert by_id(f)["no_equity_issue"]["status"] == "fail" and by_id(f)["no_equity_issue"]["proxy"] is None
    # shares_issued history alone decides EQ_OFFER even without a prior statement
    f = qe.piotroski(stmt(), None, shares_now=1_000_000, shares_then=1_000_000)
    assert by_id(f)["no_equity_issue"]["status"] == "pass" and f["available"] == 4


def test_margin_gross_profit_arithmetic():
    f = qe.piotroski(stmt(gross_profit=300.0), prior_stmt(gross_profit=320.0))
    m = by_id(f)["margin_up"]
    assert m["current"] == pytest.approx(1 / 3) and m["prior"] == pytest.approx(0.4)
    assert m["status"] == "fail"
    f = qe.piotroski(stmt(), prior_stmt())
    assert "proxy" in by_id(f)["margin_up"] and by_id(f)["margin_up"]["proxy"].startswith("profit on sales")


def test_leverage_zero_both_years_passes():
    f = qe.piotroski(stmt(noncurrent_liabilities=0.0), prior_stmt(noncurrent_liabilities=0.0))
    assert by_id(f)["leverage_down"]["status"] == "pass"
    assert by_id(f)["leverage_down"]["note"] == "no_long_term_debt_either_year"


def test_band_high_when_lower_bound_reaches_eight():
    f = qe._summarise([qe._crit(cid, "pass") for cid in ALL_IDS[:8]] + [qe._crit(ALL_IDS[8], "unknown")])
    assert f["band"] == "high" and f["score"] is None and f["lower"] == 8
    f = qe._summarise([qe._crit(cid, "fail") for cid in ALL_IDS[:8]] + [qe._crit(ALL_IDS[8], "unknown")])
    assert f["band"] == "low" and f["upper"] == 1


def test_fscore_no_statement():
    f = qe.piotroski(None)
    assert f["available"] == 0 and f["display"] == "—" and f["ratio"] is None


def test_not_applicable_for_financials():
    f = qe.not_applicable_f("financial_bank")
    assert f["applicable"] is False and f["score"] is None and f["band"] == "not_applicable"


# --------------------------------------------------------------------------
# Annualisation and TTM
# --------------------------------------------------------------------------

def test_annualise_ytd():
    st = stmt()
    assert qe.annualisation_factor(st) == pytest.approx(12 / 9)
    assert qe.annualised(st, "net_income") == pytest.approx(120.0)
    h1 = stmt(months=6, last_quarter=2)
    assert qe.annualised(h1, "net_income") == pytest.approx(180.0)
    fy = stmt(months=12)
    assert qe.annualised(fy, "net_income") == pytest.approx(90.0)
    assert qe.annualised(stmt(months=None), "net_income") is None


def test_ttm_from_prior_full_year():
    cur = stmt(net_income=90.0)
    prev_ytd = prior_stmt(net_income=70.0)
    prev_fy = prior_stmt(months=12, last_quarter=4, label="I-IV kw. 2025", net_income=100.0)
    ttm = qe.ttm_from(cur, prev_fy, prev_ytd)
    assert ttm["net_income"] == pytest.approx(90 + 100 - 70)
    assert qe.annualised(cur, "net_income", ttm) == pytest.approx(120.0)  # TTM wins over x12/9
    assert qe.ttm_from(cur, None, prev_ytd) is None
    assert qe.ttm_from(stmt(months=12), prev_fy, prev_ytd) is None


def test_metrics_report_annualisation_kind():
    m = qe.compute_metrics(stmt(), None, {}, "industrial")
    assert m["annualisation"] == "ytd_x_12/9"
    m = qe.compute_metrics(stmt(months=12), None, {}, "industrial")
    assert m["annualisation"] == "fy"
    m = qe.compute_metrics(stmt(), None, {}, "industrial", ttm={"net_income": 100.0})
    assert m["annualisation"] == "ttm" and m["net_income_ann"] == 100.0


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def test_metrics_industrial_formulas():
    st = stmt()
    m = qe.compute_metrics(st, None, {"market_cap": 2400.0, "pe_gpw": 18.0, "pbv_gpw": 4.0,
                                      "dividend_yield_pct": 3.0}, "industrial")
    assert m["roe"] == pytest.approx(120 / 550, rel=1e-4)
    assert m["roa"] == pytest.approx(120 / 1000)
    assert m["roic_approx"] == pytest.approx(160 * 0.81 / 750, rel=1e-4)
    assert m["operating_margin"] == pytest.approx(120 / 900, rel=1e-4)
    assert m["fcf_ann"] == pytest.approx((120 - 45) * 12 / 9)
    assert m["fcf_margin"] == pytest.approx(75 / 900, rel=1e-4)
    assert m["accruals_ratio"] == pytest.approx((120 - 160) / 1000, rel=1e-4)
    assert m["interest_coverage_proxy"] == pytest.approx(120 / 10)
    assert m["pe_calc"] == pytest.approx(2400 / 120)
    assert m["fcf_yield"] == pytest.approx(100 / 2400, rel=1e-4)
    assert m["net_debt"] is None and m["ev_ebitda"] is None and m["net_debt_note"] == "not_in_notoria"
    assert m["averages_use_prior_year"] is False


def test_metrics_use_average_with_prior_and_dilution_from_share_capital():
    m = qe.compute_metrics(stmt(share_capital=56_000_000.0), prior_stmt(), {}, "industrial")
    assert m["roe"] == pytest.approx(120 / 525, rel=1e-4)
    assert m["averages_use_prior_year"] is True
    assert m["share_change_pct"] == pytest.approx(12.0) and m["share_change_basis"] == "share_capital"


def test_metrics_net_financial_income_and_override():
    m = qe.compute_metrics(stmt(pretax_profit=130.0), None, {"market_cap": 1000.0}, "industrial",
                           net_debt_override={"net_debt": 300.0, "ebitda": 200.0, "period_end": "2025-12-31",
                                              "source": "esef"})
    assert m["interest_coverage_proxy"] is None and m["interest_coverage_note"] == "net_financial_income"
    assert m["net_debt_ebitda"] == pytest.approx(1.5)
    assert m["ev"] == pytest.approx(1300.0) and m["ev_ebitda"] == pytest.approx(1300 / 200)
    assert m["net_debt_source"] == "esef"
    x = qe.score_inputs(m, qe.piotroski(stmt()))
    assert x["interest_coverage_score_input"] == qe.COVERAGE_CAP


def test_metrics_bank_skips_industrial_ratios():
    st = qe.statement_from_notoria(fundamentals.parse_notoria_html(read("card_notoria_PKOBP.html")))
    m = qe.compute_metrics(st, None, {"market_cap": 149875e6, "pbv_gpw": 2.62}, "bank")
    assert "roic_approx" not in m and m["cost_income"] is None
    assert m["roe"] == pytest.approx(8223 * 12 / 9 / 57149, rel=1e-4)
    assert m["equity_to_assets"] == pytest.approx(57149 / 619866, rel=1e-4)


def test_metrics_no_statement():
    m = qe.compute_metrics(None, None, {"market_cap": 5.0}, "industrial")
    assert m["market_cap"] == 5.0 and m["period"] is None


# --------------------------------------------------------------------------
# Red flags and data checks
# --------------------------------------------------------------------------

def codes(flags):
    return {f["code"] for f in flags}


def test_red_flags_each():
    st = stmt(equity=-5.0, net_income=10.0, cfo=-30.0)
    m = {"accruals_ratio": 0.2, "net_debt_ebitda": 4.0, "share_change_pct": 8.0}
    assert codes(qe.red_flags(st, m, "industrial")) == {
        "negative_equity", "cfo_negative_with_profit", "high_accruals", "net_debt_ebitda_high", "dilution"}
    assert codes(qe.red_flags(stmt(net_income=-1.0), {}, "industrial")) == {"loss_making"}
    assert qe.red_flags(stmt(), {"accruals_ratio": 0.05, "net_debt_ebitda": None}, "industrial") == []


def test_red_flags_bank_ignore_cash_flow():
    st = stmt(cfo=-100.0, net_income=50.0)
    assert qe.red_flags(st, {"accruals_ratio": 0.3}, "bank") == []


def test_red_flag_thresholds_are_strict():
    assert qe.red_flags(stmt(), {"accruals_ratio": qe.ACCRUALS_RED_FLAG}, "industrial") == []
    assert qe.red_flags(stmt(), {"net_debt_ebitda": 3.5}, "industrial") == []
    assert qe.red_flags(stmt(), {"share_change_pct": 5.0}, "industrial") == []


def test_data_checks():
    assert codes(qe.data_checks(stmt(equity=1.0, noncurrent_liabilities=749.0), "industrial")) == {"equity_near_zero"}
    assert codes(qe.data_checks(stmt(net_income=200.0, pretax_profit=100.0), "industrial")) == {"net_income_above_pretax"}
    assert codes(qe.data_checks(stmt(net_income=5.0, pretax_profit=-1.0), "industrial")) == {"net_income_above_pretax"}
    # liabilities + equity exceed assets by 20 %
    assert codes(qe.data_checks(stmt(equity=750.0), "industrial")) == {"balance_gap"}
    # a positive gap of 30 % = minority interests, not an error
    assert qe.data_checks(stmt(equity=250.0), "industrial") == []
    assert qe.data_checks(stmt(), "industrial") == []


# --------------------------------------------------------------------------
# Composite, ranks, regression
# --------------------------------------------------------------------------

def _row(t, stype="industrial", sector="s", **inp):
    base = {"roic_approx": 0.1, "roa": 0.05, "gross_profitability": 0.1, "accruals_ratio": 0.0, "fcf_margin": 0.05,
            "liabilities_to_assets": 0.5, "interest_coverage_score_input": 5.0, "f_ratio": 1.0, "f_available": 3,
            "roe": 0.12, "equity_to_assets": 0.1}
    base.update(inp)
    return {"ticker": t, "sector_type": stype, "sector": sector, "inputs": base}


def test_composite_ranks_with_large_group():
    rows = [_row("A{}".format(i), roic_approx=0.05 * i, sector="x" if i < 3 else "y") for i in range(6)]
    qe.composite_scores(rows)
    best = max(rows, key=lambda r: r["quality"]["score"])
    assert best["ticker"] == "A5" and best["quality"]["rank"] == 1 and best["quality"]["rank_of"] == 6
    roic = next(c for c in best["quality"]["components"] if c["metric"] == "roic_approx")
    assert roic["basis"] == "rank_industrial" and roic["score"] == pytest.approx(100 * 5.5 / 6, abs=0.1)
    # F weight scales with its coverage (3 of 9 criteria known)
    fcomp = next(c for c in best["quality"]["components"] if c["metric"] == "f_ratio")
    assert fcomp["weight"] == pytest.approx(0.05) and fcomp["basis"] == "absolute"
    assert best["quality"]["coverage_pct"] == pytest.approx(90.0)
    assert best["quality"]["pct_sector"] is not None  # sector y has 3 members
    assert all(r["quality"]["score"] is not None for r in rows)


def test_composite_absolute_for_small_group_and_withholds_low_coverage():
    rows = [_row("ONE", roic_approx=0.20, roa=0.12, gross_profitability=0.30, accruals_ratio=-0.10,
                 fcf_margin=0.15, liabilities_to_assets=0.30, interest_coverage_score_input=10.0, f_ratio=1.0,
                 f_available=9)]
    sparse = _row("SPARSE")
    for k in ("roic_approx", "roa", "gross_profitability", "accruals_ratio", "fcf_margin"):
        sparse["inputs"][k] = None
    rows.append(sparse)
    qe.composite_scores(rows)
    assert rows[0]["quality"]["score"] == pytest.approx(100.0)
    assert all(c["basis"] == "absolute" for c in rows[0]["quality"]["components"])
    assert rows[1]["quality"]["score"] is None and rows[1]["quality"]["coverage_pct"] < qe.MIN_QUALITY_COVERAGE
    assert rows[1]["quality"]["raw_score"] is not None


def test_composite_banks_and_insurers_separate_groups():
    rows = [_row("B1", "bank", roe=0.20, roa=0.02, equity_to_assets=0.13),
            _row("B2", "bank", roe=0.05, roa=0.005, equity_to_assets=0.06),
            _row("I1", "insurer", roe=0.10, equity_to_assets=0.15)]
    qe.composite_scores(rows)
    assert rows[0]["quality"]["score"] == pytest.approx(100.0)
    assert rows[0]["quality"]["rank"] == 1 and rows[1]["quality"]["rank"] == 2
    assert rows[2]["quality"]["score"] == pytest.approx(50.0) and rows[2]["quality"]["rank_of"] == 1


def test_percentile_rank_direction():
    assert qe.percentile_rank([1, 2, 3, 4], 4) == pytest.approx(87.5)
    assert qe.percentile_rank([1, 2, 3, 4], 4, higher_is_better=False) == pytest.approx(12.5)
    assert qe.percentile_rank([], 1) is None


def test_bank_regression_residuals():
    rows = []
    for t, roe, pbv in (("A", 0.10, 1.0), ("B", 0.15, 1.5), ("C", 0.20, 2.0), ("D", 0.15, 1.2)):
        r = _row(t, "bank", roe=roe)
        r["inputs"]["pbv_gpw"] = pbv
        rows.append(r)
    fit = qe.bank_pbv_regression(rows)
    assert fit["n"] == 4 and fit["slope"] > 0
    d = next(r for r in rows if r["ticker"] == "D")
    assert d["pbv_vs_roe"]["residual"] < 0  # cheaper than peers at the same ROE
    assert qe.bank_pbv_regression(rows[:3]) is None


def test_ols_degenerate():
    assert qe.ols([1, 1], [2, 3]) is None
    assert qe.ols([1], [1]) is None


def test_sector_metric_ranks_need_three():
    rows = [_row("A", sector="x", roe=0.1), _row("B", sector="x", roe=0.2), _row("C", sector="z", roe=0.3)]
    rows[2]["inputs"]["roe"] = 0.3
    qe.sector_metric_ranks(rows)
    assert rows[0]["sector_ranks"]["roe"] is None
    rows.append(_row("D", sector="x", roe=0.3))
    qe.sector_metric_ranks(rows)
    assert rows[3]["sector_ranks"]["roe"] == pytest.approx(100 * 2.5 / 3, abs=0.1)


# --------------------------------------------------------------------------
# Filters and signals
# --------------------------------------------------------------------------

def _full_row(f, **m):
    return {"f": f, "metrics": m}


def test_filters_min_f_uses_bounds():
    partial = qe.piotroski(stmt(), None)                    # 3 passed, upper 9
    assert qe.passes_filters(_full_row(partial), min_f=3) == (True, None)
    assert qe.passes_filters(_full_row(partial), min_f=7) == (False, "f_undetermined")
    weak = qe.piotroski(stmt(net_income=-5.0, cfo=-10.0), None)   # passed 1 (accruals), upper 7
    assert qe.passes_filters(_full_row(weak), min_f=8) == (False, "f_below")
    assert qe.passes_filters(_full_row(qe.not_applicable_f("bank")), min_f=1) == (False, "f_not_applicable")


def test_filters_pe_and_roe():
    f = qe.piotroski(stmt())
    assert qe.passes_filters(_full_row(f, pe_gpw=12.0, roe=0.2), max_pe=15, min_roe=15) == (True, None)
    assert qe.passes_filters(_full_row(f, pe_gpw=None, pe_calc=None), max_pe=15) == (False, "pe")
    assert qe.passes_filters(_full_row(f, pe_gpw=None, pe_calc=10.0), max_pe=15) == (True, None)
    assert qe.passes_filters(_full_row(f, roe=0.1), min_roe=15) == (False, "roe")


def _sig_row(t, score=85.0, cov=90.0, flags=(), **kw):
    r = {"ticker": t, "status": "ok", "sector_type": "industrial", "stale_period": False,
         "signal_date": "2026-11-13", "quality": {"score": score, "coverage_pct": cov},
         "red_flags": list(flags), "f": {"available": 3, "display": "3/3"}, "metrics": {"period": "I-III kw. 2026"},
         "data_checks": []}
    r.update(kw)
    return r


HIGH = {"code": "cfo_negative_with_profit", "severity": "high"}
MED = {"code": "high_accruals", "severity": "medium"}


def test_signals_contract():
    import re
    rows = [_sig_row("GOOD"), _sig_row("BAD", score=30.0, flags=[HIGH, MED]), _sig_row("MID", score=60.0, flags=[MED]),
            _sig_row("STALE", stale_period=True), _sig_row("THIN", cov=60.0),
            _sig_row("CHECK", data_checks=[{"code": "equity_near_zero"}]), _sig_row("NODATE", signal_date=None),
            _sig_row("HIGHFLAG", score=90.0, flags=[HIGH], signal_price=12.5)]
    sig = qe.build_signals(rows, "2026-11-13")
    kinds = {(s["ticker"], s["kind"]) for s in sig}
    assert kinds == {("GOOD", "quality_high"), ("BAD", "quality_red_flags"), ("HIGHFLAG", "quality_red_flags")}
    for s in sig:
        assert re.fullmatch(r"[A-Za-z0-9.-]{1,20}", s["ticker"])
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", s["date"]) and s["date"] <= "2026-11-13"
        assert re.fullmatch(r"[a-z0-9_]{1,40}", s["kind"])
        assert s["direction"] in ("long", "avoid") and 0 <= s["score"] <= 100
        assert all(len(str(v)) <= 80 for v in s["tags"].values())
    bad = next(s for s in sig if s["ticker"] == "BAD")
    assert bad["direction"] == "avoid" and bad["score"] == 45
    assert next(s for s in sig if s["ticker"] == "HIGHFLAG")["price"] == 12.5
    json.dumps(sig)


def test_signal_date_never_after_as_of():
    sig = qe.build_signals([_sig_row("A", signal_date="2026-11-20")], "2026-11-13")
    assert sig[0]["date"] == "2026-11-13"


def test_bank_signal_needs_no_fscore():
    sig = qe.build_signals([_sig_row("PKOBP", sector_type="bank", f={"available": 0})], "2026-11-13")
    assert sig and sig[0]["kind"] == "quality_high"


# --------------------------------------------------------------------------
# IFRS statements (ESEF / xBRL)
# --------------------------------------------------------------------------

def test_ifrs_pair_from_xbrl_fixture():
    facts = fundamentals.extract_ifrs_facts(json.loads((FIXTURES / "xbrl_arcticpaper_2023_trimmed.json").read_text()))
    cur, prior = qe.ifrs_fy_pair(facts, "xbrl")
    assert cur["year"] == 2023 and prior["year"] == 2022 and cur["months"] == 12
    assert cur["capex"] == pytest.approx(-200172000.0)
    assert cur["cash"] == pytest.approx(500449000.0)
    f = qe.piotroski(cur, prior)
    c = by_id(f)
    assert c["roa_positive"]["status"] == "pass" and c["roa_improving"]["status"] == "fail"
    assert c["accruals"]["status"] == "pass"            # CFO 471 > NI 272
    assert c["no_equity_issue"]["status"] == "pass"     # issued capital unchanged
    assert c["turnover_up"]["status"] == "fail"         # 3549/2723 vs 4894/3254
    assert c["liquidity_up"]["status"] == "unknown"     # no current assets in the trimmed file
    assert f["score"] is None and f["available"] < 9


def test_statement_from_ifrs_borrowings_sum_and_parent_fallback():
    facts = {"2025-12-31": {"_months": {"Revenue": 12}, "Revenue": 100.0, "ProfitLossAttributableToOwnersOfParent": 9.0,
                            "EquityAttributableToOwnersOfParent": 50.0, "Assets": 120.0, "CurrentBorrowings": 5.0,
                            "NoncurrentBorrowings": 15.0, "CashAndCashEquivalents": 8.0, "Capex": 7.0,
                            "ProfitLossFromOperatingActivities": 12.0, "DepreciationAndAmortisation": 3.0}}
    st = qe.statement_from_ifrs(facts, "2025-12-31")
    assert st["borrowings"] == 20.0 and st["net_income_basis"] == "parent" and st["equity_basis"] == "parent"
    assert st["capex"] == -7.0 and st["ebitda"] == 15.0 and st["label"].startswith("FY2025")
    m = qe.compute_metrics(st, None, {"market_cap": 100.0}, "industrial")
    assert m["net_debt"] == pytest.approx(12.0) and m["net_debt_ebitda"] == pytest.approx(12 / 15)
    assert qe.statement_from_ifrs(facts, "2024-12-31") is None
    assert qe.ifrs_fy_pair({}, "esef") == (None, None)


def test_safe_div_and_piecewise():
    assert qe.safe_div(1, 0) is None and qe.safe_div(None, 1) is None
    assert qe.safe_div(float("nan"), 1) is None
    assert qe.piecewise(5, ((0, 0), (10, 100))) == 50
    assert qe.piecewise(-1, ((0, 0), (10, 100))) == 0 and qe.piecewise(11, ((0, 0), (10, 100))) == 100
    assert not math.isnan(qe.piecewise(0.08, ((0.0, 0), (0.08, 50), (0.2, 100))))
