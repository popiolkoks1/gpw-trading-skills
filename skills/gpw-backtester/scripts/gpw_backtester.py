#!/usr/bin/env python3
"""GPW Backtester - CLI.

Honest, cost- and tax-aware daily backtests of long-only rule-based strategies
on the Warsaw Stock Exchange (official GPW archive, adjusted, survivorship-free).

    python3 scripts/gpw_backtester.py list-strategies
    python3 scripts/gpw_backtester.py run --strategy trend_template --start 2019-06-01 --end 2026-11-13 \
        --broker mbank --capital 50000 --account regular --offline
    python3 scripts/gpw_backtester.py grid --strategy momentum_rotation --offline
    python3 scripts/gpw_backtester.py walkforward --strategy sma_cross --is-years 3 --oos-years 1 --offline
    python3 scripts/gpw_backtester.py run --rules my_rules.json --offline
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import brokers, gpw_archive, plcal, provenance, reports  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct, pln  # noqa: E402

import backtester_data as bd  # noqa: E402
import backtester_engine as be  # noqa: E402
import backtester_metrics as bm  # noqa: E402
import backtester_robustness as br  # noqa: E402
import backtester_strategies as bs  # noqa: E402

SKILL = "gpw-backtester"
VERSION = "1.0.0"
WARMUP_DAYS = 430        # ~295 sessions before --start: SMA200 + 21-session slope + 52-week windows
MISSING_WARN_SHARE = 0.02  # > 2% probable sessions missing from the cache -> loud warning
MAX_TRADES_MD = 30

STRINGS = {
    "title": {"pl": "Backtest strategii — GPW", "en": "Strategy backtest — GPW"},
    "stale": {"pl": "⚠️ UWAGA: dane kończą się {n} sesji przed dniem odniesienia.",
              "en": "⚠️ WARNING: data ends {n} session(s) before the reference date."},
    "missing": {"pl": "⚠️ Brakuje {n} sesji w pamięci podręcznej archiwum (np. {ex}) — wyniki mogą być zniekształcone. Uruchom bez --offline.",
                "en": "⚠️ {n} sessions are missing from the archive cache (e.g. {ex}) — results may be distorted. Run without --offline."},
    "clamped": {"pl": "ℹ️ Dane zaczynają się {d} — test liczony od tej daty, nie od {req}.",
                "en": "ℹ️ Data starts on {d} — the test runs from that date, not {req}."},
    "verdict": {"pl": "Werdykt", "en": "Verdict"},
    "v_NOT_EVALUABLE": {"pl": "NIE DO OCENY", "en": "NOT EVALUABLE"},
    "v_REJECT": {"pl": "ODRZUĆ", "en": "REJECT"},
    "v_FRAGILE": {"pl": "KRUCHA", "en": "FRAGILE"},
    "v_PROMISING": {"pl": "OBIECUJĄCA", "en": "PROMISING"},
    "v_ROBUST": {"pl": "ODPORNA", "en": "ROBUST"},
    "v_expl_NOT_EVALUABLE": {"pl": "Za mało transakcji (lub strategia-punkt odniesienia), by cokolwiek ocenić statystycznie.",
                             "en": "Too few trades (or a benchmark strategy) to evaluate anything statistically."},
    "v_expl_REJECT": {"pl": "Wynik po kosztach i podatku nie broni się — nie opieraj na tym decyzji.",
                      "en": "After costs and tax the result does not hold up — do not base decisions on it."},
    "v_expl_FRAGILE": {"pl": "Coś działa, ale wynik łatwo psują koszty, krótka historia lub dobór parametrów.",
                       "en": "Something works, but costs, a short history or parameter choice easily break it."},
    "v_expl_PROMISING": {"pl": "Przechodzi podstawowe testy, ale brakuje pełnej weryfikacji (walk-forward, PBO, t>3 lub przewaga nad WIG).",
                         "en": "Passes the basic tests but full verification is missing (walk-forward, PBO, t>3 or beating WIG)."},
    "v_expl_ROBUST": {"pl": "Przechodzi wszystkie testy odporności. To nadal historia, nie obietnica przyszłych wyników.",
                      "en": "Passes every robustness test. Still history, not a promise of future returns."},
    "reasons_reject": {"pl": "Powody odrzucenia", "en": "Reject reasons"},
    "reasons_block": {"pl": "Powody blokujące", "en": "Blocking reasons"},
    "reasons_limit": {"pl": "Czego brakuje do oceny „odporna”", "en": "Missing for ROBUST"},
    "drag_line": {"pl": "Koszty transakcyjne: **{cz} rocznie ≈ {c}% średniego kapitału**; podatek Belki: **{tz} rocznie ≈ {t}%**. CAGR netto {n}.",
                  "en": "Trading costs: **{cz} a year ≈ {c}% of average equity**; Belka tax: **{tz} a year ≈ {t}%**. Net CAGR {n}."},
    "conc_line": {"pl": "Zależność od pojedynczych transakcji: najlepsza dała {s1} całego zysku, 3 najlepsze {s3}; bez 3 najlepszych wynik łączny = {ex}.",
                  "en": "Dependence on single trades: the best one made {s1} of total P&L, the top 3 {s3}; without the top 3 the total = {ex}."},
    "summary": {"pl": "Wyniki (po kosztach{tax})", "en": "Results (after costs{tax})"},
    "and_tax": {"pl": " i podatku", "en": " and tax"},
    "col_metric": {"pl": "Miara", "en": "Metric"},
    "col_strategy": {"pl": "Strategia", "en": "Strategy"},
    "col_wig": {"pl": "WIG (kup i trzymaj, brutto)", "en": "WIG (buy & hold, gross)"},
    "col_ew": {"pl": "Równe wagi (uniwersum, brutto)", "en": "Equal weight (universe, gross)"},
    "m_cagr": {"pl": "CAGR (średnioroczna stopa zwrotu)", "en": "CAGR"},
    "m_total": {"pl": "Zwrot łączny", "en": "Total return"},
    "m_vol": {"pl": "Zmienność roczna", "en": "Annual volatility"},
    "m_sharpe": {"pl": "Sharpe", "en": "Sharpe"},
    "m_sortino": {"pl": "Sortino", "en": "Sortino"},
    "m_dd": {"pl": "Maks. obsunięcie", "en": "Max drawdown"},
    "m_mar": {"pl": "MAR (CAGR / obsunięcie)", "en": "MAR (CAGR / drawdown)"},
    "m_uw": {"pl": "Najdłużej pod wodą (sesje)", "en": "Longest under water (sessions)"},
    "m_exposure": {"pl": "Średnie zaangażowanie", "en": "Average exposure"},
    "m_final": {"pl": "Kapitał końcowy", "en": "Final equity"},
    "trades_h": {"pl": "Transakcje", "en": "Trades"},
    "t_n": {"pl": "Liczba transakcji", "en": "Trades"},
    "t_win": {"pl": "Skuteczność", "en": "Win rate"},
    "t_avgw": {"pl": "Średni zysk / strata", "en": "Average win / loss"},
    "t_exp": {"pl": "Wartość oczekiwana na transakcję", "en": "Expectancy per trade"},
    "t_pf": {"pl": "Profit factor", "en": "Profit factor"},
    "t_hold": {"pl": "Średni czas trzymania (sesje)", "en": "Average holding (sessions)"},
    "t_streak": {"pl": "Najdłuższa seria strat", "en": "Longest losing streak"},
    "t_reasons": {"pl": "Powody wyjść", "en": "Exit reasons"},
    "costs_h": {"pl": "Koszty i podatek", "en": "Costs and tax"},
    "c_comm": {"pl": "Prowizje", "en": "Commissions"},
    "c_slip": {"pl": "Poślizg (spread + wpływ na cenę)", "en": "Slippage (spread + impact)"},
    "c_minfee": {"pl": "Zlecenia z prowizją minimalną", "en": "Orders paying the minimum fee"},
    "c_turn": {"pl": "Obrót roczny (× kapitał)", "en": "Annual turnover (× equity)"},
    "c_cdrag": {"pl": "Koszty rocznie / średni kapitał", "en": "Yearly costs / average equity"},
    "c_tax": {"pl": "Podatek Belki zapłacony / należny", "en": "Belka tax paid / due"},
    "c_tdrag": {"pl": "Podatek rocznie / średni kapitał", "en": "Yearly tax / average equity"},
    "c_carry": {"pl": "Strata do odliczenia na koniec", "en": "Loss carried forward at the end"},
    "c_liq": {"pl": "Wejścia przycięte limitem płynności", "en": "Entries cut by the liquidity cap"},
    "c_noq": {"pl": "Wejścia/wyjścia bez notowania (zawieszenie, brak obrotu)", "en": "Entries/exits with no quote (suspension, no trade)"},
    "c_gap": {"pl": "Wyjścia z luką poniżej stopu", "en": "Gap-through stop exits"},
    "robust_h": {"pl": "Testy odporności", "en": "Robustness tests"},
    "r_stress": {"pl": "Koszty poślizgu ×2", "en": "Slippage cost ×2"},
    "r_boot": {"pl": "Wartość oczekiwana transakcji, 95% przedział (bootstrap)", "en": "Trade expectancy, 95% CI (bootstrap)"},
    "r_dsr": {"pl": "Deflated Sharpe Ratio (DSR)", "en": "Deflated Sharpe Ratio (DSR)"},
    "r_dsr_expl": {"pl": "Prawdopodobieństwo, że prawdziwy Sharpe > 0 po uwzględnieniu {n} przetestowanych wariantów, grubych ogonów i długości próby. Poniżej 95% wynik może być dziełem przypadku.",
                   "en": "Probability that the true Sharpe is > 0 after accounting for {n} tested variants, fat tails and sample length. Below 95% the result may be luck."},
    "r_trials_note": {"pl": "Przyjęto {n} wariant(ów). Jeśli testowałeś więcej wersji tej strategii, podaj --trials N — DSR spadnie.",
                      "en": "Assumed {n} variant(s). If you tried more versions of this strategy, pass --trials N — DSR will drop."},
    "r_pbo": {"pl": "PBO (prawdopodobieństwo przeuczenia, CSCV)", "en": "PBO (probability of backtest overfitting, CSCV)"},
    "r_pbo_expl": {"pl": "Jak często wariant najlepszy „w próbie” wypada gorzej niż mediana poza próbą. 0 = świetnie, > 0,5 = dobór parametrów to rzut monetą.",
                   "en": "How often the in-sample best variant ends below the median out of sample. 0 = great, > 0.5 = parameter choice is a coin flip."},
    "r_wf": {"pl": "Walk-forward ({m}): Sharpe OOS / IS", "en": "Walk-forward ({m}): Sharpe OOS / IS"},
    "r_plateau": {"pl": "Plateau parametrów (mediana sąsiadów / najlepszy)", "en": "Parameter plateau (neighbour median / best)"},
    "r_t": {"pl": "Statystyka t średniego zwrotu dziennego (próg Harvey-Liu-Zhu: 3)", "en": "t-stat of mean daily return (Harvey-Liu-Zhu bar: 3)"},
    "na": {"pl": "brak", "en": "n/a"},
    "not_run": {"pl": "nie uruchomiono (użyj `grid` lub `walkforward`)", "en": "not run (use `grid` or `walkforward`)"},
    "wf_h": {"pl": "Walk-forward — okna", "en": "Walk-forward — folds"},
    "col_is": {"pl": "Próba (IS)", "en": "In-sample"},
    "col_oos": {"pl": "Poza próbą (OOS)", "en": "Out-of-sample"},
    "col_choice": {"pl": "Wybrane parametry", "en": "Chosen parameters"},
    "grid_h": {"pl": "Siatka parametrów ({n} wariantów)", "en": "Parameter grid ({n} variants)"},
    "year_h": {"pl": "Wyniki roczne", "en": "Results by year"},
    "col_year": {"pl": "Rok", "en": "Year"},
    "col_ret": {"pl": "Strategia", "en": "Strategy"},
    "col_tr": {"pl": "Transakcje", "en": "Trades"},
    "col_tax": {"pl": "Podatek", "en": "Tax"},
    "regime_h": {"pl": "Wyniki wg stanu rynku", "en": "Results by market regime"},
    "regime_src_proxy": {"pl": "Źródło: przybliżenie — WIG powyżej/poniżej SMA200 (to NIE jest gpw-market-regime).",
                         "en": "Source: proxy — WIG above/below its SMA200 (NOT gpw-market-regime)."},
    "regime_src_report": {"pl": "Źródło: historia z raportu gpw-market-regime ({f}) dla {n} sesji, reszta: przybliżenie SMA200.",
                          "en": "Source: gpw-market-regime report history ({f}) for {n} sessions, the rest: SMA200 proxy."},
    "col_state": {"pl": "Stan", "en": "State"},
    "col_sessions": {"pl": "Sesje", "en": "Sessions"},
    "col_sann": {"pl": "Strategia (rocznie)", "en": "Strategy (annualised)"},
    "col_wann": {"pl": "WIG (rocznie)", "en": "WIG (annualised)"},
    "col_exp": {"pl": "Zaangażowanie", "en": "Exposure"},
    "trades_list_h": {"pl": "Ostatnie transakcje ({n} z {t}; pełna lista w JSON)", "en": "Latest trades ({n} of {t}; full list in JSON)"},
    "col_name": {"pl": "Walor", "en": "Ticker"},
    "col_in": {"pl": "Wejście", "en": "Entry"},
    "col_out": {"pl": "Wyjście", "en": "Exit"},
    "col_pnl": {"pl": "Wynik", "en": "P&L"},
    "col_r": {"pl": "R", "en": "R"},
    "col_why": {"pl": "Powód", "en": "Reason"},
    "strategy_h": {"pl": "Strategia i założenia", "en": "Strategy and assumptions"},
    "assumptions": {
        "pl": ["Sygnał na zamknięciu sesji t, realizacja na otwarciu następnej sesji z notowaniem (otwarcie skorygowane = otwarcie × kurs skorygowany / kurs zamknięcia).",
               "Stop sprawdzany na minimum sesji; otwarcie poniżej stopu = sprzedaż po otwarciu (luka). Wejście i stop tego samego dnia liczone na niekorzyść.",
               "Brak notowania danego dnia (zawieszenie, brak obrotu, próg statyczny) = brak transakcji.",
               "Prowizja od każdego zlecenia z minimalną opłatą; poślizg wg płynności + model pierwiastkowy wpływu na cenę; zlecenie ≤ {adv}% mediany obrotu z 20 sesji; całe akcje.",
               "Uniwersum bez błędu przeżywalności: każdego dnia wszystkie spółki z archiwum GPW z tego dnia, także później wycofane. Bez historycznego składu indeksów.",
               "Gotówka oprocentowana {cr}% rocznie. Konto: {acct}."],
        "en": ["Signal at the close of session t, fill at the open of the next session with a quote (adjusted open = open × adjusted close / close).",
               "Stops checked on the session low; an open below the stop fills at the open (gap). Same-day entry and stop resolved against you.",
               "No quote on a day (suspension, no trade, static threshold) = no fill.",
               "Commission on every order with the minimum fee; slippage by liquidity bucket + square-root impact; order ≤ {adv}% of 20-session median turnover; whole shares.",
               "Survivorship-free universe: every share in that day's GPW archive, including later delistings. No historical index membership.",
               "Idle cash earns {cr}% a year. Account: {acct}."],
    },
    "acct_regular": {"pl": "zwykły rachunek — podatek Belki 19% od rocznego wyniku, strata przenoszona 5 lat ({rule})",
                     "en": "regular account — 19% Belka tax on the yearly result, losses carried 5 years ({rule})"},
    "acct_ike": {"pl": "IKE — bez podatku od zysków (zakładamy, że kapitał już jest na IKE; limity wpłat obowiązują)",
                 "en": "IKE — no capital-gains tax (assumes the capital is already inside the IKE; contribution limits apply)"},
    "rule_pl_2019": {"pl": "jednorazowo do 5 mln zł albo do 50% straty rocznie, art. 9 ust. 3 ustawy o PIT",
                     "en": "once up to PLN 5m or up to 50% of the loss a year, art. 9(3) PIT Act"},
    "rule_50pct": {"pl": "maks. 50% straty rocznie — wariant ostrożny",
                   "en": "at most 50% of the loss a year — conservative variant"},
    "sources": {"pl": "Źródła", "en": "Sources"},
    "params": {"pl": "Parametry", "en": "Parameters"},
    "period": {"pl": "Okres", "en": "Period"},
    "runtime": {"pl": "Czas obliczeń", "en": "Runtime"},
    "never": {"pl": "To jest test historyczny. Nie mówi, ile strategia zarobi w przyszłości.",
              "en": "This is a historical test. It does not say what the strategy will earn in the future."},
}

REASONS = {
    "net_cagr_not_positive": {"pl": "CAGR po kosztach i podatku ≤ 0", "en": "CAGR after costs and tax ≤ 0"},
    "max_dd_ge_50": {"pl": "obsunięcie kapitału ≥ 50%", "en": "drawdown ≥ 50%"},
    "expectancy_ci_below_zero": {"pl": "cały 95% przedział wartości oczekiwanej transakcji < 0", "en": "the whole 95% CI of trade expectancy < 0"},
    "pbo_gt_0_5": {"pl": "PBO > 0,5 — wybór parametrów gorszy niż rzut monetą", "en": "PBO > 0.5 — parameter choice worse than a coin flip"},
    "dsr_lt_0_5": {"pl": "DSR < 0,5 — wynik bardziej prawdopodobnie jest szczęściem niż przewagą", "en": "DSR < 0.5 — more likely luck than edge"},
    "period_lt_5y": {"pl": "okres testu krótszy niż 5 lat", "en": "test period shorter than 5 years"},
    "no_cost_stress": {"pl": "brak testu podwojonych kosztów", "en": "no 2x cost stress test"},
    "cost_stress_kills_edge": {"pl": "przy poślizgu ×2 CAGR spada do ≤ 0", "en": "with 2x slippage CAGR falls to ≤ 0"},
    "too_good_to_be_true": {"pl": "wynik „zbyt dobry” (skuteczność > 90% przy obsunięciu < 5% lub Sharpe > 3) — szukaj błędu", "en": "too good to be true (win rate > 90% with DD < 5% or Sharpe > 3) — look for a bug"},
    "expectancy_ci_includes_zero": {"pl": "95% przedział wartości oczekiwanej transakcji obejmuje zero", "en": "95% CI of trade expectancy includes zero"},
    "dsr_lt_0_95": {"pl": "DSR < 0,95 — przewaga nieistotna po uwzględnieniu liczby prób", "en": "DSR < 0.95 — edge not significant after the number of trials"},
    "no_parameter_plateau": {"pl": "brak plateau — sąsiednie parametry wyraźnie gorsze (pojedynczy szczyt)", "en": "no plateau — neighbouring parameters much worse (lone peak)"},
    "oos_is_ratio_lt_0_5": {"pl": "walk-forward: Sharpe poza próbą < 50% Sharpe w próbie", "en": "walk-forward: OOS Sharpe < 50% of IS Sharpe"},
    "no_walk_forward": {"pl": "nie wykonano walk-forward", "en": "walk-forward not run"},
    "no_pbo": {"pl": "nie policzono PBO (wymaga siatki parametrów)", "en": "PBO not computed (needs a parameter grid)"},
    "pbo_gt_0_25": {"pl": "PBO > 0,25", "en": "PBO > 0.25"},
    "t_stat_lt_3": {"pl": "statystyka t < 3 (Harvey-Liu-Zhu)", "en": "t-stat < 3 (Harvey-Liu-Zhu)"},
    "edge_depends_on_few_trades": {"pl": "bez 3 najlepszych transakcji wynik łączny ≤ 0 — przewaga zależy od kilku strzałów", "en": "without the 3 best trades total P&L ≤ 0 — the edge rests on a few hits"},
    "does_not_beat_wig": {"pl": "CAGR netto nie przewyższa WIG (kup i trzymaj)", "en": "net CAGR does not beat WIG buy & hold"},
    "benchmark_strategy": {"pl": "strategia-punkt odniesienia (kup i trzymaj / DCA) — porównuj, nie oceniaj", "en": "benchmark strategy (buy & hold / DCA) — compare, do not grade"},
}

EXIT_REASONS = {
    "signal": {"pl": "sygnał", "en": "signal"}, "stop": {"pl": "stop", "en": "stop"},
    "gap_stop": {"pl": "luka pod stop", "en": "gap stop"}, "rebalance": {"pl": "rebalans", "en": "rebalance"},
    "max_hold": {"pl": "limit czasu", "en": "time limit"}, "end_of_test": {"pl": "koniec testu", "en": "end of test"},
}

SOURCES = [
    "GPW — Archiwum notowań (akcje, indeksy): https://www.gpw.pl/archiwum-notowan",
    "Ustawa o PIT art. 30b, art. 9 ust. 3; Ordynacja podatkowa art. 63 — https://isap.sejm.gov.pl",
    "Bailey & López de Prado (2014), The Deflated Sharpe Ratio — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551",
    "Bailey, Borwein, López de Prado & Zhu (2017), The Probability of Backtest Overfitting — J. Computational Finance 20(4)",
]


class InputError(Exception):
    pass


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
def _date(s: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError("dates must be YYYY-MM-DD, got {!r}".format(s))


def _pos(s: str) -> float:
    try:
        v = float(s)
    except ValueError:
        raise argparse.ArgumentTypeError("not a number: {!r}".format(s))
    if v <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return v


def _nonneg(s: str) -> float:
    try:
        v = float(s)
    except ValueError:
        raise argparse.ArgumentTypeError("not a number: {!r}".format(s))
    if v < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return v


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GPW long-only strategy backtester (PL/EN)")
    sub = p.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lang", choices=["pl", "en"], default="pl")
    common.add_argument("--output-dir", default=".")
    common.add_argument("--no-files", action="store_true", help="print only, write no JSON/MD")
    common.add_argument("--json", action="store_true", help="print the JSON instead of Markdown")
    common.add_argument("--cache-dir", default=None)
    common.add_argument("--offline", action="store_true", help="use cached archive only")
    common.add_argument("--strategy", default="trend_template")
    common.add_argument("--rules", default=None, help="JSON file with custom entry/exit rules")
    common.add_argument("--param", action="append", default=[], metavar="KEY=VALUE", help="strategy parameter (repeatable)")
    common.add_argument("--start", type=_date, default=dt.date(2019, 6, 3))
    common.add_argument("--end", type=_date, default=None, help="default: last cached/final session")
    common.add_argument("--capital", type=_pos, default=50_000.0)
    common.add_argument("--broker", default="mbank", choices=sorted(brokers.BROKERS))
    common.add_argument("--commission-pct", type=_nonneg, default=None)
    common.add_argument("--commission-min", type=_nonneg, default=None)
    common.add_argument("--account", choices=["regular", "ike"], default="regular")
    common.add_argument("--loss-rule", choices=["pl_2019", "50pct"], default="pl_2019")
    common.add_argument("--sizing", choices=["equal", "risk"], default="equal")
    common.add_argument("--risk-pct", type=_pos, default=1.0)
    common.add_argument("--max-position-pct", type=_pos, default=25.0)
    common.add_argument("--max-adv-pct", type=_pos, default=10.0)
    common.add_argument("--impact-coef", type=_nonneg, default=be.IMPACT_COEF)
    common.add_argument("--cost-mult", type=_pos, default=1.0, help="scale slippage + impact (stress)")
    common.add_argument("--cash-rate", type=_nonneg, default=0.0, help="yearly %% earned on idle cash")
    common.add_argument("--trials", type=int, default=1, help="variants you have tried (for the Deflated Sharpe)")
    common.add_argument("--regime-json", default=None, help="gpw-market-regime JSON (else newest in --input-dir)")
    common.add_argument("--input-dir", default=".")
    common.add_argument("--max-trades-md", type=int, default=MAX_TRADES_MD)
    for name, hlp in (("run", "single backtest"), ("grid", "parameter grid + plateau + PBO + walk-forward"),
                      ("walkforward", "walk-forward analysis (uses the grid)")):
        sp = sub.add_parser(name, parents=[common], help=hlp)
        if name != "run":
            sp.add_argument("--grid", default=None, help='e.g. "stop_pct=6,8,10;exit_sma=20,50" (default: strategy grid)')
            sp.add_argument("--max-trials", type=int, default=36)
            sp.add_argument("--is-years", type=_pos, default=3.0)
            sp.add_argument("--oos-years", type=_pos, default=1.0)
    ls = sub.add_parser("list-strategies", help="list built-in strategies and the rule format")
    ls.add_argument("--lang", choices=["pl", "en"], default="pl")
    ls.add_argument("--json", action="store_true")
    return p


def parse_kv(items: List[str]) -> Dict[str, str]:
    out = {}
    for it in items:
        if "=" not in it:
            raise InputError("--param must be KEY=VALUE, got {!r}".format(it))
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def parse_grid(text: Optional[str], default: Dict) -> Dict[str, List]:
    if not text:
        return {k: list(v) for k, v in default.items()}
    grid = {}
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise InputError("grid entries must be KEY=v1,v2 separated by ';' (got {!r})".format(part))
        k, vs = part.split("=", 1)
        vals = [v.strip() for v in vs.split(",") if v.strip()]
        if not vals:
            raise InputError("grid key {!r} has no values".format(k))
        grid[k.strip()] = vals
    return grid


def load_rules(path: str) -> Dict:
    p = Path(path).expanduser()
    if not p.is_file():
        raise InputError("rules file not found: {}".format(p))
    try:
        return bs.parse_rules(p.read_text(encoding="utf-8"))
    except bs.RuleError as exc:
        raise InputError("invalid rules ({}): {}".format(p.name, exc))


def make_strategy(args, params: Dict, rules: Optional[Dict]):
    try:
        return bs.make(args.strategy, params, rules)
    except ValueError as exc:
        raise InputError(str(exc))


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def load_market(args):
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else None
    end = args.end or plcal.today_warsaw()
    if args.start >= end:
        raise InputError("--start must be before --end")
    warm = args.start - dt.timedelta(days=WARMUP_DAYS)

    def prog(n, day):
        print("  ... {} sessions checked (at {})".format(n, day), file=sys.stderr)
    print("Loading GPW archive {}..{} (first online run downloads years of sessions, ~1.5 s each)...".format(
        warm, end), file=sys.stderr)
    md, miss_s, miss_i = bd.load_market(warm, end, cache_dir, args.offline, prog)
    if md.meta["sessions_loaded"] < 60:
        raise gpw_archive.GPWFetchError("Only {} share sessions available between {} and {}.".format(
            md.meta["sessions_loaded"], warm, end))
    in_window = lambda ds: [d for d in ds if args.start.isoformat() <= d <= end.isoformat()]  # noqa: E731
    md.meta.update({"missing_shares": in_window(miss_s), "missing_indices": in_window(miss_i),
                    "first_session": md.calendar[0], "last_session": md.calendar[-1]})
    return md


def sim_config(args) -> Dict:
    b = brokers.resolve(args.broker, args.commission_pct, args.commission_min)
    return {"capital": args.capital, "commission_pct": b["pct"], "commission_min": b["min_fee"],
            "account": args.account, "sizing": args.sizing, "risk_pct": args.risk_pct,
            "max_position_pct": args.max_position_pct, "max_adv_pct": args.max_adv_pct,
            "impact_coef": args.impact_coef, "cost_mult": args.cost_mult, "cash_rate_pct": args.cash_rate,
            "loss_rule": args.loss_rule, "broker": b["key"], "broker_label": b["label"],
            "broker_overridden": b["overridden"]}


def _r(x, d=2):
    if x is None:
        return None
    if isinstance(x, float):
        return round(x, d)
    return x


def _clean(o, d=4):
    if isinstance(o, float):
        return round(o, d)
    if isinstance(o, dict):
        return {k: _clean(v, d) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v, d) for v in o]
    return o


def evaluate(md, strategy, start: str, end: str, cfg: Dict, n_trials: int, trial_srs=None,
             regime_doc=None) -> Dict:
    """Main simulation + drag decomposition + stress + statistics."""
    try:
        main = be.simulate(md, strategy, start, end, cfg, prepared=True)
        stress = be.simulate(md, strategy, start, end, dict(cfg, cost_mult=cfg["cost_mult"] * br.STRESS_MULT), prepared=True)
    except ValueError as exc:
        raise InputError(str(exc))
    days = main["dates"]
    m = bm.perf(days, main["returns"], cfg["cash_rate_pct"])
    m_s = bm.perf(days, stress["returns"], cfg["cash_rate_pct"])
    ts = bm.trade_stats(main["trades"])
    wig = bm.wig_returns(md, days)
    p = strategy.params
    ew = bm.equal_weight_returns(md, days, p["min_price"], p["min_turnover_k"])
    wm = bm.perf(days, wig) if md.wig is not None else None
    em = bm.perf(days, ew)
    exposure = sum(main["exposure"]) / len(main["exposure"]) * 100.0
    st = main["stats"]
    avg_eq = sum(main["equity"]) / len(main["equity"])
    turnover = st["traded_value"] / 2.0 / avg_eq / m["years"] if avg_eq > 0 else None
    tax_total = sum(main["tax_paid"].values())
    # Drags as yearly PLN cost / average equity. Path-independent: a separate cost-free
    # simulation would take different trades (cash and slot timing), so its CAGR gap is noise.
    cost_pln = st["commissions"] + st["slippage"]
    cost_drag = cost_pln / avg_eq / m["years"] * 100.0 if avg_eq > 0 else None
    tax_drag = tax_total / avg_eq / m["years"] * 100.0 if avg_eq > 0 else None
    conc = bm.concentration(main["trades"])
    rets_trade = [t["ret_pct"] for t in main["trades"]]
    rs = [t["r_multiple"] for t in main["trades"] if t.get("r_multiple") is not None]
    boot = br.bootstrap_mean_ci(rets_trade)
    boot_r = br.bootstrap_mean_ci(rs) if len(rs) >= 5 else None
    dsr = br.deflated_sharpe(main["returns"], max(1, n_trials), trial_srs)
    labels = bm.proxy_regime(md, days)
    regime_src = {"source": "proxy_wig_sma200", "report_sessions": 0, "file": None}
    if regime_doc:
        rep = bm.regime_from_report(regime_doc["data"], days)
        if rep:
            labels.update(rep)
            regime_src = {"source": "gpw-market-regime+proxy", "report_sessions": len(rep), "file": regime_doc["file"],
                          "as_of": regime_doc["as_of"].isoformat() if regime_doc.get("as_of") else None}
    regimes = bm.by_regime(days, main["returns"], main["exposure"], wig, labels)
    metrics = {
        "cagr": m["cagr"], "max_dd": m["max_dd"], "sharpe": m["sharpe"], "trades": ts["trades"],
        "win_rate": ts["win_rate"], "expectancy_r": ts["expectancy_r"], "exposure": exposure,
        "sortino": m["sortino"], "mar": m["mar"], "volatility": m["volatility"], "total_return": m["total_return"],
        "max_dd_peak": m["max_dd_peak"], "max_dd_trough": m["max_dd_trough"],
        "longest_underwater_sessions": m["longest_underwater_sessions"],
        "t_stat": m["t_stat"], "years": m["years"], "final_equity": main["final_equity"],
        "contributions": main["contributions"], "start_capital": cfg["capital"],
        "expectancy_pln": ts["expectancy_pln"], "expectancy_pct": boot["mean"] if boot else None,
        "profit_factor": ts["profit_factor"], "avg_win_pct": ts["avg_win_pct"], "avg_loss_pct": ts["avg_loss_pct"],
        "avg_win_pln": ts["avg_win_pln"], "avg_loss_pln": ts["avg_loss_pln"], "avg_sessions": ts["avg_sessions"],
        "max_consecutive_losses": ts["max_consecutive_losses"], "exit_reasons": ts["exit_reasons"],
        "trades_with_r": ts["trades_with_r"], "concentration": conc,
        "units": "cagr, max_dd, win_rate, exposure, volatility, total_return, expectancy_pct in percent; "
                 "sharpe/sortino/mar/profit_factor/expectancy_r unitless; PLN for *_pln and equity",
    }
    costs = {"commissions_pln": st["commissions"], "slippage_pln": st["slippage"], "orders": st["orders"],
             "min_fee_orders": st["min_fee_orders"],
             "min_fee_share_pct": st["min_fee_orders"] / st["orders"] * 100.0 if st["orders"] else None,
             "turnover_x_per_year": turnover, "cost_drag_pp": cost_drag, "costs_total_pln": cost_pln,
             "cost_per_year_pln": cost_pln / m["years"], "average_equity_pln": avg_eq,
             "drag_method": "yearly PLN cost / average equity (path-independent)",
             "entries_capped_liquidity": st["entries_capped_liquidity"],
             "entries_skipped_no_quote": st["entries_skipped_no_quote"],
             "entries_skipped_cash": st["entries_skipped_cash"],
             "exits_delayed_no_quote": st["exits_delayed_no_quote"], "gap_stop_exits": st["gap_stop_exits"],
             "interest_pln": st["interest"]}
    tax = {"account": cfg["account"], "loss_rule": cfg["loss_rule"], "paid_or_due_pln": tax_total,
           "by_year": main["tax_years"], "tax_drag_pp": tax_drag,
           "tax_per_year_pln": tax_total / m["years"],
           "carry_forward_at_end": main["carry_forward"]}
    stress_out = {"cost_mult": cfg["cost_mult"] * br.STRESS_MULT, "cagr": m_s["cagr"], "sharpe": m_s["sharpe"],
                  "max_dd": m_s["max_dd"], "cagr_change_pp": m_s["cagr"] - m["cagr"]}
    return {"main": main, "metrics": metrics, "costs": costs, "tax": tax, "stress": stress_out, "boot": boot,
            "boot_r": boot_r, "dsr": dsr, "benchmarks": {
                "wig": {"cagr": wm["cagr"], "max_dd": wm["max_dd"], "sharpe": wm["sharpe"], "volatility": wm["volatility"],
                        "total_return": wm["total_return"], "note": "WIG total-return index, no costs or tax"} if wm else None,
                "equal_weight": {"cagr": em["cagr"], "max_dd": em["max_dd"], "sharpe": em["sharpe"],
                                 "volatility": em["volatility"], "total_return": em["total_return"],
                                 "note": "daily-rebalanced equal weight of eligible shares, gross"}},
            "by_year": bm.by_year(days, main["returns"], wig, ew, main["trades"], main["tax_years"]),
            "by_regime": regimes, "regime_source": regime_src,
            "equity_curve": bm.monthly_curve(days, main["equity"], md), "wig_cagr": wm["cagr"] if wm else None,
            "days": days}


def grid_combos(grid: Dict[str, List], base_params: Dict) -> List[Dict]:
    keys = list(grid)
    out = []
    for vals in itertools.product(*[grid[k] for k in keys]):
        prm = dict(base_params)
        prm.update(dict(zip(keys, vals)))
        out.append(prm)
    return out


def run(args) -> Dict:
    t0 = time.time()
    rules = load_rules(args.rules) if args.rules else None
    params = parse_kv(args.param)
    proto = make_strategy(args, params, rules)  # validates before loading data
    if args.trials < 1:
        raise InputError("--trials must be >= 1")
    if args.command != "run" and proto.kind == "dca":
        raise InputError("dca_buy_hold has no parameter grid; use `run`")
    md = load_market(args)
    if not md.calendar:
        raise gpw_archive.GPWFetchError("no sessions loaded")
    days = md.window(args.start.isoformat(), (args.end or plcal.today_warsaw()).isoformat())
    if len(days) < 60:
        raise InputError("fewer than 60 sessions in the requested window ({} available)".format(len(days)))
    start, end = days[0], days[-1]
    cfg = sim_config(args)
    regime_doc = None
    try:
        regime_doc = reports.find_report("gpw-market-regime", args.input_dir, explicit_path=args.regime_json,
                                         lang=args.lang)
    except reports.ReportError as exc:
        raise InputError(str(exc))

    grid_out = wf_out = pbo = plat = None
    trial_srs = None
    n_trials = args.trials
    strategy = proto
    if args.command in ("grid", "walkforward"):
        if rules is not None and not args.grid:
            raise InputError("custom rules have no default grid; pass --grid KEY=v1,v2")
        grid = parse_grid(args.grid, proto.GRID)
        if not grid:
            raise InputError("strategy {} has no parameter grid".format(proto.name))
        combos = grid_combos(grid, params)
        if len(combos) > args.max_trials:
            raise InputError("grid has {} variants > --max-trials {}".format(len(combos), args.max_trials))
        trials, scores, rows = {}, {}, []
        strategies = {}
        for prm in combos:
            try:
                s = bs.make(args.strategy, prm, rules)
            except ValueError as exc:
                raise InputError("grid point {}: {}".format({k: prm[k] for k in grid}, exc))
            s.prepare(md)
            try:
                r = be.simulate(md, s, start, end, cfg, prepared=True)
            except ValueError as exc:
                raise InputError(str(exc))
            key = json.dumps({k: s.params[k] for k in grid}, sort_keys=True)
            trials[key] = r["returns"]
            pm = bm.perf(r["dates"], r["returns"])
            tup = tuple(s.params[k] for k in grid)
            scores[tup] = pm["sharpe"]
            strategies[key] = prm
            rows.append({"params": {k: s.params[k] for k in grid}, "cagr": pm["cagr"], "sharpe": pm["sharpe"],
                         "max_dd": pm["max_dd"], "trades": len(r["trades"])})
            print("  grid {}/{}: {} Sharpe {}".format(len(rows), len(combos), key,
                                                      None if pm["sharpe"] is None else round(pm["sharpe"], 2)), file=sys.stderr)
        typed_grid = {k: sorted({tup[n] for tup in scores}) for n, k in enumerate(grid)}
        plat = br.plateau(typed_grid, scores)
        pbo = br.pbo_cscv(list(trials.values()))
        is_len, oos_len = int(args.is_years * 252), int(args.oos_years * 252)
        try:
            wf_out = {"anchored": br.walk_forward(days, trials, is_len, oos_len, True),
                      "rolling": br.walk_forward(days, trials, is_len, oos_len, False)}
        except ValueError as exc:
            raise InputError(str(exc))
        n_trials = max(len(combos), args.trials)
        trial_srs = [bm.sharpe(v, annualize=False) or 0.0 for v in trials.values()]
        best_key = max(trials, key=lambda k: ((bm.sharpe(trials[k]) or -1e9), k))
        strategy = bs.make(args.strategy, strategies[best_key], rules)
        strategy.prepare(md)
        grid_out = {"grid": {k: [_r(v, 4) for v in vs] for k, vs in typed_grid.items()}, "variants": len(combos),
                    "selected": json.loads(best_key), "selection": "best full-period Sharpe (deflated below)",
                    "results": sorted(rows, key=lambda x: -(x["sharpe"] or -1e9))}
    else:
        strategy.prepare(md)

    ev = evaluate(md, strategy, start, end, cfg, n_trials, trial_srs, regime_doc)
    wf_main = None
    if wf_out:
        wf_main = wf_out["anchored"] if wf_out["anchored"].get("available") else wf_out["rolling"]
    v = br.verdict(metrics=ev["metrics"], trades={"trades": ev["metrics"]["trades"], "win_rate": ev["metrics"]["win_rate"]},
                   years=ev["metrics"]["years"], stress=ev["stress"], boot=ev["boot"], dsr=ev["dsr"],
                   pbo=pbo, wf=wf_main, plat=plat, wig_cagr=ev["wig_cagr"], strategy_kind=strategy.kind)
    robustness = {
        "verdict": v["verdict"], "reject_reasons": v["reject_reasons"], "blocking_reasons": v["blocking_reasons"],
        "limiting_reasons": v["limiting_reasons"], "notes": v["notes"],
        "walk_forward": wf_out or {"available": False, "reason": "run `walkforward` or `grid`"},
        "oos_is_ratio": wf_main.get("oos_is_ratio") if wf_main else None,
        "deflated_sharpe": ev["dsr"].get("dsr"), "dsr_detail": ev["dsr"], "trials": n_trials,
        "pbo": pbo or {"available": False, "reason": "needs a parameter grid"},
        "plateau": plat or {"available": False, "reason": "needs a parameter grid"},
        "cost_stress": ev["stress"], "bootstrap_expectancy_pct": ev["boot"], "bootstrap_expectancy_r": ev["boot_r"],
        "t_stat": ev["metrics"]["t_stat"], "thresholds": {
            "min_trades": br.MIN_TRADES, "min_years": br.MIN_YEARS, "max_dd_reject_pct": br.MAX_DD_REJECT,
            "dsr_min": br.DSR_MIN, "pbo_max_robust": br.PBO_MAX_ROBUST, "pbo_reject": br.PBO_REJECT,
            "oos_is_min": br.OOS_IS_MIN, "plateau_min": br.PLATEAU_MIN, "t_stat_min": br.T_STAT_MIN},
    }
    meta = md.meta
    end_d = dt.date.fromisoformat(end)
    notes = ["Adjusted open estimated as raw_open * adj_close / raw_close (gpwlib.panel adjusts closes/highs/lows).",
             "Universe = every PLN share in each day's archive snapshot that passes the liquidity filter; "
             "index membership is not used, so point-in-time membership is not needed.",
             "Dividends are inside the adjusted prices; their tax is approximated by the capital-gains tax on the adjusted gain."]
    if meta["missing_shares"]:
        notes.append("{} probable share sessions missing from the cache in the window.".format(len(meta["missing_shares"])))
    prov = provenance.build(
        as_of=end_d, sources=SOURCES[:1], sessions_used=len(days),
        universe_size=sum(1 for b in md.stocks.values() if b.dates and b.dates[-1] >= start and b.dates[0] <= end),
        notes=notes, corporate_actions_adjusted=True, survivorship_free=True, point_in_time_membership=False,
        reference_date=args.end if args.end else None)
    main = ev["main"]
    out = {
        "skill": SKILL, "version": VERSION, "lang": args.lang, "command": args.command, "as_of": end,
        "strategy": {"name": strategy.name, "kind": strategy.kind, "params": strategy.params,
                     "description": strategy.describe(args.lang), "rules": rules},
        "period": {"start": start, "end": end, "requested_start": args.start.isoformat(),
                   "sessions": len(days), "years": ev["metrics"]["years"]},
        "settings": {k: v for k, v in cfg.items()},
        "metrics": ev["metrics"], "costs": ev["costs"], "tax": ev["tax"], "benchmarks": ev["benchmarks"],
        "robustness": robustness, "grid": grid_out, "by_year": ev["by_year"], "by_regime": ev["by_regime"],
        "regime_source": ev["regime_source"], "equity_curve": ev["equity_curve"], "trades": main["trades"],
        "warnings": {"missing_sessions": meta["missing_shares"][:20], "missing_sessions_count": len(meta["missing_shares"]),
                     "missing_index_sessions_count": len(meta["missing_indices"]),
                     "start_clamped": start > args.start.isoformat() and (dt.date.fromisoformat(start) - args.start).days > 7,
                     "regime_report_warning": regime_doc["warning"] if regime_doc else None,
                     "tariff_notes": brokers.tariff_notes(args.lang, cfg["broker_overridden"])},
        "runtime_s": round(time.time() - t0, 1),
        "data_provenance": prov,
    }
    return _clean(out)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def render_markdown(r: Dict, lang: str, max_trades: int = MAX_TRADES_MD) -> str:
    t = T(STRINGS, lang)
    m, c, tx, rb, bmk = r["metrics"], r["costs"], r["tax"], r["robustness"], r["benchmarks"]
    prov, w = r["data_provenance"], r["warnings"]
    P = lambda x, d=1, s=False: pct(x, d, lang, signed=s)  # noqa: E731
    Z = lambda x, d=0: pln(x, d, lang)  # noqa: E731
    N = lambda x, d=2: num(x, d, lang) if x is not None else "—"  # noqa: E731
    L = ["# {} — {}".format(t("title"), r["strategy"]["name"]), "",
         "**{}:** {} → {} ({} {}) · **as of** {}".format(t("period"), r["period"]["start"], r["period"]["end"],
                                                          N(r["period"]["years"], 1), "lat" if lang == "pl" else "years", r["as_of"]), ""]
    if prov["freshness"]["is_stale"]:
        L += [t("stale", n=prov["freshness"]["sessions_behind"]), ""]
    if w["missing_sessions_count"]:
        L += [t("missing", n=w["missing_sessions_count"], ex=", ".join(w["missing_sessions"][:3])), ""]
    if w["start_clamped"]:
        L += [t("clamped", d=r["period"]["start"], req=r["period"]["requested_start"]), ""]
    vv = rb["verdict"]
    L += ["## {}: **{}**".format(t("verdict"), t("v_" + vv)), "", t("v_expl_" + vv), ""]
    for key, title in (("reject_reasons", "reasons_reject"), ("blocking_reasons", "reasons_block"),
                       ("limiting_reasons", "reasons_limit")):
        if rb[key]:
            L += ["**{}:**".format(t(title))] + ["- " + REASONS[x][lang] for x in rb[key]] + [""]
    for x in rb["notes"]:
        L += ["- " + REASONS[x][lang], ""]
    L += [t("drag_line", cz=Z(c["cost_per_year_pln"]), c=N(c["cost_drag_pp"], 2), tz=Z(tx["tax_per_year_pln"]),
            t=N(tx["tax_drag_pp"], 2), n=P(m["cagr"])), ""]
    cc = m.get("concentration") or {}
    if cc.get("top1_share_pct") is not None and m["trades"] >= 5:
        L += [t("conc_line", s1=P(cc["top1_share_pct"], 0), s3=P(cc["top3_share_pct"], 0), ex=Z(cc["pnl_ex_top3_pln"])), ""]
    L += [
          "_" + t("never") + "_", ""]
    L += ["## " + t("summary", tax=t("and_tax") if tx["account"] == "regular" else ""), "",
          "| {} | {} | {} | {} |".format(t("col_metric"), t("col_strategy"), t("col_wig"), t("col_ew")), "|---|---|---|---|"]
    wg, ew = bmk.get("wig") or {}, bmk.get("equal_weight") or {}
    for key, lab, fmtf in (("cagr", "m_cagr", P), ("total_return", "m_total", P), ("volatility", "m_vol", P),
                           ("sharpe", "m_sharpe", N), ("max_dd", "m_dd", P)):
        L.append("| {} | **{}** | {} | {} |".format(t(lab), fmtf(m.get(key)), fmtf(wg.get(key)), fmtf(ew.get(key))))
    L += ["| {} | {} | | |".format(t("m_sortino"), N(m["sortino"])),
          "| {} | {} | | |".format(t("m_mar"), N(m["mar"])),
          "| {} | {} | | |".format(t("m_uw"), m["longest_underwater_sessions"]),
          "| {} | {} | | |".format(t("m_exposure"), P(m["exposure"], 0)),
          "| {} | {} | | |".format(t("m_final"), Z(m["final_equity"])), ""]
    L += ["## " + t("trades_h"), "", "| | |", "|---|---|",
          "| {} | {} |".format(t("t_n"), m["trades"]),
          "| {} | {} |".format(t("t_win"), P(m["win_rate"])),
          "| {} | {} / {} ({} / {}) |".format(t("t_avgw"), P(m["avg_win_pct"], 1, True), P(m["avg_loss_pct"], 1, True),
                                               Z(m["avg_win_pln"]), Z(m["avg_loss_pln"])),
          "| {} | {} · {} · {} R |".format(t("t_exp"), P(m["expectancy_pct"], 2, True), Z(m["expectancy_pln"]), N(m["expectancy_r"])),
          "| {} | {} |".format(t("t_pf"), N(m["profit_factor"])),
          "| {} | {} |".format(t("t_hold"), N(m["avg_sessions"], 0)),
          "| {} | {} |".format(t("t_streak"), m["max_consecutive_losses"]),
          "| {} | {} |".format(t("t_reasons"), ", ".join("{} {}".format(EXIT_REASONS.get(k, {}).get(lang, k), v)
                                                          for k, v in sorted(m["exit_reasons"].items(), key=lambda kv: -kv[1]))), ""]
    L += ["## " + t("costs_h"), "", "| | |", "|---|---|",
          "| {} | {} |".format(t("c_comm"), Z(c["commissions_pln"])),
          "| {} | {} |".format(t("c_slip"), Z(c["slippage_pln"])),
          "| {} | {} / {} ({}) |".format(t("c_minfee"), c["min_fee_orders"], c["orders"], P(c["min_fee_share_pct"], 0)),
          "| {} | {} |".format(t("c_turn"), N(c["turnover_x_per_year"], 1)),
          "| {} | {}% |".format(t("c_cdrag"), N(c["cost_drag_pp"])),
          "| {} | {} |".format(t("c_tax"), Z(tx["paid_or_due_pln"])),
          "| {} | {}% |".format(t("c_tdrag"), N(tx["tax_drag_pp"])),
          "| {} | {} |".format(t("c_carry"), Z(sum(x["remaining"] for x in tx["carry_forward_at_end"]))),
          "| {} | {} |".format(t("c_liq"), c["entries_capped_liquidity"]),
          "| {} | {} / {} |".format(t("c_noq"), c["entries_skipped_no_quote"], c["exits_delayed_no_quote"]),
          "| {} | {} |".format(t("c_gap"), c["gap_stop_exits"]), ""]
    for note in w["tariff_notes"]:
        L += ["_" + note + "_", ""]
    na = t("na")
    st, b, d = rb["cost_stress"], rb["bootstrap_expectancy_pct"], rb["dsr_detail"]
    L += ["## " + t("robust_h"), "", "| | |", "|---|---|",
          "| {} | CAGR {} · Sharpe {} |".format(t("r_stress"), P(st["cagr"]), N(st["sharpe"])),
          "| {} | {} … {} |".format(t("r_boot"), P(b["ci_low"], 2, True) if b else na, P(b["ci_high"], 2, True) if b else na),
          "| {} | {} |".format(t("r_dsr"), P(d["dsr"] * 100, 0) if d.get("dsr") is not None else na),
          "| {} | {} |".format(t("r_pbo"), N(rb["pbo"]["pbo"]) if rb["pbo"].get("available") else t("not_run"))]
    wf = rb["walk_forward"]
    for mode in ("anchored", "rolling"):
        x = wf.get(mode) if isinstance(wf, dict) else None
        if x and x.get("available"):
            L.append("| {} | {} / {} = {} |".format(t("r_wf", m=mode), N(x["oos_sharpe_mean"]), N(x["is_sharpe_mean"]),
                                                    N(x["oos_is_ratio"])))
        elif x:
            L.append("| {} | {} ({}) |".format(t("r_wf", m=mode), na, x.get("reason", "")))
    if not wf.get("anchored"):
        L.append("| {} | {} |".format(t("r_wf", m="—"), t("not_run")))
    pl = rb["plateau"]
    L += ["| {} | {} |".format(t("r_plateau"), N(pl["plateau_ratio"]) if pl.get("available") else t("not_run")),
          "| {} | {} |".format(t("r_t"), N(rb["t_stat"])), "",
          "- " + t("r_dsr_expl", n=rb["trials"]), "- " + t("r_pbo_expl")]
    if r["command"] == "run":
        L.append("- " + t("r_trials_note", n=rb["trials"]))
    L.append("")
    if r.get("grid"):
        g = r["grid"]
        L += ["## " + t("grid_h", n=g["variants"]), "", "| {} | CAGR | Sharpe | DD | {} |".format(t("params"), t("t_n")),
              "|---|---|---|---|---|"]
        for row in g["results"][:15]:
            L.append("| {} | {} | {} | {} | {} |".format(", ".join("{}={}".format(k, v) for k, v in row["params"].items()),
                                                       P(row["cagr"]), N(row["sharpe"]), P(row["max_dd"]), row["trades"]))
        L.append("")
        x = wf.get("anchored")
        if x and x.get("available"):
            L += ["### " + t("wf_h"), "", "| {} | {} | {} | Sharpe IS | Sharpe OOS | OOS |".format(t("col_is"), t("col_oos"), t("col_choice")),
                  "|---|---|---|---|---|---|"]
            for f in x["folds"]:
                L.append("| {}–{} | {}–{} | {} | {} | {} | {} |".format(f["is_from"], f["is_to"], f["oos_from"], f["oos_to"],
                                                                     f["chosen"].replace('"', ""), N(f["is_sharpe"]),
                                                                     N(f["oos_sharpe"]), P(f["oos_return_pct"], 1, True)))
            L.append("")
    L += ["## " + t("year_h"), "", "| {} | {} | WIG | {} | {} | {} |".format(t("col_year"), t("col_ret"), t("col_ew"), t("col_tr"), t("col_tax")),
          "|---|---|---|---|---|---|"]
    for y in r["by_year"]:
        L.append("| {} | {} | {} | {} | {} | {} |".format(y["year"], P(y["return_pct"], 1, True), P(y["wig_pct"], 1, True),
                                                        P(y["equal_weight_pct"], 1, True), y["trades_closed"], Z(y["tax_pln"])))
    L.append("")
    rs = r["regime_source"]
    L += ["## " + t("regime_h"), "",
          t("regime_src_proxy") if rs["source"] == "proxy_wig_sma200" else t("regime_src_report", f=rs["file"], n=rs["report_sessions"]), "",
          "| {} | {} | {} | {} | {} |".format(t("col_state"), t("col_sessions"), t("col_sann"), t("col_wann"), t("col_exp")),
          "|---|---|---|---|---|"]
    for g in r["by_regime"]:
        L.append("| {} | {} | {} | {} | {} |".format(g["state"], g["sessions"], P(g["strategy_ann_return_pct"], 1, True),
                                                   P(g["wig_ann_return_pct"], 1, True), P(g["avg_exposure_pct"], 0)))
    L.append("")
    tr = r["trades"]
    if tr:
        shown = tr[-max_trades:]
        L += ["## " + t("trades_list_h", n=len(shown), t=len(tr)), "",
              "| {} | {} | {} | {} | {} | {} |".format(t("col_name"), t("col_in"), t("col_out"), t("col_pnl"), t("col_r"), t("col_why")),
              "|---|---|---|---|---|---|"]
        for x in shown:
            L.append("| {} | {} @ {} | {} @ {} | {} ({}) | {} | {} |".format(
                x["name"], x["entry_date"], N(x["entry_price"]), x["exit_date"], N(x["exit_price"]), Z(x["pnl_pln"]),
                P(x["ret_pct"], 1, True), N(x["r_multiple"]), EXIT_REASONS.get(x["reason"], {}).get(lang, x["reason"])))
        L.append("")
    s = r["settings"]
    acct = t("acct_" + s["account"], rule=t("rule_" + s["loss_rule"])) if s["account"] == "regular" else t("acct_ike")
    L += ["## " + t("strategy_h"), "", r["strategy"]["description"], "",
          "**{}:** {}".format(t("params"), ", ".join("{}={}".format(k, v) for k, v in sorted(r["strategy"]["params"].items()))), "",
          "- Broker: {} ({}% min {}), {} {}".format(s["broker_label"], N(s["commission_pct"]), Z(s["commission_min"], 2),
                                                   "kapitał" if lang == "pl" else "capital", Z(s["capital"])),
          "- Sizing: {}{}".format(s["sizing"], " ({}% risk)".format(N(s["risk_pct"], 1)) if s["sizing"] == "risk" else "")]
    for line in STRINGS["assumptions"][lang]:
        L.append("- " + line.format(adv=N(s["max_adv_pct"], 0), cr=N(s["cash_rate_pct"], 1), acct=acct))
    L += ["", "{}: {} s".format(t("runtime"), N(r["runtime_s"], 1)), "",
          "## " + t("sources"), ""] + ["- " + x for x in SOURCES] + [
          "- " + ("Dane na sesję" if lang == "pl" else "Data as of session") + ": " + prov["as_of"], "", "---",
          "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


def list_strategies(lang: str, as_json: bool) -> str:
    rows = []
    for name, cls in bs.REGISTRY.items():
        prm = dict(bs.UNIVERSE_DEFAULTS)
        prm.update(cls.PARAMS)
        rows.append({"name": name, "kind": cls.kind, "description": cls.description[lang], "params": prm,
                     "grid": cls.GRID})
    if as_json:
        return json.dumps({"strategies": rows, "rules_spec": bs.RULES_SPEC}, ensure_ascii=False, indent=2)
    L = ["# " + ("Strategie wbudowane" if lang == "pl" else "Built-in strategies"), ""]
    for r in rows:
        L += ["## " + r["name"], "", r["description"], "",
              "- params: " + ", ".join("{}={}".format(k, v) for k, v in r["params"].items()),
              "- grid: " + ("; ".join("{}={}".format(k, ",".join(map(str, v))) for k, v in r["grid"].items()) or "—"), ""]
    L += ["## rules (--rules file.json)", "", "```json", json.dumps(bs.RULES_SPEC, indent=2), "```", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "list-strategies":
        print(list_strategies(args.lang, args.json))
        return 0
    if args.max_trades_md < 0:
        parser.error("--max-trades-md must be >= 0")
    if getattr(args, "max_trials", 1) < 2 and args.command != "run":
        parser.error("--max-trials must be >= 2")
    try:
        result = run(args)
    except InputError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    except gpw_archive.GPWFetchError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    md = render_markdown(result, args.lang, args.max_trades_md)
    if not args.no_files:
        out = Path(args.output_dir).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        base = "gpw_backtest_{}_{}".format(result["strategy"]["name"].replace(":", "-"), stamp)
        jp, mp = out / (base + ".json"), out / (base + ".md")
        jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        mp.write_text(md, encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else md)
    if not args.no_files:
        print("\nJSON: {}\nMarkdown: {}".format(jp, mp), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
