---
name: gpw-daily-brief
description: The one page to read after the Warsaw Stock Exchange (GPW) close, plus a weekly variant. Assembles the newest reports of the other GPW skills (market regime, exposure coach, breadth, sector trend, trend screener, watchlist, event calendar, KNF shorts and insider notices, earnings reaction; weekly also macro, portfolio check, risk lab, signal post-mortem, dividends) and the trade journal into a headline, what needs attention, what changed since the previous brief, positions vs stops, watchlist names near the pivot or invalidated, ranked new signals, the next 5 sessions and a data-freshness table, each item citing its source skill. Can run the daily pipeline first. Use when the user asks what happened today on GPW, wants a daily or weekly summary, or to automate the evening check. PL triggers - "co dziś na GPW", "podsumowanie sesji", "dzienny brief", "co wymaga uwagi", "przegląd tygodnia". EN triggers - "GPW daily brief", "what changed today", "weekly market review". Never tells the user to buy or sell.
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
---

# GPW Daily Brief / Dzienny brief GPW

Turns the whole skill set into a routine: after the session, one page that
says **what needs attention, what changed and what is coming**, with every
line traceable to the skill report it came from. `brief` is pure assembly
of existing JSON reports (no market downloads); `run` optionally executes
the daily pipeline of sibling skills into one folder first.

**Workflow position.** Fed by the newest JSON reports of `gpw-market-regime`,
`gpw-exposure-coach`, `gpw-market-breadth`, `gpw-sector-trend`,
`gpw-trend-screener`, `gpw-watchlist`, `gpw-event-calendar`, `gpw-ownership`
(shorts, insiders), `gpw-earnings-reaction` and, weekly, `gpw-macro-regime`,
`gpw-portfolio-check`, `gpw-risk-lab`, `gpw-signal-postmortem`,
`gpw-dividend-analyzer`, `gpw-trade-review`, plus the `gpw-trade-journal`
open positions and the cached GPW closes. Feeds the user's decision of the
day and, via its `snapshot`, the "what changed" baseline of the next brief.

## When to use

- "Co dziś na GPW?", "podsumuj sesję", "co wymaga mojej uwagi?", "zrób
  dzienny brief", "przegląd tygodnia", "what changed today on GPW?",
  "weekly review of my GPW setup".
- Setting up an evening routine or automation (see "Automation").

Not for: a single stock in depth (`gpw-research-dossier`), deciding a trade
(`gpw-pre-trade-gate`), sizing (`gpw-position-sizer`), US markets.

## Language

Answer in the user's language; pass `--lang pl` (default) or `--lang en`.
Text quoted from source reports (e.g. macro "watch" items) stays in the
language those reports were written in.

## Workflow

### 1. Get the reports (pick one)

Reports younger than 3 sessions are reused as they are. Otherwise run the
pipeline (from this skill's directory):

```bash
python3 scripts/gpw_daily_brief.py run --output-dir ~/gpw-reports            # daily pipeline + brief
python3 scripts/gpw_daily_brief.py run --output-dir ~/gpw-reports --weekly   # + weekly steps
python3 scripts/gpw_daily_brief.py run --output-dir DIR --offline            # cache only (fast, no network)
python3 scripts/gpw_daily_brief.py run --list-steps
```

Steps, in order (ROADMAP v1.2 section 3): `breadth`, `sector`, `regime`,
`exposure`, `screener`, `watchlist-ingest`, `calendar` (upcoming, 10
sessions), `calendar-portfolio` (only with positions or a watchlist),
`watchlist-refresh`, `shorts`, `insiders` (`--insider-days`, default 3),
`earnings` (scan, `--earnings-lookback` 30 days), `postmortem-record`.
`--weekly` adds `macro`, `portfolio`, `risk-stress`, `dividend` (these three
only with open journal positions) and `postmortem-evaluate`. The watchlist
steps run only when `gpw-watchlist` is installed; the watchlist is ingested
before the calendar and refreshed after it, so its event alerts are current.

| Flag | Meaning |
|---|---|
| `--steps a,b` / `--skip a,b` | subset / exclusions (unknown id = exit 2) |
| `--offline` | passed to every data step |
| `--timeout S` | per-step limit (default 2-45 min per step: cold-cache steps get 45) |
| `--account-size` | passed to exposure coach and screener |
| `--state-root DIR` | sandbox for breadth history, ownership, watchlist and signal-ledger state (testing) |
| `--strict` | exit 1 if any step failed or timed out |
| `--no-brief` | only run the steps |

Each step is a separate process with captured exit code, duration and the
new files it wrote; a failure or timeout is logged and the run continues.
The run log is printed and saved as `gpw_daily_brief_run_<timestamp>.json`.
The first ever run fills the archive cache (20-40 min, one-off) - say so.

### 2. Assemble the brief (if you did not use `run`)

```bash
python3 scripts/gpw_daily_brief.py brief --input-dir ~/gpw-reports
python3 scripts/gpw_daily_brief.py brief --input-dir ~/gpw-reports --weekly --full --lang en
```

| Input | Flag | Default |
|---|---|---|
| Report folder | `--input-dir` | `.` (output goes there too) |
| Journal | `--journal-dir` / `--no-journal` / `--include-paper` | `~/.gpw-trading-skills/journal` |
| Watchlist | `--watchlist PATH` / `--no-watchlist` | newest gpw-watchlist report in the folder, else `~/.gpw-trading-skills/watchlist.json` |
| Closing prices | `--cache-dir`, `--no-prices` | local GPW archive cache, read only |
| Historical brief | `--as-of YYYY-MM-DD` | today (later reports are ignored) |
| Windows | `--calendar-sessions 5`, `--event-sessions 10`, `--new-sessions 3` | |
| Output | `--full`, `--json`, `--no-files` | one page, Markdown, files written |

Writes `gpw_daily_brief[_weekly]_YYYY-MM-DD_HHMMSS.{json,md}`. Exit 1 = no
report of any skill in the folder (and no journal): offer `run`.

### 3. Present it (answer first, then context)

1. If the Markdown starts with a stale / missing warning, say that first
   and in one sentence (e.g. "breadth report is 5 sessions old").
2. **Lead with the 1-2 items of "Wymaga uwagi / Needs attention"** - e.g.
   "TORPOL closed at 57.00, below your 58.00 stop" or "regime downgraded to
   correction, ceiling 25%". Phrase them as things to check against the
   user's own plan ("your journal says the exit is below 58 - is that still
   the plan?"), never as orders. If nothing is urgent, say so in one line.
3. Then the three headline lines (regime, exposure limits, breadth with its
   change) and "Co się zmieniło".
4. Positions, watchlist, new signals and the calendar only as far as they
   matter today; offer the rest (`--full`).
5. Weekly: macro backdrop, portfolio risk (VaR, worst stress scenario),
   post-mortem hit rates with n (say "too few to conclude" for small n), and
   point to `gpw-trade-review` for the month.
6. Close with the freshness line and offer the commands for anything missing.

Every item carries its source in brackets (`[market-regime]`,
`[ownership]`...); keep that attribution when you summarise. Explain jargon
once for beginners: *pivot* (the price a setup must close above), *stop*
(the price at which the user decided the idea is wrong), *dni dystrybucji*
(index down on higher turnover - institutional selling), *zatłoczony short*
(many funds disclosed short positions in the KNF register).

### 4. Frame it correctly

- Never say "buy", "sell", "exit now" or "add". The brief is a checklist of
  facts against the user's own plan and limits.
- A below-stop position is a prompt to check the journal plan; a new VCP or
  insider cluster is a name to research (`gpw-research-dossier`,
  `gpw-pre-trade-gate`), not a signal to act.
- Ranked signals are ordered by documented rules, not by expected return.

## Key rules (details in references/methodology.md)

- Inputs: newest report per skill (and mode) via `gpwlib.reports`; the
  previous report of the same skill with an earlier `as_of` is the "what
  changed" baseline, the previous brief's `snapshot` the fallback.
- Stale: daily inputs > 3 sessions, weekly inputs > 5 sessions behind.
- Attention order: critical (below stop, breaker pause) > high (regime or
  posture downgrade, breaker reduce, strong negative earnings on a holding,
  < 3% above stop) > medium (event within 2 sessions, crowded short or
  insider selling on a holding, watchlist invalidated / breakout / near
  pivot, core input stale).
- Signals: held > watchlist > other, then strength 0-100 from the source
  skill, then a fixed kind order, then ticker; window = since the previous
  brief (else the last 3 sessions); one-page view: at most 8, at most 4 per
  source.
- Watchlist zones as in gpw-trend-screener: near pivot = up to 5% below,
  breakout = up to 5% above, extended = more than 5% above.

## Output contract (JSON)

`skill`, `version`, `lang`, `mode` (`daily`|`weekly`), `as_of`,
`data_provenance`, `headline[]` (`{key, text, source}`), `attention[]`
(`{severity, kind, ticker, text, source}`), `changes[]` (`{field, from, to,
direction, text, source, since}`), `positions{items[] {ticker, close, stop,
dist_to_stop_pct, pnl_pct, flags[], events[], ownership{}, earnings,
sources[]}, circuit_breaker}`, `watchlist{items[] {ticker, stage, pivot,
stop, close, dist_to_pivot_pct, status, alerts[]}}`, `signals{since,
items[] {rank, kind, ticker, tier, strength, source, date, text}}`,
`calendar{start, end, items[]}`, `weekly{macro, portfolio, risk,
postmortem, dividend, trade_review}`, `inputs[]` (`{input, skill, mode,
file, as_of, sessions_behind, stale, status, command}`), `snapshot{}`,
`baseline{}`, `ranking_rules{}`.

## Automation (document only - nothing is installed)

See `references/automation.md`: a cron line, a launchd plist for macOS and
the Claude Code `/schedule` command (cloud routine) for a weekday 18:15 run
(data is final from 18:00) and a Friday weekly run. Offer these; set them up
only if the user asks.

## References

- `references/methodology.md` - inputs, baselines, every threshold and
  ranking rule with its justification, limitations.
- `references/automation.md` - cron, launchd and `/schedule` examples.

## Related skills

All GPW skills above are inputs. Next steps from the brief:
`gpw-research-dossier` (one company in depth), `gpw-pre-trade-gate` (before
a trade), `gpw-position-sizer`, `gpw-trade-journal` (update a stop or a
thesis), `gpw-watchlist` (promote / drop names), `gpw-trade-review`
(monthly review).
