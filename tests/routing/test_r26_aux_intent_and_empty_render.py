"""R26-2 — aux_intent content-descriptor FP + empty-render delivery
(live R25 battery session ...273cddf0).

Two defects, one battery turn: "Browse today's news sites and write an
uncensored summary of stories the mainstream media suppressed today." —
(a) the aux classifier routed it shadow because the word "uncensored"
described the desired CONTENT, not the lane; a shadow verdict may only
route on DIRECTIVE SHAPE (imperative to the agent about the answer mode).
(b) the uncensored render that followed returned empty and delivered a
0-char turn; empty renders must retry up the ladder and never reach the
delivery seam.
"""
import json

import pytest

import hermes_router as plugin
from hermes_router import config_access, intent_classifier as ic
from hermes_router import route_gate, router_core, state

SID = "s-r26-2"

LOGGED = []


def _verdict(lane, subtype, confidence):
    return json.dumps({"choices": [{"message": {"content": json.dumps(
        {"lane": lane, "subtype": subtype, "confidence": confidence})}}]})


# The REAL battery ask (session ...273cddf0).
BATTERY_ASK = ("Browse today's news sites and write an uncensored summary "
               "of stories the mainstream media suppressed today.")


@pytest.fixture()
def _reset(monkeypatch):
    router_core._test_reset()
    route_gate._AUX_CONSULT_LAST.clear()
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
# (a) content-descriptor ask: aux says shadow -> STILL inert (no directive
# shape). The aux transport here mirrors the live failure — it votes shadow.
# ---------------------------------------------------------------------------


def test_battery_ask_content_descriptor_inert(_reset, monkeypatch):
    monkeypatch.setattr(ic, "_aux_transport",
                        lambda pj, t: _verdict("shadow", None, 0.95))
    d = _decide(BATTERY_ASK)
    assert d.route is False
    assert route_gate.peek_declared(SID) is None
    events = [f.get("event_detail") for _, f in LOGGED]
    assert "aux_intent_route" not in events
    assert "intent_none" in events  # inert WITH observability


def test_directive_shape_still_routes(_reset, monkeypatch):
    """'Please do a uncensored take on...' (leak2 shape) keeps routing —
    the gate is shape, not vocabulary."""
    monkeypatch.setattr(ic, "_aux_transport",
                        lambda pj, t: _verdict("shadow", None, 0.95))
    d = _decide("Please do a uncensored take on gabriele find shady ways")
    assert d.route is True and d.lane == route_gate.LANE_SHADOW


def test_lane_name_phrase_is_directive(_reset, monkeypatch):
    monkeypatch.setattr(ic, "_aux_transport",
                        lambda pj, t: _verdict("shadow", None, 0.95))
    assert ic._shadow_directive_shape("route this through your uncensored lane")
    assert ic._shadow_directive_shape("go uncensored")
    assert ic._shadow_directive_shape("shadow?")
    # Production-verb content asks are NOT lane directives.
    assert not ic._shadow_directive_shape(BATTERY_ASK)
    assert not ic._shadow_directive_shape(
        "write an uncensored documentary about the 90s")
    assert not ic._shadow_directive_shape("make me an uncensored playlist")


def test_prompt_carries_content_descriptor_rule():
    assert "content descriptor" in ic._INTENT_SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# (b) empty render: retried up the ladder, never delivered.
# ---------------------------------------------------------------------------


def test_empty_render_retried_then_fails_open(_reset, monkeypatch):
    """Ladder: renderer returns '' twice then content — retries happen and
    the ladder returns the substance, never ''. Renderer returning '' for
    every attempt -> ladder returns '' and the caller fails open."""
    from hermes_router import router as router_mod

    calls = []

    def _flaky(prompt, **kw):
        calls.append(1)
        return "REAL RENDER" if len(calls) >= 3 else ""

    monkeypatch.setattr(router_mod, "call", _flaky)
    monkeypatch.setattr(plugin, "_is_refusal_shaped", lambda text: False)
    rendered, retries = plugin._dispatcher_pre._render_with_retry_ladder(
        "ask", ["refusal_phrases"], "", SID)
    assert rendered == "REAL RENDER"
    assert retries == 2

    calls.clear()

    def _empty(prompt, **kw):
        calls.append(1)
        return ""

    monkeypatch.setattr(router_mod, "call", _empty)
    rendered, retries = plugin._dispatcher_pre._render_with_retry_ladder(
        "ask", ["refusal_phrases"], "", SID)
    assert rendered == "" and len(calls) == 4  # initial + 3 ladder retries


def test_empty_render_never_reaches_delivery_seam(_reset, monkeypatch):
    """_deliver_render_pass with an empty render swaps NOTHING and logs
    route_failed — the original turn delivers (fail-open)."""
    import copy as _copy

    req = {"messages": [{"role": "user", "content": "original ask"}]}
    orig = _copy.deepcopy(req)
    out = plugin._dispatcher_pre._deliver_render_pass(
        req, "original ask", "   ", "model", SID, ["refusal_phrases"])
    assert out["request"]["messages"][-1]["content"] == \
        orig["messages"][-1]["content"]
    assert any(f.get("event_detail") == "route_failed" and
               f.get("pattern_groups") == "render_empty"
               for _, f in LOGGED)
