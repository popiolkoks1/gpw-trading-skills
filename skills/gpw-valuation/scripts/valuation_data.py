"""Data layer for gpw-valuation: parsers (pure) and fetchers (gpwlib.http).

Sources (each fetched and checked on 2026-11-16):

* Damodaran, "Country Default Spreads and Risk Premiums" (updated yearly in
  January): https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html
  Columns: Country, Moody's rating, Adj. Default Spread, Country Risk Premium,
  Equity Risk Premium, Corporate Tax Rate, Sovereign CDS, ERP based on CDS.
* Damodaran, "Ratings, Interest Coverage Ratios and Default Spread":
  https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html
  (large non-financial firms table; first four cells of each row).
  robots.txt of pages.stern.nyu.edu disallows one unrelated folder only.
* PL 10Y yield: the gpw-macro-regime JSON (``indicators.pl10y``) when present;
  otherwise BondSpot daily fixing (https://www.bondspot.pl/fixing_obligacji,
  the fixed-rate bond nearest 10 years) and, as a last resort, the ECB
  monthly convergence yield (IRS M.PL.L.L40.CI.0000.PLN.N.Z). The BondSpot
  and ECB parsers follow skills/gpw-macro-regime/scripts/macro_sources.py
  (skills never import each other, so the few lines are repeated here).
* Company data: gpwlib.fundamentals (gpw.pl indicators + Notoria tabs) and the
  local quarterly history store (fundamentals.save_snapshot / load_history).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from html import unescape
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from gpwlib import fundamentals, http

import valuation_engine as ve

DAMODARAN_CTRYPREM_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html"
DAMODARAN_RATINGS_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html"
BONDSPOT_FIXING_URL = "https://www.bondspot.pl/fixing_obligacji"
ECB_PL10Y_URL = "https://data-api.ecb.europa.eu/service/data/IRS/M.PL.L.L40.CI.0000.PLN.N.Z"

# Damodaran updates both tables once a year (early January); a week of cache
# is plenty and keeps the run polite.
TTL_DAMODARAN = 7 * 86400
TTL_DAILY = 3 * 3600
TTL_MONTHLY = 24 * 3600
# Warn when the Damodaran table is more than a year old (he republishes every
# January; older figures miss a rating change or a new ERP estimate).
DAMODARAN_MAX_AGE_DAYS = 365

# Embedded fallback, verified on 2026-11-16 against the live pages above
# ("Last updated: January 5, 2026"; ratings "Data used is as of January 2026").
EMBEDDED_POLAND = {
    "country": "Poland", "rating": "A2", "default_spread": 0.0072, "crp": 0.0110, "erp": 0.0533,
    "tax_rate": 0.19, "cds_spread": 0.0086, "erp_cds": 0.0554, "updated": "2026-01-05",
    "mature_erp": 0.0423, "source": "embedded", "url": DAMODARAN_CTRYPREM_URL,
}
EMBEDDED_RATINGS = {
    "updated": "2026-01-01", "source": "embedded", "url": DAMODARAN_RATINGS_URL,
    "rows": [
        {"lo": -100000.0, "hi": 0.199999, "rating": "D2/D", "spread": 0.19},
        {"lo": 0.2, "hi": 0.649999, "rating": "C2/C", "spread": 0.16},
        {"lo": 0.65, "hi": 0.799999, "rating": "Ca2/CC", "spread": 0.1261},
        {"lo": 0.8, "hi": 1.249999, "rating": "Caa/CCC", "spread": 0.0885},
        {"lo": 1.25, "hi": 1.499999, "rating": "B3/B-", "spread": 0.0509},
        {"lo": 1.5, "hi": 1.749999, "rating": "B2/B", "spread": 0.0321},
        {"lo": 1.75, "hi": 1.999999, "rating": "B1/B+", "spread": 0.0275},
        {"lo": 2.0, "hi": 2.2499999, "rating": "Ba2/BB", "spread": 0.0184},
        {"lo": 2.25, "hi": 2.49999, "rating": "Ba1/BB+", "spread": 0.0138},
        {"lo": 2.5, "hi": 2.999999, "rating": "Baa2/BBB", "spread": 0.0111},
        {"lo": 3.0, "hi": 4.249999, "rating": "A3/A-", "spread": 0.0089},
        {"lo": 4.25, "hi": 5.499999, "rating": "A2/A", "spread": 0.0078},
        {"lo": 5.5, "hi": 6.499999, "rating": "A1/A+", "spread": 0.0070},
        {"lo": 6.5, "hi": 8.499999, "rating": "Aa2/AA", "spread": 0.0055},
        {"lo": 8.5, "hi": 100000.0, "rating": "Aaa/AAA", "spread": 0.0040},
    ],
}

_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}


class DataError(RuntimeError):
    """A required input could not be obtained."""


# --------------------------------------------------------------------------
# HTML table helpers
# --------------------------------------------------------------------------

_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TD = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)


def _cells(row_html: str) -> List[str]:
    return [" ".join(unescape(re.sub(r"<[^>]+>", " ", c)).replace("\xa0", " ").split()) for c in _TD.findall(row_html)]


def _pct(text: str) -> Optional[float]:
    t = (text or "").strip().replace(",", ".")
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*%$", t)
    return float(m.group(1)) / 100.0 if m else None


def _num(text: str) -> Optional[float]:
    t = (text or "").strip().replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


def pl_float(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    s = str(text).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if s in ("", "-", ".", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Damodaran
# --------------------------------------------------------------------------

def parse_updated_date(html: str) -> Optional[dt.date]:
    text = " ".join(unescape(re.sub(r"<[^>]+>", " ", html or "")).split())
    m = re.search(r"Last updated:\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text)
    if m and m.group(1).lower() in _MONTHS:
        return dt.date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
    m = re.search(r"as of\s+([A-Za-z]+)\s+(\d{4})", text)
    if m and m.group(1).lower() in _MONTHS:
        return dt.date(int(m.group(2)), _MONTHS[m.group(1).lower()], 1)
    return None


def parse_ctryprem(html: str, country: str = "Poland") -> Optional[Dict]:
    """Country row of Damodaran's ctryprem table (decimals) or None."""
    for row in _TR.findall(html or ""):
        c = _cells(row)
        if len(c) >= 6 and c[0].strip().lower() == country.lower():
            ds, crp, erp, tax = _pct(c[2]), _pct(c[3]), _pct(c[4]), _pct(c[5])
            if ds is None or crp is None or erp is None:
                continue
            upd = parse_updated_date(html)
            return {"country": c[0], "rating": c[1], "default_spread": ds, "crp": crp, "erp": erp,
                    "tax_rate": tax, "cds_spread": _pct(c[6]) if len(c) > 6 else None,
                    "erp_cds": _pct(c[7]) if len(c) > 7 else None,
                    "mature_erp": erp - crp, "updated": upd.isoformat() if upd else None,
                    "url": DAMODARAN_CTRYPREM_URL}
    return None


def parse_ratings(html: str) -> List[Dict]:
    """Large non-financial firms: coverage band -> rating, spread."""
    out = []
    for row in _TR.findall(html or ""):
        c = _cells(row)
        if len(c) < 4:
            continue
        lo, hi, rating, spread = _num(c[0]), _num(c[1]), c[2], _pct(c[3])
        if lo is None or hi is None or spread is None or "/" not in rating:
            continue
        out.append({"lo": lo, "hi": hi, "rating": rating, "spread": spread})
    return sorted(out, key=lambda r: r["lo"])


def damodaran_poland(as_of: dt.date, cache_dir=None, offline: bool = False, getter: Callable = None) -> Dict:
    """Poland row: live (cached a week) or the embedded copy, with an age warning."""
    get = getter or http.get
    res, err = None, None
    try:
        r = get(DAMODARAN_CTRYPREM_URL, None, cache_dir=cache_dir, ttl_seconds=TTL_DAMODARAN, offline=offline)
        res = parse_ctryprem(r.text)
        if res:
            res["source"] = "live_cache" if r.from_cache else "live"
            res["retrieved_at"] = r.retrieved_at
        else:
            err = "Poland row not found in the Damodaran table"
    except http.HTTPFetchError as exc:
        err = str(exc)
    if res is None:
        res = dict(EMBEDDED_POLAND)
        res["fetch_error"] = err
    upd = dt.date.fromisoformat(res["updated"]) if res.get("updated") else None
    res["age_days"] = (as_of - upd).days if upd else None
    res["stale"] = upd is None or res["age_days"] > DAMODARAN_MAX_AGE_DAYS
    return res


def damodaran_ratings(cache_dir=None, offline: bool = False, getter: Callable = None) -> Dict:
    get = getter or http.get
    try:
        r = get(DAMODARAN_RATINGS_URL, None, cache_dir=cache_dir, ttl_seconds=TTL_DAMODARAN, offline=offline)
        rows = parse_ratings(r.text)
        if len(rows) >= 10:
            upd = parse_updated_date(r.text)
            return {"rows": rows, "source": "live_cache" if r.from_cache else "live", "url": DAMODARAN_RATINGS_URL,
                    "updated": upd.isoformat() if upd else None}
    except http.HTTPFetchError:
        pass
    return dict(EMBEDDED_RATINGS)


# --------------------------------------------------------------------------
# PL 10Y yield
# --------------------------------------------------------------------------

_BS_DATE = re.compile(r"FIXING[^<]*Data:\s*(\d{4}-\d{2}-\d{2})")
_BS_ROW = re.compile(r"<tr>\s*<td>\s*\d+\s*</td>\s*<td>\s*([A-Z]{2}\d{4})\s*</td>\s*<td>\s*(PL[0-9A-Z]{10})\s*</td>(.*?)</tr>",
                     re.S)
_BS_CELL = re.compile(r"<td[^>]*>\s*(.*?)\s*</td>", re.S)
FIXED_PREFIXES = ("OK", "PS", "DS", "WS")  # fixed-coupon / zero-coupon Treasury bonds


def bond_maturity(name: str) -> Optional[dt.date]:
    """'DS0436' -> 2036-04-25 (Polish Treasury bonds mature on the 25th)."""
    m = re.match(r"^[A-Z]{2}(\d{2})(\d{2})$", name or "")
    if not m or not 1 <= int(m.group(1)) <= 12:
        return None
    return dt.date(2000 + int(m.group(2)), int(m.group(1)), 25)


def parse_bondspot_fixing(html: str) -> Dict:
    m = _BS_DATE.search(html or "")
    bonds = []
    for name, isin, rest in _BS_ROW.findall(html or ""):
        cells = [unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in _BS_CELL.findall(rest)]
        if len(cells) < 6:  # bid/ask price, bid/ask yield, fixing price, fixing yield
            continue
        bonds.append({"name": name, "isin": isin, "yield": pl_float(cells[5]), "maturity": bond_maturity(name),
                      "fixed": name[:2] in FIXED_PREFIXES})
    try:
        day = dt.date.fromisoformat(m.group(1)) if m else None
    except ValueError:
        day = None
    return {"date": day, "bonds": bonds}


def pick_10y(bonds: Sequence[Dict], on: dt.date, max_gap_years: float = 2.0) -> Optional[Dict]:
    """Fixed-rate bond with remaining maturity nearest 10 years (gap <= 2y,
    the same tolerance gpw-macro-regime uses)."""
    best = None
    for b in bonds:
        if not b.get("fixed") or b.get("yield") is None or b.get("maturity") is None:
            continue
        tenor = (b["maturity"] - on).days / 365.25
        if abs(tenor - 10.0) > max_gap_years:
            continue
        key = (abs(tenor - 10.0), b["isin"])
        if best is None or key < best[0]:
            best = (key, dict(b, tenor_years=round(tenor, 2)))
    return best[1] if best else None


def parse_ecb_csv(text: str) -> List[Tuple[str, float]]:
    reader = csv.DictReader(io.StringIO((text or "").lstrip("﻿")))
    if not reader.fieldnames or "TIME_PERIOD" not in reader.fieldnames or "OBS_VALUE" not in reader.fieldnames:
        return []
    out = []
    for row in reader:
        k, v = (row.get("TIME_PERIOD") or "").strip(), pl_float(row.get("OBS_VALUE"))
        if re.match(r"^\d{4}-\d{2}$", k) and v is not None:
            out.append((k, v))
    return sorted(out)


def rf_from_macro(rep: Optional[Dict]) -> Optional[Dict]:
    """``indicators.pl10y`` of a gpw-macro-regime report -> {yield, as_of, source, detail}."""
    if not rep:
        return None
    ind = (rep.get("data") or {}).get("indicators") or {}
    p = ind.get("pl10y") or ind.get("pl10y_monthly_ecb")
    if not p or p.get("value") is None:
        return None
    return {"yield": float(p["value"]) / 100.0, "as_of": p.get("as_of"),
            "source": "gpw-macro-regime ({})".format(p.get("source")),
            "detail": {"report": rep.get("file"), "bond": p.get("bond"), "tenor_years": p.get("tenor_years")}}


def rf_live(as_of: dt.date, today: dt.date, cache_dir=None, offline: bool = False, getter: Callable = None) -> Dict:
    """BondSpot fixing (newest weekday on/before as_of, 7 days back), else ECB monthly."""
    get = getter or http.get
    errors = []
    for k in range(8):
        d = as_of - dt.timedelta(days=k)
        if d.weekday() >= 5 or d > today:
            continue
        params = {"date": d.strftime("%Y%m%d"), "month": d.month, "year": d.year}
        try:
            r = get(BONDSPOT_FIXING_URL, params, cache_dir=cache_dir,
                    ttl_seconds=None if d < today else TTL_DAILY, offline=offline)
        except http.OfflineMiss:
            continue
        except http.HTTPFetchError as exc:
            errors.append("BondSpot: {}".format(exc))
            break
        parsed = parse_bondspot_fixing(r.text)
        b = pick_10y(parsed["bonds"], parsed["date"] or d)
        if b:
            return {"yield": b["yield"] / 100.0, "as_of": (parsed["date"] or d).isoformat(),
                    "source": "BondSpot fixing", "url": http.build_url(BONDSPOT_FIXING_URL, params),
                    "detail": {"bond": b["name"], "isin": b["isin"], "tenor_years": b["tenor_years"]}}
    try:
        r = get(ECB_PL10Y_URL, {"format": "csvdata", "startPeriod": "{}-01".format(as_of.year - 1)},
                cache_dir=cache_dir, ttl_seconds=TTL_MONTHLY, offline=offline)
        series = [s for s in parse_ecb_csv(r.text) if s[0] <= as_of.strftime("%Y-%m")]
        if series:
            return {"yield": series[-1][1] / 100.0, "as_of": series[-1][0], "source": "ECB IRS (monthly)",
                    "url": ECB_PL10Y_URL + "?format=csvdata", "detail": {}}
        errors.append("ECB: no observations")
    except http.HTTPFetchError as exc:
        errors.append("ECB: {}".format(exc))
    raise DataError("PL 10Y yield unavailable ({}); pass --rf or a gpw-macro-regime JSON".format("; ".join(errors)
                                                                                             or "offline cache empty"))


# --------------------------------------------------------------------------
# Fundamentals: TTM / annualisation
# --------------------------------------------------------------------------

def _find_period(periods: Dict, year: int, first_q: int, last_q: int) -> Optional[Dict]:
    for label, e in periods.items():
        p = e.get("period") or fundamentals.parse_period_label(label)
        if p.get("year") == year and p.get("first_quarter") == first_q and p.get("last_quarter") == last_q:
            return dict(e, label=label)
    return None


def annual_flows(noto: Dict, history: Optional[Dict]) -> Dict:
    """Annual (12-month) flow values from the latest Notoria period.

    Order: full year as reported -> TTM from the history store
    (YTD + previous FY - previous YTD) -> annualised YTD (x 12/months).
    Balance-sheet lines are copied unchanged (period-end).
    Returns {values, method{key: ...}, months, label, prev_ytd_label, fy_label, yoy{key: growth}}.
    """
    p = noto.get("period") or fundamentals.parse_period_label(noto.get("period_label", ""))
    vals = noto.get("values") or {}
    months = p.get("months")
    periods = (history or {}).get("periods", {})
    out, method, yoy = {}, {}, {}
    prev_ytd = fy = None
    if p and p.get("first_quarter") == 1 and months and months < 12:
        prev_ytd = _find_period(periods, p["year"] - 1, 1, p["last_quarter"])
        fy = _find_period(periods, p["year"] - 1, 1, 4)
    for k, v in vals.items():
        if v is None:
            continue
        if k not in fundamentals.FLOW_ITEMS:
            out[k], method[k] = v, "balance_sheet"
            continue
        if months == 12:
            out[k], method[k] = v, "full_year"
        elif prev_ytd and fy and k in prev_ytd.get("values", {}) and k in fy.get("values", {}):
            out[k] = v + fy["values"][k] - prev_ytd["values"][k]
            method[k] = "ttm_history"
        elif months:
            out[k] = v * 12.0 / months
            method[k] = "annualised_ytd"
        if prev_ytd and k in prev_ytd.get("values", {}) and prev_ytd["values"][k]:
            base = prev_ytd["values"][k]
            if base > 0:
                yoy[k] = v / base - 1.0
    return {"values": out, "method": method, "months": months, "label": noto.get("period_label"),
            "period_end": p.get("period_end"), "prev_ytd_label": prev_ytd["label"] if prev_ytd else None,
            "fy_label": fy["label"] if fy else None, "yoy": yoy}


def pick(values: Dict, *keys) -> Optional[float]:
    for k in keys:
        if values.get(k) is not None:
            return values[k]
    return None


def is_financial(sector_text: Optional[str], sector_index: Optional[str]) -> bool:
    """Banks / insurers use the excess-return model, not FCFF."""
    s = (sector_text or "").lower()
    return sector_index == "WIG-BANKI" or "bank" in s or "ubezpiecz" in s


def net_financial_cost(values: Dict) -> Optional[float]:
    """Annual net financial cost = operating profit - pre-tax profit (positive = net cost).

    Includes interest, FX and associates; used only as a proxy when the
    balance sheet gives no debt split (the Notoria tab has none)."""
    op, pt = values.get("operating_profit"), values.get("pretax_profit")
    if op is None or pt is None:
        return None
    return op - pt


def net_debt_proxy(values: Dict, kd_pre_tax: float, cash_yield: float) -> Optional[Dict]:
    """Net debt estimated from the net financial result (Notoria has no debt split).

    net cost > 0 -> net debt = cost / kd; net income -> net cash = income / cash_yield.
    Capped by total liabilities (debt) or current assets (cash). Low confidence.
    """
    nfc = net_financial_cost(values)
    if nfc is None:
        return None
    if nfc >= 0:
        nd = nfc / kd_pre_tax if kd_pre_tax > 0 else None
        cap = (values.get("noncurrent_liabilities") or 0) + (values.get("current_liabilities") or 0)
        if nd is not None and cap > 0:
            nd = min(nd, cap)
    else:
        nd = nfc / cash_yield if cash_yield > 0 else None
        cap = values.get("current_assets")
        if nd is not None and cap:
            nd = max(nd, -cap)
    return {"value": nd, "net_financial_cost": nfc, "method": "proxy_net_financial_cost"}


def interest_coverage(values: Dict) -> Optional[float]:
    nfc = net_financial_cost(values)
    ebit = values.get("operating_profit")
    if nfc is None or ebit is None:
        return None
    if nfc <= 0:
        return float("inf")
    return ebit / nfc


# --------------------------------------------------------------------------
# Beta from the price panel
# --------------------------------------------------------------------------

def beta_from_series(stock_dates, stock_closes, mkt_dates, mkt_closes) -> Optional[Dict]:
    rs, rm = ve.weekly_returns_aligned(ve.weekly_closes(stock_dates, stock_closes),
                                       ve.weekly_closes(mkt_dates, mkt_closes))
    reg = ve.ols_beta(rs, rm)
    if reg is None:
        return None
    reg.update(ve.adjusted_beta(reg["beta"]))
    reg["weeks"] = len(rs)
    return reg


# --------------------------------------------------------------------------
# Peers
# --------------------------------------------------------------------------

def peer_metrics(snap: Dict, price: Optional[float], kd_pre_tax: float, cash_yield: float,
                 history: Optional[Dict] = None) -> Dict:
    """Multiples of one peer from its snapshot (P/E, P/BV official from gpw.pl;
    EV multiples from annualised Notoria lines + net-debt proxy)."""
    ind = snap.get("indicators") or {}
    shares = ind.get("shares_issued")
    mcap = price * shares if (price and shares) else ind.get("market_cap_pln")
    out = {"ticker": snap["ticker"], "isin": snap.get("isin"), "market_cap": mcap, "pe": ind.get("pe"),
           "pbv": ind.get("pbv"), "ev_ebitda": None, "ev_sales": None, "period": None}
    noto = snap.get("notoria")
    # Foreign issuers (non-PL ISIN, e.g. MOL) report in their own currency and
    # their market value on gpw.pl may not match the Notoria units, so only the
    # currency-neutral official P/E and P/BV are used for them.
    out["foreign_issuer"] = bool(snap.get("isin")) and not str(snap.get("isin")).startswith("PL")
    if noto and mcap and not out["foreign_issuer"]:
        fl = annual_flows(noto, history)
        v = fl["values"]
        nd = net_debt_proxy(v, kd_pre_tax, cash_yield)
        ev = mcap + ((nd or {}).get("value") or 0.0)
        out["period"] = fl["label"]
        if v.get("ebitda"):
            out["ev_ebitda"] = ev / v["ebitda"] if v["ebitda"] > 0 else None
        if v.get("revenue"):
            out["ev_sales"] = ev / v["revenue"] if v["revenue"] > 0 else None
    for k in ("pe", "pbv", "ev_ebitda", "ev_sales"):
        if not ve.valid_multiple(k, out[k]):
            out[k] = None
    return out
