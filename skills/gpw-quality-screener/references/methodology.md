# gpw-quality-screener: methodology

Load this file when the user asks how a number is computed, why a threshold
was chosen or how much to trust the screen.

## 1. Evidence

| Source | Finding | Used for |
|---|---|---|
| Piotroski, J. (2000). Value Investing: The Use of Historical Financial Statement Information to Separate Winners from Losers. *Journal of Accounting Research* 38 (Suppl.), 1-41. | Among high book-to-market US stocks (1976-1996), buying high-F (8-9) and shorting low-F (0-1) firms earned about 23% a year; selecting high-F firms raised the return of a plain value portfolio by at least 7.5 pp a year. The effect is strongest in small, thinly followed firms. | The nine F-score signals, the 8-9 / 0-1 bands. |
| Sloan, R. (1996). Do Stock Prices Fully Reflect Information in Accruals and Cash Flows about Future Earnings? *The Accounting Review* 71(3), 289-315. | Earnings driven by accruals persist less than earnings backed by cash; a long low-accrual / short high-accrual hedge earned roughly 10% a year. The top accrual decile averages around +10% of total assets. | Accruals ratio and the 10% red flag. |
| Hribar, P. & Collins, D. (2002). Errors in Estimating Accruals. *Journal of Accounting Research* 40(1), 105-134. | Accruals measured from the cash-flow statement (NI − CFO) are cleaner than balance-sheet changes. | Accruals = (NI − CFO) / average assets. |
| Novy-Marx, R. (2013). The Other Side of Value: The Gross Profitability Premium. *Journal of Financial Economics* 108(1), 1-28. | Gross profit / assets predicts returns about as strongly as book-to-market, and the two combine well. | Gross-profitability component (proxy, see section 4). |
| Asness, C., Frazzini, A. & Pedersen, L. (2019). Quality Minus Junk. *Review of Accounting Studies* 24(1), 34-112. | Quality = profitability + growth + safety + payout, built from cross-sectional ranks; high-quality stocks earn higher risk-adjusted returns in the US and 24 other countries. | Composite built from percentile ranks of profitability, earnings quality and safety. |
| Pontiff, J. & Woodgate, A. (2008). Share Issuance and Cross-Sectional Returns. *Journal of Finance* 63(2), 921-945. | Returns fall with net share issuance. | Dilution red flag (> 5% a year). |
| WSE: Piotroski F-score on WIG30 members 2014-2020 (CEEOL, https://ceeol.com/search/article-detail?id=1023567); Pilch (2021, 2023). | Abstracts report that high-F portfolios did better on the Warsaw exchange; samples are small (tens of companies, few years). | Supports the approach; treat the evidence as thin. |
| WSE: Zaremba & Konieczka (2016). | On the Warsaw exchange many factor premiums shrink or vanish after realistic trading costs. | The screen is a filter for further research, not a trading rule. |

The Polish studies above were read from abstracts, not full texts.

## 2. Data sources and what they contain

| Source | Content | History | Notes |
|---|---|---|---|
| gpw.pl card, Indicators tab (`start=indicatorsTab`) | Sector, shares issued, market value, book value, P/E (C/Z), P/BV (C/WK), dividend yield | Current only | Cached 1 day (7 days with `--all`). |
| gpw.pl card, Notoria tab (`start=showNotoria`) | Revenue, profit on sales, EBIT, pretax, net profit (parent), D&A, EBITDA, assets (current / non-current), parent equity, share capital, liabilities (current / non-current), CFO, CFI, CFF, capex | **Latest period only** | Checked 2026-11-16: the endpoint ignores `okres`, `rok`, `year`, `period`, `type`, `kw`, `nr` and `sektor`; all variants return byte-identical HTML. Values in thousands of PLN rounded to whole millions. P&L and cash flow are year-to-date. |
| Local history store (`~/.cache/gpw-trading-skills/fundamentals/history/<TICKER>.json`) | Every Notoria period this skill (or another) has seen, with restatements kept | Grows with use | Written on every run (`gpwlib.fundamentals.save_snapshot`). After about a year it holds the same window one year earlier, and then the F-score is complete. |
| User ESEF zip (`--esef`) | Full IFRS annual report (inline XBRL), current and prior year | 2 years per file | Gives cash, borrowings and gross profit when tagged. Download it from the company's investor-relations page (ESPI attachments are blocked for scripts). |
| filings.xbrl.org (`--xbrl`) | IFRS facts of Polish annual reports | FY2020-2023 only (feed stopped in 2024) | **Stale**: shown as a separate, historical F-score, never as the current one. |

What Notoria does **not** print, and the consequence:

| Missing line | Consequence |
|---|---|
| Cash, borrowings, leases | Net debt, net debt / EBITDA, EV, EV/EBITDA and EV/Sales are `null` (reason `not_in_notoria`) unless a recent ESEF report (year end at most 15 months ago) provides them. |
| Finance costs | Interest coverage is a proxy: EBIT / (EBIT − pretax profit). The denominator is net finance cost including FX and the share of associates. When it is ≤ 0 (net financial income) coverage is reported as `null` with the note `net_financial_income` and scored at the 50x cap. |
| Gross profit (cost of sales) | F #8 and gross profitability use profit on sales (*zysk ze sprzedaży*: revenue − cost of sales − selling − G&A), marked `proxy`. |
| Net profit and equity including minorities | Net income and equity are attributable to owners of the parent (`net_income_basis` / `equity_basis` = `parent`). |
| Share count | Taken from the Indicators tab (exact) and, failing that, from share capital (nominal value; a change smaller than the PLN 1 m rounding is ignored). |

## 3. Periods and annualisation

* Notoria labels: `I-III kw. 2026` = 9 months to 30 Sep 2026; `I-II kw.` = H1; `I-IV kw. 2025` or `2025` = full year. Companies with a non-calendar fiscal year (LPP, Pepco, Text) are labelled by Notoria in calendar quarters, so their period age can be overstated by up to three months.
* Annual figures: TTM = YTD(t) + FY(t−1) − YTD(t−1) when the history store holds the prior full year and the prior same window; otherwise YTD × 12 / months (`annualisation` = `ttm`, `fy` or `ytd_x_12/9`). The YTD scaling assumes no seasonality; construction, retail and energy companies are seasonal, so their H1 or 9M ratios can be biased.
* F-score changes compare **the same YTD window one year apart** (9M with 9M), so annualisation cancels out. A prior with another window, or a switch between consolidated and standalone data, makes every change signal `unknown`.
* A period whose end is more than 270 days before the as-of date is `stale_period` (quarterly reports are due 60 days after quarter end, H1 90 days, annual 120 days - Rozporządzenie Ministra Finansów z 29 marca 2018 r., § 79; 270 days = the latest normal period plus one missed update). Stale companies stay in the table with ⚠️ but emit no signals.

## 4. Piotroski F-score: definitions used

Each signal is `pass`, `fail` or `unknown`. **Missing data is never 0 or 1.**

| # | Signal (Piotroski 2000) | Here | Deviation |
|---|---|---|---|
| 1 | ROA > 0 (NI before extraordinary items / beginning assets) | NI > 0 | Assets are positive, so the sign of NI decides. |
| 2 | CFO > 0 | CFO > 0 | none |
| 3 | ΔROA > 0 | NI / period-end assets, this year vs the same window last year | Period-end assets in both years (the store rarely has the asset base two years back); the sign of the change is what matters. |
| 4 | ACCRUAL: CFO / A > ROA | CFO > NI | same denominator, so equivalent |
| 5 | ΔLEVER < 0 (long-term debt / average assets fell) | Borrowings / assets when available (ESEF, xBRL), otherwise non-current liabilities / assets (`proxy`) | Non-current liabilities include provisions, deferred tax and leases. Zero in both years = pass. |
| 6 | ΔLIQUID > 0 (current ratio rose) | current assets / current liabilities | none |
| 7 | EQ_OFFER: no common equity issued | Shares issued (Indicators history, about one year apart, ±45 days) not up by more than 0.5%; otherwise share capital (tolerance: max(0.5%, PLN 1 m rounding)) | The 0.5% tolerance ignores small option exercises. |
| 8 | ΔMARGIN > 0 (gross margin rose) | Gross profit / revenue when both years have it, else profit on sales / revenue (`proxy`) | Proxy includes selling and G&A costs. |
| 9 | ΔTURN > 0 (asset turnover rose) | Revenue / period-end assets | Period-end instead of beginning assets (see #3). |

Output:

* `f_score`: an integer 0-9 **only when all nine signals are known**; otherwise `null`.
* `f_score_partial`: `passed`, `available` (known signals), `ratio` = passed / available, `coverage_pct` = available / 9, `lower` = passed, `upper` = passed + unknown, `display` (e.g. `3/3`), `band`: `high` (8-9, or lower bound ≥ 8), `low` (0-1, or upper bound ≤ 1), `middle`, `undetermined`.
* Today the history store is new, so for most companies only signals 1, 2 and 4 are known (`3/3`, coverage 33%). This is shown on every report.
* Source preference: (1) Notoria vs the history store (current), (2) a recent ESEF report (year end ≤ 15 months ago: 12 months to the next year end + 120 days to publish), when it knows more signals. filings.xbrl.org and older ESEF files are shown as separate historical F-scores and never replace the current one.
* `--min-f N` keeps a company only when its lower bound ≥ N. A company whose upper bound is below N fails (`f_below`). A company in between is `f_undetermined` and is excluded, never guessed.

F-score does not apply to banks and insurers (Piotroski's signals assume an industrial balance sheet: deposits are not "leverage", there is no current ratio). They get `f_score: null`, `f_score_partial.applicable: false`.

## 5. Metrics

Ratios are fractions in JSON (0.12 = 12%). Averages use the same window a year earlier when the store has it, otherwise period-end values (`averages_use_prior_year`).

| Metric | Formula |
|---|---|
| ROE | annual NI / average parent equity (null if equity ≤ 0) |
| ROA | annual NI / average total assets |
| ROIC (approx.) | annual EBIT × (1 − 19%) / (total assets − current liabilities). 19% = Polish CIT rate (ustawa o CIT, art. 19 ust. 1). Capital employed is a proxy for invested capital (it keeps cash and non-operating assets in). |
| Operating, net, EBITDA margin | EBIT, NI, EBITDA / revenue |
| Gross profitability | gross profit (or profit on sales, `proxy`) / average assets (Novy-Marx 2013) |
| FCF | CFO + capex (Notoria capex is negative); FCF margin = FCF / revenue; FCF yield = annual FCF / market value |
| Cash conversion | CFO / NI (NI > 0 only) |
| Accruals ratio | (annual NI − annual CFO) / average assets (Sloan 1996, cash-flow form of Hribar & Collins 2002) |
| Liabilities / assets | (current + non-current liabilities) / assets; banks: (assets − parent equity) / assets |
| Interest coverage (proxy) | EBIT / (EBIT − pretax) - see section 2 |
| Net debt / EBITDA | (borrowings + leases − cash) / EBITDA, only from ESEF / xBRL with the source and year end stated |
| Valuation | P/E and P/BV from GPW (official), P/E computed = market value / annual NI, P/BV computed, EV/EBITDA and EV/Sales (need net debt), FCF yield, earnings yield, dividend yield (GPW) |
| Share change | shares issued now vs about a year ago, or share capital vs the prior window |

Banks and insurers: ROE, ROA, equity / assets, P/BV, P/E, dividend yield. Cost / income is `null` (`not_derivable_from_notoria`): a bank's "Przychody ze sprzedaży" mixes interest and fee income gross of interest expense, so no reliable cost base can be derived. CET1 and NPL ratios are not in the free data.

**Banks: P/BV vs ROE.** Justified P/BV = (ROE − g) / (COE − g) is linear in ROE, so the skill fits P/BV = a + b·ROE by OLS across the banks in the screen (at least 4) and reports the fitted value and the residual. A negative residual means "priced below peers for its ROE"; it is context for valuation, not a recommendation and not part of the quality score.

## 6. Quality composite 0-100

Idea of Asness-Frazzini-Pedersen: rank companies on several quality dimensions and average the ranks.

Industrial companies (weights sum to 1):

| Dimension | Component | Weight | Better |
|---|---|---|---|
| Profitability | ROIC (approx.) | 0.20 | higher |
| Profitability | ROA | 0.10 | higher |
| Profitability | Gross profitability (proxy) | 0.10 | higher |
| Earnings quality | Accruals ratio | 0.15 | lower |
| Earnings quality | FCF margin | 0.10 | higher |
| Safety | Liabilities / assets | 0.10 | lower |
| Safety | Interest coverage (proxy, capped at 50x) | 0.10 | higher |
| Fundamentals trend | F-score passed / known | 0.15 × known / 9 | higher |

Banks: ROE 0.45, ROA 0.20, equity / assets 0.35. Insurers: ROE 0.60, equity / assets 0.40.

Rules:

1. Each component is the **percentile rank within the sector-type group** of the screened universe (industrial / bank / insurer) when at least 5 companies have that metric. Otherwise, as in `ticker` mode, a fixed absolute scale is used (0 / 50 / 100 points): ROIC 0% / 8% / 20% (8% ≈ cost of capital of a Polish company); ROA 0% / 5% / 12%; gross profitability 0% / 10% / 30%; accruals +10% / 0% (60 points) / −10%; FCF margin −5% / 5% / 15%; liabilities / assets 90% / 60% / 30%; coverage 1x / 3x / 10x; bank ROE 0% / 10% / 20%; bank ROA 0% / 1% / 2%; bank equity / assets 5% / 9% / 13%; insurer equity / assets 5% / 15% / 25%.
2. The F component is absolute (ratio × 100) and its weight scales with the number of known signals, so a 3/3 company is not rewarded as much as a 9/9 one.
3. Missing components are dropped and the remaining weights renormalised. `coverage_pct` = share of the full weight with data. Below 50% the score is withheld (`quality_score: null`, `raw_score` kept).
4. `rank` and `pct_universe` are within the sector-type group; `pct_sector` is the percentile within the GPW sector (at least 3 companies, otherwise `null`). `sector_ranks` gives sector percentiles of ROE, ROIC, operating margin and FCF margin.

Ranks are relative: a quality score of 80 in a weak universe is not the same as 80 in a strong one. Compare scores within one run only.

## 7. Red flags and data checks

| Flag | Rule | Severity | Why |
|---|---|---|---|
| `negative_equity` | parent equity ≤ 0 | high | Liabilities exceed assets. |
| `loss_making` | NI < 0 | medium | |
| `cfo_negative_with_profit` | CFO < 0 < NI (industrial only) | high | Profit not backed by cash (Sloan 1996). Developers and construction companies are often here in growth phases (inventory build-up), so check the business model. |
| `high_accruals` | accruals ratio > 10% | medium | Top accrual decile in Sloan (1996). |
| `net_debt_ebitda_high` | > 3.5x (ESEF / xBRL only) | high | Typical covenant level and sub-investment-grade leverage in rating grids. |
| `net_debt_with_negative_ebitda` | net debt > 0, EBITDA ≤ 0 | high | |
| `dilution` | share count +5% y/y | medium | Pontiff & Woodgate (2008). |

Data checks (they change no number, but block signals and are listed under "Source data checks"):

| Check | Rule | Typical cause |
|---|---|---|
| `equity_near_zero` | |equity| < 0.5% of assets | Data issue or restructuring (e.g. Grupa Azoty, Nov 2026: gpw.pl shows book value −4.1 m zł). |
| `net_income_above_pretax` | NI > 1.5 × pretax, or pretax < 0 < NI | Deferred-tax credit, discontinued operations or a data error. |
| `balance_gap` | assets − parent equity − liabilities < −5% or > +50% of assets | Lines that do not add up. Gaps up to +50% are minority interests (Asseco Poland ≈ 20%). |

## 8. Signals (`signals[]`, gpw-signal-postmortem contract v1)

| kind | direction | Rule | score |
|---|---|---|---|
| `quality_high` | long | quality ≥ 80 (top quintile), weight coverage ≥ 70%, no high-severity flag, industrials with at least 3 F signals known | quality score |
| `quality_red_flags` | avoid | ≥ 2 red flags or one high-severity flag, coverage ≥ 70% | 30 per high + 15 per medium flag, max 100 |

No signal for stale periods or failed data checks. The date is the last GPW session that was final when the company's current period first appeared in the history store, so repeated runs give the same (ticker, date, kind) and the post-mortem counts it once. `price` is the unadjusted close on that date from the cached archive (omitted when not cached).

## 9. Limitations

* Free fundamentals on GPW are thin: one period per company, rounded to PLN 1 m, parent-only profit and equity, no cash, debt, finance costs or gross profit.
* The F-score needs a year of local history (or a user ESEF file) to be complete.
* The universe is today's index membership (not point-in-time), so a historical `--as-of` run has look-ahead in membership. `--as-of` rebuilds the company data from the history store only, as it was known on that date (later restatements are excluded).
* Periods differ across companies (H1, 9M, fiscal years), so ranks mix periods; the period is shown in every row.
* Licence: gpw.pl / Notoria data stays in the local cache and must not be redistributed (`docs/DATA_SOURCES.md`).
