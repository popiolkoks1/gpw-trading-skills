# GPW Trading Skills

[![CI](https://github.com/popiolkoks1/gpw-trading-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/popiolkoks1/gpw-trading-skills/actions/workflows/ci.yml)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![skills](https://img.shields.io/badge/skills-27-green)
![tests](https://img.shields.io/badge/tests-1671-green)
![license](https://img.shields.io/badge/license-MIT-lightgrey)

**27 Claude Skills for the Polish stock market (GPW, Warsaw Stock Exchange).**

They cover two kinds of investor:

- **Beginners:** whether to invest at all, IKE/IKZE/OKI accounts, ETFs.
- **Professionals:** a full process from market regime to screener, company dossier, position size, pre-trade gate, journal and review.

They use free, official data with no API keys, in Polish and English.

🇵🇱 [Polski → README.pl.md](README.pl.md) · 📖 [Daily routine → WORKFLOW](docs/WORKFLOW.md)

> ⚠️ Education, not investment advice. The skills never place orders and never say "buy X".

## Install (Claude Code)

```bash
git clone https://github.com/popiolkoks1/gpw-trading-skills.git
cp -R gpw-trading-skills/skills/* ~/.claude/skills/
```

You need Python 3.9+ and nothing else.

The first run downloads about 2 years of GPW quotes. This takes 20–40 minutes, once. After that, runs take seconds from the local cache.

For claude.ai: run `python3 scripts/package_skills.py`, then upload `dist/*.skill` in **Customize → Skills**.

## Just ask

| Question | Skill |
|---|---|
| "I know nothing about investing, where do I start?" | `gpw-start` |
| "IKE or IKZE? Is OKI worth it?" | `gpw-account-planner` |
| "Which GPW ETFs, and how many units for 1,000 zł?" | `gpw-etf-planner` |
| "What happened on GPW today? What needs my attention?" | `gpw-daily-brief` |
| "Is the market in a correction? How much should I hold in stocks?" | `gpw-market-regime`, `gpw-exposure-coach` |
| "Which stocks are leaders or near a breakout?" | `gpw-trend-screener` |
| "Collect everything on KETY: the case for and against" | `gpw-research-dossier` |
| "What growth does PKO's price imply? Is CD Projekt expensive?" | `gpw-valuation` |
| "Who is shorting MODIVO? Are managers buying shares?" | `gpw-ownership` |
| "How many shares at 1% risk? Can I enter?" | `gpw-position-sizer`, `gpw-pre-trade-gate` |
| "How much would I lose if 2008 happened again?" | `gpw-risk-lab` |
| "Did this strategy really work on GPW?" | `gpw-backtester` |

## 27 skills in 6 groups

| | Skill | What you get | Data |
|---|---|---|---|
| 🌱 **Start** | [`gpw-start`](skills/gpw-start/SKILL.md) | Readiness check (reserve, debt), share of stocks, a step-by-step plan, glossary | local |
| | [`gpw-account-planner`](skills/gpw-account-planner/SKILL.md) | IKE / IKZE / OKI / regular account over your horizon, 2026–27 limits, which account to fill first | statutes |
| | [`gpw-etf-planner`](skills/gpw-etf-planner/SKILL.md) | GPW ETF catalogue, a whole-unit buying plan with fees, rebalancing | GPW |
| 🧭 **Market** | [`gpw-market-breadth`](skills/gpw-market-breadth/SKILL.md) | Market health 0–100: how many stocks rise, sit above averages, make new highs | GPW |
| | [`gpw-sector-trend`](skills/gpw-sector-trend/SKILL.md) | Strong and weak sectors; `--rrg` rotation chart (leading → weakening → lagging) | GPW |
| | [`gpw-market-regime`](skills/gpw-market-regime/SKILL.md) | Uptrend / under pressure / correction / bear market, distribution days, top risk | GPW |
| | [`gpw-macro-regime`](skills/gpw-macro-regime/SKILL.md) | NBP rates, inflation, zloty, PL–DE spread: when macro helps or hurts | NBP, ECB, Eurostat |
| | [`gpw-exposure-coach`](skills/gpw-exposure-coach/SKILL.md) | One posture: stock exposure, risk per trade, a circuit breaker after losses | reports |
| 🔍 **Stocks** | [`gpw-trend-screener`](skills/gpw-trend-screener/SKILL.md) | Minervini leaders, relative strength, VCP setups, pivot and stop | GPW |
| | [`gpw-quality-screener`](skills/gpw-quality-screener/SKILL.md) | Piotroski F-score, ROE, red flags in the accounts; banks scored separately | gpw.pl |
| | [`gpw-valuation`](skills/gpw-valuation/SKILL.md) | Growth the price implies (reverse DCF), value range, sector comps | gpw.pl, BondSpot |
| | [`gpw-dividend-analyzer`](skills/gpw-dividend-analyzer/SKILL.md) | Dividend history and stability, net yield after tax vs IKE, traps | GPW, ESPI |
| | [`gpw-ownership`](skills/gpw-ownership/SKILL.md) | Short positions (KNF register since 2012), insider deals, pension-fund holdings | KNF, ESPI |
| | [`gpw-earnings-reaction`](skills/gpw-earnings-reaction/SKILL.md) | How the market took results: gap, abnormal return, post-report drift | ESPI, GPW |
| | [`gpw-event-calendar`](skills/gpw-event-calendar/SKILL.md) | Results, AGMs, dividends, index revisions, MPC, CPI; events inside your holding period | ESPI, NBP |
| | [`gpw-pair-trading`](skills/gpw-pair-trading/SKILL.md) | Do two stocks move together (cointegration)? "PKO or Pekao?" | GPW |
| | [`gpw-research-dossier`](skills/gpw-research-dossier/SKILL.md) | Everything on one company in one file, plus a fact-based bull-vs-bear debate | reports |
| 🎯 **Decision** | [`gpw-watchlist`](skills/gpw-watchlist/SKILL.md) | Watched stocks with stages and alerts (pivot, stop, breakout, events) | reports |
| | [`gpw-position-sizer`](skills/gpw-position-sizer/SKILL.md) | How many shares to buy for a planned risk, with fees and tax | local |
| | [`gpw-pre-trade-gate`](skills/gpw-pre-trade-gate/SKILL.md) | 20 checks before buying → GO / GO WITH CONDITIONS / NO-GO | reports |
| 🛡️ **Risk** | [`gpw-portfolio-check`](skills/gpw-portfolio-check/SKILL.md) | Portfolio concentration, correlations, beta, VaR, liquidity | GPW |
| | [`gpw-risk-lab`](skills/gpw-risk-lab/SKILL.md) | Your portfolio in the 2008–2022 crises and shocks; FW20 futures hedge | GPW, NBP |
| 📈 **Learn** | [`gpw-trade-journal`](skills/gpw-trade-journal/SKILL.md) | Thesis journal with kill criteria; paper mode with no money at risk | local |
| | [`gpw-trade-review`](skills/gpw-trade-review/SKILL.md) | Results, process vs luck, PIT-38 estimate | local |
| | [`gpw-signal-postmortem`](skills/gpw-signal-postmortem/SKILL.md) | Did the skills' signals really beat WIG (after 5/20/60 sessions)? | GPW |
| | [`gpw-backtester`](skills/gpw-backtester/SKILL.md) | Honest strategy test: fees, slippage, tax, overfitting checks | GPW |
| | [`gpw-daily-brief`](skills/gpw-daily-brief/SKILL.md) | Runs the whole process after the close and gives you one page | reports |

"Reports" means the skill reads other skills' outputs (JSON files).

## How it connects

```
AFTER CLOSE   breadth + sector + regime → exposure-coach
              screener + quality + ownership + earnings → watchlist ┐
              event-calendar ───────────────────────────────────────┴→ daily-brief (1 page)

DECISION      research-dossier → position-sizer → pre-trade-gate → trade-journal

PERIODIC      weekly: macro, portfolio-check, risk-lab · monthly: trade-review, signal-postmortem
              before changing any rule: backtester
```

## What makes it different

- **Polish realities:** PLN, whole shares, minimum fees, Belka tax with FIFO, IKE/IKZE/OKI, ESPI, KNF, T+2 settlement.
- **Numbers come from code, not model memory.** Scripts compute and Claude explains. There are 1,671 tests on Linux, macOS and Windows.
- **Honesty:** missing data is "unknown", never a guess.
  - The backtester rejected the Minervini strategy (−63% along the way).
  - The pair scanner found no cointegrated pair on GPW, and says so.
- **Survivorship-free:** delisted stocks stay in the data, prices are adjusted for dividends and splits, and renamed tickers are stitched together (CCC → MODIVO).
- **Law from statutes**, with article numbers: OKI (Dz.U. 2026 poz. 1098), periodic reports (Dz.U. 2025 poz. 755), MAR art. 19.
- **Every report states its data date and sources**, and stale data is flagged at the top.

## Data sources (free, official)

- GPW quotes archive (shares, indices, ETFs, futures)
- ESPI on gpw.pl
- GPW Benchmark
- KNF short-position register
- NBP API
- BondSpot
- ECB
- Bundesbank
- Eurostat
- Damodaran (country risk premium)

Downloads are polite: the client identifies itself, requests are rate-limited and cached, and site protections are never bypassed. Details: [DATA_SOURCES](docs/DATA_SOURCES.md).

## Limits (stated plainly)

| Area | Limit |
|---|---|
| Insiders | Volumes and prices are in PDFs that block scripts, so you paste their text yourself |
| Fundamentals | gpw.pl shows only the latest period; year-over-year comparisons build up over time or from an ESEF report |
| Short positions | Only positions of at least 0.5% of capital are public |
| Valuation | Net debt is estimated; you can supply your own figure |
| Indices | Uses today's index membership, not historical membership |

## Development

```bash
python3 -m pip install pytest
python3 scripts/sync_shared.py      # copy shared/gpwlib into every skill
python3 -m pytest tests             # tests, no network
python3 scripts/package_skills.py   # validation and .skill packages
```

Rules: [CONVENTIONS](docs/CONVENTIONS.md) · JSON contracts and decisions: [ROADMAP v1.2](docs/ROADMAP_v1.2.md) · changes: [CHANGELOG](CHANGELOG.md).

## Licence and disclaimer

The code is under the MIT licence, based on [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills). The repository contains no market data. You download it yourself for personal analysis, and redistributing it may require a GPW licence.

This project is for education and research. It is not investment, tax or legal advice, nor a recommendation under MAR (EU 596/2014). Tax figures are estimates: your PIT-8C and a tax adviser are authoritative. Investing carries the risk of losing capital.
