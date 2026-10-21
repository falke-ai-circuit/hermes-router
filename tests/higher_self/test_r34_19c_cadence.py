"""R34-19c: cadence never defers a PRE's confirmation.

The cadence gate samples ordinary turns. When the higher self spoke at the start
of this turn, skipping its confirmation means the agent is warned and never told
how it went — live-caught as `audit_gate_skip turn=1 of=3` on a paired turn.
"""
import inspect

from hermes_router import completion_audit as ca


def test_cadence_bypass_is_explicit_in_source():
    src = inspect.getsource(ca.audit_gate)
    assert "audit_cadence_bypassed_for_pair" in src
    assert "not _fire and not _pair" in src


def test_cadence_skip_is_labelled():
    # the reason string makes an ordinary cadence deferral distinguishable
    # from a silent decline
    src = inspect.getsource(ca.audit_gate)
    assert 'reason="cadence"' in src
