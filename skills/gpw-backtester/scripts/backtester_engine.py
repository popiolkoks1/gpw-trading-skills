"""GPW Backtester - event-driven daily simulation engine (pure, no I/O).

Timing (no look-ahead)
----------------------
* Signals use data up to the CLOSE of session t.
* Orders fill at the OPEN of session t+1 (the next session in which the share
  has a bar). The adjusted open is estimated as raw_open * adj_close / raw_close
  (see backtester_data.py).
* No bar on t+1 (no trade, suspension, halt at a static price threshold) means
  no fill: a pending exit is retried the next session, a pending entry expires.
* Stops are checked on each session's LOW. If the session OPENS at or below the
  stop the fill is the open (gap-through), otherwise the stop price. A position
  entered at today's open is also checked against today's low (we cannot know
  the intraday order of events, so we assume the worse case).
* Sells are executed before buys on the same open; sale proceeds can be used at
  once (Polish brokers allow trading on unsettled T+2 cash).

Costs
-----
* Commission per order from gpwlib.brokers (percentage with a minimum fee, so
  small orders pay relatively more).
* Slippage = half-spread proxy by liquidity bucket (median 20-session turnover)
  + optional square-root market impact  coef * sigma_daily * sqrt(order / ADV)
  (Almgren et al. 2005; Toth et al. 2011). ``cost_mult`` scales slippage and
  impact (stress test 2x); commissions are a known tariff and are not scaled.
* Liquidity cap: an entry order is at most ``max_adv_pct`` % of the median
  20-session turnover (default 10%, the gpw-position-sizer default).

Tax (account="regular")
-----------------------
Belka tax 19% (art. 30b ust. 1 ustawy o PIT) on the yearly net realised result
(proceeds - cost - both commissions; art. 30b ust. 2, art. 23 ust. 1 pkt 38),
rounded to full zloty (art. 63 par. 1 Ordynacji podatkowej). A yearly loss is
carried forward 5 years (art. 9 ust. 3 ustawy o PIT): ``loss_rule="pl_2019"``
(default, law since 2019) allows one one-time deduction of up to 5 000 000 zl
in one of those years, the rest at most 50% of the loss per year;
``loss_rule="50pct"`` is the stricter pre-2019 rule. Tax for year Y is paid on
the first session on or after 30 April of Y+1 (PIT-38 deadline, art. 45 ust. 1 and 1a)
and is accrued daily as a liability, so the equity curve is after tax.
Dividends are inside the adjusted prices, so their tax is approximated through
the capital-gains tax on the adjusted gain (see methodology). account="ike":
no tax (art. 21 ust. 1 pkt 58a ustawy o PIT).
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Dict, List, Optional, Tuple

from gpwlib import brokers
from gpwlib.tax import BELKA_RATE

import backtester_strategies as bs

# Half-spread proxies in bps by median 20-session turnover (PLN). WIG20 names
# trade at 1-2 tick spreads (~5-10 bps full), sWIG80 names at 20-50 bps,
# micro caps at 100-200 bps+. Values are deliberately on the conservative side.
SLIPPAGE_BUCKETS = [(5_000_000.0, 5.0), (1_000_000.0, 10.0), (200_000.0, 25.0), (50_000.0, 50.0), (0.0, 100.0)]
IMPACT_COEF = 0.5        # square-root law prefactor Y ~ 0.5-1 (Toth et al. 2011); 0.5 = mid estimate for small orders
MAX_SLIPPAGE_BPS = 1000.0  # 10% = GPW static price threshold for most shares; a fill cannot be worse
LOSS_CARRY_YEARS = 5     # art. 9 ust. 3 ustawy o PIT
LOSS_MAX_SHARE = 0.5     # ... at most 50% of the loss in any year
LOSS_ONE_TIME_CAP = 5_000_000.0  # ... or once up to 5 mln zl (art. 9 ust. 3 pkt 2, since 1.1.2019)
TAX_PAY_MONTH_DAY = "04-30"  # PIT-38 deadline (art. 45 ust. 1 ustawy o PIT)

DEFAULT_CONFIG = {
    "capital": 50_000.0,
    "commission_pct": 0.39,   # mBank preset; the CLI resolves --broker via gpwlib.brokers
    "commission_min": 5.0,
    "account": "regular",     # regular | ike
    "sizing": "equal",        # equal | risk
    "risk_pct": 1.0,          # risk per trade in % of equity (fixed-fractional, like gpw-position-sizer)
    "max_position_pct": 25.0,  # cap per position in risk mode
    "max_adv_pct": 10.0,      # gpw-position-sizer default
    "impact_coef": IMPACT_COEF,
    "cost_mult": 1.0,
    "costs": True,
    "cash_rate_pct": 0.0,     # yearly rate on idle cash (e.g. ~WIBOR minus margin); 0 = conservative
    "loss_rule": "pl_2019",
}


def slippage_bps(med_turn: Optional[float], order_value: float, sigma: Optional[float], cfg: Dict) -> float:
    if not cfg["costs"]:
        return 0.0
    mt = med_turn or 0.0
    base = next(b for lo, b in SLIPPAGE_BUCKETS if mt >= lo)
    impact = 0.0
    if cfg["impact_coef"] and sigma and mt > 0 and order_value > 0:
        impact = cfg["impact_coef"] * sigma * math.sqrt(order_value / mt) * 1e4
    return min(MAX_SLIPPAGE_BPS, (base + impact) * cfg["cost_mult"])


def fee(value: float, cfg: Dict) -> float:
    if not cfg["costs"]:
        return 0.0
    return brokers.commission(value, cfg["commission_pct"], cfg["commission_min"])


def round_zl(x: float) -> float:
    """Ordynacja podatkowa art. 63 par. 1: < 50 gr dropped, >= 50 gr rounded up."""
    return float(math.floor(x + 0.5))


# ---------------------------------------------------------------------------
# Tax ledger
# ---------------------------------------------------------------------------
class TaxLedger:
    def __init__(self, rule: str = "pl_2019", enabled: bool = True):
        if rule not in ("pl_2019", "50pct"):
            raise ValueError("loss_rule must be pl_2019 or 50pct")
        self.rule = rule
        self.enabled = enabled
        self.realized: Dict[int, float] = {}
        self.losses: List[Dict] = []  # {year, amount, remaining, one_time_used}
        self.years: List[Dict] = []

    def add(self, year: int, pnl: float) -> None:
        self.realized[year] = self.realized.get(year, 0.0) + pnl

    def _deduct(self, year: int, income: float, mutate: bool) -> Tuple[float, List[Dict]]:
        left, used = income, []
        for lv in self.losses:
            if left <= 0:
                break
            if not (year - LOSS_CARRY_YEARS <= lv["year"] < year) or lv["remaining"] <= 0:
                continue
            cap50 = LOSS_MAX_SHARE * lv["amount"]
            d50 = min(lv["remaining"], cap50 - lv["used_by_year"].get(year, 0.0), left)
            d = d50
            one_time = False
            if self.rule == "pl_2019" and not lv["one_time_used"]:
                d1 = min(lv["remaining"], LOSS_ONE_TIME_CAP, left)
                if d1 > d50:
                    d, one_time = d1, True
            if d <= 0:
                continue
            left -= d
            used.append({"loss_year": lv["year"], "deducted": round(d, 2), "one_time": one_time})
            if mutate:
                lv["remaining"] -= d
                lv["used_by_year"][year] = lv["used_by_year"].get(year, 0.0) + d
                if one_time:
                    lv["one_time_used"] = True
        return income - left, used

    def tax_for(self, year: int, mutate: bool = False) -> Dict:
        income = self.realized.get(year, 0.0)
        if not self.enabled:
            return {"year": year, "net_realized": round(income, 2), "loss_deducted": 0.0, "base": 0.0, "tax": 0.0, "deductions": []}
        if income <= 0:
            if mutate and income < 0:
                self.losses.append({"year": year, "amount": -income, "remaining": -income, "one_time_used": False,
                                    "used_by_year": {}})
            return {"year": year, "net_realized": round(income, 2), "loss_deducted": 0.0, "base": 0.0, "tax": 0.0,
                    "deductions": []}
        ded, used = self._deduct(year, income, mutate)
        base = round_zl(max(0.0, income - ded))
        tax = round_zl(base * BELKA_RATE)
        return {"year": year, "net_realized": round(income, 2), "loss_deducted": round(ded, 2), "base": base,
                "tax": tax, "deductions": used}

    def close_year(self, year: int) -> Dict:
        r = self.tax_for(year, mutate=True)
        self.years.append(r)
        return r

    def carry_forward(self, year: int) -> List[Dict]:
        return [{"loss_year": lv["year"], "remaining": round(lv["remaining"], 2),
                 "expires_after": lv["year"] + LOSS_CARRY_YEARS}
                for lv in self.losses if lv["remaining"] > 0.005 and lv["year"] + LOSS_CARRY_YEARS >= year]


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
class Position:
    __slots__ = ("name", "shares", "entry_price", "entry_date", "entry_cal", "entry_fee", "stop", "init_stop",
                 "high_close", "last_close", "entry_slip", "init_risk")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _proxy_bars(md, ter_pct: float):
    """Synthetic WIG fund: unit = WIG / 1000, minus TER accrued per session."""
    from backtester_data import Bars
    w = md.wig
    if w is None:
        raise ValueError("WIG series missing - dca_buy_hold needs the index archive")
    k = (1 - ter_pct / 100.0) ** (1 / 252.0)
    f, o, h, l, c = [], [], [], [], []
    acc = 1.0
    for i in range(len(w)):
        if i:
            acc *= k
        o.append(w.open[i] / 1000.0 * acc)
        h.append(w.high[i] / 1000.0 * acc)
        l.append(w.low[i] / 1000.0 * acc)
        c.append(w.close[i] / 1000.0 * acc)
    return Bars(bs.WIG_PROXY, list(w.dates), o, h, l, c, [max(t, 1e9) for t in w.turn])


def simulate(md, strategy, start: str, end: str, config: Optional[Dict] = None, prepared: bool = False) -> Dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(config or {})
    if cfg["account"] not in ("regular", "ike"):
        raise ValueError("account must be regular or ike")
    if cfg["sizing"] not in ("equal", "risk"):
        raise ValueError("sizing must be equal or risk")
    if cfg["capital"] <= 0:
        raise ValueError("capital must be > 0")
    if not prepared:
        strategy.prepare(md)
    days = md.window(start, end)
    if len(days) < 2:
        raise ValueError("fewer than 2 sessions between {} and {}".format(start, end))
    p = strategy.params
    nslots = p["max_positions"]
    tax_on = cfg["account"] == "regular"
    ledger = TaxLedger(cfg["loss_rule"], enabled=tax_on)
    extra = {}
    if strategy.kind == "dca":
        inst = strategy.instrument()
        if inst == bs.WIG_PROXY:
            extra[inst] = _proxy_bars(md, p["ter_pct"])
        elif inst not in md.stocks:
            raise ValueError("ticker {!r} not found in the share archive".format(inst))

    def bars(name):
        return extra.get(name) or md.stocks.get(name)

    cash = float(cfg["capital"])
    positions: Dict[str, Position] = {}
    pending_exit: Dict[str, str] = {}
    pending_entries: List[Dict] = []
    pending_rebal: Optional[List[str]] = None
    last_exit: Dict[str, int] = {}
    trades: List[Dict] = []
    stats = {"commissions": 0.0, "slippage": 0.0, "traded_value": 0.0, "entries_skipped_no_quote": 0,
             "entries_skipped_cash": 0, "entries_capped_liquidity": 0, "exits_delayed_no_quote": 0,
             "gap_stop_exits": 0, "orders": 0, "interest": 0.0, "min_fee_orders": 0}
    eq_dates, eq_vals, exposure, flows = [], [], [], []
    prior_unpaid = 0.0
    tax_paid: Dict[int, float] = {}
    cur_liab = 0.0
    liab_dirty = False
    year = int(days[0][:4])
    pay_due: Optional[Tuple[str, int, float]] = None
    cal_index = md.cal_pos

    def do_fee(value):
        f = fee(value, cfg)
        if cfg["costs"] and f > 0 and value * cfg["commission_pct"] / 100.0 < cfg["commission_min"]:
            stats["min_fee_orders"] += 1
        return f

    def sell(pos: Position, b, i: int, ref: float, date: str, reason: str):
        nonlocal cash, liab_dirty
        mt = bs.median_s(b, 20)[i - 1] if i > 0 else None
        sg = bs.vol_s(b, 20)[i - 1] if i > 0 else None
        value_ref = pos.shares * ref
        bps = slippage_bps(mt, value_ref, sg, cfg)
        px = ref * (1 - bps / 1e4)
        proceeds = pos.shares * px
        f = do_fee(proceeds)
        cash += proceeds - f
        cost = pos.shares * pos.entry_price
        pnl = proceeds - f - cost - pos.entry_fee
        stats["commissions"] += f
        stats["slippage"] += value_ref - proceeds
        stats["traded_value"] += proceeds
        stats["orders"] += 1
        ledger.add(int(date[:4]), pnl)
        liab_dirty = True
        r_mult = pnl / pos.init_risk if pos.init_risk and pos.init_risk > 0 else None
        trades.append({
            "name": pos.name, "entry_date": pos.entry_date, "entry_price": round(pos.entry_price, 4),
            "exit_date": date, "exit_price": round(px, 4), "shares": pos.shares,
            "fees": round(pos.entry_fee + f, 2), "slippage_pln": round(pos.entry_slip + value_ref - proceeds, 2),
            "pnl_pln": round(pnl, 2), "ret_pct": round((proceeds - f) / (cost + pos.entry_fee) * 100 - 100, 3),
            "r_multiple": round(r_mult, 3) if r_mult is not None else None,
            "init_stop": round(pos.init_stop, 4) if pos.init_stop else None,
            "sessions": cal_index[date] - pos.entry_cal, "reason": reason,
        })
        del positions[pos.name]
        last_exit[pos.name] = cal_index[date]

    def buy(name: str, b, i: int, date: str, shares: int, sig_i: Optional[int]) -> bool:
        nonlocal cash
        ref = b.open[i]
        j = sig_i if sig_i is not None else max(0, i - 1)
        mt, sg = bs.median_s(b, 20)[j], bs.vol_s(b, 20)[j]
        bps = slippage_bps(mt, shares * ref, sg, cfg)
        px = ref * (1 + bps / 1e4)
        # fit into cash (commission included)
        while shares > 0 and shares * px + fee(shares * px, cfg) > cash + 1e-9:
            shares = min(shares - 1, int((cash - fee(shares * px, cfg)) // px)) if px > 0 else 0
        if shares < 1:
            stats["entries_skipped_cash"] += 1
            return False
        value = shares * px
        f = do_fee(value)
        cash -= value + f
        stats["commissions"] += f
        stats["slippage"] += shares * (px - ref)
        stats["traded_value"] += value
        stats["orders"] += 1
        stop = strategy.stop_for(b, j, px) if strategy.kind == "signal" or p.get("stop_pct") else None
        if stop is not None and stop >= px:
            stop = None
        init_risk = None
        if stop is not None:
            init_risk = shares * (px - stop) + f + fee(shares * stop, cfg)
        positions[name] = Position(name=name, shares=shares, entry_price=px, entry_date=date, entry_cal=cal_index[date],
                                   entry_fee=f, stop=stop, init_stop=stop, high_close=b.close[i], last_close=b.close[i],
                                   entry_slip=shares * (px - ref), init_risk=init_risk)
        return True

    def size_entry(name: str, b, i: int, equity: float) -> int:
        px = b.close[i]
        if px <= 0:
            return 0
        n = nslots
        if cfg["sizing"] == "risk" and strategy.kind == "signal":
            stop = strategy.stop_for(b, i, px)
            if stop is None or stop >= px:
                raise ValueError("risk sizing needs a strategy with a stop (stop_pct / stop_atr)")
            budget = equity * cfg["risk_pct"] / 100.0
            sh = int(budget // (px - stop))
            while sh > 0 and sh * (px - stop) + fee(sh * px, cfg) + fee(sh * stop, cfg) > budget:
                sh -= 1
            sh = min(sh, int(equity * cfg["max_position_pct"] / 100.0 // px))
        else:
            sh = int((equity / n) // px)
        mt = bs.median_s(b, 20)[i]
        if mt is not None and cfg["max_adv_pct"] is not None and name in md.stocks:
            cap = int(mt * cfg["max_adv_pct"] / 100.0 // px)
            if cap < sh:
                stats["entries_capped_liquidity"] += 1
                sh = cap
        return max(0, sh)

    def mark(date):
        mv = 0.0
        for pos in positions.values():
            b = bars(pos.name)
            i = b.at(date)
            if i is not None:
                pos.last_close = b.close[i]
            mv += pos.shares * pos.last_close
        return mv

    def liability(cur_year):
        nonlocal cur_liab, liab_dirty
        if not tax_on:
            return 0.0
        if liab_dirty:
            cur_liab = ledger.tax_for(cur_year)["tax"]
            liab_dirty = False
        return cur_liab

    dca_months = set()
    dca_due = False
    for k, date in enumerate(days):
        flow = 0.0
        y = int(date[:4])
        if y != year:  # close the previous tax year
            r = ledger.close_year(year)
            if tax_on and r["tax"] > 0:
                pay_due = ("{}-{}".format(y, TAX_PAY_MONTH_DAY), year, r["tax"])
                prior_unpaid += r["tax"]
            year = y
            liab_dirty = True
        if pay_due and date >= pay_due[0]:
            cash -= pay_due[2]
            prior_unpaid -= pay_due[2]
            tax_paid[pay_due[1]] = tax_paid.get(pay_due[1], 0.0) + pay_due[2]
            pay_due = None
        if k > 0 and cfg["cash_rate_pct"] and cash > 0:
            it = cash * cfg["cash_rate_pct"] / 100.0 / 252.0
            if tax_on:
                it *= 1 - BELKA_RATE  # interest: 19% withheld at source (art. 30a ust. 1 pkt 3)
            cash += it
            stats["interest"] += it

        # ---- open: exits -------------------------------------------------
        for name in list(positions):
            pos = positions[name]
            b = bars(name)
            i = b.at(date)
            if i is None:
                if name in pending_exit:
                    stats["exits_delayed_no_quote"] += 1
                continue
            if name in pending_exit:
                reason = pending_exit.pop(name)
                if pos.stop is not None and b.open[i] <= pos.stop:
                    reason = "gap_stop"
                    stats["gap_stop_exits"] += 1
                sell(pos, b, i, b.open[i], date, reason)
            elif pos.stop is not None and pos.entry_date != date:
                if b.open[i] <= pos.stop:
                    stats["gap_stop_exits"] += 1
                    sell(pos, b, i, b.open[i], date, "gap_stop")
                elif b.low[i] <= pos.stop:
                    sell(pos, b, i, pos.stop, date, "stop")
        for name in list(pending_exit):
            if name not in positions:
                pending_exit.pop(name)

        # ---- open: entries -----------------------------------------------
        if strategy.kind == "dca":
            inst = strategy.instrument()
            b = bars(inst)
            month = date[:7]
            if month not in dca_months:  # new month: contribution arrives, a buy becomes due
                if k > 0 and p["monthly"] > 0:
                    cash += p["monthly"]
                    flow = p["monthly"]
                dca_months.add(month)
                dca_due = True
            i = b.at(date)
            if i is not None and dca_due:
                dca_due = False
                px_est = b.open[i] * (1 + slippage_bps(1e12, cash, None, cfg) / 1e4)
                sh = int(cash // px_est) if px_est > 0 else 0
                if sh > 0:
                    old = positions.pop(inst, None)
                    if buy(inst, b, i, date, sh, None) and old is not None:
                        # add to the holding: average cost (one instrument held to the end, so FIFO = average)
                        new = positions[inst]
                        tot = old.shares + new.shares
                        new.entry_price = (old.shares * old.entry_price + new.shares * new.entry_price) / tot
                        new.entry_fee += old.entry_fee
                        new.entry_slip += old.entry_slip
                        new.shares = tot
                        new.entry_date, new.entry_cal = old.entry_date, old.entry_cal
                    elif old is not None:
                        positions[inst] = old
        elif strategy.kind == "rotation" and pending_rebal is not None:
            pending_rebal = None
            for e in pending_entries:
                b = md.stocks[e["name"]]
                i = b.at(date)
                if i is None:
                    stats["entries_skipped_no_quote"] += 1
                    continue
                if e["name"] in positions:
                    continue
                buy(e["name"], b, i, date, e["shares"], e["sig_i"])
            pending_entries = []
        else:
            for e in pending_entries:
                if len(positions) >= nslots:
                    break
                b = md.stocks[e["name"]]
                i = b.at(date)
                if i is None:
                    stats["entries_skipped_no_quote"] += 1
                    continue
                if e["name"] in positions:
                    continue
                if buy(e["name"], b, i, date, e["shares"], e["sig_i"]):
                    pos = positions[e["name"]]
                    if pos.stop is not None and b.low[i] <= pos.stop:  # same-day stop (worst case)
                        sell(pos, b, i, pos.stop, date, "stop")
            pending_entries = []

        # ---- close: mark, trail, signals ------------------------------------
        mv = mark(date)
        liab = liability(year) + prior_unpaid
        equity = cash + mv - liab
        eq_dates.append(date)
        eq_vals.append(equity)
        flows.append(flow)
        exposure.append(mv / (cash + mv) if cash + mv > 0 else 0.0)
        if k == len(days) - 1:
            break
        trail = strategy.trail_pct()
        mh = strategy.max_hold()
        for name, pos in positions.items():
            b = bars(name)
            i = b.at(date)
            if i is None:
                continue
            if b.close[i] > pos.high_close:
                pos.high_close = b.close[i]
            if trail:
                ts = pos.high_close * (1 - trail / 100.0)
                pos.stop = ts if pos.stop is None else max(pos.stop, ts)
            if strategy.kind == "signal" and strategy.exit_signal(name, i):
                pending_exit[name] = "signal"
            elif mh and cal_index[date] - pos.entry_cal >= mh:
                pending_exit[name] = "max_hold"
        if strategy.kind == "signal":
            free = nslots - (len(positions) - len(pending_exit))
            pending_entries = []
            if free > 0:
                ci = cal_index[date]
                for _, name in strategy.entry_candidates(date):
                    if len(pending_entries) >= free:
                        break
                    if name in positions:
                        continue
                    if name in last_exit and ci - last_exit[name] < p["cooldown"]:
                        continue
                    b = md.stocks[name]
                    i = b.at(date)
                    sh = size_entry(name, b, i, equity)
                    if sh >= 1:
                        pending_entries.append({"name": name, "shares": sh, "sig_i": i})
        elif strategy.kind == "rotation":
            tg = strategy.rebalance_targets(date, held=sorted(positions))
            if tg is not None:
                pending_rebal = tg
                for name in positions:
                    if name not in tg:
                        pending_exit[name] = "rebalance"
                # value per new slot: equity / top_n
                pending_entries = []
                for name in tg:
                    if name in positions:
                        continue
                    b = md.stocks[name]
                    i = b.at(date)
                    if i is None:
                        continue
                    sh = size_entry(name, b, i, equity)
                    if sh >= 1:
                        pending_entries.append({"name": name, "shares": sh, "sig_i": i})

    # ---- end: liquidate at the last close (with costs) and settle the tax ----
    last = days[-1]
    for name in list(positions):
        pos = positions[name]
        b = bars(name)
        i = b.on_or_before(last)
        sell(pos, b, i, b.close[i], last, "end_of_test")
    r_final = ledger.close_year(year)
    final_tax_due = (r_final["tax"] if tax_on else 0.0) + prior_unpaid
    for yy, amt in ([(year, r_final["tax"])] if tax_on and r_final["tax"] > 0 else []):
        tax_paid[yy] = tax_paid.get(yy, 0.0) + amt
    if pay_due:
        tax_paid[pay_due[1]] = tax_paid.get(pay_due[1], 0.0) + pay_due[2]
    final_equity = cash - final_tax_due
    eq_vals[-1] = final_equity
    exposure[-1] = 0.0 if not exposure else exposure[-1]
    rets = []
    for t in range(1, len(eq_vals)):
        prev = eq_vals[t - 1]
        rets.append((eq_vals[t] - flows[t]) / prev - 1 if prev > 0 else 0.0)
    return {
        "dates": eq_dates, "equity": eq_vals, "returns": rets, "flows": flows, "exposure": exposure,
        "trades": trades, "stats": stats, "tax_years": ledger.years, "tax_paid": tax_paid,
        "carry_forward": ledger.carry_forward(year + 1), "final_equity": final_equity,
        "contributions": sum(flows), "config": cfg, "strategy": strategy.name, "params": dict(p),
    }
