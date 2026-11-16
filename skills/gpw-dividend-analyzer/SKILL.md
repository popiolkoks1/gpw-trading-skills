---
name: gpw-dividend-analyzer
description: Dividend analysis for Polish investors on the Warsaw Stock Exchange (GPW). Rebuilds each company's dividend history from GPW archive price adjustments (ex-dates and DPS), cross-checked with ESPI dividend resolutions, then reports years paid, cuts and suspensions, DPS growth, trailing and forward yield, net yield after the 19% Belka tax vs IKE/IKZE, payout ratio, FCF cover, a 0-100 stability score and trap flags (yield far above the sector, payout over 100%, falling profit, one-off dividend, state control, KNF limits for banks). Also shows why dividend capture fails after tax and fees. Modes - screen WIG20/mWIG40/sWIG80, one ticker, portfolio income for 12 months. No API key. PL triggers - "stopa dywidendy netto", "czy PZU płaci stabilną dywidendę", "spółki dywidendowe", "dywidenda w IKE", "ile dostanę dywidendy", "pułapka dywidendowa". EN triggers - "Polish dividend stocks", "net dividend yield Poland", "dividend trap GPW". Not for ex-date calendars only (gpw-event-calendar) or valuation.
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
  derived_from: "tradermonty/claude-trading-skills kanchi-dividend-sop and value-dividend-screener (MIT), rebuilt on GPW archive + ESPI with Polish tax rules"
---

# GPW Dividend Analyzer / Analiza dywidend GPW

Answers: does this company really pay, how reliably, what do I keep after tax,
and is the high yield a trap?

**Workflow position:** fed by the cached GPW quotes archive (gpwlib.panel
adjustments), ESPI dividend reports (gpwlib.espi), the gpw.pl company card
(gpwlib.fundamentals) and gpw-trade-journal open positions; feeds
gpw-research-dossier (`companies[]`), gpw-watchlist / gpw-daily-brief
(`upcoming[]`) and gpw-signal-postmortem (`signals[]`, kind `dividend_cut_risk`).

## Language

Answer in the user's language; pass `--lang pl` (default) or `--lang en`.

## Workflow

### 1. Run the script (never quote dividends, dates or tax from memory)

| Question | Command (run from the skill dir) |
|---|---|
| "Które spółki z WIG20/mWIG40 płacą stabilne dywidendy?" | `python3 scripts/gpw_dividend_analyzer.py screen --universe WIG20,MWIG40` |
| "Najwyższa stopa netto bez pułapek" | `python3 scripts/gpw_dividend_analyzer.py screen --sort yield --exclude-traps --min-years-paid 4` |
| "Czy PZU to dobra spółka dywidendowa? Ile dostanę z 200 akcji?" | `python3 scripts/gpw_dividend_analyzer.py ticker PZU --shares 200` |
| "Ile dywidend dostanę w ciągu roku?" | `python3 scripts/gpw_dividend_analyzer.py portfolio` (journal) or `portfolio --tickers PZU,PKOBP --shares 200,100` |

| Flag | Default | Notes |
|---|---|---|
| `--universe` | WIG20,MWIG40 | screen: any of WIG20, MWIG40, SWIG80; `--tickers A,B` overrides |
| `--min-yield`, `--min-years-paid`, `--min-score`, `--exclude-traps`, `--sort stability\|yield`, `--top` | — | screen filters |
| `--shares N` | 100 | ticker: net amount and IKE-vs-regular simulation; portfolio: one per `--tickers` |
| `--years`, `--growth-pct` | 10, 0 | IKE vs regular horizon and assumed yearly price/DPS growth |
| `--broker`, `--commission-pct`, `--commission-min` | mbank | fees for reinvestment and the capture example (gpwlib.brokers) |
| `--capture-amount` | 10000 | PLN for the dividend-capture example |
| `--espi-days` / `--no-espi` | 365 | ESPI look-back (first run downloads missing day lists, ~1 s each) |
| `--no-fundamentals` | off | skip the gpw.pl card (payout ratio, FCF, profit) |
| `--source biznesradar`, `--br-code T=CODE` | off | longer history, **personal local use only** |
| `--isin T=PL...` | — | tickers missing from the GPW listing tables |
| `--as-of`, `--offline`, `--cache-dir`, `--output-dir`, `--no-files` | | standard |

Price history comes only from the local archive cache (the skill tops up at
most the 5 newest sessions); it reports how many full years it saw. Exit 1 =
data/input error (message on stderr): report it, do not invent numbers.

### 2. Check freshness and warnings first

If `data_provenance.freshness.is_stale` is true, `history.short` is true, or
`warnings[]` lists ESPI gaps / missing financials, say so before the answer.

### 3. Present - answer first

1. Lead with the bold one-sentence answer from the Markdown.
2. Screen: the ranking table (stability score, years paid / observed, cuts,
   gross and net yield, payout, flags). Explain that ranking is by stability
   first, yield second (or by net yield with `--sort yield`).
3. Ticker: history table, upcoming dividend with its status (**confirmed**
   resolution, **proposed**, or **estimated** from last year - never present an
   estimate as confirmed), net cash for N shares, IKE vs regular table, ex-date
   drop, the dividend-capture example, ESPI cross-check, trap flags.
4. Portfolio: income by month, gross and net, and how many payments are only
   estimates.
5. For beginners explain in one line each: *stopa dywidendy* (DPS / price),
   *dzień odcięcia* (ex-date: buying on or after it gives no dividend; the
   price drops by about the dividend), *podatek Belki* (19% withheld by the
   broker, rounded to whole złoty), *IKE/IKZE* (no tax inside; IKZE 10% at
   withdrawal), *wskaźnik wypłaty* (share of profit paid out).

### 4. Frame it correctly

- A stability score and a yield are context, not a reason to buy. Never say
  "buy" or "sell".
- Dividend capture: explain with the numbers in `capture_example` that after
  19% tax and two commissions it loses money when the price drops by about
  the dividend; the loss on the shares does not reduce the dividend tax.
- Trap flags are questions to check, not verdicts. State-controlled companies
  and banks (KNF limits, cite the year from the report) can change payouts for
  reasons outside the company's results.
- Dividends below 0.5% of the price are invisible in the archive; without ESPI
  they may be missing - say so when `espi_scan` shows gaps.

## Key rules (details in references/methodology.md)

- DPS = last cum-dividend close - reference price on the ex-date (GPW cuts the
  reference price by the dividend); precision about +-(0.005% + half a tick).
- T+2 (KDPW): last session with the right = record date - 2 sessions; ex-date
  = record date - 1 session. KSH art. 348 § 4: record date 5 days - 3 months
  after the resolution.
- Belka 19% on dividends (art. 30a ust. 1 pkt 4 PIT), withheld by the payer,
  rounded to whole złoty (art. 63 § 1 Ordynacji podatkowej). IKE gross; IKZE
  10% on withdrawal (gpwlib.tax).
- Cut = DPS down > 10% y/y; suspension = no dividend after a paying year.
- Stability score: consistency 35, streak 15, no cuts 20, growth 15, payout
  room 15.
- `dividend_cut_risk` signal (avoid) needs two independent warnings.

## Output

`gpw_dividend_analyzer_YYYY-MM-DD_HHMMSS.json` / `.md`. JSON: `skill`,
`version`, `lang`, `mode`, `as_of`, `history{}`, `companies[]` (`ticker`,
`dps_history[]`, `years_paid`, `years_observed`, `cuts[]`, `yield_gross_pct`,
`yield_net_regular_pct`, `yield_ike_pct`, `forward_yield_gross_pct`,
`payout_ratio|null`, `payout_basis`, `fcf_cover`, `stability_score`,
`trap_flags[]`, `trap_details[]`), `upcoming[]` (`ticker`, `ex_date`, `dps`,
`date_kind`), `signals[]`, `screen{}` / `ticker_detail{}` / `portfolio{}`,
`espi_scan`, `warnings[]`, `data_provenance`.

## Related skills

- `gpw-event-calendar` - exact ex-dividend and AGM dates in a holding window.
- `gpw-account-planner` - IKE vs IKZE vs OKI with contributions and refunds.
- `gpw-quality-screener` / `gpw-valuation` - is the business behind the dividend sound and priced fairly.
- `gpw-portfolio-check` - concentration of a dividend portfolio (e.g. too many banks).
- `gpw-trade-journal` - positions read by portfolio mode.
- `gpw-research-dossier` - collects this report per company.
