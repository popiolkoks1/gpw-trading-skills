import datetime as dt
import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import pytest

import trade_review as tr

ROOT = Path(__file__).resolve().parents[2]


def _journal_module():
    """Load gpw-trade-journal's journal.py under a unique name (skills are standalone)."""
    path = ROOT / "skills" / "gpw-trade-journal" / "scripts" / "journal.py"
    spec = importlib.util.spec_from_file_location("gpw_journal_for_tests", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def closed(tid, net, *, followed=True, r=None, closed_at="2026-05-10", setup="breakout", acct="regular",
           hold=10, excess=None, lessons="", stop_ok=True, fees=10.0):
    return {
        "schema_version": "1.0", "id": tid, "ticker": tid.upper(), "status": "CLOSED", "setup": setup,
        "account_type": acct, "fills": [],
        "outcome": {"closed_at": closed_at, "net_pnl": net, "return_pct": net / 10, "r_multiple": r,
                    "holding_days": hold, "exit_reason": "stop" if net < 0 else "target",
                    "followed_plan": followed, "process": {"entry": True, "stop": stop_ok, "size": followed or not stop_ok},
                    "excess_return_pct": excess, "benchmark": {"return_pct": 1.0} if excess is not None else None,
                    "lessons": lessons, "fees": fees},
    }


def test_stats_core_metrics():
    rows = tr.closed_trades([closed("a", 300, r=1.5), closed("b", -100, r=-1), closed("c", 200, r=1), closed("d", -100, r=-1)],
                            dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    st = tr.stats(rows)
    assert st["trades"] == 4 and st["wins"] == 2 and st["win_rate_pct"] == 50.0
    assert st["profit_factor"] == pytest.approx(2.5)
    assert st["expectancy"] == pytest.approx(75.0)
    assert st["payoff_ratio"] == pytest.approx(2.5)
    assert st["avg_r"] == pytest.approx(0.125, abs=0.006)
    assert st["small_sample"] is True


def test_max_drawdown_on_closed_equity():
    ths = [closed("a", 100, closed_at="2026-01-01"), closed("b", -150, closed_at="2026-01-02"),
           closed("c", -50, closed_at="2026-01-03"), closed("d", 300, closed_at="2026-01-04")]
    st = tr.stats(tr.closed_trades(ths, dt.date(2026, 1, 1), dt.date(2026, 12, 31)))
    assert st["max_drawdown_pln"] == pytest.approx(200.0)


def test_profit_factor_no_losses_is_flagged_not_inf():
    st = tr.stats(tr.closed_trades([closed("a", 100)], dt.date(2026, 1, 1), dt.date(2026, 12, 31)))
    assert st["profit_factor"] is None and st["profit_factor_no_losses"] is True
    json.dumps(st, allow_nan=False)


def test_process_matrix_quadrants():
    rows = tr.closed_trades([closed("a", 100, followed=True), closed("b", 100, followed=False),
                             closed("c", -50, followed=True), closed("d", -80, followed=False, stop_ok=False)],
                            dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    pm = tr.process_matrix(rows)
    assert pm["counts"] == {"deserved_win": 1, "lucky_win": 1, "bad_luck_loss": 1, "deserved_loss": 1}
    assert pm["plan_adherence_pct"] == 50.0
    assert pm["by_rule_broken"]["stop"] == 1


def test_period_filter_month():
    ths = [closed("a", 1, closed_at="2026-04-30"), closed("b", 2, closed_at="2026-05-01"), closed("c", 3, closed_at="2026-05-31")]
    s, e, label = tr.period_bounds("2026-05", None)
    assert label == "2026-05" and [r["id"] for r in tr.closed_trades(ths, s, e)] == ["b", "c"]
    s, e, _ = tr.period_bounds("2026-12", None)
    assert e == dt.date(2026, 12, 31)


def test_insights_rules():
    rows = tr.closed_trades([closed("a", -300, r=-2, followed=False, stop_ok=False), closed("b", 100, r=0.5, excess=-3.0)],
                            dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    keys = {i["key"] for i in tr.insights(tr.stats(rows), tr.process_matrix(rows), rows)}
    assert {"i_small_sample", "i_pf_below_1", "i_stop_broken", "i_big_losses", "i_lag_wig"} <= keys


def test_round_pln_half_up():
    assert tr.round_pln(Decimal("443.49")) == 443
    assert tr.round_pln(Decimal("443.50")) == 444


def _thesis_with_fills(tid, fills, acct="regular"):
    return {"schema_version": "1.0", "id": tid, "ticker": tid.upper(), "status": "ACTIVE",
            "account_type": acct, "fills": fills, "outcome": None}


def test_pit38_fifo_year_of_sale_and_rounding():
    fills = [
        {"date": "2025-12-10", "side": "buy", "shares": 100, "price": 10.0, "fee": 5.0},
        {"date": "2026-02-01", "side": "buy", "shares": 100, "price": 12.0, "fee": 5.0},
        {"date": "2026-03-01", "side": "sell", "shares": 150, "price": 15.0, "fee": 8.78},
    ]
    p = tr.pit38([_thesis_with_fills("x", fills)], 2026)
    # FIFO: 100@10 + 50@12 = 1600 cost; buy fees 5 + 2.5; sell fee 8.78
    assert p["revenue"] == pytest.approx(2250.0)
    assert p["costs"] == pytest.approx(1600 + 7.5 + 8.78)
    assert p["income"] == pytest.approx(633.72)
    assert p["tax_base"] == 634 and p["tax"] == 120  # 634 * 0.19 = 120.46 -> 120
    assert tr.pit38([_thesis_with_fills("x", fills)], 2025)["revenue"] == 0


def test_pit38_loss_and_tax_wrapped_excluded():
    loss = [{"date": "2026-01-02", "side": "buy", "shares": 10, "price": 100.0, "fee": 5.0},
            {"date": "2026-01-20", "side": "sell", "shares": 10, "price": 80.0, "fee": 5.0}]
    ike = [{"date": "2026-01-02", "side": "buy", "shares": 10, "price": 10.0, "fee": 0},
           {"date": "2026-02-02", "side": "sell", "shares": 10, "price": 50.0, "fee": 0}]
    p = tr.pit38([_thesis_with_fills("l", loss), _thesis_with_fills("i", ike, "ike")], 2026)
    assert p["loss"] == pytest.approx(210.0) and p["tax"] == 0
    assert p["excluded_tax_wrapped"] == [{"id": "i", "account_type": "ike"}]


def test_pit38_loss_deduction_capped_at_income():
    f = [{"date": "2026-01-02", "side": "buy", "shares": 10, "price": 10.0, "fee": 0},
         {"date": "2026-02-02", "side": "sell", "shares": 10, "price": 20.0, "fee": 0}]
    p = tr.pit38([_thesis_with_fills("a", f)], 2026, loss_deduction=500)
    assert p["loss_deduction"] == 100 and p["tax"] == 0


def test_fifo_identical_to_journal():
    j = _journal_module()
    fills = [
        {"date": "2026-01-02", "side": "buy", "shares": 7, "price": 10.0, "fee": 5.0},
        {"date": "2026-01-03", "side": "buy", "shares": 5, "price": 11.0, "fee": 5.0},
        {"date": "2026-01-04", "side": "sell", "shares": 9, "price": 12.0, "fee": 5.0},
        {"date": "2026-01-05", "side": "buy", "shares": 4, "price": 9.0, "fee": 5.0},
        {"date": "2026-01-06", "side": "sell", "shares": 7, "price": 13.0, "fee": 5.0},
    ]
    assert tr.fifo_matches(fills) == j.fifo_matches(fills)


def test_end_to_end_with_real_journal(tmp_path, capsys):
    j = _journal_module()
    th = j.new_thesis(ticker="KGHM", thesis="Copper", pillars=["Copper up"], kill_criteria=["Copper < 8k"],
                      entry=150.0, stop=140.0, today=dt.date(2026, 3, 1))
    j.add_fill(th, "buy", 10, 150.0, 5.85, dt.date(2026, 3, 2))
    j.close(th, day=dt.date(2026, 3, 20), exit_reason="stop", followed_entry=True, followed_stop=True,
            followed_size=True, price=140.0, fee=5.46, lessons="Respected the stop")
    j.save(tmp_path, th)
    out = tmp_path / "out"
    rc = tr.main(["--journal-dir", str(tmp_path), "--year", "2026", "--output-dir", str(out), "--lang", "en"])
    assert rc == 0
    md = capsys.readouterr().out
    assert "bad-luck loss" in md and "PIT-38" in md and tr.DISCLAIMER["en"] in md
    rep = json.loads(next(out.glob("*.json")).read_text(encoding="utf-8"))
    assert rep["process"]["counts"]["bad_luck_loss"] == 1
    assert rep["pit38"]["loss"] == pytest.approx(111.31)


def test_empty_journal_exit_code(tmp_path, capsys):
    assert tr.main(["--journal-dir", str(tmp_path), "--no-files"]) == 1
    assert "no journal entries" in capsys.readouterr().err


def test_paper_trades_never_mixed_with_real_or_pit38():
    real = closed("real", 100.0, closed_at="2026-05-10")
    paper = dict(closed("pap", -50.0, closed_at="2026-05-11"), paper=True)
    rep = tr.build_report([real, paper], year=2026, pit_year=2026)
    assert [t["id"] for t in rep["trades"]] == ["real"] and rep["other_mode_trades"] == 1
    assert "pit38" in rep
    prep = tr.build_report([real, paper], year=2026, pit_year=2026, paper=True)
    assert [t["id"] for t in prep["trades"]] == ["pap"] and "pit38" not in prep
    assert "na niby" in tr.render(prep, "pl")
    fills = [{"date": "2026-01-02", "side": "buy", "shares": 10, "price": 10.0, "fee": 0},
             {"date": "2026-02-02", "side": "sell", "shares": 10, "price": 50.0, "fee": 0}]
    th = dict(_thesis_with_fills("p", fills), paper=True)
    assert tr.pit38([th], 2026)["revenue"] == 0
