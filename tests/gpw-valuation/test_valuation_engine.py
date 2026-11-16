"""Engine math vs hand calculations (no network)."""

import math

import pytest

import valuation_engine as ve


# --------------------------------------------------------------- reverse DCF

def test_reverse_dcf_ev_hand_calculation():
    # FCF0=100, g=10%, r=10%, tg=2%, 2 years:
    # CF1=110 -> PV 100; CF2=121 -> PV 100; TV=121*1.02/0.08=1542.75 -> PV 1275
    ev = ve.reverse_dcf_ev(100, 0.10, 0.10, 0.02, years=2)
    assert ev == pytest.approx(1475.0, abs=1e-9)


def test_solver_recovers_known_growth():
    target = ve.reverse_dcf_ev(250.0, 0.07, 0.09, 0.025)
    sol = ve.solve_implied_growth(target, 250.0, 0.09, 0.025)
    assert sol["status"] == "ok"
    assert sol["growth"] == pytest.approx(0.07, abs=1e-7)
    assert sol["ev_at_solution"] == pytest.approx(target, rel=1e-8)


def test_solver_negative_growth_and_monotonic():
    target = ve.reverse_dcf_ev(100.0, -0.05, 0.10, 0.02)
    sol = ve.solve_implied_growth(target, 100.0, 0.10, 0.02)
    assert sol["growth"] == pytest.approx(-0.05, abs=1e-7)
    assert ve.reverse_dcf_ev(100, 0.05, 0.1, 0.02) > ve.reverse_dcf_ev(100, 0.04, 0.1, 0.02)


def test_solver_edges_and_not_solvable():
    assert ve.solve_implied_growth(1000, -5, 0.1, 0.02)["status"] == "not_solvable"
    assert ve.solve_implied_growth(1000, 0, 0.1, 0.02)["status"] == "not_solvable"
    assert ve.solve_implied_growth(-10, 100, 0.1, 0.02)["status"] == "not_solvable"
    huge = ve.solve_implied_growth(1e15, 1.0, 0.1, 0.02)
    assert huge["status"] == "above_range" and huge["growth"] == ve.REVERSE_G_BOUNDS[1]
    tiny = ve.solve_implied_growth(1e-6, 100.0, 0.1, 0.02)
    assert tiny["status"] == "below_range"


def test_implied_fcf_no_growth():
    assert ve.implied_fcf_no_growth(1000.0, 0.09) == pytest.approx(90.0)


# --------------------------------------------------------------- driver DCF

def test_driver_dcf_one_year_hand_calculation():
    d = ve.driver_dcf(1000, 0.10, 0.20, 0.05, 0.05, 0.10, 0.19, 0.10, 0.02, years=1)
    y1 = d["years"][0]
    assert y1["revenue"] == pytest.approx(1100)
    assert y1["nopat"] == pytest.approx(178.2)
    assert y1["delta_nwc"] == pytest.approx(10.0)
    assert y1["fcff"] == pytest.approx(168.2)
    assert y1["pv"] == pytest.approx(168.2 / 1.1)
    # terminal: Rev 1122, NOPAT 181.764, dNWC 2.2 -> FCFF 179.564; TV = /0.08
    assert d["terminal_fcff"] == pytest.approx(179.564)
    assert d["terminal_value"] == pytest.approx(179.564 / 0.08)
    assert d["ev"] == pytest.approx(168.2 / 1.1 + 179.564 / 0.08 / 1.1)
    assert 0 < d["terminal_share"] < 1


def test_driver_dcf_matches_gordon_when_flat():
    # zero growth, capex = D&A, no NWC: FCFF = NOPAT constant -> EV = NOPAT / r
    d = ve.driver_dcf(1000, 0.0, 0.10, 0.03, 0.03, 0.0, 0.19, 0.08, 0.0, years=5)
    assert d["ev"] == pytest.approx(81.0 / 0.08, rel=1e-9)


def test_driver_dcf_reinvestment_and_ronic():
    d = ve.driver_dcf(1000, 0.05, 0.2, 0.03, 0.05, 0.1, 0.19, 0.1, 0.03, years=5)
    assert d["terminal_reinvestment_rate"] > 0
    assert d["implied_ronic"] == pytest.approx(0.03 / d["terminal_reinvestment_rate"])


def test_equity_per_share():
    assert ve.equity_per_share(1000, 200, 100) == pytest.approx(8.0)
    assert ve.equity_per_share(1000, -200, 100) == pytest.approx(12.0)
    assert ve.equity_per_share(1000, 0, 0) is None


# --------------------------------------------------------------- validation

def test_terminal_growth_at_or_above_discount_rate_errors():
    with pytest.raises(ve.ValuationInputError):
        ve.gordon(100, 0.08, 0.08)
    with pytest.raises(ve.ValuationInputError):
        ve.driver_dcf(1000, 0.05, 0.1, 0.03, 0.03, 0.0, 0.19, 0.05, 0.06)
    with pytest.raises(ve.ValuationInputError):
        ve.reverse_dcf_ev(100, 0.05, 0.04, 0.05)
    with pytest.raises(ve.ValuationInputError):
        ve.justified_pbv(0.15, 0.10, 0.10)
    with pytest.raises(ve.ValuationInputError):
        ve.driver_dcf(0, 0.05, 0.1, 0.03, 0.03, 0.0, 0.19, 0.09, 0.02)


# --------------------------------------------------------------- WACC, CAPM, spreads

def test_capm_and_wacc_hand_calculation():
    assert ve.capm(0.0556, 0.86, 0.0533) == pytest.approx(0.0556 + 0.86 * 0.0533)
    w = ve.wacc(0.12, 0.08, 0.19, 800, 200)
    assert w["we"] == pytest.approx(0.8) and w["wd"] == pytest.approx(0.2)
    assert w["kd_after_tax"] == pytest.approx(0.0648)
    assert w["wacc"] == pytest.approx(0.096 + 0.01296)


def test_wacc_net_cash_gets_zero_debt_weight():
    w = ve.wacc(0.11, 0.07, 0.19, 1000, -300)
    assert w["wd"] == 0 and w["wacc"] == pytest.approx(0.11)
    with pytest.raises(ve.ValuationInputError):
        ve.wacc(0.11, 0.07, 0.19, 0, 10)


TABLE = [
    {"lo": -100000.0, "hi": 0.199999, "rating": "D2/D", "spread": 0.19},
    {"lo": 0.2, "hi": 1.999999, "rating": "B1/B+", "spread": 0.0275},
    {"lo": 2.0, "hi": 2.2499999, "rating": "Ba2/BB", "spread": 0.0184},
    {"lo": 2.25, "hi": 8.499999, "rating": "A2/A", "spread": 0.0078},
    {"lo": 8.5, "hi": 100000.0, "rating": "Aaa/AAA", "spread": 0.0040},
]


@pytest.mark.parametrize("cov,rating", [(16.0, "Aaa/AAA"), (2.1, "Ba2/BB"), (2.0, "Ba2/BB"), (0.1, "D2/D"),
                                        (-3.0, "D2/D"), (None, "Aaa/AAA"), (math.inf, "Aaa/AAA"), (5.0, "A2/A")])
def test_synthetic_spread(cov, rating):
    assert ve.synthetic_spread(cov, TABLE)["rating"] == rating


# --------------------------------------------------------------- beta

def test_ols_beta_exact_line():
    m = [0.01, -0.02, 0.015, 0.0, -0.01, 0.03, -0.005]
    a = [0.001 + 1.5 * x for x in m]
    reg = ve.ols_beta(a, m)
    assert reg["beta"] == pytest.approx(1.5)
    assert reg["alpha"] == pytest.approx(0.001)
    assert reg["r2"] == pytest.approx(1.0)
    assert reg["se_beta"] == pytest.approx(0.0, abs=1e-12)


def test_ols_beta_degenerate():
    assert ve.ols_beta([0.1, 0.2], [0.1, 0.2]) is None
    assert ve.ols_beta([0.1, 0.2, 0.3], [0.0, 0.0, 0.0]) is None


def test_blume_and_bounds():
    assert ve.blume(1.5) == pytest.approx(2 / 3 * 1.5 + 1 / 3)
    assert ve.blume(1.0) == pytest.approx(1.0)
    hi = ve.adjusted_beta(4.0)
    assert hi["value"] == ve.BETA_BOUNDS[1] and hi["bounded"]
    lo = ve.adjusted_beta(-1.0)
    assert lo["value"] == ve.BETA_BOUNDS[0] and lo["bounded"]
    mid = ve.adjusted_beta(0.8)
    assert not mid["bounded"] and mid["value"] == pytest.approx(0.8666666, rel=1e-6)


def test_weekly_closes_and_alignment():
    dates = ["2026-11-02", "2026-11-03", "2026-11-06", "2026-11-09", "2026-11-13", "2026-11-16"]
    closes = [10, 11, 12, 13, 14, 15]
    wk = ve.weekly_closes(dates, closes)
    assert wk == {(2026, 45): 12, (2026, 46): 14, (2026, 47): 15}
    mk = {(2026, 45): 100.0, (2026, 47): 110.0}  # week 46 missing in the market series
    rs, rm = ve.weekly_returns_aligned(wk, mk)
    assert rs == [pytest.approx(15 / 12 - 1)] and rm == [pytest.approx(0.1)]


# --------------------------------------------------------------- banks, DDM

def test_bank_formulas():
    assert ve.justified_pbv(0.15, 0.10, 0.03) == pytest.approx(0.12 / 0.07)
    assert ve.implied_roe(2.0, 0.10, 0.03) == pytest.approx(0.17)
    pbv = (0.15 - 0.03) / (0.10 - 0.03)
    assert ve.implied_bank_growth(pbv, 0.15, 0.10) == pytest.approx(0.03)
    assert ve.implied_bank_growth(1.0, 0.15, 0.10) is None
    # ROE == COE -> P/BV = 1 whatever g
    assert ve.justified_pbv(0.11, 0.11, 0.02) == pytest.approx(1.0)


def test_ddm():
    assert ve.ddm_value(5.0, 0.10, 0.02) == pytest.approx(63.75)
    with pytest.raises(ve.ValuationInputError):
        ve.ddm_value(5.0, 0.03, 0.03)


# --------------------------------------------------------------- sensitivity, range

def test_sensitivity_grid_shape_and_invalid_cells():
    sens = ve.sensitivity_grid(lambda r, g: 1.0 / (r - g), 0.03)
    assert sens["rates_pct"] == pytest.approx([1.0, 2.0, 3.0, 4.0, 5.0])
    assert sens["g_pct"] == [0.0, 1.0, 2.0, 3.0, 4.0]
    v = sens["values"]
    assert len(v) == 5 and all(len(r) == 5 for r in v)
    assert v[0][0] == pytest.approx(100.0)        # r=1%, g=0
    assert v[0][1] is None                        # g = r
    assert v[2][3] is None and v[2][4] is None    # g >= r
    assert v[4][2] == pytest.approx(1 / 0.03)
    # value falls with the discount rate, rises with g
    assert v[3][0] > v[4][0] and v[4][1] > v[4][0]


def test_fair_range_and_position():
    rng = ve.fair_range([80.0, 100.0, 130.0])
    assert rng == {"low": 80.0, "base": 100.0, "high": 130.0}
    assert ve.fair_range([None, None, None])["low"] is None
    assert ve.range_position(70.0, rng)["position"] == "below_range"
    assert ve.range_position(140.0, rng)["position"] == "above_range"
    p = ve.range_position(100.0, rng)
    assert p["position"] == "within_range" and p["to_base_pct"] == pytest.approx(0.0)
    assert ve.range_position(50.0, rng)["to_low_pct"] == pytest.approx(60.0)


# --------------------------------------------------------------- comps

def test_quantiles_match_excel_quartile_inc():
    assert ve.quantile([1, 2, 3, 4, 5], 0.25) == 2
    assert ve.quantile([1, 2, 3, 4, 5], 0.5) == 3
    assert ve.quantile([10, 20], 0.25) == pytest.approx(12.5)
    assert ve.quantile([7], 0.75) == 7
    assert ve.quantile([], 0.5) is None
    s = ve.stats_summary([5, 1, 3, None])
    assert s == {"n": 3, "median": 3, "q1": 2, "q3": 4, "min": 1, "max": 5}


def test_comps_implied_equity_vs_ev():
    st = {"n": 5, "q1": 8.0, "median": 10.0, "q3": 12.0}
    pe = ve.comps_implied("pe", st, 100.0, 500.0, 10)        # net debt ignored for P/E
    assert pe == {"low": 80.0, "base": 100.0, "high": 120.0}
    ev = ve.comps_implied("ev_ebitda", st, 100.0, 500.0, 10)  # EV - net debt
    assert ev == {"low": 30.0, "base": 50.0, "high": 70.0}
    assert ve.comps_implied("pe", {"n": 0}, 100.0, 0, 10) is None
    assert ve.comps_implied("pe", st, -5.0, 0, 10) is None


def test_valid_multiple_bounds():
    assert ve.valid_multiple("pe", 12.0)
    assert not ve.valid_multiple("pe", -3.0)
    assert not ve.valid_multiple("pe", 250.0)
    assert not ve.valid_multiple("ev_ebitda", None)
