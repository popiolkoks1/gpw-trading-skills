---
name: gpw-market-regime
description: Tells which market regime the Polish stock market (GPW, Warsaw Stock Exchange) is in, using William O'Neil / IBD rules adapted to GPW - distribution days (index down on higher turnover, 25-session count with expiry), follow-through days after a correction (day 4+ of a rally attempt, volatility-scaled gain on higher turnover), trend context (SMA50/SMA200, drawdown from the 52-week high) and WIG20-vs-sWIG80 leadership for WIG, WIG20, mWIG40 and sWIG80. Outputs one of five states - confirmed uptrend, uptrend under pressure, rally attempt, correction, bear - plus a 0-100 top-risk score and a regime history. No API key. Use when the user asks whether the Polish market is in an uptrend or a correction, about distribution days, a follow-through day or a market top on GPW. PL triggers - "czy rynek jest w korekcie", "dni dystrybucji", "follow-through", "czy to szczyt na WIG", "czy hossa się kończy". EN triggers - "GPW market regime", "WIG distribution days". Not for single stocks or US indices.
license: MIT
metadata:
  version: "1.0.0"
  derived_from: "tradermonty/claude-trading-skills ftd-detector, ibd-distribution-day-monitor, market-top-detector (MIT)"
  languages: "pl,en"
---

# GPW Market Regime / Reżim rynku GPW

Answers the first question of O'Neil's method — *what is the market doing?*
— for the Warsaw Stock Exchange. It reads the GPW index archive (WIG,
WIG20, mWIG40, sWIG80, with turnover), counts **distribution days**
(institutional selling), tracks **rally attempts** and **follow-through
days** after corrections, and runs a daily state machine that reports one of
five regimes and a 0-100 **top-risk score**. Its JSON is the market input of
`gpw-exposure-coach` and `gpw-pre-trade-gate`.

## When to use

- "Is WIG in an uptrend or a correction?", "how many distribution days?",
  "was there a follow-through day?", "is this a market top?", "czy hossa się
  kończy?", "czy już można wracać na rynek po spadkach?"
- Before planning exposure (`gpw-exposure-coach`) or a new trade
  (`gpw-pre-trade-gate`).

Not for: single-stock analysis, US/other markets, intraday timing.

## Language

Answer in the user's language and pass `--lang pl` (default) or `--lang en`.

## Workflow

### 1. Inputs

| Input | Flag | Default |
|---|---|---|
| Primary index for `regime.state` | `--index WIG\|WIG20\|MWIG40\|SWIG80` | WIG |
| Historical run | `--as-of YYYY-MM-DD` | latest session |
| Cached data only | `--offline` | off |
| Breadth file (optional, adds a component) | `--breadth-json PATH` or newest `gpw_breadth_*.json` in `--input-dir` | `.` |
| History window | `--history-sessions` | 250 |
| Output | `--output-dir`, `--no-files` | `.` |

### 2. Run the script (never estimate regime numbers yourself)

From the skill directory:

```bash
python3 scripts/gpw_market_regime.py --lang pl --output-dir <dir>
# richer: run gpw-market-breadth into the same <dir> first, then
python3 scripts/gpw_market_regime.py --lang pl --input-dir <dir> --output-dir <dir>
```

Uses the shared cache `~/.cache/gpw-trading-skills` (index sessions are
small: one request per session, ~0.6 s each on first run). Writes
`gpw_market_regime_YYYY-MM-DD_HHMMSS.{json,md}` and prints a one-line
summary. Exit code 1 = data problem (e.g. offline with too few cached
sessions): report the stderr message, do not invent numbers.

### 3. Check freshness first

If `data_provenance.freshness.is_stale` is true, say so first. If
`breadth_input.stale` is true, mention the breadth file is old.

### 4. Present (lead with the answer)

1. **One line:** regime of WIG in plain words + top-risk score/band +
   distribution-day count/band. Then the plain-language explanation of the
   state (the Markdown "Answer" section has it).
2. **Why:** `regime.state_reasons` (e.g. "6+ distribution days", "two closes
   below SMA50", "day 3 of a rally attempt").
3. **The four indices:** state table; point out divergences (e.g. WIG in
   an uptrend while sWIG80 is in a bear market = narrow market).
4. **Follow-through / rally attempt:** last FTD date, day number, gain vs
   threshold, health; or the current attempt day, rally low and what an FTD
   would need. Explain: an FTD *confirms* a new uptrend but many fail; the
   attempt fails if the index closes below the rally low.
5. **Top-risk components** briefly; name the largest contributor.
6. **History:** summarise the transitions table (how often the model changed
   its mind, share of time in each state) so the user sees its track record.
7. **Caveats:** US-designed rules, thin GPW, expiry-day turnover; price-only
   mode if flagged.

Explain jargon once, simply: *dzień dystrybucji* = a day when the index
falls on higher turnover than the day before (big players selling);
*follow-through day* = a strong up day on higher turnover from the 4th day
of a rebound, which historically marks the start of new uptrends.

### 5. Frame it correctly

- A regime is **context for risk**, not a signal to buy or sell anything.
  Never say "buy now" or "sell everything". Use phrasing like "this
  environment historically favoured caution with new positions".
- For "how much should I have in stocks?" hand over to `gpw-exposure-coach`.

## Key rules (summary — details in references/methodology.md)

- Distribution day: close ≤ −0.2% on higher turnover; 25-session window;
  expires after 25 sessions or a ≥ 5% rise above its close. Bands: 0-3
  normal, 4-5 caution, 6-7 high, 8+ severe (GPW chance level ≈ 4.7 per 25).
  Stall days are not counted.
- Thresholds scale with volatility vs the S&P 500 (≈ 1.10%/day): FTD gain
  1.25% × ratio (0.8-2.2%), correction 8% × ratio (6-12%), bear 20% × ratio
  (15-25%).
- States: under pressure = 6+ distribution days, 2 closes < SMA50 or below
  the FTD day's low (recovers at ≤ 4 and 2 closes > SMA50); correction =
  drawdown from the uptrend peak ≥ threshold, or 6+ distribution days below
  SMA50 with ≥ half that drawdown, or failed FTD; bear = drawdown ≥ bear
  threshold or below a falling SMA200 with SMA50 < SMA200; a new 52-week
  high ends a correction even without an FTD (thin-market adaptation).
- Top risk = distribution 30% + trend breaks 25% + drawdown 15% + WIG20 vs
  sWIG80 15% + breadth 15% (optional, renormalised when missing).

## JSON contract (consumed by other skills)

`regime = {state, top_risk_score (0-100), distribution_days (int, WIG), as_of,
index, top_risk_band, distribution_band, state_reasons, follow_through,
rally_attempt}`, plus `indices.<NAME>` details, `top_risk.components`,
`leadership`, `history.transitions`, `breadth_input`, `data_provenance`.

## References

- `references/methodology.md` — definitions, every threshold with its
  source, calibration on GPW data, limitations. Load when asked "how/why".

## Related skills

`gpw-market-breadth` (participation; its JSON adds the breadth component),
`gpw-sector-trend` (sector leadership), `gpw-exposure-coach` (turns this
regime into exposure and risk limits), `gpw-pre-trade-gate` (checks a trade
idea against the regime), `gpw-position-sizer`, `gpw-trade-journal`.
