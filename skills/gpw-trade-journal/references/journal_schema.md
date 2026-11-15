# Journal schema and lifecycle (v1.0)

Contents: 1. Storage · 2. Thesis fields · 3. Lifecycle · 4. Fills and FIFO ·
5. Outcome · 6. Design rationale

## 1. Storage

`<state-dir>/theses/<id>.json`, one file per thesis, UTF-8, written atomically (a
temp file plus `os.replace`). The default state dir is `~/.gpw-trading-skills/journal`,
overridden by `$GPW_JOURNAL_DIR` or `--state-dir`. You can keep the folder under git
for an audit trail. `gpw-trade-review` reads the same files.

The ID format is `th_<ticker>_<yyyymmdd>_<4 hex>`, e.g. `th_pkobp_20260901_96a0`.

## 2. Thesis fields

| Field | Type | Rule |
|---|---|---|
| `schema_version` | "1.0" | |
| `id`, `ticker` | str | ticker is the upper-case GPW short name |
| `thesis` | str | non-empty |
| `pillars` | [str] | 1–5 |
| `kill_criteria` | [str] | ≥ 1, required: what would prove the thesis wrong |
| `risks`, `catalysts` | [str] | optional |
| `setup` | enum | breakout, trend, pullback, value, dividend, growth, earnings, mean_reversion, other |
| `horizon` | enum | swing, position, long_term |
| `plan` | obj | `entry`, `stop`, `target`, `shares_planned`, `risk_pct`, `initial_stop` (set automatically when the stop is first moved) |
| `account_type` | enum | regular, ike, ikze, oki |
| `conviction` | enum | H, M, L |
| `review_date` | date | default: creation + 30 days |
| `status`, `status_history` | | see §3 |
| `updates` | [obj] | `date`, `note`, optional `pillar`, `conviction_from/conviction`, `stop_from/stop`, `target_from/target`, `review_date` |
| `fills` | [obj] | `date`, `side` buy/sell, `shares` (int > 0), `price` > 0, `fee` ≥ 0 |
| `source` | obj | e.g. `{skill: gpw-position-sizer, file: ...}` |
| `outcome` | obj/null | see §5 |

Plan geometry: while the thesis is IDEA or READY, `stop < entry < target`. Once it is
ACTIVE, the stop may trail above the entry.

## 3. Lifecycle (forward-only)

```
IDEA ──▶ READY ──▶ ACTIVE ──▶ CLOSED
  └────────┴──────────┴─(no position)─▶ INVALIDATED
```

- ACTIVE is entered automatically by the first buy fill.
- CLOSED is reached only through `close`, so every closed thesis has an outcome and process answers.
- INVALIDATED means the kill criterion fired **before** entry, or after the position was already sold. With an open position, close it with `--exit-reason kill_criterion` instead.
- Terminal states are frozen. A new idea in the same stock gets a new thesis, which preserves an honest history.

## 4. Fills and FIFO

Sells are matched against buys **first-in-first-out**. Each sell carries its share of
the matched lot's buy commission. FIFO is mandatory for securities held on a brokerage
account under Polish tax law (art. 24 ust. 10 ustawy o PIT), so the journal matches
what the broker's PIT-8C will show. Short selling is rejected because the journal is
long-only.

## 5. Outcome (on close)

| Field | Meaning |
|---|---|
| `gross_pnl` | Σ (sell − buy price) × shares over FIFO matches |
| `fees` | all commissions of matched shares |
| `net_pnl` | gross − fees (before tax) |
| `return_pct` | net / FIFO cost basis |
| `r_multiple` | net / (shares × (avg entry − **initial** stop)). This measures the result in units of the risk originally accepted. Trailing the stop must not inflate R. |
| `holding_days` | first buy → last sell, calendar days |
| `benchmark` | WIG close-to-close from the session before the first buy to the last sell (GPW archive) |
| `excess_return_pct` | return − WIG return (percentage points) |
| `process` | `entry` / `stop` / `size`: did they follow the plan (yes/no) |
| `process_score` | share of "yes" answers (0–1); `followed_plan` = all yes |
| `lessons` | free text |
| `fifo_matches` | per-lot detail; input for the PIT-38 estimate |

## 6. Design rationale

- **Kill criteria are mandatory.** Writing down in advance what would prove you wrong is the best protection against confirmation bias and "hope holding". This follows the thesis-tracker pattern ("if nothing could disprove it, it's not a thesis").
- **Process is scored separately from outcome.** It allows the review to separate skill from luck (see `gpw-trade-review`).
- **Benchmark vs WIG.** A gain smaller than the broad market's means a passive ETF would have done better.
- **JSON with validation in code, standard library only.** It runs on any Python 3.9+ with no dependencies, and invalid states cannot be saved.
