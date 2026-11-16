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
    """Number of probable GPW sessions after ``as_of`` up to the reference day.

    ``today`` is the reference day (default: today in Warsaw). Every probable
    session in ``(as_of, reference)`` counts. The reference day itself counts
    when it is a probable session and

    * it is in the past (its session is over), or
    * it is in the future - an explicit future reference (``--as-of`` next
      Friday, a planned holding window) asks "how many sessions will have
      passed by then", so that day's session counts too (gpwlib 1.2 fix:
      it used to be skipped because its session "is not final yet"), or
    * it is today in Warsaw and today's session is already final (18:00).

    So only *today's* unfinished session is ever left out.
    """
    now = plcal.today_warsaw()
    ref = today or now
    n = 0
    day = as_of + dt.timedelta(days=1)
    while day <= ref:
        if plcal.is_probable_session(day) and (day < ref or day != now or plcal.session_is_final(day)):
            n += 1
        day += dt.timedelta(days=1)
    return n


def data_gaps_note(gaps: Optional[List[str]]) -> Optional[str]:
    """Provenance note for probable sessions missing from the archive cache
    (see ``gpw_archive.session_gaps``), or None when there are none."""
    gaps = sorted(set(gaps or []))
    if not gaps:
        return None
    shown = ", ".join(gaps[:5]) + (", ..." if len(gaps) > 5 else "")
    return ("{} probable session(s) missing from the GPW archive cache ({}); price adjustments are not "
            "inferred across them and returns spanning them include several sessions.").format(len(gaps), shown)


def freshness(as_of: dt.date, max_sessions_behind: int = 1, reference: Optional[dt.date] = None) -> Dict:
    """Freshness of data that ends on ``as_of``.

    ``reference`` is the date the caller asked about. ``None`` (normal run)
    means today in Warsaw. A deliberate historical run (``--as-of D``) passes
    ``reference=D``: the data is then judged against the requested date, so a
    run for last March is not "stale" just because March is over. The
    distance to today is still recorded (``sessions_behind_today``) so a
    consumer can see that the report describes the past.
    """
    today = plcal.today_warsaw()
    behind_today = sessions_behind(as_of, today)
    if reference is None or reference >= today:
        behind, ref = behind_today, None
    else:
        behind, ref = sessions_behind(as_of, reference), reference
    return {
        "as_of": as_of.isoformat(),
        "sessions_behind": behind,
        "is_stale": behind > max_sessions_behind,
        "reference_date": ref.isoformat() if ref else None,
        "historical_run": ref is not None,
        "sessions_behind_today": behind_today,
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
    reference_date: Optional[dt.date] = None,
    max_sessions_behind: int = 1,
    data_gaps: Optional[List[str]] = None,
) -> Dict:
    """Provenance block. Pass ``reference_date`` = the requested ``--as-of``
    date for historical runs (see :func:`freshness`). ``data_gaps`` = ISO dates
    of probable sessions missing from the archive (``gpw_archive.session_gaps``);
    when given, the block gets a ``data_gaps`` list and a note (the key is
    absent otherwise, so older consumers see the 1.1 shape)."""
    fr = freshness(as_of, max_sessions_behind, reference=reference_date)
    notes = list(notes or [])
    if fr["historical_run"]:
        notes.append("Historical run (--as-of {}); freshness is measured against that date.".format(
            fr["reference_date"]))
    gap_note = data_gaps_note(data_gaps)
    if gap_note:
        notes.append(gap_note)
    out = {
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of.isoformat(),
        "retrieved_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "sources": sources,
        "sessions_used": sessions_used,
        "universe_size": universe_size,
        "freshness": fr,
        "corporate_actions_adjusted": corporate_actions_adjusted,
        "survivorship_free": survivorship_free,
        "point_in_time_membership": point_in_time_membership,
        "notes": notes,
    }
    if data_gaps:
        out["data_gaps"] = sorted(set(data_gaps))
    return out
