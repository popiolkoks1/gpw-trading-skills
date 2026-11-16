# GPW Trading Skills

[![CI](https://github.com/popiolkoks1/gpw-trading-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/popiolkoks1/gpw-trading-skills/actions/workflows/ci.yml)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![skille](https://img.shields.io/badge/skille-27-green)
![testy](https://img.shields.io/badge/testy-1671-green)
![licencja](https://img.shields.io/badge/licencja-MIT-lightgrey)

**27 skilli dla Claude'a do polskiej giełdy (GPW).** Prowadzą od pierwszych
kroków (czy w ogóle inwestować, IKE/IKZE/OKI, ETF-y) po proces zawodowca
(reżim rynku → skaner → dossier spółki → wielkość pozycji → bramka → dziennik
→ ocena wyników). Darmowe, oficjalne dane, bez kluczy API. Działa po polsku
i po angielsku.

🇬🇧 [English → README.md](README.md) · 📖 [Jak używać na co dzień → WORKFLOW](docs/WORKFLOW.md)

> ⚠️ Edukacja, nie porada inwestycyjna. Skille nie składają zleceń i nigdy nie mówią „kup X”.

## Instalacja (Claude Code)

```bash
git clone https://github.com/popiolkoks1/gpw-trading-skills.git
cp -R gpw-trading-skills/skills/* ~/.claude/skills/
```

Wymagany Python 3.9+, bez dodatkowych bibliotek. Pierwsze uruchomienie pobiera
około 2 lat notowań z gpw.pl (jednorazowo 20–40 min), potem wszystko działa
w kilka sekund z lokalnego cache.

W claude.ai uruchom `python3 scripts/package_skills.py`, a potem wgraj
`dist/*.skill` w **Customize → Skills**.

## Po prostu zapytaj

| Pytanie | Skill |
|---|---|
| „Nie znam się na inwestowaniu, od czego zacząć?” | `gpw-start` |
| „IKE czy IKZE? Czy warto OKI?” | `gpw-account-planner` |
| „Jakie ETF-y z GPW i ile jednostek kupić za 1000 zł?” | `gpw-etf-planner` |
| „Co dziś na GPW? Co wymaga mojej uwagi?” | `gpw-daily-brief` |
| „Czy rynek jest w korekcie? Ile mieć teraz w akcjach?” | `gpw-market-regime`, `gpw-exposure-coach` |
| „Które spółki są liderami / blisko wybicia?” | `gpw-trend-screener` |
| „Zbierz wszystko o KĘTY: argumenty za i przeciw” | `gpw-research-dossier` |
| „Co zakłada cena PKO? Czy CD Projekt jest drogi?” | `gpw-valuation` |
| „Kto gra na spadek MODIVO? Czy zarząd kupuje akcje?” | `gpw-ownership` |
| „Ile akcji kupić przy ryzyku 1%? Czy mogę wejść?” | `gpw-position-sizer`, `gpw-pre-trade-gate` |
| „Ile stracę, jeśli powtórzy się 2008?” | `gpw-risk-lab` |
| „Czy ta strategia naprawdę działała na GPW?” | `gpw-backtester` |

## 27 skilli w 6 grupach

| | Skill | Co daje | Dane |
|---|---|---|---|
| 🌱 **Start** | [`gpw-start`](skills/gpw-start/SKILL.md) | Gotowość do inwestowania (poduszka, długi), udział akcji, plan krok po kroku, słowniczek | lokalnie |
| | [`gpw-account-planner`](skills/gpw-account-planner/SKILL.md) | IKE / IKZE / OKI / zwykłe konto na Twoim horyzoncie, limity 2026–27, kolejność wpłat | ustawy |
| | [`gpw-etf-planner`](skills/gpw-etf-planner/SKILL.md) | Katalog ETF-ów z GPW, plan zakupów w całych jednostkach z prowizjami, rebalancing | GPW |
| 🧭 **Rynek** | [`gpw-market-breadth`](skills/gpw-market-breadth/SKILL.md) | Kondycja rynku 0–100: ile spółek rośnie, nad średnimi, nowe szczyty | GPW |
| | [`gpw-sector-trend`](skills/gpw-sector-trend/SKILL.md) | Mocne i słabe sektory; `--rrg`: wykres rotacji (liderzy → słabnący → maruderzy) | GPW |
| | [`gpw-market-regime`](skills/gpw-market-regime/SKILL.md) | Hossa / presja / korekta / bessa, dni dystrybucji, ryzyko szczytu | GPW |
| | [`gpw-macro-regime`](skills/gpw-macro-regime/SKILL.md) | Stopy NBP, inflacja, złoty, spread PL–DE: kiedy makro pomaga, a kiedy szkodzi | NBP, EBC, Eurostat |
| | [`gpw-exposure-coach`](skills/gpw-exposure-coach/SKILL.md) | Jedna postawa: ile w akcjach, ryzyko na transakcję, bezpiecznik po stratach | raporty |
| 🔍 **Spółki** | [`gpw-trend-screener`](skills/gpw-trend-screener/SKILL.md) | Liderzy według Minerviniego, siła relatywna, formacje VCP, pivot i stop | GPW |
| | [`gpw-quality-screener`](skills/gpw-quality-screener/SKILL.md) | F-score Piotroskiego, ROE, czerwone flagi w finansach; osobno banki | gpw.pl |
| | [`gpw-valuation`](skills/gpw-valuation/SKILL.md) | Jaki wzrost zakłada cena (odwrócony DCF), przedział wartości, porównania z sektorem | gpw.pl, BondSpot |
| | [`gpw-dividend-analyzer`](skills/gpw-dividend-analyzer/SKILL.md) | Historia i stabilność dywidend, stopa netto po Belce vs IKE, pułapki | GPW, ESPI |
| | [`gpw-ownership`](skills/gpw-ownership/SKILL.md) | Krótkie pozycje (rejestr KNF od 2012), transakcje insiderów, portfele OFE | KNF, ESPI |
| | [`gpw-earnings-reaction`](skills/gpw-earnings-reaction/SKILL.md) | Jak rynek przyjął wyniki: luka, nadzwyczajna stopa zwrotu, dryf po raporcie | ESPI, GPW |
| | [`gpw-event-calendar`](skills/gpw-event-calendar/SKILL.md) | Wyniki, WZA, dywidendy, rewizje indeksów, RPP, CPI; czy coś wypada w okresie trzymania | ESPI, NBP |
| | [`gpw-pair-trading`](skills/gpw-pair-trading/SKILL.md) | Czy dwie spółki chodzą razem (kointegracja); „PKO czy Pekao?” | GPW |
| | [`gpw-research-dossier`](skills/gpw-research-dossier/SKILL.md) | Wszystko o spółce w jednym pliku + debata byk vs niedźwiedź oparta na faktach | raporty |
| 🎯 **Decyzja** | [`gpw-watchlist`](skills/gpw-watchlist/SKILL.md) | Obserwowane spółki z etapami i alertami (pivot, stop, wybicie, zdarzenia) | raporty |
| | [`gpw-position-sizer`](skills/gpw-position-sizer/SKILL.md) | Ile akcji kupić przy założonym ryzyku, z prowizjami i podatkiem | lokalnie |
| | [`gpw-pre-trade-gate`](skills/gpw-pre-trade-gate/SKILL.md) | 20 kontroli przed zakupem → GO / GO Z WARUNKAMI / NO-GO | raporty |
| 🛡️ **Ryzyko** | [`gpw-portfolio-check`](skills/gpw-portfolio-check/SKILL.md) | Koncentracja, korelacje, beta, VaR, płynność portfela | GPW |
| | [`gpw-risk-lab`](skills/gpw-risk-lab/SKILL.md) | Twój portfel w kryzysach 2008–2022 i szokach; zabezpieczenie kontraktem FW20 | GPW, NBP |
| 📈 **Nauka** | [`gpw-trade-journal`](skills/gpw-trade-journal/SKILL.md) | Dziennik tez z warunkami unieważnienia; tryb „na niby” bez pieniędzy | lokalnie |
| | [`gpw-trade-review`](skills/gpw-trade-review/SKILL.md) | Wyniki, „proces vs szczęście”, szacunek PIT-38 | lokalnie |
| | [`gpw-signal-postmortem`](skills/gpw-signal-postmortem/SKILL.md) | Czy sygnały skilli naprawdę biły WIG (po 5/20/60 sesjach) | GPW |
| | [`gpw-backtester`](skills/gpw-backtester/SKILL.md) | Uczciwy test strategii: prowizje, poślizg, podatek, test przeuczenia | GPW |
| | [`gpw-daily-brief`](skills/gpw-daily-brief/SKILL.md) | Uruchamia cały proces po sesji i daje jedną stronę | raporty |

„Raporty” oznaczają, że skill korzysta z wyników innych skilli (pliki JSON).

## Jak to się łączy

```
PO SESJI     breadth + sector + regime → exposure-coach
             screener + quality + ownership + earnings → watchlist ┐
             event-calendar ───────────────────────────────────────┴→ daily-brief (1 strona)

DECYZJA      research-dossier → position-sizer → pre-trade-gate → trade-journal

CO JAKIŚ CZAS  tydzień: macro, portfolio-check, risk-lab · miesiąc: trade-review, signal-postmortem
               zanim zmienisz regułę: backtester
```

## Co wyróżnia ten zestaw

- **Polskie realia:** PLN, całe akcje, minimalne prowizje, Belka i FIFO, IKE/IKZE/OKI, ESPI, KNF, rozliczenie T+2.
- **Liczby z kodu, nie z pamięci modelu.** Skrypty liczą, Claude tłumaczy. 1671 testów na Linuksie, macOS i Windowsie.
- **Uczciwość:** brak danych = „nieznane”, nigdy zgadywanie. Backtester odrzucił strategię Minerviniego (−63% po drodze). Skaner par nie znalazł na GPW ani jednej skointegrowanej pary i tak to raportuje.
- **Bez błędu przeżycia:** spółki wycofane z giełdy zostają w danych, ceny są korygowane o dywidendy i splity, a zmiany nazw są sklejane (CCC → MODIVO).
- **Przepisy z ustaw** z numerami artykułów: OKI (Dz.U. 2026 poz. 1098), raporty okresowe (Dz.U. 2025 poz. 755), MAR art. 19.
- **Każdy raport ma datę danych i źródła**, a nieaktualne dane są oznaczone na samej górze.

## Źródła danych (darmowe, oficjalne)

Archiwum notowań GPW (akcje, indeksy, ETF-y, kontrakty) · ESPI na gpw.pl ·
GPW Benchmark · rejestr krótkich pozycji KNF · API NBP · BondSpot · EBC ·
Bundesbank · Eurostat · Damodaran (premia za ryzyko kraju).
Pobieranie jest grzeczne: klient się przedstawia, obowiązuje limit zapytań
i cache, a zabezpieczenia stron nie są obchodzone. Szczegóły:
[DATA_SOURCES](docs/DATA_SOURCES.md).

## Ograniczenia (mówimy je wprost)

| Obszar | Ograniczenie |
|---|---|
| Insiderzy | Wolumeny i ceny są w PDF-ach blokowanych dla skryptów, więc ich treść trzeba wkleić samemu |
| Fundamenty | gpw.pl pokazuje tylko ostatni okres; porównania rok do roku dochodzą z czasem albo z raportu ESEF |
| Krótkie pozycje | Widać tylko pozycje ≥ 0,5% kapitału |
| Wycena | Dług netto jest szacowany; można podać własny |
| Indeksy | Skład według dzisiejszych portfeli, nie historycznych |

## Rozwój

```bash
python3 -m pip install pytest
python3 scripts/sync_shared.py      # kopiuje shared/gpwlib do każdego skilla
python3 -m pytest tests             # testy, bez sieci
python3 scripts/package_skills.py   # walidacja i paczki .skill
```

Zasady: [CONVENTIONS](docs/CONVENTIONS.md) · kontrakty JSON i decyzje:
[ROADMAP v1.2](docs/ROADMAP_v1.2.md) · zmiany: [CHANGELOG](CHANGELOG.md).

## Licencja i zastrzeżenia

Kod jest na licencji MIT, na bazie
[tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills).
Repozytorium nie zawiera danych rynkowych: pobierasz je u siebie do osobistej
analizy, a ich rozpowszechnianie może wymagać licencji GPW.

Projekt jest edukacyjny i badawczy. Nie jest doradztwem inwestycyjnym,
podatkowym ani prawnym, ani rekomendacją w rozumieniu MAR (UE 596/2014).
Obliczenia podatkowe są szacunkowe: wiążące są PIT-8C i opinia doradcy
podatkowego. Inwestowanie wiąże się z ryzykiem utraty kapitału.
