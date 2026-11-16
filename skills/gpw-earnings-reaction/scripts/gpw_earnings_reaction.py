#!/usr/bin/env python3
"""GPW Earnings Reaction - CLI.

How did the market react to each company's periodic report on GPW (gap,
3-day abnormal return vs WIG, abnormal turnover), is there post-earnings
drift, and what does it mean for a held or watched stock.

    python3 scripts/gpw_earnings_reaction.py scan --lang pl
    python3 scripts/gpw_earnings_reaction.py ticker PKOBP --history --lang en
    python3 scripts/gpw_earnings_reaction.py portfolio --tickers PKOBP,KGHM

Report dates come from the ESPI list on gpw.pl (one cached request per day),
prices from the GPW archive (dividend/split adjusted by gpwlib.panel).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import constituents, espi, fundamentals, gpw_archive, http, journal_io, panel, plcal, provenance  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct  # noqa: E402

import earnings_reaction_engine as ere  # noqa: E402

VERSION = "1.0.0"
SKILL = "gpw-earnings-reaction"
PREFIX = "gpw_earnings_reaction"
# Without --history the ESPI lookback is capped at 150 calendar days
# (~105 sessions, ~150 requests on a cold cache, ~3 min at 1 request/s).
MAX_LOOKBACK_NO_HISTORY = 150
# Liquidity filter for the scan's top lists: median turnover >= 100k zł/day.
# At 50k zł/day (the universe floor in gpwlib.universe) a 10k zł order is
# 20% of a normal day; at 100k zł it is <= 10%, and Chordia et al. (2009)
# show that the drift in illiquid names is mostly eaten by trading costs.
DEFAULT_MIN_TURNOVER_K = 100.0
MIN_PRICE = 1.0  # same as gpwlib.universe.DEFAULTS["min_price"] (sub-1 zł tick distortions)

STRINGS = {
    "title_scan": {"pl": "Reakcje na raporty okresowe GPW — sezon {s}", "en": "GPW earnings reactions — {s} season"},
    "title_ticker": {"pl": "Reakcje na raporty — {t}", "en": "Earnings reactions — {t}"},
    "title_portfolio": {"pl": "Reakcje na raporty — moje spółki", "en": "Earnings reactions — my stocks"},
    "as_of": {"pl": "Dane na sesję", "en": "Data as of session"},
    "generated": {"pl": "Wygenerowano", "en": "Generated"},
    "stale": {"pl": "⚠️ UWAGA: dane cenowe są nieaktualne o {n} sesji.", "en": "⚠️ WARNING: price data is {n} session(s) old."},
    "espi_missing": {"pl": "⚠️ Brak w pamięci podręcznej listy ESPI dla {n} dni (tryb offline) — część raportów może być pominięta.",
                     "en": "⚠️ ESPI list missing from the cache for {n} day(s) (offline mode) — some reports may be missing."},
    "answer": {"pl": "Odpowiedź", "en": "Answer"},
    "season_line": {"pl": "Sezon **{s}**: {n} reakcji na raporty (od {d0} do {d1}); **{p}** z dodatnią 3-dniową nadwyżkową stopą zwrotu (CAR) vs WIG, średnia CAR {m} (mediana {med}). Silnie negatywnych: {sn}, silnie pozytywnych: {sp}.",
                    "en": "**{s}** season: {n} report reactions ({d0} to {d1}); **{p}** with a positive 3-day abnormal return (CAR) vs WIG, mean CAR {m} (median {med}). Strong negative: {sn}, strong positive: {sp}."},
    "baseline_line": {"pl": "Punkt odniesienia: przeciętna płynna spółka miała w tym czasie 3-dniową nadwyżkę vs WIG {b} (WIG jest ważony kapitalizacją; gdy prowadzą duże spółki, przeciętna spółka „traci” do WIG także bez raportu). Średnia reakcja względem tej bazy: {d} pkt proc.",
                      "en": "Baseline: the average liquid stock's 3-day excess return vs WIG over this period was {b} (WIG is cap-weighted; when large caps lead, the average stock lags WIG even without a report). Mean reaction vs this baseline: {d} pp."},
    "col_vsn": {"pl": "vs neutralne (t)", "en": "vs neutral (t)"},
    "no_season": {"pl": "Brak reakcji z bieżącego sezonu {s} w danych.", "en": "No reactions from the current {s} season in the data."},
    "pending_line": {"pl": "Raporty bez reakcji jeszcze w danych (opublikowane po ostatniej sesji): {n}.",
                     "en": "Reports whose reaction is not in the data yet (published after the last session): {n}."},
    "top_neg": {"pl": "Najsłabsze reakcje (płynne spółki, obrót ≥ {k} tys. zł/dzień)", "en": "Weakest reactions (liquid names, turnover ≥ {k}k PLN/day)"},
    "top_pos": {"pl": "Najmocniejsze reakcje (płynne spółki, obrót ≥ {k} tys. zł/dzień)", "en": "Strongest reactions (liquid names, turnover ≥ {k}k PLN/day)"},
    "col_ticker": {"pl": "Spółka", "en": "Ticker"},
    "col_period": {"pl": "Okres", "en": "Period"},
    "col_rday": {"pl": "Dzień reakcji", "en": "Reaction day"},
    "col_align": {"pl": "Publikacja", "en": "Published"},
    "col_gap": {"pl": "Luka otwarcia", "en": "Open gap"},
    "col_car": {"pl": "CAR [0,+2]", "en": "CAR [0,+2]"},
    "col_z": {"pl": "z", "en": "z"},
    "col_at": {"pl": "Obrót ×", "en": "Turnover ×"},
    "col_drift": {"pl": "Dryf [+3,+20]", "en": "Drift [+3,+20]"},
    "col_label": {"pl": "Ocena", "en": "Label"},
    "col_ctx": {"pl": "SMA50/200", "en": "SMA50/200"},
    "pending": {"pl": "w toku ({n} ses.)", "en": "pending ({n} s.)"},
    "pre_open": {"pl": "przed sesją", "en": "pre-open"},
    "intraday": {"pl": "w trakcie sesji ⚑", "en": "intraday ⚑"},
    "after_close": {"pl": "po sesji", "en": "after close"},
    "non_session": {"pl": "dzień wolny", "en": "non-session day"},
    "strong_positive": {"pl": "silnie pozytywna", "en": "strong positive"},
    "positive": {"pl": "pozytywna", "en": "positive"},
    "neutral": {"pl": "neutralna", "en": "neutral"},
    "negative": {"pl": "negatywna", "en": "negative"},
    "strong_negative": {"pl": "silnie negatywna", "en": "strong negative"},
    "prov": {"pl": " (wstępna)", "en": " (provisional)"},
    "segments": {"pl": "Reakcje według wielkości spółek", "en": "Reactions by size segment"},
    "col_seg": {"pl": "Segment", "en": "Segment"},
    "col_n": {"pl": "Liczba", "en": "Count"},
    "col_mean": {"pl": "Średnia CAR", "en": "Mean CAR"},
    "col_median": {"pl": "Mediana CAR", "en": "Median CAR"},
    "col_share": {"pl": "Dodatnie", "en": "Positive"},
    "other": {"pl": "pozostałe", "en": "other"},
    "drift": {"pl": "Czy po reakcji jest dryf? (CAR [+3,+20] według oceny reakcji)", "en": "Is there drift after the reaction? (CAR [+3,+20] by reaction label)"},
    "col_t": {"pl": "t-stat", "en": "t-stat"},
    "col_d60": {"pl": "Dryf [+3,+60]", "en": "Drift [+3,+60]"},
    "drift_note": {"pl": "Zdarzenia z pełnym oknem: {n}, w toku: {p}. Porównuj każdą ocenę z grupą neutralną („vs neutralne”) — ona zbiera dryf przeciętnej spółki względem ważonego kapitalizacją WIG. Próby poniżej 30 zdarzeń są zbyt małe na wnioski. W badaniach z rynku amerykańskiego dryf po wynikach (PEAD, Bernard i Thomas 1989) skupia się w mało płynnych spółkach, gdzie koszty transakcyjne zjadają 70–100% zysku (Chordia i in. 2009); na GPW dryf obserwowano głównie po negatywnych niespodziankach (Jagliński 2020). Dlatego silnie negatywna reakcja to przede wszystkim ostrzeżenie (wyjście / unikanie), a nie sygnał do gry na spadki.",
                   "en": "Events with a complete window: {n}, pending: {p}. Compare each label with the neutral group (\"vs neutral\"): it absorbs the drift of the average stock vs the cap-weighted WIG. Samples below 30 events are too small to conclude anything. US evidence puts post-earnings drift (PEAD, Bernard & Thomas 1989) mainly in illiquid stocks where trading costs eat 70–100% of it (Chordia et al. 2009); on the WSE drift was found mainly after negative surprises (Jagliński 2020). A strong negative reaction is therefore mainly a warning (exit / avoid), not a signal to trade."},
    "liq": {"pl": "Dryf a płynność (podział po medianie obrotu {k} tys. zł)", "en": "Drift vs liquidity (split at median turnover {k}k PLN)"},
    "col_group": {"pl": "Reakcja", "en": "Reaction"},
    "col_bucket": {"pl": "Płynność", "en": "Liquidity"},
    "liquid": {"pl": "płynne", "en": "liquid"},
    "illiquid": {"pl": "mało płynne", "en": "illiquid"},
    "neg_group": {"pl": "negatywne", "en": "negative"},
    "pos_group": {"pl": "pozytywne", "en": "positive"},
    "profile": {"pl": "Jak zwykle reaguje {t}", "en": "How {t} usually reacts"},
    "basis_last_year": {"pl": "ten sam okres rok temu: {d}", "en": "same period last year: {d}"},
    "basis_statutory": {"pl": "termin z rozporządzenia MF, Dz.U. 2025 poz. 755, § 83 (zm. Dz.U. 2026 poz. 1205); termin w sobotę lub święto przesuwa się na następny dzień roboczy",
                        "en": "legal deadline, Dz.U. 2025 poz. 755, § 83 (amended Dz.U. 2026 poz. 1205); a deadline on a Saturday or holiday moves to the next working day"},
    "basis_q4": {"pl": "; to termin raportu rocznego - raportu za IV kwartał nie trzeba publikować (§ 83 ust. 2)",
                 "en": "; this is the annual report deadline - no Q4 quarterly report is required (§ 83 ust. 2)"},
    "profile_line": {"pl": "Liczba zdarzeń: {n}; średnia CAR {m}, średnia wielkość ruchu |CAR| {a}, dodatnich {p}; średni obrót w dniu reakcji {at}× mediany; średni dryf [+3,+20] {d}.",
                     "en": "Events: {n}; mean CAR {m}, mean absolute move |CAR| {a}, positive {p}; mean day-0 turnover {at}× median; mean drift [+3,+20] {d}."},
    "events": {"pl": "Wszystkie zdarzenia", "en": "All events"},
    "next": {"pl": "Następny raport: **{d}** ({k}; {b}).", "en": "Next report: **{d}** ({k}; {b})."},
    "estimated": {"pl": "data szacowana", "en": "estimated date"},
    "statutory_deadline": {"pl": "ustawowy termin", "en": "statutory deadline"},
    "no_next": {"pl": "Nie da się oszacować daty następnego raportu.", "en": "The next report date cannot be estimated."},
    "fund": {"pl": "Zmiana zysku netto r/r (lokalna historia): {v}.", "en": "Net profit YoY change (local history): {v}."},
    "fund_na": {"pl": "Niespodzianka fundamentalna: brak (nie ma dwóch porównywalnych okresów w lokalnej historii; konsensusu nie wymyślamy).",
                "en": "Fundamental surprise: unavailable (no two comparable periods in the local history; we never invent a consensus)."},
    "portfolio_none": {"pl": "Brak zdarzeń dla podanych spółek w oknie {d} dni.", "en": "No events for these stocks in the {d}-day window."},
    "warn_exit": {"pl": "⚠️ {t}: silnie negatywna reakcja na raport {p} ({d}, CAR {c}, z = {z}). Na GPW po takich reakcjach częściej obserwowano dalsze spadki — to sygnał do przeglądu tezy i stop-lossa, nie automatyczne polecenie sprzedaży.",
                 "en": "⚠️ {t}: strong negative reaction to the {p} report ({d}, CAR {c}, z = {z}). On the WSE such reactions were more often followed by further weakness — review the thesis and the stop; this is not an automatic sell instruction."},
    "pf_table": {"pl": "Ostatnia reakcja na raport", "en": "Latest earnings reaction"},
    "col_next": {"pl": "Następny raport", "en": "Next report"},
    "unmapped": {"pl": "Raporty spółek spoza mapy ISIN→ticker (głównie NewConnect / spoza WIG): {n} raportów.",
                 "en": "Reports of issuers outside the ISIN→ticker map (mostly NewConnect / outside WIG): {n} reports."},
    "skipped": {"pl": "Pominięte zdarzenia (za krótka historia, brak notowań): {n}.", "en": "Skipped events (too little history, no quotes): {n}."},
    "method": {"pl": "Jak to liczymy", "en": "How it is computed"},
    "method_txt": {"pl": ("Dzień reakcji: raport przed 9:00 → ta sama sesja; 9:00–17:05 → ta sama sesja (⚑ część dnia przed publikacją); po 17:05 lub w dzień wolny → następna sesja. "
                          "CAR [0,+2] = suma dziennych nadwyżek stopy zwrotu spółki nad WIG w 3 sesjach od dnia reakcji (ceny skorygowane o dywidendy i splity). "
                          "z = CAR / (odchylenie dziennych nadwyżek z okna [-250,-30] × √3). Ocena silna: |z| ≥ 2 i obrót ≥ 1,5× mediany z [-60,-6]; zwykła: |z| ≥ 1 i obrót ≥ 1× mediany. "
                          "Jeśli wstępne wyniki poprzedzają raport, to one są zdarzeniem. Korekty raportów pomijamy. Brak konsensusu analityków — „niespodziankę” mierzy reakcja rynku."),
                   "en": ("Reaction day: report before 09:00 → same session; 09:00–17:05 → same session (⚑ part of the day precedes the news); after 17:05 or on a non-session day → next session. "
                          "CAR [0,+2] = sum of the stock's daily returns in excess of WIG over 3 sessions from the reaction day (dividend/split adjusted). "
                          "z = CAR / (sd of daily excess returns over [-250,-30] × √3). Strong: |z| ≥ 2 and turnover ≥ 1.5× the [-60,-6] median; normal: |z| ≥ 1 and turnover ≥ 1× median. "
                          "Preliminary results that precede the report are the event. Corrections are ignored. No analyst consensus — the market reaction measures the surprise.")},
    "sources": {"pl": "Źródła", "en": "Sources"},
    "req": {"pl": "Zapytania HTTP w tym uruchomieniu: {n} (czas {s} s).", "en": "HTTP requests in this run: {n} ({s} s)."},
}


# --------------------------------------------------------------------------
# Request counting (network etiquette: the report states how many requests
# a run cost). Wraps the low-level fetchers at runtime; gpwlib is unchanged.
# --------------------------------------------------------------------------

REQUESTS = {"n": 0}


def _install_counters():
    if getattr(http, "_ere_counted", False):
        return
    orig_fetch, orig_get = http._fetch, gpw_archive._http_get

    def fetch(*a, **k):
        REQUESTS["n"] += 1
        return orig_fetch(*a, **k)

    def aget(*a, **k):
        REQUESTS["n"] += 1
        return orig_get(*a, **k)

    http._fetch, gpw_archive._http_get = fetch, aget
    http._ere_counted = True


# --------------------------------------------------------------------------
# Arguments
# --------------------------------------------------------------------------

def _date(text):
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD, got {!r}".format(text))


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lang", choices=["pl", "en"], default="pl")
    common.add_argument("--output-dir", default=".")
    common.add_argument("--no-files", action="store_true", help="print the Markdown, write nothing")
    common.add_argument("--cache-dir", default=None)
    common.add_argument("--offline", action="store_true", help="cache only, no network")
    common.add_argument("--as-of", type=_date, default=None, help="analyse as of this date (YYYY-MM-DD)")
    common.add_argument("--lookback-days", type=int, default=100,
                        help="ESPI calendar days to scan back from --as-of (default 100 = ~70 sessions; max %d without --history)" % MAX_LOOKBACK_NO_HISTORY)
    common.add_argument("--history", action="store_true",
                        help="scan ESPI back to the start of the price history (~2 years, up to ~700 requests on a cold cache)")
    common.add_argument("--sessions", type=int, default=None, help="price sessions to load (default 420; 500 with --history)")
    common.add_argument("--sessions-only", action="store_true",
                        help="skip weekends/holidays in the ESPI scan (fewer requests; misses reports published on those days)")
    common.add_argument("--min-turnover-k", type=float, default=DEFAULT_MIN_TURNOVER_K,
                        help="liquidity filter for top lists, median turnover in thousand PLN (default 100)")
    common.add_argument("--top", type=int, default=5)
    common.add_argument("--state-dir", default=None, help="gpw-trade-journal state dir (held positions)")
    common.add_argument("--fundamentals-dir", default=None, help="local fundamentals history store")
    p = argparse.ArgumentParser(prog="gpw_earnings_reaction.py",
                                description="GPW earnings reaction and post-earnings drift (PL/EN)")
    sub = p.add_subparsers(dest="mode")
    sub.required = True
    sub.add_parser("scan", parents=[common], help="season overview + strongest/weakest reactions")
    tk = sub.add_parser("ticker", parents=[common], help="all events of one stock")
    tk.add_argument("ticker")
    pf = sub.add_parser("portfolio", parents=[common], help="journal open positions and/or --tickers")
    pf.add_argument("--tickers", default=None, help="comma-separated tickers (default: journal open positions)")
    return p


def parse_args(argv=None):
    p = build_parser()
    a = p.parse_args(argv)
    if a.lookback_days < 1:
        p.error("--lookback-days must be >= 1")
    if a.lookback_days > MAX_LOOKBACK_NO_HISTORY and not a.history:
        p.error("--lookback-days > {} needs --history (one ESPI request per day)".format(MAX_LOOKBACK_NO_HISTORY))
    if a.sessions is not None and a.sessions < 120:
        p.error("--sessions must be >= 120")
    if a.top < 1:
        p.error("--top must be >= 1")
    if a.min_turnover_k < 0:
        p.error("--min-turnover-k must be >= 0")
    if a.as_of and a.as_of > plcal.today_warsaw():
        p.error("--as-of cannot be in the future")
    return a


class InputError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def _espi_range(start, end, cache_dir, offline, include_non_sessions):
    """fetch_range in two-week chunks with progress on stderr."""
    rows, days, missing = [], [], []
    d = start
    n_days = (end - start).days + 1
    done = 0
    while d <= end:
        e = min(end, d + dt.timedelta(days=13))
        res = espi.fetch_range(d, e, cache_dir=cache_dir, offline=offline, include_non_sessions=include_non_sessions)
        rows += res["rows"]
        days += res["days"]
        missing += res["missing_days"]
        done += (e - d).days + 1
        if not offline and n_days > 20:
            print("  ... ESPI list {} / {} days (up to {})".format(done, n_days, e.isoformat()), file=sys.stderr)
        d = e + dt.timedelta(days=1)
    return {"rows": rows, "days": days, "missing_days": missing}


def _r3(daily):
    return None if daily is None else round(3 * daily, 2)


def _held_tickers(state_dir):
    try:
        st = Path(state_dir).expanduser() if state_dir else journal_io.default_state_dir()
        return [p["ticker"].upper() for p in journal_io.open_positions(journal_io.load_theses(st))], str(st)
    except Exception:  # journal is optional; any read problem -> no held positions
        return [], None


def run(args) -> dict:
    _install_counters()
    t_start = time.time()
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else None
    ref_end = args.as_of or plcal.today_warsaw()
    n_sessions = args.sessions or (500 if args.history else 420)
    print("Loading GPW sessions ({})...".format(n_sessions), file=sys.stderr)
    shares = gpw_archive.load_sessions(n_sessions, end=ref_end, cache_dir=cache_dir, offline=args.offline)
    if len(shares) < 120:
        raise gpw_archive.GPWFetchError("Only {} share sessions available; need at least 120.".format(len(shares)))
    indices = gpw_archive.load_sessions(n_sessions, end=ref_end, type_id=gpw_archive.TYPE_INDICES,
                                        cache_dir=cache_dir, offline=args.offline, progress=False)
    wig = panel.index_series(indices, "WIG")
    if len(wig) < 60:
        raise gpw_archive.GPWFetchError("WIG index history unavailable ({} sessions).".format(len(wig)))
    market = dict(zip(wig.dates, wig.close))
    pnl = panel.build_panel(shares)
    known = sorted(s["date"] for s in shares)
    as_of = dt.date.fromisoformat(known[-1])

    # --- universe of the run
    held, journal_path = _held_tickers(args.state_dir)
    targets = None
    if args.mode == "ticker":
        t = args.ticker.strip().upper()
        if t not in pnl:
            raise InputError("Ticker {!r} not found in the GPW share archive (use the archive name, e.g. PKOBP, KGHM, CDPROJEKT).".format(t))
        targets = {t}
    elif args.mode == "portfolio":
        if args.tickers:
            targets = {x.strip().upper() for x in args.tickers.split(",") if x.strip()}
        else:
            targets = set(held)
        if not targets:
            raise InputError("No tickers: the journal has no open positions; pass --tickers A,B.")
        unknown = sorted(x for x in targets if x not in pnl)
        if unknown:
            raise InputError("Not in the GPW share archive: {}".format(", ".join(unknown)))

    # --- ESPI range
    if args.history:
        start = dt.date.fromisoformat(known[min(len(known) - 1, ere.DEFAULTS["min_obs_sigma_short"] + 6)])
    else:
        start = ref_end - dt.timedelta(days=args.lookback_days - 1)
    n_days = (ref_end - start).days + 1
    if n_days > MAX_LOOKBACK_NO_HISTORY and not args.offline:
        print("WARNING: scanning {} days of ESPI lists = up to ~{} requests on a cold cache (~{} min at 1 request/s). "
              "Past days are cached for 30 days.".format(n_days, n_days, max(1, n_days // 60)), file=sys.stderr)
    ex = _espi_range(start, ref_end, cache_dir, args.offline, include_non_sessions=not args.sessions_only)

    # --- ISIN -> ticker (main-market shares only)
    imap = fundamentals.isin_map(cache_dir, offline=args.offline)
    isin_to_ticker = {isin.upper(): t for t, isin in imap.items() if t in pnl}
    built = ere.build_events(ex["rows"], isin_to_ticker, known, plcal.is_probable_session)
    events = built["events"]
    if targets is not None:
        events = [e for e in events if e["ticker"] in targets]

    gaps = ere.gaps_from_sessions(shares, names={e["ticker"] for e in events})
    reactions, pending, skipped = [], [], []
    rps = {}
    store = Path(args.fundamentals_dir).expanduser() if args.fundamentals_dir else None
    for e in events:
        s = pnl[e["ticker"]]
        rp = rps.get(e["ticker"]) or ere.ReturnPanel(s.dates, s.close, market)
        rps[e["ticker"]] = rp
        m = ere.event_metrics(s.dates, s.close, s.high, s.low, s.turnover_k, market, e["reaction_date"],
                              gaps.get(e["ticker"]), rp=rp)
        rec = dict(e)
        if m["status"] == "pending":
            rec["status"] = "pending"
            pending.append(rec)
            continue
        if m["status"] == "skipped":
            rec.update(status="skipped", reason=m["reason"])
            skipped.append(rec)
            continue
        rec.update(m)
        try:
            hist = fundamentals.load_history(e["ticker"], store)
        except fundamentals.FundamentalsError:
            hist = None
        rec["fundamental_surprise"] = ere.fundamental_surprise(hist, e["period"])
        reactions.append(rec)

    size_map = constituents.size_membership(cache_dir, offline=args.offline)
    for r in reactions + pending:
        r["segment"] = size_map.get(r["ticker"], "other")
    season = ere.season_for(as_of)
    season_block = ere.season_stats(reactions, size_map, season["key"], args.min_turnover_k)
    season_block.update(label=season["label"], statutory_deadline=ere.statutory_deadline(season["key"]),
                        pending_reports=sum(1 for p in pending if p["period"] == season["key"]))
    drift = ere.drift_study(reactions)
    # baseline: the average liquid stock's daily excess return vs WIG over the season's reaction days
    season_days = sorted(r["reaction_date_used"] for r in reactions if r["period"] == season["key"])
    if season_days:
        base_panels = []
        for name, s in pnl.items():
            med60 = ere._median(s.turnover_k[-60:])
            if len(s) >= 120 and s.dates[-1] == known[-1] and med60 is not None and med60 >= ere.BASELINE_MIN_TURNOVER_K:
                base_panels.append(rps.get(name) or ere.ReturnPanel(s.dates, s.close, market))
        b = ere.baseline_daily_ar(base_panels, season_days[0], known[-1])
        season_block["baseline"] = dict(b, stocks=len(base_panels), start=season_days[0], end=known[-1],
                                        car_3d_pct=_r3(b["mean_daily_ar_pct"]))
        if season_block.get("mean_car_3d_pct") is not None and b["mean_daily_ar_pct"] is not None:
            season_block["mean_car_3d_vs_baseline_pp"] = round(season_block["mean_car_3d_pct"] - 3 * b["mean_daily_ar_pct"], 2)

    def liquid(r):
        return (r.get("median_turnover_k") or 0) >= args.min_turnover_k and (r.get("close") or 0) >= MIN_PRICE

    pool = [r for r in reactions if r["period"] == season["key"]] or reactions
    pool = [r for r in pool if liquid(r) and r.get("z") is not None]
    top_neg = sorted(pool, key=lambda r: r["z"])[:args.top]
    top_pos = sorted(pool, key=lambda r: -r["z"])[:args.top]
    top_neg = [r for r in top_neg if r["z"] < 0]
    top_pos = [r for r in top_pos if r["z"] > 0]

    raw_close = {}
    sig_names = {r["ticker"] for r in reactions if r["label"] in ("strong_negative", "strong_positive")}
    for sess in shares:
        for row in sess["rows"]:
            if row.get("name") in sig_names and row.get("close"):
                raw_close.setdefault(row["name"], {})[sess["date"]] = row["close"]
    signals = ere.make_signals(reactions, held, raw_close)
    per_ticker = {}
    if args.mode in ("ticker", "portfolio"):
        for t in sorted(targets):
            evs = [r for r in reactions + pending if r["ticker"] == t]
            nxt = ere.next_expected(evs, ref_end, known, plcal.is_probable_session)
            per_ticker[t] = {"profile": ere.ticker_profile([r for r in reactions if r["ticker"] == t]),
                             "next_expected": nxt, "held": t in held,
                             "latest": max((r for r in reactions if r["ticker"] == t), key=lambda r: r["reaction_date"], default=None)}

    contract_keys = ("ticker", "report_date", "report_type", "session_alignment", "car_3d_pct",
                     "abnormal_turnover", "drift_20s_pct", "label")
    out_reactions = []
    for r in sorted(reactions, key=lambda r: (r["reaction_date"], r["ticker"]), reverse=True):
        item = {k: r.get(k) for k in contract_keys}
        item.update({k: v for k, v in r.items() if k not in item and not k.startswith("_")})
        out_reactions.append(item)

    notes = [
        "Report dates and times from the ESPI/EBI list on gpw.pl; quarterly reports are classified by report type, "
        "preliminary results by title (gpwlib.espi). Corrections ignored.",
        "Prices adjusted for dividends and splits (gpwlib.panel); gaps measured against GPW's own reference price.",
        "Index size segments use the current GPW Benchmark portfolios (not point-in-time).",
        "No analyst consensus is used anywhere; fundamental_surprise comes only from the local fundamentals history.",
    ]
    if ex["missing_days"]:
        notes.append("ESPI list missing from cache for {} day(s) (offline).".format(len(ex["missing_days"])))
    prov = provenance.build(
        as_of=as_of,
        sources=["GPW — Archiwum notowań: https://www.gpw.pl/archiwum-notowan",
                 "GPW — komunikaty ESPI/EBI: https://www.gpw.pl/komunikaty (" + espi.LIST_URL + ", GPWEspiReportUnion)",
                 "GPW Benchmark — portfele indeksów: https://gpwbenchmark.pl"],
        sessions_used=len(shares), universe_size=len({r["ticker"] for r in reactions}),
        notes=notes, point_in_time_membership=False, reference_date=args.as_of)
    elapsed = round(time.time() - t_start, 1)
    return {
        "skill": SKILL, "version": VERSION, "lang": args.lang, "mode": args.mode,
        "as_of": as_of.isoformat(),
        "reactions": out_reactions,
        "season": season_block,
        "signals": signals,
        "drift_study": drift,
        "top": {"negative": [r["ticker"] + "@" + r["reaction_date"] for r in top_neg],
                "positive": [r["ticker"] + "@" + r["reaction_date"] for r in top_pos]},
        "_top_rows": {"negative": top_neg, "positive": top_pos},
        "pending": [{k: p.get(k) for k in ("ticker", "period", "report_type", "report_date", "published_at",
                                            "reaction_date", "session_alignment", "segment", "url")} for p in pending],
        "skipped": [{k: s.get(k) for k in ("ticker", "period", "report_date", "reaction_date", "reason")} for s in skipped],
        "tickers": per_ticker,
        "espi": {"start": start.isoformat(), "end": ref_end.isoformat(), "days_scanned": len(ex["days"]),
                 "rows": len(ex["rows"]), "missing_days": ex["missing_days"], "counts": built["counts"],
                 "unmapped_issuers": built["unmapped"][:20], "include_non_sessions": not args.sessions_only},
        "config": dict(ere.DEFAULTS, min_turnover_k=args.min_turnover_k, min_price=MIN_PRICE,
                       lookback_days=n_days, history=args.history),
        "held_tickers": held, "journal_path": journal_path,
        "run": {"http_requests": REQUESTS["n"], "runtime_s": elapsed},
        "data_provenance": prov,
    }


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

def _sp(x, lang, d=1):
    return pct(x, d, lang, signed=True)


def _drift_cell(r, t, lang):
    if r.get("drift_20s_pct") is not None:
        return _sp(r["drift_20s_pct"], lang)
    if r.get("drift_20s_status") == "pending":
        return t("pending", n=r.get("drift_20s_sessions_left"))
    return "—"


def _ctx(r):
    a = "↑" if r.get("above_sma50") else ("↓" if r.get("above_sma50") is False else "·")
    b = "↑" if r.get("above_sma200") else ("↓" if r.get("above_sma200") is False else "·")
    return a + "/" + b


def _row(r, t, lang, with_period=True):
    lab = t(r["label"]) + (t("prov") if r.get("provisional") else "")
    cells = ["**{}**".format(r["ticker"])]
    if with_period:
        cells.append(r["period_label"])
    cells += [r["reaction_date"], t(r["session_alignment"]), _sp(r.get("gap_pct"), lang), _sp(r.get("car_3d_pct"), lang),
              num(r.get("z"), 1, lang), num(r.get("abnormal_turnover"), 1, lang), _drift_cell(r, t, lang), _ctx(r), lab]
    return "| " + " | ".join(cells) + " |"


def _header(t, with_period=True):
    cols = [t("col_ticker")] + ([t("col_period")] if with_period else []) + [
        t("col_rday"), t("col_align"), t("col_gap"), t("col_car"), t("col_z"), t("col_at"), t("col_drift"), t("col_ctx"), t("col_label")]
    return ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]


def render_markdown(r: dict, lang: str) -> str:
    t = T(STRINGS, lang)
    prov = r["data_provenance"]
    mode = r["mode"]
    if mode == "scan":
        title = t("title_scan", s=r["season"]["label"])
    elif mode == "ticker":
        title = t("title_ticker", t=", ".join(r["tickers"]))
    else:
        title = t("title_portfolio")
    L = ["# " + title, "", "**{}:** {} · **{}:** {}".format(t("as_of"), prov["as_of"], t("generated"), prov["retrieved_at_utc"]), ""]
    if prov["freshness"]["is_stale"]:
        L += [t("stale", n=prov["freshness"]["sessions_behind"]), ""]
    if r["espi"]["missing_days"]:
        L += [t("espi_missing", n=len(r["espi"]["missing_days"])), ""]
    s = r["season"]
    L += ["## " + t("answer"), ""]
    reacts = r["reactions"]
    if mode == "scan":
        if s["events"]:
            dates = sorted(x["reaction_date"] for x in reacts if x.get("period") == s["key"])
            L.append(t("season_line", s=s["label"], n=s["events"], d0=dates[0], d1=dates[-1],
                       p=pct((s["share_positive_car"] or 0) * 100, 0, lang), m=_sp(s["mean_car_3d_pct"], lang, 2),
                       med=_sp(s["median_car_3d_pct"], lang, 2), sn=s["labels"]["strong_negative"], sp=s["labels"]["strong_positive"]))
            if s.get("mean_car_3d_vs_baseline_pp") is not None:
                L += ["", t("baseline_line", b=_sp(s["baseline"]["car_3d_pct"], lang, 2), d=num(s["mean_car_3d_vs_baseline_pp"], 2, lang))]
        else:
            L.append(t("no_season", s=s["label"]))
        if r["pending"]:
            L += ["", t("pending_line", n=len(r["pending"]))]
        L.append("")
        for side, key in (("negative", "top_neg"), ("positive", "top_pos")):
            rows = r["_top_rows"][side]
            L += ["### " + t(key, k=num(r["config"]["min_turnover_k"], 0, lang)), ""]
            if rows:
                L += _header(t) + [_row(x, t, lang) for x in rows]
            else:
                L.append("—")
            L.append("")
        L += ["## " + t("segments"), "", "| {} | {} | {} | {} | {} |".format(t("col_seg"), t("col_n"), t("col_mean"), t("col_median"), t("col_share")),
              "|---|---|---|---|---|"]
        for seg, v in s["by_segment"].items():
            L.append("| {} | {} | {} | {} | {} |".format(t("other") if seg == "other" else seg, v["n"], _sp(v["mean_car_3d_pct"], lang, 2),
                                                        _sp(v["median_car_3d_pct"], lang, 2),
                                                        pct((v["share_positive"] or 0) * 100, 0, lang) if v["n"] else "—"))
        L.append("")
    else:
        order = sorted(r["tickers"].items(), key=lambda kv: ((kv[1].get("latest") or {}).get("label") != "strong_negative", kv[0]))
        for tk, info in order:
            prof = info["profile"]
            L += ["### " + t("profile", t=tk), ""]
            lat = info.get("latest")
            if lat and lat["label"] == "strong_negative":
                L += [t("warn_exit", t=tk, p=lat["period_label"], d=lat["reaction_date"], c=_sp(lat["car_3d_pct"], lang),
                        z=num(lat["z"], 1, lang)), ""]
            if prof["events"]:
                L.append(t("profile_line", n=prof["events"], m=_sp(prof["mean_car_3d_pct"], lang), a=pct(prof["mean_abs_car_3d_pct"], 1, lang),
                           p=pct((prof["share_positive"] or 0) * 100, 0, lang), at=num(prof["mean_abnormal_turnover"], 1, lang),
                           d=_sp(prof["mean_drift_20s_pct"], lang)))
            else:
                L.append(t("portfolio_none", d=r["config"]["lookback_days"]))
            nx = info.get("next_expected")
            if nx:
                basis = (t("basis_last_year", d=nx["base_date"]) if nx["basis_kind"] == "last_year"
                         else t("basis_statutory") + (t("basis_q4") if nx["period"].endswith("Q4") else ""))
                L += ["", t("next", d=nx["date"], k=t(nx["date_kind"]), b=basis), ""]
            else:
                L += ["", t("no_next"), ""]
            evs = [x for x in reacts if x["ticker"] == tk]
            if evs:
                L += _header(t) + [_row(x, t, lang) for x in evs] + [""]
                fs = evs[0].get("fundamental_surprise") or {}
                L += [t("fund", v=_sp(fs["yoy_net_profit_pct"], lang)) if fs.get("yoy_net_profit_pct") is not None else t("fund_na"), ""]
    d = r["drift_study"]
    L += ["## " + t("drift"), "", "| {} | {} | {} | {} | {} | {} | {} |".format(t("col_label"), t("col_n"), t("col_drift"), t("col_t"),
                                                                            t("col_vsn"), t("col_share"), t("col_d60")),
          "|---|---|---|---|---|---|---|"]
    for row in d["by_label"]:
        vsn = "—" if row.get("vs_neutral_pp") is None else "{} ({})".format(num(row["vs_neutral_pp"], 2, lang), num(row["t_vs_neutral"], 2, lang))
        L.append("| {} | {} | {} | {} | {} | {} | {} |".format(t(row["label"]), row["n"], _sp(row["mean_pct"], lang, 2), num(row["t_stat"], 2, lang),
                                                             vsn, pct((row["share_positive"] or 0) * 100, 0, lang) if row["n"] else "—",
                                                             _sp(row["drift_60s"]["mean_pct"], lang, 2) + " (n={})".format(row["drift_60s"]["n"])))
    L += ["", t("drift_note", n=d["events_with_drift"], p=d["events_pending"]), ""]
    if d["by_liquidity"] and mode == "scan":
        L += ["### " + t("liq", k=num(d["liquidity_split_k"], 0, lang)), "",
              "| {} | {} | {} | {} | {} |".format(t("col_group"), t("col_bucket"), t("col_n"), t("col_drift"), t("col_t")), "|---|---|---|---|---|"]
        for row in d["by_liquidity"]:
            L.append("| {} | {} | {} | {} | {} |".format(t("neg_group" if row["group"] == "negative" else "pos_group"), t(row["bucket"]),
                                                        row["n"], _sp(row["mean_pct"], lang, 2), num(row["t_stat"], 2, lang)))
        L.append("")
    if r["espi"]["counts"].get("unmapped_rows"):
        L.append("- " + t("unmapped", n=r["espi"]["counts"]["unmapped_rows"]))
    if r["skipped"]:
        L.append("- " + t("skipped", n=len(r["skipped"])))
    L.append("- " + t("req", n=r["run"]["http_requests"], s=num(r["run"]["runtime_s"], 1, lang)))
    L += ["", "## " + t("method"), "", t("method_txt"), ""]
    L += ["## " + t("sources"), ""] + ["- " + x for x in prov["sources"]] + [
        "- Bernard & Thomas (1989), J. Accounting Research 27; Chordia, Goyal, Sadka, Sadka & Shivakumar (2009), Financial Analysts Journal 65(4); Jagliński (2020), Financial Sciences (UE Wrocław)",
        "", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


def public(result: dict) -> dict:
    return {k: v for k, v in result.items() if not k.startswith("_")}


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except (gpw_archive.GPWFetchError, http.HTTPFetchError, InputError, fundamentals.FundamentalsError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    md = render_markdown(result, args.lang)
    if args.no_files:
        print(md)
        return 0
    out = Path(args.output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    jp, mp = out / "{}_{}.json".format(PREFIX, stamp), out / "{}_{}.md".format(PREFIX, stamp)
    jp.write_text(json.dumps(public(result), ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(md, encoding="utf-8")
    s = result["season"]
    print("Season {}: {} reactions, {} positive CAR, strong -/+ {}/{}, pending {} | signals {} | as of {} | {} HTTP requests, {} s".format(
        s["label"], s["events"], s["share_positive_car"], s["labels"]["strong_negative"], s["labels"]["strong_positive"],
        len(result["pending"]), len(result["signals"]), result["as_of"], result["run"]["http_requests"], result["run"]["runtime_s"]))
    print("JSON: {}\nMarkdown: {}".format(jp, mp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
