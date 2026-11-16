"""Turn per-session archive payloads into per-instrument time series.

Corporate-action handling
-------------------------
The GPW archive publishes *unadjusted* closing prices, but also the session's
"Zmiana kursu %" (change vs. the reference price). After a split, reverse
split or rights issue GPW adjusts the reference price, so the published change
stays small while the raw close jumps. We use that to rebuild an adjusted
series without any third-party data:

    implied_prev = close_t / (1 + change_pct_t / 100)
    factor_t     = implied_prev / close_{t-1}

If |factor_t - 1| > ADJ_TOLERANCE we treat it as a corporate action
(dividend, split, reverse split, rights issue) and multiply every earlier
price by factor_t (backward adjustment, like most charting tools). Small
mismatches come from rounding of the 2-decimal change and are ignored. Each
detected adjustment is reported so the caller can show it to the user.

The check is only valid between *consecutive* archive sessions: change% on
day t refers to the reference price of day t, i.e. the (adjusted) close of
the instrument's previous trading session. When whole sessions are missing
from the local cache (e.g. an ``--offline`` run on a cache with a hole), the
previous close we hold is older than the one GPW used, so the formula would
read the market move of the missing day(s) as a corporate action - for
hundreds of stocks at once. ``build_panel`` therefore never infers an
adjustment across a gap of missing sessions (``gpw_archive`` marks them with
``missing_before``; callers may also pass ``gaps=``); such joins are listed
in ``Series.gap_joins`` instead. The archive table has no reference-price
column, so change% is the only (and robust) reference-price signal.

Memory: ``build_panel`` accepts any iterable of sessions (e.g.
``gpw_archive.iter_sessions``), so a long window can be streamed one session
at a time; ``names=`` keeps only some instruments, ``fields=`` drops unused
columns and ``compact=True`` stores prices as ``array('d')`` (8 bytes per
value instead of ~32 for a list of floats). See :func:`build_panel`.
"""

from __future__ import annotations

from array import array
from bisect import bisect_right
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Empirical check on 320 GPW sessions (Nov 2026, ~128k share-days): 99.8% of
# mismatches are < 0.1% (rounding of the 2-decimal change); 0.1-0.5% occurs
# 9 times; 1-3% mismatches are ordinary dividends (GPW cuts the reference
# price on the ex-date) and >3% are large dividends and splits. 0.5% keeps
# rounding out and catches dividends and splits, which makes the adjusted
# series total-return-like (dividend-adjusted), as in most charting tools.
ADJ_TOLERANCE = 0.005


# --------------------------------------------------------------------------
# Renamed tickers
# --------------------------------------------------------------------------
# The archive lists each share under the short name (ticker) valid on that
# day, so a renamed company appears as two series: the old name stops, the
# new one starts on the next session with the reference price carried over.
# ``RENAMES`` is an explicit, editable alias table ``{new: [(old,
# last_old_date), ...]}``: rows of ``old`` dated on or before
# ``last_old_date`` belong to ``new``. Chains (IQP -> PUNKPIRAT -> RAEN ->
# VIRTUS) resolve to the newest name. A name reused later by another company
# is safe because rows after ``last_old_date`` are not remapped.
#
# Sources (checked 2026-11-16): every pair was found in the cached GPW archive
# (type=10, 2019-01..2026-11) as "old name's last session -> new name's first
# session" with the new name's reference price (close / (1 + change%)) within
# 2% of the old close; the current ISIN of the new name (gpw.pl quotes table,
# see fundamentals.isin_map) still carries the old name's code (PLCCC... for
# MODIVO, PLBZ... for ERSTEPL, PLNEPTS... for YANOSIK, ...). ESPI current
# reports confirm some of them: BUMECHDEF "Zmiana nazwy skróconej i oznaczenia
# akcji Emitenta" (geru_id 495204, 2026-08-12), LUNAGLBAL "Rejestracja zmian
# statutu Emitenta, w tym zmiany nazwy" (geru_id 498601, 2026-10-08). Use
# :func:`detect_rename_candidates` to find new ones; add them here by hand.
RENAMES = {
    # 2024-11 .. 2026-11 (the window used by the 1.x skills)
    "WISEFIN": [("IBSM", "2026-11-12")],        # PLPLPGR00010
    "EDGSA": [("PGFGROUP", "2026-10-16")],      # PLZSTAL00012, European Defence Group
    "LUNAGLBAL": [("LESS", "2026-10-16")],      # PLINTGR00013, ESPI 498601
    "WISEENERG": [("FMG", "2026-10-02")],       # PLTHP0000011, Wise Energy
    "BUMECHDEF": [("CAPITAL", "2026-08-12")],   # PLCPTLP00015, ESPI 495204
    "MULTIQURE": [("PURE", "2026-08-07")],      # PLPRBLG00010 (Pure Biologics)
    "ERSTEPL": [("SANPL", "2026-04-27")],       # PLBZ00000044, Santander Bank Polska -> Erste Bank Polska
    "MODIVO": [("CCC", "2026-02-18")],          # PLCCC0000016, CCC -> MODIVO
    "VIRTUS": [("RAEN", "2026-02-12")],         # PLIQPRT00017, Grupa Virtus
    "YANOSIK": [("NEPTIS", "2026-02-05")],      # PLNEPTS00011
    "MCR": [("MERCOR", "2025-12-17")],          # PLMRCOR00016
    "FOODHUB": [("PAMAPOL", "2025-10-08")],     # PLPMPOL00031
    "ECBSA": [("BEDZIN", "2025-05-22")],        # PLECBDZ00013
    "CPIEUROPE": [("IIAAV", "2025-03-28")],     # AT0000A21KS2 (Immofinanz -> CPI Europe)
    # 2019-01 .. 2024-10 (same evidence; for long backtests)
    "EQUNICO": [("RESBUD", "2024-10-04")],      # PLRESBD00032
    "JRH": [("JRHOLDING", "2024-08-23")],       # PLJRINV00013
    "DMGROUP": [("GLCOSMED", "2024-03-08")],
    "YARRL": [("UNIMA", "2023-12-21")],
    "CAPITEA": [("GETBACK", "2023-10-23")],
    "TEXT": [("LIVECHAT", "2023-09-21")],
    "FABRITY": [("K2HOLDING", "2023-08-24")],
    "CYBERFLKS": [("R22", "2023-08-02")],
    "SNTVERSE": [("BIOMEDLUB", "2023-07-17")],
    "GRENEVIA": [("FAMUR", "2023-04-05")],
    "ONESANO": [("SKOTAN", "2023-03-28")],
    "RAEN": [("PUNKPIRAT", "2023-03-20")],
    "UNFOLD": [("VENTUREIN", "2022-12-07")],
    "PJPMAKRUM": [("PROJPRZEM", "2022-10-14")],
    "VIGOPHOTN": [("VIGOSYS", "2022-10-06")],
    "NTCAPITAL": [("INVISTA", "2022-09-19")],
    "LESS": [("GROCLIN", "2022-09-01")],
    "DIGITANET": [("4FUNMEDIA", "2022-04-29")],
    "MOLECURE": [("OAT", "2022-04-01")],
    "HUUUGE": [("HUUUGE-S144", "2022-03-03")],  # Reg S/144A line merged into the listed line
    "COMPREMUM": [("POZBUD", "2022-01-21")],
    "GREENX": [("PRAIRIE", "2021-12-16")],
    "GIGROUP": [("WORKSERV", "2021-11-22")],
    "MANYDEV": [("PLATYNINW", "2021-09-10")],
    "ALTUS": [("ALTUSTFI", "2021-09-03")],
    "IMPERIO": [("IMPERA", "2021-06-16")],
    "SATIS": [("IALBGR", "2021-04-29")],
    "K2HOLDING": [("K2INTERNT", "2021-02-17")],
    "PGFGROUP": [("ZASTAL", "2021-02-11")],
    "3RGAMES": [("HUBSTYLE", "2021-01-18")],
    "NEXITY": [("EVEREST", "2020-10-22")],
    "NOVAVISGR": [("RUBICON", "2020-09-30")],
    "AIGAMES": [("SETANTA", "2020-07-03")],
    "PUNKPIRAT": [("IQP", "2020-05-29")],
    "DIGITREE": [("SARE", "2019-11-27")],
    "DEVELIA": [("LCCORP", "2019-09-09")],
    "VRG": [("VISTULA", "2019-06-21")],
    "BNPPPL": [("BGZBNPP", "2019-04-02")],
    "REINO": [("GRAVITON", "2019-01-22")],
}


def canonical_name(name: str, date: str, renames: Optional[Dict[str, List[Tuple[str, str]]]] = None) -> str:
    """Newest ticker for ``name`` as listed on ``date`` (ISO), following chains."""
    table = RENAMES if renames is None else renames
    rev = _reverse(table)
    seen = set()
    cur = name
    while cur in rev and cur not in seen:
        seen.add(cur)
        nxt = None
        for new, last in rev[cur]:
            if date <= last:
                nxt = new
                break
        if nxt is None:
            break
        cur = nxt
    return cur


def _reverse(table: Dict[str, List[Tuple[str, str]]]) -> Dict[str, List[Tuple[str, str]]]:
    rev: Dict[str, List[Tuple[str, str]]] = {}
    for new, olds in table.items():
        for old, last in olds:
            rev.setdefault(old, []).append((new, last))
    for v in rev.values():
        v.sort(key=lambda x: x[1])  # earliest rename first (a name reused twice)
    return rev


def detect_rename_candidates(sessions: List[Dict], currency: str = "PLN", max_price_gap: float = 0.02,
                             max_turnover_ratio: float = 20.0,
                             renames: Optional[Dict[str, List[Tuple[str, str]]]] = None) -> List[Dict]:
    """Heuristic: a name that disappears paired with a name that appears on the
    next session, with the new name's implied reference price
    (``close / (1 + change% / 100)``) within ``max_price_gap`` of the old
    close and a comparable turnover (ratio of the old name's last-20-session
    median to the new name's first-20-session median within
    ``max_turnover_ratio``; when either median is zero the ratio is ``None``
    and only the price continuity counts).

    Only *reports* candidates ``{old, new, last_old_date, first_new_date,
    old_close, new_close, implied_prev, price_gap, turnover_ratio, known}``
    (``known`` = already in the alias table). It never merges anything: check
    the pair (company card / ESPI) and add it to :data:`RENAMES`.
    """
    table = RENAMES if renames is None else renames
    def rows_of(sess):
        out = {}
        for r in sess["rows"]:
            cur = r.get("currency") or ""
            if currency and cur and cur != currency:
                continue
            if r.get("close") and r["close"] > 0:
                out[r["name"]] = r
        return out
    by_day = [rows_of(s) for s in sessions]
    out: List[Dict] = []
    for i in range(1, len(sessions)):
        a, b = by_day[i - 1], by_day[i]
        gone = [n for n in a if n not in b]
        new = [n for n in b if n not in a]
        if not gone or not new:
            continue
        # a name that only skipped a session (no trades) is not gone
        later = set().union(*by_day[i:i + 5])
        gone = [n for n in gone if n not in later]
        # ... and a name seen shortly before is a comeback, not a new name
        earlier = set().union(*by_day[max(0, i - 6):i - 1]) if i > 1 else set()
        new = [n for n in new if n not in earlier]
        for g in gone:
            for n in new:
                ca, cb, ch = a[g]["close"], b[n]["close"], b[n].get("change_pct")
                if ch is None or ch <= -100:
                    continue
                implied = cb / (1 + ch / 100.0)
                gap = abs(implied / ca - 1)
                if gap > max_price_gap:
                    continue
                t_old = sorted(by_day[j][g].get("turnover_k") or 0.0 for j in range(max(0, i - 20), i)
                               if g in by_day[j])
                t_new = sorted(by_day[j][n].get("turnover_k") or 0.0 for j in range(i, min(len(by_day), i + 20))
                               if n in by_day[j])
                mo = t_old[len(t_old) // 2] if t_old else 0.0
                mn = t_new[len(t_new) // 2] if t_new else 0.0
                if mo <= 0 or mn <= 0:
                    ratio = None  # illiquid on one side: price continuity is the only evidence
                else:
                    ratio = max(mo, mn) / min(mo, mn)
                    if ratio > max_turnover_ratio:
                        continue
                last_old = sessions[i - 1]["date"]
                out.append({"old": g, "new": n, "last_old_date": last_old, "first_new_date": sessions[i]["date"],
                            "old_close": ca, "new_close": cb, "implied_prev": round(implied, 6),
                            "price_gap": round(gap, 6),
                            "turnover_ratio": None if ratio is None else round(ratio, 3),
                            "known": any(o == g and d == last_old for o, d in table.get(n, []))})
    return out


class Series:
    """One instrument's daily bars, oldest first.

    ``open`` and ``volume`` were added in gpwlib 1.2: ``open`` is adjusted
    like ``close`` (falls back to the close when the archive row has none);
    ``volume`` holds the share volume when the source row carries one
    (``None`` entries otherwise - the GPW archive table has turnover in PLN
    thousands only, see ``turnover_k``). ``renamed_from`` lists the earlier
    tickers stitched into this series by ``build_panel(merge_renames=True)``
    as ``{"name", "first_date", "last_date"}``. ``gap_joins`` lists the dates
    whose previous bar is separated by sessions missing from the archive
    cache: no adjustment was inferred there (see the module docstring).
    """

    __slots__ = ("name", "dates", "close", "high", "low", "turnover_k", "adjustments", "open", "volume",
                 "renamed_from", "gap_joins")

    def __init__(self, name: str):
        self.name = name
        self.dates: List[str] = []
        self.close: List[float] = []
        self.high: List[float] = []
        self.low: List[float] = []
        self.turnover_k: List[float] = []
        self.adjustments: List[Dict] = []
        self.open: List[float] = []
        self.volume: List[Optional[float]] = []
        self.renamed_from: List[Dict] = []
        self.gap_joins: List[str] = []

    def __len__(self):
        return len(self.dates)


#: optional columns of :class:`Series` (``close`` and ``dates`` are always kept)
PANEL_FIELDS = ("open", "high", "low", "turnover_k", "volume")


def build_panel(sessions: Iterable[Dict], currency: str = "PLN", adjust: bool = True,
                merge_renames: bool = True,
                renames: Optional[Dict[str, List[Tuple[str, str]]]] = None, *,
                names: Optional[Iterable[str]] = None,
                fields: Optional[Sequence[str]] = None,
                gaps: Optional[Iterable[str]] = None,
                compact: bool = False) -> Dict[str, Series]:
    """sessions: oldest-first archive payloads (a list from
    ``gpw_archive.load_sessions`` or any iterable, e.g. the streaming
    ``gpw_archive.iter_sessions`` - each payload is read once and not kept).

    ``merge_renames`` (default True) stitches renamed tickers listed in
    :data:`RENAMES` (or ``renames``) into one series under the newest ticker
    and records the old names in ``Series.renamed_from``. The archive's
    change% on the first day of the new name is relative to the old name's
    last close, so the price adjustment runs across the join unchanged.

    Memory-efficient options (gpwlib 1.2, all default to the 1.1 behaviour):

    * ``names`` - keep only these instruments (names after rename merging);
    * ``fields`` - optional columns to fill, a subset of :data:`PANEL_FIELDS`
      (``close`` always; e.g. ``("open", "high", "low", "turnover_k")`` drops
      ``volume``). A dropped column stays an empty list;
    * ``compact`` - price and turnover columns as ``array('d')``;
    * ``gaps`` - ISO dates of sessions missing from the input, added to the
      ``missing_before`` markers set by ``gpw_archive``. No adjustment is
      inferred across a gap (recorded in ``Series.gap_joins``).
    """
    table = RENAMES if renames is None else renames
    keep = set(PANEL_FIELDS if fields is None else fields)
    unknown = keep - set(PANEL_FIELDS) - {"close"}
    if unknown:
        raise ValueError("unknown panel field(s): {}".format(", ".join(sorted(unknown))))
    want = None if names is None else set(names)
    k_open, k_high, k_low = "open" in keep, "high" in keep, "low" in keep
    k_turn, k_vol = "turnover_k" in keep, "volume" in keep
    panel: Dict[str, Series] = {}
    raw_change: Dict[str, List[float]] = {}
    origin: Dict[str, Dict[str, List[str]]] = {}
    gap_set = set(gaps or ())
    seen_dates = set()
    for sess in sessions:
        d = sess["date"]
        seen_dates.add(d)
        gap_set.update(sess.get("missing_before") or ())
        for row in sess["rows"]:
            cur = row.get("currency") or ""
            if currency and cur and cur != currency:
                continue
            close = row.get("close")
            if close is None or close <= 0:
                continue
            raw_name = row["name"]
            name = canonical_name(raw_name, d, table) if (merge_renames and table) else raw_name
            if want is not None and name not in want:
                continue
            s = panel.get(name)
            if s is None:
                s = panel[name] = Series(name)
                if compact:
                    s.close, s.high, s.low, s.open, s.turnover_k = (array("d"), array("d"), array("d"),
                                                                     array("d"), array("d"))
                raw_change[name] = []
            if s.dates and s.dates[-1] == d:
                continue  # old and new name on the same day: keep the first row
            if raw_name != name:
                origin.setdefault(name, {}).setdefault(raw_name, []).append(d)
            s.dates.append(d)
            s.close.append(close)
            if k_high:
                s.high.append(row.get("high") or close)
            if k_low:
                s.low.append(row.get("low") or close)
            if k_open:
                s.open.append(row.get("open") or close)
            if k_vol:
                s.volume.append(row.get("volume"))
            if k_turn:
                s.turnover_k.append(row.get("turnover_k") or 0.0)
            raw_change[name].append(row.get("change_pct"))
    for name, olds in origin.items():
        panel[name].renamed_from = sorted(({"name": o, "first_date": ds[0], "last_date": ds[-1]}
                                           for o, ds in olds.items()), key=lambda x: x["first_date"])
    gap_list = sorted(gap_set - seen_dates)
    if adjust:
        for name, s in panel.items():
            _adjust_series(s, raw_change[name], gap_list)
    elif gap_list:
        for s in panel.values():
            s.gap_joins = _gap_joins(s.dates, gap_list)
    raw_change.clear()
    if k_open and k_high and k_low:
        for s in panel.values():
            # an open outside the (adjusted) high/low range is an archive artefact
            lo, hi, op = s.low, s.high, s.open
            for i in range(len(op)):
                op[i] = min(max(op[i], lo[i]), hi[i])
    return panel


def _gap_joins(dates: Sequence[str], gaps: Sequence[str]) -> List[str]:
    """Dates t of ``dates`` with a gap date strictly between dates[t-1] and dates[t]."""
    out = []
    for t in range(1, len(dates)):
        k = bisect_right(gaps, dates[t - 1])
        if k < len(gaps) and gaps[k] < dates[t]:
            out.append(dates[t])
    return out


def _adjust_series(s: Series, changes: List, gaps: Optional[Sequence[str]] = None) -> None:
    """Backward-adjust ``s`` in place from the archive change% (see module
    docstring). ``gaps``: sorted ISO dates of missing sessions - no factor is
    inferred for a bar whose previous bar lies before a gap."""
    n = len(s)
    cols = [c for c in (s.close, s.high, s.low, s.open) if len(c) == n]  # filled price columns
    joins = set(_gap_joins(s.dates, gaps)) if gaps else set()
    if joins:
        s.gap_joins = sorted(joins)
    close = s.close
    for t in range(n - 1, 0, -1):
        chg = changes[t]
        prev = close[t - 1]
        if chg is None or prev <= 0 or chg <= -100:
            continue
        if joins and s.dates[t] in joins:
            continue  # previous archive session missing: change% refers to a close we do not have
        implied_prev = close[t] / (1 + chg / 100.0)
        factor = implied_prev / prev
        if abs(factor - 1) > ADJ_TOLERANCE:
            for col in cols:
                for i in range(t):
                    col[i] *= factor
            s.adjustments.append({"date": s.dates[t], "factor": round(factor, 6)})


def index_series(sessions: Iterable[Dict], index_name: str, case_sensitive: bool = False) -> Series:
    """Extract one index (e.g. 'WIG', 'WIG20') from TYPE_INDICES sessions.

    GPW changed the letter case of several index names over the years
    (``WIG-gry`` -> ``WIG-GRY`` on 2022-03-21, ``WIGdivplus`` -> ``WIGDIVPLUS``,
    ``WIG20dvp`` -> ``WIG20DVP``, ``NCFIndex`` -> ``NCFINDEX`` ...), which
    split long histories in two. The lookup is therefore case-insensitive by
    default (gpwlib 1.2) and stitches the case variants into one series: on
    each session the row spelled exactly like ``index_name`` wins, otherwise
    the first row whose name matches ignoring case. The spellings used other
    than ``index_name`` are listed in ``Series.renamed_from``.
    ``case_sensitive=True`` restores the 1.1 exact-match behaviour.
    """
    s = Series(index_name)
    want = index_name.upper()
    used: Dict[str, List[str]] = {}
    for sess in sessions:
        pick = None
        for row in sess["rows"]:
            name = row.get("name")
            if not row.get("close") or not name:
                continue
            if name == index_name:
                pick = row
                break
            if pick is None and not case_sensitive and name.upper() == want:
                pick = row
        if pick is None:
            continue
        if pick["name"] != index_name:
            used.setdefault(pick["name"], []).append(sess["date"])
        s.dates.append(sess["date"])
        s.close.append(pick["close"])
        s.high.append(pick.get("high") or pick["close"])
        s.low.append(pick.get("low") or pick["close"])
        s.open.append(pick.get("open") or pick["close"])
        s.volume.append(pick.get("volume"))
        s.turnover_k.append(pick.get("turnover_k") or 0.0)
    s.renamed_from = sorted(({"name": n, "first_date": ds[0], "last_date": ds[-1]} for n, ds in used.items()),
                            key=lambda x: x["first_date"])
    return s
