# GPW Market Breadth — methodology

Contents: 1. Data · 2. Universe · 3. Daily series · 4. Components ·
5. Composite & zones · 6. Limitations · 7. Differences from the US original

## 1. Data

| Data | Source | Notes |
|---|---|---|
| Daily OHLC + turnover of every share | GPW *Archiwum notowań*, `type=10` | One request per session, cached. Unadjusted prices. |
| Daily index levels (WIG, WIG20, ...) | GPW *Archiwum notowań*, `type=1` | For divergence. |
| WIG20 / mWIG40 / sWIG80 portfolios | GPW Benchmark `ajaxPortfolio` | Current membership only. |

Prices are adjusted for dividends, splits and other corporate actions by
comparing the raw close change with the % change GPW publishes against its
reference price (which GPW cuts on ex-dates). A mismatch above 0.5% is
treated as a corporate action and earlier prices are rescaled
(gpwlib/panel.py). Empirically (320 sessions, ~128k share-days) 99.8% of
mismatches are <0.1% rounding noise; 1-3% mismatches are ordinary dividends.

## 2. Universe (per session)

A share counts on a session when it traded that day, has ≥200 sessions of
history (needed for SMA200), closes ≥ 1.00 zł and has a 20-session median
turnover ≥ 50 000 zł.

- **≥ 1 zł**: sub-złoty shares ("groszówki") move in large percentage ticks
  and are dominated by a few speculative names.
- **≥ 50k zł median turnover**: illiquid shares often print stale single
  trades; including them adds noise, not information. At ~50k zł/day roughly
  the liquid half of the main market remains (typically 150-250 shares).
  Use `--min-turnover-k` to test sensitivity.
- Delisted companies are included while they traded → no survivorship bias.

## 3. Daily series

- `pct_above_200/50/20`: share of the universe closing above its SMA.
- **Fast line** = EMA(8) of `pct_above_200`; **slow line** = EMA(100).
  The US original uses EMA(200) on 10 years of data; with ~2 years from the
  archive, EMA(100) (~5 months) keeps enough history to see turns.
- Advancers / decliners: close vs previous close, ±0.05% dead band (avoids
  counting rounding as moves).
- **McClellan oscillator** on ratio-adjusted net advances:
  RANA = 1000·(A−D)/(A+D); oscillator = EMA19(RANA) − EMA39(RANA). The
  ratio-adjusted form makes it comparable as the universe size changes.
- New highs / lows: close above the highest / below the lowest close of the
  previous 250 sessions (120 when history is short; reported).

## 4. Components (0-100 each, higher = healthier)

### C1 Breadth level & trend — 25%
Score = 0.7·level + 0.3·trend + modifier.
Level map (fast line): 15%→5, 25%→20, 40%→38, 50%→50, 60%→65, 70%→82,
80%→95 (linear in between). Trend: slow line change over 10 sessions
>+0.2 pp up (80), <−0.2 pp down (30), else flat (50).
Modifier (5-session direction of fast line): falling above 60% → −10
(deceleration from a high level, early warning); falling below 40% → +5
(limited further downside); rising below 60% → +5 (early recovery).

### C2 Fast vs slow line — 20%
Gap map: −15 pp→5, −5 pp→30, 0→50, +5 pp→70, +15 pp→95. +10 if the fast line
is rising while still below the slow line (early recovery), −10 if falling
while above it. **Pink zone** flag: slow line falling *and* fast below slow —
historically the riskiest configuration in the US data.

### C3 Advance/decline — 15%
0.7·McClellan map (−120→5, −60→22, 0→50, +60→78, +120→95) + 0.3·10-session
A/D-line change normalised by universe size (−1.5→10, −0.5→35, 0→50,
+0.5→65, +1.5→90). The oscillator captures short-term thrust, the A/D line
persistence.

### C4 New highs vs lows — 15%
Share of new highs in (highs + lows) over 10 sessions: 0→5, 20%→20,
50%→50, 80%→80, 100%→95. Fewer than 3 events in 10 sessions → neutral 50
("quiet"): too few to mean anything.

### C5 Size participation — 10%
Average % above SMA200 inside WIG20, mWIG40, sWIG80 (each group needs ≥5
eligible members): 15%→5, 30%→25, 50%→50, 70%→80, 85%→95. −10 when WIG20
leads sWIG80 by more than 30 pp — a market carried by big caps only.

### C6 WIG vs breadth divergence — 15%
For 60 and 20 sessions compare WIG % change with the fast-line change.
Index up + breadth up 70; index up + breadth down 10-25 (bearish
divergence, worse when breadth fell more); index down + breadth up 65-80
(bullish divergence); both down 30; both near-flat (|WIG| < 0.5% and
|breadth| < 1 pp) 50. Combined 0.6·60d + 0.4·20d. **Early warning**: 20d
≤ 25 while 60d ≥ 50.

## 5. Composite & zones

Weighted average of available components; weights of missing components are
redistributed proportionally. `data_quality`: complete (6/6), partial (4-5),
limited (≤3). `caution` = a negative direction modifier in C1 or C2.

| Score | Zone | Exposure | Meaning |
|---|---|---|---|
| 80-100 | Strong | 90-100% | broad participation, momentum-friendly |
| 60-79 | Healthy | 75-90% | normal operations |
| 40-59 | Neutral | 60-75% | selective, smaller new positions, tighter stops |
| 20-39 | Weakening | 40-60% | take profits, raise cash |
| 0-19 | Critical | 25-40% | capital preservation, watch for a trough |

The zone boundaries and exposure ranges are inherited from the US original
so users of both skills can compare readings. They are heuristics, not
backtested optimal allocations.

## 6. Limitations

- Calibration: level maps were designed on S&P 500 behaviour; GPW breadth
  swings can be wider. The `fast_percentile` in the report puts today's level
  in the context of the available GPW history — use it as a sanity check.
- Dividends below 0.5% of the price are not adjusted (indistinguishable from
  rounding); their effect on SMAs is negligible.
- Size groups use current index membership (look-ahead for history).
- Data is end-of-day; intraday moves are not reflected.
- Breadth describes the market, not any single company.

## 7. Differences from the US original

| | US `market-breadth-analyzer` | `gpw-market-breadth` |
|---|---|---|
| Data | pre-computed S&P 500 breadth CSV | computed from raw GPW quotes |
| Slow line | EMA 200 | EMA 100 |
| Cycle (peak/trough) component | yes | replaced by A/D (McClellan) and new highs/lows — peak detection needs a decade of history |
| Size participation | — | WIG20 / mWIG40 / sWIG80 |
| Divergence index | S&P 500 | WIG (configurable) |
