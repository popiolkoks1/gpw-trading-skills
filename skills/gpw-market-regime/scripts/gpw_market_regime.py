#!/usr/bin/env python3
"""GPW Market Regime - CLI.

Distribution days, follow-through days, trend context and a five-state
market regime (IBD / O'Neil method adapted to GPW) for WIG, WIG20, mWIG40
and sWIG80, plus a 0-100 top-risk score. Writes JSON + Markdown (PL/EN).

    python3 scripts/gpw_market_regime.py --lang pl
    python3 scripts/gpw_market_regime.py --offline --breadth-json gpw_breadth_....json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import gpw_archive, panel, provenance  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct  # noqa: E402

import regime_engine as rg  # noqa: E402

VERSION = "1.0.0"
INDICES = ["WIG", "WIG20", "MWIG40", "SWIG80"]
LABELS = {"WIG": "WIG", "WIG20": "WIG20", "MWIG40": "mWIG40", "SWIG80": "sWIG80"}
STALE_INPUT_SESSIONS = 3  # brief: warn when an input JSON is > 3 sessions older than the data

STRINGS = {
    "title": {"pl": "Reżim rynku GPW — raport", "en": "GPW Market Regime Report"},
    "as_of": {"pl": "Dane na sesję", "en": "Data as of session"},
    "generated": {"pl": "Wygenerowano", "en": "Generated"},
    "stale": {"pl": "⚠️ UWAGA: dane są nieaktualne o {n} sesji — uruchom bez --offline, aby je odświeżyć.",
              "en": "⚠️ WARNING: data is {n} session(s) old — run without --offline to refresh."},
    "answer": {"pl": "Odpowiedź", "en": "Answer"},
    "regime_line": {"pl": "**Reżim ({idx}): {state}** · ryzyko szczytu **{risk}/100** ({band}) · dni dystrybucji: **{dd}** ({ddband})",
                    "en": "**Regime ({idx}): {state}** · top risk **{risk}/100** ({band}) · distribution days: **{dd}** ({ddband})"},
    "state_confirmed_uptrend": {"pl": "potwierdzony trend wzrostowy", "en": "confirmed uptrend"},
    "state_uptrend_under_pressure": {"pl": "trend wzrostowy pod presją", "en": "uptrend under pressure"},
    "state_rally_attempt": {"pl": "próba odbicia", "en": "rally attempt"},
    "state_correction": {"pl": "korekta", "en": "correction"},
    "state_bear": {"pl": "bessa", "en": "bear market"},
    "explain_confirmed_uptrend": {
        "pl": "Indeks jest w trendzie wzrostowym, a sprzedaż instytucji (dni dystrybucji) jest umiarkowana. To środowisko, w którym metoda O'Neila dopuszcza normalną aktywność.",
        "en": "The index is trending up and institutional selling (distribution days) is moderate. This is the environment in which O'Neil's method allows normal activity."},
    "explain_uptrend_under_pressure": {
        "pl": "Trend wzrostowy trwa, ale pojawiły się sygnały ostrzegawcze: dużo dni dystrybucji albo kurs poniżej średniej 50-sesyjnej. W metodzie IBD to moment na ostrożność z nowymi pozycjami.",
        "en": "The uptrend is intact but warning signs appeared: many distribution days or the index below its 50-session average. In the IBD method this is a time for caution with new positions."},
    "explain_rally_attempt": {
        "pl": "Po spadku indeks próbuje odbić, ale odbicie nie zostało jeszcze potwierdzone dniem follow-through (silny wzrost na wyższym obrocie od 4. dnia próby). Wiele prób odbicia kończy się niepowodzeniem.",
        "en": "After a decline the index is trying to bounce, but the bounce has not been confirmed by a follow-through day (a strong gain on higher turnover from day 4 of the attempt). Many rally attempts fail."},
    "explain_correction": {
        "pl": "Indeks jest w korekcie: spadł wyraźnie od szczytu lub przebił średnią 50-sesyjną przy silnej dystrybucji. Nowy trend wzrostowy potwierdza dopiero dzień follow-through.",
        "en": "The index is in a correction: it fell clearly from its high or broke its 50-session average with heavy distribution. Only a follow-through day confirms a new uptrend."},
    "explain_bear": {
        "pl": "Indeks jest w bessie: spadek od szczytu przekroczył próg bessy albo kurs jest pod opadającą średnią 200-sesyjną, a średnia 50 jest pod 200. To najbardziej defensywne środowisko.",
        "en": "The index is in a bear market: the fall from the high exceeded the bear threshold, or it trades below a falling 200-session average with the 50 under the 200. The most defensive environment."},
    "band_NORMAL": {"pl": "norma", "en": "normal"},
    "band_CAUTION": {"pl": "ostrożność", "en": "caution"},
    "band_HIGH": {"pl": "wysoko", "en": "high"},
    "band_SEVERE": {"pl": "bardzo wysoko", "en": "severe"},
    "risk_low": {"pl": "niskie", "en": "low"},
    "risk_moderate": {"pl": "umiarkowane", "en": "moderate"},
    "risk_elevated": {"pl": "podwyższone", "en": "elevated"},
    "risk_high": {"pl": "wysokie", "en": "high"},
    "risk_very_high": {"pl": "bardzo wysokie", "en": "very high"},
    "why": {"pl": "Dlaczego", "en": "Why"},
    "why_distribution_high": {"pl": "6+ dni dystrybucji w 25 sesjach", "en": "6+ distribution days in 25 sessions"},
    "why_below_sma50": {"pl": "dwa zamknięcia z rzędu poniżej SMA50", "en": "two closes in a row below SMA50"},
    "why_below_ftd_low": {"pl": "zamknięcie poniżej minimum dnia follow-through", "en": "close below the follow-through day's low"},
    "why_not_yet_recovered": {"pl": "sygnały osłabły, ale warunki powrotu (≤4 dni dystrybucji i 2 zamknięcia nad SMA50) jeszcze niespełnione",
                              "en": "warnings eased, but recovery rules (≤4 distribution days and 2 closes above SMA50) not met yet"},
    "why_drawdown_bear": {"pl": "spadek od szczytu ≥ progu bessy", "en": "drawdown ≥ bear threshold"},
    "why_bear_structure": {"pl": "kurs < opadającej SMA200 i SMA50 < SMA200", "en": "close < falling SMA200 and SMA50 < SMA200"},
    "why_recovered": {"pl": "powrót: ≤4 dni dystrybucji i 2 zamknięcia nad SMA50", "en": "recovery: ≤4 distribution days and 2 closes above SMA50"},
    "why_below_sma200": {"pl": "kurs < SMA200", "en": "close < SMA200"},
    "why_sma50_below_sma200": {"pl": "SMA50 < SMA200", "en": "SMA50 < SMA200"},
    "why_sma200_falling": {"pl": "SMA200 opada", "en": "SMA200 falling"},
    "trend_below_sma50": {"pl": "kurs < SMA50", "en": "close < SMA50"},
    "pat_large_caps_lead": {"pl": "prowadzą duże spółki", "en": "large caps lead"},
    "pat_small_caps_lead": {"pl": "prowadzą małe spółki", "en": "small caps lead"},
    "pat_balanced": {"pl": "równowaga", "en": "balanced"},
    "pat_unknown": {"pl": "brak danych", "en": "no data"},
    "why_no_rally_attempt": {"pl": "brak trwającej próby odbicia", "en": "no rally attempt under way"},
    "why_rally": {"pl": "dzień {d} próby odbicia", "en": "day {d} of a rally attempt"},
    "indices": {"pl": "Indeksy", "en": "Indices"},
    "col_index": {"pl": "Indeks", "en": "Index"},
    "col_state": {"pl": "Reżim", "en": "Regime"},
    "col_close": {"pl": "Zamknięcie", "en": "Close"},
    "col_dd": {"pl": "Dni dystr.", "en": "Dist. days"},
    "col_sma50": {"pl": "vs SMA50", "en": "vs SMA50"},
    "col_sma200": {"pl": "vs SMA200", "en": "vs SMA200"},
    "col_slope": {"pl": "Nachylenie SMA200 (20 s.)", "en": "SMA200 slope (20 s.)"},
    "col_draw": {"pl": "Od szczytu 52 tyg.", "en": "From 52-week high"},
    "col_ftd": {"pl": "Ostatni FTD", "en": "Last FTD"},
    "price_only": {"pl": " (bez obrotu!)", "en": " (no turnover!)"},
    "dist_title": {"pl": "Dni dystrybucji — {idx}", "en": "Distribution days — {idx}"},
    "dist_txt": {"pl": "Dzień dystrybucji = spadek indeksu o ≥ 0,2% przy obrocie wyższym niż na poprzedniej sesji. Liczymy 25 sesji; dzień wygasa po 25 sesjach albo gdy indeks zamknie się ≥ 5% powyżej jego zamknięcia.",
                 "en": "Distribution day = index down ≥ 0.2% on higher turnover than the previous session. Counted over 25 sessions; a day expires after 25 sessions or once the index closes ≥ 5% above its close."},
    "col_date": {"pl": "Data", "en": "Date"},
    "col_chg": {"pl": "Zmiana", "en": "Change"},
    "col_left": {"pl": "Wygasa za (sesji)", "en": "Expires in (sessions)"},
    "col_note": {"pl": "Uwaga", "en": "Note"},
    "expiry_note": {"pl": "wygasanie kontraktów — obrót zawyżony", "en": "futures expiry — turnover inflated"},
    "none": {"pl": "brak", "en": "none"},
    "expired_gain": {"pl": "Wygasłe po wzroście ≥ 5%: {d}", "en": "Expired after a ≥ 5% gain: {d}"},
    "ftd_title": {"pl": "Follow-through / próba odbicia — {idx}", "en": "Follow-through / rally attempt — {idx}"},
    "ftd_line": {"pl": "Ostatni dzień follow-through: **{date}** (dzień {day} próby odbicia, +{gain} przy progu {thr}, obrót {turn}). Od FTD: {since} sesji, {gs}, {dds} dni dystrybucji. Stan: **{health}**.",
                 "en": "Last follow-through day: **{date}** (day {day} of the rally attempt, +{gain} vs threshold {thr}, turnover {turn}). Since the FTD: {since} sessions, {gs}, {dds} distribution days. Health: **{health}**."},
    "health_healthy": {"pl": "zdrowy", "en": "healthy"},
    "health_established": {"pl": "trend ugruntowany (FTD > 25 sesji temu)", "en": "established (FTD > 25 sessions ago)"},
    "health_weak": {"pl": "słaby (wczesna dystrybucja)", "en": "weak (early distribution)"},
    "health_under_pressure": {"pl": "pod presją (poniżej minimum dnia FTD)", "en": "under pressure (below the FTD day's low)"},
    "health_failed": {"pl": "nieudany (indeks spadł poniżej dołka)", "en": "failed (index undercut the rally low)"},
    "health_ended": {"pl": "zakończony (później weszliśmy w korektę)", "en": "ended (a correction followed)"},
    "late_note": {"pl": "Uwaga: FTD po 10. dniu próby jest słabszym sygnałem.", "en": "Note: an FTD after day 10 of the attempt is a weaker signal."},
    "attempt_line": {"pl": "Trwa próba odbicia: dzień **{day}** (start {start}, dołek {low} z {lowd}, +{g} od dołka). FTD możliwy od 4. dnia przy wzroście ≥ {thr} i wyższym obrocie; próba upada, jeśli indeks zamknie się poniżej {low}.",
                     "en": "Rally attempt under way: day **{day}** (started {start}, low {low} on {lowd}, +{g} off the low). An FTD is possible from day 4 with a gain ≥ {thr} on higher turnover; the attempt fails on a close below {low}."},
    "no_ftd": {"pl": "Brak dnia follow-through w analizowanej historii.", "en": "No follow-through day in the analysed history."},
    "lead_title": {"pl": "Przywództwo: duże vs małe spółki", "en": "Leadership: large vs small caps"},
    "lead_line": {"pl": "{w} sesji: WIG20 {a}, mWIG40 {m}, sWIG80 {b} (różnica WIG20 − sWIG80: {s} pkt proc.)",
                  "en": "{w} sessions: WIG20 {a}, mWIG40 {m}, sWIG80 {b} (WIG20 − sWIG80 spread: {s} pp)"},
    "pattern_large_caps_lead": {"pl": "Prowadzą duże spółki, małe zostają w tyle — wąski rynek, klasyczny sygnał późnej fazy wzrostów.",
                                "en": "Large caps lead while small caps lag — a narrow market, a classic late-rally warning."},
    "pattern_small_caps_lead": {"pl": "Małe spółki silniejsze od dużych — szeroki apetyt na ryzyko.", "en": "Small caps stronger than large caps — broad risk appetite."},
    "pattern_balanced": {"pl": "Brak wyraźnej rozbieżności.", "en": "No clear divergence."},
    "pattern_unknown": {"pl": "Za krótka historia.", "en": "History too short."},
    "risk_title": {"pl": "Ryzyko szczytu (0–100)", "en": "Top risk (0–100)"},
    "col_comp": {"pl": "Składnik", "en": "Component"},
    "col_w": {"pl": "Waga", "en": "Weight"},
    "col_s": {"pl": "Ryzyko", "en": "Risk"},
    "col_v": {"pl": "Wartość", "en": "Value"},
    "comp_distribution": {"pl": "Dni dystrybucji (WIG)", "en": "Distribution days (WIG)"},
    "comp_trend": {"pl": "Złamania trendu", "en": "Trend breaks"},
    "comp_drawdown": {"pl": "Spadek od szczytu", "en": "Drawdown from high"},
    "comp_leadership": {"pl": "Rozbieżność WIG20 vs sWIG80", "en": "WIG20 vs sWIG80 divergence"},
    "comp_breadth": {"pl": "Szerokość rynku (gpw-market-breadth)", "en": "Market breadth (gpw-market-breadth)"},
    "breadth_missing": {"pl": "Brak pliku gpw_breadth_*.json — składnik szerokości pominięty, wagi przeliczone. Uruchom gpw-market-breadth, aby go dodać.",
                        "en": "No gpw_breadth_*.json — breadth component skipped, weights renormalised. Run gpw-market-breadth to add it."},
    "breadth_used": {"pl": "Szerokość rynku z pliku {f} (dane na {d}).", "en": "Breadth from {f} (data as of {d})."},
    "breadth_stale": {"pl": "⚠️ Plik szerokości rynku jest starszy o {n} sesji niż dane indeksów.", "en": "⚠️ The breadth file is {n} sessions older than the index data."},
    "breadth_future": {"pl": "Plik szerokości rynku jest nowszy niż analizowana sesja — pominięty (brak zaglądania w przyszłość).",
                       "en": "The breadth file is newer than the analysed session — ignored (no look-ahead)."},
    "hist_title": {"pl": "Historia reżimu — {idx}, ostatnie {n} sesji", "en": "Regime history — {idx}, last {n} sessions"},
    "hist_share": {"pl": "Udział czasu", "en": "Share of time"},
    "hist_start": {"pl": "Na początku okna ({d}): {s}.", "en": "At the start of the window ({d}): {s}."},
    "col_from": {"pl": "Z", "en": "From"},
    "col_to": {"pl": "Do", "en": "To"},
    "col_event": {"pl": "Przyczyna", "en": "Trigger"},
    "ev_follow_through_day": {"pl": "dzień follow-through", "en": "follow-through day"},
    "ev_rally_attempt_started": {"pl": "start próby odbicia", "en": "rally attempt started"},
    "ev_rally_attempt_failed": {"pl": "próba odbicia nieudana", "en": "rally attempt failed"},
    "ev_drawdown": {"pl": "spadek od szczytu ≥ progu korekty", "en": "drawdown ≥ correction threshold"},
    "ev_distribution_below_sma50": {"pl": "6+ dni dystrybucji i kurs < SMA50", "en": "6+ distribution days and close < SMA50"},
    "ev_ftd_failed": {"pl": "FTD nieudany (poniżej dołka)", "en": "FTD failed (below rally low)"},
    "ev_new_high_without_ftd": {"pl": "nowy szczyt 52 tyg. bez FTD", "en": "new 52-week high without an FTD"},
    "ev_init_uptrend": {"pl": "start modelu", "en": "model start"},
    "ev_init_correction": {"pl": "start modelu", "en": "model start"},
    "thr_title": {"pl": "Progi dopasowane do zmienności", "en": "Volatility-adjusted thresholds"},
    "thr_txt": {"pl": "Dzienna zmienność {idx}: {s} (S&P 500 ≈ 1,10%), współczynnik {r}. Próg FTD: {f} (USA: 1,25–1,7%), korekta: spadek ≥ {c}, bessa: ≥ {b} od szczytu 52 tyg.",
                "en": "{idx} daily volatility: {s} (S&P 500 ≈ 1.10%), ratio {r}. FTD threshold: {f} (US: 1.25–1.7%), correction: drawdown ≥ {c}, bear: ≥ {b} from the 52-week high."},
    "caveats": {"pl": "Zastrzeżenia", "en": "Caveats"},
    "cav_us": {"pl": "Reguły IBD/O'Neila powstały dla indeksów amerykańskich. GPW jest płytsza: kilka dużych spółek (PKO BP, Orlen, PZU, banki) dominuje obrót WIG i WIG20, więc pojedyncze sesje mogą zniekształcać sygnały.",
               "en": "The IBD/O'Neil rules were designed for US indices. GPW is thinner: a few large companies (PKO BP, Orlen, PZU, banks) dominate WIG and WIG20 turnover, so single sessions can distort signals."},
    "cav_turnover": {"pl": "Obrót indeksu to suma obrotów jego spółek z archiwum GPW; dni wygasania kontraktów (3. piątek marca, czerwca, września, grudnia) i rewizje indeksów zawyżają obrót — takie dni są oznaczone.",
                     "en": "Index turnover is the sum of its members' turnover from the GPW archive; futures-expiry days (3rd Friday of Mar/Jun/Sep/Dec) and index reviews inflate it — such days are flagged."},
    "cav_base": {"pl": "Na GPW ok. 19% sesji spełnia definicję dnia dystrybucji czysto statystycznie (~4–5 na 25 sesji), dlatego dopiero 6+ traktujemy jako wyraźny sygnał.",
                 "en": "On GPW ~19% of sessions meet the distribution-day definition by chance (~4–5 per 25 sessions), so only 6+ is treated as a clear signal."},
    "cav_hist": {"pl": "Model ma krótką historię (od {d}); pierwsze sesje to rozgrzewka. To narzędzie kontekstu, nie system transakcyjny.",
                 "en": "The model has a short history (from {d}); the first sessions are a warm-up. It is a context tool, not a trading system."},
    "cav_price_only": {"pl": "⚠️ Dla indeksu {idx} brak obrotu w ponad 20% sesji — reguły wolumenowe wyłączone (tryb tylko-cena), sygnały mniej wiarygodne.",
                       "en": "⚠️ {idx} has no turnover on more than 20% of sessions — volume rules switched off (price-only mode), signals less reliable."},
    "skipped": {"pl": "Pominięto indeks {idx}: {why}", "en": "Skipped index {idx}: {why}"},
    "sources": {"pl": "Źródła", "en": "Sources"},
}


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(description="GPW market regime: distribution days, follow-through days, top risk (PL/EN)")
    p.add_argument("--lang", choices=["pl", "en"], default="pl")
    p.add_argument("--output-dir", default=".")
    p.add_argument("--no-files", action="store_true", help="print Markdown only, write no files")
    p.add_argument("--cache-dir", default=None)
    p.add_argument("--offline", action="store_true", help="use cached data only")
    p.add_argument("--as-of", default=None, help="analyse up to this session YYYY-MM-DD")
    p.add_argument("--sessions", type=int, default=480, help="index sessions to load (min 260)")
    p.add_argument("--index", default="WIG", choices=INDICES, help="primary index for regime.state (default WIG)")
    p.add_argument("--history-sessions", type=int, default=250)
    p.add_argument("--breadth-json", default=None, help="gpw-market-breadth JSON (optional)")
    p.add_argument("--input-dir", default=".", help="auto-discover the newest gpw_breadth_*.json here")
    a = p.parse_args(argv)
    if a.sessions < 260:
        p.error("--sessions must be >= 260 (SMA200 + warm-up)")
    if a.history_sessions < 20:
        p.error("--history-sessions must be >= 20")
    if a.as_of:
        try:
            dt.date.fromisoformat(a.as_of)
        except ValueError:
            p.error("--as-of must be YYYY-MM-DD")
    return a


def find_input(explicit: Optional[str], input_dir: str, pattern: str) -> Optional[Path]:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise gpw_archive.GPWFetchError("File not found: {}".format(path))
        return path
    d = Path(input_dir).expanduser()
    if not d.is_dir():
        return None
    files = sorted(d.glob(pattern))
    return files[-1] if files else None


def load_breadth(path: Optional[Path], dates: List[str]) -> Dict:
    """Read a gpw-market-breadth JSON and check it against the index dates."""
    if path is None:
        return {"used": False, "reason": "missing"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise gpw_archive.GPWFetchError("Cannot read {}: {}".format(path, exc))
    if data.get("skill") != "gpw-market-breadth":
        raise gpw_archive.GPWFetchError("{} is not a gpw-market-breadth JSON".format(path))
    as_of = (data.get("data_provenance") or {}).get("as_of")
    info = {"file": str(path), "as_of": as_of, "used": False}
    if not as_of:
        info["reason"] = "no_as_of"
        return info
    if as_of > dates[-1]:
        info["reason"] = "future"
        return info
    info["sessions_behind"] = sum(1 for d in dates if d > as_of)
    info["stale"] = info["sessions_behind"] > STALE_INPUT_SESSIONS
    info["used"] = True
    info["data"] = data
    return info


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def build_report(sessions: List[Dict], primary: str = "WIG", history_sessions: int = 250,
                 breadth_path: Optional[Path] = None, lang: str = "pl") -> Dict:
    """Pure-ish core used by run() and by tests (sessions = TYPE_INDICES payloads)."""
    analyses, data, skipped = {}, {}, {}
    for name in INDICES:
        s = panel.index_series(sessions, name)
        if len(s) < 120:
            skipped[name] = "only {} sessions".format(len(s))
            continue
        ix = rg.IndexData(name, s.dates, s.close, s.high, s.low, s.turnover_k)
        data[name] = ix
        analyses[name] = rg.analyze_index(ix, history_sessions)
    if primary not in analyses:
        raise gpw_archive.GPWFetchError("Primary index {} unavailable ({}).".format(primary, skipped.get(primary, "missing")))
    ix = data[primary]
    lead = rg.leadership(data.get("WIG20"), data.get("SWIG80"), data.get("MWIG40"))
    breadth = load_breadth(breadth_path, ix.dates)
    risk = rg.top_risk(ix, lead, breadth.get("data") if breadth.get("used") else None)
    a = analyses[primary]
    sm = a.pop("_sm")
    for other in analyses.values():
        other.pop("_sm", None)
    breadth.pop("data", None)
    regime = {
        "state": a["state"], "top_risk_score": risk["score"], "distribution_days": a["distribution"]["count"],
        "as_of": a["as_of"], "index": primary, "top_risk_band": risk["band"],
        "distribution_band": a["distribution"]["band"], "state_reasons": a["state_reasons"],
        "follow_through": a["follow_through"], "rally_attempt": a["rally_attempt"],
        "indices_in_uptrend": sum(1 for x in analyses.values() if x["state"] in ("confirmed_uptrend", "uptrend_under_pressure")),
        "indices_analysed": len(analyses),
    }
    caveats = ["us_rules", "turnover", "base_rate", "short_history"]
    for name, x in analyses.items():
        if x["volume"]["mode"] == "price_only":
            caveats.append("price_only:" + name)
    hist_tail = sm["daily"][-history_sessions:]
    return {
        "skill": "gpw-market-regime", "version": VERSION, "lang": lang,
        "regime": regime,
        "top_risk": risk,
        "leadership": lead,
        "indices": analyses,
        "skipped_indices": skipped,
        "history": {"index": primary, "sessions": len(hist_tail), "from": hist_tail[0]["date"],
                    "state_before": a["state_before_history"], "transitions": a["transitions"],
                    "state_share": a["state_share"],
                    "recent": [{k: d[k] for k in ("date", "state", "dd_count", "drawdown_pct")} for d in hist_tail[-10:]]},
        "breadth_input": breadth,
        "caveats": caveats,
        "config": {
            "dd_min_decline_pct": rg.DD_MIN_DECLINE * 100, "dd_window": rg.DD_WINDOW,
            "dd_expiry_gain_pct": rg.DD_EXPIRY_GAIN * 100, "dd_bands": {b: lo for lo, b in rg.DD_BANDS},
            "pressure_dd": rg.PRESSURE_DD, "recover_dd": rg.RECOVER_DD, "ftd_min_day": rg.FTD_MIN_DAY,
            "ftd_base_gain_pct": rg.FTD_BASE_GAIN_PCT, "us_ref_sigma_pct": rg.US_REF_SIGMA_PCT,
            "correction_base_pct": rg.CORRECTION_BASE_PCT, "bear_base_pct": rg.BEAR_BASE_PCT,
            "risk_weights": rg.RISK_WEIGHTS, "stall_days": False,
        },
    }


def run(args) -> Dict:
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else None
    end = dt.date.fromisoformat(args.as_of) if args.as_of else None
    print("Loading GPW index sessions...", file=sys.stderr)
    sessions = gpw_archive.load_sessions(args.sessions, end=end, type_id=gpw_archive.TYPE_INDICES,
                                         cache_dir=cache_dir, offline=args.offline, progress=True)
    if len(sessions) < 260:
        raise gpw_archive.GPWFetchError(
            "Only {} index sessions available; need at least 260. Re-run without --offline.".format(len(sessions)))
    breadth_path = find_input(args.breadth_json, args.input_dir, "gpw_breadth_*.json")
    rep = build_report(sessions, args.index, args.history_sessions, breadth_path, args.lang)
    as_of = dt.date.fromisoformat(rep["regime"]["as_of"])
    notes = ["Index turnover = sum of member turnover in the GPW archive (thousand PLN).",
             "Stall days are not counted (see references/methodology.md)."]
    if rep["skipped_indices"]:
        notes.append("Skipped: {}".format(rep["skipped_indices"]))
    rep["data_provenance"] = provenance.build(
        as_of=as_of, sources=["GPW — Archiwum notowań (indeksy): https://www.gpw.pl/archiwum-notowan"],
        sessions_used=len(sessions), universe_size=len(rep["indices"]), notes=notes,
        corporate_actions_adjusted=False, survivorship_free=True, point_in_time_membership=False)
    if args.as_of:
        # A deliberate historical run is not "stale".
        rep["data_provenance"]["freshness"]["is_stale"] = False
        rep["data_provenance"]["notes"].append("Historical run (--as-of {}).".format(args.as_of))
    return rep


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

def _why(t, keys):
    out = []
    for k in keys:
        if k.startswith("rally_day_"):
            out.append(t("why_rally", d=k.split("_")[-1]))
        else:
            out.append(t("why_" + k))
    return "; ".join(out)


def render_markdown(r: Dict, lang: str) -> str:
    t = T(STRINGS, lang)
    g = r["regime"]
    prov = r.get("data_provenance") or {}
    idx = LABELS[g["index"]]
    L = ["# " + t("title"), "", "**{}:** {} · **{}:** {}".format(t("as_of"), g["as_of"], t("generated"),
                                                                prov.get("retrieved_at_utc", "—")), ""]
    if (prov.get("freshness") or {}).get("is_stale"):
        L += [t("stale", n=prov["freshness"]["sessions_behind"]), ""]
    L += ["## " + t("answer"), "",
          t("regime_line", idx=idx, state=t("state_" + g["state"]), risk=num(g["top_risk_score"], 0, lang),
            band=t("risk_" + g["top_risk_band"]), dd=g["distribution_days"], ddband=t("band_" + g["distribution_band"])),
          "", t("explain_" + g["state"]), ""]
    if g["state_reasons"]:
        L += ["**{}:** {}".format(t("why"), _why(t, g["state_reasons"])), ""]

    # indices table
    L += ["## " + t("indices"), "",
          "| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
              t("col_index"), t("col_state"), t("col_close"), t("col_dd"), t("col_sma50"), t("col_sma200"),
              t("col_slope"), t("col_draw"), t("col_ftd")),
          "|---|---|---|---|---|---|---|---|---|"]
    for name, a in r["indices"].items():
        tr, ds = a["trend"], a["distribution"]
        f = a["follow_through"]
        L.append("| {}{} | {} | {} | {} ({}) | {} | {} | {} | {} | {} |".format(
            LABELS[name], t("price_only") if a["volume"]["mode"] == "price_only" else "",
            t("state_" + a["state"]), num(tr["close"], 2, lang), ds["count"], t("band_" + ds["band"]),
            pct(tr["dist_sma50_pct"], 1, lang, signed=True), pct(tr["dist_sma200_pct"], 1, lang, signed=True),
            pct(tr["sma200_slope_20s_pct"], 2, lang, signed=True), pct(-tr["drawdown_pct"], 1, lang),
            "{} ({})".format(f["date"], t("health_" + f["health"])) if f else "—"))
    L.append("")
    for name, why in r.get("skipped_indices", {}).items():
        L += [t("skipped", idx=LABELS[name], why=why), ""]

    a = r["indices"][g["index"]]
    # distribution days
    ds = a["distribution"]
    L += ["## " + t("dist_title", idx=idx), "", t("dist_txt"), ""]
    if ds["days"]:
        L += ["| {} | {} | {} | {} |".format(t("col_date"), t("col_chg"), t("col_left"), t("col_note")), "|---|---|---|---|"]
        for d in reversed(ds["days"]):
            L.append("| {} | {} | {} | {} |".format(d["date"], pct(d["change_pct"], 2, lang), d["expires_in_sessions"],
                                                   t("expiry_note") if d["expiry_day"] else ""))
    else:
        L.append(t("none"))
    if ds["expired_by_gain"]:
        L += ["", t("expired_gain", d=", ".join(ds["expired_by_gain"]))]
    L.append("")

    # FTD / rally attempt
    L += ["## " + t("ftd_title", idx=idx), ""]
    if a["rally_attempt"]:
        ra = a["rally_attempt"]
        L += [t("attempt_line", day=ra["day"], start=ra["start_date"], low=num(ra["rally_low"], 2, lang), lowd=ra["low_date"],
                g=pct(ra["gain_from_low_pct"], 1, lang), thr=pct(ra["ftd_gain_needed_pct"], 2, lang)), ""]
    f = a["follow_through"]
    if f:
        L += [t("ftd_line", date=f["date"], day=f["day_number"], gain=pct(f["gain_pct"], 2, lang),
                thr=pct(f["threshold_pct"], 2, lang),
                turn=(pct(f["turnover_change_pct"], 1, lang, signed=True) if f["turnover_change_pct"] is not None else "—"),
                since=f["sessions_since"], gs=pct(f["gain_since_pct"], 1, lang, signed=True),
                dds=f["distribution_days_since"], health=t("health_" + f["health"]))]
        if f["late"]:
            L += ["", t("late_note")]
        L.append("")
    elif not a["rally_attempt"]:
        L += [t("no_ftd"), ""]
    th = a["thresholds"]
    L += ["### " + t("thr_title"), "", t("thr_txt", idx=idx, s=pct(th["sigma_pct"], 2, lang), r=num(th["vol_ratio"], 2, lang),
                                          f=pct(th["ftd_gain_pct"], 2, lang), c=pct(th["correction_pct"], 1, lang),
                                          b=pct(th["bear_pct"], 1, lang)), ""]

    # leadership
    lead = r["leadership"]
    if lead.get("available"):
        L += ["## " + t("lead_title"), ""]
        for w in (20, 60):
            x = lead["w{}".format(w)]
            L.append("- " + t("lead_line", w=w, a=pct(x["wig20_pct"], 1, lang, signed=True),
                                m=pct(x["mwig40_pct"], 1, lang, signed=True), b=pct(x["swig80_pct"], 1, lang, signed=True),
                                s=num(x["spread_pp"], 1, lang) if x["spread_pp"] is not None else "—"))
        L += ["", t("pattern_" + lead["pattern"]), ""]

    # top risk
    risk = r["top_risk"]
    L += ["## " + t("risk_title"), "", "| {} | {} | {} | {} |".format(t("col_comp"), t("col_w"), t("col_s"), t("col_v")),
          "|---|---|---|---|"]
    for k in rg.RISK_WEIGHTS:
        c = risk["components"].get(k)
        if not c:
            continue
        v = c["value"]
        if k == "trend":
            v = ", ".join(t("trend_below_sma50") if x == "below_sma50" else t("why_" + x) for x in v) if v else "—"
        elif k == "leadership":
            v = t("pat_" + v)
        elif k == "drawdown":
            v = pct(v, 1, lang)
        elif k == "breadth":
            v = "{} / {}".format(num(v.get("breadth_composite"), 0, lang), v.get("divergence_signal") or "—")
        L.append("| {} | {} | {} | {} |".format(t("comp_" + k), pct(c["weight"] * 100, 0, lang), num(c["score"], 0, lang), v))
    L += ["| **Σ** | | **{}** | {} |".format(num(risk["score"], 0, lang), t("risk_" + risk["band"])), ""]
    bi = r["breadth_input"]
    if bi.get("used"):
        L.append(t("breadth_used", f=Path(bi["file"]).name, d=bi["as_of"]))
        if bi.get("stale"):
            L.append(t("breadth_stale", n=bi["sessions_behind"]))
    elif bi.get("reason") == "future":
        L.append(t("breadth_future"))
    else:
        L.append(t("breadth_missing"))
    L.append("")

    # history
    h = r["history"]
    L += ["## " + t("hist_title", idx=idx, n=h["sessions"]), ""]
    if h["state_before"]:
        L += [t("hist_start", d=h["from"], s=t("state_" + h["state_before"])), ""]
    L += ["| {} | {} | {} | {} | {} |".format(t("col_date"), t("col_from"), t("col_to"), t("col_event"), t("col_dd")),
          "|---|---|---|---|---|"]
    for tr in h["transitions"]:
        if tr.get("event"):
            ev = t("ev_" + tr["event"])
        elif tr["to"] == "confirmed_uptrend":
            ev = t("why_recovered")
        else:
            ev = _why(t, tr["why"])
        L.append("| {} | {} | {} | {} | {} |".format(tr["date"], t("state_" + tr["from"]) if tr["from"] else "—",
                                                    t("state_" + tr["to"]), ev, tr["dd_count"]))
    share = h["state_share"]["share_pct"]
    L += ["", "**{}:** ".format(t("hist_share")) + " · ".join(
        "{} {}".format(t("state_" + s), pct(share[s], 0, lang)) for s in rg.STATES), ""]

    # caveats
    L += ["## " + t("caveats"), "", "- " + t("cav_us"), "- " + t("cav_turnover"), "- " + t("cav_base"),
          "- " + t("cav_hist", d=a["model_start"])]
    for c in r["caveats"]:
        if c.startswith("price_only:"):
            L.append("- " + t("cav_price_only", idx=LABELS[c.split(":", 1)[1]]))
    L.append("")
    L += ["## " + t("sources"), ""] + ["- " + s for s in prov.get("sources", [])]
    L += ["- O'Neil W., *How to Make Money in Stocks* (4th ed.), ch. 9 — follow-through day, distribution days",
          "- Investor's Business Daily — The Big Picture / Market Pulse methodology", "", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except (gpw_archive.GPWFetchError, ValueError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    md = render_markdown(result, args.lang)
    if args.no_files:
        print(md)
        return 0
    out = Path(args.output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    jp, mp = out / "gpw_market_regime_{}.json".format(stamp), out / "gpw_market_regime_{}.md".format(stamp)
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(md, encoding="utf-8")
    g = result["regime"]
    print("Regime ({}): {} | top risk {} / 100 ({}) | distribution days {} | as of {}".format(
        g["index"], g["state"], g["top_risk_score"], g["top_risk_band"], g["distribution_days"], g["as_of"]))
    if result["data_provenance"]["freshness"]["is_stale"]:
        print("WARNING: data is stale ({} sessions behind)".format(result["data_provenance"]["freshness"]["sessions_behind"]))
    print("JSON: {}\nMarkdown: {}".format(jp, mp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
