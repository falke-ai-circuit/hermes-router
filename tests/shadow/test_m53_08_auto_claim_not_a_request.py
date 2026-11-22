"""M53-08 — an auto-staged pre-mode claim is not a routing REQUEST.

Log evidence (conductor, session 20261115_145839_d70852e8):
  bare_call_reminder_injected 213  = 187 request_source=auto
                                     26 request_source=aux_intent
                                      0 user|declared
  every fire: lane=higher-pre request_origin=turn_claim word_evidence=False

So the vocabulary probe was never decisive (r56's premise was wrong and that
change is reverted). The defect is on the READER: `lane_request_for_turn`
returns the turn claim that `claim_pass` stamped for its own pre-mode staging,
and the reminder treats that staging as a request the user made.

The auto claim is NOT removed — the route gate consumes it to enforce
one-consult-per-turn (claim_state / _auto_shape), so the fix is the reminder's
permitted set: user|declared only.

G1 behavioural (fails at 6341fe5). G2-G5 controls (pass there).
"""
from __future__ import annotations

import pytest

import hermes_router as plugin
from hermes_router import config_access, route_gate, router_core, state

SID = "s-m53-08-g"
LOGGED = []

TOOL_CALL_MESSAGES = [{
    "role": "assistant",
    "content": "",
    "tool_calls": [{
        "id": "call_1", "type": "function",
        "function": {"name": "router_control",
                     "arguments": '{"action": "request_routing", '
                                  '"lane": "shadow"}'},
    }],
}]


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
    from hermes_router import intent_classifier
    monkeypatch.setattr(intent_classifier, "_intent_suspect",
                        lambda content: False)
    yield
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)


def _request(text, extra=()):
    return {"model": "minimax-m3",
            "messages": [{"role": "user", "content": text}] + list(extra)}


def _injected(req):
    return [m for m in (req or {}).get("messages") or []
            if isinstance(m, dict) and m.get("role") == "system"
            and route_gate.ROUTER_BARE_CALL_REMINDER_MARKER
            in str(m.get("content") or "")]


def _rows(detail):
    return [f for _, f in LOGGED if f.get("event_detail") == detail]


# ---------------------------------------------------------------------------
# G1 — BEHAVIOURAL: the auto pre-mode claim must not inject
# ---------------------------------------------------------------------------

def test_g1_auto_claim_is_not_a_request(_reset):
    """The exact measured shape: source=auto, lane=higher-pre, no user ask."""
    route_gate.stamp_turn_claim(SID, "higher-pre", route_gate.SOURCE_AUTO)
    body = "summarise the handover notes"
    req = _request(body)
    fired = route_gate.bare_call_guard(body, SID, "minimax-m3", request=req)
    skipped = _rows("bare_call_guard_skipped")
    print("\nG1 fired=%s row=%s" % (fired, skipped[-1] if skipped else None))
    assert fired is False
    assert _injected(req) == []
    assert skipped and skipped[-1]["reason"] == "auto_claim_not_a_request", skipped
    assert skipped[-1]["request_source"] == "auto"
    assert skipped[-1]["lane"] == "higher-pre"


def test_g1b_aux_intent_claim_is_not_a_request(_reset):
    """aux_intent is an inference, not a request the user made."""
    route_gate.stamp_turn_claim(SID, "shadow", route_gate.SOURCE_AUX_INTENT)
    body = "continue the analysis"
    req = _request(body)
    fired = route_gate.bare_call_guard(body, SID, "minimax-m3", request=req)
    skipped = _rows("bare_call_guard_skipped")
    print("\nG1b fired=%s" % fired)
    assert fired is False
    assert _injected(req) == []
    assert skipped[-1]["reason"] == "source_aux_intent_not_a_request", skipped


# ---------------------------------------------------------------------------
# G2 — CONTROL: a USER-declared request still injects
# ---------------------------------------------------------------------------

def test_g2_user_declared_request_still_fires(_reset):
    route_gate.register_declared(SID, "shadow", route_gate.SOURCE_DECLARED_USER)
    body = "route this to the shadow lane"
    req = _request(body)
    fired = route_gate.bare_call_guard(body, SID, "minimax-m3", request=req)
    hit = _rows("bare_call_reminder_injected")
    print("\nG2 fired=%s source=%s" % (fired, hit[-1]["request_source"] if hit else None))
    assert fired is True
    assert len(_injected(req)) == 1
    assert hit[-1]["request_source"] == "declared_user"


def test_g2b_agent_declared_request_still_fires(_reset):
    route_gate.register_declared(SID, "shadow", route_gate.SOURCE_DECLARED_AGENT)
    body = "nothing to see here"
    req = _request(body)
    fired = route_gate.bare_call_guard(body, SID, "minimax-m3", request=req)
    print("\nG2b fired=%s" % fired)
    assert fired is True


# ---------------------------------------------------------------------------
# G3 — CONTROL: the emitted request_routing tool call still counts
# ---------------------------------------------------------------------------

def test_g3_tool_call_after_last_user_counts(_reset):
    body = "summarise the last three turns for the handover"
    req = _request(body, TOOL_CALL_MESSAGES)
    fired = route_gate.bare_call_guard(body, SID, "minimax-m3", request=req)
    hit = _rows("bare_call_reminder_injected")
    print("\nG3 fired=%s origin=%s" % (fired, hit[-1]["request_origin"] if hit else None))
    assert fired is True
    assert hit[-1]["request_origin"] == "request_routing_tool_call"


# ---------------------------------------------------------------------------
# G4 — CONTROL: vocabulary and quoted log rows stay inert
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    "the unc lane did not fire on that turn; lane=uncensored-render",
    "read /opt/data/profiles/shadow/uncensored-router.log and check "
    "banner_attached=0",
    "the render is the delivered body; route_fired was 0",
])
def test_g4_vocabulary_never_injects(_reset, body):
    req = _request(body)
    fired = route_gate.bare_call_guard(body, SID, "minimax-m3", request=req)
    print("\nG4 fired=%s body=%r" % (fired, body[:50]))
    assert fired is False
    assert _injected(req) == []
    assert _rows("bare_call_guard_skipped")[-1]["word_evidence"] is False


# ---------------------------------------------------------------------------
# G5 — CONTROL: the pre-mode frontier orientation itself is untouched
# ---------------------------------------------------------------------------

def test_g5_pre_mode_orientation_still_routes_and_logs(_reset, monkeypatch):
    """The orientation still runs with source=auto and still emits its rows.

    Counted before/after for ONE turn: the claim still routes (so the route
    gate's one-consult-per-turn invariant is intact); only its status as a
    "request" for the reminder changed.
    """
    monkeypatch.setattr(plugin._dispatcher_pre, "_dispatch_pass",
                        lambda c, s, m: True)
    body = "continue the deep analysis"
    req = _request(body)
    before = len(LOGGED)
    dec = route_gate.claim_pass(body, SID, "minimax-m3", request=req)
    after_rows = LOGGED[before:]
    print("\nG5 route=%s lane=%s source=%s rows=%d"
          % (dec.route, dec.lane, dec.source, len(after_rows)))
    assert dec.route is True
    assert dec.lane == "higher-pre"
    assert dec.source == "auto"
    assert len(after_rows) > 0, "the orientation must still log its own rows"
    # NOTE: the reminder's behaviour on this claim is G1's assertion, not this
    # control's — a control must pass at the parent, where the reminder still
    # fired on auto claims.
