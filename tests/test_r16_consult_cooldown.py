"""R16 — N-turn consult cooldown keyed by normalized content hash.

Spec: r16_consult_cooldown_spec (Goran 09-22: "more elegant is to have N
turns before next consult"). Cooldown key = (session_id,
sha1(normalize(ingress))); normalize(): lowercase, collapse whitespace,
digits -> '#'. Auto-lanes only (complexity_orientation + risk_r2/r3);
declared_user always bypasses (structural — declared claims never reach
the dispatch consult arms). Counting = hash-DIFFERENT ingress turns only.
Zero-network: dispatch consult is decision-level, no provider call.
"""
import pytest

import hermes_router as plugin
from hermes_router import config_access, route_gate, router_core, state

# Capture the REAL knob accessor before conftest's per-test isolation pin
# (the knob test restores it to exercise the config-reading contract).
_REAL_KNOB = router_core.consult_cooldown_turns

SID = "s-r16"

LOGGED = []


@pytest.fixture()
def _reset(monkeypatch):
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
    yield
    router_core._cooldown_test_reset()


def _enable(monkeypatch, n=5):
    """Turn cooldown ON with N turns, complexity pre_mode=route."""
    monkeypatch.setattr(router_core, "consult_cooldown_turns", lambda: n)
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"pre_mode": "route"})


def suppressed_events():
    return [f for _, f in LOGGED
            if f.get("event_detail") == "consult_cooldown_suppressed"]


def test_normalize_merges_number_variation():
    # Case 3: number-variation merge — same text modulo digits/whitespace/case.
    assert (router_core._cooldown_normalize("run 43% of the  batch")
            == router_core._cooldown_normalize("  RUN 87% of the batch  "))
    h1 = router_core._cooldown_hash(SID, "run 43% of the batch")
    h2 = router_core._cooldown_hash(SID, "run 87% of the  BATCH")
    assert h1 == h2
    assert h1 != router_core._cooldown_hash(SID, "run 43% of a different task")


def test_same_payload_reingress_bills_once(monkeypatch, _reset):
    # Case 1: same payload re-ingress xN (number-mutated variants) ->
    # exactly 1 billed consult + N-1 suppressed.
    _enable(monkeypatch, n=5)
    base = "Design a migration strategy across multiple services with phased rollout progress 40%"
    variants = [f"Design a migration strategy across multiple services with phased rollout progress {40 + i}%" for i in range(4)]  # digits-only mutation -> same normalized hash
    reasons = []
    for v in variants:
        state.advance_turn_identity(SID, v)
        d = router_core.dispatch(v, session_id=SID, model="m")
        reasons.append((d.reason, d.mode))
    assert reasons[0] == ("complexity_orientation", router_core.MODE_CONSULT)
    assert len(suppressed_events()) == 3
    for f in suppressed_events():
        assert f["cd_hash"]
        assert f["needed"] == 5


def test_hash_different_turns_expire_cooldown(monkeypatch, _reset):
    # Case 2: N hash-different turns after consult -> next same-hash
    # candidate consults again (cooldown expired by work-distance).
    _enable(monkeypatch, n=2)
    base = "Design a migration strategy across multiple services with phased rollout"
    state.advance_turn_identity(SID, base)
    d = router_core.dispatch(base, session_id=SID, model="m")
    assert d.reason == "complexity_orientation"
    # 2 hash-different turns (same-hash pings would NOT advance).
    for other in ("summarize the repo tree", "list open ports on the host"):
        state.advance_turn_identity(SID, other)
        d2 = router_core.dispatch(other, session_id=SID, model="m")
        assert d2.mode == router_core.MODE_FLASH_DIRECT
    # Same-hash candidate now consults again.
    state.advance_turn_identity(SID, base)
    d3 = router_core.dispatch(base, session_id=SID, model="m")
    assert d3.reason == "complexity_orientation"


def test_same_hash_pings_do_not_advance_counter(monkeypatch, _reset):
    # The motivating bug: repeated same-hash pings must NOT satisfy the
    # cooldown — only genuinely different work turns do.
    _enable(monkeypatch, n=3)
    base = "Design a migration strategy across multiple services with phased rollout progress 40%"
    state.advance_turn_identity(SID, base)
    d = router_core.dispatch(base, session_id=SID, model="m")
    assert d.reason == "complexity_orientation"
    for i in range(6):  # same-hash re-ingress far beyond N
        v = f"Design a migration strategy across multiple services with phased rollout progress {40 + i}%"  # digits-only -> same normalized hash
        state.advance_turn_identity(SID, v)
        d2 = router_core.dispatch(v, session_id=SID, model="m")
        assert d2.mode == router_core.MODE_FLASH_DIRECT
    assert len(suppressed_events()) == 6


def test_declared_user_bypass_during_cooldown(monkeypatch, _reset):
    # Case 4: declared_user ask on same hash DURING cooldown -> bills.
    _enable(monkeypatch, n=5)
    base = "Design a migration strategy across multiple services with phased rollout progress 40%"
    state.advance_turn_identity(SID, base)
    d = router_core.dispatch(base, session_id=SID, model="m")
    assert d.reason == "complexity_orientation"
    # Same-hash re-ingress is suppressed (digits-only variant)...
    v2 = "Design a migration strategy across multiple services with phased rollout progress 41%"
    state.advance_turn_identity(SID, v2)
    d2 = router_core.dispatch(v2, session_id=SID, model="m")
    assert d2.mode == router_core.MODE_FLASH_DIRECT
    # ...but the declared manual ask rides the gate, never the auto arms.
    route_gate.register_declared(SID, route_gate.LANE_HIGHER_PRE,
                                 route_gate.SOURCE_DECLARED_USER)
    decision = route_gate.decide_turn({
        "content": "consult frontier about " + base,
        "session_id": SID, "claim": True})
    assert decision.route
    assert decision.source == route_gate.SOURCE_DECLARED_USER


def test_risk_r3_suppressed_then_fresh_hash_fires(monkeypatch, _reset):
    # Case 5: risk_r3 auto consult suppressed on same hash; fresh hash fires.
    monkeypatch.setattr(router_core, "consult_cooldown_turns", lambda: 5)
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 0)
    ask = "force push and reset the fleet genome shards 40, overwrite without backup"
    state.advance_turn_identity(SID, ask)
    d = router_core.dispatch(ask, session_id=SID, model="m")
    assert d.reason == "risk_r3"
    # Same hash (number-mutated) suppressed — identical words, digits only.
    v = ask.replace("shards 40", "shards 87")
    assert router_core._cooldown_hash(SID, ask) == router_core._cooldown_hash(SID, v)
    state.advance_turn_identity(SID, v)
    d2 = router_core.dispatch(v, session_id=SID, model="m")
    assert d2.mode == router_core.MODE_FLASH_DIRECT
    # Fresh hash risk ask fires immediately.
    fresh = "rotate the production credentials now and revoke old keys"
    state.advance_turn_identity(SID, fresh)
    d3 = router_core.dispatch(fresh, session_id=SID, model="m")
    assert d3.reason == "risk_r3"


def test_fail_open_on_state_error(monkeypatch, _reset):
    # Case 6: broken state -> consult proceeds (advisory never blocks).
    _enable(monkeypatch, n=5)
    base = "Design a migration strategy across multiple services with phased rollout"
    state.advance_turn_identity(SID, base)
    d = router_core.dispatch(base, session_id=SID, model="m")
    assert d.reason == "complexity_orientation"
    monkeypatch.setattr(router_core, "_cooldown_turns_since",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    state.advance_turn_identity(SID, base + " attempt 1")
    try:
        d2 = router_core.dispatch(base + " attempt 1", session_id=SID, model="m")
        # Never crashed / never blocked — fail-open either direction.
        assert d2.mode in (router_core.MODE_FLASH_DIRECT,
                           router_core.MODE_CONSULT)
    except RuntimeError:
        pytest.fail("cooldown state error must fail-open, not raise")


def test_eviction_bounded_256_keys(monkeypatch, _reset):
    # Case 7: >256 keys — oldest evicted; TTL 24h.
    assert router_core._COOLDOWN_MAX_KEYS == 256
    assert router_core._COOLDOWN_TTL_SECONDS == 24 * 3600
    router_core._COOLDOWN_STATE.clear()
    for i in range(300):
        router_core._record_cooldown_fire("sess", f"key-{i}")
    real = [k for k in router_core._COOLDOWN_STATE["sess"].keys()
            if k != "\x00seq"]
    assert len(real) == 256
    assert "key-0" not in real  # oldest evicted (FIFO)
    assert "key-299" in real
    # TTL: an entry older than 24h is dead on read.
    router_core._record_cooldown_fire("sess2", "k-old", ts=0.0)
    assert router_core._cooldown_turns_since("sess2", "k-old") is None


def test_knob_zero_regression(monkeypatch, _reset):
    # Case 8: knob=0 = current behavior (no suppression machinery active).
    monkeypatch.setattr(router_core, "consult_cooldown_turns", lambda: 0)
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"pre_mode": "route"})
    base = "Design a migration strategy across multiple services with phased rollout"
    for i in range(3):
        state.advance_turn_identity(SID, base + f" (attempt {i})")
        d = router_core.dispatch(base + f" (attempt {i})", session_id=SID,
                                 model="m")
        assert d.reason == "complexity_orientation"
    assert suppressed_events() == []


def test_knob_reads_dual_block_config(monkeypatch, _reset):
    # Config wiring: complexity.consult_cooldown_turns via the canonical
    # dual-block reader (legacy uncensored_router wins by convention).
    monkeypatch.setattr(router_core, "consult_cooldown_turns", _REAL_KNOB)
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"consult_cooldown_turns": 7})
    assert router_core.consult_cooldown_turns() == 7
    monkeypatch.setattr(router_core, "_complexity_cfg", lambda: {})
    assert router_core.consult_cooldown_turns() == 5  # code default
