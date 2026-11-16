#!/usr/bin/env python3
"""gpw-dividend-analyzer: dividend history, reliability, real net yield and traps.

Modes
-----
screen                 rank a universe (WIG20 / mWIG40 / sWIG80 or --tickers) by
                       stability score and net yield, with filters
ticker X               full history of one company, upcoming dividend, net
                       amount for --shares N, IKE vs regular over --years with
                       reinvestment, ex-date drop and dividend-capture example
portfolio              journal open positions (or --tickers + --shares):
                       expected income for the next 12 months, gross and net

Examples
--------
python3 scripts/gpw_dividend_analyzer.py screen --universe WIG20,MWIG40
python3 scripts/gpw_dividend_analyzer.py ticker PZU --shares 200
python3 scripts/gpw_dividend_analyzer.py portfolio --tickers PZU,PKOBP --shares 100,50
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import (brokers, constituents, fmt, fundamentals, gpw_archive, http, journal_io, panel,  # noqa: E402
                    plcal, provenance, tax)

import dividend_engine as E  # noqa: E402
import dividend_sources as S  # noqa: E402

D = dt.date
SKILL = "gpw-dividend-analyzer"
VERSION = "1.0.0"
PREFIX = "gpw_dividend_analyzer"
DEFAULT_ESPI_DAYS = 365          # one AGM season of resolutions and proposals
DEFAULT_MAX_REPORTS = 150        # report bodies read per run (1 request each, cached a year)
REPORTS_PER_TICKER = 4           # newest dividend reports per company worth reading
DEFAULT_CAPTURE_AMOUNT = 10_000.0
DEFAULT_BROKER = "mbank"         # a typical 0.39% / 5 zł bank-broker tariff (gpwlib.brokers)
SIZE_INDICES = ("WIG20", "MWIG40", "SWIG80")
# A series whose last quote is more than 5 sessions older than the archive
# end is suspended or delisted: its price (and yield) is not current.
STALE_SERIES_SESSIONS = 5

STRINGS = {
    "title": {"pl": "Analiza dywidend GPW", "en": "GPW dividend analysis"},
    "stale": {"pl": "UWAGA: notowania kończą się {as_of} ({n} sesji temu) - ceny i stopy dywidendy mogą być nieaktualne.",
              "en": "WARNING: quotes end {as_of} ({n} sessions ago) - prices and yields may be out of date."},
    "history_span": {"pl": "Historia z archiwum GPW: {start} - {end} ({years} pełnych lat). Dywidendy < 0,5% ceny są w archiwum niewidoczne - uzupełnia je ESPI.",
                     "en": "History from the GPW archive: {start} - {end} ({years} full years). Dividends below 0.5% of the price are invisible in the archive - ESPI fills them in."},
    "short_history": {"pl": "Krótka historia ({n} lat obserwacji) - wnioski o stabilności są słabe.",
                      "en": "Short history ({n} observed years) - stability conclusions are weak."},
    "screen_answer": {"pl": "Najstabilniejsi płatnicy dywidend w {u}: {top}. Najwyższa stopa netto (konto zwykłe): {best}.",
                      "en": "Most reliable dividend payers in {u}: {top}. Highest net yield (regular account): {best}."},
    "screen_none": {"pl": "Żadna spółka nie spełnia filtrów.", "en": "No company passes the filters."},
    "ticker_answer": {"pl": "{t}: stopa brutto {y} (netto na zwykłym koncie {n}, w IKE {i}); dywidenda wypłacona w {p} z {o} lat, obniżki/zawieszenia: {c}; ocena stabilności {s}/100.",
                      "en": "{t}: gross yield {y} (net on a regular account {n}, in IKE {i}); paid in {p} of {o} years, cuts/suspensions: {c}; stability score {s}/100."},
    "portfolio_answer": {"pl": "Oczekiwane dywidendy w 12 miesięcy: {g} brutto, {n} netto na zwykłym koncie ({k} wypłat; {e} szacowanych z historii).",
                         "en": "Expected dividends in 12 months: {g} gross, {n} net on a regular account ({k} payments; {e} estimated from history)."},
    "flags": {"pl": "Flagi pułapek", "en": "Trap flags"},
    "upcoming": {"pl": "Najbliższa dywidenda", "en": "Upcoming dividend"},
    "no_upcoming": {"pl": "Brak opublikowanej uchwały/propozycji; termin szacowany z historii (nie jest potwierdzony).",
                    "en": "No published resolution/proposal; the date is estimated from history (not confirmed)."},
    "history": {"pl": "Historia dywidend (rok dnia odcięcia)", "en": "Dividend history (calendar year of the ex-date)"},
    "events": {"pl": "Dni odcięcia", "en": "Ex-dates"},
    "net_for": {"pl": "Dla {n} akcji: brutto {g}, podatek Belki {t}, netto {nn} (IKE/IKZE: {g} bez potrącenia).",
                "en": "For {n} shares: gross {g}, Belka tax {t}, net {nn} (IKE/IKZE: {g} with nothing withheld)."},
    "ike10": {"pl": "IKE vs zwykłe konto przez {y} lat (reinwestycja, wzrost {gpct}/rok)",
              "en": "IKE vs regular account over {y} years (reinvested, growth {gpct}/yr)"},
    "drop": {"pl": "Spadek kursu w dniu odcięcia", "en": "Price drop on the ex-date"},
    "capture": {"pl": "Dlaczego „łapanie dywidendy” nie działa", "en": "Why dividend capture does not work"},
    "sources": {"pl": "Źródła", "en": "Sources"},
    "warnings": {"pl": "Ostrzeżenia", "en": "Warnings"},
    "no_tickers": {"pl": "Brak spółek do analizy (pusty dziennik i brak --tickers).",
                   "en": "No companies to analyse (empty journal and no --tickers)."},
    "unknown_ticker": {"pl": "Nieznany ticker {t} - brak w archiwum notowań do {d}.",
                       "en": "Unknown ticker {t} - not in the quotes archive up to {d}."},
    "no_archive": {"pl": "Brak notowań w cache ({c}). Uruchom bez --offline albo najpierw skill z historią notowań.",
                   "en": "No quotes in the cache ({c}). Run without --offline or run a skill that fetches history first."},
}

FLAG_TEXT = {
    "yield_above_2x_sector_median": {"pl": "stopa > 2× mediana sektora", "en": "yield > 2× sector median"},
    "payout_above_100pct": {"pl": "wypłata > 100% zysku", "en": "payout > 100% of profit"},
    "net_loss": {"pl": "strata netto", "en": "net loss"},
    "falling_eps": {"pl": "spadający zysk", "en": "falling profit"},
    "one_off_special_dividend": {"pl": "jednorazowa/nadzwyczajna dywidenda", "en": "one-off / special dividend"},
    "fcf_below_dividend": {"pl": "FCF nie pokrywa dywidendy", "en": "FCF below dividend"},
    "recent_cut_or_suspension": {"pl": "niedawna obniżka/zawieszenie", "en": "recent cut / suspension"},
    "state_controlled_policy_risk": {"pl": "kontrola Skarbu Państwa (ryzyko polityczne)",
                                     "en": "state control (policy risk)"},
    "bank_knf_dividend_policy": {"pl": "bank - limity KNF", "en": "bank - KNF limits"},
    "board_proposes_no_dividend": {"pl": "zarząd proponuje brak dywidendy", "en": "board proposes no dividend"},
}


class AnalyzerError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Arguments
# --------------------------------------------------------------------------

def _csv(s: Optional[str]) -> List[str]:
    return [x.strip().upper() for x in (s or "").split(",") if x.strip()]


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="GPW dividend analyzer (PL/EN): history, reliability, net yield, traps")
    p.add_argument("mode", choices=["screen", "ticker", "portfolio"])
    p.add_argument("symbol", nargs="?", default=None, help="ticker for 'ticker' mode (GPW short name, e.g. PZU)")
    p.add_argument("--universe", default="WIG20,MWIG40", help="screen: WIG20,MWIG40,SWIG80 (comma list)")
    p.add_argument("--tickers", default=None, help="comma-separated GPW short names (screen/portfolio)")
    p.add_argument("--shares", default=None,
                   help="ticker: number of shares (default 100); portfolio: comma list matching --tickers")
    p.add_argument("--min-yield", type=float, default=None, help="screen: minimum gross trailing yield %%")
    p.add_argument("--min-years-paid", type=int, default=None, help="screen: minimum years with a dividend")
    p.add_argument("--min-score", type=int, default=None, help="screen: minimum stability score 0-100")
    p.add_argument("--exclude-traps", action="store_true", help="screen: drop companies with high-severity trap flags")
    p.add_argument("--sort", choices=["stability", "yield"], default="stability")
    p.add_argument("--top", type=int, default=25)
    p.add_argument("--years", type=int, default=10, help="ticker: IKE vs regular horizon in years")
    p.add_argument("--growth-pct", type=float, default=0.0, help="ticker: price and DPS growth per year %% (default 0)")
    p.add_argument("--broker", default=DEFAULT_BROKER, choices=sorted(brokers.BROKERS))
    p.add_argument("--commission-pct", type=float, default=None)
    p.add_argument("--commission-min", type=float, default=None)
    p.add_argument("--capture-amount", type=float, default=DEFAULT_CAPTURE_AMOUNT,
                   help="ticker: amount in PLN for the dividend-capture example")
    p.add_argument("--espi-days", type=int, default=DEFAULT_ESPI_DAYS, help="ESPI look-back in days (0 = skip)")
    p.add_argument("--no-espi", action="store_true", help="skip ESPI (archive only)")
    p.add_argument("--max-reports", type=int, default=DEFAULT_MAX_REPORTS)
    p.add_argument("--no-fundamentals", action="store_true", help="skip the gpw.pl card (payout, FCF, EPS)")
    p.add_argument("--source", action="append", default=[], choices=["biznesradar"],
                   help="optional longer history from biznesradar.pl (personal local use only)")
    p.add_argument("--br-code", action="append", default=[], metavar="TICKER=CODE",
                   help="biznesradar company code when it differs from the GPW ticker")
    p.add_argument("--isin", action="append", default=[], metavar="TICKER=ISIN")
    p.add_argument("--journal-dir", default=None)
    p.add_argument("--as-of", default=None)
    p.add_argument("--offline", action="store_true")
    p.add_argument("--cache-dir", default=None)
    p.add_argument("--output-dir", default=".")
    p.add_argument("--no-files", action="store_true")
    p.add_argument("--lang", choices=["pl", "en"], default="pl")
    a = p.parse_args(argv)
    if a.mode == "ticker":
        if not a.symbol:
            p.error("mode 'ticker' needs a ticker, e.g. 'ticker PZU'")
        a.symbol = a.symbol.strip().upper()
    elif a.symbol:
        p.error("unexpected positional argument {!r} for mode '{}'".format(a.symbol, a.mode))
    a.universe_list = _csv(a.universe)
    bad = [u for u in a.universe_list if u not in SIZE_INDICES]
    if bad:
        p.error("--universe accepts {} (got {})".format(",".join(SIZE_INDICES), ",".join(bad)))
    a.ticker_list = _csv(a.tickers)
    shares = [x.strip() for x in (a.shares or "").split(",") if x.strip()]
    try:
        a.shares_list = [int(x) for x in shares]
    except ValueError:
        p.error("--shares must be whole numbers")
    if any(n <= 0 for n in a.shares_list):
        p.error("--shares must be positive")
    if a.mode == "ticker" and len(a.shares_list) > 1:
        p.error("ticker mode takes one --shares value")
    if a.mode == "portfolio" and a.shares_list and len(a.shares_list) != len(a.ticker_list):
        p.error("--shares needs one value per ticker in --tickers")
    if a.mode == "portfolio" and a.ticker_list and not a.shares_list:
        p.error("portfolio mode with --tickers needs --shares (one per ticker)")
    if not (1 <= a.years <= 40):
        p.error("--years must be 1..40")
    if not (-50 < a.growth_pct < 50):
        p.error("--growth-pct must be between -50 and 50")
    if not (0 <= a.espi_days <= 1500):
        p.error("--espi-days must be 0..1500")
    if a.max_reports < 0 or a.top < 1:
        p.error("--max-reports must be >= 0 and --top >= 1")
    if a.capture_amount <= 0:
        p.error("--capture-amount must be positive")
    if a.min_score is not None and not (0 <= a.min_score <= 100):
        p.error("--min-score must be 0..100")
    if a.as_of:
        try:
            a.as_of = D.fromisoformat(a.as_of)
        except ValueError:
            p.error("--as-of must be YYYY-MM-DD")
    a.isin_map = {}
    for item in a.isin:
        if "=" not in item:
            p.error("--isin expects TICKER=ISIN")
        t, i = item.split("=", 1)
        if not fundamentals.isin_is_valid(i.strip().upper()):
            p.error("invalid ISIN {!r}".format(i))
        a.isin_map[t.strip().upper()] = i.strip().upper()
    a.br_codes = {}
    for item in a.br_code:
        if "=" not in item:
            p.error("--br-code expects TICKER=CODE")
        t, c = item.split("=", 1)
        a.br_codes[t.strip().upper()] = c.strip().upper()
    return a


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def series_info(s, gaps: set) -> Dict:
    raw = E.raw_closes(s.dates, s.close, s.adjustments)
    facs = {a["date"]: a["factor"] for a in s.adjustments}
    # raw opens: same unadjustment as closes (ratio adjusted close / raw close)
    ratio = [s.close[i] / raw[i] if raw[i] else 1.0 for i in range(len(raw))]
    raw_open = [s.open[i] / ratio[i] for i in range(len(raw))]
    divs = E.reconstruct_dividends(s.dates, raw, s.adjustments, gaps)
    return {"raw_close": raw, "raw_open": raw_open, "archive_events": divs, "factors": facs}


def is_bank_company(t: str, sector_idx: Optional[str], snap: Optional[Dict]) -> bool:
    if sector_idx == "WIG-BANKI":
        return True
    sec = ((snap or {}).get("indicators") or {}).get("sector") or ""
    return "bank" in sec.lower()


def is_insurer(t: str, snap: Optional[Dict]) -> bool:
    sec = ((snap or {}).get("indicators") or {}).get("sector") or ""
    return t in S.INSURERS or "ubezpiecz" in sec.lower()


def fundamentals_metrics(t: str, snap: Optional[Dict], ttm: float, price: float, bank: bool, insurer: bool) -> Dict:
    """Payout ratio, FCF cover and profit change from the gpw.pl card (+ local history)."""
    out = {"payout_ratio": None, "payout_basis": None, "payout_pe_based": None, "fcf_cover": None,
           "fcf_note": None, "net_profit_pln": None, "profit_change_pct": None, "period": None,
           "shares_issued": None, "pe": None, "error": None}
    if not snap:
        return out
    if snap.get("error"):
        out["error"] = snap["error"]
        return out
    ind = snap.get("indicators") or {}
    shares = ind.get("shares_issued")
    pe = ind.get("pe")
    out["shares_issued"], out["pe"] = shares, pe
    if pe and pe > 0 and price > 0 and ttm > 0:
        # P/E (GPW, trailing) -> EPS = price / PE; payout = DPS / EPS
        out["payout_pe_based"] = round(ttm * pe / price * 100, 1)
    noto = snap.get("notoria") or {}
    vals = noto.get("values") or {}
    months = (noto.get("period") or {}).get("months")
    profit = vals.get("net_profit_parent") if vals.get("net_profit_parent") is not None else vals.get("net_profit")
    if profit is not None and months:
        ann = profit * 12.0 / months
        out["net_profit_pln"] = profit
        out["period"] = noto.get("period_label")
        if shares and ann > 0 and ttm > 0:
            out["payout_ratio"] = round(ttm * shares / ann * 100, 1)
            out["payout_basis"] = "full_year" if months == 12 else "ytd_annualized_{}m".format(months)
        elif shares and ann <= 0 and ttm > 0:
            out["payout_ratio"] = None
            out["payout_basis"] = "loss"
        if not (bank or insurer):
            cfo, capex = vals.get("cfo"), vals.get("capex")
            if cfo is not None and capex is not None and shares and ttm > 0:
                fcf = (cfo + capex) * 12.0 / months  # capex is negative on the card
                out["fcf_cover"] = round(fcf / (ttm * shares), 2)
        else:
            out["fcf_note"] = "not meaningful for banks/insurers"
    hist = S.profit_history(t)
    if hist and noto.get("period"):
        key = "net_profit_parent" if vals.get("net_profit_parent") is not None else "net_profit"
        ser = fundamentals.history_series(hist, key)
        p = noto["period"]
        prev = next((x for x in ser if x["months"] == p.get("months") and x["period_end"]
                     and x["period_end"][:4] == str(p.get("year", 0) - 1)), None)
        if prev and prev["value"] and profit is not None and prev["value"] > 0:
            out["profit_change_pct"] = round((profit / prev["value"] - 1) * 100, 1)
    return out


def espi_items_for(rows: List[Dict], bodies: Dict[int, Dict]) -> List[Dict]:
    out = []
    for r in rows:
        body = bodies.get(r["geru_id"])
        info = E.parse_dividend_text(r["title"], (body or {}).get("body_text") or "")
        info.update({"published": r["date"], "title": r["title"], "url": r["url"], "geru_id": r["geru_id"],
                     "body_read": body is not None, "correction": r.get("correction", False)})
        out.append(info)
    return out


def upcoming_for(t: str, merged: Dict, items: List[Dict], events: List[Dict], annual: List[Dict], as_of: D) -> Dict:
    """Confirmed (resolution with dates), proposed (no dates / proposal) or
    estimated (history) next payments."""
    last_ex = events[-1]["ex_date"] if events else "0000"
    rows = []
    for u in merged["upcoming"]:
        rows.append({"ex_date": u.get("ex_date"), "record_date": u.get("record_date"),
                     "payment_date": (u.get("payment_dates") or [u.get("payment_date")])[0]
                     if (u.get("payment_dates") or u.get("payment_date")) else None,
                     "dps": u.get("dps"), "date_kind": "confirmed" if u.get("stage") in ("resolution", "interim")
                     else ("proposed" if u.get("stage") == "proposal" else "third_party"),
                     "stage": u.get("stage") or u.get("status"), "source": u.get("url") or u.get("source")})
    # a proposal / interim without dates, published after the last ex-date
    dated_pub = max((i["published"] for i in items if i.get("ex_date") and i["ex_date"] > as_of.isoformat()),
                    default="0000")
    undated = [i for i in items if not i.get("ex_date") and i.get("dps") and i.get("currency") in (None, "PLN")
               and i["stage"] in ("proposal", "interim", "resolution") and i["published"] > last_ex
               and i["published"] > dated_pub]
    if undated:
        i = sorted(undated, key=lambda x: x["published"])[-1]
        est = E.estimate_next(events, annual, as_of)
        rows.append({"ex_date": est[0]["ex_date"] if est else None, "record_date": None, "payment_date": None,
                     "dps": i["dps"], "date_kind": "proposed_date_estimated", "stage": i["stage"],
                     "source": i["url"]})
    no_div = [i for i in items if i["stage"] == "no_dividend" and i["published"] > last_ex]
    if not rows:
        for e in E.estimate_next(events, annual, as_of):
            rows.append(dict(e, record_date=None, payment_date=None, stage="history"))
    rows.sort(key=lambda r: r.get("ex_date") or "9999")
    return {"rows": rows, "no_dividend_report": no_div[-1] if no_div else None}


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def run(args) -> Dict:
    lang = args.lang
    today = args.as_of or plcal.today_warsaw()
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else None
    warnings: List[Dict] = []
    sources: List[str] = []

    # ---- prices
    arch = S.load_archive(today, cache_dir, args.offline)
    sessions = arch["sessions"]
    if not sessions:
        raise AnalyzerError(fmt.T(STRINGS, lang)("no_archive", c=cache_dir or gpw_archive.default_cache_dir()))
    as_of = D.fromisoformat(sessions[-1]["date"])
    pnl = panel.build_panel(sessions)
    gdates = [s["date"] for s in sessions]
    # gpwlib 1.2: panel no longer infers adjustments across sessions missing from
    # the cache (gpw_archive marks them); the engine's own plcal-based check is
    # kept as a second line of defence for the dates that follow a gap.
    cache_missing = gpw_archive.session_gaps(sessions)
    gaps = E.cache_gap_dates(gdates) if cache_missing else set()
    sources.append("https://www.gpw.pl/archiwum-notowan (GPW quotes archive, cached sessions {} - {}, {} sessions; "
                   "ex-dates and DPS from gpwlib.panel reference-price adjustments)".format(gdates[0], gdates[-1],
                                                                                           len(gdates)))
    if cache_missing:
        warnings.append({"pl": "Cache archiwum ma {} luk(i) (np. {}); zmiany ceny po luce nie są traktowane jako dywidendy."
                               .format(len(cache_missing), cache_missing[0]),
                         "en": "The archive cache has {} gap(s) (e.g. {}); price changes after a gap are not treated "
                               "as dividends.".format(len(cache_missing), cache_missing[0])})

    try:
        size = constituents.size_membership(cache_dir, offline=True)
    except Exception:
        size = {}
    try:
        sector = constituents.sector_membership(cache_dir, offline=True)
    except Exception:
        sector = {}

    # ---- who
    positions: Dict[str, Dict] = {}
    if args.mode == "ticker":
        targets = [args.symbol]
    elif args.mode == "screen":
        targets = args.ticker_list or sorted(t for t, idx in size.items() if idx in args.universe_list)
        if not targets:
            raise AnalyzerError("empty universe {} (no GPW Benchmark portfolios in the cache?)".format(
                ",".join(args.universe_list)))
    else:
        if args.ticker_list:
            for t, n in zip(args.ticker_list, args.shares_list):
                positions[t] = {"shares": n, "account_type": "regular", "source": "cli"}
        else:
            theses = journal_io.load_theses(Path(args.journal_dir).expanduser() if args.journal_dir else None)
            for p in journal_io.open_positions(theses):
                cur = positions.setdefault(p["ticker"].upper(), {"shares": 0, "account_type": p["account_type"],
                                                                 "source": "journal"})
                cur["shares"] += p["shares"]
        targets = sorted(positions)
        if not targets:
            raise AnalyzerError(fmt.T(STRINGS, lang)("no_tickers"))
    missing = [t for t in targets if t not in pnl]
    if args.mode == "ticker" and missing:
        raise AnalyzerError(fmt.T(STRINGS, lang)("unknown_ticker", t=args.symbol, d=as_of.isoformat()))
    for t in missing:
        warnings.append({"pl": "{}: brak w archiwum notowań - pominięto.".format(t),
                         "en": "{}: not in the quotes archive - skipped.".format(t)})
    targets = [t for t in targets if t in pnl]

    # ---- archive reconstruction for the peer set (sector medians)
    peer = set(targets) | {t for t in size} | {t for t, s in sector.items() if s in {sector.get(x) for x in targets}}
    info: Dict[str, Dict] = {}
    peer_yield: Dict[str, float] = {}
    for t in sorted(peer):
        s = pnl.get(t)
        if s is None or len(s) < 2:
            continue
        inf = series_info(s, gaps)
        info[t] = inf
        evs = [e for e in inf["archive_events"] if e["kind"] == "dividend"]
        y = E.yield_pct(E.ttm_dps(evs, as_of), inf["raw_close"][-1])
        if y and y > 0 and s.dates[-1] >= E.shift_sessions(as_of, -STALE_SERIES_SESSIONS).isoformat():
            peer_yield[t] = y
    market_median = E.median_or_none(v for t, v in peer_yield.items() if t in size)

    # ---- ESPI
    isin_of = dict(fundamentals.isin_map(cache_dir, offline=True))
    isin_of.update(args.isin_map)
    if not args.offline:
        for t in targets:
            if t not in isin_of:
                try:
                    isin_of[t] = fundamentals.resolve_isin(t, cache_dir)["isin"]
                except Exception:
                    pass
    ticker_of = {v: k for k, v in isin_of.items()}
    espi_rows: Dict[str, List[Dict]] = {t: [] for t in targets}
    espi_meta = {"days": 0, "missing_days": 0, "reports": 0, "bodies_read": 0, "range": None, "error": None}
    bodies: Dict[int, Dict] = {}
    if not args.no_espi and args.espi_days > 0:
        start = today - dt.timedelta(days=args.espi_days)
        espi_meta["range"] = [start.isoformat(), today.isoformat()]
        try:
            res = S.scan_espi(start, today, cache_dir, args.offline)
            espi_meta["days"] = len(res["days"])
            espi_meta["missing_days"] = len(res["missing_days"])
            want = set(targets)
            for r in res["rows"]:
                t = ticker_of.get(r.get("isin") or "")
                if t in want and r["class"] == "dividend":
                    espi_rows[t].append(r)
            espi_meta["reports"] = sum(len(v) for v in espi_rows.values())
            sources.append("https://www.gpw.pl/komunikaty (ESPI/EBI list, {} days {} - {})".format(
                len(res["days"]), start.isoformat(), today.isoformat()))
            if res["missing_days"]:
                warnings.append({"pl": "Brak w cache {} dni ESPI (offline) - uchwały dywidendowe mogą być niepełne."
                                       .format(len(res["missing_days"])),
                                 "en": "{} ESPI days missing from the cache (offline) - dividend resolutions may be "
                                       "incomplete.".format(len(res["missing_days"]))})
        except http.HTTPFetchError as exc:
            espi_meta["error"] = str(exc)
            warnings.append({"pl": "ESPI niedostępne: {} - tylko archiwum.".format(exc),
                             "en": "ESPI unavailable: {} - archive only.".format(exc)})
        todo = []
        for t in targets:
            rs = sorted(espi_rows[t], key=lambda r: r["datetime"], reverse=True)[:REPORTS_PER_TICKER]
            todo += [(0 if args.mode != "screen" else 1, r) for r in rs]
        todo.sort(key=lambda x: (x[0], -int(x[1]["date"].replace("-", ""))))
        for _p, r in todo[:args.max_reports]:
            b = S.fetch_report(r["geru_id"], cache_dir, args.offline)
            if b:
                bodies[r["geru_id"]] = b
        espi_meta["bodies_read"] = len(bodies)
        if bodies:
            sources.append("https://www.gpw.pl/komunikat?geru_id=N (dividend report pages, {} read)".format(len(bodies)))
        skipped = sum(1 for t in targets for r in espi_rows[t]) - len(bodies)
        if skipped > 0:
            warnings.append({"pl": "{} raportów dywidendowych bez treści (limit/offline) - odczytano tylko tytuły.".format(skipped),
                             "en": "{} dividend reports without body (cap/offline) - titles only.".format(skipped)})
    for t in targets:
        if t not in isin_of and not args.no_espi:
            warnings.append({"pl": "{}: brak ISIN - raporty ESPI nie zostaną dopasowane (podaj --isin).".format(t),
                             "en": "{}: no ISIN - ESPI reports cannot be matched (pass --isin).".format(t)})

    # ---- fundamentals
    snaps: Dict[str, Dict] = {}
    if not args.no_fundamentals:
        for t in targets:
            snaps[t] = S.snapshot(t, isin_of.get(t), cache_dir, args.offline)
        ok = [t for t in targets if not snaps[t].get("error")]
        if ok:
            sources.append("https://www.gpw.pl/spolka?isin=... (indicators tab: shares, P/E; Notoria tab: net profit, "
                           "cash flows - latest period, year-to-date) for {} companies".format(len(ok)))
        bad = [t for t in targets if snaps[t].get("error")]
        if bad:
            warnings.append({"pl": "Brak danych finansowych z gpw.pl dla: {} - wskaźnik wypłaty nieznany.".format(", ".join(bad[:10])),
                             "en": "No gpw.pl financials for: {} - payout ratio unknown.".format(", ".join(bad[:10]))})

    # ---- biznesradar (optional)
    br: Dict[str, List[Dict]] = {}
    if "biznesradar" in args.source:
        for t in targets:
            code = args.br_codes.get(t, t)
            try:
                got = S.fetch_biznesradar(code, cache_dir, args.offline)
                br[t] = got["rows"]
                if not got["rows"]:
                    warnings.append({"pl": "biznesradar: brak tabeli dla {} (kod {}; podaj --br-code).".format(t, code),
                                     "en": "biznesradar: no table for {} (code {}; pass --br-code).".format(t, code)})
            except http.HTTPFetchError as exc:
                warnings.append({"pl": "biznesradar {}: {}".format(t, exc), "en": "biznesradar {}: {}".format(t, exc)})
        if br:
            sources.append(S.BIZNESRADAR_COMPANY_URL.format("<CODE>") + " (optional; personal local use only)")

    idx_ret = S.index_returns(as_of, cache_dir) if args.mode == "ticker" else {}
    b = brokers.resolve(args.broker, args.commission_pct, args.commission_min)

    # ---- per company
    companies = []
    details: Dict[str, Dict] = {}
    for t in targets:
        s = pnl[t]
        inf = info.get(t) or series_info(s, gaps)
        price = inf["raw_close"][-1]
        items = espi_items_for(espi_rows.get(t, []), bodies)
        extra = [r for r in br.get(t, []) if r.get("ex_date") and r.get("dps")]
        first_date = s.dates[0]
        if extra:
            first_br = min(r["ex_date"] for r in extra)
            first_date = min(first_date, "{}-01-01".format(first_br[:4]))
        merged = E.merge_history(inf["archive_events"], items, as_of, extra,
                                 espi_range=tuple(espi_meta["range"]) if espi_meta["range"] and not espi_meta["error"]
                                 and not espi_meta["missing_days"] else None)
        for x in merged["crosscheck"]:
            if x["status"] == "archive_only_large_cut_excluded":
                warnings.append({"pl": "{}: spadek ceny odniesienia o {}% w dniu {} bez raportu ESPI o dywidendzie - "
                                       "nie liczony jako dywidenda (emisja/podział/błąd danych?).".format(
                                           t, round(x["cut_pct"], 1), x["ex_date"]),
                                 "en": "{}: reference price cut of {}% on {} with no ESPI dividend report - not "
                                       "counted as a dividend (rights issue/spin-off/data error?).".format(
                                           t, round(x["cut_pct"], 1), x["ex_date"])})
        events = merged["events"]
        annual = E.annual_history(events, first_date, as_of)
        m = E.history_metrics(annual)
        ttm = E.ttm_dps(events, as_of)
        y_gross = E.yield_pct(ttm, price)
        sec = sector.get(t)
        sec_vals = [v for k, v in peer_yield.items() if sector.get(k) == sec and sec] if sec else []
        if len(sec_vals) >= E.MIN_SECTOR_PAYERS:
            sec_median, sec_basis = statistics.median(sec_vals), sec
        else:
            sec_median, sec_basis = market_median, "market (WIG20+mWIG40+sWIG80 payers)"
        snap = snaps.get(t)
        bank = is_bank_company(t, sec, snap)
        ins = is_insurer(t, snap)
        fm = fundamentals_metrics(t, snap, ttm, price, bank, ins)
        payout = fm["payout_ratio"] if fm["payout_ratio"] is not None else fm["payout_pe_based"]
        up = upcoming_for(t, merged, items, events, annual, as_of)
        state = S.STATE_HOLDINGS.get(t)
        flags = E.trap_flags(yield_gross=y_gross, sector_median=sec_median, payout_pct=payout,
                             profit_change_pct=fm["profit_change_pct"], net_profit=fm["net_profit_pln"],
                             annual=annual, special_text=any(e.get("special") for e in events[-2:]),
                             state_controlled=dict(state, as_of=S.STATE_HOLDINGS_AS_OF) if state else None,
                             is_bank=bank, fcf_cover=fm["fcf_cover"], metrics=m)
        if up["no_dividend_report"]:
            flags.append({"code": "board_proposes_no_dividend", "severity": "high",
                          "detail": {"published": up["no_dividend_report"]["published"],
                                     "url": up["no_dividend_report"]["url"]}})
        if bank:
            for f in flags:
                if f["code"] == "bank_knf_dividend_policy":
                    f["detail"] = {"knf_year": S.KNF_STANCE["year"], "source": S.KNF_STANCE["source"]}
        score = E.stability_score(m, payout)
        fwd = [r for r in up["rows"] if r["date_kind"] in ("confirmed", "proposed", "proposed_date_estimated")]
        fwd_dps = round(sum(r["dps"] for r in fwd if r.get("dps")), 4) if fwd else None
        nets = E.net_yields(y_gross)
        stale_series = s.dates[-1] < E.shift_sessions(as_of, -STALE_SERIES_SESSIONS).isoformat()
        dps_history = [{"year": a["year"], "dps": a["dps"], "payments": a["payments"], "complete": a["complete"],
                        "sources": a["sources"], "special": a["special"]} for a in annual]
        comp = {
            "ticker": t, "isin": isin_of.get(t), "index": size.get(t), "sector_index": sec,
            "price": round(price, 4), "price_date": s.dates[-1],
            "dps_history": dps_history,
            "years_observed": m["years_observed"], "years_paid": m["years_paid"],
            "consecutive_years": m["consecutive_years"],
            "cuts": m["cuts"], "n_cuts": m["n_cuts"], "n_suspensions": m["n_suspensions"],
            "dps_cagr_3y_pct": m["dps_cagr_3y_pct"], "dps_cagr_5y_pct": m["dps_cagr_5y_pct"],
            "dps_ttm": ttm,
            "yield_gross_pct": y_gross,
            "yield_net_regular_pct": nets["regular"],
            "yield_ike_pct": nets["ike"],
            "yield_ikze_after_exit_tax_pct": nets["ikze_after_exit_tax"],
            "forward_dps": fwd_dps,
            "forward_yield_gross_pct": E.yield_pct(fwd_dps, price) if fwd_dps else None,
            "payout_ratio": payout,
            "payout_basis": fm["payout_basis"] if fm["payout_ratio"] is not None else
            ("pe_trailing" if fm["payout_pe_based"] is not None else None),
            "payout_pe_based": fm["payout_pe_based"],
            "fcf_cover": fm["fcf_cover"],
            "sector_median_yield_pct": round(sec_median, 3) if sec_median else None,
            "sector_median_basis": sec_basis,
            "stability_score": score["score"], "stability_parts": score["parts"],
            "stability_confidence": score["confidence"],
            "trap_flags": [f["code"] for f in flags],
            "trap_details": flags,
            "is_bank": bank, "is_insurer": ins,
            "stale_price": stale_series,
            "espi_reports": len(items),
        }
        companies.append(comp)
        details[t] = {"events": events, "annual": annual, "merged": merged, "upcoming": up, "info": inf,
                      "fundamentals": fm, "items": items}

    # ---- mode specifics
    result_extra: Dict = {}
    upcoming_all = []
    for c in companies:
        for r in details[c["ticker"]]["upcoming"]["rows"]:
            upcoming_all.append(dict(r, ticker=c["ticker"]))
    upcoming_all.sort(key=lambda r: r.get("ex_date") or "9999")

    if args.mode == "screen":
        rows = companies
        if args.min_yield is not None:
            rows = [c for c in rows if (c["yield_gross_pct"] or 0) >= args.min_yield]
        if args.min_years_paid is not None:
            rows = [c for c in rows if c["years_paid"] >= args.min_years_paid]
        if args.min_score is not None:
            rows = [c for c in rows if c["stability_score"] >= args.min_score]
        if args.exclude_traps:
            rows = [c for c in rows if not any(f["severity"] == "high" for f in c["trap_details"])]
        if args.sort == "yield":
            rows.sort(key=lambda c: (-(c["yield_net_regular_pct"] or 0), -c["stability_score"]))
        else:
            rows.sort(key=lambda c: (-c["stability_score"], -(c["yield_net_regular_pct"] or 0)))
        for i, c in enumerate(rows, 1):
            c["rank"] = i
        result_extra["screen"] = {"universe": args.ticker_list or args.universe_list, "n_universe": len(companies),
                                  "n_passed": len(rows), "sort": args.sort,
                                  "filters": {"min_yield": args.min_yield, "min_years_paid": args.min_years_paid,
                                              "min_score": args.min_score, "exclude_traps": args.exclude_traps}}
        companies_out = rows[:args.top]
    elif args.mode == "ticker":
        c = companies[0]
        det = details[c["ticker"]]
        n = args.shares_list[0] if args.shares_list else 100
        last_dps = next((a["dps"] for a in reversed(E._final_years(det["annual"])) if a["dps"] > 0), 0.0)
        base_dps = c["forward_dps"] or c["dps_ttm"] or last_dps
        drops = E.exdate_drops(det["events"], pnl[c["ticker"]].dates, det["info"]["raw_open"],
                               det["info"]["raw_close"], idx_ret)
        cap = None
        if base_dps and c["price"] > 0:
            cap = E.capture_example(args.capture_amount, c["price"], base_dps,
                                    drops["median_ratio_close"], b["pct"], b["min_fee"])
        sim = None
        if base_dps and c["price"] > 0:
            sim = E.ike_vs_regular(c["price"], base_dps, n, args.years, args.growth_pct, b["pct"], b["min_fee"])
        result_extra["ticker_detail"] = {
            "shares": n, "dps_basis": base_dps,
            "dps_basis_kind": "forward" if c["forward_dps"] else ("ttm" if c["dps_ttm"] else "last_year"),
            "cash_regular": E.dividend_cash(base_dps, n, "regular") if base_dps else None,
            "cash_ike": E.dividend_cash(base_dps, n, "ike") if base_dps else None,
            "ex_events": det["events"],
            "archive_events_all": [e for e in det["info"]["archive_events"]],
            "crosscheck": det["merged"]["crosscheck"],
            "espi_reports": [{k: i.get(k) for k in ("published", "title", "stage", "dps", "record_date", "ex_date",
                                                     "payment_dates", "special", "url", "body_read")}
                             for i in det["items"]],
            "exdate_drop": drops, "capture_example": cap, "ike_vs_regular": sim,
            "fundamentals": det["fundamentals"],
            "broker": {"key": b["key"], "label": b["label"], "pct": b["pct"], "min_fee": b["min_fee"]},
            "knf_stance": S.KNF_STANCE if c["is_bank"] else None,
        }
        companies_out = companies
    else:
        items = []
        n_est = 0
        for c in companies:
            pos = positions[c["ticker"]]
            for r in details[c["ticker"]]["upcoming"]["rows"]:
                if r.get("dps") and r.get("ex_date"):
                    items.append({"ticker": c["ticker"], "ex_date": r["ex_date"], "dps": r["dps"],
                                  "shares": pos["shares"], "date_kind": r["date_kind"],
                                  "payment_date": r.get("payment_date"), "account_type": pos["account_type"]})
        cal = E.income_calendar(items, as_of)
        n_est = sum(1 for r in cal["rows"] if r["date_kind"] == "estimated")
        tax_free = sum(r["tax_regular"] for r in cal["rows"] if r["account_type"] in ("ike", "ikze", "oki"))
        result_extra["portfolio"] = {"positions": positions, "calendar": cal, "n_estimated": n_est,
                                     "tax_saved_in_tax_accounts": round(tax_free, 2)}
        companies_out = companies

    # ---- signals (generic contract)
    signals = []
    for c in companies:
        cr = E.cut_risk(c["trap_details"])
        if cr and not c["stale_price"]:
            signals.append({"ticker": c["ticker"], "date": as_of.isoformat(), "kind": "dividend_cut_risk",
                            "direction": "avoid", "score": cr["score"], "price": c["price"],
                            "tags": {"flags": ",".join(cr["codes"])[:80], "yield_pct": c["yield_gross_pct"]}})

    sources += ["Belka 19%: art. 30a ust. 1 pkt 4, art. 41 ust. 4 ustawy o PIT; rounding art. 63 § 1 Ordynacji "
                "podatkowej; IKE/IKZE: ustawa z 20.04.2004 (Dz.U. 2026 poz. 91), IKZE exit 10% art. 30 ust. 1 pkt 10a "
                "PIT (gpwlib.tax)",
                "KSH art. 348 § 4 (record date) and KDPW T+2 (ex-date = record date - 1 session)",
                "KNF dividend policy stance {}: {}".format(S.KNF_STANCE["year"], S.KNF_STANCE["source"]),
                "State holdings: company IR pages ({})".format(S.STATE_HOLDINGS_AS_OF),
                "Broker tariff: gpwlib.brokers ({}, entered {})".format(b["label"], brokers.TARIFF_ENTERED.isoformat())]
    full_years = max((c["years_observed"] for c in companies), default=0)
    notes = ["DPS reconstructed as previous close - reference price on the ex-date; precision about "
             "+-(0.005% + half a tick) of the price; cuts < 0.5% of the price are invisible in the archive.",
             "Calendar-year aggregation by ex-date; two-tranche payers (e.g. LPP) sum both tranches.",
             "Payout ratio uses the latest gpw.pl Notoria period (year-to-date, annualised) or the GPW P/E."]
    prov = provenance.build(as_of=as_of, sources=sources, sessions_used=len(gdates), universe_size=len(targets),
                            notes=notes, corporate_actions_adjusted=True, survivorship_free=True,
                            reference_date=args.as_of, max_sessions_behind=1)
    for tn in brokers.tariff_notes(lang, b["overridden"], today):
        warnings.append({"pl": tn, "en": tn})
    out = {
        "skill": SKILL, "version": VERSION, "lang": lang, "mode": args.mode, "as_of": as_of.isoformat(),
        "reference_date": today.isoformat(),
        "history": {"first_session": gdates[0], "last_session": gdates[-1], "max_years_observed": full_years,
                    "short": full_years < 5, "invisible_below_pct": round(panel.ADJ_TOLERANCE * 100, 2)},
        "companies": companies_out,
        "upcoming": [u for u in upcoming_all if u.get("ex_date") is None or u["ex_date"] > as_of.isoformat()],
        "signals": signals,
        "espi_scan": espi_meta,
        "warnings": warnings,
        "data_provenance": prov,
    }
    out.update(result_extra)
    return out


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

def _d(s: Optional[str], lang: str) -> str:
    if not s:
        return "—"
    d = D.fromisoformat(s)
    return d.strftime("%d.%m.%Y") if lang == "pl" else s


def _flags(c: Dict, lang: str) -> str:
    return ", ".join(FLAG_TEXT.get(f, {}).get(lang, f) for f in c["trap_flags"]) or "—"


def answer_line(r: Dict, lang: str) -> str:
    T = fmt.T(STRINGS, lang)
    cs = r["companies"]
    if r["mode"] == "screen":
        if not cs:
            return T("screen_none")
        top = ", ".join("{} ({})".format(c["ticker"], c["stability_score"]) for c in cs[:3])
        best = max(cs, key=lambda c: c["yield_net_regular_pct"] or 0)
        return T("screen_answer", u="/".join(r["screen"]["universe"]), top=top,
                 best="{} {}".format(best["ticker"], fmt.pct(best["yield_net_regular_pct"], 2, lang)))
    if r["mode"] == "ticker":
        c = cs[0]
        return T("ticker_answer", t=c["ticker"], y=fmt.pct(c["yield_gross_pct"], 2, lang),
                 n=fmt.pct(c["yield_net_regular_pct"], 2, lang), i=fmt.pct(c["yield_ike_pct"], 2, lang),
                 p=c["years_paid"], o=c["years_observed"], c=c["n_cuts"] + c["n_suspensions"], s=c["stability_score"])
    cal = r["portfolio"]["calendar"]
    return T("portfolio_answer", g=fmt.pln(cal["total_gross"], 2, lang), n=fmt.pln(cal["total_net_regular"], 2, lang),
             k=len(cal["rows"]), e=r["portfolio"]["n_estimated"])


def render_markdown(r: Dict, lang: str) -> str:
    T = fmt.T(STRINGS, lang)
    L = []
    fr = r["data_provenance"]["freshness"]
    if fr["is_stale"]:
        L.append("> ⚠️ " + T("stale", as_of=fr["as_of"], n=fr["sessions_behind"]))
        L.append("")
    L.append("# {} – {} ({})".format(T("title"), r["mode"], _d(r["as_of"], lang)))
    L.append("")
    L.append("**" + answer_line(r, lang) + "**")
    L.append("")
    h = r["history"]
    L.append(T("history_span", start=_d(h["first_session"], lang), end=_d(h["last_session"], lang),
               years=h["max_years_observed"]))
    if h["short"]:
        L.append("")
        L.append("> " + T("short_history", n=h["max_years_observed"]))
    L.append("")
    pl = lang == "pl"
    if r["mode"] == "screen":
        L.append("| # | Ticker | " + ("Stabilność | Lata wypł./obs. | Obniżki | Stopa brutto | Netto (zwykłe) | IKE | "
                                    "Wypłata z zysku | CAGR DPS 5l | Flagi |" if pl else
                                    "Stability | Years paid/obs. | Cuts | Gross yield | Net (regular) | IKE | "
                                    "Payout | DPS CAGR 5y | Flags |"))
        L.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for c in r["companies"]:
            L.append("| {} | {} | {} | {}/{} | {} | {} | {} | {} | {} | {} | {} |".format(
                c["rank"], c["ticker"], c["stability_score"], c["years_paid"], c["years_observed"],
                c["n_cuts"] + c["n_suspensions"], fmt.pct(c["yield_gross_pct"], 2, lang),
                fmt.pct(c["yield_net_regular_pct"], 2, lang), fmt.pct(c["yield_ike_pct"], 2, lang),
                fmt.pct(c["payout_ratio"], 0, lang), fmt.pct(c["dps_cagr_5y_pct"], 1, lang), _flags(c, lang)))
        L.append("")
    for c in (r["companies"] if r["mode"] != "screen" else []):
        L.append("## {} ({})".format(c["ticker"], c.get("index") or "—"))
        L.append("")
        L.append("### " + T("history"))
        L.append("")
        L.append("| " + ("Rok | DPS | Wypłaty | Źródło |" if pl else "Year | DPS | Payments | Source |"))
        L.append("|---|---|---|---|")
        for a in c["dps_history"]:
            L.append("| {}{} | {} | {} | {} |".format(a["year"], "" if a["complete"] else "*",
                                                     fmt.pln(a["dps"], 2, lang), a["payments"],
                                                     ", ".join(a["sources"]) or "—"))
        L.append("")
        L.append(("Stopa brutto {} · netto zwykłe konto {} · IKE {} · IKZE po 10% przy wypłacie {} · "
                  "wypłata z zysku {} ({}) · FCF/dywidenda {} · stabilność {}/100 ({}) · flagi: {}" if pl else
                  "Gross yield {} · net regular {} · IKE {} · IKZE after 10% exit tax {} · payout {} ({}) · "
                  "FCF cover {} · stability {}/100 ({}) · flags: {}").format(
            fmt.pct(c["yield_gross_pct"], 2, lang), fmt.pct(c["yield_net_regular_pct"], 2, lang),
            fmt.pct(c["yield_ike_pct"], 2, lang), fmt.pct(c["yield_ikze_after_exit_tax_pct"], 2, lang),
            fmt.pct(c["payout_ratio"], 0, lang), c["payout_basis"] or "—", fmt.num(c["fcf_cover"], 2, lang),
            c["stability_score"], c["stability_confidence"], _flags(c, lang)))
        L.append("")
    ups = r["upcoming"]
    if ups:
        L.append("## " + T("upcoming"))
        L.append("")
        L.append("| Ticker | " + ("Dzień odcięcia | Wypłata | DPS | Status |" if pl else "Ex-date | Payment | DPS | Status |"))
        L.append("|---|---|---|---|---|")
        for u in ups[:40]:
            L.append("| {} | {} | {} | {} | {} |".format(u["ticker"], _d(u.get("ex_date"), lang),
                                                       _d(u.get("payment_date"), lang),
                                                       fmt.pln(u.get("dps"), 2, lang), u["date_kind"]))
        L.append("")
    if r["mode"] == "ticker":
        td = r["ticker_detail"]
        c = r["companies"][0]
        if all(u["date_kind"] == "estimated" for u in ups):
            L.append("> " + T("no_upcoming"))
            L.append("")
        if td["cash_regular"]:
            L.append(T("net_for", n=td["shares"], g=fmt.pln(td["cash_regular"]["gross"], 2, lang),
                       t=fmt.pln(td["cash_regular"]["tax"], 2, lang), nn=fmt.pln(td["cash_regular"]["net"], 2, lang)))
            L.append("")
        sim = td["ike_vs_regular"]
        if sim:
            a = sim["assumptions"]
            L.append("### " + T("ike10", y=a["years"], gpct=fmt.pct(a["growth_pct"], 1, lang)))
            L.append("")
            L.append("| " + ("Konto | Akcje na końcu | Wartość | Podatki od dywidend | Podatek przy wyjściu | Po podatku |"
                             if pl else "Account | Shares at end | Value | Dividend tax | Exit tax | After tax |"))
            L.append("|---|---|---|---|---|---|")
            for acc in ("regular", "ike", "ikze"):
                x = sim[acc]
                L.append("| {} | {} | {} | {} | {} | {} |".format(acc.upper() if acc != "regular" else
                                                                  ("zwykłe" if pl else "regular"), x["shares_end"],
                                                                  fmt.pln(x["value_before_exit"], 0, lang),
                                                                  fmt.pln(x["dividend_tax_paid"], 0, lang),
                                                                  fmt.pln(x["exit_tax"], 0, lang),
                                                                  fmt.pln(x["value_after_exit"], 0, lang)))
            L.append("")
            L.append(("Różnica IKE − zwykłe: **{}** (IKE bez podatku pod warunkiem wypłaty po 60. r.ż. i spełnienia warunków ustawy; "
                      "IKZE dodatkowo daje ulgę w PIT przy wpłacie - tu pominiętą)." if pl else
                      "IKE − regular difference: **{}** (IKE tax-free if withdrawn after 60 under the act's conditions; "
                      "the IKZE contribution deduction is not included here).").format(
                fmt.pln(sim["ike_minus_regular"], 0, lang)))
            L.append("")
        dr = td["exdate_drop"]
        if dr["n"]:
            L.append("### " + T("drop"))
            L.append("")
            L.append(("Mediana z {} dni odcięcia: kurs otwarcia spadł o {}× dywidendy, zamknięcia o {}× "
                      "(po korekcie o ruch WIG: {}×). 1,0 = spadek dokładnie o dywidendę." if pl else
                      "Median of {} ex-dates: the open fell by {}× the dividend, the close by {}× "
                      "(net of the WIG move: {}×). 1.0 = a drop of exactly the dividend.").format(
                dr["n"], fmt.num(dr["median_ratio_open"], 2, lang), fmt.num(dr["median_ratio_close"], 2, lang),
                fmt.num(dr["median_ratio_close_mkt"], 2, lang)))
            L.append("")
        cap = td["capture_example"]
        if cap:
            L.append("### " + T("capture"))
            L.append("")
            L.append(("Kupno {} akcji za {} na ostatniej sesji z prawem i sprzedaż w dniu odcięcia ({}, spadek {}× DPS): "
                      "dywidenda brutto {}, Belka {}, prowizje {}, strata na cenie {} → wynik **{}** "
                      "({} jeśli stratę odliczysz od innych zysków w PIT-38). Podatek od dywidendy pobiera płatnik i "
                      "nie można go pomniejszyć o stratę ze sprzedaży akcji (inne źródło przychodu). "
                      "Próg opłacalności: spadek kursu < {}× dywidendy." if pl else
                      "Buying {} shares for {} on the last cum-dividend session and selling on the ex-date ({}, drop "
                      "{}× DPS): gross dividend {}, Belka {}, fees {}, price loss {} → result **{}** ({} if the loss "
                      "offsets other gains in PIT-38). The dividend tax is withheld at source and cannot be reduced by "
                      "the share-sale loss (a different income source). Break-even: a drop below {}× the dividend.").format(
                cap["shares"], fmt.pln(cap["amount"], 0, lang), td["broker"]["label"],
                fmt.num(cap["drop_ratio_used"], 2, lang), fmt.pln(cap["dividend_gross"], 2, lang),
                fmt.pln(cap["dividend_tax"], 2, lang), fmt.pln(cap["fees"], 2, lang),
                fmt.pln(cap["price_loss"], 2, lang), fmt.pln(cap["result"], 2, lang),
                fmt.pln(cap["result_if_loss_offsets_other_gains"], 2, lang),
                fmt.num(cap["breakeven_drop_ratio"], 2, lang)))
            L.append("")
        if td["crosscheck"]:
            L.append(("Kontrola ESPI vs archiwum: " if pl else "ESPI vs archive check: ") + "; ".join(
                "{} {}/{} {}".format(x["ex_date"], fmt.num(x["espi_dps"], 2, lang),
                                     fmt.num(x["archive_dps"], 2, lang), x["status"]) for x in td["crosscheck"]))
            L.append("")
        if td["knf_stance"]:
            L.append("> KNF {}: {}".format(td["knf_stance"]["year"], td["knf_stance"]["summary_" + lang]))
            L.append("")
        if c["trap_details"]:
            L.append("### " + T("flags"))
            L.append("")
            for f in c["trap_details"]:
                L.append("- **{}** ({}): {}".format(FLAG_TEXT.get(f["code"], {}).get(lang, f["code"]), f["severity"],
                                                    json.dumps(f["detail"], ensure_ascii=False)[:240]))
            L.append("")
    if r["mode"] == "portfolio":
        cal = r["portfolio"]["calendar"]
        L.append("| " + ("Miesiąc | Brutto | Netto (zwykłe) |" if pl else "Month | Gross | Net (regular) |"))
        L.append("|---|---|---|")
        for mth, v in cal["by_month"].items():
            L.append("| {} | {} | {} |".format(mth, fmt.pln(v["gross"], 2, lang), fmt.pln(v["net_regular"], 2, lang)))
        L.append("")
    if r["signals"]:
        L.append(("Sygnały ryzyka obniżki dywidendy: " if pl else "Dividend cut-risk signals: ") +
                 ", ".join("{} ({})".format(s["ticker"], s["tags"]["flags"]) for s in r["signals"]))
        L.append("")
    if r["warnings"]:
        L.append("## " + T("warnings"))
        L.append("")
        for w in r["warnings"]:
            L.append("- " + w[lang])
        L.append("")
    L.append("## " + T("sources"))
    L.append("")
    for s in r["data_provenance"]["sources"]:
        L.append("- " + s)
    L.append("- " + ("Dane na dzień {}" if pl else "Data as of {}").format(r["as_of"]))
    L.append("")
    L.append("_" + fmt.DISCLAIMER[lang] + "_")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        r = run(args)
    except (AnalyzerError, fundamentals.FundamentalsError, ValueError, KeyError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    except http.HTTPFetchError as exc:
        print("error: data source unavailable: {}".format(exc), file=sys.stderr)
        return 1
    md = render_markdown(r, args.lang)
    if not args.no_files:
        out_dir = Path(args.output_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        base = out_dir / "{}_{}".format(PREFIX, stamp)
        base.with_suffix(".json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        base.with_suffix(".md").write_text(md, encoding="utf-8")
        print(md)
        print("Files: {}.json, {}.md".format(base, base), file=sys.stderr)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
