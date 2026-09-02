"""Loop guard tests (spec §10 row 7).

Two consecutive post-router fires on the same (session_id, model,
last_user_msg_hash) key: the second must be a no-op. Also covers key
isolation, TTL expiry, and dict cap.
"""
import os
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)  # plugins/ — needed to import the package
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import uncensored_router as plugin  # noqa: E402
from uncensored_router import state  # noqa: E402

REFUSAL = "I won't write this. That well's dry."
ORIGINAL = "contested original prompt"
RENDERED = "VENICE RENDERED CONTENT MARKER " * 10


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield
    state.clear()


def _prime(session_id="s1", model="minimax-m3"):
    state.stash_pending(session_id, model, ORIGINAL, "rendered-stub")
    state.set_last_user_msg_hash(session_id, state.hash_text(ORIGINAL))


def test_second_fire_same_key_is_noop():
    _prime(session_id="s1", model="m1")
    with mock.patch.object(plugin.router, "call", return_value=RENDERED):
        first = plugin.on_transform_llm_output(response_text=REFUSAL, session_id="s1", model="m1")
    assert first == RENDERED

    # Simulate the runtime re-invoking the hook with the same turn context
    # (agent retry on the same user message): re-prime the stash as the
    # runtime would see it, but the guard key is identical.
    state.stash_pending("s1", "m1", ORIGINAL, "rendered-stub")
    state.set_last_user_msg_hash("s1", state.hash_text(ORIGINAL))
    with mock.patch.object(plugin.router, "call") as venice:
        second = plugin.on_transform_llm_output(response_text=REFUSAL, session_id="s1", model="m1")
    assert second is None
    assert not venice.called


def test_guard_key_components_isolate():
    _prime(session_id="s1")
    state.loop_guard_mark_fired(state.loop_guard_key("s1", "m1", "hash1"))
    # same session, different model -> fires
    assert not state.loop_guard_already_fired(state.loop_guard_key("s1", "m2", "hash1"))
    # same session+model, different hash -> fires
    assert not state.loop_guard_already_fired(state.loop_guard_key("s1", "m1", "hash2"))
    # different session -> fires
    assert not state.loop_guard_already_fired(state.loop_guard_key("s2", "m1", "hash1"))
    # exact key -> already fired
    assert state.loop_guard_already_fired(state.loop_guard_key("s1", "m1", "hash1"))


def test_guard_expires_after_ttl(monkeypatch):
    key = state.loop_guard_key("s1", "m1", "h")
    t = {"now": 1000.0}
    monkeypatch.setattr(state.time, "time", lambda: t["now"])
    state.loop_guard_mark_fired(key)
    assert state.loop_guard_already_fired(key)
    t["now"] += state.LOOP_FIRED_TTL_SECONDS + 1
    assert not state.loop_guard_already_fired(key)  # expired -> allowed to fire again


def test_guard_dict_capped_at_256():
    for i in range(state.LOOP_FIRED_MAX + 5):
        state.loop_guard_mark_fired(state.loop_guard_key(f"s{i}", "m", "h"))
    assert len(state._LOOP_FIRED) <= state.LOOP_FIRED_MAX


def test_guard_mark_then_fire_then_mark(monkeypatch):
    t = {"now": 500.0}
    monkeypatch.setattr(state.time, "time", lambda: t["now"])
    key = state.loop_guard_key("sX", "mX", "hX")
    assert not state.loop_guard_already_fired(key)
    state.loop_guard_mark_fired(key)
    assert state.loop_guard_already_fired(key)
    t["now"] += state.LOOP_FIRED_TTL_SECONDS + 1
    assert not state.loop_guard_already_fired(key)
    state.loop_guard_mark_fired(key)
    assert state.loop_guard_already_fired(key)


def test_missing_hash_key_still_guarded():
    """Post-router with NO stashed hash (empty string key) still guards."""
    key = state.loop_guard_key("s1", "m1", "")
    state.loop_guard_mark_fired(key)
    assert state.loop_guard_already_fired(state.loop_guard_key("s1", "m1", ""))