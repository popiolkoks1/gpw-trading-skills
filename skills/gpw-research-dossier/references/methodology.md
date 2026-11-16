# Methodology: how the dossier is collected

Contents: 1. Sections and sources · 2. Selection rules · 3. Staleness ·
4. Facts and ids · 5. Sector resolution · 6. Filling gaps (`--run`) ·
7. Next-step commands · 8. Output contract · 9. Limitations

## 1. Sections and sources

The dossier computes nothing new. Every number is copied from the newest
relevant JSON report of another GPW skill (CONVENTIONS rule 11: skills talk
through files), found with `gpwlib.reports.iter_reports`.

| Section (fact prefix) | Producer | What is read for the ticker | Scope |
|---|---|---|---|
| `trend` | gpw-trend-screener | `result` (ticker mode) or the entry in `watchlist[]`, `candidates[]`, `others[]` | ticker |
| `quality` | gpw-quality-screener | `companies[]` entry | ticker |
| `valuation` | gpw-valuation | the report whose `ticker` matches | ticker |
| `dividend` | gpw-dividend-analyzer | `companies[]` entry + `upcoming[]` | ticker |
| `ownership` | gpw-ownership | ticker mode, or `shorts.by_issuer[]` / `insiders.by_ticker[]` / `funds.by_ticker[]` entries | ticker |
| `events` | gpw-event-calendar | `by_ticker[T]`, company `events[]`, high-importance market events in the horizon | ticker |
| `earnings` | gpw-earnings-reaction | `reactions[]` (latest), `tickers[T].profile`, `next_expected` | ticker |
| `pairs` | gpw-pair-trading | `pairs[]` with the ticker as a or b; `switch` | ticker, optional |
| `risk` | gpw-risk-lab | the ticker's position and its worst stress scenario | ticker, optional (only if held) |
| `regime` | gpw-market-regime | `regime.*` | market |
| `exposure` | gpw-exposure-coach | `posture`, `limits.*` | market |
| `breadth` | gpw-market-breadth | `composite`, `latest` | market |
| `sector` | gpw-sector-trend | overall score + the ticker's sector row (and its RRG quadrant when the report has one) | market |
| `macro` | gpw-macro-regime | `macro.state`, key indicators, the sector tilt whose `indices` contain the ticker's sector | market |
| `journal` | gpw-trade-journal state (`gpwlib.journal_io`) | newest live thesis for the ticker | personal, optional |
| `watchlist` | gpw-watchlist state (`~/.gpw-trading-skills/watchlist.json`) | the item for the ticker (stage, pivot, stop, sources, tags) | personal, optional |

## 2. Selection rules

- Reports are read newest first (`as_of`, then file mtime). For each skill
  the **newest report that covers the ticker** wins; a newer report about
  another company does not hide an older one about this company.
- With `--as-of D`, reports dated after D are ignored (no look-ahead).
- A report that crashes an extractor is recorded in `errors[]` and skipped;
  one bad file never stops the dossier.
- **Absence semantics.** The KNF short register lists every net short
  position of 0.5% or more (Regulation (EU) 236/2012 art. 5-6), so in
  `ownership` ticker mode or a full `shorts` scan an absent issuer is
  `short_total_pct = 0` with `short_in_register = false` (a lower bound, not
  "unknown"). Insider counts in ticker mode are 0 when no MAR art. 19 notice
  was found. Everywhere else a field the report lacks is simply absent.
- Values are converted only for units: ratios that the producer stores as
  fractions (ROE 0.37) become percent (`roe_pct` 37.0); values that are
  already percent (dividend `payout_ratio`) keep their value with a `_pct`
  suffix. Nothing is re-estimated.

## 3. Staleness

`stale = sessions_behind > limit`, counted with `gpwlib.provenance.sessions_behind`
from the section's `as_of` to the reference date (today or `--as-of`).

| Limit | Sections | Reason |
|---|---|---|
| 3 sessions | trend, ownership, regime, exposure, breadth, sector | daily skills; the cross-skill rule in docs/CONVENTIONS.md |
| 5 sessions | events, earnings, pairs, risk, macro | run weekly (ROADMAP §3 workflow); a week-old run is still the current one |
| 10 sessions | quality, valuation, dividend | inputs change with quarterly reports; two weeks allow for the price moving inside them without calling a fresh card stale |
| never | journal, watchlist | live state files |

A report without `as_of` is stale. `--max-age-sessions N` overrides every
limit. Stale sections are listed first in the Markdown and in `sections{}`
(CONVENTIONS rule 2), each with its refresh command in `refresh[]`; facts
from them carry `stale: true` and the validator warns when the debate cites
them.

## 4. Facts and ids

`facts[]` = `{id, value, unit, section, as_of, stale}`. The id is
`<prefix>.<field>` with the prefix from the table in §1; ids are stable
across runs and versions (renaming one is a breaking change). Units: `pct`,
`pp`, `pln`, `x`, `score`, `count`, `days`, `sessions`, `date`, `text`,
`bool`. `None` values are not emitted, so citing a fact means the number
exists.

## 5. Sector resolution

Order: `--sector`, then a report that states it (`gpw-valuation.sector_index`,
`gpw-dividend-analyzer.companies[].sector_index`, `gpw-risk-lab` position
`sector`), then the cached GPW Benchmark sector membership
(`gpwlib.constituents.sector_membership`, offline). Companies outside the 14
sector indices (e.g. KETY) get `null`, which is a valid answer: pairs are
then not applicable and the sector section shows the market-wide score only.

## 6. Filling gaps (`--run`)

For each missing or stale section the exact CLI of the sibling skill is
built (`dossier_runner.CLI`) and, with `--run`, executed from the sibling's
directory, found relative to this skill (`<skills-root>/<skill>/scripts/...`,
the same in the repository and in `~/.claude/skills`; `--skills-dir`
overrides it).

- Sequential, never parallel: producers share the GPW cache and polite rate
  limits (gpwlib.http >= 1 s per host).
- Order: breadth, sector trend, regime (reads breadth), exposure coach (reads
  all three), macro, trend, quality, valuation (reads the macro PL 10Y),
  dividend, ownership, events, earnings, pairs, risk lab.
- `--offline` is passed to every skill that has it (the exposure coach reads
  files only and has no such flag); `--as-of` and `--lang` likewise.
- Timeout per skill (`--timeout`, default 300 s: a cold ESPI scan in
  gpw-earnings-reaction or gpw-event-calendar can take minutes). Status per
  run: `ok`, `failed` (exit code and the last 600 characters of stderr),
  `timeout`, `not_installed`, `skipped`. The dossier is always rebuilt
  afterwards from what exists.
- Not runnable automatically: pairs without a sector, risk lab when the
  ticker is not an open journal position (listed under `not_applicable`).

## 7. Next-step commands

- Position sizer: entry/stop from the journal plan, else the watchlist
  pivot/stop, else the trend screener's pivot and structural stop; risk per
  trade from `gpw-exposure-coach` `limits.max_risk_per_trade_pct`, else the
  sizer's own 1% default. Without levels: `--atr-multiplier 2`. The account
  size stays `ACCOUNT_PLN` for the user to fill.
- Pre-trade gate on the sizer's JSON with the same `--input-dir`.
- `to-thesis`: the journal command from a validated debate (never executed).

## 8. Output contract

`{skill, version, lang, kind: "dossier", ticker, as_of, reference_date,
sector{key, source}, sections{<skill>: {skill, short, scope, as_of, stale,
sessions_behind, max_sessions_behind, summary, key_fields{}, source_file,
mode}}, facts[], missing[{skill, reason, optional, command}],
not_applicable[], refresh[], stale_sections[], runs[], debate{status, bull[],
bear[], verdict_inputs}, next_steps{}, errors[], warnings[], data_provenance{...,
as_of_min, as_of_max, stale_sections}}`.

The debate itself is a separate file (`kind: "debate"`,
`gpw_research_debate_<T>_<stamp>.json`) so the dossier stays a pure copy of
the producers' numbers.

## 9. Limitations

- The dossier is only as fresh and as complete as the producers' reports; it
  cannot see what they do not compute (e.g. no analyst consensus exists for
  free on GPW, F-score coverage grows over a year).
- Summaries are short deterministic sentences from the fields; read the
  producer's own Markdown for the full context (the `source_file`).
- Sector membership is today's GPW Benchmark portfolio (point-in-time caveat
  in `gpwlib.constituents`).
- The validator checks citations exist, not that the claim reads them
  correctly.
