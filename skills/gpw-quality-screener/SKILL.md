---
name: gpw-quality-screener
description: Fundamental quality screen of Warsaw Stock Exchange (GPW) companies from free gpw.pl data - Piotroski F-score with every one of the 9 criteria shown as pass, fail or unknown, ROE, ROA, approximate ROIC, margins, free cash flow, Sloan accruals, interest coverage, valuation multiples (P/E, P/BV, FCF yield, dividend yield; EV multiples when an ESEF report gives net debt), a 0-100 quality score ranked within the screen and the sector, and red flags (negative equity, profit without cash, high accruals, dilution, losses). Banks and insurers get their own scoring (ROE, equity/assets, P/BV vs ROE). Missing data is never counted. Modes - screen (WIG20+mWIG40 by default, any index or all shares) and ticker (full card for beginners). PL triggers - "F-score Piotroskiego", "jakość spółki", "zdrowe fundamenty", "czerwone flagi w finansach", "skaner fundamentalny GPW", "ROE banków". EN triggers - "GPW quality screen", "Piotroski F-score Poland". Not for price targets, DCF valuation or US stocks.
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
  derived_from: "tradermonty/claude-trading-skills value-dividend-screener (MIT) - idea only; rebuilt for GPW on Piotroski (2000), Sloan (1996), Novy-Marx (2013) and Quality Minus Junk"
---

# GPW Quality Screener / Skaner jakości spółek GPW

Answers: **which GPW companies have healthy, cash-backed fundamentals, and
which show warning signs?** It computes the Piotroski F-score, profitability,
earnings quality, safety and valuation from the gpw.pl company card (Notoria
financial data + GPW indicators), ranks companies within the screen and their
sector, and lists red flags.

Free data on GPW is thin: the card shows **only the latest period**. The
skill therefore stores every period it sees in a local history; the F-score's
six year-over-year criteria become known after about a year of runs, or at
once when the user supplies an ESEF annual report. Until then F is shown as
`passed/known` (e.g. `3/3`) with a coverage %. Missing data is never treated
as 0 or 1.

**Workflow position.** Fed by: gpw.pl company cards (Indicators and Notoria
tabs), GPW Benchmark index portfolios, the local fundamentals history, and
optionally a user ESEF zip or filings.xbrl.org (FY2020-2023). Feeds:
`gpw-research-dossier` (`companies[]`), `gpw-pre-trade-gate` (quality red
flags), `gpw-watchlist` (candidates), `gpw-signal-postmortem` (`signals[]`),
`gpw-valuation` and `gpw-dividend-analyzer` (same company, other angle). Run
monthly, and after each reporting season (late May, September, November,
April).

## When to use

- "Które spółki z WIG20 mają najlepsze fundamenty?", "Policz F-score
  Piotroskiego dla Kęt", "Czy Newag ma czerwone flagi w finansach?", "Które
  banki mają najwyższe ROE?", "Skaner jakości mWIG40 z C/Z poniżej 15".
- "Screen GPW for quality stocks", "Piotroski F-score for KGHM", "red flags in
  a Polish company's accounts".

Not for: price targets or DCF (`gpw-valuation`), dividend safety
(`gpw-dividend-analyzer`), price trends (`gpw-trend-screener`), US stocks.

## Language

Answer in the user's language and pass `--lang pl` (default) or `--lang en`.

## Workflow

### 1. Run the script (never compute fundamentals from memory)

From the skill directory:

```bash
# default screen: WIG20 + mWIG40 (~60 companies, ~120 requests, ~1.5 min cold, seconds when cached)
python3 scripts/gpw_quality_screener.py screen --lang pl --output-dir <dir>

# other universes and filters
python3 scripts/gpw_quality_screener.py screen --index WIG20 --lang pl --output-dir <dir>
python3 scripts/gpw_quality_screener.py screen --index sWIG80 --max-pe 15 --min-roe 12 --output-dir <dir>
python3 scripts/gpw_quality_screener.py screen --tickers KETY,DINOPL,LPP --output-dir <dir>
python3 scripts/gpw_quality_screener.py screen --all --output-dir <dir>   # ~400 shares, ~15 min cold, cached 7 days

# one company, full card with every F-score criterion explained
python3 scripts/gpw_quality_screener.py ticker KETY --lang pl --output-dir <dir>
python3 scripts/gpw_quality_screener.py ticker KETY --esef ~/Downloads/kety-2025-12-31-pl.zip --output-dir <dir>
python3 scripts/gpw_quality_screener.py ticker KETY --xbrl --output-dir <dir>   # historical FY2020-2023
```

| Input | Flag | Notes |
|---|---|---|
| Universe | `--tickers A,B` / `--index WIG20,mWIG40,sWIG80,WIG` / `--all` | default WIG20 + mWIG40; tickers are archive names (`PKOBP`, `CDPROJEKT`) |
| Filters | `--min-f N`, `--max-pe X`, `--min-roe PCT` | `--min-f` keeps only companies whose F lower bound reaches N; undetermined ones are excluded and counted |
| Annual report | `--esef FILE` (ticker) | ESEF zip from the company's investor-relations page; gives net debt, EV multiples and a full-year F-score |
| Historical xBRL | `--xbrl [--lei LEI]` (ticker) | filings.xbrl.org, FY2020-2023 only, always labelled stale |
| ISIN | `--isin` (ticker) | only when the ticker is not in today's listing |
| Point in time | `--as-of YYYY-MM-DD` | rebuilds data from the local history only |
| Other | `--offline`, `--refresh`, `--cache-dir`, `--history-dir`, `--no-save-history`, `--no-files`, `--top N` | |

Exit code 1 = data problem (unknown ticker, no data offline, bad ESEF file):
report the message, do not invent numbers. Exit code 2 = bad arguments.

### 2. Check freshness and coverage first

- If `data_provenance.freshness.is_stale` is true, say so first.
- Read `coverage`: `mean_f_coverage_pct`, `f_complete`, `stale_periods`,
  `data_check_failures`. If F coverage is ~33%, say in one sentence that only
  the three level criteria are known yet and why (history builds over a year,
  or the user can pass `--esef`).

### 3. Present the screen

1. Lead with the answer: the top 5 non-financial companies by quality score
   with ROE, ROIC≈, F (`passed/known`) and P/E; then the companies with red
   flags.
2. Banks separately: ROE, equity/assets and the P/BV-vs-ROE residual (negative
   = priced below peers for its ROE). Insurers separately.
3. Mention the period of each company when they differ (H1 vs 9M) and every
   ⚠️ stale period.
4. List "Source data checks" companies as "check the report", never as facts.
5. The quality score is a rank within this screen; do not compare scores
   across different runs or universes.

### 4. Present a ticker card (beginners)

Explain each F-score criterion in plain Polish/English using the card's table
(question, result, numbers now vs a year earlier). Then ROE, ROIC≈, accruals,
FCF and the red flags, each in one sentence of what it means. Point out which
numbers are proxies (margin on sales instead of gross margin, interest
coverage from EBIT − pretax) and what is unknown.

## Key rules

- Never say "buy" or "sell". A high quality score is a reason to research
  further, a red flag a reason to read the report; neither is a decision. On
  the WSE factor premiums often shrink after trading costs.
- Never fill an unknown F criterion, net debt or EV multiple from memory or
  another website. Say it is unknown and how to get it (`--esef`).
- `f_score` is an integer only when all 9 criteria are known; otherwise use
  `f_score_partial.display` and `coverage_pct`.
- filings.xbrl.org results are historical (FY ≤ 2023); always say so.
- Developers and construction firms often show negative operating cash flow
  in growth phases; mention the business model when flagging them.
- Keep gpw.pl / Notoria data local (licence); quote numbers, do not export
  tables of them to third parties.

## Output contract (JSON)

`skill`, `version`, `lang`, `mode`, `as_of`, `universe{spec, size}`,
`filters`, `companies[]`, `coverage{}`, `bank_regression`, `signals[]`,
`run{http_requests, runtime_s}`, `data_provenance`.

`companies[]`: `{ticker, sector_type: industrial|bank|insurer, f_score|null,
quality_score|null, red_flags[{code, severity, value, threshold}], metrics{},
f_score_partial{passed, available, ratio, coverage_pct, lower, upper, band,
display, source}, f_criteria[], quality{score, coverage_pct, rank,
pct_universe, pct_sector, components[]}, data_checks[], period, period_end,
stale_period, sector, pbv_vs_roe (banks)}`.

`signals[]` (gpw-signal-postmortem contract v1): `quality_high` (long) and
`quality_red_flags` (avoid), only with adequate coverage, a fresh period and
no data-check failure; dated by when the period first entered the history so
repeated runs do not duplicate them.

## References

- `references/methodology.md` - evidence (Piotroski 2000, Sloan 1996,
  Novy-Marx 2013, Asness-Frazzini-Pedersen 2019, WSE studies), exact
  definitions, the data-limitations table, weights and thresholds. Load it on
  "how / why" questions.

## Related skills

`gpw-valuation` (fair value, reverse DCF), `gpw-dividend-analyzer` (dividend
safety), `gpw-trend-screener` (price leaders - combine with quality),
`gpw-research-dossier` (one file per company), `gpw-watchlist`,
`gpw-pre-trade-gate`, `gpw-earnings-reaction` (market reaction to the same
reports), `gpw-signal-postmortem` (did the signals work).
