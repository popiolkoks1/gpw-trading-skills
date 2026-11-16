---
name: gpw-trend-screener
description: Screens every liquid share on the Warsaw Stock Exchange (GPW) for Minervini-style leaders - the 8-point Trend Template, an IBD-style RS rating 1-99 plus performance vs WIG, and VCP (volatility contraction pattern) detection with pivot, turnover dry-up, execution state (pre-breakout, breakout, extended, damaged), a 0-100 watchlist priority, an invalidation level with risk % and a ready gpw-position-sizer command. Also explains one stock in full and backtests the screen vs WIG. For advanced users. Use when the user asks which Polish stocks are in a strong uptrend, are leaders, show a VCP or are near a breakout, or wants a SEPA/Minervini/CANSLIM-style screen of GPW. PL triggers - "skaner Minerviniego", "trend template", "spółki liderzy", "formacja VCP", "które spółki są blisko wybicia", "siła relatywna RS". EN triggers - "GPW stock screener", "Minervini screen Poland", "VCP setups on WIG", "stocks near pivot". Not a buy recommendation, not for valuation, fundamentals or US stocks.
license: MIT
metadata:
  version: "1.0.0"
  derived_from: "tradermonty/claude-trading-skills vcp-screener (MIT) and himself65/finance-skills sepa-strategy"
  languages: "pl,en"
---

# GPW Trend Screener / Skaner trendu GPW

A GPW adaptation of the best-known US momentum screens: **Minervini's Trend
Template** (is the stock in a Stage 2 uptrend?), an **IBD-style RS rating**
(is it a leader?) and **VCP detection** (is it building a tight base with a
clear pivot?). It runs on every PLN share in the GPW archive, dividend/split
adjusted and survivorship-free, and returns a **watchlist ordered by
priority** — never a buy list.

## When to use

- "Which GPW stocks are leaders / in a strong uptrend / near a breakout?"
- "Does PKOBP pass the Trend Template? Is there a VCP? Where is the pivot?"
- "Has this kind of screen worked on GPW?" (`--history`)
- The user mentions Minervini, SEPA, VCP, CANSLIM, RS rating, pivot.

Not for: valuation or fundamentals, ETFs, US stocks, intraday trading, or
"should I buy X" (the answer is context plus risk numbers, the decision is the
user's). For beginners, suggest starting with `gpw-start` and explain jargon.

## Language

Answer in the user's language; pass `--lang pl` (default) or `--lang en`.

## Workflow

### 1. Check the market first (recommended)

Breakouts fail far more often in corrections. If a recent `gpw-market-regime`
JSON exists, the screener reads it automatically from `--input-dir` (default
`.`) or from `--regime-json PATH`. If none exists, the report says so; suggest
running `gpw-market-regime` / `gpw-market-breadth` and continue.

### 2. Run the script (never guess setups from memory)

Run from this skill's directory:

```bash
python3 scripts/gpw_trend_screener.py --lang pl --output-dir <dir>                 # screen, top 20
python3 scripts/gpw_trend_screener.py --ticker PKOBP --lang en --output-dir <dir>  # one stock in full
python3 scripts/gpw_trend_screener.py --history --lang pl --output-dir <dir>       # backtest vs WIG
```

| Input | Flag | Default / note |
|---|---|---|
| Rows in the table | `--top` | 20 |
| One stock | `--ticker NAME` | GPW short name: `PKOBP`, `PKNORLEN`, `CDPROJEKT`, `KGHM`, `KETY`… (an unknown name exits 1 with suggestions) |
| Backtest | `--history` | `--history-sessions 250 --history-step 5` |
| Liquidity | `--min-turnover-k` | 200 (thousand zł, 20-session median) |
| RS threshold | `--min-rs` | 70 (Minervini) |
| Criteria required | `--min-criteria 6\|7\|8` | 8 (all) |
| Fill sizer commands | `--account-size 50000 --risk-pct 1` | otherwise `ACCOUNT_PLN` placeholder |
| Regime file | `--regime-json PATH` / `--input-dir DIR` | auto-discovers `gpw_*regime*.json` |
| Data | `--as-of YYYY-MM-DD`, `--offline`, `--cache-dir`, `--sessions 500` | shares the cache with the other GPW skills |
| Files | `--no-files` | otherwise `gpw_trend_screener[_TICKER\|_history]_<timestamp>.{json,md}` |

The script prints the Markdown report. The first run downloads ~2 years of
sessions (one-off); later runs take ~1–2 s. Exit code 1 = data/input problem
(message on stderr): report it, do not invent numbers. 2 = bad flags.

### 3. Check freshness

If the report starts with a stale-data warning (`data_provenance.freshness.is_stale`),
say so first. Mention any regime warning (correction/bear) next.

### 4. Present the screen

1. **Answer first:** how many liquid stocks, how many pass the Trend Template,
   how many valid VCPs, how many in an actionable zone. If none — say that this
   is information about the market (weak or selective), not a failure.
2. **Watchlist table** (top 5–10): name, RS, contraction depths, pivot, state,
   distance to pivot, priority band, invalidation level and risk %.
3. For each `pre_breakout` / `breakout` name: the invalidation level (structure
   and ATR), risk % from the pivot, and the `gpw-position-sizer` command. If
   risk > 8%, say the stop is wide (O'Neil's 7–8% rule).
4. "Trend OK, no setup" list — leaders to watch until they build a base
   (do not chase `extended` names).
5. Market-context line: check `gpw-market-regime` / `gpw-market-breadth`.

### 5. Present a single stock (`--ticker`)

Verdict line, then the 8 Trend Template criteria with values, RS rating and
RS vs WIG (3/6/12 months), the contraction table with dates (invite the user to
compare with a chart), dry-up, state, score components, invalidation levels.
If the pre-filter fails (illiquid, < 1 zł, short history), say it first.

### 6. Present the backtest (`--history`)

Compare **flagged** vs **all Trend Template passers** vs the **whole liquid
universe** at 20 and 60 sessions: hit rate, mean and median, excess vs WIG.
Read the medians, not only the means (a few big winners lift the mean). Always
repeat the caveats: small, overlapping sample; one market regime; no costs or
stops; survivorship-free archive (no current-constituent bias).

### 7. Explain for less experienced users

- **Trend Template** — 8 checks that the stock is in a healthy, established
  uptrend (price above rising long-term averages, near its yearly high, far
  above its yearly low, stronger than most stocks).
- **RS 1–99** — 85 means the stock did better than ~85% of liquid GPW shares
  over the past year (recent quarter weighted double).
- **VCP** — each pullback smaller than the previous one, with turnover drying
  up: sellers are running out. **Pivot** = the price the stock must close above.
- **Invalidation** — the price at which the idea is wrong; the position size
  follows from the distance to it (gpw-position-sizer).

## Key rules

- Never say "buy X" or "this will rise". Use "on the watchlist", "near its
  pivot", "the setup is invalidated below …". The band is a priority for
  further research, not a signal.
- Every number comes from the script; never edit pivots, stops or scores.
- `extended` = do not chase; `damaged` / `invalid` = not a setup now.
- Always mention the market context, and stress that results depend on the
  regime.
- Fundamentals (earnings, sales growth — the other half of SEPA) are not
  checked; say so if the user asks about quality.
- Index/sector labels are current membership and cosmetic.

## References

- `references/methodology.md` — every threshold with its source: pre-filter,
  RS formula, Trend Template, swing-detection algorithm, VCP rules, execution
  states, score weights, bands, stops, backtest design, limitations. Load it on
  "how/why" questions or when a result looks surprising.

## Related skills

`gpw-market-regime` and `gpw-market-breadth` (is it a market for breakouts?),
`gpw-sector-trend` (which sectors lead), `gpw-position-sizer` (how many shares
for the pivot and stop), `gpw-pre-trade-gate` (checklist before an order),
`gpw-trade-journal` (record the idea with its pivot and invalidation),
`gpw-exposure-coach` (how much total risk now).
