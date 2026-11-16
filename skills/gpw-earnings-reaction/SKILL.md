---
name: gpw-earnings-reaction
description: Measures how the Polish stock market (GPW, Warsaw Stock Exchange) reacted to each company's periodic report - report dates and times detected from the ESPI list, session alignment, opening gap, 3-day abnormal return vs WIG (CAR), abnormal turnover, pre-report run-up, post-earnings drift over 20 and 60 sessions and a five-level label scaled by the stock's own volatility, plus a season overview by WIG20/mWIG40/sWIG80 and a drift study with t-stats. Modes - scan (current season), ticker (past events, next expected report date), portfolio (journal positions). Use when the user asks how the market took a company's results, which GPW stocks reacted worst or best this season, whether results keep moving the price, or whether a held stock had a bad report. PL triggers - "jak rynek przyjął wyniki", "reakcja na raport kwartalny", "sezon wyników", "kto rozczarował wynikami", "dryf po wynikach". EN triggers - "earnings reaction GPW", "post-earnings drift Poland". Not for forecasting results, valuation or US stocks.
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
  derived_from: "tradermonty/claude-trading-skills earnings-trade-analyzer and pead-screener (MIT), rebuilt for GPW as a price-based event study"
---

# GPW Earnings Reaction / Reakcje na raporty okresowe GPW

Answers three questions with an event study on official GPW data:

1. **How did the market react** to a company's periodic report? (gap, CAR[0,+2]
   vs WIG, abnormal turnover, a label from strong negative to strong positive)
2. **Is there post-earnings drift** (does the price keep moving the same way over
   the next 20 / 60 sessions)?
3. **What does it mean for a stock I hold or watch?** Mostly an exit / avoid
   warning after strong negative reactions (on the WSE drift was found mainly after
   negative surprises).

There is **no free analyst consensus** for Polish companies, so the "surprise" is
measured by the market reaction itself. The skill never invents a consensus.

**Workflow position.** Fed by: the ESPI list on gpw.pl (report dates), the GPW
archive (prices) and optionally the local fundamentals history
(`gpwlib.fundamentals`). Feeds: `gpw-daily-brief` and `gpw-research-dossier`
(`reactions[]`), `gpw-pre-trade-gate` (earnings-reaction check),
`gpw-signal-postmortem` (`signals[]`), `gpw-watchlist`. Run weekly during a
reporting season.

## When to use

- "Jak rynek przyjął wyniki PKO?", "Które spółki najbardziej rozczarowały w tym
  sezonie?", "Czy po słabym raporcie kurs dalej spada?", "Kiedy KGHM poda wyniki?"
- "How did the market take CD Projekt's results?", "Worst earnings reactions on GPW
  this season", "Is there post-earnings drift in Poland?"

Not for: forecasting earnings, valuation (`gpw-valuation`), report calendars for
the whole market (`gpw-event-calendar`), US stocks.

## Language

Answer in the user's language and pass `--lang pl` (default) or `--lang en`.

## Workflow

### 1. Choose the mode and run the script (never estimate reactions from memory)

Run from the skill directory:

```bash
# season overview: all reports in the last 100 days, strongest/weakest liquid names
python3 scripts/gpw_earnings_reaction.py scan --lang pl --output-dir <dir>

# one stock: events in the last 100 days, or its full ~2-year history
python3 scripts/gpw_earnings_reaction.py ticker PKOBP --lang pl --output-dir <dir>
python3 scripts/gpw_earnings_reaction.py ticker PKOBP --history --lang pl --output-dir <dir>

# held stocks from gpw-trade-journal (or an explicit list)
python3 scripts/gpw_earnings_reaction.py portfolio --lang pl --output-dir <dir>
python3 scripts/gpw_earnings_reaction.py portfolio --tickers PKOBP,KGHM,CDPROJEKT --lang en
```

| Input | Flag | Default / note |
|---|---|---|
| Mode | `scan` / `ticker X` / `portfolio` | required |
| ESPI lookback | `--lookback-days N` | 100 calendar days (~70 sessions); max 150 without `--history` |
| Full history | `--history` | back to the start of the price history (~2 years); **up to ~700 requests on a cold cache (~12 min)** - warn the user first |
| Fewer requests | `--sessions-only` | skip weekends/holidays (misses reports published on those days) |
| Liquidity filter for top lists | `--min-turnover-k` | 100 (thousand PLN median daily turnover) |
| Top list size | `--top` | 5 |
| Historical run | `--as-of YYYY-MM-DD` | no look-ahead: data ends that day |
| Cache only | `--offline` | missing ESPI days are reported |
| Journal / tickers | `--state-dir`, `--tickers` | journal read-only |
| Print only | `--no-files` | prints Markdown, writes nothing |

The ESPI list costs one request per day (two or three on peak days), cached for 30
days; prices come from the shared archive cache. The JSON states
`run.http_requests` and `run.runtime_s`. Exit code 1 = data or input problem
(message on stderr): report it, do not invent numbers.

### 2. Check freshness first

If `data_provenance.freshness.is_stale` is true, or `espi.missing_days` is not
empty, say so before anything else.

### 3. Present (lead with the answer)

- **scan**: one paragraph from `season` - number of reactions, share with
  positive CAR, mean/median CAR, strong negatives vs strong positives. Then the
  two top lists (weakest first - they matter more), the size-segment table and the
  drift table with its n. Mention `pending` reports (published after the last
  session) by count.
- **ticker**: how the stock usually reacts (`tickers.<T>.profile`: mean |CAR|,
  share positive, typical turnover), the event table, the next report date with
  its `date_kind` - say "estimated" or "statutory deadline", never present it as
  confirmed.
- **portfolio**: for each held stock the latest reaction; for a
  `strong_negative` one, the warning (review the thesis and the stop) first.
- Explain the jargon in plain Polish for beginners: *CAR* = how much better or
  worse the stock did than WIG over 3 sessions; *z* = how unusual the move is for
  this stock (2 = a move seen roughly once in 20 reports); *obrót ×* = turnover vs
  its normal day.

### 4. Frame it correctly

- Labels are statistics about past prices, not recommendations. Never say "buy"
  or "sell". A strong negative reaction is a reason to **review** a position.
- `provisional` = the 3-day window is not complete yet; `intraday` (⚑) = the
  report came out during the session, so part of day 0 precedes the news.
- Read the season average against `season.baseline` and drift against the neutral
  group (`vs_neutral_pp`): WIG is cap-weighted, so the average stock can lag it for
  weeks without any report.
- Drift averages with `low_n: true` (n < 30) are anecdotes, not evidence. Drift
  lives mostly in illiquid stocks where costs eat it (Chordia et al. 2009).
- `fundamental_surprise` is normally `null` with a reason: free history of
  reported profits is thin. Say so; do not fill it from memory.

## Output contract (JSON)

`reactions[]`: `{ticker, report_date, report_type, session_alignment, car_3d_pct,
abnormal_turnover, drift_20s_pct|null, label}` plus `reaction_date`,
`period`, `gap_pct`, `day0_return_pct`, `car_3d_mm_pct`, `beta`, `z`,
`run_up_car_pct`, `drift_60s_pct`, `close_location`, `vs_sma50_pct`,
`vs_sma200_pct`, `provisional`, `flags[]`, `fundamental_surprise{}`.
`season{}`: `key, label, events, share_positive_car, mean_car_3d_pct,
labels{}, by_segment{WIG20, MWIG40, SWIG80, other}`.
`signals[]`: `{ticker, date (reaction day), kind: earnings_strong_negative |
earnings_strong_positive, direction: avoid | exit | long, score}` - `exit` when
the stock is an open journal position. Also `drift_study{}`, `pending[]`,
`skipped[]`, `tickers{}`, `espi{}`, `run{}`, `data_provenance`.

## Key rules

- Report dates come only from ESPI (classes `periodic_q`, `periodic_h`,
  `periodic_y`, `preliminary_results`); corrections are ignored; PDF attachments
  are never downloaded.
- Preliminary results that precede the report are the information event (day 0).
- Prices are dividend/split adjusted (`gpwlib.panel`); the opening gap is measured
  against GPW's own reference price.
- Every threshold is in `references/methodology.md`.

## References

- `references/methodology.md` - session alignment, CAR and market model, labels and
  thresholds, drift study, evidence (Bernard & Thomas 1989, Chordia et al. 2009,
  Jagliński 2020), limitations. Load on first use or when asked "how/why".

## Related skills

`gpw-event-calendar` (upcoming report dates for the whole market),
`gpw-trade-journal` (record the reaction against the thesis; held positions),
`gpw-pre-trade-gate` (checks a recent earnings reaction before a buy),
`gpw-trend-screener` (a strong positive reaction on a leader), `gpw-portfolio-check`,
`gpw-signal-postmortem` (scores the `signals[]` later), `gpw-research-dossier`.
