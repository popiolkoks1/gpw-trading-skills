# The GPW workflow: how the 27 skills fit together

This guide is for anyone using the skills. Each skill works on its own, but
together they form a routine. They pass work between them as JSON reports.
Every report states its date (`as_of`) and whether it is stale, so a downstream
skill knows how much to trust it.

Commands below are shown from the repository root (in an installed copy, use
`~/.claude/skills/<skill>/scripts/...`). Use one shared
`--output-dir` (for example `~/gpw-reports`) and pass the same folder as
`--input-dir` to skills that read other skills' reports. Your journal and
watchlist live in `~/.gpw-trading-skills/`.

## 1. If you are new to investing

```
gpw-start → gpw-account-planner → gpw-etf-planner → (yearly) gpw-portfolio-check, gpw-dividend-analyzer
```

You can stop there. The active-investor tools below are optional. To practise
picking stocks, use `gpw-trade-journal --paper` before you use real money.

## 2. Daily, after the close (about 17:30)

Run everything in one command:

```bash
python3 skills/gpw-daily-brief/scripts/gpw_daily_brief.py run --output-dir ~/gpw-reports
python3 skills/gpw-daily-brief/scripts/gpw_daily_brief.py brief --input-dir ~/gpw-reports
```

The `run` command executes these steps in this order. A failed step does not
stop the others.

| Step | Skill | What it adds |
|---|---|---|
| 1 | gpw-market-breadth | how many stocks take part in the move |
| 2 | gpw-sector-trend | sector leadership and rotation |
| 3 | gpw-market-regime | uptrend, under pressure, correction; distribution days |
| 4 | gpw-exposure-coach | posture and limits (exposure, risk per trade, circuit breaker) |
| 5 | gpw-trend-screener | Trend Template leaders, VCP setups, pivots |
| 6 | gpw-watchlist ingest | new candidates and tags from all producers |
| 7 | gpw-event-calendar | results dates, ex-dividend, AGMs, MPC, index revisions |
| 8 | gpw-watchlist refresh | pivot, stop and breakout alerts; events per item |
| 9 | gpw-ownership | changes in KNF short positions, new insider notices |
| 10 | gpw-earnings-reaction | how the market took today's reports |
| 11 | gpw-signal-postmortem record | stores every signal for later evaluation |

The brief opens with the one or two things that need action, for example a
position below its stop, then shows context. It never says buy or sell.

## 3. Before a decision

```
gpw-research-dossier build TICKER --run   ← collects every skill's view of the company
  → bull vs bear debate (each claim cites a fact id; validate-debate checks it)
  → gpw-position-sizer                     ← shares for your planned risk
  → gpw-pre-trade-gate                     ← GO / GO WITH CONDITIONS / NO-GO
  → gpw-trade-journal add ...              ← dossier to-thesis prints the command
```

The gate reads the same reports as the dossier. It adds context checks: an
earnings report inside the holding window, a crowded short, insider selling, a
strongly negative earnings reaction, quality red flags, a macro headwind and the
stress test. Only the hard checks, or `--strict-events`, can return NO-GO.

## 4. Weekly

```bash
python3 skills/gpw-daily-brief/scripts/gpw_daily_brief.py run --weekly --output-dir ~/gpw-reports
```

The weekly run adds:
- gpw-macro-regime;
- gpw-portfolio-check;
- gpw-risk-lab stress (your portfolio in 2008, 2020 and 2022, plus factor shocks);
- gpw-signal-postmortem evaluate;
- gpw-dividend-analyzer portfolio.

Also run `gpw-sector-trend --rrg` for the rotation chart.

## 5. Monthly and quarterly

- **gpw-trade-review**: your results, the process-vs-outcome matrix, lessons.
- **gpw-signal-postmortem evaluate / feedback**: which signals actually beat WIG. It suggests changes only when n ≥ 20 and |t| ≥ 3.
- **gpw-backtester**: before you change any rule, test it. It uses real fees, slippage, liquidity caps, tax, walk-forward and overfitting checks.
- **gpw-quality-screener**: run it each reporting season. Every run saves a snapshot, so year-over-year F-score criteria fill in over time.

## 6. Yearly

- January: gpw-ownership `funds`, the OFE year-end holdings.
- April to September: the dividend season, with gpw-dividend-analyzer and gpw-event-calendar.
- Before 30 April: gpw-trade-review `--year` for the PIT-38 estimate.
- December: gpw-account-planner, which shows next year's IKE/IKZE limits.

## 7. Automation

`gpw-daily-brief/references/automation.md` has ready cron and launchd entries.
A Claude Code scheduled agent cannot read your local journal and watchlist, so
local scheduling is recommended.

## 8. Data limits worth knowing

| Area | Limit |
|---|---|
| Insider volumes and prices | They are in PDF attachments that gpw.pl blocks for scripts. Detection is automatic; for details, paste the PDF text into `gpw-ownership parse`. |
| Fundamentals | gpw.pl shows only the latest period. Year-over-year metrics need saved snapshots or an ESEF file you supply. |
| Short positions | Only positions of at least 0.5% are public. |
| OFE holdings | Annual values only, with no share counts. |
| Index membership | It reflects today's portfolios, not the portfolios at the time of each signal. |
