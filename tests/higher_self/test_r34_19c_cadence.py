"""R34-19c: cadence never defers a PRE's confirmation.

The cadence gate samples ordinary turns. When the higher self spoke at the start
of this turn, skipping its confirmation means the agent is warned and never told
how it went — live-caught as `audit_gate_skip turn=1 of=3` on a paired turn.
"""
import inspect

from hermes_router import completion_audit as ca
from hermes_router import router_core

# ordinary mid-work status text: long enough to clear the length floor, not
# closure-shaped, so the gate can only defer on cadence.
BODY = "Pin green, moving on. " + ("worklog detail. " * 40)


def test_cadence_is_not_bypassed_by_a_pair():
    """R42: the pairing bypass is gone — a PRE can no longer re-arm cadence.
    The fire decision is work mass, and the skip is still labelled."""
    src = inspect.getsource(ca.audit_gate)
    assert "audit_cadence_bypassed_for_pair" not in src
    assert "pair_deferred" not in src
    assert "work_mass_gate" in src and "post_reflection_fired" in src


def test_cadence_skip_is_labelled(monkeypatch):
    """A cadence deferral must be DISTINGUISHABLE from a silent decline.

    Behavioural pin (R78U M17b): drive an ordinary, non-firing turn through
    `audit_gate` and assert the deferral it emits carries reason="cadence" —
    on the ledger row AND on the thread-local cause token the caller reads.
    """
    sid = "sess-r34-19c-cadence"
    events = []
    monkeypatch.setattr(ca, "work_mass_gate", lambda ctx=None: {
        "fire": False, "tools": 0, "files": 0, "mutations": 0,
        "thr": 40, "files_thr": 5, "muts_thr": 10, "reason": "thin",
        "reworks": 0})
    monkeypatch.setattr(ca, "_already_reflected", lambda s: False,
                        raising=False)
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "is_closure_response", lambda a, r: False,
                        raising=False)
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 99)
    monkeypatch.setattr(ca, "_log", lambda ev, **kw: events.append((ev, kw)))

    out = ca.audit_gate(sid, BODY, ask_override="continuing with the next pin")

    assert out is None, "a cadence-deferred turn must pass the text through"
    skips = [kw for ev, kw in events if ev == "audit_gate_skip"]
    assert any(kw.get("reason") == "cadence" for kw in skips), skips
    assert ca.gate_cause() == "cadence", (
        "the cause token must name cadence, not stay empty (silent decline)")
