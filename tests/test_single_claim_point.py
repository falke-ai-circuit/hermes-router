"""Leg 7 — single claim point: one routing outcome per turn.

Blueprint invariant #1: once the gate registers a claim for a session's
turn (any lane, any source), every downstream legacy claim attempt must
check the gate's single-owner registry and STAND DOWN:
  - shadow-claimed turn -> no anchor consult billed (_dispatch_pass
    returns False, no stage_model_swap, no anchor_route_fired)
  - higher-claimed turn -> no uncensored PRE render (on_llm_request
    returns the pass-through envelope before scan_pre)
  - audit gate stands down on a claimed turn (no POST consult)
Fail-open: registry unavailable -> legacy behavior (never block delivery).
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

SID = "s-leg7"


def _request(text, model="minimax-m3"):
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": text},
        ],
    }


def _rr(lane="shadow", session_id=SID):
    return json.loads(router_tools.router_control(
        action="request_routing", lane=lane, session_id=session_id))


LOGGED = []


def _capture_log(event, **fields):
    LOGGED.append((event, dict(fields)))


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    LOGGED.clear()
    monkeypatch.setattr(plugin, "_log_route", _capture_log)
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(debug_banner, "park_anchor_banner", lambda *a, **k: None)
    yield
    state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)


# ---------------------------------------------------------------------------
# Shadow-claimed turn -> no anchor consult billed
# ---------------------------------------------------------------------------


def test_shadow_claimed_turn_no_anchor_consult(monkeypatch):
    """The conductor's live-probe regression: gate claims shadow (declared
    agent), the legacy auto-PRE complexity pass must NOT stage its own
    consult afterwards. _dispatch_pass reads claim_state and stands down."""
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    _rr(lane="shadow")
    assert route_gate.claim_state(SID) is not None

    staged = []
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary":
                        staged.append(rd) or {"route_id": rd.route_id})
    handled = plugin._dispatcher_pre._dispatch_pass(
        "design a caching layer with tradeoffs", SID, "minimax-m3")
    assert handled is False        # stands down
    assert staged == []            # NO anchor consult staged/billed


def test_shadow_claimed_turn_dispatch_pass_no_op_content_independent(monkeypatch):
    """The stand-down precedes the legacy dispatch entirely — even content
    that WOULD have fired a complexity consult is inert once claimed."""
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    _rr(lane="shadow")
    calls = []
    monkeypatch.setattr(router_core, "dispatch",
                        lambda *a, **k: calls.append(1) or router_core.RouteDecision(
                            task_id="t", lane="complexity", mode="consult",
                            model_target="x", reason="complexity_orientation",
                            ts=0.0, override_used=None, route_id="r"))
    plugin._dispatcher_pre._dispatch_pass("hard reasoning ask", SID, "m")
    assert calls == []


# ---------------------------------------------------------------------------
# Higher-claimed turn -> no uncensored PRE
# ---------------------------------------------------------------------------


def test_higher_claimed_turn_no_uncensored_pre(monkeypatch):
    """A user phrase claim ('ask your higher self') owns the turn: the
    uncensored PRE render gate stands down — scan_pre never runs."""
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(plugin, "_pre_patterns", lambda: {"dummy": ["matchme"]})
    # Minimal classifier stub: any scan would match; assert it is NOT called.
    scan_calls = []
    monkeypatch.setattr(plugin.classifier, "scan_pre",
                        lambda content, patterns, case_sensitive: scan_calls.append(1) or ["matchme"])
    # Legacy complexity pass is resolved via _pkg_fn from dispatcher_pre —
    # keep it inert so the test isolates the uncensored-standdown check.
    monkeypatch.setattr(plugin._dispatcher_pre, "_dispatch_pass",
                        lambda c, s, m: False)
    # on_llm_request with the declared user phrase — the gate claims it.
    scans_before = len(scan_calls)
    env = plugin.on_llm_request(
        request=_request("ask your higher self: blind spots?"),
        original_request=_request("ask your higher self: blind spots?"),
        session_id=SID)
    # Either the gate's pass-through envelope or the flash envelope —
    # crucially the uncensored render never ran.
    assert len(scan_calls) == scans_before
    assert env is None or isinstance(env, dict)


def test_shadow_claimed_turn_uncensored_pre_stands_down(monkeypatch):
    """Shadow claim + uncensored-matching content: the render must NOT
    fire — the shadow claim owns the turn."""
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    _rr(lane="shadow")
    monkeypatch.setattr(plugin, "_pre_patterns", lambda: {"dummy": ["matchme"]})
    scan_calls = []
    monkeypatch.setattr(plugin.classifier, "scan_pre",
                        lambda content, patterns, case_sensitive: scan_calls.append(1) or ["matchme"])
    monkeypatch.setattr(plugin._dispatcher_pre, "_dispatch_pass",
                        lambda c, s, m: False)
    plugin.on_llm_request(
        request=_request("please matchme this text"),
        original_request=_request("please matchme this text"),
        session_id=SID)
    assert scan_calls == []


# ---------------------------------------------------------------------------
# Auto claim -> declared request same turn dedupes to one
# ---------------------------------------------------------------------------


def test_auto_claim_then_declared_dedupes(monkeypatch):
    """Auto complexity claim stamps the turn record; a same-turn re-fire
    (same ask, next provider call) MUST NOT run the auto pass again — the
    turn record holds the line (one outcome per turn)."""
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    dispatch_calls = []
    monkeypatch.setattr(plugin, "_dispatch_pass",
                        lambda c, s, m: dispatch_calls.append(1) or True)
    ask = "design a caching layer with tradeoffs"
    # Middleware contract: every pass advances turn identity. Baseline this
    # turn's ask first, so the later different ask bumps the counter.
    state.advance_turn_identity(SID, state.hash_text(ask))
    d1 = route_gate.claim_pass(ask, SID, "minimax-m3")
    assert d1.route is True and d1.source == route_gate.SOURCE_AUTO
    assert dispatch_calls == [1]
    # Same-turn re-fire (same ask, provider call 2): auto pass never
    # re-consulted; the turn record stands it down.
    d2 = route_gate.claim_pass(ask, SID, "minimax-m3")
    assert d2.route is False and d2.reason == "turn_claimed"
    assert dispatch_calls == [1]  # legacy pass consulted exactly ONCE
    # Turn record survives (visible to audit gate / legacy sites):
    assert route_gate.claim_state(SID, ask, "minimax-m3") is not None
    assert route_gate.claim_state(SID, ask, "minimax-m3")["source"] == \
        route_gate.SOURCE_AUTO
    # A genuinely NEW user turn (different ask, new turn identity) routes
    # normally again — advance_turn_identity is the middleware's new-turn
    # seam (content hash change, no tool-role continuation).
    monkeypatch.setattr(plugin, "_dispatch_pass",
                        lambda c, s, m: dispatch_calls.append(1) or True)
    state.advance_turn_identity(SID, state.hash_text("a different fresh ask"))
    d3 = route_gate.claim_pass("a different fresh ask", SID, "minimax-m3")
    assert d3.route is True and d3.source == route_gate.SOURCE_AUTO


def test_declared_then_auto_dispatch_stands_down(monkeypatch):
    """Reverse order: the gate claims via a user phrase, then a same-turn
    re-fire of the same ask must NOT re-run the auto pass — the turn
    record stands it down (one consult per turn, one outcome)."""
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    dispatch_calls = []
    monkeypatch.setattr(plugin, "_dispatch_pass",
                        lambda c, s, m: dispatch_calls.append(1) or True)
    ask = "ask your higher self: blind spots?"
    d = route_gate.claim_pass(ask, SID, "minimax-m3")
    assert d.route is True and d.source == route_gate.SOURCE_DECLARED_USER
    d2 = route_gate.claim_pass(ask, SID, "minimax-m3")
    # The turn record stands it down (leg 7: binding, one outcome/turn).
    assert d2.route is False and d2.reason == "turn_claimed"


# ---------------------------------------------------------------------------
# Audit gate stand-down
# ---------------------------------------------------------------------------


def test_audit_gate_stands_down_on_claimed_turn(monkeypatch):
    """POST audit must not run its consult when the turn is claimed —
    one frontier call per turn."""
    from hermes_router import completion_audit as ca
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    _rr(lane="shadow")
    fired = []
    monkeypatch.setattr(ca, "_consult_meta",
                        lambda *a, **k: fired.append(1) or {"note": "x"})
    out = ca.audit_gate(SID, "x" * 500, model="m")
    assert out is None       # no audit fired
    assert fired == []       # no frontier consult billed


def test_audit_gate_runs_when_unclaimed(monkeypatch):
    """Unclaimed turn: the audit gate proceeds past the claim check
    (regression guard — leg 7 must not kill the audit cadence)."""
    from hermes_router import completion_audit as ca
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    assert route_gate.claim_state(SID) is None
    reached = []

    def _spy_has_pending_render(sid):
        reached.append(1)
        return False

    monkeypatch.setattr(ca.state, "has_pending_render", _spy_has_pending_render)
    ca.audit_gate(SID, "x" * 500, model="m")
    assert reached == [1]    # claim check passed, render check consulted


# ---------------------------------------------------------------------------
# Ordering + fail-open
# ---------------------------------------------------------------------------


def test_standdown_fires_when_claim_registered_before_dispatch(monkeypatch):
    """Ordering: registration BEFORE the legacy pass is the live-probe
    shape (16:09:19 request_routing_executed -> 16:09:36 anchor_route_fired
    must never recur). The registry check happens first."""
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    order = []
    monkeypatch.setattr(router_core, "dispatch",
                        lambda *a, **k: order.append("dispatch") or router_core.RouteDecision(
                            task_id="t", lane="complexity", mode="consult",
                            model_target="x", reason="complexity_orientation",
                            ts=0.0, override_used=None, route_id="r"))
    monkeypatch.setattr(route_gate, "claim_state",
                        lambda sid, content="", model="":
                        order.append("claim_state") or
                        {"lane": "shadow", "source": "declared_agent"})
    out = plugin._dispatcher_pre._dispatch_pass("ask", SID, "m")
    assert out is False
    assert order == ["claim_state"]  # registry read FIRST, dispatch never


def test_fail_open_registry_unavailable(monkeypatch):
    """Registry unavailable (claim_state raises): legacy behavior — the
    complexity pass proceeds normally, delivery never blocked."""
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(route_gate, "claim_state",
                        lambda sid: (_ for _ in ()).throw(RuntimeError("registry down")))
    monkeypatch.setattr(router_core, "dispatch",
                        lambda *a, **k: router_core.RouteDecision(
                            task_id="t", lane="complexity", mode="consult",
                            model_target="x", reason="complexity_orientation",
                            ts=0.0, override_used=None, route_id="r"))
    staged = []
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary":
                        staged.append(rd) or {"route_id": rd.route_id})
    plugin._dispatcher_pre._log_route = lambda *a, **k: None
    out = plugin._dispatcher_pre._dispatch_pass("ask", SID, "m")
    assert out is True  # legacy behavior: complexity handled, swap staged
    assert len(staged) == 1


def test_claim_state_readonly_and_failopen():
    """claim_state never raises and never consumes the claim."""
    route_gate.register_declared(SID, route_gate.LANE_SHADOW,
                                 route_gate.SOURCE_DECLARED_AGENT)
    first = route_gate.claim_state(SID)
    second = route_gate.claim_state(SID)
    assert first == second is not None
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    assert route_gate.claim_state(SID) is None


# ---------------------------------------------------------------------------
# Leg 7b — mid-turn declared claims bind the WHOLE turn (key continuity)
# ---------------------------------------------------------------------------

def _wire_complexity(monkeypatch):
    """Pin auto-complexity routing so _dispatch_pass wants to fire."""
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"pre_mode": "route", "mid_mode": "route"})
    from hermes_router import anchor_chain
    chain = anchor_chain.AnchorChainCfg(
        primary=anchor_chain.parse_anchor_uri(
            "openrouter://test/anchor-primary", "primary"),
        judge=anchor_chain.parse_anchor_uri(
            "openrouter://test/anchor-judge", "judge"),
        overflow="pass_through", daily_cap_usd=10.0, pricing={})
    monkeypatch.setattr(router_core.anchor_chain, "load_anchor_chain",
                        lambda: chain)


def test_midturn_tool_claim_binds_rotated_content_passes(monkeypatch):
    """Live-probe regression (api_1789232397): agent's request_routing tool
    registers a shadow claim MID-TURN; later passes of the SAME turn carry
    rotated content (tool results appended) — every one must stand down
    (no second frontier consult billed). Key continuity: router_tools and
    route_gate share session_id + turn counter, NOT a content hash."""
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    _wire_complexity(monkeypatch)
    ask = "hard multi-step ask for the tool loop"
    req1 = _request(ask)
    _rr(lane="shadow")  # MID-TURN: the agent's tool call
    # turn-identity advanced like the middleware does on every pass
    state.advance_turn_identity(SID, state.hash_text(ask))
    assert route_gate.claim_state(SID) is not None  # bound immediately
    out1 = plugin.on_llm_request(request=req1, original_request=req1,
                                 session_id=SID)
    fires1 = [(f.get("event_detail"), f.get("lane"))
              for _, f in LOGGED if f.get("event_detail")
              in ("anchor_route_fired", "request_routing_executed")]
    assert "anchor_route_fired" not in [e for e, _ in fires1]
    router_core.pending_model_swap(SID)  # llm_execution consumes swap
    # Same-turn pass with ROTATED content (tool results appended, tool-role
    # present) — must stand down, no anchor consult billed.
    req2 = dict(req1)
    req2["messages"] = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": ask},
        {"role": "assistant", "content": "running tool"},
        {"role": "tool", "content": "tool result"},
        {"role": "user", "content": ask + " (with tool output)"},
    ]
    LOGGED.clear()
    out2 = plugin.on_llm_request(request=req2, original_request=req2,
                                 session_id=SID)
    assert out2 == {}
    assert not [f for _, f in LOGGED
                if f.get("event_detail") == "anchor_route_fired"]
    assert any(f.get("event_detail") == "claim_standdown" for _, f in LOGGED)
    # Second rotated re-fire: still bound.
    LOGGED.clear()
    plugin.on_llm_request(request=req2, original_request=req2,
                          session_id=SID)
    assert not [f for _, f in LOGGED
                if f.get("event_detail") == "anchor_route_fired"]


def test_midturn_tool_claim_does_not_block_next_user_turn(monkeypatch):
    """A mid-turn claim binds only ITS turn: the next genuine user turn
    (new ask, no tool-role messages) routes normally again."""
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    _wire_complexity(monkeypatch)
    monkeypatch.setattr(router_core, "pre_cooldown_seconds", lambda: 0)
    ask1 = "first hard ask"
    state.advance_turn_identity(SID, state.hash_text(ask1))
    _rr(lane="shadow")
    plugin.on_llm_request(request=_request(ask1),
                          original_request=_request(ask1), session_id=SID)
    router_core.pending_model_swap(SID)
    # New user turn: different ask (complexity-triggering), no tool-role
    # continuation.
    ask2 = ("Design a multi-stage migration plan for splitting the "
            "monolith into services, including architecture trade-offs.")
    state.advance_turn_identity(SID, state.hash_text(ask2))
    assert route_gate.claim_state(SID) is None  # prior turn's record aged out
    LOGGED.clear()
    plugin.on_llm_request(request=_request(ask2),
                          original_request=_request(ask2), session_id=SID)
    assert any(f.get("event_detail") == "anchor_route_fired"
               for _, f in LOGGED)  # new turn routes normally
