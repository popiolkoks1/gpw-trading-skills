#!/usr/bin/env python3
"""GPW Ownership - who is buying and who is betting against (CLI).

Modes:
    shorts    KNF register of net short positions >= 0.5% (fully automatic)
    insiders  MAR art. 19 managers' transactions detected on ESPI (semi-automatic)
    parse     parse a pasted ESMA notification form (PDF text) into transactions
    funds     OFE/PPK annual portfolio structure (user files or analizy.pl)
    ticker    everything above for one company

    python3 scripts/gpw_ownership.py shorts --lang pl
    python3 scripts/gpw_ownership.py insiders --days 30
    python3 scripts/gpw_ownership.py parse --file notice.txt --ticker KRUK
    python3 scripts/gpw_ownership.py funds --file nn_2025.pdf --file nn_2024.pdf
    python3 scripts/gpw_ownership.py ticker MODIVO
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import espi, fundamentals, gpw_archive, http, panel, plcal, provenance  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct, pln  # noqa: E402

import ownership_funds as of  # noqa: E402
import ownership_insiders as oi  # noqa: E402
import ownership_pdftext as opdf  # noqa: E402
import ownership_shorts as osh  # noqa: E402

VERSION = "1.0.0"
SKILL = "gpw-ownership"
FUNDS_URL = "https://dokumenty.analizy.pl/pobierz/fe/{code}/RR/{date}"
MAX_INPUT_BYTES = 60 * 1024 * 1024
DEFAULT_SESSIONS = 500  # ~2 years: price change since first disclosure of most open positions

STRINGS = {
    "title_shorts": {"pl": "Krótka sprzedaż na GPW — rejestr KNF", "en": "Short positions on GPW — KNF register"},
    "title_insiders": {"pl": "Transakcje insiderów (art. 19 MAR)", "en": "Insider transactions (MAR art. 19)"},
    "title_parse": {"pl": "Powiadomienie art. 19 MAR — odczytane transakcje", "en": "MAR art. 19 notification — parsed transactions"},
    "title_funds": {"pl": "Portfele OFE/PPK — akcje polskich spółek", "en": "OFE/PPK portfolios — Polish shares"},
    "title_ticker": {"pl": "Akcjonariat i pozycje: {t}", "en": "Ownership and positions: {t}"},
    "as_of": {"pl": "Dane na", "en": "Data as of"},
    "generated": {"pl": "Wygenerowano", "en": "Generated"},
    "stale": {"pl": "⚠️ UWAGA: dane rynkowe są nieaktualne o {n} sesji.", "en": "⚠️ WARNING: market data is {n} session(s) old."},
    "warnings": {"pl": "Ostrzeżenia", "en": "Warnings"},
    "summary": {"pl": "Najważniejsze", "en": "Key points"},
    "sh_summary": {"pl": "{p} pozycji krótkich ≥ 0,5% w {i} spółkach; „zatłoczone”: {c}.",
                   "en": "{p} net short positions ≥ 0.5% in {i} companies; crowded: {c}."},
    "sh_invisible": {"pl": "Rejestr pokazuje tylko pozycje ≥ 0,5% kapitału (art. 6 rozporządzenia SSR 236/2012). Mniejsze pozycje (zgłaszane do KNF od 0,1%) są niewidoczne, więc suma to DOLNA granica krótkiej sprzedaży.",
                     "en": "The register shows only positions ≥ 0.5% of share capital (SSR 236/2012 art. 6). Smaller positions (reported to KNF from 0.1%) are invisible, so totals are a LOWER bound of short interest."},
    "col_ticker": {"pl": "Spółka", "en": "Company"},
    "col_total": {"pl": "Suma %", "en": "Total %"},
    "col_holders": {"pl": "Posiadacze", "en": "Holders"},
    "col_ch20": {"pl": "Zmiana 20 sesji", "en": "20-session change"},
    "col_days": {"pl": "Dni w rejestrze", "en": "Days in register"},
    "col_dtc": {"pl": "Dni pokrycia", "en": "Days to cover"},
    "col_px": {"pl": "Kurs od 1. ujawnienia", "en": "Price since 1st disclosure"},
    "col_crowded": {"pl": "Zatłoczona", "en": "Crowded"},
    "col_holder": {"pl": "Posiadacz", "en": "Holder"},
    "col_date": {"pl": "Data", "en": "Date"},
    "col_first": {"pl": "Pierwsze ujawnienie", "en": "First disclosed"},
    "yes": {"pl": "TAK", "en": "YES"},
    "no": {"pl": "nie", "en": "no"},
    "events": {"pl": "Zmiany w ostatnich 20 sesjach", "en": "Changes in the last 20 sessions"},
    "ev_new": {"pl": "nowa pozycja", "en": "new position"},
    "ev_closed": {"pl": "zniknęła z rejestru (spadek poniżej 0,5%, data dokładna nieznana; ostatnie ujawnienie {d}, {p}%)",
                  "en": "left the register (fell below 0.5%, exact date unknown; last disclosed {d}, {p}%)"},
    "holders_detail": {"pl": "Posiadacze pozycji", "en": "Position holders"},
    "dtc_note": {"pl": "Dni pokrycia = (suma % × liczba akcji) / mediana dziennego wolumenu z 20 sesji (wolumen ≈ obrót / kurs). Przybliżenie.",
                 "en": "Days to cover = (total % × shares outstanding) / 20-session median daily volume (volume ≈ turnover / close). Approximation."},
    "crowded_def": {"pl": "„Zatłoczona” = suma ≥ {t}% lub ≥ {h} posiadaczy (uzasadnienie w references/methodology.md).",
                    "en": "\"Crowded\" = total ≥ {t}% or ≥ {h} holders (rationale in references/methodology.md)."},
    "ins_summary": {"pl": "{n} powiadomień art. 19 MAR w okresie {s} – {e} ({c} spółek). Szczegóły (wolumen, cena) tylko z wklejonego PDF.",
                    "en": "{n} MAR art. 19 notices between {s} and {e} ({c} companies). Details (volume, price) only from pasted PDFs."},
    "ins_threshold": {"pl": "Obowiązek zgłoszenia powstaje po przekroczeniu 20 000 EUR transakcji w roku (art. 19 ust. 8 MAR po Listing Act; Polska nie zmieniła progu według listy ESMA z 19.01.2026). Mniejsze transakcje są niewidoczne.",
                      "en": "Notification is required once transactions exceed EUR 20,000 in a calendar year (MAR art. 19(8) after the Listing Act; Poland has not changed the threshold per the ESMA list of 19 Jan 2026). Smaller trades are invisible."},
    "col_buys": {"pl": "Kupno", "en": "Buys"},
    "col_sells": {"pl": "Sprzedaż", "en": "Sells"},
    "col_insiders": {"pl": "Osoby", "en": "Insiders"},
    "col_net": {"pl": "Saldo PLN", "en": "Net PLN"},
    "col_cluster": {"pl": "Klaster", "en": "Cluster"},
    "col_last": {"pl": "Ostatnie", "en": "Last"},
    "col_flags": {"pl": "Flagi", "en": "Flags"},
    "col_basis": {"pl": "Podstawa", "en": "Basis"},
    "basis_parsed_transactions": {"pl": "wklejone PDF", "en": "pasted PDF"},
    "basis_notice_hints": {"pl": "tytuł/treść raportu", "en": "report title/body"},
    "notices": {"pl": "Wykryte powiadomienia", "en": "Detected notices"},
    "col_title": {"pl": "Tytuł", "en": "Title"},
    "col_dir": {"pl": "Kierunek", "en": "Direction"},
    "how_details": {"pl": "Aby policzyć wartość i klaster: pobierz PDF z linku raportu, skopiuj tekst i uruchom tryb `parse` (dane zostaną zapisane w {d}).",
                    "en": "To get values and clusters: download the PDF from the report link, copy its text and run `parse` (stored in {d})."},
    "parse_none": {"pl": "Nie znaleziono transakcji w podanym tekście (czy to formularz z art. 19 MAR?).",
                   "en": "No transactions found in the text (is it a MAR art. 19 form?)."},
    "col_person": {"pl": "Osoba", "en": "Person"},
    "col_position": {"pl": "Stanowisko", "en": "Position"},
    "col_nature": {"pl": "Rodzaj", "en": "Nature"},
    "col_volume": {"pl": "Wolumen", "en": "Volume"},
    "col_price": {"pl": "Śr. cena", "en": "Avg price"},
    "col_value": {"pl": "Wartość", "en": "Value"},
    "col_place": {"pl": "Miejsce", "en": "Place"},
    "stored": {"pl": "Zapisano {n} nowych transakcji w {p} (razem {t}).", "en": "Stored {n} new transactions in {p} (total {t})."},
    "excluded_note": {"pl": "Transakcje „wyłączone” (opcje, program motywacyjny, darowizna, zastaw, zapisy, wezwania) nie są liczone jako decyzje rynkowe.",
                      "en": "\"Excluded\" transactions (options, incentive plans, gifts, pledges, subscriptions, tenders) are not counted as market decisions."},
    "fd_summary": {"pl": "{f} plik(ów) funduszy, {h} pozycji w akcjach polskich spółek, {m} dopasowanych do tickerów.",
                   "en": "{f} fund file(s), {h} Polish equity holdings, {m} matched to tickers."},
    "fd_approx": {"pl": "Fundusze podają tylko wartość (PLN) i udział %. Liczba akcji = wartość / kurs z końca roku i przepływ = zmiana wartości minus efekt ceny — to PRZYBLIŻENIA. Brak dowodów, że naśladowanie OFE daje przewagę.",
                  "en": "Funds report only value (PLN) and % of assets. Shares = value / year-end close and flow = value change minus price effect are APPROXIMATIONS. There is no evidence that following OFE pays."},
    "col_funds": {"pl": "Fundusze", "en": "Funds"},
    "col_shares": {"pl": "≈ akcji", "en": "≈ shares"},
    "col_flow": {"pl": "≈ przepływ r/r", "en": "≈ flow YoY"},
    "col_pctf": {"pl": "Śr. % funduszu", "en": "Avg % of fund"},
    "top_buys": {"pl": "Największe ≈ zakupy (r/r)", "en": "Largest ≈ buys (YoY)"},
    "top_sells": {"pl": "Największe ≈ sprzedaże (r/r)", "en": "Largest ≈ sells (YoY)"},
    "unmatched": {"pl": "Niedopasowane nazwy (dodaj do --name-map)", "en": "Unmatched names (add to --name-map)"},
    "funds_files": {"pl": "Pliki", "en": "Files"},
    "no_prev": {"pl": "Brak poprzedniego roku dla porównania r/r (podaj plik z poprzedniego roku lub uruchom ponownie za rok — dane są zapamiętywane).",
                "en": "No previous year to compare (pass last year's file or rerun next year — parsed data is stored)."},
    "signals": {"pl": "Sygnały (do dziennika gpw-signal-postmortem)", "en": "Signals (for the gpw-signal-postmortem ledger)"},
    "sources": {"pl": "Źródła", "en": "Sources"},
    "none": {"pl": "brak", "en": "none"},
    "sh_none_ticker": {"pl": "Brak pozycji krótkich ≥ 0,5% dla tej spółki w rejestrze KNF.",
                       "en": "No net short positions ≥ 0.5% for this company in the KNF register."},
    "fd_none_ticker": {"pl": "Brak zapisanych portfeli OFE/PPK z tą spółką (uruchom tryb funds z plikami).",
                       "en": "No stored OFE/PPK portfolios with this company (run funds mode with files)."},
    "history": {"pl": "Historia sumy pozycji (tygodniowo, ostatnie 12 tygodni, dolna granica)", "en": "Total position history (weekly, last 12 weeks, lower bound)"},
}


# --------------------------------------------------------------------------
# Arguments
# --------------------------------------------------------------------------

def _date(s: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD, got {!r}".format(s))


def parse_args(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lang", choices=["pl", "en"], default="pl")
    common.add_argument("--output-dir", default=".")
    common.add_argument("--no-files", action="store_true", help="print only, write no report files")
    common.add_argument("--cache-dir", default=None)
    common.add_argument("--state-dir", default=None, help="default ~/.gpw-trading-skills")
    common.add_argument("--offline", action="store_true")
    common.add_argument("--as-of", type=_date, default=None)
    common.add_argument("--json", action="store_true", help="print the JSON instead of Markdown")

    p = argparse.ArgumentParser(description="GPW ownership: KNF shorts, MAR art. 19 insiders, OFE/PPK funds (PL/EN)")
    sub = p.add_subparsers(dest="mode", required=True)

    s = sub.add_parser("shorts", parents=[common], help="KNF net short positions register")
    s.add_argument("--since", type=_date, default=None, help="second change window start (YYYY-MM-DD)")
    s.add_argument("--ticker", action="append", default=[], help="limit the table to these tickers")
    s.add_argument("--no-shares", action="store_true", help="skip shares-outstanding lookups (no days to cover)")
    s.add_argument("--sessions", type=int, default=DEFAULT_SESSIONS)

    i = sub.add_parser("insiders", parents=[common], help="MAR art. 19 notices detected on ESPI")
    i.add_argument("--days", type=int, default=30, help="calendar days back from --as-of/today (default 30)")
    i.add_argument("--start", type=_date, default=None)
    i.add_argument("--end", type=_date, default=None)
    i.add_argument("--ticker", action="append", default=[])
    i.add_argument("--details", action="store_true", help="read each notice's report page (1 request each)")
    i.add_argument("--max-details", type=int, default=40)

    pa = sub.add_parser("parse", parents=[common], help="parse pasted ESMA notification text")
    pa.add_argument("--file", default="-", help="text file with the pasted PDF text, or - for stdin")
    pa.add_argument("--ticker", default=None)
    pa.add_argument("--no-store", action="store_true")

    f = sub.add_parser("funds", parents=[common], help="OFE/PPK annual portfolio structure")
    f.add_argument("--file", action="append", default=[], help="PDF, XLSX or CSV (repeat)")
    f.add_argument("--fetch-code", action="append", default=[],
                   help="analizy.pl fund code, e.g. ENNE1 (NN OFE); downloads the year-end report")
    f.add_argument("--year", type=int, action="append", default=[], help="year(s) for --fetch-code (default: last year)")
    f.add_argument("--name-map", default=None, help="JSON {\"full name\": \"TICKER\"}")
    f.add_argument("--include-newconnect", action="store_true")
    f.add_argument("--no-store", action="store_true")
    f.add_argument("--ticker", action="append", default=[])

    t = sub.add_parser("ticker", parents=[common], help="shorts + insiders + stored funds for one company")
    t.add_argument("symbol")
    t.add_argument("--days", type=int, default=90)
    t.add_argument("--details", action="store_true", default=True)
    t.add_argument("--no-details", dest="details", action="store_false")
    t.add_argument("--max-details", type=int, default=20)
    t.add_argument("--no-shares", action="store_true")
    t.add_argument("--sessions", type=int, default=DEFAULT_SESSIONS)

    a = p.parse_args(argv)
    if a.mode in ("shorts", "ticker") and a.sessions < 25:
        p.error("--sessions must be >= 25")
    if a.mode in ("insiders", "ticker") and not (1 <= a.days <= 400):
        p.error("--days must be between 1 and 400")
    if a.mode == "insiders" and a.max_details < 0:
        p.error("--max-details must be >= 0")
    if a.mode == "funds" and not a.file and not a.fetch_code:
        p.error("funds needs --file and/or --fetch-code")
    return a


class InputError(RuntimeError):
    """Bad user input discovered at run time (exit 1)."""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _cache(args) -> Optional[Path]:
    return Path(args.cache_dir).expanduser() if args.cache_dir else None


def _state(args) -> Path:
    return Path(args.state_dir).expanduser() if args.state_dir else Path.home() / ".gpw-trading-skills"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _reference_date(args) -> dt.date:
    return args.as_of or plcal.today_warsaw()


def load_market(args, n: int):
    """(panel, session dates, n_sessions, raw sessions); empty when quotes are unavailable offline."""
    try:
        sessions = gpw_archive.load_sessions(n, end=args.as_of, cache_dir=_cache(args), offline=args.offline,
                                             progress=False)
    except gpw_archive.GPWFetchError:
        if args.offline:
            return {}, [], 0, []
        raise
    if not sessions:
        return {}, [], 0, []
    return panel.build_panel(sessions), [s["date"] for s in sessions], len(sessions), sessions


def isin_tables(args):
    """``(ticker -> ISIN, ISIN -> current ticker)``.

    The reverse map prefers the current listing (gpw.pl quotes tables /
    GPW Benchmark) over pre-rename aliases from ``panel.RENAMES``: the
    ISIN PLCCC0000016 maps to MODIVO, never to the old ticker CCC (the
    1.2-beta reverse map let the alias win, so days to cover was empty and
    consumers had to translate CCC themselves).
    """
    m = fundamentals.isin_map(_cache(args), offline=True)
    if not m and not args.offline:
        try:
            m = fundamentals.isin_map(_cache(args), offline=False)
        except Exception:  # pragma: no cover - network trouble: fall back to names
            m = {}
    try:
        rev = dict(fundamentals.isin_to_ticker_map(_cache(args), offline=True))
    except Exception:  # pragma: no cover - unreadable cache: rebuild from m
        rev = {}
    for t, i in m.items():
        rev.setdefault(i, t)
    return m, {i: _newest_alias(t, i, m) for i, t in rev.items()}


def _newest_alias(ticker: str, isin: Optional[str], m: Dict[str, str]) -> str:
    """Follow ``panel.RENAMES`` from an old ticker to the newest one, but only
    while the newer name is the same company (same ISIN in ``m``) - a ticker
    later reused by another company is left alone."""
    t = ticker.upper()
    for _ in range(10):
        nxt = panel.canonical_name(t, "0000-00-00")  # undated: whole chain
        if nxt == t:
            break
        same = isin and m.get(nxt) == isin
        if not same:
            break
        t = nxt
    return t


def make_canon(m: Dict[str, str], rev: Dict[str, str], ref_iso: str):
    """``canon(ticker=None, isin=None, date=None) -> current ticker | None``.

    Every ticker gpw-ownership emits goes through it (shorts, insiders, funds,
    parse, ticker mode, signals): the ISIN decides first (current listing,
    :func:`isin_tables`), then a name is mapped through ``panel.RENAMES`` -
    dated (``panel.canonical_name`` on the record date) and then to the newest
    name of the same ISIN. The source's own name is kept by the callers in
    ``register_name``.
    """
    def canon(ticker: Optional[str] = None, isin: Optional[str] = None, date: Optional[str] = None):
        if isin and rev.get(isin):
            return rev[isin]
        if not ticker:
            return None
        t = panel.canonical_name(str(ticker).strip().upper(), (date or ref_iso)[:10])
        i = m.get(t)
        if i and rev.get(i):
            return rev[i]
        return _newest_alias(t, i, m) if i else t
    return canon


def _prov(as_of: dt.date, sources: List[str], args, n_sessions: int, universe=None, notes=None) -> Dict:
    return provenance.build(as_of=as_of, sources=sources, sessions_used=n_sessions, universe_size=universe,
                            notes=notes or [], reference_date=args.as_of, point_in_time_membership=False)


def _as_of_from(dates: List[str], args) -> dt.date:
    if dates:
        return dt.date.fromisoformat(dates[-1])
    d = _reference_date(args)
    while not plcal.is_probable_session(d) or (d == plcal.today_warsaw() and not plcal.session_is_final(d)):
        d -= dt.timedelta(days=1)
    return d


# --------------------------------------------------------------------------
# Shorts
# --------------------------------------------------------------------------

def _shares_lookup(args, warnings: List[str]):
    cache: Dict[str, Optional[float]] = {}

    def shares_of(ticker, isin):
        if getattr(args, "no_shares", False) or not isin:
            return None
        if isin in cache:
            return cache[isin]
        val = None
        try:
            resp = http.get(fundamentals.indicators_url(isin), cache_dir=_cache(args),
                            ttl_seconds=fundamentals.SNAPSHOT_TTL_S * 7, offline=args.offline)
            val = fundamentals.parse_indicators_html(resp.text).get("shares_issued")
        except (http.HTTPFetchError, fundamentals.FundamentalsError):
            warnings.append("shares outstanding unavailable for {} ({})".format(ticker, isin))
        cache[isin] = val
        return val
    return shares_of


def _snapshots(state: Path) -> List[Dict]:
    d = state / "ownership" / "shorts"
    out = []
    if d.exists():
        for p in sorted(d.glob("register_*.json")):
            data = _read_json(p, None)
            if isinstance(data, dict) and data.get("date") and isinstance(data.get("rows"), list):
                out.append(data)
    return out


def run_shorts(args, only_isins: Optional[List[str]] = None, include_history_for: Optional[str] = None) -> Dict:
    warnings: List[str] = []
    cache = _cache(args)
    try:
        cur = osh.fetch("Default", http, cache_dir=cache, offline=args.offline)
        hist = osh.fetch("RssHTable", http, cache_dir=cache, offline=args.offline)
    except http.OfflineMiss:
        raise gpw_archive.GPWFetchError("KNF register not in cache; run once without --offline")
    except http.HTTPFetchError as exc:
        raise gpw_archive.GPWFetchError("KNF register unavailable: {}".format(exc))
    except ValueError as exc:
        raise gpw_archive.GPWFetchError(str(exc))
    if not cur["rows"] and not hist["rows"]:
        raise gpw_archive.GPWFetchError("KNF register returned no rows")
    pnl, dates, n_sess, _raw = load_market(args, getattr(args, "sessions", DEFAULT_SESSIONS))
    if not pnl:
        warnings.append("no cached GPW quotes: price changes and days to cover are empty")
    as_of = _as_of_from(dates, args)
    ref = (args.as_of or as_of).isoformat()
    current = cur["rows"]
    history = [r for r in hist["rows"] if r["position_date"] <= ref]
    if args.as_of and current and max(r["position_date"] for r in current) > ref:
        # historical run: rebuild the register as it was (lower bound, see open_at)
        # full history: a disclosure after --as-of proves the pair was still open then
        pairs = osh.pair_histories(hist["rows"], cur["rows"])
        keys_now = {osh._key(r) for r in current}
        current = list(osh.open_at(pairs, keys_now, ref).values())
        warnings.append("historical run: register reconstructed from KNF history (closed positions without a later disclosure are not counted)")
    m, rev = isin_tables(args)
    tickers = set(pnl) | set(m)
    canon = make_canon(m, rev, ref)

    def ticker_of(isin, name):
        return canon(isin=isin) or canon(osh.match_issuer_name(name, tickers))

    state = _state(args)
    snaps = [s for s in _snapshots(state) if s["date"] < ref]
    prev = snaps[-1] if snaps else None
    d20 = osh.session_before(dates, ref, osh.CHANGE_WINDOW_SESSIONS) if dates else None
    snap20 = None
    if d20:
        cands = [s for s in _snapshots(state) if s["date"] <= d20 and
                 (dt.date.fromisoformat(d20) - dt.date.fromisoformat(s["date"])).days <= 5]
        snap20 = cands[-1] if cands else None
    agg = osh.aggregate(current, history, ref, ticker_of=ticker_of, panel=pnl,
                        shares_of=_shares_lookup(args, warnings), session_dates=dates,
                        since=args.since.isoformat() if getattr(args, "since", None) else None,
                        prev_snapshot=prev, snapshot_20s=snap20)
    if not args.as_of and not args.offline and cur["rows"]:
        _write_json(state / "ownership" / "shorts" / "register_{}.json".format(ref),
                    {"date": ref, "retrieved_at": cur["retrieved_at"], "rows": cur["rows"]})
    for r in agg["by_issuer"]:
        r["register_name"] = r.get("issuer")  # the KNF register's own issuer name
    for e in agg["events"]:
        e["register_name"] = e.get("issuer")
    sel = set(canon(t) for t in getattr(args, "ticker", []) or [])
    if sel:
        agg["by_issuer"] = [r for r in agg["by_issuer"] if (r["ticker"] or "").upper() in sel]
        agg["events"] = [e for e in agg["events"] if (e.get("ticker") or "").upper() in sel]
    if only_isins is not None:
        agg["by_issuer"] = [r for r in agg["by_issuer"] if r["isin"] in only_isins]
        agg["events"] = [e for e in agg["events"] if e["isin"] in only_isins]
    if include_history_for:
        agg["issuer_history"] = osh.issuer_history(history, current, include_history_for)[-12:]
    agg.update({
        "crowded_rule": {"total_pct_min": osh.CROWDED_TOTAL_PCT, "holders_min": osh.CROWDED_HOLDERS,
                         "public_threshold_pct": osh.PUBLIC_THRESHOLD_PCT, "high_dtc_days": osh.HIGH_DTC_DAYS},
        "register_published_max": max((r.get("published") or "" for r in cur["rows"]), default=None),
        "retrieved_at": cur["retrieved_at"], "history_retrieved_at": hist["retrieved_at"],
        "unmapped_isins": sorted({r["isin"] for r in agg["by_issuer"] if not r["ticker"]}),
    })
    return {"shorts": agg, "as_of": as_of, "sessions": n_sess, "warnings": warnings,
            "sources": ["KNF — rejestr pozycji krótkich netto: {} (JSON {})".format(osh.KNF_PAGE, osh.KNF_URL),
                        "KNF — historia: {}".format(osh.KNF_HIST_PAGE),
                        "GPW — Archiwum notowań: https://www.gpw.pl/archiwum-notowan",
                        "GPW — karta spółki (liczba akcji): https://www.gpw.pl/spolka?isin=<ISIN>"]}


# --------------------------------------------------------------------------
# Insiders
# --------------------------------------------------------------------------

def _store_paths(state: Path):
    d = state / "insiders"
    return d / "transactions.json", d / "notices.json"


def load_store(state: Path) -> List[Dict]:
    p, _ = _store_paths(state)
    data = _read_json(p, {"transactions": []})
    return data.get("transactions", []) if isinstance(data, dict) else []


def save_transactions(state: Path, txs: List[Dict]) -> Dict:
    p, _ = _store_paths(state)
    cur = load_store(state)
    ids = {t.get("id") for t in cur}
    new = [t for t in txs if t.get("id") not in ids]
    cur.extend(new)
    _write_json(p, {"schema": 1, "transactions": cur})
    return {"path": str(p), "new": len(new), "total": len(cur)}


def run_insiders(args, tickers: Optional[List[str]] = None, details: bool = False, max_details: int = 40) -> Dict:
    warnings: List[str] = []
    cache = _cache(args)
    ref = _reference_date(args)
    end = getattr(args, "end", None) or ref
    start = getattr(args, "start", None) or (end - dt.timedelta(days=args.days - 1))
    if start > end:
        raise InputError("--start is after --end")
    try:
        res = espi.fetch_range(start, end, cache_dir=cache, offline=args.offline)
    except http.HTTPFetchError as exc:
        raise gpw_archive.GPWFetchError("ESPI list unavailable: {}".format(exc))
    if res["missing_days"]:
        warnings.append("ESPI days missing from cache (offline): {}".format(", ".join(res["missing_days"])))
    if not res["days"]:
        raise gpw_archive.GPWFetchError("no ESPI data for {} .. {}".format(start, end))
    m, rev = isin_tables(args)
    canon = make_canon(m, rev, end.isoformat())
    sel_isins = None
    if tickers:
        sel_isins = set()
        for t in tickers:
            isin = m.get(t.upper()) or m.get(canon(t) or "")
            if not isin:
                try:
                    isin = fundamentals.resolve_isin(t, cache, offline=args.offline)["isin"]
                except fundamentals.FundamentalsError as exc:
                    raise InputError(str(exc))
            sel_isins.add(isin)
    rows = res["rows"]
    report_dates: Dict[str, List[str]] = {}
    for r in rows:
        if r["class"] in ("periodic_q", "periodic_h", "periodic_y") and r.get("isin"):
            report_dates.setdefault(canon(isin=r["isin"]) or r["isin"], []).append(r["date"])
    cand = [r for r in rows if r["class"] == "insider"]
    if sel_isins is not None:
        cand = [r for r in rows if r.get("isin") in sel_isins and (r["class"] == "insider" or details)]
    fetched = 0
    notices = []
    for r in cand:
        body = None
        if details and fetched < max_details and (r["class"] in ("insider", "other")):
            try:
                body = espi.fetch_report(r["geru_id"], cache_dir=cache, offline=args.offline)
                fetched += 1
            except http.OfflineMiss:
                body = None
            except http.HTTPFetchError as exc:
                warnings.append("report {} not readable: {}".format(r["geru_id"], exc))
            if body is not None and r["class"] != "insider" and (
                    body.get("class") != "insider" or
                    not oi.mentions_managers_transaction((r.get("title") or "") + " " + (body.get("body_text") or ""))):
                continue  # not a managers' transaction (ESAP TRANSD alone is not trusted)
        elif r["class"] != "insider":
            continue
        n = oi.notice_from_row(r, body)
        n["ticker"] = canon(isin=r.get("isin")) if r.get("isin") else None
        n["register_name"] = r.get("company")  # the ESPI list's own company name
        notices.append(n)
    if details and len([r for r in cand]) > max_details:
        warnings.append("only {} report pages read (--max-details)".format(max_details))
    state = _state(args)
    _, npath = _store_paths(state)
    if not args.as_of:
        stored = _read_json(npath, {"notices": []}).get("notices", [])
        seen = {x.get("geru_id"): i for i, x in enumerate(stored)}
        for n in notices:
            if n["geru_id"] in seen:
                if n["details_fetched"]:
                    stored[seen[n["geru_id"]]] = n
            else:
                stored.append(n)
        _write_json(npath, {"schema": 1, "notices": stored})
    store = load_store(state)
    s_iso, e_iso = start.isoformat(), end.isoformat()
    txs = [dict(t) for t in store if t.get("date") and s_iso <= t["date"] <= e_iso]
    for t in txs:
        if t.get("isin") and canon(isin=t["isin"]):
            t["ticker"] = canon(isin=t["isin"])
        elif t.get("ticker"):
            t["ticker"] = canon(t["ticker"], date=t.get("date"))  # stored before a rename
    if sel_isins is not None:
        sel_t = {canon(t) for t in tickers}
        txs = [t for t in txs if (t.get("ticker") or "").upper() in sel_t or t.get("isin") in sel_isins]
    by = oi.aggregate(notices, txs, e_iso, report_dates=report_dates, history=store)
    for r in by:
        r["register_name"] = r.get("company")
    pnl_dates = [d["date"] for d in res["days"]]
    return {"insiders": {"by_ticker": by, "start": s_iso, "end": e_iso, "notices_total": len(notices),
                         "companies": len(by), "report_pages_read": fetched,
                         "espi_days": len(res["days"]), "store": str(_store_paths(state)[0]),
                         "parsed_transactions_in_period": len(txs),
                         "rules": {"cluster_min_insiders": oi.CLUSTER_MIN_INSIDERS, "cluster_window_days": oi.CLUSTER_WINDOW_DAYS,
                                   "closed_period_days": oi.CLOSED_PERIOD_DAYS, "mar_threshold_eur": 20000}},
            "as_of": dt.date.fromisoformat(max(pnl_dates)) if pnl_dates else end,
            "warnings": warnings,
            "sources": ["GPW — komunikaty ESPI/EBI: https://www.gpw.pl/komunikaty (POST ajaxindex.php action=GPWEspiReportUnion)",
                        "GPW — raport: https://www.gpw.pl/komunikat?geru_id=<N> (metadane ESAP TRANSD)",
                        "MAR art. 19 — Rozporządzenie (UE) 596/2014; próg 20 000 EUR: Rozporządzenie (UE) 2024/2809 (Listing Act); "
                        "ESMA70-154-1020 (19.01.2026)"]}


def run_parse(args) -> Dict:
    if args.file == "-":
        text = sys.stdin.read()
    else:
        p = Path(args.file).expanduser()
        if not p.is_file():
            raise InputError("file not found: {}".format(p))
        raw = p.read_bytes()[:MAX_INPUT_BYTES]
        if raw[:5] == b"%PDF-":
            try:
                text = opdf.extract_text(raw)
            except opdf.PDFTextError as exc:
                raise InputError("PDF not readable: {}".format(exc))
        else:
            text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        raise InputError("empty input")
    txs = oi.parse_notification(text)
    m, rev = isin_tables(args)
    canon = make_canon(m, rev, _reference_date(args).isoformat())
    for t in txs:
        t["register_name"] = t.get("issuer")
        t["ticker"] = (canon(isin=t.get("isin")) if t.get("isin") else None) or \
            (canon(args.ticker, date=t.get("date")) if args.ticker else None)
        if not t["ticker"] and t.get("issuer"):
            tk, _how = of.match_ticker(t["issuer"], set(m))
            t["ticker"] = canon(tk, date=t.get("date")) if tk else None
        t["source"] = "user_paste"
        t["parsed_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    store = None
    if txs and not args.no_store:
        store = save_transactions(_state(args), txs)
    return {"parse": {"transactions": txs, "store": store}, "as_of": max((t["date"] for t in txs if t.get("date")), default=None),
            "warnings": [w for t in txs for w in t.get("warnings", [])],
            "sources": ["User-supplied notification text (ESMA template, Implementing Regulation (EU) 2016/523)"]}


# --------------------------------------------------------------------------
# Funds
# --------------------------------------------------------------------------

def _fetch_fund_pdf(code: str, year: int, args) -> bytes:
    """Polite download of a binary PDF through ``gpwlib.http.get_bytes`` (same
    rate limiter, User-Agent, retries and on-disk cache as every other source;
    the cache keeps the exact bytes). A copy under <cache>/ownership/funds/
    written by older versions is still read first."""
    if not re.fullmatch(r"[A-Z0-9]{3,12}", code):
        raise InputError("bad fund code {!r}".format(code))
    base = _cache(args) or gpw_archive.default_cache_dir()
    legacy = Path(base) / "ownership" / "funds" / "{}_{}.pdf".format(code, year)
    if legacy.exists():
        return legacy.read_bytes()
    url = FUNDS_URL.format(code=code, date="{}-12-31".format(year))
    try:
        # a year-end portfolio report never changes once published
        raw = http.get_bytes(url, cache_dir=base, ttl_seconds=None, offline=bool(args.offline)).content
    except http.OfflineMiss:
        raise gpw_archive.GPWFetchError("fund report {} {} not cached (offline)".format(code, year))
    except http.HTTPFetchError as exc:
        raise gpw_archive.GPWFetchError("analizy.pl: {}".format(exc))
    if raw[:5] != b"%PDF-":
        http.drop_cached(url, cache_dir=base)  # do not keep an error page forever
        raise gpw_archive.GPWFetchError("analizy.pl returned no PDF for {} {} (wrong code or year?)".format(code, year))
    return raw


def parse_fund_bytes(raw: bytes, name: str, include_nc: bool) -> Dict:
    low = name.lower()
    if raw[:5] == b"%PDF-":
        try:
            pages = opdf.extract_lines(raw)
        except opdf.PDFTextError as exc:
            raise InputError("{}: {}".format(name, exc))
        res = of.parse_lines([ln for p in pages for ln in p], include_nc)
        if not any(pages):
            raise InputError("{}: no text in PDF (scanned?) - export the table to CSV/XLSX".format(name))
    elif raw[:2] == b"PK" or low.endswith(".xlsx"):
        try:
            res = of.parse_table(of.xlsx_rows(raw), include_nc)
        except (of.FundsParseError, Exception) as exc:
            raise InputError("{}: {}".format(name, exc))
    else:
        text = raw.decode("utf-8-sig", errors="replace")
        if "�" in text:
            text = raw.decode("cp1250", errors="replace")
        res = of.parse_table(of.csv_rows(text), include_nc)
    res["file"] = name
    return res


def _year_end_close(sessions: List[Dict], vdate: str) -> Dict[str, float]:
    """Raw (unadjusted) close per ticker on the last session <= valuation date."""
    best = None
    for s in sessions:
        if s["date"] <= vdate:
            best = s
    if best is None:
        return {}
    return {r["name"]: r["close"] for r in best["rows"] if r.get("close") and (r.get("currency") in (None, "", "PLN"))}


def _split_factors(pnl, d0: str, d1: str) -> Dict[str, float]:
    out = {}
    for name, s in pnl.items():
        f = 1.0
        for a in s.adjustments:
            if d0 < a["date"] <= d1 and abs(a["factor"] - 1) > of.SPLIT_FACTOR_DEV:
                f *= a["factor"]
        if f != 1.0:
            out[name] = f  # panel multiplies old prices by f (1:10 split -> 0.1)
    return out


def _espi_names(cache: Optional[Path], rev: Dict[str, str], limit: int = 400) -> Dict[str, str]:
    """Full company names seen in locally cached ESPI lists -> ticker."""
    base = Path(cache or gpw_archive.default_cache_dir()) / "http" / "www.gpw.pl"
    out: Dict[str, str] = {}
    if not base.exists():
        return out
    files = sorted(base.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    for p in files:
        data = _read_json(p, None)
        if not isinstance(data, dict) or "GPWEspiReportUnion" not in json.dumps(data.get("form", "")):
            continue
        for r in espi.parse_list_html(data.get("text", "")):
            if r.get("isin") and r["isin"] in rev:
                out[r["company"]] = rev[r["isin"]]
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "fund").lower()).strip("_")[:60] or "fund"


def run_funds(args) -> Dict:
    warnings: List[str] = []
    cache = _cache(args)
    parsed: List[Dict] = []
    for fpath in args.file:
        p = Path(fpath).expanduser()
        if not p.is_file():
            raise InputError("file not found: {}".format(p))
        parsed.append(parse_fund_bytes(p.read_bytes()[:MAX_INPUT_BYTES], p.name, args.include_newconnect))
    years = args.year or [(_reference_date(args).year - 1)]
    for code in args.fetch_code:
        for y in years:
            raw = _fetch_fund_pdf(code.upper(), y, args)
            res = parse_fund_bytes(raw, "{}_{}-12-31.pdf".format(code.upper(), y), args.include_newconnect)
            res["source_url"] = FUNDS_URL.format(code=code.upper(), date="{}-12-31".format(y))
            res["fund_code"] = code.upper()
            parsed.append(res)
    for r in parsed:
        if not r["section_found"]:
            warnings.append("{}: Polish-equity section not found - check the file".format(r["file"]))
        if not r["holdings"]:
            warnings.append("{}: no holdings parsed".format(r["file"]))
        if not r["valuation_date"]:
            warnings.append("{}: valuation date not found; assumed 31 Dec of --year".format(r["file"]))
            r["valuation_date"] = "{}-12-31".format(years[0])
        r["fund"] = r.get("fund") or Path(r["file"]).stem
    name_map = {}
    if args.name_map:
        nm = _read_json(Path(args.name_map).expanduser(), None)
        if not isinstance(nm, dict):
            raise InputError("--name-map must be a JSON object {\"name\": \"TICKER\"}")
        name_map = nm
    # market data covering all valuation dates
    vdates = sorted(r["valuation_date"] for r in parsed)
    need = max(30, (dt.date.today() - dt.date.fromisoformat(vdates[0])).days * 5 // 7 + 30)
    pnl, dates, n_sess, sessions = load_market(args, min(need, 1500))
    if not sessions or (dates and dates[0] > vdates[0]):
        warnings.append("GPW quotes do not reach back to {}: approx shares missing for older files".format(vdates[0]))
    m, rev = isin_tables(args)
    tickers = set(pnl) | set(m)
    espi_names = _espi_names(cache, rev)
    canon = make_canon(m, rev, _reference_date(args).isoformat())
    unmatched = set()
    for r in parsed:
        closes = _year_end_close(sessions, r["valuation_date"])
        for h in r["holdings"]:
            h.setdefault("register_name", h.get("name"))  # the fund report's own name
            if not h.get("ticker"):
                tk, how = of.match_ticker(h["name"], tickers, name_map, espi_names)
                h["ticker"], h["match"] = tk, how
                if not tk:
                    unmatched.add(h["name"])
        # year-end closes are keyed by the name listed on the valuation date:
        # enrich first, then emit the current ticker
        of.enrich(r["holdings"], closes)
        miss = [h["ticker"] for h in r["holdings"] if h.get("ticker") and h.get("approx_shares") is None]
        if miss and closes:
            warnings.append("{}: no close on {} for {} (renamed or listed later?) - shares not estimated".format(
                r["file"], r["valuation_date"], ", ".join(sorted(miss)[:15])))
        for h in r["holdings"]:
            if h.get("ticker"):
                h["ticker"] = canon(h["ticker"], date=r["valuation_date"])
    # store + previous year from the store
    state = _state(args)
    sdir = state / "ownership" / "funds"
    if not args.no_store:
        for r in parsed:
            _write_json(sdir / "{}_{}.json".format(_slug(r["fund"]), r["valuation_date"]),
                        {"fund": r["fund"], "valuation_date": r["valuation_date"], "file": r["file"],
                         "holdings": r["holdings"]})
    by_fund: Dict[str, Dict[str, Dict]] = {}
    for r in parsed:
        by_fund.setdefault(r["fund"], {})[r["valuation_date"]] = r
    if sdir.exists():
        for p in sdir.glob("*.json"):
            d = _read_json(p, None)
            if isinstance(d, dict) and d.get("fund") in by_fund and d.get("valuation_date") not in by_fund[d["fund"]]:
                by_fund[d["fund"]][d["valuation_date"]] = d
    latest, comps, fund_rows = [], {}, []
    for fund, versions in by_fund.items():
        ds = sorted(versions)
        cur = versions[ds[-1]]
        latest.append(cur)
        prev = versions[ds[-2]] if len(ds) > 1 else None
        if prev:
            comps[fund] = of.compare(cur["holdings"], prev["holdings"], _split_factors(pnl, prev["valuation_date"], cur["valuation_date"]))
        fund_rows.append({"fund": fund, "valuation_date": cur["valuation_date"], "previous": prev["valuation_date"] if prev else None,
                          "holdings": len(cur["holdings"]), "matched": sum(1 for h in cur["holdings"] if h.get("ticker")),
                          "polish_equity_pln": round(sum(h["value_pln"] or 0 for h in cur["holdings"]), 2),
                          "file": cur.get("file"), "source_url": cur.get("source_url")})
    for cur in latest:  # files stored by older versions may carry pre-rename tickers
        for h in cur.get("holdings") or []:
            if h.get("ticker"):
                h["ticker"] = canon(h["ticker"], date=cur.get("valuation_date"))
    agg = of.aggregate(latest, comps)
    sel = {canon(t) for t in args.ticker}
    if sel:
        agg["by_ticker"] = [r for r in agg["by_ticker"] if r["ticker"] in sel]
    if not comps:
        warnings.append("no previous year for any fund: year-over-year fields are empty")
    as_of = dt.date.fromisoformat(max(vdates))
    srcs = ["Roczna struktura aktywów OFE/PPK (art. 194 ustawy o organizacji i funkcjonowaniu funduszy emerytalnych) — pliki: " +
            ", ".join(r["file"] for r in parsed)]
    srcs += [r["source_url"] for r in fund_rows if r.get("source_url")]
    srcs.append("GPW — Archiwum notowań (kurs z końca roku): https://www.gpw.pl/archiwum-notowan")
    return {"funds": dict(agg, funds=fund_rows, unmatched=sorted(unmatched),
                          rules={"flow_min_rel": of.FLOW_MIN_REL, "flow_min_pln": of.FLOW_MIN_PLN,
                                 "accumulation_min_rel": of.ACCUM_MIN_REL, "accumulation_min_funds": of.ACCUM_MIN_FUNDS}),
            "as_of": as_of, "sessions": n_sess, "warnings": warnings, "sources": srcs}


def stored_funds_for(state: Path, ticker: str, canon=None) -> Dict:
    sdir = state / "ownership" / "funds"
    by_fund: Dict[str, Dict] = {}
    if sdir.exists():
        for p in sdir.glob("*.json"):
            d = _read_json(p, None)
            if isinstance(d, dict) and d.get("fund"):
                by_fund.setdefault(d["fund"], {})[d["valuation_date"]] = d
    if canon is not None:  # stored files may carry pre-rename tickers
        for v in by_fund.values():
            for d in v.values():
                for h in d.get("holdings") or []:
                    if h.get("ticker"):
                        h["ticker"] = canon(h["ticker"], date=d.get("valuation_date"))
    latest, comps = [], {}
    for fund, v in by_fund.items():
        ds = sorted(v)
        latest.append(v[ds[-1]])
        if len(ds) > 1:
            comps[fund] = of.compare(v[ds[-1]]["holdings"], v[ds[-2]]["holdings"], {})
    agg = of.aggregate(latest, comps)
    return {"by_ticker": [r for r in agg["by_ticker"] if r["ticker"] == ticker.upper()],
            "funds_in_store": len(by_fund)}


# --------------------------------------------------------------------------
# Signals
# --------------------------------------------------------------------------

def build_signals(result: Dict) -> List[Dict]:
    sig = []
    sh = result.get("shorts") or {}
    as_of = result.get("as_of")
    for r in sh.get("by_issuer", []):
        if r["crowded"] and r["ticker"]:
            # score: linear crowding intensity, 4% disclosed = 100 (2x the crowded threshold)
            sig.append({"ticker": r["ticker"], "date": as_of, "kind": "short_crowded", "direction": "avoid",
                        "score": round(min(100.0, 25.0 * r["total_pct"]), 1), "source": SKILL})
    for e in sh.get("events", []):
        if e["type"] == "new_position" and e.get("ticker"):
            sig.append({"ticker": e["ticker"], "date": e["date"], "kind": "short_new", "direction": "avoid",
                        "score": round(min(100.0, 25.0 * e["pct"]), 1), "source": SKILL})
    for r in (result.get("insiders") or {}).get("by_ticker", []):
        if not r.get("ticker"):
            continue
        if r["cluster_buy"]:
            sig.append({"ticker": r["ticker"], "date": r["last_date"], "kind": "insider_cluster_buy", "direction": "long",
                        "score": min(100, 20 * r["max_distinct_buyers_30d"]), "source": SKILL})
        if r["cluster_sell"]:
            sig.append({"ticker": r["ticker"], "date": r["last_date"], "kind": "insider_cluster_sell", "direction": "exit",
                        "score": min(100, 20 * r["max_distinct_sellers_30d"]), "source": SKILL})
    fd = result.get("funds") or {}
    vd = max((f["valuation_date"] for f in fd.get("funds", [])), default=None)
    for r in fd.get("by_ticker", []):
        if r.get("fund_accumulation"):
            sig.append({"ticker": r["ticker"], "date": vd, "kind": "fund_accumulation", "direction": "long",
                        "score": round(min(100.0, 100.0 * r["implied_flow_rel"]), 1), "source": SKILL})
    return sig


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

def _yn(t, v):
    return t("yes") if v else t("no")


def md_shorts(sh: Dict, t, lang: str, L: List[str], ticker_mode: bool = False) -> None:
    rows = sh["by_issuer"]
    crowded = [r["ticker"] or r["issuer"] for r in rows if r["crowded"]]
    if not ticker_mode:
        L += ["## " + t("summary"), "", "- " + t("sh_summary", p=sum(r["holders"] for r in rows), i=len(rows),
                                                 c=", ".join(crowded) or t("none")),
              "- " + t("sh_invisible"), ""]
    elif not rows:
        L += ["- " + t("sh_none_ticker"), "- " + t("sh_invisible"), ""]
    if rows:
        L += ["| {} | {} | {} | {} | {} | {} | {} | {} |".format(t("col_ticker"), t("col_total"), t("col_holders"), t("col_ch20"),
                                                               t("col_days"), t("col_dtc"), t("col_px"), t("col_crowded")),
              "|---|---|---|---|---|---|---|---|"]
        for r in rows:
            L.append("| {} | {} | {} | {} pp | {} | {} | {} | {} |".format(
                r["ticker"] or r["issuer"], pct(r["total_pct"], 2, lang), r["holders"],
                ("+" if r["change_20s_pp"] > 0 else "") + num(r["change_20s_pp"], 2, lang), r["days_in_register"],
                num(r["days_to_cover"], 1, lang) if r["days_to_cover"] is not None else "—",
                pct(r["price_change_since_first_pct"], 1, lang, signed=True), _yn(t, r["crowded"])))
        L += ["", "_" + t("crowded_def", t=num(sh["crowded_rule"]["total_pct_min"], 1, lang), h=sh["crowded_rule"]["holders_min"]) +
              " " + t("dtc_note") + "_", ""]
        L += ["### " + t("holders_detail"), "", "| {} | {} | {} | {} | {} |".format(t("col_ticker"), t("col_holder"), "%", t("col_date"), t("col_first")),
              "|---|---|---|---|---|"]
        for r in rows:
            for h in r["holder_list"]:
                L.append("| {} | {} | {} | {} | {} |".format(r["ticker"] or r["issuer"], h["holder"], pct(h["pct"], 2, lang),
                                                          h["position_date"], h["first_disclosed"]))
        L.append("")
    if sh.get("issuer_history"):
        L += ["### " + t("history"), "", "| {} | {} | {} |".format(t("col_date"), t("col_total"), t("col_holders")), "|---|---|---|"]
        L += ["| {} | {} | {} |".format(x["date"], pct(x["total_pct"], 2, lang), x["holders"]) for x in sh["issuer_history"]]
        L.append("")
    if sh["events"]:
        L += ["### " + t("events"), ""]
        for e in sh["events"][:30]:
            who = "{} — {}".format(e.get("ticker") or e["issuer"], e["holder"])
            if e["type"] == "new_position":
                L.append("- {} {}: {} ({})".format(e["date"], who, t("ev_new"), pct(e["pct"], 2, lang)))
            else:
                L.append("- {}: {}".format(who, t("ev_closed", d=e["last_disclosed"], p=num(e["last_pct"], 2, lang))))
        L.append("")


def md_insiders(ins: Dict, t, lang: str, L: List[str], state_hint: str) -> None:
    rows = ins["by_ticker"]
    L += ["## " + t("summary"), "", "- " + t("ins_summary", n=ins["notices_total"], s=ins["start"], e=ins["end"], c=len(rows)),
          "- " + t("ins_threshold"), ""]
    if rows:
        L += ["| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            t("col_ticker"), t("col_buys"), t("col_sells"), t("col_insiders"), t("col_net"), t("col_cluster"),
            t("col_last"), t("col_basis"), t("col_flags")), "|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            cl = "buy" if r["cluster_buy"] else "sell" if r["cluster_sell"] else "—"
            L.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                r["ticker"] or r["company"], r["buys"], r["sells"], r["distinct_insiders"],
                pln(r["net_value_pln"], 0, lang) if r["net_value_pln"] is not None else "—", cl, r["last_date"] or "—",
                t("basis_" + r["basis"]), ", ".join(r["flags"]) or "—"))
        L.append("")
        L += ["### " + t("notices"), "", "| {} | {} | {} | {} |".format(t("col_date"), t("col_ticker"), t("col_dir"), t("col_title")),
              "|---|---|---|---|"]
        for r in rows:
            for n in r["notice_list"][:10]:
                L.append("| {} | {} | {} | [{}]({}) |".format(n["date"], r["ticker"] or r["company"], n["direction_hint"] or "?",
                                                            (n["title"] or "")[:90].replace("|", "/"), n["url"]))
        L += ["", "_" + t("how_details", d=state_hint) + "_", ""]


def md_parse(res: Dict, t, lang: str, L: List[str]) -> None:
    txs = res["transactions"]
    if not txs:
        L += [t("parse_none"), ""]
        return
    L += ["| {} | {} | {} | {} | {} | {} | {} | {} |".format(t("col_date"), t("col_person"), t("col_position"), t("col_nature"),
                                                           t("col_volume"), t("col_price"), t("col_value"), t("col_place")),
          "|---|---|---|---|---|---|---|---|"]
    for x in txs:
        L.append("| {} | {}{} | {} | {}{} | {} | {} | {} | {} |".format(
            x["date"] or "—", x["person"] or "—", " (entity)" if x["entity"] else "", (x["position"] or "—")[:60],
            x["nature"], " (excluded)" if x["excluded"] else "", num(x["volume"], 0, lang) if x["volume"] else "—",
            num(x["avg_price"], 4, lang) if x["avg_price"] else "—",
            (pln(x["value"], 2, lang) if x.get("currency") in (None, "PLN") else "{} {}".format(num(x["value"], 2, lang), x["currency"])) if x["value"] else "—",
            x["place_kind"] or "—"))
    L += ["", "_" + t("excluded_note") + "_", ""]
    if res.get("store"):
        L += [t("stored", n=res["store"]["new"], p=res["store"]["path"], t=res["store"]["total"]), ""]


def md_funds(fd: Dict, t, lang: str, L: List[str], ticker_mode: bool = False) -> None:
    rows = fd["by_ticker"]
    if not ticker_mode:
        L += ["## " + t("summary"), "", "- " + t("fd_summary", f=len(fd["funds"]), h=sum(f["holdings"] for f in fd["funds"]),
                                                 m=sum(f["matched"] for f in fd["funds"])), "- " + t("fd_approx"), ""]
        L += ["### " + t("funds_files"), ""] + ["- {} — {} ({} / {})".format(f["fund"], f["valuation_date"], f["matched"], f["holdings"]) +
                                                 (" vs {}".format(f["previous"]) if f["previous"] else "") for f in fd["funds"]] + [""]
    elif not rows:
        L += ["- " + t("fd_none_ticker"), ""]
        return
    if rows:
        L += ["| {} | {} | {} | {} | {} | {} |".format(t("col_ticker"), t("col_value"), t("col_pctf"), t("col_shares"),
                                                    t("col_flow"), t("col_funds")), "|---|---|---|---|---|---|"]
        for r in rows[:40]:
            L.append("| {} | {} | {} | {} | {} | {} |".format(
                r["ticker"], pln(r["value_pln"], 0, lang), pct(r["avg_pct_of_fund"], 2, lang),
                num(r["approx_shares"], 0, lang) if r["approx_shares"] else "—",
                pln(r["implied_flow_pln"], 0, lang) if r["implied_flow_pln"] is not None else "—", r["funds_holding"]))
        L.append("")
    if fd.get("top_buys") or fd.get("top_sells"):
        for key in ("top_buys", "top_sells"):
            if fd.get(key):
                L += ["### " + t(key), ""] + ["- {}: {}".format(x["ticker"], pln(x["implied_flow_pln"], 0, lang)) for x in fd[key]] + [""]
    elif not ticker_mode:
        L += ["_" + t("no_prev") + "_", ""]
    if fd.get("unmatched"):
        L += ["### " + t("unmatched"), "", ", ".join(fd["unmatched"]), ""]


def render_markdown(r: Dict, lang: str) -> str:
    t = T(STRINGS, lang)
    mode = r["mode"]
    title = t("title_ticker", t=r.get("ticker")) if mode == "ticker" else t("title_" + mode)
    prov = r.get("data_provenance") or {}
    L = ["# " + title, "", "**{}:** {} · **{}:** {}".format(t("as_of"), r.get("as_of") or "—", t("generated"),
                                                         prov.get("retrieved_at_utc", "—")), ""]
    fr = prov.get("freshness") or {}
    if fr.get("is_stale"):
        L += [t("stale", n=fr.get("sessions_behind")), ""]
    if r.get("warnings"):
        L += ["### " + t("warnings"), ""] + ["- " + w for w in r["warnings"][:20]] + [""]
    if mode == "shorts":
        md_shorts(r["shorts"], t, lang, L)
    elif mode == "insiders":
        md_insiders(r["insiders"], t, lang, L, r["insiders"]["store"])
    elif mode == "parse":
        md_parse(r["parse"], t, lang, L)
    elif mode == "funds":
        md_funds(r["funds"], t, lang, L)
    elif mode == "ticker":
        L += ["## " + t("title_shorts"), ""]
        md_shorts(r["shorts"], t, lang, L, ticker_mode=True)
        L += ["## " + t("title_insiders"), ""]
        md_insiders(r["insiders"], t, lang, L, r["insiders"]["store"])
        L += ["## " + t("title_funds"), ""]
        md_funds(r["funds"], t, lang, L, ticker_mode=True)
    if r.get("signals"):
        L += ["### " + t("signals"), ""] + ["- {} {} {} ({}, score {})".format(s["date"], s["ticker"], s["kind"], s["direction"], s.get("score"))
                                             for s in r["signals"]] + [""]
    L += ["## " + t("sources"), ""] + ["- " + s for s in prov.get("sources", r.get("sources", []))]
    L += ["", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def run(args) -> Dict:
    out: Dict = {"skill": SKILL, "version": VERSION, "lang": args.lang, "mode": args.mode}
    sources: List[str] = []
    warnings: List[str] = []
    n_sess = 0
    if args.mode == "shorts":
        res = run_shorts(args)
        out["shorts"] = res["shorts"]
        as_of, n_sess = res["as_of"], res["sessions"]
    elif args.mode == "insiders":
        res = run_insiders(args, tickers=args.ticker or None, details=args.details or bool(args.ticker),
                           max_details=args.max_details)
        out["insiders"] = res["insiders"]
        as_of = res["as_of"]
    elif args.mode == "parse":
        res = run_parse(args)
        out["parse"] = res["parse"]
        as_of = dt.date.fromisoformat(res["as_of"]) if res["as_of"] else _reference_date(args)
    elif args.mode == "funds":
        res = run_funds(args)
        out["funds"] = res["funds"]
        as_of, n_sess = res["as_of"], res["sessions"]
    else:  # ticker
        requested = args.symbol.upper()
        try:
            isin = fundamentals.resolve_isin(requested, _cache(args), offline=args.offline)["isin"]
        except fundamentals.FundamentalsError as exc:
            raise InputError(str(exc))
        m, rev = isin_tables(args)
        canon = make_canon(m, rev, _reference_date(args).isoformat())
        sym = canon(requested, isin=isin) or requested  # CCC -> MODIVO
        args.since, args.ticker = None, []
        r1 = run_shorts(args, only_isins=[isin], include_history_for=isin)
        r2 = run_insiders(args, tickers=[sym], details=args.details, max_details=args.max_details)
        out.update({"ticker": sym, "isin": isin, "shorts": r1["shorts"], "insiders": r2["insiders"],
                    "funds": stored_funds_for(_state(args), sym, canon)})
        if sym != requested:
            out["requested_ticker"] = requested
        res = {"warnings": r1["warnings"] + r2["warnings"], "sources": r1["sources"] + r2["sources"]}
        as_of, n_sess = r1["as_of"], r1["sessions"]
    warnings += res["warnings"]
    sources += res["sources"]
    out["as_of"] = as_of.isoformat()
    out["signals"] = build_signals(out)
    out["warnings"] = warnings
    notes = ["KNF register shows only net short positions >= 0.5% (SSR art. 6); totals are a lower bound.",
             "Insider volumes/prices only from user-pasted notification text; never estimated."]
    if args.mode == "funds":
        notes = ["OFE/PPK holdings: value and % only; share counts and flows are approximations (value / year-end close)."]
    out["data_provenance"] = _prov(as_of, sources, args, n_sess, notes=notes)
    if args.mode in ("parse", "funds"):
        # not daily market data: freshness is about the document date
        out["data_provenance"]["freshness"]["is_stale"] = False
        out["data_provenance"]["notes"].append("Freshness gate not applied: user-supplied documents with their own dates.")
    return out


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except (gpw_archive.GPWFetchError, InputError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    md = render_markdown(result, args.lang)
    if args.no_files:
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else md)
        return 0
    out = Path(args.output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    prefix = "gpw_ownership_{}".format(args.mode)
    jp, mp = out / "{}_{}.json".format(prefix, stamp), out / "{}_{}.md".format(prefix, stamp)
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(md, encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else md)
    print("JSON: {}\nMarkdown: {}".format(jp, mp), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
