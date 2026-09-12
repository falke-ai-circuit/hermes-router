"""Leg 1 — unified route gate tests (BLUEPRINT-request-routing-2026-09-12).

Covers the reviewer-mandated unit gates:
  - gate ordering inside on_llm_request (H6): the gate fires BEFORE any
    classification
  - precedence chain (H4): sentinel -> skip-anchor -> declared -> auto
  - input-side echo guard (H7.2)
  - double-declare dedupe (H7.5)
  - kill-switch knob on_demand_routing (H7.4)
  - state-leak guard claim_execution_guard (H7.1)
  - fail-open: the gate never raises
"""
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import route_gate  # noqa: E402
from hermes_router import state  # noqa: E402


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
    route_gate.clear_declared("s-gate")
    route_gate.clear_declared("")
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield
    state.clear()
    route_gate.clear_declared("s-gate")
    route_gate.clear_declared("")


# ---------------------------------------------------------------------------
# Precedence chain (H4) — unit-tested directly on decide_turn
# ---------------------------------------------------------------------------


def test_precedence_sentinel_beats_declared(monkeypatch):
    monkeypatch.setattr(plugin, "_frame_sentinel_check", lambda c: True)
    d = route_gate.decide_turn({
        "content": "ask your higher self", "request": _request("x"),
        "context": {}, "session_id": "s-gate", "auto_shape": None})
    assert d.route is False and d.reason == "sentinel_firewall"


def test_precedence_skip_anchor_beats_declared():
    d = route_gate.decide_turn({
        "content": "skip anchor\nask your higher self", "request": _request("x"),
        "context": {}, "session_id": "s-gate", "auto_shape": None})
    assert d.route is False and d.reason == "override_skip"


def test_precedence_declared_beats_auto_shape():
    calls = []

    def _auto():
        calls.append(1)
        return route_gate.GateDecision(route=True, lane=route_gate.LANE_HIGHER_PRE,
                                       source=route_gate.SOURCE_AUTO,
                                       reason="complexity_claim")

    d = route_gate.decide_turn({
        "content": "ask your higher self", "request": _request("x"),
        "context": {}, "session_id": "s-gate", "auto_shape": _auto})
    assert d.route is True
    assert d.source == route_gate.SOURCE_DECLARED_USER
    assert d.lane == route_gate.LANE_HIGHER_PRE
    assert not calls  # classification NOT consulted — gate claimed first


def test_precedence_auto_shape_when_no_declaration():
    def _auto():
        return route_gate.GateDecision(route=True, lane=route_gate.LANE_SHADOW,
                                       source=route_gate.SOURCE_AUTO,
                                       reason="complexity_claim")

    d = route_gate.decide_turn({
        "content": "ordinary ask", "request": _request("x"),
        "context": {}, "session_id": "s-gate", "auto_shape": _auto})
    assert d.route is True and d.source == route_gate.SOURCE_AUTO


# ---------------------------------------------------------------------------
# Echo guard (H7.2) — quoted/echoed trigger phrases are inert
# ---------------------------------------------------------------------------


def test_echo_guard_quoted_phrase_no_route():
    # User message QUOTING a trigger phrase in discussion -> no route (H7.2).
    content = ('in the router docs it says the user can say "> ask your '
               'higher self" to trigger a consult')
    d = route_gate.decide_turn({
        "content": content, "request": _request(content),
        "context": {}, "session_id": "s-gate", "auto_shape": None})
    assert d.route is False


def test_echo_guard_mid_prose_phrase_no_route():
    # Phrase embedded in prose (not a standalone directive line) -> inert.
    content = "should I ask your higher self about this design?"
    assert route_gate.detect_declared_user(content) is None


def test_declared_user_directive_line_routes():
    assert route_gate.detect_declared_user("ask your higher self") == \
        route_gate.LANE_HIGHER_PRE
    assert route_gate.detect_declared_user("route this through your shadow") == \
        route_gate.LANE_SHADOW


def test_echo_guard_case_and_markdown_directive_still_matches():
    assert route_gate.detect_declared_user("**Ask Your Higher Self**") == \
        route_gate.LANE_HIGHER_PRE


# ---------------------------------------------------------------------------
# Double-declare dedupe (H7.5)
# ---------------------------------------------------------------------------


def test_double_declare_agent_then_user_one_claim():
    assert route_gate.register_declared("s-gate", route_gate.LANE_SHADOW,
                                        route_gate.SOURCE_DECLARED_AGENT) is True
    d = route_gate._declared_decision("ask your higher self", "s-gate")
    assert d.route is True
    assert d.reason == "declared_deduped"
    assert d.source == route_gate.SOURCE_DECLARED_AGENT  # first declaration wins


def test_double_declare_same_agent_twice_one_claim():
    assert route_gate.register_declared("s-gate", route_gate.LANE_HIGHER_PRE) is True
    assert route_gate.register_declared("s-gate", route_gate.LANE_HIGHER_PRE) is False
    existing = route_gate.peek_declared("s-gate")
    assert existing["lane"] == route_gate.LANE_HIGHER_PRE


# ---------------------------------------------------------------------------
# Kill-switch (H7.4)
# ---------------------------------------------------------------------------


def test_kill_switch_off_disables_declared_only(monkeypatch):
    from hermes_router import config_access

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"on_demand_routing": "off"})
    d = route_gate.decide_turn({
        "content": "ask your higher self", "request": _request("x"),
        "context": {}, "session_id": "s-gate", "auto_shape": None})
    assert d.route is False  # declared input dead...


def _auto_hit():
    return route_gate.GateDecision(route=True, lane=route_gate.LANE_HIGHER_PRE,
                                   source=route_gate.SOURCE_AUTO,
                                   reason="complexity_claim")


def test_kill_switch_off_leaves_auto_lanes_alive(monkeypatch):
    from hermes_router import config_access

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"on_demand_routing": "off"})
    d = route_gate.decide_turn({
        "content": "ordinary ask", "request": _request("x"),
        "context": {}, "session_id": "s-gate", "auto_shape": _auto_hit})
    assert d.route is True and d.source == route_gate.SOURCE_AUTO


def test_kill_switch_on_by_default(monkeypatch):
    from hermes_router import config_access

    monkeypatch.setattr(config_access, "router_section", lambda: {})
    assert route_gate.on_demand_routing_enabled() is True


# ---------------------------------------------------------------------------
# State-leak guard (H7.1)
# ---------------------------------------------------------------------------


def test_claim_execution_guard_releases_banner_and_claim(monkeypatch):
    released = []
    from hermes_router import debug_banner

    monkeypatch.setattr(debug_banner, "consume_parked_banner",
                        lambda sid: released.append(sid) or "b")
    route_gate.register_declared("s-gate", route_gate.LANE_HIGHER_PRE)
    with pytest.raises(RuntimeError):
        with route_gate.claim_execution_guard("s-gate"):
            raise RuntimeError("provider down")
    assert released == ["s-gate"]            # parked banner released
    assert route_gate.peek_declared("s-gate") is None  # claim dropped


def test_claim_execution_guard_passes_through_on_success(monkeypatch):
    from hermes_router import debug_banner

    monkeypatch.setattr(debug_banner, "consume_parked_banner",
                        lambda sid: pytest.fail("must not release on success"))
    with route_gate.claim_execution_guard("s-gate"):
        pass


# ---------------------------------------------------------------------------
# Fail-open: the gate NEVER raises
# ---------------------------------------------------------------------------


def test_gate_fail_open_on_internal_error(monkeypatch):
    def _boom(*a, **k):
        raise ValueError("boom")

    monkeypatch.setattr(plugin, "_frame_sentinel_check", _boom)
    d = route_gate.decide_turn({
        "content": "ask your higher self", "request": _request("x"),
        "context": {}, "session_id": "s-gate", "auto_shape": None})
    assert d.route is False


# ---------------------------------------------------------------------------
# Ordered gate test (H6) — gate fires BEFORE any classification in
# on_llm_request
# ---------------------------------------------------------------------------


def test_gate_fires_before_classification_inside_on_llm_request(monkeypatch):
    """H6 ordered test: a declared-user claim must claim the turn and the
    classification scan must NEVER run (gate decision precedes it)."""
    scan_calls = []

    def _spy_scan(content, *, patterns, case_sensitive=False):
        scan_calls.append(content)
        return []

    monkeypatch.setattr(plugin.classifier, "scan_pre", _spy_scan)
    dispatch_calls = []

    def _spy_dispatch(content, session_id, model):
        dispatch_calls.append(content)
        return False

    monkeypatch.setattr(plugin._dispatcher_pre, "_dispatch_pass", _spy_dispatch)
    result = plugin.on_llm_request(
        request=_request("ask your higher self"),
        original_request=_request("ask your higher self"),
        session_id="s-gate")
    # Gate claimed the turn -> pass-through envelope, and NOTHING downstream
    # of the gate (classification scan, complexity dispatch) executed.
    assert result == {}
    assert not scan_calls
    assert not dispatch_calls
    # Leg 3 consume-on-claim semantics: the declared claim is CONSUMED by
    # the gate's claim (one consult per turn) — no fresh claim remains.
    assert route_gate.peek_declared("s-gate") is None


def test_audit_delivery_is_gate_branch_not_bypass(monkeypatch):
    """F4/H3: the audit-delivery envelope flows THROUGH the gate fence as an
    explicit no-route branch — delivered exactly as before the gate existed."""
    from hermes_router import dispatcher_pre

    env = {"request": _request("delivered")}
    monkeypatch.setattr(dispatcher_pre, "_audit_delivery_pass",
                        lambda req, ctx: env)
    monkeypatch.setattr(plugin.classifier, "scan_pre",
                        lambda *a, **k: pytest.fail("no classification on audit delivery"))
    result = plugin.on_llm_request(
        request=_request("anything"), original_request={}, session_id="s-gate")
    assert result == env
