"""R19.18 (Goran approved): midturn envelope frame starvation — root cause
of Jev's high-confidence misses (reviewer rows 146-148: opt-2 @ 0.92 while
she verifiably chose right; verdict_json had NO frame context — the sweep
seam passed only the ~600-char tool-result delta).

1. Fork detection in the sweep pulls the last ~3 assistant messages BEFORE
   the fork from the profile state.db (read-only URI, capped, newest-first)
   as labeled surrounding_context in the envelope.
2. Knob decision.frame_context_chars (default 1500; 0 disables — envelope
   identical to pre-R19.18).
3. DB failure -> fail-open: delta-only envelope, never raises.
4. Ledger: frame_context_chars_used recorded.
"""
import json
import sqlite3
import time

import pytest

from hermes_router import decision as D


def _opts():
    return [{"id": "opt-1", "label": "redis"},
            {"id": "opt-2", "label": "memcached"}]


def test_unmatched_choice_is_invalid_fork_regression():
    v, r = D.validate_verdict('{"choice": "No such file", "confidence": 0.9}',
                              _env := {"fork_class": "deploy",
                                       "options": _opts()})
    assert r == D.REASON_INVALID_FORK and v["choice"] == "unmapped"


# --- 1: prior session text lands in the envelope --------------------------------

def test_envelope_contains_prior_session_text(tmp_path, monkeypatch):
    """PIN (a): fork with prior consumer-probe text in the session ->
    envelope carries that text, labeled, distinct from the delta."""
    db = tmp_path / "state.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE messages (session_id TEXT, timestamp REAL,"
                 " role TEXT, content TEXT)")
    old = time.time() - 60
    conn.execute("INSERT INTO messages VALUES (?,?,?,?)",
                 ("s-ctx", old, "assistant",
                  "consumer-probe results: latency p99 840ms, error rate 2%"))
    conn.commit()
    conn.close()
    ctx = D.session_context_before(time.time(), 1500, db_path=str(db))
    assert "consumer-probe results" in ctx
    env = D.build_envelope("s-ctx", "redis or memcached?", _opts(),
                           "midturn", surrounding_context=ctx)
    assert "consumer-probe results" in env["surrounding_context"]
    assert env["frame_context_chars_used"] == len(ctx)
    prompt = D.render_prompt(env)
    assert "SESSION CONTEXT PRECEDING THE FORK" in prompt
    assert "consumer-probe results" in prompt  # distinct from the delta


def test_only_assistant_rows_and_cap_three(tmp_path):
    """Newest-first, assistant-only, max 3 messages, capped."""
    db = tmp_path / "state.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE messages (session_id TEXT, timestamp REAL,"
                 " role TEXT, content TEXT)")
    now = time.time()
    for i in range(5):
        conn.execute("INSERT INTO messages VALUES (?,?,?,?)",
                     ("s", now - 100 + i, "assistant", "assistant-%d" % i))
        conn.execute("INSERT INTO messages VALUES (?,?,?,?)",
                     ("s", now - 100 + i, "user", "user-%d" % i))
    conn.commit()
    conn.close()
    ctx = D.session_context_before(now, 1500, db_path=str(db))
    for i in (4, 3, 2):
        assert "assistant-%d" % i in ctx
    assert "user-" not in ctx          # assistant rows only
    assert "assistant-0" not in ctx    # newest-first LIMIT 3
    assert "assistant-1" not in ctx


# --- 2: knob=0 -> identical to today ---------------------------------------------

def test_knob_zero_disables(tmp_path):
    db = tmp_path / "state.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE messages (session_id TEXT, timestamp REAL,"
                 " role TEXT, content TEXT)")
    conn.execute("INSERT INTO messages VALUES (?,?,?,?)",
                 ("s", time.time() - 10, "assistant", "prior evidence"))
    conn.commit()
    conn.close()
    assert D.session_context_before(time.time(), 0, db_path=str(db)) == ""
    assert D.session_context_before(time.time(), -5, db_path=str(db)) == ""
    env = D.build_envelope("s", "redis or memcached?", _opts(), "midturn")
    assert "surrounding_context" not in env  # identical to pre-R19.18
    assert "SESSION CONTEXT PRECEDING" not in D.render_prompt(env)


# --- 3: DB failure -> fail-open ---------------------------------------------------

def test_db_error_fails_open(monkeypatch):
    monkeypatch.setattr(D, "_db_path", lambda: "/nonexistent/state.db")
    assert D.session_context_before(time.time(), 1500) == ""
    monkeypatch.setattr(D, "_db_path",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert D.session_context_before(time.time(), 1500) == ""


def test_missing_table_fails_open(tmp_path):
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE other (a)")
    conn.commit()
    conn.close()
    assert D.session_context_before(time.time(), 1500, db_path=str(db)) == ""


# --- 4: cap enforced ---------------------------------------------------------------

def test_cap_enforced(tmp_path):
    """2000-char context truncated to the knob (e.g. 500)."""
    db = tmp_path / "state.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE messages (session_id TEXT, timestamp REAL,"
                 " role TEXT, content TEXT)")
    conn.execute("INSERT INTO messages VALUES (?,?,?,?)",
                 ("s", time.time() - 10, "assistant", "z" * 2000))
    conn.commit()
    conn.close()
    ctx = D.session_context_before(time.time(), 500, db_path=str(db))
    assert len(ctx) == 500


# --- ledger column -------------------------------------------------------------------

def test_ledger_frame_context_chars_used(tmp_path):
    db = str(tmp_path / "t.db")
    rid = D.ledger_write({"session_id": "s", "task_id": "t",
                          "trigger": "midturn", "choice": "opt-1",
                          "confidence": 0.9,
                          "frame_context_chars_used": 743}, db_path=db)
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT frame_context_chars_used FROM decision_ledger"
                       " WHERE id=?", (rid,)).fetchone()
    conn.close()
    assert row[0] == 743
    # absent -> NULL (migration column nullable)
    rid2 = D.ledger_write({"session_id": "s", "task_id": "t2",
                           "trigger": "pre", "choice": "opt-1",
                           "confidence": 0.5}, db_path=db)
    conn = sqlite3.connect(db)
    row2 = conn.execute("SELECT frame_context_chars_used FROM decision_ledger"
                        " WHERE id=?", (rid2,)).fetchone()
    conn.close()
    assert row2[0] is None


def test_migration_adds_frame_context_column(tmp_path):
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE decision_ledger (id INTEGER PRIMARY KEY,"
                 " ts REAL, session_id TEXT, task_id TEXT, trigger TEXT)")
    conn.commit()
    conn.close()
    c = D._ledger_connect(db)
    cols = {r[1] for r in c.execute("PRAGMA table_info(decision_ledger)")}
    c.close()
    assert "frame_context_chars_used" in cols
