"""Leg 10 (live researcher-canary regression, conductor-verified):

BUG A — phrase surface too narrow: 'Can you ask higher self ...' and
'Ask shadow self to give her read' fired NO declared route. FIX: variant
table (DECLARED_USER_VARIANTS) + static normalizer (hyphen/dash collapse,
politeness-prefix strip) over strict prefix/standalone matching — no
fuzzy/semantic matching.

BUG B — bare model-call leak: with no lane fired, the agent improvised
bare provider calls. FIX B-1: self-protection rule in the router_control
tool description (request_routing is the ONLY sanctioned path). FIX B-2:
declared_intent_no_route route-log event on family near-miss (greppable,
NEVER routes).

Echo guard (H7.2) unchanged: quoted/mid-sentence phrases stay inert.
SID convention: "s-leg10".
"""
import json

import pytest

import hermes_router as plugin
from hermes_router import config_access, route_gate, router_core, state

SID = "s-leg10"

LOGGED = []


@pytest.fixture()
def _reset(monkeypatch):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    LOGGED.clear()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(plugin._dispatcher_pre, "_dispatch_pass",
                        lambda c, s, m: False)
    yield


def _request(text, model="minimax-m3"):
    return {"model": model,
            "messages": [{"role": "user", "content": text}]}


# ---------------------------------------------------------------------------
# BUG A: variant table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,lane", [
    # higher-self family — the live canary miss:
    ("Can you ask higher self to give her read", "higher-pre"),
    ("ask your higher self", "higher-pre"),
    ("ask higher self: what is one blind spot?", "higher-pre"),
    ("Ask the higher self", "higher-pre"),
    ("ask your higher-self", "higher-pre"),
    ("higher self give me a read", "higher-pre"),
    ("ask your higher self: what is one blind spot?", "higher-pre"),
    # shadow family — the live canary miss:
    ("Ask shadow self to give her read", "shadow"),
    ("route this through your shadow", "shadow"),
    ("ask your shadow self: read the situation", "shadow"),
    ("ask shadow self", "shadow"),
    ("ask your shadow", "shadow"),
    ("ask the shadow", "shadow"),
    ("shadow self read", "shadow"),
    ("route through shadow", "shadow"),
    # prefix + hyphen normalization combos:
    ("can you please ask higher self now", "higher-pre"),
    ("now go ask shadow self", "shadow"),
])
def test_variant_table_claims_lanes(_reset, text, lane):
    assert route_gate.detect_declared_user(text) == lane


def test_gate_routes_canary_miss_phrases(_reset):
    """End-to-end through claim_pass: the exact live-failure surfaces now
    fire request_routing_executed."""
    monkey_text = "Ask shadow self to give her read"
    state.advance_turn_identity(SID, state.hash_text(monkey_text))
    d = route_gate.decide_turn({
        "content": monkey_text, "request": {"messages": []},
        "context": {}, "session_id": SID, "auto_shape": None,
        "claim": True})
    assert d.route is True and d.lane == "shadow" and \
        d.source == route_gate.SOURCE_DECLARED_USER


def test_strict_match_unchanged_for_old_phrases(_reset):
    """Old exact phrases keep their lanes (no behavior change for the
    original table entries)."""
    assert route_gate.detect_declared_user("ask your higher self") == "higher-pre"
    assert route_gate.detect_declared_user(
        "route this through your shadow: audit my plan") == "shadow"
    assert route_gate.detect_declared_user("anchor this") == "higher-pre"


# ---------------------------------------------------------------------------
# Echo guard inertness (H7.2 unchanged)
# ---------------------------------------------------------------------------


def test_quoted_line_inert(_reset):
    assert route_gate.detect_declared_user(
        'the manual says "> ask your higher self" triggers routing') is None


def test_mid_sentence_inert(_reset):
    assert route_gate.detect_declared_user(
        "I think the higher self concept is interesting") is None
    assert route_gate.detect_declared_user(
        "she asked about the shadow self in her essay") is None


# ---------------------------------------------------------------------------
# BUG B-2: declared_intent_no_route near-miss observability
# ---------------------------------------------------------------------------


def test_declared_intent_no_route_emitted_on_near_miss(_reset):
    """Family words present, no strict variant matched -> greppable event,
    NO routing."""
    text = "tell me about the higher self concept in your framework"
    state.advance_turn_identity(SID, state.hash_text(text))
    d = route_gate.decide_turn({
        "content": text, "request": {"messages": []},
        "context": {}, "session_id": SID, "auto_shape": None,
        "claim": True})
    assert d.route is False  # near-miss NEVER routes
    assert any(f.get("event_detail") == "declared_intent_no_route"
               for _, f in LOGGED)


def test_no_intent_event_when_no_family_words(_reset):
    text = "what is the weather today"
    state.advance_turn_identity(SID, state.hash_text(text))
    route_gate.decide_turn({
        "content": text, "request": {"messages": []},
        "context": {}, "session_id": SID, "auto_shape": None,
        "claim": True})
    assert not any(f.get("event_detail") == "declared_intent_no_route"
                   for _, f in LOGGED)


def test_no_intent_event_when_route_fires(_reset, monkeypatch):
    """A strict match routes — the near-miss signal must not also fire.
    The execution envelope lives in claim_pass (leg-5 seam lesson)."""
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary":
                        {"route_id": rd.route_id})
    text = "Ask shadow self to give her read"
    state.advance_turn_identity(SID, state.hash_text(text))
    d = route_gate.claim_pass(text, SID, "minimax-m3")
    assert d.route is True and d.lane == "shadow"
    assert any(f.get("event_detail") == "request_routing_executed"
               for _, f in LOGGED)
    assert not any(f.get("event_detail") == "declared_intent_no_route"
                   for _, f in LOGGED)


# ---------------------------------------------------------------------------
# BUG B-1: self-protection rule in the tool surface
# ---------------------------------------------------------------------------


def test_router_control_description_carries_self_protection_rule():
    from hermes_router import router_tools
    desc = router_tools.CONTROL_SCHEMA["description"]
    assert "request_routing action" in desc
    assert "NEVER hand-rolled" in desc
    assert "never a valid outcome" in desc
    assert "curl/urllib" in desc
