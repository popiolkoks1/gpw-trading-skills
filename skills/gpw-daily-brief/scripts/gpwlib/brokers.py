"""Broker commission presets for Polish brokerage accounts (shares and ETFs on GPW).

One table shared by every skill that prices an order (position sizer, ETF
planner, portfolio check, pre-trade gate), so a tariff change is made once.

The presets are approximations of the public tariffs ("tabela opłat i
prowizji") for GPW shares/ETFs on a standard internet account. They were
entered on ``TARIFF_ENTERED`` and are NOT re-checked automatically - every
report that uses them says so, and adds a stronger warning once the table is
older than ``TARIFF_MAX_AGE_DAYS``. Users can always override with
``--commission-pct`` / ``--commission-min``.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, Optional

TARIFF_ENTERED = dt.date(2026, 11, 15)
TARIFF_MAX_AGE_DAYS = 180

BROKERS: Dict[str, Dict] = {
    "xtb": {
        "label": "XTB",
        "pct": 0.0,
        "min_fee": 0.0,
        "note": "0% up to 100k EUR monthly turnover, then 0.2% (min 10 EUR) - modelled as 0%",
        "warning_key": "warn_xtb",
    },
    "mbank": {"label": "mBank eMakler", "pct": 0.39, "min_fee": 5.0, "note": ""},
    "bossa": {"label": "DM BOŚ (bossa)", "pct": 0.38, "min_fee": 5.0, "note": ""},
    "pkobp": {"label": "BM PKO BP", "pct": 0.39, "min_fee": 5.0, "note": ""},
    "santander": {"label": "Santander BM", "pct": 0.39, "min_fee": 5.0, "note": ""},
    "custom": {"label": "custom", "pct": 0.0, "min_fee": 0.0, "note": ""},
}
VERIFY_NOTE = "verify against your broker's current tariff (tabela opłat i prowizji)"

TARIFF_NOTE = {
    "pl": "Stawki prowizji wpisano {d} i nie są aktualizowane automatycznie – sprawdź aktualną tabelę opłat swojego brokera.",
    "en": "Commission rates were entered on {d} and are not updated automatically – check your broker's current tariff.",
}
TARIFF_STALE = {
    "pl": "⚠️ Tabela prowizji ma już {n} dni (wpisana {d}). Stawki mogły się zmienić – podaj własne przez --commission-pct / --commission-min.",
    "en": "⚠️ The commission table is {n} days old (entered {d}). Rates may have changed – pass your own via --commission-pct / --commission-min.",
}


def resolve(name: Optional[str], pct: Optional[float] = None, min_fee: Optional[float] = None) -> Dict:
    """Return a broker preset (copy), with optional user overrides applied."""
    key = name or "custom"
    if key not in BROKERS:
        raise ValueError("unknown broker {!r}; choose one of {}".format(key, ", ".join(sorted(BROKERS))))
    b = dict(BROKERS[key], key=key)
    if pct is not None:
        b["pct"] = float(pct)
    if min_fee is not None:
        b["min_fee"] = float(min_fee)
    b["overridden"] = pct is not None or min_fee is not None
    return b


def commission(value: float, pct: float, min_fee: float) -> float:
    """Commission for one order of ``value`` PLN (0 for an empty order)."""
    if value <= 0:
        return 0.0
    fee = value * pct / 100.0
    if pct > 0 or min_fee > 0:
        fee = max(fee, min_fee)
    return round(fee, 2)


def tariff_age_days(today: Optional[dt.date] = None) -> int:
    return ((today or dt.date.today()) - TARIFF_ENTERED).days


def tariff_notes(lang: str = "pl", overridden: bool = False, today: Optional[dt.date] = None) -> list:
    """Human-readable notes about the tariff's age (empty if the user overrode it)."""
    if overridden:
        return []
    d = TARIFF_ENTERED.isoformat()
    age = tariff_age_days(today)
    if age > TARIFF_MAX_AGE_DAYS:
        return [TARIFF_STALE[lang].format(n=age, d=d)]
    return [TARIFF_NOTE[lang].format(d=d)]
