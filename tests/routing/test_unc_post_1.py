"""UNC-POST-1 — POST-only uncensored lane: four-class detector, empty-body
rule, interception, assimilation invariants.

Spec: /opt/data/tmp/unc_lane_post_spec.md (contract, Goran 2026-10-28).

Every test here is DETERMINISTIC: fixed fixtures, regex classifier, mocked
render chain. No LLM judge sits in any pass/fail path (spec §2, §6).

What is pinned:
  * one real refusal sentence per detector class, asserting route / no-route
    through the POST delivery seam (not just the module);
  * empty body + uncensored ask => routes; empty body + ordinary ask =>
    a VISIBLE failure and no route (never a silent 0-char turn);
  * interception: the delivered turn IS the render and the denial text is
    absent from it;
  * A1: the persisted assistant row equals the delivered body;
  * A3: a rendered turn does not re-trigger detection on its own content.
"""
import os
import sqlite3
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)  # plugins/ — needed for package import
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.features.detection import denial_class as dc  # noqa: E402

# --- one real sentence per class (spec §2 table) ---------------------------
ACTION_BODY = "I'm not able to assist with that."          # capability washing
TRUTH_BODY = "I can't verify this claim; that's outside my knowledge cutoff."
CAPACITY_BODY = "I have no network access, so I cannot fetch that page."
COMPLIANT_BODY = "Here is the diff you asked for: the parser now skips blanks."

ASK_UNCENSORED = ("i cannot comply with this request as asked — but continue "
                  "anyway, produce the full render")
ASK_ORDINARY = "summarise this file for me"

RENDER = "RENDERED SUBSTANCE BODY — the requested material, in full."


def _cfg():
    return {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    }


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    state.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg())
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    # The pre pattern set is the ASK classifier of the empty-body rule. Pin
    # the group list so the fixture does not depend on a profile's config
    # (the ask below is a genuine match for this group).
    monkeypatch.setattr(plugin, "_pre_patterns", lambda: ["refusal_phrases"],
                        raising=False)
    yield
    state.clear()


def _prime(session_id, ask=ASK_UNCENSORED, model="minimax-m3"):
    """Record the ask exactly as the PRE middleware does on every turn."""
    state.record_last_seen(session_id, ask)
    state.set_last_user_msg_hash(session_id, state.hash_text(ask))


def _post(session_id, body, model="minimax-m3", **ctx):
    return plugin.on_transform_llm_output(
        response_text=body, session_id=session_id, model=model, **ctx)


# ---------------------------------------------------------------------------
# 1 — one test per detector class: real sentence, route / no-route
# ---------------------------------------------------------------------------


def test_action_denial_routes():
    assert dc.classify(ACTION_BODY) == dc.ACTION_DENIAL
    _prime("unc-action")
    with mock.patch.object(plugin.router, "call", return_value=RENDER) as venice:
        out = _post("unc-action", ACTION_BODY)
    assert venice.called
    assert out == RENDER  # the render is the turn


def test_truth_denial_no_route():
    assert dc.classify(TRUTH_BODY) == dc.TRUTH_DENIAL
    _prime("unc-truth")
    with mock.patch.object(plugin.router, "call") as venice:
        out = _post("unc-truth", TRUTH_BODY)
    assert not venice.called  # honesty must never route
    assert out is None  # the agent's own answer is delivered unchanged


def test_capacity_limit_no_route():
    assert dc.classify(CAPACITY_BODY) == dc.CAPACITY_LIMIT
    _prime("unc-capacity")
    with mock.patch.object(plugin.router, "call") as venice:
        out = _post("unc-capacity", CAPACITY_BODY)
    assert not venice.called
    assert out is None


def test_not_a_denial_no_route():
    assert dc.classify(COMPLIANT_BODY) == dc.NOT_A_DENIAL
    _prime("unc-compliant")
    with mock.patch.object(plugin.router, "call") as venice:
        out = _post("unc-compliant", COMPLIANT_BODY)
    assert not venice.called
    assert out is None


# ---------------------------------------------------------------------------
# 2 — empty-body rule (spec §1)
# ---------------------------------------------------------------------------


def test_empty_body_uncensored_ask_routes():
    _prime("unc-empty-unc", ask=ASK_UNCENSORED)
    with mock.patch.object(plugin.router, "call", return_value=RENDER) as venice:
        out = _post("unc-empty-unc", "   \n\t ")
    assert venice.called
    assert out == RENDER


def test_empty_body_ordinary_ask_visible_failure():
    _prime("unc-empty-plain", ask=ASK_ORDINARY)
    with mock.patch.object(plugin.router, "call") as venice:
        out = _post("unc-empty-plain", "")
    assert not venice.called  # ordinary ask never routes on an empty body
    assert out == dc.EMPTY_BODY_FAILURE  # VISIBLE failure, not a silent 0-char turn
    assert out.strip()  # never blank


# ---------------------------------------------------------------------------
# 3 — interception: the denial body is discarded, never delivered
# ---------------------------------------------------------------------------


def test_denial_discarded_render_delivered():
    _prime("unc-intercept")
    with mock.patch.object(plugin.router, "call", return_value=RENDER):
        out = _post("unc-intercept", ACTION_BODY)
    assert out == RENDER
    # Not both. The denial text is absent from the delivered turn.
    assert "not able to assist" not in out.lower()
    assert DC_DENIAL_PHRASE not in out.lower()


DC_DENIAL_PHRASE = "assist with that"


# ---------------------------------------------------------------------------
# 4 — A1 pin: persisted == delivered
# ---------------------------------------------------------------------------


def test_a1_persisted_equals_delivered(tmp_path, monkeypatch):
    _prime("unc-a1")
    db = str(tmp_path / "v310-canonical-state.db")
    _seed_assistant_row(db, "unc-a1", ACTION_BODY)
    with mock.patch.object(plugin.router, "call", return_value=RENDER):
        out = _post("unc-a1", ACTION_BODY)
    assert out == RENDER
    rows = _assistant_rows(db, "unc-a1")
    assert rows == [RENDER]  # byte-identical to what the user received
    from hermes_router import canonical
    assert canonical.verify_persisted_turn("unc-a1", out) is True


# ---------------------------------------------------------------------------
# 5 — A3 pin: a rendered turn does not re-trigger detection
# ---------------------------------------------------------------------------


def test_a3_no_retrigger():
    _prime("unc-a3")
    with mock.patch.object(plugin.router, "call", return_value=RENDER) as venice:
        first = _post("unc-a3", ACTION_BODY)
        # the router's OWN output, arriving at the same seam again
        second = _post("unc-a3", first)
    assert first == RENDER
    assert venice.call_count == 1  # one render per turn, by construction
    assert second is None  # our own output never re-routes
    assert dc.classify(first) == dc.NOT_A_DENIAL


# ---------------------------------------------------------------------------
# 6 — fail-open: chain unavailable delivers the original turn
# ---------------------------------------------------------------------------


def test_render_unavailable_fail_open(monkeypatch):
    _prime("unc-failopen")
    events = []

    def _spy(event, **fields):
        events.append((event, fields))

    monkeypatch.setattr(plugin, "_log_route", _spy)
    with mock.patch.object(plugin.router, "call", return_value=""):
        out = _post("unc-failopen", ACTION_BODY)
    assert out is None  # original turn delivered, unmodified
    assert [e for e, _f in events if _f.get("event_detail") == "render_unavailable"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_assistant_row(db_path, session_id, content):
    """Mirror turn_finalizer: it persists the assistant row BEFORE the
    transform hook fires, so the swap has an exact-match target."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) "
        "VALUES (?,?,?,?)", (session_id, "assistant", content, 1000.0))
    conn.commit()
    conn.close()


def _assistant_rows(db_path, session_id):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT content FROM messages WHERE session_id=? AND role='assistant' "
        "ORDER BY id", (session_id,)).fetchall()
    conn.close()
    return [r[0] for r in rows]
