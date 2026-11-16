"""Parsers and data normalisation (fixtures trimmed from real pages, no network)."""

import datetime as dt

import pytest

from conftest import FIXTURES
from gpwlib import http

import valuation_data as vd


class FakeResp:
    def __init__(self, text, from_cache=False):
        self.text, self.from_cache, self.retrieved_at = text, from_cache, "2026-11-16T12:00:00+00:00"


def _raiser(*a, **k):
    raise http.HTTPFetchError("boom", url="x", status=503)


# --------------------------------------------------------------- Damodaran

def test_parse_ctryprem_poland_row():
    row = vd.parse_ctryprem((FIXTURES / "ctryprem_2026-01.html").read_text(encoding="utf-8"))
    assert row["rating"] == "A2"
    assert row["default_spread"] == pytest.approx(0.0072)
    assert row["crp"] == pytest.approx(0.0110)
    assert row["erp"] == pytest.approx(0.0533)
    assert row["mature_erp"] == pytest.approx(0.0423)
    assert row["tax_rate"] == pytest.approx(0.19)
    assert row["updated"] == "2026-01-05"


def test_parse_ctryprem_other_country_and_missing():
    html = (FIXTURES / "ctryprem_2026-01.html").read_text(encoding="utf-8")
    us = vd.parse_ctryprem(html, "United States")
    assert us["mature_erp"] == pytest.approx(0.0423)  # Damodaran: mature ERP identical across rows
    assert vd.parse_ctryprem(html, "Atlantis") is None
    assert vd.parse_ctryprem("<html></html>") is None


def test_parse_ratings_table():
    rows = vd.parse_ratings((FIXTURES / "ratings_2026-01.html").read_text(encoding="utf-8"))
    assert len(rows) == 15
    assert rows[0]["rating"] == "D2/D" and rows[0]["spread"] == pytest.approx(0.19)
    assert rows[-1]["rating"] == "Aaa/AAA" and rows[-1]["spread"] == pytest.approx(0.004)
    assert rows == sorted(rows, key=lambda r: r["lo"])
    # the embedded fallback equals the live table of January 2026
    assert [(r["rating"], round(r["spread"], 6)) for r in rows] == \
        [(r["rating"], round(r["spread"], 6)) for r in vd.EMBEDDED_RATINGS["rows"]]


def test_damodaran_live_then_embedded_fallback_and_age():
    html = (FIXTURES / "ctryprem_2026-01.html").read_text(encoding="utf-8")
    live = vd.damodaran_poland(dt.date(2026, 11, 16), getter=lambda *a, **k: FakeResp(html))
    assert live["source"] == "live" and not live["stale"] and live["age_days"] == 315
    emb = vd.damodaran_poland(dt.date(2026, 11, 16), getter=_raiser)
    assert emb["source"] == "embedded" and emb["crp"] == pytest.approx(0.011) and "boom" in emb["fetch_error"]
    old = vd.damodaran_poland(dt.date(2027, 3, 1), getter=_raiser)
    assert old["stale"]  # > 12 months after the January 2026 update


def test_damodaran_ratings_fallback():
    assert vd.damodaran_ratings(getter=_raiser)["source"] == "embedded"
    html = (FIXTURES / "ratings_2026-01.html").read_text(encoding="utf-8")
    assert vd.damodaran_ratings(getter=lambda *a, **k: FakeResp(html, True))["source"] == "live_cache"


# --------------------------------------------------------------- PL 10Y

def test_bondspot_parse_and_pick_10y():
    p = vd.parse_bondspot_fixing((FIXTURES / "bondspot_2026-11-16.html").read_text(encoding="utf-8"))
    assert p["date"] == dt.date(2026, 11, 16)
    b = vd.pick_10y(p["bonds"], p["date"])
    assert b["name"] == "DS0436" and b["yield"] == pytest.approx(6.28) and b["tenor_years"] == pytest.approx(9.44)
    assert vd.pick_10y([b for b in p["bonds"] if b["name"] == "DS0727"], p["date"]) is None  # too short
    assert vd.bond_maturity("DS0436") == dt.date(2036, 4, 25)
    assert vd.bond_maturity("XX1399") is None


def test_rf_live_bondspot_then_ecb():
    bs = (FIXTURES / "bondspot_2026-11-16.html").read_text(encoding="utf-8")
    calls = []

    def getter(url, params, **kw):
        calls.append(url)
        return FakeResp(bs)

    r = vd.rf_live(dt.date(2026, 11, 16), dt.date(2026, 11, 16), getter=getter)
    assert r["yield"] == pytest.approx(0.0628) and r["source"] == "BondSpot fixing"

    ecb = "KEY,FREQ,TIME_PERIOD,OBS_VALUE\nIRS.M.PL,M,2026-09,5.9\nIRS.M.PL,M,2026-10,6.1\n"

    def getter2(url, params, **kw):
        if "bondspot" in url:
            return FakeResp("<html>no fixing</html>")
        return FakeResp(ecb)

    r2 = vd.rf_live(dt.date(2026, 11, 16), dt.date(2026, 11, 16), getter=getter2)
    assert r2["yield"] == pytest.approx(0.061) and r2["as_of"] == "2026-10"

    with pytest.raises(vd.DataError):
        vd.rf_live(dt.date(2026, 11, 16), dt.date(2026, 11, 16), getter=_raiser)


def test_rf_from_macro_report():
    rep = {"file": "m.json", "data": {"indicators": {"pl10y": {"value": 6.28, "as_of": "2026-11-16", "source": "bondspot",
                                                               "bond": "DS0436", "tenor_years": 9.44}}}}
    r = vd.rf_from_macro(rep)
    assert r["yield"] == pytest.approx(0.0628) and r["as_of"] == "2026-11-16" and "bondspot" in r["source"]
    assert vd.rf_from_macro(None) is None
    assert vd.rf_from_macro({"data": {"indicators": {}}}) is None


def test_parse_ecb_csv_rejects_garbage():
    assert vd.parse_ecb_csv("not,a,csv") == []


# --------------------------------------------------------------- fundamentals

NOTO_Q3 = {"period_label": "I-III kw. 2026",
           "period": {"year": 2026, "first_quarter": 1, "last_quarter": 3, "months": 9, "cumulative": True,
                      "period_end": "2026-09-30"},
           "values": {"revenue": 900.0, "operating_profit": 90.0, "pretax_profit": 80.0, "equity_parent": 500.0,
                      "capex": -30.0, "cfo": 60.0}}


def test_annual_flows_annualises_ytd():
    fl = vd.annual_flows(NOTO_Q3, None)
    assert fl["values"]["revenue"] == pytest.approx(1200.0)
    assert fl["values"]["capex"] == pytest.approx(-40.0)
    assert fl["values"]["equity_parent"] == 500.0          # balance sheet unchanged
    assert fl["method"]["revenue"] == "annualised_ytd"
    assert fl["method"]["equity_parent"] == "balance_sheet"
    assert fl["yoy"] == {}


def test_annual_flows_ttm_from_history():
    hist = {"periods": {
        "I-III kw. 2025": {"period": {"year": 2025, "first_quarter": 1, "last_quarter": 3, "months": 9},
                           "values": {"revenue": 800.0, "operating_profit": 70.0}},
        "2025": {"period": {"year": 2025, "first_quarter": 1, "last_quarter": 4, "months": 12},
                 "values": {"revenue": 1100.0, "operating_profit": 95.0}},
    }}
    fl = vd.annual_flows(NOTO_Q3, hist)
    assert fl["values"]["revenue"] == pytest.approx(900 + 1100 - 800)
    assert fl["values"]["operating_profit"] == pytest.approx(90 + 95 - 70)
    assert fl["method"]["revenue"] == "ttm_history"
    assert fl["method"]["pretax_profit"] == "annualised_ytd"  # not in history -> annualised
    assert fl["yoy"]["revenue"] == pytest.approx(900 / 800 - 1)
    assert fl["fy_label"] == "2025" and fl["prev_ytd_label"] == "I-III kw. 2025"


def test_annual_flows_full_year():
    noto = {"period_label": "2025", "values": {"revenue": 1000.0}}
    fl = vd.annual_flows(noto, None)
    assert fl["values"]["revenue"] == 1000.0 and fl["method"]["revenue"] == "full_year"


def test_net_debt_proxy_and_coverage():
    v = {"operating_profit": 100.0, "pretax_profit": 93.0, "noncurrent_liabilities": 50.0, "current_liabilities": 60.0,
         "current_assets": 40.0}
    p = vd.net_debt_proxy(v, 0.07, 0.06)
    assert p["net_financial_cost"] == pytest.approx(7.0)
    assert p["value"] == pytest.approx(100.0)                      # 7 / 0.07, under the 110 liabilities cap
    assert vd.interest_coverage(v) == pytest.approx(100 / 7)
    capped = vd.net_debt_proxy(dict(v, pretax_profit=80.0), 0.07, 0.06)
    assert capped["value"] == pytest.approx(110.0)
    cash = vd.net_debt_proxy(dict(v, pretax_profit=101.2), 0.07, 0.06)
    assert cash["value"] == pytest.approx(-20.0)                   # -1.2 / 0.06
    big_cash = vd.net_debt_proxy(dict(v, pretax_profit=110.0), 0.07, 0.06)
    assert big_cash["value"] == pytest.approx(-40.0)               # capped by current assets
    assert vd.interest_coverage(dict(v, pretax_profit=105.0)) == float("inf")
    assert vd.net_debt_proxy({"operating_profit": 1.0}, 0.07, 0.06) is None


def test_is_financial():
    assert vd.is_financial("banki komercyjne", None)
    assert vd.is_financial(None, "WIG-BANKI")
    assert vd.is_financial("ubezpieczenia", None)
    assert not vd.is_financial("hutnictwo metali nieżelaznych", None)


def test_beta_from_series_weekly():
    import datetime as d
    start = d.date(2024, 11, 4)
    dates, mk, st = [], [], []
    m, s = 100.0, 50.0
    for i in range(500):
        day = start + d.timedelta(days=i)
        if day.weekday() >= 5:
            continue
        r = 0.004 * ((i * 7919) % 11 - 5) / 5   # deterministic pseudo-returns
        m *= 1 + r
        s *= 1 + 1.2 * r
        dates.append(day.isoformat()); mk.append(m); st.append(s)
    reg = vd.beta_from_series(dates, st, dates, mk)
    assert reg["weeks"] >= 60
    assert reg["beta"] == pytest.approx(1.2, rel=0.02)
    assert reg["value"] == pytest.approx(2 / 3 * reg["beta"] + 1 / 3)


def test_peer_metrics_foreign_and_bounds():
    snap = {"ticker": "AAA", "isin": "PLAAA0000000",
            "indicators": {"shares_issued": 100, "market_cap_pln": 1000.0, "pe": 150.0, "pbv": 2.0},
            "notoria": {"period_label": "2025", "values": {"revenue": 500.0, "ebitda": 100.0, "operating_profit": 50.0,
                                                           "pretax_profit": 50.0}}}
    m = vd.peer_metrics(snap, 10.0, 0.07, 0.06)
    assert m["pe"] is None                          # 150 > 100 bound
    assert m["pbv"] == 2.0
    assert m["ev_ebitda"] == pytest.approx(10.0)    # EV 1000 (net debt 0) / 100
    assert m["ev_sales"] == pytest.approx(2.0)
    foreign = vd.peer_metrics(dict(snap, isin="HU0000153937"), 10.0, 0.07, 0.06)
    assert foreign["foreign_issuer"] and foreign["ev_ebitda"] is None and foreign["pbv"] == 2.0
