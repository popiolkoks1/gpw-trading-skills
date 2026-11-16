#!/usr/bin/env python3
"""GPW Pre-Trade Gate - CLI.

Fail-closed checklist run BEFORE a long entry on GPW: stop, risk incl. fees,
position size, liquidity, portfolio heat, sector concentration, written thesis
with kill criteria, reward/risk, circuit breaker, market regime, exposure
limits and data freshness. Verdict GO / GO WITH CONDITIONS / NO-GO.

    python3 scripts/pre_trade_gate.py --ticker PKOBP --entry 120 --stop 113 --shares 30 --account-size 50000
    python3 scripts/pre_trade_gate.py --sizer-json gpw_position_size_2026-11-15_101500.json --thesis-id th_pkobp_...

Never places orders.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import brokers, constituents, gpw_archive, journal_io, panel, provenance  # noqa: E402
from gpwlib.constituents import SECTOR_INDICES  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct, pln  # noqa: E402
from gpwlib.indicators import median  # noqa: E402

import pretrade_gate_engine as ge  # noqa: E402

VERSION = "1.0.0"
PREFIX = "gpw_pre_trade_gate"
MIN_OVERRIDE_CHARS = 15  # a real sentence, not "ok" / "yes"

STRINGS = {
    "title": {"pl": "Bramka przed transakcją (pre-trade gate)", "en": "Pre-Trade Gate"},
    "verdict": {"pl": "Werdykt", "en": "Verdict"},
    "v_GO": {"pl": "GO – wszystkie warunki spełnione", "en": "GO – all conditions met"},
    "v_GO WITH CONDITIONS": {"pl": "GO WITH CONDITIONS – możliwe, ale z zastrzeżeniami", "en": "GO WITH CONDITIONS – possible, with caveats"},
    "v_NO-GO": {"pl": "NO-GO – co najmniej jeden warunek blokujący", "en": "NO-GO – at least one blocking condition"},
    "never": {"pl": "Bramka niczego nie kupuje i nie składa zleceń – decyzja i zlecenie należą do Ciebie.",
              "en": "The gate does not buy anything or place orders – the decision and the order are yours."},
    "stale": {"pl": "⚠️ UWAGA: dane cenowe są nieaktualne o {n} sesji.", "en": "⚠️ WARNING: price data is {n} session(s) old."},
    "no_market": {"pl": "⚠️ UWAGA: brak danych rynkowych – płynność i świeżość niezweryfikowane.",
                  "en": "⚠️ WARNING: no market data – liquidity and freshness unverified."},
    "blockers": {"pl": "Blokery (FAIL)", "en": "Blockers (FAIL)"},
    "conditions": {"pl": "Warunki (WARN)", "en": "Conditions (WARN)"},
    "trade": {"pl": "Transakcja", "en": "Trade"},
    "k_ticker": {"pl": "Walor", "en": "Ticker"}, "k_entry": {"pl": "Wejście", "en": "Entry"},
    "k_stop": {"pl": "Stop", "en": "Stop"}, "k_target": {"pl": "Cel", "en": "Target"},
    "k_shares": {"pl": "Liczba akcji", "en": "Shares"}, "k_value": {"pl": "Wartość pozycji", "en": "Position value"},
    "k_account": {"pl": "Wielkość rachunku", "en": "Account size"}, "k_acct_type": {"pl": "Rachunek", "en": "Account type"},
    "k_fees": {"pl": "Prowizje (kupno + sprzedaż na stopie)", "en": "Fees (buy + sell at stop)"},
    "k_risk": {"pl": "Ryzyko z prowizjami", "en": "Risk incl. fees"},
    "k_rr": {"pl": "Zysk/ryzyko (netto)", "en": "Reward/risk (net)"},
    "k_broker": {"pl": "Taryfa", "en": "Tariff"},
    "k_source": {"pl": "Źródło parametrów", "en": "Parameters from"},
    "checks": {"pl": "Lista kontrolna", "en": "Checklist"},
    "c_check": {"pl": "Kontrola", "en": "Check"}, "c_status": {"pl": "Status", "en": "Status"}, "c_reason": {"pl": "Uzasadnienie", "en": "Reason"},
    "n_stop": {"pl": "Stop loss", "en": "Stop loss"},
    "n_risk": {"pl": "Ryzyko na transakcję", "en": "Risk per trade"},
    "n_position": {"pl": "Wielkość pozycji", "en": "Position size"},
    "n_liquidity": {"pl": "Płynność", "en": "Liquidity"},
    "n_heat": {"pl": "Łączne ryzyko (heat)", "en": "Portfolio heat"},
    "n_sector": {"pl": "Koncentracja sektorowa", "en": "Sector concentration"},
    "n_thesis": {"pl": "Pisemna teza", "en": "Written thesis"},
    "n_reward_risk": {"pl": "Zysk do ryzyka", "en": "Reward/risk"},
    "n_circuit_breaker": {"pl": "Bezpiecznik (seria strat)", "en": "Circuit breaker"},
    "n_regime": {"pl": "Reżim rynku", "en": "Market regime"},
    "n_exposure_ceiling": {"pl": "Limit ekspozycji", "en": "Exposure ceiling"},
    "n_new_positions": {"pl": "Nowe pozycje w tygodniu", "en": "New positions this week"},
    "n_data_freshness": {"pl": "Świeżość danych", "en": "Data freshness"},
    # reasons
    "stop_missing": {"pl": "Brak stopu – bez niego ryzyko jest nieokreślone.", "en": "No stop – risk is undefined without it."},
    "stop_not_below": {"pl": "Stop {stop} nie jest poniżej wejścia {entry} (pozycja długa).", "en": "Stop {stop} is not below entry {entry} (long position)."},
    "stop_ok": {"pl": "Stop {stop}, {dist} poniżej wejścia.", "en": "Stop {stop}, {dist} below entry."},
    "risk_undefined": {"pl": "Ryzyka nie da się policzyć bez poprawnego stopu.", "en": "Risk cannot be computed without a valid stop."},
    "risk_over": {"pl": "Ryzyko {risk} = {pct} rachunku > limit {limit} ({src}). Zmniejsz liczbę akcji (gpw-position-sizer).",
                  "en": "Risk {risk} = {pct} of the account > limit {limit} ({src}). Reduce the share count (gpw-position-sizer)."},
    "risk_ok": {"pl": "Ryzyko {risk} = {pct} rachunku ≤ limit {limit} ({src}).", "en": "Risk {risk} = {pct} of the account ≤ limit {limit} ({src})."},
    "src_default": {"pl": "domyślny", "en": "default"}, "src_exposure": {"pl": "z gpw-exposure-coach", "en": "from gpw-exposure-coach"},
    "src_user": {"pl": "podany", "en": "user"},
    "position_cash": {"pl": "Pozycja {value} z prowizją przekracza wielkość rachunku.", "en": "Position {value} plus fee exceeds the account size."},
    "position_over": {"pl": "Pozycja {value} = {pct} rachunku > {limit}.", "en": "Position {value} = {pct} of the account > {limit}."},
    "position_ok": {"pl": "Pozycja {value} = {pct} rachunku ≤ {limit}.", "en": "Position {value} = {pct} of the account ≤ {limit}."},
    "liq_unknown": {"pl": "Brak danych o obrotach – płynności nie zweryfikowano.", "en": "No turnover data – liquidity not verified."},
    "liq_zero": {"pl": "Mediana obrotu = 0 – walor praktycznie nie jest handlowany.", "en": "Median turnover = 0 – the instrument barely trades."},
    "liq_over": {"pl": "Zlecenie = {pct} mediany dziennego obrotu ({median}, {window} sesji) > {limit}.", "en": "Order = {pct} of median daily turnover ({median}, {window} sessions) > {limit}."},
    "liq_ok": {"pl": "Zlecenie = {pct} mediany dziennego obrotu ({median}, {window} sesji) ≤ {limit}.", "en": "Order = {pct} of median daily turnover ({median}, {window} sessions) ≤ {limit}."},
    "heat_over": {"pl": "Ryzyko otwartych pozycji {existing} + ta transakcja {new} = {pct} rachunku > {limit}.", "en": "Open risk {existing} + this trade {new} = {pct} of the account > {limit}."},
    "heat_ok": {"pl": "Ryzyko otwartych pozycji {existing} + ta transakcja {new} = {pct} rachunku ≤ {limit}.", "en": "Open risk {existing} + this trade {new} = {pct} of the account ≤ {limit}."},
    "heat_no_stop": {"pl": "{pct} rachunku, ale pozycje bez stopu ({ns}) mają nieokreślone ryzyko – realny heat jest wyższy.",
                     "en": "{pct} of the account, but positions without a stop ({ns}) have undefined risk – real heat is higher."},
    "heat_no_journal": {"pl": "{pct} rachunku – liczone tylko z tej transakcji (brak dziennika, inne pozycje niewidoczne).",
                        "en": "{pct} of the account – this trade only (no journal, other positions invisible)."},
    "sector_unclassified": {"pl": "Walor poza 14 indeksami sektorowymi GPW – koncentracji sektorowej nie da się zmierzyć.",
                            "en": "Not in any of the 14 GPW sector indices – sector concentration cannot be measured."},
    "sector_over": {"pl": "Sektor {sector} po transakcji = {pct} rachunku > {limit}.", "en": "Sector {sector} after the trade = {pct} of the account > {limit}."},
    "sector_ok": {"pl": "Sektor {sector} po transakcji = {pct} rachunku ≤ {limit}.", "en": "Sector {sector} after the trade = {pct} of the account ≤ {limit}."},
    "thesis_missing": {"pl": "Brak zapisanej tezy z kryteriami unieważnienia (kill criteria). Bramka wymaga pisemnej tezy – zapisz ją w gpw-trade-journal (polecenie niżej).",
                       "en": "No written thesis with kill criteria. The gate requires a written thesis – record it in gpw-trade-journal (command below)."},
    "thesis_not_found": {"pl": "Nie znaleziono tezy {id} w dzienniku.", "en": "Thesis {id} not found in the journal."},
    "thesis_ambiguous": {"pl": "Kilka otwartych tez dla tego waloru ({ids}) – wskaż jedną przez --thesis-id.", "en": "Several open theses for this ticker ({ids}) – pick one with --thesis-id."},
    "thesis_ticker": {"pl": "Teza {id} dotyczy {thesis_ticker}, nie tego waloru.", "en": "Thesis {id} is about {thesis_ticker}, not this ticker."},
    "thesis_terminal": {"pl": "Teza {id} ma status {status} – nowa transakcja wymaga nowej tezy.", "en": "Thesis {id} is {status} – a new trade needs a new thesis."},
    "thesis_no_kill": {"pl": "Teza {id} nie ma kryteriów unieważnienia.", "en": "Thesis {id} has no kill criteria."},
    "thesis_stop_differs": {"pl": "Teza {id} ({kills} kryt. unieważnienia), ale stop {stop} różni się od stopu z planu {plan_stop} – zaktualizuj plan albo stop.",
                            "en": "Thesis {id} ({kills} kill criteria), but stop {stop} differs from the plan stop {plan_stop} – update the plan or the stop."},
    "thesis_ok": {"pl": "Teza {id} ({status}, {kills} kryt. unieważnienia{auto}).", "en": "Thesis {id} ({status}, {kills} kill criteria{auto})."},
    "auto": {"pl": ", znaleziona po walorze", "en": ", found by ticker"},
    "rr_no_target": {"pl": "Brak celu – zysk/ryzyko nie sprawdzony (dopuszczalne przy wyjściu trailing stopem).", "en": "No target – reward/risk not checked (acceptable for a trailing-stop exit)."},
    "rr_undefined": {"pl": "Zysku/ryzyka nie da się policzyć.", "en": "Reward/risk cannot be computed."},
    "rr_fail": {"pl": "{rr}R przy celu {target}{src} – potencjalny zysk mniejszy niż ryzyko (< {limit}R).", "en": "{rr}R at target {target}{src} – potential reward below the risk (< {limit}R)."},
    "rr_warn": {"pl": "{rr}R przy celu {target}{src} < {limit}R – słaba asymetria.", "en": "{rr}R at target {target}{src} < {limit}R – weak asymmetry."},
    "rr_ok": {"pl": "{rr}R przy celu {target}{src} ≥ {limit}R.", "en": "{rr}R at target {target}{src} ≥ {limit}R."},
    "from_thesis": {"pl": " (z tezy)", "en": " (from the thesis)"},
    "cb_pause_ex": {"pl": "gpw-exposure-coach: bezpiecznik = PAUZA – nie otwieraj nowych pozycji.", "en": "gpw-exposure-coach: circuit breaker = PAUSE – no new positions."},
    "cb_reduce_ex": {"pl": "gpw-exposure-coach: bezpiecznik = OGRANICZ – zmniejsz pozycję o połowę.", "en": "gpw-exposure-coach: circuit breaker = REDUCE – halve the size."},
    "cb_ok_ex": {"pl": "gpw-exposure-coach: bezpiecznik OK.", "en": "gpw-exposure-coach: circuit breaker OK."},
    "cb_unknown": {"pl": "Nieznana wartość bezpiecznika: {value}.", "en": "Unknown circuit-breaker value: {value}."},
    "cb_no_history": {"pl": "Brak zamkniętych transakcji w dzienniku.", "en": "No closed trades in the journal."},
    "cb_pause": {"pl": "{streak} strat z rzędu, obsunięcie zrealizowane {dd} – PAUZA (próg: 5 strat lub 10%). Najpierw przegląd (gpw-trade-review).",
                 "en": "{streak} losses in a row, realised drawdown {dd} – PAUSE (threshold: 5 losses or 10%). Review first (gpw-trade-review)."},
    "cb_reduce": {"pl": "{streak} straty z rzędu (obsunięcie {dd}) – zmniejsz pozycję o połowę.", "en": "{streak} losses in a row (drawdown {dd}) – halve the size."},
    "cb_ok": {"pl": "Seria strat {streak}, obsunięcie zrealizowane {dd} ({n} zamkniętych).", "en": "Loss streak {streak}, realised drawdown {dd} ({n} closed)."},
    "regime_missing": {"pl": "Brak pliku gpw-market-regime – reżim nieznany (mniejsza pewność).", "en": "No gpw-market-regime file – regime unknown (lower confidence)."},
    "regime_unknown": {"pl": "Nierozpoznany stan rynku: {state}.", "en": "Unrecognised market state: {state}."},
    "regime_bear_top": {"pl": "Bessa i ryzyko szczytu {top} ≥ {limit} – nowe pozycje długie zablokowane (możliwy --override z pisemnym uzasadnieniem).",
                        "en": "Bear market and top risk {top} ≥ {limit} – new longs blocked (--override with a written reason possible)."},
    "regime_overridden": {"pl": "Bessa, ryzyko szczytu {top} – ŚWIADOMIE POMINIĘTE: „{override}”.", "en": "Bear market, top risk {top} – CONSCIOUSLY OVERRIDDEN: \"{override}\"."},
    "regime_correction": {"pl": "Rynek w korekcie – nowe pozycje długie mają gorsze szanse; mniejsza pozycja, ciaśniejsze kryteria.",
                          "en": "Market in correction – new longs have worse odds; smaller size, stricter criteria."},
    "regime_bear": {"pl": "Bessa (ryzyko szczytu {top}) – nowe pozycje długie wbrew trendowi rynku.", "en": "Bear market (top risk {top}) – new longs fight the market trend."},
    "regime_top_risk": {"pl": "Stan {state}, ale ryzyko szczytu {top} ≥ {limit}.", "en": "State {state}, but top risk {top} ≥ {limit}."},
    "regime_ok": {"pl": "Stan rynku: {state} (ryzyko szczytu {top}).", "en": "Market state: {state} (top risk {top})."},
    "ceiling_missing": {"pl": "Brak pliku gpw-exposure-coach.", "en": "No gpw-exposure-coach file."},
    "ceiling_over": {"pl": "Zaangażowanie po transakcji {pct} > sufit {limit} (postawa: {posture}).", "en": "Exposure after the trade {pct} > ceiling {limit} (posture: {posture})."},
    "ceiling_ok": {"pl": "Zaangażowanie po transakcji {pct} ≤ sufit {limit} (postawa: {posture}).", "en": "Exposure after the trade {pct} ≤ ceiling {limit} (posture: {posture})."},
    "newpos_missing": {"pl": "Brak limitu nowych pozycji (brak gpw-exposure-coach).", "en": "No new-position limit (no gpw-exposure-coach)."},
    "newpos_over": {"pl": "{n} nowych pozycji w 7 dni > limit {limit}.", "en": "{n} new positions in 7 days > limit {limit}."},
    "newpos_ok": {"pl": "{n} nowych pozycji w 7 dni ≤ limit {limit}.", "en": "{n} new positions in 7 days ≤ limit {limit}."},
    "fresh_ok": {"pl": "Dane cenowe aktualne (sesja {date}).", "en": "Price data current (session {date})."},
    "i_no_market": {"pl": "brak danych cenowych", "en": "no price data"},
    "i_prices_stale": {"pl": "ceny sprzed {n} sesji ({date})", "en": "prices {n} sessions old ({date})"},
    "i_entry_far": {"pl": "wejście {dev}% od ostatniego kursu {last} (literówka?)", "en": "entry {dev}% from the last close {last} (typo?)"},
    "i_input_stale": {"pl": "plik {skill} sprzed {n} sesji ({date})", "en": "{skill} file {n} sessions old ({date})"},
    "inputs": {"pl": "Wejścia z innych skilli", "en": "Inputs from other skills"},
    "inp_line": {"pl": "{skill}: {path} (dane na {as_of}, {n} sesji temu)", "en": "{skill}: {path} (as of {as_of}, {n} sessions ago)"},
    "inp_none": {"pl": "brak – sprawdzane z mniejszą pewnością", "en": "none – checked with lower confidence"},
    "override_rec": {"pl": "Zapisane uzasadnienie pominięcia (--override)", "en": "Recorded override reason (--override)"},
    "override_unused": {"pl": " (nie było potrzebne – żaden FAIL reżimu)", "en": " (not needed – no regime FAIL)"},
    "next": {"pl": "Co dalej", "en": "Next steps"},
    "next_journal": {"pl": "Zapisz tezę w dzienniku (uzupełnij treść):", "en": "Record the thesis in the journal (fill in the text):"},
    "next_sizer": {"pl": "Przelicz wielkość pozycji: gpw-position-sizer.", "en": "Recalculate the size: gpw-position-sizer."},
    "next_go": {"pl": "Po wejściu zapisz transakcję: `journal.py fill <id> --side buy ...`.", "en": "After entering, record the fill: `journal.py fill <id> --side buy ...`."},
    "journal_note": {"pl": "Heat, sektor i ekspozycja liczone z aktywnych pozycji w dzienniku ({d}); pozycji spoza dziennika bramka nie widzi.",
                     "en": "Heat, sector and exposure use ACTIVE journal positions ({d}); positions outside the journal are invisible to the gate."},
    "sources": {"pl": "Źródła", "en": "Sources"},
}

ORDER_HINT = ("stop", "risk", "position", "liquidity", "heat", "sector", "thesis", "reward_risk",
              "circuit_breaker", "regime", "exposure_ceiling", "new_positions", "data_freshness")


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

def read_sizer_json(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ge.GateError("cannot read sizer JSON {}: {}".format(path, exc))
    if d.get("skill") != "gpw-position-sizer":
        raise ge.GateError("{} is not a gpw-position-sizer report".format(path))
    inp = d.get("inputs") or {}
    b = d.get("broker") or {}
    return {"ticker": inp.get("ticker"), "entry": d.get("entry"), "stop": d.get("stop"), "shares": d.get("shares"),
            "target": (d.get("reward") or {}).get("target"), "account_size": inp.get("account_size"),
            "account_type": d.get("account_type"), "fee_pct": b.get("pct"), "fee_min": b.get("min_fee"),
            "broker": b.get("key")}


def _as_of_of(d: dict):
    a = d.get("as_of") or (d.get("regime") or {}).get("as_of") or (d.get("data_provenance") or {}).get("as_of")
    try:
        return dt.date.fromisoformat(str(a)[:10]) if a else None
    except ValueError:
        return None


def find_input(explicit, input_dir: Path, skill: str):
    """Explicit path or newest JSON in input_dir whose "skill" matches. Returns (data, path) or (None, None)."""
    if explicit:
        p = Path(explicit).expanduser()
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ge.GateError("cannot read {}: {}".format(p, exc))
        if d.get("skill") != skill:
            raise ge.GateError("{} is not a {} report (skill={!r})".format(p, skill, d.get("skill")))
        return d, p
    if not input_dir.is_dir():
        return None, None
    for p in sorted(input_dir.glob("gpw_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("skill") == skill:
            return d, p
    return None, None


def load_market(ticker, journal_tickers, end, cache_dir, offline, window):
    """Last close / median turnover for the ticker (shares, then ETFs) and prices for journal tickers."""
    n = window + 5
    shares = gpw_archive.load_sessions(n, end=end, cache_dir=cache_dir, offline=offline, progress=False)
    if not shares:
        return None, {}, None, []
    pnl = panel.build_panel(shares)
    up = {k.upper(): k for k in pnl}
    name, kind, sessions = up.get(ticker.upper()), "share", shares
    if name is None:
        latest = gpw_archive.load_sessions(1, end=end, type_id=gpw_archive.TYPE_ETF, cache_dir=cache_dir,
                                           offline=offline, progress=False)
        etf_names = [r["name"] for r in latest[-1]["rows"]] if latest else []
        if ticker.upper() in {x.upper() for x in etf_names}:
            etf = gpw_archive.load_sessions(n, end=end, type_id=gpw_archive.TYPE_ETF, cache_dir=cache_dir,
                                            offline=offline, progress=False)
            epnl = panel.build_panel(etf)
            name = {k.upper(): k for k in epnl}.get(ticker.upper())
            if name:
                pnl, kind, sessions = dict(pnl, **epnl), "etf", etf
        if name is None:
            import difflib
            pool = sorted(set(up) | {x.upper() for x in etf_names})
            t = ticker.upper()
            cm = [x for x in pool if t in x][:3] + difflib.get_close_matches(t, pool, n=5, cutoff=0.6)
            cm = list(dict.fromkeys(cm))[:6]
            raise ge.GateError("'{}' not found in the GPW archive (shares{}); close matches: {}. Use the GPW short name, "
                               "e.g. PKOBP, PKNORLEN, CDPROJEKT.".format(
                                   ticker, " and ETFs" if etf_names else "; ETF list unavailable offline",
                                   ", ".join(cm) or "none"))
    s = pnl[name]
    turn = [v * 1000.0 for v in s.turnover_k[-window:]]
    last_date = dt.date.fromisoformat(s.dates[-1])
    fr = provenance.freshness(dt.date.fromisoformat(sessions[-1]["date"]))
    market = {"ticker": name, "kind": kind, "last_close": s.close[-1], "last_date": s.dates[-1],
              "median_turnover_pln": median(turn), "is_stale": fr["is_stale"], "sessions_behind": fr["sessions_behind"],
              "archive_date": sessions[-1]["date"]}
    prices = {k: pnl[k].close[-1] for k in journal_tickers if k in pnl}
    return market, prices, last_date, sessions


def run(args) -> dict:
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else None
    end = dt.date.fromisoformat(args.as_of) if args.as_of else None
    sizer = read_sizer_json(args.sizer_json) if args.sizer_json else {}

    def pick(name):
        v = getattr(args, name, None)
        return v if v is not None else sizer.get(name)

    broker_key = args.broker or sizer.get("broker") or "mbank"
    b = brokers.resolve(broker_key, args.commission_pct, args.commission_min)
    fee_pct, fee_min = b["pct"], b["min_fee"]
    if args.commission_pct is None and args.commission_min is None and args.broker is None and sizer.get("fee_pct") is not None:
        fee_pct, fee_min = sizer["fee_pct"], sizer["fee_min"] or 0.0
    trade = {"ticker": (pick("ticker") or "").strip().upper(), "entry": pick("entry"), "stop": pick("stop"),
             "shares": pick("shares"), "target": pick("target"), "account_size": pick("account_size"),
             "account_type": pick("account_type") or "regular", "fee_pct": fee_pct, "fee_min": fee_min,
             "thesis_id": args.thesis_id}
    if not trade["ticker"]:
        raise ge.GateError("--ticker is required (or --sizer-json with a ticker)")
    if trade["shares"] is not None:
        trade["shares"] = int(trade["shares"])
    ge.validate_trade(trade)

    state = Path(args.state_dir).expanduser() if args.state_dir else journal_io.default_state_dir()
    journal_ok = (state / "theses").is_dir()
    theses = journal_io.load_theses(state) if journal_ok else []
    opens = journal_io.open_positions(theses)
    closed = journal_io.closed_results(theses)

    market, prices, _, sessions = load_market(trade["ticker"], [p["ticker"] for p in opens], end, cache_dir,
                                              args.offline, ge.DEFAULTS["turnover_window"])
    if market:
        trade["ticker"] = market["ticker"]
    sector_map = constituents.sector_membership(cache_dir, offline=args.offline)
    sector = sector_map.get(trade["ticker"]) if not market or market["kind"] == "share" else None

    input_dir = Path(args.input_dir).expanduser()
    regime, rp = find_input(args.regime_json, input_dir, "gpw-market-regime")
    exposure, ep = find_input(args.exposure_json, input_dir, "gpw-exposure-coach")
    inputs = []
    for d, p, sk in ((regime, rp, "gpw-market-regime"), (exposure, ep, "gpw-exposure-coach")):
        if d is None:
            continue
        a = _as_of_of(d)
        inputs.append({"skill": sk, "path": str(p), "as_of": a.isoformat() if a else None,
                       "sessions_behind": provenance.sessions_behind(a) if a else None})
    ctx = {"market": market, "sector": sector, "sector_map": sector_map, "theses": theses, "journal_ok": journal_ok,
           "open_positions": opens, "closed_results": closed, "prices": prices, "regime": regime,
           "exposure": exposure, "inputs": inputs, "override": args.override, "user_max_risk_pct": args.max_risk_pct,
           "today": end or dt.date.today()}
    cfg = {}
    for k in ("max_position_pct", "max_adv_pct", "max_heat_pct", "max_sector_pct", "min_rr"):
        if getattr(args, k) is not None:
            cfg[k] = getattr(args, k)
    res = ge.evaluate(trade, ctx, cfg)
    srcs = ["GPW — Archiwum notowań: https://www.gpw.pl/archiwum-notowan",
            "GPW Benchmark — portfele indeksów sektorowych: https://gpwbenchmark.pl"]
    if market:
        prov = provenance.build(as_of=dt.date.fromisoformat(market["archive_date"]), sources=srcs,
                                sessions_used=len(sessions), universe_size=None,
                                notes=["Sector membership = current GPW Benchmark portfolio (not point-in-time)."])
    else:
        prov = {"schema_version": provenance.SCHEMA_VERSION, "as_of": None, "sources": srcs, "sessions_used": 0,
                "freshness": {"as_of": None, "sessions_behind": None, "is_stale": True},
                "notes": ["No market data available (offline cache empty?) - liquidity and freshness unverified."]}
    prov["broker_tariff"] = {"broker": broker_key, "pct": fee_pct, "min_fee": fee_min,
                             "notes": brokers.tariff_notes(args.lang, overridden=b["overridden"])}
    return {"skill": "gpw-pre-trade-gate", "version": VERSION, "lang": args.lang,
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "sizer_json": str(args.sizer_json) if args.sizer_json else None,
            "journal_dir": str(state), "journal_ok": journal_ok, "inputs": inputs, "market": market,
            "sector": sector, **res, "data_provenance": prov}


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

def _fmt_params(c: dict, lang: str, t) -> dict:
    p = dict(c["params"])
    for k in ("risk", "value", "existing", "new", "median"):
        if isinstance(p.get(k), (int, float)):
            p[k] = pln(p[k], 0 if k == "median" else 2, lang)
    for k in ("pct", "dist", "dd"):
        if isinstance(p.get(k), (int, float)):
            p[k] = ("< " + pct(0.01, 2, lang)) if 0 < p[k] < 0.01 else pct(p[k], 2, lang)
    if c["id"] in ("risk", "position", "liquidity", "heat", "sector", "exposure_ceiling") and isinstance(p.get("limit"), (int, float)):
        p["limit"] = pct(p["limit"], 2 if c["id"] == "risk" else 0, lang)
    for k in ("stop", "entry", "target", "plan_stop", "last"):
        if isinstance(p.get(k), (int, float)):
            p[k] = num(p[k], 2, lang)
    if isinstance(p.get("rr"), (int, float)):
        p["rr"] = num(p["rr"], 2, lang)
    if c["id"] == "reward_risk" and isinstance(p.get("limit"), (int, float)):
        p["limit"] = num(p["limit"], 1, lang)
    if "src" in p:
        p["src"] = t("from_thesis") if p["src"] == "thesis" else ("" if c["id"] == "reward_risk" else t("src_" + p["src"]))
    if "sector" in p and p["sector"] in SECTOR_INDICES:
        p["sector"] = SECTOR_INDICES[p["sector"]][2 if lang == "pl" else 1]
    if "ids" in p:
        p["ids"] = ", ".join(p["ids"])
    if "no_stop" in p:
        p["ns"] = ", ".join(p["no_stop"])
    if c["id"] == "thesis":
        p["auto"] = t("auto") if p.get("how") == "auto" else ""
    if p.get("top") is None and c["id"] == "regime":
        p["top"] = "—"
    return p


def reason_text(c: dict, lang: str) -> str:
    t = T(STRINGS, lang)
    if c["reason"] == "fresh_issues":
        parts = []
        for i in c["params"]["issues"]:
            q = dict(i)
            if "last" in q:
                q["last"] = num(q["last"], 2, lang)
            if "dev" in q:
                q["dev"] = num(q["dev"], 1, lang)
            parts.append(t("i_" + i["code"], **{k: v for k, v in q.items() if k != "code"}))
        return "; ".join(parts) + "."
    return t(c["reason"], **_fmt_params(c, lang, t))


def journal_command(r: dict) -> str:
    tr = r["trade"]
    cmd = ('python3 ../gpw-trade-journal/scripts/journal.py add --ticker {t} --thesis "..." '
           '--pillar "..." --kill "..." --entry {e:g} --stop {s}').format(t=tr["ticker"], e=tr["entry"], s="{:g}".format(tr["stop"]) if tr.get("stop") else "...")
    if tr.get("target"):
        cmd += " --target {:g}".format(tr["target"])
    if tr.get("account_type") and tr["account_type"] != "regular":
        cmd += " --account-type {}".format(tr["account_type"])
    if r.get("sizer_json"):
        cmd += " --sizer-json {}".format(r["sizer_json"])
    return cmd + " --status READY"


def render_markdown(r: dict, lang: str) -> str:
    t = T(STRINGS, lang)
    tr, nm, prov = r["trade"], r["numbers"], r["data_provenance"]
    icon = {"GO": "🟢", "GO WITH CONDITIONS": "🟡", "NO-GO": "🔴"}[r["verdict"]]
    L = ["# " + t("title"), ""]
    if not r["market"]:
        L += ["> " + t("no_market"), ""]
    elif prov["freshness"]["is_stale"]:
        L += ["> " + t("stale", n=prov["freshness"]["sessions_behind"]), ""]
    L += ["## {} {}: **{}**".format(icon, t("verdict"), r["verdict"]), "", t("v_" + r["verdict"]) + ". " + t("never"), ""]
    fails = [c for c in r["checks"] if c["status"] == ge.FAIL]
    warns = [c for c in r["checks"] if c["status"] == ge.WARN]
    if fails:
        L += ["### " + t("blockers"), ""] + ["- **{}** – {}".format(t("n_" + c["id"]), reason_text(c, lang)) for c in fails] + [""]
    if warns:
        L += ["### " + t("conditions"), ""] + ["- **{}** – {}".format(t("n_" + c["id"]), reason_text(c, lang)) for c in warns] + [""]
    L += ["## " + t("trade"), "", "| | |", "|---|---|",
          "| {} | {} |".format(t("k_ticker"), tr["ticker"]),
          "| {} | {} |".format(t("k_entry"), num(tr["entry"], 2, lang)),
          "| {} | {} |".format(t("k_stop"), num(tr.get("stop"), 2, lang)),
          "| {} | {} |".format(t("k_target"), num(tr.get("target"), 2, lang)),
          "| {} | {} |".format(t("k_shares"), num(tr["shares"], 0, lang)),
          "| {} | {} |".format(t("k_value"), pln(nm["value"], 2, lang)),
          "| {} | {} ({}) |".format(t("k_account"), pln(tr["account_size"], 0, lang), tr.get("account_type", "regular").upper()),
          "| {} | {} + {} |".format(t("k_fees"), pln(nm["fee_entry"], 2, lang), pln(nm.get("fee_exit_stop"), 2, lang)),
          "| {} | {} ({}) |".format(t("k_risk"), pln(nm.get("risk_total"), 2, lang),
                                    pct(nm["risk_total"] / tr["account_size"] * 100, 2, lang) if nm.get("risk_total") is not None else "—"),
          "| {} | {} |".format(t("k_rr"), (num(nm["rr"], 2, lang) + "R") if nm.get("rr") is not None else "—"),
          "| {} | {} ({}% min {}) |".format(t("k_broker"), prov["broker_tariff"]["broker"], num(tr["fee_pct"], 2, lang),
                                           pln(tr["fee_min"], 2, lang))]
    if r.get("sizer_json"):
        L.append("| {} | {} |".format(t("k_source"), r["sizer_json"]))
    L += ["", "## " + t("checks"), "", "| # | {} | {} | {} |".format(t("c_check"), t("c_status"), t("c_reason")), "|---|---|---|---|"]
    for i, c in enumerate(r["checks"], 1):
        L.append("| {} | {} | **{}** | {} |".format(i, t("n_" + c["id"]), c["status"], reason_text(c, lang)))
    L.append("")
    L += ["**{}:** ".format(t("inputs")) + ("; ".join(t("inp_line", skill=i["skill"], path=i["path"], as_of=i["as_of"] or "—",
                                                       n=i["sessions_behind"] if i["sessions_behind"] is not None else "—")
                                                 for i in r["inputs"]) or t("inp_none")), ""]
    if r.get("override"):
        used = any(c["reason"] == "regime_overridden" for c in r["checks"])
        L += ["**{}{}:** „{}”".format(t("override_rec"), "" if used else t("override_unused"), r["override"]), ""]
    L += ["_" + t("journal_note", d=r["journal_dir"]) + "_", ""]
    L += ["## " + t("next"), ""]
    if any(c["id"] == "thesis" and c["status"] == ge.FAIL for c in r["checks"]):
        L += ["- " + t("next_journal"), "", "```bash", journal_command(r), "```", ""]
    if any(c["id"] in ("risk", "position", "liquidity", "heat", "sector") and c["status"] == ge.FAIL for c in r["checks"]):
        L.append("- " + t("next_sizer"))
    if r["verdict"] != "NO-GO":
        L.append("- " + t("next_go"))
    L += [""] + ["- " + n for n in prov["broker_tariff"]["notes"]]
    L += ["", "## " + t("sources"), ""] + ["- {} ({})".format(s, prov["as_of"] or "—") for s in prov["sources"]]
    L += ["", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _pos(text: str) -> float:
    try:
        v = float(text.replace(",", ".").replace(" ", ""))
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number")
    if not math.isfinite(v) or v <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return v


def _nonneg(text: str) -> float:
    try:
        v = float(text.replace(",", "."))
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number")
    if not math.isfinite(v) or v < 0:
        raise argparse.ArgumentTypeError("must be a non-negative number")
    return v


def _shares(text: str) -> int:
    try:
        v = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number (GPW lot = 1 share)")
    if v <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return v


def _date(text: str) -> str:
    try:
        dt.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD")
    return text


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fail-closed pre-trade checklist for GPW long entries (PL/EN). Never places orders.")
    p.add_argument("--ticker", help="GPW short name, e.g. PKOBP")
    p.add_argument("--entry", type=_pos, help="Planned entry price")
    p.add_argument("--stop", type=_pos, help="Stop-loss price (required by the gate)")
    p.add_argument("--shares", type=_shares, help="Planned share count")
    p.add_argument("--target", type=_pos, help="Target price (optional; else the thesis plan target)")
    p.add_argument("--account-size", type=_pos, help="Account value in PLN")
    p.add_argument("--account-type", choices=["regular", "ike", "ikze", "oki"])
    p.add_argument("--sizer-json", type=Path, help="gpw-position-sizer JSON report (fills ticker/entry/stop/shares/target/account)")
    p.add_argument("--thesis-id", help="Journal thesis id (or unique prefix); default: the one open thesis for the ticker")
    p.add_argument("--state-dir", help="Journal directory (default ~/.gpw-trading-skills/journal or $GPW_JOURNAL_DIR)")
    p.add_argument("--regime-json", help="gpw-market-regime JSON (default: newest in --input-dir)")
    p.add_argument("--exposure-json", help="gpw-exposure-coach JSON (default: newest in --input-dir)")
    p.add_argument("--input-dir", default=".", help="Where to auto-discover other skills' JSON (default .)")
    p.add_argument("--override", help="Written reason to proceed despite a bear market with top risk >= 80 (recorded)")
    p.add_argument("--broker", choices=sorted(brokers.BROKERS), help="Commission preset (default mbank, or the sizer's)")
    p.add_argument("--commission-pct", type=_nonneg)
    p.add_argument("--commission-min", type=_nonneg)
    p.add_argument("--max-risk-pct", type=_pos, help="Max risk per trade %% (default 1; capped by exposure-coach)")
    p.add_argument("--max-position-pct", type=_pos, help="Max position %% of account (default 25)")
    p.add_argument("--max-adv-pct", type=_pos, help="Max order %% of median daily turnover (default 10)")
    p.add_argument("--max-heat-pct", type=_pos, help="Max total open risk %% (default 6)")
    p.add_argument("--max-sector-pct", type=_pos, help="Max sector %% after the trade (default 35)")
    p.add_argument("--min-rr", type=_pos, help="Reward/risk below this = WARN (default 1.5)")
    p.add_argument("--as-of", type=_date, help="Evaluate with data up to this date")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--cache-dir")
    p.add_argument("--lang", choices=["pl", "en"], default="pl")
    p.add_argument("--output-dir", default=".")
    p.add_argument("--no-files", action="store_true", help="Print Markdown only, write no files")
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.sizer_json:
        missing = [f for f in ("ticker", "entry", "shares", "account_size") if getattr(args, f) is None]
        if missing:
            parser.error("missing --{} (or pass --sizer-json)".format(", --".join(m.replace("_", "-") for m in missing)))
    if args.override is not None and len(args.override.strip()) < MIN_OVERRIDE_CHARS:
        parser.error("--override needs a written reason of at least {} characters".format(MIN_OVERRIDE_CHARS))
    if args.override:
        args.override = args.override.strip()
    try:
        result = run(args)
    except (ge.GateError, gpw_archive.GPWFetchError, ValueError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    md = render_markdown(result, args.lang)
    if args.no_files:
        print(md)
        return 0
    out = Path(args.output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    jp, mp = out / "{}_{}.json".format(PREFIX, stamp), out / "{}_{}.md".format(PREFIX, stamp)
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(md, encoding="utf-8")
    print(md)
    print("JSON: {}\nMarkdown: {}".format(jp, mp), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
