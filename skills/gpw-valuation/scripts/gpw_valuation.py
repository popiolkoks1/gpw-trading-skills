#!/usr/bin/env python3
"""GPW Valuation - CLI.

Transparent valuation of one GPW company: what the price implies (reverse DCF
first) and a fair-value range under explicit, sourced assumptions (2-stage
FCFF DCF, WACC x g sensitivity, bear/base/bull, comps; banks and insurers via
justified P/BV; Gordon DDM cross-check). Never a price target.

    python3 scripts/gpw_valuation.py KETY
    python3 scripts/gpw_valuation.py PKOBP --lang en
    python3 scripts/gpw_valuation.py CDPROJEKT --revenue-growth 12 --margin 30 --wacc 10
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import constituents, fundamentals, gpw_archive, http, panel, plcal, provenance, reports  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T, num, pct, pln  # noqa: E402

import valuation_data as vd  # noqa: E402
import valuation_engine as ve  # noqa: E402

SKILL = "gpw-valuation"
VERSION = "1.0.0"
PREFIX = "gpw_valuation"
# ~2 years of sessions: 104 weekly returns for the beta regression
# (2y weekly is the Bloomberg default and Damodaran's suggestion for
# markets with thinner trading than the US).
BETA_SESSIONS = 504
# Polish periodic reports: Q within 60 days, annual within 4 months
# (Rozporządzenie MF z 29.03.2018, par. 79), so the newest Notoria period
# can legitimately be ~7 months old (30 Sep -> annual report by 30 Apr).
FUNDAMENTALS_MAX_AGE_DAYS = 215
MAX_PEERS_DEFAULT = 15

# Assumption keys accepted from the CLI and the assumptions JSON (percent
# values in %, money in mln PLN, beta plain).
PCT_KEYS = {"revenue_growth": (-50, 100), "margin": (-100, 100), "capex_pct": (0, 100), "da_pct": (0, 100),
            "nwc_pct": (-50, 100), "wacc": (0.5, 50), "terminal_growth": (-5, 10), "rf": (-1, 30),
            "erp": (0, 20), "crp": (0, 20), "cost_of_debt": (0, 50), "tax_rate": (0, 50), "roe": (-50, 100),
            "coe": (0.5, 50)}
MLN_KEYS = {"net_debt", "fcf"}
PLAIN_KEYS = {"beta": (0.05, 5.0), "price": (0.0001, 1e7), "dps_pln": (0.0, 1e5)}
ALLOWED_JSON_KEYS = set(PCT_KEYS) | MLN_KEYS | set(PLAIN_KEYS) | {"scenarios", "peers"}


class ValuationError(RuntimeError):
    pass


STRINGS = {
    "title": {"pl": "Wycena {t} — co zakłada cena i przedział wartości", "en": "Valuation of {t} — what the price implies and a value range"},
    "stale": {"pl": "⚠️ Dane cenowe są nieaktualne (stan na {d}, {n} sesji wstecz).", "en": "⚠️ Price data is stale (as of {d}, {n} sessions behind)."},
    "answer": {"pl": "Odpowiedź", "en": "Answer"},
    "ans_rdcf": {"pl": "**Cena {p} ({d}) zakłada wzrost wolnych przepływów pieniężnych (FCF) ok. {g} rocznie przez {y} lat**, a potem {tg} rocznie w nieskończoność, przy koszcie kapitału (WACC) {w}.",
                 "en": "**The price of {p} ({d}) implies free cash flow (FCF) growth of about {g} a year for {y} years**, then {tg} a year for ever, at a cost of capital (WACC) of {w}."},
    "ans_rdcf_na": {"pl": "Odwróconego DCF nie da się policzyć: bazowy FCF jest ujemny lub zerowy ({f}). Cena {p} ({d}) przy WACC {w} odpowiada stałemu FCF ok. {nf} rocznie bez wzrostu.",
                    "en": "The reverse DCF cannot be solved: base FCF is zero or negative ({f}). The price of {p} ({d}) at a WACC of {w} equals a constant FCF of about {nf} a year with no growth."},
    "ans_rdcf_edge": {"pl": "Cena wymagałaby wzrostu FCF poza zakresem wyszukiwania ({g}).", "en": "The price would need FCF growth outside the search range ({g})."},
    "ans_bank": {"pl": "**Cena {p} ({d}) to {pbv}× wartości księgowej. Przy koszcie kapitału własnego {coe} i wzroście {tg} zakłada trwałe ROE ok. {roe}** (obecne ROE: {roe_now}).",
                 "en": "**The price of {p} ({d}) is {pbv}× book value. At a cost of equity of {coe} and growth of {tg} it implies a sustainable ROE of about {roe}** (current ROE: {roe_now})."},
    "ans_range": {"pl": "Przedział wartości przy jawnych założeniach (scenariusz pesymistyczny / bazowy / optymistyczny): **{lo} – {hi}** (bazowo {base} na akcję).",
                  "en": "Value range under explicit assumptions (bear / base / bull): **{lo} – {hi}** (base {base} per share)."},
    "pos_below_range": {"pl": "Cena jest poniżej przedziału: do dolnej granicy {a}, do wartości bazowej {b}.", "en": "The price is below the range: {a} to the low end, {b} to the base value."},
    "pos_above_range": {"pl": "Cena jest powyżej przedziału: do górnej granicy {c}, do wartości bazowej {b}.", "en": "The price is above the range: {c} to the high end, {b} to the base value."},
    "pos_within_range": {"pl": "Cena mieści się w przedziale (do wartości bazowej {b}).", "en": "The price is inside the range ({b} to the base value)."},
    "pos_unknown": {"pl": "Nie da się określić położenia ceny względem przedziału.", "en": "The price cannot be placed against the range."},
    "not_target": {"pl": "To nie jest cena docelowa ani rekomendacja — wynik zmienia się mocno z założeniami (zobacz tabelę wrażliwości).",
                   "en": "This is not a price target or a recommendation — the result moves a lot with the assumptions (see the sensitivity table)."},
    "warnings": {"pl": "Ostrzeżenia", "en": "Warnings"},
    "explain": {"pl": "Wyjaśnienie dla początkujących", "en": "Plain-language explanation"},
    "explain_wacc": {"pl": "**WACC (średni koszt kapitału)** to minimalny roczny zwrot, jakiego oczekują łącznie akcjonariusze i wierzyciele spółki. Składa się z kosztu kapitału własnego (rentowność polskich obligacji 10-letnich + premia za ryzyko akcji × beta) i kosztu długu po podatku. Im wyższy WACC, tym mniej warte są przyszłe pieniądze dziś.",
                     "en": "**WACC (weighted average cost of capital)** is the minimum yearly return shareholders and lenders together expect. It mixes the cost of equity (Polish 10-year bond yield + equity risk premium × beta) and the after-tax cost of debt. The higher the WACC, the less future cash is worth today."},
    "explain_tv": {"pl": "**Wartość rezydualna (terminalna)** to wartość wszystkich przepływów po okresie prognozy, liczona wzorem Gordona: FCF następnego roku / (WACC − g). Często stanowi ponad połowę wyceny, dlatego mała zmiana WACC lub g mocno zmienia wynik.",
                   "en": "**Terminal value** is the value of all cash flows after the forecast years, from the Gordon formula: next year's FCF / (WACC − g). It is often more than half of the value, so a small change in WACC or g moves the result a lot."},
    "explain_rdcf": {"pl": "**Odwrócony DCF** nie zgaduje przyszłości: pyta, jaki wzrost musi się zrealizować, żeby dzisiejsza cena była uczciwa. Potem możesz ocenić, czy to realne w porównaniu z historią spółki i branży.",
                     "en": "**Reverse DCF** does not guess the future: it asks what growth must happen for today's price to be fair. You then judge whether that is realistic against the company's and the sector's history."},
    "explain_bank": {"pl": "**Banki** wycenia się przez kapitał własny, nie FCFF (dług to dla banku surowiec, nie finansowanie). Uzasadniona wartość C/WK = (ROE − g) / (koszt kapitału − g): bank zarabiający więcej niż kosztuje jego kapitał jest wart więcej niż wartość księgowa.",
                     "en": "**Banks** are valued on equity, not FCFF (debt is a bank's raw material, not its financing). Justified P/BV = (ROE − g) / (cost of equity − g): a bank earning more than its cost of equity is worth more than book."},
    "dr": {"pl": "Stopa dyskontowa (CAPM w PLN, metoda Damodarana)", "en": "Discount rate (CAPM in PLN, Damodaran method)"},
    "item": {"pl": "Pozycja", "en": "Item"}, "value": {"pl": "Wartość", "en": "Value"}, "source": {"pl": "Źródło", "en": "Source"},
    "rdcf": {"pl": "Odwrócony DCF", "en": "Reverse DCF"},
    "dcf": {"pl": "DCF 2-etapowy (FCFF)", "en": "2-stage DCF (FCFF)"},
    "bank": {"pl": "Model nadwyżkowej stopy zwrotu (uzasadnione C/WK)", "en": "Excess-return model (justified P/BV)"},
    "sens": {"pl": "Wrażliwość: wartość na akcję (wiersze: {r}, kolumny: wzrost rezydualny g)", "en": "Sensitivity: value per share (rows: {r}, columns: terminal growth g)"},
    "scen": {"pl": "Scenariusze", "en": "Scenarios"},
    "comps": {"pl": "Wycena porównawcza (spółki z indeksu sektorowego)", "en": "Comparable companies (sector index peers)"},
    "ddm": {"pl": "Model dywidendowy Gordona (kontrola)", "en": "Gordon dividend model (cross-check)"},
    "assump": {"pl": "Wszystkie założenia i ich źródła", "en": "All assumptions and their sources"},
    "sources": {"pl": "Źródła / Sources", "en": "Sources"},
    "year": {"pl": "Rok", "en": "Year"},
}

ASSUMPTION_LABELS = {
    "rf_gross": ("Rentowność PL 10L", "PL 10Y yield"),
    "default_spread": ("Spread ryzyka niewypłacalności Polski", "Poland default spread"),
    "rf": ("Stopa wolna od ryzyka (PLN)", "Risk-free rate (PLN)"),
    "beta": ("Beta (Blume, ograniczona)", "Beta (Blume, bounded)"),
    "mature_erp": ("Premia za ryzyko dojrzałego rynku", "Mature-market ERP"),
    "crp": ("Premia za ryzyko kraju (Polska)", "Country risk premium (Poland)"),
    "erp": ("Premia za ryzyko akcji (razem)", "Total equity risk premium"),
    "cost_of_equity": ("Koszt kapitału własnego", "Cost of equity"),
    "company_spread": ("Spread kredytowy spółki (rating syntetyczny)", "Company credit spread (synthetic rating)"),
    "cost_of_debt": ("Koszt długu przed podatkiem", "Pre-tax cost of debt"),
    "tax_rate": ("Stawka CIT", "CIT rate"),
    "net_debt": ("Dług netto", "Net debt"),
    "weight_equity": ("Udział kapitału własnego (wartości rynkowe)", "Equity weight (market values)"),
    "wacc": ("WACC", "WACC"),
    "terminal_growth": ("Wzrost rezydualny g", "Terminal growth g"),
    "fcf0": ("Bazowy FCF (roczny)", "Base FCF (annual)"),
    "revenue0": ("Przychody bazowe (roczne)", "Base revenue (annual)"),
    "revenue_growth": ("Wzrost przychodów (lata 1-5)", "Revenue growth (years 1-5)"),
    "margin": ("Marża EBIT", "EBIT margin"),
    "da_pct": ("Amortyzacja / przychody", "D&A / revenue"),
    "capex_pct": ("Capex / przychody", "Capex / revenue"),
    "nwc_pct": ("Kapitał obrotowy / przychody", "Working capital / revenue"),
    "roe": ("ROE (roczne)", "ROE (annual)"),
    "coe": ("Koszt kapitału własnego (bank)", "Cost of equity (bank)"),
    "book_value": ("Wartość księgowa", "Book value"),
    "shares": ("Liczba akcji", "Shares"),
    "price": ("Cena", "Price"),
}


# --------------------------------------------------------------------------
# Arguments
# --------------------------------------------------------------------------

def _pct_arg(lo, hi):
    def f(text):
        try:
            v = float(str(text).replace(",", "."))
        except ValueError:
            raise argparse.ArgumentTypeError("not a number: {!r}".format(text))
        if not lo <= v <= hi:
            raise argparse.ArgumentTypeError("must be between {} and {}".format(lo, hi))
        return v
    return f


def _float_arg(text):
    try:
        return float(str(text).replace(",", "."))
    except ValueError:
        raise argparse.ArgumentTypeError("not a number: {!r}".format(text))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Transparent valuation of one GPW company (reverse DCF, DCF, comps) - PL/EN")
    p.add_argument("ticker", help="GPW ticker as in the quotes archive, e.g. KETY, CDPROJEKT, PKOBP, PKNORLEN (ORLEN)")
    p.add_argument("--isin", default=None, help="ISIN when the ticker is not on today's GPW listing")
    p.add_argument("--model", choices=["auto", "fcff", "bank"], default="auto",
                   help="auto: banks/insurers -> bank (justified P/BV), others -> fcff")
    g = p.add_argument_group("assumption overrides (percent values in %%, money in mln PLN)")
    g.add_argument("--revenue-growth", type=_pct_arg(*PCT_KEYS["revenue_growth"]), help="revenue growth years 1-5, %%")
    g.add_argument("--margin", type=_pct_arg(*PCT_KEYS["margin"]), help="EBIT margin, %%")
    g.add_argument("--capex-pct", type=_pct_arg(*PCT_KEYS["capex_pct"]), help="capex / revenue, %%")
    g.add_argument("--da-pct", type=_pct_arg(*PCT_KEYS["da_pct"]), help="D&A / revenue, %%")
    g.add_argument("--nwc-pct", type=_pct_arg(*PCT_KEYS["nwc_pct"]), help="working capital / revenue, %%")
    g.add_argument("--wacc", type=_pct_arg(*PCT_KEYS["wacc"]), help="discount rate, %% (bank model: cost of equity)")
    g.add_argument("--terminal-growth", type=_pct_arg(*PCT_KEYS["terminal_growth"]), help="perpetual growth g, %%")
    g.add_argument("--rf", type=_pct_arg(*PCT_KEYS["rf"]), help="PL 10Y government yield, %% (gross)")
    g.add_argument("--erp", type=_pct_arg(*PCT_KEYS["erp"]), help="mature-market equity risk premium, %%")
    g.add_argument("--crp", type=_pct_arg(*PCT_KEYS["crp"]), help="Poland country risk premium, %%")
    g.add_argument("--beta", type=_pct_arg(*PLAIN_KEYS["beta"]), help="equity beta (skips the regression)")
    g.add_argument("--cost-of-debt", type=_pct_arg(*PCT_KEYS["cost_of_debt"]), help="pre-tax cost of debt, %%")
    g.add_argument("--tax-rate", type=_pct_arg(*PCT_KEYS["tax_rate"]), help="tax rate, %% (default 19%% CIT)")
    g.add_argument("--net-debt", type=_float_arg, help="net debt, mln PLN (negative = net cash)")
    g.add_argument("--fcf", type=_float_arg, help="base annual free cash flow to the firm, mln PLN")
    g.add_argument("--roe", type=_pct_arg(*PCT_KEYS["roe"]), help="bank model: sustainable ROE, %%")
    g.add_argument("--price", type=_pct_arg(*PLAIN_KEYS["price"]), help="price per share in PLN (default: last close)")
    p.add_argument("--assumptions", default=None, help="JSON file with assumption overrides (CLI flags win)")
    p.add_argument("--rf-mode", choices=["damodaran", "gross"], default="damodaran",
                   help="damodaran: rf = PL 10Y - Poland default spread (default); gross: PL 10Y as is")
    p.add_argument("--peers", default=None, help="comma-separated peer tickers (default: same GPW sector index)")
    p.add_argument("--max-peers", type=int, default=MAX_PEERS_DEFAULT)
    p.add_argument("--no-comps", action="store_true", help="skip the comparable-companies step")
    p.add_argument("--macro-json", default=None, help="gpw-macro-regime JSON with indicators.pl10y")
    p.add_argument("--input-dir", default=".", help="where to look for the newest gpw-macro-regime JSON")
    p.add_argument("--no-history-save", action="store_true", help="do not add this snapshot to the local fundamentals history")
    p.add_argument("--as-of", default=None, help="price date YYYY-MM-DD (fundamentals stay the latest available)")
    p.add_argument("--offline", action="store_true", help="cache only, no network")
    p.add_argument("--cache-dir", default=None)
    p.add_argument("--lang", choices=["pl", "en"], default="pl")
    p.add_argument("--output-dir", default=".")
    p.add_argument("--no-files", action="store_true", help="print the Markdown and write no files")
    a = p.parse_args(argv)
    today = plcal.today_warsaw()
    if a.as_of:
        try:
            a.as_of_date = dt.date.fromisoformat(a.as_of)
        except ValueError:
            p.error("--as-of must be YYYY-MM-DD")
        if a.as_of_date > today:
            p.error("--as-of cannot be in the future")
    else:
        a.as_of_date = today
    if not 1 <= a.max_peers <= 40:
        p.error("--max-peers must be between 1 and 40")
    if a.isin and not fundamentals.isin_is_valid(a.isin):
        p.error("--isin is not a valid ISIN")
    a.cache_dir = Path(a.cache_dir).expanduser() if a.cache_dir else None
    return a


# --------------------------------------------------------------------------
# Overrides: CLI > assumptions JSON > computed defaults
# --------------------------------------------------------------------------

def load_assumptions(path: Optional[str]) -> Dict:
    if not path:
        return {}
    p = Path(path).expanduser()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValuationError("cannot read assumptions JSON {}: {}".format(p, exc))
    if not isinstance(data, dict):
        raise ValuationError("assumptions JSON must be an object")
    unknown = sorted(set(data) - ALLOWED_JSON_KEYS)
    if unknown:
        raise ValuationError("unknown keys in assumptions JSON: {} (allowed: {})".format(
            ", ".join(unknown), ", ".join(sorted(ALLOWED_JSON_KEYS))))
    for k, v in data.items():
        if k in ("scenarios", "peers"):
            continue
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValuationError("assumption {!r} must be a number".format(k))
        rng = PCT_KEYS.get(k) or PLAIN_KEYS.get(k)
        if rng and not rng[0] <= v <= rng[1]:
            raise ValuationError("assumption {!r} = {} outside {}..{}".format(k, v, rng[0], rng[1]))
    if "scenarios" in data:
        sc = data["scenarios"]
        if not isinstance(sc, dict) or not set(sc) <= {"bear", "base", "bull"}:
            raise ValuationError("assumptions JSON 'scenarios' must be an object with bear/base/bull keys")
    if "peers" in data and not (isinstance(data["peers"], list) and all(isinstance(x, str) for x in data["peers"])):
        raise ValuationError("assumptions JSON 'peers' must be a list of tickers")
    data["_path"] = str(p)
    return data


class Assumptions:
    """Collects every assumption with its value and source."""

    def __init__(self, args, user_json: Dict):
        self.args, self.json, self.items = args, user_json, {}

    def override(self, key: str, scale: float = 0.01):
        v = getattr(self.args, key, None)
        if v is not None:
            return v * scale, "user (CLI --{})".format(key.replace("_", "-"))
        if key in self.json:
            return self.json[key] * scale, "user (assumptions JSON {})".format(Path(self.json["_path"]).name)
        return None, None

    def take(self, key: str, default, default_source: str, unit: str = "pct", scale: float = 0.01, note: str = None):
        v, src = self.override(key, scale)
        if v is None:
            v, src = default, default_source
        self.set(key, v, src, unit, note)
        return v

    def set(self, key: str, value, source: str, unit: str = "pct", note: str = None):
        self.items[key] = {"value": value, "unit": unit, "source": source}
        if note:
            self.items[key]["note"] = note


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def _resolve(args, warnings: List[str]) -> Dict:
    try:
        return fundamentals.resolve_isin(args.ticker, args.cache_dir, args.offline, explicit=args.isin)
    except fundamentals.FundamentalsError as exc:
        alt = fundamentals.ticker_for_company_name(args.ticker, args.cache_dir, offline=True)
        if alt:
            warnings.append(T(W, args.lang)("w_alias", a=args.ticker.upper(), t=alt))
            return fundamentals.resolve_isin(alt, args.cache_dir, args.offline)
        raise ValuationError(str(exc))


W = {
    "w_alias": {"pl": "Ticker {a} zinterpretowano jako {t} (nazwa w archiwum GPW).", "en": "Ticker {a} was read as {t} (the GPW archive name)."},
    "w_annualised": {"pl": "Dane finansowe to narastająco {l} ({m} mies.); przepływy roczne = ×12/{m} (brak historii do TTM). Sezonowość może zniekształcić wynik.",
                     "en": "Financials are year-to-date {l} ({m} months); annual flows = ×12/{m} (no history for TTM). Seasonality may distort the result."},
    "w_ttm": {"pl": "Przepływy roczne = TTM z lokalnej historii ({l} + {fy} − {py}).", "en": "Annual flows = TTM from the local history ({l} + {fy} − {py})."},
    "w_fund_old": {"pl": "Najnowszy okres finansowy ({l}) kończy się {d} — ponad {n} dni temu; dane mogą być nieaktualne.",
                   "en": "The newest financial period ({l}) ends {d}, more than {n} days ago; data may be out of date."},
    "w_net_debt_proxy": {"pl": "Dług netto ({v}) oszacowano z wyniku finansowego netto (zysk operacyjny − zysk brutto), bo karta GPW nie podaje zadłużenia. To przybliżenie niskiej pewności — podaj --net-debt z bilansu.",
                         "en": "Net debt ({v}) was estimated from the net financial result (operating − pre-tax profit) because the GPW card has no debt split. Low-confidence proxy — pass --net-debt from the balance sheet."},
    "w_net_debt_none": {"pl": "Brak danych do oszacowania długu netto — przyjęto 0. Podaj --net-debt.", "en": "No data to estimate net debt — assumed 0. Pass --net-debt."},
    "w_beta_default": {"pl": "Za mało notowań do regresji bety ({n} tyg. < {m}) — przyjęto betę 1,0.", "en": "Not enough history for the beta regression ({n} weeks < {m}) — beta 1.0 assumed."},
    "w_beta_bounded": {"pl": "Beta po korekcie Blume'a ({b}) wyszła poza zakres {lo}–{hi} i została ograniczona.", "en": "Blume-adjusted beta ({b}) was outside {lo}–{hi} and was bounded."},
    "w_beta_r2": {"pl": "Niskie R² regresji bety ({r}) — rynek słabo tłumaczy ruchy kursu (płynność?), beta jest niepewna.", "en": "Low beta regression R² ({r}) — the market explains little of the stock's moves (liquidity?); beta is uncertain."},
    "w_damodaran_old": {"pl": "Dane Damodarana (ERP, CRP) z {d} są starsze niż 12 miesięcy.", "en": "Damodaran data (ERP, CRP) from {d} is older than 12 months."},
    "w_damodaran_embedded": {"pl": "Nie udało się pobrać tabeli Damodarana ({e}); użyto wartości wbudowanych z {d}.", "en": "Damodaran table could not be fetched ({e}); embedded values from {d} used."},
    "w_macro_stale": {"pl": "Raport gpw-macro-regime ({f}) jest nieaktualny; rentowność 10L pobrano bezpośrednio.", "en": "The gpw-macro-regime report ({f}) is stale; the 10Y yield was fetched directly."},
    "w_macro_stale_used": {"pl": "Użyto nieaktualnego raportu gpw-macro-regime ({f}, stan {d}) — brak świeższych danych.", "en": "A stale gpw-macro-regime report was used ({f}, as of {d}) — nothing fresher available."},
    "w_g_above_rf": {"pl": "Wzrost rezydualny {g} jest wyższy niż stopa wolna od ryzyka {rf} — żadna firma nie rośnie wiecznie szybciej niż gospodarka (Damodaran).",
                     "en": "Terminal growth {g} is above the risk-free rate {rf} — no firm can outgrow the economy for ever (Damodaran)."},
    "w_g_capped": {"pl": "Domyślny wzrost rezydualny obniżono do stopy wolnej od ryzyka ({rf}).", "en": "Default terminal growth was lowered to the risk-free rate ({rf})."},
    "w_fcf_fallback": {"pl": "FCF z przepływów (CFO − capex = {c}) jest ≤ 0; do odwróconego DCF użyto znormalizowanego FCFF = EBIT(1−t) + amortyzacja − capex ({n}).",
                       "en": "Cash FCF (CFO − capex = {c}) is ≤ 0; the reverse DCF uses normalised FCFF = EBIT(1−t) + D&A − capex ({n})."},
    "w_tv_share": {"pl": "Wartość rezydualna to {s} wyceny DCF — wynik zależy głównie od założeń długoterminowych.", "en": "Terminal value is {s} of the DCF value — the result depends mostly on long-run assumptions."},
    "w_capex_below_da": {"pl": "Capex ({c}) jest niższy niż amortyzacja ({d}) przy dodatnim wzroście w nieskończoność — to zwykle zawyża wartość.",
                         "en": "Capex ({c}) is below D&A ({d}) with positive perpetual growth — this usually overstates value."},
    "w_few_peers": {"pl": "Tylko {n} spółek porównawczych z danymi (zalecane ≥ {m}) — mediana mało reprezentatywna.", "en": "Only {n} peers with data (≥ {m} recommended) — the median is not representative."},
    "w_no_peers": {"pl": "Spółka nie należy do żadnego indeksu sektorowego GPW — wycena porównawcza pominięta (podaj --peers).", "en": "The company is in no GPW sector index — comps skipped (pass --peers)."},
    "w_peer_fail": {"pl": "Pominięto spółki bez danych: {p}.", "en": "Peers without data skipped: {p}."},
    "w_hist_as_of": {"pl": "Analiza historyczna (--as-of {d}): cena z tego dnia, ale dane finansowe są najnowsze dostępne (brak danych punktowych w czasie).",
                     "en": "Historical run (--as-of {d}): price as of that day, but financials are the latest available (no point-in-time data)."},
    "w_no_notoria": {"pl": "Brak danych finansowych (zakładka Notoria) — model FCFF niemożliwy.", "en": "No financial data (Notoria tab) — FCFF model impossible."},
    "w_bank_minority": {"pl": "ROE liczone na kapitale udziałowców jednostki dominującej na koniec okresu (brak średniego kapitału).", "en": "ROE uses parent equity at period end (no average equity available)."},
    "w_negative_value": {"pl": "Wartość kapitału w modelu bazowym jest ujemna — założenia nie generują wartości dla akcjonariuszy.", "en": "The base-case equity value is negative — the assumptions create no value for shareholders."},
    "w_ddm_unstable": {"pl": "Wskaźnik wypłaty ({p}) poza zakresem 20–100% — model dywidendowy tylko orientacyjny.", "en": "Payout ratio ({p}) outside 20–100% — the dividend model is only indicative."},
    "w_ronic": {"pl": "Wartość rezydualna zakłada zwrot z nowych inwestycji (RONIC) {r} — sprawdź, czy to realne.", "en": "The terminal value assumes a return on new invested capital (RONIC) of {r} — check that it is realistic."},
}


def _price_series(args, warnings):
    sessions = gpw_archive.load_sessions(BETA_SESSIONS, end=args.as_of_date, cache_dir=args.cache_dir,
                                         offline=args.offline, progress=True)
    idx = gpw_archive.load_sessions(BETA_SESSIONS, end=args.as_of_date, type_id=gpw_archive.TYPE_INDICES,
                                    cache_dir=args.cache_dir, offline=args.offline, progress=True)
    if not sessions or not idx:
        raise ValuationError("no GPW archive sessions available up to {} (offline cache empty?)".format(args.as_of_date))
    return sessions, panel.build_panel(sessions), panel.index_series(idx, "WIG")


def _rf(args, A, t, warnings) -> Dict:
    v, src = A.override("rf")
    if v is not None:
        return {"yield": v, "as_of": None, "source": src, "detail": {}}
    ref = args.as_of_date if args.as_of else None
    rep = reports.find_report("gpw-macro-regime", args.input_dir, explicit_path=args.macro_json,
                              reference=ref, lang=args.lang)
    from_rep = vd.rf_from_macro(rep)
    if from_rep and not rep["stale"]:
        return from_rep
    try:
        live = vd.rf_live(args.as_of_date, plcal.today_warsaw(), args.cache_dir, args.offline)
        if from_rep:
            warnings.append(t("w_macro_stale", f=rep["file"]))
        return live
    except vd.DataError:
        if from_rep:
            warnings.append(t("w_macro_stale_used", f=rep["file"], d=rep["as_of"]))
            return from_rep
        raise


def run(args) -> Dict:
    lang = args.lang
    t = T(dict(STRINGS, **W), lang)
    warnings: List[str] = []
    user_json = load_assumptions(args.assumptions)
    A = Assumptions(args, user_json)

    # -- company ----------------------------------------------------------
    res = _resolve(args, warnings)
    ticker, isin = res["ticker"], res["isin"]
    try:
        snap = fundamentals.snapshot(ticker, isin, args.cache_dir, args.offline)
    except (fundamentals.FundamentalsError, http.HTTPFetchError) as exc:
        raise ValuationError("fundamentals for {} unavailable: {}".format(ticker, exc))
    hist_status = None
    if not args.no_history_save and snap.get("notoria"):
        try:
            hist_status = fundamentals.save_snapshot(snap)["status"]
        except (OSError, fundamentals.FundamentalsError):
            hist_status = "save_failed"
    history = fundamentals.load_history(ticker)
    ind = snap["indicators"]
    noto = snap.get("notoria")
    shares = ind.get("shares_issued")
    if not shares:
        raise ValuationError("gpw.pl shows no share count for {}".format(ticker))

    # -- prices, beta -----------------------------------------------------
    sessions, pnl, wig = _price_series(args, warnings)
    s = pnl.get(ticker)
    if s is None:
        raise ValuationError("{} has no PLN quotes in the GPW archive up to {}".format(ticker, args.as_of_date))
    price_date = s.dates[-1]
    price, price_src = s.close[-1], "GPW archive close {}".format(price_date)
    v_over, src = A.override("price", 1.0)
    if v_over is not None:
        price, price_src = v_over, src
    A.set("price", price, price_src, "pln")
    A.set("shares", shares, "gpw.pl indicators tab", "count")
    mcap = price * shares
    if args.as_of:
        warnings.append(t("w_hist_as_of", d=args.as_of))

    beta_reg = vd.beta_from_series(s.dates, s.close, wig.dates, wig.close)
    b_over, b_src = A.override("beta", 1.0)
    if b_over is not None:
        beta = b_over
        A.set("beta", beta, b_src, "x")
    elif beta_reg is None or beta_reg["weeks"] < ve.MIN_BETA_WEEKS:
        beta = 1.0
        warnings.append(t("w_beta_default", n=(beta_reg or {}).get("weeks", 0), m=ve.MIN_BETA_WEEKS))
        A.set("beta", beta, "default 1.0 (market beta)", "x")
    else:
        beta = beta_reg["value"]
        if beta_reg["bounded"]:
            warnings.append(t("w_beta_bounded", b=num(beta_reg["blume"], 2, lang), lo=num(ve.BETA_BOUNDS[0], 1, lang),
                              hi=num(ve.BETA_BOUNDS[1], 1, lang)))
        if beta_reg["r2"] < ve.LOW_R2:
            warnings.append(t("w_beta_r2", r=num(beta_reg["r2"], 2, lang)))
        A.set("beta", beta, "OLS vs WIG, {} weekly returns, Blume-adjusted (raw {:.2f})".format(
            beta_reg["weeks"], beta_reg["beta"]), "x")

    # -- discount-rate inputs ----------------------------------------------
    rf_info = _rf(args, A, t, warnings)
    dam = vd.damodaran_poland(args.as_of_date, args.cache_dir, args.offline)
    if dam["source"] == "embedded":
        warnings.append(t("w_damodaran_embedded", e=dam.get("fetch_error"), d=dam["updated"]))
    if dam["stale"]:
        warnings.append(t("w_damodaran_old", d=dam["updated"]))
    dam_src = "Damodaran ctryprem ({}, updated {})".format(dam["source"], dam["updated"])
    ratings = vd.damodaran_ratings(args.cache_dir, args.offline)

    y10 = A.take("rf_gross", rf_info["yield"], "{}{}".format(
        rf_info["source"], ", as of {}".format(rf_info["as_of"]) if rf_info.get("as_of") else ""))
    if args.rf_mode == "damodaran":
        ds = A.take("default_spread", dam["default_spread"], dam_src + " (Moody's {})".format(dam["rating"]))
    else:
        ds = 0.0
        A.set("default_spread", 0.0, "--rf-mode gross (not subtracted)")
    rf = y10 - ds
    A.set("rf", rf, "PL 10Y − default spread" if args.rf_mode == "damodaran" else "PL 10Y (gross)")
    mature = A.take("erp", dam["mature_erp"], dam_src + " (ERP − CRP)")
    A.items["mature_erp"] = A.items.pop("erp")
    crp = A.take("crp", dam["crp"], dam_src)
    erp = mature + crp
    A.set("erp", erp, "mature ERP + CRP")
    ke = ve.capm(rf, beta, erp)
    tax = A.take("tax_rate", ve.CIT_RATE, "CIT 19% (ustawa o CIT art. 19 ust. 1 pkt 1)")

    # -- financials ----------------------------------------------------------
    sector_map = {}
    try:
        sector_map = constituents.sector_membership(args.cache_dir, args.offline)
    except gpw_archive.GPWFetchError:
        pass
    sector_index = sector_map.get(ticker)
    financial = vd.is_financial(ind.get("sector"), sector_index)
    model = args.model if args.model != "auto" else ("bank" if financial else "fcff")
    flows = vd.annual_flows(noto, history) if noto else None
    if flows:
        methods = set(m for k, m in flows["method"].items() if k in fundamentals.FLOW_ITEMS)
        if "annualised_ytd" in methods:
            warnings.append(t("w_annualised", l=flows["label"], m=flows["months"]))
        elif "ttm_history" in methods:
            warnings.append(t("w_ttm", l=flows["label"], fy=flows["fy_label"], py=flows["prev_ytd_label"]))
        if flows.get("period_end"):
            pe_date = dt.date.fromisoformat(flows["period_end"])
            if (args.as_of_date - pe_date).days > FUNDAMENTALS_MAX_AGE_DAYS:
                warnings.append(t("w_fund_old", l=flows["label"], d=flows["period_end"], n=FUNDAMENTALS_MAX_AGE_DAYS))
    elif model == "fcff":
        warnings.append(t("w_no_notoria"))
        raise ValuationError("no Notoria financial data for {}; FCFF valuation impossible".format(ticker))
    vals = flows["values"] if flows else {}

    result = {
        "skill": SKILL, "version": VERSION, "lang": lang, "as_of": price_date,
        "ticker": ticker, "isin": isin, "model": model, "sector": ind.get("sector"), "sector_index": sector_index,
        "price": price, "price_date": price_date, "shares": shares, "market_cap_pln": mcap,
    }
    rf_block = {"pl10y_pct": y10 * 100, "pl10y_source": rf_info["source"], "pl10y_as_of": rf_info.get("as_of"),
                "pl10y_detail": rf_info.get("detail"), "rf_mode": args.rf_mode, "default_spread_pct": ds * 100,
                "rf_pct": rf * 100, "beta": beta, "beta_regression": beta_reg, "mature_erp_pct": mature * 100,
                "crp_pct": crp * 100, "erp_pct": erp * 100, "cost_of_equity_pct": ke * 100,
                "damodaran": {k: dam.get(k) for k in ("rating", "default_spread", "crp", "erp", "mature_erp", "updated",
                                                       "source", "url", "age_days", "stale")}}

    if model == "bank":
        _bank(result, A, t, warnings, args, ind, vals, price, shares, ke, rf, rf_block)
    else:
        _fcff(result, A, t, warnings, args, ind, vals, price, shares, mcap, ke, rf, y10, tax, ratings, rf_block,
              yoy_revenue=(flows or {}).get("yoy", {}).get("revenue"))

    # -- DDM cross-check ----------------------------------------------------
    result["ddm"] = _ddm(A, t, warnings, args, ind, price, ke, result["assumptions_tg"], lang)
    # -- comps --------------------------------------------------------------
    result["comps"] = {"skipped": True}
    if not args.no_comps:
        result["comps"] = _comps(args, t, warnings, ticker, sector_index, pnl, vals, ind, result, y10, model,
                                 user_peers=A.json.get("peers"))
    rng = result["fair_value_range"]
    result["range_position"] = ve.range_position(price, rng)
    result["upside_downside_pct"] = {"to_low": result["range_position"]["to_low_pct"],
                                     "to_base": result["range_position"]["to_base_pct"],
                                     "to_high": result["range_position"]["to_high_pct"]}
    result.pop("assumptions_tg", None)
    result["assumptions"] = A.items
    result["fundamentals"] = {"period": flows["label"] if flows else None,
                              "period_end": flows.get("period_end") if flows else None,
                              "months": flows["months"] if flows else None,
                              "consolidated": noto.get("consolidated") if noto else None,
                              "annual_values": vals, "annualisation": flows["method"] if flows else {},
                              "indicators": {k: v for k, v in ind.items() if k != "other"},
                              "retrieved_at": snap.get("retrieved_at"), "history_save": hist_status}
    result["warnings"] = warnings + [w for w in snap.get("warnings", [])]
    sources = list(snap["sources"]) + [
        "https://www.gpw.pl/archiwum-notowan (shares + WIG, {} sessions)".format(len(sessions)),
        dam["url"], ratings.get("url", vd.DAMODARAN_RATINGS_URL)]
    if rf_info.get("url"):
        sources.append(rf_info["url"])
    if rf_info.get("detail", {}).get("report"):
        sources.append("gpw-macro-regime: {}".format(rf_info["detail"]["report"]))
    if result["comps"].get("sector_index"):
        sources.append(constituents.PORTFOLIO_URL.format(isin=constituents.SECTOR_INDICES[result["comps"]["sector_index"]][0]))
    result["data_provenance"] = provenance.build(
        as_of=dt.date.fromisoformat(price_date), sources=sources, sessions_used=len(sessions),
        universe_size=1 + len(result["comps"].get("peers", [])),
        notes=["Fundamentals: gpw.pl indicators + Notoria tab ({}), latest period only; not point-in-time.".format(
            flows["label"] if flows else "n/a"),
            "PL 10Y: {} ({}).".format(rf_info["source"], rf_info.get("as_of")),
            "ERP/CRP: Damodaran, updated {} ({}).".format(dam["updated"], dam["source"])],
        reference_date=args.as_of_date if args.as_of else None)
    return result


def _fcff(result, A, t, warnings, args, ind, vals, price, shares, mcap, ke, rf, y10, tax, ratings, rf_block,
          yoy_revenue=None):
    lang = args.lang
    cov = vd.interest_coverage(vals)
    syn = ve.synthetic_spread(cov, ratings["rows"])
    spread = A.take("company_spread", syn["spread"], "Damodaran synthetic rating {} (EBIT / net financial cost = {})".format(
        syn["rating"], "n/a" if cov in (None, float("inf")) else "{:.1f}".format(cov)))
    # Damodaran: kd = rf(default-free) + country default spread + company spread = PL 10Y + company spread.
    kd = A.take("cost_of_debt", y10 + spread, "PL 10Y + company spread (Damodaran)")
    nd_over, nd_src = A.override("net_debt", 1e6)
    if nd_over is not None:
        net_debt = nd_over
        A.set("net_debt", net_debt, nd_src, "pln")
    else:
        proxy = vd.net_debt_proxy(vals, kd, y10)
        if proxy and proxy["value"] is not None:
            net_debt = proxy["value"]
            A.set("net_debt", net_debt, "proxy: net financial cost {} / {} (low confidence)".format(
                "{:,.0f} mln PLN".format(proxy["net_financial_cost"] / 1e6),
                "cost of debt" if proxy["net_financial_cost"] >= 0 else "PL 10Y"), "pln")
            warnings.append(t("w_net_debt_proxy", v=_mln(net_debt, lang)))
        else:
            net_debt = 0.0
            A.set("net_debt", 0.0, "assumed 0 (no data)", "pln")
            warnings.append(t("w_net_debt_none"))
    w = ve.wacc(ke, kd, tax, mcap, net_debt)
    A.set("cost_of_equity", ke, "CAPM: rf + beta × ERP")
    A.set("weight_equity", w["we"], "market cap / (market cap + max(net debt, 0))", "ratio")
    wacc_v = A.take("wacc", w["wacc"], "computed (market-value weights)")
    tg_default = min(ve.DEFAULT_TERMINAL_G, rf)
    if tg_default < ve.DEFAULT_TERMINAL_G:
        warnings.append(t("w_g_capped", rf=pct(rf * 100, 2, lang)))
    tg = A.take("terminal_growth", tg_default, "default: NBP inflation target 2.5% (zero real growth), ≤ rf")
    if tg > rf:
        warnings.append(t("w_g_above_rf", g=pct(tg * 100, 2, lang), rf=pct(rf * 100, 2, lang)))
    ve.check_rates(wacc_v, tg)

    rev = vals.get("revenue")
    ebit = vals.get("operating_profit")
    da = vals.get("depreciation_amortization") or 0.0
    capex = abs(vals.get("capex") or 0.0)
    cfo = vals.get("cfo")
    if not rev or rev <= 0 or ebit is None:
        raise ValuationError("revenue / operating profit missing for {}; FCFF model impossible".format(result["ticker"]))
    fcf_cash = (cfo - capex) if cfo is not None else None
    fcf_norm = ebit * (1 - tax) + da - capex
    f_over, f_src = A.override("fcf", 1e6)
    if f_over is not None:
        fcf0, fcf_src = f_over, f_src
    elif fcf_cash is not None and fcf_cash > 0:
        fcf0, fcf_src = fcf_cash, "CFO − capex (annual)"
    else:
        fcf0, fcf_src = fcf_norm, "EBIT(1−t) + D&A − capex (annual, normalised)"
        if fcf_cash is not None:
            warnings.append(t("w_fcf_fallback", c=_mln(fcf_cash, lang), n=_mln(fcf_norm, lang)))
    A.set("fcf0", fcf0, fcf_src, "pln")
    ev_mkt = mcap + net_debt
    sol = ve.solve_implied_growth(ev_mkt, fcf0, wacc_v, tg)
    reverse = {"enterprise_value_market": ev_mkt, "fcf0": fcf0, "fcf0_source": fcf_src, "fcf_cash": fcf_cash,
               "fcf_normalised": fcf_norm, "years": ve.REVERSE_YEARS, "wacc_pct": wacc_v * 100,
               "terminal_growth_pct": tg * 100, "status": sol["status"],
               "implied_growth_pct": sol["growth"] * 100 if sol["growth"] is not None else None,
               "implied_fcf_no_growth": ve.implied_fcf_no_growth(ev_mkt, wacc_v),
               "fcf_yield_pct": fcf0 / ev_mkt * 100 if ev_mkt > 0 else None}

    # drivers
    hist_g = default_revenue_growth(yoy_revenue)
    g1 = A.take("revenue_growth", hist_g[0], hist_g[1])
    margin = A.take("margin", ebit / rev, "TTM/annualised EBIT / revenue")
    da_pct = A.take("da_pct", da / rev, "TTM/annualised D&A / revenue")
    capex_pct = A.take("capex_pct", capex / rev, "TTM/annualised capex / revenue")
    nwc_raw = ((vals.get("current_assets") or 0) - (vals.get("current_liabilities") or 0)) / rev
    nwc_pct = A.take("nwc_pct", ve.clamp(nwc_raw, 0.0, 0.25),
                     "(current assets − current liabilities) / revenue, clipped 0–25% (includes cash)")
    A.set("revenue0", rev, "annual revenue (Notoria)", "pln")
    if capex_pct < da_pct and tg > 0:
        warnings.append(t("w_capex_below_da", c=pct(capex_pct * 100, 1, lang), d=pct(da_pct * 100, 1, lang)))

    sc_cfg = _scenario_cfg(A.json, ve.SCENARIOS)

    def value(rate, g, growth=g1, m=margin):
        d = ve.driver_dcf(rev, growth, m, da_pct, capex_pct, nwc_pct, tax, rate, g)
        return ve.equity_per_share(d["ev"], net_debt, shares), d

    base_ps, base = value(wacc_v, tg)
    if base_ps is not None and base_ps < 0:
        warnings.append(t("w_negative_value"))
    if base["terminal_share"] and base["terminal_share"] > 0.75:
        warnings.append(t("w_tv_share", s=pct(base["terminal_share"] * 100, 0, lang)))
    if base["implied_ronic"] is not None and (base["implied_ronic"] > 0.5 or base["implied_ronic"] < wacc_v * 0.5):
        warnings.append(t("w_ronic", r=pct(base["implied_ronic"] * 100, 1, lang)))
    scen = {}
    for k in ("bear", "base", "bull"):
        c = sc_cfg[k]
        r_ = wacc_v + c["wacc_pp"] / 100
        g_ = min(tg + c["tg_pp"] / 100, r_ - 0.005)
        gr_ = g1 + c["growth_pp"] / 100
        m_ = margin * c["margin_mult"] if margin > 0 else margin / c["margin_mult"]
        try:
            ps, _ = value(r_, g_, gr_, m_)
        except ve.ValuationInputError:
            ps = None
        scen[k] = {"value_per_share": ps, "wacc_pct": r_ * 100, "terminal_growth_pct": g_ * 100,
                   "revenue_growth_pct": gr_ * 100, "margin_pct": m_ * 100, "shifts": c}
    sens = ve.sensitivity_grid(lambda r, g: value(r, g)[0], wacc_v)
    result.update({
        "implied_growth_pct": reverse["implied_growth_pct"],
        "wacc_pct": wacc_v * 100,
        "fair_value_range": ve.fair_range([scen["bear"]["value_per_share"], base_ps, scen["bull"]["value_per_share"]]),
        "sensitivity": sens["values"],
        "sensitivity_axes": {"rows": "wacc_pct", "row_values": sens["rates_pct"], "cols": "terminal_growth_pct",
                             "col_values": sens["g_pct"]},
        "discount_rate": dict(rf_block, cost_of_debt_pct=kd * 100, synthetic_rating=syn["rating"],
                              interest_coverage=None if cov in (None, float("inf")) else cov, tax_rate_pct=tax * 100,
                              net_debt_pln=net_debt, weight_equity=w["we"], weight_debt=w["wd"],
                              wacc_computed_pct=w["wacc"] * 100, wacc_pct=wacc_v * 100),
        "reverse_dcf": reverse,
        "dcf": {"value_per_share": base_ps, "enterprise_value": base["ev"], "equity_value": base["ev"] - net_debt,
                "pv_explicit": base["pv_explicit"], "terminal_value": base["terminal_value"],
                "pv_terminal": base["pv_terminal"], "terminal_share": base["terminal_share"],
                "terminal_reinvestment_rate": base["terminal_reinvestment_rate"], "implied_ronic": base["implied_ronic"],
                "years": base["years"], "stage1_years": ve.STAGE1_YEARS, "discounting": "end-of-year"},
        "scenarios": scen,
        "assumptions_tg": tg,
    })


def default_revenue_growth(yoy: Optional[float]):
    """Default stage-1 revenue growth: year-on-year YTD growth from the history
    store (clipped), else the nominal-GDP proxy."""
    if yoy is not None:
        g = ve.clamp(yoy, *ve.GROWTH_CLIP)
        return g, "history: revenue YTD y/y {:.1%}, clipped to {:.0%}..{:.0%}".format(yoy, *ve.GROWTH_CLIP)
    return ve.DEFAULT_REVENUE_GROWTH, "default 5%: Polish nominal GDP proxy (2.5% NBP target + ~2.5% real); no y/y history"


def _scenario_cfg(user_json: Dict, defaults: Dict) -> Dict:
    out = {k: dict(v) for k, v in defaults.items()}
    for k, v in (user_json.get("scenarios") or {}).items():
        if not isinstance(v, dict):
            raise ValuationError("scenario {!r} must be an object".format(k))
        bad = set(v) - set(out[k])
        if bad:
            raise ValuationError("unknown scenario fields for {}: {} (allowed: {})".format(
                k, ", ".join(sorted(bad)), ", ".join(sorted(out[k]))))
        out[k].update({kk: float(vv) for kk, vv in v.items()})
    return out


def _bank(result, A, t, warnings, args, ind, vals, price, shares, ke, rf, rf_block):
    lang = args.lang
    ni = vd.pick(vals, "net_profit_parent", "net_profit")
    equity = vd.pick(vals, "equity_parent", "equity") or ind.get("book_value_pln")
    if not equity or equity <= 0:
        raise ValuationError("no book value for {}".format(result["ticker"]))
    A.set("book_value", equity, "Notoria equity (parent) at period end" if vals.get("equity_parent") else "gpw.pl book value", "pln")
    roe_now = ni / equity if (ni is not None) else None
    warnings.append(t("w_bank_minority"))
    roe = A.take("roe", roe_now, "annual net profit (parent) / equity at period end")
    if roe is None:
        raise ValuationError("no net profit for {}; pass --roe".format(result["ticker"]))
    coe_v, coe_src = A.override("coe")
    if coe_v is None:
        coe_v, coe_src = A.override("wacc")
    if coe_v is None:
        coe_v, coe_src = ke, "CAPM: rf + beta × ERP"
    A.set("coe", coe_v, coe_src)
    tg_default = min(ve.DEFAULT_TERMINAL_G, rf)
    tg = A.take("terminal_growth", tg_default, "default: NBP inflation target 2.5% (zero real growth), ≤ rf")
    if tg > rf:
        warnings.append(t("w_g_above_rf", g=pct(tg * 100, 2, lang), rf=pct(rf * 100, 2, lang)))
    ve.check_rates(coe_v, tg, "COE")
    bvps = equity / shares
    pbv_mkt = price / bvps
    sc_cfg = _scenario_cfg(A.json, ve.BANK_SCENARIOS)

    def value(c, g, r=roe):
        return ve.justified_pbv(r, c, g) * bvps

    base_ps = value(coe_v, tg)
    scen = {}
    for k in ("bear", "base", "bull"):
        c = sc_cfg[k]
        c_ = coe_v + c["coe_pp"] / 100
        g_ = min(tg + c["tg_pp"] / 100, c_ - 0.005)
        r_ = roe + c["roe_pp"] / 100
        try:
            ps = value(c_, g_, r_)
        except ve.ValuationInputError:
            ps = None
        scen[k] = {"value_per_share": ps, "coe_pct": c_ * 100, "terminal_growth_pct": g_ * 100, "roe_pct": r_ * 100,
                   "justified_pbv": ps / bvps if ps is not None else None, "shifts": c}
    sens = ve.sensitivity_grid(lambda c, g: value(c, g), coe_v)
    impl_roe = ve.implied_roe(pbv_mkt, coe_v, tg)
    impl_g = ve.implied_bank_growth(pbv_mkt, roe, coe_v)
    result.update({
        "implied_growth_pct": impl_g * 100 if impl_g is not None else None,
        "wacc_pct": coe_v * 100,
        "fair_value_range": ve.fair_range([scen["bear"]["value_per_share"], base_ps, scen["bull"]["value_per_share"]]),
        "sensitivity": sens["values"],
        "sensitivity_axes": {"rows": "cost_of_equity_pct", "row_values": sens["rates_pct"], "cols": "terminal_growth_pct",
                             "col_values": sens["g_pct"]},
        "discount_rate": dict(rf_block, note="bank model: discount rate = cost of equity (no WACC)", wacc_pct=coe_v * 100),
        "reverse_dcf": {"model": "justified_pbv", "market_pbv": pbv_mkt, "implied_roe_pct": impl_roe * 100,
                        "implied_growth_pct": impl_g * 100 if impl_g is not None else None,
                        "current_roe_pct": roe_now * 100 if roe_now is not None else None,
                        "coe_pct": coe_v * 100, "terminal_growth_pct": tg * 100,
                        "note": "implied_growth_pct = g that makes (ROE − g)/(COE − g) equal the market P/BV at the current ROE"},
        "bank_model": {"roe_pct": roe * 100, "coe_pct": coe_v * 100, "terminal_growth_pct": tg * 100,
                       "bvps": bvps, "market_pbv": pbv_mkt, "justified_pbv": base_ps / bvps, "value_per_share": base_ps,
                       "formula": "P/BV = (ROE − g) / (COE − g)"},
        "scenarios": scen,
        "assumptions_tg": tg,
    })


def _ddm(A, t, warnings, args, ind, price, ke, tg, lang) -> Dict:
    dy = ind.get("dividend_yield_pct")
    dps_over, src = A.override("dps_pln", 1.0)
    if dps_over is not None:
        dps0, dsrc = dps_over, src
    elif dy:
        dps0, dsrc = dy / 100.0 * price, "gpw.pl dividend yield {} × price".format(dy)
    else:
        return {"applicable": False, "reason": "no dividend"}
    pe = ind.get("pe")
    payout = (dy / 100.0 * pe) if (dy and pe and pe > 0) else None
    stable = payout is not None and 0.2 <= payout <= 1.0
    if payout is not None and not stable:
        warnings.append(t("w_ddm_unstable", p=pct(payout * 100, 0, lang)))
    try:
        v = ve.ddm_value(dps0, ke, tg)
    except ve.ValuationInputError:
        v = None
    return {"applicable": True, "dps0": dps0, "dps_source": dsrc, "cost_of_equity_pct": ke * 100,
            "terminal_growth_pct": tg * 100, "value_per_share": v, "payout_ratio": payout, "stable_payer": stable,
            "role": "cross-check only (single-stage Gordon DDM)"}


def _comps(args, t, warnings, ticker, sector_index, pnl, vals, ind, result, y10, model, user_peers=None) -> Dict:
    lang = args.lang
    peers_req = [x.strip().upper() for x in (args.peers or "").split(",") if x.strip()] or \
        [x.strip().upper() for x in (user_peers or []) if x.strip()]
    members = []
    if peers_req:
        members = [{"name": p, "isin": None} for p in peers_req if p != ticker]
        src = "user --peers"
    elif sector_index:
        isin = constituents.SECTOR_INDICES[sector_index][0]
        port = constituents.fetch_portfolio(isin, args.cache_dir, args.offline)
        members = sorted([m for m in port["members"] if m["name"] != ticker], key=lambda m: -(m.get("weight_pct") or 0))
        src = "GPW Benchmark {} portfolio".format(sector_index)
    else:
        warnings.append(t("w_no_peers"))
        return {"skipped": True, "reason": "no_sector_index"}
    members = members[:args.max_peers]
    kd_peer = y10 + 0.0089  # A3/A- spread (Damodaran Jan 2026) as a neutral peer cost of debt for the proxy
    rows, failed = [], []
    for m in members:
        try:
            snap = fundamentals.snapshot(m["name"], m.get("isin"), args.cache_dir, args.offline)
        except (fundamentals.FundamentalsError, http.HTTPFetchError):
            failed.append(m["name"])
            continue
        s = pnl.get(m["name"])
        price = s.close[-1] if s is not None else None
        hist = fundamentals.load_history(m["name"])
        rows.append(vd.peer_metrics(snap, price, kd_peer, y10, hist))
    if failed:
        warnings.append(t("w_peer_fail", p=", ".join(failed)))
    if not rows:
        return {"skipped": True, "reason": "no_peer_data", "source": src, "failed": failed}
    kinds = ["pe", "pbv"] if model == "bank" else ["pe", "pbv", "ev_ebitda", "ev_sales"]
    stats = {k: ve.stats_summary([r[k] for r in rows if r[k] is not None]) for k in kinds}
    n_used = max((stats[k]["n"] for k in kinds), default=0)
    if n_used < ve.MIN_PEERS:
        warnings.append(t("w_few_peers", n=n_used, m=ve.MIN_PEERS))
    # target metrics: official trailing earnings / book behind the gpw.pl P/E and P/BV
    mcap_ind = ind.get("market_cap_pln")
    earnings = mcap_ind / ind["pe"] if (mcap_ind and ind.get("pe") and ind["pe"] > 0) else None
    book = mcap_ind / ind["pbv"] if (mcap_ind and ind.get("pbv") and ind["pbv"] > 0) else ind.get("book_value_pln")
    nd = result.get("discount_rate", {}).get("net_debt_pln") or 0.0
    metric = {"pe": earnings, "pbv": book, "ev_ebitda": vals.get("ebitda"), "ev_sales": vals.get("revenue")}
    implied = {k: ve.comps_implied(k, stats[k], metric[k], nd, result["shares"]) for k in kinds}
    target = {"pe": ind.get("pe"), "pbv": ind.get("pbv")}
    if model != "bank":
        ev = result["market_cap_pln"] + nd
        target["ev_ebitda"] = ev / vals["ebitda"] if vals.get("ebitda") else None
        target["ev_sales"] = ev / vals["revenue"] if vals.get("revenue") else None
    bases = [v for v in implied.values() if v and v["base"] is not None]
    summary = {"low": ve.quantile([v["low"] for v in bases if v["low"] is not None], 0.5) if bases else None,
               "base": ve.quantile([v["base"] for v in bases], 0.5) if bases else None,
               "high": ve.quantile([v["high"] for v in bases if v["high"] is not None], 0.5) if bases else None}
    return {"skipped": False, "source": src, "sector_index": sector_index if not peers_req else None,
            "peers": rows, "n_peers": len(rows), "multiples": kinds, "stats": stats, "target_multiples": target,
            "implied_per_share": implied, "implied_range": summary,
            "note": "P/E and P/BV: official gpw.pl indicators; EV multiples: annualised Notoria lines + net-debt proxy"}


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

def _p(x, lang, d=1, signed=False):
    return pct(x, d, lang, signed)


def _z(x, lang, d=2):
    return pln(x, d, lang)


def _mln(x, lang):
    return "—" if x is None else num(x / 1e6, 0, lang) + (" mln zł" if lang == "pl" else " mln PLN")


def render_markdown(r: Dict, lang: str) -> str:
    t = T(dict(STRINGS, **W), lang)
    L: List[str] = []
    fr = r["data_provenance"]["freshness"]
    if fr["is_stale"]:
        L += [t("stale", d=fr["as_of"], n=fr["sessions_behind"]), ""]
    L += ["# " + t("title", t=r["ticker"]), ""]
    L += ["## " + t("answer"), ""]
    price, d = _z(r["price"], lang), r["price_date"]
    rd = r["reverse_dcf"]
    if r["model"] == "bank":
        L.append(t("ans_bank", p=price, d=d, pbv=num(rd["market_pbv"], 2, lang), coe=_p(rd["coe_pct"], lang),
                   tg=_p(rd["terminal_growth_pct"], lang), roe=_p(rd["implied_roe_pct"], lang),
                   roe_now=_p(rd["current_roe_pct"], lang)))
    elif rd["status"] == "not_solvable":
        L.append(t("ans_rdcf_na", f=_mln(rd["fcf0"], lang), p=price, d=d, w=_p(rd["wacc_pct"], lang),
                   nf=_mln(rd["implied_fcf_no_growth"], lang)))
    else:
        L.append(t("ans_rdcf", p=price, d=d, g=_p(rd["implied_growth_pct"], lang), y=rd["years"],
                   tg=_p(rd["terminal_growth_pct"], lang), w=_p(rd["wacc_pct"], lang)))
        if rd["status"] != "ok":
            L.append(t("ans_rdcf_edge", g=_p(rd["implied_growth_pct"], lang)))
    L.append("")
    rng = r["fair_value_range"]
    L.append(t("ans_range", lo=_z(rng["low"], lang), hi=_z(rng["high"], lang), base=_z(rng["base"], lang)))
    pos = r["range_position"]
    L.append(t("pos_" + pos["position"], a=_p(pos["to_low_pct"], lang, signed=True),
               b=_p(pos["to_base_pct"], lang, signed=True), c=_p(pos["to_high_pct"], lang, signed=True)))
    L += ["", "_" + t("not_target") + "_", ""]
    if r["warnings"]:
        L += ["## " + t("warnings"), ""] + ["- " + w for w in r["warnings"]] + [""]
    L += ["## " + t("explain"), "", t("explain_rdcf"), "", t("explain_wacc"), "", t("explain_tv"), ""]
    if r["model"] == "bank":
        L += [t("explain_bank"), ""]

    dr = r["discount_rate"]
    L += ["## " + t("dr"), "", "| {} | {} |".format(t("item"), t("value")), "|---|---:|"]
    rows = [("PL 10Y ({})".format(dr["pl10y_source"]), _p(dr["pl10y_pct"], lang, 2)),
            ("− default spread (Moody's {})".format(dr["damodaran"]["rating"]) if dr["rf_mode"] == "damodaran"
             else "− default spread", _p(dr["default_spread_pct"], lang, 2)),
            ("= rf", _p(dr["rf_pct"], lang, 2)),
            ("beta", num(dr["beta"], 2, lang) + (" (raw {}, R² {}, {} tyg./wk)".format(
                num(dr["beta_regression"]["beta"], 2, lang), num(dr["beta_regression"]["r2"], 2, lang),
                dr["beta_regression"]["weeks"]) if dr.get("beta_regression") else "")),
            ("ERP mature + CRP", "{} + {} = {}".format(_p(dr["mature_erp_pct"], lang, 2), _p(dr["crp_pct"], lang, 2),
                                                      _p(dr["erp_pct"], lang, 2))),
            ("Ke = rf + β × ERP", _p(dr["cost_of_equity_pct"], lang, 2))]
    if r["model"] != "bank":
        rows += [("Kd ({}, {})".format(dr["synthetic_rating"], "pre-tax"), _p(dr["cost_of_debt_pct"], lang, 2)),
                 ("CIT", _p(dr["tax_rate_pct"], lang, 0)),
                 ("E / (D+E)", _p(dr["weight_equity"] * 100, lang, 1)),
                 ("**WACC**", "**" + _p(dr["wacc_pct"], lang, 2) + "**")]
    L += ["| {} | {} |".format(a, b) for a, b in rows] + [""]

    if r["model"] == "bank":
        b = r["bank_model"]
        L += ["## " + t("bank"), "", "| {} | {} |".format(t("item"), t("value")), "|---|---:|",
              "| ROE | {} |".format(_p(b["roe_pct"], lang)), "| COE | {} |".format(_p(b["coe_pct"], lang)),
              "| g | {} |".format(_p(b["terminal_growth_pct"], lang)),
              "| BVPS | {} |".format(_z(b["bvps"], lang)),
              "| P/BV rynkowe / market | {} |".format(num(b["market_pbv"], 2, lang)),
              "| P/BV uzasadnione / justified | {} |".format(num(b["justified_pbv"], 2, lang)),
              "| ROE implikowane / implied | {} |".format(_p(rd["implied_roe_pct"], lang)),
              "| g implikowane / implied | {} |".format(_p(rd["implied_growth_pct"], lang)),
              "| **{}** | **{}** |".format("Wartość na akcję" if lang == "pl" else "Value per share", _z(b["value_per_share"], lang)), ""]
    else:
        L += ["## " + t("rdcf"), "", "| {} | {} |".format(t("item"), t("value")), "|---|---:|",
              "| EV = kapitalizacja + dług netto / market cap + net debt | {} |".format(_mln(rd["enterprise_value_market"], lang)),
              "| FCF0 ({}) | {} |".format(rd["fcf0_source"], _mln(rd["fcf0"], lang)),
              "| FCF yield (FCF0 / EV) | {} |".format(_p(rd["fcf_yield_pct"], lang)),
              "| WACC / g | {} / {} |".format(_p(rd["wacc_pct"], lang, 2), _p(rd["terminal_growth_pct"], lang, 2)),
              "| **Implikowany wzrost FCF / implied FCF growth ({} lat/yrs)** | **{}** |".format(rd["years"], _p(rd["implied_growth_pct"], lang)),
              "| FCF bez wzrostu uzasadniający cenę / zero-growth FCF priced in | {} |".format(_mln(rd["implied_fcf_no_growth"], lang)), ""]
        dc = r["dcf"]
        L += ["## " + t("dcf"), "", "| {} | {} | {} | {} | {} | PV |".format(t("year"), "Przychody/Revenue", "NOPAT", "FCFF", "DF"),
              "|---:|---:|---:|---:|---:|---:|"]
        for y in dc["years"]:
            L.append("| {} | {} | {} | {} | {} | {} |".format(y["year"], _mln(y["revenue"], lang), _mln(y["nopat"], lang),
                                                             _mln(y["fcff"], lang), num(y["discount_factor"], 3, lang), _mln(y["pv"], lang)))
        L += ["", "| {} | {} |".format(t("item"), t("value")), "|---|---:|",
              "| PV FCFF 1-{} | {} |".format(dc["stage1_years"], _mln(dc["pv_explicit"], lang)),
              "| TV = FCFF{} / (WACC − g) | {} |".format(dc["stage1_years"] + 1, _mln(dc["terminal_value"], lang)),
              "| PV TV ({} EV) | {} |".format(_p((dc["terminal_share"] or 0) * 100, lang, 0), _mln(dc["pv_terminal"], lang)),
              "| EV | {} |".format(_mln(dc["enterprise_value"], lang)),
              "| − dług netto / net debt | {} |".format(_mln(r["discount_rate"]["net_debt_pln"], lang)),
              "| **{}** | **{}** |".format("Wartość na akcję" if lang == "pl" else "Value per share", _z(dc["value_per_share"], lang)),
              "| RONIC implikowany w TV / implied | {} |".format(_p((dc["implied_ronic"] or 0) * 100, lang) if dc["implied_ronic"] else "—"), ""]

    ax = r["sensitivity_axes"]
    rowname = "WACC" if ax["rows"] == "wacc_pct" else "COE"
    L += ["## " + t("sens", r=rowname), "",
          "| {} \\ g | {} |".format(rowname, " | ".join(_p(g, lang, 0) for g in ax["col_values"])),
          "|---:|" + "---:|" * len(ax["col_values"])]
    for rv, row in zip(ax["row_values"], r["sensitivity"]):
        L.append("| {} | {} |".format(_p(rv, lang, 1), " | ".join(_z(v, lang, 0) if v is not None else "—" for v in row)))
    L.append("")

    L += ["## " + t("scen"), ""]
    if r["model"] == "bank":
        L += ["| | ROE | COE | g | P/BV | {} |".format(t("value")), "|---|---:|---:|---:|---:|---:|"]
        for k, s in r["scenarios"].items():
            L.append("| {} | {} | {} | {} | {} | {} |".format(k, _p(s["roe_pct"], lang), _p(s["coe_pct"], lang),
                                                          _p(s["terminal_growth_pct"], lang), num(s["justified_pbv"], 2, lang),
                                                          _z(s["value_per_share"], lang)))
    else:
        L += ["| | {} | {} | WACC | g | {} |".format("wzrost/growth", "marża/margin", t("value")), "|---|---:|---:|---:|---:|---:|"]
        for k, s in r["scenarios"].items():
            L.append("| {} | {} | {} | {} | {} | {} |".format(k, _p(s["revenue_growth_pct"], lang), _p(s["margin_pct"], lang),
                                                          _p(s["wacc_pct"], lang), _p(s["terminal_growth_pct"], lang),
                                                          _z(s["value_per_share"], lang)))
    L.append("")

    c = r["comps"]
    if not c.get("skipped"):
        L += ["## " + t("comps"), "", "{}: {} ({})".format(t("source"), c["source"], c["n_peers"]), "",
              "| Ticker | " + " | ".join(k.upper().replace("_", "/") for k in c["multiples"]) + " |",
              "|---|" + "---:|" * len(c["multiples"])]
        for p_ in c["peers"]:
            L.append("| {} | {} |".format(p_["ticker"], " | ".join(num(p_[k], 1, lang) for k in c["multiples"])))
        L.append("| **{}** | {} |".format(r["ticker"], " | ".join(num(c["target_multiples"].get(k), 1, lang) for k in c["multiples"])))
        L.append("| mediana / median | {} |".format(" | ".join(num(c["stats"][k]["median"], 1, lang) for k in c["multiples"])))
        L.append("| Q1–Q3 | {} |".format(" | ".join("{}–{}".format(num(c["stats"][k]["q1"], 1, lang), num(c["stats"][k]["q3"], 1, lang))
                                                     for k in c["multiples"])))
        imp = c["implied_per_share"]
        L.append("| {} | {} |".format("wartość/akcję (Q1/med/Q3)" if lang == "pl" else "value/share (Q1/med/Q3)",
                                      " | ".join("{} / {} / {}".format(_z(imp[k]["low"], lang, 0), _z(imp[k]["base"], lang, 0),
                                                                       _z(imp[k]["high"], lang, 0)) if imp.get(k) else "—"
                                                 for k in c["multiples"])))
        L += ["", "_" + c["note"] + "_", ""]

    dd = r.get("ddm") or {}
    if dd.get("applicable"):
        L += ["## " + t("ddm"), "", "DPS0 {} ({}), COE {}, g {} → **{}**{}".format(
            _z(dd["dps0"], lang), dd["dps_source"], _p(dd["cost_of_equity_pct"], lang), _p(dd["terminal_growth_pct"], lang),
            _z(dd["value_per_share"], lang),
            "" if dd["stable_payer"] else (" — " + ("niestabilna wypłata, tylko orientacyjnie" if lang == "pl" else "unstable payout, indicative only"))), ""]

    L += ["## " + t("assump"), "", "| {} | {} | {} |".format(t("item"), t("value"), t("source")), "|---|---:|---|"]
    for k, a in r["assumptions"].items():
        lab = ASSUMPTION_LABELS.get(k, (k, k))[0 if lang == "pl" else 1]
        v = a["value"]
        if a["unit"] == "pct":
            vs = _p(v * 100, lang, 2)
        elif a["unit"] == "pln":
            vs = _z(v, lang) if k == "price" else _mln(v, lang)
        elif a["unit"] == "ratio":
            vs = _p(v * 100, lang, 1)
        elif a["unit"] == "count":
            vs = num(v, 0, lang)
        else:
            vs = num(v, 2, lang)
        L.append("| {} | {} | {} |".format(lab, vs, a["source"]))
    L.append("")
    L += ["## " + t("sources"), ""] + ["- " + s for s in r["data_provenance"]["sources"]]
    L += ["- Fundamentals period: {} (retrieved {})".format(r["fundamentals"]["period"], r["fundamentals"]["retrieved_at"]),
          "", "---", "_" + DISCLAIMER[lang] + "_", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except (ValuationError, ve.ValuationInputError, vd.DataError, reports.ReportError,
            fundamentals.FundamentalsError, gpw_archive.GPWFetchError, http.HTTPFetchError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    md = render_markdown(result, args.lang)
    if args.no_files:
        print(md)
        return 0
    out = Path(args.output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    jp, mp = out / "{}_{}_{}.json".format(PREFIX, result["ticker"], stamp), out / "{}_{}_{}.md".format(PREFIX, result["ticker"], stamp)
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    mp.write_text(md, encoding="utf-8")
    rng = result["fair_value_range"]
    print("{} [{}] price {:.2f} | implied growth {} | discount rate {:.2f}% | range {} / {} / {} | {}".format(
        result["ticker"], result["model"], result["price"],
        "n/a" if result["implied_growth_pct"] is None else "{:.1f}%".format(result["implied_growth_pct"]),
        result["wacc_pct"], *("n/a" if v is None else "{:.2f}".format(v) for v in (rng["low"], rng["base"], rng["high"])),
        result["range_position"]["position"]))
    print("JSON: {}\nMarkdown: {}".format(jp, mp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
