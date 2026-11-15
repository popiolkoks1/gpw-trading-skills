#!/usr/bin/env python3
"""GPW Sector Trend - CLI.

Computes the share of GPW stocks in a confirmed uptrend (overall and for each
of the 14 GPW Benchmark sector indices), scores the environment 0-100 and
writes JSON + Markdown reports (PL or EN).

    python3 scripts/gpw_sector_trend.py --lang pl
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import constituents, gpw_archive, panel, provenance  # noqa: E402
from gpwlib.constituents import SECTOR_INDICES  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct  # noqa: E402
from gpwlib.universe import DEFAULTS, build_views, session_dates  # noqa: E402

import sector_engine as se  # noqa: E402

STRINGS = {
    "title": {"pl": "Trendy sektorowe GPW — raport", "en": "GPW Sector Trend Report"},
    "as_of": {"pl": "Dane na sesję", "en": "Data as of session"},
    "generated": {"pl": "Wygenerowano", "en": "Generated"},
    "stale": {"pl": "⚠️ UWAGA: dane są nieaktualne o {n} sesji.", "en": "⚠️ WARNING: data is {n} session(s) old."},
    "summary": {"pl": "Podsumowanie", "en": "Summary"},
    "score": {"pl": "Wynik łączny", "en": "Composite score"},
    "zone": {"pl": "Strefa", "en": "Zone"},
    "exposure": {"pl": "Orientacyjna ekspozycja na akcje", "en": "Indicative equity exposure"},
    "ratio": {"pl": "Spółki w trendzie wzrostowym", "en": "Stocks in an uptrend"},
    "penalty": {"pl": "Kara za ostrzeżenia", "en": "Warning penalty"},
    "warnings": {"pl": "Ostrzeżenia", "en": "Warnings"},
    "w_late_cycle": {"pl": "Późny cykl: surowce (górnictwo, paliwa, chemia) mocniejsze niż spółki cykliczne i defensywne.",
                     "en": "Late cycle: commodities (mining, fuels, chemicals) stronger than both cyclicals and defensives."},
    "w_high_spread": {"pl": "Duży rozrzut między sektorami (>40 pkt proc.) — rynek bardzo selektywny.",
                      "en": "Wide spread between sectors (>40 pp) — very selective market."},
    "w_divergence": {"pl": "Rozbieżność wewnątrz grupy sektorów — średnie mogą maskować słabość.",
                     "en": "Divergence inside a sector group — averages may hide weakness."},
    "sectors": {"pl": "Sektory", "en": "Sectors"},
    "col_sector": {"pl": "Sektor", "en": "Sector"},
    "col_group": {"pl": "Grupa", "en": "Group"},
    "col_ratio": {"pl": "W trendzie", "en": "Uptrend"},
    "col_ma": {"pl": "Średnia 10", "en": "10-MA"},
    "col_trend": {"pl": "Kierunek", "en": "Direction"},
    "col_status": {"pl": "Status", "en": "Status"},
    "col_idx": {"pl": "Indeks 20 sesji", "en": "Index 20 sessions"},
    "col_vs": {"pl": "vs WIG", "en": "vs WIG"},
    "cyclical": {"pl": "cykliczne", "en": "cyclical"},
    "defensive": {"pl": "defensywne", "en": "defensive"},
    "commodity": {"pl": "surowcowe", "en": "commodity"},
    "up": {"pl": "↑ rośnie", "en": "↑ rising"},
    "down": {"pl": "↓ spada", "en": "↓ falling"},
    "overbought": {"pl": "wykupiony", "en": "overbought"},
    "oversold": {"pl": "wyprzedany", "en": "oversold"},
    "normal": {"pl": "normalny", "en": "normal"},
    "low_sample": {"pl": " (mała próba)", "en": " (small sample)"},
    "components": {"pl": "Składniki wyniku", "en": "Score components"},
    "col_comp": {"pl": "Składnik", "en": "Component"},
    "col_w": {"pl": "Waga", "en": "Weight"},
    "col_s": {"pl": "Wynik", "en": "Score"},
    "col_sig": {"pl": "Sygnał", "en": "Signal"},
    "market_breadth": {"pl": "Szerokość całego rynku", "en": "Overall breadth"},
    "sector_participation": {"pl": "Udział sektorów", "en": "Sector participation"},
    "sector_rotation": {"pl": "Rotacja cykliczne vs defensywne", "en": "Cyclical vs defensive rotation"},
    "momentum": {"pl": "Momentum", "en": "Momentum"},
    "historical_context": {"pl": "Kontekst historyczny", "en": "Historical context"},
    "rotation": {"pl": "Rotacja", "en": "Rotation"},
    "rot_txt": {"pl": "Cykliczne {c} · defensywne {d} · surowcowe {m} (różnica cykliczne − defensywne: {diff} pkt proc.)",
                "en": "Cyclical {c} · defensive {d} · commodity {m} (cyclical − defensive: {diff} pp)"},
    "zone_strong_bull": {"pl": "Silna hossa", "en": "Strong bull"},
    "zone_bull": {"pl": "Hossa", "en": "Bull"},
    "zone_neutral": {"pl": "Neutralna", "en": "Neutral"},
    "zone_cautious": {"pl": "Ostrożność", "en": "Cautious"},
    "zone_bear": {"pl": "Bessa", "en": "Bear"},
    "s1_overbought": {"pl": "dużo spółek w trendzie (powyżej 37%)", "en": "many stocks in uptrend (above 37%)"},
    "s1_normal": {"pl": "w normie", "en": "normal range"},
    "s1_oversold": {"pl": "bardzo mało spółek w trendzie (poniżej 9,7%)", "en": "very few stocks in uptrend (below 9.7%)"},
    "s2_broad": {"pl": "większość sektorów rośnie", "en": "most sectors rising"},
    "s2_mixed": {"pl": "mieszane", "en": "mixed"},
    "s2_narrow": {"pl": "rośnie mniejszość sektorów", "en": "minority of sectors rising"},
    "s3_risk_on": {"pl": "przewaga cyklicznych (apetyt na ryzyko)", "en": "cyclicals lead (risk-on)"},
    "s3_balanced": {"pl": "równowaga", "en": "balanced"},
    "s3_risk_off": {"pl": "przewaga defensywnych (ucieczka od ryzyka)", "en": "defensives lead (risk-off)"},
    "s4_improving": {"pl": "poprawa i przyspieszenie", "en": "improving and accelerating"},
    "s4_fading": {"pl": "poprawa, ale zwalnia", "en": "improving but fading"},
    "s4_worsening": {"pl": "pogorszenie", "en": "worsening"},
    "s5_high": {"pl": "wysoka wiarygodność", "en": "high confidence"},
    "s5_moderate": {"pl": "umiarkowana wiarygodność", "en": "moderate confidence"},
    "s5_low": {"pl": "niska wiarygodność (krótka historia)", "en": "low confidence (short history)"},
    "s5_very_low": {"pl": "bardzo niska wiarygodność", "en": "very low confidence"},
    "no_data": {"pl": "brak danych", "en": "no data"},
    "n/a": {"pl": "brak danych", "en": "no data"},
    "definition": {"pl": "Definicja trendu wzrostowego", "en": "Uptrend definition"},
    "definition_txt": {
        "pl": ("Spółka jest „w trendzie”, gdy jednocześnie: kurs > SMA20, kurs > SMA200, SMA50 > SMA200, kurs ≥ 130% "
               "najniższego kursu z ostatnich ~250 sesji i kurs wyższy niż 20 sesji temu. Uwzględniamy płynne akcje "
               "(cena ≥ {p} zł, mediana obrotu ≥ {t} tys. zł). Sektory = bieżący skład 14 indeksów sektorowych GPW Benchmark."),
        "en": ("A stock is \"in an uptrend\" when all hold: close > SMA20, close > SMA200, SMA50 > SMA200, close ≥ 130% of "
               "the lowest close of the last ~250 sessions, and close above its level 20 sessions ago. Liquid shares only "
               "(price ≥ {p} PLN, median turnover ≥ {t}k PLN). Sectors = current membership of the 14 GPW Benchmark sector indices."),
    },
    "sources": {"pl": "Źródła", "en": "Sources"},
}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="GPW sector uptrend analyzer (PL/EN)")
    p.add_argument("--lang", choices=["pl", "en"], default="pl")
    p.add_argument("--output-dir", default=".")
    p.add_argument("--cache-dir", default=None)
    p.add_argument("--sessions", type=int, default=500)
    p.add_argument("--as-of", default=None)
    p.add_argument("--offline", action="store_true")
    p.add_argument("--min-price", type=float, default=DEFAULTS["min_price"])
    p.add_argument("--min-turnover-k", type=float, default=DEFAULTS["min_turnover_k"])
    a = p.parse_args(argv)
    if a.sessions < 330:
        p.error("--sessions must be >= 330")
    return a


def run(args) -> dict:
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else None
    end = dt.date.fromisoformat(args.as_of) if args.as_of else None
    print("Loading GPW sessions (first run downloads ~2 years, a few minutes)...", file=sys.stderr)
    shares = gpw_archive.load_sessions(args.sessions, end=end, cache_dir=cache_dir, offline=args.offline)
    if len(shares) < 260:
        raise gpw_archive.GPWFetchError("Only {} sessions available; need at least 260.".format(len(shares)))
    indices = gpw_archive.load_sessions(80, end=end, type_id=gpw_archive.TYPE_INDICES, cache_dir=cache_dir,
                                        offline=args.offline, progress=False)
    sector_map = constituents.sector_membership(cache_dir, offline=args.offline)
    if not sector_map:
        raise gpw_archive.GPWFetchError("Sector membership unavailable (GPW Benchmark). Re-run online.")
    pnl = panel.build_panel(shares)
    views = build_views(pnl)
    cfg = dict(DEFAULTS, min_price=args.min_price, min_turnover_k=args.min_turnover_k)
    series = se.ratio_series(views, session_dates(pnl), cfg, sector_map)
    all_rows = [r for r in series["all"] if r["total"] >= 30]
    if len(all_rows) < 30:
        raise gpw_archive.GPWFetchError("Not enough uptrend-ratio history ({} sessions).".format(len(all_rows)))
    snap = se.sector_snapshot(series)
    idx_panel = panel.build_panel(indices, currency="", adjust=False)
    rets = se.index_returns(idx_panel, ["WIG"] + list(SECTOR_INDICES))
    for s in snap:
        s["index_20s_pct"] = rets.get(s["key"])
        s["vs_wig_pp"] = (rets[s["key"]] - rets["WIG"]) if rets.get(s["key"]) is not None and rets.get("WIG") is not None else None
    comps = {
        "market_breadth": se.c1_market_breadth(all_rows),
        "sector_participation": se.c2_participation(snap),
        "sector_rotation": se.c3_rotation(snap),
        "momentum": se.c4_momentum(all_rows, snap),
        "historical_context": se.c5_history(all_rows),
    }
    comp = se.composite(comps)
    as_of = dt.date.fromisoformat(all_rows[-1]["date"])
    prov = provenance.build(
        as_of=as_of,
        sources=["GPW — Archiwum notowań: https://www.gpw.pl/archiwum-notowan",
                 "GPW Benchmark — portfele indeksów sektorowych: https://gpwbenchmark.pl"],
        sessions_used=len(shares), universe_size=all_rows[-1]["total"],
        notes=["Sector membership is the current GPW Benchmark portfolio (not point-in-time)."],
        point_in_time_membership=False)
    return {
        "skill": "gpw-sector-trend", "version": "1.0.0", "lang": args.lang,
        "composite": comp, "components": comps, "sectors": snap,
        "wig_20s_pct": rets.get("WIG"),
        "overall_tail": [{k: (round(v, 5) if isinstance(v, float) else v) for k, v in r.items()} for r in all_rows[-30:]],
        "config": cfg, "data_provenance": prov,
    }


def render_markdown(r: dict, lang: str) -> str:
    t = T(STRINGS, lang)
    c, comps, prov = r["composite"], r["components"], r["data_provenance"]
    L = ["# " + t("title"), "", "**{}:** {} · **{}:** {}".format(t("as_of"), prov["as_of"], t("generated"), prov["retrieved_at_utc"]), ""]
    if prov["freshness"]["is_stale"]:
        L += [t("stale", n=prov["freshness"]["sessions_behind"]), ""]
    mb = comps["market_breadth"]["details"]
    L += ["## " + t("summary"), "", "| | |", "|---|---|",
          "| {} | **{} / 100** |".format(t("score"), num(c["score"], 1, lang)),
          "| {} | **{}** |".format(t("zone"), t("zone_" + c["zone"])),
          "| {} | {} |".format(t("exposure"), c["exposure"]),
          "| {} | {} ({} / {}) |".format(t("ratio"), pct(mb.get("ratio", 0) * 100, 1, lang), mb.get("count"), mb.get("total"))]
    if c["penalty"]:
        L.append("| {} | −{} |".format(t("penalty"), c["penalty"]))
    L.append("")
    if c["warnings"]:
        L += ["### " + t("warnings"), ""] + ["- " + t("w_" + w) for w in c["warnings"]] + [""]
    L += ["## " + t("sectors"), "",
          "| {} | {} | {} | {} | {} | {} | {} | {} |".format(t("col_sector"), t("col_group"), t("col_ratio"), t("col_ma"),
                                                           t("col_trend"), t("col_status"), t("col_idx"), t("col_vs")),
          "|---|---|---|---|---|---|---|---|"]
    for s in sorted(r["sectors"], key=lambda s: -s["ratio"]):
        label = s["label_pl"] if lang == "pl" else s["label_en"]
        L.append("| {}{} | {} | {} ({}/{}) | {} | {} | {} | {} | {} |".format(
            label, t("low_sample") if s["low_sample"] else "", t(s["group"]),
            pct(s["ratio"] * 100, 0, lang), s["count"], s["total"], pct(s["ma10"] * 100, 0, lang),
            t(s["trend"]), t(s["status"]), pct(s.get("index_20s_pct"), 1, lang, signed=True),
            (("+" if s["vs_wig_pp"] > 0 else "") + "{} pp".format(num(s["vs_wig_pp"], 1, lang)) if s.get("vs_wig_pp") is not None else "—")))
    L.append("")
    rot = comps["sector_rotation"]
    if rot["data_available"]:
        d = rot["details"]
        L += ["### " + t("rotation"), "", t("rot_txt", c=pct(d["cyclical_avg"] * 100, 0, lang), d=pct(d["defensive_avg"] * 100, 0, lang),
                                             m=pct((d["commodity_avg"] or 0) * 100, 0, lang),
                                             diff=num(d["cyclical_minus_defensive"] * 100, 1, lang)), ""]
    L += ["## " + t("components"), "", "| {} | {} | {} | {} |".format(t("col_comp"), t("col_w"), t("col_s"), t("col_sig")), "|---|---|---|---|"]
    for k, w in se.WEIGHTS.items():
        cc = comps[k]
        L.append("| {} | {} | {} | {} |".format(t(k), pct(w * 100, 0, lang),
                                               num(cc["score"], 0, lang) if cc["data_available"] else t("n/a"), t(cc["signal_key"])))
    L += ["", "## " + t("definition"), "", t("definition_txt", p=num(r["config"]["min_price"], 2, lang), t=num(r["config"]["min_turnover_k"], 0, lang)), ""]
    L += ["## " + t("sources"), ""] + ["- " + s for s in prov["sources"]] + ["", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


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
    jp, mp = out / "gpw_sector_trend_{}.json".format(stamp), out / "gpw_sector_trend_{}.md".format(stamp)
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(render_markdown(result, args.lang), encoding="utf-8")
    c = result["composite"]
    print("Composite: {} / 100 | zone: {} | exposure: {} | as of {}".format(c["score"], c["zone"], c["exposure"],
                                                                          result["data_provenance"]["as_of"]))
    print("JSON: {}\nMarkdown: {}".format(jp, mp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
