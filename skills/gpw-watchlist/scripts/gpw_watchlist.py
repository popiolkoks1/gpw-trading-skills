#!/usr/bin/env python3
"""GPW Watchlist - the state between "a skill found it" and "I bought it".

Stateful list of GPW tickers with stages candidate -> watch -> ready ->
entered (journal thesis ACTIVE) or dropped, fed by other skills' JSON reports
and checked against the newest quotes and the event calendar.

    python3 scripts/gpw_watchlist.py add KETY --stage watch --pivot 1281 --stop 1229 --note "VCP 3T"
    python3 scripts/gpw_watchlist.py ingest --input-dir ~/gpw-reports
    python3 scripts/gpw_watchlist.py refresh --input-dir ~/gpw-reports
    python3 scripts/gpw_watchlist.py promote KETY --to ready
    python3 scripts/gpw_watchlist.py list --stage ready
    python3 scripts/gpw_watchlist.py review
    python3 scripts/gpw_watchlist.py drop KETY --reason "zamknięcie poniżej stopu"
    python3 scripts/gpw_watchlist.py export --csv watchlist.csv

State: ``<state-dir>/watchlist.json`` (default ``~/.gpw-trading-skills``);
schema in references/watchlist-contract.md. Exit codes: 0 ok, 1 input/data
error (stderr), 2 bad arguments.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import gpw_archive, journal_io, panel, plcal, provenance, reports  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct  # noqa: E402

import watchlist_ingest as wi  # noqa: E402
import watchlist_monitor as wm  # noqa: E402
import watchlist_state as ws  # noqa: E402

VERSION = "1.0.0"
ARCHIVE_URL = "https://www.gpw.pl/archiwum-notowan"

STRINGS = {
    "title": {"pl": "Watchlista GPW", "en": "GPW Watchlist"},
    "cmd_list": {"pl": "lista", "en": "list"},
    "cmd_ingest": {"pl": "import raportów", "en": "ingest"},
    "cmd_refresh": {"pl": "odświeżenie", "en": "refresh"},
    "cmd_review": {"pl": "przegląd", "en": "review"},
    "generated": {"pl": "Wygenerowano", "en": "Generated"},
    "state_file": {"pl": "Plik stanu", "en": "State file"},
    "quotes_as_of": {"pl": "Notowania na sesję", "en": "Quotes as of session"},
    "never_refreshed": {"pl": "Ceny nie były jeszcze odświeżane - uruchom `refresh`.",
                        "en": "Prices have not been refreshed yet - run `refresh`."},
    "stale": {"pl": "⚠️ UWAGA: notowania są nieaktualne o {n} sesji (stan na {d}).",
              "en": "⚠️ WARNING: quotes are {n} session(s) old (as of {d})."},
    "empty": {"pl": "Watchlista jest pusta. Dodaj spółkę (`add TICKER`) albo zaimportuj raporty (`ingest`).",
              "en": "The watchlist is empty. Add a ticker (`add TICKER`) or ingest reports (`ingest`)."},
    # headline parts
    "h_stop": {"pl": "{n} {w} {v} stop - do usunięcia?", "en": "{n} broke the stop - drop?"},
    "h_breakout": {"pl": "{n} {w} nad pivotem dziś (wybicie)", "en": "{n} crossed the pivot today (breakout)"},
    "h_near": {"pl": "{n} {w} blisko pivotu (do 5% poniżej)", "en": "{n} near the pivot (within 5% below)"},
    "h_buy": {"pl": "{n} w strefie zakupu (do 5% nad pivotem)", "en": "{n} in the buy range (up to 5% above pivot)"},
    "h_ext": {"pl": "{n} za daleko od pivotu (>5%, nie gonić)", "en": "{n} extended (>5% above pivot, do not chase)"},
    "h_near_stop": {"pl": "{n} blisko stopu", "en": "{n} close to the stop"},
    "h_risk": {"pl": "{n} z nowym ostrzeżeniem z raportów (np. słaba reakcja na wyniki)",
               "en": "{n} with a new warning from reports (e.g. weak earnings reaction)"},
    "h_events": {"pl": "{n} z wydarzeniem w ciągu 10 sesji", "en": "{n} with an event within 10 sessions"},
    "h_none": {"pl": "Nic pilnego: {total} na liście ({stages}).", "en": "Nothing urgent: {total} on the list ({stages})."},
    "h_ingest": {"pl": "Import z {reports} raportów: dodane spółki - {added}, podniesione do obserwacji - {upgraded}, nowe alerty - {alerts}, nowe tagi - {tags}.",
                 "en": "Ingest of {reports} reports: {added} added, {upgraded} upgraded to watch, {alerts} new alerts, {tags} new tags."},
    "h_review": {"pl": "Przegląd: {n} {w} ({parts}).", "en": "Review: {n} suggestions ({parts})."},
    "h_review_none": {"pl": "Przegląd: lista jest w porządku, brak sugestii.", "en": "Review: the list is in order, no suggestions."},
    # stages
    "st_candidate": {"pl": "kandydat", "en": "candidate"},
    "st_watch": {"pl": "obserwacja", "en": "watch"},
    "st_ready": {"pl": "gotowa", "en": "ready"},
    "st_entered": {"pl": "w portfelu", "en": "entered"},
    "st_dropped": {"pl": "usunięta", "en": "dropped"},
    # severity
    "sev_high": {"pl": "🔴 wysoki", "en": "🔴 high"},
    "sev_medium": {"pl": "🟠 średni", "en": "🟠 medium"},
    "sev_low": {"pl": "⚪ niski", "en": "⚪ low"},
    # alerts
    "a_stop_breached": {"pl": "Kurs {close} ≤ stop {stop} ({dist}) - setup unieważniony",
                        "en": "Close {close} ≤ stop {stop} ({dist}) - setup invalidated"},
    "a_near_stop": {"pl": "Kurs {close} blisko stopu {stop} ({dist}, < 0,5 ATR)",
                    "en": "Close {close} near the stop {stop} ({dist}, < 0.5 ATR)"},
    "a_pivot_breakout": {"pl": "Wybicie ponad pivot {pivot}: kurs {close} ({dist}), obrót {tr}x mediany 50 sesji{conf}",
                         "en": "Breakout above pivot {pivot}: close {close} ({dist}), turnover {tr}x the 50-session median{conf}"},
    "conf_yes": {"pl": " - potwierdzone obrotem", "en": " - confirmed by turnover"},
    "conf_no": {"pl": " - BEZ potwierdzenia obrotem", "en": " - NOT confirmed by turnover"},
    "a_in_buy_range": {"pl": "W strefie zakupu: {dist} nad pivotem {pivot}", "en": "In the buy range: {dist} above pivot {pivot}"},
    "a_extended": {"pl": "Za daleko: {dist} nad pivotem {pivot} (powyżej 5% - nie gonić)",
                   "en": "Extended: {dist} above pivot {pivot} (beyond 5% - do not chase)"},
    "a_near_pivot": {"pl": "Blisko pivotu: {dist} do {pivot}", "en": "Near the pivot: {dist} to {pivot}"},
    "a_new_52w_high": {"pl": "Nowy szczyt 52-tygodniowy ({high})", "en": "New 52-week high ({high})"},
    "a_turnover_surge": {"pl": "Skok obrotu: {tr}x mediany 50 sesji (zmiana {chg})",
                         "en": "Turnover surge: {tr}x the 50-session median (change {chg})"},
    "a_levels_pre_adjustment": {"pl": "Po ustaleniu poziomów ({ld}) była korekta kursu (dywidenda/split {ad}) - sprawdź pivot i stop",
                                "en": "Price adjustment (dividend/split {ad}) after the levels were set ({ld}) - check pivot and stop"},
    "a_event_soon": {"pl": "{date}: {etype} - {title} ({kind})", "en": "{date}: {etype} - {title} ({kind})"},
    "a_ticker_renamed": {"pl": "Spółka notowana teraz jako {new} (zmiana nazwy) - dodaj {new} i usuń stary ticker",
                         "en": "Now listed as {new} (renamed) - add {new} and remove the old ticker"},
    "a_no_price_data": {"pl": "Brak notowań w archiwum GPW (zły ticker albo spółka nienotowana?)",
                        "en": "No quotes in the GPW archive (wrong ticker or not listed?)"},
    "a_no_recent_quotes": {"pl": "Brak notowań od {sessions} sesji (ostatnie {last}) - zawieszenie?",
                           "en": "No quotes for {sessions} sessions (last {last}) - suspended?"},
    "a_setup_damaged": {"pl": "gpw-trend-screener: setup uszkodzony (zamknięcie pod SMA50 lub ostatnim dołkiem)",
                        "en": "gpw-trend-screener: setup damaged (close below SMA50 or the last contraction low)"},
    "a_quality_red_flags": {"pl": "gpw-quality-screener: czerwone flagi jakości", "en": "gpw-quality-screener: quality red flags"},
    "a_insider_cluster_sell": {"pl": "gpw-ownership: klaster sprzedaży insiderów", "en": "gpw-ownership: insider selling cluster"},
    "a_short_new": {"pl": "gpw-ownership: nowa krótka pozycja w rejestrze KNF", "en": "gpw-ownership: new short position in the KNF register"},
    "a_earnings_strong_negative": {"pl": "gpw-earnings-reaction: silnie negatywna reakcja na wyniki",
                                   "en": "gpw-earnings-reaction: strong negative earnings reaction"},
    "a_dividend_cut_risk": {"pl": "gpw-dividend-analyzer: ryzyko cięcia dywidendy", "en": "gpw-dividend-analyzer: dividend cut risk"},
    # events
    "ev_periodic_report": {"pl": "raport okresowy", "en": "periodic report"},
    "ev_agm": {"pl": "walne zgromadzenie", "en": "general meeting"},
    "ev_dividend_record": {"pl": "dzień dywidendy", "en": "dividend record date"},
    "ev_ex_dividend": {"pl": "odcięcie dywidendy", "en": "ex-dividend"},
    "ev_dividend_payment": {"pl": "wypłata dywidendy", "en": "dividend payment"},
    # sections / columns
    "alerts": {"pl": "Alerty", "en": "Alerts"},
    "no_alerts": {"pl": "Brak alertów.", "en": "No alerts."},
    "items": {"pl": "Spółki na liście", "en": "Items"},
    "c_sev": {"pl": "Waga", "en": "Severity"},
    "c_ticker": {"pl": "Spółka", "en": "Ticker"},
    "c_stage": {"pl": "Etap", "en": "Stage"},
    "c_alert": {"pl": "Alert", "en": "Alert"},
    "c_close": {"pl": "Kurs", "en": "Close"},
    "c_pivot": {"pl": "Pivot", "en": "Pivot"},
    "c_dpivot": {"pl": "Do pivotu", "en": "vs pivot"},
    "c_stop": {"pl": "Stop", "en": "Stop"},
    "c_dstop": {"pl": "Nad stopem", "en": "Above stop"},
    "c_tags": {"pl": "Tagi", "en": "Tags"},
    "c_thesis": {"pl": "Teza", "en": "Thesis"},
    "c_updated": {"pl": "Zmiana", "en": "Updated"},
    "c_src": {"pl": "Źródło", "en": "Source"},
    "changes": {"pl": "Zmiany", "en": "Changes"},
    "no_changes": {"pl": "Brak zmian.", "en": "No changes."},
    "reports_used": {"pl": "Użyte raporty", "en": "Reports used"},
    "reports_stale": {"pl": "Pominięte (za stare)", "en": "Skipped (too old)"},
    "no_reports": {"pl": "Nie znaleziono raportów producentów w {dirs}.", "en": "No producer reports found in {dirs}."},
    "c_skill": {"pl": "Skill", "en": "Skill"},
    "c_file": {"pl": "Plik", "en": "File"},
    "c_asof": {"pl": "Dane na", "en": "As of"},
    "c_used": {"pl": "Użyte fakty", "en": "Facts used"},
    "c_behind": {"pl": "Sesji wstecz", "en": "Sessions behind"},
    "suggestions": {"pl": "Sugestie", "en": "Suggestions"},
    "c_action": {"pl": "Działanie", "en": "Action"},
    "c_reason": {"pl": "Powód", "en": "Reason"},
    "c_cmd": {"pl": "Polecenie", "en": "Command"},
    "act_drop": {"pl": "usuń z listy", "en": "drop"},
    "act_demote": {"pl": "cofnij do obserwacji", "en": "demote to watch"},
    "act_check_journal": {"pl": "sprawdź dziennik", "en": "check the journal"},
    "act_write_thesis": {"pl": "zapisz tezę", "en": "write a thesis"},
    "act_refresh": {"pl": "odśwież ceny", "en": "refresh prices"},
    "act_set_levels": {"pl": "ustal pivot i stop", "en": "set pivot and stop"},
    "r_invalidated": {"pl": "kurs {close} ≤ stop {stop}", "en": "close {close} ≤ stop {stop}"},
    "r_setup_damaged": {"pl": "setup uszkodzony wg skanera", "en": "setup damaged per the screener"},
    "r_stale": {"pl": "brak zmian od {days} dni (ostatnio {last})", "en": "no update for {days} days (last {last})"},
    "r_risk_alert": {"pl": "ostrzeżenie: {codes}", "en": "warning: {codes}"},
    "r_extended": {"pl": "{dist} nad pivotem - punkt wejścia minął", "en": "{dist} above pivot - the entry point has passed"},
    "r_ready_without_thesis": {"pl": "etap ready bez tezy w gpw-trade-journal", "en": "ready without a gpw-trade-journal thesis"},
    "r_no_recent_refresh": {"pl": "brak świeżych cen (ostatnio {last})", "en": "no fresh prices (last {last})"},
    "r_watch_without_levels": {"pl": "obserwacja bez pivotu i stopu", "en": "watch without pivot and stop"},
    "r_position_closed": {"pl": "teza {tid} ma status {st}", "en": "thesis {tid} is {st}"},
    "r_stop_breached": {"pl": "kurs ≤ stop - pozycja otwarta, sprawdź plan", "en": "close ≤ stop - position open, check the plan"},
    "warnings": {"pl": "Ostrzeżenia", "en": "Warnings"},
    "sources": {"pl": "Źródła", "en": "Sources"},
    "src_state": {"pl": "Stan watchlisty: {p}", "en": "Watchlist state: {p}"},
    "src_archive": {"pl": "GPW - Archiwum notowań: {u} (sesja {d}, ceny skorygowane o dywidendy/splity)",
                    "en": "GPW - quotes archive: {u} (session {d}, dividend/split adjusted)"},
    "src_events": {"pl": "gpw-event-calendar: {f} (dane na {d})", "en": "gpw-event-calendar: {f} (as of {d})"},
    "src_report": {"pl": "{s}: {f} (dane na {d})", "en": "{s}: {f} (as of {d})"},
    "src_journal": {"pl": "gpw-trade-journal: {p} ({n} tez)", "en": "gpw-trade-journal: {p} ({n} theses)"},
    "w_no_events": {"pl": "Brak świeżego raportu gpw-event-calendar w {dirs} - wydarzenia nie zostały sprawdzone.",
                    "en": "No fresh gpw-event-calendar report in {dirs} - events were not checked."},
    "w_ready_no_thesis": {"pl": "Brak tezy IDEA/READY dla {t} w dzienniku - zapisz ją w gpw-trade-journal przed wejściem.",
                          "en": "No IDEA/READY thesis for {t} in the journal - write one in gpw-trade-journal before entering."},
    "w_partial_session": {"pl": "Dzisiejsza sesja nie jest jeszcze zamknięta - użyto ostatniej zamkniętej.",
                          "en": "Today's session is not final yet - the last final session was used."},
    "w_missing": {"pl": "Brak notowań dla: {t}.", "en": "No quotes for: {t}."},
    "ok_added": {"pl": "Dodano {t} (etap: {st}).", "en": "Added {t} (stage: {st})."},
    "ok_updated": {"pl": "Zaktualizowano {t} (etap: {st}).", "en": "Updated {t} (stage: {st})."},
    "ok_stage": {"pl": "{t}: {frm} → {to}.", "en": "{t}: {frm} → {to}."},
    "ok_removed": {"pl": "Usunięto {t} z pliku (powód: {r}).", "en": "Removed {t} from the file (reason: {r})."},
    "ok_note": {"pl": "Dodano notatkę do {t}.", "en": "Note added to {t}."},
    "ok_export": {"pl": "Zapisano {n} wierszy do {p}.", "en": "Wrote {n} rows to {p}."},
    "ok_nochange": {"pl": "Bez zmian.", "en": "No change."},
    "saved": {"pl": "Stan zapisany: {p}", "en": "State saved: {p}"},
    "dry_run": {"pl": "Tryb próbny (--dry-run): stan NIE został zapisany.", "en": "Dry run (--dry-run): the state was NOT saved."},
    "plain": {"pl": "Pivot = poziom, którego przebicie potwierdza setup; stop = poziom, który go unieważnia. Etap 'gotowa' to plan, nie zlecenie.",
              "en": "Pivot = the level whose break confirms the setup; stop = the level that invalidates it. 'Ready' is a plan, not an order."},
}

SPOLKA = ("spółka", "spółki", "spółek")


def pl_plural(n: int, forms=SPOLKA) -> str:
    if n == 1:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def _date(s: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD, got {!r}".format(s))


def _price(s: str) -> float:
    try:
        v = float(s.replace(",", "."))
    except ValueError:
        raise argparse.ArgumentTypeError("expected a price, got {!r}".format(s))
    if not v > 0:
        raise argparse.ArgumentTypeError("price must be > 0, got {!r}".format(s))
    return v


# --------------------------------------------------------------------------
# Arguments
# --------------------------------------------------------------------------

def parse_args(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lang", choices=["pl", "en"], default="pl")
    common.add_argument("--state-dir", default=None, help="folder of watchlist.json (default ~/.gpw-trading-skills)")
    common.add_argument("--journal-dir", default=None, help="gpw-trade-journal state (default ~/.gpw-trading-skills/journal)")
    common.add_argument("--no-journal", action="store_true", help="do not read the trade journal")
    common.add_argument("--json", action="store_true", help="print the JSON report instead of Markdown")
    common.add_argument("--output-dir", default=".")
    common.add_argument("--no-files", action="store_true", help="write no report files (the state is still saved)")
    common.add_argument("--dry-run", action="store_true", help="compute and print, do not save the state")

    p = argparse.ArgumentParser(prog="gpw_watchlist.py", description="GPW watchlist (state between screen and trade).")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("add", parents=[common], help="add a ticker or update it")
    a.add_argument("ticker")
    a.add_argument("--stage", choices=["candidate", "watch", "ready"], default=None)
    a.add_argument("--pivot", type=_price)
    a.add_argument("--stop", type=_price)
    a.add_argument("--target", type=_price)
    a.add_argument("--note", default=None)
    a.add_argument("--tag", action="append", default=[])

    for name in ("drop", "remove"):
        d = sub.add_parser(name, parents=[common], help="drop (keep history)" if name == "drop" else "delete the item")
        d.add_argument("ticker")
        d.add_argument("--reason", required=True)

    pr = sub.add_parser("promote", parents=[common], help="change the stage (also demote)")
    pr.add_argument("ticker")
    pr.add_argument("--to", required=True, choices=["candidate", "watch", "ready", "entered"])
    pr.add_argument("--pivot", type=_price)
    pr.add_argument("--stop", type=_price)
    pr.add_argument("--target", type=_price)
    pr.add_argument("--reason", default="")

    lv = sub.add_parser("levels", parents=[common], help="set pivot / stop / target")
    lv.add_argument("ticker")
    lv.add_argument("--pivot", type=_price)
    lv.add_argument("--stop", type=_price)
    lv.add_argument("--target", type=_price)

    n = sub.add_parser("note", parents=[common], help="add a dated note")
    n.add_argument("ticker")
    n.add_argument("text")

    sh = sub.add_parser("show", parents=[common], help="one item in full")
    sh.add_argument("ticker")

    ls = sub.add_parser("list", parents=[common], help="list items")
    ls.add_argument("--stage", action="append", choices=ws.STAGES, default=[])
    ls.add_argument("--tag", default=None)

    ig = sub.add_parser("ingest", parents=[common], help="pull the newest producer reports")
    ig.add_argument("--input-dir", action="append", default=[], help="folder with skill JSON reports (repeat; default .)")
    ig.add_argument("--from", dest="producers", default=None,
                    help="comma-separated producers, e.g. trend-screener,ownership (default: all)")
    ig.add_argument("--rules", default=None, help="JSON file overriding the ingest rules table")
    ig.add_argument("--as-of", type=_date, default=None, help="reference date (default today)")

    rf = sub.add_parser("refresh", parents=[common], help="prices + events -> alerts")
    rf.add_argument("--input-dir", action="append", default=[], help="where to find gpw-event-calendar JSON (default .)")
    rf.add_argument("--events-json", default=None, help="explicit gpw-event-calendar report")
    rf.add_argument("--as-of", type=_date, default=None)
    rf.add_argument("--offline", action="store_true")
    rf.add_argument("--cache-dir", default=None)

    rv = sub.add_parser("review", parents=[common], help="stale / invalidated items -> suggestions")
    rv.add_argument("--stale-days", type=int, default=wm.STALE_DAYS)
    rv.add_argument("--as-of", type=_date, default=None)

    ex = sub.add_parser("export", parents=[common], help="export to CSV")
    ex.add_argument("--csv", required=True, help="output CSV path")
    ex.add_argument("--stage", action="append", choices=ws.STAGES, default=[])

    args = p.parse_args(argv)
    if getattr(args, "stale_days", 1) is not None and getattr(args, "stale_days", 1) < 1:
        p.error("--stale-days must be >= 1")
    return args


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _state_dir(args) -> Path:
    return Path(args.state_dir).expanduser() if args.state_dir else ws.default_state_dir()


def _journal(args) -> Optional[List[Dict]]:
    if args.no_journal:
        return None
    d = Path(args.journal_dir).expanduser() if args.journal_dir else journal_io.default_state_dir()
    return journal_io.load_theses(d)


def _journal_dir(args) -> Path:
    return Path(args.journal_dir).expanduser() if args.journal_dir else journal_io.default_state_dir()


def _ref_date(args) -> dt.date:
    return getattr(args, "as_of", None) or plcal.today_warsaw()


def _fmt_price(x, lang):
    return num(x, 2, lang) if x is not None else "—"


def alert_text(a: Dict, t: T, lang: str) -> str:
    p = a.get("params") or {}
    code = a.get("code")
    key = "a_" + str(code)
    if code in ("stop_breached", "near_stop"):
        return t(key, close=_fmt_price(p.get("close"), lang), stop=_fmt_price(p.get("stop"), lang),
                 dist=pct(p.get("dist_pct"), 1, lang, signed=True))
    if code == "pivot_breakout":
        return t(key, pivot=_fmt_price(p.get("pivot"), lang), close=_fmt_price(p.get("close"), lang),
                 dist=pct(p.get("dist_pct"), 1, lang, signed=True), tr=num(p.get("turnover_ratio"), 1, lang),
                 conf=t("conf_yes") if p.get("volume_confirmed") else t("conf_no"))
    if code in ("in_buy_range", "extended", "near_pivot"):
        return t(key, pivot=_fmt_price(p.get("pivot"), lang), dist=pct(p.get("dist_pct"), 1, lang, signed=True))
    if code == "new_52w_high":
        return t(key, high=_fmt_price(p.get("high_52w"), lang))
    if code == "turnover_surge":
        return t(key, tr=num(p.get("turnover_ratio"), 1, lang), chg=pct(p.get("change_pct"), 1, lang, signed=True))
    if code == "levels_pre_adjustment":
        return t(key, ld=p.get("levels_date"), ad=p.get("adjustment_date"))
    if code == "event_soon":
        et = p.get("event_type") or "other"
        etxt = t("ev_" + et) if ("ev_" + et) in STRINGS else et
        return t(key, date=p.get("event_date"), etype=etxt, title=p.get("title") or "", kind=p.get("date_kind") or "")
    if code == "ticker_renamed":
        return t(key, new=p.get("new_ticker"))
    if code == "no_recent_quotes":
        return t(key, sessions=p.get("sessions"), last=p.get("last_date"))
    if key in STRINGS:
        return t(key)
    return str(code)


def with_messages(alerts: List[Dict], t: T, lang: str) -> List[Dict]:
    return [dict(a, message=alert_text(a, t, lang)) for a in alerts]


def headline(state: Dict, alerts: List[Dict], t: T, lang: str) -> str:
    def tickers(code, sev=None, origin=None):
        return {a["ticker"] for a in alerts if a.get("code") == code and (sev is None or a.get("severity") == sev)
                and (origin is None or a.get("origin") == origin)}

    def w(n):
        return pl_plural(n) if lang == "pl" else ""

    parts = []
    lead = ""
    n = len(tickers("stop_breached"))
    if n:  # the stop question is its own sentence, first
        lead = t("h_stop", n=n, w=w(n), v=pl_plural(n, ("przebiła", "przebiły", "przebiło")))
    for code, key in (("pivot_breakout", "h_breakout"), ("near_pivot", "h_near")):
        n = len(tickers(code))
        if n:
            parts.append(t(key, n=n, w=w(n)))
    for code, key in (("in_buy_range", "h_buy"), ("extended", "h_ext"), ("near_stop", "h_near_stop")):
        n = len(tickers(code))
        if n:
            parts.append(t(key, n=n))
    n = len({a["ticker"] for a in alerts if a.get("origin") == "ingest" and a.get("severity") in ("high", "medium")})
    if n:
        parts.append(t("h_risk", n=n))
    n = len(tickers("event_soon"))
    if n:
        parts.append(t("h_events", n=n))
    if parts or lead:
        s = ", ".join(parts)
        s = (s[0].upper() + s[1:] + ".") if s else ""
        return (lead + " " + s).strip()
    by = ws.summary_by_stage(state)
    active = sum(v for k, v in by.items() if k != "dropped")
    stages = ", ".join("{} {}".format(v, t("st_" + k)) for k, v in by.items() if v and k != "dropped") or "0"
    return t("h_none", total=active, stages=stages)


def summary(state: Dict, alerts: List[Dict]) -> Dict:
    by = ws.summary_by_stage(state)
    sev = {"high": 0, "medium": 0, "low": 0}
    for a in alerts:
        sev[a.get("severity", "low")] = sev.get(a.get("severity", "low"), 0) + 1
    def cnt(code):
        return len({a["ticker"] for a in alerts if a.get("code") == code})
    return {"by_stage": by, "total": len(state["items"]),
            "active": sum(v for k, v in by.items() if k != "dropped"),
            "alerts_by_severity": sev,
            "stop_breached": cnt("stop_breached"), "near_stop": cnt("near_stop"),
            "pivot_breakout": cnt("pivot_breakout"), "near_pivot": cnt("near_pivot"),
            "in_buy_range": cnt("in_buy_range"), "extended": cnt("extended"), "events_soon": cnt("event_soon")}


def build_provenance(state: Dict, ref: Optional[dt.date], extra_sources: List[str], sessions_used: int = 0,
                     notes: Optional[List[str]] = None) -> Dict:
    lr = state.get("last_refresh") or {}
    if lr.get("as_of"):
        as_of = dt.date.fromisoformat(lr["as_of"])
        n = list(notes or [])
    else:
        as_of = ref or plcal.today_warsaw()
        n = list(notes or []) + ["No price refresh yet: as_of is the run date, market fields are empty."]
    return provenance.build(as_of=as_of, sources=[ARCHIVE_URL] + extra_sources, sessions_used=sessions_used,
                            universe_size=len([i for i in state["items"] if i["stage"] != "dropped"]),
                            notes=n, corporate_actions_adjusted=True, survivorship_free=True,
                            reference_date=ref)


def build_report(args, state: Dict, command: str, items: List[Dict], alerts: List[Dict], t: T, *,
                 prov: Dict, extra: Optional[Dict] = None, head: Optional[str] = None) -> Dict:
    alerts = with_messages(alerts, t, args.lang)
    rep = {
        "skill": ws.SKILL,
        "version": VERSION,
        "lang": args.lang,
        "command": command,
        "as_of": prov["as_of"],
        "generated_at": ws.now_iso(),
        "state_file": str(ws.state_path(_state_dir(args))),
        "schema_version": ws.SCHEMA_VERSION,
        "answer": head or headline(state, alerts, t, args.lang),
        "summary": summary(state, alerts),
        "items": items,
        "alerts": alerts,
    }
    rep.update(extra or {})
    rep["data_provenance"] = prov
    return rep


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

def _tags_md(it: Dict) -> str:
    risk = {k for k, r in wi.DEFAULT_RULES.items() if r.get("risk")}
    risk_tags = {wi.DEFAULT_RULES[k].get("tag") for k in risk}
    return ", ".join(("⚠️" + x) if x in risk_tags else x for x in it.get("tags") or []) or "—"


def render_md(rep: Dict, t: T) -> str:
    lang = rep["lang"]
    L = ["# {} — {}".format(t("title"), t("cmd_" + rep["command"]) if ("cmd_" + rep["command"]) in STRINGS
                           else rep["command"]), ""]
    fr = rep["data_provenance"]["freshness"]
    lr_missing = any("No price refresh yet" in x for x in rep["data_provenance"].get("notes", []))
    if lr_missing and rep["items"]:
        L += ["> " + t("never_refreshed"), ""]
    elif fr.get("is_stale"):
        L += ["> " + t("stale", n=fr["sessions_behind"], d=fr["as_of"]), ""]
    L += ["**{}**".format(rep["answer"]), ""]
    for w in rep.get("warnings") or []:
        L.append("- ⚠️ " + w)
    if rep.get("warnings"):
        L.append("")
    if rep.get("dry_run"):
        L += ["> " + t("dry_run"), ""]
    if rep["command"] == "ingest":
        L += _ingest_md(rep, t)
    if rep["command"] == "review":
        L += _review_md(rep, t)
    if rep["command"] in ("list", "refresh"):
        L += ["## " + t("alerts"), ""]
        if rep["alerts"]:
            L += ["| {} | {} | {} | {} |".format(t("c_sev"), t("c_ticker"), t("c_stage"), t("c_alert")),
                  "|---|---|---|---|"]
            # one row per (severity, ticker): several low alerts of one stock read as one line
            groups: Dict = {}
            for a in rep["alerts"]:
                groups.setdefault((a["severity"], a["ticker"], a["stage"]), []).append(a["message"].replace("|", "/"))
            for (sev, tk, stg), msgs in groups.items():
                L.append("| {} | {} | {} | {} |".format(t("sev_" + sev), tk, t("st_" + stg), "; ".join(msgs)))
        else:
            L.append(t("no_alerts"))
        L.append("")
    if rep["items"]:
        L += ["## " + t("items"), "",
              "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                  t("c_ticker"), t("c_stage"), t("c_close"), t("c_pivot"), t("c_dpivot"), t("c_stop"), t("c_dstop"),
                  t("c_tags"), t("c_thesis"), t("c_updated")),
              "|---|---|---:|---:|---:|---:|---:|---|---|---|"]
        for it in rep["items"]:
            mk = it.get("market") or {}
            L.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                it["ticker"], t("st_" + it["stage"]), _fmt_price(mk.get("close"), lang), _fmt_price(it.get("pivot"), lang),
                pct(mk.get("dist_to_pivot_pct"), 1, lang, signed=True), _fmt_price(it.get("stop"), lang),
                pct(mk.get("dist_to_stop_pct"), 1, lang, signed=True), _tags_md(it),
                "{} ({})".format(it["thesis_id"], it.get("thesis_status") or "?") if it.get("thesis_id") else "—",
                it.get("updated") or "—"))
        L += ["", "_" + t("plain") + "_", ""]
    elif rep["command"] in ("list", "refresh"):
        L += [t("empty"), ""]
    L += ["## " + t("sources"), ""]
    for s in rep.get("sources_md") or []:
        L.append("- " + s)
    L += ["", "---", DISCLAIMER[lang], ""]
    return "\n".join(L)


def _change_md(c: Dict, t: T) -> str:
    ch = c.get("change")
    tk = c.get("ticker") or ""
    if ch == "added":
        return "➕ {} → {} ({}: {})".format(tk, t("st_" + c["to"]), c.get("skill"), c.get("kind"))
    if ch == "stage":
        return "⬆️ {}: {} → {} ({})".format(tk, t("st_" + c["from"]), t("st_" + c["to"]), c.get("skill") or c.get("thesis_id"))
    if ch == "tag":
        return "🏷️ {}: {} ({})".format(tk, c["tag"], c.get("skill"))
    if ch == "tag_cleared":
        return "🧹 {}: −{} ({})".format(tk, c["tag"], c.get("skill"))
    if ch == "alert":
        return "🔔 {}: {} [{}]".format(tk, t("a_" + c["code"]) if ("a_" + c["code"]) in STRINGS else c["code"], c["severity"])
    if ch == "levels":
        return "📏 {}: pivot/stop {} → {} ({})".format(tk, c.get("from"), c.get("to"), c.get("skill"))
    if ch == "seen_while_dropped":
        return "👀 {} ({}): {} {}".format(tk, t("st_dropped"), c.get("skill"), c.get("kind"))
    if ch == "thesis_linked":
        return "🔗 {}: {}".format(tk, c.get("thesis_id"))
    if ch == "thesis_status":
        return "🔗 {}: {}".format(tk, c.get("status"))
    if ch == "alerts_expired":
        return "⌛ {} alerts expired".format(c.get("count"))
    return json.dumps(c, ensure_ascii=False)


def _ingest_md(rep: Dict, t: T) -> List[str]:
    L = ["## " + t("reports_used"), ""]
    if rep.get("reports"):
        L += ["| {} | {} | {} | {} | {} |".format(t("c_skill"), t("c_file"), t("c_asof"), t("c_behind"), t("c_used")),
              "|---|---|---|---:|---:|"]
        for r in rep["reports"]:
            L.append("| {}{} | {} | {} | {} | {}/{} |".format(r["skill"], " ({})".format(r["mode"]) if r.get("mode") else "",
                                                           r["file"], r["as_of"], r["sessions_behind"], r["used"], r["facts"]))
    else:
        L.append(t("no_reports", dirs=", ".join(rep.get("input_dirs") or ["."])))
    L.append("")
    if rep.get("stale_reports"):
        L += ["### " + t("reports_stale"), ""]
        for r in rep["stale_reports"]:
            L.append("- {}{}: {} ({}, {} > {})".format(r["skill"], " ({})".format(r["mode"]) if r.get("mode") else "",
                                                    r["file"], r["as_of"], r["sessions_behind"], r["max_age"]))
        L.append("")
    L += ["## " + t("changes"), ""]
    L += _changes_grouped(rep.get("changes") or [], t) or [t("no_changes")]
    L.append("")
    return L


def _changes_grouped(changes: List[Dict], t: T) -> List[str]:
    """Group simple changes (added / tag / tag_cleared) into one line per kind."""
    groups: Dict = {}
    order: List = []
    lines = []
    for c in changes:
        ch = c.get("change")
        if ch == "added":
            key = ("➕", t("st_" + c["to"]), c.get("kind"))
        elif ch == "tag":
            key = ("🏷️", c["tag"], c.get("skill"))
        elif ch == "tag_cleared":
            key = ("🧹 −", c["tag"], c.get("skill"))
        else:
            lines.append("- " + _change_md(c, t))
            continue
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c.get("ticker"))
    out = ["- {} {} ({}): {}".format(k[0], k[1], k[2], ", ".join(groups[k])) for k in order]
    return out + lines


def _review_md(rep: Dict, t: T) -> List[str]:
    lang = rep["lang"]
    L = ["## " + t("suggestions"), ""]
    sg = rep.get("suggestions") or []
    if not sg:
        return L + [t("h_review_none"), ""]
    L += ["| {} | {} | {} | {} | {} |".format(t("c_ticker"), t("c_stage"), t("c_action"), t("c_reason"), t("c_cmd")),
          "|---|---|---|---|---|"]
    for s in sg:
        d = s.get("details") or {}
        r = s["reason"]
        if r == "invalidated":
            txt = t("r_invalidated", close=_fmt_price(d.get("close"), lang), stop=_fmt_price(d.get("stop"), lang))
        elif r == "stale":
            txt = t("r_stale", days=d.get("days"), last=d.get("last_update"))
        elif r == "risk_alert":
            txt = t("r_risk_alert", codes=", ".join(d.get("codes") or []))
        elif r == "extended":
            txt = t("r_extended", dist=pct(d.get("dist_pct"), 1, lang, signed=True))
        elif r == "no_recent_refresh":
            txt = t("r_no_recent_refresh", last=d.get("last_date") or "—")
        elif r == "position_closed":
            txt = t("r_position_closed", tid=d.get("thesis_id"), st=d.get("thesis_status"))
        else:
            txt = t("r_" + r)
        L.append("| {} | {} | {} | {} | `{}` |".format(s["ticker"], t("st_" + s["stage"]), t("act_" + s["action"]), txt,
                                                     s.get("command") or ""))
    return L + [""]


def suggestion_command(s: Dict) -> str:
    base = "python3 scripts/gpw_watchlist.py"
    if s["action"] == "drop":
        return '{} drop {} --reason "{}"'.format(base, s["ticker"], s["reason"])
    if s["action"] == "demote":
        return "{} promote {} --to watch --reason {}".format(base, s["ticker"], s["reason"])
    if s["action"] == "refresh":
        return "{} refresh".format(base)
    if s["action"] == "set_levels":
        return "{} levels {} --pivot P --stop S".format(base, s["ticker"])
    if s["action"] == "write_thesis":
        return "gpw-trade-journal: add {}".format(s["ticker"])
    return ""


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

class Ctx:
    def __init__(self, args):
        self.args = args
        self.t = T(STRINGS, args.lang)
        self.today = plcal.today_warsaw()
        self.state, self.migration = ws.load_state(_state_dir(args), self.today)
        self.theses = _journal(args)
        self.sync_changes = ws.sync_journal(self.state, self.theses, self.today) if self.theses is not None else []
        self.warnings: List[str] = []

    def save(self) -> Optional[Dict]:
        if self.args.dry_run:
            return None
        return ws.save_state(self.state, _state_dir(self.args))

    def journal_sources(self) -> List[str]:
        if self.theses is None:
            return []
        return [self.t("src_journal", p=_journal_dir(self.args), n=len(self.theses))]


def _emit_simple(ctx: Ctx, msg: str, item: Optional[Dict] = None) -> int:
    res = ctx.save()
    if ctx.args.json:
        print(json.dumps({"skill": ws.SKILL, "version": VERSION, "command": ctx.args.command, "message": msg,
                          "item": item, "warnings": ctx.warnings, "saved": bool(res and res["written"]),
                          "state_file": str(ws.state_path(_state_dir(ctx.args)))}, ensure_ascii=False, indent=2))
    else:
        print(msg)
        for w in ctx.warnings:
            print("⚠️ " + w)
        if ctx.args.dry_run:
            print(ctx.t("dry_run"))
    return 0


def _check_ready_journal(ctx: Ctx, it: Dict) -> None:
    if ctx.theses is None:
        return
    open_ = ws.theses_for(ctx.theses, it["ticker"], ["IDEA", "READY"])
    if open_:
        it["thesis_id"] = open_[0]["id"]
        it["thesis_status"] = open_[0].get("status")
    elif not ws.theses_for(ctx.theses, it["ticker"], ["ACTIVE"]):
        ctx.warnings.append(ctx.t("w_ready_no_thesis", t=it["ticker"]))


def cmd_add(ctx: Ctx) -> int:
    a = ctx.args
    tk = ws.norm_ticker(a.ticker)
    it = ws.find_item(ctx.state, tk)
    created = it is None
    stage = a.stage or "candidate"
    if created:
        it = ws.new_item(tk, ctx.today, "candidate" if stage == "ready" else stage, source="user")
        ctx.state["items"].append(it)
    if a.pivot is not None or a.stop is not None or a.target is not None:
        ws.set_levels(it, ctx.today, a.pivot, a.stop, a.target, by="user")
    for tag in a.tag:
        ws.add_tag(it, tag, date=ctx.today.isoformat())
    if a.note:
        ws.add_note(it, ctx.today, a.note)
    target_stage = a.stage or ("candidate" if it["stage"] == "dropped" else None)
    if target_stage and it["stage"] != target_stage:
        reason = "re-added" if it["stage"] == "dropped" else "add --stage"
        if it["stage"] == "dropped" and target_stage == "ready":
            ws.transition(it, "watch", ctx.today, reason)
            reason = "add --stage ready"
        ws.transition(it, target_stage, ctx.today, reason)
        if target_stage == "ready":
            _check_ready_journal(ctx, it)
    if ctx.theses is not None:
        ws.sync_journal(ctx.state, ctx.theses, ctx.today)
    msg = ctx.t("ok_added" if created else "ok_updated", t=tk, st=ctx.t("st_" + it["stage"]))
    return _emit_simple(ctx, msg, it)


def cmd_drop(ctx: Ctx) -> int:
    it = ws.require_item(ctx.state, ctx.args.ticker)
    frm = it["stage"]
    ws.transition(it, "dropped", ctx.today, ctx.args.reason)
    return _emit_simple(ctx, ctx.t("ok_stage", t=it["ticker"], frm=ctx.t("st_" + frm), to=ctx.t("st_dropped")), it)


def cmd_remove(ctx: Ctx) -> int:
    it = ws.require_item(ctx.state, ctx.args.ticker)
    reason = ctx.args.reason.strip()
    if not reason:
        raise ws.WatchlistError("removing needs a reason (--reason)")
    ctx.state["items"].remove(it)
    ctx.state.setdefault("removed", []).append({"ticker": it["ticker"], "date": ctx.today.isoformat(),
                                                "stage": it["stage"], "reason": reason})
    return _emit_simple(ctx, ctx.t("ok_removed", t=it["ticker"], r=reason), None)


def cmd_promote(ctx: Ctx) -> int:
    a = ctx.args
    it = ws.require_item(ctx.state, a.ticker)
    if a.pivot is not None or a.stop is not None or a.target is not None:
        ws.set_levels(it, ctx.today, a.pivot, a.stop, a.target, by="user")
    frm = it["stage"]
    thesis_id = None
    if a.to == "entered":
        if ctx.theses is None:
            raise ws.WatchlistError("entered is set from the trade journal - do not use --no-journal")
        act = ws.theses_for(ctx.theses, it["ticker"], ["ACTIVE"])
        if not act:
            raise ws.WatchlistError("no ACTIVE gpw-trade-journal thesis for {} - log the buy in the journal first".format(
                it["ticker"]))
        thesis_id = act[0]["id"]
        it["thesis_id"], it["thesis_status"] = thesis_id, "ACTIVE"
    if frm == "dropped" and a.to == "ready":
        ws.transition(it, "watch", ctx.today, a.reason or "re-opened")
    ws.transition(it, a.to, ctx.today, a.reason or "promote", thesis_id=thesis_id)
    if a.to == "ready":
        _check_ready_journal(ctx, it)
    return _emit_simple(ctx, ctx.t("ok_stage", t=it["ticker"], frm=ctx.t("st_" + frm), to=ctx.t("st_" + a.to)), it)


def cmd_levels(ctx: Ctx) -> int:
    a = ctx.args
    if a.pivot is None and a.stop is None and a.target is None:
        raise ws.WatchlistError("give at least one of --pivot, --stop, --target")
    it = ws.require_item(ctx.state, a.ticker)
    changed = ws.set_levels(it, ctx.today, a.pivot, a.stop, a.target, by="user")
    if it["stage"] == "ready":
        ws.check_levels(it["pivot"], it["stop"])
    msg = ctx.t("ok_updated", t=it["ticker"], st=ctx.t("st_" + it["stage"])) if changed else ctx.t("ok_nochange")
    return _emit_simple(ctx, msg, it)


def cmd_note(ctx: Ctx) -> int:
    it = ws.require_item(ctx.state, ctx.args.ticker)
    ws.add_note(it, ctx.today, ctx.args.text)
    return _emit_simple(ctx, ctx.t("ok_note", t=it["ticker"]), it)


def cmd_show(ctx: Ctx) -> int:
    it = ws.require_item(ctx.state, ctx.args.ticker)
    ctx.save()  # journal sync only
    print(json.dumps(it, ensure_ascii=False, indent=2))
    return 0


def _write_report(ctx: Ctx, rep: Dict, prefix: str) -> List[str]:
    t = ctx.t
    md = render_md(rep, t)
    rep.pop("sources_md", None)
    paths = []
    if not ctx.args.no_files:
        out = Path(ctx.args.output_dir).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        jp = out / "{}_{}.json".format(prefix, stamp)
        mp = out / "{}_{}.md".format(prefix, stamp)
        jp.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        mp.write_text(md, encoding="utf-8")
        paths = [str(jp), str(mp)]
    if ctx.args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(md)
    for p in paths:
        print(("JSON: " if p.endswith(".json") else "Markdown: ") + p, file=sys.stderr)
    return paths


def _sources_md(ctx: Ctx, extra: Optional[List[str]] = None) -> List[str]:
    t = ctx.t
    out = [t("src_state", p=ws.state_path(_state_dir(ctx.args)))]
    lr = ctx.state.get("last_refresh") or {}
    if lr.get("as_of"):
        out.append(t("src_archive", u=ARCHIVE_URL, d=lr["as_of"]))
    return out + list(extra or []) + ctx.journal_sources()


def cmd_list(ctx: Ctx) -> int:
    a = ctx.args
    ref = plcal.today_warsaw()
    items = [it for it in ws.sort_items(ctx.state["items"])
             if (not a.stage or it["stage"] in a.stage) and (not a.tag or a.tag.lower() in it.get("tags", []))]
    if not a.stage:
        items = [it for it in items if it["stage"] != "dropped"] + [it for it in items if it["stage"] == "dropped"]
    tickers = {it["ticker"] for it in items}
    alerts = [x for x in wm.all_alerts(ctx.state) if x["ticker"] in tickers]
    res = ctx.save()
    prov = build_provenance(ctx.state, None, [], notes=["State listing; market fields come from the last refresh."])
    rep = build_report(a, ctx.state, "list", items, alerts, ctx.t, prov=prov,
                       extra={"warnings": ctx.warnings, "changes": ctx.sync_changes, "filters": {"stage": a.stage, "tag": a.tag},
                              "sources_md": _sources_md(ctx), "saved": bool(res and res["written"]), "reference_date": ref.isoformat()})
    _write_report(ctx, rep, "gpw_watchlist_list")
    return 0


def cmd_ingest(ctx: Ctx) -> int:
    a = ctx.args
    ref = a.as_of or ctx.today
    dirs = a.input_dir or ["."]
    only = {wi.resolve_skill(x) for x in a.producers.split(",") if x.strip()} if a.producers else None
    overrides = None
    if a.rules:
        try:
            overrides = json.loads(Path(a.rules).expanduser().read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ws.WatchlistError("cannot read rules file {}: {}".format(a.rules, exc))
        if not isinstance(overrides, dict):
            raise ws.WatchlistError("rules file must be a JSON object {kind: rule}")
    rules = wi.merge_rules(overrides)
    found: List[Dict] = []
    for d in dirs:
        if not Path(d).expanduser().is_dir():
            raise ws.WatchlistError("input dir not found: {}".format(d))
        for skill in wi.PRODUCERS:
            found.extend(reports.iter_reports(d, skill=skill, reference=ref))
    res = wi.ingest(ctx.state, found, rules, ref, only=only)
    if ctx.theses is not None:
        res["changes"].extend(ws.sync_journal(ctx.state, ctx.theses, ctx.today))
    ctx.state["last_ingest"] = {"at": ws.now_iso(), "reference_date": ref.isoformat(),
                                "reports": [{k: r[k] for k in ("skill", "mode", "file", "as_of")} for r in res["reports"]]}
    changes = ctx.sync_changes + res["changes"]
    saved = ctx.save()
    t = ctx.t
    cnt = lambda ch: sum(1 for c in changes if c.get("change") == ch)  # noqa: E731
    head = t("h_ingest", added=cnt("added"), upgraded=sum(1 for c in changes if c.get("change") == "stage" and c.get("to") == "watch"),
             alerts=cnt("alert"), tags=cnt("tag"), reports=len(res["reports"]))
    touched = {c["ticker"] for c in changes if c.get("ticker")}
    items = [it for it in ws.sort_items(ctx.state["items"]) if it["ticker"] in touched]
    alerts = [x for x in wm.all_alerts(ctx.state) if x.get("origin") == "ingest" and x["ticker"] in touched]
    srcs = [t("src_report", s=r["skill"], f=r["file"], d=r["as_of"]) for r in res["reports"]]
    prov = build_provenance(ctx.state, a.as_of, ["{} {}".format(r["skill"], r["file"]) for r in res["reports"]],
                            notes=["Ingest of producer JSON reports (gpwlib.reports.iter_reports)."])
    rep = build_report(a, ctx.state, "ingest", items, alerts, t, prov=prov, head=head,
                       extra={"reports": res["reports"], "stale_reports": res["stale"], "changes": changes,
                              "input_dirs": [str(Path(d).expanduser()) for d in dirs],
                              "rules": rules, "warnings": ctx.warnings, "dry_run": a.dry_run,
                              "saved": bool(saved and saved["written"]), "sources_md": _sources_md(ctx, srcs)})
    _write_report(ctx, rep, "gpw_watchlist_ingest")
    return 0


def _load_events(ctx: Ctx, ref: dt.date) -> Dict:
    a = ctx.args
    if a.events_json:
        rep = reports.find_report("gpw-event-calendar", None, explicit_path=a.events_json, reference=ref, lang=a.lang)
        reps = [rep]
    else:
        reps = []
        for d in (a.input_dir or ["."]):
            for r in reports.iter_reports(d, skill="gpw-event-calendar", reference=ref):
                r2 = reports.describe(Path(r["path"]), r["data"], "gpw-event-calendar", reference=ref, lang=a.lang)
                if not r2["stale"]:
                    reps.append(r2)
    events, seen, used = [], set(), []
    for r in reps:
        used.append({"file": r["file"], "as_of": r["as_of"], "mode": r["data"].get("mode"), "stale": r["stale"],
                     "warning": r["warning_text"]})
        for e in r["data"].get("events") or []:
            key = e.get("id") or (e.get("ticker"), e.get("type"), e.get("date"))
            if key in seen:
                continue
            seen.add(key)
            events.append(e)
    return {"events": events, "reports": used}


def cmd_refresh(ctx: Ctx) -> int:
    a = ctx.args
    t = ctx.t
    ref = a.as_of or ctx.today
    cache_dir = Path(a.cache_dir).expanduser() if a.cache_dir else None
    print("Loading GPW sessions from the archive cache...", file=sys.stderr)
    sessions = gpw_archive.load_sessions(wm.HIGH_LOOKBACK + 15, end=ref, cache_dir=cache_dir, offline=a.offline,
                                         progress=False)
    if sessions and sessions[-1]["date"] == plcal.today_warsaw().isoformat() and \
            not plcal.session_is_final(plcal.today_warsaw()):
        sessions = sessions[:-1]
        ctx.warnings.append(t("w_partial_session"))
    if len(sessions) < 60:  # 50-session turnover base + ATR(14) need at least ~60 sessions
        raise gpw_archive.GPWFetchError("Only {} sessions available; need at least 60 (run online once).".format(
            len(sessions)))
    pnl = panel.build_panel(sessions)
    price_as_of = dt.date.fromisoformat(sessions[-1]["date"])
    ev = _load_events(ctx, ref)
    if not ev["reports"]:
        ctx.warnings.append(t("w_no_events", dirs=", ".join(a.input_dir or ["."])))
    for r in ev["reports"]:
        if r.get("warning"):
            ctx.warnings.append(r["warning"])
    stops = {}
    if ctx.theses is not None:
        for th in ctx.theses:
            if th.get("status") == "ACTIVE" and (th.get("plan") or {}).get("stop"):
                stops.setdefault(str(th.get("ticker", "")).upper(), th["plan"]["stop"])
    res = wm.refresh(ctx.state, pnl, price_as_of, ev["events"], stops)
    if res["missing"]:
        ctx.warnings.append(t("w_missing", t=", ".join(res["missing"])))
    saved = ctx.save()
    prov = build_provenance(ctx.state, a.as_of, ["gpw-event-calendar " + r["file"] for r in ev["reports"]],
                            sessions_used=len(sessions),
                            notes=["Prices dividend/split-adjusted (gpwlib.panel); pivot/stop are compared with the latest close."])
    items = [it for it in ws.sort_items(ctx.state["items"]) if it["stage"] != "dropped"]
    srcs = [t("src_events", f=r["file"], d=r["as_of"]) for r in ev["reports"]]
    rep = build_report(a, ctx.state, "refresh", items, res["alerts"], t, prov=prov,
                       extra={"warnings": ctx.warnings, "changes": ctx.sync_changes, "event_reports": ev["reports"],
                              "missing": res["missing"], "dry_run": a.dry_run,
                              "saved": bool(saved and saved["written"]), "sources_md": _sources_md(ctx, srcs),
                              "thresholds": {"buy_range_pct": wm.BUY_RANGE_PCT, "near_pivot_pct": wm.NEAR_PIVOT_PCT,
                                             "turnover_surge": wm.TURNOVER_SURGE, "turnover_base": wm.TURNOVER_BASE,
                                             "near_stop_atr": wm.NEAR_STOP_ATR,
                                             "event_horizon_sessions": wm.EVENT_HORIZON_SESSIONS}})
    _write_report(ctx, rep, "gpw_watchlist_refresh")
    return 0


def cmd_review(ctx: Ctx) -> int:
    a = ctx.args
    t = ctx.t
    ref = a.as_of or ctx.today
    sg = wm.review(ctx.state, ref, a.stale_days)
    for s in sg:
        s["command"] = suggestion_command(s)
    saved = ctx.save()
    if sg:
        counts: Dict[str, int] = {}
        for s in sg:
            counts[s["action"]] = counts.get(s["action"], 0) + 1
        head = t("h_review", n=len(sg), w=pl_plural(len(sg), ("sugestia", "sugestie", "sugestii")), parts=", ".join("{} × {}".format(v, t("act_" + k)) for k, v in counts.items()))
    else:
        head = t("h_review_none")
    touched = {s["ticker"] for s in sg}
    items = [it for it in ws.sort_items(ctx.state["items"]) if it["ticker"] in touched]
    alerts = [x for x in wm.all_alerts(ctx.state) if x["ticker"] in touched]
    prov = build_provenance(ctx.state, a.as_of, [], notes=["Review of the stored state; run refresh first for current prices."])
    rep = build_report(a, ctx.state, "review", items, alerts, t, prov=prov, head=head,
                       extra={"suggestions": sg, "stale_days": a.stale_days, "warnings": ctx.warnings,
                              "changes": ctx.sync_changes, "saved": bool(saved and saved["written"]),
                              "sources_md": _sources_md(ctx)})
    _write_report(ctx, rep, "gpw_watchlist_review")
    return 0


CSV_COLUMNS = ["ticker", "stage", "added", "updated", "source", "pivot", "stop", "target", "close", "dist_to_pivot_pct",
               "dist_to_stop_pct", "market_date", "thesis_id", "thesis_status", "tags", "alerts", "last_note", "sources"]


def cmd_export(ctx: Ctx) -> int:
    a = ctx.args
    rows = []
    for it in ws.sort_items(ctx.state["items"]):
        if a.stage and it["stage"] not in a.stage:
            continue
        mk = it.get("market") or {}
        rows.append({
            "ticker": it["ticker"], "stage": it["stage"], "added": it["added"], "updated": it["updated"],
            "source": it.get("source"), "pivot": it.get("pivot"), "stop": it.get("stop"), "target": it.get("target"),
            "close": mk.get("close"), "dist_to_pivot_pct": mk.get("dist_to_pivot_pct"),
            "dist_to_stop_pct": mk.get("dist_to_stop_pct"), "market_date": mk.get("date"),
            "thesis_id": it.get("thesis_id"), "thesis_status": it.get("thesis_status"),
            "tags": ";".join(it.get("tags") or []),
            "alerts": ";".join("{}:{}".format(x.get("severity"), x.get("code")) for x in it.get("alerts") or []),
            "last_note": (it["notes"][-1]["text"] if it.get("notes") else ""),
            "sources": ";".join(sorted({s.get("skill") or "" for s in it.get("sources") or []})),
        })
    p = Path(a.csv).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(str(p), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    ctx.save()
    print(ctx.t("ok_export", n=len(rows), p=p))
    return 0


COMMANDS = {"add": cmd_add, "drop": cmd_drop, "remove": cmd_remove, "promote": cmd_promote, "levels": cmd_levels,
            "note": cmd_note, "show": cmd_show, "list": cmd_list, "ingest": cmd_ingest, "refresh": cmd_refresh,
            "review": cmd_review, "export": cmd_export}


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        ctx = Ctx(args)
        return COMMANDS[args.command](ctx)
    except (ws.WatchlistError, reports.ReportError, gpw_archive.GPWFetchError, OSError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
