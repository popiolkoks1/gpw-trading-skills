---
name: gpw-trade-journal
description: A trading and investment journal for Polish stocks (GPW). Each idea is stored as a falsifiable thesis with 1-5 pillars, risks, kill criteria, catalysts, entry/stop/target, account type (regular, IKE, IKZE, OKI) and a review date. The skill follows it through IDEA → READY → ACTIVE → CLOSED, records buys and sells with commissions using FIFO lots, logs new information, and on close computes P&L net of fees, the R multiple, holding days, the WIG benchmark return and excess return, a process score and lessons. Use it whenever the user bought or sold a stock, wants to note why they invest in something, track an idea, review open positions, close a trade or keep a trading diary, even if they don't say "journal". Triggers (PL) "dziennik inwestycyjny", "zapisz transakcję", "kupiłem", "sprzedałem", "teza inwestycyjna", "dlaczego kupiłem", "przegląd pozycji", "zamknij pozycję"; (EN) "trade journal", "log this trade", "track this idea", "investment thesis", "close position".
license: MIT
metadata:
  version: "1.0.0"
  derived_from: "tradermonty/claude-trading-skills trader-memory-core (MIT)"
  languages: "pl,en"
---

# GPW Trade Journal

A persistent memory of **what you thought, what you did and what happened**. It
works across conversations because everything is stored in local JSON files. The
script validates and computes; your job is to interview the user well.

## When to use

- The user bought or sold shares, or plans to ("kupiłem 50 PKO po 118").
- The user wants to record an investment idea or the reason behind a trade.
- The user asks what is in their journal, or which theses need a review.
- New information appeared (results, an ESPI report, dividend news) and the thesis should be updated.
- The user closes a position and wants the result.

Do **not** use for: annual statistics and PIT-38 (use `gpw-trade-review`), sizing a
new trade (use `gpw-position-sizer` first, then import its JSON here), or market
analysis.

## Language

Answer in the user's language. Pass `--lang pl` or `--lang en`. Store the thesis
text in whatever language the user writes in.

## The interview (most important part)

A thesis that cannot be wrong teaches nothing. Before `add`, make sure you have:

1. **Ticker.** Use the GPW short name (PKOBP, PKNORLEN, CDPROJEKT, KGHM, DINOPL…).
2. **Thesis**, in one or two sentences: why the price should rise.
3. **Pillars** (1-5): the facts the thesis stands on.
4. **Kill criteria** (at least 1, required): concrete, observable conditions that prove
   the thesis wrong, e.g. "close below 105 zł", "Q4 net margin under 8%", "dividend cut".
   If the user says "nothing" or gives something vague ("if it goes badly"), explain why it
   matters and help them phrase a testable criterion. Never invent one on their behalf;
   propose options and let them choose.
5. **Plan:** entry, stop, target, account type. If they don't have one yet, offer `gpw-position-sizer`.
6. Optional: risks, catalysts with dates, setup type, horizon, conviction H/M/L, review date (default 30 days).

Ask for missing items in one short message, not one question at a time. For a trade
that has already happened, record it even with a minimal thesis, but still ask for one
kill criterion.

## Commands

Run from this skill's directory. The journal lives in `~/.gpw-trading-skills/journal`
(or `--state-dir`, or `$GPW_JOURNAL_DIR`). Global flags (`--lang`, `--state-dir`,
`--json`) can go before or after the sub-command. IDs can be shortened to any unique prefix.

```bash
# new thesis (status IDEA; --status READY if the entry plan is final)
python3 scripts/journal.py add --ticker PKOBP \
  --thesis "Wysokie stopy utrzymają marżę odsetkową, dywidenda >7%" \
  --pillar "Marża odsetkowa > 4%" --pillar "Wypłata 75% zysku" \
  --kill "Zamknięcie tygodnia poniżej 105 zł" --kill "RPP tnie stopy o ≥100 pb" \
  --risk "Podatek bankowy" --catalyst "2026-11-05 wyniki Q3" \
  --entry 110 --stop 104 --target 125 --account-type regular --setup dividend --conviction M

# import the plan from gpw-position-sizer output
python3 scripts/journal.py add --ticker PKOBP --thesis "..." --pillar "..." --kill "..." \
  --sizer-json gpw_position_size_2026-11-15_101500.json

python3 scripts/journal.py fill <id> --side buy --shares 40 --price 110 --fee 17.16 --date 2026-09-02
python3 scripts/journal.py update <id> --note "Q3 above consensus" --pillar 1 --conviction H --stop 112
python3 scripts/journal.py transition <id> INVALIDATED --note "kill criterion hit before entry"
python3 scripts/journal.py close <id> --price 119.90 --fee 14.03 --exit-reason target \
  --followed-entry tak --followed-stop tak --followed-size nie --lessons "..."
python3 scripts/journal.py show <id> --lang en
python3 scripts/journal.py list [--status ACTIVE]
python3 scripts/journal.py review-due [--as-of 2026-12-01]
```

- **Fills**: whole shares only. `--fee` is the commission in PLN; ask for it or estimate it with the broker's tariff (e.g. 0.39%, min 5 zł) and say it is an estimate. A first buy moves the thesis to ACTIVE.
- **close**: sells any remaining shares at `--price`. It always asks three process questions: was the entry according to plan, was the stop respected, was the size according to plan. Ask the user honestly; don't assume "yes". `--exit-reason`: `target`, `stop`, `kill_criterion`, `time`, `discretionary`, `other`.
- **Benchmark**: on close the script downloads WIG from the GPW archive to compare against. Use `--no-benchmark` or `--benchmark-return X` when offline.
- **Paper mode** (`add ... --paper`): practise without real money. Beginners should start here. Paper theses follow the same rules (kill criteria, fills, FIFO, process questions), are marked 📝, are never included in PIT-38 and are reviewed separately (`gpw-trade-review --paper`).
- **Trailing stop**: `update --stop` can move the stop above the entry once ACTIVE. The R multiple is still measured against the **initial** stop.
- Errors exit with code 1 and a clear message (e.g. missing kill criterion, sell larger than the position, backward transition). Relay them in plain words.

## Presenting results

- After `add`: confirm the ID, repeat the kill criteria and say when the next review is.
- After `close`: the net result in zł and %, the R multiple, and the comparison with WIG ("you beat or lagged the market by X pp"). Then the process: a profit made without following the plan is luck and should not be repeated. Finally the lessons. On a regular account, mention the 19% tax and point to `gpw-trade-review` for the PIT-38 summary.
- When a thesis is due for review, go through its kill criteria one by one and ask whether any has been met.

The full field list and lifecycle rules are in `references/journal_schema.md`.

## Related skills

`gpw-position-sizer` (before entry), `gpw-trade-review` (monthly and annual statistics, PIT-38 estimate), `gpw-market-breadth` (market context to note in the thesis).
