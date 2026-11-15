# GPW Portfolio Check — methodology

Contents: 1. Data · 2. Values and P&L · 3. Buckets · 4. Concentration ·
5. Risk statistics · 6. Liquidity · 7. Portfolio heat · 8. Flags and statuses ·
9. Limitations

## 1. Data

- Shares: GPW *Archiwum notowań* (type 10), ETFs: type 241 (downloaded only if
  an ETF is held), WIG: indices archive (type 1). Last `lookback + 6` sessions.
- Prices are adjusted backwards for dividends and splits from GPW's published
  "% change" (see `gpwlib/panel.py`), so returns are total-return-like.
  Position **values use the last unadjusted close** (the adjustment only
  rescales earlier prices).
- Sector and size membership: current portfolios of GPW Benchmark indices
  (14 sector indices; WIG20, mWIG40, sWIG80). Not point-in-time.

## 2. Values and P&L

value = shares × last close; weight = value / (Σ values + cash).
Unrealised P&L = (last − avg_price) × shares.

- **CSV mode**: `avg_price` comes from the broker. Brokers usually show a
  weighted average of all purchases; Polish tax law (art. 24 ust. 10 ustawy
  o PIT, PIT-38) uses **FIFO** – after top-ups and partial sales the tax cost
  basis differs.
- **Journal mode**: `avg_price` = cost of the remaining FIFO lots
  (`gpwlib.journal_io.open_positions`), i.e. the tax-relevant basis
  (commissions excluded).
- Indicative tax = 19% × max(0, Σ unrealised result on the regular account).
  Ignores fees, losses realised earlier in the year and IKE/IKZE/OKI rules.

## 3. Buckets

Sector: GPW sector index key, `ETF`, or `OTHER` (no sector index – e.g.
insurers like PZU, holdings, most small caps). Size: WIG20 / MWIG40 /
SWIG80 / OTHER / ETF. Accounts: value per account type.

## 4. Concentration

HHI = Σ wᵢ² over position weights of the invested part (0–1); effective
number of positions = 1 / HHI (Adelman 1969). Levels follow the US DOJ/FTC
Merger Guidelines (2023): HHI > 0.18 (1 800) = highly concentrated,
0.10–0.18 = moderately concentrated. An ETF counts as one position (no
look-through into its basket), so the "high concentration" flag is raised
only when single stocks are more than 50% of the invested value.

## 5. Risk statistics (historical simulation of today's weights)

Window: the last `lookback` (default 252 = 1 GPW year) WIG sessions; dates
where every included holding has a close. Daily simple returns.

- Portfolio return rₚ,ₜ = Σ wᵢ rᵢ,ₜ with wᵢ = value share of the **total**
  portfolio (cash = zero return, zero volatility), constant weights (daily
  rebalanced) — the standard "what would today's portfolio have done" view.
- Volatility = stdev(rₚ) × √252; also for the equity part alone (weights
  rescaled to 100%).
- Beta = cov(rₚ, r_WIG) / var(r_WIG).
- **VaR 95%** (1 session, historical): the empirical 5% quantile of rₚ —
  the ⌈0.05·n⌉-th worst return — × total value (`gpwlib.stats.historical_var`).
  **ES 95%** = mean of the ⌈0.05·n⌉ worst returns (Acerbi & Tasche 2002).
- 20-session VaR = 1-day VaR × √20 (square-root-of-time rule). Caveat: it
  assumes i.i.d. returns; in stress losses cluster (volatility clustering),
  so the true monthly tail is typically fatter (Danielsson & Zigrand 2006).
- Max drawdown and current drawdown of the cumulative curve of rₚ; the same
  for WIG. 1-year return of rₚ vs WIG, difference in percentage points.
- Correlation matrix of daily returns; pairs with ρ > 0.7 listed and joined
  into clusters (connected components). 0.7 is the conventional "strong"
  correlation cut-off; GPW banks typically correlate at 0.8–0.9.
- Holdings with fewer than 61 closes in the window (~3 months) are excluded
  from risk statistics (VaR/correlation on < 60 observations is noise) and
  flagged; risk is then understated by their weight.

## 6. Liquidity

days to exit = value / (10% × median daily turnover over 60 sessions).
10% participation is a common cap for executing without moving the price
(and the `gpw-position-sizer --max-adv-pct` default); 60 sessions ≈ one
quarter reflects structural liquidity rather than one busy week. > 5 sessions
(a trading week) = illiquid. Zero turnover = cannot be estimated = illiquid.

## 7. Portfolio heat

Open risk = Σ max(0, last − stop) × shares for lots with a stop; heat = open
risk / total value. A stop at or above the current price contributes 0 (and
the position is flagged "at or below stop" – the plan says it should already
be closed). Computed only when stops are known (journal or a `stop` column).
Reference ceiling ~6% (Elder, *Come Into My Trading Room*, 2002) – used as a
hard limit by `gpw-pre-trade-gate`.

## 8. Flags and statuses

| Code | Rule | Why |
|---|---|---|
| position_weight | non-ETF weight > 20% | one stock can cost a fifth of the portfolio; UCITS caps single issuers at 10% (20% for index trackers); O'Neil/Minervini cap concentrated traders at ~20–25% |
| sector_weight | sector > 35% | one sector shock (bank tax, regulation, commodity) hits a third of the portfolio; same limit as the pre-trade gate |
| hhi_high | HHI > 0.18 and stocks > 50% | §4 |
| illiquid | days to exit > 5 or unknown | §6 |
| correlated / correlated_cluster | ρ > 0.7 | diversification is smaller than the position count suggests |
| no_stop | stops tracked and a lot has no stop | undefined risk |
| below_stop | last ≤ stop | the plan's exit level has been reached |
| stale_quote | no close for > 5 sessions | suspension or very thin market; value may be unreliable |
| check_avg_price | last / avg < 0.2 or > 5 | typical of an unadjusted split in the export or a typo |
| short_history | < 61 closes in the window | §5 |

A position with any reason is **REVIEW**, otherwise **OK**. The script never
recommends selling; REVIEW means "check whether this is intended".

## 9. Limitations

- No look-through into ETFs; no FX exposure analysis (foreign ETFs on GPW
  are PLN-quoted but carry currency risk).
- Constant-weight backtest ignores how weights actually drifted and
  dividends paid out (adjusted prices reinvest them implicitly).
- Membership not point-in-time; insurers and conglomerates have no sector.
- Market data quality is that of the GPW archive; a missing session for an
  illiquid stock means "no trade", not zero price.
