#!/usr/bin/env python3
"""gpw-quality-screener: fundamental quality screen of GPW companies (PL/EN).

Modes
-----
screen          ranked table of a universe (default WIG20 + mWIG40)
ticker X        full card of one company: every F-score criterion with the
                numbers behind it and a plain-language explanation

Examples (run from the skill directory)::

    python3 scripts/gpw_quality_screener.py screen --lang pl
    python3 scripts/gpw_quality_screener.py screen --index WIG20 --min-f 7 --max-pe 15
    python3 scripts/gpw_quality_screener.py screen --all          # ~400 companies, ~15 min cold
    python3 scripts/gpw_quality_screener.py ticker KETY --lang en
    python3 scripts/gpw_quality_screener.py ticker KETY --esef ~/Downloads/kety-2025-12-31-pl.zip

Exit codes: 0 ok, 1 data / input error (stderr), 2 bad arguments.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from gpwlib import fmt, fundamentals, gpw_archive, http, plcal, provenance  # noqa: E402

import quality_data as qd  # noqa: E402
import quality_engine as qe  # noqa: E402

SKILL = "gpw-quality-screener"
VERSION = "1.0.0"
PREFIX = "gpw_quality_screener"
#: Fundamentals move quarterly and market value daily; a screen whose
#: market data is up to a week old (cached --all run) is still usable, but
#: older than 5 sessions is flagged stale.
MAX_SESSIONS_BEHIND = 5
#: An annual report (ESEF / xBRL) counts as current for 15 months after its
#: year end: 12 months until the next year end + 120 days to publish the
#: next annual report (Rozporządzenie MF z 29.03.2018, par. 79 ust. 1 pkt 3) - 
#: rounded to 456 days.
ANNUAL_MAX_AGE_DAYS = 456

STRINGS = {
    "title_screen": {"pl": "Skaner jakości spółek GPW", "en": "GPW quality screen"},
    "title_ticker": {"pl": "Karta jakości: {t}", "en": "Quality card: {t}"},
    "stale": {"pl": "⚠️ **Dane nieaktualne**: dane rynkowe z {d} ({n} sesji temu).",
              "en": "⚠️ **Stale data**: market data from {d} ({n} sessions ago)."},
    "as_of": {"pl": "Stan na sesję {d}. Uniwersum: {u} ({n} spółek).",
              "en": "As of session {d}. Universe: {u} ({n} companies)."},
    "answer": {"pl": "**Wynik:** {ok} spółek z danymi; jakość policzona dla {q}. Najwyżej: {top}. Czerwone flagi: {rf} spółek.",
               "en": "**Result:** {ok} companies with data; quality scored for {q}. Top: {top}. Red flags: {rf} companies."},
    "coverage_line": {"pl": "Pokrycie F-score: średnio {f}% kryteriów znanych ({c} spółek z pełnym F 9/9). "
                            "Pokrycie wskaźnika jakości: średnio {q}% wag.",
                      "en": "F-score coverage: on average {f}% of criteria known ({c} companies with a full 9/9 F). "
                            "Quality composite coverage: on average {q}% of weights."},
    "thin_history": {"pl": "Lokalna historia nie ma jeszcze okresów sprzed roku, więc 6 z 9 kryteriów F-score (zmiany r/r) "
                           "jest **nieznanych**; F pokazujemy jako zaliczone/znane (np. 2/3), nigdy nie liczymy braków jako 0 ani 1. "
                           "Historia buduje się sama: każde uruchomienie zapisuje okres; po roku F będzie pełny.",
                     "en": "The local history has no periods from a year ago yet, so 6 of the 9 F-score criteria (y/y changes) "
                           "are **unknown**; F is shown as passed/known (e.g. 2/3), missing criteria never count as 0 or 1. "
                           "The history builds itself: every run stores the period; after a year F becomes complete."},
    "h_industrial": {"pl": "Spółki niefinansowe (ranking jakości)", "en": "Non-financial companies (quality ranking)"},
    "h_banks": {"pl": "Banki (F-score nie dotyczy)", "en": "Banks (F-score not applicable)"},
    "h_insurers": {"pl": "Ubezpieczyciele (F-score nie dotyczy)", "en": "Insurers (F-score not applicable)"},
    "h_flags": {"pl": "Czerwone flagi", "en": "Red flags"},
    "h_checks": {"pl": "Kontrola danych źródłowych", "en": "Source data checks"},
    "checks_marker": {"pl": "niespójne dane źródłowe - zob. „Kontrola danych źródłowych”; liczby tej spółki traktuj ostrożnie.",
                      "en": "inconsistent source data - see \"Source data checks\"; treat this company's numbers with care."},
    "stale_marker": {"pl": "okres starszy niż 270 dni (Notoria nie ma nowszego raportu) - pominięty w podsumowaniu i sygnałach.",
                     "en": "period older than 270 days (Notoria has no newer report) - left out of the summary and signals."},
    "checks_intro": {"pl": "Dane tych spółek wyglądają na niespójne w źródle (gpw.pl/Notoria). Liczby pokazujemy bez zmian, ale nie generujemy sygnałów - sprawdź raport okresowy spółki.",
                     "en": "These companies' source data (gpw.pl/Notoria) look inconsistent. Numbers are shown unchanged, but no signals are emitted - check the company's periodic report."},
    "check_equity_near_zero": {"pl": "kapitał własny bliski zera (< 0,5% aktywów)", "en": "equity near zero (< 0.5% of assets)"},
    "check_net_income_above_pretax": {"pl": "zysk netto > 1,5× zysku brutto (ulga podatkowa, działalność zaniechana lub błąd danych)",
                                      "en": "net income > 1.5× pretax profit (tax credit, discontinued operations or data error)"},
    "check_balance_gap": {"pl": "bilans się nie domyka (aktywa − kapitał − zobowiązania poza przedziałem -5%..+50% aktywów)", "en": "balance sheet does not add up (assets − equity − liabilities outside -5%..+50% of assets)"},
    "h_coverage": {"pl": "Pokrycie danych", "en": "Data coverage"},
    "h_limits": {"pl": "Ograniczenia danych", "en": "Data limitations"},
    "h_sources": {"pl": "Źródła / Sources", "en": "Sources"},
    "h_signals": {"pl": "Sygnały (dla gpw-signal-postmortem)", "en": "Signals (for gpw-signal-postmortem)"},
    "h_filtered": {"pl": "Filtry", "en": "Filters"},
    "h_bottom": {"pl": "Najsłabsze 5 (jakość)", "en": "Bottom 5 (quality)"},
    "h_errors": {"pl": "Bez danych", "en": "No data"},
    "filters_line": {"pl": "Filtry: {f}. Odrzucone: {r}.", "en": "Filters: {f}. Excluded: {r}."},
    "none": {"pl": "brak", "en": "none"},
    "no_flags": {"pl": "Brak czerwonych flag w spółkach z danymi.", "en": "No red flags among companies with data."},
    "col_ind": {"pl": "| # | Spółka | Sektor | Okres | F (zal./znane) | Jakość | Pctl sektor | ROE | ROIC≈ | Marża op. | FCF/przych. | Akruały | C/Z | C/WK | Dyw. % | Flagi |",
                "en": "| # | Ticker | Sector | Period | F (passed/known) | Quality | Sector pctl | ROE | ROIC≈ | Op. margin | FCF margin | Accruals | P/E | P/BV | Div. % | Flags |"},
    "col_bank": {"pl": "| # | Bank | Okres | Jakość | ROE | ROA | Kapitał/aktywa | C/WK | C/WK wg ROE | Odchylenie | C/Z | Dyw. % | Flagi |",
                 "en": "| # | Bank | Period | Quality | ROE | ROA | Equity/assets | P/BV | P/BV fitted on ROE | Residual | P/E | Div. % | Flags |"},
    "col_ins": {"pl": "| Spółka | Okres | Jakość | ROE | ROA | Kapitał/aktywa | C/WK | C/Z | Dyw. % | Flagi |",
                "en": "| Ticker | Period | Quality | ROE | ROA | Equity/assets | P/BV | P/E | Div. % | Flags |"},
    "bank_fit": {"pl": "Regresja C/WK = a + b·ROE dla {n} banków: a = {a}, b = {b}, R² = {r2}. Odchylenie < 0 = taniej niż rówieśnicy przy tym ROE (to nie rekomendacja).",
                 "en": "Regression P/BV = a + b·ROE over {n} banks: a = {a}, b = {b}, R² = {r2}. Residual < 0 = cheaper than peers for its ROE (not a recommendation)."},
    "bank_nofit": {"pl": "Za mało banków (< 4) na regresję C/WK vs ROE.", "en": "Too few banks (< 4) for the P/BV vs ROE regression."},
    "cov_table": {"pl": "| Miara | Wartość |\n|---|---|", "en": "| Measure | Value |\n|---|---|"},
    "stale_period": {"pl": "przeterminowany okres", "en": "stale period"},
    "limits": {"pl": [
        "Notoria (karta spółki na gpw.pl) pokazuje **tylko ostatni okres**; parametry okresu są ignorowane (sprawdzone 2026-11-16).",
        "Rachunek wyników i przepływy są **narastające od początku roku** (YTD); wskaźniki roczne = YTD × 12/miesiące (lub TTM, gdy historia ma rok poprzedni) - sezonowość może zniekształcać.",
        "Brak gotówki i długu w Notorii: **dług netto, EV/EBITDA, EV/Sprzedaż = brak** (chyba że podasz raport ESEF).",
        "Brak kosztów finansowych: pokrycie odsetek to przybliżenie EBIT / (EBIT − zysk brutto).",
        "Brak zysku brutto ze sprzedaży: marża (kryterium 8 F) i rentowność brutto (Novy-Marx) liczone na zysku ze sprzedaży - przybliżenie.",
        "Zysk netto i kapitał to część przypadająca akcjonariuszom jednostki dominującej; dane zaokrąglone do 1 mln zł.",
        "Spółki mają różne okresy (H1, 9M, rok obrotowy LPP/Pepco); okresy starsze niż 270 dni są oznaczone jako przeterminowane i nie dają sygnałów.",
    ], "en": [
        "Notoria (gpw.pl company card) shows **the latest period only**; period parameters are ignored (checked 2026-11-16).",
        "P&L and cash flow are **year-to-date**; annual ratios = YTD × 12/months (or TTM when the history holds the prior year) - seasonality can distort them.",
        "Notoria has no cash or debt: **net debt, EV/EBITDA, EV/Sales = n/a** (unless you pass an ESEF report).",
        "No finance costs: interest coverage is the proxy EBIT / (EBIT − pretax profit).",
        "No gross profit: the margin criterion (F #8) and gross profitability (Novy-Marx) use profit on sales - a proxy.",
        "Net income and equity are attributable to owners of the parent; figures rounded to PLN 1 m.",
        "Periods differ between companies (H1, 9M, fiscal years of LPP/Pepco); periods older than 270 days are flagged stale and give no signals.",
    ]},
    "sources": {"pl": [
        "gpw.pl, karta spółki - zakładki Wskaźniki i Dane finansowe (Notoria): https://www.gpw.pl/spolka?isin=<ISIN>",
        "GPW Benchmark - skład indeksów: https://gpwbenchmark.pl",
        "Lokalna historia snapshotów: ~/.cache/gpw-trading-skills/fundamentals/history/",
        "Metodologia: references/methodology.md (Piotroski 2000, Sloan 1996, Novy-Marx 2013, Asness-Frazzini-Pedersen 2019).",
    ], "en": [
        "gpw.pl company card - Indicators and Financial data (Notoria) tabs: https://www.gpw.pl/spolka?isin=<ISIN>",
        "GPW Benchmark - index portfolios: https://gpwbenchmark.pl",
        "Local snapshot history: ~/.cache/gpw-trading-skills/fundamentals/history/",
        "Methodology: references/methodology.md (Piotroski 2000, Sloan 1996, Novy-Marx 2013, Asness-Frazzini-Pedersen 2019).",
    ]},
    "req": {"pl": "Zapytania HTTP w tym uruchomieniu: {n} (czas {s} s).", "en": "HTTP requests in this run: {n} ({s} s)."},
    # ---- ticker card
    "card_summary": {"pl": "**W skrócie:** {s}", "en": "**In short:** {s}"},
    "card_type": {"pl": "Typ: {t}. Sektor GPW: {s}. Okres danych: {p} ({c}).",
                  "en": "Type: {t}. GPW sector: {s}. Data period: {p} ({c})."},
    "type_industrial": {"pl": "spółka niefinansowa", "en": "non-financial company"},
    "type_bank": {"pl": "bank", "en": "bank"},
    "type_insurer": {"pl": "ubezpieczyciel", "en": "insurer"},
    "consolidated": {"pl": "skonsolidowane", "en": "consolidated"},
    "standalone": {"pl": "jednostkowe", "en": "standalone"},
    "h_f": {"pl": "Piotroski F-score - kryterium po kryterium", "en": "Piotroski F-score - criterion by criterion"},
    "f_head": {"pl": "| # | Pytanie | Wynik | Teraz | Rok wcześniej | Uwagi |\n|---|---|---|---|---|---|",
               "en": "| # | Question | Result | Now | Year earlier | Notes |\n|---|---|---|---|---|---|"},
    "f_total": {"pl": "**F-score: {d}** (zaliczone/znane), pokrycie {c}% kryteriów, przedział możliwy {lo}-{hi} z 9. {band}",
                "en": "**F-score: {d}** (passed/known), coverage {c}% of criteria, possible range {lo}-{hi} of 9. {band}"},
    "f_source": {"pl": "Okres porównawczy: {p} (źródło: {s}).", "en": "Comparison period: {p} (source: {s})."},
    "f_noprior": {"pl": "Brak okresu porównawczego (ten sam okres rok wcześniej) - kryteria zmian są nieznane.",
                  "en": "No comparison period (same window a year earlier) - change criteria are unknown."},
    "band_high": {"pl": "To grupa „wysokiego F” (8-9) z badania Piotroskiego.", "en": "This is Piotroski's \"high F\" group (8-9)."},
    "band_low": {"pl": "To grupa „niskiego F” (0-1) z badania Piotroskiego.", "en": "This is Piotroski's \"low F\" group (0-1)."},
    "band_middle": {"pl": "Wynik pośrodku skali.", "en": "A middle score."},
    "band_undetermined": {"pl": "Przy brakujących kryteriach grupa (wysoki/niski F) jest nieustalona.",
                          "en": "With missing criteria the group (high/low F) is undetermined."},
    "f_na": {"pl": "F-score nie dotyczy banków i ubezpieczycieli (inna struktura bilansu: depozyty, rezerwy). Ocena oparta na ROE, ROA i kapitale.",
             "en": "The F-score does not apply to banks and insurers (different balance sheet: deposits, reserves). The score uses ROE, ROA and capital."},
    "pass": {"pl": "✅ tak", "en": "✅ pass"},
    "fail": {"pl": "❌ nie", "en": "❌ fail"},
    "unknown": {"pl": "❔ brak danych", "en": "❔ unknown"},
    "proxy": {"pl": "przybliżenie: {p}", "en": "proxy: {p}"},
    "crit_roa_positive": {"pl": "Czy spółka zarabia? (zysk netto > 0)", "en": "Is it profitable? (net income > 0)"},
    "crit_cfo_positive": {"pl": "Czy z działalności wpływa gotówka? (przepływy operacyjne > 0)",
                          "en": "Does operations bring in cash? (operating cash flow > 0)"},
    "crit_roa_improving": {"pl": "Czy rentowność aktywów (ROA) wzrosła r/r?", "en": "Did return on assets (ROA) rise y/y?"},
    "crit_accruals": {"pl": "Czy gotówka z działalności > zysk netto? (zysk „pokryty gotówką”)",
                      "en": "Is operating cash flow > net income? (earnings backed by cash)"},
    "crit_leverage_down": {"pl": "Czy zadłużenie długoterminowe / aktywa spadło?", "en": "Did long-term debt / assets fall?"},
    "crit_liquidity_up": {"pl": "Czy płynność bieżąca (aktywa obr. / zob. krótkoterm.) wzrosła?",
                          "en": "Did the current ratio (current assets / current liabilities) rise?"},
    "crit_no_equity_issue": {"pl": "Czy spółka nie emitowała nowych akcji?", "en": "Did the company avoid issuing new shares?"},
    "crit_margin_up": {"pl": "Czy marża wzrosła r/r?", "en": "Did the margin rise y/y?"},
    "crit_turnover_up": {"pl": "Czy rotacja aktywów (przychody / aktywa) wzrosła?", "en": "Did asset turnover (revenue / assets) rise?"},
    "h_metrics": {"pl": "Wskaźniki", "en": "Metrics"},
    "m_head": {"pl": "| Wskaźnik | Wartość | Co to znaczy |\n|---|---|---|", "en": "| Metric | Value | What it means |\n|---|---|---|"},
    "h_valuation": {"pl": "Wycena (mnożniki)", "en": "Valuation (multiples)"},
    "h_quality": {"pl": "Ocena jakości 0-100", "en": "Quality score 0-100"},
    "q_line": {"pl": "**Jakość: {s}/100** (pokrycie wag {c}%; miejsce {r} z {n} w swojej grupie; percentyl w sektorze: {ps}).",
               "en": "**Quality: {s}/100** (weights covered {c}%; rank {r} of {n} in its group; sector percentile: {ps})."},
    "q_comp_head": {"pl": "| Składnik | Wartość | Punkty 0-100 | Waga | Podstawa |\n|---|---|---|---|---|",
                    "en": "| Component | Value | Points 0-100 | Weight | Basis |\n|---|---|---|---|---|"},
    "h_annual": {"pl": "Raporty roczne (ESEF / filings.xbrl.org)", "en": "Annual reports (ESEF / filings.xbrl.org)"},
    "annual_line": {"pl": "{src}: {lab} vs rok wcześniej - F {d} (pokrycie {c}%). Dług netto {nd}, dług netto/EBITDA {nde}.",
                    "en": "{src}: {lab} vs prior year - F {d} (coverage {c}%). Net debt {nd}, net debt/EBITDA {nde}."},
    "xbrl_stale": {"pl": "⚠️ filings.xbrl.org ma polskie raporty tylko za lata 2020-2023 - to dane **historyczne**, nie bieżące.",
                   "en": "⚠️ filings.xbrl.org holds Polish reports for FY2020-2023 only - this is **historical**, not current data."},
    "xbrl_nolei": {"pl": "filings.xbrl.org: nie znaleziono jednoznacznego LEI dla {t}; podaj --lei.",
                   "en": "filings.xbrl.org: no unique LEI found for {t}; pass --lei."},
    "xbrl_nofiling": {"pl": "filings.xbrl.org: brak raportu rocznego dla LEI {l}.", "en": "filings.xbrl.org: no annual filing for LEI {l}."},
    "sum_good": {"pl": "zyskowna, gotówka z działalności pokrywa zysk", "en": "profitable, earnings backed by operating cash"},
    "sum_loss": {"pl": "spółka ma stratę netto", "en": "the company is loss-making"},
    "sum_cfo_neg": {"pl": "przepływy operacyjne są ujemne", "en": "operating cash flow is negative"},
    "sum_accr": {"pl": "zysk wyższy niż gotówka z działalności (uważaj na jakość zysku)",
                 "en": "earnings exceed operating cash (watch earnings quality)"},
    "sum_flags": {"pl": "czerwone flagi: {f}", "en": "red flags: {f}"},
    "sum_noflags": {"pl": "bez czerwonych flag", "en": "no red flags"},
    "sum_q": {"pl": "jakość {s}/100", "en": "quality {s}/100"},
    "sum_qna": {"pl": "jakość nieoceniona (za mało danych)", "en": "quality not scored (too little data)"},
    "explain_title": {"pl": "Jak to czytać (dla początkujących)", "en": "How to read this (for beginners)"},
    "explain": {"pl": [
        "**F-score** (Piotroski) to 9 prostych pytań „tak/nie” o zyskowność, zadłużenie i efektywność. Więcej „tak” = zdrowsza i poprawiająca się spółka. To nie jest prognoza kursu.",
        "**ROE** - ile zysku spółka wypracowuje na 100 zł kapitału akcjonariuszy rocznie. **ROIC≈** - zysk operacyjny po podatku na zainwestowany kapitał.",
        "**Akruały** - część zysku, za którą nie stoi gotówka. Wysokie akruały często poprzedzają słabsze wyniki (Sloan 1996).",
        "**Ocena jakości 0-100** w skanerze porównuje spółkę z innymi z tego samego skanu (percentyle); dla pojedynczej spółki używa stałej skali opisanej w metodologii.",
    ], "en": [
        "**F-score** (Piotroski) is 9 simple yes/no questions about profitability, leverage and efficiency. More \"yes\" = a healthier, improving company. It is not a price forecast.",
        "**ROE** - profit per PLN 100 of shareholders' equity per year. **ROIC≈** - after-tax operating profit on invested capital.",
        "**Accruals** - the part of profit not backed by cash. High accruals often precede weaker results (Sloan 1996).",
        "**Quality 0-100** in the screen compares the company with the others in the same screen (percentiles); for a single company it uses the fixed scale documented in the methodology.",
    ]},
    "flag_negative_equity": {"pl": "ujemny kapitał własny", "en": "negative equity"},
    "flag_loss_making": {"pl": "strata netto", "en": "loss-making"},
    "flag_cfo_negative_with_profit": {"pl": "zysk przy ujemnych przepływach operacyjnych", "en": "profit with negative operating cash flow"},
    "flag_high_accruals": {"pl": "wysokie akruały (> 10% aktywów)", "en": "high accruals (> 10% of assets)"},
    "flag_net_debt_ebitda_high": {"pl": "dług netto/EBITDA > 3,5", "en": "net debt/EBITDA > 3.5"},
    "flag_net_debt_with_negative_ebitda": {"pl": "dług netto przy ujemnej EBITDA", "en": "net debt with negative EBITDA"},
    "flag_dilution": {"pl": "rozwodnienie (> 5% akcji r/r)", "en": "dilution (> 5% shares y/y)"},
    "comp_roic_approx": {"pl": "ROIC≈", "en": "ROIC≈"},
    "comp_roa": {"pl": "ROA", "en": "ROA"},
    "comp_roe": {"pl": "ROE", "en": "ROE"},
    "comp_gross_profitability": {"pl": "Rentowność brutto / aktywa (przybl.)", "en": "Gross profitability (proxy)"},
    "comp_accruals_ratio": {"pl": "Akruały / aktywa", "en": "Accruals / assets"},
    "comp_fcf_margin": {"pl": "FCF / przychody", "en": "FCF margin"},
    "comp_liabilities_to_assets": {"pl": "Zobowiązania / aktywa", "en": "Liabilities / assets"},
    "comp_interest_coverage_score_input": {"pl": "Pokrycie kosztów fin. (przybl.)", "en": "Finance cost coverage (proxy)"},
    "comp_f_ratio": {"pl": "F-score (zal./znane)", "en": "F-score (passed/known)"},
    "comp_equity_to_assets": {"pl": "Kapitał / aktywa", "en": "Equity / assets"},
    "basis_absolute": {"pl": "skala bezwzględna", "en": "absolute scale"},
    "basis_rank": {"pl": "percentyl w grupie", "en": "percentile in group"},
}

METRIC_ROWS = [  # (key, kind, PL meaning, EN meaning)
    ("roe", "pct", "zysk / kapitał własny (rocznie)", "net income / equity (annual)"),
    ("roa", "pct", "zysk / aktywa", "net income / assets"),
    ("roic_approx", "pct", "EBIT×(1−19%) / (aktywa − zob. krótkoterm.)", "EBIT×(1−19%) / (assets − current liabilities)"),
    ("operating_margin", "pct", "zysk operacyjny / przychody", "operating profit / revenue"),
    ("net_margin", "pct", "zysk netto / przychody", "net income / revenue"),
    ("ebitda_margin", "pct", "EBITDA / przychody", "EBITDA / revenue"),
    ("gross_profitability", "pct", "zysk ze sprzedaży / aktywa (Novy-Marx, przybl.)", "profit on sales / assets (Novy-Marx, proxy)"),
    ("fcf_ann", "mln", "wolne przepływy: przepływy oper. + capex (rocznie)", "free cash flow: CFO + capex (annual)"),
    ("fcf_margin", "pct", "FCF / przychody", "FCF / revenue"),
    ("cash_conversion", "x", "przepływy oper. / zysk netto", "CFO / net income"),
    ("accruals_ratio", "pct", "(zysk − przepływy oper.) / aktywa (Sloan)", "(NI − CFO) / assets (Sloan)"),
    ("liabilities_to_assets", "pct", "zobowiązania / aktywa", "liabilities / assets"),
    ("current_ratio", "x", "aktywa obrotowe / zob. krótkoterminowe", "current assets / current liabilities"),
    ("interest_coverage_proxy", "x", "EBIT / (EBIT − zysk brutto)", "EBIT / (EBIT − pretax)"),
    ("net_debt_ebitda", "x", "dług netto / EBITDA (tylko z ESEF/xBRL)", "net debt / EBITDA (ESEF/xBRL only)"),
    ("equity_to_assets", "pct", "kapitał / aktywa", "equity / assets"),
    ("share_change_pct", "pp", "zmiana liczby akcji r/r", "share count change y/y"),
]
VALUATION_ROWS = [
    ("market_cap", "mln", "kapitalizacja (gpw.pl)", "market value (gpw.pl)"),
    ("pe_gpw", "x", "C/Z wg GPW", "P/E per GPW"),
    ("pe_calc", "x", "kapitalizacja / zysk roczny", "market cap / annual net income"),
    ("pbv_gpw", "x", "C/WK wg GPW", "P/BV per GPW"),
    ("ev_ebitda", "x", "EV / EBITDA (wymaga długu netto)", "EV / EBITDA (needs net debt)"),
    ("ev_sales", "x", "EV / przychody (wymaga długu netto)", "EV / sales (needs net debt)"),
    ("fcf_yield", "pct", "FCF / kapitalizacja", "FCF / market cap"),
    ("earnings_yield", "pct", "zysk / kapitalizacja", "earnings / market cap"),
    ("dividend_yield_pct", "pp", "stopa dywidendy (GPW)", "dividend yield (GPW)"),
]

REQUESTS = {"n": 0}


def _install_counters():
    """Count real network requests (cache hits are free)."""
    if getattr(http, "_qs_counted", False):
        return
    orig_fetch, orig_get = http._fetch, gpw_archive._http_get

    def fetch(*a, **k):
        REQUESTS["n"] += 1
        return orig_fetch(*a, **k)

    def aget(*a, **k):
        REQUESTS["n"] += 1
        return orig_get(*a, **k)

    http._fetch, gpw_archive._http_get = fetch, aget
    http._qs_counted = True


# --------------------------------------------------------------------------
# Arguments
# --------------------------------------------------------------------------

def _date(text):
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD, got {!r}".format(text))


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lang", choices=["pl", "en"], default="pl")
    common.add_argument("--output-dir", default=".")
    common.add_argument("--no-files", action="store_true", help="print the Markdown, write nothing")
    common.add_argument("--cache-dir", default=None, help="HTTP / archive cache (default ~/.cache/gpw-trading-skills)")
    common.add_argument("--history-dir", default=None,
                        help="fundamentals history store (default <cache>/fundamentals/history)")
    common.add_argument("--no-save-history", action="store_true", help="do not store this run's snapshots")
    common.add_argument("--offline", action="store_true", help="cache only, no network")
    common.add_argument("--refresh", action="store_true", help="ignore cached company cards")
    common.add_argument("--as-of", type=_date, default=None,
                        help="point-in-time run from the local history (YYYY-MM-DD); no company data fetched")
    p = argparse.ArgumentParser(prog="gpw_quality_screener.py",
                                description="GPW fundamental quality screen: Piotroski F-score, ROE/ROIC, accruals, red flags (PL/EN)")
    sub = p.add_subparsers(dest="mode")
    sub.required = True
    sc = sub.add_parser("screen", parents=[common], help="ranked table of a universe")
    uni = sc.add_mutually_exclusive_group()
    uni.add_argument("--tickers", default=None, help="comma-separated tickers (archive names, e.g. KETY,PKOBP)")
    uni.add_argument("--index", default=None, help="WIG20, mWIG40, sWIG80 or WIG; comma-separated for several")
    uni.add_argument("--all", action="store_true", help="every Main Market share (~400; ~15 min cold, cards cached 7 days)")
    sc.add_argument("--min-f", type=int, default=None, help="keep companies whose F lower bound >= N (0-9)")
    sc.add_argument("--max-pe", type=float, default=None, help="keep P/E <= X (loss-makers excluded)")
    sc.add_argument("--min-roe", type=float, default=None, help="keep ROE >= X percent")
    sc.add_argument("--top", type=int, default=25, help="rows per table (default 25)")
    tk = sub.add_parser("ticker", parents=[common], help="full card of one company")
    tk.add_argument("ticker")
    tk.add_argument("--isin", default=None, help="ISIN when the ticker is not in the current listing")
    tk.add_argument("--esef", default=None, help="ESEF annual report zip downloaded from the company's website")
    tk.add_argument("--xbrl", action="store_true", help="also look up filings.xbrl.org (FY2020-2023 only, stale)")
    tk.add_argument("--lei", default=None, help="LEI for --xbrl when the name match fails")
    return p


def parse_args(argv=None):
    p = build_parser()
    a = p.parse_args(argv)
    if a.mode == "screen":
        if a.min_f is not None and not 0 <= a.min_f <= 9:
            p.error("--min-f must be between 0 and 9")
        if a.max_pe is not None and a.max_pe <= 0:
            p.error("--max-pe must be > 0")
        if a.top < 1:
            p.error("--top must be >= 1")
        if a.tickers is not None and not [t for t in a.tickers.split(",") if t.strip()]:
            p.error("--tickers is empty")
    if a.mode == "ticker":
        if a.lei and not a.xbrl:
            p.error("--lei needs --xbrl")
        if a.isin and not fundamentals.isin_is_valid(a.isin):
            p.error("--isin {!r} is not a valid ISIN".format(a.isin))
    if a.as_of and a.as_of > plcal.today_warsaw():
        p.error("--as-of cannot be in the future")
    return a


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def _stale_days(st: Optional[Dict], as_of: dt.date) -> Optional[int]:
    try:
        return (as_of - dt.date.fromisoformat(st["period_end"])).days
    except (TypeError, ValueError, KeyError):
        return None


def analyse_company(snap: Dict, hist: Optional[Dict], as_of: dt.date, banks: List[str],
                    annual: Optional[List[Dict]] = None) -> Dict:
    """One company: statements -> F-score, metrics, red flags (scores later)."""
    t = snap["ticker"]
    ind = snap.get("indicators") or {}
    cur = qe.statement_from_notoria(snap.get("notoria"))
    stored = [s for s in qd.history_statements(hist, as_of) if not cur or s.get("label") != cur.get("label")]
    pri = qd.prior_from_history(cur, stored)
    prior = pri["prior"]
    ttm = qe.ttm_from(cur, pri["prev_fy"], prior) if cur else None
    cls = qe.classify_sector(ind.get("sector"), t, banks, cur)
    stype = cls["sector_type"]
    ind_day = (snap.get("indicators_retrieved_at") or snap.get("retrieved_at") or as_of.isoformat())[:10]
    try:
        when = dt.date.fromisoformat(ind_day)
    except ValueError:
        when = as_of
    shares_now = ind.get("shares_issued")
    shares_then = qd.shares_year_ago(hist, when)
    # F-score: Notoria vs history first, then ESEF / xBRL annual pairs
    f_variants = []
    if stype == "industrial":
        f_hist = qe.piotroski(cur, prior, shares_now, shares_then)
        f_hist["source"] = "notoria_vs_history"
        f_variants.append(f_hist)
        for a in annual or []:
            if a.get("current"):
                fv = qe.piotroski(a["current"], a.get("prior"))
                fv["source"] = a["source"]
                age = _stale_days(a["current"], as_of)
                # filings.xbrl.org stops at FY2023; an annual report older than
                # 15 months (456 days) is a past year, not the current state
                fv["stale"] = a["source"] == "xbrl" or (age is not None and age > ANNUAL_MAX_AGE_DAYS)
                f_variants.append(fv)
        f = f_hist
        if f_hist["available"] < 9:
            # only a recent annual report may replace the Notoria-based F;
            # stale ones are shown separately in the card
            rich = [v for v in f_variants[1:] if not v["stale"] and v["available"] > f_hist["available"]]
            rich.sort(key=lambda v: (v["current_label"] or ""), reverse=True)
            if rich:
                f = rich[0]
    else:
        f = qe.not_applicable_f("financial_" + stype)
        f["source"] = None
    # net debt from the newest annual report when it is recent (<= 15 months)
    override = None
    for a in sorted(annual or [], key=lambda a: (a.get("current") or {}).get("period_end") or "", reverse=True):
        c = a.get("current") or {}
        if c.get("cash") is not None and c.get("borrowings") is not None:
            age = _stale_days(c, as_of)
            if age is not None and age <= ANNUAL_MAX_AGE_DAYS:
                override = {"net_debt": c["borrowings"] + (c.get("lease_liabilities") or 0.0) - c["cash"],
                            "ebitda": c.get("ebitda"), "period_end": c.get("period_end"), "source": a["source"]}
            break
    market = {"market_cap": ind.get("market_cap_pln"), "pe_gpw": ind.get("pe"), "pbv_gpw": ind.get("pbv"),
              "dividend_yield_pct": ind.get("dividend_yield_pct"), "shares_now": shares_now,
              "shares_then": shares_then}
    metrics = qe.compute_metrics(cur, prior, market, stype, ttm, override)
    flags = qe.red_flags(cur, metrics, stype)
    age = _stale_days(cur, as_of)
    stale = age is not None and age > qe.STALE_PERIOD_DAYS
    status = "ok" if cur else "no_notoria"
    row = {
        "ticker": t, "isin": snap.get("isin"), "sector": ind.get("sector"), "sector_type": stype,
        "financial_other": cls["financial_other"], "classification": cls["reason"],
        "status": status, "period": cur.get("label") if cur else None,
        "period_end": cur.get("period_end") if cur else None, "period_age_days": age, "stale_period": stale,
        "consolidated": cur.get("consolidated") if cur else None,
        "f_score": f.get("score"), "f": f, "f_variants": f_variants,
        "red_flags": flags, "data_checks": qe.data_checks(cur, stype), "metrics": metrics,
        "inputs": qe.score_inputs(metrics, f),
        "prior_period": {"source": "history" if prior else None, "label": prior.get("label") if prior else None,
                         "ttm": bool(ttm)},
        "data": {"retrieved_at": snap.get("retrieved_at"), "indicators_retrieved_at": snap.get("indicators_retrieved_at"),
                 "sources": snap.get("sources") or [], "warnings": snap.get("warnings") or [],
                 "from_cache": snap.get("from_cache")},
        "_cur": cur, "_prior": prior,
    }
    return row


def _universe_label(args) -> str:
    if args.mode == "ticker":
        return args.ticker.upper()
    if args.tickers:
        return "tickers"
    if args.all:
        return "all_main_market"
    return "+".join(qd.normalise_index(i) for i in (args.index.split(",") if args.index else qd.DEFAULT_INDICES))


def run(args) -> Dict:
    _install_counters()
    REQUESTS["n"] = 0
    t0 = time.time()
    cache = Path(args.cache_dir).expanduser() if args.cache_dir else None
    store = Path(args.history_dir).expanduser() if args.history_dir else (
        (cache / "fundamentals" / "history") if cache else None)
    historical = args.as_of is not None and args.as_of < plcal.today_warsaw()
    as_of_session = qd.last_final_session(args.as_of or plcal.today_warsaw())
    if args.mode == "ticker":
        universe, errs = qd.resolve_universe([args.ticker], cache_dir=cache, offline=args.offline, isin=args.isin)
        if errs:
            raise qd.DataError(errs[0]["error"])
    else:
        tickers = [t for t in (args.tickers or "").split(",") if t.strip()] or None
        indices = [i for i in (args.index or "").split(",") if i.strip()] or None
        universe, errs = qd.resolve_universe(tickers, indices, args.all, cache, args.offline)
        if not universe:
            raise qd.DataError("No company could be resolved: {}".format("; ".join(e["error"] for e in errs)))
    banks = qd.bank_members(cache, args.offline)
    ttl_ind = 0 if args.refresh else (qd.ALL_TTL_S if getattr(args, "all", False) else qd.INDICATORS_TTL_S)
    ttl_noto = 0 if args.refresh else qd.NOTORIA_TTL_S
    annual: List[Dict] = []
    card_notes: List[str] = []
    if args.mode == "ticker":
        if args.esef:
            e = qd.esef_pair(args.esef)
            annual.append(dict(e, source="esef"))
        if args.xbrl:
            code = None
            x = qd.xbrl_pair(universe[0]["ticker"], args.lei, cache, args.offline, code)
            if x.get("note"):
                card_notes.append(x["note"])
            annual.append(dict(x, source="xbrl"))
    rows: List[Dict] = []
    hist_status: Dict[str, int] = {}
    n = len(universe)
    for k, u in enumerate(universe, 1):
        if n > 10 and k % 10 == 0:
            print("  ... {}/{} companies ({} HTTP requests)".format(k, n, REQUESTS["n"]), file=sys.stderr)
        hist = qd.load_history(u["ticker"], store)
        try:
            if historical:
                snap = qd.point_in_time_snapshot(u["ticker"], hist, args.as_of)
                if snap is None:
                    raise fundamentals.FundamentalsError("no local history on or before {}".format(args.as_of))
            else:
                snap = qd.fetch_snapshot(u["ticker"], u["isin"], cache, args.offline, ttl_ind, ttl_noto)
                if not args.no_save_history:
                    res = fundamentals.save_snapshot(snap, store)
                    hist_status[res["status"]] = hist_status.get(res["status"], 0) + 1
                    hist = qd.load_history(u["ticker"], store)
        except (http.HTTPFetchError, fundamentals.FundamentalsError) as exc:
            if args.mode == "ticker":
                raise qd.DataError("{}: {}".format(u["ticker"], exc))
            rows.append({"ticker": u["ticker"], "isin": u["isin"], "status": "error", "error": str(exc),
                         "sector_type": "industrial", "f_score": None, "quality_score": None, "red_flags": [],
                         "metrics": {}, "inputs": {}, "f": qe.not_applicable_f("no_data")})
            continue
        row = analyse_company(snap, hist, as_of_session, banks, annual)
        # signal date: session when the current period first appeared in the store
        seen = qd.first_seen(hist, row["period"])
        sd = qd.session_known_at(seen) if seen else None
        if sd and sd <= as_of_session:
            row["signal_date"] = sd.isoformat()
            px = qd.close_on(sd, row["ticker"], cache)
            if px:
                row["signal_price"] = px
        rows.append(row)
    ok = [r for r in rows if r.get("status") == "ok"]
    qe.composite_scores(ok)
    qe.sector_metric_ranks(ok)
    fit = qe.bank_pbv_regression(ok)
    for r in rows:
        r["quality_score"] = (r.get("quality") or {}).get("score")
    signals = qe.build_signals(ok, as_of_session.isoformat())
    # filters (screen)
    excluded: Dict[str, int] = {}
    if args.mode == "screen":
        for r in ok:
            keep, why = qe.passes_filters(r, args.min_f, args.max_pe, args.min_roe)
            r["passes_filters"] = keep
            if not keep:
                excluded[why] = excluded.get(why, 0) + 1
    # data dates
    ind_days = sorted({(r.get("data") or {}).get("indicators_retrieved_at", "")[:10] for r in ok
                       if (r.get("data") or {}).get("indicators_retrieved_at")})
    oldest = dt.date.fromisoformat(ind_days[0]) if ind_days else as_of_session
    data_session = qd.last_final_session(min(oldest, as_of_session)) if not historical else as_of_session
    if not historical and data_session > as_of_session:
        data_session = as_of_session
    cov = coverage_stats(universe, errs, rows, hist_status)
    if args.mode == "ticker" and ok and ok[0]["stale_period"]:
        card_notes.append("stale_period")
    notes = [
        "Notoria (gpw.pl card) shows only the latest period; period parameters are ignored (checked 2026-11-16).",
        "Flows are year-to-date; annual ratios = TTM when the history holds the prior year, else YTD x 12/months.",
        "Net debt and EV multiples need cash/debt lines that Notoria does not print (ESEF/xBRL only).",
        "Market value as retrieved from gpw.pl (indicators tab, cached up to {} days).".format(
            7 if getattr(args, "all", False) else 1),
        "Universe = current index portfolios / listing (not point-in-time).",
    ]
    if historical:
        notes.append("Point-in-time run from the local history store (no company data fetched).")
    prov = provenance.build(
        as_of=data_session, sources=["https://www.gpw.pl/spolka?isin=<ISIN> (indicatorsTab, showNotoria / Notoria)",
                                     "https://gpwbenchmark.pl (index portfolios)"]
        + (["https://filings.xbrl.org (FY2020-2023, stale)"] if any(a["source"] == "xbrl" for a in annual) else [])
        + (["ESEF file: {}".format(a.get("path")) for a in annual if a["source"] == "esef"]),
        sessions_used=1, universe_size=len(universe), notes=notes, corporate_actions_adjusted=False,
        survivorship_free=False, point_in_time_membership=False,
        reference_date=args.as_of if historical else None, max_sessions_behind=MAX_SESSIONS_BEHIND)
    companies = [public_company(r) for r in sorted(rows, key=_order_key)]
    result = {
        "skill": SKILL, "version": VERSION, "lang": args.lang, "mode": args.mode,
        "as_of": data_session.isoformat(),
        "universe": {"spec": _universe_label(args), "size": len(universe),
                     "unresolved": [e["ticker"] for e in errs]},
        "filters": ({"min_f": args.min_f, "max_pe": args.max_pe, "min_roe_pct": args.min_roe, "excluded": excluded}
                    if args.mode == "screen" else None),
        "companies": companies,
        "coverage": cov,
        "bank_regression": fit,
        "signals": signals,
        "warnings": card_notes,
        "run": {"http_requests": REQUESTS["n"], "runtime_s": round(time.time() - t0, 1),
                "history_store": str(fundamentals.history_dir(store)), "history": hist_status,
                "historical": historical},
        "data_provenance": prov,
        "_rows": rows, "_annual": annual,
    }
    return result


def _order_key(r):
    q = (r.get("quality") or {}).get("score")
    return ({"industrial": 0, "bank": 1, "insurer": 2}.get(r.get("sector_type"), 3), q is None,
            -(q or 0), r["ticker"])


def public_company(r: Dict) -> Dict:
    out = {k: v for k, v in r.items() if not k.startswith("_") and k != "inputs"}
    f = out.pop("f", None) or {}
    out["f_score_partial"] = {k: f.get(k) for k in ("applicable", "passed", "available", "ratio", "coverage_pct",
                                                    "lower", "upper", "band", "display", "source", "stale",
                                                    "current_label", "prior_label", "reason") if k in f}
    out["f_criteria"] = f.get("criteria") or []
    variants = out.pop("f_variants", None) or []
    out["f_variants"] = [{k: v.get(k) for k in ("source", "display", "available", "score", "current_label",
                                                "prior_label", "stale")} for v in variants]
    return out


def coverage_stats(universe, errs, rows, hist_status) -> Dict:
    ok = [r for r in rows if r.get("status") == "ok"]
    ind = [r for r in ok if r["sector_type"] == "industrial"]
    fav = [r["f"]["available"] for r in ind]
    dist: Dict[str, int] = {}
    for a in fav:
        dist[str(a)] = dist.get(str(a), 0) + 1
    qc = [r["quality"]["coverage_pct"] for r in ok if r.get("quality")]
    return {
        "requested": len(universe) + len(errs), "resolved": len(universe), "unresolved": len(errs),
        "with_data": len(ok), "no_notoria": sum(1 for r in rows if r.get("status") == "no_notoria"),
        "errors": sum(1 for r in rows if r.get("status") == "error"),
        "coverage_pct": round(100.0 * len(ok) / (len(universe) + len(errs)), 1) if (universe or errs) else 0.0,
        "by_sector_type": {t: sum(1 for r in ok if r["sector_type"] == t) for t in qe.SECTOR_TYPES},
        "stale_periods": sum(1 for r in ok if r.get("stale_period")),
        "data_check_failures": sum(1 for r in ok if r.get("data_checks")),
        "f_complete": sum(1 for a in fav if a == 9),
        "f_available_distribution": dict(sorted(dist.items())),
        "mean_f_coverage_pct": round(100.0 * sum(fav) / (9 * len(fav)), 1) if fav else None,
        "prior_period_found": sum(1 for r in ok if (r.get("prior_period") or {}).get("source")),
        "quality_scored": sum(1 for r in ok if (r.get("quality") or {}).get("score") is not None),
        "mean_quality_coverage_pct": round(sum(qc) / len(qc), 1) if qc else None,
        "history_saved": hist_status,
    }


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

def _p(x, lang, d=1):
    return fmt.pct(x * 100, d, lang) if x is not None else "—"


def _x(x, lang, d=1):
    return (fmt.num(x, d, lang) + "×") if x is not None else "—"


def _mln(x, lang):
    if x is None:
        return "—"
    return fmt.num(x / 1e6, 0, lang) + (" mln zł" if lang == "pl" else " m PLN")


def _val(kind, v, lang):
    if kind == "pct":
        return _p(v, lang)
    if kind == "x":
        return _x(v, lang)
    if kind == "mln":
        return _mln(v, lang)
    if kind == "pp":
        return fmt.pct(v, 1, lang) if v is not None else "—"
    return str(v)


def _flags(r, t):
    return ", ".join(t("flag_" + f["code"]) for f in r.get("red_flags") or []) or "—"


def _checks(r, t):
    return ", ".join(t("check_" + c["code"]) for c in r.get("data_checks") or [])


def _f_cell(r, lang):
    f = r.get("f") or {}
    if not f.get("applicable"):
        return "n/a"
    s = f.get("display") or "—"
    if f.get("source") and f["source"] != "notoria_vs_history":
        s += " ({})".format(f["source"])
    return s


def _q(r, lang):
    q = (r.get("quality") or {}).get("score")
    return fmt.num(q, 0, lang) if q is not None else "—"


def _period(r, t):
    p = r.get("period") or "—"
    return p + (" ⚠️" if r.get("stale_period") else "")


def render_markdown(res: Dict, lang: str) -> str:
    t = fmt.T(STRINGS, lang)
    out: List[str] = []
    rows = res["_rows"]
    prov = res["data_provenance"]
    title = t("title_ticker", t=res["universe"]["spec"]) if res["mode"] == "ticker" else t("title_screen")
    out.append("# " + title + "\n")
    fr = prov["freshness"]
    if fr.get("is_stale"):
        out.append(t("stale", d=prov["as_of"], n=fr.get("sessions_behind")) + "\n")
    if res["mode"] == "ticker":
        out.extend(_render_card(res, rows, t, lang))
    else:
        out.extend(_render_screen(res, rows, t, lang))
    out.append("## " + t("h_limits"))
    out.extend("- " + s for s in STRINGS["limits"][lang])
    out.append("")
    out.append("## " + t("h_sources"))
    out.extend("- " + s for s in STRINGS["sources"][lang])
    for a in res.get("_annual") or []:
        if a["source"] == "esef":
            out.append("- ESEF: {}".format(a.get("path")))
        elif a.get("filing"):
            out.append("- filings.xbrl.org: {}".format(a["filing"].get("viewer_url") or a["filing"].get("json_url")))
    out.append("- " + t("req", n=res["run"]["http_requests"], s=res["run"]["runtime_s"]))
    out.append("")
    out.append("_" + fmt.DISCLAIMER[lang] + "_")
    return "\n".join(out) + "\n"


def _render_screen(res, rows, t, lang) -> List[str]:
    out = []
    ok = [r for r in rows if r.get("status") == "ok"]
    cov = res["coverage"]
    out.append(t("as_of", d=res["as_of"], u=res["universe"]["spec"], n=res["universe"]["size"]))
    ind = [r for r in ok if r["sector_type"] == "industrial"]
    shown = [r for r in ind if r.get("passes_filters", True)]
    ranked = sorted([r for r in shown if r["quality"]["score"] is not None], key=lambda r: -r["quality"]["score"])
    clean = [r for r in ranked if not r.get("stale_period") and not r.get("data_checks")]
    top = ", ".join("{} ({})".format(r["ticker"], _q(r, lang)) for r in clean[:5]) or t("none")
    flagged = [r for r in ok if r.get("red_flags")]
    out.append("")
    out.append(t("answer", ok=len(ok), q=cov["quality_scored"], top=top, rf=len(flagged)))
    out.append("")
    out.append(t("coverage_line", f=fmt.num(cov["mean_f_coverage_pct"] or 0, 0, lang), c=cov["f_complete"],
                 q=fmt.num(cov["mean_quality_coverage_pct"] or 0, 0, lang)))
    if cov["f_complete"] == 0 and ind:
        out.append("")
        out.append("> " + t("thin_history"))
    f = res.get("filters") or {}
    if any(f.get(k) is not None for k in ("min_f", "max_pe", "min_roe_pct")):
        desc = ", ".join("{}={}".format(k, f[k]) for k in ("min_f", "max_pe", "min_roe_pct") if f.get(k) is not None)
        ex = ", ".join("{}: {}".format(k, v) for k, v in sorted(f["excluded"].items())) or t("none")
        out.append("")
        out.append(t("filters_line", f=desc, r=ex))
    top_n = res.get("_top", 25)
    out.append("\n## " + t("h_industrial") + "\n")
    out.append(t("col_ind"))
    out.append("|" + "---|" * 16)
    order = ranked + [r for r in shown if r["quality"]["score"] is None]
    for i, r in enumerate(order[:top_n], 1):
        m = r["metrics"]
        ps = r["quality"].get("pct_sector")
        out.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            i if r["quality"]["score"] is not None else "—",
            r["ticker"] + (" ¹" if r.get("financial_other") else "") + (" ‡" if r.get("data_checks") else ""),
            r.get("sector") or "—", _period(r, t), _f_cell(r, lang), _q(r, lang),
            fmt.num(ps, 0, lang) if ps is not None else "—", _p(m.get("roe"), lang), _p(m.get("roic_approx"), lang),
            _p(m.get("operating_margin"), lang), _p(m.get("fcf_margin"), lang), _p(m.get("accruals_ratio"), lang),
            _x(m.get("pe_gpw") or m.get("pe_calc"), lang), _x(m.get("pbv_gpw"), lang),
            fmt.pct(m.get("dividend_yield_pct"), 1, lang) if m.get("dividend_yield_pct") is not None else "—",
            _flags(r, t)))
    if any(r.get("financial_other") for r in order[:top_n]):
        out.append("\n¹ " + ("spółka finansowa niebankowa (windykacja, maklerzy, giełda) - wskaźniki płynności i zadłużenia mniej miarodajne."
                            if lang == "pl" else "non-bank financial (debt collector, broker, exchange) - liquidity and leverage signals are less meaningful."))
    if any(r.get("data_checks") for r in order[:top_n]):
        out.append("\n‡ " + t("checks_marker"))
    if any(r.get("stale_period") for r in order[:top_n]):
        out.append("\n⚠️ " + t("stale_marker"))
    if len(ranked) > 5:
        out.append("\n**" + t("h_bottom") + ":** " + ", ".join(
            "{} ({})".format(r["ticker"], _q(r, lang)) for r in ranked[-5:][::-1]))
    banks = sorted([r for r in ok if r["sector_type"] == "bank" and r.get("passes_filters", True)],
                   key=lambda r: -(r["quality"]["score"] or -1))
    if banks:
        out.append("\n## " + t("h_banks") + "\n")
        out.append(t("col_bank"))
        out.append("|" + "---|" * 13)
        for i, r in enumerate(banks, 1):
            m, pv = r["metrics"], r.get("pbv_vs_roe") or {}
            out.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                i, r["ticker"], _period(r, t), _q(r, lang), _p(m.get("roe"), lang), _p(m.get("roa"), lang, 2),
                _p(m.get("equity_to_assets"), lang), _x(pv.get("pbv") or m.get("pbv_gpw"), lang, 2),
                _x(pv.get("fitted"), lang, 2),
                fmt.num(pv["residual"], 2, lang) if pv.get("residual") is not None else "—",
                _x(m.get("pe_gpw"), lang), fmt.pct(m.get("dividend_yield_pct"), 1, lang) if m.get("dividend_yield_pct") is not None else "—",
                _flags(r, t)))
        fit = res.get("bank_regression")
        out.append("")
        out.append(t("bank_fit", n=fit["n"], a=fmt.num(fit["intercept"], 2, lang), b=fmt.num(fit["slope"], 2, lang),
                     r2=fmt.num(fit["r2"] or 0, 2, lang)) if fit else t("bank_nofit"))
    ins = [r for r in ok if r["sector_type"] == "insurer" and r.get("passes_filters", True)]
    if ins:
        out.append("\n## " + t("h_insurers") + "\n")
        out.append(t("col_ins"))
        out.append("|" + "---|" * 10)
        for r in ins:
            m = r["metrics"]
            out.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                r["ticker"], _period(r, t), _q(r, lang), _p(m.get("roe"), lang), _p(m.get("roa"), lang, 2),
                _p(m.get("equity_to_assets"), lang), _x(m.get("pbv_gpw"), lang, 2), _x(m.get("pe_gpw"), lang),
                fmt.pct(m.get("dividend_yield_pct"), 1, lang) if m.get("dividend_yield_pct") is not None else "—",
                _flags(r, t)))
    out.append("\n## " + t("h_flags") + "\n")
    if flagged:
        for r in sorted(flagged, key=lambda r: (-len(r["red_flags"]), r["ticker"])):
            out.append("- **{}**: {}".format(r["ticker"], _flags(r, t)))
    else:
        out.append(t("no_flags"))
    checked = [r for r in ok if r.get("data_checks")]
    if checked:
        out.append("\n## " + t("h_checks") + "\n")
        out.append(t("checks_intro"))
        for r in checked:
            out.append("- **{}**: {}".format(r["ticker"], _checks(r, t)))
    out.append("\n## " + t("h_coverage") + "\n")
    out.append(t("cov_table"))
    for k in ("requested", "with_data", "no_notoria", "errors", "coverage_pct", "stale_periods", "data_check_failures", "f_complete",
              "mean_f_coverage_pct", "prior_period_found", "quality_scored", "mean_quality_coverage_pct"):
        out.append("| {} | {} |".format(k, cov.get(k)))
    out.append("| f_available_distribution | {} |".format(
        ", ".join("{}/9: {}".format(k, v) for k, v in cov["f_available_distribution"].items()) or "—"))
    out.append("| history_saved | {} |".format(
        ", ".join("{}: {}".format(k, v) for k, v in sorted(cov["history_saved"].items())) or "—"))
    errs = [r for r in rows if r.get("status") != "ok"]
    if errs or res["universe"]["unresolved"]:
        out.append("\n**" + t("h_errors") + ":** " + ", ".join(
            [r["ticker"] for r in errs] + res["universe"]["unresolved"]))
    out.append("\n## " + t("h_signals") + "\n")
    if res["signals"]:
        for s in res["signals"]:
            out.append("- {} `{}` {} ({}; score {})".format(s["ticker"], s["kind"], s["direction"], s["date"], s.get("score")))
    else:
        out.append(t("none"))
    out.append("")
    return out


def _f_table(f, t, lang) -> List[str]:
    out = [t("f_head")]
    for i, c in enumerate(f["criteria"], 1):
        note = []
        if c.get("proxy"):
            note.append(t("proxy", p=c["proxy"]))
        if c.get("note"):
            note.append(c["note"])
        nowv, prev = c.get("current"), c.get("prior")
        if c["id"] == "no_equity_issue":
            nowv_s = fmt.num(nowv, 0, lang) if nowv is not None else "—"
            prev_s = fmt.num(prev, 0, lang) if prev is not None else "—"
        elif c["id"] == "liquidity_up":
            nowv_s, prev_s = _x(nowv, lang, 2), _x(prev, lang, 2)
        elif c["id"] == "accruals":
            nowv_s = ("CFO/A {} vs ROA {}".format(_p(nowv, lang), _p(prev, lang))
                      if nowv is not None and prev is not None else "—")
            prev_s = "—"
        else:
            nowv_s, prev_s = _p(nowv, lang, 2), _p(prev, lang, 2)
        out.append("| {} | {} | {} | {} | {} | {} |".format(i, t("crit_" + c["id"]), t(c["status"]), nowv_s, prev_s,
                                                          "; ".join(note) or ""))
    return out


def _render_card(res, rows, t, lang) -> List[str]:
    out = []
    r = rows[0]
    if r.get("status") != "ok":
        out.append(r.get("error") or "no data")
        return out
    m, f = r["metrics"], r["f"]
    out.append(t("card_type", t=t("type_" + r["sector_type"]), s=r.get("sector") or "—", p=_period(r, t),
                 c=t("consolidated") if r.get("consolidated") else t("standalone")))
    # plain summary
    bits = []
    ni, cfo = m.get("net_income_ann"), m.get("cfo_ann")
    if ni is not None and ni < 0:
        bits.append(t("sum_loss"))
    elif r["sector_type"] == "industrial" and cfo is not None and cfo < 0:
        bits.append(t("sum_cfo_neg"))
    elif r["sector_type"] == "industrial" and ni is not None and cfo is not None and cfo < ni:
        bits.append(t("sum_accr"))
    elif ni is not None and ni > 0:
        bits.append(t("sum_good") if r["sector_type"] == "industrial" else (
            "ROE " + _p(m.get("roe"), lang)))
    q = r["quality"].get("score")
    bits.append(t("sum_q", s=fmt.num(q, 0, lang)) if q is not None else t("sum_qna"))
    bits.append(t("sum_flags", f=_flags(r, t)) if r["red_flags"] else t("sum_noflags"))
    if f.get("applicable"):
        bits.append("F " + f["display"])
    out.append("")
    out.append(t("card_summary", s="; ".join(bits) + "."))
    if "stale_period" in res.get("warnings", []):
        out.append("\n⚠️ " + t("stale_period") + ": {} ({} d)".format(r.get("period_end"), r.get("period_age_days")))
    # F-score
    out.append("\n## " + t("h_f") + "\n")
    if not f.get("applicable"):
        out.append(t("f_na"))
    else:
        out.extend(_f_table(f, t, lang))
        band = {"high": t("band_high"), "low": t("band_low"), "middle": t("band_middle")}.get(f["band"], t("band_undetermined"))
        out.append("")
        out.append(t("f_total", d=f["display"], c=fmt.num(f["coverage_pct"], 0, lang), lo=f["lower"], hi=f["upper"], band=band))
        out.append("")
        if f.get("prior_label"):
            out.append(t("f_source", p=f["prior_label"], s=f.get("prior_source") or f.get("source")))
            if f.get("stale"):
                out.append("\n" + t("xbrl_stale"))
        else:
            out.append(t("f_noprior") + " " + t("thin_history"))
    # metrics
    out.append("\n## " + t("h_metrics") + "\n")
    out.append("_{}: {}_\n".format("annualizacja" if lang == "pl" else "annualisation", m.get("annualisation")))
    out.append(t("m_head"))
    for key, kind, pl, en in METRIC_ROWS:
        if key in m:
            out.append("| {} | {} | {} |".format(key, _val(kind, m.get(key), lang), pl if lang == "pl" else en))
    out.append("\n## " + t("h_valuation") + "\n")
    out.append(t("m_head"))
    for key, kind, pl, en in VALUATION_ROWS:
        if key in m:
            out.append("| {} | {} | {} |".format(key, _val(kind, m.get(key), lang), pl if lang == "pl" else en))
    if r.get("pbv_vs_roe"):
        pv = r["pbv_vs_roe"]
        out.append("| pbv_fitted_on_roe | {} | {} |".format(_x(pv["fitted"], lang, 2), "residual {}".format(fmt.num(pv["residual"], 2, lang))))
    # quality
    out.append("\n## " + t("h_quality") + "\n")
    qd_ = r["quality"]
    out.append(t("q_line", s=fmt.num(qd_.get("score"), 0, lang) if qd_.get("score") is not None else "—",
                 c=fmt.num(qd_.get("coverage_pct") or 0, 0, lang), r=qd_.get("rank") or "—", n=qd_.get("rank_of") or 1,
                 ps=fmt.num(qd_["pct_sector"], 0, lang) if qd_.get("pct_sector") is not None else "—"))
    out.append("")
    out.append(t("q_comp_head"))
    for c in qd_.get("components") or []:
        basis = t("basis_absolute") if c.get("basis") == "absolute" else (t("basis_rank") if c.get("basis") else "—")
        out.append("| {} | {} | {} | {} | {} |".format(
            t("comp_" + c["metric"]), fmt.num(c["value"], 3, lang) if c.get("value") is not None else "—",
            fmt.num(c["score"], 0, lang) if c.get("score") is not None else "—", fmt.num(c["weight"], 2, lang), basis))
    out.append("\n## " + t("h_flags") + "\n")
    out.append(_flags(r, t) if r["red_flags"] else t("sum_noflags"))
    if r.get("data_checks"):
        out.append("\n## " + t("h_checks") + "\n")
        out.append(t("checks_intro"))
        out.append("- " + _checks(r, t))
    annual = res.get("_annual") or []
    if annual or res.get("warnings"):
        out.append("\n## " + t("h_annual") + "\n")
        for a in annual:
            cur = a.get("current")
            if not cur:
                if a.get("note") == "lei_not_found":
                    out.append("- " + t("xbrl_nolei", t=r["ticker"]))
                elif a.get("note") == "no_annual_filing":
                    out.append("- " + t("xbrl_nofiling", l=a.get("lei")))
                continue
            fv = qe.piotroski(cur, a.get("prior"))
            fv_label = "{} → {}".format((a.get("prior") or {}).get("label") or "—", cur.get("label"))
            nd = None
            if cur.get("cash") is not None and cur.get("borrowings") is not None:
                nd = cur["borrowings"] + (cur.get("lease_liabilities") or 0) - cur["cash"]
            nde = qe.safe_div(nd, cur.get("ebitda")) if (nd is not None and (cur.get("ebitda") or 0) > 0) else None
            out.append("- " + t("annual_line", src=a["source"], lab=cur.get("label"), d=fv["display"],
                                 c=fmt.num(fv["coverage_pct"], 0, lang), nd=_mln(nd, lang), nde=_x(nde, lang)))
            if a["source"] == "xbrl":
                out.append("  " + t("xbrl_stale"))
            out.append("")
            out.append("**{}** ({})".format(a["source"], fv_label))
            out.append("")
            out.extend(_f_table(fv, t, lang))
            out.append("")
    out.append("\n## " + t("explain_title") + "\n")
    out.extend("- " + s for s in STRINGS["explain"][lang])
    out.append("")
    return out


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def public(result: Dict) -> Dict:
    return {k: v for k, v in result.items() if not k.startswith("_")}


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except (qd.DataError, gpw_archive.GPWFetchError, http.HTTPFetchError, fundamentals.FundamentalsError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    result["_top"] = getattr(args, "top", 25)
    md = render_markdown(result, args.lang)
    if args.no_files:
        print(md)
        return 0
    out = Path(args.output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    jp, mp = out / "{}_{}.json".format(PREFIX, stamp), out / "{}_{}.md".format(PREFIX, stamp)
    jp.write_text(json.dumps(public(result), ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(md, encoding="utf-8")
    print(md)
    print("JSON: {}\nMarkdown: {}".format(jp, mp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
