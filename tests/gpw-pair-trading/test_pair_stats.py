"""Numerics of pair_stats (stdlib ADF / Engle-Granger / half-life / BH).

Reference values marked "statsmodels" were produced with statsmodels 0.14
(adfuller, coint, mackinnonp, mackinnoncrit) on the same inputs while the
skill was built; they pin the implementation to the published procedure.
"""
import math

import pytest

import pair_stats as st
from pair_fixtures import ar1, random_walk


# ---------------------------------------------------------------- OLS

def test_ols_recovers_coefficients_and_se():
    x = [float(i) for i in range(50)]
    y = [2.0 + 3.0 * xi + (0.5 if i % 2 else -0.5) for i, xi in enumerate(x)]
    fit = st.ols(y, [[1.0, xi] for xi in x])
    assert fit["coef"][0] == pytest.approx(2.0, abs=0.2)
    assert fit["coef"][1] == pytest.approx(3.0, abs=0.01)
    assert all(s > 0 for s in fit["se"])


def test_simple_ols_hedge_ratio_exact():
    x = [math.log(10 + i) for i in range(100)]
    y = [0.5 + 1.7 * v for v in x]
    r = st.simple_ols(y, x)
    assert r["beta"] == pytest.approx(1.7, abs=1e-10)
    assert r["alpha"] == pytest.approx(0.5, abs=1e-10)
    assert r["r2"] == pytest.approx(1.0)


def test_simple_ols_rejects_bad_input():
    with pytest.raises(ValueError):
        st.simple_ols([1, 2, 3], [1, 1, 1])
    with pytest.raises(ValueError):
        st.simple_ols([1, 2], [1, 2])


def test_mat_inv_singular():
    with pytest.raises(ValueError):
        st.mat_inv([[1.0, 2.0], [2.0, 4.0]])


# ---------------------------------------------------------------- MacKinnon

def test_mackinnon_p_at_asymptotic_5pct_points():
    # tau at the asymptotic 5% critical value must give p close to 0.05
    assert st.mackinnon_p(-2.86154, "c", 1) == pytest.approx(0.05, abs=0.003)
    assert st.mackinnon_p(-3.33613, "c", 2) == pytest.approx(0.05, abs=0.003)
    assert st.mackinnon_p(-1.941, "n", 1) == pytest.approx(0.05, abs=0.003)


def test_mackinnon_p_bounds_and_continuity():
    assert st.mackinnon_p(5.0, "c", 2) == 1.0
    assert st.mackinnon_p(-30.0, "c", 2) == 0.0
    # small-p and large-p polynomials meet at tau_star (-2.62 for N=2)
    lo, hi = st.mackinnon_p(-2.6201, "c", 2), st.mackinnon_p(-2.6199, "c", 2)
    assert abs(lo - hi) < 0.005
    with pytest.raises(ValueError):
        st.mackinnon_p(-3.0, "ct", 2)


def test_mackinnon_crit_finite_sample():
    cv = st.mackinnon_crit("c", 2, 299)  # statsmodels coint, nobs=300
    assert cv["5%"] == pytest.approx(-3.3566, abs=1e-3)
    assert cv["1%"] == pytest.approx(-3.9334, abs=1e-3)
    assert st.mackinnon_crit("c", 1)["5%"] == pytest.approx(-2.86154)


# ---------------------------------------------------------------- ADF

def test_adf_random_walk_does_not_reject():
    for seed in (1, 2, 4, 5, 6):
        r = st.adf(random_walk(500, seed), "c")
        assert r["p_value"] > 0.05, seed


def test_adf_size_close_to_nominal():
    # under H0 (random walk) a 5% test should reject in roughly 5% of samples
    rej = sum(1 for seed in range(200) if st.adf(random_walk(150, 1000 + seed), "c", maxlag=2)["p_value"] < 0.05)
    assert 2 <= rej <= 22


def test_adf_stationary_ar1_rejects():
    for seed in (1, 2, 3, 4, 5):
        r = st.adf(ar1(500, 0.8, seed), "c")
        assert r["p_value"] < 0.01, seed
        assert r["stat"] < r["crit"]["1%"]


def test_adf_matches_statsmodels_reference():
    # statsmodels adfuller(x, regression="c", autolag="AIC") on this exact series
    x = [math.sin(i / 3.0) + 0.01 * i + e for i, e in enumerate(ar1(300, 0.6, 77))]
    r = st.adf(x, "c")
    assert r["nobs"] + r["lag"] + 1 == 300
    assert r["maxlag"] == 16  # Schwert: ceil(12 * 3^0.25) = ceil(15.79)
    assert r["lag"] == 14 and r["nobs"] == 285
    assert r["stat"] == pytest.approx(-2.7156559198606858, abs=1e-8)
    assert r["p_value"] == pytest.approx(0.0713680717369675, abs=1e-8)


def test_adf_deterministic_series_does_not_crash():
    # lagged differences of a pure sine are collinear; singular lag orders are skipped
    x = [math.sin(i / 3.0) + 0.01 * i for i in range(300)]
    r = st.adf(x, "c")
    assert math.isfinite(r["stat"])


def test_adf_needs_data():
    with pytest.raises(ValueError):
        st.adf([1.0] * 10)
    with pytest.raises(ValueError):
        st.adf(random_walk(100, 1), regression="ct")


def test_adf_lag_selection_picks_up_ar2_structure():
    # AR(2) stationary process: AIC should choose at least one lagged difference
    import random
    rng = random.Random(3)
    x = [0.0, 0.0]
    for _ in range(600):
        x.append(1.2 * x[-1] - 0.5 * x[-2] + rng.gauss(0, 1))
    r = st.adf(x, "c")
    assert r["lag"] >= 1
    assert r["p_value"] < 0.01


# ---------------------------------------------------------------- Engle-Granger

def test_engle_granger_cointegrated_vs_independent():
    x = random_walk(400, 11)
    u = ar1(400, 0.7, 12)
    y = [2.0 + 1.5 * a + e for a, e in zip(x, u)]
    eg = st.engle_granger(y, x)
    assert eg["beta"] == pytest.approx(1.5, abs=0.05)
    assert eg["p_value"] < 0.01
    z = random_walk(400, 99)
    assert st.engle_granger(z, x)["p_value"] > 0.05


def test_engle_granger_uses_n2_tables():
    x = random_walk(300, 21)
    u = ar1(300, 0.9, 22)
    y = [1.0 + 0.8 * a + e for a, e in zip(x, u)]
    eg = st.engle_granger(y, x)
    # statsmodels coint(y, x): (-3.826333311915287, 0.01249862469382835, [-3.93344, -3.35664, -3.05867])
    assert eg["adf_stat"] == pytest.approx(-3.826333311915287, abs=1e-8)
    assert eg["p_value"] == pytest.approx(0.01249862469382835, abs=1e-8)
    assert eg["crit"]["5%"] == pytest.approx(-3.35664144, abs=1e-6)
    assert eg["p_value"] == pytest.approx(st.mackinnon_p(eg["adf_stat"], "c", 2))
    # the same tau priced with the plain unit-root table would look far more significant
    assert st.mackinnon_p(eg["adf_stat"], "n", 1) < eg["p_value"]


# ---------------------------------------------------------------- half-life, Hurst, z

@pytest.mark.parametrize("phi", [0.5, 0.8, 0.9, 0.95])
def test_half_life_matches_ar_coefficient(phi):
    x = ar1(5000, phi, 5, sigma=1.0)
    expected = -math.log(2) / math.log(phi)
    assert st.half_life(x) == pytest.approx(expected, rel=0.15)


def test_half_life_none_for_trend_or_short():
    assert st.half_life([float(i) ** 1.5 for i in range(100)]) is None
    assert st.half_life([1.0, 2.0]) is None


def test_hurst_random_walk_vs_mean_reverting():
    h_rw = st.hurst(random_walk(3000, 8))
    h_mr = st.hurst(ar1(3000, 0.5, 8))
    assert h_rw == pytest.approx(0.5, abs=0.08)
    assert h_mr < 0.3
    assert st.hurst([1.0] * 10) is None


def test_zscore_series_known_values():
    x = [1.0, 2.0, 3.0, 4.0, 10.0]
    z = st.zscore_series(x, 3)
    assert z[:2] == [None, None]
    assert z[2] == pytest.approx(1.0)  # window 1,2,3: mean 2, sd 1
    m = (3 + 4 + 10) / 3.0
    sd = math.sqrt(((3 - m) ** 2 + (4 - m) ** 2 + (10 - m) ** 2) / 2)
    assert z[4] == pytest.approx((10 - m) / sd)
    assert st.zscore_series([5.0] * 5, 3)[4] is None


def test_pearson_and_percentile():
    assert st.pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert st.pearson([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)
    assert st.pearson([1, 1, 1], [1, 2, 3]) is None
    assert st.percentile_rank([1, 2, 3, 4], 3) == 75.0
    assert st.percentile_rank([], 3) is None


# ---------------------------------------------------------------- multiple testing

def test_bh_adjust_textbook_example():
    p = [0.01, 0.04, 0.03, 0.005]
    q = st.bh_adjust(p)
    # sorted: 0.005,0.01,0.03,0.04 -> 0.02,0.02,0.04,0.04
    assert q == pytest.approx([0.02, 0.04, 0.04, 0.02])


def test_bh_monotone_and_capped():
    p = [0.9, 0.5, 0.001, 0.2, 0.04]
    q = st.bh_adjust(p)
    order = sorted(range(len(p)), key=lambda i: p[i])
    qs = [q[i] for i in order]
    assert qs == sorted(qs)
    assert all(0 <= v <= 1 for v in q)
    assert st.bh_adjust([]) == []


def test_bonferroni():
    assert st.bonferroni_adjust([0.01, 0.2, 0.6]) == pytest.approx([0.03, 0.6, 1.0])


def test_bh_less_strict_than_bonferroni():
    p = [0.001, 0.008, 0.012, 0.02, 0.5]
    assert all(b <= f + 1e-12 for b, f in zip(st.bh_adjust(p), st.bonferroni_adjust(p)))
