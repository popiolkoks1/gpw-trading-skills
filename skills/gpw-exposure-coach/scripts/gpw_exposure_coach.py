#!/usr/bin/env python3
"""GPW Exposure Coach - CLI.

Combines the newest gpw-market-regime, gpw-market-breadth and
gpw-sector-trend JSON reports with the gpw-trade-journal (losing streak,
drawdown, open risk) into a posture and four limits: exposure ceiling, risk
per trade, new positions per week and a circuit breaker. Never names a stock.

    python3 scripts/gpw_exposure_coach.py --input-dir <reports> --account-size 100000 --lang pl

Exit codes: 0 ok, 1 no market input at all / unreadable input, 2 bad arguments.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import gpw_archive, journal_io, plcal, provenance, reports  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct, pln  # noqa: E402

import exposure_engine as ee  # noqa: E402

VERSION = "1.0.0"
PATTERNS = {"regime": "gpw_market_regime_*.json", "breadth": "gpw_breadth_*.json", "sector": "gpw_sector_trend_*.json"}
SKILL_OF = {"regime": "gpw-market-regime", "breadth": "gpw-market-breadth", "sector": "gpw-sector-trend"}

STRINGS = {
    "title": {"pl": "Trener ekspozycji GPW — raport", "en": "GPW Exposure Coach Report"},
    "as_of": {"pl": "Dane rynkowe na sesję", "en": "Market data as of session"},
    "generated": {"pl": "Wygenerowano", "en": "Generated"},
    "stale_input": {"pl": "⚠️ UWAGA: raport {name} jest nieaktualny ({n} sesji) — odśwież go, zanim oprzesz się na wyniku.",
                    "en": "⚠️ WARNING: the {name} report is stale ({n} sessions) — refresh it before relying on the result."},
    "missing_input": {"pl": "⚠️ Brak raportu {name} — wynik opiera się na pozostałych danych (niższa pewność).",
                      "en": "⚠️ No {name} report — the result relies on the other inputs (lower confidence)."},
    "summary": {"pl": "W skrócie", "en": "Summary"},
    "posture_line": {"pl": "**Postawa: {p}** — {expl}", "en": "**Posture: {p}** — {expl}"},
    "p_offensive": {"pl": "ofensywna", "en": "offensive"},
    "p_normal": {"pl": "normalna", "en": "normal"},
    "p_defensive": {"pl": "defensywna", "en": "defensive"},
    "p_capital_preservation": {"pl": "ochrona kapitału", "en": "capital preservation"},
    "e_offensive": {"pl": "rynek sprzyja; można w pełni korzystać ze swojego planu, nadal z twardymi stopami.",
                    "en": "the market is supportive; you can use your full plan, still with hard stops."},
    "e_normal": {"pl": "warunki mieszane; działaj według planu, ale z mniejszym ryzykiem i bez pośpiechu.",
                 "en": "mixed conditions; follow your plan with smaller risk and no rush."},
    "e_defensive": {"pl": "warunki słabe; tylko najlepsze okazje, małe pozycje, więcej gotówki.",
                    "en": "weak conditions; only the best setups, small positions, more cash."},
    "e_capital_preservation": {"pl": "rynek niesprzyjający; priorytetem jest ochrona kapitału, a nie szukanie nowych transakcji.",
                               "en": "hostile market; the priority is protecting capital, not finding new trades."},
    "col_limit": {"pl": "Limit", "en": "Limit"},
    "col_value": {"pl": "Wartość", "en": "Value"},
    "col_meaning": {"pl": "Co to znaczy", "en": "What it means"},
    "l_ceiling": {"pl": "Maks. ekspozycja na akcje", "en": "Max equity exposure"},
    "m_ceiling": {"pl": "suma wartości wszystkich pozycji nie powinna przekraczać tej części kapitału",
                  "en": "total value of all positions should stay below this share of the account"},
    "l_risk": {"pl": "Maks. ryzyko na transakcję", "en": "Max risk per trade"},
    "m_risk": {"pl": "strata przy stopie (wejście − stop) × liczba akcji, jako % kapitału — do kalkulatora gpw-position-sizer",
               "en": "loss at the stop (entry − stop) × shares, as % of the account — feed it to gpw-position-sizer"},
    "m_risk_pln": {"pl": " (≈ {v} przy koncie {a})", "en": " (≈ {v} on a {a} account)"},
    "l_new": {"pl": "Maks. nowych pozycji w tygodniu", "en": "Max new positions per week"},
    "m_new": {"pl": "tempo wchodzenia w rynek — chroni przed serią wejść w złym momencie",
              "en": "pace of new entries — protects against a burst of entries at a bad time"},
    "l_cb": {"pl": "Bezpiecznik (circuit breaker)", "en": "Circuit breaker"},
    "cb_ok": {"pl": "OK", "en": "OK"},
    "cb_reduce": {"pl": "OGRANICZ (ryzyko o połowę)", "en": "REDUCE (risk halved)"},
    "cb_pause": {"pl": "PAUZA (bez nowych transakcji)", "en": "PAUSE (no new trades)"},
    "m_cb": {"pl": "wynika z Twoich ostatnich wyników w dzienniku (seria strat, obsunięcie)",
             "en": "based on your recent journal results (losing streak, drawdown)"},
    "m_cb_na": {"pl": "nie oceniono — brak dziennika transakcji", "en": "not assessed — no trade journal"},
    "why": {"pl": "Dlaczego", "en": "Why"},
    "r_regime": {"pl": "Reżim rynku ({idx}): **{state}**, ryzyko szczytu {risk}/100, dni dystrybucji {dd} → ocena {score}/100.",
                 "en": "Market regime ({idx}): **{state}**, top risk {risk}/100, distribution days {dd} → score {score}/100."},
    "r_breadth": {"pl": "Szerokość rynku: {score}/100 (strefa {zone}).", "en": "Market breadth: {score}/100 (zone {zone})."},
    "r_sector": {"pl": "Trendy sektorowe: {score}/100 (strefa {zone}){warn}.", "en": "Sector trend: {score}/100 (zone {zone}){warn}."},
    "r_sector_warn": {"pl": "; ostrzeżenia: {w}", "en": "; warnings: {w}"},
    "r_combined": {"pl": "Łączna ocena (ważona): **{score}/100** → postawa {p}, pułap {c}.",
                   "en": "Combined (weighted) score: **{score}/100** → posture {p}, ceiling {c}."},
    "r_journal": {"pl": "Dziennik: zamknięte transakcje: {n}, seria strat: {s}, obsunięcie {r}R{p}.",
                  "en": "Journal: {n} closed trades, losing streak {s}, drawdown {r}R{p}."},
    "r_journal_pct": {"pl": " / {v} ({x} konta)", "en": " / {v} ({x} of the account)"},
    "r_journal_na": {"pl": "Dziennik: brak danych — bezpiecznik nieoceniony.", "en": "Journal: no data — circuit breaker not assessed."},
    "r_heat": {"pl": "Otwarte ryzyko do stopów: {v}{p}; pułap dla tej postawy {cap}.",
               "en": "Open risk to stops: {v}{p}; cap for this posture {cap}."},
    "r_heat_pct": {"pl": " = {h} konta, ekspozycja {e}", "en": " = {h} of the account, exposure {e}"},
    "r_no_stop": {"pl": "⚠️ Pozycje bez stopu w dzienniku: {t} — ich ryzyka nie da się policzyć.",
                  "en": "⚠️ Positions without a stop in the journal: {t} — their risk cannot be measured."},
    "caps": {"pl": "Zastosowane ograniczenia", "en": "Caps applied"},
    "c_regime_cap": {"pl": "Reżim „{state}” ogranicza ekspozycję do {cap}.", "en": "Regime \"{state}\" caps exposure at {cap}."},
    "c_regime_posture": {"pl": "Reżim „{state}” nie pozwala na postawę lepszą niż {p}.", "en": "Regime \"{state}\" allows no posture better than {p}."},
    "c_top_risk_cap": {"pl": "Ryzyko szczytu {risk} ≥ {lim} ogranicza ekspozycję do {cap}.", "en": "Top risk {risk} ≥ {lim} caps exposure at {cap}."},
    "c_breaker_reduce": {"pl": "Bezpiecznik OGRANICZ: ryzyko na transakcję i liczba nowych pozycji o połowę.",
                         "en": "Circuit breaker REDUCE: risk per trade and new positions halved."},
    "c_breaker_pause": {"pl": "Bezpiecznik PAUZA: bez nowych transakcji do {until}. Wykorzystaj ten czas na przegląd dziennika (gpw-trade-review).",
                        "en": "Circuit breaker PAUSE: no new trades until {until}. Use the time to review the journal (gpw-trade-review)."},
    "c_heat_full": {"pl": "Otwarte ryzyko {heat} ≥ pułapu {cap} — bez nowych pozycji, dopóki ryzyko nie spadnie (np. stopy podciągnięte do progu rentowności).",
                    "en": "Open risk {heat} ≥ cap {cap} — no new positions until it falls (e.g. stops raised to break-even)."},
    "c_above_ceiling": {"pl": "Obecna ekspozycja {exposure} ≥ pułapu {ceiling} — bez dokładania nowych pozycji.",
                        "en": "Current exposure {exposure} ≥ ceiling {ceiling} — no new positions on top."},
    "none": {"pl": "brak", "en": "none"},
    "confidence": {"pl": "Pewność oceny", "en": "Confidence"},
    "conf_high": {"pl": "wysoka", "en": "high"},
    "conf_medium": {"pl": "średnia", "en": "medium"},
    "conf_low": {"pl": "niska", "en": "low"},
    "conf_txt": {"pl": "{lvl} ({s}) — udział dostępnych i świeżych danych rynkowych w wagach.",
                 "en": "{lvl} ({s}) — share of the weights covered by available, fresh market inputs."},
    "plain": {"pl": "Co z tym zrobić", "en": "What to do with it"},
    "plain_txt": {"pl": "To są limity dla Twoich własnych decyzji, nie sygnały kupna ani sprzedaży. Przed każdą nową transakcją sprawdź: czy po niej ekspozycja zmieści się w pułapie, czy ryzyko do stopu nie przekracza limitu (gpw-position-sizer) i czy nie wyczerpałeś tygodniowego limitu wejść (gpw-pre-trade-gate).",
                  "en": "These are limits for your own decisions, not buy or sell signals. Before each new trade check: will exposure stay under the ceiling, is the risk to the stop within the limit (gpw-position-sizer), and have you used up the weekly entry limit (gpw-pre-trade-gate)."},
    "details": {"pl": "Szczegóły danych wejściowych", "en": "Input details"},
    "col_input": {"pl": "Dane", "en": "Input"},
    "col_file": {"pl": "Plik", "en": "File"},
    "col_asof": {"pl": "Dane na", "en": "As of"},
    "col_behind": {"pl": "Sesji wstecz", "en": "Sessions behind"},
    "col_score": {"pl": "Ocena", "en": "Score"},
    "col_weight": {"pl": "Waga efektywna", "en": "Effective weight"},
    "in_regime": {"pl": "Reżim rynku", "en": "Market regime"},
    "in_breadth": {"pl": "Szerokość rynku", "en": "Market breadth"},
    "in_sector": {"pl": "Trendy sektorowe", "en": "Sector trend"},
    "missing": {"pl": "brak", "en": "missing"},
    "cb_title": {"pl": "Bezpiecznik — szczegóły", "en": "Circuit breaker — details"},
    "cb_rules": {"pl": "Reguły: 3 straty z rzędu lub obsunięcie ≥ 5% konta (≥ 5R bez wielkości konta) → OGRANICZ (ryzyko ×0,5); 5 strat z rzędu lub ≥ 10% (≥ 10R) → PAUZA na {d} dni od ostatniego zamknięcia, potem OGRANICZ do pierwszego zysku.",
                 "en": "Rules: 3 losses in a row or drawdown ≥ 5% of the account (≥ 5R without account size) → REDUCE (risk ×0.5); 5 in a row or ≥ 10% (≥ 10R) → PAUSE for {d} days after the last close, then REDUCE until the first win."},
    "cbr_streak_pause": {"pl": "{n} strat z rzędu (próg {limit})", "en": "{n} losses in a row (limit {limit})"},
    "cbr_streak_reduce": {"pl": "{n} straty z rzędu (próg {limit})", "en": "{n} losses in a row (limit {limit})"},
    "cbr_dd_pct_pause": {"pl": "obsunięcie {pct}% konta (próg {limit}%)", "en": "drawdown {pct}% of the account (limit {limit}%)"},
    "cbr_dd_pct_reduce": {"pl": "obsunięcie {pct}% konta (próg {limit}%)", "en": "drawdown {pct}% of the account (limit {limit}%)"},
    "cbr_dd_r_pause": {"pl": "obsunięcie {r}R (próg {limit}R)", "en": "drawdown {r}R (limit {limit}R)"},
    "cbr_dd_r_reduce": {"pl": "obsunięcie {r}R (próg {limit}R)", "en": "drawdown {r}R (limit {limit}R)"},
    "cbr_pause_elapsed": {"pl": "pauza minęła {until} — teraz OGRANICZ do pierwszego zysku", "en": "pause ended {until} — now REDUCE until the first win"},
    "col_ticker": {"pl": "Spółka", "en": "Ticker"},
    "col_date": {"pl": "Zamknięta", "en": "Closed"},
    "col_pnl": {"pl": "Wynik netto", "en": "Net P&L"},
    "col_r": {"pl": "R", "en": "R"},
    "heat_title": {"pl": "Otwarte pozycje — ryzyko do stopów", "en": "Open positions — risk to stops"},
    "col_shares": {"pl": "Akcje", "en": "Shares"},
    "col_price": {"pl": "Kurs", "en": "Price"},
    "col_stop": {"pl": "Stop", "en": "Stop"},
    "col_val": {"pl": "Wartość", "en": "Value"},
    "col_risk": {"pl": "Ryzyko", "en": "Risk"},
    "src_last_close": {"pl": "ostatnie zamknięcie", "en": "last close"},
    "src_avg_price": {"pl": "średnia cena zakupu", "en": "average entry"},
    "method": {"pl": "Metoda (skrót)", "en": "Method (short)"},
    "method_txt": {"pl": "Ocena = 40% reżim + 35% szerokość + 25% sektory (wagi przeliczane, gdy czegoś brakuje). Ocena → postawa (≥70 ofensywna, ≥50 normalna, ≥30 defensywna, niżej ochrona kapitału) i pułap ekspozycji. Reżim działa jak twardy limit: korekta 25%, bessa 15%, próba odbicia 40%, trend pod presją 80%. Ryzyko na transakcję = {b}% × (1 / 0,75 / 0,5 / 0,25 wg postawy). Uzasadnienie progów: references/methodology.md.",
                   "en": "Score = 40% regime + 35% breadth + 25% sectors (renormalised when an input is missing). Score → posture (≥70 offensive, ≥50 normal, ≥30 defensive, below: capital preservation) and exposure ceiling. The regime is a hard cap: correction 25%, bear 15%, rally attempt 40%, under pressure 80%. Risk per trade = {b}% × (1 / 0.75 / 0.5 / 0.25 by posture). Threshold rationale: references/methodology.md."},
    "sources": {"pl": "Źródła", "en": "Sources"},
}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="GPW exposure coach: posture and risk limits from market reports + journal (PL/EN)")
    p.add_argument("--lang", choices=["pl", "en"], default="pl")
    p.add_argument("--output-dir", default=".")
    p.add_argument("--no-files", action="store_true", help="print Markdown only, write no files")
    p.add_argument("--input-dir", default=".", help="where to auto-discover the newest gpw_*.json reports")
    p.add_argument("--regime-json", default=None)
    p.add_argument("--breadth-json", default=None)
    p.add_argument("--sector-json", default=None)
    p.add_argument("--state-dir", default=None, help="gpw-trade-journal state dir (default ~/.gpw-trading-skills/journal)")
    p.add_argument("--no-journal", action="store_true", help="ignore the journal (no circuit breaker / heat)")
    p.add_argument("--include-paper", action="store_true", help="include paper trades from the journal")
    p.add_argument("--account-size", type=float, default=None, help="current account value in PLN")
    p.add_argument("--base-risk-pct", type=float, default=1.0, help="your normal risk per trade in %% (default 1.0)")
    p.add_argument("--pause-days", type=int, default=ee.DEFAULT_PAUSE_DAYS)
    p.add_argument("--as-of", default=None, help="evaluation date YYYY-MM-DD (default: today, Warsaw)")
    p.add_argument("--cache-dir", default=None, help="GPW archive cache, used read-only for last closes")
    p.add_argument("--no-prices", action="store_true", help="do not read last closes from the cache (use avg entry)")
    a = p.parse_args(argv)
    if a.account_size is not None and a.account_size <= 0:
        p.error("--account-size must be > 0")
    if not (0.05 <= a.base_risk_pct <= 3.0):
        p.error("--base-risk-pct must be between 0.05 and 3.0")
    if not (1 <= a.pause_days <= 60):
        p.error("--pause-days must be between 1 and 60")
    if a.as_of:
        try:
            dt.date.fromisoformat(a.as_of)
        except ValueError:
            p.error("--as-of must be YYYY-MM-DD")
    return a


def sessions_between(as_of: str, eval_date: Optional[dt.date]) -> int:
    d = dt.date.fromisoformat(as_of)
    if eval_date is None:
        return provenance.sessions_behind(d)
    n, day = 0, d + dt.timedelta(days=1)
    while day <= eval_date:
        if plcal.is_probable_session(day):
            n += 1
        day += dt.timedelta(days=1)
    return n


def load_inputs(args, eval_date: Optional[dt.date]) -> (Dict, Dict):
    """Returns (normalised inputs for the engine, info per input for the report)."""
    inputs, info = {}, {}
    for name in ("regime", "breadth", "sector"):
        explicit = getattr(args, name + "_json")
        path = None
        if explicit:
            path = Path(explicit).expanduser()
            if not path.exists():
                raise ee.InputError("--{}-json: file not found: {}".format(name, path))
        else:
            d = Path(args.input_dir).expanduser()
            files = sorted(d.glob(PATTERNS[name])) if d.is_dir() else []
            path = None
            for f in reversed(files):  # newest first; never a variant report type (RRG)
                try:
                    if reports.is_variant(json.loads(f.read_text(encoding="utf-8")), SKILL_OF[name]):
                        continue
                except (OSError, ValueError):
                    pass  # unreadable: reported below
                path = f
                break
        if path is None:
            inputs[name], info[name] = None, {"used": False, "reason": "missing"}
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if reports.is_variant(data, SKILL_OF[name]):
                raise ValueError("this is a gpw-sector-trend RRG report (mode rrg), not the sector-trend report")
            norm = ee.PARSERS[name](data)
        except (OSError, ValueError) as exc:
            if explicit:
                raise ee.InputError("--{}-json {}: {}".format(name, path, exc))
            print("warning: skipping {}: {}".format(path, exc), file=sys.stderr)
            inputs[name], info[name] = None, {"used": False, "reason": "unreadable", "file": str(path)}
            continue
        if not norm.get("as_of"):
            inputs[name], info[name] = None, {"used": False, "reason": "no_as_of", "file": str(path)}
            continue
        if eval_date and norm["as_of"] > eval_date.isoformat():
            inputs[name], info[name] = None, {"used": False, "reason": "future", "file": str(path), "as_of": norm["as_of"]}
            continue
        behind = sessions_between(norm["as_of"], eval_date)
        norm["sessions_behind"] = behind
        norm["stale"] = behind > ee.STALE_SESSIONS
        inputs[name] = norm
        info[name] = dict(norm, used=True, file=str(path))
    return inputs, info


def last_closes(cache_dir: Optional[str], end: Optional[dt.date]) -> Dict[str, float]:
    try:
        sess = gpw_archive.load_sessions(1, end=end, cache_dir=Path(cache_dir).expanduser() if cache_dir else None,
                                         offline=True, progress=False)
    except Exception:  # cache is optional: any problem -> fall back to entry prices
        return {}
    if not sess:
        return {}
    return {r["name"]: r["close"] for r in sess[-1]["rows"] if r.get("close") and (r.get("currency") or "PLN") == "PLN"}


def run(args) -> Dict:
    eval_date = dt.date.fromisoformat(args.as_of) if args.as_of else None
    inputs, info = load_inputs(args, eval_date)
    if not any(inputs.values()):
        raise ee.InputError(
            "No market input found (gpw_market_regime_*.json, gpw_breadth_*.json, gpw_sector_trend_*.json) in {}. "
            "The coach cannot assess exposure without at least one of them - run gpw-market-regime, "
            "gpw-market-breadth and/or gpw-sector-trend with --output-dir pointing there first.".format(
                Path(args.input_dir).expanduser()))
    today = eval_date or plcal.today_warsaw()
    breaker = heat = None
    journal = {"assessed": False}
    if not args.no_journal:
        state_dir = Path(args.state_dir).expanduser() if args.state_dir else journal_io.default_state_dir()
        theses = journal_io.load_theses(state_dir)
        journal = {"assessed": bool(theses), "state_dir": str(state_dir), "theses": len(theses)}
        if theses:
            closed = journal_io.closed_results(theses, include_paper=args.include_paper)
            positions = journal_io.open_positions(theses, include_paper=args.include_paper)
            breaker = ee.circuit_breaker(closed, args.account_size, today, args.pause_days)
            prices = {} if args.no_prices else last_closes(args.cache_dir, eval_date)
            heat = ee.portfolio_heat(positions, prices, args.account_size)
            journal.update({"closed_trades": len(closed), "open_positions": len(positions)})
    result = ee.decide(inputs, breaker, heat, args.base_risk_pct)
    as_ofs = sorted(v["as_of"] for v in inputs.values() if v)
    as_of = as_ofs[-1]
    notes = []
    if len(as_ofs) > 1 and sessions_between(as_ofs[0], dt.date.fromisoformat(as_ofs[-1])) > ee.STALE_SESSIONS:
        notes.append("Inputs disagree in date: {} .. {}".format(as_ofs[0], as_ofs[-1]))
    sources = ["{}: {}".format(SKILL_OF[k], info[k]["file"]) for k in ("regime", "breadth", "sector") if info[k].get("used")]
    if journal.get("assessed"):
        sources.append("gpw-trade-journal: {}".format(journal["state_dir"]))
    prov = provenance.build(as_of=dt.date.fromisoformat(as_of), sources=sources,
                            sessions_used=sum(1 for v in inputs.values() if v), notes=notes,
                            reference_date=eval_date)
    if eval_date:
        prov["freshness"]["is_stale"] = any(v and v["stale"] for v in inputs.values())
        prov["notes"].append("Evaluated as of {} (--as-of).".format(eval_date.isoformat()))
    out = {"skill": "gpw-exposure-coach", "version": VERSION, "lang": args.lang, "as_of": as_of}
    out.update(result)
    out.update({
        "inputs": info, "journal": journal, "circuit_breaker": breaker, "heat": heat,
        "account_size": args.account_size, "base_risk_pct": args.base_risk_pct,
        "config": {"weights": ee.WEIGHTS, "state_score": ee.STATE_SCORE, "regime_cap": ee.REGIME_CAP,
                   "top_risk_caps": ee.TOP_RISK_CAPS, "posture_bands": ee.POSTURE_BANDS,
                   "ceiling_points": ee.CEILING_POINTS, "risk_mult": ee.POSTURE_RISK_MULT,
                   "new_positions": ee.POSTURE_NEW_POSITIONS, "heat_cap": ee.HEAT_CAP,
                   "streak": [ee.STREAK_REDUCE, ee.STREAK_PAUSE], "drawdown_pct": [ee.DD_REDUCE_PCT, ee.DD_PAUSE_PCT],
                   "drawdown_r": [ee.DD_REDUCE_R, ee.DD_PAUSE_R], "pause_days": args.pause_days,
                   "stale_sessions": ee.STALE_SESSIONS},
        "data_provenance": prov,
    })
    return out


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

ZONE_LABEL = {
    "strong": {"pl": "silna", "en": "strong"}, "healthy": {"pl": "zdrowa", "en": "healthy"},
    "neutral": {"pl": "neutralna", "en": "neutral"}, "weakening": {"pl": "słabnąca", "en": "weakening"},
    "critical": {"pl": "krytyczna", "en": "critical"}, "strong_bull": {"pl": "silna hossa", "en": "strong bull"},
    "bull": {"pl": "hossa", "en": "bull"}, "cautious": {"pl": "ostrożność", "en": "cautious"},
    "bear": {"pl": "bessa", "en": "bear"},
}
WARNING_LABEL = {
    "late_cycle": {"pl": "późny cykl", "en": "late cycle"},
    "high_spread": {"pl": "duży rozrzut sektorów", "en": "wide sector spread"},
    "divergence": {"pl": "rozbieżność w grupie sektorów", "en": "intra-group divergence"},
}


def _label(table, key, lang):
    return table.get(key, {}).get(lang, key) if key else "—"


REGIME_STATE_LABEL = {
    "confirmed_uptrend": {"pl": "potwierdzony trend wzrostowy", "en": "confirmed uptrend"},
    "uptrend_under_pressure": {"pl": "trend wzrostowy pod presją", "en": "uptrend under pressure"},
    "rally_attempt": {"pl": "próba odbicia", "en": "rally attempt"},
    "correction": {"pl": "korekta", "en": "correction"},
    "bear": {"pl": "bessa", "en": "bear market"},
}


def render_markdown(r: Dict, lang: str) -> str:
    t = T(STRINGS, lang)
    lim, prov = r["limits"], r["data_provenance"]
    acct = r.get("account_size")
    L = ["# " + t("title"), "", "**{}:** {} · **{}:** {}".format(t("as_of"), r["as_of"], t("generated"), prov["retrieved_at_utc"]), ""]
    for name in ("regime", "breadth", "sector"):
        i = r["inputs"][name]
        if i.get("used") and i.get("stale"):
            L.append(t("stale_input", name=t("in_" + name), n=i["sessions_behind"]))
        elif not i.get("used"):
            L.append(t("missing_input", name=t("in_" + name)))
    if L[-1] != "":
        L.append("")

    # ---- one-page summary
    p = r["posture"]
    L += ["## " + t("summary"), "", t("posture_line", p=t("p_" + p), expl=t("e_" + p)), "",
          "| {} | {} | {} |".format(t("col_limit"), t("col_value"), t("col_meaning")), "|---|---|---|",
          "| {} | **{}** | {} |".format(t("l_ceiling"), pct(lim["exposure_ceiling_pct"], 0, lang), t("m_ceiling"))]
    risk_txt = pct(lim["max_risk_per_trade_pct"], 2, lang)
    extra = ""
    if acct:
        extra = t("m_risk_pln", v=pln(acct * lim["max_risk_per_trade_pct"] / 100, 0, lang), a=pln(acct, 0, lang))
    L.append("| {} | **{}** | {}{} |".format(t("l_risk"), risk_txt, t("m_risk"), extra))
    L.append("| {} | **{}** | {} |".format(t("l_new"), lim["max_new_positions_per_week"], t("m_new")))
    cb_meaning = t("m_cb") if r["journal"].get("assessed") else t("m_cb_na")
    L += ["| {} | **{}** | {} |".format(t("l_cb"), t("cb_" + lim["circuit_breaker"]), cb_meaning), ""]

    L += ["**{}:**".format(t("why")), ""]
    reg, br, se = (r["inputs"][k] for k in ("regime", "breadth", "sector"))
    if reg.get("used"):
        L.append("- " + t("r_regime", idx=reg.get("index", "WIG"), state=REGIME_STATE_LABEL[reg["state"]][lang],
                          risk=num(reg["top_risk_score"], 0, lang), dd=reg.get("distribution_days"),
                          score=num(reg["score"], 0, lang)))
    if br.get("used"):
        L.append("- " + t("r_breadth", score=num(br["score"], 0, lang), zone=_label(ZONE_LABEL, br.get("zone"), lang)))
    if se.get("used"):
        w = t("r_sector_warn", w=", ".join(_label(WARNING_LABEL, x, lang) for x in se["warnings"])) if se.get("warnings") else ""
        L.append("- " + t("r_sector", score=num(se["score"], 0, lang), zone=_label(ZONE_LABEL, se.get("zone"), lang), warn=w))
    comb = r["reasons"][0]["params"]
    L.append("- " + t("r_combined", score=num(comb["score"], 1, lang), p=t("p_" + comb["posture"]), c=pct(comb["ceiling"], 0, lang)))
    cb = r.get("circuit_breaker")
    if cb:
        extra = t("r_journal_pct", v=pln(cb["drawdown_pln"], 0, lang), x=pct(cb["drawdown_pct"], 1, lang)) if cb["drawdown_pct"] is not None else ""
        L.append("- " + t("r_journal", n=cb["closed_trades"], s=cb["losing_streak"], r=num(cb["drawdown_r"], 1, lang), p=extra))
    else:
        L.append("- " + t("r_journal_na"))
    h = r.get("heat")
    if h and h["positions"]:
        extra = t("r_heat_pct", h=pct(h["heat_pct"], 1, lang), e=pct(h["exposure_pct"], 0, lang)) if h["heat_pct"] is not None else ""
        L.append("- " + t("r_heat", v=pln(h["open_risk_pln"], 0, lang), p=extra, cap=pct(r["heat_cap_pct"], 0, lang)))
        if h["no_stop"]:
            L.append("- " + t("r_no_stop", t=", ".join(h["no_stop"])))
    L.append("")
    L += ["**{}:**".format(t("caps")), ""]
    if r["caps_applied"]:
        for c in r["caps_applied"]:
            prm = dict(c["params"])
            if "state" in prm:
                prm["state"] = REGIME_STATE_LABEL[prm["state"]][lang]
            if "posture" in prm:
                prm["p"] = t("p_" + prm.pop("posture"))
            for k in ("cap", "ceiling", "exposure", "heat"):
                if k in prm:
                    prm[k] = pct(prm[k], 0 if k != "heat" else 1, lang)
            if "risk" in prm:
                prm["risk"] = num(prm["risk"], 0, lang)
            if "limit" in prm:
                prm["lim"] = num(prm.pop("limit"), 0, lang)
            L.append("- " + t("c_" + c["code"], **prm))
    else:
        L.append("- " + t("none"))
    L.append("")
    conf = r["confidence"]
    L += ["**{}:** {}".format(t("confidence"), t("conf_txt", lvl=t("conf_" + conf["level"]), s=num(conf["score"], 2, lang))), ""]
    L += ["### " + t("plain"), "", t("plain_txt"), ""]

    # ---- details
    L += ["## " + t("details"), "",
          "| {} | {} | {} | {} | {} | {} |".format(t("col_input"), t("col_file"), t("col_asof"), t("col_behind"), t("col_score"), t("col_weight")),
          "|---|---|---|---|---|---|"]
    for name in ("regime", "breadth", "sector"):
        i = r["inputs"][name]
        if i.get("used"):
            L.append("| {} | {} | {} | {} | {} | {} |".format(t("in_" + name), Path(i["file"]).name, i["as_of"], i["sessions_behind"],
                                                             num(i["score"], 1, lang), pct(r["effective_weights"][name] * 100, 0, lang)))
        else:
            L.append("| {} | {} | — | — | — | — |".format(t("in_" + name), t("missing") + (" ({})".format(i["reason"]) if i.get("reason") != "missing" else "")))
    L.append("")
    if cb:
        L += ["## " + t("cb_title"), "", t("cb_rules", d=cb["pause_days"]), ""]
        for rr in cb["reasons"]:
            L.append("- " + t("cbr_" + rr["code"], **rr["params"]))
        if cb["reasons"]:
            L.append("")
        if cb["last_results"]:
            L += ["| {} | {} | {} | {} |".format(t("col_ticker"), t("col_date"), t("col_pnl"), t("col_r")), "|---|---|---|---|"]
            for x in reversed(cb["last_results"]):
                L.append("| {} | {} | {} | {} |".format(x["ticker"], x["closed_at"], pln(x["net_pnl"], 2, lang),
                                                       num(x["r_multiple"], 2, lang) if x["r_multiple"] is not None else "—"))
            L.append("")
    if h and h["positions"]:
        L += ["## " + t("heat_title"), "",
              "| {} | {} | {} | {} | {} | {} |".format(t("col_ticker"), t("col_shares"), t("col_price"), t("col_stop"), t("col_val"), t("col_risk")),
              "|---|---|---|---|---|---|"]
        for x in h["positions"]:
            L.append("| {} | {} | {} ({}) | {} | {} | {} |".format(
                x["ticker"], x["shares"], num(x["price"], 2, lang), t("src_" + x["price_source"]),
                num(x["stop"], 2, lang) if x["stop"] is not None else "—", pln(x["value_pln"], 0, lang),
                pln(x["risk_pln"], 0, lang) if x["risk_pln"] is not None else "?"))
        L.append("")
    L += ["## " + t("method"), "", t("method_txt", b=num(r["base_risk_pct"], 2, lang)), ""]
    L += ["## " + t("sources"), ""] + ["- " + s for s in prov["sources"]]
    L += ["- O'Neil W., *How to Make Money in Stocks* — market direction (\"M\")",
          "- Elder A., *The New Trading for a Living* (2014) — the 2% and 6% rules",
          "- Van Tharp K., *Trade Your Way to Financial Freedom* — position sizing, portfolio heat",
          "", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except ee.InputError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    md = render_markdown(result, args.lang)
    if args.no_files:
        print(md)
        return 0
    out = Path(args.output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    jp, mp = out / "gpw_exposure_coach_{}.json".format(stamp), out / "gpw_exposure_coach_{}.md".format(stamp)
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(md, encoding="utf-8")
    lim = result["limits"]
    print("Posture: {} | ceiling {}% | risk/trade {}% | new/week {} | breaker {} | confidence {} | as of {}".format(
        result["posture"], lim["exposure_ceiling_pct"], lim["max_risk_per_trade_pct"], lim["max_new_positions_per_week"],
        lim["circuit_breaker"], result["confidence"]["level"], result["as_of"]))
    print("JSON: {}\nMarkdown: {}".format(jp, mp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
