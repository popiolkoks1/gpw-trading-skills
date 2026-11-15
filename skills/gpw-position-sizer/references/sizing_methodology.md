# Position sizing methodology (GPW)

Contents: 1. Fixed fractional · 2. ATR stop · 3. Half-Kelly · 4. Commissions ·
5. Constraints · 6. Gap risk · 7. Taxes by account type · 8. Thresholds · 9. Sources

## 1. Fixed fractional (default)

```
risk budget  = account × risk%                          (default 1%)
risk(n)      = n × (entry − stop) + fee(n × entry) + fee(n × stop)
shares       = max n ∈ ℕ such that risk(n) ≤ risk budget
```

`risk(n)` is monotonic, so a binary search gives the exact answer. The fees are part
of the risk because they are a guaranteed cost of the losing scenario. Ignoring them
makes small accounts take more risk than planned.

**Why 1%:** at 1% risk, ten losses in a row cost about 9.6% of the account, which
can be recovered. At 5% the same streak costs about 40%. 0.5–1% is the common
guideline for beginners, and anything above 2% triggers a warning.

## 2. ATR stop

`stop = entry − k × ATR(14)` with `k = 2` by default. ATR (Average True Range) is the
average daily range, including gaps, computed with Wilder's smoothing. With
`--ticker`, ATR comes from the last 40 sessions of the GPW archive. A 2×ATR stop sits
outside normal daily noise. Use 1.5 for tighter swing setups and 3 for volatile small caps.

## 3. Half-Kelly

`f* = W − (1 − W) / R`, where `W` is the win rate and `R = avg win / avg loss`.
Kelly maximises long-run growth only if W and R are known exactly. Estimated from a
small sample of trades, they are noisy, and full Kelly then leads to very large
drawdowns. We use **half-Kelly** and cap it at **2%** risk per trade. If the
expectancy is not positive (f* ≤ 0), the script refuses to size a position.

## 4. Commissions (PLN)

`fee = max(value × pct, min_fee)` per order. The presets reflect public tariffs
summarised in November 2026. **Tariffs change, so tell the user to check their
broker's "tabela opłat i prowizji".**

| Preset | pct | min fee | note |
|---|---|---|---|
| mbank | 0.39% | 5 zł | eMakler, GPW shares |
| bossa | 0.38% | 5 zł | some sources quote 0.29% for certain plans |
| pkobp | 0.39% | 5 zł | BM PKO BP |
| santander | 0.39% | 5 zł | some plans 0.40% / 12 zł |
| xtb | 0% | 0 zł | 0% only up to 100k EUR monthly turnover, then 0.2% (min 10 EUR) |

**Minimum-fee drag warning:** when the minimum fee is binding and round-trip fees
exceed **0.5%** of the position value. That is about a third of a typical 1.5–2%
swing-trade stop distance, so costs become a material part of the risk.

## 5. Constraints (strictest wins)

| Constraint | Default | Why |
|---|---|---|
| Cash | 100% of account, entry fee included | no leverage on a cash account |
| Max position | 25% of account | at most 4 concentrated bets; one company-specific shock (ESPI report, accounting issue) cannot sink the account |
| Liquidity | 10% of 20-session **median** daily turnover | lets you exit within roughly one session without moving the price much. The median is used because block trades distort the mean |
| Illiquid flag | median turnover < 100 000 zł | typical of many sWIG80 / outside-index names, where spreads of 1–3% are common |
| Sector | optional | limit concentration in one sector |

## 6. Gap risk

A stop order on GPW turns into an order that executes at the market price. Under the
WATS trading rules (from 5 Oct 2026), shares have a **static price threshold of ±10%**
from the reference price; penny stocks have wider thresholds (15% at 0.10–0.1999 zł
and 30% below 0.10 zł). The dynamic threshold is ±3% for WIG20, ±4% for mWIG40 and ±6%
for others. Breaching a threshold triggers a balancing auction (300 s static, 60 s
dynamic). After an overnight gap (results, ESPI reports) the fill can be far below
the stop. The default scenario assumes a gap of 10% below the stop (`--gap-pct`).

## 7. Taxes by account type

| Account | At the sale | Later |
|---|---|---|
| Regular | 19% on gains (art. 30b PIT act), declared on **PIT-38 by 30 April**; commissions are deductible costs; FIFO | losses carried forward for 5 years (see gpw-trade-review) |
| IKE | no tax inside the account | no Belka tax if withdrawn after 60 (or after 55 with pension rights); otherwise taxed. 2026 limit: **28 260 zł** |
| IKZE | no tax inside the account | contributions deductible from income; withdrawal after 65 taxed at a flat **10%**. 2026 limit: **11 304 zł** (16 956 zł self-employed) |
| OKI | no Belka tax (from **1 Jan 2027**) | annual tax on asset value: **0.85%** (2027 rate) on investment assets above **100 000 zł**; a separate 25 000 zł exemption for savings assets |

The script shows the after-tax profit at the target only for regular accounts
(× 0.81). For other account types it shows the profit before the account-specific tax
and adds an explanatory note.

## 8. Thresholds summary

1% risk default · 2% risk warning · 2% Kelly cap · 2× ATR · 25% max position ·
10% of median turnover · 100k zł illiquid flag · 0.5% fee-drag warning · 10% gap ·
1.5R minimum reward warning (below 1.5R a 40% win rate barely breaks even after costs).

## 9. Sources

- GPW WATS trading rules (Szczegółowe Zasady Obrotu Giełdowego, §70–71): https://www.gpw.pl/pub/GPW/files/PDF/regulacje/Szczegolowe_Zasady_Obrotu_Gieldowego_GPW_WATS.pdf
- PIT-38 and capital gains: https://www.podatki.gov.pl/pit/
- IKZE relief: https://www.podatki.gov.pl/ulgi/ulga-na-ikze-pit
- IKE/IKZE limits 2026: https://strefainwestorow.pl/emerytura/limit-ike-ikze-2026
- OKI act: Dz.U. 2026 poz. 1098, https://eli.gov.pl/api/acts/DU/2026/1098/text/T/D20261098L.pdf
- Broker tariffs (secondary): https://freenance.io/rankingi/najtansze-konta-maklerskie/
- Upstream method: tradermonty/claude-trading-skills `position-sizer` (MIT)
