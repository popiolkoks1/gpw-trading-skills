"""Tests for the gpwlib 1.1 additions: real-page parser fixture, brokers, tax, stats, journal_io."""

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

from gpwlib import brokers, gpw_archive, journal_io, stats, tax  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "gpw_archive_shares_2026-11-13.html"


# --- parser on a real (trimmed) GPW archive page ------------------------------

def test_parser_on_real_gpw_page_layout():
    rows = gpw_archive.parse_archive_html(FIXTURE.read_text(encoding="utf-8"))
    by = {r["name"]: r for r in rows}
    assert set(by) == {"11BIT", "CDPROJEKT", "KGHM", "PKOBP"}
    pko = by["PKOBP"]
    assert pko["currency"] == "PLN"
    assert pko["close"] == pytest.approx(119.9) and pko["change_pct"] == pytest.approx(1.7)
    assert pko["turnover_k"] == pytest.approx(425164.47)
    assert by["KGHM"]["low"] == pytest.approx(276.0)


def test_etf_type_registered():
    assert gpw_archive.TYPE_ETF == 241
    assert "type=241" in gpw_archive.archive_url(dt.date(2026, 11, 13), gpw_archive.TYPE_ETF)


# --- brokers --------------------------------------------------------------------

def test_commission_min_fee_and_zero():
    assert brokers.commission(1000, 0.39, 5) == 5.0
    assert brokers.commission(10000, 0.39, 5) == 39.0
    assert brokers.commission(0, 0.39, 5) == 0.0
    assert brokers.commission(5000, 0.0, 0.0) == 0.0


def test_resolve_overrides_and_unknown():
    b = brokers.resolve("mbank", pct=0.2)
    assert b["pct"] == 0.2 and b["min_fee"] == 5.0 and b["overridden"]
    assert not brokers.resolve("xtb")["overridden"]
    with pytest.raises(ValueError):
        brokers.resolve("nosuch")


def test_tariff_notes_age():
    fresh = brokers.tariff_notes("pl", today=brokers.TARIFF_ENTERED)
    assert "wpisano" in fresh[0]
    old = brokers.tariff_notes("en", today=brokers.TARIFF_ENTERED + dt.timedelta(days=400))
    assert "400 days old" in old[0]
    assert brokers.tariff_notes("pl", overridden=True) == []


# --- tax ------------------------------------------------------------------------

def test_limits_known_years():
    assert tax.ike_limit(2027) == 30099 and tax.ikze_limit(2027) == pytest.approx(12039.60)
    assert tax.ikze_limit(2026, self_employed=True) == 16956
    with pytest.raises(KeyError):
        tax.ike_limit(2030)


def test_oki_rate_rules():
    assert tax.oki_rate_pct(2027) == 0.85
    assert tax.oki_rate_pct(2028, nbp_reference_rate_pct=4.0) == 0.76     # 19% x 4.00 = 0.76
    assert tax.oki_rate_pct(2028, nbp_reference_rate_pct=3.75) == 0.71    # 0.7125 rounded DOWN
    assert tax.oki_rate_pct(2029, nbp_reference_rate_pct=0.1) == 0.10     # minimum 0.1%
    with pytest.raises(KeyError):
        tax.oki_rate_pct(2028)
    with pytest.raises(KeyError):
        tax.oki_rate_pct(2026)


def test_oki_tax_exemptions_art26():
    # Only Polish (qualified) assets below the limit: no tax.
    assert tax.oki_tax(80_000)["tax"] == 0
    # 150k qualified: 50k taxable at 0.85% = 425
    r = tax.oki_tax(150_000)
    assert r["exempt_qualified"] == 100_000 and r["tax"] == pytest.approx(425.0)
    # Savings first (max 25k), the rest of the 100k cap goes to qualified assets.
    r = tax.oki_tax(avg_qualified=100_000, avg_savings=40_000)
    assert r["exempt_savings"] == 25_000 and r["exempt_qualified"] == 75_000
    assert r["taxable"] == 40_000
    # A global ETF is taxed from the first zloty.
    r = tax.oki_tax(avg_qualified=0, avg_other=50_000)
    assert r["taxable"] == 50_000 and r["tax"] == pytest.approx(425.0)
    with pytest.raises(ValueError):
        tax.oki_tax(-1)


def test_ikze_refund_and_belka():
    assert tax.ikze_refund(12039.60, tax.MARGINAL_RATES["scale12"]) == pytest.approx(1444.75)
    assert tax.belka(1000) == 190.0 and tax.belka(-50) == 0.0


# --- stats ----------------------------------------------------------------------

def test_stats_basics():
    closes = [100, 110, 99, 130, 104]
    assert stats.total_return(closes) == pytest.approx(0.04)
    dd = stats.max_drawdown(closes)
    assert dd["mdd"] == pytest.approx(-0.20) and dd["peak_i"] == 3 and dd["trough_i"] == 4
    assert dd["current"] == pytest.approx(-0.20)
    assert stats.cagr([100] * 252 + [110]) == pytest.approx(0.10)
    assert stats.correlation([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert stats.correlation([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)
    assert stats.beta([0.02, -0.02, 0.04], [0.01, -0.01, 0.02]) == pytest.approx(2.0)
    assert stats.hhi([0.5, 0.5]) == pytest.approx(0.5)


def test_var_es_and_align():
    rets = [-0.05, -0.04] + [0.01] * 38
    assert stats.historical_var(rets, 0.95) == pytest.approx(0.04)   # 2nd worst of 40 (5% tail = 2 obs)
    assert stats.expected_shortfall(rets, 0.95) == pytest.approx(0.045)
    assert stats.historical_var(rets[:10]) is None
    al = stats.align({"A": {"d1": 1, "d2": 2, "d3": 3}, "B": {"d2": 5, "d3": 6}})
    assert al["dates"] == ["d2", "d3"] and al["closes"]["A"] == [2, 3]


# --- journal_io -----------------------------------------------------------------

def _write(tmp, th):
    d = tmp / "theses"
    d.mkdir(parents=True, exist_ok=True)
    (d / (th["id"] + ".json")).write_text(json.dumps(th), encoding="utf-8")


def test_journal_io_open_positions_fifo_and_paper(tmp_path):
    active = {"id": "a", "ticker": "PKOBP", "status": "ACTIVE", "account_type": "regular",
              "plan": {"stop": 100.0, "target": 140.0}, "setup": "trend",
              "fills": [{"date": "2026-01-02", "side": "buy", "shares": 10, "price": 110.0, "fee": 5.0},
                        {"date": "2026-02-02", "side": "buy", "shares": 10, "price": 120.0, "fee": 5.0},
                        {"date": "2026-03-02", "side": "sell", "shares": 15, "price": 130.0, "fee": 5.0}]}
    paper = dict(active, id="p", paper=True)
    closed = {"id": "c", "ticker": "KGHM", "status": "CLOSED", "fills": [],
              "outcome": {"closed_at": "2026-04-01", "net_pnl": -50.0, "r_multiple": -1.0, "followed_plan": True}}
    for th in (active, paper, closed):
        _write(tmp_path, th)
    (tmp_path / "theses" / "broken.json").write_text("{nope", encoding="utf-8")
    theses = journal_io.load_theses(tmp_path)
    assert len(theses) == 3
    pos = journal_io.open_positions(theses)
    assert len(pos) == 1
    p = pos[0]
    assert p["shares"] == 5 and p["avg_price"] == 120.0 and p["stop"] == 100.0  # FIFO left the 2nd lot
    assert len(journal_io.open_positions(theses, include_paper=True)) == 2
    res = journal_io.closed_results(theses)
    assert res == [{"thesis_id": "c", "ticker": "KGHM", "closed_at": "2026-04-01", "net_pnl": -50.0,
                    "r_multiple": -1.0, "followed_plan": True}]
    assert journal_io.load_theses(tmp_path / "missing") == []
