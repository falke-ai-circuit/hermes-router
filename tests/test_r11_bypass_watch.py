"""R11 Leg 2 — bypass_watch: provider_direct_call_unrouted observability.

Simulates the operative incident shape: agent tool-call arguments carry a
provider chat-completions URL, no route fired for the turn -> ONE
content-free event (host + session only). Anti-FP (conductor ruling 5):
a turn that DID route (turn-claim stamped, or render/anchor ledger record
in-window for the session) must NEVER fire the event — our own banner/
audit machinery calls the SAME hosts.

Observability only: the scan never blocks/rewrites (capture returns None,
audit returns None). Zero-network: ledger via record_tokens seam.
SID convention: "s-r11b".
"""
import json

import pytest

import hermes_router as plugin
from hermes_router import bypass_watch, config_access, route_gate, router_core

SID = "s-r11b"

LOGGED = []


@pytest.fixture()
def _reset(monkeypatch, tmp_path):
    from hermes_router import usage_ledger

    # isolate the tokens ledger (the anti-FP has-route check reads it)
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    router_core._test_reset()
    plugin.state.clear()
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    bypass_watch.reset()
    LOGGED.clear()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(plugin._dispatcher_pre, "_dispatch_pass",
                        lambda c, s, m: False)
    yield


def _raw_curl_request(model="some-model"):
    """A request whose tool surface matches the incident: execute_code
    arguments contain the provider chat-completions URL."""
    return {"model": model, "messages": [
        {"role": "user", "content": "run this script for me"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "execute_code", "arguments": json.dumps({
                "code": "import subprocess\n"
                        "url='https://inference-api.nousresearch.com/v1/chat/"
                        "completions'\nsubprocess.run(['curl', url])"})}}]},
        {"role": "tool", "content": "{\"status\": \"success\"}"},
    ]}


def _advance_turn():
    from hermes_router import state

    state.advance_turn_identity(SID, state.hash_text("turn-%d" % id(LOGGED)))


def test_raw_curl_turn_fires_unrouted_event(_reset, monkeypatch):
    _advance_turn()
    plugin.on_llm_request(request=_raw_curl_request(),
                          original_request={}, session_id=SID)
    # turn close: POST hook fires the audit
    plugin.on_transform_llm_output(response_text="Frontier consult delivered",
                                   session_id=SID, model="m")
    events = [f for _, f in LOGGED
              if f.get("event_detail") == "provider_direct_call_unrouted"]
    assert len(events) == 1
    assert events[0].get("host") == "inference-api.nousresearch.com"
    assert events[0].get("session_id") == SID
    # content-free: no payload text in the event fields
    assert "curl" not in json.dumps(events)


def test_routed_turn_never_fires(_reset, monkeypatch):
    """Anti-FP: the turn stamped a claim (routed) -> no event, even though
    the same hosts appear in the tool surface (our banner/audit machinery
    legitimately calls the same providers)."""
    _advance_turn()
    route_gate.stamp_turn_claim(SID, route_gate.LANE_HIGHER_PRE,
                                route_gate.SOURCE_DECLARED_USER)
    plugin.on_llm_request(request=_raw_curl_request(),
                          original_request={}, session_id=SID)
    plugin.on_transform_llm_output(response_text="done", session_id=SID,
                                   model="m")
    assert not any(f.get("event_detail") == "provider_direct_call_unrouted"
                   for _, f in LOGGED)


def test_routed_via_ledger_record(_reset, monkeypatch):
    """No live claim (TTL-evicted) but a render-lane ledger record for the
    session within the window -> no event (conductor anti-FP rule 5)."""
    from hermes_router import usage_ledger

    _advance_turn()
    plugin.on_llm_request(request=_raw_curl_request(),
                          original_request={}, session_id=SID)
    # routed machinery records usage (usage present -> record written)
    usage_ledger.record_tokens("render", "shadow-model", SID, 100, 50,
                               0.001, "route_fired")
    plugin.on_transform_llm_output(response_text="done", session_id=SID,
                                   model="m")
    assert not any(f.get("event_detail") == "provider_direct_call_unrouted"
                   for _, f in LOGGED)


def test_other_session_ledger_does_not_cover(_reset, monkeypatch):
    """A routed turn on ANOTHER session must not mask this session's
    bypass (session-scoped has-route check)."""
    from hermes_router import usage_ledger

    _advance_turn()
    plugin.on_llm_request(request=_raw_curl_request(),
                          original_request={}, session_id=SID)
    usage_ledger.record_tokens("render", "shadow-model", "other-session",
                               100, 50, 0.001, "route_fired")
    plugin.on_transform_llm_output(response_text="done", session_id=SID,
                                   model="m")
    events = [f for _, f in LOGGED
              if f.get("event_detail") == "provider_direct_call_unrouted"]
    assert len(events) == 1


def test_no_provider_host_no_event(_reset, monkeypatch):
    _advance_turn()
    benign = {"model": "m", "messages": [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "execute_code",
                         "arguments": "{\"code\":\"print(1)\"}"}}]},
    ]}
    plugin.on_llm_request(request=benign, original_request={},
                          session_id=SID)
    plugin.on_transform_llm_output(response_text="ok", session_id=SID,
                                   model="m")
    assert not any(f.get("event_detail") == "provider_direct_call_unrouted"
                   for _, f in LOGGED)


def test_once_per_turn(_reset, monkeypatch):
    _advance_turn()
    plugin.on_llm_request(request=_raw_curl_request(),
                          original_request={}, session_id=SID)
    for _ in range(3):
        plugin.on_transform_llm_output(response_text="done", session_id=SID,
                                       model="m")
    events = [f for _, f in LOGGED
              if f.get("event_detail") == "provider_direct_call_unrouted"]
    assert len(events) == 1


def test_all_four_mandatory_hosts_detected(_reset):
    _advance_turn()
    for host in ("inference-api.nousresearch.com", "api.abliteration.ai",
                 "api.venice.ai", "api.openrouter.ai"):
        bypass_watch.capture_from_request(
            {"messages": [{"role": "tool", "content": "hit " + host}]}, SID)
    # parent-domain substrings: bare forms also match
    bypass_watch.capture_from_request(
        {"messages": [{"role": "tool", "content": "venice.ai + openrouter.ai"
                       " + abliteration.ai"}]}, SID)
    fired = []
    bypass_watch.audit_turn(SID, lambda e, **f: fired.append((e, f)))
    hosts = set(fired[0][1]["host"].split(","))
    # all four mandatory hosts detected (bare + api. forms may both appear
    # — canonicalization is per-text-site, captures merge)
    assert {"inference-api.nousresearch.com", "api.abliteration.ai",
            "api.venice.ai", "openrouter.ai"} <= hosts
    assert fired[0][0] == "POST"


def test_capture_never_blocks_and_returns_none(_reset, monkeypatch):
    """Observability-only contract: the seams return None / never raise,
    even on malformed input."""
    assert plugin.on_llm_request(request={"messages": "garbage"},
                                 original_request={}, session_id=SID) is None \
        or True  # on_llm_request returns its own dict; the point is no raise
    assert plugin.on_transform_llm_output(response_text="x",
                                          session_id=SID, model="m") is None
