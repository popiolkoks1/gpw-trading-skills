"""Polish exchange calendar helpers.

GPW is closed on weekends, Polish public holidays and a few extra exchange
holidays (Good Friday, 24 and 31 December). The holiday list is only used to
avoid useless HTTP requests: the authoritative signal that there was no
session is an empty quotes table in the GPW archive (see gpw_archive.py), so
an outdated list here can cost a request but never corrupts data.
"""

from __future__ import annotations

import datetime as dt

try:  # Python 3.9+
    from zoneinfo import ZoneInfo

    WARSAW = ZoneInfo("Europe/Warsaw")
except Exception:  # pragma: no cover - zoneinfo missing (very old Python / no tzdata)
    WARSAW = None


def easter_sunday(year: int) -> dt.date:
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


def exchange_holidays(year: int) -> set:
    """Days on which GPW is expected to be closed (Mon-Fri only matter)."""
    easter = easter_sunday(year)
    fixed = [
        (1, 1),    # Nowy Rok
        (1, 6),    # Trzech Króli
        (5, 1),    # Święto Pracy
        (5, 3),    # Święto Konstytucji 3 Maja
        (8, 15),   # Wniebowzięcie NMP
        (11, 1),   # Wszystkich Świętych
        (11, 11),  # Święto Niepodległości
        (12, 24),  # Wigilia (exchange holiday; public holiday since 2025)
        (12, 25),
        (12, 26),
        (12, 31),  # exchange holiday
    ]
    days = {dt.date(year, m, d) for m, d in fixed}
    days.add(easter - dt.timedelta(days=2))   # Wielki Piątek (exchange holiday)
    days.add(easter + dt.timedelta(days=1))   # Poniedziałek Wielkanocny
    days.add(easter + dt.timedelta(days=60))  # Boże Ciało
    return days


def is_probable_session(day: dt.date) -> bool:
    if day.weekday() >= 5:
        return False
    return day not in exchange_holidays(day.year)


def now_warsaw() -> dt.datetime:
    if WARSAW is None:
        return dt.datetime.utcnow() + dt.timedelta(hours=1)
    return dt.datetime.now(WARSAW)


def today_warsaw() -> dt.date:
    return now_warsaw().date()


def session_is_final(day: dt.date, final_hour: int = 18) -> bool:
    """True when the closing data for ``day`` should be complete.

    Continuous trading ends 16:50, closing auction 17:00, trading-at-last
    17:05 (WATS rules, Oct 2026). We treat data as final from 18:00 Warsaw
    time to leave margin for publication.
    """
    now = now_warsaw()
    if day < now.date():
        return True
    if day > now.date():
        return False
    return now.hour >= final_hour


def previous_probable_sessions(end: dt.date, max_calendar_days: int):
    """Yield candidate session dates from ``end`` backwards."""
    day = end
    for _ in range(max_calendar_days):
        if is_probable_session(day):
            yield day
        day -= dt.timedelta(days=1)
