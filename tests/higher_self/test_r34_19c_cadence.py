"""R34-19c: cadence never defers a PRE's confirmation.

The cadence gate samples ordinary turns. When the higher self spoke at the start
of this turn, skipping its confirmation means the agent is warned and never told
how it went — live-caught as `audit_gate_skip turn=1 of=3` on a paired turn.
"""
import inspect

from hermes_router import completion_audit as ca


def test_cadence_is_not_bypassed_by_a_pair():
    """R42: the pairing bypass is gone — a PRE can no longer re-arm cadence.
    The fire decision is work mass, and the skip is still labelled."""
    src = inspect.getsource(ca.audit_gate)
    assert "audit_cadence_bypassed_for_pair" not in src
    assert "pair_deferred" not in src
    assert "work_mass_gate" in src and "post_reflection_fired" in src


def test_cadence_skip_is_labelled():
    # the reason string makes an ordinary cadence deferral distinguishable
    # from a silent decline
    src = inspect.getsource(ca.audit_gate)
    assert 'reason="cadence"' in src
