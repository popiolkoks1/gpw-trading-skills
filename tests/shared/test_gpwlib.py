import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

from gpwlib import fmt, gpw_archive, indicators, panel, plcal, provenance  # noqa: E402

SAMPLE_HTML = """
<html><body>
<table class="table"><tr><th></th><th>CALL</th></tr><tr><td>x</td><td>y</td></tr></table>
<table class="table footable" data-sorting="true">
<thead><tr><th class="left">Nazwa</th><th>Waluta</th><th>Kurs otwarcia</th><th>Kurs maksymalny</th>
<th>Kurs minimalny</th><th>Kurs zamknięcia</th><th>Zmiana kursu %</th><th>Wartość obrotu (w tys.)</th></tr></thead>
<tr><td class="left">PKOBP</td><td>PLN</td><td>117,6000</td><td>119,9000</td><td>117,1600</td><td>119,9000</td>
<td>1,70</td><td>425&nbsp;164,47</td></tr>
<tr><td class="left">WIG20</td><td></td><td>4&nbsp;284,12</td><td>4&nbsp;320,05</td><td>4&nbsp;263,68</td>
<td>4&nbsp;298,46</td><td>0,44</td><td>2&nbsp;220&nbsp;530,06</td></tr>
<tr><td class="left">EURCO</td><td>EUR</td><td>1,0</td><td>1,0</td><td>1,0</td><td>1,0</td><td>-0,26</td><td>0,84</td></tr>
</table></body></html>
"""


def test_parse_pl_number():
    assert gpw_archive.parse_pl_number("425\xa0164,47") == pytest.approx(425164.47)
    assert gpw_archive.parse_pl_number("-0,26") == pytest.approx(-0.26)
    assert gpw_archive.parse_pl_number("-") is None
    assert gpw_archive.parse_pl_number("") is None


def test_parse_archive_html_picks_quotes_table():
    rows = gpw_archive.parse_archive_html(SAMPLE_HTML)
    assert [r["name"] for r in rows] == ["PKOBP", "WIG20", "EURCO"]
    pko = rows[0]
    assert pko["close"] == pytest.approx(119.9)
    assert pko["turnover_k"] == pytest.approx(425164.47)
    assert rows[1]["currency"] == ""
    assert rows[1]["close"] == pytest.approx(4298.46)


def test_parse_archive_html_no_session():
    assert gpw_archive.parse_archive_html("<html><body>Brak danych</body></html>") is None


def test_unexpected_layout_raises():
    html = '<table class="footable"><tr><th>Foo</th><th>Bar</th></tr><tr><td>1</td><td>2</td></tr></table>'
    with pytest.raises(gpw_archive.GPWFetchError):
        gpw_archive.parse_archive_html(html)


def test_easter_and_holidays():
    assert plcal.easter_sunday(2026) == dt.date(2026, 4, 5)
    assert plcal.easter_sunday(2027) == dt.date(2027, 3, 28)
    h = plcal.exchange_holidays(2026)
    assert dt.date(2026, 4, 3) in h  # Good Friday
    assert dt.date(2026, 6, 4) in h  # Corpus Christi
    assert dt.date(2026, 11, 11) in h
    assert not plcal.is_probable_session(dt.date(2026, 11, 15))  # Sunday
    assert plcal.is_probable_session(dt.date(2026, 11, 13))


def test_fetch_session_uses_cache_offline(tmp_path):
    day = dt.date(2026, 11, 13)
    assert gpw_archive.fetch_session(day, cache_dir=tmp_path, offline=True) is None
    path = tmp_path / "archive" / "shares" / "2026-11-13.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"schema": 1, "date": "2026-11-13", "session": true, "rows": [{"name": "X", "close": 1}]}')
    got = gpw_archive.fetch_session(day, cache_dir=tmp_path, offline=True)
    assert got["rows"][0]["name"] == "X"


def test_sma_ema_atr():
    assert indicators.sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    e = indicators.ema([1.0, 1.0, 1.0], 8)
    assert e == [1.0, 1.0, 1.0]
    e2 = indicators.ema([None, 2.0, 4.0], 1)
    assert e2 == [None, 2.0, 4.0]
    highs = [10 + i for i in range(30)]
    lows = [9 + i for i in range(30)]
    closes = [9.5 + i for i in range(30)]
    assert indicators.atr(highs, lows, closes, 14) == pytest.approx(1.5, rel=1e-6)


def test_piecewise_and_percentile():
    pts = [(0, 0), (10, 100)]
    assert indicators.piecewise(-5, pts) == 0
    assert indicators.piecewise(5, pts) == 50
    assert indicators.piecewise(50, pts) == 100
    assert indicators.percentile_rank([1, 2, 3, 4], 3) == pytest.approx(62.5)


def _sess(date, rows):
    return {"date": date, "rows": rows}


def test_split_adjustment_detected():
    # 10:1 split on day 3: raw close 100 -> 10.2, GPW change +2% vs adjusted reference
    s = [
        _sess("2026-01-02", [{"name": "A", "currency": "PLN", "close": 98.0, "change_pct": 0.0}]),
        _sess("2026-01-05", [{"name": "A", "currency": "PLN", "close": 100.0, "change_pct": 2.04}]),
        _sess("2026-01-06", [{"name": "A", "currency": "PLN", "close": 10.2, "change_pct": 2.0}]),
    ]
    p = panel.build_panel(s)
    a = p["A"]
    assert a.close[-1] == pytest.approx(10.2)
    assert a.close[1] == pytest.approx(10.0, rel=1e-3)
    assert a.close[0] == pytest.approx(9.8, rel=1e-3)
    assert len(a.adjustments) == 1


def test_no_adjustment_on_normal_moves():
    s = [
        _sess("2026-01-02", [{"name": "A", "currency": "PLN", "close": 100.0, "change_pct": 0.0}]),
        _sess("2026-01-05", [{"name": "A", "currency": "PLN", "close": 91.0, "change_pct": -9.0}]),
    ]
    p = panel.build_panel(s)
    assert p["A"].adjustments == []
    assert p["A"].close == [100.0, 91.0]


def test_currency_filter():
    s = [_sess("2026-01-02", [{"name": "E", "currency": "EUR", "close": 1.0}, {"name": "P", "currency": "PLN", "close": 2.0}])]
    assert list(panel.build_panel(s)) == ["P"]


def test_fmt_polish_and_english():
    assert fmt.num(1234.5, 2, "pl") == "1 234,50"
    assert fmt.num(1234.5, 2, "en") == "1,234.50"
    assert fmt.pln(5, 2, "pl").endswith("zł")
    assert fmt.pct(3.14159, 1, "en", signed=True) == "+3.1%"
    t = fmt.T({"hi": {"pl": "Cześć {x}", "en": "Hi {x}"}}, "en")
    assert t("hi", x="Ann") == "Hi Ann"
    assert t("missing") == "missing"


def test_provenance_freshness():
    old = dt.date(2020, 1, 2)
    f = provenance.freshness(old)
    assert f["is_stale"] is True and f["sessions_behind"] > 100
