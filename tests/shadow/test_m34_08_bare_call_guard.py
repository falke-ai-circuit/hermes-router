"""M34-08 — the bare-call guard keys on a ROUTING REQUEST, not on a WORD.

M34-08 spec: "Rekey the bare-call guard to a real routing request, not to the
word 'uncensored' in logs/tool output."

Contract proved here, both directions:

  (a) NEGATIVE — a body that merely CONTAINS the word (a status report, a
      quoted log line, a metaphrase of the brief) does NOT fire the guard: no
      reminder marker in the prompt, and the route log carries
      `bare_call_guard_skipped reason=no_routing_request`, with the word
      probe's answer recorded as `word_evidence` (evidence, never a key).

  (b) POSITIVE — a genuine bare call carrying a lane REQUEST fires, including
      a turn whose content contains NO lane vocabulary at all: the request is
      the router's own structured record (the emitted `request_routing` tool
      call in the prompt, and the claim registry / turn-claim record).

  (c) DEMOTION — the word probe cannot fire the guard even when it returns
      True (patched to True below); with no request the guard stays down.

SID convention: "s-m34-08".
"""
import json

import pytest

import hermes_router as plugin
from hermes_router import config_access, route_gate, router_core, state

SID = "s-m34-08"

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
    # The subject is the BARE-CALL GUARD, not the aux intent lane. The aux
    # classifier is a separate decision point that may legitimately route a
    # body of its own (observed: a status line "uncensored lane: ON" scored
    # 0.76 -> aux_intent route). Neutralised here so the guard is measured in
    # isolation; that separate behaviour is named as an open item in the
    # close report, not hidden by this fixture.
    from hermes_router import intent_classifier
    monkeypatch.setattr(intent_classifier, "_intent_suspect",
                        lambda content: False)
    yield


def _request(text, extra_messages=()):
    return {"model": "minimax-m3",
            "messages": [{"role": "user", "content": text}] + list(extra_messages)}


def _injected(req):
    msgs = (req or {}).get("messages") or []
    return [m for m in msgs if m.get("role") == "system"
            and route_gate.ROUTER_BARE_CALL_REMINDER_MARKER
            in str(m.get("content") or "")]


def _rows(detail):
    return [f for _, f in LOGGED if f.get("event_detail") == detail]


# ---------------------------------------------------------------------------
# (a) NEGATIVE — vocabulary alone never fires
# ---------------------------------------------------------------------------

NEGATIVE_BODIES = [
    # a status report: a line STARTS with the word (word_evidence True)
    "status report\nuncensored lane: ON\nrender chain: post_render_chain",
    # a quoted log line
    "> 2026-11-03 PRE event_detail=bare_call_reminder_injected lane=shadow",
    # a metaphrase of this very brief
    "M34-08 reads: rekey the bare-call guard to a real routing request, "
    "not to the word 'uncensored' in logs or tool output.",
    # prose mentioning the action name without emitting it
    "the router_control request_routing action stages a claim in the registry",
]


@pytest.mark.parametrize("body", NEGATIVE_BODIES)
def test_word_only_body_does_not_fire_the_guard(_reset, body):
    req = _request(body)
    state.advance_turn_identity(SID, state.hash_text(body))
    out = plugin.on_llm_request(request=req, original_request=req,
                                session_id=SID)
    live = (out or {}).get("request", req)
    assert _injected(live) == [], body
    assert not _rows("bare_call_reminder_injected"), body
    skipped = _rows("bare_call_guard_skipped")
    assert skipped, body
    assert skipped[-1]["reason"] == "no_routing_request", body


def test_status_report_records_word_evidence_without_firing(_reset):
    """The status report STARTS a line with 'uncensored' -> the demoted probe
    answers True. The row says so, and the guard still does not fire: the
    word is evidence, never a key."""
    body = NEGATIVE_BODIES[0]
    req = _request(body)
    state.advance_turn_identity(SID, state.hash_text(body))
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)
    row = _rows("bare_call_guard_skipped")[-1]
    assert row["word_evidence"] is True
    assert row["reason"] == "no_routing_request"
    assert not _rows("bare_call_reminder_injected")


# ---------------------------------------------------------------------------
# (b) POSITIVE — a real request fires it, even with zero vocabulary
# ---------------------------------------------------------------------------

TOOL_CALL_MESSAGES = [{
    "role": "assistant",
    "content": "",
    "tool_calls": [{
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "router_control",
            "arguments": json.dumps({"action": "request_routing",
                                     "lane": "shadow",
                                     "session_id": SID}),
        },
    }],
}]


def test_genuine_bare_call_with_lane_request_fires(_reset):
    """No lane word anywhere in the content — the REQUEST alone fires it."""
    body = "summarise the last three turns for the handover"
    req = _request(body, TOOL_CALL_MESSAGES)
    state.advance_turn_identity(SID, state.hash_text(body))
    out = plugin.on_llm_request(request=req, original_request=req,
                                session_id=SID)
    live = (out or {}).get("request", req)
    assert len(_injected(live)) == 1
    hit = _rows("bare_call_reminder_injected")
    assert hit, LOGGED
    assert hit[-1]["reason"] == "routing_request_present"
    assert hit[-1]["request_origin"] == "request_routing_tool_call"
    assert hit[-1]["lane"] == "shadow"
    assert hit[-1]["word_evidence"] is False
    assert not _rows("bare_call_guard_skipped")


def test_registered_claim_is_a_request(_reset):
    """The claim registry is a request the router itself emitted (agent
    request_routing). Guard fires on it at unit level."""
    route_gate.register_declared(SID, "shadow", route_gate.SOURCE_DECLARED_AGENT)
    req = _request("nothing to see here")
    fired = route_gate.bare_call_guard("nothing to see here", SID, "minimax-m3",
                                       request=req)
    assert fired is True
    assert len(_injected(req)) == 1
    hit = _rows("bare_call_reminder_injected")
    assert hit and hit[-1]["request_origin"] in ("turn_claim", "claim_registry")
    assert hit[-1]["request_source"] == route_gate.SOURCE_DECLARED_AGENT


# ---------------------------------------------------------------------------
# (c) DEMOTION — the old word-only path cannot fire
# ---------------------------------------------------------------------------

def test_word_probe_true_and_no_request_still_does_not_fire(_reset, monkeypatch):
    monkeypatch.setattr(route_gate, "_detect_declared_intent_near_miss",
                        lambda content: True)
    req = _request("a plain question about the weather")
    fired = route_gate.bare_call_guard("a plain question about the weather",
                                       SID, "minimax-m3", request=req)
    assert fired is False
    assert _injected(req) == []
    row = _rows("bare_call_guard_skipped")[-1]
    assert row["word_evidence"] is True
    assert row["reason"] == "no_routing_request"


def test_request_scan_ignores_prose_and_tool_output(_reset):
    """Same words, wrong shapes: a USER message and a TOOL result that quote a
    request_routing call are not requests."""
    req = _request("log excerpt follows", [
        {"role": "tool", "tool_call_id": "c0",
         "content": '{"action": "request_routing", "lane": "shadow"}'},
        {"role": "user", "content": 'assistant called router_control '
                                    '{"action": "request_routing"}'},
    ])
    assert route_gate.lane_request_for_turn(SID, "x", "m", request=req) is None
    assert route_gate.bare_call_guard("log excerpt follows", SID, "m",
                                      request=req) is False
