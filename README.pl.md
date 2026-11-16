# GPW Trading Skills

**27 skilli dla Claude'a do polskiej giełdy (GPW), połączonych w jeden proces.
Dla początkujących: gotowość do inwestowania, IKE/IKZE/OKI, plan wpłat w ETF-y.
Dla zaawansowanych: reżim rynku, skaner Minerviniego/VCP, akcjonariat i krótkie
pozycje z rejestru KNF, kalendarz ESPI, reakcje na wyniki, jakość, wycena,
dywidendy, makro NBP, stress test, hedging FW20, uczciwy backtester,
post-mortem sygnałów, dossier spółki z debatą byk–niedźwiedź i dzienny brief.**

🇬🇧 [English version → README.md](README.md)

> Narzędzia edukacyjne, nie porada inwestycyjna. Skille nie składają zleceń
> i nie mówią, co kupić. Dają dane, policzone ryzyko i uporządkowany proces,
> żeby to **Ty** podejmował lepsze decyzje. Zobacz [Zastrzeżenia](#zastrzeżenia).

## Po co to jest

Większość narzędzi AI do inwestowania jest robiona pod rynek amerykański i płatne
źródła danych. U nas jest inaczej:
- poza WIG20 i mWIG40 płynność jest niska;
- prowizje mają minimum w złotówkach, a akcje kupuje się w całych sztukach;
- obowiązuje podatek Belki (19%), a PIT-38 rozlicza się metodą FIFO;
- są konta IKE i IKZE, a od 2027 roku dojdzie OKI;
- spółki publikują raporty przez ESPI, krótkie pozycje są w rejestrze KNF.

Zestaw powstał na wzór najlepszych otwartych repozytoriów skilli giełdowych
([tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills),
Anthropic financial-services, TradingAgents, FinRobot). Do każdego skilla
sprawdziliśmy odpowiedniki z innych giełd i badania naukowe (także o GPW),
a potem przenieśliśmy sprawdzone metody na polskie realia. Zestaw korzysta
**wyłącznie z darmowych, oficjalnych danych**, **bez kluczy API**:
archiwum notowań GPW, ESPI na gpw.pl, GPW Benchmark, rejestr krótkich pozycji
KNF, API NBP, BondSpot, EBC, Bundesbank, Eurostat i teksty ustaw.

## Od czego zacząć?

**Nie znasz się na inwestowaniu?**
1. **`gpw-start`** – czy jesteś gotowy, ile akcji pasuje do Twojej tolerancji spadków, plan krok po kroku.
2. **`gpw-account-planner`** – IKE, IKZE, OKI czy zwykłe konto, policzone na Twoim horyzoncie.
3. **`gpw-etf-planner`** – ETF-y z GPW i plan miesięcznych zakupów z prowizjami.
4. **`gpw-portfolio-check`** i **`gpw-dividend-analyzer`** – raz w roku przegląd portfela i dywidend.

Zapytaj po prostu: *„Nie znam się na inwestowaniu, od czego zacząć?”*

**Inwestujesz aktywnie?** Zacznij od **`gpw-daily-brief`**. Po sesji uruchamia
cały dzienny proces i daje jedną stronę: co się zmieniło, co wymaga uwagi
w Twoich pozycjach i na watchliście, nowe sygnały i kalendarz. Przed każdą
decyzją: **`gpw-research-dossier`**, potem kalkulator pozycji i bramka.
Pełny opis procesu: [docs/WORKFLOW.md](docs/WORKFLOW.md).

## Skille

### Dla początkujących

| Skill | Co robi |
|---|---|
| [`gpw-start`](skills/gpw-start/SKILL.md) | Gotowość do inwestowania (nadwyżka, poduszka 3–6 mies., długi > 8%), udział akcji z horyzontu i tolerancji spadku, plan, słowniczek, ostrzeżenia przed oszustwami |
| [`gpw-account-planner`](skills/gpw-account-planner/SKILL.md) | IKE / IKZE / OKI / zwykłe konto na wiele lat: limity 2026–2027, zwrot z IKZE, podatek od aktywów OKI, kolejność wypełniania kont |
| [`gpw-etf-planner`](skills/gpw-etf-planner/SKILL.md) | Katalog ETF-ów z GPW ze statystykami, plan zakupów w całych jednostkach, rebalancing nowymi wpłatami, test DCA |

### Rynek: kontekst

| Skill | Co robi |
|---|---|
| [`gpw-market-breadth`](skills/gpw-market-breadth/SKILL.md) | Kondycja całej GPW 0–100: % spółek nad średnimi, McClellan, nowe maksima/minima, dywergencje |
| [`gpw-sector-trend`](skills/gpw-sector-trend/SKILL.md) | Trend w 14 indeksach sektorowych, rotacja cykliczne/defensywne; **tryb `--rrg`**: Relative Rotation Graph (liderzy, słabnący, maruderzy, poprawiający się) z ogonami i SVG |
| [`gpw-market-regime`](skills/gpw-market-regime/SKILL.md) | Reżim rynku (IBD): dni dystrybucji, follow-through day, ryzyko szczytu 0–100 |
| [`gpw-macro-regime`](skills/gpw-macro-regime/SKILL.md) | Makro dla polskich akcji: cykl stóp NBP, realna stopa, siła złotego, spread PL–DE 10Y, krzywa 2s10s, inflacja; tendencje sektorowe |
| [`gpw-exposure-coach`](skills/gpw-exposure-coach/SKILL.md) | Jedna postawa i limity: ekspozycja, ryzyko na transakcję, nowe pozycje na tydzień, bezpiecznik po stratach |

### Spółki: pomysły i badanie

| Skill | Co robi |
|---|---|
| [`gpw-trend-screener`](skills/gpw-trend-screener/SKILL.md) | Trend Template Minerviniego, RS 1–99, VCP, pivot, poziom unieważnienia |
| [`gpw-quality-screener`](skills/gpw-quality-screener/SKILL.md) | F-score Piotroskiego z każdym kryterium osobno (nieznane zaznaczone uczciwie), ROE/ROIC, akruale, czerwone flagi, osobna ocena banków |
| [`gpw-valuation`](skills/gpw-valuation/SKILL.md) | Odwrócony DCF (jaki wzrost zakłada cena), WACC w PLN z premią za ryzyko kraju (Damodaran), siatka wrażliwości, porównania, model banków P/BV–ROE |
| [`gpw-dividend-analyzer`](skills/gpw-dividend-analyzer/SKILL.md) | Historia dywidend odtworzona z notowań i ESPI, stabilność, stopa netto po Belce vs IKE, pułapki dywidendowe, dlaczego „gra pod dywidendę” nie działa |
| [`gpw-ownership`](skills/gpw-ownership/SKILL.md) | Krótkie pozycje z rejestru KNF (od 2012), transakcje insiderów (MAR art. 19) z ESPI, zmiany w portfelach OFE |
| [`gpw-earnings-reaction`](skills/gpw-earnings-reaction/SKILL.md) | Reakcja kursu na raport okresowy: luka, nadzwyczajna stopa zwrotu, obroty, dryf po wynikach; przegląd sezonu |
| [`gpw-event-calendar`](skills/gpw-event-calendar/SKILL.md) | Wyniki, WZA, dywidendy (T+2), rewizje indeksów, MSCI, RPP, CPI, wygasanie FW20; kontrola okna trzymania; eksport .ics |
| [`gpw-pair-trading`](skills/gpw-pair-trading/SKILL.md) | Kointegracja Engle'a–Grangera (zgodna ze statsmodels), z-score spreadu, okres półtrwania, korekta wielokrotnych testów; decyzje „zamiana A na B” |
| [`gpw-research-dossier`](skills/gpw-research-dossier/SKILL.md) | Jeden plik o spółce z wyników wszystkich skilli (100+ faktów ze źródłami), potem debata byk–niedźwiedź, w której każdy argument musi cytować fakt; gotowa teza do dziennika |

### Decyzja i ryzyko

| Skill | Co robi |
|---|---|
| [`gpw-watchlist`](skills/gpw-watchlist/SKILL.md) | Watchlista z etapami (kandydat → obserwowana → gotowa → kupiona), zasilana przez inne skille; alerty: pivot, stop, wybicie, zdarzenia |
| [`gpw-position-sizer`](skills/gpw-position-sizer/SKILL.md) | Ile akcji kupić przy założonym ryzyku, z prowizjami, płynnością i podatkiem dla każdego typu konta |
| [`gpw-pre-trade-gate`](skills/gpw-pre-trade-gate/SKILL.md) | Bramka przed kupnem: 13 twardych kontroli + kontekst (wyniki w oknie trzymania, krótkie pozycje, sprzedaż insiderów, reakcja na wyniki, flagi jakości, makro, stress test) → GO / GO Z WARUNKAMI / NO-GO |
| [`gpw-portfolio-check`](skills/gpw-portfolio-check/SKILL.md) | Przegląd portfela: koncentracja, korelacje, beta, VaR/ES, płynność, porównanie z WIG |
| [`gpw-risk-lab`](skills/gpw-risk-lab/SKILL.md) | Twój portfel w kryzysach GPW (2008, 2011, 2015, 2020, 2022) i w szokach (WIG, złoty, stopy); zabezpieczenie kontraktami FW20 albo ETF-em short dla małych portfeli |

### Nauka i dyscyplina

| Skill | Co robi |
|---|---|
| [`gpw-trade-journal`](skills/gpw-trade-journal/SKILL.md) | Dziennik tez z warunkami unieważnienia, FIFO, wynik na tle WIG; tryb na niby (`--paper`) |
| [`gpw-trade-review`](skills/gpw-trade-review/SKILL.md) | Statystyki, macierz „proces vs wynik”, szacunek PIT-38 |
| [`gpw-signal-postmortem`](skills/gpw-signal-postmortem/SKILL.md) | Czy sygnały skilli naprawdę działały: zwroty po 5/20/60 sesjach vs WIG, przedziały ufności, wnioski tylko przy mocnych dowodach |
| [`gpw-backtester`](skills/gpw-backtester/SKILL.md) | Uczciwy backtest z prawdziwymi prowizjami, poślizgiem, płynnością i podatkiem Belki; walk-forward, Deflated Sharpe, PBO; werdykt od ODRZUĆ do SOLIDNA |
| [`gpw-daily-brief`](skills/gpw-daily-brief/SKILL.md) | Uruchamia cały dzienny/tygodniowy proces i składa jedną stronę po sesji |

## Jak skille współpracują

```
PO SESJI (gpw-daily-brief run)
  breadth ┐
  sector  ├→ exposure-coach ─────────────────────────────┐
  regime  ┘                                              │
  trend-screener, quality, ownership, earnings ─→ watchlist ─→ daily-brief
  event-calendar ────────────────────────────────────────┘

PRZED DECYZJĄ
  research-dossier (wszystkie raporty o spółce + debata)
    → position-sizer → pre-trade-gate → trade-journal

CO TYDZIEŃ / MIESIĄC / KWARTAŁ
  macro-regime, portfolio-check, risk-lab, dividend-analyzer
  trade-review, signal-postmortem → wnioski
  backtester → zanim zmienisz jakąkolwiek regułę
```

Skille rozmawiają przez pliki JSON o ustalonych kontraktach
([docs/ROADMAP_v1.2.md](docs/ROADMAP_v1.2.md)), więc każdy działa samodzielnie,
a razem tworzą proces. Sygnały wszystkich skilli mają wspólny format, więc
`gpw-signal-postmortem` sprawdza, które z nich naprawdę działają.

## Szybki start

### Claude Code (terminal / aplikacja desktop)

```bash
git clone https://github.com/popiolkoks1/gpw-trading-skills.git
cp -R gpw-trading-skills/skills/* ~/.claude/skills/
```

Potem zapytaj: *„Od czego zacząć inwestowanie?”*, *„Zrób dzienny brief GPW”*,
*„Kto gra na spadek MODIVO?”*, *„Co zakłada cena KĘTY?”*,
*„Przetestuj strategię Minerviniego na GPW od 2019”*.

Wymagania: Python 3.9+ (tylko biblioteka standardowa). Przy pierwszym
uruchomieniu skrypty pobierają około 2 lat notowań z gpw.pl. Wysyłają jedno
zapytanie na sesję, bez obciążania serwera, więc trwa to 20–40 minut, ale tylko
raz. Backtester i stress test mogą pobrać dłuższą historię (od 2019 r. dla akcji,
od 2007 r. dla indeksów). Wszystko trafia do lokalnego cache.

### Aplikacja webowa Claude (claude.ai)

```bash
python3 scripts/package_skills.py   # tworzy dist/*.skill + SHA256SUMS
```

Wgraj plik `.skill` w **Customize → Skills**, z włączonym wykonywaniem kodu.
Skille pobierające dane potrzebują dostępu do internetu z sandboxa.

## Zasady projektu

1. **Skrypty liczą, Claude tłumaczy.** Każda liczba pochodzi z przetestowanego kodu (ponad 1600 testów).
2. **Świeżość danych jest sprawdzana**, a każdy raport ma datę i źródła.
3. **Uczciwość ponad efekt.** Brakujące dane są oznaczone jako nieznane, nigdy nie są zgadywane. Backtester odrzuca strategie, które wyglądają dobrze tylko na papierze. Post-mortem daje wnioski tylko przy mocnych dowodach.
4. **Brak błędu przeżycia.** Uniwersum spółek jest odtwarzane z każdej sesji. Ceny są korygowane o dywidendy i splity, a zmiany nazw spółek (np. CCC → MODIVO) są sklejane.
5. **Polskie realia od początku:** PLN, całe akcje, minimalne prowizje, Belka, IKE/IKZE/OKI, ESPI, KNF, T+2.
6. **Przepisy z ustaw, nie z pamięci,** z numerami artykułów (np. rozporządzenie o raportach Dz.U. 2025 poz. 755, ustawa o OKI Dz.U. 2026 poz. 1098, MAR art. 19).
7. **Decyzja zawsze należy do człowieka.** Skille nie składają zleceń i nie mówią „kup”.
8. **Grzeczne pobieranie danych:** identyfikujący się klient, limit zapytań i cache; nie obchodzimy zabezpieczeń stron.

## Rozwój

```bash
python3 -m pip install pytest
python3 scripts/sync_shared.py      # kopiuje shared/gpwlib do skilli
python3 -m pytest tests             # testy, bez sieci
python3 scripts/package_skills.py   # walidacja i budowa paczek .skill
```

Zasady dla współtwórców: [docs/CONVENTIONS.md](docs/CONVENTIONS.md),
źródła danych: [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).

## Dane i licencje

Kod jest na licencji MIT, na bazie tradermonty/claude-trading-skills (MIT).
Repozytorium **nie zawiera** danych rynkowych. Pobierasz je na własnym
komputerze do osobistej analizy. Dane GPW i Notorii mogą wymagać licencji
przy dalszym rozpowszechnianiu.

## Zastrzeżenia

Projekt służy wyłącznie celom edukacyjnym i badawczym. Nie stanowi doradztwa
inwestycyjnego, podatkowego ani prawnego, ani rekomendacji inwestycyjnej
w rozumieniu rozporządzenia MAR (UE 596/2014). Obliczenia podatkowe są
szacunkowe: wiążące są PIT-8C od brokera i opinia doradcy podatkowego.
Inwestowanie wiąże się z ryzykiem utraty kapitału. Oprogramowanie jest
udostępniane „tak jak jest”, bez gwarancji.
