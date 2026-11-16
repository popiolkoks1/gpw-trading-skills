# gpw-valuation: methodology

This skill values one GPW company in two ways:

- **what the current price implies** (reverse DCF; for banks, the implied
  ROE);
- **a fair-value range** under explicit assumptions, each with its source.

It never gives a price target. All numbers come from
`scripts/valuation_engine.py`, which is pure, stdlib-only and unit-tested
against hand calculations.

Upstream model: Anthropic's financial-services plugins `dcf-model` and
`comps-analysis` (https://claude.com/docs/office-agents/fsi-plugins.md). They
give the WACC build, the WACC × g sensitivity table and the comps statistics.
This skill adds three things they lack:

- a Polish country-risk adjustment;
- PLN currency consistency (risk-free rate, ERP and cash flows are all in
  PLN, nominal);
- the reverse DCF as the headline.

## 1. Order of work

1. Snapshot from gpw.pl: indicators tab (shares, market value, book value,
   P/E, P/BV, dividend yield) and Notoria tab (latest period, P&L, balance
   sheet, cash flow).
2. Annual figures (section 8), then price and beta (section 3).
3. Discount rate (section 2).
4. Model:
   - banks and insurers: excess-return model (section 6);
   - everyone else: reverse DCF, then 2-stage FCFF DCF (sections 4–5).
5. Sensitivity grid and scenarios.
6. Comps (section 7) and a DDM cross-check (section 6.2).

## 2. Discount rate: CAPM in PLN, Damodaran's method

### 2.1 Risk-free rate

Damodaran (*Country Risk: Determinants, Measures and Implications*, 2025
edition, SSRN 4161010; and the `ctryprem` page) states that a local-currency
government yield is a risk-free rate only if the government cannot default.
Poland is rated A2 (Moody's), so the 10-year PLN yield contains a default
spread. His rule:

    rf(PLN) = PL 10Y government yield − Poland default spread

Default (`--rf-mode damodaran`):

- the default spread is the "Adj. Default Spread" from Damodaran's ctryprem
  table: 0.72% for A2 (update of 5 January 2026);
- example: 6.28% (BondSpot DS0436, 2026-11-16) − 0.72% = **5.56%**.

`--rf-mode gross` keeps the full yield instead. Use it only to compare with
analysts who do so. It double-counts country risk, because the CRP is still
added to the ERP below.

Sources of the PL 10Y yield, in order:

1. `--rf` (or the `rf` key in the assumptions JSON);
2. `indicators.pl10y` of the newest **gpw-macro-regime** JSON, found with
   `gpwlib.reports.find_report`. A report more than 3 sessions old is
   replaced by a live fetch, or used with a warning when nothing fresher
   exists;
3. live BondSpot fixing: the fixed-rate bond whose remaining maturity is
   nearest 10 years (within 2 years), the same rule gpw-macro-regime uses;
4. the ECB monthly convergence yield (IRS M.PL.L.L40.CI.0000.PLN.N.Z);
5. if none of these works: exit 1. The skill never invents a yield.

### 2.2 Equity risk premium

    ERP = mature-market ERP + Poland country risk premium (CRP)

Both numbers come from the Damodaran ctryprem row for Poland, January 2026:

| Item | Value |
|---|---|
| Total ERP | 5.33% |
| CRP | 1.10% |
| Mature-market ERP (ERP − CRP) | 4.23% |

- The CRP is the default spread × the relative volatility of equities vs
  bonds (Damodaran's "adjusted" CRP).
- The page is fetched through `gpwlib.http` and cached for a week. If the
  fetch fails, the embedded values above are used, with their date and URL.
- A warning appears when the table is more than 12 months old, because
  Damodaran republishes every January.

### 2.3 Beta

- **Regression.** OLS of weekly returns of the stock on weekly returns of
  WIG over 2 years (~104 weeks). Weekly closes are the last session of each
  ISO week, taken from dividend- and split-adjusted closes
  (`gpwlib.panel.build_panel`). A week missing in either series skips that
  pair.
- **Why weekly over 2 years.** Daily returns understate beta for thinly
  traded stocks (Dimson 1979, *J. Financial Economics* 7). Two years of
  weekly data is the Bloomberg default and a common compromise between noise
  and staleness.
- **Blume adjustment.** β_adj = 2/3 · β_raw + 1/3 (Blume 1971, *J. Finance*
  26(1); Blume 1975). Betas regress toward 1 over time.
- **Bounds.** 0.3–2.0 after adjustment. Damodaran's global industry betas
  (January 2026) span about 0.4–1.7, so a value outside the bounds on GPW
  usually reflects thin trading or a one-off event rather than risk. Bounding
  triggers a warning.
- **Fallbacks.** With fewer than 52 weekly returns, beta = 1.0 with a
  warning. If R² < 0.05, a warning says the beta is noisy.
- The beta is a levered equity beta and is used directly in CAPM.

    Ke = rf + β × ERP

### 2.4 Cost of debt and tax

Damodaran's cost of debt for a company in a risky country:

    Kd = rf + country default spread + company default spread
       = PL 10Y + company default spread

- The **company default spread** comes from Damodaran's synthetic rating
  table ("Ratings, Interest Coverage Ratios and Default Spread", large
  non-financial firms, January 2026).
- **Interest coverage** = EBIT / net financial cost, where the net
  financial cost is operating profit minus pre-tax profit. With a net
  financial income, the best rating applies.
- **Tax.** 19% Polish CIT (ustawa o CIT, art. 19 ust. 1 pkt 1). The 9% rate
  for small taxpayers does not apply to listed companies of meaningful size.

### 2.5 WACC

    WACC = E/(D+E) · Ke + D/(D+E) · Kd · (1 − t)

- **E** = price × shares issued (market value).
- **D** = max(net debt, 0). Net cash gets zero weight, so WACC = Ke.
- **Net debt.** The Notoria tab has no debt and cash split, so the default is
  a proxy:

      net debt ≈ annual net financial cost / Kd
      net cash ≈ annual net financial income / PL 10Y

  - It is capped by total liabilities (for debt) or current assets (for
    cash).
  - It is flagged **low confidence** on every run. FX gains and losses, and
    income from associates, distort it.
  - Pass `--net-debt` (mln PLN) from the latest balance sheet whenever you
    can.

## 3. Price data

- Last close from the GPW archive up to `--as-of`. Market cap = price ×
  shares issued.
- `--as-of` moves the price date and the beta window only. Fundamentals stay
  the latest snapshot, because the free sources keep no point-in-time
  history; the report says so.

## 4. Reverse DCF (the headline)

Mauboussin & Rappaport, *Expectations Investing* (2001, revised 2021): start
from the price and solve for the expectations it implies.

    EV_market = market cap + net debt
    EV(g) = Σ_{t=1..10} FCF0·(1+g)^t / (1+WACC)^t
            + [FCF0·(1+g)^10·(1+g_T) / (WACC − g_T)] / (1+WACC)^10

- **Solving.** EV(g) increases strictly with g when FCF0 > 0, so bisection
  on g ∈ [−50%, +100%] finds the unique root (tolerance 1e-9).
- **Edge statuses.** `below_range` / `above_range` when the root lies outside
  that interval. `not_solvable` when FCF0 ≤ 0.
- **No-growth benchmark.** The report also shows the constant FCF the EV
  implies with zero growth: EV × WACC.
- **FCF0.** In order:
  1. `--fcf`;
  2. CFO − capex (annual);
  3. if that is ≤ 0, normalised FCFF = EBIT(1−t) + D&A − capex, with a
     warning.

  CFO − capex approximates FCFF because IAS 7.33 lets Polish issuers report
  interest paid in financing cash flows, where many do. If an issuer
  reports it in CFO, FCF0 is slightly understated.
- **Horizon.** 10 years, the usual "competitive advantage period" in
  Mauboussin and Damodaran.

## 5. 2-stage FCFF DCF

Damodaran, *Investment Valuation* (3rd ed., 2012), ch. 15; Koller, Goedhart
& Wessels, *Valuation* (McKinsey, 7th ed., 2020), ch. 10–11.

Years 1–5:

    Rev_t = Rev_{t−1}(1+g1);  FCFF_t = Rev_t·m·(1−t) + Rev_t·da − Rev_t·capex − nwc·(Rev_t − Rev_{t−1})

Terminal value (Gordon growth; Gordon 1962):

    TV_5 = FCFF_6 / (WACC − g_T), where FCFF_6 uses Rev growing at g_T

Value per share = (Σ PV FCFF + PV TV − net debt) / shares. Cash flows are
discounted at the end of each year.

### 5.1 Defaults

Each default is listed in the report with its source.

| Driver | Default | Why |
|---|---|---|
| g1 (revenue growth, years 1–5) | y/y YTD revenue growth from the local history, clipped to −10%…+20%; otherwise **5%** | 5% ≈ Polish nominal GDP growth: the NBP 2.5% inflation target plus ~2.5% potential real growth. Clipping follows the evidence that high growth rarely persists (Chan, Karceski & Lakonishok 2003, *J. Finance* 58(2)). |
| m (EBIT margin) | annual EBIT / revenue | latest data |
| da, capex | annual D&A / revenue, capex / revenue | latest data |
| nwc | (current assets − current liabilities) / revenue, clipped 0–25% | Includes cash, so it is an approximation. Applies to incremental revenue only. |
| g_T (terminal growth) | **2.5%** (NBP inflation target), capped at rf | Zero real growth for ever is conservative. Damodaran: g_T ≤ rf, because no firm can outgrow the economy for ever. |

### 5.2 Checks

- g_T ≥ WACC (or ≥ COE for banks) → **error**, exit 1. The Gordon formula
  has no finite value then.
- g_T above rf → warning.
- Capex below D&A while g_T > 0 → warning. Growth without reinvestment
  overstates value (McKinsey ch. 10).
- Terminal value more than 75% of EV → warning. The result then depends
  mostly on long-run assumptions.
- Implied RONIC of the terminal year → warning when > 50% or < ½ WACC.
  RONIC = g_T / reinvestment rate, with reinvestment rate = 1 − FCFF/NOPAT
  (McKinsey's value-driver formula, ch. 10).

### 5.3 Sensitivity and scenarios

- **Sensitivity grid.** WACC base −2…+2 pp in 1 pp steps × g_T 0, 1, 2, 3,
  4%. Value per share; cells with g ≥ WACC are empty.
- **Scenarios.** Symmetric and illustrative. Override them with the
  `scenarios` object in the assumptions JSON.

| | revenue growth | EBIT margin | WACC | g_T |
|---|---|---|---|---|
| bear | −3 pp | ×0.85 | +1 pp | −0.5 pp |
| base | — | — | — | — |
| bull | +3 pp | ×1.15 | −1 pp | +0.5 pp |

- Why these sizes:
  - ±1 pp of WACC is about the spread between reasonable ERP and beta
    choices;
  - ±3 pp of growth and ±15% of margin span a normal cycle for GPW
    industrials.
- `fair_value_range` = {low: min(bear, base, bull), base: base, high: max}.

## 6. Banks and insurers; dividends

### 6.1 Excess-return model (justified P/BV)

For a bank, debt is raw material, not financing, so FCFF and WACC are not
defined (Damodaran ch. 21). The equity value is book value plus the present
value of excess returns. In steady state this becomes:

    justified P/BV = (ROE − g) / (COE − g)

- **ROE** = annual net profit attributable to the parent / parent equity at
  the end of the period. The Notoria tab gives no average equity, which the
  report flags.
- **COE** = CAPM Ke (section 2).
- **g** = 2.5% by default, capped at rf.
- **Reverse version (the headline for banks).**
  - Implied ROE = g + P/BV_market · (COE − g).
  - Implied g = (P/BV·COE − ROE)/(P/BV − 1). This is the contract field
    `implied_growth_pct` for banks.
- **Sensitivity.** COE ±2 pp × g 0–4%.
- **Scenarios.**
  - ROE −3 / +2 pp: one credit-cycle swing for Polish banks in 2015–2025.
    The upside is smaller because Polish bank ROE was then near a cyclical
    high.
  - COE ±1 pp.
  - g ±0.5 pp.
- `wacc_pct` in the JSON holds the COE for banks; the report says so.
- Auto-detection: the company is in WIG-BANKI, or its gpw.pl sector
  contains "bank" or "ubezpiecz". Override with `--model`.

### 6.2 Gordon DDM (cross-check)

    value = DPS0 · (1+g) / (COE − g)

- **DPS0** = gpw.pl trailing dividend yield × price, or the `dps_pln` key in
  the assumptions JSON.
- **Payout** = dividend yield × P/E.
- **Stable payer** = payout between 20% and 100%. Otherwise the DDM is
  marked indicative.
- The DDM is never part of `fair_value_range`.

## 7. Comparable companies

- **Peers.** By default, the other constituents of the company's GPW sector
  index (`gpwlib.constituents`, GPW Benchmark portfolio). Up to
  `--max-peers` (default 15) by index weight. Override with `--peers`.
  Companies outside every sector index (e.g. KETY) get no automatic peers.
- **Multiples.**
  - P/E and P/BV: the official gpw.pl indicators (trailing, currency-neutral).
  - EV/EBITDA and EV/Sales: annual Notoria lines plus the same net-debt
    proxy. These two are not computed for foreign issuers (non-PL ISIN),
    whose reporting currency may differ.
- **Exclusions.** Multiples ≤ 0, or above P/E 100, P/BV 30, EV/EBITDA 50 or
  EV/Sales 30, are excluded as not meaningful (Koller et al. ch. 18;
  Damodaran, *Damodaran on Valuation*, ch. 7).
- **Statistics.** Median and interquartile range (Q1–Q3, linear
  interpolation as in Excel QUARTILE.INC).
- **Implied value per share.**
  - Equity multiples: multiple × metric.
  - EV multiples: multiple × metric − net debt.
  - For P/E and P/BV, the target's metric is the earnings and book value
    behind its own gpw.pl ratios, so the comparison is like for like.
- **Warnings.** Fewer than 5 peers with data → warning: a median of 2–4
  companies is not representative. GPW sector indices are small, so CEE and
  EU peers would help, but there is no free source for them.
- Banks use only P/E and P/BV.

## 8. Fundamentals: YTD data and its limits

- Notoria shows **one period**, year-to-date ("I-III kw. 2026" = 9 months),
  in thousands of PLN. Balance-sheet lines are at the period end.
- Annual flows, in order:
  1. a full year as reported;
  2. **TTM** from the local history store
     (`fundamentals.save_snapshot` / `load_history`):
     YTD + previous full year − previous YTD;
  3. **annualised** YTD × 12/months, with a warning.
- Each run adds the snapshot to the history store (unless
  `--no-history-save`), so TTM becomes available after a year of use.
- **Seasonality.** Annualising distorts seasonal businesses. Examples:
  - retail and apparel earn most in Q4;
  - game studios depend on release dates;
  - utilities depend on tariffs.
- Banks and insurers use the same Notoria labels, but "Przychody ze
  sprzedaży" is not revenue in the industrial sense. That is why they use
  the equity model.
- **Freshness.** Polish periodic reports are due 60 days after a quarter and
  4 months after the year end (Rozporządzenie MF z 29.03.2018, § 79). The
  newest period can therefore legitimately be about 7 months old; beyond
  215 days the report warns.

## 9. Limitations (state them to the user)

- The net debt is a proxy unless the user supplies it.
- There is one data point per line: no multi-year history of margins or
  growth. The history-based defaults improve as the local store grows.
- Minority interests and non-operating assets (stakes in associates, real
  estate) are not adjusted.
- Comps on GPW are small and heterogeneous.
- A DCF is only as good as its assumptions. The sensitivity grid shows how
  fragile the point estimate is, which is why the skill leads with what the
  price implies, not with a target.

## References

- Damodaran, A. (2012). *Investment Valuation*, 3rd ed. Wiley.
- Damodaran, A. (2025). *Country Risk: Determinants, Measures and
  Implications – The 2025 Edition*. SSRN 4161010.
- Damodaran, A. Country Default Spreads and Risk Premiums (updated 5 Jan
  2026),
  https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html
- Damodaran, A. Ratings, Interest Coverage Ratios and Default Spread (Jan
  2026),
  https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html
- Koller, T., Goedhart, M., Wessels, D. (2020). *Valuation: Measuring and
  Managing the Value of Companies*, 7th ed. McKinsey & Company / Wiley.
- Gordon, M. J. (1962). *The Investment, Financing, and Valuation of the
  Corporation*. Irwin.
- Blume, M. E. (1971). On the assessment of risk. *Journal of Finance*
  26(1), 1–10.
- Blume, M. E. (1975). Betas and their regression tendencies. *Journal of
  Finance* 30(3), 785–795.
- Dimson, E. (1979). Risk measurement when shares are subject to infrequent
  trading. *Journal of Financial Economics* 7(2), 197–226.
- Mauboussin, M. J., Rappaport, A. (2001; rev. 2021). *Expectations
  Investing*. Harvard Business School Press / Columbia Business School
  Publishing.
- Chan, L. K. C., Karceski, J., Lakonishok, J. (2003). The level and
  persistence of growth rates. *Journal of Finance* 58(2), 643–684.
- Anthropic financial-services plugins: `dcf-model`, `comps-analysis`
  (https://claude.com/docs/office-agents/fsi-plugins.md).
- Ustawa z dnia 15 lutego 1992 r. o podatku dochodowym od osób prawnych,
  art. 19.
