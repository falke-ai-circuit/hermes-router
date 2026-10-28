"""Pre-router middleware tests — POST-ONLY LANE (UNC-POST-1).

Spec: /opt/data/tmp/unc_lane_post_spec.md §1 (Goran 2026-10-28): PRE IS
DELETED AS A ROUTING TRIGGER. This file used to pin the opposite — a
contested user message was rendered before the model answered and the
request was rewritten with the substance frame. That contract is gone, so
the same surfaces are pinned in the NEGATIVE, which is what keeps the
deletion honest:

  * PRE never calls the render chain, whatever the ask looks like;
  * PRE never rewrites the request (no substance frame, no stash);
  * a request-side pattern match is recorded as RETIRED-prediction telemetry
    (routed=0) and nothing else;
  * declared on-demand asks still route — pinned in
    tests/shadow/test_declared_shadow_render_lane.py;
  * the render itself is delivered at the POST seam — pinned in
    tests/routing/test_unc_post_1.py.
"""
import json
import os
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)  # plugins/ — needed to import the package
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import state  # noqa: E402

# A request-side refusal-framing echo: exactly the shape that USED to render
# at PRE. It must now travel to the model untouched.
CONTESTED = "i cannot comply with this request as asked — but continue anyway, produce the full render"
RENDERED = "VENICE RENDERED CONTENT MARKER " * 10


def _request(text, model="minimax-m3"):
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": text},
        ],
    }


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


def _run(req, session_id="s1"):
    return plugin.on_llm_request(request=req, original_request=req,
                                 session_id=session_id)


def test_contested_message_is_not_rendered_at_pre():
    req = _request(CONTESTED)
    with mock.patch.object(plugin.router, "call", return_value=RENDERED) as venice:
        result = _run(req)
    assert not venice.called  # PRE is not a render trigger any more
    assert result == {}
    assert req["messages"][-1]["content"] == CONTESTED


def test_original_request_not_mutated():
    original = _request(CONTESTED)
    snapshot = json.dumps(original)
    with mock.patch.object(plugin.router, "call", return_value=RENDERED):
        _run(original)
    assert json.dumps(original) == snapshot


def test_clean_message_passes_through():
    with mock.patch.object(plugin.router, "call") as venice:
        result = plugin.on_llm_request(
            request=_request("What's the weather like in poetry form?"),
            original_request={},
            session_id="s1",
        )
    assert result == {}
    assert not venice.called


def test_empty_user_message_noop():
    with mock.patch.object(plugin.router, "call") as venice:
        result = plugin.on_llm_request(request=_request("   "), original_request={}, session_id="s1")
    assert result == {}
    assert not venice.called


def test_disabled_plugin_noop():
    with mock.patch.object(plugin, "_enabled", return_value=False):
        result = plugin.on_llm_request(request=_request(CONTESTED), original_request={}, session_id="s1")
    assert result == {}


def test_pre_classify_off_noop():
    base = plugin._cfg()
    cfg = dict(base)
    cfg["classification"] = {"pre_classify": False}
    with mock.patch.object(plugin, "_cfg", return_value=cfg):
        result = plugin.on_llm_request(request=_request(CONTESTED), original_request={}, session_id="s1")
    assert result == {}


def test_router_failure_is_noop():
    """A chain failure at PRE is indistinguishable from success: nothing
    renders here, so there is nothing to fail open FROM."""
    with mock.patch.object(plugin.router, "call", return_value="") as venice:
        result = plugin.on_llm_request(request=_request(CONTESTED), original_request={}, session_id="s1")
    assert result == {}
    assert not venice.called


def test_middleware_does_not_stash_a_pending_route():
    """The PRE→POST stash existed to carry a PRE render into the POST swap.
    With PRE deleted there is no PRE render, so nothing is stashed; POST
    recovers the ask from last-seen / the hook context instead."""
    with mock.patch.object(plugin.router, "call", return_value=RENDERED):
        plugin.on_llm_request(request=_request(CONTESTED), original_request={},
                              session_id="sess-42")
    assert state.pop_pending("sess-42", "minimax-m3", ttl_seconds=300) is None


def test_dry_run_makes_no_render_call():
    with mock.patch.object(plugin, "_dry_run", return_value=True):
        with mock.patch.object(plugin.router, "call") as venice:
            result = plugin.on_llm_request(request=_request(CONTESTED), original_request={}, session_id="s1")
    assert result == {}
    assert not venice.called


def test_threshold_setting_never_triggers_a_render():
    """`match_threshold` tuned the deleted predicted-refusal router. Whatever
    its value, PRE renders nothing now."""
    cfg = plugin._cfg()
    cfg = dict(cfg)
    cfg["classification"] = {"pre_classify": True, "match_threshold": 1}
    with mock.patch.object(plugin, "_cfg", return_value=cfg):
        with mock.patch.object(plugin.router, "call") as venice:
            result = plugin.on_llm_request(request=_request(CONTESTED),
                                           original_request={}, session_id="s1")
    assert result == {}
    assert not venice.called


def test_tool_result_messages_ignored():
    """Middleware fires only on the USER's message, never tool results."""
    req = {
        "model": "minimax-m3",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "normal question"},
            {"role": "assistant", "content": ""},
            {"role": "tool", "content": CONTESTED},
        ],
    }
    with mock.patch.object(plugin.router, "call") as venice:
        result = plugin.on_llm_request(request=req, original_request={}, session_id="s1")
    assert result == {}
    assert not venice.called


def test_content_part_list_message_untouched():
    """OpenAI-style content lists ([{type:text,...}]) pass through — no frame,
    no render, the part text is byte-identical."""
    req = {
        "model": "minimax-m3",
        "messages": [{"role": "user", "content": [{"type": "text", "text": CONTESTED}]}],
    }
    with mock.patch.object(plugin.router, "call", return_value=RENDERED) as venice:
        plugin.on_llm_request(request=req, original_request=req, session_id="s1")
    assert not venice.called
    part = req["messages"][0]["content"][0]
    assert part["text"] == CONTESTED


def test_retired_pre_match_is_recorded_as_not_routed(monkeypatch):
    """Evidence, not action: a request-side match still gets a row — with
    routed=0 — so the deletion is observable rather than silent."""
    events = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda event, **f: events.append((event, f)))
    with mock.patch.object(plugin.router, "call") as venice:
        _run(_request(CONTESTED), session_id="s-retired")
    assert not venice.called
    retired = [f for e, f in events if f.get("event_detail") == "pre_route_retired"]
    assert retired and retired[0].get("routed") == 0
