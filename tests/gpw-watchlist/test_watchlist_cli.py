"""CLI: commands end to end on a temp state dir, argument errors, exit codes."""

import csv
import datetime as dt
import json

import pytest

from watchlist_testutil import trading_days

import gpw_watchlist as cli
import watchlist_state as ws


@pytest.fixture
def env(tmp_path):
    state = tmp_path / "state"
    journal = tmp_path / "journal"
    (journal / "theses").mkdir(parents=True)
    out = tmp_path / "out"
    base = ["--state-dir", str(state), "--journal-dir", str(journal), "--output-dir", str(out)]

    def run(*argv):
        return cli.main(list(argv[:1]) + list(argv[1:]) + base)

    def state_data():
        return json.loads((state / "watchlist.json").read_text(encoding="utf-8"))

    def thesis(tid, ticker, status, stop=None):
        (journal / "theses" / "{}.json".format(tid)).write_text(json.dumps(
            {"id": tid, "ticker": ticker, "status": status, "created_at": "2026-11-10T10:00:00+00:00",
             "status_history": [{"status": status, "date": "2026-11-10"}], "plan": {"stop": stop}, "fills": []}),
            encoding="utf-8")

    return {"run": run, "state": state_data, "thesis": thesis, "out": out, "tmp": tmp_path}


def item(data, t):
    return next(i for i in data["items"] if i["ticker"] == t)


def test_add_promote_note_drop_remove_flow(env, capsys):
    run = env["run"]
    assert run("add", "kety", "--pivot", "1281", "--stop", "1229", "--note", "VCP 3T", "--tag", "mine") == 0
    it = item(env["state"](), "KETY")
    assert it["stage"] == "candidate" and it["pivot"] == 1281.0 and it["notes"][0]["text"] == "VCP 3T"
    assert it["levels_source"] == "user" and "mine" in it["tags"]
    assert run("promote", "KETY", "--to", "ready") == 0
    out = capsys.readouterr().out
    assert "gotowa" in out and "gpw-trade-journal" in out  # warns: no IDEA/READY thesis
    assert item(env["state"](), "KETY")["stage"] == "ready"
    assert run("note", "KETY", "czekam na wybicie") == 0
    assert run("drop", "KETY", "--reason", "zamknięcie pod stopem") == 0
    it = item(env["state"](), "KETY")
    assert it["stage"] == "dropped" and it["history"][-1]["reason"] == "zamknięcie pod stopem"
    assert run("add", "KETY") == 0  # re-add reopens as candidate
    assert item(env["state"](), "KETY")["stage"] == "candidate"
    assert run("remove", "KETY", "--reason", "pomyłka") == 0
    d = env["state"]()
    assert d["items"] == [] and d["removed"][0]["ticker"] == "KETY"
    assert list((env["tmp"] / "state" / ws.BACKUP_DIR).glob("*.json"))


def test_ready_links_journal_thesis(env, capsys):
    env["thesis"]("th_kety_1", "KETY", "IDEA")
    assert env["run"]("add", "KETY", "--stage", "ready", "--pivot", "100", "--stop", "95") == 0
    out = capsys.readouterr().out
    it = item(env["state"](), "KETY")
    assert it["stage"] == "ready" and it["thesis_id"] == "th_kety_1" and "przed wejściem" not in out


def test_entered_from_active_thesis(env):
    env["run"]("add", "PKOBP", "--stage", "watch")
    assert env["run"]("promote", "PKOBP", "--to", "entered") == 1  # no ACTIVE thesis
    env["thesis"]("th_pko", "PKOBP", "ACTIVE")
    assert env["run"]("list", "--no-files") == 0  # any command syncs the journal
    it = item(env["state"](), "PKOBP")
    assert it["stage"] == "entered" and it["thesis_id"] == "th_pko"


@pytest.mark.parametrize("argv", [
    ["promote", "KETY", "--to", "ready"],           # not on the list
    ["drop", "NOPE", "--reason", "x"],
    ["note", "KETY", "   "],
    ["add", "KE TY"],
    ["levels", "KETY"],
])
def test_input_errors_exit_1(env, capsys, argv):
    if argv[0] == "levels":
        env["run"]("add", "KETY")
    assert env["run"](*argv) == 1
    assert "error:" in capsys.readouterr().err


def test_promote_ready_without_levels_exit_1(env, capsys):
    env["run"]("add", "KETY", "--stage", "watch")
    assert env["run"]("promote", "KETY", "--to", "ready") == 1
    assert "pivot" in capsys.readouterr().err


def test_stop_above_pivot_exit_1(env):
    assert env["run"]("add", "KETY", "--pivot", "100", "--stop", "110") == 1


@pytest.mark.parametrize("argv", [
    ["drop", "KETY"],                               # --reason required
    ["add", "KETY", "--pivot", "abc"],
    ["add", "KETY", "--pivot", "-5"],
    ["add", "KETY", "--stage", "entered"],
    ["promote", "KETY", "--to", "bought"],
    ["list", "--stage", "maybe"],
    ["review", "--stale-days", "0"],
    ["export"],
    ["frobnicate"],
])
def test_argparse_errors_exit_2(env, argv):
    with pytest.raises(SystemExit) as e:
        env["run"](*argv)
    assert e.value.code == 2


def test_ingest_fixtures_report_contract(env, fixtures_dir, capsys):
    assert env["run"]("ingest", "--input-dir", str(fixtures_dir), "--as-of", "2026-11-14", "--json") == 0
    rep = json.loads(capsys.readouterr().out)
    for k in ("skill", "version", "lang", "as_of", "items", "alerts", "summary", "data_provenance", "changes", "reports"):
        assert k in rep
    assert rep["skill"] == "gpw-watchlist" and rep["command"] == "ingest"
    assert set(rep["summary"]["by_stage"]) == set(ws.STAGES)
    assert rep["summary"]["by_stage"]["ready"] == 0
    kety = item(rep, "KETY")
    for k in ("ticker", "stage", "added", "source", "pivot", "stop", "notes"):  # ROADMAP section 4
        assert k in kety
    assert kety["stage"] == "watch" and kety["source"] == "gpw-trend-screener"
    files = sorted(p.name for p in env["out"].iterdir())
    assert any(f.startswith("gpw_watchlist_ingest_") and f.endswith(".json") for f in files)
    assert any(f.endswith(".md") for f in files)


def test_ingest_dry_run_does_not_save(env, fixtures_dir):
    assert env["run"]("ingest", "--input-dir", str(fixtures_dir), "--dry-run", "--no-files") == 0
    assert not (env["tmp"] / "state" / "watchlist.json").exists()


def test_ingest_bad_inputs(env, tmp_path):
    assert env["run"]("ingest", "--input-dir", str(tmp_path / "missing")) == 1
    assert env["run"]("ingest", "--from", "market-breadth", "--input-dir", str(tmp_path)) == 1
    bad = tmp_path / "rules.json"
    bad.write_text('{"screener_watchlist": {"upgrade_to": "ready"}}', encoding="utf-8")
    assert env["run"]("ingest", "--rules", str(bad), "--input-dir", str(tmp_path)) == 1


def _sessions(end, rows_by_day):
    out = []
    for d, rows in zip(trading_days(end, len(rows_by_day)), rows_by_day):
        out.append({"date": d.isoformat(), "rows": rows})
    return out


def test_refresh_with_synthetic_archive(env, fixtures_dir, monkeypatch, capsys):
    env["run"]("add", "KETY", "--stage", "watch", "--pivot", "101", "--stop", "95")
    env["run"]("add", "BROKE", "--stage", "watch", "--pivot", "120", "--stop", "105")
    env["run"]("add", "PGE", "--stage", "candidate")
    rows = []
    for i in range(270):
        last = i == 269
        rows.append([
            {"name": "KETY", "currency": "PLN", "close": 103.0 if last else 100.0, "high": 104 if last else 101,
             "low": 99, "change_pct": 3.0 if last else 0.0, "turnover_k": 3000.0 if last else 1000.0},
            {"name": "BROKE", "currency": "PLN", "close": 100.0, "high": 101, "low": 99, "change_pct": 0.0,
             "turnover_k": 500.0},
            {"name": "PGE", "currency": "PLN", "close": 16.0, "high": 16.2, "low": 15.8, "change_pct": 0.0,
             "turnover_k": 800.0}])
    sess = _sessions(dt.date(2026, 11, 13), rows)

    def fake_load(n, end=None, **kw):
        return [s for s in sess if s["date"] <= end.isoformat()][-n:]

    monkeypatch.setattr(cli.gpw_archive, "load_sessions", fake_load)
    capsys.readouterr()
    assert env["run"]("refresh", "--as-of", "2026-11-16", "--input-dir", str(fixtures_dir), "--json") == 0
    rep = json.loads(capsys.readouterr().out)
    codes = {(a["ticker"], a["code"]) for a in rep["alerts"]}
    assert ("KETY", "pivot_breakout") in codes and ("BROKE", "stop_breached") in codes
    assert ("PGE", "event_soon") in codes  # PGE Q3 report on 2026-11-24 in the fixture calendar
    assert rep["summary"]["stop_breached"] == 1 and rep["as_of"] == "2026-11-13"
    assert "przebiła stop" in rep["answer"] and rep["answer"].startswith("1 spółka")
    assert all("message" in a for a in rep["alerts"])
    assert rep["data_provenance"]["freshness"]["as_of"] == "2026-11-13"
    d = env["state"]()
    assert item(d, "KETY")["market"]["close"] == 103.0 and d["last_refresh"]["as_of"] == "2026-11-13"
    # review then suggests dropping the broken one
    assert env["run"]("review", "--as-of", "2026-11-14", "--json", "--no-files") == 0
    rv = json.loads(capsys.readouterr().out)
    assert any(s["ticker"] == "BROKE" and s["action"] == "drop" and "drop BROKE" in s["command"]
               for s in rv["suggestions"])


def test_refresh_too_few_sessions_exit_1(env, monkeypatch, capsys):
    env["run"]("add", "KETY")
    monkeypatch.setattr(cli.gpw_archive, "load_sessions", lambda *a, **k: [])
    assert env["run"]("refresh", "--offline", "--no-files") == 1
    assert "sessions" in capsys.readouterr().err


def test_list_markdown_english_and_export(env, capsys, tmp_path):
    env["run"]("add", "KETY", "--stage", "watch", "--pivot", "100", "--stop", "90", "--note", "base")
    env["run"]("add", "PKOBP")
    capsys.readouterr()
    assert env["run"]("list", "--lang", "en", "--no-files") == 0
    md = capsys.readouterr().out
    assert md.startswith("# GPW Watchlist") and "Prices have not been refreshed" in md and "Educational material" in md
    assert env["run"]("list", "--stage", "watch", "--json", "--no-files") == 0
    rep = json.loads(capsys.readouterr().out)
    assert [i["ticker"] for i in rep["items"]] == ["KETY"]
    p = tmp_path / "wl.csv"
    assert env["run"]("export", "--csv", str(p)) == 0
    rows = list(csv.DictReader(open(str(p), encoding="utf-8")))
    assert {r["ticker"] for r in rows} == {"KETY", "PKOBP"} and rows[0]["last_note"] in ("base", "")


def test_pl_plural():
    assert [cli.pl_plural(n) for n in (1, 2, 4, 5, 12, 22, 25)] == \
        ["spółka", "spółki", "spółki", "spółek", "spółek", "spółki", "spółek"]
