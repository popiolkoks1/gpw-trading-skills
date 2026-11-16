"""CLI of gpw-macro-regime: arguments, contract, degradation (fake sources, no network)."""

import datetime as dt
import json

import pytest

import gpw_macro_regime as cli
import macro_sources as ms
from gpwlib import gpw_archive, http

D = dt.date
AS_OF = D(2026, 11, 13)


def bdays(start, end):
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def bonds(y2, y10):
    return [
        {"name": "OK0129", "isin": "PL0000119178", "price": 90.0, "yield": y2, "maturity": D(2029, 1, 25), "fixed": True},
        {"name": "WZ1129", "isin": "PL0000111928", "price": 98.0, "yield": None, "maturity": D(2029, 11, 25), "fixed": False},
        {"name": "DS0436", "isin": "PL0000119087", "price": 92.8, "yield": y10, "maturity": D(2036, 4, 25), "fixed": True},
    ]


class FakeFetcher:
    fail = set()
    calls = []

    def __init__(self, cache_dir=None, offline=False, getter=None, today=None):
        self.today = today

    def _chk(self, key):
        FakeFetcher.calls.append(key)
        if key in FakeFetcher.fail:
            raise http.HTTPFetchError("{} down (test)".format(key), "https://example.invalid", 503)

    def _meta(self, series):
        return {"series": series, "retrieved_at": "2026-11-13T12:00:00+00:00", "from_cache": False, "urls": ["u"]}

    def nbp_rates(self):
        self._chk("nbp_rates")
        return self._meta([(D(2023, 10, 5), 5.75), (D(2025, 5, 8), 5.25), (D(2025, 12, 4), 4.0), (D(2026, 3, 5), 3.75)])

    def nbp_fx(self, code, start, end):
        self._chk("nbp_fx_" + code.lower())
        base = {"EUR": 4.25, "USD": 3.70, "CHF": 4.6}[code]
        days = bdays(D(2023, 1, 2), end)
        n = len(days)
        return self._meta([(d, base * (1 + 0.04 * max(0, i - (n - 150)) / 150)) for i, d in enumerate(days)])

    def hicp(self, start):
        self._chk("eurostat_hicp")
        keys = ["{:04d}-{:02d}".format(2023 + (m // 12), m % 12 + 1) for m in range(0, 45)]
        return self._meta([(k, 2.5 + (0.1 * i if i > 38 else 0)) for i, k in enumerate(keys) if k <= "2026-09"])

    def ecb_pl10y(self, start):
        self._chk("ecb_pl10y")
        keys = ["{:04d}-{:02d}".format(2023 + (m // 12), m % 12 + 1) for m in range(0, 46)]
        return self._meta([(k, 5.5 + 0.02 * i) for i, k in enumerate(keys) if k <= "2026-10"])

    def bund10y(self, start):
        self._chk("bundesbank")
        return self._meta([(d, 2.6) for d in bdays(D(2023, 1, 2), AS_OF)])

    def bondspot(self, day, max_back=7):
        self._chk("bondspot")
        while day.weekday() >= 5:
            day -= dt.timedelta(days=1)
        late = day > D(2026, 10, 1)
        return {"date": day, "bonds": bonds(4.9, 6.3 if late else 5.9), "retrieved_at": "2026-11-13T12:00:00+00:00",
                "from_cache": False, "urls": ["u"]}


def fake_sessions(n, end=None, type_id=1, cache_dir=None, offline=False, progress=True):
    days = bdays(D(2025, 9, 1), end)[-n:]
    out = []
    for i, d in enumerate(days):
        rows = [{"name": name, "close": base * (1 + k * i / 1000)}
                for name, base, k in (("WIG", 100000, 1), ("WIG20", 2500, 1), ("SWIG80", 25000, 0.5),
                                      ("WIG-BANKI", 20000, 2), ("WIG-INFO", 9000, -1), ("WIG-NRCHOM", 4000, 0))]
        out.append({"date": d.isoformat(), "rows": rows, "retrieved_at": "2026-11-13T17:30:00+00:00"})
    return out


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeFetcher.fail = set()
    FakeFetcher.calls = []
    monkeypatch.setattr(ms, "Fetcher", FakeFetcher)
    monkeypatch.setattr(gpw_archive, "load_sessions", fake_sessions)


def run_json(tmp_path, *extra):
    rc = cli.main(["--as-of", AS_OF.isoformat(), "--output-dir", str(tmp_path), *extra])
    assert rc == 0
    files = sorted(tmp_path.glob("gpw_macro_regime_*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")), files


def test_full_run_contract(tmp_path):
    r, files = run_json(tmp_path)
    assert r["skill"] == "gpw-macro-regime" and r["version"] == "1.0.0" and r["lang"] == "pl"
    st = r["macro"]["state"]
    for k in ("monetary", "pln", "spread", "inflation", "composite"):
        assert k in st
    assert st["monetary"] == "hold_after_easing"
    assert st["composite"] in ("supportive", "mixed", "headwind")
    for key, ind in r["indicators"].items():
        assert {"value", "as_of", "source", "trend"} <= set(ind), key
    assert set(r["sector_tilts"]) == {"banks", "exporters", "importers_retail", "utilities_energy", "developers"}
    assert all(t["rationale"] for t in r["sector_tilts"].values())
    assert len(r["history"]) == 24 and r["history"][-1]["composite"] == st["composite"]
    assert r["macro"]["confidence"] == "high" and r["macro"]["failed_sources"] == []
    ps = r["data_provenance"]["per_source"]
    assert ps["eurostat_hicp"]["newest_observation"] == "2026-09" and ps["eurostat_hicp"]["stale"] is False
    assert r["data_provenance"]["freshness"]["historical_run"] is True
    assert r["changes_since_previous"] is None
    md = files[-1].with_suffix(".md").read_text(encoding="utf-8")
    assert md.startswith("# Reżim makro") and "Źródła" in md and md.rstrip().endswith("_")
    assert "historyczne tendencje" in md


def test_second_run_reports_changes(tmp_path):
    first, _ = run_json(tmp_path)
    prev = sorted(tmp_path.glob("*.json"))[-1]
    data = json.loads(prev.read_text(encoding="utf-8"))
    data["macro"]["state"]["monetary"] = "easing"
    data["macro"]["score"] = 0.9
    prev.write_text(json.dumps(data), encoding="utf-8")
    r, _ = run_json(tmp_path, "--lang", "en")
    ch = r["changes_since_previous"]
    assert ch["previous_file"] == prev.name
    assert {"field": "monetary", "from": "easing", "to": "hold_after_easing"} in ch["changes"]
    assert ch["score_change"] == pytest.approx(r["macro"]["score"] - 0.9, abs=1e-3)


def test_degrades_when_ecb_and_bondspot_fail(tmp_path):
    FakeFetcher.fail = {"ecb_pl10y", "bondspot"}
    r, files = run_json(tmp_path)
    m = r["macro"]
    assert set(m["failed_sources"]) == {"ecb_pl10y", "bondspot"}
    assert "spread" in m["missing_components"] and m["state"]["spread"] is None
    assert m["confidence"] == "medium"
    assert m["state"]["curve"] is None
    md = files[-1].with_suffix(".md").read_text(encoding="utf-8")
    assert md.count("Niedostępne źródło") == 2


def test_spread_falls_back_to_monthly_when_bondspot_fails(tmp_path):
    FakeFetcher.fail = {"bondspot"}
    r, _ = run_json(tmp_path)
    assert r["macro"]["details"]["spread"]["basis"] == "monthly"
    assert r["macro"]["confidence"] == "high"  # spread still available, curve is optional


def test_no_bondspot_flag_skips_source(tmp_path):
    r, _ = run_json(tmp_path, "--no-bondspot")
    assert "bondspot" not in FakeFetcher.calls
    assert "bondspot" not in r["data_provenance"]["per_source"]


def test_fails_closed_when_most_components_missing(tmp_path, capsys):
    FakeFetcher.fail = {"nbp_rates", "eurostat_hicp", "bundesbank"}
    rc = cli.main(["--as-of", AS_OF.isoformat(), "--output-dir", str(tmp_path)])
    assert rc == 1
    assert "macro components available" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.json"))


def test_gpw_index_failure_is_not_fatal(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise gpw_archive.GPWFetchError("archive down")

    monkeypatch.setattr(gpw_archive, "load_sessions", boom)
    r, _ = run_json(tmp_path)
    assert r["data_provenance"]["per_source"]["gpw_indices"]["ok"] is False
    assert "wig_usd" not in r["indicators"]
    assert r["sector_tilts"]["banks"]["market_check"]["vs_wig_3m_pp"] is None


def test_no_files_prints_markdown(tmp_path, capsys):
    rc = cli.main(["--as-of", AS_OF.isoformat(), "--output-dir", str(tmp_path), "--no-files", "--lang", "en"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("# Macro regime for Polish equities") and "Sources" in out
    assert not list(tmp_path.glob("*"))


def test_bad_previous_json_is_error(tmp_path, capsys):
    rc = cli.main(["--as-of", AS_OF.isoformat(), "--output-dir", str(tmp_path), "--previous-json", str(tmp_path / "nope.json")])
    assert rc == 1


@pytest.mark.parametrize("argv", [["--as-of", "2026-13-01"], ["--as-of", "2999-01-01"], ["--as-of", "1999-01-01"],
                                  ["--history-months", "0"], ["--history-months", "61"], ["--lang", "de"]])
def test_argument_errors_exit_2(argv):
    with pytest.raises(SystemExit) as e:
        cli.parse_args(argv)
    assert e.value.code == 2
