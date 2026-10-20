"""R34-10: session turn-lease release (10a) + stale preemption (10b).

Live blackout: a hung turn held `session_turn_leases` and kept renewing
`expires_at` (+300s) for ~30 min while no messages were written; three
dispatches sat unprocessed and the session looked idle.

10a: turn end (success / exception / abort / empty final) releases the row
     and the heartbeat has nothing left to renew.
10b: a held lease with no message for 2x the renewal interval logs
     `turn_lease_stale holder=... idle_s=...` and is preempted — fail-open,
     never blocks the new turn.
"""
import os
import sqlite3
import sys
import time

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router import session_lease as SL  # noqa: E402

SID = "api_1792530475_test"


def _db(tmp_path, last_msg_ts=None, holder="pid=1:turn=api_1792530475_test"):
    p = str(tmp_path / "state.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE messages (session_id TEXT, timestamp REAL)")
    c.execute("""CREATE TABLE session_turn_leases (
        conversation_id TEXT PRIMARY KEY,
        holder TEXT NOT NULL,
        acquired_at REAL NOT NULL,
        expires_at REAL NOT NULL)""")
    now = time.time()
    c.execute("INSERT INTO session_turn_leases VALUES (?,?,?,?)",
              (SID, holder, now - 900.0, now + 300.0))
    if last_msg_ts is not None:
        c.execute("INSERT INTO messages VALUES (?,?)", (SID, last_msg_ts))
    c.commit()
    c.close()
    return p


def _rows(db):
    c = sqlite3.connect(db)
    try:
        return c.execute("SELECT * FROM session_turn_leases").fetchall()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# 10a — release
# ---------------------------------------------------------------------------

def test_release_on_turn_end_removes_lease_and_is_idempotent(tmp_path):
    db = _db(tmp_path, last_msg_ts=time.time())
    events = []
    assert _rows(db), "precondition: lease held"
    assert SL.release(SID, db_path=db, log=lambda e, **kw: events.append((e, kw)),
                      outcome="success") is True
    assert _rows(db) == []
    assert events and events[0][0] == "turn_lease_released"
    assert events[0][1]["outcome"] == "success"
    # idempotent: a second unwind is a no-op
    assert SL.release(SID, db_path=db, log=lambda e, **kw: events.append((e, kw))) is False


def test_release_covers_exception_abort_and_empty_final(tmp_path):
    for outcome in ("exception", "abort", "empty_final"):
        (tmp_path / outcome).mkdir()
        db = _db(tmp_path / outcome, last_msg_ts=time.time())
        events = []
        SL.release(SID, db_path=db,
                   log=lambda e, **kw: events.append((e, kw)), outcome=outcome)
        assert _rows(db) == [], outcome
        assert events[0][1]["outcome"] == outcome


def test_release_never_raises_on_missing_db(tmp_path):
    assert SL.release(SID, db_path=str(tmp_path / "nope.db")) is False


# ---------------------------------------------------------------------------
# 10b — stale preemption
# ---------------------------------------------------------------------------

def test_stale_lease_is_logged_and_preempted(tmp_path):
    now = 1_900_000_000.0
    db = _db(tmp_path, last_msg_ts=now - 1000.0)
    events = []
    preempted = SL.preempt_if_stale(
        SID, db_path=db, now=now,
        log=lambda e, **kw: events.append((e, kw)))
    assert preempted is True
    assert _rows(db) == []
    stale = [kw for e, kw in events if e == "turn_lease_stale"]
    assert stale, events
    assert "holder" in stale[0] and "idle_s" in stale[0]
    assert int(stale[0]["idle_s"]) >= 600


def test_fresh_lease_is_not_preempted(tmp_path):
    now = 1_900_000_000.0
    db = _db(tmp_path, last_msg_ts=now - 30.0)
    events = []
    assert SL.preempt_if_stale(SID, db_path=db, now=now,
                               log=lambda e, **kw: events.append((e, kw))) is False
    assert len(_rows(db)) == 1
    assert not [e for e, _ in events if e == "turn_lease_stale"]


def test_idle_clock_falls_back_to_acquired_at_without_messages(tmp_path):
    now = 1_900_000_000.0
    db = _db(tmp_path, last_msg_ts=None)  # no messages at all
    events = []
    assert SL.preempt_if_stale(SID, db_path=db, now=now,
                               log=lambda e, **kw: events.append((e, kw))) is True
    assert _rows(db) == []
    assert events


def test_no_lease_is_a_noop(tmp_path):
    db = _db(tmp_path, last_msg_ts=time.time())
    SL.release(SID, db_path=db)
    assert SL.preempt_if_stale(SID, db_path=db) is False
