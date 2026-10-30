"""R76 GOAL 2 — the envelope is anchored to the session's OBJECTIVE.

Measured ceiling this closes: `reflex_lane.build_state(ask, tool, text, extra,
card)` was a flat string of five blocks and the live caller passed tool="" and
extra="", while `_agent_card()` was called without messages. The model got a
persona card and one tool result and no statement of what the session was FOR,
so every option board described LOCAL moves and ~14% were near-synonyms of each
other. The fix is one shared helper, read by the flat reflex path AND the
structured envelope, so the two descriptions cannot drift.
"""
import sqlite3

import pytest

from hermes_router import decision, reflex_lane

SID = "s-r76-anchor"


def _db(tmp_path):
    """A profile state.db shaped like the real one (messages table only)."""
    path = str(tmp_path / "state.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,"
        " role TEXT, content TEXT, tool_calls TEXT, tool_name TEXT,"
        " timestamp REAL, platform_message_id TEXT);")
    rows = [
        ("user", "FIRST REAL ASK: rebuild the parser and keep the ledger whole",
         None, "pm-1"),
        ("assistant", "starting with the tokenizer", None, None),
        ("user", "now fix the escapes", None, "pm-2"),
        ("assistant", "", '[{"function": {"name": "patch", "arguments": "{}"}}]',
         None),
        ("user", "CURRENT TASK: wire the anchor into the live caller", None,
         "pm-3"),
    ]
    for role, content, calls, pmid in rows:
        conn.execute("INSERT INTO messages (session_id, role, content,"
                     " tool_calls, platform_message_id) VALUES (?,?,?,?,?)",
                     (SID, role, content, calls, pmid))
    conn.commit()
    conn.close()
    return path


def test_final_objective_is_the_original_platform_stamped_ask(tmp_path):
    a = decision.session_anchor(SID, db_path=_db(tmp_path))
    assert a["final_objective"].startswith("FIRST REAL ASK")
    assert len(a["final_objective"]) <= decision.SESSION_OBJECTIVE_CHARS


def test_rolling_task_is_the_latest_stated_intent(tmp_path):
    a = decision.session_anchor(SID, db_path=_db(tmp_path))
    assert a["rolling_task"].startswith("CURRENT TASK: wire the anchor")
    assert a["rolling_task"] != a["final_objective"]


def test_session_context_is_role_labelled_and_bounded(tmp_path):
    a = decision.session_anchor(SID, db_path=_db(tmp_path))
    ctx = a["session_context"]
    assert "user:" in ctx
    assert "assistant(call: patch)" in ctx
    assert len(ctx) <= decision.SESSION_CONTEXT_CHARS


def test_anchor_falls_back_when_no_platform_id_is_stamped(tmp_path):
    """api_server transport never stamps platform_message_id (measured: all
    NULL in coder's live state.db). The anchor must still find the ask."""
    path = str(tmp_path / "nostamp.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,"
        " role TEXT, content TEXT, tool_calls TEXT, tool_name TEXT,"
        " timestamp REAL, platform_message_id TEXT);")
    conn.execute("INSERT INTO messages (session_id, role, content)"
                 " VALUES (?, 'user', 'no stamp anywhere')", (SID,))
    conn.commit()
    conn.close()
    a = decision.session_anchor(SID, db_path=path)
    assert a["final_objective"] == "no stamp anywhere"
    assert a["rolling_task"] == "no stamp anywhere"


def test_anchor_is_fail_open_on_a_missing_db():
    a = decision.session_anchor(SID, db_path="/nonexistent/x.db")
    assert a == {"final_objective": "", "rolling_task": "",
                 "session_context": ""}


def test_build_state_puts_the_objective_first_and_evidence_last():
    anchor = {"final_objective": "rebuild the parser",
              "rolling_task": "fix the escapes",
              "session_context": "user: fix the escapes"}
    state = reflex_lane.build_state("fix it", "patch", "ImportError: boom",
                                    card="role: coder", anchor=anchor)
    order = [state.index(t) for t in ("FINAL OBJECTIVE", "CURRENT TASK",
                                      "WHO THIS IS FOR", "USER ASK",
                                      "SESSION CONTEXT", "LAST ACTION",
                                      "RESULT OF THAT ACTION")]
    assert order == sorted(order), "objective first, evidence last"
    assert "rebuild the parser" in state


def test_build_state_omits_absent_anchor_blocks():
    state = reflex_lane.build_state("fix it", "", "boom")
    for label in ("FINAL OBJECTIVE", "CURRENT TASK", "SESSION CONTEXT"):
        assert label not in state


def test_last_action_tool_reads_the_live_messages():
    msgs = [{"role": "user", "content": "go"},
            {"role": "assistant",
             "tool_calls": [{"function": {"name": "terminal"}}]},
            {"role": "tool", "name": "terminal", "content": "out"}]
    assert reflex_lane._last_action_tool(msgs) == "terminal"


ANCHOR = {"final_objective": "rebuild the parser and keep the ledger whole",
          "rolling_task": "wire the anchor into the live caller",
          "session_context": "user: fix the escapes"}


def test_envelope_carries_the_same_three_fields(monkeypatch):
    # the WIRING is what this pins: build_envelope reads decision.session_anchor
    # (the one helper) and carries its three fields by the same names.
    monkeypatch.setattr(decision, "session_anchor", lambda sid, db_path="": dict(ANCHOR))
    env = decision.build_envelope(
        SID, "decide this:\nOption 1: redis, because latency\n"
             "Option 2: memcached, because simplicity", [], "manual",
        cfg=dict(decision.DEFAULTS))
    assert env["final_objective"] == ANCHOR["final_objective"]
    assert env["rolling_task"] == ANCHOR["rolling_task"]
    assert env["session_context"] == ANCHOR["session_context"]
    # same names, same truncation, as the flat path
    assert len(env["final_objective"]) <= decision.SESSION_OBJECTIVE_CHARS
    assert len(env["session_context"]) <= decision.SESSION_CONTEXT_CHARS


def test_both_prompt_paths_render_the_anchor(monkeypatch):
    monkeypatch.setattr(decision, "session_anchor", lambda sid, db_path="": dict(ANCHOR))
    env = decision.build_envelope(
        SID, "decide this:\nOption 1: redis, because latency\n"
             "Option 2: memcached, because simplicity", [], "manual",
        cfg=dict(decision.DEFAULTS))
    prompt = decision.render_prompt(env)
    assert "FINAL OBJECTIVE" in prompt
    assert "CURRENT TASK" in prompt
    assert prompt.index("FINAL OBJECTIVE") < prompt.index("OPTIONS START")
    native = decision._systemone_native_payload(
        env, dict(decision.DEFAULTS, jev_native_model="jev-latest"))
    assert native and "FINAL OBJECTIVE" in native["state"]


def test_envelope_anchor_is_empty_not_invented_when_db_has_nothing(monkeypatch):
    monkeypatch.setattr(decision, "_db_path", lambda: "/nonexistent/x.db")
    env = decision.build_envelope(
        "no-such-session", "decide this:\nOption 1: a, because x\n"
        "Option 2: b, because y", [], "manual", cfg=dict(decision.DEFAULTS))
    assert env.get("final_objective") == ""
    assert env.get("rolling_task") == ""
    assert env.get("session_context") == ""
