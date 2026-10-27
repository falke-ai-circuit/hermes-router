"""Delivery, fail-safety and delivery-gating of the decision lane.

Extraction accuracy (the `choice` over candidate moves) is measured in
test_r74_decision_surface.py and proven on 187 real transcripts. This file covers the
remaining machinery: config gating, delivery into the request, dedupe, staleness and the
guarantee that nothing in the lane can raise into a turn.
"""
from __future__ import annotations

import pytest

from hermes_router import decision_extract as dx
from hermes_router import reflex_lane as rl

ON = {"hermes_router": {"reflex_weights": {"enabled": True}}}
OFF = {"hermes_router": {}}

BODY = ("Traceback (most recent call last):\n  File \"/opt/data/app/src/render.py\", line 12\n"
        "ImportError: cannot import name 'cache' from /opt/data/app/src/cache.py\n"
        "while building the template cache for the report page")


def _dist(*ps):
    return {"answers": {"action": {"probabilities": {("a%d" % (i + 1)): p
                                                    for i, p in enumerate(ps)}},
                        "scope": {"score": 1}, "rigour": {"score": 4}}}


def test_disabled_by_default_emits_nothing(monkeypatch):
    """A plugin upgrade must never arm this fleet-wide."""
    assert rl.enabled(OFF) is False
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: pytest.fail("must not call"))
    assert rl.fire("s1", "terminal", BODY, OFF, ask="fix the renderer") == ""


def test_a_decisive_reading_is_delivered_once(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear("s2")
    assert rl.fire("s2", "terminal", BODY, ON, ask="fix the renderer")
    req = {"messages": [{"role": "user", "content": "hi"}]}
    assert rl.inject_pass(req, "s2") is True
    assert "80%" in req["messages"][-1]["content"]
    assert rl.inject_pass(req, "s2") is False, "delivered twice"


def test_flat_reading_is_delivered_soft_and_never_steers(monkeypatch):
    """Goran 2026-10-27: the lane fires on every midturn. A flat board must not steer, so it
    arrives downgraded - options as context plus the dials - never with "<- take this", and
    never as silence (silence withheld the dials on the least oriented turns)."""
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.32, 0.30, 0.24, 0.14))
    rl.clear("s3")
    block = rl.fire("s3", "terminal", BODY, ON)
    assert block, "a flat reading must still be delivered"
    assert "<- take this" not in block
    assert "no clear lead" in block
    assert rl.take("s3") == block


def test_stale_reading_is_dropped_not_delivered_late(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear("s4")
    rl.fire("s4", "terminal", BODY, ON)
    with rl._PENDING_LOCK:
        rl._PENDING["s4"]["ts"] = 0.0
    assert rl.take("s4") == ""


def test_thin_result_never_calls_the_model(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: pytest.fail("must not call"))
    assert rl.fire("s5", "terminal", "ok", ON) == ""


def test_transport_failure_fails_open(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: None)
    assert rl.fire("s6", "terminal", BODY, ON) == ""


def test_duplicate_situation_is_not_paid_for_twice(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    r = {"messages": [{"role": "tool", "content": BODY}]}
    assert rl.run_on_request(r, session_id="e2", cfg=ON) is True
    assert rl.run_on_request({"messages": [{"role": "tool", "content": BODY}]},
                             session_id="e2", cfg=ON) is False


def test_every_call_delivers_on_the_request_surface(monkeypatch):
    """Produced from the live request, injected there, never persisted as an assistant turn."""
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    req = {"messages": [{"role": "user", "content": "fix the renderer"},
                        {"role": "tool", "content": BODY}]}
    assert rl.run_on_request(req, session_id="e1", cfg=ON) is True
    last = req["messages"][-1]
    assert "80%" in last["content"]
    assert last["role"] == "user", "the reading must not become an assistant turn"


def test_no_work_in_request_means_no_call(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: pytest.fail("must not call"))
    assert rl.run_on_request({"messages": [{"role": "user", "content": "hi"}]},
                             session_id="e3", cfg=ON) is False


def test_delivered_reading_is_attributable():
    rl.clear("s8")
    rl._park("s8", "block body")
    req = {"messages": [{"role": "user", "content": "hi"}]}
    assert rl.inject_pass(req, "s8")
    body = req["messages"][-1]["content"]
    assert "reflex" in body.lower() and "block body" in body


def test_mindset_dial_maps_five_levels_onto_signed_range():
    cands = dx.candidate_set(BODY)
    got = dx.parse({"answers": {"action": {"probabilities": {"a1": 0.7, "a2": 0.3}},
                                "scope": {"score": 0}, "rigour": {"score": 4}}}, cands)
    assert got["mindset"] == {"scope": -1.0, "rigour": 1.0}
