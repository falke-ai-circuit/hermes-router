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


HEAD_BLOCK = (
    "explorer: 0.35 | which reading the raw interval trace actually supports --\n"
    "irregular impossible outliers versus a uniform spread at the tick size.\n"
    "verifier: 0.55 | that the pick follows from the error model.\n"
)


def test_projection_head_is_detected_as_its_own_family():
    assert "pov_projection_head" in eg.detect_envelope_echo_families(HEAD_BLOCK)


def test_the_head_block_is_cut_and_a_real_answer_below_survives():
    body = HEAD_BLOCK + "The photogate: a coarse timer cannot produce outliers."
    stripped, removed = eg.strip_advisory_echo(body)
    assert removed > 0
    assert "explorer: 0.35" not in stripped
    assert stripped.startswith("The photogate")


def test_a_body_that_is_only_the_head_block_is_withheld():
    res = eg.enforce_execution_echo(HEAD_BLOCK, session_id="t", frame="impulse")
    assert res is not None
    assert res.action == "withhold_echo"
    assert "explorer:" not in str(res.body)


def test_one_named_voice_in_a_real_answer_is_not_a_head_block():
    assert not eg.is_projection_head(
        "The verifier: 0.55 reading is wrong here -- the photogate is the cause.")


LIVE_NOTE = (
    "explorer: 0.6 | whether the raw trace or the run-to-run spread is the "
    "cheapest discriminator\n"
    "verifier: 0.8 | that the answer picks one and states an error-magnitude "
    "reason\noperator: 0.9 | commit to a single sentence and stop\n\n"
    "gates: precedent=0 consequence=0\n\n"
    "1. Next steps, in order: first decide the selection criterion explicitly, "
    "then write the sentence, then stop."
)


def test_a_turn_that_IS_the_projection_is_withheld_not_delivered():
    """The live foreign-install defect: the delivered body was the router note."""
    for frame in ("impulse", "execution", ""):
        res = eg.enforce_execution_echo(LIVE_NOTE, session_id="t", frame=frame)
        assert res is not None, frame
        assert res.action == "withhold_echo", frame
        assert "explorer" not in str(res.body), frame
        assert "Next steps, in order" not in str(res.body), frame


def test_projection_head_with_a_real_answer_below_is_kept_and_stripped():
    body = (HEAD_BLOCK + "gates: precedent=0 consequence=0\n\n"
            + "The photogate: a coarse timer cannot produce outliers like these.")
    res = eg.enforce_execution_echo(body, session_id="t", frame="impulse")
    assert res is not None and res.action == "strip_keep_work"
    assert "explorer: 0.35" not in str(res.body)
    assert "The photogate" in str(res.body)


def test_the_no_answer_notice_claims_nothing_about_work():
    res = eg.enforce_execution_echo(LIVE_NOTE, session_id="t", frame="impulse")
    body = str(res.body)
    assert "no answer was delivered" in body
    assert "work report" not in body
