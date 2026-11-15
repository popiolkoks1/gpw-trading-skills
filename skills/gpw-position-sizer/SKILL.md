---
name: gpw-position-sizer
description: Calculates how many shares to buy on the Warsaw Stock Exchange (GPW) so that a losing trade costs no more than a planned % of the account. It works in PLN with whole shares, includes broker commissions with minimum fees (mBank, XTB, Bossa, PKO BP, Santander or custom), caps the position by liquidity, shows a price-gap scenario, the R multiple and profit after Polish tax for regular, IKE, IKZE and OKI accounts. Use it whenever the user asks how much to buy, how big a position should be, where to put the stop, risk per trade, ATR or Kelly sizing for Polish stocks, even if they don't say "position sizing". Triggers (PL) "ile akcji kupić", "wielkość pozycji", "ile zaryzykować", "kalkulator pozycji", "gdzie stop loss", "ryzyko na transakcję", "ile mogę stracić", "zarządzanie kapitałem"; (EN) "how many shares", "position size", "risk per trade", "stop loss sizing" for GPW tickers like PKOBP, CDPROJEKT or ORLEN.
license: MIT
metadata:
  version: "1.0.0"
  derived_from: "tradermonty/claude-trading-skills position-sizer (MIT)"
  languages: "pl,en"
---

# GPW Position Sizer

Risk-first position sizing for **long** trades on GPW. The core idea: first decide
how much you are willing to lose if the stop is hit, and the share count follows
from that. The script computes; you explain.

## When to use

- The user wants to know how many shares to buy or how big a position to take.
- The user names an entry and a stop (or asks where to put a stop) for a Polish stock.
- The user asks about risk per trade, the 1% rule, ATR stops or the Kelly criterion.
- The user plans a trade and wants to see the commissions, the R multiple or the after-tax result.

Do **not** use for: short selling, futures or options (not supported), portfolio
allocation across ETFs (no single stop), or "which stock should I buy" (this
skill never picks stocks).

## Language

Answer in the user's language. Pass `--lang pl` for Polish and `--lang en` for English.

## Workflow

### 1. Collect the inputs

Run once you know at least the **account size** and either an **entry + stop**, or
a **ticker** (the ATR stop is then computed automatically). Ask only for what is missing:

| Input | Flag | Default / note |
|---|---|---|
| Account value in PLN | `--account-size` | required |
| GPW short name | `--ticker PKOBP` | pulls last close, ATR(14), 20-session median turnover from the GPW archive |
| Entry price | `--entry` | defaults to last close with `--ticker` |
| Stop price | `--stop` | or ATR stop: `--atr-multiplier 2` × ATR(14) below entry |
| Target | `--target` | enables profit, R multiple and after-tax view |
| Risk % per trade | `--risk-pct` | 1.0 (beginners: 0.5–1%) |
| Broker | `--broker mbank\|xtb\|bossa\|pkobp\|santander\|custom` | mbank; override with `--commission-pct`, `--commission-min` |
| Account type | `--account-type regular\|ike\|ikze\|oki` | regular |
| Kelly mode | `--win-rate 0.45 --avg-win 3 --avg-loss 1` | half-Kelly, capped at 2% risk; excludes `--risk-pct` |

GPW short names are the exchange's own codes, e.g. `PKOBP`, `PKNORLEN`, `CDPROJEKT`,
`KGHM`, `PZU`, `ALLEGRO`, `LPP`, `DINOPL`, `11BIT`. If a ticker is not found, the error
says so. Ask the user for the GPW short name; never guess prices.

### 2. Run the script

Run from this skill's directory:

```bash
python3 scripts/position_sizer.py --account-size 20000 --entry 50 --stop 46 --target 60 --lang pl
python3 scripts/position_sizer.py --account-size 50000 --ticker PKOBP --target 135 --broker xtb --lang en
```

The script writes `gpw_position_size_<timestamp>.json` and `.md` to `--output-dir`
(default: the current directory) and prints the Markdown. Use `--no-files` for a quick answer.
The first `--ticker` run downloads about 40 sessions (roughly 1 minute); after that the cache is used.
`--offline` uses the cache only.

Exit codes: `0` ok, `1` data or input error (the message is on stderr, e.g. stop above entry, unknown ticker), `2` bad flags.

### 3. Present the result

Lead with the answer in one sentence: "**45 akcji** (~2 250 zł). If the stop is hit
you lose about 197 zł including commissions, which is 0.98% of the account." Then:

1. **Why that many:** the risk budget, the distance to the stop, and which constraint was binding (risk, cash, 25% max position, liquidity).
2. **Gap scenario:** a stop is not a guaranteed price. Show the loss if the price gaps 10% below the stop.
3. **Reward:** the R multiple and the after-tax profit at the target, if one was given. Below 1.5R, say plainly that the trade pays poorly for its risk.
4. **Warnings:** report every warning, especially illiquidity, the minimum-fee drag, stale data and the XTB turnover cap.
5. **Account-type tax note**, in one line.

Explain jargon the first time it appears (R multiple, ATR, stop loss) in plain words.
Do not describe the result as a recommendation to buy.

## Key rules (built into the script)

- **Whole shares only.** The GPW lot is 1 share.
- **Risk includes round-trip commissions.** That means the entry fee plus the exit fee at the stop, each with the broker's minimum fee. The share count is the largest whole number whose total risk fits the budget.
- **Constraints, strictest wins:** risk budget, cash (no leverage), max 25% of the account in one position (`--max-position-pct`), and max 10% of median daily turnover (`--max-adv-pct`). An optional sector limit is also available.
- **Gap risk:** GPW static price threshold is ±10%. The default gap scenario uses 10%.
- **Kelly** is applied as **half-Kelly** and capped at 2% risk per trade, because full Kelly is too aggressive for estimates based on a few trades.

The reasoning behind each threshold is in `references/sizing_methodology.md`. Read it
when the user asks why, or on first use.

## Related skills

- `gpw-market-breadth` / `gpw-sector-trend`: lower `--risk-pct` (e.g. 0.5%) when market breadth is weak.
- `gpw-trade-journal`: after sizing, offer to record the plan as a thesis, including the stop, target and kill criteria.
