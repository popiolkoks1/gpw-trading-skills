import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

import gpw_signal_postmortem as cli
import postmortem_engine as pe

FIX = Path(__file__).resolve().parent / "fixtures"
SCREENER = FIX / "gpw_trend_screener_2026-11-13_fixture.json"
REGIME = FIX / "gpw_market_regime_2026-11-13_fixture.json"


def synthetic_book():
    cal, d = [], dt.date(2025, 11, 3)
    while len(cal) < 280:
        if d.weekday() < 5:
            cal.append(d.isoformat())
        d += dt.timedelta(days=1)
    b = pe.PriceBook(cal)
    n = len(cal)

    def add(name, closes, kind="share"):
        b.add(pe.Instrument(name, kind, cal, closes, closes, closes, closes, None, [1000.0] * n))

    add("WIG", [100 * 1.001 ** i for i in range(n)], "index")
    add("WIG20", [50 * 1.001 ** i for i in range(n)], "index")
    add("SWIG80", [50 * 0.999 ** i for i in range(n)], "index")
    add("KETY", [1000 * 1.002 ** i for i in range(n)])
    add("PKOBP", [70 * 0.998 ** i for i in range(n)])
    add("PGE", [9.0] * n)
    add("LPP", [15000.0] * n)
    return b


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(cli, "load_book", lambda args: (synthetic_book(), {"sector": {}, "size": {"KETY": "WIG20"}}, 280))


def run(argv, capsys):
    code = cli.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


# ---------------------------------------------------------- arg errors

@pytest.mark.parametrize("argv", [
    [],
    ["record"],
    ["backfill"],
    ["record", "--latest", "x.json"],
    ["evaluate", "--horizons", "abc"],
    ["evaluate", "--horizons", "0,5"],
    ["evaluate", "--primary-horizon", "7"],
    ["evaluate", "--min-n", "1"],
    ["evaluate", "--entry", "vwap"],
    ["evaluate", "--since", "13.11.2026"],
    ["evaluate", "--lang", "de"],
])
def test_argparse_errors(argv):
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(argv)
    assert exc.value.code == 2


# -------------------------------------------------------------- record

def test_record_is_idempotent(tmp_path, capsys):
    inp = tmp_path / "in"
    inp.mkdir()
    shutil.copy(SCREENER, inp)
    shutil.copy(REGIME, inp)
    (inp / "junk.json").write_text('{"skill": "gpw-position-sizer"}', encoding="utf-8")
    (inp / "broken.json").write_text("{", encoding="utf-8")
    state = tmp_path / "state"
    code, out, _ = run(["record", "--input-dir", str(inp), "--state-dir", str(state)], capsys)
    assert code == 0 and "skipped 2" in out
    n1 = len(json.loads((state / "ledger.json").read_text(encoding="utf-8"))["signals"])
    code, out, _ = run(["record", "--input-dir", str(inp), "--state-dir", str(state)], capsys)
    assert code == 0 and "new signals: 0" in out
    assert len(json.loads((state / "ledger.json").read_text(encoding="utf-8"))["signals"]) == n1
    code, out, _ = run(["record", "--input-dir", str(inp), "--latest", "--state-dir", str(state), "--dry-run"], capsys)
    assert code == 0 and "DRY RUN" in out


def test_record_explicit_bad_file_fails(tmp_path, capsys):
    code, _, err = run(["record", str(tmp_path / "nope.json"), "--state-dir", str(tmp_path)], capsys)
    assert code == 1 and "ERROR" in err
    bad = tmp_path / "sizer.json"
    bad.write_text('{"skill": "gpw-position-sizer"}', encoding="utf-8")
    code, _, err = run(["record", str(bad), "--state-dir", str(tmp_path)], capsys)
    assert code == 1 and "unsupported" in err
    assert not (tmp_path / "ledger.json").exists()


def test_backfill_list(tmp_path, capsys):
    lst = tmp_path / "list.txt"
    lst.write_text("# historical reports, oldest first\n{}\n{}\n".format(REGIME, SCREENER), encoding="utf-8")
    code, out, _ = run(["backfill", "--list", str(lst), "--state-dir", str(tmp_path / "s")], capsys)
    assert code == 0 and "Reports ingested: 2" in out
    code, out, _ = run(["list", "--state-dir", str(tmp_path / "s"), "--json"], capsys)
    data = json.loads(out)
    assert data["signals"] > 20 and data["regime_timelines"][0]["index"] == "WIG"
    code, _, err = run(["backfill", "--list", str(tmp_path / "missing.txt"), "--state-dir", str(tmp_path)], capsys)
    assert code == 1


# ------------------------------------------------------------ evaluate

def test_evaluate_end_to_end(tmp_path, capsys, patched):
    state = tmp_path / "state"
    run(["record", str(SCREENER), str(REGIME), "--state-dir", str(state)], capsys)
    out_dir = tmp_path / "out"
    code, md, _ = run(["evaluate", "--state-dir", str(state), "--output-dir", str(out_dir), "--input-dir", str(tmp_path),
                       "--write-feedback", "--lang", "en"], capsys)
    assert code == 0
    rep = json.loads(next(out_dir.glob("gpw_signal_postmortem_*.json")).read_text(encoding="utf-8"))
    for k in ("skill", "version", "lang", "as_of", "data_provenance", "buckets", "feedback", "signals_evaluated", "pending"):
        assert k in rep
    assert rep["skill"] == "gpw-signal-postmortem" and rep["demo"] is False
    assert rep["signals_evaluated"] > 0
    assert rep["feedback"]["auto_apply"] is False
    # screener signals dated on the last-but-one synthetic session cannot have matured
    kety = next(r for r in rep["results"] if r["ticker"] == "KETY")
    assert kety["status"] in ("pending", "evaluated")
    md_file = next(out_dir.glob("gpw_signal_postmortem_*.md")).read_text(encoding="utf-8")
    assert "## Answer" in md_file and "Caveats" in md_file and md_file.strip().endswith("_")
    assert md_file.index("## Answer") < md_file.index("## Caveats")
    fb = json.loads(next(out_dir.glob("gpw_signal_feedback_*.json")).read_text(encoding="utf-8"))
    assert fb["report_type"] == "feedback" and "feedback" in fb
    # feedback subcommand finds the evaluation report (not its own feedback file)
    code, _, _ = run(["feedback", "--input-dir", str(out_dir), "--output-dir", str(out_dir)], capsys)
    assert code == 0 and len(list(out_dir.glob("gpw_signal_feedback_*.json"))) >= 1


def test_evaluate_empty_ledger_and_filters(tmp_path, capsys, patched):
    code, md, _ = run(["evaluate", "--state-dir", str(tmp_path), "--no-files", "--input-dir", str(tmp_path)], capsys)
    assert code == 0 and "pusty" in md
    run(["record", str(REGIME), "--state-dir", str(tmp_path)], capsys)
    code, md, _ = run(["evaluate", "--state-dir", str(tmp_path), "--no-files", "--input-dir", str(tmp_path),
                       "--source", "gpw-market-regime", "--kind", "ftd", "--lang", "en"], capsys)
    assert code == 0 and "ftd" in md and "regime_correction" not in md.split("## Feedback")[0]


def test_demo_is_labelled_and_never_writes_feedback(tmp_path, capsys, patched):
    out_dir = tmp_path / "out"
    code, md, err = run(["evaluate", "--demo", "--state-dir", str(tmp_path), "--output-dir", str(out_dir),
                         "--input-dir", str(tmp_path), "--write-feedback"], capsys)
    assert code == 0 and "TRYB DEMO" in md and "feedback file not written" in err
    rep = json.loads(next(out_dir.glob("gpw_signal_postmortem_*.json")).read_text(encoding="utf-8"))
    assert rep["demo"] is True and rep["mode"] == "demo" and rep["feedback"]["demo"] is True
    assert not list(out_dir.glob("gpw_signal_feedback_*.json"))
    code, _, err = run(["feedback", "--input-dir", str(out_dir), "--output-dir", str(out_dir)], capsys)
    assert code == 1 and "DEMO" in err


def test_feedback_without_report_fails(tmp_path, capsys):
    code, _, err = run(["feedback", "--input-dir", str(tmp_path)], capsys)
    assert code == 1 and "evaluate" in err
    code, _, err = run(["feedback", "--postmortem-json", str(SCREENER), "--input-dir", str(tmp_path)], capsys)
    assert code == 1
