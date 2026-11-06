"""M45-23: a compaction pass must never persist the same content twice.

Drives ``hermes_state.SessionDB.archive_and_compact`` — the write path behind
``agent/context_compressor.py::_sync_micro_compact_to_db``, which every
micro-compaction pass calls with the live transcript — twice with the SAME
message set and asserts the durable row count does not grow.

Before the fix, the second pass soft-archived the active rows and re-inserted
every message as a fresh row with the same timestamp and identical content, so
one turn's content lived twice in state.db under two ids. Measured on live
state.db files (assistant rows only): coder 15,086 extra copies, evol 16,088,
conductor 14,158 — 7,386 of coder's 7,408 duplicate groups byte-identical.

Run against the live module (expects FAIL before the fix):
    python3 -m pytest tests/test_m45_23_persist_idempotent.py -q
Run against a patched copy of hermes_state.py (expects PASS after):
    M45_23_STATE_DIR=/opt/data/tmp/m45-23/mod python3 -m pytest \
        tests/test_m45_23_persist_idempotent.py -q
"""
import importlib.util
import os
import sys

import pytest

_LIVE_DIR = "/opt/hermes"
if _LIVE_DIR not in sys.path:
    sys.path.insert(0, _LIVE_DIR)


def _load_state_module():
    state_dir = os.environ.get("M45_23_STATE_DIR", _LIVE_DIR)
    path = os.path.join(state_dir, "hermes_state.py")
    spec = importlib.util.spec_from_file_location("hermes_state_m4523", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def db(tmp_path):
    state = _load_state_module()
    return state.SessionDB(db_path=tmp_path / "state.db")


def _seed(db):
    """One turn: user, assistant, tool, assistant — fixed timestamps."""
    sid = db.create_session("sess_m4523", source="test")
    rows = [
        ("user", "run the check", None, 1793943548.73556),
        ("assistant", "checking now", None, 1793943549.07),
        ("tool", "ok: 3 items", "call_1", 1793943555.62),
        ("assistant", "all clear", None, 1793943561.10),
    ]
    for role, content, tool_call_id, ts in rows:
        db.append_message(
            sid, role, content=content, tool_call_id=tool_call_id, timestamp=ts
        )
    return sid


def _active_set(db, sid):
    """The message dicts a compaction pass would re-supply, from the DB."""
    return [
        {
            "role": role,
            "content": content,
            "tool_call_id": tool_call_id,
            "timestamp": ts,
        }
        for role, content, tool_call_id, ts in db._conn.execute(
            "SELECT role, content, tool_call_id, timestamp FROM messages "
            "WHERE session_id = ? AND active = 1 ORDER BY id",
            (sid,),
        )
    ]


def _row_stats(db, sid):
    total = db._conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ?", (sid,)
    ).fetchone()[0]
    active = db._conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ? AND active = 1", (sid,)
    ).fetchone()[0]
    ids = [r[0] for r in db._conn.execute(
        "SELECT id FROM messages WHERE session_id = ? AND active = 1 ORDER BY id",
        (sid,),
    )]
    return total, active, ids


def test_repeated_compaction_of_an_identical_set_adds_no_rows(db):
    sid = _seed(db)
    compacted = _active_set(db, sid)
    assert len(compacted) == 4

    db.archive_and_compact(sid, compacted)
    after_first = _row_stats(db, sid)

    # Same content set again — the shape every micro-compaction pass has when
    # the pass rewrote nothing, or a defrag that only touched the summary row.
    db.archive_and_compact(sid, [dict(m) for m in compacted])
    after_second = _row_stats(db, sid)

    assert after_second[0] == after_first[0], (
        "compaction re-inserted already-persisted rows: "
        f"{after_first[0]} rows after the first pass, {after_second[0]} after "
        "an identical second pass"
    )
    assert after_second[1] == len(compacted)
    assert after_second[2] == after_first[2], "active row ids moved on a no-op pass"


def test_no_duplicate_content_rows_are_active(db):
    sid = _seed(db)
    compacted = _active_set(db, sid)
    db.archive_and_compact(sid, compacted)
    db.archive_and_compact(sid, [dict(m) for m in compacted])

    dupes = db._conn.execute(
        "SELECT role, timestamp, content, COUNT(*) c FROM messages "
        "WHERE session_id = ? AND active = 1 "
        "GROUP BY role, timestamp, content HAVING c > 1",
        (sid,),
    ).fetchall()
    assert dupes == []


def test_a_changed_message_still_persists(db):
    """The fix must not stop a genuine rewrite (delivered body lands)."""
    sid = _seed(db)
    compacted = _active_set(db, sid)
    db.archive_and_compact(sid, compacted)

    changed = [dict(m) for m in compacted]
    changed[-1]["content"] = "all clear — banner delivered"
    db.archive_and_compact(sid, changed)

    active = [
        r[0] for r in db._conn.execute(
            "SELECT content FROM messages WHERE session_id = ? AND active = 1 "
            "ORDER BY id",
            (sid,),
        )
    ]
    assert active[-1] == "all clear — banner delivered"
    assert len(active) == 4
