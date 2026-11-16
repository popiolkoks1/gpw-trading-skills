"""Evaluation engine for gpw-signal-postmortem (pure: prices come in, numbers go out).

For every recorded signal it measures what happened next on real GPW prices:

* forward return from the signal-date close (or the next session's open) over
  each horizon (default 5 / 20 / 60 sessions of the GPW calendar),
* excess return vs WIG (a total-return index) and vs the equal-weighted liquid
  GPW universe on the same dates (survivorship-free: names that stop trading
  keep their last close),
* MFE / MAE (best / worst intrabar move inside the window),
* whether the invalidation level (stop) or the pivot was touched first,
* an outcome class and bucket statistics (hit rate with a Wilson interval,
  mean / median excess, naive and date-clustered t-statistics),
* conservative, evidence-gated feedback for the skills that emitted the signals.

Thresholds are justified in references/methodology.md.
"""

from __future__ import annotations

import bisect
import math
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from gpwlib import fmt as _fmt

DEFAULT_HORIZONS = (5, 20, 60)
DEFAULT_PRIMARY = 20
DEFAULT_MIN_N = 20
# Evidence gate: |t| >= 2 is the usual 5% two-sided level -> "suggestive";
# |t| >= 3 follows Harvey, Liu & Zhu (2016) for many simultaneous tests -> "supported".
T_SUGGESTIVE = 2.0
T_SUPPORTED = 3.0
WILSON_Z = 1.96  # 95% interval

BAD_REGIMES = ("correction", "bear")
GOOD_REGIMES = ("confirmed_uptrend", "uptrend_under_pressure", "rally_attempt")

# Broad indices are judged on their absolute return (the signal is about the
# market's direction, so "excess vs WIG" would be ~0 by construction).
ABSOLUTE_INDICES = {"WIG", "WIG20", "MWIG40", "SWIG80", "WIG30", "WIG140", "WIG20TR", "MWIG40TR", "SWIG80TR"}

# Equal-weight universe filter (same spirit as gpwlib.universe.DEFAULTS, but
# only 20 sessions of history are needed because no SMA200 is computed).
EW_MIN_PRICE = 1.0
EW_MIN_TURNOVER_K = 50.0
EW_MIN_HISTORY = 20

# Score buckets: quintiles of the 0-100 priority scale used by every GPW skill.
SCORE_BUCKETS = ((80, "80-100"), (60, "60-80"), (40, "40-60"), (20, "20-40"), (0, "0-20"))
# RS rating buckets: 70 is Minervini's minimum, 80+ IBD's leaders, 90+ the top decile.
RS_BUCKETS = ((90, "90-99"), (80, "80-89"), (70, "70-79"), (0, "<70"))


# ------------------------------------------------------------------- stats

def mean(xs: Sequence[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def median(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def stdev(xs: Sequence[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def t_stat(xs: Sequence[float]) -> Optional[float]:
    """One-sample t of the mean against 0."""
    sd = stdev(xs)
    # Undefined for n < 2 or (numerically) zero dispersion - kept out of JSON as null.
    # The relative tolerance makes this deterministic across Python versions
    # (3.12's compensated sum() gives exact 0 where 3.9 leaves ~1e-16 noise).
    if not sd or sd <= 1e-9 * max(1.0, abs(mean(xs))):
        return None
    return mean(xs) / (sd / math.sqrt(len(xs)))


def welch_t(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Welch two-sample t for mean(a) - mean(b)."""
    if len(a) < 2 or len(b) < 2:
        return None
    va, vb = stdev(a) ** 2, stdev(b) ** 2
    se = math.sqrt(va / len(a) + vb / len(b))
    if se <= 1e-9 * max(1.0, abs(mean(a)), abs(mean(b))):
        return None
    return (mean(a) - mean(b)) / se


def wilson(k: int, n: int, z: float = WILSON_Z) -> Tuple[Optional[float], Optional[float]]:
    """Wilson (1927) score interval for a binomial proportion."""
    if n <= 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def _r(x, d=2):
    if x is None or (isinstance(x, float) and (math.isinf(x) or math.isnan(x))):
        return None
    return round(x, d)


# -------------------------------------------------------------- price book

class Instrument:
    __slots__ = ("name", "kind", "dates", "open", "high", "low", "close", "raw_close", "turnover_k", "_med")

    def __init__(self, name, kind, dates, open_, high, low, close, raw_close=None, turnover_k=None):
        self.name, self.kind = name, kind
        self.dates = list(dates)
        self.open, self.high, self.low, self.close = list(open_), list(high), list(low), list(close)
        self.raw_close = list(raw_close) if raw_close is not None else list(close)
        self.turnover_k = list(turnover_k) if turnover_k is not None else [0.0] * len(self.dates)
        self._med = {}

    def at_or_before(self, date: str) -> Optional[int]:
        i = bisect.bisect_right(self.dates, date) - 1
        return i if i >= 0 else None

    def exact(self, date: str) -> Optional[int]:
        i = bisect.bisect_left(self.dates, date)
        return i if i < len(self.dates) and self.dates[i] == date else None

    def med_turnover(self, i: int, n: int = 20) -> Optional[float]:
        if i + 1 < n:
            return None
        if i not in self._med:
            w = sorted(self.turnover_k[i - n + 1: i + 1])
            m = len(w) // 2
            self._med[i] = w[m] if len(w) % 2 else (w[m - 1] + w[m]) / 2
        return self._med[i]


class PriceBook:
    """Adjusted daily bars for shares and indices on one GPW session calendar."""

    def __init__(self, calendar: Iterable[str]):
        self.calendar = sorted(set(calendar))
        self.cal_idx = {d: i for i, d in enumerate(self.calendar)}
        self.instruments: Dict[str, Instrument] = {}
        self._upper: Dict[str, str] = {}
        self._ew_cache: Dict[Tuple[int, int, str], Optional[float]] = {}
        self._eligible_cache: Dict[int, List[str]] = {}

    def add(self, inst: Instrument):
        self.instruments[inst.name] = inst
        self._upper[inst.name.upper()] = inst.name

    def get(self, ticker: str) -> Optional[Instrument]:
        name = ticker if ticker in self.instruments else self._upper.get(ticker.upper())
        return self.instruments.get(name) if name else None

    def session_index(self, date: str) -> Optional[int]:
        """Calendar index of ``date`` or of the last session before it."""
        i = bisect.bisect_right(self.calendar, date) - 1
        return i if i >= 0 else None

    @property
    def last_index(self) -> int:
        return len(self.calendar) - 1

    # Equal-weight liquid universe -------------------------------------
    def eligible(self, t: int) -> List[str]:
        if t in self._eligible_cache:
            return self._eligible_cache[t]
        d = self.calendar[t]
        out = []
        for name, inst in self.instruments.items():
            if inst.kind != "share":
                continue
            i = inst.exact(d)
            if i is None or i + 1 < EW_MIN_HISTORY or inst.close[i] < EW_MIN_PRICE:
                continue
            # price filter on the unadjusted close (what the market showed)
            if inst.raw_close[i] < EW_MIN_PRICE:
                continue
            mt = inst.med_turnover(i)
            if mt is not None and mt >= EW_MIN_TURNOVER_K:
                out.append(name)
        self._eligible_cache[t] = out
        return out

    def ew_return(self, t: int, h: int, entry: str) -> Optional[float]:
        key = (t, h, entry)
        if key in self._ew_cache:
            return self._ew_cache[key]
        rets = []
        if t + h <= self.last_index:
            for name in self.eligible(t):
                inst = self.instruments[name]
                e = entry_price(inst, self, t, entry)
                if e is None:
                    continue
                x = inst.at_or_before(self.calendar[t + h])
                if x is None:
                    continue
                rets.append(inst.close[x] / e[0] - 1)
        val = mean(rets) * 100 if rets else None
        self._ew_cache[key] = val
        return val


def instruments_from_sessions(share_sessions: List[Dict], index_sessions: List[Dict]) -> PriceBook:
    """Build a PriceBook from gpw_archive.load_sessions payloads (oldest first).

    Shares use ``gpwlib.panel.build_panel`` (dividend/split adjusted). The
    panel has no open, so the adjusted open is the raw open scaled by the
    same day's adjustment factor (adjusted close / raw close).
    """
    from gpwlib import panel as gpanel  # local import keeps the engine importable without the lib

    raw_open: Dict[str, Dict[str, Tuple[float, float]]] = {}
    for sess in share_sessions:
        for row in sess["rows"]:
            if row.get("currency") not in ("PLN", "", None) or not row.get("close"):
                continue
            raw_open.setdefault(row["name"], {})[sess["date"]] = (row.get("open") or 0.0, row["close"])
    pnl = gpanel.build_panel(share_sessions)
    cal = sorted({s["date"] for s in share_sessions})
    book = PriceBook(cal)
    for name, s in pnl.items():
        ro = raw_open.get(name, {})
        opens, raws = [], []
        for d, c in zip(s.dates, s.close):
            o, rc = ro.get(d, (0.0, c))
            f = c / rc if rc else 1.0
            opens.append(o * f if o and o > 0 else None)
            raws.append(rc)
        book.add(Instrument(name, "share", s.dates, opens, s.high, s.low, s.close, raws, s.turnover_k))
    idx_open: Dict[str, Dict[str, float]] = {}
    for sess in index_sessions:
        for row in sess["rows"]:
            idx_open.setdefault(row["name"], {})[sess["date"]] = row.get("open") or 0.0
    ipnl = gpanel.build_panel(index_sessions, currency="", adjust=False)
    for name, s in ipnl.items():
        if name in book.instruments:
            continue
        oo = idx_open.get(name, {})
        opens = [(oo.get(d) or None) for d in s.dates]
        book.add(Instrument(name, "index", s.dates, opens, s.high, s.low, s.close, None, s.turnover_k))
    return book


def entry_price(inst: Instrument, book: PriceBook, t: int, entry: str) -> Optional[Tuple[float, int]]:
    """(price, calendar index of the entry bar) or None.

    ``close``: close on the signal session t (the instrument must trade that day).
    ``next_open``: open of session t+1 (falls back to that session's close when
    the archive has no open, e.g. some indices).
    """
    if entry == "close":
        i = inst.exact(book.calendar[t])
        return (inst.close[i], t) if i is not None else None
    if t + 1 > book.last_index:
        return None
    i = inst.exact(book.calendar[t + 1])
    if i is None:
        return None
    o = inst.open[i]
    return ((o if o and o > 0 else inst.close[i]), t + 1)


# -------------------------------------------------------------- evaluation

def score_bucket(score) -> Optional[str]:
    if score is None:
        return None
    for lo, label in SCORE_BUCKETS:
        if score >= lo:
            return label
    return SCORE_BUCKETS[-1][1]


def rs_bucket(rs) -> Optional[str]:
    if rs is None:
        return None
    for lo, label in RS_BUCKETS:
        if rs >= lo:
            return label
    return RS_BUCKETS[-1][1]


def basis_for(ticker: str, inst: Optional[Instrument]) -> str:
    """'absolute' for broad indices, else 'excess' (vs the chosen benchmark)."""
    if inst is not None and inst.kind == "index" and inst.name.upper() in ABSOLUTE_INDICES:
        return "absolute"
    return "excess"


def classify(direction: str, edge: float, regime_signal: Optional[str], regime_worst: Optional[str]) -> str:
    """Outcome class from the direction-signed edge (pp) at the primary horizon.

    long:  edge > 0 -> true_positive; else regime_mismatch when the regime was
           correction/bear at the signal or at any time in the window; else
           false_positive.
    avoid / exit: edge > 0 (the stock lagged) -> true_positive, else
           missed_opportunity (it rallied after we said stay out / get out).
    skip (screened, not selected): edge > 0 (it lagged) -> true_negative,
           else missed_opportunity.
    """
    if direction == "long":
        if edge > 0:
            return "true_positive"
        if regime_signal in BAD_REGIMES or regime_worst in BAD_REGIMES:
            return "regime_mismatch"
        return "false_positive"
    if direction in ("avoid", "exit"):
        return "true_positive" if edge > 0 else "missed_opportunity"
    return "true_negative" if edge > 0 else "missed_opportunity"


def _worst_regime(lookup: Callable[[str], Optional[str]], book: PriceBook, a: int, b: int) -> Optional[str]:
    rank = {"bear": 0, "correction": 1, "rally_attempt": 2, "uptrend_under_pressure": 3, "confirmed_uptrend": 4}
    worst = None
    for i in range(a, min(b, book.last_index) + 1):
        s = lookup(book.calendar[i])
        if s is not None and (worst is None or rank.get(s, 9) < rank.get(worst, 9)):
            worst = s
    return worst


def evaluate_signal(sig: Dict, book: PriceBook, cfg: Dict,
                    regime_lookup: Optional[Callable[[str], Optional[str]]] = None,
                    meta: Optional[Dict[str, Dict[str, str]]] = None) -> Dict:
    """Evaluate one ledger signal. ``cfg``: horizons, primary, entry, hit_benchmark."""
    horizons = sorted(cfg.get("horizons", DEFAULT_HORIZONS))
    primary = cfg.get("primary", DEFAULT_PRIMARY)
    entry_mode = cfg.get("entry", "close")
    bench = cfg.get("hit_benchmark", "wig")
    regime_lookup = regime_lookup or (lambda d: None)
    meta = meta or {}
    attrs = sig.get("attrs") or {}
    res = {
        "id": sig.get("id"), "source": sig["source"], "ticker": sig["ticker"], "date": sig["date"],
        "kind": sig["kind"], "direction": sig["direction"], "score": sig.get("score"),
        "execution_state": attrs.get("execution_state"), "rs_rating": attrs.get("rs_rating"),
        "on_watchlist": attrs.get("on_watchlist"),
        "sector": (meta.get("sector") or {}).get(sig["ticker"]),
        "size_segment": (meta.get("size") or {}).get(sig["ticker"]),
        "regime_at_signal": regime_lookup(sig["date"]),
        "status": None, "outcome": None, "horizons": {},
    }
    inst = book.get(sig["ticker"])
    if inst is None:
        res["status"] = "no_price_data"
        return res
    res["instrument"] = inst.kind
    if inst.kind == "index":
        res["size_segment"] = res["size_segment"] or "index"
    elif not res["size_segment"]:
        res["size_segment"] = "other"  # outside WIG20 / mWIG40 / sWIG80
    res["basis"] = basis_for(sig["ticker"], inst)
    if not book.calendar or sig["date"] < book.calendar[0]:
        res["status"] = "before_data"
        return res
    t = book.session_index(sig["date"])
    res["signal_session"] = book.calendar[t]
    e = entry_price(inst, book, t, entry_mode)
    if e is None:
        if entry_mode == "next_open" and t + 1 > book.last_index:
            res["status"] = "pending"
            for h in horizons:
                res["horizons"][str(h)] = {"status": "pending", "sessions_left": h - (book.last_index - t)}
            return res
        res["status"] = "no_entry_price"
        return res
    entry, entry_t = e
    res["entry_price"] = _r(entry, 4)
    res["entry_session"] = book.calendar[entry_t]
    wig = book.get("WIG")
    w_e = entry_price(wig, book, t, entry_mode) if wig else None
    # adjustment factor on the signal date: levels quoted by the emitting skill
    # are unadjusted; later dividends/splits rescale the adjusted history.
    i_sig = inst.exact(book.calendar[t])
    price_seen = attrs.get("price")
    factor = 1.0
    if i_sig is not None:
        if price_seen:
            factor = inst.close[i_sig] / price_seen
        elif inst.raw_close[i_sig]:
            factor = inst.close[i_sig] / inst.raw_close[i_sig]
    pivot = attrs.get("pivot") * factor if attrs.get("pivot") else None
    stop = attrs.get("invalidation") * factor if attrs.get("invalidation") else None
    sign = 1.0 if sig["direction"] == "long" else -1.0
    max_h_avail = book.last_index - t
    for h in horizons:
        hr: Dict = {}
        if h > max_h_avail:
            hr = {"status": "pending", "sessions_left": h - max_h_avail}
            res["horizons"][str(h)] = hr
            continue
        x_cal = t + h
        xi = inst.at_or_before(book.calendar[x_cal])
        exit_px = inst.close[xi]
        stale = inst.dates[xi] < book.calendar[x_cal]
        ret = (exit_px / entry - 1) * 100
        wig_ret = None
        if w_e is not None:
            wx = wig.at_or_before(book.calendar[x_cal])
            wig_ret = (wig.close[wx] / w_e[0] - 1) * 100 if wx is not None else None
        ew = book.ew_return(t, h, entry_mode)
        exc_wig = ret - wig_ret if wig_ret is not None else None
        exc_ew = ret - ew if ew is not None else None
        if res["basis"] == "absolute":
            excess = ret
        else:
            excess = exc_ew if bench == "ew" else exc_wig
        # MFE / MAE over the bars after entry (the entry bar itself when entering at the open)
        lo_i = inst.at_or_before(book.calendar[entry_t])
        a = (lo_i if entry_mode == "next_open" else lo_i + 1) if lo_i is not None else None
        highs = inst.high[a: xi + 1] if a is not None else []
        lows = inst.low[a: xi + 1] if a is not None else []
        mfe = (max(highs) / entry - 1) * 100 if highs else 0.0
        mae = (min(lows) / entry - 1) * 100 if lows else 0.0
        hr = {
            "status": "done", "exit_session": book.calendar[x_cal], "exit_price": _r(exit_px, 4),
            "return_pct": _r(ret), "wig_return_pct": _r(wig_ret), "ew_return_pct": _r(ew),
            "excess_wig_pp": _r(exc_wig), "excess_ew_pp": _r(exc_ew),
            "excess_pp": _r(excess), "edge_pp": _r(sign * excess) if excess is not None else None,
            "mfe_pct": _r(mfe), "mae_pct": _r(mae), "stale_exit": stale,
        }
        if excess is not None:
            worst = _worst_regime(regime_lookup, book, t, x_cal)
            hr["regime_worst"] = worst
            hr["outcome"] = classify(sig["direction"], sign * excess, res["regime_at_signal"], worst)
        res["horizons"][str(h)] = hr
    # stop / pivot: first touch over the longest available window
    if stop or pivot:
        res["levels"] = first_touch(inst, book, t, min(max(horizons), max_h_avail), entry, pivot, stop, entry_t,
                                    entry_mode)
    ph = res["horizons"].get(str(primary)) or {}
    if ph.get("status") == "done" and ph.get("outcome"):
        res["status"], res["outcome"] = "evaluated", ph["outcome"]
    elif ph.get("status") == "done":
        res["status"] = "no_benchmark"
    else:
        res["status"] = "pending"
        res["sessions_left"] = ph.get("sessions_left")
    return res


def first_touch(inst: Instrument, book: PriceBook, t: int, window: int, entry: float, pivot: Optional[float],
                stop: Optional[float], entry_t: int, entry_mode: str) -> Dict:
    """Which level was touched first after entry: 'pivot', 'stop', 'same_session' or 'none'.

    When the entry is already at/above the pivot (a breakout or extended
    state) only the stop is tracked (``pivot_already_above``)."""
    out = {"pivot": _r(pivot, 4), "invalidation": _r(stop, 4), "window_sessions": window,
           "pivot_already_above": bool(pivot and entry >= pivot), "first": "none",
           "stop_hit_session": None, "pivot_hit_session": None}
    if window <= 0:
        out["first"] = "pending"
        return out
    start_cal = entry_t if entry_mode == "next_open" else t + 1
    for c in range(start_cal, t + window + 1):
        i = inst.exact(book.calendar[c])
        if i is None:
            continue
        hit_stop = stop is not None and inst.low[i] <= stop
        hit_piv = (pivot is not None and not out["pivot_already_above"] and inst.high[i] >= pivot)
        if hit_stop and out["stop_hit_session"] is None:
            out["stop_hit_session"] = book.calendar[c]
        if hit_piv and out["pivot_hit_session"] is None:
            out["pivot_hit_session"] = book.calendar[c]
        if out["first"] == "none" and (hit_stop or hit_piv):
            out["first"] = "same_session" if (hit_stop and hit_piv) else ("stop" if hit_stop else "pivot")
    return out


# ----------------------------------------------------------------- buckets

DIMENSIONS = ("all", "source", "kind", "source_kind", "priority", "execution_state", "rs_bucket",
              "regime_at_signal", "sector", "size_segment", "on_watchlist", "basis")


def direction_group(direction: str) -> str:
    return {"long": "long", "avoid": "avoid_exit", "exit": "avoid_exit"}.get(direction, "skip")


def dim_value(r: Dict, dim: str) -> Optional[str]:
    if dim == "all":
        return "all"
    if dim == "source_kind":
        return "{}:{}".format(r["source"], r["kind"])
    if dim == "priority":
        return score_bucket(r.get("score"))
    if dim == "rs_bucket":
        return rs_bucket(r.get("rs_rating"))
    if dim == "on_watchlist":
        v = r.get("on_watchlist")
        return None if v is None else ("yes" if v else "no")
    v = r.get(dim)
    if dim == "regime_at_signal" and v is None:
        return "unknown"
    return None if v is None else str(v)


def bucket_stats(rows: List[Tuple[Dict, Dict]], min_n: int) -> Dict:
    """rows: (result, horizon_result) pairs with a defined edge."""
    edges = [h["edge_pp"] for _, h in rows]
    excess = [h["excess_pp"] for _, h in rows]
    hits = sum(1 for e in edges if e > 0)
    n = len(edges)
    lo, hi = wilson(hits, n)
    by_date: Dict[str, List[float]] = {}
    for r, h in rows:
        by_date.setdefault(r.get("signal_session") or r["date"], []).append(h["edge_pp"])
    date_means = [mean(v) for v in by_date.values()]
    ew = [h["excess_ew_pp"] for _, h in rows if h.get("excess_ew_pp") is not None]
    return {
        "n": n, "hits": hits,
        "hit_rate": _r(hits / n, 4) if n else None,
        "hit_rate_ci95": [_r(lo, 4), _r(hi, 4)],
        "mean_excess_pct": _r(mean(excess)), "median_excess_pct": _r(median(excess)),
        "mean_edge_pp": _r(mean(edges)), "median_edge_pp": _r(median(edges)),
        "t_stat": _r(t_stat(edges)), "n_dates": len(by_date), "t_stat_by_date": _r(t_stat(date_means)),
        "mean_excess_ew_pp": _r(mean(ew)),
        "mean_return_pct": _r(mean([h["return_pct"] for _, h in rows])),
        "mean_mfe_pct": _r(mean([h["mfe_pct"] for _, h in rows])),
        "mean_mae_pct": _r(mean([h["mae_pct"] for _, h in rows])),
        "status": "ok" if n >= min_n else "insufficient",
    }


def build_buckets(results: List[Dict], horizons: Sequence[int], min_n: int,
                  dimensions: Sequence[str] = DIMENSIONS) -> List[Dict]:
    out = []
    for h in horizons:
        groups: Dict[Tuple[str, str, str], List[Tuple[Dict, Dict]]] = {}
        for r in results:
            hr = (r.get("horizons") or {}).get(str(h)) or {}
            if hr.get("status") != "done" or hr.get("edge_pp") is None:
                continue
            g = direction_group(r["direction"])
            for dim in dimensions:
                v = dim_value(r, dim)
                if v is None:
                    continue
                groups.setdefault((g, dim, v), []).append((r, hr))
        for (g, dim, v), rows in sorted(groups.items(), key=lambda kv: (kv[0][0], DIMENSIONS.index(kv[0][1])
                                                                       if kv[0][1] in DIMENSIONS else 99, kv[0][2])):
            item = {"dimension": dim, "bucket": v, "direction_group": g, "horizon": h}
            item.update(bucket_stats(rows, min_n))
            out.append(item)
    return out


def outcome_counts(results: List[Dict]) -> Dict[str, int]:
    c: Dict[str, int] = {}
    for r in results:
        key = r["outcome"] if r["status"] == "evaluated" else r["status"]
        c[key] = c.get(key, 0) + 1
    return c


# ---------------------------------------------------------------- feedback

def _edges(results, h, pred) -> List[float]:
    out = []
    for r in results:
        hr = (r.get("horizons") or {}).get(str(h)) or {}
        if hr.get("status") == "done" and hr.get("edge_pp") is not None and pred(r):
            out.append(hr["edge_pp"])
    return out


def _verdict(t: Optional[float], na: int, nb: int, min_n: int) -> str:
    if na < min_n or nb < min_n or t is None:
        return "insufficient"
    if t >= T_SUPPORTED:
        return "supported"
    if t >= T_SUGGESTIVE:
        return "suggestive"
    if t <= -T_SUPPORTED:
        return "contradicted"
    if t <= -T_SUGGESTIVE:
        return "suggestive_against"
    return "not_supported"


def _compare(results, h, min_n, name, target, parameter, pred_a, pred_b, label_a, label_b, texts) -> Dict:
    a, b = _edges(results, h, pred_a), _edges(results, h, pred_b)
    t = welch_t(a, b)
    v = _verdict(t, len(a), len(b), min_n)
    diff = (mean(a) - mean(b)) if a and b else None
    ev = {"group_a": label_a, "n_a": len(a), "mean_edge_a_pp": _r(mean(a)), "group_b": label_b, "n_b": len(b),
          "mean_edge_b_pp": _r(mean(b)), "diff_pp": _r(diff), "welch_t": _r(t), "horizon": h}
    ev["group_a"], ev["group_b"] = _lbl(label_a, "en"), _lbl(label_b, "en")
    st = {lang: texts[v][lang].format(a=_lbl(label_a, lang), b=_lbl(label_b, lang), d=_fmt_pp(diff, lang),
                                      na=len(a), nb=len(b), t=_fmt_t(t, lang), m=min_n) for lang in ("pl", "en")}
    return {"id": name, "target_skill": target, "parameter": parameter, "verdict": v,
            "action": "review" if v in ("supported", "contradicted") else "none",
            "statement": st, "evidence": ev}


def _lbl(label, lang):
    return label.get(lang, label.get("en")) if isinstance(label, dict) else label


def _fmt_pp(x, lang="en"):
    return "—" if x is None else ("+" if x > 0 else "") + _fmt.num(x, 1, lang)


def _fmt_t(x, lang="en"):
    return "—" if x is None else _fmt.num(x, 1, lang)


_CMP_TEXT = {
    "insufficient": {"pl": "{a} vs {b}: za mało danych (n={na} vs {nb}, minimum {m}) - brak wniosku, nic nie zmieniaj.",
                     "en": "{a} vs {b}: not enough data (n={na} vs {nb}, minimum {m}) - no conclusion, change nothing."},
    "supported": {"pl": "{a} wypada lepiej niż {b} o {d} pp (n={na} vs {nb}, t={t}). Ranking jest potwierdzony; obniżanie progu nie ma podstaw.",
                  "en": "{a} beats {b} by {d} pp (n={na} vs {nb}, t={t}). The ranking is supported; lowering the threshold is not."},
    "suggestive": {"pl": "{a} wypada lepiej niż {b} o {d} pp (n={na} vs {nb}, t={t}) - sygnał wstępny (t<3 przy wielu testach), bez zmian progów.",
                   "en": "{a} beats {b} by {d} pp (n={na} vs {nb}, t={t}) - suggestive only (t<3 with many tests), keep thresholds."},
    "not_supported": {"pl": "Brak istotnej różnicy między {a} a {b} ({d} pp, n={na} vs {nb}, t={t}) - zmiana progu nie ma podstaw.",
                      "en": "No significant difference between {a} and {b} ({d} pp, n={na} vs {nb}, t={t}) - a threshold change is not supported."},
    "suggestive_against": {"pl": "{a} wypada gorzej niż {b} ({d} pp, n={na} vs {nb}, t={t}) - sygnał wstępny, obserwuj dalej.",
                           "en": "{a} trails {b} ({d} pp, n={na} vs {nb}, t={t}) - suggestive only, keep watching."},
    "contradicted": {"pl": "{a} wypada wyraźnie gorzej niż {b} ({d} pp, n={na} vs {nb}, t={t}) - warto przejrzeć tę regułę ręcznie.",
                     "en": "{a} clearly trails {b} ({d} pp, n={na} vs {nb}, t={t}) - worth a manual review of this rule."},
}


def _single(results, h, min_n, name, target, parameter, pred, label) -> Dict:
    xs = _edges(results, h, pred)
    n = len(xs)
    hits = sum(1 for x in xs if x > 0)
    lo, hi = wilson(hits, n)
    t = t_stat(xs)
    # Zero dispersion with every edge on one side: the evidence is as strong as it gets,
    # so judge it on the Wilson interval alone instead of an undefined t.
    t_eff = t
    if t is None and n >= 2 and xs and mean(xs) != 0:
        t_eff = math.copysign(float("inf"), mean(xs))
    if n < min_n:
        v = "insufficient"
    elif lo is not None and lo > 0.5 and t_eff is not None and t_eff >= T_SUGGESTIVE:
        v = "supported" if t_eff >= T_SUPPORTED else "suggestive"
    elif hi is not None and hi < 0.5 and t_eff is not None and t_eff <= -T_SUGGESTIVE:
        v = "contradicted" if t_eff <= -T_SUPPORTED else "suggestive_against"
    else:
        v = "not_supported"
    def fmt(lang):
        return dict(a=_lbl(label, lang), n=n, m=min_n, hr="—" if not n else "{:.0f}%".format(100 * hits / n),
                    lo="—" if lo is None else "{:.0f}".format(100 * lo), hi="—" if hi is None else "{:.0f}".format(100 * hi),
                    e=_fmt_pp(mean(xs), lang), t=_fmt_t(t, lang))
    texts = {
        "insufficient": {"pl": "{a}: n={n} (minimum {m}) - za mało danych na wniosek.",
                         "en": "{a}: n={n} (minimum {m}) - too few to conclude."},
        "supported": {"pl": "{a}: trafność {hr} (95% CI {lo}-{hi}%), średnio {e} pp, t={t} - sygnał działa.",
                      "en": "{a}: hit rate {hr} (95% CI {lo}-{hi}%), mean {e} pp, t={t} - the signal works."},
        "suggestive": {"pl": "{a}: trafność {hr} (95% CI {lo}-{hi}%), średnio {e} pp, t={t} - wstępnie pozytywnie.",
                       "en": "{a}: hit rate {hr} (95% CI {lo}-{hi}%), mean {e} pp, t={t} - tentatively positive."},
        "not_supported": {"pl": "{a}: trafność {hr} (95% CI {lo}-{hi}%), średnio {e} pp, t={t} - nie odróżnia się od losowości.",
                          "en": "{a}: hit rate {hr} (95% CI {lo}-{hi}%), mean {e} pp, t={t} - indistinguishable from chance."},
        "suggestive_against": {"pl": "{a}: trafność {hr} (95% CI {lo}-{hi}%), średnio {e} pp, t={t} - wstępnie negatywnie.",
                               "en": "{a}: hit rate {hr} (95% CI {lo}-{hi}%), mean {e} pp, t={t} - tentatively negative."},
        "contradicted": {"pl": "{a}: trafność {hr} (95% CI {lo}-{hi}%), średnio {e} pp, t={t} - sygnał szkodzi; przejrzyj regułę.",
                         "en": "{a}: hit rate {hr} (95% CI {lo}-{hi}%), mean {e} pp, t={t} - the signal hurts; review the rule."},
    }
    return {"id": name, "target_skill": target, "parameter": parameter, "verdict": v,
            "action": "review" if v in ("supported", "contradicted") else "none",
            "statement": {lang: texts[v][lang].format(**fmt(lang)) for lang in ("pl", "en")},
            "evidence": {"n": n, "hits": hits, "hit_rate_ci95": [_r(lo, 4), _r(hi, 4)], "mean_edge_pp": _r(mean(xs)),
                         "t_stat": _r(t), "horizon": h}}


def build_feedback(results: List[Dict], primary: int, min_n: int, demo: bool = False) -> Dict:
    h = primary
    longs = [r for r in results if r["direction"] == "long"]
    sugg: List[Dict] = []
    # 1. per (source, kind): does each signal type beat its benchmark?
    kinds = sorted({(r["source"], r["kind"], r["direction"]) for r in results})
    for src, kind, d in kinds:
        sugg.append(_single(results, h, min_n, "signal_{}_{}".format(src, kind), src, None,
                            lambda r, s=src, k=kind: r["source"] == s and r["kind"] == k,
                            "{} / {} ({})".format(src, kind, d)))
    # 2. priority: top quintile vs the rest (screener watchlist priority, any scored source)
    for src in sorted({r["source"] for r in longs if r.get("score") is not None}):
        sugg.append(_compare(longs, h, min_n, "priority_{}".format(src), src, "priority_threshold",
                             lambda r, s=src: r["source"] == s and (r.get("score") or -1) >= 80,
                             lambda r, s=src: r["source"] == s and r.get("score") is not None and r["score"] < 80,
                             {"pl": "priorytet >= 80", "en": "priority >= 80"},
                             {"pl": "priorytet < 80", "en": "priority < 80"}, _CMP_TEXT))
    # 3. RS rating: top decile vs the rest
    if any(r.get("rs_rating") is not None for r in longs):
        sugg.append(_compare(longs, h, min_n, "rs_rating", "gpw-trend-screener", "min_rs",
                             lambda r: (r.get("rs_rating") or -1) >= 90,
                             lambda r: r.get("rs_rating") is not None and r["rs_rating"] < 90,
                             "RS >= 90", "RS < 90", _CMP_TEXT))
    # 4. execution states: each state vs zero
    for st in sorted({r.get("execution_state") for r in longs if r.get("execution_state")}):
        sugg.append(_single(longs, h, min_n, "execution_state_{}".format(st), "gpw-trend-screener",
                            "execution_state", lambda r, s=st: r.get("execution_state") == s,
                            {"pl": "stan wykonania {}".format(st), "en": "execution state {}".format(st)}))
    # 5. regime filter: long signals in correction/bear vs in an uptrend
    if any(r.get("regime_at_signal") for r in longs):
        sugg.append(_compare(longs, h, min_n, "regime_filter", "gpw-exposure-coach", "regime_cap",
                             lambda r: r.get("regime_at_signal") in GOOD_REGIMES,
                             lambda r: r.get("regime_at_signal") in BAD_REGIMES,
                             {"pl": "sygnały long w trendzie wzrostowym", "en": "long signals in an uptrend"},
                             {"pl": "sygnały long w korekcie/bessie", "en": "long signals in correction/bear"}, _CMP_TEXT))
    # 6. near misses: did the watchlist filter leave money on the table?
    if any(r["direction"] == "skip" for r in results):
        sugg.append(_compare(results, h, min_n, "watchlist_filter", "gpw-trend-screener", "watchlist_band",
                             lambda r: r["direction"] == "long" and r["source"] == "gpw-trend-screener",
                             lambda r: r["direction"] == "skip",
                             {"pl": "lista obserwacyjna", "en": "watchlist"},
                             {"pl": "pominięte kandydatury (wynik = o ile były słabsze)", "en": "near misses (edge = how much they lagged)"},
                             _CMP_TEXT))
    passed = [s for s in sugg if s["verdict"] in ("supported", "contradicted")]
    by_skill: Dict[str, Dict] = {}
    for s in sugg:
        if s["parameter"]:
            by_skill.setdefault(s["target_skill"], {})[s["parameter"]] = {"verdict": s["verdict"], "id": s["id"]}
    summary = {
        "pl": ("Żadna sugestia nie przeszła progu dowodowego (n >= {m}, |t| >= {t}). Nie zmieniaj ustawień innych skilli.".format(m=min_n, t=T_SUPPORTED)
               if not passed else "{k} sugestii przeszło próg dowodowy (n >= {m}, |t| >= {t}); to wskazówki do ręcznego przeglądu.".format(k=len(passed), m=min_n, t=T_SUPPORTED)),
        "en": ("No suggestion passed the evidence gate (n >= {m}, |t| >= {t}). Do not change other skills' settings.".format(m=min_n, t=T_SUPPORTED)
               if not passed else "{k} suggestion(s) passed the evidence gate (n >= {m}, |t| >= {t}); review them manually.".format(k=len(passed), m=min_n, t=T_SUPPORTED)),
    }
    return {
        "demo": demo,
        "horizon": h,
        "evidence_gate": {"min_n": min_n, "t_suggestive": T_SUGGESTIVE, "t_supported": T_SUPPORTED,
                          "hit_rate_ci": "wilson_95"},
        "auto_apply": False,
        "summary": summary,
        "passed": len(passed),
        "suggestions": sugg,
        "by_skill": by_skill,
        "note": {"pl": "Tylko sugestie - ten skill nigdy nie zmienia ustawień innych skilli.",
                 "en": "Suggestions only - this skill never edits other skills' settings."},
    }


# -------------------------------------------------------------------- demo

DEMO_LOOKBACK = 252          # 52 weeks of sessions (Minervini/O'Neil "new high")
DEMO_REARM = 20              # one signal per stock per ~month (avoids counting one breakout many times)
DEMO_MIN_TURNOVER_K = 200.0  # the trend screener's liquidity floor (min_turnover_k = 200)
DEMO_STOP_PCT = 8.0          # Minervini's 7-8% maximum loss rule -> invalidation 8% under the pivot
DEMO_RS_WINDOW = 126         # 6-month return for the RS-like score


def demo_signals(book: PriceBook) -> List[Dict]:
    """Deterministic demo rule on real prices: first close above the prior
    52-week closing high (liquid shares only). Score = percentile of the
    6-month return across the eligible universe that day (RS-like, 0-100).

    These are NOT signals of any GPW skill; reports must label them as demo.
    """
    out: List[Dict] = []
    last_sig: Dict[str, int] = {}
    shares = [i for i in book.instruments.values() if i.kind == "share"]
    for t in range(DEMO_LOOKBACK + 1, book.last_index + 1):
        d = book.calendar[t]
        perf = {}
        hits = []
        for inst in shares:
            i = inst.exact(d)
            if i is None or i < DEMO_LOOKBACK + 1 or inst.raw_close[i] < EW_MIN_PRICE:
                continue
            mt = inst.med_turnover(i)
            if mt is None or mt < DEMO_MIN_TURNOVER_K:
                continue
            if i >= DEMO_RS_WINDOW:
                perf[inst.name] = inst.close[i] / inst.close[i - DEMO_RS_WINDOW] - 1
            prior_high = max(inst.close[i - DEMO_LOOKBACK: i])
            if inst.close[i] > prior_high and inst.close[i - 1] <= max(inst.close[i - 1 - DEMO_LOOKBACK: i - 1]):
                if t - last_sig.get(inst.name, -10 ** 9) >= DEMO_REARM:
                    hits.append((inst, i, prior_high))
        ranked = sorted(perf.values())
        for inst, i, ph in hits:
            last_sig[inst.name] = t
            p = perf.get(inst.name)
            score = 100.0 * bisect.bisect_left(ranked, p) / max(1, len(ranked) - 1) if p is not None else None
            score = min(100.0, score) if score is not None else None
            attrs = {"price": inst.raw_close[i], "pivot": ph / (inst.close[i] / inst.raw_close[i]),
                     "invalidation": ph * (1 - DEMO_STOP_PCT / 100) / (inst.close[i] / inst.raw_close[i]),
                     "rs_rating": None, "demo": True}
            out.append({"id": "demo_{}_{}".format(inst.name, d), "source": "demo", "ticker": inst.name, "date": d,
                        "kind": "demo_52w_high", "direction": "long",
                        "score": round(score, 1) if score is not None else None, "attrs": attrs})
    return out
