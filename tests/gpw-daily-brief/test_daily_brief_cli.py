"""CLI of gpw-daily-brief: brief on fixtures, files, errors, exit codes."""

import json

import pytest

import gpw_daily_brief as cli


def base(report_dir, fixtures, *extra):
    return ["brief", "--input-dir", str(report_dir), "--as-of", "2026-11-16", "--journal-dir",
            str(fixtures / "journal"), "--watchlist", str(fixtures / "watchlist.json"), "--cache-dir",
            str(fixtures / "cache")] + list(extra)


def test_brief_writes_json_and_md(report_dir, fixtures, capsys):
    assert cli.main(base(report_dir, fixtures)) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Dzienny brief GPW — 2026-11-13")
    js = sorted(report_dir.glob("gpw_daily_brief_2*.json"))
    md = sorted(report_dir.glob("gpw_daily_brief_2*.md"))
    assert len(js) == 1 and len(md) == 1
    b = json.loads(js[0].read_text(encoding="utf-8"))
    assert b["data_provenance"]["as_of"] == "2026-11-13"
    assert b["data_provenance"]["freshness"]["sessions_behind"] <= 1
    assert b["prices"]["session"] == "2026-11-13" and b["prices"]["tickers"] >= 8
    assert b["journal"]["open_positions"] == 4
    tor = [p for p in b["positions"]["items"] if p["ticker"] == "TORPOL"][0]
    assert tor["close"] == 57.0 and "below_stop" in tor["flags"]


def test_second_brief_uses_previous_brief_as_baseline(report_dir, fixtures, capsys):
    prev = {"skill": "gpw-daily-brief", "version": "1.0.0", "mode": "daily", "as_of": "2026-11-12",
            "snapshot": {"regime_state": "confirmed_uptrend"}}
    (report_dir / "gpw_daily_brief_2026-11-12_180500.json").write_text(json.dumps(prev), encoding="utf-8")
    # a run log of the same skill must never be taken as a brief
    (report_dir / "gpw_daily_brief_run_2026-11-13_180000.json").write_text(
        json.dumps({"skill": "gpw-daily-brief", "mode": "run", "as_of": "2026-11-13"}), encoding="utf-8")
    assert cli.main(base(report_dir, fixtures, "--no-files", "--json")) == 0
    b = json.loads(capsys.readouterr().out.split("\nJSON:")[0])
    assert b["baseline"]["previous_brief"] == "gpw_daily_brief_2026-11-12_180500.json"
    assert b["baseline"]["new_signals_since"] == "2026-11-12"


def test_weekly_and_full_flags(report_dir, fixtures, capsys):
    assert cli.main(base(report_dir, fixtures, "--weekly", "--full", "--no-files", "--lang", "en")) == 0
    out = capsys.readouterr().out
    assert out.startswith("# GPW weekly brief") and "## Weekly review" in out
    assert "| Input | File |" in out  # full freshness table


def test_no_prices_no_journal_no_watchlist(report_dir, fixtures, capsys):
    assert cli.main(base(report_dir, fixtures, "--no-prices", "--no-journal", "--no-watchlist", "--no-files")) == 0
    out = capsys.readouterr().out
    assert "Dziennik pominięty" in out and "Watchlista pominięta" in out


def test_empty_folder_exits_1(tmp_path, capsys):
    rc = cli.main(["brief", "--input-dir", str(tmp_path), "--no-journal", "--no-files"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "run --output-dir" in err


@pytest.mark.parametrize("argv", [
    ["brief", "--as-of", "16-11-2026"],
    ["brief", "--calendar-sessions", "0"],
    ["brief", "--lang", "de"],
    ["nope"],
    [],
])
def test_bad_arguments_exit_2(argv):
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert e.value.code == 2
