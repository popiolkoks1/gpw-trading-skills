# gpw-ownership — methodology and evidence

Load when the user asks "how/why", or before interpreting an unusual result.
Thresholds live in the scripts (`ownership_shorts.py`, `ownership_insiders.py`,
`ownership_funds.py`) with comments pointing here.

## 1. Short positions (mode `shorts`)

### What the data is
- Regulation (EU) No 236/2012 (Short Selling Regulation, SSR): a net short
  position in shares is **notified to the competent authority** from 0.1% of
  issued share capital (art. 5(2), threshold lowered from 0.2% to 0.1% by
  Commission Delegated Regulation (EU) 2022/27, applied from 31 January 2022)
  and **disclosed to the public** from 0.5% and at every 0.1 pp step above
  (art. 6(2)). Notifications are due by 15:30 on the next trading day
  (art. 9(2)).
- KNF publishes the public part: the current register (positions >= 0.5%
  now) and a history of every published disclosure since 2012-11-01.
- Consequence 1: everything below 0.5% is invisible. The skill's totals are
  a **lower bound** of real short interest.
- Consequence 2: when a holder falls below 0.5%, the pair disappears from the
  current list; the history keeps only its earlier >= 0.5% rows. In the KNF
  history we checked (Nov 2026, latest 1 000 rows), 84 of 101 closed pairs ended at
  0.50-0.64%,
  consistent with "fell below 0.5%, not published". The closing date is
  unknown; the skill reports `closed_position` with `exact_date: false`.
  From the first run on, the skill saves a snapshot of the current register
  in `<state-dir>/ownership/shorts/`, so later closings are bracketed.

### Reconstruction of past totals (`change_20s_pp`, `--since`, history)
A pair counts as open on day D when it has a disclosure on/before D and it is
still in the current register, or it had another disclosure after D, or a
local snapshot taken around D lists it. Pairs that left the register and
whose last disclosure is before D are ambiguous and are **not** counted
(lower bound). This can overstate an *increase* in the 20-session change when
a holder silently closed before D; the error is at most the last disclosed %
of such holders (typically 0.5-0.6 pp).

### Per-issuer metrics
- `total_pct`, `holders`, `largest_holder`.
- `days_in_register`: from the start of the earliest current holder's
  episode (an episode restarts after a gap of more than 365 days between two
  disclosures of the same pair - a new position, not the same one).
- `days_to_cover` = (total % x shares outstanding) / median daily volume of
  the last 20 sessions. Shares outstanding: gpw.pl company card ("Liczba
  wyemitowanych akcji"). Volume ≈ turnover / close because the archive gives
  turnover, not share volume. Hong, Li, Ni, Scheinkman and Yan (2016, NBER
  w21166, "Days to Cover and Stock Returns") show that short interest scaled
  by trading volume predicts returns better than short interest alone.
  `high_days_to_cover` at >= 5 days is a practitioner rule of thumb for
  squeeze-prone names (a week of average volume), not an academic cut-off.
- `price_change_since_first_pct`: adjusted close (panel, dividends and
  splits) from the session on/before the first disclosure to the last
  session. Null when the price history is shorter (e.g. ticker renamed).

### "Crowded" flag
`crowded = total_pct >= 2.0 or holders >= 3`.
- Desai, Ramesh, Thiagarajan and Balachandran (2002, Journal of Finance
  57(5), "An Investigation of the Informational Role of Short Interest in the
  Nasdaq Market"): stocks with short interest of at least 2.5% of shares
  ("heavily shorted") earn significant negative abnormal returns of roughly
  0.76-1.13% a month. Because the KNF figure is a lower bound, 2.0%
  disclosed is our proxy for that bucket.
- Boehmer, Jones and Zhang (2008, Journal of Finance 63(2), "Which Shorts Are
  Informed?"): heavily shorted stocks underperform lightly shorted ones;
  institutional shorts are informed.
- Jones, Reed and Waller (2016, Review of Financial Studies 29(12),
  "Revealing Shorts: An Examination of Large Short Position Disclosures"):
  EU-style public disclosure of large positions; disclosed positions predict
  negative subsequent returns. Three or more independent holders each above
  0.5% is a consensus of large, informed funds.
- On GPW only about 13-25 companies are in the register at a time, so the
  flag is rare and should be read with the company's liquidity in mind.

Interpretation: crowded = negative drift on average *and* squeeze risk
(high days to cover). It is a "be careful / check the thesis" input for
`gpw-pre-trade-gate`, never a sell signal.

### Signals
`short_crowded` (date = as_of) and `short_new` (date = first disclosure),
direction `avoid`; `score = min(100, 25 x total %)` (4% disclosed = 100),
a simple linear scale used only to bucket outcomes in the post-mortem.

## 2. Managers' transactions (modes `insiders`, `parse`)

### Legal frame (verified 2026-11-16)
- MAR (Regulation (EU) No 596/2014) art. 19(1): PDMRs and persons closely
  associated with them notify the issuer and KNF of own-account transactions
  promptly and no later than three business days after the transaction
  (wording confirmed in the UKNF position on art. 19 MAR, 2022).
- Threshold, art. 19(8) as amended by the Listing Act (Regulation (EU)
  2024/2809): EUR 20 000 within a calendar year (previously EUR 5 000; the
  2022 UKNF position still cites 5 000). Art. 19(9): authorities may raise it
  to EUR 50 000 or lower it to EUR 10 000. ESMA list ESMA70-154-1020 (19
  January 2026) names Malta (10 000), Denmark, Germany and France (50 000);
  **Poland is not listed**, so the EUR 20 000 default applies. Trades below
  the threshold are invisible.
- Art. 19(11): closed period of 30 calendar days before the announcement of
  an interim or year-end report.
- Template: Commission Implementing Regulation (EU) 2016/523 - sections 1
  (name), 2 (position/status, initial/amendment), 3 (issuer, LEI), 4 (a
  instrument, b nature, c prices and volumes, d aggregated, e date, f place).
  The parser reads Polish and English versions, several transactions per form
  and several forms per paste, and de-duplicates bilingual copies.

### Evidence
- Lakonishok and Lee (2001, Review of Financial Studies 14(1), "Are Insider
  Trades Informative?"): insider purchases predict returns, sales do not;
  the effect is concentrated in small firms.
- Jeng, Metrick and Zeckhauser (2003, Review of Economics and Statistics
  85(2), "Estimating the Returns to Insider Trading"): purchases earn about
  6% a year abnormal returns; sales earn nothing significant.
- Cohen, Malloy and Pomorski (2012, Journal of Finance 67(3), "Decoding
  Inside Information"): "routine" insiders (trading in the same calendar
  month in each of the previous three years) carry no signal; "opportunistic"
  trades earn about 0.8% a month. Hence the `routine` flag (needs >= 3 years
  of stored history, else null).
- WSE: Bohl and Wiśniewski (2005; Open University repository,
  oro.open.ac.uk/55398) find unusually high insider profits on the Warsaw
  market under weak enforcement. An event study of WSE notices 2010-2020 is
  in Annales UMCS (journals.umcs.pl/h/article/view/12787).
- Cluster buys (>= 3 distinct insiders within 30 days): the screen popularised
  by openinsider.com and used as "cluster" in practitioner research; the
  rationale (independent insiders agreeing) follows from the information
  content documented above. A 30-day window matches MAR's 3-business-day
  reporting lag plus a month of trading.

### Counting rules
- Open-market only: option exercises, incentive plans, gifts/inheritance,
  pledges and securities lending, subscriptions in share issues and tender
  offers/buy-backs are `excluded` (kept for transparency, not counted).
- Closely associated **legal persons** are flagged `entity` and kept in
  buys/sells and net value but excluded from the cluster count. On GPW they
  are often the parent company or a bank acting for itself (example seen:
  PKO Bank Polski S.A. reported as closely associated with a supervisory-board
  member of PGE), whose trades are not information-driven in the same way.
- `distinct_insiders`: from parsed names when available; otherwise from
  person hints in the report body (often in the Polish genitive, e.g.
  "Waldemara Lipkę"). The two are never mixed.
- `net_value_pln`: sum of buy values minus sell values of parsed open-market
  transactions in PLN; null when nothing was parsed.
- `closed_period`: true when a periodic report (quarterly, half-year, annual)
  of the same issuer was published within 30 days after the transaction date
  (from the ESPI list in the analysed range); null when the next report is
  not known.
- ESPI notices are detected by the list classifier (title rules) and, with
  `--details`, the report page (ESAP code TRANSD plus text that mentions MAR
  art. 19). Text that refers to art. 69/70 of the Public Offering Act is
  flagged `holding_notice` (major-holding notice mislabelled as insider).

### Signals
`insider_cluster_buy` → `long`, `insider_cluster_sell` → `exit`;
`score = min(100, 20 x max distinct insiders in 30 days)`.

## 3. Pension and PPK funds (mode `funds`)

- OFE publish once a year the "Roczna struktura aktywów" (art. 194 of the Act
  on pension funds): holding name, value in PLN, % of assets. No share counts.
- `approx_shares = value / raw year-end close` (last session on/before the
  valuation date). Funds value listed shares at the closing price, so this
  is close; renamed tickers (e.g. CCC → MODIVO, Santander Bank Polska →
  Erste Bank Polska) may have no close under the new name → null with a
  warning.
- Year-over-year `implied_flow_pln = value_now - value_prev x P_now /
  (P_prev x f)` where `f` is the product of split-like panel adjustments
  (|factor - 1| > 20%) between the dates; dividends are not added back
  because a fund receives them in cash.
- "Top buys/sells" ignore flows below 1 mln PLN or below 5% of the previous
  position (rounding of year-end prices and corporate actions dominate).
- `fund_accumulation`: implied net buying >= 20% of the previous combined
  position by at least two funds. Research note: OFE hug the WIG and face
  structural outflows; we found no evidence that following them earns excess
  returns, so this is context (who supplies liquidity), weighted low in any
  aggregation.

## 4. Limitations
- KNF: lower bound, no closing dates, GPW-listed foreign issuers appear only
  if KNF is their competent authority.
- Insiders: details only from user-pasted text; small trades below EUR
  20 000 a year invisible; title-based direction hints can be wrong.
- Funds: annual snapshots, parser tuned to two PDF layouts (NN OFE, PPK);
  unknown layouts → use CSV/XLSX.
