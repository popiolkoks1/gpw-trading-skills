#!/usr/bin/env python3
"""GPW trade journal - falsifiable investment theses with FIFO fills.

Inspired by tradermonty/claude-trading-skills ``trader-memory-core`` (MIT)
and the thesis-tracker pattern from Anthropic's financial-services skills:

* every idea is a *thesis* with pillars, risks and at least one falsifiable
  **kill criterion** ("what would prove me wrong");
* forward-only lifecycle IDEA -> READY -> ACTIVE -> CLOSED | INVALIDATED;
* buy/sell fills with commissions, FIFO lot matching (the method required by
  Polish tax law for PIT-38), partial closes;
* on close: P&L gross/net, holding days, R multiple, WIG benchmark return and
  excess return, process score (did I follow my plan) and lessons.

Storage: one JSON file per thesis in ``<state-dir>/theses/``; atomic writes
(temp file + os.replace). Schema documented in references/journal_schema.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import secrets
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpwlib import fmt  # noqa: E402
from gpwlib.fmt import DISCLAIMER, T  # noqa: E402

SCHEMA_VERSION = "1.0"
STATUSES = ["IDEA", "READY", "ACTIVE", "CLOSED", "INVALIDATED"]
TERMINAL = {"CLOSED", "INVALIDATED"}
ORDER = {"IDEA": 0, "READY": 1, "ACTIVE": 2, "CLOSED": 3, "INVALIDATED": 3}
SETUPS = ["breakout", "trend", "pullback", "value", "dividend", "growth", "earnings", "mean_reversion", "other"]
HORIZONS = ["swing", "position", "long_term"]
ACCOUNT_TYPES = ["regular", "ike", "ikze", "oki"]
EXIT_REASONS = ["target", "stop", "kill_criterion", "time", "discretionary", "other"]
CONVICTION = ["H", "M", "L"]
DEFAULT_REVIEW_DAYS = 30


class JournalError(ValueError):
    pass


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def default_state_dir() -> Path:
    env = os.environ.get("GPW_JOURNAL_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".gpw-trading-skills" / "journal"


def theses_dir(state_dir: Path) -> Path:
    return Path(state_dir) / "theses"


def _path(state_dir: Path, thesis_id: str) -> Path:
    if not thesis_id or "/" in thesis_id or "\\" in thesis_id or thesis_id.startswith("."):
        raise JournalError("invalid thesis id: {!r}".format(thesis_id))
    return theses_dir(state_dir) / "{}.json".format(thesis_id)


def save(state_dir: Path, thesis: Dict) -> Path:
    validate(thesis)
    path = _path(state_dir, thesis["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(thesis, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load(state_dir: Path, thesis_id: str) -> Dict:
    path = _path(state_dir, thesis_id)
    if not path.exists():
        matches = [p.stem for p in theses_dir(state_dir).glob("{}*.json".format(thesis_id))] if theses_dir(state_dir).exists() else []
        if len(matches) == 1:
            path = _path(state_dir, matches[0])
        elif len(matches) > 1:
            raise JournalError("ambiguous id prefix {!r}: {}".format(thesis_id, ", ".join(sorted(matches))))
        else:
            raise JournalError("thesis not found: {}".format(thesis_id))
    return json.loads(path.read_text(encoding="utf-8"))


def load_all(state_dir: Path) -> List[Dict]:
    d = theses_dir(state_dir)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            print("warning: skipping unreadable {}".format(p), file=sys.stderr)
    return out


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _is_date(s) -> bool:
    try:
        dt.date.fromisoformat(s)
        return True
    except (TypeError, ValueError):
        return False


def _pos(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def validate(th: Dict) -> None:
    """Raise JournalError if the thesis breaks the schema or business rules."""
    req = ["schema_version", "id", "ticker", "thesis", "pillars", "kill_criteria", "status",
           "status_history", "plan", "account_type", "conviction", "created_at", "fills", "updates"]
    missing = [k for k in req if k not in th]
    if missing:
        raise JournalError("missing fields: {}".format(", ".join(missing)))
    if not str(th["thesis"]).strip():
        raise JournalError("thesis statement is empty")
    if not 1 <= len(th["pillars"]) <= 5:
        raise JournalError("a thesis needs 1-5 pillars (got {})".format(len(th["pillars"])))
    if not th["kill_criteria"] or not all(str(k).strip() for k in th["kill_criteria"]):
        raise JournalError("at least one falsifiable kill criterion is required")
    if th["status"] not in STATUSES:
        raise JournalError("unknown status {}".format(th["status"]))
    if th["account_type"] not in ACCOUNT_TYPES:
        raise JournalError("account_type must be one of {}".format(ACCOUNT_TYPES))
    if th["conviction"] not in CONVICTION:
        raise JournalError("conviction must be H, M or L")
    if th.get("setup") and th["setup"] not in SETUPS:
        raise JournalError("setup must be one of {}".format(SETUPS))
    if th.get("review_date") and not _is_date(th["review_date"]):
        raise JournalError("review_date must be YYYY-MM-DD")
    plan = th["plan"]
    for k in ("entry", "stop", "target"):
        if plan.get(k) is not None and not _pos(plan[k]):
            raise JournalError("plan.{} must be a positive number".format(k))
    # Before entry the stop must sit below the planned entry (long-only). Once
    # ACTIVE a profitable long may trail its stop above the original entry.
    if th["status"] in ("IDEA", "READY") and plan.get("entry") and plan.get("stop") and plan["stop"] >= plan["entry"]:
        raise JournalError("plan.stop must be below plan.entry (long-only journal)")
    if plan.get("entry") and plan.get("target") and plan["target"] <= plan["entry"]:
        raise JournalError("plan.target must be above plan.entry")
    held = 0
    for f in th["fills"]:
        if f.get("side") not in ("buy", "sell"):
            raise JournalError("fill side must be buy or sell")
        if not _is_date(f.get("date")):
            raise JournalError("fill date must be YYYY-MM-DD")
        if not isinstance(f.get("shares"), int) or isinstance(f.get("shares"), bool) or f["shares"] <= 0:
            raise JournalError("fill shares must be a positive whole number (GPW lot = 1)")
        if not _pos(f.get("price")):
            raise JournalError("fill price must be positive")
        if not isinstance(f.get("fee", 0), (int, float)) or f.get("fee", 0) < 0:
            raise JournalError("fill fee must be >= 0")
        held += f["shares"] if f["side"] == "buy" else -f["shares"]
        if held < 0:
            raise JournalError("sell exceeds shares held (no short selling)")
    dates = [f["date"] for f in th["fills"]]
    if dates != sorted(dates):
        raise JournalError("fills must be in chronological order")


# --------------------------------------------------------------------------
# Core operations (pure on dicts)
# --------------------------------------------------------------------------

def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def make_id(ticker: str, day: dt.date) -> str:
    safe = "".join(c for c in ticker.lower() if c.isalnum())
    return "th_{}_{}_{}".format(safe, day.strftime("%Y%m%d"), secrets.token_hex(2))


def new_thesis(*, ticker: str, thesis: str, pillars: List[str], kill_criteria: List[str],
               risks: Optional[List[str]] = None, catalysts: Optional[List[str]] = None,
               entry=None, stop=None, target=None, shares_planned=None, risk_pct=None,
               account_type: str = "regular", setup: str = "other", horizon: str = "swing",
               conviction: str = "M", review_date: Optional[str] = None,
               today: Optional[dt.date] = None, source: Optional[Dict] = None) -> Dict:
    today = today or dt.date.today()
    th = {
        "schema_version": SCHEMA_VERSION,
        "id": make_id(ticker, today),
        "ticker": ticker.strip().upper(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "thesis": thesis.strip(),
        "setup": setup,
        "horizon": horizon,
        "pillars": [p.strip() for p in pillars if p.strip()],
        "risks": [r.strip() for r in (risks or []) if r.strip()],
        "kill_criteria": [k.strip() for k in kill_criteria if k.strip()],
        "catalysts": [c.strip() for c in (catalysts or []) if c.strip()],
        "plan": {"entry": entry, "stop": stop, "target": target,
                 "shares_planned": shares_planned, "risk_pct": risk_pct},
        "account_type": account_type,
        "conviction": conviction,
        "review_date": review_date or (today + dt.timedelta(days=DEFAULT_REVIEW_DAYS)).isoformat(),
        "status": "IDEA",
        "status_history": [{"status": "IDEA", "date": today.isoformat(), "note": "created"}],
        "updates": [],
        "fills": [],
        "source": source or {},
        "outcome": None,
    }
    validate(th)
    return th


def transition(th: Dict, new_status: str, day: dt.date, note: str = "") -> Dict:
    new_status = new_status.upper()
    if new_status not in STATUSES:
        raise JournalError("unknown status {}".format(new_status))
    cur = th["status"]
    if cur in TERMINAL:
        raise JournalError("thesis is already {} - create a new thesis instead".format(cur))
    if new_status == "INVALIDATED":
        pass  # allowed from any non-terminal state
    elif ORDER[new_status] <= ORDER[cur]:
        raise JournalError("transitions are forward-only: {} -> {} not allowed".format(cur, new_status))
    if new_status == "ACTIVE" and shares_held(th) == 0:
        raise JournalError("ACTIVE requires a buy fill - use the 'fill' command")
    if new_status == "CLOSED":
        raise JournalError("use the 'close' command to close a thesis (it records the outcome)")
    if new_status == "INVALIDATED" and shares_held(th) > 0:
        raise JournalError("position still open - sell it with 'close --exit-reason kill_criterion' instead")
    th["status"] = new_status
    th["status_history"].append({"status": new_status, "date": day.isoformat(), "note": note})
    th["updated_at"] = now_iso()
    return th


def add_update(th: Dict, day: dt.date, note: str, pillar: Optional[int] = None,
               conviction: Optional[str] = None, stop=None, target=None, review_date=None) -> Dict:
    if th["status"] in TERMINAL:
        raise JournalError("thesis is {} - updates are closed".format(th["status"]))
    if pillar is not None and not 1 <= pillar <= len(th["pillars"]):
        raise JournalError("pillar must be 1..{}".format(len(th["pillars"])))
    entry = {"date": day.isoformat(), "note": note.strip()}
    if pillar is not None:
        entry["pillar"] = pillar
    if conviction:
        if conviction not in CONVICTION:
            raise JournalError("conviction must be H, M or L")
        entry["conviction_from"] = th["conviction"]
        entry["conviction"] = conviction
        th["conviction"] = conviction
    if stop is not None:
        entry["stop_from"] = th["plan"].get("stop")
        entry["stop"] = stop
        if th["plan"].get("stop") is not None and "initial_stop" not in th["plan"]:
            th["plan"]["initial_stop"] = th["plan"]["stop"]
        th["plan"]["stop"] = stop
    if target is not None:
        entry["target_from"] = th["plan"].get("target")
        entry["target"] = target
        th["plan"]["target"] = target
    if review_date:
        th["review_date"] = review_date
        entry["review_date"] = review_date
    th["updates"].append(entry)
    th["updated_at"] = now_iso()
    validate(th)
    return th


def shares_held(th: Dict) -> int:
    return sum(f["shares"] if f["side"] == "buy" else -f["shares"] for f in th["fills"])


def add_fill(th: Dict, side: str, shares: int, price: float, fee: float, day: dt.date) -> Dict:
    if th["status"] in TERMINAL:
        raise JournalError("thesis is {} - no more fills".format(th["status"]))
    if th["fills"] and day.isoformat() < th["fills"][-1]["date"]:
        raise JournalError("fill date is earlier than the previous fill")
    th["fills"].append({"date": day.isoformat(), "side": side, "shares": int(shares),
                        "price": float(price), "fee": round(float(fee), 2)})
    if side == "buy" and th["status"] in ("IDEA", "READY"):
        th["status"] = "ACTIVE"
        th["status_history"].append({"status": "ACTIVE", "date": day.isoformat(), "note": "first buy fill"})
    try:
        validate(th)
    except JournalError:
        th["fills"].pop()
        raise
    th["updated_at"] = now_iso()
    return th


def fifo_matches(fills: List[Dict]) -> List[Dict]:
    """Match sells against buys first-in-first-out.

    Returns one record per (sell, matched buy lot) with the buy fee allocated
    pro rata to shares. FIFO is the method required for securities in Polish
    PIT (art. 24 ust. 10 ustawy o PIT).
    """
    lots = []  # [date, shares_left, price, fee_per_share]
    out = []
    for f in fills:
        if f["side"] == "buy":
            lots.append([f["date"], f["shares"], f["price"], f.get("fee", 0.0) / f["shares"]])
            continue
        left = f["shares"]
        sell_fee_ps = f.get("fee", 0.0) / f["shares"]
        while left > 0:
            if not lots:
                raise JournalError("sell exceeds shares held")
            lot = lots[0]
            n = min(left, lot[1])
            out.append({
                "sell_date": f["date"], "buy_date": lot[0], "shares": n,
                "buy_price": lot[2], "sell_price": f["price"],
                "proceeds": round(n * f["price"], 2),
                "cost": round(n * lot[2], 2),
                "buy_fee": round(n * lot[3], 2),
                "sell_fee": round(n * sell_fee_ps, 2),
            })
            lot[1] -= n
            left -= n
            if lot[1] == 0:
                lots.pop(0)
    return out


def realized(th: Dict) -> Dict:
    m = fifo_matches(th["fills"])
    gross = sum(x["proceeds"] - x["cost"] for x in m)
    fees = sum(x["buy_fee"] + x["sell_fee"] for x in m)
    bought = [f for f in th["fills"] if f["side"] == "buy"]
    sh_b = sum(f["shares"] for f in bought)
    avg_entry = sum(f["shares"] * f["price"] for f in bought) / sh_b if sh_b else None
    cost = sum(x["cost"] for x in m)
    return {
        "matches": m,
        "shares_closed": sum(x["shares"] for x in m),
        "gross_pnl": round(gross, 2),
        "fees": round(fees, 2),
        "net_pnl": round(gross - fees, 2),
        "return_pct": round((gross - fees) / cost * 100, 3) if cost else None,
        "avg_entry": round(avg_entry, 4) if avg_entry else None,
    }


def r_multiple(th: Dict, net_pnl: float, shares_closed: int, avg_entry: Optional[float]) -> Optional[float]:
    stop = th["plan"].get("initial_stop") or th["plan"].get("stop")
    if not stop or not avg_entry or avg_entry <= stop or not shares_closed:
        return None
    initial_risk = shares_closed * (avg_entry - stop)
    return round(net_pnl / initial_risk, 2) if initial_risk > 0 else None


def close(th: Dict, *, day: dt.date, exit_reason: str, followed_entry: bool, followed_stop: bool,
          followed_size: bool, lessons: str = "", price=None, fee: float = 0.0,
          benchmark: Optional[Dict] = None) -> Dict:
    if th["status"] != "ACTIVE":
        raise JournalError("only an ACTIVE thesis can be closed (status: {})".format(th["status"]))
    if exit_reason not in EXIT_REASONS:
        raise JournalError("exit reason must be one of {}".format(EXIT_REASONS))
    held = shares_held(th)
    if held > 0:
        if price is None:
            raise JournalError("{} shares still held - pass --price (and --fee) to sell them".format(held))
        add_fill(th, "sell", held, price, fee, day)
    r = realized(th)
    buys = [f["date"] for f in th["fills"] if f["side"] == "buy"]
    sells = [f["date"] for f in th["fills"] if f["side"] == "sell"]
    first, last = buys[0], sells[-1]
    holding_days = (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days
    process_flags = {"entry": bool(followed_entry), "stop": bool(followed_stop), "size": bool(followed_size)}
    outcome = {
        "closed_at": day.isoformat(),
        "first_buy": first,
        "last_sell": last,
        "holding_days": holding_days,
        "exit_reason": exit_reason,
        "shares": r["shares_closed"],
        "avg_entry": r["avg_entry"],
        "gross_pnl": r["gross_pnl"],
        "fees": r["fees"],
        "net_pnl": r["net_pnl"],
        "return_pct": r["return_pct"],
        "r_multiple": r_multiple(th, r["net_pnl"], r["shares_closed"], r["avg_entry"]),
        "benchmark": benchmark,
        "excess_return_pct": (round(r["return_pct"] - benchmark["return_pct"], 3)
                              if benchmark and benchmark.get("return_pct") is not None and r["return_pct"] is not None
                              else None),
        "process": process_flags,
        "process_score": round(sum(process_flags.values()) / 3, 3),
        "followed_plan": all(process_flags.values()),
        "lessons": lessons.strip(),
        "fifo_matches": r["matches"],
    }
    th["outcome"] = outcome
    th["status"] = "CLOSED"
    th["status_history"].append({"status": "CLOSED", "date": day.isoformat(), "note": exit_reason})
    th["updated_at"] = now_iso()
    validate(th)
    return th


def benchmark_return(first: str, last: str, index: str = "WIG", offline: bool = False) -> Dict:
    """Index return (close-to-close) between two dates, from the GPW archive."""
    from gpwlib import gpw_archive, panel

    d0, d1 = dt.date.fromisoformat(first), dt.date.fromisoformat(last)
    n = int((d1 - d0).days * 0.75) + 8
    sessions = gpw_archive.load_sessions(n, end=d1, type_id=gpw_archive.TYPE_INDICES,
                                         offline=offline, progress=False)
    s = panel.index_series(sessions, index)
    if not s.dates:
        return {"index": index, "return_pct": None, "note": "no index data"}
    # close of the last session strictly before the first buy (we bought during d0)
    start_i = max((i for i, d in enumerate(s.dates) if d < first), default=None)
    if start_i is None:
        start_i = 0
    end_i = max(i for i, d in enumerate(s.dates) if d <= last)
    c0, c1 = s.close[start_i], s.close[end_i]
    return {"index": index, "from": s.dates[start_i], "to": s.dates[end_i],
            "start": c0, "end": c1, "return_pct": round((c1 / c0 - 1) * 100, 3),
            "source": "https://www.gpw.pl/archiwum-notowan"}


def review_due(theses: List[Dict], as_of: dt.date) -> List[Dict]:
    return sorted(
        [t for t in theses if t["status"] not in TERMINAL and t.get("review_date") and t["review_date"] <= as_of.isoformat()],
        key=lambda t: t["review_date"],
    )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

STRINGS = {
    "status": {"pl": "Status", "en": "Status"},
    "thesis": {"pl": "Teza", "en": "Thesis"},
    "pillars": {"pl": "Filary tezy", "en": "Pillars"},
    "risks": {"pl": "Ryzyka", "en": "Risks"},
    "kill": {"pl": "Kryteria unieważnienia (co udowodni, że się mylę)", "en": "Kill criteria (what would prove me wrong)"},
    "catalysts": {"pl": "Katalizatory", "en": "Catalysts"},
    "plan": {"pl": "Plan", "en": "Plan"},
    "entry": {"pl": "wejście", "en": "entry"},
    "stop": {"pl": "stop", "en": "stop"},
    "target": {"pl": "cel", "en": "target"},
    "account": {"pl": "Rachunek", "en": "Account"},
    "conviction": {"pl": "Przekonanie", "en": "Conviction"},
    "review": {"pl": "Następny przegląd", "en": "Next review"},
    "position": {"pl": "Pozycja", "en": "Position"},
    "held": {"pl": "{n} akcji w portfelu", "en": "{n} shares held"},
    "fills": {"pl": "Transakcje", "en": "Fills"},
    "updates": {"pl": "Dziennik zmian", "en": "Update log"},
    "outcome": {"pl": "Wynik", "en": "Outcome"},
    "net": {"pl": "Wynik netto (po prowizjach)", "en": "Net P&L (after commissions)"},
    "gross": {"pl": "Wynik brutto", "en": "Gross P&L"},
    "fees": {"pl": "Prowizje", "en": "Commissions"},
    "ret": {"pl": "Stopa zwrotu", "en": "Return"},
    "r": {"pl": "Wynik w R", "en": "R multiple"},
    "hold": {"pl": "Czas trzymania", "en": "Holding period"},
    "days": {"pl": "{n} dni", "en": "{n} days"},
    "bench": {"pl": "WIG w tym czasie", "en": "WIG over the same period"},
    "excess": {"pl": "Nadwyżka nad WIG", "en": "Excess vs WIG"},
    "process": {"pl": "Proces (wejście / stop / wielkość zgodnie z planem)", "en": "Process (entry / stop / size per plan)"},
    "lessons": {"pl": "Wnioski", "en": "Lessons"},
    "exit_reason": {"pl": "Powód wyjścia", "en": "Exit reason"},
    "list_title": {"pl": "Dziennik inwestycyjny", "en": "Trade journal"},
    "due_title": {"pl": "Tezy do przeglądu (stan na {d})", "en": "Theses due for review (as of {d})"},
    "none": {"pl": "Brak.", "en": "None."},
    "yes": {"pl": "tak", "en": "yes"},
    "no": {"pl": "nie", "en": "no"},
    "id": {"pl": "ID", "en": "ID"},
    "ticker": {"pl": "Spółka", "en": "Ticker"},
    "created": {"pl": "utworzono", "en": "created"},
    "initial": {"pl": "początkowy", "en": "initial"},
    "acct_regular": {"pl": "zwykły", "en": "regular"},
    "acct_ike": {"pl": "IKE", "en": "IKE"},
    "acct_ikze": {"pl": "IKZE", "en": "IKZE"},
    "acct_oki": {"pl": "OKI", "en": "OKI"},
    "tax_note": {
        "pl": "Na zwykłym rachunku od zysku należy się 19% podatku (PIT-38, FIFO). Zestawienie roczne: skill gpw-trade-review.",
        "en": "On a regular account the gain is subject to 19% tax (PIT-38, FIFO). Annual summary: gpw-trade-review skill.",
    },
}


def render_thesis(th: Dict, lang: str = "pl") -> str:
    t = T(STRINGS, lang)
    P = lambda x: fmt.pln(x, 2, lang)  # noqa: E731
    yn = lambda b: t("yes") if b else t("no")  # noqa: E731
    L = ["# {} – {}".format(th["ticker"], th["id"]), ""]
    L.append("**{}:** {} · **{}:** {} · **{}:** {} · **{}:** {}".format(
        t("status"), th["status"], t("conviction"), th["conviction"],
        t("account"), t("acct_" + th["account_type"]), t("review"), th.get("review_date") or "—"))
    L += ["", "## " + t("thesis"), "", th["thesis"], "", "## " + t("pillars"), ""]
    L += ["{}. {}".format(i, p) for i, p in enumerate(th["pillars"], 1)]
    if th["risks"]:
        L += ["", "## " + t("risks"), ""] + ["- " + r for r in th["risks"]]
    L += ["", "## " + t("kill"), ""] + ["- " + k for k in th["kill_criteria"]]
    if th["catalysts"]:
        L += ["", "## " + t("catalysts"), ""] + ["- " + c for c in th["catalysts"]]
    pl = th["plan"]
    L += ["", "## " + t("plan"), "",
          "{} {} · {} {}{} · {} {}".format(
              t("entry"), P(pl.get("entry")), t("stop"), P(pl.get("stop")),
              " ({} {})".format(t("initial"), P(pl["initial_stop"])) if pl.get("initial_stop") else "",
              t("target"), P(pl.get("target")))]
    if th["fills"]:
        L += ["", "## " + t("fills"), "", "| data | | szt. | cena | prowizja |" if lang == "pl" else "| date | | shares | price | fee |", "|---|---|---:|---:|---:|"]
        for f in th["fills"]:
            L.append("| {} | {} | {} | {} | {} |".format(f["date"], f["side"].upper(), f["shares"], P(f["price"]), P(f.get("fee", 0))))
        L += ["", t("held", n=shares_held(th))]
    if th["updates"]:
        L += ["", "## " + t("updates"), ""]
        for u in th["updates"]:
            extra = []
            if "pillar" in u:
                extra.append("#{}".format(u["pillar"]))
            if "conviction" in u:
                extra.append("{}→{}".format(u.get("conviction_from"), u["conviction"]))
            if "stop" in u:
                extra.append("stop {}→{}".format(u.get("stop_from"), u["stop"]))
            L.append("- {} {}{}".format(u["date"], u["note"], " ({})".format(", ".join(extra)) if extra else ""))
    o = th.get("outcome")
    if o:
        L += ["", "## " + t("outcome"), "", "| | |", "|---|---|"]
        L.append("| **{}** | **{}** |".format(t("net"), P(o["net_pnl"])))
        L.append("| {} | {} |".format(t("gross"), P(o["gross_pnl"])))
        L.append("| {} | {} |".format(t("fees"), P(o["fees"])))
        L.append("| {} | {} |".format(t("ret"), fmt.pct(o["return_pct"], 2, lang, signed=True)))
        L.append("| {} | {} |".format(t("r"), (fmt.num(o["r_multiple"], 2, lang) + "R") if o["r_multiple"] is not None else "—"))
        L.append("| {} | {} |".format(t("hold"), t("days", n=o["holding_days"])))
        b = o.get("benchmark") or {}
        if b.get("return_pct") is not None:
            L.append("| {} | {} |".format(t("bench"), fmt.pct(b["return_pct"], 2, lang, signed=True)))
            L.append("| {} | {} |".format(t("excess"), fmt.pct(o["excess_return_pct"], 2, lang, signed=True)))
        L.append("| {} | {} |".format(t("exit_reason"), o["exit_reason"]))
        pr = o["process"]
        L.append("| {} | {} / {} / {} |".format(t("process"), yn(pr["entry"]), yn(pr["stop"]), yn(pr["size"])))
        if o.get("lessons"):
            L += ["", "**{}:** {}".format(t("lessons"), o["lessons"])]
        if th["account_type"] == "regular":
            L += ["", "_" + t("tax_note") + "_"]
    L += ["", "---", "_" + DISCLAIMER[lang] + "_"]
    return "\n".join(L) + "\n"


def render_list(theses: List[Dict], lang: str = "pl", title_key: str = "list_title", **kw) -> str:
    t = T(STRINGS, lang)
    L = ["# " + t(title_key, **kw), ""]
    if not theses:
        return "\n".join(L + [t("none")]) + "\n"
    L += ["| {} | {} | {} | {} | {} |".format(t("id"), t("ticker"), t("status"), t("conviction"), t("review")),
          "|---|---|---|---|---|"]
    for th in theses:
        L.append("| {} | {} | {} | {} | {} |".format(th["id"], th["ticker"], th["status"], th["conviction"], th.get("review_date") or "—"))
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _date(s: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD")


def _pos_float(s: str) -> float:
    v = float(s.replace(",", "."))
    if not math.isfinite(v) or v <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return v


def _nonneg_float(s: str) -> float:
    v = float(s.replace(",", "."))
    if not math.isfinite(v) or v < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return v


def _yesno(s: str) -> bool:
    s = s.strip().lower()
    if s in ("y", "yes", "t", "tak", "true", "1"):
        return True
    if s in ("n", "no", "nie", "false", "0"):
        return False
    raise argparse.ArgumentTypeError("use yes/no (tak/nie)")


def build_parser() -> argparse.ArgumentParser:
    # Global options are accepted both before and after the sub-command.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state-dir", type=Path, default=argparse.SUPPRESS,
                        help="Journal directory (default ~/.gpw-trading-skills/journal or $GPW_JOURNAL_DIR)")
    common.add_argument("--lang", choices=["pl", "en"], default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Print JSON instead of Markdown")
    p = argparse.ArgumentParser(description="GPW trade journal (theses, FIFO fills, outcomes).", parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)
    _add = sub.add_parser

    def add_parser(name, **kw):
        return _add(name, parents=[common], **kw)

    sub.add_parser = add_parser  # type: ignore[assignment]

    a = sub.add_parser("add", help="Register a new thesis")
    a.add_argument("--ticker", required=True)
    a.add_argument("--thesis", required=True, help="One-paragraph thesis statement")
    a.add_argument("--pillar", action="append", default=[], help="Thesis pillar (repeat 1-5x)")
    a.add_argument("--risk", action="append", default=[], help="Key risk (repeatable)")
    a.add_argument("--kill", action="append", default=[], help="Falsifiable kill criterion (repeat, >=1 required)")
    a.add_argument("--catalyst", action="append", default=[], help="Catalyst, e.g. '2026-11-20 Q3 results' (repeatable)")
    a.add_argument("--entry", type=_pos_float)
    a.add_argument("--stop", type=_pos_float)
    a.add_argument("--target", type=_pos_float)
    a.add_argument("--shares-planned", type=int)
    a.add_argument("--risk-pct", type=_pos_float)
    a.add_argument("--sizer-json", type=Path, help="Import entry/stop/target/shares from a gpw-position-sizer JSON report")
    a.add_argument("--account-type", choices=ACCOUNT_TYPES, default="regular")
    a.add_argument("--setup", choices=SETUPS, default="other")
    a.add_argument("--horizon", choices=HORIZONS, default="swing")
    a.add_argument("--conviction", choices=CONVICTION, default="M")
    a.add_argument("--review-date", type=_date)
    a.add_argument("--status", choices=["IDEA", "READY"], default="IDEA")
    a.add_argument("--date", type=_date, default=None)

    u = sub.add_parser("update", help="Log new information / change stop, target, conviction")
    u.add_argument("id")
    u.add_argument("--note", required=True)
    u.add_argument("--pillar", type=int, help="Which pillar (1-based) the note affects")
    u.add_argument("--conviction", choices=CONVICTION)
    u.add_argument("--stop", type=_pos_float)
    u.add_argument("--target", type=_pos_float)
    u.add_argument("--review-date", type=_date)
    u.add_argument("--date", type=_date)

    tr = sub.add_parser("transition", help="Move thesis forward (READY) or INVALIDATED")
    tr.add_argument("id")
    tr.add_argument("status", choices=["READY", "INVALIDATED"])
    tr.add_argument("--note", default="")
    tr.add_argument("--date", type=_date)

    f = sub.add_parser("fill", help="Record a buy or sell execution")
    f.add_argument("id")
    f.add_argument("--side", choices=["buy", "sell"], required=True)
    f.add_argument("--shares", type=int, required=True)
    f.add_argument("--price", type=_pos_float, required=True)
    f.add_argument("--fee", type=_nonneg_float, default=0.0, help="Commission in PLN")
    f.add_argument("--date", type=_date)

    c = sub.add_parser("close", help="Close an ACTIVE thesis and record the outcome")
    c.add_argument("id")
    c.add_argument("--exit-reason", choices=EXIT_REASONS, required=True)
    c.add_argument("--followed-entry", type=_yesno, required=True)
    c.add_argument("--followed-stop", type=_yesno, required=True)
    c.add_argument("--followed-size", type=_yesno, required=True)
    c.add_argument("--lessons", default="")
    c.add_argument("--price", type=_pos_float, help="Sell the remaining shares at this price")
    c.add_argument("--fee", type=_nonneg_float, default=0.0)
    c.add_argument("--date", type=_date)
    c.add_argument("--benchmark-return", type=float, help="Manual WIG return %% over the holding period")
    c.add_argument("--no-benchmark", action="store_true", help="Skip the WIG download")
    c.add_argument("--offline", action="store_true")

    s = sub.add_parser("show", help="Show one thesis")
    s.add_argument("id")
    ls = sub.add_parser("list", help="List theses")
    ls.add_argument("--status", choices=STATUSES)
    rd = sub.add_parser("review-due", help="Theses whose review date has passed")
    rd.add_argument("--as-of", type=_date)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    state = getattr(args, "state_dir", None) or default_state_dir()
    args.json = getattr(args, "json", False)
    today = dt.date.today()
    lang = getattr(args, "lang", "pl")
    try:
        if args.cmd == "add":
            entry, stop, target, shares = args.entry, args.stop, args.target, args.shares_planned
            source = {}
            if args.sizer_json:
                rep = json.loads(Path(args.sizer_json).read_text(encoding="utf-8"))
                entry = entry or rep.get("entry")
                stop = stop or rep.get("stop")
                target = target or (rep.get("reward") or {}).get("target")
                shares = shares or rep.get("shares")
                source = {"skill": "gpw-position-sizer", "file": str(args.sizer_json)}
            th = new_thesis(ticker=args.ticker, thesis=args.thesis, pillars=args.pillar,
                            kill_criteria=args.kill, risks=args.risk, catalysts=args.catalyst,
                            entry=entry, stop=stop, target=target, shares_planned=shares,
                            risk_pct=args.risk_pct, account_type=args.account_type, setup=args.setup,
                            horizon=args.horizon, conviction=args.conviction,
                            review_date=args.review_date.isoformat() if args.review_date else None,
                            today=args.date or today, source=source)
            if args.status == "READY":
                transition(th, "READY", args.date or today, "ready at creation")
        else:
            th = None
            if args.cmd in ("update", "transition", "fill", "close", "show"):
                th = load(state, args.id)
            if args.cmd == "update":
                add_update(th, args.date or today, args.note, args.pillar, args.conviction,
                           args.stop, args.target, args.review_date.isoformat() if args.review_date else None)
            elif args.cmd == "transition":
                transition(th, args.status, args.date or today, args.note)
            elif args.cmd == "fill":
                add_fill(th, args.side, args.shares, args.price, args.fee, args.date or today)
            elif args.cmd == "close":
                day = args.date or today
                bench = None
                if args.benchmark_return is not None:
                    bench = {"index": "WIG", "return_pct": args.benchmark_return, "source": "user"}
                close(th, day=day, exit_reason=args.exit_reason, followed_entry=args.followed_entry,
                      followed_stop=args.followed_stop, followed_size=args.followed_size,
                      lessons=args.lessons, price=args.price, fee=args.fee, benchmark=bench)
                if bench is None and not args.no_benchmark:
                    o = th["outcome"]
                    try:
                        bench = benchmark_return(o["first_buy"], o["last_sell"], offline=args.offline)
                    except Exception as exc:  # network is optional
                        bench = {"index": "WIG", "return_pct": None, "note": "unavailable: {}".format(exc)}
                    o["benchmark"] = bench
                    if bench.get("return_pct") is not None and o["return_pct"] is not None:
                        o["excess_return_pct"] = round(o["return_pct"] - bench["return_pct"], 3)
            elif args.cmd == "show":
                print(json.dumps(th, ensure_ascii=False, indent=2) if args.json else render_thesis(th, lang))
                return 0
            elif args.cmd == "list":
                items = [x for x in load_all(state) if not args.status or x["status"] == args.status]
                print(json.dumps(items, ensure_ascii=False, indent=2) if args.json else render_list(items, lang))
                return 0
            elif args.cmd == "review-due":
                as_of = args.as_of or today
                items = review_due(load_all(state), as_of)
                print(json.dumps(items, ensure_ascii=False, indent=2) if args.json
                      else render_list(items, lang, "due_title", d=as_of.isoformat()))
                return 0
        path = save(state, th)
        print(json.dumps(th, ensure_ascii=False, indent=2) if args.json else render_thesis(th, lang))
        print("saved: {}".format(path), file=sys.stderr)
        return 0
    except JournalError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print("error: {}: {}".format(type(exc).__name__, exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
