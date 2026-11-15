# GPW Pre-Trade Gate — rules, thresholds and inputs

Contents: 1. Philosophy · 2. Trade arithmetic · 3. Checks · 4. Verdict ·
5. Input contracts · 6. Limitations

## 1. Philosophy

Adapted from the Pre-Trade Discipline Gate idea in tradermonty's
claude-trading-skills: most retail damage comes from process errors (no stop,
oversized positions, revenge trading after losses, buying into a falling
market), not from bad stock picks. The gate is **fail-closed**: required
information that is missing (stop, written thesis) is a FAIL. Optional
context that is missing (regime, exposure limits, target) is SKIP – neutral
but shown, so the user knows what was not checked.

Statuses: PASS · WARN (allowed with conditions) · FAIL (blocking) · SKIP.

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

NO-GO if any FAIL; GO WITH CONDITIONS if any WARN; else GO. SKIPs do not
change the verdict but lower confidence. The exit code is 0 for every
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
- Auto-discovery: newest `gpw_*.json` (by mtime) in `--input-dir` whose
  `skill` matches; an explicit path with the wrong `skill` is an error.
- **Journal**: `gpwlib.journal_io` (read-only) – theses, ACTIVE positions
  (FIFO lots, plan stop), closed results (net P&L).

## 6. Limitations

- The gate sees only journal positions; holdings elsewhere are invisible to
  heat / sector / exposure checks.
- Current prices for heat come from the last session close.
- Broker tariffs are approximations entered on the date in `gpwlib.brokers`.
- Sector membership is the current GPW Benchmark portfolio.
