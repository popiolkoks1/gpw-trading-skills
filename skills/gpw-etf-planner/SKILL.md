---
name: gpw-etf-planner
description: Helps a beginner build and maintain a simple ETF portfolio from funds listed on the Warsaw Stock Exchange (GPW). Lists all GPW ETFs with category, issuer, currency exposure, OKI-qualification hint and live stats from the official GPW archive (price, liquidity, 1-year return, volatility, max drawdown) and flags leveraged and inverse ETFs. For a target allocation and a monthly amount it computes a whole-unit buy list that uses new money to move toward the target, fees with minimum commissions, how often to buy so fees stay under 0.5%, a 5/25 rebalancing check and portfolio risk; it also backtests monthly buying vs a lump sum. Use when asked which GPW ETF or how many units to buy. Triggers (PL) "jakie ETF-y na GPW", "ile jednostek ETF kupić", "portfel ETF", "ETF na WIG20 i MSCI World", "rebalancing", "uśrednianie DCA"; (EN) "GPW ETF list", "ETF portfolio Poland", "monthly ETF buy plan". Not for single stocks or choosing IKE/IKZE/OKI (use gpw-account-planner).
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
---

# GPW ETF Planner

For **beginners** who want to invest regularly in ETFs listed on GPW. Three modes:
`list` (what is available), `plan` (what to buy this month for *their* target mix),
`backtest` (monthly buying vs lump sum on past data). Scripts compute; you explain
in plain words. Never pick funds for the user — explain the options and the numbers.

## When to use

- "What ETFs can I buy on GPW?", "Is there an ETF on S&P 500 / MSCI World / WIG20 / bonds?"
- "I have 1000 zł a month, 30% Polish and 70% world ETF — how many units do I buy?"
- "Should I buy every month or every few months?" (fees), "Do I need to rebalance?"
- "How would monthly buying have worked over the last two years?"

Do **not** use for: single shares (`gpw-position-sizer`), which tax account to use
(`gpw-account-planner`), or market timing.

## Language

Answer in the user's language: `--lang pl` (default) or `--lang en`.

## Workflow

### 1. Inputs

| Input | Flag | Note |
|---|---|---|
| Mode | `list` / `plan` / `backtest` | subcommand |
| Category filter (list) | `--category pl_equity` | pl_equity, global_equity, us_equity, other_equity, mixed, pl_bonds, cash, gold_commodity, crypto, thematic, leveraged, short |
| Target allocation | `--allocation ETFBW20TR=30,ETFIWDA=70` | GPW short names, weights in % (normalised to 100) |
| Monthly amount | `--monthly 1000` | PLN |
| Current holdings (plan) | `--holdings ETFBW20TR=12,ETFIWDA=3` | units |
| Uninvested cash (plan) | `--cash 150` | PLN already on the account |
| Broker | `--broker mbank\|xtb\|bossa\|pkobp\|santander\|custom` | default mbank; override `--commission-pct`, `--commission-min` |
| Rotation | `--max-orders 1` | buy only the most underweight ETF each month (one fee) |
| Known TERs | `--ter ETFBW20TR=0.5,ETFIWDA=0.2` | from the KID; never guessed |
| Data | `--offline`, `--cache-dir`, `--as-of YYYY-MM-DD`, `--sessions 500` | |

If the user has no allocation yet, run `list` first and explain the categories; let
the user choose. A common beginner structure to *explain* (not prescribe) is: a broad
equity part (Polish and/or global) plus a bond/cash part sized to how big a fall they
can stomach.

### 2. Run

From this skill's directory:

```bash
python3 scripts/gpw_etf_planner.py list --lang pl
python3 scripts/gpw_etf_planner.py plan --allocation ETFBW20TR=30,ETFIWDA=70 --monthly 1000 --broker mbank
python3 scripts/gpw_etf_planner.py plan --allocation ETFBW20TR=30,ETFIWDA=60,ETFBTBSP=10 --monthly 1000 --holdings ETFBW20TR=40,ETFIWDA=5 --cash 150 --max-orders 1
python3 scripts/gpw_etf_planner.py backtest --allocation ETFBW20TR=30,ETFSP500=70 --monthly 1000 --lang en
```

The first run downloads ~500 ETF sessions from the GPW archive (several minutes,
rate-limited); later runs use the cache (`--offline` = cache only). Output:
`gpw_etf_{list,plan,backtest}_<timestamp>.{json,md}` in `--output-dir`; `--no-files`
only prints. Exit codes: 0 ok, 1 data/input error (e.g. unknown ETF name), 2 bad flags.

### 3. Present the result

- **Stale data first:** if the report starts with a stale-data warning, say so.
- **list:** lead with the plain answer (how many ETFs, which kinds suit a beginner),
  then the relevant category tables. Explain liquidity, volatility and max drawdown
  in one sentence each. Always warn that **LV/ST (leveraged/inverse) ETFs are not for
  long-term holding**. Say TER must be checked in the KID.
- **plan:** lead with the buy list in one sentence ("3 units of ETFBW20TR and 1 unit of
  ETFIWDA, fees 10 zł, 134,86 zł stays for next month") and say it follows from the
  user's own target, not a recommendation. Then: fee drag per order and the
  suggested buying frequency (monthly vs every 2–3 months vs rotation), the
  rebalancing status, the risk summary (equity share, max drop per 10 000 zł,
  currency exposure) and the very short history caveat. Mention the OKI-qualified
  share and offer `gpw-account-planner` (the report prints the command).
- **backtest:** start with the caveat that ~2 years says almost nothing; then DCA vs
  lump sum final values; explain why a lump sum usually wins in rising markets and why
  DCA is still the natural way to invest a salary.

Explain jargon the first time: ETF, TER, KID, jednostka, DCA, rebalancing,
zmienność, obsunięcie (drawdown), hedging walutowy, ETF lewarowany/odwrotny.

## Key rules (built into the script)

- Whole units only; commission per order = max(pct × value, minimum fee).
- New money first (cash-flow rebalancing); the planner never sells.
- Fee threshold 0.5% per order (= a year of typical TER); with a 5 zł minimum the
  order should be ≥ 1 000 zł → buy less often, use fewer ETFs, or rotate.
- Rebalancing band = min(5 pp, 25% of target) — the 5/25 rule.
- Leveraged/inverse detected from names (LV, 3LV, ST, 2ST) even for new funds.
- OKI hint "likely qualified" only for Polish shares / PLN bonds / PLN cash funds; always "verify in the statute".
- TER is never invented; unknown ETFs are "uncategorised".

Thresholds and formulas: `references/methodology.md`. The fund list with
issuers, underlyings, currency and OKI hints: `references/etf_catalog.md`.

## Related skills

- `gpw-account-planner` — IKE / IKZE / OKI / regular account for these ETFs (pass `--oki-qualified-pct`).
- `gpw-market-breadth`, `gpw-sector-trend` — context on the Polish market (not a timing signal for a monthly plan).
- `gpw-trade-journal` — record each purchase; `gpw-trade-review` — Belka tax when selling.
