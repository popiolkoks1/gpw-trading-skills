# GPW Account Planner — methodology

Contents: 1. What is compared · 2. Simulation · 3. Wrapper rules · 4. Waterfall ·
5. Default assumptions and why · 6. Limitations · 7. Legal sources

## 1. What is compared

Four ways of holding the same monthly savings invested in the same product
(e.g. an index ETF), plus a computed combination:

| Wrapper | Tax during | Tax at the end | Limit |
|---|---|---|---|
| Regular brokerage account | none if nothing is sold (accumulating ETF) | 19% of the gain (Belka) | none |
| IKE | none | 0% if age ≥ 60 and contributions in ≥ 5 calendar years; otherwise 19% of the gain | 3 × forecast average wage |
| IKZE | refund of the marginal PIT rate on contributions each year | 10% of the whole balance if age ≥ 65 and ≥ 5 years; otherwise the whole amount taxed at the PIT scale | 1.2 × wage (1.8 × self-employed) |
| OKI (from 2027) | yearly tax on the average value above the exemptions | none | none modelled (optional `--oki-limit`) |

## 2. Simulation (monthly)

* Starts in January of `--start-year`, runs `--years` full calendar years.
* Each month: contribution invested at the start of the month (one broker
  order per wrapper that receives money, commission with the broker's minimum
  fee via `gpwlib.brokers.commission`), then growth `(1 + r)^(1/12)`, then the
  product cost (TER) `(1 − TER)^(1/12)` taken from the balance.
* Limits are spread evenly: a wrapper takes at most `limit/12` per month. That
  is how a standing order works, fills the yearly limit exactly and keeps
  money invested evenly through the year.
* End: every pot is sold with one order, then the exit tax of its wrapper.
* The cost basis for Belka includes buy commissions (they are tax-deductible
  costs of acquiring the units, art. 30b ust. 2 ustawy o PIT); the sell commission
  reduces the proceeds.
* Real values = nominal / (1 + inflation)^years.

## 3. Wrapper rules as modelled

**IKE** (art. 34 ustawy o IKE i IKZE). Tax-free if at the end the saver is ≥ 60
and paid in during ≥ 5 calendar years, or more than half of the contributions
were paid more than 5 years before the withdrawal. Otherwise the withdrawal is
a "zwrot" taxed like a regular account (19% of the gain). The 55-year
early-retirement variant is mentioned but not modelled.

**IKZE.** Refund = contributions of year Y × marginal rate (`scale12` 12%,
`scale32` 32%, `linear` 19% — art. 30c ust. 2 allows the deduction for linear
taxpayers, `none` 0%). The refund for year Y arrives in April of Y+1 (PIT
filing) and is, by default, invested in the regular account (taxed with Belka
at the end); the refund of the last year arrives after the horizon and is added
as cash. With `--no-reinvest-refund` refunds are counted as cash without growth.
Withdrawal at ≥ 65 with ≥ 5 years: 10% flat on the whole balance (art. 30 ust. 1
pkt 10a). Early "zwrot": the whole amount is added to income and taxed at the
PIT scale — modelled as 12% up to 120 000 zł and 32% above (or 32% on everything
for `scale32`), because one large amount crosses the threshold; other income
and the tax-free amount are ignored.

**OKI** (Dz.U. 2026 poz. 1098). No PIT on income from OKI assets. Yearly tax =
rate × (average value − exemptions) via `gpwlib.tax.oki_tax`:
* average value = mean of the 12 month-end values of the calendar year
  (approximation of art. 23 — the act's exact averaging method should be
  checked with the provider);
* `--oki-qualified-pct` of the balance is "qualified" (exempt up to
  100 000 zł), the rest is "other" (taxed from the first zloty); savings assets
  are not modelled (0);
* the tax is paid from the account at year end (reduces compounding);
* rate: 0.85% in 2027 (art. 43); from 2028 the law says 19% of the NBP
  reference rate of 31 October of the previous year, minimum 0.1%, rounded down
  to 0.01 pp (art. 25). That rate is unknown in advance, so `--oki-rate-later`
  (default 0.85%, i.e. "as in 2027", equivalent to an NBP rate of ~4.5%) or
  `--nbp-rate X` (applied to every later year) is used;
* if `--start-year 2026`, OKI does not exist yet: 2026 money goes to the regular
  account in the OKI scenario.

**Why the qualified share matters:** a global ETF (MSCI World, S&P 500) is not
a qualified asset (it does not hold ≥ 70% Polish shares / PLN bonds), so on OKI
it pays the asset tax on the whole value every year. 0.85% a year of the whole
balance, compounded over decades, can cost more than a 19% tax on the gain paid
once at the end — the comparison table shows it explicitly.

**Regular account.** One sale at the end. Selling earlier (rebalancing, changing
funds) triggers the tax earlier and loses compounding on the tax amount;
distributing ETFs pay dividends taxed at 19% each year — use accumulating
funds where possible.

## 4. Waterfall (computed combination)

Every fill order of a subset of {IKE, IKZE, OKI} is simulated (16 orders), each
with the regular account as the final overflow: money goes into the first
wrapper up to its monthly share of the limit, the rest into the next, and so on.
The order with the highest net value in the **middle return scenario** is
reported and then evaluated in every scenario; if another scenario has a
different winner, the report says so. Near-ties (< 1 zł) are broken in favour of
an order starting with IKE (when IKE's conditions are not met it is taxed
exactly like a regular account, so it is never worse and keeps the option of a
tax-free withdrawal later), otherwise the simpler order.

If the saver's age at the end of the horizon is below 65, a second computation
("hold to 65") keeps all money invested without new contributions until 65,
when both IKE (60) and IKZE (65) are tax-free, and repeats the comparison and
the waterfall. This is the fair comparison for retirement accounts, which are
designed to be held to retirement.

This is a computed comparison for the user's inputs — never advice.

## 5. Default assumptions and why

| Parameter | Default | Justification |
|---|---|---|
| Return scenarios | 3% / 6% / 9% nominal | 3% ≈ Polish treasury bonds / deposits over the long run (NBP target inflation 2.5% + a small real yield); 6% ≈ a balanced mix; 9% ≈ long-run nominal equity return: global equities earned about 5% real a year in 1900–2023 (Dimson, Marsh, Staunton, *UBS Global Investment Returns Yearbook*), plus ~3% inflation, rounded up for Polish equities' higher risk premium. Not forecasts. |
| Inflation | 3.0% | NBP target 2.5% ± 1 pp (Rada Polityki Pieniężnej); Polish CPI has averaged above target over the last two decades, so the target + 0.5 pp is a neutral middle. |
| TER | 0.5% | typical yearly cost of a GPW-listed index ETF (Polish ETFs ~0.5–0.8%, large global UCITS ETFs 0.1–0.3%); check the KID of the actual fund. |
| Broker | none (0%) | brokers differ (0% to 0.39% min 5 zł); pass `--broker mbank` etc. to see the effect of minimum fees on small monthly orders. |
| Limit growth | 5%/yr after the known years | limits are a fixed multiple of the forecast average wage; nominal wage growth in Poland was 5–13% in 2020–2026 (GUS); 5% is a conservative long-run value. 2027 limits grew 6.5% vs 2026. |
| OKI rate after 2027 | 0.85% | the only rate known from the law; corresponds to an NBP reference rate of about 4.5%. Pass `--nbp-rate` for an explicit scenario. |
| IKZE refund | reinvested in the regular account | the refund is real money that, if spent, is lost to the plan; putting it into IKE (if there is room) would be even better — not modelled. |
| OKI qualified share | 0% | conservative: most beginners buy global ETFs; set it to the share of Polish equity/bond ETFs they would hold on OKI (gpw-etf-planner prints it). |

## 6. Limitations

* Constant returns: real markets swing; the order of returns matters for
  the size of the final balance but the ranking of wrappers is driven mostly by
  tax rules, not by the path.
* PIT scale, tax-free amount, thresholds and the PIT rate of the saver are
  assumed constant; the IKZE refund assumes enough tax due to absorb it.
* No account fees, FX conversion costs, spreads, dividends taxed along the way.
* OKI is a new law (in force from 2027): the averaging method, payment of the
  tax and any contribution or withdrawal rules should be verified with the
  provider; the model has no OKI contribution limit unless `--oki-limit`.
* One IKE, one IKZE (and one OKI) per person; married couples can each have
  their own — run the planner per person.

## 7. Legal sources

* Ustawa z 20.04.2004 o IKE i IKZE (Dz.U. 2026 poz. 91): art. 13 (IKE limit), art. 13a (IKZE limit), art. 34 (IKE withdrawal), art. 34a (IKZE withdrawal).
* Ustawa o PIT: art. 26 ust. 1 pkt 2b (IKZE deduction), art. 27 (scale 12/32%, 120 000 zł), art. 30 ust. 1 pkt 10a (IKZE 10%), art. 30a/30b (19% on capital gains), art. 30c (linear 19%).
* Ustawa z 3.07.2026 o osobistych kontach inwestycyjnych (Dz.U. 2026 poz. 1098): art. 22–23 (tax on average value), art. 25 (rate from NBP reference rate), art. 26 (exemptions), art. 43 (0.85% in 2027). Verified against the official text (see `gpwlib.tax` docstring).
* Limits 2026: obwieszczenia MRPiPS (Nov 2025); 2027: obwieszczenia MRPiPS z 27 i 30.10.2026 (M.P. 2026 poz. 1062, 1081).
