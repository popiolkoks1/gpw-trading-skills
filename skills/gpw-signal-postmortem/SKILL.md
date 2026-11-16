---
name: gpw-signal-postmortem
description: Checks with real forward prices whether the signals of the GPW (Warsaw Stock Exchange) skills worked. Records signals from gpw-trend-screener, gpw-market-regime, gpw-market-breadth, gpw-sector-trend and any report with a signals array into a deduplicated ledger, then measures returns over 5, 20 and 60 sessions against WIG and an equal-weighted market, the best and worst move (MFE/MAE), whether the stop or the pivot was hit first, and outcome classes - true positive, false positive, regime mismatch, missed opportunity, pending. Buckets by priority, execution state, RS, regime, sector and size, with hit rate, Wilson interval and t-stats, plus evidence-gated feedback that never edits other skills. For advanced users. PL triggers - "czy sygnały działały", "skuteczność skanera", "post-mortem sygnałów", "trafność VCP", "czy priorytet ma znaczenie". EN triggers - "did my signals work", "signal post-mortem", "screener hit rate". Not for closed-trade P&L (gpw-trade-review) or testing new rules (gpw-backtester).
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
  derived_from: "tradermonty/claude-trading-skills signal-postmortem (MIT)"
---

# GPW Signal Post-mortem / Post-mortem sygnałów GPW

Closes the loop **signal → outcome → lesson**. Every screener watchlist, regime
change, breadth or sector call is a dated claim. This skill records those claims and
later measures, on real adjusted GPW prices, whether they beat the market,
then says - only when the evidence is strong enough - what the other skills
could learn.

Improves on the upstream `signal-postmortem`: it judges **excess return vs WIG**
(and vs an equal-weighted market), not the raw sign of the return, and uses
several horizons, MFE/MAE, stop-vs-pivot, Wilson intervals and date-clustered
t-statistics, a minimum n and a strict evidence gate.

**Workflow position:** fed by the JSON reports of `gpw-trend-screener`,
`gpw-market-regime`, `gpw-market-breadth`, `gpw-sector-trend` and any skill
that emits the generic `signals[]` array (planned: `gpw-ownership`,
`gpw-earnings-reaction`, `gpw-quality-screener`). It feeds
`gpw_signal_feedback_*.json` to `gpw-trend-screener` and `gpw-exposure-coach`
(read-only suggestions) and hypotheses to `gpw-backtester`. Cadence: record
daily or weekly, evaluate monthly.

## When to use

- "Czy sygnały VCP ze skanera w ogóle działały?" / "Did my screener picks beat WIG?"
- "Czy wyższy priorytet na liście obserwacyjnej daje lepsze wyniki?"
- "Czy sygnały w korekcie rynku są gorsze?" (regime filter)
- "Co przegapiłem - spółki odrzucone przez skaner, które potem urosły?"
- Monthly review of signal quality, before changing any threshold.

## Language

Answer in the user's language and pass `--lang pl` (default) or `--lang en`.

## Workflow

Run from the skill directory. The ledger lives in `~/.gpw-trading-skills/signals/ledger.json`
(`--state-dir` to change).

### 1. Record signals (idempotent, safe to repeat)

```bash
python3 scripts/gpw_signal_postmortem.py record --input-dir <folder with skill reports>
python3 scripts/gpw_signal_postmortem.py record path/to/gpw_trend_screener_2026-11-13_170512.json
python3 scripts/gpw_signal_postmortem.py backfill --list old_reports.txt   # one path per line, oldest first
python3 scripts/gpw_signal_postmortem.py list                              # what is in the ledger
```

`record --input-dir` ingests every supported report in the folder; `--latest`
takes only the newest per skill (gpwlib.reports). Duplicates by
`(source, ticker, date, kind)` are skipped. Always record a
`gpw-market-regime` report too: its history becomes the regime timeline that
separates `regime_mismatch` from `false_positive`.

### 2. Evaluate

```bash
python3 scripts/gpw_signal_postmortem.py evaluate --offline --input-dir <reports> --lang pl
```

| Option | Default | Meaning |
|---|---|---|
| `--horizons` | `5,20,60` | Forward windows in GPW sessions |
| `--primary-horizon` | `20` | Horizon that sets the outcome class |
| `--entry` | `close` | `close` of the signal session or `next_open` |
| `--hit-benchmark` | `wig` | `wig` or `ew` (equal-weighted liquid universe) |
| `--min-n` | `20` | Minimum signals per bucket for a conclusion |
| `--months N` / `--since` / `--until` | all | Time filter |
| `--source`, `--kind` | all | Filter (repeatable) |
| `--input-dir` / `--regime-json` | `.` | Where to find a gpw-market-regime report for the regime timeline |
| `--as-of`, `--offline`, `--cache-dir`, `--sessions` | | Market data (GPW archive cache) |
| `--write-feedback` | off | Also write `gpw_signal_feedback_*.json` |
| `--no-files`, `--output-dir` | | Output control |

Writes `gpw_signal_postmortem_YYYY-MM-DD_HHMMSS.{json,md}` and prints the Markdown.

### 3. Feedback file (optional)

```bash
python3 scripts/gpw_signal_postmortem.py feedback --input-dir <folder with the post-mortem JSON>
```

Writes `gpw_signal_feedback_*.json` (refuses demo reports).

### 4. Demo (only when there is no matured history yet)

```bash
python3 scripts/gpw_signal_postmortem.py evaluate --demo --offline --input-dir <reports>
```

Synthesises signals from a simple deterministic rule (first close above the
52-week high, liquid shares) on real cached prices, to show how the
measurement works. **Always tell the user that demo signals are synthetic and
are NOT the track record of any skill.** Never quote demo numbers as "your
signals".

### How to present results

1. If `data_provenance.freshness.is_stale`, say so first. If `demo` is true, say
   "DEMO - synthetic signals" before any number.
2. **Lead with the answer** from `headline[]`, e.g. "Twoje sygnały VCP z ostatnich
   6 miesięcy: 58% trafień, średnio +1,9 pp vs WIG po 20 sesjach, n=41". Always
   give n, the horizon and the benchmark.
3. If most signals are `pending`, say how many and when the first matures
   (`next_maturity`) - do not judge pending signals.
4. Show the buckets that matter (priority, execution state, regime, RS) and
   mark `insufficient` ones as "too few to conclude". Quote the Wilson
   interval, not just the hit rate.
5. Feedback: quote `feedback.summary` and only suggestions with verdict
   `supported` / `contradicted` as findings; `suggestive` as "worth watching";
   everything else as "no evidence". State that nothing was changed.
6. Caveats: small n, overlapping windows (use `t_stat_by_date`), regime
   concentration (`regime_mix`), costs not included, current index membership.

## Output contract (JSON)

Envelope `skill`, `version`, `lang`, `as_of`, `data_provenance`, plus
`signals_total`, `signals_evaluated`, `pending`, `next_maturity`, `outcomes{}`,
`regime_mix{}`, `headline[]`, `buckets[]`
(`{dimension, bucket, direction_group, horizon, n, hits, hit_rate, hit_rate_ci95,
mean_excess_pct, median_excess_pct, mean_edge_pp, t_stat, n_dates, t_stat_by_date,
mean_excess_ew_pp, mean_mfe_pct, mean_mae_pct, status}`), `feedback{}`
(`auto_apply: false`, `summary{pl,en}`, `suggestions[]` with `verdict`,
`target_skill`, `parameter`, `statement{pl,en}`, `evidence{}`, `by_skill{}`),
`results[]` (one per signal with per-horizon returns, outcome, levels).

The generic `signals[]` contract other skills can emit is in
`references/signal-contract.md`; methodology and thresholds in
`references/methodology.md`.

## Key rules

- Scripts compute, Claude interprets: never estimate a hit rate or return yourself.
- Pending signals are never judged; buckets below `--min-n` give no conclusion.
- Feedback is advice only. Never edit another skill's settings or code because
  of it; suggest testing a change with `gpw-backtester` first.
- Demo output is synthetic - label it every time.
- Never say "buy X" / "sell X"; this is a measurement of past signals.

## Related skills

- `gpw-trend-screener`, `gpw-market-regime`, `gpw-market-breadth`, `gpw-sector-trend` - signal sources.
- `gpw-exposure-coach` - can use the regime-filter evidence.
- `gpw-backtester` - test a rule change before adopting it.
- `gpw-trade-review` - results of trades actually taken (P&L, PIT-38), not signals.
- `gpw-trade-journal` - the trades themselves.
