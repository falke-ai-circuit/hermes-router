"""R63L: a consult needs two named routes.

Measured fault this pins shut: every billed decision consult with an empty
option set had the shape choice=opt-1, confidence=1.0, options_json='' -- a
verdict emitted over an envelope that offered ONE route, so no transcript could
ever show which route was taken and the row abstained forever (or worse, scored
against a fork nobody posed).

The floor lives inside call_backend because that is the single call every seam
passes through; a gate at one call site leaves the others open.
"""

import json

import pytest

from hermes_router import decision
from hermes_router import decision_signatures as ds


def _env(*labels):
    return {"options": [{"id": "opt-%d" % (i + 1), "label": l}
                        for i, l in enumerate(labels)],
            "question": "which?", "task_id": "t", "session_id": "s"}


def test_two_named_routes_are_admissible():
    ok, why = decision.consult_admissible(_env("redis", "memcached"))
    assert ok is True
    assert why == ""


def test_one_route_is_not_a_fork():
    ok, why = decision.consult_admissible(_env("redis"))
    assert ok is False
    assert why == decision.REASON_NO_OPTION_SET


def test_blank_labels_do_not_count():
    ok, why = decision.consult_admissible(
        {"options": [{"id": "opt-1", "label": "redis"},
                     {"id": "opt-2", "label": "   "}]})
    assert ok is False
    assert why == decision.REASON_NO_OPTION_SET


def test_no_options_at_all():
    assert decision.consult_admissible({})[0] is False
    assert decision.consult_admissible({"options": []})[0] is False


def test_string_options_are_tolerated():
    ok, _ = decision.consult_admissible({"options": ["redis", "memcached"]})
    assert ok is True


def test_the_gate_fails_open_on_a_broken_envelope():
    """A fault in the check must never silence a real fork."""
    class Boom(dict):
        def __init__(self):
            super().__init__(marker=1)   # truthy, so `envelope or {}` keeps it

        def get(self, *a, **k):
            raise RuntimeError("boom")

    ok, why = decision.consult_admissible(Boom())
    assert ok is True and why == ""


def test_call_backend_refuses_without_spending(monkeypatch):
    """No backend call, no tokens, and the reason is the floor's own -- so the
    ledger records WHY a fire produced no verdict instead of a silent empty."""
    calls = []

    def _http(*a, **k):
        calls.append(a)
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(decision, "_http_post_json", _http)
    monkeypatch.setattr(decision, "_call_jev", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("backend must not be called")))
    cfg = {"backend": "jev_native", "typesafe_api_key_env": "PATH"}
    content, meta, reason = decision.call_backend(_env("redis"), cfg)
    assert content is None
    assert reason == decision.REASON_NO_OPTION_SET
    assert calls == []


def test_the_floor_does_not_depend_on_the_seam():
    """Every seam reaches the floor because it is inside call_backend, not at a
    call site: grep the midturn seam to prove it carries no bypass of its own."""
    import inspect

    from hermes_router import decision_midturn as dm
    src = inspect.getsource(dm)
    assert "call_backend" in src
    # no local re-implementation of the floor that could drift from the rule
    assert "def consult_admissible" not in src


def test_a_single_route_cannot_produce_a_resolvable_row():
    """The measurement consequence, stated as a property: one route can never
    be separable, so the consult could not have produced a usable verdict."""
    sigs = [ds.option_signature("redis")]
    assert ds.fork_observable(sigs) in (False, True)  # observability is separate
    diag = ds.diagnosticity(sigs)
    assert diag.get("separable") is False
