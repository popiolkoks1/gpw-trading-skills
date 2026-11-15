# GPW Exposure Coach — methodology

Contents: 1. Inputs · 2. Market score · 3. Posture and exposure ceiling ·
4. Regime hard caps · 5. Risk per trade and pacing · 6. Circuit breaker ·
7. Portfolio heat · 8. Confidence and staleness · 9. Zero inputs ·
10. Limitations

Derived from tradermonty/claude-trading-skills (MIT) *Exposure Coach* and
*Drawdown Circuit Breaker*, adapted to the GPW skill set. The coach computes
**limits for a human decision**; it never names a stock and never says buy
or sell.

## 1. Inputs

| Input | File (newest in `--input-dir`) or flag | Fields used |
|---|---|---|
| Market regime | `gpw_market_regime_*.json` / `--regime-json` | `regime.state`, `regime.top_risk_score`, `regime.distribution_days`, `regime.as_of` |
| Market breadth | `gpw_breadth_*.json` / `--breadth-json` | `composite.score`, `composite.zone`, `data_provenance.as_of` |
| Sector trend | `gpw_sector_trend_*.json` / `--sector-json` | `composite.score`, `composite.zone`, `composite.warnings`, `data_provenance.as_of` |
| Trade journal | `--state-dir` (gpw-trade-journal, read-only via `gpwlib.journal_io`) | closed results (net P&L, R), open positions (shares, avg price, stop) |
| Last closes | GPW archive cache, read-only, never downloads | close of open positions on the latest cached session |

"Newest" = last file name in sort order (the timestamp in the name). A file
with the wrong `skill`, without a score or without `as_of` is skipped with a
warning (or is an error, exit 1, when passed explicitly). A file whose
`as_of` is after `--as-of` is ignored (no look-ahead).

## 2. Market score (0–100, higher = more favourable)

`score = Σ weight_i × score_i` over the available inputs, weights
renormalised to sum to 1:

| Input | Weight | Score | Why this weight |
|---|---|---|---|
| Regime | 40% | 0.6 × state score + 0.4 × (100 − top risk) | The index's own price/volume behaviour is O'Neil's "M": three of four stocks follow the market direction. Largest weight. |
| Breadth | 35% | `composite.score` | Participation of all ~180 liquid shares; catches narrow rallies the index hides. |
| Sector trend | 25% | `composite.score` | Leadership and rotation; computed from the same share universe as breadth, so partly redundant → smallest weight. |

State scores (midpoints of IBD's suggested exposure for each market status,
see section 4): confirmed uptrend 85, under pressure 55, rally attempt 35,
correction 20, bear 5. Inside the regime input the state counts 60% (a
confirmed fact) and the inverted top-risk score 40% (a leading warning).

## 3. Posture and exposure ceiling

| Score | Posture | Ceiling from score |
|---|---|---|
| ≥ 70 | offensive | 85% at 70 → 100% at 85+ |
| 50–69 | normal | 60% at 50 → 85% at 70 |
| 30–49 | defensive | 30% at 30 → 60% at 50 |
| < 30 | capital_preservation | 10% at 0 → 30% at 30 |

Ceilings are interpolated linearly and rounded to 5%. They are anchored to
the `gpw-market-breadth` zones (critical 25–40%, weakening 40–60%, neutral
60–75%, healthy 75–90%, strong 90–100%) and the `gpw-sector-trend` zones,
slightly more conservative at the bottom because the coach also knows the
regime. Equal 20-point posture bands around the neutral 50 keep the mapping
easy to explain.

## 4. Regime hard caps

The weighted score can be high while the index is already breaking down
(breadth and sector reports lag at turns). The regime therefore acts as a
**hard cap**, applied after the score:

| Regime state | Max exposure | Best allowed posture | Source |
|---|---|---|---|
| confirmed_uptrend | 100% | offensive | IBD: 80–100% invested in a confirmed uptrend |
| uptrend_under_pressure | 80% | normal | IBD: 40–80% when under pressure |
| rally_attempt | 40% | defensive | still a correction until an FTD; pilot positions only once the FTD window opens |
| correction | 25% | defensive | IBD: 0–20% in a correction (+5 pp for long-term holdings on GPW, where many investors hold IKE/IKZE core positions) |
| bear | 15% | capital_preservation | below the correction cap: structural downtrend |

Top-risk caps (many warnings at once while the trend still looks fine):
top risk ≥ 70 → max 60%; ≥ 85 → max 40%.

Final posture = the more defensive of the score posture and the regime's
best allowed posture. Final ceiling = min(score ceiling, regime cap,
top-risk cap). Every cap that changed something is listed in
`caps_applied`.

## 5. Risk per trade and pacing

`max_risk_per_trade_pct = base risk × posture multiplier`, base default
**1%** (`--base-risk-pct`; O'Neil/Minervini: risk 0.5–1.25% per trade, Van
Tharp: ~1% for most traders, Elder's 2% rule is the absolute maximum).
Multipliers: offensive 1.0, normal 0.75, defensive 0.5, capital
preservation 0.25. Rationale: a weak market lowers the win rate of breakouts,
so the same stop distance should cost less of the account.

`max_new_positions_per_week`: offensive 5, normal 3, defensive 1, capital
preservation 0. Pacing prevents a burst of entries at a bad moment; the
~180 liquid GPW shares (breadth universe) rarely offer more than a handful
of valid setups per week. It is a weekly maximum, not a target.

## 6. Circuit breaker (journal)

Inputs: `journal_io.closed_results()` (oldest first) — net P&L in PLN and R
multiple per closed trade. Paper trades are excluded unless
`--include-paper`.

* **Losing streak:** consecutive closed trades with net P&L < 0 counted from
  the most recent one (falls back to R < 0 when P&L is missing). A win or a
  break-even trade ends the streak.
* **Drawdown:** peak-to-current fall of the cumulative closed P&L (and of the
  cumulative R). With `--account-size` (= current account value) the
  drawdown in % is `drawdown_pln / (account_size + drawdown_pln)`, i.e.
  measured from peak equity. Without an account size, R units are used.

| Condition | Status | Effect |
|---|---|---|
| none | ok | — |
| 3 losses in a row, or drawdown ≥ 5% (≥ 5R without account size) | reduce | risk per trade × 0.5, new positions halved (min 1 if the posture allows any) |
| 5 losses in a row, or drawdown ≥ 10% (≥ 10R) | pause | risk per trade 0, new positions 0 until `pause_until` |

Why these levels: with a 40–50% win rate, 3 losses in a row are common
(0.6³ ≈ 22% of any 3-trade window), so halving risk is a cheap precaution
that limits the damage of a cold streak. 5 in a row (0.6⁵ ≈ 8%) is unusual
enough to suggest a mismatch between the method and the market, or an
execution problem. 5% / 10% of the account mirror the "reduce" / "stop"
levels of prop-trading risk desks and Elder's 6% monthly stop rule; at 1%
risk per trade, 5R and 10R are the same thresholds.

**Pause length:** `--pause-days` (default 10 calendar days ≈ two trading
weeks) counted from the last closed trade. It is time to review the journal
(`gpw-trade-review`), not a punishment. When the pause has elapsed the
status becomes `reduce` (reason `pause_elapsed`) until a winning trade ends
the streak or the drawdown falls below the thresholds.

The circuit breaker overrides risk per trade and new positions; it does not
change the posture (which describes the **market**) and never tells the user
to sell existing positions.

## 7. Portfolio heat (open risk)

For each open position (`journal_io.open_positions()`):
`risk = shares × max(0, price − stop)`, where price is the last cached close
(or the average entry if the ticker is not in the cache). This is the money
that would be lost — including open profit given back — if every stop were
hit. A stop at or above the price contributes 0. A position without a stop
is listed as unmeasurable (`no_stop`) — a warning in itself.

Heat cap by posture (% of account): offensive 8, normal 6, defensive 4,
capital preservation 2 — Elder's "6% rule" (*The New Trading for a Living*,
2014) for the normal posture, more room when offensive, less when
defensive. If heat ≥ cap, or current exposure ≥ the exposure ceiling, new
positions this week are set to 0 (`heat_full`, `above_ceiling`). Requires
`--account-size`; without it heat is reported in PLN only.

## 8. Confidence and staleness

Each input's `as_of` is compared with today's Warsaw date (or `--as-of`):
more than **3 sessions** behind = stale (warning at the top of the report).
`confidence = Σ weight_i × (1 if fresh, 0.5 if stale)` over available
inputs: ≥ 0.8 high, ≥ 0.5 medium, below low. Example: all three fresh = 1.0;
regime missing = 0.6 (medium); only a stale sector report = 0.125 (low).
Inputs more than 3 sessions apart from each other are noted in
`data_provenance.notes`.

## 9. Zero inputs

Without any market input the coach **exits with code 1** and a stderr
message explaining which skills to run — it does not produce a posture from
the journal alone, because the contract's posture and exposure ceiling are
market judgements. (Choice documented here; a journal-only report would
invite users to treat an "ok" circuit breaker as permission to trade.)

## 10. Limitations

* The weights and caps are judgement calls grounded in IBD guidance and
  common risk-management practice, not optimised on GPW data (two years of
  history are too few for that).
* Breadth and sector reports use current index membership (not
  point-in-time).
* Heat uses the last cached close, which may be a session or more old.
* The journal must be kept up to date; stale or missing stops make heat
  unreliable.
