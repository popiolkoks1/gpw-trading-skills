# GPW ETF Planner — methodology

Contents: 1. Data · 2. List statistics · 3. Buy list (cash-flow rebalancing) ·
4. Fees and buying frequency · 5. Rebalancing bands · 6. Portfolio risk ·
7. Backtest · 8. Limitations

## 1. Data

Official GPW *Archiwum notowań*, instrument type 241 (ETFs), one request per
session, cached in `~/.cache/gpw-trading-skills/archive/etf/`. Default 500
sessions (~2 years). Many GPW ETFs were listed in 2025–2026, so their
history is shorter — every statistic says how many sessions it uses.
Closes are adjusted for distributions and splits from GPW's published %
change (`gpwlib.panel`, tolerance 0.5%), i.e. total-return-like.

The archive lists only instruments that traded; the catalog (`etf_catalog.py`,
`references/etf_catalog.md`) adds descriptions. ETFs found in the archive but
missing from the catalog are reported as **uncategorised**; leveraged/inverse
funds are still recognised from the name suffix (`LV`, `3LV`, `ST`, `2ST`).

## 2. List statistics

| Statistic | Definition | Why |
|---|---|---|
| Liquidity | median traded value of the last 20 sessions (thousand zł) | median, not mean, so one block trade does not hide a thin market |
| Liquidity label | ≥ 100k zł "good", 20–100k "low", < 20k "very low" | every GPW ETF has a market maker, but thin turnover goes with wider spreads; 100k zł/day ≈ a mid-sized mWIG40 share; < 20k means a handful of trades a day → limit orders only |
| 1-year return | close / close 252 sessions earlier − 1 | 252 = one GPW year (`gpwlib.stats`); shown only with a full year of data, otherwise "since <first date>" |
| Volatility | annualised st. dev. of daily returns over the last ≤ 252 sessions, needs > 60 sessions | standard risk measure; < 3 months is noise |
| Max drawdown | largest peak-to-trough fall in the loaded history | what a beginner actually feels; the history is short, so this understates real bear markets |

Single-session outliers in very thin ETFs (an odd trade far from the market
maker's quotes) can show up as a spike in drawdown; treat the numbers of
"very low" liquidity funds with extra care.

## 3. Buy list — cash-flow rebalancing

The planner never sells. With current holdings `h` (units), prices `p`, and
money to invest `C` = monthly amount + uninvested cash:

1. Portfolio total `V = Σ h·p + C`; target value of ETF *i* = `w_i · V`.
2. Shortfall `g_i = max(0, w_i·V − h_i·p_i)`. If `C ≤ Σg`, cash is split in
   proportion to the shortfalls; otherwise each gets its shortfall and the rest
   is split by target weight.
3. Each allocation is converted to whole units **including the commission**
   (rounded down).
4. Left-over cash buys one more unit at a time of the ETF with the largest
   remaining shortfall, while affordable and while the shortfall exceeds half a
   unit (so the result rounds to the nearest unit instead of over-buying).
5. `--max-orders N` restricts the buys to the N most underweight ETFs
   ("rotation": with N = 1 there is one minimum fee a month and the proportions
   converge over the following months).

New money first is the cheapest way to rebalance: no sale, no Belka tax, no
second commission (Vanguard, Jaconetti, Kinniry & Zilbering 2010).

## 4. Fees and buying frequency

Commission per order = `max(pct × value, min fee)` from `gpwlib.brokers`
(presets: XTB 0%, mBank/PKO BP/Santander 0.39% min 5 zł, bossa 0.38% min 5 zł —
tariffs entered 2026-11-15, verify).

**Threshold 0.5% per order.** At 0.5% a single purchase costs as much as a full
year of a typical index-ETF TER; paying more per order doubles the yearly cost
of a buy-and-hold investor. With a 5 zł minimum fee the order must be at least
5 / 0.5% = 1 000 zł.

For each ETF the planner finds the smallest number of months `k` (1–12) for
which `fee(k × monthly × w) / (k × monthly × w) ≤ 0.5%`. The suggestion is the
largest `k` across the ETFs. Above 3 months (`MAX_FREQUENCY_MONTHS`) cash idles
too long, so the report suggests fewer ETFs, a cheaper broker, or rotation
(`--max-orders 1`: one order of the full monthly amount). If the percentage
commission alone exceeds 0.5% the threshold cannot be met.

## 5. Rebalancing bands — 5/25 rule

Larry Swedroe's 5/25 rule: rebalance when a holding drifts from its target by
more than **5 percentage points** or by **25% of its target weight**, whichever
band is tighter: `band = min(5 pp, 0.25 × target)`. A 60% holding triggers at
±5 pp, a 10% holding at ±2.5 pp. Threshold bands rebalance less often than a
calendar rule with similar risk control (Jaconetti et al. 2010). The check runs
on the holdings before and after this month's buys; out-of-band after the buys
means new money alone was not enough — the user may wait for further
contributions or sell (in a regular account a sale at a profit pays 19% tax;
in IKE/IKZE not).

## 6. Portfolio risk

* Historical: a daily-rebalanced index of the target weights on the sessions
  common to all ETFs (`gpwlib.stats.align`), its annualised volatility and max
  drawdown (expressed per 10 000 zł for readability).
* Composition: equities / bonds+cash / other (gold, crypto) from the catalog's
  `equity_share` (a 60/40 mixed fund counts 60% equities, 40% bonds),
  currency exposure, share of leveraged/inverse funds, and the share that is
  *likely* qualified for the OKI exemption — passed to `gpw-account-planner`
  as `--oki-qualified-pct`.
* Weighted TER only if the user passed every TER with `--ter` (we never guess).
* Always contrasted with history: in 2007–2009 broad equity indices fell more
  than 50% and WIG20 about 65% — two years of data cannot show that.

## 7. Backtest — DCA vs lump sum

* Common history of the allocation's ETFs; if it starts after the 5th of a
  month, the first full month is used so both strategies share the start date.
* DCA: on the first session of each month add `--monthly`, then run the buy-list
  algorithm (whole units, commissions, left-over cash carried over).
* Lump sum: the same total amount invested on the first buy day with the same
  algorithm.
* No tax (as in IKE); closes are adjusted, so distributions count as reinvested.
* DCA annual return = money-weighted (XIRR, bisection); lump sum = CAGR.
* **Caveat in every report:** ~2 years is far too short to judge an ETF or a
  strategy; a full market cycle is 7–10+ years (`SHORT_HISTORY_YEARS` = 10).

## 8. Limitations

* Catalog descriptions marked "name" are inferred from the fund name; issuers,
  indices, hedging and OKI qualification must be checked in the KID and statute.
* TER, spreads, FX costs inside the fund and tracking error are not modelled.
* Closing prices are not execution prices; thin ETFs can trade away from NAV.
* Survivorship: delisted ETFs are not in the current list.
