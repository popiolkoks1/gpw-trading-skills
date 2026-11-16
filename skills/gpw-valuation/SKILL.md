---
name: gpw-valuation
description: Transparent valuation of one company listed on the Warsaw Stock Exchange (GPW). Leads with a reverse DCF - the FCF growth the current price implies over 10 years - then a 2-stage FCFF DCF with explicit, sourced assumptions, a WACC x terminal-growth sensitivity grid, bear/base/bull scenarios and comps against the same GPW sector index (P/E, P/BV, EV/EBITDA, EV/Sales; median and quartiles). Discount rate - CAPM in PLN with Damodaran country risk, 2-year weekly beta vs WIG, synthetic-rating cost of debt, 19% CIT. Banks and insurers use justified P/BV = (ROE - g)/(COE - g); Gordon DDM as a cross-check. Every assumption can be overridden. Use when the user asks what a GPW stock is worth or what its price implies. PL triggers - "ile jest warta spółka", "wycena DCF", "co zakłada cena", "wartość godziwa", "czy PKO jest drogie", "odwrócony DCF". EN triggers - "valuation of a Polish stock", "reverse DCF", "fair value range". Never a price target or buy/sell call; not for screening many stocks.
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
  derived_from: "Anthropic financial-services plugins dcf-model and comps-analysis (WACC build, sensitivity table, comps statistics); adapted to PLN and Polish country risk"
---

# GPW Valuation / Wycena spółki z GPW

Values **one** GPW company and answers two questions:

1. **What does today's price imply?** For most companies this is a reverse
   DCF: the FCF growth over 10 years that the price requires. For banks it is
   the implied sustainable ROE.
2. **What is a fair-value range** under explicit assumptions, each with its
   source?

The answer comes from the 2-stage DCF (or the bank model), a sensitivity
grid, three scenarios and comps.

It is **never a price target** and never a buy or sell call. It shows what
you would have to believe.

**Workflow position.**

- **Fed by:**
  - gpw.pl fundamentals (`gpwlib.fundamentals`);
  - the GPW quotes archive, for the price and the beta vs WIG;
  - Damodaran's country-risk and ratings tables;
  - the PL 10Y yield from the newest **gpw-macro-regime** JSON (fetched live
    from BondSpot / ECB when that report is missing or stale).
- **Feeds:** `gpw-research-dossier` (valuation section) and indirectly the
  bull/bear debate. The JSON follows the contract `{ticker,
  implied_growth_pct, wacc_pct, fair_value_range{low, base, high},
  sensitivity[][], comps{}}`.

## When to use

- "Ile jest warta spółka KETY?" / "Jaka jest wartość godziwa CD Projektu?"
- "Co zakłada obecna cena Orlenu?" / "Czy PKO BP jest drogie?"
- "Policz DCF dla … przy WACC 10% i wzroście 3%."
- "What does the price of PKO BP imply?" / "Reverse DCF for LPP."

Not for:

- screening many stocks: use `gpw-quality-screener` or
  `gpw-trend-screener`;
- dividend history and safety: use `gpw-dividend-analyzer`;
- timing an entry: use `gpw-market-regime`;
- how many shares to buy: use `gpw-position-sizer`;
- US stocks.

## Language

Answer in the user's language and pass `--lang pl` (default) or `--lang en`.
For beginners, explain these terms in plain Polish (the Markdown already
contains short explanations):

- **WACC**: the minimum return investors and lenders expect;
- **wartość rezydualna**: the value of everything after the forecast years;
- **odwrócony DCF**: what has to happen for the price to be fair.

## Workflow

### 1. Run the script

Run it from the skill directory. Never quote valuation numbers from memory.

```bash
python3 scripts/gpw_valuation.py KETY --lang pl --output-dir <dir> --input-dir <dir-with-macro-json>
python3 scripts/gpw_valuation.py PKOBP                    # bank -> justified P/BV model
python3 scripts/gpw_valuation.py ORLEN                    # read as PKNORLEN (archive name)
python3 scripts/gpw_valuation.py CDPROJEKT --revenue-growth 12 --margin 35 --wacc 10 --terminal-growth 3
python3 scripts/gpw_valuation.py KETY --net-debt 950 --peers GRUPAAZOTY,BORYSZEW --assumptions my.json
```

| Flag | Meaning |
|---|---|
| `TICKER` | GPW archive name (KETY, CDPROJEKT, PKOBP, PKNORLEN). Common names are matched (ORLEN → PKNORLEN). `--isin` covers unlisted cases. |
| `--revenue-growth`, `--margin`, `--capex-pct`, `--da-pct`, `--nwc-pct` | DCF drivers in %. Revenue growth applies to years 1–5. |
| `--wacc`, `--terminal-growth` | Discount rate and perpetual growth in %. For banks `--wacc` is the cost of equity. |
| `--rf`, `--erp`, `--crp`, `--beta`, `--cost-of-debt`, `--tax-rate` | Discount-rate inputs. `--rf` is the gross PL 10Y yield in %; `--erp` is the mature-market ERP. |
| `--net-debt`, `--fcf` | mln PLN. `--fcf` is the base FCF of the reverse DCF. **Pass `--net-debt` from the balance sheet when you can.** The default is a low-confidence proxy. |
| `--roe` | Bank model: sustainable ROE in %. |
| `--assumptions FILE` | JSON with the same keys (`revenue_growth`, `margin`, `wacc`, `net_debt`, `dps_pln`, `peers`, `scenarios{bear,base,bull}`, …). CLI flags win over the file. |
| `--rf-mode gross` | Keep the full PL 10Y yield. The default subtracts Poland's default spread (Damodaran). |
| `--model fcff\|bank` | Override the automatic choice. |
| `--peers A,B` / `--no-comps` / `--max-peers N` | Comps control. |
| `--macro-json` / `--input-dir` | gpw-macro-regime report with the PL 10Y yield. |
| `--as-of`, `--offline`, `--cache-dir`, `--no-files`, `--no-history-save` | Standard flags. |

- **Exit codes.**
  - 1 = data or input error (message on stderr). Examples: terminal growth ≥
    WACC, an unknown ticker, no PL 10Y yield available offline (pass
    `--rf`).
  - 2 = bad arguments.
- **Network.**
  - First run: about 4 requests plus 2 per peer.
  - Afterwards the cache is used: snapshots for 1 day, Damodaran tables for
    7 days.

### 2. Read the warnings first

Read the `warnings[]` and `data_provenance.freshness` before presenting
anything. Mention these first:

- **Annualised YTD data.** The figures are ×12/months, not TTM, so
  seasonality can distort them.
- **Net debt proxy.** It is low-confidence. Ask the user for the
  balance-sheet net debt if the result matters.
- **Comps.** Fewer than 5 peers, or a company with no sector index (then no
  comps at all).
- **Beta.** Bounded, or a low R².
- **Damodaran data.** Older than 12 months, or embedded fallback values.
- **Terminal value.** More than 75% of the DCF.

### 3. Present (lead with the answer)

1. **What the price implies.** One sentence from the JSON:
   - non-financial companies: "Cena 1 337 zł zakłada wzrost FCF ok. 10%
     rocznie przez 10 lat (WACC 9,8%, g 2,5%)", using
     `reverse_dcf.implied_growth_pct`;
   - banks: "Cena to 2,6× wartości księgowej; przy koszcie kapitału 13%
     zakłada trwałe ROE ok. 30% (obecne 19%)", using
     `reverse_dcf.implied_roe_pct`.

   Then help the user judge it. Compare it with the company's growth,
   margins and history; say whether it looks demanding or modest, and why.
   This is interpretation, not a verdict.
2. **Fair-value range.** Give `fair_value_range` low / base / high and where
   the price sits (`range_position`). Describe it neutrally, for example
   "cena jest powyżej górnej granicy o X%". Never say "undervalued, buy".
3. **Why.** Cover:
   - the WACC build (rf after the default spread, beta, ERP + CRP, Kd,
     weights);
   - the key DCF drivers and their sources (`assumptions{}`).
4. **Sensitivity.** Show the WACC × g table and point out how much the
   value moves for ±1 pp of WACC.
5. **Scenarios.** Bear, base and bull, with what changes in each.
6. **Comps.** Peer medians and IQR, where the company sits, and the implied
   range. State the peer count.
7. **DDM.** Only as a cross-check for dividend payers.
8. Offer to rerun with the user's own assumptions (growth, margin, net debt).

### 4. Frame it correctly

- A DCF converts assumptions into a number. The reverse DCF is more honest:
  it asks whether the expectations built into the price are plausible.
- Use the `scenarios` and the sensitivity grid to show uncertainty. Never
  present the base value as "the" value.
- Banks: ROE and the cost of equity drive everything. Mention the rate
  cycle (`gpw-macro-regime`) and dividend policy (KNF).

## Key rules (details in references/methodology.md)

- **Risk-free rate.** rf = PL 10Y − Poland default spread (0.72%, Moody's
  A2, Damodaran January 2026).
- **ERP.** Mature-market ERP 4.23% + CRP 1.10% = 5.33%.
- **Cost of debt.** Kd = PL 10Y + company spread from the synthetic rating
  (interest coverage).
- **Beta.** OLS on 2 years of weekly returns vs WIG, Blume-adjusted
  (2/3 raw + 1/3), bounded to 0.3–2.0; 1.0 if there are fewer than 52
  weeks.
- **WACC.** Market-value weights; net cash gets zero debt weight; 19% CIT.
- **Reverse DCF.** 10 years of constant FCF growth, then Gordon with g_T,
  solved by bisection on g ∈ [−50%, 100%]. `not_solvable` when FCF0 ≤ 0.
- **DCF.** 5 explicit years plus Gordon. Defaults: revenue growth from the
  history (clipped −10…+20%) or 5% (nominal-GDP proxy); g_T = 2.5% (NBP
  target), capped at rf. g_T ≥ WACC is an error.
- **Sensitivity.** WACC −2…+2 pp × g 0…4%.
- **Scenarios.** ±3 pp growth, margin ×0.85 / ×1.15, ±1 pp WACC, ±0.5 pp
  g_T. Banks: ROE −3 / +2 pp, ±1 pp COE.
- **Comps.** The sector-index peers. P/E and P/BV are the official gpw.pl
  figures; EV multiples are computed from Notoria (not for foreign issuers).
  Outliers are excluded. Warning when there are fewer than 5 peers.
- **Banks and insurers.** Justified P/BV = (ROE − g)/(COE − g). The implied
  ROE and implied g are the "reverse" answer.

## References

- `references/methodology.md`: the method in full, every constant and its
  source, YTD and TTM handling, and limitations. Sources include Damodaran,
  McKinsey *Valuation*, Gordon 1962, Blume 1971, Dimson 1979 and Mauboussin
  & Rappaport.

## Related skills

- `gpw-macro-regime`: supplies the PL 10Y yield and the rate-cycle context.
- `gpw-quality-screener`: quality and red flags before you value a company.
- `gpw-dividend-analyzer`: dividend history, which complements the DDM
  cross-check.
- `gpw-research-dossier`: collects this report as its valuation section.
- `gpw-trade-journal`: write the valuation assumptions into the thesis as
  pillars and kill criteria.
- `gpw-position-sizer` / `gpw-pre-trade-gate`: risk and discipline before
  any trade.
