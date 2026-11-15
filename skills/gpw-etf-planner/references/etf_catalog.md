# ETFs listed on GPW — catalog (compiled 2026-11-15)

Source of the list: GPW *Archiwum notowań*, instrument type 241 (38 funds
quoted on 2026-11-13). The machine-readable copy is `scripts/etf_catalog.py`;
keep both in sync.

**Read before using:** descriptions marked "(verify)" are inferred from the
fund's name. TER is deliberately not listed — take it from the fund's KID
(Key Information Document) and pass `--ter NAME=0.5`. The OKI column is only a
hint: "likely qualified" is given only where the fund holds Polish shares or
PLN bonds/cash directly, as required by art. 26 of the OKI act (≥ 70%
qualified assets); everything else is "not qualified". Always verify in the
fund statute.

Currency: PLN = Polish assets; PLN-hedged = foreign assets with the currency
risk hedged to PLN; USD/EUR/JPY/multi = the investor bears that currency risk.

| Short name | Issuer | Underlying | Category | Currency | OKI hint |
|---|---|---|---|---|---|
| ETFBW20TR | Beta ETF | WIG20TR (20 largest GPW companies, total return) | pl_equity | PLN | likely qualified |
| ETFBM40TR | Beta ETF | mWIG40TR (40 mid caps) | pl_equity | PLN | likely qualified |
| ETFBS80TR | Beta ETF | sWIG80TR (80 small caps) | pl_equity | PLN | likely qualified |
| ETFBDIVPL | Beta ETF | Polish dividend stocks (verify index) | pl_equity | PLN | likely qualified |
| ETFPZUW20M40 | TFI PZU (inPZU) | WIG20 + mWIG40 large and mid caps (verify) | pl_equity | PLN | likely qualified |
| ETFIWDA | iShares | MSCI World (developed markets) | global_equity | multi | not qualified |
| ETFPZUWORLD | TFI PZU (inPZU) | developed-market equities (verify index) | global_equity | multi | not qualified |
| ETFSP500 | Beta ETF | S&P 500, currency unhedged | us_equity | USD | not qualified |
| ETFBSPXPL | Beta ETF | S&P 500 hedged to PLN | us_equity | PLN-hedged | not qualified |
| ETFBNDXPL | Beta ETF | Nasdaq-100 hedged to PLN | us_equity | PLN-hedged | not qualified |
| ETFDAX | Beta ETF | DAX (German large caps) | other_equity | EUR | not qualified |
| ETFEUNM | iShares | MSCI Emerging Markets (verify) | other_equity | multi | not qualified |
| ETFISIJPA | iShares | Japanese equities (verify) | other_equity | JPY | not qualified |
| ETFV60A | Vanguard | LifeStrategy 60% equity / 40% bonds (verify) | mixed | multi | not qualified |
| ETFBTBSP | Beta ETF | Polish treasury bonds (TBSP index) | pl_bonds | PLN | likely qualified |
| ETFPZULONGDUR | TFI PZU (inPZU) | long-duration Polish bonds (verify) | pl_bonds | PLN | likely qualified |
| ETFBCASH | Beta ETF | short-term PLN bonds / money market | cash | PLN | likely qualified |
| ETFPZUCASH | TFI PZU (inPZU) | PLN money market (verify) | cash | PLN | likely qualified |
| ETFPZUGOLD | TFI PZU (inPZU) | gold (verify physical/futures) | gold_commodity | USD | not qualified |
| ETFSLVR | ? (verify) | silver or silver miners (verify) | gold_commodity | USD | not qualified |
| ETFBTCPL | Beta ETF | bitcoin via futures (verify) | crypto | PLN-hedged | not qualified |
| ETFNATO | ? (verify) | defence companies (verify) | thematic | multi | not qualified |
| ETFAIFS | ? (verify) | AI / semiconductors (verify) | thematic | multi | not qualified |
| ETFHANARMY | HANetf | defence (verify) | thematic | multi | not qualified |
| ETFHANCPPR | HANetf | copper miners (verify) | thematic | multi | not qualified |
| ETFHANDRON | HANetf | drones (verify) | thematic | multi | not qualified |
| ETFHANESGO | HANetf | theme — check the KID | thematic | multi | not qualified |
| ETFHANGR8 | HANetf | theme — check the KID | thematic | multi | not qualified |
| ETFHANMMLP | HANetf | theme — check the KID | thematic | multi | not qualified |
| ETFHANUKRN | HANetf | Ukraine / reconstruction (verify) | thematic | multi | not qualified |
| ETFHANURNM | HANetf | uranium miners (verify) | thematic | multi | not qualified |
| ETFHANYMAG | HANetf | option-income strategy on large US stocks (verify — complex) | thematic | USD | not qualified |
| ETFBW20LV | Beta ETF | WIG20 2x daily leverage | leveraged | PLN | not qualified (derivatives) |
| ETFBM40LV | Beta ETF | mWIG40 daily leverage (verify multiple) | leveraged | PLN | not qualified (derivatives) |
| ETFBNQ3LV | Beta ETF | Nasdaq-100 3x daily leverage | leveraged | PLN-hedged | not qualified |
| ETFBW20ST | Beta ETF | inverse WIG20 | short | PLN | not qualified (derivatives) |
| ETFBM40ST | Beta ETF | inverse mWIG40 (verify) | short | PLN | not qualified (derivatives) |
| ETFBNQ2ST | Beta ETF | Nasdaq-100 2x inverse | short | PLN-hedged | not qualified |

## Why leveraged and inverse ETFs are flagged

They reset their leverage every day, so over more than one day the result is
not 2x (or −1x) of the index: in a choppy market they lose value even when the
index ends flat ("volatility decay"). Example: index +10% then −9.09% = 0%;
a 2x fund: +20% then −18.18% = −1.8%. They are tools for very short-term
trading, never for a beginner's long-term portfolio.

## Why OKI qualification matters

On OKI (from 2027) up to 100 000 zł of *qualified* assets is exempt from the
yearly asset tax; non-qualified assets (global/US ETFs, gold, crypto) are taxed
from the first zloty (0.85% of the average value in 2027). The planner reports
the share of the target portfolio that is likely qualified so it can be passed
to `gpw-account-planner --oki-qualified-pct`.

## Maintenance

When GPW lists a new ETF, it appears as "uncategorised" in `list`. Add it to
`CATALOG` in `scripts/etf_catalog.py` and to this table, with
`confidence="name"` unless the description was checked in the KID.
