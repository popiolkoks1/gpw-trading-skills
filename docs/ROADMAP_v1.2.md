# Roadmap v1.2: advanced skills and one connected workflow

Status: implemented in 1.2.0 (branch `feature/v1.2-advanced`). Written 2026-11-16 after two research passes:
foreign equivalents (tradermonty/claude-trading-skills, Anthropic financial-services plugins,
TradingAgents, openinsider, StockCharts RRG, QuantConnect) with academic evidence, and a
due-diligence pass on Polish data sources (each endpoint fetched and checked).

## 1. Decisions

| Idea | Decision | Why |
|---|---|---|
| backtester | **new skill** `gpw-backtester` | Highest value for advanced users; on WSE factor premiums vanish after costs (Zaremba & Konieczka 2016), so realistic PLN costs decide everything. |
| signal post-mortem | **new skill** `gpw-signal-postmortem` | Closes the loop signal → outcome → weights (tradermonty `signal-postmortem`, fixed: excess return vs WIG, not raw sign). |
| relative rotation (RRG) | **mode of `gpw-sector-trend`** (`--rrg`) | Same data and universe; a separate skill would duplicate it. |
| pair trading | **new skill** `gpw-pair-trading`, framed as relative value | Shorting is hard on GPW; used to choose a switch (PKO vs PEO) or a hedge leg. Gatev et al. 2006, Engle-Granger. |
| insider tracker, short interest, OFE flows | **one skill** `gpw-ownership` (modes `shorts`, `insiders`, `funds`) | KNF short register is clean JSON since 2012 (fully automatic). Insider notices are detected automatically from the ESPI list; volumes/prices come from the PDF the user pastes. OFE: annual PDFs only, weak evidence as a standalone signal. |
| event calendar | **new skill** `gpw-event-calendar` | Beginner 4/5, advanced 4/5. Feeds the pre-trade gate (event inside the holding window). |
| earnings reaction / PEAD | **new skill** `gpw-earnings-reaction` | Price-based surprise (no free consensus). On WSE drift appears mainly after negative surprises (Jagliński 2020), so it is mostly an exit/avoid warning. |
| quality screener | **new skill** `gpw-quality-screener` | Piotroski F-score, ROE/ROIC, debt; separate metrics for banks and insurers. |
| dividend analyzer | **new skill** `gpw-dividend-analyzer` | Dividend-heavy blue chips; net yield after Belka vs IKE/IKZE. |
| valuation | **new skill** `gpw-valuation` | Reverse DCF first, Damodaran country-risk WACC, sensitivity grid, comps. |
| bull/bear debate | **part of `gpw-research-dossier`** | Structure, not alpha (TradingAgents evaluation is thin). Every claim must cite a dossier field. |
| macro regime | **new skill** `gpw-macro-regime` | NBP, BondSpot, ECB, Bundesbank, GUS/Eurostat: all automatic. Slow context only. |
| futures hedge + stress test | **one skill** `gpw-risk-lab` (modes `stress`, `hedge`) | FW20 quotes in the archive (type 35, multiplier 20 zł). Stress results drive the hedge size. |
| (missing) watchlist | **new skill** `gpw-watchlist` | The state between "screener found it" and "I bought it". |
| (missing) research dossier | **new skill** `gpw-research-dossier` | One file per company from every other skill's JSON, then the debate. |
| (missing) daily brief | **new skill** `gpw-daily-brief` | One page after the close: what changed and what needs attention. |
| pre-trade gate | **extended** | New checks: event inside the holding window, crowded short, insider selling cluster, earnings reaction, quality red flags. |

## 2. Shared foundation (gpwlib 1.2)

| Module | Purpose |
|---|---|
| `http.py` | Polite GET/POST with identifying User-Agent, rate limit (>= 1 s per host), on-disk cache with TTL, typed errors. Used by every new data source. |
| `reports.py` | One implementation of "find the newest JSON of skill X in `--input-dir`", read `as_of`, count sessions behind, warn if older than N sessions. Replaces copies in five skills. |
| `espi.py` | List ESPI/EBI reports for a day or date range from gpw.pl (`GPWEspiReportUnion`), parse the HTML, classify each report (`insider`, `periodic_q`, `periodic_h`, `periodic_y`, `agm`, `dividend`, `holding_5pct`, `buyback`, `other`). Fetch a report body (`komunikat?geru_id=`). |
| `fundamentals.py` | Company snapshot: indicators tab (shares, market cap, book value, P/E, P/BV, dividend yield, sector) and the Notoria tab (revenue, EBIT, EBITDA, net profit, assets, equity, cash flows, capex) per ticker; a quarterly local history cache; optional xBRL-JSON history from filings.xbrl.org (2020–2023) and user-supplied ESEF zip parsing. |
| `provenance.py` | Fix: freshness relative to `--as-of`, not today (known issue from 1.1). |
| `gpw_archive.py` | `TYPE_FUTURES = 35` and the full type list. |

## 3. Workflow

```
DAILY (after 17:05)                 BEFORE A TRADE                      PERIODIC
market-breadth ┐                    research-dossier ← screener,        weekly:   macro-regime, portfolio-check,
sector-trend  ─┼→ exposure-coach      quality, valuation, dividend,               risk-lab, earnings-reaction scan
market-regime ┘        │              ownership, events, earnings,      monthly:  trade-review, signal-postmortem
trend-screener ──→ watchlist          macro  ──→ bull/bear debate                   → weights feedback
event-calendar ┐       │                    ↓                           before changing a rule: backtester
ownership      ┼→ daily-brief        position-sizer → pre-trade-gate    yearly:   ownership funds (Jan),
earnings-react ┘                       → trade-journal                            dividend season, PIT-38
```

## 4. JSON contracts (new)

Every report keeps the 1.1 envelope: `skill`, `version`, `lang`, `as_of`, `data_provenance`.

- `gpw-event-calendar` → `events[]`: `{ticker|null, isin|null, type, date, date_kind: "confirmed"|"estimated"|"statutory_deadline", title, source}`; `type` in `periodic_report, agm, dividend_record, dividend_payment, ex_dividend, index_revision, mpc_meeting, macro_release, msci_review, other`.
- `gpw-ownership` → `shorts.by_issuer[]`: `{ticker, isin, total_pct, holders, change_20s_pp, crowded}`; `insiders.by_ticker[]`: `{ticker, buys, sells, distinct_insiders, net_value_pln|null, cluster_buy, cluster_sell, last_date}`; `funds.by_ticker[]`.
- `gpw-earnings-reaction` → `reactions[]`: `{ticker, report_date, report_type, session_alignment, car_3d_pct, abnormal_turnover, drift_20s_pct|null, label: "strong_positive"|"positive"|"neutral"|"negative"|"strong_negative"}`.
- `gpw-quality-screener` → `companies[]`: `{ticker, sector_type: "industrial"|"bank"|"insurer", f_score|null, quality_score, red_flags[], metrics{}}`.
- `gpw-dividend-analyzer` → `companies[]`: `{ticker, dps_history[], years_paid, cuts, yield_gross_pct, yield_net_regular_pct, payout_ratio|null, stability_score, trap_flags[]}`.
- `gpw-valuation` → `{ticker, implied_growth_pct, wacc_pct, fair_value_range{low, base, high}, sensitivity[][], comps{}}`.
- `gpw-macro-regime` → `macro.state` (`easing`|`neutral`|`tightening` × `pln_strong`|`pln_weak` …), `indicators{}`, `sector_tilts{}`.
- `gpw-risk-lab` → `stress.scenarios[]`: `{id, portfolio_return_pct, pln_loss, worst_positions[]}`; `hedge`: `{beta, contracts, notional_pln, residual_risk_pct}`.
- `gpw-backtester` → `strategy`, `metrics{cagr, max_dd, sharpe, trades, win_rate, expectancy_r, exposure}`, `robustness{walk_forward, deflated_sharpe, oos_is_ratio, verdict}`.
- `gpw-signal-postmortem` → `buckets[]`: `{dimension, bucket, n, hit_rate, mean_excess_pct}`, `feedback{}`.
- `gpw-watchlist` (state `~/.gpw-trading-skills/watchlist.json`) → `items[]`: `{ticker, stage: "candidate"|"watch"|"ready"|"dropped", added, source, pivot|null, stop|null, notes[]}`.
- `gpw-research-dossier` → `{ticker, sections{<skill>: {as_of, summary, key_fields}}, missing[], debate{bull[], bear[], verdict_inputs}}`.

## 5. Build order

1. Foundation (gpwlib 1.2) with tests.
2. Wave A (data and analysis): backtester, signal-postmortem, ownership, event-calendar, earnings-reaction, macro-regime.
3. Wave B (fundamentals and risk): quality-screener, dividend-analyzer, valuation, risk-lab, pair-trading.
4. Wave C (integration): watchlist, research-dossier, daily-brief, sector-trend `--rrg`, pre-trade-gate checks.
5. Docs, CHANGELOG 1.2.0, full test suite, packaging, end-to-end run on real data, PR.

## 6. Data limits stated honestly

- Insider notices: volumes and prices are in PDF attachments that gpw.pl blocks for scripts. Detection is automatic; details need the user to paste the PDF text.
- Fundamentals: free history is thin (filings.xbrl.org has PL filings for 2020–2023 only). The skill builds its own quarterly history over time and accepts ESEF files from the user.
- Dividends: the archive detects ex-dates from price adjustments; third-party calendars (biznesradar) are for local personal use only.
- OFE holdings: annual PDFs with values, no share counts.
