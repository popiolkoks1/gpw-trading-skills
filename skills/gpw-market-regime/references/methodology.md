# GPW Market Regime — methodology

Contents: 1. Data · 2. Distribution days · 3. Volatility scaling ·
4. Corrections, rally attempts, follow-through days · 5. State machine ·
6. Trend context and leadership · 7. Top-risk score · 8. Calibration on
GPW data · 9. Limitations

Derived from tradermonty/claude-trading-skills (MIT): *FTD Detector*, *IBD
Distribution Day Monitor* and *Market Top Detector*, which implement
William O'Neil's / Investor's Business Daily market-direction rules for US
indices. Everything below is the GPW adaptation.

## 1. Data

GPW *Archiwum notowań*, instrument type "indeksy" (`type=1`): daily open,
high, low, close, % change and **turnover in thousand PLN** for WIG, WIG20,
mWIG40 (`MWIG40`) and sWIG80 (`SWIG80`). Indices have no corporate actions
(they are already adjusted by GPW Benchmark), so prices are used as
published.

**Turnover check (Nov 2026, 479 cached sessions 2024-12-12 .. 2026-11-13):**
all four indices carry a non-zero `turnover_k` on every session; the
published % change matches the close-to-close change on every session. If an
index has turnover on fewer than 80% of sessions the engine switches that
index to **price-only mode** (distribution days = any decline ≥ 0.2%, FTDs
without volume confirmation), flags it in the table (`no turnover!`) and in
`caveats`. A single missing turnover value on an otherwise covered index
makes that one session "not comparable": it is never counted as a
distribution day or an FTD (conservative: no signal from missing data).

Index turnover in the archive is the sum of the session turnover of the
index members. Quarterly WIG20 futures expiry and index reviews (third
Friday of March, June, September, December) inflate it with mechanical
trades; such distribution days and FTDs are flagged `expiry_day`.

## 2. Distribution days (IBD)

* **Definition:** index close down **≥ 0.2%** vs the previous close on
  **higher turnover** than the previous session (IBD; O'Neil, *How to Make
  Money in Stocks*, ch. 9). The 0.2% filter is kept unscaled: its purpose is
  to ignore flat sessions, not to measure volatility.
* **Window:** count over the last **25 sessions** (IBD).
* **Expiry:** a day drops out after 25 sessions **or** as soon as the index
  closes **≥ 5%** above that day's close (IBD). Once expired by the 5% rule
  it stays expired.
* **Stall days are not counted.** IBD also counts "stalling" (a small gain
  on higher volume near highs). Its definition is subjective (how small? how
  near the high? close in the lower half of the range?) and on GPW, where a
  handful of large caps drive WIG turnover, it would add noise. Documented
  omission; the plain distribution count is used everywhere.

**Bands (25 sessions):**

| Count | Band | Rationale |
|---|---|---|
| 0–3 | NORMAL | at or below the GPW median (3) and below the chance level |
| 4–5 | CAUTION | IBD: 4–5 = warning; GPW 50–80th percentile |
| 6–7 | HIGH | IBD: 5–6+ typically marks "uptrend under pressure"; GPW ≥ 90th pct |
| 8+ | SEVERE | above the GPW 95th percentile (7) |

**Chance level:** on GPW 2025-26 about 37% of WIG sessions fall ≥ 0.2% and
about half of all sessions have higher turnover than the day before, so
~19% of sessions qualify by chance (measured: 18.6% WIG, 19.6% WIG20,
19.6% mWIG40, 15.9% sWIG80) — about 4.7 per 25 sessions before expiries.
Counts of 4–5 are therefore weak evidence on their own; 6+ is clearly
abnormal. The same is broadly true of US indices, which is why IBD reacts to
5–6+, not to 3–4.

## 3. Volatility scaling

US thresholds assume S&P 500 / Nasdaq volatility. The reference is the S&P
500 daily return standard deviation 1990–2024, ≈ **1.10%** (≈ 17.5%
annualised). For every session the engine computes the index's own daily
st.dev. over the trailing 250 sessions (minimum 60) and the ratio
`vol_ratio = sigma_index / 1.10`:

| Threshold | US base | GPW rule | Clamp | WIG Nov 2026 |
|---|---|---|---|---|
| FTD minimum gain | 1.25% (IBD's modern minimum; O'Neil's original 1%) | 1.25% × ratio | 0.8–2.2% | 1.21% |
| Correction (drawdown) | 8% | 8% × ratio | 6–12% | 7.7% |
| Bear (drawdown) | 20% | 20% × ratio | 15–25% | 19.3% |

Measured sigma (Nov 2026): WIG 1.06%, WIG20 1.17%, mWIG40 1.03%, sWIG80
0.73%. WIG is about as volatile as the S&P 500, so the GPW thresholds land
very close to IBD's; sWIG80 is much calmer, so its FTD threshold is lower
(clamped at 0.8%). The clamps stop silly values on very calm or very wild
stretches. Rolling (not full-sample) sigma avoids look-ahead in the history
table.

**Why 8% for a correction:** "pullback" is commonly 5–10% and "correction"
10%+ in the press, but IBD usually changes its outlook to "market in
correction" after 7–10% falls accompanied by distribution. 8% sits in that
range; scaled by WIG volatility it is 7.7%. WIG's largest fall in the cached
history was 11.8% (April 2025 tariff shock) — a 10% rule would have seen a
single correction in two years.

## 4. Corrections, rally attempts and follow-through days

* **Correction phase begins** when, during an uptrend phase, (a) the index
  closes ≥ the correction threshold below the **highest close of the current
  uptrend phase** (not the 52-week high — after a follow-through the index can
  still be 15% under its old high and that must not undo the new uptrend),
  or (b) there are ≥ 6 distribution days, the close is below SMA50 **and** the
  index is at least half the correction threshold below that peak (a real
  decline, not a 1% dip), or (c) a follow-through fails (below).
* **Low:** the lowest close since the correction began (and the lowest
  intraday low for the undercut test).
* **Rally attempt, day 1:** the first close higher than the previous close
  after the low. Days are counted in sessions, down days included, as long as
  the low holds.
* **Failure:** a *close* below the lowest intraday low of the correction ends
  the attempt; the next up close starts a new day 1 from the new low. Closing
  basis (not intraday) keeps single intraday spikes on thin sessions from
  resetting the count.
* **Follow-through day (FTD):** day **4 or later** of an attempt, gain ≥
  the scaled threshold, turnover higher than the previous session (O'Neil:
  "on day four or later, a gain of well over 1% on heavier volume"). FTDs
  after day 10 are flagged `late` (weaker evidence). On an FTD the model
  enters the uptrend phase.
* **Post-FTD health:** `healthy` (≤ 25 sessions since the FTD, no warning),
  `under_pressure` (close below the FTD day's low within 25 sessions — IBD's
  first sign of failure), `weak` (≥ 3 distribution days within 15 sessions of
  the FTD), `failed` (close below the rally low → back to correction),
  `ended` (a later correction began), `established` (> 25 sessions, uptrend
  intact; earlier notes kept for information).
* **Thin-market escape hatch:** a new 52-week closing high ends a correction
  even without a qualifying FTD (`new_high_without_ftd`). On GPW turnover is
  lumpy and some recoveries never print an FTD; without this rule the model
  could report "rally attempt" at an all-time high. This is a documented
  deviation from O'Neil.

## 5. State machine (`regime.state`)

Evaluated once per session, from the first session with an SMA50 and 60
returns (SMA200 rules switch on automatically after 200 sessions). Two
internal phases (UP / CORRECTION) map to five reported states:

| State | Phase | Rule |
|---|---|---|
| `confirmed_uptrend` | UP | no pressure condition |
| `uptrend_under_pressure` | UP | ≥ 6 distribution days, **or** two consecutive closes below SMA50, **or** close below the FTD day's low within 25 sessions of the FTD. Stays until distribution ≤ 4 **and** two consecutive closes above SMA50 (hysteresis, so one expiring day does not flip the state) |
| `bear` | CORRECTION | drawdown from the 52-week high ≥ bear threshold, **or** close < SMA200 with SMA200 falling (20-session slope < 0) and SMA50 < SMA200 |
| `rally_attempt` | CORRECTION | not bear, a rally attempt is under way |
| `correction` | CORRECTION | otherwise |

Precedence inside the correction phase: bear > rally attempt > correction (a
bounce inside a bear market is still reported as `bear`; the attempt and its
day count are in `rally_attempt`). The machine starts with
`init_uptrend` if the close is above SMA50 and the drawdown is below the
correction threshold, otherwise `init_correction`; the first sessions are
warm-up and the report shows the last 250 sessions (`--history-sessions`).

`regime.state` is the state of the primary index (`--index`, default WIG,
the broadest index). Each of WIG, WIG20, mWIG40, sWIG80 has its own state in
`indices`; `regime.indices_in_uptrend` counts how many are in an uptrend
phase.

## 6. Trend context and leadership

Per index: close vs SMA50 and SMA200, SMA50 vs SMA200, SMA200 slope over 20
sessions (Minervini trend template: "200-day rising for at least one
month"), drawdown from the 52-week (250-session) closing high and its date,
20- and 60-session returns.

**Leadership divergence:** WIG20 (large caps) minus sWIG80 (small caps)
return over 20 and 60 sessions. Generals leading while the soldiers lag is
the classic narrow-rally / topping pattern (O'Neil; also the breadth
divergence logic in gpw-market-breadth). Risk score 0–100:
60-session spread 0 → 0, 5 pp → 40, 10 pp → 80, 15 pp → 100; 20-session
spread 0 → 0, 3 → 40, 6 → 80, 9 → 100; blended 60/40. In 2025-26 the
60-session spread averaged +5 pp with a st.dev. of ~6 pp, so 10 pp is about
one sigma above an already large-cap-led norm and 15 pp is rare. Small caps
leading scores 0 (it is not a topping sign).

## 7. Top-risk score (0–100, higher = more risk)

| Component | Weight | Scoring |
|---|---|---|
| Distribution (primary index) | 30% | 0 → 0, 3 → 25, 5 → 50, 7 → 80, 9+ → 100 |
| Trend breaks | 25% | close < SMA50 +35, close < SMA200 +35, SMA50 < SMA200 +15, SMA200 falling +15 |
| Drawdown from 52-week high | 15% | 0 → 0, 3% → 20, correction threshold → 70, bear threshold → 100 |
| WIG20 vs sWIG80 leadership | 15% | section 6 |
| Breadth (optional) | 15% | 50% × (100 − gpw-market-breadth `index_divergence` score) + 50% × (100 − breadth composite) |

Distribution gets the largest weight because it is the core IBD top signal;
trend breaks next (they confirm that selling has damaged the trend). Missing
components (no breadth file, no WIG20/sWIG80) are dropped and the weights
renormalised; `top_risk.missing` lists them. Bands: < 20 low, 20–39
moderate, 40–59 elevated, 60–79 high, 80+ very high.

The breadth file is taken from `--breadth-json` or the newest
`gpw_breadth_*.json` in `--input-dir`. It is ignored if its `as_of` is
later than the analysed session (no look-ahead), and flagged `stale` if it
is more than 3 sessions older.

## 8. Calibration on GPW data (Nov 2026 cache)

WIG, 2025-03-14 .. 2026-11-13: April 2025 tariff fall (−11.8%) →
correction (2025-04-04) → rally attempt from 2025-04-08 → FTD 2025-04-22
(day 9, +2.06%); a distribution-led correction 2025-09-17 → FTD 2025-10-15
(day 17, flagged late); another 2026-03-09 → FTD 2026-03-17 (day 6, +2.2%);
the rest of the time a confirmed uptrend with recurring "under pressure"
spells when distribution reached 6. Over the last 250
sessions: confirmed 72%, under pressure 25%, rally attempt 2%, correction
<1%, bear 0%. sWIG80 ended the period in `bear` (below a falling SMA200)
while WIG made new highs — exactly the narrow-market divergence the
leadership component is designed to catch.

## 9. Limitations

* The rules were designed for deep, liquid US indices. WIG and WIG20 are
  dominated by a few companies (PKO BP, Orlen, PZU, Pekao, KGHM, LPP), so one
  stock's news can create or erase a distribution day or an FTD.
* Short history (the cache holds ~2 years): the bands and the calibration
  are based on one market cycle without a real bear market.
* Turnover includes expiry-day and index-review volume (flagged, not
  removed).
* No intraday data: "close in the lower half of the range", used by some
  IBD variants, is not applied.
* A regime is context for risk decisions, not a timing system. It says
  nothing about any individual stock.
