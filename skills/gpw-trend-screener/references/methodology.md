# GPW Trend Screener — methodology

Contents: 1. Data & universe · 2. Pre-filter · 3. RS rating · 4. Trend Template ·
5. VCP detection · 6. Execution state · 7. Composite score & bands ·
8. Invalidation levels · 9. Backtest (`--history`) · 10. Market context ·
11. Limitations · 12. Sources

## 1. Data & universe

- Daily quotes of every share in the GPW *Archiwum notowań* (type 10), PLN only,
  the last 500 sessions (~2 years) by default.
- Prices are dividend- and split-adjusted by `gpwlib.panel` (GPW's published
  % change vs the reference price reveals corporate actions; earlier prices are
  scaled backwards). So SMAs, 52-week highs and returns are not distorted by
  dividends or splits, and returns are total-return-like — comparable with WIG,
  which is itself a total-return index.
- The archive is a daily snapshot, so stocks that were later delisted are in the
  universe while they traded: the screen and the backtest are
  **survivorship-free**. Index/sector labels (WIG20, mWIG40, sWIG80, sector
  indices from GPW Benchmark) are *current* membership and are display-only;
  they never influence a result.
- All computations use the stock's own observations (a suspended day is simply
  missing), and only data up to the evaluation date (no look-ahead).

## 2. Pre-filter

| Rule | Default | Why |
|---|---|---|
| Median turnover of the last 20 sessions | ≥ 200 000 zł (`--min-turnover-k 200`) | Minervini/O'Neil setups need liquidity to enter at the pivot and exit at the stop. At 200k zł/day a 20k zł position is already 10% of a typical day — the `gpw-position-sizer` liquidity cap. Below that, closes are often single stale trades and breakouts are not tradable. Median (not mean) so one block trade does not qualify a stock. |
| Price | ≥ 1.00 zł | sub-1 zł "groszówki" move in very large % ticks; patterns are noise |
| History | ≥ 221 sessions | SMA200 plus 21 sessions to measure its slope |
| Full history | 252 sessions | 52-week high/low and 4-quarter RS. With 221–251 sessions the stock is screened using the available data and flagged `short_history` |

Stocks failing the pre-filter are excluded from the RS ranking and the screen.
`--ticker` still shows their full breakdown (with the RS rating computed against
the eligible universe) for information.

## 3. RS rating (IBD-style)

Raw score = **2·Q1 + Q2 + Q3 + Q4**, where Q1 is the % return over the most
recent 63 sessions (one quarter of a 252-session year), Q2 the quarter before,
etc. Doubling the latest quarter is the published approximation of IBD's
Relative Strength Rating (O'Neil, *How to Make Money in Stocks*; IBD
methodology) and is what both reference skills implement. With only 2–3 complete
quarters (recent IPOs) the available quarters are used and rescaled to the same
total weight (×5 / sum of weights) instead of being treated as zero.

The rating is the percentile rank of the raw score among all eligible stocks
on that session (ties count half), rounded and clipped to **1–99** — the IBD
scale. A rating of 80 means the stock outperformed ~80% of liquid GPW shares
over the weighted year.

Also reported: **RS vs WIG** = stock return − WIG return over 63/126/252
sessions (percentage points, same dates; WIG from the GPW index archive).
Positive = beating the broad market.

## 4. Trend Template (Minervini)

Mark Minervini, *Trade Like a Stock Market Wizard* (2013), ch. 5 — the stock must
be in a confirmed Stage 2 uptrend. All 8 criteria by default (`--min-criteria 7`
gives a looser watchlist):

| # | Criterion | Note |
|---|---|---|
| 1 | close > SMA150 and close > SMA200 | |
| 2 | SMA150 > SMA200 | |
| 3 | SMA200 rising: SMA200 today > SMA200 21 sessions ago | "trending up for at least 1 month" (21 sessions); slope reported in % |
| 4 | SMA50 > SMA150 and SMA50 > SMA200 | |
| 5 | close > SMA50 | |
| 6 | close ≥ 1.30 × 52-week low | "at least 30% above its 52-week low" |
| 7 | close ≥ 0.75 × 52-week high | "within at least 25% of its 52-week high" |
| 8 | RS rating ≥ 70 | Minervini: "no less than 70, preferably in the 80s or 90s" (`--min-rs`) |

The 52-week high/low use the adjusted intraday high/low of the last 252
sessions (fewer if history is shorter). Simple moving averages of adjusted
closes are precomputed once per stock.

## 5. VCP detection

Minervini, ch. 10: a volatility contraction pattern is a base in which each
pullback is smaller than the previous one ("T"s of e.g. 25% → 15% → 8% → 4%),
with turnover drying up, until supply is exhausted and the price breaks the
**pivot** — the high of the last, tightest contraction.

### 5.1 Swing detection (fractals)

Over the last `vcp_lookback` = **120 sessions** (~6 months; Minervini's VCPs
last 3 to 65 weeks — longer bases are out of scope):

- bar *k* is a **swing high** if its high is strictly above the highs of the
  previous **5** sessions and not below the highs of the next **3** sessions;
- a **swing low** mirrors this with lows; a bar that qualifies as both
  (outside bar) is treated as a high;
- the left side (5 sessions = one trading week) makes the point a meaningful
  local extreme; the short right side (3 sessions) keeps the latest pivot fresh
  — a pivot is known 3 sessions after it formed, not a week later;
- only swings confirmed by data up to the evaluation date are used (no
  look-ahead); the sequence is reduced to an alternating zigzag (of two
  consecutive highs the higher survives, of two lows the lower).

### 5.2 Contractions

1. The base starts at the **highest swing high** in the window (later highs
   are by construction at or below it).
2. Every swing high from there starts a contraction; its low is the lowest low
   before the next swing high. The last contraction's low is the lowest low from
   its high up to today (it may still be forming).
3. **Noise merge:** a contraction shallower than **2%** (except the last) is a
   wobble inside a move, not a pullback; of the two highs around it the lower
   is dropped and the contractions are recomputed.
4. The pattern is the **longest run of strictly shallower contractions ending
   with the latest one**, capped at the last **5**.

Depth = (high − low) / high of each contraction.

### 5.3 Validity

| Rule | Threshold | Why |
|---|---|---|
| contractions | ≥ 2 (≤ 5 counted) | a single pullback is not a contraction *sequence*; Minervini typically shows 2–6 |
| first contraction depth | ≤ 40% | O'Neil: bases deeper than ~33–40% (more in bear markets) fail more often; Minervini's first T is usually 10–35% |
| last contraction depth | ≤ 12% | the final T must be tight (Minervini's examples: 3–10%) — supply is exhausted |
| base length | ≥ 15 sessions | ~3 weeks, the shortest VCP Minervini describes; a fresh high with one dip is a pullback, not a base |
| pivot vs base high | pivot ≥ 85% of the base high | the right side must recover most of the left side; a pivot far below the base high is a lower-high series |

Also reported (not required): **higher lows** (each contraction low ≥ the
previous one) and **turnover dry-up** = median turnover of the last 10
sessions / median of the last 50 (O'Neil's 50-day average volume). Below 1
means turnover is shrinking, the volume signature of a VCP.

### 5.4 Contraction quality (0–100)

Average of four sub-scores: count (2 → 60, 3 → 85, 4–5 → 100); last depth
(3% → 100, 6% → 90, 9% → 70, 12% → 45); progression last/first depth (0.2 →
100, 0.4 → 85, 0.6 → 60, 0.9 → 30); higher lows (100, else 50).
Piecewise-linear between the points.

## 6. Execution state (checked before the rating)

Evaluated in this order:

| State | Rule | Meaning |
|---|---|---|
| `invalid` | no SMA50 data | cannot judge |
| `damaged` | close < SMA50, or close < last contraction low | the setup failed (Minervini: a VCP that undercuts its last low is broken) |
| `extended` | close > 25% above SMA50 | too far from support; mean-reversion risk — wait for a new base |
| `invalid` | no valid VCP | strong trend but nothing to trade against |
| `extended` | close > 5% above the pivot | O'Neil: do not buy more than 5% past the pivot |
| `breakout` | close above the pivot by ≤ 5% and today's turnover ≥ 1.4 × 50-session median | O'Neil: breakout volume at least 40–50% above average |
| `breakout_unconfirmed` | above the pivot by ≤ 5%, turnover below 1.4× | price broke out without demand — lower odds |
| `pre_breakout` | 0–5% below the pivot | the actionable watch zone — set an alert at the pivot |
| `forming` | more than 5% below the pivot (still above the last low) | base still building |

`forming` and `breakout_unconfirmed` are additions to the five states of the
brief; they keep "near the pivot" and "confirmed by volume" honest.

## 7. Composite score & bands

| Component | Weight | Mapping |
|---|---|---|
| Trend Template strength | 25 | 60% × share of criteria met + 40% × proximity to the 52-week high (0% → 100, −5% → 90, −15% → 50, −25% → 0) |
| Relative strength | 25 | RS rating 50 → 0, 70 → 40, 80 → 65, 90 → 90, 99 → 100 |
| Contraction quality | 20 | §5.4; 0 without a valid VCP |
| Volume dry-up | 10 | ratio 0.5 → 100, 0.7 → 80, 1.0 → 30, 1.3 → 0 |
| Distance to pivot | 10 | −12% → 0, −5% → 60, −2…0% → 100, +2% → 90, +5% → 50, +10% → 0 |
| Liquidity | 10 | median turnover 200k → 30, 500k → 60, 2 mln → 90, 5 mln → 100 zł |

Weights follow the order of importance in Minervini's SEPA (trend and
leadership first, then the pattern, then volume and entry timing) and in the
VCP screener of tradermonty/claude-trading-skills; liquidity is added for GPW,
where most listed shares trade thinly.

**Bands (watchlist priority, never a buy call):** 90+ textbook · 80–89 strong ·
70–79 good · 60–69 watchlist · < 60 skip. The state caps the band: `damaged`
and `invalid` → skip; `extended`, `forming`, `breakout_unconfirmed` → at most
watchlist. The table lists Trend Template passers with band ≥ watchlist; the
rest are listed by name as "trend OK, no usable setup".

## 8. Invalidation levels

- **Structure:** 0.5% below the last contraction low — if price undercuts the
  low that defined the tightest contraction, the pattern is broken. The small
  buffer avoids sitting exactly on an obvious level.
- **ATR alternative:** pivot − 2 × ATR(14) (Wilder), the same default as
  `gpw-position-sizer --atr-multiplier 2`.
- **Risk %** = (pivot − stop) / pivot. Above **8%** it is flagged as wide:
  O'Neil's rule is to cut every loss at 7–8%, Minervini keeps the average loss
  well below that. A wide structural stop means wait for a tighter setup or size
  down.
- A ready `gpw-position-sizer` command uses the pivot as entry and the
  structural stop (`--account-size` and `--risk-pct` are passed through when
  given, otherwise `ACCOUNT_PLN` is a placeholder the user must replace).

## 9. Backtest (`--history`)

- The screen is re-run every `--history-step` = 5 sessions over the last
  `--history-sessions` = 250 sessions, starting no earlier than session 252 of
  the loaded data (needs a full year for RS and 52-week stats), and only on
  dates with at least 20 forward sessions. Everything (RS ranks, SMAs, swings,
  eligibility) is point-in-time.
- Groups: **flagged** (state `pre_breakout` or `breakout` and band ≥
  watchlist), **all Trend Template passers**, and the **whole eligible
  universe** as the baseline.
- Forward return = close on the screen date → close 20 and 60 sessions later
  (the last available close if the stock stopped trading; the count is
  reported). Excess = minus WIG over the same dates. Reported: count, hit rate
  (> 0), mean, median, mean/median excess, share beating WIG.
- Caveats printed with every result: small and overlapping sample (signals
  every 5 sessions with 20/60-session horizons), one market regime,
  no costs/slippage/stops, entry at the close rather than on a pivot break.
  The current-constituent bias does not apply — the archive is survivorship-free.

## 10. Market context

O'Neil (*How to Make Money in Stocks*): three of four stocks follow the general
market, and breakouts fail far more often in corrections. The report always
tells the user to check `gpw-market-regime` and `gpw-market-breadth`. If a
`gpw-market-regime` JSON is given (`--regime-json`) or found in `--input-dir`
(newest `gpw_*regime*.json` with `"skill": "gpw-market-regime"`), its state is
shown; `correction`/`bear` add a prominent warning, `uptrend_under_pressure` /
`rally_attempt` a softer one, and a file more than 3 sessions older than the
screen data is flagged. Without the file the screen still runs (lower
confidence). The regime never changes scores — it is context.

## 11. Limitations

- Pattern recognition is mechanical: fractal swings catch the classic shape but
  not every nuance a chartist sees (shakeouts, cheat entries, handles).
  `--ticker` lists every contraction with dates so the user can check the chart.
- Daily data only; no intraday volume at the breakout.
- GPW's universe is small (~110–130 shares pass 200k zł/day), so the RS
  percentile is coarser than IBD's ranking of thousands of US stocks.
- No fundamentals (Minervini's SEPA also requires earnings/sales growth) —
  check results and reports separately.
- The backtest covers one year in one regime; it is a sanity check, not proof
  of an edge.

## 12. Sources

- M. Minervini, *Trade Like a Stock Market Wizard*, McGraw-Hill 2013 (Trend
  Template ch. 5, VCP ch. 10); *Think & Trade Like a Champion*, 2017 (stops,
  risk).
- W. J. O'Neil, *How to Make Money in Stocks*, 4th ed., McGraw-Hill 2009
  (RS rating, base depth, breakout volume +40–50%, 5% chase rule, 7–8% loss
  rule, market direction).
- tradermonty/claude-trading-skills — VCP Screener (MIT); himself65/finance-skills
  — sepa-strategy (structure of the screen this skill adapts to GPW).
- GPW Archiwum notowań: https://www.gpw.pl/archiwum-notowan ·
  GPW Benchmark: https://gpwbenchmark.pl
