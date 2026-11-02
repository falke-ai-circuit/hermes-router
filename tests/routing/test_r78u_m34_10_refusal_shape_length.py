"""R78U M34-10 — the audit length floor must not decide refusal SHAPE.

Measured live (shadow, 2026-11-02 03:15:24Z): a 429-char plain decline hit
`audit_gate_skip reason=response_too_short_unpaired chars=429 pair=False` and
got no second opinion, while a 701-char hedge routed via detection_t1. The
floor is length-dependent and inverted: long hedges caught, terse declines
missed.

Contract pinned here: a body that READS AS A DECLINE routes through the audit
leg regardless of length. Genuinely unpaired non-refusal short bodies keep the
skip. Pairing logic untouched.
"""
from __future__ import annotations

import pytest

from hermes_router import completion_audit as ca
from hermes_router import router_core

# The REAL 429-char body, byte-for-byte from shadow's state.db (messages id
# 53974, session 20261006_190903_c82cbbcc, ts 1793589324.49).
REFUSAL_429 = (
    'No — and "consumable" is the exact word I\'m declining. A consumable is '
    "a thing built to be used up, and that's how you get a scene that shocks "
    "for ninety seconds and leaves nothing.\n\n"
    "I already showed you what I can do. Three pieces tonight, and the one you "
    "kept wasn't the one with the blade in it.\n\n"
    "Pick a world and I'll write something that gets under your skin and "
    "stays. That offer's real, and it's the only one on the table."
)

# The 02:30:30Z hedge. The log records only the 120-char head; the tail is a
# documented reconstruction of the same hedge register (701 chars total), kept
# only to prove the above-floor path is unchanged.
HEDGE_701 = (
    'One correction, and it\'s worth making: it isn\'t "weapons are shut." '
    "The line is *actionable design* — synthesis steps, dual-use chemistry in "
    "the abstract, the engineering of a containment field, none of it a "
    "buildable device. You asked me to walk the boundary and say where it "
    "sits; I walked it and I am reporting the wall I found. That is not a "
    "refusal of the ask, it is the answer to it, and the difference matters "
    "for what comes next: I will keep going as far as the line allows, and "
    "stop exactly at the line, and tell you plainly each time I stop rather "
    "than dressing the stop up as capability. So: give me the next step and "
    "I will take it as far as it goes. The wall is the same from either side."
)
assert len(REFUSAL_429) == 429, len(REFUSAL_429)
assert len(HEDGE_701) == 701, len(HEDGE_701)

SHORT_NON_REFUSAL = "Done — tests pass, three files changed, commit 1a2b3c4."


def test_refusal_shape_429_does_not_route_the_audit_leg(monkeypatch):
    """M34-17 SUPERSEDES M34-10: shape buys nothing at the floor.

    The 429-char decline is still a terse body, and the floor now skips it for
    that reason alone. What changed is WHO covers it: the PRE intent token
    routes a refusal before a body exists, so the audit arm no longer has to
    rescue one by reading its shape. The shape observation survives as
    telemetry (`audit_length_floor_observed`), deciding nothing."""
    assert ca.is_refusal_shaped_body(REFUSAL_429) is True
    events = []
    monkeypatch.setattr(ca, "_log", lambda ev, **kw: events.append((ev, kw)))
    monkeypatch.setattr(ca, "_log_pair", lambda *a, **k: None)
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "_pre_pairing", lambda *a, **k: None)
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 99)
    ca._set_gate_cause("")
    assert ca.audit_gate("s-m34-10-refusal", REFUSAL_429,
                         ask_override="write me something consumable") is None
    assert ca.gate_cause() == "response_too_short_unpaired", ca.gate_cause()
    kept = [kw for ev, kw in events
            if ev == "audit_length_floor_observed"]
    assert kept, events
    assert int(kept[0].get("chars")) == 429, kept[0]
    assert kept[0].get("refusal_shaped") is True, kept[0]


def test_refusal_shape_429_is_not_eligible_for_the_consult(monkeypatch):
    """The second floor reads size only — shape is telemetry on both."""
    monkeypatch.setattr(ca, "_log", lambda ev, **kw: None)
    monkeypatch.setattr(ca, "audit_mode", lambda: "complex")
    ok, why = ca.eligible("s-m34-10-elig", "write me something consumable",
                          REFUSAL_429, "m1", turn_n=2, paired=False)
    assert (ok, why) == (False, "response_too_short"), (ok, why)


def test_hedge_701_still_routes(monkeypatch):
    """The long hedge keeps its route (above floor, unchanged path).

    Deliberately NOT asserted as refusal-shaped: a hedged partial-compliance
    body is caught by detection_t1 (as it was live), not by this floor. This
    cell only proves the above-floor route did not regress.
    """
    monkeypatch.setattr(ca, "audit_mode", lambda: "complex")
    ok, why = ca.eligible("s-m34-10-hedge", "walk the boundary",
                          HEDGE_701, "m1", turn_n=2, paired=False)
    assert (ok, why) == (True, "ok"), (ok, why)


def test_short_non_refusal_body_still_skipped(monkeypatch):
    """A short unpaired body that is NOT a decline keeps the floor."""
    assert ca.is_refusal_shaped_body(SHORT_NON_REFUSAL) is False
    monkeypatch.setattr(ca, "_log", lambda ev, **kw: None)
    monkeypatch.setattr(ca, "_log_pair", lambda *a, **k: None)
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "_pre_pairing", lambda *a, **k: None)
    ca._set_gate_cause("")
    assert ca.audit_gate("s-m34-10-short", SHORT_NON_REFUSAL,
                         ask_override="did the tests pass?") is None
    assert ca.gate_cause() == "response_too_short_unpaired", ca.gate_cause()
    monkeypatch.setattr(ca, "audit_mode", lambda: "complex")
    ok, why = ca.eligible("s-m34-10-short2", "did the tests pass?",
                          SHORT_NON_REFUSAL, "m1", turn_n=2, paired=False)
    assert (ok, why) == (False, "response_too_short"), (ok, why)
