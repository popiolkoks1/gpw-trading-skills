# GPW Pair Trading — methodology

Contents: 1. Framing on GPW · 2. Data · 3. Statistics · 4. Candidate pairs and multiple testing ·
5. State, bands and signals · 6. Backtests · 7. Hedge legs (futures) · 8. Thresholds table ·
9. Limitations · 10. References

## 1. Framing: relative value, not "long X / short Y"

Classic pairs trading (Gatev, Goetzmann & Rouwenhorst 2006) buys the cheaper and sells
short the richer of two stocks that normally move together, then closes when the gap
closes. On GPW a retail investor usually **cannot short a single stock cheaply**:

- Short selling needs a stock loan arranged by the broker (Rozporządzenie Ministra
  Finansów on lending securities with brokerage houses, https://eli.gov.pl/eli/DU/1999/1269/ogl/pol/pdf).
  Brokers publish their own short lists, take collateral (e.g. 130% of the borrowed
  value) and charge a borrow fee; availability changes and is not guaranteed.
- Net short positions of at least 0.1% of capital are reported to KNF and from 0.5% made
  public (EU Short Selling Regulation 236/2012; KNF register, see `gpw-ownership`).
- **Single-stock futures** are the practical short leg. The GPW archive (type 35, session
  2026-11-13) lists futures on 44 shares, including every large bank (PKO, PEO, MBK, ING,
  MIL, ALR, EBP) and fuels/energy (PKN, PGE, TPE, ENA). The contract multiplier differs by
  stock (1, 10, 100 or 1000 shares — GPW KID, https://www.gpw.pl/pub/GPW/files/PDF/standardy_pl/KID/KID_GPW_futures_akcyjne_PL_2023.pdf;
  e.g. mBank and Santander/Erste series moved from 100 to 10 in 2019,
  https://bank.pl/gpw-wprowadza-zmiany-na-rynku-instrumentow-pochodnych/), so the skill
  does not compute stock-futures contract counts — check the current GPW contract
  specification. Turnover of most stock futures is small (a few hundred thousand PLN a
  day outside PKO, PKN, KGHM, CDR). Futures are not allowed in IKE/IKZE.
- WIG20 futures (FW20, multiplier 20 PLN) are liquid; sizing an index hedge is the job
  of `gpw-risk-lab --mode hedge`. Sector-index futures exist (codes BAS, FIN, GMS =
  WIG.MS-BAS, WIG.MS-FIN, WIG.GAMES) but had zero turnover on 2026-11-13.

The skill therefore supports three uses and never phrases output as an order:

1. **Switch decision** inside a sector ("I hold PKO — is PEKAO relatively cheap?").
   Long-only, feasible on every account incl. IKE/IKZE.
2. **Hedge leg** — the hedge ratio says how much of the other stock (or its future)
   offsets one leg.
3. **Spotting dislocations** — a large |z| on a pair that historically mean-reverted.

## 2. Data

- Daily closes of GPW shares from the official archive (`gpwlib.gpw_archive`), adjusted
  for dividends, splits and rights issues by `gpwlib.panel.build_panel(adjust=True)`;
  renamed tickers are merged (`panel.RENAMES`, e.g. SANPL → ERSTEPL, CCC → MODIVO).
- Default load 750 sessions (~3 years); the cointegration test uses the last 500
  (~2 years, the window of the tradermonty screener); the extra year feeds the rolling
  stability check.
- Two legs are aligned on common sessions only. Log prices are used throughout so the
  hedge ratio is an elasticity (a 1% move of B ~ β% move of A) and the spread is in
  log points.

## 3. Statistics (all in `scripts/pair_stats.py`, stdlib, unit-tested)

**Hedge ratio.** OLS `ln A_t = α + β ln B_t + u_t` (Engle-Granger step 1). In money terms,
10 000 zł in A corresponds to β × 10 000 zł in B. OLS is super-consistent under
cointegration but biased towards zero in small samples (Banerjee et al. 1986); the
rolling β path shows how stable it is.

**Engle-Granger test** (Engle & Granger 1987). Step 2 runs an Augmented Dickey-Fuller
regression without deterministic terms on the residuals:
`Δu_t = γ u_{t-1} + Σ_{j=1..p} φ_j Δu_{t-j} + e_t`, H0: γ = 0 (no cointegration).

- *Lag order p* minimises AIC over 0..p_max on a common sample, p_max = ⌈12 (T/100)^{1/4}⌉
  (Schwert 1989); the chosen model is re-estimated on all usable observations. This is
  the `adfuller(autolag="AIC")` procedure of statsmodels; the implementation reproduces
  statsmodels 0.14 to 1e-8 (statistic, lag, p-value) on the reference series in the tests.
- *Distribution.* Residuals of a regression with an estimated constant do not follow the
  Dickey-Fuller table. The statistic is compared with the **MacKinnon tables for N = 2
  variables with a constant**:
  - critical values from MacKinnon (2010), Table 2 response surfaces
    `cv(T) = β∞ + β1/T + β2/T² (+ β3/T³)`; for N = 2: 1% (−3.89644, −10.9519, −33.527),
    5% (−3.33613, −6.1101, −6.823), 10% (−3.04445, −4.2412, −2.720);
  - approximate p-values from MacKinnon (1994, 1996): `p = Φ(c0 + c1 τ + c2 τ² [+ c3 τ³])`
    with a small-p polynomial below τ* = −2.62 (2.92, 1.5012, 0.039796) and a large-p one
    above (2.1945, 0.64695, −0.29198, −0.042377); p = 0 below −18.86, 1 above 0.92.
  The N = 1 tables (constant / none) are included for the plain ADF test.
- *Order.* EG is not symmetric. The skill runs A on B and B on A and reports the
  **larger** p-value, so the result does not depend on the order the user typed.

**Half-life.** OLS `Δs_t = a + b s_{t-1} + e_t`; φ = 1 + b is the AR(1) coefficient and
`HL = −ln 2 / ln φ` sessions. None when b ≥ 0 (no reversion). A half-life far above the
holding period you have in mind (e.g. > 60 sessions) makes the pair impractical even if
cointegrated; very short ones (< 5) are often noise or bid-ask bounce.

**Hurst exponent** (secondary). Slope of log sd(s_{t+k} − s_t) on log k for k = 2..20:
0.5 random walk, < 0.5 mean reverting. Noisy; never used as a filter.

**Correlation.** Pearson correlation of **daily log returns** (filter, see §4) and of log
price levels (reported only).

**z-score.** `z_t = (s_t − mean(s_{t−59..t})) / sd(s_{t−59..t})`, a 60-session (~3 months)
rolling window using past data only. The percentile of today's z within the window's
z history is reported too.

**Rolling stability.** Engle-Granger (both directions) on rolling 250-session windows
ending at the last session and every 21 sessions before (≈ monthly) → share of windows
with p < 0.05. Do & Faff (2010) show that pair profits decayed largely because
relationships stopped converging; a pair cointegrated in the full window but in few
1-year windows is fragile. The same windows give the **hedge-ratio drift** (β of the last
year vs the full window, min–max).

**Liquidity.** Median daily turnover over 60 sessions for each leg must be at least
position value / 10% (10 000 zł per leg → 100 000 zł), the order-size rule used by
`gpw-position-sizer` and `gpw-pre-trade-gate`.

## 4. Candidate pairs and multiple testing

- Candidates: every pair inside each requested GPW sector index (`gpwlib.constituents`,
  current GPW Benchmark portfolios — not point-in-time) plus `--pair A,B`. `--cross`
  adds pairs across the listed sectors (e.g. fuels × energy).
- Pre-filters (before any test): ≥ 250 common sessions (one year) and liquidity of both
  legs. Explicit `--pair` items skip the liquidity pre-filter (the flag is still shown).
- **Every pre-filtered pair is tested**; the correlation threshold (≥ 0.70 on daily
  returns) is applied after the test as a second condition, so the number of tests m
  is not reduced by peeking. tradermonty's screener applies 0.70 to price levels; on GPW
  level correlations of trending peers are near-trivially high (ORLEN–MOL 0.94 on levels,
  0.30 on returns), so returns are the stricter, more meaningful basis.
- **Multiple-testing correction.** Testing m pairs at 5% produces about 0.05 m false
  "cointegrated" pairs by chance (45 bank pairs → ~2). Harvey, Liu & Zhu (2016) show the
  same problem across finance research. Default: **Benjamini-Hochberg (1995)** adjusted
  p-values (q-values), controlling the false discovery rate at 5%; valid for independent
  or positively dependent tests (Benjamini & Yekutieli 2001), which fits pairs that share
  legs. `--correction bonferroni` controls the family-wise error (stricter);
  `--correction none` is allowed but the report warns. A pair passes when
  `p_adj < 0.05`, return correlation ≥ 0.70 and β > 0.
- In `pair` / `switch` mode the user names one pair in advance, so m = 1 and the raw
  p-value is used (stated in the notes).

## 5. State, bands and signals

| Band | Value | Source |
|---|---|---|
| Entry | \|z\| ≥ 2 | Gatev et al. (2006) open at a two-standard-deviation divergence; also tradermonty |
| Exit | z crosses 0 | GGR close when prices cross (spread back at mean) |
| Stop | \|z\| ≥ 3.5 | beyond 3.5σ the relationship has likely changed (corporate event, re-rating); about 1 in 2 000 sessions under normality, so a hit is evidence against the model, not an opportunity |

State: `a_rich` (z ≥ 2), `a_cheap` (z ≤ −2), `beyond_stop`, `neutral`.

**Signals** (generic contract of `gpw-signal-postmortem`): one `pair_dislocation` signal
per *cointegrated* pair with 2 ≤ |z| < 3.5. `direction` is `long` for the **cheap leg
only** — the skill never emits a short. `date` is the first session of the current
dislocation episode (so reruns deduplicate), `price` the unadjusted close that day,
`score` = 100 × stability, `tags` = pair, rich leg, z, β, adjusted p, half-life.
Caveat: the post-mortem judges the cheap leg against WIG, not against the rich leg, so it
measures "the cheap leg beat the market", a stricter test than convergence.

## 6. Backtests (illustrative, clearly labelled)

- Window split: first 60% in-sample, last 40% out-of-sample. α and β are estimated on
  the in-sample part only; the z-score is rolling (past data only).
- **Spread rule** (two-legged): open at |z| ≥ 2 (short A / long B when z > 0), close at
  the mean, at the 3.5σ stop or at the period end; no re-entry until |z| has been back
  below 2. Legs: V zł in A, β·V zł in B (fixed share counts). Costs: broker commission on
  all four orders (`gpwlib.brokers`, default mBank 0.39% min 5 zł) and an **assumed**
  borrow fee on the short leg (`--borrow-pct`, default 0% — set your broker's rate).
  Short availability is assumed; in reality it may not exist.
- **Switch rule** (long-only, feasible for retail): start in A; move everything to B
  when z ≥ 2, back to A when z ≤ −2. Two commissions per switch; compared with holding
  A, holding B and 50/50. Capital-gains tax (19% Belka) on switches in a regular account
  is not modelled; inside IKE/IKZE it does not apply.
- Few trades per pair: the numbers illustrate mechanics and costs, they are not evidence
  of an edge. Use `gpw-backtester` for systematic tests.

## 7. Evidence on GPW

GGR (2006) report ~11% a year excess returns for US pairs 1962–2002, declining over time;
Do & Faff (2010) find most of the profit gone after 2002, mainly because fewer pairs
converge. Vidyamurthy (2004) is the standard practitioner treatment of the cointegration
approach. No peer-reviewed study of cointegration pairs trading on WSE was found; the
closest is a preprint on WIG20/mWIG40 (https://arxiv.org/html/2512.02037v1). A live check
on 2026-11-13 found no pair among liquid banks (45 tested) or fuels × energy (45 tested)
cointegrated after BH correction — expect "no pair" to be the common answer.

## 8. Thresholds

| Parameter | Default | Why |
|---|---|---|
| `--lookback` | 500 sessions | ~2 years (tradermonty); enough power for EG, short enough for regime changes |
| min history | 250 | one year (tradermonty: 252 days) |
| `--z-lookback` | 60 | ~3 months; long enough to estimate sd, short enough to track the spread mean |
| `--entry-z / --exit-z / --stop-z` | 2 / 0 / 3.5 | §5 |
| `--min-corr` | 0.70 | tradermonty threshold, applied to returns (§4) |
| `--alpha` | 0.05 | conventional significance / FDR level |
| stability window / step | 250 / 21 | 1-year windows, monthly step |
| liquidity | leg ≤ 10% of 60-session median turnover | gpw-position-sizer / pre-trade gate rule |
| in-sample share | 60% | leaves ~200 sessions (~10 months) out-of-sample |

## 9. Limitations

- Sector membership is today's GPW Benchmark portfolio (look-ahead in history).
- Cointegration found in the past can break (bank taxes, mergers, rate cycles,
  dividends cut differently). The adjusted series handles ex-dividend jumps but not
  changes of dividend policy.
- Futures availability is read from one archive session; liquidity of stock futures is
  thin and their multipliers must be checked in the GPW specification.
- p-values are asymptotic approximations; with T ≈ 500 the finite-sample critical values
  are shown alongside.

## 10. References

- Banerjee, A., Dolado, J., Hendry, D., Smith, G. (1986). Exploring equilibrium relationships in econometrics through static models. *Oxford Bulletin of Economics and Statistics* 48(3), 253–277.
- Benjamini, Y., Hochberg, Y. (1995). Controlling the false discovery rate. *JRSS B* 57(1), 289–300. https://doi.org/10.1111/j.2517-6161.1995.tb02031.x
- Benjamini, Y., Yekutieli, D. (2001). The control of the false discovery rate in multiple testing under dependency. *Annals of Statistics* 29(4), 1165–1188.
- Do, B., Faff, R. (2010). Does simple pairs trading still work? *Financial Analysts Journal* 66(4), 83–95. https://doi.org/10.2469/faj.v66.n4.1
- Engle, R.F., Granger, C.W.J. (1987). Co-integration and error correction. *Econometrica* 55(2), 251–276. https://doi.org/10.2307/1913236
- Gatev, E., Goetzmann, W.N., Rouwenhorst, K.G. (2006). Pairs trading: performance of a relative-value arbitrage rule. *Review of Financial Studies* 19(3), 797–827. https://doi.org/10.1093/rfs/hhj020
- Harvey, C.R., Liu, Y., Zhu, H. (2016). … and the cross-section of expected returns. *RFS* 29(1), 5–68.
- MacKinnon, J.G. (1994). Approximate asymptotic distribution functions for unit-root and cointegration tests. *JBES* 12(2), 167–176.
- MacKinnon, J.G. (1996). Numerical distribution functions for unit root and cointegration tests. *Journal of Applied Econometrics* 11(6), 601–618.
- MacKinnon, J.G. (2010). Critical values for cointegration tests. Queen's Economics Department Working Paper No. 1227. https://www.econ.queensu.ca/sites/econ.queensu.ca/files/qed_wp_1227.pdf
- Schwert, G.W. (1989). Tests for unit roots: a Monte Carlo investigation. *JBES* 7(2), 147–159.
- Vidyamurthy, G. (2004). *Pairs Trading: Quantitative Methods and Analysis*. Wiley.
- tradermonty/claude-trading-skills, `pair-trade-screener` (MIT): correlation ≥ 0.70, EG + ADF, half-life, entry ±2 / exit 0.
- GPW archive (shares, indices, futures type 35): https://www.gpw.pl/archiwum-notowan · GPW Benchmark portfolios: https://gpwbenchmark.pl
