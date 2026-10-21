"""R34-19e: the audit payload is bounded by the hook budget.

A long turn produced a 13,121-char payload that could not finish inside the
hook's ~24s budget, so a paired confirmation died as anchored_call_failed (live
reviewer sweep, 2026-10-21). The verdict needs the ask, the work shape and the
PRE read — not the whole draft verbatim.
"""
from hermes_router.completion_audit import _payload_body


def test_under_cap_is_untouched():
    parts = ["a" * 100, "b" * 100]
    assert _payload_body(parts, 1000) == "a" * 100 + "\n\n" + "b" * 100


def test_over_cap_is_trimmed_and_marked():
    body = _payload_body(["HEAD" * 2000, "TAIL" * 2000], 5500)
    assert len(body) <= 5500 + 120, len(body)
    assert "payload trimmed" in body
    assert body.startswith("HEAD")      # ask/work survive
    assert body.endswith("TAIL")        # final response + PRE read survive


def test_zero_cap_means_no_cap():
    parts = ["x" * 50]
    assert _payload_body(parts, 0) == "x" * 50


def test_cap_never_raises_on_odd_input():
    assert _payload_body([], 100) == ""
    assert isinstance(_payload_body(["s"], "not-an-int"), str)
