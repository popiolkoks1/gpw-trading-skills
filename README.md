# GPW Trading Skills

**Claude Skills for the Polish stock market (GPW, Warsaw Stock Exchange): market health,
sector trends, risk-based position sizing in PLN, an investment journal and a trade
review with a PIT-38 helper.**

🇵🇱 [Wersja polska → README.pl.md](README.pl.md)

> Educational tools, not investment advice. The skills never place orders and never
> tell you what to buy. They give you data, risk numbers and a structured process
> so that *you* make better decisions. See [Disclaimer](#disclaimer).

## Why

Most AI trading toolkits target US markets and paid data APIs. Polish investors
face different realities: thin liquidity outside WIG20/mWIG40, minimum broker
fees in PLN, whole-share lots, the 19% "Belka" tax with FIFO on PIT-38,
IKE/IKZE and (from 2027) OKI accounts. This set rebuilds five proven skills from
[tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills)
for those realities, using **only free, official data** (GPW quotes archive,
GPW Benchmark, NBP) and **no API keys**.

## Skills

| Skill | What it does | Data |
|---|---|---|
| [`gpw-market-breadth`](skills/gpw-market-breadth/SKILL.md) | 0-100 health score of the whole GPW: % of stocks above SMA 20/50/200, advancers/decliners, McClellan, new highs/lows, WIG20 vs mWIG40 vs sWIG80 participation, WIG-vs-breadth divergence | GPW archive, GPW Benchmark |
| [`gpw-sector-trend`](skills/gpw-sector-trend/SKILL.md) | share of stocks in a confirmed uptrend overall and in 14 GPW sector indices, cyclical vs defensive rotation, sector index vs WIG | GPW archive, GPW Benchmark |
| [`gpw-position-sizer`](skills/gpw-position-sizer/SKILL.md) | how many shares to buy for a given risk: fixed-fractional, ATR, half-Kelly; round-trip commissions with broker minimum fees, liquidity cap vs daily turnover, gap risk, after-tax profit for regular / IKE / IKZE / OKI | local + optional GPW archive |
| [`gpw-trade-journal`](skills/gpw-trade-journal/SKILL.md) | investment-thesis journal: pillars, falsifiable kill criteria, catalysts, lifecycle IDEA → ACTIVE → CLOSED, fills with FIFO lots, result vs WIG | local JSON |
| [`gpw-trade-review`](skills/gpw-trade-review/SKILL.md) | monthly/annual review: win rate, expectancy, R-multiples, process-vs-outcome matrix, lessons; **PIT-38 estimate** (revenue, costs, income, 19% tax) | local JSON |

Every skill is bilingual (Polish / English): Claude answers in your language and
the scripts write reports with `--lang pl` or `--lang en`.

## Quick start

### Claude Code (CLI / desktop)

```bash
git clone https://github.com/popiolkoks1/gpw-trading-skills.git
cp -R gpw-trading-skills/skills/* ~/.claude/skills/
```

Then just ask: *"How healthy is the Polish market?"* or *"Jak wygląda rynek na GPW?"*.

Requirements: Python 3.9+ (standard library only). The first breadth/sector
run downloads ~2 years of sessions from gpw.pl (one polite request per session,
20-40 min, one-off). Later runs take seconds.

### Claude web app (claude.ai)

```bash
python3 scripts/package_skills.py   # builds dist/*.skill + SHA256SUMS
```

Upload a `.skill` file under **Customize → Skills** (code execution must be
enabled). Note: skills that download GPW data need network access from the
code-execution sandbox; the sizer, journal and review work offline.

## Design principles

Borrowed from the best open-source finance skills (Anthropic financial-services
plugins, TradingAgents, FinRobot, tradermonty) and Anthropic's skill-authoring
guidance:

1. **Scripts compute, Claude interprets.** Every number comes from deterministic,
   tested Python; Claude explains it and never invents prices or tax figures.
2. **Freshness is a gate.** Each JSON report has a `data_provenance` block (as-of
   date, sessions behind, sources); stale data is flagged first.
3. **Source and date on every figure.** Reports end with a *Sources* section.
4. **Survivorship-free breadth.** The universe is rebuilt from each day's archive,
   so delisted companies count while they traded. Prices are adjusted for
   dividends and splits using GPW's own reference-price changes.
5. **Polish realities built in**, not bolted on.
6. **Human decision gates.** No orders, no "buy" calls; risk and plan first.

Details: [docs/CONVENTIONS.md](docs/CONVENTIONS.md) ·
[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).

## Development

```bash
python3 -m pip install pytest
python3 scripts/sync_shared.py          # vendor shared/gpwlib into skills
python3 -m pytest tests                 # unit tests, no network
python3 scripts/package_skills.py       # validate + build .skill archives
```

`shared/gpwlib` is the canonical shared library; each skill ships a vendored
copy so it can be installed on its own. CI fails if a copy drifts.

Each skill has `evals/evals.json` with should-trigger / should-not-trigger
prompts (PL + EN) and output assertions, following Anthropic's skill-creator
evaluation approach.

## Data & licences

Code: MIT (see [LICENSE](LICENSE)); derived from tradermonty/claude-trading-skills
(MIT). Market data is **not** included: it is downloaded on your machine from
GPW for personal analysis. Redistributing GPW data may require a GPW licence.

## Disclaimer

This project is for education and research only. It is not investment, tax or
legal advice, nor an investment recommendation within the meaning of MAR
(Regulation (EU) 596/2014). Tax calculations are estimates — your broker's
PIT-8C and a tax advisor are authoritative. Investing involves the risk of losing
capital. Software is provided "as is", without warranty.
