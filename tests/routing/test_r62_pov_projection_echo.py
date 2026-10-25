"""R62: the echo guard must see the voice set the code actually emits.

Found on an isolated install: a turn with no tool activity delivered a body that
opened with the mindset projection itself -- "explorer: 0.6 | ... verifier: 0.70 |
..." -- and no family matched, because the POV family still keyed on the retired
practitioner/outsider/skeptic vocabulary. The model had stripped the bracketed
header while restating the note, which is precisely the case a header-based
family cannot catch.
"""
from hermes_router import echo_guard as eg


def test_projection_head_is_a_family():
    body = ("explorer: 0.6 | the one distinguishing observable between the two "
            "readings. verifier: 0.70 | that the pick is a single choice.")
    assert "pov_projection" in eg.detect_envelope_echo_families(body)


def test_two_scored_voices_are_enough_but_one_is_not():
    assert "pov_projection" in eg.detect_envelope_echo_families(
        "skeptic: 0.2 | maybe. operator: 0.9 | do it.")
    assert "pov_projection" not in eg.detect_envelope_echo_families(
        "skeptic: 0.2 | maybe, but the photogate is the likelier cause.")


def test_a_real_answer_is_never_a_family():
    for body in (
        "The photogate, because the failure is intermittent and a coarse timer "
        "would bias every run by the same offset.",
        "I read experiment_notes.txt: two causes are listed, and I would test "
        "the photogate first.",
    ):
        assert eg.detect_envelope_echo_families(body) == []


def test_legacy_triplet_still_matches():
    assert "pov_triplet" in eg.detect_envelope_echo_families(
        "practitioner: x | outsider: y | skeptic: z")


def test_a_body_that_is_only_the_projection_is_withheld_not_shipped():
    """The delivery boundary: the router's own note is never the user's answer."""
    body = ("explorer: 0.3 | the data signature that separates them "
            "verifier: 0.8 | that timer resolution gets checked")
    res = eg.enforce_execution_echo(body, session_id="t", frame="answer")
    assert res is not None
    assert res.action == "withhold_echo"
    assert "explorer: 0.3" not in str(res.body)


def test_a_clean_answer_is_left_alone():
    assert eg.enforce_execution_echo(
        "The photogate, because an intermittent failure is not a resolution "
        "problem.", session_id="t", frame="answer") is None
