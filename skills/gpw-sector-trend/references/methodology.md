# GPW Sector Trend — methodology

Contents: 1. Data & universe · 2. Uptrend ratio · 3. Sectors & groups ·
4. Components · 5. Composite, warnings, zones · 6. Limitations

## 1. Data & universe

Same as `gpw-market-breadth`: daily quotes of every share from the GPW
*Archiwum notowań*, dividend- and split-adjusted via GPW's published % change, liquid
universe (≥200 sessions history, ≥ 1 zł, 20-session median turnover ≥ 50k zł).

## 2. Uptrend ratio

A stock is in an uptrend when **all** hold (adapted 1:1 from the Finviz screen
behind Monty's US Uptrend Ratio dashboard, minus the US-specific price/volume
/market-cap filters which are replaced by the liquidity filter):

| Condition | Why |
|---|---|
| close > SMA20 | short-term trend up |
| close > SMA200 | long-term trend up |
| SMA50 > SMA200 | "golden cross" structure |
| close ≥ 1.3 × lowest close of ~250 sessions | real recovery from the low, not a dead-cat bounce |
| close > close 20 sessions ago | positive ~4-week momentum |

Ratio = stocks in uptrend / eligible stocks. Then MA10 of the ratio, slope =
1-session change of MA10, smoothed with EMA(3); trend = up if slope > 0.

Status thresholds 37% (overbought) and 9.7% (oversold) are the US dashboard
constants. Because the definition is identical they are a reasonable
starting point; the historical-percentile component corrects for any
GPW-specific level shift.

## 3. Sectors & groups

Sectors = current portfolios of the 14 GPW Benchmark sector indices
(WIG-BANKI, WIG-BUDOW, WIG-CHEMIA, WIG-ENERG, WIG-GORNIC, WIG-GRY, WIG-INFO,
WIG-LEKI, WIG-MEDIA, WIG-MOTO, WIG-NRCHOM, WIG-ODZIEZ, WIG-PALIWA,
WIG-SPOZYW). WIG-TELKOM no longer exists (verified Nov 2026).

| Group | Sectors | Rationale |
|---|---|---|
| Cyclical | banks, construction, IT, games, automotive, apparel, real estate, media | earnings tied to the domestic cycle, credit and consumer demand; Polish banks are strongly rate- and GDP-sensitive |
| Defensive | food, pharma, energy utilities | stable demand; Polish utilities are regulated, state-controlled and trade as yield/defensive names |
| Commodity | mining (KGHM, JSW...), fuels (Orlen...), chemicals | driven by copper, coal, oil, gas prices — leadership often late-cycle |

Sectors with fewer than 3 eligible members are shown but marked
"small sample" and excluded from scoring.

## 4. Components

**C1 Overall breadth (30%)** — ratio map: 0→0, 9.7%→10, 25%→40, 37%→70,
50%→90, 65%→100; ±5 for slope direction.

**C2 Sector participation (25%)** — 0.6 × (% of scored sectors with rising
MA10) + 0.4 × spread score (max−min ratio: ≤15 pp→90, 40 pp→30, ≥60 pp→10).
Narrow spread = uniform participation.

**C3 Rotation (15%)** — cyclical avg − defensive avg: −15 pp→10, −5→45,
0→57, +5→70, +15→95. −5 if commodity avg exceeds both groups (late cycle);
−5 if a group is internally divergent (std > 8 pp, spread > 20 pp, or a
sector trending against its group majority).

**C4 Momentum (20%)** — 0.5 × smoothed slope (−0.02→0, 0→50, +0.02→100) +
0.3 × acceleration (last 10 vs previous 10 smoothed slopes; −0.01→10,
+0.01→90; 5v5 if short) + 0.2 × share of sectors with positive slope.

**C5 Historical context (10%)** — percentile of today's ratio in the
available history, with a confidence grade (sample size, presence of both
bear <10% and bull >40% readings, recent range coverage).

## 5. Composite, warnings, zones

Weighted average (missing components re-weighted), minus warning penalties:
late cycle −5, sector spread > 40 pp −3, intra-group divergence −3; max −10,
+1 back when ≥2 warnings fire (overlapping causes). Zones: 80+ strong bull
(100%), 60-79 bull (80-100%), 40-59 neutral (60-80%), 20-39 cautious
(30-60%), <20 bear (0-30%). Exposure ranges are context, not allocations.

## 6. Limitations

- Sector membership is today's portfolio (look-ahead for history).
- Small sectors (automotive, media, apparel) are dominated by 1-2 names.
- Many GPW companies (holdings, foreign issuers, NewConnect) are in no sector
  index; they count in the overall ratio only.
- End-of-day data; dividends < 0.5% of price are not adjusted.
