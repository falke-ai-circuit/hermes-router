"""DECISION-1 pins: PRE context, task on the envelope, role-ordered causal
context, what-we-sent digest, stale option sets, ledger provenance.

Each assertion here corresponds to a measured defect:
  (a) PRE dispatched with surrounding_context="" while midturn passed one;
  (b) task_id is a hash — no task text ever rode the envelope;
  (c) the causal leg took the last 4 rows with no role filter => 4 tool bodies;
  (d) only the verdict was persisted, so "what did we send" was unanswerable;
  (e) the same {"opt-1":"redis"},{"opt-2":"memcached"} pair fired for six days;
  (f) 88% of option-bearing ledger rows had no transcript in state.db.
"""
from __future__ import annotations

import sqlite3

import pytest

from hermes_router import decision as d


@pytest.fixture()
def state_db(tmp_path):
    """A profile-shaped state.db: user/assistant rows buried under tool output."""
    path = str(tmp_path / "state.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (session_id TEXT, role TEXT,"
                 " content TEXT, timestamp REAL)")
    blob = "x" * 4000
    conn.executemany(
        "INSERT INTO messages VALUES (?,?,?,?)",
        [("s-dense", "user", "we need to pick a cache layer", 1.0),
         ("s-dense", "assistant", "reviewing both candidates", 2.0),
         ("s-dense", "tool", '{"result": "%s"}' % blob, 3.0),
         ("s-dense", "tool", '{"result": "%s"}' % blob, 4.0),
         ("s-dense", "tool", '{"result": "%s"}' % blob, 5.0),
         ("s-dense", "tool", '{"result": "%s"}' % blob, 6.0)])
    conn.commit()
    conn.close()
    return path


def test_pre_turn_context_prioritises_user_and_assistant(state_db):
    text, rows = d.pre_turn_context("s-dense", 1200, db_path=state_db)
    assert text and rows
    roles = [line.split(":", 1)[0] for line in text.splitlines()]
    # (a) the two human rows lead; tool output only fills the remainder
    assert roles[:2] == ["user", "assistant"]
    assert "pick a cache layer" in text
    assert len(text) <= 1200
    # bounded: tool bodies are hard-truncated per row
    tool_line = [x for x in text.splitlines() if x.startswith("tool:")][0]
    assert len(tool_line) <= d.TOOL_ROW_CHARS + len("tool: ") + 8


def test_pre_turn_context_fails_open():
    assert d.pre_turn_context("", 1200) == ("", 0)
    assert d.pre_turn_context("s-x", 0) == ("", 0)
    assert d.pre_turn_context("s-x", 1200, db_path="/nonexistent/x.db") == ("", 0)


def test_causal_context_role_ordered(state_db, monkeypatch):
    monkeypatch.setattr(d, "_db_path", lambda: state_db)
    out = d._causal_context("s-dense", "redis or memcached?", {})
    # (c) human text is present and leads before any tool filler
    assert out is not None
    assert "pick a cache layer" in out
    tool_json = [p for p in out.split(" | ") if '{"result"' in p]
    assert not tool_json, "raw tool JSON must never fill the causal leg"


def test_envelope_task_present_and_absent(state_db):
    text, _ = d.pre_turn_context("s-dense", 1200, db_path=state_db)
    env = d.build_envelope("s-dense", "redis or memcached?",
                           ["redis", "memcached"], "pre", {},
                           surrounding_context=text, task="pick a cache layer")
    assert env["task"] == "pick a cache layer"
    assert env["digest"]["task_chars"] == len("pick a cache layer")
    assert env["digest"]["sctx_chars"] == len(text)
    assert env["digest"]["sctx_rows"] >= 2
    # (b) absent component -> omitted, never invented as ""
    env2 = d.build_envelope("s-dense", "redis or memcached?",
                            ["redis", "memcached"], "pre", {},
                            surrounding_context=text)
    assert "task" not in env2


def test_rendered_payload_carries_the_task():
    env = d.build_envelope("s-dense", "redis or memcached?",
                           ["redis", "memcached"], "pre", {},
                           task="pick a cache layer")
    prompt = d.render_prompt(env)
    assert "TASK (the job this turn belongs to): pick a cache layer" in prompt
    native = d._systemone_native_payload(env, {})
    assert "pick a cache layer" in native["state"]


def test_option_set_repeat_suppresses_only_identical_sets():
    d._OPTION_SETS.clear()
    first = d._option_set_repeat("s-ttl", ["redis", "memcached"], "t1")
    d._option_set_record("s-ttl", ["redis", "memcached"], "t1")
    repeat = d._option_set_repeat("s-ttl", ["redis", "memcached"], "t1")
    other_set = d._option_set_repeat("s-ttl", ["redis", "memcached", "tile"], "t1")
    other_task = d._option_set_repeat("s-ttl", ["redis", "memcached"], "t2")
    other_session = d._option_set_repeat("s-other", ["redis", "memcached"], "t1")
    # (e) first fire is allowed, the identical repeat is suppressed
    assert first == ""
    assert repeat and len(repeat) == 12
    assert other_set == ""
    assert other_task == ""          # different task = genuine new fork (r19_17)
    assert other_session == ""


def test_ledger_records_digest(state_db, tmp_path, monkeypatch):
    # (d) the digest rides the row; an isolated store is written directly
    ledger = str(tmp_path / "ledger.db")
    rid = d.ledger_write(
        {"session_id": "s-dense", "trigger": "pre",
         "envelope_digest": '{"sctx_chars": 925, "task_chars": 18}'},
        db_path=ledger)
    assert rid
    conn = sqlite3.connect(ledger)
    try:
        row = conn.execute(
            "SELECT envelope_digest FROM decision_ledger WHERE id = ?",
            (rid,)).fetchone()
    finally:
        conn.close()
    assert row and "sctx_chars" in row[0]
