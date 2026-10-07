"""R11 — frontier imperative-consult family (Goran 09-17 ruling).

Incident: operative 20260807_050731, user turn "ask frontier her consult"
fired NO lane (strict table had no frontier family; aux prompt had no
class for imperative consult directives) -> the agent hand-rolled a raw
provider curl (unrouted, uncapped, unbannered spend).

Fix (Conductor-approved Option B): surface-form directives belong in the
strict declared variant table. Coverage under test:
- imperative family detection ('ask frontier <payload>',
  'consult frontier: <payload>', 'frontier consult on <X>',
  'consult your higher self <Y>')
- payload extraction rides the existing separator machinery
- R7: named-model override rides the declared_user source
- aux-down fail-open: strict path unaffected
- echo guard: quoted / fenced / meta lines stay inert
- intent_none carries the closest-class hint (auditability)

Aux fully mocked via the _aux_transport seam (zero-network doctrine).
SID convention: "s-r11".
"""
import json

import pytest

import hermes_router as plugin
from hermes_router import config_access, intent_classifier as ic
from hermes_router import route_gate, router_core, state

SID = "s-r11"

LOGGED = []


def _verdict(lane, subtype, confidence):
    return json.dumps({"choices": [{"message": {"content": json.dumps(
        {"lane": lane, "subtype": subtype, "confidence": confidence})}}]})


@pytest.fixture()
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
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(plugin._dispatcher_pre, "_dispatch_pass",
                        lambda c, s, m: False)
    yield


def _decide(text, session_id=SID):
    state.advance_turn_identity(session_id, state.hash_text(text))
    return route_gate.decide_turn({
        "content": text, "request": {"messages": []},
        "context": {}, "session_id": session_id, "auto_shape": None,
        "claim": True})


# ---------------------------------------------------------------------------
# Imperative family detection (the incident class)
# ---------------------------------------------------------------------------


def test_incident_phrase_ask_frontier_routes(_reset):
    """THE incident phrase: 'ask frontier her consult' must route
    declared_user higher-pre (previously intent_none -> raw curl)."""
    d = _decide("ask frontier her consult")
    assert d.route is True
    assert d.lane == route_gate.LANE_HIGHER_PRE
    assert d.source == route_gate.SOURCE_DECLARED_USER
    assert d.reason == "declared_user"


@pytest.mark.parametrize("text", [
    "ask frontier about the eval matrix",
    "consult frontier on this approach",
    "frontier consult for the quota doctrine",
    "ask the frontier: what is 2+2, short answer",
    "consult your higher self about the routing gap",
    "can you ask frontier her opinion on the twist detection rates",
])
def test_frontier_family_routes(_reset, text):
    d = _decide(text)
    assert d.route is True, text
    assert d.lane == route_gate.LANE_HIGHER_PRE, text
    assert d.source == route_gate.SOURCE_DECLARED_USER, text


def test_payload_rides_claim(_reset):
    """The consult payload is the rest of the ask — the claim carries the
    lane; payload text does not change detection."""
    d = _decide("ask frontier: what is one blind spot in the current "
                "T1/T2 pair design, short answer")
    assert d.route is True
    assert d.lane == route_gate.LANE_HIGHER_PRE


# ---------------------------------------------------------------------------
# R7: named-model override rides the declared_user source
# ---------------------------------------------------------------------------


def test_named_model_override_rides_frontier_family(_reset, monkeypatch):
    from hermes_router import anchor_chain
    monkeypatch.setattr(
        anchor_chain, "anchor_models",
        lambda: {"luna": "openai/gpt-5.6-luna-pro"})
    d = _decide("consult frontier using luna: eval the matrix")
    assert d.route is True
    assert d.source == route_gate.SOURCE_DECLARED_USER
    assert d.model_override == ("luna", "openai/gpt-5.6-luna-pro")


# ---------------------------------------------------------------------------
# Aux-down fail-open: the strict path is unaffected
# ---------------------------------------------------------------------------


def test_aux_down_strict_path_still_routes(_reset, monkeypatch):
    monkeypatch.setattr(ic, "_aux_transport", lambda pj, t: None)
    d = _decide("ask frontier her consult")
    assert d.route is True
    assert d.lane == route_gate.LANE_HIGHER_PRE
    assert not any(f.get("event_detail") == "intent_aux_error"
                   for _, f in LOGGED)


# ---------------------------------------------------------------------------
# Echo guard / FP doctrine: quoted, fenced, meta lines stay inert
# ---------------------------------------------------------------------------


def test_quoted_phrase_inert(_reset):
    d = _decide('she said "ask frontier her consult" in the incident report')
    assert d.route is False
    assert route_gate.peek_declared(SID) is None


def test_fenced_phrase_inert(_reset):
    d = _decide("```\nask frontier her consult\n```")
    assert d.route is False
    assert route_gate.peek_declared(SID) is None


def test_meta_discussion_inert(_reset):
    d = _decide("what does the ask frontier phrase trigger in the router")
    assert d.route is False


# ---------------------------------------------------------------------------
# intent_none closest-class hint (auditability)
# ---------------------------------------------------------------------------


def test_intent_none_carries_vocab_hint(_reset, monkeypatch):
    """Near-miss turn the strict tables miss AND aux says none -> the
    intent_none event carries the vocabulary hint ('frontier')."""
    monkeypatch.setattr(
        ic, "_aux_transport",
        lambda pj, t: _verdict("none", None, 0.95))
    d = _decide("did the frontier consult machinery ever get used here")
    assert d.route is False
    none_events = [f for _, f in LOGGED
                   if f.get("event_detail") == "intent_none"]
    assert none_events and none_events[0].get("hint") == "frontier"
