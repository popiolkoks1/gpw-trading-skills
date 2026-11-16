"""GPW Backtester - performance metrics, benchmarks and regime split (pure).

Conventions: daily simple returns (time-weighted, external contributions
removed), 252 sessions per year (gpwlib.stats.SESSIONS_PER_YEAR), CAGR over
calendar time. Percent values carry a ``_pct`` suffix or are listed in
``PCT_FIELDS``; ratios (Sharpe, Sortino, MAR, profit factor, R) are unitless.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Dict, List, Optional, Sequence

from gpwlib.indicators import sma
from gpwlib.stats import SESSIONS_PER_YEAR

import backtester_strategies as bs

PCT_FIELDS = ("cagr", "max_dd", "win_rate", "exposure", "volatility", "total_return")


def _years(d0: str, d1: str) -> float:
    return max(1e-9, (dt.date.fromisoformat(d1) - dt.date.fromisoformat(d0)).days / 365.25)


def moments(rets: Sequence[float]) -> Dict:
    n = len(rets)
    if n < 3:
        return {"n": n, "mean": None, "sd": None, "skew": None, "kurt": None}
    m = sum(rets) / n
    var = sum((r - m) ** 2 for r in rets) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return {"n": n, "mean": m, "sd": 0.0, "skew": 0.0, "kurt": 3.0}
    m2 = sum((r - m) ** 2 for r in rets) / n
    m3 = sum((r - m) ** 3 for r in rets) / n
    m4 = sum((r - m) ** 4 for r in rets) / n
    return {"n": n, "mean": m, "sd": sd, "skew": m3 / m2 ** 1.5, "kurt": m4 / m2 ** 2}


def sharpe(rets: Sequence[float], rf_daily: float = 0.0, annualize: bool = True) -> Optional[float]:
    mo = moments([r - rf_daily for r in rets])
    if not mo["sd"]:
        return None
    s = mo["mean"] / mo["sd"]
    return s * math.sqrt(SESSIONS_PER_YEAR) if annualize else s


def index_from(rets: Sequence[float]) -> List[float]:
    out, v = [1.0], 1.0
    for r in rets:
        v *= 1 + r
        out.append(v)
    return out


def drawdown(idx: Sequence[float], dates: Sequence[str]) -> Dict:
    peak, peak_k = idx[0], 0
    mdd, mdd_peak, mdd_trough = 0.0, 0, 0
    longest, long_from, long_to = 0, None, None
    for k, v in enumerate(idx):
        if v >= peak:
            if k - peak_k > longest:
                longest, long_from, long_to = k - peak_k, dates[peak_k], dates[k]
            peak, peak_k = v, k
        dd = v / peak - 1 if peak > 0 else 0.0
        if dd < mdd:
            mdd, mdd_peak, mdd_trough = dd, peak_k, k
    if len(idx) - 1 - peak_k > longest:  # still under water at the end
        longest, long_from, long_to = len(idx) - 1 - peak_k, dates[peak_k], None
    return {"max_dd": mdd * 100.0, "peak": dates[mdd_peak], "trough": dates[mdd_trough],
            "longest_underwater_sessions": longest, "longest_from": long_from, "longest_to": long_to,
            "current_dd": (idx[-1] / max(idx) - 1) * 100.0}


def perf(dates: Sequence[str], rets: Sequence[float], rf_pct: float = 0.0) -> Dict:
    """Performance of a daily return stream; ``dates`` has one more element than ``rets``."""
    idx = index_from(rets)
    yrs = _years(dates[0], dates[-1])
    tr = idx[-1] - 1
    cagr = ((idx[-1]) ** (1 / yrs) - 1) * 100.0 if idx[-1] > 0 else -100.0
    rf_d = rf_pct / 100.0 / SESSIONS_PER_YEAR
    mo = moments(rets)
    sd = mo["sd"]
    vol = sd * math.sqrt(SESSIONS_PER_YEAR) * 100.0 if sd is not None else None
    sh = sharpe(rets, rf_d)
    down = [min(0.0, r - rf_d) for r in rets]
    dd_dev = math.sqrt(sum(x * x for x in down) / len(down)) if down else 0.0
    sortino = ((mo["mean"] - rf_d) / dd_dev * math.sqrt(SESSIONS_PER_YEAR)) if dd_dev and mo["mean"] is not None else None
    dd = drawdown(idx, list(dates))
    t_stat = (mo["mean"] / (sd / math.sqrt(len(rets)))) if sd else None
    return {
        "cagr": cagr, "total_return": tr * 100.0, "volatility": vol, "sharpe": sh, "sortino": sortino,
        "max_dd": dd["max_dd"], "mar": (cagr / abs(dd["max_dd"])) if dd["max_dd"] < 0 else None,
        "max_dd_peak": dd["peak"], "max_dd_trough": dd["trough"],
        "longest_underwater_sessions": dd["longest_underwater_sessions"],
        "longest_underwater_from": dd["longest_from"], "longest_underwater_to": dd["longest_to"],
        "t_stat": t_stat, "skew": mo["skew"], "kurtosis": mo["kurt"], "sessions": len(rets), "years": yrs,
    }


def trade_stats(trades: List[Dict]) -> Dict:
    n = len(trades)
    if not n:
        return {"trades": 0, "win_rate": None, "avg_win_pct": None, "avg_loss_pct": None, "avg_win_pln": None,
                "avg_loss_pln": None, "expectancy_pln": None, "expectancy_r": None, "trades_with_r": 0,
                "profit_factor": None, "avg_sessions": None, "max_consecutive_losses": 0, "exit_reasons": {}}
    wins = [t for t in trades if t["pnl_pln"] > 0]
    losses = [t for t in trades if t["pnl_pln"] <= 0]
    gw = sum(t["pnl_pln"] for t in wins)
    gl = -sum(t["pnl_pln"] for t in losses)
    rs = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    streak = best = 0
    for t in sorted(trades, key=lambda x: (x["exit_date"], x["name"])):
        streak = streak + 1 if t["pnl_pln"] <= 0 else 0
        best = max(best, streak)
    reasons: Dict[str, int] = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1

    def avg(xs):
        return sum(xs) / len(xs) if xs else None
    return {
        "trades": n, "win_rate": len(wins) / n * 100.0,
        "avg_win_pct": avg([t["ret_pct"] for t in wins]), "avg_loss_pct": avg([t["ret_pct"] for t in losses]),
        "avg_win_pln": avg([t["pnl_pln"] for t in wins]), "avg_loss_pln": avg([t["pnl_pln"] for t in losses]),
        "expectancy_pln": sum(t["pnl_pln"] for t in trades) / n,
        "expectancy_r": avg(rs), "trades_with_r": len(rs),
        "profit_factor": (gw / gl) if gl > 0 else None,
        "avg_sessions": avg([t["sessions"] for t in trades]),
        "max_consecutive_losses": best, "exit_reasons": reasons,
    }


def concentration(trades: List[Dict]) -> Dict:
    """How much of the total P&L comes from the best trades (outlier dependence)."""
    pnl = sorted((t["pnl_pln"] for t in trades), reverse=True)
    total = sum(pnl)
    if not pnl:
        return {"total_pnl_pln": 0.0, "top1_share_pct": None, "top3_share_pct": None, "pnl_ex_top3_pln": None}
    return {"total_pnl_pln": round(total, 2),
            "top1_share_pct": pnl[0] / total * 100.0 if total > 0 else None,
            "top3_share_pct": sum(pnl[:3]) / total * 100.0 if total > 0 else None,
            "pnl_ex_top3_pln": round(total - sum(x for x in pnl[:3] if x > 0), 2)}


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------
def wig_returns(md, days: Sequence[str]) -> List[float]:
    """WIG close-to-close returns over ``days`` (0 on a day the index is missing)."""
    c = [wig_close_on(md, d) for d in days]
    return [c[k] / c[k - 1] - 1 if c[k] and c[k - 1] else 0.0 for k in range(1, len(days))]


def equal_weight_returns(md, days: Sequence[str], min_price: float, min_turnover_k: float) -> List[float]:
    """Gross daily mean return of all eligible shares (price, 20-session median
    turnover), rebalanced daily, no costs. Survivorship-free because the
    universe on each day is that day's archive snapshot."""
    pos = {d: k for k, d in enumerate(days)}
    sums = [0.0] * len(days)
    cnt = [0] * len(days)
    mt = min_turnover_k * 1000.0
    for b in md.stocks.values():
        med = bs.median_s(b, 20)
        for i in range(1, len(b)):
            k = pos.get(b.dates[i])
            if k is None or k == 0 or b.dates[i - 1] != days[k - 1]:
                continue
            j = i - 1
            if med[j] is None or med[j] < mt or b.close[j] < min_price:
                continue
            sums[k] += b.close[i] / b.close[j] - 1
            cnt[k] += 1
    return [sums[k] / cnt[k] if cnt[k] else 0.0 for k in range(1, len(days))]


def wig_close_on(md, d: str) -> Optional[float]:
    if md.wig is None:
        return None
    i = md.wig.on_or_before(d)
    return md.wig.close[i] if i is not None else None


# ---------------------------------------------------------------------------
# Regimes
# ---------------------------------------------------------------------------
def proxy_regime(md, days: Sequence[str]) -> Dict[str, str]:
    """WIG close vs its SMA200: 'above_sma200' / 'below_sma200' (a proxy, not gpw-market-regime)."""
    w = md.wig
    out = {}
    if w is None:
        return out
    s200 = sma(w.close, 200)
    for d in days:
        i = w.on_or_before(d)
        if i is None or s200[i] is None:
            continue
        out[d] = "above_sma200" if w.close[i] > s200[i] else "below_sma200"
    return out


def regime_from_report(doc: Dict, days: Sequence[str]) -> Dict[str, str]:
    """Daily states rebuilt from a gpw-market-regime report's history (state_before + transitions)."""
    h = (doc or {}).get("history") or {}
    start, state = h.get("from"), h.get("state_before")
    trans = sorted(h.get("transitions") or [], key=lambda x: x.get("date", ""))
    as_of = ((doc or {}).get("regime") or {}).get("as_of") or (doc or {}).get("as_of")
    if not start:
        return {}
    out, k = {}, 0
    for d in days:
        if d < start or (as_of and d > as_of):
            continue
        while k < len(trans) and trans[k]["date"] <= d:
            state = trans[k].get("to")
            k += 1
        if state:
            out[d] = state
    return out


def by_regime(days: Sequence[str], rets: Sequence[float], exposure: Sequence[float], wig_rets: Sequence[float],
              labels: Dict[str, str]) -> List[Dict]:
    groups: Dict[str, Dict] = {}
    for k in range(1, len(days)):
        lab = labels.get(days[k - 1])  # regime known at the previous close
        if lab is None:
            continue
        g = groups.setdefault(lab, {"state": lab, "sessions": 0, "r": [], "w": [], "x": []})
        g["sessions"] += 1
        g["r"].append(rets[k - 1])
        g["w"].append(wig_rets[k - 1])
        g["x"].append(exposure[k - 1])
    out = []
    for g in groups.values():
        n = g["sessions"]
        out.append({"state": g["state"], "sessions": n,
                    "strategy_ann_return_pct": (sum(g["r"]) / n) * SESSIONS_PER_YEAR * 100.0,
                    "wig_ann_return_pct": (sum(g["w"]) / n) * SESSIONS_PER_YEAR * 100.0,
                    "avg_exposure_pct": sum(g["x"]) / n * 100.0,
                    "strategy_sharpe": sharpe(g["r"])})
    return sorted(out, key=lambda x: -x["sessions"])


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
def by_year(days: Sequence[str], rets: Sequence[float], wig: Sequence[float], ew: Sequence[float],
            trades: List[Dict], tax_years: List[Dict]) -> List[Dict]:
    acc: Dict[str, Dict] = {}
    for k in range(1, len(days)):
        y = days[k][:4]
        a = acc.setdefault(y, {"s": 1.0, "w": 1.0, "e": 1.0, "n": 0})
        a["s"] *= 1 + rets[k - 1]
        a["w"] *= 1 + wig[k - 1]
        a["e"] *= 1 + ew[k - 1]
        a["n"] += 1
    taxes = {str(t["year"]): t for t in tax_years}
    out = []
    for y in sorted(acc):
        a = acc[y]
        ty = [t for t in trades if t["exit_date"][:4] == y]
        tx = taxes.get(y, {})
        out.append({"year": int(y), "sessions": a["n"], "return_pct": (a["s"] - 1) * 100.0,
                    "wig_pct": (a["w"] - 1) * 100.0, "equal_weight_pct": (a["e"] - 1) * 100.0,
                    "trades_closed": len(ty), "realized_pln": round(sum(t["pnl_pln"] for t in ty), 2),
                    "tax_pln": tx.get("tax", 0.0), "loss_deducted_pln": tx.get("loss_deducted", 0.0)})
    return out


def monthly_curve(days: Sequence[str], equity: Sequence[float], md) -> List[Dict]:
    out = []
    peak = None
    w0 = wig_close_on(md, days[0])
    for k, d in enumerate(days):
        peak = equity[k] if peak is None else max(peak, equity[k])
        if k + 1 == len(days) or days[k + 1][:7] != d[:7]:
            w = wig_close_on(md, d)
            out.append({"date": d, "equity": round(equity[k], 2),
                        "drawdown_pct": round((equity[k] / peak - 1) * 100.0, 2) if peak else 0.0,
                        "wig_index": round(w / w0 * 100.0, 2) if w and w0 else None})
    return out
