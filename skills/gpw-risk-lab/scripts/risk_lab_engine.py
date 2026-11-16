"""gpw-risk-lab - pure engine (no I/O, no network).

Two jobs:

* **stress** - what today's portfolio would have lost in real GPW crises
  (historical replay of index windows, with a documented proxy chain for
  stocks that did not trade then) and under hypothetical factor shocks (WIG,
  a sector, the zloty, interest rates); liquidity under stress; comparison
  with the user's loss tolerance.
* **hedge** - how many FW20 (WIG20 futures) contracts offset the portfolio's
  market risk, what risk stays (non-WIG20 residual), basis, roll dates, a
  margin estimate and a one-year hedge-effectiveness backtest on a
  continuous futures series.

Every constant lives in ``THRESHOLDS`` with its justification, expanded in
``references/methodology.md``. Nothing here says "buy" or "sell".
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Callable, Dict, List, Optional, Sequence, Tuple

THRESHOLDS = {
    # One GPW year of daily returns for betas (the brief, and the usual 1y
    # window of commercial beta services). < 60 returns (~3 months) is too
    # noisy: such a position gets beta 1.0 against its proxy and is labelled.
    "lookback": 252,
    "min_obs": 60,
    "default_beta": 1.0,
    # Betas used to scale an index move into a stock move are clipped to
    # [0, 3]: a one-year OLS beta outside that range is almost always noise
    # (Blume 1971: betas regress towards 1) and would explode a stress number.
    "beta_clip": (0.0, 3.0),
    # Liquidity under stress: turnover halves and you sell at most 20% of a
    # day's volume (the brief; 20% participation is the upper end of the
    # 10-25% range used in execution practice; a 50% turnover drop matches
    # what GPW saw between 2007 and early 2009 and in March 2020 for small caps).
    "liquidity_window": 60,
    "participation": 0.20,
    "stress_turnover_factor": 0.50,
    "max_days_to_exit": 5.0,     # one trading week, same as gpw-portfolio-check
    "worst_positions": 3,
    # A historical window counts as covered by data only if the index has a
    # quote within 7 calendar days of both ends (one week of tolerance for
    # holidays / missing archive days).
    "window_tolerance_days": 7,
    # Stock window return: its last quote must be at most 5 sessions before
    # either end of the window, otherwise it was not trading (suspension,
    # not listed yet) and the proxy chain is used.
    "stale_quote_sessions": 5,
    # FW20: recommend the next series when the front one expires within 5
    # sessions (liquidity moves to the next series in the expiry week).
    "roll_window_sessions": 5,
    # Margin model (estimate only): 99% one-sided confidence, 2-day margin
    # period of risk, >= 12 months of data (EU Delegated Regulation 153/2013,
    # arts 24-26, for exchange-traded derivatives at a CCP); brokers charge
    # the investor more than the CCP minimum - 120% is an example taken from
    # a Polish broker's 2026 notice (initial = 120% of KDPW_CCP's basic risk level).
    "margin_confidence": 0.99,
    "margin_days": 2,
    "broker_margin_markup": 1.20,
}


class StressError(ValueError):
    """Bad scenario definition or input that makes a calculation impossible."""


# --------------------------------------------------------------------------
# Small statistics helpers
# --------------------------------------------------------------------------

def ols(y: Sequence[float], x: Sequence[float]) -> Optional[Dict]:
    """Simple regression y = a + b*x. Returns beta, alpha, r2, t-stat, corr, n."""
    n = min(len(y), len(x))
    if n < 3:
        return None
    y, x = list(y[-n:]), list(x[-n:])
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((v - mx) ** 2 for v in x)
    syy = sum((v - my) ** 2 for v in y)
    if sxx <= 0:
        return None
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    beta = sxy / sxx
    alpha = my - beta * mx
    sse = sum((b - alpha - beta * a) ** 2 for a, b in zip(x, y))
    r2 = 1 - sse / syy if syy > 0 else 0.0
    se = math.sqrt(sse / (n - 2) / sxx) if n > 2 and sse > 0 else 0.0
    t = beta / se if se > 0 else None
    corr = sxy / math.sqrt(sxx * syy) if syy > 0 else None
    return {"beta": beta, "alpha": alpha, "r2": max(0.0, min(1.0, r2)), "t": t, "corr": corr, "n": n,
            "resid_sd": math.sqrt(sse / (n - 2)) if n > 2 else None}


def _stdev(xs: Sequence[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((v - m) ** 2 for v in xs) / (len(xs) - 1))


def paired_returns(a: Dict[str, float], b: Dict[str, float], dates: Sequence[str]) -> Tuple[List[float], List[float], List[str]]:
    """Daily simple returns of two {date: close} maps on consecutive common dates of ``dates``."""
    common = [d for d in dates if d in a and d in b]
    ra, rb, ds = [], [], []
    for i in range(1, len(common)):
        p, c = common[i - 1], common[i]
        if a[p] > 0 and b[p] > 0:
            ra.append(a[c] / a[p] - 1)
            rb.append(b[c] / b[p] - 1)
            ds.append(c)
    return ra, rb, ds


def beta_of(stock: Dict[str, float], index: Dict[str, float], dates: Sequence[str], cfg: Optional[Dict] = None) -> Dict:
    """1-year beta of a stock against an index; falls back to default_beta when history is short."""
    cfg = dict(THRESHOLDS, **(cfg or {}))
    win = list(dates)[-(cfg["lookback"] + 1):]
    rs, ri, _ = paired_returns(stock, index, win)
    fit = ols(rs, ri) if len(rs) >= cfg["min_obs"] else None
    if fit is None:
        return {"beta": cfg["default_beta"], "raw_beta": None, "r2": None, "n": len(rs), "default": True}
    lo, hi = cfg["beta_clip"]
    return {"beta": round(min(hi, max(lo, fit["beta"])), 4), "raw_beta": round(fit["beta"], 4),
            "r2": round(fit["r2"], 4), "n": fit["n"], "default": False}


# --------------------------------------------------------------------------
# Historical windows
# --------------------------------------------------------------------------

def _last_on_or_before(dates: Sequence[str], day: str) -> Optional[int]:
    lo, hi, ans = 0, len(dates) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if dates[mid] <= day:
            ans, lo = mid, mid + 1
        else:
            hi = mid - 1
    return ans


def _days_between(a: str, b: str) -> int:
    return abs((dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days)


def resolve_window(spec: Dict, dates: Sequence[str], closes: Sequence[float], cfg: Optional[Dict] = None) -> Optional[Dict]:
    """Turn a scenario window spec into concrete session dates on the reference index.

    kinds: ``fixed`` (start close -> end close), ``peak_to_trough`` (highest
    close in [from, to], then the lowest close after it within the range),
    ``worst_days`` (the largest fall over ``days`` sessions in all data).
    Returns {start, end, index_return} or None when the data does not cover it.
    """
    cfg = dict(THRESHOLDS, **(cfg or {}))
    if not dates:
        return None
    tol = cfg["window_tolerance_days"]
    kind = spec.get("kind")
    if kind == "worst_days":
        n = int(spec.get("days", 1))
        if n < 1 or len(closes) <= n:
            return None
        best = None
        for i in range(n, len(closes)):
            if closes[i - n] > 0:
                r = closes[i] / closes[i - n] - 1
                if best is None or r < best[0]:
                    best = (r, i - n, i)
        if best is None:
            return None
        return {"start": dates[best[1]], "end": dates[best[2]], "index_return": best[0]}
    if kind == "fixed":
        s, e = spec["start"], spec["end"]
        i0, i1 = _last_on_or_before(dates, s), _last_on_or_before(dates, e)
        if i0 is None or i1 is None or i1 <= i0:
            return None
        if _days_between(dates[i0], s) > tol or _days_between(dates[i1], e) > tol:
            return None
        return {"start": dates[i0], "end": dates[i1], "index_return": closes[i1] / closes[i0] - 1}
    if kind == "peak_to_trough":
        f, t = spec["from"], spec["to"]
        idx = [i for i, d in enumerate(dates) if f <= d <= t]
        if not idx or _days_between(dates[idx[0]], f) > tol or _days_between(dates[idx[-1]], t) > tol:
            return None
        peak = max(idx, key=lambda i: (closes[i], -i))
        after = [i for i in idx if i > peak]
        if not after:
            return None
        trough = min(after, key=lambda i: (closes[i], i))
        return {"start": dates[peak], "end": dates[trough], "index_return": closes[trough] / closes[peak] - 1}
    raise StressError("unknown window kind {!r}".format(kind))


def window_return(series: Dict[str, float], start: str, end: str, ref_dates: Sequence[str],
                  cfg: Optional[Dict] = None) -> Optional[float]:
    """Return of one instrument between two session dates, None if it was not trading then.

    ``ref_dates`` are the session dates of the reference index (to count how
    stale a quote is)."""
    cfg = dict(THRESHOLDS, **(cfg or {}))
    if not series:
        return None
    sd = sorted(series)
    out = []
    for day in (start, end):
        i = _last_on_or_before(sd, day)
        if i is None:
            return None
        quote_day = sd[i]
        stale = sum(1 for d in ref_dates if quote_day < d <= day)
        if stale > cfg["stale_quote_sessions"]:
            return None
        out.append(series[quote_day])
    if out[0] <= 0:
        return None
    if sd[0] > start:
        return None
    return out[1] / out[0] - 1


def proxy_chain(sector: Optional[str], size: Optional[str]) -> List[Tuple[str, str]]:
    """Order of proxies for a position without its own history in a window."""
    chain = []
    if sector:
        chain.append(("proxy_sector", sector))
    if size:
        chain.append(("proxy_size", size))
    chain.append(("proxy_wig", "WIG"))
    return chain


def _clamp_ret(r: float) -> float:
    return max(-1.0, r)


def index_daily_returns(index: Dict[str, float], start: str, end: str) -> List[float]:
    """Daily returns of an index between two session dates (start close -> end close)."""
    ds = [d for d in sorted(index) if start <= d <= end]
    return [index[b] / index[a] - 1 for a, b in zip(ds, ds[1:]) if index[a] > 0]


def proxy_return(beta: float, total_return: float, daily: Optional[Sequence[float]] = None) -> float:
    """Stock return implied by an index move.

    With the index's daily returns the beta is applied day by day and
    compounded, prod(1 + beta * r_t) - 1, which is how a stock with that beta
    would actually have travelled the path; beta x total return is only the
    one-period approximation and overstates long windows (e.g. beta 1.7 x
    WIG20 -70% = -119%, while compounding gives about -90%)."""
    if daily:
        g = 1.0
        for r in daily:
            g *= max(0.0, 1 + beta * r)
        return _clamp_ret(g - 1)
    return _clamp_ret(beta * total_return)


def historical_position_returns(positions: List[Dict], stock_rets: Dict[str, Optional[float]],
                                index_rets: Dict[str, Optional[float]], betas: Dict[str, Dict[str, Dict]],
                                index_daily: Optional[Dict[str, List[float]]] = None) -> List[Dict]:
    """Return per position: actual window return, or proxy index return x beta
    (compounded daily when ``index_daily`` holds the index's daily returns).

    positions: {ticker, value, sector, size}; stock_rets: {ticker: return or None};
    index_rets: {index: return or None}; betas: {ticker: {index: beta_of(...) dict}}.
    """
    index_daily = index_daily or {}
    out = []
    for p in positions:
        r = stock_rets.get(p["ticker"])
        if r is not None:
            out.append({"ticker": p["ticker"], "return": r, "method": "actual", "proxy": None, "beta": None,
                        "proxy_return": None})
            continue
        rec = {"ticker": p["ticker"], "return": None, "method": "unavailable", "proxy": None, "beta": None,
               "proxy_return": None}
        for method, idx in proxy_chain(p.get("sector"), p.get("size")):
            ir = index_rets.get(idx)
            if ir is None:
                continue
            b = betas.get(p["ticker"], {}).get(idx, {"beta": THRESHOLDS["default_beta"], "default": True})
            rec.update({"return": proxy_return(b["beta"], ir, index_daily.get(idx)), "method": method, "proxy": idx,
                        "beta": b["beta"], "beta_default": bool(b.get("default")), "proxy_return": ir})
            break
        out.append(rec)
    return out


# --------------------------------------------------------------------------
# Factor sensitivities (FX, rates) from index data
# --------------------------------------------------------------------------

def _period_key(day: str, freq: str) -> str:
    d = dt.date.fromisoformat(day)
    if freq == "W":
        y, w, _ = d.isocalendar()
        return "{}-W{:02d}".format(y, w)
    if freq == "M":
        return day[:7]
    raise StressError("freq must be W or M")


def resample_last(series: Dict[str, float], freq: str) -> Dict[str, float]:
    """{date: value} -> {period: last value in period}."""
    out: Dict[str, Tuple[str, float]] = {}
    for d in sorted(series):
        out[_period_key(d, freq)] = (d, series[d])
    return {k: v[1] for k, v in out.items()}


def factor_sensitivity(index: Dict[str, float], factor: Dict[str, float], freq: str, factor_kind: str,
                       start: Optional[str] = None, end: Optional[str] = None, min_obs: int = 30) -> Optional[Dict]:
    """Regress index returns on factor changes per period.

    factor_kind 'pct' (FX: % change, so beta = index % per 1% EURPLN move) or
    'diff' (rates: change in percentage points, beta = index return per +1 pp).
    """
    def cut(s):
        return {d: v for d, v in s.items() if (start is None or d >= start) and (end is None or d <= end)}
    ix, fx = resample_last(cut(index), freq), resample_last(cut(factor), freq)
    keys = sorted(set(ix) & set(fx))
    y, x = [], []
    for a, b in zip(keys, keys[1:]):
        if ix[a] <= 0:
            continue
        y.append(ix[b] / ix[a] - 1)
        if factor_kind == "pct":
            if fx[a] <= 0:
                y.pop()
                continue
            x.append(fx[b] / fx[a] - 1)
        else:
            x.append(fx[b] - fx[a])
    if len(y) < min_obs:
        return None
    fit = ols(y, x)
    if fit is None:
        return None
    return {"beta": round(fit["beta"], 4), "r2": round(fit["r2"], 4),
            "t": round(fit["t"], 2) if fit["t"] is not None else None,
            "corr": round(fit["corr"], 4) if fit["corr"] is not None else None, "n": fit["n"],
            "start": keys[0], "end": keys[-1], "freq": freq,
            "significant": fit["t"] is not None and abs(fit["t"]) >= 2.0}


# --------------------------------------------------------------------------
# Factor scenarios
# --------------------------------------------------------------------------

def factor_position_returns(positions: List[Dict], spec: Dict, betas: Dict[str, Dict[str, Dict]],
                            sens: Dict[str, Dict[str, Optional[Dict]]]) -> List[Dict]:
    """Per-position returns under a hypothetical shock.

    spec kinds:
      market  {index: 'WIG', shock_pct}           r = beta(stock, index) x shock
      sector  {index, shock_pct, spillover}       members: beta(stock, sector) x shock;
                                                  others: 0 ('none') or beta x shock ('beta')
      fx      {shock_pct}  EURPLN % change (+ = weaker zloty)
      rates   {shock_bp}   change of the PL 10Y yield
              both:  r = beta(stock, proxy) x sensitivity(proxy) x shock, proxy = sector -> size -> WIG
    ``overrides`` {TICKER: return_pct} replace the model for named positions.
    """
    kind = spec.get("kind")
    overrides = {k.upper(): v for k, v in (spec.get("overrides") or {}).items()}
    out = []
    for p in positions:
        t = p["ticker"]
        rec = {"ticker": t, "return": None, "method": None, "proxy": None, "beta": None, "sensitivity": None}
        if t.upper() in overrides:
            rec.update({"return": _clamp_ret(float(overrides[t.upper()]) / 100.0), "method": "override"})
            out.append(rec)
            continue
        if kind == "market":
            idx = spec.get("index", "WIG")
            b = betas.get(t, {}).get(idx, {"beta": THRESHOLDS["default_beta"], "default": True})
            rec.update({"return": _clamp_ret(b["beta"] * spec["shock_pct"] / 100.0), "method": "beta_market",
                        "proxy": idx, "beta": b["beta"]})
        elif kind == "sector":
            idx = spec["index"]
            b = betas.get(t, {}).get(idx, {"beta": THRESHOLDS["default_beta"], "default": True})
            member = p.get("sector") == idx
            if member:
                rec.update({"return": _clamp_ret(b["beta"] * spec["shock_pct"] / 100.0), "method": "sector_member",
                            "proxy": idx, "beta": b["beta"]})
            elif spec.get("spillover", "none") == "beta":
                rec.update({"return": _clamp_ret(b["beta"] * spec["shock_pct"] / 100.0), "method": "sector_spillover",
                            "proxy": idx, "beta": b["beta"]})
            else:
                rec.update({"return": 0.0, "method": "not_in_sector"})
        elif kind in ("fx", "rates"):
            key = "fx" if kind == "fx" else "rates"
            shock = spec["shock_pct"] / 100.0 if kind == "fx" else spec["shock_bp"] / 100.0  # bp -> pp
            for _m, idx in proxy_chain(p.get("sector"), p.get("size")):
                s = (sens.get(idx) or {}).get(key)
                if s is None:
                    continue
                b = betas.get(t, {}).get(idx, {"beta": THRESHOLDS["default_beta"], "default": True})
                rec.update({"return": _clamp_ret(b["beta"] * s["beta"] * shock), "method": "factor_" + key,
                            "proxy": idx, "beta": b["beta"], "sensitivity": s["beta"],
                            "sensitivity_significant": s.get("significant")})
                break
            if rec["return"] is None:
                rec["method"] = "unavailable"
        else:
            raise StressError("unknown factor scenario kind {!r}".format(kind))
        out.append(rec)
    return out


# --------------------------------------------------------------------------
# Aggregation, liquidity, tolerance
# --------------------------------------------------------------------------

def aggregate(positions: List[Dict], pos_rets: List[Dict], cash: float, cfg: Optional[Dict] = None) -> Dict:
    """Portfolio return, PLN loss (positive = loss), contributions and worst positions."""
    cfg = dict(THRESHOLDS, **(cfg or {}))
    by = {r["ticker"]: r for r in pos_rets}
    total = sum(p["value"] for p in positions) + cash
    if total <= 0:
        raise StressError("portfolio value is zero")
    rows, pnl, missing = [], 0.0, []
    for p in positions:
        r = by.get(p["ticker"])
        if r is None or r["return"] is None:
            missing.append(p["ticker"])
            continue
        x = p["value"] * r["return"]
        pnl += x
        rows.append(dict(r, value=round(p["value"], 2), pnl_pln=round(x, 2),
                         return_pct=round(r["return"] * 100, 2),
                         contribution_pp=round(x / total * 100, 3)))
    rows.sort(key=lambda r: r["pnl_pln"])
    worst = [{"ticker": r["ticker"], "return_pct": r["return_pct"], "pnl_pln": r["pnl_pln"],
              "contribution_pp": r["contribution_pp"], "method": r["method"]}
             for r in rows[:cfg["worst_positions"]] if r["pnl_pln"] < 0]
    return {"portfolio_return_pct": round(pnl / total * 100, 2), "pln_loss": round(-pnl, 2),
            "pnl_pln": round(pnl, 2), "value_after": round(total + pnl, 2), "positions": rows,
            "worst_positions": worst, "missing": missing, "complete": not missing}


def liquidity_stress(positions: List[Dict], medians: Dict[str, Optional[float]], cfg: Optional[Dict] = None) -> Dict:
    """Days to exit each position at participation x (stress factor x median turnover)."""
    cfg = dict(THRESHOLDS, **(cfg or {}))
    rows = []
    total = sum(p["value"] for p in positions)
    for p in positions:
        med = medians.get(p["ticker"])
        normal_cap = (med or 0.0) * cfg["participation"]
        stress_cap = normal_cap * cfg["stress_turnover_factor"]
        rows.append({
            "ticker": p["ticker"], "value": round(p["value"], 2),
            "median_turnover_pln": round(med, 2) if med else None,
            "days_normal": round(p["value"] / normal_cap, 2) if normal_cap > 0 else None,
            "days_stress": round(p["value"] / stress_cap, 2) if stress_cap > 0 else None,
            "stress_capacity_pln_day": round(stress_cap, 2),
        })
    rows.sort(key=lambda r: -(r["days_stress"] if r["days_stress"] is not None else float("inf")))

    def sellable_within(days: float) -> float:
        got = 0.0
        for r in rows:
            got += min(r["value"], r["stress_capacity_pln_day"] * days)
        return round(got / total * 100, 2) if total > 0 else 0.0

    worst = [r["days_stress"] for r in rows]
    return {"positions": rows,
            "max_days_stress": None if any(d is None for d in worst) else (max(worst) if worst else 0.0),
            "sellable_1d_pct": sellable_within(1), "sellable_5d_pct": sellable_within(5),
            "illiquid": [r["ticker"] for r in rows if r["days_stress"] is None or r["days_stress"] > cfg["max_days_to_exit"]],
            "participation": cfg["participation"], "stress_turnover_factor": cfg["stress_turnover_factor"],
            "window": cfg["liquidity_window"]}


def tolerance_check(scenarios: List[Dict], max_loss_pct: Optional[float]) -> Optional[Dict]:
    """Which scenarios break the user's loss tolerance; equity share to move to cash to fit."""
    if max_loss_pct is None:
        return None
    breaches = []
    for s in scenarios:
        loss = -s["portfolio_return_pct"]
        if s.get("skipped") or loss <= max_loss_pct:
            continue
        breaches.append({"id": s["id"], "loss_pct": round(loss, 2),
                         # loss scales with the equity share (cash returns 0), so equity x (1-c) fits when
                         # c = 1 - tolerance / loss
                         "equity_reduction_to_fit_pct": round((1 - max_loss_pct / loss) * 100, 1)})
    breaches.sort(key=lambda b: -b["loss_pct"])
    return {"max_loss_pct": max_loss_pct, "breaches": breaches, "ok": not breaches}


# --------------------------------------------------------------------------
# Hedge: FW20 futures
# --------------------------------------------------------------------------

def last_trading_day(year: int, month: int, is_session: Callable[[dt.date], bool]) -> dt.date:
    """GPW WIG20 futures standard: 3rd Friday of the expiry month, or the last
    session before it if no session is held that Friday."""
    first = dt.date(year, month, 1)
    d = first + dt.timedelta(days=(4 - first.weekday()) % 7 + 14)
    guard = 0
    while not is_session(d):
        d -= dt.timedelta(days=1)
        guard += 1
        if guard > 15:
            raise StressError("no session found before the 3rd Friday of {}-{:02d}".format(year, month))
    return d


def sessions_between(a: dt.date, b: dt.date, is_session: Callable[[dt.date], bool]) -> int:
    """Sessions in (a, b]."""
    n, d = 0, a + dt.timedelta(days=1)
    while d <= b:
        if is_session(d):
            n += 1
        d += dt.timedelta(days=1)
    return n


def futures_chain(rows: List[Dict], as_of: dt.date, is_session: Callable[[dt.date], bool],
                  cfg: Optional[Dict] = None) -> List[Dict]:
    """Live FW20 series on ``as_of`` (rows already parsed with month/year/multiplier,
    e.g. by gpwlib.gpw_archive.futures_rows), nearest first, with last trading day,
    sessions to expiry, price (settlement, else close) and a recommendation flag."""
    cfg = dict(THRESHOLDS, **(cfg or {}))
    out = []
    for r in rows:
        ltd = last_trading_day(r["year"], r["month"], is_session)
        if ltd <= as_of:
            continue  # expired (or expiring today: the final settlement is fixed, nothing to hedge with)
        price = r.get("settlement") or r.get("close")
        if not price:
            continue
        out.append({"symbol": r["name"], "year": r["year"], "month": r["month"], "multiplier": r.get("multiplier"),
                    "last_trading_day": ltd.isoformat(), "sessions_to_expiry": sessions_between(as_of, ltd, is_session),
                    "price": price, "turnover_pln": round((r.get("turnover_k") or 0.0) * 1000, 2)})
    out.sort(key=lambda c: c["last_trading_day"])
    for i, c in enumerate(out):
        c["role"] = "front" if i == 0 else ("next" if i == 1 else "far")
    if out:
        rec = out[1] if len(out) > 1 and out[0]["sessions_to_expiry"] <= cfg["roll_window_sessions"] else out[0]
        rec["recommended"] = True
    return out


def contracts_needed(beta: float, value: float, price: float, multiplier: float) -> Dict:
    """N = beta x V / (F x multiplier); floor (never round up) and ceil, with achieved hedge ratios."""
    if price <= 0 or multiplier <= 0:
        raise StressError("futures price and multiplier must be positive")
    per = price * multiplier
    target = beta * value
    exact = target / per if per > 0 else 0.0
    fl, ce = int(math.floor(exact + 1e-9)), int(math.ceil(exact - 1e-9))
    return {"exact": round(exact, 3), "floor": fl, "ceil": ce, "notional_per_contract": round(per, 2),
            "target_notional": round(target, 2),
            "hedge_ratio_floor": round(fl * per / target, 4) if target > 0 else None,
            "hedge_ratio_ceil": round(ce * per / target, 4) if target > 0 else None,
            "below_one_contract": exact < 1.0}


def continuous_returns(sessions: List[Tuple[str, List[Dict]]], is_session: Callable[[dt.date], bool]) -> Dict[str, Dict]:
    """Daily returns of a continuous front-month series rolled at expiry.

    ``sessions``: [(date, parsed FW20 rows)] oldest first. On day t the
    contract is the nearest series whose last trading day is >= t (so the
    expiring series is held through its final settlement); the return uses
    the SAME series on t-1 and t, so the roll never creates an artificial
    price jump (this is the standard "ratio/return-chained" continuous series).
    Returns {date: {"ret", "symbol", "price"}}.
    """
    prev: Dict[str, float] = {}
    out: Dict[str, Dict] = {}
    for day, rows in sessions:
        d = dt.date.fromisoformat(day)
        prices = {}
        live = []
        for r in rows:
            p = r.get("settlement") or r.get("close")
            if not p:
                continue
            prices[r["name"]] = p
            ltd = last_trading_day(r["year"], r["month"], is_session)
            if ltd >= d:
                live.append((ltd, r["name"]))
        if live and prev:
            live.sort()
            sym = live[0][1]
            if sym in prev and prev[sym] > 0:
                out[day] = {"ret": prices[sym] / prev[sym] - 1, "symbol": sym, "price": prices[sym]}
        prev = prices
    return out


def roll_dates(chain_symbols: List[Tuple[str, int, int]], is_session: Callable[[dt.date], bool]) -> List[Dict]:
    """[(symbol, year, month)] -> expiry dates with the suggested roll window start."""
    out = []
    for sym, y, m in chain_symbols:
        ltd = last_trading_day(y, m, is_session)
        start = ltd
        n = 0
        while n < THRESHOLDS["roll_window_sessions"]:
            start -= dt.timedelta(days=1)
            if is_session(start):
                n += 1
        out.append({"symbol": sym, "last_trading_day": ltd.isoformat(), "roll_window_from": start.isoformat()})
    return out


def _curve_stats(rets: Sequence[float]) -> Dict:
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    sd = _stdev(rets)
    return {"total_return_pct": round((eq - 1) * 100, 2), "max_drawdown_pct": round(mdd * 100, 2),
            "volatility_pct": round(sd * math.sqrt(252) * 100, 2) if sd else None,
            "worst_day_pct": round(min(rets) * 100, 2) if rets else None}


def hedge_backtest(port: Dict[str, float], fut: Dict[str, float], ratios: Dict[str, float]) -> Optional[Dict]:
    """Unhedged vs hedged daily returns over the common dates.

    hedged r = r_portfolio - h x r_futures, with h = futures notional / portfolio
    value held constant (daily re-balanced notional ratio)."""
    dates = sorted(set(port) & set(fut))
    if len(dates) < 20:
        return None
    rp = [port[d] for d in dates]
    rf = [fut[d] for d in dates]
    res = {"start": dates[0], "end": dates[-1], "n": len(dates), "unhedged": _curve_stats(rp)}
    for name, h in ratios.items():
        res[name] = dict(_curve_stats([a - h * b for a, b in zip(rp, rf)]), hedge_ratio=round(h, 4))
    return res


def margin_estimate(fut_rets: Sequence[float], price: float, multiplier: float, cfg: Optional[Dict] = None) -> Optional[Dict]:
    """Model estimate of the initial margin per contract (NOT the broker's figure).

    99% quantile of 2-day absolute futures moves (overlapping) over the data,
    times the contract value; x broker markup for the investor's initial margin."""
    cfg = dict(THRESHOLDS, **(cfg or {}))
    k = cfg["margin_days"]
    if len(fut_rets) < 60 + k:
        return None
    moves = []
    for i in range(k, len(fut_rets) + 1):
        g = 1.0
        for r in fut_rets[i - k:i]:
            g *= 1 + r
        moves.append(abs(g - 1))
    moves.sort()
    q = moves[min(len(moves) - 1, int(math.ceil(cfg["margin_confidence"] * len(moves))) - 1)]
    per = price * multiplier
    return {"ccp_like_pct": round(q * 100, 2), "ccp_like_pln": round(q * per, 2),
            "investor_initial_pct": round(q * cfg["broker_margin_markup"] * 100, 2),
            "investor_initial_pln": round(q * cfg["broker_margin_markup"] * per, 2),
            "observations": len(moves)}


def portfolio_beta(weights: Dict[str, float], betas: Dict[str, float]) -> float:
    """Value-weighted beta (weights of the equity part sum to 1)."""
    return sum(w * betas.get(t, THRESHOLDS["default_beta"]) for t, w in weights.items())


def residual_risk(port_rets: Sequence[float], index_rets: Sequence[float]) -> Optional[Dict]:
    """Share of portfolio variance NOT explained by the index (1 - R^2) and its annualised volatility."""
    fit = ols(port_rets, index_rets)
    if fit is None:
        return None
    sd = _stdev(port_rets) or 0.0
    return {"beta": round(fit["beta"], 4), "r2": round(fit["r2"], 4),
            "residual_risk_pct": round((1 - fit["r2"]) * 100, 2),
            "residual_vol_pct": round(sd * math.sqrt(max(0.0, 1 - fit["r2"])) * math.sqrt(252) * 100, 2),
            "total_vol_pct": round(sd * math.sqrt(252) * 100, 2), "n": fit["n"]}
