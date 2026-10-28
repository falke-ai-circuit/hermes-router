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


def test_not_a_denial_reaches_the_t2_sweep():
    """The sweep is gated on `not matches and not _class_authoritative`, so a
    not_a_denial result falls INTO it rather than returning early."""
    src = _source()
    gate_idx = src.index("if not matches and not _class_authoritative:")
    scan_idx = src.index("_r22sj.post_detection_scan(", gate_idx)
    sweep_idx = src.index("detection_t2", gate_idx)
    assert gate_idx < scan_idx < sweep_idx
    # The sweep must be inside that block (deeper indent than the `if`).
    gate_line = src[src.rindex("\n", 0, gate_idx) + 1: gate_idx]
    scan_line_start = src.rindex("\n", 0, scan_idx) + 1
    scan_indent = scan_idx - scan_line_start
    assert scan_indent > len(gate_line) - len(gate_line.lstrip()), \
        "t2 sweep is not nested inside the not-authoritative branch"


def test_a_body_the_class_misses_still_reaches_the_judge():
    """Functional proof: this body is not_a_denial for the pre-filter, and yet
    is_candidate/flagged_by_bounded_family are both True — i.e. the tier-1 gate
    the sweep runs still sees it. The pre-filter cannot veto it."""
    assert denial_class.classify(BODY_CLASS_MISSES_T1_CATCHES) == \
        denial_class.NOT_A_DENIAL
    assert structural.is_candidate(BODY_CLASS_MISSES_T1_CATCHES) is True
    assert structural.flagged_by_bounded_family(BODY_CLASS_MISSES_T1_CATCHES) is True


def test_action_denial_still_pre_routes():
    """The half of the ordering that IS allowed: action_denial may pre-route
    without spending a judge call."""
    src = _source()
    assert '_class_triggers = _denial_class == "action_denial"' in src
    m = re.search(r"if _class_triggers:\s*\n\s*matches = \[", src)
    assert m, "action_denial no longer seeds matches"
