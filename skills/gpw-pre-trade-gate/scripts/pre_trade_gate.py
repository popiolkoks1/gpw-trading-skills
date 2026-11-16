#!/usr/bin/env python3
"""GPW Pre-Trade Gate - CLI.

Fail-closed checklist run BEFORE a long entry on GPW: stop, risk incl. fees,
position size, liquidity, portfolio heat, sector concentration, written thesis
with kill criteria, reward/risk, circuit breaker, market regime, exposure
limits and data freshness. Verdict GO / GO WITH CONDITIONS / NO-GO.

    python3 scripts/pre_trade_gate.py --ticker PKOBP --entry 120 --stop 113 --shares 30 --account-size 50000
    python3 scripts/pre_trade_gate.py --sizer-json gpw_position_size_2026-11-15_101500.json --thesis-id th_pkobp_...

Never places orders.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import brokers, constituents, fundamentals, gpw_archive, journal_io, panel, plcal, provenance, reports  # noqa: E402
from gpwlib.constituents import SECTOR_INDICES  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct, pln  # noqa: E402
from gpwlib.indicators import median  # noqa: E402

import pretrade_context_checks as cc  # noqa: E402
import pretrade_gate_engine as ge  # noqa: E402

VERSION = "1.1.0"
PREFIX = "gpw_pre_trade_gate"
MIN_OVERRIDE_CHARS = 15  # a real sentence, not "ok" / "yes"

STRINGS = {
    "title": {"pl": "Bramka przed transakcją (pre-trade gate)", "en": "Pre-Trade Gate"},
    "verdict": {"pl": "Werdykt", "en": "Verdict"},
    "v_GO": {"pl": "GO – wszystkie warunki spełnione", "en": "GO – all conditions met"},
    "v_GO WITH CONDITIONS": {"pl": "GO WITH CONDITIONS – możliwe, ale z zastrzeżeniami", "en": "GO WITH CONDITIONS – possible, with caveats"},
    "v_NO-GO": {"pl": "NO-GO – co najmniej jeden warunek blokujący", "en": "NO-GO – at least one blocking condition"},
    "never": {"pl": "Bramka niczego nie kupuje i nie składa zleceń – decyzja i zlecenie należą do Ciebie.",
              "en": "The gate does not buy anything or place orders – the decision and the order are yours."},
    "stale": {"pl": "⚠️ UWAGA: dane cenowe są nieaktualne o {n} sesji.", "en": "⚠️ WARNING: price data is {n} session(s) old."},
    "no_market": {"pl": "⚠️ UWAGA: brak danych rynkowych – płynność i świeżość niezweryfikowane.",
                  "en": "⚠️ WARNING: no market data – liquidity and freshness unverified."},
    "blockers": {"pl": "Blokery (FAIL)", "en": "Blockers (FAIL)"},
    "conditions": {"pl": "Warunki (WARN)", "en": "Conditions (WARN)"},
    "trade": {"pl": "Transakcja", "en": "Trade"},
    "k_ticker": {"pl": "Walor", "en": "Ticker"}, "k_entry": {"pl": "Wejście", "en": "Entry"},
    "k_stop": {"pl": "Stop", "en": "Stop"}, "k_target": {"pl": "Cel", "en": "Target"},
    "k_shares": {"pl": "Liczba akcji", "en": "Shares"}, "k_value": {"pl": "Wartość pozycji", "en": "Position value"},
    "k_account": {"pl": "Wielkość rachunku", "en": "Account size"}, "k_acct_type": {"pl": "Rachunek", "en": "Account type"},
    "k_fees": {"pl": "Prowizje (kupno + sprzedaż na stopie)", "en": "Fees (buy + sell at stop)"},
    "k_risk": {"pl": "Ryzyko z prowizjami", "en": "Risk incl. fees"},
    "k_rr": {"pl": "Zysk/ryzyko (netto)", "en": "Reward/risk (net)"},
    "k_broker": {"pl": "Taryfa", "en": "Tariff"},
    "k_source": {"pl": "Źródło parametrów", "en": "Parameters from"},
    "checks": {"pl": "Lista kontrolna", "en": "Checklist"},
    "c_check": {"pl": "Kontrola", "en": "Check"}, "c_status": {"pl": "Status", "en": "Status"}, "c_reason": {"pl": "Uzasadnienie", "en": "Reason"},
    "n_stop": {"pl": "Stop loss", "en": "Stop loss"},
    "n_risk": {"pl": "Ryzyko na transakcję", "en": "Risk per trade"},
    "n_position": {"pl": "Wielkość pozycji", "en": "Position size"},
    "n_liquidity": {"pl": "Płynność", "en": "Liquidity"},
    "n_heat": {"pl": "Łączne ryzyko (heat)", "en": "Portfolio heat"},
    "n_sector": {"pl": "Koncentracja sektorowa", "en": "Sector concentration"},
    "n_thesis": {"pl": "Pisemna teza", "en": "Written thesis"},
    "n_reward_risk": {"pl": "Zysk do ryzyka", "en": "Reward/risk"},
    "n_circuit_breaker": {"pl": "Bezpiecznik (seria strat)", "en": "Circuit breaker"},
    "n_regime": {"pl": "Reżim rynku", "en": "Market regime"},
    "n_exposure_ceiling": {"pl": "Limit ekspozycji", "en": "Exposure ceiling"},
    "n_new_positions": {"pl": "Nowe pozycje w tygodniu", "en": "New positions this week"},
    "n_data_freshness": {"pl": "Świeżość danych", "en": "Data freshness"},
    # reasons
    "stop_missing": {"pl": "Brak stopu – bez niego ryzyko jest nieokreślone.", "en": "No stop – risk is undefined without it."},
    "stop_not_below": {"pl": "Stop {stop} nie jest poniżej wejścia {entry} (pozycja długa).", "en": "Stop {stop} is not below entry {entry} (long position)."},
    "stop_ok": {"pl": "Stop {stop}, {dist} poniżej wejścia.", "en": "Stop {stop}, {dist} below entry."},
    "risk_undefined": {"pl": "Ryzyka nie da się policzyć bez poprawnego stopu.", "en": "Risk cannot be computed without a valid stop."},
    "risk_over": {"pl": "Ryzyko {risk} = {pct} rachunku > limit {limit} ({src}). Zmniejsz liczbę akcji (gpw-position-sizer).",
                  "en": "Risk {risk} = {pct} of the account > limit {limit} ({src}). Reduce the share count (gpw-position-sizer)."},
    "risk_ok": {"pl": "Ryzyko {risk} = {pct} rachunku ≤ limit {limit} ({src}).", "en": "Risk {risk} = {pct} of the account ≤ limit {limit} ({src})."},
    "src_default": {"pl": "domyślny", "en": "default"}, "src_exposure": {"pl": "z gpw-exposure-coach", "en": "from gpw-exposure-coach"},
    "src_user": {"pl": "podany", "en": "user"},
    "position_cash": {"pl": "Pozycja {value} z prowizją przekracza wielkość rachunku.", "en": "Position {value} plus fee exceeds the account size."},
    "position_over": {"pl": "Pozycja {value} = {pct} rachunku > {limit}.", "en": "Position {value} = {pct} of the account > {limit}."},
    "position_ok": {"pl": "Pozycja {value} = {pct} rachunku ≤ {limit}.", "en": "Position {value} = {pct} of the account ≤ {limit}."},
    "liq_unknown": {"pl": "Brak danych o obrotach – płynności nie zweryfikowano.", "en": "No turnover data – liquidity not verified."},
    "liq_zero": {"pl": "Mediana obrotu = 0 – walor praktycznie nie jest handlowany.", "en": "Median turnover = 0 – the instrument barely trades."},
    "liq_over": {"pl": "Zlecenie = {pct} mediany dziennego obrotu ({median}, {window} sesji) > {limit}.", "en": "Order = {pct} of median daily turnover ({median}, {window} sessions) > {limit}."},
    "liq_ok": {"pl": "Zlecenie = {pct} mediany dziennego obrotu ({median}, {window} sesji) ≤ {limit}.", "en": "Order = {pct} of median daily turnover ({median}, {window} sessions) ≤ {limit}."},
    "heat_over": {"pl": "Ryzyko otwartych pozycji {existing} + ta transakcja {new} = {pct} rachunku > {limit}.", "en": "Open risk {existing} + this trade {new} = {pct} of the account > {limit}."},
    "heat_ok": {"pl": "Ryzyko otwartych pozycji {existing} + ta transakcja {new} = {pct} rachunku ≤ {limit}.", "en": "Open risk {existing} + this trade {new} = {pct} of the account ≤ {limit}."},
    "heat_no_stop": {"pl": "{pct} rachunku, ale pozycje bez stopu ({ns}) mają nieokreślone ryzyko – realny heat jest wyższy.",
                     "en": "{pct} of the account, but positions without a stop ({ns}) have undefined risk – real heat is higher."},
    "heat_no_journal": {"pl": "{pct} rachunku – liczone tylko z tej transakcji (brak dziennika, inne pozycje niewidoczne).",
                        "en": "{pct} of the account – this trade only (no journal, other positions invisible)."},
    "sector_unclassified": {"pl": "Walor poza 14 indeksami sektorowymi GPW – koncentracji sektorowej nie da się zmierzyć.",
                            "en": "Not in any of the 14 GPW sector indices – sector concentration cannot be measured."},
    "sector_over": {"pl": "Sektor {sector} po transakcji = {pct} rachunku > {limit}.", "en": "Sector {sector} after the trade = {pct} of the account > {limit}."},
    "sector_ok": {"pl": "Sektor {sector} po transakcji = {pct} rachunku ≤ {limit}.", "en": "Sector {sector} after the trade = {pct} of the account ≤ {limit}."},
    "thesis_missing": {"pl": "Brak zapisanej tezy z kryteriami unieważnienia (kill criteria). Bramka wymaga pisemnej tezy – zapisz ją w gpw-trade-journal (polecenie niżej).",
                       "en": "No written thesis with kill criteria. The gate requires a written thesis – record it in gpw-trade-journal (command below)."},
    "thesis_not_found": {"pl": "Nie znaleziono tezy {id} w dzienniku.", "en": "Thesis {id} not found in the journal."},
    "thesis_ambiguous": {"pl": "Kilka otwartych tez dla tego waloru ({ids}) – wskaż jedną przez --thesis-id.", "en": "Several open theses for this ticker ({ids}) – pick one with --thesis-id."},
    "thesis_ticker": {"pl": "Teza {id} dotyczy {thesis_ticker}, nie tego waloru.", "en": "Thesis {id} is about {thesis_ticker}, not this ticker."},
    "thesis_terminal": {"pl": "Teza {id} ma status {status} – nowa transakcja wymaga nowej tezy.", "en": "Thesis {id} is {status} – a new trade needs a new thesis."},
    "thesis_no_kill": {"pl": "Teza {id} nie ma kryteriów unieważnienia.", "en": "Thesis {id} has no kill criteria."},
    "thesis_stop_differs": {"pl": "Teza {id} ({kills} kryt. unieważnienia), ale stop {stop} różni się od stopu z planu {plan_stop} – zaktualizuj plan albo stop.",
                            "en": "Thesis {id} ({kills} kill criteria), but stop {stop} differs from the plan stop {plan_stop} – update the plan or the stop."},
    "thesis_ok": {"pl": "Teza {id} ({status}, {kills} kryt. unieważnienia{auto}).", "en": "Thesis {id} ({status}, {kills} kill criteria{auto})."},
    "auto": {"pl": ", znaleziona po walorze", "en": ", found by ticker"},
    "rr_no_target": {"pl": "Brak celu – zysk/ryzyko nie sprawdzony (dopuszczalne przy wyjściu trailing stopem).", "en": "No target – reward/risk not checked (acceptable for a trailing-stop exit)."},
    "rr_undefined": {"pl": "Zysku/ryzyka nie da się policzyć.", "en": "Reward/risk cannot be computed."},
    "rr_fail": {"pl": "{rr}R przy celu {target}{src} – potencjalny zysk mniejszy niż ryzyko (< {limit}R).", "en": "{rr}R at target {target}{src} – potential reward below the risk (< {limit}R)."},
    "rr_warn": {"pl": "{rr}R przy celu {target}{src} < {limit}R – słaba asymetria.", "en": "{rr}R at target {target}{src} < {limit}R – weak asymmetry."},
    "rr_ok": {"pl": "{rr}R przy celu {target}{src} ≥ {limit}R.", "en": "{rr}R at target {target}{src} ≥ {limit}R."},
    "from_thesis": {"pl": " (z tezy)", "en": " (from the thesis)"},
    "cb_pause_ex": {"pl": "gpw-exposure-coach: bezpiecznik = PAUZA – nie otwieraj nowych pozycji.", "en": "gpw-exposure-coach: circuit breaker = PAUSE – no new positions."},
    "cb_reduce_ex": {"pl": "gpw-exposure-coach: bezpiecznik = OGRANICZ – zmniejsz pozycję o połowę.", "en": "gpw-exposure-coach: circuit breaker = REDUCE – halve the size."},
    "cb_ok_ex": {"pl": "gpw-exposure-coach: bezpiecznik OK.", "en": "gpw-exposure-coach: circuit breaker OK."},
    "cb_unknown": {"pl": "Nieznana wartość bezpiecznika: {value}.", "en": "Unknown circuit-breaker value: {value}."},
    "cb_no_history": {"pl": "Brak zamkniętych transakcji w dzienniku.", "en": "No closed trades in the journal."},
    "cb_pause": {"pl": "{streak} strat z rzędu, obsunięcie zrealizowane {dd} – PAUZA (próg: 5 strat lub 10%). Najpierw przegląd (gpw-trade-review).",
                 "en": "{streak} losses in a row, realised drawdown {dd} – PAUSE (threshold: 5 losses or 10%). Review first (gpw-trade-review)."},
    "cb_reduce": {"pl": "{streak} straty z rzędu (obsunięcie {dd}) – zmniejsz pozycję o połowę.", "en": "{streak} losses in a row (drawdown {dd}) – halve the size."},
    "cb_ok": {"pl": "Seria strat {streak}, obsunięcie zrealizowane {dd} ({n} zamkniętych).", "en": "Loss streak {streak}, realised drawdown {dd} ({n} closed)."},
    "regime_missing": {"pl": "Brak pliku gpw-market-regime – reżim nieznany (mniejsza pewność).", "en": "No gpw-market-regime file – regime unknown (lower confidence)."},
    "regime_unknown": {"pl": "Nierozpoznany stan rynku: {state}.", "en": "Unrecognised market state: {state}."},
    "regime_bear_top": {"pl": "Bessa i ryzyko szczytu {top} ≥ {limit} – nowe pozycje długie zablokowane (możliwy --override z pisemnym uzasadnieniem).",
                        "en": "Bear market and top risk {top} ≥ {limit} – new longs blocked (--override with a written reason possible)."},
    "regime_overridden": {"pl": "Bessa, ryzyko szczytu {top} – ŚWIADOMIE POMINIĘTE: „{override}”.", "en": "Bear market, top risk {top} – CONSCIOUSLY OVERRIDDEN: \"{override}\"."},
    "regime_correction": {"pl": "Rynek w korekcie – nowe pozycje długie mają gorsze szanse; mniejsza pozycja, ciaśniejsze kryteria.",
                          "en": "Market in correction – new longs have worse odds; smaller size, stricter criteria."},
    "regime_bear": {"pl": "Bessa (ryzyko szczytu {top}) – nowe pozycje długie wbrew trendowi rynku.", "en": "Bear market (top risk {top}) – new longs fight the market trend."},
    "regime_top_risk": {"pl": "Stan {state}, ale ryzyko szczytu {top} ≥ {limit}.", "en": "State {state}, but top risk {top} ≥ {limit}."},
    "regime_ok": {"pl": "Stan rynku: {state} (ryzyko szczytu {top}).", "en": "Market state: {state} (top risk {top})."},
    "ceiling_missing": {"pl": "Brak pliku gpw-exposure-coach.", "en": "No gpw-exposure-coach file."},
    "ceiling_over": {"pl": "Zaangażowanie po transakcji {pct} > sufit {limit} (postawa: {posture}).", "en": "Exposure after the trade {pct} > ceiling {limit} (posture: {posture})."},
    "ceiling_ok": {"pl": "Zaangażowanie po transakcji {pct} ≤ sufit {limit} (postawa: {posture}).", "en": "Exposure after the trade {pct} ≤ ceiling {limit} (posture: {posture})."},
    "newpos_missing": {"pl": "Brak limitu nowych pozycji (brak gpw-exposure-coach).", "en": "No new-position limit (no gpw-exposure-coach)."},
    "newpos_over": {"pl": "{n} nowych pozycji w 7 dni > limit {limit}.", "en": "{n} new positions in 7 days > limit {limit}."},
    "newpos_ok": {"pl": "{n} nowych pozycji w 7 dni ≤ limit {limit}.", "en": "{n} new positions in 7 days ≤ limit {limit}."},
    "fresh_ok": {"pl": "Dane cenowe aktualne (sesja {date}).", "en": "Price data current (session {date})."},
    "i_no_market": {"pl": "brak danych cenowych", "en": "no price data"},
    "i_prices_stale": {"pl": "ceny sprzed {n} sesji ({date})", "en": "prices {n} sessions old ({date})"},
    "i_entry_far": {"pl": "wejście {dev}% od ostatniego kursu {last} (literówka?)", "en": "entry {dev}% from the last close {last} (typo?)"},
    "i_input_stale": {"pl": "plik {skill} sprzed {n} sesji ({date})", "en": "{skill} file {n} sessions old ({date})"},
    "inputs": {"pl": "Wejścia z innych skilli", "en": "Inputs from other skills"},
    "inp_line": {"pl": "{skill}: {path} (dane na {as_of}, {n} sesji temu)", "en": "{skill}: {path} (as of {as_of}, {n} sessions ago)"},
    "inp_none": {"pl": "brak – sprawdzane z mniejszą pewnością", "en": "none – checked with lower confidence"},
    "override_rec": {"pl": "Zapisane uzasadnienie pominięcia (--override)", "en": "Recorded override reason (--override)"},
    "override_unused": {"pl": " (nie było potrzebne – żaden FAIL reżimu)", "en": " (not needed – no regime FAIL)"},
    "next": {"pl": "Co dalej", "en": "Next steps"},
    "next_journal": {"pl": "Zapisz tezę w dzienniku (uzupełnij treść):", "en": "Record the thesis in the journal (fill in the text):"},
    "next_sizer": {"pl": "Przelicz wielkość pozycji: gpw-position-sizer.", "en": "Recalculate the size: gpw-position-sizer."},
    "next_go": {"pl": "Po wejściu zapisz transakcję: `journal.py fill <id> --side buy ...`.", "en": "After entering, record the fill: `journal.py fill <id> --side buy ...`."},
    "journal_note": {"pl": "Heat, sektor i ekspozycja liczone z aktywnych pozycji w dzienniku ({d}); pozycji spoza dziennika bramka nie widzi.",
                     "en": "Heat, sector and exposure use ACTIVE journal positions ({d}); positions outside the journal are invisible to the gate."},
    "sources": {"pl": "Źródła", "en": "Sources"},
    # ---- v1.1: context checks fed by the v1.2 skills ----
    "n_event_window": {"pl": "Zdarzenia w okresie trzymania", "en": "Events in the holding window"},
    "n_crowded_short": {"pl": "Zatłoczony short (KNF)", "en": "Crowded short (KNF)"},
    "n_insider_selling": {"pl": "Transakcje insiderów", "en": "Insider transactions"},
    "n_earnings_reaction": {"pl": "Reakcja na wyniki", "en": "Earnings reaction"},
    "n_quality_red_flags": {"pl": "Czerwone flagi w finansach", "en": "Quality red flags"},
    "n_macro_context": {"pl": "Tło makro", "en": "Macro context"},
    "n_stress_test": {"pl": "Test warunków skrajnych portfela", "en": "Portfolio stress test"},
    "infos": {"pl": "Informacje (INFO – nie zmieniają werdyktu)", "en": "Information (INFO – does not change the verdict)"},
    "unknowns": {"pl": "Nie sprawdzono (UNKNOWN) – jak uzupełnić", "en": "Not checked (UNKNOWN) – how to fill the gap"},
    "unknown_note": {"pl": "UNKNOWN nie blokuje (chyba że check jest w --require), ale obniża pewność werdyktu.",
                     "en": "UNKNOWN does not block (unless the check is in --require) but lowers confidence in the verdict."},
    "context": {"pl": "Kontekst transakcji", "en": "Trade context"},
    "window_line": {"pl": "Okres trzymania: {start} – {end} ({n} sesji) + bufor {buf} sesji do {bend}.",
                    "en": "Holding window: {start} – {end} ({n} sessions) + {buf}-session buffer to {bend}."},
    "gap_line": {"pl": "Typowa luka cenowa na otwarciu po wynikach: mediana |luki| {gap} (maks. {mx}, n = {n}{alln}) vs odległość stopu {stop}.",
                 "en": "Typical opening gap after reports: median |gap| {gap} (max {mx}, n = {n}{alln}) vs stop distance {stop}."},
    "gap_alln": {"pl": ", w tym raporty w trakcie sesji", "en": ", incl. intraday reports"},
    "ev_table": {"pl": "| Sesja | Data | Zdarzenie | Rodzaj daty | Źródło |", "en": "| Session | Date | Event | Date kind | Source |"},
    "dk_confirmed": {"pl": "potwierdzona", "en": "confirmed"},
    "dk_statutory_deadline": {"pl": "termin ustawowy (może być wcześniej)", "en": "statutory deadline (may come earlier)"},
    "dk_estimated": {"pl": "szacunek", "en": "estimated"},
    "dk_proposed_date_estimated": {"pl": "proponowana (szacunek)", "en": "proposed (estimated)"},
    "k_earnings": {"pl": "wyniki (raport okresowy)", "en": "earnings (periodic report)"},
    "k_ex_dividend": {"pl": "dzień odcięcia dywidendy", "en": "ex-dividend date"},
    "k_agm": {"pl": "walne zgromadzenie", "en": "general meeting"},
    "k_dividend_record": {"pl": "dzień dywidendy", "en": "dividend record date"},
    "k_dividend_payment": {"pl": "wypłata dywidendy", "en": "dividend payment"},
    "k_mpc_meeting": {"pl": "posiedzenie RPP", "en": "MPC (RPP) meeting"},
    "k_index_revision": {"pl": "rewizja indeksów GPW", "en": "GPW index revision"},
    "k_msci_review": {"pl": "przegląd MSCI", "en": "MSCI review"},
    "k_macro_release": {"pl": "dane makro (GUS)", "en": "macro release (GUS)"},
    "k_other": {"pl": "inne (rynek)", "en": "other (market)"},
    "k_company_other": {"pl": "inne (spółka)", "en": "other (company)"},
    "also": {"pl": " W oknie także: {items}.", "en": " Also in the window: {items}."},
    "exdiv_note": {"pl": "{kind} {date} (DPS {dps} = {dpsp} wejścia – kurs spada o ok. dywidendę, stop bliżej niż to może zadziałać od samego odcięcia)",
                   "en": "{kind} {date} (DPS {dps} = {dpsp} of entry – the price drops by about the dividend; a stop closer than that can trigger on the cut alone)"},
    "event_earnings_in_window": {"pl": "Wyniki {date} ({dk}) – sesja {session} z {n} planowanych. Reakcja na raport może przeskoczyć stop (luka). Rozważ mniejszą pozycję albo wejście po publikacji.{gapx}",
                                 "en": "Earnings {date} ({dk}) – session {session} of the planned {n}. The reaction can gap through the stop. Consider a smaller size or entering after the report.{gapx}"},
    "event_earnings_near_window": {"pl": "Wyniki {date} ({dk}) – sesja {session}, tuż po planowanym oknie {n} sesji (bufor {buf}). Jeśli pozycja potrwa dłużej, raport wypadnie w trakcie.{gapx}",
                                   "en": "Earnings {date} ({dk}) – session {session}, just after the planned {n}-session window (buffer {buf}). If the trade runs longer, the report falls inside.{gapx}"},
    "gapx": {"pl": " Typowa luka po wynikach {gap} {cmp} odległość stopu {stop}.", "en": " Typical post-report gap {gap} {cmp} stop distance {stop}."},
    "cmp_gt": {"pl": ">", "en": ">"}, "cmp_le": {"pl": "≤", "en": "≤"},
    "event_strict_gap_exceeds": {"pl": "--strict-events: wyniki {date} już w sesji {session} (≤ {limit}), a typowa luka po wynikach {gap} > odległość stopu {stop} – stop nie ochroni przed luką. Poczekaj na raport.",
                                 "en": "--strict-events: earnings {date} in session {session} (≤ {limit}) and the typical post-report gap {gap} > stop distance {stop} – the stop cannot protect against the gap. Wait for the report."},
    "event_strict_gap_unknown": {"pl": "--strict-events: wyniki {date} już w sesji {session} (≤ {limit}), a historii luk po wynikach brak (uruchom gpw-earnings-reaction ticker --history) – tryb ścisły blokuje.",
                                 "en": "--strict-events: earnings {date} in session {session} (≤ {limit}) and no post-report gap history (run gpw-earnings-reaction ticker --history) – strict mode blocks."},
    "event_strict_gap_ok": {"pl": "--strict-events: wyniki {date} w sesji {session}, ale typowa luka {gap} ≤ odległość stopu {stop} – dopuszczalne z zastrzeżeniem.",
                            "en": "--strict-events: earnings {date} in session {session}, but the typical gap {gap} ≤ stop distance {stop} – allowed with a caveat."},
    "event_no_calendar": {"pl": "Brak kalendarza zdarzeń dla tego waloru – nie wiadomo, czy w okresie trzymania są wyniki.",
                          "en": "No event calendar for this ticker – unknown whether earnings fall inside the holding window."},
    "event_partial_coverage": {"pl": "Kalendarz obejmuje spółkę tylko do {cov} – reszta okna niesprawdzona.",
                               "en": "The calendar covers the company only to {cov} – the rest of the window is unchecked."},
    "event_info_only": {"pl": "Brak wyników w oknie {n} sesji (+{buf}).", "en": "No earnings in the {n}-session window (+{buf})."},
    "event_clear": {"pl": "Brak wyników, dywidend i ważnych zdarzeń w oknie {n} sesji (+{buf}).", "en": "No earnings, dividends or key events in the {n}-session window (+{buf})."},
    "short_missing": {"pl": "Brak raportu gpw-ownership obejmującego ten walor (rejestr krótkiej sprzedaży KNF).", "en": "No gpw-ownership report covering this ticker (KNF short register)."},
    "short_none": {"pl": "Walor nie figuruje w rejestrze KNF (brak ujawnionych pozycji krótkich ≥ 0,5%; rejestr to dolna granica).",
                   "en": "Not in the KNF register (no disclosed net shorts ≥ 0.5%; the register is a lower bound)."},
    "short_crowded": {"pl": "Zatłoczony short: {total} akcji ujawnione krótko przez {holders} podm., zmiana 20 sesji {chg}, dni pokrycia {dtc}. Dwie strony: średnio takie spółki zachowują się słabiej (ujemny dryf), ale dobra wiadomość może wywołać gwałtowny short squeeze – oczekuj większej zmienności, ustaw stop świadomie.",
                      "en": "Crowded short: {total} of shares disclosed short by {holders} holder(s), 20-session change {chg}, days to cover {dtc}. Both ways: such stocks underperform on average (negative drift), yet good news can trigger a sharp short squeeze – expect higher volatility and set the stop deliberately."},
    "short_present": {"pl": "W rejestrze KNF: {total} akcji, {holders} podm., dni pokrycia {dtc} – poniżej progu zatłoczenia.",
                      "en": "In the KNF register: {total} of shares, {holders} holder(s), days to cover {dtc} – below the crowding threshold."},
    "insider_short_coverage": {"pl": "Raport insiderów obejmuje tylko {covered_days} dni ({start} – {end}); do oceny klastra potrzeba co najmniej {min_days} dni. Uruchom gpw-ownership insiders --days 60.", "en": "The insider report covers only {covered_days} days ({start} – {end}); a cluster check needs at least {min_days} days. Run gpw-ownership insiders --days 60."},
    "insider_missing": {"pl": "Brak raportu gpw-ownership z transakcjami insiderów (MAR art. 19) dla tego waloru.", "en": "No gpw-ownership report with insider transactions (MAR art. 19) for this ticker."},
    "insider_none": {"pl": "Brak powiadomień insiderów w okresie {start} – {end}.", "en": "No insider notices in {start} – {end}."},
    "insider_cluster_sell": {"pl": "Klaster sprzedaży insiderów (≥ 3 osoby w 30 dni), ostatnio {last} ({age} dni temu, ≤ {win}) – sprzedaż ma słabszą wartość informacyjną niż kupno, ale grupowa sprzedaż przed Twoim wejściem to sygnał do sprawdzenia tezy.{basis}",
                             "en": "Insider selling cluster (≥ 3 people in 30 days), last on {last} ({age} days ago, ≤ {win}) – sells carry less information than buys, but group selling right before your entry is a reason to re-check the thesis.{basis}"},
    "insider_cluster_buy": {"pl": "Pozytywnie: klaster zakupów insiderów (≥ 3 osoby w 30 dni), ostatnio {last}. Zakupy insiderów historycznie niosą informację (Lakonishok i Lee 2001) – to kontekst, nie sygnał kupna.{basis}",
                            "en": "Positive: insider buying cluster (≥ 3 people in 30 days), last on {last}. Insider buys have historically been informative (Lakonishok & Lee 2001) – context, not a buy signal.{basis}"},
    "insider_cluster_sell_old": {"pl": "Klaster sprzedaży insiderów, ale ostatnio {last} ({age} dni temu > {win}).", "en": "Insider selling cluster, but last on {last} ({age} days ago > {win})."},
    "insider_no_cluster": {"pl": "Powiadomienia insiderów: kupno {buys}, sprzedaż {sells} – bez klastra.{basis}", "en": "Insider notices: {buys} buy(s), {sells} sell(s) – no cluster.{basis}"},
    "basis_hints": {"pl": " (liczone z tytułów komunikatów – szczegóły w PDF, wklej do gpw-ownership parse)", "en": " (counted from notice titles – details are in the PDF; paste it into gpw-ownership parse)"},
    "reaction_missing": {"pl": "Brak raportu gpw-earnings-reaction obejmującego ten walor.", "en": "No gpw-earnings-reaction report covering this ticker."},
    "reaction_none": {"pl": "Brak reakcji na raporty okresowe w danych.", "en": "No reactions to periodic reports in the data."},
    "reaction_old": {"pl": "Ostatnia reakcja na wyniki ({rdate}, {label}) była {n} sesji temu (> {win}).", "en": "The latest earnings reaction ({rdate}, {label}) was {n} sessions ago (> {win})."},
    "reaction_strong_negative": {"pl": "Silnie negatywna reakcja na wyniki {rdate} ({n} sesji temu): CAR 3 dni {car} vs WIG{prov}. Na GPW dryf po wynikach występuje głównie po negatywnych niespodziankach (Jagliński 2020) – kurs często dalej słabnie; wejście wbrew temu wymaga mocnej tezy.",
                                 "en": "Strongly negative earnings reaction on {rdate} ({n} sessions ago): 3-day CAR {car} vs WIG{prov}. On the WSE post-earnings drift appears mainly after negative surprises (Jagliński 2020) – the price often keeps weakening; entering against it needs a strong thesis."},
    "reaction_strong_positive": {"pl": "Silnie pozytywna reakcja na wyniki {rdate} ({n} sesji temu), CAR 3 dni {car}{prov}. Na GPW dryf po pozytywnych niespodziankach jest słaby – nie zakładaj kontynuacji.",
                                 "en": "Strongly positive earnings reaction on {rdate} ({n} sessions ago), 3-day CAR {car}{prov}. On the WSE drift after positive surprises is weak – do not assume continuation."},
    "reaction_recent": {"pl": "Ostatnia reakcja na wyniki {rdate} ({n} sesji temu): {label}, CAR 3 dni {car}{prov}.", "en": "Latest earnings reaction {rdate} ({n} sessions ago): {label}, 3-day CAR {car}{prov}."},
    "provisional": {"pl": " (okno 3 dni jeszcze niepełne)", "en": " (3-day window not complete yet)"},
    "quality_missing": {"pl": "Brak raportu gpw-quality-screener obejmującego ten walor.", "en": "No gpw-quality-screener report covering this ticker."},
    "quality_negative_equity": {"pl": "⚠️ UJEMNY KAPITAŁ WŁASNY – zobowiązania przewyższają majątek. Flagi: {flags}. To nie blokuje automatycznie, ale wymaga przeczytania raportu (ryzyko rozwodnienia / restrukturyzacji).{extra}",
                                "en": "⚠️ NEGATIVE EQUITY – liabilities exceed assets. Flags: {flags}. Not an automatic block, but read the report first (dilution / restructuring risk).{extra}"},
    "quality_flags": {"pl": "Czerwone flagi w finansach: {flags} – przeczytaj raport, zanim wejdziesz.{extra}", "en": "Red flags in the accounts: {flags} – read the report before entering.{extra}"},
    "quality_ok": {"pl": "Bez czerwonych flag (wynik jakości {qs}, F-score {fs}).{extra}", "en": "No red flags (quality score {qs}, F-score {fs}).{extra}"},
    "q_stale_period": {"pl": " Dane za {period} są nieaktualne (nowszy raport już powinien być).", "en": " Data for {period} are stale (a newer report should exist)."},
    "q_checks": {"pl": " Kontrole danych: {checks}.", "en": " Data checks: {checks}."},
    "macro_missing": {"pl": "Brak raportu gpw-macro-regime.", "en": "No gpw-macro-regime report."},
    "macro_headwind_negative_tilt": {"pl": "Makro: {composite} ({label}), a historyczna tendencja dla sektora ({group}) jest {tilt} – tło wolne, tylko informacja; nie blokuje.",
                                     "en": "Macro: {composite} ({label}) and the historical tendency for the sector ({group}) is {tilt} – slow background, information only; never blocks."},
    "macro_with_tilt": {"pl": "Makro: {composite} ({label}); tendencja dla sektora ({group}): {tilt}. Tło wolne, tylko informacja.",
                        "en": "Macro: {composite} ({label}); sector tendency ({group}): {tilt}. Slow background, information only."},
    "macro_no_tilt": {"pl": "Makro: {composite} ({label}); brak przypisanej tendencji sektorowej dla tego waloru.", "en": "Macro: {composite} ({label}); no sector tendency mapped for this ticker."},
    "stress_missing": {"pl": "Brak raportu gpw-risk-lab (stress test portfela) – opcjonalne.", "en": "No gpw-risk-lab report (portfolio stress test) – optional."},
    "stress_empty": {"pl": "Raport gpw-risk-lab bez policzonych scenariuszy.", "en": "gpw-risk-lab report without computed scenarios."},
    "stress_no_tolerance": {"pl": "Najgorszy scenariusz obecnego portfela: {worst} – strata {loss} ({pln}); nie podano tolerancji straty.",
                            "en": "Worst scenario for the current portfolio: {worst} – loss {loss} ({pln}); no loss tolerance given."},
    "stress_breach": {"pl": "Portfel już teraz przekracza Twoją tolerancję straty {tol} w {n} scenariuszach (najgorszy: {worst}, strata {loss}). Ta transakcja dokłada ekspozycję na akcje.",
                      "en": "The portfolio already exceeds your loss tolerance {tol} in {n} scenario(s) (worst: {worst}, loss {loss}). This trade adds equity exposure."},
    "stress_ok": {"pl": "Najgorszy scenariusz {worst}: strata {loss} ≤ tolerancja {tol}.", "en": "Worst scenario {worst}: loss {loss} ≤ tolerance {tol}."},
    "required_unknown": {"pl": "Check wymagany (--require), a danych brak – fail-closed. {orig}", "en": "Required check (--require) without data – fail-closed. {orig}"},
    "stale_src": {"pl": " ⚠️ raport {file} sprzed {n} sesji (limit {max}).", "en": " ⚠️ report {file} is {n} sessions old (limit {max})."},
    "next_event": {"pl": "Wyniki w oknie: rozważ wejście po publikacji albo mniejszą pozycję (gpw-position-sizer, scenariusz luki).",
                   "en": "Earnings in the window: consider entering after the report or a smaller size (gpw-position-sizer, gap scenario)."},
    "inp_stale": {"pl": " – NIEAKTUALNY", "en": " – STALE"},
}

ORDER_HINT = ("stop", "risk", "position", "liquidity", "heat", "sector", "thesis", "reward_risk",
              "circuit_breaker", "regime", "exposure_ceiling", "new_positions", "data_freshness")


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

def read_sizer_json(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ge.GateError("cannot read sizer JSON {}: {}".format(path, exc))
    if d.get("skill") != "gpw-position-sizer":
        raise ge.GateError("{} is not a gpw-position-sizer report".format(path))
    inp = d.get("inputs") or {}
    b = d.get("broker") or {}
    return {"ticker": inp.get("ticker"), "entry": d.get("entry"), "stop": d.get("stop"), "shares": d.get("shares"),
            "target": (d.get("reward") or {}).get("target"), "account_size": inp.get("account_size"),
            "account_type": d.get("account_type"), "fee_pct": b.get("pct"), "fee_min": b.get("min_fee"),
            "broker": b.get("key")}


# input key -> (skill, CLI attribute of the explicit path)
INPUTS = {
    "regime": ("gpw-market-regime", "regime_json"),
    "exposure": ("gpw-exposure-coach", "exposure_json"),
    "events": ("gpw-event-calendar", "events_json"),
    "ownership": ("gpw-ownership", "ownership_json"),
    "earnings": ("gpw-earnings-reaction", "earnings_json"),
    "quality": ("gpw-quality-screener", "quality_json"),
    "macro": ("gpw-macro-regime", "macro_json"),
    "dividend": ("gpw-dividend-analyzer", "dividend_json"),
    "risk_lab": ("gpw-risk-lab", "risk_lab_json"),
}
TICKER_INPUTS = ("events", "ownership", "earnings", "quality", "dividend")


def load_input(key, explicit, input_dir: Path, reference, lang, ids=None):
    """One input report via gpwlib.reports (explicit path or newest in input_dir).

    For per-company inputs, when the newest report does not cover the ticker
    (e.g. an ownership report for another company), the newest report that
    does cover it is used instead. Returns the find_report dict or None.
    An explicit path that is missing, corrupt or of another skill raises
    reports.ReportError (exit 1).
    """
    skill = INPUTS[key][0]
    max_age = cc.CONTEXT_DEFAULTS["max_age"].get(key, ge.DEFAULTS["max_input_age_sessions"])
    rep = reports.find_report(skill, input_dir, explicit_path=explicit, reference=reference,
                              max_sessions_behind=max_age, lang=lang)
    if explicit or ids is None or key not in TICKER_INPUTS or (rep and cc.covers(key, rep, ids)):
        return rep
    for r in reports.iter_reports(input_dir, skill=skill, reference=reference, newest_first=True):
        cand = {"data": r["data"]}
        if cc.covers(key, cand, ids):
            return reports.describe(Path(r["path"]), r["data"], skill, reference, max_age, lang)
    return rep


def load_ownership_parts(base, explicit, input_dir: Path, reference, lang, ids) -> dict:
    """gpw-ownership writes one report per mode: the newest report may be the
    insiders run while the KNF shorts sit in an older shorts run (both from
    the same daily pipeline). Pick, for each block (``shorts``, ``insiders``),
    the newest report that carries it and covers the ticker. An explicit
    ``--ownership-json`` is used as given."""
    out = {}
    skill = INPUTS["ownership"][0]
    max_age = cc.CONTEXT_DEFAULTS["max_age"].get("ownership", ge.DEFAULTS["max_input_age_sessions"])
    for block in ("shorts", "insiders"):
        if base and isinstance((base.get("data") or {}).get(block), dict) and (explicit or cc.covers("ownership", base, ids)):
            out[block] = base
            continue
        if explicit:
            out[block] = base
            continue
        for r in reports.iter_reports(input_dir, skill=skill, reference=reference, newest_first=True):
            if isinstance(r["data"].get(block), dict) and cc.covers("ownership", {"data": r["data"]}, ids):
                out[block] = reports.describe(Path(r["path"]), r["data"], skill, reference, max_age, lang)
                break
        else:
            out[block] = base
    return out


def input_row(key: str, rep: dict) -> dict:
    return {"skill": INPUTS[key][0], "input": key, "path": rep["path"], "file": rep["file"], "as_of": rep["as_of"],
            "sessions_behind": rep["sessions_behind"], "max_sessions_behind": rep["max_sessions_behind"],
            "stale": rep["stale"], "warning": rep.get("warning")}


def resolve_isins(ticker, cache_dir, offline):
    """ISIN of the ticker from gpwlib.fundamentals (cached GPW listing); [] if unknown."""
    try:
        r = fundamentals.resolve_isin(ticker, cache_dir=cache_dir, offline=offline)
        return [r["isin"]] if r and r.get("isin") else []
    except Exception:  # noqa: BLE001 - optional enrichment; matching falls back to ticker names
        return []


def load_market(ticker, journal_tickers, end, cache_dir, offline, window):
    """Last close / median turnover for the ticker (shares, then ETFs) and prices for journal tickers."""
    n = window + 5
    shares = gpw_archive.load_sessions(n, end=end, cache_dir=cache_dir, offline=offline, progress=False)
    if not shares:
        return None, {}, None, []
    pnl = panel.build_panel(shares)
    up = {k.upper(): k for k in pnl}
    name, kind, sessions = up.get(ticker.upper()), "share", shares
    if name is None:
        latest = gpw_archive.load_sessions(1, end=end, type_id=gpw_archive.TYPE_ETF, cache_dir=cache_dir,
                                           offline=offline, progress=False)
        etf_names = [r["name"] for r in latest[-1]["rows"]] if latest else []
        if ticker.upper() in {x.upper() for x in etf_names}:
            etf = gpw_archive.load_sessions(n, end=end, type_id=gpw_archive.TYPE_ETF, cache_dir=cache_dir,
                                            offline=offline, progress=False)
            epnl = panel.build_panel(etf)
            name = {k.upper(): k for k in epnl}.get(ticker.upper())
            if name:
                pnl, kind, sessions = dict(pnl, **epnl), "etf", etf
        if name is None:
            import difflib
            pool = sorted(set(up) | {x.upper() for x in etf_names})
            t = ticker.upper()
            cm = [x for x in pool if t in x][:3] + difflib.get_close_matches(t, pool, n=5, cutoff=0.6)
            cm = list(dict.fromkeys(cm))[:6]
            raise ge.GateError("'{}' not found in the GPW archive (shares{}); close matches: {}. Use the GPW short name, "
                               "e.g. PKOBP, PKNORLEN, CDPROJEKT.".format(
                                   ticker, " and ETFs" if etf_names else "; ETF list unavailable offline",
                                   ", ".join(cm) or "none"))
    s = pnl[name]
    turn = [v * 1000.0 for v in s.turnover_k[-window:]]
    last_date = dt.date.fromisoformat(s.dates[-1])
    fr = provenance.freshness(dt.date.fromisoformat(sessions[-1]["date"]), reference=end)
    market = {"ticker": name, "kind": kind, "last_close": s.close[-1], "last_date": s.dates[-1],
              "median_turnover_pln": median(turn), "is_stale": fr["is_stale"], "sessions_behind": fr["sessions_behind"],
              "archive_date": sessions[-1]["date"]}
    prices = {k: pnl[k].close[-1] for k in journal_tickers if k in pnl}
    return market, prices, last_date, sessions


def run(args) -> dict:
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else None
    end = dt.date.fromisoformat(args.as_of) if args.as_of else None
    sizer = read_sizer_json(args.sizer_json) if args.sizer_json else {}

    def pick(name):
        v = getattr(args, name, None)
        return v if v is not None else sizer.get(name)

    broker_key = args.broker or sizer.get("broker") or "mbank"
    b = brokers.resolve(broker_key, args.commission_pct, args.commission_min)
    fee_pct, fee_min = b["pct"], b["min_fee"]
    if args.commission_pct is None and args.commission_min is None and args.broker is None and sizer.get("fee_pct") is not None:
        fee_pct, fee_min = sizer["fee_pct"], sizer["fee_min"] or 0.0
    trade = {"ticker": (pick("ticker") or "").strip().upper(), "entry": pick("entry"), "stop": pick("stop"),
             "shares": pick("shares"), "target": pick("target"), "account_size": pick("account_size"),
             "account_type": pick("account_type") or "regular", "fee_pct": fee_pct, "fee_min": fee_min,
             "thesis_id": args.thesis_id}
    if not trade["ticker"]:
        raise ge.GateError("--ticker is required (or --sizer-json with a ticker)")
    if trade["shares"] is not None:
        trade["shares"] = int(trade["shares"])
    ge.validate_trade(trade)

    state = Path(args.state_dir).expanduser() if args.state_dir else journal_io.default_state_dir()
    journal_ok = (state / "theses").is_dir()
    theses = journal_io.load_theses(state) if journal_ok else []
    opens = journal_io.open_positions(theses)
    closed = journal_io.closed_results(theses)

    market, prices, _, sessions = load_market(trade["ticker"], [p["ticker"] for p in opens], end, cache_dir,
                                              args.offline, ge.DEFAULTS["turnover_window"])
    if market:
        trade["ticker"] = market["ticker"]
    sector_map = constituents.sector_membership(cache_dir, offline=args.offline)
    sector = sector_map.get(trade["ticker"]) if not market or market["kind"] == "share" else None

    input_dir = Path(args.input_dir).expanduser()
    isins = resolve_isins(trade["ticker"], cache_dir, args.offline)
    ids = cc.identity(trade["ticker"], isins)
    reps = {k: load_input(k, getattr(args, INPUTS[k][1], None), input_dir, end, args.lang, ids) for k in INPUTS}
    inputs = [input_row(k, reps[k]) for k in INPUTS if reps[k]]
    own = load_ownership_parts(reps["ownership"], getattr(args, "ownership_json", None), input_dir, end, args.lang, ids)
    for part in own.values():
        if part and part is not reps["ownership"] and part["path"] not in {i["path"] for i in inputs}:
            inputs.append(input_row("ownership", part))
    reference = end or plcal.today_warsaw()
    # entry session: the reference day if its session is not in the data yet, else the next session
    after_close = bool(market and market["last_date"] >= reference.isoformat())
    context = {"reports": dict({k: reps[k] for k in cc.INPUT_SKILLS},
                               ownership_shorts=own.get("shorts"), ownership_insiders=own.get("insiders")),
               "reference_date": reference,
               "after_close": after_close, "sector": sector, "isins": isins, "strict_events": args.strict_events,
               "require": args.require or [], "out_dir": str(input_dir),
               "cfg": {"holding_days": args.holding_days, "event_buffer_sessions": args.event_buffer_sessions}}
    ctx = {"market": market, "sector": sector, "sector_map": sector_map, "theses": theses, "journal_ok": journal_ok,
           "open_positions": opens, "closed_results": closed, "prices": prices,
           "regime": (reps["regime"] or {}).get("data"), "exposure": (reps["exposure"] or {}).get("data"),
           "inputs": inputs, "override": args.override, "user_max_risk_pct": args.max_risk_pct,
           "today": end or dt.date.today(), "context": context}
    cfg = {}
    for k in ("max_position_pct", "max_adv_pct", "max_heat_pct", "max_sector_pct", "min_rr"):
        if getattr(args, k) is not None:
            cfg[k] = getattr(args, k)
    res = ge.evaluate(trade, ctx, cfg)
    srcs = ["GPW — Archiwum notowań: https://www.gpw.pl/archiwum-notowan",
            "GPW Benchmark — portfele indeksów sektorowych: https://gpwbenchmark.pl"]
    if market:
        prov = provenance.build(as_of=dt.date.fromisoformat(market["archive_date"]), sources=srcs,
                                sessions_used=len(sessions), universe_size=None,
                                notes=["Sector membership = current GPW Benchmark portfolio (not point-in-time)."],
                                reference_date=end)
    else:
        prov = {"schema_version": provenance.SCHEMA_VERSION, "as_of": None, "sources": srcs, "sessions_used": 0,
                "freshness": {"as_of": None, "sessions_behind": None, "is_stale": True},
                "notes": ["No market data available (offline cache empty?) - liquidity and freshness unverified."]}
    prov["input_reports"] = [{"skill": i["skill"], "file": i["file"], "as_of": i["as_of"], "stale": i["stale"]} for i in inputs]
    prov["broker_tariff"] = {"broker": broker_key, "pct": fee_pct, "min_fee": fee_min,
                             "notes": brokers.tariff_notes(args.lang, overridden=b["overridden"])}
    return {"skill": "gpw-pre-trade-gate", "version": VERSION, "lang": args.lang,
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "sizer_json": str(args.sizer_json) if args.sizer_json else None,
            "journal_dir": str(state), "journal_ok": journal_ok, "inputs": inputs, "market": market,
            "sector": sector, **res, "data_provenance": prov}


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

def _fmt_params(c: dict, lang: str, t) -> dict:
    p = dict(c["params"])
    for k in ("risk", "value", "existing", "new", "median"):
        if isinstance(p.get(k), (int, float)):
            p[k] = pln(p[k], 0 if k == "median" else 2, lang)
    for k in ("pct", "dist", "dd"):
        if isinstance(p.get(k), (int, float)):
            p[k] = ("< " + pct(0.01, 2, lang)) if 0 < p[k] < 0.01 else pct(p[k], 2, lang)
    if c["id"] in ("risk", "position", "liquidity", "heat", "sector", "exposure_ceiling") and isinstance(p.get("limit"), (int, float)):
        p["limit"] = pct(p["limit"], 2 if c["id"] == "risk" else 0, lang)
    for k in ("stop", "entry", "target", "plan_stop", "last"):
        if isinstance(p.get(k), (int, float)):
            p[k] = num(p[k], 2, lang)
    if isinstance(p.get("rr"), (int, float)):
        p["rr"] = num(p["rr"], 2, lang)
    if c["id"] == "reward_risk" and isinstance(p.get("limit"), (int, float)):
        p["limit"] = num(p["limit"], 1, lang)
    if "src" in p:
        p["src"] = t("from_thesis") if p["src"] == "thesis" else ("" if c["id"] == "reward_risk" else t("src_" + p["src"]))
    if "sector" in p and p["sector"] in SECTOR_INDICES:
        p["sector"] = SECTOR_INDICES[p["sector"]][2 if lang == "pl" else 1]
    if "ids" in p:
        p["ids"] = ", ".join(p["ids"])
    if "no_stop" in p:
        p["ns"] = ", ".join(p["no_stop"])
    if c["id"] == "thesis":
        p["auto"] = t("auto") if p.get("how") == "auto" else ""
    if p.get("top") is None and c["id"] == "regime":
        p["top"] = "—"
    return p


LABELS = {
    "pl": {"supportive": "sprzyjające", "mixed": "mieszane", "headwind": "niesprzyjające (headwind)",
           "positive": "pozytywna", "slightly_positive": "lekko pozytywna", "neutral": "neutralna",
           "slightly_negative": "lekko negatywna", "negative": "negatywna",
           "strong_positive": "silnie pozytywna", "strong_negative": "silnie negatywna"},
    "en": {},
}


def _lbl(v, lang):
    return LABELS.get(lang, {}).get(v, str(v).replace("_", " ")) if v is not None else "—"


def _d(x, lang):
    """ISO date -> 16.11.2026 (pl) / 2026-11-16 (en)."""
    if not x:
        return "—"
    x = str(x)[:10]
    return "{}.{}.{}".format(x[8:10], x[5:7], x[0:4]) if lang == "pl" else x


def _pp(v, lang, digits=2, signed=False):
    if not isinstance(v, (int, float)):
        return "—"
    txt = pct(v, digits, lang)
    return ("+" + txt) if signed and v > 0 else txt


def event_items(c: dict, lang: str, t) -> str:
    items = []
    for e in c["params"].get("events") or []:
        if e["kind"] == "earnings":
            continue
        kind = t("k_" + e["kind"]) if ("k_" + e["kind"]) in STRINGS else e["kind"]
        if e["kind"] == "ex_dividend" and e.get("dps") is not None and e.get("dps_pct_of_entry") is not None:
            items.append(t("exdiv_note", kind=kind, date=_d(e["date"], lang), dps=pln(e["dps"], 2, lang),
                           dpsp=pct(e["dps_pct_of_entry"], 2, lang)))
        else:
            items.append("{} {}".format(kind, _d(e["date"], lang)))
    seen, uniq = set(), []
    for i in items:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    return t("also", items="; ".join(uniq)) if uniq else ""


def ctx_text(c: dict, lang: str) -> str:
    """Translate a context check (v1.1) into one sentence."""
    t = T(STRINGS, lang)
    p, r = c["params"], c["reason"]
    if r == "required_unknown":
        orig = dict(c, reason=p.get("original_reason"), status=cc.UNKNOWN)
        return t("required_unknown", orig=ctx_text(orig, lang))
    cid = c["id"]
    if cid == "event_window":
        w = p.get("window") or {}
        gap = p.get("earnings_gap") or {}
        q = dict(date=_d(p.get("date"), lang), dk=t("dk_" + p["date_kind"]) if ("dk_" + str(p.get("date_kind"))) in STRINGS else (p.get("date_kind") or "—"),
                 session=p.get("session"), n=w.get("sessions"), buf=w.get("buffer_sessions"), limit=p.get("limit"),
                 gap=_pp(gap.get("median_abs_gap_pct"), lang), stop=_pp(p.get("stop_distance_pct"), lang),
                 cov=_d(p.get("coverage_end"), lang))
        q["gapx"] = (t("gapx", gap=q["gap"], stop=q["stop"], cmp=t("cmp_gt" if p.get("gap_exceeds_stop") else "cmp_le"))
                     if gap and p.get("stop_distance_pct") is not None else "")
        txt = t(r, **q)
        return txt + (event_items(c, lang, t) if r not in ("event_clear", "event_no_calendar") else "")
    if cid == "crowded_short":
        q = dict(total=_pp(p.get("total_pct"), lang), holders=p.get("holders", "—"),
                 chg=(num(p["change_20s_pp"], 2, lang) + " pp") if isinstance(p.get("change_20s_pp"), (int, float)) else "—",
                 dtc=num(p["days_to_cover"], 1, lang) if isinstance(p.get("days_to_cover"), (int, float)) else "—")
        return t(r, **q)
    if cid == "insider_selling":
        q = dict(start=_d(p.get("period_start"), lang), end=_d(p.get("period_end"), lang), last=_d(p.get("last_date"), lang),
                 age=p.get("age_days", "—"), win=p.get("window_days"), buys=p.get("buys", 0), sells=p.get("sells", 0),
                 basis=t("basis_hints") if p.get("basis") == "notice_hints" else "",
                 covered_days=p.get("covered_days", "—"), min_days=p.get("min_days", "—"))
        return t(r, **q)
    if cid == "earnings_reaction":
        q = dict(rdate=_d(p.get("reaction_date"), lang), n=p.get("sessions_since"), win=p.get("window"),
                 label=_lbl(p.get("label"), lang), car=_pp(p.get("car"), lang, signed=True),
                 prov=t("provisional") if p.get("provisional") else "")
        return t(r, **q)
    if cid == "quality_red_flags":
        extra = ""
        if p.get("stale_period"):
            extra += t("q_stale_period", period=p.get("period"))
        if p.get("data_checks"):
            extra += t("q_checks", checks=", ".join(p["data_checks"]))
        q = dict(flags=", ".join(p.get("flags") or []) or "—", extra=extra,
                 qs=num(p["quality_score"], 1, lang) if isinstance(p.get("quality_score"), (int, float)) else "—",
                 fs=p.get("f_score") if p.get("f_score") is not None else (p.get("f_display") or "—"))
        return t(r, **q)
    if cid == "macro_context":
        tl = p.get("tilt") or {}
        q = dict(composite=_lbl(p.get("composite"), lang), label=p.get("regime_label") or "—",
                 group=tl.get("group", "—"), tilt=_lbl(tl.get("tilt"), lang))
        return t(r, **q)
    if cid == "stress_test":
        wl = p.get("worst_label")
        worst = (wl.get(lang) or wl.get("en")) if isinstance(wl, dict) else (wl or p.get("worst_id") or "—")
        q = dict(worst=worst, loss=_pp(p.get("worst_loss_pct"), lang), tol=_pp(p.get("max_loss_pct"), lang, 0),
                 n=p.get("breaches", 0), pln=pln(p["worst_pln_loss"], 0, lang) if isinstance(p.get("worst_pln_loss"), (int, float)) else "—")
        return t(r, **q)
    return r


def source_suffix(c: dict, lang: str) -> str:
    src = c.get("source") or {}
    if src.get("stale") and src.get("sessions_behind") is not None:
        mx = cc.CONTEXT_DEFAULTS["max_age"].get(next((k for k, v in cc.INPUT_SKILLS.items() if v == src.get("skill")), ""), 3)
        return T(STRINGS, lang)("stale_src", file=src.get("file"), n=src["sessions_behind"], max=mx)
    return ""


def reason_text(c: dict, lang: str) -> str:
    t = T(STRINGS, lang)
    if c["id"] in cc.CONTEXT_CHECKS:
        return ctx_text(c, lang) + source_suffix(c, lang)
    if c["reason"] == "fresh_issues":
        parts = []
        for i in c["params"]["issues"]:
            q = dict(i)
            if "last" in q:
                q["last"] = num(q["last"], 2, lang)
            if "dev" in q:
                q["dev"] = num(q["dev"], 1, lang)
            parts.append(t("i_" + i["code"], **{k: v for k, v in q.items() if k != "code"}))
        return "; ".join(parts) + "."
    return t(c["reason"], **_fmt_params(c, lang, t))


def context_section(r: dict, lang: str) -> list:
    cx = r.get("context")
    if not cx:
        return []
    t = T(STRINGS, lang)
    w = cx.get("holding_window") or {}
    L = ["## " + t("context"), ""]
    if w:
        L += [t("window_line", start=_d(w["start"], lang), end=_d(w["end"], lang), n=w["sessions"],
                buf=w["buffer_sessions"], bend=_d(w["buffer_end"], lang)), ""]
    g = cx.get("earnings_gap")
    if g:
        stop = r["numbers"].get("stop_distance_pct")
        L += [t("gap_line", gap=_pp(g["median_abs_gap_pct"], lang), mx=_pp(g["max_abs_gap_pct"], lang), n=g["n"],
                alln=t("gap_alln") if g.get("all_alignments") else "", stop=_pp(stop, lang)), ""]
    evs = cx.get("events_in_window") or []
    if evs:
        L += [t("ev_table"), "|---|---|---|---|---|"]
        for e in evs:
            kind = t("k_" + e["kind"]) if ("k_" + e["kind"]) in STRINGS else e["kind"]
            title = (e.get("title") or "")[:70]
            dk = t("dk_" + e["date_kind"]) if ("dk_" + str(e.get("date_kind"))) in STRINGS else (e.get("date_kind") or "—")
            L.append("| {}{} | {} | {}{} | {} | {} |".format(e["session"], "" if e["in_window"] else " (+)", _d(e["date"], lang),
                                                       kind, (": " + title) if title else "", dk, e["origin"]))
        L.append("")
    return L


def journal_command(r: dict) -> str:
    tr = r["trade"]
    cmd = ('python3 ../gpw-trade-journal/scripts/journal.py add --ticker {t} --thesis "..." '
           '--pillar "..." --kill "..." --entry {e:g} --stop {s}').format(t=tr["ticker"], e=tr["entry"], s="{:g}".format(tr["stop"]) if tr.get("stop") else "...")
    if tr.get("target"):
        cmd += " --target {:g}".format(tr["target"])
    if tr.get("account_type") and tr["account_type"] != "regular":
        cmd += " --account-type {}".format(tr["account_type"])
    if r.get("sizer_json"):
        cmd += " --sizer-json {}".format(r["sizer_json"])
    return cmd + " --status READY"


def render_markdown(r: dict, lang: str) -> str:
    t = T(STRINGS, lang)
    tr, nm, prov = r["trade"], r["numbers"], r["data_provenance"]
    icon = {"GO": "🟢", "GO WITH CONDITIONS": "🟡", "NO-GO": "🔴"}[r["verdict"]]
    L = ["# " + t("title"), ""]
    if not r["market"]:
        L += ["> " + t("no_market"), ""]
    elif prov["freshness"]["is_stale"]:
        L += ["> " + t("stale", n=prov["freshness"]["sessions_behind"]), ""]
    L += ["## {} {}: **{}**".format(icon, t("verdict"), r["verdict"]), "", t("v_" + r["verdict"]) + ". " + t("never"), ""]
    fails = [c for c in r["checks"] if c["status"] == ge.FAIL]
    warns = [c for c in r["checks"] if c["status"] == ge.WARN]
    if fails:
        L += ["### " + t("blockers"), ""] + ["- **{}** – {}".format(t("n_" + c["id"]), reason_text(c, lang)) for c in fails] + [""]
    if warns:
        warns = sorted(warns, key=lambda c: not c["params"].get("prominent"))
        L += ["### " + t("conditions"), ""] + ["- **{}** – {}".format(t("n_" + c["id"]), reason_text(c, lang)) for c in warns] + [""]
    infos = [c for c in r["checks"] if c["status"] == ge.INFO]
    if infos:
        L += ["### " + t("infos"), ""] + ["- **{}** – {}".format(t("n_" + c["id"]), reason_text(c, lang)) for c in infos] + [""]
    unknowns = [c for c in r["checks"] if c["status"] == ge.UNKNOWN]
    if unknowns:
        L += ["### " + t("unknowns"), "", "_" + t("unknown_note") + "_", ""]
        for c in unknowns:
            L.append("- **{}** – {}".format(t("n_" + c["id"]), reason_text(c, lang)))
            if c.get("command"):
                L.append("  `{}`".format(c["command"]))
        L.append("")
    L += ["## " + t("trade"), "", "| | |", "|---|---|",
          "| {} | {} |".format(t("k_ticker"), tr["ticker"]),
          "| {} | {} |".format(t("k_entry"), num(tr["entry"], 2, lang)),
          "| {} | {} |".format(t("k_stop"), num(tr.get("stop"), 2, lang)),
          "| {} | {} |".format(t("k_target"), num(tr.get("target"), 2, lang)),
          "| {} | {} |".format(t("k_shares"), num(tr["shares"], 0, lang)),
          "| {} | {} |".format(t("k_value"), pln(nm["value"], 2, lang)),
          "| {} | {} ({}) |".format(t("k_account"), pln(tr["account_size"], 0, lang), tr.get("account_type", "regular").upper()),
          "| {} | {} + {} |".format(t("k_fees"), pln(nm["fee_entry"], 2, lang), pln(nm.get("fee_exit_stop"), 2, lang)),
          "| {} | {} ({}) |".format(t("k_risk"), pln(nm.get("risk_total"), 2, lang),
                                    pct(nm["risk_total"] / tr["account_size"] * 100, 2, lang) if nm.get("risk_total") is not None else "—"),
          "| {} | {} |".format(t("k_rr"), (num(nm["rr"], 2, lang) + "R") if nm.get("rr") is not None else "—"),
          "| {} | {} ({}% min {}) |".format(t("k_broker"), prov["broker_tariff"]["broker"], num(tr["fee_pct"], 2, lang),
                                           pln(tr["fee_min"], 2, lang))]
    if r.get("sizer_json"):
        L.append("| {} | {} |".format(t("k_source"), r["sizer_json"]))
    L += ["", "## " + t("checks"), "", "| # | {} | {} | {} |".format(t("c_check"), t("c_status"), t("c_reason")), "|---|---|---|---|"]
    for i, c in enumerate(r["checks"], 1):
        L.append("| {} | {} | **{}** | {} |".format(i, t("n_" + c["id"]), c["status"], reason_text(c, lang)))
    L.append("")
    L += context_section(r, lang)
    L += ["**{}:** ".format(t("inputs")) + ("; ".join(t("inp_line", skill=i["skill"], path=i.get("file") or i["path"], as_of=i["as_of"] or "—",
                                                       n=i["sessions_behind"] if i["sessions_behind"] is not None else "—")
                                                 + (t("inp_stale") if i.get("stale") else "")
                                                 for i in r["inputs"]) or t("inp_none")), ""]
    if r.get("override"):
        used = any(c["reason"] == "regime_overridden" for c in r["checks"])
        L += ["**{}{}:** „{}”".format(t("override_rec"), "" if used else t("override_unused"), r["override"]), ""]
    L += ["_" + t("journal_note", d=r["journal_dir"]) + "_", ""]
    L += ["## " + t("next"), ""]
    if any(c["id"] == "thesis" and c["status"] == ge.FAIL for c in r["checks"]):
        L += ["- " + t("next_journal"), "", "```bash", journal_command(r), "```", ""]
    if any(c["id"] in ("risk", "position", "liquidity", "heat", "sector") and c["status"] == ge.FAIL for c in r["checks"]):
        L.append("- " + t("next_sizer"))
    if any(c["id"] == "event_window" and c["status"] in (ge.WARN, ge.FAIL) for c in r["checks"]):
        L.append("- " + t("next_event"))
    if r["verdict"] != "NO-GO":
        L.append("- " + t("next_go"))
    L += [""] + ["- " + n for n in prov["broker_tariff"]["notes"]]
    L += ["", "## " + t("sources"), ""] + ["- {} ({})".format(s, prov["as_of"] or "—") for s in prov["sources"]]
    L += ["", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _pos(text: str) -> float:
    try:
        v = float(text.replace(",", ".").replace(" ", ""))
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number")
    if not math.isfinite(v) or v <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return v


def _nonneg(text: str) -> float:
    try:
        v = float(text.replace(",", "."))
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number")
    if not math.isfinite(v) or v < 0:
        raise argparse.ArgumentTypeError("must be a non-negative number")
    return v


def _shares(text: str) -> int:
    try:
        v = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number (GPW lot = 1 share)")
    if v <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return v


def _date(text: str) -> str:
    try:
        dt.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD")
    return text


def _require(text: str) -> list:
    items = [x.strip() for x in text.split(",") if x.strip()]
    bad = [x for x in items if x not in cc.CONTEXT_CHECKS]
    if bad or not items:
        raise argparse.ArgumentTypeError("unknown check(s) {}; choose from {}".format(
            ", ".join(bad) or "(empty)", ", ".join(cc.CONTEXT_CHECKS)))
    return items


def _sessions(text: str) -> int:
    try:
        v = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number of sessions")
    if v < 1 or v > 250:
        raise argparse.ArgumentTypeError("must be between 1 and 250 sessions")
    return v


def _nonneg_int(text: str) -> int:
    try:
        v = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number")
    if v < 0 or v > 60:
        raise argparse.ArgumentTypeError("must be between 0 and 60")
    return v


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fail-closed pre-trade checklist for GPW long entries (PL/EN). Never places orders.")
    p.add_argument("--ticker", help="GPW short name, e.g. PKOBP")
    p.add_argument("--entry", type=_pos, help="Planned entry price")
    p.add_argument("--stop", type=_pos, help="Stop-loss price (required by the gate)")
    p.add_argument("--shares", type=_shares, help="Planned share count")
    p.add_argument("--target", type=_pos, help="Target price (optional; else the thesis plan target)")
    p.add_argument("--account-size", type=_pos, help="Account value in PLN")
    p.add_argument("--account-type", choices=["regular", "ike", "ikze", "oki"])
    p.add_argument("--sizer-json", type=Path, help="gpw-position-sizer JSON report (fills ticker/entry/stop/shares/target/account)")
    p.add_argument("--thesis-id", help="Journal thesis id (or unique prefix); default: the one open thesis for the ticker")
    p.add_argument("--state-dir", help="Journal directory (default ~/.gpw-trading-skills/journal or $GPW_JOURNAL_DIR)")
    p.add_argument("--regime-json", help="gpw-market-regime JSON (default: newest in --input-dir)")
    p.add_argument("--exposure-json", help="gpw-exposure-coach JSON (default: newest in --input-dir)")
    p.add_argument("--events-json", help="gpw-event-calendar JSON (window mode is best; default: newest in --input-dir)")
    p.add_argument("--ownership-json", help="gpw-ownership JSON (ticker / shorts / insiders mode)")
    p.add_argument("--earnings-json", help="gpw-earnings-reaction JSON (ticker --history gives the gap history)")
    p.add_argument("--quality-json", help="gpw-quality-screener JSON (screen or ticker mode)")
    p.add_argument("--macro-json", help="gpw-macro-regime JSON")
    p.add_argument("--dividend-json", help="gpw-dividend-analyzer JSON (upcoming ex-dates)")
    p.add_argument("--risk-lab-json", help="gpw-risk-lab JSON (stress mode; optional)")
    p.add_argument("--input-dir", default=".", help="Where to auto-discover other skills' JSON (default .)")
    p.add_argument("--holding-days", type=_sessions, default=cc.CONTEXT_DEFAULTS["holding_days"],
                   help="Planned holding window in sessions (default 20)")
    p.add_argument("--event-buffer-sessions", type=_nonneg_int, default=cc.CONTEXT_DEFAULTS["event_buffer_sessions"],
                   help="Earnings this many sessions after the window still warn (default 5)")
    p.add_argument("--strict-events", action="store_true",
                   help="NO-GO when a report falls within 2 sessions and its typical gap exceeds the stop distance")
    p.add_argument("--require", type=_require, default=[],
                   help="Comma list of context checks whose UNKNOWN status must fail (e.g. event_window,quality_red_flags)")
    p.add_argument("--override", help="Written reason to proceed despite a bear market with top risk >= 80 (recorded)")
    p.add_argument("--broker", choices=sorted(brokers.BROKERS), help="Commission preset (default mbank, or the sizer's)")
    p.add_argument("--commission-pct", type=_nonneg)
    p.add_argument("--commission-min", type=_nonneg)
    p.add_argument("--max-risk-pct", type=_pos, help="Max risk per trade %% (default 1; capped by exposure-coach)")
    p.add_argument("--max-position-pct", type=_pos, help="Max position %% of account (default 25)")
    p.add_argument("--max-adv-pct", type=_pos, help="Max order %% of median daily turnover (default 10)")
    p.add_argument("--max-heat-pct", type=_pos, help="Max total open risk %% (default 6)")
    p.add_argument("--max-sector-pct", type=_pos, help="Max sector %% after the trade (default 35)")
    p.add_argument("--min-rr", type=_pos, help="Reward/risk below this = WARN (default 1.5)")
    p.add_argument("--as-of", type=_date, help="Evaluate with data up to this date")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--cache-dir")
    p.add_argument("--lang", choices=["pl", "en"], default="pl")
    p.add_argument("--output-dir", default=".")
    p.add_argument("--no-files", action="store_true", help="Print Markdown only, write no files")
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.sizer_json:
        missing = [f for f in ("ticker", "entry", "shares", "account_size") if getattr(args, f) is None]
        if missing:
            parser.error("missing --{} (or pass --sizer-json)".format(", --".join(m.replace("_", "-") for m in missing)))
    if args.override is not None and len(args.override.strip()) < MIN_OVERRIDE_CHARS:
        parser.error("--override needs a written reason of at least {} characters".format(MIN_OVERRIDE_CHARS))
    if args.override:
        args.override = args.override.strip()
    try:
        result = run(args)
    except (ge.GateError, gpw_archive.GPWFetchError, ValueError) as exc:
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
