"""Data-provenance block attached to every JSON report.

Borrowed from the upstream claude-trading-skills provenance contract and
Anthropic's financial-services skills: every number must be traceable to a
source and a business date, and staleness must be explicit, never silent.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional

from . import plcal

SCHEMA_VERSION = "1.0"


def sessions_behind(as_of: dt.date, today: Optional[dt.date] = None) -> int:
    """Number of probable GPW sessions between as_of (exclusive) and today
    (inclusive only if today's session is already final)."""
    today = today or plcal.today_warsaw()
    n = 0
    day = as_of + dt.timedelta(days=1)
    while day <= today:
        if plcal.is_probable_session(day) and (day < today or plcal.session_is_final(day)):
            n += 1
        day += dt.timedelta(days=1)
    return n


def freshness(as_of: dt.date, max_sessions_behind: int = 1) -> Dict:
    behind = sessions_behind(as_of)
    return {
        "as_of": as_of.isoformat(),
        "sessions_behind": behind,
        "is_stale": behind > max_sessions_behind,
    }


def build(
    *,
    as_of: dt.date,
    sources: List[str],
    sessions_used: int,
    universe_size: Optional[int] = None,
    notes: Optional[List[str]] = None,
    corporate_actions_adjusted: bool = True,
    survivorship_free: bool = True,
    point_in_time_membership: bool = False,
) -> Dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of.isoformat(),
        "retrieved_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "sources": sources,
        "sessions_used": sessions_used,
        "universe_size": universe_size,
        "freshness": freshness(as_of),
        "corporate_actions_adjusted": corporate_actions_adjusted,
        "survivorship_free": survivorship_free,
        "point_in_time_membership": point_in_time_membership,
        "notes": notes or [],
    }
