"""Data layer and CLI tests (no network: fetchers are monkeypatched)."""

import datetime as dt
import json
import re

import pytest

import gpw_quality_screener as cli
import quality_data as qd
import quality_engine as qe
from gpwlib import fundamentals
from quality_screener_fixtures import notoria_from_values, read, snapshot

KETY_IND = {"isin": "PLKETY000011", "sector": "hutnictwo metali nieżelaznych", "shares_issued": 9_620_000,
            "market_cap_pln": 13_183e6, "book_value_pln": 2_140e6, "pbv": 6.2, "pe": 18.9, "dividend_yield_pct": 3.7}


def pko_ind():
    return fundamentals.parse_indicators_html(read("card_indicators_PKOBP.html"))


# --------------------------------------------------------------------------
# Sessions / signal dates
# --------------------------------------------------------------------------

@pytest.mark.parametrize("ts,expected", [
    ("2026-11-16T14:06:55+00:00", "2026-11-13"),   # Monday 15:06 Warsaw: Monday not final yet
    ("2026-11-16T17:30:00+00:00", "2026-11-16"),   # Monday 18:30 Warsaw: final
    ("2026-11-14T10:00:00+00:00", "2026-11-13"),   # Saturday
    ("2026-11-11T19:00:00+00:00", "2026-11-10"),   # 11 Nov holiday (no session) -> Tuesday
])
def test_session_known_at_is_deterministic(ts, expected):
    assert qd.session_known_at(ts).isoformat() == expected
    assert qd.session_known_at("garbage") is None


# --------------------------------------------------------------------------
# History store
# --------------------------------------------------------------------------

def _save(tmp_path, ticker, label, values, when, ind=None):
    snap = {"ticker": ticker, "isin": "PLTEST000000", "retrieved_at": when,
            "notoria": notoria_from_values(label, values), "indicators": ind or {}}
    return fundamentals.save_snapshot(snap, tmp_path)


VALS_2025 = {"revenue": 800.0, "profit_on_sales": 110.0, "operating_profit": 100.0, "pretax_profit": 90.0,
             "net_profit_parent": 70.0, "cfo": 90.0, "capex": -40.0, "total_assets": 1000.0, "current_assets": 350.0,
             "current_liabilities": 250.0, "noncurrent_liabilities": 250.0, "equity_parent": 500.0,
             "share_capital": 50e6, "depreciation_amortization": 30.0}
VALS_2026 = dict(VALS_2025, revenue=900.0, profit_on_sales=135.0, operating_profit=120.0, pretax_profit=110.0,
                 net_profit_parent=90.0, cfo=120.0, current_assets=400.0, noncurrent_liabilities=200.0,
                 equity_parent=550.0)


def test_history_prior_same_window_and_full_fscore(tmp_path):
    _save(tmp_path, "ABC", "I-III kw. 2025", VALS_2025, "2025-11-20T10:00:00+00:00", {"shares_issued": 1000})
    _save(tmp_path, "ABC", "I-III kw. 2026", VALS_2026, "2026-11-16T10:00:00+00:00", {"shares_issued": 1000})
    hist = qd.load_history("ABC", tmp_path)
    stored = qd.history_statements(hist)
    assert [s["label"] for s in stored] == ["I-III kw. 2025", "I-III kw. 2026"]
    cur = stored[-1]
    pri = qd.prior_from_history(cur, stored[:-1])
    assert pri["prior"]["label"] == "I-III kw. 2025" and pri["prev_fy"] is None
    assert qd.shares_year_ago(hist, dt.date(2026, 11, 16)) == 1000.0
    f = qe.piotroski(cur, pri["prior"], 1000, 1000)
    assert f["available"] == 9 and f["score"] == 9


def test_point_in_time_uses_only_what_was_known(tmp_path):
    _save(tmp_path, "ABC", "I-II kw. 2026", VALS_2025, "2026-09-01T10:00:00+00:00", {"market_cap_pln": 1.0})
    _save(tmp_path, "ABC", "I-III kw. 2026", VALS_2026, "2026-11-16T10:00:00+00:00", {"market_cap_pln": 2.0})
    hist = qd.load_history("ABC", tmp_path)
    snap = qd.point_in_time_snapshot("ABC", hist, dt.date(2026, 10, 1))
    assert snap["notoria"]["period_label"] == "I-II kw. 2026"
    assert snap["indicators"]["market_cap_pln"] == 1.0
    assert qd.point_in_time_snapshot("ABC", hist, dt.date(2026, 1, 1)) is None
    assert qd.point_in_time_snapshot("ABC", None, dt.date(2026, 1, 1)) is None


def test_point_in_time_ignores_later_restatement(tmp_path):
    _save(tmp_path, "ABC", "I-II kw. 2026", VALS_2025, "2026-09-01T10:00:00+00:00")
    _save(tmp_path, "ABC", "I-II kw. 2026", dict(VALS_2025, net_profit_parent=10.0), "2026-10-15T10:00:00+00:00")
    hist = qd.load_history("ABC", tmp_path)
    old = qd.point_in_time_snapshot("ABC", hist, dt.date(2026, 10, 1))
    assert old["notoria"]["values"]["net_profit_parent"] == 70.0
    new = qd.point_in_time_snapshot("ABC", hist, dt.date(2026, 10, 20))
    assert new["notoria"]["values"]["net_profit_parent"] == 10.0


# --------------------------------------------------------------------------
# Universe and xBRL name matching
# --------------------------------------------------------------------------

def test_normalise_index():
    assert qd.normalise_index("mWIG40") == "MWIG40" and qd.normalise_index("wig") == "WIG"
    with pytest.raises(qd.DataError):
        qd.normalise_index("DAX")


@pytest.mark.parametrize("url,key", [
    ("/x/2022-12-31/ESEF/PL/0/GrupaBudimex-2022-12-31-pl.zip", "budimex"),
    ("/x/2022-12-31/ESEF/PL/0/cdprojektsa-2022-12-31-pl.zip", "cdprojekt"),
    ("/x/2021-12-31/ESEF/PL/0/esef_SSF_GRUPAKRUK-2021-12-31.zip", "kruk"),
    ("/x/2023-01-31/ESEF/PL/0/GK_LPP_2023-01-31_PL.zip", "lpp"),
    ("/x/2022-12-31/ESEF/PL/0/esef_grupakety_2022-12-31_pl.zip", "kety"),
])
def test_package_name_key(url, key):
    assert qd.package_name_key(url) == key


def test_match_lei_unique_only():
    files = [{"lei": "L1", "package_url": "/a/ESEF/PL/0/GrupaAzoty-2023-12-31-pl.zip"},
             {"lei": "L2", "package_url": "/a/ESEF/PL/0/GrupaAzotyPolice-2023-12-31-pl.zip"},
             {"lei": "L3", "package_url": "/a/ESEF/PL/0/PGE-2021-12-31.zip"},
             {"lei": "L4", "package_url": "/a/ESEF/PL/0/pge-2022-12-31.zip"}]
    assert qd.match_lei("GRUPAAZOTY", files) == "L1"
    assert qd.match_lei("PGE", files) is None          # two LEIs -> ambiguous
    assert qd.match_lei("KETY", files) is None


def test_esef_missing_file_is_data_error(tmp_path):
    with pytest.raises(qd.DataError):
        qd.esef_pair(str(tmp_path / "nope.zip"))
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(qd.DataError):
        qd.esef_pair(str(bad))


# --------------------------------------------------------------------------
# analyse_company on the real card fixtures
# --------------------------------------------------------------------------

def test_analyse_company_kety_and_pkobp():
    as_of = dt.date(2026, 11, 13)
    kety = cli.analyse_company(snapshot("KETY", read("card_notoria_KETY.html"), KETY_IND), None, as_of, [])
    assert kety["sector_type"] == "industrial" and kety["status"] == "ok"
    assert kety["f"]["display"] == "3/3" and kety["f_score"] is None
    assert kety["metrics"]["roe"] == pytest.approx(594 * 12 / 9 / 2140, rel=1e-4)
    assert kety["red_flags"] == [] and kety["data_checks"] == [] and kety["stale_period"] is False
    pko = cli.analyse_company(snapshot("PKOBP", read("card_notoria_PKOBP.html"), pko_ind()), None, as_of, [])
    assert pko["sector_type"] == "bank" and pko["f"]["applicable"] is False and pko["f_score"] is None
    # bank CFO is negative but that is not a red flag for a bank
    assert pko["red_flags"] == []


def test_stale_period_flag():
    as_of = dt.date(2026, 11, 13)
    html = read("card_notoria_KETY.html").replace("I-III kw. 2026", "I-III kw. 2025")
    row = cli.analyse_company(snapshot("KETY", html, KETY_IND), None, as_of, [])
    assert row["stale_period"] is True and row["period_age_days"] > qe.STALE_PERIOD_DAYS


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    [],
    ["screen", "--min-f", "10"],
    ["screen", "--max-pe", "0"],
    ["screen", "--top", "0"],
    ["screen", "--all", "--index", "WIG20"],
    ["screen", "--tickers", " , "],
    ["screen", "--as-of", "2999-01-01"],
    ["screen", "--lang", "de"],
    ["ticker", "KETY", "--lei", "X"],
    ["ticker", "KETY", "--isin", "PL123"],
    ["ticker"],
])
def test_cli_argument_errors_exit_2(argv):
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(argv)
    assert exc.value.code == 2


SYNTH = {  # ticker -> (sector, values tweaks)
    "AAA": ("informatyka", {"net_profit_parent": 150.0, "cfo": 200.0}),
    "BBB": ("informatyka", {"net_profit_parent": 40.0, "cfo": 30.0}),
    "CCC": ("budownictwo", {"net_profit_parent": 60.0, "cfo": -50.0}),
    "DDD": ("budownictwo", {"net_profit_parent": -30.0, "cfo": 10.0}),
}


@pytest.fixture
def patched(monkeypatch, tmp_path):
    def fake_universe(tickers=None, indices=None, all_main=False, cache_dir=None, offline=False, isin=None):
        if tickers and tickers[0].upper() == "NOPE":
            return [], [{"ticker": "NOPE", "error": "Unknown ticker 'NOPE'"}]
        if indices:
            for i in indices:
                qd.normalise_index(i)
        names = [t.upper() for t in tickers] if tickers else ["KETY", "PKOBP"] + list(SYNTH)
        return [{"ticker": n, "isin": "PLTEST000000", "origin": "test"} for n in names], []

    def fake_snapshot(ticker, isin, cache_dir=None, offline=False, ttl_indicators=0, ttl_notoria=0):
        when = "2026-11-16T15:00:00+00:00"
        if ticker == "KETY":
            return snapshot("KETY", read("card_notoria_KETY.html"), KETY_IND, when)
        if ticker == "PKOBP":
            return snapshot("PKOBP", read("card_notoria_PKOBP.html"), pko_ind(), when)
        sector, tweak = SYNTH[ticker]
        vals = {k: v * 1e6 if k != "share_capital" else v for k, v in dict(VALS_2026, **tweak).items()}
        s = snapshot(ticker, None, {"sector": sector, "market_cap_pln": 2e9, "pe": 15.0, "pbv": 2.0}, when)
        s["notoria"] = notoria_from_values("I-III kw. 2026", vals)
        return s

    monkeypatch.setattr(qd, "resolve_universe", fake_universe)
    monkeypatch.setattr(qd, "fetch_snapshot", fake_snapshot)
    monkeypatch.setattr(qd, "bank_members", lambda *a, **k: ["PKOBP"])
    monkeypatch.setattr(qd, "close_on", lambda *a, **k: 100.0)
    return tmp_path


def _run(patched, *argv):
    out = patched / "out"
    extra = [] if "--history-dir" in argv else ["--history-dir", str(patched / "hist")]
    code = cli.main(list(argv) + ["--output-dir", str(out), "--cache-dir", str(patched / "cache")] + extra)
    files = sorted(out.glob("gpw_quality_screener_*.json")) if out.exists() else []
    return code, (json.loads(files[-1].read_text()) if files else None), out


def test_cli_screen_contract(patched, capsys):
    code, data, out = _run(patched, "screen", "--lang", "en")
    assert code == 0
    for k in ("skill", "version", "lang", "as_of", "companies", "coverage", "signals", "data_provenance"):
        assert k in data
    assert data["skill"] == "gpw-quality-screener" and data["mode"] == "screen"
    assert len(data["companies"]) == 6
    for c in data["companies"]:
        for k in ("ticker", "sector_type", "f_score", "quality_score", "red_flags", "metrics"):
            assert k in c
        assert c["sector_type"] in ("industrial", "bank", "insurer")
    by = {c["ticker"]: c for c in data["companies"]}
    assert by["PKOBP"]["sector_type"] == "bank" and by["PKOBP"]["f_score_partial"]["applicable"] is False
    assert by["KETY"]["f_score"] is None and by["KETY"]["f_score_partial"]["display"] == "3/3"
    assert {f["code"] for f in by["CCC"]["red_flags"]} >= {"cfo_negative_with_profit"}
    assert {f["code"] for f in by["DDD"]["red_flags"]} == {"loss_making"}
    assert data["coverage"]["with_data"] == 6 and data["coverage"]["f_available_distribution"] == {"3": 5}
    assert data["coverage"]["history_saved"] == {"new": 6}
    for s in data["signals"]:
        assert s["date"] <= data["as_of"] and re.fullmatch(r"[a-z0-9_]{1,40}", s["kind"])
        assert s["date"] == "2026-11-13" and s["price"] == 100.0
    assert "CCC" in {s["ticker"] for s in data["signals"] if s["kind"] == "quality_red_flags"}
    md = next(out.glob("*.md")).read_text()
    assert "Sources" in md and "Educational material" in md
    assert "unknown" in md  # the thin-history note
    # second run: same periods -> unchanged history, identical signal keys (dedup-friendly)
    code, data2, _ = _run(patched, "screen", "--lang", "en")
    assert data2["coverage"]["history_saved"] == {"unchanged": 6}
    key = lambda d: sorted((s["ticker"], s["date"], s["kind"]) for s in d["signals"])  # noqa: E731
    assert key(data) == key(data2)


def test_cli_screen_filters(patched):
    code, data, _ = _run(patched, "screen", "--min-f", "3", "--max-pe", "16")
    assert code == 0
    ex = data["filters"]["excluded"]
    assert ex.get("f_not_applicable") == 1           # the bank
    assert ex.get("pe") == 1                         # KETY P/E 18.9
    assert data["filters"]["min_f"] == 3
    kept = [c["ticker"] for c in data["companies"] if c.get("passes_filters")]
    assert "KETY" not in kept and "AAA" in kept


def test_cli_ticker_card_polish(patched, capsys):
    code = cli.main(["ticker", "KETY", "--no-files", "--history-dir", str(patched / "h")])
    md = capsys.readouterr().out
    assert code == 0
    assert "Karta jakości: KETY" in md and "kryterium po kryterium" in md
    assert md.count("❔ brak danych") == 6 and "Źródła / Sources" in md
    assert "Materiał edukacyjny" in md


def test_cli_ticker_bank_card(patched, capsys):
    code = cli.main(["ticker", "PKOBP", "--no-files", "--lang", "en", "--history-dir", str(patched / "h")])
    md = capsys.readouterr().out
    assert code == 0 and "does not apply to banks" in md


def test_cli_unknown_ticker_exit_1(patched, capsys):
    assert cli.main(["ticker", "NOPE", "--no-files", "--history-dir", str(patched / "h")]) == 1
    assert "Unknown ticker" in capsys.readouterr().err


def test_cli_unknown_index_exit_1(patched, capsys):
    assert cli.main(["screen", "--index", "DAX", "--no-files", "--history-dir", str(patched / "h")]) == 1
    assert "Unknown index" in capsys.readouterr().err


def test_cli_as_of_without_history_exit_1(patched, capsys):
    code = cli.main(["ticker", "KETY", "--as-of", "2026-01-05", "--no-files", "--history-dir", str(patched / "empty")])
    assert code == 1 and "no local history" in capsys.readouterr().err


def test_cli_as_of_point_in_time(patched, monkeypatch):
    hist = patched / "pit"
    for t in ("KETY", "PKOBP"):
        snap = qd.fetch_snapshot(t, "PLTEST000000")
        snap["retrieved_at"] = snap["indicators_retrieved_at"] = "2026-11-02T10:00:00+00:00"
        fundamentals.save_snapshot(snap, hist)

    def no_network(*a, **k):
        raise AssertionError("point-in-time run must not fetch company data")

    monkeypatch.setattr(qd, "fetch_snapshot", no_network)
    code, data, _ = _run(patched, "screen", "--tickers", "KETY,PKOBP,AAA", "--as-of", "2026-11-05",
                         "--history-dir", str(hist))
    assert code == 0 and data["run"]["historical"] is True
    by = {c["ticker"]: c for c in data["companies"]}
    assert by["KETY"]["status"] == "ok" and by["KETY"]["period"] == "I-III kw. 2026"
    assert by["AAA"]["status"] == "error" and "no local history" in by["AAA"]["error"]
    assert data["data_provenance"]["freshness"]["historical_run"] is True


def test_cli_esef_missing_exit_1(patched, capsys):
    code = cli.main(["ticker", "KETY", "--esef", str(patched / "missing.zip"), "--no-files",
                     "--history-dir", str(patched / "h")])
    assert code == 1 and "ESEF file not found" in capsys.readouterr().err
