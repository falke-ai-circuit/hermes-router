"""R34-19b: the banner names WHICH END of the consult it marks.

Both ends attach at the delivery edge, so the PRE banner renders below the
answer. With trigger text 'consult' vs 'completion_audit' the user read the PRE
banner as a post banner. 'pre-check' / 'post-check' makes the position honest.
"""
import pytest

from hermes_router import debug_banner as db


def _banner(trigger):
    return db.format_banner(
        lane="frontier-anchor", trigger=trigger, model="anthropic/claude-sonnet-5.5",
        endpoint="https://inference-api.nousresearch.com/v1", tokens_in=1000,
        tokens_out=200, est_cost=0.0123, latency_s=5.0, level=1)


def test_pre_consult_banner_says_pre_check():
    b = _banner("consult")
    assert "pre-check" in b
    assert "| consult |" not in b


def test_post_audit_banner_says_post_check():
    b = _banner("completion_audit")
    assert "post-check" in b
    assert "completion_audit" not in b


def test_orientation_is_also_the_pre_end():
    assert "pre-check" in _banner("orientation")


def test_unknown_trigger_passes_through_unchanged():
    # shadow/decision lanes keep their own trigger names
    assert "refusal_phrases" in _banner("refusal_phrases")


@pytest.mark.parametrize("trigger", ["consult", "completion_audit"])
def test_labels_stay_inside_the_one_liner_budget(trigger):
    b = _banner(trigger)
    assert len(b) <= db.MAX_BANNER_CHARS
    assert "\n" not in b  # L1 is a single line
