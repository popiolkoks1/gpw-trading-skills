# GPW Trading Skills

**13 Claude Skills for the Polish stock market (GPW, Warsaw Stock Exchange): from
first steps for people who know nothing about investing (readiness, IKE/IKZE/OKI,
an ETF contribution plan) to advanced tools (Minervini/VCP trend screener, market
regime, exposure limits, a pre-trade gate, a journal and a PIT-38 helper).**

🇵🇱 [Wersja polska → README.pl.md](README.pl.md)

> Educational tools, not investment advice. The skills never place orders and never
> tell you what to buy. They give you data, risk numbers and a structured process
> so that *you* make better decisions. See [Disclaimer](#disclaimer).

## Why

Most AI trading toolkits target US markets and paid data APIs. Polish investors
face different realities: thin liquidity outside WIG20/mWIG40, minimum broker
fees in PLN, whole-share lots, the 19% "Belka" tax with FIFO on PIT-38,
IKE/IKZE and (from 2027) OKI accounts. This set follows the patterns of the best
open-source trading-skill repos ([tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills),
Anthropic financial-services plugins, himself65/finance-skills, TradingAgents,
FinRobot) and rebuilds them for those realities, using **only free, official data**
(GPW quotes archive for shares, indices and ETFs, GPW Benchmark, statute texts)
and **no API keys**.

## Where to start (if you are new to investing)

1. **`gpw-start`** – are you ready (budget, emergency fund, expensive debt), what share of equities fits your loss tolerance, a step-by-step plan, a glossary.
2. **`gpw-account-planner`** – IKE vs IKZE vs OKI vs a regular account over your horizon.
3. **`gpw-etf-planner`** – GPW-listed ETFs with real data and a monthly whole-unit buying plan with commissions.
4. **`gpw-portfolio-check`** – a yearly review: concentration, correlation, risk, in plain words.
5. Want to learn stock picking? **`gpw-trade-journal --paper`** – paper trades, no real money.

## Skills

### Beginners

| Skill | What it does | Data |
|---|---|---|
| [`gpw-start`](skills/gpw-start/SKILL.md) | readiness check (surplus, 3–6-month reserve, debt > 8%), indicative equity share from horizon and loss tolerance, ordered plan, glossary, learning path, scam warnings | local |
| [`gpw-account-planner`](skills/gpw-account-planner/SKILL.md) | IKE / IKZE / OKI / regular account compared over many years: 2026–2027 limits, IKZE refund and 10% withdrawal tax, OKI asset tax (art. 25–26 of the act), age conditions, fill order, real values | local (law in code) |
| [`gpw-etf-planner`](skills/gpw-etf-planner/SKILL.md) | catalog of GPW-listed ETFs (category, currency, leveraged/short warnings, OKI hint) with archive stats; monthly whole-unit buy plan with commissions and cash-flow rebalancing; DCA vs lump-sum test | GPW archive (ETF) |

### Everyone

| Skill | What it does | Data |
|---|---|---|
| [`gpw-portfolio-check`](skills/gpw-portfolio-check/SKILL.md) | portfolio review from a broker CSV or the journal: weights, sectors, HHI, correlation, volatility, beta, VaR/ES, drawdown, liquidity (days to exit), vs WIG, review flags | GPW archive, GPW Benchmark |
| [`gpw-market-breadth`](skills/gpw-market-breadth/SKILL.md) | 0-100 health score of the whole GPW: % above SMA 20/50/200, advancers/decliners, McClellan, new highs/lows, WIG20 vs mWIG40 vs sWIG80, WIG-vs-breadth divergence | GPW archive, GPW Benchmark |
| [`gpw-sector-trend`](skills/gpw-sector-trend/SKILL.md) | share of stocks in a confirmed uptrend overall and in 14 sector indices, cyclical vs defensive rotation | GPW archive, GPW Benchmark |
| [`gpw-trade-journal`](skills/gpw-trade-journal/SKILL.md) | investment-thesis journal with kill criteria, lifecycle, FIFO lots, result vs WIG; **paper mode (`--paper`)** | local JSON |
| [`gpw-trade-review`](skills/gpw-trade-review/SKILL.md) | monthly/annual review: win rate, expectancy, R, process-vs-outcome matrix, lessons; **PIT-38 estimate** | local JSON |

### Advanced

| Skill | What it does | Data |
|---|---|---|
| [`gpw-trend-screener`](skills/gpw-trend-screener/SKILL.md) | whole-market screen: Minervini Trend Template (8 criteria), IBD-style RS 1–99, VCP detection, pivot, execution states, invalidation level and a ready position-sizer command; history mode (forward 20/60-session results vs WIG) | GPW archive |
| [`gpw-market-regime`](skills/gpw-market-regime/SKILL.md) | regime of WIG, WIG20, mWIG40, sWIG80: IBD distribution days, O'Neil follow-through day, trend, drawdown, large-vs-small divergence, 0-100 top-risk score | GPW archive (indices) |
| [`gpw-exposure-coach`](skills/gpw-exposure-coach/SKILL.md) | combines breadth, sectors and regime into one posture with an exposure ceiling, risk per trade and a losing-streak circuit breaker (from the journal) | other skills' JSON + journal |
| [`gpw-position-sizer`](skills/gpw-position-sizer/SKILL.md) | how many shares to buy for a given risk: fixed-fractional, ATR, half-Kelly; round-trip commissions with minimum fees, liquidity cap, gap risk, after-tax profit for regular / IKE / IKZE / OKI | local + optional GPW archive |
| [`gpw-pre-trade-gate`](skills/gpw-pre-trade-gate/SKILL.md) | fail-closed checklist before a buy: stop, risk incl. fees, size, liquidity, portfolio heat, sector concentration, written thesis with kill criteria, R:R, circuit breaker, regime → GO / GO WITH CONDITIONS / NO-GO | GPW archive + journal + JSON |

### How the skills fit together

```
gpw-start → gpw-account-planner → gpw-etf-planner → gpw-portfolio-check      (long-term investor)

gpw-market-breadth ┐
gpw-sector-trend   ├→ gpw-exposure-coach ┐
gpw-market-regime  ┘                      ├→ gpw-pre-trade-gate → gpw-trade-journal → gpw-trade-review
gpw-trend-screener → gpw-position-sizer ──┘                                         (active trader)
```

Every skill is bilingual (Polish / English): Claude answers in your language and
the scripts write reports with `--lang pl` or `--lang en`.

## Quick start

### Claude Code (CLI / desktop)

```bash
git clone https://github.com/popiolkoks1/gpw-trading-skills.git
cp -R gpw-trading-skills/skills/* ~/.claude/skills/
```

Then just ask: *"I'm new to investing, where do I start?"*, *"IKE or IKZE?"*,
*"How healthy is the Polish market?"* or *"Jak wygląda rynek na GPW?"*.

Requirements: Python 3.9+ (standard library only). The first breadth/sector
run downloads ~2 years of sessions from gpw.pl (one polite request per session,
20-40 min, one-off). Later runs take seconds.

### Claude web app (claude.ai)

```bash
python3 scripts/package_skills.py   # builds dist/*.skill + SHA256SUMS
```

Upload a `.skill` file under **Customize → Skills** (code execution must be
enabled). Note: skills that download GPW data need network access from the
code-execution sandbox; `gpw-start`, `gpw-account-planner`, the sizer, journal
and review work offline.

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
7. **Law from statutes, not memory.** IKE/IKZE limits and OKI rules live in one
   module (`gpwlib/tax.py`) with article numbers; OKI was checked against the
   text of Dz.U. 2026 poz. 1098. The commission table is dated and warns when old.
8. **Skills talk through JSON files** with a fixed contract, so each works alone
   and together they form a process.

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
