# Methodology: gpw-signal-postmortem

## 1. What is measured and why

A signal is a dated claim, such as "KETY is a VCP leader" or "the market is
in correction". The question is whether the instrument then did better or
worse than the alternative. For a GPW investor, that alternative is owning
the market.

The upstream tradermonty `signal-postmortem` judges a signal by the **sign of
the raw return** over 5 and 20 days. In a rising market, this rewards every
long signal. This skill judges a signal by the **return in excess of a
benchmark**, as in an event study (Brown and Warner 1985, market-adjusted
returns).

| Item | Choice | Reason |
|---|---|---|
| Prices | `gpwlib.panel.build_panel(adjust=True)`, i.e. GPW archive closes adjusted for dividends and splits | A dividend drop is not a loss. WIG is a total-return index, so adjusted stock returns are comparable to it. |
| Entry | close of the signal session (default) or the next session's open (`--entry next_open`) | The close is what the skill saw. The next open is what a person could actually trade after a report published after 17:05. |
| Horizons | 5 / 20 / 60 GPW sessions (about 1 week, 1 month, 1 quarter) | 20 sessions is the screener's swing horizon. 60 sessions covers Minervini/O'Neil position trades. 5 sessions shows immediate follow-through. |
| Primary benchmark | WIG over the same window | The broad, cap-weighted total-return index of GPW. |
| Second benchmark | equal-weighted liquid universe (price at least 1 zł, median 20-session turnover at least 50k zł, 20+ sessions of history) on the same dates | WIG is dominated by about 20 large caps. Small-cap signals are fairer against an equal-weighted basket (Barber and Lyon 1997, on benchmark choice). |
| Broad indices (WIG, WIG20, mWIG40, sWIG80) | absolute return | A market-direction signal measured against WIG would be about 0 by construction. |
| Survivorship | names that stop trading keep their last close (`stale_exit: true`) | Dropping them would bias results upward (survivorship bias). |
| MFE / MAE | best high and worst low after entry, within the window | MAE shows whether the stop would have been hit even when the end result was positive (Sweeney 1997, MAE analysis). |
| Stop vs pivot | first session whose low is at or below the stop, or whose high is at or above the pivot. Levels are rescaled by (adjusted close / close seen by the skill). | This tells you whether the invalidation was too tight. When the entry is already above the pivot (breakout, extended), only the stop is tracked. |

## 2. Outcome classes (primary horizon)

- `true_positive`: a long signal had excess > 0, or an avoid/exit signal had excess < 0.
- `false_positive`: a long signal had excess of 0 or less while the regime was not bad.
- `regime_mismatch`: a long signal failed while WIG was in `correction` or `bear` (from the gpw-market-regime timeline) at the signal date or at any session in the window. It separates "the idea was wrong" from "the market fell". It needs a recorded regime report. Without one, the regime is `unknown` and the class cannot be assigned.
- `missed_opportunity`: an avoid/exit signal was followed by the instrument beating the benchmark, or a screener candidate left off the watchlist beat WIG.
- `true_negative`: a candidate left off the watchlist lagged WIG.
- `pending`: the horizon has not been reached yet. The report shows sessions left and an estimated maturity date from the Polish exchange calendar.

There is no "neutral" band around 0, because a band would hide a systematic
small edge. Costs are reported separately: a GPW round trip with minimum
commissions usually costs 0.3-0.8% (see `gpwlib.brokers`).

## 3. Statistics

- **Hit rate with a Wilson 95% interval** (Wilson 1927). The interval stays inside [0, 1] and behaves well for small n and extreme proportions, unlike the normal (Wald) interval (Brown, Cai and DasGupta 2001).
- **Mean and median excess.** The median is reported because single-stock returns are right-skewed: a few big winners carry the mean.
- **t-statistic of the direction-signed edge**, in two versions:
  - naive, with one observation per signal;
  - **date-clustered**, which averages the signals of each date first and then computes t across dates.
  Signals on the same day share the same market move, so they are not independent. Clustering by time is the standard fix (Petersen 2009). With 20-session windows starting on consecutive days, the returns also overlap, which induces autocorrelation (Hansen and Hodrick 1980). The report states this caveat every time.
- **Welch two-sample t** for comparisons, such as priority of 80 or more vs below 80.
- **Minimum n = 20 per bucket** (`--min-n`, the upstream default). Below it, the bucket is marked `insufficient` and gives no feedback. Twenty is a floor, not a guarantee. With n = 20 and a 60% hit rate, the Wilson interval is still about 39-78%.

## 4. Buckets

Dimensions: `all`, `source`, `kind`, `source_kind`, `priority` (0-100 score
in quintiles), `execution_state` (screener: pre_breakout, breakout,
breakout_unconfirmed, extended, ...), `rs_bucket` (<70, 70-79, 80-89, 90-99:
70 is Minervini's minimum, 80+ is IBD's leader zone and 90+ the top decile),
`regime_at_signal`, `sector` (GPW Benchmark sector index), `size_segment`
(WIG20 / mWIG40 / sWIG80 / other / index), `on_watchlist`, `basis`.

Buckets are computed separately for each direction group (`long`,
`avoid_exit`, `skip`), so that an avoid hit never offsets a long miss.
Sector and size use **current** index membership, not point-in-time
membership. The report says so.

## 5. Feedback evidence gate

Feedback is a list of suggestions. **Nothing is applied automatically**
(`auto_apply: false`). A suggestion is a hypothesis to check, ideally with
`gpw-backtester`, before changing a rule.

| Verdict | Rule |
|---|---|
| `insufficient` | either group has n < min_n |
| `not_supported` | \|t\| < 2 |
| `suggestive` / `suggestive_against` | 2 <= \|t\| < 3 |
| `supported` / `contradicted` | \|t\| >= 3, the only verdicts with `action: review` |

The t = 3 bar follows Harvey, Liu and Zhu (2016). Many buckets are tested at
once, so t = 2 is too weak a standard for changing a rule. For a single-group
test, the Wilson interval must also exclude 50% in the same direction as t.

Checks:
1. Each (source, kind): does the signal beat its benchmark?
2. Priority of 80 or more vs below 80, for each source with scores. If the high bucket does not significantly beat the low one, "lowering the threshold is not supported".
3. RS of 90 or more vs below 90.
4. Each execution state vs zero.
5. Long signals in an uptrend vs in correction/bear. This feeds the regime cap of `gpw-exposure-coach`.
6. Watchlist vs near misses: did the screener's filter add value?

On WSE, factor premiums often disappear after costs (Zaremba and Konieczka
2016). An edge of a few tenths of a percent at 5 sessions is therefore not
actionable, even when it is significant.

## 6. Demo mode (`--demo`)

The cache holds about 2 years of sessions, and real skill reports exist only
from November 2026. Most real signals are therefore still pending. `--demo`
shows the machinery on real prices. It uses a deterministic rule: the first
close above the prior 252-session closing high, for liquid shares only
(median turnover at least 200k zł, the screener's floor). Each stock is
re-armed after 20 sessions. The score is the 6-month return percentile. The
invalidation is 8% under the pivot (Minervini's 7-8% maximum-loss rule).
Demo signals are **synthetic**:

- the report carries `demo: true` and a banner;
- no feedback file is written;
- the `feedback` command refuses demo reports.

Never present demo numbers as the track record of a skill.

## 7. Backfill

Re-running a skill historically is not this skill's job. To build history,
run the emitting skill for past dates yourself (for example
`gpw_trend_screener.py --as-of 2026-05-15`, which is point-in-time except for
current index membership). Then import the reports with
`backfill --list files.txt`.

## References

- Barber, B. and Lyon, J. (1997). Detecting long-run abnormal stock returns: the empirical power and specification of test statistics. *Journal of Financial Economics* 43.
- Brown, L., Cai, T. and DasGupta, A. (2001). Interval estimation for a binomial proportion. *Statistical Science* 16.
- Brown, S. and Warner, J. (1985). Using daily stock returns: the case of event studies. *Journal of Financial Economics* 14.
- Hansen, L. and Hodrick, R. (1980). Forward exchange rates as optimal predictors of future spot rates. *Journal of Political Economy* 88 (overlapping observations).
- Harvey, C., Liu, Y. and Zhu, H. (2016). ... and the cross-section of expected returns. *Review of Financial Studies* 29.
- Minervini, M. (2013). *Trade Like a Stock Market Wizard*. McGraw-Hill (7-8% maximum loss, VCP, RS >= 70).
- O'Neil, W. (2009). *How to Make Money in Stocks*, 4th ed. (follow-through days, distribution days).
- Petersen, M. (2009). Estimating standard errors in finance panel data sets. *Review of Financial Studies* 22.
- Sweeney, J. (1997). *Maximum Adverse Excursion: Analyzing Price Fluctuations for Trading Management*. Wiley.
- Wilson, E. B. (1927). Probable inference, the law of succession, and statistical inference. *JASA* 22.
- Zaremba, A. and Konieczka, P. (2016): factor premiums on the Warsaw Stock Exchange largely vanish after transaction costs (as cited in docs/ROADMAP_v1.2.md).
- Upstream: tradermonty/claude-trading-skills `signal-postmortem` (MIT): outcome taxonomy and the minimum-sample rule.
