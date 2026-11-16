"""Unit tests for the gpw-earnings-reaction engine and CLI (no network)."""

import datetime as dt
import json
import math
import random
from pathlib import Path

import pytest

import earnings_reaction_engine as ere
import gpw_earnings_reaction as cli
from gpwlib import espi, plcal

FIX = Path(__file__).resolve().parent / "fixtures"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def sessions_between(start, end):
    d, out = start, []
    while d <= end:
        if plcal.is_probable_session(d):
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


KNOWN = sessions_between(dt.date(2025, 9, 1), dt.date(2026, 11, 13))


def synthetic(n=320, event_at=None, jump=0.0, beta=1.0, noise=0.01, seed=7, market_noise=0.008,
              turnover=100.0, event_turnover=None, drift=0.0):
    """Stock = beta * market + noise; optional jump at index event_at (abnormal),
    optional daily abnormal drift after event_at+2."""
    rng = random.Random(seed)
    dates = KNOWN[-n:]
    m = [1000.0]
    c = [50.0]
    for k in range(1, n):
        rm = rng.gauss(0, market_noise)
        ri = beta * rm + rng.gauss(0, noise)
        if event_at is not None and k == event_at:
            ri += jump
        if event_at is not None and event_at + 3 <= k:
            ri += drift
        m.append(m[-1] * (1 + rm))
        c.append(c[-1] * (1 + ri))
    hi = [x * 1.01 for x in c]
    lo = [x * 0.99 for x in c]
    tv = [turnover] * n
    if event_at is not None and event_turnover is not None:
        tv[event_at] = event_turnover
    return dates, c, hi, lo, tv, dict(zip(dates, m))


# --------------------------------------------------------------------------
# session alignment
# --------------------------------------------------------------------------

@pytest.mark.parametrize("stamp,expected_day,alignment", [
    ("2026-11-12T07:30:00", "2026-11-12", "pre_open"),
    ("2026-11-12T08:59:59", "2026-11-12", "pre_open"),
    ("2026-11-12T09:00:00", "2026-11-12", "intraday"),
    ("2026-11-12T17:04:59", "2026-11-12", "intraday"),
    ("2026-11-12T17:05:00", "2026-11-13", "after_close"),
    ("2026-11-12T22:10:00", "2026-11-13", "after_close"),
    ("2026-11-13T18:00:00", "2026-11-16", "after_close"),   # Friday evening -> Monday
    ("2026-11-14T10:00:00", "2026-11-16", "non_session"),   # Saturday
    ("2026-11-15T07:00:00", "2026-11-16", "non_session"),   # Sunday before open
    ("2026-11-10T19:00:00", "2026-11-12", "after_close"),   # 11 Nov = Independence Day
    ("2026-11-11T08:00:00", "2026-11-12", "non_session"),   # holiday itself
])
def test_align_event(stamp, expected_day, alignment):
    a = ere.align_event(dt.datetime.fromisoformat(stamp), KNOWN, plcal.is_probable_session)
    assert a["reaction_date"] == expected_day
    assert a["alignment"] == alignment
    assert a["intraday"] == (alignment == "intraday")


def test_archive_is_authoritative_over_calendar():
    # a weekday missing from the archive (no session) is skipped even if the calendar says session
    known = [d for d in KNOWN if d != "2026-11-12"]
    a = ere.align_event(dt.datetime(2026, 11, 10, 20, 0), known, plcal.is_probable_session)
    assert a["reaction_date"] == "2026-11-13"


def test_alignment_beyond_archive_uses_calendar():
    a = ere.align_event(dt.datetime(2026, 12, 23, 18, 0), KNOWN, plcal.is_probable_session)
    # 24, 25, 26 Dec closed, 27 Sunday -> Monday 28 Dec
    assert a["reaction_date"] == "2026-12-28"


def test_easter_holidays():
    known = sessions_between(dt.date(2027, 3, 1), dt.date(2027, 4, 30))
    # Easter 2027: 28 March; Good Friday 26 March, Easter Monday 29 March
    a = ere.align_event(dt.datetime(2027, 3, 25, 18, 0), known)
    assert a["reaction_date"] == "2027-03-30"


# --------------------------------------------------------------------------
# periods and deadlines
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cls,day,title,key", [
    ("periodic_q", "2026-11-13", "", "2026Q3"),
    ("periodic_q", "2026-05-20", "", "2026Q1"),
    ("periodic_q", "2026-02-27", "", "2025Q4"),
    ("periodic_h", "2026-09-10", "", "2026H1"),
    ("periodic_y", "2026-04-28", "", "2025Q4"),
    ("preliminary_results", "2026-10-20", "Wstępne wybrane dane finansowe za III kwartał 2026 r.", "2026Q3"),
    ("preliminary_results", "2026-10-02", "Szacunkowe wyniki za 3Q 2026", "2026Q3"),
    ("preliminary_results", "2026-07-15", "Preliminary results Q2 2026", "2026H1"),
    ("preliminary_results", "2026-01-28", "Wstępne wyniki za 2025 rok", "2025Q4"),
    ("preliminary_results", "2026-11-03", "Szacunkowe wyniki Grupy za III kwartały 2026 roku", "2026Q3"),
    # title naming a future period is ignored (sanity rule)
    ("preliminary_results", "2026-10-02", "Wstępne dane za IV kw. 2026", "2026Q3"),
])
def test_infer_period(cls, day, title, key):
    assert ere.infer_period(cls, dt.date.fromisoformat(day), title)["key"] == key


def test_kwiecien_not_a_quarter():
    # "1 kwietnia 2026" must not be read as Q1
    p = ere.infer_period("preliminary_results", dt.date(2026, 7, 20), "dane opublikowane 1 kwietnia 2026")
    assert p["key"] == "2026H1" and p["source"] == "publication_month"


def test_statutory_deadlines_and_next_period():
    # Dz.U. 2025 poz. 755 § 83 ust. 11: weekend / holiday -> next working day
    assert ere.statutory_deadline("2026Q3") == "2026-11-30"   # nominal 29.11.2026 = Sunday
    assert ere.statutory_deadline("2026Q1") == "2026-06-01"   # nominal 30.05.2026 = Saturday
    assert ere.statutory_deadline("2025Q1") == "2025-05-30"   # Friday, unchanged
    assert ere.statutory_deadline("2027Q4") == "2028-05-02"   # 30.04.2028 Sunday, 1.05 holiday
    assert ere.statutory_deadline("2026H1") == "2026-09-30"
    assert ere.statutory_deadline("2025Q4") == "2026-04-30"
    assert ere.statutory_deadline("bad") is None
    assert ere.next_period_key("2026Q3") == "2026Q4"
    assert ere.next_period_key("2026Q4") == "2027Q1"
    assert ere.season_for(dt.date(2026, 11, 16))["key"] == "2026Q3"
    assert ere.season_for(dt.date(2027, 2, 1))["key"] == "2026Q4"


# --------------------------------------------------------------------------
# events from ESPI fixtures
# --------------------------------------------------------------------------

def _fixture_rows(name):
    return espi.parse_list_html((FIX / name).read_text(encoding="utf-8"))


def test_build_events_synthetic_fixture():
    rows = _fixture_rows("espi_list_synthetic.html")
    imap = {"PLALFA000016": "ALFA", "PLBETA000019": "BETA", "PLWKND000013": "WKND"}
    built = ere.build_events(rows, imap, KNOWN, plcal.is_probable_session)
    ev = {e["ticker"]: e for e in built["events"]}
    # corrections ignored, AGM call not an event
    assert built["counts"]["corrections_ignored"] == 1
    assert built["counts"]["other_classes"] == 1
    # preliminary results precede the report -> they are the information event
    a = ev["ALFA"]
    assert a["information_event"] == "preliminary_results"
    assert a["preliminary_first"] is True
    assert a["report_type"] == "periodic_q"
    assert a["reaction_date"] == "2026-10-21"          # 17:40 -> next session
    assert a["session_alignment"] == "after_close"
    assert a["periodic_report"]["date"] == "2026-11-13"
    assert len(a["filings"]) == 2                      # correction not attached
    # intraday report
    assert ev["BETA"]["session_alignment"] == "intraday" and ev["BETA"]["reaction_date"] == "2026-11-12"
    # Saturday report -> Monday
    assert ev["WKND"]["reaction_date"] == "2026-11-16" and ev["WKND"]["session_alignment"] == "non_session"
    # unmapped issuer reported, not dropped silently
    assert built["unmapped"][0]["isin"] == "PLNONE000010"
    assert built["counts"]["unmapped_rows"] == 1


def test_prelim_too_far_before_report_uses_report():
    rows = [
        {"datetime": "2026-07-01T08:00:00", "class": "preliminary_results", "correction": False, "isin": "PLX",
         "title": "Wstępne wyniki za III kw. 2026", "company": "X"},
        {"datetime": "2026-11-20T08:00:00", "class": "periodic_q", "correction": False, "isin": "PLX", "title": "", "company": "X"},
    ]
    # the July preliminary names Q3 2026 (in the future) -> month rule makes it H1, so two groups
    built = ere.build_events(rows, {"PLX": "X"}, KNOWN, plcal.is_probable_session)
    assert {e["period"] for e in built["events"]} == {"2026H1", "2026Q3"}
    rows[0]["datetime"] = "2026-09-01T08:00:00"
    rows[0]["title"] = "Wstępne wyniki za III kwartał 2026"  # still future at 1 Sep -> H1
    rows.append({"datetime": "2026-10-01T08:00:00", "class": "preliminary_results", "correction": False, "isin": "PLX",
                 "title": "Wstępne wyniki za III kwartał 2026", "company": "X"})
    built = ere.build_events(rows, {"PLX": "X"}, KNOWN, plcal.is_probable_session, dict(ere.DEFAULTS, prelim_merge_days=30))
    q3 = [e for e in built["events"] if e["period"] == "2026Q3"][0]
    # preliminary on 1 Oct, report on 20 Nov = 50 days > 30 -> the report is the event
    assert q3["information_event"] == "periodic_q" and q3["report_date"] == "2026-11-20"


def test_real_espi_fixture_classes():
    rows = _fixture_rows("espi_list_2026-11-10.html")
    imap = {r["isin"]: r["company"][:6] for r in rows if r["isin"]}
    built = ere.build_events(rows, imap, KNOWN, plcal.is_probable_session)
    kinds = {e["information_event"] for e in built["events"]}
    assert kinds <= set(ere.EVENT_CLASSES)
    # 5 quarterly + 2 preliminary rows, the schedule notice (BOS) is not an event
    assert built["counts"]["kept"] == 7
    assert all(e["period"] == "2026Q3" for e in built["events"])
    after = [e for e in built["events"] if e["published_at"].endswith("22:03:56")][0]
    assert after["reaction_date"] == "2026-11-12"  # 10 Nov evening, 11 Nov holiday


# --------------------------------------------------------------------------
# CAR, abnormal turnover, beta, labels
# --------------------------------------------------------------------------

def test_car_exact_on_constructed_series():
    # market +1% every day, stock +1% every day except days 0..2 (+3%, +2%, +1%)
    n = 300
    dates = KNOWN[-n:]
    m, c = [100.0], [10.0]
    t0 = 270
    extra = {t0: 0.03, t0 + 1: 0.02, t0 + 2: 0.01}
    for k in range(1, n):
        m.append(m[-1] * 1.01)
        c.append(c[-1] * (1.01 + extra.get(k, 0.0) + (0.001 if k % 2 else -0.001)))
    tv = [100.0] * n
    tv[t0] = 300.0
    res = ere.event_metrics(dates, c, [x * 1.02 for x in c], [x * 0.98 for x in c], tv, dict(zip(dates, m)), dates[t0])
    assert res["status"] == "ok"
    # AR alternates +-0.1% outside the window; inside 3%+2%+1% plus the alternating term
    alt = sum(0.001 if k % 2 else -0.001 for k in (t0, t0 + 1, t0 + 2))
    assert res["car_3d_pct"] == pytest.approx((0.06 + alt) * 100, abs=0.01)
    assert res["abnormal_turnover"] == pytest.approx(3.0)
    assert res["day0_abnormal_pct"] == pytest.approx((0.03 + (0.001 if t0 % 2 else -0.001)) * 100, abs=0.01)
    assert res["sigma_obs"] == 221  # [-250,-30] inclusive
    assert res["sigma_daily_pct"] == pytest.approx(0.1, abs=0.002)
    assert res["label"] == "strong_positive"
    assert res["close_location"] == pytest.approx(0.5)
    assert res["drift_20s_status"] == "complete"
    assert res["drift_60s_status"] == "pending" and res["drift_60s_pct"] is None


def test_beta_estimation_recovers_true_beta():
    dates, c, hi, lo, tv, mk = synthetic(n=320, event_at=300, beta=1.6, noise=0.004, seed=3)
    res = ere.event_metrics(dates, c, hi, lo, tv, mk, dates[300])
    assert res["beta"] == pytest.approx(1.6, abs=0.15)
    assert res["beta_obs"] == 221
    # market-adjusted and market-model CAR are both reported
    assert res["car_3d_mm_pct"] is not None and res["z_mm"] is not None


def test_beta_needs_enough_observations():
    dates, c, hi, lo, tv, mk = synthetic(n=150, event_at=140, seed=4)
    res = ere.event_metrics(dates, c, hi, lo, tv, mk, dates[140])
    # [-250,-30] gives 110 obs -> sigma ok, beta ok (>=100)
    assert res["sigma_source"] == "estimation_window" and res["beta"] is not None
    dates, c, hi, lo, tv, mk = synthetic(n=150, event_at=100, seed=4)
    res = ere.event_metrics(dates, c, hi, lo, tv, mk, dates[100])
    assert res["beta"] is None and res["car_3d_mm_pct"] is None   # 70 obs < 100
    assert res["sigma_source"] == "estimation_window"


def test_short_window_and_insufficient_history():
    dates, c, hi, lo, tv, mk = synthetic(n=150, event_at=50, seed=5)
    res = ere.event_metrics(dates, c, hi, lo, tv, mk, dates[50])
    assert res["sigma_source"] == "short_window" and "short_estimation_window" in res["flags"]
    res = ere.event_metrics(dates, c, hi, lo, tv, mk, dates[20])
    assert res["status"] == "skipped" and res["reason"] == "insufficient_history"


def test_strong_negative_with_volume_and_unconfirmed_without():
    dates, c, hi, lo, tv, mk = synthetic(n=320, event_at=290, jump=-0.12, noise=0.01, seed=11, event_turnover=400.0)
    res = ere.event_metrics(dates, c, hi, lo, tv, mk, dates[290])
    assert res["z"] < -2 and res["label"] == "strong_negative"
    dates, c, hi, lo, tv, mk = synthetic(n=320, event_at=290, jump=-0.12, noise=0.01, seed=11, event_turnover=120.0)
    res = ere.event_metrics(dates, c, hi, lo, tv, mk, dates[290])
    assert res["label"] == "negative" and "turnover_unconfirmed" in res["flags"]


@pytest.mark.parametrize("z,at,label", [
    (2.5, 2.0, "strong_positive"),
    (-2.0, 1.5, "strong_negative"),
    (2.5, 1.2, "positive"),
    (2.5, 0.5, "neutral"),
    (2.5, None, "positive"),
    (1.2, 1.0, "positive"),
    (-1.2, 1.1, "negative"),
    (-1.2, 0.7, "neutral"),
    (-1.2, None, "negative"),
    (0.8, 5.0, "neutral"),
    (None, 3.0, "neutral"),
])
def test_label_table(z, at, label):
    assert ere.label_for(z, at)[0] == label


def test_gap_uses_reference_price():
    sessions = [{"date": "2026-11-12", "rows": [
        {"name": "A", "open": 105.0, "close": 110.0, "change_pct": 10.0},     # ref 100 -> +5%
        {"name": "B", "open": 0, "close": 10.0, "change_pct": 1.0},          # no open -> skipped
        {"name": "C", "open": 9.0, "close": 9.5, "change_pct": None},
    ]}]
    g = ere.gaps_from_sessions(sessions)
    assert g["A"]["2026-11-12"] == pytest.approx(5.0)
    assert "B" not in g and "C" not in g
    assert ere.gaps_from_sessions(sessions, names={"C"}) == {}


# --------------------------------------------------------------------------
# pending logic
# --------------------------------------------------------------------------

def test_pending_reaction_and_provisional_window():
    dates, c, hi, lo, tv, mk = synthetic(n=300, event_at=299, seed=6)
    assert ere.event_metrics(dates, c, hi, lo, tv, mk, "2026-11-16")["status"] == "pending"
    res = ere.event_metrics(dates, c, hi, lo, tv, mk, dates[-1])
    assert res["status"] == "ok" and res["car_window_days"] == 1 and res["provisional"]
    assert "provisional_window" in res["flags"]
    assert res["drift_20s_pct"] is None and res["drift_20s_status"] == "pending"
    assert res["drift_20s_sessions_left"] == 20
    res = ere.event_metrics(dates, c, hi, lo, tv, mk, dates[-21])
    assert res["drift_20s_status"] == "complete" and res["drift_20s_pct"] is not None
    assert res["drift_60s_status"] == "pending" and res["drift_60s_sessions_left"] == 40


def test_drift_measures_post_window_abnormal_return():
    dates, c, hi, lo, tv, mk = synthetic(n=330, event_at=280, jump=-0.1, noise=0.0005, seed=8, drift=-0.002,
                                         event_turnover=500.0)
    res = ere.event_metrics(dates, c, hi, lo, tv, mk, dates[280])
    # 18 days x -0.2% abnormal
    assert res["drift_20s_pct"] == pytest.approx(-3.6, abs=0.3)


def test_shifted_reaction_day():
    dates, c, hi, lo, tv, mk = synthetic(n=300, event_at=250, seed=9)
    missing = dates[250]
    keep = [i for i, d in enumerate(dates) if d != missing]
    d2 = [dates[i] for i in keep]
    res = ere.event_metrics(d2, [c[i] for i in keep], [hi[i] for i in keep], [lo[i] for i in keep],
                            [tv[i] for i in keep], mk, missing)
    assert res["status"] == "ok" and res["reaction_date_used"] == dates[251]
    assert "reaction_day_shifted" in res["flags"]


# --------------------------------------------------------------------------
# aggregates, signals, fundamentals, next date
# --------------------------------------------------------------------------

def _r(t, car, lab, d20=None, turn=500.0, period="2026Q3", z=None, date="2026-11-10"):
    return {"ticker": t, "car_3d_pct": car, "label": lab, "drift_20s_pct": d20, "drift_60s_pct": None,
            "drift_20s_status": "complete" if d20 is not None else "pending", "median_turnover_k": turn,
            "period": period, "z": z if z is not None else car / 2, "reaction_date": date, "abnormal_turnover": 2.0,
            "intraday": False, "preliminary_first": False}


def test_season_stats_and_segments():
    rs = [_r("PKOBP", 3.0, "positive"), _r("KGHM", -5.0, "strong_negative"), _r("XYZ", 1.0, "neutral"),
          _r("OLD", 9.0, "strong_positive", period="2026H1")]
    s = ere.season_stats(rs, {"PKOBP": "WIG20", "KGHM": "WIG20"}, "2026Q3", min_turnover_k=100)
    assert s["events"] == 3
    assert s["share_positive_car"] == pytest.approx(0.667, abs=0.001)
    assert s["mean_car_3d_pct"] == pytest.approx(-0.33, abs=0.01)
    assert s["by_segment"]["WIG20"]["n"] == 2 and s["by_segment"]["other"]["n"] == 1
    assert s["labels"]["strong_negative"] == 1 and s["labels"]["strong_positive"] == 0


def test_drift_study_tstat():
    rs = [_r("A{}".format(i), -6.0, "strong_negative", d20=x) for i, x in enumerate([-1.0, -2.0, -3.0, 0.0])]
    rs += [_r("P", 4.0, "positive")]  # pending drift -> excluded
    d = ere.drift_study(rs)
    row = [x for x in d["by_label"] if x["label"] == "strong_negative"][0]
    assert row["n"] == 4 and row["mean_pct"] == pytest.approx(-1.5)
    sd = math.sqrt(sum((x + 1.5) ** 2 for x in [-1, -2, -3, 0]) / 3)
    assert row["t_stat"] == pytest.approx(round(-1.5 / (sd / 2), 2))
    assert row["low_n"] is True
    assert d["events_pending"] == 1 and d["events_with_drift"] == 4


def test_signals_exit_avoid_long():
    rs = [_r("HELD", -8.0, "strong_negative", z=-3.0), _r("WATCH", -8.0, "strong_negative", z=-5.0),
          _r("UP", 8.0, "strong_positive", z=2.4), _r("MEH", 1.0, "positive", z=1.1)]
    sig = {s["ticker"]: s for s in ere.make_signals(rs, held=["held"], raw_close={"UP": {"2026-11-10": 12.5}})}
    assert sig["UP"]["price"] == 12.5 and "price" not in sig["HELD"]
    assert sig["HELD"]["tags"]["held"] is True and sig["WATCH"]["tags"]["z"] == -5.0
    assert set(sig) == {"HELD", "WATCH", "UP"}
    assert sig["HELD"]["direction"] == "exit" and sig["HELD"]["kind"] == "earnings_strong_negative"
    assert sig["WATCH"]["direction"] == "avoid" and sig["WATCH"]["score"] == 100.0
    assert sig["UP"]["direction"] == "long" and sig["UP"]["kind"] == "earnings_strong_positive"
    assert sig["HELD"]["score"] == 75.0   # |z| 3 / 4 * 100; turnover 2x < 3x -> no bonus


def test_fundamental_surprise():
    hist = {"periods": {
        "I-III kw. 2026": {"period": {"year": 2026, "months": 9, "first_quarter": 1}, "values": {"net_profit": 120.0}, "consolidated": True},
        "I-III kw. 2025": {"period": {"year": 2025, "months": 9, "first_quarter": 1}, "values": {"net_profit": 100.0}, "consolidated": True},
        "I-VI kw. 2026": {"period": {"year": 2026, "months": 6, "first_quarter": 1}, "values": {"net_profit": 70.0}, "consolidated": True},
    }}
    assert ere.fundamental_surprise(hist, "2026Q3")["yoy_net_profit_pct"] == 20.0
    miss = ere.fundamental_surprise(hist, "2026H1")
    assert miss["yoy_net_profit_pct"] is None and miss["reason"] == "two_comparable_periods_not_in_history"
    assert ere.fundamental_surprise(None, "2026Q3")["reason"] == "no_local_fundamentals_history"
    hist["periods"]["I-III kw. 2025"]["consolidated"] = False
    assert ere.fundamental_surprise(hist, "2026Q3")["reason"] == "consolidation_basis_differs"


def test_next_expected_from_last_year():
    evs = [{"period": "2025Q3", "report_date": "2025-11-06"}, {"period": "2026H1", "report_date": "2026-09-03"}]
    nx = ere.next_expected(evs, dt.date(2026, 10, 1), KNOWN, plcal.is_probable_session)
    assert nx["period"] == "2026Q3" and nx["date_kind"] == "estimated"
    assert nx["date"] == "2026-11-06"
    # no same period last year -> statutory deadline
    nx = ere.next_expected([{"period": "2026H1", "report_date": "2026-09-03"}], dt.date(2026, 10, 1))
    assert nx["date_kind"] == "statutory_deadline" and nx["date"] == "2026-11-30"
    assert "2025 poz. 755" in nx["basis"] and "757" not in nx["basis"]
    assert ere.next_expected([], dt.date(2026, 10, 1)) is None


def test_ticker_profile():
    rs = [_r("A", 4.0, "strong_positive", d20=1.0), _r("A", -2.0, "negative", d20=-1.0), _r("A", 1.0, "neutral")]
    p = ere.ticker_profile(rs)
    assert p["events"] == 3 and p["mean_abs_car_3d_pct"] == pytest.approx(2.33, abs=0.01)
    assert p["reaction_drift_same_sign"] == 1.0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_cli_requires_mode(capsys):
    with pytest.raises(SystemExit) as e:
        cli.parse_args([])
    assert e.value.code == 2


@pytest.mark.parametrize("argv", [
    ["scan", "--lookback-days", "400"],
    ["scan", "--lookback-days", "0"],
    ["scan", "--lang", "de"],
    ["scan", "--as-of", "2026-13-01"],
    ["scan", "--as-of", "2999-01-01"],
    ["scan", "--sessions", "50"],
    ["scan", "--top", "0"],
    ["ticker"],
])
def test_cli_bad_arguments(argv):
    with pytest.raises(SystemExit) as e:
        cli.parse_args(argv)
    assert e.value.code == 2


def test_cli_history_allows_long_lookback():
    a = cli.parse_args(["ticker", "PKOBP", "--history", "--lookback-days", "600"])
    assert a.history and a.ticker == "PKOBP"


def test_cli_data_error_exit_1(tmp_path, capsys):
    rc = cli.main(["scan", "--offline", "--cache-dir", str(tmp_path), "--no-files"])
    assert rc == 1
    assert "ERROR" in capsys.readouterr().err


def _write_cache(tmp_path, n=200):
    """Synthetic archive cache (shares + WIG) in the gpw_archive layout."""
    dates, c, hi, lo, tv, mk = synthetic(n=n, event_at=n - 30, jump=-0.15, seed=21, event_turnover=900.0)
    for kind in ("shares", "indices"):
        (tmp_path / "archive" / kind).mkdir(parents=True, exist_ok=True)
    for i, d in enumerate(dates):
        prev = c[i - 1] if i else c[i]
        rows = [{"name": "ALFA", "currency": "PLN", "open": prev, "high": hi[i], "low": lo[i], "close": c[i],
                 "change_pct": round((c[i] / prev - 1) * 100, 2), "turnover_k": tv[i]}]
        (tmp_path / "archive" / "shares" / "{}.json".format(d)).write_text(json.dumps(
            {"schema": 1, "date": d, "type": "shares", "session": True, "rows": rows}), encoding="utf-8")
        (tmp_path / "archive" / "indices" / "{}.json".format(d)).write_text(json.dumps(
            {"schema": 1, "date": d, "type": "indices", "session": True,
             "rows": [{"name": "WIG", "open": mk[d], "high": mk[d], "low": mk[d], "close": mk[d], "change_pct": 0, "turnover_k": 1}]}), encoding="utf-8")
    return dates


def test_cli_unknown_ticker_exit_1(tmp_path, capsys):
    dates = _write_cache(tmp_path)
    rc = cli.main(["ticker", "NOPE", "--offline", "--cache-dir", str(tmp_path), "--as-of", dates[-1], "--no-files"])
    assert rc == 1 and "not found" in capsys.readouterr().err


def test_cli_end_to_end_offline(tmp_path, monkeypatch, capsys):
    dates = _write_cache(tmp_path)
    ev_day = dt.date.fromisoformat(dates[-30])
    prev_day = dt.date.fromisoformat(dates[-31])

    def fake_range(start, end, **kw):
        rows = [{"datetime": "{}T18:00:00".format(prev_day.isoformat()), "date": prev_day.isoformat(), "class": "periodic_q",
                 "correction": False, "isin": "PLALFA000016", "company": "ALFA SA", "title": "", "type": "Q",
                 "url": "https://www.gpw.pl/komunikat?geru_id=1"},
                {"datetime": "{}T18:30:00".format(prev_day.isoformat()), "date": prev_day.isoformat(), "class": "periodic_q",
                 "correction": True, "isin": "PLALFA000016", "company": "ALFA SA", "title": "Korekta raportu", "type": "RB",
                 "url": "https://www.gpw.pl/komunikat?geru_id=2"}]
        rows = rows if start <= prev_day <= end else []   # the CLI fetches in two-week chunks
        return {"rows": rows, "days": [{"date": start.isoformat()}], "missing_days": []}

    monkeypatch.setattr(cli.espi, "fetch_range", fake_range)
    monkeypatch.setattr(cli.fundamentals, "isin_map", lambda *a, **k: {"ALFA": "PLALFA000016"})
    monkeypatch.setattr(cli.constituents, "size_membership", lambda *a, **k: {"ALFA": "SWIG80"})
    out = tmp_path / "out"
    rc = cli.main(["ticker", "ALFA", "--offline", "--cache-dir", str(tmp_path), "--as-of", dates[-1],
                   "--output-dir", str(out), "--lang", "en", "--state-dir", str(tmp_path / "nojournal")])
    assert rc == 0
    js = json.loads(next(out.glob("gpw_earnings_reaction_*.json")).read_text(encoding="utf-8"))
    assert js["skill"] == "gpw-earnings-reaction" and js["version"] == "1.0.0" and js["lang"] == "en"
    assert {"reactions", "season", "signals", "data_provenance", "as_of"} <= set(js)
    assert len(js["reactions"]) == 1
    r = js["reactions"][0]
    for k in ("ticker", "report_date", "report_type", "session_alignment", "car_3d_pct", "abnormal_turnover",
              "drift_20s_pct", "label"):
        assert k in r
    assert r["reaction_date"] == ev_day.isoformat() and r["session_alignment"] == "after_close"
    assert r["label"] == "strong_negative"
    assert r["fundamental_surprise"]["yoy_net_profit_pct"] is None
    assert js["espi"]["counts"]["corrections_ignored"] == 1
    assert js["signals"][0]["direction"] == "avoid" and js["signals"][0]["kind"] == "earnings_strong_negative"
    assert js["tickers"]["ALFA"]["next_expected"]["date_kind"] == "statutory_deadline"
    assert js["data_provenance"]["freshness"]["historical_run"] is True
    md = next(out.glob("gpw_earnings_reaction_*.md")).read_text(encoding="utf-8")
    assert "strong negative" in md and "Educational material" in md


def test_drift_vs_neutral_control():
    rs = [_r("N{}".format(i), 0.1, "neutral", d20=x) for i, x in enumerate([-3.0, -2.0, -4.0, -3.0])]
    rs += [_r("S{}".format(i), -6.0, "strong_negative", d20=x) for i, x in enumerate([-5.0, -6.0, -4.0])]
    d = ere.drift_study(rs)
    row = [x for x in d["by_label"] if x["label"] == "strong_negative"][0]
    assert row["vs_neutral_pp"] == pytest.approx(-2.0)
    assert row["t_vs_neutral"] < -2
    neu = [x for x in d["by_label"] if x["label"] == "neutral"][0]
    assert neu["vs_neutral_pp"] is None
    assert ere.welch_t([1.0], [2.0, 3.0]) is None


def test_baseline_daily_ar():
    dates = ["2026-11-10", "2026-11-12", "2026-11-13"]
    mk = {"2026-11-10": 100.0, "2026-11-12": 101.0, "2026-11-13": 102.01}
    a = ere.ReturnPanel(dates, [10.0, 10.0, 10.0], mk)          # -1% AR each day
    b = ere.ReturnPanel(dates, [10.0, 10.1, 10.201], mk)         # 0 AR
    c = ere.ReturnPanel(dates, [10.0, 30.0, 30.3], mk)           # +199% day dropped as a data error
    out = ere.baseline_daily_ar([a, b, c], "2026-11-12", "2026-11-13")
    assert out["stock_days"] == 5
    assert out["mean_daily_ar_pct"] == pytest.approx(-0.4, abs=1e-6)
