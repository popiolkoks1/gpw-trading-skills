import json
from pathlib import Path

import pytest

import postmortem_ledger as pl

FIX = Path(__file__).resolve().parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------ screener

def test_screener_watchlist_and_near_misses():
    ex = pl.extract(load("gpw_trend_screener_2026-11-13_fixture.json"), "s.json")
    by = {s["ticker"]: s for s in ex["signals"]}
    assert set(by) == {"KETY", "PKOBP", "PGE", "LPP"}
    k = by["KETY"]
    assert k["direction"] == "long" and k["kind"] == "vcp_setup" and k["date"] == "2026-11-13"
    assert k["score"] == 72.2 and k["source"] == "gpw-trend-screener"
    a = k["attrs"]
    assert a["execution_state"] == "breakout" and a["rs_rating"] == 71 and a["on_watchlist"] is True
    assert a["pivot"] == 1281.0 and a["invalidation"] == 1228.83 and a["price"] == 1337.0
    assert by["PGE"]["direction"] == "skip" and by["PGE"]["kind"] == "near_miss"
    ex2 = pl.extract(load("gpw_trend_screener_2026-11-13_fixture.json"), near_misses=False)
    assert {s["ticker"] for s in ex2["signals"]} == {"KETY", "PKOBP"}


def test_screener_non_screen_mode_rejected():
    data = load("gpw_trend_screener_2026-11-13_fixture.json")
    data["mode"] = "history"
    ex = pl.extract(data)
    assert ex["signals"] == [] and ex["rejected"][0]["reason"] == "screener_mode_not_screen"


# -------------------------------------------------------------- regime

def test_regime_transitions_become_index_signals():
    ex = pl.extract(load("gpw_market_regime_2026-11-13_fixture.json"))
    kinds = {(s["ticker"], s["kind"], s["direction"]) for s in ex["signals"]}
    assert ("WIG", "ftd", "long") in kinds
    assert ("WIG", "distribution_cluster", "exit") in kinds
    assert ("WIG", "regime_correction", "avoid") in kinds
    assert ("WIG", "regime_confirmed_uptrend", "long") in kinds
    assert any(t == "SWIG80" for t, _, _ in kinds)
    assert not any(k == "regime_rally_attempt" for _, k, _ in kinds)  # rally attempt = wait
    ftd = next(s for s in ex["signals"] if s["kind"] == "ftd" and s["ticker"] == "WIG")
    assert ftd["date"] == "2026-03-17"


def test_regime_timeline_lookup():
    ex = pl.extract(load("gpw_market_regime_2026-11-13_fixture.json"))
    tl = ex["timeline"]
    assert tl["index"] == "WIG" and tl["start"] == "2025-11-14" and tl["end"] == "2026-11-13"
    tls = [tl]
    assert pl.regime_at(tls, "2025-11-17") == "confirmed_uptrend"
    assert pl.regime_at(tls, "2025-11-21") == "uptrend_under_pressure"
    assert pl.regime_at(tls, "2026-03-09") == "correction"
    assert pl.regime_at(tls, "2026-03-11") == "rally_attempt"
    assert pl.regime_at(tls, "2026-11-13") == "confirmed_uptrend"
    assert pl.regime_at(tls, "2025-01-01") is None      # outside coverage -> unknown, never guessed
    assert pl.regime_at(tls, "2026-12-01") is None


# ---------------------------------------------------- breadth / sector

def test_breadth_and_sector_exposure_signals():
    br = {"skill": "gpw-market-breadth", "composite": {"score": 72.0, "zone": "healthy"},
          "data_provenance": {"as_of": "2026-11-13"}}
    s = pl.extract(br)["signals"]
    assert len(s) == 1 and s[0]["ticker"] == "WIG" and s[0]["direction"] == "long" and s[0]["kind"] == "breadth_supportive"
    br["composite"]["score"] = 50.0
    assert pl.extract(br)["signals"] == []  # neutral zone makes no claim
    br["composite"]["score"] = 35.0
    assert pl.extract(br)["signals"][0]["direction"] == "avoid"
    st = {"skill": "gpw-sector-trend", "composite": {"score": 45.0}, "data_provenance": {"as_of": "2026-11-13"},
          "sectors": [{"key": "WIG-BANKI", "trend": "up", "ratio": 0.8, "low_sample": False},
                      {"key": "WIG-GRY", "trend": "down", "ratio": 0.1, "low_sample": False},
                      {"key": "WIG-LEKI", "trend": "up", "ratio": 0.5, "low_sample": True}]}
    sig = {x["ticker"]: x for x in pl.extract(st)["signals"]}
    assert set(sig) == {"WIG-BANKI", "WIG-GRY"}
    assert sig["WIG-BANKI"]["direction"] == "long" and sig["WIG-BANKI"]["score"] == 80.0
    assert sig["WIG-GRY"]["direction"] == "avoid"


# ------------------------------------------------------ generic contract

GOOD = {"ticker": "PKOBP", "date": "2026-11-10", "kind": "insider_cluster_buy", "direction": "long", "score": 70}


@pytest.mark.parametrize("patch,reason", [
    ({"ticker": ""}, "bad_ticker"),
    ({"ticker": "PKO BP!"}, "bad_ticker"),
    ({"date": "10.11.2026"}, "bad_date"),
    ({"date": "2026-11-10T10:00"}, "bad_date"),
    ({"date": "2026-11-20"}, "date_after_as_of"),
    ({"kind": "Insider Buy"}, "bad_kind"),
    ({"direction": "short"}, "bad_direction"),
    ({"score": 140}, "bad_score"),
    ({"score": "high"}, "bad_score"),
    ({"pivot": -1}, "bad_pivot"),
    ({"tags": ["x"]}, "bad_tags"),
])
def test_generic_contract_rejections(patch, reason):
    assert pl.validate_generic(dict(GOOD, **patch), "2026-11-13") == reason


def test_generic_contract_accepts_and_ingests():
    assert pl.validate_generic(GOOD, "2026-11-13") is None
    assert pl.validate_generic({k: GOOD[k] for k in ("ticker", "date", "kind", "direction")}, None) is None
    rep = {"skill": "gpw-ownership", "as_of": "2026-11-13",
           "signals": [GOOD, dict(GOOD, direction="sideways"), "junk",
                       dict(GOOD, ticker="CDPROJEKT", kind="crowded_short", direction="avoid", tags={"pct": 2.1})]}
    ex = pl.extract(rep)
    assert [s["ticker"] for s in ex["signals"]] == ["PKOBP", "CDPROJEKT"]
    assert ex["signals"][0]["source"] == "gpw-ownership"
    assert ex["signals"][1]["attrs"]["tags"] == {"pct": 2.1}
    assert [r["reason"] for r in ex["rejected"]] == ["bad_direction", "not_an_object"]


def test_unsupported_and_own_reports():
    with pytest.raises(pl.LedgerError):
        pl.extract({"skill": "gpw-position-sizer", "shares": 10})
    with pytest.raises(pl.LedgerError):
        pl.extract({"skill": "gpw-signal-postmortem", "signals": []})
    with pytest.raises(pl.LedgerError):
        pl.extract(["not", "a", "dict"])
    assert pl.extract({"signals": [GOOD]})["rejected"][0]["reason"] == "generic_report_without_skill"


# ---------------------------------------------------------------- ledger

def test_dedupe_is_idempotent(tmp_path):
    led = pl.empty_ledger()
    ex = pl.extract(load("gpw_trend_screener_2026-11-13_fixture.json"))
    e1 = pl.merge(led, ex, "a.json")
    e2 = pl.merge(led, pl.extract(load("gpw_trend_screener_2026-11-13_fixture.json")), "b.json")
    assert e1["new"] == 4 and e2["new"] == 0 and e2["duplicates"] == 4
    assert len(led["signals"]) == 4
    # same ticker/date but a different kind or source is a different signal
    other = {"source": "x", "as_of": None, "rejected": [], "timeline": None,
             "signals": [pl.make_signal("gpw-ownership", "KETY", "2026-11-13", "vcp_setup", "long")]}
    assert pl.merge(led, other)["new"] == 1
    pl.save_ledger(tmp_path, led)
    back = pl.load_ledger(tmp_path)
    assert len(back["signals"]) == 5 and len(back["ingested"]) == 3
    assert pl.signal_id("a", "kety", "2026-01-01", "k") == pl.signal_id("a", "KETY", "2026-01-01", "k")


def test_regime_timeline_stored_once():
    led = pl.empty_ledger()
    for _ in range(2):
        pl.merge(led, pl.extract(load("gpw_market_regime_2026-11-13_fixture.json")))
    assert len(led["regime_timelines"]) == 1


def test_ledger_errors(tmp_path):
    assert pl.load_ledger(tmp_path / "missing")["signals"] == []
    (tmp_path / pl.LEDGER_FILE).write_text("{not json", encoding="utf-8")
    with pytest.raises(pl.LedgerError):
        pl.load_ledger(tmp_path)
    (tmp_path / pl.LEDGER_FILE).write_text('{"foo": 1}', encoding="utf-8")
    with pytest.raises(pl.LedgerError):
        pl.load_ledger(tmp_path)
