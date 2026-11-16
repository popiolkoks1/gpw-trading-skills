#!/usr/bin/env python3
"""GPW Market Breadth - CLI.

Downloads (and caches) daily quotes of every share on the Warsaw Stock
Exchange from the official GPW archive, computes market breadth and a 0-100
composite health score, and writes JSON + Markdown reports (PL or EN).

    python3 scripts/gpw_breadth.py --lang pl
    python3 scripts/gpw_breadth.py --lang en --output-dir reports/ --sessions 500
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import constituents, gpw_archive, panel, provenance  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct  # noqa: E402
from gpwlib.universe import DEFAULTS, build_views, session_dates  # noqa: E402

import breadth_engine as be  # noqa: E402

STRINGS = {
    "title": {"pl": "Szerokość rynku GPW — raport", "en": "GPW Market Breadth Report"},
    "as_of": {"pl": "Dane na sesję", "en": "Data as of session"},
    "generated": {"pl": "Wygenerowano", "en": "Generated"},
    "stale": {
        "pl": "⚠️ UWAGA: dane są nieaktualne o {n} sesji. Wnioski mogą nie odpowiadać obecnej sytuacji.",
        "en": "⚠️ WARNING: data is {n} session(s) old. Conclusions may not reflect the current market.",
    },
    "summary": {"pl": "Podsumowanie", "en": "Summary"},
    "score": {"pl": "Wynik łączny", "en": "Composite score"},
    "zone": {"pl": "Strefa", "en": "Zone"},
    "exposure": {"pl": "Orientacyjna ekspozycja na akcje", "en": "Indicative equity exposure"},
    "quality": {"pl": "Kompletność danych", "en": "Data quality"},
    "strongest": {"pl": "Najmocniejszy element", "en": "Strongest component"},
    "weakest": {"pl": "Najsłabszy element", "en": "Weakest component"},
    "trend": {"pl": "Zmiana vs poprzedni pomiar", "en": "Change vs previous reading"},
    "key_numbers": {"pl": "Kluczowe liczby", "en": "Key numbers"},
    "universe": {"pl": "Spółki w analizie (płynne, ≥200 sesji historii)", "en": "Stocks analysed (liquid, ≥200 sessions history)"},
    "a200": {"pl": "% spółek powyżej średniej 200-sesyjnej", "en": "% of stocks above 200-session MA"},
    "a50": {"pl": "% spółek powyżej średniej 50-sesyjnej", "en": "% of stocks above 50-session MA"},
    "a20": {"pl": "% spółek powyżej średniej 20-sesyjnej", "en": "% of stocks above 20-session MA"},
    "fast": {"pl": "Szerokość — linia szybka (EMA 8)", "en": "Breadth — fast line (EMA 8)"},
    "slow": {"pl": "Szerokość — linia wolna (EMA 100)", "en": "Breadth — slow line (EMA 100)"},
    "adv": {"pl": "Rosnące / spadające (ostatnia sesja)", "en": "Advancers / decliners (last session)"},
    "mcc": {"pl": "Oscylator McClellana", "en": "McClellan oscillator"},
    "nhnl": {"pl": "Nowe maksima / minima (10 sesji)", "en": "New highs / lows (10 sessions)"},
    "pctile": {"pl": "Percentyl linii szybkiej w dostępnej historii", "en": "Fast-line percentile in available history"},
    "components": {"pl": "Składniki wyniku", "en": "Score components"},
    "col_comp": {"pl": "Składnik", "en": "Component"},
    "col_w": {"pl": "Waga", "en": "Weight"},
    "col_s": {"pl": "Wynik", "en": "Score"},
    "col_sig": {"pl": "Sygnał", "en": "Signal"},
    "n/a": {"pl": "brak danych", "en": "no data"},
    "size": {"pl": "Udział według wielkości spółek (% powyżej SMA200)", "en": "Participation by size (% above SMA200)"},
    "div": {"pl": "WIG vs szerokość rynku", "en": "WIG vs breadth"},
    "div_row": {
        "pl": "{w} sesji: WIG {idx}, linia szybka {b} pkt proc. → {p}",
        "en": "{w} sessions: WIG {idx}, fast line {b} pp → {p}",
    },
    "early_warning": {
        "pl": "⚠️ Wczesne ostrzeżenie: w ostatnich 20 sesjach indeks rośnie, a coraz mniej spółek uczestniczy we wzrostach.",
        "en": "⚠️ Early warning: over the last 20 sessions the index rose while fewer stocks participated.",
    },
    "pink": {
        "pl": "🟥 „Różowa strefa”: wolna linia spada, a szybka jest pod nią — historycznie najbardziej ryzykowny układ.",
        "en": "🟥 \"Pink zone\": the slow line is falling and the fast line is below it — historically the riskiest set-up.",
    },
    "caution": {
        "pl": "⚠️ Ostrożnie: szerokość krótkoterminowo słabnie — wskazówki dla strefy mogą być zbyt optymistyczne.",
        "en": "⚠️ Caution: short-term breadth is deteriorating — zone guidance may be too optimistic.",
    },
    "guidance": {"pl": "Co to oznacza w praktyce", "en": "What it means in practice"},
    "adjust": {"pl": "Wykryte korekty (dywidendy, splity) w ostatnich 60 sesjach", "en": "Detected adjustments (dividends, splits) in the last 60 sessions"},
    "method": {"pl": "Metodologia i ograniczenia", "en": "Methodology and limitations"},
    "method_txt": {
        "pl": (
            "- Uniwersum: wszystkie akcje notowane w PLN na Głównym Rynku GPW danego dnia, z ceną ≥ {p} zł i medianą obrotu z 20 sesji ≥ {t} tys. zł. Spółki wycofane są uwzględniane, dopóki były notowane (brak błędu przeżycia).\n"
            "- Ceny korygowane o dywidendy i splity na podstawie zmiany % publikowanej przez GPW względem kursu odniesienia.\n"
            "- Udział wg wielkości korzysta z *bieżącego* składu WIG20/mWIG40/sWIG80 (GPW Benchmark).\n"
            "- Linia wolna to EMA 100 (w wersji amerykańskiej EMA 200) ze względu na krótszą historię."
        ),
        "en": (
            "- Universe: every PLN-quoted share on the GPW Main Market that day with price ≥ {p} PLN and 20-session median turnover ≥ {t}k PLN. Delisted companies count while they traded (survivorship-free).\n"
            "- Prices adjusted for dividends and splits using the % change GPW publishes against the reference price.\n"
            "- Size participation uses the *current* WIG20/mWIG40/sWIG80 membership (GPW Benchmark).\n"
            "- The slow line is an EMA 100 (EMA 200 in the US original) because the archive history is shorter."
        ),
    },
    "sources": {"pl": "Źródła", "en": "Sources"},
    # component names
    "breadth_level_trend": {"pl": "Poziom i trend szerokości", "en": "Breadth level & trend"},
    "ma_crossover": {"pl": "Linia szybka vs wolna", "en": "Fast vs slow line"},
    "advance_decline": {"pl": "Rosnące/spadające (McClellan)", "en": "Advance/decline (McClellan)"},
    "new_highs_lows": {"pl": "Nowe maksima vs minima", "en": "New highs vs lows"},
    "size_participation": {"pl": "Udział duże/średnie/małe spółki", "en": "Large/mid/small participation"},
    "index_divergence": {"pl": "Dywergencja WIG vs szerokość", "en": "WIG vs breadth divergence"},
    # zones
    "zone_strong": {"pl": "Silna", "en": "Strong"},
    "zone_healthy": {"pl": "Zdrowa", "en": "Healthy"},
    "zone_neutral": {"pl": "Neutralna", "en": "Neutral"},
    "zone_weakening": {"pl": "Słabnąca", "en": "Weakening"},
    "zone_critical": {"pl": "Krytyczna", "en": "Critical"},
    "q_complete": {"pl": "pełna (6/6)", "en": "complete (6/6)"},
    "q_partial": {"pl": "częściowa — interpretuj ostrożnie", "en": "partial — interpret with caution"},
    "q_limited": {"pl": "ograniczona — niska wiarygodność", "en": "limited — low confidence"},
    "g_strong": {
        "pl": "Szeroki udział spółek we wzrostach. Otoczenie sprzyja strategiom wzrostowym i momentum; standardowe zarządzanie ryzykiem.",
        "en": "Broad participation. Environment favours growth/momentum; standard risk management.",
    },
    "g_healthy": {
        "pl": "Normalne warunki. Można działać według planu, pilnując stop-lossów.",
        "en": "Normal conditions. Operate according to plan, keep stops in place.",
    },
    "g_neutral": {
        "pl": "Rynek mieszany. Selektywnie, mniejsze nowe pozycje, ciaśniejsze stop-lossy, unikanie spekulacyjnych spółek.",
        "en": "Mixed market. Be selective, smaller new positions, tighter stops, avoid speculative names.",
    },
    "g_weakening": {
        "pl": "Coraz mniej spółek rośnie. Rozważ realizację zysków, podniesienie gotówki i ograniczenie nowych zakupów.",
        "en": "Fewer stocks participating. Consider taking profits, raising cash and limiting new buys.",
    },
    "g_critical": {
        "pl": "Szeroka słabość rynku. Priorytet: ochrona kapitału. Obserwuj sygnały dołka zamiast łapać spadające noże.",
        "en": "Broad weakness. Priority: capital preservation. Watch for a trough rather than catching falling knives.",
    },
    # signals
    "c1_up": {"pl": "wolna linia rośnie", "en": "slow line rising"},
    "c1_down": {"pl": "wolna linia spada", "en": "slow line falling"},
    "c1_flat": {"pl": "wolna linia płaska", "en": "slow line flat"},
    "c2_above": {"pl": "szybka nad wolną", "en": "fast above slow"},
    "c2_below": {"pl": "szybka pod wolną", "en": "fast below slow"},
    "c3_pos": {"pl": "przewaga wzrostów", "en": "advances dominate"},
    "c3_neg": {"pl": "przewaga spadków", "en": "declines dominate"},
    "c4_highs": {"pl": "więcej nowych maksimów", "en": "more new highs"},
    "c4_lows": {"pl": "więcej nowych minimów", "en": "more new lows"},
    "c4_quiet": {"pl": "mało nowych ekstremów", "en": "few new extremes"},
    "c5_broad": {"pl": "szeroki udział", "en": "broad participation"},
    "c5_weak": {"pl": "słaby udział", "en": "weak participation"},
    "c5_narrow": {"pl": "wąsko — prowadzą tylko duże spółki", "en": "narrow — only large caps lead"},
    "c6_healthy": {"pl": "zgodnie w górę", "en": "aligned upward"},
    "c6_bearish_divergence": {"pl": "negatywna dywergencja", "en": "bearish divergence"},
    "c6_bullish_divergence": {"pl": "pozytywna dywergencja", "en": "bullish divergence"},
    "c6_consistent_decline": {"pl": "zgodnie w dół", "en": "consistent decline"},
    "c6_flat": {"pl": "bez wyraźnego ruchu", "en": "no clear movement"},
    "no_data": {"pl": "brak danych", "en": "no data"},
    "flat": {"pl": "bez wyraźnego ruchu", "en": "no clear movement"},
    "healthy": {"pl": "zgodnie w górę", "en": "aligned upward"},
    "bearish_divergence": {"pl": "negatywna dywergencja", "en": "bearish divergence"},
    "bullish_divergence": {"pl": "pozytywna dywergencja", "en": "bullish divergence"},
    "consistent_decline": {"pl": "zgodnie w dół", "en": "consistent decline"},
}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="GPW market breadth analyzer (PL/EN)")
    p.add_argument("--lang", choices=["pl", "en"], default="pl")
    p.add_argument("--output-dir", default=".")
    p.add_argument("--state-dir", default=os.environ.get("GPW_SKILLS_STATE", str(Path.home() / ".gpw-trading-skills")))
    p.add_argument("--cache-dir", default=None, help="default ~/.cache/gpw-trading-skills or $GPW_SKILLS_CACHE")
    p.add_argument("--sessions", type=int, default=500, help="sessions of history to load (min 320)")
    p.add_argument("--as-of", default=None, help="analyse up to this date YYYY-MM-DD (default: latest)")
    p.add_argument("--offline", action="store_true", help="use cached data only, no network")
    p.add_argument("--min-price", type=float, default=DEFAULTS["min_price"])
    p.add_argument("--min-turnover-k", type=float, default=DEFAULTS["min_turnover_k"])
    p.add_argument("--index", default="WIG", help="index used for divergence (WIG, WIG20, MWIG40, SWIG80)")
    args = p.parse_args(argv)
    if args.sessions < 320:
        p.error("--sessions must be >= 320 (200 for SMA200 + 120 for breadth history)")
    return args


def update_history(state_dir: Path, as_of: str, comp: dict) -> dict:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "breadth_history.json"
    hist = []
    if path.exists():
        try:
            hist = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            hist = []
    prev = [h for h in hist if h["as_of"] < as_of]
    hist = [h for h in hist if h["as_of"] != as_of]
    hist.append({"as_of": as_of, "score": comp["score"], "zone": comp["zone"]})
    hist = sorted(hist, key=lambda h: h["as_of"])[-60:]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(hist, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    last_prev = prev[-1] if prev else None
    return {
        "previous": last_prev,
        "delta": round(comp["score"] - last_prev["score"], 1) if last_prev else None,
    }


def render_markdown(r: dict, lang: str) -> str:
    t = T(STRINGS, lang)
    c, comp, last, prov = r["composite"], r["components"], r["latest"], r["data_provenance"]
    L = ["# " + t("title"), ""]
    L.append("**{}:** {} · **{}:** {}".format(t("as_of"), prov["as_of"], t("generated"), prov["retrieved_at_utc"]))
    L.append("")
    if prov["freshness"]["is_stale"]:
        L += [t("stale", n=prov["freshness"]["sessions_behind"]), ""]
    L += ["## " + t("summary"), ""]
    L.append("| | |\n|---|---|")
    L.append("| {} | **{} / 100** |".format(t("score"), num(c["score"], 1, lang)))
    L.append("| {} | **{}** |".format(t("zone"), t("zone_" + c["zone"])))
    L.append("| {} | {} |".format(t("exposure"), c["exposure"]))
    L.append("| {} | {} |".format(t("quality"), t("q_" + c["data_quality"])))
    if c["strongest"]:
        L.append("| {} | {} |".format(t("strongest"), t(c["strongest"])))
        L.append("| {} | {} |".format(t("weakest"), t(c["weakest"])))
    if r["history"]["delta"] is not None:
        L.append("| {} | {} ({}) |".format(t("trend"), num(r["history"]["delta"], 1, lang), r["history"]["previous"]["as_of"]))
    L.append("")
    for flag, key in ((c["caution"], "caution"), (comp["ma_crossover"]["details"].get("pink_zone"), "pink"),
                      (comp["index_divergence"]["details"].get("early_warning"), "early_warning")):
        if flag:
            L += [t(key), ""]
    L += ["### " + t("guidance"), "", t("g_" + c["zone"]), ""]

    L += ["## " + t("key_numbers"), "", "| | |", "|---|---|"]
    L.append("| {} | {} |".format(t("universe"), last["eligible"]))
    L.append("| {} | {} |".format(t("a200"), pct(last["pct_above_200"] * 100, 1, lang)))
    L.append("| {} | {} |".format(t("a50"), pct(last["pct_above_50"] * 100, 1, lang)))
    L.append("| {} | {} |".format(t("a20"), pct(last["pct_above_20"] * 100, 1, lang)))
    L.append("| {} | {} |".format(t("fast"), pct(last["breadth_fast"] * 100, 1, lang)))
    L.append("| {} | {} |".format(t("slow"), pct(last["breadth_slow"] * 100, 1, lang)))
    L.append("| {} | {} / {} |".format(t("adv"), last["advancers"], last["decliners"]))
    L.append("| {} | {} |".format(t("mcc"), num(last["mcclellan"], 1, lang)))
    nh = comp["new_highs_lows"]["details"]
    if comp["new_highs_lows"]["data_available"]:
        L.append("| {} | {} / {} |".format(t("nhnl"), nh["new_highs_10d"], nh["new_lows_10d"]))
    L.append("| {} | {} |".format(t("pctile"), pct(r["history_context"]["fast_percentile"], 0, lang)))
    L.append("")

    L += ["## " + t("components"), "", "| {} | {} | {} | {} |".format(t("col_comp"), t("col_w"), t("col_s"), t("col_sig")), "|---|---|---|---|"]
    for k, w in be.WEIGHTS.items():
        cc = comp[k]
        s = num(cc["score"], 0, lang) if cc["data_available"] else t("n/a")
        L.append("| {} | {} | {} | {} |".format(t(k), pct(w * 100, 0, lang), s, t(cc["signal_key"])))
    L.append("")

    sp = comp["size_participation"]
    if sp["data_available"]:
        L += ["### " + t("size"), ""]
        for g, v in sp["details"]["pct_above_200_by_index"].items():
            L.append("- {}: {}".format(g.replace("MWIG", "mWIG").replace("SWIG", "sWIG"), pct(v * 100, 0, lang)))
        L.append("")
    dv = comp["index_divergence"]
    if dv["data_available"]:
        L += ["### " + t("div"), ""]
        for w in (dv["details"]["w20"], dv["details"]["w60"]):
            if w:
                L.append("- " + t("div_row", w=w["window"], idx=pct(w["index_pct"], 1, lang, signed=True),
                                   b=num(w["breadth_change"] * 100, 1, lang), p=t(w["pattern"])))
        L.append("")
    if r["recent_adjustments"]:
        L += ["### " + t("adjust"), ""]
        for a in r["recent_adjustments"][:15]:
            L.append("- {} {} × {}".format(a["date"], a["name"], num(a["factor"], 4, lang)))
        L.append("")
    L += ["## " + t("method"), "", t("method_txt", p=num(r["config"]["min_price"], 2, lang), t=num(r["config"]["min_turnover_k"], 0, lang)), ""]
    L += ["## " + t("sources"), ""]
    for s in prov["sources"]:
        L.append("- " + s)
    L += ["", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


def run(args) -> dict:
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else None
    end = dt.date.fromisoformat(args.as_of) if args.as_of else None
    print("Loading GPW share sessions (first run downloads ~2 years, a few minutes)...", file=sys.stderr)
    shares = gpw_archive.load_sessions(args.sessions, end=end, cache_dir=cache_dir, offline=args.offline)
    if len(shares) < 260:
        raise gpw_archive.GPWFetchError(
            "Only {} sessions available; need at least 260. Re-run without --offline or check network.".format(len(shares))
        )
    # divergence needs 60 sessions of index history; 130 leaves margin for holidays/gaps
    indices = gpw_archive.load_sessions(130, end=end, type_id=gpw_archive.TYPE_INDICES,
                                        cache_dir=cache_dir, offline=args.offline, progress=False)
    pnl = panel.build_panel(shares)
    views = build_views(pnl)
    dates = session_dates(pnl)
    notes = []
    try:
        size_map = constituents.size_membership(cache_dir, offline=args.offline)
    except gpw_archive.GPWFetchError as exc:
        size_map = {}
        notes.append("Index membership unavailable: {}".format(exc))
    cfg = dict(DEFAULTS, min_price=args.min_price, min_turnover_k=args.min_turnover_k)
    total_sessions = len(dates)
    nhnl_lb = be.NHNL_LOOKBACK_FULL if total_sessions >= cfg["min_history"] + be.NHNL_LOOKBACK_FULL // 2 else be.NHNL_LOOKBACK_MIN
    rows = be.daily_breadth(views, dates, cfg, size_map, nhnl_lb)
    # first SLOW_SPAN rows are warm-up for the EMA: drop rows before the series is meaningful
    rows = [r for r in rows if r["eligible"] >= 30]
    if len(rows) < 60:
        raise gpw_archive.GPWFetchError("Not enough breadth history ({} sessions).".format(len(rows)))
    idx_series = panel.index_series(indices, args.index.upper())
    comps = {
        "breadth_level_trend": be.c1_level_trend(rows),
        "ma_crossover": be.c2_crossover(rows),
        "advance_decline": be.c3_advance_decline(rows),
        "new_highs_lows": be.c4_new_highs_lows(rows, nhnl_lb),
        "size_participation": be.c5_size_participation(rows),
        "index_divergence": be.c6_divergence(idx_series if len(idx_series) else None, rows),
    }
    comp = be.composite(comps)
    as_of = dt.date.fromisoformat(rows[-1]["date"])
    cutoff = rows[max(0, len(rows) - 60)]["date"]
    adjustments = sorted(
        ({"name": n, **a} for n, s in pnl.items() for a in s.adjustments if a["date"] >= cutoff),
        key=lambda a: a["date"], reverse=True,
    )
    sources = [
        "GPW — Archiwum notowań (akcje, indeksy): https://www.gpw.pl/archiwum-notowan",
        "GPW Benchmark — portfele indeksów WIG20/mWIG40/sWIG80: https://gpwbenchmark.pl",
    ]
    prov = provenance.build(as_of=as_of, sources=sources, sessions_used=len(shares),
                            universe_size=rows[-1]["eligible"], notes=notes,
                            point_in_time_membership=False, reference_date=end)
    hist = update_history(Path(args.state_dir).expanduser(), as_of.isoformat(), comp) if not args.as_of else {"previous": None, "delta": None}
    last = {k: v for k, v in rows[-1].items() if k != "size"}
    return {
        "skill": "gpw-market-breadth",
        "version": "1.0.0",
        "lang": args.lang,
        "composite": comp,
        "components": comps,
        "latest": last,
        "history_context": be.history_context(rows),
        "history": hist,
        "recent_adjustments": adjustments,
        "series_tail": [
            {"date": r["date"], "pct_above_200": round(r["pct_above_200"], 4), "fast": round(r["breadth_fast"], 4),
             "slow": round(r["breadth_slow"], 4), "mcclellan": round(r["mcclellan"], 1)}
            for r in rows[-60:]
        ],
        "config": cfg,
        "data_provenance": prov,
    }


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except gpw_archive.GPWFetchError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    out = Path(args.output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    jp, mp = out / "gpw_breadth_{}.json".format(stamp), out / "gpw_breadth_{}.md".format(stamp)
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(render_markdown(result, args.lang), encoding="utf-8")
    c = result["composite"]
    print("Composite: {} / 100 | zone: {} | exposure: {} | as of {}".format(
        c["score"], c["zone"], c["exposure"], result["data_provenance"]["as_of"]))
    if result["data_provenance"]["freshness"]["is_stale"]:
        print("WARNING: data is stale ({} sessions behind)".format(result["data_provenance"]["freshness"]["sessions_behind"]))
    print("JSON: {}\nMarkdown: {}".format(jp, mp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
