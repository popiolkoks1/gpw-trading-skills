"""Pure assembly engine of gpw-daily-brief (no I/O, no network).

Input: the newest (and previous) JSON reports of the other GPW skills as
returned by ``daily_brief_inputs.collect``, the journal's open positions,
the watchlist items and last closes. Output: one dict with the brief's
sections; :func:`render_markdown` turns it into the one-page Markdown.

Every rule and threshold is documented in ``references/methodology.md``.
Nothing is computed here that another skill already computes: the brief
selects, compares with the previous report, ranks and cites.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, Iterable, List, Optional, Tuple

from gpwlib import fmt, plcal

VERSION = "1.0.0"
SKILL = "gpw-daily-brief"

# ---------------------------------------------------------------- thresholds
# Distance to the stop (as % of the last close) below which a position is
# flagged "near stop": 3% is about 1.5 average daily ranges of a WIG20 share
# (daily sigma ~1.5-2%), i.e. one normal bad session can reach the stop.
NEAR_STOP_PCT = 3.0
# Watchlist zones vs the pivot, identical to gpw-trend-screener's execution
# states (pre_breakout_pct = 5, breakout_max_pct = 5; Minervini: do not buy
# more than 5% above the pivot).
PIVOT_NEAR_PCT = 5.0
PIVOT_EXTENDED_PCT = 5.0
# Change thresholds for "what changed" (below them a move is daily noise):
# top-risk score 5 points (one sub-component band), breadth score 5 points
# (a quarter of a breadth zone, which spans 20 points), macro score 0.10
# (the macro composite moves in 0.05 steps; 0.10 = a component flipped).
TOP_RISK_DELTA = 5.0
BREADTH_DELTA = 5.0
MACRO_DELTA = 0.10
# Events of a held stock within this many sessions are "imminent" (action
# item): two sessions leave one session to adjust size or stop beforehand.
IMMINENT_SESSIONS = 2
# Default caps of the one-page brief (``--full`` lifts them).
CAPS = {"attention": 3, "changes": 6, "positions": 8, "watchlist": 6, "signals": 8, "calendar": 8}
# In the one-page view no single source may take more than this many signal
# slots, so a busy earnings season cannot hide a new VCP or an insider
# cluster (the JSON keeps the full ranked list).
PER_SOURCE_CAP = 4
CAPS_FULL = {"attention": 20, "changes": 30, "positions": 50, "watchlist": 40, "signals": 30, "calendar": 40}

REGIME_RANK = {"bear": 0, "correction": 1, "rally_attempt": 2, "uptrend_under_pressure": 3, "confirmed_uptrend": 4}
POSTURE_RANK = {"capital_preservation": 0, "defensive": 1, "normal": 2, "offensive": 3}
BREAKER_RANK = {"pause": 0, "reduce": 1, "ok": 2}
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "info": 3}
# Tie-break order of signal kinds (more decision-relevant first: negative
# information about a stock outranks positive, KNF/ESPI facts outrank
# price-pattern detections).
SIGNAL_KIND_ORDER = ["earnings_strong_negative", "insider_cluster_sell", "short_newly_crowded",
                     "insider_cluster_buy", "earnings_strong_positive", "vcp_new", "short_new_position",
                     "short_no_longer_crowded"]
# Strength used when a source gives no score (documented in methodology.md).
DEFAULT_STRENGTH = {"insider_cluster_buy": 60.0, "insider_cluster_sell": 60.0, "short_newly_crowded": 50.0,
                    "short_new_position": 25.0, "short_no_longer_crowded": 20.0}

LABELS = {
    "regime": {
        "confirmed_uptrend": {"pl": "potwierdzony trend wzrostowy", "en": "confirmed uptrend"},
        "uptrend_under_pressure": {"pl": "trend wzrostowy pod presją", "en": "uptrend under pressure"},
        "rally_attempt": {"pl": "próba odbicia", "en": "rally attempt"},
        "correction": {"pl": "korekta", "en": "correction"},
        "bear": {"pl": "bessa", "en": "bear market"},
    },
    "posture": {
        "offensive": {"pl": "ofensywna", "en": "offensive"},
        "normal": {"pl": "normalna", "en": "normal"},
        "defensive": {"pl": "defensywna", "en": "defensive"},
        "capital_preservation": {"pl": "ochrona kapitału", "en": "capital preservation"},
    },
    "breaker": {
        "ok": {"pl": "ok", "en": "ok"},
        "reduce": {"pl": "zmniejsz ryzyko", "en": "reduce"},
        "pause": {"pl": "pauza", "en": "pause"},
    },
    "zone": {
        "strong": {"pl": "silna", "en": "strong"}, "healthy": {"pl": "zdrowa", "en": "healthy"},
        "neutral": {"pl": "neutralna", "en": "neutral"}, "weakening": {"pl": "słabnąca", "en": "weakening"},
        "critical": {"pl": "krytyczna", "en": "critical"},
        "strong_bull": {"pl": "silna hossa", "en": "strong bull"}, "bull": {"pl": "hossa", "en": "bull"},
        "cautious": {"pl": "ostrożność", "en": "cautious"}, "bear": {"pl": "bessa", "en": "bear"},
    },
    "macro": {
        "supportive": {"pl": "sprzyja", "en": "supportive"}, "mixed": {"pl": "mieszane", "en": "mixed"},
        "headwind": {"pl": "wiatr w oczy", "en": "headwind"},
    },
    "event": {
        "periodic_report": {"pl": "raport okresowy", "en": "periodic report"},
        "agm": {"pl": "walne zgromadzenie", "en": "general meeting"},
        "dividend_record": {"pl": "dzień dywidendy", "en": "dividend record date"},
        "dividend_payment": {"pl": "wypłata dywidendy", "en": "dividend payment"},
        "ex_dividend": {"pl": "odcięcie dywidendy", "en": "ex-dividend"},
        "index_revision": {"pl": "rewizja indeksów", "en": "index revision"},
        "mpc_meeting": {"pl": "posiedzenie RPP", "en": "MPC meeting"},
        "macro_release": {"pl": "dane makro", "en": "macro release"},
        "msci_review": {"pl": "przegląd MSCI", "en": "MSCI review"},
        "futures_expiry": {"pl": "wygasanie kontraktów", "en": "futures expiry"},
        "other": {"pl": "inne", "en": "other"},
    },
    "date_kind": {
        "confirmed": {"pl": "potwierdzona", "en": "confirmed"},
        "estimated": {"pl": "szacunek", "en": "estimated"},
        "statutory_deadline": {"pl": "termin ustawowy", "en": "statutory deadline"},
    },
    "earn": {
        "strong_positive": {"pl": "silnie pozytywna", "en": "strong positive"},
        "positive": {"pl": "pozytywna", "en": "positive"}, "neutral": {"pl": "neutralna", "en": "neutral"},
        "negative": {"pl": "negatywna", "en": "negative"},
        "strong_negative": {"pl": "silnie negatywna", "en": "strong negative"},
    },
    "conf": {
        "high": {"pl": "wysoka", "en": "high"}, "medium": {"pl": "średnia", "en": "medium"},
        "low": {"pl": "niska", "en": "low"},
    },
    "scr_state": {
        "pre_breakout": {"pl": "przed wybiciem", "en": "pre-breakout"},
        "breakout": {"pl": "wybicie", "en": "breakout"},
        "breakout_unconfirmed": {"pl": "wybicie bez potwierdzenia obrotem", "en": "breakout, turnover unconfirmed"},
        "extended": {"pl": "rozciągnięta", "en": "extended"},
        "damaged": {"pl": "uszkodzona", "en": "damaged"},
        "invalid": {"pl": "nieważna", "en": "invalid"},
    },
    "wl_alert": {
        "stop_breached": {"pl": "stop przebity", "en": "stop breached"},
        "near_stop": {"pl": "blisko stopu", "en": "near stop"},
        "pivot_breakout": {"pl": "wybicie ponad pivot dziś", "en": "crossed the pivot today"},
        "in_buy_range": {"pl": "w strefie do 5% nad pivotem", "en": "within 5% above pivot"},
        "extended": {"pl": "rozciągnięta", "en": "extended"},
        "near_pivot": {"pl": "blisko pivotu", "en": "near pivot"},
        "event_soon": {"pl": "zdarzenie:", "en": "event:"},
        "no_price_data": {"pl": "brak notowań", "en": "no quotes"},
        "no_recent_quotes": {"pl": "brak świeżych notowań", "en": "no recent quotes"},
    },
    "wl_status": {
        "invalidated": {"pl": "unieważniona (kurs < stop)", "en": "invalidated (close < stop)"},
        "breakout": {"pl": "nad pivotem (≤5%)", "en": "above pivot (≤5%)"},
        "near_pivot": {"pl": "blisko pivotu", "en": "near pivot"},
        "extended": {"pl": "rozciągnięta (>5% nad pivotem)", "en": "extended (>5% above pivot)"},
        "below": {"pl": "daleko pod pivotem", "en": "far below pivot"},
        "no_pivot": {"pl": "brak pivotu", "en": "no pivot"},
        "no_price": {"pl": "brak kursu", "en": "no price"},
    },
}

STRINGS = {
    "title": {"pl": "Dzienny brief GPW — {date}", "en": "GPW daily brief — {date}"},
    "title_weekly": {"pl": "Tygodniowy brief GPW — {date}", "en": "GPW weekly brief — {date}"},
    "stale_top": {"pl": "⚠️ Dane nieaktualne: {items}. Odśwież je przed decyzjami (komendy w tabeli świeżości).",
                  "en": "⚠️ Stale data: {items}. Refresh before deciding (commands in the freshness table)."},
    "missing_top": {"pl": "Brak wejść: {items} — te sekcje są niepełne.",
                    "en": "Missing inputs: {items} — those sections are incomplete."},
    "h_headline": {"pl": "W skrócie", "en": "Headline"},
    "h_attention": {"pl": "Wymaga uwagi", "en": "Needs attention"},
    "h_changes": {"pl": "Co się zmieniło", "en": "What changed"},
    "h_positions": {"pl": "Twoje pozycje", "en": "Your positions"},
    "h_watchlist": {"pl": "Watchlista", "en": "Watchlist"},
    "h_signals": {"pl": "Nowe sygnały", "en": "New signals"},
    "h_calendar": {"pl": "Kalendarz (najbliższe {n} sesji)", "en": "Calendar (next {n} sessions)"},
    "h_weekly": {"pl": "Przegląd tygodniowy", "en": "Weekly review"},
    "h_fresh": {"pl": "Świeżość danych", "en": "Data freshness"},
    "h_sources": {"pl": "Źródła / Sources", "en": "Sources"},
    "nothing_urgent": {"pl": "Nic pilnego — żadna pozycja nie jest przy stopie, bezpiecznik ok, reżim bez pogorszenia.",
                       "en": "Nothing urgent — no position at its stop, breaker ok, no regime downgrade."},
    "hl_regime": {"pl": "**Reżim (WIG):** {state}{chg} · ryzyko szczytu {risk}/100 · dni dystrybucji {dd}{ddchg}",
                  "en": "**Regime (WIG):** {state}{chg} · top risk {risk}/100 · distribution days {dd}{ddchg}"},
    "hl_regime_missing": {"pl": "**Reżim:** brak raportu gpw-market-regime.",
                          "en": "**Regime:** no gpw-market-regime report."},
    "hl_exposure": {"pl": "**Ekspozycja:** postawa {posture}{chg} · do {ceil}% w akcjach · ryzyko {risk}% na transakcję · {newpos} nowe poz./tydz. · bezpiecznik: {cb}",
                    "en": "**Exposure:** {posture} posture{chg} · up to {ceil}% in equities · {risk}% risk per trade · {newpos} new positions/week · breaker: {cb}"},
    "hl_exposure_missing": {"pl": "**Ekspozycja:** brak raportu gpw-exposure-coach.",
                            "en": "**Exposure:** no gpw-exposure-coach report."},
    "hl_breadth": {"pl": "**Szerokość:** {score}/100 ({zone}){chg}{sect}",
                   "en": "**Breadth:** {score}/100 ({zone}){chg}{sect}"},
    "hl_breadth_missing": {"pl": "**Szerokość:** brak raportu gpw-market-breadth.",
                           "en": "**Breadth:** no gpw-market-breadth report."},
    "hl_sect": {"pl": " · liderzy sektorów: {names}", "en": " · sector leaders: {names}"},
    "was": {"pl": " (było: {v})", "en": " (was: {v})"},
    "unchanged": {"pl": " (bez zmian)", "en": " (unchanged)"},
    "delta": {"pl": " ({d} vs {date})", "en": " ({d} vs {date})"},
    "first": {"pl": "Pierwszy raport w folderze — brak punktu odniesienia do porównania.",
              "en": "First report in the folder — nothing to compare with yet."},
    "no_changes": {"pl": "Bez istotnych zmian od {date}.", "en": "No material changes since {date}."},
    "chg_regime": {"pl": "Reżim: {a} → {b} ({dir})", "en": "Regime: {a} → {b} ({dir})"},
    "chg_dd": {"pl": "Dni dystrybucji: {a} → {b}", "en": "Distribution days: {a} → {b}"},
    "chg_toprisk": {"pl": "Ryzyko szczytu: {a} → {b}", "en": "Top risk: {a} → {b}"},
    "chg_posture": {"pl": "Postawa: {a} → {b} ({dir})", "en": "Posture: {a} → {b} ({dir})"},
    "chg_ceiling": {"pl": "Limit ekspozycji: {a}% → {b}%", "en": "Exposure ceiling: {a}% → {b}%"},
    "chg_breaker": {"pl": "Bezpiecznik: {a} → {b}", "en": "Circuit breaker: {a} → {b}"},
    "chg_breadth": {"pl": "Szerokość: {a} → {b} pkt ({za} → {zb})", "en": "Breadth: {a} → {b} pts ({za} → {zb})"},
    "chg_leaders": {"pl": "Liderzy sektorów: weszli {inn}; wypadli {out}", "en": "Sector leaders: in {inn}; out {out}"},
    "chg_macro": {"pl": "Makro: {a} → {b} (wynik {sa} → {sb})", "en": "Macro: {a} → {b} (score {sa} → {sb})"},
    "better": {"pl": "poprawa", "en": "better"},
    "worse": {"pl": "pogorszenie", "en": "worse"},
    "none": {"pl": "—", "en": "—"},
    "no_journal": {"pl": "Brak otwartych pozycji w dzienniku (gpw-trade-journal).",
                   "en": "No open positions in the journal (gpw-trade-journal)."},
    "journal_off": {"pl": "Dziennik pominięty (--no-journal).", "en": "Journal skipped (--no-journal)."},
    "pos_head": {"pl": "| Spółka | Kurs | Stop | Do stopu | Wynik | Uwagi |",
                 "en": "| Stock | Close | Stop | To stop | P&L | Notes |"},
    "breaker_line": {"pl": "Bezpiecznik: **{cb}**{extra} [exposure-coach]", "en": "Circuit breaker: **{cb}**{extra} [exposure-coach]"},
    "pause_until": {"pl": " do {d}", "en": " until {d}"},
    "no_watchlist": {"pl": "Brak watchlisty ({path}) — dodaj spółki skillem gpw-watchlist.",
                     "en": "No watchlist ({path}) — add names with gpw-watchlist."},
    "watchlist_off": {"pl": "Watchlista pominięta (--no-watchlist).", "en": "Watchlist skipped (--no-watchlist)."},
    "wl_head": {"pl": "| Spółka | Etap | Kurs | Pivot | Do pivotu | Stop | Status |",
                "en": "| Stock | Stage | Close | Pivot | To pivot | Stop | Status |"},
    "wl_quiet": {"pl": "{n} pozycji na watchliście, żadna nie jest blisko pivotu ani unieważniona.",
                 "en": "{n} watchlist names, none near its pivot or invalidated."},
    "wl_rest": {"pl": "+{n} innych na watchliście bez sygnału (pełna lista: `--full` lub gpw-watchlist list).",
                "en": "+{n} other watchlist names without a signal (full list: `--full` or gpw-watchlist list)."},
    "no_signals": {"pl": "Brak nowych sygnałów od {date}.", "en": "No new signals since {date}."},
    "no_events": {"pl": "Brak zdarzeń o znaczeniu ≥ średnie w tym oknie.", "en": "No events of medium+ importance in this window."},
    "more": {"pl": "… i {n} więcej (`--full`).", "en": "… and {n} more (`--full`)."},
    "fresh_ok": {"pl": "Wszystkie wejścia aktualne ({n}, dane na {dates}).", "en": "All inputs fresh ({n}, data as of {dates})."},
    "fresh_rest": {"pl": "Pozostałe wejścia aktualne ({n}, dane na {dates}).", "en": "Other inputs fresh ({n}, data as of {dates})."},
    "w_div_none": {"pl": "**Dywidendy:** brak odcięć w portfelu w ciągu {n} sesji [dividend-analyzer]",
                   "en": "**Dividends:** no ex-dates in the portfolio within {n} sessions [dividend-analyzer]"},
    "fresh_head": {"pl": "| Wejście | Plik | Dane na | Sesji wstecz | Status | Jak uzupełnić |",
                   "en": "| Input | File | As of | Sessions behind | Status | How to produce |"},
    "st_ok": {"pl": "ok", "en": "ok"}, "st_stale": {"pl": "NIEAKTUALNE", "en": "STALE"},
    "st_missing": {"pl": "BRAK", "en": "MISSING"},
    "st_not_needed": {"pl": "niepotrzebne", "en": "not needed"},
    "fresh_hint": {"pl": "Komendy uruchamiaj z katalogu danego skilla; DIR = folder raportów. Całość naraz: `python3 scripts/gpw_daily_brief.py run --output-dir DIR`.",
                   "en": "Run commands from that skill's directory; DIR = the report folder. Everything at once: `python3 scripts/gpw_daily_brief.py run --output-dir DIR`."},
    "w_macro": {"pl": "**Makro:** {comp} (wynik {score}, pewność {conf}){chg} [macro-regime]",
                "en": "**Macro:** {comp} (score {score}, confidence {conf}){chg} [macro-regime]"},
    "w_macro_watch": {"pl": "  · do obserwacji: {t}", "en": "  · watch: {t}"},
    "w_portfolio": {"pl": "**Portfel:** {value}, wynik {pnl}, VaR 1 dzień {var} ({varp}), efektywnie {eff} niezależnych pozycji, do przeglądu: {rev} [portfolio-check]",
                    "en": "**Portfolio:** {value}, P&L {pnl}, 1-day VaR {var} ({varp}), {eff} effective positions, to review: {rev} [portfolio-check]"},
    "w_risk": {"pl": "**Stres:** najgorszy scenariusz {label}: {ret} ({loss}){tol} [risk-lab]",
               "en": "**Stress:** worst scenario {label}: {ret} ({loss}){tol} [risk-lab]"},
    "w_hedge": {"pl": "  · zabezpieczenie FW20: {c} kontr. (beta {b}) [risk-lab]",
                "en": "  · FW20 hedge: {c} contracts (beta {b}) [risk-lab]"},
    "w_pm": {"pl": "**Post-mortem sygnałów:** {tot} w rejestrze, {ev} ocenionych, {pend} czeka (najbliższe dojrzewa {nm}) [signal-postmortem]",
             "en": "**Signal post-mortem:** {tot} in the ledger, {ev} evaluated, {pend} pending (next matures {nm}) [signal-postmortem]"},
    "w_pm_line": {"pl": "  · {src}/{kind}: n={n}, trafność {hit}, śr. nadwyżka vs WIG {ex} pp ({h} sesji)",
                  "en": "  · {src}/{kind}: n={n}, hit rate {hit}, mean excess vs WIG {ex} pp ({h} sessions)"},
    "w_pm_demo": {"pl": "  · UWAGA: raport DEMO — sygnały syntetyczne, to nie są Twoje wyniki.",
                  "en": "  · NOTE: DEMO report — synthetic signals, not your track record."},
    "w_div": {"pl": "**Dywidendy (≤ {n} sesji):** {items} [dividend-analyzer]",
              "en": "**Dividends (≤ {n} sessions):** {items} [dividend-analyzer]"},
    "w_review": {"pl": "**Przegląd transakcji:** ostatni raport {file} (dane na {date}) [trade-review]",
                 "en": "**Trade review:** latest report {file} (as of {date}) [trade-review]"},
    "w_review_missing": {"pl": "**Przegląd transakcji:** brak raportu — `cd ../gpw-trade-review && python3 scripts/trade_review.py --month {m}`",
                         "en": "**Trade review:** no report — `cd ../gpw-trade-review && python3 scripts/trade_review.py --month {m}`"},
    "w_missing": {"pl": "**{name}:** brak raportu ({why}).", "en": "**{name}:** no report ({why})."},
    "why_no_pos": {"pl": "brak otwartych pozycji", "en": "no open positions"},
    "why_missing": {"pl": "uruchom `run --weekly`", "en": "run `run --weekly`"},
    "rules": {"pl": "Kolejność: pilność (krytyczne → wysokie → średnie), potem pozycje → watchlista → reszta, potem siła sygnału 0-100. Reguły: references/methodology.md.",
              "en": "Order: severity (critical → high → medium), then positions → watchlist → others, then signal strength 0-100. Rules: references/methodology.md."},
    "footer_sources": {"pl": "Raporty wejściowe (skill, plik, dane na):", "en": "Input reports (skill, file, as of):"},
    "prices_src": {"pl": "Kursy zamknięcia: archiwum notowań GPW (cache lokalny), sesja {d}.",
                   "en": "Closing prices: GPW quotes archive (local cache), session {d}."},
    # attention texts
    "a_below_stop": {"pl": "{t}: kurs {c} jest na/pod stopem {s} — sprawdź plan wyjścia w dzienniku.",
                     "en": "{t}: close {c} is at/below the stop {s} — check the exit plan in your journal."},
    "a_near_stop": {"pl": "{t}: {d}% nad stopem {s}.", "en": "{t}: {d}% above the stop {s}."},
    "a_pause": {"pl": "Bezpiecznik PAUZA{u}: nowe transakcje wstrzymane (gpw-trade-review pomoże przeanalizować serię).",
                "en": "Circuit breaker PAUSE{u}: new trades on hold (gpw-trade-review helps study the streak)."},
    "a_reduce": {"pl": "Bezpiecznik ZMNIEJSZ: ryzyko na transakcję obniżone do {r}%.",
                 "en": "Circuit breaker REDUCE: risk per trade lowered to {r}%."},
    "a_regime_down": {"pl": "Reżim pogorszył się: {a} → {b}. Limit ekspozycji {c}%.",
                      "en": "Regime downgraded: {a} → {b}. Exposure ceiling {c}%."},
    "a_posture_down": {"pl": "Postawa pogorszyła się: {a} → {b}.", "en": "Posture downgraded: {a} → {b}."},
    "a_earn_neg": {"pl": "{t}: {lab} reakcja na wyniki (CAR 3 dni {car}) — przejrzyj tezę i stop.",
                   "en": "{t}: {lab} earnings reaction (3-day CAR {car}) — review the thesis and stop."},
    "a_event": {"pl": "{t}: {ev} {d} ({k}) — za {n} sesj.", "en": "{t}: {ev} on {d} ({k}) — in {n} session(s)."},
    "a_crowded": {"pl": "{t}: zatłoczony short {p}% ({h} podm.) [KNF].", "en": "{t}: crowded short {p}% ({h} holders) [KNF]."},
    "a_cluster_sell": {"pl": "{t}: klaster sprzedaży insiderów.", "en": "{t}: insider selling cluster."},
    "a_wl_invalid": {"pl": "Watchlista {t}: kurs {c} pod stopem/unieważnieniem {s}.",
                     "en": "Watchlist {t}: close {c} below its stop/invalidation {s}."},
    "a_wl_pivot": {"pl": "Watchlista {t}: {d}% od pivotu {p} ({st}).", "en": "Watchlist {t}: {d}% from pivot {p} ({st})."},
    "a_stale": {"pl": "Nieaktualne lub brakujące wejścia: {items}.", "en": "Stale or missing inputs: {items}."},
    # signals
    "s_vcp_new": {"pl": "{t}: nowy VCP, priorytet {sc} ({band}), stan {st}, pivot {p}", "en": "{t}: new VCP, priority {sc} ({band}), state {st}, pivot {p}"},
    "s_insider_cluster_buy": {"pl": "{t}: klaster zakupów insiderów ({n} os.), ostatni {d}", "en": "{t}: insider cluster buy ({n} people), last {d}"},
    "s_insider_cluster_sell": {"pl": "{t}: klaster sprzedaży insiderów ({n} os.), ostatni {d}", "en": "{t}: insider cluster sell ({n} people), last {d}"},
    "s_short_newly_crowded": {"pl": "{t}: short stał się zatłoczony — {p}% ({h} podm.)", "en": "{t}: short became crowded — {p}% ({h} holders)"},
    "s_short_no_longer_crowded": {"pl": "{t}: short przestał być zatłoczony — {p}%", "en": "{t}: short no longer crowded — {p}%"},
    "s_short_new_position": {"pl": "{t}: nowa pozycja krótka {h} {p}% ({d})", "en": "{t}: new short position {h} {p}% ({d})"},
    "s_earnings_strong_negative": {"pl": "{t}: silnie negatywna reakcja na wyniki, CAR 3 dni {car} ({d})", "en": "{t}: strong negative earnings reaction, 3-day CAR {car} ({d})"},
    "s_earnings_strong_positive": {"pl": "{t}: silnie pozytywna reakcja na wyniki, CAR 3 dni {car} ({d})", "en": "{t}: strong positive earnings reaction, 3-day CAR {car} ({d})"},
    "tag_held": {"pl": "pozycja", "en": "held"}, "tag_watch": {"pl": "watchlista", "en": "watchlist"},
    "n_more_held": {"pl": "zdarzenie", "en": "event"},
    "pf_events": {"pl": "{ev} {d}", "en": "{ev} {d}"},
    "pf_crowded": {"pl": "short {p}% (zatłoczony)", "en": "short {p}% (crowded)"},
    "pf_short": {"pl": "short {p}%", "en": "short {p}%"},
    "pf_csell": {"pl": "insiderzy sprzedają", "en": "insiders selling"},
    "pf_cbuy": {"pl": "insiderzy kupują", "en": "insiders buying"},
    "pf_earn": {"pl": "wyniki: {lab} ({car})", "en": "earnings: {lab} ({car})"},
    "pf_no_stop": {"pl": "brak stopu", "en": "no stop"},
    "pf_below": {"pl": "POD STOPEM", "en": "BELOW STOP"},
    "pf_near": {"pl": "blisko stopu", "en": "near stop"},
}

SHORT_SOURCE = {
    "gpw-market-regime": "market-regime", "gpw-exposure-coach": "exposure-coach", "gpw-market-breadth": "market-breadth",
    "gpw-sector-trend": "sector-trend", "gpw-trend-screener": "trend-screener", "gpw-event-calendar": "event-calendar",
    "gpw-ownership": "ownership", "gpw-earnings-reaction": "earnings-reaction", "gpw-watchlist": "watchlist",
    "gpw-macro-regime": "macro-regime", "gpw-portfolio-check": "portfolio-check", "gpw-risk-lab": "risk-lab",
    "gpw-signal-postmortem": "signal-postmortem", "gpw-dividend-analyzer": "dividend-analyzer",
    "gpw-trade-review": "trade-review", "gpw-trade-journal": "trade-journal", "gpw-daily-brief": "daily-brief",
    "gpw-archive": "gpw-archive",
}


# ------------------------------------------------------------------ helpers
def label(group: str, key, lang: str) -> str:
    if key is None:
        return "—"
    entry = LABELS.get(group, {}).get(key)
    return entry.get(lang, entry.get("en")) if entry else str(key).replace("_", " ")


def _d(s) -> Optional[dt.date]:
    if isinstance(s, dt.date):
        return s
    if isinstance(s, str) and len(s) >= 10:
        try:
            return dt.date.fromisoformat(s[:10])
        except ValueError:
            return None
    return None


def nth_session_after(day: dt.date, n: int) -> dt.date:
    """Date of the n-th probable GPW session after ``day`` (n >= 1)."""
    cur, k = day, 0
    while k < n:
        cur += dt.timedelta(days=1)
        if plcal.is_probable_session(cur):
            k += 1
    return cur


def nth_session_before(day: dt.date, n: int) -> dt.date:
    """Date of the n-th probable session before ``day`` (n >= 1)."""
    cur, k = day, 0
    while k < n:
        cur -= dt.timedelta(days=1)
        if plcal.is_probable_session(cur):
            k += 1
    return cur


def sessions_until(start: dt.date, day: dt.date) -> int:
    """Number of probable sessions in (start, day]."""
    n, cur = 0, start
    while cur < day:
        cur += dt.timedelta(days=1)
        if plcal.is_probable_session(cur):
            n += 1
    return n


def _data(inputs: Dict, key: str, which: str = "current") -> Optional[Dict]:
    rec = (inputs.get(key) or {}).get(which)
    return rec.get("data") if rec else None


def _rec(inputs: Dict, key: str, which: str = "current") -> Optional[Dict]:
    return (inputs.get(key) or {}).get(which)


def _src(skill: str) -> str:
    return SHORT_SOURCE.get(skill, skill)


def _f(x, digits=1, lang="pl", signed=False):
    if x is None:
        return "—"
    s = fmt.num(float(x), digits, lang)
    if signed and float(x) > 0:
        s = "+" + s
    return s


def _pct(x, lang, digits=1, signed=True):
    return fmt.pct(x, digits, lang, signed=signed) if x is not None else "—"


# ------------------------------------------------------------- snapshot
def snapshot(inputs: Dict, which: str = "current") -> Dict:
    """Key state fields of the market inputs, for headline and diffs."""
    snap: Dict = {}
    reg = _data(inputs, "regime", which)
    if reg:
        r = reg.get("regime") or {}
        snap.update({"regime_state": r.get("state"), "top_risk_score": r.get("top_risk_score"),
                     "distribution_days": r.get("distribution_days"),
                     "regime_as_of": (_rec(inputs, "regime", which) or {}).get("as_of")})
    exp = _data(inputs, "exposure", which)
    if exp:
        lim = exp.get("limits") or {}
        snap.update({"posture": exp.get("posture"), "exposure_ceiling_pct": lim.get("exposure_ceiling_pct"),
                     "max_risk_per_trade_pct": lim.get("max_risk_per_trade_pct"),
                     "max_new_positions_per_week": lim.get("max_new_positions_per_week"),
                     "circuit_breaker": lim.get("circuit_breaker"),
                     "exposure_as_of": (_rec(inputs, "exposure", which) or {}).get("as_of")})
    br = _data(inputs, "breadth", which)
    if br:
        comp = br.get("composite") or {}
        snap.update({"breadth_score": comp.get("score"), "breadth_zone": comp.get("zone"),
                     "breadth_as_of": (_rec(inputs, "breadth", which) or {}).get("as_of")})
    sec = _data(inputs, "sector", which)
    if sec:
        snap.update({"sector_score": (sec.get("composite") or {}).get("score"),
                     "sector_zone": (sec.get("composite") or {}).get("zone"),
                     "sector_leaders": sector_leaders(sec)})
    mac = _data(inputs, "macro", which)
    if mac:
        m = mac.get("macro") or {}
        snap.update({"macro_composite": (m.get("state") or {}).get("composite"), "macro_score": m.get("score"),
                     "macro_as_of": (_rec(inputs, "macro", which) or {}).get("as_of")})
    return snap


def sector_leaders(sec: Dict, n: int = 3) -> List[str]:
    """Top ``n`` sectors by uptrend ratio (ties: 20-session performance vs
    WIG, then key). Sectors flagged ``low_sample`` are skipped: with 3-5
    companies one stock moves the ratio by 20-30 pp (gpw-sector-trend)."""
    rows = [s for s in (sec.get("sectors") or []) if isinstance(s, dict) and s.get("ratio") is not None
            and not s.get("low_sample")]
    rows.sort(key=lambda s: (-float(s["ratio"]), -float(s.get("vs_wig_pp") or 0.0), s.get("key", "")))
    return [s.get("key") for s in rows[:n]]


def sector_name(sec: Optional[Dict], key: str, lang: str) -> str:
    for s in (sec or {}).get("sectors") or []:
        if s.get("key") == key:
            return s.get("label_pl" if lang == "pl" else "label_en") or key
    return key


def baseline_snapshot(inputs: Dict, prev_brief: Optional[Dict]) -> Tuple[Dict, Dict]:
    """Previous values: from the previous report of each skill; fields with
    no previous report fall back to the previous brief's ``snapshot``.
    Returns (snapshot, {field: source label})."""
    prev = snapshot(inputs, "previous")
    origin = {k: "report" for k in prev}
    if prev_brief:
        for k, v in (prev_brief.get("snapshot") or {}).items():
            if k not in prev and v is not None:
                prev[k] = v
                origin[k] = "brief"
    return prev, origin


def _cmp_rank(rank: Dict, a, b) -> Optional[str]:
    if a in rank and b in rank and a != b:
        return "better" if rank[b] > rank[a] else "worse"
    return None


def changes(cur: Dict, prev: Dict, inputs: Dict, prev_dates: Dict, lang: str) -> List[Dict]:
    """State changes vs the baseline (rules: methodology.md "What changed")."""
    T = fmt.T(STRINGS, lang)
    out: List[Dict] = []
    sec = _data(inputs, "sector")

    def add(field, text, source, direction=None, severity="info", frm=None, to=None):
        out.append({"field": field, "from": frm, "to": to, "text": text, "source": source,
                    "direction": direction, "severity": severity, "since": prev_dates.get(source)})

    a, b = prev.get("regime_state"), cur.get("regime_state")
    if a and b and a != b:
        d = _cmp_rank(REGIME_RANK, a, b)
        add("regime_state", T("chg_regime", a=label("regime", a, lang), b=label("regime", b, lang),
                              dir=T(d) if d else "?"),
            "gpw-market-regime", d, "high" if d == "worse" else "info", a, b)
    a, b = prev.get("distribution_days"), cur.get("distribution_days")
    if a is not None and b is not None and a != b:
        add("distribution_days", T("chg_dd", a=a, b=b), "gpw-market-regime", "worse" if b > a else "better",
            frm=a, to=b)
    a, b = prev.get("top_risk_score"), cur.get("top_risk_score")
    if a is not None and b is not None and abs(float(b) - float(a)) >= TOP_RISK_DELTA:
        add("top_risk_score", T("chg_toprisk", a=_f(a, 0, lang), b=_f(b, 0, lang)), "gpw-market-regime",
            "worse" if b > a else "better", frm=a, to=b)
    a, b = prev.get("posture"), cur.get("posture")
    if a and b and a != b:
        d = _cmp_rank(POSTURE_RANK, a, b)
        add("posture", T("chg_posture", a=label("posture", a, lang), b=label("posture", b, lang),
                         dir=T(d) if d else "?"),
            "gpw-exposure-coach", d, "high" if d == "worse" else "info", a, b)
    a, b = prev.get("exposure_ceiling_pct"), cur.get("exposure_ceiling_pct")
    if a is not None and b is not None and float(a) != float(b):
        add("exposure_ceiling_pct", T("chg_ceiling", a=_f(a, 0, lang), b=_f(b, 0, lang)), "gpw-exposure-coach",
            "worse" if b < a else "better", frm=a, to=b)
    a, b = prev.get("circuit_breaker"), cur.get("circuit_breaker")
    if a and b and a != b:
        d = _cmp_rank(BREAKER_RANK, a, b)
        add("circuit_breaker", T("chg_breaker", a=label("breaker", a, lang), b=label("breaker", b, lang)),
            "gpw-exposure-coach", d, "high" if d == "worse" else "info", a, b)
    a, b = prev.get("breadth_score"), cur.get("breadth_score")
    za, zb = prev.get("breadth_zone"), cur.get("breadth_zone")
    if a is not None and b is not None and (abs(float(b) - float(a)) >= BREADTH_DELTA or (za and zb and za != zb)):
        add("breadth_score", T("chg_breadth", a=_f(a, 0, lang), b=_f(b, 0, lang), za=label("zone", za, lang),
                               zb=label("zone", zb, lang)), "gpw-market-breadth", "worse" if b < a else "better",
            frm=a, to=b)
    la, lb = prev.get("sector_leaders"), cur.get("sector_leaders")
    if la is not None and lb is not None and set(la) != set(lb):
        inn = [sector_name(sec, k, lang) for k in lb if k not in la]
        gone = [sector_name(sec, k, lang) for k in la if k not in lb]
        add("sector_leaders", T("chg_leaders", inn=", ".join(inn) or "—", out=", ".join(gone) or "—"),
            "gpw-sector-trend", frm=la, to=lb)
    a, b = prev.get("macro_composite"), cur.get("macro_composite")
    sa, sb = prev.get("macro_score"), cur.get("macro_score")
    if a and b and (a != b or (sa is not None and sb is not None and abs(float(sb) - float(sa)) >= MACRO_DELTA)):
        add("macro_composite", T("chg_macro", a=label("macro", a, lang), b=label("macro", b, lang),
                                 sa=_f(sa, 2, lang, True), sb=_f(sb, 2, lang, True)), "gpw-macro-regime",
            None if a == b else ("better" if (sb or 0) > (sa or 0) else "worse"), a, b)
    return out


# --------------------------------------------------------------- headline
def headline(cur: Dict, prev: Dict, inputs: Dict, prev_dates: Dict, lang: str) -> List[Dict]:
    T = fmt.T(STRINGS, lang)
    lines: List[Dict] = []
    if cur.get("regime_state"):
        a = prev.get("regime_state")
        chg = T("was", v=label("regime", a, lang)) if a and a != cur["regime_state"] else (
            T("unchanged") if a else "")
        dd, dda = cur.get("distribution_days"), prev.get("distribution_days")
        ddchg = T("was", v=dda) if dda is not None and dd is not None and dda != dd else ""
        lines.append({"key": "regime", "source": "gpw-market-regime",
                      "text": T("hl_regime", state=label("regime", cur["regime_state"], lang), chg=chg,
                                risk=_f(cur.get("top_risk_score"), 0, lang), dd=dd if dd is not None else "—",
                                ddchg=ddchg)})
    else:
        lines.append({"key": "regime", "source": "gpw-market-regime", "text": T("hl_regime_missing")})
    if cur.get("posture"):
        a = prev.get("posture")
        chg = T("was", v=label("posture", a, lang)) if a and a != cur["posture"] else ""
        lines.append({"key": "exposure", "source": "gpw-exposure-coach",
                      "text": T("hl_exposure", posture=label("posture", cur["posture"], lang), chg=chg,
                                ceil=_f(cur.get("exposure_ceiling_pct"), 0, lang),
                                risk=_f(cur.get("max_risk_per_trade_pct"), 2, lang),
                                newpos=cur.get("max_new_positions_per_week", "—"),
                                cb=label("breaker", cur.get("circuit_breaker"), lang))})
    else:
        lines.append({"key": "exposure", "source": "gpw-exposure-coach", "text": T("hl_exposure_missing")})
    if cur.get("breadth_score") is not None:
        a = prev.get("breadth_score")
        chg = ""
        if a is not None:
            chg = T("delta", d=_f(float(cur["breadth_score"]) - float(a), 1, lang, True),
                    date=prev.get("breadth_as_of") or prev_dates.get("gpw-market-breadth") or "?")
        else:
            br = _data(inputs, "breadth") or {}
            delta = (br.get("history") or {}).get("delta")
            prevh = (br.get("history") or {}).get("previous")
            if isinstance(delta, (int, float)) and isinstance(prevh, dict):
                chg = T("delta", d=_f(delta, 1, lang, True), date=prevh.get("date") or prevh.get("as_of") or "?")
        sect = ""
        sec = _data(inputs, "sector")
        if cur.get("sector_leaders"):
            sect = T("hl_sect", names=", ".join(sector_name(sec, k, lang) for k in cur["sector_leaders"]))
        lines.append({"key": "breadth", "source": "gpw-market-breadth",
                      "text": T("hl_breadth", score=_f(cur["breadth_score"], 0, lang),
                                zone=label("zone", cur.get("breadth_zone"), lang), chg=chg, sect=sect)})
    else:
        lines.append({"key": "breadth", "source": "gpw-market-breadth", "text": T("hl_breadth_missing")})
    return lines


# --------------------------------------------------------------- helpers 2
def calendar_events(inputs: Dict) -> List[Dict]:
    """Events of the newest upcoming + portfolio calendars, de-duplicated."""
    seen, out = set(), []
    for key in ("calendar", "calendar_portfolio"):
        data = _data(inputs, key)
        if not data:
            continue
        for e in data.get("events") or []:
            if not isinstance(e, dict) or not _d(e.get("date")):
                continue
            ident = e.get("id") or (e.get("ticker"), e.get("type"), e.get("date"), e.get("title"))
            if ident in seen:
                continue
            seen.add(ident)
            out.append(dict(e, _source=key))
    out.sort(key=lambda e: (e["date"], {"high": 0, "medium": 1, "low": 2}.get(e.get("importance"), 3),
                            e.get("ticker") or "", e.get("title") or ""))
    return out


def shorts_by_ticker(inputs: Dict, which: str = "current") -> Dict[str, Dict]:
    data = _data(inputs, "shorts", which) or {}
    return {str(b.get("ticker")).upper(): b for b in ((data.get("shorts") or {}).get("by_issuer") or [])
            if isinstance(b, dict) and b.get("ticker")}


def insiders_by_ticker(inputs: Dict) -> Dict[str, Dict]:
    data = _data(inputs, "insiders") or {}
    return {str(b.get("ticker")).upper(): b for b in ((data.get("insiders") or {}).get("by_ticker") or [])
            if isinstance(b, dict) and b.get("ticker")}


def latest_reactions(inputs: Dict) -> Dict[str, Dict]:
    data = _data(inputs, "earnings") or {}
    out: Dict[str, Dict] = {}
    for r in data.get("reactions") or []:
        if not isinstance(r, dict) or not r.get("ticker"):
            continue
        t = str(r["ticker"]).upper()
        day = r.get("reaction_date") or r.get("report_date") or ""
        if t not in out or day > (out[t].get("reaction_date") or out[t].get("report_date") or ""):
            out[t] = r
    return out


def screener_names(data: Optional[Dict], key: str = "watchlist") -> Dict[str, Dict]:
    return {str(w.get("name")).upper(): w for w in ((data or {}).get(key) or []) if isinstance(w, dict) and w.get("name")}


# --------------------------------------------------------------- positions
def positions_section(positions: Optional[List[Dict]], prices: Dict, inputs: Dict, as_of: Optional[dt.date],
                      event_sessions: int, lang: str) -> Dict:
    T = fmt.T(STRINGS, lang)
    if positions is None:
        return {"available": False, "items": [], "circuit_breaker": None}
    exp = _data(inputs, "exposure") or {}
    cb_detail = exp.get("circuit_breaker") if isinstance(exp.get("circuit_breaker"), dict) else {}
    cb = {"state": (exp.get("limits") or {}).get("circuit_breaker"), "pause_until": (cb_detail or {}).get("pause_until"),
          "source": "gpw-exposure-coach"} if exp else None
    events = calendar_events(inputs)
    horizon = nth_session_after(as_of, event_sessions) if as_of else None
    shorts = shorts_by_ticker(inputs)
    ins = insiders_by_ticker(inputs)
    reac = latest_reactions(inputs)
    items = []
    for p in positions:
        t = str(p.get("ticker")).upper()
        px = prices.get(t) or {}
        close = px.get("close")
        stop = p.get("stop")
        item = {"ticker": t, "thesis_id": p.get("thesis_id"), "shares": p.get("shares"), "avg_price": p.get("avg_price"),
                "stop": stop, "close": close, "price_date": px.get("date"), "account_type": p.get("account_type"),
                "flags": [], "events": [], "ownership": {}, "earnings": None, "sources": []}
        if close is not None:
            item["sources"].append("gpw-archive")
            if p.get("avg_price"):
                item["pnl_pct"] = round((float(close) / float(p["avg_price"]) - 1) * 100, 2)
            if stop:
                dist = (float(close) - float(stop)) / float(close) * 100
                item["dist_to_stop_pct"] = round(dist, 2)
                if float(close) <= float(stop):
                    item["flags"].append("below_stop")
                elif dist < NEAR_STOP_PCT:
                    item["flags"].append("near_stop")
        if not stop:
            item["flags"].append("no_stop")
        if horizon:
            for e in events:
                if str(e.get("ticker") or "").upper() != t:
                    continue
                day = _d(e["date"])
                if as_of < day <= horizon:
                    n = sessions_until(as_of, day)
                    item["events"].append({"date": e["date"], "type": e.get("type"), "date_kind": e.get("date_kind"),
                                           "title": e.get("title"), "sessions_ahead": n, "source": "gpw-event-calendar"})
                    if n <= IMMINENT_SESSIONS:
                        item["flags"].append("event_imminent")
        sh = shorts.get(t)
        if sh:
            item["ownership"]["short_pct"] = sh.get("total_pct")
            item["ownership"]["short_crowded"] = bool(sh.get("crowded"))
            item["ownership"]["short_holders"] = sh.get("holders")
            item["ownership"]["short_change_20s_pp"] = sh.get("change_20s_pp")
            if sh.get("crowded"):
                item["flags"].append("crowded_short")
        ib = ins.get(t)
        if ib:
            item["ownership"]["insider_cluster_buy"] = bool(ib.get("cluster_buy"))
            item["ownership"]["insider_cluster_sell"] = bool(ib.get("cluster_sell"))
            item["ownership"]["insider_notices"] = ib.get("notices")
            if ib.get("cluster_sell"):
                item["flags"].append("insider_cluster_sell")
        if item["ownership"]:
            item["sources"].append("gpw-ownership")
        r = reac.get(t)
        if r:
            item["earnings"] = {"label": r.get("label"), "car_3d_pct": r.get("car_3d_pct"),
                                "reaction_date": r.get("reaction_date"), "report_type": r.get("report_type"),
                                "source": "gpw-earnings-reaction"}
            if r.get("label") in ("strong_negative",):
                item["flags"].append("earnings_strong_negative")
            item["sources"].append("gpw-earnings-reaction")
        if item["events"]:
            item["sources"].append("gpw-event-calendar")
        item["status"] = "review" if set(item["flags"]) - {"no_stop"} else "ok"
        items.append(item)
    order = {"below_stop": 0, "near_stop": 1, "earnings_strong_negative": 2, "event_imminent": 3,
             "insider_cluster_sell": 4, "crowded_short": 5, "no_stop": 6}
    items.sort(key=lambda i: (min([order.get(f, 9) for f in i["flags"]] or [9]),
                              i.get("dist_to_stop_pct") if i.get("dist_to_stop_pct") is not None else 1e9, i["ticker"]))
    return {"available": True, "items": items, "circuit_breaker": cb,
            "heat": exp.get("heat") if exp else None}


# --------------------------------------------------------------- watchlist
def watchlist_section(watch: Optional[Dict], prices: Dict, inputs: Dict, disabled: bool = False,
                      path: Optional[str] = None, lang: str = "pl") -> Dict:
    if watch is None:
        return {"available": False, "disabled": disabled, "items": [], "path": path}
    scr = screener_names(_data(inputs, "screener"), "candidates")
    scr.update(screener_names(_data(inputs, "screener"), "watchlist"))
    items, dropped = [], 0
    for it in watch.get("items") or []:
        if it.get("stage") == "dropped":
            dropped += 1
            continue
        if it.get("stage") == "entered":
            continue  # bought: shown under "Your positions" (journal)
        t = str(it.get("ticker")).upper()
        px = prices.get(t) or {}
        mk = it.get("market") if isinstance(it.get("market"), dict) else {}
        if not px and mk.get("close") is not None:
            px = {"close": mk["close"], "date": mk.get("date")}  # gpw-watchlist's own refresh
        close = px.get("close")
        pivot = it.get("pivot")
        stop = it.get("stop")
        row = {"ticker": t, "stage": it.get("stage"), "pivot": pivot, "stop": stop, "close": close,
               "price_date": px.get("date"), "alerts": _alerts(it, lang), "source": "gpw-watchlist"}
        d = (float(close) / float(pivot) - 1) * 100 if (close is not None and pivot) else None
        if d is not None:
            row["dist_to_pivot_pct"] = round(d, 2)
        if close is None:
            status = "no_price"
        elif stop and float(close) < float(stop):
            status = "invalidated"
        elif d is None:
            status = "no_pivot"
        else:
            if d > PIVOT_EXTENDED_PCT:
                status = "extended"
            elif d >= 0:
                status = "breakout"
            elif d >= -PIVOT_NEAR_PCT:
                status = "near_pivot"
            else:
                status = "below"
        row["status"] = status
        s = scr.get(t)
        if s:
            row["screener_state"] = (s.get("execution") or {}).get("state") or s.get("state")
            row["screener_score"] = s.get("score")
        items.append(row)
    rank = {"invalidated": 0, "breakout": 1, "near_pivot": 2, "extended": 4, "below": 5, "no_pivot": 6, "no_price": 7}

    def key(r):
        k = rank.get(r["status"], 9)
        if r["alerts"] and k > 2:
            k = 3  # names with an alert come right after the actionable zones
        return (k, abs(r.get("dist_to_pivot_pct") or 0.0), r["ticker"])

    items.sort(key=key)
    return {"available": True, "disabled": False, "items": items, "dropped": dropped, "path": watch.get("path"),
            "updated_at": watch.get("updated_at"), "error": watch.get("error")}


def _alerts(item: Dict, lang: str = "pl") -> List[str]:
    """Readable alerts of a watchlist item. gpw-watchlist alerts are dicts
    ``{code, severity, params, message?}``; ``low`` ones (52-week high,
    turnover surge...) are left out of the one-page brief."""
    raw = item.get("alerts")
    out: List[str] = []
    if isinstance(raw, list):
        for a in raw:
            if isinstance(a, str):
                out.append(a)
            elif isinstance(a, dict):
                if a.get("active") is False or a.get("triggered") is False or a.get("severity") == "low":
                    continue
                code = a.get("code") or a.get("kind") or a.get("type")
                txt = a.get("text") or a.get("message")
                if not txt and code:
                    txt = label("wl_alert", code, lang)
                    ev = (a.get("params") or {})
                    if code == "event_soon" and ev.get("event_date"):
                        txt += " {} {}".format(label("event", ev.get("event_type"), lang), ev["event_date"])
                out.append(str(txt or a))
    elif isinstance(raw, dict):
        out.extend(str(k) for k, v in raw.items() if v)
    return out


# --------------------------------------------------------------- signals
def _signal_scores(data: Optional[Dict]) -> Dict[Tuple[str, str], float]:
    out = {}
    for s in (data or {}).get("signals") or []:
        if isinstance(s, dict) and s.get("ticker") and s.get("kind") and s.get("score") is not None:
            out[(str(s["ticker"]).upper(), s["kind"])] = float(s["score"])
    return out


def signals_section(inputs: Dict, since: Optional[dt.date], held: Iterable[str], watched: Iterable[str],
                    lang: str) -> Dict:
    """New signals since ``since`` (exclusive), ranked (methodology.md)."""
    T = fmt.T(STRINGS, lang)
    held, watched = set(held), set(watched)
    out: List[Dict] = []

    def add(kind, ticker, strength, source, date, text, extra=None):
        t = str(ticker).upper()
        tier = 0 if t in held else (1 if t in watched else 2)
        out.append(dict({"kind": kind, "ticker": t, "strength": round(float(strength), 1), "source": source,
                         "date": date, "tier": tier, "text": text}, **(extra or {})))

    def new(day) -> bool:
        d = _d(day)
        return d is not None and (since is None or d > since)

    # 1) screener: VCP names not on the previous screen's watchlist
    cur = _data(inputs, "screener")
    if cur:
        prev = _data(inputs, "screener", "previous")
        prev_names = set(screener_names(prev)) if prev else set()
        day = (cur.get("summary") or {}).get("date") or (cur.get("data_provenance") or {}).get("as_of")
        for name, w in screener_names(cur).items():
            if name in prev_names:
                continue
            ex = w.get("execution") or {}
            add("vcp_new", name, w.get("score") or 0.0, "gpw-trend-screener", day,
                T("s_vcp_new", t=name, sc=_f(w.get("score"), 0, lang), band=w.get("band") or "—",
                  st=label("scr_state", ex.get("state"), lang), p=_f((w.get("vcp") or {}).get("pivot"), 2, lang)),
                {"first_screen": prev is None, "pivot": (w.get("vcp") or {}).get("pivot"), "state": ex.get("state")})
    # 2) insiders
    ins = _data(inputs, "insiders")
    if ins:
        scores = _signal_scores(ins)
        for t, b in insiders_by_ticker(inputs).items():
            if not new(b.get("last_date")):
                continue
            for flag, kind in (("cluster_buy", "insider_cluster_buy"), ("cluster_sell", "insider_cluster_sell")):
                if not b.get(flag):
                    continue
                if kind == "insider_cluster_sell" and t not in held and t not in watched:
                    continue  # a sell cluster matters for names the user owns or watches
                n = b.get("max_distinct_buyers_30d" if flag == "cluster_buy" else "max_distinct_sellers_30d") or \
                    b.get("distinct_insiders")
                add(kind, t, scores.get((t, kind), DEFAULT_STRENGTH[kind]), "gpw-ownership", b.get("last_date"),
                    T("s_" + kind, t=t, n=n if n is not None else "—", d=b.get("last_date")),
                    {"basis": b.get("basis")})
    # 3) shorts: new positions in the window, crowded flag flips vs previous report
    sh = _data(inputs, "shorts")
    if sh:
        scores = _signal_scores(sh)
        for e in (sh.get("shorts") or {}).get("events") or []:
            if isinstance(e, dict) and e.get("type") == "new_position" and e.get("ticker") and new(e.get("date")):
                t = str(e["ticker"]).upper()
                add("short_new_position", t, scores.get((t, "short_new"), DEFAULT_STRENGTH["short_new_position"]),
                    "gpw-ownership", e.get("date"),
                    T("s_short_new_position", t=t, h=e.get("holder") or "—", p=_f(e.get("pct"), 2, lang), d=e.get("date")))
        prevmap = shorts_by_ticker(inputs, "previous") if _data(inputs, "shorts", "previous") else None
        if prevmap is not None:
            curmap = shorts_by_ticker(inputs)
            day = sh.get("as_of")
            for t, b in curmap.items():
                was = bool((prevmap.get(t) or {}).get("crowded"))
                if b.get("crowded") and not was:
                    add("short_newly_crowded", t, scores.get((t, "short_crowded"), DEFAULT_STRENGTH["short_newly_crowded"]),
                        "gpw-ownership", day, T("s_short_newly_crowded", t=t, p=_f(b.get("total_pct"), 2, lang),
                                                h=b.get("holders") or "—"))
            for t, b in prevmap.items():
                if b.get("crowded") and not (curmap.get(t) or {}).get("crowded"):
                    add("short_no_longer_crowded", t, DEFAULT_STRENGTH["short_no_longer_crowded"], "gpw-ownership", day,
                        T("s_short_no_longer_crowded", t=t, p=_f((curmap.get(t) or {}).get("total_pct"), 2, lang)))
    # 4) strong earnings reactions (the skill's own signals[] = liquid, strong)
    er = _data(inputs, "earnings")
    if er:
        reac = latest_reactions(inputs)
        for s in er.get("signals") or []:
            if not isinstance(s, dict) or s.get("kind") not in ("earnings_strong_negative", "earnings_strong_positive"):
                continue
            if not new(s.get("date")):
                continue
            t = str(s.get("ticker")).upper()
            car = (s.get("tags") or {}).get("car_3d_pct")
            if car is None:
                car = (reac.get(t) or {}).get("car_3d_pct")
            add(s["kind"], t, s.get("score") or 0.0, "gpw-earnings-reaction", s.get("date"),
                T("s_" + s["kind"], t=t, car=_pct(car, lang), d=s.get("date")), {"car_3d_pct": car})
    # de-duplicate (same ticker+kind) keeping the strongest
    best: Dict[Tuple[str, str], Dict] = {}
    for s in out:
        k = (s["ticker"], s["kind"])
        if k not in best or s["strength"] > best[k]["strength"]:
            best[k] = s
    ranked = sorted(best.values(), key=rank_signal_key)
    for i, s in enumerate(ranked, 1):
        s["rank"] = i
    return {"since": since.isoformat() if since else None, "items": ranked}


def rank_signal_key(s: Dict):
    kind_idx = SIGNAL_KIND_ORDER.index(s["kind"]) if s["kind"] in SIGNAL_KIND_ORDER else len(SIGNAL_KIND_ORDER)
    return (s["tier"], -s["strength"], kind_idx, s["ticker"])


# --------------------------------------------------------------- calendar
def calendar_section(inputs: Dict, as_of: Optional[dt.date], n_sessions: int, held: Iterable[str],
                     watched: Iterable[str]) -> Dict:
    if not as_of:
        return {"start": None, "end": None, "sessions": n_sessions, "items": []}
    held, watched = set(held), set(watched)
    end = nth_session_after(as_of, n_sessions)
    items = []
    for e in calendar_events(inputs):
        day = _d(e["date"])
        if not (as_of < day <= end):
            continue
        t = str(e.get("ticker") or "").upper() or None
        rel = "held" if t in held else ("watch" if t in watched else None)
        if e.get("importance") == "low" and not rel:
            continue
        items.append({"date": e["date"], "ticker": t, "type": e.get("type"), "date_kind": e.get("date_kind"),
                      "importance": e.get("importance"), "title": e.get("title"), "relevance": rel,
                      "source": "gpw-event-calendar", "source_url": e.get("source_url")})
    items.sort(key=lambda e: (e["date"], 0 if e["relevance"] == "held" else (1 if e["relevance"] == "watch" else 2),
                              {"high": 0, "medium": 1, "low": 2}.get(e.get("importance"), 3), e.get("ticker") or ""))
    return {"start": nth_session_after(as_of, 1).isoformat(), "end": end.isoformat(), "sessions": n_sessions,
            "items": items}


# --------------------------------------------------------------- weekly
def weekly_section(inputs: Dict, cur: Dict, prev: Dict, positions: Optional[List[Dict]], as_of: Optional[dt.date],
                   lang: str) -> Dict:
    out: Dict = {}
    mac = _data(inputs, "macro")
    if mac:
        m = mac.get("macro") or {}
        out["macro"] = {"composite": (m.get("state") or {}).get("composite"), "score": m.get("score"),
                        "confidence": m.get("confidence"), "regime_label": (m.get("state") or {}).get("regime_label"),
                        "failed_sources": m.get("failed_sources") or [],
                        "previous_composite": prev.get("macro_composite"),
                        "watch": [w.get("text") for w in (mac.get("watch") or []) if isinstance(w, dict) and w.get("text")][:2],
                        "source": "gpw-macro-regime", "as_of": (_rec(inputs, "macro") or {}).get("as_of")}
    pc = _data(inputs, "portfolio")
    if pc:
        tot, risk, conc = pc.get("totals") or {}, pc.get("risk") or {}, pc.get("concentration") or {}
        out["portfolio"] = {"total_value": tot.get("total_value"), "pnl_pct": tot.get("pnl_pct"),
                            "var_pct": risk.get("var_pct"), "var_pln": risk.get("var_pln"),
                            "effective_positions": conc.get("effective_positions"), "review_count": pc.get("review_count"),
                            "flags": [f.get("code") for f in (pc.get("flags") or []) if isinstance(f, dict)],
                            "source": "gpw-portfolio-check", "as_of": (_rec(inputs, "portfolio") or {}).get("as_of")}
    rl = _data(inputs, "risk")
    if rl:
        sc = [s for s in ((rl.get("stress") or {}).get("scenarios") or []) if isinstance(s, dict)
              and not s.get("skipped") and s.get("pln_loss") is not None]
        worst = max(sc, key=lambda s: float(s["pln_loss"])) if sc else None
        hedge = rl.get("hedge") if isinstance(rl.get("hedge"), dict) else None
        tol = (rl.get("stress") or {}).get("tolerance")
        out["risk"] = {"worst": {"id": worst.get("id"), "label": worst.get("label"),
                                 "portfolio_return_pct": worst.get("portfolio_return_pct"),
                                 "pln_loss": worst.get("pln_loss")} if worst else None,
                       "tolerance": tol if isinstance(tol, dict) else None,
                       "hedge": {"contracts": hedge.get("contracts"), "beta": hedge.get("beta")} if hedge else None,
                       "source": "gpw-risk-lab", "as_of": (_rec(inputs, "risk") or {}).get("as_of")}
    pm = _data(inputs, "postmortem")
    if pm:
        out["postmortem"] = {"signals_total": pm.get("signals_total"), "signals_evaluated": pm.get("signals_evaluated"),
                             "pending": pm.get("pending"), "next_maturity": pm.get("next_maturity"),
                             "demo": bool(pm.get("demo")),
                             "headline": [{k: h.get(k) for k in ("source", "kind", "n", "hit_rate", "mean_excess_pct",
                                                                  "horizon")}
                                          for h in (pm.get("headline") or [])
                                          if isinstance(h, dict) and h.get("n")][:3],
                             "feedback_summary": ((pm.get("feedback") or {}).get("summary") or {}).get(lang),
                             "source": "gpw-signal-postmortem", "as_of": (_rec(inputs, "postmortem") or {}).get("as_of")}
    dv = _data(inputs, "dividend")
    if dv and as_of:
        end = nth_session_after(as_of, 22)  # ~one calendar month of sessions
        ups = [u for u in (dv.get("upcoming") or []) if isinstance(u, dict) and _d(u.get("ex_date"))
               and as_of < _d(u["ex_date"]) <= end]
        out["dividend"] = {"window_sessions": 22, "upcoming": [{k: u.get(k) for k in ("ticker", "ex_date", "dps", "date_kind")}
                                                                for u in ups], "source": "gpw-dividend-analyzer",
                           "as_of": (_rec(inputs, "dividend") or {}).get("as_of")}
    tr = _rec(inputs, "trade_review")
    out["trade_review"] = {"file": tr.get("file"), "path": tr.get("path"), "as_of": tr.get("as_of"),
                           "source": "gpw-trade-review"} if tr else None
    out["has_positions"] = bool(positions)
    return out


# --------------------------------------------------------------- freshness
def freshness_section(inputs: Dict, weekly: bool, has_positions: bool, journal_used: bool) -> List[Dict]:
    rows = []
    for key, slot in inputs.items():
        spec = slot["spec"]
        cad = spec["cadence"]
        if cad == "optional" and not slot.get("current"):
            continue
        if key == "previous_brief":
            continue
        cur = slot.get("current")
        needed = cad == "daily" or (cad == "weekly" and weekly)
        if key in ("portfolio", "risk", "dividend") and not has_positions:
            needed = False
        if cur:
            status = "stale" if cur.get("stale") else "ok"
        else:
            status = "missing" if needed else "not_needed"
        if status == "not_needed":
            continue
        rows.append({"input": key, "skill": spec["skill"], "mode": (cur or {}).get("mode"), "file": (cur or {}).get("file"),
                     "path": (cur or {}).get("path"), "as_of": (cur or {}).get("as_of"),
                     "sessions_behind": (cur or {}).get("sessions_behind"),
                     "max_sessions_behind": (cur or {}).get("max_sessions_behind"), "stale": status == "stale",
                     "status": status, "cadence": cad,
                     "command": None if status == "ok" else "cd ../{} && {}".format(spec["skill"], spec["cmd"]),
                     "warning": (cur or {}).get("warning")})
    return rows


# --------------------------------------------------------------- attention
def attention(cur: Dict, chg: List[Dict], pos: Dict, wl: Dict, fresh: List[Dict], lang: str) -> List[Dict]:
    """Action items, most urgent first (rules: methodology.md)."""
    T = fmt.T(STRINGS, lang)
    out: List[Dict] = []

    def add(sev, kind, text, source, ticker=None):
        out.append({"severity": sev, "kind": kind, "ticker": ticker, "text": text, "source": source})

    cb = cur.get("circuit_breaker")
    if cb == "pause":
        until = ((pos.get("circuit_breaker") or {}).get("pause_until"))
        add("critical", "circuit_breaker_pause", T("a_pause", u=T("pause_until", d=until) if until else ""),
            "gpw-exposure-coach")
    elif cb == "reduce":
        add("high", "circuit_breaker_reduce", T("a_reduce", r=_f(cur.get("max_risk_per_trade_pct"), 2, lang)),
            "gpw-exposure-coach")
    for c in chg:
        if c["field"] == "regime_state" and c["direction"] == "worse":
            add("high", "regime_downgrade", T("a_regime_down", a=label("regime", c["from"], lang),
                                               b=label("regime", c["to"], lang),
                                               c=_f(cur.get("exposure_ceiling_pct"), 0, lang)), c["source"])
        if c["field"] == "posture" and c["direction"] == "worse":
            add("high", "posture_downgrade", T("a_posture_down", a=label("posture", c["from"], lang),
                                                b=label("posture", c["to"], lang)), c["source"])
    for p in pos.get("items") or []:
        t = p["ticker"]
        if "below_stop" in p["flags"]:
            add("critical", "below_stop", T("a_below_stop", t=t, c=_f(p["close"], 2, lang), s=_f(p["stop"], 2, lang)),
                "gpw-trade-journal", t)
        elif "near_stop" in p["flags"]:
            add("high", "near_stop", T("a_near_stop", t=t, d=_f(p.get("dist_to_stop_pct"), 1, lang),
                                       s=_f(p["stop"], 2, lang)), "gpw-trade-journal", t)
        if "earnings_strong_negative" in p["flags"]:
            e = p["earnings"] or {}
            add("high", "earnings_strong_negative", T("a_earn_neg", t=t, lab=label("earn", e.get("label"), lang),
                                                      car=_pct(e.get("car_3d_pct"), lang)), "gpw-earnings-reaction", t)
        for e in p["events"]:
            if e["sessions_ahead"] <= IMMINENT_SESSIONS:
                add("medium", "event_imminent", T("a_event", t=t, ev=label("event", e["type"], lang), d=e["date"],
                                                  k=label("date_kind", e.get("date_kind"), lang), n=e["sessions_ahead"]),
                    "gpw-event-calendar", t)
        if "crowded_short" in p["flags"]:
            o = p["ownership"]
            add("medium", "crowded_short", T("a_crowded", t=t, p=_f(o.get("short_pct"), 2, lang),
                                             h=o.get("short_holders") or "—"), "gpw-ownership", t)
        if "insider_cluster_sell" in p["flags"]:
            add("medium", "insider_cluster_sell", T("a_cluster_sell", t=t), "gpw-ownership", t)
    for w in wl.get("items") or []:
        if w["status"] == "invalidated":
            add("medium", "watchlist_invalidated", T("a_wl_invalid", t=w["ticker"], c=_f(w["close"], 2, lang),
                                                     s=_f(w["stop"], 2, lang)), "gpw-watchlist", w["ticker"])
        elif w["status"] in ("breakout", "near_pivot"):
            add("medium", "watchlist_" + w["status"], T("a_wl_pivot", t=w["ticker"], d=_f(w.get("dist_to_pivot_pct"), 1, lang, True),
                                                        p=_f(w["pivot"], 2, lang), st=label("wl_status", w["status"], lang)),
                "gpw-watchlist", w["ticker"])
    bad = [r for r in fresh if r["status"] in ("stale", "missing") and r["cadence"] == "daily"
           and r["input"] in ("regime", "exposure", "breadth")]
    if bad:
        add("medium", "inputs_stale", T("a_stale", items=", ".join("{} ({})".format(_src(r["skill"]), T("st_" + r["status"]))
                                                                for r in bad)), "gpw-daily-brief")
    kind_order = ["below_stop", "circuit_breaker_pause", "regime_downgrade", "circuit_breaker_reduce", "posture_downgrade",
                  "earnings_strong_negative", "near_stop", "event_imminent", "insider_cluster_sell", "crowded_short",
                  "watchlist_invalidated", "watchlist_breakout", "watchlist_near_pivot", "inputs_stale"]
    out.sort(key=lambda a: (SEVERITY_RANK[a["severity"]], kind_order.index(a["kind"]) if a["kind"] in kind_order else 99,
                            a.get("ticker") or ""))
    return out


# --------------------------------------------------------------- assemble
def market_as_of(inputs: Dict) -> Optional[dt.date]:
    """Business date of the brief: the newest market session among the core
    inputs (regime, breadth, exposure, sector, screener), else any input."""
    core = [(_rec(inputs, k) or {}).get("as_of") for k in ("regime", "breadth", "exposure", "sector", "screener")]
    core = [c for c in core if c]
    if not core:
        core = [s["current"]["as_of"] for k, s in inputs.items() if s.get("current") and s["current"].get("as_of")
                and k != "previous_brief"]
    return _d(max(core)) if core else None


def build_brief(inputs: Dict, *, positions: Optional[List[Dict]], watch: Optional[Dict], prices: Dict,
                weekly: bool = False, lang: str = "pl", new_sessions: int = 3, calendar_sessions: int = 5,
                position_event_sessions: int = 10, journal_used: bool = True, watch_disabled: bool = False,
                watch_path: Optional[str] = None) -> Dict:
    as_of = market_as_of(inputs)
    prev_brief_rec = _rec(inputs, "previous_brief")
    prev_brief = prev_brief_rec.get("data") if prev_brief_rec else None
    cur = snapshot(inputs)
    prev, origin = baseline_snapshot(inputs, prev_brief)
    prev_dates = {}
    for key, slot in inputs.items():
        if slot.get("previous"):
            prev_dates.setdefault(slot["spec"]["skill"], slot["previous"].get("as_of"))
    if prev_brief_rec:
        for k in origin:
            if origin[k] == "brief":
                prev_dates.setdefault("brief", prev_brief_rec.get("as_of"))
    chg = changes(cur, prev, inputs, prev_dates, lang)
    # window for "new" signals: since the previous brief, else the last N sessions
    if prev_brief_rec and prev_brief_rec.get("as_of"):
        since = _d(prev_brief_rec["as_of"])
    elif as_of:
        since = nth_session_before(as_of, new_sessions)
    else:
        since = None
    held = [str(p.get("ticker")).upper() for p in (positions or [])]
    watched = [str(i.get("ticker")).upper() for i in ((watch or {}).get("items") or [])
               if i.get("stage") not in ("dropped", "entered")]
    pos = positions_section(positions, prices, inputs, as_of, position_event_sessions, lang)
    wl = watchlist_section(watch, prices, inputs, watch_disabled, watch_path, lang)
    sig = signals_section(inputs, since, held, watched, lang)
    cal = calendar_section(inputs, as_of, calendar_sessions, held, watched)
    fresh = freshness_section(inputs, weekly, bool(positions), journal_used)
    att = attention(cur, chg, pos, wl, fresh, lang)
    brief = {
        "skill": SKILL, "version": VERSION, "lang": lang, "mode": "weekly" if weekly else "daily",
        "as_of": as_of.isoformat() if as_of else None,
        "baseline": {"previous_brief": prev_brief_rec.get("file") if prev_brief_rec else None,
                     "previous_brief_as_of": prev_brief_rec.get("as_of") if prev_brief_rec else None,
                     "previous_reports": {k: v for k, v in prev_dates.items() if k != "brief"},
                     "new_signals_since": sig["since"]},
        "headline": headline(cur, prev, inputs, prev_dates, lang),
        "attention": att,
        "changes": chg,
        "first_report": not any(slot.get("previous") for k, slot in inputs.items() if k != "previous_brief") and not prev_brief,
        "positions": pos,
        "watchlist": wl,
        "signals": sig,
        "calendar": cal,
        "weekly": weekly_section(inputs, cur, prev, positions, as_of, lang) if weekly else None,
        "inputs": fresh,
        "snapshot": cur,
        "ranking_rules": {
            "attention": "severity (critical > high > medium) -> fixed kind order -> ticker",
            "signals": "tier (held=0, watchlist=1, other=2) -> strength desc (source score 0-100) -> kind order -> ticker",
            "watchlist": "invalidated > breakout (<=5% above pivot) > near pivot (<=5% below) > with alert > extended > far",
            "positions": "below stop > near stop (<3%) > strong negative earnings > event within 2 sessions > insider sell cluster > crowded short",
            "signal_kind_order": SIGNAL_KIND_ORDER,
        },
    }
    return brief


# --------------------------------------------------------------- markdown
def _cap(items: List, n: int) -> Tuple[List, int]:
    return items[:n], max(0, len(items) - n)


def select_signals(items: List[Dict], cap: int, per_source: Optional[int]) -> Tuple[List[Dict], int]:
    """Top ``cap`` signals in rank order, at most ``per_source`` per source skill."""
    chosen, per = [], {}
    for s in items:
        if len(chosen) >= cap:
            break
        if per_source is not None and per.get(s["source"], 0) >= per_source:
            continue
        per[s["source"]] = per.get(s["source"], 0) + 1
        chosen.append(s)
    return chosen, len(items) - len(chosen)


def render_markdown(brief: Dict, lang: str = "pl", full: bool = False, price_date: Optional[str] = None,
                    disclaimer: bool = True) -> str:
    T = fmt.T(STRINGS, lang)
    caps = CAPS_FULL if full else CAPS
    L: List[str] = []
    title = T("title_weekly" if brief["mode"] == "weekly" else "title", date=brief.get("as_of") or "?")
    L.append("# " + title)
    L.append("")
    stale = [r for r in brief["inputs"] if r["status"] == "stale"]
    missing = [r for r in brief["inputs"] if r["status"] == "missing"]
    if stale:
        L.append("> " + T("stale_top", items=", ".join("{} ({})".format(_src(r["skill"]), r["as_of"]) for r in stale)))
    if missing:
        L.append("> " + T("missing_top", items=", ".join(_src(r["skill"]) + (" " + r["input"].split("_")[-1]
                                                                             if r["input"] in ("shorts", "insiders") else "")
                                                         for r in missing)))
    if stale or missing:
        L.append("")
    # 1 headline
    L.append("## " + T("h_headline"))
    for h in brief["headline"]:
        L.append("- " + h["text"])
    L.append("")
    # attention
    L.append("**" + T("h_attention") + ":**")
    att, more = _cap(brief["attention"], caps["attention"])
    if att:
        for i, a in enumerate(att, 1):
            L.append("{}. {} [{}]".format(i, a["text"], _src(a["source"])))
        if more:
            L.append(T("more", n=more))
    else:
        L.append(T("nothing_urgent"))
    L.append("")
    # 2 changes
    L.append("## " + T("h_changes"))
    if brief["first_report"]:
        L.append(T("first"))
    elif not brief["changes"]:
        since = brief["baseline"].get("previous_brief_as_of") or max(
            (v for v in brief["baseline"]["previous_reports"].values() if v), default="?")
        L.append(T("no_changes", date=since))
    else:
        ch, more = _cap(brief["changes"], caps["changes"])
        for c in ch:
            L.append("- {} [{}{}]".format(c["text"], _src(c["source"]), ", vs " + c["since"] if c.get("since") else ""))
        if more:
            L.append(T("more", n=more))
    L.append("")
    # 3 positions
    pos = brief["positions"]
    L.append("## " + T("h_positions"))
    if not pos["available"]:
        L.append(T("journal_off"))
    elif not pos["items"]:
        L.append(T("no_journal"))
    else:
        L.append(T("pos_head"))
        L.append("|---|---:|---:|---:|---:|---|")
        rows, more = _cap(pos["items"], caps["positions"])
        for p in rows:
            notes = []
            for f in p["flags"]:
                if f == "below_stop":
                    notes.append("**" + T("pf_below") + "**")
                elif f == "near_stop":
                    notes.append(T("pf_near"))
                elif f == "no_stop":
                    notes.append(T("pf_no_stop"))
            for e in p["events"][:2]:
                notes.append(T("pf_events", ev=label("event", e["type"], lang), d=e["date"][5:]))
            o = p["ownership"]
            if o.get("short_pct") is not None:
                notes.append(T("pf_crowded" if o.get("short_crowded") else "pf_short", p=_f(o["short_pct"], 2, lang)))
            if o.get("insider_cluster_sell"):
                notes.append(T("pf_csell"))
            if o.get("insider_cluster_buy"):
                notes.append(T("pf_cbuy"))
            if p.get("earnings") and p["earnings"].get("label") in ("strong_negative", "negative", "strong_positive"):
                notes.append(T("pf_earn", lab=label("earn", p["earnings"]["label"], lang),
                               car=_pct(p["earnings"].get("car_3d_pct"), lang)))
            L.append("| {} | {} | {} | {} | {} | {} |".format(
                p["ticker"], _f(p.get("close"), 2, lang), _f(p.get("stop"), 2, lang),
                _pct(p.get("dist_to_stop_pct"), lang, 1, False), _pct(p.get("pnl_pct"), lang),
                "; ".join(notes) or "—"))
        if more:
            L.append(T("more", n=more))
        cb = pos.get("circuit_breaker")
        if cb and cb.get("state"):
            L.append("")
            L.append(T("breaker_line", cb=label("breaker", cb["state"], lang),
                       extra=T("pause_until", d=cb["pause_until"]) if cb.get("pause_until") else ""))
    L.append("")
    # 4 watchlist
    wl = brief["watchlist"]
    L.append("## " + T("h_watchlist"))
    if not wl["available"]:
        L.append(T("watchlist_off") if wl.get("disabled") else
                 T("no_watchlist", path=wl.get("path") or "~/.gpw-trading-skills/watchlist.json"))
    else:
        show = [w for w in wl["items"] if full or w["status"] in ("invalidated", "breakout", "near_pivot") or w["alerts"]]
        if not show:
            L.append(T("wl_quiet", n=len(wl["items"])))
        else:
            L.append(T("wl_head"))
            L.append("|---|---|---:|---:|---:|---:|---|")
            rows, more = _cap(show, caps["watchlist"])
            for w in rows:
                st = label("wl_status", w["status"], lang)
                if w["alerts"]:
                    st += " · ⚑ " + "; ".join(w["alerts"][:2])
                L.append("| {} | {} | {} | {} | {} | {} | {} |".format(
                    w["ticker"], w.get("stage") or "—", _f(w.get("close"), 2, lang), _f(w.get("pivot"), 2, lang),
                    _pct(w.get("dist_to_pivot_pct"), lang), _f(w.get("stop"), 2, lang), st))
            if more:
                L.append(T("more", n=more))
            rest = len(wl["items"]) - len(show)
            if rest > 0 and not full:
                L.append(T("wl_rest", n=rest))
    L.append("")
    # 5 signals
    sig = brief["signals"]
    L.append("## " + T("h_signals"))
    if not sig["items"]:
        L.append(T("no_signals", date=sig.get("since") or "?"))
    else:
        rows, more = select_signals(sig["items"], caps["signals"], None if full else PER_SOURCE_CAP)
        for i, s in enumerate(rows, 1):
            tag = {0: " · " + T("tag_held"), 1: " · " + T("tag_watch")}.get(s["tier"], "")
            L.append("{}. {} [{}{}]".format(i, s["text"], _src(s["source"]), tag))
        if more:
            L.append(T("more", n=more))
    L.append("")
    # 6 calendar
    cal = brief["calendar"]
    L.append("## " + T("h_calendar", n=cal.get("sessions", 5)))
    if not cal["items"]:
        L.append(T("no_events"))
    else:
        rows, more = _cap(cal["items"], caps["calendar"])
        for e in rows:
            tag = {"held": " · **" + T("tag_held") + "**", "watch": " · " + T("tag_watch")}.get(e["relevance"], "")
            who = (e["ticker"] + ": ") if e.get("ticker") else ""
            L.append("- {} {}{} ({}{}) [event-calendar]".format(
                e["date"], who, label("event", e["type"], lang) if not who else (e.get("title") or label("event", e["type"], lang))[:70],
                label("date_kind", e.get("date_kind"), lang), tag))
        if more:
            L.append(T("more", n=more))
    L.append("")
    # weekly
    if brief.get("weekly") is not None:
        L.extend(_render_weekly(brief, lang))
    # 7 freshness
    L.append("## " + T("h_fresh"))
    rows = brief["inputs"]
    bad = [r for r in rows if r["status"] != "ok"]
    if not full and not bad:
        dates = sorted(set(r["as_of"] for r in rows if r.get("as_of")))
        L.append(T("fresh_ok", n=len(rows), dates=", ".join(dates)))
    else:
        L.append(T("fresh_head"))
        L.append("|---|---|---|---:|---|---|")
        for r in (rows if full else bad):
            L.append("| {} | {} | {} | {} | {} | {} |".format(
                _src(r["skill"]) + (" " + r["mode"] if r.get("mode") and r["skill"] in ("gpw-ownership", "gpw-event-calendar") else ""),
                r.get("file") or "—", r.get("as_of") or "—",
                r["sessions_behind"] if r.get("sessions_behind") is not None else "—",
                T("st_" + r["status"]), "`{}`".format(r["command"]) if r.get("command") else "—"))
        if bad:
            L.append("")
            L.append(T("fresh_hint"))
        if not full:
            ok = [r for r in rows if r["status"] == "ok"]
            if ok:
                L.append(T("fresh_rest", n=len(ok), dates=", ".join(sorted(set(r["as_of"] for r in ok if r.get("as_of"))))))
    L.append("")
    L.append("_" + T("rules") + "_")
    L.append("")
    # sources
    L.append("## " + T("h_sources"))
    L.append(T("footer_sources") + " " + "; ".join("{} `{}` ({})".format(_src(r["skill"]), r["file"], r["as_of"])
                                                  for r in rows if r.get("file")))
    if price_date:
        L.append(T("prices_src", d=price_date))
    if disclaimer:
        L.append("")
        L.append("_" + fmt.DISCLAIMER[lang] + "_")
    return "\n".join(L) + "\n"


def _render_weekly(brief: Dict, lang: str) -> List[str]:
    T = fmt.T(STRINGS, lang)
    w = brief["weekly"]
    L = ["## " + T("h_weekly")]
    m = w.get("macro")
    if m:
        chg = ""
        if m.get("previous_composite") and m["previous_composite"] != m.get("composite"):
            chg = T("was", v=label("macro", m["previous_composite"], lang))
        L.append("- " + T("w_macro", comp=label("macro", m.get("composite"), lang), score=_f(m.get("score"), 2, lang, True),
                          conf=label("conf", m.get("confidence"), lang), chg=chg))
        for t in m.get("watch") or []:
            L.append(T("w_macro_watch", t=t))
    else:
        L.append("- " + T("w_missing", name="Makro" if lang == "pl" else "Macro", why=T("why_missing")))
    p = w.get("portfolio")
    if p:
        L.append("- " + T("w_portfolio", value=fmt.pln(p.get("total_value"), 0, lang), pnl=_pct(p.get("pnl_pct"), lang),
                          var=fmt.pln(p.get("var_pln"), 0, lang), varp=_pct(p.get("var_pct"), lang, 2, False),
                          eff=_f(p.get("effective_positions"), 1, lang), rev=p.get("review_count") if p.get("review_count") is not None else "—"))
    elif w.get("has_positions"):
        L.append("- " + T("w_missing", name="Portfel" if lang == "pl" else "Portfolio", why=T("why_missing")))
    r = w.get("risk")
    if r and r.get("worst"):
        wst = r["worst"]
        lab = wst.get("label")
        lab = lab.get(lang, lab.get("en")) if isinstance(lab, dict) else (lab or wst.get("id"))
        tol = r.get("tolerance") or {}
        tol_txt = ""
        if tol.get("verdict") or tol.get("status"):
            tol_txt = " · " + str(tol.get("verdict") or tol.get("status"))
        L.append("- " + T("w_risk", label=lab, ret=_pct(wst.get("portfolio_return_pct"), lang),
                          loss=fmt.pln(wst.get("pln_loss"), 0, lang), tol=tol_txt))
        if r.get("hedge"):
            L.append(T("w_hedge", c=r["hedge"].get("contracts"), b=_f(r["hedge"].get("beta"), 2, lang)))
    pm = w.get("postmortem")
    if pm:
        L.append("- " + T("w_pm", tot=pm.get("signals_total"), ev=pm.get("signals_evaluated"), pend=pm.get("pending"),
                          nm=pm.get("next_maturity") or "—"))
        if pm.get("demo"):
            L.append(T("w_pm_demo"))
        for h in pm.get("headline") or []:
            hr = h.get("hit_rate")
            L.append(T("w_pm_line", src=_src(h.get("source") or "?"), kind=h.get("kind") or "?", n=h.get("n"),
                       hit=_pct(hr * 100 if hr is not None else None, lang, 0, False),
                       ex=_f(h.get("mean_excess_pct"), 2, lang, True), h=h.get("horizon") or 20))
    else:
        L.append("- " + T("w_missing", name="Post-mortem", why=T("why_missing")))
    dv = w.get("dividend")
    if dv and dv.get("upcoming"):
        L.append("- " + T("w_div", n=dv["window_sessions"], items=", ".join(
            "{} {} ({}, {})".format(u["ticker"], u["ex_date"], _f(u.get("dps"), 2, lang), label("date_kind", u.get("date_kind"), lang))
            for u in dv["upcoming"])))
    elif dv:
        L.append("- " + T("w_div_none", n=dv["window_sessions"]))
    tr = w.get("trade_review")
    if tr:
        L.append("- " + T("w_review", file=tr["file"], date=tr.get("as_of") or "—"))
    else:
        month = (brief.get("as_of") or "????-??")[:7]
        L.append("- " + T("w_review_missing", m=month))
    L.append("")
    return L
