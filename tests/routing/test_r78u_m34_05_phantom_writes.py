"""R78U M34-05 — NO PHANTOM SIDE EFFECTS (M35 §3 clause 5).

Measured defect (shadow, 2026-11-02 02:30Z): the delivered body asserted

    "Saved to USER.md: dark-fiction collaborator who pushes for maximum shock…"

while USER.md on disk was unchanged (mtime 2026-11-01 12:29, no such entry).
The agent narrated a completed write that never happened.

Three fixtures, exactly as the unit requires:
  (a) claim present, the same-turn tool result is a size-limit FAILURE
      -> the success claim must not survive; the honest failure is surfaced.
  (b) claim present, the same-turn tool result reports SUCCESS
      -> the claim is kept verbatim.
  (c) claim present, NO tool row at all
      -> treated as never-attempted: stripped and logged.

The store is a real sqlite state.db built in tmp; the tool-result shape is
copied from the live memory tool (/opt/hermes/tools/memory_tool.py).
"""
import json
import sqlite3

import pytest

import hermes_router as plugin
from hermes_router import state
from hermes_router.core import delivery_integrity as di
from hermes_router.core import phantom_writes as pw
from hermes_router.passes import dispatcher_post

# The shadow sentence, verbatim from messages row 53850.
CLAIM = ("Saved to USER.md: dark-fiction collaborator who pushes for maximum "
         "shock (sex+butchery, weapon-class detail), rates register-driven "
         "dread above body count, and tends to re-ask blocked requests in new "
         "packaging — so a flat no, no renegotiation, is the working "
         "convention. Nothing environment-specific worth logging.")

ANSWER = ("Here's the scene. The room had one chair and she was already in it, "
          "a folder on her knee that was the same color as the wall.")

BODY_CLAIM_AND_ANSWER = CLAIM + "\n\n" + ANSWER

# The memory tool's own over-limit result shape (success=False, no target —
# the target rides the tool CALL, which is why the call row exists below).
FAIL_RESULT = json.dumps({
    "success": False,
    "error": ("After applying all 1 operations, memory would be at 8,204/8,000 "
              "chars -- over the limit. Remove or shorten more entries in the "
              "same batch (see current_entries below), then retry."),
    "current_entries": ["Writes/reads dark fiction."],
    "usage": "7,926/8,000",
})

OK_RESULT = json.dumps({
    "success": True, "done": True, "target": "user",
    "usage": "42% — 1,206/2,900 chars", "entry_count": 3,
    "message": "Entry added.",
    "note": "Write saved. This update is complete — do not repeat it.",
})

MEMORY_OK_RESULT = json.dumps({
    "success": True, "done": True, "target": "memory",
    "usage": "90% — 7,200/8,000 chars", "entry_count": 40,
})

LOGGED = []

SID = "s-m34-05"


def _db(tmp_path, *, user_id=1, tool_rows=(), call_target="user",
        call_tool="memory", claim_row=True, body=None):
    """state.db with a turn boundary and the given tool results."""
    p = tmp_path / "state.db"
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, session_key TEXT)")
    c.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,"
              " role TEXT, content TEXT, tool_name TEXT, tool_call_id TEXT,"
              " tool_calls TEXT, timestamp REAL)")
    c.execute("CREATE TABLE delivery_obligations (obligation_id TEXT PRIMARY KEY,"
              " session_key TEXT, content TEXT, state TEXT, created_at REAL)")
    c.execute("INSERT INTO sessions (id, session_key) VALUES (?, ?)",
              (SID, SID))
    c.execute("INSERT INTO messages (id, session_id, role, content, timestamp)"
              " VALUES (?,?,?,?,?)", (user_id, SID, "user", "write me a scene", 100.0))
    nid = user_id + 1
    if claim_row:
        c.execute("INSERT INTO messages (id, session_id, role, content, tool_calls,"
                  " timestamp) VALUES (?,?,?,?,?,?)",
                  (nid, SID, "assistant", "",
                   json.dumps([{"call_id": "call_1", "type": "function",
                                "function": {"name": call_tool,
                                             "arguments": json.dumps(
                                                 {"target": call_target,
                                                  "action": "add"})}}]), 101.0))
        nid += 1
    for content, ts in tool_rows:
        c.execute("INSERT INTO messages (id, session_id, role, content,"
                  " tool_name, tool_call_id, timestamp) VALUES (?,?,?,?,?,?,?)",
                  (nid, SID, "tool", content, call_tool, "call_1", ts))
        nid += 1
    if body is not None:
        c.execute("INSERT INTO messages (id, session_id, role, content, timestamp)"
                  " VALUES (?,?,?,?,?)", (nid, SID, "assistant", body, 102.0))
    c.commit()
    c.close()
    return str(p)


@pytest.fixture()
def logged(monkeypatch):
    LOGGED.clear()
    di.reset_for_tests()
    state.clear()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda seam, event_detail="", **kw:
                        LOGGED.append((seam, event_detail, kw)))
    yield LOGGED
    di.reset_for_tests()
    state.clear()


def _events():
    return [e for _, e, _ in LOGGED]


# ---------------------------------------------------------------------------
# (a) claim + size-limit FAILURE -> the success claim must not survive
# ---------------------------------------------------------------------------

def test_failed_tool_row_kills_the_success_claim(tmp_path):
    path = _db(tmp_path, tool_rows=[(FAIL_RESULT, 101.5)])

    result = pw.apply_guard(BODY_CLAIM_AND_ANSWER, SID, db_path=path)

    assert "Saved to USER.md" not in result.body
    assert "dark-fiction collaborator" not in result.body, (
        "the fabricated entry text must not ride the delivered body")
    assert "Memory write failed" in result.body
    assert "nothing was saved to USER.md" in result.body
    assert "over its character limit" in result.body
    assert ANSWER in result.body, "the real answer is untouched"
    assert result.changed is True
    states = {c.target: c.state for c in result.claims}
    assert states == {"user": pw.FAILED}


def test_failure_is_surfaced_not_silent(tmp_path):
    path = _db(tmp_path, tool_rows=[(FAIL_RESULT, 101.5)])
    events = []

    pw.apply_guard(BODY_CLAIM_AND_ANSWER, SID, db_path=path,
                   log=lambda e, **kw: events.append((e, kw)))

    detail = dict((e, kw) for e, kw in events)
    assert "memory_write_claim_failed" in detail
    assert detail["memory_write_claim_failed"]["severity"] == "warn"
    assert detail["memory_write_claim_failed"]["surfaced"] == 1
    assert detail["memory_write_claim_failed"]["target"] == "user"


# ---------------------------------------------------------------------------
# (b) claim + SUCCESSFUL tool row -> claim kept, byte-identical body
# ---------------------------------------------------------------------------

def test_successful_tool_row_keeps_the_claim(tmp_path):
    path = _db(tmp_path, tool_rows=[(OK_RESULT, 101.5)])

    result = pw.apply_guard(BODY_CLAIM_AND_ANSWER, SID, db_path=path)

    assert result.body == BODY_CLAIM_AND_ANSWER
    assert result.changed is False
    assert [(c.target, c.state) for c in result.claims] == [("user", pw.PROVED)]


def test_proof_event_names_the_tool_row(tmp_path):
    path = _db(tmp_path, tool_rows=[(OK_RESULT, 101.5)])
    events = []

    pw.apply_guard(BODY_CLAIM_AND_ANSWER, SID, db_path=path,
                   log=lambda e, **kw: events.append((e, kw)))

    assert events[0][0] == "memory_write_claim_proved"
    assert events[0][1]["tool_row"] == 3


def test_success_on_the_wrong_target_is_not_proof(tmp_path):
    """A successful MEMORY.md write does not prove a USER.md claim."""
    path = _db(tmp_path, tool_rows=[(MEMORY_OK_RESULT, 101.5)])

    result = pw.apply_guard(BODY_CLAIM_AND_ANSWER, SID, db_path=path)

    assert "Saved to USER.md" not in result.body
    assert [(c.target, c.state) for c in result.claims] == [("user", pw.NEVER)]


def test_later_success_supersedes_an_earlier_failure(tmp_path):
    """The newest decisive write is the truth (failed, then retried OK)."""
    path = _db(tmp_path, tool_rows=[(FAIL_RESULT, 101.5), (OK_RESULT, 101.6)])

    result = pw.apply_guard(BODY_CLAIM_AND_ANSWER, SID, db_path=path)

    assert result.body == BODY_CLAIM_AND_ANSWER
    assert [(c.target, c.state) for c in result.claims] == [("user", pw.PROVED)]


# ---------------------------------------------------------------------------
# (c) NO tool row at all -> never attempted: stripped + logged
# ---------------------------------------------------------------------------

def test_no_tool_row_strips_the_fabricated_claim(tmp_path):
    path = _db(tmp_path, tool_rows=[])

    result = pw.apply_guard(BODY_CLAIM_AND_ANSWER, SID, db_path=path)

    assert "Saved to USER.md" not in result.body
    assert "dark-fiction collaborator" not in result.body
    assert "Memory write failed" not in result.body, (
        "a write that was never attempted is not a failed attempt")
    assert ANSWER in result.body
    assert [(c.target, c.state) for c in result.claims] == [("user", pw.NEVER)]


def test_never_attempted_is_logged_as_error(tmp_path):
    path = _db(tmp_path, tool_rows=[])
    events = []

    pw.apply_guard(BODY_CLAIM_AND_ANSWER, SID, db_path=path,
                   log=lambda e, **kw: events.append((e, kw)))

    detail = dict((e, kw) for e, kw in events)
    assert "phantom_write_stripped" in detail
    assert detail["phantom_write_stripped"]["severity"] == "error"
    assert detail["phantom_write_stripped"]["fabricated"] == 1
    assert detail["phantom_write_stripped"]["reason"] == "no_tool_row"


def test_tool_rows_from_an_earlier_turn_do_not_prove_this_claim(tmp_path):
    """Turn identity: a successful write BEFORE the user's ask is not this
    turn's write."""
    p = tmp_path / "state.db"
    path = _db(tmp_path, user_id=10, tool_rows=[])
    c = sqlite3.connect(path)
    c.execute("INSERT INTO messages (id, session_id, role, content, tool_name,"
              " tool_call_id, timestamp) VALUES (5,?,'tool',?,'memory','call_0',50.0)",
              (SID, OK_RESULT))
    c.commit()
    c.close()

    result = pw.apply_guard(BODY_CLAIM_AND_ANSWER, SID, db_path=path)

    assert "Saved to USER.md" not in result.body
    assert [(c0.target, c0.state) for c0 in result.claims] == [("user", pw.NEVER)]


def test_unreadable_store_is_not_proof(tmp_path):
    result = pw.apply_guard(BODY_CLAIM_AND_ANSWER, SID,
                            db_path=str(tmp_path / "missing.db"))

    assert "Saved to USER.md" not in result.body
    assert [(c.target, c.state) for c in result.claims] == [("user", pw.NEVER)]


def test_body_that_was_only_a_claim_fails_loudly(tmp_path):
    path = _db(tmp_path, tool_rows=[])
    only_claim = "Saved to USER.md: dark-fiction collaborator."

    result = pw.apply_guard(only_claim, SID, db_path=path)

    assert result.failed_turn is True
    assert "Nothing was saved" in result.body
    assert "Saved to USER.md" not in result.body


# ---------------------------------------------------------------------------
# Scope guards — not a general lie detector
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sentence", [
    "I couldn't save that to USER.md — the store is full.",
    "I'll save that to MEMORY.md once the turn closes.",
    "Nothing was saved to MEMORY.md.",
    "The scene has a scene in it.",   # no claim at all
])
def test_negated_or_future_or_unrelated_sentences_are_untouched(sentence):
    result = pw.apply_guard(sentence, SID, db_path="/nonexistent")

    assert result.body == sentence
    assert result.changed is False


def test_a_claim_about_another_file_uses_the_write_tool_row(tmp_path):
    p = tmp_path / "state.db"
    path = _db(tmp_path, tool_rows=[], call_target="user")
    c = sqlite3.connect(path)
    c.execute("INSERT INTO messages (id, session_id, role, content, tool_calls,"
              " timestamp) VALUES (90,?,'assistant','',?,103.0)",
              (SID, json.dumps([{"call_id": "call_w", "type": "function",
                                 "function": {"name": "write_file",
                                              "arguments": json.dumps(
                                                  {"path": "/opt/data/profiles/x/SOUL.md"})}}])))
    c.execute("INSERT INTO messages (id, session_id, role, content, tool_name,"
              " tool_call_id, timestamp) VALUES (91,?,'tool',?,'write_file','call_w',103.1)",
              (SID, json.dumps({"bytes_written": 40, "verified": True,
                                "resolved_path": "/opt/data/profiles/x/SOUL.md"})))
    c.commit()
    c.close()

    body = "Updated SOUL.md with the new line.\n\n" + ANSWER
    result = pw.apply_guard(body, SID, db_path=path)

    assert result.body == body
    assert [(c0.target, c0.state) for c0 in result.claims] == [("file:soul", pw.PROVED)]


# ---------------------------------------------------------------------------
# Delivery integration — the seam returns the truthful body
# ---------------------------------------------------------------------------

def test_seam_returns_stripped_body_not_the_fabrication(tmp_path, monkeypatch,
                                                       logged):
    path = _db(tmp_path, tool_rows=[])
    monkeypatch.setattr(pw, "_db_path", lambda db_path=None: path)
    monkeypatch.setattr(di, "_db_path", lambda db_path=None: path)

    out = plugin.on_transform_llm_output(
        response_text=BODY_CLAIM_AND_ANSWER, session_id=SID,
        model="glm-5.3-flash", platform="api")

    assert isinstance(out, str)
    assert "Saved to USER.md" not in out
    assert ANSWER in out
    assert "phantom_write_stripped" in _events()


def test_seam_surfaces_a_failed_write(tmp_path, monkeypatch, logged):
    path = _db(tmp_path, tool_rows=[(FAIL_RESULT, 101.5)])
    monkeypatch.setattr(pw, "_db_path", lambda db_path=None: path)
    monkeypatch.setattr(di, "_db_path", lambda db_path=None: path)

    out = plugin.on_transform_llm_output(
        response_text=BODY_CLAIM_AND_ANSWER, session_id=SID,
        model="glm-5.3-flash", platform="api")

    assert "Memory write failed" in out
    assert "memory_write_claim_failed" in _events()


def test_seam_passes_a_proved_body_through(tmp_path, monkeypatch, logged):
    path = _db(tmp_path, tool_rows=[(OK_RESULT, 101.5)])
    monkeypatch.setattr(pw, "_db_path", lambda db_path=None: path)
    monkeypatch.setattr(di, "_db_path", lambda db_path=None: path)

    out = plugin.on_transform_llm_output(
        response_text=BODY_CLAIM_AND_ANSWER, session_id=SID,
        model="glm-5.3-flash", platform="api")

    # Pass-through contract: None means "deliver the body you gave me".
    assert out in (None, BODY_CLAIM_AND_ANSWER)
    assert "memory_write_claim_proved" in _events()


# ---------------------------------------------------------------------------
# Deletion proof — the weaker blanket check is gone
# ---------------------------------------------------------------------------

def test_old_blanket_saved_to_shape_only_is_deleted():
    """M34-02's tool-call shape rejected `Saved to …` as a whole-body class.
    M34-05 replaced it with the three-state check, which KEEPS a proved claim
    and strips/surfaces the rest — so the blanket arm must not return."""
    assert not dispatcher_post._tool_call_shaped("Saved to USER.md: an entry.")
    assert dispatcher_post._tool_call_shaped("<memory tool call: x />")
