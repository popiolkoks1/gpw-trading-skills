"""Orchestrator (`run`) with fake sibling skills: ok, failing, timing out."""

import json
import textwrap
from pathlib import Path

import pytest

import daily_brief_runner as runner
import gpw_daily_brief as cli

FAKE_OK = textwrap.dedent('''
    import json, sys, pathlib
    a = sys.argv[1:]
    out = pathlib.Path(a[a.index("--output-dir") + 1]) if "--output-dir" in a else pathlib.Path(".")
    skill = "{skill}"
    data = {{"skill": skill, "version": "1.0.0", "lang": "pl", "as_of": "2026-11-13", "argv": a}}
    data.update({extra})
    (out / "{prefix}_2026-11-13_170500.json").write_text(json.dumps(data))
    print("ok", skill)
''')
FAKE_FAIL = "import sys\nprint('boom: no cached sessions', file=sys.stderr)\nsys.exit(1)\n"
FAKE_SLOW = "import time\ntime.sleep(30)\n"


def make_skill(root: Path, skill: str, script: str, body: str):
    d = root / skill / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    (d / script).write_text(body, encoding="utf-8")


def ok_body(skill, prefix, extra=None):
    return FAKE_OK.format(skill=skill, prefix=prefix, extra=json.dumps(extra or {}))


@pytest.fixture
def fake_root(tmp_path):
    root = tmp_path / "skills"
    make_skill(root, "gpw-market-breadth", "gpw_breadth.py",
               ok_body("gpw-market-breadth", "gpw_breadth", {"composite": {"score": 55.0, "zone": "neutral"}}))
    make_skill(root, "gpw-sector-trend", "gpw_sector_trend.py", FAKE_FAIL)
    make_skill(root, "gpw-market-regime", "gpw_market_regime.py",
               ok_body("gpw-market-regime", "gpw_market_regime",
                       {"regime": {"state": "rally_attempt", "top_risk_score": 50, "distribution_days": 3,
                                   "as_of": "2026-11-13"}}))
    make_skill(root, "gpw-exposure-coach", "gpw_exposure_coach.py", FAKE_SLOW)
    return root


def ctx(tmp_path, **kw):
    c = {"output_dir": str(tmp_path / "out"), "lang": "pl", "offline": True, "as_of": None,
         "has_positions": False, "watchlist_path": None}
    c.update(kw)
    return c


def test_select_steps_subset_skip_weekly():
    assert [s["id"] for s in runner.select_steps(None, None, False)] == runner.DAILY_IDS
    weekly = [s["id"] for s in runner.select_steps(None, None, True)]
    assert weekly == runner.DAILY_IDS + runner.WEEKLY_IDS
    assert "macro" in weekly and "dividend" in weekly
    sub = [s["id"] for s in runner.select_steps(["regime", "breadth"], None, False)]
    assert sub == ["breadth", "regime"]  # pipeline order, not argument order
    assert "shorts" not in [s["id"] for s in runner.select_steps(None, ["shorts"], False)]
    with pytest.raises(ValueError):
        runner.select_steps(["nope"], None, False)
    with pytest.raises(ValueError):
        runner.select_steps(None, ["nope"], False)


def test_pipeline_order_matches_roadmap():
    ids = runner.DAILY_IDS
    for a, b in [("breadth", "regime"), ("sector", "regime"), ("regime", "exposure"), ("exposure", "screener"),
                 ("screener", "watchlist-ingest"), ("calendar", "watchlist-refresh"), ("shorts", "earnings"),
                 ("earnings", "postmortem-record")]:
        assert ids.index(a) < ids.index(b)


def test_run_continues_after_failure_and_timeout(tmp_path, fake_root):
    steps = runner.select_steps(["breadth", "sector", "regime", "exposure", "shorts"], None, False)
    logged = []
    recs = runner.run_pipeline(ctx(tmp_path), steps, skills_root=fake_root, timeout=2, log=logged.append)
    st = {r["step"]: r for r in recs}
    assert st["breadth"]["status"] == "ok" and st["breadth"]["exit_code"] == 0
    assert st["breadth"]["outputs"] and st["breadth"]["outputs"][0].endswith("gpw_breadth_2026-11-13_170500.json")
    assert st["sector"]["status"] == "failed" and st["sector"]["exit_code"] == 1
    assert "boom" in st["sector"]["stderr_tail"]
    assert st["regime"]["status"] == "ok"  # ran after the failure
    assert st["exposure"]["status"] == "timeout" and st["exposure"]["exit_code"] is None
    assert 1.5 <= st["exposure"]["duration_s"] < 10
    assert st["shorts"]["status"] == "not_installed"
    assert len(logged) == 5 and "timeout" in logged[3]
    # every step got the shared output dir and --offline
    argv = json.loads((tmp_path / "out" / "gpw_breadth_2026-11-13_170500.json").read_text())["argv"]
    assert "--offline" in argv and argv[argv.index("--output-dir") + 1] == str(tmp_path / "out")
    reg = json.loads((tmp_path / "out" / "gpw_market_regime_2026-11-13_170500.json").read_text())["argv"]
    assert reg[reg.index("--input-dir") + 1] == str(tmp_path / "out")


def test_conditions_skip_weekly_portfolio_steps(tmp_path, fake_root):
    make_skill(fake_root, "gpw-portfolio-check", "portfolio_check.py", ok_body("gpw-portfolio-check", "p"))
    make_skill(fake_root, "gpw-event-calendar", "gpw_event_calendar.py", ok_body("gpw-event-calendar", "e"))
    steps = runner.select_steps(["portfolio", "calendar-portfolio"], None, True)
    recs = runner.run_pipeline(ctx(tmp_path), steps, skills_root=fake_root)
    assert {r["step"]: r["status"] for r in recs} == {"calendar-portfolio": "skipped", "portfolio": "skipped"}
    recs = runner.run_pipeline(ctx(tmp_path, has_positions=True), steps[1:], skills_root=fake_root)
    assert recs[0]["status"] == "ok"


def test_args_builders_pass_options(tmp_path):
    c = ctx(tmp_path, as_of="2026-11-12", state_root="/sandbox", journal_dir="/j", account_size=50000,
            insider_days=2, earnings_lookback=20)
    by = {s["id"]: s["args"](c) for s in runner.STEPS}
    assert by["breadth"][by["breadth"].index("--output-dir") + 1] == c["output_dir"] and "--as-of" in by["breadth"]
    assert by["breadth"][by["breadth"].index("--state-dir") + 1] == "/sandbox"
    assert by["insiders"][:3] == ["insiders", "--days", "2"]
    assert by["insiders"][by["insiders"].index("--state-dir") + 1] == "/sandbox"
    assert by["earnings"][:3] == ["scan", "--lookback-days", "20"]
    assert by["postmortem-record"][by["postmortem-record"].index("--state-dir") + 1] == str(Path("/sandbox") / "signals")
    assert "--offline" not in by["postmortem-record"] and "--offline" not in by["exposure"]
    assert by["exposure"][by["exposure"].index("--account-size") + 1] == "50000"
    assert by["exposure"][by["exposure"].index("--state-dir") + 1] == "/j"
    assert by["dividend"][by["dividend"].index("--journal-dir") + 1] == "/j"
    assert by["calendar"][:3] == ["upcoming", "--sessions", "10"]


def test_cli_run_with_fake_skills_writes_log_and_brief(tmp_path, fake_root, capsys):
    out = tmp_path / "out"
    rc = cli.main(["run", "--skills-dir", str(fake_root), "--output-dir", str(out), "--steps",
                   "breadth,sector,regime", "--offline", "--timeout", "5", "--no-journal", "--no-watchlist",
                   "--no-prices", "--as-of", "2026-11-16"])
    text = capsys.readouterr().out
    assert rc == 0
    assert "[ 2/3] sector" in text and "failed" in text
    logs = list(out.glob("gpw_daily_brief_run_*.json"))
    assert len(logs) == 1
    log = json.loads(logs[0].read_text())
    assert log["mode"] == "run" and log["counts"]["ok"] == 2 and log["counts"]["failed"] == 1
    assert log["brief"]["json"].endswith(".json")
    brief = json.loads(Path(log["brief"]["json"]).read_text())
    assert brief["snapshot"]["regime_state"] == "rally_attempt"
    rows = {r["input"]: r["status"] for r in brief["inputs"]}
    assert rows["sector"] == "missing" and rows["regime"] == "ok"
    assert "próba odbicia" in text  # the brief is printed after the run log


def test_cli_run_strict_and_unknown_step(tmp_path, fake_root, capsys):
    rc = cli.main(["run", "--skills-dir", str(fake_root), "--output-dir", str(tmp_path / "o"), "--steps", "sector",
                   "--strict", "--no-brief", "--no-journal", "--no-watchlist"])
    assert rc == 1
    rc = cli.main(["run", "--output-dir", str(tmp_path / "o"), "--steps", "nope", "--no-journal"])
    assert rc == 2
    assert "unknown step" in capsys.readouterr().err


def test_cli_run_list_steps(capsys):
    assert cli.main(["run", "--list-steps"]) == 0
    out = capsys.readouterr().out
    assert "breadth" in out and "dividend (weekly)" in out


def test_real_skills_are_located_as_siblings():
    # in the repository every pipeline step resolves to an existing script
    missing = [s["id"] for s in runner.STEPS if runner.resolve_script(s, runner.SKILLS_ROOT) is None
               and s["skill"] != "gpw-watchlist"]
    assert missing == []

