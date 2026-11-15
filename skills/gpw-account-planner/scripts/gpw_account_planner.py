#!/usr/bin/env python3
"""GPW Account Planner - CLI.

"Where should my monthly savings go: IKE, IKZE, OKI or a regular brokerage
account?" Pure calculation (no market data): simulates each wrapper month by
month for several return scenarios, finds the fill order with the highest
computed net value (the "waterfall") and writes JSON + Markdown (PL or EN).

    python3 scripts/gpw_account_planner.py --monthly 1000 --years 25 --age 30 --pit scale12
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import brokers, tax  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct, pln  # noqa: E402

import account_planner_engine as ape  # noqa: E402

VERSION = "1.0.0"
SKILL = "gpw-account-planner"

STRINGS = {
    "title": {"pl": "Gdzie odkładać pieniądze: IKE, IKZE, OKI czy zwykłe konto?",
              "en": "Where to put your monthly savings: IKE, IKZE, OKI or a regular account?"},
    "answer": {"pl": "Odpowiedź w skrócie", "en": "Short answer"},
    "ans_main": {
        "pl": ("Przy Twoich założeniach ({m} miesięcznie przez {y} lat, scenariusz {r} rocznie) najwięcej zostaje "
               "„na rękę” przy kolejności: **{order}**. Po {y} latach to ok. **{net}** po wszystkich podatkach i opłatach "
               "(ok. {real} w dzisiejszych pieniądzach). Wpłacisz łącznie {own}."),
        "en": ("With your assumptions ({m} a month for {y} years, {r} a year scenario) the most money is left after tax "
               "with this order: **{order}**. After {y} years that is about **{net}** after all taxes and fees "
               "(about {real} in today's money). You pay in {own} in total."),
    },
    "ans_vs_regular": {"pl": "Dla porównania: tylko zwykłe konto maklerskie to ok. {reg} — różnica **{diff}**.",
                       "en": "For comparison: a regular brokerage account only gives about {reg} — a difference of **{diff}**."},
    "ans_same_regular": {"pl": "W tym horyzoncie żadne konto z ulgą podatkową nie daje więcej niż zwykłe konto — zobacz niżej, dlaczego (warunki wieku).",
                         "en": "Over this horizon no tax-advantaged account beats a regular account — see below why (age conditions)."},
    "ans_same_ike": {"pl": "Tu IKE wychodzi dokładnie tak samo jak zwykłe konto, bo warunek wieku (60 lat) nie jest jeszcze spełniony — ale nic nie tracisz, a zachowujesz możliwość wypłaty bez podatku, jeśli poczekasz. Dlatego IKE jest pierwsze.",
                     "en": "Here IKE gives exactly the same as a regular account because the age condition (60) is not met yet — but you lose nothing and keep the option of a tax-free withdrawal if you wait. That is why IKE comes first."},
    "ans_hold": {
        "pl": ("Ale uwaga: w wieku {age} lat IKE i IKZE nie są jeszcze zwolnione z podatku. Jeśli po {y} latach przestaniesz wpłacać, "
               "a pieniądze zostawisz do {wa} roku życia, najlepsza kolejność to **{order}**: ok. **{net}** (ok. {real} dzisiaj), "
               "a zwykłe konto w tym samym czasie ok. {reg}."),
        "en": ("But note: at age {age} IKE and IKZE are not tax-free yet. If you stop paying in after {y} years and leave the money "
               "until age {wa}, the best order is **{order}**: about **{net}** (about {real} today), versus about {reg} on a regular account "
               "over the same time."),
    },
    "ans_not_advice": {"pl": "To wynik obliczeń dla podanych założeń, a nie porada — przyszłe stopy zwrotu, limity i przepisy są niepewne.",
                       "en": "This is the result of a calculation for the given assumptions, not advice — future returns, limits and laws are uncertain."},
    "glossary": {"pl": "Słowniczek (dla początkujących)", "en": "Glossary (for beginners)"},
    "g_ike": {"pl": "**IKE** (Indywidualne Konto Emerytalne) — konto na emeryturę z rocznym limitem wpłat. Jeśli wypłacisz po 60. roku życia (i wpłacałeś w co najmniej 5 latach kalendarzowych), nie płacisz żadnego podatku od zysku. Wcześniejsza wypłata = podatek jak na zwykłym koncie.",
              "en": "**IKE** (Individual Retirement Account) — a retirement account with a yearly contribution limit. Withdraw after age 60 (having paid in during at least 5 calendar years) and the gain is completely tax-free. Earlier withdrawal = taxed like a regular account."},
    "g_ikze": {"pl": "**IKZE** (Indywidualne Konto Zabezpieczenia Emerytalnego) — wpłaty odliczasz od dochodu w PIT, więc co roku dostajesz zwrot części podatku (12%, 19% lub 32% wpłaty). Przy wypłacie po 65. roku życia płacisz 10% od całej kwoty. Wcześniejsza wypłata = cała kwota doliczana do dochodu i opodatkowana skalą PIT.",
               "en": "**IKZE** (Individual Retirement Security Account) — contributions are deducted from your taxable income, so each year you get part of your income tax back (12%, 19% or 32% of the contribution). Withdraw after 65 and you pay 10% of the whole amount. Earlier withdrawal = the whole amount is added to income and taxed at the PIT scale."},
    "g_oki": {"pl": "**OKI** (Osobiste Konto Inwestycyjne, od 2027 r.) — nie płacisz podatku od zysku, ale co roku płacisz mały podatek od średniej wartości konta (0,85% w 2027 r.). Zwolnione jest do 100 000 zł aktywów „kwalifikowanych” (polskie akcje, obligacje w zł, fundusze/ETF-y z min. 70% takich aktywów) i do 25 000 zł oszczędności. Globalne ETF-y (np. na cały świat czy S&P 500) są opodatkowane od pierwszej złotówki.",
              "en": "**OKI** (Personal Investment Account, from 2027) — no tax on gains, but a small yearly tax on the average account value (0.85% in 2027). Up to 100,000 zł of \"qualified\" assets (Polish shares, PLN bonds, funds/ETFs with at least 70% such assets) and up to 25,000 zł of savings are exempt. Global ETFs (e.g. world or S&P 500) are taxed from the first zloty."},
    "g_regular": {"pl": "**Zwykłe konto maklerskie i podatek Belki** — bez limitów i warunków; od zysku przy sprzedaży płacisz 19% („podatek Belki”, od nazwiska ministra, który go wprowadził).",
                  "en": "**Regular brokerage account and the Belka tax** — no limits or conditions; you pay 19% of the gain when you sell (the \"Belka tax\", named after the minister who introduced it)."},
    "g_etf": {"pl": "**ETF** — fundusz notowany na giełdzie, który kupuje cały koszyk akcji lub obligacji (np. 20 największych polskich spółek). Jedna transakcja = rozproszenie na wiele firm.",
              "en": "**ETF** — an exchange-traded fund that holds a whole basket of shares or bonds (e.g. the 20 largest Polish companies). One trade = spread over many companies."},
    "g_ter": {"pl": "**TER** — roczny koszt funduszu w % (np. 0,5% = 5 zł rocznie od każdego 1 000 zł). Pobierany po cichu z wartości funduszu.",
              "en": "**TER** — the fund's yearly cost in % (e.g. 0.5% = 5 zł a year per 1,000 zł). Taken silently from the fund's value."},
    "g_compound": {"pl": "**Procent składany** — zyski też zarabiają kolejne zyski. Dlatego podatek płacony co roku (lub wcześniej) kosztuje więcej, niż wygląda, a długi horyzont ma ogromne znaczenie.",
                   "en": "**Compound interest** — gains earn further gains. That is why a tax paid every year (or earlier) costs more than it looks, and why a long horizon matters so much."},
    "g_real": {"pl": "**Wartość realna (w dzisiejszych pieniądzach)** — kwota po uwzględnieniu inflacji, czyli ile byłaby warta dzisiaj. 1 000 000 zł za 25 lat przy inflacji 3% to ok. 478 000 dzisiejszych złotych.",
               "en": "**Real value (in today's money)** — the amount adjusted for inflation, i.e. what it would buy today. 1,000,000 zł in 25 years at 3% inflation is about 478,000 of today's zloty."},
    "inputs": {"pl": "Twoje założenia", "en": "Your assumptions"},
    "i_monthly": {"pl": "Wpłata miesięczna", "en": "Monthly contribution"},
    "i_horizon": {"pl": "Horyzont / wiek", "en": "Horizon / age"},
    "i_horizon_v": {"pl": "{y} lat, od {s} r.; wiek teraz {a}, na końcu {e}", "en": "{y} years from {s}; age now {a}, at the end {e}"},
    "i_scen": {"pl": "Scenariusze zwrotu (nominalnie, rocznie)", "en": "Return scenarios (nominal, per year)"},
    "i_infl": {"pl": "Inflacja (do wartości realnych)", "en": "Inflation (for real values)"},
    "i_ter": {"pl": "Koszt produktu (TER)", "en": "Product cost (TER)"},
    "i_broker": {"pl": "Prowizja maklerska", "en": "Broker commission"},
    "i_broker_v": {"pl": "{label}: {p} (min. {m}) za każde zlecenie — 1 zakup na konto miesięcznie + sprzedaż na końcu",
                   "en": "{label}: {p} (min. {m}) per order — 1 buy per account per month + one sale at the end"},
    "i_pit": {"pl": "Twoja stawka PIT (zwrot z IKZE)", "en": "Your PIT rate (IKZE refund)"},
    "i_selfemp": {"pl": "Działalność gospodarcza (wyższy limit IKZE)", "en": "Self-employed (higher IKZE limit)"},
    "i_okiq": {"pl": "Część OKI w aktywach kwalifikowanych (np. polskie ETF-y)", "en": "OKI share in qualified assets (e.g. Polish ETFs)"},
    "i_okirate": {"pl": "Stawka podatku OKI", "en": "OKI tax rate"},
    "i_okirate_v_nbp": {"pl": "2027: 0,85% (ustawa); od 2028: 19% stopy referencyjnej NBP {n}% = {r}%",
                        "en": "2027: 0.85% (law); from 2028: 19% of the NBP reference rate {n}% = {r}%"},
    "i_okirate_v": {"pl": "2027: 0,85% (ustawa); od 2028: przyjęto {r}% (nieznana — zależy od stopy NBP)",
                    "en": "2027: 0.85% (law); from 2028: assumed {r}% (unknown — depends on the NBP rate)"},
    "i_refund": {"pl": "Zwrot podatku z IKZE", "en": "IKZE tax refund"},
    "i_refund_yes": {"pl": "reinwestowany na zwykłym koncie (w kwietniu następnego roku)", "en": "reinvested in the regular account (in April of the next year)"},
    "i_refund_no": {"pl": "wydawany (liczony jako gotówka, bez pomnażania)", "en": "spent (counted as cash, not invested)"},
    "i_wage": {"pl": "Wzrost limitów IKE/IKZE w kolejnych latach", "en": "IKE/IKZE limit growth in later years"},
    "i_wage_v": {"pl": "znane limity 2026–2027 dokładnie, potem +{g}% rocznie (wzrost płac)", "en": "known 2026–2027 limits exactly, then +{g}% a year (wage growth)"},
    "yes": {"pl": "tak", "en": "yes"}, "no": {"pl": "nie", "en": "no"},
    "none_broker": {"pl": "bez prowizji", "en": "no commission"},
    "compare": {"pl": "Porównanie kont — wynik po {y} latach", "en": "Account comparison — result after {y} years"},
    "compare_note": {"pl": "„Netto” = ile dostaniesz na rękę po sprzedaży wszystkiego, zapłaceniu podatków i opłat. Kwoty powyżej limitu konta trafiają na zwykłe konto (wliczone w wynik).",
                     "en": "\"Net\" = what you receive after selling everything and paying taxes and fees. Amounts above an account's limit go to the regular account (included in the result)."},
    "scenario": {"pl": "Scenariusz {r} rocznie", "en": "Scenario {r} a year"},
    "c_wrapper": {"pl": "Konto", "en": "Account"},
    "c_own": {"pl": "Wpłacone", "en": "Paid in"},
    "c_net": {"pl": "Netto na końcu", "en": "Net at the end"},
    "c_real": {"pl": "Netto w dzisiejszych zł", "en": "Net in today's money"},
    "c_tax": {"pl": "Podatki", "en": "Taxes"},
    "c_fees": {"pl": "Opłaty (TER + prowizje)", "en": "Fees (TER + commissions)"},
    "c_refund": {"pl": "Zwroty z IKZE", "en": "IKZE refunds"},
    "c_cond": {"pl": "Ulga podatkowa?", "en": "Tax relief?"},
    "w_regular": {"pl": "Zwykłe konto", "en": "Regular account"},
    "w_ike": {"pl": "IKE", "en": "IKE"}, "w_ikze": {"pl": "IKZE", "en": "IKZE"}, "w_oki": {"pl": "OKI", "en": "OKI"},
    "w_waterfall": {"pl": "Kombinacja: {o}", "en": "Combination: {o}"},
    "cond_ok": {"pl": "tak", "en": "yes"},
    "cond_age": {"pl": "nie — wypłata przed {a} r.ż.", "en": "no — withdrawal before age {a}"},
    "cond_years": {"pl": "nie — wpłaty w < 5 latach", "en": "no — contributions in < 5 years"},
    "cond_na": {"pl": "—", "en": "—"},
    "cond_oki": {"pl": "podatek roczny", "en": "yearly tax"},
    "waterfall": {"pl": "Jak podzielić miesięczną wpłatę (kombinacja z obliczeń)", "en": "How to split the monthly amount (computed combination)"},
    "wf_intro": {"pl": "Sprawdzono {n} kolejności wypełniania kont (zawsze w ramach rocznych limitów, nadwyżka na zwykłe konto). Najwyższy wynik netto w scenariuszu {r}: **{o}**.",
                 "en": "{n} fill orders were tested (always within yearly limits, overflow to the regular account). Highest net result in the {r} scenario: **{o}**."},
    "wf_differs": {"pl": "Uwaga: w innych scenariuszach najlepsza kolejność jest inna: {d}. Wynik zależy od założonej stopy zwrotu.",
                   "en": "Note: in other scenarios a different order wins: {d}. The result depends on the assumed return."},
    "wf_split": {"pl": "Podział w pierwszym miesiącu ({y} r.)", "en": "Split in the first month ({y})"},
    "wf_total": {"pl": "Łącznie wpłacone w {n} lat", "en": "Total paid in over {n} years"},
    "wf_refunds": {"pl": "Dodatkowo zwroty podatku z IKZE reinwestowane na zwykłym koncie: łącznie {x}.",
                   "en": "In addition, IKZE tax refunds reinvested in the regular account: {x} in total."},
    "wf_top": {"pl": "Ranking kolejności (scenariusz {r})", "en": "Order ranking ({r} scenario)"},
    "c_order": {"pl": "Kolejność", "en": "Order"},
    "only_regular": {"pl": "tylko zwykłe konto", "en": "regular account only"},
    "then_regular": {"pl": " → nadwyżka: zwykłe konto", "en": " → overflow: regular account"},
    "hold": {"pl": "A gdyby poczekać z wypłatą do {wa} lat?", "en": "What if you wait with the withdrawal until age {wa}?"},
    "hold_intro": {"pl": "Wpłaty kończą się po {y} latach, ale pieniądze pracują dalej jeszcze {h} lat (scenariusz {r}). Dopiero wtedy IKE (60 lat) i IKZE (65 lat) dają pełną ulgę.",
                   "en": "Contributions stop after {y} years, but the money keeps working for {h} more years ({r} scenario). Only then do IKE (60) and IKZE (65) give full tax relief."},
    "limits": {"pl": "Limity wpłat użyte w obliczeniach", "en": "Contribution limits used"},
    "c_year": {"pl": "Rok", "en": "Year"},
    "c_ike_lim": {"pl": "Limit IKE", "en": "IKE limit"},
    "c_ikze_lim": {"pl": "Limit IKZE", "en": "IKZE limit"},
    "c_oki_rate": {"pl": "Stawka OKI", "en": "OKI rate"},
    "c_src": {"pl": "Źródło", "en": "Source"},
    "src_known": {"pl": "obwieszczenie MRPiPS", "en": "official notice (MRPiPS)"},
    "src_projected": {"pl": "prognoza (+{g}%/rok)", "en": "projection (+{g}%/yr)"},
    "limits_more": {"pl": "… kolejne lata rosną o {g}% rocznie.", "en": "… later years grow {g}% a year."},
    "rules": {"pl": "Najważniejsze zasady i warunki", "en": "Key rules and conditions"},
    "r_ike": {"pl": "IKE: zwolnienie z podatku przy wypłacie po 60. r.ż. (55 przy uprawnieniach do wcześniejszej emerytury) i wpłatach w co najmniej 5 latach kalendarzowych albo gdy ponad połowa wpłat była dokonana ponad 5 lat przed wypłatą (art. 34 ustawy o IKE i IKZE). Jedno IKE na osobę.",
              "en": "IKE: tax-free withdrawal after age 60 (55 with early-retirement rights) with contributions in at least 5 calendar years, or more than half of contributions made over 5 years before withdrawal (art. 34 of the IKE/IKZE act). One IKE per person."},
    "r_ikze": {"pl": "IKZE: wpłaty odliczasz od dochodu (art. 26 ust. 1 pkt 2b ustawy o PIT); wypłata po 65. r.ż. przy wpłatach w co najmniej 5 latach — 10% od całej kwoty (art. 30 ust. 1 pkt 10a). Zwrot przed 65 — cała kwota doliczana do dochodu (skala PIT). Jedno IKZE na osobę.",
               "en": "IKZE: contributions are deductible from income (art. 26(1)(2b) PIT act); withdrawal after 65 with contributions in at least 5 years — 10% of the whole amount (art. 30(1)(10a)). Early withdrawal — the whole amount is added to income (PIT scale). One IKZE per person."},
    "r_oki": {"pl": "OKI (ustawa z 3 lipca 2026 r., Dz.U. 2026 poz. 1098, od 1.01.2027): dochody z aktywów OKI poza PIT; roczny podatek od średniej wartości aktywów (art. 22–23), stawka 0,85% w 2027 (art. 43), potem 19% stopy referencyjnej NBP z 31 października poprzedniego roku, min. 0,1% (art. 25); zwolnienia do 25 000 zł oszczędności i do 100 000 zł aktywów kwalifikowanych, łącznie max 100 000 zł (art. 26).",
              "en": "OKI (act of 3 July 2026, Dz.U. 2026 item 1098, from 1 Jan 2027): income from OKI assets is outside PIT; yearly tax on the average asset value (art. 22–23), rate 0.85% in 2027 (art. 43), later 19% of the NBP reference rate of 31 October of the previous year, min. 0.1% (art. 25); exemptions up to 25,000 zł of savings and up to 100,000 zł of qualified assets, 100,000 zł in total (art. 26)."},
    "r_regular": {"pl": "Zwykłe konto: 19% podatku od zysku (art. 30a/30b ustawy o PIT), rozliczenie w PIT-38. Model zakłada jedną sprzedaż na końcu — każda wcześniejsza sprzedaż lub rebalancing z zyskiem to podatek wcześniej (mniej procentu składanego). ETF-y akumulujące nie wypłacają dywidend, więc nie generują podatku po drodze.",
                  "en": "Regular account: 19% tax on the gain (art. 30a/30b PIT act), filed in PIT-38. The model assumes a single sale at the end — any earlier sale or rebalancing at a profit means paying tax earlier (less compounding). Accumulating ETFs do not pay dividends, so they create no tax along the way."},
    "warnings": {"pl": "Ostrzeżenia", "en": "Warnings"},
    "warn_ike_age": {"pl": "Na końcu horyzontu będziesz mieć {e} lat, a IKE zwalnia z podatku dopiero po 60. r.ż. — wypłata wcześniej to „zwrot” opodatkowany jak zwykłe konto (19%). Nie musisz wtedy wypłacać — możesz zostawić pieniądze na IKE do 60 lat.",
                     "en": "You will be {e} at the end of the horizon, but IKE is tax-free only after 60 — an earlier withdrawal is a \"return\" taxed like a regular account (19%). You do not have to withdraw — you can leave the money in IKE until 60."},
    "warn_ikze_age": {"pl": "Na końcu horyzontu będziesz mieć {e} lat; IKZE daje 10% podatku dopiero po 65. r.ż. Wcześniejszy zwrot = cała kwota doliczona do dochodu (12%/32% skali), a duża kwota wpada w próg 32%. IKZE ma sens głównie, gdy pieniądze zostaną do 65 lat.",
                      "en": "You will be {e} at the end of the horizon; IKZE gets the 10% tax only after 65. An early return = the whole amount added to income (12%/32% scale) and a large amount crosses into the 32% bracket. IKZE mainly makes sense if the money stays until 65."},
    "warn_short_horizon": {"pl": "Horyzont krótszy niż 5 lat: warunek wpłat w 5 latach kalendarzowych dla IKE/IKZE nie będzie spełniony, a przy akcjach/ETF-ach akcyjnych kilka lat to za mało, by przeczekać bessę.",
                           "en": "Horizon under 5 years: the 5-calendar-year contribution condition for IKE/IKZE will not be met, and for equity ETFs a few years is too short to sit out a bear market."},
    "warn_no_pit": {"pl": "Bez dochodu opodatkowanego PIT nie dostaniesz zwrotu z IKZE — wtedy IKZE traci główną zaletę.",
                    "en": "Without PIT-taxed income you get no IKZE refund — IKZE then loses its main advantage."},
    "warn_oki_global": {"pl": "OKI: przyjęto, że tylko {q} aktywów to aktywa kwalifikowane (polskie akcje/obligacje, ETF-y z min. 70% takich aktywów). Globalne ETF-y (np. MSCI World, S&P 500) są opodatkowane od pierwszej złotówki — dlatego ustawiaj --oki-qualified-pct zgodnie z tym, co faktycznie chcesz trzymać na OKI.",
                        "en": "OKI: only {q} of the assets are assumed to be qualified (Polish shares/bonds, ETFs with at least 70% such assets). Global ETFs (e.g. MSCI World, S&P 500) are taxed from the first zloty — so set --oki-qualified-pct to what you actually plan to hold on OKI."},
    "warn_oki_rate_assumed": {"pl": "Stawka OKI od 2028 r. jest nieznana (19% stopy referencyjnej NBP z 31.10 roku poprzedniego). Przyjęto {r}% — podaj --nbp-rate, aby policzyć ją według art. 25.",
                              "en": "The OKI rate from 2028 is unknown (19% of the NBP reference rate of 31 Oct of the previous year). {r}% was assumed — pass --nbp-rate to compute it per art. 25."},
    "warn_min_fee_small_orders": {"pl": "Prowizja minimalna przy małych miesięcznych zleceniach może zjadać kilka % wpłaty — rozważ zakupy co 2–3 miesiące (gpw-etf-planner liczy to dokładnie).",
                                  "en": "The minimum commission on small monthly orders can eat several % of the contribution — consider buying every 2–3 months (gpw-etf-planner computes this exactly)."},
    "warn_oki_not_yet": {"pl": "OKI działa od 1 stycznia 2027 r. — wpłaty z 2026 r. w scenariuszu OKI trafiają na zwykłe konto.",
                         "en": "OKI starts on 1 January 2027 — 2026 contributions in the OKI scenario go to the regular account."},
    "caveats": {"pl": "Zastrzeżenia", "en": "Caveats"},
    "cav": {"pl": ["Przepisy się zmieniają: limity IKE/IKZE są ogłaszane co roku, stawka OKI zależy od stopy NBP, a sama ustawa o OKI jest nowa — sprawdzaj aktualny stan prawny.",
                   "Stopy zwrotu nie są gwarantowane; scenariusze 3/6/9% to założenia, nie prognozy. Rzeczywiste wyniki skaczą z roku na rok (w bessie akcje tracą 30–50%).",
                   "IKZE jest opodatkowane przy wypłacie (10% po 65. r.ż.) — kwota na koncie to nie cała Twoja kwota. Zwrot z IKZE wymaga, by podatek należny był co najmniej tak wysoki jak ulga.",
                   "Podatek OKI liczony jest jako średnia z 12 wartości na koniec miesięcy i pobierany z konta na koniec roku — to przybliżenie art. 23; dokładny sposób sprawdź u dostawcy OKI.",
                   "Model nie uwzględnia opłat za prowadzenie konta, przewalutowań, spreadu ani zmiany stawki PIT w czasie."],
            "en": ["Laws change: IKE/IKZE limits are announced every year, the OKI rate depends on the NBP rate, and the OKI act is new — check the current law.",
                   "Returns are not guaranteed; the 3/6/9% scenarios are assumptions, not forecasts. Real returns swing year to year (in a bear market shares lose 30–50%).",
                   "IKZE is taxed at withdrawal (10% after 65) — the account balance is not all yours. The IKZE refund requires your tax due to be at least as large as the relief.",
                   "The OKI tax is computed as the mean of 12 month-end values and taken from the account at year end — an approximation of art. 23; check the exact method with your OKI provider.",
                   "The model ignores account-maintenance fees, currency conversion, spreads and changes of your PIT rate over time."]},
    "tie_in": {"pl": "Co dalej: jeśli chcesz wybrać konkretne ETF-y i policzyć, ile jednostek kupić co miesiąc, użyj **gpw-etf-planner** (pokaże też, które ETF-y mogą być aktywami kwalifikowanymi dla OKI).",
               "en": "Next: to pick specific ETFs and compute how many units to buy each month, use **gpw-etf-planner** (it also shows which ETFs may count as qualified assets for OKI)."},
    "sources": {"pl": "Źródła", "en": "Sources"},
}

SOURCES = [
    "Ustawa z 20 kwietnia 2004 r. o indywidualnych kontach emerytalnych oraz indywidualnych kontach zabezpieczenia emerytalnego (Dz.U. 2026 poz. 91), art. 13, 13a, 34, 34a — https://isap.sejm.gov.pl",
    "Ustawa z 26 lipca 1991 r. o podatku dochodowym od osób fizycznych: art. 26 ust. 1 pkt 2b, art. 27, art. 30 ust. 1 pkt 10a, art. 30a, art. 30b, art. 30c — https://isap.sejm.gov.pl",
    "Ustawa z 3 lipca 2026 r. o osobistych kontach inwestycyjnych (Dz.U. 2026 poz. 1098), art. 22–26, 43 — https://dziennikustaw.gov.pl",
    "Limity IKE/IKZE 2026: obwieszczenia MRPiPS (listopad 2025); 2027: obwieszczenia MRPiPS z 27 i 30.10.2026 (M.P. 2026 poz. 1062, 1081) — https://monitorpolski.gov.pl",
    "NBP — stopy procentowe (stopa referencyjna): https://nbp.pl/polityka-pieniezna/decyzje-rpp/podstawowe-stopy-procentowe-nbp/",
]


def _floats(text: str):
    try:
        vals = [float(x.strip().replace(",", ".")) for x in text.split(";" if ";" in text else ",") if x.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError("expected numbers like 3,6,9")
    if not vals or any(v <= -100 or v > 50 for v in vals):
        raise argparse.ArgumentTypeError("return scenarios must be between -100 and 50 (%)")
    return vals


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="IKE / IKZE / OKI / regular account planner (PL/EN)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--monthly", type=float, help="monthly contribution in PLN")
    g.add_argument("--yearly", type=float, help="yearly contribution in PLN (spread evenly over 12 months)")
    p.add_argument("--years", type=int, required=True, help="horizon in years (contributions every month)")
    p.add_argument("--age", type=int, required=True, help="current age")
    p.add_argument("--start-year", type=int, default=2027)
    p.add_argument("--returns", type=_floats, default=[3.0, 6.0, 9.0], help="nominal yearly return scenarios in %%, e.g. 3,6,9")
    p.add_argument("--inflation", type=float, default=3.0, help="inflation in %% a year (real values)")
    p.add_argument("--ter", type=float, default=0.5, help="yearly product cost (TER) in %%")
    p.add_argument("--broker", choices=sorted(brokers.BROKERS), default="custom",
                   help="commission preset (default custom = 0%%, no commission)")
    p.add_argument("--commission-pct", type=float, default=None)
    p.add_argument("--commission-min", type=float, default=None)
    p.add_argument("--pit", choices=sorted(tax.MARGINAL_RATES), default="scale12",
                   help="marginal PIT rate: scale12, scale32, linear (19%%) or none")
    p.add_argument("--self-employed", action="store_true", help="higher IKZE limit (1.8x average wage)")
    p.add_argument("--oki-qualified-pct", type=float, default=0.0,
                   help="share of OKI assets that are qualified (Polish shares/PLN bonds/funds >= 70%% such), 0-100")
    p.add_argument("--oki-rate-later", type=float, default=tax.OKI_RATE_2027_PCT,
                   help="assumed OKI rate in %% from 2028 (unknown; default = 2027 rate)")
    p.add_argument("--nbp-rate", type=float, default=None, help="NBP reference rate in %% -> OKI rate per art. 25")
    p.add_argument("--oki-limit", type=float, default=None, help="optional yearly OKI contribution cap in PLN (none modelled by default)")
    p.add_argument("--no-reinvest-refund", action="store_true", help="IKZE refund is spent, not reinvested")
    p.add_argument("--wage-growth", type=float, default=5.0, help="%% a year growth of IKE/IKZE limits after the known years")
    p.add_argument("--lang", choices=["pl", "en"], default="pl")
    p.add_argument("--output-dir", default=".")
    p.add_argument("--no-files", action="store_true")
    a = p.parse_args(argv)
    if a.yearly is not None and a.yearly <= 0 or a.monthly is not None and a.monthly <= 0:
        p.error("contribution must be > 0")
    if not (1 <= a.years <= 60):
        p.error("--years must be 1-60")
    if not (16 <= a.age <= 100):
        p.error("--age must be 16-100")
    if not (0 <= a.oki_qualified_pct <= 100):
        p.error("--oki-qualified-pct must be 0-100")
    if a.start_year < 2026 or a.start_year > 2100:
        p.error("--start-year must be >= 2026")
    if not (0 <= a.ter < 10):
        p.error("--ter must be 0-10")
    return a


def build_config(a) -> dict:
    b = brokers.resolve(a.broker, a.commission_pct, a.commission_min)
    cfg = ape.default_config(
        monthly=a.monthly if a.monthly is not None else a.yearly / 12.0,
        years=a.years, age=a.age, start_year=a.start_year, inflation_pct=a.inflation, ter_pct=a.ter,
        commission_pct=b["pct"], commission_min=b["min_fee"], pit=a.pit, self_employed=a.self_employed,
        oki_qualified_pct=a.oki_qualified_pct, oki_rate_later_pct=a.oki_rate_later, nbp_rate_pct=a.nbp_rate,
        oki_limit=a.oki_limit, reinvest_refund=not a.no_reinvest_refund, wage_growth_pct=a.wage_growth)
    return cfg, b


def run(a) -> dict:
    cfg, b = build_config(a)
    res = ape.plan(cfg, a.returns)
    later_rate = tax.oki_rate_pct(2028, a.nbp_rate) if a.nbp_rate is not None else a.oki_rate_later
    return {
        "skill": SKILL, "version": VERSION, "lang": a.lang,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "inputs": dict(cfg, returns_pct=a.returns, broker=b["key"], broker_label=b["label"],
                       oki_rate_from_2028_pct=later_rate),
        "results": res,
        "data_provenance": {
            "type": "calculation",
            "market_data": False,
            "as_of": dt.date.today().isoformat(),
            "assumptions": {
                "return_scenarios_pct": a.returns, "inflation_pct": a.inflation, "ter_pct": a.ter,
                "limit_growth_pct": a.wage_growth, "oki_rate_from_2028_pct": later_rate,
                "oki_rate_source": "nbp_rate art. 25" if a.nbp_rate is not None else "assumed (--oki-rate-later)",
                "ikze_refund": "reinvested in regular account" if cfg["reinvest_refund"] else "spent",
                "known_limit_years": sorted(tax.IKE_LIMITS),
            },
            "legal_sources": SOURCES,
            "broker_tariff_notes": brokers.tariff_notes(a.lang, b["overridden"]) if b["key"] != "custom" else [],
        },
    }


def _order_label(order, t) -> str:
    if not order:
        return t("only_regular")
    return " → ".join(t("w_" + w) for w in order) + t("then_regular")


def _cond_label(w, r, t) -> str:
    if w == "regular":
        return t("cond_na")
    if w == "oki":
        return t("cond_oki")
    pot = r["pots"].get(w)
    if not pot:
        return t("cond_na")
    c = pot["conditions"]
    if c["met"]:
        return t("cond_ok")
    if not c["age_ok"]:
        return t("cond_age", a=c["required_age"])
    return t("cond_years")


def render_markdown(r: dict, lang: str) -> str:
    t = T(STRINGS, lang)
    P = lambda x: pln(x, 0, lang)  # noqa: E731
    R = lambda x: pct(x, 1, lang) if abs(x - round(x)) > 1e-9 else pct(x, 0, lang)  # noqa: E731
    inp, res = r["inputs"], r["results"]
    ref = res["reference_scenario_pct"]
    wf = res["waterfall"]
    wref = wf["results"][str(ref)]
    reg = res["standalone"][str(ref)]["regular"]
    y = inp["years"]
    L = ["# " + t("title"), ""]
    # --- answer first
    L += ["## " + t("answer"), "",
          t("ans_main", m=P(inp["monthly"]), y=y, r=R(ref), order=_order_label(wf["order"], t),
            net=P(wref["net_value"]), real=P(wref["net_value_real"]), own=P(wref["contributed_own"])), ""]
    diff = wref["net_value"] - reg["net_value"]
    if wf["order"] and diff > ape.TIE_TOLERANCE_PLN:
        L += [t("ans_vs_regular", reg=P(reg["net_value"]), diff=P(diff)), ""]
    elif wf["order"][:1] == ["ike"]:
        L += [t("ans_same_ike"), ""]
    else:
        L += [t("ans_same_regular"), ""]
    hold = res.get("hold_to_65")
    if hold:
        hb = hold["waterfall"]
        L += [t("ans_hold", age=res["end_age"], y=y, wa=hold["withdraw_age"], order=_order_label(hb["order"], t),
                net=P(hb["net_value"]), real=P(hb["net_value_real"]), reg=P(hold["standalone"]["regular"]["net_value"])), ""]
    L += ["_" + t("ans_not_advice") + "_", ""]
    # --- warnings early
    if res["warnings"]:
        L += ["## " + t("warnings"), ""]
        for w in res["warnings"]:
            L.append("- ⚠️ " + t("warn_" + w, e=res["end_age"], q=pct(inp["oki_qualified_pct"], 0, lang),
                                 r=num(inp["oki_rate_from_2028_pct"], 2, lang)))
        L.append("")
    # --- comparison tables
    L += ["## " + t("compare", y=y), "", t("compare_note"), ""]
    for s in res["scenarios_pct"]:
        st = res["standalone"][str(s)]
        L += ["### " + t("scenario", r=R(s)), "",
              "| {} | {} | {} | {} | {} | {} | {} | {} |".format(t("c_wrapper"), t("c_own"), t("c_net"), t("c_real"),
                                                               t("c_tax"), t("c_fees"), t("c_refund"), t("c_cond")),
              "|---|---:|---:|---:|---:|---:|---:|---|"]
        rows = [(t("w_" + w), st[w], w) for w in ape.WRAPPERS]
        rows.append(("**" + t("w_waterfall", o=_order_label(wf["order"], t)) + "**", wf["results"][str(s)], "waterfall"))
        for label, x, w in rows:
            cond = _cond_label(w, x, t) if w != "waterfall" else ", ".join(
                "{}: {}".format(t("w_" + k), _cond_label(k, x, t)) for k in wf["order"] if k in ("ike", "ikze")) or t("cond_na")
            L.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                label, P(x["contributed_own"]), "**" + P(x["net_value"]) + "**", P(x["net_value_real"]),
                P(x["taxes_total"]), P(x["fees_total"]), P(x["ikze_refunds_total"]) if x["ikze_refunds_total"] else "—", cond))
        L.append("")
    # --- waterfall split
    L += ["## " + t("waterfall"), "",
          t("wf_intro", n=len(wf["ranking_reference"]), r=R(ref), o=_order_label(wf["order"], t)), ""]
    if wf["order_differs_by_scenario"]:
        d = "; ".join("{}: {}".format(R(float(k)), _order_label(v, t)) for k, v in wf["best_order_per_scenario"].items())
        L += [t("wf_differs", d=d), ""]
    split = wref["first_month_split"]
    L += ["| {} | {} | {} |".format(t("c_wrapper"), t("wf_split", y=inp["start_year"]), t("wf_total", n=y)), "|---|---:|---:|"]
    for w in ape.WRAPPERS:
        pot = wref["pots"].get(w, {})
        own_part = pot.get("contributed", 0.0) - (wref["ikze_refunds_reinvested"] if w == "regular" else 0.0)
        if split.get(w, 0.0) > 0 or own_part > 0.5:
            L.append("| {} | {} | {} |".format(t("w_" + w), P(split.get(w, 0.0)), P(own_part)))
    L.append("")
    if wref["ikze_refunds_reinvested"] > 0:
        L += [t("wf_refunds", x=P(wref["ikze_refunds_reinvested"])), ""]
    L += ["**" + t("wf_top", r=R(ref)) + "**", "", "| # | {} | {} |".format(t("c_order"), t("c_net")), "|---|---|---:|"]
    seen = set()
    k = 0
    for row in wf["ranking_reference"]:
        key = round(row["net_value"], 0)
        if key in seen:
            continue  # orders with identical outcome (e.g. anything after an unlimited OKI)
        seen.add(key)
        k += 1
        L.append("| {} | {} | {} |".format(k, _order_label(row["order"], t), P(row["net_value"])))
        if k >= 6:
            break
    L.append("")
    if hold:
        L += ["## " + t("hold", wa=hold["withdraw_age"]), "",
              t("hold_intro", y=y, h=hold["hold_years"], r=R(hold["scenario_pct"])), "",
              "| {} | {} | {} | {} | {} |".format(t("c_wrapper"), t("c_net"), t("c_real"), t("c_tax"), t("c_cond")),
              "|---|---:|---:|---:|---|"]
        for w in ape.WRAPPERS:
            x = hold["standalone"][w]
            L.append("| {} | {} | {} | {} | {} |".format(t("w_" + w), P(x["net_value"]), P(x["net_value_real"]),
                                                       P(x["taxes_total"]), _cond_label(w, x, t)))
        hb = hold["waterfall"]
        L.append("| **{}** | **{}** | {} | {} | |".format(t("w_waterfall", o=_order_label(hb["order"], t)), P(hb["net_value"]),
                                                        P(hb["net_value_real"]), P(hb["taxes_total"])))
        L.append("")
    # --- glossary
    L += ["## " + t("glossary"), ""] + ["- " + t(k) for k in ("g_ike", "g_ikze", "g_oki", "g_regular", "g_etf", "g_ter", "g_compound", "g_real")] + [""]
    # --- assumptions
    b_txt = t("none_broker") if inp["commission_pct"] == 0 and inp["commission_min"] == 0 else t(
        "i_broker_v", label=inp["broker_label"], p=pct(inp["commission_pct"], 2, lang), m=pln(inp["commission_min"], 2, lang))
    okir = (t("i_okirate_v_nbp", n=num(inp["nbp_rate_pct"], 2, lang), r=num(inp["oki_rate_from_2028_pct"], 2, lang))
            if inp["nbp_rate_pct"] is not None else t("i_okirate_v", r=num(inp["oki_rate_from_2028_pct"], 2, lang)))
    L += ["## " + t("inputs"), "", "| | |", "|---|---|",
          "| {} | {} |".format(t("i_monthly"), P(inp["monthly"])),
          "| {} | {} |".format(t("i_horizon"), t("i_horizon_v", y=y, s=inp["start_year"], a=inp["age"], e=res["end_age"])),
          "| {} | {} |".format(t("i_scen"), " / ".join(R(s) for s in res["scenarios_pct"])),
          "| {} | {} |".format(t("i_infl"), pct(inp["inflation_pct"], 1, lang)),
          "| {} | {} |".format(t("i_ter"), pct(inp["ter_pct"], 2, lang)),
          "| {} | {} |".format(t("i_broker"), b_txt),
          "| {} | {} ({}) |".format(t("i_pit"), inp["pit"], pct(tax.MARGINAL_RATES[inp["pit"]] * 100, 0, lang)),
          "| {} | {} |".format(t("i_selfemp"), t("yes") if inp["self_employed"] else t("no")),
          "| {} | {} |".format(t("i_okiq"), pct(inp["oki_qualified_pct"], 0, lang)),
          "| {} | {} |".format(t("i_okirate"), okir),
          "| {} | {} |".format(t("i_refund"), t("i_refund_yes") if inp["reinvest_refund"] else t("i_refund_no")),
          "| {} | {} |".format(t("i_wage"), t("i_wage_v", g=num(inp["wage_growth_pct"], 1, lang))), ""]
    # --- limits
    L += ["### " + t("limits"), "", "| {} | {} | {} | {} | {} |".format(t("c_year"), t("c_ike_lim"), t("c_ikze_lim"), t("c_oki_rate"), t("c_src")),
          "|---|---:|---:|---:|---|"]
    rates = {x["year"]: x["rate_pct"] for x in res["oki_rates"]}
    for lim in res["limits"][:4]:
        src = t("src_known") if lim["source"] == "known" else t("src_projected", g=num(inp["wage_growth_pct"], 1, lang))
        rr = rates.get(lim["year"])
        L.append("| {} | {} | {} | {} | {} |".format(lim["year"], pln(lim["ike"], 2, lang), pln(lim["ikze"], 2, lang),
                                                   pct(rr, 2, lang) if rr is not None else "—", src))
    if len(res["limits"]) > 4:
        L.append("| … | | | | {} |".format(t("limits_more", g=num(inp["wage_growth_pct"], 1, lang))))
    L.append("")
    # --- rules, caveats, tie-in, sources
    L += ["## " + t("rules"), ""] + ["- " + t(k) for k in ("r_ike", "r_ikze", "r_oki", "r_regular")] + [""]
    L += ["## " + t("caveats"), ""] + ["- " + c for c in STRINGS["cav"][lang]] + [""]
    for n in r["data_provenance"]["broker_tariff_notes"]:
        L += ["- " + n]
    L += [t("tie_in"), ""]
    L += ["## " + t("sources"), ""] + ["- " + s for s in SOURCES] + ["", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except (ValueError, KeyError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    md = render_markdown(result, args.lang)
    if args.no_files:
        print(md)
        return 0
    out = Path(args.output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    jp, mp = out / "gpw_account_plan_{}.json".format(stamp), out / "gpw_account_plan_{}.md".format(stamp)
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(md, encoding="utf-8")
    print(md)
    print("JSON: {}\nMarkdown: {}".format(jp, mp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
