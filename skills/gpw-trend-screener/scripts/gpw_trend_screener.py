#!/usr/bin/env python3
"""GPW Trend Screener - CLI.

Minervini Trend Template + IBD-style RS rating + VCP detection on every liquid
GPW share (dividend/split-adjusted panel from the GPW archive). Three modes:

    python3 scripts/gpw_trend_screener.py --lang pl                 # screen, top 20
    python3 scripts/gpw_trend_screener.py --ticker PKOBP --lang en  # one stock in full
    python3 scripts/gpw_trend_screener.py --history                 # backtest of the screen

Output is a watchlist priority, never a buy call.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import constituents, gpw_archive, panel, provenance  # noqa: E402
from gpwlib.constituents import SECTOR_INDICES  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct  # noqa: E402

import trend_screener_engine as eng  # noqa: E402

SKILL = "gpw-trend-screener"
VERSION = "1.0.0"
PREFIX = "gpw_trend_screener"

STRINGS = {
    "title": {"pl": "Skaner trendu GPW (Minervini + RS + VCP)", "en": "GPW Trend Screener (Minervini + RS + VCP)"},
    "title_ticker": {"pl": "Skaner trendu GPW — analiza {name}", "en": "GPW Trend Screener — {name} breakdown"},
    "title_history": {"pl": "Skaner trendu GPW — test historyczny", "en": "GPW Trend Screener — historical test"},
    "as_of": {"pl": "Dane na sesję", "en": "Data as of session"},
    "generated": {"pl": "Wygenerowano", "en": "Generated"},
    "stale": {"pl": "⚠️ UWAGA: dane są nieaktualne o {n} sesji — wyniki dotyczą {d}.",
              "en": "⚠️ WARNING: data is {n} session(s) old — results refer to {d}."},
    "answer": {"pl": "Odpowiedź", "en": "Answer"},
    "answer_txt": {
        "pl": ("Spośród **{u}** płynnych spółek **{tt}** spełnia Trend Template Minerviniego, **{v}** ma poprawną formację VCP, "
               "a **{a}** jest w strefie działania (tuż pod pivotem lub świeże wybicie z wolumenem). "
               "To lista obserwacyjna uporządkowana wg priorytetu — nie rekomendacja kupna."),
        "en": ("Of **{u}** liquid stocks, **{tt}** pass Minervini's Trend Template, **{v}** show a valid VCP and **{a}** "
               "are in an actionable zone (just below the pivot or a fresh breakout on volume). "
               "This is a watchlist ordered by priority — not a buy recommendation."),
    },
    "states_line": {"pl": "Stany wykonawcze kandydatów", "en": "Execution states of candidates"},
    "market_ctx": {"pl": "Kontekst rynku", "en": "Market context"},
    "market_ctx_txt": {
        "pl": ("Wybicia z baz częściej zawodzą w korekcie i bessie (O'Neil: ~3 na 4 akcje podążają za rynkiem). "
               "Przed działaniem sprawdź `gpw-market-regime` i `gpw-market-breadth`."),
        "en": ("Base breakouts fail more often in corrections and bear markets (O'Neil: ~3 of 4 stocks follow the market). "
               "Check `gpw-market-regime` and `gpw-market-breadth` before acting."),
    },
    "regime_none": {"pl": "Brak pliku gpw-market-regime — kontekst rynku nieznany (niższa pewność).",
                    "en": "No gpw-market-regime file — market context unknown (lower confidence)."},
    "regime_line": {"pl": "Reżim rynku (gpw-market-regime, {f}, na {d}): **{s}**.",
                    "en": "Market regime (gpw-market-regime, {f}, as of {d}): **{s}**."},
    "regime_high": {"pl": "⚠️ Rynek w stanie **{s}** — w takim otoczeniu większość wybić zawodzi. Traktuj listę wyłącznie jako obserwacyjną, rozważ mniejsze pozycje lub brak nowych wejść.",
                    "en": "⚠️ Market state is **{s}** — most breakouts fail in this environment. Treat the list as watch-only; consider smaller positions or no new entries."},
    "regime_medium": {"pl": "⚠️ Rynek w stanie **{s}** — selektywność i mniejsze pozycje.",
                      "en": "⚠️ Market state is **{s}** — be selective and size down."},
    "regime_stale": {"pl": "⚠️ Plik reżimu jest starszy o {n} sesji niż dane skanu.",
                     "en": "⚠️ The regime file is {n} session(s) older than the screen data."},
    "st_confirmed_uptrend": {"pl": "potwierdzony trend wzrostowy", "en": "confirmed uptrend"},
    "st_uptrend_under_pressure": {"pl": "trend wzrostowy pod presją", "en": "uptrend under pressure"},
    "st_rally_attempt": {"pl": "próba odbicia", "en": "rally attempt"},
    "st_correction": {"pl": "korekta", "en": "correction"},
    "st_bear": {"pl": "bessa", "en": "bear market"},
    "watchlist": {"pl": "Lista obserwacyjna (priorytet)", "en": "Watchlist (priority)"},
    "no_cands": {"pl": "Żadna spółka nie przechodzi teraz pełnego Trend Template z poprawnym ustawieniem — to też jest informacja (słaby lub selektywny rynek).",
                 "en": "No stock currently passes the full Trend Template with a usable setup — that is information too (weak or selective market)."},
    "c_n": {"pl": "#", "en": "#"},
    "c_name": {"pl": "Spółka", "en": "Stock"},
    "c_grp": {"pl": "Indeks / sektor", "en": "Index / sector"},
    "c_close": {"pl": "Kurs", "en": "Close"},
    "c_rs": {"pl": "RS", "en": "RS"},
    "c_tt": {"pl": "TT", "en": "TT"},
    "c_vcp": {"pl": "Skurczenia (głęb. %)", "en": "Contractions (depth %)"},
    "c_pivot": {"pl": "Pivot", "en": "Pivot"},
    "c_state": {"pl": "Stan", "en": "State"},
    "c_dist": {"pl": "vs pivot", "en": "vs pivot"},
    "c_score": {"pl": "Wynik", "en": "Score"},
    "c_band": {"pl": "Priorytet", "en": "Priority"},
    "c_stop": {"pl": "Unieważnienie", "en": "Invalidation"},
    "c_risk": {"pl": "Ryzyko", "en": "Risk"},
    "others": {"pl": "Trend Template OK, ale bez gotowego ustawienia", "en": "Trend Template OK but no usable setup"},
    "others_txt": {"pl": "Spółki w silnym trendzie, ale bez poprawnej formacji VCP, rozciągnięte lub uszkodzone — do obserwacji, aż zbudują bazę:",
                   "en": "Stocks in a strong trend but without a valid VCP, extended or damaged — watch until they build a base:"},
    "plans": {"pl": "Plan ryzyka dla kandydatów", "en": "Risk plan for candidates"},
    "plan_line": {"pl": "**{n}** — pivot {p}; unieważnienie pod dołkiem ostatniego skurczenia: **{s}** (ryzyko {r} od pivotu); alternatywa ATR: {sa} ({ra}, 2×ATR14 = {atr}).",
                  "en": "**{n}** — pivot {p}; invalidation below the last contraction low: **{s}** (risk {r} from the pivot); ATR alternative: {sa} ({ra}, 2×ATR14 = {atr})."},
    "wide": {"pl": " ⚠️ Ryzyko > {m}% — szeroki stop (O'Neil/Minervini: cięcie strat przy 7–8%); poczekaj na ciaśniejsze ustawienie lub zmniejsz pozycję.",
             "en": " ⚠️ Risk > {m}% — wide stop (O'Neil/Minervini cut losses at 7–8%); wait for a tighter setup or size down."},
    "sizer": {"pl": "Wielkość pozycji — uruchom w katalogu skilla `gpw-position-sizer`:",
              "en": "Position size — run from the `gpw-position-sizer` skill directory:"},
    "acct_note": {"pl": "Zastąp `ACCOUNT_PLN` wartością rachunku w zł (albo podaj `--account-size` temu skanerowi).",
                  "en": "Replace `ACCOUNT_PLN` with the account value in PLN (or pass `--account-size` to this screener)."},
    "legend": {"pl": "Jak czytać", "en": "How to read"},
    "legend_txt": {
        "pl": ("- **TT** — liczba spełnionych z 8 warunków Trend Template (kurs nad SMA50/150/200, ułożenie średnich, rosnąca SMA200, "
               "≥30% nad dołkiem 52 tyg., ≤25% pod szczytem 52 tyg., RS ≥ {rs}).\n"
               "- **RS** — siła relatywna 1–99: percentyl ważonej zmiany kursu z 12 miesięcy (ostatni kwartał ×2) na tle płynnych spółek GPW.\n"
               "- **VCP** — kolejne korekty coraz płytsze (np. 20% → 10% → 5%) przy słabnących obrotach; **pivot** to szczyt ostatniego skurczenia.\n"
               "- **Stany:** `pre_breakout` ≤5% pod pivotem · `breakout` nad pivotem ≤5% i obrót ≥1,4× mediany 50 sesji · "
               "`breakout_unconfirmed` nad pivotem bez wolumenu · `extended` >5% nad pivotem lub >25% nad SMA50 (nie gonić) · "
               "`forming` baza w budowie (>5% pod pivotem) · `damaged` kurs pod SMA50 lub pod dołkiem ostatniego skurczenia · `invalid` brak formacji.\n"
               "- **Priorytet:** 90+ podręcznikowy · 80–89 silny · 70–79 dobry · 60–69 obserwuj · <60 pomiń. "
               "Stany `extended`, `forming`, `breakout_unconfirmed` obniżają priorytet najwyżej do „obserwuj”, `damaged`/`invalid` do „pomiń”."),
        "en": ("- **TT** — Trend Template criteria met out of 8 (close above SMA50/150/200, MA alignment, rising SMA200, "
               "≥30% above the 52-week low, ≤25% below the 52-week high, RS ≥ {rs}).\n"
               "- **RS** — relative strength 1–99: percentile of the weighted 12-month price change (latest quarter ×2) among liquid GPW stocks.\n"
               "- **VCP** — successive pullbacks getting shallower (e.g. 20% → 10% → 5%) on drying-up turnover; the **pivot** is the high of the last contraction.\n"
               "- **States:** `pre_breakout` ≤5% below the pivot · `breakout` above the pivot by ≤5% with turnover ≥1.4× the 50-session median · "
               "`breakout_unconfirmed` above the pivot without volume · `extended` >5% above the pivot or >25% above SMA50 (do not chase) · "
               "`forming` base still building (>5% below pivot) · `damaged` close below SMA50 or below the last contraction low · `invalid` no pattern.\n"
               "- **Priority:** 90+ textbook · 80–89 strong · 70–79 good · 60–69 watchlist · <60 skip. "
               "`extended`, `forming`, `breakout_unconfirmed` cap the priority at watchlist; `damaged`/`invalid` at skip."),
    },
    "b_textbook": {"pl": "podręcznikowy", "en": "textbook"},
    "b_strong": {"pl": "silny", "en": "strong"},
    "b_good": {"pl": "dobry", "en": "good"},
    "b_watchlist": {"pl": "obserwuj", "en": "watchlist"},
    "b_skip": {"pl": "pomiń", "en": "skip"},
    # ticker mode
    "not_found": {"pl": "Nie znaleziono spółki {t} w archiwum GPW. Podobne: {s}",
                  "en": "Ticker {t} not found in the GPW archive. Similar: {s}"},
    "no_session": {"pl": "Spółka {t} nie była notowana na sesji {d}.", "en": "{t} did not trade on {d}."},
    "verdict": {"pl": "Werdykt", "en": "Verdict"},
    "verdict_txt": {"pl": "Trend Template: **{p}/{n}** · RS **{rs}** · VCP: **{v}** · stan: **{s}** · wynik **{sc}/100** → priorytet **{b}**.",
                    "en": "Trend Template: **{p}/{n}** · RS **{rs}** · VCP: **{v}** · state: **{s}** · score **{sc}/100** → priority **{b}**."},
    "yes": {"pl": "tak", "en": "yes"},
    "no": {"pl": "nie", "en": "no"},
    "prefilter": {"pl": "Filtr wstępny", "en": "Pre-filter"},
    "pf_ok": {"pl": "Spełniony (cena ≥ {p} zł, mediana obrotu 20 sesji {m} tys. zł ≥ {t} tys., historia {h} sesji).",
              "en": "Passed (price ≥ {p} PLN, 20-session median turnover {m}k PLN ≥ {t}k, history {h} sessions)."},
    "pf_fail": {"pl": "Niespełniony: {r}. Spółka nie wchodzi do rankingu RS ani skanu; poniżej pełna analiza informacyjnie.",
                "en": "Failed: {r}. The stock is excluded from the RS ranking and the screen; full breakdown below for information."},
    "r_history": {"pl": "za krótka historia (< {n} sesji)", "en": "history too short (< {n} sessions)"},
    "r_price": {"pl": "cena < {p} zł", "en": "price < {p} PLN"},
    "r_liquidity": {"pl": "mediana obrotu < {t} tys. zł", "en": "median turnover < {t}k PLN"},
    "short_hist": {"pl": "Historia krótsza niż 252 sesje — szczyt/dołek 52 tyg. i RS liczone z dostępnych danych.",
                   "en": "History shorter than 252 sessions — 52-week high/low and RS use the available data."},
    "rs_sec": {"pl": "Siła relatywna", "en": "Relative strength"},
    "rs_txt": {"pl": "RS rating **{r}** (surowy wynik {raw}, kwartały: {q}). Względem WIG: 3 mies. {a} · 6 mies. {b} · 12 mies. {c}",
               "en": "RS rating **{r}** (raw {raw}, quarters: {q}). Versus WIG: 3 m {a} · 6 m {b} · 12 m {c}."},
    "tt_sec": {"pl": "Trend Template", "en": "Trend Template"},
    "c_crit": {"pl": "Warunek", "en": "Criterion"},
    "c_val": {"pl": "Wartość", "en": "Value"},
    "c_thr": {"pl": "Próg", "en": "Threshold"},
    "c_ok": {"pl": "OK?", "en": "Pass?"},
    "tt_close_above_sma150_sma200": {"pl": "Kurs > SMA150 i > SMA200", "en": "Close > SMA150 and > SMA200"},
    "tt_sma150_above_sma200": {"pl": "SMA150 > SMA200", "en": "SMA150 > SMA200"},
    "tt_sma200_rising": {"pl": "SMA200 rośnie (vs 21 sesji temu)", "en": "SMA200 rising (vs 21 sessions ago)"},
    "tt_sma50_above_sma150_sma200": {"pl": "SMA50 > SMA150 i > SMA200", "en": "SMA50 > SMA150 and > SMA200"},
    "tt_close_above_sma50": {"pl": "Kurs > SMA50", "en": "Close > SMA50"},
    "tt_above_52w_low": {"pl": "Kurs ≥ 1,30 × dołek 52 tyg.", "en": "Close ≥ 1.30 × 52-week low"},
    "tt_near_52w_high": {"pl": "Kurs ≥ 0,75 × szczyt 52 tyg.", "en": "Close ≥ 0.75 × 52-week high"},
    "tt_rs_rating": {"pl": "RS rating ≥ {rs}", "en": "RS rating ≥ {rs}"},
    "tt_na": {"pl": "Za mało danych do Trend Template (potrzeba SMA200 i jej zmiany z 21 sesji).",
              "en": "Not enough data for the Trend Template (needs SMA200 and its 21-session change)."},
    "tt_extra": {"pl": "Szczyt 52 tyg. {h} ({fh}) · dołek {l} (+{al}) · kurs vs SMA50 {s50}.",
                 "en": "52-week high {h} ({fh}) · low {l} (+{al}) · close vs SMA50 {s50}."},
    "vcp_sec": {"pl": "Formacja VCP (ostatnie ~120 sesji)", "en": "VCP (last ~120 sessions)"},
    "c_from": {"pl": "Szczyt (data)", "en": "High (date)"},
    "c_to": {"pl": "Dołek (data)", "en": "Low (date)"},
    "c_depth": {"pl": "Głębokość", "en": "Depth"},
    "vcp_valid": {"pl": "Formacja poprawna: {n} skurczenia, baza {b} sesji, pivot **{p}**, dołek ostatniego skurczenia {l}, wyższe dołki: {hl}, jakość {q}/100.",
                  "en": "Valid pattern: {n} contractions, base {b} sessions, pivot **{p}**, last contraction low {l}, higher lows: {hl}, quality {q}/100."},
    "vcp_invalid": {"pl": "Brak poprawnej formacji: {r}.", "en": "No valid pattern: {r}."},
    "dryup": {"pl": "Wysychanie obrotu (mediana 10 / 50 sesji): {d} (<1 = obroty maleją, typowe dla VCP).",
              "en": "Turnover dry-up (10 / 50-session median): {d} (<1 = turnover shrinking, typical for a VCP)."},
    "v_short_window": {"pl": "za krótkie okno danych", "en": "data window too short"},
    "v_no_swing_high": {"pl": "brak szczytu lokalnego", "en": "no swing high"},
    "v_no_contraction": {"pl": "brak korekty", "en": "no pullback"},
    "v_too_few_contractions": {"pl": "za mało coraz płytszych korekt (min. 2)", "en": "too few successively shallower pullbacks (min 2)"},
    "v_first_too_deep": {"pl": "pierwsza korekta głębsza niż 40%", "en": "first pullback deeper than 40%"},
    "v_last_too_loose": {"pl": "ostatnia korekta głębsza niż 12%", "en": "last pullback deeper than 12%"},
    "v_base_too_short": {"pl": "baza krótsza niż 15 sesji (świeży szczyt)", "en": "base shorter than 15 sessions (fresh high)"},
    "v_pivot_far_below_base": {"pl": "pivot ponad 15% pod szczytem bazy", "en": "pivot more than 15% below the base high"},
    "exec_sec": {"pl": "Stan wykonawczy", "en": "Execution state"},
    "exec_txt": {"pl": "Stan **{s}** · kurs vs pivot {d} · vs SMA50 {s50} · obrót dziś / mediana 50 sesji {v}×.",
                 "en": "State **{s}** · close vs pivot {d} · vs SMA50 {s50} · today's turnover / 50-session median {v}×."},
    "score_sec": {"pl": "Wynik i priorytet", "en": "Score and priority"},
    "c_comp": {"pl": "Składnik", "en": "Component"},
    "c_w": {"pl": "Waga", "en": "Weight"},
    "k_trend": {"pl": "Siła Trend Template", "en": "Trend Template strength"},
    "k_rs": {"pl": "Siła relatywna", "en": "Relative strength"},
    "k_contraction": {"pl": "Jakość skurczeń", "en": "Contraction quality"},
    "k_dryup": {"pl": "Wysychanie obrotu", "en": "Volume dry-up"},
    "k_pivot": {"pl": "Odległość od pivotu", "en": "Distance to pivot"},
    "k_liquidity": {"pl": "Płynność", "en": "Liquidity"},
    "capped": {"pl": "Priorytet obniżony ze względu na stan ({raw} → {b}).", "en": "Priority capped by the state ({raw} → {b})."},
    "risk_sec": {"pl": "Poziomy unieważnienia", "en": "Invalidation levels"},
    "no_plan": {"pl": "Brak poprawnej formacji, więc nie ma pivotu, od którego można liczyć ryzyko. ATR14 = {a}.",
                "en": "No valid pattern, so there is no pivot to measure risk from. ATR14 = {a}."},
    # history
    "hist_answer": {
        "pl": "Skan uruchomiono **{n}** razy (co {s} sesji, {a} → {b}). Spółki oznaczone (stan `pre_breakout`/`breakout`, priorytet ≥ obserwuj) vs WIG:",
        "en": "The screen was run **{n}** times (every {s} sessions, {a} → {b}). Flagged stocks (state `pre_breakout`/`breakout`, priority ≥ watchlist) vs WIG:"},
    "c_group": {"pl": "Grupa", "en": "Group"},
    "c_h": {"pl": "Horyzont", "en": "Horizon"},
    "c_cnt": {"pl": "Sygnały", "en": "Signals"},
    "c_hit": {"pl": "Trafność (>0)", "en": "Hit rate (>0)"},
    "c_mean": {"pl": "Średnia", "en": "Mean"},
    "c_med": {"pl": "Mediana", "en": "Median"},
    "c_mex": {"pl": "Śr. nadwyżka vs WIG", "en": "Mean excess vs WIG"},
    "c_mdex": {"pl": "Mediana nadwyżki", "en": "Median excess"},
    "c_beat": {"pl": "Lepsze od WIG", "en": "Beat WIG"},
    "g_flagged": {"pl": "Oznaczone (VCP, strefa działania)", "en": "Flagged (VCP, actionable)"},
    "g_template": {"pl": "Wszystkie z Trend Template", "en": "All Trend Template passers"},
    "g_universe": {"pl": "Cały płynny rynek (punkt odniesienia)", "en": "Whole liquid universe (baseline)"},
    "sess": {"pl": "{h} sesji", "en": "{h} sessions"},
    "hist_meta": {"pl": "Unikalnych oznaczonych spółek: {t}. Sygnałów, w których spółka przestała być notowana przed końcem horyzontu: {s}.",
                  "en": "Distinct flagged tickers: {t}. Signals where the stock stopped trading before the horizon ended: {s}."},
    "caveats": {"pl": "Zastrzeżenia (czytaj przed wyciąganiem wniosków)", "en": "Caveats (read before drawing conclusions)"},
    "caveats_txt": {
        "pl": ("- **Mała próba:** ~{n} dat skanu z jednego roku; sygnały co 5 sesji z horyzontem 20/60 sesji nakładają się, więc efektywna liczba niezależnych obserwacji jest dużo mniejsza niż liczba sygnałów.\n"
               "- **Zależność od reżimu:** wynik opisuje jeden okres rynkowy (WIG {w} w badanym okresie). W korekcie lub bessie te same zasady dają zwykle znacznie gorsze wyniki.\n"
               "- **Bez kosztów i poślizgu:** stopa zwrotu liczona od zamknięcia w dniu skanu do zamknięcia po 20/60 sesjach, bez prowizji, podatku i bez stop-lossów; realne wejście na wybiciu pivotu i wyjście na stopie dają inny wynik.\n"
               "- **Brak problemu „obecnego składu”:** archiwum GPW zawiera wszystkie notowane wtedy spółki, także później wycofane — test jest wolny od błędu przeżywalności (survivorship-free). Spółka, która przestała być notowana, liczy się po ostatnim kursie.\n"
               "- **Parametry nie były optymalizowane** pod ten okres (progi z literatury), ale to wciąż jeden okres — nie traktuj wyniku jako prognozy."),
        "en": ("- **Small sample:** ~{n} screen dates from one year; signals every 5 sessions with 20/60-session horizons overlap, so the effective number of independent observations is far smaller than the signal count.\n"
               "- **Regime dependence:** this describes one market period (WIG {w} over the window). In corrections or bear markets the same rules usually do much worse.\n"
               "- **No costs or slippage:** return from the close on the screen date to the close 20/60 sessions later, no commissions, tax or stop-losses; real entries on a pivot break and exits on a stop differ.\n"
               "- **No current-constituent bias:** the GPW archive holds every stock that traded then, including later delistings — the test is survivorship-free. A stock that stopped trading counts at its last price.\n"
               "- **Parameters were not fitted** to this period (literature thresholds), but it is still one period — do not treat the result as a forecast."),
    },
    "hist_none": {"pl": "Za mało danych do testu (potrzeba ≥ 252 sesji historii + horyzontu).",
                  "en": "Not enough data for the test (needs ≥ 252 sessions of history + horizon)."},
    "sources": {"pl": "Źródła", "en": "Sources"},
    "method_note": {"pl": "Metodyka i uzasadnienie progów: `references/methodology.md`.",
                    "en": "Methodology and threshold rationale: `references/methodology.md`."},
}


# ---------------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="GPW trend screener: Minervini Trend Template + RS + VCP (PL/EN)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--ticker", help="Full breakdown for one GPW short name (e.g. PKOBP)")
    mode.add_argument("--history", action="store_true", help="Backtest the screen over the past ~250 sessions")
    p.add_argument("--top", type=int, default=20, help="Rows in the watchlist table (default 20)")
    p.add_argument("--lang", choices=["pl", "en"], default="pl")
    p.add_argument("--output-dir", default=".")
    p.add_argument("--no-files", action="store_true", help="Print Markdown only, write no files")
    p.add_argument("--cache-dir", default=None)
    p.add_argument("--offline", action="store_true", help="Use only the local GPW cache")
    p.add_argument("--as-of", default=None, help="Screen as of this session (YYYY-MM-DD)")
    p.add_argument("--sessions", type=int, default=500, help="Sessions to load (default 500, min 260)")
    p.add_argument("--min-turnover-k", type=float, default=eng.DEFAULTS["min_turnover_k"],
                   help="Min 20-session median turnover in thousand PLN (default 200)")
    p.add_argument("--min-price", type=float, default=eng.DEFAULTS["min_price"])
    p.add_argument("--min-rs", type=int, default=eng.DEFAULTS["min_rs"], help="RS rating threshold (default 70)")
    p.add_argument("--min-criteria", type=int, choices=[6, 7, 8], default=eng.DEFAULTS["min_criteria"],
                   help="Trend Template criteria required (default 8 = Minervini)")
    p.add_argument("--history-sessions", type=int, default=250)
    p.add_argument("--history-step", type=int, default=5)
    p.add_argument("--regime-json", default=None, help="gpw-market-regime JSON (else newest in --input-dir)")
    p.add_argument("--input-dir", default=".", help="Where to look for gpw-market-regime JSON")
    p.add_argument("--account-size", type=float, default=None, help="Fills the position-sizer commands")
    p.add_argument("--risk-pct", type=float, default=1.0, help="Risk %% used in the position-sizer commands")
    a = p.parse_args(argv)
    if a.sessions < 260:
        p.error("--sessions must be >= 260")
    if a.top < 1:
        p.error("--top must be >= 1")
    if a.min_turnover_k < 0 or a.min_price < 0:
        p.error("--min-turnover-k and --min-price must be >= 0")
    if not 1 <= a.min_rs <= 99:
        p.error("--min-rs must be between 1 and 99")
    if a.history_step < 1 or a.history_sessions < 20:
        p.error("--history-step must be >= 1 and --history-sessions >= 20")
    if a.account_size is not None and a.account_size <= 0:
        p.error("--account-size must be > 0")
    if not 0 < a.risk_pct <= 10:
        p.error("--risk-pct must be in (0, 10]")
    if a.as_of:
        try:
            dt.date.fromisoformat(a.as_of)
        except ValueError:
            p.error("--as-of must be YYYY-MM-DD")
    return a


def config(args) -> dict:
    return dict(eng.DEFAULTS, min_turnover_k=args.min_turnover_k, min_price=args.min_price,
                min_rs=args.min_rs, min_criteria=args.min_criteria)


def load_data(args):
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else None
    end = dt.date.fromisoformat(args.as_of) if args.as_of else None
    print("Loading GPW sessions (first run downloads ~2 years, one-off)...", file=sys.stderr)
    shares = gpw_archive.load_sessions(args.sessions, end=end, cache_dir=cache_dir, offline=args.offline)
    if len(shares) < 260:
        raise gpw_archive.GPWFetchError("Only {} share sessions available; need at least 260.".format(len(shares)))
    indices = gpw_archive.load_sessions(args.sessions, end=end, type_id=gpw_archive.TYPE_INDICES,
                                        cache_dir=cache_dir, offline=args.offline, progress=False)
    w = panel.index_series(indices, "WIG")
    wig = eng.RefSeries(w.dates, w.close) if len(w) else None
    data = eng.prepare(panel.build_panel(shares))
    dates = [s["date"] for s in shares]
    try:
        size = constituents.size_membership(cache_dir, offline=args.offline)
        sector = constituents.sector_membership(cache_dir, offline=args.offline)
    except Exception:  # labels are cosmetic; never fail the screen on them
        size, sector = {}, {}
    return data, dates, wig, size, sector, len(shares), cache_dir


def find_regime(args, dates):
    """Explicit --regime-json, else the newest valid gpw-market-regime JSON in --input-dir."""
    paths = []
    if args.regime_json:
        p = Path(args.regime_json).expanduser()
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("cannot read --regime-json {}: {}".format(p, exc))
        reg = eng.parse_regime(doc)
        if reg is None:
            raise ValueError("--regime-json {} is not a gpw-market-regime report".format(p))
        reg["file"] = str(p)
    else:
        d = Path(args.input_dir).expanduser()
        if d.is_dir():
            paths = sorted(d.glob("gpw_*regime*.json"), key=lambda q: q.stat().st_mtime, reverse=True)
        reg = None
        for p in paths:
            try:
                reg = eng.parse_regime(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                reg = None
            if reg:
                reg["file"] = str(p)
                break
    if reg and reg.get("as_of"):
        reg["sessions_older"] = eng.sessions_between(dates, reg["as_of"], dates[-1])
        reg["stale"] = reg["sessions_older"] > 3
    return reg


def _labels(name, size, sector, lang):
    out = []
    if size.get(name):
        out.append(size[name].replace("MWIG40", "mWIG40").replace("SWIG80", "sWIG80"))
    sk = sector.get(name)
    if sk:
        out.append(SECTOR_INDICES[sk][2] if lang == "pl" else SECTOR_INDICES[sk][1])
    return out


def _slim(r):
    """Drop internal indices from a candidate for the JSON report."""
    if r.get("vcp"):
        r["vcp"]["contractions"] = [{k: v for k, v in c.items() if not k.endswith("_idx")} for c in r["vcp"]["contractions"]]
    return r


def _ref(args):
    """Requested --as-of date (freshness is judged against it, not today)."""
    return dt.date.fromisoformat(args.as_of) if getattr(args, "as_of", None) else None


def provenance_block(dates, n_sessions, universe, notes, reference_date=None):
    return provenance.build(reference_date=reference_date,
        as_of=dt.date.fromisoformat(dates[-1]),
        sources=["GPW — Archiwum notowań (akcje, indeksy): https://www.gpw.pl/archiwum-notowan",
                 "GPW Benchmark — portfele indeksów (etykiety WIG20/mWIG40/sWIG80 i sektorów): https://gpwbenchmark.pl"],
        sessions_used=n_sessions, universe_size=universe,
        notes=notes, corporate_actions_adjusted=True, survivorship_free=True, point_in_time_membership=False)


BASE_NOTES = [
    "Prices dividend/split-adjusted from GPW's published % change (gpwlib.panel).",
    "Universe = every PLN share in the daily GPW archive (survivorship-free); index/sector labels are current membership, display only.",
    "Trend Template: Minervini, Trade Like a Stock Market Wizard (2013), ch. 5; VCP: ch. 10; RS: IBD-style weighted 12-month performance.",
]


# ---------------------------------------------------------------------------
def run(args) -> dict:
    cfg = config(args)
    data, dates, wig, size, sector, n_sess, _cache = load_data(args)
    regime = find_regime(args, dates)
    date = dates[-1]
    common = {"skill": SKILL, "version": VERSION, "lang": args.lang, "config": cfg, "regime": regime,
              "account_size": args.account_size, "risk_pct": args.risk_pct}
    if args.history:
        bt = eng.backtest(data, dates, wig, cfg, lookback=args.history_sessions, step=args.history_step)
        wig_ret = wig.ret(bt["first_date"], dates[-1]) if (wig and bt["first_date"]) else None
        res = dict(common, mode="history", history=bt, wig_return_window_pct=wig_ret)
        res["data_provenance"] = provenance_block(dates, n_sess, None, BASE_NOTES + [
            "Backtest: screen re-run with data up to each date only (RS ranks, SMAs, swings point-in-time)."],
            reference_date=_ref(args))
        return res
    sc = eng.screen_at(data, date, wig, cfg)
    if args.ticker:
        name = args.ticker.strip().upper()
        if name not in data:
            close = difflib.get_close_matches(name, list(data), n=5, cutoff=0.5)
            raise LookupError(T(STRINGS, args.lang)("not_found", t=name, s=", ".join(close) or "—"))
        sd = data[name]
        i = sd.index_at(date)
        if i is None:
            raise LookupError(T(STRINGS, args.lang)("no_session", t=name, d=date))
        rating = sc["ratings"].get(name)
        if rating is None:
            raw, _q = eng.rs_raw(sd.close, i)
            rating = eng.rating_against(sc["rs_sorted"], raw)
        r = eng.analyze(sd, i, rating, wig, cfg, full=True)
        r["labels"] = _labels(name, size, sector, args.lang)
        r["in_universe"] = name in sc["ratings"]
        r["sizer_command"] = eng.sizer_command(name, r["risk"], args.account_size, args.risk_pct, args.lang)
        res = dict(common, mode="ticker", result=_slim(r), universe=sc["universe"])
        res["data_provenance"] = provenance_block(dates, n_sess, sc["universe"], BASE_NOTES, reference_date=_ref(args))
        return res
    cands = sc["candidates"]
    for r in cands:
        r["labels"] = _labels(r["name"], size, sector, args.lang)
        r["sizer_command"] = eng.sizer_command(r["name"], r["risk"], args.account_size, args.risk_pct, args.lang)
        _slim(r)
    top = [r for r in cands if r["band"] != "skip"][: args.top]
    rest = [r for r in cands if r["band"] == "skip"]
    res = dict(common, mode="screen", summary={
        "date": date, "universe": sc["universe"], "traded": sc["traded"], "template_passed": sc["template_passed"],
        "valid_vcp": sc["valid_vcp"], "actionable": sum(1 for r in cands if eng.is_flagged(r)), "states": sc["states"]},
        watchlist=top, others=[{"name": r["name"], "state": r["execution"]["state"], "rs": r["rs"]["rating"],
                                "score": r["score"], "vcp_reasons": (r["vcp"] or {}).get("reasons")} for r in rest],
        candidates=cands)
    res["data_provenance"] = provenance_block(dates, n_sess, sc["universe"], BASE_NOTES, reference_date=_ref(args))
    return res


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def _pp(x, lang):
    return "—" if x is None else ("+" if x > 0 else "") + num(x, 1, lang) + (" pp" if lang == "en" else " pkt proc.")


def _price(x, lang):
    if x is None:
        return "—"
    return num(x, 2 if x < 1000 else 0, lang)


def _header(r, t, title):
    prov = r["data_provenance"]
    L = ["# " + title, "", "**{}:** {} · **{}:** {}".format(t("as_of"), prov["as_of"], t("generated"), prov["retrieved_at_utc"]), ""]
    if prov["freshness"]["is_stale"]:
        L += [t("stale", n=prov["freshness"]["sessions_behind"], d=prov["as_of"]), ""]
    reg = r.get("regime")
    if reg:
        s = t("st_" + reg["state"])
        if reg["warning_level"] == "high":
            L += [t("regime_high", s=s), ""]
        elif reg["warning_level"] == "medium":
            L += [t("regime_medium", s=s), ""]
        if reg.get("stale"):
            L += [t("regime_stale", n=reg["sessions_older"]), ""]
    return L


def _context(r, t):
    reg = r.get("regime")
    L = ["## " + t("market_ctx"), "", t("market_ctx_txt")]
    if reg:
        L += ["", t("regime_line", f=Path(reg["file"]).name, d=reg.get("as_of") or "—", s=t("st_" + reg["state"]))]
    else:
        L += ["", t("regime_none")]
    return L + [""]


def _footer(r, t, lang):
    return (["## " + t("sources"), ""] + ["- " + s for s in r["data_provenance"]["sources"]]
            + ["- " + t("method_note"), "", "---", "_" + DISCLAIMER[lang] + "_", ""])


def _sizer_note(r, t):
    return t("sizer") + (" " + t("acct_note") if r.get("account_size") is None else "")


def _plan_lines(r, t, lang, cfg):
    rk = r["risk"]
    if not rk.get("entry_pivot"):
        return []
    line = t("plan_line", n=r["name"], p=_price(rk["entry_pivot"], lang), s=_price(rk["stop_structure"], lang),
             r=pct(rk["risk_pct_structure"], 1, lang), sa=_price(rk["stop_atr"], lang), ra=pct(rk["risk_pct_atr"], 1, lang),
             atr=_price(rk["atr14"] * cfg["atr_mult"] if rk["atr14"] else None, lang))
    if rk.get("wide_structure"):
        line += t("wide", m=num(cfg["max_risk_pct"], 0, lang))
    out = ["- " + line]
    if r.get("sizer_command"):
        out.append("  `{}`".format(r["sizer_command"]))
    return out


def render_screen(r, lang):
    t = T(STRINGS, lang)
    s, cfg = r["summary"], r["config"]
    L = _header(r, t, t("title"))
    L += ["## " + t("answer"), "", t("answer_txt", u=s["universe"], tt=s["template_passed"], v=s["valid_vcp"], a=s["actionable"]), ""]
    if s["states"]:
        L += ["{}: {}".format(t("states_line"), " · ".join("`{}` {}".format(k, v) for k, v in sorted(s["states"].items()))), ""]
    L += _context(r, t)
    L += ["## " + t("watchlist"), ""]
    wl = r["watchlist"]
    if not wl:
        L += [t("no_cands"), ""]
    else:
        L += ["| {} |".format(" | ".join(t(k) for k in ("c_n", "c_name", "c_grp", "c_close", "c_rs", "c_tt", "c_vcp", "c_pivot",
                                                       "c_state", "c_dist", "c_score", "c_band", "c_stop", "c_risk"))),
              "|" + "---|" * 14]
        for n, c in enumerate(wl, 1):
            v, rk, ex = c["vcp"] or {}, c["risk"], c["execution"]
            depths = " → ".join(num(x["depth_pct"], 1, lang) for x in v.get("contractions", [])) if v.get("valid") else "—"
            L.append("| {} | **{}** | {} | {} | {} | {}/{} | {} | {} | `{}` | {} | {} | {} | {} | {} |".format(
                n, c["name"], ", ".join(c.get("labels") or []) or "—", _price(c["close"], lang), c["rs"]["rating"],
                c["template"]["passed"], c["template"]["total"], depths, _price(v.get("pivot"), lang),
                ex["state"], pct(ex["pct_vs_pivot"], 1, lang, signed=True), num(c["score"], 0, lang), t("b_" + c["band"]),
                _price(rk.get("stop_structure"), lang), pct(rk.get("risk_pct_structure"), 1, lang)))
        L.append("")
        L += ["### " + t("plans"), "", _sizer_note(r, t), ""]
        for c in wl:
            L += _plan_lines(c, t, lang, cfg)
        L.append("")
    if r["others"]:
        L += ["## " + t("others"), "", t("others_txt"), ""]
        L += [", ".join("{} (`{}`, RS {})".format(o["name"], o["state"], o["rs"]) for o in r["others"]), ""]
    L += ["## " + t("legend"), "", t("legend_txt", rs=cfg["min_rs"]), ""]
    return "\n".join(L + _footer(r, t, lang))


def render_ticker(r, lang):
    t = T(STRINGS, lang)
    c, cfg = r["result"], r["config"]
    L = _header(r, t, t("title_ticker", name=c["name"]))
    tt, v, ex, rk, pf = c["template"], c["vcp"] or {}, c["execution"], c["risk"], c["prefilter"]
    L += ["## " + t("verdict"), "", t("verdict_txt", p=tt["passed"], n=tt["total"], rs=c["rs"]["rating"] if c["rs"]["rating"] is not None else "—",
                                      v=t("yes") if v.get("valid") else t("no"), s=ex["state"], sc=num(c["score"], 0, lang),
                                      b=t("b_" + c["band"])), ""]
    if c.get("labels"):
        L += [" · ".join(c["labels"]), ""]
    L += _context(r, t)
    L += ["## " + t("prefilter"), ""]
    if pf["ok"]:
        L.append(t("pf_ok", p=num(cfg["min_price"], 2, lang), m=num(pf["med_turnover20_k"], 0, lang),
                   t=num(cfg["min_turnover_k"], 0, lang), h=pf["history"]))
    else:
        rs = {"history": t("r_history", n=cfg["min_history"]), "price": t("r_price", p=num(cfg["min_price"], 2, lang)),
              "liquidity": t("r_liquidity", t=num(cfg["min_turnover_k"], 0, lang))}
        L.append(t("pf_fail", r="; ".join(rs[x] for x in pf["reasons"])))
    if pf.get("short_history"):
        L += ["", t("short_hist")]
    vw = c["rs"]["vs_wig_pp"]
    L += ["", "## " + t("rs_sec"), "", t("rs_txt", r=c["rs"]["rating"] if c["rs"]["rating"] is not None else "—",
                                         raw=num(c["rs"]["raw"], 1, lang), q=c["rs"]["quarters"],
                                         a=_pp(vw.get("63"), lang), b=_pp(vw.get("126"), lang), c=_pp(vw.get("252"), lang)), ""]
    L += ["## " + t("tt_sec"), ""]
    if not tt["available"]:
        L += [t("tt_na"), ""]
    else:
        L += ["| {} | {} | {} | {} |".format(t("c_crit"), t("c_val"), t("c_thr"), t("c_ok")), "|---|---|---|---|"]
        for cr in tt["criteria"]:
            fmtv = (lambda x: str(x) if x is not None else "—") if cr["key"] == "rs_rating" else (lambda x: _price(x, lang))
            L.append("| {} | {} | {} | {} |".format(t("tt_" + cr["key"], rs=cfg["min_rs"]), fmtv(cr["value"]), fmtv(cr["threshold"]),
                                                    "✅" if cr["pass"] else "❌"))
        L += ["", t("tt_extra", h=_price(tt["high_52w"], lang), fh=pct(tt["pct_from_high"], 1, lang, signed=True),
                    l=_price(tt["low_52w"], lang), al=pct(tt["pct_above_low"], 0, lang), s50=pct(tt["pct_above_sma50"], 1, lang, signed=True)), ""]
    L += ["## " + t("vcp_sec"), ""]
    if v.get("contractions"):
        L += ["| # | {} | {} | {} |".format(t("c_from"), t("c_to"), t("c_depth")), "|---|---|---|---|"]
        for n, x in enumerate(v["contractions"], 1):
            L.append("| {} | {} ({}) | {} ({}) | {} |".format(n, _price(x["high"], lang), x["high_date"], _price(x["low"], lang),
                                                            x["low_date"], pct(x["depth_pct"], 1, lang)))
        L.append("")
    if v.get("valid"):
        L.append(t("vcp_valid", n=len(v["contractions"]), b=v["base_len"], p=_price(v["pivot"], lang), l=_price(v["last_low"], lang),
                   hl=t("yes") if v["higher_lows"] else t("no"), q=num(v["quality"], 0, lang)))
    else:
        L.append(t("vcp_invalid", r="; ".join(t("v_" + x) for x in v.get("reasons", [])) or "—"))
    L += ["", t("dryup", d=num(v.get("dryup_ratio"), 2, lang)), ""]
    L += ["## " + t("exec_sec"), "", t("exec_txt", s=ex["state"], d=pct(ex["pct_vs_pivot"], 1, lang, signed=True),
                                       s50=pct(ex["pct_above_sma50"], 1, lang, signed=True), v=num(ex["volume_ratio"], 2, lang)), ""]
    L += ["## " + t("score_sec"), "", "| {} | {} | {} |".format(t("c_comp"), t("c_w"), t("c_score")), "|---|---|---|"]
    for k, w in eng.WEIGHTS.items():
        L.append("| {} | {}% | {} |".format(t("k_" + k), w, num(c["components"][k], 0, lang)))
    L += ["| **Σ** | 100% | **{}** → {} |".format(num(c["score"], 1, lang), t("b_" + c["band"])), ""]
    if c["band_capped"]:
        L += [t("capped", raw=t("b_" + c["band_raw"]), b=t("b_" + c["band"])), ""]
    L += ["## " + t("risk_sec"), ""]
    pl = _plan_lines(c, t, lang, cfg)
    if pl:
        L += [_sizer_note(r, t), ""] + pl
    else:
        L.append(t("no_plan", a=_price(rk.get("atr14"), lang)))
    L += ["", "## " + t("legend"), "", t("legend_txt", rs=cfg["min_rs"]), ""]
    return "\n".join(L + _footer(r, t, lang))


def render_history(r, lang):
    t = T(STRINGS, lang)
    h = r["history"]
    L = _header(r, t, t("title_history"))
    if not h["screen_dates"]:
        return "\n".join(L + [t("hist_none"), ""] + _footer(r, t, lang))
    L += ["## " + t("answer"), "", t("hist_answer", n=h["screen_dates"], s=h["step"], a=h["first_date"], b=h["last_date"]), ""]
    L += ["| {} |".format(" | ".join(t(k) for k in ("c_group", "c_h", "c_cnt", "c_hit", "c_mean", "c_med", "c_mex", "c_mdex", "c_beat"))),
          "|" + "---|" * 9]
    for g in ("flagged", "template", "universe"):
        for hz in h["horizons"]:
            s = h["stats"][g][str(hz)]
            if not s.get("n"):
                L.append("| {} | {} | 0 | — | — | — | — | — | — |".format(t("g_" + g), t("sess", h=hz)))
                continue
            L.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                t("g_" + g), t("sess", h=hz), s["n"], pct(s["hit_rate"], 0, lang), pct(s["mean"], 1, lang, signed=True),
                pct(s["median"], 1, lang, signed=True), _pp(s["mean_excess"], lang), _pp(s["median_excess"], lang),
                pct(s["beat_wig_rate"], 0, lang)))
    L += ["", t("hist_meta", t=h["distinct_flagged_tickers"], s=h["flagged_stopped_trading"]), ""]
    L += _context(r, t)
    L += ["## " + t("caveats"), "", t("caveats_txt", n=h["screen_dates"], w=pct(r.get("wig_return_window_pct"), 1, lang, signed=True)), ""]
    return "\n".join(L + _footer(r, t, lang))


def render(r, lang):
    return {"screen": render_screen, "ticker": render_ticker, "history": render_history}[r["mode"]](r, lang)


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except (gpw_archive.GPWFetchError, LookupError, ValueError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    md = render(result, args.lang)
    print(md)
    if not args.no_files:
        out = Path(args.output_dir).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        tag = {"screen": "", "ticker": "_" + (args.ticker or "").upper(), "history": "_history"}[result["mode"]]
        jp = out / "{}{}_{}.json".format(PREFIX, tag, stamp)
        mp = out / "{}{}_{}.md".format(PREFIX, tag, stamp)
        jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        mp.write_text(md, encoding="utf-8")
        print("JSON: {}\nMarkdown: {}".format(jp, mp), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
