#!/usr/bin/env python3
"""gpw-event-calendar: one calendar of events that can move a GPW position.

Modes
-----
upcoming              next N sessions, whole market (default 15 sessions)
ticker X              past (lookback) and next events of one company
portfolio             journal open positions + --tickers + watchlist
window --ticker X     does a holding window (--days sessions) contain
                      earnings / ex-dividend / AGM / MPC / expiry? -> holding_window_check{}

Examples
--------
python3 scripts/gpw_event_calendar.py upcoming --sessions 15
python3 scripts/gpw_event_calendar.py ticker PKOBP
python3 scripts/gpw_event_calendar.py portfolio --tickers PKOBP,KETY,CDPROJEKT,PZU --ics
python3 scripts/gpw_event_calendar.py window --ticker PZU --days 20
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import constituents, fmt, fundamentals, gpw_archive, http, journal_io, panel, plcal, provenance  # noqa: E402

import event_calendar_engine as E  # noqa: E402
import event_calendar_sources as S  # noqa: E402

D = dt.date
SKILL = "gpw-event-calendar"
VERSION = "1.0.0"
PREFIX = "gpw_event_calendar"
DEFAULT_LOOKBACK_DAYS = 120
DEFAULT_MAX_REPORTS = 150      # report bodies fetched per run (1 request each, cached a year)
TICKER_COMPANY_HORIZON_DAYS = 370   # ticker mode: next dividend/AGM window up to a year ahead
ARCHIVE_SESSIONS = 500         # two years of quotes -> two dividend seasons for the ex-date pattern
WATCHLIST_PATH = Path.home() / ".gpw-trading-skills" / "watchlist.json"

STRINGS = {
    "title": {"pl": "Kalendarz zdarzeń GPW", "en": "GPW event calendar"},
    "stale": {"pl": "UWAGA: dane ESPI kończą się {as_of} ({n} sesji temu) - kalendarz może być niepełny.",
              "en": "WARNING: ESPI data ends {as_of} ({n} sessions ago) - the calendar may be incomplete."},
    "horizon": {"pl": "Horyzont: {start} - {end} ({n} sesji).", "en": "Horizon: {start} - {end} ({n} sessions)."},
    "answer_lead": {"pl": "W ciągu {n} sesji ({start} - {end})", "en": "Within {n} sessions ({start} - {end})"},
    "nothing": {"pl": "nie znaleziono zdarzeń o istotności >= {imp}.", "en": "no events of importance >= {imp} found."},
    "earn": {"pl": "raporty okresowe Twoich spółek - {n} ({detail})", "en": "periodic reports of your companies - {n} ({detail})"},
    "earn_m": {"pl": "raporty okresowe spółek - {n} ({detail})", "en": "periodic reports - {n} companies ({detail})"},
    "exdiv": {"pl": "{t} ma dzień odcięcia dywidendy {d}{k}", "en": "{t} goes ex-dividend on {d}{k}"},
    "agm": {"pl": "walne zgromadzenia: {n}", "en": "general meetings: {n}"},
    "mpc": {"pl": "posiedzenie RPP (decyzja {d})", "en": "MPC meeting (decision {d})"},
    "expiry": {"pl": "wygasanie FW20 {d}", "en": "FW20 expiry {d}"},
    "idx": {"pl": "zmiany indeksów: {txt}", "en": "index changes: {txt}"},
    "msci": {"pl": "MSCI: {txt}", "en": "MSCI: {txt}"},
    "confirmed": {"pl": "potwierdzone", "en": "confirmed"},
    "estimated": {"pl": "szacunek", "en": "estimate"},
    "statutory_deadline": {"pl": "termin ustawowy", "en": "statutory deadline"},
    "est_suffix": {"pl": " (szacunek)", "en": " (estimate)"},
    "tbl_head": {"pl": "| Data | Godz. | Spółka | Zdarzenie | Rodzaj daty | Istotność | Źródło |",
                 "en": "| Date | Time | Company | Event | Date kind | Importance | Source |"},
    "events_h": {"pl": "## Zdarzenia według dat", "en": "## Events by date"},
    "past_h": {"pl": "## Ostatnie zdarzenia (lookback)", "en": "## Recent events (lookback)"},
    "ticker_h": {"pl": "## Podsumowanie spółek", "en": "## Company summary"},
    "window_h": {"pl": "## Okno trzymania pozycji", "en": "## Holding window"},
    "window_line": {"pl": "{t}: {start} - {end} ({n} sesji) -> werdykt **{v}**.",
                    "en": "{t}: {start} - {end} ({n} sessions) -> verdict **{v}**."},
    "verdict_event_risk": {"pl": "event_risk - w oknie jest raport okresowy lub potwierdzony dzień odcięcia dywidendy",
                           "en": "event_risk - a periodic report or a confirmed ex-dividend date falls inside"},
    "verdict_caution": {"pl": "caution - tylko zdarzenia szacowane lub ważne zdarzenia rynkowe",
                        "en": "caution - only estimated company events or high-importance market events"},
    "verdict_clear": {"pl": "clear - brak istotnych zdarzeń w oknie", "en": "clear - no relevant events inside"},
    "warn_h": {"pl": "## Ostrzeżenia i ograniczenia", "en": "## Warnings and limitations"},
    "sources_h": {"pl": "## Źródła / Sources", "en": "## Źródła / Sources"},
    "legend": {"pl": "Rodzaj daty: **potwierdzone** = data podana przez spółkę/organizatora; **termin ustawowy** = "
                     "najpóźniejszy dzień wynikający z przepisów (spółka może opublikować wcześniej); **szacunek** = "
                     "reguła lub historia (np. dzień odcięcia ~rok po poprzednim).",
               "en": "Date kind: **confirmed** = date announced by the company/organiser; **statutory deadline** = "
                     "latest day allowed by law (the company may publish earlier); **estimate** = rule or history "
                     "(e.g. ex-date ~a year after the previous one)."},
    "no_tickers": {"pl": "Brak spółek: podaj --tickers, otwórz pozycje w gpw-trade-journal lub dodaj watchlistę.",
                   "en": "No tickers: pass --tickers, open positions in gpw-trade-journal or add a watchlist."},
    "next_earn": {"pl": "następne wyniki", "en": "next results"},
    "next_ex": {"pl": "następny dzień odcięcia", "en": "next ex-dividend"},
    "next_agm": {"pl": "następne WZ", "en": "next general meeting"},
    "hist_ex": {"pl": "historyczne dni odcięcia (archiwum GPW)", "en": "past ex-dates (GPW archive)"},
    "none": {"pl": "brak", "en": "none"},
    "window": {"pl": "okno", "en": "window"},
}

TYPE_LABEL = {
    "periodic_report": {"pl": "raport okresowy", "en": "periodic report"},
    "agm": {"pl": "walne zgromadzenie", "en": "general meeting"},
    "dividend_record": {"pl": "dzień dywidendy (ustalenia prawa)", "en": "dividend record date"},
    "dividend_payment": {"pl": "wypłata dywidendy", "en": "dividend payment"},
    "ex_dividend": {"pl": "dzień odcięcia dywidendy (ex)", "en": "ex-dividend date"},
    "index_revision": {"pl": "indeksy GPW Benchmark", "en": "GPW Benchmark indices"},
    "mpc_meeting": {"pl": "RPP", "en": "MPC (NBP)"},
    "macro_release": {"pl": "dane makro", "en": "macro release"},
    "msci_review": {"pl": "przegląd MSCI", "en": "MSCI review"},
    "other": {"pl": "inne", "en": "other"},
}
PERIOD_LABEL = {
    "Q1": {"pl": "I kw.", "en": "Q1"}, "Q2": {"pl": "II kw.", "en": "Q2"}, "Q3": {"pl": "III kw.", "en": "Q3"},
    "Q4": {"pl": "IV kw.", "en": "Q4"}, "H1": {"pl": "I półrocze", "en": "H1"}, "FY": {"pl": "rok", "en": "FY"},
    "Q": {"pl": "kwartał", "en": "quarter"},
}


class CalendarError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Arguments
# --------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(description="GPW event calendar (PL/EN): earnings, dividends, AGMs, index "
                                            "revisions, MPC, MSCI, expiries")
    p.add_argument("mode", choices=["upcoming", "ticker", "portfolio", "window"])
    p.add_argument("symbol", nargs="?", default=None, help="ticker for 'ticker' mode (or use --ticker)")
    p.add_argument("--ticker", default=None)
    p.add_argument("--tickers", default=None, help="comma-separated GPW short names, e.g. PKOBP,KETY")
    p.add_argument("--isin", action="append", default=[], metavar="TICKER=ISIN",
                   help="ISIN for a ticker outside the GPW Benchmark portfolios (repeatable)")
    p.add_argument("--sessions", type=int, default=None,
                   help="horizon in sessions (upcoming 15, portfolio 20, ticker 20 for market events)")
    p.add_argument("--days", type=int, default=20, help="window mode: holding window in sessions")
    p.add_argument("--start", default=None, help="window mode: first day of the holding window (YYYY-MM-DD)")
    p.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    p.add_argument("--min-importance", choices=["low", "medium", "high"], default=None)
    p.add_argument("--deep", action="store_true", help="read schedule/dividend/AGM report bodies for all companies")
    p.add_argument("--max-reports", type=int, default=DEFAULT_MAX_REPORTS)
    p.add_argument("--source", action="append", default=[], choices=["biznesradar"],
                   help="optional extra source (biznesradar: personal local use only)")
    p.add_argument("--journal-dir", default=None)
    p.add_argument("--no-journal", action="store_true")
    p.add_argument("--watchlist", default=None, help="gpw-watchlist state file (default ~/.gpw-trading-skills/watchlist.json)")
    p.add_argument("--no-archive", action="store_true", help="skip the GPW price archive (ex-date history)")
    p.add_argument("--ics", action="store_true", help="also write an iCalendar .ics file")
    p.add_argument("--as-of", default=None)
    p.add_argument("--offline", action="store_true")
    p.add_argument("--cache-dir", default=None)
    p.add_argument("--output-dir", default=".")
    p.add_argument("--no-files", action="store_true")
    p.add_argument("--lang", choices=["pl", "en"], default="pl")
    a = p.parse_args(argv)
    if a.mode in ("ticker", "window"):
        a.ticker = (a.ticker or a.symbol)
        if not a.ticker:
            p.error("mode '{}' needs a ticker (positional or --ticker)".format(a.mode))
    elif a.symbol:
        p.error("unexpected positional argument {!r} for mode '{}'".format(a.symbol, a.mode))
    if a.sessions is not None and not (1 <= a.sessions <= 260):
        p.error("--sessions must be 1..260")
    if not (1 <= a.days <= 260):
        p.error("--days must be 1..260")
    if not (0 <= a.lookback_days <= 730):
        p.error("--lookback-days must be 0..730")
    if a.max_reports < 0:
        p.error("--max-reports must be >= 0")
    for opt in ("as_of", "start"):
        v = getattr(a, opt)
        if v:
            try:
                setattr(a, opt, D.fromisoformat(v))
            except ValueError:
                p.error("--{} must be YYYY-MM-DD".format(opt.replace("_", "-")))
    isins = {}
    for item in a.isin:
        if "=" not in item:
            p.error("--isin expects TICKER=ISIN")
        t, i = item.split("=", 1)
        if not fundamentals.isin_is_valid(i):
            p.error("invalid ISIN {!r}".format(i))
        isins[t.strip().upper()] = i.strip().upper()
    a.isin_map = isins
    return a


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

def read_watchlist(path: Optional[str]) -> List[str]:
    """Tickers from the gpw-watchlist state (read-only; missing file -> [])."""
    p = Path(path).expanduser() if path else WATCHLIST_PATH
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for it in (data.get("items") or []) if isinstance(data, dict) else []:
        if isinstance(it, dict) and it.get("ticker") and it.get("stage") != "dropped":
            out.append(str(it["ticker"]).upper())
    return out


def portfolio_tickers(args) -> Dict[str, List[str]]:
    src: Dict[str, List[str]] = {}
    if args.tickers:
        for t in args.tickers.split(","):
            if t.strip():
                src.setdefault(t.strip().upper(), []).append("--tickers")
    if not args.no_journal:
        state = Path(args.journal_dir).expanduser() if args.journal_dir else None
        for pos in journal_io.open_positions(journal_io.load_theses(state)):
            src.setdefault(pos["ticker"].upper(), []).append("journal")
    for t in read_watchlist(args.watchlist):
        src.setdefault(t, []).append("watchlist")
    return src


# --------------------------------------------------------------------------
# Event builders
# --------------------------------------------------------------------------

def _imp_for_company(base: str, ticker: Optional[str], personal: set, size: Dict[str, str]) -> str:
    """Personal tickers keep the base importance; in the market view WIG20
    keeps it, mWIG40 drops one level, the rest is low (liquidity and index
    weight decide market impact)."""
    if ticker is None or ticker in personal:
        return base
    idx = size.get(ticker)
    if idx == "WIG20":
        return base
    if idx == "MWIG40":
        return {"high": "medium", "medium": "low", "low": "low"}[base]
    return "low"


def _period_title(period: str, fy: int, lang: str) -> str:
    lab = PERIOD_LABEL.get(period, {}).get(lang, period)
    if lang == "pl":
        return "Raport za {} {}".format(lab, fy) if period != "FY" else "Raport roczny za {}".format(fy)
    return "{} {} report".format(lab, fy) if period != "FY" else "Annual report {}".format(fy)


def espi_events(rows: List[Dict], bodies: Dict[int, Dict], today: D, lang: str,
                imp, history: Dict[str, List[Dict]]) -> Dict:
    """Events from ESPI rows (+ parsed bodies). Returns events and per-ticker report state."""
    events: List[Dict] = []
    published: Dict[tuple, Dict] = {}      # (ticker, period, fy) -> row
    scheduled: Dict[tuple, Dict] = {}      # (ticker, period, fy) -> {date, row}
    dividend_notes: List[Dict] = []
    for r in rows:
        t = r.get("ticker")
        if not t:
            continue
        cls = r["class"]
        pub = D.fromisoformat(r["date"])
        url = r["url"]
        src = "ESPI {} {}".format(r.get("number") or r["type"] or "", r["date"]).strip()
        if cls in ("periodic_q", "periodic_h", "periodic_y") and not r.get("correction"):
            per, fy = E.period_of_report(pub, {"periodic_q": "Q", "periodic_h": "P", "periodic_y": "R"}[cls])
            published.setdefault((t, per, fy), r)
            events.append(E.make_event(type="periodic_report", subtype="published", date=pub, time=r["time"][:5],
                                       ticker=t, isin=r["isin"], company=r["company"], date_kind="confirmed",
                                       importance=imp("medium", t), source=src, source_url=url,
                                       title=_period_title(per, fy, lang) + (" - opublikowany" if lang == "pl" else " - published"),
                                       details={"period": per, "fiscal_year": fy, "published": True,
                                                "report_type": r["type"]}))
        elif cls == "preliminary_results":
            events.append(E.make_event(type="periodic_report", subtype="preliminary", date=pub, time=r["time"][:5],
                                       ticker=t, isin=r["isin"], company=r["company"], date_kind="confirmed",
                                       importance=imp("medium", t), source=src, source_url=url,
                                       title=r["title"][:140] or "Wstępne wyniki", details={"published": True}))
        elif cls == "periodic_schedule":
            body = bodies.get(r["geru_id"])
            text = (r["title"] or "") + ". " + ((body or {}).get("body_text") or "")
            for it in E.parse_schedule_text(text, pub):
                key = (t, it["period"], it["fiscal_year"])
                prev = scheduled.get(key)
                if prev is None or prev["row"]["datetime"] <= r["datetime"]:
                    scheduled[key] = {"date": it["date"], "row": r, "from_body": body is not None}
        elif cls == "agm_call" and not r.get("correction"):
            body = bodies.get(r["geru_id"])
            info = E.parse_agm_text(r["title"], (body or {}).get("body_text") or "", pub)
            if info["date"]:
                ttl = r["title"][:140]
                events.append(E.make_event(type="agm", subtype=info["kind"], date=info["date"], time=info["time"],
                                           ticker=t, isin=r["isin"], company=r["company"], date_kind="confirmed",
                                           importance=imp("medium", t), source=src, source_url=url, title=ttl,
                                           details={"announced": r["date"], "kind": info["kind"],
                                                    "from_body": body is not None}))
        elif cls == "dividend" and not r.get("correction"):
            body = bodies.get(r["geru_id"])
            info = E.parse_dividend_text(r["title"], (body or {}).get("body_text") or "", pub)
            det = {"stage": info["stage"], "dps": info["dps"], "currency": info["currency"],
                   "record_date": info["record_date"], "ex_date": info["ex_date"],
                   "last_cum_date": info["last_cum_date"], "payment_dates": info["payment_dates"],
                   "fiscal_year": info["fiscal_year"], "announced": r["date"], "from_body": body is not None,
                   "rule": "ex-date = record date - 1 session, last day with right = record date - 2 sessions (T+2)"}
            # proposals can still change at the AGM -> estimated
            kind = "confirmed" if info["stage"] in ("resolution", "interim") else "estimated"
            dps_txt = " {} {}".format(fmt.num(info["dps"], 2, lang), info["currency"]) if info["dps"] else ""
            if info["ex_date"]:
                det["archive_check"] = E.validate_exdate(info["ex_date"], history.get(t, [])) \
                    if D.fromisoformat(info["ex_date"]) <= today else None
                events.append(E.make_event(type="ex_dividend", date=info["ex_date"], ticker=t, isin=r["isin"],
                                           company=r["company"], date_kind=kind, importance=imp("high", t),
                                           source=src, source_url=url, details=det,
                                           title=("Dzień odcięcia dywidendy" if lang == "pl" else "Ex-dividend") + dps_txt))
                events.append(E.make_event(type="dividend_record", date=info["record_date"], ticker=t, isin=r["isin"],
                                           company=r["company"], date_kind=kind, importance=imp("medium", t),
                                           source=src, source_url=url, details=det,
                                           title=("Dzień dywidendy (ustalenia prawa)" if lang == "pl" else "Dividend record date") + dps_txt))
            for pd in info["payment_dates"]:
                events.append(E.make_event(type="dividend_payment", date=pd, ticker=t, isin=r["isin"],
                                           company=r["company"], date_kind=kind, importance=imp("low", t),
                                           source=src, source_url=url, details=det,
                                           title=("Wypłata dywidendy" if lang == "pl" else "Dividend payment") + dps_txt))
            if not info["ex_date"] and not info["payment_dates"]:
                dividend_notes.append({"ticker": t, "date": r["date"], "title": r["title"], "url": url,
                                       "stage": info["stage"], "dps": info["dps"], "from_body": body is not None})
    for (t, per, fy), s in scheduled.items():
        if (t, per, fy) in published:
            continue
        r = s["row"]
        events.append(E.make_event(type="periodic_report", subtype="scheduled", date=s["date"], ticker=t,
                                   isin=r["isin"], company=r["company"], date_kind="confirmed",
                                   importance=imp("high", t), title=_period_title(per, fy, lang),
                                   source="ESPI {} {}".format(r.get("number") or "", r["date"]).strip(),
                                   source_url=r["url"],
                                   details={"period": per, "fiscal_year": fy, "schedule_report": r["date"],
                                            "from_body": s["from_body"]}))
    return {"events": events, "published": published, "scheduled": scheduled, "dividend_notes": dividend_notes}


def deadline_events(tickers: List[str], isins: Dict[str, str], start: D, end: D, published: Dict, scheduled: Dict,
                    lang: str, imp, market: bool) -> List[Dict]:
    out = []
    dls = E.deadlines_between(start, end)
    for d in dls:
        per, fy = d["period"], d["fiscal_year"]
        for t in tickers:
            if (t, per, fy) in published or (t, per, fy) in scheduled:
                continue
            out.append(E.make_event(type="periodic_report", subtype="deadline", date=d["deadline"], ticker=t,
                                    isin=isins.get(t), date_kind="statutory_deadline", importance=imp("high", t),
                                    title=_period_title(per, fy, lang) + (" - termin ustawowy" if lang == "pl" else " - statutory deadline"),
                                    source=d["legal_basis"], details={"period": per, "fiscal_year": fy,
                                                                       "period_end": d["period_end"],
                                                                       "nominal_deadline": d["nominal_deadline"]}))
        if market:
            out.append(E.make_event(type="periodic_report", subtype="deadline_market", date=d["deadline"],
                                    date_kind="statutory_deadline", importance="medium", source=d["legal_basis"],
                                    title=("Ostatni dzień na raporty za {} {} (rynek główny)" if lang == "pl"
                                           else "Last day for {} {} reports (main market)").format(
                                        PERIOD_LABEL[per][lang], fy),
                                    details={"period": per, "fiscal_year": fy, "nominal_deadline": d["nominal_deadline"]}))
    return out


def market_events(start: D, end: D, lang: str, index_items: List[Dict]) -> Dict:
    ev, warnings = [], []
    for y in S.schedule_gaps(start, end):
        warnings.append({"pl": "Brak harmonogramu RPP na {} (NBP publikuje go zwykle w grudniu) - posiedzenia RPP "
                               "w tym roku nie są pokazane.".format(y),
                         "en": "No MPC schedule for {} embedded (NBP usually publishes it in December) - MPC "
                               "meetings that year are not shown.".format(y)})
    for y, meetings in S.MPC_SCHEDULE.items():
        for m in meetings:
            day = D.fromisoformat(m["days"][-1])
            if not (start <= day <= end):
                continue
            title = ("Posiedzenie RPP - decyzja o stopach" if lang == "pl" else "MPC meeting - rate decision") \
                if m["decision"] else ("Posiedzenie RPP (niedecyzyjne)" if lang == "pl" else "MPC meeting (non-decision)")
            ev.append(E.make_event(type="mpc_meeting", subtype="decision" if m["decision"] else "non_decision",
                                   date=day, date_kind="confirmed", importance="high" if m["decision"] else "low",
                                   title=title, source="NBP, harmonogram posiedzeń RPP 2026 (as of {})".format(S.MPC_AS_OF),
                                   source_url=S.MPC_RELAY_URL,
                                   details={"meeting_days": m["days"], "minutes_publication": m["minutes"]}))
    for r in S.msci_reviews(start, end):
        a, e_ = D.fromisoformat(r["announcement"]), D.fromisoformat(r["effective_close"])
        if start <= a <= end:
            ev.append(E.make_event(type="msci_review", subtype="announcement", date=a, date_kind=r["kind"],
                                   importance="medium", source=r["source"],
                                   title=("MSCI: ogłoszenie przeglądu {}" if lang == "pl" else "MSCI: {} review announcement").format(r["review"]),
                                   details={"review": r["review"], "effective_close": r["effective_close"]}))
        if start <= e_ <= end:
            ev.append(E.make_event(type="msci_review", subtype="effective", date=e_, date_kind=r["kind"],
                                   importance="high", source=r["source"],
                                   title=("MSCI: zmiany w indeksach na zamknięciu (przegląd {})" if lang == "pl"
                                          else "MSCI: index changes at the close ({} review)").format(r["review"]),
                                   details={"review": r["review"], "announcement": r["announcement"],
                                            "note": "rebalancing flows in the closing auction"}))
    for x in S.derivative_expiries(start, end):
        if x["quarterly"]:
            ev.append(E.make_event(type="other", subtype="futures_expiry", date=x["date"], date_kind="confirmed",
                                   importance="medium", source="GPW, standard kontraktów na WIG20 (WATS 06.2026)",
                                   source_url=S.FW20_STANDARD_URL,
                                   title=("Wygasanie kontraktów FW20/FW40 i opcji OW20 ({})" if lang == "pl"
                                          else "FW20/FW40 futures and OW20 options expiry ({})").format(x["month"]),
                                   details={"rule": "third Friday of Mar/Jun/Sep/Dec, previous session if no session"}))
        else:
            ev.append(E.make_event(type="other", subtype="options_expiry", date=x["date"], date_kind="estimated",
                                   importance="low", source="rule: third Friday of the month (WIG20 option series)",
                                   title=("Wygasanie miesięcznej serii opcji OW20 ({})" if lang == "pl"
                                          else "OW20 monthly option expiry ({})").format(x["month"])))
    for c in S.cpi_releases(start, end):
        f, fi = D.fromisoformat(c["flash"]), D.fromisoformat(c["final"])
        if start <= f <= end:
            ev.append(E.make_event(type="macro_release", subtype="cpi_flash", date=f, date_kind="estimated",
                                   importance="medium", source="GUS (rule: last working day of the month)",
                                   source_url=S.GUS_CAL_URL, time="10:00",
                                   title=("GUS: szybki szacunek CPI za {}" if lang == "pl" else "GUS: flash CPI for {}").format(c["ref_month"]),
                                   details={"ref_month": c["ref_month"]}))
        if start <= fi <= end:
            ev.append(E.make_event(type="macro_release", subtype="cpi_final", date=fi, date_kind="estimated",
                                   importance="low", source="GUS (rule: ~15th of the month, +-2 days)",
                                   source_url=S.GUS_CAL_URL, time="10:00",
                                   title=("GUS: CPI (dane finalne) za {}" if lang == "pl" else "GUS: final CPI for {}").format(c["ref_month"]),
                                   details={"ref_month": c["ref_month"]}))
    for it in index_items:
        d = D.fromisoformat(it["date"])
        if not (start <= d <= end) or it["category"] == "bonds":
            continue
        if it["category"] == "no_session":
            ev.append(E.make_event(type="other", subtype="no_session", date=d, date_kind="confirmed", importance="low",
                                   title="Dzień bez sesji" if lang == "pl" else "No session (exchange closed)",
                                   source="GPW Benchmark, kalendarium indeksów", source_url=S.INDEX_CAL_URL))
            continue
        det = {"category": it["category"], "family": it["family"]}
        if it["category"] in ("quarterly_revision", "annual_revision", "quarterly_adjustment"):
            det["note"] = "portfolio changes take effect after this session (index funds trade at the close)"
        ev.append(E.make_event(type="index_revision", subtype=it["category"], date=d, date_kind="confirmed",
                               importance=it["importance"], title=it["text"][:160],
                               source="GPW Benchmark, kalendarium indeksów",
                               source_url=it.get("href") or S.INDEX_CAL_URL, details=det))
    return {"events": ev, "warnings": warnings}


def archive_history(tickers: List[str], today: D, cache_dir: Optional[Path]) -> Dict:
    """Past ex-dates from the cached GPW archive (never downloads here)."""
    try:
        sessions = gpw_archive.load_sessions(ARCHIVE_SESSIONS, end=today, cache_dir=cache_dir, offline=True,
                                             progress=False)
    except Exception as exc:  # pragma: no cover - defensive
        return {"history": {}, "as_of": None, "sessions": 0, "error": str(exc)}
    if not sessions:
        return {"history": {}, "as_of": None, "sessions": 0, "error": "no cached sessions"}
    want = set(tickers)
    pnl = panel.build_panel(sessions)
    raw_prev: Dict[str, Dict[str, float]] = {t: {} for t in want}
    last_close: Dict[str, float] = {}
    for sess in sessions:
        for row in sess["rows"]:
            n = row.get("name")
            if n in want and row.get("close"):
                if n in last_close:
                    raw_prev[n][sess["date"]] = last_close[n]
                last_close[n] = row["close"]
    hist = {}
    for t in want:
        s = pnl.get(t)
        hist[t] = E.exdiv_history(s.adjustments if s else [], raw_prev.get(t))
    return {"history": hist, "as_of": sessions[-1]["date"], "sessions": len(sessions), "error": None,
            "known": [t for t in want if t in pnl]}


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def run(args) -> Dict:
    lang = args.lang
    today = args.as_of or plcal.today_warsaw()
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else None
    warnings: List[Dict] = []
    sources_used: List[str] = []

    # ---- who is of interest
    personal: List[str] = []
    ticker_sources: Dict[str, List[str]] = {}
    if args.mode in ("ticker", "window"):
        personal = [args.ticker.upper()]
    elif args.mode == "portfolio":
        ticker_sources = portfolio_tickers(args)
        personal = sorted(ticker_sources)
        if not personal:
            raise CalendarError(fmt.T(STRINGS, lang)("no_tickers"))
    try:
        size = constituents.size_membership(cache_dir, offline=True)
    except Exception:
        size = {}
    isin_of = dict(fundamentals.isin_map(cache_dir, offline=True))
    isin_of.update(args.isin_map)
    missing_isin = [t for t in personal if t not in isin_of]
    if missing_isin and not args.offline:
        for t in list(missing_isin):
            try:
                isin_of[t] = fundamentals.resolve_isin(t, cache_dir)["isin"]
                missing_isin.remove(t)
            except Exception:
                pass
    for t in missing_isin:
        warnings.append({"pl": "{}: brak ISIN (spoza portfeli GPW Benchmark) - raporty ESPI nie zostaną "
                               "dopasowane; podaj --isin {}=PL...".format(t, t),
                         "en": "{}: no ISIN (not in GPW Benchmark portfolios) - ESPI reports cannot be matched; "
                               "pass --isin {}=PL...".format(t, t)})
    ticker_of = {v: k for k, v in isin_of.items()}
    pset = set(personal)
    interest = set(personal)
    if args.mode == "upcoming":
        interest = {t for t, idx in size.items() if idx == "WIG20" or (args.deep and idx in ("MWIG40",))}

    def imp(base, t):
        return _imp_for_company(base, t, pset, size)

    # ---- horizon
    if args.mode == "window":
        w_start = args.start or today
        sess = E.next_sessions(w_start, args.days)
    else:
        n = args.sessions or (15 if args.mode == "upcoming" else 20)
        sess = E.next_sessions(today, n)
    h_start, h_end = sess[0], sess[-1]
    h_start_market = h_start
    company_end = max(h_end, today + dt.timedelta(days=TICKER_COMPANY_HORIZON_DAYS)) if args.mode == "ticker" else h_end

    # ---- ESPI scan: lookback + the schedule season. § 84 ust. 1: dates for
    # the year by the end of January; many issuers publish them in December.
    lb_start = today - dt.timedelta(days=args.lookback_days)
    scans = [(lb_start, today)]
    sch_start, sch_end = D(today.year - 1, 12, 1), min(D(today.year, 1, 31), today)
    if sch_start < lb_start:
        scans.insert(0, (sch_start, min(sch_end, lb_start - dt.timedelta(days=1))))
    rows, days_meta, missing_days = [], [], []
    espi_error = None
    for a, b in scans:
        try:
            res = S.scan_espi(a, b, cache_dir=cache_dir, offline=args.offline)
        except http.HTTPFetchError as exc:
            espi_error = str(exc)
            break
        rows.extend(res["rows"])
        days_meta.extend(res["days"])
        missing_days.extend(res["missing_days"])
    if espi_error:
        warnings.append({"pl": "ESPI niedostępne: {} - zdarzenia spółek niepełne.".format(espi_error),
                         "en": "ESPI unavailable: {} - company events incomplete.".format(espi_error)})
    if missing_days:
        warnings.append({"pl": "Brak w cache {} dni ESPI (tryb offline) - uruchom bez --offline.".format(len(missing_days)),
                         "en": "{} ESPI days missing from the cache (offline) - run without --offline.".format(len(missing_days))})
    seen = set()
    uniq = []
    for r in rows:
        if r["geru_id"] in seen:
            continue
        seen.add(r["geru_id"])
        r["ticker"] = ticker_of.get(r.get("isin") or "")
        uniq.append(r)
    rows = sorted(uniq, key=lambda r: r["datetime"])
    sources_used.append("https://www.gpw.pl/komunikaty (ESPI/EBI list, POST ajaxindex.php GPWEspiReportUnion), "
                        "{} days".format(len(days_meta)))

    # ---- report bodies for tickers of interest (or all with --deep)
    body_classes = ("periodic_schedule", "dividend", "agm_call")
    targets = [r for r in rows if r["class"] in body_classes and r.get("ticker")
               and (args.deep or r["ticker"] in interest)]
    # personal tickers first, then the newest reports (the cap keeps the run polite)
    targets.sort(key=lambda r: (r["ticker"] not in pset, -int(r["datetime"][:10].replace("-", ""))))
    targets = targets[: args.max_reports]
    bodies, body_missing = {}, 0
    for r in targets:
        b = S.fetch_report(r["geru_id"], cache_dir=cache_dir, offline=args.offline)
        if b is None:
            body_missing += 1
        else:
            bodies[r["geru_id"]] = b
    if body_missing:
        warnings.append({"pl": "{} treści raportów niedostępnych (offline/błąd) - daty z samych tytułów.".format(body_missing),
                         "en": "{} report bodies unavailable (offline/error) - dates from titles only.".format(body_missing)})
    if bodies:
        sources_used.append("https://www.gpw.pl/komunikat?geru_id=N (report pages), {} read".format(len(bodies)))

    # ---- archive ex-date history
    hist_tickers = sorted(pset | ({t for t in interest} if args.mode == "upcoming" else set()))
    arch = {"history": {}, "as_of": None, "sessions": 0, "error": "skipped"}
    if not args.no_archive and hist_tickers:
        arch = archive_history(hist_tickers, today, cache_dir)
        if arch["as_of"]:
            sources_used.append("https://www.gpw.pl/archiwum-notowan (cached shares archive, {} sessions to {}), "
                                "ex-dates from gpwlib.panel price adjustments".format(arch["sessions"], arch["as_of"]))
    history = arch["history"]

    # ---- events
    ex = espi_events(rows, bodies, today, lang, imp, history)
    events = list(ex["events"])
    dl_tickers = [t for t in personal if t in isin_of] + ([t for t in personal if t not in isin_of])
    events += deadline_events(dl_tickers, isin_of, today, company_end, ex["published"], ex["scheduled"], lang, imp,
                              market=args.mode == "upcoming")
    # AGM statutory deadline (ticker mode looks a year ahead)
    if args.mode in ("ticker", "portfolio", "window"):
        for t in personal:
            for fy in (today.year - 1, today.year):
                dd = E.agm_deadline(fy)
                if today <= dd <= company_end and not any(
                        e["ticker"] == t and e["type"] == "agm" and e.get("subtype") == "annual"
                        and e["date"][:4] == str(dd.year) for e in events):
                    events.append(E.make_event(type="agm", subtype="annual_deadline", date=dd, ticker=t,
                                               isin=isin_of.get(t), date_kind="statutory_deadline",
                                               importance="low", source=E.LEGAL_AGM,
                                               title=("Zwyczajne WZ za {} - najpóźniej" if lang == "pl"
                                                      else "Annual general meeting for {} - latest date").format(fy)))
    # estimated next ex-date from history
    for t, h in history.items():
        est = E.estimate_next_exdate(h, today)
        if est and D.fromisoformat(est["estimated_ex_date"]) <= company_end:
            events.append(E.make_event(type="ex_dividend", subtype="estimated_from_history", date=est["estimated_ex_date"],
                                       ticker=t, isin=isin_of.get(t), date_kind="estimated",
                                       importance=imp("medium", t), source="GPW archive (price adjustments)",
                                       title=("Szacowany dzień odcięcia dywidendy (~rok po {})" if lang == "pl"
                                              else "Estimated ex-dividend (~a year after {})").format(est["based_on"]),
                                       details=est))
        for hh in h:
            if hh["kind"] == "dividend_likely" and (args.mode == "ticker") and \
                    D.fromisoformat(hh["ex_date"]) >= today - dt.timedelta(days=args.lookback_days + 300):
                events.append(E.make_event(type="ex_dividend", subtype="observed", date=hh["ex_date"], ticker=t,
                                           isin=isin_of.get(t), date_kind="confirmed", importance="low",
                                           source="GPW archive (reference-price cut)",
                                           title=("Dzień odcięcia (archiwum): -{} %, ~{} zł/akcję" if lang == "pl"
                                                  else "Ex-date (archive): -{} %, ~PLN {}/share").format(
                                               fmt.num(hh["cut_pct"], 2, lang), fmt.num(hh["implied_dps"], 2, lang)),
                                           details=hh))
    # optional biznesradar
    if "biznesradar" in args.source:
        try:
            br = S.fetch_biznesradar(cache_dir, args.offline)
            sources_used.append(S.BIZNESRADAR_URL + " (optional, personal local use only)")
            br_want = pset or interest
            for r in br["rows"]:
                t = r["ticker"]
                if t not in br_want or not r["last_cum_date"]:
                    continue
                x = E.exdiv_from_last_cum(D.fromisoformat(r["last_cum_date"]))
                kind = "confirmed" if r["status"] in ("uchwalona", "wypłacona") else "estimated"
                det = dict(r, **x)
                dps_txt = " {} {}".format(fmt.num(r["dps"], 2, lang), r["currency"]) if r["dps"] else ""
                for typ, d, base, ttl in (("ex_dividend", x["ex_date"], "high", "Dzień odcięcia dywidendy"),
                                          ("dividend_record", x["record_date"], "medium", "Dzień dywidendy"),
                                          ("dividend_payment", r["payment_date"], "low", "Wypłata dywidendy")):
                    if d:
                        events.append(E.make_event(type=typ, date=d, ticker=t, isin=isin_of.get(t), date_kind=kind,
                                                   importance=imp(base, t), source="biznesradar.pl",
                                                   source_url=S.BIZNESRADAR_URL, details=det,
                                                   title=(ttl if lang == "pl" else typ.replace("_", " ")) + dps_txt))
        except http.HTTPFetchError as exc:
            warnings.append({"pl": "biznesradar.pl niedostępny: {}".format(exc), "en": "biznesradar.pl unavailable: {}".format(exc)})
    # market-level events
    index_items = []
    for y in sorted({h_start_market.year, h_end.year}):
        try:
            ic = S.fetch_index_calendar(y, cache_dir, args.offline)
            if not ic["items"]:
                warnings.append({"pl": "Kalendarium indeksów {} jest puste lub zmieniło format.".format(y),
                                 "en": "Index calendar {} is empty or changed format.".format(y)})
            index_items += ic["items"]
            sources_used.append("{} ({} items, retrieved {})".format(ic["url"], len(ic["items"]), ic["retrieved_at"]))
        except http.HTTPFetchError as exc:
            warnings.append({"pl": "Kalendarium indeksów {} niedostępne: {}".format(y, exc),
                             "en": "Index calendar {} unavailable: {}".format(y, exc)})
    mk = market_events(h_start_market, h_end, lang, index_items)
    events += mk["events"]
    warnings += mk["warnings"]
    sources_used += ["NBP MPC schedule 2026 (embedded, as of {}; relay {})".format(S.MPC_AS_OF, S.MPC_RELAY_URL),
                     "MSCI index review dates (embedded): " + S.MSCI_REVIEWS[0]["source"],
                     "GPW FW20 standard: " + S.FW20_STANDARD_URL,
                     "GUS calendar rule check: " + S.GUS_CAL_URL,
                     "Dz.U. 2025 poz. 755 (§ 83-84): https://api.sejm.gov.pl/eli/acts/DU/2025/755/text.pdf",
                     "KSH (Dz.U. 2024 poz. 18) art. 348, 395: https://api.sejm.gov.pl/eli/acts/DU/2024/18/text.pdf"]

    events = E.dedupe_events(events)

    # ---- select per mode
    if args.mode == "upcoming":
        min_imp = args.min_importance or "medium"
        shown = E.filter_events(events, h_start, h_end, min_imp)
        past = []
    elif args.mode == "ticker":
        min_imp = args.min_importance or "low"
        t = personal[0]
        comp = [e for e in events if e["ticker"] == t]
        shown = [e for e in comp if h_start <= D.fromisoformat(e["date"]) <= company_end]
        shown += E.filter_events([e for e in events if e["ticker"] is None], h_start, h_end, "medium")
        shown = E.sort_events([e for e in shown if E.IMP_RANK[e["importance"]] >= E.IMP_RANK[min_imp]
                               or e["ticker"] == t])
        past = E.sort_events([e for e in comp if D.fromisoformat(e["date"]) < h_start])
    else:
        min_imp = args.min_importance or "low"
        shown = E.filter_events([e for e in events if e["ticker"] in pset], h_start, h_end, min_imp)
        shown += E.filter_events([e for e in events if e["ticker"] is None], h_start, h_end,
                                 args.min_importance or "medium")
        shown = E.sort_events(shown)
        past = E.sort_events([e for e in events if e["ticker"] in pset and
                              today - dt.timedelta(days=30) <= D.fromisoformat(e["date"]) < h_start])

    # ---- per-ticker summary
    by_ticker = {}
    for t in personal:
        fut = [e for e in events if e["ticker"] == t and D.fromisoformat(e["date"]) >= today]

        def first(pred):
            for e in fut:
                if pred(e):
                    return {"date": e["date"], "date_kind": e["date_kind"], "title": e["title"]}
            return None
        h = history.get(t, [])
        by_ticker[t] = {
            "isin": isin_of.get(t), "index": size.get(t), "sources": ticker_sources.get(t, ["cli"]),
            "next_earnings": first(lambda e: e["type"] == "periodic_report" and e.get("subtype") != "published"),
            "next_ex_dividend": first(lambda e: e["type"] == "ex_dividend"),
            "next_agm": first(lambda e: e["type"] == "agm"),
            "exdiv_history": h[:4],
            "next_exdate_estimate": E.estimate_next_exdate(h, today),
            "espi_reports_in_lookback": sum(1 for r in rows if r.get("ticker") == t),
            "dividend_reports_without_dates": [n for n in ex["dividend_notes"] if n["ticker"] == t],
        }

    hwc = None
    if args.mode == "window":
        hwc = E.holding_window_check(events, personal[0], h_start, args.days)
        hwc["market_min_importance"] = "medium"

    espi_as_of = max((d["date"] for d in days_meta), default=None)
    as_of = D.fromisoformat(espi_as_of) if espi_as_of else today
    prov = provenance.build(as_of=as_of, sources=sources_used, sessions_used=len(days_meta),
                            universe_size=len({r.get("isin") for r in rows}),
                            notes=["Company events come from ESPI titles and (for tickers of interest) report "
                                   "bodies; PDF attachments are never downloaded.",
                                   "Statutory deadlines assume a calendar financial year and the main market "
                                   "(NewConnect has its own deadlines).",
                                   "Archive ex-date history as of {}.".format(arch.get("as_of"))],
                            corporate_actions_adjusted=True, survivorship_free=True,
                            reference_date=args.as_of, max_sessions_behind=1)
    counts: Dict[str, int] = {}
    for e in shown:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    out = {
        "skill": SKILL, "version": VERSION, "lang": lang, "mode": args.mode, "as_of": as_of.isoformat(),
        "reference_date": today.isoformat(),
        "horizon": {"start": h_start.isoformat(), "end": h_end.isoformat(), "sessions": len(sess),
                    "company_end": company_end.isoformat()},
        "tickers": personal,
        "events": shown,
        "past_events": past,
        "summary": {"n_events": len(shown), "by_type": counts,
                    "by_date_kind": {k: sum(1 for e in shown if e["date_kind"] == k) for k in E.DATE_KINDS},
                    "n_company": sum(1 for e in shown if e["ticker"]), "n_market": sum(1 for e in shown if not e["ticker"]),
                    "min_importance": min_imp},
        "by_ticker": by_ticker,
        "holding_window_check": hwc,
        "espi_scan": {"ranges": [[a.isoformat(), b.isoformat()] for a, b in scans], "days": len(days_meta),
                      "reports": len(rows), "missing_days": missing_days, "bodies_read": len(bodies),
                      "bodies_missing": body_missing, "body_targets": len(targets),
                      "tickers_for_bodies": "all (--deep)" if args.deep else sorted(interest)},
        "archive": {"as_of": arch.get("as_of"), "sessions": arch.get("sessions"), "error": arch.get("error")},
        "warnings": warnings,
        "data_provenance": prov,
    }
    return out


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

def _d(s: str, lang: str) -> str:
    d = D.fromisoformat(s)
    return d.strftime("%d.%m.%Y") if lang == "pl" else s


def answer_line(r: Dict, lang: str) -> str:
    T = fmt.T(STRINGS, lang)
    h = r["horizon"]
    ev = [e for e in r["events"] if h["start"] <= e["date"] <= h["end"]]
    parts = []
    if r["mode"] == "ticker":
        for t, b in r["by_ticker"].items():
            def f(x):
                return "{} ({})".format(_d(x["date"], lang), T(x["date_kind"])) if x else T("none")
            parts.append("{}: {} {}, {} {}, {} {}".format(t, T("next_earn"), f(b["next_earnings"]), T("next_ex"),
                                                          f(b["next_ex_dividend"]), T("next_agm"), f(b["next_agm"])))
        ev = [e for e in ev if e["ticker"] is None]
    earn = [e for e in ev if e["type"] == "periodic_report" and e["ticker"] and e.get("subtype") in ("scheduled", "deadline")]
    if earn:
        tick = sorted({e["ticker"] for e in earn})
        kinds = {}
        for e in earn:
            kinds[e["date_kind"]] = kinds.get(e["date_kind"], 0) + 1
        detail = ", ".join("{} {}".format(v, T(k)) for k, v in sorted(kinds.items()))
        key = "earn" if r["mode"] != "upcoming" else "earn_m"
        parts.append(T(key, n=len(tick), detail=detail) + ": " + ", ".join(tick[:8]) + ("..." if len(tick) > 8 else ""))
    for e in [e for e in ev if e["type"] == "ex_dividend" and e["ticker"]][:4]:
        parts.append(T("exdiv", t=e["ticker"], d=_d(e["date"], lang),
                       k=T("est_suffix") if e["date_kind"] != "confirmed" else ""))
    agms = [e for e in ev if e["type"] == "agm" and e["ticker"] and e["date_kind"] == "confirmed"]
    if agms:
        parts.append(T("agm", n=len(agms)) + " (" + ", ".join(sorted({e["ticker"] for e in agms})[:6]) + ")")
    for e in ev:
        if e["type"] == "mpc_meeting" and e.get("subtype") == "decision":
            parts.append(T("mpc", d=_d(e["date"], lang)))
    for e in ev:
        if e.get("subtype") == "futures_expiry":
            parts.append(T("expiry", d=_d(e["date"], lang)))
    idx = [e for e in ev if e["type"] == "index_revision" and e["importance"] == "high"]
    if idx:
        lab = {"announcement": {"pl": "ogłoszenie zmian", "en": "announcement"},
               "quarterly_adjustment": {"pl": "korekta kwartalna", "en": "quarterly adjustment"},
               "quarterly_revision": {"pl": "rewizja kwartalna", "en": "quarterly revision"},
               "annual_revision": {"pl": "rewizja roczna", "en": "annual revision"}}
        parts.append(T("idx", txt="; ".join("{} {}".format(_d(e["date"], lang), lab.get(e["subtype"], {}).get(lang, e["subtype"]))
                                            for e in idx[:3])))
    ms = [e for e in ev if e["type"] == "msci_review" and e.get("subtype") == "effective"]
    if ms:
        parts.append(T("msci", txt=", ".join(_d(e["date"], lang) for e in ms)))
    lead = T("answer_lead", n=h["sessions"], start=_d(h["start"], lang), end=_d(h["end"], lang))
    if not parts:
        return lead + ": " + T("nothing", imp=r["summary"]["min_importance"])
    return lead + ": " + "; ".join(parts) + "."


def _row(e: Dict, lang: str, T) -> str:
    label = TYPE_LABEL[e["type"]][lang]
    title = e["title"].replace("|", "/")
    src = e["source"].replace("|", "/")
    if e.get("source_url") and e["source_url"].startswith("http"):
        src = "[{}]({})".format(src if len(src) <= 40 else src[:38].rstrip() + "…", e["source_url"])
    return "| {} | {} | {} | {}: {} | {} | {} | {} |".format(
        _d(e["date"], lang), e.get("time") or "", e.get("ticker") or "—", label, title[:110],
        T(e["date_kind"]), e["importance"], src)


def render_markdown(r: Dict, lang: str) -> str:
    T = fmt.T(STRINGS, lang)
    L = []
    fr = r["data_provenance"]["freshness"]
    if fr["is_stale"]:
        L.append("> " + T("stale", as_of=fr["as_of"], n=fr["sessions_behind"]))
        L.append("")
    L.append("# {} ({})".format(T("title"), r["mode"]))
    L.append("")
    L.append("**" + answer_line(r, lang) + "**")
    L.append("")
    if r.get("holding_window_check"):
        w = r["holding_window_check"]
        L.append(T("window_h"))
        L.append("")
        L.append(T("window_line", t=w["ticker"], start=_d(w["start"], lang), end=_d(w["end"], lang), n=w["sessions"],
                   v=T("verdict_" + w["verdict"])))
        L.append("")
        for k, v in w["contains"].items():
            f = w["first"].get(k[4:])
            L.append("- {}: {}{}".format(k, "TAK" if (v and lang == "pl") else ("YES" if v else ("nie" if lang == "pl" else "no")),
                                         " - {} {} ({})".format(_d(f["date"], lang), f["title"], T(f["date_kind"])) if f else ""))
        L.append("")
    L.append(T("horizon", start=_d(r["horizon"]["start"], lang), end=_d(r["horizon"]["end"], lang),
               n=r["horizon"]["sessions"]))
    L.append("")
    L.append(T("events_h"))
    L.append("")
    if r["events"]:
        L.append(T("tbl_head"))
        L.append("|---|---|---|---|---|---|---|")
        for e in r["events"]:
            L.append(_row(e, lang, T))
    else:
        L.append(T("nothing", imp=r["summary"]["min_importance"]))
    L.append("")
    L.append(T("legend"))
    L.append("")
    if r["by_ticker"]:
        L.append(T("ticker_h"))
        L.append("")
        for t, b in r["by_ticker"].items():
            def f(x):
                return "{} ({})".format(_d(x["date"], lang), T(x["date_kind"])) if x else T("none")
            hist = ", ".join("{} (-{}%)".format(_d(h["ex_date"], lang), fmt.num(h["cut_pct"], 1, lang))
                             for h in b["exdiv_history"] if h["kind"] == "dividend_likely") or T("none")
            nx = f(b["next_ex_dividend"])
            est = b.get("next_exdate_estimate")
            if not b["next_ex_dividend"] and est:
                nx += " ({} ~{}, {} {}-{})".format(T("estimated"), _d(est["estimated_ex_date"], lang), T("window"),
                                                   _d(est["window_start"], lang), _d(est["window_end"], lang))
            L.append("- **{}** ({}): {} {}; {} {}; {} {}; {}: {}".format(
                t, b.get("index") or "—", T("next_earn"), f(b["next_earnings"]), T("next_ex"),
                nx, T("next_agm"), f(b["next_agm"]), T("hist_ex"), hist))
        L.append("")
    if r["past_events"]:
        L.append(T("past_h"))
        L.append("")
        L.append(T("tbl_head"))
        L.append("|---|---|---|---|---|---|---|")
        for e in r["past_events"][-25:]:
            L.append(_row(e, lang, T))
        L.append("")
    if r["warnings"]:
        L.append(T("warn_h"))
        L.append("")
        for w in r["warnings"]:
            L.append("- " + w.get(lang, w.get("en")))
        L.append("")
    L.append(T("sources_h"))
    L.append("")
    for s in r["data_provenance"]["sources"]:
        L.append("- " + s)
    L.append("- ESPI as of {}; generated {}".format(r["as_of"], r["data_provenance"]["retrieved_at_utc"]))
    L.append("")
    L.append(fmt.DISCLAIMER[lang])
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        r = run(args)
    except (CalendarError, fundamentals.FundamentalsError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    except http.HTTPFetchError as exc:
        print("error: data source unavailable: {}".format(exc), file=sys.stderr)
        return 1
    md = render_markdown(r, args.lang)
    if not args.no_files:
        out_dir = Path(args.output_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        base = out_dir / "{}_{}".format(PREFIX, stamp)
        base.with_suffix(".json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        base.with_suffix(".md").write_text(md, encoding="utf-8")
        if args.ics:
            ics_events = r["events"] + (r["past_events"] if args.mode == "ticker" else [])
            with open(base.with_suffix(".ics"), "w", encoding="utf-8", newline="") as fh:
                fh.write(E.to_ics(ics_events, dt.datetime.now(dt.timezone.utc), "GPW " + r["mode"]))
        print(md)
        print("Files: {}.json, {}.md{}".format(base, base, ", {}.ics".format(base) if args.ics else ""), file=sys.stderr)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
