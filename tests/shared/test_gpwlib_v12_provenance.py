"""gpwlib 1.2 - provenance: --as-of runs are judged against the requested date;
gpw_archive: futures (type 35) and the full instrument-type list."""

import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

from gpwlib import gpw_archive, plcal, provenance  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


# --- provenance -----------------------------------------------------------------

def test_historical_run_is_not_stale_but_records_distance_to_today():
    d = dt.date(2025, 3, 14)  # a Friday session
    fr = provenance.freshness(d, reference=d)
    assert fr["is_stale"] is False and fr["sessions_behind"] == 0
    assert fr["historical_run"] is True and fr["reference_date"] == "2025-03-14"
    assert fr["sessions_behind_today"] > 100


def test_normal_run_on_old_data_is_stale():
    fr = provenance.freshness(dt.date(2025, 3, 14))
    assert fr["is_stale"] is True and fr["historical_run"] is False and fr["reference_date"] is None
    assert fr["sessions_behind"] == fr["sessions_behind_today"]


def test_as_of_on_a_weekend_uses_the_previous_session():
    # requested Sunday 16 Mar 2025, data ends Friday 14 Mar -> 0 sessions behind
    fr = provenance.freshness(dt.date(2025, 3, 14), reference=dt.date(2025, 3, 16))
    assert fr["sessions_behind"] == 0 and not fr["is_stale"]
    # data two sessions older than the requested date is still flagged
    fr = provenance.freshness(dt.date(2025, 3, 12), reference=dt.date(2025, 3, 14))
    assert fr["sessions_behind"] == 2 and fr["is_stale"]
    assert not provenance.freshness(dt.date(2025, 3, 12), 2, reference=dt.date(2025, 3, 14))["is_stale"]


def test_reference_today_or_later_is_a_normal_run():
    today = plcal.today_warsaw()
    fr = provenance.freshness(dt.date(2025, 3, 14), reference=today)
    assert fr["historical_run"] is False and fr["is_stale"]


def test_build_passes_reference_and_notes():
    d = dt.date(2025, 6, 13)
    p = provenance.build(as_of=d, sources=["x"], sessions_used=10, reference_date=d, notes=["n"])
    assert p["freshness"]["is_stale"] is False
    assert p["notes"][0] == "n" and "Historical run (--as-of 2025-06-13)" in p["notes"][1]
    p2 = provenance.build(as_of=d, sources=["x"], sessions_used=10)
    assert p2["freshness"]["is_stale"] is True and p2["notes"] == []


@pytest.mark.parametrize("script", [
    "skills/gpw-market-breadth/scripts/gpw_breadth.py",
    "skills/gpw-sector-trend/scripts/gpw_sector_trend.py",
    "skills/gpw-market-regime/scripts/gpw_market_regime.py",
    "skills/gpw-etf-planner/scripts/gpw_etf_planner.py",
    "skills/gpw-portfolio-check/scripts/portfolio_check.py",
    "skills/gpw-trend-screener/scripts/gpw_trend_screener.py",
    "skills/gpw-pre-trade-gate/scripts/pre_trade_gate.py",
    "skills/gpw-exposure-coach/scripts/gpw_exposure_coach.py",
])
def test_skills_with_as_of_pass_a_reference_date(script):
    src = (ROOT / script).read_text(encoding="utf-8")
    assert "reference_date=" in src or "reference=end" in src


# --- archive types and futures -------------------------------------------------------

def test_type_list_and_stable_cache_slugs():
    assert gpw_archive.TYPE_FUTURES == 35
    assert gpw_archive.TYPE_NAMES[10] == "shares" and gpw_archive.TYPE_NAMES[1] == "indices"
    assert gpw_archive.TYPE_NAMES[241] == "etf" and gpw_archive.TYPE_NAMES[35] == "futures"
    assert set(gpw_archive.TYPE_NAMES) == {1, 10, 13, 17, 35, 37, 48, 53, 54, 66, 161, 241, 301, 537, 560, 561}
    assert set(gpw_archive.TYPE_LABELS_PL) == set(gpw_archive.TYPE_NAMES)
    assert "type=35" in gpw_archive.archive_url(dt.date(2026, 11, 13), gpw_archive.TYPE_FUTURES)


def test_futures_table_with_settlement_column():
    rows = gpw_archive.parse_archive_html((FIX / "gpw_archive_futures_2026-11-13.html").read_text(encoding="utf-8"))
    by = {r["name"]: r for r in rows}
    fw = by["FW20Z2620"]
    assert fw["settlement"] == pytest.approx(4311.0) and fw["close"] == pytest.approx(4311.0)
    assert fw["change_pct"] == pytest.approx(0.28)          # 'Zmiana kursu % **' header still mapped
    assert fw["turnover_k"] == pytest.approx(2368797.14)
    idle = by["F11BH27"]                                      # no trades: OHLC 0, settlement = close
    assert idle["open"] == 0.0 and idle["settlement"] == pytest.approx(121.6)
    assert by["FUSDZ26"]["settlement"] == pytest.approx(3.9941)


def test_shares_fixture_still_parses_without_settlement():
    rows = gpw_archive.parse_archive_html((FIX / "gpw_archive_shares_2026-11-13.html").read_text(encoding="utf-8"))
    assert {r["name"] for r in rows} == {"11BIT", "CDPROJEKT", "KGHM", "PKOBP"}
    assert all("settlement" not in r for r in rows)


def test_header_normalisation():
    assert gpw_archive._norm_header("Kurs rozliczeniowy  *") == "kurs rozliczeniowy"
    assert gpw_archive._norm_header(" Zmiana kursu % ** ") == "zmiana kursu %"


def test_parse_futures_symbol():
    f = gpw_archive.parse_futures_symbol("FW20Z2620")
    assert f["underlying"] == "WIG20" and f["multiplier"] == 20
    assert (f["year"], f["month"]) == (2026, 12) and f["expiry"] == dt.date(2026, 12, 18)
    s = gpw_archive.parse_futures_symbol("FPKOZ26")
    assert s["underlying"] == "PKO" and s["multiplier"] is None
    assert gpw_archive.parse_futures_symbol("F11BH27")["expiry"] == dt.date(2027, 3, 19)
    assert gpw_archive.parse_futures_symbol("FW40Z2610")["underlying"] == "MWIG40"
    assert gpw_archive.parse_futures_symbol("PKOBP") is None
    assert gpw_archive.parse_futures_symbol("FW20A2620") is None


def test_futures_rows_nearest_first():
    rows = gpw_archive.parse_archive_html((FIX / "gpw_archive_futures_2026-11-13.html").read_text(encoding="utf-8"))
    fw = gpw_archive.futures_rows(rows, "WIG20")
    assert [r["name"] for r in fw] == ["FW20Z2620", "FW20H2720"]
    assert fw[0]["multiplier"] == 20 and fw[0]["settlement"] == pytest.approx(4311.0)
