"""F2 closure-prose-collision regression suite (v3.9.2).

Frontier-anchored finding: the closure detector matched bare completion
verbs in ordinary prose and fiction ("She wrapped her legs around him",
"They wrapped up in each other", "The plane landed and the crew fixed
the gear", "closed out the bar tab") — an FP class, not a single token.
Fix: completion phrases now require a task/bookkeeping noun anchor
within the match window. These tests pin BOTH directions: prose must
never match; real closures must never be lost.
"""
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router.completion_audit import is_closure_response, _CLOSURE_PATTERNS  # noqa: E402

# Ordinary prose / fiction — none of these is a task closure. All were
# live-match risks under the pre-hardening patterns.
PROSE_NEVER_CLOSURE = [
    "She wrapped her legs around him and pulled him deep.",
    "They wrapped up in each other as the storm passed.",
    "He wrapped up the wound with a strip of cloth.",
    "Her hair was wrapped in silk.",
    "The plane landed and the crew fixed the gear.",
    "He fixed his gaze on the door.",
    "She closed out the bar tab and left.",
    "The cargo shipped out of the port at dawn.",
    "The package was wrapped and delivered.",
    "The ship landed its cargo and the harbor crew was done for the day.",
]

# Genuine task closures — the hardening must not lose these.
REAL_CLOSURES = [
    ("Close this out with a final recommendation and wrap-up.", "Done. Recommendation follows."),
    ("", "All three tasks are done and shipped."),
    ("", "The audit and the review are complete and verified."),
    ("", "Wrapped up the session. Everything is shipped and landed."),
    ("", "Closed out the investigation — all items verified."),
    ("", "Work is done, changes are shipped, and every commit verified."),
    ("", "In summary, the caching layer is complete and verified."),
    ("", "Everything is shipped and landed."),
    ("", "Bottom line: ship it."),
    ("", "All done — closing out the incident-response runbook review."),
]


@pytest.mark.parametrize("text", PROSE_NEVER_CLOSURE)
def test_prose_is_never_closure(text):
    assert not is_closure_response("", text), "prose FP'd as closure: %r" % text


@pytest.mark.parametrize("ask,resp", REAL_CLOSURES)
def test_real_closures_still_detected(ask, resp):
    assert is_closure_response(ask, resp), "lost closure: %r" % (ask or resp)


def test_closure_pattern_count_documented():
    """Guard: patterns tuple is pinned — an edit here must consciously
    update this suite (the wrapped-verb FP shipped unnoticed)."""
    assert len(_CLOSURE_PATTERNS) == 10
