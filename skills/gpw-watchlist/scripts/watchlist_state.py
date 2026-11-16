"""gpw-watchlist state: schema, stage machine, atomic storage, migration.

The state file (default ``~/.gpw-trading-skills/watchlist.json``) is a
contract read by other skills (gpw-event-calendar, gpw-research-dossier,
gpw-daily-brief, gpw-pre-trade-gate). Its schema is documented in
``references/watchlist-contract.md``; keep both in sync and bump
``SCHEMA_VERSION`` (with a migration in :func:`migrate`) for any breaking
change.

Everything here is pure except :func:`load_state` / :func:`save_state`.
Functions take ``today`` (a ``datetime.date``) so tests are deterministic.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

SKILL = "gpw-watchlist"
SCHEMA_VERSION = "1.0"
STATE_FILE = "watchlist.json"
BACKUP_DIR = "watchlist_backups"
# Rolling backups: enough to undo a bad ingest or a mistaken bulk drop from the
# last couple of days of daily use, small enough not to clutter the folder.
MAX_BACKUPS = 10
LOCK_FILE = "watchlist.lock"
# A writer holds the lock for milliseconds; a lock older than this is left
# over from a crashed process and may be broken.
LOCK_STALE_SECONDS = 60

STAGES = ["candidate", "watch", "ready", "entered", "dropped"]
ACTIVE_STAGES = ("candidate", "watch", "ready", "entered")
STAGE_ORDER = {s: i for i, s in enumerate(STAGES)}

# Allowed transitions (from -> set of to). See references/watchlist-contract.md.
#   candidate: raw idea from a screener / user
#   watch:     worth following, setup forming
#   ready:     pivot + stop defined, written plan - could be acted on
#   entered:   a gpw-trade-journal thesis for the ticker is ACTIVE (position open)
#   dropped:   no longer followed (kept for history)
TRANSITIONS: Dict[str, set] = {
    "candidate": {"watch", "ready", "entered", "dropped"},
    "watch": {"candidate", "ready", "entered", "dropped"},
    "ready": {"watch", "entered", "dropped"},
    "entered": {"watch", "dropped"},   # after the position is closed in the journal
    "dropped": {"candidate", "watch"},  # re-opened by the user only
}
# Stage changes an automatic process (ingest) may make. Never "ready": the
# move to ready needs a human-defined pivot, stop and plan.
AUTO_TRANSITIONS = {("candidate", "watch")}

# Caps keep the file small enough to be read by every skill on every run.
MAX_SOURCES_PER_ITEM = 50
MAX_HISTORY_PER_ITEM = 200
MAX_NOTES_PER_ITEM = 200
MAX_REMOVED_LOG = 200

TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,20}$")


class WatchlistError(ValueError):
    """Invalid user input or forbidden state change (exit code 1)."""


# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------

def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def empty_state() -> Dict:
    ts = now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "skill": SKILL,
        "created_at": ts,
        "updated_at": ts,
        "last_refresh": None,
        "last_ingest": None,
        "items": [],
        "removed": [],
    }


def norm_ticker(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if not TICKER_RE.match(t):
        raise WatchlistError("invalid ticker {!r} (GPW short name, e.g. PKOBP, CDPROJEKT)".format(ticker))
    return t


def new_item(ticker: str, today: dt.date, stage: str = "candidate", source: str = "user") -> Dict:
    if stage not in STAGES or stage in ("entered", "dropped"):
        raise WatchlistError("a new item starts as candidate, watch or ready, not {!r}".format(stage))
    d = today.isoformat()
    return {
        "ticker": norm_ticker(ticker),
        "stage": stage,
        "added": d,
        "updated": d,
        "source": source,
        "sources": [],
        "pivot": None,
        "stop": None,
        "target": None,
        "levels_source": None,
        "levels_date": None,
        "thesis_id": None,
        "thesis_status": None,
        "tags": [],
        "tag_meta": {},
        "notes": [],
        "alerts": [],
        "history": [{"date": d, "from": None, "to": stage, "reason": "added", "by": source}],
        "market": None,
        "screener": None,
        "upcoming_events": [],
    }


def find_item(state: Dict, ticker: str) -> Optional[Dict]:
    t = (ticker or "").strip().upper()
    for it in state["items"]:
        if it["ticker"] == t:
            return it
    return None


def require_item(state: Dict, ticker: str) -> Dict:
    it = find_item(state, ticker)
    if it is None:
        raise WatchlistError("{} is not on the watchlist".format(ticker.upper()))
    return it


# --------------------------------------------------------------------------
# Stage machine
# --------------------------------------------------------------------------

def check_levels(pivot: Optional[float], stop: Optional[float], target: Optional[float] = None) -> None:
    for name, v in (("pivot", pivot), ("stop", stop), ("target", target)):
        if v is not None and (not isinstance(v, (int, float)) or v != v or v <= 0):
            raise WatchlistError("{} must be a positive price, got {!r}".format(name, v))
    if pivot is not None and stop is not None and stop >= pivot:
        raise WatchlistError("stop ({}) must be below the pivot ({}) for a long setup".format(stop, pivot))
    if pivot is not None and target is not None and target <= pivot:
        raise WatchlistError("target ({}) must be above the pivot ({})".format(target, pivot))


def can_transition(frm: str, to: str, auto: bool = False) -> bool:
    if frm == to or to not in TRANSITIONS.get(frm, set()):
        return False
    if auto and (frm, to) not in AUTO_TRANSITIONS:
        return False
    return True


def transition(item: Dict, to: str, today: dt.date, reason: str, by: str = "user",
               thesis_id: Optional[str] = None, auto: bool = False) -> Dict:
    """Move ``item`` to stage ``to``; raises WatchlistError when not allowed.

    Preconditions: ``ready`` needs pivot and stop (stop < pivot); a reason is
    mandatory for ``dropped``.
    """
    frm = item["stage"]
    if to not in STAGES:
        raise WatchlistError("unknown stage {!r} (allowed: {})".format(to, ", ".join(STAGES)))
    if frm == to:
        raise WatchlistError("{} is already in stage {}".format(item["ticker"], to))
    if not can_transition(frm, to, auto=auto):
        allowed = sorted(TRANSITIONS.get(frm, set()))
        raise WatchlistError("{} -> {} is not allowed for {} (from {} you can go to: {})".format(
            frm, to, item["ticker"], frm, ", ".join(allowed) or "-"))
    if to == "ready":
        if item.get("pivot") is None or item.get("stop") is None:
            raise WatchlistError("ready needs a pivot and a stop: set them with --pivot and --stop")
        check_levels(item["pivot"], item["stop"])
    if to == "dropped" and not (reason or "").strip():
        raise WatchlistError("dropping needs a reason (--reason)")
    entry = {"date": today.isoformat(), "from": frm, "to": to, "reason": reason or "", "by": by}
    if thesis_id:
        entry["thesis_id"] = thesis_id
    item["stage"] = to
    item["history"].append(entry)
    del item["history"][:-MAX_HISTORY_PER_ITEM]
    item["updated"] = today.isoformat()
    return entry


# --------------------------------------------------------------------------
# Item helpers
# --------------------------------------------------------------------------

def add_note(item: Dict, today: dt.date, text: str) -> None:
    text = (text or "").strip()
    if not text:
        raise WatchlistError("empty note")
    item["notes"].append({"date": today.isoformat(), "text": text[:2000]})
    del item["notes"][:-MAX_NOTES_PER_ITEM]
    item["updated"] = today.isoformat()


def set_levels(item: Dict, today: dt.date, pivot=None, stop=None, target=None, by: str = "user") -> bool:
    """Set the given levels (None = keep). Returns True when something changed."""
    new_p = item.get("pivot") if pivot is None else round(float(pivot), 4)
    new_s = item.get("stop") if stop is None else round(float(stop), 4)
    new_t = item.get("target") if target is None else round(float(target), 4)
    check_levels(new_p, new_s, new_t)
    if (new_p, new_s, new_t) == (item.get("pivot"), item.get("stop"), item.get("target")):
        return False
    item["pivot"], item["stop"], item["target"] = new_p, new_s, new_t
    item["levels_source"] = by
    item["levels_date"] = today.isoformat()
    item["updated"] = today.isoformat()
    return True


def add_tag(item: Dict, tag: str, skill: Optional[str] = None, date: Optional[str] = None,
            mode: Optional[str] = None) -> bool:
    """Add a tag. ``skill`` given -> automatic tag owned by that producer."""
    tag = (tag or "").strip().lower()
    if not tag:
        return False
    meta = item.setdefault("tag_meta", {})
    changed = tag not in item["tags"]
    if changed:
        item["tags"].append(tag)
    if skill:
        old = meta.get(tag)
        if old is None or old.get("origin") == "auto":
            meta[tag] = {"origin": "auto", "skill": skill, "mode": mode, "date": date}
    else:
        meta[tag] = {"origin": "user", "skill": None, "mode": None, "date": date}
    return changed


def remove_tag(item: Dict, tag: str) -> bool:
    tag = (tag or "").strip().lower()
    if tag not in item["tags"]:
        return False
    item["tags"].remove(tag)
    item.get("tag_meta", {}).pop(tag, None)
    return True


def add_source(item: Dict, src: Dict) -> bool:
    """Append a producer observation; dedup by (skill, kind, date). True if new."""
    key = (src.get("skill"), src.get("kind"), src.get("date"))
    for s in item["sources"]:
        if (s.get("skill"), s.get("kind"), s.get("date")) == key:
            return False
    item["sources"].append(src)
    item["sources"].sort(key=lambda s: (s.get("date") or "", s.get("skill") or ""))
    del item["sources"][:-MAX_SOURCES_PER_ITEM]
    return True


def add_alert(item: Dict, alert: Dict) -> bool:
    """Append an alert; dedup by (code, date, origin). True if new."""
    key = (alert.get("code"), alert.get("date"), alert.get("origin"))
    for a in item["alerts"]:
        if (a.get("code"), a.get("date"), a.get("origin")) == key:
            return False
    item["alerts"].append(alert)
    return True


def summary_by_stage(state: Dict) -> Dict[str, int]:
    out = {s: 0 for s in STAGES}
    for it in state["items"]:
        out[it["stage"]] = out.get(it["stage"], 0) + 1
    return out


def sort_items(items: Iterable[Dict]) -> List[Dict]:
    def key(it):
        sc = (it.get("screener") or {}).get("score")
        return (STAGE_ORDER.get(it["stage"], 99), -(sc or 0), it["ticker"])
    return sorted(items, key=key)


# --------------------------------------------------------------------------
# Journal link (read-only, via gpwlib.journal_io)
# --------------------------------------------------------------------------

_STATUS_PRIORITY = {"ACTIVE": 0, "READY": 1, "IDEA": 2, "CLOSED": 3, "INVALIDATED": 4}


def theses_for(theses: List[Dict], ticker: str, statuses: Optional[Iterable[str]] = None) -> List[Dict]:
    st = set(statuses) if statuses else None
    out = [t for t in theses if isinstance(t, dict) and str(t.get("ticker", "")).upper() == ticker
           and (st is None or t.get("status") in st)]
    out.sort(key=lambda t: str(t.get("created_at") or ""), reverse=True)  # newest first ...
    out.sort(key=lambda t: _STATUS_PRIORITY.get(t.get("status"), 9))     # ... inside each status (stable)
    return out


def _thesis_active_date(th: Dict) -> Optional[str]:
    for h in th.get("status_history") or []:
        if h.get("status") == "ACTIVE":
            return h.get("date")
    return None


def sync_journal(state: Dict, theses: List[Dict], today: dt.date) -> List[Dict]:
    """Mirror the journal into the watchlist.

    * A thesis for the ticker that is ACTIVE -> stage ``entered`` (once per
      thesis: after the user moves the item away again, the same thesis does
      not pull it back), ``thesis_id`` linked.
    * Otherwise an IDEA/READY thesis is linked (``thesis_id``) without a
      stage change.
    * ``thesis_status`` always mirrors the linked thesis (CLOSED /
      INVALIDATED are reported by ``review``).

    Returns a list of change records.
    """
    changes = []
    by_id = {t.get("id"): t for t in theses if isinstance(t, dict)}
    for it in state["items"]:
        active = theses_for(theses, it["ticker"], ["ACTIVE"])
        if active:
            th = active[0]
            already = any(h.get("to") == "entered" and h.get("thesis_id") == th["id"] for h in it["history"])
            if it["stage"] != "entered" and not already and can_transition(it["stage"], "entered"):
                frm = it["stage"]
                transition(it, "entered", today, "journal thesis {} is ACTIVE".format(th["id"]),
                           by="gpw-trade-journal", thesis_id=th["id"])
                changes.append({"ticker": it["ticker"], "change": "stage", "from": frm, "to": "entered",
                                "thesis_id": th["id"]})
            if it.get("thesis_id") != th["id"] and (it["stage"] == "entered" or not it.get("thesis_id")):
                it["thesis_id"] = th["id"]
                changes.append({"ticker": it["ticker"], "change": "thesis_linked", "thesis_id": th["id"]})
        elif not it.get("thesis_id") and it["stage"] in ("candidate", "watch", "ready"):
            open_ = theses_for(theses, it["ticker"], ["IDEA", "READY"])
            if open_:
                it["thesis_id"] = open_[0]["id"]
                changes.append({"ticker": it["ticker"], "change": "thesis_linked", "thesis_id": open_[0]["id"]})
        th = by_id.get(it.get("thesis_id"))
        status = th.get("status") if th else None
        if it.get("thesis_id") and status != it.get("thesis_status"):
            it["thesis_status"] = status
            changes.append({"ticker": it["ticker"], "change": "thesis_status", "status": status})
    return changes


# --------------------------------------------------------------------------
# Migration
# --------------------------------------------------------------------------

def _parse_version(v) -> Tuple[int, int]:
    try:
        a, b = str(v).split(".")[:2]
        return int(a), int(b)
    except (ValueError, AttributeError):
        return (0, 0)


def migrate(data, today: Optional[dt.date] = None) -> Tuple[Dict, List[str]]:
    """Bring any earlier state shape to the current schema.

    Accepted legacy shapes (schema 0, i.e. no ``schema_version``):
    a JSON list of tickers, a list of item dicts, or ``{"items": [...]}``
    with ``source`` as a string and ``notes`` as strings. Returns
    ``(state, messages)``. A state written by a *newer* schema raises
    WatchlistError so this version never overwrites data it does not know.
    """
    today = today or dt.date.today()
    msgs: List[str] = []
    if isinstance(data, list):
        data = {"items": data}
        msgs.append("migrated a bare list to the 1.0 state object")
    if not isinstance(data, dict):
        raise WatchlistError("watchlist state is not a JSON object")
    ver = data.get("schema_version")
    if ver is not None and _parse_version(ver)[0] > _parse_version(SCHEMA_VERSION)[0]:
        raise WatchlistError("watchlist state has schema {} - newer than this skill ({}); update the skill".format(
            ver, SCHEMA_VERSION))
    if ver == SCHEMA_VERSION and isinstance(data.get("items"), list):
        st = data
    else:
        if ver is None:
            msgs.append("migrated schema 0 -> {}".format(SCHEMA_VERSION))
        st = empty_state()
        for k in ("created_at", "last_refresh", "last_ingest"):
            if data.get(k):
                st[k] = data[k]
        st["removed"] = list(data.get("removed") or [])
        st["items"] = list(data.get("items") or [])
    items, seen = [], {}
    for raw in st["items"]:
        it = _migrate_item(raw, today)
        if it is None:
            msgs.append("skipped an item without a valid ticker: {!r}".format(raw)[:200])
            continue
        if it["ticker"] in seen:
            msgs.append("merged duplicate item {}".format(it["ticker"]))
            _merge_into(seen[it["ticker"]], it)
            continue
        seen[it["ticker"]] = it
        items.append(it)
    st["items"] = items
    st["schema_version"] = SCHEMA_VERSION
    st["skill"] = SKILL
    st.setdefault("removed", [])
    st.setdefault("last_refresh", None)
    st.setdefault("last_ingest", None)
    st.setdefault("created_at", now_iso())
    st.setdefault("updated_at", st["created_at"])
    return st, msgs


def _migrate_item(raw, today: dt.date) -> Optional[Dict]:
    if isinstance(raw, str):
        raw = {"ticker": raw}
    if not isinstance(raw, dict):
        return None
    try:
        t = norm_ticker(str(raw.get("ticker") or ""))
    except WatchlistError:
        return None
    stage = raw.get("stage") if raw.get("stage") in STAGES else "candidate"
    added = str(raw.get("added") or today.isoformat())[:10]
    src = raw.get("source") if isinstance(raw.get("source"), str) else "user"
    it = new_item(t, today, "candidate", source=src)
    it.update({k: v for k, v in raw.items() if k in it})
    it["ticker"], it["stage"], it["added"], it["source"] = t, stage, added, src
    it["updated"] = str(raw.get("updated") or added)[:10]
    notes = []
    for n in raw.get("notes") or []:
        if isinstance(n, str):
            notes.append({"date": added, "text": n})
        elif isinstance(n, dict) and n.get("text"):
            notes.append({"date": str(n.get("date") or added)[:10], "text": str(n["text"])})
    it["notes"] = notes
    for key in ("sources", "alerts", "history", "tags"):
        if not isinstance(it.get(key), list):
            it[key] = []
    if not it["history"] or not isinstance(raw.get("history"), list):
        it["history"] = [{"date": added, "from": None, "to": stage, "reason": "migrated", "by": "migration"}]
    if not isinstance(it.get("tag_meta"), dict):
        it["tag_meta"] = {}
    it["tags"] = [str(x).lower() for x in it["tags"] if isinstance(x, str) and x.strip()]
    for tag in it["tags"]:
        it["tag_meta"].setdefault(tag, {"origin": "user", "skill": None, "mode": None, "date": None})
    for k in ("pivot", "stop", "target"):
        v = it.get(k)
        it[k] = float(v) if isinstance(v, (int, float)) and v > 0 else None
    return it


def _merge_into(dst: Dict, src: Dict) -> None:
    for s in src["sources"]:
        add_source(dst, s)
    dst["notes"].extend(src["notes"])
    for t in src["tags"]:
        if t not in dst["tags"]:
            dst["tags"].append(t)


# --------------------------------------------------------------------------
# Storage (atomic, locked, backed up)
# --------------------------------------------------------------------------

def default_state_dir() -> Path:
    env = os.environ.get("GPW_WATCHLIST_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".gpw-trading-skills"


def state_path(state_dir: Optional[Path] = None) -> Path:
    return Path(state_dir or default_state_dir()).expanduser() / STATE_FILE


def load_state(state_dir: Optional[Path] = None, today: Optional[dt.date] = None) -> Tuple[Dict, List[str]]:
    """Load (or create in memory) the state. Corrupt file -> WatchlistError."""
    p = state_path(state_dir)
    if not p.exists():
        return empty_state(), []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise WatchlistError("cannot read {}: {} (backups are in {})".format(p, exc, p.parent / BACKUP_DIR))
    return migrate(data, today)


def _serialize(state: Dict) -> str:
    return json.dumps(state, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


class _Lock:
    def __init__(self, folder: Path):
        self.path = folder / LOCK_FILE
        self.fd = None

    def __enter__(self):
        deadline = time.time() + 10
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > LOCK_STALE_SECONDS:
                        self.path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.time() > deadline:
                    raise WatchlistError("watchlist is locked by another process ({})".format(self.path))
                time.sleep(0.1)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def save_state(state: Dict, state_dir: Optional[Path] = None) -> Dict:
    """Write atomically (temp file + fsync + os.replace) when the content
    changed; keep the previous version in ``watchlist_backups/``.

    Returns ``{"path", "written": bool, "backup": path|None}``.
    """
    p = state_path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    state = copy.deepcopy(state)
    state["schema_version"] = SCHEMA_VERSION
    state["skill"] = SKILL
    state["items"] = sort_items(state["items"])
    state["removed"] = state.get("removed", [])[-MAX_REMOVED_LOG:]
    with _Lock(p.parent):
        old = p.read_text(encoding="utf-8") if p.exists() else None
        if old is not None:
            try:
                prev = json.loads(old)
                state["updated_at"] = prev.get("updated_at", state.get("updated_at"))
            except ValueError:
                prev = None
            if prev is not None and _serialize(prev) == _serialize(state):
                return {"path": str(p), "written": False, "backup": None}
        state["updated_at"] = now_iso()
        backup = None
        if old is not None:
            bdir = p.parent / BACKUP_DIR
            bdir.mkdir(exist_ok=True)
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            backup = bdir / "watchlist_{}.json".format(stamp)
            shutil.copy2(str(p), str(backup))
            olds = sorted(bdir.glob("watchlist_*.json"))
            for f in olds[:-MAX_BACKUPS]:
                try:
                    f.unlink()
                except OSError:
                    pass
        tmp = p.with_name(p.name + ".tmp{}".format(os.getpid()))
        with open(str(tmp), "w", encoding="utf-8") as fh:
            fh.write(_serialize(state))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(p))
    return {"path": str(p), "written": True, "backup": str(backup) if backup else None}
