# gpw-backtester — methodology

Everything the engine assumes, with the reason and the source. The engine is
`scripts/backtester_engine.py`; strategies and the rule format are in
`backtester_strategies.py`; metrics in `backtester_metrics.py`; robustness and
the verdict in `backtester_robustness.py`.

## 1. Data

- **Source:** official GPW quotation archive (`https://www.gpw.pl/archiwum-notowan`), type 10 (shares, PLN) and type 1 (indices, WIG), loaded session by session through `gpwlib.gpw_archive` (cached in `~/.cache/gpw-trading-skills`).
- **Adjustment:** `gpwlib.panel.build_panel(adjust=True)` rebuilds dividend- and split-adjusted closes, highs and lows from the published "zmiana kursu %" (backward adjustment; see `panel.py`). The archive has no adjusted open, so we use
  `adj_open = raw_open × adj_close / raw_close` — the same factor applies to the whole session, so this is exact up to rounding; an open outside the adjusted high–low range is clamped (archive artefact).
- **Survivorship:** each day's archive snapshot lists every share that traded that day, including companies delisted later. The universe on day t is "every PLN share with a bar on t that passes the liquidity filter" — no current-constituent list is used, so the test is survivorship-free and does not need point-in-time index membership (`point_in_time_membership: false` because no membership is used at all).
- **Warm-up:** 430 calendar days (~295 sessions) are loaded before `--start` so SMA200 with a 21-session slope and 52-week windows exist on the first test day. If the cache starts later, signals start later (the report says when the start was clamped).
- **Missing sessions:** probable sessions (gpwlib.plcal calendar) absent from the cache inside the test window are counted and reported (`warnings.missing_sessions_count`) — never silently skipped.

## 2. Timing and fills (no look-ahead)

| Event | Rule | Why |
|---|---|---|
| Signal | computed at the close of session t with data up to t | a retail trader sees the close, then places orders |
| Fill | open of the next session in which the share has a bar | GPW opening auction; avoids same-close fills |
| No bar on that session | no fill: exits retried next session, entries expire | proxy for suspensions, no trading, halts at the static price threshold (±10% for most shares; GPW Regulamin Giełdy, Szczegółowe Zasady Obrotu Giełdowego) |
| Stop | checked on each session's low; open ≤ stop → fill at the open (gap-through), else at the stop price | a stop is not a guaranteed price on GPW (gaps) |
| Same-day entry + stop | if the entry session's low touches the stop, the trade exits at the stop that day | intraday order unknown → resolved against the user |
| Trailing stop | highest close since entry × (1 − trail%), updated at the close | |
| Order of execution | sells before buys; sale proceeds usable at once | Polish brokers let you reuse unsettled (T+2) cash for buying |
| Sizing price | share count from the signal close; reduced at the fill if cash (incl. commission) is short | an order is placed before the open is known |
| End of test | all positions sold at the last close with costs; the final year's tax is settled | results are fully realised and after tax |

Rotation (`momentum_rotation`): signals on the last session of each month,
trades on the next open; kept holdings are not resized (saves fees).

## 3. Costs

- **Commission:** per order from `gpwlib.brokers` (percentage with a minimum fee; mBank/PKO/Santander 0.39% min 5 zł, bossa 0.38% min 5 zł, XTB 0% up to the monthly turnover limit). The minimum fee is applied per order, so small positions pay relatively more; the report counts orders paying the minimum. Tariffs are today's tariffs applied to the past (stated in the report).
- **Slippage (half-spread proxy)** by 20-session median turnover (PLN): ≥ 5 mln → 5 bps; ≥ 1 mln → 10 bps; ≥ 200 k → 25 bps; ≥ 50 k → 50 bps; below → 100 bps. WIG20 names trade at 1–2 ticks (≈5–10 bps full spread), sWIG80 names typically at 20–50 bps, micro caps at 100–200 bps or more; the buckets are deliberately conservative.
- **Square-root market impact:** `coef × σ_daily × √(order / median turnover)`, `coef = 0.5` (the prefactor Y is ≈ 0.5–1 across markets: Almgren, Thum, Hauptmann & Li 2005, "Direct estimation of equity market impact", Risk; Tóth et al. 2011, "Anomalous price impact and the critical nature of liquidity in financial markets", Phys. Rev. X 1). σ = 20-session stdev of daily returns.
- **Cap:** total slippage ≤ 1000 bps (10%, the static threshold).
- **Liquidity cap:** entry order ≤ `max_adv_pct` (10%) of the 20-session median turnover — the `gpw-position-sizer` default. Exits are not capped (you must be able to get out), but their impact grows with size.
- **Stress test:** slippage and impact × 2 (tradermonty backtest-expert: test slippage at 1.5–2x). Commissions are a known tariff and are not scaled.
- **Drag reporting:** yearly PLN costs / average equity. A separate cost-free simulation would take different trades (cash and slot timing differ), so a CAGR difference between two simulations is path noise, not "the cost".

## 4. Tax

- **Regular account:** Belka tax 19% on income from the disposal of securities (art. 30b ust. 1 ustawy z 26 lipca 1991 r. o podatku dochodowym od osób fizycznych); income = proceeds − costs, costs include both commissions (art. 30b ust. 2, art. 23 ust. 1 pkt 38). The yearly net result of all sales is taxed (gains and losses net within the year, PIT-38). Base and tax rounded to full złoty (art. 63 § 1 Ordynacji podatkowej).
- **Loss carry-forward (art. 9 ust. 3 PIT):** a loss from a source reduces income from the same source in the next 5 consecutive tax years, either (pkt 1) by at most 50% of the loss in any year, or (pkt 2, in force since 1 January 2019, ustawa z 23 października 2018 r., Dz.U. 2018 poz. 2159) once by up to 5 000 000 zł in one of those years, the rest within the 50% limit. `--loss-rule pl_2019` (default) uses the one-time option in the first year where it deducts more than the 50% route; `--loss-rule 50pct` is the stricter variant. Losses older than 5 years expire.
- **Payment:** the tax for year Y is paid on the first session on or after 30 April of Y+1 (PIT-38 deadline, art. 45 ust. 1 and 1a pkt 1). It is accrued daily as a liability, so the equity curve is after tax and the payment does not create a fake drawdown.
- **Dividends:** they are inside the adjusted prices (total-return-like). In reality 19% is withheld at source (art. 30a ust. 1 pkt 4) and cannot be offset by losses. We tax the adjusted gain instead, which taxes dividends at the same 19% but lets losses offset them → the regular-account result is slightly optimistic in loss years for high-dividend holdings.
- **Interest on idle cash** (`--cash-rate`, default 0): 19% withheld at source on a regular account (art. 30a ust. 1 pkt 3).
- **IKE:** no capital-gains tax if the IKE conditions are met (art. 21 ust. 1 pkt 58a PIT; ustawa o IKE i IKZE). The test assumes the capital is already inside the IKE; the yearly contribution limit (`gpwlib.tax.IKE_LIMITS`) makes 50 000 zł a multi-year build-up in reality.

## 5. Strategies (built-in)

Common universe filter (`UNIVERSE_DEFAULTS`): close ≥ 1 zł (groszówki move in huge % ticks), 20-session median turnover ≥ 200 k zł (as `gpw-trend-screener`; below it a 10–20 k zł order is already > 5–10% of daily turnover), 10 slots (~10% each, ≈1% risk at an 8–10% stop), 10-session cooldown before re-entering a name just exited (avoids churn on the same signal).

| Strategy | Entry | Exit | Defaults | Source |
|---|---|---|---|---|
| `trend_template` | all 8 Trend Template criteria at the close (close > SMA150 & SMA200; SMA150 > SMA200; SMA200 above its value 21 sessions ago; SMA50 > SMA150 & SMA200; close > SMA50; close ≥ 1.30 × 52-week low; close ≥ 0.75 × 52-week high; RS rating ≥ 70); ranked by RS | close < SMA50; initial stop 8% below the fill | `min_rs 70, exit_sma 50, stop_pct 8` | Minervini, *Trade Like a Stock Market Wizard* (2013), ch. 5; O'Neil 7–8% loss rule (*How to Make Money in Stocks*). RS = IBD-style 2·Q1+Q2+Q3+Q4, percentile 1–99 across the eligible universe that day (same formula as `gpw-trend-screener`, re-implemented here) |
| `sma_cross` | SMA50 crosses above SMA200 that day; ranked by 126-session ROC | SMA50 < SMA200; stop 3 × ATR(14) | `fast 50, slow 200, stop_atr 3` | classic golden/death cross; ATR stop: Wilder (1978) |
| `breakout_52w` | close > highest high of the previous 252 sessions and turnover ≥ 1.5 × its 50-session median; ranked by the turnover ratio | close < lowest low of the previous 20 sessions; stop 2 × ATR(14) | `vol_mult 1.5, exit_low 20, stop_atr 2` | O'Neil (breakout volume ≥ 40–50% above average); Donchian channel / Turtle exit (Faith, *Way of the Turtle*, 2007); George & Hwang (2004), "The 52-week high and momentum investing", J. Finance 59(5) |
| `momentum_rotation` | last session of each month: rank eligible shares by the return from t−252 to t−21 (12-1 momentum), hold the top N equally weighted; keep a holding while it ranks within top N × 1.5 | monthly rebalance | `lookback 252, skip 21, top_n 10, buffer 1.5` | Jegadeesh & Titman (1993), J. Finance 48(1); skipping the last month avoids short-term reversal (Jegadeesh 1990). WSE evidence: Zaremba & Konieczka (2016) — momentum, value and size premiums on WSE largely vanish after transaction costs and liquidity constraints; the buffer is a standard turnover control |
| `dca_buy_hold` | whole capital on the first session plus an optional fixed monthly amount on the first session of each month | held to the end | synthetic WIG fund: unit = WIG/1000, TER 0.5% a year | benchmark with real costs and tax; contributions are external flows, so returns are time-weighted |

WIG is a total-return index (dividends reinvested), so the WIG benchmark and
the WIG proxy include dividends.

## 6. Custom rule format (`--rules`)

A JSON object with `entry` (required), `exit`, `stop` (`{"pct": x}` 0–50 or
`{"atr_mult": x}` 0–10), `trail_pct`, `max_hold` (sessions), `rank` (an
operand, highest first; default ROC 126) and `params` (`max_positions`,
`min_turnover_k`, `min_price`, `cooldown`). A condition is
`{"left", "op", "right"}`; groups `{"all": [...]}` / `{"any": [...]}` nest up
to 3 levels, at most 30 conditions. Operand = number or
`{"ind", "n", "of", "shift", "mult"}` with `ind` in close, open, high, low,
turnover (PLN), sma, ema, roc (%), rsi (Wilder), highest, lowest (window
includes today — use `shift: 1` for "above the previous N-day high"), atr
(Wilder), median; `n` 1–400, `shift` 0–60, `mult` > 0. Ops `>`, `>=`, `<`,
`<=`, `crosses_above`, `crosses_below` (today vs yesterday). The document is
parsed into a small tree and evaluated by a fixed interpreter — no `eval`, no
attribute access, unknown keys are errors.

## 7. Metrics

- Daily time-weighted returns (external DCA contributions removed); CAGR over calendar time; volatility, Sharpe and Sortino annualised with 252 sessions (`gpwlib.stats.SESSIONS_PER_YEAR`), risk-free = `--cash-rate`.
- Max drawdown, its peak and trough, the longest time under water (sessions from a peak to its recovery, or to the end).
- MAR = CAGR / |max DD|. t-stat of the mean daily return = mean / (sd / √n).
- Exposure = average share of (cash + positions) invested.
- Trades: win rate, average win / loss (% and zł), expectancy (% per trade, zł, R), profit factor, holding time, longest losing streak, exit reasons. R = P&L / initial risk, initial risk = shares × (fill − stop) + entry fee + exit fee at the stop (as `gpw-position-sizer`). Trades without a stop have no R.
- Concentration: share of the total P&L from the best 1 and 3 trades; total without the best 3.
- Turnover = (traded value / 2) / average equity per year.
- Benchmarks (gross, no costs or tax): WIG buy & hold; equal weight of the eligible universe, rebalanced daily (survivorship-free).
- Regime split: daily states from a `gpw-market-regime` report's history (`state_before` + `transitions`) where it covers the window, otherwise a **proxy**: WIG close above/below its SMA200 — labelled as a proxy, it is not the O'Neil regime model.

## 8. Robustness

- **Deflated Sharpe Ratio** (Bailey & López de Prado 2014, "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality", J. Portfolio Management 40(5), https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551):
  `SR0 = √V[SR] · ((1−γ) Φ⁻¹(1 − 1/N) + γ Φ⁻¹(1 − 1/(N e)))`, γ = 0.5772 (Euler–Mascheroni);
  `DSR = Φ((SR − SR0) √(T−1) / √(1 − γ3 SR + (γ4 − 1)/4 SR²))` with daily SR, skewness γ3 and (non-excess) kurtosis γ4.
  V[SR] = variance of the grid's Sharpe ratios; for a single run (`--trials N` without a grid) the variance of the SR estimator itself. N = grid size or `--trials`. The paper's example (SR 2.5, T 1250, N 100, V 0.5, skew −3, kurtosis 10 → DSR ≈ 0.90) is a unit test. With N = 1, DSR equals the Probabilistic Sharpe Ratio (Bailey & López de Prado 2012).
- **PBO via CSCV** (Bailey, Borwein, López de Prado & Zhu 2017, "The Probability of Backtest Overfitting", J. Computational Finance 20(4)): the T × N matrix of daily returns is cut into S = 12 blocks; for all C(12,6) = 924 splits, the IS-best variant's OOS relative rank ω gives a logit λ = ln(ω/(1−ω)); PBO = share of λ ≤ 0.
- **Walk-forward:** anchored (IS from the start) and rolling (IS of fixed length), default IS 3 years, OOS 1 year; the IS-best grid point by Sharpe is scored on the next OOS window. Selection works on the return streams of full-period simulations (positions carry across window boundaries — a standard simplification). OOS/IS = mean OOS Sharpe / mean IS Sharpe.
- **Plateau:** median Sharpe of the best point's grid neighbours (one step in one parameter) / best Sharpe; < 0.5 = a lone peak (tradermonty backtest-expert: look for plateaus, not peaks).
- **Bootstrap:** 2 000 resamples (fixed seed) of per-trade net returns → 95% CI of the mean; also for R.
- **Cost stress:** slippage × 2.
- **Harvey, Liu & Zhu (2016)**, "… and the Cross-Section of Expected Returns", Review of Financial Studies 29(1): with hundreds of tested factors a new result needs t > 3.

## 9. Verdict

Modelled on tradermonty `backtest-expert` (NOT_EVALUABLE below 30 trades; blocking reasons for drawdown ≥ 50%, untested slippage, < 5-year period; a "too good" flag).

| Level | Reasons | Thresholds |
|---|---|---|
| NOT_EVALUABLE | < 30 closed trades, or a benchmark strategy (`dca_buy_hold`) | 30 |
| REJECT | net CAGR ≤ 0; max DD ≥ 50%; whole bootstrap CI < 0; PBO > 0.5; DSR < 0.5 | |
| FRAGILE (blocking) | period < 5 years (one GPW cycle with a bear and a bull phase); no cost stress; 2x cost stress makes CAGR ≤ 0; too good (win rate > 90% with DD < 5%, or Sharpe > 3); bootstrap CI includes 0; DSR < 0.95; no plateau (< 0.5); walk-forward OOS/IS < 0.5 | |
| PROMISING (limiting) | no walk-forward; no PBO or PBO > 0.25; t-stat < 3; net CAGR ≤ WIG; total P&L without the best 3 trades ≤ 0 | |
| ROBUST | none of the above | |

ROBUST means "survived these tests on 2019–2026 GPW data", never "will work".

## 10. Limitations

- ~7.5 years of cached history (2019–2026): few regimes (2020 crash, 2022 bear, 2023–2026 bull). A 5-year minimum is a floor, not proof.
- Daily bars only: no intraday path, no opening-auction volume, no price-limit detection beyond "no bar".
- Today's broker tariffs applied to past years; no FX, no borrowing, long only.
- Adjusted prices can create fractional historical prices; whole shares are computed on adjusted prices (value-equivalent).
- The dividend tax approximation (section 4) and the "no bar = no fill" proxy for halts.
- Tax is paid from cash on 30 April; if the strategy is fully invested the cash balance can dip below zero until the next sale (no forced liquidation is modelled; new buys wait for cash).
- Path dependence: with a fixed number of slots, which signal gets a slot depends on timing; small rule changes can reshuffle trades. That is real, and why the plateau and PBO tests matter.
