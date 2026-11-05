"""M42-08 pin: fork_class carries a sentinel for non-fork rows, stays empty
for a fork whose class the caller omitted.

The two states '' used to stand for were conflated:

* "this row is not a fork"      -> now stored as "not_a_fork"
* "this row is a fork, class missing" -> stays "" so the gap is visible

Both rows go through the same public writer and are read back from the table,
so the assertion is about what landed in decision_ledger, not about the dict
that was passed in.
"""
from __future__ import annotations

import sqlite3

from hermes_router import decision as d


def _fork_class(db_path, row_id):
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT fork_class FROM decision_ledger WHERE id = ?", (row_id,))
        return cur.fetchone()[0]
    finally:
        conn.close()


def test_non_fork_row_stores_not_a_fork(tmp_path):
    """pre_cooldown is a suppression, not a fork: it must be labelled."""
    db_path = str(tmp_path / "ledger.db")
    row_id = d.ledger_write({"session_id": "s", "task_id": "t",
                             "trigger": "pre_cooldown"}, db_path=db_path)
    assert row_id, "writer returned no row id"
    assert _fork_class(db_path, row_id) == "not_a_fork"


def test_fork_gap_stays_empty(tmp_path):
    """midturn IS a fork: a missing class must stay an empty, visible gap."""
    db_path = str(tmp_path / "ledger.db")
    row_id = d.ledger_write({"session_id": "s", "task_id": "t",
                             "trigger": "midturn"}, db_path=db_path)
    assert row_id, "writer returned no row id"
    assert _fork_class(db_path, row_id) == ""
