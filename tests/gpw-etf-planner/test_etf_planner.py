import datetime as dt
import json

import pytest

import etf_catalog as cat
import etf_planner_engine as epe
import gpw_etf_planner as cli
from gpwlib import plcal


# --- catalog -------------------------------------------------------------------

def test_leverage_from_name_suffix():
    assert cat.name_leverage("ETFBW20LV") == 2.0
    assert cat.name_leverage("ETFBNQ3LV") == 3.0
    assert cat.name_leverage("ETFBNQ2ST") == -2.0
    assert cat.name_leverage("ETFBW20ST") == -1.0
    assert cat.name_leverage("ETFBW20TR") is None
    assert cat.name_leverage("ETFBCASH") is None


def test_catalog_consistency():
    for name, e in cat.CATALOG.items():
        assert e["category"] in cat.CATEGORIES
        lev = cat.name_leverage(name)
        assert (lev is not None) == (e["category"] in ("leveraged", "short")), name
        if e["oki_hint"] == "likely_qualified":
            assert e["category"] in ("pl_equity", "pl_bonds", "cash") and e["currency"] == "PLN", name
        assert "ter" not in " ".join(e).lower()  # TER is never stored


def test_classify_unknown_and_new_leveraged():
    u = cat.classify("ETFNEWFUND")
    assert u["category"] == "uncategorised" and not u["in_catalog"]
    n = cat.classify("ETFXYZ3LV")
    assert n["category"] == "leveraged" and n["leverage"] == 3.0 and n["is_leveraged_or_short"]
    assert cat.classify("ETFIWDA")["oki_hint"] == "not_qualified"
    assert cat.classify("ETFBW20TR")["oki_hint"] == "likely_qualified"


# --- stats -----------------------------------------------------------------------

def test_etf_stats_synthetic():
    n = 300
    closes = [100 * 1.001 ** i for i in range(n)]
    closes[150] = closes[149] * 0.8  # one-day dip
    dates = ["d{:03d}".format(i) for i in range(n)]
    st = epe.etf_stats(dates, closes, [50.0] * n)
    assert st["last_close"] == closes[-1]
    assert st["liquidity"] == "low" and st["median_turnover_k"] == 50.0
    assert st["return_1y"] == pytest.approx(closes[-1] / closes[-253] - 1)
    assert st["max_drawdown"] == pytest.approx(-0.2, abs=1e-9)
    short = epe.etf_stats(dates[:30], closes[:30], [5.0] * 30)
    assert short["return_1y"] is None and short["volatility"] is None and short["liquidity"] == "very_low"


def test_normalise_weights():
    assert epe.normalise_weights({"A": 30, "B": 70}) == {"A": 0.3, "B": 0.7}
    assert epe.normalise_weights({"A": 1, "B": 1})["A"] == 0.5
    with pytest.raises(ValueError):
        epe.normalise_weights({"A": 0})
    with pytest.raises(ValueError):
        epe.normalise_weights({"A": -1, "B": 2})


# --- buy list --------------------------------------------------------------------

def test_plan_buys_whole_units_no_fee():
    p = epe.plan_buys({"A": 100.0, "B": 100.0}, {"A": 0.5, "B": 0.5}, {}, 1000.0, 0.0, 0.0)
    units = {r["name"]: r["buy_units"] for r in p["rows"]}
    assert units == {"A": 5, "B": 5} and p["cash_left"] == 0


def test_plan_buys_min_fee_rounding_and_leftover():
    p = epe.plan_buys({"A": 100.0, "B": 100.0}, {"A": 0.5, "B": 0.5}, {}, 1000.0, 0.39, 5.0)
    units = {r["name"]: r["buy_units"] for r in p["rows"]}
    # 500 each -> 5 units cost 505 > 500 -> 4 units (405); left 190 -> one more A unit (+100)
    assert units == {"A": 5, "B": 4}
    assert p["fees"] == 10.0
    assert p["spent"] <= 1000.0 and p["cash_left"] == pytest.approx(1000 - 505 - 405)


def test_plan_buys_never_exceeds_cash_and_never_sells():
    prices = {"A": 89.18, "B": 587.6, "C": 227.05}
    w = {"A": 0.3, "B": 0.6, "C": 0.1}
    hold = {"A": 40, "B": 5, "C": 2}
    p = epe.plan_buys(prices, w, hold, 1150.0, 0.39, 5.0)
    assert p["spent"] <= 1150.0 + 1e-9
    assert all(r["buy_units"] >= 0 for r in p["rows"])
    a = next(r for r in p["rows"] if r["name"] == "A")
    assert a["buy_units"] == 0  # A is overweight (40 x 89 = 3567 of 7584) -> no new money


def test_plan_buys_cash_flow_targets_underweight():
    p = epe.plan_buys({"A": 10.0, "B": 10.0}, {"A": 0.5, "B": 0.5}, {"A": 100}, 1000.0, 0.0, 0.0)
    units = {r["name"]: r["buy_units"] for r in p["rows"]}
    assert units == {"A": 0, "B": 100}


def test_plan_buys_max_orders_rotation():
    p = epe.plan_buys({"A": 10.0, "B": 10.0, "C": 10.0}, {"A": 0.4, "B": 0.4, "C": 0.2},
                      {"A": 10, "B": 50, "C": 0}, 300.0, 0.39, 5.0, max_orders=1)
    bought = [r["name"] for r in p["rows"] if r["buy_units"] > 0]
    assert bought == ["A"] and p["fees"] == 5.0


def test_plan_buys_errors():
    with pytest.raises(ValueError):
        epe.plan_buys({"A": 10.0}, {"A": 1.0, "B": 0.0}, {}, 100.0, 0, 0)
    with pytest.raises(ValueError):
        epe.plan_buys({"A": 10.0}, {"A": 1.0}, {}, -1.0, 0, 0)


# --- frequency / rebalancing ------------------------------------------------------

def test_order_frequency_min_fee():
    f = epe.order_frequency(1000, {"A": 0.3, "B": 0.7}, 0.39, 5.0)
    assert f["per_etf"]["A"]["months"] == 4   # 1200 zł -> 5/1200 = 0.42%
    assert f["per_etf"]["B"]["months"] == 2
    assert f["suggested_every_months"] == 4 and f["too_infrequent"]
    assert f["min_order_value"] == pytest.approx(1000.0)
    assert f["rotation_one_order_drag_pct"] == pytest.approx(0.5)
    assert epe.order_frequency(1000, {"A": 0.3, "B": 0.7}, 0.0, 0.0)["suggested_every_months"] == 1
    assert epe.order_frequency(1000, {"A": 1.0}, 0.6, 0.0)["impossible"]


def test_rebalance_bands_5_25():
    r = epe.rebalance_check({"A": 870, "B": 130}, {"A": 0.9, "B": 0.1})
    rows = {x["name"]: x for x in r["rows"]}
    assert rows["B"]["band_pp"] == 2.5 and rows["B"]["out_of_band"]       # 13% vs 10% target
    assert rows["A"]["band_pp"] == 5.0 and not rows["A"]["out_of_band"]   # -3 pp
    r2 = epe.rebalance_check({"A": 660, "B": 340}, {"A": 0.6, "B": 0.4})
    assert r2["any_out_of_band"]                                         # +6 pp
    assert not epe.rebalance_check({}, {"A": 1.0})["any_out_of_band"]


# --- risk / composition -----------------------------------------------------------

def test_portfolio_index_and_composition():
    idx = epe.portfolio_index({"A": [100, 110, 99], "B": [100, 100, 100]}, {"A": 0.5, "B": 0.5})
    assert idx[1] == pytest.approx(1.05) and idx[2] == pytest.approx(1.05 * (1 + 0.5 * (99 / 110 - 1)))
    c = epe.composition({"ETFV60A": 0.5, "ETFBTBSP": 0.25, "ETFPZUGOLD": 0.25})
    assert c["equity"] == pytest.approx(0.3) and c["bonds_cash"] == pytest.approx(0.45) and c["other"] == pytest.approx(0.25)
    assert c["oki_likely_qualified"] == pytest.approx(0.25)
    c2 = epe.composition({"ETFBW20LV": 0.2, "ETFBW20TR": 0.8})
    assert c2["leveraged_or_short"] == pytest.approx(0.2) and c2["oki_likely_qualified"] == pytest.approx(0.8)


# --- backtest -------------------------------------------------------------------

def _dated(prices_by_day, start=dt.date(2025, 1, 1)):
    days = []
    d = start
    while len(days) < len(prices_by_day):
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += dt.timedelta(days=1)
    return days


def test_first_sessions_of_month_and_xirr():
    assert epe.first_sessions_of_month(["2025-01-02", "2025-01-03", "2025-02-03", "2025-03-03"]) == [0, 2, 3]
    flows = [(dt.date(2025, 1, 1), -100.0), (dt.date(2026, 1, 1), 110.0)]
    assert epe.xirr(flows) == pytest.approx(0.10, abs=1e-6)
    assert epe.xirr([(dt.date(2025, 1, 1), -100.0)]) is None


def test_backtest_flat_prices_value_equals_invested_minus_fees():
    n = 130
    days = _dated(range(n))
    data = {"A": {d: 50.0 for d in days}, "B": {d: 20.0 for d in days}}
    bt = epe.backtest(data, {"A": 0.5, "B": 0.5}, 1000.0, 0.0, 0.0)
    assert bt["dca"]["value"] == pytest.approx(bt["invested"])
    assert bt["lump_sum"]["value"] == pytest.approx(bt["invested"])
    assert bt["months"] == 7 and bt["too_short"]  # Jan-Jul 2025
    bt2 = epe.backtest(data, {"A": 0.5, "B": 0.5}, 1000.0, 0.39, 5.0)
    assert bt2["dca"]["value"] == pytest.approx(bt2["invested"] - bt2["dca"]["fees"], abs=0.01)
    assert bt2["dca"]["fees"] > bt2["lump_sum"]["fees"]


def test_backtest_rising_market_lump_sum_wins():
    n = 130
    days = _dated(range(n))
    data = {"A": {d: 50.0 * 1.002 ** i for i, d in enumerate(days)}}
    bt = epe.backtest(data, {"A": 1.0}, 1000.0, 0.0, 0.0)
    assert bt["lump_sum"]["value"] > bt["dca"]["value"] > bt["invested"] * 0.99


def test_backtest_needs_history():
    days = _dated(range(10))
    with pytest.raises(ValueError):
        epe.backtest({"A": {d: 1.0 for d in days}}, {"A": 1.0}, 100.0, 0, 0)


# --- CLI on a synthetic offline cache --------------------------------------------

def _write_cache(tmp_path, n=80, end=dt.date(2026, 11, 13)):
    days = []
    for d in plcal.previous_probable_sessions(end, 400):
        days.append(d)
        if len(days) == n:
            break
    days.reverse()
    folder = tmp_path / "archive" / "etf"
    folder.mkdir(parents=True)
    base = {"ETFBW20TR": 80.0, "ETFIWDA": 550.0, "ETFBW20LV": 120.0, "ETFNEWONE": 30.0}
    for i, d in enumerate(days):
        rows = []
        for k, p0 in base.items():
            close = round(p0 * (1 + 0.001 * i), 2)
            prev = round(p0 * (1 + 0.001 * (i - 1)), 2)
            rows.append({"name": k, "currency": "PLN", "open": close, "high": close, "low": close, "close": close,
                         "change_pct": round((close / prev - 1) * 100, 2) if i else 0.0, "turnover_k": 500.0})
        payload = {"schema": 1, "date": d.isoformat(), "type": "etf", "session": True, "rows": rows,
                   "source_url": "https://www.gpw.pl/archiwum-notowan", "retrieved_at": "2026-11-14T00:00:00+00:00"}
        (folder / "{}.json".format(d.isoformat())).write_text(json.dumps(payload), encoding="utf-8")
    return end


def test_cli_list_plan_backtest_offline(tmp_path, capsys):
    end = _write_cache(tmp_path)
    common = ["--offline", "--cache-dir", str(tmp_path), "--as-of", end.isoformat(), "--sessions", "80"]
    out_dir = tmp_path / "out"
    assert cli.main(["list"] + common + ["--output-dir", str(out_dir)]) == 0
    data = json.loads(next(out_dir.glob("gpw_etf_list_*.json")).read_text(encoding="utf-8"))
    assert data["skill"] == "gpw-etf-planner" and data["count"] == 4
    assert data["uncategorised"] == ["ETFNEWONE"]
    assert "data_provenance" in data and data["data_provenance"]["as_of"] == end.isoformat()
    md = next(out_dir.glob("gpw_etf_list_*.md")).read_text(encoding="utf-8")
    assert "LEWAROWANY" in md and "sprawdź KID" in md
    capsys.readouterr()
    assert cli.main(["plan", "--allocation", "ETFBW20TR=30,ETFIWDA=70", "--monthly", "1000", "--no-files",
                     "--ter", "ETFBW20TR=0.5"] + common) == 0
    out = capsys.readouterr().out
    assert out.index("Odpowiedź w skrócie") < out.index("Lista zakupów")
    assert "gpw-account-planner" in out and "--oki-qualified-pct 30" in out
    assert cli.main(["backtest", "--allocation", "ETFBW20TR=50,ETFIWDA=50", "--monthly", "1000", "--no-files",
                     "--lang", "en"] + common) == 0
    out = capsys.readouterr().out
    assert "far too short" in out and "Lump sum" in out


def test_cli_unknown_etf_exit_1(tmp_path, capsys):
    end = _write_cache(tmp_path, n=30)
    rc = cli.main(["plan", "--allocation", "ETFXYZ=100", "--monthly", "500", "--no-files", "--offline",
                   "--cache-dir", str(tmp_path), "--as-of", end.isoformat(), "--sessions", "30"])
    assert rc == 1
    assert "unknown ETF" in capsys.readouterr().err


def test_cli_no_data_offline_exit_1(tmp_path, capsys):
    rc = cli.main(["list", "--offline", "--cache-dir", str(tmp_path), "--no-files"])
    assert rc == 1


@pytest.mark.parametrize("argv", [
    ["plan", "--allocation", "ETFBW20TR", "--monthly", "100"],
    ["plan", "--allocation", "ETFBW20TR=abc", "--monthly", "100"],
    ["plan", "--allocation", "ETFBW20TR=100", "--monthly", "0"],
    ["plan", "--allocation", "ETFBW20TR=100", "--monthly", "100", "--max-orders", "0"],
    ["plan", "--allocation", "ETFBW20TR=100", "--monthly", "100", "--cash", "-5"],
    ["backtest", "--monthly", "100"],
    ["list", "--category", "nope"],
    ["list", "--as-of", "13-11-2026"],
])
def test_cli_bad_arguments_exit_2(argv):
    with pytest.raises(SystemExit) as e:
        cli.main(argv + ["--no-files"])
    assert e.value.code == 2
