# gpw-event-calendar - methodology and sources

All facts below were checked on 2026-11-16. Quotes are from the official texts.

## 1. Why an event calendar

Scheduled information events concentrate price moves: earnings announcements
carry a large share of annual return variance and produce post-announcement
drift (Ball & Brown 1968; Bernard & Thomas 1989); on the WSE the drift is
mainly visible after negative surprises (Jagliński 2020, see gpw-earnings-
reaction). Ex-dividend days cut the reference price by the dividend, index
rebalancing days move closing-auction volume (Chen, Noronha & Singal 2004 on
index additions), and central-bank decision days move rate-sensitive sectors
(banks are ~1/3 of WIG20). Equivalents: tradermonty `earnings-calendar` and
`economic-calendar-fetcher` (paid FMP API); this skill rebuilds them from free
Polish sources.

## 2. Periodic reports - legal deadlines

Rozporządzenie Ministra Finansów z dnia 6 czerwca 2025 r. w sprawie informacji
bieżących i okresowych przekazywanych przez emitentów papierów wartościowych
(Dz.U. 2025 poz. 755, in force 25.06.2025; amended by Dz.U. 2026 poz. 1205,
which only adds gender-balance disclosures in § 72-73). It replaced the
regulation of 29.03.2018 (Dz.U. 2018 poz. 757), which ELI marks as repealed.
Source text: https://api.sejm.gov.pl/eli/acts/DU/2025/755/text.pdf

- § 83 ust. 1: quarterly report "nie później jednak niż w terminie 60 dni od
  dnia zakończenia kwartału roku obrotowego".
- § 83 ust. 2: "Emitent nie ma obowiązku przekazywania raportu kwartalnego ...
  za drugi i ostatni kwartał roku obrotowego, z wyjątkiem emitenta będącego
  funduszem." -> no deadline is generated for Q2 and Q4. (The old 2018 rule
  "Q4 may be skipped if the annual report comes within 80 days" no longer
  exists.)
- § 83 ust. 5: half-year report "nie później jednak niż w terminie trzech
  miesięcy od dnia zakończenia półrocza".
- § 83 ust. 7: annual report "nie później niż cztery miesiące od dnia
  zakończenia roku obrotowego".
- § 83 ust. 11: a deadline falling on "dzień ustawowo wolny od pracy, sobotę"
  moves to "pierwszego dnia roboczego następującego po tym dniu".
- § 84 ust. 1: fixed dates for the year are published "do końca pierwszego
  miesiąca danego roku obrotowego" in a current report; § 84 ust. 2: a change
  is published at least two days before.

Implementation: calendar financial year assumed (most issuers; LPP and a few
others use other years and their schedules are read as published). Month
arithmetic follows KC art. 112 (31.12 + 4 months = 30.04). Working days =
Mon-Fri minus Polish public holidays (24.12 is a public holiday from 2025;
Good Friday and 31.12 are exchange-only holidays, so they are working days
for this rule). Examples: Q3 2026 nominal 29.11.2026 (Sunday) -> 30.11.2026;
Q1 2026 30.05 (Saturday) -> 01.06; FY 2027 30.04.2028 (Sunday) -> 1.05
holiday -> 02.05.2028. NewConnect (EBI) has different deadlines in the ASO
rules and is not modelled.

Confirmed dates: ESPI current reports classified `periodic_schedule`
(`gpwlib.espi`). The scan covers December of the previous year and January
(many issuers publish their schedule in December) plus the lookback (default
120 days) for date changes. A date counts when the period named just before
it (lists "raport roczny za 2025 r. - 5 marca 2026 r.") or, in date-first
layouts, just after it; dates before the report's publication day (references
"z dnia ...", the regulation's own date) are ignored; in change notices the
date after "na dzień / nowy termin" wins over "pierwotny / zamiast". Tested on
the real 2026 schedules of PGE, PZU, LPP, MODIVO, TAURON, CD PROJEKT, DINO,
BUDIMEX and KĘTY (all four dates correct for each).

Published reports: ESPI types Q/P/R (quarterly reports often have an empty
title); the period is inferred from the publication month.

## 3. General meetings and dividends

- KSH (tekst jednolity Dz.U. 2024 poz. 18) art. 395 § 1: "Zwyczajne walne
  zgromadzenie powinno się odbyć w terminie sześciu miesięcy po upływie
  każdego roku obrotowego" -> 30 June for a calendar year (statutory_deadline,
  importance low; no working-day shift).
- Art. 348 § 3-4: the ordinary general meeting sets the record date ("dzień
  dywidendy") "nie wcześniej niż pięć dni i nie później niż trzy miesiące od
  dnia powzięcia uchwały o podziale zysku".
- AGM date parsing: lead phrases ("na dzień", "zwołuje na", "odbędzie się w
  dniu", "held on"), title first, else first date on/after publication (a
  listed company calls a meeting at least 26 days ahead, KSH art. 402^1 § 1).
- Dividend parsing: DPS = an amount in zł/PLN/gr within 60 characters of "na
  (jedną) akcję"/"per share" (amounts > 10 000 rejected as totals); record
  date = first date within 100 characters after "dzień dywidendy / dzień
  ustalenia prawa / record date"; payment dates = up to three dates within 160
  characters after "termin/dzień wypłaty / payment date". Stage: interim
  (zaliczka), proposal (rekomendacja/wniosek zarządu), resolution (uchwała
  WZ); proposals are marked `estimated` because the AGM can change them.

### Ex-dividend (T+2)

KDPW moved GPW cash settlement from T+3 to T+2 on 6.10.2014
(https://www.30.kdpw.pl/uploads/user_files/zmiana_cyklu_rozrachunkowego_z_t_plus_3_na_t_plus_2.pdf).
To be on the register at the end of record date D the buyer must trade on D-2
settlement days at the latest; KDPW's own example: record date 23.05.2014
(Friday) -> purchase by 21.05.2014 (Wednesday). Hence:

- last session with the right = D - 2 sessions,
- ex-date (first session without the right, GPW cuts the reference price) =
  D - 1 session.

Verified on the GPW archive 2026: biznesradar's "ostatnie notowanie z prawem"
+ 1 session equals the price-adjustment day found by `gpwlib.panel` for
PKOBP (03.08 -> 04.08), PZU (15.09 -> 16.09), KETY (17.08 -> 18.08), PEKAO
(11.06 -> 12.06) and PKNORLEN (16.06 -> 17.06). KDPW settlement days match GPW
sessions except rare cases, so sessions are used (`gpwlib.plcal`).

### Archive cross-check and next-year estimate

`gpwlib.panel.build_panel` detects corporate actions as reference-price
mismatches > 0.5 %. Factors 0.75-0.995 are labelled `dividend_likely`
(ordinary GPW dividends are 1-10 % of the price; 2025-26 WIG20 payers 3.8-
9.8 %), others `corporate_action` (splits, rights issues). Implied DPS =
previous raw close x (1 - factor) (PKOBP 2026: 6.14 zł, equal to the
resolution). Dividends below 0.5 % of the price (e.g. CD PROJEKT) are not
visible. Next ex-date estimate = last dividend ex-date + 52 weeks, rolled to a
session, window +-10 sessions; confidence medium with two seasons of history,
low with one.

## 4. Market events

| Event | Source | date_kind |
|---|---|---|
| GPW Benchmark index calendar (rankings, announcements, quarterly/annual revisions, MWO) | https://gpwbenchmark.pl/kalendarium-indeksow?year=YYYY (HTML, parsed from the per-day help boxes) | confirmed |
| MSCI review Nov 2026 (announce 11.11, changes as of the close of 30.11.2026) | MSCI press release 4.11.2026 | confirmed |
| MSCI Feb/May/Aug 2027 | MSCI schedule as reported by the press (Aug 2026) | estimated |
| other MSCI reviews | rule: last business day of Feb/May/Aug/Nov, announcement ~13 business days earlier | estimated |
| NBP MPC 2026 (11 two-day decision meetings + 25.08 non-decision) | NBP schedule, embedded (nbp.pl blocks scripted access); relay infor.pl, cross-checked rankomat.pl | confirmed |
| MPC 2027 | not yet published -> warning, no dates invented | — |
| GUS flash CPI | last working day of the month (verified 30.09 and 30.10.2026 in the GUS calendar) | estimated |
| GUS final CPI | ~15th, moved back to a working day (15.09, 14.10, 13.11.2026 observed) | estimated |
| FW20/FW40 expiry | GPW standard (WATS 06.2026): third Friday of Mar/Jun/Sep/Dec, preceding session if none | confirmed |
| OW20 monthly options | third Friday of the month (standard not re-checked) | estimated |

Index importance: WIG20/mWIG40/sWIG80 announcements and revisions are high
(index funds and FW20 track them; changes take effect after the session of
the third Friday), their ranking (data cut-off) medium, WIG-family revisions
medium, WIGdiv / NewConnect / CEE / MWO low. Bond-index items are dropped.

Company-event importance in the market view: WIG20 keeps the base level,
mWIG40 one level lower, others low (market impact scales with index weight
and liquidity). Tickers the user asked about keep the base level.

## 5. Holding-window verdict

`event_risk`: a periodic report (confirmed or statutory deadline) or a
confirmed ex-dividend date of the ticker falls inside the window - the price
can gap through a stop. `caution`: only estimated company events or
high-importance market events (MPC decision, MSCI effective day, WIG20
revision). `clear`: nothing relevant. Index rankings do not count (data
cut-off only).

## 6. Optional third-party source

`--source biznesradar` reads https://www.biznesradar.pl/dywidendy/ (robots.txt
allows `/dywidendy`; terms silent on scraping). Treat it as personal, local use
only and never redistribute (see docs/DATA_SOURCES.md). Default off; ESPI and
the GPW archive are the primary sources. strefainwestorow.pl is not used (its
terms ban redistribution), stooq is excluded (robots), espiebi.pap.pl is behind
Incapsula, and `POST https://www.gpw.pl/ajax-kalendarium` covers only the
current day's instrument changes and is not used.

## 7. Limits

- ESPI titles are free text; bodies are read only for the tickers of interest
  (WIG20 in upcoming mode, all with `--deep`, capped by `--max-reports`).
- PDF attachments are never downloaded (GPW WAF), so dates that only appear
  in a PDF are missed.
- Statutory deadlines assume a calendar financial year and the main market.
- Embedded schedules (MPC, MSCI) must be refreshed every December.
