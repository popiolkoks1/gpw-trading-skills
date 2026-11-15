# GPW Trading Skills

**Zestaw skilli dla Claude'a do polskiej giełdy (GPW): kondycja rynku, trendy
sektorowe, wielkość pozycji w złotówkach, dziennik inwestycyjny i podsumowanie
transakcji z pomocnikiem do PIT-38.**

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

Ten zestaw przebudowuje pięć sprawdzonych skilli z
[tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills)
pod polskie realia. Korzysta **wyłącznie z darmowych, oficjalnych danych**
(archiwum notowań GPW, GPW Benchmark, NBP), **bez kluczy API**.

## Skille

| Skill | Co robi | Dane |
|---|---|---|
| [`gpw-market-breadth`](skills/gpw-market-breadth/SKILL.md) | Ocena kondycji całej GPW w skali 0–100. Liczy % spółek powyżej średnich 20/50/200 sesji, rosnące i spadające, oscylator McClellana, nowe maksima i minima, udział WIG20 vs mWIG40 vs sWIG80 oraz dywergencję WIG vs szerokość rynku | archiwum GPW, GPW Benchmark |
| [`gpw-sector-trend`](skills/gpw-sector-trend/SKILL.md) | Ile spółek jest w potwierdzonym trendzie wzrostowym, łącznie i w 14 indeksach sektorowych. Rotacja spółek cyklicznych vs defensywnych, indeksy sektorowe na tle WIG | archiwum GPW, GPW Benchmark |
| [`gpw-position-sizer`](skills/gpw-position-sizer/SKILL.md) | Ile akcji kupić przy założonym ryzyku: metoda stałego procentu, ATR lub pół-Kelly. Wlicza prowizje z minimami brokera, limit względem dziennego obrotu i ryzyko luki cenowej. Pokazuje zysk po podatku na zwykłym rachunku, IKE, IKZE i OKI | lokalnie + opcjonalnie archiwum GPW |
| [`gpw-trade-journal`](skills/gpw-trade-journal/SKILL.md) | Dziennik tez inwestycyjnych: filary, warunki unieważnienia tezy, katalizatory, cykl życia POMYSŁ → AKTYWNA → ZAMKNIĘTA, transakcje rozliczane FIFO, wynik na tle WIG | lokalny JSON |
| [`gpw-trade-review`](skills/gpw-trade-review/SKILL.md) | Podsumowanie miesięczne i roczne: skuteczność, wartość oczekiwana, wielokrotność R, macierz „proces vs wynik”, wnioski. Do tego **szacunek PIT-38**: przychód, koszty, dochód i 19% podatku | lokalny JSON |

Każdy skill działa po polsku i po angielsku. Claude odpowiada w Twoim języku,
a skrypty generują raporty z flagą `--lang pl` lub `--lang en`.

## Szybki start

### Claude Code (terminal / aplikacja desktop)

```bash
git clone https://github.com/popiolkoks1/gpw-trading-skills.git
cp -R gpw-trading-skills/skills/* ~/.claude/skills/
```

Potem po prostu zapytaj: *„Jak wygląda rynek na GPW?”*, *„Które sektory są
mocne?”* albo *„Ile akcji PKO BP kupić przy ryzyku 1%?”*.

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
z sandboxa. Kalkulator, dziennik i podsumowanie działają bez sieci.

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
