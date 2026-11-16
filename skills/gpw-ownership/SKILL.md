---
name: gpw-ownership
description: Shows who is buying and who is betting against companies listed on GPW (Warsaw Stock Exchange). Mode shorts reads the official KNF register of net short positions of 0.5% or more (current and history since 2012): total disclosed %, holders, 20-session change, days in register, days to cover, price since the position appeared, new and closed positions and a crowded flag. Mode insiders detects MAR art. 19 managers' transaction notices on ESPI; mode parse turns pasted notification PDF text into stored transactions, so buy/sell clusters, closed-period trades and excluded plan or pledge deals are counted. Mode funds reads OFE/PPK annual portfolio files into values, approximate shares and yearly flows. Mode ticker shows all of it for one company. PL triggers - "krótka sprzedaż", "rejestr shortów KNF", "kto shortuje", "transakcje insiderów", "zarząd kupuje akcje", "art. 19 MAR", "portfele OFE". EN triggers - "short interest Poland", "insider buying GPW", "OFE holdings". Not a buy or sell signal, not for US stocks.
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
---

# GPW Ownership / Kto kupuje, kto gra na spadki

Three public ownership signals for GPW companies, in one skill:

| Mode | Source | Automation |
|---|---|---|
| `shorts` | KNF register of net short positions (JSON behind rss.knf.gov.pl) | fully automatic |
| `insiders` + `parse` | ESPI list on gpw.pl (detection) + the notification PDF text the user pastes (details) | semi-automatic |
| `funds` | OFE/PPK "Roczna struktura aktywów" files (user files or analizy.pl) | semi-automatic, yearly |
| `ticker` | all of the above for one company | used by research-dossier and pre-trade-gate |

**Workflow position.** Fed by: KNF, ESPI (gpw.pl), OFE/PPK annual reports,
the GPW archive (prices, turnover) and the gpw.pl company card (shares
outstanding). Feeds: `gpw-research-dossier` (ownership section),
`gpw-pre-trade-gate` (crowded short, insider selling cluster),
`gpw-daily-brief` (new KNF changes and insider notices for watchlist names)
and `gpw-signal-postmortem` (`signals[]` ledger).

## When to use

- "Kto shortuje KRUK?", "Czy MODIVO jest mocno shortowane?", "lista shortów KNF".
- "Czy zarząd kupował akcje?", "transakcje insiderów w tym miesiącu", the user
  pastes an "Powiadomienie o transakcji" / PDMR form.
- "W co inwestują OFE?", the user gives a "Roczna struktura aktywów" file.
- Before a trade: `ticker <TICKER>` to see short crowding and insider flow.

## Language

Answer in the user's language; pass `--lang pl` (default) or `--lang en`.

## Workflow

Run from the skill directory. Common flags: `--lang`, `--output-dir` (default
`.`), `--no-files`, `--json`, `--offline`, `--cache-dir`, `--state-dir`
(default `~/.gpw-trading-skills`), `--as-of YYYY-MM-DD`.

| Input | Flag | Default |
|---|---|---|
| change window start (shorts) | `--since YYYY-MM-DD` | only the 20-session window |
| limit to tickers | `--ticker T` (repeat) | all |
| skip shares lookups | `--no-shares` | look up (1 cached request per issuer) |
| insider period | `--days N` or `--start/--end` | 30 calendar days |
| read report pages | `--details` (`--max-details 40`) | off, except for `--ticker` |
| notification text | `parse --file F` (or `-` for stdin, or a text PDF) | stdin |
| fund files | `funds --file F` (repeat), `--fetch-code ENNE1 --year 2025` | — |
| name fixes | `funds --name-map map.json` | built-in aliases |

```bash
python3 scripts/gpw_ownership.py shorts --lang pl
python3 scripts/gpw_ownership.py insiders --days 30
python3 scripts/gpw_ownership.py insiders --ticker KRUK --days 90
python3 scripts/gpw_ownership.py parse --file notice.txt --ticker KRUK
python3 scripts/gpw_ownership.py funds --file nn_ofe_2025.pdf --file nn_ofe_2024.pdf
python3 scripts/gpw_ownership.py funds --fetch-code ENNE1 --year 2024 --year 2025
python3 scripts/gpw_ownership.py ticker MODIVO
```

Exit 1 = data problem (message on stderr): report it, never fill gaps from
memory. Exit 2 = bad arguments.

### Getting insider details (the user's part)

gpw.pl blocks scripts from downloading attachment PDFs, and the skill must
not get around it. When a notice matters, ask the user to open the report
link from the table, open the PDF, select all text and paste it (or save it
to a file) and run `parse`. Parsed transactions are stored in
`<state-dir>/insiders/transactions.json`, so later runs count them. Never
type volumes or prices yourself.

### Present the result

1. **Lead with the answer**: e.g. "KRUK: 2.67% of shares disclosed short by 4
   funds, +0.13 pp in 20 sessions, crowded" or "3 board members bought within
   30 days (cluster buy), net +1.2 mln zł".
2. Explain in plain Polish: what a short position is (betting the price will
   fall), that the register is a **lower bound** (only positions >= 0.5%),
   what days to cover means (how many average sessions the shorts would need
   to buy back), why insider *buys* carry more information than sells.
3. Warnings: data freshness (`data_provenance.freshness.is_stale` first),
   `warnings[]`, `flags` such as `closely_associated_entity`,
   `plan_or_non_market_excluded`, `non_market_notice:pledge`,
   `details_missing_paste_pdf`, `trade_in_closed_period`, approximations in
   funds mode.
4. Context, not advice: a crowded short is a reason to be careful (negative
   drift on average, but squeeze risk), a cluster buy is a reason to look
   closer. Never say "buy" or "sell".

## Key rules

- Short totals are a lower bound; closing dates of positions are not
  published (the pair just disappears), so `closed_position` events carry
  `exact_date: false` unless local snapshots bracket them.
- Insider counts from notice titles are hints (`basis: notice_hints`); values
  and clusters are reliable only with `basis: parsed_transactions`.
- `net_value_pln` is null unless parsed details exist; nothing is estimated.
- Closely associated **legal persons** (parent companies, banks acting as
  brokers, family foundations) are flagged and excluded from the cluster
  count. Option exercises, incentive plans, gifts, pledges, subscriptions and
  tender offers are excluded from buy/sell counts.
- Some issuers tag every ESPI report with the ESAP code TRANSD; a report is
  only treated as an insider notice when its text also refers to MAR art. 19.
- OFE/PPK data is annual and approximate (value / year-end close). There is
  no evidence that following OFE pays; present it as context.
- Thresholds and their sources: `references/methodology.md`. Endpoints and
  legal notes: `references/data_sources.md`.

## Output contract (JSON)

Envelope: `skill`, `version`, `lang`, `mode`, `as_of`, `data_provenance`,
`warnings[]`, `signals[]` (`{ticker, date, kind, direction, score, source}`;
kinds `short_crowded`, `short_new` → `avoid`; `insider_cluster_buy` → `long`;
`insider_cluster_sell` → `exit`; `fund_accumulation` → `long`).

- `shorts.by_issuer[]`: `{ticker, isin, total_pct, holders, change_20s_pp,
  crowded, ...days_in_register, days_to_cover, price_change_since_first_pct,
  holder_list[]}`, `shorts.events[]`.
- `insiders.by_ticker[]`: `{ticker, buys, sells, distinct_insiders,
  net_value_pln|null, cluster_buy, cluster_sell, last_date, basis, flags[],
  transactions[], notice_list[]}`.
- `funds.by_ticker[]`: `{ticker, value_pln, approx_shares, funds_holding,
  implied_flow_pln, approx_shares_change, fund_accumulation, approximate}`,
  `funds.top_buys[]`, `funds.top_sells[]`.
- Tickers: every `ticker` (shorts, insiders, funds, parse, ticker mode,
  signals) is the **current** GPW listing - ISIN first (current listing beats
  pre-rename aliases), then `gpwlib.panel.RENAMES` (e.g. ISIN PLCCC0000016 →
  `MODIVO`, never `CCC`). The source's own name is kept in `register_name`
  (KNF issuer, ESPI company, fund-report name); `ticker` mode run with an old
  ticker adds `requested_ticker`.

## References

- `references/methodology.md` — evidence (Desai et al. 2002, Boehmer-Jones-
  Zhang 2008, Jones-Reed-Waller 2016, Lakonishok-Lee 2001, Jeng-Metrick-
  Zeckhauser 2003, Cohen-Malloy-Pomorski 2012, Bohl-Wiśniewski on WSE) and
  every threshold.
- `references/data_sources.md` — KNF, ESPI and fund endpoints, legal frame
  (SSR 236/2012, MAR art. 19, Listing Act), known data quirks.

## Related skills

`gpw-pre-trade-gate` (uses crowded shorts and insider selling), `gpw-research-dossier`
(ownership section), `gpw-daily-brief`, `gpw-signal-postmortem` (records
`signals[]`), `gpw-trend-screener` and `gpw-watchlist` (where the names come
from), `gpw-event-calendar` (report dates for the closed period).
