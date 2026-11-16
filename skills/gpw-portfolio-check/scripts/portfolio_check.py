#!/usr/bin/env python3
"""GPW Portfolio Check - CLI.

Health check of a portfolio of GPW shares and ETFs: values, weights, P&L,
sector and size mix, concentration, correlations, volatility, beta, VaR/ES,
drawdown, comparison with WIG, liquidity and open risk to stops.

    python3 scripts/portfolio_check.py --csv holdings.csv --cash 5000
    python3 scripts/portfolio_check.py --from-journal --lang en

The engine lives in portfolio_check_engine.py (pure, tested); this file loads
data, renders the bilingual Markdown and writes the JSON + Markdown reports.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import constituents, gpw_archive, journal_io, panel, provenance  # noqa: E402
from gpwlib.constituents import SECTOR_INDICES  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct, pln  # noqa: E402

import portfolio_check_engine as pe  # noqa: E402

VERSION = "1.0.0"
PREFIX = "gpw_portfolio_check"

STRINGS = {
    "title": {"pl": "Przegląd portfela GPW", "en": "GPW Portfolio Check"},
    "as_of": {"pl": "Dane na sesję", "en": "Data as of session"},
    "generated": {"pl": "Wygenerowano", "en": "Generated"},
    "source_in": {"pl": "Źródło pozycji", "en": "Holdings source"},
    "src_csv": {"pl": "plik CSV {f}", "en": "CSV file {f}"},
    "src_journal": {"pl": "dziennik (aktywne pozycje) {f}", "en": "journal (ACTIVE positions) {f}"},
    "stale": {"pl": "⚠️ UWAGA: dane rynkowe są nieaktualne o {n} sesji – liczby mogą nie odpowiadać dzisiejszym cenom.",
              "en": "⚠️ WARNING: market data is {n} session(s) old – figures may not match today's prices."},
    "in_short": {"pl": "W skrócie", "en": "In short"},
    # --- summary sentences
    "s_value": {"pl": "Twój portfel jest wart {total} ({n} poz.{cash}).",
                "en": "Your portfolio is worth {total} ({n} position(s){cash})."},
    "s_cash": {"pl": ", w tym gotówka {c}", "en": ", incl. cash {c}"},
    "s_conc_high": {"pl": "Jest mocno skoncentrowany: {parts}; to tyle, co ok. {eff} równych pozycji.",
                    "en": "It is heavily concentrated: {parts}; that is like holding about {eff} equal positions."},
    "s_conc_mod": {"pl": "Koncentracja jest umiarkowana: odpowiada ok. {eff} równym pozycjom, największa to {t} ({w}).",
                   "en": "Concentration is moderate: it behaves like about {eff} equal positions, the largest is {t} ({w})."},
    "s_conc_low": {"pl": "Jest dobrze rozłożony: odpowiada ok. {eff} równym pozycjom, największa to {t} ({w}).",
                   "en": "It is well spread: it behaves like about {eff} equal positions, the largest is {t} ({w})."},
    "s_conc_etf": {"pl": "Większość portfela to ETF-y: fundusz sam jest koszykiem wielu spółek, więc formalna liczba pozycji (≈ {eff}) zaniża realne rozproszenie; największa pozycja to {t} ({w}).",
                   "en": "Most of the portfolio is in ETFs: a fund is itself a basket of many companies, so the formal position count (≈ {eff}) understates real diversification; the largest position is {t} ({w})."},
    "s_part_pos": {"pl": "największa pozycja, {t}, to {w} wartości", "en": "the largest position, {t}, is {w} of the value"},
    "s_part_sec": {"pl": "sektor {s} to {w}", "en": "the {s} sector is {w}"},
    "s_cluster": {"pl": " {names} poruszają się bardzo podobnie (korelacja > {c}), więc realna dywersyfikacja jest mniejsza, niż wygląda.",
                  "en": " {names} move very similarly (correlation > {c}), so real diversification is lower than it looks."},
    "s_risk": {"pl": "W złej sesji (taka lub gorsza zdarza się średnio raz na 20) portfel może stracić ok. {var} lub więcej, a w ciągu ostatniego roku najgłębszy spadek portfela o obecnym składzie wyniósł {mdd}.",
               "en": "On a bad session (that bad or worse about once in 20) the portfolio can lose about {var} or more; over the last year the deepest fall of a portfolio with today's weights was {mdd}."},
    "s_wig": {"pl": "Przez ostatni rok portfel o dzisiejszych wagach zmieniłby się o {r} wobec {w} dla WIG; beta {b} oznacza, że reaguje na ruchy rynku {how}.",
              "en": "Over the last year a portfolio with today's weights would have returned {r} vs {w} for the WIG; a beta of {b} means it reacts to market moves {how}."},
    "how_more": {"pl": "mocniej niż WIG", "en": "more strongly than the WIG"},
    "how_less": {"pl": "słabiej niż WIG", "en": "less strongly than the WIG"},
    "how_same": {"pl": "podobnie jak WIG", "en": "about as much as the WIG"},
    "s_review": {"pl": "{k} z {n} pozycji oznaczono jako „do przeglądu” (REVIEW) – to sygnał, żeby się im przyjrzeć, a nie polecenie sprzedaży.",
                 "en": "{k} of {n} positions are marked REVIEW – a prompt to take a closer look, not an instruction to sell."},
    "s_ok": {"pl": "Żadna pozycja nie przekracza progów ostrzegawczych.", "en": "No position breaches the warning thresholds."},
    "s_norisk": {"pl": "Statystyk ryzyka nie policzono (za krótka historia notowań).",
                 "en": "Risk statistics were not computed (price history too short)."},
    # --- sections
    "totals": {"pl": "Podsumowanie wartości", "en": "Value summary"},
    "t_total": {"pl": "Wartość portfela", "en": "Portfolio value"},
    "t_invested": {"pl": "W akcjach / ETF", "en": "In shares / ETFs"},
    "t_cash": {"pl": "Gotówka", "en": "Cash"},
    "t_cost": {"pl": "Koszt zakupu (średnia cena)", "en": "Cost (average price)"},
    "t_pnl": {"pl": "Niezrealizowany wynik", "en": "Unrealised P&L"},
    "t_tax": {"pl": "Orientacyjny podatek Belki przy sprzedaży dziś (rachunek zwykły)", "en": "Indicative 19% tax if sold today (regular account)"},
    "flags": {"pl": "Na co zwrócić uwagę", "en": "What needs attention"},
    "no_flags": {"pl": "Brak przekroczonych progów.", "en": "No thresholds breached."},
    "f_position_weight": {"pl": "Pojedyncza pozycja > {l}% portfela: {x}.", "en": "Single position > {l}% of the portfolio: {x}."},
    "f_sector_weight": {"pl": "Sektor > {l}% portfela: {x}.", "en": "Sector > {l}% of the portfolio: {x}."},
    "f_hhi_high": {"pl": "Wysoka koncentracja: HHI {h} (próg {l}) – mało niezależnych „zakładów”.",
                   "en": "High concentration: HHI {h} (threshold {l}) – few independent bets."},
    "f_illiquid": {"pl": "Niska płynność (wyjście > {l} sesji przy 10% obrotu): {x}.", "en": "Illiquid (exit > {l} sessions at 10% of turnover): {x}."},
    "f_correlated_cluster": {"pl": "Silnie skorelowana grupa ({w} portfela): {x}.", "en": "Highly correlated cluster ({w} of the portfolio): {x}."},
    "f_no_stop": {"pl": "Brak stop lossa: {x}.", "en": "No stop loss: {x}."},
    "f_below_stop": {"pl": "Kurs na lub poniżej stopu z planu: {x}.", "en": "Price at or below the planned stop: {x}."},
    "positions": {"pl": "Pozycje", "en": "Positions"},
    "c_ticker": {"pl": "Walor", "en": "Ticker"}, "c_shares": {"pl": "Szt.", "en": "Shares"},
    "c_avg": {"pl": "Śr. cena", "en": "Avg price"}, "c_last": {"pl": "Kurs", "en": "Last"},
    "c_value": {"pl": "Wartość", "en": "Value"}, "c_weight": {"pl": "Udział", "en": "Weight"},
    "c_pnl": {"pl": "Wynik", "en": "P&L"}, "c_sector": {"pl": "Sektor", "en": "Sector"},
    "c_size": {"pl": "Indeks", "en": "Index"}, "c_dte": {"pl": "Dni do wyjścia", "en": "Days to exit"},
    "c_status": {"pl": "Status", "en": "Status"}, "c_reasons": {"pl": "Powody", "en": "Reasons"},
    "r_position_weight": {"pl": "udział {v}% > {l}%", "en": "weight {v}% > {l}%"},
    "r_sector_weight": {"pl": "sektor {s} {v}% > {l}%", "en": "sector {s} {v}% > {l}%"},
    "r_illiquid": {"pl": "płynność: {v} dni > {l}", "en": "liquidity: {v} days > {l}"},
    "r_correlated": {"pl": "korelacja > 0,7 z {w}", "en": "correlation > 0.7 with {w}"},
    "r_no_stop": {"pl": "brak stopu", "en": "no stop"},
    "r_below_stop": {"pl": "kurs ≤ stop", "en": "price ≤ stop"},
    "r_stale_quote": {"pl": "brak notowań od {v} sesji", "en": "no quote for {v} sessions"},
    "r_check_avg_price": {"pl": "sprawdź średnią cenę (split? literówka?) – kurs to {v}× ceny zakupu", "en": "check the average price (split? typo?) – price is {v}x the purchase price"},
    "r_short_history": {"pl": "krótka historia – poza statystykami ryzyka", "en": "short history – left out of risk stats"},
    "sectors": {"pl": "Sektory", "en": "Sectors"},
    "sizes": {"pl": "Wielkość spółek (przynależność do indeksów)", "en": "Company size (index membership)"},
    "accounts": {"pl": "Rachunki", "en": "Accounts"},
    "c_bucket": {"pl": "Grupa", "en": "Group"},
    "conc": {"pl": "Koncentracja", "en": "Concentration"},
    "conc_txt": {"pl": "HHI = {h} ({h10} w skali 0–10 000, poziom: {lvl}); efektywna liczba pozycji = {eff} z {n}; 3 największe = {t3}.",
                 "en": "HHI = {h} ({h10} on the 0–10,000 scale, level: {lvl}); effective number of positions = {eff} of {n}; top 3 = {t3}."},
    "lvl_low": {"pl": "niska", "en": "low"}, "lvl_moderate": {"pl": "umiarkowana", "en": "moderate"}, "lvl_high": {"pl": "wysoka", "en": "high"},
    "conc_etf": {"pl": "ETF liczony jest jako jedna pozycja, więc HHI zawyża koncentrację portfela z szerokimi ETF-ami.",
                 "en": "An ETF counts as one position, so HHI overstates concentration of a portfolio with broad ETFs."},
    "risk": {"pl": "Ryzyko (portfel o dzisiejszych wagach, ostatni rok)", "en": "Risk (today's weights, last year)"},
    "risk_period": {"pl": "Okres: {a} – {b} ({n} dziennych stóp zwrotu). Gotówka liczona z zerową zmiennością.",
                    "en": "Period: {a} – {b} ({n} daily returns). Cash is treated as zero-volatility."},
    "k_vol": {"pl": "Zmienność roczna portfela (z gotówką)", "en": "Annual volatility (incl. cash)"},
    "k_evol": {"pl": "Zmienność części akcyjnej", "en": "Volatility of the equity part"},
    "k_wvol": {"pl": "Zmienność WIG", "en": "WIG volatility"},
    "k_beta": {"pl": "Beta względem WIG", "en": "Beta vs WIG"},
    "k_var": {"pl": "VaR 95% (1 sesja, historyczny)", "en": "VaR 95% (1 session, historical)"},
    "k_es": {"pl": "ES 95% (średnia strata w 5% najgorszych sesji)", "en": "ES 95% (average loss on the worst 5% of sessions)"},
    "k_var20": {"pl": "VaR 95% przeskalowany na {h} sesji (√czasu)", "en": "VaR 95% scaled to {h} sessions (√time)"},
    "k_mdd": {"pl": "Maks. obsunięcie (1 rok)", "en": "Max drawdown (1 year)"},
    "k_cdd": {"pl": "Obecne obsunięcie od szczytu", "en": "Current drawdown from peak"},
    "k_ret": {"pl": "Zmiana portfela (1 rok)", "en": "Portfolio return (1 year)"},
    "k_wret": {"pl": "Zmiana WIG (1 rok)", "en": "WIG return (1 year)"},
    "k_wmdd": {"pl": "Maks. obsunięcie WIG", "en": "WIG max drawdown"},
    "k_excess": {"pl": "Różnica vs WIG", "en": "Difference vs WIG"},
    "var_caveat": {"pl": "VaR/ES to statystyka historyczna: nie jest limitem straty. Skalowanie √czasu zakłada niezależne, podobne sesje – w kryzysie straty skupiają się i bywają większe.",
                   "en": "VaR/ES are historical statistics, not a loss limit. √time scaling assumes independent, similar sessions – in a crisis losses cluster and can be larger."},
    "excluded": {"pl": "Poza statystykami ryzyka (za krótka historia): {x} ({w} portfela) – ryzyko jest przez to niedoszacowane.",
                 "en": "Left out of risk statistics (history too short): {x} ({w} of the portfolio) – risk is understated."},
    "risk_na": {"pl": "Statystyki ryzyka niedostępne: {r}.", "en": "Risk statistics unavailable: {r}."},
    "per_pos": {"pl": "Zmienność, beta i wynik 1 rok per pozycja", "en": "Volatility, beta and 1-year return per position"},
    "c_vol": {"pl": "Zmienność", "en": "Volatility"}, "c_beta": {"pl": "Beta", "en": "Beta"}, "c_ret": {"pl": "Zmiana 1 rok", "en": "1y return"},
    "corr": {"pl": "Korelacje dziennych stóp zwrotu", "en": "Correlation of daily returns"},
    "corr_top": {"pl": "Najsilniej skorelowane pary", "en": "Most correlated pairs"},
    "corr_none": {"pl": "Brak par z korelacją > {c}.", "en": "No pair with correlation > {c}."},
    "liq": {"pl": "Płynność", "en": "Liquidity"},
    "liq_txt": {"pl": "Dni do wyjścia = wartość pozycji / (10% mediany dziennego obrotu z {n} sesji). Powyżej {l} sesji sprzedaż całej pozycji bez wpływu na kurs może być trudna.",
                "en": "Days to exit = position value / (10% of the median daily turnover over {n} sessions). Above {l} sessions, selling the whole position without moving the price may be hard."},
    "c_medto": {"pl": "Mediana obrotu", "en": "Median turnover"},
    "heat": {"pl": "Otwarte ryzyko do stopów (portfolio heat)", "en": "Open risk to stops (portfolio heat)"},
    "heat_txt": {"pl": "Suma (kurs − stop) × liczba akcji = {r}, czyli {p} portfela. Popularna górna granica u traderów to ok. 6%.",
                 "en": "Sum of (price − stop) × shares = {r}, i.e. {p} of the portfolio. A common trader ceiling is about 6%."},
    "heat_missing": {"pl": "Bez stopu (ryzyko nieokreślone, nieuwzględnione w sumie): {x}.",
                     "en": "Without a stop (risk undefined, not in the total): {x}."},
    "heat_na": {"pl": "Stopy nieznane (CSV bez kolumny stop) – heat nie policzony. Użyj --from-journal albo dodaj kolumnę stop.",
                "en": "Stops unknown (CSV without a stop column) – heat not computed. Use --from-journal or add a stop column."},
    "notes": {"pl": "Uwagi metodologiczne", "en": "Method notes"},
    "n_avg_csv": {"pl": "Wynik liczony od średniej ceny z pliku (jak w aplikacji brokera). Do PIT-38 liczy się koszt metodą FIFO – przy dokupkach i częściowej sprzedaży może się różnić. Dywidendy nie są wliczone.",
                  "en": "P&L uses the average price from the file (as shown by the broker). PIT-38 uses FIFO cost – it can differ after top-ups and partial sales. Dividends are not included."},
    "n_avg_journal": {"pl": "Średnia cena = koszt pozostałych lotów FIFO z dziennika (tak jak do PIT-38), bez prowizji. Dywidendy nie są wliczone.",
                      "en": "Average price = cost of the remaining FIFO lots in the journal (as for PIT-38), excl. commissions. Dividends are not included."},
    "n_adjusted": {"pl": "Statystyki liczone na kursach skorygowanych o dywidendy i splity (z publikowanej przez GPW zmiany %); wartość pozycji – po ostatnim kursie zamknięcia.",
                   "en": "Statistics use prices adjusted for dividends and splits (from GPW's published % change); position values use the last close."},
    "n_sector": {"pl": "Sektory = bieżący skład 14 indeksów sektorowych GPW Benchmark; spółki spoza nich = „poza indeksami sektorowymi”, ETF = osobna grupa (bez limitów sektorowych i pojedynczej pozycji).",
                 "en": "Sectors = current members of the 14 GPW Benchmark sector indices; others = 'no sector index', ETFs = separate group (exempt from sector and single-position limits)."},
    "n_tax": {"pl": "Podatek orientacyjny: 19% od dodatniego niezrealizowanego wyniku na rachunku zwykłym, bez prowizji i bez strat z innych transakcji w roku. IKE/IKZE/OKI pominięte (inne zasady).",
              "en": "Indicative tax: 19% of the positive unrealised result on the regular account, ignoring fees and other losses in the year. IKE/IKZE/OKI excluded (different rules)."},
    "n_alias": {"pl": "Rozpoznane skróty: {x}.", "en": "Recognised tickers: {x}."},
    "thresholds": {"pl": "Progi", "en": "Thresholds"},
    "thr_txt": {"pl": "pozycja > {p}% · sektor > {s}% · HHI > {h} · wyjście > {d} sesji przy {pa}% obrotu · korelacja > {c}. Uzasadnienie: references/methodology.md.",
                "en": "position > {p}% · sector > {s}% · HHI > {h} · exit > {d} sessions at {pa}% of turnover · correlation > {c}. Rationale: references/methodology.md."},
    "sources": {"pl": "Źródła", "en": "Sources"},
    "OTHER_sector": {"pl": "poza indeksami sektorowymi", "en": "no sector index"},
    "OTHER_size": {"pl": "poza WIG20/mWIG40/sWIG80", "en": "outside WIG20/mWIG40/sWIG80"},
}

SIZE_LABELS = {"WIG20": "WIG20", "MWIG40": "mWIG40", "SWIG80": "sWIG80", "ETF": "ETF"}


def sector_label(key: str, lang: str, t) -> str:
    if key == "ETF":
        return "ETF"
    if key in SECTOR_INDICES:
        return SECTOR_INDICES[key][2] if lang == "pl" else SECTOR_INDICES[key][1]
    return t("OTHER_sector")


def size_label(key: str, t) -> str:
    return SIZE_LABELS.get(key) or t("OTHER_size")


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------

def _series_dict(s) -> dict:
    return {"dates": list(s.dates), "close": list(s.close), "turnover_k": list(s.turnover_k)}


def load_market(tickers, lookback, end, cache_dir, offline):
    """Resolve tickers against the shares archive (then ETFs) and return price data."""
    n = lookback + 6
    print("Loading GPW share sessions...", file=sys.stderr)
    shares = gpw_archive.load_sessions(n, end=end, cache_dir=cache_dir, offline=offline, progress=True)
    if not shares:
        raise gpw_archive.GPWFetchError("no GPW share sessions available (offline cache empty or network error)")
    pnl = panel.build_panel(shares)
    resolved, aliases, missing = {}, [], []
    for t in tickers:
        name, note = pe.resolve_ticker(t, pnl.keys())
        if name:
            resolved[t] = (name, "share")
            if note:
                aliases.append(note)
        else:
            missing.append(t)
    etf_pnl, etf_sessions, etf_names = {}, [], []
    if missing:
        latest = gpw_archive.load_sessions(1, end=end, type_id=gpw_archive.TYPE_ETF, cache_dir=cache_dir,
                                           offline=offline, progress=False)
        etf_names = [r["name"] for r in latest[-1]["rows"]] if latest else []
        still = [t for t in missing if not pe.resolve_ticker(t, etf_names)[0]]
        if still:  # fail before downloading ETF history
            msgs = []
            for t in still:
                cm = pe.close_matches(t, list(pnl.keys()) + etf_names)
                msgs.append("'{}' not found in the GPW archive (shares{}); close matches: {}".format(
                    t, " and ETFs" if etf_names else "; ETF list unavailable offline",
                    ", ".join(cm) if cm else "none"))
            raise pe.PortfolioError("; ".join(msgs) + ". Use the GPW short name, e.g. PKOBP, PKNORLEN, CDPROJEKT.")
        print("Loading GPW ETF sessions (not cached yet: ~1 s per session)...", file=sys.stderr)
        etf_sessions = gpw_archive.load_sessions(n, end=end, type_id=gpw_archive.TYPE_ETF, cache_dir=cache_dir,
                                                 offline=offline, progress=True)
        etf_pnl = panel.build_panel(etf_sessions)
        for t in missing:
            name, note = pe.resolve_ticker(t, etf_pnl.keys())
            if not name:
                raise pe.PortfolioError("'{}' has no ETF price history in the archive".format(t))
            resolved[t] = (name, "etf")
            if note:
                aliases.append(note)
    indices = gpw_archive.load_sessions(n, end=end, type_id=gpw_archive.TYPE_INDICES, cache_dir=cache_dir,
                                        offline=offline, progress=False)
    wig = panel.index_series(indices, "WIG") if indices else None
    if not wig or len(wig) < 2:
        raise gpw_archive.GPWFetchError("WIG index history unavailable")
    series = {}
    for t, (name, kind) in resolved.items():
        series[name] = _series_dict((pnl if kind == "share" else etf_pnl)[name])
    adjustments = {name: (pnl if kind == "share" else etf_pnl)[name].adjustments for t, (name, kind) in resolved.items()}
    return {"resolved": resolved, "aliases": aliases, "series": series,
            "wig": {"dates": list(wig.dates), "close": list(wig.close)}, "shares_sessions": shares,
            "etf_sessions": etf_sessions, "index_sessions": indices, "adjustments": adjustments}


def run(args) -> dict:
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else None
    end = dt.date.fromisoformat(args.as_of) if args.as_of else None
    if args.from_journal:
        state = Path(args.state_dir).expanduser() if args.state_dir else journal_io.default_state_dir()
        holdings = pe.holdings_from_journal(journal_io.open_positions(journal_io.load_theses(state)))
        src = {"type": "journal", "path": str(state)}
    else:
        path = Path(args.csv).expanduser()
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise pe.PortfolioError("cannot read {}: {}".format(path, exc))
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1250")  # older Polish broker exports (Windows-1250)
        holdings = pe.parse_holdings_csv(text)
        src = {"type": "csv", "path": str(path)}
    mk = load_market([h["ticker"] for h in holdings], args.lookback, end, cache_dir, args.offline)
    sector_map = constituents.sector_membership(cache_dir, offline=args.offline)
    size_map = constituents.size_membership(cache_dir, offline=args.offline)
    for h in holdings:
        name, kind = mk["resolved"][h["ticker"]]
        h.update(name=name, kind=kind, sector=pe.sector_of(name, kind, sector_map), size=pe.size_bucket(name, kind, size_map))
    # Merge rows that resolve to the same archive name (e.g. 'PKN' and 'PKNORLEN').
    merged = {}
    for h in holdings:
        m = merged.get(h["name"])
        if m is None:
            merged[h["name"]] = h
            continue
        m["cost"] += h["cost"]
        m["shares"] += h["shares"]
        m["avg_price"] = round(m["cost"] / m["shares"], 4)
        for a, s in h["accounts"].items():
            m["accounts"][a] = m["accounts"].get(a, 0) + s
        m["stops"] += h["stops"]
        m["thesis_ids"] += h["thesis_ids"]
    res = pe.analyze(list(merged.values()), mk["series"], mk["wig"], cash=args.cash,
                     cfg={"lookback": args.lookback})
    as_of = dt.date.fromisoformat(res["as_of"])
    sources = ["GPW — Archiwum notowań (akcje / shares): https://www.gpw.pl/archiwum-notowan",
               "GPW — Archiwum notowań (indeksy / indices, WIG): https://www.gpw.pl/archiwum-notowan"]
    if mk["etf_sessions"]:
        sources.append("GPW — Archiwum notowań (ETF, type 241): https://www.gpw.pl/archiwum-notowan")
    sources.append("GPW Benchmark — portfele indeksów (sektory, WIG20/mWIG40/sWIG80): https://gpwbenchmark.pl")
    notes = ["Sector and size membership = current GPW Benchmark portfolios (not point-in-time).",
             "Risk statistics use today's weights applied to the last {} sessions (historical simulation).".format(args.lookback)]
    adj = {k: v for k, v in mk["adjustments"].items() if v}
    if adj:
        notes.append("Corporate-action adjustments detected: " + ", ".join(
            "{} ({})".format(k, ", ".join(a["date"] for a in v)) for k, v in sorted(adj.items())))
    prov = provenance.build(as_of=as_of, sources=sources, sessions_used=len(mk["shares_sessions"]),
                            universe_size=len(res["positions"]), notes=notes, point_in_time_membership=False,
                            reference_date=end)
    return {"skill": "gpw-portfolio-check", "version": VERSION, "lang": args.lang, "source": src,
            "aliases": mk["aliases"], **res, "data_provenance": prov}


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

def _dte(x, lang):
    if x is None:
        return "∞"
    return "< " + num(0.1, 1, lang) if x < 0.1 else num(x, 1, lang)


def _w(x, lang):
    return pct(x, 1, lang)


def summary_sentences(r: dict, lang: str) -> list:
    t = T(STRINGS, lang)
    tot, conc, risk, pos = r["totals"], r["concentration"], r["risk"], r["positions"]
    cfg = r["thresholds"]
    out = [t("s_value", total=pln(tot["total_value"], 0, lang), n=len(pos),
             cash=t("s_cash", c=_w(tot["cash_pct"], lang)) if tot["cash"] > 0 else "")]
    eff = num(conc["effective_positions"], 1, lang)
    big = [p for p in pos if p["kind"] != "etf" and p["weight_pct"] > cfg["max_position_pct"]]
    secs = [(k, v) for k, v in r["sectors"].items() if k not in ("ETF", "OTHER") and v["weight_pct"] > cfg["max_sector_pct"]]
    cl_txt = ""
    if risk.get("clusters"):
        cl = risk["clusters"][0]
        cl_txt = t("s_cluster", names=", ".join(cl), c=num(cfg["corr_high"], 1, lang))
    if big or secs or any(f["code"] == "hhi_high" for f in r["flags"]):
        parts = []
        if big:
            parts.append(t("s_part_pos", t=big[0]["ticker"], w=_w(big[0]["weight_pct"], lang)))
        for k, v in secs[:2]:
            parts.append(t("s_part_sec", s=sector_label(k, lang, t), w=_w(v["weight_pct"], lang)))
        if not parts:
            parts.append(t("s_part_pos", t=pos[0]["ticker"], w=_w(pos[0]["weight_pct"], lang)))
        out.append(t("s_conc_high", parts=", ".join(parts), eff=eff) + cl_txt)
    else:
        key = "s_conc_mod" if conc["level"] == "moderate" else "s_conc_low"
        if conc["level"] != "low" and conc.get("stock_share_pct", 100) <= 50:
            key = "s_conc_etf"
        out.append(t(key, eff=eff, t=pos[0]["ticker"], w=_w(pos[0]["weight_pct"], lang)) + cl_txt)
    if risk.get("available") and risk.get("var_pln") is not None:
        out.append(t("s_risk", var=pln(risk["var_pln"], 0, lang), mdd=_w(risk["max_drawdown_pct"], lang)))
        b = risk["beta"]
        how = t("how_same") if b is None or 0.9 <= b <= 1.1 else (t("how_more") if b > 1.1 else t("how_less"))
        out.append(t("s_wig", r=pct(risk["return_pct"], 1, lang, signed=True), w=pct(risk["wig_return_pct"], 1, lang, signed=True),
                     b=num(b, 2, lang), how=how))
    else:
        out.append(t("s_norisk"))
    out.append(t("s_review", k=r["review_count"], n=len(pos)) if r["review_count"] else t("s_ok"))
    return out


def _reason(rs: dict, lang: str, t) -> str:
    c = rs["code"]
    if c == "position_weight":
        return t("r_position_weight", v=num(rs["value"], 1, lang), l=num(rs["limit"], 0, lang))
    if c == "sector_weight":
        return t("r_sector_weight", s=sector_label(rs["sector"], lang, t), v=num(rs["value"], 1, lang), l=num(rs["limit"], 0, lang))
    if c == "illiquid":
        return t("r_illiquid", v=num(rs["value"], 1, lang) if rs["value"] is not None else "∞", l=num(rs["limit"], 0, lang))
    if c == "correlated":
        return t("r_correlated", w=", ".join(rs["with"]))
    if c == "check_avg_price":
        return t("r_check_avg_price", v=num(rs["value"], 2, lang))
    if c == "stale_quote":
        return t("r_stale_quote", v=rs["value"])
    return t("r_" + c)


def render_markdown(r: dict, lang: str) -> str:
    t = T(STRINGS, lang)
    prov, tot, pos, risk, cfg = r["data_provenance"], r["totals"], r["positions"], r["risk"], r["thresholds"]
    L = ["# " + t("title"), ""]
    if prov["freshness"]["is_stale"]:
        L += ["> " + t("stale", n=prov["freshness"]["sessions_behind"]), ""]
    src = t("src_csv", f=r["source"]["path"]) if r["source"]["type"] == "csv" else t("src_journal", f=r["source"]["path"])
    L += ["**{}:** {} · **{}:** {} · **{}:** {}".format(t("as_of"), prov["as_of"], t("generated"), prov["retrieved_at_utc"],
                                                         t("source_in"), src), ""]
    L += ["## " + t("in_short"), "", " ".join(summary_sentences(r, lang)), ""]
    # totals
    L += ["## " + t("totals"), "", "| | |", "|---|---|",
          "| {} | **{}** |".format(t("t_total"), pln(tot["total_value"], 2, lang)),
          "| {} | {} ({}) |".format(t("t_invested"), pln(tot["invested"], 2, lang), _w(100 - tot["cash_pct"], lang)),
          "| {} | {} ({}) |".format(t("t_cash"), pln(tot["cash"], 2, lang), _w(tot["cash_pct"], lang)),
          "| {} | {} |".format(t("t_cost"), pln(tot["cost"], 2, lang)),
          "| {} | {} ({}) |".format(t("t_pnl"), pln(tot["pnl"], 2, lang), pct(tot["pnl_pct"], 1, lang, signed=True))]
    if r["tax"]["regular_unrealised_net"] > 0:
        L.append("| {} | ≈ {} |".format(t("t_tax"), pln(r["tax"]["indicative_tax_if_sold"], 0, lang)))
    L.append("")
    # flags
    L += ["## " + t("flags"), ""]
    if not r["flags"]:
        L += [t("no_flags"), ""]
    for f in r["flags"]:
        c = f["code"]
        if c == "position_weight":
            x = ", ".join("{} ({})".format(p["ticker"], _w(p["weight_pct"], lang)) for p in pos if p["ticker"] in f["tickers"])
            L.append("- " + t("f_position_weight", l=num(f["limit"], 0, lang), x=x))
        elif c == "sector_weight":
            x = ", ".join("{} ({})".format(sector_label(s, lang, t), _w(r["sectors"][s]["weight_pct"], lang)) for s in f["sectors"])
            L.append("- " + t("f_sector_weight", l=num(f["limit"], 0, lang), x=x))
        elif c == "hhi_high":
            L.append("- " + t("f_hhi_high", h=num(f["hhi"], 3, lang), l=num(f["limit"], 2, lang)))
        elif c == "illiquid":
            L.append("- " + t("f_illiquid", l=num(f["limit"], 0, lang), x=", ".join(f["tickers"])))
        elif c == "correlated_cluster":
            L.append("- " + t("f_correlated_cluster", w=_w(f["weight_pct"], lang), x=", ".join(f["tickers"])))
        else:
            L.append("- " + t("f_" + c, x=", ".join(f["tickers"])))
    L.append("")
    # positions
    L += ["## " + t("positions"), "",
          "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
              t("c_ticker"), t("c_shares"), t("c_avg"), t("c_last"), t("c_value"), t("c_weight"), t("c_pnl"),
              t("c_sector"), t("c_size"), t("c_dte"), t("c_status"), t("c_reasons")),
          "|---|---:|---:|---:|---:|---:|---:|---|---|---:|---|---|"]
    for p in pos:
        L.append("| {} | {} | {} | {} | {} | {} | {} ({}) | {} | {} | {} | **{}** | {} |".format(
            p["ticker"], num(p["shares"], 0, lang), num(p["avg_price"], 2, lang), num(p["last"], 2, lang),
            pln(p["value"], 0, lang), _w(p["weight_pct"], lang), pln(p["pnl"], 0, lang),
            pct(p["pnl_pct"], 1, lang, signed=True), sector_label(p["sector"], lang, t), size_label(p["size"], t),
            _dte(p["days_to_exit"], lang), p["status"],
            "; ".join(_reason(x, lang, t) for x in p["reasons"]) or "—"))
    L.append("")
    # buckets
    for key, title, lab in (("sectors", "sectors", lambda k: sector_label(k, lang, t)),
                            ("sizes", "sizes", lambda k: size_label(k, t)),
                            ("accounts", "accounts", lambda k: k.upper() if k != "regular" else ("zwykły" if lang == "pl" else "regular"))):
        if key == "accounts" and len(r["accounts"]) < 2:
            continue
        L += ["### " + t(title), "", "| {} | {} | {} |".format(t("c_bucket"), t("c_value"), t("c_weight")), "|---|---:|---:|"]
        for k, v in r[key].items():
            L.append("| {} | {} | {} |".format(lab(k), pln(v["value"], 0, lang), _w(v["weight_pct"], lang)))
        L.append("")
    conc = r["concentration"]
    L += ["## " + t("conc"), "", t("conc_txt", h=num(conc["hhi"], 3, lang), h10=num(conc["hhi_10000"], 0, lang),
                                   lvl=t("lvl_" + conc["level"]), eff=num(conc["effective_positions"], 1, lang),
                                   n=conc["n_positions"], t3=_w(conc["top3_weight_pct"], lang))]
    if conc["etfs_counted_as_one"]:
        L += ["", t("conc_etf")]
    L.append("")
    # risk
    L += ["## " + t("risk"), ""]
    if not risk.get("available"):
        L += [t("risk_na", r=risk.get("reason")), ""]
    else:
        L += [t("risk_period", a=risk["start"], b=risk["end"], n=risk["returns_used"]), "", "| | |", "|---|---:|",
              "| {} | {} |".format(t("k_vol"), _w(risk["volatility_pct"], lang)),
              "| {} | {} |".format(t("k_evol"), _w(risk["equity_volatility_pct"], lang)),
              "| {} | {} |".format(t("k_wvol"), _w(risk["wig_volatility_pct"], lang)),
              "| {} | {} |".format(t("k_beta"), num(risk["beta"], 2, lang)),
              "| {} | {} ({}) |".format(t("k_var"), pln(risk["var_pln"], 0, lang), pct(risk["var_pct"], 2, lang)),
              "| {} | {} ({}) |".format(t("k_es"), pln(risk["es_pln"], 0, lang), pct(risk["es_pct"], 2, lang)),
              "| {} | {} |".format(t("k_var20", h=risk["var_horizon"]), pln(risk["var_horizon_pln"], 0, lang)),
              "| {} | {} |".format(t("k_mdd"), _w(risk["max_drawdown_pct"], lang)),
              "| {} | {} |".format(t("k_cdd"), _w(risk["current_drawdown_pct"], lang)),
              "| {} | {} |".format(t("k_ret"), pct(risk["return_pct"], 1, lang, signed=True)),
              "| {} | {} |".format(t("k_wret"), pct(risk["wig_return_pct"], 1, lang, signed=True)),
              "| {} | {} |".format(t("k_wmdd"), _w(risk["wig_max_drawdown_pct"], lang)),
              "| {} | {} pp |".format(t("k_excess"), num(risk["excess_return_pp"], 1, lang)), "",
              "_" + t("var_caveat") + "_", ""]
        if risk["excluded"]:
            L += [t("excluded", x=", ".join(risk["excluded"]), w=_w(risk["excluded_weight_pct"], lang)), ""]
        L += ["### " + t("per_pos"), "", "| {} | {} | {} | {} |".format(t("c_ticker"), t("c_vol"), t("c_beta"), t("c_ret")),
              "|---|---:|---:|---:|"]
        for tk, v in risk["per_position"].items():
            L.append("| {} | {} | {} | {} |".format(tk, _w(v["volatility_pct"], lang), num(v["beta"], 2, lang),
                                                   pct(v["return_pct"], 1, lang, signed=True)))
        L += ["", "### " + t("corr"), ""]
        inc = risk["included"]
        if 2 <= len(inc) <= 10:
            L += ["| | " + " | ".join(inc) + " |", "|---|" + "---:|" * len(inc)]
            for a in inc:
                L.append("| {} | ".format(a) + " | ".join(num(risk["correlation"][a][b], 2, lang) for b in inc) + " |")
            L.append("")
        if risk["top_pairs"]:
            L += ["**{}:** ".format(t("corr_top")) + "; ".join("{}–{} {}".format(x["a"], x["b"], num(x["corr"], 2, lang))
                                                             for x in risk["top_pairs"][:5]), ""]
        if not risk["high_pairs"]:
            L += [t("corr_none", c=num(cfg["corr_high"], 1, lang)), ""]
    # liquidity
    L += ["## " + t("liq"), "", t("liq_txt", n=cfg["liquidity_window"], l=num(cfg["max_days_to_exit"], 0, lang)), "",
          "| {} | {} | {} | {} |".format(t("c_ticker"), t("c_value"), t("c_medto"), t("c_dte")), "|---|---:|---:|---:|"]
    for p in sorted(pos, key=lambda p: -(p["days_to_exit"] if p["days_to_exit"] is not None else math.inf)):
        L.append("| {} | {} | {} | {} |".format(p["ticker"], pln(p["value"], 0, lang), pln(p["median_turnover_pln"], 0, lang),
                                               _dte(p["days_to_exit"], lang)))
    L.append("")
    # heat
    heat = r["heat"]
    L += ["## " + t("heat"), ""]
    if heat["tracked"]:
        L.append(t("heat_txt", r=pln(heat["open_risk"], 0, lang), p=_w(heat["heat_pct"], lang)))
        if heat["positions_without_stop"]:
            L += ["", t("heat_missing", x=", ".join(heat["positions_without_stop"]))]
    else:
        L.append(t("heat_na"))
    L.append("")
    # notes
    L += ["## " + t("notes"), "", "- " + t("n_avg_journal" if r["source"]["type"] == "journal" else "n_avg_csv"),
          "- " + t("n_adjusted"), "- " + t("n_sector"), "- " + t("n_tax")]
    if r["aliases"]:
        L.append("- " + t("n_alias", x=", ".join(r["aliases"])))
    L.append("- **{}:** ".format(t("thresholds")) + t("thr_txt", p=num(cfg["max_position_pct"], 0, lang),
                                                     s=num(cfg["max_sector_pct"], 0, lang), h=num(cfg["hhi_high"], 2, lang),
                                                     d=num(cfg["max_days_to_exit"], 0, lang), pa=num(cfg["participation_pct"], 0, lang),
                                                     c=num(cfg["corr_high"], 1, lang)))
    L += ["", "## " + t("sources"), ""] + ["- {} (sesja / session {})".format(s, prov["as_of"]) for s in prov["sources"]]
    L += ["", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _nonneg(text: str) -> float:
    try:
        v = pe.parse_number(text)
    except pe.PortfolioError:
        v = None
    if v is None or v < 0:
        raise argparse.ArgumentTypeError("must be a non-negative number")
    return v


def _date(text: str) -> str:
    try:
        dt.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD")
    return text


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Health check of a GPW portfolio (PL/EN). Never places orders.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="Holdings CSV: ticker,shares,avg_price[,account_type][,stop] (';' and decimal commas OK)")
    src.add_argument("--from-journal", action="store_true", help="Use ACTIVE positions from gpw-trade-journal")
    p.add_argument("--state-dir", help="Journal directory (default ~/.gpw-trading-skills/journal or $GPW_JOURNAL_DIR)")
    p.add_argument("--cash", type=_nonneg, default=0.0, help="Cash in PLN held in the portfolio (default 0)")
    p.add_argument("--lookback", type=int, default=pe.THRESHOLDS["lookback"], help="Sessions for risk stats (default 252 = 1 year)")
    p.add_argument("--as-of", type=_date, help="Analyse as of this session date (YYYY-MM-DD)")
    p.add_argument("--offline", action="store_true", help="Use only the local GPW cache")
    p.add_argument("--cache-dir", help="GPW cache directory (default ~/.cache/gpw-trading-skills)")
    p.add_argument("--lang", choices=["pl", "en"], default="pl")
    p.add_argument("--output-dir", default=".", help="Where to write JSON + Markdown")
    p.add_argument("--no-files", action="store_true", help="Print Markdown only, write no files")
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 60 <= args.lookback <= 480:
        parser.error("--lookback must be between 60 and 480 sessions")
    try:
        result = run(args)
    except (pe.PortfolioError, gpw_archive.GPWFetchError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    md = render_markdown(result, args.lang)
    if args.no_files:
        print(md)
        return 0
    out = Path(args.output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    jp, mp = out / "{}_{}.json".format(PREFIX, stamp), out / "{}_{}.md".format(PREFIX, stamp)
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(md, encoding="utf-8")
    print(md)
    print("JSON: {}\nMarkdown: {}".format(jp, mp), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
