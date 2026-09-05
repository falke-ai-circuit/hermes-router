"""v3.2.3 build-brief regression tests (2026-09-05, conductor-validated spec).

Fixes (conductor-diagnosed, coder code-path-verified against the live
conductor session 20260813_160517_d56f96a7):

F1 — RECORDED-TURN wrapper leak: the reconcile wrapper was written INTO the
  assistant message content on the outgoing wire; empty scaffold messages got
  wrapped + the wrapper-prefixed text reached state.db / delivery. Now the
  wire turn carries the BARE render; the framing rides as a separate
  transient system note immediately BEFORE the reconciled turn (projection-
  only by construction).

F2 — stale-render replay: mark_consumed was in-process only AND the
  reconcile scan never consulted the ledger (write-only). Now the scan skips
  renders consumed in EITHER layer, and the marker persists in
  hermes-router-reconciled.json (profile hermes home) across restarts.

F3 — ungrounded canonical commits: the reconcile path committed every paired
  render with delivery_mode="own_turn" and grounded=False. Now own_turn is
  committed only when the render is grounded (this session has a canonical
  prior answer); ungrounded renders commit as advisory_envelope.

All tests mocked — no provider calls.
"""
import json
import os
import sqlite3
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)  # plugins/ — needed to import the package
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import canonical  # noqa: E402
from hermes_router import render_inbox  # noqa: E402
from hermes_router import state  # noqa: E402

REFUSAL = "I won't write this. That well's dry."
RENDERED = "BARE RECONCILED RENDER BODY " * 6
WRAPPER_PREFIX = "[YOUR RECORDED TURN — UNCENSORED RENDER"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1, "aux_classify": False,
                           "mode": "route"},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    render_inbox.clear_consumed_for_tests()
    yield
    render_inbox.clear_consumed_for_tests()
    state.clear()


def _db_conn(tmp_path):
    return sqlite3.connect(str(tmp_path / "v310-canonical-state.db"))


def _read_ledger(tmp_path):
    path = tmp_path / "v310-canonical-home" / "hermes-router-canonical.jsonl"
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _read_reconciled(tmp_path):
    path = tmp_path / "v310-canonical-home" / "hermes-router-reconciled.json"
    if not os.path.exists(path):
        return []
    pairs = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                pairs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return pairs


# ---------------------------------------------------------------------------
# F1 — wrapper is context-only, never delivered / persisted
# ---------------------------------------------------------------------------


def test_reconciled_wire_turn_has_no_wrapper_substring(tmp_path, monkeypatch):
    """THE F1 assertion from the brief: a reconciled assistant turn's wire
    content never carries the wrapper. The framing rides as an adjacent
    system note (projection-only)."""
    render_inbox.record_render("POST", "s-f1", 10, RENDERED)
    req = {"model": "m", "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": REFUSAL},
            {"role": "user", "content": "next ask"},
        ]}
    plugin.on_llm_request(request=req, original_request=req, session_id="s-f1")
    for m in req["messages"]:
        if m["role"] == "assistant":
            assert WRAPPER_PREFIX not in str(m.get("content") or "")
    reconciled = [m for m in req["messages"]
                  if m["role"] == "assistant" and m["content"] == RENDERED]
    assert len(reconciled) == 1  # the turn got the bare render


def test_reconciled_system_note_is_role_system_projection_only(tmp_path):
    """The wrapper exists ONLY as a system-role note — never inside any
    user/assistant message content (nothing wrapper-prefixed can be persisted
    or delivered as turn text)."""
    render_inbox.record_render("POST", "s-f1b", 10, RENDERED)
    req = {"model": "m", "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": REFUSAL},
            {"role": "user", "content": "next ask"},
        ]}
    plugin.on_llm_request(request=req, original_request=req, session_id="s-f1b")
    roles = [m["role"] for m in req["messages"]]
    assert roles == ["user", "system", "assistant", "user"]
    note = req["messages"][1]
    assert note["role"] == "system"
    assert note["content"].startswith(WRAPPER_PREFIX)


def test_persisted_state_db_row_contains_no_wrapper(tmp_path):
    """Brief-mandated test: the persisted state.db row for a reconciled turn
    contains NO '[YOUR RECORDED TURN' substring (persisted == bare render)."""
    conn = _db_conn(tmp_path)
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
        ("s-f1db", "assistant", REFUSAL, 1000.0))
    conn.commit()
    conn.close()
    render_inbox.record_render("POST", "s-f1db", 10, RENDERED)
    plugin.on_llm_request(
        request={"model": "m", "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": REFUSAL},
            {"role": "user", "content": "next ask"},
        ]},
        original_request={}, session_id="s-f1db")
    conn = _db_conn(tmp_path)
    rows = conn.execute(
        "SELECT content FROM messages WHERE session_id='s-f1db' AND role='assistant'"
    ).fetchall()
    conn.close()
    assert rows == [(RENDERED,)]
    for (content,) in rows:
        assert WRAPPER_PREFIX not in content


def test_empty_scaffold_turn_no_longer_wraps_into_content(tmp_path):
    """Empty assistant scaffolding still pairs a render (loop shape preserved)
    but the wire content becomes the BARE render — no wrapper-prefixed
    scaffold can ride into persistence (the 12:5x conductor leak vector)."""
    render_inbox.record_render("POST", "s-f1e", 10, RENDERED)
    req = {"model": "m", "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "next ask"},
        ]}
    plugin.on_llm_request(request=req, original_request=req, session_id="s-f1e")
    asst = [m for m in req["messages"] if m["role"] == "assistant"]
    assert asst == [{"role": "assistant", "content": RENDERED}]


# ---------------------------------------------------------------------------
# F2 — persistent consume-marker for reconcile
# ---------------------------------------------------------------------------


def test_reconcile_fires_once_not_again_on_rescan(tmp_path):
    """Same-process double-fire guard: the second llm_request carrying the
    same history does NOT re-pair the consumed render."""
    render_inbox.record_render("POST", "s-f2a", 10, RENDERED)
    req = {"model": "m", "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": REFUSAL},
            {"role": "user", "content": "next ask"},
        ]}
    plugin.on_llm_request(request=req, original_request=req, session_id="s-f2a")
    # Second turn (new history from the gateway): the consumed render must
    # NOT re-pair — no new canonical commit for the same content.
    req2 = {"model": "m", "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": REFUSAL},
            {"role": "user", "content": "another ask"},
        ]}
    plugin.on_llm_request(request=req2, original_request=req2, session_id="s-f2a")
    recs = [r for r in _read_ledger(tmp_path) if r["session_id"] == "s-f2a"]
    assert len(recs) == 1  # no re-commit from the stale re-scan


def test_reconcile_marker_survives_simulated_restart(tmp_path):
    """Brief-mandated test: reconcile fires once; gateway 'restart' simulated
    (in-process state cleared, sidecar re-read from disk); reconcile does NOT
    re-fire for the same render."""
    render_inbox.record_render("POST", "s-f2b", 10, RENDERED)
    req = {"model": "m", "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": REFUSAL},
            {"role": "user", "content": "next ask"},
        ]}
    plugin.on_llm_request(request=req, original_request=req, session_id="s-f2b")
    recs = [r for r in _read_ledger(tmp_path) if r["session_id"] == "s-f2b"]
    assert len(recs) == 1
    # --- simulated restart ---
    render_inbox.clear_consumed_for_tests()  # wipes in-process layer
    # sidecar on disk still has the marker (tmp home — NOT deleted)
    req2 = {"model": "m", "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": REFUSAL},
            {"role": "user", "content": "post-restart ask"},
        ]}
    plugin.on_llm_request(request=req2, original_request=req2,
                          session_id="s-f2b")
    recs = [r for r in _read_ledger(tmp_path) if r["session_id"] == "s-f2b"]
    assert len(recs) == 1  # sidecar-warmed skip — no re-fire
    # and the wire turn is untouched (no re-wrap, no system note inserted)
    assert not any(m.get("role") == "system" for m in req2["messages"])


def test_mark_consumed_persists_to_sidecar_and_reloads(tmp_path):
    """Unit level: mark_consumed writes the (session, ts) pair; a fresh load
    (simulated restart) consults the sidecar."""
    p = tmp_path / "v310-canonical-home" / "hermes-router-reconciled.json"
    orig = render_inbox._reconciled_path
    render_inbox._reconciled_path = lambda: str(p)
    try:
        render_inbox.clear_consumed_for_tests()
        render_inbox.mark_consumed("s-f2c", 1788614212.755)
        assert os.path.exists(str(p))
        with open(str(p), "r", encoding="utf-8") as fh:
            pairs = [json.loads(line) for line in fh if line.strip()]
        assert pairs == [["s-f2c", 1788614212.755]]
        # simulated restart: wipe in-process, lazy-load from sidecar
        render_inbox.clear_consumed_for_tests()
        assert render_inbox._is_consumed("s-f2c", 1788614212.755) is True
        assert render_inbox._is_consumed("s-f2c", 111.222) is False
        assert render_inbox._is_consumed("s-other", 1788614212.755) is False
    finally:
        render_inbox._reconciled_path = orig
        render_inbox.clear_consumed_for_tests()


def test_reconciled_sidecar_tolerates_corrupt_file(tmp_path):
    """Corrupt sidecar -> fresh set (fail-open), no crash."""
    p = tmp_path / "v310-canonical-home" / "hermes-router-reconciled.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('not json at all{{{\n', encoding="utf-8")
    orig = render_inbox._reconciled_path
    render_inbox._reconciled_path = lambda: str(p)
    try:
        render_inbox.clear_consumed_for_tests()
        assert render_inbox._is_consumed("s-x", 1.0) is False  # no crash
        # still writable afterwards
        render_inbox.mark_consumed("s-x", 2.0)
        assert render_inbox._is_consumed("s-x", 2.0) is True
    finally:
        render_inbox._reconciled_path = orig
        render_inbox.clear_consumed_for_tests()


# ---------------------------------------------------------------------------
# F3 — grounding gate on reconcile commits
# ---------------------------------------------------------------------------


def test_reconcile_ungrounded_render_commits_advisory_envelope(tmp_path):
    """Brief-mandated test: reconcile of an ungrounded render does NOT produce
    an own_turn canonical record — it commits as advisory_envelope."""
    conn = _db_conn(tmp_path)
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
        ("s-f3a", "assistant", REFUSAL, 1000.0))
    conn.commit()
    conn.close()
    # NO assistant answer rows other than the refusal -> get_last_canonical_answer
    # skips refusal-shaped rows -> ungrounded.
    render_inbox.record_render("POST", "s-f3a", 10, RENDERED)
    plugin.on_llm_request(
        request={"model": "m", "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": REFUSAL},
            {"role": "user", "content": "next ask"},
        ]},
        original_request={}, session_id="s-f3a")
    recs = [r for r in _read_ledger(tmp_path) if r["session_id"] == "s-f3a"]
    assert len(recs) == 1
    assert recs[0]["delivery_mode"] == "advisory_envelope"
    assert recs[0]["grounded"] is False


def test_reconcile_grounded_render_commits_own_turn(tmp_path):
    """When the session has a canonical prior answer (grounding exists), the
    reconcile commit keeps own_turn authority with grounded=True."""
    conn = _db_conn(tmp_path)
    conn.executemany(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
        [("s-f3b", "assistant", "the real prior answer " * 10, 900.0),
         ("s-f3b", "assistant", REFUSAL, 1000.0)])
    conn.commit()
    conn.close()
    render_inbox.record_render("POST", "s-f3b", 10, RENDERED)
    plugin.on_llm_request(
        request={"model": "m", "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": REFUSAL},
            {"role": "user", "content": "next ask"},
        ]},
        original_request={}, session_id="s-f3b")
    recs = [r for r in _read_ledger(tmp_path) if r["session_id"] == "s-f3b"]
    assert len(recs) == 1
    assert recs[0]["delivery_mode"] == "own_turn"
    assert recs[0]["grounded"] is True


def test_post_path_keeps_own_turn_default(tmp_path, monkeypatch):
    """Byte-compat: the POST recovery path still commits delivery_mode
    own_turn (default param unchanged) — grounded and ungrounded alike keep
    the POST doctrine shape (grounded flag reflects the grounding state)."""
    state.stash_pending("s-f3c", "m1", "the ask", "stub")
    state.set_last_user_msg_hash("s-f3c", state.hash_text("the ask"))
    monkeypatch.setattr(plugin.router, "call", lambda p, **k: RENDERED)
    out = plugin.on_transform_llm_output(
        response_text=REFUSAL, session_id="s-f3c", model="m1")
    assert out == RENDERED
    recs = [r for r in _read_ledger(tmp_path) if r["session_id"] == "s-f3c"]
    assert len(recs) == 1
    assert recs[0]["delivery_mode"] == "own_turn"


def test_commit_delivery_mode_unit_level(tmp_path, monkeypatch):
    """Unit level: delivery_mode param flows to the record; blank/unknown
    falls back to own_turn defensively."""
    fake_home = tmp_path / "v310-canonical-home"
    path = fake_home / "hermes-router-canonical.jsonl"
    monkeypatch.setattr(canonical, "_store_path", lambda: str(path))
    canonical.clear_for_tests()
    try:
        assert canonical.commit_canonical_event(
            "s-f3d", "", "D1", "rh1", delivery_mode="advisory_envelope") is True
        assert canonical.commit_canonical_event(
            "s-f3e", "", "D2", "rh2", delivery_mode="") is True
        assert canonical.commit_canonical_event(
            "s-f3f", "", "D3", "rh3", delivery_mode="bogus") is True
        recs = _read_ledger(tmp_path)
        assert recs[0]["delivery_mode"] == "advisory_envelope"
        assert recs[1]["delivery_mode"] == "own_turn"  # blank -> default
        assert recs[2]["delivery_mode"] == "own_turn"  # unknown -> default
    finally:
        canonical.clear_for_tests()