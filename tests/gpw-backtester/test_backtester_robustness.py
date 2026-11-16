import random

import pytest

import backtester_metrics as bm
import backtester_robustness as br
from backtester_fixtures import sessions


def test_dsr_matches_bailey_lopez_de_prado_example():
    # Bailey & Lopez de Prado (2014), numerical example: SR 2.5 annual, T = 1250 daily obs,
    # N = 100 trials with V[SR] = 0.5 annual, skew -3, kurtosis 10 -> SR0 = 0.1132 daily, DSR ~ 0.90
    sr = 2.5 / 250 ** 0.5
    sr0 = br.expected_max_sr(0.5 / 250, 100)
    assert sr0 == pytest.approx(0.1132, abs=5e-4)
    dsr = br.psr(sr, 1250, -3.0, 10.0, sr0)
    assert dsr == pytest.approx(0.9004, abs=2e-3)


def test_expected_max_sr_grows_with_trials():
    assert br.expected_max_sr(0.01, 1) == 0.0
    assert br.expected_max_sr(0.01, 10) < br.expected_max_sr(0.01, 100) < br.expected_max_sr(0.01, 1000)


def test_deflated_sharpe_drops_with_more_trials():
    rnd = random.Random(1)
    rets = [rnd.gauss(0.0006, 0.01) for _ in range(1500)]
    one = br.deflated_sharpe(rets, 1)
    many = br.deflated_sharpe(rets, 200)
    assert one["available"] and many["available"]
    assert one["dsr"] == pytest.approx(one["psr"])
    assert many["dsr"] < one["dsr"]


def test_wf_splits_anchored_and_rolling():
    a = br.wf_splits(1000, 300, 100, anchored=True)
    assert a[0] == (0, 300, 300, 400) and a[-1] == (0, 900, 900, 1000) and len(a) == 7
    r = br.wf_splits(1000, 300, 100, anchored=False)
    assert r[1] == (100, 400, 400, 500)
    assert all(x[1] - x[0] == 300 for x in r)
    assert br.wf_splits(1050, 300, 100, True)[-1] == (0, 1000, 1000, 1050)  # partial last fold >= half
    with pytest.raises(ValueError):
        br.wf_splits(1000, 10, 100, True)


def test_walk_forward_picks_is_best_and_scores_oos():
    rnd = random.Random(3)
    n = 600
    good = [rnd.gauss(0.001, 0.01) for _ in range(n)]
    bad = [rnd.gauss(-0.001, 0.01) for _ in range(n)]
    dates = sessions(n + 1)
    wf = br.walk_forward(dates, {"good": good, "bad": bad}, 200, 100, True)
    assert wf["available"] and all(f["chosen"] == "good" for f in wf["folds"])
    assert wf["oos_is_ratio"] is not None and wf["oos_is_ratio"] > 0


def test_pbo_low_for_dominant_trial_high_for_noise():
    rnd = random.Random(5)
    T = 1200
    dominant = [[rnd.gauss(0.002 if j == 0 else 0.0, 0.01) for _ in range(T)] for j in range(6)]
    assert br.pbo_cscv(dominant)["pbo"] < 0.1
    noise = [[rnd.gauss(0.0, 0.01) for _ in range(T)] for _ in range(10)]
    p = br.pbo_cscv(noise)
    assert 0.2 < p["pbo"] < 0.8 and p["combinations"] == 924
    assert not br.pbo_cscv([noise[0]])["available"]


def test_plateau_detects_lone_peak():
    grid = {"a": [1, 2, 3], "b": [10, 20]}
    flat = {(1, 10): 1.0, (2, 10): 1.1, (3, 10): 1.0, (1, 20): 0.9, (2, 20): 1.0, (3, 20): 0.95}
    p = br.plateau(grid, flat)
    assert p["is_plateau"] and p["best"] == {"a": 2, "b": 10}
    peak = {(1, 10): 0.1, (2, 10): 2.0, (3, 10): 0.0, (1, 20): 0.1, (2, 20): 0.2, (3, 20): 0.1}
    assert not br.plateau(grid, peak)["is_plateau"]


def test_bootstrap_ci_deterministic_and_brackets_mean():
    xs = [1.0, -0.5, 2.0, -1.0, 0.5, 3.0, -2.0, 1.5]
    a, b = br.bootstrap_mean_ci(xs), br.bootstrap_mean_ci(xs)
    assert a == b and a["ci_low"] < a["mean"] < a["ci_high"]
    assert br.bootstrap_mean_ci([1.0, 2.0]) is None


def _metrics(**kw):
    m = {"cagr": 10.0, "max_dd": -20.0, "sharpe": 1.0, "t_stat": 3.5, "concentration": {"pnl_ex_top3_pln": 100.0}}
    m.update(kw)
    return m


GOOD = dict(stress={"cagr": 5.0}, boot={"ci_low": 0.5, "ci_high": 2.0}, dsr={"available": True, "dsr": 0.99},
            pbo={"available": True, "pbo": 0.1}, wf={"available": True, "oos_is_ratio": 0.8},
            plat={"available": True, "is_plateau": True}, wig_cagr=5.0)


def test_verdict_ladder():
    v = br.verdict(metrics=_metrics(), trades={"trades": 100, "win_rate": 45}, years=7, **GOOD)
    assert v["verdict"] == "ROBUST"
    v = br.verdict(metrics=_metrics(), trades={"trades": 10, "win_rate": 45}, years=7, **GOOD)
    assert v["verdict"] == "NOT_EVALUABLE"
    v = br.verdict(metrics=_metrics(max_dd=-55.0), trades={"trades": 100, "win_rate": 45}, years=7, **GOOD)
    assert v["verdict"] == "REJECT" and "max_dd_ge_50" in v["reject_reasons"]
    v = br.verdict(metrics=_metrics(), trades={"trades": 100, "win_rate": 45}, years=4, **GOOD)
    assert v["verdict"] == "FRAGILE" and "period_lt_5y" in v["blocking_reasons"]
    g = dict(GOOD, stress=None)
    v = br.verdict(metrics=_metrics(), trades={"trades": 100, "win_rate": 45}, years=7, **g)
    assert "no_cost_stress" in v["blocking_reasons"]
    v = br.verdict(metrics=_metrics(max_dd=-3.0), trades={"trades": 100, "win_rate": 95}, years=7, **GOOD)
    assert "too_good_to_be_true" in v["blocking_reasons"]
    g = dict(GOOD, wf=None, pbo=None, plat=None)
    v = br.verdict(metrics=_metrics(t_stat=2.0), trades={"trades": 100, "win_rate": 45}, years=7, **g)
    assert v["verdict"] == "PROMISING"
    assert {"no_walk_forward", "no_pbo", "t_stat_lt_3"} <= set(v["limiting_reasons"])
    v = br.verdict(metrics=_metrics(cagr=-1.0), trades={"trades": 100, "win_rate": 45}, years=7, **GOOD)
    assert v["verdict"] == "REJECT"
    v = br.verdict(metrics=_metrics(), trades={"trades": 100, "win_rate": 45}, years=7, strategy_kind="dca", **GOOD)
    assert v["verdict"] == "NOT_EVALUABLE" and "benchmark_strategy" in v["notes"]


def test_perf_metrics_known_values():
    dates = ["2020-01-01", "2020-07-01", "2021-01-01"]
    p = bm.perf(dates, [0.10, -0.10])
    assert p["total_return"] == pytest.approx(-1.0)
    assert p["max_dd"] == pytest.approx(-10.0)
    idx = bm.index_from([0.1, -0.5, 0.2])
    dd = bm.drawdown(idx, ["a", "b", "c", "d"])
    assert dd["max_dd"] == pytest.approx(-50.0) and dd["longest_from"] == "b" and dd["longest_to"] is None


def test_trade_stats_and_concentration():
    tr = [{"pnl_pln": 100.0, "ret_pct": 10.0, "r_multiple": 2.0, "sessions": 5, "reason": "signal", "exit_date": "2021-01-02", "name": "A"},
          {"pnl_pln": -50.0, "ret_pct": -5.0, "r_multiple": -1.0, "sessions": 3, "reason": "stop", "exit_date": "2021-01-03", "name": "B"},
          {"pnl_pln": -50.0, "ret_pct": -5.0, "r_multiple": -1.0, "sessions": 3, "reason": "stop", "exit_date": "2021-01-04", "name": "C"}]
    s = bm.trade_stats(tr)
    assert s["win_rate"] == pytest.approx(100 / 3)
    assert s["profit_factor"] == pytest.approx(1.0)
    assert s["expectancy_r"] == pytest.approx(0.0)
    assert s["max_consecutive_losses"] == 2
    c = bm.concentration(tr)
    assert c["pnl_ex_top3_pln"] == pytest.approx(-100.0)


def test_regime_from_report_rebuilds_daily_states():
    doc = {"regime": {"as_of": "2021-01-06"},
           "history": {"from": "2021-01-02", "state_before": "correction",
                       "transitions": [{"date": "2021-01-04", "from": "correction", "to": "rally_attempt"}]}}
    lab = bm.regime_from_report(doc, ["2021-01-01", "2021-01-02", "2021-01-04", "2021-01-05", "2021-01-07"])
    assert lab == {"2021-01-02": "correction", "2021-01-04": "rally_attempt", "2021-01-05": "rally_attempt"}
