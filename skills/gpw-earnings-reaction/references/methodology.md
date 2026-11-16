# gpw-earnings-reaction: methodology

An event study of periodic reports on the Warsaw Stock Exchange, built only from
official free data: the ESPI/EBI list on gpw.pl for report dates and times, the
GPW archive for prices and turnover, and the GPW Benchmark portfolios for the
size segments.

## 1. Why price-based surprise

US earnings studies measure surprise against the analyst consensus (I/B/E/S) or
with SUE (standardised unexpected earnings). No free consensus exists for Polish
companies, and free history of reported profits is thin (filings.xbrl.org has PL
filings for FY2020–2023 only). So we use the **earnings announcement return**: the
market's own verdict, i.e. the abnormal return in the three sessions starting on
the reaction day. Brandt, Kishore, Santa-Clara and Venkatachalam (2008, "Earnings
announcements are full of surprises") show that this return-based surprise
predicts drift at least as well as SUE. The skill never invents a consensus.

## 2. Report dates and session alignment

- Source: `gpwlib.espi.fetch_range` (POST `GPWEspiReportUnion`, one request per
  day, two or three on peak days, cached 30 days once the day is over). Weekends
  and holidays are scanned too (by default), because some issuers publish then,
  close to a deadline.
- Classes used: `periodic_q`, `periodic_h`, `periodic_y` (classified by report type
  when the title is empty, which is normal for quarterly reports) and
  `preliminary_results` (title rule). `espi.is_correction` titles (korekta,
  sprostowanie) are ignored: a correction is not new information about results.
- ISIN → ticker: `gpwlib.fundamentals.isin_map` (GPW Benchmark portfolios: WIG,
  size, sector indices, NCIndex). Only tickers in the main-market share archive are
  kept. Unmapped issuers are counted in `espi.unmapped_issuers`, never dropped
  silently.
- Session alignment (Warsaw time; GPW WATS rules, Oct 2026 - opening auction to
  09:00, closing auction 17:00, trading-at-last to 17:05):

| Publication | Reaction day (day 0) | `session_alignment` |
|---|---|---|
| session day, before 09:00 | same session | `pre_open` |
| session day, 09:00–17:05 | same session, **flagged** (part of day 0 precedes the news) | `intraday` |
| session day, from 17:05 | next session | `after_close` |
| weekend / holiday | next session | `non_session` |

  The archive is authoritative for which days were sessions; after the last
  archived session the calendar (`gpwlib.plcal`) is used, so the reaction day of a
  report published today is known even before its quotes are.
- **One information event per company and period.** The period is inferred from the
  publication month (quarterly titles are empty) and refined from the title when it
  names a period ("III kwartał 2026", "3Q 2026"). Deadlines (Rozporządzenie
  Ministra Finansów z 6 czerwca 2025 r. w sprawie informacji bieżących i okresowych
  przekazywanych przez emitentów papierów wartościowych, Dz.U. 2025 poz. 755,
  § 83, amended by Dz.U. 2026 poz. 1205; it replaced the repealed Dz.U. 2018
  poz. 757): quarterly report within 60 days of the quarter end (ust. 1), no
  quarterly report for the second and last quarter (ust. 2), half-year within
  3 months (ust. 5), annual within 4 months (ust. 7); a deadline on a Saturday or
  public holiday moves to the next working day (ust. 11; Q3 2026: 29.11.2026 is a
  Sunday -> 30.11.2026). Verified text: `gpw-event-calendar`
  references/methodology.md. So Q1 is published in Apr–May, H1 in Jul–Sep, Q3 in
  Oct–Nov, the annual report (and a voluntary Q4 report, if any) in Jan–Apr. Q4
  and the annual report share one key (`<year>Q4`), H1 and Q2 another
  (`<year>H1`), because they carry the same new quarter. Several filings for the
  same period (consolidated and standalone, duplicates) collapse into one event.
- **Preliminary results** that precede the periodic report (within 60 days) are the
  information event: the market reacts to the first release of the numbers.
  The later report is attached as `periodic_report`. Banks and large caps often
  publish preliminary figures 2–4 weeks before the report.

## 3. Metrics per event

All prices are adjusted for dividends and splits by `gpwlib.panel.build_panel`.
Windows are counted in the stock's own observations; the WIG return always spans
the same calendar interval as the stock return (so a day without a quote does not
break the abnormal return).

| Field | Definition |
|---|---|
| `gap_pct` | open on day 0 / GPW reference price − 1 (reference = close / (1 + change%), i.e. the previous close as GPW adjusted it for that session) |
| `day0_return_pct` | close-to-close return on day 0 |
| `car_3d_pct` | CAR[0,+2] = Σ (r_stock − r_WIG), market-adjusted model |
| `car_3d_mm_pct`, `beta` | market model: AR = r − (α + β r_WIG), OLS on [−250, −30], at least 100 observations |
| `sigma_daily_pct` | sd of daily market-adjusted AR on [−250, −30] (≥ 60 obs; else [start, −6] with ≥ 30 obs, flagged `short_estimation_window`; else skipped) |
| `z` | CAR[0,+2] / (σ · √k), k = days in the window (3, fewer while provisional) |
| `abnormal_turnover` | turnover on day 0 / median turnover on [−60, −6] (≥ 20 obs) |
| `run_up_car_pct` | CAR[−20, −1] (pre-report positioning or leakage) |
| `drift_20s_pct`, `drift_60s_pct` | CAR[+3, +20] and CAR[+3, +60]; `null` with status `pending` and `sessions_left` until the window is complete |
| `close_location` | (close − low) / (high − low) on day 0: 1 = closed at the high (buyers held the move), 0 = at the low |
| `vs_sma50_pct`, `vs_sma200_pct` | close on day 0 vs its SMA50 / SMA200 (trend context, as in tradermonty earnings-trade-analyzer) |

Why these windows:
- **[−250, −30] estimation window**: about one year ending six weeks before the
  event, the standard in daily event studies (MacKinlay 1997, J. Econ. Lit. 35;
  Brown & Warner 1985, J. Fin. Econ. 14). It keeps the previous report and the
  pre-event run-up out of the volatility estimate.
- **Market-adjusted as the primary model**: Brown & Warner (1985) show it performs
  about as well as the market model in daily event studies; on the WSE many small
  caps trade thinly, which biases OLS betas towards zero (Dimson 1979, J. Fin.
  Econ. 7). Both are reported so the user can compare.
- **3-day window [0, +2]**: absorbs late reactions (reports at 22:00 on Friday,
  slow price discovery in thin names) without diluting the signal; the usual
  announcement window in PEAD studies.
- **Turnover baseline [−60, −6], median**: three months, ending a week before the
  event; median because GPW turnover is heavy-tailed (block trades).
- **Drift [+3, +20] and [+3, +60]**: one month and one quarter (to the next
  report), the horizons of Bernard & Thomas (1989) and Chordia et al. (2009).
  Starting at +3 keeps the announcement window out.

## 4. Labels

z = CAR[0,+2] / (σ√3), abnormal turnover AT.

| Label | Rule |
|---|---|
| `strong_positive` / `strong_negative` | \|z\| ≥ 2 and AT ≥ 1.5 |
| `positive` / `negative` | \|z\| ≥ 1 and AT ≥ 1.0; or \|z\| ≥ 2 with AT in [1.0, 1.5) or unknown (flag `turnover_unconfirmed` / `turnover_unknown`) |
| `neutral` | otherwise (including \|z\| ≥ 1 on below-median turnover) |

- **|z| ≥ 2**: the 5% two-sided significance level of the standard event-study
  test is 1.96 (Brown & Warner 1985), rounded to 2. For a beginner: a move this
  large happens by chance roughly once in 20 reports.
- **|z| ≥ 1**: a one-sigma move, about the top / bottom 16% of 3-day moves under
  normality - noticeable but not exceptional.
- **AT ≥ 1.5 for strong labels**: a price move on the news should come with
  abnormal volume (Beaver 1968, J. Acc. Res. 6; Kim & Verrecchia 1991, J. Acc.
  Res. 29). O'Neil / IBD require volume 40–50% above average to confirm a move;
  tradermonty earnings-trade-analyzer scores volume the same way. A big move on a
  normal or quiet day is more likely noise, a market-wide move or a stale price.
- **AT ≥ 1.0 for normal labels**: at least a normal day; a reporting-day move on
  below-median turnover is treated as noise.

## 5. Aggregates

- `season{}`: the reporting season running on the as-of date (Oct–Dec → Q3,
  Jan–Mar → Q4/FY of the previous year, Apr–Jun → Q1, Jul–Sep → H1). Share of
  events with positive CAR, mean / median CAR with t-stat, label counts, share of
  intraday publications, and by size segment (current WIG20 / mWIG40 / sWIG80
  portfolios, `other` for the rest; not point-in-time).
- `season.baseline`: the equal-weighted mean daily excess return vs WIG of all
  liquid stocks (median turnover ≥ 50k zł over the last 60 sessions) from the
  first season reaction to the last session, × 3 = `baseline.car_3d_pct`.
  WIG is cap-weighted, so when large caps lead the average stock lags WIG on any
  day, report or not; `mean_car_3d_vs_baseline_pp` reads the season's average
  reaction against that "normal 3 days" instead of zero. (Nov 2026: the average
  liquid stock lagged WIG by about 0.5% per 3 sessions.)
- Top lists: liquid names only (median turnover ≥ 100k zł/day and price ≥ 1 zł),
  sorted by z. 100k zł/day: at 50k zł/day (the universe floor in
  `gpwlib.universe`) a 10k zł order is 20% of a normal day; at 100k zł it is at
  most 10%. Chordia et al. (2009) show that drift in illiquid names is mostly eaten
  by trading costs.
- `drift_study{}`: average CAR[+3,+20] (and [+3,+60]) by label with n, median,
  t-stat (mean / (sd/√n)) and share positive; only events with a complete window.
  `vs_neutral_pp` / `t_vs_neutral` (Welch) compare each label with the neutral
  group, a control that sits in roughly the same calendar windows and absorbs the
  drift of the average stock vs the cap-weighted WIG (Aug–Nov 2026: about −3% per
  18 sessions for neutral reactions). Read drift as "vs neutral", not vs zero.
  `low_n` when n < 30 (below that a t-stat is unreliable and one stock dominates).
  `by_liquidity` splits negative and positive reactions at the sample's median
  turnover. In `scan` mode most current-season events are still `pending`; use
  `--history` (ticker mode) or a historical `--as-of` for a larger sample.
- `signals[]` (for `gpw-signal-postmortem`): only strong labels. `kind`
  `earnings_strong_negative` → `direction` `exit` if the ticker is an open
  position in `gpw-trade-journal`, else `avoid`; `earnings_strong_positive` →
  `long` (a watchlist candidate). `score` 0–100 = |z| / 4 × 100 (z = 2 → 50,
  z ≥ 4 → 100), +10 when AT ≥ 3, capped at 100.

## 6. Evidence

- **Post-earnings-announcement drift**: Ball & Brown (1968) and Bernard & Thomas
  (1989, "Post-Earnings-Announcement Drift: Delayed Price Response or Risk
  Premium?", J. Acc. Res. 27 suppl.) - prices keep moving in the direction of the
  surprise for about 60 days.
- **Liquidity**: Chordia, Goyal, Sadka, Sadka & Shivakumar (2009, "Liquidity and
  the Post-Earnings-Announcement Drift", Financial Analysts Journal 65(4)) - drift
  is concentrated in illiquid stocks, and transaction costs eat 70–100% of it.
- **WSE**: Jagliński (2020, Financial Sciences / Nauki o Finansach, UE Wrocław,
  https://journals.ue.wroc.pl/fins/article/view/233) - on the Warsaw Stock Exchange
  drift appears mainly after negative surprises (from the abstract). This is why a
  strong negative reaction is presented as the more reliable signal - a warning to
  review or avoid a long position - and a strong positive one only as a watchlist
  candidate. Shorting individual stocks on GPW is limited, so no short signals.
- Seasonal random walk for profits (fundamental surprise): Foster (1977, The
  Accounting Review 52); the SUE of Bernard & Thomas uses the same benchmark.

## 7. Fundamental surprise (optional)

`fundamental_surprise.yoy_net_profit_pct` = (net profit of the period − same
period last year) / |last year|, year-to-date figures, from the **local** history
store of `gpwlib.fundamentals` (`save_snapshot`, built by skills that fetch the
Notoria tab each quarter). Requires two comparable periods (same number of months,
same consolidation basis). It is not standardised (that needs several years of
history), so it is labelled "SUE-like". Usually `null` with a `reason`
(`no_local_fundamentals_history`, `two_comparable_periods_not_in_history`,
`consolidation_basis_differs`). The Notoria tab shows only the latest period, and
filings.xbrl.org covers 2020–2023 annual reports only, so the store fills over time.

## 8. Next report date (ticker / portfolio)

Last year's publication date of the next period + 1 year, moved to the next
session (`date_kind: "estimated"`). Without last year's date (needs `--history`):
the statutory deadline (`date_kind: "statutory_deadline"`, § 83 Dz.U. 2025 poz. 755,
rolled to the next working day; for `<year>Q4` it is the annual-report deadline,
as there is no Q4 quarterly-report obligation). Never presented as
confirmed; `gpw-event-calendar` reads published report schedules.

## 9. Limitations

- Event dates depend on ESPI classification; a periodic report filed with an
  unusual title and type `RB` can be missed. Reports of issuers outside the GPW
  Benchmark portfolios (NewConnect, some foreign listings) are not mapped.
- The period of a quarterly report is inferred from the month; non-calendar fiscal
  years (some agri and retail companies) may be keyed to a neighbouring period.
- A reaction can be contaminated by other news on the same days (dividend
  announcement, guidance, market shock); the 3-day window and the WIG adjustment
  reduce, not remove, this.
- Thin stocks: stale closes understate σ and inflate z; the turnover confirmation
  and the liquidity filter guard against it.
- Index membership and size segments are current, not point-in-time.
- No consensus, no guidance, no text analysis of the report (PDF attachments are
  blocked for scripts on gpw.pl and are not downloaded).
