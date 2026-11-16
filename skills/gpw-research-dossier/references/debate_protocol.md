# Bull/bear debate protocol (gpw-research-dossier)

The debate turns a dossier into a structured argument a human can decide on.
It is **structure, not alpha**: the aim is to make the strongest case on each
side visible, tie every claim to a number a script produced, and write down
in advance what would prove the idea wrong. It never ends in "buy" or "sell".

Contents: 1. Roles · 2. Order and format · 3. Anti-bias rules · 4. Risk manager ·
5. Pre-mortem · 6. Verdict inputs · 7. Guardrail (validate-debate) · 8. Evidence

## 1. Roles (TradingAgents, adapted)

TradingAgents (Xiao, Sun, Luo & Wang 2024, arXiv:2412.20138) splits a trading
decision into analysts, a bull and a bear researcher, a trader and a risk
team. Here:

| TradingAgents role | In this skill |
|---|---|
| Fundamental / sentiment / news / technical analysts | The other GPW skills. Their JSON reports are the dossier sections; Claude adds **no** analysis that is not in a section. |
| Bear researcher | Claude, **first**: 3-5 points against. |
| Bull researcher | Claude, second: 3-5 points for. |
| Debate rounds | At most one optional `rebuttals[]` round (each rebuttal also cites fact ids). More rounds add words, not evidence. |
| Risk management team | Claude as risk manager: size from the `gpw-position-sizer` command, kill criteria, what would change the view. |
| Trader / fund manager (final decision) | **The user.** Claude writes `verdict_inputs` only. |

Why adapted: the TradingAgents evaluation covers about three months and a few
US large caps, with look-ahead concerns (the model may know what happened),
so it is no evidence that LLM debates make money. What transfers is the
discipline: separated roles, both sides argued, risk owned by a separate step.

## 2. Order and format

1. `build TICKER` (fresh sections; mention stale sections first).
2. `debate-template TICKER` writes the skeleton with `available_fact_ids`.
3. Fill **bear** first, then **bull**. Each point:
   `{"claim": "...", "fact_ids": ["valuation.implied_growth_pct", ...], "strength": "high|medium|low"}`.
   3-5 points per side; a claim is one falsifiable sentence with the number in it.
4. `risk_manager`, `pre_mortem`, `unknowns`, `verdict_inputs`, optionally `thesis`.
5. `validate-debate FILE` until it passes; fix errors, do not argue with them.
6. Show the user the debate (bear section first) and the validation line.
7. Only if the user wants to record the idea: `to-thesis FILE` and show the
   command. Never run it.

## 3. Anti-bias rules

| Rule | Why |
|---|---|
| **Bear first.** | Whatever is written first anchors the rest (Tversky & Kahneman 1974). Writing the case against first counters confirmation bias, the tendency to look for supporting evidence (Nickerson 1998). |
| **Steelman, not strawman.** Each side in its strongest honest form; a weak point on the side you lean against is a failure. | "Consider the opposite" is one of the few debiasing instructions that works in experiments (Lord, Lepper & Preston 1984). |
| **No claim without a fact id.** Every bull and bear point cites >= 1 id from the dossier. | Makes every sentence checkable and stops facts from memory or the internet entering the record. |
| **Same number, both sides only with a reason.** A fact may be cited by both sides only if the claim explains why it cuts both ways (e.g. a high ROE that the price already discounts). | Prevents double counting. |
| **List the unknowns.** Every missing or stale section, unknown F-score criteria, proxy values (net-debt proxy, annualised YTD data) go to `unknowns[]`. | Absence of evidence is not evidence; the user must see what was not checked. The validator rejects an empty list when the dossier has gaps. |
| **Outside view.** Where a section gives a base rate (screen backtest hit rate, drift study, sector uptrend ratio), use it before the company story. | Reference-class forecasting beats the inside view (Kahneman & Lovallo 1993). |
| **Strength is about evidence, not conviction.** `high` = a computed number far from its threshold; `low` = a proxy, a small sample or a stale fact. | Keeps tone from substituting for data. |
| **Stale facts are flagged.** Citing a fact from a stale section is allowed but is warned about and must be mentioned. | CONVENTIONS rule 2: freshness is a gate. |
| **No price targets, no "buy"/"sell".** | CONVENTIONS rule 6. |

## 4. Risk manager

- **Position size:** copy the `next_steps.position_sizer.command` from the
  dossier (entry/stop from the journal plan, else the watchlist, else the
  trend screener's pivot and structural stop; risk % from
  `gpw-exposure-coach`, else the sizer's 1% default). The user replaces
  `ACCOUNT_PLN`. Claude never computes the share count itself.
- **Kill criteria (>= 1):** observable, dated or priced conditions, e.g.
  "close below 1 229 zł" (`trend.stop_structure`), "annual report ROE below
  25%" (`quality.roe_pct`, `events.next_earnings_date`). They become the
  journal's `--kill` entries.
- **What would change the view (>= 1):** evidence that would flip the
  balance in either direction (regime turns to correction, short interest
  becomes crowded, valuation range position changes).

## 5. Pre-mortem

Klein (2007, "Performing a project premortem", HBR) and Kahneman (2011,
*Thinking, Fast and Slow*, ch. 24): imagine it is a year later and the
position lost 30%; write the most likely reasons. This surfaces risks the
bear list missed because it legitimises dissent. At least one item; cite
fact ids where the reason rests on a dossier number.

## 6. Verdict inputs (not a verdict)

```
"verdict_inputs": {
  "summary": "balance of evidence in one or two sentences",
  "evidence_balance": "bear_heavier | balanced | bull_heavier | insufficient_evidence",
  "strongest_bear_fact_ids": [...], "strongest_bull_fact_ids": [...],
  "conditions_to_revisit": ["2027-04-30 annual report", ...],
  "not_a_recommendation": true
}
```

Use `insufficient_evidence` when key sections are missing or stale. The
summary describes evidence; it never tells the user what to do.

## 7. Guardrail (`validate-debate`)

Deterministic checks (errors make exit code 1):

- bear and bull exist, each with 3-5 points, each claim >= 12 characters;
- every point cites >= 1 fact id and every cited id exists in the dossier
  (also for kill criteria, change-view, pre-mortem and verdict ids);
- >= 1 kill criterion, >= 1 change-view item, >= 1 pre-mortem item;
- `unknowns[]` not empty when the dossier has non-optional gaps;
- `evidence_balance` in the allowed set and `not_a_recommendation: true`;
- no `<<` placeholder left (outside the optional `thesis` block).

Warnings: duplicate claims, a side built from one section only, citations of
stale facts, call-to-action words in the summary, no sizer command, unfilled
thesis block.

`to-thesis` runs the same validation and refuses an invalid debate.

## 8. Evidence and limits

- Xiao Y., Sun E., Luo D., Wang W. (2024). *TradingAgents: Multi-Agents LLM
  Financial Trading Framework*. arXiv:2412.20138.
- Tversky A., Kahneman D. (1974). Judgment under uncertainty: heuristics and
  biases. *Science* 185.
- Lord C., Lepper M., Preston E. (1984). Considering the opposite: a
  corrective strategy for social judgment. *JPSP* 47(6).
- Kahneman D., Lovallo D. (1993). Timid choices and bold forecasts.
  *Management Science* 39(1).
- Nickerson R. (1998). Confirmation bias: a ubiquitous phenomenon in many
  guises. *Review of General Psychology* 2(2).
- Klein G. (2007). Performing a project premortem. *Harvard Business Review*.
- Kahneman D. (2011). *Thinking, Fast and Slow*.

Limit: the guardrail checks structure and citations, not whether a claim
reads the number correctly. Claude must quote values as they appear in
`facts_digest`; the user can check every one.
