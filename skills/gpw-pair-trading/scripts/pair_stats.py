"""Pure-Python statistics for pair (relative-value) analysis.

Stdlib only. Everything here is deterministic and unit-tested
(tests/gpw-pair-trading/test_pair_stats.py) and was cross-checked against
statsmodels 0.14 (``adfuller``, ``coint``, ``mackinnonp``, ``mackinnoncrit``)
on synthetic data while the skill was built.

Contents
--------
* ``ols`` / ``simple_ols``     least squares with standard errors
* ``adf``                      Augmented Dickey-Fuller regression, lag order by AIC
* ``mackinnon_p`` / ``mackinnon_crit``  MacKinnon p-values and critical values
* ``engle_granger``            two-step cointegration test (Engle & Granger 1987)
* ``half_life``                mean-reversion half-life from an AR(1) on the spread
* ``hurst``                    Hurst exponent (variance-of-lagged-differences method)
* ``zscore_series``            rolling z-score, ``percentile_rank``, ``pearson``
* ``bh_adjust`` / ``bonferroni_adjust``  multiple-testing corrections
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

# --------------------------------------------------------------------------
# Linear algebra / OLS
# --------------------------------------------------------------------------


def mat_inv(a: List[List[float]]) -> List[List[float]]:
    """Inverse of a small square matrix (Gauss-Jordan, partial pivoting)."""
    n = len(a)
    m = [list(map(float, row)) + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
    tol = 1e-12 * max(max(abs(v) for v in row) for row in a) if n else 0.0
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) <= tol:
            raise ValueError("singular matrix")
        m[col], m[piv] = m[piv], m[col]
        p = m[col][col]
        m[col] = [v / p for v in m[col]]
        for r in range(n):
            if r != col and m[r][col] != 0.0:
                f = m[r][col]
                rr, cr = m[r], m[col]
                m[r] = [rr[j] - f * cr[j] for j in range(2 * n)]
    return [row[n:] for row in m]


def ols(y: Sequence[float], X: Sequence[Sequence[float]]) -> Dict:
    """OLS of ``y`` on the columns of ``X`` (rows = observations).

    Returns ``coef``, ``se``, ``tvalues``, ``resid``, ``ssr``, ``nobs``, ``k``,
    ``llf`` (Gaussian log-likelihood) and ``aic`` (= -2 llf + 2k, the
    statsmodels convention used for ADF lag selection).
    """
    n, k = len(y), len(X[0])
    if n <= k:
        raise ValueError("not enough observations for OLS")
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for row, yi in zip(X, y):
        for i in range(k):
            ri = row[i]
            xty[i] += ri * yi
            for j in range(i, k):
                xtx[i][j] += ri * row[j]
    for i in range(k):
        for j in range(i):
            xtx[i][j] = xtx[j][i]
    inv = mat_inv(xtx)
    coef = [sum(inv[i][j] * xty[j] for j in range(k)) for i in range(k)]
    resid = [yi - sum(c * r for c, r in zip(coef, row)) for row, yi in zip(X, y)]
    ssr = sum(e * e for e in resid)
    s2 = ssr / (n - k)
    se = [math.sqrt(max(inv[i][i] * s2, 0.0)) for i in range(k)]
    tv = [c / s if s > 0 else float("inf") for c, s in zip(coef, se)]
    llf = -n / 2.0 * (math.log(2 * math.pi) + math.log(ssr / n) + 1.0) if ssr > 0 else float("inf")
    return {"coef": coef, "se": se, "tvalues": tv, "resid": resid, "ssr": ssr, "nobs": n, "k": k,
            "llf": llf, "aic": -2.0 * llf + 2.0 * k}


def simple_ols(y: Sequence[float], x: Sequence[float]) -> Dict:
    """y = alpha + beta * x + e -> {alpha, beta, resid, r2}."""
    n = len(y)
    if n != len(x) or n < 3:
        raise ValueError("simple_ols needs two equal series of length >= 3")
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((xi - mx) ** 2 for xi in x)
    if sxx <= 0:
        raise ValueError("x has zero variance")
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    beta = sxy / sxx
    alpha = my - beta * mx
    resid = [yi - alpha - beta * xi for xi, yi in zip(x, y)]
    syy = sum((yi - my) ** 2 for yi in y)
    r2 = 1.0 - sum(e * e for e in resid) / syy if syy > 0 else None
    return {"alpha": alpha, "beta": beta, "resid": resid, "r2": r2}


# --------------------------------------------------------------------------
# MacKinnon p-values and critical values
# --------------------------------------------------------------------------
# P-values: MacKinnon, J.G. (1994) "Approximate asymptotic distribution
# functions for unit-root and cointegration tests", JBES 12(2), 167-176, and
# MacKinnon (1996) "Numerical distribution functions for unit root and
# cointegration tests", J. Applied Econometrics 11, 601-618. The asymptotic
# p-value is Phi(c0 + c1*tau + c2*tau^2 [+ c3*tau^3]) with one polynomial
# below ``tau_star`` (small p) and another above (large p). Coefficients are
# the published ones (same values and scaling as statsmodels.tsa.adfvalues).
# Key: (regression, N) where N = number of I(1) variables (1 = plain ADF
# unit-root test, 2 = Engle-Granger test for two variables) and regression
# is the deterministic term: "c" constant, "n" none.
_P_TABLE = {
    ("c", 1): {"star": -1.61, "min": -18.83, "max": 2.74,
               "small": (2.1659, 1.4412, 0.038269), "large": (1.7339, 0.93202, -0.12745, -0.010368)},
    ("c", 2): {"star": -2.62, "min": -18.86, "max": 0.92,
               "small": (2.92, 1.5012, 0.039796), "large": (2.1945, 0.64695, -0.29198, -0.042377)},
    ("n", 1): {"star": -1.04, "min": -19.04, "max": float("inf"),
               "small": (0.6344, 1.2378, 0.032496), "large": (0.4797, 0.93557, -0.06999, 0.033066)},
}

# Critical values: MacKinnon, J.G. (2010) "Critical Values for Cointegration
# Tests", Queen's Economics Department Working Paper No. 1227, Table 2
# (response surfaces): cv(T) = b_inf + b1/T + b2/T^2 + b3/T^3, rows 1%, 5%, 10%.
_CRIT_2010 = {
    ("c", 1): ((-3.43035, -6.5393, -16.786, -79.433), (-2.86154, -2.8903, -4.234, -40.040),
               (-2.56677, -1.5384, -2.809, 0.0)),
    ("c", 2): ((-3.89644, -10.9519, -33.527, 0.0), (-3.33613, -6.1101, -6.823, 0.0),
               (-3.04445, -4.2412, -2.720, 0.0)),
    ("n", 1): ((-2.56574, -2.2358, -3.627, 0.0), (-1.94100, -0.2686, -3.365, 31.223),
               (-1.61682, 0.2656, -2.714, 25.364)),
}


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def mackinnon_p(stat: float, regression: str = "c", n_vars: int = 1) -> float:
    """Approximate asymptotic p-value of an ADF / Engle-Granger tau statistic."""
    tab = _P_TABLE.get((regression, n_vars))
    if tab is None:
        raise ValueError("no MacKinnon table for regression={!r}, N={}".format(regression, n_vars))
    if stat > tab["max"]:
        return 1.0
    if stat < tab["min"]:
        return 0.0
    coefs = tab["small"] if stat <= tab["star"] else tab["large"]
    z = sum(c * stat ** i for i, c in enumerate(coefs))
    return norm_cdf(z)


def mackinnon_crit(regression: str = "c", n_vars: int = 1, nobs: Optional[int] = None) -> Dict[str, float]:
    """Finite-sample critical values {"1%", "5%", "10%"} (asymptotic if nobs is None)."""
    rows = _CRIT_2010.get((regression, n_vars))
    if rows is None:
        raise ValueError("no MacKinnon critical values for regression={!r}, N={}".format(regression, n_vars))
    out = {}
    for label, b in zip(("1%", "5%", "10%"), rows):
        if nobs:
            out[label] = b[0] + b[1] / nobs + b[2] / nobs ** 2 + b[3] / nobs ** 3
        else:
            out[label] = b[0]
    return out


# --------------------------------------------------------------------------
# ADF and Engle-Granger
# --------------------------------------------------------------------------


def schwert_maxlag(nobs: int) -> int:
    """Default maximum ADF lag, Schwert (1989): 12 * (T/100)^(1/4)."""
    return int(math.ceil(12.0 * (nobs / 100.0) ** 0.25))


def _adf_design(x: Sequence[float], lags: int, regression: str):
    dx = [x[i] - x[i - 1] for i in range(1, len(x))]
    y, X = [], []
    for t in range(lags, len(dx)):
        row = [x[t]]  # level lag: x_{t} is the lagged level for dx[t] = x[t+1]-x[t]
        row += [dx[t - j] for j in range(1, lags + 1)]
        if regression == "c":
            row.append(1.0)
        X.append(row)
        y.append(dx[t])
    return y, X


def _moments(y, X):
    k = len(X[0])
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for row, yi in zip(X, y):
        for i in range(k):
            ri = row[i]
            if ri == 0.0:
                continue
            xty[i] += ri * yi
            xi = xtx[i]
            for j in range(i, k):
                xi[j] += ri * row[j]
    for i in range(k):
        for j in range(i):
            xtx[i][j] = xtx[j][i]
    return xtx, xty, sum(v * v for v in y)


def _fit_from_moments(xtx, xty, yty, n, cols):
    """OLS on a subset of columns using precomputed X'X, X'y, y'y.

    Returns (aic, t-statistic of the first listed column, its coefficient).
    """
    sub = [[xtx[i][j] for j in cols] for i in cols]
    inv = mat_inv(sub)
    rhs = [xty[i] for i in cols]
    k = len(cols)
    coef = [sum(inv[i][j] * rhs[j] for j in range(k)) for i in range(k)]
    ssr = max(yty - sum(c * r for c, r in zip(coef, rhs)), 1e-300)
    llf = -n / 2.0 * (math.log(2 * math.pi) + math.log(ssr / n) + 1.0)
    se0 = math.sqrt(max(inv[0][0] * ssr / (n - k), 1e-300))
    return -2.0 * llf + 2.0 * k, coef[0] / se0, coef[0]


def adf(x: Sequence[float], regression: str = "c", maxlag: Optional[int] = None, autolag: bool = True) -> Dict:
    """Augmented Dickey-Fuller test.

    dx_t = [const] + gamma * x_{t-1} + sum_{j=1..p} phi_j dx_{t-j} + e_t,
    H0: gamma = 0 (unit root). With ``autolag`` the lag order p in 0..maxlag
    minimises AIC on a common sample (the last T - maxlag - 1 observations),
    then the chosen model is re-estimated on all usable observations - the
    procedure of statsmodels ``adfuller(autolag="AIC")``.

    Returns {stat, lag, nobs, maxlag, p_value, crit, gamma}; ``p_value`` and
    ``crit`` assume a plain unit-root test (N = 1). For residuals of a
    cointegrating regression use :func:`engle_granger`, which re-prices the
    statistic with the N = 2 tables.
    """
    if regression not in ("c", "n"):
        raise ValueError("regression must be 'c' or 'n'")
    n = len(x)
    if n < 20:
        raise ValueError("ADF needs at least 20 observations")
    ntrend = 1 if regression == "c" else 0
    if maxlag is None:
        maxlag = min(schwert_maxlag(n), n // 2 - ntrend - 1)
    maxlag = max(0, int(maxlag))
    lag = maxlag
    if autolag and maxlag > 0:
        # one pass over the data: every candidate model is a sub-block of X'X
        y_full, X_full = _adf_design(x, maxlag, regression)
        xtx, xty, yty = _moments(y_full, X_full)
        best = None
        for p in range(0, maxlag + 1):
            cols = [0] + list(range(1, p + 1)) + ([maxlag + 1] if regression == "c" else [])
            try:
                aic = _fit_from_moments(xtx, xty, yty, len(y_full), cols)[0]
            except ValueError:  # (near-)collinear lags, e.g. a deterministic series: skip that order
                continue
            if best is None or aic < best[0] - 1e-12:
                best = (aic, p)
        if best is None:
            raise ValueError("ADF: every candidate regression is singular")
        lag = best[1]
    y, X = _adf_design(x, lag, regression)
    xtx, xty, yty = _moments(y, X)
    _aic, stat, gamma = _fit_from_moments(xtx, xty, yty, len(y), list(range(len(X[0]))))
    return {"stat": stat, "lag": lag, "nobs": len(y), "maxlag": maxlag,
            "p_value": mackinnon_p(stat, regression, 1),
            "crit": mackinnon_crit(regression, 1, len(y)), "gamma": gamma}


def engle_granger(y: Sequence[float], x: Sequence[float], maxlag: Optional[int] = None) -> Dict:
    """Engle-Granger two-step test of cointegration between y and x.

    Step 1: y_t = alpha + beta x_t + u_t (OLS; beta = hedge ratio).
    Step 2: ADF without deterministic terms on u_t; the tau statistic is
    compared with the MacKinnon tables for N = 2 variables with a constant
    ("c"), because the residuals of a regression with an estimated constant
    do not follow the plain Dickey-Fuller distribution.
    """
    reg = simple_ols(y, x)
    test = adf(reg["resid"], regression="n", maxlag=maxlag)
    stat = test["stat"]
    return {"alpha": reg["alpha"], "beta": reg["beta"], "r2": reg["r2"], "resid": reg["resid"],
            "adf_stat": stat, "lag": test["lag"], "nobs": test["nobs"],
            "p_value": mackinnon_p(stat, "c", 2),
            "crit": mackinnon_crit("c", 2, len(y) - 1)}


# --------------------------------------------------------------------------
# Mean reversion descriptors
# --------------------------------------------------------------------------


def half_life(spread: Sequence[float]) -> Optional[float]:
    """Half-life (sessions) from ds_t = a + b s_{t-1} + e: HL = -ln 2 / ln(1 + b).

    1 + b is the AR(1) coefficient of the spread. Returns None when b >= 0
    (no mean reversion) or b <= -1 (oscillation, half-life undefined).
    """
    if len(spread) < 10:
        return None
    lagged = spread[:-1]
    ds = [spread[i] - spread[i - 1] for i in range(1, len(spread))]
    fit = simple_ols(ds, lagged)
    b = fit["beta"]
    if b >= 0 or b <= -1:
        return None
    return -math.log(2.0) / math.log(1.0 + b)


def hurst(x: Sequence[float], min_lag: int = 2, max_lag: int = 20) -> Optional[float]:
    """Hurst exponent: slope of log std(x_{t+k} - x_t) on log k, k in [min_lag, max_lag].

    Std of k-step changes scales as k^H: H = 0.5 random walk, H < 0.5 mean
    reverting, H > 0.5 trending. Short lags (2-20 sessions) match the
    holding periods of a spread trade; the estimate is noisy and is only a
    secondary descriptor next to the ADF test and the half-life.
    """
    if len(x) < max_lag * 3:
        return None
    lx, ly = [], []
    for k in range(min_lag, max_lag + 1):
        d = [x[i + k] - x[i] for i in range(len(x) - k)]
        m = sum(d) / len(d)
        sd = math.sqrt(sum((v - m) ** 2 for v in d) / (len(d) - 1))
        if sd <= 0:
            return None
        lx.append(math.log(k))
        ly.append(math.log(sd))
    return simple_ols(ly, lx)["beta"]


def zscore_series(x: Sequence[float], lookback: int) -> List[Optional[float]]:
    """z_t = (x_t - mean(x_{t-L+1..t})) / sd(x_{t-L+1..t}); None until L values exist."""
    out: List[Optional[float]] = []
    for t in range(len(x)):
        if t + 1 < lookback:
            out.append(None)
            continue
        w = x[t + 1 - lookback:t + 1]
        m = sum(w) / lookback
        sd = math.sqrt(sum((v - m) ** 2 for v in w) / (lookback - 1))
        out.append((x[t] - m) / sd if sd > 0 else None)
    return out


def pearson(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    n = len(a)
    if n != len(b) or n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    sab = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    saa = sum((x - ma) ** 2 for x in a)
    sbb = sum((y - mb) ** 2 for y in b)
    if saa <= 0 or sbb <= 0:
        return None
    return sab / math.sqrt(saa * sbb)


def percentile_rank(history: Sequence[float], value: float) -> Optional[float]:
    """Share (0-100) of ``history`` at or below ``value``."""
    h = [v for v in history if v is not None]
    if not h:
        return None
    return 100.0 * sum(1 for v in h if v <= value) / len(h)


# --------------------------------------------------------------------------
# Multiple testing
# --------------------------------------------------------------------------


def bonferroni_adjust(pvals: Sequence[float]) -> List[float]:
    m = len(pvals)
    return [min(1.0, p * m) for p in pvals]


def bh_adjust(pvals: Sequence[float]) -> List[float]:
    """Benjamini-Hochberg (1995) adjusted p-values (q-values), original order.

    q_(i) = min_{j >= i} (m / j) p_(j), capped at 1. Rejecting q <= alpha
    controls the false discovery rate at alpha for independent or positively
    dependent tests.
    """
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvals[i])
    q = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, pvals[i] * m / rank)
        q[i] = min(1.0, running)
    return q
