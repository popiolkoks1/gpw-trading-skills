import datetime as dt
import math

import pytest

import postmortem_engine as pe


# --------------------------------------------------------------- helpers

def make_calendar(n, start=dt.date(2025, 1, 1)):
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += dt.timedelta(days=1)
    return days


def add(book, name, closes, kind="share", dates=None, opens=None, highs=None, lows=None, turnover=1000.0, raw=None):
    dates = dates or book.calendar[: len(closes)]
    opens = opens or list(closes)
    highs = highs or list(closes)
    lows = lows or list(closes)
    book.add(pe.Instrument(name, kind, dates, opens, highs, lows, closes, raw, [turnover] * len(closes)))


def geometric(start, rate, n):
    return [start * (1 + rate) ** i for i in range(n)]


@pytest.fixture
def book():
    cal = make_calendar(120)
    b = pe.PriceBook(cal)
    add(b, "WIG", geometric(100.0, 0.001, 120), kind="index")       # +0.1% per session
    add(b, "UP", geometric(10.0, 0.01, 120))                         # +1% per session
    add(b, "DOWN", geometric(10.0, -0.01, 120))                      # -1% per session
    add(b, "FLAT", [20.0] * 120)
    return b


CFG = {"horizons": [5, 20, 60], "primary": 20, "entry": "close", "hit_benchmark": "wig"}


def sig(ticker, date, direction="long", kind="test", score=None, **attrs):
    return {"id": "x", "source": "unit", "ticker": ticker, "date": date, "kind": kind, "direction": direction,
            "score": score, "attrs": attrs}


# ----------------------------------------------------------------- stats

def test_wilson_known_values():
    lo, hi = pe.wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-4) and hi == pytest.approx(0.7634, abs=1e-4)
    lo, hi = pe.wilson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=1e-4)
    lo, hi = pe.wilson(10, 10)
    assert hi == 1.0 and lo == pytest.approx(0.7225, abs=1e-4)
    assert pe.wilson(0, 0) == (None, None)


def test_t_stats():
    assert pe.t_stat([1.0, 2.0, 3.0]) == pytest.approx(2.0 / (1.0 / math.sqrt(3)))
    assert pe.t_stat([1.0]) is None
    assert pe.t_stat([2.0, 2.0, 2.0]) is None  # zero dispersion -> undefined, never Infinity in JSON
    assert pe.welch_t([1, 2, 3], [1, 2, 3]) == pytest.approx(0.0)
    assert pe.welch_t([1], [1, 2]) is None
    assert pe.median([3, 1, 2, 10]) == 2.5


# ------------------------------------------------------- forward returns

def test_forward_returns_close_entry(book):
    r = pe.evaluate_signal(sig("UP", book.calendar[10]), book, CFG)
    h5 = r["horizons"]["5"]
    assert h5["status"] == "done"
    assert h5["return_pct"] == pytest.approx((1.01 ** 5 - 1) * 100, abs=0.01)
    assert h5["wig_return_pct"] == pytest.approx((1.001 ** 5 - 1) * 100, abs=0.01)
    assert h5["excess_wig_pp"] == pytest.approx(h5["return_pct"] - h5["wig_return_pct"], abs=0.02)
    assert h5["exit_session"] == book.calendar[15]
    assert r["status"] == "evaluated" and r["outcome"] == "true_positive"
    assert r["horizons"]["60"]["return_pct"] == pytest.approx((1.01 ** 60 - 1) * 100, abs=0.05)


def test_equal_weight_benchmark(book):
    r = pe.evaluate_signal(sig("FLAT", book.calendar[30]), book, CFG)
    h = r["horizons"]["20"]
    ew = ((1.01 ** 20 - 1) + (0.99 ** 20 - 1) + 0) / 3 * 100
    assert h["ew_return_pct"] == pytest.approx(ew, abs=0.01)
    assert h["excess_ew_pp"] == pytest.approx(-ew, abs=0.01)
    r2 = pe.evaluate_signal(sig("FLAT", book.calendar[30]), book, dict(CFG, hit_benchmark="ew"))
    assert r2["horizons"]["20"]["excess_pp"] == pytest.approx(-ew, abs=0.01)


def test_next_open_entry(book):
    cal = book.calendar
    opens = [10.0] * 120
    opens[11] = 9.0
    add(book, "GAP", [10.0] * 120, opens=opens)
    r = pe.evaluate_signal(sig("GAP", cal[10]), book, dict(CFG, entry="next_open"))
    assert r["entry_session"] == cal[11] and r["entry_price"] == 9.0
    assert r["horizons"]["5"]["return_pct"] == pytest.approx((10 / 9 - 1) * 100, abs=0.01)
    assert r["horizons"]["5"]["exit_session"] == cal[15]


def test_avoid_and_skip_directions(book):
    d = book.calendar[10]
    down = pe.evaluate_signal(sig("DOWN", d, "avoid"), book, CFG)
    assert down["outcome"] == "true_positive" and down["horizons"]["20"]["edge_pp"] > 0
    up = pe.evaluate_signal(sig("UP", d, "exit"), book, CFG)
    assert up["outcome"] == "missed_opportunity"
    assert pe.evaluate_signal(sig("UP", d, "skip"), book, CFG)["outcome"] == "missed_opportunity"
    assert pe.evaluate_signal(sig("DOWN", d, "skip"), book, CFG)["outcome"] == "true_negative"
    assert pe.evaluate_signal(sig("DOWN", d, "long"), book, CFG)["outcome"] == "false_positive"


def test_regime_mismatch(book):
    lookup = lambda d: "correction" if d >= book.calendar[15] else "confirmed_uptrend"  # noqa: E731
    r = pe.evaluate_signal(sig("DOWN", book.calendar[10]), book, CFG, regime_lookup=lookup)
    assert r["regime_at_signal"] == "confirmed_uptrend"
    assert r["horizons"]["20"]["regime_worst"] == "correction"
    assert r["outcome"] == "regime_mismatch"
    # a long that worked in a bad regime is still a true positive
    r2 = pe.evaluate_signal(sig("UP", book.calendar[10]), book, CFG, regime_lookup=lambda d: "bear")
    assert r2["outcome"] == "true_positive"


def test_broad_index_judged_on_absolute_return(book):
    r = pe.evaluate_signal(sig("WIG", book.calendar[10]), book, CFG)
    assert r["basis"] == "absolute" and r["size_segment"] == "index"
    assert r["horizons"]["20"]["excess_pp"] == pytest.approx(r["horizons"]["20"]["return_pct"])
    assert r["outcome"] == "true_positive"


def test_pending_and_missing(book):
    cal = book.calendar
    r = pe.evaluate_signal(sig("UP", cal[110]), book, CFG)
    assert r["horizons"]["5"]["status"] == "done"
    assert r["horizons"]["20"] == {"status": "pending", "sessions_left": 11}
    assert r["status"] == "pending" and r["sessions_left"] == 11
    assert pe.evaluate_signal(sig("NOPE", cal[10]), book, CFG)["status"] == "no_price_data"
    assert pe.evaluate_signal(sig("UP", "2020-01-01"), book, CFG)["status"] == "before_data"
    r = pe.evaluate_signal(sig("UP", cal[-1]), book, dict(CFG, entry="next_open"))
    assert r["status"] == "pending"


def test_weekend_signal_maps_to_previous_session(book):
    fri = next(d for d in book.calendar if dt.date.fromisoformat(d).weekday() == 4 and d > book.calendar[5])
    sat = (dt.date.fromisoformat(fri) + dt.timedelta(days=1)).isoformat()
    r = pe.evaluate_signal(sig("UP", sat), book, CFG)
    assert r["signal_session"] == fri


def test_delisted_name_keeps_last_close(book):
    add(book, "GONE", [10.0] * 30 + [5.0] * 10)  # trades 40 sessions, then disappears
    r = pe.evaluate_signal(sig("GONE", book.calendar[25]), book, CFG)
    h = r["horizons"]["20"]
    assert h["stale_exit"] is True and h["exit_price"] == 5.0 and h["return_pct"] == pytest.approx(-50.0)


def test_mfe_mae(book):
    closes = [10.0] * 120
    highs, lows = list(closes), list(closes)
    highs[12], lows[14] = 12.0, 9.0
    add(book, "SWING", closes, highs=highs, lows=lows)
    h = pe.evaluate_signal(sig("SWING", book.calendar[10]), book, CFG)["horizons"]["5"]
    assert h["mfe_pct"] == pytest.approx(20.0) and h["mae_pct"] == pytest.approx(-10.0)


def test_first_touch_stop_pivot(book):
    closes = [10.0] * 120
    highs, lows = list(closes), list(closes)
    lows[13] = 9.0     # stop touched on session 13
    highs[15] = 11.5   # pivot touched later
    add(book, "LVL", closes, highs=highs, lows=lows)
    r = pe.evaluate_signal(sig("LVL", book.calendar[10], pivot=11.0, invalidation=9.5), book, CFG)
    lv = r["levels"]
    assert lv["first"] == "stop" and lv["stop_hit_session"] == book.calendar[13]
    assert lv["pivot_hit_session"] == book.calendar[15] and lv["pivot_already_above"] is False
    # same session
    lows2, highs2 = list(closes), list(closes)
    lows2[12], highs2[12] = 9.0, 11.5
    add(book, "LVL2", closes, highs=highs2, lows=lows2)
    assert pe.evaluate_signal(sig("LVL2", book.calendar[10], pivot=11.0, invalidation=9.5), book, CFG)["levels"]["first"] == "same_session"
    # entry above pivot: only the stop is tracked
    lv3 = pe.evaluate_signal(sig("LVL", book.calendar[10], pivot=9.8, invalidation=9.5), book, CFG)["levels"]
    assert lv3["pivot_already_above"] is True and lv3["first"] == "stop"


def test_levels_rescaled_by_adjustment_factor(book):
    # history was later adjusted by 0.5 (e.g. a 2:1 split): the skill saw price 20, pivot 22, stop 19
    add(book, "SPLIT", [10.0] * 120, lows=[10.0] * 12 + [9.4] + [10.0] * 107)
    r = pe.evaluate_signal(sig("SPLIT", book.calendar[10], price=20.0, pivot=22.0, invalidation=19.0), book, CFG)
    assert r["levels"]["pivot"] == pytest.approx(11.0) and r["levels"]["invalidation"] == pytest.approx(9.5)
    assert r["levels"]["first"] == "stop"


# --------------------------------------------------------------- buckets

def _results(book, n_up=25, n_down=5):
    out = []
    for i in range(n_up):
        out.append(pe.evaluate_signal(sig("UP", book.calendar[i], score=90, execution_state="breakout",
                                          rs_rating=95), book, CFG))
    for i in range(n_down):
        out.append(pe.evaluate_signal(sig("DOWN", book.calendar[i], score=50, execution_state="extended",
                                          rs_rating=75), book, CFG))
    return out


def test_bucket_stats_and_min_n(book):
    res = _results(book)
    buckets = pe.build_buckets(res, [20], min_n=20)
    allb = next(b for b in buckets if b["dimension"] == "all" and b["direction_group"] == "long")
    assert allb["n"] == 30 and allb["hits"] == 25 and allb["hit_rate"] == pytest.approx(25 / 30, abs=1e-4)
    lo, hi = pe.wilson(25, 30)
    assert allb["hit_rate_ci95"] == [round(lo, 4), round(hi, 4)]
    assert allb["status"] == "ok" and allb["n_dates"] == 25
    pri = {b["bucket"]: b for b in buckets if b["dimension"] == "priority"}
    assert pri["80-100"]["n"] == 25 and pri["80-100"]["status"] == "ok"
    assert pri["40-60"]["n"] == 5 and pri["40-60"]["status"] == "insufficient"
    rs = {b["bucket"] for b in buckets if b["dimension"] == "rs_bucket"}
    assert rs == {"90-99", "70-79"}
    reg = {b["bucket"] for b in buckets if b["dimension"] == "regime_at_signal"}
    assert reg == {"unknown"}
    for b in buckets:  # ROADMAP contract fields
        for k in ("dimension", "bucket", "n", "hit_rate", "mean_excess_pct"):
            assert k in b


def test_buckets_skip_pending(book):
    res = [pe.evaluate_signal(sig("UP", book.calendar[115]), book, CFG)]
    assert [b for b in pe.build_buckets(res, [20], 20)] == []
    assert pe.outcome_counts(res) == {"pending": 1}


def test_score_and_rs_buckets():
    assert pe.score_bucket(80) == "80-100" and pe.score_bucket(79.9) == "60-80" and pe.score_bucket(0) == "0-20"
    assert pe.score_bucket(None) is None
    assert pe.rs_bucket(99) == "90-99" and pe.rs_bucket(70) == "70-79" and pe.rs_bucket(10) == "<70"


# -------------------------------------------------------------- feedback

def test_feedback_insufficient_is_conservative(book):
    res = _results(book, n_up=5, n_down=3)
    fb = pe.build_feedback(res, 20, min_n=20)
    assert fb["auto_apply"] is False and fb["passed"] == 0
    assert all(s["verdict"] == "insufficient" for s in fb["suggestions"])
    assert all(s["action"] == "none" for s in fb["suggestions"])
    assert "pl" in fb["summary"] and "en" in fb["summary"]


def test_feedback_priority_supported(book):
    # high-priority signals on a rising name, low-priority on a falling one: clear, large-n difference
    cal = book.calendar
    noisy_up = [10.0 * (1.01 ** i) * (1 + 0.002 * ((i % 3) - 1)) for i in range(120)]
    noisy_dn = [10.0 * (0.99 ** i) * (1 + 0.002 * ((i % 4) - 1.5)) for i in range(120)]
    add(book, "NUP", noisy_up)
    add(book, "NDN", noisy_dn)
    res = [pe.evaluate_signal(sig("NUP", cal[i], score=85), book, CFG) for i in range(30)]
    res += [pe.evaluate_signal(sig("NDN", cal[i], score=50), book, CFG) for i in range(30)]
    fb = pe.build_feedback(res, 20, min_n=20)
    pri = next(s for s in fb["suggestions"] if s["id"] == "priority_unit")
    assert pri["verdict"] == "supported" and pri["action"] == "review"
    assert pri["evidence"]["n_a"] == 30 and pri["evidence"]["n_b"] == 30 and pri["evidence"]["diff_pp"] > 0
    assert "pp" in pri["statement"]["pl"] and "," in pri["statement"]["pl"]  # Polish decimal comma
    assert fb["by_skill"]["unit"]["priority_threshold"]["verdict"] == "supported"


def test_feedback_contradicted_single(book):
    res = [pe.evaluate_signal(sig("FLAT", book.calendar[i], kind="bad"), book, CFG) for i in range(25)]
    s = next(x for x in pe.build_feedback(res, 20, 20)["suggestions"] if x["id"] == "signal_unit_bad")
    assert s["verdict"] in ("contradicted", "suggestive_against")  # flat stock lags a rising WIG every time
    noisy = [10.0 * (0.99 ** i) * (1 + 0.003 * ((i % 5) - 2)) for i in range(120)]
    add(book, "NOISY", noisy)
    res = [pe.evaluate_signal(sig("NOISY", book.calendar[i], kind="bad"), book, CFG) for i in range(25)]
    s = next(x for x in pe.build_feedback(res, 20, 20)["suggestions"] if x["id"] == "signal_unit_bad")
    assert s["verdict"] == "contradicted" and s["evidence"]["hit_rate_ci95"][1] < 0.5


# ------------------------------------------------------------------ demo

def test_demo_signals_breakout_rule():
    cal = make_calendar(300)
    b = pe.PriceBook(cal)
    add(b, "WIG", [100.0] * 300, kind="index")
    closes = [10.0] * 270 + [11.0] * 5 + [10.0] * 10 + [12.0] * 15   # breakout at 270, re-break at 285
    add(b, "BRK", closes, turnover=500.0)
    add(b, "ILLQ", closes, turnover=10.0)        # below the screener's 200k liquidity floor
    sigs = pe.demo_signals(b)
    assert [s["date"] for s in sigs] == [cal[270]]  # 285 is within the 20-session re-arm window
    s = sigs[0]
    assert s["source"] == "demo" and s["kind"] == "demo_52w_high" and s["direction"] == "long"
    assert s["attrs"]["pivot"] == pytest.approx(10.0)
    assert s["attrs"]["invalidation"] == pytest.approx(9.2)
