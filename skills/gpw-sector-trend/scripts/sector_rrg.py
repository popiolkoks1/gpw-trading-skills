"""GPW Sector Trend - Relative Rotation Graph mode (``--rrg``).

Data loading, report assembly and rendering for the RRG mode of
gpw_sector_trend.py. All maths lives in sector_rrg_engine.py (pure, tested).

    python3 scripts/gpw_sector_trend.py --rrg [--benchmark WIG] [--tail 8] [--svg rrg.svg]
    python3 scripts/gpw_sector_trend.py --rrg --stocks WIG-BANKI
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

from gpwlib import constituents, gpw_archive, panel, provenance
from gpwlib.constituents import SECTOR_INDICES, SIZE_INDICES
from gpwlib.fmt import DISCLAIMER, T, num
from gpwlib.indicators import median

import sector_rrg_engine as rr

SIZE_ITEMS = ["WIG20", "MWIG40", "SWIG80"]
SIZE_LABELS = {"WIG20": "WIG20", "MWIG40": "mWIG40", "SWIG80": "sWIG80"}
# WIG and the sector sub-indices are total-return ("dochodowe") indices, while
# WIG20 / mWIG40 / sWIG80 are price indices. Their published total-return
# variants are used so that every item and the benchmark treat dividends the
# same way (otherwise dividend season alone would push the size indices left).
TR_SERIES = {"WIG20": "WIG20TR", "MWIG40": "MWIG40TR", "SWIG80": "SWIG80TR"}
LIQ_WINDOW = 20  # sessions for the median-turnover liquidity filter (same as gpwlib.universe)

STRINGS = {
    "title": {"pl": "Rotacja sektorów GPW — wykres RRG", "en": "GPW Relative Rotation Graph"},
    "title_stocks": {"pl": "Rotacja spółek w sektorze {s} — wykres RRG", "en": "Relative Rotation Graph — stocks in {s}"},
    "as_of": {"pl": "Dane na tydzień kończący się sesją", "en": "Weekly data up to session"},
    "bench": {"pl": "Benchmark", "en": "Benchmark"},
    "generated": {"pl": "Wygenerowano", "en": "Generated"},
    "stale": {"pl": "⚠️ UWAGA: dane są nieaktualne o {n} sesji.", "en": "⚠️ WARNING: data is {n} session(s) old."},
    "partial": {"pl": "ℹ️ Ostatni tydzień jest niepełny (ostatnia sesja {d}); ostatni punkt może się jeszcze zmienić.",
                "en": "ℹ️ The latest week is incomplete (last session {d}); the latest point can still move."},
    "answer": {"pl": "Odpowiedź", "en": "Answer"},
    "in_q": {"pl": "{q}: {names}", "en": "{q}: {names}"},
    "none": {"pl": "brak", "en": "none"},
    "rot_summary": {"pl": "Podsumowanie rotacji", "en": "Rotation summary"},
    "sum_transition": {"pl": "{label}: przejście z „{f}” do „{t}” (tydzień do {d}){ccw}",
                       "en": "{label}: moved from {f} to {t} (week to {d}){ccw}"},
    "sum_approaching": {"pl": "{label}: zbliża się do przejścia z „{f}” do „{t}”",
                        "en": "{label}: approaching the move from {f} to {t}"},
    "ccw_note": {"pl": " — zawrócenie, przeciwnie do typowego obrotu", "en": " — a turn-back against the usual clockwise rotation"},
    "no_moves": {"pl": "Brak zmian ćwiartek w ostatnich 4 tygodniach.", "en": "No quadrant changes in the last 4 weeks."},
    "table": {"pl": "Tabela", "en": "Table"},
    "col_item": {"pl": "Pozycja", "en": "Item"},
    "col_code": {"pl": "Kod", "en": "Code"},
    "col_q": {"pl": "Ćwiartka", "en": "Quadrant"},
    "col_weeks": {"pl": "Tyg. w ćw.", "en": "Weeks in q."},
    "col_head": {"pl": "Kierunek", "en": "Heading"},
    "col_dist": {"pl": "Odległość", "en": "Distance"},
    "col_rot": {"pl": "Obrót", "en": "Rotation"},
    "col_tr": {"pl": "Zmiany (4 tyg.)", "en": "Changes (4 wk)"},
    "col_conc": {"pl": "Koncentracja", "en": "Concentration"},
    "conc_yes": {"pl": "⚠️ {top} ({w})", "en": "⚠️ {top} ({w})"},
    "conc_members": {"pl": "⚠️ {n} spółki", "en": "⚠️ {n} members"},
    "dominant": {"pl": "⚠️ {w} indeksu", "en": "⚠️ {w} of index"},
    "clockwise": {"pl": "↻ zgodnie", "en": "↻ clockwise"},
    "counterclockwise": {"pl": "↺ przeciwnie", "en": "↺ counter-clockwise"},
    "rot_none": {"pl": "—", "en": "—"},
    "q_leading": {"pl": "liderzy (leading)", "en": "leading"},
    "q_weakening": {"pl": "słabnące (weakening)", "en": "weakening"},
    "q_lagging": {"pl": "maruderzy (lagging)", "en": "lagging"},
    "q_improving": {"pl": "poprawa (improving)", "en": "improving"},
    "qa_leading": {"pl": "lid.", "en": "lead"},
    "qa_weakening": {"pl": "słab.", "en": "weak"},
    "qa_lagging": {"pl": "mar.", "en": "lag"},
    "qa_improving": {"pl": "popr.", "en": "impr"},
    "qs_leading": {"pl": "LIDERZY", "en": "LEADING"},
    "qs_weakening": {"pl": "SŁABNĄCE", "en": "WEAKENING"},
    "qs_lagging": {"pl": "MARUDERZY", "en": "LAGGING"},
    "qs_improving": {"pl": "POPRAWA", "en": "IMPROVING"},
    "chart": {"pl": "Wykres (oś X: RS-Ratio, oś Y: RS-Momentum, środek = 100)",
              "en": "Chart (x: RS-Ratio, y: RS-Momentum, centre = 100)"},
    "legend": {"pl": "Legenda", "en": "Legend"},
    "collision": {"pl": "„*” = kilka pozycji w jednym polu.", "en": "\"*\" = several items in one cell."},
    "svg_written": {"pl": "Wykres SVG z ogonami: {p}", "en": "SVG chart with tails: {p}"},
    "conc_title": {"pl": "Koncentracja", "en": "Concentration"},
    "conc_txt": {"pl": ("Indeksy oznaczone ⚠️ są zdominowane przez 1-2 spółki (największa ≥ 50% albo dwie największe ≥ 70% "
                        "wagi). Ich punkt na wykresie to w praktyce wykres tych spółek, a nie całego sektora."),
                 "en": ("Indices marked ⚠️ are dominated by 1-2 companies (largest ≥ 50% or top two ≥ 70% of the weight). "
                        "Their point is in practice a chart of those companies, not of a broad sector.")},
    "dom_txt": {"pl": ("Spółki oznaczone ⚠️ mają ≥ 50% wagi w indeksie sektora, więc ich siła względna wobec tego indeksu "
                       "jest bliska zeru z definicji — porównuj je raczej z WIG."),
                "en": ("Stocks marked ⚠️ hold ≥ 50% of the sector index, so their relative strength against it is close to "
                       "flat by construction — compare them with WIG instead.")},
    "excluded": {"pl": "Pominięte", "en": "Excluded"},
    "ex_illiquid": {"pl": "mała płynność", "en": "illiquid"},
    "ex_history": {"pl": "za krótka historia", "en": "history too short"},
    "ex_no_data": {"pl": "brak notowań", "en": "no quotes"},
    "howto": {"pl": "Jak czytać ten wykres", "en": "How to read this chart"},
    "howto_txt": {
        "pl": ("Każdy punkt to indeks (albo spółka) porównany z benchmarkiem. Oś pozioma (RS-Ratio) mówi, czy pozycja jest "
               "silniejsza (>100) czy słabsza (<100) od benchmarku; oś pionowa (RS-Momentum) — czy ta przewaga rośnie (>100) "
               "czy maleje (<100). **Liderzy**: silni i coraz silniejsi. **Słabnące**: wciąż silni, ale przewaga maleje. "
               "**Maruderzy**: słabi i coraz słabsi. **Poprawa**: wciąż słabi, ale nadrabiają. Typowo punkty krążą zgodnie "
               "z ruchem wskazówek zegara: poprawa → liderzy → słabnące → maruderzy. To tendencja, nie reguła — punkt może "
               "zawrócić w każdej ćwiartce. „Odległość” od środka to siła ruchu (w odchyleniach standardowych), „kierunek” "
               "to kąt ostatniego tygodniowego kroku (0° = w prawo, 90° = w górę; 0-90° = najlepszy kierunek)."),
        "en": ("Each point is an index (or a stock) compared with the benchmark. The horizontal axis (RS-Ratio) shows whether it "
               "is stronger (>100) or weaker (<100) than the benchmark; the vertical axis (RS-Momentum) whether that edge is "
               "growing (>100) or shrinking (<100). **Leading**: strong and getting stronger. **Weakening**: still strong, but "
               "the edge is fading. **Lagging**: weak and getting weaker. **Improving**: still weak, but catching up. Points "
               "usually rotate clockwise: improving → leading → weakening → lagging. It is a tendency, not a rule — a point can "
               "turn back in any quadrant. \"Distance\" from the centre is the strength of the move (in standard deviations), "
               "\"heading\" is the angle of the latest weekly step (0° = right, 90° = up; 0-90° is the strongest direction)."),
    },
    "method": {"pl": "Metoda", "en": "Method"},
    "method_txt": {
        "pl": ("Otwarte przybliżenie JdK RS-Ratio i RS-Momentum (oryginalna formuła J. de Kempenaera jest zastrzeżona). "
               "Dane tygodniowe (ostatnia sesja tygodnia). RS = 100 × cena / benchmark; RS-Ratio = 100 + z-score EMA({s}) z RS "
               "w oknie {n} tyg.; RS-Momentum = 100 + {r}-tyg. zmiana RS-Ratio (EMA({ms})) podzielona przez jej RMS z {m} tyg. "
               "(z-score wokół zera). Ogon: {t} tyg. "
               "Wartości nie są porównywalne 1:1 z wykresami StockCharts."),
        "en": ("Open approximation of JdK RS-Ratio and RS-Momentum (J. de Kempenaer's original formula is proprietary). "
               "Weekly data (last session of each week). RS = 100 × price / benchmark; RS-Ratio = 100 + z-score of EMA({s}) of RS "
               "over {n} weeks; RS-Momentum = 100 + the {r}-week change of RS-Ratio (EMA({ms})) divided by its RMS over {m} weeks "
               "(z-score around zero). Tail: {t} weeks. "
               "Values are not 1:1 comparable with StockCharts charts."),
    },
    "sources": {"pl": "Źródła", "en": "Sources"},
}

HEADING_ARROWS = ["→", "↗", "↑", "↖", "←", "↙", "↓", "↘"]


# ---------------------------------------------------------------- CLI arguments

def _sector_key(text: str) -> Optional[str]:
    t = text.strip().upper()
    for key, (_isin, en, pl, _g) in SECTOR_INDICES.items():
        if t in (key, key.replace("WIG-", ""), en.upper(), pl.upper()):
            return key
    return None


def add_arguments(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("RRG mode (Relative Rotation Graph)")
    g.add_argument("--rrg", action="store_true", help="Relative Rotation Graph of sector and size indices vs a benchmark")
    g.add_argument("--benchmark", default=None, help="benchmark index (default WIG; with --stocks: the sector index)")
    g.add_argument("--stocks", default=None, metavar="SECTOR",
                   help="stock mode: constituents of one sector index (e.g. WIG-BANKI, BANKI, banki) vs that index")
    g.add_argument("--tail", type=int, default=rr.TAIL_WEEKS, help="tail length in weeks (default 8)")
    g.add_argument("--rrg-smooth", type=int, default=rr.SMOOTH_WEEKS, help="EMA span of RS in weeks (default 10)")
    g.add_argument("--rrg-norm", type=int, default=rr.NORM_WEEKS, help="RS-Ratio normalisation window in weeks (default 52)")
    g.add_argument("--rrg-mom-norm", type=int, default=rr.MOM_NORM_WEEKS,
                   help="RS-Momentum normalisation window in weeks (default 26)")
    g.add_argument("--rrg-mom-smooth", type=int, default=rr.MOM_SMOOTH_WEEKS,
                   help="EMA span of the RS-Ratio rate of change in weeks (default 3; 1 = no smoothing)")
    g.add_argument("--svg", default=None, metavar="FILE", help="also write a standalone SVG chart with tails")
    p.add_argument("--no-files", action="store_true", help="do not write JSON/Markdown files; print the Markdown")


def validate_args(p: argparse.ArgumentParser, a) -> None:
    if not a.rrg:
        for flag, val in (("--stocks", a.stocks), ("--svg", a.svg), ("--benchmark", a.benchmark)):
            if val is not None:
                p.error("{} requires --rrg".format(flag))
        return
    if not 2 <= a.tail <= 26:
        p.error("--tail must be between 2 and 26 weeks")
    if a.rrg_smooth < 1 or a.rrg_mom_smooth < 1 or a.rrg_norm < 4 or a.rrg_mom_norm < 4:
        p.error("--rrg-smooth / --rrg-mom-smooth must be >= 1 and normalisation windows >= 4 weeks")
    if a.stocks is not None:
        key = _sector_key(a.stocks)
        if key is None:
            p.error("--stocks: unknown sector '{}'; choose one of {}".format(a.stocks, ", ".join(SECTOR_INDICES)))
        a.stocks = key
    if a.benchmark is not None:
        a.benchmark = a.benchmark.strip().upper()


# ---------------------------------------------------------------- data

def _label(item_id: str, lang: str) -> str:
    if item_id in SECTOR_INDICES:
        _isin, en, pl, _g = SECTOR_INDICES[item_id]
        return pl if lang == "pl" else en
    return SIZE_LABELS.get(item_id, item_id)


def _portfolio(isin: str, cache_dir, offline) -> List[Dict]:
    try:
        return constituents.fetch_portfolio(isin, cache_dir, offline).get("members", [])
    except gpw_archive.GPWFetchError:
        return []


def _index_isin(item_id: str) -> Optional[str]:
    if item_id in SECTOR_INDICES:
        return SECTOR_INDICES[item_id][0]
    return SIZE_INDICES.get(item_id)


def _windows(args) -> Dict:
    return {"smooth": args.rrg_smooth, "norm": args.rrg_norm, "mom_norm": args.rrg_mom_norm,
            "roc": rr.ROC_WEEKS, "tail": args.tail, "mom_smooth": args.rrg_mom_smooth}


def _stale_check(args):
    return dt.date.fromisoformat(args.as_of) if args.as_of else None


def run_rrg(args, version: str) -> Dict:
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else None
    end = _stale_check(args)
    win = _windows(args)
    print("Loading GPW index sessions...", file=sys.stderr)
    idx_sessions = gpw_archive.load_sessions(args.sessions, end=end, type_id=gpw_archive.TYPE_INDICES,
                                             cache_dir=cache_dir, offline=args.offline, progress=False)
    if not idx_sessions:
        raise gpw_archive.GPWFetchError("No index sessions available (run online once to fill the cache).")
    stock_mode = args.stocks is not None

    def iseries(name):
        return panel.index_series(idx_sessions, name)

    if stock_mode:
        bench_id = args.benchmark or args.stocks
    else:
        bench_id = args.benchmark or "WIG"
    bench_name = TR_SERIES.get(bench_id, bench_id)
    bs = iseries(bench_name)
    if len(bs) == 0:
        raise gpw_archive.GPWFetchError("Benchmark index '{}' not found in the GPW index archive.".format(bench_id))

    items: List[Dict] = []
    excluded: List[Dict] = []
    sessions_used = len(idx_sessions)
    if stock_mode:
        members = _portfolio(SECTOR_INDICES[args.stocks][0], cache_dir, args.offline)
        if not members:
            raise gpw_archive.GPWFetchError("Constituents of {} unavailable (GPW Benchmark). Re-run online.".format(args.stocks))
        print("Loading GPW share sessions...", file=sys.stderr)
        shares = gpw_archive.load_sessions(args.sessions, end=end, cache_dir=cache_dir, offline=args.offline, progress=True)
        sessions_used = len(shares)
        pnl = panel.build_panel(shares)
        weights = {m["name"]: m.get("weight_pct") for m in members}
        candidates = []
        for m in members:
            s = pnl.get(m["name"])
            if s is None or len(s) == 0:
                excluded.append({"id": m["name"], "reason": "no_data"})
                continue
            med = median(s.turnover_k[-LIQ_WINDOW:])
            if s.close[-1] < args.min_price or med is None or med < args.min_turnover_k:
                excluded.append({"id": m["name"], "reason": "illiquid", "median_turnover_k": med})
                continue
            candidates.append((m["name"], s))
    else:
        weights = {}
        candidates = []
        for item_id in list(SECTOR_INDICES) + SIZE_ITEMS:
            if item_id == bench_id:
                continue
            s = iseries(TR_SERIES.get(item_id, item_id))
            if len(s) == 0:
                excluded.append({"id": item_id, "reason": "no_data"})
                continue
            candidates.append((item_id, s))

    for item_id, s in candidates:
        wd, wp, wb = rr.weekly_pair(s.dates, s.close, bs.dates, bs.close)
        try:
            it = rr.compute_item(item_id, wd, wp, wb, **win)
        except rr.RRGError as exc:
            excluded.append({"id": item_id, "reason": "history", "detail": str(exc)})
            continue
        it["label"] = _label(item_id, args.lang) if not stock_mode else item_id
        it["series"] = TR_SERIES.get(item_id, item_id) if not stock_mode else item_id
        it["benchmark"] = bench_id
        if stock_mode:
            w = weights.get(item_id)
            it["weight_pct"] = w
            it["concentrated"] = bool(w is not None and w >= rr.DOMINANT_WEIGHT_PCT)
            it["concentration"] = {"basis": "weight_in_benchmark", "weight_pct": w}
        else:
            isin = _index_isin(item_id)
            mem = _portfolio(isin, cache_dir, args.offline) if isin else []
            conc = rr.concentration([m.get("weight_pct") for m in mem], len(mem) or None)
            top = sorted(mem, key=lambda m: -(m.get("weight_pct") or 0))[:2]
            conc["top_names"] = [m["name"] for m in top]
            it["concentrated"] = conc["concentrated"]
            it["concentration"] = conc
        items.append(it)
    if not items:
        need = rr.min_weeks(**win)
        raise gpw_archive.GPWFetchError(
            "No item has enough weekly history ({} weeks needed for these windows; {} sessions loaded). "
            "Load more sessions (--sessions, online) or shorten --rrg-norm / --rrg-mom-norm.".format(need, len(idx_sessions)))

    as_of_s = max(it["tail"][-1]["date"] for it in items)
    as_of = dt.date.fromisoformat(as_of_s)
    items.sort(key=lambda it: (rr.QUADRANTS.index(it["quadrant"]), -it["distance"]))
    summary = rr.rotation_summary(items)
    sigs = rr.signals(items, as_of_s, stock_mode=stock_mode)
    sources = ["GPW — Archiwum notowań (indeksy): https://www.gpw.pl/archiwum-notowan",
               "GPW Benchmark — portfele indeksów: https://gpwbenchmark.pl",
               "J. de Kempenaer, RRG Research — Relative Rotation Graphs: https://www.relativerotationgraphs.com",
               "StockCharts ChartSchool — RRG Charts: https://school.stockcharts.com/doku.php?id=chart_analysis:rrg_charts"]
    if stock_mode:
        sources.insert(1, "GPW — Archiwum notowań (akcje): https://www.gpw.pl/archiwum-notowan")
    notes = ["RS-Ratio / RS-Momentum are an open approximation of the proprietary JdK indicators (see references/methodology.md §7).",
             "Weekly series = last common session of each ISO week; holidays are handled by the session dates.",
             "Index constituents/weights are the current GPW Benchmark portfolios (not point-in-time)."]
    if not stock_mode:
        notes.append("WIG20/mWIG40/sWIG80 use their total-return variants (WIG20TR, MWIG40TR, SWIG80TR) to match total-return WIG.")
    prov = provenance.build(as_of=as_of, sources=sources, sessions_used=sessions_used, universe_size=len(items),
                            notes=notes, point_in_time_membership=False, reference_date=end)
    return {
        "skill": "gpw-sector-trend", "version": version, "lang": args.lang, "mode": "rrg",
        "as_of": as_of_s,
        "rrg": {
            "benchmark": bench_id,
            "benchmark_series": bench_name,
            "as_of": as_of_s,
            "mode": "stocks" if stock_mode else "indices",
            "sector": args.stocks,
            "partial_week": not rr.week_is_complete(as_of_s),
            "params": dict(win, transition_weeks=rr.TRANSITION_WEEKS, approximation="open JdK RS-Ratio/RS-Momentum"),
            "quadrant_counts": rr.quadrant_counts(items),
            "items": items,
            "rotation_summary": summary,
            "excluded": excluded,
        },
        "signals": sigs,
        "data_provenance": prov,
    }


# ---------------------------------------------------------------- rendering

def _codes(items: List[Dict]) -> Dict[str, str]:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return {it["id"]: alphabet[i % len(alphabet)] for i, it in enumerate(items)}


def _arrow(h: Optional[float]) -> str:
    if h is None:
        return "·"
    return HEADING_ARROWS[int(((h + 22.5) % 360) // 45)]


def summary_lines(r: Dict, lang: str) -> List[str]:
    t = T(STRINGS, lang)
    labels = {it["id"]: it["label"] for it in r["rrg"]["items"]}
    out = []
    for s in r["rrg"]["rotation_summary"]:
        f, to = t("q_" + s["from"]), t("q_" + s["to"])
        if s["kind"] == "transition":
            out.append(t("sum_transition", label=labels[s["id"]], f=f, t=to, d=s["date"],
                         ccw="" if s["clockwise"] else t("ccw_note")))
        else:
            out.append(t("sum_approaching", label=labels[s["id"]], f=f, t=to))
    return out


def render_markdown(r: Dict, lang: str, svg_path: Optional[str] = None) -> str:
    t = T(STRINGS, lang)
    g, prov = r["rrg"], r["data_provenance"]
    stock_mode = g["mode"] == "stocks"
    title = t("title_stocks", s=g["sector"]) if stock_mode else t("title")
    L = ["# " + title, "",
         "**{}:** {} · **{}:** {} · **{}:** {}".format(t("as_of"), g["as_of"], t("bench"), g["benchmark"],
                                                     t("generated"), prov["retrieved_at_utc"]), ""]
    if prov["freshness"]["is_stale"]:
        L += [t("stale", n=prov["freshness"]["sessions_behind"]), ""]
    if g["partial_week"]:
        L += [t("partial", d=g["as_of"]), ""]
    items = g["items"]
    L += ["## " + t("answer"), ""]
    for q in rr.QUADRANTS:
        names = [it["label"] for it in items if it["quadrant"] == q]
        L.append("- **{}** ({}): {}".format(t("q_" + q), len(names), ", ".join(names) if names else t("none")))
    L += ["", "### " + t("rot_summary"), ""]
    lines = summary_lines(r, lang)
    L += ["- " + x for x in lines] if lines else ["- " + t("no_moves")]
    L += ["", "## " + t("table"), ""]
    codes = _codes(items)
    L += ["| {} | {} | {} | RS-Ratio | RS-Momentum | {} | {} | {} | {} | {} | {} |".format(
        t("col_code"), t("col_item"), t("col_q"), t("col_head"), t("col_dist"), t("col_weeks"), t("col_rot"),
        t("col_tr"), t("col_conc")),
        "|---|---|---|---:|---:|---|---:|---:|---|---|---|"]
    for it in items:
        c = it.get("concentration") or {}
        if stock_mode:
            conc = t("dominant", w="{}%".format(num(c.get("weight_pct"), 1, lang))) if it["concentrated"] else "—"
        elif it["concentrated"]:
            if c.get("basis") == "weights":
                one = c["top1_pct"] >= rr.CONC_TOP1_PCT
                names = (c.get("top_names") or [])[:1 if one else 2]
                conc = t("conc_yes", top=" + ".join(names),
                         w="{}%".format(num(c["top1_pct"] if one else c["top2_pct"], 0, lang)))
            else:
                conc = t("conc_members", n=c.get("members"))
        else:
            conc = "—"
        trs = ", ".join("{}→{}".format(t("qa_" + x["from"]), t("qa_" + x["to"])) for x in it["transitions"]) or "—"
        rot = it["rotation"]["direction"]
        L.append("| {} | {} | {} | {} | {} | {} {} | {} | {} | {} | {} | {} |".format(
            codes[it["id"]], it["label"], t("q_" + it["quadrant"]), num(it["rs_ratio"], 2, lang),
            num(it["rs_momentum"], 2, lang), _arrow(it["heading_deg"]),
            "—" if it["heading_deg"] is None else "{}°".format(num(it["heading_deg"], 0, lang)),
            num(it["distance"], 2, lang), it["weeks_in_quadrant"],
            t(rot) if rot != "none" else t("rot_none"), trs, conc))
    L += ["", "## " + t("chart"), "", "```",
          rr.ascii_scatter(items, codes, quadrant_names={q: t("qs_" + q) for q in rr.QUADRANTS}), "```", "",
          "{}: ".format(t("legend")) + " · ".join("{} = {}".format(codes[it["id"]], it["label"]) for it in items)
          + ". " + t("collision"), ""]
    if svg_path:
        L += [t("svg_written", p=svg_path), ""]
    if any(it["concentrated"] for it in items):
        L += ["### " + t("conc_title"), "", t("dom_txt") if stock_mode else t("conc_txt"), ""]
    if g["excluded"]:
        reason = {"illiquid": t("ex_illiquid"), "history": t("ex_history"), "no_data": t("ex_no_data")}
        L += ["**{}:** ".format(t("excluded")) + ", ".join("{} ({})".format(x["id"], reason.get(x["reason"], x["reason"]))
                                                       for x in g["excluded"]), ""]
    p = g["params"]
    L += ["## " + t("howto"), "", t("howto_txt"), "",
          "## " + t("method"), "", t("method_txt", s=p["smooth"], n=p["norm"], r=p["roc"], ms=p["mom_smooth"], m=p["mom_norm"], t=p["tail"]), ""]
    L += ["## " + t("sources"), ""] + ["- " + s for s in prov["sources"]] + ["", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


def render_svg(r: Dict, lang: str) -> str:
    t = T(STRINGS, lang)
    g = r["rrg"]
    title = t("title_stocks", s=g["sector"]) if g["mode"] == "stocks" else t("title")
    sub = "{}: {} · {} · {} {}".format(t("bench"), g["benchmark"], g["as_of"],
                                       "tail" if lang == "en" else "ogon", g["params"]["tail"])
    return rr.svg_chart(g["items"], title, sub, quadrant_names={q: t("q_" + q) for q in rr.QUADRANTS})


def main_rrg(args, version: str) -> int:
    try:
        result = run_rrg(args, version)
    except (gpw_archive.GPWFetchError, rr.RRGError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    svg_path = None
    if args.svg:
        sp = Path(args.svg).expanduser()
        try:
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text(render_svg(result, args.lang), encoding="utf-8")
        except OSError as exc:
            print("ERROR: cannot write SVG {}: {}".format(sp, exc), file=sys.stderr)
            return 1
        svg_path = str(sp)
        result["rrg"]["svg_path"] = svg_path
    md = render_markdown(result, args.lang, svg_path)
    if args.no_files:
        print(md)
        return 0
    out = Path(args.output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    # Own prefix: consumers of gpw_sector_trend_*.json (exposure-coach, signal-postmortem)
    # must keep finding the uptrend-ratio report.
    jp, mp = out / "gpw_sector_rrg_{}.json".format(stamp), out / "gpw_sector_rrg_{}.md".format(stamp)
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(md, encoding="utf-8")
    c = result["rrg"]["quadrant_counts"]
    print("RRG vs {} as of {} | leading {} · weakening {} · lagging {} · improving {}".format(
        result["rrg"]["benchmark"], result["rrg"]["as_of"], c["leading"], c["weakening"], c["lagging"], c["improving"]))
    for line in summary_lines(result, args.lang):
        print("  - " + line)
    print("JSON: {}\nMarkdown: {}".format(jp, mp))
    if svg_path:
        print("SVG: {}".format(svg_path))
    return 0
