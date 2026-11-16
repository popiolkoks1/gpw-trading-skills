#!/usr/bin/env python3
"""GPW Risk Lab - CLI.

"What can happen to my portfolio, and how to protect it":

    python3 scripts/gpw_risk_lab.py stress --positions PKOBP:100,KGHM:30 --cash 10000
    python3 scripts/gpw_risk_lab.py hedge  --csv holdings.csv
    python3 scripts/gpw_risk_lab.py all    --journal --max-loss-pct 20 --lang en

Modes: ``stress`` (historical replays + factor shocks + liquidity under
stress), ``hedge`` (FW20 futures hedge), ``all`` (both; stress losses are
also shown with the hedge applied). The maths lives in risk_lab_engine.py
(pure, tested); data loading in risk_lab_data.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import constituents, gpw_archive, journal_io, panel, plcal, provenance  # noqa: E402
from gpwlib.constituents import SECTOR_INDICES  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct, pln  # noqa: E402
from gpwlib.indicators import median  # noqa: E402

import risk_lab_data as rd  # noqa: E402
import risk_lab_engine as re_  # noqa: E402
import risk_lab_portfolio as rp  # noqa: E402

VERSION = "1.0.0"
PREFIX = "gpw_risk_lab"
SKILL = "gpw-risk-lab"
# FW20 multiplier per the GPW standard (Uchwała 980/2013 z późn. zm., version 06.2026): 20 zł per point.
FW20_MULTIPLIER_SPEC = 20
SIZE_LABELS = {"WIG20": "WIG20", "MWIG40": "mWIG40", "SWIG80": "sWIG80", "WIG": "WIG"}
METHOD_CODES = {"actual": "A", "proxy_sector": "S", "proxy_size": "R", "proxy_wig": "W", "unavailable": "–"}

STRINGS = {
    "title": {"pl": "GPW Risk Lab – test warunków skrajnych i zabezpieczenie", "en": "GPW Risk Lab – stress test and hedge"},
    "as_of": {"pl": "Dane na sesję", "en": "Data as of session"},
    "stale": {"pl": "⚠️ UWAGA: dane rynkowe są nieaktualne o {n} sesji – liczby mogą nie odpowiadać dzisiejszym cenom.",
              "en": "⚠️ WARNING: market data is {n} session(s) old – figures may not match today's prices."},
    "in_short": {"pl": "W skrócie", "en": "In short"},
    "s_value": {"pl": "Portfel: {total} (akcje/ETF {eq}, gotówka {cash}).",
                "en": "Portfolio: {total} (stocks/ETFs {eq}, cash {cash})."},
    "s_gfc": {"pl": "W scenariuszu „{label}” Twój portfel straciłby ~{loss} ({ret}).",
              "en": "In the “{label}” scenario your portfolio would have lost ~{loss} ({ret})."},
    "s_gain": {"pl": "W scenariuszu „{label}” Twój portfel zyskałby ~{gain} ({ret}).",
               "en": "In the “{label}” scenario your portfolio would have gained ~{gain} ({ret})."},
    "s_worst": {"pl": "Najgorszy scenariusz to „{label}”: ~{loss} ({ret}); najwięcej traci {ticker}.",
                "en": "The worst scenario is “{label}”: ~{loss} ({ret}); {ticker} loses the most."},
    "s_tol_ok": {"pl": "Żaden scenariusz nie przekracza Twojej granicy straty {lim}.",
                 "en": "No scenario exceeds your loss limit of {lim}."},
    "s_tol_bad": {"pl": "{n} scenariusz(e/y) przekracza(ją) Twoją granicę straty {lim}; najgorszy o {pp} pp. Aby zmieścić się w limicie w najgorszym z nich, część akcji w portfelu musiałaby być mniejsza o ~{cut}% (np. więcej gotówki lub zabezpieczenie).",
                  "en": "{n} scenario(s) exceed your loss limit of {lim}; the worst by {pp} pp. To stay within the limit in the worst one, the equity part would need to be ~{cut}% smaller (e.g. more cash or a hedge)."},
    "s_liq": {"pl": "Płynność w stresie: przy połowie zwykłych obrotów i 20% udziału w obrocie sprzedasz {d1} portfela w 1 sesję i {d5} w 5 sesji; najdłużej trwa wyjście z {ticker} ({days} sesji).",
              "en": "Liquidity under stress: at half the usual turnover and 20% participation you could sell {d1} of the portfolio in 1 session and {d5} in 5 sessions; {ticker} takes longest ({days} sessions)."},
    "s_hedge": {"pl": "Pełne zabezpieczenie kontraktami FW20 to {exact} kontraktu (beta {beta} wobec WIG20); w praktyce {floor} szt. (zaokrąglenie w dół) zabezpiecza {hr} ryzyka rynkowego. {res}% wahań portfela nie wynika z WIG20 i kontrakt ich nie usunie.",
                "en": "A full FW20 hedge is {exact} contracts (beta {beta} vs WIG20); in practice {floor} (rounded down) covers {hr} of the market risk. {res}% of the portfolio's variance is not explained by WIG20 and futures cannot remove it."},
    "s_small": {"pl": "Portfel jest mniejszy niż wartość jednego kontraktu ({per}) – zobacz alternatywy poniżej (ETF odwrotny na WIG20 albo więcej gotówki).",
                "en": "The portfolio is smaller than one contract ({per}) – see the alternatives below (inverse WIG20 ETF or more cash)."},
    # --- sections
    "h_hist": {"pl": "Powtórka historycznych kryzysów (dzisiejszy portfel)", "en": "Historical replays (today's portfolio)"},
    "h_factor": {"pl": "Hipotetyczne szoki czynnikowe", "en": "Hypothetical factor shocks"},
    "h_methods": {"pl": "Jak policzono każdą pozycję w kryzysach historycznych", "en": "How each position was computed in the historical replays"},
    "methods_legend": {"pl": "A = rzeczywisty kurs spółki; S = indeks sektora × beta; R = WIG20/mWIG40/sWIG80 × beta; W = WIG × beta; – = brak danych. Beta = wrażliwość spółki na indeks z ostatniego roku.",
                       "en": "A = the stock's actual price; S = sector index × beta; R = WIG20/mWIG40/sWIG80 × beta; W = WIG × beta; – = no data. Beta = the stock's sensitivity to the index over the last year."},
    "h_sens": {"pl": "Wrażliwości czynnikowe (z danych)", "en": "Factor sensitivities (estimated from data)"},
    "sens_note": {"pl": "Złoty: tygodniowe zmiany indeksu vs tygodniowe zmiany EURPLN (NBP). Stopy: średnie miesięczne indeksu vs zmiana rentowności 10-latek (ECB), na +1 pp. |t| < 2 = zależność statystycznie słaba – traktuj ostrożnie.",
                  "en": "Zloty: weekly index returns vs weekly EURPLN changes (NBP). Rates: monthly-average index returns vs change of the 10-year yield (ECB), per +1 pp. |t| < 2 = statistically weak – treat with caution."},
    "h_liq": {"pl": "Płynność w stresie", "en": "Liquidity under stress"},
    "h_tol": {"pl": "Twoja tolerancja straty", "en": "Your loss tolerance"},
    "h_hedge": {"pl": "Zabezpieczenie kontraktami FW20", "en": "FW20 futures hedge"},
    "h_contracts": {"pl": "Notowane serie FW20", "en": "Listed FW20 series"},
    "h_bt": {"pl": "Skuteczność zabezpieczenia – test na ostatnim roku", "en": "Hedge effectiveness – last-year backtest"},
    "bt_note": {"pl": "Seria ciągła FW20: zawsze najbliższa seria do jej ostatniego dnia obrotu, potem następna; zwrot liczony na tej samej serii (bez sztucznego skoku przy rolowaniu). Dzisiejsze wagi i liczba kontraktów zastosowane wstecz – to test w próbie, nie prognoza.",
                "en": "Continuous FW20: always the nearest series up to its last trading day, then the next one; each return uses the same series on both days (no artificial roll gap). Today's weights and contract count applied backwards – an in-sample test, not a forecast."},
    "h_alt": {"pl": "Alternatywy dla małego portfela", "en": "Alternatives for a small portfolio"},
    "h_tax": {"pl": "Podatki i rachunki", "en": "Tax and account rules"},
    "h_assume": {"pl": "Założenia i ograniczenia", "en": "Assumptions and limits"},
    "h_sources": {"pl": "Źródła", "en": "Sources"},
    # --- columns
    "c_scen": {"pl": "Scenariusz", "en": "Scenario"},
    "c_window": {"pl": "Okno", "en": "Window"},
    "c_wig": {"pl": "WIG", "en": "WIG"},
    "c_port": {"pl": "Portfel", "en": "Portfolio"},
    "c_loss": {"pl": "Strata", "en": "Loss"},
    "c_loss_h": {"pl": "Strata z FW20", "en": "Loss with FW20"},
    "c_worst": {"pl": "Najwięcej tracą", "en": "Worst positions"},
    "c_ticker": {"pl": "Spółka", "en": "Ticker"},
    "c_value": {"pl": "Wartość", "en": "Value"},
    "c_sector": {"pl": "Sektor", "en": "Sector"},
    "c_size": {"pl": "Segment", "en": "Segment"},
    "c_beta": {"pl": "Beta WIG", "en": "Beta WIG"},
    "c_index": {"pl": "Indeks", "en": "Index"},
    "c_fx": {"pl": "EURPLN +1%", "en": "EURPLN +1%"},
    "c_rates": {"pl": "Stopy +1 pp", "en": "Rates +1 pp"},
    "c_tilt": {"pl": "Różnica vs WIG (eksporter > 0)", "en": "Tilt vs WIG (exporter > 0)"},
    "c_med": {"pl": "Mediana obrotu", "en": "Median turnover"},
    "c_days_n": {"pl": "Dni (normalnie)", "en": "Days (normal)"},
    "c_days_s": {"pl": "Dni (stres)", "en": "Days (stress)"},
    "c_symbol": {"pl": "Seria", "en": "Series"},
    "c_price": {"pl": "Kurs rozl.", "en": "Settlement"},
    "c_ltd": {"pl": "Ostatni dzień obrotu", "en": "Last trading day"},
    "c_sess": {"pl": "Sesji do wygaśn.", "en": "Sessions to expiry"},
    "c_turn": {"pl": "Obrót", "en": "Turnover"},
    "c_roll": {"pl": "Okno rolowania od", "en": "Roll window from"},
    "c_variant": {"pl": "Wariant", "en": "Variant"},
    "c_ret": {"pl": "Zwrot", "en": "Return"},
    "c_vol": {"pl": "Zmienność roczna", "en": "Annual volatility"},
    "c_mdd": {"pl": "Maks. obsunięcie", "en": "Max drawdown"},
    "c_wday": {"pl": "Najgorszy dzień", "en": "Worst day"},
    "skipped": {"pl": "pominięty: {r}", "en": "skipped: {r}"},
    "r_no_index": {"pl": "brak historii WIG w cache dla tego okna (uruchom z --fetch-history, ~1 h za pierwszym razem)",
                   "en": "no WIG history in the cache for this window (run with --fetch-history, ~1 h the first time)"},
    "r_no_fx": {"pl": "brak kursów EURPLN z NBP", "en": "no EURPLN rates from NBP"},
    "r_no_rates": {"pl": "brak rentowności z ECB", "en": "no yields from ECB"},
    "r_unknown_index": {"pl": "brak indeksu {i} w danych", "en": "index {i} not in the data"},
    "incomplete": {"pl": "niepełny: brak danych dla {t}", "en": "incomplete: no data for {t}"},
    # --- hedge lines
    "l_beta": {"pl": "Beta portfela akcji wobec WIG20: **{b20}** (R² {r2}); wobec WIG: {bw}. Minimalno-wariancyjny współczynnik wobec FW20 (Ederington 1979): {hmv}.",
               "en": "Equity beta vs WIG20: **{b20}** (R² {r2}); vs WIG: {bw}. Minimum-variance hedge ratio vs FW20 (Ederington 1979): {hmv}."},
    "l_n": {"pl": "Kontrakty: N = β × V / (F × 20 zł) = {b} × {v} / ({f} × {m} zł) = **{exact}** → w dół **{floor}** (pokrycie {hrf}), w górę {ceil} (pokrycie {hrc}). Wartość nominalna {floor} szt.: {notional}.",
            "en": "Contracts: N = β × V / (F × 20 PLN) = {b} × {v} / ({f} × {m} PLN) = **{exact}** → round down **{floor}** (covers {hrf}), round up {ceil} (covers {hrc}). Notional of {floor}: {notional}."},
    "l_resid": {"pl": "Ryzyko rezydualne (niezwiązane z WIG20): {res}% wariancji, ~{rv} zmienności rocznej z {tv} łącznie. Spółki spoza WIG20 (mWIG40, sWIG80) podnoszą tę część.",
                "en": "Residual (non-WIG20) risk: {res}% of variance, ~{rv} of annual volatility out of {tv}. Stocks outside WIG20 (mWIG40, sWIG80) raise this part."},
    "l_basis": {"pl": "Baza: kontrakt {sym} {f} vs WIG20 {s} → {b} pkt ({bp}). Przy wygaśnięciu baza spada do zera.",
                "en": "Basis: {sym} {f} vs WIG20 {s} → {b} pts ({bp}). It converges to zero at expiry."},
    "l_margin": {"pl": "Depozyt (szacunek modelowy, NIE kwota brokera): ~{pct} wartości kontraktu ≈ {pln} na kontrakt, razem ~{tot} dla {n} szt. Poziom depozytu ustala Twój broker (standard GPW) – sprawdź u brokera.",
                 "en": "Margin (model estimate, NOT your broker's figure): ~{pct} of contract value ≈ {pln} per contract, ~{tot} for {n}. Your broker sets the investor margin (GPW standard) – check with your broker."},
    "l_margin_na": {"pl": "Depozyt: za mało danych do szacunku – sprawdź u brokera.", "en": "Margin: not enough data for an estimate – check with your broker."},
    "l_mult": {"pl": "Mnożnik: {m} zł za punkt (z symbolu serii; zgodny ze standardem GPW 06.2026).",
               "en": "Multiplier: {m} PLN per point (from the series symbol; matches the GPW standard 06.2026)."},
    "l_mult_bad": {"pl": "⚠️ Mnożnik z symbolu ({m}) różni się od standardu GPW (20 zł) – sprawdź specyfikację.",
                   "en": "⚠️ The multiplier in the symbol ({m}) differs from the GPW standard (20 PLN) – check the specification."},
    "l_no_fut": {"pl": "Brak notowań FW20 na sesję {d} w archiwum – nie można policzyć zabezpieczenia.",
                 "en": "No FW20 quotes for session {d} in the archive – cannot size the hedge."},
    "l_etf": {"pl": "{name} (ETF odwrotny do {idx}, dzienny reset): kurs {price}, mediana obrotu {turn}/sesję; ekspozycję β × V ≈ {target} pokrywa ~{units} jedn. Uwaga: przy dłuższym trzymaniu wynik odbiega od −1× indeksu (efekt dziennego resetu).",
              "en": "{name} (inverse {idx} ETF, daily reset): price {price}, median turnover {turn}/session; β × V ≈ {target} needs ~{units} units. Note: over longer holding periods it drifts from −1× the index (daily reset effect)."},
    "l_cash": {"pl": "Więcej gotówki: strata w scenariuszach rynkowych maleje proporcjonalnie do udziału akcji (gotówka ma zwrot 0). Np. o połowę mniej akcji = o połowę mniejsza strata w scenariuszu WIG −30%.",
               "en": "More cash: losses in market scenarios fall in proportion to the equity share (cash returns 0). E.g. half the equity = half the loss in the WIG −30% scenario."},
    "tax_pit": {"pl": "Wynik na kontraktach terminowych to przychód z kapitałów pieniężnych (realizacja praw z pochodnych instrumentów finansowych, art. 17 ust. 1 pkt 10 i art. 30b ust. 1 ustawy o PIT): 19% podatku, rozliczany w PIT-38 razem z akcjami, więc strata na kontraktach pomniejsza zysk z akcji w tym samym roku (i odwrotnie); nierozliczoną stratę można odliczać przez 5 kolejnych lat (art. 9 ust. 3).",
                "en": "Futures P&L is capital income (rights from derivative financial instruments, art. 17(1)(10) and art. 30b(1) of the PIT Act): 19% tax, reported on PIT-38 together with shares, so a futures loss offsets share gains in the same year (and vice versa); an unused loss can be carried forward for 5 years (art. 9(3))."},
    "tax_ike": {"pl": "IKE/IKZE: ustawa o IKE i IKZE (art. 27 ust. 2) dopuszcza na IKE/IKZE instrumenty niebędące papierami wartościowymi wyłącznie w celu ograniczenia ryzyka papierów z tego konta i tylko jeśli umowa z brokerem to przewiduje; w praktyce brokerzy zwykle nie oferują kontraktów na IKE/IKZE – sprawdź u swojego. Jeśli akcje są na IKE/IKZE, a kontrakt na zwykłym rachunku, zysk z kontraktu jest opodatkowany 19%, a strata pomniejsza tylko zyski ze zwykłego rachunku.",
                "en": "IKE/IKZE: the IKE/IKZE Act (art. 27(2)) allows non-security instruments on IKE/IKZE only to limit the risk of securities held on that account and only if the broker's agreement provides for it; in practice brokers usually do not offer futures on IKE/IKZE – check with yours. If the shares sit on IKE/IKZE and the futures on a regular account, a futures gain is taxed at 19% while a futures loss only offsets regular-account gains."},
    "tax_wrapped": {"pl": "{p} wartości Twoich akcji jest na IKE/IKZE/OKI – tu asymetria podatkowa zabezpieczenia na zwykłym rachunku dotyczy Cię bezpośrednio.",
                    "en": "{p} of your equity is held on IKE/IKZE/OKI – the tax asymmetry of a hedge on a regular account applies to you directly."},
    "tax_oki": {"pl": "OKI (od 2027): dochody z aktywów OKI są poza PIT; kontrakt na zwykłym rachunku rozliczasz osobno w PIT-38.",
                "en": "OKI (from 2027): income from OKI assets is outside PIT; futures on a regular account are reported separately on PIT-38."},
    "assume": {"pl": ["Powtórki historyczne stosują DZISIEJSZE wagi do zwrotów z przeszłych okien; spółki notowane w oknie mają rzeczywisty zwrot (skorygowany o dywidendy i splity), pozostałe – zwrot indeksu × beta z ostatniego roku (łańcuch: sektor → WIG20/mWIG40/sWIG80 → WIG).",
                      "Przynależność do sektorów i segmentów to dzisiejsze portfele GPW Benchmark (nie stan z dnia kryzysu).",
                      "Szoki czynnikowe to warunkowe oczekiwanie (beta × szok) – realne spadki w kryzysie bywają większe, bo korelacje rosną.",
                      "Wrażliwości na złotego i stopy pochodzą z regresji na danych i zawierają reakcję całego rynku (np. ucieczka od ryzyka przy słabym złotym).",
                      "Gotówka ma zwrot 0; nie uwzględniono dywidend w trakcie scenariusza ani prowizji.",
                      "To nie jest prognoza: przyszłe kryzysy będą inne."],
               "en": ["Historical replays apply TODAY's weights to past windows; stocks listed in the window use their actual (dividend/split adjusted) return, the rest index return × last-year beta (chain: sector → WIG20/mWIG40/sWIG80 → WIG).",
                      "Sector and segment membership = current GPW Benchmark portfolios (not point-in-time).",
                      "Factor shocks are conditional expectations (beta × shock) – real crisis falls can be larger because correlations rise.",
                      "Zloty and rate sensitivities are data regressions and include the whole-market reaction (e.g. risk-off when the zloty weakens).",
                      "Cash returns 0; dividends during a scenario and commissions are ignored.",
                      "This is not a forecast: future crises will differ."]},
}


def sector_label(key: Optional[str], lang: str) -> str:
    if key and key in SECTOR_INDICES:
        return SECTOR_INDICES[key][2] if lang == "pl" else SECTOR_INDICES[key][1]
    return "—"


def index_label(key: Optional[str], lang: str) -> str:
    if not key:
        return "—"
    if key in SECTOR_INDICES:
        return "{} ({})".format(key, sector_label(key, lang))
    return SIZE_LABELS.get(key, key)


# --------------------------------------------------------------------------
# Loading and resolution
# --------------------------------------------------------------------------

def read_holdings(args) -> (List[Dict], Dict):
    if args.journal:
        state = Path(args.state_dir).expanduser() if args.state_dir else journal_io.default_state_dir()
        return rp.holdings_from_journal(journal_io.open_positions(journal_io.load_theses(state))), \
            {"type": "journal", "path": str(state)}
    if args.positions:
        return rp.parse_positions_arg(args.positions), {"type": "positions", "text": args.positions}
    path = Path(args.csv).expanduser()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise rp.PortfolioError("cannot read {}: {}".format(path, exc))
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1250")  # older Polish broker exports (Windows-1250)
    return rp.parse_holdings_csv(text), {"type": "csv", "path": str(path)}


def _map(s) -> Dict[str, float]:
    return dict(zip(s.dates, s.close))


def resolve(holdings, end, cache_dir, offline, lookback):
    n = lookback + 10
    print("Loading GPW share sessions...", file=sys.stderr)
    shares = gpw_archive.load_sessions(n, end=end, cache_dir=cache_dir, offline=offline, progress=True)
    if not shares:
        raise gpw_archive.GPWFetchError("no GPW share sessions available (offline cache empty or network error)")
    pnl = panel.build_panel(shares)
    resolved, aliases, missing = {}, [], []
    for h in holdings:
        name, note = rp.resolve_ticker(h["ticker"], pnl.keys())
        if name:
            resolved[h["ticker"]] = (name, "share")
            if note:
                aliases.append(note)
        else:
            missing.append(h["ticker"])
    # ETFs: full history (may download) only when a holding is not a share; otherwise the
    # cached last ~70 sessions are enough to list inverse ETFs as hedge alternatives.
    if missing:
        etf_sessions = gpw_archive.load_sessions(n, end=end, type_id=gpw_archive.TYPE_ETF, cache_dir=cache_dir,
                                                 offline=offline, progress=True)
    else:
        etf_sessions = gpw_archive.load_sessions(70, end=end, type_id=gpw_archive.TYPE_ETF, cache_dir=cache_dir,
                                                 offline=True, progress=False)
    etf_pnl = panel.build_panel(etf_sessions) if etf_sessions else {}
    still = []
    for t in missing:
        name, note = rp.resolve_ticker(t, etf_pnl.keys())
        if name:
            resolved[t] = (name, "etf")
            if note:
                aliases.append(note)
        else:
            still.append(t)
    if still:
        msgs = []
        for t in still:
            cm = rp.close_matches(t, list(pnl.keys()) + list(etf_pnl.keys()))
            msgs.append("'{}' not found in the GPW archive (shares and ETFs); close matches: {}".format(
                t, ", ".join(cm) if cm else "none"))
        raise rp.PortfolioError("; ".join(msgs) + ". Use the GPW short name, e.g. PKOBP, PKNORLEN, CDPROJEKT.")
    return {"shares": shares, "panel": pnl, "etf_panel": etf_pnl, "etf_sessions": etf_sessions,
            "resolved": resolved, "aliases": aliases}


def wig20_sector_weight(sector: str, sector_map: Dict[str, str], cache_dir, offline) -> Optional[float]:
    """Share of WIG20 (current GPW Benchmark portfolio weights) belonging to one sector index."""
    try:
        members = constituents.fetch_portfolio(constituents.SIZE_INDICES["WIG20"], cache_dir, offline)["members"]
    except gpw_archive.GPWFetchError:
        return None
    tot = sum(m.get("weight_pct") or 0.0 for m in members)
    if tot <= 0:
        return None
    return sum(m.get("weight_pct") or 0.0 for m in members if sector_map.get(m["name"]) == sector) / tot


def build_positions(holdings, mk, sector_map, size_map):
    merged: Dict[str, Dict] = {}
    for h in holdings:
        name, kind = mk["resolved"][h["ticker"]]
        s = (mk["panel"] if kind == "share" else mk["etf_panel"])[name]
        m = merged.get(name)
        if m is None:
            merged[name] = {"ticker": name, "input_ticker": h["ticker"], "kind": kind, "shares": h["shares"],
                            "accounts": dict(h["accounts"]), "last": s.close[-1], "last_date": s.dates[-1],
                            "sector": sector_map.get(name) if kind == "share" else None,
                            "size": size_map.get(name) if kind == "share" else None}
        else:
            m["shares"] += h["shares"]
            for a, n in h["accounts"].items():
                m["accounts"][a] = m["accounts"].get(a, 0) + n
    out = list(merged.values())
    for p in out:
        p["value"] = round(p["shares"] * p["last"], 2)
    out.sort(key=lambda p: -p["value"])
    return out


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def _pct(x):
    return None if x is None else round(x * 100, 2)


def run(args) -> Dict:
    t0 = time.time()
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else None
    end = dt.date.fromisoformat(args.as_of) if args.as_of else None
    holdings, src = read_holdings(args)
    scen = rd.load_scenarios(args.scenarios)
    if args.only:
        keep = {x.strip() for x in args.only.split(",") if x.strip()}
        ids = {s["id"] for g in ("historical", "factor") for s in scen.get(g, [])}
        unknown = keep - ids
        if unknown:
            raise rp.PortfolioError("--only: unknown scenario id(s) {}; known: {}".format(
                ", ".join(sorted(unknown)), ", ".join(sorted(ids))))
        for g in ("historical", "factor"):
            scen[g] = [s for s in scen.get(g, []) if s["id"] in keep]
    cfg = {"lookback": args.lookback, "participation": args.participation / 100.0,
           "stress_turnover_factor": args.stress_turnover / 100.0}

    mk = resolve(holdings, end, cache_dir, args.offline, args.lookback)
    as_of = dt.date.fromisoformat(mk["shares"][-1]["date"])
    sector_map = constituents.sector_membership(cache_dir, offline=args.offline)
    size_map = constituents.size_membership(cache_dir, offline=args.offline)
    positions = build_positions(holdings, mk, sector_map, size_map)
    equity = sum(p["value"] for p in positions)
    total = equity + args.cash
    recent_dates = [s["date"] for s in mk["shares"]]

    # --- index history -------------------------------------------------------------
    print("Loading GPW index history (2007-)...", file=sys.stderr)
    fetch_before = None if args.fetch_history else as_of - dt.timedelta(days=int(args.lookback * 1.6) + 30)
    idx_sessions, idx_missing = rd.load_range(rd.HISTORY_START, as_of, gpw_archive.TYPE_INDICES, cache_dir,
                                              args.offline, fetch_before=fetch_before)
    imaps = rd.index_maps(idx_sessions)
    if "WIG" not in imaps or "WIG20" not in imaps:
        raise gpw_archive.GPWFetchError("WIG / WIG20 index history unavailable")
    wig_dates = sorted(imaps["WIG"])
    wig_closes = [imaps["WIG"][d] for d in wig_dates]

    stock_maps = {}
    for p in positions:
        s = (mk["panel"] if p["kind"] == "share" else mk["etf_panel"])[p["ticker"]]
        stock_maps[p["ticker"]] = _map(s)

    needed_idx = {"WIG", "WIG20"}
    for p in positions:
        for _m, idx in re_.proxy_chain(p["sector"], p["size"]):
            needed_idx.add(idx)
    for s in scen.get("factor", []):
        if s.get("index"):
            needed_idx.add(s["index"].upper())
    betas = {p["ticker"]: {idx: re_.beta_of(stock_maps[p["ticker"]], imaps.get(idx, {}), recent_dates, cfg)
                           for idx in needed_idx} for p in positions}
    for p in positions:
        p["beta_wig"] = betas[p["ticker"]]["WIG"]["beta"]
        p["beta_wig_default"] = betas[p["ticker"]]["WIG"]["default"]

    result = {"skill": SKILL, "version": VERSION, "lang": args.lang, "as_of": as_of.isoformat(), "mode": args.mode,
              "source": src, "aliases": mk["aliases"],
              "portfolio": {"total_value": round(total, 2), "equity_value": round(equity, 2), "cash": round(args.cash, 2),
                            "positions": [{k: p[k] for k in ("ticker", "input_ticker", "kind", "shares", "last", "last_date",
                                                              "value", "sector", "size", "accounts", "beta_wig",
                                                              "beta_wig_default")} for p in positions]}}
    notes = []
    sources = [rd.SOURCES["archive"], rd.SOURCES["benchmark"]]
    w20_map = imaps["WIG20"]

    # --- stress ---------------------------------------------------------------------
    stress = None
    if args.mode in ("stress", "all"):
        scenarios = []
        for spec in scen.get("historical", []):
            item = {"id": spec["id"], "group": "historical", "kind": spec["kind"], "label": spec.get("label", {})}
            w = re_.resolve_window(spec, wig_dates, wig_closes, cfg)
            if w is None:
                item.update(skipped=True, reason="no_index_data", portfolio_return_pct=None, pln_loss=None,
                            worst_positions=[])
                scenarios.append(item)
                continue
            stock_rets = {}
            start_d, end_d = dt.date.fromisoformat(w["start"]), dt.date.fromisoformat(w["end"])
            if end_d >= rd.SHARES_MIN_DATE:
                sess, _ = rd.load_range(start_d - dt.timedelta(days=21), end_d, gpw_archive.TYPE_SHARES, cache_dir,
                                        args.offline, progress=True)
                wp = panel.build_panel(sess) if sess else {}
                esess, _ = rd.load_range(start_d - dt.timedelta(days=21), end_d, gpw_archive.TYPE_ETF, cache_dir,
                                         True, progress=False)
                ep = panel.build_panel(esess) if esess else {}
                for p in positions:
                    src_panel = wp if p["kind"] == "share" else ep
                    s = src_panel.get(p["ticker"])
                    stock_rets[p["ticker"]] = re_.window_return(_map(s), w["start"], w["end"], wig_dates, cfg) if s else None
            index_rets = {idx: re_.window_return(imaps.get(idx, {}), w["start"], w["end"], wig_dates, cfg)
                          for idx in needed_idx}
            index_daily = {idx: re_.index_daily_returns(imaps[idx], w["start"], w["end"])
                           for idx in needed_idx if index_rets.get(idx) is not None}
            pr = re_.historical_position_returns(positions, stock_rets, index_rets, betas, index_daily)
            agg = re_.aggregate(positions, pr, args.cash, cfg)
            item.update({"window": {"start": w["start"], "end": w["end"], "index_return_pct": _pct(w["index_return"]),
                                    "wig20_return_pct": _pct(index_rets.get("WIG20"))},
                         "skipped": False, **{k: agg[k] for k in ("portfolio_return_pct", "pln_loss", "pnl_pln",
                                                                   "value_after", "worst_positions", "complete", "missing")},
                         "positions": agg["positions"], "index_move_wig20": index_rets.get("WIG20")})
            scenarios.append(item)

        # factor sensitivities
        sens: Dict[str, Dict] = {}
        need_fx = any(s["kind"] == "fx" for s in scen.get("factor", []))
        need_rates = any(s["kind"] == "rates" for s in scen.get("factor", []))
        fx_idx = [i for i in sorted(needed_idx) if i in imaps]
        fx_problems, rate_problems = [], []
        if need_fx:
            print("Loading EURPLN (NBP)...", file=sys.stderr)
            eurpln, fx_problems = rd.nbp_eurpln(dt.date(2007, 1, 1), as_of, cache_dir, args.offline)
            if eurpln:
                sources.append(rd.SOURCES["nbp"])
                for i in fx_idx:
                    sens.setdefault(i, {})["fx"] = re_.factor_sensitivity(imaps[i], eurpln, "W", "pct")
                wfx = (sens.get("WIG") or {}).get("fx")
                for i in fx_idx:
                    f = sens[i].get("fx")
                    if f and wfx:
                        # exporter / importer tilt: sensitivity relative to the whole market
                        f["tilt_vs_wig"] = round(f["beta"] - wfx["beta"], 4)
        if need_rates:
            print("Loading PL 10Y yield (ECB)...", file=sys.stderr)
            pl10y, rate_problems = rd.ecb_pl10y(cache_dir, args.offline)
            if pl10y:
                sources.append(rd.SOURCES["ecb"])
                for i in fx_idx:
                    monthly: Dict[str, List[float]] = {}
                    for d, v in imaps[i].items():
                        monthly.setdefault(d[:7] + "-01", []).append(v)
                    mm = {k: sum(v) / len(v) for k, v in monthly.items()}
                    sens.setdefault(i, {})["rates"] = re_.factor_sensitivity(mm, pl10y, "M", "diff", min_obs=36)
        for spec in scen.get("factor", []):
            spec = dict(spec)
            if spec.get("index"):
                spec["index"] = spec["index"].upper()
            item = {"id": spec["id"], "group": "factor", "kind": spec["kind"], "label": spec.get("label", {}),
                    "shock": {k: spec[k] for k in ("index", "shock_pct", "shock_bp", "spillover") if k in spec}}
            reason = None
            if spec["kind"] == "fx" and not any((sens.get(i) or {}).get("fx") for i in fx_idx):
                reason = "no_fx_data"
            if spec["kind"] == "rates" and not any((sens.get(i) or {}).get("rates") for i in fx_idx):
                reason = "no_rates_data"
            if spec["kind"] in ("market", "sector") and spec.get("index") not in imaps:
                reason = "unknown_index:" + str(spec.get("index"))
            if reason:
                item.update(skipped=True, reason=reason, portfolio_return_pct=None, pln_loss=None, worst_positions=[])
                scenarios.append(item)
                continue
            pr = re_.factor_position_returns(positions, spec, betas, sens)
            agg = re_.aggregate(positions, pr, args.cash, cfg)
            item.update({"skipped": False, **{k: agg[k] for k in ("portfolio_return_pct", "pln_loss", "pnl_pln",
                                                                   "value_after", "worst_positions", "complete", "missing")},
                         "positions": agg["positions"]})
            if spec["kind"] == "market":
                b = re_.ols(*re_.paired_returns(w20_map, imaps[spec["index"]], recent_dates[-(args.lookback + 1):])[:2])
                item["index_move_wig20"] = (b["beta"] if b else 1.0) * spec["shock_pct"] / 100.0
            elif spec["kind"] == "sector" and spec.get("spillover", "none") == "beta":
                b = re_.ols(*re_.paired_returns(w20_map, imaps[spec["index"]], recent_dates[-(args.lookback + 1):])[:2])
                item["index_move_wig20"] = (b["beta"] if b else 0.0) * spec["shock_pct"] / 100.0
            elif spec["kind"] == "sector":
                # no spillover: WIG20 moves only through the sector's own weight in WIG20
                wsec = wig20_sector_weight(spec["index"], sector_map, cache_dir, args.offline)
                if wsec is not None:
                    item["index_move_wig20"] = wsec * spec["shock_pct"] / 100.0
            elif spec["kind"] in ("fx", "rates"):
                s = (sens.get("WIG20") or {}).get(spec["kind"])
                if s:
                    shock = spec["shock_pct"] / 100.0 if spec["kind"] == "fx" else spec["shock_bp"] / 100.0
                    item["index_move_wig20"] = s["beta"] * shock
            scenarios.append(item)

        medians = {}
        for p in positions:
            s = (mk["panel"] if p["kind"] == "share" else mk["etf_panel"])[p["ticker"]]
            win = [v * 1000.0 for v in s.turnover_k[-re_.THRESHOLDS["liquidity_window"]:]]
            medians[p["ticker"]] = median(win) if win else None
        liq = re_.liquidity_stress(positions, medians, cfg)
        done = [s for s in scenarios if not s.get("skipped")]
        tol = re_.tolerance_check(done, args.max_loss_pct)
        worst = min(done, key=lambda s: s["portfolio_return_pct"]) if done else None
        stress = {"scenarios": scenarios, "liquidity": liq, "tolerance": tol,
                  "worst_scenario": worst["id"] if worst else None,
                  "sensitivities": {i: sens[i] for i in sorted(sens)},
                  "scenarios_file": scen["_path"],
                  "data_gaps": {"index_sessions_missing_in_cache": idx_missing, "fx": fx_problems[:5],
                                "rates": rate_problems[:5]}}
        if idx_missing:
            notes.append("{} probable index sessions 2007-{} are not in the local cache (run with --fetch-history to "
                         "backfill).".format(idx_missing, as_of.year))

    # --- hedge -------------------------------------------------------------------------
    hedge = None
    if args.mode in ("hedge", "all"):
        hedge = run_hedge(args, positions, stock_maps, imaps, recent_dates, as_of, equity, mk, cfg, cache_dir)
        sources.append(rd.SOURCES["fw20_spec"])
        if stress and hedge.get("contracts"):  # only a non-zero hedge changes scenario losses
            n, m = hedge["contracts"], hedge["multiplier"]
            f = hedge["futures_price"]
            for s in stress["scenarios"]:
                mv = s.pop("index_move_wig20", None)
                if s.get("skipped") or mv is None:
                    continue
                gain = -n * m * f * mv
                s["hedge_pnl_pln"] = round(gain, 2)
                s["pln_loss_hedged"] = round(s["pln_loss"] - gain, 2)
    if stress:
        for s in stress["scenarios"]:
            s.pop("index_move_wig20", None)
    result["stress"] = stress
    result["hedge"] = hedge
    result["runtime_s"] = round(time.time() - t0, 1)
    notes += ["Sector and size membership = current GPW Benchmark portfolios (not point-in-time).",
              "Betas: OLS on the last {} daily returns, clipped to [0, 3]; < 60 returns -> beta 1.0.".format(args.lookback)]
    if args.mode in ("hedge", "all"):
        notes.append("FW20 multiplier 20 PLN and last trading day (3rd Friday, else the previous session) per the GPW "
                     "WIG20 futures standard (06.2026).")
    result["data_provenance"] = provenance.build(
        as_of=as_of, sources=sources, sessions_used=len(mk["shares"]) + len(idx_sessions),
        universe_size=len(positions), notes=notes, point_in_time_membership=False, reference_date=end)
    return result


def run_hedge(args, positions, stock_maps, imaps, recent_dates, as_of, equity, mk, cfg, cache_dir) -> Dict:
    lookback = args.lookback
    win = recent_dates[-(lookback + 1):]
    w20, wig = imaps["WIG20"], imaps["WIG"]
    out: Dict = {"as_of": as_of.isoformat(), "equity_value": round(equity, 2)}
    if equity <= 0:
        out.update(contracts=None, reason="no_equity")
        return out
    # portfolio returns of positions with enough history, equal to today's weights
    weights = {p["ticker"]: p["value"] / equity for p in positions}
    included = [p["ticker"] for p in positions
                if sum(1 for d in win if d in stock_maps[p["ticker"]]) >= re_.THRESHOLDS["min_obs"] + 1]
    excluded = [p["ticker"] for p in positions if p["ticker"] not in included]
    common = [d for d in win if all(d in stock_maps[t] for t in included) and d in w20 and d in wig]
    w_inc = sum(weights[t] for t in included)
    port: Dict[str, float] = {}
    for a, b in zip(common, common[1:]):
        port[b] = sum(weights[t] / w_inc * (stock_maps[t][b] / stock_maps[t][a] - 1) for t in included) if w_inc else 0.0
    pd = sorted(port)
    r20 = {b: w20[b] / w20[a] - 1 for a, b in zip(common, common[1:])}
    rwig = {b: wig[b] / wig[a] - 1 for a, b in zip(common, common[1:])}
    rr20 = re_.residual_risk([port[d] for d in pd], [r20[d] for d in pd]) if len(pd) >= re_.THRESHOLDS["min_obs"] else None
    rrw = re_.residual_risk([port[d] for d in pd], [rwig[d] for d in pd]) if len(pd) >= re_.THRESHOLDS["min_obs"] else None
    if rr20 is None:
        out.update(contracts=None, reason="short_history")
        return out
    # excluded (short-history) positions count with beta 1.0 (documented default)
    beta20 = w_inc * rr20["beta"] + (1 - w_inc) * re_.THRESHOLDS["default_beta"]
    betaw = w_inc * rrw["beta"] + (1 - w_inc) * re_.THRESHOLDS["default_beta"] if rrw else None

    # futures
    print("Loading FW20 futures sessions (type 35)...", file=sys.stderr)
    fsess = gpw_archive.load_sessions(lookback + 5, end=as_of, type_id=gpw_archive.TYPE_FUTURES,
                                      cache_dir=cache_dir, offline=args.offline, progress=True)
    fs = rd.futures_sessions(fsess, "WIG20")
    today_rows = fs[-1][1] if fs and fs[-1][0] == as_of.isoformat() else []
    out.update(beta=round(beta20, 4), beta_wig=round(betaw, 4) if betaw is not None else None,
               r2_wig20=rr20["r2"], residual_risk_pct=rr20["residual_risk_pct"],
               residual_vol_pct=rr20["residual_vol_pct"], total_vol_pct=rr20["total_vol_pct"],
               regression_obs=rr20["n"], included=included, excluded_short_history=excluded,
               excluded_weight_pct=round((1 - w_inc) * 100, 2))
    if not today_rows:
        out.update(contracts=None, reason="no_futures_quotes")
        return out
    chain = re_.futures_chain(today_rows, as_of, plcal.is_probable_session, cfg)
    if not chain:
        out.update(contracts=None, reason="no_live_series")
        return out
    rec = next(c for c in chain if c.get("recommended"))
    mult = rec["multiplier"] or FW20_MULTIPLIER_SPEC
    cn = re_.contracts_needed(beta20, equity, rec["price"], mult)
    cont = re_.continuous_returns(fs, plcal.is_probable_session)
    frets = {d: v["ret"] for d, v in cont.items()}
    common_f = [d for d in pd if d in frets]
    mv = re_.ols([port[d] for d in common_f], [frets[d] for d in common_f]) if len(common_f) >= 60 else None
    spot = w20.get(as_of.isoformat())
    per = rec["price"] * mult
    bt = re_.hedge_backtest(port, frets, {
        "hedged_floor": cn["floor"] * per / equity,
        "hedged_exact": beta20,
    })
    margin = re_.margin_estimate([frets[d] for d in sorted(frets)], rec["price"], mult, cfg)
    alt = None
    if cn["below_one_contract"]:
        alt = inverse_etf_alternatives(mk, beta20 * equity, as_of, cache_dir, args.offline)
    out.update({
        "contracts": cn["floor"], "contracts_ceil": cn["ceil"], "contracts_exact": cn["exact"],
        "notional_pln": round(cn["floor"] * per, 2), "notional_per_contract": cn["notional_per_contract"],
        "target_notional_pln": cn["target_notional"], "hedge_ratio_floor": cn["hedge_ratio_floor"],
        "hedge_ratio_ceil": cn["hedge_ratio_ceil"], "below_one_contract": cn["below_one_contract"],
        "multiplier": mult, "multiplier_matches_spec": mult == FW20_MULTIPLIER_SPEC,
        "futures_symbol": rec["symbol"], "futures_price": rec["price"],
        "min_variance_ratio": round(mv["beta"], 4) if mv else None,
        "min_variance_contracts": round(mv["beta"] * equity / per, 3) if mv else None,
        "wig20_spot": spot,
        "basis_points": round(rec["price"] - spot, 2) if spot else None,
        "basis_pct": round((rec["price"] / spot - 1) * 100, 3) if spot else None,
        "series": chain,
        "roll_schedule": re_.roll_dates([(c["symbol"], c["year"], c["month"]) for c in chain[:2]], plcal.is_probable_session),
        "roll_method": "continuous series = nearest series held to its last trading day, then the next; returns on the same series (no roll gap)",
        "margin_estimate": margin, "backtest": bt, "alternatives": alt,
        "tax_wrapped_equity_pct": wrapped_share(positions),
    })
    return out


def wrapped_share(positions) -> float:
    """% of equity value held on IKE / IKZE / OKI (tax-asymmetry note for a hedge on a regular account)."""
    eq = sum(p["value"] for p in positions)
    if eq <= 0:
        return 0.0
    wrapped = sum(p["value"] * sum(n for a, n in p["accounts"].items() if a in ("ike", "ikze", "oki")) / p["shares"]
                  for p in positions)
    return round(wrapped / eq * 100, 2)


def inverse_etf_alternatives(mk, target, as_of, cache_dir, offline) -> Dict:
    """Inverse ETFs listed on GPW (names ending in ST: Beta ETF short series)."""
    names = {}
    for name, s in (mk["etf_panel"] or {}).items():
        m = re.match(r"^ETF.*(W20|M40)ST$", name)
        if m and s.dates and s.dates[-1] >= (as_of - dt.timedelta(days=10)).isoformat():
            names[name] = ("WIG20" if m.group(1) == "W20" else "mWIG40", s)
    if not names:
        sess = gpw_archive.load_sessions(70, end=as_of, type_id=gpw_archive.TYPE_ETF, cache_dir=cache_dir,
                                         offline=offline, progress=False)
        ep = panel.build_panel(sess) if sess else {}
        for name, s in ep.items():
            m = re.match(r"^ETF.*(W20|M40)ST$", name)
            if m:
                names[name] = ("WIG20" if m.group(1) == "W20" else "mWIG40", s)
    etfs = []
    for name, (idx, s) in sorted(names.items()):
        med = median([v * 1000 for v in s.turnover_k[-60:]]) if s.turnover_k else None
        price = s.close[-1]
        etfs.append({"name": name, "index": idx, "price": price, "last_date": s.dates[-1],
                     "median_turnover_pln": round(med, 2) if med else None,
                     "units_for_full_hedge": int(round(target / price)) if price > 0 and idx == "WIG20" else None})
    return {"inverse_etfs": etfs, "target_exposure_pln": round(target, 2), "cash_option": True}


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

def _days(x: Optional[float], lang: str) -> str:
    if x is None:
        return "∞"
    return "< " + num(0.1, 1, lang) if x < 0.1 else num(x, 1, lang)


def _label(s: Dict, lang: str) -> str:
    lab = s.get("label") or {}
    return lab.get(lang) or lab.get("en") or s["id"]


def _reason(s: Dict, t) -> str:
    r = s.get("reason") or ""
    if r == "no_index_data":
        return t("r_no_index")
    if r == "no_fx_data":
        return t("r_no_fx")
    if r == "no_rates_data":
        return t("r_no_rates")
    if r.startswith("unknown_index:"):
        return t("r_unknown_index", i=r.split(":", 1)[1])
    return r


def _worst_txt(s: Dict, lang: str) -> str:
    return ", ".join("{} {}".format(w["ticker"], pct(w["return_pct"], 1, lang, signed=True))
                     for w in s.get("worst_positions", [])) or "—"


def summary(r: Dict, lang: str) -> List[str]:
    t = T(STRINGS, lang)
    pf = r["portfolio"]
    out = [t("s_value", total=pln(pf["total_value"], 0, lang), eq=pln(pf["equity_value"], 0, lang),
             cash=pln(pf["cash"], 0, lang))]
    st = r.get("stress")
    if st:
        done = {s["id"]: s for s in st["scenarios"] if not s.get("skipped")}
        anchor = done.get("gfc_2008") or (done.get(st["worst_scenario"]) if st["worst_scenario"] else None)
        if anchor:
            key = "s_gfc" if anchor["pln_loss"] >= 0 else "s_gain"
            out.append(t(key, label=_label(anchor, lang), loss=pln(round(anchor["pln_loss"], -1), 0, lang),
                         gain=pln(round(-anchor["pln_loss"], -1), 0, lang),
                         ret=pct(anchor["portfolio_return_pct"], 1, lang, signed=True)))
        w = done.get(st["worst_scenario"]) if st["worst_scenario"] else None
        if w and w is not anchor and w["worst_positions"]:
            out.append(t("s_worst", label=_label(w, lang), loss=pln(round(w["pln_loss"], -1), 0, lang),
                         ret=pct(w["portfolio_return_pct"], 1, lang, signed=True), ticker=w["worst_positions"][0]["ticker"]))
        tol = st.get("tolerance")
        if tol:
            if tol["ok"]:
                out.append(t("s_tol_ok", lim=pct(tol["max_loss_pct"], 0, lang)))
            else:
                b = tol["breaches"][0]
                out.append(t("s_tol_bad", n=len(tol["breaches"]), lim=pct(tol["max_loss_pct"], 0, lang),
                             pp=num(b["loss_pct"] - tol["max_loss_pct"], 1, lang),
                             cut=num(b["equity_reduction_to_fit_pct"], 0, lang)))
        liq = st["liquidity"]
        if liq["positions"]:
            top = liq["positions"][0]
            out.append(t("s_liq", d1=pct(liq["sellable_1d_pct"], 0, lang), d5=pct(liq["sellable_5d_pct"], 0, lang),
                         ticker=top["ticker"], days=("~" if top["days_stress"] is None or top["days_stress"] >= 0.1 else "") + _days(top["days_stress"], lang)))
    h = r.get("hedge")
    if h and h.get("contracts") is not None:
        out.append(t("s_hedge", exact=num(h["contracts_exact"], 2, lang), beta=num(h["beta"], 2, lang),
                     floor=h["contracts"], hr=pct((h["hedge_ratio_floor"] or 0) * 100, 0, lang),
                     res=num(h["residual_risk_pct"], 0, lang)))
        if h["below_one_contract"]:
            out.append(t("s_small", per=pln(h["notional_per_contract"], 0, lang)))
    return out


def render_markdown(r: Dict, lang: str) -> str:
    t = T(STRINGS, lang)
    L: List[str] = []
    prov = r["data_provenance"]
    if prov["freshness"]["is_stale"]:
        L += [t("stale", n=prov["freshness"]["sessions_behind"]), ""]
    L += ["# " + t("title"), "", "{}: **{}**".format(t("as_of"), r["as_of"]), ""]
    L += ["## " + t("in_short"), ""] + ["- " + s for s in summary(r, lang)] + [""]
    st = r.get("stress")
    hedge_on = bool(r.get("hedge") and r["hedge"].get("contracts"))
    if st:
        hist = [s for s in st["scenarios"] if s["group"] == "historical"]
        fac = [s for s in st["scenarios"] if s["group"] == "factor"]
        for title, rows, is_hist in (("h_hist", hist, True), ("h_factor", fac, False)):
            if not rows:
                continue
            L += ["## " + t(title), ""]
            head = [t("c_scen")] + ([t("c_window"), t("c_wig")] if is_hist else []) + [t("c_port"), t("c_loss")]
            if hedge_on:
                head.append(t("c_loss_h"))
            head.append(t("c_worst"))
            L += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
            for s in rows:
                if s.get("skipped"):
                    L.append("| {} | {} |".format(_label(s, lang), t("skipped", r=_reason(s, t))) + " |" * (len(head) - 2))
                    continue
                cells = [_label(s, lang)]
                if is_hist:
                    cells += ["{} → {}".format(s["window"]["start"], s["window"]["end"]),
                              pct(s["window"]["index_return_pct"], 1, lang, signed=True)]
                cells += [pct(s["portfolio_return_pct"], 1, lang, signed=True), pln(s["pln_loss"], 0, lang)]
                if hedge_on:
                    cells.append(pln(s.get("pln_loss_hedged"), 0, lang))
                wt = _worst_txt(s, lang)
                if not s.get("complete", True):
                    wt += " (" + t("incomplete", t=", ".join(s["missing"])) + ")"
                cells.append(wt)
                L.append("| " + " | ".join(cells) + " |")
            L.append("")
        # per-position method matrix
        done_hist = [s for s in hist if not s.get("skipped")]
        if done_hist:
            L += ["### " + t("h_methods"), "", t("methods_legend"), ""]
            head = [t("c_ticker"), t("c_sector"), t("c_size"), t("c_beta")] + [s["id"] for s in done_hist]
            L += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
            for p in r["portfolio"]["positions"]:
                cells = [p["ticker"], sector_label(p["sector"], lang), SIZE_LABELS.get(p["size"] or "", "—"),
                         num(p["beta_wig"], 2, lang) + ("*" if p["beta_wig_default"] else "")]
                for s in done_hist:
                    row = next((x for x in s["positions"] if x["ticker"] == p["ticker"]), None)
                    cells.append("{} {}".format(pct(row["return_pct"], 0, lang, signed=True), METHOD_CODES.get(row["method"], "?"))
                                 if row else "–")
                L.append("| " + " | ".join(cells) + " |")
            L.append("")
        if st["sensitivities"]:
            L += ["### " + t("h_sens"), "", t("sens_note"), ""]
            head = [t("c_index"), t("c_fx"), t("c_tilt"), t("c_rates")]
            L += ["| " + " | ".join(head) + " |", "|---|---|---|---|"]
            for i, sv in st["sensitivities"].items():
                def cell(x, scale):
                    if not x:
                        return "—"
                    return "{} (t {}, n {}{})".format(pct(x["beta"] * scale, 2, lang, signed=True),
                                                     num(x["t"], 1, lang), x["n"], "" if x["significant"] else ", ⚠")
                fxs = sv.get("fx") or {}
                tilt = pct(fxs["tilt_vs_wig"], 2, lang, signed=True) if fxs.get("tilt_vs_wig") is not None else "—"
                L.append("| {} | {} | {} | {} |".format(index_label(i, lang), cell(sv.get("fx"), 1.0), tilt,
                                                        cell(sv.get("rates"), 100.0)))
            L.append("")
        liq = st["liquidity"]
        L += ["## " + t("h_liq"), ""]
        head = [t("c_ticker"), t("c_value"), t("c_med"), t("c_days_n"), t("c_days_s")]
        L += ["| " + " | ".join(head) + " |", "|---|---|---|---|---|"]
        for x in liq["positions"]:
            L.append("| {} | {} | {} | {} | {} |".format(
                x["ticker"], pln(x["value"], 0, lang), pln(x["median_turnover_pln"], 0, lang),
                _days(x["days_normal"], lang), _days(x["days_stress"], lang)))
        L.append("")
        tol = st.get("tolerance")
        if tol:
            L += ["## " + t("h_tol"), ""]
            if tol["ok"]:
                L.append(t("s_tol_ok", lim=pct(tol["max_loss_pct"], 0, lang)))
            for b in tol["breaches"]:
                s = next(x for x in st["scenarios"] if x["id"] == b["id"])
                L.append("- {}: {} > {} → {} {}".format(_label(s, lang), pct(-b["loss_pct"], 1, lang),
                                                      pct(-tol["max_loss_pct"], 0, lang),
                                                      "akcje mniejsze o" if lang == "pl" else "equity smaller by",
                                                      "~" + pct(b["equity_reduction_to_fit_pct"], 0, lang)))
            L.append("")
    h = r.get("hedge")
    if h:
        L += ["## " + t("h_hedge"), ""]
        if h.get("contracts") is None:
            L += [t("l_no_fut", d=r["as_of"]) if h.get("reason") in ("no_futures_quotes", "no_live_series")
                  else "{}: {}".format("Brak wyniku" if lang == "pl" else "No result", h.get("reason")), ""]
        else:
            L.append("- " + t("l_beta", b20=num(h["beta"], 2, lang), r2=num(h["r2_wig20"], 2, lang),
                                bw=num(h["beta_wig"], 2, lang), hmv=num(h["min_variance_ratio"], 2, lang)))
            L.append("- " + t("l_n", b=num(h["beta"], 2, lang), v=pln(h["equity_value"], 0, lang),
                                f=num(h["futures_price"], 0, lang), m=h["multiplier"], exact=num(h["contracts_exact"], 2, lang),
                                floor=h["contracts"], ceil=h["contracts_ceil"],
                                hrf=pct((h["hedge_ratio_floor"] or 0) * 100, 0, lang),
                                hrc=pct((h["hedge_ratio_ceil"] or 0) * 100, 0, lang), notional=pln(h["notional_pln"], 0, lang)))
            L.append("- " + (t("l_mult", m=h["multiplier"]) if h["multiplier_matches_spec"] else t("l_mult_bad", m=h["multiplier"])))
            L.append("- " + t("l_resid", res=num(h["residual_risk_pct"], 0, lang), rv=pct(h["residual_vol_pct"], 1, lang),
                                tv=pct(h["total_vol_pct"], 1, lang)))
            if h.get("basis_points") is not None:
                L.append("- " + t("l_basis", sym=h["futures_symbol"], f=num(h["futures_price"], 0, lang),
                                    s=num(h["wig20_spot"], 2, lang), b=num(h["basis_points"], 1, lang),
                                    bp=pct(h["basis_pct"], 2, lang, signed=True)))
            m = h.get("margin_estimate")
            if m:
                L.append("- " + t("l_margin", pct=pct(m["investor_initial_pct"], 1, lang), pln=pln(m["investor_initial_pln"], 0, lang),
                                    tot=pln(m["investor_initial_pln"] * max(1, h["contracts"]), 0, lang), n=max(1, h["contracts"])))
            else:
                L.append("- " + t("l_margin_na"))
            if h["excluded_short_history"]:
                L.append("- " + ("Krótka historia (beta 1,0): " if lang == "pl" else "Short history (beta 1.0): ")
                         + ", ".join(h["excluded_short_history"]))
            L += ["", "### " + t("h_contracts"), ""]
            head = [t("c_symbol"), t("c_price"), t("c_ltd"), t("c_sess"), t("c_turn")]
            L += ["| " + " | ".join(head) + " |", "|---|---|---|---|---|"]
            for c in h["series"]:
                L.append("| {}{} | {} | {} | {} | {} |".format(c["symbol"], " ✓" if c.get("recommended") else "",
                                                            num(c["price"], 0, lang), c["last_trading_day"],
                                                            c["sessions_to_expiry"], pln(c["turnover_pln"], 0, lang)))
            L.append("")
            for rr in h["roll_schedule"]:
                L.append("- {}: {} {}, {} {}".format(rr["symbol"], t("c_ltd"), rr["last_trading_day"], t("c_roll"), rr["roll_window_from"]))
            L.append("")
            bt = h.get("backtest")
            if bt:
                L += ["### " + t("h_bt"), "", t("bt_note"), "",
                      "{} → {} ({} {})".format(bt["start"], bt["end"], bt["n"], "sesji" if lang == "pl" else "sessions"), ""]
                head = [t("c_variant"), t("c_ret"), t("c_vol"), t("c_mdd"), t("c_wday")]
                L += ["| " + " | ".join(head) + " |", "|---|---|---|---|---|"]
                names = {"unhedged": "bez zabezpieczenia" if lang == "pl" else "unhedged",
                         "hedged_floor": ("{} kontr. (w dół)" if lang == "pl" else "{} contracts (floor)").format(h["contracts"]),
                         "hedged_exact": ("pełne β = {} ({} kontr.)" if lang == "pl" else "full β = {} ({} contracts)").format(
                             num(h["beta"], 2, lang), num(h["contracts_exact"], 2, lang))}
                for k in ("unhedged", "hedged_floor", "hedged_exact"):
                    v = bt.get(k)
                    if v:
                        L.append("| {} | {} | {} | {} | {} |".format(names[k], pct(v["total_return_pct"], 1, lang, signed=True),
                                                                   pct(v["volatility_pct"], 1, lang), pct(v["max_drawdown_pct"], 1, lang),
                                                                   pct(v["worst_day_pct"], 1, lang)))
                L.append("")
            alt = h.get("alternatives")
            if alt:
                L += ["### " + t("h_alt"), ""]
                for e in alt["inverse_etfs"]:
                    L.append("- " + t("l_etf", name=e["name"], idx=e["index"], price=pln(e["price"], 2, lang),
                                        turn=pln(e["median_turnover_pln"], 0, lang), target=pln(alt["target_exposure_pln"], 0, lang),
                                        units=e["units_for_full_hedge"] if e["units_for_full_hedge"] is not None else "—"))
                L += ["- " + t("l_cash"), ""]
        L += ["### " + t("h_tax"), "", "- " + t("tax_pit"), "- " + t("tax_ike"), "- " + t("tax_oki")]
        if h.get("tax_wrapped_equity_pct"):
            L.append("- " + t("tax_wrapped", p=pct(h["tax_wrapped_equity_pct"], 0, lang)))
        L.append("")
    L += ["## " + t("h_assume"), ""] + ["- " + a for a in STRINGS["assume"][lang]] + [""]
    if r.get("aliases"):
        L += ["- " + ("Zamiana tickerów: " if lang == "pl" else "Ticker aliases: ") + ", ".join(r["aliases"]), ""]
    L += ["## " + t("h_sources"), ""] + ["- " + s for s in prov["sources"]]
    L += ["- " + ("Sesja danych: " if lang == "pl" else "Data session: ") + prov["as_of"], "", "---", DISCLAIMER[lang], ""]
    return "\n".join(L)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _nonneg(text: str) -> float:
    try:
        v = rp.parse_number(text)
    except rp.PortfolioError:
        v = None
    if v is None or v < 0:
        raise argparse.ArgumentTypeError("must be a non-negative number")
    return v


def _loss_pct(text: str) -> float:
    v = _nonneg(text)
    if not 0 < v <= 100:
        raise argparse.ArgumentTypeError("must be between 0 and 100 (percent)")
    return v


def _date(text: str) -> str:
    try:
        dt.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD")
    return text


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GPW Risk Lab: stress test and FW20 hedge for a GPW portfolio (PL/EN). Never places orders.")
    p.add_argument("mode", choices=["stress", "hedge", "all"], help="stress = scenarios, hedge = FW20 futures, all = both")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="Holdings CSV: ticker,shares[,avg_price][,account_type] (';' and decimal commas OK)")
    src.add_argument("--journal", "--from-journal", dest="journal", action="store_true",
                     help="Use ACTIVE positions from gpw-trade-journal")
    src.add_argument("--positions", help="Inline holdings: TICKER:SHARES,TICKER:SHARES")
    p.add_argument("--state-dir", help="Journal directory (default ~/.gpw-trading-skills/journal or $GPW_JOURNAL_DIR)")
    p.add_argument("--cash", type=_nonneg, default=0.0, help="Cash in PLN (default 0)")
    p.add_argument("--max-loss-pct", type=_loss_pct, help="Your maximum acceptable loss in %% of the portfolio")
    p.add_argument("--scenarios", help="Scenario JSON (default scripts/risk_lab_scenarios.json)")
    p.add_argument("--only", help="Comma-separated scenario ids to run")
    p.add_argument("--lookback", type=int, default=re_.THRESHOLDS["lookback"], help="Sessions for betas (default 252)")
    p.add_argument("--participation", type=float, default=re_.THRESHOLDS["participation"] * 100,
                   help="Max share of daily turnover you sell, %% (default 20)")
    p.add_argument("--stress-turnover", type=float, default=re_.THRESHOLDS["stress_turnover_factor"] * 100,
                   help="Turnover in stress as %% of normal median (default 50)")
    p.add_argument("--fetch-history", action="store_true",
                   help="Download missing index sessions back to 2007 (first time ~1 h, polite rate)")
    p.add_argument("--as-of", type=_date, help="Analyse as of this session date (YYYY-MM-DD)")
    p.add_argument("--offline", action="store_true", help="Use only the local cache")
    p.add_argument("--cache-dir", help="Cache directory (default ~/.cache/gpw-trading-skills)")
    p.add_argument("--lang", choices=["pl", "en"], default="pl")
    p.add_argument("--output-dir", default=".", help="Where to write JSON + Markdown")
    p.add_argument("--no-files", action="store_true", help="Print Markdown only, write no files")
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 60 <= args.lookback <= 504:
        parser.error("--lookback must be between 60 and 504 sessions")
    if not 0 < args.participation <= 100:
        parser.error("--participation must be in (0, 100]")
    if not 0 < args.stress_turnover <= 100:
        parser.error("--stress-turnover must be in (0, 100]")
    if args.state_dir and not args.journal:
        parser.error("--state-dir only works with --journal")
    try:
        result = run(args)
    except (rp.PortfolioError, re_.StressError, ValueError, gpw_archive.GPWFetchError) as exc:
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
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    mp.write_text(md, encoding="utf-8")
    print(md)
    print("JSON: {}\nMarkdown: {}".format(jp, mp), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
