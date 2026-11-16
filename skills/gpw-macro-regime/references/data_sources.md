# gpw-macro-regime: data sources

Every endpoint was fetched and checked on 2026-11-16.

- **How we fetch.** All requests go through `gpwlib.http`, which provides:
  - the identifying User-Agent;
  - at least 1 s between requests to the same host;
  - an on-disk cache under `~/.cache/gpw-trading-skills/http/`.
- **Cost of a run.**
  - The first run makes about 45 requests (roughly 1-2 minutes).
  - A later run on the same day is served from the cache.
  - Past FX windows and past BondSpot days are cached permanently.
- **Offline.** `--offline` uses the cache only.

## Endpoints

| Key | URL | Format | Notes |
|---|---|---|---|
| `nbp_rates` | `https://static.nbp.pl/dane/stopy/stopy_procentowe_archiwum.xml` + `https://static.nbp.pl/dane/stopy/stopy_procentowe.xml` | XML | (1) |
| `nbp_fx_*` | `https://api.nbp.pl/api/exchangerates/rates/a/{eur,usd,chf}/{from}/{to}/?format=json` | JSON | (2) |
| `ecb_pl10y` | `https://data-api.ecb.europa.eu/service/data/IRS/M.PL.L.L40.CI.0000.PLN.N.Z?format=csvdata&startPeriod=YYYY-01` | SDMX CSV | (3) |
| `bundesbank` | `https://api.statistiken.bundesbank.de/rest/data/BBSIS/D.I.ZAR.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A?format=csv&startPeriod=YYYY-01-01` | CSV | (4) |
| `eurostat_hicp` | `https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr?geo=PL&coicop18=TOTAL&unit=RCH_A&unit=I15&sinceTimePeriod=YYYY-01` | JSON-stat 2.0 | (5) |
| `bondspot` | `https://www.bondspot.pl/fixing_obligacji?date=YYYYMMDD&month=M&year=YYYY` | HTML | (6) |
| `gpw_indices` | GPW archive via `gpwlib.gpw_archive` (type 1, indices) | HTML (cached JSON) | WIG, WIG20, mWIG40, sWIG80 and sector indices. |

Notes:

1. **NBP rate files.**
   - The archive runs from 1998 and changes only after an MPC decision.
   - Values use a decimal comma.
   - `www.nbp.pl/xml` sits behind Incapsula, so use `static.nbp.pl`.
2. **NBP FX.**
   - One request may cover at most 93 days. We request calendar quarters
     (90-92 days), so the cache keys are the same in every run.
   - `404 Brak danych` means there was no fixing in the window.
   - `400` is returned for an end date in the future.
3. **ECB PL 10Y.**
   - This is the Maastricht convergence 10Y yield, a monthly average.
   - The API is occasionally down (503 or a timeout on 2026-11-16). The
     skill then falls back to BondSpot alone.
   - The start period is floored to January so the cache key stays stable.
4. **Bundesbank Bund 10Y.**
   - Daily yields from the term structure.
   - The format depends on `Accept-Language`: German uses `;` and a decimal
     comma, English uses `,` and a decimal point. The parser handles both.
   - `.` means no value (weekends).
5. **Eurostat HICP.**
   - The dimension was renamed from `coicop` (values like `CP00`) to
     `coicop18` (`TOTAL`) with ECOICOP 2 in 2026. The fetcher retries with
     `coicop=CP00` on HTTP 400.
   - The old set `prc_hicp_manr` stops at 2025-12.
   - We use the published y/y (`RCH_A`) and fill gaps from the index (`I15`).
6. **BondSpot fixing.**
   - The page shows the second fixing by default. There is one row per bond:
     ISIN, bid/ask price, bid/ask yield, fixing price and fixing yield.
   - Non-session days return the page without the table, so the fetcher
     steps back up to 7 days.
   - robots.txt blocks only SEO crawlers.

## Publication lags (point-in-time rules)

| Source | Released | Used in history from |
|---|---|---|
| HICP month M | Eurostat, around mid M+1 | day 46 after the 1st of M |
| ECB 10Y month M | first days of M+1 | day 40 after the 1st of M |
| NBP rate | effective date in the archive | that date |
| FX, Bund, BondSpot | same day | that date |

## Considered and not used

| Source | Why not |
|---|---|
| GUS DBW / BDL API (`api-dbw.stat.gov.pl`, `bdl.stat.gov.pl`) | Usable without a key (BDL subjects list checked), but CPI needs the right variable/position IDs, which we have not verified as stable. Eurostat HICP is one documented query. A future switch would give the exact NBP target measure (CPI). |
| Ministry of Finance: non-resident holdings of T-bonds (monthly) | Published as spreadsheet attachments on gov.pl ("Zadłużenie Skarbu Państwa"). We found no stable, documented machine-readable URL, so this is future work. |
| GPW semi-annual investor-structure report | PDF, twice a year. Too slow and not machine-readable. |
| stooq, investing.com | Excluded: robots.txt, API keys or terms. |
| `www.nbp.pl/xml` | Incapsula bot protection. We do not get around it; we use `static.nbp.pl`. |
