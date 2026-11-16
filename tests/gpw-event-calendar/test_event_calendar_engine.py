import datetime as dt
from pathlib import Path

import pytest

import event_calendar_engine as E
import event_calendar_sources as S

D = dt.date
FIX = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------- dates

@pytest.mark.parametrize("text,expected", [
    ("w dniu 5 marca 2026 r.", [D(2026, 3, 5)]),
    ("Dzień dywidendy: 05.08.2026", [D(2026, 8, 5)]),
    ("termin wypłaty 13-08-2026 r.", [D(2026, 8, 13)]),
    ("na dzień 2026-12-10", [D(2026, 12, 10)]),
    ("odbędzie się 10 grudnia 2026r. o godz. 11:00", [D(2026, 12, 10)]),
    ("to be held on 10 December 2026 at 11:00", [D(2026, 12, 10)]),
    ("on December 10, 2026", [D(2026, 12, 10)]),
    ("od 1-go lipca 2026 roku", [D(2026, 7, 1)]),
    ("dnia 5.III.2026 r.", [D(2026, 3, 5)]),
    ("31 lutego 2026 r. i 28 lutego 2026 r.", [D(2026, 2, 28)]),   # invalid day skipped
    ("raport 2/2026 za 2025 rok", []),                               # numbers that are not dates
    ("19 listopada 2026 r. oraz 26 listopada 2026 r.", [D(2026, 11, 19), D(2026, 11, 26)]),
])
def test_find_dates_polish_formats(text, expected):
    assert [d for d, _s, _e in E.find_dates(text)] == expected


def test_two_digit_year_only_when_allowed():
    assert E.find_dates("13 lip 26") == []
    assert E.find_dates("13 lip 26", allow_two_digit_year=True)[0][0] == D(2026, 7, 13)


def test_find_time():
    assert E.find_time("na godz. 11:00 w Warszawie") == "11:00"
    assert E.find_time("o godzinie 9.30") == "09:30"
    assert E.find_time("bez godziny") is None


# ---------------------------------------------------------------- schedules

PKO_STYLE = """Zarząd PKO Banku Polskiego S.A. informuje, że raporty okresowe w 2026 roku
będą przekazywane w następujących terminach:
- skonsolidowany raport roczny za 2025 rok – 5 marca 2026 r.,
- skonsolidowany raport za I kwartał 2026 r. – 6 maja 2026 r.,
- skonsolidowany raport półroczny za I półrocze 2026 r. – 6 sierpnia 2026 r.,
- skonsolidowany raport za III kwartał 2026 r. – 5 listopada 2026 r.
Zgodnie z § 83 ust. 2 Bank nie będzie publikował raportów za II i IV kwartał."""

PZU_STYLE = """Raport roczny:
- jednostkowy raport roczny oraz skonsolidowany raport roczny za rok obrotowy 2025
- 26 lutego 2026 r.
Raporty kwartalne zawierające skrócone jednostkowe informacje finansowe:
- skonsolidowany raport kwartalny za I kwartał 2026 r. - 14 maja 2026 r.,
- skonsolidowany raport kwartalny za III kwartał 2026 r. - 19 listopada 2026 r.
Raport półroczny zawierający skrócone jednostkowe informacje finansowe:
- skonsolidowany raport półroczny za I półrocze 2026 r. - 20 sierpnia 2026 r."""


def _pairs(items):
    return [(i["period"], i["date"], i["fiscal_year"]) for i in items]


def test_schedule_list_period_first():
    got = _pairs(E.parse_schedule_text(PKO_STYLE, D(2026, 1, 20)))
    assert got == [("FY", "2026-03-05", 2025), ("Q1", "2026-05-06", 2026), ("H1", "2026-08-06", 2026),
                   ("Q3", "2026-11-05", 2026)]


def test_schedule_grouped_headers():
    got = _pairs(E.parse_schedule_text(PZU_STYLE, D(2026, 1, 13)))
    assert ("FY", "2026-02-26", 2025) in got and ("H1", "2026-08-20", 2026) in got
    assert ("Q3", "2026-11-19", 2026) in got and ("Q1", "2026-05-14", 2026) in got


def test_schedule_date_first_and_numeric():
    txt = "Terminy: 21.04.2026 - raport roczny za 2025 r.; 27.05.2026 - raport za 1Q 2026; 23.09.2026 - raport półroczny"
    got = _pairs(E.parse_schedule_text(txt, D(2026, 1, 30)))
    assert got == [("FY", "2026-04-21", 2025), ("Q1", "2026-05-27", 2026), ("H1", "2026-09-23", 2026)]


def test_schedule_change_notice_keeps_new_date():
    txt = ("Zarząd informuje o zmianie terminu publikacji raportu za III kwartał 2026 r., określonego w raporcie "
           "bieżącym nr 2/2026 z dnia 20 stycznia 2026 r. Pierwotny termin 19 listopada 2026 r. "
           "Nowy termin publikacji: 26 listopada 2026 r.")
    assert _pairs(E.parse_schedule_text(txt, D(2026, 11, 10))) == [("Q3", "2026-11-26", 2026)]


def test_schedule_change_notice_na_dzien():
    txt = ("Zmiana terminu publikacji raportu okresowego za 3Q 2026 r. Raport zostanie opublikowany "
           "na dzień 27 listopada 2026 r. zamiast 20 listopada 2026 r.")
    got = _pairs(E.parse_schedule_text(txt, D(2026, 11, 12)))
    assert got == [("Q3", "2026-11-27", 2026)]


def test_schedule_q4_fiscal_year():
    got = _pairs(E.parse_schedule_text("raport za IV kwartał 2026 r. - 27 lutego 2027 r.", D(2026, 12, 20)))
    assert got == [("Q4", "2027-02-27", 2026)]


def test_schedule_english():
    got = _pairs(E.parse_schedule_text("Q1 FY2027 results will be published on 19 November 2026.", D(2026, 11, 13)))
    assert got and got[0][0] == "Q1" and got[0][1] == "2026-11-19"


# ---------------------------------------------------------------- dividends

def test_dividend_resolution_with_record_and_payment():
    body = ("Zwyczajne Walne Zgromadzenie postanowiło przeznaczyć kwotę 7 675 000 000,00 zł, tj. 6,14 zł na jedną "
            "akcję, na wypłatę dywidendy. Dzień dywidendy ustalono na 5 sierpnia 2026 r., a termin wypłaty "
            "dywidendy na 13 sierpnia 2026 r.")
    r = E.parse_dividend_text("Uchwała ZWZ w sprawie podziału zysku za rok 2025", body, D(2026, 6, 29))
    assert r["stage"] == "resolution" and r["dps"] == 6.14 and r["currency"] == "PLN"
    assert r["record_date"] == "2026-08-05" and r["ex_date"] == "2026-08-04" and r["last_cum_date"] == "2026-08-03"
    assert r["payment_dates"] == ["2026-08-13"] and r["fiscal_year"] == 2025


def test_dividend_proposal_two_tranches_numeric_dates():
    body = ("Zarząd rekomenduje wypłatę dywidendy w wysokości 48,96 zł na akcję. Proponowany dzień ustalenia prawa "
            "do dywidendy: 19.08.2026. Wypłata dywidendy nastąpi w dwóch ratach: 03.09.2026 i 04.11.2026.")
    r = E.parse_dividend_text("Rekomendacja Zarządu dotycząca podziału zysku za 2025 r.", body, D(2026, 3, 10))
    assert r["stage"] == "proposal" and r["dps"] == 48.96
    assert r["record_date"] == "2026-08-19" and r["ex_date"] == "2026-08-18"
    assert r["payment_dates"] == ["2026-09-03", "2026-11-04"]


def test_dividend_amount_after_keyword_and_groszy():
    r = E.parse_dividend_text("Wypłata zaliczki na poczet dywidendy",
                              "Kwota zaliczki na jedną akcję wynosi 50 groszy. Dzień ustalenia prawa 2 grudnia 2026 r., "
                              "dzień wypłaty 10 grudnia 2026 r.", D(2026, 11, 10))
    assert r["stage"] == "interim" and r["dps"] == 0.5
    assert r["record_date"] == "2026-12-02" and r["ex_date"] == "2026-12-01" and r["payment_dates"] == ["2026-12-10"]


def test_dividend_english():
    r = E.parse_dividend_text("Dividend resolution", "The AGM resolved to pay a dividend of PLN 4.80 per share. "
                              "Record date: 17 September 2026. Payment date: 8 October 2026.", D(2026, 6, 18))
    assert r["dps"] == 4.8 and r["record_date"] == "2026-09-17" and r["payment_dates"] == ["2026-10-08"]
    assert r["ex_date"] == "2026-09-16"   # matches the 2026 PZU price cut in the GPW archive


def test_dividend_without_dates_is_empty_not_invented():
    r = E.parse_dividend_text("Przyjęcie nowej polityki dywidendy", "Polityka zakłada wypłatę 50-70% zysku.", D(2026, 3, 1))
    assert r["stage"] == "policy" and r["record_date"] is None and r["payment_dates"] == [] and r["dps"] is None


# ---------------------------------------------------------------- AGM

def test_agm_title_date():
    r = E.parse_agm_text("Informacja o zwołaniu Nadzwyczajnego Walnego Zgromadzenia Trakcja S.A. na 7 grudnia 2026 "
                         "roku wraz z projektami uchwał", "", D(2026, 11, 10))
    assert r == {"date": "2026-12-07", "time": None, "kind": "extraordinary"}


def test_agm_numeric_title_and_body_time():
    r = E.parse_agm_text("Ogłoszenie o zwołaniu NWZ PHS Hydrotor SA - 10-12-2026 r.", "", D(2026, 11, 13))
    assert r["date"] == "2026-12-10"
    r2 = E.parse_agm_text("Zwołanie Zwyczajnego Walnego Zgromadzenia Spółki",
                          "Zarząd zwołuje na dzień 18 czerwca 2026 r., na godz. 12:00, Zwyczajne Walne Zgromadzenie, "
                          "zgodnie z raportem z dnia 20 maja 2026 r.", D(2026, 5, 21))
    assert r2 == {"date": "2026-06-18", "time": "12:00", "kind": "annual"}


def test_agm_ignores_dates_before_publication():
    r = E.parse_agm_text("Zwołanie NWZ", "W nawiązaniu do raportu z dnia 3 listopada 2026 r. Zarząd zwołuje "
                         "Walne Zgromadzenie na dzień 9 grudnia 2026 r.", D(2026, 11, 12))
    assert r["date"] == "2026-12-09"


# ---------------------------------------------------------------- statutory deadlines

def test_statutory_holidays():
    h = E.statutory_holidays(2026)
    assert D(2026, 12, 24) in h and D(2026, 11, 11) in h
    assert D(2026, 4, 3) not in h        # Good Friday: exchange holiday, not a public one
    assert D(2026, 12, 31) not in h
    assert D(2024, 12, 24) not in E.statutory_holidays(2024)


def test_deadlines_2026_with_weekend_roll():
    d = {x["period"]: x for x in E.periodic_deadlines(2026)}
    assert d["Q1"]["nominal_deadline"] == "2026-05-30" and d["Q1"]["deadline"] == "2026-06-01"  # Sat -> Mon
    assert d["H1"]["deadline"] == "2026-09-30"
    assert d["Q3"]["nominal_deadline"] == "2026-11-29" and d["Q3"]["deadline"] == "2026-11-30"  # Sun -> Mon
    assert d["FY"]["deadline"] == "2027-04-30"
    assert "Q2" not in d and "Q4" not in d      # § 83 ust. 2


def test_deadline_rolls_over_holidays():
    d = {x["period"]: x for x in E.periodic_deadlines(2027)}
    # 30.04.2028 Sunday -> 1 May holiday -> 2 May
    assert d["FY"]["nominal_deadline"] == "2028-04-30" and d["FY"]["deadline"] == "2028-05-02"
    assert d["Q1"]["deadline"] == "2027-05-31"


def test_add_months_and_agm_deadline():
    assert E.add_months(D(2026, 12, 31), 4) == D(2027, 4, 30)
    assert E.add_months(D(2026, 1, 31), 1) == D(2026, 2, 28)
    assert E.agm_deadline(2026) == D(2027, 6, 30)


def test_period_of_report():
    assert E.period_of_report(D(2026, 11, 5), "Q") == ("Q3", 2026)
    assert E.period_of_report(D(2026, 8, 13), "P") == ("H1", 2026)
    assert E.period_of_report(D(2026, 3, 5), "R") == ("FY", 2025)
    assert E.period_of_report(D(2026, 5, 14), "Q") == ("Q1", 2026)


# ---------------------------------------------------------------- ex-date math (T+2)

def test_exdate_from_record_simple():
    x = E.exdiv_from_record(D(2026, 8, 5))
    assert x["ex_date"] == "2026-08-04" and x["last_cum_date"] == "2026-08-03"


def test_exdate_across_holiday_and_weekend():
    x = E.exdiv_from_record(D(2026, 11, 12))           # 11.11 holiday
    assert x["ex_date"] == "2026-11-10" and x["last_cum_date"] == "2026-11-09"
    y = E.exdiv_from_record(D(2026, 5, 4))             # Monday, 1.05 holiday on Friday
    assert y["ex_date"] == "2026-04-30" and y["last_cum_date"] == "2026-04-29"


def test_exdate_from_last_cum_matches_archive():
    # biznesradar "ostatnie notowanie z prawem" vs GPW archive price cuts (2026)
    assert E.exdiv_from_last_cum(D(2026, 8, 3))["ex_date"] == "2026-08-04"     # PKOBP
    assert E.exdiv_from_last_cum(D(2026, 9, 15))["ex_date"] == "2026-09-16"    # PZU
    assert E.exdiv_from_last_cum(D(2026, 8, 17))["ex_date"] == "2026-08-18"    # KETY


def test_exdiv_history_and_estimate():
    adj = [{"date": "2026-08-04", "factor": 0.945431}, {"date": "2025-08-04", "factor": 0.932518},
           {"date": "2025-03-03", "factor": 0.2}]
    h = E.exdiv_history(adj, {"2026-08-04": 112.5})
    assert [x["kind"] for x in h] == ["dividend_likely", "dividend_likely", "corporate_action"]
    assert h[0]["implied_dps"] == pytest.approx(6.14, abs=0.01)
    est = E.estimate_next_exdate(h, D(2026, 11, 16))
    assert est["estimated_ex_date"] == "2027-08-03" and est["confidence"] == "medium"
    assert est["window_start"] < est["estimated_ex_date"] < est["window_end"]
    assert E.estimate_next_exdate([], D(2026, 11, 16)) is None
    assert E.validate_exdate("2026-08-04", h) is True and E.validate_exdate("2026-08-05", h) is None


def test_derivative_expiry_rule():
    xs = S.derivative_expiries(D(2026, 1, 1), D(2026, 12, 31))
    q = [x["date"] for x in xs if x["quarterly"]]
    assert q == ["2026-03-20", "2026-06-19", "2026-09-18", "2026-12-18"]
    assert E.third_friday(2027, 3) == D(2027, 3, 19)


def test_mpc_and_msci_and_cpi_rules():
    assert S.schedule_gaps(D(2026, 11, 16), D(2027, 1, 20)) == [2027]
    dec = [m for m in S.MPC_SCHEDULE[2026] if m["days"][0].startswith("2026-12")]
    assert dec[0]["days"] == ["2026-12-01", "2026-12-02"]
    rev = {r["review"]: r for r in S.msci_reviews(D(2026, 11, 1), D(2027, 12, 31))}
    assert rev["2026-11"]["effective_close"] == "2026-11-30" and rev["2026-11"]["kind"] == "confirmed"
    assert rev["2027-11"]["kind"] == "estimated" and rev["2027-11"]["effective_close"] == "2027-11-30"
    cpi = {c["ref_month"]: c for c in S.cpi_releases(D(2026, 9, 1), D(2026, 11, 30))}
    assert cpi["2026-10"]["flash"] == "2026-10-30" and cpi["2026-09"]["flash"] == "2026-09-30"
    assert cpi["2026-10"]["final"] == "2026-11-13"   # 15.11 Sunday -> Friday (GUS published 13.11)


# ---------------------------------------------------------------- parsers of sources

def test_gpwbenchmark_calendar_fixture():
    items = E.parse_index_calendar_html((FIX / "gpwbenchmark_kalendarium_2026.html").read_text(encoding="utf-8"))
    assert len(items) > 80
    by = {(i["date"], i["category"], i["family"]): i for i in items}
    assert by[("2026-12-03", "announcement", "size")]["importance"] == "high"
    assert by[("2026-12-18", "quarterly_adjustment", "size")]["importance"] == "high"
    assert by[("2026-03-20", "annual_revision", "size")]["href"].startswith("https://gpwbenchmark.pl/")
    assert by[("2026-12-18", "quarterly_revision", "wig")]["importance"] == "medium"
    assert ("2026-11-20", "ranking", "size") in by
    assert any(i["category"] == "bonds" for i in items)
    assert any(i["category"] == "no_session" and i["date"] == "2026-12-24" for i in items)
    assert E.parse_index_calendar_html("<html>changed layout</html>") == []


def test_biznesradar_fixture():
    rows = E.parse_biznesradar_dividends((FIX / "biznesradar_dywidendy_trimmed.html").read_text(encoding="utf-8"))
    pko = [r for r in rows if r["ticker"] == "PKOBP"][0]
    assert pko["last_cum_date"] == "2026-08-03" and pko["payment_date"] == "2026-08-13" and pko["dps"] == 6.14
    assert pko["agm_date"] == "2026-06-29" and pko["status"] == "wypłacona"
    assert {r["ticker"] for r in rows} >= {"PKOBP", "PZU", "KETY", "ATAL"}


def test_espi_fixture_classes_used_by_calendar():
    from gpwlib import espi
    rows = espi.parse_list_html((FIX / "espi_list_2026-11-10.html").read_text(encoding="utf-8"))
    assert rows
    for r in rows:
        if r["class"] == "agm_call" and "7 grudnia" in r["title"]:
            assert E.parse_agm_text(r["title"], "", D.fromisoformat(r["date"]))["date"] == "2026-12-07"


# ---------------------------------------------------------------- events, window, ics

def _ev(**kw):
    base = dict(type="periodic_report", date="2026-11-19", title="t", source="s", ticker="PZU")
    base.update(kw)
    return E.make_event(**base)


def test_make_event_validates():
    with pytest.raises(ValueError):
        _ev(type="earnings")
    with pytest.raises(ValueError):
        _ev(date_kind="maybe")
    with pytest.raises(ValueError):
        _ev(date="2026-13-01")


def test_dedupe_confirmed_beats_deadline():
    a = _ev(date="2026-11-30", date_kind="statutory_deadline", details={"period": "Q3", "fiscal_year": 2026})
    b = _ev(date="2026-11-19", date_kind="confirmed", details={"period": "Q3", "fiscal_year": 2026})
    out = E.dedupe_events([a, b])
    assert len(out) == 1 and out[0]["date"] == "2026-11-19"
    est = _ev(type="ex_dividend", date="2027-08-03", date_kind="estimated")
    conf = _ev(type="ex_dividend", date="2027-08-10", date_kind="confirmed")
    assert [e["date_kind"] for e in E.dedupe_events([est, conf])] == ["confirmed"]


def test_holding_window_check_verdicts():
    evs = [
        _ev(date="2026-11-19", date_kind="confirmed", details={"period": "Q3", "fiscal_year": 2026}),
        E.make_event(type="mpc_meeting", date="2026-12-02", title="RPP", source="NBP", importance="high"),
        E.make_event(type="index_revision", subtype="ranking", date="2026-11-20", title="ranking", source="GPWB",
                     importance="medium"),
        E.make_event(type="other", subtype="futures_expiry", date="2026-12-18", title="FW20", source="GPW",
                     importance="medium"),
        _ev(ticker="KETY", date="2026-11-18", date_kind="confirmed"),
    ]
    w = E.holding_window_check(evs, "PZU", D(2026, 11, 16), 20)
    assert w["start"] == "2026-11-16" and w["end"] == "2026-12-11"
    assert w["verdict"] == "event_risk"
    c = w["contains"]
    assert c["has_earnings"] and c["has_mpc"] and not c["has_expiry"] and not c["has_index_revision"]
    assert w["first"]["earnings"]["sessions_from_start"] == 4
    w2 = E.holding_window_check(evs, "PKOBP", D(2026, 11, 16), 25)
    assert w2["verdict"] == "caution" and w2["contains"]["has_expiry"] and not w2["contains"]["has_earnings"]
    w3 = E.holding_window_check([], "PKOBP", D(2026, 11, 16), 5)
    assert w3["verdict"] == "clear" and w3["n_events"] == 0


def test_ics_output():
    long_title = "Ogłoszenie o zwołaniu Nadzwyczajnego Walnego Zgromadzenia, projekty uchwał; ąęółśżźćń " * 2
    evs = [_ev(title=long_title, time="12:00", type="agm"),
           _ev(date="2026-11-30", date_kind="statutory_deadline", title="Raport za III kw. 2026")]
    ics = E.to_ics(evs, dt.datetime(2026, 11, 16, 12, 0, tzinfo=dt.timezone.utc))
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    lines = ics.split("\r\n")
    assert all(len(ln.encode("utf-8")) <= 75 for ln in lines)
    assert "DTSTART;TZID=Europe/Warsaw:20261119T120000" in lines
    assert "DTSTART;VALUE=DATE:20261130" in lines and "DTEND;VALUE=DATE:20261201" in lines
    assert ics.count("BEGIN:VEVENT") == 2 and "BEGIN:VTIMEZONE" in ics
    unfolded = ics.replace("\r\n ", "")
    assert "\\, projekty uchwał\\;" in unfolded and "[termin ustawowy] PZU: Raport za III kw. 2026" in unfolded
