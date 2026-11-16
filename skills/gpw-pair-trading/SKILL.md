---
name: gpw-pair-trading
description: Relative-value (pair) analysis for Polish stocks on the Warsaw Stock Exchange (GPW). Tests whether two shares move together in a stable way - Engle-Granger cointegration (ADF, MacKinnon p-values), hedge ratio, half-life, rolling stability, spread z-score, liquidity and GPW single-stock futures for each leg. Scans pairs inside GPW sector indices (banks, fuels, energy ...) with a Benjamini-Hochberg multiple-testing correction, analyses one pair with an illustrative backtest, and tells a holder of one stock whether a peer is relatively cheap (long-only switch). For advanced users. PL triggers - "PKO czy Pekao", "czy zamienić akcje na inną spółkę z sektora", "para spółek", "pair trading GPW", "kointegracja", "spread między bankami", "która spółka relatywnie tańsza". EN triggers - "pair trading Poland", "cointegrated GPW stocks", "relative value PKO vs Pekao", "hedge one bank with another". Not a buy or sell recommendation, not for valuation or US stocks.
license: MIT
metadata:
  version: "1.0.0"
  derived_from: "tradermonty/claude-trading-skills pair-trade-screener (MIT)"
  languages: "pl,en"
---

# GPW Pair Trading / Wartość względna par GPW

Measures whether two GPW shares have a **stable price relationship** and where the
spread between them stands today. Framed honestly for Poland: shorting single stocks is
hard for retail investors (stock loan via the broker, limited lists, borrow fee), so the
realistic uses are:

1. **Switch decision within a sector** — "I hold PKOBP; is PEKAO relatively cheap?"
   (long-only, works in IKE/IKZE too).
2. **Hedge leg** — the hedge ratio and the list of GPW single-stock futures (FPKO…,
   FPEO…, FPKN…) or WIG20 futures.
3. **Spotting dislocations** — a large z-score on a pair that historically reverted.

**Workflow position:** fed by the GPW archive and sector membership (optionally by
`gpw-sector-trend` to choose a sector); feeds `gpw-signal-postmortem` (generic
`signals[]`, kind `pair_dislocation`), `gpw-risk-lab` (hedge sizing),
`gpw-pre-trade-gate` and `gpw-trade-journal` (record a switch thesis).

## When to use

- "PKO czy Pekao?", "czy zamienić X na Y?", "która spółka z sektora jest relatywnie tańsza?"
- "Find cointegrated pairs among Polish banks / fuels / energy."
- "How many PEKAO shares hedge my PKO position?" (hedge ratio; futures availability)

Not for: valuation (`gpw-valuation`), single-stock trend (`gpw-trend-screener`),
portfolio risk (`gpw-portfolio-check`), index hedging size (`gpw-risk-lab`).

## Language

Answer in the user's language; pass `--lang pl` (default) or `--lang en`.

## Workflow

### 1. Pick the mode and run the script (never estimate statistics yourself)

Run from the skill directory:

```bash
# scan pairs inside sectors (aliases: banks/banki, fuels/paliwa, energy/energetyka, games/gry, it, ...)
python3 scripts/gpw_pair_trading.py scan --sector banks --lang pl --output-dir <dir>
python3 scripts/gpw_pair_trading.py scan --sector fuels,energy --cross --output-dir <dir>
# one pair, full analysis (A first, B second)
python3 scripts/gpw_pair_trading.py pair PKOBP PEKAO --output-dir <dir>
# holder of A asks about B
python3 scripts/gpw_pair_trading.py switch PKOBP PEKAO --position-value 20000 --output-dir <dir>
```

| Input | Flag | Default |
|---|---|---|
| Tickers | GPW archive short names (`PKOBP`, `PEKAO`, `PKNORLEN`, `ERSTEPL`, `TAURONPE`) | — |
| Sectors (scan) | `--sector a,b`, `--cross`, extra `--pair A,B` (repeatable) | all 14 sectors |
| Multiple-testing correction | `--correction bh|bonferroni|none` | `bh` |
| Windows | `--lookback` (test), `--z-lookback` | 500, 60 sessions |
| Bands | `--entry-z`, `--exit-z`, `--stop-z` | 2, 0, 3.5 |
| Filters | `--min-corr` (daily returns), `--alpha` | 0.70, 0.05 |
| Money | `--position-value` per leg, `--broker` / `--commission-pct` / `--commission-min`, `--borrow-pct` (assumed short-leg fee) | 10 000 zł, mbank, 0 |
| Data | `--offline`, `--cache-dir`, `--as-of YYYY-MM-DD`, `--sessions` | online, 750 |
| Output | `--no-files` (print Markdown only) | writes `gpw_pair_trading_*.json/.md` |

Exit code 1 = data problem (unknown ticker, no data): report it, do not invent numbers.
Exit code 2 = bad arguments.

### 2. Check freshness

If `data_provenance.freshness.is_stale` is true, say so first.

### 3. Present (lead with the answer)

- **scan**: how many pairs were tested (`tests`), how many pass after correction
  (`summary.cointegrated`), then the top pairs table. "No pair passed" is a common,
  honest result — say it plainly and explain that high correlation is not a stable
  relationship. Mention why the correction matters (`expected_false_at_alpha_raw`).
- **pair**: one sentence verdict (cointegrated or not, p-value, half-life), today's z
  and percentile, then stability and hedge-ratio drift (a drifting β or stability below
  ~50% means the relationship is fragile), then the backtest table **labelled
  illustrative**, then implementation (futures, WIG20 beta, shorting reality).
- **switch**: the verdict from `switch.verdict` (`b_relatively_cheap`,
  `b_cheap_unstable`, `b_relatively_expensive`, `no_dislocation`,
  `extreme_deviation`), the switch cost (`switch_cost_pln`) and the tax note (19% Belka
  on a gain in a regular account; none in IKE/IKZE).
- Explain jargon once in plain words: *kointegracja* = the gap between the two prices
  tends to come back; *z-score* = how many "usual deviations" the gap is from its
  average; *półokres* = how many sessions it typically takes to close half of the gap.

### 4. Frame it correctly

- Never say "buy X, sell Y". Say "B is relatively cheap vs A against their history".
- A z-score on a pair that is **not** cointegrated only describes recent relative
  performance; it gives no reason to expect reversion.
- |z| ≥ 3.5 (`beyond_stop` / `extreme_deviation`) suggests the relationship may have
  changed — look for the cause (results, dividend, regulation) first.
- Short legs: availability and borrow cost are assumptions; single-stock futures have
  thin turnover and stock-specific multipliers (check the GPW specification); futures
  are not allowed in IKE/IKZE.

## Output JSON (key fields)

`skill`, `version`, `lang`, `mode`, `as_of`, `data_provenance`, `config`;
`pairs[]` `{a, b, hedge_ratio, adf_stat, p_value, p_adj, tests, coint_pass,
half_life_days, z, z_percentile, corr, corr_price, stability, liquidity_ok, state,
episode_start, hedge_drift{}, rel_perf_a_vs_b_pct{}, backtest{}, series{z, spread},
hedge{futures, beta_wig20}}`; `signals[]` (`kind: "pair_dislocation"`, `direction:
"long"` for the cheap leg only, `tags.pair`, `tags.rich_leg`); scan adds `summary`,
`skipped`; switch adds `switch{verdict, switch_cost_pln}`.

## Key rules

- Scripts compute, Claude interprets. Every number comes from the JSON.
- Use the multiple-testing-corrected `p_adj` / `coint_pass` in scans; never cherry-pick
  the lowest raw p-value from a scan.
- Mention the data date and that sector membership is today's GPW Benchmark portfolio.
- End with the disclaimer printed by the script.

## References

- `references/methodology.md` — formulas, MacKinnon coefficients, thresholds with
  sources, GPW short-selling and futures facts, backtest assumptions, limitations.
  Load it when the user asks "how" or "why", or questions a threshold.

## Related skills

`gpw-sector-trend` (which sector is moving), `gpw-risk-lab` (WIG20 futures hedge and
stress tests), `gpw-backtester` (systematic strategy tests), `gpw-position-sizer` and
`gpw-pre-trade-gate` (before acting on a switch), `gpw-trade-journal` (record the thesis),
`gpw-signal-postmortem` (measure `pair_dislocation` signals later).
