#!/usr/bin/env python3
"""GPW ETF Planner - CLI (list / plan / backtest), PL or EN.

    python3 scripts/gpw_etf_planner.py list
    python3 scripts/gpw_etf_planner.py plan --allocation ETFBW20TR=30,ETFIWDA=70 --monthly 1000 --broker mbank
    python3 scripts/gpw_etf_planner.py backtest --allocation ETFBW20TR=30,ETFSP500=70 --monthly 1000

Market data: official GPW archive, instrument type 241 (ETFs). The first run
downloads up to ~500 sessions (about 6-8 minutes, politely rate-limited);
later runs use the cache. ``--offline`` uses the cache only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import brokers, gpw_archive, panel, provenance  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct, pln  # noqa: E402

import etf_catalog as cat  # noqa: E402
import etf_planner_engine as epe  # noqa: E402

VERSION = "1.0.0"
SKILL = "gpw-etf-planner"
MIN_SESSIONS = 20

STRINGS = {
    "t_list": {"pl": "ETF-y na GPW — katalog z danymi z giełdy", "en": "ETFs on GPW — catalog with exchange data"},
    "t_plan": {"pl": "Plan zakupów ETF na ten miesiąc", "en": "ETF buying plan for this month"},
    "t_backtest": {"pl": "Test historyczny: regularne zakupy (DCA) vs jednorazowa wpłata", "en": "Historical test: monthly buying (DCA) vs lump sum"},
    "as_of": {"pl": "Dane na sesję", "en": "Data as of session"},
    "stale": {"pl": "⚠️ UWAGA: dane są nieaktualne o {n} sesji (ostatnia: {d}). Ceny mogły się zmienić.",
              "en": "⚠️ WARNING: data is {n} session(s) old (last: {d}). Prices may have changed."},
    "answer": {"pl": "Odpowiedź w skrócie", "en": "Short answer"},
    # list
    "list_answer": {"pl": "Na GPW notowanych jest {n} ETF-ów. Dla początkującego najprostsze są szerokie ETF-y akcyjne (np. na WIG20, MSCI World, S&P 500) i obligacyjne. ETF-y **lewarowane i odwrotne** ({k} szt.) to produkty dla traderów — nie nadają się do długiego trzymania.",
                    "en": "{n} ETFs are listed on GPW. For a beginner the simplest are broad equity ETFs (e.g. WIG20, MSCI World, S&P 500) and bond ETFs. **Leveraged and inverse** ETFs ({k}) are trading tools — not suitable for long-term holding."},
    "c_name": {"pl": "ETF", "en": "ETF"}, "c_under": {"pl": "Co kupujesz", "en": "What you own"},
    "c_issuer": {"pl": "Wystawca", "en": "Issuer"}, "c_cur": {"pl": "Waluta", "en": "Currency"},
    "c_close": {"pl": "Kurs", "en": "Price"}, "c_turn": {"pl": "Obrót/dzień", "en": "Turnover/day"},
    "c_liq": {"pl": "Płynność", "en": "Liquidity"}, "c_1y": {"pl": "1 rok", "en": "1 year"},
    "c_vol": {"pl": "Zmienność", "en": "Volatility"}, "c_mdd": {"pl": "Max spadek", "en": "Max drop"},
    "c_oki": {"pl": "OKI", "en": "OKI"}, "c_ter": {"pl": "TER", "en": "TER"}, "c_hist": {"pl": "Historia", "en": "History"},
    "liq_ok": {"pl": "dobra", "en": "good"}, "liq_low": {"pl": "niska", "en": "low"},
    "liq_very_low": {"pl": "bardzo niska", "en": "very low"}, "liq_unknown": {"pl": "—", "en": "—"},
    "oki_likely_qualified": {"pl": "prawdop. kwalif.", "en": "likely qual."},
    "oki_not_qualified": {"pl": "nie", "en": "no"},
    "ter_check": {"pl": "sprawdź KID", "en": "check KID"},
    "since": {"pl": "od {d}", "en": "since {d}"},
    "verify_mark": {"pl": " ¹", "en": " ¹"},
    "verify_note": {"pl": "¹ opis wywnioskowany z nazwy — sprawdź w KID i statucie funduszu.",
                    "en": "¹ description inferred from the name — verify in the fund's KID and statute."},
    "list_notes": {"pl": "Jak czytać tabelę", "en": "How to read the table"},
    "ln": {"pl": ["**Obrót/dzień** — mediana wartości obrotu z 20 sesji (tys. zł). Płynność „niska” (< 100 tys. zł) lub „bardzo niska” (< 20 tys. zł) oznacza szerszy spread — składaj zlecenia z limitem ceny.",
                  "**1 rok** — zmiana kursu w ostatnich 252 sesjach (z dywidendami, jeśli fundusz je wypłaca). Puste = fundusz notowany krócej niż rok.",
                  "**Zmienność** — jak mocno kurs zwykle skacze w skali roku (odchylenie standardowe). 15–20% to typowo dla ETF-u akcyjnego, 3–5% dla obligacji.",
                  "**Max spadek** — największy spadek od szczytu do dołka w dostępnej historii (~2 lata lub krócej). W prawdziwej bessie akcje tracą 30–50%.",
                  "**OKI** — wskazówka, czy ETF może być „aktywem kwalifikowanym” na OKI (zwolnienie do 100 000 zł). Tylko fundusze z min. 70% polskich akcji/obligacji w zł. Zawsze sprawdź w statucie funduszu.",
                  "**TER** — roczny koszt funduszu; nie zgadujemy go — sprawdź w dokumencie KID i podaj przez --ter NAZWA=0,5."],
           "en": ["**Turnover/day** — median traded value over 20 sessions (thousand PLN). \"Low\" (< 100k) or \"very low\" (< 20k) liquidity means wider spreads — use limit orders.",
                  "**1 year** — price change over the last 252 sessions (incl. dividends where paid). Empty = listed for less than a year.",
                  "**Volatility** — how much the price typically swings per year (standard deviation). 15–20% is typical for an equity ETF, 3–5% for bonds.",
                  "**Max drop** — the largest peak-to-trough fall in the available history (~2 years or less). In a real bear market equities lose 30–50%.",
                  "**OKI** — hint whether the ETF may be a \"qualified asset\" on OKI (exempt up to 100,000 PLN). Only funds with >= 70% Polish shares/PLN bonds. Always verify in the fund statute.",
                  "**TER** — the fund's yearly cost; we do not guess it — check the KID and pass --ter NAME=0.5."]},
    "lev_warn": {"pl": "⚠️ **ETF-y lewarowane (LV) i odwrotne (ST)**: mnożą DZIENNĄ zmianę indeksu (x2, x3 albo odwrotnie). Przy wahaniach tracą wartość nawet wtedy, gdy indeks po czasie wraca do punktu wyjścia („erozja zmienności”). To narzędzia na dni, nie na lata — początkujący nie powinien ich kupować do portfela długoterminowego.",
                 "en": "⚠️ **Leveraged (LV) and inverse (ST) ETFs**: they multiply the DAILY index move (x2, x3 or inverted). In choppy markets they lose value even when the index ends where it started (\"volatility decay\"). They are tools for days, not years — a beginner should not hold them in a long-term portfolio."},
    "uncat": {"pl": "Nowe ETF-y z archiwum GPW, których nie ma w katalogu (nieskategoryzowane): {l}.", "en": "New ETFs from the GPW archive not in the catalog (uncategorised): {l}."},
    # plan
    "plan_answer": {"pl": "Masz do zainwestowania **{c}** ({m} nowej wpłaty + {k} gotówki). Dla Twojej alokacji docelowej wyliczona lista zakupów to: **{buys}**. Prowizje: {f}. Zostaje {left} na kolejny miesiąc.",
                    "en": "You have **{c}** to invest ({m} new money + {k} cash). For your target allocation the computed buy list is: **{buys}**. Commissions: {f}. {left} is left for next month."},
    "plan_nothing": {"pl": "nic — kwota nie wystarcza na pełną jednostkę z prowizją", "en": "nothing — the amount does not cover a whole unit plus commission"},
    "plan_not_advice": {"pl": "To arytmetyka dla alokacji, którą sam wybrałeś — nie rekomendacja zakupu konkretnych funduszy.",
                        "en": "This is arithmetic for the allocation you chose — not a recommendation to buy particular funds."},
    "buy_list": {"pl": "Lista zakupów (całe jednostki)", "en": "Buy list (whole units)"},
    "c_target": {"pl": "Cel", "en": "Target"}, "c_before": {"pl": "Teraz", "en": "Now"},
    "c_units": {"pl": "Kup (j.)", "en": "Buy (units)"}, "c_value": {"pl": "Wartość", "en": "Value"},
    "c_fee": {"pl": "Prowizja", "en": "Fee"}, "c_drag": {"pl": "Koszt zlecenia", "en": "Order cost"},
    "c_after": {"pl": "Po zakupie", "en": "After"},
    "drag_warn": {"pl": "⚠️ Prowizja przy {n} to {d} wartości zlecenia — więcej niż próg {x}. Przy małych kwotach minimalna prowizja zjada zysk.",
                  "en": "⚠️ The commission on {n} is {d} of the order — above the {x} threshold. On small orders the minimum fee eats the return."},
    "freq": {"pl": "Jak często kupować, żeby prowizje nie zjadały wyników", "en": "How often to buy so fees do not eat the returns"},
    "freq_monthly": {"pl": "Przy {b} każde zlecenie mieści się w {x} kosztu nawet przy zakupach co miesiąc — możesz kupować co miesiąc.",
                     "en": "With {b} every order stays within {x} cost even when buying monthly — monthly buying is fine."},
    "freq_every": {"pl": "Aby prowizja nie przekraczała {x} zlecenia, odkładaj gotówkę i kupuj **co {k} mies.** (minimalne opłacalne zlecenie: ok. {mo}).",
                   "en": "To keep the commission under {x} per order, save the cash and buy **every {k} months** (minimum efficient order: about {mo})."},
    "freq_too_rare": {"pl": "Przy tej kwocie i liczbie ETF-ów trzeba by czekać aż {k} mies. — za długo. Lepiej: mniej ETF-ów, tańszy broker albo **co miesiąc tylko jeden ETF** (najbardziej niedoważony) — wtedy jedna prowizja to {r} wpłaty (użyj --max-orders 1).",
                      "en": "With this amount and number of ETFs you would have to wait {k} months — too long. Better: fewer ETFs, a cheaper broker, or **one ETF a month** (the most underweight) — one commission is then {r} of the contribution (use --max-orders 1)."},
    "max_orders_on": {"pl": "Ten plan kupuje najwyżej {n} ETF w miesiącu (--max-orders {n}): zawsze ten najbardziej niedoważony, więc proporcje wyrównują się w kolejnych miesiącach.",
                      "en": "This plan buys at most {n} ETF(s) a month (--max-orders {n}): always the most underweight, so the proportions even out over the following months."},
    "freq_impossible": {"pl": "Prowizja procentowa {p} sama przekracza próg {x} — rozważ tańszego brokera.", "en": "The percentage commission {p} alone exceeds the {x} threshold — consider a cheaper broker."},
    "freq_table": {"pl": "| ETF | Miesięcznie | Koszt zlecenia co miesiąc | Co ile miesięcy ≤ {x} |", "en": "| ETF | Monthly | Order cost if monthly | Months to stay ≤ {x} |"},
    "never": {"pl": "> 12", "en": "> 12"},
    "rebal": {"pl": "Czy portfel wymaga rebalancingu? (reguła 5/25)", "en": "Does the portfolio need rebalancing? (5/25 rule)"},
    "rebal_txt": {"pl": "Rebalancing = przywracanie proporcji docelowych. Sygnał, gdy udział ETF-u odbiega od celu o więcej niż 5 pkt proc. albo o 25% samego celu (przy małych udziałach). Najpierw używaj nowych wpłat (tak liczy lista wyżej) — sprzedaż na zwykłym koncie oznacza podatek Belki od zysku (na IKE/IKZE nie).",
                  "en": "Rebalancing = restoring the target proportions. Signal when a holding drifts more than 5 pp from target, or 25% of the target itself (for small weights). Use new money first (as the list above does) — selling in a regular account triggers 19% tax on the gain (not in IKE/IKZE)."},
    "c_drift": {"pl": "Odchylenie", "en": "Drift"}, "c_band": {"pl": "Próg", "en": "Band"}, "c_status": {"pl": "Status", "en": "Status"},
    "c_weight": {"pl": "Udział", "en": "Weight"},
    "in_band": {"pl": "w normie", "en": "within band"}, "out_band": {"pl": "⚠️ poza progiem", "en": "⚠️ out of band"},
    "rebal_before": {"pl": "Przed zakupem", "en": "Before buying"}, "rebal_after": {"pl": "Po zakupie", "en": "After buying"},
    "rebal_empty": {"pl": "Brak obecnych pozycji — pierwszy zakup ustawia proporcje.", "en": "No current holdings — the first purchase sets the proportions."},
    "rebal_still": {"pl": "Nawet po wpłacie część pozycji jest poza progiem. Możesz poczekać na kolejne wpłaty albo sprzedać nadwyżkę (pamiętaj o podatku na zwykłym koncie).",
                    "en": "Even after the contribution some holdings are out of band. You can wait for further contributions or sell the excess (mind the tax in a regular account)."},
    "risk": {"pl": "Ryzyko portfela docelowego", "en": "Risk of the target portfolio"},
    "r_equity": {"pl": "Akcje / obligacje i gotówka / inne", "en": "Equities / bonds and cash / other"},
    "r_vol": {"pl": "Zmienność historyczna (roczna)", "en": "Historical volatility (annual)"},
    "r_mdd": {"pl": "Największy spadek w historii danych", "en": "Largest drop in the data"},
    "r_mdd_v": {"pl": "{m} ({a} → {b}) — na każde {v} to ok. {l}", "en": "{m} ({a} → {b}) — about {l} per {v} invested"},
    "r_period": {"pl": "Okres danych (wspólny dla wszystkich ETF-ów)", "en": "Data period (common to all ETFs)"},
    "r_period_v": {"pl": "{a} – {b} ({n} sesji, {y} roku)", "en": "{a} – {b} ({n} sessions, {y} years)"},
    "r_cur": {"pl": "Ekspozycja walutowa", "en": "Currency exposure"},
    "r_oki": {"pl": "Udział prawdopodobnie kwalifikowany do zwolnienia OKI", "en": "Share likely qualifying for the OKI exemption"},
    "r_ter": {"pl": "Średni koszt roczny (TER)", "en": "Average yearly cost (TER)"},
    "r_ter_v": {"pl": "{t} ≈ {c} rocznie przy obecnej wartości", "en": "{t} ≈ {c} a year at the current value"},
    "r_ter_missing": {"pl": "nieznany dla: {l} — sprawdź KID i podaj --ter", "en": "unknown for: {l} — check the KID and pass --ter"},
    "r_short": {"pl": "Dane obejmują tylko {y} roku — to za mało, by ocenić ryzyko. W bessie 2007–2009 szerokie indeksy akcji traciły ponad 50%, a WIG20 ok. 65%.",
                "en": "The data covers only {y} years — too short to judge risk. In the 2007–2009 bear market broad equity indices lost over 50%, and WIG20 about 65%."},
    "r_lev": {"pl": "⚠️ {w} portfela to ETF-y lewarowane/odwrotne — tracą na wartości przy wahaniach rynku i nie nadają się do długiego trzymania. Początkujący nie powinien ich mieć w portfelu długoterminowym.",
              "en": "⚠️ {w} of the portfolio is in leveraged/inverse ETFs — they decay in choppy markets and are not for long-term holding. A beginner should not hold them long term."},
    "r_uncat": {"pl": "⚠️ ETF-y spoza katalogu (brak opisu): {l} — sprawdź w KID, co to za fundusz.", "en": "⚠️ ETFs not in the catalog (no description): {l} — check the KID to see what the fund is."},
    "cur_PLN": {"pl": "PLN", "en": "PLN"}, "cur_PLN-hedged": {"pl": "zabezp. do PLN", "en": "PLN-hedged"},
    "cur_multi": {"pl": "wiele walut (głównie USD)", "en": "multi (mostly USD)"},
    "tie_in": {"pl": "Gdzie trzymać te ETF-y (IKE, IKZE, OKI czy zwykłe konto)? Policz to w **gpw-account-planner** (podstaw swój wiek, horyzont i stawkę PIT), np.:",
               "en": "Where to hold these ETFs (IKE, IKZE, OKI or a regular account)? Compute it with **gpw-account-planner** (use your own age, horizon and PIT rate), e.g.:"},
    "glossary": {"pl": "Słowniczek", "en": "Glossary"},
    "gl": {"pl": ["**ETF** — fundusz notowany na giełdzie; kupujesz go jak akcję, a w środku jest cały koszyk spółek lub obligacji.",
                  "**TER** — roczny koszt funduszu w % (pobierany z jego wartości). Znajdziesz go w dokumencie **KID** (Kluczowe Informacje) na stronie wystawcy.",
                  "**Jednostka** — jeden „kawałek” ETF-u; na GPW kupujesz tylko całe jednostki.",
                  "**DCA (uśrednianie)** — kupowanie za stałą kwotę co miesiąc, bez zgadywania, czy jest drogo, czy tanio.",
                  "**Rebalancing** — przywracanie proporcji (np. 30/70), gdy jedna część urosła bardziej niż druga.",
                  "**Zabezpieczenie walutowe (hedged)** — fundusz usuwa wpływ kursu dolara na wynik; bez zabezpieczenia zarabiasz/tracisz także na kursie USD/PLN.",
                  "**ETF akumulujący** — nie wypłaca dywidend, tylko je reinwestuje (wygodne podatkowo: brak podatku po drodze)."],
           "en": ["**ETF** — an exchange-traded fund; you buy it like a share, and inside is a whole basket of companies or bonds.",
                  "**TER** — the fund's yearly cost in % (taken from its value). You find it in the **KID** (Key Information Document) on the issuer's website.",
                  "**Unit** — one \"piece\" of an ETF; on GPW you buy whole units only.",
                  "**DCA (cost averaging)** — buying for a fixed amount every month, without guessing whether the market is cheap or expensive.",
                  "**Rebalancing** — restoring the proportions (e.g. 30/70) when one part has grown more than the other.",
                  "**Currency hedged** — the fund removes the effect of the dollar exchange rate; unhedged, you also gain/lose on USD/PLN.",
                  "**Accumulating ETF** — does not pay dividends but reinvests them (tax-friendly: no tax along the way)."]},
    # backtest
    "bt_caveat": {"pl": "⚠️ **Bardzo ważne:** test obejmuje tylko {y} roku ({a} – {b}). To o wiele za krótko, by ocenić ETF lub strategię — jeden pełny cykl rynkowy (hossa + bessa) to zwykle 7–10 lat i więcej. Wynik z ostatnich lat nie mówi nic o przyszłych.",
                  "en": "⚠️ **Very important:** the test covers only {y} years ({a} – {b}). That is far too short to judge an ETF or a strategy — one full market cycle (bull + bear) usually takes 7–10 years or more. Recent results say nothing about the future."},
    "bt_answer": {"pl": "Wpłacając {m} co miesiąc (liczba wpłat: {n}, łącznie {inv}) w pierwszej sesji miesiąca, portfel byłby dziś wart **{dv}** ({dg}). Ta sama kwota wpłacona od razu na początku: **{lv}** ({lg}).",
                  "en": "Paying in {m} every month ({n} payments, {inv} in total) on the first session of each month, the portfolio would now be worth **{dv}** ({dg}). The same amount invested at once at the start: **{lv}** ({lg})."},
    "bt_why": {"pl": "Jednorazowa wpłata zwykle wygrywa, gdy rynek rośnie (pieniądze dłużej pracują); regularne zakupy chronią przed złym momentem wejścia i są naturalne, gdy odkładasz z pensji. Większość ludzi nie ma całej kwoty od razu — wtedy DCA to po prostu sposób inwestowania.",
               "en": "A lump sum usually wins when markets rise (money works longer); monthly buying protects against bad timing and is natural when saving from a salary. Most people do not have the whole amount up front — then DCA is simply how they invest."},
    "c_strategy": {"pl": "Strategia", "en": "Strategy"}, "c_invested": {"pl": "Wpłacone", "en": "Invested"},
    "c_final": {"pl": "Wartość końcowa", "en": "Final value"}, "c_gain": {"pl": "Zysk", "en": "Gain"},
    "c_fees": {"pl": "Prowizje", "en": "Commissions"}, "c_ann": {"pl": "Wynik roczny", "en": "Annual return"},
    "dca": {"pl": "Regularnie co miesiąc (DCA)", "en": "Monthly (DCA)"}, "lump": {"pl": "Jednorazowo na start", "en": "Lump sum at start"},
    "bt_notes": {"pl": "Zakupy po cenie zamknięcia pierwszej sesji miesiąca, całe jednostki, z prowizją; dywidendy (jeśli są) traktowane jak reinwestowane; bez podatku (np. na IKE). Wynik roczny DCA to stopa ważona kapitałem (XIRR) — liczy, że późniejsze wpłaty pracowały krócej, więc nie porównuj go wprost z wynikiem wpłaty jednorazowej; porównuj kwoty końcowe.",
                 "en": "Buys at the close of the first session of each month, whole units, with commission; dividends (if any) treated as reinvested; no tax (e.g. IKE). The DCA annual figure is the money-weighted return (XIRR) — it accounts for later payments working for a shorter time, so do not compare it directly with the lump sum's figure; compare the final amounts."},
    "warnings": {"pl": "Ostrzeżenia", "en": "Warnings"},
    "sources": {"pl": "Źródła", "en": "Sources"},
    "catalog_note": {"pl": "Katalog ETF-ów opracowano {d} na podstawie nazw i danych GPW; opisy i wskazówki OKI sprawdź w KID i statucie funduszu.",
                     "en": "The ETF catalog was compiled on {d} from names and GPW data; verify descriptions and OKI hints in each fund's KID and statute."},
}

SOURCES = [
    "GPW — Archiwum notowań, ETF (typ 241): https://www.gpw.pl/archiwum-notowan",
    "GPW — lista ETF: https://www.gpw.pl/etf",
    "Ustawa o OKI (Dz.U. 2026 poz. 1098), art. 26 — aktywa kwalifikowane",
    "Reguła 5/25 (Larry Swedroe); C. Jaconetti, F. Kinniry, Y. Zilbering, „Best practices for portfolio rebalancing”, Vanguard Research (2010)",
]


def _kv_list(text: str, kind=float):
    out = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise argparse.ArgumentTypeError("expected NAME=value pairs, e.g. ETFBW20TR=30,ETFIWDA=70")
        k, v = part.split("=", 1)
        try:
            out[k.strip().upper()] = kind(v.strip())
        except ValueError:
            raise argparse.ArgumentTypeError("bad number in {!r}".format(part))
    if not out:
        raise argparse.ArgumentTypeError("empty list")
    return out


def parse_args(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lang", choices=["pl", "en"], default="pl")
    common.add_argument("--output-dir", default=".")
    common.add_argument("--no-files", action="store_true")
    common.add_argument("--offline", action="store_true")
    common.add_argument("--cache-dir", default=None)
    common.add_argument("--as-of", default=None, help="YYYY-MM-DD, last session to use")
    common.add_argument("--sessions", type=int, default=500, help="sessions to load (default 500 ~ 2 years)")
    common.add_argument("--ter", type=_kv_list, default={}, help="known TERs from the KID, e.g. ETFBW20TR=0.5,ETFIWDA=0.2")
    p = argparse.ArgumentParser(description="GPW ETF planner (PL/EN)")
    sub = p.add_subparsers(dest="mode", required=True)
    pl = sub.add_parser("list", parents=[common], help="catalog with live stats")
    pl.add_argument("--category", choices=cat.CATEGORIES, default=None)
    for name in ("plan", "backtest"):
        sp = sub.add_parser(name, parents=[common])
        sp.add_argument("--allocation", type=_kv_list, required=True, help="target weights, e.g. ETFBW20TR=30,ETFIWDA=70")
        sp.add_argument("--monthly", type=float, required=True, help="monthly amount in PLN")
        sp.add_argument("--broker", choices=sorted(brokers.BROKERS), default="mbank")
        sp.add_argument("--commission-pct", type=float, default=None)
        sp.add_argument("--commission-min", type=float, default=None)
        sp.add_argument("--max-orders", type=int, default=None, help="buy at most N ETFs per month (rotation; 1 = one fee)")
        if name == "plan":
            sp.add_argument("--holdings", type=lambda s: _kv_list(s, float), default={}, help="current units, e.g. ETFBW20TR=10")
            sp.add_argument("--cash", type=float, default=0.0, help="uninvested cash already on the account")
    a = p.parse_args(argv)
    if a.sessions < MIN_SESSIONS or a.sessions > 2000:
        p.error("--sessions must be {}-2000".format(MIN_SESSIONS))
    if a.as_of:
        try:
            dt.date.fromisoformat(a.as_of)
        except ValueError:
            p.error("--as-of must be YYYY-MM-DD")
    if a.mode in ("plan", "backtest"):
        if a.monthly <= 0:
            p.error("--monthly must be > 0")
        if any(v < 0 for v in a.allocation.values()) or sum(a.allocation.values()) <= 0:
            p.error("--allocation weights must be >= 0 and sum to > 0")
        if a.max_orders is not None and a.max_orders < 1:
            p.error("--max-orders must be >= 1")
        if a.mode == "plan" and (a.cash < 0 or any(v < 0 for v in a.holdings.values())):
            p.error("--cash and --holdings must be >= 0")
    return a


# ---------------------------------------------------------------------------

def load_data(a):
    cache_dir = Path(a.cache_dir).expanduser() if a.cache_dir else None
    end = dt.date.fromisoformat(a.as_of) if a.as_of else None
    print("Loading GPW ETF sessions (first run downloads ~2 years, several minutes)...", file=sys.stderr)
    sessions = gpw_archive.load_sessions(a.sessions, end=end, type_id=gpw_archive.TYPE_ETF, cache_dir=cache_dir,
                                         offline=a.offline)
    if len(sessions) < MIN_SESSIONS:
        raise gpw_archive.GPWFetchError("Only {} ETF sessions available (need {}); run without --offline.".format(
            len(sessions), MIN_SESSIONS))
    pnl = panel.build_panel(sessions)
    last_date = sessions[-1]["date"]
    listed_now = {r["name"] for r in sessions[-1]["rows"]}
    prov = provenance.build(
        as_of=dt.date.fromisoformat(last_date),
        sources=[SOURCES[0]], sessions_used=len(sessions), universe_size=len(listed_now),
        notes=["Prices adjusted for distributions/splits via the GPW reference-price change (gpwlib.panel).",
               "ETF catalog compiled {} - descriptions marked 'name' are inferred from the fund name.".format(cat.CATALOG_DATE)],
        survivorship_free=False)
    return pnl, listed_now, last_date, prov


def _stats_for(pnl, name):
    s = pnl[name]
    return epe.etf_stats(s.dates, s.close, s.turnover_k)


def _broker(a):
    b = brokers.resolve(a.broker, a.commission_pct, a.commission_min)
    return b


def _check_names(names, pnl, last_date):
    missing = [n for n in names if n not in pnl]
    if missing:
        raise ValueError("unknown ETF short name(s) or no data: {} (run `list` to see GPW ETF names)".format(", ".join(missing)))
    old = [n for n in names if pnl[n].dates[-1] != last_date]
    if old:
        raise ValueError("no quote on the last session {} for: {} (delisted or suspended?)".format(last_date, ", ".join(old)))


def run(a) -> dict:
    pnl, listed_now, last_date, prov = load_data(a)
    base = {"skill": SKILL, "version": VERSION, "lang": a.lang, "mode": a.mode,
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "catalog_date": cat.CATALOG_DATE, "data_provenance": prov}
    ters = {k: v for k, v in a.ter.items()}
    if a.mode == "list":
        rows = []
        for name in sorted(listed_now):
            c = cat.classify(name)
            if a.category and c["category"] != a.category:
                continue
            st = _stats_for(pnl, name) if name in pnl else {"sessions": 0}
            rows.append(dict(c, stats=st, ter_pct=ters.get(name)))
        order = {k: i for i, k in enumerate(cat.CATEGORIES)}
        rows.sort(key=lambda r: (order[r["category"]], -(r["stats"].get("median_turnover_k") or 0)))
        missing_from_archive = sorted(set(cat.CATALOG) - listed_now)
        base.update(etfs=rows, count=len(rows), catalog_not_in_archive=missing_from_archive,
                    uncategorised=[r["name"] for r in rows if not r["in_catalog"]])
        return base
    weights = epe.normalise_weights(a.allocation)
    _check_names(list(weights), pnl, last_date)
    b = _broker(a)
    dated = {k: dict(zip(pnl[k].dates, pnl[k].close)) for k in weights}
    info = {k: dict(cat.classify(k), stats=_stats_for(pnl, k), ter_pct=ters.get(k)) for k in weights}
    base.update(allocation=weights, broker={k: b[k] for k in ("key", "label", "pct", "min_fee", "overridden")},
                tariff_notes=brokers.tariff_notes(a.lang, b["overridden"]), etfs=info,
                allocation_sum_input=sum(a.allocation.values()))
    if a.mode == "plan":
        prices = {k: pnl[k].close[-1] for k in weights}
        unknown_hold = [k for k in a.holdings if k not in pnl]
        if unknown_hold:
            raise ValueError("unknown ETF in --holdings: {}".format(", ".join(unknown_hold)))
        hp = dict(prices)
        hp.update({k: pnl[k].close[-1] for k in a.holdings if k not in prices})
        values_before = {k: a.holdings.get(k, 0.0) * hp[k] for k in weights}
        buys = epe.plan_buys(hp, weights, a.holdings, a.monthly + a.cash, b["pct"], b["min_fee"], a.max_orders)
        values_after = {r["name"]: r["value_after"] for r in buys["rows"]}
        risk = epe.portfolio_risk(dated, weights)
        known_ter = {k: ters[k] for k in weights if k in ters}
        ter_w = sum(weights[k] * ters[k] for k in known_ter) / sum(weights[k] for k in known_ter) if known_ter else None
        base.update(
            monthly=a.monthly, cash=a.cash, holdings=a.holdings, buys=buys, max_orders=a.max_orders,
            frequency=epe.order_frequency(a.monthly, weights, b["pct"], b["min_fee"]),
            rebalance_before=epe.rebalance_check(values_before, weights),
            rebalance_after=epe.rebalance_check(values_after, weights),
            risk=risk, ter_weighted_pct=ter_w, ter_missing=[k for k in weights if k not in ters],
            ter_complete=len(known_ter) == len(weights))
        return base
    bt = epe.backtest(dated, weights, a.monthly, b["pct"], b["min_fee"], a.max_orders)
    base.update(monthly=a.monthly, backtest=bt, max_orders=a.max_orders, risk=epe.portfolio_risk(dated, weights))
    return base


# ---------------------------------------------------------------------------
# Markdown

def _hdr(r, t, lang, title):
    prov = r["data_provenance"]
    L = []
    if prov["freshness"]["is_stale"]:
        L += ["> " + t("stale", n=prov["freshness"]["sessions_behind"], d=prov["as_of"]), ""]
    L += ["# " + t(title), "", "**{}:** {}".format(t("as_of"), prov["as_of"]), ""]
    return L


def _p(x, lang, d=1, signed=False):
    return pct(x * 100, d, lang, signed=signed) if x is not None else "—"


def _under(c, lang, t):
    u = c["underlying_pl"] if lang == "pl" else c["underlying_en"]
    return u + (t("verify_mark") if c["confidence"] == "name" else "")


def _footer(r, t, lang):
    L = ["## " + t("sources"), ""] + ["- " + s for s in SOURCES]
    L += ["- " + t("catalog_note", d=r["catalog_date"])]
    for n in r.get("tariff_notes", []):
        L.append("- " + n)
    L += ["", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return L


def render_list(r, lang):
    t = T(STRINGS, lang)
    L = _hdr(r, t, lang, "t_list")
    n_lev = sum(1 for e in r["etfs"] if e["is_leveraged_or_short"])
    L += ["## " + t("answer"), "", t("list_answer", n=r["count"], k=n_lev), ""]
    cur = None
    for e in r["etfs"]:
        if e["category"] != cur:
            cur = e["category"]
            L += ["", "### " + cat.CATEGORY_LABELS[cur][lang], "",
                  "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                      t("c_name"), t("c_under"), t("c_issuer"), t("c_cur"), t("c_close"), t("c_turn"), t("c_liq"),
                      t("c_1y"), t("c_vol"), t("c_mdd"), t("c_oki"), t("c_ter")),
                  "|---|---|---|---|---:|---:|---|---:|---:|---:|---|---|"]
        s = e["stats"]
        r1 = _p(s.get("return_1y"), lang, 1, True) if s.get("return_1y") is not None else (
            t("since", d=s["first_date"]) if s.get("first_date") else "—")
        L.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            "**{}**".format(e["name"]), _under(e, lang, t), e["issuer"] or "?", t("cur_" + e["currency"]) if ("cur_" + e["currency"]) in STRINGS else e["currency"],
            num(s.get("last_close"), 2, lang), num(s.get("median_turnover_k"), 0, lang) + (" tys." if lang == "pl" else "k"),
            t("liq_" + s.get("liquidity", "unknown")), r1, _p(s.get("volatility"), lang), _p(s.get("max_drawdown"), lang),
            t("oki_" + e["oki_hint"]), pct(e["ter_pct"], 2, lang) if e.get("ter_pct") is not None else t("ter_check")))
    L += ["", t("verify_note"), ""]
    if n_lev:
        L += [t("lev_warn"), ""]
    if r["uncategorised"]:
        L += [t("uncat", l=", ".join(r["uncategorised"])), ""]
    L += ["## " + t("list_notes"), ""] + ["- " + x for x in STRINGS["ln"][lang]] + [""]
    L += ["## " + t("glossary"), ""] + ["- " + x for x in STRINGS["gl"][lang]] + [""]
    return "\n".join(L + _footer(r, t, lang))


def _risk_block(r, t, lang, portfolio_value):
    risk = r["risk"]
    comp = risk["composition"]
    P = lambda x: pln(x, 0, lang)  # noqa: E731
    L = ["## " + t("risk"), "", "| | |", "|---|---|",
         "| {} | {} / {} / {} |".format(t("r_equity"), _p(comp["equity"], lang, 0), _p(comp["bonds_cash"], lang, 0), _p(comp["other"], lang, 0)),
         "| {} | {} |".format(t("r_vol"), _p(risk.get("volatility"), lang))]
    if risk.get("max_drawdown") is not None:
        v = 10000.0  # a round reference amount reads better for beginners than a tiny first purchase
        L.append("| {} | {} |".format(t("r_mdd"), t("r_mdd_v", m=_p(risk["max_drawdown"], lang), a=risk["mdd_peak"], b=risk["mdd_trough"],
                                                     v=P(v), l=P(v * risk["max_drawdown"]))))
    if risk.get("start"):
        L.append("| {} | {} |".format(t("r_period"), t("r_period_v", a=risk["start"], b=risk["end"], n=risk["common_sessions"],
                                                       y=num(risk.get("years") or 0, 1, lang))))
    curtxt = ", ".join("{} {}".format(t("cur_" + k) if ("cur_" + k) in STRINGS else k, _p(v, lang, 0))
                       for k, v in sorted(comp["currency"].items(), key=lambda x: -x[1]))
    L.append("| {} | {} |".format(t("r_cur"), curtxt))
    L.append("| {} | {} |".format(t("r_oki"), _p(comp["oki_likely_qualified"], lang, 0)))
    if "ter_weighted_pct" in r:
        if r["ter_complete"]:
            L.append("| {} | {} |".format(t("r_ter"), t("r_ter_v", t=pct(r["ter_weighted_pct"], 2, lang),
                                                         c=P((portfolio_value or 0) * r["ter_weighted_pct"] / 100))))
        else:
            L.append("| {} | {} |".format(t("r_ter"), t("r_ter_missing", l=", ".join(r["ter_missing"]))))
    L.append("")
    if (risk.get("years") or 0) < epe.SHORT_HISTORY_YEARS:
        L += ["- " + t("r_short", y=num(risk.get("years") or 0, 1, lang))]
    if comp["leveraged_or_short"] > 0:
        L += ["- " + t("r_lev", w=_p(comp["leveraged_or_short"], lang, 0))]
    unc = [k for k, e in r["etfs"].items() if not e["in_catalog"]]
    if unc:
        L += ["- " + t("r_uncat", l=", ".join(unc))]
    L.append("")
    return L


def _tie_in(r, t):
    q = round(r["risk"]["composition"]["oki_likely_qualified"] * 100)
    return [t("tie_in"), "", "```bash",
            "python3 ../gpw-account-planner/scripts/gpw_account_planner.py --monthly {} --years 25 --age 30 --pit scale12 --oki-qualified-pct {} --lang {}".format(
                int(r["monthly"]), q, r["lang"]), "```", ""]


def render_plan(r, lang):
    t = T(STRINGS, lang)
    P = lambda x: pln(x, 2, lang)  # noqa: E731
    L = _hdr(r, t, lang, "t_plan")
    b = r["buys"]
    items = ["{} j. {} (~{})".format(x["buy_units"], x["name"], P(x["buy_value"])) if lang == "pl" else
             "{} units of {} (~{})".format(x["buy_units"], x["name"], P(x["buy_value"])) for x in b["rows"] if x["buy_units"] > 0]
    L += ["## " + t("answer"), "",
          t("plan_answer", c=P(b["cash_in"]), m=P(r["monthly"]), k=P(r["cash"]), buys=", ".join(items) if items else t("plan_nothing"),
            f=P(b["fees"]), left=P(b["cash_left"])), "", "_" + t("plan_not_advice") + "_", ""]
    warns = []
    for x in b["rows"]:
        if x["fee_drag_pct"] is not None and x["fee_drag_pct"] > epe.MAX_FEE_DRAG_PCT:
            warns.append(t("drag_warn", n=x["name"], d=pct(x["fee_drag_pct"], 2, lang), x=pct(epe.MAX_FEE_DRAG_PCT, 1, lang)))
    if r["risk"]["composition"]["leveraged_or_short"] > 0:
        warns.append(t("r_lev", w=_p(r["risk"]["composition"]["leveraged_or_short"], lang, 0)))
    if warns:
        L += ["## " + t("warnings"), ""] + ["- " + w for w in warns] + [""]
    L += ["## " + t("buy_list"), "",
          "| ETF | {} | {} | {} | {} | {} | {} | {} | {} |".format(t("c_close"), t("c_target"), t("c_before"), t("c_units"),
                                                                 t("c_value"), t("c_fee"), t("c_drag"), t("c_after")),
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for x in b["rows"]:
        L.append("| **{}** | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            x["name"], P(x["price"]), _p(x["target_weight"], lang), _p(x["weight_before"], lang) if b["portfolio_before"] > 0 else "—",
            "**{}**".format(x["buy_units"]), P(x["buy_value"]), P(x["fee"]),
            pct(x["fee_drag_pct"], 2, lang) if x["fee_drag_pct"] is not None else "—", _p(x["weight_after"], lang)))
    L.append("")
    # frequency
    f = r["frequency"]
    br = r["broker"]
    btxt = "{} ({}, min. {})".format(br["label"], pct(br["pct"], 2, lang), P(br["min_fee"]))
    X = pct(f["max_drag_pct"], 1, lang)
    L += ["## " + t("freq"), ""]
    if f["impossible"]:
        L += [t("freq_impossible", p=pct(br["pct"], 2, lang), x=X), ""]
    elif f["suggested_every_months"] == 1:
        L += [t("freq_monthly", b=btxt, x=X), ""]
    elif f["suggested_every_months"] and not f["too_infrequent"]:
        L += [t("freq_every", x=X, k=f["suggested_every_months"], mo=P(f["min_order_value"])), ""]
    else:
        k = f["suggested_every_months"] or "> 12"
        L += [t("freq_too_rare", k=k, r=pct(f["rotation_one_order_drag_pct"], 2, lang)), ""]
    if r.get("max_orders"):
        L += [t("max_orders_on", n=r["max_orders"]), ""]
    L += [t("freq_table", x=X), "|---|---:|---:|---:|"]
    for k, v in f["per_etf"].items():
        L.append("| {} | {} | {} | {} |".format(k, P(v["monthly_amount"]), pct(v["drag_monthly_pct"], 2, lang) if v["drag_monthly_pct"] is not None else "—",
                                               v["months"] if v["months"] else t("never")))
    L.append("")
    # rebalancing
    L += ["## " + t("rebal"), "", t("rebal_txt"), ""]
    rb, ra = r["rebalance_before"], r["rebalance_after"]
    if rb["portfolio_value"] <= 0:
        L += [t("rebal_empty"), ""]
    else:
        L += ["| ETF | {} | {} ({}) | {} | {} ({}) | {} |".format(t("c_target"), t("c_weight"), t("rebal_before"), t("c_drift"),
                                                                t("c_weight"), t("rebal_after"), t("c_status")),
              "|---|---:|---:|---:|---:|---|"]
        for x, y in zip(rb["rows"], ra["rows"]):
            L.append("| {} | {} | {} | {} pp (± {}) | {} | {} |".format(
                x["name"], _p(x["target"], lang), _p(x["weight"], lang), num(x["drift_pp"], 1, lang), num(x["band_pp"], 1, lang),
                _p(y["weight"], lang), t("out_band") if y["out_of_band"] else t("in_band")))
        L.append("")
        if ra["any_out_of_band"]:
            L += [t("rebal_still"), ""]
    L += _risk_block(r, t, lang, b["portfolio_after"])
    L += _tie_in(r, t)
    L += ["## " + t("glossary"), ""] + ["- " + x for x in STRINGS["gl"][lang]] + [""]
    return "\n".join(L + _footer(r, t, lang))


def render_backtest(r, lang):
    t = T(STRINGS, lang)
    P = lambda x: pln(x, 0, lang)  # noqa: E731
    bt = r["backtest"]
    L = _hdr(r, t, lang, "t_backtest")
    L += [t("bt_caveat", y=num(bt["years"], 1, lang), a=bt["start"], b=bt["end"]), ""]
    d, l = bt["dca"], bt["lump_sum"]
    L += ["## " + t("answer"), "",
          t("bt_answer", m=P(r["monthly"]), n=bt["months"], inv=P(bt["invested"]), dv=P(d["value"]),
            dg=pct(d["gain_pct"], 1, lang, signed=True), lv=P(l["value"]), lg=pct(l["gain_pct"], 1, lang, signed=True)), "",
          t("bt_why"), ""]
    L += ["| {} | {} | {} | {} | {} | {} |".format(t("c_strategy"), t("c_invested"), t("c_final"), t("c_gain"), t("c_fees"), t("c_ann")),
          "|---|---:|---:|---:|---:|---:|",
          "| {} | {} | **{}** | {} | {} | {} |".format(t("dca"), P(bt["invested"]), P(d["value"]), P(d["gain"]), pln(d["fees"], 2, lang),
                                                    _p(d["money_weighted_return"], lang, 1, True)),
          "| {} | {} | **{}** | {} | {} | {} |".format(t("lump"), P(bt["invested"]), P(l["value"]), P(l["gain"]), pln(l["fees"], 2, lang),
                                                    _p(l["annualised_return"], lang, 1, True)), "", t("bt_notes"), ""]
    L += _risk_block(r, t, lang, d["value"])
    L += _tie_in(r, t)
    return "\n".join(L + _footer(r, t, lang))


RENDER = {"list": render_list, "plan": render_plan, "backtest": render_backtest}
PREFIX = {"list": "gpw_etf_list", "plan": "gpw_etf_plan", "backtest": "gpw_etf_backtest"}


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except (gpw_archive.GPWFetchError, ValueError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    md = RENDER[args.mode](result, args.lang)
    if args.no_files:
        print(md)
        return 0
    out = Path(args.output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    jp, mp = out / "{}_{}.json".format(PREFIX[args.mode], stamp), out / "{}_{}.md".format(PREFIX[args.mode], stamp)
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    mp.write_text(md, encoding="utf-8")
    print(md)
    print("JSON: {}\nMarkdown: {}".format(jp, mp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
