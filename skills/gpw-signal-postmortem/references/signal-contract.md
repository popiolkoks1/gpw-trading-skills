# Generic `signals[]` contract (v1)

Any GPW skill can make its output measurable by `gpw-signal-postmortem`.
It adds one top-level array to its JSON report. No import and no shared code
are needed: skills talk through files (CONVENTIONS rule 11).

```json
{
  "skill": "gpw-ownership",
  "version": "1.0.0",
  "as_of": "2026-11-13",
  "signals": [
    {"ticker": "PKOBP", "date": "2026-11-13", "kind": "insider_cluster_buy", "direction": "long",
     "score": 72, "price": 71.30, "invalidation": 66.10, "tags": {"insiders": 3}},
    {"ticker": "CDPROJEKT", "date": "2026-11-12", "kind": "crowded_short", "direction": "avoid"}
  ],
  "data_provenance": {"as_of": "2026-11-13"}
}
```

## Fields

| Field | Required | Rule |
|---|---|---|
| `ticker` | yes | GPW short name as in the GPW archive (`PKOBP`, `CDPROJEKT`) or an index name (`WIG`, `WIG20`, `MWIG40`, `SWIG80`, `WIG-BANKI` ...). Pattern `[A-Za-z0-9.-]{1,20}`. |
| `date` | yes | `YYYY-MM-DD`, exactly 10 characters. It is the session whose close produced the signal, and it must not be later than the report's `as_of`. A weekend date is mapped to the previous session. |
| `kind` | yes | Lower-case slug `[a-z0-9_]{1,40}`. Keep it stable across versions, because it is the unit of evaluation. Examples: `insider_cluster_buy`, `crowded_short`, `earnings_strong_negative`, `quality_red_flag`. |
| `direction` | yes | `long` means "expect it to beat the benchmark". `avoid` means "stay out, expect it to lag". `exit` means "reduce or close, expect it to lag". |
| `score` | no | Number from 0 to 100, higher = stronger. It is bucketed into quintiles (0-20 ... 80-100) to test whether a higher score means a better outcome. |
| `price` | no | Unadjusted close on `date` as the skill saw it. It is used to rescale `pivot` and `invalidation` after later dividends or splits. |
| `pivot` | no | Unadjusted price level that confirms the idea. The skill reports whether it was touched before the stop. |
| `invalidation` | no | Unadjusted stop level that kills the idea. |
| `tags` | no | Object of short scalar values (at most 80 characters each). It is stored for reference and not used as a bucket dimension. |

## Validation

Each item is validated on its own. When an item is invalid, it is rejected
with a reason code, and the rest of the report is still recorded. The reason
codes are: `not_an_object`, `bad_ticker`, `bad_date`, `date_after_as_of`,
`bad_kind`, `bad_direction`, `bad_score`, `bad_price`, `bad_pivot`,
`bad_invalidation`, `bad_tags`. A report without a `skill` field is rejected
as a whole (`generic_report_without_skill`).

## Deduplication

The key is `(source skill, ticker upper-cased, date, kind)`. Recording the
same report again, or a later report that repeats the same signal, adds
nothing. Emit a signal **once, on the day it fires**, not every day while a
condition holds. Repeated daily copies are overlapping observations that
inflate n without adding information.

## How outcomes are judged

| direction | basis | hit (`true_positive`) | otherwise |
|---|---|---|---|
| `long` | stock: excess vs WIG; broad index: absolute return | edge > 0 | `regime_mismatch` if WIG was in correction/bear at the signal or during the window, else `false_positive` |
| `avoid`, `exit` | same | the instrument lagged (edge > 0) | `missed_opportunity` |

The primary horizon (default 20 sessions) sets the outcome. All horizons are
reported.

## Built-in adapters (no `signals[]` needed)

- `gpw-trend-screener` (mode `screen`): watchlist items become `long` (`vcp_setup` or `trend_leader`); other Trend-Template candidates become near misses (`skip`, internal only).
- `gpw-market-regime`: each state transition in the report history becomes a signal. `ftd` and `regime_confirmed_uptrend` are long; `distribution_cluster` and `regime_uptrend_under_pressure` are exit; `regime_correction` and `regime_bear` are avoid. The WIG history also becomes the regime timeline.
- `gpw-market-breadth` and `gpw-sector-trend`: a composite score of 60 or more is `long` on WIG, and a score below 40 is `avoid` on WIG. Sector trend also emits one signal per sector index (`sector_uptrend` / `sector_downtrend`).

A known skill that also emits `signals[]` gets both: the adapter signals and the generic ones.
