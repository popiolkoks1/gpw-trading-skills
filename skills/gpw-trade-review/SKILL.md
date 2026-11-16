---
name: gpw-trade-review
description: Reviews closed trades from the gpw-trade-journal for a month, a year or all time. It reports win rate, average win and loss, expectancy, profit factor, average R, max drawdown and excess return against WIG. It also separates skill from luck with a process-vs-outcome matrix (deserved win, lucky win, bad-luck loss, deserved loss) and finds patterns by setup, holding period and exit reason. For a tax year it estimates PIT-38 (przychód, koszty FIFO plus commissions, dochód or strata, 19% podatek Belki, Polish rounding). Use it whenever the user asks how their investing went, wants a monthly or annual summary, wants to learn from mistakes or needs to estimate capital-gains tax on Polish stocks. Triggers (PL) "podsumuj moje transakcje", "jak mi poszło", "statystyki tradingu", "wyniki za miesiąc/rok", "czego się nauczyłem", "ile podatku Belki", "PIT-38", "rozliczenie zysków z giełdy"; (EN) "review my trades", "trading performance", "monthly review", "win rate", "PIT-38 estimate".
license: MIT
metadata:
  version: "1.0.0"
  derived_from: "tradermonty/claude-trading-skills signal-postmortem (MIT)"
  languages: "pl,en"
---

# GPW Trade Review

Turns the journal into feedback. The key question is not "did I make money" but
**"is my process working, and am I following it?"**. On a small sample, outcomes
are mostly luck.

## When to use

- Monthly or annual reviews, or "how did I do?"
- The user wants to find their recurring mistakes or strengths.
- The user asks how much tax they will owe on stock gains, or wants help with PIT-38.

Do **not** use for: recording or closing a trade (use `gpw-trade-journal`), sizing (use `gpw-position-sizer`), or market analysis.

## Language

Answer in the user's language. Pass `--lang pl` or `--lang en`.

## Workflow

Run from this skill's directory. The script reads `~/.gpw-trading-skills/journal`
(or `--journal-dir` / `$GPW_JOURNAL_DIR`, the same location as `gpw-trade-journal`).
It never modifies the journal.

```bash
python3 scripts/trade_review.py --month 2026-11 --lang pl
python3 scripts/trade_review.py --year 2026 --lang pl          # stats + PIT-38 estimate
python3 scripts/trade_review.py --year 2026 --loss-deduction 1200 # prior-year loss the user deducts
python3 scripts/trade_review.py --all --paper                    # paper trades only (never mixed with real ones, no PIT-38)
python3 scripts/trade_review.py --all --lang en --no-files
```

It writes `gpw_trade_review_<timestamp>.json` and `.md` to `--output-dir` and prints the Markdown.
Exit code `1` with "no journal entries" means the journal is empty, so offer to start one.

## Presenting results

Use this order. Keep it short and plain:

1. **Headline:** the number of trades, net result in zł, win rate and average R. If the sample has fewer than 20 trades, say upfront that the statistics are only indicative.
2. **Process vs outcome:** explain the 2×2 in human terms:
   - *Deserved win*: the plan was followed and made money. Repeat it.
   - *Lucky win*: the plan was broken and still made money. This is dangerous because it rewards bad habits.
   - *Bad-luck loss*: the plan was followed and lost money. This is the normal cost of doing business. It is fine.
   - *Deserved loss*: the plan was broken and lost money. Fix this first.

   Point out which rule was broken most often (entry, stop or size) and compare P&L when following the plan with P&L when breaking it.
3. **Observations:** relay the script's `insights`, e.g. profit factor below 1, a stop that was not respected, or lagging WIG. If the result is below WIG, say honestly that a cheap index ETF would have done better. That is useful information, not a failure.
4. **Patterns:** at most 2–3 notable ones (e.g. "swing trades held ≤5 days lose; trend trades >20 days earn").
5. **Lessons:** quote the user's own lessons. Propose **one** concrete rule to change next month.
6. **PIT-38** (for `--year`): give the revenue, costs, income or loss and the estimated tax. Then always say:
   - this is an estimate, and the broker's **PIT-8C** is the official source;
   - the deadline is **30 April** of the following year;
   - IKE/IKZE/OKI trades are not included in PIT-38;
   - a loss can be carried forward for 5 years (verify the rules);
   - foreign-currency trades need NBP rates, which the script does not handle.

   Never present the estimate as a completed tax return.

## Metric definitions (short)

- **Expectancy**: the average net result per trade in zł.
- **Profit factor**: sum of wins ÷ sum of losses. Below 1 means the approach loses money.
- **R multiple**: the result ÷ the risk accepted at entry (measured to the initial stop).
- **Max drawdown**: the largest peak-to-trough fall of cumulative closed-trade P&L.
- **Excess vs WIG**: the trade's return minus WIG's return over the same holding period, in percentage points.

Full definitions, thresholds and the Polish tax rules with sources are in
`references/review_methodology.md`. Read it when the user asks "why" or for tax details.

## Related skills

`gpw-trade-journal` (data source), `gpw-position-sizer` (apply lessons such as a smaller risk %), `gpw-market-breadth` (was a losing streak a market-regime problem?).
