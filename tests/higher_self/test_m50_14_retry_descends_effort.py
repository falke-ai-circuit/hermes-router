"""M50-14 — a starved consult retry must descend effort, not raise the ceiling.

Measured 2026-11-15 (anthropic/claude-sonnet-5.5, one fixed long-form ask, effort x
budget crossed — /opt/data/tmp/effort_probe.json):

    effort=max   mt=12,000   out=12,000  reasoning=12,000  finish=length  chars=0
    effort=max   mt=48,000   out=48,000  reasoning=48,000  finish=length  chars=0
    effort=high  mt=12,000   out= 3,658  reasoning= 2,015  finish=stop    chars=3,424
    effort=high  mt=48,000   out= 3,352  reasoning= 1,648  finish=stop    chars=3,417

At effort=max the model spends the whole allowance thinking and writes nothing, and
quadrupling the ceiling changes nothing except the bill. That is the owner's "we bill
and get no consult" — the worst case. The old ladder responded to finish=length by
raising max_tokens alone, which by construction re-buys the same empty answer.

These pins lock the correction: on a length-exhausted consult, effort descends one
notch (max -> high) so the retry can emit content.
"""
from hermes_router.anchor_exec import _budget_retry_effort


def test_max_descends_to_high():
    assert _budget_retry_effort("max") == "high"


def test_case_and_whitespace_do_not_defeat_the_descent():
    assert _budget_retry_effort(" MAX ") == "high"


def test_xhigh_alias_descends_too():
    """Providers accept xhigh for the same top tier; it must not slip through."""
    assert _budget_retry_effort("xhigh") == "high"


def test_high_is_left_alone():
    """high already produced complete answers — no further descent."""
    assert _budget_retry_effort("high") == "high"


def test_low_and_none_are_untouched():
    assert _budget_retry_effort("low") == "low"
    assert _budget_retry_effort("none") == "none"


def test_absent_effort_stays_absent():
    """No configured effort means the provider default — never invent a level."""
    assert _budget_retry_effort("") == ""
    assert _budget_retry_effort(None) == ""


def test_never_raises_on_junk():
    assert _budget_retry_effort(123) == "123"
    assert _budget_retry_effort(object()) != ""  # str() of it, never an exception
