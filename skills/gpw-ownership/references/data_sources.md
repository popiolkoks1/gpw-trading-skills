# gpw-ownership — data sources and legal notes

All requests go through `gpwlib.http` (identifying User-Agent
`gpw-trading-skills/<version>`, >= 1 s between requests to one host, on-disk
cache in `~/.cache/gpw-trading-skills`). Cached data stays on the user's
machine.

## KNF short-position register (mode `shorts`) - verified 2026-11-16
- Public pages: https://rss.knf.gov.pl/rss_pub/rss.html (current) and
  https://rss.knf.gov.pl/rss_pub/rssH.html (history). KNF robots.txt allows
  crawling.
- JSON endpoint behind both pages:
  `POST https://rss.knf.gov.pl/rss_pub/JSON`, form field
  `request={"cmd":"get","search":[],"limit":1000,"offset":0,"method":"Default"|"RssHTable","sort":[],"searchLogic":"AND","searchValue":""}`.
  `Default` = current (27 rows on 2026-11-16), `RssHTable` = history (2 329
  rows since 2012-11-01, paginated by 1 000).
- Fields: `HOLDER_FULL_NAME` (HTML-escaped), `ISSUER_NAME` (GPW short name),
  `ISIN`, `NET_SHORT_POSITION_O` ("0.61"), `POSITION_DATE` (calculation date),
  `MODIFY_DATE` (publication date).
- Cache: current 6 h, history 24 h (KNF publishes once per trading day).
- Legal basis: Regulation (EU) No 236/2012 (SSR) art. 5 (notification to KNF
  from 0.1%, Delegated Regulation (EU) 2022/27), art. 6 (public disclosure
  from 0.5%), art. 9 (deadline 15:30 next trading day).

## ESPI list and report pages (modes `insiders`, `ticker`)
- `gpwlib.espi.fetch_range` (one POST per session day, past days cached 30
  days) and `espi.fetch_report` (one GET per report, cached a year) only with
  `--details` or `--ticker`, capped by `--max-details`.
- Attachment PDFs are never downloaded (the GPW WAF refuses scripts; no
  browser impersonation). The user pastes the text.
- Quirk (Nov 2026): some issuers tag every report with ESAP `TRANSD`
  (e.g. a credit-agreement annex and a rating report), so the skill requires
  MAR art. 19 wording in the text as well. This also matters for
  `gpwlib.espi.classify`, which trusts `TRANSD` alone.
- MAR art. 19: Regulation (EU) No 596/2014; threshold EUR 20 000 from the
  Listing Act (Regulation (EU) 2024/2809); ESMA70-154-1020 (19 Jan 2026) list
  of national deviations - Poland not listed.
- Local store: `<state-dir>/insiders/transactions.json` (parsed transactions,
  de-duplicated by id) and `<state-dir>/insiders/notices.json` (detected
  notices; not written on `--as-of` runs).

## OFE / PPK annual portfolios (mode `funds`)
- analizy.pl document store (robots.txt does not block `/pobierz`):
  `https://dokumenty.analizy.pl/pobierz/fe/<CODE>/RR/YYYY-12-31`.
  Confirmed code: `ENNE1` = Nationale-Nederlanden OFE (PDF, 4 pages, 128 Polish
  equity lines for 2025-12-31). Codes of other funds have to be looked up on
  analizy.pl by the user (the fund page links to "Roczna struktura aktywów");
  PPK fund codes start with `EPCZ`/similar and must be configured by hand.
- Downloads use gpwlib.http's rate-limited fetcher; the raw PDF is cached in
  `<cache>/ownership/funds/<CODE>_<YEAR>.pdf` (gpwlib.http only returns
  decoded text, which corrupts binary files - reported as a shared-lib gap).
- Each PTE also posts the file on its own website; any PDF with a text layer,
  XLSX or CSV works with `--file`.
- Parsed portfolios are stored in `<state-dir>/ownership/funds/` so next
  year's run finds the previous year automatically.

## Prices and shares
- GPW archive (`gpwlib.gpw_archive`, `gpwlib.panel` adjusted closes) for
  prices, turnover and year-end closes.
- Shares outstanding: gpw.pl company card indicators tab
  (`fundamentals.indicators_url`), cached 7 days, one request per issuer;
  `--no-shares` skips it.
- ISIN ↔ ticker: `fundamentals.isin_map` (GPW Benchmark portfolios); fund
  names → tickers: user `--name-map`, company names seen in cached ESPI
  lists, built-in aliases, then automatic rules.
