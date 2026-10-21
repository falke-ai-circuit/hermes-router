"""Fixture for the higher-self lane tests.

R34-19h split two concerns that used to be one:
  * WHEN a paired POST fires (the worthwhileness policy — `post_pair_mode`), and
  * WHETHER the confirmation mechanic works once it does fire.

Every pin in this directory except `test_r34_19h_post_when_it_makes_sense.py`
belongs to the second concern. Their asks and responses are ordinary mid-work
text, so under the live default ("worthwhile") the gate now correctly DEFERS the
confirmation and the mechanic under test is never reached — which produced a wall
of failures that had nothing to do with the code being wrong.

This fixture pins the fire policy to `always` (the pre-R34-19h behaviour) for
this directory; the policy tests override it explicitly.
"""
import pytest


@pytest.fixture(autouse=True)
def _pair_confirmation_mechanics(monkeypatch, request):
    # the fire-policy file owns that policy and sets it per test
    if "r34_19h" in request.node.nodeid:
        yield
        return
    from hermes_router import completion_audit as ca

    monkeypatch.setattr(ca, "post_pair_mode", lambda: "always")
    yield
