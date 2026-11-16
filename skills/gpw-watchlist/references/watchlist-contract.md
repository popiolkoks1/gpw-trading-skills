# gpw-watchlist state contract (schema 1.0)

This file is the **contract** for every skill that reads the watchlist
(gpw-event-calendar, gpw-research-dossier, gpw-daily-brief,
gpw-pre-trade-gate, gpw-sector-trend RRG). Readers open the JSON file
directly. They do not import gpw-watchlist code (CONVENTIONS rule 11).

- Location: `<state-dir>/watchlist.json`. The default state dir is
  `~/.gpw-trading-skills/`. The environment variable `GPW_WATCHLIST_DIR`
  overrides the default, and `--state-dir` overrides both.
- Only gpw-watchlist writes the file. Other skills read it and never modify it.
- Writes are atomic: temp file, then `fsync`, then `os.replace`. A reader
  never sees a half-written file.
- A lock file `watchlist.lock` prevents two writers at once. Readers ignore it.
- Every write that changes the content first copies the previous file to
  `watchlist_backups/watchlist_<timestamp>.json`. The 10 newest backups are kept.

## Stability rules

1. The fields listed below are stable within schema `1.x`. New fields may be
   added. Fields are never renamed or removed, and their meaning never changes.
2. A breaking change bumps the major version (`2.0`). gpw-watchlist migrates
   older files on load (`watchlist_state.migrate`). It refuses to write a
   file with a newer major version.
3. Readers must ignore unknown fields. They must treat a missing optional
   field as `null`, `[]` or `{}`.
4. Readers should check `schema_version`. If the major version is not `1`,
   warn and skip the file.

## Top level

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | `"1.0"` | Schema of this file. |
| `skill` | `"gpw-watchlist"` | Writer. |
| `created_at`, `updated_at` | ISO-8601 UTC | When the file was created and last changed. |
| `last_refresh` | object \| null | Set by the last `refresh`: `{as_of, at, checked, missing[]}`. `as_of` is the session of the quotes in `items[].market`. |
| `last_ingest` | object \| null | Set by the last `ingest`: `{at, reference_date, reports[{skill, mode, file, as_of}]}`. |
| `items` | array | One object per ticker (unique). Sorted by stage, then screener score, then ticker. |
| `removed` | array | Audit log of hard deletes: `{ticker, date, stage, reason}`. At most 200 entries. |

## Item

| Field | Type | Meaning |
|---|---|---|
| `ticker` | string | GPW short name as in the GPW archive, upper case (`PKOBP`, `CDPROJEKT`). Pattern `[A-Z0-9.-]{1,20}`. |
| `stage` | enum | One of `candidate`, `watch`, `ready`, `entered`, `dropped`. See the stage machine below. |
| `added` | `YYYY-MM-DD` | Date the item was first added. |
| `updated` | `YYYY-MM-DD` | Date of the last change, made by the user or by new producer evidence. Used for staleness. |
| `source` | string | Who added the item: `user` or a skill name (ROADMAP section 4 `source`). |
| `sources` | array | Producer evidence, deduplicated by `(skill, kind, date)`. At most the 50 newest, oldest first. Shape: `{skill, mode, kind, date, score, report_file, report_as_of}`. |
| `pivot` | number \| null | Unadjusted price level whose break confirms the setup. |
| `stop` | number \| null | Unadjusted price level that invalidates the setup. Always `stop < pivot`. |
| `target` | number \| null | Optional target. Always `target > pivot`. |
| `levels_source` | string \| null | `user` or the skill that set pivot/stop. A producer never overwrites levels set by the user. Levels are frozen once the item is `ready` or later. |
| `levels_date` | date \| null | Session the levels refer to. A dividend or split after this date raises `levels_pre_adjustment`. |
| `thesis_id` | string \| null | Linked gpw-trade-journal thesis id. The ACTIVE thesis when `entered`, otherwise the newest IDEA/READY thesis. |
| `thesis_status` | string \| null | Mirror of the linked thesis status (`IDEA`, `READY`, `ACTIVE`, `CLOSED`, `INVALIDATED`). |
| `tags` | string[] | Lower-case tags. They are either set by the user (`--tag`) or managed automatically by ingest (see below). |
| `tag_meta` | object | `{tag: {origin: "user"\|"auto", skill, mode, date}}`. |
| `notes` | array | `{date, text}`, oldest first. |
| `alerts` | array | Current alerts (see below). |
| `history` | array | Stage changes `{date, from, to, reason, by, thesis_id?}`. The first entry has `from: null`. `by` is `user`, `migration`, a producer skill or `gpw-trade-journal`. |
| `market` | object \| null | Snapshot from the last `refresh`: `{date, close, change_pct, atr14, turnover_k, turnover_ratio, high_52w, new_52w_high, pivot, stop, dist_to_pivot_pct, dist_to_stop_pct, pivot_state, crossed_pivot_today, below_stop}`. `pivot_state` is one of `below`, `near`, `buy_range`, `extended`, or `null` when there is no pivot. |
| `screener` | object \| null | Newest gpw-trend-screener view: `{date, score, band, state, rs, pct_vs_pivot, kind}`. |
| `upcoming_events` | array | Company events in the next 10 sessions from the last refresh: `{type, date, date_kind, title}`. |

### Alert

`{code, severity, date, origin, ...}`.

- `severity` is `high` (act or decide now), `medium` (attention soon) or `low` (information).
- `origin` is `refresh` or `ingest`. Refresh alerts are replaced on every
  refresh. Ingest alerts expire 10 sessions after their `date`.
- Refresh alerts carry `params{}`. Ingest alerts carry `skill`, `kind` and `score`.

| code | origin | severity |
|---|---|---|
| `stop_breached` | refresh | high: close ≤ stop. For `entered` items the journal plan's stop is used. |
| `near_stop` | refresh | medium: close − stop ≤ 0.5 × ATR(14) |
| `pivot_breakout` | refresh | The close crossed the pivot this session. high when the item is `ready` and turnover ≥ 1.4× the 50-session median, otherwise medium. |
| `in_buy_range` | refresh | 0 % < close vs pivot ≤ 5 %. medium for `ready`, low otherwise. |
| `extended` | refresh | close more than 5 % above the pivot. medium for `ready`, low otherwise. |
| `near_pivot` | refresh | close within 5 % below the pivot. medium for `ready`, low otherwise. |
| `new_52w_high` | refresh | low |
| `turnover_surge` | refresh | low: turnover ≥ 1.4× the 50-session median |
| `event_soon` | refresh | An event within 10 sessions. medium for `ready`/`entered` and the types periodic_report, agm, dividend_record, ex_dividend, dividend_payment; low otherwise. |
| `levels_pre_adjustment` | refresh | low |
| `no_price_data`, `no_recent_quotes` | refresh | medium |
| `ticker_renamed` | refresh | medium: quotes found under the new name (gpwlib.panel RENAMES), `params.new_ticker` |
| `setup_damaged` | ingest | high (gpw-trend-screener state `damaged`) |
| `earnings_strong_negative` | ingest | high |
| `quality_red_flags`, `insider_cluster_sell` | ingest | medium |
| `short_new`, `dividend_cut_risk` | ingest | low |

### Automatic tags

| tag | set by | risk tag |
|---|---|---|
| `vcp` | gpw-trend-screener watchlist | |
| `trend_template` | gpw-trend-screener Trend Template passers | |
| `setup_damaged` | gpw-trend-screener | ⚠️ |
| `quality_high` | gpw-quality-screener | |
| `quality_red_flags` | gpw-quality-screener | ⚠️ |
| `insider_cluster_buy` | gpw-ownership insiders | |
| `insider_cluster_sell` | gpw-ownership insiders | ⚠️ |
| `short_crowded` | gpw-ownership shorts | ⚠️ |
| `fund_accumulation` | gpw-ownership funds | |
| `earnings_strong_positive` | gpw-earnings-reaction | |
| `earnings_strong_negative` | gpw-earnings-reaction | ⚠️ |
| `dividend_cut_risk` | gpw-dividend-analyzer | ⚠️ |
| `pair_dislocation` | gpw-pair-trading (cheap leg) | |

An automatic tag is removed when a newer report of the same producer and
mode covers the ticker and no longer contains the fact. User tags are never
removed automatically.

## Stage machine

```
candidate ──> watch ──> ready ──> entered      (entered = journal thesis ACTIVE)
    any active stage ──> dropped ──> candidate | watch   (re-open by the user)
```

| from \ to | candidate | watch | ready | entered | dropped |
|---|---|---|---|---|---|
| candidate | – | ✔ (also auto) | ✔ | ✔ | ✔ |
| watch | ✔ | – | ✔ | ✔ | ✔ |
| ready | – | ✔ | – | ✔ | ✔ |
| entered | – | ✔ | – | – | ✔ |
| dropped | ✔ | ✔ | – | – | – |

Preconditions:

- `ready` needs `pivot` and `stop`, with `stop < pivot`. If no IDEA/READY
  journal thesis exists for the ticker, the move still happens and gives a
  warning.
- `entered` is set automatically when gpw-trade-journal has an ACTIVE
  thesis for the ticker. It happens once per thesis, read through
  `gpwlib.journal_io`. A manual `promote --to entered` also requires an
  ACTIVE thesis.
- `dropped` needs a reason.
- Automatic processes (ingest) may only add items as `candidate` or `watch`
  and move `candidate → watch`. Nothing automatic moves an item to `ready`.

## Minimal reader example

```python
import json, pathlib
p = pathlib.Path.home() / ".gpw-trading-skills" / "watchlist.json"
data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"items": []}
if str(data.get("schema_version", "1.0")).split(".")[0] == "1":
    followed = [i for i in data["items"] if i.get("stage") != "dropped"]
    ready = [i for i in followed if i["stage"] == "ready"]
    urgent = [(i["ticker"], a["code"]) for i in followed for a in i.get("alerts", []) if a.get("severity") == "high"]
```

## Report JSON (command output)

The commands `list`, `ingest`, `refresh` and `review` also write
`gpw_watchlist_<command>_YYYY-MM-DD_HHMMSS.json`. It follows the 1.1
envelope: `skill`, `version`, `lang`, `as_of`, `data_provenance`.

Other top-level keys:

- `command`, `answer` (the headline)
- `summary{by_stage{candidate, watch, ready, entered, dropped}, total, active, alerts_by_severity, stop_breached, near_stop, pivot_breakout, near_pivot, in_buy_range, extended, events_soon}`
- `items[]` (item objects as above)
- `alerts[]` (flat, with `ticker`, `stage`, `message`)
- `warnings[]`, `changes[]`
- `ingest`: `reports[]`, `stale_reports[]`, `rules`
- `review`: `suggestions[{ticker, stage, action, to, reason, details, command}]`

The watchlist does **not** emit `signals[]`. Stage changes are user
decisions, and gpw-signal-postmortem measures the producers that found the
idea.
