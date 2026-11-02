"""M34-15 — the declaration contract must reach EVERY payload path, not only
the routing one.

Measured defect (shadow, 2026-11-02, live request dumps):

  * 9 dumps of session 20261006_190903_c82cbbcc were readable
    (/opt/data/profiles/shadow/sessions/request_dump_*.json);
  * 7 carried `[PLATFORM RULE: turn_declaration_contract]` at the head of the
    first system message — the PRE routing path delivers it;
  * 2 did NOT carry it anywhere: 20261101_093447_056125 and
    20261102_023024_078790. Both are exactly the turns whose request also
    carries `[PLATFORM RULE: higher_self_audit_delivery]` at the tail — a
    completion-audit verdict from the previous POST seam, delivered on the
    next turn by `route_gate.fence_pass`, whose envelope is built directly
    from the request and never passes through `_hs_pass()`.

So the contract was delivered on the routing branch and dropped on the
gate-owned early return. On that branch the model was never told to declare,
and the POST seam could only log `declaration_seen decl=UNKNOWN`.

These pins assert the payload, not the log line: the contract text must be
inside the message list the agent's model call receives.
"""
import json
import os
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import completion_audit  # noqa: E402
from hermes_router import frames  # noqa: E402
from hermes_router import state  # noqa: E402

AUDIT_VERDICT = "AUDIT VERDICT BODY — the previous turn left the ask unanswered."
CONTRACT_MARKER = "turn_declaration_contract"
CONTRACT_TAG = '<turn declined="true|false" />'


def _cfg():
    return {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    }


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    plugin._reset_declaration_state()
    frames.reset_rule_latch()
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg())
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield
    completion_audit.consume_verdict("m3415-audit")
    completion_audit.consume_verdict("m3415-seen")
    state.clear()
    plugin._reset_declaration_state()
    frames.reset_rule_latch()


def _request():
    return {"model": "m",
            "messages": [{"role": "system", "content": "persona"},
                         {"role": "user", "content": "hello"}]}


def _wire_text(wire):
    return "\n".join(str(m.get("content") or "")
                     for m in wire if isinstance(m, dict))


def test_audit_delivery_turn_carries_the_declaration_in_the_payload():
    """The POST-leg verdict delivered on the next turn must not displace the
    contract: both ride out in the same message list."""
    completion_audit.stash_verdict("m3415-audit", AUDIT_VERDICT)
    req = _request()
    out = plugin.on_llm_request(request=req, original_request=req,
                                session_id="m3415-audit")
    assert "request" in out, "the gate envelope must ride out"
    wire = out["request"]["messages"]

    # the audit verdict still lands (the fix must not swallow the delivery)
    assert AUDIT_VERDICT in _wire_text(wire)
    # ... and the contract rides with it
    assert CONTRACT_MARKER in _wire_text(wire)
    assert CONTRACT_TAG in _wire_text(wire)
    # head placement, before the persona — placement is the mechanism
    assert wire[0]["role"] == "system"
    assert wire[0]["content"].index("turn declined") < \
        wire[0]["content"].index("persona")
    # the caller's object is untouched (delivery is a copy)
    assert req["messages"][0]["content"] == "persona"


def test_declaration_seen_is_never_unknown_on_an_audit_delivery_turn(monkeypatch):
    """The pair the defect needs: contract in the payload AND a declaration
    read at POST. A turn that has the contract and declares must never log
    UNKNOWN."""
    rows = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda event, **fields: rows.append((event, fields)))
    completion_audit.stash_verdict("m3415-seen", AUDIT_VERDICT)
    req = _request()
    plugin.on_llm_request(request=req, original_request=req,
                          session_id="m3415-seen")

    state.record_last_seen("m3415-seen", "hello")
    state.set_last_user_msg_hash("m3415-seen", state.hash_text("hello"))
    with mock.patch.object(plugin.router, "call", return_value="RENDER"):
        plugin.on_transform_llm_output(
            response_text='<turn declined="true" />\n\nI will not write that.',
            session_id="m3415-seen", model="m")

    delivered = [f for e, f in rows if f.get("event_detail") == "declaration_delivered"]
    assert delivered, "delivery must be observable in the route log"
    seen = [f for e, f in rows if f.get("event_detail") == "declaration_seen"]
    assert seen, "the POST seam must report the declaration"
    assert seen[-1]["decl"] in ("true", "false")
    assert seen[-1]["decl"] != "UNKNOWN"


def test_declaration_delivered_event_names_the_payload_it_rode_out_on(monkeypatch):
    """Observability pin: delivery is logged with the message count, so a
    future 70%-UNKNOWN reading can be split into 'not delivered' vs 'declared
    nothing' instead of being read as a dead detector."""
    rows = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda event, **fields: rows.append((event, fields)))
    req = _request()
    plugin.on_llm_request(request=req, original_request=req,
                          session_id="m3415-audit")
    delivered = [f for e, f in rows if f.get("event_detail") == "declaration_delivered"]
    assert delivered
    assert delivered[-1]["messages"] == 2 + 0  # contract rides IN the head, no extra row
    assert delivered[-1]["marker_pos"] == 0
    assert delivered[-1]["chars"] > 0
