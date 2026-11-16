"""gpwlib 1.2 patch: binary downloads, TRANSD rule (see test_gpwlib_v12_espi),
full ticker <-> ISIN listing, renamed tickers in panel, open/volume, iter_reports,
working-day roll. Offline: fixtures and fake transports only."""

import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

from gpwlib import fundamentals as fu, gpw_archive, http, panel, plcal, reports  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


# --- http.get_bytes -------------------------------------------------------------------

class _FakeResp:
    def __init__(self, body: bytes, ctype="application/pdf"):
        self._body, self.status = body, 200
        self.headers = {"Content-Type": ctype}

    def read(self):
        return self._body

    def getcode(self):
        return 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def transport(monkeypatch):
    calls, waits = [], []
    body = {"value": b"%PDF-1.4\n\x00\xff\xfe\x80binary\x9c\r\n%%EOF"}

    def fake_urlopen(req, timeout=None):
        calls.append({"url": req.full_url, "ua": req.get_header("User-agent"), "method": req.get_method()})
        return _FakeResp(body["value"])

    monkeypatch.setattr(http, "_urlopen", fake_urlopen)
    monkeypatch.setattr(http.LIMITER, "wait", lambda host: waits.append(host) or 0.0)
    return calls, waits, body


def test_get_bytes_keeps_exact_bytes_and_caches(tmp_path, transport):
    calls, waits, body = transport
    raw = body["value"]
    r = http.get_bytes("https://example.org/f.pdf", {"y": 2025}, cache_dir=tmp_path, ttl_seconds=None)
    assert r.content == raw and not r.from_cache and r.status == 200
    assert calls[0]["url"] == "https://example.org/f.pdf?y=2025" and calls[0]["ua"] == http.USER_AGENT
    assert waits == ["example.org"]  # same polite limiter as get()
    bins = list((tmp_path / "http" / "example.org").glob("*.bin"))
    assert len(bins) == 1 and bins[0].read_bytes() == raw
    # cached: no new request, identical bytes, also offline
    r2 = http.get_bytes("https://example.org/f.pdf", {"y": 2025}, cache_dir=tmp_path, ttl_seconds=None)
    r3 = http.get_bytes("https://example.org/f.pdf", {"y": 2025}, cache_dir=tmp_path, offline=True)
    assert r2.content == raw and r2.from_cache and r3.content == raw and len(calls) == 1
    # the text cache of get() is separate
    with pytest.raises(http.OfflineMiss):
        http.get("https://example.org/f.pdf", {"y": 2025}, cache_dir=tmp_path, offline=True)
    with pytest.raises(http.OfflineMiss):
        http.get_bytes("https://example.org/other.pdf", cache_dir=tmp_path, offline=True)


def test_get_bytes_ttl_and_drop_cached(tmp_path, transport):
    calls, _, body = transport
    http.get_bytes("https://example.org/x.xlsx", cache_dir=tmp_path, ttl_seconds=3600)
    body["value"] = b"PK\x03\x04new"
    assert http.get_bytes("https://example.org/x.xlsx", cache_dir=tmp_path, ttl_seconds=0).content == b"PK\x03\x04new"
    assert len(calls) == 2
    assert http.drop_cached("https://example.org/x.xlsx", cache_dir=tmp_path) == 2  # .bin + .bin.json
    with pytest.raises(http.OfflineMiss):
        http.get_bytes("https://example.org/x.xlsx", cache_dir=tmp_path, offline=True)


def test_get_bytes_truncated_cache_is_refetched(tmp_path, transport):
    calls, _, body = transport
    http.get_bytes("https://example.org/z.zip", cache_dir=tmp_path, ttl_seconds=None)
    b = next((tmp_path / "http" / "example.org").glob("*.bin"))
    b.write_bytes(body["value"][:3])
    assert http.get_bytes("https://example.org/z.zip", cache_dir=tmp_path, ttl_seconds=None).content == body["value"]
    assert len(calls) == 2


def test_response_content_and_lazy_text(tmp_path, transport):
    _, _, body = transport
    body["value"] = "Zażółć".encode("utf-8")
    r = http.get("https://example.org/t.html", cache_dir=tmp_path)
    assert r.text == "Zażółć" and r.content == "Zażółć".encode("utf-8")
    cached = http.get("https://example.org/t.html", cache_dir=tmp_path, offline=True)
    assert cached.content == "Zażółć".encode("utf-8")  # re-encoded from the cached text
    b = http.Response("u", 200, None, "", "t", False, {"content-type": "text/plain; charset=utf-8"},
                      content="ąę".encode("utf-8"))
    assert b.text == "ąę"
    old_style = http.Response("u", 200, "abc", "utf-8", "t", False)  # 1.1 positional signature still works
    assert old_style.text == "abc" and old_style.content == b"abc" and old_style.json is not None


# --- full ticker <-> ISIN listing -------------------------------------------------------

def _listing_http(monkeypatch, fail_nc=False):
    calls = []
    pages = {"showTable": "gpw_listing_main_2026-11-16.html", "fix1": None, "fix2": "gpw_listing_fix2_2026-11-16.html",
             "NCExternalDataFrontController": "gpw_listing_nc_2026-11-16.html"}

    def fake_get(url, params=None, **kw):
        calls.append((url, dict(params or {}), kw))
        p = dict(params or {})
        if p.get("action") == "NCExternalDataFrontController":
            if fail_nc:
                raise http.HTTPFetchError("down", url, 503)
            name = pages["NCExternalDataFrontController"]
        elif p.get("type") in ("fix1", "fix2"):
            name = pages[p["type"]]
        else:
            name = pages["showTable"]
        if kw.get("offline") and not calls_allowed_offline[0]:
            raise http.OfflineMiss("x", url)
        text = (FIX / name).read_text(encoding="utf-8") if name else "<table></table>"
        return http.Response(url, 200, text, "utf-8", "2026-11-16T12:00:00+00:00", False)

    calls_allowed_offline = [True]
    monkeypatch.setattr(http, "get", fake_get)
    return calls, calls_allowed_offline


def test_parse_listing_real_fixtures():
    rows = fu.parse_listing_html((FIX / "gpw_listing_main_2026-11-16.html").read_text(encoding="utf-8"))
    by = {r["ticker"]: r for r in rows}
    assert by["PULAWY"]["isin"] == "PLZAPUL00057" and by["DEBICA"]["isin"] == "PLDEBCA00016"
    assert by["11BIT"] == {"ticker": "11BIT", "isin": "PL11BTS00015", "code": "11B", "market": "main"}
    assert by["MODIVO"]["isin"] == "PLCCC0000016" and by["SANTANDER"]["isin"] == "ES0113900J37"
    fix2 = fu.parse_listing_html((FIX / "gpw_listing_fix2_2026-11-16.html").read_text(encoding="utf-8"))
    assert fix2[0]["ticker"] == "IZOBLOK"  # status flag "/Z" stripped from the link text
    nc = fu.parse_listing_html((FIX / "gpw_listing_nc_2026-11-16.html").read_text(encoding="utf-8"), "newconnect")
    assert {r["ticker"] for r in nc} == {"1SOLUTION", "4MOBILITY"} and nc[0]["market"] == "newconnect"
    assert fu.parse_listing_html("<html>no table</html>") == []


def test_listed_companies_cached_7_days(tmp_path, monkeypatch):
    calls, _ = _listing_http(monkeypatch)
    out = fu.listed_companies(tmp_path)
    assert out["counts"] == {"main": 12, "newconnect": 2} and out["complete"] is False  # fix1 page empty
    assert len(calls) == 4 and all(c[2]["ttl_seconds"] == fu.LISTING_TTL_S for c in calls)
    assert (tmp_path / "fundamentals" / "listed_companies.json").exists()
    again = fu.listed_companies(tmp_path)
    assert again["from_cache"] and len(calls) == 4  # parsed file is fresh: no request
    fu.listed_companies(tmp_path, refresh=True)
    assert len(calls) == 8 and calls[-1][2]["ttl_seconds"] == 0
    # stale file (> 7 days) triggers a refetch; offline never does
    p = tmp_path / "fundamentals" / "listed_companies.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["retrieved_at"] = "2026-11-01T00:00:00+00:00"
    p.write_text(json.dumps(d), encoding="utf-8")
    assert fu.listed_companies(tmp_path, offline=True)["from_cache"] and len(calls) == 8
    fu.listed_companies(tmp_path)
    assert len(calls) == 12
    assert {c["market"] for c in fu.listed_companies(tmp_path, include_newconnect=False)["companies"]} == {"main"}


def test_listed_companies_network_failure_never_raises(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise http.HTTPFetchError("down", "u", 503)
    monkeypatch.setattr(http, "get", boom)
    assert fu.listed_companies(tmp_path)["companies"] == []


def _portfolio(cache, isin, members):
    p = cache / "constituents" / "{}.json".format(isin)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"isin": isin, "members": members, "retrieved_on": "2026-11-16",
                             "source_url": "x"}), encoding="utf-8")


def _cache_only_portfolios(monkeypatch):
    """GPW Benchmark portfolios come from the test cache only (never the network)."""
    real = fu.constituents.fetch_portfolio

    def fake(isin, cache_dir=None, offline=False, *a, **kw):
        return real(isin, cache_dir, True, *a, **kw)
    monkeypatch.setattr(fu.constituents, "fetch_portfolio", fake)


def test_isin_map_all_listed_plus_fallbacks(tmp_path, monkeypatch):
    _listing_http(monkeypatch)
    _cache_only_portfolios(monkeypatch)
    _portfolio(tmp_path, fu.WIG_ISIN, [{"name": "KETY", "isin": "PLKETY000011", "weight_pct": 1.0},
                                       {"name": "DEBICA", "isin": "PLXXXXXXXXX0", "weight_pct": 0.1}])
    m = fu.isin_map(tmp_path)
    assert m["PULAWY"] == "PLZAPUL00057" and m["DEBICA"] == "PLDEBCA00016"  # listing wins over portfolios
    assert m["KETY"] == "PLKETY000011"                                        # portfolio fallback
    assert m["1SOLUTION"] == "PLONESL00011"                                   # NewConnect
    assert m["CCC"] == "PLCCC0000016" and m["SANPL"] == "PLBZ00000044"        # pre-rename aliases
    assert m["LESS"] == m["GROCLIN"] == "PLINTGR00013"                        # alias chain
    assert fu.ticker_for_isin("PLCCC0000016", tmp_path) == "MODIVO"           # current name, not alias
    assert fu.isin_to_ticker_map(tmp_path)["PLBZ00000044"] == "ERSTEPL"
    assert fu.resolve_isin("pulawy", tmp_path, offline=True) == {"ticker": "PULAWY", "isin": "PLZAPUL00057",
                                                                  "source": "gpw_listing"}
    assert fu.resolve_isin("CCC", tmp_path, offline=True)["source"] == "rename_alias"
    assert fu.resolve_isin("KETY", tmp_path, offline=True)["source"] == "gpwbenchmark"
    fu.remember_isin("DEBICA", "PLOPTTC00011", tmp_path)
    assert fu.resolve_isin("DEBICA", tmp_path, offline=True)["source"] == "override"
    with pytest.raises(fu.FundamentalsError) as ei:
        fu.resolve_isin("NIEMA", tmp_path, offline=True)
    assert "--isin" in str(ei.value)


def test_normalize_company_name():
    assert fu.normalize_company_name("Grupa Azoty Zakłady Azotowe Puławy S.A.") == "AZOTYZAKLADYAZOTOWEPULAWY"
    assert fu.normalize_company_name("FIRMA OPONIARSKA DĘBICA SPÓŁKA AKCYJNA") == "FIRMAOPONIARSKADEBICA"
    assert fu.normalize_company_name("PEPCO GROUP N.V.") == "PEPCO"
    assert fu.normalize_company_name("KRUK SA") == fu.normalize_company_name("Kruk S.A.") == "KRUK"
    assert fu.normalize_company_name("PBG SPÓŁKA AKCYJNA W RESTRUKTURYZACJI W LIKWIDACJI") == "PBG"


def test_ticker_for_company_name():
    pool = ["PULAWY", "DEBICA", "11BIT", "MODIVO", "ERSTEPL", "ASSECOPOL", "ASSECOSEE", "PKNORLEN", "KRUK",
            "VIGOPHOTN", "PHOTON", "ENAP", "ENERGA", "BUMECHDEF", "CDPROJEKT", "POLICE", "PZU"]
    f = fu.ticker_for_company_name
    assert f("MODIVO SPÓŁKA AKCYJNA", tickers=pool) == "MODIVO"                    # exact
    assert f("11 BIT STUDIOS SPÓŁKA AKCYJNA", tickers=pool) == "11BIT"             # prefix
    assert f("ASSECO POLAND SPÓŁKA AKCYJNA", tickers=pool) == "ASSECOPOL"
    assert f("FIRMA OPONIARSKA DĘBICA SPÓŁKA AKCYJNA", tickers=pool) == "DEBICA"  # whole word
    assert f("ERSTE BANK POLSKA SPÓŁKA AKCYJNA", tickers=pool) == "ERSTEPL"       # abbreviation
    assert f("VIGO PHOTONICS SPÓŁKA AKCYJNA", tickers=pool) == "VIGOPHOTN"        # not PHOTON (mid-word)
    assert f("ORLEN S.A.", tickers=pool) == "PKNORLEN"
    assert f("BUMECH DEFENSE PARTNERS SPÓŁKA AKCYJNA", tickers=pool) == "BUMECHDEF"
    assert f("ENERGOAPARATURA SPÓŁKA AKCYJNA", tickers=pool) is None              # ENAP? ENERGA? -> ambiguous
    assert f("POWSZECHNY ZAKŁAD UBEZPIECZEŃ SPÓŁKA AKCYJNA", tickers=pool) is None  # no guess (not POLICE)
    assert f("", tickers=pool) is None


# --- panel: open, volume, renames ---------------------------------------------------------

def _sess(date, rows):
    return {"date": date, "rows": [dict(currency="PLN", **r) for r in rows]}


def test_series_open_adjusted_like_close():
    sessions = [
        _sess("2026-01-05", [{"name": "ABC", "open": 98.0, "high": 101.0, "low": 97.0, "close": 100.0, "change_pct": 0.0,
                              "turnover_k": 10.0, "volume": 1000}]),
        # 2:1 split: reference halves, change +2%
        _sess("2026-01-06", [{"name": "ABC", "open": 50.0, "high": 52.0, "low": 49.5, "close": 51.0, "change_pct": 2.0,
                              "turnover_k": 12.0}]),
    ]
    s = panel.build_panel(sessions)["ABC"]
    assert s.close[0] == pytest.approx(50.0) and s.open[0] == pytest.approx(49.0) and s.high[0] == pytest.approx(50.5)
    assert s.open[1] == 50.0 and s.volume == [1000, None]
    raw = panel.build_panel(sessions, adjust=False)["ABC"]
    assert raw.open == [98.0, 50.0]
    # an open outside high/low (archive artefact) is clamped; a missing open falls back to close
    s2 = panel.build_panel([_sess("2026-01-05", [{"name": "X", "open": 0.0, "high": 11.0, "low": 9.0, "close": 10.0},
                                                 {"name": "Y", "open": 15.0, "high": 11.0, "low": 9.0, "close": 10.0}])])
    assert s2["X"].open == [10.0] and s2["Y"].open == [11.0]


def test_adjust_series_backward_compatible_without_open():
    s = panel.Series("OLD")  # callers (gpw-backtester) fill close/high/low only
    s.dates, s.close, s.high, s.low, s.turnover_k = ["a", "b"], [100.0, 51.0], [100.0, 51.0], [100.0, 51.0], [0, 0]
    panel._adjust_series(s, [None, 2.0])
    assert s.close[0] == pytest.approx(50.0) and s.open == [] and s.adjustments[0]["factor"] == pytest.approx(0.5)


def _rename_sessions():
    return [
        _sess("2026-02-17", [{"name": "CCC", "open": 126, "high": 128, "low": 125, "close": 127.0, "change_pct": 0.5,
                              "turnover_k": 100000.0}]),
        _sess("2026-02-18", [{"name": "CCC", "open": 127, "high": 128, "low": 126, "close": 127.05, "change_pct": 0.04,
                              "turnover_k": 117343.15}]),
        _sess("2026-02-19", [{"name": "MODIVO", "open": 127, "high": 127, "low": 124, "close": 125.0, "change_pct": -1.61,
                              "turnover_k": 46286.34}]),
        _sess("2026-02-20", [{"name": "MODIVO", "open": 125, "high": 126, "low": 124, "close": 126.0, "change_pct": 0.8,
                              "turnover_k": 50000.0},
                             # a later, unrelated company reusing an old name must not be merged
                             {"name": "CCC", "open": 5, "high": 5, "low": 5, "close": 5.0, "change_pct": 0.0,
                              "turnover_k": 1.0}]),
    ]


def test_build_panel_stitches_renames():
    pnl = panel.build_panel(_rename_sessions())
    s = pnl["MODIVO"]
    assert s.dates == ["2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20"]
    assert s.renamed_from == [{"name": "CCC", "first_date": "2026-02-17", "last_date": "2026-02-18"}]
    assert s.adjustments == []  # the reference price carried over: no false corporate action at the join
    assert pnl["CCC"].dates == ["2026-02-20"]  # the reused name after last_old_date stays separate
    split = panel.build_panel(_rename_sessions(), merge_renames=False)
    assert split["CCC"].dates == ["2026-02-17", "2026-02-18", "2026-02-20"] and len(split["MODIVO"]) == 2
    assert split["MODIVO"].renamed_from == []
    custom = panel.build_panel(_rename_sessions(), renames={})
    assert len(custom["MODIVO"]) == 2


def test_canonical_name_chains():
    assert panel.canonical_name("IQP", "2020-01-02") == "VIRTUS"         # IQP -> PUNKPIRAT -> RAEN -> VIRTUS
    assert panel.canonical_name("RAEN", "2026-02-12") == "VIRTUS"
    assert panel.canonical_name("RAEN", "2026-02-13") == "RAEN"          # after the rename: not remapped
    assert panel.canonical_name("SANPL", "2025-06-02") == "ERSTEPL"
    assert panel.canonical_name("PKOBP", "2025-06-02") == "PKOBP"
    for new, olds in panel.RENAMES.items():                              # table sanity
        for old, last in olds:
            assert old != new and dt.date.fromisoformat(last)


def test_detect_rename_candidates_reports_only():
    sess = _rename_sessions()[:3]
    sess[2]["rows"].append(dict(name="NEWLIST", currency="PLN", close=10.0, change_pct=0.0, turnover_k=1.0))
    cands = panel.detect_rename_candidates(sess)
    assert [(c["old"], c["new"], c["last_old_date"], c["first_new_date"]) for c in cands] == \
        [("CCC", "MODIVO", "2026-02-18", "2026-02-19")]
    assert cands[0]["known"] is True and cands[0]["price_gap"] < 0.001
    assert panel.detect_rename_candidates(sess, renames={})[0]["known"] is False
    # a name that only skipped a session is not a rename
    gap = [_sess("d1", [{"name": "A", "close": 10.0, "change_pct": 0.0}]),
           _sess("d2", [{"name": "B", "close": 10.0, "change_pct": 0.0}]),
           _sess("d3", [{"name": "A", "close": 10.0, "change_pct": 0.0}])]
    assert panel.detect_rename_candidates(gap) == []
    # very different turnover profile -> not a candidate
    tv = [_sess("d1", [{"name": "A", "close": 10.0, "change_pct": 0.0, "turnover_k": 5000.0}]),
          _sess("d2", [{"name": "B", "close": 10.0, "change_pct": 0.0, "turnover_k": 1.0}])]
    assert panel.detect_rename_candidates(tv) == []


def test_real_archive_fixture_has_open():
    rows = gpw_archive.parse_archive_html((FIX / "gpw_archive_shares_2026-11-13.html").read_text(encoding="utf-8"))
    pnl = panel.build_panel([{"date": "2026-11-13", "rows": rows}])
    s = next(iter(pnl.values()))
    assert len(s.open) == len(s.close) == 1 and s.open[0] > 0 and s.volume == [None]


# --- reports.iter_reports -------------------------------------------------------------------

def test_iter_reports(tmp_path):
    def w(name, data, age=0):
        p = tmp_path / name
        p.write_text(json.dumps(data), encoding="utf-8")
        t = time.time() - age
        os.utime(p, (t, t))
    w("a.json", {"skill": "gpw-trend-screener", "as_of": "2026-11-10"})
    w("b.json", {"skill": "gpw-trend-screener", "as_of": "2026-11-13"})
    w("c.json", {"skill": "gpw-trend-screener", "as_of": "2026-11-13"}, age=100)
    w("d.json", {"skill": "gpw-market-regime", "as_of": "2026-11-12"})
    w("e.json", {"no": "skill"})
    (tmp_path / "f.json").write_text("{broken", encoding="utf-8")
    got = reports.iter_reports(tmp_path, "gpw-trend-screener")
    assert [r["file"] for r in got] == ["a.json", "c.json", "b.json"]
    assert got[0]["as_of"] == "2026-11-10" and got[0]["data"]["skill"] == "gpw-trend-screener"
    assert [r["file"] for r in reports.iter_reports(tmp_path, "gpw-trend-screener", newest_first=True)][0] == "b.json"
    assert [r["file"] for r in reports.iter_reports(tmp_path, "gpw-trend-screener",
                                                    reference=dt.date(2026, 11, 12))] == ["a.json"]
    assert {r["skill"] for r in reports.iter_reports(tmp_path)} == {"gpw-trend-screener", "gpw-market-regime"}
    assert reports.iter_reports(tmp_path / "missing", "x") == [] and reports.iter_reports(None) == []


# --- plcal working days (statutory deadlines) --------------------------------------------------

def test_working_day_roll():
    assert plcal.roll_to_working_day(dt.date(2026, 11, 29)) == dt.date(2026, 11, 30)   # Sunday
    assert plcal.roll_to_working_day(dt.date(2026, 5, 30)) == dt.date(2026, 6, 1)      # Saturday
    assert plcal.roll_to_working_day(dt.date(2028, 4, 30)) == dt.date(2028, 5, 2)      # Sun + 1 May
    assert plcal.is_working_day(dt.date(2026, 4, 3))                                   # Good Friday: exchange-only
    assert plcal.is_working_day(dt.date(2026, 12, 31))
    assert not plcal.is_working_day(dt.date(2026, 12, 24))                             # public holiday from 2025
    assert plcal.is_working_day(dt.date(2024, 12, 24))


def test_dropped_connection_becomes_typed_error(monkeypatch):
    """IncompleteRead (http.client.HTTPException, not OSError) must not escape as a crash."""
    import http.client as hc
    import urllib.request as ur
    from gpwlib import gpw_archive as ga
    from gpwlib import http as gh

    def boom(*a, **k):
        raise hc.IncompleteRead(b"partial", 100)
    monkeypatch.setattr(ur, "urlopen", boom)
    monkeypatch.setattr(ga.time, "sleep", lambda s: None)
    with pytest.raises(ga.GPWFetchError):
        ga._http_get("https://example.invalid/x", retries=2)
    monkeypatch.setattr(gh, "_urlopen", boom)
    monkeypatch.setattr(gh, "_sleep", lambda s: None)
    monkeypatch.setattr(gh.LIMITER, "wait", lambda host: None)
    with pytest.raises(gh.HTTPFetchError):
        gh.get("https://example.invalid/y", None, cache_dir=None, use_cache=False, retries=2)
