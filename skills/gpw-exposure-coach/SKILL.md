---
name: gpw-exposure-coach
description: Sets how much risk to take on the Polish stock market (GPW, Warsaw Stock Exchange) right now. Combines the newest reports of gpw-market-regime, gpw-market-breadth and gpw-sector-trend with the user's gpw-trade-journal (losing streak, drawdown in R and PLN, open risk to stops) into a posture - offensive, normal, defensive or capital preservation - and four limits - maximum equity exposure, maximum risk per trade, maximum new positions per week and a circuit breaker (ok, reduce, pause) - with reasons per input and a confidence level. The market regime is a hard cap and the circuit breaker overrides risk per trade. Use when the user asks how much to invest now, how much to risk, or whether to slow down or pause after losses. PL triggers - "ile mieć w akcjach", "jaka ekspozycja", "ile ryzykować teraz", "seria strat", "czy zrobić przerwę", "obsunięcie kapitału". EN triggers - "how much exposure", "risk budget", "drawdown circuit breaker". Never recommends buying or selling a stock.
license: MIT
metadata:
  version: "1.0.0"
  derived_from: "tradermonty/claude-trading-skills exposure-coach, drawdown-circuit-breaker (MIT)"
  languages: "pl,en"
---

# GPW Exposure Coach / Trener ekspozycji GPW

Turns "what is the market doing?" and "how am I doing?" into **limits**:
how much of the account may be in stocks, how much one trade may risk, how
many new positions per week, and whether a losing streak or drawdown should
slow the user down or pause new trades. It reads the JSON reports of three
market skills and the trade journal; it never names a stock.

## When to use

- "Ile powinienem teraz mieć w akcjach?", "jaką ekspozycję trzymać?",
  "ile ryzykować na transakcję w tym rynku?", "mam serię strat — co robić?",
  "czy zrobić przerwę od tradingu?", "how much exposure should I run now?"
- Weekly planning; before `gpw-pre-trade-gate` (which reads these limits).

Not for: choosing stocks, valuation, tax questions, US markets.

## Language

Answer in the user's language and pass `--lang pl` (default) or `--lang en`.

## Workflow

### 1. Produce fresh market inputs (same directory)

Run, from each skill's directory, with one shared `<dir>`:

```bash
python3 scripts/gpw_breadth.py --output-dir <dir>         # gpw-market-breadth
python3 scripts/gpw_sector_trend.py --output-dir <dir>    # gpw-sector-trend
python3 scripts/gpw_market_regime.py --input-dir <dir> --output-dir <dir>   # gpw-market-regime
```

If the user already has fresh reports (≤ 3 sessions old), reuse them.
Add `--offline` to each when working from the cache only.

### 2. Run the coach

```bash
python3 scripts/gpw_exposure_coach.py --input-dir <dir> --account-size 100000 --lang pl --output-dir <dir>
```

| Input | Flag | Default |
|---|---|---|
| Report folder (newest `gpw_market_regime_*`, `gpw_breadth_*`, `gpw_sector_trend_*`) | `--input-dir` | `.` |
| Explicit files | `--regime-json`, `--breadth-json`, `--sector-json` | auto |
| Account value (enables % drawdown and heat) | `--account-size` PLN | none (R units only) |
| Normal risk per trade | `--base-risk-pct` | 1.0 |
| Journal | `--state-dir` / `--no-journal` / `--include-paper` | `~/.gpw-trading-skills/journal` |
| Pause length | `--pause-days` | 10 |
| Evaluation date | `--as-of YYYY-MM-DD` | today |
| Output | `--output-dir`, `--no-files` | `.` |

Ask for the account size if the user has not given it (heat and % drawdown
need it); the coach still runs without it. Exit code 1 = no market input at
all, or an explicitly given file is missing/invalid: tell the user which
skills to run first; never guess a posture.

### 3. Present (the Markdown "Summary" is the one-page answer)

1. **Stale/missing input warnings first** (top of the report).
2. **Posture in plain words** and the four limits table (ceiling %, risk per
   trade % and in PLN, new positions per week, circuit breaker).
3. **Why** — one line per input (regime, breadth, sectors, journal, open
   risk), then every entry of `caps_applied` (e.g. "correction caps exposure
   at 25%", "3 losses in a row: risk halved").
4. **Confidence** and what would raise it (refresh a stale report, add the
   missing one, give the account size).
5. If the circuit breaker is `pause`: give `pause_until` and suggest
   `gpw-trade-review` to study the losing trades. If `reduce`: explain it
   lifts after a winning trade / when drawdown recovers.
6. If current exposure is above the ceiling or heat is above its cap: say
   that new positions are on hold; do **not** tell the user to sell —
   tightening stops or simply not adding are their options to consider.

### 4. Frame it correctly

- These are limits for the user's own decisions, not buy/sell signals.
- Feed `max_risk_per_trade_pct` into `gpw-position-sizer` for a concrete
  trade; `gpw-pre-trade-gate` checks a trade idea against all limits.

## Key rules (details and sources in references/methodology.md)

- Score = 40% regime (0.6 × state score + 0.4 × (100 − top risk)) + 35%
  breadth + 25% sectors; renormalised when an input is missing.
- Score ≥ 70 offensive, ≥ 50 normal, ≥ 30 defensive, else capital
  preservation; ceiling 10% → 30% → 60% → 85% → 100% at scores 0/30/50/70/85.
- Regime hard cap: confirmed 100%, under pressure 80% (max normal), rally
  attempt 40% (max defensive), correction 25% (max defensive), bear 15%
  (capital preservation); top risk ≥ 70 → 60%, ≥ 85 → 40%.
- Risk per trade = base × 1 / 0.75 / 0.5 / 0.25; new positions per week
  5 / 3 / 1 / 0 by posture.
- Circuit breaker: 3 losses in a row or drawdown ≥ 5% (5R) → reduce (risk
  × 0.5); 5 in a row or ≥ 10% (10R) → pause N days, then reduce until a win.
- Heat cap 8 / 6 / 4 / 2% by posture; heat or exposure above the limit →
  0 new positions.
- Inputs > 3 sessions old are stale and count half for confidence. No market
  input at all → exit 1 (documented choice).

## JSON contract

`{"skill": "gpw-exposure-coach", "posture": ..., "limits":
{"exposure_ceiling_pct": float, "max_risk_per_trade_pct": float,
"max_new_positions_per_week": int, "circuit_breaker": "ok|reduce|pause"},
"as_of": ...}` plus `score`, `confidence`, `reasons`, `caps_applied`,
`inputs` (file, as_of, sessions_behind, stale), `circuit_breaker`, `heat`,
`data_provenance`.

## References

- `references/methodology.md` — weights, caps, circuit-breaker and heat
  thresholds with sources, confidence, limitations.

## Related skills

`gpw-market-regime`, `gpw-market-breadth`, `gpw-sector-trend` (inputs),
`gpw-trade-journal` (source of results and open positions),
`gpw-trade-review` (study a losing streak), `gpw-position-sizer` (apply the
risk limit to a trade), `gpw-pre-trade-gate` (checks a trade idea against
these limits).
