# Data sources / Źródła danych

All sources are free and need no API key. Verified November 2026.

| Source | Used for | Access | Notes |
|---|---|---|---|
| **GPW — Archiwum notowań** `https://www.gpw.pl/archiwum-notowan?fetch=0&type=10&date=DD-MM-YYYY` | daily OHLC, % change, turnover of every share (`type=10`), index (`type=1`) and ETF (`type=241`) | HTML table, one request per session | Main source. Polish number format. Empty table = no session. Type ids come from the page's own instrument drop-down. A trimmed real page is kept in `tests/shared/fixtures/` so CI detects layout changes. |
| **GPW Benchmark** `https://gpwbenchmark.pl/ajaxindex.php?action=GPWIndexes&start=ajaxPortfolio&format=html&lang=PL&isin=<ISIN>` | current portfolios of WIG20/mWIG40/sWIG80 and 14 sector indices | HTML table | Current membership only (not point-in-time). Cached 7 days. |
| **NBP API** `https://api.nbp.pl/api/exchangerates/rates/a/<ccy>/<date>/?format=json` | FX rates (foreign dividends / trades, PIT-38) | JSON | Documented for users; not called by the scripts yet. 404 = no fixing that day → use the previous business day. |

| **Legal texts** (Dz.U., M.P.) | IKE/IKZE limits, OKI rules, Belka tax | constants in `shared/gpwlib/tax.py` | Each constant cites its article. OKI verified against the official text of Dz.U. 2026 poz. 1098 (art. 25, 26, 32, 43). Limits change every year and must be updated by hand. |
| **Broker tariffs** | commission presets | constants in `shared/gpwlib/brokers.py` | Entered on `TARIFF_ENTERED`; reports warn after 180 days. Users can always override the rates. |

## Not used, and why

- **Stooq** CSV endpoints: since 2026 they require a manually obtained API key
  (CAPTCHA) and serve a browser proof-of-work challenge to scripts. The skills
  do **not** try to bypass it. A user-supplied key could be added later as an
  optional source.
- **biznesradar.pl**: `robots.txt` disallows the historical-quotes paths.

## Politeness and terms

- Sequential requests, ≥0.6 s apart, retries with back-off, identifying
  User-Agent `gpw-trading-skills/1.0`.
- Every session is downloaded once and cached locally
  (`~/.cache/gpw-trading-skills`, override with `GPW_SKILLS_CACHE`).
- GPW market data is published for information purposes; redistribution of
  data may require a licence from GPW. This project ships **code only** — the
  cache stays on the user's machine and must not be republished.

## Corporate actions

The archive publishes unadjusted closes. Dividends and splits are detected
from the mismatch between the raw close change and GPW's published % change
(computed against the reference price, which GPW cuts on ex-dates) and
history is rescaled when the mismatch exceeds 0.5%. See
`shared/gpwlib/panel.py`.
