"""Tests of the RRG mode of gpw-sector-trend (sector_rrg_engine + sector_rrg CLI). No network."""

import datetime as dt
import json
import math
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "gpw-sector-trend" / "scripts"))

import gpw_sector_trend as cli  # noqa: E402
import sector_rrg  # noqa: E402
import sector_rrg_engine as rr  # noqa: E402

_TICKER_RE = re.compile(r"^[A-Za-z0-9.-]{1,20}$")
_KIND_RE = re.compile(r"^[a-z0-9_]{1,40}$")


def weekly_dates(n, start=dt.date(2024, 1, 5)):
    return [(start + dt.timedelta(weeks=i)).isoformat() for i in range(n)]


def hump_rs(n=135, rise_start=90, rise_len=25, amp=12.0):
    """Flat (small noise) -> outperformance that decelerates to a peak -> plateau."""
    out = []
    for i in range(n):
        base = 100 + 0.3 * math.sin(i * 1.7)
        if i < rise_start:
            out.append(base)
        elif i <= rise_start + rise_len:
            out.append(base + amp * math.sin(math.pi / 2 * (i - rise_start) / rise_len))
        else:
            out.append(base + amp)
    return out


# ---------------------------------------------------------------- weekly resampling

def test_weekly_last_sessions_with_holidays():
    # Week of Good Friday 2025-04-18: last session Thursday 17th.
    # Week of 11 November 2025 (Tuesday holiday): last session Friday 14th.
    dates = ["2025-04-14", "2025-04-15", "2025-04-16", "2025-04-17",
             "2025-04-22", "2025-04-23", "2025-04-24", "2025-04-25",
             "2025-11-10", "2025-11-12", "2025-11-13", "2025-11-14",
             "2025-12-22", "2025-12-23", "2025-12-29", "2025-12-30", "2026-01-02"]
    idx = rr.weekly_last_sessions(dates)
    assert [dates[i] for i in idx] == ["2025-04-17", "2025-04-25", "2025-11-14", "2025-12-23", "2026-01-02"]


def test_weekly_last_sessions_iso_week_across_year_end():
    # 2025-12-29..2026-01-02 is ISO week 2026-W01: one weekly point.
    dates = ["2025-12-29", "2025-12-30", "2026-01-02", "2026-01-05"]
    assert [dates[i] for i in rr.weekly_last_sessions(dates)] == ["2026-01-02", "2026-01-05"]
    assert rr.weekly_last_sessions([]) == []


def test_week_is_complete_uses_calendar():
    assert rr.week_is_complete("2026-11-13") is True    # Friday
    assert rr.week_is_complete("2026-11-10") is False   # Tuesday, Thu/Fri sessions still to come
    assert rr.week_is_complete("2025-04-17") is True    # Thursday before Good Friday
    assert rr.week_is_complete("2025-12-23") is True    # 24, 25, 26 Dec closed


def test_weekly_pair_aligns_on_common_dates():
    da = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"]
    db = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-09"]  # benchmark missing Friday
    d, a, b = rr.weekly_pair(da, [1, 2, 3, 4, 5, 6], db, [10, 20, 30, 40, 60])
    assert d == ["2026-11-05", "2026-11-09"]
    assert a == [4, 6] and b == [40, 60]


# ---------------------------------------------------------------- RS-Ratio / RS-Momentum

def test_outperform_then_decelerate_goes_leading_then_weakening():
    rs = hump_rs()
    ratio, mom = rr.rrg_series(rs)
    q = {i: rr.quadrant(ratio[i], mom[i]) for i in range(len(rs)) if mom[i] is not None}
    # while the outperformance is building (rise phase): leading
    assert q[91] == "leading" and q[92] == "leading"
    assert ratio[92] > 100 and mom[92] > 100
    # after the peak (plateau): still above the benchmark trend, but momentum < 100
    for i in range(118, 135):
        assert q[i] == "weakening", i
    assert ratio[-1] > 100 > mom[-1]


def test_rs_ratio_centred_and_unit_scaled():
    rs = [100 + 5 * math.sin(i / 6) for i in range(200)]
    ratio, mom = rr.rrg_series(rs)
    vals = [r for r in ratio if r is not None]
    assert abs(sum(vals) / len(vals) - 100) < 0.5
    assert max(vals) < 104 and min(vals) > 96      # z-score: unit = one standard deviation
    assert all(m is None for m in mom[:rr.min_weeks(tail=1) - 1])
    assert mom[rr.min_weeks(tail=1) - 1] is not None


def test_constant_rs_gives_centre():
    ratio, mom = rr.rrg_series([100.0] * 120)
    assert ratio[-1] == pytest.approx(100.0) and mom[-1] == pytest.approx(100.0)


def test_momentum_scaling_keeps_sign():
    # RS-Momentum is scaled around zero (not de-meaned): a persistently negative
    # rate of change stays below 100 instead of drifting back to the centre.
    vals = [-0.5] * 30
    assert rr._rolling_z(vals, 26, demean=False)[-1] == pytest.approx(-1.0)
    assert rr._rolling_z(vals, 26, demean=True)[-1] == pytest.approx(0.0)
    assert rr._rolling_z([None] + vals, 26, demean=False)[25] is None


def test_rrg_series_rejects_bad_windows():
    with pytest.raises(rr.RRGError):
        rr.rrg_series([100.0] * 50, smooth=0)
    with pytest.raises(rr.RRGError):
        rr.rrg_series([100.0] * 50, norm=1)


def test_min_weeks_and_short_history_error():
    assert rr.min_weeks() == 9 + 51 + 1 + 2 + 25 + 8
    n = rr.min_weeks() - 1
    with pytest.raises(rr.RRGError, match="need"):
        rr.compute_item("X", weekly_dates(n), [100.0] * n, [100.0] * n)
    with pytest.raises(rr.RRGError):
        rr.compute_item("X", weekly_dates(3), [1, 2], [1, 2, 3])
    with pytest.raises(rr.RRGError):
        rr.compute_item("X", weekly_dates(140), [100.0] * 140, [100.0] * 140, tail=1)


# ---------------------------------------------------------------- geometry

@pytest.mark.parametrize("x,y,q", [
    (101, 101, "leading"), (101, 99, "weakening"), (99, 99, "lagging"), (99, 101, "improving"),
    (100, 101, "improving"), (101, 100, "weakening"), (100, 100, "lagging"),
])
def test_quadrant_classification(x, y, q):
    assert rr.quadrant(x, y) == q


def test_heading_and_distance():
    assert rr.heading_deg((100, 100), (101, 101)) == pytest.approx(45)
    assert rr.heading_deg((100, 100), (99, 100)) == pytest.approx(180)
    assert rr.heading_deg((100, 100), (100, 99)) == pytest.approx(270)
    assert rr.heading_deg((100, 100), (101, 99.99)) == pytest.approx(359.43, abs=0.01)
    assert rr.heading_deg((100, 100), (100, 100)) is None
    assert rr.heading_sector(45) == "NE" and rr.heading_sector(135) == "NW"
    assert rr.heading_sector(225) == "SW" and rr.heading_sector(315) == "SE"
    assert rr.heading_sector(None) is None
    assert rr.distance(103, 104) == pytest.approx(5)


def _circle(clockwise, n=9, radius=2.0):
    sign = -1 if clockwise else 1
    return [(100 + radius * math.cos(sign * k * math.pi / 8), 100 + radius * math.sin(sign * k * math.pi / 8))
            for k in range(n)]


def test_rotation_direction():
    cw = rr.rotation(_circle(True))
    assert cw["direction"] == "clockwise" and cw["angle_deg"] == pytest.approx(-180, abs=0.5)
    ccw = rr.rotation(_circle(False))
    assert ccw["direction"] == "counterclockwise" and ccw["angle_deg"] > 0
    # straight radial move: no rotation
    assert rr.rotation([(101, 101), (102, 102), (103, 103)])["direction"] == "none"
    # points jittering around the centre are ignored
    assert rr.rotation([(100.1, 100.0), (100.0, 100.1), (99.9, 100.0), (100.0, 99.9)])["direction"] == "none"


def test_synthetic_cycle_rotates_clockwise():
    n = 140
    p = [100 * (1 + 0.1 * math.sin(2 * math.pi * i / 40)) for i in range(n)]
    it = rr.compute_item("X", weekly_dates(n), p, [100.0] * n)
    assert it["rotation"]["direction"] == "clockwise"
    seq = []
    ratio, mom = rr.rrg_series([100 * x / 100 for x in p])
    for r, m in zip(ratio, mom):
        if m is None:
            continue
        q = rr.quadrant(r, m)
        if not seq or seq[-1] != q:
            seq.append(q)
    # every change follows the clockwise order improving -> leading -> weakening -> lagging
    assert len(seq) >= 4
    for a, b in zip(seq, seq[1:]):
        assert rr.CLOCKWISE_NEXT[a] == b


def test_transitions_and_weeks_in_quadrant():
    tail = [{"date": "2026-10-{:02d}".format(d), "rs_ratio": r, "rs_momentum": m}
            for d, (r, m) in zip(range(2, 31, 4), [(99, 101), (99.5, 101), (100.5, 101), (101, 100.5),
                                                  (101, 99.5), (101.2, 99), (101.5, 100.2), (101, 99.8)])]
    tr = rr.transitions(tail, 4)
    assert [(t["from"], t["to"]) for t in tr] == [("leading", "weakening"), ("weakening", "leading"),
                                                 ("leading", "weakening")]
    assert [t["clockwise"] for t in tr] == [True, False, True]
    assert rr.weeks_in_quadrant([99, 101, 101.2], [101, 99, 99.5]) == 2
    assert rr.weeks_in_quadrant([None, 101], [None, 101]) == 1


def test_concentration_rules():
    lpp = rr.concentration([82.6, 13.0, 1.7, 1.6, 0.5])
    assert lpp["concentrated"] is True and lpp["top1_pct"] > 80 and lpp["effective_n"] < 2
    energy = rr.concentration([44.1, 31.0, 17.9, 7.0])
    assert energy["concentrated"] is True and energy["top1_pct"] < 50   # top-2 rule
    banks = rr.concentration([32.4, 19.9, 14.3, 10, 8, 6, 5, 4.4])
    assert banks["concentrated"] is False and banks["hhi"] < 2500
    assert rr.concentration([], 3)["concentrated"] is True
    assert rr.concentration(None, 10)["concentrated"] is False
    assert rr.concentration(None)["concentrated"] is None


# ---------------------------------------------------------------- summary & signals

def _item(item_id, tail, quadrant=None, heading=None):
    last = tail[-1]
    return {"id": item_id, "label": item_id, "quadrant": quadrant or rr.quadrant(last["rs_ratio"], last["rs_momentum"]),
            "rs_ratio": last["rs_ratio"], "rs_momentum": last["rs_momentum"], "heading_deg": heading,
            "distance": rr.distance(last["rs_ratio"], last["rs_momentum"]), "tail": tail,
            "transitions": rr.transitions(tail), "benchmark": "WIG", "concentrated": False}


def test_signals_contract_and_kinds():
    t1 = [{"date": "2026-10-23", "rs_ratio": 101, "rs_momentum": 100.5},
          {"date": "2026-10-30", "rs_ratio": 101, "rs_momentum": 99.5},
          {"date": "2026-11-06", "rs_ratio": 100.8, "rs_momentum": 99.2}]
    t2 = [{"date": "2026-10-30", "rs_ratio": 99.5, "rs_momentum": 101},
          {"date": "2026-11-06", "rs_ratio": 100.4, "rs_momentum": 101.2}]
    t3 = [{"date": "2026-10-30", "rs_ratio": 99, "rs_momentum": 99},
          {"date": "2026-11-06", "rs_ratio": 99, "rs_momentum": 100.5}]  # lagging -> improving: no signal
    items = [_item("WIG-BANKI", t1), _item("WIG-GRY", t2), _item("WIG-LEKI", t3)]
    sig = rr.signals(items, "2026-11-13")
    assert {(s["ticker"], s["kind"], s["direction"], s["date"]) for s in sig} == {
        ("WIG-BANKI", "rrg_leading_to_weakening", "exit", "2026-10-30"),
        ("WIG-GRY", "rrg_improving_to_leading", "long", "2026-11-06")}
    for s in sig:
        assert _TICKER_RE.match(s["ticker"]) and _KIND_RE.match(s["kind"]) and len(s["date"]) == 10
        assert 0 <= s["score"] <= 100 and isinstance(s["tags"], dict)
    assert rr.signals(items, "2026-11-01") == [rr.signals(items, "2026-11-01")[0]]  # nothing after as_of
    stock = rr.signals(items, "2026-11-13", stock_mode=True)
    assert {s["kind"] for s in stock} == {"rrg_stock_leading_to_weakening", "rrg_stock_improving_to_leading"}


def test_rotation_summary_transitions_and_approaching():
    t1 = [{"date": "2026-10-30", "rs_ratio": 101, "rs_momentum": 100.5},
          {"date": "2026-11-06", "rs_ratio": 101, "rs_momentum": 99.5}]
    t2 = [{"date": "2026-10-30", "rs_ratio": 99.2, "rs_momentum": 100.5},
          {"date": "2026-11-06", "rs_ratio": 99.7, "rs_momentum": 100.8}]
    items = [_item("A", t1, heading=270), _item("B", t2, heading=31)]
    s = rr.rotation_summary(items)
    assert s[0] == {"id": "A", "kind": "transition", "from": "leading", "to": "weakening", "date": "2026-11-06",
                    "clockwise": True}
    assert s[1]["id"] == "B" and s[1]["kind"] == "approaching" and s[1]["to"] == "leading"
    assert rr.quadrant_counts(items) == {"leading": 0, "weakening": 1, "lagging": 0, "improving": 1}


# ---------------------------------------------------------------- rendering

def _render_items():
    pts = [("A", 101.5, 101.2), ("B", 98.5, 101.0), ("C", 98.6, 98.6), ("D", 101.0, 98.5), ("E", 98.6, 98.6)]
    out = []
    for name, x, y in pts:
        tail = [{"date": "2026-11-06", "rs_ratio": x - 0.3, "rs_momentum": y - 0.2},
                {"date": "2026-11-13", "rs_ratio": x, "rs_momentum": y}]
        out.append(_item(name, tail))
    return out


def test_ascii_scatter_layout():
    items = _render_items()
    txt = rr.ascii_scatter(items, {it["id"]: it["id"] for it in items})
    lines = txt.splitlines()
    assert len(lines) == 21 + 3
    body = [ln[9:-1] for ln in lines[1:22]]
    assert all(len(b) == 41 for b in body)
    assert body[10][20] == "+"                      # centre
    assert body[0].startswith("IMPROVING") and body[0].endswith("LEADING")
    assert body[20].startswith("LAGGING") and body[20].endswith("WEAKENING")
    flat = "\n".join(body)
    assert "A" in flat and "B" in flat and "D" in flat
    assert "*" in flat                              # C and E share a cell
    a_row = next(i for i, b in enumerate(body) if "A" in b[1:-1] and "LEADING" not in b)
    assert a_row < 10 and body[a_row].index("A") > 20  # A in the upper-right quadrant
    with pytest.raises(rr.RRGError):
        rr.ascii_scatter(items, {}, width=5, height=5)


def test_svg_chart_is_standalone_and_escaped():
    items = _render_items()
    items[0]["id"] = "R&D<1>"
    svg = rr.svg_chart(items, "Test & title", "sub")
    assert svg.startswith("<?xml") and "<svg xmlns=\"http://www.w3.org/2000/svg\"" in svg
    assert svg.rstrip().endswith("</svg>")
    assert svg.count("<polyline") == len(items)
    assert "R&amp;D&lt;1&gt;" in svg and "Test &amp; title" in svg
    assert "<script" not in svg


# ---------------------------------------------------------------- CLI

@pytest.mark.parametrize("argv", [
    ["--stocks", "WIG-BANKI"],
    ["--svg", "x.svg"],
    ["--benchmark", "WIG20"],
    ["--rrg", "--tail", "1"],
    ["--rrg", "--tail", "40"],
    ["--rrg", "--stocks", "NOSUCHSECTOR"],
    ["--rrg", "--rrg-norm", "2"],
    ["--rrg", "--rrg-mom-smooth", "0"],
])
def test_cli_argument_errors(argv):
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(argv)
    assert exc.value.code == 2


def test_cli_sector_aliases():
    for alias in ("WIG-BANKI", "banki", "Banks", "BANKI"):
        assert cli.parse_args(["--rrg", "--stocks", alias]).stocks == "WIG-BANKI"
    a = cli.parse_args(["--rrg", "--benchmark", "mwig40"])
    assert a.benchmark == "MWIG40" and a.tail == 8


def test_cli_offline_empty_cache_exit_1(tmp_path, capsys):
    rc = cli.main(["--rrg", "--offline", "--cache-dir", str(tmp_path / "empty"), "--output-dir", str(tmp_path),
                   "--as-of", "2026-11-13"])
    assert rc == 1
    assert "ERROR" in capsys.readouterr().err


def _synthetic_index_sessions(n_days=560):
    """Daily index sessions (Mon-Fri) with WIG, the sector indices and the size TR indices."""
    names = list(rr_ids()) + ["WIG"]
    start = dt.date(2024, 10, 1)
    sessions, day, k = [], start, 0
    while len(sessions) < n_days:
        if day.weekday() < 5:
            rows = []
            for j, name in enumerate(names):
                if name == "WIG":
                    close = 100 * (1.0003 ** k)
                else:
                    close = 100 * (1.0003 ** k) * (1 + 0.08 * math.sin(2 * math.pi * k / (150 + 9 * j) + j))
                rows.append({"name": name, "close": close})
            sessions.append({"date": day.isoformat(), "rows": rows})
            k += 1
        day += dt.timedelta(days=1)
    return sessions


def rr_ids():
    return [sector_rrg.TR_SERIES.get(i, i) for i in list(sector_rrg.SECTOR_INDICES) + sector_rrg.SIZE_ITEMS]


def test_cli_end_to_end_synthetic(tmp_path, monkeypatch, capsys):
    sessions = _synthetic_index_sessions()
    monkeypatch.setattr(sector_rrg.gpw_archive, "load_sessions", lambda *a, **k: sessions)
    weights = {"members": [{"name": "BIG", "weight_pct": 80.0}, {"name": "SMALL", "weight_pct": 20.0}]}
    monkeypatch.setattr(sector_rrg.constituents, "fetch_portfolio", lambda *a, **k: weights)
    svg = tmp_path / "rrg.svg"
    rc = cli.main(["--rrg", "--offline", "--output-dir", str(tmp_path), "--svg", str(svg), "--lang", "en",
                   "--as-of", sessions[-1]["date"]])
    assert rc == 0
    js = list(tmp_path.glob("gpw_sector_rrg_*.json"))
    md = list(tmp_path.glob("gpw_sector_rrg_*.md"))
    assert len(js) == 1 and len(md) == 1 and svg.exists()
    assert not list(tmp_path.glob("gpw_sector_trend_*.json"))  # does not shadow the uptrend-ratio report
    r = json.loads(js[0].read_text(encoding="utf-8"))
    assert r["skill"] == "gpw-sector-trend" and r["version"] == "1.1.0" and r["mode"] == "rrg"
    g = r["rrg"]
    assert g["benchmark"] == "WIG" and g["as_of"] == sessions[-1]["date"] and len(g["items"]) == 17
    for it in g["items"]:
        assert set(["id", "label", "quadrant", "rs_ratio", "rs_momentum", "heading_deg", "distance", "tail",
                    "transitions", "concentrated"]) <= set(it)
        assert len(it["tail"]) == 8 and it["concentrated"] is True
        assert it["quadrant"] == rr.quadrant(it["rs_ratio"], it["rs_momentum"])
    assert {it["series"] for it in g["items"] if it["id"] == "WIG20"} == {"WIG20TR"}
    assert sum(g["quadrant_counts"].values()) == 17
    assert isinstance(r["signals"], list) and r["data_provenance"]["as_of"] == g["as_of"]
    text = md[0].read_text(encoding="utf-8")
    assert "## Answer" in text and "```" in text and "Sources" in text and "LEADING" in text
    out = capsys.readouterr().out
    assert "RRG vs WIG" in out and "SVG:" in out


def test_cli_benchmark_missing_exit_1(tmp_path, monkeypatch, capsys):
    sessions = _synthetic_index_sessions(50)
    monkeypatch.setattr(sector_rrg.gpw_archive, "load_sessions", lambda *a, **k: sessions)
    rc = cli.main(["--rrg", "--offline", "--benchmark", "NOPE", "--no-files"])
    assert rc == 1 and "NOPE" in capsys.readouterr().err


def test_cli_too_short_history_exit_1(tmp_path, monkeypatch, capsys):
    sessions = _synthetic_index_sessions(200)
    monkeypatch.setattr(sector_rrg.gpw_archive, "load_sessions", lambda *a, **k: sessions)
    monkeypatch.setattr(sector_rrg.constituents, "fetch_portfolio", lambda *a, **k: {"members": []})
    rc = cli.main(["--rrg", "--offline", "--no-files"])
    assert rc == 1 and "weeks needed" in capsys.readouterr().err
