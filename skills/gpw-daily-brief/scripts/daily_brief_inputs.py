"""Input registry and report discovery for gpw-daily-brief.

Every input of the brief is another skill's JSON report (CONVENTIONS rule 11)
or a local state file (journal, watchlist). This module knows which skills
feed the brief, how to find their newest and previous report with
``gpwlib.reports`` and which command produces a missing one. It does no
analysis; ``daily_brief_engine`` does.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Dict, List, Optional

from gpwlib import reports

# Freshness limits (sessions behind the reference date).
# - Daily market inputs: 3 sessions, the cross-skill rule of docs/CONVENTIONS.md.
# - Weekly inputs (macro, portfolio risk, post-mortem, dividends, trade review):
#   5 sessions = one trading week, the cadence the roadmap sets for them.
DAILY_MAX_BEHIND = 3
WEEKLY_MAX_BEHIND = 5

# key -> spec. ``modes``: accepted values of the report's top-level "mode"
# (None = any). ``cadence``: "daily" inputs are expected in every brief,
# "weekly" ones only in ``--weekly`` (but are used when present), "optional"
# ones are never reported missing. ``cmd``: how to produce it, run from the
# named skill's directory (DIR = the shared report folder).
INPUT_SPECS: List[Dict] = [
    {"key": "regime", "skill": "gpw-market-regime", "modes": None, "pattern": "gpw_market_regime_*.json",
     "cadence": "daily", "cmd": "python3 scripts/gpw_market_regime.py --input-dir DIR --output-dir DIR"},
    {"key": "exposure", "skill": "gpw-exposure-coach", "modes": None, "pattern": "gpw_exposure_coach_*.json",
     "cadence": "daily", "cmd": "python3 scripts/gpw_exposure_coach.py --input-dir DIR --output-dir DIR"},
    {"key": "breadth", "skill": "gpw-market-breadth", "modes": None, "pattern": "gpw_breadth_*.json",
     "cadence": "daily", "cmd": "python3 scripts/gpw_breadth.py --output-dir DIR"},
    {"key": "sector", "skill": "gpw-sector-trend", "modes": None, "pattern": "gpw_sector_trend_*.json",
     "cadence": "daily", "cmd": "python3 scripts/gpw_sector_trend.py --output-dir DIR"},
    {"key": "screener", "skill": "gpw-trend-screener", "modes": ("screen",), "pattern": "gpw_trend_screener_*.json",
     "cadence": "daily", "cmd": "python3 scripts/gpw_trend_screener.py --input-dir DIR --output-dir DIR"},
    {"key": "calendar", "skill": "gpw-event-calendar", "modes": ("upcoming",), "pattern": None,
     "cadence": "daily", "cmd": "python3 scripts/gpw_event_calendar.py upcoming --sessions 10 --output-dir DIR"},
    {"key": "calendar_portfolio", "skill": "gpw-event-calendar", "modes": ("portfolio",), "pattern": None,
     "cadence": "optional", "cmd": "python3 scripts/gpw_event_calendar.py portfolio --sessions 10 --output-dir DIR"},
    {"key": "shorts", "skill": "gpw-ownership", "modes": ("shorts",), "pattern": None,
     "cadence": "daily", "cmd": "python3 scripts/gpw_ownership.py shorts --output-dir DIR"},
    {"key": "insiders", "skill": "gpw-ownership", "modes": ("insiders",), "pattern": None,
     "cadence": "daily", "cmd": "python3 scripts/gpw_ownership.py insiders --days 3 --output-dir DIR"},
    {"key": "earnings", "skill": "gpw-earnings-reaction", "modes": ("scan", "portfolio"), "pattern": None,
     "cadence": "daily", "cmd": "python3 scripts/gpw_earnings_reaction.py scan --lookback-days 30 --output-dir DIR"},
    {"key": "watchlist_report", "skill": "gpw-watchlist", "modes": None, "pattern": None,
     "cadence": "optional", "cmd": "python3 scripts/gpw_watchlist.py refresh --output-dir DIR"},
    {"key": "macro", "skill": "gpw-macro-regime", "modes": None, "pattern": None,
     "cadence": "weekly", "cmd": "python3 scripts/gpw_macro_regime.py --input-dir DIR --output-dir DIR"},
    {"key": "portfolio", "skill": "gpw-portfolio-check", "modes": None, "pattern": None,
     "cadence": "weekly", "cmd": "python3 scripts/portfolio_check.py --from-journal --output-dir DIR"},
    {"key": "risk", "skill": "gpw-risk-lab", "modes": None, "pattern": None,
     "cadence": "weekly", "cmd": "python3 scripts/gpw_risk_lab.py stress --journal --output-dir DIR"},
    {"key": "postmortem", "skill": "gpw-signal-postmortem", "modes": None, "pattern": "gpw_signal_postmortem_*.json",
     "cadence": "weekly", "cmd": "python3 scripts/gpw_signal_postmortem.py evaluate --input-dir DIR --output-dir DIR"},
    {"key": "dividend", "skill": "gpw-dividend-analyzer", "modes": ("portfolio",), "pattern": None,
     "cadence": "weekly", "cmd": "python3 scripts/gpw_dividend_analyzer.py portfolio --output-dir DIR"},
    {"key": "trade_review", "skill": "gpw-trade-review", "modes": None, "pattern": "gpw_trade_review_*.json",
     "cadence": "optional", "cmd": "python3 scripts/trade_review.py --month YYYY-MM --output-dir DIR"},
    {"key": "previous_brief", "skill": "gpw-daily-brief", "modes": ("daily", "weekly"), "pattern": None,
     "cadence": "optional", "cmd": None},
]

SPEC_BY_KEY = {s["key"]: s for s in INPUT_SPECS}

# Weekly inputs that only make sense when the journal has open positions.
NEEDS_POSITIONS = {"portfolio", "risk", "dividend"}


def _mode_ok(data: Dict, modes) -> bool:
    if modes is None:
        return True
    return data.get("mode") in modes


def _describe(rec: Dict, spec: Dict, reference: Optional[dt.date], lang: str) -> Dict:
    max_behind = WEEKLY_MAX_BEHIND if spec["cadence"] == "weekly" else DAILY_MAX_BEHIND
    out = reports.describe(Path(rec["path"]), rec["data"], spec["skill"], reference=reference,
                           max_sessions_behind=max_behind, lang=lang)
    out["mode"] = rec["data"].get("mode")
    return out


def collect(input_dir, reference: Optional[dt.date] = None, lang: str = "pl",
            exclude_paths: Optional[List[str]] = None) -> Dict[str, Dict]:
    """``{key: {"current": report|None, "previous": report|None, "spec": spec}}``.

    ``current`` = newest report of the skill (and mode) via
    ``gpwlib.reports.iter_reports``; ``previous`` = newest one whose ``as_of``
    is strictly earlier than the current one's (the "what changed" baseline).
    Reports dated after ``reference`` are ignored (no look-ahead).
    """
    exclude = {str(Path(p).resolve()) for p in (exclude_paths or [])}
    by_skill: Dict[str, List[Dict]] = {}
    out: Dict[str, Dict] = {}
    for spec in INPUT_SPECS:
        if spec["skill"] not in by_skill:
            by_skill[spec["skill"]] = [r for r in reports.iter_reports(input_dir, skill=spec["skill"],
                                                                       pattern=spec["pattern"], reference=reference,
                                                                       newest_first=True)
                                       if str(Path(r["path"]).resolve()) not in exclude]
        recs = [r for r in by_skill[spec["skill"]] if _mode_ok(r["data"], spec["modes"])]
        cur = recs[0] if recs else None
        prev = None
        if cur is not None and cur["as_of"]:
            for r in recs[1:]:
                if r["as_of"] and r["as_of"] < cur["as_of"]:
                    prev = r
                    break
        out[spec["key"]] = {
            "spec": spec,
            "current": _describe(cur, spec, reference, lang) if cur else None,
            "previous": _describe(prev, spec, reference, lang) if prev else None,
        }
    return out


def load_watchlist(path: Optional[Path]) -> Optional[Dict]:
    """Read the gpw-watchlist state file (``items[]``); None when absent.

    A corrupt file is reported as ``{"error": ...}`` instead of raising: the
    watchlist is optional and must never stop the brief.
    """
    if path is None:
        return None
    p = Path(path).expanduser()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return {"path": str(p), "items": [], "error": str(exc)}
    if isinstance(data, list):  # tolerate a bare list of items
        data = {"items": data}
    if not isinstance(data, dict):
        return {"path": str(p), "items": [], "error": "not a JSON object"}
    items = data.get("items") if isinstance(data.get("items"), list) else []
    return {"path": str(p), "items": [i for i in items if isinstance(i, dict) and i.get("ticker")],
            "updated_at": data.get("updated_at") or data.get("as_of"), "error": None}


def default_watchlist_path() -> Path:
    return Path.home() / ".gpw-trading-skills" / "watchlist.json"
