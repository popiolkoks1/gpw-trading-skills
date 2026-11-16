"""pair_engine: pair analysis, scan correction, backtests, signals, futures."""
import datetime as dt
import math

import pytest

import pair_engine as pe
from pair_fixtures import cointegrated_prices, random_walk


def _dates(n, start=dt.date(2023, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _al(pa, pb, turnover_k=50000.0):
    d = _dates(len(pa))
    return pe.align(d, pa, [turnover_k] * len(pa), d, pb, [turnover_k] * len(pb))


CFG = dict(pe.DEFAULTS)


def test_align_keeps_common_sessions_only():
    al = pe.align(["d1", "d2", "d3"], [1, 2, 3], [1, 1, 1], ["d2", "d3", "d4"], [5, 6, 7], [2, 2, 2])
    assert al["dates"] == ["d2", "d3"] and al["pa"] == [2, 3] and al["pb"] == [5, 6]


def test_cointegrated_pair_passes_and_recovers_hedge_ratio():
    pa, pb = cointegrated_prices(600, beta=1.2, phi=0.9)
    r = pe.analyze_pair("AAA", "BBB", _al(pa, pb), dict(CFG, min_corr=0.0), full=False)
    assert r["status"] == "ok"
    # OLS is super-consistent but biased towards 0 in finite samples (Banerjee et al. 1986)
    assert r["hedge_ratio"] == pytest.approx(1.2, abs=0.1)
    assert r["p_value"] < 0.01
    assert r["coint_pass"] is True
    hl_true = -math.log(2) / math.log(0.9)
    assert r["half_life_days"] == pytest.approx(hl_true, rel=0.35)
    assert r["window_sessions"] == CFG["lookback"]


def test_independent_walks_fail():
    pa = [math.exp(math.log(50) + v) for v in random_walk(600, 8, 0.015)]
    pb = [math.exp(math.log(50) + v) for v in random_walk(600, 9, 0.015)]
    r = pe.analyze_pair("AAA", "BBB", _al(pa, pb), CFG, full=False)
    assert r["p_value"] > 0.05
    assert r["coint_pass"] is False


def test_p_value_is_order_invariant():
    pa, pb = cointegrated_prices(600)
    r1 = pe.analyze_pair("A", "B", _al(pa, pb), CFG, full=False)
    r2 = pe.analyze_pair("B", "A", _al(pb, pa), CFG, full=False)
    assert r1["p_value"] == pytest.approx(r2["p_value"])


def test_short_history_and_liquidity():
    pa, pb = cointegrated_prices(200)
    r = pe.analyze_pair("A", "B", _al(pa, pb), CFG, full=False)
    assert r["status"] == "short_history" and "coint_pass" not in r
    pa, pb = cointegrated_prices(400)
    r = pe.analyze_pair("A", "B", _al(pa, pb, turnover_k=50.0), CFG, full=False)  # 50k PLN < 100k needed
    assert r["liquidity_ok"] is False
    assert r["min_turnover_needed_pln"] == 100000


def test_full_analysis_has_stability_backtests_series():
    pa, pb = cointegrated_prices(700, phi=0.85)
    r = pe.analyze_pair("A", "B", _al(pa, pb), dict(CFG, min_corr=0.0), fee=lambda v: round(v * 0.0039, 2))
    assert 0.5 <= r["stability"] <= 1.0
    assert r["hedge_drift"]["windows"] == len(range(700, 249, -21))
    bt = r["backtest"]
    assert bt["in_sample"]["end"] < bt["out_of_sample"]["start"]
    for per in ("in_sample", "out_of_sample"):
        sr = bt[per]["spread_rule"]
        assert sr["trades"] >= 1
        assert sr["costs_pln"] > 0
        assert "return_pct" in bt[per]["switch_rule"]
    assert len(r["series"]["z"]["values"]) <= CFG["max_series_points"]
    assert r["series"]["z"]["dates"][-1] == r["as_of"]
    assert len(r["ascii_chart"]) == 7 and r["sparkline"]


def test_spread_backtest_mechanics():
    # A jumps up (z > entry) then returns: short A / long B should earn the reversion
    n = 10
    dates = _dates(n)
    pa = [100.0] * 3 + [110.0, 110.0, 100.0] + [100.0] * 4
    pb = [100.0] * n
    z = [None, 0.0, 0.5, 2.5, 2.4, -0.1, 0.0, 0.0, 0.0, 0.0]
    cfg = dict(CFG, position_value=1000.0)
    res = pe.spread_backtest(dates, pa, pb, z, 1.0, 0, n, cfg, fee=lambda v: 0.0, borrow_pct_annual=0.0)
    assert res["trades"] == 1
    t = res["list"][0]
    assert t["side"] == "short_a_long_b" and t["reason"] == "mean"
    assert t["gross_pln"] == pytest.approx(1000.0 / 110.0 * 10.0, abs=0.01)
    # costs: 4 orders at min 5 PLN + borrow fee on the short leg
    res2 = pe.spread_backtest(dates, pa, pb, z, 1.0, 0, n, cfg, fee=lambda v: 5.0, borrow_pct_annual=10.0)
    assert res2["list"][0]["costs_pln"] > 20.0


def test_spread_backtest_stop_and_no_immediate_reentry():
    n = 8
    dates = _dates(n)
    pa = [100, 100, 105, 112, 112, 112, 112, 112]
    pb = [100.0] * n
    z = [0.0, 0.0, 2.2, 3.8, 3.0, 2.5, 2.1, 2.1]
    res = pe.spread_backtest(dates, pa, pb, z, 1.0, 0, n, CFG, fee=lambda v: 0.0, borrow_pct_annual=0.0)
    assert res["trades"] == 1 and res["stops"] == 1  # z stayed above entry -> not re-armed


def test_switch_backtest_rotates_and_counts_costs():
    n = 8
    dates = _dates(n)
    pa = [100, 100, 120, 120, 100, 100, 100, 100]
    pb = [100, 100, 100, 100, 100, 100, 120, 120]
    z = [0.0, 0.0, 2.5, 1.0, -2.5, 0.0, 0.0, 0.0]
    res = pe.switch_backtest(dates, pa, pb, z, 0, n, dict(CFG, position_value=1000.0), fee=lambda v: 0.0)
    assert res["switches"] == 2 and res["holding_at_end"] == "A"
    # sold A at 120 into B at 100 (x1.2), back to A at 100: final 1200
    assert res["final_pln"] == pytest.approx(1200.0)
    assert res["hold_a_return_pct"] == pytest.approx(0.0)
    res_fee = pe.switch_backtest(dates, pa, pb, z, 0, n, dict(CFG, position_value=1000.0), fee=lambda v: 5.0)
    assert res_fee["costs_pln"] == pytest.approx(5.0 + 4 * 5.0)


def test_apply_multiple_testing_bh_vs_raw():
    pairs = [{"status": "ok", "p_value": p, "corr_ok": True, "hedge_ratio": 1.0} for p in (0.001, 0.015, 0.025, 0.035, 0.6)]
    pairs.append({"status": "short_history"})
    m = pe.apply_multiple_testing(pairs, "bh", 0.05, 0.7)
    assert m == 5
    assert [p["coint_pass"] for p in pairs[:5]] == [True, True, True, True, False]
    pe.apply_multiple_testing(pairs, "bonferroni", 0.05, 0.7)
    assert [p["coint_pass"] for p in pairs[:5]] == [True, False, False, False, False]
    assert pairs[5]["coint_pass"] is False
    pairs[0]["corr_ok"] = False
    pe.apply_multiple_testing(pairs, "none", 0.05, 0.7)
    assert pairs[0]["coint_pass"] is False and pairs[0]["p_adj"] == pairs[0]["p_value"]


def test_negative_hedge_ratio_never_passes():
    pairs = [{"status": "ok", "p_value": 0.0001, "corr_ok": True, "hedge_ratio": -0.5}]
    pe.apply_multiple_testing(pairs, "none", 0.05, 0.7)
    assert pairs[0]["coint_pass"] is False


def test_episode_start_and_signals():
    dates = ["d1", "d2", "d3", "d4", "d5"]
    z = [0.1, 1.5, 2.1, 2.4, 2.2]
    assert pe.episode_start(z, dates, 2.0) == "d3"
    assert pe.episode_start([0.0, 1.0], ["a", "b"], 2.0) is None
    base = {"status": "ok", "coint_pass": True, "a": "AAA", "b": "BBB", "hedge_ratio": 1.1, "p_adj": 0.01,
            "half_life_days": 12.0, "stability": 0.75, "as_of": "2026-11-13", "episode_start": "2026-11-10"}
    sig = pe.signals_for([dict(base, z=2.3)], CFG, raw_close=lambda t, d: 42.0)
    assert len(sig) == 1
    s = sig[0]
    assert s["ticker"] == "BBB" and s["direction"] == "long" and s["kind"] == "pair_dislocation"
    assert s["date"] == "2026-11-10" and s["score"] == 75.0 and s["price"] == 42.0
    assert s["tags"]["rich_leg"] == "AAA"
    assert pe.signals_for([dict(base, z=-2.3)], CFG)[0]["ticker"] == "AAA"
    # no signal: inside the band, beyond the stop, or not cointegrated
    assert pe.signals_for([dict(base, z=1.0)], CFG) == []
    assert pe.signals_for([dict(base, z=4.0)], CFG) == []
    assert pe.signals_for([dict(base, z=2.5, coint_pass=False)], CFG) == []


def test_signals_follow_contract_patterns():
    import re
    base = {"status": "ok", "coint_pass": True, "a": "PKOBP", "b": "PEKAO", "hedge_ratio": 1.0, "p_adj": 0.01,
            "half_life_days": None, "stability": 0.5, "as_of": "2026-11-13", "episode_start": None, "z": 2.2}
    s = pe.signals_for([base], CFG)[0]
    assert re.fullmatch(r"[A-Za-z0-9.-]{1,20}", s["ticker"])
    assert re.fullmatch(r"[a-z0-9_]{1,40}", s["kind"])
    assert len(s["date"]) == 10 and s["direction"] in ("long", "avoid", "exit")
    assert all(not isinstance(v, (dict, list)) and len(str(v)) <= 80 for v in s["tags"].values())


def test_switch_view_verdicts():
    p = {"status": "ok", "coint_pass": True, "stability": 0.6}
    assert pe.switch_view(dict(p, z=2.4), CFG)["verdict"] == "b_relatively_cheap"
    assert pe.switch_view(dict(p, z=2.4, coint_pass=False), CFG)["verdict"] == "b_cheap_unstable"
    assert pe.switch_view(dict(p, z=-2.4), CFG)["verdict"] == "b_relatively_expensive"
    assert pe.switch_view(dict(p, z=0.5), CFG)["verdict"] == "no_dislocation"
    assert pe.switch_view(dict(p, z=-3.9), CFG)["verdict"] == "extreme_deviation"
    assert pe.switch_view({"status": "short_history"}, CFG)["verdict"] == "insufficient_data"


def test_futures_for():
    from gpwlib.gpw_archive import parse_futures_symbol
    rows = [{"name": "FPKOZ26", "close": 119.6, "turnover_k": 5000.0},
            {"name": "FPKOH27", "close": 120.1, "turnover_k": 100.0},
            {"name": "FPEOZ26", "close": 269.0, "turnover_k": 800.0}]
    f = pe.futures_for("PKOBP", rows, parse_futures_symbol)
    assert f["available"] and f["code"] == "PKO" and f["series"][0]["symbol"] == "FPKOZ26"
    assert f["turnover_k_total"] == 5100.0
    assert pe.futures_for("BOS", rows, parse_futures_symbol)["available"] is False
    assert pe.futures_for("PKOBP", None, parse_futures_symbol)["available"] is None


def test_sparkline_ascii_chart_compact():
    z = [i / 10.0 - 3 for i in range(70)]
    assert len(pe.sparkline(z, width=60)) == 60
    rows = pe.ascii_chart(z, width=60)
    assert len(rows) == 7 and rows[0].startswith("+3 |") and rows[3].startswith(" 0 |")
    assert all(len(r) == len(rows[0]) for r in rows)
    c = pe.compact(list(range(250)), list(range(250)), 120)
    assert len(c["values"]) <= 120 and c["values"][-1] == 249
    assert pe.sparkline([None]) == "" and pe.ascii_chart([]) == []
