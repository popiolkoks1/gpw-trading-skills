"""Data access of gpw-dividend-analyzer (archive cache, ESPI, gpw.pl card,
optional biznesradar) and the embedded reference tables.

Every network call goes through ``gpwlib`` (identifying User-Agent, >= 1 s per
host, on-disk cache). The price archive is read from the local cache only;
just the newest few sessions are topped up online, so a run never downloads
years of history (``ARCHIVE_TOPUP_SESSIONS``).
"""

from __future__ import annotations

import datetime as dt
import html as _html
import re
from pathlib import Path
from typing import Dict, List, Optional

from gpwlib import espi, fundamentals, gpw_archive, http, panel

import dividend_engine as E

D = dt.date

# The archive cache can hold sessions back to 2019 (fetched by other skills);
# 2 600 sessions (~10 years) is the most we ever read. Only this many newest
# sessions may be fetched online when missing (today's close, a weekend gap).
ARCHIVE_MAX_SESSIONS = 2600
ARCHIVE_TOPUP_SESSIONS = 5


def load_archive(as_of: D, cache_dir: Optional[Path], offline: bool) -> Dict:
    """Cached share sessions up to ``as_of`` (oldest first) + small online top-up."""
    sessions = gpw_archive.load_sessions(ARCHIVE_MAX_SESSIONS, end=as_of, cache_dir=cache_dir, offline=True,
                                         progress=False)
    topped = 0
    if not offline:
        try:
            recent = gpw_archive.load_sessions(ARCHIVE_TOPUP_SESSIONS, end=as_of, cache_dir=cache_dir,
                                               offline=False, progress=False)
        except gpw_archive.GPWFetchError:
            recent = []
        have = {s["date"] for s in sessions}
        for s in recent:
            if s["date"] not in have:
                sessions.append(s)
                topped += 1
        sessions.sort(key=lambda s: s["date"])
    return {"sessions": sessions, "topped_up": topped}


def index_returns(as_of: D, cache_dir: Optional[Path], name: str = "WIG") -> Dict[str, float]:
    """Daily returns of an index from the cached index archive (offline only)."""
    try:
        sess = gpw_archive.load_sessions(ARCHIVE_MAX_SESSIONS, end=as_of, type_id=gpw_archive.TYPE_INDICES,
                                         cache_dir=cache_dir, offline=True, progress=False)
    except Exception:
        return {}
    s = panel.index_series(sess, name)
    out = {}
    for i in range(1, len(s.dates)):
        if s.close[i - 1] > 0:
            out[s.dates[i]] = s.close[i] / s.close[i - 1] - 1
    return out


def scan_espi(start: D, end: D, cache_dir: Optional[Path], offline: bool) -> Dict:
    return espi.fetch_range(start, end, cache_dir=cache_dir, offline=offline)


def fetch_report(geru_id: int, cache_dir: Optional[Path], offline: bool) -> Optional[Dict]:
    try:
        return espi.fetch_report(geru_id, cache_dir=cache_dir, offline=offline)
    except http.HTTPFetchError:
        return None


def snapshot(ticker: str, isin: Optional[str], cache_dir: Optional[Path], offline: bool) -> Dict:
    """gpw.pl indicators + Notoria; ``{"error": str}`` instead of raising."""
    try:
        return fundamentals.snapshot(ticker, isin, cache_dir=cache_dir, offline=offline)
    except (http.HTTPFetchError, fundamentals.FundamentalsError, gpw_archive.GPWFetchError, KeyError,
            ValueError) as exc:
        return {"error": str(exc) or exc.__class__.__name__}


def profit_history(ticker: str) -> Optional[Dict]:
    try:
        return fundamentals.load_history(ticker)
    except fundamentals.FundamentalsError:
        return None


# --------------------------------------------------------------------------
# biznesradar.pl (optional, personal local use only)
# --------------------------------------------------------------------------
# Company page https://www.biznesradar.pl/dywidenda/<CODE> (robots.txt allows
# it; checked 2026-11-16). One table: "wypłata za rok", "zaliczka", DPS,
# total (tys. zł), "z kapitału zapasowego", yield (premium-blurred), status,
# WZA date, "ostatnie notowanie z prawem do dywidendy", payment date. Data
# stays in the user's cache; it is never redistributed.
BIZNESRADAR_COMPANY_URL = "https://www.biznesradar.pl/dywidenda/{}"
BIZNESRADAR_TTL_S = 7 * 86400

_RX_TABLE = re.compile(r"<table[^>]*>(.*?)</table>", re.S | re.I)
_RX_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_RX_CELL = re.compile(r"<t([dh])[^>]*>(.*?)</t[dh]>", re.S | re.I)


def _cell_text(fragment: str) -> str:
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse_biznesradar_company(html: str) -> List[Dict]:
    """Rows of the dividend-history table -> ``{fiscal_year, dps, total_k, from_reserve_k,
    status, agm_date, last_cum_date, ex_date, payment_date}`` (newest first)."""
    for tb in _RX_TABLE.findall(html or ""):
        rows = [[(k, _cell_text(c)) for k, c in _RX_CELL.findall(tr)] for tr in _RX_TR.findall(tb)]
        head = next((r for r in rows if r and all(k == "h" for k, _ in r)), None)
        if not head:
            continue
        labels = [t.lower() for _, t in head]
        if not any("wypłata za rok" in l for l in labels) or not any("na akcję" in l for l in labels):
            continue
        col = {
            "year": next(i for i, l in enumerate(labels) if "wypłata za rok" in l),
            "dps": next(i for i, l in enumerate(labels) if "na akcję" in l),
        }
        for key, needle in (("total", "wartość dywidendy"), ("reserve", "kapitału zapasowego"),
                            ("status", "status"), ("agm", "wza"), ("last_cum", "ostatnie notowanie"),
                            ("pay", "dzień wypłaty")):
            col[key] = next((i for i, l in enumerate(labels) if needle in l), None)
        out = []
        for r in rows:
            if not r or r is head or any(k == "h" for k, _ in r):
                continue
            cells = [t for _, t in r]
            if len(cells) < len(labels):
                continue

            def g(key):
                i = col.get(key)
                return cells[i] if i is not None and i < len(cells) else ""

            def num(s):
                s = s.replace(" ", "").replace(",", ".")
                try:
                    return float(s)
                except ValueError:
                    return None

            def day(s):
                m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
                return m.group(0) if m else None
            if not re.fullmatch(r"\d{4}", g("year")):
                continue
            lc = day(g("last_cum"))
            ex = E.exdiv_from_last_cum(D.fromisoformat(lc))["ex_date"] if lc else None
            out.append({"fiscal_year": int(g("year")), "dps": num(g("dps")), "total_k": num(g("total")),
                        "from_reserve_k": num(g("reserve")), "status": g("status").lower() or None,
                        "agm_date": day(g("agm")), "last_cum_date": lc, "ex_date": ex,
                        "payment_date": day(g("pay")), "source": "biznesradar"})
        return out
    return []


def fetch_biznesradar(code: str, cache_dir: Optional[Path], offline: bool) -> Dict:
    url = BIZNESRADAR_COMPANY_URL.format(code)
    resp = http.get(url, cache_dir=cache_dir, ttl_seconds=BIZNESRADAR_TTL_S, offline=offline)
    return {"rows": parse_biznesradar_company(resp.text), "url": resp.url, "retrieved_at": resp.retrieved_at}


# --------------------------------------------------------------------------
# Reference tables
# --------------------------------------------------------------------------
# Companies where the State Treasury (Skarb Państwa) holds a controlling or
# blocking stake directly, or controls through a state-owned company. The
# dividend is then also a budget decision: the Ministry of State Assets
# (MAP) votes at the AGM, so payouts can change with fiscal needs or policy
# (e.g. investment programmes, windfall taxes). Stakes are approximate
# shares of capital as published on each company's investor-relations page
# ("Akcjonariat"/"Struktura akcjonariatu") and in its annual report;
# re-check them before relying on the number - they change.
STATE_HOLDINGS = {
    "PKOBP": {"holder": "Skarb Państwa", "stake_pct": 29.43, "kind": "direct",
              "source": "https://www.pkobp.pl/relacje-inwestorskie/akcje-i-dywidendy/akcjonariat/"},
    "PZU": {"holder": "Skarb Państwa", "stake_pct": 34.19, "kind": "direct",
            "source": "https://www.pzu.pl/relacje-inwestorskie/akcje-i-obligacje/akcjonariat"},
    "PKNORLEN": {"holder": "Skarb Państwa", "stake_pct": 49.90, "kind": "direct",
                 "source": "https://www.orlen.pl/pl/relacje-inwestorskie/akcje-i-obligacje/akcjonariat"},
    "PGE": {"holder": "Skarb Państwa", "stake_pct": 60.86, "kind": "direct",
            "source": "https://www.gkpge.pl/relacje-inwestorskie/akcje/akcjonariat"},
    "KGHM": {"holder": "Skarb Państwa", "stake_pct": 31.79, "kind": "direct",
             "source": "https://kghm.com/pl/inwestorzy/akcje/akcjonariat"},
    "TAURONPE": {"holder": "Skarb Państwa", "stake_pct": 30.06, "kind": "direct",
                 "source": "https://www.tauron.pl/tauron/relacje-inwestorskie/akcje/akcjonariat"},
    "ENEA": {"holder": "Skarb Państwa", "stake_pct": 52.29, "kind": "direct",
             "source": "https://ir.enea.pl/akcje/akcjonariat"},
    "JSW": {"holder": "Skarb Państwa", "stake_pct": 55.16, "kind": "direct",
            "source": "https://www.jsw.pl/relacje-inwestorskie/akcje/akcjonariat"},
    "GPW": {"holder": "Skarb Państwa", "stake_pct": 35.00, "kind": "direct (51.76% of votes)",
            "source": "https://www.gpw.pl/relacje-inwestorskie (Akcjonariat)"},
    "GRUPAAZOTY": {"holder": "Skarb Państwa", "stake_pct": 33.00, "kind": "direct",
                   "source": "https://grupaazoty.com/relacje-inwestorskie/akcje/akcjonariat"},
    "ENERGA": {"holder": "ORLEN S.A. (state-controlled)", "stake_pct": 90.92, "kind": "indirect",
               "source": "https://ir.energa.pl/akcje/akcjonariat"},
    "PEKAO": {"holder": "PZU S.A. + PFR S.A. (state-controlled)", "stake_pct": 32.8, "kind": "indirect",
              "source": "https://www.pekao.com.pl/relacje-inwestorskie/akcje/akcjonariat.html"},
    "ALIOR": {"holder": "PZU S.A. (state-controlled)", "stake_pct": 31.9, "kind": "indirect",
              "source": "https://www.aliorbank.pl/relacje-inwestorskie/akcje/akcjonariat.html"},
    "PKPCARGO": {"holder": "PKP S.A. (state-owned)", "stake_pct": 33.01, "kind": "indirect",
                 "source": "https://ir.pkp-cargo.pl/akcje/akcjonariat"},
}
STATE_HOLDINGS_AS_OF = "2026-11 (company IR pages as known at build time; verify)"

# Insurers: free cash flow is not meaningful (investment flows are the
# business), like banks.
INSURERS = {"PZU", "ERGOHESTIA", "UNIQA"}

# KNF stance on bank dividends (yearly, issued in December for the next
# AGM season). Text in references/methodology.md.
KNF_STANCE = {
    "year": 2026,
    "summary_pl": ("KNF (stanowisko ws. polityki dywidendowej w 2026 r.): banki komercyjne mogą wypłacić do 50% "
                   "zysku za 2025 r. przy spełnieniu kryteriów podstawowych (m.in. wymogi kapitałowe, ocena BION), "
                   "do 75% przy dodatkowo NPL <= 5%; kryteria muszą być spełnione na koniec 2025 r. i w dniu "
                   "uchwały; nowe kryterium wrażliwości na zmiany stóp procentowych. W 2020 r. KNF zaleciła "
                   "zatrzymanie całego zysku (COVID-19)."),
    "summary_en": ("KNF stance on dividend policy in 2026: commercial banks may pay up to 50% of 2025 profit if they "
                   "meet the basic criteria (capital requirements, SREP/BION score), up to 75% if NPL <= 5% as "
                   "well; criteria must hold at end-2025 and on the resolution date; new interest-rate "
                   "sensitivity criterion. In 2020 KNF asked banks to retain all profit (COVID-19)."),
    "source": "https://www.knf.gov.pl/knf/pl/komponenty/img/Stanowisko_KNF_ws_polityki_dywidendowej_w_2026_roku_96591.pdf",
}
