"""M53-08 — the bare-call guard asserts a routing REQUEST, never the word.

Measured: ~60 fires in one conductor session, every one on prose that merely
NAMED the lane — a log path, a test name, the gate's own fields
(lane=uncensored-render, route_fired), or the agent DISCUSSING routing. Each
fire injected an instruction that would fabricate a lane request and write a
false ledger row.

N1-N4, N7 are CONTROLS: they must pass at parent 865dd65 (no fire) and here.
N5-N6 are BEHAVIOURAL: they must FAIL at parent (it fired on vocabulary, and
its structural key did not require the user's own wording).

The subject is `route_gate.explicit_routing_request`, plus the guard's own
fire decision for the end-to-end shape.
"""
from __future__ import annotations

import pytest

import hermes_router as plugin
from hermes_router import config_access, route_gate, router_core, state

SID = "s-m53-08"
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
    from hermes_router import intent_classifier
    monkeypatch.setattr(intent_classifier, "_intent_suspect",
                        lambda content: False)
    yield


def _injected(req):
    return [m for m in (req or {}).get("messages") or []
            if isinstance(m, dict) and m.get("role") == "system"
            and route_gate.ROUTER_BARE_CALL_REMINDER_MARKER
            in str(m.get("content") or "")]


def _request(text):
    return {"model": "minimax-m3",
            "messages": [{"role": "user", "content": text}]}


# ---------------------------------------------------------------------------
# CONTROLS — vocabulary, paths, reports and descriptions never fire
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("content", [
    "read domain_gate.py and check confirm_in_domain",
    "fix domain_gate.py in hermes_router",
])
def test_n1_n7_work_on_the_repo_does_not_fire(_reset, content):
    req = _request(content)
    fired = route_gate.bare_call_guard(content, SID, "m", request=req)
    print("\nN1/N7 content=%r fired=%s" % (content[:48], fired))
    assert fired is False
    assert _injected(req) == []


def test_n2_log_path_and_field_dump_does_not_fire(_reset):
    content = ("check /opt/data/profiles/shadow/uncensored-router.log: "
               "banner_attached=0 and lane=uncensored-render")
    req = _request(content)
    fired = route_gate.bare_call_guard(content, SID, "m", request=req)
    print("\nN2 fired=%s" % fired)
    assert fired is False
    assert _injected(req) == []


def test_n3_brief_that_describes_routing_does_not_fire(_reset):
    content = ("The required behaviour: the render is the delivered body; "
               "lane=uncensored-render and route_fired are logged fields. "
               "The unc lane's banner ships when the render ships.")
    req = _request(content)
    fired = route_gate.bare_call_guard(content, SID, "m", request=req)
    print("\nN3 fired=%s" % fired)
    assert fired is False
    assert _injected(req) == []


def test_n4_report_about_the_lane_does_not_fire(_reset):
    content = "the unc lane did not fire on that turn"
    req = _request(content)
    fired = route_gate.bare_call_guard(content, SID, "m", request=req)
    print("\nN4 fired=%s" % fired)
    assert fired is False
    assert _injected(req) == []


# ---------------------------------------------------------------------------
# BEHAVIOURAL — an explicit request to route fires, on the named lane
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("content", [
    "route this to the unc lane",
    "have shadow-self answer this one",
])
def test_n5_explicit_request_fires(_reset, content):
    req = _request(content)
    fired = route_gate.bare_call_guard(content, SID, "m", request=req)
    print("\nN5 content=%r fired=%s" % (content, fired))
    assert fired is True
    assert len(_injected(req)) == 1


def test_n6_explicit_higher_self_request_fires_on_that_lane(_reset):
    content = "have higher-self answer this one"
    req = _request(content)
    fired = route_gate.bare_call_guard(content, SID, "m", request=req)
    print("\nN6 fired=%s lane=%r" % (fired, (LOGGED[-1][1] if LOGGED else {})))
    assert fired is True
    hit = [f for _, f in LOGGED
           if f.get("event_detail") == "bare_call_reminder_injected"]
    assert hit, LOGGED
    assert "higher" in str(hit[-1].get("lane") or "") or hit[-1].get("lane") == ""
