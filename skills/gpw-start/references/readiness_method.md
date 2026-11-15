# Readiness method and thresholds

Contents: 1. Order of steps · 2. Thresholds · 3. Equity range · 4. What the check cannot know · 5. Sources

## 1. Order of steps (the "financial pyramid")

The script places the user at one of four stages and lists the steps in this order:

| Stage | Condition | Why this comes first |
|---|---|---|
| 0 Budget | income − expenses ≤ 0 | Investing needs a surplus. Without one, any investment is eventually sold at a random moment to cover bills. |
| 1 Emergency fund | safe savings < N months of expenses | Without a reserve, a lost job or a car repair forces a sale, often during a crash (job losses and market falls tend to come together). |
| 2 Expensive debt | a consumer debt above 8% a year | Repaying it is a guaranteed, tax-free "return" equal to its rate. No sensible investment promises that. |
| 3 Invest | none of the above | Regular contributions to broad, cheap, diversified funds, ideally via a tax wrapper. |

This is the order taught in mainstream personal-finance education: the "piramida finansowa"
in KNF/UKNF and NBP consumer material, and the Bogleheads "prime directive" (live below your
means, emergency fund, pay high-interest debt, use tax-advantaged accounts, diversify, keep
costs low). The check never skips a stage, but it still lists the later steps so the user
can see the whole path.

## 2. Thresholds

| Constant | Value | Reasoning |
|---|---|---|
| Emergency fund, stable income | 3 months of expenses | The usual lower bound in consumer education. It covers typical shocks (repairs, short gaps between jobs). |
| Emergency fund, unstable income | 6 months | For B2B contracts, a single income, commission pay or dependants. Finding a new job can take a few months. |
| Expensive debt | > 8% a year | Long-run equity returns are about 5–8% nominal a year **before** the 19% Belka tax and with large swings. A debt costing more than 8% is a certain loss larger than an uncertain gain. Credit cards, instalment loans and "chwilówki" are usually far above this. Most mortgages are below it and are not counted unless the user enters them. |
| Short horizon | < 3 years: no shares | Broad equity markets have had falls that took several years to recover (e.g. WIG after the 2007 peak, world equities after 2000 and 2007). Money needed sooner belongs in deposits, treasury savings bonds or money-market funds. |
| Medium horizon | 3–7 years: equity share capped at 50% | There is some time to recover, but not enough to bet everything on it. |
| Long horizon | ≥ 7 years: no horizon cap | The cap then comes from the user's loss tolerance only. |
| Satellite | ≤ ~10% of investments in single stocks for learners | Most retail stock pickers underperform the index after costs. Keeping stock-picking small limits the cost of learning. |

## 3. Equity range

The user states the largest fall of the **whole portfolio** they could sit through without
selling (10, 20, 30… %). Broad equity markets fell about 50–60% in the worst crashes of the
last decades: MSCI World about −55% in USD from 2007 to 2009, the S&P 500 −57%, and WIG
about −65%. The script uses 55%.

```
equity_high = tolerance / 55% × 100%   (rounded down to 5 pp, capped by the horizon)
equity_low  = equity_high / 2          (rounded down to 5 pp)
```

Example: tolerance 30% → 30 / 0.55 ≈ 54% → 50% upper bound → range 25–50%.

It is a **range**, not a recommendation. Within it the user decides, based on things the
script does not know: job security, other assets (e.g. property), pension rights, experience.
The non-equity part is meant for safe assets: treasury savings bonds, deposits,
money-market funds and short-duration bond ETFs.

Behavioural reasoning: the main risk for beginners is not the fall itself but **selling after
the fall**. An equity share the user can actually hold through a crash beats a "better" one
they abandon. This is the core of every risk-profile questionnaire under MiFID II.

## 4. What the check cannot know

- Whether the user's job is at risk, their health, their family plans or other assets.
- Their real tolerance. People overestimate it before their first crash, so the report says so.
- Future returns. Nothing here is a forecast.

The report tells the user that a licensed investment adviser (doradca inwestycyjny, listed by
KNF) can do a full suitability assessment.

## 5. Sources

- KNF, public warning list: https://www.knf.gov.pl/dla_konsumenta/ostrzezenia_publiczne
- NBP educational portal: https://www.nbportal.pl
- Treasury savings bonds: https://www.obligacjeskarbowe.pl
- Bogleheads, "prime directive": https://www.bogleheads.org/wiki/Bogleheads%C2%AE_investment_philosophy
- Historical drawdowns: MSCI World Index factsheets (msci.com); GPW WIG history (gpw.pl, gpwbenchmark.pl)
- Contribution limits and OKI: `gpwlib/tax.py` (legal references in its docstring)
