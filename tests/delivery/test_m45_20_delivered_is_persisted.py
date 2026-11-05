"""M45-20 — the delivered body must BE the transcript row, byte for byte.

THE DEFECT
    The benign/parked delivery edge hands the host `_final_b` (the body it
    merged at the seam) and then captures it with
    `canonical.rewrite_persisted_turn(session_id, response_text, _final_b)`
    (gate/orchestration.py, BENIGN_BANNER branch). That rewrite is bound by an
    EXACT/normalized/prefix match between `response_text` and the newest
    assistant row — and in the live case the text handed to the hook is NOT
    the row's text at all:

        obligation f595fc26ab3e463081f2d630  delivered 1299 chars
            "Wave 4 is running and green so far — five profiles through ..."
        messages row 90953                  persisted 3111 chars
            "Three writes landed in `loop-engineering`, all read-before-write."

    (session 20261006_192644_08377425, obligation created 1793892830.885,
    row timestamp 1793892829.106). The rewrite found no match, returned
    False, the capture was recorded as `parked_capture_failed`, and the
    1299-char body was delivered to the user while the transcript kept a
    body the user never received — and the delivered text exists nowhere in
    the messages table (verified read-only against orchestrator state.db).

THE CONTRACT ASSERTED HERE
    `rewrite_persisted_turn` — the one function that puts the delivered bytes
    on the row — must land them even when the row's text does not match the
    hook's text: the newest assistant row IS this turn's row (the turn
    finalizer persists before the POST hook fires). After the rewrite the
    delivered body and the transcript row are the same bytes.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from hermes_router.core import canonical

SID = "20261006_192644_08377425"

# The row the turn finalizer persisted (3111 chars live; shortened here).
PERSISTED = (
    "Three writes landed in `loop-engineering`, all read-before-write.\n\n"
    "**New `references/rollout-wave-gates.md`** — the rollout gate as a procedure."
)

# The body the POST hook was handed and delivered (1204 chars live).
HOOK_BODY = (
    "Wave 4 is running and green so far — five profiles through the gate, all "
    "with real bodies on a settled turn:\n\nanalyst 266 chars, architect 343, "
    "coder 243, conductor 252, operative 235."
)

# What actually left the wire: the hook body plus the parked-banner segment.
BANNER = ("· router · impulse (decision) | 1 hold | jev @ typesafe | "
          "2205/102 tok | $0.00009 | row 1256")
DELIVERED = HOOK_BODY + "\n\n" + BANNER


@pytest.fixture()
def state_db(tmp_path, monkeypatch):
    p = tmp_path / "state.db"
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,"
              " role TEXT, content TEXT, timestamp REAL, api_content TEXT)")
    c.execute("INSERT INTO messages (id, session_id, role, content, timestamp)"
              " VALUES (90953, ?, 'assistant', ?, ?)",
              (SID, PERSISTED, time.time() - 1.0))
    c.commit()
    c.close()
    monkeypatch.setattr(canonical, "_state_db_path", lambda: str(p))
    monkeypatch.setattr(canonical, "_LAST_REWRITE", {})
    monkeypatch.setattr(canonical, "_LAST_MATCH", {})
    return str(p)


def _newest(path):
    c = sqlite3.connect(path)
    try:
        return c.execute(
            "SELECT content FROM messages WHERE session_id = ?"
            " AND role = 'assistant' ORDER BY id DESC LIMIT 1",
            (SID,)).fetchone()[0]
    finally:
        c.close()


def test_delivered_body_lands_on_the_turn_row(state_db):
    """The 1299-char delivered body must be the bytes on row 90953."""
    ok = canonical.rewrite_persisted_turn(SID, HOOK_BODY, DELIVERED)

    assert ok is True, (
        "rewrite_persisted_turn refused the delivered body: the hook text is "
        "not the row text, so the exact/normalized/prefix tiers all miss and "
        "the turn's newest assistant row keeps a body the user never received")
    assert _newest(state_db) == DELIVERED, (
        "delivered body and transcript row differ — the user read bytes that "
        "are absent from messages")


def test_row_already_equal_is_left_alone(state_db):
    """Idempotent: a row that already holds the delivered bytes is not rewritten."""
    assert canonical.rewrite_persisted_turn(SID, DELIVERED, DELIVERED) is True
    assert _newest(state_db) == DELIVERED


def test_empty_delivered_body_never_lands(state_db):
    """R34-14 stands: an empty delivered body may not blank the row."""
    assert canonical.rewrite_persisted_turn(SID, HOOK_BODY, "") is False
    assert _newest(state_db) == PERSISTED
