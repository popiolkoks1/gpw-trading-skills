# GPW Sector Trend — methodology

Contents: 1. Data & universe · 2. Uptrend ratio · 3. Sectors & groups ·
4. Components · 5. Composite, warnings, zones · 6. Limitations ·
7. Relative Rotation Graph (`--rrg`)

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

## 7. Relative Rotation Graph (`--rrg`)

### 7.1 Concept and sources

The Relative Rotation Graph was developed by Julius de Kempenaer (RRG
Research) and is distributed by StockCharts and Bloomberg. Each item is a
point: x = **JdK RS-Ratio** (trend of relative strength vs a benchmark),
y = **JdK RS-Momentum** (rate of change of RS-Ratio), both centred on 100,
with a tail of the last weeks. The exact JdK formulas are **proprietary and
not published**; this skill uses an open approximation built only from the
public description, so its numbers are *not* identical to StockCharts or
Bloomberg values (shape and quadrant are what matter).

- J. de Kempenaer, RRG Research — https://www.relativerotationgraphs.com
- StockCharts ChartSchool, "RRG Charts" — https://school.stockcharts.com/doku.php?id=chart_analysis:rrg_charts
  (quadrants, clockwise rotation, tails, weekly default).
- Evidence: RRG itself has little academic backing. The effect it visualises
  — persistence of relative (industry) momentum — is documented: Moskowitz
  & Grinblatt (1999), "Do Industries Explain Momentum?", *Journal of
  Finance* 54(4). Treat quadrants as context for where to look, not as a
  tested trading rule.

### 7.2 Formulas (weekly data)

Weekly series = the **last common session of each ISO week** of the item and
the benchmark (session dates come from the GPW archive, so holiday weeks end
on the last real session, e.g. Thursday before Good Friday). If the newest
week is not finished (`gpwlib.plcal` still expects a session in it),
`rrg.partial_week = true`.

| Step | Formula | Default | Why |
|---|---|---|---|
| RS | 100 × price / benchmark | — | classic relative-strength line |
| smoothing | S = EMA(RS, n_s), first n_s−1 values dropped | n_s = 10 weeks | ~one quarter; removes weekly noise while keeping turns visible within a month or two |
| **RS-Ratio** | 100 + (S − mean(S, N)) / std(S, N) | N = 52 weeks | one year: removes the long-run RS level (sector re-ratings), covers a full seasonal/dividend cycle; one unit = one standard deviation, so typical readings fall in the 96-104 band known from StockCharts charts |
| ROC | EMA(100 × (RS-Ratio_t / RS-Ratio_{t−1} − 1), n_m) | n_m = 3 weeks | week-over-week change of an already smoothed series; the EMA(3) cut one-week quadrant flip-flops on GPW indices from ~14 to ~8 per item and year (2026 sample) for ~1 week of extra lag |
| **RS-Momentum** | 100 + ROC / rms(ROC, M) | M = 26 weeks | a z-score *around zero* (mean fixed at 0): RS-Momentum > 100 exactly when RS-Ratio is rising. A de-meaned z-score would drift back to 100 after a few months of steady decline and break that reading. Half a year keeps momentum faster than the ratio and lets ~2 years of data give a full tail |

History needed: (n_s−1) + (N−1) + 1 + (n_m−1) + (M−1) + tail = **96 weeks**
with defaults (about 480 sessions — what the shared cache holds). With less
history the run stops with exit code 1 and says how many weeks are missing;
shorter `--rrg-norm` / `--rrg-mom-norm` trade stability for coverage.

Total-return consistency: WIG and the sector sub-indices are total-return
(*dochodowe*) indices; WIG20, mWIG40 and sWIG80 are price indices, so their
published TR variants (WIG20TR, MWIG40TR, SWIG80TR) are used. Stock mode uses
dividend- and split-adjusted closes (`gpwlib.panel.build_panel`) vs the
total-return sector index.

### 7.3 Derived fields

| Field | Definition |
|---|---|
| `quadrant` | leading (RS-Ratio > 100, RS-Momentum > 100), weakening (> 100, ≤ 100), lagging (≤ 100, ≤ 100), improving (≤ 100, > 100) |
| `heading_deg` | angle of the last weekly step, 0° = east (RS-Ratio rising), 90° = north, counter-clockwise, 0-360. 0-90° (NE) is the strongest direction, 180-270° (SW) the weakest |
| `distance` | √((RS-Ratio−100)² + (RS-Momentum−100)²), in standard deviations: strength of the relative move |
| `rotation` | net change of the position angle around (100,100) along the tail; negative = **clockwise** (the normal improving → leading → weakening → lagging cycle). Steps with a point within 0.25 of the centre are skipped (angle undefined, inside week-to-week noise); a net turn under 20° (~1/18 of a circle) is `none` |
| `transitions` | quadrant changes in the last 4 weekly steps (one month), each with `clockwise` = it followed the normal order. A counter-clockwise change (e.g. weakening → leading) is a *turn-back*: common, not an error |
| `weeks_in_quadrant` | consecutive latest weeks in the current quadrant |
| `rotation_summary` | recent transitions (newest first) plus items *approaching* the next clockwise quadrant: heading toward it and within 0.5 of the axis they must cross |

### 7.4 Concentration flag

GPW Benchmark weights (current portfolio, `gpwlib.constituents`): an index is
`concentrated` when its largest member has ≥ 50% of the weight or the two
largest ≥ 70%. Then one or two companies explain most of the index's moves
and the point is really a single-stock (or pair) chart. November 2026
examples: WIG-PALIWA (Orlen ~98%), WIG-GORNIC (KGHM ~95%), WIG-GRY
(CD Projekt ~88%), WIG-ODZIEZ (LPP ~83%), WIG-MOTO (Inter Cars ~79%),
WIG-BUDOW (Budimex ~61%), WIG-CHEMIA (Azoty ~60%), WIG-INFO (Asseco ~55%),
WIG-ENERG (PGE + Tauron ~75%). Without weights the fallback is ≤ 3 members.
The JSON also carries `top1_pct`, `top2_pct`, `hhi` and `effective_n`
(= 10 000 / HHI) for context.

Stock mode: `concentrated` = the stock holds ≥ 50% of its own sector index;
its RS against that index is then close to flat by construction, so compare
it with WIG instead.

### 7.5 Stock mode

`--stocks SECTOR`: current constituents of the sector index (GPW Benchmark)
vs that index (or `--benchmark`). Liquidity filter = the universe defaults:
last close ≥ 1 zł and 20-session median turnover ≥ 50k zł (same as §1);
excluded names are listed with the reason.

### 7.6 Signals (`signals[]`, gpw-signal-postmortem contract v1)

One signal per quadrant transition in the last 4 weeks, dated at the weekly
close where it happened (the post-mortem deduplicates on skill, ticker, date
and kind, so re-running weekly adds nothing twice):

| Transition | kind | direction |
|---|---|---|
| improving → leading | `rrg_improving_to_leading` | long |
| leading → weakening | `rrg_leading_to_weakening` | exit |

`ticker` = the index name (`WIG-BANKI`, `WIG20` ...). Stock mode uses
`rrg_stock_improving_to_leading` / `rrg_stock_leading_to_weakening` because
the benchmark is the sector index, not WIG. `score` = min(100, 25 ×
distance): a move 4 standard deviations from the centre scores 100, so
quintiles of the score separate weak (near-centre) from strong crossings.

### 7.7 Limitations

- Open approximation, not the JdK formula; values differ from commercial RRGs.
- Relative only: a leading sector can fall in absolute terms in a bear market.
- Clockwise rotation is a tendency; hooks back (weakening → leading,
  improving → lagging) are frequent, especially near the centre.
- Weekly data reacts slowly; the newest point of an unfinished week moves.
- Index weights are today's portfolio (not point-in-time); ~2 years of
  cached history limits the normalisation windows.
