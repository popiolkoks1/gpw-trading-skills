import datetime as dt
import json
import os

import pytest

import watchlist_state as ws

D = dt.date(2026, 11, 14)


def _item(stage="candidate", **kw):
    it = ws.new_item("KETY", D, stage)
    it.update(kw)
    return it


# ---------------------------------------------------------------- items

def test_new_item_contract_fields():
    it = ws.new_item("kety", D)
    for k in ("ticker", "stage", "added", "updated", "source", "sources", "pivot", "stop", "target", "thesis_id",
              "tags", "notes", "alerts", "history"):
        assert k in it
    assert it["ticker"] == "KETY" and it["stage"] == "candidate" and it["source"] == "user"
    assert it["history"][0]["to"] == "candidate"


@pytest.mark.parametrize("bad", ["", "KE TY", "x" * 21, "PKO/BP", None])
def test_invalid_ticker(bad):
    with pytest.raises(ws.WatchlistError):
        ws.new_item(bad, D)


@pytest.mark.parametrize("stage", ["entered", "dropped", "bogus"])
def test_new_item_cannot_start_entered_or_dropped(stage):
    with pytest.raises(ws.WatchlistError):
        ws.new_item("KETY", D, stage)


# ---------------------------------------------------------------- stage machine

ALL = ws.STAGES


@pytest.mark.parametrize("frm", ALL)
@pytest.mark.parametrize("to", ALL)
def test_transition_matrix(frm, to):
    assert ws.can_transition(frm, to) == (to in ws.TRANSITIONS[frm] and frm != to)


def test_auto_transitions_only_candidate_to_watch():
    assert ws.can_transition("candidate", "watch", auto=True)
    for frm, to in [("candidate", "ready"), ("watch", "ready"), ("dropped", "candidate"), ("watch", "candidate")]:
        assert not ws.can_transition(frm, to, auto=True)
    with pytest.raises(ws.WatchlistError):
        ws.transition(_item("watch", pivot=10.0, stop=9.0), "ready", D, "x", auto=True)


def test_ready_requires_pivot_and_stop():
    it = _item("watch")
    with pytest.raises(ws.WatchlistError, match="pivot"):
        ws.transition(it, "ready", D, "")
    ws.set_levels(it, D, pivot=100, stop=95)
    e = ws.transition(it, "ready", D, "plan")
    assert it["stage"] == "ready" and e["from"] == "watch" and it["history"][-1]["to"] == "ready"


def test_drop_requires_reason_and_reopen():
    it = _item("watch")
    with pytest.raises(ws.WatchlistError, match="reason"):
        ws.transition(it, "dropped", D, "  ")
    ws.transition(it, "dropped", D, "broke support")
    with pytest.raises(ws.WatchlistError):
        ws.transition(it, "ready", D, "x")  # dropped -> ready not allowed directly
    ws.transition(it, "candidate", D, "re-added")
    assert it["stage"] == "candidate"


def test_same_stage_and_unknown_stage_rejected():
    it = _item("watch")
    with pytest.raises(ws.WatchlistError):
        ws.transition(it, "watch", D, "")
    with pytest.raises(ws.WatchlistError):
        ws.transition(it, "bought", D, "")


@pytest.mark.parametrize("p,s,t", [(100, 100, None), (100, 101, None), (100, 95, 99), (-1, None, None), (0, None, None)])
def test_check_levels_rejects(p, s, t):
    with pytest.raises(ws.WatchlistError):
        ws.check_levels(p, s, t)


def test_set_levels_change_detection():
    it = _item()
    assert ws.set_levels(it, D, pivot=10, stop=9, by="user")
    assert it["levels_source"] == "user"
    assert not ws.set_levels(it, D, pivot=10)


def test_tags_sources_alerts_dedup():
    it = _item()
    assert ws.add_tag(it, "VCP", skill="gpw-trend-screener", date="2026-11-13", mode="screen")
    assert not ws.add_tag(it, "vcp", skill="gpw-trend-screener", date="2026-11-14")
    assert it["tag_meta"]["vcp"]["date"] == "2026-11-14"
    ws.add_tag(it, "mine")
    assert it["tag_meta"]["mine"]["origin"] == "user"
    assert not ws.add_tag(it, "mine", skill="x")  # user tag stays user-owned
    assert it["tag_meta"]["mine"]["origin"] == "user"
    src = {"skill": "s", "kind": "k", "date": "2026-11-13"}
    assert ws.add_source(it, dict(src)) and not ws.add_source(it, dict(src))
    al = {"code": "c", "date": "2026-11-13", "origin": "ingest"}
    assert ws.add_alert(it, dict(al)) and not ws.add_alert(it, dict(al))


def test_notes():
    it = _item()
    ws.add_note(it, D, "  base forming ")
    assert it["notes"] == [{"date": "2026-11-14", "text": "base forming"}]
    with pytest.raises(ws.WatchlistError):
        ws.add_note(it, D, " ")


# ---------------------------------------------------------------- journal sync

def _thesis(tid, ticker, status, created="2026-11-01T10:00:00+00:00", stop=None):
    return {"id": tid, "ticker": ticker, "status": status, "created_at": created,
            "status_history": [{"status": status, "date": "2026-11-10"}], "plan": {"stop": stop}}


def test_sync_active_thesis_enters_once():
    st = ws.empty_state()
    st["items"].append(_item("watch"))
    th = [_thesis("th_kety_1", "KETY", "ACTIVE")]
    ch = ws.sync_journal(st, th, D)
    it = st["items"][0]
    assert it["stage"] == "entered" and it["thesis_id"] == "th_kety_1" and it["thesis_status"] == "ACTIVE"
    assert any(c["change"] == "stage" and c["to"] == "entered" for c in ch)
    assert it["history"][-1]["by"] == "gpw-trade-journal"
    ws.transition(it, "watch", D, "user moved back")
    assert ws.sync_journal(st, th, D) == []  # same thesis does not pull it back
    assert it["stage"] == "watch"


def test_sync_links_idea_without_stage_change_and_mirrors_status():
    st = ws.empty_state()
    st["items"].append(_item("candidate"))
    th = [_thesis("old", "KETY", "IDEA", created="2026-10-01"), _thesis("new", "KETY", "READY", created="2026-11-01")]
    ws.sync_journal(st, th, D)
    it = st["items"][0]
    assert it["stage"] == "candidate" and it["thesis_id"] == "new" and it["thesis_status"] == "READY"
    th[1]["status"] = "INVALIDATED"
    ch = ws.sync_journal(st, th, D)
    assert it["thesis_status"] == "INVALIDATED" and ch[-1]["change"] == "thesis_status"


def test_sync_dropped_item_with_active_thesis_is_not_entered():
    st = ws.empty_state()
    it = _item("watch")
    ws.transition(it, "dropped", D, "no")
    st["items"].append(it)
    ws.sync_journal(st, [_thesis("a", "KETY", "ACTIVE")], D)
    assert it["stage"] == "dropped"  # dropped -> entered is not an allowed transition


# ---------------------------------------------------------------- storage

def test_save_load_roundtrip_atomic_backup(tmp_path):
    st = ws.empty_state()
    st["items"].append(_item("watch"))
    r1 = ws.save_state(st, tmp_path)
    assert r1["written"] and r1["backup"] is None
    loaded, msgs = ws.load_state(tmp_path, D)
    assert msgs == [] and loaded["items"][0]["ticker"] == "KETY"
    assert loaded["schema_version"] == ws.SCHEMA_VERSION and loaded["skill"] == "gpw-watchlist"
    r2 = ws.save_state(loaded, tmp_path)
    assert not r2["written"]  # unchanged -> no write, no backup
    ws.add_note(loaded["items"][0], D, "x")
    r3 = ws.save_state(loaded, tmp_path)
    assert r3["written"] and r3["backup"] and os.path.exists(r3["backup"])
    assert not list(tmp_path.glob("*.tmp*")) and not (tmp_path / ws.LOCK_FILE).exists()
    prev = json.loads(open(r3["backup"], encoding="utf-8").read())
    assert prev["items"][0]["notes"] == []


def test_backup_rotation(tmp_path):
    st = ws.empty_state()
    st["items"].append(_item())
    ws.save_state(st, tmp_path)
    for i in range(ws.MAX_BACKUPS + 4):
        ws.add_note(st["items"][0], D, "n{}".format(i))
        ws.save_state(st, tmp_path)
    assert len(list((tmp_path / ws.BACKUP_DIR).glob("watchlist_*.json"))) == ws.MAX_BACKUPS


def test_load_missing_and_corrupt(tmp_path):
    st, msgs = ws.load_state(tmp_path / "nope", D)
    assert st["items"] == [] and msgs == []
    (tmp_path / ws.STATE_FILE).write_text("{not json", encoding="utf-8")
    with pytest.raises(ws.WatchlistError, match="cannot read"):
        ws.load_state(tmp_path, D)


def test_newer_schema_refused(tmp_path):
    (tmp_path / ws.STATE_FILE).write_text(json.dumps({"schema_version": "2.0", "items": []}), encoding="utf-8")
    with pytest.raises(ws.WatchlistError, match="newer"):
        ws.load_state(tmp_path, D)


def test_stale_lock_is_broken(tmp_path):
    lock = tmp_path / ws.LOCK_FILE
    lock.write_text("123", encoding="utf-8")
    old = lock.stat().st_mtime - ws.LOCK_STALE_SECONDS - 5
    os.utime(str(lock), (old, old))
    assert ws.save_state(ws.empty_state(), tmp_path)["written"]


def test_migrate_legacy_shapes():
    st, msgs = ws.migrate(["pkobp", "KETY", "bad ticker!"], D)
    assert [i["ticker"] for i in st["items"]] == ["PKOBP", "KETY"]
    assert any("schema 0" in m for m in msgs) and any("skipped" in m for m in msgs)
    legacy = {"items": [{"ticker": "CDPROJEKT", "stage": "ready", "added": "2026-10-01", "source": "gpw-trend-screener",
                         "pivot": 300, "stop": 280, "notes": ["old note"]},
                        {"ticker": "cdprojekt", "notes": ["dup"]}]}
    st, msgs = ws.migrate(legacy, D)
    it = st["items"][0]
    assert len(st["items"]) == 1 and any("duplicate" in m for m in msgs)
    assert it["stage"] == "ready" and it["pivot"] == 300.0 and it["added"] == "2026-10-01"
    assert it["notes"][0] == {"date": "2026-10-01", "text": "old note"} and it["notes"][-1]["text"] == "dup"
    assert it["history"][0]["reason"] == "migrated"
    assert st["schema_version"] == ws.SCHEMA_VERSION


def test_state_readable_by_event_calendar_reader(tmp_path):
    """gpw-event-calendar reads items[].ticker/stage directly (read_watchlist)."""
    st = ws.empty_state()
    a, b = _item("watch"), ws.new_item("PKOBP", D)
    ws.transition(b, "dropped", D, "x")
    st["items"] += [a, b]
    ws.save_state(st, tmp_path)
    data = json.loads((tmp_path / "watchlist.json").read_text(encoding="utf-8"))
    tickers = [i["ticker"] for i in data["items"] if i.get("stage") != "dropped"]
    assert tickers == ["KETY"]
