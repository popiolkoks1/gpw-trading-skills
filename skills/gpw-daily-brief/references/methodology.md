# gpw-daily-brief — methodology

The brief computes almost nothing new. It **selects, compares and ranks**
what the other GPW skills already computed, and says where each line came
from. Every rule below is deterministic and implemented in
`scripts/daily_brief_engine.py` (assembly) and `scripts/daily_brief_inputs.py`
(discovery); the orchestrator is `scripts/daily_brief_runner.py`.

## 1. Design goals

- **Answer first.** The user reads it once after the close. Items that need
  a decision against the user's own plan come first ("Wymaga uwagi"), then
  the market headline, then detail. This follows the checklist idea
  (Gawande, *The Checklist Manifesto*, 2009): a short list of the few
  things that are easy to miss, not a dump of every number.
- **One page.** Default caps keep the Markdown under ~80 lines: attention
  3, changes 6, positions 8, watchlist 6, signals 8, calendar 8; the
  freshness table collapses to one line when everything is fresh. `--full`
  lifts the caps (20/30/50/40/30/40) and prints the full table.
- **Traceable.** Every headline line, change, attention item, position
  note, signal and event carries `source` (the skill) in JSON and a
  `[skill]` tag in Markdown; the "Źródła / Sources" section lists each input
  file with its `as_of`.
- **No advice.** Wording is "close X is below your stop Y — check your
  plan", never "sell". Signals are names to research.

## 2. Inputs and discovery

| Input key | Skill (mode) | Cadence | Stale after |
|---|---|---|---|
| regime | gpw-market-regime | daily | 3 sessions |
| exposure | gpw-exposure-coach | daily | 3 |
| breadth | gpw-market-breadth | daily | 3 |
| sector | gpw-sector-trend | daily | 3 |
| screener | gpw-trend-screener (`screen`) | daily | 3 |
| calendar | gpw-event-calendar (`upcoming`) | daily | 3 |
| calendar_portfolio | gpw-event-calendar (`portfolio`) | optional | 3 |
| shorts / insiders | gpw-ownership (`shorts` / `insiders`) | daily | 3 |
| earnings | gpw-earnings-reaction (`scan` or `portfolio`) | daily | 3 |
| watchlist_report | gpw-watchlist (any command) | optional | 3 |
| macro | gpw-macro-regime | weekly | 5 |
| portfolio | gpw-portfolio-check | weekly (with positions) | 5 |
| risk | gpw-risk-lab | weekly (with positions) | 5 |
| postmortem | gpw-signal-postmortem | weekly | 5 |
| dividend | gpw-dividend-analyzer (`portfolio`) | weekly (with positions) | 5 |
| trade_review | gpw-trade-review | pointer only | 5 |
| previous_brief | gpw-daily-brief (`daily`/`weekly`) | baseline | — |

- Discovery uses `gpwlib.reports.iter_reports` (newest first) and
  `gpwlib.reports.describe` for `as_of`, sessions behind and the stale flag;
  no own glob logic. A skill with several modes (ownership, calendar) is
  split by the report's `mode` field, so a `ticker` run of a skill never
  replaces its market-wide report.
- **3 sessions** is the cross-skill freshness rule of docs/CONVENTIONS.md.
  **5 sessions** for weekly inputs = one trading week, the cadence the
  roadmap gives them; a weekly report is not stale on Wednesday.
- `--as-of D` ignores reports dated after D (no look-ahead) and measures
  staleness against D.
- Missing **daily** inputs are listed with the exact command that produces
  them (run from that skill's folder, `DIR` = the report folder); weekly
  inputs are listed only in `--weekly`, and portfolio/risk/dividend only
  when the journal has open positions. The trade review is a pointer (it
  needs closed trades), never "missing".
- Journal: `gpwlib.journal_io.open_positions` (ACTIVE theses with shares,
  FIFO lots, plan stop - the trailing stop if the user moved it).
- Watchlist: explicit `--watchlist` file, else the newest gpw-watchlist
  report in the folder (it carries refreshed alerts), else the state file
  `~/.gpw-trading-skills/watchlist.json`; absence or a corrupt file never
  stops the brief. Items `dropped` are counted, `entered` ones are shown
  under positions (journal), not twice.
- Closing prices for positions and watchlist come from the newest cached
  share session of the GPW archive (`gpw_archive.load_sessions(1,
  offline=True)`): read only, never a download. The last session's close is
  the same raw or adjusted (adjustments only rescale older prices).

## 3. Business date and baselines

- `as_of` of the brief = newest `as_of` among the core market inputs
  (regime, breadth, exposure, sector, screener), else among all inputs.
- **"What changed" baseline:** for each skill, the newest report whose
  `as_of` is strictly earlier than the current one (re-running the same day
  does not erase yesterday as the baseline). Fields with no previous report
  fall back to the previous brief's `snapshot`.
- **"New" window for signals:** events after the previous brief's `as_of`;
  with no previous brief, the last `--new-sessions` (default 3) sessions
  including `as_of` - a weekend or a missed day does not lose signals.

## 4. "Co się zmieniło" rules

| Field | Reported when | Why |
|---|---|---|
| regime state | any change; direction from the order bear < correction < rally attempt < under pressure < confirmed | the regime is the hard cap of exposure (gpw-exposure-coach) |
| distribution days | any change | each day matters for the 4/6/8 bands (O'Neil) |
| top-risk score | change ≥ 5 points | one sub-component band; smaller moves are noise |
| posture, ceiling, breaker | any change | they are the user's limits |
| breadth score | change ≥ 5 points or a zone change | a zone spans 20 points; 5 = a quarter of it |
| sector leaders | set of the top 3 by uptrend ratio changes (ties: 20-session performance vs WIG; low-sample sectors skipped) | gpw-sector-trend warns that 3-5 company sectors swing 20-30 pp on one stock |
| macro composite | composite changes or score moves ≥ 0.10 | the composite moves in 0.05 steps; 0.10 = a component flipped |

A downgrade of the regime or posture and a worse circuit breaker are
`high` severity and also go to "Wymaga uwagi".

## 5. "Wymaga uwagi" (attention) — order

Severity first, then a fixed kind order, then ticker:

1. **critical** - a held position closed at or below its stop; circuit
   breaker `pause` (with `pause_until`).
2. **high** - regime downgrade; breaker `reduce`; posture downgrade; strong
   negative earnings reaction on a holding (on the WSE post-earnings drift
   was found mainly after negative surprises, Jagliński 2020 - see
   gpw-earnings-reaction); a holding less than **3%** above its stop
   (≈ 1.5 average daily ranges of a WIG20 share, daily sigma 1.5-2%: one
   ordinary bad session can reach it).
3. **medium** - an event of a holding within **2 sessions** (one session
   left to adjust size or stop before it); insider selling cluster or
   crowded short on a holding; watchlist item invalidated (close below its
   stop), above the pivot by up to 5%, or within 5% below it; a core input
   (regime, exposure, breadth) stale or missing.

## 6. Positions

Per open journal position: close vs stop (`dist_to_stop_pct` = (close −
stop) / close), P&L vs FIFO average price, events within
`--event-sessions` (default 10 - a typical swing holding window and the
horizon of gpw-watchlist), KNF short total and crowded flag, insider
clusters, the latest earnings reaction label. Order: below stop > near
stop > strong negative earnings > imminent event > insider selling >
crowded short > no stop. A missing stop is shown, never invented.

## 7. Watchlist zones

Same thresholds as gpw-trend-screener's execution states and gpw-watchlist
(`pre_breakout_pct` = `breakout_max_pct` = 5; Minervini: do not chase more
than 5% above the pivot): invalidated (close < stop) > breakout (0-5% above
pivot) > near pivot (up to 5% below) > with an active alert > extended
(> 5% above) > far below > no pivot / no price. gpw-watchlist alerts of
severity `low` (52-week high, turnover surge) are left out of the one-page
view.

## 8. New signals and ranking

Sources (each item keeps its `source`):

| Kind | Rule | Strength (0-100) |
|---|---|---|
| `vcp_new` | on the current screener watchlist, not on the previous screen's | screener priority score |
| `insider_cluster_buy` | `cluster_buy` with `last_date` in the window | ownership signal score, else 60 |
| `insider_cluster_sell` | as above, only for held or watched names | ownership signal score, else 60 |
| `short_new_position` | KNF `new_position` event dated in the window | ownership `short_new` score, else 25 |
| `short_newly_crowded` / `short_no_longer_crowded` | crowded flag flips vs the previous shorts report | ownership `short_crowded` score, else 50 / 20 |
| `earnings_strong_negative/positive` | the earnings skill's own `signals[]` (liquid, strong) dated in the window | its score |

Fallback strengths are deliberately below typical scored signals: a
notice-title-only insider cluster (`basis: notice_hints`) or a single new
0.5% short is weaker evidence than a parsed, scored signal.

**Ranking key:** (tier: held 0, watchlist 1, other 2) → strength
descending → kind order (`earnings_strong_negative`, `insider_cluster_sell`,
`short_newly_crowded`, `insider_cluster_buy`, `earnings_strong_positive`,
`vcp_new`, `short_new_position`, `short_no_longer_crowded`: negative
information about a stock before positive, filed facts before price
patterns) → ticker. Duplicates (same ticker and kind) keep the strongest.
Ranks are global in JSON; the Markdown shows at most 8 and at most **4 per
source skill**, so an earnings season cannot hide a new VCP or an insider
cluster. Strengths from different skills are not on a common scale; the
rank is an ordering for attention, not an expected return.

## 9. Calendar

Events of the newest `upcoming` and `portfolio` calendars, de-duplicated by
event id, in the next `--calendar-sessions` (default 5 = one trading week)
after `as_of`; `low` importance only for held or watched names. Held >
watched > others on the same day, then importance. The date kind
(confirmed / estimated / statutory deadline) is always shown.

## 10. Orchestrator

- Steps follow ROADMAP v1.2 section 3. Two deliberate deviations: the
  watchlist is ingested right after the screener and refreshed after the
  event calendar (so refresh sees the new events), and `calendar-portfolio`
  runs only when there are positions or a watchlist.
- Each step: separate process (`sys.executable`, cwd = the skill folder),
  timeout (45 min for steps that may fill a cold archive cache - first run
  ~20-40 min per gpw-market-breadth; 10 min for network steps; 2 min for
  JSON consumers), captured exit code, duration, the new JSON files it
  wrote, stderr tail on failure. Failures and timeouts never stop the run.
- `--state-root` passes `--state-dir` to breadth, ownership, watchlist and the signal
  ledger, so tests and dry runs do not touch the user's state.

## 11. Limitations

- The brief is as good as its inputs: a stale screener means stale VCPs; it
  says so but cannot fix it.
- Strengths across skills are not calibrated against each other.
- Closing prices are the last cached session; if the cache is older than
  the reports, positions show that session's date.
- Text inside source reports (macro watch items, calendar titles) keeps the
  language of the report.
- Insider clusters from notice titles are hints (`basis`); values need the
  user's pasted PDF in gpw-ownership.
