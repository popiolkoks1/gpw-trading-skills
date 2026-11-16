---
name: gpw-pre-trade-gate
description: Fail-closed checklist run BEFORE buying a Polish stock or ETF on GPW (Warsaw Stock Exchange). Checks stop, risk incl. round-trip fees, position size, order vs median turnover, portfolio heat, sector concentration, a written journal thesis with kill criteria, reward/risk, circuit breaker, market regime, exposure limits and data freshness, plus context from other GPW skills - earnings or ex-dividend in the holding window (typical earnings gap vs the stop), crowded KNF short, insider selling cluster, strong negative earnings reaction, quality red flags, macro headwind, portfolio stress. Verdict GO, GO WITH CONDITIONS or NO-GO with reasons. Never places orders. Use when the user is about to enter a trade or asks if a planned buy is OK. PL triggers - "chcę kupić", "czy mogę wejść", "sprawdź transakcję przed zakupem", "czy są wyniki w okresie trzymania". EN triggers - "pre-trade check", "should I take this trade", "go/no-go before buying". Not for stock picking, valuation or sizing alone (gpw-position-sizer).
license: MIT
metadata:
  version: "1.1.0"
  languages: "pl,en"
  derived_from: "tradermonty/claude-trading-skills pre-trade discipline gate (MIT), adapted to GPW"
---

# GPW Pre-Trade Gate / Bramka przed transakcją

A checklist an experienced trader runs before pressing "buy": is the risk
defined and small, is the position not too big for the account or for the
stock's liquidity, is the total risk of the book under control, is there a
written thesis with a way to be proven wrong, does the trade pay enough for
its risk, am I tilting after losses, and is the market environment hostile.
It is **fail-closed**: a missing stop or a missing written thesis is a FAIL,
not a pass.

**Workflow position:** fed by gpw-position-sizer (`--sizer-json`),
gpw-trade-journal (thesis, open positions, closed trades), gpw-market-regime,
gpw-exposure-coach and, since 1.1, the v1.2 skills gpw-event-calendar,
gpw-ownership, gpw-earnings-reaction, gpw-quality-screener,
gpw-macro-regime, gpw-dividend-analyzer and gpw-risk-lab (all as JSON files);
feeds the decision to record the fill in gpw-trade-journal.

## When to use

- The user describes a planned buy (ticker, entry, stop, shares) or just ran
  `gpw-position-sizer` and wants to know if the trade is OK.
- Before recording a buy fill in `gpw-trade-journal`.

## Language

Answer in the user's language; pass `--lang pl` (default) or `--lang en`.

## Workflow

### 1. Inputs

| Input | Flag | Notes |
|---|---|---|
| Ticker, entry, stop, shares | `--ticker --entry --stop --shares` | or `--sizer-json` (gpw-position-sizer report); explicit flags override the JSON |
| Account size | `--account-size` | PLN; from the sizer JSON if given |
| Target | `--target` | optional; else the thesis plan target |
| Account type | `--account-type regular/ike/ikze/oki` | shown in the report |
| Thesis | `--thesis-id` | id or unique prefix; default: the single open thesis for the ticker |
| Journal | `--state-dir` | default `~/.gpw-trading-skills/journal` / `$GPW_JOURNAL_DIR` |
| Regime / exposure | `--regime-json`, `--exposure-json` | default: newest report of that skill in `--input-dir` (default `.`) |
| Context reports (v1.2) | `--events-json`, `--ownership-json`, `--earnings-json`, `--quality-json`, `--macro-json`, `--dividend-json`, `--risk-lab-json` | default: newest report of that skill in `--input-dir` that covers the ticker; missing = UNKNOWN |
| Holding window | `--holding-days 20`, `--event-buffer-sessions 5` | sessions from the next session after the last data session |
| Strict events | `--strict-events` | NO-GO when a report falls in sessions 1-2 and the stock's typical earnings gap is larger than the stop distance |
| Required context | `--require event_window,quality_red_flags` | listed checks fail when their input is missing (fail-closed on request) |
| Broker fees | `--broker`, `--commission-pct`, `--commission-min` | default mbank preset (or the sizer's tariff) |
| Override | `--override "written reason"` | only lifts the bear-market FAIL; ≥ 15 characters; recorded |

If the stop or the share count is unknown, ask for it or run
`gpw-position-sizer` first – never invent a stop.

### 2. Run (from the skill directory)

```bash
python3 scripts/pre_trade_gate.py --ticker PKOBP --entry 120 --stop 113 --shares 60 --target 138 --account-size 50000 --lang pl --output-dir <dir>
python3 scripts/pre_trade_gate.py --sizer-json gpw_position_size_2026-11-15_101500.json --thesis-id th_pkobp --input-dir <dir>
python3 scripts/pre_trade_gate.py --ticker MODIVO --entry 112.5 --stop 104 --shares 30 --target 135 --account-size 100000 --input-dir <dir> --holding-days 20 --strict-events --lang en
```

For the full context, first write the v1.2 reports into the same `<dir>`
(from the skill directory; each takes ~seconds when cached):

```bash
python3 ../gpw-event-calendar/scripts/gpw_event_calendar.py window --ticker MODIVO --days 20 --output-dir <dir>
python3 ../gpw-ownership/scripts/gpw_ownership.py ticker MODIVO --output-dir <dir>
python3 ../gpw-earnings-reaction/scripts/gpw_earnings_reaction.py ticker MODIVO --history --output-dir <dir>
python3 ../gpw-quality-screener/scripts/gpw_quality_screener.py ticker MODIVO --output-dir <dir>
python3 ../gpw-macro-regime/scripts/gpw_macro_regime.py --output-dir <dir>
python3 ../gpw-dividend-analyzer/scripts/gpw_dividend_analyzer.py ticker MODIVO --output-dir <dir>
python3 ../gpw-risk-lab/scripts/gpw_risk_lab.py stress --journal --max-loss-pct 20 --output-dir <dir>   # optional
```

Every missing input is listed in the report (UNKNOWN) with exactly this
command, so you can also run the gate first and fill the gaps it names.

Flags: `--offline`, `--cache-dir`, `--as-of`, limit overrides
`--max-risk-pct --max-position-pct --max-adv-pct --max-heat-pct
--max-sector-pct --min-rr`, `--no-files`, `--holding-days`,
`--event-buffer-sessions`, `--strict-events`, `--require`. Exit 0 = report produced (whatever
the verdict); 1 = input/data error (e.g. unknown ticker with close matches);
2 = bad arguments.

### 3. Present

1. Verdict first: **GO / GO WITH CONDITIONS / NO-GO**, then the blockers
   (FAIL) and conditions (WARN), each in one sentence with the number and
   limit. Stale data warning, if any, goes above everything.
2. For NO-GO, say what would change the verdict (fewer shares → re-run the
   sizer; write the thesis → show the journal command from the report; wait
   for the circuit breaker / regime).
3. SKIP rows (no regime / exposure file, no target) mean "not checked" –
   mention the lower confidence and suggest running `gpw-market-regime` /
   `gpw-exposure-coach`.
4. Context checks (14-20): lead with any WARN - earnings in the window
   (give the date, its date kind - a statutory deadline means "not later
   than" - and the typical gap vs the stop), crowded short (explain that both
   a squeeze and negative drift are possible), insider selling cluster,
   strong negative reaction, red flags (negative equity first and in bold).
   INFO rows (macro, ex-dividend, MPC, index revision) are background in one
   line each; for an ex-dividend date say the price drops by about the DPS on
   that day, so a tight stop can trigger on the cut alone.
5. UNKNOWN rows: name them, say confidence is lower and show the command the
   report gives. Never fill a missing report from memory.
6. If an override was used, quote the recorded reason.
7. Never say "buy"; the user decides and places the order.

## Checks

| # | Check | PASS | WARN | FAIL |
|---|---|---|---|---|
| 1 | Stop | below entry | – | missing or ≥ entry |
| 2 | Risk incl. round-trip fees | ≤ limit (1% or exposure-coach limit) | – | > limit |
| 3 | Position size | ≤ 25% of account | – | > 25% or more than the cash |
| 4 | Liquidity | order ≤ 10% of 20-session median turnover | no turnover data | > 10% or zero turnover |
| 5 | Portfolio heat | open risk + this trade ≤ 6% | open positions without stop / no journal | > 6% |
| 6 | Sector | after trade ≤ 35% (unclassified = PASS with note) | – | > 35% |
| 7 | Written thesis | exists, open, same ticker, kill criteria | stop differs from plan > 0.5% | missing / not found / ambiguous / closed / no kill criteria |
| 8 | Reward/risk (net of fees) | ≥ 1.5R | 1.0–1.5R | < 1.0R (SKIP without target) |
| 9 | Circuit breaker | ok | 3+ losses in a row or coach "reduce" (halve size) | 5+ losses, realised DD ≥ 10%, or coach "pause" |
| 10 | Market regime | uptrend states | correction, bear, top risk ≥ 80, override used | bear with top risk ≥ 80 (no override) |
| 11 | Exposure ceiling | ≤ coach ceiling | above ceiling | – (SKIP without coach) |
| 12 | New positions / week | ≤ coach limit | above | – (SKIP without coach) |
| 13 | Data freshness | current | stale prices, entry > 10% from last close, an input report older than its limit (regime / exposure / events 3, ownership / earnings / risk-lab 5, macro 10, dividend 22, quality 65 sessions) | – |

Context checks (1.1, from the v1.2 skills; statuses PASS / INFO / WARN /
FAIL / UNKNOWN):

| # | Check | Input | PASS / INFO | WARN | FAIL |
|---|---|---|---|---|---|
| 14 | `event_window` | gpw-event-calendar (+ dividend-analyzer ex-dates, earnings-reaction gap history and fallback date) | no events (PASS); only ex-dividend / AGM / MPC / index revision / MSCI / GUS (INFO) | earnings inside the holding window or within 5 sessions after it | only with `--strict-events`: report in sessions 1-2 and typical earnings gap > stop distance (or gap unknown) |
| 15 | `crowded_short` | gpw-ownership shorts | not in KNF register / below threshold | `crowded` (≥ 2% or ≥ 3 holders): total %, holders, days to cover | – |
| 16 | `insider_selling` | gpw-ownership insiders | no cluster; cluster buy = positive note | cluster sell, last ≤ 60 days ago | – |
| 17 | `earnings_reaction` | gpw-earnings-reaction | other labels, or > 20 sessions ago | `strong_negative` within 20 sessions | – |
| 18 | `quality_red_flags` | gpw-quality-screener | no red flags | any red flag; negative equity = prominent WARN | – |
| 19 | `macro_context` | gpw-macro-regime | always INFO (composite + sector tilt) | – | – |
| 20 | `stress_test` | gpw-risk-lab (optional) | within tolerance (PASS) / no tolerance (INFO) | portfolio already breaches the loss tolerance | – |

A missing report, or one that does not cover the ticker, is UNKNOWN with
the command that produces it. UNKNOWN and INFO never change the verdict,
unless the check is listed in `--require` (then UNKNOWN → FAIL).

Verdict: any FAIL → NO-GO; else any WARN → GO WITH CONDITIONS; else GO.
Only the 13 core checks, `event_window` under `--strict-events` and a
`--require`d check without data can produce NO-GO; the other context checks
add conditions at most. Rationale and sources: `references/gate_rules.md`.

## Key rules

- Fail-closed: missing stop or thesis = FAIL. Missing optional context = SKIP,
  stated explicitly. Never fabricate a regime or exposure file.
- Heat, sector and exposure see only ACTIVE journal positions – say so if the
  user holds positions outside the journal.
- The override is for the regime block only, must be a real written reason
  and is always shown in the report.
- Never place orders, never say "buy". The disclaimer is appended.
- Context reports are matched by ticker and ISIN (a company renamed on GPW
  may still appear under its old short name in another feed); a report
  about another company is not used.
- Macro is slow context: it is INFO and can never block or warn.

## References

- `references/gate_rules.md` – every check, threshold, source, input contract
  (section 7: context checks and their evidence).

## Output (JSON)

`gpw_pre_trade_gate_YYYY-MM-DD_HHMMSS.json`: `skill`, `version` (1.1.0),
`lang`, `verdict`, `counts{PASS, WARN, FAIL, SKIP, UNKNOWN, INFO}`,
`checks[]` (`{id, status, reason, params}`; context checks add `source{skill,
file, as_of, sessions_behind, stale}` and `command` when UNKNOWN), `trade`,
`numbers`, `inputs[]` (`{skill, file, path, as_of, sessions_behind,
max_sessions_behind, stale, warning}`), `context{}` (`holding_window`,
`events_in_window[]`, `earnings_gap`, one block per context check,
`missing_inputs[]` with commands, `thresholds`), `data_provenance`
(`input_reports[]`). The 1.0 fields are unchanged.

## Related skills

`gpw-position-sizer` (share count, feeds `--sizer-json`), `gpw-trade-journal`
(thesis with kill criteria; record the fill after entry), `gpw-market-regime`
and `gpw-exposure-coach` (regime / limits JSON), `gpw-event-calendar`
(holding window), `gpw-ownership` (short register, insiders),
`gpw-earnings-reaction` (reaction labels, gap history),
`gpw-quality-screener` (red flags), `gpw-macro-regime` (macro context),
`gpw-dividend-analyzer` (ex-dates), `gpw-risk-lab` (stress vs tolerance),
`gpw-portfolio-check` (whole-portfolio view), `gpw-trade-review` (after a
losing streak).
