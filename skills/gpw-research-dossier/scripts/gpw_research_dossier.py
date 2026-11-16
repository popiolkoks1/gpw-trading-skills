#!/usr/bin/env python3
"""gpw-research-dossier - one sourced research file per GPW company, then a
structured bull/bear debate whose every claim cites a dossier fact id.

Sub-commands
------------
build TICKER            collect the newest relevant JSON of every GPW skill
                        from --input-dir into sections + facts[] + missing[]
                        (--run fills missing/stale sections by running the
                        sibling skills' CLIs, sequentially, with a timeout)
debate-template TICKER  fill-in debate skeleton (JSON + Markdown) with fact ids
validate-debate FILE    deterministic guardrail: citations, 3-5 points a side,
                        kill criteria, pre-mortem, unknowns, no placeholders
to-thesis FILE          ready ``gpw-trade-journal add ...`` command (never run)

    python3 scripts/gpw_research_dossier.py build KETY --input-dir ~/gpw --output-dir ~/gpw
    python3 scripts/gpw_research_dossier.py build PKOBP --input-dir ~/gpw --run --offline
    python3 scripts/gpw_research_dossier.py debate-template KETY --input-dir ~/gpw --output-dir ~/gpw
    python3 scripts/gpw_research_dossier.py validate-debate ~/gpw/gpw_research_debate_KETY_....json
    python3 scripts/gpw_research_dossier.py to-thesis ~/gpw/gpw_research_debate_KETY_....json

Exit codes: 0 ok, 1 data/input error or an invalid debate (details on
stderr / in the report), 2 bad arguments.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import constituents, journal_io, plcal, provenance, reports  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num  # noqa: E402

import dossier_debate as dd  # noqa: E402
import dossier_engine as de  # noqa: E402
import dossier_runner as dr  # noqa: E402

SKILL = "gpw-research-dossier"
VERSION = "1.0.0"
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9_\-]{0,19}$")
DEFAULT_TIMEOUT_S = 300  # per sibling skill; a cold earnings/ESPI scan can need minutes
WATCHLIST_PATH = Path.home() / ".gpw-trading-skills" / "watchlist.json"

STRINGS = {
    "title": {"pl": "Dossier spółki {t}", "en": "Research dossier {t}"},
    "headline": {"pl": "**{t}: {n} sekcji, {f} faktów do cytowania; brakuje {m} sekcji{opt}.** Dane z {a0} – {a1}.",
                 "en": "**{t}: {n} sections, {f} citable facts; {m} sections missing{opt}.** Data from {a0} – {a1}."},
    "opt": {"pl": " (+{n} opcjonalnych)", "en": " (+{n} optional)"},
    "stale_head": {"pl": "⚠️ **Nieaktualne sekcje (podaj to najpierw):**", "en": "⚠️ **Stale sections (mention first):**"},
    "stale_row": {"pl": "- {s}: dane na {a}, {n} sesji wstecz (limit {m}) → odśwież: `{c}`",
                  "en": "- {s}: data as of {a}, {n} sessions old (limit {m}) → refresh: `{c}`"},
    "sections": {"pl": "Sekcje", "en": "Sections"},
    "th_sec": {"pl": "| Sekcja | Dane na | Status | Podsumowanie |", "en": "| Section | As of | Status | Summary |"},
    "ok": {"pl": "aktualna", "en": "fresh"},
    "stale": {"pl": "NIEAKTUALNA", "en": "STALE"},
    "facts": {"pl": "Fakty (id do cytowania w debacie)", "en": "Facts (ids to cite in the debate)"},
    "th_fact": {"pl": "| id | wartość |", "en": "| id | value |"},
    "missing": {"pl": "Brakujące sekcje i jak je uzupełnić", "en": "Missing sections and how to fill them"},
    "na": {"pl": "Nie dotyczy", "en": "Not applicable"},
    "runs": {"pl": "Uruchomione komendy (--run)", "en": "Commands run (--run)"},
    "next": {"pl": "Następne kroki", "en": "Next steps"},
    "next_debate": {"pl": "1. Debata (najpierw bear, potem bull, każdy punkt z id faktów): `{c}`",
                    "en": "1. Debate (bear first, then bull, every point with fact ids): `{c}`"},
    "next_sizer": {"pl": "2. Wielkość pozycji (ryzyko {r}% z {src}): `{c}`",
                   "en": "2. Position size (risk {r}% from {src}): `{c}`"},
    "next_gate": {"pl": "3. Bramka przed transakcją: `{c}`", "en": "3. Pre-trade gate: `{c}`"},
    "next_thesis": {"pl": "4. Teza do dziennika (po walidacji debaty): `{c}`",
                    "en": "4. Journal thesis (after the debate validates): `{c}`"},
    "errors": {"pl": "Błędy odczytu raportów", "en": "Report read errors"},
    "sector_line": {"pl": "Sektor: {s} (źródło: {src}).", "en": "Sector: {s} (source: {src})."},
    "sources": {"pl": "Źródła", "en": "Sources"},
    "no_sector": {"pl": "brak indeksu sektorowego", "en": "no sector index"},
    "reason_no_report": {"pl": "brak raportu w katalogu", "en": "no report in the folder"},
    "reason_ticker_not_in_report": {"pl": "raport nie obejmuje spółki", "en": "the report does not cover the ticker"},
    "reason_unusable_report": {"pl": "raport bez wymaganych pól", "en": "report lacks the required fields"},
    "reason_no_state": {"pl": "brak pliku stanu", "en": "no state file"},
    "reason_not_in_state": {"pl": "spółki nie ma w stanie", "en": "ticker not in the state"},
    "reason_no_sector": {"pl": "spółka bez indeksu sektorowego", "en": "company has no sector index"},
    "reason_not_in_open_positions": {"pl": "spółki nie ma w otwartych pozycjach dziennika",
                                     "en": "ticker is not an open journal position"},
    "debate_title": {"pl": "Debata bull/bear: {t}", "en": "Bull/bear debate: {t}"},
    "debate_saved": {"pl": "Szablon zapisany: {p}. Wypełnij go i uruchom: `{c}`",
                     "en": "Template saved: {p}. Fill it in, then run: `{c}`"},
    "valid": {"pl": "✅ Debata poprawna: bear {b}, bull {u}, {c} cytowań ({d} różnych faktów z {s} sekcji).",
              "en": "✅ Debate is valid: bear {b}, bull {u}, {c} citations ({d} distinct facts from {s} sections)."},
    "invalid": {"pl": "❌ Debata niepoprawna: {n} błędów.", "en": "❌ Debate is invalid: {n} errors."},
    "embedded": {"pl": "Uwaga: nie znaleziono dossier - walidacja względem listy faktów zapisanej w szablonie.",
                 "en": "Note: dossier not found - validated against the fact list embedded in the template."},
    "thesis_head": {"pl": "Komenda gpw-trade-journal (NIE została uruchomiona - uruchom ją sam po przeczytaniu):",
                    "en": "gpw-trade-journal command (NOT executed - run it yourself after reading it):"},
}


# --------------------------------------------------------------------------
# Arguments
# --------------------------------------------------------------------------

def _ticker(s: str) -> str:
    t = s.strip().upper()
    if not TICKER_RE.match(t):
        raise argparse.ArgumentTypeError("invalid GPW ticker {!r} (use the short name, e.g. KETY, PKOBP)".format(s))
    return t


def _date(s: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD")


def _pos(s: str) -> float:
    v = float(s)
    if v <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return v


def _nonneg_int(s: str) -> int:
    v = int(s)
    if v < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return v


def parse_args(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lang", choices=["pl", "en"], default="pl")
    common.add_argument("--input-dir", default=".", help="folder with the other skills' JSON reports (default .)")
    common.add_argument("--output-dir", default=".", help="where to write files (default .)")
    common.add_argument("--no-files", action="store_true", help="print only, write nothing")
    common.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    p = argparse.ArgumentParser(prog="gpw_research_dossier.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", parents=[common], help="collect the dossier for one ticker")
    b.add_argument("ticker", type=_ticker)
    b.add_argument("--report", action="append", default=[], help="extra report file outside --input-dir (repeatable)")
    b.add_argument("--run", action="store_true", help="run sibling skills for missing/stale sections")
    b.add_argument("--only", default=None, help="with --run: comma list of skills to run (e.g. gpw-valuation)")
    b.add_argument("--offline", action="store_true", help="pass --offline to sibling skills; cache-only membership")
    b.add_argument("--timeout", type=_pos, default=DEFAULT_TIMEOUT_S, help="seconds per sibling skill (default 300)")
    b.add_argument("--skills-dir", default=None, help="where sibling skills live (default: next to this skill)")
    b.add_argument("--as-of", type=_date, default=None, help="historical run: ignore reports dated later")
    b.add_argument("--max-age-sessions", type=_nonneg_int, default=None,
                   help="override every section's staleness limit (sessions)")
    b.add_argument("--sector", default=None, help="GPW sector index key, e.g. WIG-BANKI (default: detected)")
    b.add_argument("--state-dir", default=None, help="gpw-trade-journal state dir (default ~/.gpw-trading-skills/journal)")
    b.add_argument("--no-journal", action="store_true")
    b.add_argument("--watchlist", default=None, help="gpw-watchlist state (default ~/.gpw-trading-skills/watchlist.json)")
    b.add_argument("--cache-dir", default=None)

    t = sub.add_parser("debate-template", parents=[common], help="fill-in debate skeleton")
    t.add_argument("ticker", type=_ticker)
    t.add_argument("--dossier-json", default=None)

    v = sub.add_parser("validate-debate", parents=[common], help="check a filled debate")
    v.add_argument("file")
    v.add_argument("--dossier-json", default=None)

    th = sub.add_parser("to-thesis", parents=[common], help="gpw-trade-journal add command from a valid debate")
    th.add_argument("file")
    th.add_argument("--dossier-json", default=None)
    th.add_argument("--status", choices=["IDEA", "READY"], default="IDEA")
    th.add_argument("--skills-dir", default=None)
    return p.parse_args(argv)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def load_reports(input_dir: str, extra: List[str], reference: Optional[dt.date]) -> Dict[str, List[Dict]]:
    """{skill: [report, ...] newest first} for every skill the dossier reads."""
    by: Dict[str, List[Dict]] = {}
    for rep in reports.iter_reports(input_dir, None, reference=reference, newest_first=True):
        # a variant report type (gpw-sector-trend "mode": "rrg") is never filed
        # under its skill name - the classic sector-trend reader would misread it
        key = de.RRG_KEY if rep.get("variant") and rep["skill"] == "gpw-sector-trend" else rep["skill"]
        if rep.get("variant") and key == rep["skill"]:
            key = "{}:{}".format(rep["skill"], rep.get("mode"))
        by.setdefault(key, []).append(rep)
    for spec in de.SPECS:
        if spec.get("pattern") and spec["skill"] not in by:
            legacy = reports.iter_reports(input_dir, spec["skill"], pattern=spec["pattern"], reference=reference,
                                          newest_first=True)
            if legacy:
                by[spec["skill"]] = legacy
    for path in extra:
        data = reports.load_report(path)  # raises ReportError -> exit 1
        skill = data.get("skill")
        if not isinstance(skill, str):
            raise reports.ReportError("{} has no 'skill' field".format(path))
        a = reports.report_as_of(data)
        if reports.is_variant(data, skill):
            skill = de.RRG_KEY if skill == "gpw-sector-trend" else "{}:{}".format(skill, data.get("mode"))
        by.setdefault(skill, []).insert(0, {"skill": data.get("skill"), "path": str(Path(path).expanduser()),
                                            "file": Path(path).name, "data": data,
                                            "as_of": a.isoformat() if a else None})
    return by


def watchlist_path(path: Optional[str] = None) -> Path:
    """Watchlist state file per the contract: ``--watchlist`` (a file, or a
    state dir holding watchlist.json), else ``$GPW_WATCHLIST_DIR/watchlist.json``,
    else ``~/.gpw-trading-skills/watchlist.json``."""
    if path:
        p = Path(path).expanduser()
        return p / "watchlist.json" if p.is_dir() else p
    env = os.environ.get("GPW_WATCHLIST_DIR")
    if env:
        return Path(env).expanduser() / "watchlist.json"
    return WATCHLIST_PATH


def load_watchlist(path: Optional[str]):
    """``(items, path, warning)``. Contract rules: read-only, unknown fields
    ignored, a ``schema_version`` whose major is not 1 -> warn and skip."""
    p = watchlist_path(path)
    if not p.is_file():
        return None, str(p), None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, str(p), "watchlist unreadable: {}".format(exc)
    if isinstance(data, dict):
        ver = str(data.get("schema_version") or "1.0")
        if ver.split(".")[0] != "1":
            return None, str(p), "watchlist schema_version {} not supported (expected 1.x) - skipped".format(ver)
    items = data.get("items") if isinstance(data, dict) else data if isinstance(data, list) else None
    if not isinstance(items, list):
        return None, str(p), "watchlist has no items[]"
    return items, str(p), None


def load_journal(state_dir: Optional[str], disabled: bool):
    if disabled:
        return None, None, None
    d = Path(state_dir).expanduser() if state_dir else journal_io.default_state_dir()
    if not (d / "theses").is_dir():
        return None, [], str(d)
    theses = journal_io.load_theses(d)
    return theses, journal_io.open_positions(theses, include_paper=True), str(d)


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def _gap_commands(gaps, stale, ticker, args, sector, has_pos, python="python3"):
    out = []
    for skill in [g["skill"] for g in gaps] + stale:
        cmd = dr.command_for(skill, ticker, str(Path(args.input_dir).expanduser().resolve()), lang=args.lang,
                             offline=args.offline, as_of=args.as_of.isoformat() if args.as_of else None,
                             sector=sector, has_journal_position=has_pos, python=python)
        if cmd:
            out.append(cmd)
    return out


def _sizer_suggestion(sections: Dict, ticker: str, lang: str, skills_root) -> Dict:
    j = sections.get("gpw-trade-journal", {}).get("key_fields", {})
    w = sections.get("gpw-watchlist", {}).get("key_fields", {})
    tr = sections.get("gpw-trend-screener", {}).get("key_fields", {})
    ex = sections.get("gpw-exposure-coach", {}).get("key_fields", {})
    entry = stop = None
    ids: List[str] = []
    for src, e_key, s_key, pre in ((j, "entry", "stop", "journal"), (w, "pivot", "stop", "watchlist"),
                                   (tr, "pivot", "stop_structure", "trend")):
        if src.get(e_key) and src.get(s_key) and src[s_key] < src[e_key]:
            entry, stop = src[e_key], src[s_key]
            ids = ["{}.{}".format(pre, e_key), "{}.{}".format(pre, s_key)]
            break
    if ex.get("max_risk_per_trade_pct") is not None:
        risk, rsrc = ex["max_risk_per_trade_pct"], "gpw-exposure-coach"
        ids.append("exposure.max_risk_per_trade_pct")
    else:
        risk, rsrc = 1.0, "gpw-position-sizer default"
    argv = ["python3", "scripts/position_sizer.py", "--ticker", ticker]
    if entry is not None:
        argv += ["--entry", "{:g}".format(entry), "--stop", "{:g}".format(stop)]
    else:
        argv += ["--atr-multiplier", "2"]
    argv += ["--account-size", "ACCOUNT_PLN", "--risk-pct", "{:g}".format(risk), "--lang", lang]
    cmd = dr.display({"skill": "gpw-position-sizer", "argv": argv}, skills_root)
    return {"command": cmd, "entry": entry, "stop": stop, "risk_pct": risk, "risk_pct_source": rsrc, "fact_ids": ids}


def build(args) -> Dict:
    ticker = args.ticker
    in_dir = Path(args.input_dir).expanduser()
    if not in_dir.is_dir():
        raise reports.ReportError("input dir not found: {}".format(in_dir))
    ref = args.as_of or plcal.today_warsaw()
    skills_root = Path(args.skills_dir).expanduser() if args.skills_dir else None
    theses, open_pos, jdir = load_journal(args.state_dir, args.no_journal)
    watch_items, wpath, wwarn = load_watchlist(args.watchlist)
    warnings = [wwarn] if wwarn else []
    has_pos = any(de.norm_ticker(p.get("ticker")) == ticker for p in (open_pos or []))

    def collect_now():
        by = load_reports(args.input_dir, args.report, args.as_of)
        membership = None
        sector, ssrc = de.resolve_sector(ticker, by, None, args.sector)
        if ssrc == "none":
            try:
                membership = constituents.sector_membership(args.cache_dir, offline=True)
                sector, ssrc = de.resolve_sector(ticker, by, membership, None)
            except Exception:  # cache missing: sector stays unknown, said in the report
                pass
        col = de.collect(ticker, by, ref, args.lang, sector=sector, theses=theses, open_positions=open_pos,
                         watch_items=watch_items, watch_source=wpath, journal_source=jdir,
                         max_override=args.max_age_sessions)
        return col, sector, ssrc

    col, sector, ssrc = collect_now()
    runs: List[Dict] = []
    if args.run:
        stale = [s["skill"] for s in col["sections"].values() if s["stale"] and s["skill"] in dr.CLI]
        cmds = _gap_commands([g for g in col["gaps"] if g["skill"] in dr.CLI], stale, ticker, args, sector, has_pos,
                             python=sys.executable)
        cmds = [c for c in cmds if c["runnable"]]  # the rest is reported under not_applicable
        if args.only:
            only = {s.strip() for s in args.only.split(",") if s.strip()}
            cmds = [c for c in cmds if c["skill"] in only]
        order = {s: i for i, s in enumerate(dr.RUN_ORDER)}
        cmds.sort(key=lambda c: order.get(c["skill"], 99))
        runs = dr.run_commands(cmds, args.timeout, skills_root,
                               progress=lambda c: print("running: " + c, file=sys.stderr))
        col, sector, ssrc = collect_now()

    sections = col["sections"]
    missing, not_applicable = [], []
    for g in col["gaps"]:
        spec = de.SPEC_BY_SKILL[g["skill"]]
        cmd = dr.command_for(g["skill"], ticker, str(in_dir.resolve()), lang=args.lang, offline=args.offline,
                             as_of=args.as_of.isoformat() if args.as_of else None, sector=sector,
                             has_journal_position=has_pos)
        entry = {"skill": g["skill"], "reason": g["reason"], "optional": bool(spec.get("optional")),
                 "command": dr.display(cmd, skills_root) if cmd else None}
        if g.get("newest_file"):
            entry["newest_file"] = g["newest_file"]
        if cmd and not cmd["runnable"]:
            entry["reason"] = cmd["reason"]
            entry["command"] = None
            not_applicable.append(entry)
            continue
        if g["skill"] == "gpw-trade-journal":
            entry["command"] = "python3 scripts/gpw_research_dossier.py to-thesis <debate.json>"
        if g["skill"] == "gpw-watchlist":
            wl = dr.skill_dir("gpw-watchlist", skills_root)
            wl_state = getattr(args, "watchlist", None)
            state_flag = " --state-dir {}".format(Path(wl_state).expanduser().parent) if wl_state else ""
            entry["command"] = ("cd {} && python3 scripts/gpw_watchlist.py add {} --stage candidate --tag dossier --lang {}{}".format(
                wl, ticker, args.lang, state_flag) if (wl / "scripts" / "gpw_watchlist.py").is_file() else None)
            entry["hint"] = "gpw-watchlist (state {})".format(wl_state or "~/.gpw-trading-skills/watchlist.json")
        missing.append(entry)
    missing.sort(key=lambda m: (m["optional"], [s["skill"] for s in de.SPECS].index(m["skill"])))

    refresh = []
    for s in de.order_sections(sections):
        if s["stale"]:
            cmd = dr.command_for(s["skill"], ticker, str(in_dir.resolve()), lang=args.lang, offline=args.offline,
                                 sector=sector, has_journal_position=has_pos)
            refresh.append({"skill": s["skill"], "as_of": s["as_of"], "sessions_behind": s["sessions_behind"],
                            "max_sessions_behind": s["max_sessions_behind"],
                            "command": dr.display(cmd, skills_root) if cmd else None})

    prov = de.provenance_summary(sections)
    as_of = prov["as_of_max"] or ref.isoformat()
    dp = provenance.build(as_of=dt.date.fromisoformat(as_of[:10]),
                          sources=sorted({s["source_file"] for s in sections.values() if s.get("source_file")}),
                          sessions_used=len(sections), universe_size=1,
                          notes=["Sections are copies of other skills' reports; each keeps its own as_of.",
                                 "Stale = more sessions behind than the section's limit (references/methodology.md)."],
                          reference_date=args.as_of, max_sessions_behind=3)
    dp.update(prov)
    here = Path(__file__).resolve().parent.parent
    sizer = _sizer_suggestion(sections, ticker, args.lang, skills_root)
    gate_dir = dr.skill_dir("gpw-pre-trade-gate", skills_root)
    out = {
        "skill": SKILL, "version": VERSION, "lang": args.lang, "kind": "dossier", "ticker": ticker,
        "as_of": as_of, "reference_date": ref.isoformat(),
        "sector": {"key": sector, "source": ssrc},
        "sections": {s["skill"]: s for s in de.order_sections(sections)},
        "facts": col["facts"],
        "missing": missing, "not_applicable": not_applicable, "refresh": refresh,
        "stale_sections": prov["stale_sections"],
        "runs": runs,
        "debate": {"status": "not_started", "bear": [], "bull": [], "verdict_inputs": None},
        "next_steps": {
            "debate_template": "cd {} && python3 scripts/gpw_research_dossier.py debate-template {} --input-dir {} --output-dir {} --lang {}".format(
                here, ticker, Path(args.output_dir).expanduser().resolve(), Path(args.output_dir).expanduser().resolve(), args.lang),
            "position_sizer": sizer,
            "pre_trade_gate": "cd {} && python3 scripts/pre_trade_gate.py --sizer-json <gpw_position_size_*.json> --input-dir {} --lang {}".format(
                gate_dir, in_dir.resolve(), args.lang),
            "to_thesis": "cd {} && python3 scripts/gpw_research_dossier.py to-thesis <gpw_research_debate_{}_*.json>".format(here, ticker),
        },
        "errors": col["errors"], "warnings": warnings,
        "data_provenance": dp,
    }
    return out


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

def _val(v, lang):
    if isinstance(v, bool):
        return ("tak" if v else "nie") if lang == "pl" else ("yes" if v else "no")
    if isinstance(v, float):
        return num(v, 4 if 0 < abs(v) < 0.1 else 2 if abs(v) < 1000 else 0, lang)
    if isinstance(v, int):
        return num(v, 0, lang)
    return str(v).replace("|", "/")


def render_dossier(r: Dict, lang: str) -> str:
    t = T(STRINGS, lang)
    L = ["# " + t("title", t=r["ticker"]), ""]
    if r["refresh"]:
        L.append(t("stale_head"))
        for s in r["refresh"]:
            L.append(t("stale_row", s=s["skill"], a=s["as_of"], n=s["sessions_behind"], m=s["max_sessions_behind"],
                       c=s["command"] or "-"))
        L.append("")
    req = [m for m in r["missing"] if not m["optional"]]
    opt = [m for m in r["missing"] if m["optional"]]
    dp = r["data_provenance"]
    L.append(t("headline", t=r["ticker"], n=len(r["sections"]), f=len(r["facts"]), m=len(req),
               opt=t("opt", n=len(opt)) if opt else "", a0=dp.get("as_of_min") or "-", a1=dp.get("as_of_max") or "-"))
    L += ["", t("sector_line", s=r["sector"]["key"] or t("no_sector"), src=r["sector"]["source"]), ""]
    L += ["## " + t("sections"), "", t("th_sec"), "|---|---|---|---|"]
    for s in r["sections"].values():
        L.append("| {} | {} | {} | {} |".format(s["skill"], s["as_of"] or "-", t("stale") if s["stale"] else t("ok"),
                                                 (s["summary"] or "").replace("|", "/")))
    L += ["", "## " + t("facts"), "", t("th_fact"), "|---|---|"]
    for f in r["facts"]:
        L.append("| `{}` | {}{} |".format(f["id"], _val(f["value"], lang), " ⚠️" if f["stale"] else ""))
    if r["missing"]:
        L += ["", "## " + t("missing"), ""]
        for m in r["missing"]:
            L.append("- **{}**{}: {}".format(m["skill"], " (opcjonalna)" if m["optional"] and lang == "pl" else
                                            " (optional)" if m["optional"] else "", t("reason_" + m["reason"])))
            if m.get("command"):
                L += ["  ```", "  " + m["command"], "  ```"]
            elif m.get("hint"):
                L.append("  " + m["hint"])
    if r["not_applicable"]:
        L += ["", "## " + t("na"), ""]
        for m in r["not_applicable"]:
            L.append("- {}: {}".format(m["skill"], t("reason_" + m["reason"])))
    if r["runs"]:
        L += ["", "## " + t("runs"), "", "| skill | status | s | command |", "|---|---|---|---|"]
        for x in r["runs"]:
            L.append("| {} | {} | {} | `{}` |".format(x["skill"], x["status"], x["duration_s"] if x["duration_s"] is not None else "-",
                                                      x["command"]))
    if r["errors"]:
        L += ["", "## " + t("errors"), ""] + ["- {}: {} ({})".format(e["skill"], e["error"], e["file"]) for e in r["errors"]]
    ns = r["next_steps"]
    L += ["", "## " + t("next"), "", t("next_debate", c=ns["debate_template"]),
          t("next_sizer", r=num(ns["position_sizer"]["risk_pct"], 2, lang), src=ns["position_sizer"]["risk_pct_source"],
            c=ns["position_sizer"]["command"]),
          t("next_gate", c=ns["pre_trade_gate"]), t("next_thesis", c=ns["to_thesis"])]
    L += ["", "## " + t("sources"), ""] + (["- {}".format(s) for s in dp["sources"]] or ["- -"])
    L += ["", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


def render_template_md(tpl: Dict, dossier: Dict, lang: str) -> str:
    t = T(STRINGS, lang)
    pl = lang == "pl"
    L = ["# " + t("debate_title", t=tpl["ticker"]), ""] + ["- " + i for i in tpl["instructions"]] + [""]
    if tpl["unknowns"]:
        L += ["## " + ("Niewiadome z dossier" if pl else "Unknowns from the dossier"), ""]
        L += ["- " + u["text"] for u in tpl["unknowns"]] + [""]
    L += ["## " + ("Fakty do cytowania" if pl else "Facts to cite"), "", "| id | value |", "|---|---|"]
    for fid, v in tpl["facts_digest"].items():
        L.append("| `{}` | {} |".format(fid, _val(v, lang)))
    L += ["", "## 1. BEAR", "", "1. … [fact_ids]", "2. …", "3. …", "", "## 2. BULL", "", "1. … [fact_ids]", "2. …", "3. …",
          "", "## 3. Risk manager", "", "- " + ("wielkość: " if pl else "size: ") + "`{}`".format(
              tpl["risk_manager"]["position_sizing"]["command"]),
          "- kill criteria: …", "- " + ("co zmieni zdanie: …" if pl else "what would change the view: …"),
          "", "## 4. Pre-mortem", "", "- …", "", "## 5. Verdict inputs (" + ("nie rekomendacja" if pl else "not a recommendation") + ")",
          "", "- …", "", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


def render_validation(res: Dict, lang: str, embedded: bool) -> str:
    t = T(STRINGS, lang)
    st = res["stats"]
    L = []
    if embedded:
        L += [t("embedded"), ""]
    if res["valid"]:
        L.append(t("valid", b=st["bear_points"], u=st["bull_points"], c=st["citations"], d=st["distinct_facts_cited"],
                   s=len(st["sections_cited"])))
    else:
        L.append(t("invalid", n=len(res["errors"])))
        L += [""] + ["- `{}` {}{}".format(e["path"], e["code"], " ({})".format(e["fact_id"]) if e.get("fact_id") else "")
                     for e in res["errors"]]
    if res["warnings"]:
        L += ["", "Warnings:" if lang == "en" else "Ostrzeżenia:"]
        L += ["- `{}` {}{}".format(w["path"], w["code"], " ({})".format(w["fact_id"]) if w.get("fact_id") else "")
              for w in res["warnings"]]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# Debate helpers
# --------------------------------------------------------------------------

def find_dossier(ticker: Optional[str], explicit: Optional[str], input_dir: str, hint: Optional[str] = None) -> Optional[Dict]:
    if explicit:
        data = reports.load_report(explicit)
        if data.get("skill") != SKILL or data.get("kind") != "dossier":
            raise reports.ReportError("{} is not a gpw-research-dossier dossier".format(explicit))
        return {"path": str(Path(explicit).expanduser()), "data": data}
    if hint and Path(hint).expanduser().is_file():
        try:
            data = reports.load_report(hint)
            if data.get("kind") == "dossier":
                return {"path": hint, "data": data}
        except reports.ReportError:
            pass
    for rep in reports.iter_reports(input_dir, SKILL, newest_first=True):
        d = rep["data"]
        if d.get("kind") == "dossier" and (ticker is None or de.norm_ticker(d.get("ticker")) == ticker):
            return {"path": rep["path"], "data": d}
    return None


def load_debate(path: str) -> Dict:
    data = reports.load_report(path)
    if data.get("kind") != "debate":
        raise reports.ReportError("{} is not a debate file (kind != 'debate')".format(path))
    return data


def validate_file(args):
    deb = load_debate(args.file)
    dos = find_dossier(de.norm_ticker(deb.get("ticker")), args.dossier_json, args.input_dir, deb.get("dossier_file"))
    if dos:
        facts = dos["data"].get("facts") or []
        ids = {f["id"] for f in facts}
        stale = {f["id"] for f in facts if f.get("stale")}
        missing = [m for m in dos["data"].get("missing") or [] if not m.get("optional")]
        embedded = False
    else:
        ids = set(deb.get("available_fact_ids") or [])
        stale, missing, embedded = set(), [], True
    return deb, dos, dd.validate(deb, ids, stale, missing), embedded


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def _stamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _write(out_dir: str, name: str, obj: Dict, md: str) -> Path:
    d = Path(out_dir).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    jp = d / (name + ".json")
    jp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / (name + ".md")).write_text(md, encoding="utf-8")
    return jp


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if args.cmd == "build":
            r = build(args)
            md = render_dossier(r, args.lang)
            if not args.no_files:
                jp = _write(args.output_dir, "gpw_research_dossier_{}_{}".format(r["ticker"], _stamp()), r, md)
                print("JSON: {}".format(jp), file=sys.stderr)
            print(json.dumps(r, ensure_ascii=False, indent=2) if args.json else md)
            return 0
        if args.cmd == "debate-template":
            dos = find_dossier(args.ticker, args.dossier_json, args.input_dir)
            if dos is None:
                raise reports.ReportError("no gpw-research-dossier dossier for {} in {} - run `build {}` first".format(
                    args.ticker, args.input_dir, args.ticker))
            tpl = dd.make_template(dos["data"], dos["path"], args.lang)
            md = render_template_md(tpl, dos["data"], args.lang)
            if not args.no_files:
                jp = _write(args.output_dir, "gpw_research_debate_{}_{}".format(args.ticker, _stamp()), tpl, md)
                here = Path(__file__).resolve().parent.parent
                print(T(STRINGS, args.lang)("debate_saved", p=jp, c="cd {} && python3 scripts/gpw_research_dossier.py validate-debate {}".format(here, jp)),
                      file=sys.stderr)
            print(json.dumps(tpl, ensure_ascii=False, indent=2) if args.json else md)
            return 0
        if args.cmd == "validate-debate":
            deb, dos, res, embedded = validate_file(args)
            res["dossier_file"] = dos["path"] if dos else None
            print(json.dumps(res, ensure_ascii=False, indent=2) if args.json else render_validation(res, args.lang, embedded))
            return 0 if res["valid"] else 1
        if args.cmd == "to-thesis":
            deb, dos, res, embedded = validate_file(args)
            if not res["valid"]:
                sys.stderr.write(render_validation(res, args.lang, embedded))
                return 1
            skills_root = Path(args.skills_dir).expanduser() if args.skills_dir else None
            jdir = str(dr.skill_dir("gpw-trade-journal", skills_root))
            out = dd.thesis_command(deb, dos["data"] if dos else None, jdir, lang=args.lang, status=args.status)
            out["validation"] = res["stats"]
            if args.json:
                print(json.dumps(out, ensure_ascii=False, indent=2))
            else:
                print(T(STRINGS, args.lang)("thesis_head"))
                print("")
                print(out["command"])
                if out["notes"]:
                    print("\nnotes: " + ", ".join(out["notes"]))
            return 0
    except (reports.ReportError, ValueError, OSError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
