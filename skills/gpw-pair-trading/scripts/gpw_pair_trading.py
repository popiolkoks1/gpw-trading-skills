#!/usr/bin/env python3
"""GPW Pair Trading (relative value) - CLI.

Modes
-----
scan    pairs inside GPW sector indices (``--sector banks,fuels``) and/or
        explicit ``--pair A,B``; Engle-Granger cointegration with a
        multiple-testing correction, half-life, z-score, stability.
pair    full analysis of one pair: statistics, spread z-score series,
        rolling hedge-ratio drift, illustrative backtests, hedge legs.
switch  for a holder of A: is B relatively cheap against their history?

    python3 scripts/gpw_pair_trading.py scan --sector banks --offline
    python3 scripts/gpw_pair_trading.py pair PKOBP PEKAO --lang en
    python3 scripts/gpw_pair_trading.py switch PKOBP PEKAO

Never says "buy X, sell Y": the output is context for a human decision.
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import sys
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import brokers, constituents, gpw_archive, panel, provenance, stats  # noqa: E402
from gpwlib.constituents import SECTOR_INDICES  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct, pln  # noqa: E402

import pair_engine as pe  # noqa: E402

VERSION = "1.0.0"
SKILL = "gpw-pair-trading"

STRINGS = {
    "title_scan": {"pl": "Pary GPW — skan wartości względnej", "en": "GPW pairs — relative-value scan"},
    "title_pair": {"pl": "Para {a} / {b} — analiza wartości względnej", "en": "Pair {a} / {b} — relative-value analysis"},
    "title_switch": {"pl": "Zamiana {a} → {b}? Wycena względna", "en": "Switch {a} → {b}? Relative valuation"},
    "as_of": {"pl": "Dane na sesję", "en": "Data as of session"},
    "generated": {"pl": "Wygenerowano", "en": "Generated"},
    "stale": {"pl": "⚠️ UWAGA: dane są nieaktualne o {n} sesji.", "en": "⚠️ WARNING: data is {n} session(s) old."},
    "answer": {"pl": "Odpowiedź", "en": "Answer"},
    "scan_summary": {
        "pl": "Przetestowano {m} par (po filtrach płynności i historii). Kointegracja (korekta {corr}, próg {alpha}): **{k}**. Pary z |z| ≥ {entry} wśród nich: **{d}**.",
        "en": "{m} pairs tested (after liquidity and history filters). Cointegrated (correction {corr}, threshold {alpha}): **{k}**. Of those with |z| ≥ {entry}: **{d}**."},
    "scan_none": {
        "pl": "Żadna para nie przeszła testu kointegracji (z wymaganą korelacją stóp zwrotu). To częsty i uczciwy wynik: wysoka korelacja spółek z jednego sektora nie oznacza stabilnej relacji cen.",
        "en": "No pair passed the cointegration test (with the required return correlation). This is a common, honest result: high correlation between sector peers does not mean a stable price relationship."},
    "corr_bh": {"pl": "Benjamini-Hochberg (FDR)", "en": "Benjamini-Hochberg (FDR)"},
    "corr_bonferroni": {"pl": "Bonferroniego", "en": "Bonferroni"},
    "corr_none": {"pl": "— bez korekty (surowe p)", "en": "— no correction (raw p)"},
    "why_mt_none": {"pl": "⚠️ Bez korekty: testując {m} par na poziomie 5% spodziewamy się ok. {fp} „fałszywych” par z samego przypadku. Użyj domyślnego --correction bh.",
                    "en": "⚠️ No correction: testing {m} pairs at 5% we expect about {fp} false pairs by chance alone. Use the default --correction bh."},
    "pairs": {"pl": "Pary", "en": "Pairs"},
    "col_pair": {"pl": "Para A / B", "en": "Pair A / B"},
    "col_corr": {"pl": "Korelacja stóp", "en": "Return corr."},
    "col_beta": {"pl": "β", "en": "β"},
    "col_p": {"pl": "p (EG)", "en": "p (EG)"},
    "col_padj": {"pl": "p skoryg.", "en": "adj. p"},
    "col_pass": {"pl": "Kointegr.", "en": "Coint."},
    "col_hl": {"pl": "Półokres [sesje]", "en": "Half-life [sessions]"},
    "col_z": {"pl": "z", "en": "z"},
    "col_state": {"pl": "Stan", "en": "State"},
    "col_stab": {"pl": "Stabilność", "en": "Stability"},
    "col_liq": {"pl": "Płynność", "en": "Liquidity"},
    "yes": {"pl": "tak", "en": "yes"},
    "no": {"pl": "nie", "en": "no"},
    "st_a_rich": {"pl": "A droga vs B", "en": "A rich vs B"},
    "st_a_cheap": {"pl": "A tania vs B", "en": "A cheap vs B"},
    "st_neutral": {"pl": "w normie", "en": "normal"},
    "st_beyond_stop": {"pl": "poza stopem (|z|≥{s})", "en": "beyond stop (|z|≥{s})"},
    "st_unknown": {"pl": "brak danych", "en": "no data"},
    "skipped": {"pl": "Pominięte pary", "en": "Skipped pairs"},
    "skipped_txt": {"pl": "{n} par odrzuconych przed testem: płynność ({liq}), za krótka historia ({hist}), brak notowań ({miss}).",
                    "en": "{n} pairs rejected before testing: liquidity ({liq}), short history ({hist}), no quotes ({miss})."},
    "why_mt": {"pl": "Dlaczego korekta? Testując {m} par na poziomie 5% spodziewamy się ok. {fp} „fałszywych” par z samego przypadku. Korekta {corr} kontroluje ten błąd.",
               "en": "Why the correction? Testing {m} pairs at 5% we expect about {fp} false pairs by chance alone. The {corr} correction controls that error."},
    "verdict_pair_pass": {"pl": "Para **{a}/{b}** jest skointegrowana (p = {p}) — spread historycznie wracał do średniej (półokres ~{hl} sesji).",
                          "en": "Pair **{a}/{b}** is cointegrated (p = {p}) — the spread has historically reverted to its mean (half-life ~{hl} sessions)."},
    "verdict_pair_fail": {"pl": "Para **{a}/{b}** NIE przechodzi testu kointegracji (p = {p} ≥ {alpha}) — z-score opisuje tylko względną drożyznę wobec ostatnich {L} sesji, bez statystycznej podstawy, że wróci.",
                          "en": "Pair **{a}/{b}** does NOT pass the cointegration test (p = {p} ≥ {alpha}) — the z-score only describes relative richness vs the last {L} sessions, with no statistical basis that it will revert."},
    "verdict_corr": {"pl": " Korelacja dziennych stóp zwrotu {c} jest poniżej progu {m}.", "en": " Daily return correlation {c} is below the {m} threshold."},
    "z_now": {"pl": "Obecnie z = **{z}** ({state}); {pctl}. percentyl z ostatnich {L} sesji.",
              "en": "Now z = **{z}** ({state}); percentile {pctl} of the last {L} sessions."},
    "stats": {"pl": "Statystyki", "en": "Statistics"},
    "k_window": {"pl": "Okno testu", "en": "Test window"},
    "k_beta": {"pl": "Współczynnik zabezpieczenia β (ln A na ln B)", "en": "Hedge ratio β (ln A on ln B)"},
    "k_beta_txt": {"pl": "na 10 000 zł w {a} odpowiada {v} w {b}", "en": "10,000 PLN of {a} corresponds to {v} of {b}"},
    "k_adf": {"pl": "Statystyka ADF reszt (A~B / B~A); wartość krytyczna 5%", "en": "ADF stat of residuals (A~B / B~A); 5% critical value"},
    "k_p": {"pl": "p-value Engle-Granger (gorszy z dwóch kierunków)", "en": "Engle-Granger p-value (worse of both directions)"},
    "k_hl": {"pl": "Półokres powrotu do średniej", "en": "Mean-reversion half-life"},
    "k_hurst": {"pl": "Wykładnik Hursta spreadu (<0,5 = powracanie)", "en": "Spread Hurst exponent (<0.5 = mean reverting)"},
    "k_corr": {"pl": "Korelacja dziennych stóp zwrotu / poziomów cen (log)", "en": "Correlation of daily returns / log price levels"},
    "k_stab": {"pl": "Stabilność: odsetek rocznych okien skointegrowanych", "en": "Stability: share of 1-year windows cointegrated"},
    "k_drift": {"pl": "Dryf β: ostatni rok vs całe okno (min–max w oknach)", "en": "β drift: last year vs full window (min–max across windows)"},
    "k_rel": {"pl": "Wynik A względem B: 20 / 60 / 250 sesji", "en": "A vs B relative return: 20 / 60 / 250 sessions"},
    "k_liq": {"pl": "Mediana obrotu ({w} sesji): A / B (wymagane {need})", "en": "Median turnover ({w} sessions): A / B (needed {need})"},
    "sessions_u": {"pl": "sesji", "en": "sessions"},
    "windows_u": {"pl": "okien", "en": "windows"},
    "na_hl": {"pl": "brak (spread nie wraca)", "en": "none (no mean reversion)"},
    "chart": {"pl": "Z-score spreadu (ostatnie {n} sesji; wiersze = poziom z, linie „-” = pasma ±2)", "en": "Spread z-score (last {n} sessions; rows = z level, '-' rows = ±2 bands)"},
    "bands": {"pl": "Pasma: wejście ±{e}, wyjście {x}, stop ±{s} (Gatev i in. 2006: 2σ).", "en": "Bands: entry ±{e}, exit {x}, stop ±{s} (Gatev et al. 2006: 2σ)."},
    "bt": {"pl": "Test historyczny (ilustracyjny)", "en": "Historical test (illustrative)"},
    "bt_note": {"pl": "β szacowane tylko na części in-sample ({is_s} → {is_e}); out-of-sample {oos_s} → {oos_e}. Pozycja {v} na nogę, prowizje {broker}. Koszt pożyczki akcji do krótkiej sprzedaży założony: {borrow}% rocznie. Mała liczba transakcji — to ilustracja, nie dowód przewagi.",
               "en": "β estimated on the in-sample part only ({is_s} → {is_e}); out-of-sample {oos_s} → {oos_e}. Position {v} per leg, commissions {broker}. Assumed stock-borrow cost for the short leg: {borrow}% a year. Few trades — an illustration, not proof of an edge."},
    "col_rule": {"pl": "Reguła", "en": "Rule"},
    "col_period": {"pl": "Okres", "en": "Period"},
    "col_trades": {"pl": "Transakcje / zamiany", "en": "Trades / switches"},
    "col_net": {"pl": "Wynik netto", "en": "Net result"},
    "col_costs": {"pl": "Koszty", "en": "Costs"},
    "col_extra": {"pl": "Uwagi", "en": "Notes"},
    "rule_spread": {"pl": "spread (long/short)", "en": "spread (long/short)"},
    "rule_switch": {"pl": "zamiana long-only", "en": "long-only switch"},
    "is": {"pl": "in-sample", "en": "in-sample"},
    "oos": {"pl": "out-of-sample", "en": "out-of-sample"},
    "spread_extra": {"pl": "trafność {w}, stopy {s}, śr. {d} sesji", "en": "win rate {w}, stops {s}, avg {d} sessions"},
    "switch_extra": {"pl": "trzymanie A {a}, B {b}, 50/50 {m}; vs A {vs} pp", "en": "hold A {a}, B {b}, 50/50 {m}; vs A {vs} pp"},
    "hedge": {"pl": "Realizacja i zabezpieczenie", "en": "Implementation and hedging"},
    "fut_yes": {"pl": "{t}: kontrakty terminowe na akcje dostępne ({sym}; obrót ostatniej sesji {to} tys. zł).",
                "en": "{t}: single-stock futures listed ({sym}; last-session turnover {to}k PLN)."},
    "fut_no": {"pl": "{t}: brak kontraktów terminowych na akcje w archiwum GPW.", "en": "{t}: no single-stock futures in the GPW archive."},
    "fut_unknown": {"pl": "{t}: nie sprawdzono kontraktów (brak danych typu 35 w pamięci podręcznej; uruchom bez --offline).",
                    "en": "{t}: futures not checked (no type-35 data cached; run without --offline)."},
    "beta_w20": {"pl": "Beta względem WIG20 (250 sesji): {a} {ba}, {b} {bb}. Zabezpieczenie indeksowe (FW20) liczy `gpw-risk-lab --mode hedge`.",
                 "en": "Beta vs WIG20 (250 sessions): {a} {ba}, {b} {bb}. Index hedge sizing (FW20): `gpw-risk-lab --mode hedge`."},
    "short_note": {"pl": "Krótka sprzedaż pojedynczych akcji na GPW jest dla inwestora indywidualnego trudna (pożyczka akcji przez dom maklerski, ograniczona lista spółek, zabezpieczenie, koszt pożyczki). Realne zastosowania: (1) decyzja o zamianie w sektorze, (2) noga zabezpieczająca przez kontrakty na akcje lub WIG20, (3) wychwycenie anomalii. Kontrakty nie są dostępne w IKE/IKZE.",
                   "en": "Short-selling single stocks on GPW is hard for retail investors (stock borrow via the broker, limited list of names, collateral, borrow fee). Realistic uses: (1) a switch decision within a sector, (2) a hedge leg via single-stock or WIG20 futures, (3) spotting dislocations. Futures are not allowed in IKE/IKZE."},
    "sw_verdict_b_relatively_cheap": {"pl": "**{b} jest względnie tani wobec {a}** na tle historii pary (z = {z}), a relacja jest skointegrowana. To kontekst do rozważenia zamiany, nie rekomendacja.",
                                      "en": "**{b} is relatively cheap vs {a}** against the pair's history (z = {z}) and the relationship is cointegrated. Context for considering a switch, not a recommendation."},
    "sw_verdict_b_cheap_unstable": {"pl": "{b} wygląda na względnie tani wobec {a} (z = {z}), **ale relacja nie jest stabilna statystycznie** — „taniość” może się pogłębiać.",
                                    "en": "{b} looks relatively cheap vs {a} (z = {z}), **but the relationship is not statistically stable** — the gap may keep widening."},
    "sw_verdict_extreme_deviation": {"pl": "Odchylenie skrajne (z = {z}, |z| ≥ {s}): relacja {a}/{b} mogła się zmienić (zdarzenie spółki, dywidenda, zmiana wyceny). Sprawdź przyczynę przed jakąkolwiek decyzją.",
                                   "en": "Extreme deviation (z = {z}, |z| ≥ {s}): the {a}/{b} relationship may have changed (corporate event, dividend, re-rating). Check the cause before any decision."},
    "sw_verdict_b_relatively_expensive": {"pl": "{b} jest względnie **drogi** wobec {a} (z = {z}) — z perspektywy posiadacza {a} brak argumentu cenowego za zamianą.",
                                          "en": "{b} is relatively **expensive** vs {a} (z = {z}) — for a holder of {a} there is no price argument for a switch."},
    "sw_verdict_no_dislocation": {"pl": "Brak wyraźnego odchylenia (z = {z}, pasmo ±{e}): ceny {a} i {b} są względem siebie w normie.",
                                  "en": "No clear dislocation (z = {z}, band ±{e}): {a} and {b} are priced normally relative to each other."},
    "sw_verdict_insufficient_data": {"pl": "Za mało danych do oceny.", "en": "Not enough data for a verdict."},
    "sw_cost": {"pl": "Koszt zamiany {v} dziś: prowizje ok. {c} ({broker}). Na zwykłym rachunku sprzedaż {a} z zyskiem oznacza 19% podatku Belki (PIT-38); w IKE/IKZE nie.",
                "en": "Cost of switching {v} today: commissions about {c} ({broker}). In a regular account selling {a} at a gain triggers the 19% Belka tax (PIT-38); not inside IKE/IKZE."},
    "signals": {"pl": "Sygnały (dla gpw-signal-postmortem)", "en": "Signals (for gpw-signal-postmortem)"},
    "sig_line": {"pl": "`pair_dislocation` — {t} (tańsza noga pary {p}), od {d}, z = {z}", "en": "`pair_dislocation` — {t} (cheap leg of {p}), since {d}, z = {z}"},
    "sig_none": {"pl": "Brak — żadna skointegrowana para nie ma |z| ≥ {e}.", "en": "None — no cointegrated pair has |z| ≥ {e}."},
    "notes": {"pl": "Uwagi", "en": "Notes"},
    "sources": {"pl": "Źródła", "en": "Sources"},
    "n_pairs_shown": {"pl": "Pokazano {k} z {m} par (najpierw skointegrowane, potem wg p).", "en": "Showing {k} of {m} pairs (cointegrated first, then by p)."},
}

SOURCES = [
    "GPW — Archiwum notowań (akcje, indeksy, kontrakty): https://www.gpw.pl/archiwum-notowan",
    "GPW Benchmark — portfele indeksów sektorowych: https://gpwbenchmark.pl",
    "Engle & Granger (1987), Econometrica 55(2): https://doi.org/10.2307/1913236",
    "MacKinnon (2010), QED Working Paper 1227: https://www.econ.queensu.ca/sites/econ.queensu.ca/files/qed_wp_1227.pdf",
    "Gatev, Goetzmann & Rouwenhorst (2006), RFS 19(3): https://doi.org/10.1093/rfs/hhj020",
    "Benjamini & Hochberg (1995), JRSS B 57(1): https://doi.org/10.1111/j.2517-6161.1995.tb02031.x",
]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.strip().lower())
    return "".join(c for c in s if not unicodedata.combining(c)).replace("ł", "l").replace(" ", "-")


SECTOR_ALIASES: Dict[str, str] = {}
for _key, (_isin, _en, _pl, _grp) in SECTOR_INDICES.items():
    for _alias in (_key, _key.replace("WIG-", ""), _en, _pl):
        SECTOR_ALIASES[_norm(_alias)] = _key
SECTOR_ALIASES.update({_norm(k): v for k, v in {
    "banks": "WIG-BANKI", "fuels": "WIG-PALIWA", "oil": "WIG-PALIWA", "energy": "WIG-ENERG",
    "utilities": "WIG-ENERG", "energetyka": "WIG-ENERG", "games": "WIG-GRY", "gaming": "WIG-GRY",
    "it": "WIG-INFO", "construction": "WIG-BUDOW", "mining": "WIG-GORNIC", "chemicals": "WIG-CHEMIA",
    "food": "WIG-SPOZYW", "pharma": "WIG-LEKI", "apparel": "WIG-ODZIEZ", "automotive": "WIG-MOTO",
    "real-estate": "WIG-NRCHOM", "nieruchomosci": "WIG-NRCHOM", "media": "WIG-MEDIA"}.items()})


def resolve_sector(name: str) -> Optional[str]:
    return SECTOR_ALIASES.get(_norm(name))


# --------------------------------------------------------------------------
# Arguments
# --------------------------------------------------------------------------


def _pos_float(v):
    x = float(v)
    if not x > 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return x


def _nonneg_float(v):
    x = float(v)
    if x < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return x


def _date(v):
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    D = pe.DEFAULTS
    common.add_argument("--lang", choices=["pl", "en"], default="pl")
    common.add_argument("--output-dir", default=".")
    common.add_argument("--no-files", action="store_true", help="print Markdown only, write no files")
    common.add_argument("--cache-dir", default=None)
    common.add_argument("--offline", action="store_true")
    common.add_argument("--as-of", type=_date, default=None)
    common.add_argument("--sessions", type=int, default=750, help="sessions to load (default 750, >= 300)")
    common.add_argument("--lookback", type=int, default=D["lookback"], help="cointegration window (sessions)")
    common.add_argument("--z-lookback", type=int, default=D["z_lookback"])
    common.add_argument("--entry-z", type=_pos_float, default=D["entry_z"])
    common.add_argument("--exit-z", type=_nonneg_float, default=D["exit_z"])
    common.add_argument("--stop-z", type=_pos_float, default=D["stop_z"])
    common.add_argument("--min-corr", type=float, default=D["min_corr"])
    common.add_argument("--alpha", type=float, default=D["alpha"])
    common.add_argument("--position-value", type=_pos_float, default=D["position_value"], help="PLN per leg")
    common.add_argument("--max-adv-pct", type=_pos_float, default=D["max_adv_pct"])
    common.add_argument("--broker", default="mbank", choices=sorted(brokers.BROKERS))
    common.add_argument("--commission-pct", type=_nonneg_float, default=None)
    common.add_argument("--commission-min", type=_nonneg_float, default=None)
    common.add_argument("--borrow-pct", type=_nonneg_float, default=0.0,
                        help="assumed annual stock-borrow fee for the short leg in the spread backtest (default 0)")

    p = argparse.ArgumentParser(description="GPW pair / relative-value analysis (PL/EN)")
    sub = p.add_subparsers(dest="mode", required=True)
    sc = sub.add_parser("scan", parents=[common], help="scan pairs inside sectors")
    sc.add_argument("--sector", default=None, help="comma list, e.g. banks,fuels,energy (default: all 14 sectors)")
    sc.add_argument("--cross", action="store_true", help="also pair names across the listed sectors")
    sc.add_argument("--pair", action="append", default=[], help="extra pair A,B (repeatable)")
    sc.add_argument("--correction", choices=["bh", "bonferroni", "none"], default="bh")
    sc.add_argument("--top", type=int, default=15, help="pairs shown in Markdown (default 15)")
    for name in ("pair", "switch"):
        sp = sub.add_parser(name, parents=[common], help="analyse one pair" if name == "pair" else "holder of A: is B cheap?")
        sp.add_argument("a")
        sp.add_argument("b")
    return p


def parse_args(argv=None):
    p = build_parser()
    a = p.parse_args(argv)
    if a.sessions < 300:
        p.error("--sessions must be >= 300")
    if a.lookback < 250:
        p.error("--lookback must be >= 250 (one year of sessions)")
    if not 20 <= a.z_lookback <= 250:
        p.error("--z-lookback must be between 20 and 250")
    if not (a.exit_z < a.entry_z < a.stop_z):
        p.error("bands must satisfy exit-z < entry-z < stop-z")
    if not 0 < a.alpha < 0.5:
        p.error("--alpha must be in (0, 0.5)")
    if not 0 <= a.min_corr <= 1:
        p.error("--min-corr must be between 0 and 1")
    if a.mode in ("pair", "switch"):
        a.a, a.b = a.a.strip().upper(), a.b.strip().upper()
        if a.a == a.b:
            p.error("the two tickers must differ")
    if a.mode == "scan":
        a.sectors = []
        if a.sector:
            for s in a.sector.split(","):
                key = resolve_sector(s)
                if not key:
                    p.error("unknown sector {!r}; use e.g. banks, fuels, energy, games, it, or a WIG-xxx key".format(s))
                if key not in a.sectors:
                    a.sectors.append(key)
        pairs = []
        for item in a.pair:
            parts = [x.strip().upper() for x in item.split(",") if x.strip()]
            if len(parts) != 2 or parts[0] == parts[1]:
                p.error("--pair expects two different tickers: A,B")
            pairs.append(tuple(parts))
        a.pairs = pairs
        if a.top < 1:
            p.error("--top must be >= 1")
    return a


def cfg_from(args) -> Dict:
    return dict(pe.DEFAULTS, lookback=args.lookback, z_lookback=args.z_lookback, entry_z=args.entry_z,
                exit_z=args.exit_z, stop_z=args.stop_z, min_corr=args.min_corr, alpha=args.alpha,
                position_value=args.position_value, max_adv_pct=args.max_adv_pct)


class DataError(Exception):
    pass


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------


def load_data(args):
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else None
    sess = gpw_archive.load_sessions(args.sessions, end=args.as_of, cache_dir=cache_dir, offline=args.offline,
                                     progress=True)
    if len(sess) < 260:
        raise DataError("Only {} share sessions available; need at least 260 (run without --offline).".format(len(sess)))
    pnl = panel.build_panel(sess)
    return cache_dir, sess, pnl


def raw_close_lookup(sessions):
    by_date = {s["date"]: s for s in sessions}
    cache: Dict[str, Dict[str, float]] = {}

    def get(name: str, date: str) -> Optional[float]:
        if date not in cache:
            s = by_date.get(date)
            cache[date] = {r["name"]: r.get("close") for r in (s["rows"] if s else [])}
        v = cache[date].get(name)
        if v is None:  # renamed ticker: the archive row carries the old name
            for old in pe_renames(name):
                v = cache[date].get(old)
                if v is not None:
                    break
        return v
    return get


def pe_renames(name: str) -> List[str]:
    return [o for o, _d in panel.RENAMES.get(name, [])]


def load_futures(args, cache_dir, as_of: str):
    try:
        fs = gpw_archive.load_sessions(1, end=dt.date.fromisoformat(as_of), type_id=gpw_archive.TYPE_FUTURES,
                                       cache_dir=cache_dir, offline=args.offline, progress=False)
    except gpw_archive.GPWFetchError:
        return None, None
    if not fs:
        return None, None
    return fs[-1]["rows"], fs[-1]["date"]


def wig20_betas(args, cache_dir, al, names):
    try:
        idx = gpw_archive.load_sessions(260, end=args.as_of, type_id=gpw_archive.TYPE_INDICES, cache_dir=cache_dir,
                                        offline=args.offline, progress=False)
    except gpw_archive.GPWFetchError:
        return {}
    w = panel.index_series(idx, "WIG20")
    wmap = dict(zip(w.dates, w.close))
    out = {}
    for key, nm in zip(("pa", "pb"), names):
        pts = [(d, p) for d, p in zip(al["dates"], al[key]) if d in wmap][-251:]
        if len(pts) < 120:
            out[nm] = None
            continue
        ra = stats.returns([p for _d, p in pts])
        rw = stats.returns([wmap[d] for d, _p in pts])
        b = stats.beta(ra, rw)
        out[nm] = None if b is None else round(b, 2)
    return out


def fee_fn(args):
    b = brokers.resolve(args.broker, args.commission_pct, args.commission_min)
    return b, (lambda v: brokers.commission(v, b["pct"], b["min_fee"]))


def _pair_input(pnl, a, b):
    missing = [t for t in (a, b) if t not in pnl]
    if missing:
        raise DataError("Unknown or not quoted ticker(s): {} (use GPW archive short names, e.g. PKOBP, PEKAO, PKNORLEN)."
                        .format(", ".join(missing)))
    sa, sb = pnl[a], pnl[b]
    return pe.align(sa.dates, sa.close, sa.turnover_k, sb.dates, sb.close, sb.turnover_k)


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


def _envelope(args, mode, as_of, prov, broker, extra_notes=None):
    return {"skill": SKILL, "version": VERSION, "lang": args.lang, "mode": mode, "as_of": as_of,
            "config": dict(cfg_from(args), broker=broker["key"], commission_pct=broker["pct"],
                           commission_min=broker["min_fee"], borrow_pct_annual=args.borrow_pct,
                           correction=getattr(args, "correction", "none")),
            "data_provenance": prov}


def _prov(args, as_of, sessions_used, universe, notes):
    return provenance.build(
        as_of=dt.date.fromisoformat(as_of), sources=SOURCES[:2], sessions_used=sessions_used,
        universe_size=universe, notes=notes, point_in_time_membership=False, reference_date=args.as_of)


def run_scan(args) -> Dict:
    cfg = cfg_from(args)
    cache_dir, sess, pnl = load_data(args)
    broker, fee = fee_fn(args)
    candidates = []
    notes = []
    sectors = args.sectors
    if sectors or not args.pairs:
        smap = constituents.sector_membership(cache_dir, offline=args.offline)
        if not smap:
            raise DataError("Sector membership unavailable (GPW Benchmark). Re-run online or use --pair A,B.")
        keys = sectors or list(SECTOR_INDICES)
        groups = {k: sorted(n for n, s in smap.items() if s == k) for k in keys}
        for k in keys:
            for x, y in itertools.combinations(groups[k], 2):
                candidates.append((x, y, k))
        if args.cross and len(keys) > 1:
            for k1, k2 in itertools.combinations(keys, 2):
                for x in groups[k1]:
                    for y in groups[k2]:
                        candidates.append((x, y, k1 + "×" + k2))
        notes.append("Sector membership is the current GPW Benchmark portfolio (not point-in-time).")
    seen = {(x, y) for x, y, _ in candidates} | {(y, x) for x, y, _ in candidates}
    for x, y in args.pairs:
        if (x, y) not in seen:
            candidates.append((x, y, "user"))
            seen.add((x, y))
    pairs, skipped = [], {"liquidity": 0, "history": 0, "missing": 0}
    for x, y, grp in candidates:
        if x not in pnl or y not in pnl:
            skipped["missing"] += 1
            if grp == "user":
                raise DataError("Unknown or not quoted ticker in --pair {},{}.".format(x, y))
            continue
        al = _pair_input(pnl, x, y)
        pre = pe.prefilter(x, y, al, cfg)
        if not pre["history_ok"]:
            skipped["history"] += 1
            continue
        if not pre["liquidity_ok"] and grp != "user":
            skipped["liquidity"] += 1
            continue
        r = pe.analyze_pair(x, y, al, cfg, full=False)
        r["group"] = grp
        r["_al"] = al
        pairs.append(r)
    m = pe.apply_multiple_testing(pairs, args.correction, cfg["alpha"], cfg["min_corr"])
    pairs.sort(key=pe.rank_key)
    for i, r in enumerate(pairs):
        al = r.pop("_al")
        if r["coint_pass"] or i < args.top:
            pe.complete(r, al, cfg, fee, args.borrow_pct)
            r.pop("series", None)  # compact series only in pair/switch mode
            if not r["coint_pass"]:
                r.pop("backtest", None)
    if not pairs and not candidates:
        raise DataError("No candidate pairs.")
    as_of = sess[-1]["date"]
    sig = pe.signals_for(pairs, cfg, raw_close_lookup(sess))
    prov = _prov(args, as_of, len(sess), len(pairs), notes)
    res = _envelope(args, "scan", as_of, prov, broker)
    res["config"]["top"] = args.top
    res.update({"sectors": sectors if sectors else ([] if args.pairs else list(SECTOR_INDICES)),
                "tests": m, "skipped": skipped,
                "summary": {"tested": m, "cointegrated": sum(1 for p in pairs if p["coint_pass"]),
                            "dislocated": len(sig), "expected_false_at_alpha_raw": round(m * cfg["alpha"], 1)},
                "pairs": pairs, "signals": sig})
    res["broker_notes"] = brokers.tariff_notes(args.lang, broker["overridden"])
    return res


def run_pair(args, mode: str) -> Dict:
    cfg = cfg_from(args)
    cache_dir, sess, pnl = load_data(args)
    broker, fee = fee_fn(args)
    al = _pair_input(pnl, args.a, args.b)
    r = pe.analyze_pair(args.a, args.b, al, cfg, fee=fee, borrow_pct_annual=args.borrow_pct, full=True)
    if r["status"] != "ok":
        raise DataError("Only {} common sessions for {} and {}; need at least {}.".format(
            r["sessions"], args.a, args.b, cfg["min_history"]))
    as_of = r["as_of"]
    fut_rows, fut_date = load_futures(args, cache_dir, as_of)
    hedge = {"futures_session": fut_date,
             "futures": {t: pe.futures_for(t, fut_rows, gpw_archive.parse_futures_symbol) for t in (args.a, args.b)},
             "beta_wig20": wig20_betas(args, cache_dir, al, (args.a, args.b)),
             "per_10k_a_in_b_pln": round(10000.0 * r["hedge_ratio"], 2)}
    r["hedge"] = hedge
    sig = pe.signals_for([r], cfg, raw_close_lookup(sess))
    notes = ["Single pre-specified pair: p-value not adjusted for multiple testing (m = 1)."]
    for t in (args.a, args.b):
        ren = pnl[t].renamed_from
        if ren:
            notes.append("{} history includes earlier ticker(s): {}.".format(t, ", ".join(x["name"] for x in ren)))
    prov = _prov(args, as_of, len(sess), 2, notes)
    res = _envelope(args, mode, as_of, prov, broker)
    res.update({"pairs": [r], "signals": sig})
    if mode == "switch":
        sv = pe.switch_view(r, cfg)
        cost = fee(args.position_value) * 2
        sv["switch_cost_pln"] = round(cost, 2)
        sv["holder_of"] = args.a
        sv["candidate"] = args.b
        res["switch"] = sv
    res["broker_notes"] = brokers.tariff_notes(args.lang, broker["overridden"])
    return res


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------


def _yn(t, v):
    return t("yes") if v else t("no")


def _state(t, p, cfg):
    return t("st_" + p.get("state", "unknown"), s=num(cfg["stop_z"], 1, t.lang))


def _head(r, t, title):
    prov = r["data_provenance"]
    L = ["# " + title, "", "**{}:** {} · **{}:** {}".format(t("as_of"), prov["as_of"], t("generated"),
                                                            prov["retrieved_at_utc"]), ""]
    if prov["freshness"]["is_stale"]:
        L = [t("stale", n=prov["freshness"]["sessions_behind"]), ""] + L
    return L


def _tail(r, t, lang):
    L = []
    notes = list(r["data_provenance"].get("notes", [])) + list(r.get("broker_notes", []))
    if notes:
        L += ["## " + t("notes"), ""] + ["- " + n for n in notes] + [""]
    L += ["## " + t("sources"), ""] + ["- " + s for s in SOURCES] + [
        "- " + ("Dane do sesji" if lang == "pl" else "Data through session") + " " + r["as_of"], "",
        "---", "_" + DISCLAIMER[lang] + "_", ""]
    return L


def _signals_md(r, t, cfg):
    L = ["## " + t("signals"), ""]
    if r["signals"]:
        for s in r["signals"]:
            L.append("- " + t("sig_line", t=s["ticker"], p=s["tags"]["pair"], d=s["date"], z=num(s["tags"]["z"], 2, t.lang)))
    else:
        L.append(t("sig_none", e=num(cfg["entry_z"], 1, t.lang)))
    return L + [""]


def render_scan(r: Dict, lang: str) -> str:
    t = T(STRINGS, lang)
    cfg = r["config"]
    L = _head(r, t, t("title_scan"))
    s = r["summary"]
    corr = t("corr_" + cfg["correction"])
    L += ["## " + t("answer"), "", t("scan_summary", m=s["tested"], corr=corr, alpha=num(cfg["alpha"], 2, lang),
                                     k=s["cointegrated"], entry=num(cfg["entry_z"], 1, lang), d=s["dislocated"]), ""]
    if not s["cointegrated"]:
        L += [t("scan_none"), ""]
    L += [t("why_mt_none" if cfg["correction"] == "none" else "why_mt", m=s["tested"],
            fp=num(s["expected_false_at_alpha_raw"], 1, lang), corr=corr), ""]
    L += ["## " + t("pairs"), "",
          "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
              t("col_pair"), t("col_corr"), t("col_beta"), t("col_p"), t("col_padj"), t("col_pass"), t("col_hl"),
              t("col_z"), t("col_state"), t("col_stab"), t("col_liq")),
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    shown = r["pairs"][:cfg.get("top", 15)]
    for p in shown:
        L.append("| {}/{} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            p["a"], p["b"], num(p.get("corr"), 2, lang), num(p.get("hedge_ratio"), 2, lang),
            num(p.get("p_value"), 3, lang), num(p.get("p_adj"), 3, lang), _yn(t, p.get("coint_pass")),
            num(p.get("half_life_days"), 0, lang) if p.get("half_life_days") is not None else "—",
            num(p.get("z"), 2, lang), _state(t, p, cfg),
            pct(100 * p["stability"], 0, lang) if p.get("stability") is not None else "—",
            _yn(t, p.get("liquidity_ok"))))
    L += ["", t("n_pairs_shown", k=len(shown), m=len(r["pairs"])), ""]
    sk = r["skipped"]
    if sum(sk.values()):
        L += ["### " + t("skipped"), "", t("skipped_txt", n=sum(sk.values()), liq=sk["liquidity"], hist=sk["history"],
                                           miss=sk["missing"]), ""]
    L += _signals_md(r, t, cfg)
    L += ["## " + t("hedge"), "", t("short_note"), ""]
    L += _tail(r, t, lang)
    return "\n".join(L)


def _pair_body(r: Dict, t, lang: str) -> List[str]:
    cfg = r["config"]
    p = r["pairs"][0]
    a, b = p["a"], p["b"]
    L = ["## " + t("stats"), "", "| | |", "|---|---|"]
    L.append("| {} | {} → {} ({} {}) |".format(t("k_window"), p["window_start"], p["as_of"], p["window_sessions"],
                                                t("sessions_u")))
    L.append("| {} | **{}** — {} |".format(t("k_beta"), num(p["hedge_ratio"], 3, lang),
                                           t("k_beta_txt", a=a, b=b, v=pln(10000 * p["hedge_ratio"], 0, lang))))
    L.append("| {} | {} / {}; {} |".format(t("k_adf"), num(p["adf_stat"], 2, lang), num(p["adf_stat_ba"], 2, lang),
                                           num(p["crit_5pct"], 2, lang)))
    L.append("| {} | **{}** |".format(t("k_p"), num(p["p_value"], 3, lang)))
    L.append("| {} | {} |".format(t("k_hl"), (num(p["half_life_days"], 1, lang) + " " + t("sessions_u"))
                                  if p.get("half_life_days") is not None else t("na_hl")))
    L.append("| {} | {} |".format(t("k_hurst"), num(p.get("hurst"), 2, lang)))
    L.append("| {} | {} / {} |".format(t("k_corr"), num(p["corr"], 2, lang), num(p["corr_price"], 2, lang)))
    if p.get("stability") is not None:
        hd = p["hedge_drift"]
        L.append("| {} | {} ({} {}) |".format(t("k_stab"), pct(100 * p["stability"], 0, lang), hd["windows"], t("windows_u")))
        L.append("| {} | {} → {} ({}; {}–{}) |".format(t("k_drift"), num(p["hedge_ratio"], 2, lang),
                                                       num(hd["beta_last_1y"], 2, lang),
                                                       pct(hd["drift_pct"], 1, lang, signed=True),
                                                       num(hd["beta_min"], 2, lang), num(hd["beta_max"], 2, lang)))
    rel = p.get("rel_perf_a_vs_b_pct", {})
    L.append("| {} | {} / {} / {} |".format(t("k_rel"), *(pct(rel.get(k), 1, lang, signed=True) for k in ("20", "60", "250"))))
    L.append("| {} | {} / {} ({}) |".format(t("k_liq", w=cfg["turnover_window"], need=pln(p["min_turnover_needed_pln"], 0, lang)),
                                            pln(p["median_turnover_a_pln"], 0, lang), pln(p["median_turnover_b_pln"], 0, lang),
                                            _yn(t, p["liquidity_ok"])))
    L += ["", "### " + t("chart", n=min(p["window_sessions"], cfg["max_series_points"])), "", "```"]
    L += p.get("ascii_chart", []) + ["", p.get("sparkline", ""), "```",
          t("bands", e=num(cfg["entry_z"], 1, lang), x=num(cfg["exit_z"], 1, lang), s=num(cfg["stop_z"], 1, lang)), ""]
    bt = p.get("backtest", {})
    if "in_sample" in bt and "spread_rule" in bt["in_sample"]:
        L += ["## " + t("bt"), "",
              t("bt_note", is_s=bt["in_sample"]["start"], is_e=bt["in_sample"]["end"], oos_s=bt["out_of_sample"]["start"],
                oos_e=bt["out_of_sample"]["end"], v=pln(cfg["position_value"], 0, lang), broker=cfg["broker"],
                borrow=num(cfg["borrow_pct_annual"], 1, lang)), "",
              "| {} | {} | {} | {} | {} | {} |".format(t("col_rule"), t("col_period"), t("col_trades"), t("col_net"),
                                                       t("col_costs"), t("col_extra")),
              "|---|---|---|---|---|---|"]
        for per in ("in_sample", "out_of_sample"):
            sr = bt[per]["spread_rule"]
            L.append("| {} | {} | {} | {} ({}) | {} | {} |".format(
                t("rule_spread"), t("is" if per == "in_sample" else "oos"), sr["trades"], pln(sr["net_pln"], 0, lang),
                pct(sr["net_pct_of_exposure"], 1, lang, signed=True), pln(sr["costs_pln"], 0, lang),
                t("spread_extra", w=pct(100 * sr["win_rate"], 0, lang) if sr["win_rate"] is not None else "—",
                  s=sr["stops"], d=num(sr["avg_sessions"], 0, lang) if sr["avg_sessions"] is not None else "—")))
        for per in ("in_sample", "out_of_sample"):
            sw = bt[per]["switch_rule"]
            if "return_pct" not in sw:
                continue
            L.append("| {} | {} | {} | {} | {} | {} |".format(
                t("rule_switch"), t("is" if per == "in_sample" else "oos"), sw["switches"],
                pct(sw["return_pct"], 1, lang, signed=True), pln(sw["costs_pln"], 0, lang),
                t("switch_extra", a=pct(sw["hold_a_return_pct"], 1, lang, signed=True),
                  b=pct(sw["hold_b_return_pct"], 1, lang, signed=True), m=pct(sw["mix_return_pct"], 1, lang, signed=True),
                  vs=num(sw["vs_hold_a_pp"], 1, lang))))
        L.append("")
    h = p.get("hedge", {})
    L += ["## " + t("hedge"), "", t("short_note"), ""]
    for tk in (a, b):
        f = h.get("futures", {}).get(tk, {})
        if f.get("available") is None:
            L.append("- " + t("fut_unknown", t=tk))
        elif f["available"]:
            L.append("- " + t("fut_yes", t=tk, sym=", ".join(s["symbol"] for s in f["series"]),
                              to=num(f["turnover_k_total"], 0, lang)))
        else:
            L.append("- " + t("fut_no", t=tk))
    bw = h.get("beta_wig20", {})
    if bw:
        L.append("- " + t("beta_w20", a=a, ba=num(bw.get(a), 2, lang), b=b, bb=num(bw.get(b), 2, lang)))
    return L + [""]


def _pair_answer(r, t, lang):
    cfg = r["config"]
    p = r["pairs"][0]
    if p["coint_pass"]:
        s = t("verdict_pair_pass", a=p["a"], b=p["b"], p=num(p["p_value"], 3, lang),
              hl=num(p["half_life_days"], 0, lang) if p.get("half_life_days") is not None else "—")
    else:
        s = t("verdict_pair_fail", a=p["a"], b=p["b"], p=num(p["p_value"], 3, lang), alpha=num(cfg["alpha"], 2, lang),
              L=cfg["z_lookback"])
        if not p.get("corr_ok"):
            s += t("verdict_corr", c=num(p.get("corr"), 2, lang), m=num(cfg["min_corr"], 2, lang))
    zl = t("z_now", z=num(p["z"], 2, lang), state=_state(t, p, cfg), pctl=num(p.get("z_percentile"), 0, lang),
           L=p["window_sessions"])
    return [s, "", zl, ""]


def render_pair(r: Dict, lang: str) -> str:
    t = T(STRINGS, lang)
    p = r["pairs"][0]
    L = _head(r, t, t("title_pair", a=p["a"], b=p["b"]))
    L += ["## " + t("answer"), ""] + _pair_answer(r, t, lang)
    L += _pair_body(r, t, lang)
    L += _signals_md(r, t, r["config"])
    L += _tail(r, t, lang)
    return "\n".join(L)


def render_switch(r: Dict, lang: str) -> str:
    t = T(STRINGS, lang)
    cfg = r["config"]
    p = r["pairs"][0]
    sv = r["switch"]
    L = _head(r, t, t("title_switch", a=p["a"], b=p["b"]))
    L += ["## " + t("answer"), "",
          t("sw_verdict_" + sv["verdict"], a=p["a"], b=p["b"], z=num(p.get("z"), 2, lang),
            e=num(cfg["entry_z"], 1, lang), s=num(cfg["stop_z"], 1, lang)), ""]
    L += _pair_answer(r, t, lang)
    L += [t("sw_cost", v=pln(cfg["position_value"], 0, lang), c=pln(sv["switch_cost_pln"], 2, lang),
            broker=cfg["broker"], a=p["a"]), ""]
    L += _pair_body(r, t, lang)
    L += _signals_md(r, t, cfg)
    L += _tail(r, t, lang)
    return "\n".join(L)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if args.mode == "scan":
            result = run_scan(args)
            md = render_scan(result, args.lang)
        else:
            result = run_pair(args, args.mode)
            md = (render_pair if args.mode == "pair" else render_switch)(result, args.lang)
    except (gpw_archive.GPWFetchError, DataError, ValueError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    if args.no_files:
        print(md)
        return 0
    out = Path(args.output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    jp, mp = out / "gpw_pair_trading_{}.json".format(stamp), out / "gpw_pair_trading_{}.md".format(stamp)
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(md, encoding="utf-8")
    print(md)
    print("JSON: {}\nMarkdown: {}".format(jp, mp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
