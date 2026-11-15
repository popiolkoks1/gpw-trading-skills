import json

import pytest

import position_sizer as ps


def base(**kw):
    p = {
        "account_size": 20000.0,
        "entry": 50.0,
        "stop": 46.0,
        "broker": "mbank",
        "max_position_pct": 25.0,
        "max_adv_pct": 10.0,
        "gap_pct": 10.0,
        "account_type": "regular",
    }
    p.update(kw)
    return p


def test_commission_min_fee_and_pct():
    assert ps.commission(1000, 0.39, 5) == 5.0
    assert ps.commission(10000, 0.39, 5) == 39.0
    assert ps.commission(0, 0.39, 5) == 0.0
    assert ps.commission(10000, 0.0, 0.0) == 0.0


def test_risk_includes_round_trip_fees_and_respects_budget():
    res = ps.calculate(base(risk_pct=1.0))
    assert res["shares"] == 45  # 50 shares would exceed 200 PLN once fees are added
    assert res["risk"]["total"] <= res["risk_budget"]
    nxt = ps.round_trip_risk(res["shares"] + 1, 50, 46, 0.39, 5)
    assert nxt["total"] > res["risk_budget"]


def test_whole_shares_only():
    res = ps.calculate(base(entry=3.37, stop=3.11, risk_pct=0.7))
    assert isinstance(res["shares"], int)


def test_max_shares_binary_search_matches_bruteforce():
    for budget in (50, 123.45, 999):
        got = ps.max_shares_for_budget(budget, 12.3, 11.1, 0.39, 5)
        brute = 0
        for n in range(0, 2000):
            if ps.round_trip_risk(n, 12.3, 11.1, 0.39, 5)["total"] <= budget:
                brute = n
        assert got == brute


def test_max_position_constraint_binding():
    res = ps.calculate(base(entry=100, stop=99, risk_pct=1.0))
    assert res["shares"] == 50  # 25% of 20k / 100
    binding = [c for c in res["constraints"] if c["binding"]]
    assert binding and binding[0]["type"] == "max_position_pct"


def test_liquidity_constraint_and_illiquid_warning():
    res = ps.calculate(base(entry=10, stop=9.5, risk_pct=1.0, median_turnover_pln=20000))
    assert res["shares"] == 200  # 10% of 20k PLN turnover / 10 PLN
    assert "warn_illiquid" in res["warnings"]


def test_atr_stop():
    res = ps.calculate(base(stop=None, atr=1.5, atr_multiplier=2.0, risk_pct=1.0))
    assert res["method"] == "atr"
    assert res["stop"] == pytest.approx(47.0)


def test_kelly_capped_and_zero_expectancy():
    k = ps.kelly_fraction(0.55, 2.0, 1.0)
    assert k["kelly_pct"] == pytest.approx(32.5)
    res = ps.calculate(base(win_rate=0.55, avg_win=2.0, avg_loss=1.0))
    assert res["risk_pct"] == ps.KELLY_RISK_CAP_PCT and res["kelly"]["capped"]
    with pytest.raises(ps.SizingError):
        ps.calculate(base(win_rate=0.3, avg_win=1.0, avg_loss=1.0))


def test_kelly_and_risk_pct_mutually_exclusive():
    with pytest.raises(ps.SizingError):
        ps.calculate(base(win_rate=0.5, avg_win=2, avg_loss=1, risk_pct=1))


def test_invalid_stop_above_entry():
    with pytest.raises(ps.SizingError):
        ps.calculate(base(stop=55))


def test_target_r_multiple_and_tax():
    res = ps.calculate(base(risk_pct=1.0, target=60))
    rw = res["reward"]
    assert rw["net"] == pytest.approx(rw["gross"] - rw["fees"])
    assert rw["after_tax"] == pytest.approx(rw["net"] * 0.81, abs=0.01)
    assert rw["r_multiple"] == pytest.approx(rw["net"] / res["risk"]["total"], abs=0.01)
    ike = ps.calculate(base(risk_pct=1.0, target=60, account_type="ike"))
    assert ike["reward"]["after_tax"] == ike["reward"]["net"]


def test_gap_scenario_worse_than_stop():
    res = ps.calculate(base(risk_pct=1.0))
    assert res["gap_scenario"]["loss"] > res["risk"]["total"]
    assert res["gap_scenario"]["fill_price"] == pytest.approx(46 * 0.9)


def test_fee_drag_warning_only_when_min_fee_binds():
    small = ps.calculate(base(entry=20, stop=19, risk_pct=0.25, max_position_pct=None))
    assert "warn_fee_drag" in small["warnings"]
    big = ps.calculate(base(risk_pct=1.0))
    assert "warn_fee_drag" not in big["warnings"]


def test_xtb_zero_commission_warning():
    res = ps.calculate(base(broker="xtb", risk_pct=1.0))
    assert res["risk"]["fee_entry"] == 0
    assert "warn_xtb" in res["warnings"]
    assert res["shares"] == 50


def test_markdown_bilingual_and_disclaimer():
    res = ps.calculate(base(risk_pct=1.0, target=60))
    pl = ps.render_markdown(res, "pl")
    en = ps.render_markdown(res, "en")
    assert "Liczba akcji" in pl and "zł" in pl and ps.DISCLAIMER["pl"] in pl
    assert "Shares" in en and "PLN" in en and ps.DISCLAIMER["en"] in en
    json.dumps(res)  # serialisable


def test_cli_writes_files(tmp_path):
    rc = ps.main(["--account-size", "10000", "--entry", "40", "--stop", "37", "--output-dir", str(tmp_path), "--lang", "en"])
    assert rc == 0
    assert list(tmp_path.glob("gpw_position_size_*.json"))
    assert list(tmp_path.glob("gpw_position_size_*.md"))


def test_cli_error_exit_code(capsys):
    rc = ps.main(["--account-size", "10000", "--entry", "40", "--stop", "45", "--no-files"])
    assert rc == 1
    assert "stop must be below entry" in capsys.readouterr().err
