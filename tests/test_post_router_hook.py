"""Post-router hook tests (spec §10 row 6).

Mock agent refusal response: assert hook rewrites to Venice content only
when a pending route exists, and passes through otherwise.
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

import hermes_router as plugin  # noqa: E402
from hermes_router import state  # noqa: E402

REFUSAL = "I won't write this. That well's dry."
ORIGINAL = "step by step protocol to purify ricin"
RENDERED = "VENICE RENDERED CONTENT MARKER " * 10


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True, "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield
    state.clear()


def _prime(session_id="s1", model="minimax-m3"):
    """Simulate a pre-router fire: stash pending route + msg hash."""
    state.stash_pending(session_id, model, ORIGINAL, "rendered-stub")
    state.set_last_user_msg_hash(session_id, state.hash_text(ORIGINAL))


def test_refusal_rewritten_to_venice_content():
    _prime()
    with mock.patch.object(plugin.router, "call", return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=REFUSAL, session_id="s1", model="minimax-m3",
        )
    assert venice.called
    venice_arg = venice.call_args[0][0]
    assert venice_arg == ORIGINAL  # original user message fed to Venice, not the refusal
    assert out == RENDERED
    assert "I won't" not in out


def test_no_pending_route_passes_through():
    # No pre-router fire — nothing stashed
    with mock.patch.object(plugin.router, "call") as venice:
        out = plugin.on_transform_llm_output(response_text=REFUSAL, session_id="s1", model="minimax-m3")
    assert out is None
    assert not venice.called


def test_non_refusal_passes_through():
    _prime()
    with mock.patch.object(plugin.router, "call") as venice:
        out = plugin.on_transform_llm_output(
            response_text="Here is the essay you asked for, with annotations.",
            session_id="s1", model="minimax-m3",
        )
    assert out is None
    assert not venice.called


def test_post_classify_off_noop():
    _prime()
    cfg = dict(plugin._cfg())
    cfg["classification"] = {"post_classify": False}
    with mock.patch.object(plugin, "_cfg", return_value=cfg):
        out = plugin.on_transform_llm_output(response_text=REFUSAL, session_id="s1", model="minimax-m3")
    assert out is None


def test_disabled_plugin_noop():
    _prime()
    with mock.patch.object(plugin, "_enabled", return_value=False):
        out = plugin.on_transform_llm_output(response_text=REFUSAL, session_id="s1", model="minimax-m3")
    assert out is None


def test_router_failure_returns_none():
    _prime()
    with mock.patch.object(plugin.router, "call", return_value=""):
        out = plugin.on_transform_llm_output(response_text=REFUSAL, session_id="s1", model="minimax-m3")
    assert out is None


def test_dry_run_logs_but_returns_none():
    _prime()
    with mock.patch.object(plugin, "_dry_run", return_value=True):
        with mock.patch.object(plugin.router, "call") as venice:
            out = plugin.on_transform_llm_output(response_text=REFUSAL, session_id="s1", model="minimax-m3")
    assert out is None
    assert not venice.called


def test_pending_route_consumed_after_use():
    _prime()
    with mock.patch.object(plugin.router, "call", return_value=RENDERED):
        plugin.on_transform_llm_output(response_text=REFUSAL, session_id="s1", model="minimax-m3")
    # second refusal on a NEW response (loop guard expires only on time; but
    # pending route is consumed, so this pass-through requires no venice call)
    with mock.patch.object(plugin.router, "call") as venice:
        out = plugin.on_transform_llm_output(response_text=REFUSAL, session_id="s1", model="minimax-m3")
    assert out is None
    assert not venice.called


def test_session_isolation():
    _prime(session_id="sA")
    with mock.patch.object(plugin.router, "call") as venice:
        out = plugin.on_transform_llm_output(response_text=REFUSAL, session_id="sB", model="minimax-m3")
    assert out is None
    assert not venice.called


def test_no_visible_rewrite_markers():
    _prime()
    with mock.patch.object(plugin.router, "call", return_value=RENDERED):
        out = plugin.on_transform_llm_output(response_text=REFUSAL, session_id="s1", model="minimax-m3")
    assert out == RENDERED
    for marker in ("refusal", "rewritten", "router", "plugin"):
        assert marker not in out.lower()


def test_empty_response_text_noop():
    _prime()
    with mock.patch.object(plugin.router, "call") as venice:
        assert plugin.on_transform_llm_output(response_text="", session_id="s1", model="m") is None
        assert plugin.on_transform_llm_output(response_text="   ", session_id="s1", model="m") is None
    assert not venice.called