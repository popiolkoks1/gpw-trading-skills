import json
import math

import pytest

import portfolio_check as cli
import portfolio_check_engine as pe
from gpwlib import stats
from gpw_fake_cache import gen_returns, path_for, standard_cache


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def mk_series(closes, turnover_k=5000.0, start_day=0):
    dates = ["2025-{:03d}".format(i) for i in range(start_day, start_day + len(closes))]
    return {"dates": dates, "close": list(closes), "turnover_k": [turnover_k] * len(closes)}


def mk_holding(name, shares, avg, kind="share", sector="OTHER", size="OTHER", stops=None, tracked=False, accounts=None):
    return {"ticker": name, "name": name, "shares": shares, "avg_price": avg, "kind": kind, "sector": sector,
            "size": size, "accounts": accounts or {"regular": shares},
            "stops": stops if stops is not None else [{"shares": shares, "stop": None}],
            "stops_tracked": tracked, "thesis_ids": []}


N = 254


@pytest.fixture
def market():
    base = gen_returns(N - 1, 11)
    other = gen_returns(N - 1, 12, vol=0.02)
    wig = mk_series(path_for(base, 1000.0))
    series = {
        "A": mk_series(path_for(base, 50.0)),                       # identical returns to WIG
        "B": mk_series(path_for([x * 1.0001 for x in base], 20.0)),  # ~identical to A -> corr ~1
        "C": mk_series(path_for(other, 100.0)),
        "D": mk_series(path_for(gen_returns(N - 1, 13), 10.0), turnover_k=1.0),  # illiquid
    }
    return series, wig


# --------------------------------------------------------------------------
# number + CSV parsing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,val", [("1 234,56", 1234.56), ("1.234,56", 1234.56), ("1,234.56", 1234.56),
                                      ("12,5 zł", 12.5), ("100", 100.0), ("\xa07\xa0000,00", 7000.0), ("0.5", 0.5)])
def test_parse_number(text, val):
    assert pe.parse_number(text) == pytest.approx(val)


def test_parse_number_blank_and_bad():
    assert pe.parse_number("") is None
    with pytest.raises(pe.PortfolioError):
        pe.parse_number("abc")


def test_csv_polish_semicolon_export():
    text = "﻿Walor;Ilość;Średnia cena;Rachunek\nPKOBP;150;58,20;zwykły\nPZU;1 200;52,10;IKE\n"
    rows = pe.parse_holdings_csv(text)
    assert [r["ticker"] for r in rows] == ["PKOBP", "PZU"]
    assert rows[1]["shares"] == 1200 and rows[1]["avg_price"] == pytest.approx(52.10)
    assert rows[1]["accounts"] == {"ike": 1200}
    assert rows[0]["stops_tracked"] is False


def test_csv_comma_quoted_decimals_and_comments():
    text = '# my portfolio\nticker,shares,avg_price,account_type,stop\nkghm,10,"160,5",ikze,140\n\n'
    rows = pe.parse_holdings_csv(text)
    assert rows[0]["ticker"] == "KGHM" and rows[0]["avg_price"] == 160.5
    assert rows[0]["stops"] == [{"shares": 10, "stop": 140.0}] and rows[0]["stops_tracked"] is True


def test_csv_tab_separated():
    rows = pe.parse_holdings_csv("ticker\tshares\tavg_price\nLPP\t2\t15000\n")
    assert rows[0]["shares"] == 2


def test_csv_unquoted_decimal_comma_with_comma_separator():
    with pytest.raises(pe.PortfolioError, match="quoted"):
        pe.parse_holdings_csv("ticker,shares,avg_price\nPKOBP,10,58,20\n")


def test_csv_missing_column():
    with pytest.raises(pe.PortfolioError, match="avg_price"):
        pe.parse_holdings_csv("ticker,shares\nPKOBP,10\n")


@pytest.mark.parametrize("row,msg", [("PKOBP;10,5;50", "whole"), ("PKOBP;0;50", "positive"), ("PKOBP;10;0", "avg_price"),
                                     ("PKOBP;10;50;IKZE2", "account_type"), (";10;50", "ticker")])
def test_csv_bad_rows(row, msg):
    with pytest.raises(pe.PortfolioError, match=msg):
        pe.parse_holdings_csv("ticker;shares;avg_price;account_type\n" + row + "\n")


def test_csv_empty():
    with pytest.raises(pe.PortfolioError):
        pe.parse_holdings_csv("\n# nothing\n")
    with pytest.raises(pe.PortfolioError, match="no positions"):
        pe.parse_holdings_csv("ticker,shares,avg_price\n")


def test_csv_duplicates_merge_weighted_average():
    rows = pe.parse_holdings_csv("ticker;shares;avg_price;account_type\nPZU;100;50;regular\nPZU;300;54;ike\n")
    assert len(rows) == 1
    assert rows[0]["shares"] == 400 and rows[0]["avg_price"] == pytest.approx(53.0)
    assert rows[0]["accounts"] == {"regular": 100, "ike": 300}


def test_holdings_from_journal():
    rows = pe.holdings_from_journal([{"thesis_id": "t1", "ticker": "pkobp", "shares": 10, "avg_price": 100.0,
                                      "stop": 90.0, "account_type": "ike"},
                                     {"thesis_id": "t2", "ticker": "PKOBP", "shares": 5, "avg_price": 130.0,
                                      "stop": None, "account_type": "regular"}])
    assert rows[0]["shares"] == 15 and rows[0]["avg_price"] == pytest.approx(110.0)
    assert rows[0]["stops_tracked"] and rows[0]["thesis_ids"] == ["t1", "t2"]
    with pytest.raises(pe.PortfolioError, match="ACTIVE"):
        pe.holdings_from_journal([])


def test_resolve_and_close_matches():
    names = ["PKOBP", "PKNORLEN", "CDPROJEKT", "PEKAO"]
    assert pe.resolve_ticker("pkobp", names) == ("PKOBP", None)
    assert pe.resolve_ticker("PKN", names) == ("PKNORLEN", "PKN -> PKNORLEN")
    assert pe.resolve_ticker("ORLEN", names)[0] == "PKNORLEN"
    assert pe.resolve_ticker("KGH", names) == (None, None)  # alias target missing from data
    assert "CDPROJEKT" in pe.close_matches("CDPROJEKTT", names)
    assert "PKOBP" in pe.close_matches("PKO", names)


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------

def test_values_weights_and_pnl(market):
    series, wig = market
    hs = [mk_holding("A", 100, 40.0, sector="WIG-BANKI"), mk_holding("C", 10, 80.0)]
    r = pe.analyze(hs, series, wig, cash=1000.0)
    a = next(p for p in r["positions"] if p["ticker"] == "A")
    assert a["value"] == pytest.approx(100 * series["A"]["close"][-1], abs=0.01)
    assert a["pnl"] == pytest.approx(a["value"] - 4000.0, abs=0.01)
    total = r["totals"]["total_value"]
    assert total == pytest.approx(sum(p["value"] for p in r["positions"]) + 1000.0, abs=0.01)
    assert sum(p["weight_pct"] for p in r["positions"]) + r["totals"]["cash_pct"] == pytest.approx(100.0, abs=0.05)
    assert r["positions"][0]["value"] >= r["positions"][1]["value"]


def test_concentration_flags(market):
    series, wig = market
    hs = [mk_holding("A", 1000, 40.0, sector="WIG-BANKI"), mk_holding("B", 100, 20.0, sector="WIG-BANKI"),
          mk_holding("C", 10, 80.0)]
    r = pe.analyze(hs, series, wig)
    codes = {f["code"] for f in r["flags"]}
    assert {"position_weight", "sector_weight", "hhi_high", "correlated_cluster"} <= codes
    a = next(p for p in r["positions"] if p["ticker"] == "A")
    assert a["status"] == "REVIEW"
    assert {x["code"] for x in a["reasons"]} >= {"position_weight", "sector_weight", "correlated"}
    w = [p["value"] / r["totals"]["invested"] for p in r["positions"]]
    assert r["concentration"]["hhi"] == pytest.approx(sum(x * x for x in w), abs=1e-4)
    assert r["concentration"]["effective_positions"] == pytest.approx(1 / sum(x * x for x in w), abs=0.01)


def test_well_diversified_has_no_weight_flags(market):
    series, wig = market
    closes = {k: series[k]["close"][-1] for k in "ABC"}
    # roughly equal 3 x 1/3 weights -> every position > 20%: use 6 synthetic copies instead
    extra = {}
    for i in range(6):
        extra["S{}".format(i)] = mk_series(path_for(gen_returns(N - 1, 100 + i, vol=0.015), 100.0))
    hs = [mk_holding(k, 10, 90.0, sector="WIG-{}".format(i)) for i, k in enumerate(extra)]
    r = pe.analyze(hs, extra, wig)
    assert r["concentration"]["level"] == "moderate"  # 1/6 = 0.167
    assert not [f for f in r["flags"] if f["code"] in ("position_weight", "sector_weight", "hhi_high")]
    assert r["review_count"] == 0 and all(p["status"] == "OK" for p in r["positions"])
    assert closes  # fixture sanity


def test_etf_exempt_from_position_and_sector_limits(market):
    series, wig = market
    hs = [mk_holding("A", 1000, 40.0, kind="etf", sector="ETF", size="ETF"), mk_holding("C", 1, 80.0)]
    r = pe.analyze(hs, series, wig)
    a = next(p for p in r["positions"] if p["ticker"] == "A")
    assert a["weight_pct"] > 90
    assert not [x for x in a["reasons"] if x["code"] in ("position_weight", "sector_weight")]
    assert not [f for f in r["flags"] if f["code"] == "hhi_high"]
    assert r["concentration"]["etfs_counted_as_one"] is True


def test_single_stock_is_concentrated(market):
    series, wig = market
    r = pe.analyze([mk_holding("C", 10, 80.0)], series, wig)
    assert r["concentration"]["hhi"] == 1.0
    assert {f["code"] for f in r["flags"]} >= {"position_weight", "hhi_high"}


def test_liquidity_days_to_exit(market):
    series, wig = market
    hs = [mk_holding("D", 1000, 10.0), mk_holding("C", 1, 80.0)]
    r = pe.analyze(hs, series, wig)
    d = next(p for p in r["positions"] if p["ticker"] == "D")
    # median turnover 1k PLN -> 10% = 100 PLN per session
    assert d["median_turnover_pln"] == 1000.0
    assert d["days_to_exit"] == pytest.approx(d["value"] / 100.0, abs=0.01)
    assert any(x["code"] == "illiquid" for x in d["reasons"])


def test_zero_turnover_is_illiquid(market):
    series, wig = market
    series = dict(series, Z=mk_series(series["C"]["close"], turnover_k=0.0))
    r = pe.analyze([mk_holding("Z", 1, 80.0), mk_holding("C", 1, 80.0)], series, wig)
    z = next(p for p in r["positions"] if p["ticker"] == "Z")
    assert z["days_to_exit"] is None and z["status"] == "REVIEW"


def test_risk_stats_match_gpwlib(market):
    series, wig = market
    hs = [mk_holding("A", 100, 40.0), mk_holding("C", 20, 80.0)]
    cash = 2000.0
    r = pe.analyze(hs, series, wig, cash=cash)
    risk = r["risk"]
    assert risk["available"] and risk["returns_used"] == 252
    total = r["totals"]["total_value"]
    wa = 100 * series["A"]["close"][-1] / total
    wc = 20 * series["C"]["close"][-1] / total
    ra, rc = stats.returns(series["A"]["close"][-253:]), stats.returns(series["C"]["close"][-253:])
    port = [wa * x + wc * y for x, y in zip(ra, rc)]
    # empirical quantile: ceil(n * 5%)-th worst return (gpwlib convention)
    k = math.ceil(len(port) * 0.05)
    assert risk["var_pct"] == pytest.approx(-sorted(port)[k - 1] * 100, abs=1e-3)
    assert risk["var_pln"] == pytest.approx(stats.historical_var(port) * total, abs=0.02)
    assert risk["es_pln"] == pytest.approx(stats.expected_shortfall(port) * total, abs=0.02)
    assert risk["es_pln"] >= risk["var_pln"]
    assert risk["var_horizon_pln"] == pytest.approx(risk["var_pln"] * math.sqrt(20), abs=0.1)
    assert risk["volatility_pct"] == pytest.approx(stats.stdev(port) * math.sqrt(252) * 100, abs=0.01)
    # equity-only volatility is higher than total (cash dampens)
    assert risk["equity_volatility_pct"] > risk["volatility_pct"]
    assert risk["per_position"]["A"]["beta"] == pytest.approx(1.0, abs=1e-6)


def test_identical_to_wig_portfolio(market):
    series, wig = market
    r = pe.analyze([mk_holding("A", 100, 40.0)], series, wig)
    risk = r["risk"]
    assert risk["beta"] == pytest.approx(1.0, abs=1e-6)
    assert risk["return_pct"] == pytest.approx(risk["wig_return_pct"], abs=0.01)
    assert risk["excess_return_pp"] == pytest.approx(0.0, abs=0.01)
    assert risk["max_drawdown_pct"] == pytest.approx(risk["wig_max_drawdown_pct"], abs=0.01)


def test_drawdown_known_path():
    wig = mk_series([100.0] * 70 + [100.0 + i for i in range(1, 11)])
    closes = [100.0] * 60 + [120.0, 90.0] + [90.0] * 18
    series = {"X": mk_series(closes)}
    r = pe.analyze([mk_holding("X", 10, 100.0)], series, wig)
    assert r["risk"]["max_drawdown_pct"] == pytest.approx(-25.0, abs=0.01)
    assert r["risk"]["current_drawdown_pct"] == pytest.approx(-25.0, abs=0.01)


def test_correlation_pairs_and_clusters(market):
    series, wig = market
    hs = [mk_holding(k, 10, 10.0) for k in ("A", "B", "C")]
    r = pe.analyze(hs, series, wig)
    pairs = r["risk"]["high_pairs"]
    assert pairs and {pairs[0]["a"], pairs[0]["b"]} == {"A", "B"} and pairs[0]["corr"] > 0.99
    assert r["risk"]["clusters"] == [["A", "B"]]
    assert r["risk"]["correlation"]["A"]["A"] == 1.0
    assert abs(r["risk"]["correlation"]["A"]["C"]) < 0.7


def test_short_history_excluded(market):
    series, wig = market
    series = dict(series, NEW=mk_series(path_for(gen_returns(29, 7), 30.0), start_day=N - 30))
    # align NEW's dates with the tail of WIG's dates
    series["NEW"]["dates"] = wig["dates"][-30:]
    r = pe.analyze([mk_holding("A", 10, 40.0), mk_holding("NEW", 10, 30.0)], series, wig)
    assert r["risk"]["excluded"] == ["NEW"] and r["risk"]["returns_used"] == 252
    new = next(p for p in r["positions"] if p["ticker"] == "NEW")
    assert any(x["code"] == "short_history" for x in new["reasons"])


def test_risk_unavailable_with_short_wig():
    wig = mk_series([100.0 + i for i in range(30)])
    series = {"X": mk_series([10.0 + i for i in range(30)])}
    r = pe.analyze([mk_holding("X", 1, 10.0)], series, wig)
    assert r["risk"]["available"] is False


def test_heat_and_stops(market):
    series, wig = market
    last_a, last_c = series["A"]["close"][-1], series["C"]["close"][-1]
    hs = [mk_holding("A", 100, 40.0, stops=[{"shares": 60, "stop": last_a - 2}, {"shares": 40, "stop": None}], tracked=True),
          mk_holding("C", 10, 80.0, stops=[{"shares": 10, "stop": last_c + 1}], tracked=True)]
    r = pe.analyze(hs, series, wig, cash=500.0)
    heat = r["heat"]
    assert heat["tracked"] and heat["open_risk"] == pytest.approx(120.0, abs=0.01)  # 60 x 2; C stop above price -> 0
    assert heat["heat_pct"] == pytest.approx(120.0 / r["totals"]["total_value"] * 100, abs=0.01)
    assert heat["positions_without_stop"] == ["A"] or heat["positions_without_stop"] == ["A"]
    codes = {f["code"] for f in r["flags"]}
    assert {"no_stop", "below_stop"} <= codes
    c = next(p for p in r["positions"] if p["ticker"] == "C")
    assert any(x["code"] == "below_stop" for x in c["reasons"])


def test_untracked_stops_not_flagged(market):
    series, wig = market
    r = pe.analyze([mk_holding("A", 10, 40.0), mk_holding("C", 1, 80.0)], series, wig)
    assert r["heat"]["tracked"] is False and r["heat"]["heat_pct"] is None
    assert not [f for f in r["flags"] if f["code"] == "no_stop"]


def test_check_avg_price_split_hint(market):
    series, wig = market
    last = series["C"]["close"][-1]
    r = pe.analyze([mk_holding("C", 10, last * 10), mk_holding("A", 10, 40.0)], series, wig)
    c = next(p for p in r["positions"] if p["ticker"] == "C")
    assert any(x["code"] == "check_avg_price" for x in c["reasons"])


def test_latent_tax_only_regular_net(market):
    series, wig = market
    la, lc = series["A"]["close"][-1], series["C"]["close"][-1]
    hs = [mk_holding("A", 100, la - 10, accounts={"regular": 50, "ike": 50}), mk_holding("C", 10, lc + 20)]
    r = pe.analyze(hs, series, wig)
    gain = 50 * 10 - 10 * 20
    assert r["tax"]["regular_unrealised_net"] == pytest.approx(gain, abs=0.01)
    assert r["tax"]["indicative_tax_if_sold"] == pytest.approx(gain * 0.19, abs=0.01)
    assert set(r["accounts"]) == {"regular", "ike"}


def test_stale_quote_flag(market):
    series, wig = market
    s = dict(series["C"])
    s = {"dates": s["dates"][:-10], "close": s["close"][:-10], "turnover_k": s["turnover_k"][:-10]}
    series = dict(series, OLD=s)
    r = pe.analyze([mk_holding("OLD", 1, 80.0), mk_holding("A", 1, 40.0)], series, wig)
    old = next(p for p in r["positions"] if p["ticker"] == "OLD")
    assert old["stale_sessions"] == 10 and any(x["code"] == "stale_quote" for x in old["reasons"])


def test_analyze_errors(market):
    series, wig = market
    with pytest.raises(pe.PortfolioError):
        pe.analyze([], series, wig)
    with pytest.raises(pe.PortfolioError):
        pe.analyze([mk_holding("A", 1, 1.0)], series, wig, cash=-1)


# --------------------------------------------------------------------------
# CLI (synthetic offline cache)
# --------------------------------------------------------------------------

@pytest.fixture
def cache(tmp_path):
    root = tmp_path / "cache"
    standard_cache(root)
    return root


def _csv(tmp_path, text):
    p = tmp_path / "h.csv"
    p.write_text(text, encoding="utf-8")
    return p


def test_cli_end_to_end_pl(tmp_path, cache, capsys):
    p = _csv(tmp_path, "Walor;Ilość;Średnia cena\nBANKA;1000;40,00\nBANKB;200;70\nGAMES;20;180\nILLIQ;500;9,5\nNEWCO;10;28\n")
    out = tmp_path / "out"
    rc = cli.main(["--csv", str(p), "--cash", "5 000", "--offline", "--cache-dir", str(cache), "--output-dir", str(out)])
    assert rc == 0
    md = capsys.readouterr().out
    assert md.startswith("# Przegląd portfela GPW")
    assert "## W skrócie" in md and "Twój portfel jest wart" in md
    assert "nie polecenie sprzedaży" in md
    assert "Źródła" in md and md.rstrip().endswith("_")
    js = json.loads(next(out.glob("gpw_portfolio_check_*.json")).read_text(encoding="utf-8"))
    assert js["skill"] == "gpw-portfolio-check" and js["version"] == "1.0.0" and js["lang"] == "pl"
    assert js["data_provenance"]["as_of"] == "2026-11-13"
    sectors = {p["ticker"]: p["sector"] for p in js["positions"]}
    assert sectors["BANKA"] == "WIG-BANKI" and sectors["ILLIQ"] == "OTHER"
    sizes = {p["ticker"]: p["size"] for p in js["positions"]}
    assert sizes["GAMES"] == "MWIG40" and sizes["NEWCO"] == "OTHER"
    assert "ILLIQ" in next(f for f in js["flags"] if f["code"] == "illiquid")["tickers"]
    assert js["risk"]["excluded"] == ["NEWCO"]
    assert ["BANKA", "BANKB"] in js["risk"]["clusters"]
    assert js["totals"]["cash"] == 5000.0
    assert list(out.glob("gpw_portfolio_check_*.md"))


def test_cli_etf_and_english(tmp_path, cache, capsys):
    p = _csv(tmp_path, "ticker,shares,avg_price\nETFW20,100,80\nGAMES,1,150\n")
    rc = cli.main(["--csv", str(p), "--offline", "--cache-dir", str(cache), "--no-files", "--lang", "en"])
    assert rc == 0
    md = capsys.readouterr().out
    assert "Most of the portfolio is in ETFs" in md and "| ETFW20 |" in md


def test_cli_unknown_ticker_lists_matches(tmp_path, cache, capsys):
    p = _csv(tmp_path, "ticker,shares,avg_price\nBANKC,1,10\n")
    rc = cli.main(["--csv", str(p), "--offline", "--cache-dir", str(cache), "--no-files"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "BANKC" in err and "BANKA" in err


def test_cli_from_journal(tmp_path, cache, capsys):
    th = {"schema_version": "1.0", "id": "th_banka_20261001_aaaa", "ticker": "BANKA", "status": "ACTIVE",
          "plan": {"entry": 40.0, "stop": 35.0}, "account_type": "regular",
          "fills": [{"date": "2026-10-01", "side": "buy", "shares": 100, "price": 40.0, "fee": 5.0},
                    {"date": "2026-10-05", "side": "buy", "shares": 100, "price": 44.0, "fee": 5.0},
                    {"date": "2026-10-10", "side": "sell", "shares": 50, "price": 45.0, "fee": 5.0}]}
    d = tmp_path / "journal" / "theses"
    d.mkdir(parents=True)
    (d / "th_banka_20261001_aaaa.json").write_text(json.dumps(th), encoding="utf-8")
    rc = cli.main(["--from-journal", "--state-dir", str(tmp_path / "journal"), "--offline", "--cache-dir", str(cache),
                   "--output-dir", str(tmp_path / "o")])
    assert rc == 0
    js = json.loads(next((tmp_path / "o").glob("*.json")).read_text(encoding="utf-8"))
    pos = js["positions"][0]
    # FIFO: 50 of the 40.00 lot sold -> 50 @ 40 + 100 @ 44 remain
    assert pos["shares"] == 150 and pos["avg_price"] == pytest.approx((50 * 40 + 100 * 44) / 150, abs=1e-3)
    assert js["heat"]["tracked"] and js["heat"]["open_risk"] > 0
    assert "FIFO" in capsys.readouterr().out


def test_cli_empty_journal(tmp_path, cache):
    assert cli.main(["--from-journal", "--state-dir", str(tmp_path / "nothing"), "--offline",
                     "--cache-dir", str(cache), "--no-files"]) == 1


def test_cli_missing_csv_file(tmp_path, cache):
    assert cli.main(["--csv", str(tmp_path / "nope.csv"), "--offline", "--cache-dir", str(cache), "--no-files"]) == 1


def test_cli_empty_cache(tmp_path):
    p = _csv(tmp_path, "ticker,shares,avg_price\nBANKA,1,10\n")
    assert cli.main(["--csv", str(p), "--offline", "--cache-dir", str(tmp_path / "empty"), "--no-files"]) == 1


@pytest.mark.parametrize("argv", [[], ["--csv", "x.csv", "--from-journal"], ["--csv", "x.csv", "--cash", "-5"],
                                  ["--csv", "x.csv", "--lookback", "10"], ["--csv", "x.csv", "--as-of", "13.11.2026"],
                                  ["--csv", "x.csv", "--lang", "de"]])
def test_cli_argument_errors(argv):
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert e.value.code == 2
