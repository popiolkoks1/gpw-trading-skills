---
name: gpw-pre-trade-gate
description: Fail-closed discipline checklist run BEFORE buying a Polish stock or ETF on GPW (Warsaw Stock Exchange). Checks stop below entry, risk incl. round-trip broker fees vs max risk per trade, position size vs account, order vs median daily turnover, portfolio heat after the trade, sector concentration, a written journal thesis with kill criteria, reward/risk, circuit breaker after a losing streak or drawdown, market regime and exposure limits from gpw-market-regime / gpw-exposure-coach, and data freshness. Verdict GO, GO WITH CONDITIONS or NO-GO with reasons. Never places orders. Use when the user is about to enter a trade or asks whether a planned buy is OK. PL triggers - "chcę kupić", "czy mogę wejść", "sprawdź transakcję przed zakupem", "checklista przed wejściem", "czy to dobry pomysł kupić teraz". EN triggers - "pre-trade check", "should I take this trade", "go/no-go before buying". Not for stock picking, valuation or sizing alone (use gpw-position-sizer).
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
  derived_from: "tradermonty/claude-trading-skills pre-trade discipline gate (MIT), adapted to GPW"
---

# GPW Pre-Trade Gate / Bramka przed transakcją

A checklist an experienced trader runs before pressing "buy": is the risk
defined and small, is the position not too big for the account or for the
stock's liquidity, is the total risk of the book under control, is there a
written thesis with a way to be proven wrong, does the trade pay enough for
its risk, am I tilting after losses, and is the market environment hostile.
It is **fail-closed**: a missing stop or a missing written thesis is a FAIL,
not a pass.

## When to use

- The user describes a planned buy (ticker, entry, stop, shares) or just ran
  `gpw-position-sizer` and wants to know if the trade is OK.
- Before recording a buy fill in `gpw-trade-journal`.

## Language

Answer in the user's language; pass `--lang pl` (default) or `--lang en`.

## Workflow

### 1. Inputs

| Input | Flag | Notes |
|---|---|---|
| Ticker, entry, stop, shares | `--ticker --entry --stop --shares` | or `--sizer-json` (gpw-position-sizer report); explicit flags override the JSON |
| Account size | `--account-size` | PLN; from the sizer JSON if given |
| Target | `--target` | optional; else the thesis plan target |
| Account type | `--account-type regular/ike/ikze/oki` | shown in the report |
| Thesis | `--thesis-id` | id or unique prefix; default: the single open thesis for the ticker |
| Journal | `--state-dir` | default `~/.gpw-trading-skills/journal` / `$GPW_JOURNAL_DIR` |
| Regime / exposure | `--regime-json`, `--exposure-json` | default: newest matching `gpw_*.json` in `--input-dir` (default `.`) |
| Broker fees | `--broker`, `--commission-pct`, `--commission-min` | default mbank preset (or the sizer's tariff) |
| Override | `--override "written reason"` | only lifts the bear-market FAIL; ≥ 15 characters; recorded |

If the stop or the share count is unknown, ask for it or run
`gpw-position-sizer` first – never invent a stop.

### 2. Run (from the skill directory)

```bash
python3 scripts/pre_trade_gate.py --ticker PKOBP --entry 120 --stop 113 --shares 60 --target 138 --account-size 50000 --lang pl --output-dir <dir>
python3 scripts/pre_trade_gate.py --sizer-json gpw_position_size_2026-11-15_101500.json --thesis-id th_pkobp --input-dir <dir>
```

Flags: `--offline`, `--cache-dir`, `--as-of`, limit overrides
`--max-risk-pct --max-position-pct --max-adv-pct --max-heat-pct
--max-sector-pct --min-rr`, `--no-files`. Exit 0 = report produced (whatever
the verdict); 1 = input/data error (e.g. unknown ticker with close matches);
2 = bad arguments.

### 3. Present

1. Verdict first: **GO / GO WITH CONDITIONS / NO-GO**, then the blockers
   (FAIL) and conditions (WARN), each in one sentence with the number and
   limit. Stale data warning, if any, goes above everything.
2. For NO-GO, say what would change the verdict (fewer shares → re-run the
   sizer; write the thesis → show the journal command from the report; wait
   for the circuit breaker / regime).
3. SKIP rows (no regime / exposure file, no target) mean "not checked" –
   mention the lower confidence and suggest running `gpw-market-regime` /
   `gpw-exposure-coach`.
4. If an override was used, quote the recorded reason.
5. Never say "buy"; the user decides and places the order.

## Checks

| # | Check | PASS | WARN | FAIL |
|---|---|---|---|---|
| 1 | Stop | below entry | – | missing or ≥ entry |
| 2 | Risk incl. round-trip fees | ≤ limit (1% or exposure-coach limit) | – | > limit |
| 3 | Position size | ≤ 25% of account | – | > 25% or more than the cash |
| 4 | Liquidity | order ≤ 10% of 20-session median turnover | no turnover data | > 10% or zero turnover |
| 5 | Portfolio heat | open risk + this trade ≤ 6% | open positions without stop / no journal | > 6% |
| 6 | Sector | after trade ≤ 35% (unclassified = PASS with note) | – | > 35% |
| 7 | Written thesis | exists, open, same ticker, kill criteria | stop differs from plan > 0.5% | missing / not found / ambiguous / closed / no kill criteria |
| 8 | Reward/risk (net of fees) | ≥ 1.5R | 1.0–1.5R | < 1.0R (SKIP without target) |
| 9 | Circuit breaker | ok | 3+ losses in a row or coach "reduce" (halve size) | 5+ losses, realised DD ≥ 10%, or coach "pause" |
| 10 | Market regime | uptrend states | correction, bear, top risk ≥ 80, override used | bear with top risk ≥ 80 (no override) |
| 11 | Exposure ceiling | ≤ coach ceiling | above ceiling | – (SKIP without coach) |
| 12 | New positions / week | ≤ coach limit | above | – (SKIP without coach) |
| 13 | Data freshness | current | stale prices, entry > 10% from last close, input JSON > 3 sessions old | – |

Verdict: any FAIL → NO-GO; else any WARN → GO WITH CONDITIONS; else GO.
Rationale and sources: `references/gate_rules.md`.

## Key rules

- Fail-closed: missing stop or thesis = FAIL. Missing optional context = SKIP,
  stated explicitly. Never fabricate a regime or exposure file.
- Heat, sector and exposure see only ACTIVE journal positions – say so if the
  user holds positions outside the journal.
- The override is for the regime block only, must be a real written reason
  and is always shown in the report.
- Never place orders, never say "buy". The disclaimer is appended.

## References

- `references/gate_rules.md` – every check, threshold, source, input contract.

## Related skills

`gpw-position-sizer` (share count, feeds `--sizer-json`), `gpw-trade-journal`
(thesis with kill criteria; record the fill after entry), `gpw-market-regime`
and `gpw-exposure-coach` (regime / limits JSON), `gpw-portfolio-check`
(whole-portfolio view), `gpw-trade-review` (after a losing streak).
