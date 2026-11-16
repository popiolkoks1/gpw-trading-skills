---
name: gpw-portfolio-check
description: Health check of a Polish stock portfolio (GPW, Warsaw Stock Exchange) from a broker CSV export or the trade journal - market value, weights, unrealised P&L, sector and WIG20/mWIG40/sWIG80 mix, concentration (HHI, effective number of positions), correlations, volatility, beta vs WIG, historical VaR and ES in PLN, max drawdown, 1-year comparison with WIG, liquidity (days to exit) and open risk to stops. Starts with a plain-Polish summary for beginners, marks each position OK or REVIEW with reasons. No API key. Use when the user shares holdings or asks how risky or diversified their Polish portfolio is. PL triggers - "sprawdź mój portfel", "czy mój portfel jest zdywersyfikowany", "ryzyko portfela", "za dużo banków?", "analiza portfela GPW", "mam takie akcje". EN triggers - "check my GPW portfolio", "portfolio risk Poland", "am I too concentrated". Not for picking stocks, rebalancing orders or US portfolios.
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
  derived_from: "tradermonty/claude-trading-skills portfolio-manager (MIT), offline CSV mode adapted to GPW"
---

# GPW Portfolio Check / Przegląd portfela GPW

Takes what the user already holds on GPW (shares and ETFs) and answers the
beginner's question "is my portfolio OK?" with numbers: how concentrated it
is, which holdings move together, how much a bad day can cost, how it
compared with the WIG, whether positions can be sold quickly and how much is
at risk to stops. Positions are marked **OK** or **REVIEW** – never "sell".

## When to use

- The user pastes or attaches holdings (broker export, list of tickers and
  amounts) or asks about the risk / diversification of their Polish portfolio.
- The user keeps positions in `gpw-trade-journal` and wants an overview
  (`--from-journal`, includes stops and portfolio heat).

## Language

Answer in the user's language; pass `--lang pl` (default) or `--lang en`.

## Workflow

### 1. Get the holdings

| Input | How |
|---|---|
| CSV file | `--csv FILE` with header `ticker,shares,avg_price[,account_type][,stop]` |
| Journal | `--from-journal [--state-dir DIR]` (ACTIVE theses, FIFO lots, stops) |
| Cash | `--cash 5000` (PLN, optional; counts in weights and risk as zero-volatility) |

CSV rules (`references/csv_format.md`): `;`, tab or `,` separators; decimal
commas (`58,20`); Polish headers (`Walor;Ilość;Średnia cena;Rachunek`);
`account_type` = regular/zwykły, ike, ikze, oki; `#` comments; duplicates
merged. If the user pastes a list in chat, write it to a CSV file first – do
not invent missing average prices, ask for them.

Tickers are GPW archive short names (PKOBP, PKNORLEN, CDPROJEKT, DINOPL...);
common 3-letter codes (PKN, PKO, CDR, KGH...) are mapped and reported. An
unknown ticker stops the run with close matches – ask the user which one.

### 2. Run (from the skill directory)

```bash
python3 scripts/portfolio_check.py --csv holdings.csv --cash 5000 --lang pl --output-dir <dir>
python3 scripts/portfolio_check.py --from-journal --lang en --output-dir <dir>
```

Other flags: `--as-of YYYY-MM-DD`, `--offline`, `--cache-dir`, `--lookback`
(default 252 sessions = 1 year), `--no-files`. Shares/indices come from the
shared cache (`~/.cache/gpw-trading-skills`); ETF quotes are downloaded only if
an ETF is held (first time ~1 s per session). Exit 1 = input/data problem:
relay the message, do not guess.

### 3. Present

1. If `data_provenance.freshness.is_stale` – say so first.
2. Lead with the **"W skrócie / In short"** paragraph (3-5 plain sentences);
   keep it plain for beginners.
3. "What needs attention": each flag in one sentence, with the number and the
   threshold. Explain jargon on first use: HHI / effective positions ("your 7
   stocks behave like 3 independent bets"), VaR ("a bad day, 1 in 20"), beta,
   drawdown, days to exit.
4. Positions marked REVIEW with their reasons. REVIEW is a prompt to think
   (is this concentration intended? is there a plan/stop?), not a sell signal.
5. Optional details on request: correlation matrix, per-position beta/vol,
   liquidity table, sector/size/account buckets.

### 4. Caveats to mention when relevant

- P&L uses the broker's **average price**; PIT-38 uses **FIFO** cost – they
  can differ after top-ups and partial sales (journal mode uses FIFO lots).
  Dividends received are not included. The "indicative tax" line is a rough
  19% of the regular-account gain only.
- Risk numbers are a **historical simulation of today's weights** over the
  last year; VaR is not a loss limit and the 20-session √time scaling
  understates crisis losses.
- Sector = GPW Benchmark sector index membership (current, not point-in-time);
  insurers, holdings and many small caps have no sector index ("no sector
  index"). ETFs are one group, exempt from single-position and sector limits.
- Holdings with < 60 sessions of history are left out of risk statistics.

## Flags and thresholds

| Flag | Threshold |
|---|---|
| Single position (not ETF) | > 20% of portfolio |
| Sector (GPW sector index) | > 35% of portfolio |
| Concentration | HHI > 0.18 (moderate 0.10-0.18) while single stocks are > 50% of the book |
| Illiquid | > 5 sessions to exit at 10% of 60-session median turnover |
| Correlated cluster | daily-return correlation > 0.7 (1 year) |
| No stop / at or below stop | only when stops are known (journal or `stop` column) |
| Stale quote | no trade for > 5 sessions |
| Check avg price | last price < 0.2× or > 5× avg price (split? typo?) |

Rationale and sources: `references/methodology.md`.

## Key rules

- Never say "sell X" / "buy X"; never suggest a target allocation of specific
  stocks. Frame REVIEW as questions for the user.
- Every number comes from the script; quote the session date.
- Report ends with the disclaimer (the script appends it).

## References

- `references/methodology.md` – every metric, formula and threshold with sources.
- `references/csv_format.md` – CSV format, examples of broker exports, errors.

## Related skills

`gpw-pre-trade-gate` (checklist before adding a position), `gpw-position-sizer`
(how many shares), `gpw-trade-journal` (theses, stops, FIFO lots - feeds
`--from-journal`), `gpw-sector-trend` and `gpw-market-breadth` (market context),
`gpw-trade-review` (closed-trade statistics, PIT-38).
