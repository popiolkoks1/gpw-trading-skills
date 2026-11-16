import json

import pytest
from conftest import FIXTURES

import ownership_shorts as osh
from gpwlib import panel


def _rows(name):
    return osh.parse_records(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def _row(holder, isin, pct, date, issuer="X"):
    return {"holder": holder, "issuer": issuer, "isin": isin, "pct": pct, "position_date": date, "published": date}


def test_parse_fixture_rows():
    cur = _rows("knf_current.json")
    assert cur and all(r["pct"] >= 0.5 for r in cur)
    assert {"holder", "issuer", "isin", "pct", "position_date", "published"} <= set(cur[0])
    # HTML entities are unescaped (KNF sends "&amp;")
    assert any("QUBE RESEARCH & TECHNOLOGIES" in r["holder"] for r in cur)


def test_parse_drops_bad_rows_and_comma_decimal():
    rows = osh.parse_records({"records": [
        {"HOLDER_FULL_NAME": "A", "ISIN": "PLX", "NET_SHORT_POSITION_O": "0,61", "POSITION_DATE": "2026-11-09"},
        {"HOLDER_FULL_NAME": "B", "ISIN": "PLX", "NET_SHORT_POSITION_O": "n/a", "POSITION_DATE": "2026-11-09"},
        {"HOLDER_FULL_NAME": "", "ISIN": "PLX", "NET_SHORT_POSITION_O": "0.7", "POSITION_DATE": "2026-11-09"},
        "garbage"]})
    assert len(rows) == 1 and rows[0]["pct"] == 0.61


def test_parse_rejects_non_register():
    with pytest.raises(ValueError):
        osh.parse_records({"status": "error"})


def test_request_form():
    f = osh.request_form("RssHTable", offset=1000)
    payload = json.loads(f["request"])
    assert payload["method"] == "RssHTable" and payload["offset"] == 1000 and payload["limit"] == 1000
    with pytest.raises(ValueError):
        osh.request_form("Other")


def test_fixture_aggregation_real_data():
    cur, hist = _rows("knf_current.json"), _rows("knf_history.json")
    agg = osh.aggregate(cur, hist, "2026-11-13")
    by = {r["isin"]: r for r in agg["by_issuer"]}
    modivo = by["PLCCC0000016"]
    assert modivo["holders"] == 3 and modivo["crowded"]
    assert modivo["total_pct"] == pytest.approx(sum(r["pct"] for r in cur if r["isin"] == "PLCCC0000016"))
    assert by["LU2237380790"]["crowded"] is False


def test_crowded_rules():
    cur = [_row("A", "PL1", 1.2, "2026-11-01"), _row("B", "PL1", 0.9, "2026-11-01"),
           _row("C", "PL2", 0.5, "2026-11-01"), _row("D", "PL2", 0.5, "2026-11-01"), _row("E", "PL2", 0.5, "2026-11-01"),
           _row("F", "PL3", 1.9, "2026-11-01")]
    agg = osh.aggregate(cur, [], "2026-11-13")
    by = {r["isin"]: r for r in agg["by_issuer"]}
    assert by["PL1"]["crowded"] and by["PL1"]["crowded_reasons"] == ["total_pct>=2.0"]
    assert by["PL2"]["crowded"] and by["PL2"]["crowded_reasons"] == ["holders>=3"]
    assert not by["PL3"]["crowded"]


def test_change_window_new_and_closed_events():
    dates = ["2026-10-{:02d}".format(d) for d in range(1, 32) if d not in (4, 5, 11, 12, 18, 19, 25, 26)] + \
            ["2026-11-{:02d}".format(d) for d in (2, 3, 4, 5, 6, 9, 10, 12, 13)]
    hist = [
        _row("OLD", "PL1", 0.8, "2026-09-01"), _row("OLD", "PL1", 1.0, "2026-11-10"),   # increased
        _row("GONE", "PL1", 0.6, "2026-10-01"), _row("GONE", "PL1", 0.5, "2026-11-05"),  # left register
        _row("NEW", "PL1", 0.7, "2026-11-12"),                                              # new position
    ]
    cur = [_row("OLD", "PL1", 1.0, "2026-11-10"), _row("NEW", "PL1", 0.7, "2026-11-12")]
    agg = osh.aggregate(cur, hist, "2026-11-13", session_dates=dates)
    r = agg["by_issuer"][0]
    d20 = agg["window_start"]
    assert d20 == dates[dates.index("2026-11-13") - 20]
    # 20 sessions ago: OLD 0.8 + GONE 0.6 (open: disclosed again after d20) = 1.4 -> now 1.7
    assert r["total_20s_ago_pct"] == pytest.approx(1.4)
    assert r["change_20s_pp"] == pytest.approx(0.3)
    types = {(e["type"], e["holder"]) for e in agg["events"]}
    assert ("new_position", "NEW") in types and ("closed_position", "GONE") in types
    closed = next(e for e in agg["events"] if e["type"] == "closed_position")
    assert closed["exact_date"] is False and closed["date"] is None


def test_open_at_lower_bound_for_ambiguous_closed():
    pairs = osh.pair_histories([_row("X", "PL1", 0.6, "2026-01-05")], [])
    assert osh.open_at(pairs, set(), "2026-06-01") == {}           # ambiguous -> not counted
    assert len(osh.open_at(pairs, {("X", "PL1")}, "2026-06-01")) == 1  # still current -> counted


def test_snapshot_gives_exact_closed_window():
    cur = [_row("A", "PL1", 0.7, "2026-11-01")]
    prev = {"date": "2026-11-12", "rows": [_row("A", "PL1", 0.7, "2026-11-01"), _row("B", "PL1", 0.55, "2026-06-01")]}
    agg = osh.aggregate(cur, [], "2026-11-13", prev_snapshot=prev)
    ev = [e for e in agg["events"] if e["type"] == "closed_position"]
    assert ev and ev[0]["closed_between"] == ["2026-11-12", "2026-11-13"]


def test_episode_restarts_after_long_gap():
    rows = [_row("A", "PL1", 0.6, "2023-01-02"), _row("A", "PL1", 0.6, "2026-10-01"), _row("A", "PL1", 0.7, "2026-11-01")]
    assert osh.first_date_of_episode(rows) == "2026-10-01"


def _series(name, closes, turnover_k, start_day=1):
    s = panel.Series(name)
    for i, (c, t) in enumerate(zip(closes, turnover_k)):
        s.dates.append("2026-10-{:02d}".format(start_day + i))
        s.close.append(c)
        s.high.append(c)
        s.low.append(c)
        s.turnover_k.append(t)
    return s


def test_days_to_cover_and_price_change():
    s = _series("ABC", [10.0] * 19 + [12.0], [100.0] * 20)   # 100k PLN / 10 PLN = 10 000 shares a day
    cur = [_row("A", "PL1", 1.0, "2026-10-01", issuer="ABC")]
    agg = osh.aggregate(cur, [], "2026-10-20", ticker_of=lambda i, n: "ABC", panel={"ABC": s},
                        shares_of=lambda t, i: 10_000_000)
    r = agg["by_issuer"][0]
    assert r["short_shares_est"] == 100_000
    assert r["median_volume_20s_est"] == 10_000
    assert r["days_to_cover"] == pytest.approx(10.0)
    assert r["high_days_to_cover"] is True
    assert r["price_change_since_first_pct"] == pytest.approx(20.0)


def test_missing_market_data_gives_nulls():
    agg = osh.aggregate([_row("A", "PL1", 0.6, "2026-10-01")], [], "2026-10-20")
    r = agg["by_issuer"][0]
    assert r["ticker"] is None and r["days_to_cover"] is None and r["price_change_since_first_pct"] is None


def test_issuer_name_match_and_history():
    assert osh.match_issuer_name("GRUPAAZOTY", ["GRUPAAZOTY", "KETY"]) == "GRUPAAZOTY"
    assert osh.match_issuer_name("Unknown", ["KETY"]) is None
    hist = [_row("A", "PL1", 0.6, "2026-10-01"), _row("A", "PL1", 0.8, "2026-10-15")]
    h = osh.issuer_history(hist, [hist[-1]], "PL1", step_days=7)
    assert h[0]["total_pct"] == 0.6 and h[-1]["total_pct"] == 0.8


class _Resp:
    def __init__(self, payload):
        self.text = json.dumps(payload)
        self.retrieved_at = "2026-11-16T12:00:00+00:00"
        self.from_cache = True

    def json(self):
        return json.loads(self.text)


class _FakeHTTP:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def post(self, url, form, **kw):
        self.calls.append(json.loads(form["request"]))
        return _Resp(self.pages[len(self.calls) - 1])


def test_fetch_paginates():
    rec = {"HOLDER_FULL_NAME": "A", "ISIN": "PL1", "NET_SHORT_POSITION_O": "0.6", "POSITION_DATE": "2026-11-09"}
    fake = _FakeHTTP([{"total": 1001, "records": [rec] * 1000}, {"total": 1001, "records": [rec]}])
    res = osh.fetch("RssHTable", fake)
    assert len(res["rows"]) == 1001 and [c["offset"] for c in fake.calls] == [0, 1000]
