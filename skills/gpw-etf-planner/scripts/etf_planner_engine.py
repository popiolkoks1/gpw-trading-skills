"""Pure engine for gpw-etf-planner (stdlib only, no I/O, no network).

* ``etf_stats``      - last close, 20-session median turnover, 1-year return,
                       volatility and max drawdown of one ETF series;
* ``plan_buys``      - whole-unit buy list that moves a portfolio toward its
                       target weights using new money only (cash-flow
                       rebalancing, never sells), with broker minimum fees;
* ``order_frequency``- how often to buy so one order costs <= MAX_FEE_DRAG_PCT;
* ``rebalance_check``- 5/25 threshold bands;
* ``portfolio_risk`` - historical volatility / drawdown of the target mix and
                       its equity / bonds-cash / currency split;
* ``backtest``       - monthly DCA vs lump sum on the available history.

All thresholds are justified in references/methodology.md.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Dict, List, Optional, Sequence

from gpwlib import brokers, stats
from gpwlib.indicators import median

import etf_catalog as cat

# A single order should not cost more than 0.5% of its value: at that level the
# fee alone equals a full year of TER for a typical index ETF (0.5%), so paying
# more per order doubles the yearly cost of a buy-and-hold investor.
MAX_FEE_DRAG_PCT = 0.5
MAX_FREQUENCY_MONTHS = 3  # beyond a quarter, cash idles too long - consider fewer ETFs / cheaper broker

# Rebalancing bands, Larry Swedroe's "5/25 rule" (see also Vanguard, Jaconetti,
# Kinniry & Zilbering 2010): rebalance when a holding drifts 5 pp from its
# target, or 25% of the target for small allocations - whichever band is tighter.
BAND_ABS_PP = 5.0
BAND_REL = 0.25

# Liquidity of GPW ETFs (median daily turnover over 20 sessions). Every GPW ETF
# has a market maker quoting both sides, but thin turnover goes with wider
# spreads. 100k zł/day ~ the level of mid-sized mWIG40 shares; < 20k zł/day
# means a few trades a day, so use limit orders only.
LIQ_LOW_K = 100.0
LIQ_VERY_LOW_K = 20.0
TURNOVER_WINDOW = 20

ONE_YEAR = stats.SESSIONS_PER_YEAR
MIN_SESSIONS_VOL = 60      # < ~3 months of data: volatility is not meaningful
SHORT_HISTORY_YEARS = 10   # a full market cycle; anything shorter is anecdotal


# ---------------------------------------------------------------------------
# Single-ETF statistics
# ---------------------------------------------------------------------------

def liquidity_label(median_turnover_k: Optional[float]) -> str:
    if median_turnover_k is None:
        return "unknown"
    if median_turnover_k < LIQ_VERY_LOW_K:
        return "very_low"
    if median_turnover_k < LIQ_LOW_K:
        return "low"
    return "ok"


def etf_stats(dates: Sequence[str], closes: Sequence[float], turnover_k: Sequence[float]) -> Dict:
    n = len(closes)
    if n == 0:
        return {"sessions": 0}
    last_turn = [t for t in turnover_k[-TURNOVER_WINDOW:]]
    med = median(last_turn) if last_turn else None
    r1y = closes[-1] / closes[-ONE_YEAR - 1] - 1 if n > ONE_YEAR and closes[-ONE_YEAR - 1] > 0 else None
    window = closes[-(ONE_YEAR + 1):]
    vol = stats.volatility(window) if len(window) > MIN_SESSIONS_VOL else None
    mdd = stats.max_drawdown(list(closes)) if n > 1 else {"mdd": 0.0, "current": 0.0, "peak_i": 0, "trough_i": 0}
    return {
        "sessions": n,
        "first_date": dates[0],
        "last_date": dates[-1],
        "last_close": closes[-1],
        "median_turnover_k": round(med, 2) if med is not None else None,
        "liquidity": liquidity_label(med),
        "return_1y": r1y,
        "return_since_start": stats.total_return(list(closes)),
        "volatility": vol,
        "max_drawdown": mdd["mdd"],
        "drawdown_now": mdd["current"],
        "mdd_peak_date": dates[mdd["peak_i"]] if n > 1 else None,
        "mdd_trough_date": dates[mdd["trough_i"]] if n > 1 else None,
    }


# ---------------------------------------------------------------------------
# Allocation / buy list
# ---------------------------------------------------------------------------

def normalise_weights(alloc: Dict[str, float]) -> Dict[str, float]:
    if not alloc:
        raise ValueError("allocation is empty")
    if any(v < 0 for v in alloc.values()):
        raise ValueError("allocation weights must be >= 0")
    tot = sum(alloc.values())
    if tot <= 0:
        raise ValueError("allocation weights must sum to > 0")
    return {k: v / tot for k, v in alloc.items()}


def _cost(units: int, price: float, pct: float, min_fee: float) -> float:
    v = units * price
    return v + brokers.commission(v, pct, min_fee)


def plan_buys(prices: Dict[str, float], weights: Dict[str, float], holdings: Dict[str, float],
              cash: float, pct: float, min_fee: float, max_orders: Optional[int] = None) -> Dict:
    """Whole-unit buys that move the portfolio toward ``weights`` with ``cash`` only.

    1. Target value of each ETF = weight x (holdings value + cash).
    2. Cash goes to the underweight ETFs in proportion to their shortfall
       (if cash exceeds all shortfalls, the rest is split by target weight).
    3. Each allocation is rounded DOWN to whole units including the fee.
    4. Leftover cash buys one more unit at a time of the most underweight ETF,
       while affordable and while the ETF is short of target by more than half
       a unit (rounding to the nearest unit, never far overweight).

    ``max_orders`` (e.g. 1) limits the buys to the N most underweight ETFs -
    the "rotation" variant that pays one minimum fee instead of several.
    """
    if cash < 0:
        raise ValueError("cash must be >= 0")
    for k in weights:
        if k not in prices or prices[k] is None or prices[k] <= 0:
            raise ValueError("no price for {}".format(k))
    names = list(weights)
    values = {k: holdings.get(k, 0.0) * prices[k] for k in names}
    other = sum(holdings.get(k, 0.0) * prices[k] for k in holdings if k not in weights and k in prices)
    total = sum(values.values()) + cash + other
    target = {k: weights[k] * total for k in names}
    gaps = {k: max(0.0, target[k] - values[k]) for k in names}
    buyable = names
    if max_orders is not None and max_orders < len(names):
        if max_orders < 1:
            raise ValueError("max_orders must be >= 1")
        buyable = sorted(names, key=lambda k: (-gaps[k], -weights[k]))[:max_orders]
    gsum = sum(gaps[k] for k in buyable)
    wsum = sum(weights[k] for k in buyable)
    alloc = {k: 0.0 for k in names}
    for k in buyable:
        if gsum <= 0:
            alloc[k] = cash * weights[k] / wsum if wsum > 0 else 0.0
        elif gsum >= cash:
            alloc[k] = cash * gaps[k] / gsum
        else:
            alloc[k] = gaps[k] + (cash - gsum) * (weights[k] / wsum if wsum > 0 else 0.0)
    units = {}
    for k in names:
        u = int(alloc[k] // prices[k]) if alloc[k] > 0 else 0
        while u > 0 and _cost(u, prices[k], pct, min_fee) > alloc[k] + 1e-9:
            u -= 1
        units[k] = u
    spent = sum(_cost(units[k], prices[k], pct, min_fee) for k in names)
    left = cash - spent
    while True:
        best, best_def = None, 0.0
        for k in buyable:
            extra = _cost(units[k] + 1, prices[k], pct, min_fee) - _cost(units[k], prices[k], pct, min_fee)
            deficit = target[k] - (values[k] + units[k] * prices[k])
            if extra <= left + 1e-9 and deficit > prices[k] / 2 and deficit > best_def:
                best, best_def = k, deficit
        if best is None:
            break
        left -= _cost(units[best] + 1, prices[best], pct, min_fee) - _cost(units[best], prices[best], pct, min_fee)
        units[best] += 1
    rows = []
    after_total = total - sum(brokers.commission(units[k] * prices[k], pct, min_fee) for k in names)
    for k in names:
        bv = units[k] * prices[k]
        fee = brokers.commission(bv, pct, min_fee)
        new_val = values[k] + bv
        rows.append({
            "name": k, "price": prices[k], "units_held": holdings.get(k, 0.0), "value_before": round(values[k], 2),
            "weight_before": values[k] / (total - cash) if total - cash > 0 else 0.0,
            "target_weight": weights[k], "target_value": round(target[k], 2),
            "buy_units": units[k], "buy_value": round(bv, 2), "fee": fee,
            "fee_drag_pct": round(fee / bv * 100, 3) if bv > 0 else None,
            "value_after": round(new_val, 2),
            "weight_after": new_val / after_total if after_total > 0 else 0.0,
        })
    fees = sum(r["fee"] for r in rows)
    return {"rows": rows, "cash_in": round(cash, 2), "spent": round(cash - left, 2), "cash_left": round(left, 2),
            "fees": round(fees, 2), "portfolio_before": round(total - cash, 2), "portfolio_after": round(after_total - left, 2),
            "other_holdings_value": round(other, 2)}


def order_frequency(monthly: float, weights: Dict[str, float], pct: float, min_fee: float,
                    max_drag_pct: float = MAX_FEE_DRAG_PCT) -> Dict:
    """Months to accumulate before buying each ETF so one order costs <= max_drag_pct."""
    per = {}
    impossible = pct > max_drag_pct
    for k, w in weights.items():
        a = monthly * w
        months = None
        for m in range(1, 13):
            v = a * m
            if v > 0 and brokers.commission(v, pct, min_fee) / v * 100 <= max_drag_pct + 1e-9:
                months = m
                break
        per[k] = {"monthly_amount": round(a, 2), "months": months,
                  "drag_monthly_pct": round(brokers.commission(a, pct, min_fee) / a * 100, 3) if a > 0 else None}
    worst = [v["months"] for v in per.values()]
    overall = None if (impossible or any(m is None for m in worst)) else max(worst)
    one = brokers.commission(monthly, pct, min_fee) / monthly * 100 if monthly > 0 else None
    min_order = (min_fee / (max_drag_pct / 100.0)) if (min_fee > 0 and not impossible) else 0.0
    return {"per_etf": per, "suggested_every_months": overall,
            "too_infrequent": overall is not None and overall > MAX_FREQUENCY_MONTHS,
            "impossible": impossible, "rotation_one_order_drag_pct": round(one, 3) if one is not None else None,
            "min_order_value": round(min_order, 2), "max_drag_pct": max_drag_pct}


def rebalance_check(values: Dict[str, float], weights: Dict[str, float]) -> Dict:
    """5/25 bands: band = min(5 pp, 25% of the target weight)."""
    tot = sum(values.get(k, 0.0) for k in weights)
    rows = []
    for k, t in weights.items():
        w = values.get(k, 0.0) / tot if tot > 0 else 0.0
        band = min(BAND_ABS_PP, BAND_REL * t * 100)
        drift = (w - t) * 100
        rows.append({"name": k, "weight": w, "target": t, "drift_pp": round(drift, 2), "band_pp": round(band, 2),
                     "out_of_band": tot > 0 and abs(drift) > band + 1e-9})
    return {"rows": rows, "any_out_of_band": any(r["out_of_band"] for r in rows), "portfolio_value": round(tot, 2)}


# ---------------------------------------------------------------------------
# Portfolio risk
# ---------------------------------------------------------------------------

def portfolio_index(closes: Dict[str, List[float]], weights: Dict[str, float]) -> List[float]:
    """Daily-rebalanced portfolio index (1.0 at start) from aligned closes."""
    names = list(weights)
    n = min(len(closes[k]) for k in names)
    idx = [1.0]
    for i in range(1, n):
        r = sum(weights[k] * (closes[k][i] / closes[k][i - 1] - 1) for k in names if closes[k][i - 1] > 0)
        idx.append(idx[-1] * (1 + r))
    return idx


def composition(weights: Dict[str, float]) -> Dict:
    eq = safe = other = lev = oki = 0.0
    currency: Dict[str, float] = {}
    cats: Dict[str, float] = {}
    for k, w in weights.items():
        c = cat.classify(k)
        es = c["equity_share"]
        if c["category"] in cat.SAFE_CATEGORIES:
            safe += w
        elif es is not None and es > 0:
            eq += w * es
            if c["category"] == "mixed":
                safe += w * (1 - es)
        else:
            other += w
        if c["is_leveraged_or_short"]:
            lev += w
        if c["oki_hint"] == "likely_qualified" and not c["is_leveraged_or_short"]:
            oki += w
        currency[c["currency"]] = currency.get(c["currency"], 0.0) + w
        cats[c["category"]] = cats.get(c["category"], 0.0) + w
    return {"equity": eq, "bonds_cash": safe, "other": other, "leveraged_or_short": lev,
            "oki_likely_qualified": oki, "currency": currency, "categories": cats}


def portfolio_risk(dated_closes: Dict[str, Dict[str, float]], weights: Dict[str, float]) -> Dict:
    """Risk of the target mix on the dates all its ETFs have in common."""
    al = stats.align({k: dated_closes[k] for k in weights})
    dates = al["dates"]
    out = {"common_sessions": len(dates), "start": dates[0] if dates else None, "end": dates[-1] if dates else None,
           "composition": composition(weights)}
    if len(dates) < 2:
        out.update(volatility=None, max_drawdown=None, total_return=None)
        return out
    idx = portfolio_index(al["closes"], weights)
    mdd = stats.max_drawdown(idx)
    out.update(volatility=stats.volatility(idx) if len(idx) > MIN_SESSIONS_VOL else None,
               max_drawdown=mdd["mdd"], mdd_peak=dates[mdd["peak_i"]], mdd_trough=dates[mdd["trough_i"]],
               total_return=stats.total_return(idx), years=round((len(dates) - 1) / ONE_YEAR, 2))
    return out


# ---------------------------------------------------------------------------
# Backtest: monthly DCA vs lump sum
# ---------------------------------------------------------------------------

def first_sessions_of_month(dates: Sequence[str]) -> List[int]:
    out, prev = [], None
    for i, d in enumerate(dates):
        if d[:7] != prev:
            out.append(i)
            prev = d[:7]
    return out


def xirr(flows: List[tuple]) -> Optional[float]:
    """Annualised money-weighted return of [(date, amount)] (negative = paid in), bisection."""
    if not flows or not any(a > 0 for _, a in flows) or not any(a < 0 for _, a in flows):
        return None
    d0 = flows[0][0]

    def npv(r):
        return sum(a / (1 + r) ** ((d - d0).days / 365.0) for d, a in flows)

    lo, hi = -0.99, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f = npv(mid)
        if abs(f) < 1e-7:
            break
        if (f > 0) == (f_lo > 0):
            lo, f_lo = mid, f
        else:
            hi = mid
    return (lo + hi) / 2


def backtest(dated_closes: Dict[str, Dict[str, float]], weights: Dict[str, float], monthly: float,
             pct: float, min_fee: float, max_orders: Optional[int] = None) -> Dict:
    al = stats.align({k: dated_closes[k] for k in weights})
    dates, closes = al["dates"], al["closes"]
    if len(dates) < 40:
        raise ValueError("not enough common history for a backtest ({} sessions)".format(len(dates)))
    buy_days = first_sessions_of_month(dates)
    if dates[0][8:] > "05" and len(buy_days) > 1:
        # history starts mid-month: start on the first full month so DCA and lump sum share the start
        buy_days = buy_days[1:]
    start = buy_days[0]
    holdings = {k: 0 for k in weights}
    cash = 0.0
    invested = 0.0
    fees = 0.0
    orders = 0
    flows = []
    for i in buy_days:
        cash += monthly
        invested += monthly
        flows.append((dt.date.fromisoformat(dates[i]), -monthly))
        prices = {k: closes[k][i] for k in weights}
        p = plan_buys(prices, weights, holdings, cash, pct, min_fee, max_orders)
        orders += sum(1 for r in p["rows"] if r["buy_units"] > 0)
        for r in p["rows"]:
            holdings[r["name"]] += r["buy_units"]
        cash = p["cash_left"]
        fees += p["fees"]
    last = {k: closes[k][-1] for k in weights}
    dca_value = sum(holdings[k] * last[k] for k in weights) + cash
    flows.append((dt.date.fromisoformat(dates[-1]), dca_value))
    # lump sum: the same total amount on the first buy day
    p0 = plan_buys({k: closes[k][start] for k in weights}, weights, {}, invested, pct, min_fee)
    ls_hold = {r["name"]: r["buy_units"] for r in p0["rows"]}
    ls_value = sum(ls_hold[k] * last[k] for k in weights) + p0["cash_left"]
    d_start, d_end = dt.date.fromisoformat(dates[start]), dt.date.fromisoformat(dates[-1])
    years = (d_end - d_start).days / 365.0
    ls_ann = (ls_value / invested) ** (1 / years) - 1 if years > 0 and invested > 0 and ls_value > 0 else None
    return {
        "start": dates[start], "end": dates[-1], "years": round(years, 2), "months": len(buy_days),
        "invested": round(invested, 2),
        "dca": {"value": round(dca_value, 2), "gain": round(dca_value - invested, 2),
                "gain_pct": (dca_value / invested - 1) * 100, "fees": round(fees, 2), "orders": orders,
                "cash_left": round(cash, 2), "units": holdings, "money_weighted_return": xirr(flows)},
        "lump_sum": {"value": round(ls_value, 2), "gain": round(ls_value - invested, 2),
                     "gain_pct": (ls_value / invested - 1) * 100, "fees": p0["fees"], "units": ls_hold,
                     "annualised_return": ls_ann},
        "too_short": years < SHORT_HISTORY_YEARS,
    }
