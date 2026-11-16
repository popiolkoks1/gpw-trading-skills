"""GPW Backtester - robustness tests and the verdict (pure, stdlib only).

* Probabilistic / Deflated Sharpe Ratio - Bailey & Lopez de Prado (2012, 2014).
* Probability of Backtest Overfitting via CSCV - Bailey, Borwein, Lopez de
  Prado & Zhu (2017).
* Walk-forward (anchored and rolling) parameter selection on IS, scored OOS.
* Parameter plateau (neighbourhood stability, not the peak).
* Bootstrap confidence interval of the per-trade expectancy.
* Verdict modelled on tradermonty ``backtest-expert``: NOT_EVALUABLE, REJECT,
  FRAGILE, PROMISING, ROBUST with explicit reasons.
"""

from __future__ import annotations

import itertools
import math
import random
from statistics import NormalDist
from typing import Dict, List, Optional, Sequence, Tuple

import backtester_metrics as bm

EULER_GAMMA = 0.5772156649015329
N01 = NormalDist()

# Verdict thresholds (see references/methodology.md)
MIN_TRADES = 30            # backtest-expert: below 30 trades nothing is evaluable
MIN_YEARS = 5.0            # backtest-expert: test period >= 5 years (GPW: at least one bear + one bull phase)
MAX_DD_REJECT = 50.0       # backtest-expert: drawdown >= 50% is a blocking reason
DSR_MIN = 0.95             # Bailey & Lopez de Prado: 95% confidence the true SR > expected max of N trials
DSR_REJECT = 0.50          # below 0.5 the result is more likely luck than skill
PBO_MAX_ROBUST = 0.25      # PBO > 0.25: > 1 in 4 chance the IS-best is below the OOS median
PBO_REJECT = 0.50          # PBO > 0.5: selection is worse than a coin flip
OOS_IS_MIN = 0.5           # walk-forward OOS Sharpe at least half of IS Sharpe (research brief, common practice)
PLATEAU_MIN = 0.5          # median neighbour Sharpe >= 50% of the best (else a lone peak)
T_STAT_MIN = 3.0           # Harvey, Liu & Zhu (2016): t > 3 for a new "discovery"
TOO_GOOD_WIN = 90.0        # backtest-expert "too good" flag: win rate > 90% ...
TOO_GOOD_DD = 5.0          # ... with drawdown < 5%
TOO_GOOD_SHARPE = 3.0      # or Sharpe > 3 on daily data of a long-only GPW strategy
STRESS_MULT = 2.0          # slippage stress 2x (backtest-expert: 1.5-2x)
BOOT_N, BOOT_SEED = 2000, 20261116
PBO_BLOCKS = 12            # CSCV S (even); C(12,6) = 924 combinations


# ---------------------------------------------------------------------------
# Sharpe statistics
# ---------------------------------------------------------------------------
def psr(sr: float, n_obs: int, skew: float, kurt: float, sr0: float = 0.0) -> Optional[float]:
    """Probabilistic Sharpe Ratio; sr and sr0 in per-period (daily) units, kurt non-excess."""
    if n_obs < 3:
        return None
    den = 1 - skew * sr + (kurt - 1) / 4.0 * sr * sr
    if den <= 0:
        return None
    return N01.cdf((sr - sr0) * math.sqrt(n_obs - 1) / math.sqrt(den))


def expected_max_sr(var_sr: float, n_trials: int) -> float:
    """E[max SR] of N independent trials with zero true SR (False Strategy Theorem)."""
    if n_trials <= 1 or var_sr <= 0:
        return 0.0
    g = EULER_GAMMA
    return math.sqrt(var_sr) * ((1 - g) * N01.inv_cdf(1 - 1.0 / n_trials) + g * N01.inv_cdf(1 - 1.0 / (n_trials * math.e)))


def deflated_sharpe(rets: Sequence[float], n_trials: int, trial_srs: Optional[Sequence[float]] = None) -> Dict:
    """DSR of the selected strategy. ``trial_srs`` = daily SRs of all trials when known
    (grid); otherwise the variance of the SR estimator itself is used."""
    mo = bm.moments(rets)
    if not mo["sd"]:
        return {"available": False, "reason": "no_variance"}
    sr = mo["mean"] / mo["sd"]
    n = mo["n"]
    if trial_srs and len(trial_srs) >= 2:
        m = sum(trial_srs) / len(trial_srs)
        var = sum((x - m) ** 2 for x in trial_srs) / (len(trial_srs) - 1)
        var_src = "trials"
    else:
        var = (1 - mo["skew"] * sr + (mo["kurt"] - 1) / 4.0 * sr * sr) / (n - 1)
        var_src = "estimator"
    sr0 = expected_max_sr(var, n_trials)
    d = psr(sr, n, mo["skew"], mo["kurt"], sr0)
    return {"available": d is not None, "dsr": d, "psr": psr(sr, n, mo["skew"], mo["kurt"], 0.0),
            "sr_daily": sr, "sr_annual": sr * math.sqrt(252), "sr0_daily": sr0, "sr0_annual": sr0 * math.sqrt(252),
            "trials": n_trials, "variance_source": var_src, "skew": mo["skew"], "kurtosis": mo["kurt"], "n_obs": n}


# ---------------------------------------------------------------------------
# PBO via CSCV
# ---------------------------------------------------------------------------
def pbo_cscv(matrix: Sequence[Sequence[float]], blocks: int = PBO_BLOCKS) -> Dict:
    """matrix[j] = daily returns of trial j (equal lengths). Returns PBO and logit stats."""
    n_tr = len(matrix)
    if n_tr < 2:
        return {"available": False, "reason": "needs >= 2 trials"}
    T = min(len(r) for r in matrix)
    if blocks % 2 or T < blocks * 5:
        return {"available": False, "reason": "series too short for {} blocks".format(blocks)}
    size = T // blocks
    agg = []  # agg[b][j] = (sum, sumsq, n)
    for b in range(blocks):
        a, z = b * size, (b + 1) * size if b < blocks - 1 else T
        row = []
        for j in range(n_tr):
            seg = matrix[j][a:z]
            row.append((sum(seg), sum(x * x for x in seg), len(seg)))
        agg.append(row)

    def sr(sel, j):
        s = sum(agg[b][j][0] for b in sel)
        s2 = sum(agg[b][j][1] for b in sel)
        n = sum(agg[b][j][2] for b in sel)
        var = (s2 - s * s / n) / (n - 1) if n > 1 else 0.0
        return s / n / math.sqrt(var) if var > 0 else 0.0

    logits = []
    all_b = set(range(blocks))
    for comb in itertools.combinations(range(blocks), blocks // 2):
        oos = sorted(all_b - set(comb))
        is_sr = [sr(comb, j) for j in range(n_tr)]
        best = max(range(n_tr), key=lambda j: (is_sr[j], -j))
        oos_sr = [sr(oos, j) for j in range(n_tr)]
        rank = 1 + sum(1 for j in range(n_tr) if oos_sr[j] < oos_sr[best]) + 0.5 * sum(
            1 for j in range(n_tr) if j != best and oos_sr[j] == oos_sr[best])
        w = rank / (n_tr + 1)
        logits.append(math.log(w / (1 - w)))
    pbo = sum(1 for x in logits if x <= 0) / len(logits)
    return {"available": True, "pbo": pbo, "combinations": len(logits), "blocks": blocks, "trials": n_tr,
            "median_logit": sorted(logits)[len(logits) // 2]}


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------
def wf_splits(n: int, is_len: int, oos_len: int, anchored: bool) -> List[Tuple[int, int, int, int]]:
    """[(is_start, is_end, oos_start, oos_end)) on return indices 0..n."""
    if is_len < 20 or oos_len < 20:
        raise ValueError("walk-forward windows must be >= 20 sessions")
    out = []
    s = is_len
    while s + oos_len <= n or (s < n and n - s >= oos_len // 2):
        e = min(n, s + oos_len)
        out.append((0 if anchored else s - is_len, s, s, e))
        s = e
    return out


def walk_forward(dates: Sequence[str], trials: Dict[str, Sequence[float]], is_len: int, oos_len: int,
                 anchored: bool) -> Dict:
    keys = list(trials)
    n = min(len(v) for v in trials.values())
    splits = wf_splits(n, is_len, oos_len, anchored)
    if not splits:
        return {"available": False, "reason": "period too short for the windows"}
    folds, oos_rets = [], []
    for a, b, c, d in splits:
        scores = {k: (bm.sharpe(trials[k][a:b]) or -1e9) for k in keys}
        best = max(keys, key=lambda k: (scores[k], k))
        o = bm.sharpe(trials[best][c:d])
        folds.append({"is_from": dates[a], "is_to": dates[b], "oos_from": dates[c], "oos_to": dates[d],
                      "chosen": best, "is_sharpe": scores[best], "oos_sharpe": o,
                      "oos_return_pct": (bm.index_from(trials[best][c:d])[-1] - 1) * 100.0})
        oos_rets.extend(trials[best][c:d])
    is_mean = sum(f["is_sharpe"] for f in folds) / len(folds)
    oos_mean = sum((f["oos_sharpe"] or 0.0) for f in folds) / len(folds)
    oos_perf = bm.perf(list(dates[splits[0][2]:splits[-1][3] + 1]), oos_rets)
    return {"available": True, "mode": "anchored" if anchored else "rolling", "folds": folds,
            "is_sharpe_mean": is_mean, "oos_sharpe_mean": oos_mean,
            "oos_is_ratio": (oos_mean / is_mean) if is_mean > 0 else None,
            "oos_cagr": oos_perf["cagr"], "oos_sharpe": oos_perf["sharpe"], "oos_max_dd": oos_perf["max_dd"],
            "distinct_choices": len({f["chosen"] for f in folds}), "is_sessions": is_len, "oos_sessions": oos_len}


# ---------------------------------------------------------------------------
# Plateau
# ---------------------------------------------------------------------------
def plateau(grid: Dict[str, List], scores: Dict[Tuple, Optional[float]]) -> Dict:
    """scores: {tuple of values in grid key order: Sharpe}. Neighbour = one step in one dimension."""
    keys = list(grid)
    valid = {c: s for c, s in scores.items() if s is not None}
    if len(valid) < 2:
        return {"available": False, "reason": "needs >= 2 grid points"}
    best = max(valid, key=lambda c: (valid[c], c))
    pos = [grid[k].index(best[n]) for n, k in enumerate(keys)]
    neigh = []
    for n, k in enumerate(keys):
        for step in (-1, 1):
            j = pos[n] + step
            if 0 <= j < len(grid[k]):
                c = list(best)
                c[n] = grid[k][j]
                c = tuple(c)
                if c in valid:
                    neigh.append(valid[c])
    vals = sorted(valid.values())
    med_all = vals[len(vals) // 2]
    med_n = sorted(neigh)[len(neigh) // 2] if neigh else None
    ratio = (med_n / valid[best]) if (med_n is not None and valid[best] > 0) else None
    return {"available": True, "best": dict(zip(keys, best)), "best_sharpe": valid[best],
            "neighbour_median_sharpe": med_n, "neighbours": len(neigh), "plateau_ratio": ratio,
            "grid_median_sharpe": med_all, "share_positive": sum(1 for v in vals if v > 0) / len(vals),
            "is_plateau": ratio is not None and ratio >= PLATEAU_MIN}


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def bootstrap_mean_ci(values: Sequence[float], n: int = BOOT_N, seed: int = BOOT_SEED, alpha: float = 0.05) -> Optional[Dict]:
    xs = list(values)
    if len(xs) < 5:
        return None
    rnd = random.Random(seed)
    k = len(xs)
    means = sorted(sum(xs[rnd.randrange(k)] for _ in range(k)) / k for _ in range(n))
    lo, hi = means[int(alpha / 2 * n)], means[min(n - 1, int((1 - alpha / 2) * n))]
    return {"mean": sum(xs) / k, "ci_low": lo, "ci_high": hi, "n": k, "resamples": n, "level": 1 - alpha}


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------
def verdict(*, metrics: Dict, trades: Dict, years: float, stress: Optional[Dict], boot: Optional[Dict],
            dsr: Optional[Dict], pbo: Optional[Dict], wf: Optional[Dict], plat: Optional[Dict],
            wig_cagr: Optional[float], strategy_kind: str = "signal") -> Dict:
    reject, block, limit, notes = [], [], [], []
    n = trades.get("trades") or 0
    if metrics["cagr"] is None or metrics["cagr"] <= 0:
        reject.append("net_cagr_not_positive")
    if metrics["max_dd"] is not None and metrics["max_dd"] <= -MAX_DD_REJECT:
        reject.append("max_dd_ge_50")
    if boot and boot["ci_high"] < 0:
        reject.append("expectancy_ci_below_zero")
    if pbo and pbo.get("available") and pbo["pbo"] > PBO_REJECT:
        reject.append("pbo_gt_0_5")
    if dsr and dsr.get("available") and dsr["dsr"] < DSR_REJECT:
        reject.append("dsr_lt_0_5")
    if years < MIN_YEARS:
        block.append("period_lt_5y")
    if stress is None:
        block.append("no_cost_stress")
    elif stress["cagr"] is None or stress["cagr"] <= 0:
        block.append("cost_stress_kills_edge")
    win = trades.get("win_rate")
    if (win is not None and win > TOO_GOOD_WIN and metrics["max_dd"] > -TOO_GOOD_DD) or \
            (metrics.get("sharpe") or 0) > TOO_GOOD_SHARPE:
        block.append("too_good_to_be_true")
    if boot and boot["ci_low"] <= 0 <= boot["ci_high"]:
        block.append("expectancy_ci_includes_zero")
    if dsr and dsr.get("available") and DSR_REJECT <= dsr["dsr"] < DSR_MIN:
        block.append("dsr_lt_0_95")
    if plat and plat.get("available") and not plat["is_plateau"]:
        block.append("no_parameter_plateau")
    if wf and wf.get("available"):
        r = wf.get("oos_is_ratio")
        if r is None or r < OOS_IS_MIN:
            block.append("oos_is_ratio_lt_0_5")
    else:
        limit.append("no_walk_forward")
    if not (pbo and pbo.get("available")):
        limit.append("no_pbo")
    elif pbo["pbo"] > PBO_MAX_ROBUST:
        limit.append("pbo_gt_0_25")
    if (metrics.get("t_stat") or 0) < T_STAT_MIN:
        limit.append("t_stat_lt_3")
    conc = metrics.get("concentration") or {}
    if conc.get("pnl_ex_top3_pln") is not None and conc["pnl_ex_top3_pln"] <= 0 and n >= MIN_TRADES:
        limit.append("edge_depends_on_few_trades")
    if wig_cagr is not None and metrics["cagr"] is not None and metrics["cagr"] <= wig_cagr:
        limit.append("does_not_beat_wig")
    if strategy_kind == "dca":
        notes.append("benchmark_strategy")
        limit = [x for x in limit if x == "does_not_beat_wig"]
    if n < MIN_TRADES and strategy_kind != "dca":
        v = "NOT_EVALUABLE"
    elif strategy_kind == "dca":
        v = "NOT_EVALUABLE"
    elif reject:
        v = "REJECT"
    elif block:
        v = "FRAGILE"
    elif limit:
        v = "PROMISING"
    else:
        v = "ROBUST"
    return {"verdict": v, "reject_reasons": reject, "blocking_reasons": block, "limiting_reasons": limit,
            "notes": notes, "min_trades": MIN_TRADES}
