"""M50-18: a POST model fallback must never be SILENT.

Owner ruling: this lane should NOT fall back at all — PRE and POST must run the same
model. Until that is decided, the fallback gets a log line so a divergence is found
in the log instead of being inferred from behaviour.

Background: operative reported that PRE and POST use different models. Verified false
on the current fleet — resolution is anthropic/claude-sonnet-5.5 for both on all 11
profiles, and live POST events carry model=anthropic/claude-sonnet-5.5. The claim was
only *latently* true: the revision defaults to a hard-coded z-ai/glm-5.3 and reaches
the anchor model via an override that sits inside a swallowed try/except. If that read
throws, the pass silently runs glm-5.3 at reasoning_effort=max — which is exactly the
empty-response pairing the code comment warns about.
"""
from hermes_router.completion_audit import (
    _REVISION_MODEL_FALLBACK,
    _revision_fallback_reason,
)


def test_fallback_reported_when_default_survives():
    assert _revision_fallback_reason("", _REVISION_MODEL_FALLBACK) == "unset_model_key"


def test_no_fallback_when_anchor_model_resolved():
    assert _revision_fallback_reason("", "anthropic/claude-sonnet-5.5") == ""


def test_anchor_failure_is_the_reported_reason():
    r = _revision_fallback_reason("anchor_load_raised:ValueError", "x")
    assert r == "anchor_load_raised:ValueError"


def test_missing_primary_is_reported():
    assert _revision_fallback_reason("no_anchor_primary", "x") == "no_anchor_primary"


def test_reason_wins_even_if_model_looks_fine():
    """A swallowed read failure matters even when a model name is present."""
    assert _revision_fallback_reason("no_anchor_primary",
                                     "anthropic/claude-sonnet-5.5") == "no_anchor_primary"


def test_never_throws_on_junk():
    assert _revision_fallback_reason(None, None) == ""
    assert _revision_fallback_reason(123, _REVISION_MODEL_FALLBACK) == "123"
    assert _revision_fallback_reason("", 0) == ""


def test_fallback_constant_is_the_glm_default():
    """Stated once, so a future edit cannot silently change the fallback target."""
    assert _REVISION_MODEL_FALLBACK == "z-ai/glm-5.3"
