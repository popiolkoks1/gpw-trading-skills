# gpw-macro-regime: methodology

Slow macro context for Polish equities. Horizon: weeks to months. The states
describe what happened over the last months. They are not forecasts and not a
timing signal. Every rule below is coded in `scripts/macro_engine.py`, with
the same constants.

Upstream model: tradermonty `macro-regime-detector` (MIT). It uses monthly
cross-asset ratios, a 1-2 year horizon and a composite of weighted components.
We keep the structure: components scored in [-1, 1], a weighted composite and
fail-closed behaviour when data is missing. The US ETF ratios are replaced by
Polish inputs, which are free and need no key: the NBP rate cycle, PLN, the
PL-DE spread, the curve and HICP.

## 1. Components and rules

### 1.1 Monetary cycle (NBP reference rate)

Input: the dates on which the reference rate changed, from the NBP archive
XML and the current XML.

| State | Rule |
|---|---|
| `easing` | last change was a cut, ≤ 120 days ago |
| `tightening` | last change was a hike, ≤ 120 days ago |
| `hold_after_easing` | last change was a cut, > 120 days ago |
| `hold_after_tightening` | last change was a hike, > 120 days ago |

- **Why 120 days.** The MPC (RPP) holds about 11 decision meetings a year
  (NBP, "Harmonogram posiedzeń RPP"), so 120 days is about four meetings.
  Three or more "no change" decisions in a row read as a pause.
- **Other fields.** We also report:
  - the direction of the last two moves; `turn` = they differ;
  - the current cycle: consecutive moves in one direction, with their total in bp;
  - the 12-month change.
- **Roadmap contract.** `monetary_simple` maps the state to `easing`,
  `neutral` or `tightening`; both `hold_*` states map to `neutral`.

### 1.2 Inflation (HICP y/y) and the real rate

- **Target.** The NBP inflation target is 2.5% CPI with a ±1 pp tolerance band
  (NBP, *Założenia polityki pieniężnej*). The band labels are:
  - `above`: more than 3.5%;
  - `below`: less than 1.5%;
  - `within`: otherwise.
- **Trend.** We compare the y/y rate with the value 3 months earlier:
  - `falling`: down by 0.3 pp or more;
  - `rising`: up by 0.3 pp or more;
  - `stable`: otherwise.

  Eurostat rounds to 0.1 pp, and the y/y rate usually moves by 0.1-0.2 pp in
  a month, so 0.3 pp in 3 months is a real change.
- **Measure.** We use HICP (Eurostat `prc_hicp_minr`), not national CPI
  (GUS). The two series differ slightly in weights and coverage. HICP is
  available without a key and in a stable JSON-stat format. The report says
  this.
- **Real rate** = reference rate − HICP y/y. This is an ex-post real rate. The
  stance labels are:
  - `restrictive`: above +2 pp. This is conservative: it is above most
    published estimates of the neutral real rate for European economies
    (Holston, Laubach & Williams 2017, *JIE* 108).
  - `accommodative`: below 0.
  - `moderate`: in between.

### 1.3 PLN regime

- **Pairs.** EURPLN and USDPLN, NBP table A mid fixings.
- **Signal per pair.**
  - `weak` PLN: the pair is more than 1% above its 200-fixing average **and**
    has risen over 3 months (91 days).
  - `strong` PLN: the pair is more than 1% below its average **and** has
    fallen over 3 months.
  - `neutral`: anything else.
- **Why 1%.** EURPLN's annualised volatility is usually 3-6% (3.4% over the
  year to 2026-11), which is roughly 1-1.7% a month. A 1% band therefore
  filters ordinary noise but still reacts within weeks. The 200-fixing
  average is the standard long-trend filter, as in the equity skills.
- **Combining the pairs.**
  - Signals are coded strong = −1, neutral = 0, weak = +1.
  - Weights are EUR 2/3 and USD 1/3, because the euro area takes over half
    of Polish exports (GUS foreign-trade data).
  - The regime is `weak` if the weighted score is ≥ 0.5 and `strong` if it
    is ≤ −0.5. If one pair is missing, the other is used alone.
- **Volatility flag.** 3-month annualised volatility above 1.5× the 1-year
  value means the variance has roughly doubled. This is reported as a
  stress warning.
- **CHFPLN** is shown only as context: CHF mortgages are a legal risk for
  banks.

### 1.4 Risk appetite: PL-DE 10Y spread and the 2s10s curve

- **Spread.** PL 10Y minus Bund 10Y, in bp.
  - Primary (daily): the BondSpot fixing yield of the fixed-rate bond nearest
    to 10 years, minus the Bundesbank Bund 10Y on the same day. The current
    value is compared with the value about 91 days earlier.
  - Fallback (monthly): the ECB convergence 10Y yield minus the Bund monthly
    average, last month vs 3 months earlier.
  - Trend: `narrowing` if the spread fell by 15 bp or more, `widening` if it
    rose by 15 bp or more, `stable` otherwise. 15 bp is about one month's
    typical move. Sovereign spreads of CEE countries price country risk and
    global risk appetite (Ebner 2009, *Emerging Markets Review* 10(2)), so a
    narrowing spread means foreign investors are more willing to hold Polish
    risk.
- **Curve.** The slope is 10Y − 2Y, from the BondSpot benchmarks nearest to 2
  and 10 years.
  - Shape: `inverted` below 0, `flat` 0-50 bp, `normal` 50-150 bp, `steep`
    above 150 bp.
  - Trend: `steepening` or `flattening` on a 3-month change of 15 bp or more.
  - An inverted curve has preceded most recessions (Estrella & Mishkin 1998,
    *Review of Economics and Statistics* 80(1)). It subtracts 0.10 from the
    composite.
- **Benchmark choice.**
  - Eligible bonds are fixed-coupon or zero-coupon only: OK, PS, DS, WS.
  - Excluded: WZ and NZ (floaters) and IZ (CPI-linked).
  - Maturity is the 25th of the month in the code (PS0729 → 2029-07-25).
  - The 2Y benchmark must be within ±1 year of 2 years, and the 10Y benchmark
    within ±2 years of 10 years.
- **10Y yield trend.** A 3-month change of ±25 bp in the outright yield is
  treated as material. It is used for the utilities tilt.

## 2. Composite

The composite is the weighted mean of the component scores that are
available:

| Component | Weight | Scores |
|---|---|---|
| monetary | 0.30 | easing +1, hold_after_easing +0.5, hold_after_tightening −0.5, tightening −1; ±0.25 for an accommodative / restrictive real rate (clamped to [−1, 1]) |
| inflation | 0.25 | by trend × band (table below) |
| spread | 0.25 | narrowing +1, stable 0, widening −1 |
| pln | 0.20 | strong +1, neutral 0, weak −1 |

Inflation scores:

| Trend | above | within | below |
|---|---|---|---|
| falling | +0.5 | +1 | +0.5 |
| stable | −0.5 | +0.5 | 0 |
| rising | −1 | −0.5 | 0 |

- **Why these weights.**
  - Monetary policy leads, because it drives the discount rate and credit.
    Equity prices react to rate surprises (Bernanke & Kuttner 2005, *Journal
    of Finance* 60(3)).
  - Inflation and the risk premium come next.
  - PLN comes last: it matters to foreign investors' returns in EUR/USD and to
    sector margins, but it is noisier.
- **Labels.** `supportive` if the score is ≥ +0.25, `headwind` if it is ≤ −0.25,
  `mixed` otherwise. A single component at full strength (weight 0.20-0.30)
  can tip the label on its own only when nothing offsets it.
- **Weak PLN counts against equities.** A weak zloty usually comes with
  emerging-market outflows and lowers foreign investors' returns. It still
  helps exporters (see the tilts).
- **Confidence.**
  - `high`: no missing core component and no stale core source.
  - `medium`: one problem.
  - `low`: two or more problems.
- **Fail-closed.** With fewer than 2 of the 4 core components available, the
  script exits with code 1 and writes no report (the upstream behaviour).

## 3. Sector tilts (historical tendencies)

Each tilt is the sum of its driver contributions, clamped to [−1, 1]. The
labels are:

- `positive` ≥ 0.5;
- `slightly_positive` ≥ 0.25;
- `neutral`;
- `slightly_negative` ≤ −0.25;
- `negative` ≤ −0.5.

Every driver is listed in the JSON. In a driver, an active cycle counts fully,
and a pause (`hold_*`) counts as ±0.25, because the level of rates still
matters but the direction no longer adds to it.

| Group | Indices | Drivers | Evidence |
|---|---|---|---|
| Banks | WIG-BANKI | easing −1 / tightening +1 | Lower rates compress bank margins (Borio, Gambacorta & Hofmann 2017, *International Finance* 20(1)). Polish banks are asset-sensitive: most PLN loans are floating-rate (WIBOR, being replaced by WIRON) or fixed for only 5 years, so net interest income follows the reference rate (NBP, *Raport o stabilności systemu finansowego*, NII sensitivity sections). Other drivers: the bank levy, CHF litigation, risk costs. |
| Exporters | WIG-INFO, WIG-GRY, WIG-GORNIC, WIG-CHEMIA | PLN weak +1 / strong −1 | Revenue in EUR/USD and costs in PLN. Game studios sell mostly abroad; copper and silver are priced in USD. Firm-level FX exposure is documented by Dominguez & Tesar (2006), *Journal of International Economics* 68(1). |
| Importers / retail | WIG-ODZIEZ, WIG-SPOZYW | PLN strong +0.5 / weak −0.5; inflation falling +0.5 / rising −0.5 | Purchases in USD (apparel sourced in Asia); real wages drive consumption. |
| Utilities | WIG-ENERG | 10Y yield falling +0.25 / rising −0.25 (±25 bp in 3M) | Bond-like (heavy capex and debt). Capped at "slightly", because regulation (URE tariffs, windfall levies, coal transition) usually dominates. |
| Developers | WIG-NRCHOM | easing +1 / tightening −1; negative real rate +0.25 | Mortgage affordability; savings move into housing when deposits lose real value. Government housing programmes are a large exogenous factor. |

- **Market check.** The report shows the average 3-month return of the
  group's indices vs WIG, and whether it agrees with the tilt. The check never
  changes the tilt: a disagreement means the market is driven by something
  else, such as earnings, regulation or flows.
- **What the tilts are not.** They are not a recommendation for any company.
  A group-level tendency says nothing about one stock.

## 4. History and "what changed"

- **History.** The composite is computed at each of the last 24 month-ends
  (`--history-months`), point in time. HICP is used only once normally
  published (month M from day 46 after the 1st of M). ECB monthly yields are
  used from day 40. FX and the NBP rate are taken as of that date.
  - The monthly history uses the monthly spread (ECB − Bund) and no curve,
    because daily BondSpot history would need ~500 page requests.
  - The last row is replaced by the current analysis, so it matches the
    report.
  - The values are today's vintage: Eurostat revisions are not undone.
- **What changed.** `gpwlib.reports.find_report` locates the previous
  `gpw-macro-regime` JSON in `--input-dir`, dated on or before the analysis
  date. The state fields are diffed and the score change is reported.

## 5. Freshness per source

Each source has its own rhythm, so staleness is judged per source. The
measure is calendar days from the newest observation (for monthly data, the
1st of the month) to the analysis date:

| Source | Frequency | Stale after |
|---|---|---|
| NBP rate files | event (MPC) | 35 days since retrieval (one MPC cycle) |
| NBP FX | daily | 5 days |
| Bundesbank Bund 10Y | daily | 5 days |
| BondSpot fixing | daily | 5 days |
| ECB PL 10Y | monthly | 75 days |
| Eurostat HICP | monthly | 80 days |
| GPW indices | daily | 5 days |

A stale or failed core source lowers the confidence, and the report names it
first.

## 6. Limitations

- The model is deliberately simple and deterministic. It does not forecast the
  MPC, CPI or PLN.
- HICP is not the CPI measure that NBP targets.
- The daily PL 10Y comes from a single benchmark bond, which rolls as bonds
  age, so a change of benchmark can shift the spread by a few bp.
- WIG in USD uses the NBP fixing (~11:45) against the GPW close (17:00), so it
  is approximate.
- Non-resident holdings of Polish T-bonds (Ministry of Finance, monthly) are
  not included because no stable machine-readable URL exists. This is future
  work (see data_sources.md).
- Sector groups use the current GPW sector indices, and the tilts are group
  averages.
