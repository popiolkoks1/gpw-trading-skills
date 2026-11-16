import pytest
from conftest import FIXTURES

import ownership_insiders as oi


def _parse(name):
    return oi.parse_notification((FIXTURES / name).read_text(encoding="utf-8"))


def test_pl_buy_multiple_prices():
    (tx,) = _parse("notif_pl_buy.txt")
    assert tx["person"] == "Jan Kowalski" and tx["position"] == "Prezes Zarządu"
    assert tx["issuer"] == "Przykładowa Spółka S.A." and tx["lei"] == "259400ABCDEF12345678"
    assert tx["isin"] == "PLPRZYK00010"
    assert tx["nature"] == "buy" and tx["direction"] == "buy" and not tx["excluded"]
    assert [p["volume"] for p in tx["prices"]] == [1000, 2500]
    assert tx["volume"] == 3500 and tx["value"] == pytest.approx(45.2 * 1000 + 45.5 * 2500)
    assert tx["aggregated"] == {"volume": 3500, "price": 45.41}
    assert tx["date"] == "2026-11-05" and tx["place_kind"] == "gpw" and tx["currency"] == "PLN"
    assert "ceo" in tx["roles"] and tx["warnings"] == []


def test_en_sell_table_with_thousands_commas():
    (tx,) = _parse("notif_en_sell.txt")
    assert tx["person"] == "Anna Nowak" and tx["direction"] == "sell"
    assert tx["volume"] == 4000 and tx["value"] == pytest.approx(12.10 * 1200 + 12.05 * 800 + 11.98 * 2000)
    assert tx["date"] == "2026-11-06"
    assert "supervisory_board" in tx["roles"]
    assert any("PLN assumed" in w for w in tx["warnings"])


def test_closely_associated_entity_and_option_exercise():
    a, b = _parse("notif_pl_entity_option.txt")
    assert a["person"] == "Rodzinna Fundacja Kowalskich"
    assert a["entity"] and a["closely_associated"]
    assert a["direction"] == "buy" and a["place_kind"] == "off_venue" and a["date"] == "2026-11-04"
    assert a["volume"] == 100000 and a["value"] == 5_000_000
    assert b["person"] == "Piotr Wiśniewski" and not b["entity"]
    assert b["nature"] == "option_exercise" and b["excluded"] and b["date"] == "2026-11-03"


def test_bilingual_duplicate_is_deduplicated():
    txt = (FIXTURES / "notif_pl_buy.txt").read_text(encoding="utf-8")
    eng = txt.replace("Dane osoby pełniącej obowiązki zarządcze", "Details of the person discharging managerial responsibilities")
    assert len(oi.parse_notification(txt + "\n" + eng)) == 1


def test_missing_volume_is_not_invented():
    txt = ("1 Dane osoby pełniącej obowiązki zarządcze\na) Imię i nazwisko: Ewa Lis\n2 Powód powiadomienia\n"
           "a) Stanowisko/status: Członek Zarządu\n4 Szczegółowe informacje dotyczące transakcji\n"
           "b) Rodzaj transakcji: Nabycie akcji\nc) Cena i wolumen: patrz załącznik\ne) Data transakcji: 2026-11-02\n")
    (tx,) = oi.parse_notification(txt)
    assert tx["volume"] is None and tx["value"] is None
    assert any("volume not found" in w for w in tx["warnings"])


def test_empty_text():
    assert oi.parse_notification("   ") == []
    assert oi.parse_notification("Zarząd informuje o zawarciu umowy.") == []


@pytest.mark.parametrize("tok,english,val", [
    ("1 000,50", False, 1000.5), ("1.000,50", False, 1000.5), ("1,000.50", True, 1000.5),
    ("45,2", False, 45.2), ("1,200", True, 1200.0), ("12.10", True, 12.1), ("2 500", False, 2500.0),
    ("1.000", False, 1000.0), ("0.500", False, 0.5)])
def test_parse_number(tok, english, val):
    assert oi.parse_number(tok, english) == pytest.approx(val)


@pytest.mark.parametrize("text,date", [
    ("2026-11-05; 10:00 UTC", "2026-11-05"), ("05.11.2026", "2026-11-05"), ("5 listopada 2026", "2026-11-05"),
    ("November 5, 2026", "2026-11-05"), ("5 November 2026", "2026-11-05"), ("brak", None)])
def test_parse_date(text, date):
    assert oi.parse_date(text) == date


@pytest.mark.parametrize("text,nature", [
    ("Nabycie akcji na sesji", "buy"), ("Sprzedaż akcji", "sell"), ("Disposal of shares", "sell"),
    ("Ustanowienie zastawu na akcjach", "pledge"), ("Darowizna akcji", "gift"),
    ("Objęcie akcji w ramach programu motywacyjnego", "incentive_plan"),
    ("Exercise of options", "option_exercise"), ("Zapis na akcje w ofercie publicznej", "subscription"),
    ("Coś innego", "other")])
def test_classify_nature(text, nature):
    assert oi.classify_nature(text) == nature


def _n(date, ticker, direction, person, entity=False, correction=False):
    return {"date": date, "ticker": ticker, "isin": "PL" + ticker, "company": ticker, "direction_hint": direction,
            "person_hint": person, "entity_hint": entity, "correction": correction, "title": "t", "url": "u"}


def _t(date, ticker, direction, person, value, entity=False, nature=None, tid=None):
    return {"id": tid or (date + person), "date": date, "ticker": ticker, "person": person, "direction": direction,
            "nature": nature or direction, "excluded": nature not in (None, "buy", "sell"), "value": value,
            "currency": "PLN", "entity": entity, "volume": 1}


def test_cluster_buy_from_parsed_transactions():
    txs = [_t("2026-10-01", "ABC", "buy", "A", 100000), _t("2026-10-10", "ABC", "buy", "B", 50000),
           _t("2026-10-25", "ABC", "buy", "C", 20000), _t("2026-10-26", "ABC", "sell", "D", 30000)]
    (r,) = oi.aggregate([], txs, "2026-10-31")
    assert r["cluster_buy"] and not r["cluster_sell"]
    assert r["buys"] == 3 and r["sells"] == 1 and r["distinct_insiders"] == 4
    assert r["net_value_pln"] == pytest.approx(140000)
    assert r["basis"] == "parsed_transactions"


def test_no_cluster_when_spread_over_more_than_30_days():
    txs = [_t("2026-08-01", "ABC", "buy", "A", 1), _t("2026-08-20", "ABC", "buy", "B", 1),
           _t("2026-09-15", "ABC", "buy", "C", 1)]
    (r,) = oi.aggregate([], txs, "2026-10-31")
    assert not r["cluster_buy"] and r["max_distinct_buyers_30d"] == 2


def test_entities_and_excluded_do_not_make_a_cluster():
    txs = [_t("2026-10-01", "ABC", "buy", "A", 1), _t("2026-10-02", "ABC", "buy", "B", 1),
           _t("2026-10-03", "ABC", "buy", "Parent S.A.", 1, entity=True),
           _t("2026-10-04", "ABC", "buy", "C", 1, nature="option_exercise")]
    (r,) = oi.aggregate([], txs, "2026-10-31")
    assert not r["cluster_buy"]
    assert "closely_associated_entity" in r["flags"] and "plan_or_non_market_excluded" in r["flags"]
    assert r["buys"] == 3  # entity buy counted, option exercise not


def test_cluster_sell_from_notice_hints_and_net_value_null():
    ns = [_n("2026-10-01", "XYZ", "sell", "Jana Kowalskiego"), _n("2026-10-02", "XYZ", "sell", "Anny Nowak"),
          _n("2026-10-03", "XYZ", "sell", "Piotra Lisa"), _n("2026-10-03", "XYZ", "sell", "Piotra Lisa", correction=True)]
    (r,) = oi.aggregate(ns, [], "2026-10-31")
    assert r["cluster_sell"] and r["sells"] == 3 and r["net_value_pln"] is None
    assert r["basis"] == "notice_hints" and "details_missing_paste_pdf" in r["flags"]


def test_possible_cluster_when_identity_unknown():
    ns = [_n("2026-10-0{}".format(i), "XYZ", "buy", None) for i in range(1, 4)]
    (r,) = oi.aggregate(ns, [], "2026-10-31")
    assert not r["cluster_buy"] and "possible_cluster_buy_identity_unknown" in r["flags"]


def test_closed_period_and_routine():
    assert oi.in_closed_period("2026-10-10", ["2026-11-05"]) is True
    assert oi.in_closed_period("2026-09-01", ["2026-11-05"]) is False
    assert oi.in_closed_period("2026-11-10", ["2026-11-05"]) is None
    assert oi.in_closed_period("2026-11-10", []) is None
    hist = [{"person": "A", "ticker": "ABC", "date": "{}-03-10".format(y)} for y in (2023, 2024, 2025)]
    assert oi.routine_flag("A", "ABC", "2026-03-20", hist) is True
    assert oi.routine_flag("A", "ABC", "2026-04-20", hist) is False
    assert oi.routine_flag("A", "ABC", "2026-03-20", hist[1:]) is None  # < 3 years of history


def test_closed_period_flag_in_aggregate():
    txs = [_t("2026-10-20", "ABC", "buy", "A", 10)]
    (r,) = oi.aggregate([], txs, "2026-10-31", report_dates={"ABC": ["2026-11-05"]})
    assert "trade_in_closed_period" in r["flags"]
    assert r["transactions"][0]["closed_period"] is True


def test_notice_from_row_hints():
    row = {"date": "2026-11-10", "title": "Nabycie akcji przez Prezesa Zarządu", "company": "K", "isin": "PLK",
           "geru_id": 1, "url": "u"}
    body = {"body_text": "otrzymał zawiadomienie o transakcjach na akcjach Emitenta dokonanych przez Waldemara Lipkę - "
                         "Prezesa Zarządu Emitenta.", "esap_codes": ["TRANSD"], "attachments": [{"name": "a.pdf"}]}
    n = oi.notice_from_row(row, body)
    assert n["direction_hint"] == "buy" and "ceo" in n["roles_hint"] and n["person_hint"] == "Waldemara Lipkę"
    ent = oi.notice_from_row(dict(row, title="Informacja o transakcjach art. 19 MAR"),
                             {"body_text": "powiadomienie od spółki PKO Bank Polski S.A. jako osoby blisko związanej"})
    assert ent["entity_hint"] and ent["person_hint"] == "PKO Bank Polski S.A." and ent["closely_associated_hint"]
    pledge = oi.notice_from_row(dict(row, title="Zawiadomienie o ustanowieniu zastawu na akcjach"), None)
    assert pledge["nature_hint"] == "pledge" and pledge["direction_hint"] is None
    hold = oi.notice_from_row(dict(row, title="Informacja o transakcjach na akcjach"),
                              {"body_text": "Działając zgodnie z art. 70 pkt 1 ustawy o ofercie publicznej"})
    assert hold["nature_hint"] == "holding_notice"


def test_mentions_managers_transaction():
    assert oi.mentions_managers_transaction("powiadomienie w trybie art. 19 MAR")
    assert not oi.mentions_managers_transaction("Informacja w sprawie utrzymania oceny przez agencję ratingową")
