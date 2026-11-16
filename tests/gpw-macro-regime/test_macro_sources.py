"""Parsers and fetchers of gpw-macro-regime (trimmed real fixtures, no network)."""

import datetime as dt
from pathlib import Path

import pytest

import macro_sources as ms
from gpwlib import http

FX = Path(__file__).resolve().parent / "fixtures"
D = dt.date


def read(name):
    return (FX / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- NBP rates

def test_nbp_archive_parses_reference_rate_and_dedupes():
    pts = ms.parse_nbp_rates_archive(read("nbp_stopy_archiwum_trimmed.xml"))
    assert pts[-1] == (D(2026, 3, 5), 3.75)
    assert (D(2025, 12, 4), 4.0) in pts
    # consecutive equal values collapsed
    assert all(abs(a[1] - b[1]) > 1e-9 for a, b in zip(pts, pts[1:]))
    assert pts == sorted(pts)


def test_nbp_current_xml():
    cur = ms.parse_nbp_rates_current(read("nbp_stopy_current.xml"))
    assert cur["rate"] == 3.75
    assert cur["valid_from"] == D(2026, 3, 5)
    assert cur["published"] == D(2026, 3, 5)
    assert cur["trend"] == "spadek"


def test_merge_rate_history_adds_newer_current_and_dedupes():
    arch = [(D(2025, 12, 4), 4.0), (D(2025, 12, 4), 4.0)]
    merged = ms.merge_rate_history(arch, {"rate": 3.75, "valid_from": D(2026, 3, 5)})
    assert merged == [(D(2025, 12, 4), 4.0), (D(2026, 3, 5), 3.75)]
    assert ms.merge_rate_history(merged, {"rate": 3.75, "valid_from": D(2026, 3, 5)}) == merged


def test_pl_float():
    assert ms.pl_float("3,75") == 3.75
    assert ms.pl_float(" 2.5 ") == 2.5
    for bad in ("-", ".", "", None, "abc"):
        assert ms.pl_float(bad) is None


# ---------------------------------------------------------------- NBP FX

def test_parse_nbp_fx_fixture():
    pts = ms.parse_nbp_fx(read("nbp_fx_eur.json"))
    assert len(pts) == 8
    assert pts[-1] == (D(2026, 11, 13), 4.407)
    assert pts == sorted(pts)


@pytest.mark.parametrize("start,end", [(D(2023, 1, 1), D(2026, 11, 16)), (D(2026, 2, 27), D(2026, 3, 2)),
                                       (D(2024, 2, 29), D(2024, 12, 31))])
def test_date_chunks_cover_range_within_limit(start, end):
    ch = ms.date_chunks(start, end)
    assert ch[0][0] == start and ch[-1][1] == end
    for (a, b), (c, _) in zip(ch, ch[1:]):
        assert c == b + dt.timedelta(days=1)
    assert all((b - a).days + 1 <= ms.NBP_MAX_DAYS for a, b in ch)


def test_date_chunks_edges():
    assert ms.date_chunks(D(2026, 1, 2), D(2026, 1, 1)) == []
    assert ms.date_chunks(D(2026, 1, 1), D(2026, 1, 3), max_days=1) == [
        (D(2026, 1, 1), D(2026, 1, 1)), (D(2026, 1, 2), D(2026, 1, 2)), (D(2026, 1, 3), D(2026, 1, 3))]
    with pytest.raises(ValueError):
        ms.date_chunks(D(2026, 1, 1), D(2026, 1, 3), max_days=0)


def test_quarter_chunks_are_calendar_quarters_under_93_days():
    ch = ms.quarter_chunks(D(2023, 7, 1), D(2026, 11, 16))
    assert ch[0] == (D(2023, 7, 1), D(2023, 9, 30))
    assert ch[-1] == (D(2026, 10, 1), D(2026, 11, 16))
    assert all((b - a).days + 1 <= ms.NBP_MAX_DAYS for a, b in ch)
    assert all(a.month in (1, 4, 7, 10) and a.day == 1 for a, _ in ch)
    assert len(ch) == 14


class FakeResp:
    def __init__(self, text, url=""):
        self.text, self.url, self.retrieved_at, self.from_cache, self.status = text, url, "2026-11-16T10:00:00+00:00", False, 200


def fx_json(dates_vals):
    import json
    return json.dumps({"table": "A", "code": "EUR", "rates": [{"effectiveDate": d.isoformat(), "mid": v} for d, v in dates_vals]})


def test_fetcher_chunks_fx_requests_and_skips_404():
    calls = []

    def getter(url, params=None, **kw):
        calls.append((url, kw.get("ttl_seconds")))
        parts = url.rstrip("/").split("/")
        a, b = D.fromisoformat(parts[-2]), D.fromisoformat(parts[-1])
        assert (b - a).days + 1 <= ms.NBP_MAX_DAYS
        if a == D(2026, 4, 1):
            raise http.HTTPFetchError("404", url, 404)
        return FakeResp(fx_json([(b, 4.3)]))

    f = ms.Fetcher(getter=getter, today=D(2026, 11, 16))
    res = f.nbp_fx("EUR", D(2025, 11, 20), D(2026, 11, 16))
    starts = [D.fromisoformat(u.rstrip("/").split("/")[-2]) for u, _ in calls]
    assert starts[0] == D(2025, 10, 1)  # floored to the quarter -> stable cache key
    assert len(calls) == 5
    assert D(2026, 6, 30) not in [d for d, _ in res["series"]]  # 404 window skipped
    # past windows cached forever, the open quarter with a TTL
    assert calls[0][1] is None and calls[-1][1] == ms.TTL_DAILY


def test_fetcher_fx_all_empty_raises_source_error():
    def getter(url, params=None, **kw):
        raise http.HTTPFetchError("404", url, 404)

    with pytest.raises(ms.SourceError):
        ms.Fetcher(getter=getter, today=D(2026, 11, 16)).nbp_fx("USD", D(2026, 10, 1), D(2026, 11, 16))


def test_fetcher_fx_offline_finds_open_chunk_cached_earlier():
    cached_end = D(2026, 11, 13)

    def getter(url, params=None, **kw):
        parts = url.rstrip("/").split("/")
        a, b = D.fromisoformat(parts[-2]), D.fromisoformat(parts[-1])
        if b == cached_end or b == D(2026, 9, 30):
            return FakeResp(fx_json([(b, 4.4)]))
        raise http.OfflineMiss("miss", url)

    f = ms.Fetcher(getter=getter, offline=True, today=D(2026, 11, 16))
    res = f.nbp_fx("EUR", D(2026, 8, 1), D(2026, 11, 16))
    assert res["series"][-1] == (cached_end, 4.4)


def test_fetcher_fx_offline_miss_propagates():
    def getter(url, params=None, **kw):
        raise http.OfflineMiss("miss", url)

    with pytest.raises(http.OfflineMiss):
        ms.Fetcher(getter=getter, offline=True, today=D(2026, 11, 16)).nbp_fx("EUR", D(2026, 8, 1), D(2026, 11, 16))


# ---------------------------------------------------------------- ECB / Bundesbank / Eurostat

def test_parse_ecb_csv_fixture():
    pts = ms.parse_ecb_csv(read("ecb_pl10y.csv"))
    assert len(pts) == 6
    assert pts[-1] == ("2026-10", 6.32)
    with pytest.raises(ms.SourceError):
        ms.parse_ecb_csv("<html>ECB Data Portal problems</html>")


def test_parse_bundesbank_german_and_english_variants():
    de = ms.parse_bundesbank_csv(read("bundesbank_bund10y.csv"))
    en = ms.parse_bundesbank_csv(read("bundesbank_bund10y_en.csv"))
    assert de[0] == (D(2025, 10, 1), 2.75)
    assert de[-1] == (D(2026, 11, 16), 3.43)
    assert en[-1] == (D(2026, 11, 16), 3.43)
    assert all(d.weekday() < 5 for d, _ in de + en)  # '.' (no value) skipped
    with pytest.raises(ms.SourceError):
        ms.parse_bundesbank_csv("<html>error</html>")


def test_parse_eurostat_minr_fixture():
    parsed = ms.parse_eurostat_hicp(read("eurostat_hicp_pl.json"))
    yoy = dict(parsed["yoy"])
    assert yoy["2026-09"] == 4.0
    assert yoy["2025-12"] == 2.6  # ECOICOP 2 vintage (the old prc_hicp_manr said 2.5)
    idx = dict(parsed["index"])
    assert idx["2026-09"] > idx["2025-09"]
    # y/y recomputed from the index agrees with the published rate (rounding 0.1)
    rec = dict(ms.yoy_from_index(parsed["index"]))
    assert abs(rec["2026-09"] - yoy["2026-09"]) <= 0.15
    comb = dict(ms.combine_hicp(parsed))
    assert comb["2026-09"] == 4.0


def test_parse_eurostat_old_coicop_dimension():
    parsed = ms.parse_eurostat_hicp(read("eurostat_hicp_manr_old.json"))
    assert parsed["yoy"] == [("2025-10", 2.9), ("2025-11", 2.6), ("2025-12", 2.5)]
    assert parsed["index"] == []


def test_parse_eurostat_error_raises():
    with pytest.raises(ms.SourceError):
        ms.parse_eurostat_hicp('{"error":[{"status":400,"label":"INVALID_QUERY_DIMENSION"}]}')


def test_fetcher_hicp_falls_back_to_old_coicop_on_400():
    seen = []

    def getter(url, params=None, **kw):
        seen.append(dict(params))
        if ("coicop18", "TOTAL") in params:
            raise http.HTTPFetchError("400", url, 400)
        return FakeResp(read("eurostat_hicp_manr_old.json"))

    res = ms.Fetcher(getter=getter, today=D(2026, 11, 16)).hicp(D(2023, 5, 1))
    assert len(seen) == 2 and seen[1].get("coicop") == "CP00"
    assert seen[0]["sinceTimePeriod"] == "2023-01"  # floored to January: stable cache key
    assert res["series"][-1] == ("2025-12", 2.5)


# ---------------------------------------------------------------- BondSpot

def test_parse_bondspot_fixture():
    p = ms.parse_bondspot_fixing(read("bondspot_fixing_2026-11-13.html"))
    assert p["date"] == D(2026, 11, 13)
    assert len(p["bonds"]) == 31
    first = p["bonds"][0]
    assert first["name"] == "PS0527" and first["isin"] == "PL0000114393" and first["yield"] == 3.47
    wz = [b for b in p["bonds"] if b["name"].startswith("WZ")]
    assert wz and all(b["yield"] is None and not b["fixed"] for b in wz)


def test_parse_bondspot_no_session():
    p = ms.parse_bondspot_fixing(read("bondspot_no_session.html"))
    assert p["bonds"] == []


def test_bond_maturity():
    assert ms.bond_maturity("PS0729") == D(2029, 7, 25)
    assert ms.bond_maturity("DS0436") == D(2036, 4, 25)
    assert ms.bond_maturity("XX1399") is None
    assert ms.bond_maturity("bad") is None


def test_pick_benchmarks_from_fixture():
    p = ms.parse_bondspot_fixing(read("bondspot_fixing_2026-11-13.html"))
    b2 = ms.pick_benchmark(p["bonds"], p["date"], 2.0, 1.0)
    b10 = ms.pick_benchmark(p["bonds"], p["date"], 10.0, 2.0)
    assert b2["name"] == "OK0129" and abs(b2["tenor_years"] - 2.2) < 0.05
    assert b10["name"] == "DS0436" and b10["yield"] == 6.28
    assert ms.pick_benchmark(p["bonds"], p["date"], 30.0, 2.0) is None


def test_fetcher_bondspot_walks_back_over_weekend_and_offline_gaps():
    html = read("bondspot_fixing_2026-11-13.html")
    empty = read("bondspot_no_session.html")
    asked = []

    def getter(url, params=None, **kw):
        asked.append(params["date"])
        if params["date"] == "20261116":
            raise http.OfflineMiss("miss", url)
        return FakeResp(html if params["date"] == "20261113" else empty)

    res = ms.Fetcher(getter=getter, offline=True, today=D(2026, 11, 16)).bondspot(D(2026, 11, 16))
    assert res["date"] == D(2026, 11, 13)
    assert asked == ["20261116", "20261113"]  # Sat/Sun skipped without a request


def test_fetcher_bondspot_none_found():
    def getter(url, params=None, **kw):
        return FakeResp(read("bondspot_no_session.html"))

    with pytest.raises(ms.SourceError):
        ms.Fetcher(getter=getter, today=D(2026, 11, 16)).bondspot(D(2026, 11, 16), max_back=3)


def test_fetcher_nbp_rates_from_fixtures():
    def getter(url, params=None, **kw):
        return FakeResp(read("nbp_stopy_archiwum_trimmed.xml") if "archiwum" in url else read("nbp_stopy_current.xml"))

    res = ms.Fetcher(getter=getter, today=D(2026, 11, 16)).nbp_rates()
    assert res["series"][-1] == (D(2026, 3, 5), 3.75)


def test_fetcher_nbp_rates_bad_xml():
    def getter(url, params=None, **kw):
        return FakeResp("<html><script src='/_Incapsula_Resource'></script>")

    with pytest.raises(ms.SourceError):
        ms.Fetcher(getter=getter, today=D(2026, 11, 16)).nbp_rates()
