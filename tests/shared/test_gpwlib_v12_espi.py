"""gpwlib 1.2 - espi: list parser on a real trimmed page, classifier on real titles,
report page parser, pagination and range logic (offline, fake http)."""

import datetime as dt
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

from gpwlib import espi, http  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
LIST_HTML = (FIX / "espi_list_2026-11-10.html").read_text(encoding="utf-8")


# --- list parser ------------------------------------------------------------------

def test_parse_list_real_page():
    rows = espi.parse_list_html(LIST_HTML)
    assert len(rows) == 16
    kompap = next(r for r in rows if r["geru_id"] == 500343)
    assert kompap["company"] == "PRZEDSIĘBIORSTWO PRODUKCYJNO HANDLOWE KOMPAP SPÓŁKA AKCYJNA"
    assert kompap["isin"] == "PLKOMPP00017"
    assert kompap["datetime"] == "2026-11-10T14:37:35" and kompap["time"] == "14:37:35"
    assert (kompap["category"], kompap["type"], kompap["number"]) == ("ESPI", "RB", "36/2026")
    assert kompap["title"] == "Nabycie akcji przez Prezesa Zarządu"
    assert kompap["url"] == "https://www.gpw.pl/komunikat?geru_id=500343"
    assert kompap["class"] == "insider" and kompap["correction"] is False


def test_quarterly_rows_have_empty_title_and_number():
    rows = espi.parse_list_html(LIST_HTML)
    q = [r for r in rows if r["type"] == "Q"]
    assert len(q) >= 3
    assert all(r["title"] == "" and r["number"] is None and r["class"] == "periodic_q" for r in q)
    assert all(r["type_label"] == "Kwartalny" for r in q)


def test_empty_page_and_garbage():
    assert espi.parse_list_html((FIX / "espi_list_empty.html").read_text(encoding="utf-8")) == []
    assert espi.parse_list_html("") == []
    assert espi.parse_list_html("<li><span class=\"date\">bad</span></li>") == []


# --- classifier on real titles (gpw.pl lists, Nov 2026 and Mar 2019) -----------------

REAL_TITLES = [
    ("Informacja o transakcjach na akcjach Spółki uzyskana w trybie art. 19 MAR.", "insider"),
    ("Informacja o transakcji w trybie art. 19 MAR", "insider"),
    ("Informacja o transakcjach w trybie art. 19 Rozporządzenia MAR", "insider"),
    ("Powiadomienie o transakcjach na instrumentach finansowych dokonanych przez osoby pełniące obowiązki zarządcze.", "insider"),
    ("Powiadomienie o transakcji na akcjach Emitenta otrzymane w trybie art. 19 MAR.", "insider"),
    ("Powiadomienie o transakcji na akcjach „Bowim” S.A.", "insider"),
    ("Zawiadomienie o zawarciu transakcji przez osobę pełniącą obowiązki zarządcze", "insider"),
    ("Nabycie akcji przez Prezesa Zarządu", "insider"),
    ("Director/PDMR transaction", "insider"),
    ("Skup akcji własnych", "buyback"),
    ("Acquisition of treasury shares", "buyback"),
    ("Informacja ws. skupionych akcji własnych w okresie od 08.03.2019 r. do 14.03.2019 r.", "buyback"),
    ("Zawiadomienie o zmianie stanu posiadania akcji Invista S.A", "holding_change"),
    ("Zawiadomienie akcjonariusza o zmniejszeniu stanu posiadania akcji", "holding_change"),
    ("Zawiadomienie o zmianie udziału w ogólnej liczbie głosów na Walnym Zgromadzeniu Emitenta", "holding_change"),
    ("Zawiadomienie BlackRock, Inc. w sprawie zwiększenia udziału w ogólnej liczbie głosów na Walnym Zgromadzeniu Banku powyżej progu 5%", "holding_change"),
    ("Zmiana terminu publikacji raportu kwartalnego za III kwartał 2026 roku", "periodic_schedule"),
    ("Zmiana terminu publikacji raportu okresowego za III kw.2026 r.", "periodic_schedule"),
    ("Zmiana terminu publikacji jednostkowego i skonsolidowanego raportu rocznego za 2018 r.", "periodic_schedule"),
    ("Terminy przekazywania raportów okresowych w 2027 roku", "periodic_schedule"),
    ("KERNEL ANNOUNCES DATE FOR Q1 FY2027 RESULTS PUBLICATION", "periodic_schedule"),
    ("Korekta jednostkowego i skonsolidowanego raportu rocznego za 2018 rok", "periodic_y"),
    ("Raport roczny 2025", "periodic_y"),
    ("Raport półroczny za I półrocze 2026", "periodic_h"),
    ("Rozszerzony skonsolidowany raport kwartalny za III kwartał 2026", "periodic_q"),
    ("Korekta raportu za I kwartał 2026 r.", "periodic_q"),
    ("January to September 2026 unaudited interim report of the Krka Group and Krka", "periodic_q"),
    ("Wstępne wybrane dane finansowe za III kwartał 2026", "preliminary_results"),
    ("Szacunkowe wyniki Grupy Kapitałowej Lentex za III kwartały 2026 roku", "preliminary_results"),
    ("Informacja w sprawie skonsolidowanych i jednostkowych wstępnych wyników finansowych za trzy kwartały 2026 roku", "preliminary_results"),
    ("Wybrane skonsolidowane dane finansowe za okresy trzech i dziewięciu miesięcy zakończonych 30 września 2026 r.", "preliminary_results"),
    ("Zaliczka na poczet dywidendy Q3 2026", "dividend"),
    ("Uchwała w sprawie warunkowej wypłaty zaliczki na poczet przyszłej dywidendy za rok obrotowy 2026", "dividend"),
    ("Przyjęcie nowej polityki dywidendy spółki Cyfrowy Polsat S.A.", "dividend"),
    ("Rekomendacja Zarządu dotycząca podziału zysku za 2018 r.", "dividend"),
    ("Uchwały podjęte przez Nadzwyczajne Walne Zgromadzenie RAFAMET S.A. w restrukturyzacji w dniu 10 listopada 2026 r.", "agm_resolutions"),
    ("Podjęte uchwały na Nadzwyczajnym Walnym Zgromadzeniu", "agm_resolutions"),
    ("Uchwały Nadzwyczajnego Walnego Zgromadzenia Akcjonariuszy GetBack S.A. w restrukturyzacji z dnia 14 marca 2019 r.;", "agm_resolutions"),
    ("Zwołanie Zwyczajnego Walnego Zgromadzenia Spółki", "agm_call"),
    ("Zwołanie NWZA Sanok RC S.A. Projekty uchwał.", "agm_call"),
    ("Ogłoszenie o zwołaniu Nadzwyczajnego Walnego Zgromadzenia Eurocash S.A.", "agm_call"),
    ("Projekty uchwał na Nadzwyczajne Walne Zgromadzenie Eurocash S.A.", "agm_call"),
    ("Notice of call to the extraordinary general shareholder's meeting", "agm_call"),
    ("Dodatek do zawiadomienia o zwołaniu nadzwyczajnego walnego zgromadzenia Banco Santander, S.A.", "agm_call"),
    ("Wykaz akcjonariuszy posiadających co najmniej 5% głosów na NWZ Spółki w dniu 10 listopada 2026 roku", "agm_other"),
    ("Żądanie akcjonariusza umieszczenia określonej sprawy w porządku obrad Nadzwyczajnego Walnego Zgromadzenia wraz z projektami uchwał", "agm_other"),
    ("Przerwa w obradach Nadzwyczajnego Walnego Zgromadzenia Akcjonariuszy GetBack S.A. w restrukturyzacji", "agm_other"),
    ("Zawarcie umowy znaczącej", "other"),
    ("Rezygnacja Członka Rady Nadzorczej", "other"),
    ("Stanowisko Zarządu Echo Investment S.A. dotyczące wezwania do zapisywania się na sprzedaz akcji Echo Investment S.A.", "other"),
    ("Telekonferencja z inwestorami dotycząca wyników za trzeci kwartał 2026 roku", "other"),
    ("OPERATIONS UPDATE - IMPACT OF THE RUSSIAN WAR AGAINST UKRAINE", "other"),
]


@pytest.mark.parametrize("title,expected", REAL_TITLES)
def test_classifier_on_real_titles(title, expected):
    assert espi.classify(title) == expected


def test_classifier_covers_enough_real_titles():
    assert len(REAL_TITLES) >= 25
    assert {e for _, e in REAL_TITLES} == set(espi.CLASSES)


def test_type_fallback_and_title_priority():
    assert espi.classify("", report_type="Q") == "periodic_q"
    assert espi.classify("", report_type="P") == "periodic_h"
    assert espi.classify("", report_type="R") == "periodic_y"
    assert espi.classify("Monthly Report for October 2026", report_type="Q") == "periodic_q"
    # GPW labelled this AGM notice "Półroczny" - the title rule wins
    t = "Rozszerzenie porządku obrad Zwyczajnego Walnego Zgromadzenia DELKO S.A. zwołanego na dzień 3 grudnia 2026 roku"
    assert espi.classify(t, report_type="P") == "agm_other"
    assert espi.classify("") == "other"


def test_esap_transd_needs_mar_wording():
    # TRANSD + MAR art. 19 / managers'-transaction wording in title or body -> insider
    body = {"esap_codes": ["TRANSD"], "body_text": "Zarząd informuje, że otrzymał powiadomienie w trybie art. 19 MAR"}
    assert espi.classify("Informacja dla akcjonariuszy", body) == "insider"
    assert espi.classify("Informacja dla akcjonariuszy",
                         {"esap_codes": ["transd"], "body_text": "od osoby pełniącej obowiązki zarządcze"}) == "insider"
    # TRANSD alone is not proof: some issuers (KRUK) tag every report with it
    kruk = {"esap_codes": ["TRANSD"],
            "body_text": "Zarząd KRUK S.A. informuje, iż agencja ratingowa Moody's potwierdziła rating."}
    assert espi.classify("Informacja w sprawie utrzymania oceny przez agencję ratingową Moody's", kruk) == "other"
    assert espi.classify("Informacja dla akcjonariuszy", {"esap_codes": ["TRANSD"]}) == "other"
    # inside information quotes "art. 17 ust. 1 MAR" / "(UE) nr 596/2014" - not art. 19
    inside = {"esap_codes": ["TRANSD"], "body_text": "Podstawa prawna: art. 17 ust. 1 MAR, Rozporządzenie (UE) nr 596/2014"}
    assert espi.classify("Zawarcie aneksu do umowy kredytu", inside) == "other"
    # TRANSD on another class: the title rules still decide
    agm = {"esap_codes": ["TRANSD"], "body_text": "działając na podstawie art. 399 § 1 KSH zwołuje"}
    assert espi.classify("Zwołanie Nadzwyczajnego Walnego Zgromadzenia", agm) == "agm_call"
    # the title alone still classifies insiders without any ESAP code
    assert espi.classify("Informacja o transakcji w trybie art. 19 MAR") == "insider"
    assert espi.mentions_managers_transaction("Director/PDMR transaction")
    assert not espi.mentions_managers_transaction("art. 17 ust. 1 MAR")


def test_esap_metadata_other_codes():
    assert espi.classify("Zawarcie umowy znaczącej", {"esap_codes": ["XYZ"]}) == "other"
    assert espi.classify("", {"esap_codes": [], "title": "Skup akcji własnych"}) == "buyback"


def test_correction_flag_and_groups():
    assert espi.is_correction("Korekta raportu za I kwartał 2026 r.")
    assert espi.is_correction("Korekta metadanych ESAP w raporcie bieżącym nr 44/2026")
    assert not espi.is_correction("Zawarcie umowy znaczącej")
    assert set(espi.GROUPS) == set(espi.CLASSES)
    assert espi.GROUPS["holding_change"] == "holding_5pct" and espi.GROUPS["agm_call"] == "agm"


def test_summarize():
    s = espi.summarize(espi.parse_list_html(LIST_HTML))
    assert set(s) == set(espi.CLASSES) and sum(s.values()) == 16
    assert s["periodic_q"] >= 3 and s["insider"] == 1


# --- report page ---------------------------------------------------------------------

def test_parse_report_page_real_fixture():
    d = espi.parse_report_html((FIX / "espi_report_500287.html").read_text(encoding="utf-8"))
    assert d["title"] == "Informacja o transakcji w trybie art. 19 MAR"
    assert d["company"] == "ASSECO SOUTH EASTERN EUROPE SPÓŁKA AKCYJNA" and d["isin"] == "PLASSEE00014"
    assert d["header"].startswith("10-11-2026 12:14:47 | Bieżący | ESPI | 12/2026")
    assert d["esap_codes"] == ["TRANSD"]
    assert d["esap"][0]["description"].startswith("Transakcja dokonywana przez osoby")
    assert d["legal_basis"].startswith("Art. 19 ust. 3 Rozporządzenia MAR")
    assert "powiadomienie o transakcji nabycia akcji" in d["body_text"]
    assert len(d["attachments"]) == 1
    att = d["attachments"][0]
    assert att["name"].endswith(".pdf") and att["url"].startswith("https://www.gpw.pl/attachment/")
    assert d["sheets"] == ["RAPORT BIEŻĄCY", "METADANE ESAP"]   # page chrome h4s ignored
    assert espi.classify(d["title"], d) == "insider"


def test_parse_report_page_long_title_wraps_in_cell():
    # integration fix 1.2: the "Temat" cell wraps a long title over two lines
    # while the <h1> has one space - company used to keep the whole title
    d = espi.parse_report_html((FIX / "espi_report_long_title.html").read_text(encoding="utf-8"))
    assert d["title"].startswith("Przyjęcie przez Zarząd Budimex S.A.")
    assert d["company"] == "BUDIMEX SPÓŁKA AKCYJNA" and d["isin"] == "PLBUDMX00013"


def test_parse_report_company_fallbacks():
    def page(title, h1):
        return ("<html><body><h1>Raporty Spółek ESPI/EBI</h1><h1>{}</h1>"
                "<h4>RAPORT BIEŻĄCY</h4><table><tr><td><b>Temat</b></td></tr><tr><td>{}</td></tr></table>"
                "</body></html>").format(h1, title)
    # double spaces / different case still match the title
    d = espi.parse_report_html(page("Wypłata  dywidendy", "wypłata dywidendy XYZ SPÓŁKA AKCYJNA (PLXYZ0000016)"))
    assert d["company"] == "XYZ SPÓŁKA AKCYJNA"
    # title missing (EBI / English reports): keep the trailing upper-case legal name, drop a year
    d = espi.parse_report_html(page("", "Dividend payment information for 2025 PEPCO GROUP N.V. (NL0015000AU7)"))
    assert d["company"] == "PEPCO GROUP N.V." and d["isin"] == "NL0015000AU7"
    d = espi.parse_report_html(page("", "Uchwała w sprawie dywidendy za 2025 r. 2025 MBANK SPÓŁKA AKCYJNA (PLBRE0000012)"))
    assert d["company"] == "MBANK SPÓŁKA AKCYJNA"
    # names starting with a digit survive
    d = espi.parse_report_html(page("Raport", "Raport 4MASS SPÓŁKA AKCYJNA (PL4MASS00011)"))
    assert d["company"] == "4MASS SPÓŁKA AKCYJNA"


def test_parse_report_page_empty():
    d = espi.parse_report_html("<html></html>")
    assert d["title"] == "" and d["esap_codes"] == [] and d["attachments"] == []


# --- fetching: pagination, ranges, cache policy (fake http) -------------------------------

_LI_TEMPLATE = re.findall(r"<li style.*?</li>", LIST_HTML, re.S)[10]  # an RB row of 10-11-2026


def _page(day: dt.date, ids):
    out = []
    for i in ids:
        li = re.sub(r"geru_id=\d+", "geru_id={}".format(i), _LI_TEMPLATE)
        li = re.sub(r"\d{2}-\d{2}-\d{4} (\d{2}:\d{2}:\d{2})", day.strftime("%d-%m-%Y") + r" \1", li)
        out.append(li)
    return "\n".join(out) or "\n\n\t\t\t\n\n\r\n\n\n"


class FakePost:
    def __init__(self, pages_by_day):
        self.pages = pages_by_day
        self.calls = []

    def __call__(self, url, form, **kw):
        f = dict(form)
        day = dt.datetime.strptime(f["date"], "%d-%m-%Y").date()
        self.calls.append((day, int(f["offset"]), int(f["limit"]), kw))
        if kw.get("offline") and day not in self.pages:
            raise http.OfflineMiss("miss")
        pages = self.pages.get(day, [[]])
        idx = int(f["offset"]) // int(f["limit"])
        ids = pages[idx] if idx < len(pages) else []
        return http.Response(url, 200, _page(day, ids), "utf-8", "2026-11-16T10:00:00+00:00", False)


def test_fetch_day_paginates_until_short_page(monkeypatch):
    day = dt.date(2026, 11, 12)
    fake = FakePost({day: [list(range(1000, 1100)), list(range(1100, 1103))]})
    monkeypatch.setattr(http, "post", fake)
    res = espi.fetch_day(day)
    assert len(res["rows"]) == 103 and res["pages"] == 2
    assert [c[1] for c in fake.calls] == [0, 100]
    assert all(r["date"] == "2026-11-12" for r in res["rows"])


def test_fetch_day_stops_on_repeated_page_and_dedups(monkeypatch):
    day = dt.date(2026, 11, 12)
    same = list(range(1, 101))
    fake = FakePost({day: [same, same, same]})
    monkeypatch.setattr(http, "post", fake)
    res = espi.fetch_day(day)
    assert len(res["rows"]) == 100 and res["pages"] == 2


def test_list_form_matches_site_fields():
    form = espi.list_form(dt.date(2026, 11, 12), ["ESPI"], ["RB", "Q"], offset=100)
    d = dict(form)
    assert d["action"] == "GPWEspiReportUnion" and d["start"] == "ajaxSearch" and d["page"] == "komunikaty"
    assert d["date"] == "12-11-2026" and d["offset"] == "100" and d["limit"] == "100"
    assert [v for k, v in form if k == "typeRaports[]"] == ["RB", "Q"]
    assert [v for k, v in form if k == "categoryRaports[]"] == ["ESPI"]


def test_fetch_range_skips_weekends_and_holidays(monkeypatch):
    days = {dt.date(2026, 11, 10): [[1, 2]], dt.date(2026, 11, 12): [[3]], dt.date(2026, 11, 13): [[4]]}
    fake = FakePost(days)
    monkeypatch.setattr(http, "post", fake)
    # Tue 10 .. Mon 16 Nov 2026; 11 Nov = Independence Day, 14-15 weekend
    res = espi.fetch_range(dt.date(2026, 11, 10), dt.date(2026, 11, 16))
    asked = sorted({c[0] for c in fake.calls})
    assert asked == [dt.date(2026, 11, 10), dt.date(2026, 11, 12), dt.date(2026, 11, 13), dt.date(2026, 11, 16)]
    assert [r["geru_id"] for r in res["rows"]] == [2, 1, 3, 4]  # oldest first within each day
    assert [d["count"] for d in res["days"]] == [2, 1, 1, 0]
    fake.calls.clear()
    espi.fetch_range(dt.date(2026, 11, 14), dt.date(2026, 11, 15), include_non_sessions=True)
    assert sorted({c[0] for c in fake.calls}) == [dt.date(2026, 11, 14), dt.date(2026, 11, 15)]


def test_fetch_range_offline_reports_missing_days(monkeypatch):
    fake = FakePost({dt.date(2026, 11, 12): [[7]]})
    monkeypatch.setattr(http, "post", fake)
    res = espi.fetch_range(dt.date(2026, 11, 12), dt.date(2026, 11, 13), offline=True)
    assert res["missing_days"] == ["2026-11-13"] and len(res["rows"]) == 1
    with pytest.raises(http.OfflineMiss):
        espi.fetch_range(dt.date(2026, 11, 12), dt.date(2026, 11, 13), offline=True, skip_missing=False)


def test_cache_policy_past_vs_today(monkeypatch):
    from gpwlib import plcal
    monkeypatch.setattr(plcal, "today_warsaw", lambda: dt.date(2026, 11, 16))
    ttl, va = espi._day_cache_policy(dt.date(2026, 11, 16))
    assert ttl == espi.TODAY_TTL_S and va is None
    ttl, va = espi._day_cache_policy(dt.date(2026, 11, 12))
    assert ttl == espi.PAST_DAY_TTL_S
    # 13 Nov 00:30 Warsaw (CET, UTC+1) = 12 Nov 23:30 UTC
    assert va == dt.datetime(2026, 11, 12, 23, 30, tzinfo=dt.timezone.utc)


def test_fetch_report_uses_long_cache(monkeypatch):
    seen = {}

    def fake_get(url, params=None, **kw):
        seen.update(kw, url=url)
        return http.Response(url, 200, (FIX / "espi_report_500287.html").read_text(encoding="utf-8"), "utf-8",
                             "2026-11-16T10:00:00+00:00", False)

    monkeypatch.setattr(http, "get", fake_get)
    d = espi.fetch_report(500287)
    assert seen["url"] == "https://www.gpw.pl/komunikat?geru_id=500287"
    assert seen["ttl_seconds"] == espi.REPORT_TTL_S
    assert d["class"] == "insider" and d["geru_id"] == 500287
