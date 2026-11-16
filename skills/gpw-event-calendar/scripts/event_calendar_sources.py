"""Data sources of gpw-event-calendar: embedded official schedules and fetchers.

Embedded schedules carry their source URL and an as-of date. They are
checked by :func:`schedule_gaps`, which tells the caller when a requested
period has no embedded schedule (the report then warns instead of
inventing dates).

Fetchers go through ``gpwlib.http`` / ``gpwlib.espi`` (identifying
User-Agent, >= 1 s per host, on-disk cache). Nothing here bypasses bot
protection: nbp.pl (Incapsula) and stooq (robots) are not used.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Dict, List, Optional

from gpwlib import espi, http

import event_calendar_engine as E

D = dt.date

# --------------------------------------------------------------------------
# NBP Monetary Policy Council (RPP) - 2026 schedule
# --------------------------------------------------------------------------
# Published by NBP ("Harmonogram posiedzeń Rady Polityki Pieniężnej i
# publikacji opisów dyskusji z posiedzeń decyzyjnych w 2026 r."). nbp.pl is
# behind a bot check for scripts, so the table is embedded. Transcribed from
# the NBP table as reproduced by infor.pl (Dec 2025); January-June dates
# cross-checked against rankomat.pl. 11 two-day decision meetings + one
# one-day non-decision meeting (25 August). The decision is announced after
# the second day.
MPC_SOURCE_URL = "https://nbp.pl/ (Harmonogram posiedzeń RPP w 2026 r.)"
MPC_RELAY_URL = ("https://ksiegowosc.infor.pl/obrot-gospodarczy/finanse-i-inwestycje/7451398,"
                 "harmonogram-posiedzen-rady-polityki-pienieznej-w-2026-roku.html")
MPC_AS_OF = "2026-11-16"
MPC_SCHEDULE: Dict[int, List[Dict]] = {
    2026: [
        {"days": ["2026-01-13", "2026-01-14"], "decision": True, "minutes": "2026-01-16"},
        {"days": ["2026-02-03", "2026-02-04"], "decision": True, "minutes": "2026-02-06"},
        {"days": ["2026-03-03", "2026-03-04"], "decision": True, "minutes": "2026-03-06"},
        {"days": ["2026-04-08", "2026-04-09"], "decision": True, "minutes": "2026-04-13"},
        {"days": ["2026-05-05", "2026-05-06"], "decision": True, "minutes": "2026-05-08"},
        {"days": ["2026-06-09", "2026-06-10"], "decision": True, "minutes": "2026-06-12"},
        {"days": ["2026-07-07", "2026-07-08"], "decision": True, "minutes": "2026-07-10"},
        {"days": ["2026-08-25"], "decision": False, "minutes": "2026-08-27"},
        {"days": ["2026-09-01", "2026-09-02"], "decision": True, "minutes": None},
        {"days": ["2026-10-06", "2026-10-07"], "decision": True, "minutes": "2026-10-09"},
        {"days": ["2026-11-03", "2026-11-04"], "decision": True, "minutes": "2026-11-06"},
        {"days": ["2026-12-01", "2026-12-02"], "decision": True, "minutes": "2026-12-04"},
    ],
}

# --------------------------------------------------------------------------
# MSCI quarterly index reviews (Poland: MSCI Emerging Markets)
# --------------------------------------------------------------------------
# Nov 2026: MSCI press release of 4.11.2026 ("announcement ... November 11,
# 2026 ... changes made as of the close of November 30, 2026"). 2027 Feb-Aug:
# dates of MSCI's schedule announcement of Aug 2026 as reported by the press
# (msci.com page not machine-readable) -> marked "estimated". Other reviews
# follow the rule: effective as of the close of the last business day of
# Feb/May/Aug/Nov, announced ~13 business days earlier (median of the four
# known pairs: 13, 13, 14, 13).
MSCI_REVIEWS = [
    {"review": "2026-11", "announcement": "2026-11-11", "effective_close": "2026-11-30", "kind": "confirmed",
     "source": "https://www.msci.com/downloads/documents/press-releases/media-room/MSCI_Nov_2026_IR_Pre-Ann_PR_Doc.pdf"},
    {"review": "2027-02", "announcement": "2027-02-09", "effective_close": "2027-02-26", "kind": "estimated",
     "source": "MSCI schedule (Aug 2026) as reported by market.bisnis.com, 13.08.2026"},
    {"review": "2027-05", "announcement": "2027-05-10", "effective_close": "2027-05-28", "kind": "estimated",
     "source": "MSCI schedule (Aug 2026) as reported by market.bisnis.com, 13.08.2026"},
    {"review": "2027-08", "announcement": "2027-08-12", "effective_close": "2027-08-31", "kind": "estimated",
     "source": "MSCI schedule (Aug 2026) as reported by market.bisnis.com, 13.08.2026"},
]
MSCI_ANNOUNCE_LEAD_BDAYS = 13


def _bdays_before(day: D, n: int) -> D:
    cur = day
    while n:
        cur -= dt.timedelta(days=1)
        if cur.weekday() < 5:
            n -= 1
    return cur


def msci_reviews(start: D, end: D) -> List[Dict]:
    known = {r["review"]: r for r in MSCI_REVIEWS}
    out = []
    for y in range(start.year, end.year + 1):
        for m in (2, 5, 8, 11):
            key = "{}-{:02d}".format(y, m)
            if key in known:
                r = dict(known[key])
            else:
                eff = E.last_business_day(y, m)
                r = {"review": key, "effective_close": eff.isoformat(),
                     "announcement": _bdays_before(eff, MSCI_ANNOUNCE_LEAD_BDAYS).isoformat(),
                     "kind": "estimated", "source": "rule: last business day of Feb/May/Aug/Nov (MSCI index "
                                                    "review calendar), announcement ~13 business days earlier"}
            out.append(r)
    return out


# --------------------------------------------------------------------------
# GUS CPI release rule (estimated)
# --------------------------------------------------------------------------
# Flash CPI ("Szybki szacunek wskaźnika cen towarów i usług konsumpcyjnych")
# on the last working day of the reference month: verified in the GUS
# calendar for Sep 2026 (30.09) and Oct 2026 (30.10). Final CPI ("Wskaźniki
# cen towarów i usług konsumpcyjnych") mid-month: 15.09, 14.10, 13.11.2026 -
# modelled as the 15th moved back to a working day, +-2 days. GUS lists a
# day's publications only once they are out, so future dates are estimates.
GUS_CAL_URL = "https://stat.gov.pl/kalendarium/"


def cpi_releases(start: D, end: D) -> List[Dict]:
    out = []
    y, m = start.year, start.month - 1
    if m == 0:
        y, m = y - 1, 12
    while D(y, m, 1) <= end:
        flash = E.last_working_day_of_month(y, m)
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        final = D(ny, nm, 15)
        while not E.is_working_day(final):
            final -= dt.timedelta(days=1)
        out.append({"ref_month": "{}-{:02d}".format(y, m), "flash": flash.isoformat(), "final": final.isoformat()})
        y, m = ny, nm
    return out


# --------------------------------------------------------------------------
# Derivatives expiry (GPW standards)
# --------------------------------------------------------------------------
FW20_STANDARD_URL = "https://www.gpw.pl/pub/GPW/files/PDF/standardy_pl/WATS/Standard_kontrakty_WIG20_WATS_06.2026.pdf"


def _session_on_or_before(day: D) -> D:
    while not E.is_session(day):
        day -= dt.timedelta(days=1)
    return day


def derivative_expiries(start: D, end: D) -> List[Dict]:
    """FW20/FW40 quarterly expiry (confirmed by rule) and monthly WIG20 option
    series (third Friday of the month - estimated: the option standard was
    not re-checked for 2026)."""
    out = []
    y, m = start.year, start.month
    while D(y, m, 1) <= end:
        day = _session_on_or_before(E.third_friday(y, m))
        if start <= day <= end:
            out.append({"date": day.isoformat(), "quarterly": m in (3, 6, 9, 12), "month": "{}-{:02d}".format(y, m)})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def schedule_gaps(start: D, end: D) -> List[int]:
    """Years in [start, end] without an embedded MPC schedule."""
    return [y for y in range(start.year, end.year + 1) if y not in MPC_SCHEDULE]


# --------------------------------------------------------------------------
# Fetchers
# --------------------------------------------------------------------------
INDEX_CAL_URL = "https://gpwbenchmark.pl/kalendarium-indeksow"
INDEX_CAL_TTL_S = 86400          # announcements are added during the year
BIZNESRADAR_URL = "https://www.biznesradar.pl/dywidendy/"
BIZNESRADAR_TTL_S = 12 * 3600


def fetch_index_calendar(year: int, cache_dir: Optional[Path] = None, offline: bool = False) -> Dict:
    resp = http.get(INDEX_CAL_URL, {"year": year}, cache_dir=cache_dir, ttl_seconds=INDEX_CAL_TTL_S,
                    offline=offline)
    items = E.parse_index_calendar_html(resp.text)
    items = [i for i in items if i["date"].startswith(str(year))]
    return {"year": year, "items": items, "url": resp.url, "retrieved_at": resp.retrieved_at,
            "from_cache": resp.from_cache}


def fetch_biznesradar(cache_dir: Optional[Path] = None, offline: bool = False) -> Dict:
    resp = http.get(BIZNESRADAR_URL, cache_dir=cache_dir, ttl_seconds=BIZNESRADAR_TTL_S, offline=offline)
    return {"rows": E.parse_biznesradar_dividends(resp.text), "url": resp.url, "retrieved_at": resp.retrieved_at}


def scan_espi(start: D, end: D, cache_dir: Optional[Path] = None, offline: bool = False) -> Dict:
    return espi.fetch_range(start, end, cache_dir=cache_dir, offline=offline)


def fetch_report(geru_id: int, cache_dir: Optional[Path] = None, offline: bool = False) -> Optional[Dict]:
    try:
        return espi.fetch_report(geru_id, cache_dir=cache_dir, offline=offline)
    except http.HTTPFetchError:
        return None
