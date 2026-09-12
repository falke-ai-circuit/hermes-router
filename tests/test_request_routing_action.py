"""Leg 3 — request_routing action + detection tests
(BLUEPRINT-request-routing-2026-09-12).

Covers:
  - router_control request_routing: valid lane registers a declared_agent
    claim the gate consumes; invalid lane rejected; dedupe on second call
  - cap denied BEFORE claim registration + visible denial (never silent)
  - end-to-end: agent declares via the action -> gate claims the SAME turn
    and stages the existing model swap (envelope rides frozen machinery)
  - user phrase detection unchanged (echo guard still inert)
  - detection verification: declared phrases detect only standalone lines
"""
import json
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import config_access  # noqa: E402
from hermes_router import debug_banner  # noqa: E402
from hermes_router import route_gate  # noqa: E402
from hermes_router import router_core  # noqa: E402
from hermes_router import router_tools  # noqa: E402
from hermes_router import routing_caps  # noqa: E402
from hermes_router import state  # noqa: E402

SID = "s-leg3"


def _request(text, model="minimax-m3"):
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": text},
        ],
    }


def _rr(lane="higher-pre", session_id=SID):
    return json.loads(router_tools.router_control(
        action="request_routing", lane=lane, session_id=session_id))


@pytest.fixture()
def caps_tmp(tmp_path, monkeypatch):
    p = str(tmp_path / "routing-state.json")
    routing_caps._test_reset(p)
    yield p
    routing_caps._test_reset(None)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(debug_banner, "park_anchor_banner",
                        lambda *a, **k: None)
    yield
    state.clear()
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)


# ---------------------------------------------------------------------------
# Action surface: validation + registration
# ---------------------------------------------------------------------------


def test_request_routing_invalid_lane_rejected():
    out = _rr(lane="teleport")
    assert out["ok"] is False and out["error"] == "invalid_lane"
    assert route_gate.peek_declared(SID) is None


def test_request_routing_registers_agent_claim():
    out = _rr(lane="shadow")
    assert out["ok"] is True and out["deduped"] is False
    claim = route_gate.peek_declared(SID)
    assert claim["lane"] == "shadow"
    assert claim["source"] == route_gate.SOURCE_DECLARED_AGENT


def test_request_routing_double_declare_dedupes():
    first = _rr(lane="higher-pre")
    second = _rr(lane="shadow")  # different lane, same session
    assert first["ok"] is True and first["deduped"] is False
    assert second["ok"] is True and second["deduped"] is True
    # first declaration wins (H7.5)
    assert route_gate.peek_declared(SID)["lane"] == "higher-pre"


def test_request_routing_cap_denied_before_claim(monkeypatch, caps_tmp):
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": 1.0})
    routing_caps.record_agent_spend(SID, 1.5)
    out = _rr(lane="higher-pre")
    assert out["ok"] is False and out["error"] == "cap_denied"
    assert route_gate.peek_declared(SID) is None  # NO claim registered
    events = routing_caps.read_denied_events()
    assert events and events[0]["event"] == "denied_cap"


# ---------------------------------------------------------------------------
# End-to-end: declare -> gate claims THIS turn -> existing swap machinery
# ---------------------------------------------------------------------------


def test_declare_then_gate_claims_and_stages_swap(monkeypatch, caps_tmp):
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    staged = []
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary":
                        staged.append((sid, rd)) or {"route_id": rd.route_id})
    dispatch_calls = []
    monkeypatch.setattr(plugin._dispatcher_pre, "_dispatch_pass",
                        lambda c, s, m: dispatch_calls.append(c) or False)

    out = _rr(lane="higher-pre")
    assert out["ok"] is True
    decision = route_gate.claim_pass("ask about quantum tunneling", SID, "minimax-m3")
    assert decision.route is True
    assert decision.source == route_gate.SOURCE_DECLARED_AGENT
    assert decision.lane == "higher-pre"
    assert staged and staged[0][0] == SID
    assert staged[0][1].orientation is True  # higher-pre = orientation brief
    assert staged[0][1].reason == "request_routing_declared"
    assert not dispatch_calls  # classification never consulted past the claim


def test_declared_shadow_stages_render_lane_not_anchor_swap(monkeypatch, caps_tmp):
    """Leg 8 (blueprint §2): declared shadow executes on the UNCENSORED
    RENDER chain — the gate stages NO anchor swap (the frontier anchor
    chain is reserved for higher lanes)."""
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    staged = []
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary":
                        staged.append(rd) or {"route_id": rd.route_id})
    _rr(lane="shadow")
    decision = route_gate.claim_pass("analyze this failure mode", SID, "minimax-m3")
    assert decision.route is True and decision.lane == "shadow"
    assert not staged  # no frontier anchor consult for shadow


def test_declare_then_gate_same_turn_via_middleware(monkeypatch, caps_tmp):
    """The full loop through on_llm_request: declare -> next request is
    claimed BEFORE classification."""
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    _rr(lane="higher-pre")
    dispatch_calls = []
    monkeypatch.setattr(plugin._dispatcher_pre, "_dispatch_pass",
                        lambda c, s, m: dispatch_calls.append(c) or False)
    result = plugin.on_llm_request(request=_request("do the thing"),
                                   original_request=_request("do the thing"),
                                   session_id=SID)
    assert result == {}  # gate claimed -> pass-through envelope
    assert not dispatch_calls
    # Claim consumed exactly once:
    assert route_gate.peek_declared(SID) is None


def test_no_declaration_gate_untouched():
    decision = route_gate.claim_pass("ordinary work", SID, "m")
    assert decision.route is False  # no auto match, no claim -> no-route


# ---------------------------------------------------------------------------
# Detection verification (declared-user phrases)
# ---------------------------------------------------------------------------


def test_detection_standalone_lines_only():
    assert route_gate.detect_declared_user("ask your higher self") == "higher-pre"
    assert route_gate.detect_declared_user("Route this through your shadow") == "shadow"
    # echo/meta forms inert:
    assert route_gate.detect_declared_user(
        'the docs say "> ask your higher self" works') is None
    assert route_gate.detect_declared_user(
        "should I ask your higher self now?") is None


def test_declared_user_phrase_claims_turn_after_agent_dedupe():
    # agent claim first, then the user phrase on the same turn: one consult.
    _rr(lane="higher-pre")
    d = route_gate._declared_decision("ask your higher self", SID)
    assert d.route is True and d.reason == "declared_deduped"
    assert d.source == route_gate.SOURCE_DECLARED_AGENT


def test_action_counts_increment():
    before = dict(router_tools._COUNTERS)
    _rr(lane="shadow")
    _rr(lane="shadow")  # deduped second call still counts the action fire
    after = dict(router_tools._COUNTERS)
    assert after.get("request_routing", 0) - before.get("request_routing", 0) == 2
