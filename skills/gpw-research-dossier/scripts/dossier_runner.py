"""Commands that fill dossier gaps, and an optional subprocess runner.

Skills talk through JSON files (CONVENTIONS rule 11), never through imports,
so a missing section is filled by running the sibling skill's own CLI, which
writes its report into the dossier's ``--input-dir``. Sibling skills are found
relative to this file (``<skills-root>/<skill>/scripts/<cli>.py``): that works
both in the repository (``skills/``) and in an install (``~/.claude/skills``).

``command_for`` returns the exact argv; ``display`` renders it as a shell line
for ``missing[]``. ``run_commands`` executes them one by one (never in
parallel: the producers share the GPW cache and rate limits), each with a
timeout, and never raises - a failed or timed-out skill is recorded and the
dossier is built from whatever exists.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

SKILLS_ROOT = Path(__file__).resolve().parent.parent.parent

# skill -> (script, positional args template, flags). "{T}" = ticker, "{OUT}" =
# input dir. "offline"/"as_of" say whether the CLI accepts --offline / --as-of
# (gpw-exposure-coach has no --offline: it reads JSON files only).
CLI: Dict[str, Dict] = {
    "gpw-market-breadth": {"script": "scripts/gpw_breadth.py", "args": ["--output-dir", "{OUT}"],
                           "offline": True, "as_of": True},
    "gpw-sector-trend": {"script": "scripts/gpw_sector_trend.py", "args": ["--output-dir", "{OUT}"],
                         "offline": True, "as_of": True},
    "gpw-market-regime": {"script": "scripts/gpw_market_regime.py",
                          "args": ["--input-dir", "{OUT}", "--output-dir", "{OUT}"], "offline": True, "as_of": True},
    "gpw-exposure-coach": {"script": "scripts/gpw_exposure_coach.py",
                           "args": ["--input-dir", "{OUT}", "--output-dir", "{OUT}"], "offline": False, "as_of": True},
    "gpw-macro-regime": {"script": "scripts/gpw_macro_regime.py", "args": ["--output-dir", "{OUT}"],
                         "offline": True, "as_of": True},
    "gpw-trend-screener": {"script": "scripts/gpw_trend_screener.py",
                           "args": ["--ticker", "{T}", "--input-dir", "{OUT}", "--output-dir", "{OUT}"],
                           "offline": True, "as_of": True},
    "gpw-quality-screener": {"script": "scripts/gpw_quality_screener.py",
                             "args": ["ticker", "{T}", "--output-dir", "{OUT}"], "offline": True, "as_of": True},
    "gpw-valuation": {"script": "scripts/gpw_valuation.py",
                      "args": ["{T}", "--input-dir", "{OUT}", "--output-dir", "{OUT}"], "offline": True, "as_of": True},
    "gpw-dividend-analyzer": {"script": "scripts/gpw_dividend_analyzer.py",
                              "args": ["ticker", "{T}", "--output-dir", "{OUT}"], "offline": True, "as_of": True},
    "gpw-ownership": {"script": "scripts/gpw_ownership.py", "args": ["ticker", "{T}", "--output-dir", "{OUT}"],
                      "offline": True, "as_of": True},
    "gpw-event-calendar": {"script": "scripts/gpw_event_calendar.py",
                           "args": ["ticker", "{T}", "--output-dir", "{OUT}"], "offline": True, "as_of": True},
    "gpw-earnings-reaction": {"script": "scripts/gpw_earnings_reaction.py",
                              "args": ["ticker", "{T}", "--output-dir", "{OUT}"], "offline": True, "as_of": True},
    "gpw-pair-trading": {"script": "scripts/gpw_pair_trading.py",
                         "args": ["scan", "--sector", "{SECTOR}", "--output-dir", "{OUT}"], "offline": True,
                         "as_of": True, "needs": "sector"},
    "gpw-risk-lab": {"script": "scripts/gpw_risk_lab.py", "args": ["stress", "--journal", "--output-dir", "{OUT}"],
                     "offline": True, "as_of": True, "needs": "journal_position"},
}

# Producers first: regime reads breadth, exposure reads all three, valuation
# reads the macro report (PL 10Y yield).
RUN_ORDER = ["gpw-market-breadth", "gpw-sector-trend", "gpw-market-regime", "gpw-exposure-coach",
             "gpw-macro-regime", "gpw-trend-screener", "gpw-quality-screener", "gpw-valuation",
             "gpw-dividend-analyzer", "gpw-ownership", "gpw-event-calendar", "gpw-earnings-reaction",
             "gpw-pair-trading", "gpw-risk-lab"]


def skill_dir(skill: str, skills_root: Optional[Path] = None) -> Path:
    return Path(skills_root or SKILLS_ROOT) / skill


def command_for(skill: str, ticker: str, out_dir: str, *, lang: str = "pl", offline: bool = False,
                as_of: Optional[str] = None, sector: Optional[str] = None, has_journal_position: bool = False,
                python: Optional[str] = None) -> Optional[Dict]:
    """argv (run from the skill directory) or None with a reason.

    Returns ``{skill, argv, cwd_hint, runnable, reason}``; ``runnable`` is
    False when a precondition is missing (pairs need the ticker's sector,
    risk-lab needs the ticker among the journal's open positions).
    """
    spec = CLI.get(skill)
    if spec is None:
        return None
    reason = None
    if spec.get("needs") == "sector" and not sector:
        reason = "no_sector"
    if spec.get("needs") == "journal_position" and not has_journal_position:
        reason = "not_in_open_positions"
    sub = {"{T}": ticker, "{OUT}": str(out_dir), "{SECTOR}": sector or "<SECTOR>"}
    args = [sub.get(a, a) for a in spec["args"]]
    argv = [python or "python3", spec["script"]] + args + ["--lang", lang]
    if offline and spec["offline"]:
        argv.append("--offline")
    if as_of and spec["as_of"]:
        argv += ["--as-of", as_of]
    return {"skill": skill, "argv": argv, "runnable": reason is None, "reason": reason}


def display(cmd: Dict, skills_root: Optional[Path] = None) -> str:
    d = skill_dir(cmd["skill"], skills_root)
    argv = list(cmd["argv"])
    if argv and argv[0] == sys.executable:
        argv[0] = "python3"
    return "cd {} && {}".format(shlex.quote(str(d)), " ".join(shlex.quote(a) for a in argv))


def run_commands(cmds: List[Dict], timeout_s: float, skills_root: Optional[Path] = None,
                 progress=None) -> List[Dict]:
    """Run each runnable command sequentially; never raises."""
    log = []
    for cmd in cmds:
        d = skill_dir(cmd["skill"], skills_root)
        entry = {"skill": cmd["skill"], "command": display(cmd, skills_root), "status": None,
                 "exit_code": None, "duration_s": None, "stderr_tail": None}
        if not cmd.get("runnable", True):
            entry["status"] = "skipped"
            entry["stderr_tail"] = cmd.get("reason")
            log.append(entry)
            continue
        if not (d / CLI[cmd["skill"]]["script"]).is_file():
            entry["status"] = "not_installed"
            log.append(entry)
            continue
        if progress:
            progress(entry["command"])
        t0 = time.monotonic()
        try:
            cp = subprocess.run(cmd["argv"], cwd=str(d), capture_output=True, text=True, timeout=timeout_s)
            entry["exit_code"] = cp.returncode
            entry["status"] = "ok" if cp.returncode == 0 else "failed"
            if cp.returncode != 0:
                entry["stderr_tail"] = (cp.stderr or cp.stdout or "")[-600:].strip()
        except subprocess.TimeoutExpired:
            entry["status"] = "timeout"
            entry["stderr_tail"] = "timeout after {:.0f} s".format(timeout_s)
        except OSError as exc:
            entry["status"] = "failed"
            entry["stderr_tail"] = str(exc)
        entry["duration_s"] = round(time.monotonic() - t0, 1)
        log.append(entry)
    return log
