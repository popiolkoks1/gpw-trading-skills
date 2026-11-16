"""Pure engine of gpw-research-dossier: section extractors, facts, summaries.

Every other GPW skill writes a JSON report; this module turns the newest
relevant report of each skill into one dossier *section* for one ticker:

    {skill, short, scope, as_of, stale, sessions_behind, summary,
     key_fields{...}, source_file, warning}

plus a flat list of *facts* with stable ids (``valuation.implied_growth_pct``,
``ownership.short_total_pct`` ...). The bull/bear debate must cite these ids
(see ``dossier_debate.py`` and ``references/debate_protocol.md``).

No I/O happens here except through the callables passed in (report lists,
journal theses, watchlist items), so every function is unit-testable with
synthetic dicts. Extractors never invent a value: a field that the source
report does not carry is simply absent (or ``None`` in ``key_fields``).
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

from gpwlib import plcal, provenance
from gpwlib.fmt import num, pct

ENGINE_VERSION = "1.0.0"

# --------------------------------------------------------------------------
# Section registry
# --------------------------------------------------------------------------
# max_sessions = staleness limit in GPW sessions (justified in
# references/methodology.md, "Staleness"):
#   3  - daily skills (cross-skill rule in docs/CONVENTIONS.md);
#   5  - weekly skills (macro, earnings season scan, event calendar, pairs, risk lab);
#   10 - fundamental skills whose inputs change quarterly (quality, valuation,
#        dividends); the price inside them still moves, hence two weeks, not a quarter.

SPECS: List[Dict[str, Any]] = [
    {"skill": "gpw-trend-screener", "short": "trend", "scope": "ticker", "max_sessions": 3},
    {"skill": "gpw-quality-screener", "short": "quality", "scope": "ticker", "max_sessions": 10},
    {"skill": "gpw-valuation", "short": "valuation", "scope": "ticker", "max_sessions": 10},
    {"skill": "gpw-dividend-analyzer", "short": "dividend", "scope": "ticker", "max_sessions": 10},
    {"skill": "gpw-ownership", "short": "ownership", "scope": "ticker", "max_sessions": 3},
    {"skill": "gpw-event-calendar", "short": "events", "scope": "ticker", "max_sessions": 5},
    {"skill": "gpw-earnings-reaction", "short": "earnings", "scope": "ticker", "max_sessions": 5},
    {"skill": "gpw-pair-trading", "short": "pairs", "scope": "ticker", "max_sessions": 5, "optional": True},
    {"skill": "gpw-risk-lab", "short": "risk", "scope": "ticker", "max_sessions": 5, "optional": True},
    {"skill": "gpw-market-regime", "short": "regime", "scope": "market", "max_sessions": 3},
    {"skill": "gpw-exposure-coach", "short": "exposure", "scope": "market", "max_sessions": 3},
    {"skill": "gpw-market-breadth", "short": "breadth", "scope": "market", "max_sessions": 3,
     "pattern": "gpw_breadth_*.json"},
    {"skill": "gpw-sector-trend", "short": "sector", "scope": "market", "max_sessions": 3,
     "pattern": "gpw_sector_trend_*.json"},
    {"skill": "gpw-macro-regime", "short": "macro", "scope": "market", "max_sessions": 5},
    {"skill": "gpw-trade-journal", "short": "journal", "scope": "personal", "max_sessions": None, "optional": True},
    {"skill": "gpw-watchlist", "short": "watchlist", "scope": "personal", "max_sessions": None, "optional": True},
]
SPEC_BY_SKILL = {s["skill"]: s for s in SPECS}
SHORT_TO_SKILL = {s["short"]: s["skill"] for s in SPECS}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def norm_ticker(t: Optional[str]) -> str:
    return (t or "").strip().upper()


def _f(x) -> Optional[float]:
    """Float or None (NaN/inf/garbage -> None)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _r(x, d: int = 2) -> Optional[float]:
    v = _f(x)
    return None if v is None else round(v, d)


def _pct_from_ratio(x, d: int = 2) -> Optional[float]:
    v = _f(x)
    return None if v is None else round(v * 100.0, d)


def _date(s) -> Optional[dt.date]:
    if isinstance(s, dt.date):
        return s
    if isinstance(s, str) and len(s) >= 10:
        try:
            return dt.date.fromisoformat(s[:10])
        except ValueError:
            return None
    return None


def sessions_until(ref: dt.date, day: dt.date) -> int:
    """Probable GPW sessions in (ref, day]; negative when day is in the past."""
    if day == ref:
        return 0
    sign = 1 if day > ref else -1
    lo, hi = (ref, day) if day > ref else (day, ref)
    n, d = 0, lo + dt.timedelta(days=1)
    while d <= hi:
        if plcal.is_probable_session(d):
            n += 1
        d += dt.timedelta(days=1)
    return sign * n


class Facts:
    """Collects (id, value, unit) for one section; drops None values."""

    def __init__(self, short: str):
        self.short = short
        self.items: List[Tuple[str, Any, str]] = []

    def add(self, name: str, value, unit: str = "") -> None:
        if value is None:
            return
        if isinstance(value, float) and not math.isfinite(value):
            return
        self.items.append(("{}.{}".format(self.short, name), value, unit))


def _yn(v: bool, lang: str) -> str:
    return ("tak" if v else "nie") if lang == "pl" else ("yes" if v else "no")


def _L(lang: str, pl: str, en: str) -> str:
    return pl if lang == "pl" else en


# --------------------------------------------------------------------------
# Ticker-level extractors. Signature: (data, ticker, ctx, lang) ->
#   None (ticker not covered by this report) or
#   {"key_fields": {...}, "facts": Facts, "summary": str}
# --------------------------------------------------------------------------

def ex_trend(data: Dict, ticker: str, ctx: Dict, lang: str) -> Optional[Dict]:
    entry, where = None, None
    res = data.get("result")
    if data.get("mode") == "ticker" and isinstance(res, dict) and norm_ticker(res.get("name")) == ticker:
        entry, where = res, "ticker"
    else:
        for lst in ("watchlist", "candidates", "others"):
            for it in data.get(lst) or []:
                if isinstance(it, dict) and norm_ticker(it.get("name")) == ticker:
                    entry, where = it, lst
                    break
            if entry:
                break
    if entry is None:
        return None
    F = Facts("trend")
    kf: Dict[str, Any] = {"found_in": where}
    pre = entry.get("prefilter") or {}
    if pre and not pre.get("ok", True):
        kf["prefilter_reasons"] = pre.get("reasons") or []
        F.add("prefilter_ok", False, "bool")
    tmpl = entry.get("template") or {}
    rs = entry.get("rs")
    rs_rating = rs.get("rating") if isinstance(rs, dict) else rs
    vs = (rs.get("vs_wig_pp") or {}) if isinstance(rs, dict) else {}
    exe = entry.get("execution") or {}
    vcp = entry.get("vcp") or {}
    risk = entry.get("risk") or {}
    state = exe.get("state") or entry.get("state")
    kf.update({
        "close": _r(entry.get("close")),
        "template_passed": tmpl.get("passed"),
        "template_total": tmpl.get("total"),
        "template_pass": entry.get("template_pass"),
        "rs_rating": rs_rating,
        "rs_vs_wig_252_pp": _r(vs.get("252")),
        "rs_vs_wig_63_pp": _r(vs.get("63")),
        "pct_from_52w_high": _r(tmpl.get("pct_from_high")),
        "vcp_valid": vcp.get("valid") if vcp else None,
        "pivot": _r(vcp.get("pivot")) if vcp else None,
        "execution_state": state,
        "pct_vs_pivot": _r(exe.get("pct_vs_pivot")),
        "score": _r(entry.get("score"), 1),
        "band": entry.get("band"),
        "stop_structure": _r(risk.get("stop_structure")),
        "stop_atr": _r(risk.get("stop_atr")),
        "risk_pct_structure": _r(risk.get("risk_pct_structure")),
        "labels": entry.get("labels") or [],
        "sizer_command": entry.get("sizer_command"),
        "vcp_reasons": entry.get("vcp_reasons") or vcp.get("reasons") or [],
    })
    F.add("close", kf["close"], "pln")
    F.add("template_passed", kf["template_passed"], "count")
    F.add("template_pass", kf["template_pass"], "bool")
    F.add("rs_rating", rs_rating, "score")
    F.add("rs_vs_wig_252_pp", kf["rs_vs_wig_252_pp"], "pp")
    F.add("pct_from_52w_high", kf["pct_from_52w_high"], "pct")
    F.add("vcp_valid", kf["vcp_valid"], "bool")
    F.add("pivot", kf["pivot"], "pln")
    F.add("execution_state", state, "text")
    F.add("pct_vs_pivot", kf["pct_vs_pivot"], "pct")
    F.add("score", kf["score"], "score")
    F.add("stop_structure", kf["stop_structure"], "pln")
    F.add("risk_pct_structure", kf["risk_pct_structure"], "pct")
    parts = []
    if kf["template_passed"] is not None:
        parts.append(_L(lang, "Trend Template {}/{}", "Trend Template {}/{}").format(
            kf["template_passed"], kf["template_total"] or 8))
    if rs_rating is not None:
        parts.append("RS {}".format(rs_rating))
    if state:
        parts.append(_L(lang, "stan {}", "state {}").format(state))
    if kf["pivot"] is not None:
        parts.append(_L(lang, "pivot {} ({} od pivotu)", "pivot {} ({} vs pivot)").format(
            num(kf["pivot"], 2, lang), pct(kf["pct_vs_pivot"], 1, lang, signed=True)))
    if kf["stop_structure"] is not None:
        parts.append(_L(lang, "unieważnienie {} (ryzyko {})", "invalidation {} (risk {})").format(
            num(kf["stop_structure"], 2, lang), pct(kf["risk_pct_structure"], 1, lang)))
    if kf.get("prefilter_reasons"):
        parts.insert(0, _L(lang, "odrzucona przez filtr wstępny", "fails the pre-filter"))
    return {"key_fields": kf, "facts": F, "summary": "; ".join(parts) or where}


def ex_quality(data: Dict, ticker: str, ctx: Dict, lang: str) -> Optional[Dict]:
    c = next((x for x in data.get("companies") or [] if norm_ticker(x.get("ticker")) == ticker), None)
    if c is None:
        return None
    m = c.get("metrics") or {}
    fp = c.get("f_score_partial") or {}
    flags = [f.get("code") if isinstance(f, dict) else f for f in c.get("red_flags") or []]
    F = Facts("quality")
    kf = {
        "sector_type": c.get("sector_type"),
        "period": c.get("period"),
        "stale_period": c.get("stale_period"),
        "quality_score": _r(c.get("quality_score"), 1),
        "f_score": c.get("f_score"),
        "f_score_display": fp.get("display"),
        "f_coverage_pct": _r(fp.get("coverage_pct"), 1),
        "roe_pct": _pct_from_ratio(m.get("roe"), 1),
        "roa_pct": _pct_from_ratio(m.get("roa"), 1),
        "roic_approx_pct": _pct_from_ratio(m.get("roic_approx"), 1),
        "operating_margin_pct": _pct_from_ratio(m.get("operating_margin"), 1),
        "net_margin_pct": _pct_from_ratio(m.get("net_margin"), 1),
        "fcf_yield_pct": _pct_from_ratio(m.get("fcf_yield"), 2),
        "accruals_ratio": _r(m.get("accruals_ratio"), 4),
        "cash_conversion": _r(m.get("cash_conversion"), 2),
        "interest_coverage": _r(m.get("interest_coverage_proxy"), 1),
        "equity_to_assets_pct": _pct_from_ratio(m.get("equity_to_assets"), 1),
        "pe": _r(m.get("pe_gpw"), 1),
        "pbv": _r(m.get("pbv_gpw"), 2),
        "net_debt_ebitda": _r(m.get("net_debt_ebitda"), 2),
        "red_flags": flags,
        "data_checks": len(c.get("data_checks") or []),
        "pbv_vs_roe": c.get("pbv_vs_roe"),
    }
    for k, unit in (("quality_score", "score"), ("f_score", "count"), ("f_score_display", "text"),
                    ("f_coverage_pct", "pct"), ("roe_pct", "pct"), ("roa_pct", "pct"),
                    ("roic_approx_pct", "pct"), ("operating_margin_pct", "pct"), ("net_margin_pct", "pct"),
                    ("fcf_yield_pct", "pct"), ("accruals_ratio", "x"), ("cash_conversion", "x"),
                    ("interest_coverage", "x"), ("equity_to_assets_pct", "pct"), ("pe", "x"), ("pbv", "x"),
                    ("net_debt_ebitda", "x"), ("period", "text"), ("sector_type", "text")):
        F.add(k, kf[k], unit)
    F.add("red_flag_count", len(flags), "count")
    if flags:
        F.add("red_flags", ", ".join(str(x) for x in flags), "text")
    F.add("stale_period", bool(c.get("stale_period")), "bool")
    parts = [_L(lang, "jakość {}/100", "quality {}/100").format(num(kf["quality_score"], 1, lang))
             if kf["quality_score"] is not None else _L(lang, "jakość: brak oceny", "quality: no score")]
    if kf["f_score_display"] and fp.get("applicable", True) and kf["f_score_display"] != "n/a":
        parts.append("F {} ({} {})".format(kf["f_score_display"], _L(lang, "pokrycie", "coverage"),
                                           pct(kf["f_coverage_pct"], 0, lang)))
    if kf["roe_pct"] is not None:
        parts.append("ROE {}".format(pct(kf["roe_pct"], 1, lang)))
    if kf["roic_approx_pct"] is not None and kf["sector_type"] == "industrial":
        parts.append("ROIC≈ {}".format(pct(kf["roic_approx_pct"], 1, lang)))
    if kf["pe"] is not None:
        parts.append(_L(lang, "C/Z {}", "P/E {}").format(num(kf["pe"], 1, lang)))
    parts.append(_L(lang, "czerwone flagi: {}", "red flags: {}").format(", ".join(flags) if flags else "0"))
    if kf["period"]:
        parts.append(_L(lang, "okres {}", "period {}").format(kf["period"]))
    return {"key_fields": kf, "facts": F, "summary": "; ".join(parts)}


def ex_valuation(data: Dict, ticker: str, ctx: Dict, lang: str) -> Optional[Dict]:
    if norm_ticker(data.get("ticker")) != ticker:
        return None
    rd = data.get("reverse_dcf") or {}
    fv = data.get("fair_value_range") or {}
    rp = data.get("range_position") or {}
    ud = data.get("upside_downside_pct") or {}
    dcf = data.get("dcf") or {}
    comps = data.get("comps") or {}
    peers = comps.get("peers") if isinstance(comps.get("peers"), list) else None
    kf = {
        "model": data.get("model") or rd.get("model"),
        "price": _r(data.get("price")),
        "implied_growth_pct": _r(data.get("implied_growth_pct"), 2),
        "implied_roe_pct": _r(rd.get("implied_roe_pct"), 2),
        "current_roe_pct": _r(rd.get("current_roe_pct"), 2),
        "cost_of_equity_pct": _r(rd.get("coe_pct"), 2),
        "reverse_dcf_status": rd.get("status"),
        "wacc_pct": _r(data.get("wacc_pct"), 2),
        "fair_value_low": _r(fv.get("low")),
        "fair_value_base": _r(fv.get("base")),
        "fair_value_high": _r(fv.get("high")),
        "range_position": rp.get("position"),
        "to_base_pct": _r(ud.get("to_base") if ud else rp.get("to_base_pct"), 1),
        "terminal_share_pct": _pct_from_ratio(dcf.get("terminal_share"), 1),
        "comps_peers": len(peers) if peers is not None else None,
        "comps_skipped": comps.get("reason") if comps.get("skipped") else None,
        "sector_index": data.get("sector_index"),
        "warnings_count": len(data.get("warnings") or []),
    }
    F = Facts("valuation")
    for k, unit in (("model", "text"), ("price", "pln"), ("implied_growth_pct", "pct"),
                    ("implied_roe_pct", "pct"), ("current_roe_pct", "pct"), ("cost_of_equity_pct", "pct"),
                    ("wacc_pct", "pct"), ("fair_value_low", "pln"), ("fair_value_base", "pln"),
                    ("fair_value_high", "pln"), ("range_position", "text"), ("to_base_pct", "pct"),
                    ("terminal_share_pct", "pct"), ("comps_peers", "count"), ("warnings_count", "count")):
        F.add(k, kf[k], unit)
    parts = []
    if kf["model"] in ("bank", "justified_pbv") or kf["implied_roe_pct"] is not None:
        parts.append(_L(lang, "cena zakłada trwałe ROE {} (obecne {}, koszt kapitału {})",
                        "price implies sustainable ROE {} (current {}, cost of equity {})").format(
            pct(kf["implied_roe_pct"], 1, lang), pct(kf["current_roe_pct"], 1, lang),
            pct(kf["cost_of_equity_pct"], 1, lang)))
    elif kf["implied_growth_pct"] is not None:
        parts.append(_L(lang, "cena zakłada wzrost FCF {} rocznie przez 10 lat (WACC {})",
                        "price implies FCF growth of {} a year for 10 years (WACC {})").format(
            pct(kf["implied_growth_pct"], 1, lang), pct(kf["wacc_pct"], 1, lang)))
    if kf["fair_value_base"] is not None:
        parts.append(_L(lang, "przedział {}–{}–{} zł, cena {} zł ({})",
                        "range {}–{}–{} PLN, price {} PLN ({})").format(
            num(kf["fair_value_low"], 0, lang), num(kf["fair_value_base"], 0, lang),
            num(kf["fair_value_high"], 0, lang), num(kf["price"], 2, lang), kf["range_position"] or "?"))
    if kf["warnings_count"]:
        parts.append(_L(lang, "{} ostrzeżeń w raporcie", "{} warnings in the report").format(kf["warnings_count"]))
    return {"key_fields": kf, "facts": F, "summary": "; ".join(parts) or "valuation"}


def ex_dividend(data: Dict, ticker: str, ctx: Dict, lang: str) -> Optional[Dict]:
    c = next((x for x in data.get("companies") or [] if norm_ticker(x.get("ticker")) == ticker), None)
    if c is None:
        return None
    up = next((u for u in data.get("upcoming") or [] if norm_ticker(u.get("ticker")) == ticker), None)
    traps = [t.get("code") if isinstance(t, dict) else t for t in c.get("trap_flags") or []]
    cuts = c.get("cuts") or []
    kf = {
        "years_paid": c.get("years_paid"),
        "years_observed": c.get("years_observed"),
        "cuts": c.get("n_cuts") if c.get("n_cuts") is not None else len(cuts),
        "suspensions": c.get("n_suspensions"),
        "dps_ttm": _r(c.get("dps_ttm")),
        "dps_cagr_5y_pct": _r(c.get("dps_cagr_5y_pct"), 2),
        "yield_gross_pct": _r(c.get("yield_gross_pct"), 2),
        "yield_net_regular_pct": _r(c.get("yield_net_regular_pct"), 2),
        "yield_ike_pct": _r(c.get("yield_ike_pct"), 2),
        # payout_ratio is already in percent in the dividend-analyzer report
        "payout_ratio_pct": _r(c.get("payout_ratio"), 1),
        "payout_basis": c.get("payout_basis"),
        "fcf_cover": _r(c.get("fcf_cover"), 2),
        "stability_score": _r(c.get("stability_score"), 1),
        "trap_flags": traps,
        "next_ex_date": up.get("ex_date") if up else None,
        "next_ex_date_kind": up.get("date_kind") if up else None,
        "next_dps": _r(up.get("dps")) if up else None,
    }
    F = Facts("dividend")
    for k, unit in (("years_paid", "count"), ("years_observed", "count"), ("cuts", "count"),
                    ("suspensions", "count"), ("dps_ttm", "pln"), ("dps_cagr_5y_pct", "pct"),
                    ("yield_gross_pct", "pct"), ("yield_net_regular_pct", "pct"), ("yield_ike_pct", "pct"),
                    ("payout_ratio_pct", "pct"), ("fcf_cover", "x"), ("stability_score", "score"), ("next_ex_date", "date"),
                    ("next_ex_date_kind", "text")):
        F.add(k, kf[k], unit)
    F.add("trap_flag_count", len(traps), "count")
    if traps:
        F.add("trap_flags", ", ".join(str(t) for t in traps), "text")
    parts = [_L(lang, "płaci {}/{} lat, cięcia {}", "paid {}/{} years, cuts {}").format(
        kf["years_paid"], kf["years_observed"], kf["cuts"])]
    if kf["yield_gross_pct"] is not None:
        parts.append(_L(lang, "stopa brutto {} (netto {})", "gross yield {} (net {})").format(
            pct(kf["yield_gross_pct"], 2, lang), pct(kf["yield_net_regular_pct"], 2, lang)))
    if kf["stability_score"] is not None:
        parts.append(_L(lang, "stabilność {}/100", "stability {}/100").format(num(kf["stability_score"], 0, lang)))
    parts.append(_L(lang, "pułapki: {}", "trap flags: {}").format(", ".join(traps) if traps else "0"))
    if kf["next_ex_date"]:
        parts.append(_L(lang, "następne odcięcie {} ({})", "next ex-date {} ({})").format(
            kf["next_ex_date"], kf["next_ex_date_kind"]))
    return {"key_fields": kf, "facts": F, "summary": "; ".join(parts)}


def ex_ownership(data: Dict, ticker: str, ctx: Dict, lang: str) -> Optional[Dict]:
    mode = data.get("mode")
    is_ticker_mode = mode == "ticker" and norm_ticker(data.get("ticker")) == ticker
    if mode == "ticker" and not is_ticker_mode:
        return None
    shorts = data.get("shorts") or {}
    ins = data.get("insiders") or {}
    funds = data.get("funds") or {}
    s_row = next((x for x in shorts.get("by_issuer") or [] if norm_ticker(x.get("ticker")) == ticker), None)
    i_row = next((x for x in ins.get("by_ticker") or [] if norm_ticker(x.get("ticker")) == ticker), None)
    f_row = next((x for x in funds.get("by_ticker") or [] if norm_ticker(x.get("ticker")) == ticker), None)
    # The KNF register lists every disclosed position >= 0.5%: in ticker or full
    # shorts mode an absent issuer means "nothing disclosed" (0), not "unknown".
    shorts_covered = s_row is not None or is_ticker_mode or (mode == "shorts" and "by_issuer" in shorts)
    insiders_covered = i_row is not None or is_ticker_mode
    if not (shorts_covered or insiders_covered or f_row):
        return None
    F = Facts("ownership")
    kf: Dict[str, Any] = {"mode": mode}
    if shorts_covered:
        kf.update({
            "short_total_pct": _r((s_row or {}).get("total_pct"), 2) if s_row else 0.0,
            "short_holders": (s_row or {}).get("holders", 0) if s_row else 0,
            "short_change_20s_pp": _r(s_row.get("change_20s_pp"), 2) if s_row else None,
            "short_crowded": bool(s_row.get("crowded")) if s_row else False,
            "short_days_to_cover": _r(s_row.get("days_to_cover"), 1) if s_row else None,
            "short_in_register": s_row is not None,
        })
        for k, unit in (("short_total_pct", "pct"), ("short_holders", "count"), ("short_change_20s_pp", "pp"),
                        ("short_crowded", "bool"), ("short_days_to_cover", "days"), ("short_in_register", "bool")):
            F.add(k, kf[k], unit)
    if insiders_covered:
        r = i_row or {}
        kf.update({
            "insider_buys": r.get("buys", 0),
            "insider_sells": r.get("sells", 0),
            "insider_distinct": r.get("distinct_insiders", 0),
            "insider_cluster_buy": bool(r.get("cluster_buy")),
            "insider_cluster_sell": bool(r.get("cluster_sell")),
            "insider_net_value_pln": _r(r.get("net_value_pln"), 0),
            "insider_basis": r.get("basis") or ("no_notices" if not i_row else None),
            "insider_notices": r.get("notices", 0),
            "insider_last_date": r.get("last_date"),
            "insider_flags": r.get("flags") or [],
        })
        for k, unit in (("insider_buys", "count"), ("insider_sells", "count"), ("insider_distinct", "count"),
                        ("insider_cluster_buy", "bool"), ("insider_cluster_sell", "bool"),
                        ("insider_net_value_pln", "pln"), ("insider_basis", "text"),
                        ("insider_notices", "count"), ("insider_last_date", "date")):
            F.add(k, kf[k], unit)
    if f_row:
        kf.update({"funds_value_pln": _r(f_row.get("value_pln"), 0),
                   "funds_holding": f_row.get("funds_holding"),
                   "funds_accumulation": f_row.get("fund_accumulation"),
                   "funds_implied_flow_pln": _r(f_row.get("implied_flow_pln"), 0)})
        for k, unit in (("funds_value_pln", "pln"), ("funds_holding", "count"),
                        ("funds_accumulation", "bool"), ("funds_implied_flow_pln", "pln")):
            F.add(k, kf[k], unit)
    parts = []
    if shorts_covered:
        if kf["short_in_register"]:
            parts.append(_L(lang, "krótka sprzedaż {} ({} podmiotów, zmiana 20s {} pp){}",
                            "short interest {} ({} holders, 20s change {} pp){}").format(
                pct(kf["short_total_pct"], 2, lang), kf["short_holders"],
                num(kf["short_change_20s_pp"], 2, lang),
                _L(lang, ", TŁOK", ", CROWDED") if kf["short_crowded"] else ""))
        else:
            parts.append(_L(lang, "brak ujawnionych shortów (≥0,5%) w rejestrze KNF",
                            "no disclosed short positions (>=0.5%) in the KNF register"))
    if insiders_covered:
        parts.append(_L(lang, "insiderzy: kupna {}, sprzedaże {} (podstawa {})",
                        "insiders: buys {}, sells {} (basis {})").format(
            kf["insider_buys"], kf["insider_sells"], kf["insider_basis"]))
        if kf["insider_cluster_buy"] or kf["insider_cluster_sell"]:
            parts.append(_L(lang, "klaster {}", "cluster {}").format(
                "buy" if kf["insider_cluster_buy"] else "sell"))
    if f_row:
        parts.append(_L(lang, "OFE/PPK: {} zł", "OFE/PPK: {} PLN").format(num(kf["funds_value_pln"], 0, lang)))
    return {"key_fields": kf, "facts": F, "summary": "; ".join(parts)}


def ex_events(data: Dict, ticker: str, ctx: Dict, lang: str) -> Optional[Dict]:
    bt = (data.get("by_ticker") or {}).get(ticker)
    evs = [e for e in data.get("events") or [] if norm_ticker(e.get("ticker")) == ticker]
    listed = ticker in [norm_ticker(t) for t in (data.get("tickers") or [])] if isinstance(data.get("tickers"), list) else False
    if bt is None and not evs and not listed:
        return None
    ref = ctx["reference"]
    bt = bt or {}
    F = Facts("events")
    kf: Dict[str, Any] = {"mode": data.get("mode")}
    for key, name in (("next_earnings", "next_earnings"), ("next_ex_dividend", "next_ex_dividend"),
                      ("next_agm", "next_agm")):
        item = bt.get(key) or {}
        d = item.get("date")
        kf[name + "_date"] = d
        kf[name + "_kind"] = item.get("date_kind")
        F.add(name + "_date", d, "date")
        F.add(name + "_kind", item.get("date_kind"), "text")
        dd = _date(d)
        if dd:
            s = sessions_until(ref, dd)
            kf[name + "_sessions"] = s
            F.add(name + "_sessions", s, "sessions")
    horizon = data.get("horizon") or {}
    h_end = _date(horizon.get("end"))
    company_in_horizon = [e for e in evs if h_end is None or (_date(e.get("date")) or dt.date.max) <= h_end]
    market_high = [e for e in data.get("events") or []
                   if not e.get("ticker") and e.get("importance") == "high"
                   and (h_end is None or (_date(e.get("date")) or dt.date.max) <= h_end)]
    kf["company_events_in_horizon"] = len(company_in_horizon)
    kf["market_high_events_in_horizon"] = len(market_high)
    kf["market_high_events"] = ["{} {}".format(e.get("date"), e.get("type")) for e in market_high[:6]]
    kf["horizon_end"] = horizon.get("end")
    F.add("company_events_in_horizon", len(company_in_horizon), "count")
    F.add("market_high_events_in_horizon", len(market_high), "count")
    hw = data.get("holding_window_check") or {}
    if hw and norm_ticker(hw.get("ticker") or ticker) == ticker and hw.get("verdict"):
        kf["holding_window_verdict"] = hw.get("verdict")
        F.add("holding_window_verdict", hw.get("verdict"), "text")
    parts = []
    if kf.get("next_earnings_date"):
        parts.append(_L(lang, "następny raport {} ({}, za {} sesji)", "next report {} ({}, in {} sessions)").format(
            kf["next_earnings_date"], kf["next_earnings_kind"], kf.get("next_earnings_sessions")))
    if kf.get("next_ex_dividend_date"):
        parts.append(_L(lang, "odcięcie dywidendy {} ({})", "ex-dividend {} ({})").format(
            kf["next_ex_dividend_date"], kf["next_ex_dividend_kind"]))
    if kf.get("next_agm_date"):
        parts.append(_L(lang, "WZ {} ({})", "AGM {} ({})").format(kf["next_agm_date"], kf["next_agm_kind"]))
    parts.append(_L(lang, "ważne zdarzenia rynkowe do {}: {}", "high-importance market events to {}: {}").format(
        kf["horizon_end"] or "?", len(market_high)))
    return {"key_fields": kf, "facts": F, "summary": "; ".join(parts)}


def ex_earnings(data: Dict, ticker: str, ctx: Dict, lang: str) -> Optional[Dict]:
    reacts = [r for r in data.get("reactions") or [] if norm_ticker(r.get("ticker")) == ticker]
    tk = (data.get("tickers") or {}).get(ticker)
    if not reacts and tk is None:
        return None
    tk = tk or {}
    latest = tk.get("latest") if isinstance(tk.get("latest"), dict) and tk["latest"].get("label") else None
    if latest is None and reacts:
        latest = max(reacts, key=lambda r: (r.get("report_date") or "", r.get("reaction_date") or ""))
    prof = tk.get("profile") or {}
    nxt = tk.get("next_expected") or {}
    F = Facts("earnings")
    kf: Dict[str, Any] = {}
    if latest:
        kf.update({
            "latest_report_date": latest.get("report_date"),
            "latest_period": latest.get("period_label") or latest.get("period"),
            "latest_label": latest.get("label"),
            "latest_car_3d_pct": _r(latest.get("car_3d_pct"), 2),
            "latest_abnormal_turnover": _r(latest.get("abnormal_turnover"), 2),
            "latest_drift_20s_pct": _r(latest.get("drift_20s_pct"), 2),
            "latest_z": _r(latest.get("z"), 2),
            "latest_provisional": latest.get("provisional"),
        })
    kf.update({
        "profile_events": prof.get("events"),
        "profile_mean_abs_car_pct": _r(prof.get("mean_abs_car_3d_pct"), 2),
        "profile_share_positive": _r(prof.get("share_positive"), 2),
        "next_expected_date": nxt.get("date"),
        "next_expected_kind": nxt.get("date_kind"),
    })
    for k, unit in (("latest_report_date", "date"), ("latest_period", "text"), ("latest_label", "text"),
                    ("latest_car_3d_pct", "pct"), ("latest_abnormal_turnover", "x"),
                    ("latest_drift_20s_pct", "pct"), ("latest_z", "x"), ("latest_provisional", "bool"),
                    ("profile_events", "count"), ("profile_mean_abs_car_pct", "pct"),
                    ("next_expected_date", "date"), ("next_expected_kind", "text")):
        F.add(k, kf.get(k), unit)
    parts = []
    if latest:
        parts.append(_L(lang, "ostatni raport {} ({}): {}, CAR 3d {}, obrót ×{}",
                        "latest report {} ({}): {}, CAR 3d {}, turnover ×{}").format(
            kf["latest_report_date"], kf["latest_period"], kf["latest_label"],
            pct(kf["latest_car_3d_pct"], 2, lang, signed=True), num(kf["latest_abnormal_turnover"], 2, lang)))
    if kf["profile_events"]:
        parts.append(_L(lang, "{} zdarzeń w historii, śr. |CAR| {}", "{} events in history, mean |CAR| {}").format(
            kf["profile_events"], pct(kf["profile_mean_abs_car_pct"], 2, lang)))
    if kf["next_expected_date"]:
        parts.append(_L(lang, "następny {} ({})", "next {} ({})").format(kf["next_expected_date"], kf["next_expected_kind"]))
    return {"key_fields": kf, "facts": F, "summary": "; ".join(parts) or "earnings"}


def ex_pairs(data: Dict, ticker: str, ctx: Dict, lang: str) -> Optional[Dict]:
    pairs = [p for p in data.get("pairs") or [] if ticker in (norm_ticker(p.get("a")), norm_ticker(p.get("b")))]
    sw = data.get("switch") if data.get("mode") == "switch" else None
    if not pairs and not sw:
        return None
    ok = [p for p in pairs if p.get("status", "ok") == "ok" and p.get("p_adj", p.get("p_value")) is not None]
    best = min(ok, key=lambda p: (not p.get("coint_pass"), p.get("p_adj", p.get("p_value")))) if ok else None
    F = Facts("pairs")
    kf: Dict[str, Any] = {"mode": data.get("mode"), "pairs_with_ticker": len(pairs),
                          "cointegrated": sum(1 for p in pairs if p.get("coint_pass"))}
    F.add("pairs_with_ticker", len(pairs), "count")
    F.add("cointegrated", kf["cointegrated"], "count")
    if best:
        is_a = norm_ticker(best.get("a")) == ticker
        peer = best.get("b") if is_a else best.get("a")
        state = best.get("state") or ""
        rel = "neutral"
        if state in ("a_rich", "b_rich"):
            rich_leg = "a" if state == "a_rich" else "b"
            rel = "rich" if (rich_leg == "a") == is_a else "cheap"
        kf.update({"best_peer": peer, "best_p_adj": _r(best.get("p_adj"), 4), "best_coint_pass": bool(best.get("coint_pass")),
                   "best_z": _r(best.get("z"), 2), "best_half_life_days": _r(best.get("half_life_days"), 1),
                   "best_stability": _r(best.get("stability"), 2), "ticker_vs_peer": rel})
        for k, unit in (("best_peer", "text"), ("best_p_adj", "x"), ("best_coint_pass", "bool"),
                        ("best_z", "x"), ("best_half_life_days", "days"), ("best_stability", "x"),
                        ("ticker_vs_peer", "text")):
            F.add(k, kf[k], unit)
    if sw and isinstance(sw, dict):
        kf["switch_verdict"] = sw.get("verdict")
        F.add("switch_verdict", sw.get("verdict"), "text")
    parts = [_L(lang, "{} par z {}, kointegracja po korekcie: {}", "{} pairs with {}, cointegrated after correction: {}").format(
        len(pairs), ticker, kf["cointegrated"])]
    if best:
        parts.append(_L(lang, "najbliższa para {} (p_adj {}, z {}, {} vs peer)",
                        "closest pair {} (p_adj {}, z {}, {} vs peer)").format(
            kf["best_peer"], num(kf["best_p_adj"], 3, lang), num(kf["best_z"], 2, lang), kf["ticker_vs_peer"]))
    return {"key_fields": kf, "facts": F, "summary": "; ".join(parts)}


def ex_risk(data: Dict, ticker: str, ctx: Dict, lang: str) -> Optional[Dict]:
    port = data.get("portfolio") or {}
    pos = next((p for p in port.get("positions") or [] if norm_ticker(p.get("ticker")) == ticker), None)
    if pos is None:
        return None
    total = _f(port.get("total_value"))
    F = Facts("risk")
    kf: Dict[str, Any] = {
        "position_value_pln": _r(pos.get("value"), 0),
        "weight_pct": _r(100.0 * _f(pos.get("value")) / total, 1) if total and _f(pos.get("value")) is not None else None,
        "beta_wig": _r(pos.get("beta_wig"), 2),
    }
    worst_ret, worst_id, worst_method = None, None, None
    stress = data.get("stress") or {}
    for sc in stress.get("scenarios") or []:
        if sc.get("skipped"):
            continue
        for p in sc.get("positions") or []:
            if norm_ticker(p.get("ticker")) == ticker and _f(p.get("return_pct")) is not None:
                if worst_ret is None or p["return_pct"] < worst_ret:
                    worst_ret, worst_id, worst_method = p["return_pct"], sc.get("id"), p.get("method")
    kf.update({"worst_scenario": worst_id, "worst_scenario_return_pct": _r(worst_ret, 2),
               "worst_scenario_method": worst_method,
               "portfolio_worst_scenario": stress.get("worst_scenario")})
    hedge = data.get("hedge") or {}
    if hedge:
        kf["portfolio_beta"] = _r(hedge.get("beta"), 2)
    for k, unit in (("position_value_pln", "pln"), ("weight_pct", "pct"), ("beta_wig", "x"),
                    ("worst_scenario", "text"), ("worst_scenario_return_pct", "pct"),
                    ("worst_scenario_method", "text"), ("portfolio_beta", "x")):
        F.add(k, kf.get(k), unit)
    parts = [_L(lang, "pozycja {} zł ({} portfela), beta {}", "position {} PLN ({} of portfolio), beta {}").format(
        num(kf["position_value_pln"], 0, lang), pct(kf["weight_pct"], 1, lang), num(kf["beta_wig"], 2, lang))]
    if worst_id:
        parts.append(_L(lang, "najgorszy scenariusz {}: {} ({})", "worst scenario {}: {} ({})").format(
            worst_id, pct(worst_ret, 1, lang, signed=True), worst_method))
    return {"key_fields": kf, "facts": F, "summary": "; ".join(parts)}


# --------------------------------------------------------------------------
# Market-level extractors (ticker is used only for the sector / tilt lookup)
# --------------------------------------------------------------------------

def ex_regime(data: Dict, ticker: str, ctx: Dict, lang: str) -> Optional[Dict]:
    rg = data.get("regime") or {}
    if not rg.get("state"):
        return None
    kf = {"state": rg.get("state"), "top_risk_score": _r(rg.get("top_risk_score"), 1),
          "top_risk_band": rg.get("top_risk_band"), "distribution_days": rg.get("distribution_days"),
          "index": rg.get("index"), "indices_in_uptrend": rg.get("indices_in_uptrend"),
          "leadership": (data.get("leadership") or {}).get("pattern")}
    F = Facts("regime")
    for k, unit in (("state", "text"), ("top_risk_score", "score"), ("distribution_days", "count"),
                    ("indices_in_uptrend", "count"), ("leadership", "text")):
        F.add(k, kf[k], unit)
    s = _L(lang, "{}: {}, ryzyko szczytu {}/100, dni dystrybucji {}",
           "{}: {}, top risk {}/100, distribution days {}").format(
        kf["index"] or "WIG", kf["state"], num(kf["top_risk_score"], 0, lang), kf["distribution_days"])
    return {"key_fields": kf, "facts": F, "summary": s}


def ex_exposure(data: Dict, ticker: str, ctx: Dict, lang: str) -> Optional[Dict]:
    if not data.get("posture"):
        return None
    lim = data.get("limits") or {}
    conf = data.get("confidence") or {}
    kf = {"posture": data.get("posture"), "exposure_ceiling_pct": _r(lim.get("exposure_ceiling_pct"), 1),
          "max_risk_per_trade_pct": _r(lim.get("max_risk_per_trade_pct"), 2),
          "max_new_positions_per_week": lim.get("max_new_positions_per_week"),
          "circuit_breaker": lim.get("circuit_breaker"), "confidence": conf.get("level"),
          "score": _r(data.get("score"), 1)}
    F = Facts("exposure")
    for k, unit in (("posture", "text"), ("exposure_ceiling_pct", "pct"), ("max_risk_per_trade_pct", "pct"),
                    ("max_new_positions_per_week", "count"), ("circuit_breaker", "text"), ("confidence", "text")):
        F.add(k, kf[k], unit)
    s = _L(lang, "postawa {}, pułap ekspozycji {}, ryzyko/transakcję {}, nowe pozycje/tydz. {}, bezpiecznik {}",
           "posture {}, exposure ceiling {}, risk/trade {}, new positions/week {}, circuit breaker {}").format(
        kf["posture"], pct(kf["exposure_ceiling_pct"], 0, lang), pct(kf["max_risk_per_trade_pct"], 2, lang),
        kf["max_new_positions_per_week"], kf["circuit_breaker"])
    return {"key_fields": kf, "facts": F, "summary": s}


def ex_breadth(data: Dict, ticker: str, ctx: Dict, lang: str) -> Optional[Dict]:
    comp = data.get("composite") or {}
    if comp.get("score") is None:
        return None
    latest = data.get("latest") or {}
    kf = {"score": _r(comp.get("score"), 1), "zone": comp.get("zone"), "exposure_hint": comp.get("exposure"),
          "pct_above_200": _pct_from_ratio(latest.get("pct_above_200"), 1),
          "pct_above_50": _pct_from_ratio(latest.get("pct_above_50"), 1),
          "weakest": comp.get("weakest"), "date": latest.get("date")}
    F = Facts("breadth")
    for k, unit in (("score", "score"), ("zone", "text"), ("pct_above_200", "pct"), ("pct_above_50", "pct"),
                    ("weakest", "text")):
        F.add(k, kf[k], unit)
    s = _L(lang, "szerokość {}/100 ({}), powyżej SMA200 {} spółek", "breadth {}/100 ({}), {} of stocks above SMA200").format(
        num(kf["score"], 0, lang), kf["zone"], pct(kf["pct_above_200"], 0, lang))
    return {"key_fields": kf, "facts": F, "summary": s}


# gpw-sector-trend writes two report types under one skill name; the RRG one
# carries "mode": "rrg" (gpwlib.reports.REPORT_VARIANTS). The dossier keeps it
# under this pseudo-skill key so it can never be read as the classic report.
RRG_KEY = "gpw-sector-trend:rrg"


def rrg_reports_for(reports_by_skill: Dict[str, List[Dict]], reference: Optional[dt.date] = None) -> List[Dict]:
    """RRG reports (newest first), dated on or before ``reference``."""
    out = []
    for rep in reports_by_skill.get(RRG_KEY) or []:
        d = rep.get("data")
        if not isinstance(d, dict) or d.get("mode") != "rrg" or not isinstance(d.get("rrg"), dict):
            continue
        if reference is not None and rep.get("as_of") and rep["as_of"][:10] > reference.isoformat():
            continue
        out.append(rep)
    return out


def _find_rrg(rrg_reps: List[Dict], item_id: Optional[str], kind: str = "indices",
              sector: Optional[str] = None) -> Optional[Dict]:
    """RRG position of ``item_id`` from the newest gpw-sector-trend RRG report
    (``rrg.items[] {id, label, quadrant, rs_ratio, rs_momentum, heading_deg,
    distance, transitions[], concentrated}``). ``kind`` = ``indices`` (sector
    indices vs WIG; item id = GPW sector index key, gpwlib.constituents) or
    ``stocks`` (members of ``sector`` vs that index; item id = ticker)."""
    if not item_id:
        return None
    for rep in rrg_reps:
        r = rep["data"]["rrg"]
        if (r.get("mode") or "indices") != kind:
            continue
        if kind == "stocks" and (r.get("sector") or "").upper() != (sector or "").upper():
            continue
        for it in r.get("items") or []:
            if isinstance(it, dict) and str(it.get("id", "")).upper() == item_id.upper() and it.get("quadrant"):
                tr = [t for t in it.get("transitions") or [] if isinstance(t, dict)]
                last = tr[-1] if tr else None
                return {"quadrant": it["quadrant"], "rs_ratio": _r(it.get("rs_ratio"), 2),
                        "rs_momentum": _r(it.get("rs_momentum"), 2), "heading_deg": _r(it.get("heading_deg"), 1),
                        "distance": _r(it.get("distance"), 2),
                        "last_transition": ("{} -> {} ({})".format(last.get("from"), last.get("to"), last.get("date"))
                                            if last else None),
                        "concentrated": it.get("concentrated"), "benchmark": r.get("benchmark") or it.get("benchmark"),
                        "as_of": rep.get("as_of") or r.get("as_of"), "file": rep.get("path"),
                        "partial_week": r.get("partial_week")}
    return None


def ex_sector(data: Dict, ticker: str, ctx: Dict, lang: str) -> Optional[Dict]:
    comp = data.get("composite") or {}
    sectors = data.get("sectors") or []
    if comp.get("score") is None and not sectors:
        return None
    sk = ctx.get("sector")
    row = next((s for s in sectors if isinstance(s, dict) and s.get("key") == sk), None) if sk else None
    kf: Dict[str, Any] = {"market_score": _r(comp.get("score"), 1), "market_zone": comp.get("zone"),
                          "warnings": comp.get("warnings") or [], "ticker_sector": sk}
    F = Facts("sector")
    F.add("market_score", kf["market_score"], "score")
    F.add("market_zone", kf["market_zone"], "text")
    F.add("ticker_sector", sk, "text")
    if row:
        kf.update({"uptrend_ratio_pct": _pct_from_ratio(row.get("ratio"), 1), "trend": row.get("trend"),
                   "status": row.get("status"), "vs_wig_pp": _r(row.get("vs_wig_pp"), 2),
                   "index_20s_pct": _r(row.get("index_20s_pct"), 2), "low_sample": row.get("low_sample"),
                   "rank": 1 + sorted([s for s in sectors if s.get("ratio") is not None],
                                      key=lambda s: -s["ratio"]).index(row) if row.get("ratio") is not None else None,
                   "sectors_ranked": sum(1 for s in sectors if s.get("ratio") is not None)})
        rrg_reps = ctx.get("rrg") or []
        rg = _find_rrg(rrg_reps, sk, "indices")
        q = rg["quadrant"] if rg else None
        kf["rrg_quadrant"] = q
        if rg:
            kf.update({"rrg_rs_ratio": rg["rs_ratio"], "rrg_rs_momentum": rg["rs_momentum"],
                       "rrg_heading_deg": rg["heading_deg"], "rrg_distance": rg["distance"],
                       "rrg_last_transition": rg["last_transition"], "rrg_concentrated": rg["concentrated"],
                       "rrg_as_of": rg["as_of"], "rrg_file": rg["file"]})
        rs = _find_rrg(rrg_reps, ticker, "stocks", sector=sk)
        if rs:
            kf.update({"rrg_stock_quadrant": rs["quadrant"], "rrg_stock_rs_ratio": rs["rs_ratio"],
                       "rrg_stock_rs_momentum": rs["rs_momentum"], "rrg_stock_as_of": rs["as_of"]})
        for k, unit in (("uptrend_ratio_pct", "pct"), ("trend", "text"), ("status", "text"),
                        ("vs_wig_pp", "pp"), ("index_20s_pct", "pct"), ("low_sample", "bool"),
                        ("rank", "count"), ("rrg_quadrant", "text"), ("rrg_rs_ratio", "score"),
                        ("rrg_rs_momentum", "score"), ("rrg_last_transition", "text"),
                        ("rrg_stock_quadrant", "text")):
            if k.startswith("rrg_") and k != "rrg_quadrant" and kf.get(k) is None:
                continue
            F.add(k, kf.get(k), unit)
        s = _L(lang, "sektor {}: {} spółek w trendzie, kierunek {}, 20s vs WIG {} pp (miejsce {}/{}); rynek {}/100",
               "sector {}: {} of stocks in uptrend, direction {}, 20s vs WIG {} pp (rank {}/{}); market {}/100").format(
            sk, pct(kf["uptrend_ratio_pct"], 0, lang), kf["trend"], num(kf["vs_wig_pp"], 1, lang),
            kf["rank"], kf["sectors_ranked"], num(kf["market_score"], 0, lang))
        if q:
            s += "; RRG {} (RS-Ratio {}, RS-Momentum {})".format(q, num(kf.get("rrg_rs_ratio"), 1, lang),
                                                                 num(kf.get("rrg_rs_momentum"), 1, lang))
        if kf.get("rrg_stock_quadrant"):
            s += _L(lang, "; spółka w RRG sektora: {}", "; stock in the sector RRG: {}").format(kf["rrg_stock_quadrant"])
    else:
        s = _L(lang, "spółka bez indeksu sektorowego GPW; rynek {}/100 ({})",
               "company has no GPW sector index; market {}/100 ({})").format(
            num(kf["market_score"], 0, lang), kf["market_zone"]) if not sk else _L(
            lang, "sektor {} nieobecny w raporcie; rynek {}/100", "sector {} not in the report; market {}/100").format(
            sk, num(kf["market_score"], 0, lang))
    return {"key_fields": kf, "facts": F, "summary": s}


def ex_macro(data: Dict, ticker: str, ctx: Dict, lang: str) -> Optional[Dict]:
    m = data.get("macro") or {}
    st = m.get("state") or {}
    if not st:
        return None
    ind = data.get("indicators") or {}
    kf: Dict[str, Any] = {"composite": st.get("composite"), "score": _r(m.get("score"), 3),
                          "confidence": m.get("confidence"), "monetary": st.get("monetary"),
                          "inflation": st.get("inflation"), "pln": st.get("pln"), "spread": st.get("spread"),
                          "nbp_rate_pct": _r((ind.get("nbp_reference_rate") or {}).get("value"), 2),
                          "pl10y_pct": _r((ind.get("pl10y") or {}).get("value"), 2),
                          "hicp_yoy_pct": _r((ind.get("hicp_yoy") or {}).get("value"), 1)}
    F = Facts("macro")
    for k, unit in (("composite", "text"), ("score", "x"), ("confidence", "text"), ("monetary", "text"),
                    ("inflation", "text"), ("pln", "text"), ("spread", "text"), ("nbp_rate_pct", "pct"),
                    ("pl10y_pct", "pct"), ("hicp_yoy_pct", "pct")):
        F.add(k, kf[k], unit)
    sk = ctx.get("sector")
    tilt_group = None
    for grp, t in (data.get("sector_tilts") or {}).items():
        if isinstance(t, dict) and sk and sk in (t.get("indices") or []):
            tilt_group = grp
            kf.update({"sector_tilt_group": grp, "sector_tilt": t.get("tilt"), "sector_tilt_score": _r(t.get("score"), 2),
                       "sector_tilt_market_agrees": (t.get("market_check") or {}).get("agrees_with_tilt")})
            break
    for k, unit in (("sector_tilt_group", "text"), ("sector_tilt", "text"), ("sector_tilt_score", "x"),
                    ("sector_tilt_market_agrees", "bool")):
        F.add(k, kf.get(k), unit)
    s = _L(lang, "makro {} (wynik {}, pewność {}); stopa NBP {}, PL10Y {}, PLN {}",
           "macro {} (score {}, confidence {}); NBP rate {}, PL10Y {}, PLN {}").format(
        kf["composite"], num(kf["score"], 2, lang), kf["confidence"], pct(kf["nbp_rate_pct"], 2, lang),
        pct(kf["pl10y_pct"], 2, lang), kf["pln"])
    if tilt_group:
        s += _L(lang, "; tendencja historyczna dla grupy {}: {}", "; historical tilt for {}: {}").format(
            tilt_group, kf["sector_tilt"])
    return {"key_fields": kf, "facts": F, "summary": s}


EXTRACTORS: Dict[str, Callable] = {
    "gpw-trend-screener": ex_trend, "gpw-quality-screener": ex_quality, "gpw-valuation": ex_valuation,
    "gpw-dividend-analyzer": ex_dividend, "gpw-ownership": ex_ownership, "gpw-event-calendar": ex_events,
    "gpw-earnings-reaction": ex_earnings, "gpw-pair-trading": ex_pairs, "gpw-risk-lab": ex_risk,
    "gpw-market-regime": ex_regime, "gpw-exposure-coach": ex_exposure, "gpw-market-breadth": ex_breadth,
    "gpw-sector-trend": ex_sector, "gpw-macro-regime": ex_macro,
}


# --------------------------------------------------------------------------
# Personal sections (journal, watchlist): state files, never stale
# --------------------------------------------------------------------------

TERMINAL = ("CLOSED", "INVALIDATED")


def journal_section(theses: List[Dict], open_positions: List[Dict], ticker: str, lang: str) -> Optional[Dict]:
    mine = [t for t in theses if norm_ticker(t.get("ticker")) == ticker]
    if not mine:
        return None
    live = [t for t in mine if t.get("status") not in TERMINAL]
    th = max(live or mine, key=lambda t: (t.get("created") or t.get("created_at") or "", t.get("id") or ""))
    plan = th.get("plan") or {}
    pos = next((p for p in open_positions if p.get("thesis_id") == th.get("id")), None)
    kf = {"thesis_id": th.get("id"), "status": th.get("status"), "thesis": th.get("thesis"),
          "pillars": th.get("pillars") or [], "kill_criteria": th.get("kill_criteria") or [],
          "entry": plan.get("entry"), "stop": plan.get("stop"), "target": plan.get("target"),
          "review_date": th.get("review_date"), "conviction": th.get("conviction"),
          "open_shares": pos.get("shares") if pos else 0, "theses_total": len(mine),
          "theses_live": len(live), "paper": bool(th.get("paper"))}
    F = Facts("journal")
    F.add("status", kf["status"], "text")
    F.add("thesis_id", kf["thesis_id"], "text")
    F.add("pillars_count", len(kf["pillars"]), "count")
    F.add("kill_criteria_count", len(kf["kill_criteria"]), "count")
    for k, unit in (("entry", "pln"), ("stop", "pln"), ("target", "pln"), ("review_date", "date"),
                    ("conviction", "text"), ("open_shares", "count")):
        F.add(k, kf[k], unit)
    s = _L(lang, "teza {} ({}), filary {}, kryteria unieważnienia {}, plan {}/{}/{}",
           "thesis {} ({}), pillars {}, kill criteria {}, plan {}/{}/{}").format(
        kf["thesis_id"], kf["status"], len(kf["pillars"]), len(kf["kill_criteria"]),
        kf["entry"], kf["stop"], kf["target"])
    return {"key_fields": kf, "facts": F, "summary": s}


def _source_label(x) -> str:
    if isinstance(x, dict):
        return ":".join(str(v) for v in (x.get("skill"), x.get("kind"), x.get("date")) if v)
    return str(x)


# Risk tags of the watchlist contract (skills/gpw-watchlist/references/watchlist-contract.md)
WATCHLIST_RISK_TAGS = ("setup_damaged", "quality_red_flags", "insider_cluster_sell", "short_crowded",
                       "earnings_strong_negative", "dividend_cut_risk")
WATCHLIST_ACTIVE_STAGES = ("candidate", "watch", "ready", "entered")


def _watch_item(items: List[Dict], ticker: str) -> Optional[Dict]:
    """The item for ``ticker``: exact ticker first, then an item stored under a
    pre-rename ticker (``ticker_renamed`` alert with ``params.new_ticker``, or
    gpwlib.panel.RENAMES, e.g. CCC -> MODIVO)."""
    items = [i for i in items if isinstance(i, dict)]
    it = next((i for i in items if norm_ticker(i.get("ticker")) == ticker), None)
    if it is not None:
        return it
    for i in items:
        for a in i.get("alerts") or []:
            if isinstance(a, dict) and a.get("code") == "ticker_renamed" and \
                    norm_ticker((a.get("params") or {}).get("new_ticker")) == ticker:
                return i
    try:
        from gpwlib import panel as _panel
        for i in items:
            t = norm_ticker(i.get("ticker"))
            if t and _panel.canonical_name(t, (i.get("added") or "0000-00-00")[:10]) == ticker:
                return i
    except Exception:  # pragma: no cover - vendored lib always present
        pass
    return None


def watchlist_section(items: List[Dict], ticker: str, lang: str) -> Optional[Dict]:
    """gpw-watchlist item, read per the state contract (schema 1.x,
    skills/gpw-watchlist/references/watchlist-contract.md): ticker, stage
    (candidate/watch/ready/entered/dropped), added/updated, source + sources[],
    pivot/stop/target with levels_source/levels_date, thesis_id/status, tags
    (risk tags flagged), alerts{code, severity}, market snapshot, screener,
    upcoming_events. Unknown fields are ignored, missing ones read as null."""
    it = _watch_item(items, ticker)
    if it is None:
        return None
    sources = it.get("sources") or []
    if not sources and it.get("source") is not None:
        sources = [it.get("source")]
    alerts = [a for a in it.get("alerts") or [] if isinstance(a, dict)]
    tags = [str(x) for x in it.get("tags") or []]
    market = it.get("market") if isinstance(it.get("market"), dict) else {}
    scr = it.get("screener") if isinstance(it.get("screener"), dict) else {}
    kf = {"stage": it.get("stage"), "active": it.get("stage") in WATCHLIST_ACTIVE_STAGES,
          "pivot": _r(it.get("pivot")), "stop": _r(it.get("stop")),
          "target": _r(it.get("target")), "added": it.get("added"), "updated": it.get("updated"),
          "source": it.get("source"), "sources": [_source_label(x) for x in sources], "tags": tags,
          "risk_tags": [t for t in tags if t in WATCHLIST_RISK_TAGS],
          "levels_source": it.get("levels_source"), "levels_date": it.get("levels_date"),
          "notes": len(it.get("notes") or []), "alerts": len(alerts),
          "alert_codes": [a.get("code") for a in alerts if a.get("code")],
          "high_alerts": [a.get("code") for a in alerts if a.get("severity") == "high" and a.get("code")],
          "thesis_id": it.get("thesis_id"), "thesis_status": it.get("thesis_status"),
          "pivot_state": market.get("pivot_state"), "dist_to_pivot_pct": _r(market.get("dist_to_pivot_pct"), 2),
          "dist_to_stop_pct": _r(market.get("dist_to_stop_pct"), 2), "market_date": market.get("date"),
          "screener_score": scr.get("score"), "screener_state": scr.get("state"),
          "upcoming_events": len(it.get("upcoming_events") or []),
          "stored_ticker": it.get("ticker") if norm_ticker(it.get("ticker")) != ticker else None}
    F = Facts("watchlist")
    for k, unit in (("stage", "text"), ("pivot", "pln"), ("stop", "pln"), ("target", "pln"), ("added", "date"),
                    ("alerts", "count")):
        F.add(k, kf[k], unit)
    for k, unit in (("updated", "date"), ("levels_source", "text"), ("thesis_status", "text"),
                    ("pivot_state", "text"), ("dist_to_pivot_pct", "pct"), ("dist_to_stop_pct", "pct"),
                    ("screener_state", "text")):
        if kf[k] is not None:
            F.add(k, kf[k], unit)
    if kf["high_alerts"]:
        F.add("high_alerts", ", ".join(kf["high_alerts"]), "text")
    if kf["risk_tags"]:
        F.add("risk_tags", ", ".join(kf["risk_tags"]), "text")
    if kf["sources"]:
        F.add("sources", ", ".join(kf["sources"][-5:]), "text")
    if kf["tags"]:
        F.add("tags", ", ".join(kf["tags"]), "text")
    s = _L(lang, "etap {}, pivot {}, stop {}, od {}, źródła {}", "stage {}, pivot {}, stop {}, since {}, sources {}").format(
        kf["stage"], kf["pivot"], kf["stop"], kf["added"], len(kf["sources"]))
    if kf["high_alerts"]:
        s += _L(lang, "; pilne alerty: {}", "; urgent alerts: {}").format(", ".join(kf["high_alerts"]))
    if kf["risk_tags"]:
        s += _L(lang, "; tagi ryzyka: {}", "; risk tags: {}").format(", ".join(kf["risk_tags"]))
    return {"key_fields": kf, "facts": F, "summary": s}


# --------------------------------------------------------------------------
# Sector resolution
# --------------------------------------------------------------------------

def resolve_sector(ticker: str, reports_by_skill: Dict[str, List[Dict]], membership: Optional[Dict[str, str]] = None,
                   override: Optional[str] = None) -> Tuple[Optional[str], str]:
    """GPW sector index key for the ticker and where it came from.

    Order: explicit --sector, then any report that states it (valuation
    ``sector_index``, dividend ``sector_index``, risk-lab position ``sector``),
    then the cached GPW Benchmark membership. Returns (None, "none") when the
    company belongs to no sector index (e.g. KETY), which is a valid answer.
    """
    if override:
        return override.upper(), "cli"
    for rep in reports_by_skill.get("gpw-valuation", []):
        d = rep["data"]
        if norm_ticker(d.get("ticker")) == ticker and "sector_index" in d:
            return d.get("sector_index"), "gpw-valuation"
    for rep in reports_by_skill.get("gpw-dividend-analyzer", []):
        for c in rep["data"].get("companies") or []:
            if norm_ticker(c.get("ticker")) == ticker and "sector_index" in c:
                return c.get("sector_index"), "gpw-dividend-analyzer"
    for rep in reports_by_skill.get("gpw-risk-lab", []):
        for p in (rep["data"].get("portfolio") or {}).get("positions") or []:
            if norm_ticker(p.get("ticker")) == ticker and "sector" in p:
                return p.get("sector"), "gpw-risk-lab"
    if membership is not None:
        return membership.get(ticker), "gpw_benchmark_membership"
    return None, "none"


# --------------------------------------------------------------------------
# Collection
# --------------------------------------------------------------------------

def staleness(as_of: Optional[str], reference: dt.date, max_sessions: Optional[int]) -> Tuple[Optional[int], bool]:
    d = _date(as_of)
    if max_sessions is None:
        return (0 if d else None), False
    if d is None:
        return None, True
    behind = provenance.sessions_behind(d, reference) if d <= reference else 0
    return behind, behind > max_sessions


def build_section(spec: Dict, rep: Dict, result: Dict, reference: dt.date, max_override: Optional[int]) -> Dict:
    max_s = spec["max_sessions"] if max_override is None or spec["max_sessions"] is None else max_override
    behind, stale = staleness(rep.get("as_of"), reference, max_s)
    return {
        "skill": spec["skill"], "short": spec["short"], "scope": spec["scope"],
        "as_of": rep.get("as_of"), "stale": stale, "sessions_behind": behind, "max_sessions_behind": max_s,
        "summary": result["summary"], "key_fields": result["key_fields"],
        "source_file": rep.get("path"),
        "mode": rep.get("data", {}).get("mode") if isinstance(rep.get("data"), dict) else None,
    }


def collect(ticker: str, reports_by_skill: Dict[str, List[Dict]], reference: dt.date, lang: str = "pl",
            sector: Optional[str] = None, theses: Optional[List[Dict]] = None,
            open_positions: Optional[List[Dict]] = None, watch_items: Optional[List[Dict]] = None,
            watch_source: Optional[str] = None, journal_source: Optional[str] = None,
            max_override: Optional[int] = None, as_of_today: Optional[str] = None) -> Dict:
    """Build sections + facts from pre-loaded reports.

    ``reports_by_skill[skill]`` = list of ``{path, file, data, as_of}`` newest
    first (gpwlib.reports.iter_reports(..., newest_first=True)). For each
    skill the newest report that covers the ticker wins. Returns
    ``{sections, facts, gaps, errors}``; ``gaps`` are ``{skill, reason}``.
    """
    ticker = norm_ticker(ticker)
    ctx = {"reference": reference, "sector": sector, "rrg": rrg_reports_for(reports_by_skill, reference)}
    sections: Dict[str, Dict] = {}
    facts: List[Dict] = []
    gaps: List[Dict] = []
    errors: List[Dict] = []
    today_s = as_of_today or reference.isoformat()

    def take(spec, sec, fobj):
        sections[spec["skill"]] = sec
        for fid, val, unit in fobj.items:
            facts.append({"id": fid, "value": val, "unit": unit, "section": spec["skill"],
                          "as_of": sec["as_of"], "stale": sec["stale"]})

    for spec in SPECS:
        skill = spec["skill"]
        if spec["scope"] == "personal":
            if skill == "gpw-trade-journal":
                res = journal_section(theses or [], open_positions or [], ticker, lang) if theses is not None else None
                src = journal_source
            else:
                res = watchlist_section(watch_items or [], ticker, lang) if watch_items is not None else None
                src = watch_source
            if res is None:
                gaps.append({"skill": skill, "reason": "not_in_state" if (theses if skill == "gpw-trade-journal" else watch_items) is not None else "no_state"})
                continue
            sec = {"skill": skill, "short": spec["short"], "scope": "personal", "as_of": today_s,
                   "stale": False, "sessions_behind": 0, "max_sessions_behind": None,
                   "summary": res["summary"], "key_fields": res["key_fields"], "source_file": src, "mode": None}
            take(spec, sec, res["facts"])
            continue
        reps = reports_by_skill.get(skill) or []
        if not reps:
            gaps.append({"skill": skill, "reason": "no_report"})
            continue
        found = None
        for rep in reps:
            data = rep.get("data")
            if not isinstance(data, dict):
                continue
            try:
                res = EXTRACTORS[skill](data, ticker, ctx, lang)
            except Exception as exc:  # one broken report must not kill the dossier
                errors.append({"skill": skill, "file": rep.get("path"), "error": "{}: {}".format(type(exc).__name__, exc)})
                continue
            if res is not None:
                found = (rep, res)
                break
        if found is None:
            gaps.append({"skill": skill, "reason": "ticker_not_in_report" if spec["scope"] == "ticker" else "unusable_report",
                         "newest_file": reps[0].get("path")})
            continue
        rep, res = found
        take(spec, build_section(spec, rep, res, reference, max_override), res["facts"])
    return {"sections": sections, "facts": facts, "gaps": gaps, "errors": errors}


def order_sections(sections: Dict[str, Dict]) -> List[Dict]:
    """Stale sections first (rule: stale data is mentioned first), then registry order."""
    idx = {s["skill"]: i for i, s in enumerate(SPECS)}
    return sorted(sections.values(), key=lambda s: (not s["stale"], idx.get(s["skill"], 99)))


def provenance_summary(sections: Dict[str, Dict]) -> Dict:
    dates = sorted(s["as_of"][:10] for s in sections.values() if s.get("as_of") and s["scope"] != "personal")
    return {"as_of_min": dates[0] if dates else None, "as_of_max": dates[-1] if dates else None,
            "stale_sections": [s["skill"] for s in order_sections(sections) if s["stale"]],
            "sections_used": len(sections)}
