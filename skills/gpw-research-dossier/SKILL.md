---
name: gpw-research-dossier
description: One sourced research file per Warsaw Stock Exchange (GPW) company before a decision, then a structured bull vs bear debate. Collects the newest JSON of every GPW skill for the ticker - trend screener, quality, valuation, dividends, ownership (KNF shorts, insiders), event calendar, earnings reaction, pairs, risk lab - plus market regime, exposure limits, breadth, sector trend, macro tilt, the journal thesis and the watchlist item, into sections with as-of dates, stale flags and a list of citable fact ids; missing sections get the exact command or are filled with --run. The debate puts the bear case first, then the bull case, a risk manager (sizer command, kill criteria), a pre-mortem and verdict inputs; a validator rejects claims without existing fact ids; a gpw-trade-journal command is built, never run. PL triggers - "dossier spółki", "zbierz wszystko o", "argumenty za i przeciw", "debata bull bear", "research przed zakupem". EN triggers - "research dossier", "bull vs bear case". Never a buy or sell call.
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
  derived_from: "TradingAgents (Xiao et al. 2024) role structure - analysts, bull/bear researchers, risk team - adapted; no code reused"
---

# GPW Research Dossier / Dossier spółki i debata bull/bear

Answers: **what do all our analyses say about this one company, how fresh is
each piece, what is missing, and what is the strongest honest case against
and for it?** The script copies numbers; Claude argues, but every claim must
point at a fact id the script produced.

**Workflow position.** Fed by: the JSON reports of gpw-trend-screener,
gpw-quality-screener, gpw-valuation, gpw-dividend-analyzer, gpw-ownership,
gpw-event-calendar, gpw-earnings-reaction, gpw-pair-trading, gpw-risk-lab,
gpw-market-regime, gpw-exposure-coach, gpw-market-breadth, gpw-sector-trend,
gpw-macro-regime, plus the gpw-trade-journal and gpw-watchlist state. Feeds:
gpw-position-sizer (suggested command), gpw-pre-trade-gate, gpw-trade-journal
(`to-thesis` command), gpw-daily-brief. Step "before a trade" in
docs/ROADMAP_v1.2.md.

## When to use

- "Zbierz wszystko o KETY przed decyzją", "dossier PKO BP", "argumenty za i
  przeciw Orlenowi", "zrób debatę bull/bear dla CD Projektu".
- "Research dossier for KGHM", "bull vs bear case for PZU from our data".
- After a screener or the watchlist put a name on the table and before
  `gpw-position-sizer` / `gpw-pre-trade-gate`.

Not for: a single angle (use that skill directly: valuation, quality,
ownership ...), screening many stocks, US stocks, or "should I buy" - the
answer is evidence and risk numbers, the decision is the user's.

## Language

Answer in the user's language; pass `--lang pl` (default) or `--lang en`.

## Workflow

Run from this skill's directory. Keep one report folder for all skills
(`<dir>`), e.g. `~/gpw-reports`.

### 1. Build the dossier

```bash
python3 scripts/gpw_research_dossier.py build KETY --input-dir <dir> --output-dir <dir>
python3 scripts/gpw_research_dossier.py build PKOBP --input-dir <dir> --output-dir <dir> --run --offline
```

| Input | Flag | Default / note |
|---|---|---|
| Ticker | `TICKER` | GPW short name (KETY, PKOBP, PKNORLEN, CDPROJEKT) |
| Reports folder | `--input-dir` | `.`; extra files `--report FILE` (repeatable) |
| Fill gaps | `--run` | runs sibling skills for missing/stale sections, sequentially; `--only gpw-valuation,gpw-ownership` limits it |
| Cache only | `--offline` | passed to every sibling that supports it |
| Timeout | `--timeout` | 300 s per sibling skill |
| Staleness | `--max-age-sessions N` | overrides the per-section limits (3 / 5 / 10 sessions) |
| Sector | `--sector WIG-BANKI` | default: detected from reports or GPW Benchmark cache |
| Personal state | `--state-dir`, `--no-journal`, `--watchlist PATH` | journal `~/.gpw-trading-skills/journal`, watchlist `~/.gpw-trading-skills/watchlist.json` |
| Other | `--as-of`, `--lang`, `--json`, `--no-files`, `--skills-dir`, `--cache-dir` | |

`--run` can take minutes on a cold cache (ESPI scans): tell the user before
running it without `--offline`. Exit 1 = input/data error (message on
stderr). Output: `gpw_research_dossier_<T>_<stamp>.{json,md}`.

### 2. Present the dossier

1. **Stale sections first** (the Markdown starts with them, each with its
   refresh command).
2. One-line answer: sections found, number of facts, what is missing.
3. A short tour of the sections (one line each from `summary`), market
   context last. Quote numbers exactly as in `facts[]`.
4. Missing sections: say what is unknown because of them and show the
   command (or offer `--run`). Never fill a gap from memory.

### 3. The debate (Claude does the reasoning)

Read `references/debate_protocol.md` before the first debate.

```bash
python3 scripts/gpw_research_dossier.py debate-template KETY --input-dir <dir> --output-dir <dir>
# fill the JSON, then:
python3 scripts/gpw_research_dossier.py validate-debate <dir>/gpw_research_debate_KETY_....json
```

Fill the template in this order:

1. **Bear** - 3-5 points, the strongest honest case against. Each
   `{"claim", "fact_ids": [...], "strength"}`; the claim contains the number.
2. **Bull** - 3-5 points, steelmanned the same way.
3. **Risk manager** - keep the `gpw-position-sizer` command from
   `position_sizing` (risk % from gpw-exposure-coach), write >= 1 kill
   criterion (observable: a price, a reported metric, a date) and >= 1
   "what would change the view".
4. **Pre-mortem** - "a year later the position lost 30%: why?"
5. **Unknowns** - every missing/stale section, proxies, low coverage.
6. **Verdict inputs** - balance of evidence (`bear_heavier`, `balanced`,
   `bull_heavier`, `insufficient_evidence`), strongest fact ids per side,
   conditions to revisit. `not_a_recommendation` stays `true`.
7. Optional **thesis** block (one-sentence thesis, pillars, setup, plan) for
   the journal.

Run `validate-debate` and fix every error until it passes (exit 0). Then
present: bear section first, bull, risk manager, pre-mortem, unknowns,
verdict inputs, and the validation line (points, citations, sections cited).
Mention warnings (stale facts cited, one-section side).

### 4. Record it (only if the user wants)

```bash
python3 scripts/gpw_research_dossier.py to-thesis <debate.json> [--status READY]
```

Prints a ready `gpw-trade-journal add ...` command (pillars from the thesis
block or the bull points, kill criteria from the risk manager, risks from the
bear points, catalysts from the event calendar). The script never runs it;
show it and run it only after the user confirms.

## Key rules

- No claim without a fact id; no number that is not in `facts[]`. A gap is an
  unknown, not a guess.
- Bear first, steelman both sides, list unknowns, do the pre-mortem.
- Never "buy" / "sell" / price targets; verdict inputs describe evidence.
- Freshness: mention stale sections first; facts from them are flagged.
- Short totals are a lower bound (KNF publishes positions >= 0.5%).
- The dossier copies producer numbers; for context read the producer's own
  Markdown (`source_file`).

## Output contract (JSON)

`{skill, version, lang, kind: "dossier", ticker, as_of, sector{key, source},
sections{<skill>: {as_of, stale, sessions_behind, summary, key_fields{},
source_file}}, facts[{id, value, unit, section, as_of, stale}],
missing[{skill, reason, optional, command}], not_applicable[], refresh[],
stale_sections[], runs[{skill, command, status, exit_code, duration_s}],
debate{status, bull[], bear[], verdict_inputs}, next_steps{debate_template,
position_sizer{command, entry, stop, risk_pct}, pre_trade_gate, to_thesis},
data_provenance{as_of_min, as_of_max, stale_sections, ...}}`. Debate file:
`kind: "debate"` with `bear[]`, `bull[]`, `risk_manager{}`, `pre_mortem[]`,
`unknowns[]`, `verdict_inputs{}`, `thesis{}`.

## References

- `references/debate_protocol.md` - roles (TradingAgents adapted), order,
  anti-bias rules, pre-mortem, guardrail checks, evidence.
- `references/methodology.md` - sources per section, selection and absence
  rules, staleness limits, fact ids, sector resolution, `--run` order and
  timeouts, contract, limitations.

## Related skills

`gpw-trend-screener`, `gpw-quality-screener`, `gpw-valuation`,
`gpw-dividend-analyzer`, `gpw-ownership`, `gpw-event-calendar`,
`gpw-earnings-reaction`, `gpw-pair-trading`, `gpw-risk-lab` (sections);
`gpw-market-regime`, `gpw-exposure-coach`, `gpw-market-breadth`,
`gpw-sector-trend`, `gpw-macro-regime` (market context); `gpw-watchlist`
(where ideas wait); `gpw-position-sizer` -> `gpw-pre-trade-gate` ->
`gpw-trade-journal` (after the debate); `gpw-daily-brief`.
