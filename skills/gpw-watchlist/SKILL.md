---
name: gpw-watchlist
description: Stateful watchlist of Polish stocks (GPW, Warsaw Stock Exchange) between "a skill found it" and "I bought it". Each ticker moves through stages candidate, watch, ready (pivot and stop set), entered (gpw-trade-journal thesis ACTIVE) or dropped, with sources, notes, tags and history. Ingests the newest gpw-trend-screener, gpw-ownership, gpw-quality-screener, gpw-earnings-reaction, gpw-dividend-analyzer and gpw-pair-trading reports via a documented rules table, and refreshes items against the newest GPW quotes and gpw-event-calendar - distance to pivot, buy range up to 5% above it, stop breached, 52-week high, turnover surge, events in 10 sessions - as alerts with severity; reviews stale or invalidated names. Use when the user wants to follow, add, promote or drop stocks or asks what is near a buy point. PL triggers - "dodaj do obserwowanych", "moja watchlista", "co jest blisko pivotu", "które spółki są gotowe". EN triggers - "add to watchlist", "my GPW watchlist". Not for screening the market or placing orders.
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
---

# GPW Watchlist / Lista obserwacyjna GPW

A persistent list of the stocks the user follows, kept in
`~/.gpw-trading-skills/watchlist.json`. Each ticker goes through stages:

**candidate → watch → ready → entered**, or **dropped**.

The skill pulls ideas and warnings from other skills' JSON reports. After
each session it checks every followed name against the newest quotes and the
event calendar. Then it says, answer first, what needs attention today. For
example: "3 spółki blisko pivotu, 1 przebiła stop - do usunięcia?".

**Workflow position:** fed by gpw-trend-screener (ideas, pivot, stop),
gpw-ownership, gpw-quality-screener, gpw-earnings-reaction,
gpw-dividend-analyzer and gpw-pair-trading (tags and warnings),
gpw-event-calendar (events) and gpw-trade-journal (theses, `entered`). It
feeds gpw-daily-brief, gpw-research-dossier, gpw-pre-trade-gate and
gpw-event-calendar `portfolio` mode, all of which read the state file
(contract: `references/watchlist-contract.md`).

## When to use

- "Add KETY to my watchlist with pivot 1281 and stop 1229" / "dodaj KETY do obserwowanych".
- "Import the screener results into my watchlist" / "zaktualizuj listę ze skanera".
- "What is close to a buy point?", "czy coś przebiło stop?", "co jest gotowe do wejścia?".
- "Clean up my watchlist" / "co usunąć z listy?".
- Not for finding new stocks: use gpw-trend-screener or gpw-quality-screener.
- Not for sizing: use gpw-position-sizer. Not for the go/no-go check: use gpw-pre-trade-gate.

## Language

Answer in the user's language and pass `--lang pl` (default) or `--lang en`.

## Workflow

Run everything from the skill directory. Every command accepts:

| flag | meaning |
|---|---|
| `--state-dir DIR` | folder of `watchlist.json` (default `~/.gpw-trading-skills`) |
| `--journal-dir DIR` / `--no-journal` | gpw-trade-journal state (default `~/.gpw-trading-skills/journal`) |
| `--lang pl\|en`, `--json`, `--output-dir DIR`, `--no-files` | output |
| `--dry-run` | compute and print, do not save the state |

### 1. Daily routine (after the 17:05 close)

```bash
python3 scripts/gpw_watchlist.py ingest  --input-dir <reports dir>   # newest producer reports
python3 scripts/gpw_watchlist.py refresh --input-dir <reports dir>   # quotes + events -> alerts
```

- `ingest` reads the newest report of each producer and mode from
  `--input-dir` (repeatable). It skips reports that are too old, with a
  warning. `--from trend-screener,ownership` limits the producers.
  `--rules FILE` overrides the rules table.
- `refresh` uses the GPW archive cache (`--offline`, `--cache-dir`,
  `--as-of`). It also reads every fresh gpw-event-calendar JSON in
  `--input-dir`, or a single file given with `--events-json`.
- For a complete event check, run gpw-event-calendar in `portfolio` mode
  first. That mode reads this watchlist.

### 2. User actions

```bash
python3 scripts/gpw_watchlist.py add KETY --stage watch --pivot 1281 --stop 1229 --note "VCP, 3 skurcze"
python3 scripts/gpw_watchlist.py levels KETY --pivot 1290 --stop 1235 [--target 1450]
python3 scripts/gpw_watchlist.py promote KETY --to ready        # needs pivot + stop
python3 scripts/gpw_watchlist.py promote KETY --to watch --reason "za daleko od pivotu"
python3 scripts/gpw_watchlist.py note KETY "wyniki 20.11 - poczekać"
python3 scripts/gpw_watchlist.py drop KETY --reason "zamknięcie pod stopem"   # keeps history
python3 scripts/gpw_watchlist.py remove KETY --reason "pomyłka"               # deletes the record
python3 scripts/gpw_watchlist.py list [--stage ready] [--tag short_crowded]
python3 scripts/gpw_watchlist.py show KETY
python3 scripts/gpw_watchlist.py review [--stale-days 30]
python3 scripts/gpw_watchlist.py export --csv watchlist.csv
```

Stage rules:

- `ready` needs a pivot and a stop below it. If gpw-trade-journal has an
  IDEA/READY thesis for the ticker, it is linked. If there is none, the
  script warns, and you should suggest writing a thesis (gpw-trade-journal)
  before any entry.
- `entered` is set automatically once a journal thesis for the ticker is
  ACTIVE (after the buy fill is logged). Do not set it by hand.
- Automatic ingest never moves anything to `ready`.

Exit codes: 1 is an input or data error (message on stderr; say what is
wrong, do not guess), 2 is bad arguments.

### 3. Present the result

1. **Answer first**: use the `answer` line from the JSON or the first bold
   line of the Markdown.
2. If `data_provenance.freshness.is_stale` is true, or the Markdown starts
   with a warning, say so before anything else.
3. **High-severity alerts**, one line each, with the number and its meaning:
   - `stop_breached`: setup invalidated. Ask whether to drop it.
   - `pivot_breakout`: say whether turnover confirmed it.
   - `earnings_strong_negative`, `setup_damaged`.
4. **Ready items**: distance to the pivot, the buy range, the stop distance,
   events in the next 10 sessions, and whether a journal thesis exists.
5. **Medium alerts** (near the stop, extended, events). Summarise the low
   ones in one sentence.
6. For `ingest`: what was added and upgraded, and which reports were
   skipped as stale.
7. For `review`: list the suggestions with their commands. **Never apply
   them without the user's yes.**

Explain the jargon for beginners in plain Polish:

- **pivot**: the price whose break confirms the setup.
- **stop**: the price that proves the idea wrong.
- **strefa zakupu**: up to 5% above the pivot. Beyond that, do not chase.

## Key rules

- The script is the source of every price, distance and date. Never invent
  levels. If pivot or stop are missing, ask the user, or point to
  gpw-trend-screener.
- A watchlist is not a recommendation. Say "jest blisko pivotu" or
  "przebiło stop", never "kup" or "sprzedaj". `ready` means the plan is
  complete, not that buying is advised.
- Do not change stages on your own. Run a mutating command only when the
  user asks or confirms a suggestion.
- Before an actual entry, suggest gpw-position-sizer and then
  gpw-pre-trade-gate.
- The state file is a contract read by other skills. Change it only through
  this script, never by editing it by hand.

## References

- `references/watchlist-contract.md`: state schema, stage machine, alert codes and tags (for other skills).
- `references/methodology.md`: ingest rules table, thresholds with sources, review rules and limitations.

## Related skills

gpw-trend-screener, gpw-ownership, gpw-quality-screener, gpw-earnings-reaction,
gpw-dividend-analyzer, gpw-pair-trading, gpw-event-calendar, gpw-trade-journal,
gpw-position-sizer, gpw-pre-trade-gate, gpw-research-dossier, gpw-daily-brief,
gpw-signal-postmortem.
