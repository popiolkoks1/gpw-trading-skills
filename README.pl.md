# GPW Trading Skills

**13 skilli dla Claude'a do polskiej giełdy (GPW): od pierwszych kroków dla osób,
które nie znają się na inwestowaniu (gotowość, IKE/IKZE/OKI, plan wpłat w ETF-y),
po narzędzia dla zaawansowanych (skaner trendu Minerviniego/VCP, reżim rynku,
limity ekspozycji, bramka przed transakcją, dziennik i PIT-38).**

🇬🇧 [English version → README.md](README.md)

> Narzędzia edukacyjne, nie porada inwestycyjna. Skille nie składają zleceń
> i nie mówią, co kupić. Dają dane, policzone ryzyko i uporządkowany proces,
> żeby to **Ty** podejmował lepsze decyzje. Zobacz [Zastrzeżenia](#zastrzeżenia).

## Po co to jest

Większość narzędzi AI do inwestowania jest robiona pod rynek amerykański i płatne
źródła danych. U nas jest inaczej:
- poza WIG20 i mWIG40 płynność jest niska;
- prowizje maklerskie mają minimum w złotówkach;
- akcje kupuje się w całych sztukach;
- obowiązuje podatek Belki (19%), a w PIT-38 rozlicza się metodą FIFO;
- są konta IKE i IKZE, a od 2027 roku dojdzie OKI.

Zestaw powstał na wzór najlepszych otwartych repozytoriów skilli giełdowych
([tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills),
Anthropic financial-services, himself65/finance-skills, TradingAgents, FinRobot)
i przenosi ich sprawdzone pomysły na polskie realia. Korzysta **wyłącznie z
darmowych, oficjalnych danych** (archiwum notowań GPW – akcje, indeksy, ETF-y –
GPW Benchmark, teksty ustaw), **bez kluczy API**.

## Od czego zacząć? (nie znasz się na inwestowaniu)

1. **`gpw-start`** – czy jesteś gotowy (budżet, poduszka, drogie długi), ile akcji pasuje do Twojej tolerancji spadków, plan krok po kroku, słowniczek.
2. **`gpw-account-planner`** – gdzie odkładać: IKE, IKZE, OKI czy zwykłe konto, policzone na Twoim horyzoncie.
3. **`gpw-etf-planner`** – katalog ETF-ów z GPW z prawdziwymi danymi i plan miesięcznych zakupów w całych jednostkach, z prowizjami.
4. **`gpw-portfolio-check`** – raz w roku: koncentracja, korelacje, ryzyko, w prostych słowach.
5. Chcesz się uczyć wyboru spółek? **`gpw-trade-journal --paper`** – transakcje na niby, bez prawdziwych pieniędzy.

Zapytaj po prostu: *„Nie znam się na inwestowaniu, od czego zacząć?”*

## Skille

### Dla początkujących

| Skill | Co robi | Dane |
|---|---|---|
| [`gpw-start`](skills/gpw-start/SKILL.md) | Sprawdzenie gotowości do inwestowania (nadwyżka, poduszka 3–6 mies., długi > 8%), orientacyjny udział akcji z horyzontu i tolerancji spadku, plan krok po kroku, słowniczek, ścieżka nauki, ostrzeżenia przed oszustwami | lokalnie |
| [`gpw-account-planner`](skills/gpw-account-planner/SKILL.md) | Porównanie IKE / IKZE / OKI / zwykłego konta na wiele lat: limity 2026–2027, zwrot z IKZE, 10% przy wypłacie, podatek od aktywów OKI (art. 25–26 ustawy), warunki wieku, kolejność wypełniania kont, wartości realne | lokalnie (przepisy w kodzie) |
| [`gpw-etf-planner`](skills/gpw-etf-planner/SKILL.md) | Katalog ETF-ów z GPW (kategoria, waluta, ostrzeżenia dla lewarowanych/short, wskazówka OKI) ze statystykami z notowań; plan miesięcznych zakupów w całych jednostkach z prowizjami i rebalancingiem nowymi wpłatami; test DCA vs jednorazowo | archiwum GPW (ETF) |

### Dla wszystkich

| Skill | Co robi | Dane |
|---|---|---|
| [`gpw-portfolio-check`](skills/gpw-portfolio-check/SKILL.md) | Przegląd portfela z CSV od brokera lub z dziennika: wagi, sektory, HHI, korelacje, zmienność, beta, VaR/ES, obsunięcie, płynność (dni do wyjścia), porównanie z WIG, flagi do przeglądu | archiwum GPW, GPW Benchmark |
| [`gpw-market-breadth`](skills/gpw-market-breadth/SKILL.md) | Ocena kondycji całej GPW w skali 0–100: % spółek powyżej średnich 20/50/200 sesji, rosnące i spadające, McClellan, nowe maksima i minima, WIG20 vs mWIG40 vs sWIG80, dywergencja WIG vs szerokość rynku | archiwum GPW, GPW Benchmark |
| [`gpw-sector-trend`](skills/gpw-sector-trend/SKILL.md) | Ile spółek jest w potwierdzonym trendzie wzrostowym, łącznie i w 14 indeksach sektorowych; rotacja cykliczne vs defensywne | archiwum GPW, GPW Benchmark |
| [`gpw-trade-journal`](skills/gpw-trade-journal/SKILL.md) | Dziennik tez inwestycyjnych: filary, warunki unieważnienia, cykl życia, FIFO, wynik na tle WIG; **tryb na niby (`--paper`)** | lokalny JSON |
| [`gpw-trade-review`](skills/gpw-trade-review/SKILL.md) | Podsumowanie miesięczne i roczne: skuteczność, wartość oczekiwana, R, macierz „proces vs wynik”, wnioski; **szacunek PIT-38** | lokalny JSON |

### Dla zaawansowanych

| Skill | Co robi | Dane |
|---|---|---|
| [`gpw-trend-screener`](skills/gpw-trend-screener/SKILL.md) | Skaner całej GPW: Trend Template Minerviniego (8 warunków), siła relatywna 1–99 w stylu IBD, wykrywanie formacji VCP, pivot, stany wykonawcze (przed wybiciem / wybicie / rozciągnięta / uszkodzona), poziom unieważnienia i gotowe polecenie do kalkulatora pozycji; tryb historyczny (wyniki sygnałów po 20/60 sesjach vs WIG) | archiwum GPW |
| [`gpw-market-regime`](skills/gpw-market-regime/SKILL.md) | Reżim rynku dla WIG, WIG20, mWIG40, sWIG80: dni dystrybucji (IBD), dzień potwierdzenia (follow-through day, O'Neil), trend, obsunięcie, dywergencja dużych i małych spółek, ryzyko szczytu 0–100 | archiwum GPW (indeksy) |
| [`gpw-exposure-coach`](skills/gpw-exposure-coach/SKILL.md) | Łączy szerokość rynku, sektory i reżim w jedną postawę (ofensywna / normalna / defensywna / ochrona kapitału) z limitem ekspozycji, ryzykiem na transakcję i bezpiecznikiem po serii strat (z dziennika) | raporty JSON innych skilli + dziennik |
| [`gpw-position-sizer`](skills/gpw-position-sizer/SKILL.md) | Ile akcji kupić przy założonym ryzyku: stały procent, ATR lub pół-Kelly; prowizje z minimami, limit względem obrotu, luka cenowa, zysk po podatku dla zwykłego rachunku, IKE, IKZE i OKI | lokalnie + opcjonalnie archiwum GPW |
| [`gpw-pre-trade-gate`](skills/gpw-pre-trade-gate/SKILL.md) | Bramka przed kupnem (fail-closed): stop, ryzyko z prowizjami, wielkość, płynność, łączne ryzyko portfela, koncentracja sektorowa, spisana teza z warunkami unieważnienia, R:R, bezpiecznik, reżim rynku → GO / GO Z WARUNKAMI / NO-GO | archiwum GPW + dziennik + JSON |

### Jak skille współpracują

```
gpw-start → gpw-account-planner → gpw-etf-planner → gpw-portfolio-check      (inwestor długoterminowy)

gpw-market-breadth ┐
gpw-sector-trend   ├→ gpw-exposure-coach ┐
gpw-market-regime  ┘                      ├→ gpw-pre-trade-gate → gpw-trade-journal → gpw-trade-review
gpw-trend-screener → gpw-position-sizer ──┘                                         (aktywny inwestor)
```

Każdy skill działa po polsku i po angielsku. Claude odpowiada w Twoim języku,
a skrypty generują raporty z flagą `--lang pl` lub `--lang en`.

## Szybki start

### Claude Code (terminal / aplikacja desktop)

```bash
git clone https://github.com/popiolkoks1/gpw-trading-skills.git
cp -R gpw-trading-skills/skills/* ~/.claude/skills/
```

Potem po prostu zapytaj: *„Od czego zacząć inwestowanie?”*, *„IKE czy IKZE?”*,
*„Jak wygląda rynek na GPW?”*, *„Które spółki spełniają trend template?”* albo
*„Ile akcji PKO BP kupić przy ryzyku 1%?”*.

Wymagania: Python 3.9+ (tylko biblioteka standardowa). Przy pierwszym
uruchomieniu analizy rynku lub sektorów skrypt pobiera około 2 lat notowań
z gpw.pl. Wysyła jedno zapytanie na sesję, bez obciążania serwera, więc trwa to
20–40 minut, ale tylko raz. Kolejne uruchomienia zajmują kilka sekund.

### Aplikacja webowa Claude (claude.ai)

```bash
python3 scripts/package_skills.py   # tworzy dist/*.skill + SHA256SUMS
```

Wgraj plik `.skill` w **Customize → Skills**. Wykonywanie kodu musi być
włączone. Skille, które pobierają dane z GPW, potrzebują dostępu do internetu
z sandboxa. `gpw-start`, `gpw-account-planner`, kalkulator pozycji, dziennik
i podsumowanie działają bez sieci.

## Zasady projektu

1. **Skrypty liczą, Claude tłumaczy.** Każda liczba pochodzi z przetestowanego
   kodu, Claude nie wymyśla kursów ani kwot podatku.
2. **Świeżość danych jest sprawdzana.** Każdy raport ma datę danych i
   ostrzeżenie, jeśli dane są nieaktualne.
3. **Każda liczba ma źródło i datę** (sekcja *Źródła*).
4. **Brak błędu przeżycia.** Uniwersum spółek jest odtwarzane z każdej sesji,
   więc spółki wycofane z giełdy liczą się, dopóki były notowane. Ceny są
   korygowane o dywidendy i splity na podstawie kursu odniesienia GPW.
5. **Polskie realia są wbudowane od początku**, a nie doklejone na końcu.
6. **Decyzja zawsze należy do człowieka.** Skille nie składają zleceń i nie
   mówią „kup”. Najpierw ryzyko i plan.
7. **Przepisy z ustaw, nie z pamięci.** Limity IKE/IKZE i zasady OKI są w
   jednym module (`gpwlib/tax.py`) z numerami artykułów; OKI sprawdzone w
   tekście Dz.U. 2026 poz. 1098. Tabela prowizji ma datę i ostrzega, gdy się
   zestarzeje.
8. **Skille rozmawiają przez pliki JSON** o ustalonym kontrakcie, więc każdy
   działa samodzielnie, a razem tworzą proces.

## Rozwój

```bash
python3 -m pip install pytest
python3 scripts/sync_shared.py      # kopiuje shared/gpwlib do skilli
python3 -m pytest tests             # testy jednostkowe, bez sieci
python3 scripts/package_skills.py   # walidacja i budowa paczek .skill
```

## Dane i licencje

Kod jest na licencji MIT, na bazie tradermonty/claude-trading-skills (MIT).
Repozytorium **nie zawiera** danych rynkowych. Pobierasz je na własnym
komputerze z GPW do osobistej analizy, a ich dalsze rozpowszechnianie może
wymagać licencji GPW.

## Zastrzeżenia

Projekt służy wyłącznie celom edukacyjnym i badawczym. Nie stanowi doradztwa
inwestycyjnego, podatkowego ani prawnego, ani rekomendacji inwestycyjnej
w rozumieniu rozporządzenia MAR (UE 596/2014). Obliczenia podatkowe są
szacunkowe: wiążące są PIT-8C od brokera i opinia doradcy podatkowego.
Inwestowanie wiąże się z ryzykiem utraty kapitału. Oprogramowanie jest
udostępniane „tak jak jest”, bez gwarancji.
