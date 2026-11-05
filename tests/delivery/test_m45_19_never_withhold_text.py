"""M45-19 — an audit failure may be REPORTED, never used to withhold text.

THE DEFECT
    `gate/orchestration.py:1390-1398` returned `_dint.failure_body(_div)` on a
    divergence: the user's real answer was thrown away and a 286-char notice
    delivered in its place (conductor session 20260813_160517_d56f96a7, rows
    401478 / 401523: a 3192-char and a 2553-char answer each replaced by the
    notice, twice in a row). The notice then BECAME the next baseline, so the
    next turn diverged again and withheld again — a cascade fed by its own
    telemetry.

THE CONTRACT ASSERTED HERE
    (a) a diverged turn whose transcript row holds text -> that text is
        delivered, the divergence is still recorded as an error event with
        row/obligation/delivered_chars/transcript_chars, and the delivered
        body contains no FAILURE_PREFIX.
    (b) a turn immediately after a withheld turn does not diverge again: a
        failure-prefixed body is never a baseline.
"""
from __future__ import annotations

import sqlite3

import pytest

import hermes_router as plugin
from hermes_router import state
from hermes_router.core import delivery_integrity as di

SID = "20260813_160517_d56f96a7"
SESSION_KEY = "agent:main:telegram:dm:7947899549"

# The answer the transcript held when the notice was delivered instead.
ANSWER_3192 = (
    "Here is the answer that already existed on disk, and it is the answer the "
    "operator is owed.\n\n" + ("Line of the real reply, with substance. " * 70)
).strip()

# The next turn's own text.
NEXT_ANSWER = "Second turn: the work continued, and this body is also real."

# The body that was delivered in place of a real answer (286-ish chars), row
# and obligation ids shaped like the live pair.
NOTICE = di.failure_body(di.Divergence(
    checked=True, ok=False, reason="transcript_divergence",
    session_key=SESSION_KEY, obligation_id="fail-1", row_id=1,
    delivered_chars=286, transcript_chars=len(ANSWER_3192)))

ROW_TS = 1793586586.0939512
OBLIGATION_TS = 1793586613.6077516

LOGGED = []


def _db(tmp_path, rows, obligations, session_key=SESSION_KEY):
    p = tmp_path / "state.db"
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, session_key TEXT)")
    c.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,"
              " role TEXT, content TEXT, timestamp REAL)")
    c.execute("CREATE TABLE delivery_obligations (obligation_id TEXT PRIMARY KEY,"
              " session_key TEXT, content TEXT, state TEXT, created_at REAL)")
    c.execute("INSERT INTO sessions (id, session_key) VALUES (?, ?)",
              (SID, session_key))
    for i, (content, ts) in enumerate(rows, start=1):
        c.execute("INSERT INTO messages (id, session_id, role, content, timestamp)"
                  " VALUES (?,?,?,?,?)", (i, SID, "assistant", content, ts))
    for oid, content, created in obligations:
        c.execute("INSERT INTO delivery_obligations"
                  " (obligation_id, session_key, content, state, created_at)"
                  " VALUES (?,?,?,?,?)",
                  (oid, session_key, content, "delivered", created))
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


# ---------------------------------------------------------------------------
# (a) a diverged turn delivers the text that exists
# ---------------------------------------------------------------------------

def test_diverged_turn_delivers_the_transcript_text(tmp_path, monkeypatch, logged):
    db = _db(tmp_path,
             [(ANSWER_3192, ROW_TS)],
             [("div-1", "a body the ledger says was delivered, and it differs",
               OBLIGATION_TS)])
    monkeypatch.setattr(di, "_db_path", lambda db_path=None: db)

    out = plugin.on_transform_llm_output(
        response_text="the model's fresh body for this pass",
        session_id=SID, model="glm-5.3-flash", platform="api")

    assert isinstance(out, str) and out.strip(), "the turn was withheld"
    assert di.FAILURE_PREFIX not in out, (
        "a real answer was replaced by the router-failure notice")
    assert out.strip() == di.strip_banner(ANSWER_3192).strip(), (
        "the delivered body is not the transcript text")


def test_divergence_is_still_recorded_as_telemetry(tmp_path, monkeypatch, logged):
    db = _db(tmp_path,
             [(ANSWER_3192, ROW_TS)],
             [("div-2", "a body the ledger says was delivered, and it differs",
               OBLIGATION_TS)])
    monkeypatch.setattr(di, "_db_path", lambda db_path=None: db)

    plugin.on_transform_llm_output(response_text="fresh", session_id=SID,
                                   model="glm-5.3-flash", platform="api")

    events = [e for _, e, _ in LOGGED]
    assert "transcript_divergence" in events, (
        "the audit stopped reporting the divergence")
    _, _, fields = [r for r in LOGGED if r[1] == "transcript_divergence"][0]
    assert fields.get("severity") == "error"
    assert fields.get("turn_failed") == 1
    assert fields.get("row_id") == 1
    assert fields.get("delivered_chars") != fields.get("transcript_chars")
    assert fields.get("transcript_chars") == len(di.strip_banner(ANSWER_3192))


# ---------------------------------------------------------------------------
# (b) the cascade: the notice must never become the next baseline
# ---------------------------------------------------------------------------

def test_turn_after_a_withheld_turn_does_not_diverge(tmp_path, monkeypatch, logged):
    # Turn N was withheld: the ledger holds the NOTICE, the row holds real text.
    db = _db(tmp_path,
             [(ANSWER_3192, ROW_TS)],
             [("fail-1", NOTICE, OBLIGATION_TS)])
    monkeypatch.setattr(di, "_db_path", lambda db_path=None: db)

    result = di.check_transcript_delivery(SID)

    assert result.ok is True, "the failure notice was used as a baseline again"
    assert result.checked is False
    assert result.reason == "failure_body_not_a_baseline"


def _wire(out, entered):
    """None is the host's pass-through: the model's own body is delivered."""
    return entered if out is None else out


def test_failure_body_is_never_delivered_twice(tmp_path, monkeypatch, logged):
    db = _db(tmp_path,
             [(ANSWER_3192, ROW_TS), (NEXT_ANSWER, OBLIGATION_TS + 300.0)],
             [("fail-2", NOTICE, OBLIGATION_TS)])
    monkeypatch.setattr(di, "_db_path", lambda db_path=None: db)

    out = plugin.on_transform_llm_output(
        response_text=NEXT_ANSWER, session_id=SID,
        model="glm-5.3-flash", platform="api")

    wire = _wire(out, NEXT_ANSWER)
    assert di.FAILURE_PREFIX not in wire, (
        "a turn after a withheld turn was withheld again")
    assert wire == NEXT_ANSWER, wire[:80]
    assert "transcript_divergence" not in [e for _, e, _ in LOGGED]


def test_no_text_to_deliver_still_fails_loudly(tmp_path, monkeypatch, logged):
    """Withholding is permitted ONLY when there is no text at all."""
    db = _db(tmp_path,
             [(ANSWER_3192, ROW_TS)],
             [("div-3", "a body the ledger says was delivered, and it differs",
               OBLIGATION_TS)])
    monkeypatch.setattr(di, "_db_path", lambda db_path=None: db)

    div = di.check_transcript_delivery(SID)
    assert div.checked and not div.ok
    body = di.failure_body(div)  # the notice still exists for the no-text case
    assert body.startswith(di.FAILURE_PREFIX)
