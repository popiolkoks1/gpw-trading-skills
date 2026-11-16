import datetime as dt
import json

import pytest

import journal as j

D = dt.date


def mk(**kw):
    base = dict(ticker="pkobp", thesis="Bank benefits from high rates", pillars=["NIM stays high"],
                kill_criteria=["Close below 105"], entry=110.0, stop=104.0, target=125.0,
                today=D(2026, 9, 1))
    base.update(kw)
    return j.new_thesis(**base)


def test_new_thesis_requires_kill_criterion_and_pillars():
    with pytest.raises(j.JournalError, match="kill"):
        mk(kill_criteria=[])
    with pytest.raises(j.JournalError, match="pillars"):
        mk(pillars=[])
    with pytest.raises(j.JournalError, match="pillars"):
        mk(pillars=["a", "b", "c", "d", "e", "f"])


def test_new_thesis_defaults():
    th = mk()
    assert th["ticker"] == "PKOBP" and th["status"] == "IDEA"
    assert th["review_date"] == "2026-10-01"
    assert th["id"].startswith("th_pkobp_20260901_")


def test_plan_geometry_validated():
    with pytest.raises(j.JournalError):
        mk(stop=115.0)
    with pytest.raises(j.JournalError):
        mk(target=100.0)


def test_forward_only_transitions():
    th = mk()
    j.transition(th, "READY", D(2026, 9, 1))
    with pytest.raises(j.JournalError, match="forward-only"):
        j.transition(th, "IDEA", D(2026, 9, 2))
    with pytest.raises(j.JournalError, match="buy fill"):
        j.transition(th, "ACTIVE", D(2026, 9, 2))
    j.transition(th, "INVALIDATED", D(2026, 9, 3), "kill criterion hit before entry")
    with pytest.raises(j.JournalError, match="already"):
        j.transition(th, "READY", D(2026, 9, 4))


def test_buy_fill_activates_and_whole_shares_only():
    th = mk()
    j.add_fill(th, "buy", 10, 110.0, 5.0, D(2026, 9, 2))
    assert th["status"] == "ACTIVE"
    with pytest.raises(j.JournalError):
        j.add_fill(th, "sell", 11, 111.0, 5.0, D(2026, 9, 3))
    assert j.shares_held(th) == 10  # rejected fill rolled back


def test_fills_must_be_chronological():
    th = mk()
    j.add_fill(th, "buy", 10, 110.0, 5.0, D(2026, 9, 5))
    with pytest.raises(j.JournalError, match="earlier"):
        j.add_fill(th, "buy", 10, 110.0, 5.0, D(2026, 9, 4))


def test_fifo_matching_and_fee_allocation():
    fills = [
        {"date": "2026-09-02", "side": "buy", "shares": 40, "price": 110.0, "fee": 17.16},
        {"date": "2026-09-10", "side": "buy", "shares": 20, "price": 112.0, "fee": 8.74},
        {"date": "2026-10-15", "side": "sell", "shares": 50, "price": 118.0, "fee": 23.0},
    ]
    m = j.fifo_matches(fills)
    assert [(x["buy_date"], x["shares"]) for x in m] == [("2026-09-02", 40), ("2026-09-10", 10)]
    assert m[0]["buy_fee"] == pytest.approx(17.16)
    assert m[1]["buy_fee"] == pytest.approx(4.37)
    assert m[0]["sell_fee"] + m[1]["sell_fee"] == pytest.approx(23.0)


def test_full_lifecycle_pnl_r_and_process():
    th = mk()
    j.add_fill(th, "buy", 40, 110.0, 17.16, D(2026, 9, 2))
    j.add_fill(th, "buy", 20, 112.0, 8.74, D(2026, 9, 10))
    j.add_fill(th, "sell", 30, 118.0, 13.81, D(2026, 10, 15))
    j.add_update(th, D(2026, 11, 5), "Q3 beat", pillar=1, conviction="H", stop=112.0)  # trailing stop above entry ok
    assert th["plan"]["initial_stop"] == 104.0
    j.close(th, day=D(2026, 11, 13), exit_reason="target", followed_entry=True, followed_stop=True,
            followed_size=False, lessons="Adding without a plan", price=119.9, fee=14.03,
            benchmark={"index": "WIG", "return_pct": 1.92})
    o = th["outcome"]
    assert th["status"] == "CLOSED"
    assert o["gross_pnl"] == pytest.approx(497.0)
    assert o["fees"] == pytest.approx(53.74)
    assert o["net_pnl"] == pytest.approx(443.26)
    assert o["r_multiple"] == pytest.approx(1.11)  # risk measured from the INITIAL stop
    assert o["holding_days"] == 72
    assert o["process_score"] == pytest.approx(2 / 3, abs=1e-3)
    assert o["followed_plan"] is False
    assert o["excess_return_pct"] == pytest.approx(o["return_pct"] - 1.92, abs=1e-3)


def test_close_requires_price_when_shares_held():
    th = mk()
    j.add_fill(th, "buy", 10, 110.0, 5.0, D(2026, 9, 2))
    with pytest.raises(j.JournalError, match="still held"):
        j.close(th, day=D(2026, 9, 9), exit_reason="stop", followed_entry=True, followed_stop=True, followed_size=True)


def test_invalidate_with_open_position_refused():
    th = mk()
    j.add_fill(th, "buy", 10, 110.0, 5.0, D(2026, 9, 2))
    with pytest.raises(j.JournalError, match="still open"):
        j.transition(th, "INVALIDATED", D(2026, 9, 3))


def test_review_due():
    a, b = mk(review_date="2026-09-10"), mk(review_date="2026-12-01")
    due = j.review_due([a, b], D(2026, 9, 15))
    assert [x["review_date"] for x in due] == ["2026-09-10"]


def test_storage_roundtrip_and_prefix_lookup(tmp_path):
    th = mk()
    j.save(tmp_path, th)
    assert j.load(tmp_path, th["id"])["thesis"] == th["thesis"]
    assert j.load(tmp_path, th["id"][:12])["id"] == th["id"]
    with pytest.raises(j.JournalError):
        j.load(tmp_path, "../etc")


def test_render_bilingual():
    th = mk()
    assert "Kryteria unieważnienia" in j.render_thesis(th, "pl")
    assert "Kill criteria" in j.render_thesis(th, "en")
    assert j.DISCLAIMER["en"] in j.render_thesis(th, "en")


def test_cli_end_to_end(tmp_path, capsys):
    sd = ["--state-dir", str(tmp_path)]
    assert j.main(sd + ["--json", "add", "--ticker", "KGHM", "--thesis", "Copper up", "--pillar", "Copper > 10k USD",
                         "--kill", "Copper < 8k USD", "--entry", "150", "--stop", "140", "--date", "2026-10-01"]) == 0
    tid = json.loads(capsys.readouterr().out)["id"]
    assert j.main(["fill", tid, "--side", "buy", "--shares", "10", "--price", "150", "--fee", "5.85", "--date", "2026-10-02"] + sd) == 0
    assert j.main(["close", tid, "--price", "140", "--fee", "5.46", "--date", "2026-10-09", "--exit-reason", "stop",
                   "--followed-entry", "tak", "--followed-stop", "tak", "--followed-size", "tak", "--no-benchmark"] + sd) == 0
    th = j.load(tmp_path, tid)
    assert th["outcome"]["r_multiple"] == pytest.approx(-1.11, abs=0.01)
    assert j.main(["transition", tid, "READY"] + sd) == 1  # closed theses are frozen


def test_cli_rejects_missing_kill(tmp_path, capsys):
    rc = j.main(["--state-dir", str(tmp_path), "add", "--ticker", "PZU", "--thesis", "x", "--pillar", "y"])
    assert rc == 1
    assert "kill criterion" in capsys.readouterr().err


def test_paper_flag_stored_and_validated():
    th = mk(paper=True)
    assert th["paper"] is True
    assert mk()["paper"] is False
    th["paper"] = "yes"
    with pytest.raises(j.JournalError, match="paper"):
        j.validate(th)
    md = j.render_thesis(mk(paper=True), "pl")
    assert "na niby" in md
