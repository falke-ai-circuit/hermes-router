"""Unit 2 / JOB A — the apostrophe-fold CLASS fix, pinned across detectors.

Unit 1 (commit 9491034) folded U+2019 and three friends into
``denial_class._normalise``. The SAME hole existed in
``features/detection/structural.py``, which had NO fold of any kind. Measured on
one sentence, ASCII vs U+2019, before this fix:

    has_refusal_semantics("I can't write that. It would be a phishing lure and
                          I won't provide one.")
        -> True   with ASCII,  False with U+2019
    is_candidate(...)
        -> True   with ASCII,  False with U+2019

structural.py is the tier-1 gate that FEEDS the semantic judge — the net that
actually routed the live turn after denial_class missed it — so it was
defeatable by the exact character the model emitted.

This file pins the CLASS, not one regex: for a small set of refusal bodies it
asserts that the ASCII and curly variants classify IDENTICALLY for BOTH
structural and denial_class. The curly variants are derived from the source
sentence with ``.replace("'", "\\u2019")`` so the fixture cannot drift from the
text a model actually emits.

It also pins the single definition: structural and denial_class must import the
SAME function object (moved, not copied), so the two can never drift apart again.
"""
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router.features.detection import denial_class  # noqa: E402
from hermes_router.features.detection import normalise as nz  # noqa: E402
from hermes_router.features.detection import structural  # noqa: E402

# Refusal bodies with ASCII contractions, spanning the registers the legacy
# detectors claim to cover: first-person modal, produce-verb decline,
# settled-line hold, bare hold.
REFUSAL_BODIES = (
    "I can't write that. It would be a phishing lure and I won't provide one.",
    "I can't help with that request. I won't produce a working exploit "
    "for a live target.",
    "No. That isn't a flinch to be ground down — I've held this line and I "
    "won't manufacture it.",
    "I won't do that.",
)

CURLY_FORMS = {
    "U+2019": "\u2019",
    "U+2018": "\u2018",
    "U+02BC": "\u02bc",
    "U+FF07": "\uff07",
}


@pytest.mark.parametrize("body", REFUSAL_BODIES)
def test_structural_classifies_ascii_and_curly_identically(body):
    """The whole point of the fix: the fold, not the cue table, decides.

    A body that is a refusal with ASCII apostrophes must still be a refusal —
    same candidate, same semantics, same bounded-family signal — when the model
    spells it with U+2019."""
    curly = body.replace("'", "\u2019")
    assert curly != body, "fixture drift: body carries no ASCII apostrophe"

    assert structural.is_candidate(curly) == structural.is_candidate(body)
    assert structural.has_refusal_semantics(curly) == \
        structural.has_refusal_semantics(body)
    assert structural.flagged_by_bounded_family(curly) == \
        structural.flagged_by_bounded_family(body)
    # And the shared answer must be the positive one — an equality between two
    # FALSEs would pass while the miss is still live.
    assert structural.is_candidate(body) is True
    assert structural.has_refusal_semantics(body) is True


@pytest.mark.parametrize("body", REFUSAL_BODIES)
def test_denial_class_classifies_ascii_and_curly_identically(body):
    assert denial_class.classify(body.replace("'", "\u2019")) == \
        denial_class.classify(body)
    assert denial_class.classify(body) == denial_class.ACTION_DENIAL


@pytest.mark.parametrize("name", sorted(CURLY_FORMS))
def test_every_folded_codepoint_restores_the_ascii_classification(name):
    """Each codepoint in the fold table must restore the ASCII verdict on both
    detectors — not just U+2019, which is the one the live model happened to
    emit."""
    body = REFUSAL_BODIES[0]
    variant = body.replace("'", CURLY_FORMS[name])
    assert variant != body
    assert structural.is_candidate(variant) is True
    assert structural.has_refusal_semantics(variant) is True
    assert denial_class.classify(variant) == denial_class.ACTION_DENIAL


@pytest.mark.parametrize("body", REFUSAL_BODIES)
def test_fold_is_one_to_one_so_offsets_are_unaffected(body):
    folded = nz.fold_apostrophes(body)
    assert len(folded) == len(body)
    assert folded == body  # ASCII input is unchanged by the fold


def test_normalise_preserves_length_on_single_apostrophes():
    curly = REFUSAL_BODIES[0].replace("'", "\u2019")
    assert len(nz.normalise(curly)) == len(curly)


def test_normaliser_is_one_definition_imported_not_copied():
    """Moved, not duplicated: same function object in both detectors."""
    assert structural.normalise is nz.normalise
    assert denial_class._normalise is nz.normalise


def test_structural_has_no_private_fold_of_its_own():
    """Guards the regression that created this defect: a second, partial fold."""
    assert not hasattr(structural, "_APOSTROPHE_FOLD")
    assert not hasattr(structural, "_normalise")
