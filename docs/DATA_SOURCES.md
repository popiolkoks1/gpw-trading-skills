# Data sources / Źródła danych

All sources are free and need no API key. Verified November 2026.

| Source | Used for | Access | Notes |
|---|---|---|---|
| **GPW — Archiwum notowań** `https://www.gpw.pl/archiwum-notowan?fetch=0&type=10&date=DD-MM-YYYY` | daily OHLC, % change, turnover of every share (`type=10`), index (`type=1`) and ETF (`type=241`) | HTML table, one request per session | Main source. Polish number format. Empty table = no session. Type ids come from the page's own instrument drop-down. A trimmed real page is kept in `tests/shared/fixtures/` so CI detects layout changes. |
| **GPW Benchmark** `https://gpwbenchmark.pl/ajaxindex.php?action=GPWIndexes&start=ajaxPortfolio&format=html&lang=PL&isin=<ISIN>` | current portfolios of WIG20/mWIG40/sWIG80 and 14 sector indices | HTML table | Current membership only (not point-in-time). Cached 7 days. |
| **GPW — Archiwum notowań, kontrakty** `...archiwum-notowan?fetch=0&type=35&date=DD-MM-YYYY` | futures (FW20, FW40, single-stock, FX) with the daily settlement price (`Kurs rozliczeniowy`) | same HTML table + one column | `gpwlib.gpw_archive.TYPE_FUTURES`. Symbol `FW20Z2620` = WIG20, Dec 2026, 20 PLN per point (last two digits; single-stock futures have none). All instrument types: `TYPE_NAMES` / `TYPE_LABELS_PL`. |
| **GPW — Komunikaty ESPI/EBI** `POST https://www.gpw.pl/ajaxindex.php` with `action=GPWEspiReportUnion&start=ajaxSearch&page=komunikaty&format=html&lang=PL&offset=0&limit=100&categoryRaports[]=ESPI&categoryRaports[]=EBI&typeRaports[]=RB&…&date=DD-MM-YYYY` | list of company reports per day (time, type, number, company, ISIN, title) | HTML `<li>` list, paginated by `offset` | `gpwlib.espi`. Types: RB bieżący, P półroczny, Q kwartalny, O okresowy, R roczny. Quarterly reports often have an empty title. Past days cached 30 days, today 10 min. |
| **GPW — report page** `https://www.gpw.pl/komunikat?geru_id=<N>` | subject, legal basis, text, ESAP metadata codes (e.g. `TRANSD` = managers' transaction, MAR art. 19), attachment names | HTML | Cached a year. **Attachments (PDF) are not downloaded**: the GPW WAF blocks scripted downloads and the skills do not try to get around it - the user pastes the PDF text when needed. |
| **GPW — company card** `https://www.gpw.pl/ajaxindex.php?start=indicatorsTab&format=html&action=GPWListaSp&gls_isin=<ISIN>&lang=PL` and `…?start=showNotoria&action=GPWListaSp&code=<TICKER>&isin=<ISIN>&format=html&lang=PL` | shares issued, market value, book value, P/E, P/BV, dividend yield, sector; latest-period revenue, EBIT, EBITDA, net profit, assets, equity, cash flows, capex | HTML tables | `gpwlib.fundamentals`. Notoria figures: thousands of PLN (converted to PLN), consolidated flag, P&L / cash flow year-to-date (`I-III kw.` = 9 months). Only the latest period is shown, so the skills build their own history (`~/.cache/gpw-trading-skills/fundamentals/history`). Cached 1 day. Ticker -> ISIN: see the quotes tables below. |
| **GPW / NewConnect — quotes tables (ticker <-> ISIN)** `https://www.gpw.pl/ajaxindex.php?action=GPWQuotations&start=showTable&tab=all&lang=PL&type=&full=1&format=html`, the same with `start=listSingle&type=fix1` / `type=fix2`, and `https://newconnect.pl/ajaxindex.php?action=NCExternalDataFrontController&start=showTable&tab=all&lang=PL&type=ALL&full=1&system_type=&format=html` | every listed share: "Nazwa" (the short name used in the archive, e.g. `11BIT`), ISIN, "Skrót" (3-letter code) | HTML tables behind `https://www.gpw.pl/akcje` and `https://newconnect.pl/notowania` | `gpwlib.fundamentals.listed_companies` / `isin_map` / `ticker_for_isin` / `resolve_isin`. Four GET requests, cached 7 days (parsed copy in `<cache>/fundamentals/listed_companies.json`). Nov 2026: 402 Main Market shares (369 continuous + 3 + 30 single-price) and 327 NewConnect shares; 402/402 names of the 2026-11-13 archive session and 6 422/6 422 cached ESPI rows (Dec 2025 - Nov 2026) map to a ticker (GPW Benchmark portfolios alone: 5 620). Status flags after the name (`PBG /Z /6,/7`) are stripped. Fallbacks: GPW Benchmark portfolios, pre-rename tickers (`panel.RENAMES`), the user's override file, `--isin`. `robots.txt` of both hosts disallows one unrelated path only. Current listing only: delisted companies need `--isin`. `ticker_for_company_name` matches ESPI company names when a row has no ISIN (best effort: 336 of 407 cached names matched, 1 wrong, the rest unresolved). |
| **filings.xbrl.org** `https://filings.xbrl.org/api/filings?filter=[{"name":"country","op":"eq","val":"PL"}]` | ESEF annual reports as xBRL-JSON (`json_url`): IFRS facts (Revenue, ProfitLoss, Assets, Equity, cash flows, EPS) | JSON:API + xBRL-JSON | Open data (XBRL International). Polish coverage is thin: FY2020-2023 and a few Q1 2024 filings, nothing for FY2025 (Nov 2026). User-supplied ESEF zips are parsed locally (`fundamentals.parse_esef_zip`). |
| **KNF — rejestr krótkiej sprzedaży** (knf.gov.pl) | net short positions >= 0.5% per issuer and holder | JSON behind the public register page | Public register of the Polish FSA. Not called by gpwlib itself; used by `gpw-ownership`. |
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

- Sequential requests, retries with back-off, identifying User-Agent
  `gpw-trading-skills/<version>` - never a spoofed browser string. New data
  sources go through `gpwlib.http`: at least 1 s between requests to the same
  host (`GPW_SKILLS_MIN_INTERVAL` can only make it slower), on-disk cache under
  `<cache>/http/`, `--offline` = cache only. The 1.0 archive client keeps its
  own 0.6 s interval (one request per session, cached forever).
- Bot protection is respected: when a site refuses scripted access (the GPW
  WAF on attachment PDFs, Stooq's proof-of-work), the skills stop and ask the
  user to supply the data instead of working around it.
- Every session is downloaded once and cached locally
  (`~/.cache/gpw-trading-skills`, override with `GPW_SKILLS_CACHE`).
- GPW market data is published for information purposes; redistribution of
  data may require a licence from GPW. This project ships **code only** — the
  cache stays on the user's machine and must not be republished.
- The same applies to company-card data on gpw.pl, including the figures
  supplied by Notoria Serwis: personal analysis only, kept in the local cache
  and the local fundamentals history, never redistributed. ESPI/EBI report
  texts are public regulatory disclosures; the KNF short-selling register is a
  public register; filings.xbrl.org data is open.

## Corporate actions

The archive publishes unadjusted closes. Dividends and splits are detected
from the mismatch between the raw close change and GPW's published % change
(computed against the reference price, which GPW cuts on ex-dates) and
history is rescaled when the mismatch exceeds 0.5%. See
`shared/gpwlib/panel.py`. The open price is rescaled the same way
(`Series.open`, gpwlib 1.2); the archive has no share volume column
(`Series.volume` stays `None`, turnover in PLN thousands is `turnover_k`).

## Renamed tickers

The archive lists each share under the ticker valid on that day, so a renamed
company appears as two series (CCC until 2026-02-18, MODIVO from
2026-02-19). `panel.RENAMES` is an explicit, editable alias table
`{new: [(old, last_old_date)]}` (53 entries for 2019-01..2026-11, 14 of them in
2024-11..2026-11); `build_panel(merge_renames=True)` (the default) stitches
them under the newest ticker and lists the old names in
`Series.renamed_from`. Evidence per pair: the new name's first-session
reference price equals the old name's last close (GPW carries it over), the
new name's current ISIN keeps the old code (PLCCC0000016, PLBZ00000044, ...)
and, where cached, an ESPI notice (e.g. Bumech "Zmiana nazwy skróconej i
oznaczenia akcji", geru_id 495204). `panel.detect_rename_candidates`
reports new candidates (price continuity + turnover profile) and never
merges anything by itself.
