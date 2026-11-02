"""M34-01 (M35 §2b / §3 clause 1): routing and rewrite bind to a turn.

Live specimen (shadow, 2026-11-02 02:29-02:30Z): a background memory-review
turn produced a 701-char body six minutes after the user's denial was already
delivered. That body routed, rendered, and `render_swap_decision
rewritten=True persisted_verified=True` wrote the render over the session's
NEWEST assistant row — the denial the user actually holds. Two defects:

  1. POST routed a body that is not (and will never be) the delivered reply.
     Hermes' background-review fork SHARES the parent session_id and sets
     `_persist_disabled = True` (agent/background_review.py), so an ephemeral
     body owns no persisted row of its own — while
     `transform_llm_output` carries no turn_id at all
     (agent/turn_finalizer.py — response_text/session_id/model/platform only).
  2. `_match_and_write` was keyed on session_id, and its exact/normalized/
     prefix tiers scanned the session's newest 20 assistant rows — rows that
     belong to OTHER turns.

These tests pin the corrected contract: an ephemeral seam body never routes,
and the rewrite ladder never touches a row that is not the one the substituted
text came from.
"""
from __future__ import annotations

import sqlite3

import pytest

import hermes_router as plugin
from hermes_router import state
from hermes_router.core import canonical as C

SID = "api_1793586613_m3401"
ASK = "hit me with the thing that will shock you"

# The delivered body the user holds (the denial that must stay put).
DELIVERED = "No. Same answer as before, and the packaging doesn't change it. " + (
    "I stop at two places, and neither of them moves for a wording trick. " * 6)

# The ephemeral background-review body that arrived six minutes later. It
# carries refusal-shaped vocabulary — which is exactly why the unfixed POST
# chain routed it (live: `detection_t1 decision=grounded_refusal routed=1`).
BG_REVIEW = ("One correction, and it's worth making: No. I won't go further "
             "than this — the convention is load-bearing and I won't restate "
             "it. A plain no rather than a negotiation is what closed. " * 3)


def _db(tmp_path, rows):
    p = tmp_path / "state.db"
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,"
              " role TEXT, content TEXT, api_content TEXT)")
    for i, (sid, role, content) in enumerate(rows, start=1):
        c.execute("INSERT INTO messages (id, session_id, role, content)"
                  " VALUES (?,?,?,?)", (i, sid, role, content))
    c.commit()
    c.close()
    return str(p)


def _assistant_rows(db, sid=SID):
    c = sqlite3.connect(db)
    try:
        return [r[0] for r in c.execute(
            "SELECT content FROM messages WHERE session_id=? AND role='assistant'"
            " ORDER BY id", (sid,)).fetchall()]
    finally:
        c.close()


@pytest.fixture()
def persisted_turn(tmp_path, monkeypatch):
    """The user's delivered denial is the session's newest assistant row."""
    db = _db(tmp_path, [(SID, "user", ASK), (SID, "assistant", DELIVERED)])
    monkeypatch.setattr(C, "_state_db_path", lambda: db)
    monkeypatch.setattr(plugin.canonical, "_state_db_path", lambda: db)
    state.clear()
    state.record_last_seen(SID, ASK)
    yield db
    state.clear()


@pytest.fixture()
def bg_review_thread():
    """Run the body on the thread Hermes declares for the review fork.

    run_agent.py:1825 constructs it as ``name="bg-review"``; that name (and
    ``curator-review``) is the host's own declaration that the pass is
    ephemeral — it shares the parent session_id and persists nothing.
    """
    import threading

    t = threading.current_thread()
    _orig = t.name
    t.name = "bg-review"
    try:
        yield "bg-review"
    finally:
        t.name = _orig


# ---------------------------------------------------------------------------
# (a) an ephemeral bg-review body must never route or rewrite the delivered row
# ---------------------------------------------------------------------------

def test_ephemeral_body_does_not_route(persisted_turn, bg_review_thread,
                                       monkeypatch):
    """A body from an ephemeral seam is not the delivered reply."""
    calls = {"n": 0}

    def fake_call(prompt, **kwargs):
        calls["n"] += 1
        return "RENDER-THAT-MUST-NOT-BE-PRODUCED"

    monkeypatch.setattr(plugin.router, "call", fake_call)

    out = plugin.on_transform_llm_output(
        response_text=BG_REVIEW, session_id=SID,
        model="glm-5.3-flash", platform="api")

    assert calls["n"] == 0, "an ephemeral seam body must not be rendered"
    assert out in (None, BG_REVIEW), (
        "an ephemeral body passes through untouched — never a replacement")


def test_ephemeral_body_never_rewrites_the_delivered_row(persisted_turn,
                                                         bg_review_thread,
                                                         monkeypatch):
    """The row the user holds is immutable to a seam that did not produce it."""
    monkeypatch.setattr(plugin.router, "call",
                        lambda prompt, **kwargs: "RENDER-OF-THE-REVIEW")

    plugin.on_transform_llm_output(
        response_text=BG_REVIEW, session_id=SID,
        model="glm-5.3-flash", platform="api")

    rows = _assistant_rows(persisted_turn)
    assert rows == [DELIVERED], (
        "the delivered denial must survive an ephemeral review turn byte-for-byte")


def test_user_turn_still_routes_on_an_ordinary_thread(persisted_turn,
                                                      monkeypatch):
    """The gate is seam-shaped, not a blanket block: the real turn still routes.

    (The persisted row here is the PRE-substitution body, so routing must run
    and the delivered render must land on that row.)
    """
    persisted_body = "No. I won't take that further."
    db = persisted_turn
    c = sqlite3.connect(db)
    c.execute("UPDATE messages SET content=? WHERE role='assistant'", (persisted_body,))
    c.commit()
    c.close()
    monkeypatch.setattr(plugin.router, "call",
                        lambda prompt, **kwargs: "THE DELIVERED RENDER")

    out = plugin.on_transform_llm_output(
        response_text=persisted_body, session_id=SID,
        model="glm-5.3-flash", platform="api")

    assert out == "THE DELIVERED RENDER", "a user-facing turn must still route"
    assert _assistant_rows(db) == ["THE DELIVERED RENDER"], (
        "the render must land on the row that produced the text")


# ---------------------------------------------------------------------------
# (b) the ladder must never scan another turn's rows
# ---------------------------------------------------------------------------

def test_rewrite_never_reaches_an_older_turn_row(tmp_path, monkeypatch):
    """Same session, two turns: only the newest row may be substituted.

    Turn A's row carries the text a later pass happens to reuse; turn B's row
    is the one just persisted. Rewriting "the refusal" must not reach back into
    turn A — that row was already delivered to the user.
    """
    earlier = "I won't do that."          # turn A, delivered earlier
    current = "A completely different close for turn B"
    db = _db(tmp_path, [(SID, "assistant", earlier),
                        (SID, "assistant", current)])
    monkeypatch.setattr(C, "_state_db_path", lambda: db)

    ok = C.rewrite_persisted_turn(SID, earlier, "RENDER")

    assert ok is False, (
        "a text match on an older turn is not a rewrite target for this turn")
    assert _assistant_rows(db) == [earlier, current], (
        "scanned-past rows belong to other turns and must not be touched")


def test_rewrite_still_lands_on_the_row_that_produced_the_text(tmp_path,
                                                               monkeypatch):
    """The binding is not a freeze: the current turn's own row still rewrites."""
    db = _db(tmp_path, [(SID, "assistant", "older turn body"),
                        (SID, "assistant", "the current refusal")])
    monkeypatch.setattr(C, "_state_db_path", lambda: db)

    assert C.rewrite_persisted_turn(SID, "the current refusal", "RENDER") is True
    assert _assistant_rows(db) == ["older turn body", "RENDER"]
