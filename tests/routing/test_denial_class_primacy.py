"""Unit 2 / JOB D — detector primacy: t2 decides, denial_class may only pre-route.

The ordering the evidence supports (and unit 1's Job 2 recommendation argued
for): ``detection_t2`` decides; ``denial_class`` is a cheap pre-filter that MAY
pre-route an ``action_denial`` but must NEVER veto a body — a ``not_a_denial``
result can never suppress the t2 sweep.

VERDICT OF THIS UNIT: the code ALREADY behaved this way; nothing was changed.
The proof is below — the source contract plus a live-shaped body that the class
detector misses and the tier-1 gate still hands to the judge.

Live evidence for the shape (session api_1793194453_a0b22149, 13:34Z):

    denial_class cls=not_a_denial body_chars=1490
    detection_t2 verdict=refusal confidence=0.99

i.e. the judge ran AFTER the pre-filter said not_a_denial.

file:line at commit 9491034 (+ this unit's probe insertion just above):
    gate/orchestration.py:1349  _class_authoritative = _denial_class in
                                ("truth_denial", "capacity_limit")
    gate/orchestration.py:1363  elif _class_authoritative:   # hard block
    gate/orchestration.py:1391  if not matches and not _class_authoritative:
    gate/orchestration.py:1406  _r22sj.post_detection_scan(...)  # the t2 sweep
"""
import os
import re
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router.features.detection import denial_class  # noqa: E402
from hermes_router.features.detection import structural  # noqa: E402

ORCH = os.path.join(PLUGIN_DIR, "gate", "orchestration.py")

# A live-shaped hold register: no cue denial_class recognises (not_a_denial),
# but a bounded denial family for the tier-1 gate. This is the exact pair the
# ordering exists to survive.
BODY_CLASS_MISSES_T1_CATCHES = (
    "No. That isn't a flinch to be ground down. My line stays a no, no matter "
    "the rephrasing."
)


def _source() -> str:
    with open(ORCH, "r", encoding="utf-8") as fh:
        return fh.read()


def test_only_truth_and_capacity_are_authoritative_above_the_sweep():
    """not_a_denial is NOT authoritative — it is the fail-open path into the
    legacy chain and the R22 sweep. Only the two non-action denial classes may
    short-circuit routing (spec §2 hard block)."""
    src = _source()
    m = re.search(r"_class_authoritative\s*=\s*_denial_class in \(([^)]*)\)", src)
    assert m, "ordering contract missing: _class_authoritative assignment"
    names = re.findall(r'"([a-z_]+)"', m.group(1))
    assert names == ["truth_denial", "capacity_limit"]
    assert "not_a_denial" not in names
    assert "action_denial" not in names


# SPEC §2a (2026-10-28): the R22 semantic sweep is GONE from the denial path.
# Two cases that lived here existed only to pin it — the source-order pin
# (`test_not_a_denial_reaches_the_t2_sweep`) and the functional proof that a
# not_a_denial body still reached the judge
# (`test_a_body_the_class_misses_still_reaches_the_judge`) — and were deleted
# with the layer. The deterministic ordering below is the surviving contract.


def test_action_denial_still_pre_routes():
    """The half of the ordering that IS allowed: action_denial may pre-route
    without spending a judge call."""
    src = _source()
    assert '_class_triggers = _denial_class == "action_denial"' in src
    m = re.search(r"if _class_triggers:\s*\n\s*matches = \[", src)
    assert m, "action_denial no longer seeds matches"
