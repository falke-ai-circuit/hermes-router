"""Fixture for the higher-self lane tests.

Two concerns are deliberately kept apart:

  * WHEN a POST fires — the FIRE POLICY. Since R42 that policy is realised
    WORK MASS (`completion_audit.work_mass_gate`); before R42 it was the
    pairing policy (`post_pair_mode`).
  * WHETHER the confirmation mechanic works once it fires.

Every pin in this directory except `test_r34_19h_post_when_it_makes_sense.py`
and `test_r42_decouple_post_from_pre.py` belongs to the second concern. Their
asks and responses are ordinary text with no tool payload, so under the live
gate they would not fire and the mechanic under test would never be reached —
a wall of failures that had nothing to do with the code being wrong.

This fixture therefore SUPPLIES a fired work-mass decision for this directory.
The policy files override it explicitly (r34_19h pins the retired pairing
policy, r42 pins the work-mass gate itself, including its would-fire logging).
"""
import pytest


@pytest.fixture(autouse=True)
def _pair_confirmation_mechanics(monkeypatch, request):
    nodeid = request.node.nodeid
    if "r34_19h" in nodeid or "r42_" in nodeid:
        yield
        return
    from hermes_router import completion_audit as ca

    def _fired(_context=None):
        return {"fire": True, "tools": 50, "files": 5, "mutations": 10,
                "thr": 40, "files_thr": 5, "muts_thr": 10, "reason": "mass"}

    monkeypatch.setattr(ca, "work_mass_gate", _fired, raising=False)
    monkeypatch.setattr(ca, "_already_reflected", lambda sid: False,
                        raising=False)
    yield
