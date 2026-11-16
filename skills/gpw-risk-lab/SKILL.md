---
name: gpw-risk-lab
description: Stress test and futures hedge for a Polish stock portfolio (GPW, Warsaw Stock Exchange). Replays today's holdings through real GPW crises - 2008, 2011 euro crisis, 2013 OFE reform, 2015 Swiss franc shock, 2020 COVID, 2021-22 war and inflation, worst 1-day and 5-day WIG falls - with actual returns or a labelled index x beta proxy, plus factor shocks (WIG -10 to -40%, banks -25%, zloty -15%, rates +200 bp) from an editable scenario file; PLN loss, worst positions, liquidity under stress and the user's loss limit. Hedge mode sizes a WIG20 futures (FW20) hedge from portfolio beta - contracts, residual risk, basis, roll dates, margin estimate, tax notes, backtest, inverse ETF for small portfolios. PL triggers - "ile stracę jak w 2008", "test warunków skrajnych", "jak zabezpieczyć portfel", "ile kontraktów FW20". EN triggers - "stress test my GPW portfolio", "hedge with WIG20 futures". Not for picking stocks or placing orders.
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
  derived_from: "tradermonty/claude-trading-skills scenario-analyzer and futures-position-sizer (MIT), rebuilt as deterministic numbers on GPW data"
---

# GPW Risk Lab / Laboratorium ryzyka GPW

Answers two questions about a portfolio of GPW shares and ETFs:

1. **"What can happen to my portfolio?"** (`stress`) – today's holdings
   replayed through real Polish market crises and hypothetical shocks, in PLN.
2. **"How can I protect it?"** (`hedge`) – how many FW20 (WIG20 futures)
   contracts offset the market risk, what they cost in margin, what risk stays,
   and what a small portfolio can use instead.

Workflow position: fed by the user's holdings (broker CSV, `gpw-trade-journal`
or an inline list – the same inputs as `gpw-portfolio-check`); feeds
`gpw-exposure-coach` and `gpw-pre-trade-gate` decisions (how much equity risk
the user can carry) and the weekly risk review. Stress results drive the hedge
size (`all` mode shows each loss with and without the hedge).

## When to use

- The user asks how much they could lose in a crash / "like 2008" / if banks
  or the zloty fall, or whether their portfolio fits their loss tolerance.
- The user asks how to hedge or protect a GPW portfolio, about FW20 / WIG20
  futures, how many contracts, margin, rolling, or inverse ETFs.

## Language

Answer in the user's language; pass `--lang pl` (default) or `--lang en`.

## Workflow

### 1. Inputs

| Input | Flag |
|---|---|
| Broker CSV | `--csv FILE` – `ticker,shares[,avg_price][,account_type]`; `;`/tab/`,`, decimal commas, Polish headers (same format as gpw-portfolio-check) |
| Journal | `--journal` (alias `--from-journal`) `[--state-dir DIR]` – ACTIVE positions, FIFO lots |
| Inline list | `--positions PKOBP:100,KGHM:30,DINO:10` (whole shares) |
| Cash | `--cash 10000` (PLN, return 0 in every scenario) |
| Loss tolerance | `--max-loss-pct 20` – the most the user says they can lose (% of the whole portfolio) |
| Own scenarios | `--scenarios my.json` (copy `scripts/risk_lab_scenarios.json` and edit), `--only gfc_2008,wig_-30` |

If the user lists holdings in chat, use `--positions`; ask for share counts if
only PLN amounts are given – never invent them. Tickers are GPW short names;
common 3-letter codes (PKO, PKN, CDR, KGH, DNP...) are mapped and reported.

### 2. Run (from the skill directory)

```bash
python3 scripts/gpw_risk_lab.py stress --positions PKOBP:100,KGHM:30,CDPROJEKT:20,DINO:10,KETY:5 --cash 10000 --max-loss-pct 20 --output-dir <dir>
python3 scripts/gpw_risk_lab.py hedge --csv holdings.csv --lang en --output-dir <dir>
python3 scripts/gpw_risk_lab.py all --journal --max-loss-pct 15 --output-dir <dir>
```

Other flags: `--as-of YYYY-MM-DD`, `--offline`, `--cache-dir`, `--lookback`
(beta window, default 252 sessions), `--participation` (default 20%),
`--stress-turnover` (default 50% of normal), `--no-files`.

**History before 2019.** The 2008, 2011, 2013 and 2015 replays need GPW index
history back to 2007. It is read from the local cache; if it is missing those
scenarios are reported as skipped. `--fetch-history` downloads it once
(~3 000 polite requests, about an hour) – tell the user before running it.
Shares before 2019 are never downloaded: older windows use index proxies.

First online run also downloads EURPLN history from NBP (~80 small requests)
and the Polish 10-year yield from the ECB (one request); later runs use the cache.

Exit 1 = input or data problem (message on stderr): relay it, do not guess.

### 3. Present

1. If `data_provenance.freshness.is_stale` – say so first.
2. Lead with the plain answer from "W skrócie / In short", e.g. *"W scenariuszu
   2008 Twój portfel straciłby ~X zł (−Y%)"*, then the worst scenario and the
   loss-tolerance verdict.
3. Tables: historical replays (window dates, WIG move, portfolio %, PLN loss,
   worst positions), factor shocks, then liquidity under stress.
4. Always say **how each position was computed** in the old crises (method
   column: A actual, S sector index × beta, R size index × beta, W WIG × beta):
   a stock that did not exist in 2008 is an estimate, not history.
5. Hedge: contracts rounded **down** (and the round-up alternative), hedge
   ratio achieved, residual (non-WIG20) risk, basis, roll dates, the margin
   **estimate** (always add "check with your broker"), the backtest, tax notes.
   For portfolios below one contract, present the inverse ETF and the cash
   options as alternatives, with their caveats.
6. Explain jargon on first use: beta ("how much your portfolio moves when the
   index moves 1%"), hedge ratio, basis, rolling, margin, residual risk.

### 4. Caveats to mention when relevant

- Replays apply **today's weights** to past windows; sector membership is the
  current GPW Benchmark list. Proxied positions are estimates.
- Factor shocks are beta × shock (conditional expectation); real crashes can be
  worse because correlations rise. FX and rate sensitivities come from
  regressions and include the whole-market reaction; low |t| = weak evidence.
- The hedge removes only the WIG20-related part of the risk; mid and small caps
  keep most of their own risk. A short futures position loses money when the
  market rises, needs margin top-ups and must be rolled every quarter.
- Futures P&L goes to PIT-38 with shares (offsets gains in the same year).
  IKE/IKZE: the act allows non-security instruments only to limit risk and only
  if the broker's agreement provides for it; in practice brokers rarely offer
  futures there – a hedge usually sits on a regular account (tax asymmetry).

## Key rules

- Never say "buy", "sell" or "short X"; present numbers and options for the
  user's decision. "Contracts needed" is arithmetic, not a recommendation.
- Every number comes from the script; quote the session date and the window dates.
- Never present the margin estimate as the broker's figure.
- Report ends with the disclaimer (the script appends it).

## Output contract (JSON)

`stress.scenarios[]`: `{id, group, label, window|shock, portfolio_return_pct,
pln_loss (positive = loss), worst_positions[], positions[], skipped, reason,
pln_loss_hedged (all mode)}`; `stress.liquidity`, `stress.tolerance`,
`stress.sensitivities`. `hedge`: `{beta, beta_wig, contracts (floor),
contracts_ceil, contracts_exact, notional_pln, residual_risk_pct,
futures_symbol, futures_price, multiplier, basis_points, series[],
roll_schedule[], margin_estimate, backtest, alternatives}`. Envelope: `skill`,
`version`, `lang`, `as_of`, `data_provenance`.

## References

- `references/methodology.md` – scenarios and why these windows, proxy chain,
  factor regressions, liquidity, beta, minimum-variance hedge (Ederington
  1979), roll method, margin model, tax and IKE rules, contract specification.
- `scripts/risk_lab_scenarios.json` – the default scenarios (editable copy).

## Related skills

`gpw-portfolio-check` (health check of the same portfolio: concentration, VaR),
`gpw-exposure-coach` (how much equity risk to carry now), `gpw-market-regime`
(is the market in a correction), `gpw-etf-planner` (ETF list incl. inverse
ETFs), `gpw-position-sizer` and `gpw-pre-trade-gate` (before adding risk),
`gpw-trade-journal` (feeds `--journal`), `gpw-trade-review` (PIT-38 estimate).
