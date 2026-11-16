---
name: gpw-macro-regime
description: Slow-moving macro context for Polish equities (GPW, Warsaw Stock Exchange) over weeks to months. Reads the NBP reference-rate history (easing, on hold, tightening), the real rate vs HICP inflation and the NBP 2.5% target, the PLN regime from EURPLN and USDPLN trends, the PL-DE 10Y spread and 2s10s curve (BondSpot, ECB, Bundesbank) and WIG in USD, and combines them into a deterministic supportive, mixed or headwind composite with a 24-month history and changes since the last report. Sector tilts (banks, exporters, importers, utilities, developers) are shown as historical tendencies with plain-Polish reasons. No API key. Use when the user asks how rates, inflation or the zloty affect Polish stocks or which sectors the macro backdrop favours. PL triggers - "sytuacja makro", "stopy NBP a giełda", "czy słaby złoty pomaga eksporterom", "obniżki stóp a banki", "inflacja a akcje". EN triggers - "Polish macro regime", "NBP rate cycle and stocks". Not a timing tool, not for single stocks or US macro.
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
  derived_from: "tradermonty/claude-trading-skills macro-regime-detector (MIT)"
---

# GPW Macro Regime / Reżim makro dla polskich akcji

Describes the **macro backdrop** of the Polish stock market. It covers:

- the NBP rate cycle;
- inflation vs the NBP target, and the real interest rate;
- the zloty;
- the Polish risk premium (PL-DE 10Y spread);
- the yield curve.

These are combined into a composite: **supportive / mixed / headwind** for
equities. The skill also shows which sector groups such a backdrop has
historically helped or hurt.

It is **slow context for weeks to months, not a timing tool**. Run it about
once a week.

**Workflow position.**

- Fed by public macro data (NBP, BondSpot, ECB, Bundesbank, Eurostat) and the
  GPW index cache. It needs no other skill.
- Feeds `gpw-research-dossier` (the macro section), `gpw-daily-brief` and
  `gpw-exposure-coach`. Those skills read `macro.state` and `sector_tilts`
  as context; it is never a hard cap.

## When to use

- "Jak stopy NBP / inflacja / złoty wpływają teraz na giełdę?"
- "Czy otoczenie makro sprzyja bankom albo deweloperom?"
- "Czy słaby złoty pomaga eksporterom (CD Projekt, KGHM)?"
- "Rośnie spread polskich obligacji: co to znaczy dla akcji?"
- "What is the Polish macro regime?"
- "Is the NBP still cutting?"

Not for:

- timing an entry: use `gpw-market-regime` or `gpw-market-breadth`;
- single-stock valuation: use `gpw-valuation` or `gpw-research-dossier`;
- US or global macro.

## Language

Answer in the user's language and pass `--lang pl` (the default) or
`--lang en`. Explain jargon in plain Polish for beginners, for example:

- **realna stopa** = the NBP rate minus inflation;
- **spread** = the extra yield investors demand for Polish bonds over German
  ones.

## Workflow

### 1. Run the script

Never quote rates, yields or inflation from memory.

```bash
python3 scripts/gpw_macro_regime.py --lang pl --output-dir <dir>
```

| Flag | Meaning |
|---|---|
| `--as-of YYYY-MM-DD` | Historical analysis. Monthly data is used only once it had been published by that date. |
| `--offline` | Cache only (`~/.cache/gpw-trading-skills`). |
| `--cache-dir DIR` | A different cache location. |
| `--input-dir DIR` / `--previous-json PATH` | Where to find the previous report for "what changed" (default: `--output-dir`). |
| `--history-months N` | Length of the monthly history (default 24). |
| `--no-bondspot` | Skip the optional BondSpot source. The spread then comes from ECB monthly data, and there is no curve. |
| `--no-files` | Print the Markdown and write no files. |

- **Run time.** The first run makes ~45 polite requests (1-2 minutes). Later
  runs use the cache.
- **Exit code 1.** Fewer than 2 of the 4 core components were available.
  Report which sources failed; do not invent a regime.
- **Exit code 2.** Bad arguments.

### 2. Check freshness and failures first

Read these fields before anything else:

- `data_provenance.per_source`: each source has its own `newest_observation`,
  `stale` and `max_lag_days`;
- `macro.failed_sources`;
- `macro.confidence`.

If anything failed or is stale, say so in the first sentence, name the
source, and say that the confidence is lower.

### 3. Present (lead with the answer)

1. **Verdict.** One paragraph built from `macro.state.composite`, the score
   and the confidence, plus the `macro.summary` sentence. Say that it is
   context, not a buy or sell signal.
2. **Why.** Cover each dimension in plain language:
   - the rate cycle: state, rate, last change;
   - inflation vs the 2.5% ±1 pp target, and its trend;
   - the real rate;
   - the PLN regime: EURPLN and USDPLN vs their 200-day averages;
   - the spread and curve.
3. **Sectors.** List the `sector_tilts`. Always call them **historical
   tendencies** ("historycznie", "zwykle"), never predictions. Mention
   `market_check.agrees_with_tilt == false` honestly: "rynek ostatnio zachowuje
   się inaczej" (other factors dominate).
4. **What to watch.** Use the `watch[]` items. For MPC dates, point to
   `gpw-event-calendar`.
5. **What changed.** Use `changes_since_previous`, or say that this is the
   first report.
6. Optionally, give a 24-month history summary: when the composite turned.

### 4. Frame it correctly

- Macro moves slowly and explains backdrops, not next week's prices. A
  supportive regime with a falling market is possible, and vice versa.
- Never say "kup banki" or "sprzedaj deweloperów". A sector tilt is not a
  view on any single company.
- Inflation is HICP (Eurostat), close to but not identical with the GUS CPI
  that the NBP targets. Mention this if the user compares it with GUS
  headlines.

## Key rules (details in references/methodology.md)

- **Monetary state.**
  - `easing` / `tightening`: the last rate change was ≤ 120 days ago (about
    4 MPC meetings).
  - `hold_after_easing` / `hold_after_tightening`: the last change was
    longer ago.
- **Inflation band.** Above 3.5%, within 1.5-3.5%, or below 1.5%.
- **Inflation trend.** Change over 3 months of ≥ 0.3 pp.
- **Real rate.** NBP rate − HICP y/y. Above +2 pp is restrictive; below 0 is
  accommodative.
- **PLN, per pair.** The pair is more than 1% from its 200-fixing average and
  has moved the same way over 3 months. EUR weighs 2/3 and USD 1/3.
- **Spread / curve trend.** A change of ±15 bp over 3 months. An inverted
  curve subtracts 0.10.
- **Composite.** The weights are monetary 0.30, inflation 0.25, spread 0.25
  and PLN 0.20. A score of ≥ +0.25 is supportive and ≤ −0.25 is headwind.
- **Contract.**
  - `macro.state` has the fields `{monetary, monetary_simple, real_rate, pln,
    spread, curve, inflation, inflation_band, composite, regime_label}`.
  - `indicators{}` entries each carry `{value, unit, as_of, source, trend}`.
  - `sector_tilts{}` entries are `{tilt, score, drivers[], rationale,
    market_check}`.

## References

- `references/methodology.md`: rules, thresholds, evidence (Estrella &
  Mishkin 1998; Borio, Gambacorta & Hofmann 2017; Bernanke & Kuttner 2005;
  Dominguez & Tesar 2006; Ebner 2009) and limitations.
- `references/data_sources.md`: endpoints, format quirks, publication lags
  and sources not used (MF non-resident holdings, GUS DBW).

## Related skills

- `gpw-market-regime`: the tactical trend state of the indices.
- `gpw-market-breadth`: how broad the market move is.
- `gpw-sector-trend`: which sectors actually trend. Compare it with the macro
  tilts.
- `gpw-exposure-coach`: sets the risk budget.
- `gpw-event-calendar`: MPC meetings and macro release dates.
- `gpw-research-dossier`: uses this report as its macro section.
- `gpw-start`: for beginners who first need the basics.
