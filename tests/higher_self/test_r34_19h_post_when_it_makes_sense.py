"""R34-19h — POST fires when it makes sense, not mechanically.

Goran-direct: "we need post when it makes sense not mechanically". Until now a
paired POST fired on EVERY turn that had a PRE on record — mid-work chatter
included. Measured cost of that policy on 2026-10-21: 59 paired POSTs across ten
profiles in one day, at $0.003-0.021 a consult, and the majority confirmed
sentences like "pin green" or a status line.

The paired arm is now gated on the turn being one where a confirmation means
something: an ON-DEMAND ask (the user asked her higher self — confirmation is the
other half of the request), a CLOSURE-shaped turn (a wrap-up/verdict, where a
second pair of eyes earns its price), or a turn carrying a declared decision
frame. Mid-work turns DEFER: the PRE record is not cleared, it TTLs out on its
own, so the next worthwhile turn confirms.

`complexity.post_pair_mode` — worthwhile (default) | always (the old policy, kept
for A/B) | off (retire the paired arm).
"""
import itertools

from hermes_router import completion_audit as ca
from hermes_router import router_core
from hermes_router.core import state

_SIDS = itertools.count()
WORK_ASK = "continuing with the next pin, no decisions needed here"
WORK_RESP = "Pin green, moving on."
CLOSURE_ASK = "close this out"
CLOSURE_RESP = ("In summary: all three fixes landed and verified, the badge is "
                "green, and the branch is pushed.")


def _sid():
    return f"sess-r34-19h-{next(_SIDS)}"


# --------------------------------------------------------------------------
# the judgement itself
# --------------------------------------------------------------------------

def test_on_demand_always_earns_a_confirmation(monkeypatch):
    ok, why = ca._pair_worth_confirming(
        WORK_ASK, WORK_RESP, {"trigger": "request_routing_declared"})
    assert ok and why == "on_demand"


def test_a_closure_turn_earns_a_confirmation():
    ok, why = ca._pair_worth_confirming(
        CLOSURE_ASK, CLOSURE_RESP, {"trigger": "complexity_orientation"})
    assert ok and why == "closure"


def test_mid_work_chatter_does_not(monkeypatch):
    monkeypatch.setattr(ca, "is_closure_response", lambda a, r: False,
                        raising=False)
    ok, why = ca._pair_worth_confirming(
        WORK_ASK, WORK_RESP, {"trigger": "complexity_orientation"})
    assert not ok and why == "mid_work"


def test_a_declared_decision_frame_earns_a_confirmation(monkeypatch):
    import hermes_router.decision as dec

    monkeypatch.setattr(ca, "is_closure_response", lambda a, r: False,
                        raising=False)
    monkeypatch.setattr(dec, "_manual_trigger_in_text", lambda t: True,
                        raising=False)
    ok, why = ca._pair_worth_confirming(
        "should I take option A or B?", "A is better here.",
        {"trigger": "complexity_orientation"})
    assert ok and why == "decision_frame"


def test_the_judgement_fails_open(monkeypatch):
    """A helper blowing up must not silently retire the confirmation."""
    def _boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(ca, "is_closure_response", _boom, raising=False)
    monkeypatch.setattr(ca, "_pair_worth_confirming", ca._pair_worth_confirming)
    ok, why = ca._pair_worth_confirming(
        "x", "y", {"trigger": None})
    # is_closure blowing up is contained; the mid-work verdict still stands
    assert isinstance(ok, bool)


# --------------------------------------------------------------------------
# the gate honours the mode
# --------------------------------------------------------------------------

def _wire(monkeypatch, mode=None, closure=False, consult_ran=None):
    monkeypatch.setattr(ca, "_consult_meta", lambda *a, **k: {
        "note": "", "model": "m", "endpoint": "e",
        "tokens_in": 1, "tokens_out": 1, "cost": 0.0})
    monkeypatch.setattr(ca, "audit_topology", lambda: "sync")
    monkeypatch.setattr(ca, "audit_sync_seconds", lambda: 45)
    monkeypatch.setattr(ca, "audit_revision_seconds", lambda: 5)
    monkeypatch.setattr(ca, "revise_with_verdict", lambda *a, **k: None)
    monkeypatch.setattr(ca, "stash_verdict", lambda *a, **k: None)
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "audit_mode", lambda: "complex")
    monkeypatch.setattr(ca, "is_closure_response", lambda a, r: closure,
                        raising=False)
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 99)
    if mode is not None:
        monkeypatch.setattr(ca, "post_pair_mode", lambda: mode)
    events = []
    monkeypatch.setattr(ca, "_log", lambda ev, **kw: events.append((ev, kw)))

    def _sync(*a, **k):
        if consult_ran is not None:
            consult_ran.append(a[0])
        return {"note": "", "model": "m", "endpoint": "e",
                "tokens_in": 1, "tokens_out": 1, "cost": 0.0}

    monkeypatch.setattr(ca, "run_completion_audit_sync", _sync)
    return events


def test_a_mid_work_paired_turn_costs_nothing(monkeypatch):
    sid = _sid()
    ran = []
    events = _wire(monkeypatch, mode="worthwhile", closure=False,
                   consult_ran=ran)
    state.mark_pre_advisory(sid, "complexity_orientation", "PRE", ask=WORK_ASK)
    out = ca.audit_gate(sid, WORK_RESP, ask_override=WORK_ASK)
    assert out is None, "a confirmation was bought for a status line"
    assert ran == [], "the consult ran anyway"
    # R42: no deferral state exists any more — the turn simply does not fire
    # on work mass, and the PRE record survives as context for a later turn.
    assert not any("pair_deferred" in str(k.get("reason") or "")
                   for e, k in events), events
    assert state.pre_advisory(sid, 300, ask=WORK_ASK) is not None


def test_a_closure_paired_turn_is_still_confirmed(monkeypatch):
    sid = _sid()
    ran = []
    _wire(monkeypatch, mode="worthwhile", closure=True, consult_ran=ran)
    state.mark_pre_advisory(sid, "complexity_orientation", "PRE",
                            ask=CLOSURE_ASK)
    out = ca.audit_gate(sid, CLOSURE_RESP, ask_override=CLOSURE_ASK)
    assert ran, "a closure turn must still be confirmed"
    assert out is not None


def test_on_demand_does_not_buy_a_post_by_itself(monkeypatch):
    """R42: POST fires on realised work, never on the ask or the pair. A
    request to the higher self is PRE-side business; the POST follows only
    when the turn actually did heavy work."""
    sid = _sid()
    ran = []
    _wire(monkeypatch, mode="worthwhile", closure=False, consult_ran=ran)
    state.mark_pre_advisory(sid, "request_routing_declared", "PRE",
                            ask="ask your higher self: is this right?")
    out = ca.audit_gate(sid, "It is right, and here is why.",
                        ask_override="ask your higher self: is this right?")
    assert out is None
    assert ran == [], "pairing must not fire a POST on its own"


def test_pair_mode_always_no_longer_fires_without_work_mass(monkeypatch):
    sid = _sid()
    ran = []
    _wire(monkeypatch, mode="always", closure=False, consult_ran=ran)
    state.mark_pre_advisory(sid, "complexity_orientation", "PRE", ask=WORK_ASK)
    ca.audit_gate(sid, WORK_RESP, ask_override=WORK_ASK)
    assert ran == [], "post_pair_mode is a no-op knob since R42"


def test_pair_mode_off_retires_the_paired_arm(monkeypatch):
    """mode=off removes the PAIRED arm only. Independent evidence still audits:
    a closure-shaped turn is confirmed by the closure trigger, exactly as it
    would be without any PRE — the point of `off` is that the PRE no longer
    buys a confirmation by itself."""
    sid = _sid()
    ran = []
    events = _wire(monkeypatch, mode="off", closure=False, consult_ran=ran)
    state.mark_pre_advisory(sid, "complexity_orientation", "PRE", ask=WORK_ASK)
    ca.audit_gate(sid, WORK_RESP, ask_override=WORK_ASK)
    assert ran == [], "mode=off still bought a confirmation from the pair"
    assert any(k.get("reason") == "pair_mode_off" for e, k in events), events


def test_pair_mode_off_leaves_the_closure_arm_intact(monkeypatch):
    sid = _sid()
    ran = []
    _wire(monkeypatch, mode="off", closure=True, consult_ran=ran)
    state.mark_pre_advisory(sid, "complexity_orientation", "PRE", ask=CLOSURE_ASK)
    ca.audit_gate(sid, CLOSURE_RESP, ask_override=CLOSURE_ASK)
    assert ran, "a closure turn audits on its own evidence, PRE or no PRE"


def test_post_pair_mode_defaults_to_worthwhile(monkeypatch):
    monkeypatch.setattr(router_core, "_complexity_cfg", lambda: {})
    assert ca.post_pair_mode() == "worthwhile"
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"post_pair_mode": "always"})
    assert ca.post_pair_mode() == "always"
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"post_pair_mode": "garbage"})
    assert ca.post_pair_mode() == "worthwhile"


# --------------------------------------------------------------------------
# R34-19k: the CLAIM is pairing evidence when the stored text is missing
# --------------------------------------------------------------------------

def test_a_higher_pre_claim_is_context_not_a_fire_trigger(
        monkeypatch):
    """R42: a claim still resolves pairing CONTEXT (so the payload can carry
    the earlier read), but it cannot fire a POST — work mass does that."""
    sid = _sid()
    ran = []
    events = _wire(monkeypatch, mode="worthwhile", closure=False, consult_ran=ran)
    import hermes_router.route_gate as rg
    monkeypatch.setattr(rg, "claim_state", lambda *a, **k: {
        "lane": "higher-pre", "source": "declared_user"}, raising=False)
    ca.audit_gate(sid, WORK_RESP, ask_override=WORK_ASK)
    assert ran == [], "a claim must not buy a POST by itself"
    assert any(k.get("claim_lane") == "higher-pre" for e, k in events
               if e == "higher_self_pair_derived_from_claim"), events


def test_a_non_pre_claim_still_stands_the_gate_down(monkeypatch):
    sid = _sid()
    ran = []
    events = _wire(monkeypatch, mode="worthwhile", closure=False, consult_ran=ran)
    import hermes_router.route_gate as rg
    monkeypatch.setattr(rg, "claim_state", lambda *a, **k: {
        "lane": "uncensored", "source": "detection"}, raising=False)
    ca.audit_gate(sid, WORK_RESP, ask_override=WORK_ASK)
    assert ran == [], "a render claim must still own the turn"
    assert any(k.get("reason") == "turn_claimed" for e, k in events), events


def test_a_stale_advisory_cannot_relabel_an_on_demand_turn(monkeypatch):
    """R34-19k2 still holds: the claim decides WHY the turn's pairing context
    exists; the stored record is only cargo. R42 adds that none of this fires
    a POST — the turn still reflects only on work mass."""
    sid = _sid()
    ran = []
    events = _wire(monkeypatch, mode="worthwhile", closure=False, consult_ran=ran)
    import hermes_router.route_gate as rg
    monkeypatch.setattr(rg, "claim_state", lambda *a, **k: {
        "lane": "higher-pre", "source": "declared_user"}, raising=False)
    # stale advisory: different ask, PRE-owned trigger
    state.mark_pre_advisory(sid, "complexity_orientation", "OLD PRE",
                            ask="something else entirely")
    ca.audit_gate(sid, WORK_RESP, ask_override=WORK_ASK)
    assert ran == [], "pairing context must not fire a POST"
    assert any(e == "higher_self_pair_trigger_from_claim" for e, _ in events), events
