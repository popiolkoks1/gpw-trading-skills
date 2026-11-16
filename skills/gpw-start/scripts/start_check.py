#!/usr/bin/env python3
"""GPW Start - "am I ready to invest, and where do I begin?" for beginners.

A deterministic readiness check that turns a few facts about the user's
finances into an ordered, explained action plan:

0. budget surplus (can you save at all?),
1. emergency fund (3-6 months of expenses in safe, liquid money),
2. expensive debt first (a guaranteed "return" equal to its interest rate),
3. horizon -> which assets make sense at all (money needed in < 3 years
   does not belong in shares),
4. loss tolerance -> an indicative equity-share range,
5. tax wrappers (IKE / IKZE / OKI) before a regular account,
6. broad, cheap ETFs as the core; single stocks only as a small satellite.

The order follows standard personal-finance practice (e.g. the "financial
pyramid" taught by KNF/UKNF and NBP educational material, Bogleheads' "prime
directive") and the reasoning for every threshold is in
references/readiness_method.md. The script never recommends a security.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import fmt, tax  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T  # noqa: E402

VERSION = "1.0.0"

# --- thresholds (justified in references/readiness_method.md) -------------
EMERGENCY_MONTHS_STABLE = 3        # stable job / two incomes
EMERGENCY_MONTHS_UNSTABLE = 6      # B2B, single income, commission pay, dependants
EXPENSIVE_DEBT_RATE_PCT = 8.0      # above this, repaying beats expected net equity return
HORIZON_NO_EQUITY_Y = 3            # money needed sooner: no shares
HORIZON_MIXED_Y = 7                # 3-7 years: limited shares
EQUITY_CRASH_DRAWDOWN = 0.55       # broad equity crashes of the past: -50..-60% (2008, WIG -65%)
SATELLITE_MAX_PCT = 10             # single stocks for learners: at most ~10% of investments
TOLERANCE_CHOICES = [10, 20, 30, 40, 50]
HORIZON_CAP = {"short": 0, "mixed": 50, "long": 100}

STRINGS = {
    "title": {"pl": "Start na giełdzie – Twój plan", "en": "Getting started – your plan"},
    "answer": {"pl": "Odpowiedź w skrócie", "en": "Short answer"},
    "stage": {"pl": "Twój etap", "en": "Your stage"},
    "stage_0": {"pl": "Etap 0 – najpierw budżet: w tej chwili nie zostaje Ci nic do odłożenia.",
                "en": "Stage 0 – budget first: right now nothing is left to save."},
    "stage_1": {"pl": "Etap 1 – poduszka finansowa: zanim kupisz pierwszą akcję lub ETF, zbuduj rezerwę na nagłe wydatki.",
                "en": "Stage 1 – emergency fund: before your first share or ETF, build a cash reserve."},
    "stage_2": {"pl": "Etap 2 – drogie długi: spłata kredytu oprocentowanego powyżej {r} da Ci pewny „zysk”, którego giełda nie gwarantuje.",
                "en": "Stage 2 – expensive debt: repaying a loan above {r} is a guaranteed 'return' the market cannot promise."},
    "stage_3": {"pl": "Etap 3 – możesz zacząć inwestować: regularnie, w szeroko zdywersyfikowane fundusze, najlepiej przez IKE/IKZE/OKI.",
                "en": "Stage 3 – you can start investing: regularly, in broadly diversified funds, ideally via IKE/IKZE/OKI."},
    "stage_3_short": {"pl": "Etap 3 – możesz oszczędzać, ale Twój horyzont jest krótki: akcje nie są dla tych pieniędzy dobrym miejscem.",
                      "en": "Stage 3 – you can save, but your horizon is short: shares are not a good place for this money."},
    "numbers": {"pl": "Twoje liczby", "en": "Your numbers"},
    "surplus": {"pl": "Nadwyżka miesięczna (dochód − wydatki)", "en": "Monthly surplus (income − expenses)"},
    "ef_target": {"pl": "Cel poduszki finansowej ({m} mies. wydatków)", "en": "Emergency-fund target ({m} months of expenses)"},
    "ef_have": {"pl": "Masz w bezpiecznych oszczędnościach", "en": "Safe savings you have"},
    "ef_gap": {"pl": "Brakuje do poduszki", "en": "Missing to the target"},
    "ef_months": {"pl": "Czas uzupełnienia poduszki przy obecnej nadwyżce", "en": "Time to fill it at the current surplus"},
    "months": {"pl": "{n} mies.", "en": "{n} months"},
    "debt_exp": {"pl": "Drogie długi (> {r})", "en": "Expensive debt (> {r})"},
    "investable": {"pl": "Kwota, którą rozsądnie możesz inwestować miesięcznie", "en": "Amount you can sensibly invest monthly"},
    "horizon": {"pl": "Horyzont", "en": "Horizon"},
    "years": {"pl": "{n} lat", "en": "{n} years"},
    "horizon_short": {"pl": "krótki (< {a} lat) – bez akcji", "en": "short (< {a} years) – no shares"},
    "horizon_mixed": {"pl": "średni ({a}–{b} lat) – ograniczony udział akcji", "en": "medium ({a}–{b} years) – limited shares"},
    "horizon_long": {"pl": "długi (≥ {b} lat) – akcje mogą być główną częścią", "en": "long (≥ {b} years) – shares can be the core"},
    "equity_range": {"pl": "Orientacyjny udział akcji w inwestycjach", "en": "Indicative share of equities"},
    "equity_why": {
        "pl": ("Dlaczego: szeroki rynek akcji w kryzysach tracił ok. 50–60% (2008: MSCI World ≈ −55%, WIG ≈ −65%). "
               "Mówisz, że wytrzymasz spadek całego portfela o {t}%, więc akcje nie powinny przekraczać ok. {mx}% "
               "(bo {mx}% × ~55% ≈ {t}%). Resztę trzymasz w bezpiecznych aktywach (obligacje skarbowe, lokaty, fundusze gotówkowe)."),
        "en": ("Why: broad equity markets lost about 50–60% in crashes (2008: MSCI World ≈ −55%, WIG ≈ −65%). "
               "You say you can sit through a {t}% fall of the whole portfolio, so equities should stay below about {mx}% "
               "({mx}% × ~55% ≈ {t}%). The rest sits in safe assets (treasury bonds, deposits, money-market funds)."),
    },
    "equity_capped": {"pl": " Krótszy horyzont obniża górną granicę do {c}%.", "en": " Your horizon caps the upper bound at {c}%."},
    "steps": {"pl": "Co zrobić – krok po kroku", "en": "What to do – step by step"},
    "s_budget": {"pl": "Spisz wydatki z 2–3 ostatnich miesięcy i znajdź choć 5–10% dochodu do odkładania. Bez nadwyżki nie ma z czego inwestować.",
                 "en": "List your spending from the last 2–3 months and find at least 5–10% of income to save. Without a surplus there is nothing to invest."},
    "s_ef": {"pl": "Odkładaj {a} miesięcznie na poduszkę (konto oszczędnościowe, lokata lub obligacje skarbowe), aż uzbierasz {g}.",
             "en": "Put {a} a month into the emergency fund (savings account, deposit or treasury bonds) until you have {g}."},
    "s_debt": {"pl": "Nadpłacaj najpierw dług „{n}” ({r}) – zaczynając od najwyżej oprocentowanego.",
               "en": "Overpay the debt '{n}' ({r}) first – highest interest rate first."},
    "s_learn": {"pl": "Poznaj podstawy (słowniczek w tym skillu): akcja, ETF, dywersyfikacja, koszty (TER, prowizje), podatek Belki, IKE/IKZE/OKI.",
                "en": "Learn the basics (glossary in this skill): share, ETF, diversification, costs (TER, commissions), Belka tax, IKE/IKZE/OKI."},
    "s_wrapper": {"pl": "Wybierz konto: policz w gpw-account-planner, ile zyskasz na IKE / IKZE / OKI względem zwykłego rachunku.",
                  "en": "Pick the account: compute in gpw-account-planner how much IKE / IKZE / OKI saves vs a regular account."},
    "s_etf": {"pl": "Ustal stały plan wpłat w szerokie ETF-y (gpw-etf-planner): ten sam dzień, ta sama kwota, bez zgadywania „dobrego momentu”.",
              "en": "Set a fixed contribution plan into broad ETFs (gpw-etf-planner): same day, same amount, no timing the market."},
    "s_safe": {"pl": "Pieniądze potrzebne za mniej niż {a} lata trzymaj w bezpiecznych instrumentach (obligacje skarbowe, lokaty), nie w akcjach.",
               "en": "Money you need within {a} years belongs in safe instruments (treasury bonds, deposits), not shares."},
    "s_satellite": {"pl": "Pojedyncze spółki – jeśli chcesz się uczyć – maks. ok. {p}% inwestycji, z dziennikiem (gpw-trade-journal) i kalkulatorem pozycji (gpw-position-sizer). Zacznij od trybu „na niby” (--paper).",
                    "en": "Single stocks – if you want to learn – at most about {p}% of investments, with a journal (gpw-trade-journal) and the position sizer (gpw-position-sizer). Start in paper mode (--paper)."},
    "s_review": {"pl": "Raz w roku sprawdź portfel (gpw-portfolio-check) i wróć do proporcji. Nie zmieniaj planu po spadkach – to normalna część inwestowania.",
                 "en": "Once a year check the portfolio (gpw-portfolio-check) and rebalance. Don't change the plan after a fall – falls are normal."},
    "warnings": {"pl": "Ostrzeżenia", "en": "Warnings"},
    "w_negative": {"pl": "Wydajesz więcej, niż zarabiasz. Inwestowanie nie rozwiąże tego problemu – najpierw budżet.",
                   "en": "You spend more than you earn. Investing will not fix that – budget first."},
    "w_expensive": {"pl": "Masz drogi dług ({n}, {r}). Żadna rozsądna inwestycja nie da pewnych {r} rocznie po podatku.",
                    "en": "You have expensive debt ({n}, {r}). No sensible investment returns a certain {r} a year after tax."},
    "w_low_tolerance": {"pl": "Przy tolerancji spadku {t}% akcje mogą być tylko małą częścią. To w porządku – lepiej mniej akcji niż sprzedaż w panice.",
                        "en": "With {t}% loss tolerance equities can only be a small part. That's fine – fewer shares beats panic selling."},
    "w_no_experience": {"pl": "Nie masz doświadczenia: unikaj ETF-ów lewarowanych i „short” (w nazwie LV, ST), kryptowalut, CFD i forexu – to narzędzia spekulacyjne.",
                        "en": "No experience: avoid leveraged and short ETFs (LV, ST in the name), crypto, CFDs and forex – these are speculative tools."},
    "w_scam": {"pl": "Nikt uczciwy nie gwarantuje zysku. Sprawdzaj firmy na liście ostrzeżeń publicznych KNF i w rejestrze KNF przed wpłatą pieniędzy.",
               "en": "No honest firm guarantees profit. Check firms on the KNF public warning list and in the KNF register before sending money."},
    "w_age_ike": {"pl": "IKE daje zwolnienie z podatku dopiero przy wypłacie po 60. roku życia (lub 55 z uprawnieniami emerytalnymi), IKZE – po 65. Wcześniejsza wypłata oznacza podatek.",
                  "en": "IKE is tax-free only when withdrawn after age 60 (or 55 with pension rights), IKZE after 65. Earlier withdrawal is taxed."},
    "limits": {"pl": "Limity wpłat {y}: IKE {ike}, IKZE {ikze} ({ikze_se} dla prowadzących firmę). OKI (od 2027): bez limitu wpłat, zamiast podatku Belki roczny podatek od wartości aktywów (0,85% w 2027) ponad zwolnienie do 100 000 zł dla aktywów kwalifikowanych.",
               "en": "Contribution limits {y}: IKE {ike}, IKZE {ikze} ({ikze_se} self-employed). OKI (from 2027): no contribution limit; instead of the Belka tax an annual asset tax (0.85% in 2027) above an exemption of up to PLN 100,000 for qualified assets."},
    "next": {"pl": "Następne narzędzia", "en": "Next tools"},
    "sources": {"pl": "Źródła", "en": "Sources"},
    "inputs": {"pl": "Założenia, które podałeś", "en": "Your inputs"},
    "stable": {"pl": "stabilny", "en": "stable"},
    "unstable": {"pl": "niestabilny", "en": "unstable"},
    "income_type": {"pl": "Dochód", "en": "Income"},
    "tolerance": {"pl": "Akceptowalny spadek portfela", "en": "Acceptable portfolio fall"},
    "experience": {"pl": "Doświadczenie", "en": "Experience"},
    "exp_none": {"pl": "brak", "en": "none"},
    "exp_basic": {"pl": "podstawowe", "en": "basic"},
    "exp_some": {"pl": "pewne", "en": "some"},
    "none": {"pl": "brak", "en": "none"},
}


class StartError(ValueError):
    pass


def parse_debts(items: List[str]) -> List[Dict]:
    """'karta:5000:21.5' -> {'name': 'karta', 'amount': 5000.0, 'rate_pct': 21.5}."""
    out = []
    for raw in items or []:
        parts = [p.strip() for p in raw.replace(",", ".").split(":")] if raw.count(":") == 2 else None
        if not parts:
            raise StartError("debt must be NAME:AMOUNT:RATE_PCT, e.g. karta:5000:21.5 (got {!r})".format(raw))
        try:
            amount, rate = float(parts[1]), float(parts[2])
        except ValueError:
            raise StartError("debt amount and rate must be numbers: {!r}".format(raw))
        if amount < 0 or rate < 0:
            raise StartError("debt amount and rate must be >= 0: {!r}".format(raw))
        out.append({"name": parts[0] or "dług", "amount": amount, "rate_pct": rate})
    return sorted(out, key=lambda d: -d["rate_pct"])


def equity_range(tolerance_pct: float, horizon_years: float) -> Dict:
    """Indicative equity share (%) of invested money.

    Upper bound: the share whose crash loss (share x ~55%) equals the stated
    tolerance, rounded down to 5 pp and capped by the horizon. Lower bound is
    half of the upper bound - a range, not a point, because the right number
    depends on things the script cannot know.
    """
    if tolerance_pct <= 0:
        raise StartError("loss tolerance must be > 0%")
    raw = min(100.0, tolerance_pct / (EQUITY_CRASH_DRAWDOWN * 100) * 100)
    hi = int(raw // 5 * 5)
    band = horizon_band(horizon_years)
    cap = HORIZON_CAP[band]
    capped = hi > cap
    hi = min(hi, cap)
    lo = int(hi / 2 // 5 * 5)
    return {"low": lo, "high": hi, "uncapped_high": int(raw // 5 * 5), "horizon_cap": cap, "capped_by_horizon": capped}


def horizon_band(years: float) -> str:
    if years < HORIZON_NO_EQUITY_Y:
        return "short"
    if years < HORIZON_MIXED_Y:
        return "mixed"
    return "long"


def assess(*, income: float, expenses: float, savings: float, debts: List[Dict], horizon_years: float,
           age: Optional[int], tolerance_pct: float, unstable_income: bool = False,
           experience: str = "none", year: Optional[int] = None) -> Dict:
    if income < 0 or expenses < 0 or savings < 0:
        raise StartError("income, expenses and savings must be >= 0")
    if horizon_years <= 0:
        raise StartError("horizon must be > 0 years")
    surplus = round(income - expenses, 2)
    months = EMERGENCY_MONTHS_UNSTABLE if unstable_income else EMERGENCY_MONTHS_STABLE
    ef_target = round(expenses * months, 2)
    ef_gap = round(max(0.0, ef_target - savings), 2)
    expensive = [d for d in debts if d["rate_pct"] > EXPENSIVE_DEBT_RATE_PCT and d["amount"] > 0]
    band = horizon_band(horizon_years)
    eq = equity_range(tolerance_pct, horizon_years)

    if surplus <= 0:
        stage = 0
    elif ef_gap > 0:
        stage = 1
    elif expensive:
        stage = 2
    else:
        stage = 3
    # How much of the surplus can go to investing now: everything only at stage 3;
    # while building the emergency fund, the surplus goes there first.
    investable = surplus if stage == 3 else 0.0
    ef_months_needed = (int(-(-ef_gap // surplus)) if surplus > 0 and ef_gap > 0 else 0)

    steps: List[Dict] = []
    if stage == 0:
        steps.append({"key": "s_budget"})
    if ef_gap > 0 and surplus > 0:
        steps.append({"key": "s_ef", "a": surplus, "g": ef_target})
    for d in expensive:
        steps.append({"key": "s_debt", "n": d["name"], "r": d["rate_pct"]})
    steps.append({"key": "s_learn"})
    if band == "short":
        steps.append({"key": "s_safe"})
    else:
        steps += [{"key": "s_wrapper"}, {"key": "s_etf"}]
        if experience != "none" or band == "long":
            steps.append({"key": "s_satellite"})
    steps.append({"key": "s_review"})

    warnings: List[Dict] = []
    if surplus < 0:
        warnings.append({"key": "w_negative"})
    for d in expensive:
        warnings.append({"key": "w_expensive", "n": d["name"], "r": d["rate_pct"]})
    if eq["high"] <= 20 and band != "short":
        warnings.append({"key": "w_low_tolerance", "t": tolerance_pct})
    if experience == "none":
        warnings.append({"key": "w_no_experience"})
    if age is not None and band != "short" and age + horizon_years < tax.IKE_TAX_FREE_AGE:
        warnings.append({"key": "w_age_ike"})
    warnings.append({"key": "w_scam"})

    year = year or tax.latest_limit_year()
    if year not in tax.IKE_LIMITS:
        year = tax.latest_limit_year()
    return {
        "skill": "gpw-start",
        "version": VERSION,
        "stage": stage,
        "stage_key": "stage_3_short" if stage == 3 and band == "short" else "stage_{}".format(stage),
        "numbers": {
            "surplus": surplus,
            "emergency_months": months,
            "emergency_target": ef_target,
            "savings": savings,
            "emergency_gap": ef_gap,
            "emergency_months_to_fill": ef_months_needed,
            "expensive_debt_total": round(sum(d["amount"] for d in expensive), 2),
            "investable_monthly": round(investable, 2),
        },
        "horizon": {"years": horizon_years, "band": band},
        "equity_range_pct": eq,
        "debts": debts,
        "steps": steps,
        "warnings": warnings,
        "limits": {"year": year, "ike": tax.ike_limit(year), "ikze": tax.ikze_limit(year),
                   "ikze_self_employed": tax.ikze_limit(year, True), "source": tax.LIMITS_SOURCE.get(year)},
        "inputs": {"income": income, "expenses": expenses, "savings": savings, "age": age,
                   "tolerance_pct": tolerance_pct, "unstable_income": unstable_income, "experience": experience},
        "data_provenance": {
            "type": "calculation (no market data)",
            "sources": ["IKE/IKZE limits: " + str(tax.LIMITS_SOURCE.get(year)),
                        "OKI: " + tax.OKI_LAW + " (art. 25, 26, 43)"],
            "limits_year": year,
        },
        "assumptions": {
            "emergency_months_stable": EMERGENCY_MONTHS_STABLE,
            "emergency_months_unstable": EMERGENCY_MONTHS_UNSTABLE,
            "expensive_debt_rate_pct": EXPENSIVE_DEBT_RATE_PCT,
            "equity_crash_drawdown": EQUITY_CRASH_DRAWDOWN,
            "horizon_no_equity_years": HORIZON_NO_EQUITY_Y,
            "horizon_mixed_years": HORIZON_MIXED_Y,
            "method": "references/readiness_method.md",
        },
        "generated_at": dt.datetime.now().replace(microsecond=0).isoformat(),
    }


def render_markdown(r: Dict, lang: str) -> str:
    t = T(STRINGS, lang)
    P = lambda x, d=0: fmt.pln(x, d, lang)  # noqa: E731
    R = lambda x: fmt.pct(x, 1, lang)  # noqa: E731
    n, eq = r["numbers"], r["equity_range_pct"]
    hy = r["horizon"]
    L = ["# " + t("title"), "", "## " + t("answer"), "",
         "**" + t(r["stage_key"], r=R(EXPENSIVE_DEBT_RATE_PCT)) + "**", ""]
    if r["stage"] == 3 and hy["band"] != "short":
        L += ["{}: **{}–{}%** · {}: **{}**".format(t("equity_range"), eq["low"], eq["high"], t("investable"),
                                                    P(n["investable_monthly"])), ""]
    L += ["## " + t("numbers"), "", "| | |", "|---|---|",
          "| {} | {} |".format(t("surplus"), P(n["surplus"])),
          "| {} | {} |".format(t("ef_target", m=n["emergency_months"]), P(n["emergency_target"])),
          "| {} | {} |".format(t("ef_have"), P(n["savings"])),
          "| {} | {} |".format(t("ef_gap"), P(n["emergency_gap"]))]
    if n["emergency_gap"] > 0 and n["emergency_months_to_fill"]:
        L.append("| {} | {} |".format(t("ef_months"), t("months", n=n["emergency_months_to_fill"])))
    L.append("| {} | {} |".format(t("debt_exp", r=R(EXPENSIVE_DEBT_RATE_PCT)),
                                  P(n["expensive_debt_total"]) if n["expensive_debt_total"] else t("none")))
    band_txt = t("horizon_" + hy["band"], a=HORIZON_NO_EQUITY_Y, b=HORIZON_MIXED_Y)
    L.append("| {} | {} – {} |".format(t("horizon"), t("years", n=fmt.num(hy["years"], 0, lang)), band_txt))
    L.append("| {} | {}–{}% |".format(t("equity_range"), eq["low"], eq["high"]))
    L.append("| {} | {} |".format(t("investable"), P(n["investable_monthly"])))
    L += ["", t("equity_why", t=fmt.num(r["inputs"]["tolerance_pct"], 0, lang), mx=eq["uncapped_high"])
          + (t("equity_capped", c=eq["horizon_cap"]) if eq["capped_by_horizon"] else ""), ""]
    L += ["## " + t("steps"), ""]
    for i, s in enumerate(r["steps"], 1):
        kw = {}
        if s["key"] == "s_ef":
            kw = {"a": P(s["a"]), "g": P(s["g"])}
        elif s["key"] == "s_debt":
            kw = {"n": s["n"], "r": R(s["r"])}
        elif s["key"] == "s_safe":
            kw = {"a": HORIZON_NO_EQUITY_Y}
        elif s["key"] == "s_satellite":
            kw = {"p": SATELLITE_MAX_PCT}
        L.append("{}. {}".format(i, t(s["key"], **kw)))
    L += ["", "## " + t("warnings"), ""]
    for w in r["warnings"]:
        kw = {}
        if w["key"] == "w_expensive":
            kw = {"n": w["n"], "r": R(w["r"])}
        elif w["key"] == "w_low_tolerance":
            kw = {"t": fmt.num(w["t"], 0, lang)}
        L.append("- " + t(w["key"], **kw))
    lim = r["limits"]
    L += ["- " + t("limits", y=lim["year"], ike=P(lim["ike"]), ikze=P(lim["ikze"], 2), ikze_se=P(lim["ikze_self_employed"], 2)), ""]
    L += ["## " + t("next"), "",
          "- `gpw-account-planner` – IKE / IKZE / OKI / " + ("zwykły rachunek" if lang == "pl" else "regular account"),
          "- `gpw-etf-planner` – " + ("plan regularnych wpłat w ETF-y z GPW" if lang == "pl" else "regular ETF contribution plan on GPW"),
          "- `gpw-portfolio-check` – " + ("przegląd portfela" if lang == "pl" else "portfolio review"),
          "- `gpw-trade-journal --paper` – " + ("ćwiczenia na niby, bez prawdziwych pieniędzy" if lang == "pl" else "paper trading, no real money"),
          ""]
    L += ["## " + t("sources"), "",
          "- KNF – lista ostrzeżeń publicznych: https://www.knf.gov.pl/dla_konsumenta/ostrzezenia_publiczne",
          "- Limity IKE/IKZE: {}".format(lim["source"] or "—"),
          "- OKI: ustawa z 3 lipca 2026 r., {} (art. 25, 26, 43)".format(tax.OKI_LAW),
          "- Obligacje skarbowe: https://www.obligacjeskarbowe.pl",
          "", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GPW Start - investing readiness check for beginners (PL/EN)")
    p.add_argument("--income", type=float, required=True, help="net monthly income, PLN")
    p.add_argument("--expenses", type=float, required=True, help="monthly expenses, PLN")
    p.add_argument("--savings", type=float, default=0.0, help="safe, liquid savings today, PLN")
    p.add_argument("--debt", action="append", default=[], metavar="NAME:AMOUNT:RATE_PCT",
                   help="consumer debt, repeatable, e.g. karta:5000:21.5 (exclude the mortgage unless its rate is high)")
    p.add_argument("--horizon", type=float, required=True, help="years until you need the money")
    p.add_argument("--age", type=int, default=None)
    p.add_argument("--tolerance", type=float, required=True,
                   help="largest fall of the WHOLE portfolio you could sit through without selling, %% (e.g. 10, 20, 30)")
    p.add_argument("--unstable-income", action="store_true", help="B2B / single income / irregular pay -> 6-month reserve")
    p.add_argument("--experience", choices=["none", "basic", "some"], default="none")
    p.add_argument("--year", type=int, default=None, help="year for contribution limits (default: latest known)")
    p.add_argument("--lang", choices=["pl", "en"], default="pl")
    p.add_argument("--output-dir", default=".")
    p.add_argument("--no-files", action="store_true")
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        res = assess(income=a.income, expenses=a.expenses, savings=a.savings, debts=parse_debts(a.debt),
                     horizon_years=a.horizon, age=a.age, tolerance_pct=a.tolerance,
                     unstable_income=a.unstable_income, experience=a.experience, year=a.year)
    except StartError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    res["lang"] = a.lang
    md = render_markdown(res, a.lang)
    if not a.no_files:
        out = Path(a.output_dir).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        (out / "gpw_start_{}.json".format(stamp)).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "gpw_start_{}.md".format(stamp)).write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
