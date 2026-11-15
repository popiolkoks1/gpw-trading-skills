---
name: gpw-sector-trend
description: Shows which sectors of the Polish stock market (GPW, Warsaw Stock Exchange) are in an uptrend - computes the share of stocks in a confirmed uptrend overall and in each of the 14 GPW sector indices (banks, fuels, energy, IT, video games, construction, mining, chemicals, food, pharma, apparel, automotive, real estate, media), cyclical-vs-defensive rotation, momentum and sector index performance vs WIG, scored 0-100. No API key. Use when the user asks which Polish sectors are strong or weak, where money is rotating, whether banks/gaming/energy etc. are trending, or how many GPW stocks are in an uptrend. PL triggers - "które sektory są mocne", "rotacja sektorowa", "jak radzą sobie banki / spółki gamingowe / energetyka", "ile spółek jest w trendzie wzrostowym", "sektory na GPW". EN triggers - "GPW sector rotation", "strongest Polish sectors", "uptrend ratio Poland". Not for single-company valuation.
license: MIT
metadata:
  version: "1.0.0"
  derived_from: "tradermonty/claude-trading-skills uptrend-analyzer (MIT)"
  languages: "pl,en"
---

# GPW Sector Trend / Trendy sektorowe GPW

Counts how many GPW stocks are in a **confirmed uptrend** — overall and in
each of the 14 GPW Benchmark sector indices — and scores the environment
0-100 (higher = healthier). It complements `gpw-market-breadth`: breadth
asks "how many stocks are rising", this skill asks "which parts of the
market lead and is leadership rotating to risk-on or risk-off sectors".

## Language

Answer in the user's language and pass `--lang pl` (default) or `--lang en`.

## Workflow

### 1. Run the script (never guess sector strength from memory)

```bash
python3 scripts/gpw_sector_trend.py --lang pl --output-dir <dir>
```

Shares the cache with `gpw-market-breadth` (`~/.cache/gpw-trading-skills`);
the first run downloads ~2 years of sessions (one-off, ~20-40 min). Flags:
`--as-of YYYY-MM-DD`, `--offline`, `--min-turnover-k`.
Exit code 1 = data problem: report it, do not invent numbers.

### 2. Check `data_provenance.freshness`

If `is_stale`, say so first.

### 3. Present

1. One-paragraph verdict: score, zone, overall uptrend ratio.
2. Leaders and laggards: top 3 and bottom 3 sectors by uptrend ratio, with
   direction (rising/falling) and 20-session index performance vs WIG.
3. Rotation: cyclical vs defensive vs commodity — what it says about risk
   appetite.
4. Warnings (`late_cycle`, `high_spread`, `divergence`) in one sentence each.
5. Mention `(small sample)` sectors carefully: with 3-5 liquid companies one
   stock changes the ratio by 20-30 pp.

### 4. Frame it correctly

- A strong sector is a place to *look* for ideas, not a recommendation of any
  company in it. Never say "buy".
- Sector membership follows the GPW Benchmark indices, so some companies
  (e.g. holdings, conglomerates, foreign listings) belong to no sector.

## Uptrend definition (per stock)

Close > SMA20, close > SMA200, SMA50 > SMA200, close ≥ 130% of the lowest
close of the last ~250 sessions, close above its level 20 sessions ago.
Liquid shares only (≥ 1 zł, median turnover ≥ 50k zł). Same logic as the
US Uptrend Ratio dashboard used by the original skill.

## Score

| Component | Weight |
|---|---|
| Overall uptrend ratio (level + direction) | 30% |
| Sector participation (sectors rising + spread) | 25% |
| Cyclical vs defensive rotation | 15% |
| Momentum (slope, acceleration, sectors with positive slope) | 20% |
| Historical percentile | 10% |

Zones: 80+ Strong bull / Silna hossa · 60-79 Bull / Hossa · 40-59 Neutral /
Neutralna · 20-39 Cautious / Ostrożność · <20 Bear / Bessa. Warnings subtract
up to 10 points.

Sector groups: **cyclical** — banks, construction, IT, games, automotive,
apparel, real estate, media; **defensive** — food, pharma, energy utilities;
**commodity** — mining, fuels, chemicals. Rationale in the methodology.

## References

- `references/methodology.md` — thresholds, sector grouping rationale,
  limitations. Load on first use or when asked "how/why".

## Related skills

`gpw-market-breadth` (whole-market health), `gpw-position-sizer` (risk for a
trade idea), `gpw-trade-journal` (record the idea with its sector context).
