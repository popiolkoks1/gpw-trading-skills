import datetime as dt
import json

import pytest

import dividend_engine as E
import gpw_dividend_analyzer as CLI

D = dt.date


def _write_cache(tmp_path, n=420, end=D(2026, 11, 13)):
    """Synthetic cached archive: DIVCO pays 2.00 zł every July, NODIV never pays."""
    days, d = [], end
    while len(days) < n:
        if E.is_session(d):
            days.append(d)
        d -= dt.timedelta(days=1)
    days.reverse()
    root = tmp_path / "archive" / "shares"
    root.mkdir(parents=True)
    prev = {"DIVCO": None, "NODIV": None}
    exdates = set()
    for y in sorted({x.year for x in days}):
        july = [x for x in days if x.year == y and x.month == 7]
        if july:
            exdates.add(july[5])
    for i, day in enumerate(days):
        rows = []
        for name, base in (("DIVCO", 50.0), ("NODIV", 20.0)):
            p = prev[name]
            close = base + 0.01 * (i % 7)
            ref = p if p is not None else close
            if name == "DIVCO" and day in exdates and p is not None:
                ref = p - 2.0
                close = ref + 0.05
            chg = round((close / ref - 1) * 100, 2)
            rows.append({"name": name, "currency": "PLN", "open": close, "high": close, "low": close,
                         "close": round(close, 2), "change_pct": chg, "turnover_k": 500.0})
            prev[name] = round(close, 2)
        payload = {"schema": 1, "date": day.isoformat(), "type": "shares", "session": True, "rows": rows,
                   "source_url": "test", "retrieved_at": "2026-11-14T00:00:00+00:00"}
        (root / "{}.json".format(day.isoformat())).write_text(json.dumps(payload), encoding="utf-8")
    return sorted(exdates)


BASE = ["--offline", "--no-espi", "--no-fundamentals", "--as-of", "2026-11-13"]


def test_cli_ticker_end_to_end(tmp_path, capsys):
    exd = _write_cache(tmp_path)
    out = tmp_path / "out"
    rc = CLI.main(["ticker", "divco", "--cache-dir", str(tmp_path), "--output-dir", str(out), "--shares", "100",
                   "--lang", "en"] + BASE)
    assert rc == 0
    files = list(out.glob("gpw_dividend_analyzer_*.json"))
    assert len(files) == 1
    r = json.loads(files[0].read_text(encoding="utf-8"))
    assert r["skill"] == "gpw-dividend-analyzer" and r["version"] == "1.0.0" and r["as_of"] == "2026-11-13"
    c = r["companies"][0]
    for k in ("ticker", "dps_history", "years_paid", "cuts", "yield_gross_pct", "yield_net_regular_pct",
              "payout_ratio", "stability_score", "trap_flags"):
        assert k in c
    assert c["ticker"] == "DIVCO" and c["payout_ratio"] is None
    paid = [a for a in c["dps_history"] if a["dps"] > 0]
    assert paid and all(a["dps"] == pytest.approx(2.0, abs=0.01) for a in paid)
    assert c["yield_net_regular_pct"] == pytest.approx(c["yield_gross_pct"] * 0.81, abs=0.01)
    td = r["ticker_detail"]
    assert td["cash_regular"]["gross"] == pytest.approx(200.0, abs=1)
    assert td["capture_example"]["result"] < 0
    assert td["ike_vs_regular"]["ike_minus_regular"] > 0
    assert r["upcoming"] and r["upcoming"][0]["date_kind"] == "estimated"
    assert "data_provenance" in r and isinstance(r["signals"], list)
    md = capsys.readouterr().out
    assert "Sources" in md and "Educational material" in md


def test_cli_screen_with_tickers_and_filters(tmp_path, capsys):
    _write_cache(tmp_path)
    rc = CLI.main(["screen", "--tickers", "DIVCO,NODIV", "--cache-dir", str(tmp_path), "--no-files"] + BASE)
    assert rc == 0
    md = capsys.readouterr().out
    assert "DIVCO" in md and "NODIV" in md
    args = CLI.parse_args(["screen", "--tickers", "DIVCO,NODIV", "--cache-dir", str(tmp_path), "--min-yield", "1"] + BASE)
    r = CLI.run(args)
    assert [c["ticker"] for c in r["companies"]] == ["DIVCO"]
    assert r["companies"][0]["rank"] == 1 and r["screen"]["n_universe"] == 2


def test_cli_portfolio_income(tmp_path):
    _write_cache(tmp_path)
    args = CLI.parse_args(["portfolio", "--tickers", "DIVCO,NODIV", "--shares", "100,50",
                           "--cache-dir", str(tmp_path)] + BASE)
    r = CLI.run(args)
    cal = r["portfolio"]["calendar"]
    assert cal["total_gross"] == pytest.approx(200.0, abs=1)
    assert cal["total_net_regular"] == pytest.approx(162.0, abs=1)
    assert r["portfolio"]["n_estimated"] == 1


def test_cli_unknown_ticker_exit_1(tmp_path, capsys):
    _write_cache(tmp_path, n=30)
    rc = CLI.main(["ticker", "NOSUCH", "--cache-dir", str(tmp_path), "--no-files"] + BASE)
    assert rc == 1
    assert "NOSUCH" in capsys.readouterr().err


def test_cli_empty_cache_exit_1(tmp_path, capsys):
    rc = CLI.main(["ticker", "PZU", "--cache-dir", str(tmp_path), "--no-files"] + BASE)
    assert rc == 1


def test_cli_empty_portfolio_exit_1(tmp_path, capsys):
    _write_cache(tmp_path, n=30)
    rc = CLI.main(["portfolio", "--journal-dir", str(tmp_path / "nojournal"), "--cache-dir", str(tmp_path),
                   "--no-files"] + BASE)
    assert rc == 1


@pytest.mark.parametrize("argv", [
    ["ticker"],                                         # missing ticker
    ["screen", "PZU"],                                  # unexpected positional
    ["screen", "--universe", "WIG30"],
    ["ticker", "PZU", "--shares", "abc"],
    ["ticker", "PZU", "--shares", "-5"],
    ["ticker", "PZU", "--shares", "1,2"],
    ["portfolio", "--tickers", "PZU,KETY", "--shares", "10"],
    ["portfolio", "--tickers", "PZU"],
    ["ticker", "PZU", "--years", "0"],
    ["ticker", "PZU", "--as-of", "2026-13-01"],
    ["ticker", "PZU", "--isin", "PZU=XX123"],
    ["ticker", "PZU", "--br-code", "PZU"],
    ["screen", "--min-score", "150"],
    ["ticker", "PZU", "--lang", "de"],
    ["ticker", "PZU", "--source", "stooq"],
])
def test_cli_argument_errors(argv):
    with pytest.raises(SystemExit) as e:
        CLI.parse_args(argv)
    assert e.value.code == 2


def test_cli_missing_cached_session_is_not_a_dividend(tmp_path, capsys):
    # integration fix 1.2: a session missing from the cache (NODIV jumps +15% on
    # the missing day) used to produce a spurious ex-date; gpwlib now marks the
    # gap and the CLI warns from gpw_archive.session_gaps
    _write_cache(tmp_path)
    root = tmp_path / "archive" / "shares"
    files = sorted(root.glob("*.json"))
    gone = files[200]
    for f in files[200:]:  # NODIV +15% on the missing day, every later close scaled
        payload = json.loads(f.read_text(encoding="utf-8"))
        for r in payload["rows"]:
            if r["name"] == "NODIV":
                r["close"] = round(r["close"] * 1.15, 2)
        f.write_text(json.dumps(payload), encoding="utf-8")
    gone.unlink()
    out = tmp_path / "out"
    rc = CLI.main(["ticker", "nodiv", "--cache-dir", str(tmp_path), "--output-dir", str(out), "--lang", "en"] + BASE)
    assert rc == 0
    r = json.loads(next(out.glob("gpw_dividend_analyzer_*.json")).read_text(encoding="utf-8"))
    c = r["companies"][0]
    assert all(a["dps"] == 0 for a in c["dps_history"]) or not c["dps_history"]
    txt = json.dumps(r, ensure_ascii=False)
    assert gone.stem in txt  # the warning names the missing session
