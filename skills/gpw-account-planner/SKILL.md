---
name: gpw-account-planner
description: Compares where a beginner's monthly savings should go in Poland - IKE, IKZE, OKI (osobiste konto inwestycyjne, from 2027) or a regular brokerage account. Simulates each account month by month for 3/6/9% return scenarios with fund costs (TER), broker fees, the 19% Belka tax, the IKZE refund and 10% exit tax, the OKI yearly asset tax and its exemption for Polish assets, yearly limits and the age conditions (60 / 65); finds the fill order with the highest computed net result and shows inflation-adjusted values in plain Polish. No market data needed. Use when the user asks which account to save in. Triggers (PL) "IKE czy IKZE", "czy warto OKI", "gdzie odkładać pieniądze", "ile będę mieć za 25 lat", "zwrot podatku z IKZE", "limit IKE 2027", "jak podzielić 1000 zł miesięcznie"; (EN) "IKE vs IKZE", "Polish retirement accounts", "is OKI worth it", "where to invest monthly savings in Poland". Not for picking ETFs (use gpw-etf-planner) or filing PIT-38.
license: MIT
metadata:
  version: "1.0.0"
  languages: "pl,en"
---

# GPW Account Planner

Pure calculator for **beginners**: "I can save X zł a month — should it go to IKE,
IKZE, OKI or a normal brokerage account?" The script simulates every account with
Polish tax rules and prints a plain-language answer. Scripts compute; you explain.
The user is assumed to know nothing about investing.

## When to use

- The user asks IKE vs IKZE vs OKI vs a regular account, or "where to put my savings".
- The user wants to know how much they will have after N years, after tax.
- The user asks whether the IKZE refund / OKI is worth it, or about 2027 limits.

Do **not** use for: choosing specific ETFs or how many units to buy (`gpw-etf-planner`),
filing PIT-38 for past trades (`gpw-trade-review`), single-stock trades
(`gpw-position-sizer`). Never present the result as personal advice.

## Language

Answer in the user's language: `--lang pl` (default) or `--lang en`.

## Workflow

### 1. Collect the inputs

Ask only for what is missing; the defaults are documented in `references/methodology.md`.

| Input | Flag | Default / note |
|---|---|---|
| Monthly or yearly amount | `--monthly 1000` or `--yearly 12000` | required (one of them) |
| Horizon in years | `--years 25` | required |
| Current age | `--age 30` | required — decides the IKE (60) / IKZE (65) conditions |
| Start year | `--start-year` | 2027 (2026 limits also known; OKI exists from 2027) |
| Return scenarios | `--returns 3,6,9` | nominal % a year |
| Inflation | `--inflation 3.0` | for "today's money" values |
| Product cost | `--ter 0.5` | yearly % (check the fund's KID) |
| Broker | `--broker xtb\|mbank\|bossa\|pkobp\|santander\|custom` | custom = 0%; override `--commission-pct`, `--commission-min` |
| PIT rate | `--pit scale12\|scale32\|linear\|none` | scale12; decides the IKZE refund |
| Self-employed | `--self-employed` | higher IKZE limit |
| OKI qualified share | `--oki-qualified-pct 0..100` | 0 = global ETFs (taxed from the first zloty); 100 = only Polish shares/PLN bonds ETFs |
| OKI rate from 2028 | `--oki-rate-later 0.85` or `--nbp-rate 5.0` | unknown in advance; NBP rate → 19% of it per art. 25 |
| IKZE refund | `--no-reinvest-refund` | default: reinvested in the regular account |
| Limit growth | `--wage-growth 5` | % a year after the known 2026–2027 limits |

Tip: if the user already planned an ETF portfolio with `gpw-etf-planner`, use the
"share likely qualified for OKI" it printed as `--oki-qualified-pct`.

### 2. Run the script

From this skill's directory:

```bash
python3 scripts/gpw_account_planner.py --monthly 1000 --years 25 --age 30 --pit scale12 --lang pl
python3 scripts/gpw_account_planner.py --monthly 2500 --years 30 --age 35 --pit scale32 --oki-qualified-pct 30 --broker mbank --lang en
python3 scripts/gpw_account_planner.py --yearly 20000 --years 20 --age 45 --pit linear --self-employed --nbp-rate 4.0
```

It writes `gpw_account_plan_<timestamp>.json` and `.md` to `--output-dir` (default `.`)
and prints the Markdown; `--no-files` only prints. It runs in about a second.
Exit codes: 0 ok, 1 input error (stderr), 2 bad flags.

### 3. Present the result

1. **Lead with the short answer** from the report in 2–3 plain sentences: which fill
   order gives the most money after tax in the middle scenario, how much that is in
   today's money, and the difference vs a regular account. Say it is a calculation, not advice.
2. **If the age at the end is below 60/65**, explain the "hold to 65" section: IKE/IKZE
   only pay off fully if the money stays until 60/65; show both numbers.
3. **Explain every term the first time** (IKE, IKZE, OKI, ETF, TER, podatek Belki,
   procent składany, wartość realna) — use the report's glossary, in simple words.
4. Show the comparison table for the middle scenario; mention the range (3% vs 9%)
   in one sentence rather than reading out every table.
5. Show the monthly split of the computed combination (e.g. 1003 zł IKZE, rest IKE).
6. Repeat the warnings (age conditions, OKI with global ETFs, unknown OKI rate,
   no PIT income → no IKZE refund).
7. End with the caveats: laws and limits change yearly, IKZE is taxed at withdrawal,
   the OKI rate depends on the NBP rate; and point to `gpw-etf-planner` for what to buy.

Never say "open an IKZE" as an instruction; say "in this calculation IKZE first gives
the highest result because…".

## Key rules (built into the script)

- **IKE:** tax-free after 60 with contributions in ≥ 5 calendar years (art. 34);
  otherwise taxed like a regular account. Limit = 3 × forecast average wage
  (30 099 zł in 2027).
- **IKZE:** refund = contribution × marginal PIT rate each year (April next year);
  10% of the whole balance after 65 (≥ 5 years); early return = whole amount at
  the PIT scale (12% / 32% above 120 000 zł). Limit 12 039,60 zł in 2027
  (18 059,40 zł self-employed).
- **OKI (from 2027):** no tax on gains; yearly tax on the average value above the
  exemptions (100 000 zł qualified + 25 000 zł savings, max 100 000 zł total);
  0.85% in 2027, later 19% of the NBP reference rate (min 0.1%). Global ETFs
  are not qualified — taxed from the first zloty.
- **Regular account:** 19% of the gain at the sale; selling earlier = tax earlier.
- **Waterfall:** all 16 fill orders of IKE/IKZE/OKI (+ regular overflow) are
  simulated; the best in the middle scenario is reported, and a different winner in
  other scenarios is flagged.

Read `references/methodology.md` when the user asks why, how the OKI tax or IKZE
refund is computed, or where the default assumptions come from.

## Related skills

- `gpw-etf-planner` — which GPW ETFs, how many units a month, fees, risk; prints the OKI-qualified share for this skill.
- `gpw-position-sizer` — after-tax view for a single trade per account type.
- `gpw-trade-review` — PIT-38 / Belka tax for trades already closed.
- `gpw-trade-journal` — record purchases and the account type (regular / IKE / IKZE / OKI).
