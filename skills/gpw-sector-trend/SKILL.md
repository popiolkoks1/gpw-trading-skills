---
name: gpw-sector-trend
description: Shows which sectors of the Polish stock market (GPW, Warsaw Stock Exchange) are in an uptrend - share of stocks in a confirmed uptrend overall and in each of the 14 GPW sector indices (banks, fuels, energy, IT, games, construction, mining, chemicals, food, pharma, apparel, automotive, real estate, media), cyclical-vs-defensive rotation and index performance vs WIG, scored 0-100. RRG mode draws a Relative Rotation Graph (RS-Ratio vs RS-Momentum, leading/weakening/lagging/improving, weekly tails, also stocks inside one sector). No API key. Use when the user asks which Polish sectors are strong or weak, where money is rotating, or how many GPW stocks are in an uptrend. PL triggers - "które sektory są mocne", "rotacja sektorowa", "jak radzą sobie banki", "ile spółek jest w trendzie wzrostowym", "rotacja sektorów wykres", "który sektor przechodzi do liderów". EN triggers - "GPW sector rotation", "strongest Polish sectors", "RRG", "relative rotation graph". Not for single-company valuation.
license: MIT
metadata:
  version: "1.1.0"
  derived_from: "tradermonty/claude-trading-skills uptrend-analyzer (MIT)"
  languages: "pl,en"
---

# GPW Sector Trend / Trendy sektorowe GPW

Counts how many GPW stocks are in a **confirmed uptrend** — overall and in
each of the 14 GPW Benchmark sector indices — and scores the environment
0-100 (higher = healthier). It complements `gpw-market-breadth`: breadth
asks "how many stocks are rising", this skill asks "which parts of the
market lead and is leadership rotating to risk-on or risk-off sectors".
The `--rrg` mode adds a **Relative Rotation Graph**: where each sector (and
WIG20 / mWIG40 / sWIG80) sits against WIG and which way it is turning.

**Workflow position:** reads the GPW archive (shares + indices) and GPW
Benchmark portfolios; feeds `gpw-exposure-coach` (`gpw_sector_trend_*.json`,
uptrend mode), `gpw-signal-postmortem` (RRG `signals[]` in
`gpw_sector_rrg_*.json`), `gpw-daily-brief` / `gpw-watchlist` (where to look
for ideas).

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

## RRG mode (Relative Rotation Graph)

### When to use it

- "RRG", "relative rotation", "rotacja sektorów wykres", "który sektor
  przechodzi do liderów", "co traci impet", "where is money rotating to now".
- The uptrend-ratio mode answers *how many* stocks of a sector trend up; RRG
  answers *how the sector index behaves relative to WIG and which way it is
  turning*. For "which sectors are strong" run the default mode first; use
  RRG for "which ones are gaining / losing leadership".
- Stock mode: "które banki są najsilniejsze na tle sektora", "RRG for the
  stocks in WIG-GRY".

### Commands

```bash
python3 scripts/gpw_sector_trend.py --rrg --lang pl --output-dir <dir>                  # 14 sectors + WIG20/mWIG40/sWIG80 vs WIG
python3 scripts/gpw_sector_trend.py --rrg --benchmark WIG20 --tail 10 --svg <dir>/rrg.svg
python3 scripts/gpw_sector_trend.py --rrg --stocks banki --lang pl                     # bank stocks vs WIG-BANKI
```

| Flag | Default | Meaning |
|---|---|---|
| `--benchmark` | WIG (stocks mode: the sector index) | index to compare against; WIG20/MWIG40/SWIG80 use their TR variants |
| `--stocks SECTOR` | — | stock mode; accepts `WIG-BANKI`, `BANKI`, `banki`, `Banks` |
| `--tail` | 8 | weeks of path shown/returned (2-26) |
| `--rrg-smooth` / `--rrg-norm` / `--rrg-mom-smooth` / `--rrg-mom-norm` | 10 / 52 / 3 / 26 | weeks; see methodology §7 |
| `--svg FILE` | — | standalone SVG chart with tails |
| `--min-price`, `--min-turnover-k` | 1 zł / 50k zł | stock-mode liquidity filter (20-session median turnover) |
| `--no-files`, `--offline`, `--as-of`, `--cache-dir` | | as in the default mode |

Writes `gpw_sector_rrg_<stamp>.{json,md}` (its own prefix, so consumers of
`gpw_sector_trend_*.json` are not affected). Exit 1 = data problem (missing
benchmark, too little history) — report it, do not guess positions.

### Present (lead with the answer)

1. One sentence per quadrant: who is leading, improving, weakening, lagging.
2. The rotation summary (`rrg.rotation_summary`): recent quadrant changes,
   e.g. "Banki przechodzą z liderów do słabnących", and items approaching
   the next quadrant. Flag counter-clockwise moves as turn-backs.
3. The table and the ASCII chart from the Markdown (or offer the SVG).
4. Concentration: for indices flagged `concentrated` say that the point is
   really one or two companies (WIG-ODZIEZ ≈ LPP, WIG-PALIWA ≈ Orlen,
   WIG-GRY ≈ CD Projekt, WIG-GORNIC ≈ KGHM).
5. If `rrg.partial_week` is true, say the latest point can still move.

### Explaining it to a beginner

- X axis (RS-Ratio): is the sector doing better (>100) or worse (<100) than
  the whole market? Y axis (RS-Momentum): is that getting better (>100) or
  worse (<100)?
- **Leading** = strong and getting stronger; **weakening** = still strong
  but fading; **lagging** = weak and getting weaker; **improving** = weak but
  catching up.
- Points usually travel **clockwise**: improving → leading → weakening →
  lagging → improving. Heading 0-90° (up and to the right) is the strongest
  direction; distance from the centre is how strong the move is.
- Limits, always say at least one: this is a tendency, not a law (points
  often hook back, e.g. weakening → leading); quadrant changes near the
  centre are noise; RRG is relative — a "leading" sector can still fall if
  WIG falls more; the formula is an open approximation of de Kempenaer's
  proprietary JdK indicators, so numbers differ from StockCharts; weekly data
  reacts slowly. A quadrant is a place to look, never a buy or sell signal.

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
  limitations; §7 RRG formulas, parameters, concentration flag, signals and
  sources. Load on first use or when asked "how/why".

## Related skills

`gpw-market-breadth` (whole-market health), `gpw-market-regime` (trend state
of WIG), `gpw-exposure-coach` (reads this report), `gpw-trend-screener`
(leaders inside a strong sector), `gpw-signal-postmortem` (scores the RRG
signals later), `gpw-position-sizer` (risk for a trade idea),
`gpw-trade-journal` (record the idea with its sector context).
