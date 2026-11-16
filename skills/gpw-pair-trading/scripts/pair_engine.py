"""Pair / relative-value engine for GPW (pure functions, no I/O).

Inputs are plain lists (dates, adjusted closes, turnover in thousand PLN), so
everything is testable with synthetic data. The CLI (gpw_pair_trading.py)
loads GPW archive data and calls :func:`analyze_pair`, :func:`scan`,
:func:`switch_view` and :func:`signals_for`.

Conventions
-----------
* Spread  s_t = ln A_t - alpha - beta ln B_t  (OLS of ln A on ln B, Engle-Granger step 1).
* z_t = rolling z-score of s_t over ``z_lookback`` sessions (past data only).
* z >= +entry: A is rich relative to B ("B relatively cheap").
  z <= -entry: A is cheap relative to B.
* Cointegration p-value = the larger of the two Engle-Granger p-values
  (A on B and B on A): the test is not symmetric, and taking the worse of
  the two keeps the result independent of the order the user typed.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import pair_stats as st

# Defaults - every value is justified in references/methodology.md.
DEFAULTS = {
    "lookback": 500,          # ~2 years of sessions for the cointegration test (tradermonty: 2y)
    "min_history": 250,       # >= 1 year of common sessions (brief / tradermonty: 252 days)
    "z_lookback": 60,         # rolling window of the z-score (~3 months)
    "entry_z": 2.0,           # Gatev, Goetzmann & Rouwenhorst (2006): open at a 2-sigma divergence
    "exit_z": 0.0,            # close when the spread is back at its mean (GGR 2006)
    "stop_z": 3.5,            # beyond 3.5 sigma treat the relationship as possibly broken
    "min_corr": 0.70,         # tradermonty pair-trade-screener threshold
    "alpha": 0.05,            # significance / false discovery rate
    "stab_window": 250,       # rolling 1-year cointegration windows
    "stab_step": 21,          # one window per ~month
    "turnover_window": 60,    # median turnover window (sessions)
    "position_value": 10000.0,  # PLN per leg used for liquidity and cost examples
    "max_adv_pct": 10.0,      # leg <= 10% of median daily turnover (same rule as gpw-pre-trade-gate)
    "is_share": 0.6,          # first 60% of the window = in-sample, last 40% = out-of-sample
    "max_series_points": 120,  # compact series in JSON
}

# GPW single-stock futures: underlying code in the futures symbol (F<code><month><yy>)
# -> share ticker in the GPW archive. Checked 2026-11-16 against the archive
# (type 35, session 2026-11-13): each futures close is within 3.5% of the share
# close on the same day. Codes BAS, FIN, GMS are sector-index futures
# (WIG.MS-BAS, WIG.MS-FIN, WIG.GAMES), CHF/EUR/GBP/USD currency futures,
# W20/W40 index futures.
FUTURES_CODES = {
    "11B": "11BIT", "ACP": "ASSECOPOL", "ALE": "ALLEGRO", "ALR": "ALIOR", "APR": "AUTOPARTN",
    "ASB": "ASBIS", "ATT": "GRUPAAZOTY", "BDX": "BUDIMEX", "CAR": "INTERCARS", "CDR": "CDPROJEKT",
    "CPS": "CYFRPLSAT", "DIA": "DIAG", "DNP": "DINOPL", "DOM": "DOMDEV", "EAT": "AMREST",
    "EBP": "ERSTEPL", "ENA": "ENEA", "EUH": "EUROCASH", "GPW": "GPW", "ING": "INGBSK", "JSW": "JSW",
    "KGH": "KGHM", "KRU": "KRUK", "KTY": "KETY", "LPP": "LPP", "LWB": "BOGDANKA", "MAB": "MABION",
    "MBK": "MBANK", "MDV": "MODIVO", "MIL": "MILLENNIUM", "OPL": "ORANGEPL", "PCO": "PEPCO",
    "PEO": "PEKAO", "PGE": "PGE", "PKN": "PKNORLEN", "PKO": "PKOBP", "PKP": "PKPCARGO",
    "PXM": "POLIMEXMS", "PZU": "PZU", "SVE": "SNTVERSE", "TEN": "TSGAMES", "TPE": "TAURONPE",
    "XTB": "XTB", "ZAB": "ZABKA",
}
TICKER_TO_FUTURES = {v: k for k, v in FUTURES_CODES.items()}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def align(dates_a: Sequence[str], close_a: Sequence[float], turn_a: Sequence[float],
          dates_b: Sequence[str], close_b: Sequence[float], turn_b: Sequence[float]) -> Dict[str, list]:
    """Common sessions of two legs (both traded / quoted that day)."""
    ib = {d: i for i, d in enumerate(dates_b)}
    out = {"dates": [], "pa": [], "pb": [], "ta": [], "tb": []}
    for i, d in enumerate(dates_a):
        j = ib.get(d)
        if j is None:
            continue
        if close_a[i] <= 0 or close_b[j] <= 0:
            continue
        out["dates"].append(d)
        out["pa"].append(close_a[i])
        out["pb"].append(close_b[j])
        out["ta"].append(turn_a[i] if turn_a else 0.0)
        out["tb"].append(turn_b[j] if turn_b else 0.0)
    return out


def _median(xs: Sequence[float]) -> Optional[float]:
    s = sorted(xs)
    if not s:
        return None
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def _log_returns(p: Sequence[float]) -> List[float]:
    return [math.log(p[i] / p[i - 1]) for i in range(1, len(p))]


def _r(x, d=4):
    return None if x is None else round(x, d)


def coint_test(la: Sequence[float], lb: Sequence[float]) -> Dict:
    """Engle-Granger in both directions; p_value = max of the two."""
    ab = st.engle_granger(la, lb)
    ba = st.engle_granger(lb, la)
    return {"ab": ab, "ba": ba, "p_value": max(ab["p_value"], ba["p_value"]),
            "adf_stat": ab["adf_stat"], "adf_stat_ba": ba["adf_stat"]}


def episode_start(z: Sequence[Optional[float]], dates: Sequence[str], entry: float) -> Optional[str]:
    """First session of the current run of |z| >= entry with the same sign (None if |z_now| < entry)."""
    if not z or z[-1] is None or abs(z[-1]) < entry:
        return None
    sign = 1 if z[-1] > 0 else -1
    i = len(z) - 1
    while i - 1 >= 0 and z[i - 1] is not None and z[i - 1] * sign >= entry:
        i -= 1
    return dates[i]


def sparkline(values: Sequence[Optional[float]], width: int = 60, lo: float = -3.5, hi: float = 3.5) -> str:
    """ASCII/Unicode sparkline of a z-score path, clipped to [lo, hi]."""
    blocks = "▁▂▃▄▅▆▇█"
    vals = [v for v in values if v is not None]
    if not vals:
        return ""
    if len(vals) > width:
        step = len(vals) / float(width)
        vals = [vals[int(i * step)] for i in range(width - 1)] + [vals[-1]]
    out = []
    for v in vals:
        c = min(max(v, lo), hi)
        out.append(blocks[int(round((c - lo) / (hi - lo) * (len(blocks) - 1)))])
    return "".join(out)


def ascii_chart(values: Sequence[Optional[float]], width: int = 60, levels: Sequence[float] = (3, 2, 1, 0, -1, -2, -3)
                ) -> List[str]:
    """Plain-ASCII chart of a z-score path: one row per z level, '*' marks the
    value rounded to the nearest level (clipped to the outer rows); band rows
    (+-2) are drawn with '-' and the zero row with '.'."""
    vals = [v for v in values if v is not None]
    if not vals:
        return []
    if len(vals) > width:
        step = len(vals) / float(width)
        vals = [vals[int(i * step)] for i in range(width - 1)] + [vals[-1]]
    top, bot = levels[0], levels[-1]
    rows = []
    for lv in levels:
        fill = "-" if abs(lv) == 2 else ("." if lv == 0 else " ")
        line = "".join("*" if min(max(round(v), bot), top) == lv else fill for v in vals)
        rows.append("{:+d} |{}".format(int(lv), line).replace("+0", " 0"))
    return rows


def compact(dates: Sequence[str], values: Sequence[Optional[float]], max_points: int, digits: int = 3) -> Dict:
    """Down-sample a series to at most ``max_points`` (always keeps the last point)."""
    n = len(values)
    if n == 0:
        return {"dates": [], "values": []}
    step = max(1, int(math.ceil(n / float(max_points))))
    idx = list(range((n - 1) % step, n, step))
    return {"step": step, "dates": [dates[i] for i in idx],
            "values": [None if values[i] is None else round(values[i], digits) for i in idx]}


# --------------------------------------------------------------------------
# Rolling stability
# --------------------------------------------------------------------------


def rolling_stability(la: Sequence[float], lb: Sequence[float], dates: Sequence[str],
                      window: int, step: int, alpha: float) -> Dict:
    """Engle-Granger (both directions) on rolling windows ending at the last
    session and every ``step`` sessions before it. Returns the share of
    windows with p < alpha and the hedge-ratio path."""
    n = len(la)
    ends = list(range(n, window - 1, -step))[::-1]
    rows = []
    for e in ends:
        s = e - window
        ct = coint_test(la[s:e], lb[s:e])
        rows.append({"end": dates[e - 1], "p_value": round(ct["p_value"], 4),
                     "beta": round(ct["ab"]["beta"], 4), "pass": ct["p_value"] < alpha})
    share = (sum(1 for r in rows if r["pass"]) / len(rows)) if rows else None
    betas = [r["beta"] for r in rows]
    return {"windows": len(rows), "share_cointegrated": None if share is None else round(share, 4),
            "beta_min": min(betas) if betas else None, "beta_max": max(betas) if betas else None,
            "rows": rows}


# --------------------------------------------------------------------------
# Backtests (illustrative)
# --------------------------------------------------------------------------


def _days(d1: str, d2: str) -> int:
    return (dt.date.fromisoformat(d2) - dt.date.fromisoformat(d1)).days


def spread_backtest(dates, pa, pb, z, beta, start, end, cfg, fee: Callable[[float], float],
                    borrow_pct_annual: float) -> Dict:
    """Classic two-legged spread rule on sessions [start, end).

    Open when |z| >= entry (short A / long B when z > 0, the reverse when
    z < 0); close when z crosses ``exit_z`` (back to the mean), at |z| >=
    stop (stop-loss) or at the end of the period. A new trade needs |z| to
    have been back below ``entry`` first. Leg sizes: V PLN in A and beta*V
    PLN in B at entry (fixed share counts). Costs: commission on all four
    orders via ``fee`` and a borrow fee on the short leg (an assumption, see
    methodology). Returns per-trade results and totals.
    """
    V = cfg["position_value"]
    entry, exit_z, stop = cfg["entry_z"], cfg["exit_z"], cfg["stop_z"]
    trades = []
    pos = None
    armed = True
    for t in range(start, end):
        zt = z[t]
        if zt is None:
            continue
        if pos is None:
            if abs(zt) < entry:
                armed = True
            if armed and entry <= abs(zt) < stop:
                side = -1 if zt > 0 else 1  # +1 = long A / short B
                pos = {"side": side, "i": t, "qa": V / pa[t], "qb": beta * V / pb[t], "z_in": zt}
                armed = False
            continue
        side = pos["side"]
        reason = None
        if (side == -1 and zt <= exit_z) or (side == 1 and zt >= -exit_z):
            reason = "mean"
        elif abs(zt) >= stop and zt * side < 0:
            reason = "stop"
        elif t == end - 1:
            reason = "end"
        if reason:
            trades.append(_close_spread(pos, t, reason, dates, pa, pb, fee, borrow_pct_annual))
            pos = None
    if pos is not None and end - 1 > pos["i"]:
        trades.append(_close_spread(pos, end - 1, "end", dates, pa, pb, fee, borrow_pct_annual))
    return _summ_trades(trades, V, beta)


def _close_spread(pos, t, reason, dates, pa, pb, fee, borrow_pct):
    i, side, qa, qb = pos["i"], pos["side"], pos["qa"], pos["qb"]
    gross = side * (qa * (pa[t] - pa[i]) - qb * (pb[t] - pb[i]))
    costs = fee(qa * pa[i]) + fee(qb * pb[i]) + fee(qa * pa[t]) + fee(qb * pb[t])
    short_value = qb * pb[i] if side == 1 else qa * pa[i]
    borrow = short_value * borrow_pct / 100.0 * _days(dates[i], dates[t]) / 365.0
    net = gross - costs - borrow
    return {"entry": dates[i], "exit": dates[t], "side": "long_a_short_b" if side == 1 else "short_a_long_b",
            "z_in": round(pos["z_in"], 2), "sessions": t - i, "reason": reason,
            "gross_pln": round(gross, 2), "costs_pln": round(costs + borrow, 2), "net_pln": round(net, 2)}


def _summ_trades(trades, V, beta):
    n = len(trades)
    net = sum(t["net_pln"] for t in trades)
    gross_exposure = V * (1 + abs(beta))
    return {"trades": n, "net_pln": round(net, 2), "gross_pln": round(sum(t["gross_pln"] for t in trades), 2),
            "costs_pln": round(sum(t["costs_pln"] for t in trades), 2),
            "net_pct_of_exposure": round(100.0 * net / gross_exposure, 2) if n else 0.0,
            "win_rate": round(sum(1 for t in trades if t["net_pln"] > 0) / n, 3) if n else None,
            "stops": sum(1 for t in trades if t["reason"] == "stop"),
            "avg_sessions": round(sum(t["sessions"] for t in trades) / n, 1) if n else None,
            "list": trades}


def switch_backtest(dates, pa, pb, z, start, end, cfg, fee: Callable[[float], float]) -> Dict:
    """Long-only rotation for a holder of A (feasible on any retail account).

    Start in A with V PLN. Switch to B when z >= +entry (A rich vs B);
    switch back to A when z <= -entry (B rich vs A). Every switch pays two
    commissions (sell + buy). Fractional units for simplicity. Compared with
    holding A, holding B and a 50/50 mix bought at the start. Capital-gains
    tax on a switch in a regular account is NOT modelled (it is zero inside
    IKE/IKZE).
    """
    V = cfg["position_value"]
    entry = cfg["entry_z"]
    if end - start < 2:
        return {"switches": 0}
    hold = "A"
    units = (V - fee(V)) / pa[start]
    costs = fee(V)
    switches = []
    for t in range(start + 1, end):
        zt = z[t]
        if zt is None:
            continue
        target = "B" if (hold == "A" and zt >= entry) else ("A" if (hold == "B" and zt <= -entry) else hold)
        if target != hold:
            p_from = pa[t] if hold == "A" else pb[t]
            p_to = pb[t] if target == "B" else pa[t]
            value = units * p_from
            c1 = fee(value)
            cash = value - c1
            c2 = fee(cash)
            units = (cash - c2) / p_to
            costs += c1 + c2
            hold = target
            switches.append({"date": dates[t], "to": target, "z": round(zt, 2)})
    last = end - 1
    final = units * (pa[last] if hold == "A" else pb[last])
    hold_a = V * pa[last] / pa[start] - fee(V)
    hold_b = V * pb[last] / pb[start] - fee(V)
    mix = 0.5 * V * (pa[last] / pa[start] + pb[last] / pb[start]) - fee(V / 2) * 2
    return {"start": dates[start], "end": dates[last], "switches": len(switches), "holding_at_end": hold,
            "final_pln": round(final, 2), "return_pct": round(100.0 * (final / V - 1), 2),
            "hold_a_return_pct": round(100.0 * (hold_a / V - 1), 2),
            "hold_b_return_pct": round(100.0 * (hold_b / V - 1), 2),
            "mix_return_pct": round(100.0 * (mix / V - 1), 2),
            "vs_hold_a_pp": round(100.0 * (final - hold_a) / V, 2),
            "costs_pln": round(costs, 2), "list": switches[-10:]}


# --------------------------------------------------------------------------
# Pair analysis
# --------------------------------------------------------------------------


def prefilter(a: str, b: str, al: Dict, cfg: Dict) -> Dict:
    """Cheap checks before any statistics: history and liquidity."""
    n = len(al["dates"])
    w = cfg["turnover_window"]
    med_a = _median([x * 1000.0 for x in al["ta"][-w:]]) if n else None
    med_b = _median([x * 1000.0 for x in al["tb"][-w:]]) if n else None
    need = cfg["position_value"] / (cfg["max_adv_pct"] / 100.0)
    liq_ok = bool(med_a and med_b and med_a >= need and med_b >= need)
    return {"a": a, "b": b, "sessions": n, "history_ok": n >= cfg["min_history"],
            "median_turnover_a_pln": None if med_a is None else round(med_a, 0),
            "median_turnover_b_pln": None if med_b is None else round(med_b, 0),
            "min_turnover_needed_pln": round(need, 0), "liquidity_ok": liq_ok}


def analyze_pair(a: str, b: str, al: Dict, cfg: Dict, fee: Optional[Callable[[float], float]] = None,
                 borrow_pct_annual: float = 0.0, full: bool = True) -> Dict:
    """Full statistics of one pair on aligned data ``al`` (see :func:`align`).

    ``full=False`` skips the rolling stability, backtests and series (used
    for the first pass of a scan; the CLI completes the shortlisted pairs).
    """
    pre = prefilter(a, b, al, cfg)
    out = dict(pre)
    if not pre["history_ok"]:
        out["status"] = "short_history"
        return out
    L = min(cfg["lookback"], len(al["dates"]))
    dates = al["dates"][-L:]
    pa, pb = al["pa"][-L:], al["pb"][-L:]
    la, lb = [math.log(x) for x in pa], [math.log(x) for x in pb]
    ct = coint_test(la, lb)
    ab = ct["ab"]
    spread = ab["resid"]
    z = st.zscore_series(spread, cfg["z_lookback"])
    zvals = [v for v in z if v is not None]
    z_now = z[-1]
    ra, rb = _log_returns(pa), _log_returns(pb)
    corr_ret = st.pearson(ra, rb)
    corr_px = st.pearson(la, lb)
    hl = st.half_life(spread)
    entry, stop = cfg["entry_z"], cfg["stop_z"]
    if z_now is None:
        state = "unknown"
    elif abs(z_now) >= stop:
        state = "beyond_stop"
    elif z_now >= entry:
        state = "a_rich"
    elif z_now <= -entry:
        state = "a_cheap"
    else:
        state = "neutral"
    out.update({
        "status": "ok", "window_sessions": L, "window_start": dates[0], "as_of": dates[-1],
        "hedge_ratio": round(ab["beta"], 4), "alpha": round(ab["alpha"], 4), "r2": _r(ab["r2"]),
        "hedge_ratio_ba": round(ct["ba"]["beta"], 4),
        "adf_stat": round(ct["adf_stat"], 3), "adf_stat_ba": round(ct["adf_stat_ba"], 3),
        "adf_lag": ab["lag"], "crit_5pct": round(ab["crit"]["5%"], 3),
        "p_value_ab": round(ab["p_value"], 4), "p_value_ba": round(ct["ba"]["p_value"], 4),
        "p_value": round(ct["p_value"], 4), "p_adj": round(ct["p_value"], 4), "tests": 1,
        "half_life_days": None if hl is None else round(hl, 1),
        "hurst": _r(st.hurst(spread), 3),
        "corr": _r(corr_ret, 3), "corr_price": _r(corr_px, 3),
        "z": _r(z_now, 2), "z_percentile": _r(st.percentile_rank(zvals, z_now), 1) if z_now is not None else None,
        "spread_percentile": _r(st.percentile_rank(spread, spread[-1]), 1),
        "state": state, "episode_start": episode_start(z, dates, entry),
    })
    out["corr_ok"] = corr_ret is not None and corr_ret >= cfg["min_corr"]
    # a negative hedge ratio is not a pair (the legs move against each other)
    out["coint_pass"] = out["p_adj"] < cfg["alpha"] and out["corr_ok"] and out["hedge_ratio"] > 0
    if not full:
        return out
    complete(out, al, cfg, fee, borrow_pct_annual)
    return out


def complete(out: Dict, al: Dict, cfg: Dict, fee: Optional[Callable[[float], float]],
             borrow_pct_annual: float) -> Dict:
    """Add stability, hedge-ratio drift, relative performance, backtests and series."""
    fee = fee or (lambda v: 0.0)
    la_all = [math.log(x) for x in al["pa"]]
    lb_all = [math.log(x) for x in al["pb"]]
    stab = rolling_stability(la_all, lb_all, al["dates"], cfg["stab_window"], cfg["stab_step"], cfg["alpha"])
    out["stability"] = stab["share_cointegrated"]
    beta = out["hedge_ratio"]
    last_beta = stab["rows"][-1]["beta"] if stab["rows"] else None
    out["hedge_drift"] = {
        "windows": stab["windows"], "beta_min": stab["beta_min"], "beta_max": stab["beta_max"],
        "beta_last_1y": last_beta,
        "drift_pct": round(100.0 * (last_beta / beta - 1), 1) if (last_beta is not None and beta) else None,
        "path": [{"end": r["end"], "beta": r["beta"], "p": r["p_value"]} for r in stab["rows"]],
    }
    L = out["window_sessions"]
    dates = al["dates"][-L:]
    pa, pb = al["pa"][-L:], al["pb"][-L:]
    # relative performance A vs B (positive = A outperformed)
    rel = {}
    for k in (20, 60, 250):
        if len(pa) > k:
            rel[str(k)] = round(100.0 * ((pa[-1] / pa[-1 - k]) / (pb[-1] / pb[-1 - k]) - 1), 2)
    out["rel_perf_a_vs_b_pct"] = rel
    # backtests: hedge ratio estimated on the in-sample part only
    n_is = int(L * cfg["is_share"])
    la, lb = [math.log(x) for x in pa], [math.log(x) for x in pb]
    bt = {"in_sample": {"start": dates[0], "end": dates[n_is - 1]},
          "out_of_sample": {"start": dates[n_is], "end": dates[-1]}}
    try:
        fit = st.simple_ols(la[:n_is], lb[:n_is])
        is_beta = fit["beta"]
    except ValueError:
        is_beta = None
    bt["hedge_ratio_is"] = None if is_beta is None else round(is_beta, 4)
    if is_beta is not None and is_beta > 0:
        spread_is = [x - fit["alpha"] - is_beta * y for x, y in zip(la, lb)]
        z_is = st.zscore_series(spread_is, cfg["z_lookback"])
        bt["in_sample"]["spread_rule"] = spread_backtest(dates, pa, pb, z_is, is_beta, 0, n_is, cfg, fee,
                                                         borrow_pct_annual)
        bt["out_of_sample"]["spread_rule"] = spread_backtest(dates, pa, pb, z_is, is_beta, n_is, L, cfg, fee,
                                                             borrow_pct_annual)
        bt["in_sample"]["switch_rule"] = switch_backtest(dates, pa, pb, z_is, cfg["z_lookback"], n_is, cfg, fee)
        bt["out_of_sample"]["switch_rule"] = switch_backtest(dates, pa, pb, z_is, n_is, L, cfg, fee)
    else:
        bt["skipped"] = "hedge ratio <= 0 in-sample"
    out["backtest"] = bt
    # compact series for charts
    spread = [x - out["alpha"] - beta * y for x, y in zip(la, lb)]
    z = st.zscore_series(spread, cfg["z_lookback"])
    mp = cfg["max_series_points"]
    out["series"] = {"z": compact(dates, z, mp, 2), "spread": compact(dates, spread, mp, 4)}
    out["sparkline"] = sparkline(z[-mp:])
    out["ascii_chart"] = ascii_chart(z[-mp:])
    return out


# --------------------------------------------------------------------------
# Scan with multiple-testing correction
# --------------------------------------------------------------------------


def apply_multiple_testing(pairs: List[Dict], method: str, alpha: float, min_corr: float) -> int:
    """Adjust p-values over every pair that was actually tested; set coint_pass.

    Returns the number of tests m. Pairs without a test keep coint_pass False.
    """
    tested = [p for p in pairs if p.get("status") == "ok"]
    pv = [p["p_value"] for p in tested]
    if method == "none":
        adj = list(pv)
    elif method == "bonferroni":
        adj = st.bonferroni_adjust(pv)
    else:
        adj = st.bh_adjust(pv)
    for p, q in zip(tested, adj):
        p["p_adj"] = round(q, 4)
        p["tests"] = len(tested)
        p["coint_pass"] = q < alpha and bool(p.get("corr_ok")) and (p.get("hedge_ratio") or 0) > 0
    for p in pairs:
        if p.get("status") != "ok":
            p["coint_pass"] = False
    return len(tested)


def rank_key(p: Dict) -> Tuple:
    """Passing pairs first, then adjusted p, raw p, and |z| descending."""
    ok = p.get("status") == "ok"
    return (0 if p.get("coint_pass") else 1, p.get("p_adj", 1.0) if ok else 2.0,
            p.get("p_value", 1.0) if ok else 2.0, -abs(p.get("z") or 0.0))


# --------------------------------------------------------------------------
# Switch view (holder of A)
# --------------------------------------------------------------------------


def switch_view(p: Dict, cfg: Dict) -> Dict:
    """Plain verdict for a holder of A considering B."""
    if p.get("status") != "ok":
        return {"verdict": "insufficient_data"}
    z = p.get("z")
    entry = cfg["entry_z"]
    if z is None:
        verdict = "insufficient_data"
    elif abs(z) >= cfg["stop_z"]:
        verdict = "extreme_deviation"     # beyond the stop band: relationship may have broken
    elif z >= entry and p.get("coint_pass"):
        verdict = "b_relatively_cheap"
    elif z >= entry:
        verdict = "b_cheap_unstable"      # dislocation, but no reliable relationship
    elif z <= -entry:
        verdict = "b_relatively_expensive"
    else:
        verdict = "no_dislocation"
    return {"verdict": verdict, "z": z, "coint_pass": p.get("coint_pass"), "stability": p.get("stability")}


# --------------------------------------------------------------------------
# Signals (generic contract of gpw-signal-postmortem)
# --------------------------------------------------------------------------


def signals_for(pairs: List[Dict], cfg: Dict, raw_close: Optional[Callable[[str, str], Optional[float]]] = None
                ) -> List[Dict]:
    """One ``pair_dislocation`` signal per cointegrated pair with entry <= |z| < stop.

    ``direction`` is "long" for the cheap leg only (never a short); the date
    is the first session of the current dislocation episode, so re-running
    the report on later days repeats the same (deduplicated) signal.
    ``score`` = share of rolling 1-year windows that were cointegrated (0-100).
    """
    out = []
    seen = set()
    for p in pairs:
        if p.get("status") != "ok" or not p.get("coint_pass"):
            continue
        z = p.get("z")
        if z is None or abs(z) < cfg["entry_z"] or abs(z) >= cfg["stop_z"]:
            continue
        cheap, rich = (p["b"], p["a"]) if z > 0 else (p["a"], p["b"])
        date = p.get("episode_start") or p["as_of"]
        key = (cheap, date, rich)
        if key in seen:
            continue
        seen.add(key)
        sig = {"ticker": cheap, "date": date, "kind": "pair_dislocation", "direction": "long",
               "tags": {"pair": "{}/{}".format(p["a"], p["b"]), "rich_leg": rich, "z": p["z"],
                        "hedge_ratio": p["hedge_ratio"], "p_adj": p["p_adj"],
                        "half_life_days": p.get("half_life_days")}}
        if p.get("stability") is not None:
            sig["score"] = round(100.0 * p["stability"], 1)
        if raw_close:
            px = raw_close(cheap, date)
            if px:
                sig["price"] = px
        out.append(sig)
    return out


# --------------------------------------------------------------------------
# Futures availability
# --------------------------------------------------------------------------


def futures_for(ticker: str, rows: Optional[List[Dict]], parse: Callable[[str], Optional[Dict]]) -> Dict:
    """Single-stock futures series for ``ticker`` from one archive session (type 35)."""
    code = TICKER_TO_FUTURES.get(ticker)
    if rows is None:
        return {"code": code, "available": None, "series": []}
    series = []
    for r in rows:
        info = parse(r.get("name", ""))
        if info and info["underlying_code"] == code:
            series.append({"symbol": r["name"], "expiry": info["expiry"].isoformat(),
                           "close": r.get("close"), "turnover_k": r.get("turnover_k")})
    series.sort(key=lambda s: s["expiry"])
    return {"code": code, "available": bool(series), "series": series[:3],
            "turnover_k_total": round(sum((s["turnover_k"] or 0.0) for s in series), 2) if series else 0.0}
