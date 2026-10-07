"""R19.13 FIX 4 (reviewer audit fix-first 1): decision_ledger write failures
were SILENT — the coder tape-recorder gap (lane firing, ledger empty for
hours) was only found by manual diffing. Now WARN-logged on both failure
paths: store unavailable (conn None) and insert exception.
"""
import logging

import pytest

from hermes_router import decision as D


def test_ledger_write_warns_on_unusable_store(monkeypatch, tmp_path):
    monkeypatch.setattr(D, "_ledger_connect", lambda db_path="": None)
    recs = []
    handler = logging.Handler()
    handler.emit = lambda r: recs.append(r)
    lg = logging.getLogger("hermes_router.decision")
    lg.addHandler(handler)
    try:
        rid = D.ledger_write({"session_id": "s", "task_id": "t",
                              "trigger": "manual"}, db_path=str(tmp_path))
    finally:
        lg.removeHandler(handler)
    assert rid is None
    assert any(r.levelno == logging.WARNING and "decision_ledger" in r.message
               for r in recs), "store-unavailable failure must WARN"


def test_ledger_write_warns_on_insert_exception(monkeypatch, tmp_path):
    class _BoomConn:
        def execute(self, *a, **k):
            raise RuntimeError("disk I/O error")

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(D, "_ledger_connect", lambda db_path="": _BoomConn())
    recs = []
    handler = logging.Handler()
    handler.emit = lambda r: recs.append(r)
    lg = logging.getLogger("hermes_router.decision")
    lg.addHandler(handler)
    try:
        rid = D.ledger_write({"session_id": "s", "task_id": "t",
                              "trigger": "manual"}, db_path=str(tmp_path))
    finally:
        lg.removeHandler(handler)
    assert rid is None
    assert any(r.levelno == logging.WARNING and "disk I/O error" in r.message
               for r in recs), "insert exception must WARN with the reason"


def test_ledger_write_success_stays_quiet(monkeypatch, tmp_path):
    """Happy path must not start warning."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE decision_ledger (id INTEGER PRIMARY KEY, ts REAL,"
                 " session_id TEXT, task_id TEXT, trigger TEXT, trigger_kind TEXT,"
                 " fork_class TEXT, options_hash TEXT, model TEXT, model_version TEXT,"
                 " choice TEXT, confidence REAL, fail_open_reason TEXT,"
                 " actual_choice TEXT, outcome TEXT, verdict_json TEXT,"
                 " envelope_hash TEXT, follow_verdict INTEGER DEFAULT 0,"
                 " delta_source TEXT, fork_signature TEXT, midturn_mode TEXT,"
                 " envelope_ids TEXT, tool_name TEXT, seam TEXT)")
    recs = []
    handler = logging.Handler()
    handler.emit = lambda r: recs.append(r)
    lg = logging.getLogger("hermes_router.decision")
    lg.addHandler(handler)
    try:
        rid = D.ledger_write({"session_id": "s", "task_id": "t",
                              "trigger": "manual"})
    finally:
        lg.removeHandler(handler)
        conn.close()
    assert rid is not None
    assert not any(r.levelno >= logging.WARNING for r in recs)
