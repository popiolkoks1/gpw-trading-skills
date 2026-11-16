# gpw-risk-lab – methodology

Every number in the report comes from `scripts/risk_lab_engine.py` (pure
functions, unit-tested) on data loaded by `scripts/risk_lab_data.py`. This
file explains each method, each constant and its source.

## 1. Stress testing framework

The skill follows the two standard families of stress tests used by
supervisors:

- **Historical scenarios** – replay the market moves of a real crisis on
  today's portfolio. *Basel Committee on Banking Supervision, "Stress testing
  principles"* (BCBS, October 2018) and the earlier *"Principles for sound
  stress testing practices and supervision"* (BCBS, May 2009) ask for both
  historical and hypothetical scenarios, severe but plausible, and for
  reverse thinking about which scenario breaks a limit.
- **Hypothetical (factor) scenarios** – shock a risk factor (equity index,
  sector, FX, rates) and translate it into positions through sensitivities.
  ESMA uses the same split in *Guidelines on stress test scenarios under the
  MMF Regulation* (ESMA34-49-115, updated yearly) and requires liquidity
  stress testing for funds in *Guidelines on liquidity stress testing in UCITS
  and AIFs* (ESMA34-39-897, 2019, applicable from 30 Sep 2020).

The method is **historical simulation**: no distribution is assumed; the
portfolio is revalued with observed returns (Jorion, *Value at Risk*, 3rd ed.,
ch. 12 "Stress testing"). It answers "what would today's portfolio have done",
not "what will happen".

## 2. Historical windows (`scripts/risk_lab_scenarios.json`)

| id | Kind | Window rule | Why |
|---|---|---|---|
| gfc_2008 | peak_to_trough | WIG high in 2007-10-01..2009-02-28, then the low after it | Global financial crisis; WIG20 peaked in Oct 2007, WIG bottomed in Feb 2009 |
| euro_2011 | peak_to_trough | 2011 calendar year | Euro-area sovereign debt crisis, Aug-Sep 2011 sell-off |
| ofe_2013 | fixed | close 2013-09-03 → close 2013-09-05 | Government announced the OFE (pension fund) reform on 4 Sep 2013 – the main domestic buyer of Polish equities lost its bond portfolio and inflows. WIG fell 2.1% and 4.4% on 4-5 Sep and had fully recovered by 10 Sep (archive data), so the window covers the two shock sessions |
| chf_2015 | fixed | close 2015-01-14 → close 2015-01-16 | SNB removed the EURCHF floor on 15 Jan 2015; Polish banks with CHF mortgage books fell |
| covid_2020 | peak_to_trough | 2020-02-01..2020-03-31 | COVID-19 crash |
| war_inflation_2022 | peak_to_trough | 2021-11-01..2022-10-31 | Inflation, NBP hikes, Russia's invasion of Ukraine, bank "credit holidays" |
| worst_1d / worst_5d | worst_days | largest WIG fall over 1 / 5 sessions in all loaded data | Tail events the list above may miss |

`peak_to_trough` is resolved on WIG closes: the highest close in the range,
then the lowest close after it within the range. A window counts as covered
only if the index has quotes within 7 calendar days of both ends. Users can
add or edit windows in their own copy of the JSON file (`--scenarios`).

## 3. Position returns in a historical window – the proxy chain

For each position, in this order:

1. **actual** (A) – the stock's own dividend- and split-adjusted return
   (`gpwlib.panel.build_panel(adjust=True)`) when it has a quote on/just before
   both window ends (at most 5 sessions stale; otherwise it was suspended or not
   listed). Share data is used from 2019 on (policy: no share backfill before 2019).
2. **sector proxy** (S) – return of its GPW sector index (current GPW Benchmark
   membership) × the stock's beta to that index, if the index existed in the window.
3. **size proxy** (R) – WIG20 / mWIG40 / sWIG80 (current membership) × beta.
4. **WIG proxy** (W) – WIG × beta.

The proxy applies the beta **day by day** and compounds it over the window:
r = Π(1 + β·r_index,t) − 1. This is the path a stock with that beta would have
followed; the one-period shortcut β × (window return) overstates long windows
(β 1.7 × WIG20 −70% would be −119%, compounding gives about −90%).

Betas are OLS slopes of daily simple returns over the last 252 sessions
(one GPW year; `--lookback`). Fewer than 60 returns (~3 months) → beta 1.0,
flagged. Betas used as multipliers are clipped to [0, 3]: one-year betas
outside that range are dominated by estimation noise, and betas regress
towards 1 over time (Blume, M. 1971, "On the assessment of risk", *Journal of
Finance* 26(1)). A proxied return is floored at −100% (only reachable on a single extreme day).

Limits: today's beta may differ from the crisis beta; the proxy captures the
index-driven part of the move only (no stock-specific news). Sector membership
is not point-in-time. Proxied rows are always labelled in the report.

## 4. Factor shocks

All factor scenarios are **conditional expectations**: position return =
sensitivity × shock. They assume the relationship estimated in normal data
holds in the shock; in real crises correlations tend to rise (Longin &
Solnik 2001, *Journal of Finance* 56(2)), so losses can be larger.

- **Market** (`kind: market`): r = beta(stock, WIG) × shock. Defaults −10, −20,
  −30, −40% span a correction to a 2008-type bear market (WIG fell ~65%
  peak-to-trough in 2007-09, ~37% in Feb-Mar 2020).
- **Sector** (`kind: sector`): members of the sector index get
  beta(stock, sector) × shock. `spillover: none` (default) leaves other
  stocks unchanged – the right model for an idiosyncratic sector event (bank
  windfall tax, CJEU ruling on CHF loans); `spillover: beta` applies
  beta(stock, sector) × shock to everyone (contagion).
- **Zloty** (`kind: fx`, shock = EURPLN % change, +15% = zloty 15% weaker):
  sensitivity of each index = OLS slope of **weekly** index returns on weekly
  EURPLN % changes (NBP table A mid rates, last fixing of the ISO week),
  over all common history since 2007. Weekly data avoid the timing mismatch
  between the NBP fixing (~11:00) and the GPW close. Position return =
  beta(stock, proxy) × sensitivity(proxy) × shock with the proxy chain of §3.
  The report also gives the **tilt vs WIG** (index sensitivity minus WIG's):
  a less negative/positive tilt marks exporters (earnings in EUR/USD), a more
  negative one importers and domestic financials.
- **Rates** (`kind: rates`, shock in bp of the Polish 10-year government bond
  yield): sensitivity = OLS slope of **monthly-average** index returns on the
  monthly change of the ECB "long-term interest rate for convergence purposes"
  for Poland (a monthly average yield, so index levels are averaged the same
  way), per +1 pp, ≥ 36 months, since 2007. This replaces invented elasticities
  ("banks +, developers −") with data; the report shows t-statistics and marks
  |t| < 2 as weak evidence.

The sensitivities include the whole-market reaction to the factor (in Poland a
weaker zloty usually comes with foreign selling of equities), which is what a
portfolio owner experiences.

## 5. Aggregation and loss tolerance

- Portfolio return = Σ (position value × return) / (equity + cash); cash returns 0.
- `pln_loss` is positive for a loss; `contribution_pp` = position P&L / total × 100.
- With `--max-loss-pct T`: every scenario with loss L > T is listed with the
  equity reduction that would fit it: c = 1 − T / L (loss scales linearly with
  the equity share because cash returns 0). This is arithmetic, not advice.

## 6. Liquidity under stress

Days to exit = position value / (participation × stress factor × median daily
turnover of the last 60 sessions).

- 60 sessions ≈ one quarter: structural liquidity, not one quiet week
  (same window as `gpw-portfolio-check`).
- Stress factor 50%: turnover halves in a crisis – of the order seen on GPW
  for small and mid caps in 2008 and March 2020; ESMA's liquidity stress
  testing guidelines (ESMA34-39-897) ask for reduced market liquidity, not a
  specific number, so the factor is an editable flag (`--stress-turnover`).
- Participation 20%: the upper end of the 10-25% of volume used in execution
  practice before an order moves the price materially (`--participation`).
- More than 5 sessions = illiquid (one trading week, as in gpw-portfolio-check).

## 7. Hedge with FW20 (WIG20 futures)

**Contract** (GPW, *Standard kontraktów terminowych na WIG20*, Uchwała Nr
980/2013 Zarządu Giełdy z późn. zm., version 06.2026,
https://www.gpw.pl/pub/GPW/files/PDF/standardy_pl/WATS/Standard_kontrakty_WIG20_WATS_06.2026.pdf):
symbol `FW20krr20`, multiplier **20 zł** per index point, value = multiplier ×
price, four nearest series of the March/June/September/December cycle, last
trading day = **third Friday** of the expiry month or the last session before it
if no session is held then, final settlement = average of WIG20 values in the
last hour of continuous trading plus the closing value (5 highest and 5 lowest
rejected), cash settled in PLN. *"Firma inwestycyjna lub bank powierniczy
określa poziom depozytu zabezpieczającego wnoszonego przez inwestora"* – the
broker sets the investor's margin. The multiplier is also read from the
symbol (`gpwlib.gpw_archive.parse_futures_symbol`) and checked against 20.

**Beta and contracts.** Portfolio daily returns (today's weights, positions
with ≥ 61 quotes in the window; shorter-history positions count with beta 1.0)
are regressed on WIG20 (and WIG) over the last 252 sessions:

    N* = β × V / (F × 20 zł)            (Hull, Options, Futures and Other Derivatives, ch. 3)

V = equity value (cash excluded), F = settlement price of the recommended
series. The report shows N* rounded **down** (never over-hedge with whole
contracts; same rule as tradermonty futures-position-sizer) and up, with the
hedge ratio each achieves (N × F × 20 / (β V)).

**Minimum-variance hedge ratio** (Ederington, L. 1979, "The Hedging
Performance of the New Futures Markets", *Journal of Finance* 34(1), 157-170):
h* = Cov(r_p, r_f) / Var(r_f), estimated against the continuous FW20 series;
shown as a cross-check of the index beta (it also absorbs basis noise).

**Residual risk** = 1 − R² of the WIG20 regression: the share of portfolio
variance that WIG20 futures cannot remove (stock-specific and mid/small-cap
risk), also shown as annualised volatility σ_p √(1−R²).

**Series choice and roll.** The front series is recommended unless it expires
within 5 sessions (liquidity moves to the next series in the expiry week),
then the next one. The roll schedule lists the last trading day and a roll
window starting 5 sessions before it. **Basis** = futures − WIG20 spot
(converges to 0 at expiry; it mostly reflects dividends and interest to expiry).

**Continuous series for the backtest.** Each day uses the nearest series
whose last trading day is on or after that day (the expiring series is held
through its final settlement), and each daily return is computed on the
**same** series on both days, so rolling never creates an artificial jump
(return-chained continuous series). Data: GPW archive type 35, one year.

**Backtest.** Unhedged vs hedged daily returns r_h = r_p − h × r_f with
h = contracts × F × 20 / V (the floor), and h = β (full hedge), constant over
the year: volatility, maximum drawdown, total return, worst day. It is
in-sample (today's weights and beta applied backwards) – an illustration of
effectiveness, not a forecast.

**Margin estimate.** KDPW_CCP computes margins with a SPAN-type model and the
broker sets the investor's level (often a markup – e.g. one Polish broker's
2026 notice sets the initial deposit at 120% of KDPW_CCP's basic risk level).
The skill shows a *model estimate only*: the 99% quantile of 2-day absolute
moves of the continuous FW20 series over the year, × contract value, × 1.2.
99% confidence, a 2-day margin period of risk and ≥ 12 months of data are the
EU minimums for exchange-traded derivatives at a CCP (Commission Delegated
Regulation (EU) No 153/2013, arts 24-26). **Always check the actual figure
with the broker.**

**Small portfolios.** If N* < 1 the portfolio is smaller than one contract
(~86 000 zł notional at WIG20 ≈ 4 300). Alternatives shown: inverse ETFs listed
on GPW (detected from the ETF archive by the Beta ETF short-series names
`ETF…W20ST` / `ETF…M40ST`, e.g. ETFBW20ST = −1× WIG20 daily) with price,
median turnover and the units that match β × V – with the daily-reset caveat
(over longer periods returns drift from −1× the index); or holding more cash
(loss in market scenarios falls in proportion to the equity share).

## 8. Tax and account rules (Poland)

- Futures P&L is capital income from realising rights from derivative
  financial instruments – art. 17 ust. 1 pkt 10 and art. 30b ust. 1 ustawy o
  PIT: 19% tax, reported on **PIT-38** together with shares, so futures losses
  offset share gains of the same year and vice versa; a remaining loss can be
  deducted over the next 5 years (art. 9 ust. 3). Rate: `gpwlib.tax.BELKA_RATE`.
- **IKE/IKZE:** ustawa o IKE i IKZE (consolidated text Dz.U. 2022 poz. 1792,
  amended Dz.U. 2026 poz. 91), **art. 27 ust. 2**: a brokerage IKE/IKZE
  agreement *may* allow orders in financial instruments that are not
  securities only *"wyłącznie w celu ograniczenia ryzyka inwestycyjnego"*,
  for instruments whose price depends on securities held on that account and
  that trade on a regulated market. So futures are not banned outright, but
  only for hedging and only if the broker offers it – in practice most
  brokers do not; check with the broker. If the hedge sits on a regular account
  while the shares are on IKE/IKZE, futures gains are taxed at 19% and futures
  losses offset only regular-account gains (tax asymmetry).
- **OKI** (from 2027, Dz.U. 2026 poz. 1098): income from OKI assets is outside PIT
  (`gpwlib.tax` docstring); a futures hedge on a regular account is taxed separately.

## 9. Data sources

- GPW archive (https://www.gpw.pl/archiwum-notowan): shares (type 10, from 2019),
  indices (type 1, from 2007), ETFs (type 241), futures (type 35).
- GPW Benchmark index portfolios (sector and size membership, current).
- NBP API table A EUR mid rates (https://api.nbp.pl), 93-day limit per query →
  calendar-quarter requests, cached.
- ECB Data Portal, series IRS.M.PL.L.L40.CI.0000.PLN.N.Z (Poland, long-term
  interest rate for convergence purposes, monthly average).
- All requests go through `gpwlib.http` / `gpwlib.gpw_archive` (identifying
  User-Agent, ≥ 1 s per host for http, cache on the user's machine).

## 10. What the skill does not do

No forecasts, no probabilities of a crash, no option strategies, no shorting
advice, no orders. "Contracts needed" and "equity reduction to fit" are
arithmetic for the user's own decision.
