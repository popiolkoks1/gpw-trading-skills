"""gpwlib 1.2 - fundamentals: card parsers on real fixtures, ISIN mapping, snapshot,
history store with restatement detection, xBRL-JSON and a synthetic ESEF zip."""

import copy
import datetime as dt
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

from gpwlib import fundamentals as fu, http  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def _read(name):
    return (FIX / name).read_text(encoding="utf-8")


# --- indicators / Notoria ---------------------------------------------------------

def test_indicators_real_pkobp():
    d = fu.parse_indicators_html(_read("card_indicators_PKOBP.html"))
    assert d["isin"] == "PLPKO0000016" and d["sector"] == "banki komercyjne"
    assert d["market_segment"] == "Podstawowy"
    assert d["shares_issued"] == 1_250_000_000
    assert d["market_cap_pln"] == pytest.approx(149_875e6)     # mln zł -> PLN
    assert d["book_value_pln"] == pytest.approx(57_149e6)
    assert (d["pbv"], d["pe"], d["dividend_yield_pct"]) == (pytest.approx(2.62), pytest.approx(13.7), pytest.approx(5.1))


def test_indicators_missing_values_and_no_table():
    html = "<table><tr><th>C/Z</th><td>-</td></tr><tr><th>Nowe pole</th><td>7</td></tr></table>"
    d = fu.parse_indicators_html(html)
    assert d["pe"] is None and d["other"] == {"Nowe pole": "7"}
    with pytest.raises(fu.FundamentalsError):
        fu.parse_indicators_html("<div>nic</div>")


def test_notoria_real_kety_industrial():
    n = fu.parse_notoria_html(_read("card_notoria_KETY.html"))
    assert n["period_label"] == "I-III kw. 2026" and n["consolidated"] is True
    assert n["unit_source"] == "thousand PLN" and n["multiplier"] == 1000
    v = n["values"]
    assert v["revenue"] == pytest.approx(4_803e6)          # 4 803 000,00 tys. PLN
    assert v["operating_profit"] == pytest.approx(785e6)
    assert v["net_profit_parent"] == pytest.approx(594e6)
    assert v["ebitda"] == pytest.approx(968e6)
    assert v["equity_parent"] == pytest.approx(2_140e6)
    assert v["capex"] == pytest.approx(-175e6) and v["cfi"] == pytest.approx(-178e6)
    assert v["current_liabilities"] == pytest.approx(1_794e6)
    assert n["unmapped"] == []
    assert n["period"] == {"year": 2026, "first_quarter": 1, "last_quarter": 3, "months": 9,
                           "cumulative": True, "period_end": "2026-09-30"}


def test_notoria_real_pkobp_bank():
    n = fu.parse_notoria_html(_read("card_notoria_PKOBP.html"))
    assert n["values"]["total_assets"] == pytest.approx(619_866e9 / 1000)
    assert n["values"]["net_profit_parent"] == pytest.approx(8_223e9 / 1000)
    assert n["values"]["cfo"] == pytest.approx(-5_084e6)
    assert "noncurrent_assets" not in n["values"]          # banks: no current/non-current split


def test_notoria_units_and_basis_variants():
    html = ("<h3>2025</h3><table><tr><th>Przychody ze sprzedaży</th><td>1,5</td></tr>"
            "<tr><th>Coś nowego</th><td>2</td></tr></table><div>Dane finansowe w mln PLN<br>Dane jednostkowe</div>")
    n = fu.parse_notoria_html(html)
    assert n["values"]["revenue"] == pytest.approx(1.5e6) and n["consolidated"] is False
    assert n["unmapped"] == ["Coś nowego"] and n["raw"]["Coś nowego"] == pytest.approx(2e6)
    assert n["period"]["months"] == 12
    assert fu.parse_notoria_html("<div>brak danych</div>") is None


@pytest.mark.parametrize("label,months,cum,end", [
    ("I kw. 2026", 3, False, "2026-03-31"),
    ("I-II kw. 2026", 6, True, "2026-06-30"),
    ("I-IV kw. 2025", 12, True, "2025-12-31"),
    ("IV kw. 2025", 3, False, "2025-12-31"),
    ("III kw. 2026", 3, False, "2026-09-30"),
])
def test_period_labels(label, months, cum, end):
    p = fu.parse_period_label(label)
    assert (p["months"], p["cumulative"], p["period_end"]) == (months, cum, end)


def test_period_label_unknown():
    assert fu.parse_period_label("2026/2027") == {} and fu.parse_period_label("III-I kw. 2026") == {}


# --- ISIN ----------------------------------------------------------------------------

def test_isin_check_digit():
    for good in ("PLPKO0000016", "PLKETY000011", "PLOPTTC00011", "LU2237380790", "PL9999999995"):
        assert fu.isin_is_valid(good), good
    for bad in ("PLPKO0000017", "PKO", "plpko000001", ""):
        assert not fu.isin_is_valid(bad)


def _portfolio(cache, isin, members):
    p = cache / "constituents" / "{}.json".format(isin)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"isin": isin, "members": members, "retrieved_on": "2026-11-16",
                             "source_url": "x"}), encoding="utf-8")


@pytest.fixture
def cache(tmp_path):
    _portfolio(tmp_path, "PL9999999987", [{"name": "PKOBP", "isin": "PLPKO0000016", "weight_pct": 14.6}])
    _portfolio(tmp_path, fu.WIG_ISIN, [{"name": "KETY", "isin": "PLKETY000011", "weight_pct": 1.0},
                                       {"name": "PKOBP", "isin": "PLPKO0000016", "weight_pct": 9.0}])
    _portfolio(tmp_path, fu.NCINDEX_ISIN, [{"name": "NCSPOLKA", "isin": "PLNCSPL00012", "weight_pct": 1.0}])
    return tmp_path


def test_resolve_isin_sources(cache):
    assert fu.resolve_isin("pkobp", cache, offline=True) == {"ticker": "PKOBP", "isin": "PLPKO0000016",
                                                            "source": "gpwbenchmark"}
    assert fu.resolve_isin("KETY", cache, offline=True)["isin"] == "PLKETY000011"
    assert fu.resolve_isin("X", cache, offline=True, explicit="plpko0000016")["source"] == "explicit"
    with pytest.raises(fu.FundamentalsError):
        fu.resolve_isin("X", cache, offline=True, explicit="PLPKO0000017")
    with pytest.raises(fu.FundamentalsError) as ei:
        fu.resolve_isin("NIEMA", cache, offline=True)
    assert "--isin" in str(ei.value)


def test_remember_isin_override_wins(cache):
    fu.remember_isin("niema", "PLOPTTC00011", cache)
    assert fu.resolve_isin("NIEMA", cache, offline=True) == {"ticker": "NIEMA", "isin": "PLOPTTC00011",
                                                            "source": "override"}
    with pytest.raises(ValueError):
        fu.remember_isin("X", "BAD", cache)
    assert fu.isin_map(cache, offline=True)["NIEMA"] == "PLOPTTC00011"


def test_ticker_for_isin(cache):
    assert fu.ticker_for_isin("PLKETY000011", cache) == "KETY"
    assert fu.ticker_for_isin("PLNCSPL00012", cache) == "NCSPOLKA"
    assert fu.ticker_for_isin("PLZZZZZZ0000", cache) is None


# --- snapshot (fake http) -------------------------------------------------------------

@pytest.fixture
def fake_card(monkeypatch):
    calls = []

    def fake_get(url, params=None, **kw):
        if "GPWQuotations" in url or "NCExternalData" in url or (params and "GPWQuotations" in str(params)) \
                or (params and "NCExternalData" in str(params)):
            raise http.OfflineMiss("listing not cached in this test", url)  # isin_map falls back to portfolios
        calls.append((url, kw))
        body = _read("card_indicators_PKOBP.html") if "indicatorsTab" in url else _read("card_notoria_PKOBP.html")
        return http.Response(url, 200, body, "utf-8", "2026-11-16T12:00:00+00:00", False)

    monkeypatch.setattr(http, "get", fake_get)
    return calls


def test_snapshot_combines_tabs(cache, fake_card):
    s = fu.snapshot("PKOBP", cache_dir=cache, offline=True)
    assert s["isin"] == "PLPKO0000016" and s["isin_source"] == "gpwbenchmark"
    assert s["indicators"]["pe"] == pytest.approx(13.7)
    assert s["notoria"]["period_label"] == "I-III kw. 2026"
    urls = [c[0] for c in fake_card]
    assert "gls_isin=PLPKO0000016" in urls[0] and "code=PKOBP" in urls[1] and "isin=PLPKO0000016" in urls[1]
    assert all(c[1]["ttl_seconds"] == fu.SNAPSHOT_TTL_S for c in fake_card)
    assert s["sources"][0] == "https://www.gpw.pl/spolka?isin=PLPKO0000016"


def test_snapshot_separate_ttls(cache, fake_card):
    # integration fix 1.2: indicators move daily, Notoria only with a new report
    s = fu.snapshot("PKOBP", cache_dir=cache, offline=True, ttl_indicators=3600, ttl_notoria=7 * 86400)
    ttls = [c[1]["ttl_seconds"] for c in fake_card]
    assert ttls == [3600, 7 * 86400]
    assert "indicators_retrieved_at" in s and "notoria_retrieved_at" in s and "from_cache" in s
    fake_card.clear()
    fu.snapshot("PKOBP", cache_dir=cache, offline=True, ttl_seconds=60, ttl_notoria=0)
    assert [c[1]["ttl_seconds"] for c in fake_card] == [60, 0]


# --- history store / restatements --------------------------------------------------------

def _snap(values, when, label="I-III kw. 2026", consolidated=True, pe=13.7):
    return {"ticker": "KETY", "isin": "PLKETY000011", "retrieved_at": when,
            "indicators": {"isin": "PLKETY000011", "pe": pe, "other": {}},
            "notoria": {"period_label": label, "period": fu.parse_period_label(label),
                        "consolidated": consolidated, "unit_source": "thousand PLN", "values": dict(values)}}


def test_history_new_unchanged_restated(tmp_path):
    base = {"revenue": 4.803e9, "net_profit_parent": 594e6}
    r1 = fu.save_snapshot(_snap(base, "2026-11-14T10:00:00+00:00"), tmp_path)
    assert r1["status"] == "new" and r1["period"] == "I-III kw. 2026"
    r2 = fu.save_snapshot(_snap(base, "2026-11-15T10:00:00+00:00"), tmp_path)
    assert r2["status"] == "unchanged" and r2["changed"] == {}
    h = fu.load_history("KETY", tmp_path)
    e = h["periods"]["I-III kw. 2026"]
    assert e["first_retrieved_at"].startswith("2026-11-14") and e["retrieved_at"].startswith("2026-11-15")
    assert e["restated"] is False and e["revisions"] == []

    changed = dict(base, net_profit_parent=580e6)
    r3 = fu.save_snapshot(_snap(changed, "2026-11-16T10:00:00+00:00"), tmp_path)
    assert r3["status"] == "restated"
    assert r3["changed"] == {"net_profit_parent": [594e6, 580e6]}
    e = fu.load_history("KETY", tmp_path)["periods"]["I-III kw. 2026"]
    assert e["values"]["net_profit_parent"] == 580e6          # latest values are current ...
    assert e["restated"] is True                                 # ... flagged ...
    rev = e["revisions"][0]                                      # ... and the old ones kept
    assert rev["previous_values"]["net_profit_parent"] == 594e6
    assert rev["previous_retrieved_at"].startswith("2026-11-15") and rev["replaced_at"].startswith("2026-11-16")


def test_history_tolerates_rounding_noise_and_flags_basis_change(tmp_path):
    fu.save_snapshot(_snap({"revenue": 4.803e9}, "2026-11-14T10:00:00+00:00"), tmp_path)
    assert fu.save_snapshot(_snap({"revenue": 4.803e9 + 0.1}, "2026-11-15T10:00:00+00:00"), tmp_path)["status"] == "unchanged"
    r = fu.save_snapshot(_snap({"revenue": 4.803e9}, "2026-11-16T10:00:00+00:00", consolidated=False), tmp_path)
    assert r["status"] == "restated" and r["changed"] == {"consolidated": [True, False]}
    r = fu.save_snapshot(_snap({"revenue": 4.803e9, "ebitda": 968e6}, "2026-11-17T10:00:00+00:00",
                               consolidated=False), tmp_path)
    assert r["status"] == "restated" and r["changed"] == {"ebitda": [None, 968e6]}


def test_history_periods_series_and_indicators(tmp_path):
    fu.save_snapshot(_snap({"revenue": 1.6e9}, "2026-05-20T10:00:00+00:00", "I kw. 2026", pe=15.0), tmp_path)
    fu.save_snapshot(_snap({"revenue": 3.2e9}, "2026-08-30T10:00:00+00:00", "I-II kw. 2026", pe=16.0), tmp_path)
    fu.save_snapshot(_snap({"revenue": 4.8e9}, "2026-11-16T09:00:00+00:00", pe=17.0), tmp_path)
    fu.save_snapshot(_snap({"revenue": 4.8e9}, "2026-11-16T15:00:00+00:00", pe=18.0), tmp_path)
    h = fu.load_history("kety", tmp_path)
    ser = fu.history_series(h, "revenue")
    assert [x["label"] for x in ser] == ["I kw. 2026", "I-II kw. 2026", "I-III kw. 2026"]
    assert [x["months"] for x in ser] == [3, 6, 9]
    assert [r["pe"] for r in h["indicators"]] == [15.0, 16.0, 18.0]   # one per day, latest wins
    assert fu.save_snapshot({"ticker": "KETY", "notoria": None, "indicators": {}}, tmp_path)["status"] == "no_notoria"


def test_history_missing_and_corrupt(tmp_path):
    assert fu.load_history("NONE", tmp_path) is None
    (tmp_path / "BAD.json").write_text("{x", encoding="utf-8")
    with pytest.raises(fu.FundamentalsError):
        fu.load_history("BAD", tmp_path)


# --- xBRL-JSON ------------------------------------------------------------------------

def test_extract_ifrs_facts_real_trimmed_filing():
    x = json.loads(_read("xbrl_arcticpaper_2023_trimmed.json"))
    f = fu.extract_ifrs_facts(x)
    fy23 = f["2023-12-31"]
    assert fy23["Revenue"] == pytest.approx(3_549_153_000)       # 'Revenue' beats 'RevenueFromSaleOfGoods'
    assert fy23["ProfitLoss"] == pytest.approx(272_388_000)
    assert fy23["ProfitLossAttributableToOwnersOfParent"] == pytest.approx(247_132_000)
    assert fy23["BasicEarningsLossPerShare"] == pytest.approx(3.57)
    assert fy23["Assets"] == pytest.approx(2_722_877_000)        # instant 2024-01-01T00:00 = end of 2023-12-31
    assert fy23["Equity"] == pytest.approx(1_801_508_000)        # only the non-dimensional total
    assert fy23["CashFlowsFromUsedInOperatingActivities"] == pytest.approx(471_204_000)
    assert fy23["_months"]["Revenue"] == 12 and "Assets" not in fy23["_months"]
    assert fy23["_currency"] == "PLN"
    assert f["2022-12-31"]["Revenue"] == pytest.approx(4_894_276_000)


def test_extract_prefers_longest_duration_and_precision():
    def fact(concept, period, value, dec=-3, **dims):
        d = {"concept": "ifrs-full:" + concept, "entity": "scheme:X", "period": period, "unit": "iso4217:PLN"}
        d.update(dims)
        return {"value": str(value), "decimals": dec, "dimensions": d}
    x = {"facts": {
        "a": fact("Revenue", "2023-10-01T00:00:00/2024-01-01T00:00:00", 100),
        "b": fact("Revenue", "2023-01-01T00:00:00/2024-01-01T00:00:00", 400),
        "c": fact("Assets", "2024-01-01T00:00:00", 1000, dec=-6),
        "d": fact("Assets", "2024-01-01T00:00:00", 1234, dec=-3),
        "e": fact("Equity", "2024-01-01T00:00:00", 9, **{"ifrs-full:ComponentsOfEquityAxis": "x"}),
        "f": {"value": None, "dimensions": {"concept": "ifrs-full:ProfitLoss", "period": "2024-01-01T00:00:00"}},
    }}
    r = fu.extract_ifrs_facts(x)["2023-12-31"]
    assert r["Revenue"] == 400 and r["_months"]["Revenue"] == 12
    assert r["Assets"] == 1234 and "Equity" not in r and "ProfitLoss" not in r


def test_xbrl_date_rule():
    assert fu._xbrl_date("2024-01-01T00:00:00", True) == dt.date(2023, 12, 31)
    assert fu._xbrl_date("2023-01-01T00:00:00", False) == dt.date(2023, 1, 1)
    assert fu._xbrl_date("2023-12-31", True) == dt.date(2023, 12, 31)
    assert fu._xbrl_date("bad", True) is None


def test_list_pl_filings_follows_pages(monkeypatch):
    page1 = json.loads(_read("xbrl_filings_pl.json"))
    page2 = {"data": [], "links": {}, "meta": {"count": 876}}
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(params)
        body = page1 if params["page[number]"] == 1 else page2
        return http.Response(url, 200, json.dumps(body), "utf-8", "2026-11-16T12:00:00+00:00", False)

    monkeypatch.setattr(http, "get", fake_get)
    out = fu.list_pl_filings(page_size=5)
    assert len(out) == 5 and len(calls) == 2
    assert json.loads(calls[0]["filter"]) == [{"name": "country", "op": "eq", "val": "PL"}]
    f = out[0]
    assert f["lei"] == "2594007GQP5TDYY9OD45" and f["period_end"] == "2024-03-31"
    assert f["json_url"].startswith("https://filings.xbrl.org/2594007GQP5TDYY9OD45/")


def test_load_xbrl_json_local_and_remote(monkeypatch, tmp_path):
    p = tmp_path / "f.json"
    p.write_text('{"facts": {}}', encoding="utf-8")
    assert fu.load_xbrl_json(p) == {"facts": {}}
    seen = {}

    def fake_get(url, params=None, **kw):
        seen.update(kw, url=url)
        return http.Response(url, 200, '{"facts": {"x": 1}}', "utf-8", "t", False)

    monkeypatch.setattr(http, "get", fake_get)
    assert fu.load_xbrl_json("/LEI/2023-12-31/ESEF/PL/0/x.json") == {"facts": {"x": 1}}
    assert seen["url"] == "https://filings.xbrl.org/LEI/2023-12-31/ESEF/PL/0/x.json" and seen["ttl_seconds"] is None
    with pytest.raises(fu.FundamentalsError):
        fu.load_xbrl_json(tmp_path / "missing.json")


# --- ESEF zip (synthetic, tiny) ------------------------------------------------------------

IXBRL = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:ifrs-full="https://xbrl.ifrs.org/taxonomy/2022-03-24/ifrs-full"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
<body>
<div style="display:none"><ix:header><ix:resources>
  <xbrli:context id="FY23"><xbrli:entity><xbrli:identifier scheme="http://standards.iso.org/iso/17442">LEI1</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:startDate>2023-01-01</xbrli:startDate><xbrli:endDate>2023-12-31</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="Q4"><xbrli:entity><xbrli:identifier scheme="s">LEI1</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:startDate>2023-10-01</xbrli:startDate><xbrli:endDate>2023-12-31</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="I23"><xbrli:entity><xbrli:identifier scheme="s">LEI1</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:instant>2023-12-31</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:context id="I23seg"><xbrli:entity><xbrli:identifier scheme="s">LEI1</xbrli:identifier>
    <xbrli:segment><xbrldi:explicitMember dimension="ifrs-full:ComponentsOfEquityAxis">ifrs-full:RetainedEarningsMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
    <xbrli:period><xbrli:instant>2023-12-31</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:unit id="PLN"><xbrli:measure>iso4217:PLN</xbrli:measure></xbrli:unit>
  <xbrli:unit id="PLNps"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:PLN</xbrli:measure></xbrli:unitNumerator>
    <xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>
</ix:resources></ix:header></div>
<table>
<tr><td>Przychody</td><td><ix:nonFraction name="ifrs-full:Revenue" contextRef="FY23" unitRef="PLN" decimals="-3" scale="3" format="ixt:num-dot-decimal">3,549,153</ix:nonFraction></td></tr>
<tr><td>Przychody Q4</td><td><ix:nonFraction name="ifrs-full:Revenue" contextRef="Q4" unitRef="PLN" decimals="-3" scale="3" format="ixt:num-dot-decimal">800,000</ix:nonFraction></td></tr>
<tr><td>Strata</td><td>(<ix:nonFraction name="ifrs-full:ProfitLoss" contextRef="FY23" unitRef="PLN" decimals="-3" scale="3" sign="-" format="ixt:num-comma-decimal">1 234,5</ix:nonFraction>)</td></tr>
<tr><td>EPS</td><td><ix:nonFraction name="ifrs-full:BasicEarningsLossPerShare" contextRef="FY23" unitRef="PLNps" decimals="2" format="ixt-sec:numwordsen">3.57</ix:nonFraction></td></tr>
<tr><td>Aktywa</td><td><span><ix:nonFraction name="ifrs-full:Assets" contextRef="I23" unitRef="PLN" decimals="-3" scale="3"><b>2 722 877</b></ix:nonFraction></span></td></tr>
<tr><td>Zyski zatrzymane</td><td><ix:nonFraction name="ifrs-full:Equity" contextRef="I23seg" unitRef="PLN" decimals="-3" scale="3">500</ix:nonFraction></td></tr>
<tr><td>Kapitał</td><td><ix:nonFraction name="ifrs-full:Equity" contextRef="I23" unitRef="PLN" decimals="-3" scale="3">1,801,508</ix:nonFraction></td></tr>
<tr><td>Dywidenda</td><td><ix:nonFraction name="ifrs-full:DividendsPaid" contextRef="FY23" unitRef="PLN" decimals="-3" scale="3" format="ixt:fixed-zero">-</ix:nonFraction></td></tr>
<tr><td>Nil</td><td><ix:nonFraction name="ifrs-full:Inventories" contextRef="I23" unitRef="PLN" xsi:nil="true"></ix:nonFraction></td></tr>
</table></body></html>
"""


def test_parse_esef_zip_synthetic(tmp_path):
    z = tmp_path / "esef-test-2023-12-31-pl.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("esef-test/META-INF/reportPackage.json", "{}")
        zf.writestr("esef-test/reports/esef-test-2023-12-31-pl.xhtml", IXBRL.encode("utf-8"))
    res = fu.parse_esef_zip(z)
    assert res["files"] == ["esef-test/reports/esef-test-2023-12-31-pl.xhtml"]
    r = res["facts"]["2023-12-31"]
    assert r["Revenue"] == pytest.approx(3_549_153_000) and r["_months"]["Revenue"] == 12  # FY beats Q4
    assert r["ProfitLoss"] == pytest.approx(-1_234_500)          # sign="-", comma decimal, scale 3
    assert r["BasicEarningsLossPerShare"] == pytest.approx(3.57)
    assert r["Assets"] == pytest.approx(2_722_877_000)           # nested markup inside the fact
    assert r["Equity"] == pytest.approx(1_801_508_000)           # dimensional context skipped
    assert r["DividendsPaid"] == 0.0 and "Inventories" not in r  # fixed-zero / nil
    assert r["_currency"] == "PLN"


def test_esef_same_keys_as_xbrl_json():
    x = fu.extract_ifrs_facts(json.loads(_read("xbrl_arcticpaper_2023_trimmed.json")))["2023-12-31"]
    facts = fu._collect(fu.parse_ixbrl_html(IXBRL), fu.IFRS_CONCEPTS)["2023-12-31"]
    for k in ("Revenue", "Assets", "Equity", "BasicEarningsLossPerShare"):
        assert facts[k] == pytest.approx(x[k])


def test_esef_bad_inputs(tmp_path):
    bad = tmp_path / "x.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(fu.FundamentalsError):
        fu.parse_esef_zip(bad)
    empty = tmp_path / "e.zip"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("r/reports/a.xhtml", "<html><body>no facts</body></html>")
    with pytest.raises(fu.FundamentalsError):
        fu.parse_esef_zip(empty)


@pytest.mark.parametrize("text,fmt,scale,sign,expected", [
    ("1,234.5", "ixt:num-dot-decimal", None, None, 1234.5),
    ("1.234,5", "ixt:num-comma-decimal", None, None, 1234.5),
    ("1 234", "ixt:numcommadecimal", "3", None, 1_234_000),
    ("12", "", "6", "-", -12_000_000),
    ("-", "ixt:fixed-zero", "3", None, 0.0),
    ("—", "", None, None, 0.0),
    ("abc", "", None, None, None),
])
def test_ix_number(text, fmt, scale, sign, expected):
    assert fu.ix_number(text, fmt, scale, sign) == (pytest.approx(expected) if expected is not None else None)


def test_concept_list_is_documented_ifrs_names():
    assert "Revenue" in fu.IFRS_CONCEPTS and "CashFlowsFromUsedInOperatingActivities" in fu.IFRS_CONCEPTS
    for names in fu.IFRS_CONCEPTS.values():
        assert names and all(n[0].isupper() and ":" not in n for n in names)
    assert copy.deepcopy(fu.IFRS_CONCEPTS) == fu.IFRS_CONCEPTS
