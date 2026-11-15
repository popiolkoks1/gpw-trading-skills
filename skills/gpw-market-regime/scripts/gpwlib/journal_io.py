"""Read-only access to the gpw-trade-journal state for other skills.

The journal itself (skills/gpw-trade-journal/scripts/journal.py) owns writing
and validation. Other skills (portfolio check, pre-trade gate, exposure coach)
only need to read theses and derive open positions, so this module keeps that
logic in one place and never writes.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional


def default_state_dir() -> Path:
    env = os.environ.get("GPW_JOURNAL_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".gpw-trading-skills" / "journal"


def load_theses(state_dir: Optional[Path] = None) -> List[Dict]:
    d = Path(state_dir or default_state_dir()) / "theses"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            print("warning: skipping unreadable {}".format(p), file=sys.stderr)
    return out


def open_lots(fills: List[Dict]) -> List[Dict]:
    """Remaining FIFO buy lots after all sells: [{'date','shares','price','fee'}]."""
    lots: List[Dict] = []
    for f in sorted(fills, key=lambda f: f["date"]):
        if f["side"] == "buy":
            lots.append({"date": f["date"], "shares": int(f["shares"]), "price": float(f["price"]),
                         "fee": float(f.get("fee") or 0.0), "orig": int(f["shares"])})
        else:
            left = int(f["shares"])
            while left > 0 and lots:
                take = min(left, lots[0]["shares"])
                lots[0]["shares"] -= take
                left -= take
                if lots[0]["shares"] == 0:
                    lots.pop(0)
    for lot in lots:
        lot["fee"] = round(lot["fee"] * lot["shares"] / lot["orig"], 2) if lot["orig"] else 0.0
        del lot["orig"]
    return lots


def open_positions(theses: List[Dict], include_paper: bool = False) -> List[Dict]:
    """One row per ACTIVE thesis with shares still held."""
    rows = []
    for th in theses:
        if th.get("status") != "ACTIVE":
            continue
        if th.get("paper") and not include_paper:
            continue
        lots = open_lots(th.get("fills", []))
        shares = sum(l["shares"] for l in lots)
        if shares <= 0:
            continue
        cost = sum(l["shares"] * l["price"] for l in lots)
        plan = th.get("plan", {})
        rows.append({
            "thesis_id": th["id"],
            "ticker": th["ticker"],
            "shares": shares,
            "avg_price": round(cost / shares, 4),
            "cost": round(cost, 2),
            "stop": plan.get("stop"),
            "target": plan.get("target"),
            "account_type": th.get("account_type", "regular"),
            "setup": th.get("setup"),
            "paper": bool(th.get("paper")),
            "first_buy": lots[0]["date"] if lots else None,
        })
    return rows


def closed_results(theses: List[Dict], include_paper: bool = False) -> List[Dict]:
    """Closed theses with an outcome, oldest close first."""
    rows = []
    for th in theses:
        if th.get("status") != "CLOSED" or not th.get("outcome"):
            continue
        if th.get("paper") and not include_paper:
            continue
        o = th["outcome"]
        rows.append({"thesis_id": th["id"], "ticker": th["ticker"], "closed_at": o.get("closed_at"),
                     "net_pnl": o.get("net_pnl"), "r_multiple": o.get("r_multiple"),
                     "followed_plan": o.get("followed_plan")})
    rows.sort(key=lambda r: r["closed_at"] or "")
    return rows
