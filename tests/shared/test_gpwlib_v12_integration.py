"""gpwlib 1.2 integration fixes: archive gaps, gap-aware adjustment, the
memory-efficient panel API, provenance data_gaps and sessions_behind for an
explicit reference date."""

import datetime as dt
import json
import sys
from array import array
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

from gpwlib import gpw_archive, panel, plcal, provenance  # noqa: E402


def _row(name, close, chg, open_=None, turn=100.0):
    return {"name": name, "currency": "PLN", "open": open_ or close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "change_pct": chg, "turnover_k": turn}


def _write_cache(cache: Path, day: str, rows, session=True, type_id=gpw_archive.TYPE_SHARES):
    p = cache / "archive" / gpw_archive.TYPE_NAMES[type_id] / "{}.json".format(day)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema": gpw_archive.CACHE_SCHEMA, "date": day, "session": session,
                             "rows": rows if session else []}), encoding="utf-8")


# 2022-01-03 (Mon) .. 2022-01-07 (Fri); 2022-01-06 is a holiday (Trzech Króli)
DAYS = ["2021-12-30", "2022-01-03", "2022-01-04", "2022-01-05", "2022-01-07"]


@pytest.fixture
def gap_cache(tmp_path):
    """Cache with 2022-01-03 missing; stock A rose 10% on the missing day, so
    on 2022-01-04 its change% (+1%) refers to a close we do not hold."""
    closes = {"2021-12-30": 100.0, "2022-01-04": 111.1, "2022-01-05": 112.211, "2022-01-07": 113.33311}
    chg = {"2021-12-30": 0.0, "2022-01-04": 1.0, "2022-01-05": 1.0, "2022-01-07": 1.0}
    for d in DAYS:
        if d == "2022-01-03":
            continue
        _write_cache(tmp_path, d, [_row("A", closes[d], chg[d])])
    return tmp_path


def test_load_sessions_marks_missing_session(gap_cache):
    sess = gpw_archive.load_sessions(10, end=dt.date(2022, 1, 7), cache_dir=gap_cache, offline=True, progress=False)
    assert [s["date"] for s in sess] == ["2021-12-30", "2022-01-04", "2022-01-05", "2022-01-07"]
    by = {s["date"]: s for s in sess}
    # 2021-12-31 is an exchange holiday, 2022-01-03 is missing from the cache
    assert by["2022-01-04"][gpw_archive.MISSING_BEFORE] == ["2022-01-03"]
    assert gpw_archive.MISSING_BEFORE not in by["2022-01-07"]  # 01-06 is a holiday, not a gap
    assert gpw_archive.session_gaps(sess) == ["2022-01-03"]


def test_cached_no_session_day_is_not_a_gap(gap_cache):
    _write_cache(gap_cache, "2022-01-03", [], session=False)  # e.g. an unplanned closure cached as such
    sess = gpw_archive.load_sessions(10, end=dt.date(2022, 1, 7), cache_dir=gap_cache, offline=True, progress=False)
    assert gpw_archive.session_gaps(sess) == []
    assert gpw_archive.find_gaps([s["date"] for s in sess], cache_dir=gap_cache) == []


def test_trailing_missing_days_are_not_gaps(gap_cache):
    # cache ends 2022-01-07; asking up to 2022-01-12 must not flag 01-10..01-12
    sess = gpw_archive.load_sessions(10, end=dt.date(2022, 1, 12), cache_dir=gap_cache, offline=True, progress=False)
    assert gpw_archive.session_gaps(sess) == ["2022-01-03"]


def test_session_gaps_ignores_days_topped_up_later(gap_cache):
    sess = gpw_archive.load_sessions(10, end=dt.date(2022, 1, 7), cache_dir=gap_cache, offline=True, progress=False)
    sess.append({"date": "2022-01-03", "rows": []})
    assert gpw_archive.session_gaps(sess) == []


def test_find_gaps_for_self_assembled_lists(gap_cache):
    assert gpw_archive.find_gaps(["2021-12-30", "2022-01-04", "2022-01-07"], cache_dir=gap_cache) == ["2022-01-03"]
    assert gpw_archive.find_gaps(["2022-01-04"], cache_dir=gap_cache) == []


def test_iter_sessions_streams_and_reports_missing(gap_cache):
    missing = []
    it = gpw_archive.iter_sessions(dt.date(2021, 12, 30), dt.date(2022, 1, 7), cache_dir=gap_cache,
                                   offline=True, missing=missing)
    assert not isinstance(it, list)
    sess = list(it)
    assert [s["date"] for s in sess] == ["2021-12-30", "2022-01-04", "2022-01-05", "2022-01-07"]
    assert missing == ["2022-01-03"] and sess[1][gpw_archive.MISSING_BEFORE] == ["2022-01-03"]


def test_panel_does_not_infer_adjustment_across_gap(gap_cache):
    sess = gpw_archive.load_sessions(10, end=dt.date(2022, 1, 7), cache_dir=gap_cache, offline=True, progress=False)
    s = panel.build_panel(sess)["A"]
    assert s.adjustments == [] and s.gap_joins == ["2022-01-04"]
    assert s.close[0] == pytest.approx(100.0)  # not rescaled by a fake "dividend"
    # the same sessions without the marker (1.1 behaviour) report a spurious 10% "split"
    bare = [{k: v for k, v in x.items() if k != gpw_archive.MISSING_BEFORE} for x in sess]
    old = panel.build_panel(bare)["A"]
    assert len(old.adjustments) == 1 and old.adjustments[0]["date"] == "2022-01-04"
    # an explicit gaps= list works for self-assembled sessions
    assert panel.build_panel(bare, gaps=["2022-01-03"])["A"].adjustments == []


def test_panel_real_adjustment_after_gap_elsewhere_still_detected():
    # gap on 01-03; a 5% dividend on 01-05 (consecutive sessions) is still found
    sess = [
        {"date": "2021-12-30", "rows": [_row("B", 50.0, 0.0)]},
        {"date": "2022-01-04", "rows": [_row("B", 51.0, 0.5)], "missing_before": ["2022-01-03"]},
        {"date": "2022-01-05", "rows": [_row("B", 48.45, 0.0)]},  # ref = 48.45 = 51 * 0.95
    ]
    s = panel.build_panel(sess)["B"]
    assert [a["date"] for a in s.adjustments] == ["2022-01-05"] and s.gap_joins == ["2022-01-04"]
    assert s.adjustments[0]["factor"] == pytest.approx(0.95)


def _sessions():
    out = []
    price = {"X": 10.0, "Y": 20.0}
    for k, d in enumerate(["2026-01-05", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12"]):
        rows = []
        for n in ("X", "Y"):
            prev = price[n]
            if n == "X" and d == "2026-01-09":
                price[n] = prev * 0.5 * 1.02  # 2:1 split, +2%
                rows.append(_row(n, price[n], 2.0, open_=price[n] * 0.98))
            else:
                price[n] = prev * 1.01
                rows.append(_row(n, price[n], 1.0 if k else 0.0, open_=price[n] * 1.2))  # open above high
        out.append({"date": d, "rows": rows})
    return out


def test_build_panel_accepts_a_generator_and_options_keep_results():
    full = panel.build_panel(_sessions())
    streamed = panel.build_panel(iter(_sessions()), names=["X"], fields=("open", "high", "low", "turnover_k"),
                                 compact=True)
    assert set(streamed) == {"X"}
    a, b = full["X"], streamed["X"]
    assert isinstance(b.close, array) and b.volume == []
    for f in ("dates", "close", "high", "low", "open", "turnover_k"):
        assert list(getattr(a, f)) == list(getattr(b, f))
    assert a.adjustments == b.adjustments and a.adjustments[0]["date"] == "2026-01-09"
    assert all(o <= h for o, h in zip(b.open, b.high))  # open clamped in compact mode too


def test_build_panel_close_only_and_bad_field():
    p = panel.build_panel(_sessions(), fields=())
    x = p["X"]
    assert x.high == [] and x.open == [] and x.turnover_k == [] and len(x.close) == 5
    assert x.adjustments == panel.build_panel(_sessions())["X"].adjustments
    with pytest.raises(ValueError):
        panel.build_panel(_sessions(), fields=("vwap",))


def test_adjust_series_old_two_argument_call_still_works():
    s = panel.Series("Z")
    s.dates, s.close, s.high, s.low = ["d1", "d2"], [10.0, 5.0], [10.0, 5.0], [10.0, 5.0]
    panel._adjust_series(s, [None, 0.0])
    assert s.close[0] == pytest.approx(5.0) and s.adjustments[0]["factor"] == pytest.approx(0.5)


# --- provenance ---------------------------------------------------------------------------

def test_provenance_data_gaps_note():
    d = dt.date(2025, 6, 13)
    p = provenance.build(as_of=d, sources=["x"], sessions_used=10, reference_date=d,
                         data_gaps=["2025-06-02", "2025-06-02", "2025-05-30"])
    assert p["data_gaps"] == ["2025-05-30", "2025-06-02"]
    assert any("2 probable session(s) missing" in n for n in p["notes"])
    p2 = provenance.build(as_of=d, sources=["x"], sessions_used=10)
    assert "data_gaps" not in p2
    assert provenance.data_gaps_note([]) is None


def test_sessions_behind_counts_explicit_future_reference_day(monkeypatch):
    # "today" = Monday 2026-11-16 10:00 (session not final)
    now = dt.datetime(2026, 11, 16, 10, 0, tzinfo=plcal.WARSAW)
    monkeypatch.setattr(plcal, "now_warsaw", lambda: now)
    fri = dt.date(2026, 11, 13)
    assert provenance.sessions_behind(fri) == 0                          # today not final: not counted
    assert provenance.sessions_behind(fri, dt.date(2026, 11, 16)) == 0   # explicit today: same rule
    # explicit future reference (Friday 20th): Mon..Fri all count, incl. the reference day
    assert provenance.sessions_behind(fri, dt.date(2026, 11, 20)) == 5
    # explicit past reference: unchanged (the reference day counts; 11 Nov is a holiday)
    assert provenance.sessions_behind(dt.date(2026, 11, 10), fri) == 2
    # a future reference that is not a probable session (Sunday) adds nothing
    assert provenance.sessions_behind(fri, dt.date(2026, 11, 22)) == 5
    # after 18:00 today's session is final
    monkeypatch.setattr(plcal, "now_warsaw", lambda: dt.datetime(2026, 11, 16, 18, 30, tzinfo=plcal.WARSAW))
    assert provenance.sessions_behind(fri) == 1


# --- index_series: case variants of index names -------------------------------------------

def test_index_series_stitches_case_variants():
    def sess(d, *rows):
        return {"date": d, "rows": [{"name": n, "close": c} for n, c in rows]}
    sessions = [sess("2022-03-17", ("WIG-gry", 100.0)), sess("2022-03-18", ("WIG-gry", 101.0)),
                sess("2022-03-21", ("WIG-GRY", 102.0)), sess("2022-03-22", ("WIG-GRY", 103.0), ("WIG-gry", 999.0))]
    s = panel.index_series(sessions, "WIG-GRY")
    assert s.dates == ["2022-03-17", "2022-03-18", "2022-03-21", "2022-03-22"]
    assert s.close == [100.0, 101.0, 102.0, 103.0]  # exact spelling wins on a day with both
    assert s.renamed_from == [{"name": "WIG-gry", "first_date": "2022-03-17", "last_date": "2022-03-18"}]
    assert panel.index_series(sessions, "wig-gry").close[:2] == [100.0, 101.0]
    old = panel.index_series(sessions, "WIG-GRY", case_sensitive=True)
    assert old.dates == ["2022-03-21", "2022-03-22"] and old.renamed_from == []
    assert len(panel.index_series(sessions, "WIG")) == 0


# --- tax: derivatives and IKE/IKZE facts ----------------------------------------------------

def test_tax_derivatives_and_ike_facts():
    from gpwlib import tax
    assert tax.DERIVATIVES_RATE == tax.BELKA_RATE == 0.19 and tax.DERIVATIVES_PIT_FORM == "PIT-38"
    assert "art. 30b ust. 1" in tax.DERIVATIVES_LEGAL_BASIS and tax.LOSS_CARRY_FORWARD_YEARS == 5
    r = tax.IKE_IKZE_NON_SECURITIES_RULE
    assert "art. 27 ust. 2" in r["legal_basis"] and r["allowed"] == "only_to_limit_risk"
    assert r["requires_broker_agreement"] is True
    for k in ("derivatives_pit", "ike_derivatives"):
        assert set(tax.NOTES[k]) == {"pl", "en"}
    assert "PIT-38" in tax.NOTES["derivatives_pit"]["pl"] and "art. 27" in tax.NOTES["ike_derivatives"]["en"]


# --- reports: RRG vs classic sector-trend report ----------------------------------------------

def _rep(folder, name, data):
    (folder / name).write_text(json.dumps(data), encoding="utf-8")


def test_reports_never_mistake_rrg_for_classic_sector_trend(tmp_path):
    from gpwlib import reports
    _rep(tmp_path, "gpw_sector_trend_2026-11-12.json", {"skill": "gpw-sector-trend", "as_of": "2026-11-12",
                                                        "composite": {"score": 55}})
    # newer RRG report of the same skill
    _rep(tmp_path, "gpw_sector_rrg_2026-11-13.json", {"skill": "gpw-sector-trend", "mode": "rrg", "as_of": "2026-11-13",
                                                      "rrg": {"items": []}})
    ref = dt.date(2026, 11, 13)
    classic = reports.find_report("gpw-sector-trend", tmp_path, reference=ref)
    assert classic["file"] == "gpw_sector_trend_2026-11-12.json"
    rrg = reports.find_report("gpw-sector-trend", tmp_path, reference=ref, mode="rrg")
    assert rrg["file"] == "gpw_sector_rrg_2026-11-13.json"
    assert [r["file"] for r in reports.iter_reports(tmp_path, "gpw-sector-trend")] == ["gpw_sector_trend_2026-11-12.json"]
    assert len(reports.iter_reports(tmp_path, "gpw-sector-trend", exclude_modes=())) == 2
    allr = reports.iter_reports(tmp_path)
    assert {(r["file"], r["variant"]) for r in allr} == {("gpw_sector_trend_2026-11-12.json", False),
                                                         ("gpw_sector_rrg_2026-11-13.json", True)}
    assert reports.find_reports(["gpw-sector-trend"], tmp_path, reference=ref)["gpw-sector-trend"]["data"]["composite"]
    with pytest.raises(reports.ReportError):
        reports.find_report("gpw-sector-trend", explicit_path=tmp_path / "gpw_sector_rrg_2026-11-13.json")
    # other skills' modes are run modes, not variants: unaffected
    _rep(tmp_path, "own.json", {"skill": "gpw-ownership", "mode": "shorts", "as_of": "2026-11-13"})
    assert reports.find_report("gpw-ownership", tmp_path, reference=ref)["file"] == "own.json"
