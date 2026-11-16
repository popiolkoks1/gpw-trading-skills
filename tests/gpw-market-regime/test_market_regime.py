"""Tests for gpw-market-regime: distribution days, FTD rules, state machine,
top-risk score and the CLI/JSON contract. Synthetic data only, no network."""

import datetime as dt
import json

import pytest

import regime_engine as rg
import gpw_market_regime as cli


# --------------------------------------------------------------------------
# Synthetic index builder
# --------------------------------------------------------------------------

def _dates(n, start=dt.date(2024, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


class B:
    """Sequential builder: add(ret, turnover)."""

    def __init__(self, c0=1000.0, t0=1000.0):
        self.close, self.turn = [c0], [t0]

    def add(self, ret, turn):
        self.close.append(self.close[-1] * (1 + ret))
        self.turn.append(turn)
        return self

    def up_base(self, n, up=0.006, down=-0.0025):
        """Alternating up/down days, down days on lower turnover (no distribution)."""
        for i in range(n):
            if i % 2 == 0:
                self.add(up, 1100.0)
            else:
                self.add(down, 900.0)
        return self

    def ix(self, name="WIG", turnover=True):
        return rg.IndexData(name, _dates(len(self.close)), self.close, None, None,
                            self.turn if turnover else [None] * len(self.close))

    @property
    def last(self):
        return len(self.close) - 1


def _events(sm):
    return [(d["date"], d["event"]) for d in sm["daily"] if "event" in d]


def _event_idx(sm, ix, name):
    for d in sm["daily"]:
        if d.get("event") == name:
            return ix.dates.index(d["date"])
    return None


# --------------------------------------------------------------------------
# Distribution days
# --------------------------------------------------------------------------

def test_distribution_day_definition():
    b = B().add(0.01, 1000).add(-0.002, 1100)      # exactly -0.2% on higher turnover -> DD
    b.add(-0.0019, 1200)                             # -0.19% -> no
    b.add(-0.01, 1100)                               # lower turnover -> no
    b.add(-0.01, 1100)                               # equal turnover -> no
    b.add(0.01, 2000)                                # up day -> no
    flags = rg.distribution_flags(b.close, b.turn, "turnover")
    assert flags == [False, False, True, False, False, False, False]


def test_distribution_missing_turnover_not_counted_and_price_only_mode():
    close = [100, 99, 98, 97]
    flags = rg.distribution_flags(close, [1000, None, 1200, 0], "turnover")
    assert flags == [False, False, False, False]
    assert rg.distribution_flags(close, [None] * 4, "price_only") == [False, True, True, True]
    vm = rg.volume_mode([1, None, None, 2, 3])
    assert vm["mode"] == "price_only" and vm["missing_sessions"] == 2
    assert rg.volume_mode([1, 2, 3, 4, 5, None])["mode"] == "turnover"  # 83% coverage


def test_distribution_expires_after_25_sessions():
    b = B().up_base(10).add(-0.01, 5000)           # DD
    d = b.last
    for _ in range(30):
        b.add(0.0, 100)                              # flat, no gain, no new DD
    ix = b.ix()
    assert ix.flags[d]
    assert rg.active_distribution(ix.dates, ix.close, ix.flags, d + 24)["count"] == 1
    assert rg.active_distribution(ix.dates, ix.close, ix.flags, d + 24)["days"][0]["expires_in_sessions"] == 1
    assert rg.active_distribution(ix.dates, ix.close, ix.flags, d + 25)["count"] == 0


def test_distribution_expires_after_5pct_gain():
    b = B().up_base(10).add(-0.01, 5000)
    d = b.last
    b.add(0.049, 100)                                # +4.9% from DD close -> still active
    ix = b.ix()
    assert rg.active_distribution(ix.dates, ix.close, ix.flags, b.last)["count"] == 1
    b.add(0.002, 90)                                 # now >= +5%
    ix = b.ix()
    res = rg.active_distribution(ix.dates, ix.close, ix.flags, b.last)
    assert res["count"] == 0 and res["expired_by_gain"] == [ix.dates[d]]
    # stays expired even if the index falls back below +5%
    b.add(-0.03, 80)
    ix = b.ix()
    assert ix.dates[d] in rg.active_distribution(ix.dates, ix.close, ix.flags, b.last)["expired_by_gain"]


def test_dd_bands():
    assert [rg.dd_band(n) for n in (0, 3, 4, 5, 6, 7, 8, 12)] == [
        "NORMAL", "NORMAL", "CAUTION", "CAUTION", "HIGH", "HIGH", "SEVERE", "SEVERE"]


def test_expiry_day_detection():
    assert rg.is_expiry_day("2026-03-20")       # third Friday of March 2026
    assert rg.is_expiry_day("2026-12-18")
    assert not rg.is_expiry_day("2026-03-13")
    assert not rg.is_expiry_day("2026-04-17")  # April is not an expiry month


# --------------------------------------------------------------------------
# Volatility-scaled thresholds
# --------------------------------------------------------------------------

def test_thresholds_scale_and_clamp():
    th = rg.thresholds(1.10)
    assert th["ftd_gain_pct"] == 1.25 and th["correction_pct"] == 8.0 and th["bear_pct"] == 20.0
    th = rg.thresholds(1.32)                       # 1.2x S&P
    assert th["ftd_gain_pct"] == 1.5 and th["correction_pct"] == 9.6
    hi, lo = rg.thresholds(3.0), rg.thresholds(0.3)
    assert hi["ftd_gain_pct"] == 2.2 and hi["correction_pct"] == 12.0 and hi["bear_pct"] == 25.0
    assert lo["ftd_gain_pct"] == 0.8 and lo["correction_pct"] == 6.0 and lo["bear_pct"] == 15.0
    assert rg.thresholds(None)["ftd_gain_pct"] == 1.25   # not enough history -> unscaled


def test_rolling_sigma_needs_min_obs():
    rets = rg.daily_returns([100 + (i % 2) for i in range(40)])
    assert rg.rolling_sigma_pct(rets, 39) is None
    rets = rg.daily_returns([100 + (i % 2) for i in range(100)])
    assert rg.rolling_sigma_pct(rets, 99) == pytest.approx(1.0, rel=0.05)


# --------------------------------------------------------------------------
# State machine: corrections, rally attempts, follow-through days
# --------------------------------------------------------------------------

def _corrected(days=10, step=-0.013):
    """Uptrend, then a decline on falling turnover (no distribution)."""
    b = B().up_base(260)
    for _ in range(days):
        b.add(step, 900.0)
    return b


def test_uptrend_is_confirmed():
    ix = B().up_base(300).ix()
    sm = rg.run_state_machine(ix)
    assert sm["daily"][-1]["state"] == "confirmed_uptrend"
    assert ix.dd_counts[-1] == 0


def test_correction_on_drawdown():
    b = _corrected()
    ix = b.ix()
    sm = rg.run_state_machine(ix)
    assert ix.drawdown_pct(b.last) >= ix.thr(b.last)["correction_pct"]
    assert sm["daily"][-1]["state"] == "correction"
    assert "drawdown" in [e for _, e in _events(sm)]


def test_follow_through_day_on_day_4():
    b = _corrected()
    b.add(0.005, 900).add(0.003, 900).add(-0.002, 850)   # days 1-3 (day 3 above the low)
    b.add(0.02, 1500)                                     # day 4: +2% on higher turnover
    ix = b.ix()
    sm = rg.run_state_machine(ix)
    assert sm["daily"][-2]["state"] == "rally_attempt"
    assert sm["daily"][-1]["event"] == "follow_through_day"
    assert sm["daily"][-1]["state"] in ("confirmed_uptrend", "uptrend_under_pressure")
    f = rg.ftd_health(ix, sm["ftds"][-1])
    assert f["day_number"] == 4 and f["gain_pct"] == 2.0 and f["volume_confirmed"]
    assert f["turnover_change_pct"] == pytest.approx(76.5, abs=0.1)
    assert f["health"] == "healthy" and f["sessions_since"] == 0


def test_no_ftd_before_day_4():
    b = _corrected()
    b.add(0.005, 900).add(0.003, 900).add(0.025, 1500)   # big day 3 does not count
    ix = b.ix()
    sm = rg.run_state_machine(ix)
    assert sm["daily"][-1]["state"] == "rally_attempt" and not sm["ftds"]
    b.add(0.001, 800).add(0.02, 1600)                     # day 5 qualifies
    sm = rg.run_state_machine(b.ix())
    assert sm["ftds"] and sm["ftds"][-1]["day_number"] == 5


def test_no_ftd_without_higher_turnover_or_big_enough_gain():
    b = _corrected()
    b.add(0.005, 900).add(0.003, 900).add(0.002, 900)
    b.add(0.02, 800)                                      # lower turnover
    ix = b.ix()
    assert not rg.run_state_machine(ix)["ftds"]
    thr = ix.thr(b.last)["ftd_gain_pct"]
    b.add((thr - 0.1) / 100, 2000)                        # higher turnover, gain below threshold
    assert not rg.run_state_machine(b.ix())["ftds"]
    b.add((thr + 0.2) / 100, 3000)
    sm = rg.run_state_machine(b.ix())
    assert sm["ftds"] and sm["ftds"][-1]["day_number"] == 6


def test_ftd_in_price_only_mode_is_flagged():
    b = _corrected()
    b.add(0.005, 900).add(0.003, 900).add(0.002, 900).add(0.02, 800)
    ix = b.ix(turnover=False)
    sm = rg.run_state_machine(ix)
    assert ix.vol["mode"] == "price_only"
    assert sm["ftds"] and sm["ftds"][-1]["volume_confirmed"] is False


def test_rally_attempt_fails_on_undercut_and_restarts():
    b = _corrected()
    low = b.close[-1]
    b.add(0.01, 900).add(-0.005, 800)
    b.add((low * 0.99) / b.close[-1] - 1, 800)            # close below the rally low
    ix = b.ix()
    sm = rg.run_state_machine(ix)
    assert sm["daily"][-1]["event"] == "rally_attempt_failed"
    assert sm["daily"][-1]["state"] == "correction"
    assert sm["failed_attempts"][-1]["days"] == 3
    b.add(0.004, 800)                                     # new attempt, day 1
    sm = rg.run_state_machine(b.ix())
    assert sm["daily"][-1]["state"] == "rally_attempt" and sm["attempt"]["day"] == 1
    assert sm["attempt"]["rally_low"] == pytest.approx(low * 0.99)


def test_down_day_above_low_does_not_end_attempt():
    b = _corrected()
    b.add(0.01, 900).add(-0.004, 800).add(-0.004, 800)    # still above the low
    sm = rg.run_state_machine(b.ix())
    assert sm["daily"][-1]["state"] == "rally_attempt" and sm["attempt"]["day"] == 3


def test_ftd_far_below_old_high_is_not_undone_by_drawdown_rule():
    # deep correction (~23%), FTD while still ~20% under the 52-week high:
    # the next ordinary down day must not flip the regime back to correction
    b = _corrected(days=20, step=-0.013)
    b.add(0.005, 900).add(0.003, 900).add(0.002, 900).add(0.02, 1500)
    b.add(-0.003, 800).add(0.004, 900)
    ix = b.ix()
    sm = rg.run_state_machine(ix)
    assert ix.drawdown_pct(b.last) > ix.thr(b.last)["correction_pct"]
    assert sm["daily"][-1]["phase"] == "UP"
    assert sm["ftds"][-1]["day_number"] == 4


def test_ftd_failure_back_to_correction():
    b = _corrected()
    low = b.close[-1]
    b.add(0.005, 900).add(0.003, 900).add(0.002, 900).add(0.02, 1500)
    b.add(-0.01, 800).add(-0.01, 700)
    b.add((low * 0.995) / b.close[-1] - 1, 600)            # below the rally low
    ix = b.ix()
    sm = rg.run_state_machine(ix)
    assert sm["daily"][-1]["event"] == "ftd_failed"
    assert sm["daily"][-1]["state"] == "correction"
    assert rg.ftd_health(ix, sm["ftds"][-1])["health"] == "failed"


def test_close_below_ftd_day_low_means_under_pressure():
    b = _corrected()
    b.add(0.005, 900).add(0.003, 900).add(0.002, 900).add(0.02, 1500)
    b.add(-0.012, 800)                                     # below the FTD day's low, above the rally low
    ix = b.ix()
    sm = rg.run_state_machine(ix)
    assert sm["daily"][-1]["state"] == "uptrend_under_pressure"
    assert "below_ftd_low" in sm["daily"][-1]["why"]
    assert rg.ftd_health(ix, sm["ftds"][-1])["health"] == "under_pressure"


def test_new_high_without_ftd_ends_correction():
    b = _corrected()
    t = 890.0
    while b.close[-1] < max(b.close[:-1]) * 1.001:
        t -= 1
        b.add(0.005, t)                                    # grind up on falling turnover, gain < FTD threshold
    sm = rg.run_state_machine(b.ix())
    assert not sm["ftds"]
    assert sm["daily"][-1]["event"] == "new_high_without_ftd"
    assert sm["daily"][-1]["phase"] == "UP"


def test_bear_on_deep_drawdown():
    b = _corrected(days=25, step=-0.015)                   # ~31% fall
    ix = b.ix()
    sm = rg.run_state_machine(ix)
    assert ix.drawdown_pct(b.last) > 25
    assert sm["daily"][-1]["state"] == "bear"
    assert "drawdown_bear" in sm["daily"][-1]["why"]
    b.add(0.01, 900)                                       # a rally attempt inside a bear stays 'bear'
    sm = rg.run_state_machine(b.ix())
    assert sm["daily"][-1]["state"] == "bear" and sm["attempt"]["day"] == 1


def test_bear_structure_without_deep_drawdown():
    # long slow decline: below a falling SMA200, SMA50 < SMA200, but drawdown kept moderate
    b = B().up_base(220)
    for i in range(200):
        b.add(-0.0012 if i % 2 == 0 else 0.0006, 900 if i % 2 == 0 else 1000)
    ix = b.ix()
    t = b.last
    assert ix.bear_structure(t)
    sm = rg.run_state_machine(ix)
    assert sm["daily"][-1]["state"] == "bear"
    assert "bear_structure" in sm["daily"][-1]["why"]


def _distribution_cluster(b, n):
    for _ in range(n):
        b.add(0.004, 1000).add(-0.003, 1200)               # each down day is a DD
    return b


def test_under_pressure_on_distribution_and_hysteresis():
    b = _distribution_cluster(B().up_base(260), 6)
    ix = b.ix()
    sm = rg.run_state_machine(ix)
    assert ix.dd_counts[b.last] == 6
    assert sm["daily"][-1]["state"] == "uptrend_under_pressure"
    assert "distribution_high" in sm["daily"][-1]["why"]
    # let the cluster expire: the state must stay 'under pressure' at 5, recover at <= 4
    b.up_base(30)
    ix = b.ix()
    sm = rg.run_state_machine(ix)
    by_date = {d["date"]: d for d in sm["daily"]}
    first6 = next(t for t in range(len(ix)) if ix.dd_counts[t] == 6)
    seen5 = [by_date[ix.dates[t]] for t in range(first6, len(ix)) if ix.dd_counts[t] == 5]
    assert seen5 and all(d["state"] == "uptrend_under_pressure" for d in seen5)
    assert seen5[0]["why"] == ["not_yet_recovered"]
    assert sm["daily"][-1]["state"] == "confirmed_uptrend"
    tos = [tr["to"] for tr in sm["transitions"]]
    assert tos[-2:] == ["uptrend_under_pressure", "confirmed_uptrend"]


def test_one_close_below_sma50_is_not_pressure():
    b = B().up_base(260)
    ix = b.ix()
    drop = ix.sma50[b.last] * 0.995 / b.close[-1] - 1
    b.add(drop, 800)                                       # one close below SMA50, lower turnover
    sm = rg.run_state_machine(b.ix())
    assert sm["daily"][-1]["state"] == "confirmed_uptrend"
    ix = b.ix()
    b.add(ix.sma50[b.last] * 0.995 / b.close[-1] - 1, 700)  # second close below
    sm = rg.run_state_machine(b.ix())
    assert sm["daily"][-1]["state"] == "uptrend_under_pressure"
    assert "below_sma50" in sm["daily"][-1]["why"]


def test_correction_from_distribution_below_sma50():
    # flat-ish uptrend (SMA50 close to price), then heavy distribution
    b = B().up_base(260, up=0.005, down=-0.0045)
    t = 1300.0
    for _ in range(8):
        t += 100
        b.add(-0.006, t)                                   # consecutive DDs, each on higher turnover
    ix = b.ix()
    sm = rg.run_state_machine(ix)
    evs = [e for _, e in _events(sm)]
    assert "distribution_below_sma50" in evs
    i = _event_idx(sm, ix, "distribution_below_sma50")
    assert ix.dd_counts[i] >= 6 and ix.close[i] < ix.sma50[i]
    assert ix.drawdown_pct(i) < ix.thr(i)["correction_pct"]
    assert sm["daily"][-1]["state"] in ("correction", "rally_attempt")


def test_state_summary_and_transitions_shape():
    b = _corrected()
    b.add(0.005, 900).add(0.003, 900).add(0.002, 900).add(0.02, 1500)
    ix = b.ix()
    a = rg.analyze_index(ix, 250)
    assert a["state"] in rg.STATES
    share = a["state_share"]["share_pct"]
    assert sum(share.values()) == pytest.approx(100, abs=0.5)
    assert a["transitions"][-1]["event"] == "follow_through_day"
    assert {"date", "from", "to", "event", "dd_count", "drawdown_pct"} <= set(a["transitions"][-1])
    assert a["state_before_history"] is None          # window reaches back to the model start
    assert rg.analyze_index(ix, 20)["state_before_history"] in rg.STATES


def test_state_machine_needs_history():
    with pytest.raises(ValueError):
        rg.run_state_machine(B().up_base(30).ix())


# --------------------------------------------------------------------------
# Leadership and top risk
# --------------------------------------------------------------------------

def _trend(n, step):
    b = B()
    for _ in range(n):
        b.add(step, 1000)
    return b.ix


def test_leadership_large_caps_lead():
    w20 = _trend(100, 0.002)("WIG20")        # +12.7% over 60
    s80 = _trend(100, -0.001)("SWIG80")      # -5.8% over 60
    lead = rg.leadership(w20, s80)
    assert lead["pattern"] == "large_caps_lead"
    assert lead["w60"]["spread_pp"] > 15 and lead["risk_score"] >= 90
    lead = rg.leadership(s80, w20)
    assert lead["pattern"] == "small_caps_lead" and lead["risk_score"] == 0
    assert rg.leadership(None, s80) == {"available": False}


def test_top_risk_components_and_renormalisation():
    ix = B().up_base(300).ix()
    lead = {"available": False}
    r = rg.top_risk(ix, lead)
    assert r["missing"] == ["leadership", "breadth"]
    assert sum(c["weight"] for c in r["components"].values()) == pytest.approx(1.0, abs=0.01)
    assert r["score"] < 5 and r["band"] == "low"
    breadth = {"composite": {"score": 20}, "components": {"index_divergence": {"score": 10, "data_available": True,
                                                                              "signal_key": "c6_bearish_divergence"}}}
    r2 = rg.top_risk(ix, lead, breadth)
    assert r2["components"]["breadth"]["score"] == 85.0
    assert r2["score"] > r["score"]


def test_top_risk_high_in_bear():
    b = _corrected(days=25, step=-0.015)
    ix = b.ix()
    r = rg.top_risk(ix, {"available": False})
    assert r["components"]["drawdown"]["score"] == 100
    assert "below_sma50" in r["components"]["trend"]["value"]
    assert 0 <= r["score"] <= 100


def test_breadth_risk_handles_partial_input():
    assert rg.breadth_risk(None) is None
    assert rg.breadth_risk({"composite": {}}) is None
    assert rg.breadth_risk({"composite": {"score": 40}})["risk_score"] == 60.0


# --------------------------------------------------------------------------
# CLI / JSON contract
# --------------------------------------------------------------------------

def _sessions(n=300, crash=False):
    """TYPE_INDICES-like payloads for the four indices."""
    builders = {k: B(c0=c0).up_base(n - 1) for k, c0 in
                (("WIG", 80000.0), ("WIG20", 2500.0), ("MWIG40", 6000.0), ("SWIG80", 24000.0))}
    if crash:
        for b in builders.values():
            for _ in range(12):
                b.add(-0.012, 900)
    dates = _dates(len(builders["WIG"].close))
    out = []
    for i, d in enumerate(dates):
        rows = [{"name": k, "currency": "", "open": b.close[i], "high": b.close[i], "low": b.close[i],
                 "close": b.close[i], "change_pct": 0.0, "turnover_k": b.turn[i]} for k, b in builders.items()]
        out.append({"date": d, "rows": rows})
    return out


def test_build_report_contract(tmp_path):
    rep = cli.build_report(_sessions(), "WIG", 250, None, "en")
    g = rep["regime"]
    assert rep["skill"] == "gpw-market-regime" and rep["version"] == "1.0.0"
    assert g["state"] in rg.STATES
    assert isinstance(g["top_risk_score"], float) and 0 <= g["top_risk_score"] <= 100
    assert isinstance(g["distribution_days"], int)
    dt.date.fromisoformat(g["as_of"])
    assert set(rep["indices"]) == {"WIG", "WIG20", "MWIG40", "SWIG80"}
    assert rep["breadth_input"] == {"used": False, "reason": "missing"}
    json.dumps(rep)  # serialisable, no internal objects left
    assert "_sm" not in rep["indices"]["WIG"]
    for lang in ("pl", "en"):
        md = cli.render_markdown(rep, lang)
        assert cli.DISCLAIMER[lang] in md
        assert "WIG" in md


def test_build_report_correction_and_markdown():
    rep = cli.build_report(_sessions(crash=True), "WIG", 250, None, "pl")
    assert rep["regime"]["state"] == "correction"
    md = cli.render_markdown(rep, "pl")
    assert "korekta" in md and "Historia reżimu" in md


def test_breadth_input_used_stale_and_future(tmp_path):
    sess = _sessions()
    last = sess[-1]["date"]
    old = sess[-10]["date"]
    p = tmp_path / "gpw_breadth_2026-01-01_000000.json"
    p.write_text(json.dumps({"skill": "gpw-market-breadth", "composite": {"score": 30},
                             "components": {"index_divergence": {"score": 10, "data_available": True}},
                             "data_provenance": {"as_of": old}}), encoding="utf-8")
    rep = cli.build_report(sess, "WIG", 250, p, "en")
    assert rep["breadth_input"]["used"] and rep["breadth_input"]["stale"]
    assert rep["breadth_input"]["sessions_behind"] == 9
    assert "breadth" in rep["top_risk"]["components"]
    future = (dt.date.fromisoformat(last) + dt.timedelta(days=7)).isoformat()
    p.write_text(json.dumps({"skill": "gpw-market-breadth", "composite": {"score": 30},
                             "data_provenance": {"as_of": future}}), encoding="utf-8")
    rep = cli.build_report(sess, "WIG", 250, p, "en")
    assert rep["breadth_input"]["used"] is False and rep["breadth_input"]["reason"] == "future"
    assert "breadth" not in rep["top_risk"]["components"]


def test_breadth_input_wrong_skill(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"skill": "gpw-sector-trend"}), encoding="utf-8")
    with pytest.raises(cli.gpw_archive.GPWFetchError):
        cli.build_report(_sessions(), "WIG", 250, p, "en")


def test_find_input_newest(tmp_path):
    for s in ("2026-11-01_100000", "2026-11-13_090000", "2026-11-05_120000"):
        (tmp_path / "gpw_breadth_{}.json".format(s)).write_text("{}")
    assert cli.find_input(None, str(tmp_path), "gpw_breadth_*.json").name == "gpw_breadth_2026-11-13_090000.json"
    assert cli.find_input(None, str(tmp_path / "nope"), "gpw_breadth_*.json") is None
    with pytest.raises(cli.gpw_archive.GPWFetchError):
        cli.find_input(str(tmp_path / "missing.json"), ".", "gpw_breadth_*.json")


def test_missing_primary_index():
    sess = [{"date": s["date"], "rows": [r for r in s["rows"] if r["name"] != "WIG"]} for s in _sessions()]
    with pytest.raises(cli.gpw_archive.GPWFetchError):
        cli.build_report(sess, "WIG", 250, None, "en")
    rep = cli.build_report(sess, "WIG20", 250, None, "en")
    assert rep["regime"]["index"] == "WIG20" and "WIG" in rep["skipped_indices"]


@pytest.mark.parametrize("argv", [["--sessions", "100"], ["--as-of", "2026-13-01"], ["--lang", "de"],
                                  ["--index", "WIG30"], ["--history-sessions", "5"]])
def test_cli_argument_errors(argv):
    with pytest.raises(SystemExit) as e:
        cli.parse_args(argv)
    assert e.value.code == 2


def test_cli_offline_empty_cache_exits_1(tmp_path, capsys):
    rc = cli.main(["--offline", "--cache-dir", str(tmp_path), "--output-dir", str(tmp_path), "--input-dir", str(tmp_path)])
    assert rc == 1
    assert "ERROR" in capsys.readouterr().err
