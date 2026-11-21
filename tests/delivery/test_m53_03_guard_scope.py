"""M53-03 — a routing request claims ONLY the turn it was made in.

route_gate._lane_request_from_messages scanned every assistant tool_calls entry
in the prompt history with no recency bound, so ONE request_routing call made
every later turn look like a fresh lane request — a self-perpetuating loop that
fired the bare-call guard on stale history.

T-R1 is the bug (fails at the parent); T-R2 proves a genuine current-turn
request still fires; T-R3 pins the empty case.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router.route_gate as rg  # noqa: E402


def _call(lane="shadow"):
    return {"role": "assistant", "tool_calls": [{
        "id": "c1", "type": "function",
        "function": {"name": "router_control",
                     "arguments": json.dumps(
                         {"action": "request_routing", "lane": lane})}}]}


def _user(text):
    return {"role": "user", "content": text}


@pytest.fixture(autouse=True)
def _only_source_three(monkeypatch):
    """Sources #1/#2 are out of scope for this fix — neutralise them so the
    test measures source #3 alone."""
    monkeypatch.setattr(rg, "_peek_turn_claim", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(rg, "peek_declared", lambda *a, **k: None, raising=False)


def _ask(msgs, sid):
    return rg.lane_request_for_turn(session_id=sid, content="x", model="m",
                                    request={"messages": msgs})


def test_t_r1_stale_request_does_not_fire():
    msgs = [_user("ask one"), _call(), _user("ask two")]
    assert _ask(msgs, "m53-03-tr1") is None, \
        "a previous turn's request must not claim this turn"


def test_t_r2_current_turn_request_fires():
    msgs = [_user("ask one"), _user("ask two"), _call(lane="shadow")]
    got = _ask(msgs, "m53-03-tr2")
    assert isinstance(got, dict), got
    assert got.get("lane") == "shadow"


def test_t_r3_no_tool_calls_is_none():
    assert _ask([_user("just a question")], "m53-03-tr3") is None
