"""M42-03 pin: the decision_ledger bounded store evicts at the CAP boundary.

Measured contract (decision.py:4794-4806):

* count <  cap  -> nothing evicted, every row survives (no pre-emptive trim)
* count == cap  -> nothing evicted, the table holds exactly cap rows
* count == cap+1-> exactly ONE row evicted, the OLDEST id, count returns to cap
* after an external delete puts the table back under cap, the next write does
  not re-trim below cap and the caller's new row survives

The cap is driven through the writer's documented `_cap` hook (min 10), so the
boundary is exercised on ten rows instead of five thousand.
"""
from __future__ import annotations

import sqlite3

from hermes_router import decision as d

CAP = 10


def _ids(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT id FROM decision_ledger ORDER BY id")]
    finally:
        conn.close()


def _count(db_path):
    return len(_ids(db_path))


def _write(db_path, n, cap=CAP):
    rid = d.ledger_write({"session_id": "s%d" % n, "task_id": "t%d" % n,
                          "_cap": cap}, db_path=db_path)
    assert rid, "writer returned no row id at write %d" % n
    assert rid in _ids(db_path), "caller's row %r was evicted by its own insert" % rid
    return rid


def test_cap_minus_one_trims_nothing(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    for n in range(1, CAP):                      # cap-1 rows
        _write(db_path, n)
    assert _count(db_path) == CAP - 1
    assert _ids(db_path) == list(range(1, CAP))


def test_exactly_at_cap_trims_nothing(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    for n in range(1, CAP + 1):                  # count == cap
        _write(db_path, n)
    assert _count(db_path) == CAP
    assert _ids(db_path) == list(range(1, CAP + 1))


def test_cap_plus_one_evicts_the_oldest_only(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    for n in range(1, CAP + 2):                  # cap+1 rows
        rid = _write(db_path, n)
    assert _count(db_path) == CAP, "table left above the cap"
    assert _ids(db_path) == list(range(2, CAP + 2)), "wrong rows evicted"
    assert rid == CAP + 1 and rid in _ids(db_path), "newest row lost"
    assert 1 not in _ids(db_path), "oldest row survived eviction"


def test_a_failed_eviction_is_never_silent(tmp_path, monkeypatch, caplog):
    """The eviction is best-effort: when the DELETE cannot run the table stays
    at cap+1. That is only acceptable if the gap is diagnosable from the log —
    a silently oversized store is a dead instrument (SOUL: no silent death)."""
    db_path = str(tmp_path / "ledger.db")
    for n in range(1, CAP + 1):                  # fill to cap
        _write(db_path, n)

    real_connect = d._ledger_connect

    class _NoDelete:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            if "DELETE FROM decision_ledger" in sql:
                raise sqlite3.OperationalError("database is locked")
            return self._conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    with monkeypatch.context() as m:
        m.setattr(d, "_ledger_connect",
                  lambda db_path="": _NoDelete(real_connect(db_path)))
        with caplog.at_level("WARNING"):
            rid = d.ledger_write({"session_id": "boom", "_cap": CAP},
                                 db_path=db_path)

    assert rid and rid in _ids(db_path), "the insert itself must still land"
    assert _count(db_path) == CAP + 1, "precondition: eviction could not run"
    assert any("evict" in r.message.lower() or "evict" in str(r.msg).lower()
               for r in caplog.records), \
        "an eviction that did not run left cap+1 rows with no log line"


def test_after_delete_back_under_cap_the_next_write_does_not_over_trim(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    for n in range(1, CAP + 2):
        _write(db_path, n)
    # external delete (maintenance) puts the table at cap-1
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM decision_ledger WHERE id = 2")
        conn.commit()
    finally:
        conn.close()
    assert _count(db_path) == CAP - 1 and _ids(db_path) == list(range(3, CAP + 2))

    rid = _write(db_path, CAP + 2)               # ladder is now full again
    assert _count(db_path) == CAP, "write after a dip left the table short or long"
    assert _ids(db_path) == list(range(3, CAP + 3))
    assert rid in _ids(db_path)
