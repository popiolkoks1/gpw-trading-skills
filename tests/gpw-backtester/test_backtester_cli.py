import datetime as dt
import json
import math

import pytest

import backtester_data as bd
import gpw_backtester as cli


def _write(cache, kind, day, rows):
    p = cache / "archive" / kind / "{}.json".format(day.isoformat())
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema": 1, "date": day.isoformat(), "type": kind, "session": True, "rows": rows,
                             "source_url": "synthetic", "retrieved_at": "2026-01-01T00:00:00+00:00"}))


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    root = tmp_path_factory.mktemp("cache")
    days = list(bd.probable_sessions(dt.date(2019, 6, 3), dt.date(2021, 12, 30)))
    for k, d in enumerate(days):
        rows = []
        for j in range(12):
            drift = 0.0004 * (j - 4)
            c = round(20.0 * math.exp(drift * k + 0.03 * math.sin(k / (7.0 + j))), 2)
            rows.append({"name": "S{:02d}".format(j), "currency": "PLN", "open": c, "high": round(c * 1.01, 2),
                         "low": round(c * 0.99, 2), "close": c, "change_pct": None, "turnover_k": 2000.0})
        _write(root, "shares", d, rows)
        w = 60000.0 * math.exp(0.0002 * k)
        _write(root, "indices", d, [{"name": "WIG", "currency": "", "open": w, "high": w, "low": w, "close": w,
                                     "change_pct": None, "turnover_k": 1e6}])
    return root


def run(args, capsys):
    rc = cli.main(args)
    out = capsys.readouterr()
    return rc, out.out, out.err


BASE = ["--start", "2020-06-01", "--end", "2021-12-30", "--offline", "--no-files"]


def test_cli_run_json_contract(cache, capsys):
    rc, out, _ = run(["run", "--strategy", "momentum_rotation", "--param", "top_n=3", "--param", "lookback=126",
                      "--param", "min_turnover_k=0", "--cache-dir", str(cache), "--json"] + BASE, capsys)
    assert rc == 0
    r = json.loads(out)
    assert r["skill"] == "gpw-backtester" and r["version"] == "1.0.0"
    for k in ("cagr", "max_dd", "sharpe", "trades", "win_rate", "expectancy_r", "exposure"):
        assert k in r["metrics"]
    for k in ("walk_forward", "deflated_sharpe", "oos_is_ratio", "verdict"):
        assert k in r["robustness"]
    assert r["robustness"]["verdict"] in ("NOT_EVALUABLE", "REJECT", "FRAGILE", "PROMISING", "ROBUST")
    assert "period_lt_5y" in r["robustness"]["blocking_reasons"] or r["robustness"]["verdict"] in ("NOT_EVALUABLE", "REJECT")
    assert r["data_provenance"]["survivorship_free"] is True
    assert r["data_provenance"]["point_in_time_membership"] is False
    assert r["equity_curve"] and r["equity_curve"][-1]["date"] == r["period"]["end"]
    assert r["warnings"]["missing_sessions_count"] == 0


def test_cli_grid_markdown_and_files(cache, capsys, tmp_path):
    rc, out, _ = run(["grid", "--strategy", "sma_cross", "--grid", "fast=10,20;slow=50,100", "--param", "min_turnover_k=0",
                      "--is-years", "0.5", "--oos-years", "0.25", "--cache-dir", str(cache), "--lang", "en",
                      "--start", "2020-06-01", "--end", "2021-12-30", "--offline", "--output-dir", str(tmp_path)], capsys)
    assert rc == 0
    assert "## Verdict:" in out and "Parameter grid (4 variants)" in out
    files = sorted(p.name for p in tmp_path.iterdir())
    assert any(f.startswith("gpw_backtest_sma_cross_") and f.endswith(".json") for f in files)
    r = json.loads(next(p for p in tmp_path.iterdir() if p.suffix == ".json").read_text())
    assert r["robustness"]["trials"] == 4 and r["robustness"]["pbo"]["available"]
    assert r["robustness"]["walk_forward"]["anchored"]["available"]


def test_cli_rules_file(cache, capsys, tmp_path):
    rules = tmp_path / "r.json"
    rules.write_text(json.dumps({"name": "above_sma", "entry": {"left": {"ind": "close"}, "op": "crosses_above",
                                                                "right": {"ind": "sma", "n": 50}},
                                 "exit": {"left": {"ind": "close"}, "op": "<", "right": {"ind": "sma", "n": 50}},
                                 "stop": {"pct": 8}, "params": {"min_turnover_k": 0}}))
    rc, out, _ = run(["run", "--rules", str(rules), "--cache-dir", str(cache)] + BASE, capsys)
    assert rc == 0 and "rules:above_sma" in out and "Werdykt" in out


def test_cli_errors(cache, capsys, tmp_path):
    with pytest.raises(SystemExit) as e:
        cli.main(["run", "--start", "2020-13-01"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        cli.main(["run", "--capital", "-5"])
    assert e.value.code == 2
    rc, _, err = run(["run", "--strategy", "nope", "--cache-dir", str(cache)] + BASE, capsys)
    assert rc == 1 and "unknown strategy" in err
    rc, _, err = run(["run", "--param", "stop_pct", "--cache-dir", str(cache)] + BASE, capsys)
    assert rc == 1 and "KEY=VALUE" in err
    rc, _, err = run(["run", "--rules", str(tmp_path / "missing.json"), "--cache-dir", str(cache)] + BASE, capsys)
    assert rc == 1 and "not found" in err
    bad = tmp_path / "bad.json"
    bad.write_text('{"entry": {"left": {"ind": "eval"}, "op": ">", "right": 1}, "stop": {"pct": 5}}')
    rc, _, err = run(["run", "--rules", str(bad), "--cache-dir", str(cache)] + BASE, capsys)
    assert rc == 1 and "unknown indicator" in err
    rc, _, err = run(["grid", "--strategy", "trend_template", "--max-trials", "3", "--cache-dir", str(cache)] + BASE, capsys)
    assert rc == 1 and "max-trials" in err
    rc, _, err = run(["grid", "--strategy", "dca_buy_hold", "--cache-dir", str(cache)] + BASE, capsys)
    assert rc == 1 and "no parameter grid" in err
    rc, _, err = run(["run", "--start", "2021-12-01", "--end", "2021-11-01", "--cache-dir", str(cache), "--offline"], capsys)
    assert rc == 1
    empty = tmp_path / "empty"
    empty.mkdir()
    rc, _, err = run(["run", "--cache-dir", str(empty)] + BASE, capsys)
    assert rc == 1 and "share sessions" in err


def test_cli_reports_missing_sessions(cache, capsys, tmp_path):
    import shutil
    c2 = tmp_path / "c2"
    shutil.copytree(cache, c2)
    (c2 / "archive" / "shares" / "2021-03-01.json").unlink()
    rc, out, _ = run(["run", "--strategy", "dca_buy_hold", "--cache-dir", str(c2), "--json"] + BASE, capsys)
    r = json.loads(out)
    assert rc == 0 and r["warnings"]["missing_sessions_count"] == 1
    assert r["robustness"]["verdict"] == "NOT_EVALUABLE"


def test_list_strategies(capsys):
    assert cli.main(["list-strategies", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert {s["name"] for s in d["strategies"]} == {"trend_template", "sma_cross", "breakout_52w",
                                                     "momentum_rotation", "dca_buy_hold"}
    assert "crosses_above" in d["rules_spec"]["ops"]


# --- integration fix 1.2: public streaming panel API, gap-aware adjustment -----------------

def test_loader_uses_public_panel_api():
    src = (__import__("pathlib").Path(bd.__file__)).read_text(encoding="utf-8")
    assert "_adjust_series" not in src and "build_panel(" in src


def test_load_market_skips_adjustment_across_missing_session(tmp_path):
    days = [dt.date(2022, 1, 4), dt.date(2022, 1, 5), dt.date(2022, 1, 7)]  # 01-03 missing, 01-06 holiday
    _write(tmp_path, "shares", dt.date(2021, 12, 30),
           [{"name": "A", "currency": "PLN", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
             "change_pct": 0.0, "turnover_k": 10.0}])
    for k, d in enumerate(days):  # A rose 10% on the missing day, then +1% a day
        c = 111.1 * 1.01 ** k
        _write(tmp_path, "shares", d, [{"name": "A", "currency": "PLN", "open": c, "high": c * 1.01, "low": c * 0.99,
                                        "close": c, "change_pct": 1.0, "turnover_k": 10.0}])
    md, miss_s, _ = bd.load_market(dt.date(2021, 12, 30), dt.date(2022, 1, 7), cache_dir=tmp_path, offline=True)
    assert miss_s == ["2022-01-03"]
    a = md.stocks["A"]
    assert a.adjustments == []
    assert a.close[0] == pytest.approx(100.0) and a.turn[0] == pytest.approx(10000.0)
    assert md.calendar[0] == "2021-12-30" and md.meta["sessions_loaded"] == 4
