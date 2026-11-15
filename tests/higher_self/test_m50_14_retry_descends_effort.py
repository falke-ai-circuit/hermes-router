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
from hermes_router.anchor_exec import (
    _budget_can_raise,
    _budget_retry_effort,
    _empty_consult_spend,
    _retry_is_worth_paying,
    _retry_raises_ceiling,
)


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


# --- M50-14b: the at-ceiling hole -----------------------------------------
# The ladder used to retry only when the ceiling could rise, so a consult already
# at the model ceiling got NO retry even though descending effort is the cure.

def test_retry_paid_when_ceiling_can_rise():
    assert _retry_is_worth_paying(12000, 48000, "max", "high") is True


def test_retry_paid_at_ceiling_if_effort_descends():
    """The case that was silently skipped: no headroom left, but effort moves."""
    assert _retry_is_worth_paying(47170, 47170, "max", "high") is True


def test_retry_not_paid_when_nothing_changes():
    """At ceiling with effort already descended, a repeat buys the same empty answer."""
    assert _retry_is_worth_paying(47170, 47170, "high", "high") is False


def test_a_shrinking_budget_alone_is_not_worth_paying():
    assert _retry_is_worth_paying(48000, 12000, "low", "low") is False


def test_worth_paying_never_raises_on_junk():
    assert _retry_is_worth_paying(None, None, None, None) is False
    assert _retry_is_worth_paying("bad", "worse", "max", "high") is True


# --- M50-15: do not raise the ceiling when effort already descended ---------

def test_no_ceiling_raise_when_effort_descends():
    """high produced a complete answer in 3,658 tokens at a 12,000 cap — the
    bigger allowance adds no answer and widens exposure to 47,170-64,000."""
    assert _retry_raises_ceiling(True, "max", "high") is False


def test_ceiling_raise_allowed_when_effort_is_unchanged():
    """At high already, the allowance is the only lever left and it does recover
    (11 of 51 paid retries fleet-wide)."""
    assert _retry_raises_ceiling(True, "high", "high") is True


def test_no_ceiling_raise_when_there_is_no_headroom():
    assert _retry_raises_ceiling(False, "high", "high") is False
    assert _retry_raises_ceiling(False, "max", "high") is False


def test_raises_ceiling_never_throws():
    assert _retry_raises_ceiling(True, None, None) is True
    assert _retry_raises_ceiling(None, "max", "high") is False


# --- M50-16: a rise to a non-positive allowance is not a budget -------------
# Found by architect's review: cur=-5, new=0 satisfied `new > cur`, and the retry
# would be dispatched with max_tokens=0 — a guaranteed billed-and-empty call.

def test_zero_allowance_is_not_a_raise():
    assert _budget_can_raise(-5, 0) is False


def test_negative_allowance_is_not_a_raise():
    assert _budget_can_raise(-100, -5) is False


def test_real_rise_is_allowed():
    assert _budget_can_raise(12000, 47170) is True


def test_equal_or_smaller_is_not_a_raise():
    assert _budget_can_raise(47170, 47170) is False
    assert _budget_can_raise(48000, 12000) is False


def test_can_raise_never_throws():
    assert _budget_can_raise(None, None) is False
    assert _budget_can_raise("junk", "junk") is False
    assert _budget_can_raise(None, 12000) is True


def test_worth_paying_rejects_zero_allowance_rise():
    """The exact architect finding: junk current budget must not buy a retry."""
    assert _retry_is_worth_paying(-5, 0, "high", "high") is False


# --- M50-17: price the empty consult, which returns above record_spend -------

def test_empty_consult_spend_sums_every_billed_call(monkeypatch):
    """A starved consult can cost TWO billed calls (original + retry). Both count."""
    import hermes_router.anchor_exec as ax

    monkeypatch.setattr(ax, "usage_cost_from_response",
                        lambda raw, pricing, model: 0.5)
    assert ax._empty_consult_spend([{"a": 1}, {"b": 2}], {}, "m") == 1.0


def test_empty_consult_spend_ignores_missing_responses(monkeypatch):
    import hermes_router.anchor_exec as ax

    monkeypatch.setattr(ax, "usage_cost_from_response",
                        lambda raw, pricing, model: 0.5)
    assert ax._empty_consult_spend([None, {}, {"a": 1}], {}, "m") == 0.5


def test_empty_consult_spend_is_zero_when_nothing_billed():
    assert _empty_consult_spend([], {}, "m") == 0.0
    assert _empty_consult_spend(None, {}, "m") == 0.0


def test_empty_consult_spend_never_throws(monkeypatch):
    """A pricing blow-up must never break the lane."""
    import hermes_router.anchor_exec as ax

    def _boom(*a, **k):
        raise ValueError("pricing down")

    monkeypatch.setattr(ax, "usage_cost_from_response", _boom)
    assert ax._empty_consult_spend([{"a": 1}], {}, "m") == 0.0




