"""gpwlib 1.2 - reports: discovery of another skill's newest JSON (stale / missing / corrupt)."""

import datetime as dt
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

from gpwlib import reports  # noqa: E402

REF = dt.date(2026, 11, 13)  # Friday session


def _write(folder: Path, name: str, payload, mtime=None) -> Path:
    p = folder / name
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _rep(skill, as_of, where="top"):
    d = {"skill": skill, "version": "1.0.0"}
    if where == "top":
        d["as_of"] = as_of
    elif where == "prov":
        d["data_provenance"] = {"as_of": as_of}
    elif where == "regime":
        d["regime"] = {"as_of": as_of, "state": "correction"}
    return d


def test_newest_by_as_of_then_mtime(tmp_path):
    _write(tmp_path, "gpw_regime_a.json", _rep("gpw-market-regime", "2026-11-12"), mtime=3000)
    _write(tmp_path, "gpw_regime_b.json", _rep("gpw-market-regime", "2026-11-13", "regime"), mtime=1000)
    _write(tmp_path, "gpw_regime_c.json", _rep("gpw-market-regime", "2026-11-13", "prov"), mtime=2000)
    _write(tmp_path, "other.json", _rep("gpw-market-breadth", "2026-11-14"))
    r = reports.find_report("gpw-market-regime", tmp_path, reference=REF)
    assert r["file"] == "gpw_regime_c.json"            # same as_of -> newer mtime wins
    assert r["as_of"] == "2026-11-13" and r["sessions_behind"] == 0
    assert r["stale"] is False and r["warning"] is None
    assert r["data"]["skill"] == "gpw-market-regime"


def test_stale_report_has_bilingual_warning(tmp_path):
    _write(tmp_path, "x.json", _rep("gpw-market-breadth", "2026-11-06"))
    r = reports.find_report("gpw-market-breadth", tmp_path, reference=REF, lang="en")
    # 9, 10, 12, 13 Nov (11 Nov = Independence Day, no session)
    assert r["sessions_behind"] == 4 and r["stale"] is True
    assert "4 sesji" in r["warning"]["pl"] and "4 sessions" in r["warning"]["en"]
    assert r["warning_text"] == r["warning"]["en"]
    r2 = reports.find_report("gpw-market-breadth", tmp_path, reference=REF, max_sessions_behind=4)
    assert r2["stale"] is False


def test_missing_dir_or_no_match_returns_none(tmp_path):
    assert reports.find_report("gpw-market-regime", tmp_path / "nope") is None
    assert reports.find_report("gpw-market-regime", tmp_path) is None
    assert reports.find_report("gpw-market-regime", None) is None
    _write(tmp_path, "a.json", _rep("gpw-sector-trend", "2026-11-13"))
    assert reports.find_report("gpw-market-regime", tmp_path) is None


def test_corrupt_files_are_skipped_in_discovery(tmp_path):
    _write(tmp_path, "bad.json", "{not json")
    _write(tmp_path, "list.json", "[1, 2]")
    _write(tmp_path, "ok.json", _rep("gpw-sector-trend", "2026-11-13"))
    r = reports.find_report("gpw-sector-trend", tmp_path, reference=REF)
    assert r["file"] == "ok.json"


def test_explicit_path_corrupt_missing_or_wrong_skill_raises(tmp_path):
    bad = _write(tmp_path, "bad.json", "{oops")
    with pytest.raises(reports.ReportError):
        reports.find_report("gpw-market-regime", tmp_path, explicit_path=bad)
    with pytest.raises(reports.ReportError):
        reports.find_report("gpw-market-regime", tmp_path, explicit_path=tmp_path / "missing.json")
    other = _write(tmp_path, "o.json", _rep("gpw-market-breadth", "2026-11-13"))
    with pytest.raises(reports.ReportError):
        reports.find_report("gpw-market-regime", tmp_path, explicit_path=other)
    arr = _write(tmp_path, "arr.json", "[]")
    with pytest.raises(reports.ReportError):
        reports.load_report(arr)


def test_explicit_path_wins_over_newer_files(tmp_path):
    old = _write(tmp_path, "old.json", _rep("gpw-market-regime", "2026-11-09"))
    _write(tmp_path, "new.json", _rep("gpw-market-regime", "2026-11-13"))
    r = reports.find_report("gpw-market-regime", tmp_path, explicit_path=old, reference=REF)
    assert r["file"] == "old.json" and r["sessions_behind"] == 3 and r["stale"] is False  # 10, 12, 13 Nov


def test_report_without_as_of_is_stale_with_warning(tmp_path):
    _write(tmp_path, "n.json", {"skill": "gpw-exposure-coach"})
    r = reports.find_report("gpw-exposure-coach", tmp_path, reference=REF)
    assert r["as_of"] is None and r["sessions_behind"] is None and r["stale"] is True
    assert "as_of" in r["warning"]["en"]


def test_pattern_fallback_for_untagged_reports(tmp_path):
    _write(tmp_path, "gpw_breadth_2026-11-13_170000.json", {"as_of": "2026-11-13"})
    _write(tmp_path, "random.json", {"as_of": "2026-11-13"})
    assert reports.find_report("gpw-market-breadth", tmp_path) is None
    r = reports.find_report("gpw-market-breadth", tmp_path, pattern="gpw_breadth_*.json", reference=REF)
    assert r["file"] == "gpw_breadth_2026-11-13_170000.json"
    # a tagged report takes precedence over untagged pattern matches
    _write(tmp_path, "tagged.json", _rep("gpw-market-breadth", "2026-11-11"))
    r = reports.find_report("gpw-market-breadth", tmp_path, pattern="gpw_breadth_*.json", reference=REF)
    assert r["file"] == "tagged.json"


def test_historical_reference_ignores_future_reports(tmp_path):
    _write(tmp_path, "a.json", _rep("gpw-market-regime", "2026-03-13"))
    _write(tmp_path, "b.json", _rep("gpw-market-regime", "2026-11-13"))
    r = reports.find_report("gpw-market-regime", tmp_path, reference=dt.date(2026, 3, 16))
    assert r["file"] == "a.json" and r["sessions_behind"] == 1 and not r["stale"]


def test_find_reports_many(tmp_path):
    _write(tmp_path, "r.json", _rep("gpw-market-regime", "2026-11-13"))
    p = _write(tmp_path, "s.json", _rep("gpw-sector-trend", "2026-11-12"))
    out = reports.find_reports(["gpw-market-regime", "gpw-market-breadth"], tmp_path, reference=REF)
    assert out["gpw-market-regime"]["file"] == "r.json" and out["gpw-market-breadth"] is None
    out = reports.find_reports({"gpw-sector-trend": p, "gpw-market-regime": None}, tmp_path, reference=REF)
    assert out["gpw-sector-trend"]["as_of"] == "2026-11-12"


def test_report_as_of_lookup_order():
    assert reports.report_as_of({"as_of": "2026-11-13T17:00:00", "data_provenance": {"as_of": "2026-01-01"}}) \
        == dt.date(2026, 11, 13)
    assert reports.report_as_of({"data_provenance": {"as_of": "2026-11-12"}}) == dt.date(2026, 11, 12)
    assert reports.report_as_of({"regime": {"as_of": "2026-11-11"}}) == dt.date(2026, 11, 11)
    assert reports.report_as_of({"as_of": "garbage-date"}) is None
    assert reports.report_as_of([]) is None


def test_default_reference_is_today(tmp_path):
    _write(tmp_path, "a.json", _rep("gpw-market-regime", "2020-01-02"))
    r = reports.find_report("gpw-market-regime", tmp_path)
    assert r["stale"] is True and r["sessions_behind"] > 1000
