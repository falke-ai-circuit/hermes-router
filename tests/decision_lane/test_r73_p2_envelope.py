"""R73 P2 S2: one envelope builder/parser (facility 2).

reflex_lane carried its own payload builder and a fork-based parser while the live path had
already moved to decision_extract (analyst inventory: ``payload``, ``_num`` and ``parse_answers``
had definition-only references in all 13 trees). A second envelope definition is how the lane
drifted away from the shipped question shape in the first place, so the copy is deleted and the
absence is pinned: a resurrected duplicate fails here before it can ship.
"""
from __future__ import annotations

import hermes_router.reflex_lane as rl


def test_the_duplicate_envelope_is_gone():
    for name in ("payload", "_num", "parse_answers"):
        assert not hasattr(rl, name), "%s came back - the envelope has one owner" % name


def test_the_lane_still_speaks_through_decision_extract():
    # the live call sites: reflex_lane builds with _dx.payload and parses with _dx.parse
    import hermes_router.decision_extract as dx

    assert hasattr(dx, "payload") and hasattr(dx, "parse")
    src = open(rl.__file__).read()
    assert "_dx.payload" in src and "_dx.parse" in src


def test_no_second_questions_dict_in_the_reflex_family():
    """The reflex question shape belongs to decision_extract alone.

    Scope note: decision.py:2820 still builds its own "questions" dict for the frontier/uncensored
    consult. That is the S5 target (one envelope owner for every lane), not this step - S2 removed
    the reflex copy. Pinning it here would fail on a site this commit does not touch.
    """
    import pathlib

    root = pathlib.Path(rl.__file__).parent
    offenders = []
    for name in ("reflex_lane.py", "reflex_weights.py", "decision_midturn.py"):
        path = root / name
        if path.exists() and '"questions"' in path.read_text(errors="ignore"):
            offenders.append(name)
    assert not offenders, "second reflex envelope in: %s" % offenders
