# gpw-dividend-analyzer - methodology and sources

Everything below is computed by `scripts/dividend_engine.py` (pure, unit-tested)
from data loaded by `scripts/dividend_sources.py`. Claude only interprets.

## 1. Where the dividend history comes from

### 1.1 GPW price archive (always)

The GPW quotes archive (`https://www.gpw.pl/archiwum-notowan`, cached locally by
`gpwlib.gpw_archive`) publishes the **raw** close and the % change versus the
**reference price**. On the ex-dividend date GPW lowers the reference price by
the dividend, so:

```
reference price  = close_ex / (1 + change%_ex / 100)
gross DPS        = close_(last cum session) - reference price
factor           = reference price / previous close      (gpwlib.panel adjustment)
```

`gpwlib.panel.build_panel` already detects these adjustments (it uses them for
dividend-adjusted series); the skill does not add a second adjustment layer, it
undoes panel's backward adjustment to get raw closes (`raw_closes`) and reads
DPS from each adjustment (`reconstruct_dividends`).

* **Precision.** Change% is published with 2 decimals, so the reference price
  is known to about +-0.005% of the price; GPW also rounds the reference price
  to the tick (ticks are roughly 0.01-0.1% of the price, Szczegółowe Zasady
  Obrotu Giełdowego). Each event carries `precision_pln`; DPS is rounded to
  whole grosze (dividends are set in grosze) and `dps_raw` keeps the estimate.
  Live check: PZU 2019-2026 reconstructed DPS equal the published ones
  (2.80, 3.50, 1.94, 2.40, 4.34, 4.80) except 2025: 4.46 vs 4.47.
* **Invisible dividends.** `panel` reports only cuts larger than 0.5% of the
  price (`panel.ADJ_TOLERANCE`, chosen there to stay above change% rounding
  noise). A dividend below 0.5% of the price (e.g. KGHM 2025, 0.6 zł at ~150 zł)
  is invisible in the archive and is taken from ESPI or biznesradar.
* **Not dividends.** Factors below 0.75 (cut > 25%: splits, rights issues,
  spin-offs) or above 1 are `corporate_action`. Cuts right after a missing
  cached session are `cache_gap` (e.g. the archive cache lacks 2022-01-03, which
  makes panel report a spurious adjustment on 2022-01-04 for ~320 shares). A
  rights issue or spin-off with a cut between 0.5% and 25% would look like a
  dividend; the ESPI cross-check catches most of them.
* **History length.** As deep as the local cache: in Nov 2026 it holds shares
  from 2019-01-02 (fetched by gpw-backtester). The skill never downloads old
  sessions itself (only the newest 5 if missing) and says how many full years
  it saw (`history.max_years_observed`, `short` < 5 years).

### 1.2 ESPI dividend reports (cross-check and upcoming payments)

`gpwlib.espi` lists current reports per day (gpw.pl, `GPWEspiReportUnion`) and
classifies titles; class `dividend` reports of the analysed companies (matched
by ISIN) are read (report page only - PDF attachments are never downloaded,
GPW's WAF blocks scripts). The parser (copied from gpw-event-calendar and
extended) extracts:

* DPS ("1,00 zł na jedną akcję", "45 groszy na każdą akcję", "PLN 2.10 per
  share"); several amounts in one report: ordinary + extraordinary dividend are
  summed; "pozostająca do wypłaty dywidenda ... X zł" gives the tranche still to
  be paid after interims;
* record date ("dzień dywidendy", "dzień ustalenia prawa", "lista akcjonariuszy
  uprawnionych ... na dzień"), payment date (strong keywords "dzień/termin
  wypłaty" first; dates after "z dnia" or in the legal-basis paragraph are
  ignored; payment is never before the record date), total amount, shares
  covered, "nadzwyczajna/specjalna/jednorazowa" wording, stage (resolution,
  proposal, interim, policy, no_dividend).
* **Ex-date (KDPW T+2, since 6 Oct 2014):** last session with the right =
  record date - 2 sessions; ex-date = record date - 1 session. KSH art. 348 § 4:
  the record date falls 5 days to 3 months after the resolution.

Merge rule: an ESPI amount replaces the archive estimate for the same ex-date
(+-1 session) and the pair is reported in `crosscheck[]` (`match` within 2% or
0.02 zł, else `mismatch`); an ESPI dividend with a past ex-date missing in the
archive is added (`not_in_archive`, usually the < 0.5% case). ESPI items with a
future ex-date are `upcoming` (confirmed); a proposal or interim without dates,
published after the last ex-date, is `proposed_date_estimated` (date from
history). Default look-back: 365 days (one AGM season); `--espi-days` changes it.

### 1.3 biznesradar.pl (optional, `--source biznesradar`)

Company page `https://www.biznesradar.pl/dywidenda/<CODE>` (robots.txt allows
it, checked 2026-11-16): fiscal year, DPS, total, part from reserve capital,
status, AGM date, last session with the right, payment date - often 15+ years.
Third-party data: **personal, local use only** (it stays in the user's cache,
never redistributed; see docs/DATA_SOURCES.md). Default off. Rows are added only
for ex-dates the archive and ESPI do not already cover.

## 2. Metrics per company

| Field | Definition |
|---|---|
| `dps_history[]` | DPS summed per **calendar year of the ex-date**, from the first fully observed year (series starting within the first 10 days of January) to the running year (`complete: false`). Two-tranche payers (LPP: April + October) sum both. |
| `years_observed`, `years_paid` | Final years only: complete years plus the running year when it already paid **and** the company never paid more than once a year (a two-tranche year is not final yet). |
| `consecutive_years` | Paying years counted back from the last final year. |
| `cuts[]` | Year-over-year fall of DPS by more than **10%** (`cut`) or to zero (`suspension`). 10% stays above reconstruction noise and PLN-rounded DPS policies; Lintner (1956) managers move DPS gradually, so a larger fall is a deliberate signal. |
| `dps_cagr_3y_pct`, `dps_cagr_5y_pct` | `(DPS_last / DPS_last-n)^(1/n) - 1` on final years; `null` when either end is zero or the history is shorter. |
| `yield_gross_pct` | Trailing 12 months: sum of DPS with ex-date in (as_of-365 d, as_of] / last raw close. |
| `forward_yield_gross_pct` | DPS of a published resolution/proposal for the next payment / last close; `null` without one. |
| `yield_net_regular_pct` | gross x (1 - 19%). Belka tax on dividends: art. 30a ust. 1 pkt 4 ustawy o PIT, withheld by the payer (art. 41 ust. 4). |
| `yield_ike_pct` | gross (IKE: no tax if withdrawn under the act's conditions - ustawa o IKE i IKZE, Dz.U. 2026 poz. 91). |
| `yield_ikze_after_exit_tax_pct` | gross x 0.9 - inside IKZE nothing is withheld; the 10% flat tax (art. 30 ust. 1 pkt 10a PIT) falls on the whole withdrawal. |
| `payout_ratio` | TTM DPS x shares issued / net profit (parent) of the latest gpw.pl Notoria period, annualised (`payout_basis` = `full_year` or `ytd_annualized_Nm`). YTD caveat: seasonal businesses and one-off items distort annualisation. Fallback: GPW P/E x yield (`pe_trailing`). |
| `fcf_cover` | (CFO + capex) annualised / dividends paid (TTM DPS x shares); not computed for banks and insurers (investment flows are their business). |
| `profit_change_pct` | Same-length period one year earlier from the local fundamentals history (`gpwlib.fundamentals.save_snapshot` store); `null` when that history has not been built yet. |

### 2.1 Cash amounts

Net amount for N shares: tax = 19% of the gross amount **rounded to whole
złoty** (art. 63 § 1 Ordynacji podatkowej: under 50 gr dropped, 50 gr and more
up; the grosze rule of § 1a covers interest and discount - art. 30a ust. 1 pkt
1-3 - not dividends). Brokers may differ by up to 0.50 zł; check the statement.

### 2.2 IKE vs regular over N years

`ike_vs_regular`: same start (N shares at today's price), dividends reinvested
once a year in whole shares at the then price with the chosen broker's
commission (`gpwlib.brokers`), price and DPS growing `--growth-pct` a year
(default 0 - a conservative "no growth" case). Regular: dividend net of
rounded 19%, plus 19% on the capital gain when sold at the end. IKE: no tax
(conditions met: withdrawal after 60, or 55 with early-retirement rights).
IKZE: 10% of the whole withdrawal; the IKZE contribution deduction (PIT refund)
is deliberately excluded - gpw-account-planner models it.

## 3. Stability score (0-100)

| Part | Points | Rule |
|---|---|---|
| Consistency | 35 | 35 x years_paid / years_observed |
| Streak | 15 | 15 x min(consecutive_years, 7) / 7 (7 = full years in the 2019-2026 cache) |
| No cuts | 20 | 20 - 8 per cut - 12 per suspension (floor 0) |
| Growth | 15 | DPS CAGR 5y (else 3y): <= -10% -> 0; -10..0% -> 0..7.5; 0..10% -> 7.5..15; unknown -> 5 |
| Payout room | 15 | payout <= 60% -> 15; 60-100% -> 15..5 linearly; > 100% or loss -> 0; unknown -> 7.5 |

Rationale: the paying record and the absence of cuts dominate because managers
treat the dividend level as a commitment and cut only reluctantly (Lintner
1956; Brav, Graham, Harvey & Michaely 2005, "Payout policy in the 21st
century", JFE 77), and markets punish cuts and omissions hard (Michaely, Thaler
& Womack 1995, JF 50: about -7% on omission announcements). 60% payout leaves
room for a weaker year (Lintner's partial adjustment towards a target payout).
Confidence: `high` with >= 6 observed years and a payout ratio, `medium` with
>= 4 years, else `low`. A company that never paid scores 0.

## 4. Trap flags

| Code | Severity | Rule and source |
|---|---|---|
| `yield_above_2x_sector_median` | high | TTM gross yield > 2x the median of dividend payers in the same GPW sector index (`constituents.sector_membership`); fewer than 3 payers -> median of WIG20+mWIG40+sWIG80 payers. A very high yield usually means the price fell on bad news or the last payment was one-off - the "yield trap" of tradermonty `kanchi-dividend-sop`; high-yield deciles carry more cut risk (e.g. Conover, Jensen & Simpson 2016, "What difference do dividends make?", FAJ 72(6)). |
| `payout_above_100pct` | high | dividend larger than the profit -> paid from reserves or debt; not repeatable. |
| `net_loss` | high | latest period net loss. |
| `falling_eps` | medium | profit down > 10% y/y on the same-length period. |
| `one_off_special_dividend` | medium | a year's DPS > 2x the higher of the two previous paying years (>= 3 paying years; a steadily growing payer is not flagged), or ESPI wording "nadzwyczajna / specjalna / jednorazowa". Extrapolating a special dividend inflates the yield. |
| `fcf_below_dividend` | medium | FCF cover < 1. |
| `recent_cut_or_suspension` | medium | cut/suspension in the last two final years. |
| `board_proposes_no_dividend` | high | the newest ESPI dividend report after the last ex-date proposes no dividend. |
| `state_controlled_policy_risk` | info | Skarb Państwa holds a controlling or blocking stake, directly or through a state company (`dividend_sources.STATE_HOLDINGS`, stakes from company IR pages, verify). The Ministry of State Assets votes the dividend; payouts follow budget needs and policy (e.g. ORLEN, PGE, PZU, PKO BP, KGHM, ENEA, TAURON, JSW). |
| `bank_knf_dividend_policy` | info | banks: dividends depend on the yearly KNF stance (below). |

### 4.1 KNF stance on bank dividends

KNF publishes each December its stance on banks' dividend policy for the next
AGM season. **2026** (stance on dividends from 2025 profit,
`https://www.knf.gov.pl/knf/pl/komponenty/img/Stanowisko_KNF_ws_polityki_dywidendowej_w_2026_roku_96591.pdf`):
commercial banks meeting the basic criteria (capital ratios above
requirements, SREP/BION assessment) may pay up to **50%** of 2025 profit;
up to **75%** if, in addition, the NPL ratio is at most 5%; the criteria must
hold at the end of 2025 and on the resolution date; a new criterion on
sensitivity to NBP rate changes was added. Individual confirmation is given by
KNF letter (e.g. PKO BP, 2 March 2026, up to 75%). In **2020** KNF asked banks
(and insurers) to retain all 2019 profit because of COVID-19 - this is why bank
and PZU histories show a 2020 suspension that is not a company-specific signal.

## 5. Ex-date drop and dividend capture

`exdate_drops`: for each ex-date, `ratio_open = (last cum close - ex open) / DPS`,
`ratio_close` likewise with the close, and `ratio_close_mkt` net of the WIG move
that day (prev close x WIG return). Theory: in a world with taxes the price
falls by about DPS x (1 - t_div) / (1 - t_gain) (Elton & Gruber 1970, "Marginal
stockholder tax rates and the clientele effect", REStat 52). In Poland the
dividend and the capital gain are both taxed at 19%, so the expected ratio is
close to 1; observed medians below 1 mostly reflect the day's market move and
noise.

`capture_example`: buy for `--capture-amount` at the last cum close, sell at
the ex-date (price lower by the median observed ratio x DPS, or 1.0 when there
is no history), two commissions, dividend taxed 19% at source. The price loss
is a capital loss that can only offset other capital gains in PIT-38 (art.
30b), never the flat dividend tax (art. 30a) - so the report shows the result
with and without that later offset, and the break-even drop ratio. With a
ratio near 1 the strategy loses about 19% of the dividend plus fees.

## 6. Signals (generic `signals[]` contract)

`dividend_cut_risk` (`direction: avoid`) when at least **two** of
`payout_above_100pct`, `net_loss`, `falling_eps`, `fcf_below_dividend`,
`yield_above_2x_sector_median` hold (one alone is often benign, e.g. a single
loss year); `score` = 40 + 20 per extra flag. Evaluated by gpw-signal-postmortem
(skills/gpw-signal-postmortem/references/signal-contract.md).

## 7. Known limitations

* History depth = local cache (2019+ in Nov 2026); older years need
  `--source biznesradar`.
* Calendar-year aggregation can shift a payment between years when the AGM
  moves across New Year (rare on GPW).
* Payout ratio mixes TTM dividends with the latest (YTD) profit; banks and
  insurers report different P&L lines (only net profit is used).
* State holdings are a static table; re-check stakes on the company IR page.
* ESPI coverage = scanned days; offline runs only see cached days.

## References

* KSH (Dz.U. 2024 poz. 18) art. 348 § 4 (record date), art. 349 (interim dividend).
* Ustawa o PIT: art. 30a ust. 1 pkt 4 (19% on dividends), art. 41 ust. 4 (payer withholds), art. 30 ust. 1 pkt 10a (IKZE 10%), art. 30b (capital gains).
* Ordynacja podatkowa art. 63 § 1 and § 1a (rounding).
* Ustawa o IKE i IKZE (Dz.U. 2026 poz. 91).
* KDPW: T+2 settlement on GPW since 6 Oct 2014.
* KNF, Stanowisko ws. polityki dywidendowej w 2026 r. (Dec 2025), URL above.
* Lintner J. (1956), Distribution of incomes of corporations among dividends, retained earnings and taxes, AER 46(2).
* Brav A., Graham J., Harvey C., Michaely R. (2005), Payout policy in the 21st century, JFE 77(3).
* Michaely R., Thaler R., Womack K. (1995), Price reactions to dividend initiations and omissions, JF 50(2).
* Elton E., Gruber M. (1970), Marginal stockholder tax rates and the clientele effect, REStat 52(1).
* Conover C.M., Jensen G., Simpson M. (2016), What difference do dividends make?, FAJ 72(6).
* tradermonty/claude-trading-skills `kanchi-dividend-sop` (yield floor, yield-trap flag) and `value-dividend-screener` (MIT) - design inspiration.
