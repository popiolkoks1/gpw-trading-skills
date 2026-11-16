"""Orchestrator of gpw-daily-brief (``run`` sub-command).

Runs the sibling GPW skills in the order of docs/ROADMAP_v1.2.md section 3
as separate processes (skills talk through JSON files, never imports):
every step gets the same ``--output-dir`` so all reports land together,
has a timeout, and its exit code, duration and new output files are logged.
A failing step never stops the run; the brief then shows the gap in its
freshness table.

Sibling skills are located relative to this file
(``<skills root>/gpw-daily-brief/scripts`` -> ``<skills root>/gpw-xxx/scripts``),
which works in the repository and in an ``~/.claude/skills`` install.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

SKILLS_ROOT = Path(__file__).resolve().parent.parent.parent

# Default per-step timeouts in seconds. Market-data steps that may fill a
# cold archive cache (first run: ~500 sessions at ~0.6 s each, see
# gpw-market-breadth SKILL.md "~20-40 min") get 45 minutes; steps that only
# read the cache or a few ESPI days get 10 minutes; pure JSON consumers 2.
T_COLD = 2700
T_NET = 600
T_LOCAL = 120


def _common(ctx: Dict, offline: bool = True) -> List[str]:
    out = ["--lang", ctx["lang"]]
    if offline and ctx.get("offline"):
        out.append("--offline")
    return out


def _asof(ctx: Dict) -> List[str]:
    return ["--as-of", ctx["as_of"]] if ctx.get("as_of") else []


def _state(ctx: Dict, sub: str = "") -> List[str]:
    root = ctx.get("state_root")
    if not root:
        return []
    return ["--state-dir", str(Path(root) / sub) if sub else str(root)]


def _journal(ctx: Dict, flag: str) -> List[str]:
    """Pass an explicit journal folder on (each skill names the flag differently)."""
    return [flag, str(ctx["journal_dir"])] if ctx.get("journal_dir") else []


def _watch(ctx: Dict) -> List[str]:
    return ["--watchlist", str(ctx["watchlist"])] if ctx.get("watchlist") else []


def _watch_exists(ctx: Dict) -> bool:
    """Checked when the step runs: watchlist-ingest may have just created it."""
    p = ctx.get("watchlist_path")
    return bool(p and Path(p).exists())


# Each step: id, skill folder, script, argv builder, timeout, weekly flag and
# an optional condition (ctx -> (bool, reason)).
def _steps() -> List[Dict]:
    O = lambda c: ["--output-dir", c["output_dir"]]  # noqa: E731
    I = lambda c: ["--input-dir", c["output_dir"]]  # noqa: E731
    has_pos = lambda c: (bool(c.get("has_positions")), "no open journal positions")  # noqa: E731
    has_pos_or_wl = lambda c: (bool(c.get("has_positions") or _watch_exists(c)),  # noqa: E731
                               "no open positions and no watchlist")
    return [
        {"id": "breadth", "skill": "gpw-market-breadth", "script": "gpw_breadth.py", "timeout": T_COLD,
         "args": lambda c: _common(c) + _asof(c) + O(c) + _state(c)},
        {"id": "sector", "skill": "gpw-sector-trend", "script": "gpw_sector_trend.py", "timeout": T_COLD,
         "args": lambda c: _common(c) + _asof(c) + O(c)},
        {"id": "regime", "skill": "gpw-market-regime", "script": "gpw_market_regime.py", "timeout": T_NET,
         "args": lambda c: _common(c) + _asof(c) + I(c) + O(c)},
        {"id": "exposure", "skill": "gpw-exposure-coach", "script": "gpw_exposure_coach.py", "timeout": T_LOCAL,
         "args": lambda c: _common(c, offline=False) + _asof(c) + I(c) + O(c) + _journal(c, "--state-dir")
         + (["--account-size", str(c["account_size"])] if c.get("account_size") else [])},
        {"id": "screener", "skill": "gpw-trend-screener", "script": "gpw_trend_screener.py", "timeout": T_COLD,
         "args": lambda c: _common(c) + _asof(c) + I(c) + O(c)
         + (["--account-size", str(c["account_size"])] if c.get("account_size") else [])},
        {"id": "watchlist-ingest", "skill": "gpw-watchlist", "script": "gpw_watchlist.py", "timeout": T_LOCAL,
         "args": lambda c: ["ingest", "--lang", c["lang"]] + _asof(c) + I(c) + O(c) + _state(c)
         + _journal(c, "--journal-dir")},
        {"id": "calendar", "skill": "gpw-event-calendar", "script": "gpw_event_calendar.py", "timeout": T_NET,
         "args": lambda c: ["upcoming", "--sessions", "10"] + _common(c) + _asof(c) + O(c)},
        {"id": "calendar-portfolio", "skill": "gpw-event-calendar", "script": "gpw_event_calendar.py",
         "timeout": T_NET, "condition": has_pos_or_wl,
         "args": lambda c: ["portfolio", "--sessions", "10"] + _common(c) + _asof(c) + O(c)
         + _journal(c, "--journal-dir") + _watch(c)},
        {"id": "watchlist-refresh", "skill": "gpw-watchlist", "script": "gpw_watchlist.py", "timeout": T_NET,
         "args": lambda c: ["refresh"] + _common(c) + _asof(c) + I(c) + O(c) + _state(c)
         + _journal(c, "--journal-dir")},
        {"id": "shorts", "skill": "gpw-ownership", "script": "gpw_ownership.py", "timeout": T_NET,
         "args": lambda c: ["shorts"] + _common(c) + _asof(c) + _state(c) + O(c)},
        {"id": "insiders", "skill": "gpw-ownership", "script": "gpw_ownership.py", "timeout": T_NET,
         "args": lambda c: ["insiders", "--days", str(c.get("insider_days", 3))] + _common(c) + _asof(c)
         + _state(c) + O(c)},
        {"id": "earnings", "skill": "gpw-earnings-reaction", "script": "gpw_earnings_reaction.py", "timeout": T_NET,
         "args": lambda c: ["scan", "--lookback-days", str(c.get("earnings_lookback", 30))] + _common(c) + _asof(c)
         + O(c)},
        {"id": "postmortem-record", "skill": "gpw-signal-postmortem", "script": "gpw_signal_postmortem.py",
         "timeout": T_LOCAL,
         "args": lambda c: ["record", "--lang", c["lang"], "--input-dir", c["output_dir"], "--latest"]
         + _state(c, "signals")},
        # ---- weekly
        {"id": "macro", "skill": "gpw-macro-regime", "script": "gpw_macro_regime.py", "timeout": T_NET,
         "weekly": True, "args": lambda c: _common(c) + _asof(c) + I(c) + O(c)},
        {"id": "portfolio", "skill": "gpw-portfolio-check", "script": "portfolio_check.py", "timeout": T_NET,
         "weekly": True, "condition": has_pos,
         "args": lambda c: ["--from-journal"] + _common(c) + _asof(c) + O(c) + _journal(c, "--state-dir")},
        {"id": "risk-stress", "skill": "gpw-risk-lab", "script": "gpw_risk_lab.py", "timeout": T_NET,
         "weekly": True, "condition": has_pos,
         "args": lambda c: ["stress", "--journal"] + _common(c) + _asof(c) + O(c) + _journal(c, "--state-dir")},
        {"id": "postmortem-evaluate", "skill": "gpw-signal-postmortem", "script": "gpw_signal_postmortem.py",
         "timeout": T_NET, "weekly": True,
         "args": lambda c: ["evaluate"] + _common(c) + _asof(c) + I(c) + O(c) + _state(c, "signals")},
        {"id": "dividend", "skill": "gpw-dividend-analyzer", "script": "gpw_dividend_analyzer.py", "timeout": T_NET,
         "weekly": True, "condition": has_pos,
         "args": lambda c: ["portfolio"] + _common(c) + _asof(c) + O(c) + _journal(c, "--journal-dir")},
    ]


STEPS = _steps()
STEP_IDS = [s["id"] for s in STEPS]
DAILY_IDS = [s["id"] for s in STEPS if not s.get("weekly")]
WEEKLY_IDS = [s["id"] for s in STEPS if s.get("weekly")]


def resolve_script(step: Dict, skills_root: Path) -> Optional[Path]:
    p = Path(skills_root) / step["skill"] / "scripts" / step["script"]
    return p if p.is_file() else None


def _json_files(folder: Path) -> Dict[str, float]:
    try:
        return {p.name: p.stat().st_mtime for p in folder.glob("*.json") if p.is_file()}
    except OSError:
        return {}


def _tail(text: str, n: int = 8) -> str:
    lines = [l for l in (text or "").strip().splitlines() if l.strip()]
    return "\n".join(lines[-n:])


def select_steps(steps: Optional[List[str]], skip: Optional[List[str]], weekly: bool) -> List[Dict]:
    """Steps to run in pipeline order. ``steps`` = explicit subset (any ids,
    weekly ones included); otherwise daily (+ weekly with ``weekly``)."""
    wanted = list(steps) if steps else (DAILY_IDS + (WEEKLY_IDS if weekly else []))
    unknown = [s for s in (wanted + list(skip or [])) if s not in STEP_IDS]
    if unknown:
        raise ValueError("unknown step(s): {} (known: {})".format(", ".join(unknown), ", ".join(STEP_IDS)))
    skip = set(skip or [])
    return [s for s in STEPS if s["id"] in wanted and s["id"] not in skip]


def run_pipeline(ctx: Dict, steps: List[Dict], skills_root: Path = SKILLS_ROOT,
                 timeout: Optional[float] = None, log: Optional[Callable[[str], None]] = None,
                 python: str = sys.executable) -> List[Dict]:
    """Execute ``steps``; return one record per step (never raises for a
    failing step)."""
    out_dir = Path(ctx["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for i, step in enumerate(steps, 1):
        rec = {"step": step["id"], "skill": step["skill"], "status": None, "exit_code": None, "duration_s": 0.0,
               "outputs": [], "command": None, "reason": None, "stderr_tail": None}
        script = resolve_script(step, skills_root)
        cond = step.get("condition")
        if script is None:
            rec["status"] = "not_installed"
            rec["reason"] = "{}/scripts/{} not found".format(step["skill"], step["script"])
        elif cond is not None and not cond(ctx)[0]:
            rec["status"] = "skipped"
            rec["reason"] = cond(ctx)[1]
        else:
            argv = [python, str(script)] + step["args"](ctx)
            rec["command"] = "cd {} && {}".format(script.parent.parent, " ".join(
                [Path(argv[0]).name] + ["scripts/" + script.name] + argv[2:]))
            before = _json_files(out_dir)
            t0 = time.monotonic()
            limit = timeout if timeout is not None else step["timeout"]
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            try:
                proc = subprocess.run(argv, cwd=str(script.parent.parent), capture_output=True, text=True,
                                      timeout=limit, env=env)
                rec["exit_code"] = proc.returncode
                rec["status"] = "ok" if proc.returncode == 0 else "failed"
                if proc.returncode != 0:
                    rec["stderr_tail"] = _tail(proc.stderr) or _tail(proc.stdout)
            except subprocess.TimeoutExpired as exc:
                rec["status"] = "timeout"
                rec["reason"] = "timeout after {} s".format(limit)
                err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
                rec["stderr_tail"] = _tail(err) or None
            except OSError as exc:
                rec["status"] = "failed"
                rec["reason"] = str(exc)
            rec["duration_s"] = round(time.monotonic() - t0, 2)
            after = _json_files(out_dir)
            rec["outputs"] = sorted(str(out_dir / n) for n, m in after.items() if before.get(n) != m)
        records.append(rec)
        if log:
            log(format_step(i, len(steps), rec))
    return records


def format_step(i: int, n: int, rec: Dict) -> str:
    outs = ", ".join(Path(p).name for p in rec["outputs"]) or "-"
    extra = ""
    if rec["status"] not in ("ok",):
        extra = " | " + (rec.get("reason") or (rec.get("stderr_tail") or "").splitlines()[-1:][0]
                         if (rec.get("reason") or rec.get("stderr_tail")) else "")
    code = "-" if rec["exit_code"] is None else rec["exit_code"]
    return "[{:>2}/{}] {:<20} {:<13} exit={:<3} {:>7.1f}s  {}{}".format(
        i, n, rec["step"], rec["status"], code, rec["duration_s"], outs, extra)
