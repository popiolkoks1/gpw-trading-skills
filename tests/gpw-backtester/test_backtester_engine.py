import math

import pytest

import backtester_engine as be
import backtester_strategies as bs
from backtester_fixtures import bars_from_closes, market, sessions

NO_SLIP = {"cost_mult": 0.0, "impact_coef": 0.0}


def rule(entry_above, exit_above=None, **params):
    doc = {"entry": {"left": {"ind": "close"}, "op": ">", "right": entry_above},
           "params": dict({"max_positions": 1, "min_turnover_k": 0, "cooldown": 1000}, **params)}
    if exit_above is not None:
        doc["exit"] = {"left": {"ind": "close"}, "op": ">", "right": exit_above}
    return doc


def one_trade_market():
    days = sessions(60)
    closes = [10.0] * 30 + [11.0] * 10 + [13.0] * 20
    return days, market({"AAA": bars_from_closes(days, closes)}, days)


def test_one_trade_exact_pnl_min_fee_and_tax():
    days, md = one_trade_market()
    st = bs.make("rules", rules=bs.parse_rules(rule(10.5, 12.5)))
    r = be.simulate(md, st, days[0], days[-1], dict(NO_SLIP, capital=1000.0))
    assert len(r["trades"]) == 1
    t = r["trades"][0]
    # signal at the close of day 30 -> fill at the open of day 31
    assert t["entry_date"] == days[31] and t["exit_date"] == days[41]
    assert t["shares"] == 90  # 1000 // 11 at the signal close
    # 990 zl and 1170 zl orders: 0.39% < 5 zl -> minimum fee both ways
    assert t["fees"] == 10.0
    assert t["pnl_pln"] == pytest.approx(170.0)
    assert r["stats"]["min_fee_orders"] == 2
    # Belka: base 170 zl -> 19% = 32.30 -> 32 zl (Ordynacja art. 63)
    assert r["tax_years"][0]["tax"] == 32.0
    assert r["final_equity"] == pytest.approx(1138.0)


def test_ike_has_no_tax():
    days, md = one_trade_market()
    st = bs.make("rules", rules=bs.parse_rules(rule(10.5, 12.5)))
    r = be.simulate(md, st, days[0], days[-1], dict(NO_SLIP, capital=1000.0, account="ike"))
    assert r["final_equity"] == pytest.approx(1170.0)


def test_slippage_buckets_and_stress():
    cfg = dict(be.DEFAULT_CONFIG, impact_coef=0.0)
    assert be.slippage_bps(10e6, 1000, 0.02, cfg) == 5.0
    assert be.slippage_bps(300e3, 1000, 0.02, cfg) == 25.0
    assert be.slippage_bps(10e3, 1000, 0.02, cfg) == 100.0
    assert be.slippage_bps(300e3, 1000, 0.02, dict(cfg, cost_mult=2.0)) == 50.0
    cfg2 = dict(be.DEFAULT_CONFIG)  # sqrt impact: 0.5 * 2% * sqrt(10%) = 31.6 bps on top of 25
    assert be.slippage_bps(1e6 - 1, 1e5, 0.02, cfg2) == pytest.approx(25 + 0.5 * 0.02 * math.sqrt(0.1) * 1e4, rel=1e-3)
    assert be.slippage_bps(1, 1e9, 0.5, cfg2) == be.MAX_SLIPPAGE_BPS


def test_gap_through_stop_fills_at_open():
    days = sessions(50)
    closes = [10.0] * 30 + [11.0] * 20
    opens = [None] * 50
    lows = [None] * 50
    opens[33], lows[33] = 9.0, 8.8  # gap far below the 8% stop (11 * 0.92 = 10.12)
    closes[33] = 9.2
    md = market({"AAA": bars_from_closes(days, closes, opens=opens, lows=lows)}, days)
    st = bs.make("rules", rules=bs.parse_rules(dict(rule(10.5), stop={"pct": 8})))
    r = be.simulate(md, st, days[0], days[-1], dict(NO_SLIP, capital=10000.0))
    t = r["trades"][0]
    assert t["reason"] == "gap_stop" and t["exit_date"] == days[33]
    assert t["exit_price"] == pytest.approx(9.0)
    assert t["r_multiple"] < -1.0  # gap made the loss bigger than 1R


def test_intraday_stop_fills_at_stop_price():
    days = sessions(50)
    closes = [10.0] * 30 + [11.0] * 20
    lows = [None] * 50
    lows[34] = 9.5
    md = market({"AAA": bars_from_closes(days, closes, lows=lows)}, days)
    st = bs.make("rules", rules=bs.parse_rules(dict(rule(10.5), stop={"pct": 8})))
    r = be.simulate(md, st, days[0], days[-1], dict(NO_SLIP, capital=10000.0))
    t = r["trades"][0]
    assert t["reason"] == "stop" and t["exit_price"] == pytest.approx(11.0 * 0.92)


def test_liquidity_cap_limits_order():
    days = sessions(50)
    closes = [10.0] * 30 + [11.0] * 20
    md = market({"AAA": bars_from_closes(days, closes, turnover=100_000.0)}, days)
    st = bs.make("rules", rules=bs.parse_rules(dict(rule(10.5, 100.0), stop={"pct": 8})))
    r = be.simulate(md, st, days[0], days[-1], dict(NO_SLIP, capital=100_000.0, max_adv_pct=10.0))
    t = r["trades"][0]
    assert t["shares"] == int(100_000 * 0.10 // 11.0)  # 10% of the 100k zl median turnover
    assert r["stats"]["entries_capped_liquidity"] == 1


def test_no_quote_day_skips_entry_and_delays_exit():
    days = sessions(60)
    closes = [10.0] * 30 + [11.0] * 10 + [13.0] * 20
    rows = bars_from_closes(days, closes)
    rows = [r for k, r in enumerate(rows) if k != 31]  # no trade on the session after the signal
    md = market({"AAA": rows}, days)
    st = bs.make("rules", rules=bs.parse_rules(rule(10.5, 12.5)))
    r = be.simulate(md, st, days[0], days[-1], dict(NO_SLIP, capital=1000.0))
    t = r["trades"][0]
    assert r["stats"]["entries_skipped_no_quote"] == 1  # the day-30 order expires
    assert t["entry_date"] == days[33]  # new signal at the close of day 32 -> open of day 33
    assert t["exit_date"] == days[41]


def test_exit_delayed_when_no_bar():
    days = sessions(60)
    closes = [10.0] * 30 + [11.0] * 10 + [13.0] * 20
    rows = [r for k, r in enumerate(bars_from_closes(days, closes)) if k != 41]
    md = market({"AAA": rows}, days)
    st = bs.make("rules", rules=bs.parse_rules(rule(10.5, 12.5)))
    r = be.simulate(md, st, days[0], days[-1], dict(NO_SLIP, capital=1000.0))
    assert r["trades"][0]["exit_date"] == days[42]
    assert r["stats"]["exits_delayed_no_quote"] == 1


def test_risk_sizing_matches_budget():
    days, md = one_trade_market()
    st = bs.make("rules", rules=bs.parse_rules(dict(rule(10.5, 12.5), stop={"pct": 10})))
    r = be.simulate(md, st, days[0], days[-1], dict(NO_SLIP, capital=100_000.0, sizing="risk", risk_pct=1.0,
                                                    max_position_pct=100.0))
    t = r["trades"][0]
    # budget 1000 zl, risk/share 1.10 + ~69 zl of round-trip fees -> (1000 - 69) / 1.1 = 846 shares
    assert t["shares"] == 846
    risk = t["shares"] * 1.1 + be.fee(t["shares"] * 11.0, be.DEFAULT_CONFIG) + be.fee(t["shares"] * 9.9, be.DEFAULT_CONFIG)
    assert risk <= 1000.0 + 1e-6


def test_risk_sizing_requires_stop():
    days, md = one_trade_market()
    st = bs.make("rules", rules=bs.parse_rules(rule(10.5, 12.5)))
    with pytest.raises(ValueError):
        be.simulate(md, st, days[0], days[-1], dict(NO_SLIP, sizing="risk"))


def test_tax_is_paid_on_30_april_next_year():
    days = sessions(400, start=__import__("datetime").date(2021, 1, 4))
    closes = [10.0] * 30 + [11.0] * 10 + [13.0] * 360
    md = market({"AAA": bars_from_closes(days, closes)}, days)
    st = bs.make("rules", rules=bs.parse_rules(rule(10.5, 12.5)))
    r = be.simulate(md, st, days[0], days[-1], dict(NO_SLIP, capital=1000.0))
    assert r["tax_paid"] == {2021: 32.0}
    # equity is after the accrued liability, so the payment itself does not move it
    k = next(i for i, d in enumerate(r["dates"]) if d >= "2022-04-30")
    assert r["equity"][k] == pytest.approx(r["equity"][k - 1])
    assert r["final_equity"] == pytest.approx(1138.0)


def test_loss_carry_forward_rules():
    led = be.TaxLedger("pl_2019")
    led.add(2021, -1000.0)
    led.close_year(2021)
    led.add(2022, 1500.0)
    y = led.close_year(2022)
    assert y["loss_deducted"] == 1000.0 and y["base"] == 500.0 and y["tax"] == 95.0
    led = be.TaxLedger("50pct")
    led.add(2021, -1000.0)
    led.close_year(2021)
    led.add(2022, 1500.0)
    assert led.close_year(2022)["tax"] == 190.0  # only 500 deductible
    led.add(2023, 1500.0)
    y = led.close_year(2023)
    assert y["loss_deducted"] == 500.0 and y["tax"] == 190.0
    led.add(2024, 1000.0)
    assert led.close_year(2024)["loss_deducted"] == 0.0  # fully used


def test_loss_expires_after_five_years():
    led = be.TaxLedger("pl_2019")
    led.add(2019, -1000.0)
    led.close_year(2019)
    for y in range(2020, 2025):
        led.close_year(y)
    led.add(2025, 2000.0)
    assert led.close_year(2025)["loss_deducted"] == 0.0  # 2019 loss usable only in 2020-2024
    led2 = be.TaxLedger("pl_2019")
    led2.add(2019, -1000.0)
    led2.close_year(2019)
    led2.add(2024, 2000.0)
    assert led2.close_year(2024)["loss_deducted"] == 1000.0


def test_tax_rounding():
    assert be.round_zl(10.49) == 10.0
    assert be.round_zl(10.5) == 11.0


def test_momentum_rotation_holds_top_names():
    days = sessions(320)
    up = [10.0 * (1.002 ** k) for k in range(320)]
    mid = [10.0 * (1.001 ** k) for k in range(320)]
    down = [10.0 * (0.999 ** k) for k in range(320)]
    md = market({"UP": bars_from_closes(days, up), "MID": bars_from_closes(days, mid),
                 "DOWN": bars_from_closes(days, down)}, days)
    st = bs.make("momentum_rotation", {"top_n": 1, "lookback": 126, "skip": 21, "min_turnover_k": 0})
    r = be.simulate(md, st, days[200], days[-1], dict(NO_SLIP, capital=10000.0))
    assert {t["name"] for t in r["trades"]} == {"UP"}


def test_dca_wig_proxy_with_contributions():
    days = sessions(260)
    wig = [(d, 1e5 * 1.0004 ** k, 1e5 * 1.0004 ** k, 1e5 * 1.0004 ** k, 1e5 * 1.0004 ** k, 1e9) for k, d in enumerate(days)]
    md = market({"AAA": bars_from_closes(days, [10.0] * 260)}, days, wig=wig)
    st = bs.make("dca_buy_hold", {"monthly": 1000.0, "ter_pct": 0.0})
    r = be.simulate(md, st, days[0], days[-1], dict(NO_SLIP, capital=10000.0, account="ike"))
    assert r["contributions"] == pytest.approx(1000.0 * 11)
    assert len(r["trades"]) == 1 and r["trades"][0]["reason"] == "end_of_test"
    twr = 1.0
    for x in r["returns"]:
        twr *= 1 + x
    assert twr - 1 < 1.0004 ** 259 - 1  # costs make the TWR a bit lower than the index
    assert twr - 1 > 1.0004 ** 259 - 1 - 0.03


def test_signal_uses_next_open_not_same_close():
    days = sessions(45)
    closes = [10.0] * 30 + [11.0] * 15
    opens = [None] * 45
    opens[31] = 12.0  # the fill must happen at this open, not at the 11.0 signal close
    md = market({"AAA": bars_from_closes(days, closes, opens=opens)}, days)
    st = bs.make("rules", rules=bs.parse_rules(rule(10.5, 100.0)))
    r = be.simulate(md, st, days[0], days[-1], dict(NO_SLIP, capital=10000.0))
    assert r["trades"][0]["entry_price"] == pytest.approx(12.0)


def test_config_validation():
    days, md = one_trade_market()
    st = bs.make("rules", rules=bs.parse_rules(rule(10.5, 12.5)))
    with pytest.raises(ValueError):
        be.simulate(md, st, days[0], days[-1], {"account": "ikze"})
    with pytest.raises(ValueError):
        be.simulate(md, st, days[0], days[-1], {"capital": 0})
    with pytest.raises(ValueError):
        be.simulate(md, st, days[5], days[5])
