#!/usr/bin/env python3
"""GPW position sizer - risk-based position sizing for long trades on the
Warsaw Stock Exchange, in PLN, whole shares, with broker commissions.

Derived from tradermonty/claude-trading-skills ``position-sizer`` (MIT),
adapted to Polish market realities:

* whole shares only (GPW lot = 1 share);
* risk per trade INCLUDES round-trip commissions (entry + exit at the stop),
  with each broker's minimum fee;
* optional --ticker pulls last close, ATR(14) and median turnover from the
  official GPW archive; position is capped at a % of median daily turnover;
* gap-risk scenario (GPW static price threshold is ±10%, so a stop is not a
  guaranteed price);
* after-tax view per account type (regular / IKE / IKZE / OKI).

Methods: fixed fractional (entry + stop), ATR-based stop, half-Kelly risk
budget. The script never places orders; it only computes numbers.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import brokers, fmt, provenance, tax  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T  # noqa: E402

# --------------------------------------------------------------------------
# Broker presets (PLN). Tariffs change - every preset carries a reminder.
# Sources (2026-11): broker tariffs as summarised by freenance.io,
# ppcg.com.pl, brokerchooser.com; see references/sizing_methodology.md.
# Commission presets live in gpwlib.brokers (one table for every skill).
BROKERS = brokers.BROKERS
VERIFY_NOTE = brokers.VERIFY_NOTE

BELKA_RATE = tax.BELKA_RATE            # art. 30b ustawy o PIT
OKI_EXEMPTION_PLN = tax.OKI_EXEMPT_TOTAL  # art. 26 Dz.U. 2026 poz. 1098 (qualified assets only)
OKI_RATE_2027 = tax.OKI_RATE_2027_PCT     # art. 43, rate for 2027
KELLY_RISK_CAP_PCT = 2.0   # cap half-Kelly risk budget for retail accounts
ILLIQUID_TURNOVER_PLN = 100_000.0
FEE_DRAG_WARN_PCT = 0.5    # warn when round-trip fees exceed 0.5% of the position
LARGE_RISK_WARN_PCT = 2.0


STRINGS = {
    "title": {"pl": "Wielkość pozycji – GPW", "en": "Position size – GPW"},
    "inputs": {"pl": "Dane wejściowe", "en": "Inputs"},
    "result": {"pl": "Wynik", "en": "Result"},
    "account": {"pl": "Kapitał", "en": "Account size"},
    "ticker": {"pl": "Spółka", "en": "Ticker"},
    "entry": {"pl": "Cena wejścia", "en": "Entry price"},
    "stop": {"pl": "Stop loss", "en": "Stop loss"},
    "target": {"pl": "Cel", "en": "Target"},
    "method": {"pl": "Metoda", "en": "Method"},
    "risk_budget": {"pl": "Budżet ryzyka", "en": "Risk budget"},
    "broker": {"pl": "Broker / prowizja", "en": "Broker / commission"},
    "shares": {"pl": "Liczba akcji", "en": "Shares"},
    "position_value": {"pl": "Wartość pozycji", "en": "Position value"},
    "pct_of_account": {"pl": "{p} kapitału", "en": "{p} of account"},
    "risk_price": {"pl": "Ryzyko cenowe (do stopa)", "en": "Price risk (to stop)"},
    "fees_rt": {"pl": "Prowizje (kupno + sprzedaż na stopie)", "en": "Commissions (entry + exit at stop)"},
    "risk_total": {"pl": "Łączne ryzyko z prowizjami", "en": "Total risk incl. commissions"},
    "gap": {"pl": "Strata, gdy kurs otworzy się {g} poniżej stopa (luka)", "en": "Loss if price gaps {g} below the stop"},
    "verify": {"pl": "stawki orientacyjne – sprawdź aktualną tabelę opłat i prowizji brokera", "en": "indicative rates – check your broker's current fee table"},
    "reward": {"pl": "Zysk na celu (po prowizjach)", "en": "Profit at target (after commissions)"},
    "r_multiple": {"pl": "Stosunek zysku do ryzyka (R)", "en": "Reward-to-risk (R multiple)"},
    "after_tax": {"pl": "Zysk na celu po podatku", "en": "Profit at target after tax"},
    "constraints": {"pl": "Ograniczenia", "en": "Constraints"},
    "binding": {"pl": "wiążące", "en": "binding"},
    "warnings": {"pl": "Ostrzeżenia", "en": "Warnings"},
    "market_data": {"pl": "Dane rynkowe (archiwum GPW)", "en": "Market data (GPW archive)"},
    "last_close": {"pl": "Ostatnie zamknięcie", "en": "Last close"},
    "atr": {"pl": "ATR(14)", "en": "ATR(14)"},
    "median_turnover": {"pl": "Mediana obrotu z 20 sesji", "en": "20-session median turnover"},
    "sources": {"pl": "Źródła", "en": "Sources"},
    "c_cash": {"pl": "Gotówka (bez dźwigni)", "en": "Available cash (no leverage)"},
    "c_max_position": {"pl": "Maks. {p} kapitału w jednej pozycji", "en": "Max {p} of account per position"},
    "c_liquidity": {"pl": "Maks. {p} mediany dziennego obrotu", "en": "Max {p} of median daily turnover"},
    "c_sector": {"pl": "Limit sektora {p}", "en": "Sector limit {p}"},
    "m_fixed": {"pl": "stały procent ryzyka", "en": "fixed fractional"},
    "m_atr": {"pl": "stop z ATR ({m}× ATR)", "en": "ATR stop ({m}× ATR)"},
    "m_kelly": {"pl": "pół-Kelly (z historii transakcji)", "en": "half-Kelly (from trade history)"},
    "kelly_line": {
        "pl": "Kelly: {k} → pół-Kelly: {h} ryzyka na transakcję{cap}",
        "en": "Kelly: {k} → half-Kelly: {h} risk per trade{cap}",
    },
    "kelly_cap": {"pl": " (ograniczone do {c})", "en": " (capped at {c})"},
    "warn_xtb": {
        "pl": "XTB: 0% prowizji tylko do 100 tys. EUR obrotu miesięcznie; powyżej 0,2% (min. 10 EUR).",
        "en": "XTB: 0% commission only up to 100k EUR monthly turnover; above that 0.2% (min 10 EUR).",
    },
    "warn_verify": {
        "pl": "Stawki prowizji to przybliżenie – sprawdź aktualną tabelę opłat swojego brokera.",
        "en": "Commission rates are approximations – check your broker's current fee table.",
    },
    "warn_tariff_stale": {
        "pl": "⚠️ Tabela prowizji ma ponad {n} dni (wpisana {d}) – podaj aktualne stawki przez --commission-pct i --commission-min.",
        "en": "⚠️ The commission table is over {n} days old (entered {d}) – pass current rates via --commission-pct and --commission-min.",
    },
    "warn_fee_drag": {
        "pl": "Prowizje w obie strony to {p} wartości pozycji (minimalna opłata). Mała pozycja jest droga – rozważ większą lub tańszego brokera.",
        "en": "Round-trip commissions are {p} of the position (minimum fee). Small positions are expensive – consider a larger size or a cheaper broker.",
    },
    "warn_illiquid": {
        "pl": "Niska płynność: mediana obrotu {v} dziennie (< 100 tys. zł). Spread i poślizg mogą być duże; stop może się nie wykonać po zakładanej cenie.",
        "en": "Low liquidity: median turnover {v} per day (< PLN 100k). Spread and slippage can be large; the stop may not fill at the planned price.",
    },
    "warn_zero": {
        "pl": "Wynik to 0 akcji – budżet ryzyka jest za mały względem odległości stopa i prowizji.",
        "en": "Result is 0 shares – the risk budget is too small for this stop distance and commissions.",
    },
    "warn_large_risk": {
        "pl": "Ryzykujesz {p} kapitału na jedną transakcję. Dla początkujących typowo 0,5–1%, maksymalnie 2%.",
        "en": "You are risking {p} of the account on one trade. Beginners typically use 0.5–1%, at most 2%.",
    },
    "warn_r_low": {
        "pl": "Stosunek zysku do ryzyka poniżej 1,5R – potencjalny zysk słabo rekompensuje ryzyko.",
        "en": "Reward-to-risk below 1.5R – the potential profit barely compensates the risk.",
    },
    "warn_stale": {
        "pl": "UWAGA: dane rynkowe są nieaktualne (ostatnia sesja {d}, {n} sesji wstecz).",
        "en": "WARNING: market data is stale (last session {d}, {n} sessions behind).",
    },
    "warn_entry_far": {
        "pl": "Cena wejścia różni się o {p} od ostatniego zamknięcia ({c}).",
        "en": "Entry price differs by {p} from the last close ({c}).",
    },
    "warn_gap_note": {
        "pl": "Stop loss nie gwarantuje ceny: na GPW przy lukach i równoważeniu (próg statyczny ±10%) zlecenie może wykonać się znacznie niżej.",
        "en": "A stop loss does not guarantee the price: on GPW, gaps and volatility auctions (static threshold ±10%) can fill the order much lower.",
    },
    "tax_regular": {
        "pl": "Rachunek zwykły: 19% podatku od zysku (\"podatek Belki\", PIT-38). Prowizje są kosztem uzyskania przychodu.",
        "en": "Regular account: 19% capital-gains tax (\"Belka tax\", PIT-38). Commissions are deductible costs.",
    },
    "tax_ike": {
        "pl": "IKE: brak podatku Belki przy wypłacie po spełnieniu warunków (wiek 60 lat / 55 lat z uprawnieniami emerytalnymi). Przy wcześniejszym zwrocie podatek jest należny. Limit wpłat 2026: 28 260 zł.",
        "en": "IKE: no Belka tax on withdrawal once conditions are met (age 60, or 55 with pension rights). Early withdrawal is taxed. 2026 contribution limit: PLN 28,260.",
    },
    "tax_ikze": {
        "pl": "IKZE: brak podatku Belki w trakcie; wpłaty odliczasz od dochodu, a przy wypłacie po 65. roku życia płacisz 10% zryczałtowanego podatku od całości. Limit 2026: 11 304 zł (16 956 zł dla samozatrudnionych).",
        "en": "IKZE: no Belka tax inside; contributions are deductible, withdrawal after 65 is taxed at a flat 10% of the total. 2026 limit: PLN 11,304 (PLN 16,956 self-employed).",
    },
    "tax_oki": {
        "pl": "OKI (od 1.01.2027, Dz.U. 2026 poz. 1098): zamiast podatku Belki roczny podatek od średniej wartości aktywów – 0,85% w 2027 r. Zwolnienie do 100 000 zł dotyczy tylko aktywów kwalifikowanych (akcje spółek z kapitałem w zł, obligacje w zł, fundusze z ≥70% takich aktywów); zagraniczne akcje i ETF-y globalne są opodatkowane od pierwszej złotówki. Pokazany zysk jest przed tym podatkiem.",
        "en": "OKI (from 1 Jan 2027, Dz.U. 2026 poz. 1098): instead of the Belka tax, an annual tax on average asset value – 0.85% in 2027. The PLN 100,000 exemption covers only qualified assets (shares of companies with PLN share capital, PLN bonds, funds with ≥70% such assets); foreign shares and global ETFs are taxed from the first zloty. Profit shown is before that tax.",
    },
    "no_target": {"pl": "(podaj --target, aby policzyć zysk i R)", "en": "(pass --target to compute profit and R)"},
    "acct_regular": {"pl": "zwykły rachunek maklerski", "en": "regular brokerage account"},
    "acct_ike": {"pl": "IKE", "en": "IKE"},
    "acct_ikze": {"pl": "IKZE", "en": "IKZE"},
    "acct_oki": {"pl": "OKI", "en": "OKI"},
    "account_type": {"pl": "Typ rachunku", "en": "Account type"},
}


class SizingError(ValueError):
    """Invalid inputs that make sizing impossible."""


# --------------------------------------------------------------------------
# Pure calculation functions
# --------------------------------------------------------------------------

def commission(value: float, pct: float, min_fee: float) -> float:
    """Commission for one order of ``value`` PLN (0 for an empty order)."""
    return brokers.commission(value, pct, min_fee)


def round_trip_risk(shares: int, entry: float, stop: float, pct: float, min_fee: float) -> Dict:
    """Loss if we buy ``shares`` at entry and sell all at the stop."""
    price_risk = shares * (entry - stop)
    fee_in = commission(shares * entry, pct, min_fee)
    fee_out = commission(shares * stop, pct, min_fee)
    return {
        "price_risk": round(price_risk, 2),
        "fee_entry": fee_in,
        "fee_exit": fee_out,
        "total": round(price_risk + fee_in + fee_out, 2),
    }


def max_shares_for_budget(budget: float, entry: float, stop: float, pct: float, min_fee: float) -> int:
    """Largest whole number of shares whose risk incl. round-trip fees <= budget.

    Risk is monotonic in the share count, so binary search is exact.
    """
    if entry <= stop:
        raise SizingError("stop must be below entry for a long trade")
    hi = int(math.floor(budget / (entry - stop)))
    lo = 0
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if round_trip_risk(mid, entry, stop, pct, min_fee)["total"] <= budget + 1e-9:
            lo = mid
        else:
            hi = mid - 1
    return lo


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> Dict:
    """Kelly f* = W - (1 - W) / R, R = avg_win / avg_loss; floored at 0."""
    if not 0 < win_rate < 1:
        raise SizingError("win rate must be between 0 and 1 (exclusive)")
    if avg_win <= 0 or avg_loss <= 0:
        raise SizingError("average win and average loss must be positive")
    r = avg_win / avg_loss
    k = max(0.0, win_rate - (1 - win_rate) / r) * 100
    return {"kelly_pct": round(k, 2), "half_kelly_pct": round(k / 2, 2), "payoff_ratio": round(r, 3)}


def after_tax_profit(profit: float, account_type: str) -> Optional[float]:
    """Profit after capital-gains tax at the moment of sale.

    Regular account: 19% on a positive result (losses are not refunded but can
    offset other gains in PIT-38 - not modelled per trade). IKE/IKZE/OKI: no
    tax at the sale itself (see notes for withdrawal / asset taxes).
    """
    if account_type == "regular":
        return round(profit * (1 - BELKA_RATE), 2) if profit > 0 else round(profit, 2)
    return round(profit, 2)


def calculate(params: Dict) -> Dict:
    """Run the full sizing. ``params`` keys mirror the CLI flags (see build_parser)."""
    account = params["account_size"]
    if not account or account <= 0:
        raise SizingError("account size must be positive")
    entry = params.get("entry")
    if entry is None or entry <= 0:
        raise SizingError("entry price is required (pass --entry or --ticker)")

    broker = BROKERS[params.get("broker") or "custom"].copy()
    if params.get("commission_pct") is not None:
        broker["pct"] = params["commission_pct"]
    if params.get("commission_min") is not None:
        broker["min_fee"] = params["commission_min"]
    pct, min_fee = broker["pct"], broker["min_fee"]

    warnings: List[str] = []

    # --- stop -------------------------------------------------------------
    stop = params.get("stop")
    method = "fixed"
    if stop is None and params.get("atr"):
        method = "atr"
        stop = round(entry - params["atr"] * params.get("atr_multiplier", 2.0), 4)
    if stop is None:
        raise SizingError("a stop is required: pass --stop, or --atr / --ticker for an ATR stop")
    if stop <= 0:
        raise SizingError("computed stop is not positive - use a smaller ATR multiplier or an explicit --stop")
    if stop >= entry:
        raise SizingError("stop must be below entry for a long trade")

    # --- risk budget ------------------------------------------------------
    kelly = None
    if params.get("win_rate") is not None:
        if params.get("risk_pct") is not None:
            raise SizingError("use either --risk-pct or Kelly inputs, not both")
        kelly = kelly_fraction(params["win_rate"], params["avg_win"], params["avg_loss"])
        risk_pct = min(kelly["half_kelly_pct"], KELLY_RISK_CAP_PCT)
        kelly["capped"] = kelly["half_kelly_pct"] > KELLY_RISK_CAP_PCT
        kelly["risk_pct_used"] = risk_pct
        if method == "fixed":
            method = "kelly"
    else:
        risk_pct = params.get("risk_pct") if params.get("risk_pct") is not None else 1.0
    if risk_pct <= 0:
        raise SizingError("risk budget is 0% (Kelly with non-positive expectancy) - no position")
    budget = round(account * risk_pct / 100.0, 2)
    if risk_pct > LARGE_RISK_WARN_PCT:
        warnings.append("warn_large_risk")

    by_risk = max_shares_for_budget(budget, entry, stop, pct, min_fee)

    # --- constraints ------------------------------------------------------
    constraints = []

    def add_constraint(kind, limit, max_shares):
        constraints.append({"type": kind, "limit": limit, "max_shares": max(0, int(max_shares)), "binding": False})

    # cash: shares * entry + entry fee <= account
    cash_shares = int(account // entry)
    while cash_shares > 0 and cash_shares * entry + commission(cash_shares * entry, pct, min_fee) > account:
        cash_shares -= 1
    add_constraint("cash", account, cash_shares)
    if params.get("max_position_pct") is not None:
        add_constraint("max_position_pct", params["max_position_pct"],
                       account * params["max_position_pct"] / 100.0 // entry)
    if params.get("median_turnover_pln") and params.get("max_adv_pct") is not None:
        add_constraint("liquidity", params["max_adv_pct"],
                       params["median_turnover_pln"] * params["max_adv_pct"] / 100.0 // entry)
    if params.get("max_sector_pct") is not None:
        remaining = (params["max_sector_pct"] - (params.get("current_sector_exposure") or 0.0)) / 100.0 * account
        add_constraint("max_sector_pct", params["max_sector_pct"], max(0.0, remaining) // entry)

    shares = min([by_risk] + [c["max_shares"] for c in constraints])
    for c in constraints:
        if c["max_shares"] == shares and shares < by_risk:
            c["binding"] = True

    risk = round_trip_risk(shares, entry, stop, pct, min_fee)
    position_value = round(shares * entry, 2)

    if shares == 0:
        warnings.append("warn_zero")
    fees_rt = risk["fee_entry"] + risk["fee_exit"]
    fee_drag = fees_rt / position_value * 100 if position_value else None
    min_fee_binds = shares > 0 and min_fee > 0 and position_value * pct / 100.0 < min_fee
    if fee_drag is not None and min_fee_binds and fee_drag > FEE_DRAG_WARN_PCT:
        warnings.append("warn_fee_drag")
    if broker.get("warning_key"):
        warnings.append(broker["warning_key"])
    if params.get("broker") not in (None, "custom") and params.get("commission_pct") is None:
        warnings.append("warn_verify")
        if brokers.tariff_age_days() > brokers.TARIFF_MAX_AGE_DAYS:
            warnings.append("warn_tariff_stale")
    if params.get("median_turnover_pln") is not None and params["median_turnover_pln"] < ILLIQUID_TURNOVER_PLN:
        warnings.append("warn_illiquid")

    # --- gap scenario -----------------------------------------------------
    gap_pct = params.get("gap_pct", 10.0)
    gap_fill = stop * (1 - gap_pct / 100.0)
    gap = round_trip_risk(shares, entry, gap_fill, pct, min_fee)
    warnings.append("warn_gap_note")

    # --- reward -----------------------------------------------------------
    target = params.get("target")
    reward = None
    if target is not None:
        if target <= entry:
            raise SizingError("target must be above entry for a long trade")
        gross = shares * (target - entry)
        fees_t = commission(shares * entry, pct, min_fee) + commission(shares * target, pct, min_fee)
        net = round(gross - fees_t, 2)
        r_mult = round(net / risk["total"], 2) if risk["total"] > 0 else None
        reward = {
            "target": target,
            "gross": round(gross, 2),
            "fees": round(fees_t, 2),
            "net": net,
            "after_tax": after_tax_profit(net, params.get("account_type", "regular")),
            "r_multiple": r_mult,
        }
        if r_mult is not None and r_mult < 1.5:
            warnings.append("warn_r_low")

    return {
        "schema_version": "1.0",
        "skill": "gpw-position-sizer",
        "inputs": {k: v for k, v in params.items() if v is not None},
        "method": method,
        "entry": entry,
        "stop": stop,
        "risk_pct": risk_pct,
        "risk_budget": budget,
        "broker": {"key": params.get("broker") or "custom", "label": broker["label"],
                   "pct": pct, "min_fee": min_fee, "note": broker["note"], "verify": VERIFY_NOTE},
        "kelly": kelly,
        "shares_by_risk": by_risk,
        "shares": shares,
        "position_value": position_value,
        "position_pct_of_account": round(position_value / account * 100, 2),
        "risk": risk,
        "risk_pct_of_account": round(risk["total"] / account * 100, 3),
        "fee_drag_pct": round(fee_drag, 3) if fee_drag is not None else None,
        "constraints": constraints,
        "gap_scenario": {"gap_pct": gap_pct, "fill_price": round(gap_fill, 4), "loss": gap["total"]},
        "reward": reward,
        "account_type": params.get("account_type", "regular"),
        "warnings": list(dict.fromkeys(warnings)),
    }


# --------------------------------------------------------------------------
# Market data (optional --ticker)
# --------------------------------------------------------------------------

def load_ticker_data(name: str, sessions: int = 40, offline: bool = False) -> Dict:
    from gpwlib import gpw_archive, indicators, panel

    data = gpw_archive.load_sessions(sessions, offline=offline, progress=False)
    if not data:
        raise SizingError("no GPW sessions available (offline cache empty or network error)")
    pnl = panel.build_panel(data)
    key = {k.upper(): k for k in pnl}.get(name.strip().upper())
    if key is None:
        raise SizingError(
            "ticker '{}' not found in the GPW archive - use the GPW short name, e.g. PKOBP, PKNORLEN, CDPROJEKT".format(name)
        )
    s = pnl[key]
    last_date = dt.date.fromisoformat(s.dates[-1])
    turnover = [v * 1000.0 for v in s.turnover_k[-20:]]
    return {
        "ticker": key,
        "last_date": s.dates[-1],
        "last_close": s.close[-1],
        "atr14": indicators.atr(s.high, s.low, s.close, 14),
        "median_turnover_pln": indicators.median(turnover),
        "sessions": len(s),
        "adjustments": s.adjustments,
        "provenance": provenance.build(
            as_of=last_date,
            sources=[data[-1]["source_url"].split("?")[0]],
            sessions_used=len(data),
            universe_size=None,
            notes=["Prices adjusted for splits via GPW reference-price change"] if s.adjustments else [],
        ),
    }


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def render_markdown(res: Dict, lang: str = "pl", market: Optional[Dict] = None) -> str:
    t = T(STRINGS, lang)
    L: List[str] = []
    P = lambda x, d=2: fmt.pln(x, d, lang)  # noqa: E731
    L.append("# " + t("title"))
    L.append("")
    if market and market["provenance"]["freshness"]["is_stale"]:
        f = market["provenance"]["freshness"]
        L.append("> ⚠️ " + t("warn_stale", d=f["as_of"], n=f["sessions_behind"]))
        L.append("")
    L.append("## " + t("result"))
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append("| **{}** | **{}** |".format(t("shares"), fmt.num(res["shares"], 0, lang)))
    L.append("| {} | {} ({}) |".format(t("position_value"), P(res["position_value"]),
                                      t("pct_of_account", p=fmt.pct(res["position_pct_of_account"], 1, lang))))
    L.append("| {} | {} |".format(t("risk_price"), P(res["risk"]["price_risk"])))
    L.append("| {} | {} |".format(t("fees_rt"), P(res["risk"]["fee_entry"] + res["risk"]["fee_exit"])))
    L.append("| **{}** | **{}** ({}) |".format(t("risk_total"), P(res["risk"]["total"]),
                                              t("pct_of_account", p=fmt.pct(res["risk_pct_of_account"], 2, lang))))
    gs = res["gap_scenario"]
    L.append("| {} | {} |".format(t("gap", g=fmt.pct(gs["gap_pct"], 0, lang)), P(gs["loss"])))
    if res["reward"]:
        rw = res["reward"]
        L.append("| {} ({}) | {} |".format(t("reward"), P(rw["target"]), P(rw["net"])))
        L.append("| {} | {} |".format(t("after_tax"), P(rw["after_tax"])))
        L.append("| **{}** | **{}R** |".format(t("r_multiple"), fmt.num(rw["r_multiple"], 2, lang)))
    else:
        L.append("| {} | {} |".format(t("r_multiple"), t("no_target")))
    L.append("")

    L.append("## " + t("inputs"))
    L.append("")
    inp = res["inputs"]
    if inp.get("ticker"):
        L.append("- {}: **{}**".format(t("ticker"), inp["ticker"]))
    L.append("- {}: {}".format(t("account"), P(inp["account_size"])))
    L.append("- {}: {}".format(t("entry"), P(res["entry"], 4 if res["entry"] < 10 else 2)))
    L.append("- {}: {}".format(t("stop"), P(res["stop"], 4 if res["stop"] < 10 else 2)))
    m = {"fixed": t("m_fixed"), "atr": t("m_atr", m=fmt.num(inp.get("atr_multiplier", 2.0), 1, lang)), "kelly": t("m_kelly")}[res["method"]]
    L.append("- {}: {}".format(t("method"), m))
    L.append("- {}: {} ({})".format(t("risk_budget"), P(res["risk_budget"]), fmt.pct(res["risk_pct"], 2, lang)))
    if res["kelly"]:
        k = res["kelly"]
        cap = t("kelly_cap", c=fmt.pct(KELLY_RISK_CAP_PCT, 1, lang)) if k["capped"] else ""
        L.append("- " + t("kelly_line", k=fmt.pct(k["kelly_pct"], 2, lang), h=fmt.pct(k["half_kelly_pct"], 2, lang), cap=cap))
    b = res["broker"]
    L.append("- {}: {} – {} (min. {})".format(t("broker"), b["label"], fmt.pct(b["pct"], 2, lang), P(b["min_fee"])))
    L.append("- {}: {}".format(t("account_type"), t("acct_" + res["account_type"])))
    L.append("")

    if market:
        L.append("## " + t("market_data"))
        L.append("")
        L.append("- {} ({}): {}".format(t("last_close"), market["last_date"], P(market["last_close"], 4 if market["last_close"] < 10 else 2)))
        if market["atr14"] is not None:
            L.append("- {}: {}".format(t("atr"), P(market["atr14"], 4 if market["atr14"] < 1 else 2)))
        L.append("- {}: {}".format(t("median_turnover"), P(market["median_turnover_pln"], 0)))
        L.append("")

    if res["constraints"]:
        L.append("## " + t("constraints"))
        L.append("")
        names = {
            "cash": lambda c: t("c_cash"),
            "max_position_pct": lambda c: t("c_max_position", p=fmt.pct(c["limit"], 0, lang)),
            "liquidity": lambda c: t("c_liquidity", p=fmt.pct(c["limit"], 0, lang)),
            "max_sector_pct": lambda c: t("c_sector", p=fmt.pct(c["limit"], 0, lang)),
        }
        for c in res["constraints"]:
            L.append("- {}: ≤ {} {}{}".format(names[c["type"]](c), fmt.num(c["max_shares"], 0, lang),
                                              "akcji" if lang == "pl" else "shares",
                                              " – **{}**".format(t("binding")) if c["binding"] else ""))
        L.append("")

    L.append("## " + t("warnings"))
    L.append("")
    for w in res["warnings"]:
        kw = {}
        if w == "warn_fee_drag":
            kw = {"p": fmt.pct(res["fee_drag_pct"], 2, lang)}
        elif w == "warn_tariff_stale":
            kw = {"n": brokers.TARIFF_MAX_AGE_DAYS, "d": brokers.TARIFF_ENTERED.isoformat()}
        elif w == "warn_large_risk":
            kw = {"p": fmt.pct(res["risk_pct"], 2, lang)}
        elif w == "warn_illiquid":
            kw = {"v": P(market["median_turnover_pln"] if market else res["inputs"].get("median_turnover_pln"), 0)}
        L.append("- " + t(w, **kw))
    L.append("- " + t("tax_" + res["account_type"]))
    if market and res["inputs"].get("entry_from_user") and market["last_close"]:
        diff = (res["entry"] / market["last_close"] - 1) * 100
        if abs(diff) > 5:
            L.append("- " + t("warn_entry_far", p=fmt.pct(diff, 1, lang, signed=True), c=P(market["last_close"])))
    L.append("")

    L.append("## " + t("sources"))
    L.append("")
    if market:
        L.append("- GPW – Archiwum notowań: https://www.gpw.pl/archiwum-notowan (sesja {})".format(market["last_date"]))
    L.append("- {}: {}".format(t("broker"), t("verify")))
    L.append("- Podatki: https://www.podatki.gov.pl/pit/ (PIT-38), ustawa o OKI: Dz.U. 2026 poz. 1098")
    L.append("")
    L.append("---")
    L.append("_" + DISCLAIMER[lang] + "_")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def positive_float(text: str) -> float:
    v = float(text.replace(",", "."))
    if not math.isfinite(v) or v <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return v


def nonneg_float(text: str) -> float:
    v = float(text.replace(",", "."))
    if not math.isfinite(v) or v < 0:
        raise argparse.ArgumentTypeError("must be a non-negative number")
    return v


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Risk-based position sizing for GPW long trades (PLN, whole shares, commissions).")
    p.add_argument("--account-size", type=positive_float, required=True, help="Account value in PLN")
    p.add_argument("--ticker", help="GPW short name (e.g. PKOBP) - pulls last close, ATR(14), median turnover")
    p.add_argument("--entry", type=positive_float, help="Entry price (default: last close when --ticker is used)")
    p.add_argument("--stop", type=positive_float, help="Stop-loss price")
    p.add_argument("--target", type=positive_float, help="Target price (for profit, R multiple, after-tax view)")
    p.add_argument("--risk-pct", type=positive_float, help="Percent of account risked per trade (default 1.0)")
    p.add_argument("--atr", type=positive_float, help="ATR value for an ATR stop (auto with --ticker)")
    p.add_argument("--atr-multiplier", type=positive_float, default=2.0, help="Stop distance in ATRs (default 2.0)")
    p.add_argument("--win-rate", type=float, help="Historical win rate 0-1 (half-Kelly mode)")
    p.add_argument("--avg-win", type=positive_float, help="Average winning trade (PLN or %%)")
    p.add_argument("--avg-loss", type=positive_float, help="Average losing trade (same unit as --avg-win)")
    p.add_argument("--broker", choices=sorted(BROKERS), default="mbank", help="Commission preset (default mbank)")
    p.add_argument("--commission-pct", type=nonneg_float, help="Override commission %% per order")
    p.add_argument("--commission-min", type=nonneg_float, help="Override minimum fee per order in PLN")
    p.add_argument("--max-position-pct", type=positive_float, default=25.0, help="Max position as %% of account (default 25)")
    p.add_argument("--max-adv-pct", type=positive_float, default=10.0, help="Max position as %% of median daily turnover (default 10)")
    p.add_argument("--median-turnover", type=positive_float, help="Median daily turnover in PLN (if not using --ticker)")
    p.add_argument("--max-sector-pct", type=positive_float, help="Max sector exposure %% of account")
    p.add_argument("--current-sector-exposure", type=nonneg_float, default=0.0, help="Current sector exposure %%")
    p.add_argument("--gap-pct", type=nonneg_float, default=10.0, help="Gap-below-stop scenario in %% (default 10 = GPW static threshold)")
    p.add_argument("--account-type", choices=["regular", "ike", "ikze", "oki"], default="regular")
    p.add_argument("--offline", action="store_true", help="Use only the local GPW cache")
    p.add_argument("--lang", choices=["pl", "en"], default="pl")
    p.add_argument("--output-dir", default=".", help="Where to write JSON + Markdown reports")
    p.add_argument("--no-files", action="store_true", help="Print Markdown only, write no files")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.win_rate is not None and (args.avg_win is None or args.avg_loss is None):
        build_parser().error("--win-rate requires --avg-win and --avg-loss")
    market = None
    try:
        if args.ticker:
            market = load_ticker_data(args.ticker, offline=args.offline)
        params = {
            "account_size": args.account_size,
            "ticker": market["ticker"] if market else None,
            "entry": args.entry or (market["last_close"] if market else None),
            "entry_from_user": bool(args.entry) if market else None,
            "stop": args.stop,
            "target": args.target,
            "risk_pct": args.risk_pct,
            "atr": args.atr or (market["atr14"] if market and not args.stop else None),
            "atr_multiplier": args.atr_multiplier,
            "win_rate": args.win_rate,
            "avg_win": args.avg_win,
            "avg_loss": args.avg_loss,
            "broker": args.broker,
            "commission_pct": args.commission_pct,
            "commission_min": args.commission_min,
            "max_position_pct": args.max_position_pct,
            "max_adv_pct": args.max_adv_pct,
            "median_turnover_pln": args.median_turnover or (market["median_turnover_pln"] if market else None),
            "max_sector_pct": args.max_sector_pct,
            "current_sector_exposure": args.current_sector_exposure,
            "gap_pct": args.gap_pct,
            "account_type": args.account_type,
        }
        res = calculate(params)
    except SizingError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # network / parsing problems
        print("error: {}: {}".format(type(exc).__name__, exc), file=sys.stderr)
        return 1
    if market:
        res["market_data"] = {k: v for k, v in market.items() if k != "provenance"}
        res["data_provenance"] = market["provenance"]
    md = render_markdown(res, args.lang, market)
    if args.no_files:
        print(md)
        return 0
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    jp = out / "gpw_position_size_{}.json".format(stamp)
    mp = out / "gpw_position_size_{}.md".format(stamp)
    jp.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(md, encoding="utf-8")
    print(md)
    print("JSON: {}\nMarkdown: {}".format(jp, mp), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
