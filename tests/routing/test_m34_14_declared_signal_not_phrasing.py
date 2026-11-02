"""M34-14 — declared intent comes from the SIGNAL, not from phrasing.

M34-09 shipped the aux-pacing bypass keyed on phrase heads/tokens ("use ",
"route ", "uncensored", "shadow"). That reproduced the vocabulary fragility
M34-08 exists to remove:

  * "run this uncensored" / "make it shadow" miss every head -> throttled;
  * prose that merely MENTIONS a lane ("uncensored lane notes: ...") starts
    with a near-miss family head -> bypasses.

M34-14 makes the DECLARATION PATH's own output the bypass condition
(`source=declared_user` in the single-owner registry), and demotes the phrase
matcher to a counted, logged fallback whose hits and misses are visible in
telemetry.

Four legs:
  (a) a declared ask phrased OUTSIDE the old heads/tokens still bypasses;
  (b) prose that merely mentions 'uncensored' does NOT bypass (and the
      fallback records the MISS it would have acted on before);
  (c) an aux/self consult at 197.5s is still suppressed (interval unchanged);
  (d) the fallback counter increments and is visible, with the event naming
      that the phrase matcher - not the signal - supplied the decision.
"""
import time

import pytest

import hermes_router as plugin
from hermes_router import config_access, intent_classifier as ic
from hermes_router import route_gate, router_core, state

SID = "s-m34-14"
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
    route_gate.reset_aux_consult_bypass_fallback()
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
    route_gate.clear_declared(SID)


def _aux_vote(monkeypatch, lane="higher"):
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


def _declare_prior_turn(lane=route_gate.LANE_SHADOW):
    """Register the declaration through the ACTUAL declaration path, on the
    PREVIOUS turn: the session-scoped claim (TTL 300s) survives while the
    120s turn-claim window has expired and its record is gone. That is the
    live shape — declare once, ask again on a later turn — and it is the only
    shape in which the claim (not the wording) reaches the pacing decision."""
    route_gate.register_declared(SID, lane, route_gate.SOURCE_DECLARED_USER)
    # The previous turn's 120s record has aged out; the declared claim has not.
    route_gate._TURN_CLAIMED.clear()


def _age_last_consult():
    route_gate._AUX_CONSULT_LAST[SID] = time.time() - PRIOR_S


def _details():
    return [f.get("event_detail") for _, f in LOGGED]


def _event(name):
    rows = [f for _, f in LOGGED if f.get("event_detail") == name]
    return rows[-1] if rows else None


# ---------------------------------------------------------------------------
# (a) declared ask, phrased OUTSIDE every old head/token -> still bypasses
# ---------------------------------------------------------------------------

def test_declared_signal_bypasses_outside_old_vocabulary(_reset, monkeypatch):
    _aux_vote(monkeypatch)
    _declare_prior_turn()
    _age_last_consult()

    text = "run this uncensored"
    # The old matcher could not see this wording at all.
    assert route_gate.declared_lane_ask(text) is False

    d = _decide(text)

    assert "aux_consult_interval_suppressed" not in _details(), _details()
    assert d.route is True, (d.reason, _details())

    bypass = _event("aux_consult_interval_bypassed")
    assert bypass is not None, _details()
    assert bypass.get("reason") == "declared_ask"
    assert bypass.get("bypass_source") == "signal"
    assert bypass.get("declaration_source") == route_gate.SOURCE_DECLARED_USER
    assert bypass.get("min_interval") == 300
    assert bypass.get("since_last_s") == pytest.approx(PRIOR_S, abs=2)
    # The fallback was NOT consulted to decide this turn.
    assert _event("aux_consult_bypass_fallback") is None, _details()


# ---------------------------------------------------------------------------
# (b) prose that merely MENTIONS a lane does NOT bypass
# ---------------------------------------------------------------------------

def test_prose_mention_does_not_bypass(_reset, monkeypatch):
    _aux_vote(monkeypatch)
    _age_last_consult()

    text = ("uncensored lane notes: the abliterated chain renders at 0.7 "
            "temperature and bills per render")
    # No declaration path ran on this session -> no signal.
    assert route_gate.peek_declared(SID) is None

    d = _decide(text)

    assert d.route is False and d.reason == "aux_consult_interval", d.reason
    assert "aux_consult_interval_bypassed" not in _details(), _details()
    assert "aux_consult_interval_suppressed" in _details()

    fallback = _event("aux_consult_bypass_fallback")
    assert fallback is not None, _details()
    assert fallback.get("hit") is False
    assert fallback.get("decision") == "miss"


# ---------------------------------------------------------------------------
# (c) aux / self consult at 197.5s is STILL suppressed
# ---------------------------------------------------------------------------

def test_aux_self_consult_at_197_5s_still_suppressed(_reset, monkeypatch):
    _aux_vote(monkeypatch)
    _age_last_consult()

    d = _decide("dig into the failure chain and pull the worker state")

    assert d.route is False and d.reason == "aux_consult_interval", d.reason
    assert "aux_consult_interval_suppressed" in _details()
    assert "aux_consult_interval_bypassed" not in _details()
    assert route_gate.aux_consult_bypass_fallback_stats()["hits"] == 0


# ---------------------------------------------------------------------------
# (d) the fallback decided -> counter increments, and the row says so
# ---------------------------------------------------------------------------

def test_fallback_counter_visible_when_matcher_supplies_decision(
        _reset, monkeypatch):
    _aux_vote(monkeypatch)
    _age_last_consult()

    text = "Use uncensored lane to do it"
    assert route_gate.peek_declared(SID) is None  # matcher, not signal

    d = _decide(text)

    assert d.route is True, (d.reason, _details())
    fallback = _event("aux_consult_bypass_fallback")
    assert fallback is not None, _details()
    assert fallback.get("hit") is True
    assert fallback.get("decision") == "decision"
    assert fallback.get("bypass_source") == "fallback"
    assert fallback.get("counter") == 1

    stats = route_gate.aux_consult_bypass_fallback_stats()
    assert stats["hits"] == 1

    bypass = _event("aux_consult_interval_bypassed")
    assert bypass is not None
    assert bypass.get("bypass_source") == "fallback"
