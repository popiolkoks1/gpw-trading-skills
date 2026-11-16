---
name: gpw-start
description: Starting point for people new to investing in Poland. It checks whether the user is ready to invest (monthly surplus, emergency fund of 3-6 months, expensive debt above 8%), maps the horizon and the loss they can tolerate to an indicative share of equities, and gives an ordered, plain-Polish action plan (budget, reserve, debt, IKE/IKZE/OKI, broad ETFs, stocks only as a small satellite), with a glossary, a learning path and scam warnings. Use it whenever someone says they know nothing about investing, asks how or where to start, whether they should invest at all, how much to put into stocks, or what IKE/ETF/a share is. Triggers (PL) "od czego zacząć inwestowanie", "nie znam się na inwestowaniu", "czy powinienem inwestować", "jak zacząć na giełdzie", "mam oszczędności, co z nimi zrobić", "ile w akcje", "co to jest ETF"; (EN) "how do I start investing", "I'm a beginner", "should I invest", "where do I start". Not for picking stocks or timing the market.
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
---

# GPW Start

The front door of the GPW skill set, for people who "don't know anything about investing".
It answers three questions, in this order: **Am I ready? How much risk suits me? What do I
do first?** The script computes; you explain in plain language, like a patient friend who
knows finance, not like a salesperson.

## When to use

- The user says they are a beginner, asks how or where to start, or whether to invest at all.
- The user has savings and asks what to do with them, or asks how much to put into shares.
- The user asks what a share, ETF, IKE, IKZE, OKI or the Belka tax is: use `references/glossary_pl.md`.
- The user asks how to learn or what mistakes to avoid: use `references/learning_path.md`.

Do **not** use it to pick stocks, ETFs or the moment to buy. It never names a security to buy.

## Language

Answer in the user's language. Pass `--lang pl` (default) or `--lang en`. Use simple words.
Explain every term the first time it appears, using the glossary.

## Workflow

### 1. Ask a few questions (conversationally, at most 2-3 at a time)

| Input | Flag | Note |
|---|---|---|
| Net monthly income | `--income` | required |
| Monthly expenses | `--expenses` | required; an estimate is fine |
| Safe savings today | `--savings` | cash, deposits, savings bonds |
| Consumer debts | `--debt NAME:AMOUNT:RATE` | repeatable, e.g. `--debt karta:5000:21.5`; skip a low-rate mortgage |
| When the money is needed | `--horizon` | years |
| Age | `--age` | enables the IKE/IKZE age warning |
| Largest fall of the whole portfolio they could sit through | `--tolerance` | in %; ask: "Gdyby 10 000 zł zamieniło się w 7 000 zł po roku, sprzedał(a)byś?" |
| Irregular income (B2B, single income, commission) | `--unstable-income` | 6-month reserve instead of 3 |
| Experience | `--experience none\|basic\|some` | default none |

If the user does not want to share numbers, explain the stages in general terms and offer to
run the check later. Never guess their numbers.

### 2. Run the script

From this skill's directory:

```bash
python3 scripts/start_check.py --income 6500 --expenses 4800 --savings 9000 \
  --debt karta:4000:19.9 --horizon 25 --age 29 --tolerance 30 --lang pl
```

It prints Markdown and writes `gpw_start_<timestamp>.json` and `.md` to `--output-dir`
(`--no-files` to skip). Exit code `1` with a message on stderr means invalid inputs.

### 3. Present the result

1. **Start with the stage, in one sentence**, e.g. "Na razie nie inwestuj jeszcze w akcje: najpierw dokończ poduszkę finansową, brakuje Ci 5 400 zł, czyli ok. 4 miesiące odkładania."
2. **Why, in two or three sentences.** Link it to their situation: a job loss during a crash, or a credit card at 20% vs an uncertain 6%.
3. **The plan**, as numbered steps from the report. Keep the order: the order is the point.
4. **The equity range**, if they are at stage 3 or close to it. Explain the "×55%" reasoning in one sentence. It is a range for their own decision, not a recommendation.
5. **Warnings** from the report. Always keep the scam warning and the leveraged-ETF warning for users with no experience.
6. **The next tool**: `gpw-account-planner` for where to hold investments, then `gpw-etf-planner`.

Reassure, don't lecture. Being at stage 1 is normal and sensible, not a failure.

## Key rules (built into the script)

- **Stages never get skipped:** budget, emergency fund, expensive debt, then investing.
- **A horizon under 3 years means no shares.** 3-7 years caps equities at 50%.
- **Equity upper bound = tolerance / 55%,** since broad markets have fallen ~50-60% in crashes. The lower bound is half of that.
- **Single stocks are at most ~10%** of investments for learners, with a journal and paper trading first.
- All thresholds are justified in `references/readiness_method.md`.

## Related skills

- `gpw-account-planner`: IKE vs IKZE vs OKI vs a regular account, over the user's horizon.
- `gpw-etf-planner`: a regular contribution plan into ETFs listed on GPW, with commissions.
- `gpw-portfolio-check`: a yearly portfolio review.
- `gpw-trade-journal` (with `--paper`) and `gpw-position-sizer`: practise stock picking without real money.
