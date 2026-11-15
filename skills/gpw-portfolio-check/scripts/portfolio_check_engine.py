"""gpw-portfolio-check - pure engine (no I/O, no network).

Takes holdings plus price series and returns every number of the report:
values, weights, unrealised P&L, sector / size buckets, concentration (HHI),
correlations, volatility, beta, historical VaR / ES, drawdown, comparison with
WIG, liquidity (days to exit) and open risk to stops ("portfolio heat").

Every threshold lives in ``THRESHOLDS`` and is justified in
``references/methodology.md``. Nothing here says "sell": positions are only
marked OK or REVIEW with reasons.
"""

from __future__ import annotations

import csv
import difflib
import io
import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from gpwlib import stats
from gpwlib.indicators import median
from gpwlib.tax import BELKA_RATE

THRESHOLDS = {
    # Single position > 20% of the portfolio. Common retail / fund risk rule
    # (UCITS 5/10/40 caps a single issuer at 10%, 20% for index-tracking funds;
    # O'Neil and Minervini both cap a single stock at ~20-25% for concentrated
    # traders). 20% = "one bad stock can cost a fifth of everything".
    "max_position_pct": 20.0,
    # Sector > 35% of the portfolio: same order of magnitude as UCITS 35% cap
    # for a single issuer of government debt and the gpw-pre-trade-gate limit.
    "max_sector_pct": 35.0,
    # HHI on position weights (0-1 scale). US DOJ/FTC Merger Guidelines 2023:
    # > 1 800 (0.18) highly concentrated, 1 000-1 800 moderately concentrated.
    "hhi_moderate": 0.10,
    "hhi_high": 0.18,
    # Liquidity: assume you can sell at most 10% of a typical (median) day's
    # turnover without moving the price much (common participation cap in
    # execution and fund-liquidity practice; same default as gpw-position-sizer
    # --max-adv-pct). More than 5 sessions (one trading week) to exit = illiquid.
    "participation_pct": 10.0,
    "max_days_to_exit": 5.0,
    "liquidity_window": 60,  # ~one quarter of sessions: structural, not one-week liquidity
    # Daily-return correlation above 0.7 = the two holdings largely move
    # together (conventional "strong" correlation cut-off).
    "corr_high": 0.70,
    # 1 year of GPW sessions; fewer than 60 returns (~3 months) is too short
    # for a meaningful VaR / correlation, so such holdings are left out of risk stats.
    "lookback": 252,
    "min_risk_returns": 60,
    "var_level": 0.95,
    "var_horizon": 20,  # ~one month of sessions for the sqrt-time scaling
    # A quote older than 5 sessions usually means suspension or a very thin market.
    "stale_quote_sessions": 5,
    # Last price below 1/5 or above 5x the average purchase price: unusual for a
    # real position and typical of a split / reverse split the broker applied
    # to the share count but not to the price in the export (or a typo).
    "avg_price_ratio_low": 0.2,
    "avg_price_ratio_high": 5.0,
}

ACCOUNT_ALIASES = {
    "regular": "regular", "zwykly": "regular", "zwykły": "regular", "standard": "regular",
    "standardowy": "regular", "maklerski": "regular", "rm": "regular", "normalny": "regular",
    "ike": "ike", "ikze": "ikze", "oki": "oki",
}

HEADER_ALIASES = {
    "ticker": {"ticker", "symbol", "walor", "instrument", "nazwa", "papier", "spolka", "spółka",
               "name", "kod", "nazwa_waloru", "skrot", "skrót"},
    "shares": {"shares", "ilosc", "ilość", "liczba", "liczba_akcji", "quantity", "qty", "szt",
               "sztuk", "ilosc_szt", "ilość_szt", "liczba_sztuk"},
    "avg_price": {"avg_price", "cena", "srednia_cena", "średnia_cena", "cena_srednia", "cena_średnia",
                  "kurs_sredni", "kurs_średni", "avg", "price", "cena_zakupu", "average_price",
                  "sredni_kurs", "średni_kurs", "cena_nabycia"},
    "account_type": {"account_type", "rachunek", "konto", "account", "typ_rachunku", "typ_konta"},
    "stop": {"stop", "stop_loss", "sl", "stoploss"},
}

# Common 3-letter GPW tickers -> archive short names (the archive lists names,
# brokers often export tickers). Applied only if the target exists in the data.
TICKER_ALIASES = {
    "PKO": "PKOBP", "PKN": "PKNORLEN", "ORLEN": "PKNORLEN", "KGH": "KGHM", "CDR": "CDPROJEKT",
    "PEO": "PEKAO", "DNP": "DINOPL", "DINO": "DINOPL", "ALE": "ALLEGRO", "MBK": "MBANK",
    "KTY": "KETY", "ALR": "ALIOR", "PCO": "PEPCO", "BDX": "BUDIMEX", "TPE": "TAURONPE",
    "KRU": "KRUK", "OPL": "ORANGEPL", "CPS": "CYFRPLSAT", "ACP": "ASSECOPOL", "MIL": "MILLENNIUM",
    "ING": "INGBSK", "BHW": "HANDLOWY", "ENA": "ENEA", "ATT": "GRUPAAZOTY",
}


class PortfolioError(ValueError):
    """Bad input (CSV, unknown ticker, empty portfolio)."""


# --------------------------------------------------------------------------
# CSV input
# --------------------------------------------------------------------------

def parse_number(text) -> Optional[float]:
    """'1 234,56' / '1.234,56' / '1,234.56' / '12,5 zł' -> float; '' -> None."""
    if text is None:
        return None
    s = str(text).strip().replace("\xa0", "").replace(" ", "").replace(" ", "")
    for unit in ("zł", "PLN", "pln", "zl"):
        s = s.replace(unit, "")
    if s in ("", "-", "--"):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        raise PortfolioError("not a number: {!r}".format(text))
    if not math.isfinite(v):
        raise PortfolioError("not a finite number: {!r}".format(text))
    return v


def _norm_header(h: str) -> str:
    return h.strip().lstrip("﻿").strip().lower().replace(" ", "_").replace("-", "_").replace(".", "")


def _detect_delimiter(line: str) -> str:
    if ";" in line:
        return ";"
    if "\t" in line:
        return "\t"
    return ","


def parse_holdings_csv(text: str) -> List[Dict]:
    """Parse ``ticker,shares,avg_price[,account_type][,stop]`` (header required).

    Tolerates: UTF-8 BOM, ';' or tab separators (Polish broker exports), decimal
    commas, thousands spaces, 'zł' suffixes, Polish column names, '#' comment
    lines. Rows with the same ticker are merged (weighted average price).
    """
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise PortfolioError("the holdings CSV is empty")
    delim = _detect_delimiter(lines[0])
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)
    header = [_norm_header(h) for h in next(reader)]
    col: Dict[str, int] = {}
    for i, h in enumerate(header):
        for key, aliases in HEADER_ALIASES.items():
            if h in aliases and key not in col:
                col[key] = i
    missing = [k for k in ("ticker", "shares", "avg_price") if k not in col]
    if missing:
        raise PortfolioError("CSV header must contain ticker, shares, avg_price (missing: {}; got: {})".format(
            ", ".join(missing), ", ".join(header)))
    rows = []
    for n, rec in enumerate(reader, start=2):
        if not any(c.strip() for c in rec):
            continue
        if len(rec) > len(header):
            raise PortfolioError(
                "line {}: more fields than columns - with ',' as separator, decimal commas must be quoted "
                "(\"12,50\") or use ';' as separator".format(n))
        rec = rec + [""] * (len(header) - len(rec))
        ticker = rec[col["ticker"]].strip().upper()
        if not ticker:
            raise PortfolioError("line {}: empty ticker".format(n))
        shares = parse_number(rec[col["shares"]])
        if shares is None or shares <= 0:
            raise PortfolioError("line {}: shares must be a positive number".format(n))
        if abs(shares - round(shares)) > 1e-9:
            raise PortfolioError("line {}: shares must be a whole number (GPW lot = 1 share)".format(n))
        price = parse_number(rec[col["avg_price"]])
        if price is None or price <= 0:
            raise PortfolioError("line {}: avg_price must be a positive number".format(n))
        acct = "regular"
        if "account_type" in col and rec[col["account_type"]].strip():
            raw = rec[col["account_type"]].strip().lower()
            if raw not in ACCOUNT_ALIASES:
                raise PortfolioError("line {}: account_type must be regular, ike, ikze or oki (got {!r})".format(n, raw))
            acct = ACCOUNT_ALIASES[raw]
        stop = parse_number(rec[col["stop"]]) if "stop" in col else None
        if stop is not None and stop <= 0:
            raise PortfolioError("line {}: stop must be positive".format(n))
        rows.append({"ticker": ticker, "shares": int(round(shares)), "avg_price": price,
                     "account_type": acct, "stop": stop})
    if not rows:
        raise PortfolioError("the holdings CSV has a header but no positions")
    return merge_rows(rows, stops_tracked="stop" in col)


def merge_rows(rows: List[Dict], stops_tracked: bool) -> List[Dict]:
    """Merge rows per ticker. Keeps per-account shares and per-lot stops."""
    out: Dict[str, Dict] = {}
    for r in rows:
        h = out.setdefault(r["ticker"], {"ticker": r["ticker"], "shares": 0, "cost": 0.0,
                                         "accounts": {}, "stops": [], "thesis_ids": []})
        h["shares"] += r["shares"]
        h["cost"] += r["shares"] * r["avg_price"]
        acct = r.get("account_type") or "regular"
        h["accounts"][acct] = h["accounts"].get(acct, 0) + r["shares"]
        h["stops"].append({"shares": r["shares"], "stop": r.get("stop")})
        if r.get("thesis_id"):
            h["thesis_ids"].append(r["thesis_id"])
    res = []
    for h in out.values():
        h["avg_price"] = round(h["cost"] / h["shares"], 4)
        h["cost"] = round(h["cost"], 2)
        h["stops_tracked"] = stops_tracked
        res.append(h)
    return res


def holdings_from_journal(open_rows: List[Dict]) -> List[Dict]:
    """Convert gpwlib.journal_io.open_positions rows (FIFO remaining lots)."""
    rows = [{"ticker": r["ticker"].upper(), "shares": int(r["shares"]), "avg_price": float(r["avg_price"]),
             "account_type": r.get("account_type") or "regular", "stop": r.get("stop"),
             "thesis_id": r.get("thesis_id")} for r in open_rows]
    if not rows:
        raise PortfolioError("the journal has no ACTIVE positions")
    return merge_rows(rows, stops_tracked=True)


# --------------------------------------------------------------------------
# Ticker resolution
# --------------------------------------------------------------------------

def resolve_ticker(ticker: str, names: Iterable[str]) -> Tuple[Optional[str], Optional[str]]:
    """Return (archive name, alias note) or (None, None)."""
    upper = {n.upper(): n for n in names}
    t = ticker.strip().upper()
    if t in upper:
        return upper[t], None
    alias = TICKER_ALIASES.get(t)
    if alias and alias in upper:
        return upper[alias], "{} -> {}".format(t, upper[alias])
    return None, None


def close_matches(ticker: str, names: Iterable[str], n: int = 6) -> List[str]:
    t = ticker.strip().upper()
    pool = sorted({x.upper() for x in names})
    sub = [x for x in pool if t in x or (len(x) >= 3 and x in t)]
    fuzzy = difflib.get_close_matches(t, pool, n=n, cutoff=0.6)
    out = []
    for x in sub + fuzzy:
        if x not in out:
            out.append(x)
    return out[:n]


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

def _sessions_since(last_date: str, ref_dates: Sequence[str]) -> int:
    """Sessions in ref_dates strictly after last_date."""
    return sum(1 for d in ref_dates if d > last_date)


def size_bucket(name: str, kind: str, size_map: Dict[str, str]) -> str:
    if kind == "etf":
        return "ETF"
    return size_map.get(name, "OTHER")


def sector_of(name: str, kind: str, sector_map: Dict[str, str]) -> str:
    if kind == "etf":
        return "ETF"
    return sector_map.get(name, "OTHER")


def _clusters(names: List[str], pairs: List[Dict]) -> List[List[str]]:
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in pairs:
        ra, rb = find(p["a"]), find(p["b"])
        if ra != rb:
            parent[ra] = rb
    groups: Dict[str, List[str]] = {}
    for n in names:
        groups.setdefault(find(n), []).append(n)
    return sorted([sorted(g) for g in groups.values() if len(g) >= 2], key=lambda g: (-len(g), g))


def _curve(rets: Sequence[float]) -> List[float]:
    c = [1.0]
    for r in rets:
        c.append(c[-1] * (1 + r))
    return c


def hhi_level(h: float, cfg: Dict) -> str:
    if h > cfg["hhi_high"]:
        return "high"
    if h > cfg["hhi_moderate"]:
        return "moderate"
    return "low"


def analyze(holdings: List[Dict], series: Dict[str, Dict], wig: Dict, cash: float = 0.0,
            cfg: Optional[Dict] = None) -> Dict:
    """Compute the full portfolio check.

    holdings: merged rows (see merge_rows) enriched by the caller with ``name``
        (archive name), ``kind`` ('share'|'etf'), ``sector`` and ``size``.
    series: {name: {'dates': [...], 'close': [...], 'turnover_k': [...]}} adjusted, oldest first.
    wig: {'dates': [...], 'close': [...]} for the WIG index.
    """
    cfg = dict(THRESHOLDS, **(cfg or {}))
    if not holdings:
        raise PortfolioError("no holdings")
    if cash < 0:
        raise PortfolioError("cash cannot be negative")
    ref_dates = wig["dates"] if wig.get("dates") else []
    as_of = max(series[h["name"]]["dates"][-1] for h in holdings)

    # --- positions -----------------------------------------------------------
    pos = []
    for h in holdings:
        s = series[h["name"]]
        last = s["close"][-1]
        value = h["shares"] * last
        cost = h["shares"] * h["avg_price"]
        pos.append({
            "ticker": h["name"], "input_ticker": h["ticker"], "kind": h["kind"],
            "sector": h["sector"], "size": h["size"], "shares": h["shares"],
            "avg_price": h["avg_price"], "last": last, "last_date": s["dates"][-1],
            "value": round(value, 2), "cost": round(cost, 2),
            "pnl": round(value - cost, 2), "pnl_pct": round((last / h["avg_price"] - 1) * 100, 2),
            "accounts": dict(h["accounts"]), "stops": list(h["stops"]),
            "thesis_ids": list(h.get("thesis_ids", [])),
            "stale_sessions": _sessions_since(s["dates"][-1], [d for d in ref_dates if d <= as_of]),
        })
    invested = sum(p["value"] for p in pos)
    total = invested + cash
    if total <= 0:
        raise PortfolioError("portfolio value is zero")
    for p in pos:
        p["weight_pct"] = round(p["value"] / total * 100, 2)
        p["weight_invested_pct"] = round(p["value"] / invested * 100, 2) if invested else 0.0
    pos.sort(key=lambda p: -p["value"])

    # --- buckets ---------------------------------------------------------------
    def bucket(key):
        out: Dict[str, float] = {}
        for p in pos:
            out[p[key]] = out.get(p[key], 0.0) + p["value"]
        return {k: {"value": round(v, 2), "weight_pct": round(v / total * 100, 2)}
                for k, v in sorted(out.items(), key=lambda kv: -kv[1])}

    sectors = bucket("sector")
    sizes = bucket("size")
    accounts: Dict[str, float] = {}
    for p in pos:
        for a, sh in p["accounts"].items():
            accounts[a] = accounts.get(a, 0.0) + sh * p["last"]
    accounts = {a: {"value": round(v, 2), "weight_pct": round(v / total * 100, 2)} for a, v in accounts.items()}

    # --- concentration ---------------------------------------------------------
    w_inv = [p["value"] / invested for p in pos] if invested else []
    h_idx = stats.hhi(w_inv) if w_inv else 0.0
    concentration = {
        "hhi": round(h_idx, 4), "hhi_10000": round(h_idx * 10000), "level": hhi_level(h_idx, cfg),
        "effective_positions": round(1 / h_idx, 2) if h_idx > 0 else None,
        "n_positions": len(pos), "top_position": pos[0]["ticker"], "top_weight_pct": pos[0]["weight_pct"],
        "top3_weight_pct": round(sum(p["weight_pct"] for p in pos[:3]), 2),
        "etfs_counted_as_one": any(p["kind"] == "etf" for p in pos),
    }

    # --- liquidity -------------------------------------------------------------
    for p in pos:
        s = series[p["ticker"]]
        win = [v * 1000.0 for v in s["turnover_k"][-cfg["liquidity_window"]:]]
        med = median(win) if win else None
        p["median_turnover_pln"] = round(med, 2) if med is not None else None
        cap = (med or 0.0) * cfg["participation_pct"] / 100.0
        p["days_to_exit"] = round(p["value"] / cap, 2) if cap > 0 else None

    # --- heat (open risk to stops) ---------------------------------------------
    tracked = any(h.get("stops_tracked") for h in holdings)
    heat_total = 0.0
    for p in pos:
        risk, no_stop_shares = 0.0, 0
        below = False
        for seg in p["stops"]:
            if seg["stop"] is None:
                no_stop_shares += seg["shares"]
                continue
            risk += max(0.0, p["last"] - seg["stop"]) * seg["shares"]
            if seg["stop"] >= p["last"]:
                below = True
        p["open_risk"] = round(risk, 2)
        p["no_stop_shares"] = no_stop_shares
        p["at_or_below_stop"] = below
        heat_total += risk
    heat = {
        "tracked": tracked,
        "open_risk": round(heat_total, 2) if tracked else None,
        "heat_pct": round(heat_total / total * 100, 2) if tracked else None,
        "positions_without_stop": [p["ticker"] for p in pos if p["no_stop_shares"] > 0] if tracked else [],
    }

    # --- risk statistics ---------------------------------------------------------
    risk = _risk_stats(pos, series, wig, total, invested, cfg)

    # --- flags -----------------------------------------------------------------
    flags = []
    for p in pos:
        reasons = []
        if p["kind"] != "etf" and p["weight_pct"] > cfg["max_position_pct"]:
            reasons.append({"code": "position_weight", "value": p["weight_pct"], "limit": cfg["max_position_pct"]})
        if p["sector"] not in ("ETF", "OTHER") and sectors[p["sector"]]["weight_pct"] > cfg["max_sector_pct"]:
            reasons.append({"code": "sector_weight", "sector": p["sector"],
                            "value": sectors[p["sector"]]["weight_pct"], "limit": cfg["max_sector_pct"]})
        if p["days_to_exit"] is None or p["days_to_exit"] > cfg["max_days_to_exit"]:
            reasons.append({"code": "illiquid", "value": p["days_to_exit"], "limit": cfg["max_days_to_exit"]})
        partners = [x["b"] if x["a"] == p["ticker"] else x["a"] for x in risk.get("high_pairs", [])
                    if p["ticker"] in (x["a"], x["b"])]
        if partners:
            reasons.append({"code": "correlated", "with": partners})
        if tracked and p["no_stop_shares"] > 0:
            reasons.append({"code": "no_stop", "shares": p["no_stop_shares"]})
        if p["at_or_below_stop"]:
            reasons.append({"code": "below_stop"})
        if p["stale_sessions"] > cfg["stale_quote_sessions"]:
            reasons.append({"code": "stale_quote", "value": p["stale_sessions"]})
        ratio = p["last"] / p["avg_price"]
        if ratio < cfg["avg_price_ratio_low"] or ratio > cfg["avg_price_ratio_high"]:
            reasons.append({"code": "check_avg_price", "value": round(ratio, 3)})
        if p["ticker"] in risk.get("excluded", []):
            reasons.append({"code": "short_history"})
        p["reasons"] = reasons
        p["status"] = "REVIEW" if reasons else "OK"

    over_sectors = [k for k, v in sectors.items() if k not in ("ETF", "OTHER") and v["weight_pct"] > cfg["max_sector_pct"]]
    big = [p["ticker"] for p in pos if p["kind"] != "etf" and p["weight_pct"] > cfg["max_position_pct"]]
    if big:
        flags.append({"code": "position_weight", "tickers": big, "limit": cfg["max_position_pct"]})
    if over_sectors:
        flags.append({"code": "sector_weight", "sectors": over_sectors, "limit": cfg["max_sector_pct"]})
    # HHI counts an ETF as one position; with no look-through into ETF baskets
    # the flag is raised only when single stocks are the majority of the book.
    stock_share = sum(p["value"] for p in pos if p["kind"] != "etf") / invested if invested else 0.0
    concentration["stock_share_pct"] = round(stock_share * 100, 2)
    if concentration["level"] == "high" and stock_share > 0.5:
        flags.append({"code": "hhi_high", "hhi": concentration["hhi"], "limit": cfg["hhi_high"]})
    illiq = [p["ticker"] for p in pos if p["days_to_exit"] is None or p["days_to_exit"] > cfg["max_days_to_exit"]]
    if illiq:
        flags.append({"code": "illiquid", "tickers": illiq, "limit": cfg["max_days_to_exit"]})
    for cl in risk.get("clusters", []):
        flags.append({"code": "correlated_cluster", "tickers": cl,
                      "weight_pct": round(sum(p["weight_pct"] for p in pos if p["ticker"] in cl), 2)})
    if heat["positions_without_stop"]:
        flags.append({"code": "no_stop", "tickers": heat["positions_without_stop"]})
    below = [p["ticker"] for p in pos if p["at_or_below_stop"]]
    if below:
        flags.append({"code": "below_stop", "tickers": below})

    # --- latent tax on the regular account (indicative) --------------------------
    gain_regular = sum((p["last"] - p["avg_price"]) * p["accounts"].get("regular", 0) for p in pos)
    tax = {"regular_unrealised_net": round(gain_regular, 2),
           "indicative_tax_if_sold": round(max(0.0, gain_regular) * BELKA_RATE, 2), "rate": BELKA_RATE}

    totals = {
        "total_value": round(total, 2), "invested": round(invested, 2), "cash": round(cash, 2),
        "cash_pct": round(cash / total * 100, 2), "cost": round(sum(p["cost"] for p in pos), 2),
        "pnl": round(sum(p["pnl"] for p in pos), 2),
    }
    totals["pnl_pct"] = round(totals["pnl"] / totals["cost"] * 100, 2) if totals["cost"] else None
    return {
        "as_of": as_of, "totals": totals, "positions": pos, "sectors": sectors, "sizes": sizes,
        "accounts": accounts, "concentration": concentration, "risk": risk, "heat": heat,
        "flags": flags, "tax": tax, "thresholds": cfg,
        "review_count": sum(1 for p in pos if p["status"] == "REVIEW"),
    }


def _risk_stats(pos: List[Dict], series: Dict[str, Dict], wig: Dict, total: float, invested: float,
                cfg: Dict) -> Dict:
    lookback, min_r = cfg["lookback"], cfg["min_risk_returns"]
    wig_map = dict(zip(wig.get("dates", []), wig.get("close", [])))
    window = [d for d in wig.get("dates", []) if d <= max(p["last_date"] for p in pos)][-(lookback + 1):]
    if len(window) < min_r + 1:
        return {"available": False, "reason": "not enough WIG history", "excluded": []}
    maps = {p["ticker"]: dict(zip(series[p["ticker"]]["dates"], series[p["ticker"]]["close"])) for p in pos}
    included, excluded = [], []
    for p in pos:
        n = sum(1 for d in window if d in maps[p["ticker"]])
        (included if n >= min_r + 1 else excluded).append(p["ticker"])
    common = [d for d in window if all(d in maps[t] for t in included)]
    if len(common) < min_r + 1 or not included:
        return {"available": False, "reason": "not enough common history", "excluded": [p["ticker"] for p in pos]}
    rets = {t: stats.returns([maps[t][d] for d in common]) for t in included}
    wig_r = stats.returns([wig_map[d] for d in common])
    weights = {p["ticker"]: p["value"] / total for p in pos if p["ticker"] in included}
    n = len(wig_r)
    port = [sum(weights[t] * rets[t][i] for t in included) for i in range(n)]
    inv_w = sum(weights.values())
    equity = [x / inv_w for x in port] if inv_w > 0 else port
    var1 = stats.historical_var(port, cfg["var_level"])
    es1 = stats.expected_shortfall(port, cfg["var_level"])
    curve, wig_curve = _curve(port), _curve(wig_r)
    mdd = stats.max_drawdown(curve)
    per = {}
    for t in included:
        per[t] = {"volatility_pct": round(stats.stdev(rets[t]) * math.sqrt(stats.SESSIONS_PER_YEAR) * 100, 2),
                  "beta": _r(stats.beta(rets[t], wig_r), 3),
                  "return_pct": round((_curve(rets[t])[-1] - 1) * 100, 2)}
    corr, pairs = {}, []
    for i, a in enumerate(included):
        corr[a] = {}
        for b in included:
            corr[a][b] = 1.0 if a == b else _r(stats.correlation(rets[a], rets[b]), 3)
    for i, a in enumerate(included):
        for b in included[i + 1:]:
            c = corr[a][b]
            if c is not None and c > cfg["corr_high"]:
                pairs.append({"a": a, "b": b, "corr": c})
    pairs.sort(key=lambda x: -x["corr"])
    top_pairs = sorted(({"a": a, "b": b, "corr": corr[a][b]} for i, a in enumerate(included)
                        for b in included[i + 1:] if corr[a][b] is not None), key=lambda x: -x["corr"])[:5]
    sd = stats.stdev(port)
    return {
        "available": True, "returns_used": n, "start": common[0], "end": common[-1],
        "included": included, "excluded": excluded,
        "excluded_weight_pct": round(sum(p["weight_pct"] for p in pos if p["ticker"] in excluded), 2),
        "volatility_pct": round(sd * math.sqrt(stats.SESSIONS_PER_YEAR) * 100, 2) if sd else None,
        "equity_volatility_pct": round(stats.stdev(equity) * math.sqrt(stats.SESSIONS_PER_YEAR) * 100, 2)
        if stats.stdev(equity) else None,
        "wig_volatility_pct": round(stats.stdev(wig_r) * math.sqrt(stats.SESSIONS_PER_YEAR) * 100, 2),
        "beta": _r(stats.beta(port, wig_r), 3),
        "var_pct": _r(var1 * 100 if var1 is not None else None, 3),
        "var_pln": _r(var1 * total if var1 is not None else None, 2),
        "es_pct": _r(es1 * 100 if es1 is not None else None, 3),
        "es_pln": _r(es1 * total if es1 is not None else None, 2),
        "var_horizon": cfg["var_horizon"],
        "var_horizon_pln": _r(var1 * math.sqrt(cfg["var_horizon"]) * total if var1 is not None else None, 2),
        "max_drawdown_pct": round(mdd["mdd"] * 100, 2),
        "current_drawdown_pct": round(mdd["current"] * 100, 2),
        "return_pct": round((curve[-1] - 1) * 100, 2),
        "wig_return_pct": round((wig_curve[-1] - 1) * 100, 2),
        "wig_max_drawdown_pct": round(stats.max_drawdown(wig_curve)["mdd"] * 100, 2),
        "excess_return_pp": round((curve[-1] - wig_curve[-1]) * 100, 2),
        "per_position": per, "correlation": corr, "high_pairs": pairs, "top_pairs": top_pairs,
        "clusters": _clusters(included, pairs),
    }


def _r(x, d):
    return round(x, d) if x is not None else None
