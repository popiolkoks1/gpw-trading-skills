"""CLI tests with every network source replaced by fixtures (no network)."""
import datetime as dt
import json
from pathlib import Path

import pytest

import event_calendar_engine as E
import event_calendar_sources as S
import gpw_event_calendar as C
from gpwlib import espi, http

FIX = Path(__file__).resolve().parent / "fixtures"
D = dt.date

PZU_ISIN = "PLPZU0000011"
SCHEDULE_ROW = {
    "time": "17:30:00", "datetime": "2026-01-13T17:30:00", "date": "2026-01-13", "category": "ESPI", "type": "RB",
    "type_label": "Bieżący", "number": "2/2026", "company": "POWSZECHNY ZAKŁAD UBEZPIECZEŃ SPÓŁKA AKCYJNA",
    "isin": PZU_ISIN, "title": "Terminy publikacji raportów okresowych w roku obrotowym 2026", "geru_id": 1,
    "url": "https://www.gpw.pl/komunikat?geru_id=1", "class": "periodic_schedule", "correction": False,
}
DIV_ROW = dict(SCHEDULE_ROW, datetime="2026-11-10T18:00:00", date="2026-11-10", number="30/2026", geru_id=2,
               title="Uchwała w sprawie wypłaty zaliczki na poczet dywidendy", **{"class": "dividend"})
SCHEDULE_BODY = {"body_text": "- skonsolidowany raport kwartalny za III kwartał 2026 r. - 19 listopada 2026 r."}
DIV_BODY = {"body_text": "Zaliczka w wysokości 1,50 zł na jedną akcję. Dzień ustalenia prawa do zaliczki "
                         "(dzień dywidendy) 3 grudnia 2026 r., termin wypłaty 10 grudnia 2026 r."}


@pytest.fixture
def offline_sources(monkeypatch, tmp_path):
    def scan(a, b, cache_dir=None, offline=False):
        rows = [r for r in (SCHEDULE_ROW, DIV_ROW) if a.isoformat() <= r["date"] <= b.isoformat()]
        days = [{"date": b.isoformat(), "count": len(rows), "pages": 1, "from_cache": True}]
        return {"rows": [dict(r) for r in rows], "days": days, "missing_days": []}

    def report(geru_id, cache_dir=None, offline=False):
        return {1: SCHEDULE_BODY, 2: DIV_BODY}.get(geru_id)

    def index_cal(year, cache_dir=None, offline=False):
        items = E.parse_index_calendar_html((FIX / "gpwbenchmark_kalendarium_2026.html").read_text(encoding="utf-8"))
        return {"year": year, "items": [i for i in items if i["date"].startswith(str(year))],
                "url": S.INDEX_CAL_URL + "?year={}".format(year), "retrieved_at": "2026-11-16T10:00:00+00:00",
                "from_cache": True}

    def archive(tickers, today, cache_dir):
        hist = {t: [] for t in tickers}
        hist["PZU"] = E.exdiv_history([{"date": "2026-09-16", "factor": 0.9377}, {"date": "2025-09-24", "factor": 0.9262}])
        return {"history": hist, "as_of": "2026-11-13", "sessions": 500, "error": None}

    monkeypatch.setattr(S, "scan_espi", scan)
    monkeypatch.setattr(S, "fetch_report", report)
    monkeypatch.setattr(S, "fetch_index_calendar", index_cal)
    monkeypatch.setattr(C, "archive_history", archive)
    monkeypatch.setattr(C.fundamentals, "isin_map", lambda *a, **k: {"PZU": PZU_ISIN, "PKOBP": "PLPKO0000016"})
    monkeypatch.setattr(C.constituents, "size_membership", lambda *a, **k: {"PZU": "WIG20", "PKOBP": "WIG20"})
    monkeypatch.setattr(C, "WATCHLIST_PATH", tmp_path / "no_watchlist.json")
    return tmp_path


def _run(argv, tmp):
    args = C.parse_args(argv + ["--as-of", "2026-11-16", "--output-dir", str(tmp), "--no-journal"])
    return C.run(args)


def test_window_mode_contract(offline_sources):
    r = _run(["window", "--ticker", "PZU", "--days", "20"], offline_sources)
    assert r["skill"] == "gpw-event-calendar" and r["version"] == "1.0.0" and "data_provenance" in r
    for e in r["events"]:
        assert set(["ticker", "isin", "type", "date", "date_kind", "title", "source"]) <= set(e)
        assert e["type"] in E.EVENT_TYPES and e["date_kind"] in E.DATE_KINDS
    w = r["holding_window_check"]
    assert w["verdict"] == "event_risk" and w["contains"]["has_earnings"] and w["contains"]["has_ex_dividend"]
    assert w["first"]["earnings"]["date"] == "2026-11-19"
    ex = [e for e in r["events"] if e["type"] == "ex_dividend"]
    assert ex[0]["date"] == "2026-12-02" and ex[0]["details"]["dps"] == 1.5   # record 3.12 -> ex 2.12
    assert any(e["type"] == "mpc_meeting" and e["date"] == "2026-12-02" for e in r["events"])


def test_ticker_mode_estimates_next_exdate_and_deadlines(offline_sources):
    r = _run(["ticker", "PKOBP"], offline_sources)
    kinds = {(e["type"], e["date_kind"]) for e in r["events"] if e["ticker"] == "PKOBP"}
    assert ("periodic_report", "statutory_deadline") in kinds      # Q3 2026 not scheduled -> 30.11.2026
    dl = [e for e in r["events"] if e["ticker"] == "PKOBP" and e["type"] == "periodic_report"]
    assert dl[0]["date"] == "2026-11-30"
    assert ("agm", "statutory_deadline") in kinds


def test_upcoming_mode_market_view(offline_sources):
    r = _run(["upcoming", "--sessions", "15"], offline_sources)
    assert r["horizon"] == {"start": "2026-11-16", "end": "2026-12-04", "sessions": 15, "company_end": "2026-12-04"}
    types = {e["type"] for e in r["events"]}
    assert {"index_revision", "mpc_meeting", "msci_review", "periodic_report"} <= types
    assert all(E.IMP_RANK[e["importance"]] >= 2 for e in r["events"])
    md = C.render_markdown(r, "pl")
    assert md.startswith("# Kalendarz") and "W ciągu 15 sesji" in md and "Źródła / Sources" in md
    assert md.rstrip().endswith(C.fmt.DISCLAIMER["pl"])


def test_portfolio_mode_reads_watchlist(offline_sources, tmp_path):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"items": [{"ticker": "pzu", "stage": "watch"}, {"ticker": "XYZ", "stage": "dropped"}]}))
    r = _run(["portfolio", "--watchlist", str(wl)], offline_sources)
    assert r["tickers"] == ["PZU"] and r["by_ticker"]["PZU"]["sources"] == ["watchlist"]
    assert r["by_ticker"]["PZU"]["next_earnings"]["date"] == "2026-11-19"


def test_main_writes_files_and_ics(offline_sources, capsys):
    rc = C.main(["portfolio", "--tickers", "PZU", "--as-of", "2026-11-16", "--output-dir", str(offline_sources),
                 "--no-journal", "--ics", "--lang", "en"])
    assert rc == 0
    files = sorted(p.suffix for p in offline_sources.glob("gpw_event_calendar_*"))
    assert files == [".ics", ".json", ".md"]
    data = json.loads(next(offline_sources.glob("gpw_event_calendar_*.json")).read_text(encoding="utf-8"))
    assert data["lang"] == "en" and data["mode"] == "portfolio"
    assert "Within 20 sessions" in capsys.readouterr().out


def test_cli_errors(offline_sources, capsys):
    with pytest.raises(SystemExit) as e:
        C.parse_args(["ticker"])
    assert e.value.code == 2
    for bad in (["window", "--ticker", "PZU", "--start", "16-11-2026"], ["upcoming", "--sessions", "0"],
                ["upcoming", "--isin", "PZU=XX123"], ["upcoming", "PZU"], ["nonsense"]):
        with pytest.raises(SystemExit) as e:
            C.parse_args(bad)
        assert e.value.code == 2


def test_portfolio_without_tickers_exit_1(offline_sources, capsys):
    rc = C.main(["portfolio", "--no-journal", "--as-of", "2026-11-16", "--no-files", "--lang", "en"])
    assert rc == 1
    assert "No tickers" in capsys.readouterr().err


def test_espi_failure_is_a_warning_not_a_crash(offline_sources, monkeypatch):
    def boom(*a, **k):
        raise http.HTTPFetchError("down")
    monkeypatch.setattr(S, "scan_espi", boom)
    r = _run(["window", "--ticker", "PZU"], offline_sources)
    assert any("ESPI" in w["en"] for w in r["warnings"])
    assert r["holding_window_check"]["contains"]["has_earnings"]   # statutory deadline still applies
