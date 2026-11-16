#!/usr/bin/env python3
"""gpw-daily-brief: the one page to read after the GPW close.

    # assemble the brief from reports already in a folder (pure assembly)
    python3 scripts/gpw_daily_brief.py brief --input-dir ~/gpw-reports
    python3 scripts/gpw_daily_brief.py brief --input-dir ~/gpw-reports --weekly --full

    # run the daily pipeline of sibling skills into one folder, then the brief
    python3 scripts/gpw_daily_brief.py run --output-dir ~/gpw-reports
    python3 scripts/gpw_daily_brief.py run --output-dir ~/gpw-reports --weekly
    python3 scripts/gpw_daily_brief.py run --output-dir DIR --steps breadth,regime --offline
    python3 scripts/gpw_daily_brief.py run --list-steps

Exit codes: 0 ok, 1 data/input error (message on stderr), 2 bad arguments.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import fmt, gpw_archive, journal_io, plcal, provenance  # noqa: E402
from gpwlib.reports import ReportError  # noqa: E402

import daily_brief_engine as engine  # noqa: E402
import daily_brief_inputs as inputs_mod  # noqa: E402
import daily_brief_runner as runner  # noqa: E402

STRINGS = {
    "no_inputs": {"pl": "Brak raportów skilli GPW w {d}. Uruchom najpierw: python3 scripts/gpw_daily_brief.py run --output-dir {d}",
                  "en": "No GPW skill reports in {d}. Run first: python3 scripts/gpw_daily_brief.py run --output-dir {d}"},
    "run_head": {"pl": "Przebieg pipeline'u ({n} kroków) → {d}", "en": "Pipeline run ({n} steps) → {d}"},
    "run_sum": {"pl": "Podsumowanie: {ok} ok, {failed} błąd, {timeout} timeout, {skipped} pominięte, {ni} niezainstalowane; łącznie {t} s",
                "en": "Summary: {ok} ok, {failed} failed, {timeout} timeout, {skipped} skipped, {ni} not installed; total {t} s"},
    "steps_list": {"pl": "Kroki (kolejność): ", "en": "Steps (in order): "},
}


def _date(s: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD, got {!r}".format(s))


def _ids(s: str) -> List[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _pos_int(s: str) -> int:
    v = int(s)
    if v < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return v


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="gpw_daily_brief.py", description="GPW daily brief (PL/EN)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def brief_opts(p, run_mode: bool = False):
        p.add_argument("--lang", choices=("pl", "en"), default="pl")
        p.add_argument("--as-of", type=_date, help="Historical brief: ignore reports dated after this day")
        p.add_argument("--weekly", action="store_true", help="Weekly variant (macro, portfolio risk, post-mortem)")
        p.add_argument("--full", action="store_true", help="Longer brief: no caps, full freshness table")
        p.add_argument("--no-files", action="store_true", help="Print only, write nothing")
        p.add_argument("--json", action="store_true", help="Print the JSON instead of the Markdown")
        p.add_argument("--journal-dir", help="gpw-trade-journal folder (default ~/.gpw-trading-skills/journal)")
        p.add_argument("--no-journal", action="store_true")
        p.add_argument("--include-paper", action="store_true", help="Include paper positions")
        p.add_argument("--watchlist", help="gpw-watchlist state file (default ~/.gpw-trading-skills/watchlist.json)")
        p.add_argument("--no-watchlist", action="store_true")
        p.add_argument("--cache-dir", help="GPW archive cache (closing prices; read only, never downloads)")
        p.add_argument("--no-prices", action="store_true", help="Do not read closing prices from the cache")
        p.add_argument("--new-sessions", type=_pos_int, default=3,
                       help="Window for 'new' signals when there is no previous brief (sessions, default 3)")
        p.add_argument("--calendar-sessions", type=_pos_int, default=5, help="Calendar horizon (default 5)")
        p.add_argument("--event-sessions", type=_pos_int, default=10,
                       help="Events of held stocks within N sessions (default 10)")

    b = sub.add_parser("brief", help="Assemble the brief from existing reports")
    b.add_argument("--input-dir", default=".", help="Folder with the other skills' JSON reports (default .)")
    b.add_argument("--output-dir", help="Where to write the brief (default: --input-dir)")
    brief_opts(b)

    r = sub.add_parser("run", help="Run the daily pipeline of sibling skills, then the brief")
    r.add_argument("--output-dir", default=".", help="One folder for every step's reports and the brief (default .)")
    r.add_argument("--steps", type=_ids, help="Comma-separated subset of steps (see --list-steps)")
    r.add_argument("--skip", type=_ids, help="Comma-separated steps to skip")
    r.add_argument("--list-steps", action="store_true", help="Print the step ids and exit")
    r.add_argument("--offline", action="store_true", help="Pass --offline to every data step (cache only)")
    r.add_argument("--timeout", type=float, help="Timeout per step in seconds (default: per step, 2-45 min)")
    r.add_argument("--account-size", type=float, help="Passed to gpw-exposure-coach and gpw-trend-screener")
    r.add_argument("--insider-days", type=_pos_int, default=3, help="gpw-ownership insiders --days (default 3)")
    r.add_argument("--earnings-lookback", type=_pos_int, default=30,
                   help="gpw-earnings-reaction scan --lookback-days (default 30)")
    r.add_argument("--state-root", help="Sandbox state folder for ownership and signal ledger (testing)")
    r.add_argument("--skills-dir", help=argparse.SUPPRESS)
    r.add_argument("--no-brief", action="store_true", help="Only run the steps")
    r.add_argument("--strict", action="store_true", help="Exit 1 when any step failed or timed out")
    brief_opts(r, run_mode=True)
    return ap


# ------------------------------------------------------------------ helpers
def load_positions(args) -> Optional[List[Dict]]:
    if args.no_journal:
        return None
    theses = journal_io.load_theses(Path(args.journal_dir).expanduser() if args.journal_dir else None)
    return journal_io.open_positions(theses, include_paper=args.include_paper)


def watchlist_path(args) -> Optional[Path]:
    if args.no_watchlist:
        return None
    return Path(args.watchlist).expanduser() if args.watchlist else inputs_mod.default_watchlist_path()


def load_watch(args, collected: Dict) -> Optional[Dict]:
    """Explicit --watchlist file > newest gpw-watchlist report in the folder
    > default state file."""
    if args.no_watchlist:
        return None
    if not args.watchlist:
        rep = (collected.get("watchlist_report") or {}).get("current")
        if rep and isinstance(rep["data"].get("items"), list):
            return {"path": rep["path"], "items": [i for i in rep["data"]["items"] if isinstance(i, dict) and i.get("ticker")],
                    "updated_at": rep.get("as_of"), "error": None}
    return inputs_mod.load_watchlist(watchlist_path(args))


def load_prices(args, as_of: Optional[dt.date], tickers: List[str]) -> (Dict, Optional[str]):
    """Last closes of ``tickers`` from the local archive cache (no network)."""
    if args.no_prices or not tickers:
        return {}, None
    try:
        sessions = gpw_archive.load_sessions(1, end=as_of, type_id=gpw_archive.TYPE_SHARES,
                                             cache_dir=Path(args.cache_dir).expanduser() if args.cache_dir else None,
                                             offline=True, progress=False)
    except Exception as exc:  # cache problems must not stop the brief
        print("warning: closing prices unavailable: {}".format(exc), file=sys.stderr)
        return {}, None
    if not sessions:
        return {}, None
    s = sessions[-1]
    want = set(tickers)
    out = {}
    for row in s.get("rows") or []:
        name = str(row.get("name") or "").upper()
        if name in want and row.get("close") is not None:
            out[name] = {"close": row["close"], "date": s.get("date")}
    return out, s.get("date")


def assemble(args, input_dir: Path, output_dir: Optional[Path]) -> Dict:
    """Collect inputs, build the brief, render, write. Returns paths + brief."""
    lang = args.lang
    collected = inputs_mod.collect(input_dir, reference=args.as_of, lang=lang)
    positions = load_positions(args)
    found = [k for k, s in collected.items() if s.get("current") and k != "previous_brief"]
    if not found and not positions:
        raise ReportError(fmt.T(STRINGS, lang)("no_inputs", d=input_dir))
    watch = load_watch(args, collected)
    tickers = sorted({str(p["ticker"]).upper() for p in (positions or [])}
                     | {str(i["ticker"]).upper() for i in ((watch or {}).get("items") or [])})
    as_of = engine.market_as_of(collected)
    prices, price_date = load_prices(args, as_of or args.as_of, tickers)
    wpath = watchlist_path(args)
    brief = engine.build_brief(collected, positions=positions, watch=watch, prices=prices, weekly=args.weekly,
                               lang=lang, new_sessions=args.new_sessions, calendar_sessions=args.calendar_sessions,
                               position_event_sessions=args.event_sessions, journal_used=not args.no_journal,
                               watch_disabled=args.no_watchlist, watch_path=str(wpath) if wpath else None)
    used = [r for r in brief["inputs"] if r.get("path")]
    notes = ["Pure assembly of other skills' reports; no market data downloaded."]
    if price_date:
        notes.append("Closing prices for positions/watchlist from the cached GPW archive, session {}.".format(price_date))
    brief["data_provenance"] = provenance.build(
        as_of=dt.date.fromisoformat(brief["as_of"]) if brief["as_of"] else (args.as_of or plcal.today_warsaw()),
        sources=[r["path"] for r in used] + (["https://www.gpw.pl/archiwum-notowan (local cache)"] if price_date else []),
        sessions_used=1 if price_date else 0, universe_size=len(used), notes=notes,
        reference_date=args.as_of, max_sessions_behind=1)
    brief["journal"] = {"used": positions is not None, "open_positions": len(positions or []),
                        "dir": str(Path(args.journal_dir).expanduser()) if args.journal_dir else str(journal_io.default_state_dir())}
    brief["prices"] = {"session": price_date, "tickers": len(prices)}
    md = engine.render_markdown(brief, lang=lang, full=args.full, price_date=price_date)
    paths = {}
    if not args.no_files and output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        prefix = "gpw_daily_brief_weekly_" if args.weekly else "gpw_daily_brief_"
        jp, mp = output_dir / (prefix + stamp + ".json"), output_dir / (prefix + stamp + ".md")
        jp.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
        mp.write_text(md, encoding="utf-8")
        paths = {"json": str(jp), "md": str(mp)}
    return {"brief": brief, "markdown": md, "paths": paths}


def _print_brief(res: Dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(res["brief"], ensure_ascii=False, indent=2))
    else:
        print(res["markdown"])
    for k in ("json", "md"):
        if res["paths"].get(k):
            print("{}: {}".format("JSON" if k == "json" else "Markdown", res["paths"][k]))


# ------------------------------------------------------------------ commands
def cmd_brief(args) -> int:
    input_dir = Path(args.input_dir).expanduser()
    out = Path(args.output_dir).expanduser() if args.output_dir else input_dir
    res = assemble(args, input_dir, out)
    _print_brief(res, args.json)
    return 0


def cmd_run(args) -> int:
    T = fmt.T(STRINGS, args.lang)
    if args.list_steps:
        print(T("steps_list") + ", ".join(
            s["id"] + (" (weekly)" if s.get("weekly") else "") for s in runner.STEPS))
        return 0
    try:
        steps = runner.select_steps(args.steps, args.skip, args.weekly)
    except ValueError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    out_dir = Path(args.output_dir).expanduser().resolve()
    if args.state_root and not args.watchlist and not args.no_watchlist:
        # sandbox: the watchlist steps write <state-root>/watchlist.json
        args.watchlist = str(Path(args.state_root).expanduser() / "watchlist.json")
    positions = load_positions(args)
    wpath = watchlist_path(args)
    ctx = {"output_dir": str(out_dir), "lang": args.lang, "offline": args.offline,
           "as_of": args.as_of.isoformat() if args.as_of else None, "account_size": args.account_size,
           "insider_days": args.insider_days, "earnings_lookback": args.earnings_lookback,
           "state_root": str(Path(args.state_root).expanduser()) if args.state_root else None,
           "journal_dir": args.journal_dir, "watchlist": args.watchlist,
           "has_positions": bool(positions), "watchlist_path": str(wpath) if wpath else None}
    skills_root = Path(args.skills_dir).expanduser() if args.skills_dir else runner.SKILLS_ROOT
    print(T("run_head", n=len(steps), d=out_dir), flush=True)
    t0 = dt.datetime.now()
    records = runner.run_pipeline(ctx, steps, skills_root=skills_root, timeout=args.timeout,
                                  log=lambda line: print(line, flush=True))
    counts = {k: sum(1 for r in records if r["status"] == k) for k in ("ok", "failed", "timeout", "skipped", "not_installed")}
    total = round(sum(r["duration_s"] for r in records), 1)
    print(T("run_sum", ok=counts["ok"], failed=counts["failed"], timeout=counts["timeout"], skipped=counts["skipped"],
            ni=counts["not_installed"], t=fmt.num(total, 1, args.lang)))
    print("")
    log = {"skill": engine.SKILL, "version": engine.VERSION, "lang": args.lang, "mode": "run",
           "as_of": None, "started_at": t0.isoformat(timespec="seconds"), "output_dir": str(out_dir),
           "weekly": args.weekly, "offline": args.offline, "steps": records, "counts": counts, "total_s": total,
           "brief": None}
    rc = 0
    if not args.no_brief:
        try:
            res = assemble(args, out_dir, out_dir)
            log["brief"] = res["paths"] or None
            log["as_of"] = res["brief"]["as_of"]
            _print_brief(res, args.json)
        except ReportError as exc:
            print("error: {}".format(exc), file=sys.stderr)
            rc = 1
    if not args.no_files:
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        lp = out_dir / "gpw_daily_brief_run_{}.json".format(stamp)
        lp.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Run log: {}".format(lp))
    if args.strict and (counts["failed"] or counts["timeout"]):
        rc = 1
    return rc


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "brief":
            return cmd_brief(args)
        return cmd_run(args)
    except ReportError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
