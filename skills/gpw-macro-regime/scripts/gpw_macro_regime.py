#!/usr/bin/env python3
"""GPW Macro Regime - CLI.

Slow-moving macro context for Polish equities (weeks to months): NBP rate
cycle, real rate, PLN regime, PL-DE 10Y spread, 2s10s curve and HICP
inflation, combined into a composite (supportive / mixed / headwind) with
sector tilts as historical tendencies. Not a timing tool.

    python3 scripts/gpw_macro_regime.py --lang pl
    python3 scripts/gpw_macro_regime.py --as-of 2026-06-30 --offline
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import gpw_archive, http, panel, plcal, provenance, reports  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct  # noqa: E402

import macro_engine as me  # noqa: E402
import macro_sources as ms  # noqa: E402

SKILL = "gpw-macro-regime"
VERSION = "1.0.0"
PREFIX = "gpw_macro_regime"
CORE_COMPONENTS = ("monetary", "inflation", "spread", "pln")
MIN_COMPONENTS = 2  # fail closed below this: a "composite" of one input is not a regime
INDICES = ["WIG", "WIG20", "MWIG40", "SWIG80", "WIG-BANKI", "WIG-INFO", "WIG-GRY", "WIG-GORNIC", "WIG-CHEMIA",
           "WIG-ODZIEZ", "WIG-SPOZYW", "WIG-ENERG", "WIG-NRCHOM"]


class MacroError(RuntimeError):
    pass


STRINGS = {
    "title": {"pl": "Reżim makro dla polskich akcji — raport", "en": "Macro regime for Polish equities — report"},
    "as_of": {"pl": "Data analizy", "en": "Analysis date"},
    "generated": {"pl": "Wygenerowano", "en": "Generated"},
    "historical": {"pl": "Analiza historyczna (--as-of {d}): dane miesięczne tylko te, które były już opublikowane.",
                   "en": "Historical run (--as-of {d}): monthly data only as published by then."},
    "warn_failed": {"pl": "⚠️ Niedostępne źródło: {name} — {err}. Wynik ma niższą pewność.",
                    "en": "⚠️ Source unavailable: {name} — {err}. Lower confidence."},
    "warn_stale": {"pl": "⚠️ Nieaktualne źródło: {name} — najnowsza obserwacja {obs} ({days} dni; limit {lim}).",
                   "en": "⚠️ Stale source: {name} — newest observation {obs} ({days} days; limit {lim})."},
    "warn_prev": {"pl": "Poprzedni raport: {w}", "en": "Previous report: {w}"},
    "answer": {"pl": "Odpowiedź", "en": "Bottom line"},
    "composite_line": {"pl": "Tło makro dla akcji: **{label}** (wynik {score} w skali −1…+1, pewność: {conf}).",
                       "en": "Macro backdrop for equities: **{label}** (score {score} on a −1…+1 scale, confidence: {conf})."},
    "summary_parts": {"pl": "Polityka pieniężna: {mon}. Inflacja: {infl}. Realna stopa: {real}. Złoty: {pln}. Spread PL-DE 10L: {spr}.",
                      "en": "Monetary policy: {mon}. Inflation: {infl}. Real rate: {real}. Zloty: {pln}. PL-DE 10Y spread: {spr}."},
    "horizon": {"pl": "To wolnozmienny kontekst na tygodnie–miesiące, nie sygnał do kupna ani sprzedaży i nie narzędzie do wyczucia momentu wejścia.",
                "en": "Slow-moving context for weeks to months - not a buy/sell signal and not a timing tool."},
    "state": {"pl": "Stan makro", "en": "Macro state"},
    "col_dim": {"pl": "Wymiar", "en": "Dimension"},
    "col_state": {"pl": "Stan", "en": "State"},
    "col_key": {"pl": "Kluczowa liczba", "en": "Key number"},
    "col_score": {"pl": "Wkład (−1…+1)", "en": "Score (−1…+1)"},
    "dim_monetary": {"pl": "Polityka pieniężna NBP", "en": "NBP monetary policy"},
    "dim_inflation": {"pl": "Inflacja (HICP r/r)", "en": "Inflation (HICP y/y)"},
    "dim_real_rate": {"pl": "Realna stopa procentowa", "en": "Real interest rate"},
    "dim_pln": {"pl": "Złoty (PLN)", "en": "Zloty (PLN)"},
    "dim_spread": {"pl": "Spread PL-DE 10L (premia za ryzyko)", "en": "PL-DE 10Y spread (risk premium)"},
    "dim_curve": {"pl": "Krzywa 2L–10L", "en": "2s10s curve"},
    "key_monetary": {"pl": "stopa ref. {rate}, ostatnia zmiana {d} ({mv} pb)", "en": "ref. rate {rate}, last change {d} ({mv} bp)"},
    "key_inflation": {"pl": "{v} ({p}); zmiana 3 mies.: {ch} pp; cel NBP 2,5% ±1 pp",
                      "en": "{v} ({p}); 3-month change: {ch} pp; NBP target 2.5% ±1 pp"},
    "key_real": {"pl": "{v} pp (stopa NBP − HICP)", "en": "{v} pp (NBP rate − HICP)"},
    "key_pln": {"pl": "EURPLN {e} ({ed} vs śr. 200), USDPLN {u} ({ud} vs śr. 200)",
                "en": "EURPLN {e} ({ed} vs 200-avg), USDPLN {u} ({ud} vs 200-avg)"},
    "key_spread": {"pl": "{v} pb; zmiana 3 mies.: {ch} pb ({basis})", "en": "{v} bp; 3-month change: {ch} bp ({basis})"},
    "key_curve": {"pl": "{v} pb ({b2} {y2} → {b10} {y10}); zmiana 3 mies.: {ch} pb",
                  "en": "{v} bp ({b2} {y2} → {b10} {y10}); 3-month change: {ch} bp"},
    "basis_daily": {"pl": "dziennie, BondSpot − Bund", "en": "daily, BondSpot − Bund"},
    "basis_monthly": {"pl": "miesięcznie, EBC − Bund", "en": "monthly, ECB − Bund"},
    "indicators": {"pl": "Wskaźniki", "en": "Indicators"},
    "col_ind": {"pl": "Wskaźnik", "en": "Indicator"},
    "col_val": {"pl": "Wartość", "en": "Value"},
    "col_asof": {"pl": "Na dzień", "en": "As of"},
    "col_trend": {"pl": "Trend", "en": "Trend"},
    "col_src": {"pl": "Źródło", "en": "Source"},
    "sectors": {"pl": "Sektory — historyczne tendencje (nie prognoza)", "en": "Sectors — historical tendencies (not a forecast)"},
    "sectors_note": {"pl": "Tak sektory zachowywały się zwykle w podobnym otoczeniu. To nie prognoza ani rekomendacja; wyniki spółek zależą też od wielu innych czynników. Kolumna „rynek” pokazuje, czy indeksy sektora w ostatnich 3 miesiącach rzeczywiście zachowywały się zgodnie z tą tendencją.",
                     "en": "How these sectors usually behaved in a similar backdrop. Not a forecast or a recommendation; company results depend on much more. The \"market\" column shows whether the sector indices actually behaved that way over the last 3 months."},
    "col_sector": {"pl": "Sektor", "en": "Sector"},
    "col_tilt": {"pl": "Tendencja", "en": "Tilt"},
    "col_drivers": {"pl": "Czynniki", "en": "Drivers"},
    "col_market": {"pl": "Rynek: 3 mies. vs WIG", "en": "Market: 3M vs WIG"},
    "agrees": {"pl": "zgodnie", "en": "consistent"},
    "disagrees": {"pl": "wbrew tendencji", "en": "against the tilt"},
    "watch": {"pl": "Co obserwować", "en": "What to watch"},
    "changes": {"pl": "Zmiany od poprzedniego raportu", "en": "Changes since the previous report"},
    "no_prev": {"pl": "Brak poprzedniego raportu gpw-macro-regime w katalogu wejściowym — to pierwszy punkt odniesienia.",
                "en": "No previous gpw-macro-regime report in the input directory - this is the first reference point."},
    "no_changes": {"pl": "Bez zmian stanu od raportu {f} (dane na {d}).", "en": "No state changes since report {f} (as of {d})."},
    "prev_line": {"pl": "Poprzedni raport: {f} (dane na {d}); zmiana wyniku: {ch}.", "en": "Previous report: {f} (as of {d}); score change: {ch}."},
    "history": {"pl": "Historia (miesięcznie, {n} mies.)", "en": "History (monthly, {n} months)"},
    "history_note": {"pl": "Koniec miesiąca, tylko dane opublikowane do tego dnia; spread miesięczny (EBC − Bund), bez krzywej. Ostatni wiersz = bieżąca analiza.",
                     "en": "Month-end, only data published by then; monthly spread (ECB − Bund), no curve. Last row = current analysis."},
    "col_month": {"pl": "Miesiąc", "en": "Month"},
    "col_rate": {"pl": "Stopa", "en": "Rate"},
    "col_cpi": {"pl": "HICP", "en": "HICP"},
    "col_comp": {"pl": "Wynik", "en": "Composite"},
    "sources": {"pl": "Źródła", "en": "Sources"},
    "src_line": {"pl": "{name}: {url} — najnowsza obserwacja {obs}, pobrano {ret}{cache}",
                 "en": "{name}: {url} — newest observation {obs}, retrieved {ret}{cache}"},
    "src_fail": {"pl": "{name}: {url} — NIEDOSTĘPNE ({err})", "en": "{name}: {url} — UNAVAILABLE ({err})"},
    "from_cache": {"pl": " (z pamięci podręcznej)", "en": " (from cache)"},
    "method": {"pl": "Reguły i progi: references/methodology.md. Stany są deterministyczne i opisują ostatnie miesiące.",
               "en": "Rules and thresholds: references/methodology.md. States are deterministic and describe recent months."},
    # labels
    "easing": {"pl": "obniżki stóp (cykl łagodzenia)", "en": "easing (rate cuts)"},
    "hold_after_easing": {"pl": "pauza po obniżkach", "en": "on hold after easing"},
    "hold_after_tightening": {"pl": "pauza po podwyżkach", "en": "on hold after tightening"},
    "tightening": {"pl": "podwyżki stóp (zacieśnianie)", "en": "tightening (rate hikes)"},
    "strong": {"pl": "mocny", "en": "strong"},
    "weak": {"pl": "słaby", "en": "weak"},
    "neutral": {"pl": "neutralny", "en": "neutral"},
    "narrowing": {"pl": "zawęża się (rośnie apetyt na ryzyko)", "en": "narrowing (risk appetite up)"},
    "widening": {"pl": "rozszerza się (inwestorzy żądają większej premii)", "en": "widening (higher risk premium)"},
    "stable": {"pl": "stabilny", "en": "stable"},
    "falling": {"pl": "spada", "en": "falling"},
    "rising": {"pl": "rośnie", "en": "rising"},
    "flat": {"pl": "płasko", "en": "flat"},
    "steepening": {"pl": "stromieje", "en": "steepening"},
    "flattening": {"pl": "wypłaszcza się", "en": "flattening"},
    "above": {"pl": "powyżej pasma celu NBP", "en": "above the NBP target band"},
    "within": {"pl": "w paśmie celu NBP", "en": "within the NBP target band"},
    "below": {"pl": "poniżej pasma celu NBP", "en": "below the NBP target band"},
    "positive": {"pl": "dodatnia", "en": "positive"},
    "negative": {"pl": "ujemna", "en": "negative"},
    "zero": {"pl": "zerowa", "en": "zero"},
    "restrictive": {"pl": "restrykcyjna", "en": "restrictive"},
    "accommodative": {"pl": "łagodna, stymulująca", "en": "accommodative"},
    "outperforming": {"pl": "lepiej od WIG", "en": "outperforming"},
    "underperforming": {"pl": "gorzej od WIG", "en": "underperforming"},
    "small_caps_lead": {"pl": "małe spółki prowadzą", "en": "small caps lead"},
    "large_caps_lead": {"pl": "duże spółki prowadzą", "en": "large caps lead"},
    "moderate": {"pl": "umiarkowana", "en": "moderate"},
    "inverted": {"pl": "odwrócona", "en": "inverted"},
    "normal": {"pl": "normalna", "en": "normal"},
    "steep": {"pl": "stroma", "en": "steep"},
    "supportive": {"pl": "WSPIERAJĄCE", "en": "SUPPORTIVE"},
    "mixed": {"pl": "MIESZANE", "en": "MIXED"},
    "headwind": {"pl": "NIEKORZYSTNE", "en": "HEADWIND"},
    "high": {"pl": "wysoka", "en": "high"},
    "medium": {"pl": "średnia", "en": "medium"},
    "low": {"pl": "niska", "en": "low"},
    "unknown": {"pl": "brak danych", "en": "no data"},
    "tilt_positive": {"pl": "korzystna", "en": "positive"},
    "tilt_slightly_positive": {"pl": "lekko korzystna", "en": "slightly positive"},
    "tilt_neutral": {"pl": "neutralna", "en": "neutral"},
    "tilt_slightly_negative": {"pl": "lekko niekorzystna", "en": "slightly negative"},
    "tilt_negative": {"pl": "niekorzystna", "en": "negative"},
    "tilt_unknown": {"pl": "brak danych", "en": "no data"},
    "drv_monetary": {"pl": "stopy", "en": "rates"},
    "drv_pln": {"pl": "złoty", "en": "PLN"},
    "drv_inflation": {"pl": "inflacja", "en": "inflation"},
    "drv_pl10y_3m": {"pl": "rentowność 10L (3 mies.)", "en": "10Y yield (3M)"},
    "drv_real_rate": {"pl": "realna stopa", "en": "real rate"},
    "sec_banks": {"pl": "Banki (WIG-BANKI)", "en": "Banks (WIG-BANKI)"},
    "sec_exporters": {"pl": "Eksporterzy: IT, gry, górnictwo, chemia", "en": "Exporters: IT, games, mining, chemicals"},
    "sec_importers_retail": {"pl": "Importerzy / handel: odzież, spożywcze", "en": "Importers / retail: apparel, food"},
    "sec_utilities_energy": {"pl": "Energetyka (WIG-ENERG)", "en": "Utilities (WIG-ENERG)"},
    "sec_developers": {"pl": "Deweloperzy (WIG-NRCHOM)", "en": "Developers (WIG-NRCHOM)"},
    "why_banks": {
        "pl": "Polskie banki zarabiają głównie na odsetkach, a większość ich kredytów ma zmienne oprocentowanie (WIBOR/WIRON). Obniżki stóp historycznie zmniejszały wynik odsetkowy banków, podwyżki go zwiększały. Ważne są też podatek bankowy, sprawy frankowe i koszty ryzyka.",
        "en": "Polish banks earn mostly on interest and most loans are floating-rate (WIBOR/WIRON). Rate cuts have historically reduced their net interest income, hikes raised it. The bank levy, CHF-mortgage litigation and risk costs also matter."},
    "why_exporters": {
        "pl": "Spółki IT, gamingowe (gry sprzedawane za granicą w USD/EUR), górnicze (miedź i srebro w USD) i chemiczne mają przychody w walutach, a dużą część kosztów w złotych. Słaby złoty historycznie podnosił ich wyniki w PLN, mocny — obniżał.",
        "en": "IT, game studios (games sold abroad in USD/EUR), miners (copper and silver in USD) and chemicals earn in foreign currency with many costs in PLN. A weak zloty has historically lifted their PLN results, a strong one cut them."},
    "why_importers_retail": {
        "pl": "Sieci odzieżowe i handel kupują część towarów za granicą (często w USD), a sprzedają w złotych. Mocny złoty obniża koszty zakupu; spadająca inflacja podnosi realne płace i konsumpcję.",
        "en": "Apparel and retail chains buy part of their goods abroad (often in USD) and sell in PLN. A strong zloty cuts purchase costs; falling inflation lifts real wages and consumption."},
    "why_utilities_energy": {
        "pl": "Energetyka ma duże inwestycje i długi, więc reaguje trochę jak obligacje: spadek rentowności 10-letnich obligacji pomagał wycenom, wzrost szkodził. W Polsce zwykle ważniejsza jest regulacja (taryfy URE, podatki nadzwyczajne, transformacja węglowa), dlatego wpływ makro ograniczamy najwyżej do „lekko”.",
        "en": "Utilities carry heavy capex and debt, so they trade partly like bonds: falling 10Y yields helped valuations, rising yields hurt. In Poland regulation (URE tariffs, windfall levies, coal transition) usually dominates, so the macro tilt is capped at \"slightly\"."},
    "why_developers": {
        "pl": "Deweloperzy zależą od kredytów hipotecznych: obniżki stóp poprawiają zdolność kredytową i popyt na mieszkania, podwyżki go hamują. Ujemna realna stopa historycznie skłaniała do lokowania oszczędności w mieszkaniach. Duży wpływ mają też rządowe programy mieszkaniowe.",
        "en": "Developers depend on mortgages: rate cuts improve borrowing capacity and housing demand, hikes cool it. A negative real rate has historically pushed savings into housing. Government housing programmes matter a lot too."},
    # indicators
    "ind_nbp_reference_rate": {"pl": "Stopa referencyjna NBP", "en": "NBP reference rate"},
    "ind_eurpln": {"pl": "EURPLN (fixing NBP)", "en": "EURPLN (NBP fixing)"},
    "ind_usdpln": {"pl": "USDPLN (fixing NBP)", "en": "USDPLN (NBP fixing)"},
    "ind_chfpln": {"pl": "CHFPLN (fixing NBP)", "en": "CHFPLN (NBP fixing)"},
    "ind_hicp_yoy": {"pl": "Inflacja HICP r/r", "en": "HICP inflation y/y"},
    "ind_real_rate": {"pl": "Realna stopa (NBP − HICP)", "en": "Real rate (NBP − HICP)"},
    "ind_pl10y": {"pl": "Rentowność PL 10L", "en": "PL 10Y yield"},
    "ind_pl2y": {"pl": "Rentowność PL 2L", "en": "PL 2Y yield"},
    "ind_bund10y": {"pl": "Rentowność Bund 10L", "en": "Bund 10Y yield"},
    "ind_pl_de_spread_10y": {"pl": "Spread PL-DE 10L", "en": "PL-DE 10Y spread"},
    "ind_curve_2s10s": {"pl": "Nachylenie krzywej 2L–10L", "en": "2s10s curve slope"},
    "ind_wig_usd": {"pl": "WIG w USD (WIG / USDPLN)", "en": "WIG in USD (WIG / USDPLN)"},
    "ind_banks_vs_wig": {"pl": "WIG-BANKI vs WIG (3 mies.)", "en": "WIG-BANKI vs WIG (3M)"},
    "ind_swig80_vs_wig20": {"pl": "sWIG80 vs WIG20 (3 mies.)", "en": "sWIG80 vs WIG20 (3M)"},
    # watch
    "w_hawkish_risk": {"pl": "Inflacja {cpi} przy stopie {rate} i pauzie/obniżkach: rośnie ryzyko, że RPP wstrzyma łagodzenie lub zacznie mówić o podwyżkach. Obserwuj decyzje i komunikaty RPP (gpw-event-calendar) oraz szybki szacunek CPI GUS.",
                        "en": "Inflation {cpi} with the rate at {rate} after cuts: risk that the MPC stops easing or turns hawkish. Watch MPC decisions/statements (gpw-event-calendar) and the GUS flash CPI."},
    "w_easing_room": {"pl": "Inflacja {cpi} spada przy stopie {rate}: rośnie przestrzeń do obniżek. Obserwuj komunikaty RPP i projekcję NBP.",
                      "en": "Inflation {cpi} falling with the rate at {rate}: room for cuts is growing. Watch MPC statements and the NBP projection."},
    "w_negative_real_rate": {"pl": "Realna stopa jest ujemna ({v} pp): oszczędzanie na lokatach traci na wartości, co historycznie wspierało ryzykowne aktywa i nieruchomości, ale też grozi utrwaleniem inflacji.",
                             "en": "The real rate is negative ({v} pp): deposits lose purchasing power, which historically supported risk assets and housing but risks entrenching inflation."},
    "w_fx_near_threshold": {"pl": "{pair} jest {dev} od średniej z 200 fixingów ({sma}) — zwykły miesięczny ruch może zmienić ocenę złotego.",
                            "en": "{pair} is {dev} from its 200-fixing average ({sma}) - an ordinary month's move could change the PLN reading."},
    "w_fx_vol": {"pl": "Zmienność złotego w ostatnich 3 miesiącach jest wyraźnie wyższa niż średnio w roku — typowe dla okresów napięć.",
                 "en": "PLN volatility over 3 months is clearly above its 1-year level - typical of stress periods."},
    "w_spread_near_threshold": {"pl": "Spread PL-DE zmienił się o {ch} pb w 3 miesiące — blisko progu ±15 pb, przy którym zmieni się ocena.",
                                "en": "The PL-DE spread moved {ch} bp in 3 months - close to the ±15 bp threshold that changes the reading."},
    "w_curve_inverted": {"pl": "Krzywa 2L–10L jest odwrócona ({v} pb): historycznie częsty sygnał spowolnienia gospodarczego.",
                         "en": "The 2s10s curve is inverted ({v} bp): historically a frequent precursor of a slowdown."},
    "w_curve_flat": {"pl": "Krzywa 2L–10L jest płaska ({v} pb): obserwuj, czy się nie odwróci.",
                     "en": "The 2s10s curve is flat ({v} bp): watch for an inversion."},
    "w_next_cpi": {"pl": "Następny odczyt inflacji HICP do sprawdzenia: za {period} (Eurostat publikuje ok. połowy kolejnego miesiąca; GUS podaje szybki szacunek CPI na koniec miesiąca).",
                   "en": "Next HICP reading to check: {period} (Eurostat publishes around mid next month; GUS releases a flash CPI at month-end)."},
}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Macro regime for Polish equities (NBP, FX, yields, HICP) - PL/EN")
    p.add_argument("--as-of", default=None, help="analysis date YYYY-MM-DD (default: today, Warsaw)")
    p.add_argument("--offline", action="store_true", help="use cached data only, no network")
    p.add_argument("--cache-dir", default=None)
    p.add_argument("--lang", choices=["pl", "en"], default="pl")
    p.add_argument("--output-dir", default=".")
    p.add_argument("--input-dir", default=None, help="where to look for the previous report (default: --output-dir)")
    p.add_argument("--previous-json", default=None, help="explicit previous gpw-macro-regime JSON to compare with")
    p.add_argument("--history-months", type=int, default=24)
    p.add_argument("--no-bondspot", action="store_true", help="skip the optional BondSpot curve/spread source")
    p.add_argument("--no-files", action="store_true", help="print the Markdown instead of writing files")
    a = p.parse_args(argv)
    today = plcal.today_warsaw()
    if a.as_of:
        try:
            a.as_of_date = dt.date.fromisoformat(a.as_of)
        except ValueError:
            p.error("--as-of must be YYYY-MM-DD")
        if a.as_of_date > today:
            p.error("--as-of cannot be in the future")
        if a.as_of_date < dt.date(2005, 1, 1):
            p.error("--as-of must be 2005-01-01 or later (NBP API FX history starts 2002, ECB/Eurostat sooner)")
    else:
        a.as_of_date = today
    if not 1 <= a.history_months <= 60:
        p.error("--history-months must be between 1 and 60")
    return a


def _attempt(status: Dict, key: str, fn):
    try:
        res = fn()
    except (http.HTTPFetchError, ms.SourceError, ValueError, KeyError, TypeError) as exc:
        status[key] = {"ok": False, "error": str(exc)[:300]}
        return None
    status[key] = {"ok": True, "retrieved_at": res.get("retrieved_at"), "from_cache": res.get("from_cache"),
                   "urls": res.get("urls")}
    return res


def _obs_date(key) -> dt.date:
    return key if isinstance(key, dt.date) else ms.month_start(key)


def _finish_status(status: Dict, key: str, src_key: str, newest, as_of: dt.date, historical: bool):
    st = status.get(key)
    if not st or not st["ok"]:
        return
    lim = ms.SOURCES[src_key]["max_lag_days"]
    if newest is None:
        st.update({"newest_observation": None, "stale": True, "lag_days": None, "max_lag_days": lim})
        return
    obs = _obs_date(newest)
    lag = (as_of - obs).days
    st.update({"newest_observation": newest.isoformat() if isinstance(newest, dt.date) else newest,
               "lag_days": lag, "max_lag_days": lim, "stale": lag > lim, "name": ms.SOURCES[src_key]["name"],
               "frequency": ms.SOURCES[src_key]["frequency"]})


def run(args) -> Dict:
    as_of: dt.date = args.as_of_date
    today = plcal.today_warsaw()
    historical = as_of < today
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else None
    f = ms.Fetcher(cache_dir=cache_dir, offline=args.offline, today=today)
    status: Dict[str, Dict] = {}
    months = args.history_months
    # FX: history months + 12-month change + 200 fixings before the oldest month
    fx_start = as_of - dt.timedelta(days=months * 31 + 400)
    m_start = as_of - dt.timedelta(days=(months + 15) * 31)

    rates = _attempt(status, "nbp_rates", f.nbp_rates)
    eur = _attempt(status, "nbp_fx_eur", lambda: f.nbp_fx("EUR", fx_start, as_of))
    usd = _attempt(status, "nbp_fx_usd", lambda: f.nbp_fx("USD", fx_start, as_of))
    chf = _attempt(status, "nbp_fx_chf", lambda: f.nbp_fx("CHF", as_of - dt.timedelta(days=400), as_of))
    hicp = _attempt(status, "eurostat_hicp", lambda: f.hicp(m_start))
    ecb = _attempt(status, "ecb_pl10y", lambda: f.ecb_pl10y(m_start))
    bund = _attempt(status, "bundesbank", lambda: f.bund10y(m_start))
    bs_now = bs_then = None
    if not args.no_bondspot:
        bs_now = _attempt(status, "bondspot", lambda: f.bondspot(as_of))
        if bs_now:
            st_now = status["bondspot"]
            bs_then = _attempt(status, "bondspot_3m", lambda: f.bondspot(bs_now["date"] - dt.timedelta(days=me.LOOKBACK_3M)))
            status["bondspot"] = st_now

    rate_s = rates["series"] if rates else []
    eur_s = me.upto(eur["series"], as_of) if eur else []
    usd_s = me.upto(usd["series"], as_of) if usd else []
    chf_s = me.upto(chf["series"], as_of) if chf else []
    hicp_s = hicp["series"] if hicp else []
    ecb_s = ecb["series"] if ecb else []
    bund_s = me.upto(bund["series"], as_of) if bund else []

    # newest observation per source (staleness is per source, by its own rhythm)
    if rates:
        ret = dt.date.fromisoformat(rates["retrieved_at"][:10]) if rates.get("retrieved_at") else None
        _finish_status(status, "nbp_rates", "nbp_rates", as_of if historical else min(ret or as_of, as_of), as_of, historical)
    for key, ser in (("nbp_fx_eur", eur_s), ("nbp_fx_usd", usd_s), ("nbp_fx_chf", chf_s)):
        _finish_status(status, key, "nbp_fx", ser[-1][0] if ser else None, as_of, historical)
    hicp_pit = me.published(hicp_s, as_of, me.PUB_LAG_DAYS["hicp"]) if historical else me.upto(hicp_s, me.month_key(as_of))
    _finish_status(status, "eurostat_hicp", "eurostat_hicp", hicp_pit[-1][0] if hicp_pit else None, as_of, historical)
    ecb_pit = me.published(ecb_s, as_of, me.PUB_LAG_DAYS["ecb"]) if historical else me.upto(ecb_s, me.month_key(as_of))
    _finish_status(status, "ecb_pl10y", "ecb_pl10y", ecb_pit[-1][0] if ecb_pit else None, as_of, historical)
    _finish_status(status, "bundesbank", "bundesbank", bund_s[-1][0] if bund_s else None, as_of, historical)
    if bs_now:
        _finish_status(status, "bondspot", "bondspot", bs_now["date"], as_of, historical)
    if bs_then:
        _finish_status(status, "bondspot_3m", "bondspot", bs_then["date"], bs_now["date"] - dt.timedelta(days=me.LOOKBACK_3M), historical)

    # ---- states ---------------------------------------------------------
    mon = me.monetary_state(rate_s, as_of) if rate_s else None
    infl = me.inflation_state(hicp_s, as_of, point_in_time=historical) if hicp_s else None
    real = me.real_rate_state(mon["rate"] if mon else None, infl["value"] if infl else None)
    eur_m = me.fx_metrics(eur_s, as_of) if eur_s else None
    usd_m = me.fx_metrics(usd_s, as_of) if usd_s else None
    chf_m = me.fx_metrics(chf_s, as_of) if chf_s else None
    pln = me.pln_state(eur_m, usd_m)

    def bench(snap):
        if not snap:
            return None
        return {"date": snap["date"], "y2": ms.pick_benchmark(snap["bonds"], snap["date"], 2.0, 1.0),
                "y10": ms.pick_benchmark(snap["bonds"], snap["date"], 10.0, 2.0)}

    b_now, b_then = bench(bs_now), bench(bs_then)
    curve = me.curve_state(b_now, b_then)
    pl_now = (b_now["date"], b_now["y10"]["yield"]) if b_now and b_now.get("y10") else None
    pl_then = (b_then["date"], b_then["y10"]["yield"]) if b_then and b_then.get("y10") else None
    spread_m = me.spread_monthly(ecb_pit, bund_s) if ecb_pit and bund_s else []
    spread = me.spread_state_daily(pl_now, pl_then, bund_s) if bund_s else None
    if spread is None and spread_m:
        spread = me.spread_state_monthly(spread_m, as_of, point_in_time=historical)

    built = me.build_state(mon, infl, real, pln, spread, curve)
    scores, comp = built["scores"], built["composite"]
    missing = [c for c in CORE_COMPONENTS if scores.get(c) is None]
    if len(CORE_COMPONENTS) - len(missing) < MIN_COMPONENTS:
        failed = ", ".join("{} ({})".format(k, v.get("error")) for k, v in status.items() if not v["ok"]) or "none"
        raise MacroError("Only {} of {} macro components available (need {}). Failed sources: {}".format(
            len(CORE_COMPONENTS) - len(missing), len(CORE_COMPONENTS), MIN_COMPONENTS, failed))
    core_sources = ["nbp_rates", "nbp_fx_eur", "nbp_fx_usd", "eurostat_hicp", "bundesbank"]
    stale = sorted(k for k, v in status.items() if v.get("ok") and v.get("stale") and k in core_sources + ["ecb_pl10y", "bondspot"])
    # a stale secondary source only matters if the spread had to rely on it
    stale = [k for k in stale if not (k == "ecb_pl10y" and spread and spread.get("basis") == "daily")]
    failed = sorted(k for k, v in status.items() if not v["ok"])
    conf = me.confidence(missing, stale)

    # ---- equity side ----------------------------------------------------
    idx_end = as_of
    if not historical and not plcal.session_is_final(today):
        idx_end = today - dt.timedelta(days=1)
    closes: Dict[str, List] = {}
    try:
        sess = gpw_archive.load_sessions(270, end=idx_end, type_id=gpw_archive.TYPE_INDICES, cache_dir=cache_dir,
                                         offline=args.offline, progress=False)
        for name in INDICES:
            s = panel.index_series(sess, name)
            closes[name] = [(dt.date.fromisoformat(d), c) for d, c in zip(s.dates, s.close)]
        if not closes.get("WIG"):
            raise gpw_archive.GPWFetchError("WIG not found in the cached index sessions")
        status["gpw_indices"] = {"ok": True, "sessions": len(sess), "from_cache": None,
                                 "retrieved_at": sess[-1].get("retrieved_at") if sess else None,
                                 "urls": ["https://www.gpw.pl/archiwum-notowan"]}
        _finish_status(status, "gpw_indices", "gpw_indices", closes["WIG"][-1][0], idx_end, historical)
    except (gpw_archive.GPWFetchError, OSError, ValueError) as exc:
        status["gpw_indices"] = {"ok": False, "error": str(exc)[:300]}
    perf = me.index_perf(closes, idx_end) if closes else {}
    wig_usd = me.wig_in_usd(closes.get("WIG", []), usd_s, idx_end) if closes.get("WIG") and usd_s else None

    pl10_change = None
    if pl_now and pl_then:
        pl10_change = (pl_now[1] - pl_then[1]) * 100
    elif len(ecb_pit) >= 4:
        pl10_change = (ecb_pit[-1][1] - ecb_pit[-4][1]) * 100
    tilts = me.sector_tilts(mon, infl, real, pln, pl10_change, perf)
    t = T(STRINGS, args.lang)
    for key, tl in tilts.items():
        tl["rationale"] = t("why_" + key)

    indicators = build_indicators(mon, infl, real, eur_m, usd_m, chf_m, b_now, ecb_pit, bund_s, spread, curve,
                                  wig_usd, perf, pl10_change)

    # ---- history and previous report ------------------------------------
    hist = me.history(months, as_of, rate_s, hicp_s, eur_s, usd_s, spread_m)
    if hist:
        hist[-1].update({"monetary": built["state"]["monetary"], "pln": built["state"]["pln"],
                         "spread": built["state"]["spread"], "inflation": built["state"]["inflation"],
                         "inflation_value": infl["value"] if infl else None, "rate": mon["rate"] if mon else None,
                         "composite": comp["label"], "score": comp["score"], "date": as_of.isoformat()})
    input_dir = args.input_dir if args.input_dir is not None else args.output_dir
    prev = reports.find_report(SKILL, input_dir, explicit_path=args.previous_json, reference=as_of, lang=args.lang)
    diff = me.changes_since(prev["data"] if prev else None, built["state"], comp["score"])
    if diff is not None:
        diff.update({"previous_file": prev["file"], "previous_as_of": prev["as_of"], "warning": prev["warning"]})

    watch = me.watch_items(mon, infl, real, pln, eur_m, usd_m, spread, curve)
    for w in watch:
        w["text"] = render_watch(w, args.lang)

    report_as_of = eur_s[-1][0] if eur_s else as_of
    sources_used = []
    for k, v in status.items():
        if v.get("ok"):
            src = ms.SOURCES.get(_src_key(k), {})
            sources_used.append("{}: {}".format(src.get("name", k), src.get("url", "")))
    prov = provenance.build(
        as_of=report_as_of, sources=sorted(set(sources_used)), sessions_used=len(closes.get("WIG", [])),
        notes=["Each macro source has its own publication lag; see per_source.",
               "HICP (Eurostat) is used instead of national CPI (GUS): close but not identical to the NBP target measure.",
               "Ministry of Finance non-resident holdings of T-bonds: not included (no stable machine-readable URL)."],
        corporate_actions_adjusted=False, reference_date=as_of if historical else None, max_sessions_behind=3)
    prov["per_source"] = status
    return {
        "skill": SKILL, "version": VERSION, "lang": args.lang, "as_of": report_as_of.isoformat(),
        "analysis_date": as_of.isoformat(),
        "macro": {"state": built["state"], "score": comp["score"], "confidence": conf,
                  "component_scores": scores, "components_used": comp["components_used"],
                  "missing_components": missing, "failed_sources": failed, "stale_sources": stale,
                  "curve_penalty": comp["curve_penalty"],
                  "details": {"monetary": mon, "inflation": infl, "real_rate": real, "pln": pln,
                              "spread": spread, "curve": curve}},
        "indicators": indicators,
        "sector_tilts": tilts,
        "history": hist,
        "changes_since_previous": diff,
        "watch": watch,
        "index_performance": perf,
        "data_provenance": prov,
    }


def _src_key(status_key: str) -> str:
    if status_key.startswith("nbp_fx"):
        return "nbp_fx"
    if status_key.startswith("bondspot"):
        return "bondspot"
    return status_key


def _ind(value, unit, as_of, source, trend, **extra):
    d = {"value": value, "unit": unit, "as_of": as_of, "source": source, "trend": trend}
    d.update(extra)
    return d


def build_indicators(mon, infl, real, eur_m, usd_m, chf_m, b_now, ecb_pit, bund_s, spread, curve, wig_usd, perf,
                     pl10_change) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    if mon:
        out["nbp_reference_rate"] = _ind(mon["rate"], "%", mon["last_change_date"], "nbp_rates", mon["trend"],
                                         last_move_bp=mon["last_move_bp"], change_12m_bp=mon["change_12m_bp"],
                                         cycle_total_bp=mon["cycle_total_bp"])
    for key, m in (("eurpln", eur_m), ("usdpln", usd_m), ("chfpln", chf_m)):
        if m:
            out[key] = _ind(m["value"], "PLN", m["as_of"], "nbp_fx", m["trend"],
                            **{k: m[k] for k in ("sma200", "dev_sma200_pct", "change_3m_pct", "change_6m_pct",
                                                 "change_12m_pct", "vol_3m_ann_pct", "vol_1y_ann_pct", "pln_signal")})
    if infl:
        out["hicp_yoy"] = _ind(infl["value"], "%", infl["period"], "eurostat_hicp", infl["trend"],
                               change_3m_pp=infl["change_3m_pp"], band=infl["band"])
    if real:
        out["real_rate"] = _ind(real["value"], "pp", infl["period"] if infl else None, "nbp_rates+eurostat_hicp",
                                None, stance=real["stance"])
    if b_now and b_now.get("y10"):
        out["pl10y"] = _ind(b_now["y10"]["yield"], "%", b_now["date"].isoformat(), "bondspot",
                            me.classify_change(pl10_change, me.YIELD_TREND_BP, "falling", "rising"),
                            bond=b_now["y10"]["name"], isin=b_now["y10"]["isin"], tenor_years=b_now["y10"]["tenor_years"],
                            change_3m_bp=me._r(pl10_change, 1))
    elif ecb_pit:
        out["pl10y"] = _ind(ecb_pit[-1][1], "%", ecb_pit[-1][0], "ecb_pl10y",
                            me.classify_change(pl10_change, me.YIELD_TREND_BP, "falling", "rising"), change_3m_bp=me._r(pl10_change, 1))
    if ecb_pit:
        out["pl10y_monthly_ecb"] = _ind(ecb_pit[-1][1], "%", ecb_pit[-1][0], "ecb_pl10y", None)
    if b_now and b_now.get("y2"):
        out["pl2y"] = _ind(b_now["y2"]["yield"], "%", b_now["date"].isoformat(), "bondspot", None,
                           bond=b_now["y2"]["name"], isin=b_now["y2"]["isin"], tenor_years=b_now["y2"]["tenor_years"])
    if bund_s:
        b3 = me.value_on(bund_s, bund_s[-1][0] - dt.timedelta(days=me.LOOKBACK_3M))
        ch = (bund_s[-1][1] - b3[1]) * 100 if b3 else None
        out["bund10y"] = _ind(bund_s[-1][1], "%", bund_s[-1][0].isoformat(), "bundesbank",
                              me.classify_change(ch, me.YIELD_TREND_BP, "falling", "rising"), change_3m_bp=me._r(ch, 1))
    if spread:
        out["pl_de_spread_10y"] = _ind(spread["value_bp"], "bp", spread["as_of"],
                                       "bondspot+bundesbank" if spread["basis"] == "daily" else "ecb_pl10y+bundesbank",
                                       spread["trend"], change_3m_bp=spread["change_3m_bp"], basis=spread["basis"])
    if curve:
        out["curve_2s10s"] = _ind(curve["value_bp"], "bp", curve["as_of"], "bondspot", curve["trend"],
                                  shape=curve["shape"], change_3m_bp=curve["change_3m_bp"])
    if wig_usd:
        out["wig_usd"] = _ind(wig_usd["value"], "USD pts", wig_usd["as_of"], "gpw_indices+nbp_fx", wig_usd["trend"],
                              **{k: wig_usd[k] for k in ("change_3m_pct", "change_6m_pct", "change_12m_pct", "dev_sma200_pct")})
    if perf.get("WIG-BANKI") and perf["WIG-BANKI"].get("vs_wig_3m_pp") is not None:
        v = perf["WIG-BANKI"]["vs_wig_3m_pp"]
        out["banks_vs_wig"] = _ind(v, "pp", perf["WIG-BANKI"]["as_of"], "gpw_indices",
                                   "outperforming" if v > 0 else "underperforming", vs_wig_6m_pp=perf["WIG-BANKI"]["vs_wig_6m_pp"])
    if perf.get("SWIG80") and perf.get("WIG20") and perf["SWIG80"]["ret_3m_pct"] is not None:
        v = me._r(perf["SWIG80"]["ret_3m_pct"] - perf["WIG20"]["ret_3m_pct"], 2)
        out["swig80_vs_wig20"] = _ind(v, "pp", perf["SWIG80"]["as_of"], "gpw_indices",
                                      "small_caps_lead" if v > 0 else "large_caps_lead")
    return out


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _p(x, lang, d=2, signed=False):
    return pct(x, d, lang, signed=signed)


def _n(x, lang, d=0, signed=False):
    if x is None:
        return "—"
    s = num(x, d, lang)
    return ("+" + s) if signed and x > 0 else s


def render_watch(w: Dict, lang: str) -> str:
    t = T(STRINGS, lang)
    i = w["id"]
    if i in ("hawkish_risk", "easing_room"):
        return t("w_" + i, cpi=_p(w["cpi"], lang, 1), rate=_p(w["rate"], lang, 2))
    if i == "negative_real_rate":
        return t("w_" + i, v=_n(w["value"], lang, 2, True))
    if i == "fx_near_threshold":
        return t("w_" + i, pair=w["pair"], dev=_p(w["dev"], lang, 2, True), sma=_n(w["sma"], lang, 4))
    if i == "spread_near_threshold":
        return t("w_" + i, ch=_n(w["change"], lang, 0, True))
    if i.startswith("curve_"):
        return t("w_" + i, v=_n(w["value"], lang, 0))
    if i == "next_cpi":
        return t("w_" + i, period=w["period"])
    return t("w_" + i)


def render_summary(macro: Dict, lang: str) -> str:
    t = T(STRINGS, lang)
    d = macro["details"]
    mon, infl, real, pln, spr = d["monetary"], d["inflation"], d["real_rate"], d["pln"], d["spread"]
    return t("summary_parts",
             mon="{} ({} {})".format(t(mon["state"]), _p(mon["rate"], lang), mon["last_change_date"]) if mon else t("unknown"),
             infl="HICP {} ({}), {}, {}".format(_p(infl["value"], lang, 1), infl["period"], t(infl["trend"]) if infl["trend"] else "—",
                                               t(infl["band"])) if infl else t("unknown"),
             real="{} pp ({})".format(_n(real["value"], lang, 2, True), t(real["stance"])) if real else t("unknown"),
             pln=t(pln["regime"]) if pln else t("unknown"),
             spr="{} {}, {}".format(_n(spr["value_bp"], lang, 0), "pb" if lang == "pl" else "bp", t(spr["trend"])) if spr and spr.get("trend") else t("unknown"))


def _src_url(status: Dict, key: str) -> str:
    urls = status.get("urls") or [ms.SOURCES.get(_src_key(key), {}).get("url", "")]
    return urls[-1] if urls else ""


def render_markdown(r: Dict, lang: str) -> str:
    t = T(STRINGS, lang)
    m, prov = r["macro"], r["data_provenance"]
    st, det = m["state"], m["details"]
    L = ["# " + t("title"), "",
         "**{}:** {} · **{}:** {}".format(t("as_of"), r["analysis_date"], t("generated"), prov["retrieved_at_utc"]), ""]
    warn = []
    for k, v in prov["per_source"].items():
        name = ms.SOURCES.get(_src_key(k), {}).get("name", k)
        if not v.get("ok"):
            warn.append(t("warn_failed", name=name + (" (−3M)" if k == "bondspot_3m" else ""), err=v.get("error")))
        elif v.get("stale"):
            warn.append(t("warn_stale", name=name, obs=v.get("newest_observation"), days=v.get("lag_days"), lim=v.get("max_lag_days")))
    if prov["freshness"]["historical_run"]:
        warn.append(t("historical", d=r["analysis_date"]))
    ch = r.get("changes_since_previous")
    if ch and ch.get("warning"):
        warn.append(t("warn_prev", w=ch["warning"].get(lang)))
    if warn:
        L += ["> " + w for w in warn] + [""]

    L += ["## " + t("answer"), "",
          t("composite_line", label=t(st["composite"] or "unknown"), score=_n(m["score"], lang, 2, True), conf=t(m["confidence"])),
          "", r["macro"].get("summary", ""), "", t("horizon"), ""]

    # state table
    L += ["## " + t("state"), "", "| {} | {} | {} | {} |".format(t("col_dim"), t("col_state"), t("col_key"), t("col_score")),
          "|---|---|---|---|"]
    sc = m["component_scores"]
    mon, infl, real, pln, spr, cur = det["monetary"], det["inflation"], det["real_rate"], det["pln"], det["spread"], det["curve"]
    ind = r["indicators"]
    if mon:
        L.append("| {} | {} | {} | {} |".format(t("dim_monetary"), t(mon["state"]),
                 t("key_monetary", rate=_p(mon["rate"], lang), d=mon["last_change_date"], mv=_n(mon["last_move_bp"], lang, 0, True)),
                 _n(sc.get("monetary"), lang, 2, True)))
    else:
        L.append("| {} | {} | — | — |".format(t("dim_monetary"), t("unknown")))
    if infl:
        L.append("| {} | {}, {} | {} | {} |".format(t("dim_inflation"), t(infl["trend"]) if infl["trend"] else "—", t(infl["band"]),
                 t("key_inflation", v=_p(infl["value"], lang, 1), p=infl["period"], ch=_n(infl["change_3m_pp"], lang, 1, True)),
                 _n(sc.get("inflation"), lang, 2, True)))
    else:
        L.append("| {} | {} | — | — |".format(t("dim_inflation"), t("unknown")))
    if real:
        L.append("| {} | {} ({}) | {} | — |".format(t("dim_real_rate"), t(real["sign"]), t(real["stance"]),
                 t("key_real", v=_n(real["value"], lang, 2, True))))
    if pln:
        e, u = ind.get("eurpln"), ind.get("usdpln")
        L.append("| {} | {} | {} | {} |".format(t("dim_pln"), t(pln["regime"]),
                 t("key_pln", e=_n(e["value"], lang, 4) if e else "—", ed=_p(e["dev_sma200_pct"], lang, 1, True) if e else "—",
                   u=_n(u["value"], lang, 4) if u else "—", ud=_p(u["dev_sma200_pct"], lang, 1, True) if u else "—"),
                 _n(sc.get("pln"), lang, 2, True)))
    else:
        L.append("| {} | {} | — | — |".format(t("dim_pln"), t("unknown")))
    if spr:
        L.append("| {} | {} | {} | {} |".format(t("dim_spread"), t(spr["trend"]),
                 t("key_spread", v=_n(spr["value_bp"], lang, 0), ch=_n(spr["change_3m_bp"], lang, 0, True), basis=t("basis_" + spr["basis"])),
                 _n(sc.get("spread"), lang, 2, True)))
    else:
        L.append("| {} | {} | — | — |".format(t("dim_spread"), t("unknown")))
    if cur:
        L.append("| {} | {}{} | {} | {} |".format(t("dim_curve"), t(cur["shape"]), (", " + t(cur["trend"])) if cur.get("trend") else "",
                 t("key_curve", v=_n(cur["value_bp"], lang, 0), b2=cur["y2_bond"], y2=_p(cur["y2"], lang), b10=cur["y10_bond"],
                   y10=_p(cur["y10"], lang), ch=_n(cur.get("change_3m_bp"), lang, 0, True)),
                 "−{}".format(num(m["curve_penalty"], 2, lang)) if m["curve_penalty"] else "0"))
    L.append("")

    # indicators
    L += ["## " + t("indicators"), "", "| {} | {} | {} | {} | {} |".format(t("col_ind"), t("col_val"), t("col_asof"), t("col_trend"), t("col_src")),
          "|---|---|---|---|---|"]
    for key, d in ind.items():
        if key == "pl10y_monthly_ecb":
            label = t("ind_pl10y") + " (EBC, mies.)" if lang == "pl" else t("ind_pl10y") + " (ECB, monthly)"
        else:
            label = t("ind_" + key)
        unit = d["unit"]
        if unit == "%":
            val = _p(d["value"], lang, 2)
        elif unit == "pp":
            val = _n(d["value"], lang, 2, True) + " pp"
        elif unit == "bp":
            val = _n(d["value"], lang, 0) + (" pb" if lang == "pl" else " bp")
        elif unit == "PLN":
            val = _n(d["value"], lang, 4)
        else:
            val = _n(d["value"], lang, 2)
        extra = []
        for k, lab in (("change_3m_pct", "3M"), ("change_6m_pct", "6M"), ("change_12m_pct", "12M")):
            if d.get(k) is not None:
                extra.append("{} {}".format(lab, _p(d[k], lang, 1, True)))
        if d.get("change_3m_bp") is not None and unit != "bp":
            extra.append("3M {} {}".format(_n(d["change_3m_bp"], lang, 0, True), "pb" if lang == "pl" else "bp"))
        if d.get("bond"):
            extra.append(d["bond"])
        if extra:
            val += " (" + ", ".join(extra) + ")"
        trend = d.get("trend")
        L.append("| {} | {} | {} | {} | {} |".format(label, val, d.get("as_of") or "—", t(trend) if trend else "—", d["source"]))
    L.append("")

    # sectors
    L += ["## " + t("sectors"), "", t("sectors_note"), "",
          "| {} | {} | {} | {} |".format(t("col_sector"), t("col_tilt"), t("col_drivers"), t("col_market")), "|---|---|---|---|"]
    for key, tl in r["sector_tilts"].items():
        drv = []
        for dd in tl["drivers"]:
            cat, _, val = dd["driver"].partition(":")
            drv.append("{}: {} ({})".format(t("drv_" + cat), t(val) if val != "None" else "—", _n(dd["contribution"], lang, 2, True)))
        mc = tl["market_check"]
        mk = "—"
        if mc["vs_wig_3m_pp"] is not None:
            mk = "{} pp".format(_n(mc["vs_wig_3m_pp"], lang, 1, True))
            if mc["agrees_with_tilt"] is not None:
                mk += " ({})".format(t("agrees") if mc["agrees_with_tilt"] else t("disagrees"))
        L.append("| {} | **{}** | {} | {} |".format(t("sec_" + key), t("tilt_" + tl["tilt"]), "; ".join(drv) or "—", mk))
    L.append("")
    for key, tl in r["sector_tilts"].items():
        L.append("- **{}** — {}".format(t("sec_" + key), tl["rationale"]))
    L.append("")

    # watch
    if r["watch"]:
        L += ["## " + t("watch"), ""] + ["- " + w["text"] for w in r["watch"]] + [""]

    # changes
    L += ["## " + t("changes"), ""]
    if ch is None:
        L.append(t("no_prev"))
    else:
        if ch["changes"]:
            L.append(t("prev_line", f=ch["previous_file"], d=ch["previous_as_of"], ch=_n(ch.get("score_change"), lang, 2, True)))
            L.append("")
            for c in ch["changes"]:
                L.append("- {}: {} → **{}**".format(c["field"], t(c["from"]) if c["from"] else "—", t(c["to"]) if c["to"] else "—"))
        else:
            L.append(t("no_changes", f=ch["previous_file"], d=ch["previous_as_of"]))
    L.append("")

    # history
    if r["history"]:
        L += ["## " + t("history", n=len(r["history"])), "", t("history_note"), "",
              "| {} | {} | {} | {} | {} | {} | {} |".format(t("col_month"), t("col_rate"), t("dim_monetary"), t("col_cpi"),
                                                       t("dim_pln"), t("dim_spread").split(" (")[0], t("col_comp")),
              "|---|---|---|---|---|---|---|"]
        for h in r["history"]:
            L.append("| {} | {} | {} | {} | {} | {} | {} ({}) |".format(
                h["month"], _p(h["rate"], lang) if h["rate"] is not None else "—", t(h["monetary"]) if h["monetary"] else "—",
                (_p(h["inflation_value"], lang, 1) + (" " + t(h["inflation"]) if h["inflation"] else "")) if h["inflation_value"] is not None else "—",
                t(h["pln"]) if h["pln"] else "—", t(h["spread"]).split(" (")[0] if h["spread"] else "—",
                t(h["composite"]) if h["composite"] else "—", _n(h["score"], lang, 2, True)))
        L.append("")

    # sources
    L += ["## " + t("sources"), ""]
    for k, v in prov["per_source"].items():
        name = ms.SOURCES.get(_src_key(k), {}).get("name", k) + (" (−3M)" if k == "bondspot_3m" else "")
        if v.get("ok"):
            L.append("- " + t("src_line", name=name, url=_src_url(v, k), obs=v.get("newest_observation") or "—",
                                ret=v.get("retrieved_at") or "—", cache=t("from_cache") if v.get("from_cache") else ""))
        else:
            L.append("- " + t("src_fail", name=name, url=ms.SOURCES.get(_src_key(k), {}).get("url", ""), err=v.get("error")))
    L += ["", t("method"), "", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
        result["macro"]["summary"] = render_summary(result["macro"], args.lang)
    except (MacroError, reports.ReportError) as exc:
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
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    mp.write_text(md, encoding="utf-8")
    m = result["macro"]
    print("Macro composite: {} (score {}, confidence {}) | monetary {} | pln {} | spread {} | inflation {} | as of {}".format(
        m["state"]["composite"], m["score"], m["confidence"], m["state"]["monetary"], m["state"]["pln"],
        m["state"]["spread"], m["state"]["inflation"], result["as_of"]))
    if m["failed_sources"]:
        print("Unavailable sources: {}".format(", ".join(m["failed_sources"])))
    print("JSON: {}\nMarkdown: {}".format(jp, mp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
