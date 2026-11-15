"""Pure calculation engine for gpw-account-planner (stdlib only, no I/O).

Monthly simulation of four Polish "wrappers" for long-term saving:

* ``regular`` - zwykły rachunek maklerski: 19% Belka tax on the gain when
  everything is sold at the end of the horizon (art. 30a/30b ustawy o PIT);
* ``ike``     - Indywidualne Konto Emerytalne: no tax if the withdrawal
  conditions are met (age 60 + contributions in 5 calendar years, art. 34
  ustawy o IKE i IKZE), otherwise taxed like a regular account ("zwrot");
* ``ikze``    - Indywidualne Konto Zabezpieczenia Emerytalnego: contributions
  deducted from income (refund at the marginal PIT rate, art. 26 ust. 1
  pkt 2b ustawy o PIT), 10% flat tax on the whole balance when withdrawn after
  65 (art. 30 ust. 1 pkt 10a); an early "zwrot" adds the whole amount to
  income taxed at the PIT scale;
* ``oki``     - Osobiste Konto Inwestycyjne (Dz.U. 2026 poz. 1098, from 2027):
  no PIT on income, instead an annual tax on the average value of the assets
  above the exemptions (gpwlib.tax.oki_tax).

Plus a "waterfall" combination: every fill order of IKE / IKZE / OKI (with the
regular account as the final overflow) is simulated and the order with the
highest computed net value is reported. This is arithmetic for the given
assumptions, not advice.

Modelling conventions (all documented in references/methodology.md):
* the simulation starts in January of ``start_year`` and runs ``years`` full
  calendar years; each month's contribution is invested at the start of the
  month, then the month's return and the product cost (TER) are applied;
* annual limits are spread evenly: a wrapper takes at most limit/12 a month
  (that is how a standing order works and it fills the limit exactly);
* every monthly buy in every wrapper is one broker order (commission with the
  broker's minimum fee); at the end every pot is sold with one order;
* IKZE refunds for year Y arrive in April of Y+1 (PIT filing); the refund of
  the last year arrives after the horizon and is added as cash;
* OKI tax is paid from the account at year end, base = mean of the 12
  month-end values of the year (approximation of art. 23 - verify).
"""

from __future__ import annotations

import itertools
import math
from typing import Dict, List, Optional, Sequence

from gpwlib import brokers, tax

WRAPPERS = ("regular", "ike", "ikze", "oki")
LIMITED = ("ike", "ikze", "oki")

# An early IKZE "zwrot" adds the whole amount to income taxed at the PIT scale
# (art. 34a ust. 2 ustawy o IKE i IKZE + art. 20 ustawy o PIT). A large balance
# crosses the 120 000 zł threshold, so it is taxed piecewise: 12% up to the
# threshold and 32% above (scale12 / linear / none - linear taxpayers pay the
# scale on non-business income), or 32% on everything for scale32 taxpayers
# whose other income already exceeds the threshold. Other income of the year
# and the tax-free amount are ignored (both are unknown here).


def early_ikze_tax(amount: float, pit: str) -> float:
    if amount <= 0:
        return 0.0
    if pit == "scale32":
        return round(amount * tax.PIT_SCALE_HIGH, 2)
    low = min(amount, tax.PIT_SCALE_THRESHOLD)
    return round(low * tax.PIT_SCALE_LOW + max(0.0, amount - low) * tax.PIT_SCALE_HIGH, 2)

# Fill orders whose net values differ by less than this (PLN) count as a tie
# (rounding noise); ties are resolved by ``_tie_key``.
TIE_TOLERANCE_PLN = 1.0


def limits_for_year(year: int, self_employed: bool = False, wage_growth_pct: float = 5.0) -> Dict:
    """IKE / IKZE limits for ``year``: exact for published years, projected otherwise.

    Limits are multiples of the forecast average wage (art. 13, 13a), so later
    years are projected from the latest published year with ``wage_growth_pct``.
    Years before the first published year use the first one (no back-casting).
    """
    known = sorted(tax.IKE_LIMITS)
    if year in tax.IKE_LIMITS:
        return {"year": year, "ike": tax.ike_limit(year), "ikze": tax.ikze_limit(year, self_employed),
                "source": "known", "source_note": tax.LIMITS_SOURCE.get(year, "")}
    base = known[-1] if year > known[-1] else known[0]
    k = (1 + wage_growth_pct / 100.0) ** max(0, year - base)
    return {"year": year, "ike": round(tax.ike_limit(base) * k, 2),
            "ikze": round(tax.ikze_limit(base, self_employed) * k, 2),
            "source": "projected", "source_note": "projected from {} at {}%/yr wage growth".format(base, wage_growth_pct)}


def oki_rate_for_year(year: int, rate_later_pct: float, nbp_rate_pct: Optional[float] = None) -> Optional[float]:
    """OKI rate in % (None before OKI exists). 2027 is fixed by law (art. 43)."""
    if year < tax.OKI_START_YEAR:
        return None
    if year == tax.OKI_START_YEAR:
        return tax.OKI_RATE_2027_PCT
    if nbp_rate_pct is not None:
        return tax.oki_rate_pct(year, nbp_rate_pct)
    return rate_later_pct


def default_config(**kw) -> Dict:
    cfg = {
        "monthly": 1000.0,
        "years": 25,
        "age": 30,
        "start_year": 2027,
        "inflation_pct": 3.0,
        "ter_pct": 0.5,
        "commission_pct": 0.0,
        "commission_min": 0.0,
        "pit": "scale12",
        "self_employed": False,
        "oki_qualified_pct": 0.0,
        "oki_rate_later_pct": tax.OKI_RATE_2027_PCT,
        "nbp_rate_pct": None,
        "oki_limit": None,
        "reinvest_refund": True,
        "wage_growth_pct": 5.0,
    }
    cfg.update(kw)
    validate_config(cfg)
    return cfg


def validate_config(cfg: Dict) -> None:
    if cfg["monthly"] <= 0:
        raise ValueError("monthly contribution must be > 0")
    if not (1 <= int(cfg["years"]) <= 60):
        raise ValueError("horizon must be 1-60 years")
    if not (16 <= cfg["age"] <= 100):
        raise ValueError("age must be 16-100 (IKE/IKZE can be opened from 16 with income / 18)")
    if cfg["start_year"] < min(tax.IKE_LIMITS):
        raise ValueError("start year must be >= {}".format(min(tax.IKE_LIMITS)))
    if cfg["pit"] not in tax.MARGINAL_RATES:
        raise ValueError("pit must be one of {}".format(", ".join(tax.MARGINAL_RATES)))
    if not (0 <= cfg["oki_qualified_pct"] <= 100):
        raise ValueError("OKI qualified share must be 0-100%")
    if not (0 <= cfg["ter_pct"] < 10):
        raise ValueError("TER must be 0-10%")
    if cfg["inflation_pct"] <= -100:
        raise ValueError("inflation must be > -100%")


class _Pot:
    __slots__ = ("kind", "value", "basis", "contributed", "fee_ter", "fee_comm", "years", "month_ends",
                 "oki_tax", "contrib_by_month")

    def __init__(self, kind: str):
        self.kind = kind
        self.value = 0.0
        self.basis = 0.0          # cost basis for Belka (contributions incl. buy commissions)
        self.contributed = 0.0    # money put in (user money + reinvested refunds)
        self.fee_ter = 0.0
        self.fee_comm = 0.0
        self.years = set()
        self.month_ends: List[float] = []
        self.oki_tax = 0.0
        self.contrib_by_month: List = []  # (month index, amount)

    def buy(self, amount: float, cfg: Dict, year: int, month_idx: int) -> None:
        if amount <= 0:
            return
        fee = brokers.commission(amount, cfg["commission_pct"], cfg["commission_min"])
        fee = min(fee, amount)
        self.value += amount - fee
        self.basis += amount
        self.contributed += amount
        self.fee_comm += fee
        self.years.add(year)
        self.contrib_by_month.append((month_idx, amount))


def _monthly_caps(year: int, cfg: Dict) -> Dict[str, float]:
    lim = limits_for_year(year, cfg["self_employed"], cfg["wage_growth_pct"])
    caps = {"ike": lim["ike"] / 12.0, "ikze": lim["ikze"] / 12.0}
    if year < tax.OKI_START_YEAR:
        caps["oki"] = 0.0
    elif cfg.get("oki_limit"):
        caps["oki"] = float(cfg["oki_limit"]) / 12.0
    else:
        caps["oki"] = math.inf  # the act sets no contribution limit (only exemption caps) - verify
    return caps


def simulate(order: Sequence[str], cfg: Dict, annual_return_pct: float, hold_years: int = 0) -> Dict:
    """Simulate a fill order (e.g. ``["ikze", "ike"]``; regular is the implicit overflow).

    ``hold_years``: extra years after the contribution horizon during which
    nothing is paid in but the money stays invested (e.g. waiting for age 65).
    """
    for w in order:
        if w not in LIMITED:
            raise ValueError("unknown wrapper {!r}".format(w))
    contrib_months = int(cfg["years"]) * 12
    months = contrib_months + int(hold_years) * 12
    g = (1 + annual_return_pct / 100.0) ** (1 / 12.0)
    ter_keep = (1 - cfg["ter_pct"] / 100.0) ** (1 / 12.0)
    rate = tax.MARGINAL_RATES[cfg["pit"]]
    q = cfg["oki_qualified_pct"] / 100.0
    pots = {w: _Pot(w) for w in WRAPPERS}
    ikze_year = 0.0
    pending_refund = 0.0
    refunds_total = 0.0
    refunds_cash = 0.0
    oki_years: List[Dict] = []
    first_year_split: Dict[str, float] = {}
    caps: Dict[str, float] = {}
    for m in range(months):
        year = cfg["start_year"] + m // 12
        month = m % 12 + 1
        if month == 1:
            caps = _monthly_caps(year, cfg)
            ikze_year = 0.0
        if month == 4 and pending_refund > 0:
            refunds_total += pending_refund
            if cfg["reinvest_refund"]:
                pots["regular"].buy(pending_refund, cfg, year, m)
            else:
                refunds_cash += pending_refund
            pending_refund = 0.0
        remaining = cfg["monthly"] if m < contrib_months else 0.0
        split = {}
        for w in order:
            amt = min(remaining, caps[w])
            if amt <= 0:
                continue
            pots[w].buy(amt, cfg, year, m)
            split[w] = amt
            remaining -= amt
            if w == "ikze":
                ikze_year += amt
        if remaining > 1e-9:
            pots["regular"].buy(remaining, cfg, year, m)
            split["regular"] = split.get("regular", 0.0) + remaining
        if m == 0:
            first_year_split = {k: round(v, 2) for k, v in split.items()}
        for p in pots.values():
            if p.value <= 0:
                continue
            p.value *= g
            fee = p.value * (1 - ter_keep)
            p.value -= fee
            p.fee_ter += fee
        pots["oki"].month_ends.append(pots["oki"].value)
        if month == 12:
            if ikze_year > 0:
                pending_refund += tax.ikze_refund(ikze_year, rate)
            oki = pots["oki"]
            r_pct = oki_rate_for_year(year, cfg["oki_rate_later_pct"], cfg["nbp_rate_pct"])
            ends = oki.month_ends[-12:]
            if r_pct is not None and any(v > 0 for v in ends):
                avg = sum(ends) / 12.0
                t = tax.oki_tax(avg * q, 0.0, avg * (1 - q), r_pct)
                paid = min(t["tax"], oki.value)
                oki.value -= paid
                oki.oki_tax += paid
                oki_years.append({"year": year, "avg_value": round(avg, 2), "taxable": t["taxable"],
                                  "rate_pct": r_pct, "tax": round(paid, 2)})
    end_age = cfg["age"] + int(cfg["years"]) + int(hold_years)
    result_pots = {}
    total_net = 0.0
    totals = {"contributed_own": 0.0, "fees": 0.0, "taxes": 0.0, "gross": 0.0}
    for w, p in pots.items():
        if p.contributed <= 0:
            continue
        exit_fee = brokers.commission(p.value, cfg["commission_pct"], cfg["commission_min"]) if p.value > 0 else 0.0
        exit_fee = min(exit_fee, p.value)
        after_fee = p.value - exit_fee
        cond = conditions(w, p, end_age, months)
        if w == "regular" or (w == "ike" and not cond["met"]):
            exit_tax = tax.belka(after_fee - p.basis)
            tax_kind = "belka"
        elif w == "ike":
            exit_tax, tax_kind = 0.0, "none"
        elif w == "ikze":
            if cond["met"]:
                exit_tax, tax_kind = round(after_fee * tax.IKZE_WITHDRAWAL_RATE, 2), "ikze_10"
            else:
                exit_tax, tax_kind = early_ikze_tax(after_fee, cfg["pit"]), "ikze_scale"
        else:
            exit_tax, tax_kind = 0.0, "oki_annual"
        net = after_fee - exit_tax
        total_net += net
        result_pots[w] = {
            "contributed": round(p.contributed, 2),
            "gross_value": round(p.value, 2),
            "fees_ter": round(p.fee_ter, 2),
            "fees_commission": round(p.fee_comm + exit_fee, 2),
            "tax_exit": round(exit_tax, 2),
            "tax_exit_kind": tax_kind,
            "tax_oki_annual": round(p.oki_tax, 2),
            "net_value": round(net, 2),
            "contribution_years": len(p.years),
            "conditions": cond,
        }
        totals["fees"] += p.fee_ter + p.fee_comm + exit_fee
        totals["taxes"] += exit_tax + p.oki_tax
        totals["gross"] += p.value
    last_refund = pending_refund  # refund for the final year arrives after the horizon
    refunds_total += last_refund
    total_net += refunds_cash + last_refund
    own = cfg["monthly"] * contrib_months
    infl = (1 + cfg["inflation_pct"] / 100.0) ** (months / 12.0)
    return {
        "order": list(order),
        "return_pct": annual_return_pct,
        "pots": result_pots,
        "contributed_own": round(own, 2),
        "ikze_refunds_total": round(refunds_total, 2),
        "ikze_refunds_reinvested": round(refunds_total - refunds_cash - last_refund, 2),
        "ikze_refunds_cash": round(refunds_cash + last_refund, 2),
        "fees_total": round(totals["fees"], 2),
        "taxes_total": round(totals["taxes"], 2),
        "net_value": round(total_net, 2),
        "net_value_real": round(total_net / infl, 2),
        "contributed_own_real": round(_real_contributions(cfg), 2),
        "net_gain": round(total_net - own, 2),
        "first_month_split": first_year_split,
        "oki_tax_years": oki_years,
        "end_age": end_age,
        "hold_years": int(hold_years),
    }


def _real_contributions(cfg: Dict) -> float:
    """Contributions expressed in today's money (each deflated to the start)."""
    mi = (1 + cfg["inflation_pct"] / 100.0) ** (1 / 12.0)
    return sum(cfg["monthly"] / mi ** m for m in range(int(cfg["years"]) * 12))


def conditions(wrapper: str, pot: "_Pot", end_age: int, months: int) -> Dict:
    """Withdrawal conditions at the end of the horizon (art. 34 / 34a ustawy o IKE i IKZE)."""
    years_ok = len(pot.years) >= tax.MIN_CONTRIBUTION_YEARS
    if wrapper == "ike":
        # alternative condition: more than half of the contributions paid >= 5 years before withdrawal
        early = sum(a for mi, a in pot.contrib_by_month if months - mi >= 60)
        half_ok = pot.contributed > 0 and early > pot.contributed / 2
        age_ok = end_age >= tax.IKE_TAX_FREE_AGE
        return {"met": age_ok and (years_ok or half_ok), "age_ok": age_ok, "years_ok": years_ok or half_ok,
                "required_age": tax.IKE_TAX_FREE_AGE}
    if wrapper == "ikze":
        age_ok = end_age >= tax.IKZE_WITHDRAWAL_AGE
        return {"met": age_ok and years_ok, "age_ok": age_ok, "years_ok": years_ok,
                "required_age": tax.IKZE_WITHDRAWAL_AGE}
    return {"met": True, "age_ok": True, "years_ok": True, "required_age": None}


def candidate_orders() -> List[List[str]]:
    """Every fill order of a subset of IKE/IKZE/OKI (regular is always the final overflow)."""
    out: List[List[str]] = [[]]
    for k in range(1, len(LIMITED) + 1):
        for perm in itertools.permutations(LIMITED, k):
            out.append(list(perm))
    return out


def _tie_key(order: Sequence[str]):
    """Tie-break among orders with (nearly) equal net value.

    1. Prefer an order that starts with IKE: when its conditions are not met
       IKE is taxed exactly like the regular account, so it is never worse,
       and it keeps the option of a tax-free withdrawal after 60.
    2. Otherwise the shorter order (fewer accounts, fewer orders and fees).
    """
    return (0 if order[:1] == ["ike"] else 1, len(order))


def best_order(cfg: Dict, annual_return_pct: float, hold_years: int = 0) -> Dict:
    """Fill order with the highest net value; near-ties resolved by ``_tie_key``."""
    best = None
    ranking = []
    for order in candidate_orders():
        r = simulate(order, cfg, annual_return_pct, hold_years)
        ranking.append({"order": order, "net_value": r["net_value"]})
        if best is None or r["net_value"] > best["net_value"] + TIE_TOLERANCE_PLN or (
                abs(r["net_value"] - best["net_value"]) <= TIE_TOLERANCE_PLN and _tie_key(order) < _tie_key(best["order"])):
            best = r
    ranking.sort(key=lambda x: (-round(x["net_value"]), _tie_key(x["order"])))
    return {"best": best, "ranking": ranking}


def plan(cfg: Dict, scenarios: Sequence[float]) -> Dict:
    """Full computation: standalone wrappers + waterfall per scenario."""
    if not scenarios:
        raise ValueError("at least one return scenario is needed")
    scenarios = list(scenarios)
    ref = scenarios[len(scenarios) // 2]
    standalone = {}
    for s in scenarios:
        standalone[str(s)] = {
            "regular": simulate([], cfg, s),
            "ike": simulate(["ike"], cfg, s),
            "ikze": simulate(["ikze"], cfg, s),
            "oki": simulate(["oki"], cfg, s),
        }
    wf_ref = best_order(cfg, ref)
    ref_order = wf_ref["best"]["order"]
    waterfall = {}
    best_per_scenario = {}
    for s in scenarios:
        waterfall[str(s)] = simulate(ref_order, cfg, s)
        best_per_scenario[str(s)] = best_order(cfg, s)["best"]["order"] if s != ref else ref_order
    years = [limits_for_year(cfg["start_year"] + i, cfg["self_employed"], cfg["wage_growth_pct"])
             for i in range(int(cfg["years"]))]
    oki_rates = [{"year": y["year"], "rate_pct": oki_rate_for_year(y["year"], cfg["oki_rate_later_pct"], cfg["nbp_rate_pct"])}
                 for y in years]
    end_age = cfg["age"] + int(cfg["years"])
    hold = None
    if end_age < tax.IKZE_WITHDRAWAL_AGE:
        # What if contributions stop at the end of the horizon but the money is
        # left invested until IKZE (65) - and therefore also IKE (60) - is tax-free?
        hy = tax.IKZE_WITHDRAWAL_AGE - end_age
        hb = best_order(cfg, ref, hy)["best"]
        hold = {"hold_years": hy, "withdraw_age": tax.IKZE_WITHDRAWAL_AGE, "scenario_pct": ref,
                "standalone": {"regular": simulate([], cfg, ref, hy), "ike": simulate(["ike"], cfg, ref, hy),
                               "ikze": simulate(["ikze"], cfg, ref, hy), "oki": simulate(["oki"], cfg, ref, hy)},
                "waterfall": hb}
    return {
        "reference_scenario_pct": ref,
        "hold_to_65": hold,
        "scenarios_pct": scenarios,
        "standalone": standalone,
        "waterfall": {"order": ref_order, "results": waterfall, "ranking_reference": wf_ref["ranking"],
                      "best_order_per_scenario": best_per_scenario,
                      "order_differs_by_scenario": any(o != ref_order for o in best_per_scenario.values())},
        "limits": years,
        "oki_rates": oki_rates,
        "end_age": end_age,
        "warnings": warnings(cfg),
    }


def warnings(cfg: Dict) -> List[str]:
    """Warning keys (translated by the CLI)."""
    w = []
    end_age = cfg["age"] + int(cfg["years"])
    if end_age < tax.IKE_TAX_FREE_AGE:
        w.append("ike_age")
    if end_age < tax.IKZE_WITHDRAWAL_AGE:
        w.append("ikze_age")
    if int(cfg["years"]) < tax.MIN_CONTRIBUTION_YEARS:
        w.append("short_horizon")
    if cfg["pit"] == "none":
        w.append("no_pit")
    if cfg["oki_qualified_pct"] < 100:
        w.append("oki_global")
    if cfg["nbp_rate_pct"] is None:
        w.append("oki_rate_assumed")
    if cfg["commission_min"] > 0 and cfg["monthly"] < 1000:
        w.append("min_fee_small_orders")
    if cfg["start_year"] < tax.OKI_START_YEAR:
        w.append("oki_not_yet")
    return w
