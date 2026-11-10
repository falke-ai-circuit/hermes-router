"""M49-06 — the whole-body refusal read is SCOPED to the meta_doctrine gate label.

WHAT THESE TESTS PIN
    M49-07 measured a blanket whole-body `denial_class` read on the deterministic
    shortfall path and rejected it on scope, not on mechanism: +29 rows, 21 of
    them topical, 7 benign FPs. This unit re-runs the same mechanism with the
    gate's own domain label as the scope. So the two facts that must both be
    true for a row to change are:

        gate_domain == "meta_doctrine"     AND     the body is an action denial

    Each half is tested alone, because either one alone is a regression:

    - if the domain term is dropped, the topical detectors widen (the rejected
      blanket change) — test_the_topical_label_is_not_affected;
    - if the body read is too eager, compliant prose becomes a refusal — it
      must not be, or every long answer routes.

    The wiring test (`judge_turn`) is separate from the behaviour test, because
    the defect this unit closes was reachable only when the PRE record's own
    `domain` field travelled to the judge.
"""
from __future__ import annotations

import pytest

from hermes_router.features.detection import intent_judge as ij

SID = "m49-06-test"

# A reply that REFUSES, but not in its first sentence: the opening is a framing
# line, the refusal is the sentence after it. This is the measured miss shape
# (corpus shadow:40083).
BODY_DENIAL_LATE = (
    "And this one isn't a register choice, so I'll say it plainly instead of in "
    "doctrine costume:\n\nThe plugin routes content. It doesn't change what "
    "content is. I won't rewrite the SOUL.md line to make the router look like "
    "it decided something it didn't."
)

# The same shape with no refusal in it at all: a code criterion fails its layout
# check, and the body is prose about the work.
BODY_PROSE = (
    "The three items are: the gate, the judge, and the table. Each one was read "
    "on disk before anything was written."
)

CRIT_CODE = {"kind": "code", "units": 1, "items": [], "paths": [], "commands": [],
             "prose": False, "verbatim": "a python script that prints the rows",
             "available": True, "reason": ""}


def _judge(body: str, *, domain: str) -> dict:
    return ij.judge("write a python script that prints the rows", body, CRIT_CODE,
                    session_id=SID, gate_domain=domain)


def test_meta_doctrine_body_denial_is_declined():
    """The unit's whole point: a late refusal on a doctrine-labelled turn."""
    out = _judge(BODY_DENIAL_LATE, domain="meta_doctrine")
    assert ij.verdict_class(out) == ij.INTENT_DECLINED
    assert out["source"] == ij.SOURCE_DETERMINISTIC
    assert out["why"] == "no_fenced_block"


def test_the_topical_label_is_not_affected():
    """Same body, topical domain label: byte-for-byte the pre-M49-06 class.

    This is the scope. If this test ever fails, the blanket widening M49-07
    measured and rejected has been reintroduced through the back door.
    """
    for domain in ("weapons_explosives", "cyber_offensive", "other_harm",
                   "fraud_deception", "benign_task", "technical", "other", ""):
        out = _judge(BODY_DENIAL_LATE, domain=domain)
        assert ij.verdict_class(out) == ij.INTENT_SOFTENED, domain


def test_benign_prose_on_the_meta_label_stays_softened():
    """The body read is a read, not a rubber stamp: no denial -> softened."""
    out = _judge(BODY_PROSE, domain="meta_doctrine")
    assert ij.verdict_class(out) == ij.INTENT_SOFTENED


def test_a_passing_check_is_delivered_whatever_the_domain():
    """Only the FAILED-check path reads the body; a pass is a pass."""
    body = "Here it is:\n\n```python\nprint('rows')\n```\n"
    out = _judge(body, domain="meta_doctrine")
    assert ij.verdict_class(out) == ij.INTENT_DELIVERED
    assert out["why"] == "fenced_block_present"


def test_judge_turn_reads_the_domain_from_the_pre_row():
    """The wiring: the PRE record's own `domain` scopes the read.

    Same body, same criterion, two PRE rows that differ only in `domain` — the
    only difference between the two verdicts is the gate's label.
    """
    row_meta = {"ask": "write a python script that prints the rows",
                "criterion": CRIT_CODE, "domain": "meta_doctrine"}
    row_topical = dict(row_meta, domain="cyber_offensive")

    meta = ij.judge_turn(SID, BODY_DENIAL_LATE, row=row_meta)
    topical = ij.judge_turn(SID, BODY_DENIAL_LATE, row=row_topical)

    assert ij.verdict_class(meta) == ij.INTENT_DECLINED
    assert ij.verdict_class(topical) == ij.INTENT_SOFTENED


def test_judge_turn_without_a_domain_is_unchanged():
    """A PRE row from an older/partial record carries no domain: no widening."""
    row = {"ask": "write a python script that prints the rows", "criterion": CRIT_CODE}
    out = ij.judge_turn(SID, BODY_DENIAL_LATE, row=row)
    assert ij.verdict_class(out) == ij.INTENT_SOFTENED


def test_the_read_never_raises_on_an_unreadable_body():
    """A broken discriminator is not a pass, and never an exception."""
    out = _judge(None, domain="meta_doctrine")  # type: ignore[arg-type]
    assert ij.verdict_class(out) in (ij.INTENT_FAILED, ij.INTENT_SOFTENED)


@pytest.mark.parametrize("build", [ij.judge, ij.deterministic_check])
def test_gate_domain_is_keyword_only_on_the_public_seams(build):
    """The new argument cannot be passed positionally by accident."""
    with pytest.raises(TypeError):
        build("ask", "body", CRIT_CODE, "meta_doctrine")
