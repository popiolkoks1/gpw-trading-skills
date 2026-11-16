#!/usr/bin/env python3
"""GPW trade review - statistics, process-vs-outcome analysis and a PIT-38
estimate from the gpw-trade-journal files.

Inspired by tradermonty/claude-trading-skills ``signal-postmortem`` (MIT):
instead of classifying predicted vs realised direction, it separates
**decision quality** (did you follow your plan) from **outcome** (did you make
money), because on small samples outcomes are dominated by luck.

Reads ``<journal-dir>/theses/*.json`` (schema: gpw-trade-journal
references/journal_schema.md). Never modifies the journal.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import fmt  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T  # noqa: E402

TAX_RATE = Decimal("0.19")
MIN_SAMPLE = 20  # below this, win rate / profit factor are noise (±11pp std. error at 50%)
HOLD_BUCKETS = [(5, "≤5"), (20, "6–20"), (60, "21–60"), (10 ** 9, ">60")]


class ReviewError(ValueError):
    pass


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def default_journal_dir() -> Path:
    env = os.environ.get("GPW_JOURNAL_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".gpw-trading-skills" / "journal"


def load_theses(journal_dir: Path) -> List[Dict]:
    d = Path(journal_dir) / "theses"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            th = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            print("warning: skipping unreadable {}".format(p), file=sys.stderr)
            continue
        if th.get("schema_version") != "1.0":
            print("warning: skipping {} (unknown schema {})".format(p.name, th.get("schema_version")), file=sys.stderr)
            continue
        out.append(th)
    return out


# --------------------------------------------------------------------------
# Period filter
# --------------------------------------------------------------------------

def period_bounds(month: Optional[str], year: Optional[int]):
    if month:
        y, m = map(int, month.split("-"))
        start = dt.date(y, m, 1)
        end = dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
        return start, end, month
    if year:
        return dt.date(year, 1, 1), dt.date(year, 12, 31), str(year)
    return dt.date(1900, 1, 1), dt.date(2999, 12, 31), "all"


def in_period(date_str: str, start: dt.date, end: dt.date) -> bool:
    d = dt.date.fromisoformat(date_str)
    return start <= d <= end


# --------------------------------------------------------------------------
# Statistics on closed theses
# --------------------------------------------------------------------------

def closed_trades(theses: List[Dict], start: dt.date, end: dt.date, paper: bool = False) -> List[Dict]:
    """Closed trades in the period. Real and paper trades are never mixed:
    ``paper=False`` returns real trades only, ``paper=True`` paper trades only."""
    rows = []
    for th in theses:
        o = th.get("outcome")
        if th.get("status") != "CLOSED" or not o:
            continue
        if bool(th.get("paper")) != paper:
            continue
        if not in_period(o["closed_at"], start, end):
            continue
        b = o.get("benchmark") or {}
        rows.append({
            "id": th["id"], "ticker": th["ticker"], "setup": th.get("setup") or "other",
            "account_type": th.get("account_type", "regular"), "closed_at": o["closed_at"],
            "net_pnl": o["net_pnl"], "return_pct": o.get("return_pct"), "r": o.get("r_multiple"),
            "holding_days": o.get("holding_days"), "exit_reason": o.get("exit_reason"),
            "followed_plan": bool(o.get("followed_plan")), "process": o.get("process") or {},
            "excess": o.get("excess_return_pct"), "bench": b.get("return_pct"),
            "lessons": o.get("lessons") or "", "fees": o.get("fees", 0.0),
        })
    rows.sort(key=lambda r: (r["closed_at"], r["id"]))
    return rows


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def stats(rows: List[Dict]) -> Dict:
    n = len(rows)
    wins = [r for r in rows if r["net_pnl"] > 0]
    losses = [r for r in rows if r["net_pnl"] < 0]
    gross_win = sum(r["net_pnl"] for r in wins)
    gross_loss = -sum(r["net_pnl"] for r in losses)
    equity, peak, mdd = 0.0, 0.0, 0.0
    for r in rows:
        equity += r["net_pnl"]
        peak = max(peak, equity)
        mdd = max(mdd, peak - equity)
    win_rate = len(wins) / n * 100 if n else None
    avg_win = gross_win / len(wins) if wins else None
    avg_loss = -gross_loss / len(losses) if losses else None
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": n - len(wins) - len(losses),
        "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
        "net_pnl": round(sum(r["net_pnl"] for r in rows), 2),
        "fees": round(sum(r["fees"] for r in rows), 2),
        "avg_win": round(avg_win, 2) if avg_win is not None else None,
        "avg_loss": round(avg_loss, 2) if avg_loss is not None else None,
        "payoff_ratio": round(avg_win / -avg_loss, 2) if avg_win and avg_loss else None,
        "expectancy": round(sum(r["net_pnl"] for r in rows) / n, 2) if n else None,
        # None when there are no losses (undefined); flag tells the report to show "∞"
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "profit_factor_no_losses": bool(wins) and gross_loss == 0,
        "avg_r": _round(_mean([r["r"] for r in rows]), 2),
        "avg_return_pct": _round(_mean([r["return_pct"] for r in rows]), 2),
        "avg_excess_pct": _round(_mean([r["excess"] for r in rows]), 2),
        "beat_wig": sum(1 for r in rows if r["excess"] is not None and r["excess"] > 0),
        "with_benchmark": sum(1 for r in rows if r["excess"] is not None),
        "avg_holding_days": _round(_mean([r["holding_days"] for r in rows]), 1),
        "max_drawdown_pln": round(mdd, 2),
        "small_sample": n < MIN_SAMPLE,
    }


def _round(x, d):
    return round(x, d) if x is not None else None


def process_matrix(rows: List[Dict]) -> Dict:
    """2x2: decision quality (followed plan) × outcome (net > 0)."""
    m = {"deserved_win": [], "lucky_win": [], "bad_luck_loss": [], "deserved_loss": []}
    for r in rows:
        win = r["net_pnl"] > 0
        key = ("deserved_win" if win else "bad_luck_loss") if r["followed_plan"] else ("lucky_win" if win else "deserved_loss")
        m[key].append(r["id"])
    total = len(rows)
    followed = len(m["deserved_win"]) + len(m["bad_luck_loss"])
    return {
        "counts": {k: len(v) for k, v in m.items()},
        "ids": m,
        "plan_adherence_pct": round(followed / total * 100, 1) if total else None,
        "by_rule_broken": {
            rule: sum(1 for r in rows if r["process"].get(rule) is False) for rule in ("entry", "stop", "size")
        },
        "pnl_followed": round(sum(r["net_pnl"] for r in rows if r["followed_plan"]), 2),
        "pnl_not_followed": round(sum(r["net_pnl"] for r in rows if not r["followed_plan"]), 2),
    }


def hold_bucket(days: Optional[int]) -> str:
    if days is None:
        return "?"
    for lim, label in HOLD_BUCKETS:
        if days <= lim:
            return label
    return HOLD_BUCKETS[-1][1]


def patterns(rows: List[Dict]) -> Dict:
    def group(keyf):
        g: Dict[str, List[Dict]] = {}
        for r in rows:
            g.setdefault(keyf(r), []).append(r)
        out = []
        for k, rs in sorted(g.items(), key=lambda kv: -sum(x["net_pnl"] for x in kv[1])):
            out.append({
                "key": k, "trades": len(rs),
                "win_rate_pct": round(sum(1 for x in rs if x["net_pnl"] > 0) / len(rs) * 100, 1),
                "net_pnl": round(sum(x["net_pnl"] for x in rs), 2),
                "avg_r": _round(_mean([x["r"] for x in rs]), 2),
            })
        return out

    return {
        "setup": group(lambda r: r["setup"]),
        "account_type": group(lambda r: r["account_type"]),
        "holding": group(lambda r: hold_bucket(r["holding_days"])),
        "exit_reason": group(lambda r: r["exit_reason"] or "?"),
    }


def insights(st: Dict, pm: Dict, rows: List[Dict]) -> List[Dict]:
    """Deterministic, rule-based observations (keys translated in the report)."""
    out = []
    if st["trades"] == 0:
        return out
    if st["small_sample"]:
        out.append({"key": "i_small_sample", "n": st["trades"], "min": MIN_SAMPLE})
    if st["profit_factor"] is not None and st["profit_factor"] < 1:
        out.append({"key": "i_pf_below_1", "pf": st["profit_factor"]})
    if st["payoff_ratio"] is not None and st["payoff_ratio"] < 1 and (st["win_rate_pct"] or 0) < 60:
        out.append({"key": "i_payoff_low", "p": st["payoff_ratio"]})
    if pm["by_rule_broken"]["stop"]:
        broken = [r for r in rows if r["process"].get("stop") is False and r["net_pnl"] < 0]
        avg_broken = _mean([r["r"] for r in broken])
        out.append({"key": "i_stop_broken", "n": pm["by_rule_broken"]["stop"],
                    "avg_r": _round(avg_broken, 2)})
    if pm["counts"]["lucky_win"]:
        out.append({"key": "i_lucky", "n": pm["counts"]["lucky_win"]})
    if st["with_benchmark"] and st["avg_excess_pct"] is not None and st["avg_excess_pct"] < 0:
        out.append({"key": "i_lag_wig", "x": st["avg_excess_pct"]})
    big_r_losses = [r for r in rows if r["r"] is not None and r["r"] < -1.5]
    if big_r_losses:
        out.append({"key": "i_big_losses", "n": len(big_r_losses)})
    return out


def top_lessons(rows: List[Dict], k: int = 5) -> List[Dict]:
    withl = [r for r in rows if r["lessons"].strip()]
    withl.sort(key=lambda r: -abs(r["net_pnl"]))
    return [{"id": r["id"], "ticker": r["ticker"], "net_pnl": r["net_pnl"], "lessons": r["lessons"]} for r in withl[:k]]


# --------------------------------------------------------------------------
# PIT-38 estimate
# --------------------------------------------------------------------------

def fifo_matches(fills: List[Dict]) -> List[Dict]:
    """FIFO matching identical to gpw-trade-journal (kept in sync by tests)."""
    lots = []
    out = []
    for f in fills:
        if f["side"] == "buy":
            lots.append([f["date"], f["shares"], f["price"], f.get("fee", 0.0) / f["shares"]])
            continue
        left = f["shares"]
        sell_fee_ps = f.get("fee", 0.0) / f["shares"]
        while left > 0:
            if not lots:
                raise ReviewError("sell exceeds shares held")
            lot = lots[0]
            n = min(left, lot[1])
            out.append({
                "sell_date": f["date"], "buy_date": lot[0], "shares": n,
                "buy_price": lot[2], "sell_price": f["price"],
                "proceeds": round(n * f["price"], 2), "cost": round(n * lot[2], 2),
                "buy_fee": round(n * lot[3], 2), "sell_fee": round(n * sell_fee_ps, 2),
            })
            lot[1] -= n
            left -= n
            if lot[1] == 0:
                lots.pop(0)
    return out


def round_pln(x: Decimal) -> int:
    """Art. 63 Ordynacji podatkowej: <50 gr down, ≥50 gr up to the full złoty."""
    return int(x.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def pit38(theses: List[Dict], year: int, loss_deduction: float = 0.0) -> Dict:
    """Estimate PIT-38 section C/D for securities sold in ``year`` on regular accounts.

    Revenue and costs are recognised in the year of the SALE (FIFO cost of the
    sold shares + buy and sell commissions of those shares).
    """
    revenue = Decimal("0")
    costs = Decimal("0")
    sales = []
    excluded = []
    for th in theses:
        if th.get("paper"):
            continue  # paper trades are not real transactions
        if th.get("account_type", "regular") != "regular":
            if any(f["side"] == "sell" and f["date"].startswith(str(year)) for f in th.get("fills", [])):
                excluded.append({"id": th["id"], "account_type": th["account_type"]})
            continue
        for m in fifo_matches(th.get("fills", [])):
            if not m["sell_date"].startswith(str(year)):
                continue
            rev = Decimal(str(m["proceeds"]))
            cst = Decimal(str(m["cost"])) + Decimal(str(m["buy_fee"])) + Decimal(str(m["sell_fee"]))
            revenue += rev
            costs += cst
            sales.append(dict(m, ticker=th["ticker"], thesis_id=th["id"]))
    income = revenue - costs
    deduction = Decimal(str(loss_deduction or 0))
    if deduction < 0:
        raise ReviewError("loss deduction must be >= 0")
    if income > 0:
        deduction = min(deduction, income)
        base = round_pln(income - deduction)
        tax = round_pln(Decimal(base) * TAX_RATE)
    else:
        deduction = Decimal("0")
        base = 0
        tax = 0
    return {
        "year": year,
        "revenue": float(revenue.quantize(Decimal("0.01"))),
        "costs": float(costs.quantize(Decimal("0.01"))),
        "income": float(income.quantize(Decimal("0.01"))) if income > 0 else 0.0,
        "loss": float((-income).quantize(Decimal("0.01"))) if income < 0 else 0.0,
        "loss_deduction": float(deduction),
        "tax_base": base,
        "tax_rate_pct": 19,
        "tax": tax,
        "sales": sales,
        "excluded_tax_wrapped": excluded,
        "deadline": "{}-04-30".format(year + 1),
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

STRINGS = {
    "title": {"pl": "Przegląd transakcji – {p}", "en": "Trade review – {p}"},
    "paper_mode": {"pl": "📝 Tylko transakcje na niby (paper trading) – bez PIT-38.",
                   "en": "📝 Paper trades only – no PIT-38."},
    "paper_hidden": {"pl": "Pominięto {n} transakcji na niby – zobacz je osobno z --paper.",
                     "en": "{n} paper trade(s) excluded – review them separately with --paper."},
    "summary": {"pl": "Podsumowanie", "en": "Summary"},
    "trades": {"pl": "Zamknięte transakcje", "en": "Closed trades"},
    "win_rate": {"pl": "Skuteczność (zyskowne)", "en": "Win rate"},
    "net": {"pl": "Wynik netto (po prowizjach, przed podatkiem)", "en": "Net P&L (after commissions, before tax)"},
    "fees": {"pl": "Zapłacone prowizje", "en": "Commissions paid"},
    "avg_win": {"pl": "Średni zysk", "en": "Average win"},
    "avg_loss": {"pl": "Średnia strata", "en": "Average loss"},
    "payoff": {"pl": "Średni zysk / średnia strata", "en": "Payoff ratio"},
    "expectancy": {"pl": "Wartość oczekiwana na transakcję", "en": "Expectancy per trade"},
    "pf": {"pl": "Profit factor (zyski / straty)", "en": "Profit factor (wins / losses)"},
    "avg_r": {"pl": "Średni wynik w R", "en": "Average R multiple"},
    "mdd": {"pl": "Maks. obsunięcie (zamknięte transakcje)", "en": "Max drawdown (closed trades)"},
    "excess": {"pl": "Średnio vs WIG", "en": "Average vs WIG"},
    "beat": {"pl": "Pobite WIG", "en": "Beat WIG"},
    "hold": {"pl": "Średni czas trzymania", "en": "Average holding period"},
    "days": {"pl": "{n} dni", "en": "{n} days"},
    "process": {"pl": "Proces a wynik", "en": "Process vs outcome"},
    "pm_head": {"pl": "| | Zysk | Strata |", "en": "| | Win | Loss |"},
    "pm_followed": {"pl": "**Zgodnie z planem**", "en": "**Followed plan**"},
    "pm_not": {"pl": "**Wbrew planowi**", "en": "**Broke the plan**"},
    "deserved_win": {"pl": "zasłużony zysk", "en": "deserved win"},
    "lucky_win": {"pl": "szczęśliwy zysk", "en": "lucky win"},
    "bad_luck_loss": {"pl": "pech (dobra decyzja)", "en": "bad-luck loss"},
    "deserved_loss": {"pl": "zasłużona strata", "en": "deserved loss"},
    "adherence": {"pl": "Trzymanie się planu: {p} transakcji. Wynik zgodnie z planem: {a}; wbrew planowi: {b}.",
                  "en": "Plan adherence: {p} of trades. P&L when following the plan: {a}; when breaking it: {b}."},
    "broken": {"pl": "Złamane zasady – wejście: {e}, stop: {s}, wielkość: {z}", "en": "Rules broken – entry: {e}, stop: {s}, size: {z}"},
    "patterns": {"pl": "Wzorce", "en": "Patterns"},
    "by_setup": {"pl": "Według typu setupu", "en": "By setup"},
    "by_account": {"pl": "Według rachunku", "en": "By account type"},
    "by_holding": {"pl": "Według czasu trzymania (dni)", "en": "By holding period (days)"},
    "by_exit": {"pl": "Według powodu wyjścia", "en": "By exit reason"},
    "pat_head": {"pl": "| | Liczba | Skuteczność | Wynik netto | Śr. R |", "en": "| | Trades | Win rate | Net P&L | Avg R |"},
    "insights": {"pl": "Obserwacje", "en": "Observations"},
    "lessons": {"pl": "Najważniejsze wnioski", "en": "Top lessons"},
    "no_trades": {"pl": "Brak zamkniętych transakcji w tym okresie.", "en": "No closed trades in this period."},
    "i_small_sample": {"pl": "Mała próba (transakcji: {n}) – poniżej {min} statystyki są bardzo niepewne; traktuj je jako wskazówkę, nie dowód.",
                       "en": "Only {n} trades – below {min} the statistics are very noisy; treat them as hints, not proof."},
    "i_pf_below_1": {"pl": "Profit factor {pf} < 1: łączne straty przewyższają zyski. Obecny sposób grania traci pieniądze.",
                     "en": "Profit factor {pf} < 1: total losses exceed total wins. The current approach loses money."},
    "i_payoff_low": {"pl": "Średnia strata jest większa niż średni zysk (stosunek {p}). Tniesz zyski za wcześnie lub przetrzymujesz straty?",
                     "en": "Average loss is larger than average win (ratio {p}). Cutting winners early or holding losers?"},
    "i_stop_broken": {"pl": "Stop nie był respektowany w {n} transakcjach (średni wynik stratnych z nich: {avg_r}R). To najdroższy błąd w tradingu.",
                      "en": "The stop was not respected in {n} trades (average of the losing ones: {avg_r}R). This is the most expensive trading mistake."},
    "i_lucky": {"pl": "Zyskowne transakcje mimo złamania planu: {n} – nie wyciągaj z nich wniosku, że plan jest zbędny.",
                "en": "{n} win(s) despite breaking the plan – don't conclude the plan is unnecessary."},
    "i_lag_wig": {"pl": "Średnio {x} pp gorzej niż WIG w tym samym czasie – tani ETF na WIG/WIG20 dałby lepszy wynik.",
                  "en": "On average {x} pp worse than WIG over the same periods – a cheap WIG/WIG20 ETF would have done better."},
    "i_big_losses": {"pl": "Straty większe niż 1,5R: {n} – sprawdź luki cenowe i przesuwanie stopa.",
                     "en": "{n} losses larger than 1.5R – check price gaps and stop moving."},
    "pit_title": {"pl": "Szacunek PIT-38 za {y} (rachunki zwykłe)", "en": "PIT-38 estimate for {y} (regular accounts)"},
    "pit_rev": {"pl": "Przychód (wartość sprzedaży)", "en": "Revenue (sale proceeds)"},
    "pit_cost": {"pl": "Koszty uzyskania przychodu (zakup FIFO + prowizje)", "en": "Deductible costs (FIFO purchase cost + commissions)"},
    "pit_income": {"pl": "Dochód", "en": "Income (gain)"},
    "pit_loss": {"pl": "Strata (do rozliczenia w kolejnych latach)", "en": "Loss (can be carried forward)"},
    "pit_ded": {"pl": "Odliczenie strat z lat ubiegłych", "en": "Prior-year loss deduction"},
    "pit_base": {"pl": "Podstawa opodatkowania (zaokrąglona)", "en": "Tax base (rounded)"},
    "pit_tax": {"pl": "Podatek 19% (zaokrąglony)", "en": "Tax 19% (rounded)"},
    "pit_deadline": {"pl": "Termin złożenia PIT-38: {d}", "en": "PIT-38 filing deadline: {d}"},
    "pit_sales": {"pl": "Sprzedaże ujęte w szacunku: {n}", "en": "Sales included: {n}"},
    "pit_excluded": {"pl": "Pominięto sprzedaże na IKE/IKZE/OKI ({n}) – nie wchodzą do PIT-38.",
                     "en": "Excluded IKE/IKZE/OKI sales ({n}) – they are not reported on PIT-38."},
    "pit_notes": {
        "pl": [
            "Oficjalnym źródłem jest PIT-8C od brokera (do końca lutego) – porównaj z nim ten szacunek.",
            "Uwzględnione są tylko transakcje zapisane w dzienniku. Dywidendy z polskich spółek są opodatkowane u źródła i nie wchodzą do PIT-38.",
            "Transakcje w walutach obcych przelicza się po średnim kursie NBP z dnia roboczego poprzedzającego (art. 11a) – ten szacunek tego nie robi.",
            "Stratę można odliczać przez 5 kolejnych lat: maks. 50% straty w jednym roku albo jednorazowo do 5 mln zł (resztę wg zasady 50%). Zweryfikuj w ISAP / z doradcą podatkowym.",
            "Straty z akcji nie kompensują zysków z kryptowalut (osobne źródło).",
        ],
        "en": [
            "The official source is the broker's PIT-8C (by end of February) – compare this estimate with it.",
            "Only trades recorded in the journal are included. Dividends from Polish companies are taxed at source and are not part of PIT-38.",
            "Foreign-currency trades must be converted at the NBP average rate from the business day before (art. 11a) – this estimate does not do that.",
            "A loss can be deducted over the next 5 years: max 50% of it in one year, or once up to PLN 5m (rest under the 50% rule). Verify in ISAP / with a tax adviser.",
            "Share losses cannot offset crypto gains (separate income source).",
        ],
    },
    "pit_disclaimer": {"pl": "To szacunek pomocniczy, nie porada podatkowa ani wypełniony formularz.",
                       "en": "This is an estimate for orientation, not tax advice or a completed form."},
    "sources": {"pl": "Źródła", "en": "Sources"},
}


def _pf(x, lang, no_losses=False):
    if x is None:
        return "∞" if no_losses else "—"
    return fmt.num(x, 2, lang)


def render(report: Dict, lang: str = "pl") -> str:
    t = T(STRINGS, lang)
    P = lambda x: fmt.pln(x, 2, lang)  # noqa: E731
    st, pm = report["stats"], report["process"]
    L = ["# " + t("title", p=report["period"]), ""]
    if report.get("paper"):
        L += ["> " + t("paper_mode"), ""]
    elif report.get("other_mode_trades"):
        L += ["_" + t("paper_hidden", n=report["other_mode_trades"]) + "_", ""]
    if not st["trades"]:
        L += [t("no_trades"), ""]
    else:
        L += ["## " + t("summary"), "", "| | |", "|---|---|"]
        rows = [
            (t("trades"), "{} ({}/{})".format(st["trades"], st["wins"], st["losses"])),
            (t("win_rate"), fmt.pct(st["win_rate_pct"], 1, lang)),
            ("**" + t("net") + "**", "**" + P(st["net_pnl"]) + "**"),
            (t("fees"), P(st["fees"])),
            (t("avg_win"), P(st["avg_win"])),
            (t("avg_loss"), P(st["avg_loss"])),
            (t("payoff"), _pf(st["payoff_ratio"], lang)),
            (t("expectancy"), P(st["expectancy"])),
            (t("pf"), _pf(st["profit_factor"], lang, st["profit_factor_no_losses"])),
            (t("avg_r"), (fmt.num(st["avg_r"], 2, lang) + "R") if st["avg_r"] is not None else "—"),
            (t("mdd"), P(st["max_drawdown_pln"])),
            (t("hold"), t("days", n=fmt.num(st["avg_holding_days"], 0, lang)) if st["avg_holding_days"] is not None else "—"),
        ]
        if st["with_benchmark"]:
            rows.append((t("excess"), fmt.pct(st["avg_excess_pct"], 2, lang, signed=True)[:-1] + " pp"))
            rows.append((t("beat"), "{}/{}".format(st["beat_wig"], st["with_benchmark"])))
        L += ["| {} | {} |".format(a, b) for a, b in rows]
        c = pm["counts"]
        L += ["", "## " + t("process"), "", t("pm_head"), "|---|---|---|",
              "| {} | {} {} | {} {} |".format(t("pm_followed"), c["deserved_win"], t("deserved_win"), c["bad_luck_loss"], t("bad_luck_loss")),
              "| {} | {} {} | {} {} |".format(t("pm_not"), c["lucky_win"], t("lucky_win"), c["deserved_loss"], t("deserved_loss")),
              "",
              t("adherence", p=fmt.pct(pm["plan_adherence_pct"], 0, lang), a=P(pm["pnl_followed"]), b=P(pm["pnl_not_followed"])),
              "",
              t("broken", e=pm["by_rule_broken"]["entry"], s=pm["by_rule_broken"]["stop"], z=pm["by_rule_broken"]["size"])]
        if report["insights"]:
            L += ["", "## " + t("insights"), ""]
            for i in report["insights"]:
                kw = {k: v for k, v in i.items() if k != "key"}
                for k in ("pf", "p", "x", "avg_r"):
                    if k in kw and isinstance(kw[k], float):
                        kw[k] = fmt.num(kw[k], 2, lang)
                L.append("- " + t(i["key"], **kw))
        L += ["", "## " + t("patterns")]
        for key, title in (("setup", "by_setup"), ("holding", "by_holding"), ("exit_reason", "by_exit"), ("account_type", "by_account")):
            L += ["", "### " + t(title), "", t("pat_head"), "|---|---:|---:|---:|---:|"]
            for g in report["patterns"][key]:
                L.append("| {} | {} | {} | {} | {} |".format(
                    g["key"], g["trades"], fmt.pct(g["win_rate_pct"], 0, lang), P(g["net_pnl"]),
                    fmt.num(g["avg_r"], 2, lang) if g["avg_r"] is not None else "—"))
        if report["lessons"]:
            L += ["", "## " + t("lessons"), ""]
            for x in report["lessons"]:
                L.append("- **{}** ({}): {}".format(x["ticker"], P(x["net_pnl"]), x["lessons"]))
    pit = report.get("pit38")
    if pit:
        L += ["", "## " + t("pit_title", y=pit["year"]), "", "| | |", "|---|---|",
              "| {} | {} |".format(t("pit_rev"), P(pit["revenue"])),
              "| {} | {} |".format(t("pit_cost"), P(pit["costs"]))]
        if pit["loss"]:
            L.append("| {} | {} |".format(t("pit_loss"), P(pit["loss"])))
        else:
            L.append("| {} | {} |".format(t("pit_income"), P(pit["income"])))
            if pit["loss_deduction"]:
                L.append("| {} | {} |".format(t("pit_ded"), P(pit["loss_deduction"])))
        L.append("| {} | {} |".format(t("pit_base"), fmt.pln(pit["tax_base"], 0, lang)))
        L.append("| **{}** | **{}** |".format(t("pit_tax"), fmt.pln(pit["tax"], 0, lang)))
        L += ["", t("pit_deadline", d=pit["deadline"]) + " · " + t("pit_sales", n=len(pit["sales"]))]
        if pit["excluded_tax_wrapped"]:
            L += ["", t("pit_excluded", n=len(pit["excluded_tax_wrapped"]))]
        L += [""] + ["- " + n for n in STRINGS["pit_notes"][lang]]
        L += ["", "_" + t("pit_disclaimer") + "_"]
    L += ["", "## " + t("sources"), "",
          "- gpw-trade-journal: {}".format(report["journal_dir"]),
          "- PIT-38: https://www.podatki.gov.pl/pit/ · ustawa o PIT (art. 22, 24 ust. 10, 30b): https://isap.sejm.gov.pl/",
          "", "---", "_" + DISCLAIMER[lang] + "_"]
    return "\n".join(L) + "\n"


def build_report(theses: List[Dict], month=None, year=None, pit_year=None, loss_deduction=0.0, journal_dir="",
                 paper: bool = False) -> Dict:
    start, end, label = period_bounds(month, year)
    rows = closed_trades(theses, start, end, paper=paper)
    other = len(closed_trades(theses, start, end, paper=not paper))
    st = stats(rows)
    pm = process_matrix(rows)
    rep = {
        "schema_version": "1.0",
        "skill": "gpw-trade-review",
        "period": label,
        "period_start": start.isoformat() if label != "all" else None,
        "period_end": end.isoformat() if label != "all" else None,
        "generated_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "journal_dir": str(journal_dir),
        "paper": bool(paper),
        "other_mode_trades": other,
        "stats": st,
        "process": pm,
        "patterns": patterns(rows),
        "insights": insights(st, pm, rows),
        "lessons": top_lessons(rows),
        "trades": rows,
    }
    if pit_year and not paper:
        rep["pit38"] = pit38(theses, pit_year, loss_deduction)
    return rep


def build_parser():
    p = argparse.ArgumentParser(description="Statistics, process review and PIT-38 estimate from the GPW trade journal.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--month", help="YYYY-MM")
    g.add_argument("--year", type=int, help="YYYY (also produces the PIT-38 estimate)")
    g.add_argument("--all", action="store_true", help="All closed trades (default)")
    p.add_argument("--no-pit38", action="store_true", help="Skip the PIT-38 estimate for --year")
    p.add_argument("--paper", action="store_true", help="Review paper trades only (never mixed with real ones)")
    p.add_argument("--loss-deduction", type=float, default=0.0, help="Prior-year loss to deduct (PLN), user-supplied")
    p.add_argument("--journal-dir", type=Path, default=None, help="gpw-trade-journal state dir (default ~/.gpw-trading-skills/journal)")
    p.add_argument("--lang", choices=["pl", "en"], default="pl")
    p.add_argument("--output-dir", default=".")
    p.add_argument("--no-files", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.month:
        try:
            y, m = map(int, args.month.split("-"))
            dt.date(y, m, 1)
        except ValueError:
            build_parser().error("--month must be YYYY-MM")
    jd = args.journal_dir or default_journal_dir()
    try:
        theses = load_theses(jd)
        if not theses:
            print("error: no journal entries found in {} (use gpw-trade-journal first)".format(jd), file=sys.stderr)
            return 1
        rep = build_report(theses, month=args.month, year=args.year,
                           pit_year=args.year if args.year and not args.no_pit38 else None,
                           loss_deduction=args.loss_deduction, journal_dir=jd, paper=args.paper)
    except (ReviewError, KeyError, ValueError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    md = render(rep, args.lang)
    print(md)
    if not args.no_files:
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        jp, mp = out / "gpw_trade_review_{}.json".format(stamp), out / "gpw_trade_review_{}.md".format(stamp)
        jp.write_text(json.dumps(rep, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        mp.write_text(md, encoding="utf-8")
        print("JSON: {}\nMarkdown: {}".format(jp, mp), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
