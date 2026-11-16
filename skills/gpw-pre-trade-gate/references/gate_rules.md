# GPW Pre-Trade Gate — rules, thresholds and inputs

Contents: 1. Philosophy · 2. Trade arithmetic · 3. Checks · 4. Verdict ·
5. Input contracts · 6. Limitations · 7. Context checks (1.1, v1.2 skills)

## 1. Philosophy

Adapted from the Pre-Trade Discipline Gate idea in tradermonty's
claude-trading-skills: most retail damage comes from process errors (no stop,
oversized positions, revenge trading after losses, buying into a falling
market), not from bad stock picks. The gate is **fail-closed**: required
information that is missing (stop, written thesis) is a FAIL. Optional
context that is missing (regime, exposure limits, target) is SKIP – neutral
but shown, so the user knows what was not checked.

Statuses: PASS · WARN (allowed with conditions) · FAIL (blocking) · SKIP.
Context checks (section 7) also use INFO (background, never changes the
verdict) and UNKNOWN (input missing; neutral unless the check is in
`--require`, then FAIL).

## 2. Trade arithmetic

- value = shares × entry; fee(order) = max(value × pct, min_fee)
  (`gpwlib.brokers`, presets mBank/XTB/Bossa/PKO BP/Santander or custom).
- risk = shares × (entry − stop) + fee(entry order) + fee(exit at stop) – the
  same definition as `gpw-position-sizer`, so a sizer result passes check 2 by
  construction.
- reward (net) = shares × (target − entry) − fee(entry) − fee(exit at target);
  R = reward / risk.
- A stop is not a guaranteed price: a gap through the stop (GPW static limit
  ±10% in continuous trading) can make the realised loss larger – the sizer
  shows that scenario.

## 3. Checks

| Check | Rule | Justification |
|---|---|---|
| stop | stop present and < entry | without a stop the risk (and every other check) is undefined (Van Tharp, *Trade Your Way to Financial Freedom*: "R" needs a defined exit) |
| risk | risk % ≤ limit; limit = 1% default, or gpw-exposure-coach `max_risk_per_trade_pct`; a user `--max-risk-pct` can only tighten the coach's limit | 1–2% fixed-fractional risk is the standard (Van Tharp; Elder's 2% rule); 1% leaves room for losing streaks |
| position | value ≤ 25% of account and value + fee ≤ account | single-stock gap risk; same 25% default as the sizer |
| liquidity | value ≤ 10% of the 20-session median daily turnover | 10% participation keeps market impact small; 20 sessions = sizer convention; zero turnover = FAIL |
| heat | (Σ open risk of ACTIVE journal positions at current prices + this trade) ≤ 6% of account | Elder's 6% rule (*Come Into My Trading Room*, 2002); open positions without a stop → WARN (heat understated); no journal → WARN (other positions invisible) |
| sector | (journal positions in the same GPW sector index + this trade) ≤ 35% of account | same as gpw-portfolio-check; unclassified (no sector index, ETFs) = PASS with note |
| thesis | journal thesis exists, status IDEA/READY/ACTIVE, same ticker, ≥ 1 kill criterion; WARN if gate stop differs > 0.5% from `plan.stop` | written, falsifiable thesis = the core of the journal (trader-memory-core, Anthropic thesis-tracker); a gate stop that differs from the plan means the plan is outdated |
| reward_risk | R ≥ 1.5 PASS, 1.0–1.5 WARN, < 1.0 FAIL; no target → SKIP | at a 40% win rate a 1.5R winner is break-even (0.4 × 1.5 − 0.6 × 1 = 0, R already net of fees), so lower R needs an unusually high hit rate; < 1R means risking more than the planned reward (Minervini: 2:1–3:1 preferred) |
| circuit_breaker | coach `circuit_breaker` if present (ok/reduce/pause → PASS/WARN/FAIL); else journal closed trades: ≥ 3 consecutive losses → WARN "halve size", ≥ 5 or realised drawdown ≥ 10% → FAIL "pause" | streaks raise the risk of tilt / revenge trading; halving after 3 losses and pausing after 5 or −10% are common prop-desk style rules |
| regime | gpw-market-regime `state`: confirmed_uptrend / uptrend_under_pressure / rally_attempt → PASS (WARN if top_risk ≥ 80); correction / bear → WARN; bear with top_risk ≥ 80 → FAIL unless `--override` | O'Neil (CAN SLIM "M"): ~3 of 4 stocks follow the market; buying new longs in a bear market fights the trend |
| exposure_ceiling | (journal positions value + this trade) ≤ coach `exposure_ceiling_pct` → else WARN | the coach's posture caps total equity exposure |
| new_positions | first buys in the last 7 calendar days (+1 unless adding to an ACTIVE thesis) ≤ coach `max_new_positions_per_week` → else WARN | pacing new entries in weak markets |
| data_freshness | WARN if: price data stale (`provenance.freshness`), entry > 10% from last close (likely typo; 10% = GPW static threshold), regime/exposure JSON as_of > 3 sessions old | stale inputs make every other check unreliable |

Drawdown reconstruction (journal mode): equity before closed trade k =
account − Σ net P&L of later trades (assumes no deposits/withdrawals);
drawdown = (peak − today) / peak.

## 4. Verdict

NO-GO if any FAIL; GO WITH CONDITIONS if any WARN; else GO. SKIP, INFO and
UNKNOWN do not change the verdict but SKIP / UNKNOWN lower confidence.
Sources of NO-GO: the 13 core checks; `event_window` under `--strict-events`;
a check listed in `--require` whose input is missing. The other context
checks can at most add conditions (WARN): they are context about the company
or the market, not personal risk rules, and their evidence is statistical
(averages over many stocks), so a hard block would overstate it. The exit code is 0 for every
verdict (the report is the product); 1 only for input/data errors.

### Override

`--override "reason"` (≥ 15 characters) turns only the regime FAIL
(bear + top risk ≥ 80) into WARN. The reason is stored in the JSON
(`override`) and quoted in the report. It does not lift any other FAIL –
risk, size, liquidity, heat, sector, thesis and circuit breaker are personal
risk rules, not market opinions.

## 5. Input contracts

- **gpw-position-sizer JSON** (`skill: gpw-position-sizer`): `inputs.ticker`,
  `inputs.account_size`, `entry`, `stop`, `shares`, `reward.target`,
  `account_type`, `broker.{key,pct,min_fee}`. Explicit CLI flags override.
- **gpw-market-regime JSON**: `skill`, `regime.state`
  (confirmed_uptrend | uptrend_under_pressure | rally_attempt | correction |
  bear), `regime.top_risk_score` (0–100), `regime.as_of`.
- **gpw-exposure-coach JSON**: `skill`, `posture`, `limits.exposure_ceiling_pct`,
  `limits.max_risk_per_trade_pct`, `limits.max_new_positions_per_week`,
  `limits.circuit_breaker` (ok | reduce | pause), `as_of`.
- Discovery (1.1): `gpwlib.reports.find_report` - an explicit `--<name>-json`
  path, else the report of that skill in `--input-dir` with the newest
  `as_of` (then mtime); reports dated after `--as-of` are ignored. For
  per-company inputs (events, ownership, earnings, quality, dividend) the
  newest report that covers the ticker is used when the newest one is about
  another company. An explicit path that is missing, corrupt or of another
  skill is an error (exit 1). Each input is listed in `inputs[]` with
  `as_of`, `sessions_behind`, its limit and `stale`.
- **Journal**: `gpwlib.journal_io` (read-only) – theses, ACTIVE positions
  (FIFO lots, plan stop), closed results (net P&L).

## 6. Limitations

- The gate sees only journal positions; holdings elsewhere are invisible to
  heat / sector / exposure checks.
- Current prices for heat come from the last session close.
- Broker tariffs are approximations entered on the date in `gpwlib.brokers`.
- Sector membership is the current GPW Benchmark portfolio.

## 7. Context checks (1.1) fed by the v1.2 skills

Engine: `scripts/pretrade_context_checks.py` (pure). Each check has `id`,
`status`, `reason`, `params` and `source {skill, file, as_of,
sessions_behind, stale}`; UNKNOWN adds `command` (the exact command that
produces the missing report). Matching is by ticker, issuer short name or
ISIN (ISIN from `gpwlib.fundamentals.resolve_isin` and from the reports
themselves): the KNF register and ESPI may still list a renamed company under
its old name (MODIVO = ISIN PLCCC0000016, formerly CCC).

### 7.1 Holding window

- Starts at the next session after the last data session (or the reference
  day itself if its session is not in the data yet); `--holding-days`
  sessions long (default **20** ≈ one month, the gpw-event-calendar window
  default and a typical swing holding period) plus a buffer of
  `--event-buffer-sessions` (default **5** = one trading week: exits slip and
  winners are held longer than planned). Sessions follow the Polish exchange
  calendar (`gpwlib.plcal`); an event on a non-session day counts as the
  session before it.

### 7.2 Thresholds and evidence

| Check | Rule | Justification |
|---|---|---|
| `event_window` | earnings (periodic report: confirmed date, estimate or statutory deadline) in the window or buffer → WARN; ex-dividend, record / payment date, AGM, MPC, GPW index revision, MSCI review, GUS release → INFO; nothing → PASS; calendar missing or not covering the ticker → UNKNOWN (a WARN is still raised if the earnings-reaction fallback date falls inside) | Results are the largest scheduled source of single-stock gaps; a stop is not executed at its price when the stock opens below it (GPW static limit ±10% only pauses trading). A statutory deadline (Dz.U. 2025 poz. 755 § 83) means "not later than", so it counts as inside the window when the deadline is. Ex-date: the reference price is cut by the DPS (KDPW T+2: ex-date = record date − 1 session), a mechanical drop that can trigger a stop placed closer than the DPS; index revisions and MSCI reviews move closing-auction volume (rebalancing flows). |
| `event_window` strict | `--strict-events` and the report in sessions 1-**2**: FAIL if the stock's typical earnings gap > stop distance, or if no gap history exists; WARN if the gap ≤ stop distance | Within 2 sessions the gap arrives before the plan can adapt. Typical gap = median \|opening gap\| on reaction days of reports published outside the session (pre-open / after close / non-session), from gpw-earnings-reaction `reactions[]` (duplicate reaction days counted once); intraday reports are used only when there are no others, because the stop can still fill near its price during the session. Median, not mean: few events per stock, one outlier would dominate. If the typical gap exceeds the stop distance, the planned 1R loss is likely exceeded, so risk (check 2) is understated. Unknown gap under strict mode fails closed (the user asked for strictness). |
| `crowded_short` | gpw-ownership `crowded` (total disclosed net short ≥ 2% or ≥ 3 holders) → WARN with total %, holders, 20-session change, days to cover | Heavily shorted stocks underperform on average (Desai et al. 2002; Boehmer, Jones & Zhang 2008; EU disclosures: Jones, Reed & Waller 2016 "Revealing Shorts"), but high days to cover means squeeze risk on good news. Both directions raise volatility around the entry, so it is a condition (size, stop), not a block. The register is a lower bound (positions ≥ 0.5% only). |
| `insider_selling` | `cluster_sell` (≥ 3 distinct insiders within 30 days, per gpw-ownership) with the last sell ≤ **60** days ago → WARN; older → PASS with note; `cluster_buy` → PASS with a positive note | Insider purchases are informative, sales much less so (Lakonishok & Lee 2001; Jeng, Metrick & Zeckhauser 2003; opportunistic vs routine trades: Cohen, Malloy & Pomorski 2012) - hence WARN, not FAIL. 60 days ≈ the horizon over which the abnormal returns of insider trades are concentrated in these studies; older clusters are known to the market. Counts with `basis: notice_hints` come from notice titles (details are in PDFs), which the report says. |
| `earnings_reaction` | latest reaction label `strong_negative` within **20** sessions → WARN; `strong_positive` → PASS with "do not assume continuation"; others / older → PASS | On the WSE post-earnings drift appears mainly after negative surprises (Jagliński 2020, https://journals.ue.wroc.pl/fins/article/view/233); drift accrues over roughly the first 20-60 sessions (Bernard & Thomas 1989), and gpw-earnings-reaction measures `drift_20s`. Costs eat much of the drift in illiquid names (Chordia et al. 2009), so it is used as an avoid / caution flag only. |
| `quality_red_flags` | any `red_flags[]` → WARN listing codes; `negative_equity` → WARN marked `prominent` (shown first, bold); data checks and stale periods are mentioned | Red flags (Sloan 1996 accruals; CFO < 0 < NI; leverage; dilution - Pontiff & Woodgate 2008) say "read the report", not "the stock will fall"; negative equity raises dilution / restructuring risk but some restructurings recover, so it stays a WARN. |
| `macro_context` | always INFO: composite (supportive / mixed / headwind) and the historical sector tilt whose `indices` contain the ticker's sector index | Macro is slow context over weeks to months (gpw-macro-regime), not a timing tool; sector tilts are historical tendencies. It never warns or blocks. |
| `stress_test` | gpw-risk-lab `stress.tolerance.ok == false` → WARN (portfolio already breaches the user's loss tolerance; the trade adds equity exposure); ok → PASS; no tolerance → INFO with the worst scenario | The user's stated maximum loss is a personal limit, like heat; the stress scenarios are the existing portfolio, so this is a condition, not a block. Optional input (tolerated when absent). |

### 7.3 Input age limits (`stale`)

| Input | Limit (sessions) | Why |
|---|---|---|
| gpw-market-regime, gpw-exposure-coach, gpw-event-calendar | 3 | cross-skill rule (CONVENTIONS); dates can be announced any day |
| gpw-ownership, gpw-earnings-reaction, gpw-risk-lab | 5 | weekly cadence in the ROADMAP workflow (one trading week) |
| gpw-macro-regime | 10 | weekly run + one week slack; slow context |
| gpw-dividend-analyzer | 22 | monthly run; ex-dates change with AGM resolutions |
| gpw-quality-screener | 65 | data change only with a new quarterly report (~65 sessions) |

A stale input keeps its check status but is marked in the check (`source.stale`)
and raises a `data_freshness` WARN, as in 1.0 for regime / exposure.

### 7.4 Limitations

- The calendar is only as good as ESPI: a company may publish before a
  statutory deadline or move a confirmed date.
- The typical gap comes from at most ~2 years of reports (≤ 8 events), often
  fewer: `earnings_gap.n` is shown.
- Insider clusters from notice titles are hints; values need the PDF text
  pasted into gpw-ownership.
- Stress results describe the portfolio as the risk-lab run saw it, without
  this trade.
