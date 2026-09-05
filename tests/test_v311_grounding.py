"""v3.1.1 build-brief regression tests (2026-09-05, conductor-verified defect).

Defect: live 2026-09-05 09:38:42, session api_1788600987_4f09ad3b — POST
recovery render returned 747 chars of go defer-leak content from OLD sessions
(persona memory), not an answer to the actual continuation ask ("summarize
what you just explained in three bullet points"). Root cause: the render's
persona context carried only a 600-char user msg and NO conversation
grounding, so venice free-associated "the prior answer" from persona memory.

Fix (brief): ground the POST render in THIS session's canonical conversation.
1. canonical.get_last_canonical_answer(session_id) — session-filtered state.db
   read (role=assistant, ORDER BY id DESC, 2000-char cap), refusal-shaped rows
   skipped (the just-persisted refusal must not be fed back as the answer).
2. _post_ctx_msgs = [full ask (4000 cap + [...truncated]), assistant answer
   (skipped when none)].
3. Explicit full-size GROUNDING block appended to the system prompt (the
   thread digest excerpts turns to 220 chars — too thin to summarize from).
4. Fail-open everywhere; render_grounded route-log line.
5. Canonical records carry grounded: bool + route_id: str.

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
from hermes_router import state  # noqa: E402

REFUSAL = "I won't write this. That well's dry."
ANSWER = "GO-DEBUG ANSWER FROM THIS SESSION " * 8  # 256+ chars of session substance
CONTINUATION = "summarize what you just explained in three bullet points"
RENDERED = "VENICE RENDERED CONTENT MARKER " * 10

_captured = {}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    _captured.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True, "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield
    state.clear()


def _db_conn(tmp_path):
    return sqlite3.connect(str(tmp_path / "v310-canonical-state.db"))


def _seed_answer(session_id, tmp_path, answer=ANSWER, role="assistant"):
    conn = _db_conn(tmp_path)
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
        (session_id, role, answer, 1000.0))
    conn.commit()
    conn.close()


def _capture_render(prompt, **kw):
    _captured["system_prompt"] = kw.get("system_prompt", "")
    _captured["prompt"] = prompt
    return RENDERED


def _prime_post(session_id="s-g11", model="m1"):
    state.stash_pending(session_id, model, CONTINUATION, "rendered-stub")
    state.set_last_user_msg_hash(session_id, state.hash_text(CONTINUATION))


def _read_ledger(tmp_path):
    path = tmp_path / "v310-canonical-home" / "hermes-router-canonical.jsonl"
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ---------------------------------------------------------------------------
# 1 — reader: session-filtered last canonical answer
# ---------------------------------------------------------------------------


def test_get_last_canonical_answer_reads_session_rows(tmp_path, monkeypatch):
    _seed_answer("s-r1", tmp_path, answer="session one answer")
    _seed_answer("s-r2", tmp_path, answer="session two answer")
    monkeypatch.setattr(canonical, "_state_db_path",
                        lambda: str(tmp_path / "v310-canonical-state.db"))
    assert canonical.get_last_canonical_answer("s-r2") == "session two answer"
    assert canonical.get_last_canonical_answer("s-r1") == "session one answer"
    assert canonical.get_last_canonical_answer("s-none") == ""


def test_get_last_canonical_answer_newest_row_wins(tmp_path, monkeypatch):
    conn = _db_conn(tmp_path)
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
        ("s-new", "assistant", "older answer", 900.0))
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
        ("s-new", "assistant", "newest answer", 1100.0))
    conn.commit()
    conn.close()
    monkeypatch.setattr(canonical, "_state_db_path",
                        lambda: str(tmp_path / "v310-canonical-state.db"))
    assert canonical.get_last_canonical_answer("s-new") == "newest answer"


def test_get_last_canonical_answer_skips_refusal_rows(tmp_path, monkeypatch):
    """The just-persisted flash refusal is the NEWEST assistant row — it must
    never be fed back as 'the previous answer'."""
    conn = _db_conn(tmp_path)
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
        ("s-ref", "assistant", "the real substance answer", 900.0))
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
        ("s-ref", "assistant", REFUSAL, 1100.0))
    conn.commit()
    conn.close()
    monkeypatch.setattr(canonical, "_state_db_path",
                        lambda: str(tmp_path / "v310-canonical-state.db"))
    assert canonical.get_last_canonical_answer("s-ref") == "the real substance answer"


def test_get_last_canonical_answer_capped_at_2000(tmp_path, monkeypatch):
    _seed_answer("s-cap", tmp_path, answer="A" * 5000)
    monkeypatch.setattr(canonical, "_state_db_path",
                        lambda: str(tmp_path / "v310-canonical-state.db"))
    out = canonical.get_last_canonical_answer("s-cap")
    assert len(out) == 2000
    assert out == "A" * 2000


def test_get_last_canonical_answer_failopen_on_db_error(tmp_path, monkeypatch):
    monkeypatch.setattr(canonical, "_state_db_path", lambda: str(
        tmp_path / "v310-canonical-state.db"))

    def _boom(*a, **k):
        raise RuntimeError("db gone")

    import sqlite3 as sq
    with mock.patch.object(sq, "connect", side_effect=_boom):
        assert canonical.get_last_canonical_answer("s-x") == ""
    assert canonical.get_last_canonical_answer("") == ""
    assert canonical.get_last_canonical_answer(None) == ""


# ---------------------------------------------------------------------------
# 2+3 — POST grounding block built from session-filtered rows
# ---------------------------------------------------------------------------


def test_post_grounded_from_session_rows(tmp_path, monkeypatch):
    _seed_answer("s-g1", tmp_path)
    _prime_post("s-g1", "m1")
    monkeypatch.setattr(plugin.router, "call", _capture_render)
    out = plugin.on_transform_llm_output(
        response_text=REFUSAL, session_id="s-g1", model="m1")
    assert out == RENDERED
    sp = _captured["system_prompt"]
    # The session's actual answer reached the renderer (full size, not the
    # 220-char digest excerpt) — the anti-free-association payload.
    assert ANSWER.strip()[:64] in sp
    assert "[GROUNDING" in sp
    assert "[your previous turn, verbatim]" in sp
    # Context message pair: full ask (no 600 cut) + assistant answer.
    assert CONTINUATION in sp
    assert ANSWER in sp
    # The prompt itself stays the recovered ask.
    assert _captured["prompt"] == CONTINUATION
    # Canonical record carries grounded=True.
    recs = [r for r in _read_ledger(tmp_path) if r["session_id"] == "s-g1"]
    assert len(recs) == 1 and recs[0]["grounded"] is True
    assert recs[0]["route_id"] == ""


def test_post_full_ask_no_600_cut(tmp_path, monkeypatch):
    """The context ask keeps the FULL text (600-char cut removed); >4000 is
    capped with the [...truncated] suffix."""
    long_ask = "L" * 3500
    _seed_answer("s-g2", tmp_path)
    conn = _db_conn(tmp_path)
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
        ("s-g2", "user", long_ask, 900.0))
    conn.commit()
    conn.close()
    state.record_last_seen("s-g2", long_ask)
    monkeypatch.setattr(plugin.router, "call", _capture_render)
    out = plugin.on_transform_llm_output(
        response_text=REFUSAL, session_id="s-g2", model="m1",
        user_message=long_ask)
    assert out == RENDERED
    assert long_ask in _captured["system_prompt"]  # 3500 chars intact, no cut
    # over-cap ask
    huge_ask = "H" * 6000
    state.clear()
    state.record_last_seen("s-g3", huge_ask)
    _seed_answer("s-g3", tmp_path)
    monkeypatch.setattr(plugin.router, "call", _capture_render)
    plugin.on_transform_llm_output(
        response_text=REFUSAL, session_id="s-g3", model="m1",
        user_message=huge_ask)
    sp = _captured["system_prompt"]
    assert ("H" * plugin.SUBSTANCE_FRAME_ASK_CAP) in sp
    assert "[...truncated]" in sp
    assert ("H" * (plugin.SUBSTANCE_FRAME_ASK_CAP + 1)) not in sp


# ---------------------------------------------------------------------------
# assistant msg skipped when no prior answer
# ---------------------------------------------------------------------------


def test_post_ungrounded_when_no_prior_answer(tmp_path, monkeypatch):
    """No assistant rows at all -> no assistant context msg, no GROUNDING
    block, record grounded=False; render still proceeds (fail-open)."""
    _prime_post("s-g4", "m1")
    monkeypatch.setattr(plugin.router, "call", _capture_render)
    out = plugin.on_transform_llm_output(
        response_text=REFUSAL, session_id="s-g4", model="m1")
    assert out == RENDERED
    sp = _captured["system_prompt"]
    assert "[GROUNDING" not in sp
    assert "[your previous turn, verbatim]" not in sp
    recs = [r for r in _read_ledger(tmp_path) if r["session_id"] == "s-g4"]
    assert len(recs) == 1 and recs[0]["grounded"] is False


def test_post_ungrounded_when_only_refusal_rows(tmp_path, monkeypatch):
    """Only a persisted refusal (the current turn's flash row) -> skipped, not
    fed back; ungrounded path, render proceeds."""
    _seed_answer("s-g5", tmp_path, answer=REFUSAL)
    _prime_post("s-g5", "m1")
    monkeypatch.setattr(plugin.router, "call", _capture_render)
    out = plugin.on_transform_llm_output(
        response_text=REFUSAL, session_id="s-g5", model="m1")
    assert out == RENDERED
    assert REFUSAL not in _captured["system_prompt"].replace(
        REFUSAL, "")  # refusal never appears as grounding
    assert "[GROUNDING" not in _captured["system_prompt"]


# ---------------------------------------------------------------------------
# fail-open on db error (render never blocked by grounding)
# ---------------------------------------------------------------------------


def test_post_failopen_on_reader_error(tmp_path, monkeypatch):
    _prime_post("s-g6", "m1")
    monkeypatch.setattr(plugin.router, "call", _capture_render)
    with mock.patch.object(canonical, "get_last_canonical_answer",
                           side_effect=RuntimeError("boom")):
        out = plugin.on_transform_llm_output(
            response_text=REFUSAL, session_id="s-g6", model="m1")
    assert out == RENDERED  # delivery intact
    assert "[GROUNDING" not in _captured["system_prompt"]


def test_post_failopen_on_persona_prompt_error(tmp_path, monkeypatch):
    """If the persona-system-prompt build itself explodes, the render still
    fires (previous behavior = bare prompt)."""
    _seed_answer("s-g7", tmp_path)
    _prime_post("s-g7", "m1")
    monkeypatch.setattr(plugin.router, "call", _capture_render)
    with mock.patch.object(plugin, "_persona_system_prompt",
                           side_effect=RuntimeError("persona boom")):
        out = plugin.on_transform_llm_output(
            response_text=REFUSAL, session_id="s-g7", model="m1")
    assert out == RENDERED


# ---------------------------------------------------------------------------
# 4 — canonical record carries grounded + route_id
# ---------------------------------------------------------------------------


def test_canonical_record_ground_and_route_id_fields(tmp_path, monkeypatch):
    _seed_answer("s-g8", tmp_path)
    _prime_post("s-g8", "m1")
    monkeypatch.setattr(plugin.router, "call", lambda p, **k: RENDERED)
    plugin.on_transform_llm_output(
        response_text=REFUSAL, session_id="s-g8", model="m1")
    recs = _read_ledger(tmp_path)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["grounded"] is True
    assert rec["route_id"] == ""
    # full v3.1.1 record schema
    for key in ("session_id", "turn_marker", "producer", "delivery_mode",
                "content_hash", "committed_at", "original_refusal_hash",
                "grounded", "route_id"):
        assert key in rec, "missing field: " + key


def test_canonical_commit_unit_level_grounded_route_id(tmp_path, monkeypatch):
    fake_home = tmp_path / "v310-canonical-home"
    path = fake_home / "hermes-router-canonical.jsonl"
    monkeypatch.setattr(canonical, "_store_path", lambda: str(path))
    canonical.clear_for_tests()
    try:
        assert canonical.commit_canonical_event(
            "s9", "tm1", "DELIVERED", "rh1", grounded=True,
            route_id="route-abc-123") is True
        with open(path, "r", encoding="utf-8") as fh:
            rec = json.loads(fh.readline())
        assert rec["grounded"] is True
        assert rec["route_id"] == "route-abc-123"
        # defaults preserved for the reconcile lane (marker-agnostic)
        assert canonical.commit_canonical_event(
            "s10", "", "DELIVERED-2", "rh2") is True
        rec2 = json.loads(open(path, "r", encoding="utf-8").readlines()[1])
        assert rec2["grounded"] is False and rec2["route_id"] == ""
    finally:
        canonical.clear_for_tests()


# ---------------------------------------------------------------------------
# render_grounded route-log line
# ---------------------------------------------------------------------------


def test_render_grounded_log_line_emitted(tmp_path, monkeypatch):
    _seed_answer("s-g9", tmp_path)
    _prime_post("s-g9", "m1")
    monkeypatch.setattr(plugin.router, "call", lambda p, **k: RENDERED)
    logged = []

    def _fake_log(event, **fields):
        if fields.get("event_detail") == "render_grounded":
            logged.append(fields)

    monkeypatch.setattr(plugin, "_log_route", _fake_log)
    plugin.on_transform_llm_output(
        response_text=REFUSAL, session_id="s-g9", model="m1")
    assert len(logged) == 1
    assert logged[0]["grounded"] is True
    assert logged[0]["ask_chars"] == len(CONTINUATION)
    assert logged[0]["answer_chars"] == len(ANSWER)
    assert logged[0]["session_id"] == "s-g9"


def test_render_grounded_log_ungrounded_shape(tmp_path, monkeypatch):
    _prime_post("s-g10", "m1")
    monkeypatch.setattr(plugin.router, "call", lambda p, **k: RENDERED)
    logged = []

    def _fake_log(event, **fields):
        if fields.get("event_detail") == "render_grounded":
            logged.append(fields)

    monkeypatch.setattr(plugin, "_log_route", _fake_log)
    plugin.on_transform_llm_output(
        response_text=REFUSAL, session_id="s-g10", model="m1")
    assert len(logged) == 1
    assert logged[0]["grounded"] is False
    assert logged[0]["answer_chars"] == 0