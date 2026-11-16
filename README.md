# GPW Trading Skills

**27 Claude Skills for the Polish stock market (GPW, Warsaw Stock Exchange),
connected into one process.**

- **For beginners:** readiness to invest, IKE/IKZE/OKI accounts, an ETF plan.
- **For advanced users:**
  - market context: market regime, sector rotation (RRG), NBP macro;
  - finding and researching stocks: a Minervini/VCP screener, ownership and the KNF short register, the ESPI event calendar, earnings reactions, quality, valuation, dividends;
  - risk: stress tests, FW20 hedging;
  - checking yourself: an honest backtester, a signal post-mortem;
  - daily use: a company dossier with a bull-vs-bear debate, and a daily brief.

🇵🇱 [Wersja polska → README.pl.md](README.pl.md)

> Educational tools, not investment advice. The skills never place orders and never
> tell you what to buy. They give you data, risk numbers and a structured process
> so that *you* make better decisions. See [Disclaimer](#disclaimer).

## Why

Most AI trading toolkits target US markets and paid data APIs. Polish investors
face different realities:

- liquidity is thin outside WIG20/mWIG40;
- broker fees have a PLN minimum, and shares are bought in whole lots;
- gains carry the 19% "Belka" tax, reported FIFO on PIT-38;
- there are IKE/IKZE accounts and, from 2027, OKI;
- companies file through ESPI, and short positions are listed in a KNF register.

This set follows the best open-source trading-skill repos
([tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills),
Anthropic financial-services plugins, TradingAgents, FinRobot). For each skill
we studied equivalents on other markets and the research evidence, including
studies on the WSE, then rebuilt them for Polish realities.

The data is **only free, official sources**, with **no API keys**: the GPW quotes
archive, ESPI on gpw.pl, GPW Benchmark, the KNF short register, the NBP API,
BondSpot, ECB, Bundesbank, Eurostat and statute texts.

## Where to start

**New to investing?** Run these in order:
1. `gpw-start`
2. `gpw-account-planner`
3. `gpw-etf-planner`
4. `gpw-portfolio-check` and `gpw-dividend-analyzer` once a year

Or just ask: *"I know nothing about investing, where do I start?"*

**Active investor?** Start with `gpw-daily-brief`. After the close it runs the
whole daily pipeline and gives you one page: what changed, what needs attention
in your positions and watchlist, new signals and the calendar.

Before any decision: `gpw-research-dossier`, then the sizer and the gate.

The full routine is in [docs/WORKFLOW.md](docs/WORKFLOW.md).

## Skills

### Beginners

| Skill | What it does |
|---|---|
| [`gpw-start`](skills/gpw-start/SKILL.md) | Checks readiness (surplus, 3–6-month reserve, debt above 8%). Gives an equity share from your horizon and loss tolerance, a plan, a glossary and scam warnings |
| [`gpw-account-planner`](skills/gpw-account-planner/SKILL.md) | Compares IKE, IKZE, OKI and a regular account over many years: 2026–2027 limits, the IKZE refund, the OKI asset tax, which account to fill first |
| [`gpw-etf-planner`](skills/gpw-etf-planner/SKILL.md) | GPW ETF catalogue with live stats, a whole-unit buying plan, rebalancing with new money, a DCA backtest |

### Market context

| Skill | What it does |
|---|---|
| [`gpw-market-breadth`](skills/gpw-market-breadth/SKILL.md) | Market health 0–100: % of stocks above moving averages, McClellan, new highs/lows, divergences |
| [`gpw-sector-trend`](skills/gpw-sector-trend/SKILL.md) | Uptrend ratio in the 14 sector indices and cyclical-vs-defensive rotation. **`--rrg` mode:** a Relative Rotation Graph with tails, quadrant transitions and an SVG chart |
| [`gpw-market-regime`](skills/gpw-market-regime/SKILL.md) | IBD-style regime: distribution days, follow-through day, a top-risk score |
| [`gpw-macro-regime`](skills/gpw-macro-regime/SKILL.md) | NBP rate cycle, real rate, PLN regime, PL–DE 10Y spread, 2s10s curve, inflation, and historical sector tilts |
| [`gpw-exposure-coach`](skills/gpw-exposure-coach/SKILL.md) | One posture and its limits: exposure ceiling, risk per trade, new positions per week, a circuit breaker |

### Ideas and research

| Skill | What it does |
|---|---|
| [`gpw-trend-screener`](skills/gpw-trend-screener/SKILL.md) | Minervini Trend Template, RS 1–99, VCP, pivot and invalidation level |
| [`gpw-quality-screener`](skills/gpw-quality-screener/SKILL.md) | Piotroski F-score with each of the 9 criteria shown (unknowns stated honestly), ROE/ROIC, accruals, red flags, a separate model for banks |
| [`gpw-valuation`](skills/gpw-valuation/SKILL.md) | Reverse DCF first, then a PLN WACC with Damodaran country risk, a sensitivity grid, comps and a bank P/BV–ROE model |
| [`gpw-dividend-analyzer`](skills/gpw-dividend-analyzer/SKILL.md) | Dividend history rebuilt from quotes and ESPI, stability, net yield after Belka vs IKE, trap flags, why dividend capture fails |
| [`gpw-ownership`](skills/gpw-ownership/SKILL.md) | KNF net short positions (history since 2012), MAR art. 19 insider notices from ESPI, OFE pension-fund holdings |
| [`gpw-earnings-reaction`](skills/gpw-earnings-reaction/SKILL.md) | Reaction to each periodic report: gap, abnormal return, abnormal turnover, post-earnings drift, a season overview |
| [`gpw-event-calendar`](skills/gpw-event-calendar/SKILL.md) | Results dates, AGMs, dividends (T+2), index revisions, MSCI, MPC, CPI, FW20 expiry, a holding-window check and `.ics` export |
| [`gpw-pair-trading`](skills/gpw-pair-trading/SKILL.md) | Engle-Granger cointegration (matches statsmodels), spread z-score, half-life, multiple-testing correction, "switch A for B" checks |
| [`gpw-research-dossier`](skills/gpw-research-dossier/SKILL.md) | One sourced file per company (100+ fact ids), then a bull-vs-bear debate where every claim must cite a fact, and a draft journal thesis |

### Decision and risk

| Skill | What it does |
|---|---|
| [`gpw-watchlist`](skills/gpw-watchlist/SKILL.md) | A staged watchlist (candidate → watch → ready → entered) fed by the other skills, with pivot, stop, breakout and event alerts |
| [`gpw-position-sizer`](skills/gpw-position-sizer/SKILL.md) | Shares to buy for a planned risk, with fees, liquidity and after-tax results per account type |
| [`gpw-pre-trade-gate`](skills/gpw-pre-trade-gate/SKILL.md) | 13 fail-closed checks plus context checks: earnings in the holding window, crowded short, insider selling, earnings reaction, quality flags, macro, stress test |
| [`gpw-portfolio-check`](skills/gpw-portfolio-check/SKILL.md) | Concentration, correlations, beta, VaR/ES, liquidity, comparison with WIG |
| [`gpw-risk-lab`](skills/gpw-risk-lab/SKILL.md) | Your portfolio through real GPW crises (2008, 2011, 2015, 2020, 2022) and factor shocks. Hedging with FW20 futures, or a short ETF for small portfolios |

### Learning and discipline

| Skill | What it does |
|---|---|
| [`gpw-trade-journal`](skills/gpw-trade-journal/SKILL.md) | Thesis journal with kill criteria, FIFO lots, results vs WIG, a paper mode |
| [`gpw-trade-review`](skills/gpw-trade-review/SKILL.md) | Statistics, a process-vs-outcome matrix, a PIT-38 estimate |
| [`gpw-signal-postmortem`](skills/gpw-signal-postmortem/SKILL.md) | Did the skills' signals work? Forward returns over 5/20/60 sessions vs WIG with confidence intervals; feedback only when the evidence is strong |
| [`gpw-backtester`](skills/gpw-backtester/SKILL.md) | Honest backtest with real fees, slippage, liquidity caps and Belka tax. Walk-forward, Deflated Sharpe and PBO, with a verdict from REJECT to ROBUST |
| [`gpw-daily-brief`](skills/gpw-daily-brief/SKILL.md) | Runs the daily or weekly pipeline and assembles one page after the close |

## How the skills connect

```
AFTER THE CLOSE (gpw-daily-brief run)
  breadth ┐
  sector  ├→ exposure-coach ─────────────────────────────┐
  regime  ┘                                              │
  trend-screener, quality, ownership, earnings ─→ watchlist ─→ daily-brief
  event-calendar ────────────────────────────────────────┘

BEFORE A DECISION
  research-dossier (every report on the company + debate)
    → position-sizer → pre-trade-gate → trade-journal

WEEKLY / MONTHLY / QUARTERLY
  macro-regime, portfolio-check, risk-lab, dividend-analyzer
  trade-review, signal-postmortem → feedback
  backtester → before you change any rule
```

Skills talk through JSON files with fixed contracts ([docs/ROADMAP_v1.2.md](docs/ROADMAP_v1.2.md)),
so each one works alone and together they form a process. Every skill emits
signals in one shared format, so `gpw-signal-postmortem` can measure which of
them actually work.

## Quick start

### Claude Code

```bash
git clone https://github.com/popiolkoks1/gpw-trading-skills.git
cp -R gpw-trading-skills/skills/* ~/.claude/skills/
```

Then ask, for example:
- *"Where do I start investing?"*
- *"Make the GPW daily brief"*
- *"Who is shorting MODIVO?"*
- *"What growth does KETY's price imply?"*
- *"Backtest the Minervini template on GPW since 2019"*

Requirements: Python 3.9+, standard library only.

The first run downloads about 2 years of GPW quotes, one request per session:
about 20–40 minutes, once. The backtester and the stress test can fetch a longer
history (shares from 2019, indices from 2007). Everything is stored in a local
cache.

### Claude web app (claude.ai)

```bash
python3 scripts/package_skills.py   # builds dist/*.skill + SHA256SUMS
```

Upload a `.skill` file in **Customize → Skills** with code execution enabled.
Data skills need network access from the sandbox.

## Principles

1. **Scripts compute, Claude interprets.** Every number comes from tested code (1,600+ tests).
2. **Freshness is a gate.** Every report has its date and its sources.
3. **Honesty over effect.** Missing data is marked unknown, never guessed. The backtester rejects strategies that only look good on paper. The post-mortem gives feedback only when the evidence is strong.
4. **Survivorship-free.** The stock universe is rebuilt from every session. Prices are adjusted for dividends and splits, and renamed tickers are stitched together (e.g. CCC → MODIVO).
5. **Polish realities first:** PLN, whole shares, minimum fees, Belka tax, IKE/IKZE/OKI, ESPI, KNF, T+2 settlement.
6. **Law from statutes, not memory**, with article numbers (e.g. the reporting regulation Dz.U. 2025 poz. 755, the OKI act Dz.U. 2026 poz. 1098, MAR art. 19).
7. **Human decision gates.** No orders, no "buy" calls.
8. **Polite data access:** an identifying client, rate limits and caching. We never circumvent site protections.

Details: [docs/CONVENTIONS.md](docs/CONVENTIONS.md) ·
[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) · [docs/WORKFLOW.md](docs/WORKFLOW.md).

## Development

```bash
python3 -m pip install pytest
python3 scripts/sync_shared.py          # vendor shared/gpwlib into skills
python3 -m pytest tests                 # tests, no network
python3 scripts/package_skills.py       # validate + build .skill archives
```

`shared/gpwlib` is the canonical shared library. Each skill ships a vendored
copy so it can be installed on its own, and CI fails if a copy drifts.

Each skill has `evals/evals.json` with should-trigger and should-not-trigger
prompts (PL and EN) and output assertions.

## Data & licences

Code: MIT (see [LICENSE](LICENSE)), derived from tradermonty/claude-trading-skills
(MIT). Market data is **not** included. It is downloaded on your machine for
personal analysis. Redistributing GPW or Notoria data may require a licence.

## Disclaimer

This project is for education and research only. It is not investment, tax or
legal advice, nor an investment recommendation within the meaning of MAR
(Regulation (EU) 596/2014). Tax calculations are estimates: your broker's
PIT-8C and a tax advisor are authoritative. Investing involves the risk of losing
capital. Software is provided "as is", without warranty.
