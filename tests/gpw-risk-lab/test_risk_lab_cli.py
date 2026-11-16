"""CLI, portfolio input and an offline end-to-end run on a synthetic GPW cache."""

import datetime as dt
import json
import math

import pytest

import gpw_risk_lab as cli
import risk_lab_data as rd
import risk_lab_portfolio as rp
from gpwlib import plcal


# ---------------------------------------------------------------- portfolio input

def test_positions_arg():
    rows = rp.parse_positions_arg("PKOBP:100, kghm:30;DINO=10 PKOBP:5")
    by = {r["ticker"]: r for r in rows}
    assert by["PKOBP"]["shares"] == 105 and by["KGHM"]["shares"] == 30 and by["DINO"]["shares"] == 10
    assert by["PKOBP"]["avg_price"] is None


@pytest.mark.parametrize("bad", ["", "PKOBP", "PKOBP:0", "PKOBP:1.5", "PKOBP:abc"])
def test_positions_arg_errors(bad):
    with pytest.raises(rp.PortfolioError):
        rp.parse_positions_arg(bad)


def test_broker_csv_polish_without_avg_price():
    text = "﻿Walor;Ilość;Rachunek\nPKOBP;100;IKE\nPKOBP;50;zwykły\n# komentarz\nKGHM;1 000;\n"
    rows = {r["ticker"]: r for r in rp.parse_holdings_csv(text)}
    assert rows["PKOBP"]["shares"] == 150 and rows["PKOBP"]["accounts"] == {"ike": 100, "regular": 50}
    assert rows["KGHM"]["shares"] == 1000 and rows["KGHM"]["avg_price"] is None


def test_broker_csv_with_avg_price_and_errors():
    rows = rp.parse_holdings_csv("ticker,shares,avg_price\nCDR,20,\"250,50\"\n")
    assert rows[0]["avg_price"] == pytest.approx(250.5)
    with pytest.raises(rp.PortfolioError):
        rp.parse_holdings_csv("ticker;cena\nPKOBP;50\n")
    with pytest.raises(rp.PortfolioError):
        rp.parse_holdings_csv("ticker;shares;account_type\nPKOBP;5;konto_x\n")


def test_journal_rows_and_aliases():
    rows = rp.holdings_from_journal([{"ticker": "pko", "shares": 10, "avg_price": 50.0, "account_type": "ike"}])
    assert rows[0]["ticker"] == "PKO" and rows[0]["accounts"] == {"ike": 10}
    assert rp.resolve_ticker("PKO", ["PKOBP", "KGHM"]) == ("PKOBP", "PKO -> PKOBP")
    assert rp.resolve_ticker("XYZ", ["PKOBP"]) == (None, None)
    assert "PKOBP" in rp.close_matches("PKOB", ["PKOBP", "KGHM"])
    with pytest.raises(rp.PortfolioError):
        rp.holdings_from_journal([])


# ---------------------------------------------------------------- scenario file

def test_default_scenarios_valid():
    s = rd.load_scenarios(None)
    ids = [x["id"] for x in s["historical"] + s["factor"]]
    for need in ("gfc_2008", "euro_2011", "ofe_2013", "chf_2015", "covid_2020", "war_inflation_2022",
                 "worst_1d", "worst_5d", "wig_-40", "banks_-25", "pln_-15", "rates_+200bp"):
        assert need in ids


@pytest.mark.parametrize("data,msg", [
    ({"historical": [{"id": "a", "kind": "fixed", "start": "2020-01-01"}]}, "missing field"),
    ({"factor": [{"id": "a", "kind": "boom", "shock_pct": 1}]}, "kind must be"),
    ({"factor": [{"id": "a", "kind": "fx", "shock_pct": 1}, {"id": "a", "kind": "fx", "shock_pct": 2}]}, "duplicate"),
    ({"historical": [{"id": "a", "kind": "fixed", "start": "2020-13-01", "end": "2020-01-05"}]}, "YYYY-MM-DD"),
    ({}, "no scenarios"),
])
def test_scenario_validation(data, msg):
    with pytest.raises(ValueError, match=msg):
        rd.validate_scenarios(data)


def test_quarter_chunks_respect_nbp_93_day_limit():
    chunks = list(rd._quarters(dt.date(2007, 1, 1), dt.date(2008, 2, 10)))
    assert chunks[0] == (dt.date(2007, 1, 1), dt.date(2007, 3, 31))
    assert chunks[-1] == (dt.date(2008, 1, 1), dt.date(2008, 2, 10))
    assert all((b - a).days <= 92 for a, b in chunks)


def test_parse_ecb_csv():
    text = "KEY,TIME_PERIOD,OBS_VALUE\nX,2007-01,5.2\nX,2007-02,\nX,2007-03,5.5\n"
    assert rd.parse_ecb_csv(text) == {"2007-01-01": 5.2, "2007-03-01": 5.5}


# ---------------------------------------------------------------- CLI argument errors (exit 2)

@pytest.mark.parametrize("argv", [
    [],                                                     # no mode
    ["stress"],                                             # no portfolio source
    ["boom", "--positions", "PKOBP:1"],                     # bad mode
    ["stress", "--positions", "PKOBP:1", "--csv", "x.csv"],  # two sources
    ["stress", "--positions", "PKOBP:1", "--max-loss-pct", "0"],
    ["stress", "--positions", "PKOBP:1", "--max-loss-pct", "150"],
    ["stress", "--positions", "PKOBP:1", "--cash", "-5"],
    ["stress", "--positions", "PKOBP:1", "--lookback", "10"],
    ["stress", "--positions", "PKOBP:1", "--participation", "0"],
    ["stress", "--positions", "PKOBP:1", "--state-dir", "/tmp"],
    ["stress", "--positions", "PKOBP:1", "--as-of", "2026-13-01"],
])
def test_cli_argument_errors(argv):
    with pytest.raises(SystemExit) as ex:
        cli.main(argv)
    assert ex.value.code == 2


def test_cli_input_errors_exit_1(tmp_path, capsys):
    assert cli.main(["stress", "--positions", "PKOBP", "--offline", "--cache-dir", str(tmp_path)]) == 1
    assert "TICKER:SHARES" in capsys.readouterr().err
    bad = tmp_path / "s.json"
    bad.write_text("{not json")
    assert cli.main(["stress", "--positions", "PKOBP:1", "--scenarios", str(bad), "--offline",
                     "--cache-dir", str(tmp_path)]) == 1
    assert cli.main(["stress", "--positions", "PKOBP:1", "--only", "nope", "--offline",
                     "--cache-dir", str(tmp_path)]) == 1
    assert "unknown scenario" in capsys.readouterr().err
    assert cli.main(["stress", "--csv", str(tmp_path / "missing.csv"), "--offline", "--cache-dir", str(tmp_path)]) == 1
    # empty cache -> data error, exit 1
    assert cli.main(["stress", "--positions", "PKOBP:1", "--offline", "--cache-dir", str(tmp_path)]) == 1


# ---------------------------------------------------------------- offline end-to-end on a synthetic cache

def _write(cache, kind, day, rows):
    p = cache / "archive" / kind / "{}.json".format(day)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema": 1, "date": day, "type": kind, "session": True, "rows": rows,
                             "source_url": "synthetic", "retrieved_at": "2026-11-14T00:00:00+00:00"}))


@pytest.fixture()
def synth_cache(tmp_path):
    cache = tmp_path / "cache"
    days, d = [], dt.date(2026, 11, 13)
    while len(days) < 110:
        if plcal.is_probable_session(d):
            days.append(d)
        d -= dt.timedelta(days=1)
    days.reverse()
    w20 = wig = 3000.0
    a = b = 100.0
    fut = {"FW20Z2620": 3010.0, "FW20H2720": 3030.0}
    for i, day in enumerate(days):
        m = 0.01 * math.sin(i / 2.0)
        prev = (a, b, wig, w20)
        wig *= 1 + m
        w20 *= 1 + 1.1 * m
        a *= 1 + 1.2 * m + 0.002 * math.cos(i)
        b *= 1 + 0.6 * m
        chg = lambda new, old: round((new / old - 1) * 100, 4)
        _write(cache, "shares", day.isoformat(), [
            {"name": "AAA", "currency": "PLN", "open": a, "high": a, "low": a, "close": a,
             "change_pct": chg(a, prev[0]), "turnover_k": 5000.0},
            {"name": "BBB", "currency": "PLN", "open": b, "high": b, "low": b, "close": b,
             "change_pct": chg(b, prev[1]), "turnover_k": 50.0}])
        _write(cache, "indices", day.isoformat(), [
            {"name": "WIG", "close": wig, "change_pct": 0}, {"name": "WIG20", "close": w20, "change_pct": 0}])
        for k in fut:
            fut[k] *= 1 + 1.1 * m
        _write(cache, "futures", day.isoformat(), [
            {"name": k, "currency": "PLN", "close": v, "settlement": v, "turnover_k": 1000.0} for k, v in fut.items()])
    _write(cache, "etf", days[-1].isoformat(), [
        {"name": "ETFBW20ST", "currency": "PLN", "close": 20.0, "change_pct": 0, "turnover_k": 300.0}])
    return cache


def test_offline_end_to_end_all_modes(synth_cache, tmp_path, capsys):
    out = tmp_path / "out"
    rc = cli.main(["all", "--positions", "AAA:100,BBB:50", "--cash", "1000", "--max-loss-pct", "15",
                   "--offline", "--cache-dir", str(synth_cache), "--lookback", "60", "--output-dir", str(out),
                   "--lang", "en"])
    assert rc == 0
    js = json.loads(next(out.glob("gpw_risk_lab_*.json")).read_text())
    assert js["skill"] == "gpw-risk-lab" and js["as_of"] == "2026-11-13"
    sc = {s["id"]: s for s in js["stress"]["scenarios"]}
    assert sc["gfc_2008"]["skipped"] and sc["gfc_2008"]["reason"] == "no_index_data"
    assert sc["pln_-15"]["skipped"]                     # no NBP data offline
    w = sc["wig_-20"]
    assert not w["skipped"] and set(w) >= {"id", "portfolio_return_pct", "pln_loss", "worst_positions"}
    total = js["portfolio"]["total_value"]
    assert w["pln_loss"] == pytest.approx(-w["portfolio_return_pct"] / 100 * total, abs=1.0)
    assert not sc["worst_1d"]["skipped"]
    h = js["hedge"]
    assert set(h) >= {"beta", "contracts", "notional_pln", "residual_risk_pct"}
    assert h["beta"] == pytest.approx((js["portfolio"]["positions"][0]["value"] * 1.2 / 1.1
                                       + js["portfolio"]["positions"][1]["value"] * 0.6 / 1.1)
                                      / js["portfolio"]["equity_value"], abs=0.05)
    assert h["contracts"] == 0 and h["below_one_contract"] and h["multiplier"] == 20
    assert h["futures_symbol"] == "FW20Z2620"
    assert h["alternatives"]["inverse_etfs"][0]["name"] == "ETFBW20ST"
    assert h["backtest"]["n"] > 20
    md = capsys.readouterr().out
    assert "FW20" in md and "Educational material" in md
