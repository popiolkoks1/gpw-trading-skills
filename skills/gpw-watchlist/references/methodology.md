# gpw-watchlist methodology

The watchlist is the bridge between "a skill found it" and "I bought it".
It keeps one record per ticker and moves that record through stages. It
also checks every followed name against the newest quotes and the event
calendar. The script does all the computing. Claude explains the result
(CONVENTIONS rule 1).

## 1. Why stages

Practitioner sources agree on one point: idea generation and execution are
separate steps.

- Minervini (2013, *Trade Like a Stock Market Wizard*, ch. 10–12) keeps a
  focus list of names in a proper base. He acts only near a pre-defined
  pivot, with the stop decided **before** entry.
- O'Neil (*How to Make Money in Stocks*, 4th ed. 2009) gives the buy rule
  that defines "ready": buy at the pivot, at most 5 % above it.

Behavioural research explains why the plan is written down first:

- Implementation intentions ("if price X, then I do Y", Gollwitzer 1999)
  raise follow-through.
- A stop set in advance limits the disposition effect, i.e. holding losers
  too long (Shefrin & Statman 1985; Odean 1998).

| stage | meaning | how it is entered |
|---|---|---|
| `candidate` | raw idea | `add`, or ingest from a screener or an insider cluster |
| `watch` | setup forming, worth daily attention | user, or ingest when the screener priority is high |
| `ready` | written plan: pivot, stop, ideally a journal thesis | **user only**, needs pivot and stop with stop < pivot |
| `entered` | position open | automatic when a gpw-trade-journal thesis is ACTIVE |
| `dropped` | no longer followed (history kept) | user, with a reason |

Nothing automatic moves an item to `ready`. That step is a human decision,
and the skill never recommends buying.

The transition table and its preconditions are in
`references/watchlist-contract.md`.

## 2. Ingest rules (configurable)

A report is turned into facts in two ways:

- From the generic `signals[]` array (contract in
  skills/gpw-signal-postmortem/references/signal-contract.md).
- From the producer's ROADMAP section-4 contract. This covers
  gpw-trend-screener, and older reports that have no `signals[]`.

| fact kind | producer | action | why |
|---|---|---|---|
| `screener_watchlist_high` | trend-screener, band good/strong/textbook (score ≥ 70) | add as **watch**, candidate → watch, tag `vcp`, copy pivot + structural stop | The screener's own priority bands (trend-screener methodology §7). |
| `screener_watchlist` | trend-screener, band "watchlist" (60–69) | add as candidate, tag `vcp`, copy levels | A weaker setup is worth recording but not daily focus. |
| `screener_candidate` | Trend Template passers outside the watchlist (not damaged) | add as candidate, tag `trend_template` | Stage-2 stocks to watch for a base (Minervini 2013, ch. 5). |
| `screener_damaged` | trend-screener state `damaged` | alert **high** + tag on existing items | A VCP that undercuts its last low is broken (Minervini). |
| `quality_high` / `quality_red_flags` | quality-screener | tag; red flags also alert medium | Piotroski 2000 F-score and accrual red flags. These are context, not timing. |
| `insider_cluster_buy` | ownership | add candidate + tag | Purchases by several insiders carry the information (Lakonishok & Lee 2001; Cohen, Malloy & Pomorski 2012). |
| `insider_cluster_sell` | ownership | tag + alert medium | Selling is weaker evidence (it has many non-information motives), so it warns but does not drop. |
| `short_crowded` | ownership (KNF register) | warning tag | Crowded shorts mean squeeze and borrow risk. This is not a direction call. |
| `short_new` | ownership | alert low | A new disclosed position is information to check. |
| `fund_accumulation` | ownership funds | tag | Annual OFE data. Slow context only. |
| `earnings_strong_negative` | earnings-reaction | alert **high** + warning tag | On the WSE the post-announcement drift appears mainly after negative surprises (Jagliński 2020), which makes this an exit/avoid warning. |
| `earnings_strong_positive` | earnings-reaction | tag | |
| `dividend_cut_risk` | dividend-analyzer | warning tag + alert low | A yield trap matters for income ideas. |
| `pair_dislocation` | pair-trading (cheap leg) | tag | Relative-value context (Gatev, Goetzmann & Rouwenhorst 2006). |

The rules that may **add** a ticker are the three screener kinds (all
`long`) and `insider_cluster_buy`. Every other kind only annotates items
that are already on the list. `avoid` and `exit` signals never add a stock.

**Override.** `--rules FILE` takes a JSON object
`{kind: {tag, add, upgrade_to, levels, alert{code, severity}, max_fact_age_sessions, risk} | null}`.
`null` disables a kind. `upgrade_to` can only be `watch`, and `add` can only
be `candidate` or `watch`, so no rules file can produce `ready`.

**Age limits.**

- Facts older than 20 sessions relative to their report's `as_of` are
  ignored. Twenty sessions is the primary horizon of gpw-signal-postmortem,
  the period over which these signals are judged.
- Reports older than the per-producer limit are skipped with a warning:
  - trend-screener: 3 sessions (cross-skill freshness rule)
  - ownership, earnings, pairs: 5 sessions (weekly cadence)
  - quality, dividend: 10 sessions (fundamental data change quarterly)
  - ownership funds: 300 sessions (annual OFE reports)
- Ingest alerts expire 10 sessions (two trading weeks) after their date. The
  evidence stays in `sources[]`.

**Tag lifecycle.** An automatic tag is cleared when a newer report of the
same producer and mode covers the ticker and no longer contains the fact.
Tags therefore describe the newest evidence.

Coverage is defined per producer:

- `companies[]` for quality and dividend reports
- `pairs[]` legs for pair trading
- the whole market for the screener, the KNF short register and insider notices

**Levels from the screener.**

- The pivot is `vcp.pivot` and the stop is `risk.stop_structure` (just
  below the last contraction low).
- They are copied only while the user has not set levels and the item is
  candidate or watch. A ready plan is frozen.

**Idempotency.** Sources are deduplicated by `(skill, kind, date)`, so
re-ingesting the same reports changes nothing.

A ticker the user dropped is **not** re-opened. The new evidence is recorded
(`seen_while_dropped`), and re-adding is the user's call.

## 3. Refresh thresholds

Prices come from `gpwlib.panel.build_panel` (dividend/split adjusted; the
newest close equals the traded price). The window is 252 + 15 sessions.

| check | rule | source |
|---|---|---|
| buy range | 0 % < close vs pivot ≤ **5 %** | O'Neil, Minervini: do not chase beyond 5 % |
| extended | > 5 % above pivot | as above; for a `ready` item the entry has passed, so review suggests demoting it to watch |
| near pivot | within **5 %** below the pivot | trend-screener `pre_breakout_pct` |
| pivot breakout | previous close ≤ pivot < close | the close basis avoids intraday noise |
| volume confirmation | turnover ≥ **1.4×** the median of the previous **50** sessions | O'Neil: breakout volume at least 40–50 % above average. GPW publishes turnover in PLN, not shares. Same values as trend-screener `breakout_vol_mult` / `dryup_long`. The median is robust to block trades. |
| stop breached | close ≤ stop | the setup is invalidated. For `entered` items the journal plan stop is used. |
| near stop | close − stop ≤ **0.5 × ATR(14)** (Wilder 1978) | an ordinary day's range can reach the stop |
| new 52-week high | today's high > highest high of the previous 251 sessions | leaders make new highs (O'Neil "N") |
| levels after a corporate action | a panel adjustment dated after `levels_date` | the pivot/stop were set in pre-dividend or pre-split prices |
| events | company events in the next **10** sessions from the newest gpw-event-calendar reports (all modes, ≤ 3 sessions old, deduplicated by id) | two trading weeks: the usual window between "ready" and the first days of a trade |
| stale quotes | last quote more than 3 sessions old | suspension or delisting |

A severity orders the attention:

- `high`: decide now
- `medium`: look soon
- `low`: for information

## 4. Review

`review` never changes the state. It lists suggestions together with the
exact command to apply each one.

| suggestion | condition |
|---|---|
| drop: `invalidated` | close ≤ stop (candidate, watch or ready) |
| drop: `setup_damaged` | the screener reported the setup damaged |
| drop: `stale` | no update and no new evidence for **30 days**, the same default review period as gpw-trade-journal |
| drop: `risk_alert` | a high-severity ingest alert on a candidate or watch item |
| demote to watch: `extended` | a ready item more than 5 % above its pivot |
| write a thesis | a ready item without a journal thesis |
| refresh | a ready item without quotes from the last 3 sessions |
| set levels | a watch item without a pivot and stop |
| demote/drop: `position_closed` | an entered item whose thesis is CLOSED or INVALIDATED |
| check journal: `stop_breached` | an entered item below its stop |

## 5. Limitations

- Pivot and stop are unadjusted price levels. After a dividend or split
  they need a manual update, which `levels_pre_adjustment` flags.
- Turnover is in PLN. A large block trade inflates the ratio, which is why
  the median is used.
- Events are only as complete as the newest gpw-event-calendar report. Run
  it for the watchlist tickers (`portfolio` mode reads this state file).
- One item per ticker. Two setups in the same stock share one record. The
  notes and the journal keep the detail.
- The watchlist emits no `signals[]`. Stage changes are user decisions, and
  gpw-signal-postmortem evaluates the producers instead.

## References

- Cohen, L., Malloy, C., Pomorski, L. (2012). Decoding Inside Information. *Journal of Finance* 67(3).
- Gatev, E., Goetzmann, W., Rouwenhorst, K. G. (2006). Pairs Trading. *Review of Financial Studies* 19(3).
- Gollwitzer, P. M. (1999). Implementation intentions. *American Psychologist* 54(7).
- Jagliński, T. (2020). Post-earnings-announcement drift on the Warsaw Stock Exchange (as summarised in docs/ROADMAP_v1.2.md).
- Lakonishok, J., Lee, I. (2001). Are Insider Trades Informative? *Review of Financial Studies* 14(1).
- Minervini, M. (2013). *Trade Like a Stock Market Wizard*. McGraw-Hill.
- Odean, T. (1998). Are Investors Reluctant to Realize Their Losses? *Journal of Finance* 53(5).
- O'Neil, W. J. (2009). *How to Make Money in Stocks*, 4th ed. McGraw-Hill.
- Piotroski, J. (2000). Value Investing: The Use of Historical Financial Statement Information. *Journal of Accounting Research* 38.
- Shefrin, H., Statman, M. (1985). The Disposition to Sell Winners Too Early and Ride Losers Too Long. *Journal of Finance* 40(3).
- Wilder, J. W. (1978). *New Concepts in Technical Trading Systems*.
