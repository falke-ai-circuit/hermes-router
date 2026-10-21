"""R34-13 — the complexity SKIP path is observable.

Behavioural finding from the live probe: the identical refactor brief fired
`complexity_orientation` at 22:44 and 22:50, did NOT fire at 23:53, then DID
fire at 00:00 — same config, same text. A no-fire left no trace, so the lane
looked non-deterministic with nothing to read.

Contract (log-only, no classifier tuning): every non-route emits
`complexity_skip reason=<class> score=<n>` so the verdict that produced the
skip is in the log. Cooldown skips carry reason=consult_cooldown.
"""
import pytest

import hermes_router as plugin
from hermes_router import config_access, route_gate, router_core, state

SID = "s-r34-13-skip"
LOGGED = []


@pytest.fixture()
def _env(monkeypatch):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    router_core._cooldown_test_reset()
    LOGGED.clear()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(router_core, "_primary_model",
                        lambda: "z-ai/glm-5.3")
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"pre_mode": "route"})
    monkeypatch.setattr(router_core, "consult_cooldown_turns", lambda: 0)
    yield
    router_core._cooldown_test_reset()


def _skips():
    return [f for _, f in LOGGED
            if f.get("event_detail") == "complexity_skip"]


def test_clear_simple_turn_logs_the_classification_verdict(_env):
    state.advance_turn_identity(SID, "summarize the repo tree")
    d = router_core.dispatch("summarize the repo tree", session_id=SID,
                             model="m")
    assert d.mode == router_core.MODE_FLASH_DIRECT
    skips = _skips()
    assert len(skips) == 1, skips
    assert skips[0]["reason"] == "clear_simple"
    assert skips[0]["score"] == 0
    assert skips[0]["stage1"] == "clear_simple"
    assert skips[0]["session_id"] == SID


def test_complex_turn_is_not_a_skip(_env):
    """A routed consult must not emit a skip row — the event means no-fire."""
    brief = ("Design a migration strategy across multiple services with "
             "phased rollout")
    state.advance_turn_identity(SID, brief)
    d = router_core.dispatch(brief, session_id=SID, model="m")
    assert d.reason == "complexity_orientation"
    assert _skips() == []


def test_cooldown_no_fire_logs_complexity_skip(_env, monkeypatch):
    """The observable explanation of the probe's non-determinism: the same
    brief re-ingested inside the cooldown window skips — now visibly."""
    monkeypatch.setattr(router_core, "consult_cooldown_turns", lambda: 5)
    brief = ("Design a migration strategy across multiple services with "
             "phased rollout")
    state.advance_turn_identity(SID, brief)
    first = router_core.dispatch(brief, session_id=SID, model="m")
    assert first.reason == "complexity_orientation"
    LOGGED.clear()
    state.advance_turn_identity(SID, brief)
    second = router_core.dispatch(brief, session_id=SID, model="m")
    assert second.mode == router_core.MODE_FLASH_DIRECT
    skips = _skips()
    assert len(skips) == 1, skips
    assert skips[0]["reason"] == "consult_cooldown"
    assert skips[0]["score"] >= 1
