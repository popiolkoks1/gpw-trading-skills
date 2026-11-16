---
name: gpw-event-calendar
description: One calendar of everything that can move a position on the Warsaw Stock Exchange (GPW) - periodic report dates from ESPI schedules or the statutory deadline, general meetings, dividend record, ex-dividend and payment dates (T+2 rule, cross-checked with GPW archive price cuts), GPW Benchmark index revisions, MSCI reviews, NBP MPC (RPP) meetings, GUS CPI and FW20 expiries. Every date is marked confirmed, estimated or statutory deadline. Modes - upcoming market sessions, one ticker, a portfolio or watchlist, and a holding-window check for the pre-trade gate; optional .ics export. No API key. Use when asked what is coming up for a Polish stock or the market. PL triggers - "kiedy wyniki PKO", "kalendarz wydarzeń", "kiedy dywidenda / dzień odcięcia", "kiedy walne", "co się dzieje w tym tygodniu na GPW", "czy w okresie trzymania są wyniki". EN triggers - "GPW earnings calendar", "ex-dividend date Poland", "events in my holding window". Not for dividend valuation, earnings reactions or US calendars.
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
  derived_from: "tradermonty/claude-trading-skills earnings-calendar and economic-calendar-fetcher (MIT), rebuilt on free Polish sources"
---

# GPW Event Calendar / Kalendarz zdarzeń GPW

Collects dated events from official Polish sources into one list with an
explicit **date kind**:

| date_kind | meaning |
|---|---|
| `confirmed` | announced by the company (ESPI), GPW Benchmark, NBP, MSCI or fixed by an exchange rule |
| `statutory_deadline` | the latest day the law allows (the company may publish earlier) |
| `estimated` | a rule or history (e.g. ex-date ~52 weeks after last year's, GUS release rule) |

**Workflow position:** fed by ESPI (gpw.pl), GPW Benchmark, the GPW archive
cache, gpw-trade-journal open positions and the gpw-watchlist state; feeds
gpw-pre-trade-gate (`holding_window_check`), gpw-earnings-reaction (report
dates in `events[]`), gpw-daily-brief and gpw-research-dossier.

## Language

Answer in the user's language; pass `--lang pl` (default) or `--lang en`.

## Workflow

### 1. Pick the mode and run the script (never give dates from memory)

| Question | Command (run from the skill dir) |
|---|---|
| "Co się dzieje na GPW w najbliższych tygodniach?" | `python3 scripts/gpw_event_calendar.py upcoming --sessions 15` |
| "Kiedy PKO publikuje wyniki / ma dywidendę?" | `python3 scripts/gpw_event_calendar.py ticker PKOBP` |
| "Co czeka moje spółki?" | `python3 scripts/gpw_event_calendar.py portfolio [--tickers PKOBP,KETY]` |
| "Chcę kupić PZU na ~4 tygodnie - czy są wyniki po drodze?" | `python3 scripts/gpw_event_calendar.py window --ticker PZU --days 20` |

Inputs and flags:

| Flag | Default | Notes |
|---|---|---|
| `--sessions N` | 15 upcoming, 20 portfolio/ticker | horizon in sessions (ticker mode shows company events up to a year ahead) |
| `--days N`, `--start D` | 20, today | window mode: holding period in sessions |
| `--tickers A,B` | — | portfolio mode also reads gpw-trade-journal open positions (`--no-journal` to skip) and `~/.gpw-trading-skills/watchlist.json` (`--watchlist PATH`), read-only |
| `--isin T=PL...` | — | tickers outside the GPW Benchmark portfolios (needed to match ESPI reports) |
| `--lookback-days` | 120 | ESPI scan; December-January (schedule season) is always scanned too |
| `--deep` | off | read report bodies for all companies (upcoming mode otherwise reads WIG20 only) |
| `--max-reports` | 150 | cap of report pages read per run |
| `--min-importance` | medium (upcoming) / low | `low`, `medium`, `high` |
| `--source biznesradar` | off | optional third-party dividend table, **personal local use only** |
| `--ics` | off | also write an iCalendar file for Google/Apple/Outlook |
| `--as-of`, `--offline`, `--cache-dir`, `--output-dir`, `--no-files` | | standard |

The first run downloads about 120 ESPI day lists (~1-3 min, polite rate
limit); later runs use the cache. Exit 1 = input/data error (message on
stderr): report it, do not invent dates.

### 2. Check freshness and warnings first

If `data_provenance.freshness.is_stale` is true or `warnings[]` is not empty
(offline cache gaps, missing ISIN, no MPC schedule for next year), say so
before anything else.

### 3. Present - answer first

1. Lead with the one-sentence answer printed in bold at the top of the
   Markdown, e.g. "W ciągu 15 sesji: PZU i CD Projekt publikują wyniki
   (potwierdzone), RPP 2 grudnia, ogłoszenie zmian WIG20 3 grudnia".
2. Then the table by date. Always say whether a date is **potwierdzona**,
   **termin ustawowy** (latest possible day, not the actual day) or
   **szacunek**.
3. Window mode: give the verdict (`event_risk` / `caution` / `clear`) and
   which events fall inside, with the session number.
4. For beginners, explain in one line each: *dzień odcięcia dywidendy*
   (ex-date: buying on or after it gives no dividend; the price drops by
   about the dividend), *dzień dywidendy* (record date), *rewizja indeksu*
   (index funds rebalance at the close of that session), *wygasanie FW20*
   (third Friday of Mar/Jun/Sep/Dec; volatile close).

### 4. Frame it correctly

- An event is a risk to plan for (position size, stop, timing), not a
  reason to buy or sell. Never say "buy" or "sell".
- A statutory deadline only says "not later than"; the company usually
  publishes earlier and announces the exact date in January.
- Estimated ex-dates come from last year's price cut; they become
  confirmed only when the company publishes the AGM resolution.

## Key rules (details in references/methodology.md)

- Periodic reports (Rozporządzenie MF z 6.06.2025, Dz.U. 2025 poz. 755, § 83):
  quarterly within 60 days, half-year within 3 months, annual within 4
  months; no obligation for Q2 and Q4 reports; a deadline on a holiday or
  Saturday moves to the next working day. Dates for the year are announced
  by the end of January (§ 84 ust. 1).
- AGM within 6 months of the financial year end (KSH art. 395 § 1); record
  date 5 days - 3 months after the resolution (KSH art. 348 § 4).
- Ex-dividend (KDPW, T+2): last session with the right = record date - 2
  sessions; ex-date = record date - 1 session. Verified on the 2026 archive
  for PKOBP, PZU, KETY, PEKAO and ORLEN.
- Index calendar from gpwbenchmark.pl; MSCI Nov 2026 confirmed from msci.com,
  later reviews estimated; MPC 2026 schedule embedded (nbp.pl blocks
  scripts) - the report warns when a year has no schedule.
- PDF attachments are never downloaded; only report pages and lists.

## Output

`gpw_event_calendar_YYYY-MM-DD_HHMMSS.json` / `.md` (+ `.ics`). JSON:
`skill`, `version`, `lang`, `mode`, `as_of`, `horizon`, `events[]` (`ticker`,
`isin`, `type`, `subtype`, `date`, `time`, `date_kind`, `importance`,
`title`, `source`, `source_url`, `details{}`), `past_events[]`,
`by_ticker{}` (next earnings / ex-date / AGM, archive ex-date history),
`holding_window_check{}` (window mode: `contains.has_earnings`,
`has_ex_dividend`, `has_agm`, `has_mpc`, `has_expiry`, `has_index_revision`,
`has_macro`, `verdict`), `espi_scan`, `warnings[]`, `data_provenance`.

## Related skills

- `gpw-pre-trade-gate` - reads `holding_window_check` before a trade.
- `gpw-earnings-reaction` - uses report dates to measure price reactions.
- `gpw-dividend-analyzer` - dividend history, yield and traps.
- `gpw-trade-journal` / `gpw-watchlist` - the tickers of portfolio mode.
- `gpw-position-sizer` - size down when an event falls inside the window.
- `gpw-macro-regime` - what MPC and CPI mean for the market.
