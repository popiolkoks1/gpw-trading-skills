# Changelog

## [1.2.0] - 2026-11-16

Advanced skills and one connected workflow: 14 new skills, 2 extended skills
(27 in total). Design decisions, research and JSON contracts:
`docs/ROADMAP_v1.2.md`; the daily/weekly routine: `docs/WORKFLOW.md`.

### Added

- New skills:
  - `gpw-backtester`: long-only daily backtester with per-order minimum fees,
    liquidity cap, gap-through stops, Belka tax with loss carry-forward, IKE
    mode, survivorship-free universe; walk-forward, parameter plateau,
    Deflated Sharpe, PBO (CSCV), cost stress and a REJECT..ROBUST verdict.
  - `gpw-signal-postmortem`: signal ledger (generic `signals[]` contract),
    forward returns 5/20/60 vs WIG and equal-weight, MFE/MAE, Wilson CI,
    date-clustered t-stats, evidence-gated feedback.
  - `gpw-ownership`: KNF net short register (history since 2012, crowded
    shorts, days to cover), MAR art. 19 insider notices from ESPI with a
    paste-the-PDF parser and cluster detection, OFE annual holdings.
  - `gpw-event-calendar`: periodic report dates (ESPI schedules or statutory
    deadlines, Dz.U. 2025 poz. 755), AGMs, dividends with T+2 ex-dates,
    index revisions, MSCI, MPC, CPI, FW20 expiry, holding-window check, .ics.
  - `gpw-earnings-reaction`: report-time session alignment, CAR vs WIG and
    market model, abnormal turnover, drift study vs a neutral control.
  - `gpw-macro-regime`: NBP rate cycle, real rate, PLN regime, PL-DE 10Y
    spread, 2s10s curve, HICP; composite and sector tilts with a market check.
  - `gpw-quality-screener`: Piotroski F-score with explicit unknowns and
    bounds, ROE/ROIC, accruals, red flags, bank P/BV-vs-ROE model.
  - `gpw-dividend-analyzer`: dividend history rebuilt from archive
    adjustments and ESPI resolutions, stability, net yield vs IKE/IKZE, trap
    flags, ex-date drop and dividend-capture arithmetic.
  - `gpw-valuation`: reverse DCF, PLN WACC with Damodaran country risk,
    2-stage FCFF, sensitivity grid, comps, bank excess-return model, DDM.
  - `gpw-risk-lab`: historical stress replays (2008-2022) and factor shocks,
    liquidity under stress, FW20 hedge with beta, residual risk, roll and
    margin notes, short-ETF alternative for small portfolios.
  - `gpw-pair-trading`: Engle-Granger with MacKinnon p-values (matches
    statsmodels), half-life, stability, Benjamini-Hochberg, switch analysis.
  - `gpw-watchlist`: staged watchlist state fed by the producer skills, with
    pivot/stop/breakout/event alerts and journal sync.
  - `gpw-research-dossier`: one sourced dossier per company with fact ids,
    `--run` of sibling skills, bull/bear debate validator, thesis command.
  - `gpw-daily-brief`: daily and weekly pipeline runner and one-page brief.
- `gpw-sector-trend` 1.1.0: `--rrg` Relative Rotation Graph mode (sector
  and size indices or stocks of one sector), tails, transitions, SVG.
- `gpw-pre-trade-gate` 1.1.0: context checks `event_window`,
  `crowded_short`, `insider_selling`, `earnings_reaction`,
  `quality_red_flags`, `macro_context`, `stress_test`; input discovery via
  `gpwlib.reports`.
- `docs/WORKFLOW.md`, `docs/ROADMAP_v1.2.md`; README rewritten (PL/EN).
- `shared/gpwlib` 1.2.0 foundation for the advanced skills:
  `http` (polite cached GET/POST, >= 1 s per host, identifying User-Agent,
  retries, typed errors, `offline` mode), `reports` (find the newest JSON of
  another skill, sessions behind, stale warning PL/EN), `espi` (ESPI/EBI
  report list per day or range, classifier, report page parser),
  `fundamentals` (gpw.pl indicators and Notoria tabs, ticker -> ISIN mapping,
  local history with restatement detection, xBRL-JSON from filings.xbrl.org,
  ESEF zip parsing), `gpw_archive` futures support (type 35, settlement
  price, futures symbol parser) and the full instrument-type list.
- `gpwlib.http.get_bytes` (and `Response.content`): binary downloads (PDF,
  XLSX, ZIP) through the same rate limiter, User-Agent, retries and cache,
  stored as exact bytes; `http.drop_cached` removes an unusable cached copy.
- `gpwlib.fundamentals`: ticker <-> ISIN for every listed share from the
  official gpw.pl / newconnect.pl quotes tables (`listed_companies`,
  `isin_map(refresh=...)`, `isin_to_ticker_map`; ~400 Main Market + ~330
  NewConnect shares, cached 7 days), pre-rename aliases (`CCC` -> MODIVO's
  ISIN) and a company-name matcher for rows without ISIN
  (`normalize_company_name`, `ticker_for_company_name`).
- `gpwlib.panel`: renamed tickers - an explicit, sourced alias table
  `RENAMES` (CCC -> MODIVO, SANPL -> ERSTEPL and 51 more since 2019),
  `build_panel(..., merge_renames=True)` stitches them under the newest
  ticker (`Series.renamed_from`), `canonical_name`, and a report-only
  heuristic `detect_rename_candidates`. `Series.open` (adjusted like the
  close) and `Series.volume` (when the source has it).
- `gpwlib.reports.iter_reports(input_dir, skill=None)`: every report in a
  folder, oldest first (not only the newest).
- `gpwlib.plcal.public_holidays`, `is_working_day`, `roll_to_working_day`
  for statutory deadlines.

### Fixed

- A dropped connection (`http.client.IncompleteRead`) is now a typed fetch
  error in `gpw_archive` and `gpwlib.http` instead of a crash.
- Tests: a suite-wide guard (`tests/conftest.py`) blocks all network access;
  the one test that silently reached gpw.pl now uses the cache only.
- Windows: importing `gpwlib` switches non-UTF-8 stdout/stderr (cp1252
  pipes, as used when Claude runs a script) to UTF-8, so Polish output no
  longer raises `UnicodeEncodeError` (affected every skill, incl. 1.1).
  Tests read and write files as UTF-8 explicitly.
- `gpw-signal-postmortem`: t-statistics treat numerically zero dispersion
  deterministically (Python 3.12's compensated `sum()` vs 3.9 noise), and a
  zero-dispersion one-sided sample is judged on the Wilson interval.
- `gpw-pre-trade-gate`: `insider_selling` is UNKNOWN (not PASS) when the
  insider report covers fewer than 30 days.
- `--as-of` historical runs are no longer marked stale:
  `provenance.freshness(..., reference=...)` / `provenance.build(...,
  reference_date=...)` judge freshness against the requested date and still
  record the distance to today (`sessions_behind_today`). Applied in
  `gpw-market-breadth`, `gpw-sector-trend`, `gpw-market-regime`,
  `gpw-etf-planner`, `gpw-trend-screener`, `gpw-portfolio-check`,
  `gpw-exposure-coach` and `gpw-pre-trade-gate`.
- `gpwlib.espi.classify`: the ESAP code `TRANSD` alone no longer makes a
  report `insider` (some issuers, e.g. KRUK, tag every report with it); MAR
  art. 19 / managers'-transaction wording in the title or body is required
  (`espi.mentions_managers_transaction`, now also used by `gpw-ownership`).
- `gpw-ownership` downloads fund PDFs with `http.get_bytes` instead of the
  private `http._fetch`.
- `gpw-earnings-reaction`: deadlines cite the current regulation (Dz.U. 2025
  poz. 755, § 83, amended Dz.U. 2026 poz. 1205) instead of the repealed
  Dz.U. 2018 poz. 757 § 79; no Q2/Q4 quarterly-report obligation (§ 83
  ust. 2) and a deadline on a weekend or holiday moves to the next working
  day (ust. 11), e.g. Q3 2026 -> 30.11.2026.
- Series of renamed companies are no longer split in two in every skill that
  uses `panel.build_panel`.
- `gpw-ownership` emits the current ticker everywhere (shorts, insiders,
  funds, parse, ticker mode, signals): the ISIN -> ticker map prefers the
  current listing over `panel.RENAMES` aliases, so ISIN PLCCC0000016 is
  `MODIVO` (not `CCC`) and gets `days_to_cover`; the source name is kept in
  `register_name`.
- `gpwlib.panel`: no corporate-action adjustment is inferred across sessions
  missing from the archive cache (a missing 2022-01-03 used to fake ~320
  "dividends"); `gpw_archive.load_sessions` marks such gaps
  (`missing_before`), new `gpw_archive.session_gaps` / `find_gaps` /
  `iter_sessions` (streaming), `Series.gap_joins`, and
  `provenance.build(data_gaps=...)` adds a `data_gaps` list and note.
  `gpw-dividend-analyzer` warns from the shared gap detection.
- `gpwlib.panel.build_panel` takes any iterable of sessions plus `names=`,
  `fields=`, `compact=True` (`array('d')`) and `gaps=`; `gpw-backtester`
  streams through it instead of the private `_adjust_series` (identical
  metrics on 2019-06..2026-11, peak memory 659 -> 381 MB for trend_template).
- `gpwlib.panel.index_series` is case-insensitive and stitches renamed-case
  index series (`WIG-gry` -> `WIG-GRY`, `WIG20dvp` -> `WIG20DVP`, ...).
- `gpwlib.espi.parse_report_html`: `company` no longer contains a long
  (line-wrapped) report title; fallback to the upper-case legal name.
- `gpwlib.fundamentals.snapshot`: optional `ttl_indicators` / `ttl_notoria`
  (defaults unchanged) and per-tab `indicators_retrieved_at`,
  `notoria_retrieved_at`, `from_cache`; `gpw-quality-screener` uses it
  instead of its own copy.
- `gpwlib.provenance.sessions_behind` counts an explicit future reference
  day as a session (only today's unfinished session is left out).
- `gpwlib.reports`: the RRG report of `gpw-sector-trend` (`"mode": "rrg"`)
  is a report variant (`REPORT_VARIANTS`) and is never returned as the
  classic sector-trend report (`mode=` to ask for it); `gpw-exposure-coach`,
  `gpw-daily-brief` and `gpw-research-dossier` are covered by tests.
- `gpw-research-dossier` reads the real RRG schema (`rrg.items[]`, sector
  index of the ticker, plus the ticker's own quadrant from a stock-mode RRG)
  and reads the watchlist per its contract (schema major check,
  `GPW_WATCHLIST_DIR`, `entered`, alerts by severity, risk tags, market
  snapshot, pre-rename tickers).
- `gpw-pre-trade-gate` takes KNF shorts and insider data from the newest
  ownership report of each mode (the newest file may be an insiders run
  without shorts).
- `gpwlib.tax`: documented derivatives facts (PIT-38, art. 30b, 5-year loss
  carry-forward) and the IKE/IKZE art. 27 ust. 2 rule, with bilingual notes.

## 1.1.0 — 2026-11-15

Eight new skills (13 in total), for beginners and advanced users.

- **Beginners:** `gpw-start` (readiness check, equity range, plan, glossary,
  learning path), `gpw-account-planner` (IKE / IKZE / OKI / regular account over
  many years), `gpw-etf-planner` (GPW ETF catalog with archive stats, whole-unit
  monthly buy plan with commissions, DCA test).
- **Everyone:** `gpw-portfolio-check` (concentration, correlation, VaR/ES,
  drawdown, liquidity, vs WIG).
- **Advanced:** `gpw-trend-screener` (Minervini Trend Template, IBD-style RS,
  VCP, execution states, history mode), `gpw-market-regime` (distribution days,
  follow-through day, top-risk score), `gpw-exposure-coach` (posture, exposure
  ceiling, risk per trade, circuit breaker), `gpw-pre-trade-gate` (fail-closed
  GO / NO-GO checklist).
- `gpw-trade-journal`: paper mode (`--paper`); `gpw-trade-review` reviews paper
  trades separately and never puts them into PIT-38.
- `gpw-position-sizer`: uses the shared commission table, warns when it is older
  than 180 days; OKI note corrected to the act (exemption covers qualified assets
  only).
- `shared/gpwlib` 1.1.0: `brokers` (dated commission presets), `tax` (Belka,
  IKE/IKZE limits 2026–2027, OKI rules verified against Dz.U. 2026 poz. 1098),
  `stats` (returns, volatility, drawdown, correlation, beta, VaR/ES, HHI),
  `journal_io` (read-only journal access), ETF archive type 241.
- Parser test on a trimmed real GPW archive page (detects layout changes).

## 1.0.0 — 2026-11-15

First public release.

- `gpw-market-breadth`, `gpw-sector-trend`, `gpw-position-sizer`,
  `gpw-trade-journal`, `gpw-trade-review` (PL/EN).
- `shared/gpwlib`: GPW archive client with cache, Polish exchange calendar,
  dividend/split adjustment from GPW reference prices, GPW Benchmark
  constituents, provenance block, PL/EN formatting.
- Packaging script for claude.ai `.skill` uploads, CI (Linux/macOS/Windows,
  Python 3.9 and 3.12), trigger/output evals per skill.
