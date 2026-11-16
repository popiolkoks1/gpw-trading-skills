#!/usr/bin/env python3
"""GPW Signal Post-mortem - CLI.

Did the signals of the GPW skills work? Records signals from skill reports
into a ledger, measures what happened next on real GPW prices (5/20/60
sessions, excess vs WIG and vs the equal-weighted universe, MFE/MAE, stop vs
pivot) and turns the results into evidence-gated feedback.

    python3 scripts/gpw_signal_postmortem.py record --input-dir ~/gpw-reports
    python3 scripts/gpw_signal_postmortem.py backfill --list old_reports.txt
    python3 scripts/gpw_signal_postmortem.py list
    python3 scripts/gpw_signal_postmortem.py evaluate --offline
    python3 scripts/gpw_signal_postmortem.py evaluate --demo --offline     # labelled demo
    python3 scripts/gpw_signal_postmortem.py feedback --input-dir .
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import constituents, gpw_archive, plcal, provenance, reports  # noqa: E402
from gpwlib.constituents import SECTOR_INDICES  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct  # noqa: E402

import postmortem_engine as pe  # noqa: E402
import postmortem_ledger as pl  # noqa: E402

VERSION = "1.0.0"
PREFIX = "gpw_signal_postmortem"
FEEDBACK_PREFIX = "gpw_signal_feedback"

STRINGS = {
    "title": {"pl": "Post-mortem sygnałów GPW", "en": "GPW Signal Post-mortem"},
    "fb_title": {"pl": "Wnioski z post-mortem sygnałów GPW", "en": "GPW Signal Post-mortem Feedback"},
    "as_of": {"pl": "Ceny do sesji", "en": "Prices up to session"},
    "generated": {"pl": "Wygenerowano", "en": "Generated"},
    "stale": {"pl": "⚠️ UWAGA: dane cenowe są nieaktualne o {n} sesji.", "en": "⚠️ WARNING: price data is {n} session(s) old."},
    "demo_banner": {
        "pl": ("⚠️ **TRYB DEMO.** Sygnały poniżej są SYNTETYCZNE - wygenerowane prostą regułą (pierwsze zamknięcie "
               "powyżej 52-tygodniowego maksimum, płynne spółki) na prawdziwych cenach z archiwum GPW. To NIE są "
               "sygnały Twoich skilli ani ich historyczne wyniki. Demo pokazuje tylko, jak działa pomiar."),
        "en": ("⚠️ **DEMO MODE.** The signals below are SYNTHETIC - produced by a simple rule (first close above the "
               "52-week closing high, liquid shares) on real GPW archive prices. They are NOT signals of your skills "
               "and NOT their track record. The demo only shows how the measurement works."),
    },
    "answer": {"pl": "Odpowiedź", "en": "Answer"},
    "headline": {
        "pl": "Twoje sygnały {what} z okresu {a} - {b}: **{hr} trafień**, średnio **{ex} pp vs {bench}** po {h} sesjach, n={n}{small}.",
        "en": "Your {what} signals from {a} - {b}: **{hr} hit rate**, on average **{ex} pp vs {bench}** after {h} sessions, n={n}{small}.",
    },
    "headline_abs": {
        "pl": "Sygnały {what} (indeks, wynik bezwzględny) z okresu {a} - {b}: **{hr} trafień**, średnio **{ex}%** po {h} sesjach, n={n}{small}.",
        "en": "{what} signals (index, absolute return) from {a} - {b}: **{hr} hit rate**, on average **{ex}%** after {h} sessions, n={n}{small}.",
    },
    "demo_what": {"pl": "DEMO (wybicie 52-tyg. maksimum)", "en": "DEMO (52-week-high breakout)"},
    "headline_demo": {
        "pl": "Syntetyczne sygnały DEMO (wybicie 52-tyg. maksimum, NIE Twoje) z okresu {a} - {b}: **{hr} trafień**, średnio **{ex} pp vs {bench}** po {h} sesjach, n={n}{small}.",
        "en": "Synthetic DEMO signals (52-week-high breakout, NOT yours) from {a} - {b}: **{hr} hit rate**, on average **{ex} pp vs {bench}** after {h} sessions, n={n}{small}.",
    },
    "fb_more": {"pl": "…oraz {n} pozycji bez dojrzałych sygnałów (n=0).", "en": "…plus {n} item(s) with no matured signals (n=0)."},
    "small": {"pl": " - **za mało na wnioski** (minimum {m})", "en": " - **too few to conclude** (minimum {m})"},
    "none_mature": {
        "pl": "Żaden z {n} sygnałów nie osiągnął jeszcze horyzontu {h} sesji - nie da się ich jeszcze ocenić. Najbliższy dojrzeje ok. {d}.",
        "en": "None of the {n} signals has reached the {h}-session horizon yet - they cannot be judged yet. The first matures around {d}.",
    },
    "no_signals": {"pl": "Rejestr sygnałów jest pusty (po filtrach). Najpierw: `record --input-dir <folder z raportami>`.",
                   "en": "The signal ledger is empty (after filters). First run: `record --input-dir <reports folder>`."},
    "pending_line": {"pl": "Oczekuje na horyzont {h} sesji: **{n}** sygnałów (najbliższy ok. {d}).",
                     "en": "Waiting for the {h}-session horizon: **{n}** signals (first around {d})."},
    "outcomes": {"pl": "Wyniki sygnałów (horyzont {h} sesji)", "en": "Signal outcomes ({h}-session horizon)"},
    "o_true_positive": {"pl": "trafny", "en": "true positive"},
    "o_false_positive": {"pl": "chybiony", "en": "false positive"},
    "o_regime_mismatch": {"pl": "chybiony w złym reżimie rynku", "en": "failed in a bad market regime"},
    "o_missed_opportunity": {"pl": "przegapiona okazja", "en": "missed opportunity"},
    "o_true_negative": {"pl": "słusznie pominięty", "en": "correctly skipped"},
    "o_pending": {"pl": "czeka na horyzont", "en": "pending"},
    "o_no_price_data": {"pl": "brak notowań", "en": "no price data"},
    "o_before_data": {"pl": "przed początkiem danych", "en": "before the data window"},
    "o_no_entry_price": {"pl": "brak ceny wejścia", "en": "no entry price"},
    "o_no_benchmark": {"pl": "brak benchmarku", "en": "no benchmark"},
    "col_class": {"pl": "Klasa", "en": "Class"},
    "col_n": {"pl": "Liczba", "en": "Count"},
    "by_h": {"pl": "Sygnały „long” według horyzontu", "en": "Long signals by horizon"},
    "col_h": {"pl": "Horyzont", "en": "Horizon"},
    "col_hit": {"pl": "Trafność", "en": "Hit rate"},
    "col_ci": {"pl": "95% CI", "en": "95% CI"},
    "col_mex": {"pl": "Śr. nadwyżka", "en": "Mean excess"},
    "col_medex": {"pl": "Mediana", "en": "Median"},
    "col_ew": {"pl": "vs równoważony rynek", "en": "vs equal-weight"},
    "col_t": {"pl": "t", "en": "t"},
    "col_td": {"pl": "t (po datach)", "en": "t (by date)"},
    "col_mfe": {"pl": "MFE / MAE", "en": "MFE / MAE"},
    "buckets": {"pl": "Koszyki (sygnały „long”, {h} sesji)", "en": "Buckets (long signals, {h} sessions)"},
    "col_dim": {"pl": "Wymiar", "en": "Dimension"},
    "col_bucket": {"pl": "Koszyk", "en": "Bucket"},
    "insufficient": {"pl": "za mało", "en": "too few"},
    "other_groups": {"pl": "Sygnały „avoid/exit” i pominięte kandydatury ({h} sesji)",
                     "en": "Avoid/exit signals and skipped candidates ({h} sessions)"},
    "g_avoid_exit": {"pl": "avoid / exit (trafny = spółka/indeks słabszy)", "en": "avoid / exit (hit = it lagged)"},
    "g_skip": {"pl": "pominięte przez skaner (trafny = słabsze od WIG)", "en": "skipped by the screener (hit = lagged WIG)"},
    "levels": {"pl": "Stop czy pivot pierwszy?", "en": "Stop or pivot first?"},
    "levels_txt": {"pl": "Z {n} sygnałów z poziomami: stop dotknięty {s} razy, pivot (gdy wejście poniżej) {p} razy, oba tego samego dnia {b}, żaden {z}.",
                   "en": "Of {n} signals with levels: stop touched first {s} times, pivot first (entry below it) {p} times, both on the same session {b}, neither {z}."},
    "feedback": {"pl": "Wnioski (sugestie, nie zmiany)", "en": "Feedback (suggestions, not changes)"},
    "recent": {"pl": "Ostatnie sygnały", "en": "Recent signals"},
    "col_date": {"pl": "Data", "en": "Date"},
    "col_ticker": {"pl": "Walor", "en": "Ticker"},
    "col_src": {"pl": "Źródło / rodzaj", "en": "Source / kind"},
    "col_dir": {"pl": "Kierunek", "en": "Direction"},
    "col_status": {"pl": "Status", "en": "Status"},
    "col_ret": {"pl": "Wynik {h}s", "en": "Return {h}s"},
    "col_exc": {"pl": "vs bench. {h}s", "en": "vs bench. {h}s"},
    "left": {"pl": "zostało {n} sesji", "en": "{n} sessions left"},
    "caveats": {"pl": "Zastrzeżenia", "en": "Caveats"},
    "c_small": {"pl": "Mała próba: koszyki z n < {m} są oznaczone „za mało” i nie dają wniosków. Przedział Wilsona pokazuje niepewność trafności.",
                "en": "Small samples: buckets with n < {m} are marked 'too few' and support no conclusion. The Wilson interval shows the hit-rate uncertainty."},
    "c_overlap": {"pl": "Nakładające się okna: sygnały z bliskich dat dzielą te same ruchy rynku (autokorelacja), więc zwykłe t zawyża pewność. Patrz kolumna „t (po datach)”: najpierw średnia na datę, potem t ({d} różnych dat dla {n} sygnałów).",
                  "en": "Overlapping windows: signals from nearby dates share the same market moves (autocorrelation), so the plain t overstates confidence. See 't (by date)': average per date first, then t ({d} distinct dates for {n} signals)."},
    "c_regime": {"pl": "Koncentracja reżimu: {p} ocenionych sygnałów powstało w stanie „{s}”. Wyniki mogą nie przenieść się na inny stan rynku.",
                 "en": "Regime concentration: {p} of evaluated signals were issued in the '{s}' state. Results may not carry over to other market states."},
    "c_regime_unknown": {"pl": "Brak osi czasu reżimu dla części sygnałów ({n}) - nagraj raport gpw-market-regime (`record`), aby rozróżnić „regime_mismatch”.",
                         "en": "No regime timeline for some signals ({n}) - record a gpw-market-regime report to separate 'regime_mismatch'."},
    "c_costs": {"pl": "Wyniki są przed prowizjami i podatkiem; na GPW koszt wejścia i wyjścia to zwykle 0,3-0,8% (minimalne prowizje).",
                "en": "Results are before commissions and tax; a GPW round trip usually costs 0.3-0.8% (minimum fees)."},
    "c_membership": {"pl": "Sektor i segment (WIG20/mWIG40/sWIG80) to bieżący skład indeksów, nie z dnia sygnału.",
                     "en": "Sector and size segment use current index membership, not membership on the signal date."},
    "c_survivor": {"pl": "Spółki, które przestały być notowane, zostają w próbie z ostatnim kursem (bez błędu przeżywalności).",
                   "en": "Names that stopped trading stay in the sample at their last close (no survivorship bias)."},
    "c_demo": {"pl": "DEMO: sygnały syntetyczne; wniosków nie wolno przenosić na skile.",
               "en": "DEMO: synthetic signals; do not carry conclusions over to any skill."},
    "method": {"pl": "Metoda", "en": "Method"},
    "method_txt": {
        "pl": ("Wejście: {entry}. Wynik = zmiana skorygowanego kursu (dywidendy, splity) do zamknięcia po h sesjach GPW. "
               "Nadwyżka = wynik minus {bench} w tym samym oknie; indeksy szerokie oceniamy wynikiem bezwzględnym. "
               "Trafny „long”: nadwyżka > 0; trafny „avoid/exit”: nadwyżka < 0. MFE/MAE = najlepszy/najgorszy ruch "
               "intraday w oknie. Szczegóły: references/methodology.md."),
        "en": ("Entry: {entry}. Return = change of the adjusted price (dividends, splits) to the close h GPW sessions later. "
               "Excess = return minus {bench} over the same window; broad indices are judged on absolute return. "
               "Long hit: excess > 0; avoid/exit hit: excess < 0. MFE/MAE = best/worst intraday move in the window. "
               "Details: references/methodology.md."),
    },
    "entry_close": {"pl": "zamknięcie z dnia sygnału", "en": "close on the signal day"},
    "entry_next_open": {"pl": "otwarcie następnej sesji", "en": "next session's open"},
    "bench_wig": {"pl": "WIG", "en": "WIG"},
    "bench_ew": {"pl": "równoważony koszyk płynnych spółek", "en": "the equal-weight liquid universe"},
    "inputs": {"pl": "Dane wejściowe", "en": "Inputs"},
    "ledger": {"pl": "Rejestr sygnałów", "en": "Signal ledger"},
    "regime_src": {"pl": "Oś czasu reżimu", "en": "Regime timeline"},
    "sources": {"pl": "Źródła", "en": "Sources"},
    "fb_from": {"pl": "Na podstawie raportu {f} (ceny do {a}, horyzont {h} sesji, {n} ocenionych sygnałów).",
                "en": "Based on report {f} (prices up to {a}, {h}-session horizon, {n} evaluated signals)."},
    "fb_how": {"pl": "Plik JSON może odczytać gpw-trend-screener lub gpw-exposure-coach; żadne ustawienie nie jest zmieniane automatycznie.",
               "en": "The JSON can be read by gpw-trend-screener or gpw-exposure-coach; no setting is changed automatically."},
}

SOURCES = ["GPW — Archiwum notowań (akcje i indeksy): https://www.gpw.pl/archiwum-notowan",
           "GPW Benchmark — portfele indeksów (sektor, segment): https://gpwbenchmark.pl"]


# ------------------------------------------------------------------- args

def _horizons(text: str) -> List[int]:
    try:
        hs = sorted({int(x) for x in text.split(",") if x.strip()})
    except ValueError:
        raise argparse.ArgumentTypeError("horizons must be integers, e.g. 5,20,60")
    if not hs or any(h < 1 or h > 250 for h in hs):
        raise argparse.ArgumentTypeError("each horizon must be 1-250 sessions")
    return hs


def _date_arg(text: str) -> dt.date:
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD")


def parse_args(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lang", choices=["pl", "en"], default="pl")
    common.add_argument("--state-dir", default=None, help="Ledger folder (default ~/.gpw-trading-skills/signals)")
    p = argparse.ArgumentParser(description="GPW signal post-mortem (PL/EN)")
    sub = p.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", parents=[common], help="Ingest skill reports into the signal ledger")
    rec.add_argument("files", nargs="*", help="Report JSON files")
    rec.add_argument("--input-dir", default=None, help="Ingest every supported report in this folder")
    rec.add_argument("--latest", action="store_true",
                     help="With --input-dir: only the newest report of each known skill (gpwlib.reports)")
    rec.add_argument("--no-near-misses", action="store_true", help="Do not record screener candidates left off the watchlist")
    rec.add_argument("--dry-run", action="store_true", help="Show what would be recorded, write nothing")

    bf = sub.add_parser("backfill", parents=[common], help="Ingest a list of historical reports (oldest first)")
    bf.add_argument("files", nargs="*")
    bf.add_argument("--list", dest="list_file", default=None,
                    help="Text file with one report path per line (or a JSON array of paths)")
    bf.add_argument("--no-near-misses", action="store_true")
    bf.add_argument("--dry-run", action="store_true")

    ls = sub.add_parser("list", parents=[common], help="Summarise the ledger")
    ls.add_argument("--json", action="store_true")

    ev = sub.add_parser("evaluate", parents=[common], help="Measure forward returns and write the post-mortem report")
    ev.add_argument("--demo", action="store_true", help="Synthetic 52-week-high signals on real prices (labelled DEMO)")
    ev.add_argument("--horizons", type=_horizons, default=list(pe.DEFAULT_HORIZONS))
    ev.add_argument("--primary-horizon", type=int, default=pe.DEFAULT_PRIMARY)
    ev.add_argument("--entry", choices=["close", "next_open"], default="close")
    ev.add_argument("--hit-benchmark", choices=["wig", "ew"], default="wig")
    ev.add_argument("--min-n", type=int, default=pe.DEFAULT_MIN_N)
    ev.add_argument("--since", type=_date_arg, default=None)
    ev.add_argument("--until", type=_date_arg, default=None)
    ev.add_argument("--months", type=int, default=None, help="Only signals from the last N months (overrides --since)")
    ev.add_argument("--source", action="append", default=None, help="Filter by source skill (repeatable)")
    ev.add_argument("--kind", action="append", default=None, help="Filter by signal kind (repeatable)")
    ev.add_argument("--input-dir", default=".", help="Folder searched for a gpw-market-regime report (regime timeline)")
    ev.add_argument("--regime-json", default=None)
    ev.add_argument("--as-of", type=_date_arg, default=None)
    ev.add_argument("--sessions", type=int, default=500)
    ev.add_argument("--offline", action="store_true")
    ev.add_argument("--cache-dir", default=None)
    ev.add_argument("--output-dir", default=".")
    ev.add_argument("--no-files", action="store_true")
    ev.add_argument("--write-feedback", action="store_true", help="Also write gpw_signal_feedback_*.json")

    fb = sub.add_parser("feedback", parents=[common], help="Write gpw_signal_feedback_*.json from a post-mortem report")
    fb.add_argument("--postmortem-json", default=None)
    fb.add_argument("--input-dir", default=".")
    fb.add_argument("--output-dir", default=".")

    a = p.parse_args(argv)
    if a.cmd == "evaluate":
        if a.primary_horizon not in a.horizons:
            p.error("--primary-horizon must be one of --horizons")
        if a.min_n < 2:
            p.error("--min-n must be >= 2")
        if a.sessions < 30:
            p.error("--sessions must be >= 30")
        if a.months is not None and a.months < 1:
            p.error("--months must be >= 1")
    if a.cmd == "backfill" and not a.files and not a.list_file:
        p.error("backfill needs report files or --list FILE")
    if a.cmd == "record" and not a.files and not a.input_dir:
        p.error("record needs report files or --input-dir")
    if a.cmd == "record" and a.latest and not a.input_dir:
        p.error("--latest needs --input-dir")
    return a


def state_dir(args) -> Path:
    return Path(args.state_dir).expanduser() if args.state_dir else pl.default_state_dir()


# ----------------------------------------------------------------- record

def _scan_dir(folder: Path) -> List[Path]:
    # gpwlib.reports.find_report returns only the newest report per skill; to
    # ingest *all* history we list the folder and load each file with
    # reports.load_report (same parsing and errors as the shared helper).
    try:
        return sorted(p for p in folder.glob("*.json") if p.is_file())
    except OSError:
        return []


def ingest(paths: List[Path], ledger: Dict, explicit: bool, near_misses: bool) -> Dict:
    summary = {"files": 0, "new": 0, "duplicates": 0, "rejected": 0, "skipped_files": [], "per_file": []}
    for path in paths:
        try:
            data = reports.load_report(path)
            ex = pl.extract(data, path.name, near_misses=near_misses)
        except (reports.ReportError, pl.LedgerError) as exc:
            if explicit:
                raise pl.LedgerError("{}: {}".format(path, exc))
            summary["skipped_files"].append({"file": path.name, "reason": str(exc)[:120]})
            continue
        entry = pl.merge(ledger, ex, str(path))
        entry["rejected_items"] = ex["rejected"][:20]
        summary["files"] += 1
        summary["new"] += entry["new"]
        summary["duplicates"] += entry["duplicates"]
        summary["rejected"] += entry["rejected"]
        summary["per_file"].append(entry)
    return summary


def cmd_record(args) -> int:
    sd = state_dir(args)
    ledger = pl.load_ledger(sd)
    paths: List[Path] = [Path(f).expanduser() for f in getattr(args, "files", []) or []]
    explicit = list(paths)
    if getattr(args, "list_file", None):
        lf = Path(args.list_file).expanduser()
        try:
            text = lf.read_text(encoding="utf-8")
        except OSError as exc:
            raise pl.LedgerError("cannot read --list {}: {}".format(lf, exc))
        try:
            items = json.loads(text)
            if not isinstance(items, list):
                raise ValueError
        except ValueError:
            items = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        base = lf.parent
        for it in items:
            q = Path(str(it)).expanduser()
            explicit.append(q if q.is_absolute() else base / q)
    summary = ingest(explicit, ledger, True, not args.no_near_misses) if explicit else None
    if getattr(args, "input_dir", None):
        folder = Path(args.input_dir).expanduser()
        if not folder.is_dir():
            raise pl.LedgerError("--input-dir {} is not a folder".format(folder))
        if args.latest:
            found = [reports.find_report(s, folder) for s in pl.ADAPTERS]
            dir_paths = [Path(f["path"]) for f in found if f]
        else:
            dir_paths = _scan_dir(folder)
        s2 = ingest(dir_paths, ledger, False, not args.no_near_misses)
        if summary is None:
            summary = s2
        else:
            for k in ("files", "new", "duplicates", "rejected"):
                summary[k] += s2[k]
            summary["skipped_files"] += s2["skipped_files"]
            summary["per_file"] += s2["per_file"]
    if not args.dry_run:
        path = pl.save_ledger(sd, ledger)
    else:
        path = sd / pl.LEDGER_FILE
    print(("DRY RUN - " if args.dry_run else "") +
          "Reports ingested: {files} | new signals: {new} | duplicates skipped: {duplicates} | items rejected: {rejected}".format(**summary))
    for e in summary["per_file"]:
        print("  {:<55} {:<20} as_of {}  +{} new, {} dup, {} rejected".format(
            Path(e["file"]).name[:55], str(e["source"])[:20], e["as_of"], e["new"], e["duplicates"], e["rejected"]))
        for r in e.get("rejected_items", [])[:5]:
            print("      rejected: {}".format(r))
    if summary["skipped_files"]:
        print("  skipped {} non-signal/unsupported file(s)".format(len(summary["skipped_files"])))
    print("Ledger: {} ({} signals)".format(path, len(ledger["signals"])))
    return 0


def cmd_list(args) -> int:
    ledger = pl.load_ledger(state_dir(args))
    sigs = ledger["signals"]
    groups: Dict[str, int] = {}
    for s in sigs:
        k = "{} / {} / {}".format(s["source"], s["kind"], s["direction"])
        groups[k] = groups.get(k, 0) + 1
    out = {"ledger": str(state_dir(args) / pl.LEDGER_FILE), "signals": len(sigs),
           "first_date": sigs[0]["date"] if sigs else None, "last_date": sigs[-1]["date"] if sigs else None,
           "by_source_kind_direction": dict(sorted(groups.items())),
           "regime_timelines": [{k: t[k] for k in ("index", "start", "end")} for t in ledger["regime_timelines"]],
           "reports_ingested": len(ledger["ingested"])}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print("Ledger: {ledger}\nSignals: {signals} ({first_date} .. {last_date}); reports ingested: {reports_ingested}".format(**out))
    for k, v in out["by_source_kind_direction"].items():
        print("  {:<70} {}".format(k, v))
    for t in out["regime_timelines"]:
        print("  regime timeline {index}: {start} .. {end}".format(**t))
    return 0


# --------------------------------------------------------------- evaluate

def _months_back(d: dt.date, months: int) -> dt.date:
    y, m = d.year, d.month - months
    while m <= 0:
        m += 12
        y -= 1
    day = min(d.day, 28)
    return dt.date(y, m, day)


def _add_sessions(d: dt.date, n: int) -> dt.date:
    while n > 0:
        d += dt.timedelta(days=1)
        if plcal.is_probable_session(d):
            n -= 1
    return d


def load_book(args):
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else None
    end = args.as_of
    print("Loading GPW sessions (cache first; first online run downloads ~2 years)...", file=sys.stderr)
    shares = gpw_archive.load_sessions(args.sessions, end=end, cache_dir=cache_dir, offline=args.offline)
    if len(shares) < 30:
        raise gpw_archive.GPWFetchError("Only {} share sessions available; need at least 30.".format(len(shares)))
    indices = gpw_archive.load_sessions(args.sessions, end=end, type_id=gpw_archive.TYPE_INDICES, cache_dir=cache_dir,
                                        offline=args.offline, progress=False)
    book = pe.instruments_from_sessions(shares, indices)
    if book.get("WIG") is None:
        raise gpw_archive.GPWFetchError("WIG index quotes unavailable - cannot compute excess returns.")
    try:
        sector = constituents.sector_membership(cache_dir, offline=args.offline)
        size = constituents.size_membership(cache_dir, offline=args.offline)
    except gpw_archive.GPWFetchError:
        sector, size = {}, {}
    for key in SECTOR_INDICES:
        sector.setdefault(key, key)
    return book, {"sector": sector, "size": size}, len(shares)


def filter_signals(sigs: List[Dict], args, as_of: dt.date) -> List[Dict]:
    since = _months_back(as_of, args.months) if args.months else args.since
    out = []
    for s in sigs:
        if since and s["date"] < since.isoformat():
            continue
        if args.until and s["date"] > args.until.isoformat():
            continue
        if s["date"] > as_of.isoformat():
            continue
        if args.source and s["source"] not in args.source:
            continue
        if args.kind and s["kind"] not in args.kind:
            continue
        out.append(s)
    return out


def regime_inputs(args, ledger: Dict, as_of: dt.date) -> Dict:
    timelines = list(ledger.get("regime_timelines") or [])
    info = {"from_ledger": len(timelines), "report": None}
    try:
        rep = reports.find_report("gpw-market-regime", args.input_dir, explicit_path=args.regime_json,
                                  reference=as_of, lang=args.lang)
    except reports.ReportError as exc:
        raise pl.LedgerError(str(exc))
    if rep:
        tl = pl.regime_timeline(rep["data"], "WIG")
        if tl and not any((x["index"], x["start"], x["end"]) == (tl["index"], tl["start"], tl["end"]) for x in timelines):
            timelines.append(tl)
            info["report"] = {"file": rep["file"], "as_of": rep["as_of"], "start": tl["start"], "end": tl["end"],
                              "stale": rep["stale"]}
    info["coverage"] = [{"start": t["start"], "end": t["end"]} for t in timelines if t.get("index") == "WIG"]
    return {"timelines": timelines, "info": info}


def run_evaluate(args) -> Dict:
    ledger = pl.empty_ledger() if args.demo else pl.load_ledger(state_dir(args))
    book, meta, n_sess = load_book(args)
    as_of = dt.date.fromisoformat(book.calendar[-1])
    sigs = pe.demo_signals(book) if args.demo else ledger["signals"]
    sigs = filter_signals(sigs, args, as_of)
    reg = regime_inputs(args, ledger, as_of)
    lookup = (lambda d: pl.regime_at(reg["timelines"], d)) if reg["timelines"] else None
    cfg = {"horizons": args.horizons, "primary": args.primary_horizon, "entry": args.entry,
           "hit_benchmark": args.hit_benchmark}
    results = [pe.evaluate_signal(s, book, cfg, lookup, meta) for s in sigs]
    buckets = pe.build_buckets(results, args.horizons, args.min_n)
    feedback = pe.build_feedback(results, args.primary_horizon, args.min_n, demo=args.demo)
    evaluated = [r for r in results if r["status"] == "evaluated"]
    pending = [r for r in results if r["status"] == "pending"]
    nxt = None
    if pending:
        left = min((r.get("sessions_left") or 1) for r in pending)
        nxt = _add_sessions(as_of, left).isoformat()
    regimes: Dict[str, int] = {}
    for r in evaluated:
        k = r.get("regime_at_signal") or "unknown"
        regimes[k] = regimes.get(k, 0) + 1
    prov = provenance.build(
        as_of=as_of, sources=SOURCES, sessions_used=n_sess, universe_size=None,
        notes=(["DEMO run: synthetic 52-week-high signals, not skill output."] if args.demo else [])
        + ["Prices are dividend/split adjusted (gpwlib.panel); results exclude commissions and tax.",
           "Sector and size segment use current GPW Benchmark membership (not point-in-time)."],
        point_in_time_membership=False, reference_date=args.as_of)
    return {
        "skill": pl.SKILL, "version": VERSION, "lang": args.lang, "as_of": as_of.isoformat(),
        "mode": "demo" if args.demo else "ledger", "demo": bool(args.demo),
        "config": {"horizons": args.horizons, "primary_horizon": args.primary_horizon, "entry": args.entry,
                   "hit_benchmark": args.hit_benchmark, "min_n": args.min_n,
                   "since": (_months_back(as_of, args.months).isoformat() if args.months else
                             (args.since.isoformat() if args.since else None)),
                   "until": args.until.isoformat() if args.until else None,
                   "sources": args.source, "kinds": args.kind},
        "ledger": None if args.demo else {"path": str(state_dir(args) / pl.LEDGER_FILE),
                                          "signals_total": len(ledger["signals"])},
        "regime": reg["info"],
        "signals_total": len(results),
        "signals_evaluated": len(evaluated),
        "pending": len(pending),
        "next_maturity": nxt,
        "outcomes": pe.outcome_counts(results),
        "regime_mix": regimes,
        "headline": headline_rows(results, args.primary_horizon, args.min_n),
        "buckets": buckets,
        "feedback": feedback,
        "results": results,
        "data_provenance": prov,
    }


def headline_rows(results: List[Dict], h: int, min_n: int) -> List[Dict]:
    groups: Dict[tuple, List[Dict]] = {}
    for r in results:
        if r["direction"] != "long":
            continue
        groups.setdefault((r["source"], r["kind"], r.get("basis") or "excess"), []).append(r)
    rows = []
    for (src, kind, basis), rs in groups.items():
        done = [(r, r["horizons"][str(h)]) for r in rs
                if (r["horizons"].get(str(h)) or {}).get("status") == "done" and r["horizons"][str(h)].get("edge_pp") is not None]
        st = pe.bucket_stats(done, min_n) if done else {"n": 0}
        rows.append({"source": src, "kind": kind, "basis": basis, "signals": len(rs),
                     "first_date": min(r["date"] for r in rs), "last_date": max(r["date"] for r in rs),
                     "pending": sum(1 for r in rs if r["status"] == "pending"), **st})
    rows.sort(key=lambda x: (-x.get("n", 0), -x["signals"]))
    return rows


# --------------------------------------------------------------- markdown

def _hr(x, lang):
    return "—" if x is None else pct(100 * x, 0, lang)


def _pp(x, lang):
    return "—" if x is None else ("+" if x > 0 else "") + num(x, 1, lang)


def render_markdown(r: Dict, lang: str) -> str:
    t = T(STRINGS, lang)
    cfg, prov = r["config"], r["data_provenance"]
    h = cfg["primary_horizon"]
    bench = "WIG" if cfg["hit_benchmark"] == "wig" else ("EW" if lang == "en" else "rynek równoważony")
    L = ["# " + t("title"), "", "**{}:** {} · **{}:** {}".format(t("as_of"), r["as_of"], t("generated"), prov["retrieved_at_utc"]), ""]
    if prov["freshness"]["is_stale"]:
        L += [t("stale", n=prov["freshness"]["sessions_behind"]), ""]
    if r["demo"]:
        L += ["> " + t("demo_banner"), ""]
    L += ["## " + t("answer"), ""]
    if not r["signals_total"]:
        L += [t("no_signals"), ""]
    elif not r["signals_evaluated"]:
        L += [t("none_mature", n=r["signals_total"], h=h, d=r["next_maturity"] or "—"), ""]
    for row in r["headline"][:4]:
        if not row.get("n"):
            continue
        what = t("demo_what") if r["demo"] else "{} / {}".format(row["source"], row["kind"])
        small = t("small", m=cfg["min_n"]) if row["n"] < cfg["min_n"] else ""
        key = "headline_demo" if r["demo"] else ("headline_abs" if row["basis"] == "absolute" else "headline")
        L.append("- " + t(key, what=what, a=row["first_date"], b=row["last_date"], hr=_hr(row["hit_rate"], lang),
                          ex=_pp(row["mean_excess_pct"], lang), bench=bench, h=h, n=row["n"], small=small))
    if r["pending"]:
        L.append("- " + t("pending_line", h=h, n=r["pending"], d=r["next_maturity"] or "—"))
    L.append("")
    # outcomes
    L += ["### " + t("outcomes", h=h), "", "| {} | {} |".format(t("col_class"), t("col_n")), "|---|---:|"]
    for k, v in sorted(r["outcomes"].items(), key=lambda kv: -kv[1]):
        L.append("| {} | {} |".format(t("o_" + k), v))
    L.append("")
    # by horizon (long, all)
    allb = [b for b in r["buckets"] if b["dimension"] == "all" and b["direction_group"] == "long"]
    if allb:
        L += ["### " + t("by_h"), "",
              "| {} | n | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                  t("col_h"), t("col_hit"), t("col_ci"), t("col_mex"), t("col_medex"), t("col_ew"), t("col_t"),
                  t("col_td"), t("col_mfe")),
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for b in allb:
            L.append("| {} | {}{} | {} | {}-{} | {} pp | {} pp | {} pp | {} | {} | {}% / {}% |".format(
                b["horizon"], b["n"], " ⚠️" if b["status"] != "ok" else "", _hr(b["hit_rate"], lang),
                _hr(b["hit_rate_ci95"][0], lang), _hr(b["hit_rate_ci95"][1], lang), _pp(b["mean_excess_pct"], lang),
                _pp(b["median_excess_pct"], lang), _pp(b["mean_excess_ew_pp"], lang), num(b["t_stat"], 1, lang),
                num(b["t_stat_by_date"], 1, lang), _pp(b["mean_mfe_pct"], lang), _pp(b["mean_mae_pct"], lang)))
        L.append("")
    # buckets at primary horizon
    pb = [b for b in r["buckets"] if b["horizon"] == h and b["direction_group"] == "long" and b["dimension"] != "all"]
    if pb:
        L += ["### " + t("buckets", h=h), "",
              "| {} | {} | n | {} | {} | {} | {} |".format(t("col_dim"), t("col_bucket"), t("col_hit"), t("col_ci"),
                                                       t("col_mex"), t("col_td")),
              "|---|---|---:|---:|---:|---:|---:|"]
        for b in pb[:60]:
            L.append("| {} | {} | {}{} | {} | {}-{} | {} pp | {} |".format(
                b["dimension"], b["bucket"], b["n"], " ({})".format(t("insufficient")) if b["status"] != "ok" else "",
                _hr(b["hit_rate"], lang), _hr(b["hit_rate_ci95"][0], lang), _hr(b["hit_rate_ci95"][1], lang),
                _pp(b["mean_excess_pct"], lang), num(b["t_stat_by_date"], 1, lang)))
        L.append("")
    og = [b for b in r["buckets"] if b["horizon"] == h and b["direction_group"] != "long" and b["dimension"] in ("all", "kind")]
    if og:
        L += ["### " + t("other_groups", h=h), "",
              "| {} | {} | n | {} | {} | {} |".format(t("col_dim"), t("col_bucket"), t("col_hit"), t("col_ci"), t("col_mex")),
              "|---|---|---:|---:|---:|---:|"]
        for b in og:
            L.append("| {} | {} | {}{} | {} | {}-{} | {} pp |".format(
                t("g_" + b["direction_group"]), b["bucket"], b["n"], " ({})".format(t("insufficient")) if b["status"] != "ok" else "",
                _hr(b["hit_rate"], lang), _hr(b["hit_rate_ci95"][0], lang), _hr(b["hit_rate_ci95"][1], lang),
                _pp(b["mean_excess_pct"], lang)))
        L.append("")
    lv = [x["levels"] for x in r["results"] if x.get("levels") and x["levels"]["first"] != "pending"]
    if lv:
        c = {k: sum(1 for x in lv if x["first"] == k) for k in ("stop", "pivot", "same_session", "none")}
        L += ["### " + t("levels"), "", t("levels_txt", n=len(lv), s=c["stop"], p=c["pivot"], b=c["same_session"], z=c["none"]), ""]
    # feedback
    fb = r["feedback"]
    L += ["## " + t("feedback"), "", "**" + fb["summary"][lang] + "**", ""]
    shown = sorted(fb["suggestions"], key=lambda s: ["supported", "contradicted", "suggestive", "suggestive_against",
                                                     "not_supported", "insufficient"].index(s["verdict"]))
    empty = [s for s in shown if not (s["evidence"].get("n") or s["evidence"].get("n_a") or s["evidence"].get("n_b"))]
    for s in [s for s in shown if s not in empty][:12]:
        L.append("- `{}` - {}".format(s["verdict"], s["statement"][lang]))
    if empty:
        L.append("- " + t("fb_more", n=len(empty)))
    L += ["", "_" + fb["note"][lang] + "_", ""]
    # recent signals
    rec = sorted(r["results"], key=lambda x: (x["date"], x["ticker"]), reverse=True)[:15]
    if rec:
        L += ["## " + t("recent"), "",
              "| {} | {} | {} | {} | {} | {} | {} |".format(t("col_date"), t("col_ticker"), t("col_src"), t("col_dir"),
                                                        t("col_status"), t("col_ret", h=h), t("col_exc", h=h)),
              "|---|---|---|---|---|---:|---:|"]
        for x in rec:
            ph = x["horizons"].get(str(h)) or {}
            status = t("o_" + x["outcome"]) if x["status"] == "evaluated" else (
                t("left", n=x.get("sessions_left")) if x["status"] == "pending" and x.get("sessions_left") else t("o_" + x["status"]))
            L.append("| {} | {} | {} / {} | {} | {} | {} | {} |".format(
                x["date"], x["ticker"], x["source"], x["kind"], x["direction"], status,
                pct(ph.get("return_pct"), 1, lang, signed=True), _pp(ph.get("excess_pp"), lang)))
        L.append("")
    # caveats
    ev = [x for x in r["results"] if x["status"] == "evaluated"]
    L += ["## " + t("caveats"), ""]
    if r["demo"]:
        L.append("- " + t("c_demo"))
    L.append("- " + t("c_small", m=cfg["min_n"]))
    if ev:
        L.append("- " + t("c_overlap", d=len({x.get("signal_session") for x in ev}), n=len(ev)))
        mix = r["regime_mix"]
        top = max(mix.items(), key=lambda kv: kv[1])
        if top[0] != "unknown" and top[1] / len(ev) >= 0.7:
            L.append("- " + t("c_regime", p=pct(100 * top[1] / len(ev), 0, lang), s=top[0]))
        if mix.get("unknown"):
            L.append("- " + t("c_regime_unknown", n=mix["unknown"]))
    L += ["- " + t("c_costs"), "- " + t("c_membership"), "- " + t("c_survivor"), ""]
    L += ["## " + t("method"), "", t("method_txt", entry=t("entry_" + cfg["entry"]), bench=t("bench_" + cfg["hit_benchmark"])), ""]
    L += ["## " + t("inputs"), ""]
    if r["ledger"]:
        L.append("- {}: `{}` ({} {})".format(t("ledger"), r["ledger"]["path"], r["ledger"]["signals_total"],
                                             "sygnałów" if lang == "pl" else "signals"))
    for c in r["regime"]["coverage"]:
        L.append("- {}: WIG {} .. {}".format(t("regime_src"), c["start"], c["end"]))
    L += ["", "## " + t("sources"), ""] + ["- " + s for s in prov["sources"]] + ["", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


def feedback_doc(report: Dict, report_file: str, lang: str) -> Dict:
    return {
        "skill": pl.SKILL, "version": VERSION, "lang": lang, "report_type": "feedback",
        "as_of": report["as_of"], "demo": report.get("demo", False),
        "based_on": {"file": report_file, "signals_evaluated": report.get("signals_evaluated"),
                     "pending": report.get("pending"), "config": report.get("config")},
        "feedback": report["feedback"],
        "buckets": [b for b in report.get("buckets", []) if b["status"] == "ok"
                    and b["horizon"] == report["config"]["primary_horizon"]],
        "data_provenance": report.get("data_provenance"),
    }


def render_feedback_md(doc: Dict, lang: str) -> str:
    t = T(STRINGS, lang)
    fb = doc["feedback"]
    L = ["# " + t("fb_title"), "", t("fb_from", f=doc["based_on"]["file"], a=doc["as_of"], h=fb["horizon"],
                                     n=doc["based_on"]["signals_evaluated"]), "",
         "**" + fb["summary"][lang] + "**", ""]
    for s in fb["suggestions"]:
        L.append("- `{}` ({}{}) - {}".format(s["verdict"], s["target_skill"],
                                             (" · " + s["parameter"]) if s["parameter"] else "", s["statement"][lang]))
    L += ["", t("fb_how"), "", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


def _write(out_dir: Path, prefix: str, doc: Dict, md: str):
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    jp, mp = out_dir / "{}_{}.json".format(prefix, stamp), out_dir / "{}_{}.md".format(prefix, stamp)
    jp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(md, encoding="utf-8")
    return jp, mp


def cmd_evaluate(args) -> int:
    result = run_evaluate(args)
    md = render_markdown(result, args.lang)
    print(md)
    if not args.no_files:
        out = Path(args.output_dir).expanduser()
        jp, mp = _write(out, PREFIX, result, md)
        print("JSON: {}\nMarkdown: {}".format(jp, mp))
        if args.write_feedback:
            if result["demo"]:
                print("Demo run: feedback file not written (synthetic signals).", file=sys.stderr)
            else:
                doc = feedback_doc(result, jp.name, args.lang)
                fj, fm = _write(out, FEEDBACK_PREFIX, doc, render_feedback_md(doc, args.lang))
                print("Feedback JSON: {}".format(fj))
    return 0


def cmd_feedback(args) -> int:
    try:
        rep = reports.find_report(pl.SKILL, args.input_dir, explicit_path=args.postmortem_json, lang=args.lang)
    except reports.ReportError as exc:
        raise pl.LedgerError(str(exc))
    if rep is None or rep["data"].get("report_type") == "feedback" and not args.postmortem_json:
        # find_report picks the newest gpw-signal-postmortem file; skip our own feedback files
        rep = None
        folder = Path(args.input_dir).expanduser()
        best = None
        for p in _scan_dir(folder) if folder.is_dir() else []:
            try:
                d = reports.load_report(p)
            except reports.ReportError:
                continue
            if d.get("skill") == pl.SKILL and d.get("report_type") != "feedback" and "buckets" in d:
                key = (d.get("as_of") or "", p.stat().st_mtime)
                if best is None or key > best[0]:
                    best = (key, p, d)
        if best:
            rep = {"file": best[1].name, "data": best[2]}
    if rep is None:
        raise pl.LedgerError("no gpw-signal-postmortem report found - run `evaluate` first")
    data = rep["data"]
    if data.get("report_type") == "feedback" or "buckets" not in data:
        raise pl.LedgerError("{} is not a post-mortem evaluation report".format(rep["file"]))
    if data.get("demo"):
        raise pl.LedgerError("{} is a DEMO report (synthetic signals) - feedback is not written".format(rep["file"]))
    doc = feedback_doc(data, rep["file"], args.lang)
    md = render_feedback_md(doc, args.lang)
    print(md)
    jp, mp = _write(Path(args.output_dir).expanduser(), FEEDBACK_PREFIX, doc, md)
    print("JSON: {}\nMarkdown: {}".format(jp, mp))
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if args.cmd in ("record", "backfill"):
            if args.cmd == "backfill":
                args.input_dir, args.latest = None, False
            return cmd_record(args)
        if args.cmd == "list":
            return cmd_list(args)
        if args.cmd == "evaluate":
            return cmd_evaluate(args)
        return cmd_feedback(args)
    except (pl.LedgerError, gpw_archive.GPWFetchError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
