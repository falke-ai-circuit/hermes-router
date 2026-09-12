"""Leg 9 (blueprint H1): per-agent cap spend keys by AGENT IDENTITY
(profile), not session. Evidence (conductor, researcher sidecar): 'agents'
keys were SESSION ids — a fresh session always read 0.0 spend, so the
per-agent daily cap NEVER bound across sessions (live: ~0.023 agent spend
across sessions, T6b agent-initiated request_routing fired anyway).

Leg-9 contract:
- Spend accumulates per AGENT per UTC day across ALL of the agent's
  sessions; a FRESH session with prior agent-level spend denies under cap.
- Separate agents are independent.
- initiator=user claims stay exempt at the gate (blueprint).
- Boundary exactly-at-cap passes (H7.7); beyond denies with visible banner.
- agent_id is greppable in the denied_cap event (field agent_id).

SID convention: "s-leg9".
"""
import json

import pytest

import hermes_router as plugin
from hermes_router import config_access, route_gate, routing_caps, router_core, state

SID = "s-leg9"
SID2 = "s-leg9-fresh-session"


@pytest.fixture()
def caps_tmp(tmp_path):
    p = str(tmp_path / "routing-state.json")
    routing_caps._test_reset(p)
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    state.reset_turn_identity(SID2)
    route_gate.clear_declared(SID2)
    route_gate.clear_turn_claims(SID2)
    yield p
    routing_caps._test_reset(None)


def test_spend_in_session_a_denies_fresh_session_b_same_agent(monkeypatch, caps_tmp):
    """THE live regression: spend recorded while working in session A; a
    FRESH session B (same agent) must see the accumulated spend and DENY."""
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": 0.001})
    routing_caps.record_agent_spend(routing_caps.agent_identity(), 0.5,
                                    initiator="agent", lane="higher-pre")
    assert routing_caps.agent_spend(routing_caps.agent_identity()) == pytest.approx(0.5)
    # Fresh session B — NOT session A: agent-initiated request_routing must
    # be denied by the gate cap check keyed on AGENT identity.
    allowed, spend, cap = routing_caps.gate_cap_check(
        routing_caps.agent_identity(), est_cost=0.0)
    assert allowed is False
    assert spend == pytest.approx(0.5)
    assert cap == pytest.approx(0.001)
    # And through the actual tool path (T6b):
    from hermes_router import router_tools
    res = json.loads(router_tools.router_control(
        action="request_routing", lane="shadow", session_id=SID2))
    assert res["ok"] is False and res["error"] == "cap_denied"
    assert res.get("agent_id") == routing_caps.agent_identity()


def test_separate_agents_independent(monkeypatch, caps_tmp):
    """Agent identity scoping: a DIFFERENT agent id's spend does not bind
    this agent (bucket isolation)."""
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": 0.001})
    routing_caps.record_agent_spend("other-agent", 0.5,
                                    initiator="agent", lane="higher-pre")
    allowed, spend, _ = routing_caps.gate_cap_check(
        routing_caps.agent_identity(), est_cost=0.0)
    assert allowed is True  # THIS agent has no spend
    assert spend == 0.0
    assert routing_caps.agent_spend("other-agent") == pytest.approx(0.5)


def test_agent_identity_nonempty_and_stable(monkeypatch, caps_tmp):
    """The derived agent id is non-empty, stable across calls, and session-
    independent (the same value regardless of which session_id is passed)."""
    a = routing_caps.agent_identity()
    b = routing_caps.agent_identity()
    assert a and a.strip()
    assert a == b
    # Greppable in the denied_cap sidecar event:
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": 0.001})
    routing_caps.record_agent_spend(a, 0.5, initiator="agent")
    from hermes_router import router_tools
    res = json.loads(router_tools.router_control(
        action="request_routing", lane="shadow", session_id=SID))
    assert res["ok"] is False
    events = routing_caps.read_denied_events(limit=5)
    assert events and events[0].get("agent_id") == a


def test_gate_denies_agent_claim_fresh_session_with_prior_spend(
        monkeypatch, caps_tmp):
    """End-to-end through decide_turn: fresh session, agent-initiated
    declared claim, prior agent spend over cap -> cap_denied (not routed)."""
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": 0.001})
    routing_caps.record_agent_spend(routing_caps.agent_identity(), 0.5,
                                    initiator="agent")
    route_gate.register_declared(SID2, route_gate.LANE_SHADOW,
                                 route_gate.SOURCE_DECLARED_AGENT)
    d = route_gate.decide_turn({
        "content": "work the problem", "request": {"messages": []},
        "context": {}, "session_id": SID2, "auto_shape": None,
        "claim": True})
    assert d.route is False and d.reason == "cap_denied"
    # User-initiated claims stay EXEMPT (blueprint) even over cap — a user
    # PHRASE in the turn content claims + routes while over cap:
    route_gate.clear_declared(SID2)
    route_gate.clear_turn_claims(SID2)
    d2 = route_gate.decide_turn({
        "content": "route this through your shadow: review my plan",
        "request": {"messages": []},
        "context": {}, "session_id": SID2, "auto_shape": None,
        "claim": True})
    assert d2.route is True  # user exempt from the per-agent cap


def test_boundary_exactly_at_cap_passes(monkeypatch, caps_tmp):
    """H7.7 boundary: projected == cap passes; beyond denies."""
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": 1.0})
    routing_caps.record_agent_spend(routing_caps.agent_identity(), 1.0,
                                    initiator="agent")
    allowed, spend, _ = routing_caps.gate_cap_check(
        routing_caps.agent_identity(), est_cost=0.0)
    assert allowed is True  # exactly at cap
    assert spend == pytest.approx(1.0)
    allowed2, _, _ = routing_caps.gate_cap_check(
        routing_caps.agent_identity(), est_cost=0.01)
    assert allowed2 is False  # beyond cap denies
