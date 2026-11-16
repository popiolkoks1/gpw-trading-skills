"""gpw-pre-trade-gate - pure engine (no I/O, no network).

A fail-closed checklist evaluated BEFORE a long entry on GPW. Every check
returns PASS / WARN / FAIL (or SKIP when an optional input is absent) with a
reason key and parameters; the CLI translates them. Verdict:

* NO-GO               - at least one FAIL
* GO WITH CONDITIONS  - no FAIL, at least one WARN
* GO                  - everything PASS / SKIP / INFO / UNKNOWN

Since 1.1.0 the caller may pass ``ctx["context"]`` (reports of the v1.2
skills); then seven context checks from ``pretrade_context_checks`` are
appended (statuses also INFO and UNKNOWN, which never change the verdict).
Without that key the 13 core checks run exactly as in 1.0.0.

Thresholds live in ``DEFAULTS`` and are justified in references/gate_rules.md.
The gate never places orders and never says "buy".
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Tuple

from gpwlib import brokers

import pretrade_context_checks as cc

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
UNKNOWN, INFO = cc.UNKNOWN, cc.INFO
STATUSES = (PASS, WARN, FAIL, SKIP, UNKNOWN, INFO)

DEFAULTS = {
    "max_risk_pct": 1.0,        # Van Tharp / O'Neil-style fixed-fractional 1% per trade
    "max_position_pct": 25.0,   # one position <= 1/4 of the account (same as gpw-position-sizer)
    "max_adv_pct": 10.0,        # order <= 10% of median daily turnover (gpw-position-sizer default)
    "turnover_window": 20,      # ~1 month of sessions, as in gpw-position-sizer
    "max_heat_pct": 6.0,        # total open risk to stops <= 6% of the account (Elder's 6% rule)
    "max_sector_pct": 35.0,     # same limit as gpw-portfolio-check
    "min_rr": 1.5,              # reward/risk below 1.5R = WARN ...
    "fail_rr": 1.0,             # ... below 1.0R = FAIL (risking more than the planned reward)
    "cb_warn_losses": 3,        # 3 consecutive losses -> halve size
    "cb_fail_losses": 5,        # 5 consecutive losses -> pause
    "cb_fail_dd_pct": 10.0,     # realised drawdown >= 10% of peak equity -> pause
    "regime_fail_top_risk": 80, # bear market with top-risk score >= 80 -> FAIL without override
    "regime_warn_top_risk": 80, # any regime with top-risk >= 80 -> at least WARN
    "max_input_age_sessions": 3,  # regime / exposure JSON older than 3 sessions = stale (common brief)
    "entry_deviation_pct": 10.0,  # entry > 10% away from last close = likely typo (GPW static threshold is 10%)
    "plan_stop_tolerance_pct": 0.5,  # gate stop vs journal plan stop
    "new_positions_window_days": 7,
}

REGIME_STATES = ("confirmed_uptrend", "uptrend_under_pressure", "rally_attempt", "correction", "bear")


class GateError(ValueError):
    """Invalid trade input (exit code 1)."""


def _chk(_cid: str, _status: str, _key: str, **params) -> Dict:
    return {"id": _cid, "status": _status, "reason": _key, "params": params}


# --------------------------------------------------------------------------
# Trade arithmetic
# --------------------------------------------------------------------------

def trade_numbers(trade: Dict) -> Dict:
    """Value, fees and risk of buying ``shares`` at entry and exiting at stop / target."""
    sh, e, s = trade["shares"], trade["entry"], trade.get("stop")
    pct_, mn = trade["fee_pct"], trade["fee_min"]
    value = sh * e
    fee_in = brokers.commission(value, pct_, mn)
    out = {"value": round(value, 2), "fee_entry": fee_in}
    if s is not None and s < e:
        fee_out = brokers.commission(sh * s, pct_, mn)
        out.update(price_risk=round(sh * (e - s), 2), fee_exit_stop=fee_out,
                   risk_total=round(sh * (e - s) + fee_in + fee_out, 2),
                   stop_distance_pct=round((e - s) / e * 100, 2))
    tgt = trade.get("target")
    if tgt is not None and "risk_total" in out:
        fee_t = brokers.commission(sh * tgt, pct_, mn)
        reward = sh * (tgt - e) - fee_in - fee_t
        out.update(reward_net=round(reward, 2), fee_exit_target=fee_t,
                   rr=round(reward / out["risk_total"], 2) if out["risk_total"] > 0 else None)
    return out


def validate_trade(trade: Dict) -> None:
    if not trade.get("ticker"):
        raise GateError("ticker is required")
    for k in ("entry", "account_size"):
        if trade.get(k) is None or trade[k] <= 0:
            raise GateError("{} must be a positive number".format(k))
    if not isinstance(trade.get("shares"), int) or trade["shares"] <= 0:
        raise GateError("shares must be a positive whole number (GPW lot = 1)")
    if trade.get("target") is not None and trade["target"] <= trade["entry"]:
        raise GateError("target must be above entry for a long trade")


# --------------------------------------------------------------------------
# Journal helpers
# --------------------------------------------------------------------------

def find_thesis(theses: List[Dict], ticker: str, thesis_id: Optional[str]) -> Tuple[Optional[Dict], str, List[str]]:
    """Return (thesis, how, candidates). how: 'id', 'auto', 'not_found', 'missing', 'ambiguous'."""
    if thesis_id:
        exact = [t for t in theses if t.get("id") == thesis_id]
        if exact:
            return exact[0], "id", []
        pref = [t for t in theses if str(t.get("id", "")).startswith(thesis_id)]
        if len(pref) == 1:
            return pref[0], "id", []
        return None, ("ambiguous" if pref else "not_found"), [t["id"] for t in pref]
    cand = [t for t in theses if str(t.get("ticker", "")).upper() == ticker.upper()
            and t.get("status") in ("IDEA", "READY", "ACTIVE")]
    if len(cand) == 1:
        return cand[0], "auto", []
    if len(cand) > 1:
        return None, "ambiguous", sorted(t["id"] for t in cand)
    return None, "missing", []


def consecutive_losses(closed: List[Dict]) -> int:
    n = 0
    for r in reversed(closed):
        if r.get("net_pnl") is not None and r["net_pnl"] < 0:
            n += 1
        else:
            break
    return n


def realised_drawdown_pct(closed: List[Dict], account_size: float) -> float:
    """Drawdown of realised equity from its peak, in % (positive number).

    Equity is reconstructed backwards from today's account size (assumes no
    deposits / withdrawals): equity before trade k = account - sum(P&L after k).
    """
    pnl = [r.get("net_pnl") or 0.0 for r in closed]
    eq = [account_size]
    for p in reversed(pnl):
        eq.append(eq[-1] - p)
    eq.reverse()  # oldest first; last element = account_size
    peak = max(eq)
    return round(max(0.0, (peak - account_size) / peak * 100), 2) if peak > 0 else 0.0


def open_risk(positions: List[Dict], prices: Dict[str, float]) -> Dict:
    """Open risk to stops of journal positions, at current prices (avg price if unknown)."""
    total, no_stop, value = 0.0, [], 0.0
    rows = []
    for p in positions:
        px = prices.get(p["ticker"]) or p["avg_price"]
        v = p["shares"] * px
        value += v
        if p.get("stop") is None:
            no_stop.append(p["ticker"])
            r = None
        else:
            r = max(0.0, px - p["stop"]) * p["shares"]
            total += r
        rows.append({"ticker": p["ticker"], "shares": p["shares"], "price": px, "value": round(v, 2),
                     "stop": p.get("stop"), "risk": round(r, 2) if r is not None else None})
    return {"risk": round(total, 2), "value": round(value, 2), "no_stop": no_stop, "rows": rows}


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def check_stop(trade: Dict) -> Dict:
    s = trade.get("stop")
    if s is None:
        return _chk("stop", FAIL, "stop_missing")
    if s >= trade["entry"]:
        return _chk("stop", FAIL, "stop_not_below", stop=s, entry=trade["entry"])
    return _chk("stop", PASS, "stop_ok", stop=s, dist=round((trade["entry"] - s) / trade["entry"] * 100, 2))


def risk_limit(cfg: Dict, exposure: Optional[Dict], user_limit: Optional[float]) -> Tuple[float, str]:
    lim, src = cfg["max_risk_pct"], "default"
    ex = (exposure or {}).get("limits", {}).get("max_risk_per_trade_pct") if exposure else None
    if ex is not None:
        lim, src = float(ex), "exposure"
    if user_limit is not None:
        if ex is not None and user_limit > lim:
            return lim, "exposure"
        lim, src = float(user_limit), "user"
    return lim, src


def check_risk(nums: Dict, trade: Dict, limit: float, src: str) -> Dict:
    if "risk_total" not in nums:
        return _chk("risk", FAIL, "risk_undefined")
    pct_ = nums["risk_total"] / trade["account_size"] * 100
    st = FAIL if pct_ > limit + 1e-9 else PASS
    return _chk("risk", st, "risk_over" if st == FAIL else "risk_ok", risk=nums["risk_total"],
                pct=round(pct_, 3), limit=limit, src=src)


def check_position(nums: Dict, trade: Dict, cfg: Dict) -> Dict:
    pct_ = nums["value"] / trade["account_size"] * 100
    if nums["value"] + nums["fee_entry"] > trade["account_size"]:
        return _chk("position", FAIL, "position_cash", value=nums["value"], pct=round(pct_, 2))
    st = FAIL if pct_ > cfg["max_position_pct"] else PASS
    return _chk("position", st, "position_over" if st == FAIL else "position_ok", value=nums["value"],
                pct=round(pct_, 2), limit=cfg["max_position_pct"])


def check_liquidity(nums: Dict, market: Optional[Dict], cfg: Dict) -> Dict:
    med = (market or {}).get("median_turnover_pln")
    if med is None:
        return _chk("liquidity", WARN, "liq_unknown")
    if med <= 0:
        return _chk("liquidity", FAIL, "liq_zero")
    pct_ = nums["value"] / med * 100
    st = FAIL if pct_ > cfg["max_adv_pct"] else PASS
    return _chk("liquidity", st, "liq_over" if st == FAIL else "liq_ok", pct=round(pct_, 4),
                median=round(med, 0), limit=cfg["max_adv_pct"], window=cfg["turnover_window"])


def check_heat(nums: Dict, trade: Dict, book: Dict, cfg: Dict, journal_ok: bool) -> Dict:
    new = nums.get("risk_total") or 0.0
    after = (book["risk"] + new) / trade["account_size"] * 100
    p = dict(existing=book["risk"], new=new, pct=round(after, 2), limit=cfg["max_heat_pct"],
             no_stop=book["no_stop"])
    if after > cfg["max_heat_pct"] + 1e-9:
        return _chk("heat", FAIL, "heat_over", **p)
    if book["no_stop"]:
        return _chk("heat", WARN, "heat_no_stop", **p)
    if not journal_ok:
        return _chk("heat", WARN, "heat_no_journal", **p)
    return _chk("heat", PASS, "heat_ok", **p)


def check_sector(nums: Dict, trade: Dict, sector: Optional[str], book: Dict, sector_map: Dict[str, str],
                 cfg: Dict) -> Dict:
    if not sector:
        return _chk("sector", PASS, "sector_unclassified")
    existing = sum(r["value"] for r in book["rows"] if sector_map.get(r["ticker"]) == sector)
    after = (existing + nums["value"]) / trade["account_size"] * 100
    st = FAIL if after > cfg["max_sector_pct"] + 1e-9 else PASS
    return _chk("sector", st, "sector_over" if st == FAIL else "sector_ok", sector=sector,
                existing=round(existing, 2), pct=round(after, 2), limit=cfg["max_sector_pct"])


def check_thesis(trade: Dict, thesis: Optional[Dict], how: str, candidates: List[str], cfg: Dict,
                 journal_ok: bool) -> Dict:
    if thesis is None:
        if how == "ambiguous":
            return _chk("thesis", FAIL, "thesis_ambiguous", ids=candidates)
        if how == "not_found":
            return _chk("thesis", FAIL, "thesis_not_found", id=trade.get("thesis_id"))
        return _chk("thesis", FAIL, "thesis_missing", journal=journal_ok)
    if str(thesis.get("ticker", "")).upper() != trade["ticker"].upper():
        return _chk("thesis", FAIL, "thesis_ticker", id=thesis["id"], thesis_ticker=thesis.get("ticker"))
    if thesis.get("status") in ("CLOSED", "INVALIDATED"):
        return _chk("thesis", FAIL, "thesis_terminal", id=thesis["id"], status=thesis["status"])
    kills = [k for k in thesis.get("kill_criteria") or [] if str(k).strip()]
    if not kills:
        return _chk("thesis", FAIL, "thesis_no_kill", id=thesis["id"])
    plan_stop = (thesis.get("plan") or {}).get("stop")
    s = trade.get("stop")
    if plan_stop and s and abs(s - plan_stop) / plan_stop * 100 > cfg["plan_stop_tolerance_pct"]:
        return _chk("thesis", WARN, "thesis_stop_differs", id=thesis["id"], plan_stop=plan_stop, stop=s,
                    kills=len(kills), how=how)
    return _chk("thesis", PASS, "thesis_ok", id=thesis["id"], status=thesis.get("status"), kills=len(kills), how=how)


def check_rr(nums: Dict, trade: Dict, cfg: Dict) -> Dict:
    if trade.get("target") is None:
        return _chk("reward_risk", SKIP, "rr_no_target")
    rr = nums.get("rr")
    if rr is None:
        return _chk("reward_risk", FAIL, "rr_undefined")
    src = trade.get("target_source", "user")
    if rr < cfg["fail_rr"]:
        return _chk("reward_risk", FAIL, "rr_fail", rr=rr, target=trade["target"], limit=cfg["fail_rr"], src=src)
    if rr < cfg["min_rr"]:
        return _chk("reward_risk", WARN, "rr_warn", rr=rr, target=trade["target"], limit=cfg["min_rr"], src=src)
    return _chk("reward_risk", PASS, "rr_ok", rr=rr, target=trade["target"], limit=cfg["min_rr"], src=src)


def check_circuit_breaker(exposure: Optional[Dict], closed: List[Dict], account_size: float, cfg: Dict) -> Dict:
    cb = ((exposure or {}).get("limits") or {}).get("circuit_breaker") if exposure else None
    if cb is not None:
        if cb == "pause":
            return _chk("circuit_breaker", FAIL, "cb_pause_ex")
        if cb == "reduce":
            return _chk("circuit_breaker", WARN, "cb_reduce_ex")
        if cb == "ok":
            return _chk("circuit_breaker", PASS, "cb_ok_ex")
        return _chk("circuit_breaker", WARN, "cb_unknown", value=cb)
    if not closed:
        return _chk("circuit_breaker", PASS, "cb_no_history")
    streak = consecutive_losses(closed)
    dd = realised_drawdown_pct(closed, account_size)
    p = dict(streak=streak, dd=dd, n=len(closed))
    if streak >= cfg["cb_fail_losses"] or dd >= cfg["cb_fail_dd_pct"]:
        return _chk("circuit_breaker", FAIL, "cb_pause", **p)
    if streak >= cfg["cb_warn_losses"]:
        return _chk("circuit_breaker", WARN, "cb_reduce", **p)
    return _chk("circuit_breaker", PASS, "cb_ok", **p)


def check_regime(regime: Optional[Dict], override: Optional[str], cfg: Dict) -> Dict:
    if not regime:
        return _chk("regime", SKIP, "regime_missing")
    r = regime.get("regime") or {}
    state, top = r.get("state"), r.get("top_risk_score")
    p = dict(state=state, top=top)
    if state not in REGIME_STATES:
        return _chk("regime", WARN, "regime_unknown", **p)
    if state == "bear" and top is not None and top >= cfg["regime_fail_top_risk"]:
        if override:
            return _chk("regime", WARN, "regime_overridden", override=override, **p)
        return _chk("regime", FAIL, "regime_bear_top", limit=cfg["regime_fail_top_risk"], **p)
    if state in ("correction", "bear"):
        return _chk("regime", WARN, "regime_" + state, **p)
    if top is not None and top >= cfg["regime_warn_top_risk"]:
        return _chk("regime", WARN, "regime_top_risk", limit=cfg["regime_warn_top_risk"], **p)
    return _chk("regime", PASS, "regime_ok", **p)


def check_exposure_ceiling(exposure: Optional[Dict], nums: Dict, trade: Dict, book: Dict) -> Dict:
    lim = ((exposure or {}).get("limits") or {}).get("exposure_ceiling_pct") if exposure else None
    if lim is None:
        return _chk("exposure_ceiling", SKIP, "ceiling_missing")
    after = (book["value"] + nums["value"]) / trade["account_size"] * 100
    st = WARN if after > lim + 1e-9 else PASS
    return _chk("exposure_ceiling", st, "ceiling_over" if st == WARN else "ceiling_ok", pct=round(after, 2),
                limit=lim, posture=exposure.get("posture"))


def new_positions_in_window(theses: List[Dict], today: dt.date, days: int) -> int:
    start = (today - dt.timedelta(days=days - 1)).isoformat()
    n = 0
    for th in theses:
        buys = sorted(f["date"] for f in th.get("fills", []) if f.get("side") == "buy")
        if buys and start <= buys[0] <= today.isoformat() and not th.get("paper"):
            n += 1
    return n


def check_new_positions(exposure: Optional[Dict], theses: List[Dict], thesis: Optional[Dict], today: dt.date,
                        cfg: Dict) -> Dict:
    lim = ((exposure or {}).get("limits") or {}).get("max_new_positions_per_week") if exposure else None
    if lim is None:
        return _chk("new_positions", SKIP, "newpos_missing")
    n = new_positions_in_window(theses, today, cfg["new_positions_window_days"])
    adding = bool(thesis and thesis.get("status") == "ACTIVE")
    after = n + (0 if adding else 1)
    st = WARN if after > lim else PASS
    return _chk("new_positions", st, "newpos_over" if st == WARN else "newpos_ok", n=after, limit=lim, adding=adding)


def check_freshness(market: Optional[Dict], trade: Dict, inputs: List[Dict], cfg: Dict) -> Dict:
    issues = []
    if not market:
        issues.append({"code": "no_market"})
    else:
        if market.get("is_stale"):
            issues.append({"code": "prices_stale", "n": market.get("sessions_behind"), "date": market.get("last_date")})
        lc = market.get("last_close")
        if lc:
            dev = (trade["entry"] / lc - 1) * 100
            if abs(dev) > cfg["entry_deviation_pct"]:
                issues.append({"code": "entry_far", "dev": round(dev, 1), "last": lc})
    for i in inputs:
        lim = i.get("max_sessions_behind", cfg["max_input_age_sessions"])
        if i.get("sessions_behind") is not None and i["sessions_behind"] > lim:
            issues.append({"code": "input_stale", "skill": i["skill"], "n": i["sessions_behind"], "date": i.get("as_of")})
    if issues:
        return _chk("data_freshness", WARN, "fresh_issues", issues=issues)
    return _chk("data_freshness", PASS, "fresh_ok", date=(market or {}).get("last_date"))


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def evaluate(trade: Dict, ctx: Dict, cfg: Optional[Dict] = None) -> Dict:
    """Run every check. ``ctx`` keys (all optional):

    market: {last_close, last_date, median_turnover_pln, is_stale, sessions_behind}
    sector: sector key of the ticker; sector_map: {name: sector}
    theses: journal theses; journal_ok: journal dir readable
    open_positions / closed_results: from gpwlib.journal_io; prices: {name: last close}
    regime / exposure: parsed JSON (contract); inputs: [{skill, as_of, sessions_behind}]
    override: written reason; user_max_risk_pct; today
    context: v1.2 reports and options (see pretrade_context_checks.run_context_checks);
             when present, the seven context checks are appended
    """
    cfg = dict(DEFAULTS, **(cfg or {}))
    validate_trade(trade)
    theses = ctx.get("theses") or []
    today = ctx.get("today") or dt.date.today()
    thesis, how, cand = find_thesis(theses, trade["ticker"], trade.get("thesis_id"))
    if trade.get("target") is None and thesis and (thesis.get("plan") or {}).get("target"):
        tgt = thesis["plan"]["target"]
        if tgt > trade["entry"]:
            trade = dict(trade, target=tgt, target_source="thesis")
    nums = trade_numbers(trade)
    book = open_risk(ctx.get("open_positions") or [], ctx.get("prices") or {})
    limit, src = risk_limit(cfg, ctx.get("exposure"), ctx.get("user_max_risk_pct"))
    checks = [
        check_stop(trade),
        check_risk(nums, trade, limit, src),
        check_position(nums, trade, cfg),
        check_liquidity(nums, ctx.get("market"), cfg),
        check_heat(nums, trade, book, cfg, bool(ctx.get("journal_ok"))),
        check_sector(nums, trade, ctx.get("sector"), book, ctx.get("sector_map") or {}, cfg),
        check_thesis(trade, thesis, how, cand, cfg, bool(ctx.get("journal_ok"))),
        check_rr(nums, trade, cfg),
        check_circuit_breaker(ctx.get("exposure"), ctx.get("closed_results") or [], trade["account_size"], cfg),
        check_regime(ctx.get("regime"), ctx.get("override"), cfg),
        check_exposure_ceiling(ctx.get("exposure"), nums, trade, book),
        check_new_positions(ctx.get("exposure"), theses, thesis, today, cfg),
        check_freshness(ctx.get("market"), trade, ctx.get("inputs") or [], cfg),
    ]
    context = None
    if ctx.get("context") is not None:
        extra = cc.run_context_checks(trade, nums, ctx["context"])
        checks += extra["checks"]
        context = extra["context"]
    statuses = [c["status"] for c in checks]
    verdict = verdict_of(statuses)
    out = {
        "verdict": verdict,
        "counts": {s: statuses.count(s) for s in ((PASS, WARN, FAIL, SKIP) if context is None else STATUSES)},
        "checks": checks,
        "trade": dict(trade),
        "numbers": nums,
        "risk_limit_pct": limit,
        "risk_limit_source": src,
        "book": book,
        "thesis": ({"id": thesis["id"], "status": thesis.get("status"), "found_by": how,
                    "kill_criteria": thesis.get("kill_criteria"), "thesis": thesis.get("thesis")} if thesis else None),
        "override": ctx.get("override"),
        "thresholds": cfg,
    }
    if context is not None:
        out["context"] = context
    return out


def verdict_of(statuses) -> str:
    """FAIL -> NO-GO; WARN -> GO WITH CONDITIONS; PASS/SKIP/INFO/UNKNOWN only -> GO."""
    if FAIL in statuses:
        return "NO-GO"
    return "GO WITH CONDITIONS" if WARN in statuses else "GO"
