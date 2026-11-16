---
name: gpw-backtester
description: Honest daily backtester for long-only rule-based strategies on the Warsaw Stock Exchange (GPW), for advanced users. Official GPW archive (adjusted, survivorship-free), next-open fills, broker commissions with minimum fees, slippage by liquidity plus square-root impact, liquidity cap, whole shares, gap-through stops, no fill on suspended days, 19% Belka tax with 5-year loss carry-forward or IKE. Built-in strategies - Minervini trend template, SMA cross, 52-week breakout, 12-1 momentum rotation, DCA into WIG - plus custom JSON rules. Reports CAGR, drawdown, Sharpe, expectancy in R, cost and tax drag vs WIG, then walk-forward, parameter plateau, Deflated Sharpe, PBO, 2x cost stress, bootstrap and a verdict from NOT_EVALUABLE to ROBUST. PL triggers - "backtest strategii", "przetestuj strategię na GPW", "czy ta strategia działała", "walk-forward", "przeuczenie strategii". EN triggers - "backtest a GPW strategy", "test my trading rules on WIG stocks". Not a forecast, not for US stocks, intraday or shorting.
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
  derived_from: "tradermonty/claude-trading-skills backtest-expert (MIT) - verdict rules; engine written for GPW"
---

# GPW Backtester / Backtester strategii GPW

Tests a long-only, rule-based strategy on daily GPW data with Polish costs,
taxes and liquidity, then asks whether the result could be luck or curve
fitting. On WSE, factor premiums often disappear after costs (Zaremba &
Konieczka 2016), so here **costs and tax are part of the result, not a
footnote**, and the **verdict comes first**.

**Workflow position:** fed by ideas from `gpw-trend-screener`, your own rules
and (optionally) the `gpw-market-regime` history; it feeds the decision to
adopt or change a rule before it reaches `gpw-position-sizer`,
`gpw-pre-trade-gate` and `gpw-trade-journal`, and its expectancy in R can be
compared later with `gpw-trade-review` and `gpw-signal-postmortem`.

## When to use

- "Czy strategia X działała na GPW? Przetestuj trend template / momentum / złoty krzyż."
- "Ile zabierają koszty i podatek?" "Czy to nie jest przeuczone?" "Walk-forward."
- The user has written rules ("kupuję, gdy kurs > SMA200 i RSI < 30…") → JSON rules.
- Before changing a rule in a live process (ROADMAP: "before changing a rule: backtester").

Not for: forecasts ("ile zarobię"), intraday, short selling, options/futures,
US stocks, single-stock valuation. For beginners, start with `gpw-start` and
`gpw-etf-planner`; the backtester is an advanced tool.

## Language

Answer in the user's language; pass `--lang pl` (default) or `--lang en`.

## Workflow

### 1. Clarify the test (ask only what is missing)

| Input | Flag | Default / note |
|---|---|---|
| Strategy | `--strategy NAME` or `--rules file.json` | `trend_template`; `list-strategies` shows all, their parameters and default grids |
| Parameters | `--param key=value` (repeat) | e.g. `--param stop_pct=10 --param max_positions=8` |
| Period | `--start`, `--end` | 2019-06-03 … last cached session; < 5 years blocks a good verdict |
| Capital | `--capital` | 50 000 zł |
| Broker | `--broker mbank\|xtb\|bossa\|pkobp\|santander\|custom`, `--commission-pct`, `--commission-min` | mBank (0.39%, min 5 zł) |
| Account | `--account regular\|ike`, `--loss-rule pl_2019\|50pct` | regular, current law |
| Sizing | `--sizing equal\|risk`, `--risk-pct`, `--max-position-pct` | equal weight over `max_positions` slots; `risk` = fixed-fractional to the stop, like `gpw-position-sizer` |
| Liquidity | `--max-adv-pct` | 10% of 20-session median turnover (position-sizer default) |
| Costs | `--impact-coef`, `--cost-mult` | 0.5, 1.0 (the 2x stress runs automatically) |
| Idle cash | `--cash-rate` | 0% (conservative; e.g. 4 for ~WIBOR-like deposits) |
| Variants tried | `--trials N` | 1 — ask the user how many versions they already tried; it deflates the Sharpe |
| Regime split | `--regime-json PATH` / `--input-dir DIR` | gpw-market-regime history if found, else a labelled WIG-SMA200 proxy |
| Data | `--offline`, `--cache-dir` | shares the cache with the other GPW skills |
| Output | `--output-dir`, `--no-files`, `--json`, `--max-trades-md` | `gpw_backtest_<strategy>_<timestamp>.{json,md}` |

### 2. Run the script (never estimate results yourself)

Run from this skill's directory:

```bash
python3 scripts/gpw_backtester.py list-strategies --lang pl
python3 scripts/gpw_backtester.py run --strategy trend_template --start 2019-06-03 --broker mbank --capital 50000 --account regular --offline --output-dir <dir>
python3 scripts/gpw_backtester.py grid --strategy momentum_rotation --offline --output-dir <dir>
python3 scripts/gpw_backtester.py walkforward --strategy sma_cross --is-years 3 --oos-years 1 --offline --output-dir <dir>
python3 scripts/gpw_backtester.py run --rules my_rules.json --lang en --offline --output-dir <dir>
```

- `run` — one parameter set: metrics, costs, tax, 2x cost stress, bootstrap, DSR (with `--trials`), by year, by regime. Walk-forward and PBO are not available → the best possible verdict is PROMISING.
- `grid` / `walkforward` — every grid point (default grid per strategy, or `--grid "stop_pct=6,8,10;exit_sma=20,50"`, capped by `--max-trials`, default 36), plateau, PBO (CSCV), anchored and rolling walk-forward, DSR with N = grid size. The detailed report is for the best full-period Sharpe, **deflated**.
- A single run on ~7.5 years × ~450 shares takes a few seconds; a 12-point grid well under a minute. Without the cache, the first online run downloads years of sessions (~1.5 s each) — warn the user and prefer `--offline` if the cache exists.
- Exit code 1 = data/input problem (message on stderr: bad rules, unknown parameter, too few sessions) — report it, do not invent numbers. 2 = bad flags.

### 3. Present the result honestly (order matters)

1. **Data warnings first:** stale data, missing sessions in the cache, a clamped start date.
2. **Verdict + reasons** (`robustness.verdict`, `reject_reasons`, `blocking_reasons`, `limiting_reasons`) in plain words. Never soften REJECT/FRAGILE.
3. **Cost and tax drag** next to the net CAGR: "koszty zabrały X zł rocznie (≈Y% kapitału), podatek Z%". Then net CAGR, max drawdown and its length, vs **WIG** and **equal weight** (both gross, so the comparison is generous to the benchmarks only in costs — say so).
4. **Outlier dependence:** if the best 1–3 trades made most of the profit (`metrics.concentration`), say it plainly.
5. Trades: count, win rate, average win/loss, expectancy in % / zł / R, profit factor, losing streak (psychology: "20 strat z rzędu — czy wytrzymasz?").
6. Robustness in plain Polish (below), then the by-year table (bad years matter) and the regime split (label the proxy as a proxy).

Never say "ta strategia da X% rocznie" or "warto/nie warto kupować". Say
"w latach A–B, przy tych kosztach, wynik wyniósł…; to nie jest prognoza".

### 4. Explain the robustness tests (plain Polish)

- **Koszty ×2** — what if spreads and price impact are twice as bad. If the edge disappears, it was too thin for GPW.
- **Bootstrap** — the range where the average trade result plausibly lies; if it crosses zero, we cannot tell skill from chance.
- **DSR (Deflated Sharpe Ratio)** — "jak bardzo wierzyć Sharpe'owi po uwzględnieniu, ile wariantów przetestowano". 97% = solid; 60% = "przy tylu próbach taki wynik łatwo trafić przypadkiem". More variants tried → lower DSR, so ask about `--trials`.
- **PBO** — "jak często najlepszy w przeszłości wariant okazuje się słabszy od przeciętnego w kolejnym okresie". 0.1 = dobór parametrów ma sens; > 0.5 = rzut monetą.
- **Walk-forward OOS/IS** — the parameters are chosen on past data and tested on the next, unseen year; ≥ 0.5 means the strategy kept at least half of its quality out of sample.
- **Plateau** — good results should not hang on one exact parameter; neighbours should be similar.
- **t > 3** — Harvey-Liu-Zhu: after decades of data mining, a new "discovery" needs t above 3, not 2.

### 5. Custom rules (`--rules`)

Translate the user's words into JSON (show it to them before running); a ready example is `references/example_rules.json`:

```json
{"name": "pullback_in_trend",
 "entry": {"all": [{"left": {"ind": "close"}, "op": ">", "right": {"ind": "sma", "n": 200}},
                   {"left": {"ind": "rsi", "n": 14}, "op": "<", "right": 35}]},
 "exit": {"any": [{"left": {"ind": "rsi", "n": 14}, "op": ">", "right": 60},
                  {"left": {"ind": "close"}, "op": "crosses_below", "right": {"ind": "sma", "n": 200}}]},
 "stop": {"atr_mult": 2.5}, "max_hold": 30, "rank": {"ind": "roc", "n": 126},
 "params": {"max_positions": 8, "min_turnover_k": 300}}
```

Operands: a number or `{ind, n, of, shift, mult}`; indicators `close open high
low turnover sma ema roc rsi highest lowest atr median`; ops `> >= < <=
crosses_above crosses_below`; groups `all`/`any` up to 3 levels; `highest`/`lowest`
include today, so a breakout uses `"shift": 1`. Nothing is `eval`-ed; invalid
files exit 1 with the exact path of the error. A rule set needs a way out
(exit, stop, trail_pct or max_hold).

## Key rules

- Every number comes from the script. Do not re-compute, round up or extrapolate.
- Lead with the verdict and the cost/tax drag; never present gross numbers as the result.
- A backtest is history under stated assumptions, never a promise. Repeat it when the user gets excited.
- Point out the assumptions that matter for the case: next-open fills, same-day stop resolved against the user, no fills on no-quote days, adjusted-open estimate, dividends inside adjusted prices (tax approximated), IKE assumes the capital is already in the account (contribution limits apply), current broker tariff applied to past years.
- Survivorship: the universe is each day's archive snapshot (delisted names included); historical index membership is not used.
- If the user tried many variants, the honest DSR needs `--trials`. Ask.

## References

- `references/methodology.md` — engine timing, fill and cost model, tax law (art. 30b, art. 9 ust. 3 PIT, art. 63 Ordynacji), strategy definitions with sources (Minervini, O'Neil, Jegadeesh-Titman, Zaremba-Konieczka), metrics, DSR/PBO formulas, walk-forward, verdict thresholds, limitations. Load it on "how/why" questions.

## Related skills

`gpw-trend-screener` (live Trend Template screen), `gpw-market-regime` (regime
history for the split), `gpw-position-sizer` (same sizing and liquidity
defaults for a real trade), `gpw-pre-trade-gate`, `gpw-trade-journal` and
`gpw-trade-review` (compare live expectancy in R with the backtest),
`gpw-signal-postmortem` (did the signals work after the fact),
`gpw-etf-planner` / `gpw-account-planner` (for buy-and-hold savers, DCA is
usually the answer, not a trading rule).
