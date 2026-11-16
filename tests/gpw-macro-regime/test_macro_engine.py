"""State rules of gpw-macro-regime on synthetic series (pure engine)."""

import datetime as dt
import math

import pytest

import macro_engine as me

D = dt.date


def daily(start, n, f):
    """n business days from start with value f(i)."""
    out, d, i = [], start, 0
    while len(out) < n:
        if d.weekday() < 5:
            out.append((d, f(i)))
            i += 1
        d += dt.timedelta(days=1)
    return out


RATES = [(D(2022, 9, 8), 6.75), (D(2023, 9, 7), 6.0), (D(2023, 10, 5), 5.75), (D(2025, 5, 8), 5.25),
         (D(2025, 7, 3), 5.0), (D(2025, 9, 4), 4.75), (D(2025, 10, 9), 4.5), (D(2025, 11, 6), 4.25),
         (D(2025, 12, 4), 4.0), (D(2026, 3, 5), 3.75)]


# ---------------------------------------------------------------- monetary

def test_monetary_easing_within_120_days():
    m = me.monetary_state(RATES, D(2026, 7, 3))  # 120 days after 2026-03-05
    assert m["state"] == "easing" and m["simple"] == "easing"
    assert m["rate"] == 3.75 and m["last_move_bp"] == -25 and m["previous_move_bp"] == -25
    assert m["last_two_moves"] == ["cut", "cut"] and m["turn"] is False


def test_monetary_hold_after_easing_after_120_days():
    m = me.monetary_state(RATES, D(2026, 7, 4))
    assert m["state"] == "hold_after_easing" and m["simple"] == "neutral"
    assert m["days_since_last_change"] == 121
    # the cycle: every consecutive cut since 2023-09 (no hike in between)
    assert m["cycle_start"] == "2023-09-07" and m["cycle_moves"] == 9 and m["cycle_total_bp"] == -300


def test_monetary_tightening_and_turn():
    rates = [(D(2025, 1, 1), 5.0), (D(2025, 6, 1), 4.75), (D(2026, 10, 1), 5.0)]
    m = me.monetary_state(rates, D(2026, 11, 16))
    assert m["state"] == "tightening" and m["turn"] is True and m["cycle_moves"] == 1
    m2 = me.monetary_state(rates, D(2027, 3, 1))
    assert m2["state"] == "hold_after_tightening" and m2["simple"] == "neutral"


def test_monetary_point_in_time_and_too_short():
    assert me.monetary_state(RATES, D(2025, 5, 7))["rate"] == 5.75
    assert me.monetary_state(RATES[:1], D(2026, 1, 1)) is None
    assert me.monetary_state(RATES, D(2020, 1, 1)) is None


def test_monetary_12m_change():
    m = me.monetary_state(RATES, D(2026, 11, 16))
    assert m["change_12m_bp"] == -50 and m["trend"] == "falling"


# ---------------------------------------------------------------- inflation / real rate

def hicp(values, start="2025-01"):
    return [(me.month_add(start, i), v) for i, v in enumerate(values)]


@pytest.mark.parametrize("vals,trend,band", [
    ([2.5, 2.6, 2.7, 3.0, 3.6], "rising", "above"),      # +1.0 pp vs 3 months earlier
    ([4.0, 4.0, 4.0, 3.8, 3.7], "falling", "above"),     # exactly -0.3 pp counts as a trend
    ([3.0, 3.0, 3.0, 2.9, 2.8], "stable", "within"),
    ([1.6, 1.5, 1.4, 1.2, 1.0], "falling", "below"),
])
def test_inflation_trend_and_band(vals, trend, band):
    s = me.inflation_state(hicp(vals), D(2025, 6, 30))
    assert s["trend"] == trend and s["band"] == band
    assert s["period"] == "2025-05"


def test_inflation_band_edges():
    assert me.inflation_band(3.5) == "within" and me.inflation_band(3.51) == "above"
    assert me.inflation_band(1.5) == "within" and me.inflation_band(1.49) == "below"


def test_inflation_point_in_time_publication_lag():
    s = hicp([3.0, 3.1, 3.2, 3.3, 3.4, 3.5])  # up to 2025-06
    # on 2025-07-10 June HICP is not out yet (needs 46 days from 1 June)
    assert me.inflation_state(s, D(2025, 7, 10), point_in_time=True)["period"] == "2025-05"
    assert me.inflation_state(s, D(2025, 7, 17), point_in_time=True)["period"] == "2025-06"
    assert me.inflation_state(s, D(2025, 7, 10))["period"] == "2025-06"  # live run: all fetched data


def test_inflation_missing_comparison_month_gives_no_trend():
    s = [("2025-01", 3.0), ("2025-05", 3.5)]
    assert me.inflation_state(s, D(2025, 6, 1))["trend"] is None


@pytest.mark.parametrize("rate,cpi,sign,stance", [(3.75, 4.0, "negative", "accommodative"),
                                                  (5.75, 3.5, "positive", "restrictive"),
                                                  (4.0, 3.0, "positive", "moderate"),
                                                  (3.0, 3.0, "zero", "moderate")])
def test_real_rate(rate, cpi, sign, stance):
    r = me.real_rate_state(rate, cpi)
    assert r["sign"] == sign and r["stance"] == stance
    assert me.real_rate_state(None, 3.0) is None


# ---------------------------------------------------------------- PLN

def test_fx_metrics_weak_strong_neutral():
    as_of = D(2026, 11, 16)
    up = daily(D(2025, 6, 2), 380, lambda i: 4.20 * (1 + 0.0002 * i))
    m = me.fx_metrics(up, as_of)
    assert m["pln_signal"] == "weak" and m["dev_sma200_pct"] > 1 and m["change_3m_pct"] > 0
    down = daily(D(2025, 6, 2), 380, lambda i: 4.40 * (1 - 0.0002 * i))
    assert me.fx_metrics(down, as_of)["pln_signal"] == "strong"
    flat = daily(D(2025, 6, 2), 380, lambda i: 4.30 + 0.002 * math.sin(i))
    f = me.fx_metrics(flat, as_of)
    assert f["pln_signal"] == "neutral" and f["vol_elevated"] is False
    assert me.fx_metrics(up[:150], as_of) is None


def test_fx_metrics_above_band_but_falling_is_neutral():
    # long rise, then a 3-month pull-back that keeps the pair above its average
    s = daily(D(2025, 6, 2), 300, lambda i: 4.0 + 0.002 * i if i < 230 else 4.46 - 0.0005 * (i - 230))
    m = me.fx_metrics(s, s[-1][0])
    assert m["dev_sma200_pct"] > 1 and m["change_3m_pct"] < 0
    assert m["pln_signal"] == "neutral"


def test_fx_volatility_elevated():
    calm = [4.3 + 0.001 * math.sin(i) for i in range(300)]
    wild = [4.3 + 0.08 * (1 if i % 2 else -1) for i in range(70)]
    s = daily(D(2025, 1, 1), 370, lambda i: (calm + wild)[i])
    assert me.fx_metrics(s, s[-1][0])["vol_elevated"] is True


def _sig(sig):
    return {"pln_signal": sig, "vol_elevated": False}


def test_pln_state_weighting():
    assert me.pln_state(_sig("weak"), _sig("neutral"))["regime"] == "weak"       # 2/3
    assert me.pln_state(_sig("neutral"), _sig("weak"))["regime"] == "neutral"    # 1/3
    assert me.pln_state(_sig("strong"), _sig("strong"))["regime"] == "strong"
    assert me.pln_state(_sig("strong"), _sig("weak"))["regime"] == "neutral"     # -1/3
    one = me.pln_state(None, _sig("weak"))
    assert one["regime"] == "weak" and one["pairs_used"] == ["USD"]
    assert me.pln_state(None, None) is None


# ---------------------------------------------------------------- spread / curve

def test_spread_daily_trend():
    bund = daily(D(2026, 7, 1), 100, lambda i: 2.6)
    s = me.spread_state_daily((D(2026, 11, 13), 5.6), (D(2026, 8, 14), 5.4), bund)
    assert s["value_bp"] == 300 and s["change_3m_bp"] == 20 and s["trend"] == "widening"
    s2 = me.spread_state_daily((D(2026, 11, 13), 5.5), (D(2026, 8, 14), 5.6), bund)
    assert s2["trend"] == "stable"
    assert me.spread_state_daily(None, (D(2026, 8, 14), 5.4), bund) is None
    # Bund observation too far from the bond fixing date -> no spread
    assert me.spread_state_daily((D(2026, 12, 31), 5.6), (D(2026, 8, 14), 5.4), bund) is None


def test_spread_monthly_and_point_in_time():
    pl = [("2026-0{}".format(m), 5.0 + 0.1 * m) for m in range(1, 9)]
    bund = daily(D(2026, 1, 1), 180, lambda i: 2.5)
    sm = me.spread_monthly(pl, bund)
    assert sm[0] == ("2026-01", 260.0)
    s = me.spread_state_monthly(sm, D(2026, 8, 31))
    assert s["as_of"] == "2026-08" and s["change_3m_bp"] == 30 and s["trend"] == "widening"
    pit = me.spread_state_monthly(sm, D(2026, 8, 31), point_in_time=True)
    assert pit["as_of"] == "2026-07"  # August ECB data not out until ~10 September


@pytest.mark.parametrize("slope,shape", [(-10, "inverted"), (20, "flat"), (100, "normal"), (200, "steep")])
def test_curve_shape(slope, shape):
    assert me.curve_shape(slope) == shape


def test_curve_state_trend():
    def snap(d, y2, y10):
        return {"date": d, "y2": {"yield": y2, "name": "OK0128"}, "y10": {"yield": y10, "name": "DS1035"}}

    c = me.curve_state(snap(D(2026, 11, 13), 4.9, 6.3), snap(D(2026, 8, 14), 4.6, 6.2))
    assert c["value_bp"] == 140 and c["shape"] == "normal" and c["change_3m_bp"] == -20 and c["trend"] == "flattening"
    assert me.curve_state(snap(D(2026, 11, 13), 4.9, 6.3), None)["trend"] is None
    assert me.curve_state(None, None) is None


# ---------------------------------------------------------------- composite

def test_composite_weights_labels_and_renormalisation():
    sc = {"monetary": 1.0, "inflation": 1.0, "spread": 0.0, "pln": 0.0}
    c = me.composite(sc)
    assert c["score"] == pytest.approx(0.55) and c["label"] == "supportive"
    c2 = me.composite({"monetary": -1.0, "inflation": None, "spread": None, "pln": 0.0})
    assert c2["score"] == pytest.approx(-0.6) and c2["label"] == "headwind" and c2["components_used"] == ["monetary", "pln"]
    c3 = me.composite({"monetary": 0.5, "inflation": -0.5, "spread": 0.0, "pln": 0.0})
    assert c3["label"] == "mixed"
    assert me.composite({k: None for k in me.WEIGHTS})["label"] is None


def test_composite_inverted_curve_penalty():
    sc = {"monetary": 1.0, "inflation": 0.0, "spread": 0.0, "pln": 0.0}  # 0.30
    assert me.composite(sc)["label"] == "supportive"
    c = me.composite(sc, {"shape": "inverted"})
    assert c["curve_penalty"] == 0.1 and c["label"] == "mixed"


def test_component_scores_real_rate_modifier():
    mon = {"state": "hold_after_easing"}
    acc = me.component_scores(mon, None, {"stance": "accommodative"}, None, None, None)
    res = me.component_scores(mon, None, {"stance": "restrictive"}, None, None, None)
    assert acc["monetary"] == 0.75 and res["monetary"] == 0.25
    assert me.component_scores({"state": "easing"}, None, {"stance": "accommodative"}, None, None, None)["monetary"] == 1.0


def test_inflation_score_table_is_complete():
    for trend in ("falling", "stable", "rising"):
        for band in ("above", "within", "below"):
            assert -1 <= me.INFLATION_SCORE[(trend, band)] <= 1


def test_confidence_levels():
    assert me.confidence([], []) == "high"
    assert me.confidence(["spread"], []) == "medium"
    assert me.confidence([], ["eurostat_hicp"]) == "medium"
    assert me.confidence(["spread"], ["ecb_pl10y"]) == "low"


def test_build_state_contract():
    mon = me.monetary_state(RATES, D(2026, 11, 16))
    st = me.build_state(mon, {"trend": "rising", "band": "above"}, {"sign": "negative", "stance": "accommodative"},
                        {"regime": "weak"}, {"trend": "widening"}, {"shape": "normal"})
    s = st["state"]
    assert set(["monetary", "pln", "spread", "inflation", "composite"]) <= set(s)
    assert s["regime_label"] == "neutral_x_pln_weak"
    assert s["composite"] == "headwind"


# ---------------------------------------------------------------- sector tilts

def test_sector_tilts_easing_weak_pln():
    mon = {"state": "easing"}
    infl = {"trend": "falling"}
    real = {"sign": "negative"}
    t = me.sector_tilts(mon, infl, real, {"regime": "weak"}, -40.0)
    assert t["banks"]["tilt"] == "negative" and t["banks"]["score"] == -1.0
    assert t["developers"]["tilt"] == "positive" and t["developers"]["score"] == 1.0  # clamped
    assert t["exporters"]["tilt"] == "positive"
    assert t["importers_retail"]["score"] == 0.0 and t["importers_retail"]["tilt"] == "neutral"
    assert t["utilities_energy"]["tilt"] == "slightly_positive"
    assert all(v["kind"] == "historical_tendency" for v in t.values())


def test_sector_tilts_tightening_strong_pln_and_unknown():
    t = me.sector_tilts({"state": "tightening"}, {"trend": "rising"}, {"sign": "positive"}, {"regime": "strong"}, 30.0)
    assert t["banks"]["tilt"] == "positive" and t["exporters"]["tilt"] == "negative"
    assert t["importers_retail"]["score"] == 0.0
    assert t["utilities_energy"]["tilt"] == "slightly_negative"
    u = me.sector_tilts(None, None, None, None, None)
    assert all(v["tilt"] == "unknown" and v["score"] is None for v in u.values())


def test_sector_tilts_market_check():
    perf = {"WIG-BANKI": {"vs_wig_3m_pp": 5.0}, "WIG-INFO": {"vs_wig_3m_pp": -2.0}, "WIG-GRY": {"vs_wig_3m_pp": 4.0}}
    t = me.sector_tilts({"state": "easing"}, None, None, {"regime": "weak"}, None, perf)
    assert t["banks"]["market_check"] == {"vs_wig_3m_pp": 5.0, "agrees_with_tilt": False}
    assert t["exporters"]["market_check"]["vs_wig_3m_pp"] == 1.0
    assert t["exporters"]["market_check"]["agrees_with_tilt"] is True
    assert t["developers"]["market_check"]["vs_wig_3m_pp"] is None


# ---------------------------------------------------------------- equity side

def test_index_perf_and_wig_usd():
    wig = daily(D(2025, 9, 1), 200, lambda i: 100000 * (1 + 0.001 * i))
    banks = daily(D(2025, 9, 1), 200, lambda i: 20000 * (1 + 0.002 * i))
    perf = me.index_perf({"WIG": wig, "WIG-BANKI": banks}, wig[-1][0])
    assert perf["WIG-BANKI"]["vs_wig_3m_pp"] > 0
    assert perf["WIG"]["vs_wig_3m_pp"] == 0
    usd = daily(D(2025, 9, 1), 200, lambda i: 4.0)
    w = me.wig_in_usd(wig, usd, wig[-1][0])
    assert w["value"] == pytest.approx(wig[-1][1] / 4.0, rel=1e-6)
    assert w["trend"] == "rising" and w["change_12m_pct"] is None
    assert me.wig_in_usd(wig[:30], usd, wig[-1][0]) is None


# ---------------------------------------------------------------- history / diff / watch

def test_history_length_and_point_in_time():
    as_of = D(2026, 11, 16)
    eur = daily(D(2023, 1, 2), 1000, lambda i: 4.3)
    usd = daily(D(2023, 1, 2), 1000, lambda i: 3.8)
    h = hicp([3.0 + 0.05 * i for i in range(40)], start="2023-08")
    rows = me.history(24, as_of, RATES, h, eur, usd, [])
    assert len(rows) == 24 and rows[0]["month"] == "2024-12" and rows[-1]["month"] == "2026-11"
    assert rows[-1]["date"] == "2026-11-16"
    assert all(r["pln"] == "neutral" for r in rows)
    assert all(r["spread"] is None for r in rows)
    may = [r for r in rows if r["month"] == "2025-05"][0]
    assert may["monetary"] == "easing" and may["rate"] == 5.25


def test_changes_since_previous():
    prev = {"macro": {"state": {"monetary": "easing", "pln": "neutral", "composite": "supportive"}, "score": 0.5}}
    cur = {"monetary": "hold_after_easing", "pln": "neutral", "composite": "mixed", "spread": "stable"}
    d = me.changes_since(prev, cur, 0.1)
    fields = {c["field"]: (c["from"], c["to"]) for c in d["changes"]}
    assert fields == {"monetary": ("easing", "hold_after_easing"), "composite": ("supportive", "mixed")}
    assert d["score_change"] == pytest.approx(-0.4)
    assert me.changes_since(None, cur, 0.1) is None


def test_watch_items():
    mon = {"state": "hold_after_easing", "rate": 3.75}
    infl = {"value": 4.0, "trend": "rising", "band": "above", "period": "2026-09"}
    real = {"sign": "negative", "value": -0.25}
    eur = {"dev_sma200_pct": 1.2, "sma200": 4.3}
    usd = {"dev_sma200_pct": 7.0, "sma200": 3.7}
    items = me.watch_items(mon, infl, real, {"vol_elevated": True}, eur, usd, {"trend": "stable", "change_3m_bp": 12},
                           {"shape": "inverted", "value_bp": -5})
    ids = [i["id"] for i in items]
    assert ids == ["hawkish_risk", "negative_real_rate", "fx_near_threshold", "fx_vol", "spread_near_threshold",
                   "curve_inverted", "next_cpi"]
    assert items[2]["pair"] == "EURPLN" and items[-1]["period"] == "2026-10"


def test_month_helpers():
    assert me.month_add("2026-11", 2) == "2027-01"
    assert me.month_add("2026-01", -1) == "2025-12"
    assert me.month_end("2024-02") == D(2024, 2, 29)
