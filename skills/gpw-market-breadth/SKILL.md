---
name: gpw-market-breadth
description: Measures the health of the Polish stock market (GPW, Warsaw Stock Exchange) by computing market breadth from official daily quotes of every listed share - % of stocks above their 20/50/200-session averages, advancers vs decliners, McClellan oscillator, new highs vs new lows, WIG20/mWIG40/sWIG80 participation and WIG-vs-breadth divergence - and turns it into a 0-100 score with an indicative equity-exposure range. No API key. Use whenever the user asks how the Polish market / GPW / WIG / Warsaw stocks are doing, whether a rally or sell-off is broad, or how much to have in stocks now. PL triggers - "jak wygląda rynek", "kondycja GPW", "szerokość rynku", "czy hossa jest szeroka", "ile spółek rośnie", "czy to dobry moment na akcje", "WIG rośnie, ale...". EN triggers - "Polish market breadth", "GPW market health", "is the WIG rally broad". Not for single-stock analysis or US markets.
license: MIT
metadata:
  version: "1.0.0"
  derived_from: "tradermonty/claude-trading-skills market-breadth-analyzer (MIT)"
  languages: "pl,en"
---

# GPW Market Breadth / Szerokość rynku GPW

Breadth answers one question: **is the move carried by most stocks, or only by
a few heavyweights?** A WIG rally led by three banks while most shares fall is
fragile; a rally where 70% of stocks sit above their 200-session average is
healthy. This skill computes that from the official GPW quotes archive and
scores it 0-100 (100 = healthiest).

## Language

Answer in the user's language. Pass `--lang pl` for Polish (default) or
`--lang en` for English. Use the terminology pairs in
`references/glossary.md` (e.g. *szerokość rynku* = market breadth).

## Workflow

### 1. Run the script (always - never estimate breadth from memory)

```bash
python3 scripts/gpw_breadth.py --lang pl --output-dir <dir>
```

- First run downloads ~2 years of sessions from gpw.pl (one polite request
  per session, ~20-40 min); tell the user it is a one-off. Later runs only
  fetch new sessions (seconds). Cache: `~/.cache/gpw-trading-skills`.
- Useful flags: `--as-of YYYY-MM-DD` (analyse a past date),
  `--offline` (cache only), `--index WIG20` (divergence vs another index),
  `--min-turnover-k 100` (stricter liquidity filter).
- Exit code 1 = data problem (message on stderr). Report it plainly; do not
  substitute numbers from memory.

### 2. Check freshness first

Read `data_provenance.freshness` in the JSON. If `is_stale` is true, open the
answer with that fact (how many sessions old the data is). Data for today is
final only after ~18:00 Warsaw time.

### 3. Present the result

Lead with a 2-3 sentence plain-language verdict, then:

1. Score, zone and indicative exposure range (from `composite`).
2. The 2-3 numbers that drive the verdict: % above SMA200, fast vs slow line,
   advancers/decliners, new highs vs lows.
3. Any flags: `caution`, `pink_zone`, `early_warning` - explain each in one
   sentence.
4. Size participation (WIG20 vs mWIG40 vs sWIG80) - "is it only the big
   caps?".
5. One line on what would change the picture (e.g. "a return of the fast line
   above the slow line").

Link or attach the generated Markdown report. Keep the methodology section
for users who ask "how is this calculated?".

### 4. Frame it correctly

- The exposure range is a **context indicator for the whole equity part of a
  portfolio**, not an instruction. Never say "buy" or "sell".
- Long-term investors (ETF/IKE/IKZE, regular contributions) should hear that
  breadth matters mostly for *timing new risk and position sizes*, not for
  abandoning a long-term plan.
- If the user asks about a specific company, say breadth describes the market
  as a whole and offer `gpw-sector-trend` (sector view) or
  `gpw-position-sizer` (risk for a planned trade).

## Score zones

| Score | Zone (PL / EN) | Indicative exposure |
|---|---|---|
| 80-100 | Silna / Strong | 90-100% |
| 60-79 | Zdrowa / Healthy | 75-90% |
| 40-59 | Neutralna / Neutral | 60-75% |
| 20-39 | Słabnąca / Weakening | 40-60% |
| 0-19 | Krytyczna / Critical | 25-40% |

Components and weights: breadth level & trend 25%, fast vs slow line 20%,
advance/decline (McClellan) 15%, new highs vs lows 15%, size participation
10%, WIG-vs-breadth divergence 15%. Missing components are dropped and the
weights re-normalised (reported as `data_quality`).

## References (load when needed)

- `references/methodology.md` - every component, threshold and its
  justification, data limitations. Load on first use or when asked "why".
- `references/glossary.md` - PL/EN terminology and plain-language
  explanations for beginners.

## Related skills

- `gpw-sector-trend` - which sectors carry the market.
- `gpw-position-sizer` - convert the exposure context into a concrete
  position size for a planned trade.
- `gpw-trade-journal` - log the market context together with a trade idea.
