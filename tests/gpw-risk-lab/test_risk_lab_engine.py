"""Unit tests of the gpw-risk-lab engine (synthetic data, no network)."""

import datetime as dt
import json
import math
from pathlib import Path

import pytest

import risk_lab_engine as e
from gpwlib import gpw_archive

FIX = Path(__file__).parent / "fixtures"


def weekdays(start, n):
    out, d = [], dt.date.fromisoformat(start)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def path_from_returns(dates, rets, start=100.0):
    out, v = {dates[0]: start}, start
    for d, r in zip(dates[1:], rets):
        v *= 1 + r
        out[d] = v
    return out


def is_session_weekday(d):
    return d.weekday() < 5


# ---------------------------------------------------------------- statistics

def test_ols_recovers_exact_line():
    x = [0.01, -0.02, 0.03, 0.005, -0.01, 0.02]
    y = [0.001 + 1.5 * v for v in x]
    fit = e.ols(y, x)
    assert fit["beta"] == pytest.approx(1.5)
    assert fit["alpha"] == pytest.approx(0.001)
    assert fit["r2"] == pytest.approx(1.0)


def test_ols_degenerate():
    assert e.ols([1, 2], [1, 2]) is None
    assert e.ols([1, 2, 3], [5, 5, 5]) is None


def test_beta_regression_on_prices():
    dates = weekdays("2025-01-01", 200)
    mret = [0.01 * math.sin(i) for i in range(199)]
    idx = path_from_returns(dates, mret)
    stock = path_from_returns(dates, [1.3 * r for r in mret])
    b = e.beta_of(stock, idx, dates)
    assert b["beta"] == pytest.approx(1.3, abs=1e-6)
    assert not b["default"]
    assert b["n"] == 199


def test_beta_short_history_defaults_to_one():
    dates = weekdays("2025-01-01", 200)
    idx = path_from_returns(dates, [0.01] * 199)
    stock = {d: 10.0 + i for i, d in enumerate(dates[-30:])}
    b = e.beta_of(stock, idx, dates)
    assert b["default"] and b["beta"] == 1.0


def test_beta_is_clipped():
    dates = weekdays("2025-01-01", 120)
    mret = [0.01 * math.sin(i) for i in range(119)]
    b = e.beta_of(path_from_returns(dates, [5 * r for r in mret]), path_from_returns(dates, mret), dates)
    assert b["beta"] == 3.0 and b["raw_beta"] == pytest.approx(5.0)


# ---------------------------------------------------------------- windows

def _wig():
    dates = weekdays("2008-01-01", 30)
    closes = [100, 105, 110, 108, 90, 80, 85, 70, 75, 95] + [96 + i for i in range(20)]
    return dates, closes


def test_peak_to_trough_window():
    dates, closes = _wig()
    w = e.resolve_window({"kind": "peak_to_trough", "from": dates[0], "to": dates[9]}, dates, closes)
    assert w["start"] == dates[2] and w["end"] == dates[7]
    assert w["index_return"] == pytest.approx(70 / 110 - 1)


def test_fixed_window_and_worst_days():
    dates, closes = _wig()
    w = e.resolve_window({"kind": "fixed", "start": dates[3], "end": dates[4]}, dates, closes)
    assert w["index_return"] == pytest.approx(90 / 108 - 1)
    w1 = e.resolve_window({"kind": "worst_days", "days": 1}, dates, closes)
    assert (w1["start"], w1["end"]) == (dates[6], dates[7])  # 85 -> 70 = -17.6%
    w5 = e.resolve_window({"kind": "worst_days", "days": 5}, dates, closes)
    assert w5["index_return"] == pytest.approx(70 / 110 - 1)


def test_window_outside_data_is_none():
    dates, closes = _wig()
    assert e.resolve_window({"kind": "peak_to_trough", "from": "2007-10-01", "to": "2007-12-31"}, dates, closes) is None
    assert e.resolve_window({"kind": "fixed", "start": "2001-01-02", "end": "2001-01-05"}, dates, closes) is None
    with pytest.raises(e.StressError):
        e.resolve_window({"kind": "bogus"}, dates, closes)


def test_window_return_requires_trading_at_start():
    ref = weekdays("2020-01-01", 40)
    listed_later = {d: 10.0 for d in ref[20:]}
    assert e.window_return(listed_later, ref[2], ref[30], ref) is None
    full = {d: 10.0 + i for i, d in enumerate(ref)}
    assert e.window_return(full, ref[2], ref[30], ref) == pytest.approx((10 + 30) / (10 + 2) - 1)
    suspended = {d: 10.0 for d in ref[:5]}  # last quote far before the end
    assert e.window_return(suspended, ref[2], ref[30], ref) is None


# ---------------------------------------------------------------- proxy chain

def test_proxy_chain_order():
    assert e.proxy_chain("WIG-BANKI", "WIG20") == [("proxy_sector", "WIG-BANKI"), ("proxy_size", "WIG20"), ("proxy_wig", "WIG")]
    assert e.proxy_chain(None, None) == [("proxy_wig", "WIG")]


def test_historical_returns_actual_then_sector_size_wig():
    pos = [{"ticker": "A", "value": 100, "sector": "WIG-BANKI", "size": "WIG20"},
           {"ticker": "B", "value": 100, "sector": "WIG-GRY", "size": "MWIG40"},
           {"ticker": "C", "value": 100, "sector": "WIG-GRY", "size": None},
           {"ticker": "D", "value": 100, "sector": None, "size": None}]
    stock = {"A": -0.30, "B": None, "C": None, "D": None}
    idx = {"WIG-BANKI": -0.5, "WIG-GRY": None, "MWIG40": -0.20, "WIG": -0.25, "WIG20": -0.3}
    betas = {"B": {"MWIG40": {"beta": 1.5}}, "C": {"WIG": {"beta": 0.8}}, "D": {}}
    out = {r["ticker"]: r for r in e.historical_position_returns(pos, stock, idx, betas)}
    assert out["A"]["method"] == "actual" and out["A"]["return"] == -0.30
    assert out["B"]["method"] == "proxy_size" and out["B"]["return"] == pytest.approx(-0.30)
    assert out["C"]["method"] == "proxy_wig" and out["C"]["return"] == pytest.approx(-0.20)
    assert out["D"]["method"] == "proxy_wig" and out["D"]["beta"] == 1.0  # default beta


def test_proxy_return_never_below_minus_100():
    pos = [{"ticker": "X", "value": 100, "sector": None, "size": None}]
    out = e.historical_position_returns(pos, {"X": None}, {"WIG": -0.6}, {"X": {"WIG": {"beta": 3.0}}})
    assert out[0]["return"] == -1.0


def test_no_proxy_available_marks_unavailable_and_aggregate_flags_it():
    pos = [{"ticker": "X", "value": 100, "sector": None, "size": None}]
    pr = e.historical_position_returns(pos, {"X": None}, {"WIG": None}, {})
    assert pr[0]["method"] == "unavailable"
    agg = e.aggregate(pos, pr, cash=0)
    assert not agg["complete"] and agg["missing"] == ["X"]


# ---------------------------------------------------------------- factor shocks

POS = [{"ticker": "BANK", "value": 6000.0, "sector": "WIG-BANKI", "size": "WIG20"},
       {"ticker": "GAME", "value": 4000.0, "sector": "WIG-GRY", "size": "MWIG40"}]
BETAS = {"BANK": {"WIG": {"beta": 1.2}, "WIG-BANKI": {"beta": 1.0}, "WIG20": {"beta": 1.1}},
         "GAME": {"WIG": {"beta": 0.5}, "WIG-BANKI": {"beta": 0.3}, "WIG-GRY": {"beta": 0.9}, "MWIG40": {"beta": 1.0}}}


def test_market_shock_math_and_aggregation():
    pr = e.factor_position_returns(POS, {"kind": "market", "index": "WIG", "shock_pct": -20}, BETAS, {})
    agg = e.aggregate(POS, pr, cash=10000.0)
    # BANK -24% x 6000 = -1440; GAME -10% x 4000 = -400; total 20000
    assert agg["pln_loss"] == pytest.approx(1840.0)
    assert agg["portfolio_return_pct"] == pytest.approx(-9.2)
    assert agg["worst_positions"][0]["ticker"] == "BANK"
    assert agg["worst_positions"][0]["contribution_pp"] == pytest.approx(-7.2)
    assert sum(r["contribution_pp"] for r in agg["positions"]) == pytest.approx(agg["portfolio_return_pct"], abs=1e-6)


def test_sector_shock_spillover_modes():
    spec = {"kind": "sector", "index": "WIG-BANKI", "shock_pct": -25}
    none = {r["ticker"]: r for r in e.factor_position_returns(POS, dict(spec, spillover="none"), BETAS, {})}
    assert none["BANK"]["return"] == pytest.approx(-0.25) and none["GAME"]["return"] == 0.0
    beta = {r["ticker"]: r for r in e.factor_position_returns(POS, dict(spec, spillover="beta"), BETAS, {})}
    assert beta["GAME"]["return"] == pytest.approx(-0.075)


def test_fx_and_rates_use_proxy_sensitivity():
    sens = {"WIG-BANKI": {"fx": {"beta": -2.0, "significant": True}, "rates": {"beta": 0.05, "significant": False}},
            "WIG-GRY": {"fx": None}, "MWIG40": {"fx": {"beta": -1.0, "significant": True}},
            "WIG": {"rates": {"beta": -0.02, "significant": True}}}
    fx = {r["ticker"]: r for r in e.factor_position_returns(POS, {"kind": "fx", "shock_pct": 15}, BETAS, sens)}
    assert fx["BANK"]["return"] == pytest.approx(1.0 * -2.0 * 0.15)
    assert fx["GAME"]["proxy"] == "MWIG40" and fx["GAME"]["return"] == pytest.approx(-0.15)
    rt = {r["ticker"]: r for r in e.factor_position_returns(POS, {"kind": "rates", "shock_bp": 200}, BETAS, sens)}
    assert rt["BANK"]["return"] == pytest.approx(0.10)       # +5% per pp x 2 pp
    assert rt["GAME"]["proxy"] == "WIG" and rt["GAME"]["return"] == pytest.approx(0.5 * -0.02 * 2)


def test_overrides_and_unknown_kind():
    pr = e.factor_position_returns(POS, {"kind": "market", "shock_pct": -10, "overrides": {"game": -50}}, BETAS, {})
    assert {r["ticker"]: r["return"] for r in pr}["GAME"] == -0.5
    with pytest.raises(e.StressError):
        e.factor_position_returns(POS, {"kind": "meteor"}, BETAS, {})


def test_factor_sensitivity_weekly_fx():
    dates = weekdays("2018-01-01", 1000)
    fx_rets = [0.004 * math.sin(i / 3.0) for i in range(999)]
    fx = path_from_returns(dates, fx_rets, 4.3)
    idx = path_from_returns(dates, [-2.0 * r for r in fx_rets], 1000)
    s = e.factor_sensitivity(idx, fx, "W", "pct")
    assert s["beta"] == pytest.approx(-2.0, abs=0.15)
    assert s["significant"] and s["n"] > 150


def test_factor_sensitivity_monthly_rates_diff():
    months = ["{}-{:02d}-01".format(2010 + i // 12, i % 12 + 1) for i in range(120)]
    y, level, idx, v = {}, 5.0, {}, 100.0
    for i, m in enumerate(months):
        dy = 0.3 * math.sin(i)
        level += dy
        v *= 1 + 0.04 * dy
        y[m], idx[m] = level, v
    s = e.factor_sensitivity(idx, y, "M", "diff", min_obs=36)
    assert s["beta"] == pytest.approx(0.04, abs=0.01)


def test_factor_sensitivity_too_short():
    d = weekdays("2020-01-01", 20)
    assert e.factor_sensitivity({x: 1.0 + i for i, x in enumerate(d)}, {x: 2.0 + i for i, x in enumerate(d)}, "W", "pct") is None


# ---------------------------------------------------------------- liquidity / tolerance

def test_liquidity_days_to_exit():
    pos = [{"ticker": "BIG", "value": 1_000_000.0}, {"ticker": "SMALL", "value": 50_000.0},
           {"ticker": "DEAD", "value": 1_000.0}]
    med = {"BIG": 2_000_000.0, "SMALL": 100_000.0, "DEAD": None}
    liq = e.liquidity_stress(pos, med)
    rows = {r["ticker"]: r for r in liq["positions"]}
    # stress capacity = 2M x 20% x 50% = 200k/day -> 5 days; normal = 400k/day -> 2.5 days
    assert rows["BIG"]["days_stress"] == pytest.approx(5.0)
    assert rows["BIG"]["days_normal"] == pytest.approx(2.5)
    assert rows["SMALL"]["days_stress"] == pytest.approx(5.0)
    assert rows["DEAD"]["days_stress"] is None
    assert liq["max_days_stress"] is None and "DEAD" in liq["illiquid"]
    assert liq["sellable_1d_pct"] == pytest.approx((200_000 + 10_000) / 1_051_000 * 100, abs=0.01)


def test_liquidity_custom_participation():
    liq = e.liquidity_stress([{"ticker": "A", "value": 100.0}], {"A": 1000.0},
                             {"participation": 0.10, "stress_turnover_factor": 1.0})
    assert liq["positions"][0]["days_stress"] == pytest.approx(1.0)


def test_tolerance_check():
    sc = [{"id": "a", "portfolio_return_pct": -40.0}, {"id": "b", "portfolio_return_pct": -10.0},
          {"id": "c", "portfolio_return_pct": -25.0}]
    tol = e.tolerance_check(sc, 20.0)
    assert [b["id"] for b in tol["breaches"]] == ["a", "c"]
    assert tol["breaches"][0]["equity_reduction_to_fit_pct"] == pytest.approx(50.0)
    assert e.tolerance_check(sc, None) is None
    assert e.tolerance_check(sc, 50.0)["ok"]


# ---------------------------------------------------------------- hedge

def test_contracts_floor_and_ceil():
    c = e.contracts_needed(beta=1.1, value=400_000, price=4311, multiplier=20)
    exact = 1.1 * 400_000 / (4311 * 20)
    assert c["exact"] == pytest.approx(round(exact, 3))
    assert (c["floor"], c["ceil"]) == (5, 6)
    assert c["hedge_ratio_floor"] == pytest.approx(5 * 4311 * 20 / 440_000, abs=1e-4)
    assert not c["below_one_contract"]


def test_contracts_exact_integer_and_small_portfolio():
    c = e.contracts_needed(1.0, 172_440, 4311, 20)
    assert (c["floor"], c["ceil"]) == (2, 2)
    s = e.contracts_needed(0.9, 30_000, 4311, 20)
    assert s["floor"] == 0 and s["ceil"] == 1 and s["below_one_contract"]
    with pytest.raises(e.StressError):
        e.contracts_needed(1.0, 1000, 0, 20)


def test_last_trading_day_third_friday_and_holiday_shift():
    assert e.last_trading_day(2026, 12, is_session_weekday) == dt.date(2026, 12, 18)
    assert e.last_trading_day(2027, 3, is_session_weekday) == dt.date(2027, 3, 19)
    holiday = dt.date(2026, 12, 18)
    assert e.last_trading_day(2026, 12, lambda d: d.weekday() < 5 and d != holiday) == dt.date(2026, 12, 17)


def test_futures_symbol_parsing_integration_and_chain():
    rows = json.loads((FIX / "fw20_rows.json").read_text())
    fw20 = gpw_archive.futures_rows(rows, "WIG20")
    assert [r["name"] for r in fw20] == ["FW20Z2620", "FW20H2720", "FW20M2720", "FW20U2720"]
    assert all(r["multiplier"] == 20 for r in fw20)
    chain = e.futures_chain(fw20, dt.date(2026, 11, 13), is_session_weekday)
    assert chain[0]["symbol"] == "FW20Z2620" and chain[0]["role"] == "front"
    assert chain[0]["last_trading_day"] == "2026-12-18" and chain[0].get("recommended")
    assert chain[1]["role"] == "next"
    assert chain[0]["turnover_pln"] == pytest.approx(2368797140.0)
    # inside the roll window the next series is recommended
    late = e.futures_chain(fw20, dt.date(2026, 12, 15), is_session_weekday)
    assert late[0]["sessions_to_expiry"] == 3 and late[1].get("recommended")
    # on expiry day the expiring series is no longer offered
    on_exp = e.futures_chain(fw20, dt.date(2026, 12, 18), is_session_weekday)
    assert on_exp[0]["symbol"] == "FW20H2720"


def test_roll_schedule():
    rs = e.roll_dates([("FW20Z2620", 2026, 12), ("FW20H2720", 2027, 3)], is_session_weekday)
    assert rs[0] == {"symbol": "FW20Z2620", "last_trading_day": "2026-12-18", "roll_window_from": "2026-12-11"}
    assert rs[1]["last_trading_day"] == "2027-03-19"


def _row(name, price):
    info = gpw_archive.parse_futures_symbol(name)
    return {"name": name, "settlement": price, "close": price, "month": info["month"], "year": info["year"],
            "multiplier": info["multiplier"]}


def test_continuous_series_rolls_at_expiry_without_gap():
    sess = [
        ("2026-12-17", [_row("FW20Z2620", 4000), _row("FW20H2720", 4100)]),
        ("2026-12-18", [_row("FW20Z2620", 4040), _row("FW20H2720", 4150)]),   # expiry day: still Z26
        ("2026-12-21", [_row("FW20H2720", 4191.5)]),                          # now H27, vs its own prior price
    ]
    c = e.continuous_returns(sess, is_session_weekday)
    assert c["2026-12-18"]["symbol"] == "FW20Z2620" and c["2026-12-18"]["ret"] == pytest.approx(0.01)
    assert c["2026-12-21"]["symbol"] == "FW20H2720" and c["2026-12-21"]["ret"] == pytest.approx(4191.5 / 4150 - 1)
    assert "2026-12-17" not in c


def test_hedge_backtest_full_hedge_removes_market_moves():
    dates = weekdays("2026-01-01", 60)
    fut = {d: 0.01 * math.sin(i) for i, d in enumerate(dates)}
    port = {d: 1.2 * r for d, r in fut.items()}
    bt = e.hedge_backtest(port, fut, {"hedged_exact": 1.2, "hedged_floor": 0.6})
    assert bt["hedged_exact"]["volatility_pct"] == pytest.approx(0.0, abs=1e-9) or bt["hedged_exact"]["volatility_pct"] is None
    assert bt["hedged_floor"]["volatility_pct"] == pytest.approx(bt["unhedged"]["volatility_pct"] / 2, rel=1e-6)
    assert e.hedge_backtest({}, fut, {"x": 1}) is None


def test_residual_risk_and_portfolio_beta():
    x = [0.01 * math.sin(i) for i in range(100)]
    noise = [0.005 * math.cos(3 * i) for i in range(100)]
    rr = e.residual_risk([1.1 * a + b for a, b in zip(x, noise)], x)
    assert 0 < rr["residual_risk_pct"] < 50
    assert rr["beta"] == pytest.approx(1.1, abs=0.05)
    assert e.portfolio_beta({"A": 0.5, "B": 0.5}, {"A": 1.2}) == pytest.approx(1.1)


def test_margin_estimate():
    rets = [0.01 * ((-1) ** i) for i in range(100)] + [0.05]
    m = e.margin_estimate(rets, 4000, 20)
    assert m["ccp_like_pct"] > 0
    assert m["investor_initial_pln"] == pytest.approx(m["ccp_like_pln"] * 1.2, rel=1e-3)
    assert e.margin_estimate([0.01] * 10, 4000, 20) is None


def test_proxy_return_compounds_daily_beta():
    daily = [-0.01] * 100                      # index -63.4% over the window
    total = (0.99 ** 100) - 1
    assert e.proxy_return(1.0, total, daily) == pytest.approx(total)
    comp = e.proxy_return(1.7, total, daily)
    assert comp == pytest.approx(0.983 ** 100 - 1)
    assert -1.0 < comp and e.proxy_return(1.7, total) == -1.0   # linear approximation hits the floor
    assert e.index_daily_returns({"a": 100, "b": 110, "c": 99, "z": 1}, "a", "c") == pytest.approx([0.1, -0.1])


def test_historical_returns_use_daily_path_when_given():
    pos = [{"ticker": "X", "value": 100, "sector": None, "size": None}]
    out = e.historical_position_returns(pos, {"X": None}, {"WIG": 0.99 ** 50 - 1}, {"X": {"WIG": {"beta": 2.0}}},
                                        {"WIG": [-0.01] * 50})
    assert out[0]["return"] == pytest.approx(0.98 ** 50 - 1)
