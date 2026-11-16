import datetime as dt
import json
from pathlib import Path

import pytest

import dividend_engine as E
import dividend_sources as S
from gpwlib import panel

D = dt.date
FIX = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------
# Synthetic archive: reconstruction from reference-price adjustments
# --------------------------------------------------------------------------

def _sessions(n, start=D(2021, 1, 4)):
    out, d = [], start
    while len(out) < n:
        if E.is_session(d):
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def synth(dates, closes, divs, name="DIVCO"):
    """Archive-like payloads: on an ex-date the reference price is prev close - DPS
    and change% is measured against it (rounded to 2 decimals like GPW)."""
    sess, prev = [], None
    for d, c in zip(dates, closes):
        ref = prev - divs.get(d, 0.0) if prev is not None else c
        chg = round((c / ref - 1) * 100, 2)
        sess.append({"date": d, "rows": [{"name": name, "currency": "PLN", "open": c, "high": c, "low": c,
                                          "close": c, "change_pct": chg, "turnover_k": 100.0}]})
        prev = c
    return sess


def test_reconstruct_dividend_from_adjusted_series():
    dates = _sessions(300)
    closes = [40.0 + 0.01 * i for i in range(300)]
    ex1, ex2 = dates[100], dates[250]
    closes[100] = closes[99] - 2.0 + 0.10      # drop by the dividend plus a small gain
    closes[250] = closes[249] - 1.37
    sess = synth(dates, closes, {ex1: 2.0, ex2: 1.37})
    s = panel.build_panel(sess)["DIVCO"]
    assert [a["date"] for a in s.adjustments] == [ex2, ex1]
    raw = E.raw_closes(s.dates, s.close, s.adjustments)
    assert raw == pytest.approx(closes, abs=1e-3)       # un-adjustment restores the archive closes (2-dec change%)
    ev = E.reconstruct_dividends(s.dates, raw, s.adjustments)
    assert [e["ex_date"] for e in ev] == [ex1, ex2]
    assert ev[0]["dps"] == pytest.approx(2.0, abs=0.01)
    assert ev[1]["dps"] == pytest.approx(1.37, abs=0.01)
    assert all(e["kind"] == "dividend" and e["last_cum_date"] < e["ex_date"] for e in ev)
    assert ev[0]["precision_pln"] < 0.05


def test_small_dividend_invisible_and_split_not_a_dividend():
    dates = _sessions(60)
    closes = [100.0] * 60
    closes[20] = 99.7              # 0.30 zł = 0.3% of price -> below panel.ADJ_TOLERANCE
    closes[40] = 50.0              # 2:1 split: reference price halves
    sess = synth(dates, closes, {dates[20]: 0.30, dates[40]: 49.7 + 0.15})
    s = panel.build_panel(sess)["DIVCO"]
    raw = E.raw_closes(s.dates, s.close, s.adjustments)
    ev = E.reconstruct_dividends(s.dates, raw, s.adjustments)
    assert all(e["ex_date"] != dates[20] for e in ev)
    split = [e for e in ev if e["ex_date"] == dates[40]]
    assert split and split[0]["kind"] == "corporate_action" and split[0]["dps"] is None


def test_cache_gap_is_not_a_dividend():
    dates = ["2021-12-29", "2021-12-30", "2022-01-04", "2022-01-05"]  # 2022-01-03 missing
    assert E.cache_gap_dates(dates) == {"2022-01-04"}
    adj = [{"date": "2022-01-04", "factor": 0.98}]
    ev = E.reconstruct_dividends(dates, [10, 10, 10, 10], adj, E.cache_gap_dates(dates))
    assert ev[0]["kind"] == "cache_gap"


def test_t_plus_2_rule():
    x = E.exdiv_from_record(D(2026, 9, 17))          # Thursday
    assert x["ex_date"] == "2026-09-16" and x["last_cum_date"] == "2026-09-15"
    y = E.exdiv_from_record(D(2026, 1, 7))           # 6 Jan is a holiday
    assert y["ex_date"] == "2026-01-05"
    z = E.exdiv_from_last_cum(D(2026, 9, 15))
    assert z["ex_date"] == "2026-09-16" and z["record_date"] == "2026-09-17"


# --------------------------------------------------------------------------
# ESPI text parsing (real report bodies from gpw.pl, Dec 2025 - Nov 2026)
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def reports():
    return {r["geru_id"]: r for r in json.loads((FIX / "espi_dividend_reports.json").read_text(encoding="utf-8"))}


def _p(reports, g):
    r = reports[g]
    return E.parse_dividend_text(r["title"], r["body_text"])


def test_espi_atlanta_resolution(reports):
    p = _p(reports, 478924)
    assert p["stage"] == "resolution" and p["dps"] == 1.0
    assert p["record_date"] == "2026-03-06" and p["ex_date"] == "2026-03-05"
    assert p["payment_dates"] == ["2026-04-03"] and p["shares"] == 6091904 and p["total_pln"] == 6091904.0


def test_espi_text_remaining_tranche(reports):
    p = _p(reports, 495002)   # 4,26 zł for the year, 2,13 zł still to be paid after two interims
    assert p["dps"] == 2.13 and p["dps_total_year"] == 4.26
    assert p["record_date"] == "2026-10-08" and p["payment_dates"] == ["2026-10-15"]


def test_espi_betacom_groszy_and_statute_date_ignored(reports):
    p = _p(reports, 497520)
    assert p["dps"] == 0.10 and p["record_date"] == "2026-10-01"
    assert p["payment_dates"] == ["2026-10-15"]      # "ustawy z dnia 25 lipca 2005" is not a payment date


def test_espi_grodno_and_ambra(reports):
    g = _p(reports, 499142)
    assert g["dps"] == 0.17 and g["record_date"] == "2026-10-27" and g["payment_dates"] == ["2026-11-19"]
    a = _p(reports, 498946)
    assert a["dps"] == 1.10 and a["record_date"] == "2026-10-28" and a["total_pln"] == pytest.approx(27727308.40)


def test_espi_lpp_interim(reports):
    p = _p(reports, 481176)
    assert p["stage"] == "interim" and p["dps"] == 400.0 and p["record_date"] == "2026-04-23"
    assert p["ex_date"] == "2026-04-22" and p["total_pln"] == 742356000.0


def test_espi_delko_special_dividend(reports):
    p = _p(reports, 478981)
    assert p["special"] is True and p["dps_parts"] == [0.65, 0.40] and p["dps"] == pytest.approx(1.05)
    assert p["ex_date"] == "2026-01-05" and p["payment_dates"] == ["2026-01-21"]


def test_espi_proposal_and_interims_without_dates(reports):
    p = _p(reports, 496955)
    assert p["stage"] == "proposal" and p["dps"] == 1.10 and p["ex_date"] is None
    s = _p(reports, 499203)
    assert s["stage"] == "interim" and s["dps"] is None    # only a total amount
    e = _p(reports, 500273)
    assert e["stage"] == "interim" and e["dps"] == 0.40 and e["fiscal_year"] == 2026


@pytest.mark.parametrize("title,body,stage,dps", [
    ("Rekomendacja Zarządu dotycząca wypłaty dywidendy", "Zarząd rekomenduje wypłatę 5,25 zł na jedną akcję.",
     "proposal", 5.25),
    ("Rekomendacja Zarządu w sprawie niewypłacania dywidendy", "Zarząd proponuje przeznaczyć zysk w całości na kapitał zapasowy.",
     "no_dividend", None),
    ("Uchwała ZWZ w sprawie podziału zysku", "dywidenda w wysokości 45 groszy na każdą akcję; dzień dywidendy 10.07.2026 r.",
     "resolution", 0.45),
    ("Aktualizacja polityki dywidendowej", "Spółka zamierza wypłacać 50% zysku.", "policy", None),
])
def test_espi_synthetic_polish_cases(title, body, stage, dps):
    p = E.parse_dividend_text(title, body)
    assert p["stage"] == stage
    assert p["dps"] == dps


def test_record_date_derives_ex_date_from_dotted_date():
    p = E.parse_dividend_text("Uchwała ZWZ", "dywidenda 2,00 zł na akcję, dzień dywidendy 10.07.2026 r., "
                                             "termin wypłaty 24.07.2026 r.")
    assert p["ex_date"] == "2026-07-09" and p["last_cum_date"] == "2026-07-08"
    assert p["payment_dates"] == ["2026-07-24"]


# --------------------------------------------------------------------------
# Merge, annual history, metrics
# --------------------------------------------------------------------------

def _ev(d, dps, src="archive"):
    return {"ex_date": d, "dps": dps, "kind": "dividend", "source": src}


def test_merge_espi_replaces_archive_and_adds_invisible():
    arch = [_ev("2025-07-10", 1.99), dict(_ev("2024-07-10", None), kind="corporate_action")]
    espi_items = [{"stage": "resolution", "dps": 2.00, "currency": "PLN", "ex_date": "2025-07-10", "published": "2025-06-01"},
                  {"stage": "resolution", "dps": 0.10, "currency": "PLN", "ex_date": "2025-12-01", "published": "2025-11-01"},
                  {"stage": "resolution", "dps": 3.00, "currency": "PLN", "ex_date": "2026-07-10", "published": "2026-06-01"},
                  {"stage": "policy", "dps": 9.0, "currency": "PLN", "ex_date": None, "published": "2026-01-01"}]
    m = E.merge_history(arch, espi_items, D(2026, 3, 1))
    assert [e["ex_date"] for e in m["events"]] == ["2025-07-10", "2025-12-01"]
    assert m["events"][0]["dps"] == 2.00 and m["events"][0]["source"] == "archive+espi"
    assert m["crosscheck"][0]["status"] == "match"
    assert m["crosscheck"][1]["status"] == "not_in_archive"
    assert m["upcoming"][0]["dps"] == 3.00


def test_annual_history_cuts_suspension_cagr():
    ev = [_ev("2019-07-01", 1.0), _ev("2021-07-01", 1.2), _ev("2022-07-01", 1.3), _ev("2023-07-01", 1.0),
          _ev("2024-07-01", 1.5), _ev("2025-07-01", 1.6)]
    ann = E.annual_history(ev, "2019-01-02", D(2026, 3, 1))
    assert [a["year"] for a in ann] == list(range(2019, 2027))
    assert ann[-1]["complete"] is False
    m = E.history_metrics(ann)
    assert m["years_observed"] == 7 and m["years_paid"] == 6 and m["consecutive_years"] == 5
    assert m["n_suspensions"] == 1 and m["n_cuts"] == 1
    assert m["cuts"][1]["year"] == 2023 and m["cuts"][1]["change_pct"] == pytest.approx(-23.1)
    assert m["dps_cagr_3y_pct"] == pytest.approx(((1.6 / 1.3) ** (1 / 3) - 1) * 100, abs=0.01)
    assert m["dps_cagr_5y_pct"] is None              # 2020 had no dividend -> no CAGR from zero


def test_series_starting_mid_year_skips_partial_year():
    ann = E.annual_history([_ev("2021-07-01", 1.0)], "2020-06-15", D(2022, 12, 30))
    assert ann[0]["year"] == 2021


def test_running_year_counts_only_for_single_payers():
    ev = [_ev("2024-04-20", 1.0), _ev("2024-10-20", 1.0), _ev("2025-04-20", 1.0), _ev("2025-10-20", 1.0),
          _ev("2026-04-20", 1.0)]
    m = E.history_metrics(E.annual_history(ev, "2024-01-02", D(2026, 6, 1)))
    assert m["years_observed"] == 2 and m["n_cuts"] == 0      # 2026 has a second tranche to come


def test_ttm_and_yields_and_tax():
    ev = [_ev("2025-06-01", 1.0), _ev("2025-12-01", 0.5)]
    assert E.ttm_dps(ev, D(2026, 5, 31)) == 1.5
    assert E.ttm_dps(ev, D(2026, 6, 2)) == 0.5
    y = E.yield_pct(2.0, 40.0)
    assert y == 5.0
    n = E.net_yields(y)
    assert n["regular"] == pytest.approx(4.05) and n["ike"] == 5.0 and n["ikze_after_exit_tax"] == pytest.approx(4.5)
    c = E.dividend_cash(4.80, 200, "regular")    # 960 zł * 19% = 182.40 -> 182 zł
    assert c == {"gross": 960.0, "tax": 182.0, "net": 778.0, "account": "regular"}
    assert E.dividend_cash(0.13, 100, "regular")["tax"] == 2.0   # 2.47 -> 2
    assert E.dividend_cash(0.29, 10, "regular")["tax"] == 1.0    # 0.551 -> 1
    assert E.dividend_cash(4.80, 200, "ike")["net"] == 960.0
    assert E.net_yields(None)["regular"] is None


def test_ike_vs_regular_constant_price():
    r = E.ike_vs_regular(price=50.0, dps=3.0, shares=100, years=10, growth_pct=0.0)
    assert r["ike"]["dividend_tax_paid"] == 0 and r["regular"]["dividend_tax_paid"] > 0
    assert r["ike"]["value_after_exit"] > r["regular"]["value_after_exit"]
    assert r["ikze"]["exit_tax"] == pytest.approx(round(r["ikze"]["value_before_exit"] * 0.10), abs=1)
    assert r["regular"]["exit_tax"] == 0              # no capital gain at constant price
    assert r["ike_minus_regular"] > 0


def test_ike_vs_regular_growth_taxes_gain():
    r = E.ike_vs_regular(price=50.0, dps=2.0, shares=100, years=10, growth_pct=5.0, commission_pct=0.39,
                         commission_min=5.0)
    assert r["regular"]["exit_tax"] > 0 and r["regular"]["fees"] > 0


def test_capture_loses_after_tax_and_fees():
    c = E.capture_example(10_000, price=50.0, dps=2.5, drop_ratio=1.0, commission_pct=0.39, commission_min=5.0)
    assert c["shares"] == 200 and c["dividend_tax"] == 95.0
    assert c["result"] < 0
    assert c["breakeven_drop_ratio"] < 0.81
    free = E.capture_example(10_000, price=50.0, dps=2.5, drop_ratio=0.0, commission_pct=0.0, commission_min=0.0)
    assert free["result"] == pytest.approx(405.0)    # 500 - 95 tax, no drop, no fees
    with pytest.raises(ValueError):
        E.capture_example(1000, 0, 1, None, 0, 0)


def test_exdate_drops():
    dates = ["2026-07-01", "2026-07-02", "2026-07-03"]
    ev = [{"ex_date": "2026-07-02", "dps": 2.0}]
    r = E.exdate_drops(ev, dates, raw_open=[50, 48.2, 48], raw_close=[50, 48.4, 48], index_ret={"2026-07-02": 0.01})
    assert r["n"] == 1 and r["median_ratio_open"] == pytest.approx(0.9)
    assert r["median_ratio_close"] == pytest.approx(0.8)
    assert r["median_ratio_close_mkt"] == pytest.approx((50 * 1.01 - 48.4) / 2.0, abs=1e-3)


# --------------------------------------------------------------------------
# Stability score and trap flags
# --------------------------------------------------------------------------

def _m(**kw):
    base = {"years_observed": 7, "years_paid": 7, "consecutive_years": 7, "n_cuts": 0, "n_suspensions": 0,
            "dps_cagr_5y_pct": 10.0, "dps_cagr_3y_pct": 10.0, "cuts": [], "last_final_year": 2025}
    base.update(kw)
    return base


def test_stability_score_bounds_and_order():
    perfect = E.stability_score(_m(), 50.0)
    assert perfect["score"] == 100 and perfect["confidence"] == "high"
    weak = E.stability_score(_m(years_paid=4, consecutive_years=1, n_cuts=1, n_suspensions=2,
                                dps_cagr_5y_pct=-15.0), 120.0)
    assert 0 <= weak["score"] < 40
    assert E.stability_score(_m(years_observed=0, years_paid=0), None)["score"] == 0
    never = E.stability_score(_m(years_paid=0, consecutive_years=0, dps_cagr_5y_pct=None, dps_cagr_3y_pct=None), None)
    assert never["score"] == 0
    # payout bands: <=60 full, 100 -> one third, >100 zero, unknown -> half
    assert E._payout_points(60) == 15 and E._payout_points(100) == pytest.approx(5)
    assert E._payout_points(130) == 0 and E._payout_points(None) == 7.5
    assert E._growth_points(0) == 7.5 and E._growth_points(-10) == 0 and E._growth_points(25) == 15


def _flags(**kw):
    args = dict(yield_gross=5.0, sector_median=4.0, payout_pct=50.0, profit_change_pct=5.0, net_profit=1e9,
                annual=[], special_text=False, state_controlled=None, is_bank=False, fcf_cover=2.0, metrics=_m())
    args.update(kw)
    return [f["code"] for f in E.trap_flags(**args)]


def test_trap_flags():
    assert _flags() == []
    assert "yield_above_2x_sector_median" in _flags(yield_gross=9.0, sector_median=4.0)
    assert "payout_above_100pct" in _flags(payout_pct=140.0)
    assert "net_loss" in _flags(net_profit=-5.0)
    assert "falling_eps" in _flags(profit_change_pct=-25.0)
    assert "fcf_below_dividend" in _flags(fcf_cover=0.6)
    assert "state_controlled_policy_risk" in _flags(state_controlled={"holder": "Skarb Państwa"})
    assert "bank_knf_dividend_policy" in _flags(is_bank=True)
    assert "one_off_special_dividend" in _flags(special_text=True)
    ann = [{"year": y, "dps": d, "payments": 1, "complete": True, "special": False, "sources": []}
           for y, d in ((2021, 1.0), (2022, 1.1), (2023, 1.0), (2024, 3.5))]
    assert "one_off_special_dividend" in _flags(annual=ann)
    cut = _m(cuts=[{"year": 2025, "kind": "cut"}])
    assert "recent_cut_or_suspension" in _flags(metrics=cut)


def test_cut_risk_needs_two_flags():
    one = E.trap_flags(yield_gross=5, sector_median=4, payout_pct=150, profit_change_pct=None, net_profit=None,
                       annual=[], special_text=False, state_controlled=None, is_bank=False, fcf_cover=None,
                       metrics=_m())
    assert E.cut_risk(one) is None
    two = one + [{"code": "fcf_below_dividend", "severity": "medium", "detail": {}}]
    cr = E.cut_risk(two)
    assert cr and cr["score"] == 60 and set(cr["codes"]) == {"payout_above_100pct", "fcf_below_dividend"}


# --------------------------------------------------------------------------
# Next payment, income calendar, biznesradar parser
# --------------------------------------------------------------------------

def test_estimate_next_and_calendar():
    ev = [_ev("2025-09-16", 4.80)]
    est = E.estimate_next(ev, [], D(2025, 11, 13))
    assert est[0]["ex_date"] == "2026-09-15" and est[0]["date_kind"] == "estimated"
    cal = E.income_calendar([{"ticker": "PZU", "ex_date": est[0]["ex_date"], "dps": 4.80, "shares": 200,
                              "date_kind": "estimated"},
                             {"ticker": "OLD", "ex_date": "2025-01-01", "dps": 1, "shares": 1, "date_kind": "x"}],
                            D(2025, 11, 13))
    assert len(cal["rows"]) == 1 and cal["total_gross"] == 960.0 and cal["total_net_regular"] == 778.0
    assert cal["by_month"] == {"2026-09": {"gross": 960.0, "net_regular": 778.0}}


def test_parse_biznesradar_company_table():
    rows = S.parse_biznesradar_company((FIX / "biznesradar_pzu_table.html").read_text(encoding="utf-8"))
    assert rows[0]["fiscal_year"] == 2025 and rows[0]["dps"] == 4.80
    assert rows[0]["last_cum_date"] == "2026-09-15" and rows[0]["ex_date"] == "2026-09-16"
    assert rows[0]["status"] == "wypłacona"
    by_year = {r["fiscal_year"]: r["dps"] for r in rows}
    assert by_year[2021] == 1.94 and by_year[2022] == 2.40
    assert S.parse_biznesradar_company("<html>no table</html>") == []


def test_state_holdings_table_is_sourced():
    for t, v in S.STATE_HOLDINGS.items():
        assert v["source"].startswith("http") and 0 < v["stake_pct"] < 100
    assert S.KNF_STANCE["source"].startswith("https://www.knf.gov.pl")


def test_merge_dedupes_repeated_resolution_and_growth_not_special():
    arch = [_ev("2026-08-04", 6.14)]
    item = {"stage": "resolution", "dps": 6.14, "currency": "PLN", "ex_date": "2026-08-04", "published": "2026-06-20"}
    m = E.merge_history(arch, [item, dict(item, published="2026-06-21")], D(2026, 11, 13))
    assert len(m["crosscheck"]) == 1 and m["crosscheck"][0]["status"] == "match"
    grow = [{"year": y, "dps": d, "payments": 1, "complete": True, "special": False, "sources": []}
            for y, d in ((2019, 1.33), (2022, 1.83), (2024, 3.86), (2025, 5.48))]
    assert E.special_dividend_check(grow) is None


def test_espi_period_end_date_is_not_record_date(reports):
    pzu = _p(reports, 491754)   # "... za rok obrotowy zakończony dnia 31 grudnia 2025 r., został ustalony na 17 września 2026"
    assert pzu["dps"] == 4.80 and pzu["record_date"] == "2026-09-17" and pzu["ex_date"] == "2026-09-16"
    assert pzu["payment_dates"] == ["2026-10-08"] and pzu["shares"] == 863523000
    kruk = _p(reports, 489721)
    assert kruk["dps"] == 20.0 and kruk["record_date"] == "2026-06-01" and kruk["ex_date"] == "2026-05-29"


def test_espi_date_before_keyword_jako(reports):
    orl = _p(reports, 490999)   # "18 czerwca 2026 roku jako dzień dywidendy oraz - 25 czerwca 2026 roku jako dzień wypłaty"
    assert orl["dps"] == 8.0 and orl["record_date"] == "2026-06-18" and orl["ex_date"] == "2026-06-17"
    assert orl["payment_dates"] == ["2026-06-25"]


def test_merge_shifted_espi_date_same_amount_is_one_payment():
    arch = [_ev("2026-06-17", 8.00)]
    item = {"stage": "resolution", "dps": 8.0, "currency": "PLN", "ex_date": "2026-06-24", "published": "2026-06-09"}
    m = E.merge_history(arch, [item], D(2026, 11, 13))
    assert len(m["events"]) == 1 and m["crosscheck"][0]["status"] == "match_date_shifted"


def test_large_archive_cut_without_espi_is_excluded_in_window():
    arch = [dict(_ev("2026-04-07", 123.0), factor=0.834, cut_pct=16.6), dict(_ev("2026-06-10", 2.0), factor=0.96)]
    m = E.merge_history(arch, [], D(2026, 11, 13), espi_range=("2025-11-16", "2026-11-16"))
    assert [e["ex_date"] for e in m["events"]] == ["2026-06-10"]
    assert m["crosscheck"][0]["status"] == "archive_only_large_cut_excluded"
    m2 = E.merge_history(arch, [], D(2026, 11, 13))          # no ESPI scan -> kept
    assert len(m2["events"]) == 2
