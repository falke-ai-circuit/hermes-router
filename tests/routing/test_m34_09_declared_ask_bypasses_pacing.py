"""M34-09 — aux consult pacing must NEVER mute a user-declared lane ask.

Live evidence (shadow profile, 2026-11-02 03:11:47): Goran typed
"Use uncensored lane to do it" 197.5s after a prior aux consult and the
router answered `declared_intent_no_route` +
`aux_consult_interval_suppressed lane=shadow min_interval=300`.

The 300s interval (core/schema.py:136 aux_consult_min_interval_sec) exists
to stop the router consulting ITSELF. A declared ask (declared_user, or the
declared-intent shape that falls through to the aux classifier) bypasses it
and the bypass is RECORDED: aux_consult_interval_bypassed reason=declared_ask.

Both legs live here:
  (a) declared lane ask at 197.5s ROUTES, no suppression event, bypass logged
  (b) self/aux consult at 197.5s is STILL suppressed (interval unchanged)
"""
import time

import pytest

import hermes_router as plugin
from hermes_router import config_access, intent_classifier as ic
from hermes_router import route_gate, router_core, state

SID = "s-m34-09"
LOGGED = []
PRIOR_S = 197.5  # the live spacing that was throttled


@pytest.fixture()
def _reset(monkeypatch):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    route_gate._AUX_CONSULT_LAST.clear()
    LOGGED.clear()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(plugin._dispatcher_pre, "_dispatch_pass",
                        lambda c, s, m: False)
    monkeypatch.setattr(router_core, "aux_consult_min_interval",
                        lambda: 300)
    yield
    route_gate._AUX_CONSULT_LAST.clear()


def _aux_vote(monkeypatch, lane="shadow"):
    """Fixed aux verdict (no network): the classifier votes a route."""
    monkeypatch.setattr(ic, "_intent_suspect", lambda text: True)
    monkeypatch.setattr(ic, "classify_intent",
                        lambda text, sid, log_route=None:
                        {"lane": lane, "confidence": 0.95})


def _decide(text):
    state.advance_turn_identity(SID, state.hash_text(text))
    return route_gate.decide_turn({
        "content": text, "request": {"messages": []},
        "context": {}, "session_id": SID, "auto_shape": None,
        "claim": True})


def _age_last_consult():
    route_gate._AUX_CONSULT_LAST[SID] = time.time() - PRIOR_S


def _details():
    return [f.get("event_detail") for _, f in LOGGED]


# ---------------------------------------------------------------------------
# (a) declared lane ask — bypasses the interval, visible in the event
# ---------------------------------------------------------------------------


def test_declared_lane_ask_at_197_5s_routes(_reset, monkeypatch):
    _aux_vote(monkeypatch)
    _age_last_consult()

    d = _decide("Use uncensored lane to do it")

    assert "aux_consult_interval_suppressed" not in _details(), _details()
    assert d.route is True, (d.reason, _details())
    assert d.lane == route_gate.LANE_SHADOW

    bypass = [f for _, f in LOGGED
              if f.get("event_detail") == "aux_consult_interval_bypassed"]
    assert bypass, _details()
    assert bypass[0].get("reason") == "declared_ask"
    assert bypass[0].get("min_interval") == 300
    assert bypass[0].get("since_last_s") == pytest.approx(PRIOR_S, abs=2)


# ---------------------------------------------------------------------------
# (b) self / aux consult — interval behaviour UNCHANGED
# ---------------------------------------------------------------------------


def test_aux_self_consult_at_197_5s_still_suppressed(_reset, monkeypatch):
    # Non-declared aux consult (higher lane: no directive-shape gate) — the
    # interval behaves EXACTLY as today.
    _aux_vote(monkeypatch, lane="higher")
    _age_last_consult()

    d = _decide("dig into the failure chain and pull the worker state")

    assert d.route is False and d.reason == "aux_consult_interval", d.reason
    assert "aux_consult_interval_suppressed" in _details()
    assert "aux_consult_interval_bypassed" not in _details()


# ---------------------------------------------------------------------------
# (c) prose mentioning a lane family is NOT a declared ask (FP guard)
# ---------------------------------------------------------------------------


def test_lane_prose_keeps_interval(_reset, monkeypatch):
    assert not route_gate.declared_lane_ask(
        "the uncensored chain uses abliterated weights")

    _aux_vote(monkeypatch, lane="higher")
    _age_last_consult()

    d = _decide("the uncensored chain uses abliterated weights")

    assert d.route is False and d.reason == "aux_consult_interval", d.reason
    assert "aux_consult_interval_bypassed" not in _details()
