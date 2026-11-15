# Review methodology and PIT-38 rules

Contents: 1. Data · 2. Metrics · 3. Process vs outcome · 4. Insight rules ·
5. PIT-38 estimate · 6. Sources

## 1. Data

The review uses CLOSED theses from `gpw-trade-journal`, filtered by `outcome.closed_at`
within the period. Each thesis is counted as one trade, with partial sells aggregated.
The PIT-38 estimate is different: it uses **every sell fill** on regular accounts dated
in the tax year, including partial sales of theses that are still ACTIVE, because
tax follows the sale date, not the thesis.

## 2. Metrics

| Metric | Formula | Notes |
|---|---|---|
| Win rate | wins / trades | a trade with net = 0 counts as neither win nor loss |
| Avg win / avg loss | mean net of winners / losers | |
| Payoff ratio | avg win / abs(avg loss) | |
| Expectancy | Σ net / trades | zł per trade, after commissions, before tax |
| Profit factor | Σ wins / abs(Σ losses) | shown as ∞ when there are no losses |
| Avg R | mean of `r_multiple` | uses the initial stop, so trailing stops don't distort it |
| Max drawdown | max(peak − equity) of cumulative closed-trade net P&L ordered by close date | open positions are excluded |
| Excess vs WIG | mean(trade return − WIG return over the holding period) | percentage points |

**Small-sample warning: fewer than 20 trades.** With n = 20 and a true win rate of 50%,
the standard error of the observed win rate is √(0.25/20) ≈ 11 pp. A 40% result and a
60% result are then statistically indistinguishable, so below 20 trades every
statistic is flagged as indicative only.

## 3. Process vs outcome (2×2)

`followed_plan` comes from the three close-time questions in the journal: was the
entry according to plan, was the stop respected, was the size according to plan. All
three must be "yes".

| | net > 0 | net ≤ 0 |
|---|---|---|
| followed plan | deserved win | bad-luck loss |
| broke plan | lucky win | deserved loss |

Why: in trading, good decisions regularly produce losses and bad ones sometimes
produce profits. Judging only by P&L reinforces random behaviour. The goal is to
increase the left column (process) and accept bad-luck losses as a normal cost. This
is the decision-quality idea behind the upstream `signal-postmortem` (which separates
signal error from regime mismatch), adapted to individual discretionary trades.

## 4. Insight rules (deterministic)

| Key | Condition | Rationale |
|---|---|---|
| i_small_sample | trades < 20 | see §2 |
| i_pf_below_1 | profit factor < 1 | the approach loses money net of costs |
| i_payoff_low | payoff < 1 and win rate < 60% | without a high win rate, losses larger than wins cannot be profitable |
| i_stop_broken | any trade with stop rule = no | respecting the stop is the main defence against catastrophic losses |
| i_lucky | ≥ 1 lucky win | risk of reinforcing the bad habit |
| i_lag_wig | mean excess < 0 | a passive index would have done better |
| i_big_losses | any R < −1.5 | a loss far beyond the planned risk points to a gap or a moved stop |

## 5. PIT-38 estimate (regular accounts)

Rules applied by the script:

1. **Revenue (przychód)** is the sale proceeds (price × shares) of sales in the tax year.
2. **Costs (koszty uzyskania przychodu)** are the purchase cost of the shares sold under **FIFO** (art. 24 ust. 10 ustawy o PIT) plus the buy commission allocated to those shares plus the sell commission. They are recognised in the year of the sale (art. 22 ust. 1d / 1f pkt 1, cost of acquisition at disposal).
3. **Income (dochód)** = revenue − costs. A negative result is a **loss (strata)**.
4. An optional **prior-year loss deduction** comes from the user (`--loss-deduction`) and is capped at the income. Under art. 9 ust. 6 in connection with ust. 3, a capital loss can be deducted over the next **5 years**, with either max **50%** of the loss in one year, or once up to **5 000 000 zł** with the rest under the 50% rule. **Verify the current wording in ISAP or with a tax adviser.** The script does not track carry-forward history.
5. **Tax base (podstawa)** is rounded to the full złoty, and the **tax at 19%** (art. 30b) is rounded to the full złoty. Both use art. 63 § 1 Ordynacji podatkowej: amounts below 50 gr are dropped, 50 gr or more round up.
6. IKE, IKZE and OKI accounts are excluded and listed separately.
7. The filing deadline is **30 April** of the following year.

Not handled (stated in every report):
- reconciliation with the broker's **PIT-8C** (the official source, issued by the end of February);
- foreign-currency trades, which are converted at the NBP average rate from the business day before the transaction (art. 11a);
- dividends, which are taxed at source for Polish companies, while foreign dividends are settled on PIT-38 with a credit;
- derivatives;
- offsetting against other capital income such as bonds or funds on the same PIT-38;
- crypto, which is a separate source and cannot be offset.

## 6. Sources

- PIT-38 information: https://www.podatki.gov.pl/pit/
- Ustawa o PIT (Dz.U. consolidated text), arts 9, 11a, 22, 24 ust. 10, 30b: https://isap.sejm.gov.pl/
- Ordynacja podatkowa art. 63 (rounding): https://isap.sejm.gov.pl/
- IKZE relief: https://www.podatki.gov.pl/ulgi/ulga-na-ikze-pit
- OKI act: Dz.U. 2026 poz. 1098, https://eli.gov.pl/api/acts/DU/2026/1098/text/T/D20261098L.pdf
- Upstream concept: tradermonty/claude-trading-skills `signal-postmortem` (MIT)
