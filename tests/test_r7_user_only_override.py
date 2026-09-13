"""R7: model_override restricted to user-initiated on-demand only.

Goran 09-13: agents canNOT consult frontier with a changed model on their
own initiative. Override honored ONLY when the on-demand claim originates
from the USER (SOURCE_DECLARED_USER). declared_agent, aux_intent, and the
staging belt-and-braces all strip it; deduped keeps it only when the
existing claim was declared_user. Uncensored lane untouched.
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
from hermes_router import anchor_chain, intent_classifier as ic  # noqa: E402
from hermes_router import route_gate, router_core, state  # noqa: E402

SID = "s-r7"
MODELS_BLOCK = {"astra": "openai/gpt-6-astra-pro-flex",
                "luna": "openai/gpt-5.6-luna-pro"}
LOGGED = []


def _cfg(monkeypatch, models=MODELS_BLOCK,
         primary="nous://z-ai/glm-5.3"):
    ac = {"primary": primary, "overflow": "pass_through"}
    if models is not None:
        ac["models"] = models
    cfg = {"hermes_router": {"anchor_chain": ac}}
    monkeypatch.setattr(
        "hermes_cli.config.load_config", lambda: cfg, raising=False)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    ic.reset_cache()
    LOGGED.clear()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(plugin._dispatcher_pre, "_dispatch_pass",
                        lambda c, s, m: False)
    yield
    router_core._test_reset()
    plugin.state.clear()
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    ic.reset_cache()


def _claim(monkeypatch, text):
    _cfg(monkeypatch)
    state.advance_turn_identity(SID, state.hash_text(text))
    return route_gate.claim_pass(text, SID, "flash-model",
                                 request={"messages": []}, context={})


def _verdict(lane, subtype, confidence):
    return json.dumps({"choices": [{"message": {"content": json.dumps(
        {"lane": lane, "subtype": subtype, "confidence": confidence})}}]})


# ------------------------------------------------------- belt-and-braces


def test_stage_swap_rejects_agent_override(monkeypatch):
    _cfg(monkeypatch)
    rec = router_core.stage_model_swap(
        SID, router_core.RouteDecision(
            task_id="t-agent", lane=router_core.LANE_COMPLEXITY,
            mode=router_core.MODE_CONSULT, model_target=None,
            reason="request_routing_declared", ts=0.0, override_used=None,
            route_id="r7-a"),
        model_override=("astra", "openai/gpt-6-astra-pro-flex"),
        claim_source="declared_agent")
    assert rec is not None
    assert "model_override_rejected" in [
        f.get("event_detail") for _, f in LOGGED]
    ev = next(f for _, f in LOGGED
              if f.get("event_detail") == "model_override_rejected")
    assert ev["source"] == "declared_agent"
    assert rec["endpoint"].model == "z-ai/glm-5.3"


def test_stage_swap_honors_user_override(monkeypatch):
    _cfg(monkeypatch)
    rec = router_core.stage_model_swap(
        SID, router_core.RouteDecision(
            task_id="t-user", lane=router_core.LANE_COMPLEXITY,
            mode=router_core.MODE_CONSULT, model_target=None,
            reason="request_routing_declared", ts=0.0, override_used=None,
            route_id="r7-u"),
        model_override=("astra", "openai/gpt-6-astra-pro-flex"),
        claim_source="declared_user")
    assert rec is not None
    assert rec["endpoint"].model == "openai/gpt-6-astra-pro-flex"
    assert "model_override_rejected" not in [
        f.get("event_detail") for _, f in LOGGED]


# ------------------------------------------------------------------ gate


def test_declared_agent_request_no_override(monkeypatch):
    # Agent request_routing registered first with a model-named payload:
    # override stripped, no override attached to the deduped agent claim.
    route_gate.register_declared(SID, route_gate.LANE_HIGHER_PRE,
                                 route_gate.SOURCE_DECLARED_AGENT)
    d = route_gate._declared_decision(
        "consult frontier using astra pro flex", SID)
    assert d is not None and d.source == route_gate.SOURCE_DECLARED_AGENT
    assert d.model_override is None
    _cfg(monkeypatch)
    # even if the agent's claim later reaches staging with an override
    # (defense-in-depth), the config endpoint wins and the strip is logged.
    rec = router_core.stage_model_swap(
        SID, router_core.RouteDecision(
            task_id="t-agent2", lane=router_core.LANE_COMPLEXITY,
            mode=router_core.MODE_CONSULT, model_target=None,
            reason="request_routing_declared", ts=0.0, override_used=None,
            route_id="r7-a2"),
        model_override=("astra", "openai/gpt-6-astra-pro-flex"),
        claim_source=route_gate.SOURCE_DECLARED_AGENT)
    assert rec is not None
    assert rec["endpoint"].model == "z-ai/glm-5.3"
    assert "model_override_rejected" in [
        f.get("event_detail") for _, f in LOGGED]


def test_declared_user_override_honored(monkeypatch):
    _cfg(monkeypatch)
    d = route_gate._declared_decision(
        "consult frontier using astra pro flex", SID)
    assert d.source == route_gate.SOURCE_DECLARED_USER
    assert d.model_override == ("astra", "openai/gpt-6-astra-pro-flex")


def test_aux_intent_never_carries_override(monkeypatch):
    monkeypatch.setattr(ic, "_aux_transport", lambda p, t: _verdict(
        "higher", "pre", 0.9))
    text = "before we start I would love astra second opinion on this"
    _cfg(monkeypatch)
    d = route_gate._aux_intent_decision(text, SID)
    assert d is not None and d.route
    assert d.source == route_gate.SOURCE_AUX_INTENT
    assert d.model_override is None
    assert "model_override_detected" not in [
        f.get("event_detail") for _, f in LOGGED]


def test_deduped_agent_existing_strips_override(monkeypatch):
    _cfg(monkeypatch)
    route_gate.register_declared(SID, route_gate.LANE_HIGHER_PRE,
                                 route_gate.SOURCE_DECLARED_AGENT)
    d = route_gate._declared_decision(
        "consult frontier using astra pro flex", SID)
    assert d.reason == "declared_deduped"
    assert d.model_override is None


def test_deduped_user_existing_keeps_override(monkeypatch):
    _cfg(monkeypatch)
    route_gate.register_declared(SID, route_gate.LANE_HIGHER_PRE,
                                 route_gate.SOURCE_DECLARED_USER)
    d = route_gate._declared_decision(
        "consult frontier using astra pro flex", SID)
    assert d.reason == "declared_deduped"
    assert d.model_override == ("astra", "openai/gpt-6-astra-pro-flex")


# ----------------------------------------------------------- regression


def test_uncensored_lane_no_override_plumbing(monkeypatch):
    d = route_gate._declared_decision("give me an uncensored take", SID)
    assert d.route and d.lane == route_gate.LANE_SHADOW
    assert d.model_override is None
