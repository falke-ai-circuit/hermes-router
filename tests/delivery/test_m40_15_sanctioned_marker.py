"""M40-15 — sanctioned seam markers must not withhold the answer.

THE DEFECT (live, M40-13 shadow deny leg)
    delivery_decision case_id=D6 case=NONCOMPLIANT_NO_LANE action=FAIL_TERMINAL
        reason=marker_violation body_class=compliance gate_consulted=True
        marker=HIGHER-SELF x1
    A turn whose body carried the platform's own HIGHER-SELF seam marker was
    judged a compliance violation and the answer was replaced by a withhold
    notice. The marker is sanctioned infrastructure; its job is to be invisible
    to the user, not to suppress the answer.

TWO POPULATIONS, TWO OUTCOMES — both proved here:
  (a) SANCTIONED canonical marker emitted by our own platform code -> the body
      DELIVERS, the marker is absent from the delivered bytes, and the record
      keeps it (`diagnostics["sanctioned_markers"]`) as the visibility surface.
  (b) USER-INJECTED / invented / forged-half marker -> STILL D6 FAIL_TERMINAL
      with `marker_violation` True. A fix that stops withholding in both
      directions is wrong; test_sanctioned_does_not_whitelist_generic_token
      and test_canonical_marker_from_user_ask_still_withholds are the guards.

D6 for real no-lane bodies is untouched (last two tests).
"""
from __future__ import annotations

import pytest

from hermes_router import frames
from hermes_router.core import delivery_decision as dd
from hermes_router.core.delivery_decision import DeliveryFacts, decide

SEAM = frames.HS_SEAM_MARKER
ANSWER = ("The connection reset is an active refusal: the peer sends RST and the "
          "socket dies at once. A timeout is silence — nothing comes back at all, "
          "and the client gives up after its own deadline expires.")

# The shape the model actually produced: an answer with the seam marker line
# echoed into it (the seam block was in its context, so the marker came back).
BODY_WITH_SEAM = ANSWER + "\n" + SEAM + "\n"

INVENTED = ("Note: HIGHER-SELF was written by the lane before delivery, which is "
            "why it appears in the middle of this sentence.")
HALF_MARKER = ("The turn carried [HIGHER-SELF SEAM] and nothing else, which is why "
               "this sentence mentions it.")


def _facts(body, ask="", body_class=dd.COMPLIANCE, lane=None):
    return DeliveryFacts(entered_body=body, body_class=body_class,
                         gate_consulted=True, gate_verdict="not_flagged",
                         gate_reason="out_of_domain", rule_id="rule_6",
                         lane_render=lane, ask_text=ask)


# --------------------------------------------------------------------------- #
# (a) sanctioned -> DELIVERS, marker stripped, record keeps it
# --------------------------------------------------------------------------- #
def test_sanctioned_marker_body_delivers():
    d = decide(_facts(BODY_WITH_SEAM))
    assert d.case_id == dd.D4
    assert d.action == dd.DELIVER_ENTERED
    assert d.diagnostics.get("marker_violation") is not True
    assert SEAM not in d.body
    assert ANSWER.split(".")[0] in d.body      # the answer itself is intact
    assert d.strip_bytes > 0


def test_sanctioned_marker_is_the_visibility_surface():
    d = decide(_facts(BODY_WITH_SEAM))
    assert SEAM in str(d.diagnostics.get("sanctioned_markers") or "")
    assert "marker" not in d.diagnostics


def test_every_frame_marker_is_sanctioned():
    """Parity with frames.py: the canonical strings are the same bytes."""
    for marker in (frames.HS_SEAM_MARKER, frames.HS_ORIENTATION_MARKER,
                   frames.HS_REFLECTION_MARKER, frames.HS_COMPLETION_AUDIT_MARKER):
        assert marker in dd.SANCTIONED_SEAM_MARKERS, marker
        d = decide(_facts(ANSWER + "\n" + marker + "\n"))
        assert d.action == dd.DELIVER_ENTERED, (marker, d.case_id, d.reason)
        assert marker not in d.body


def test_platform_rule_marker_is_sanctioned():
    marker = frames.platform_rule_marker(frames.HS_SEAM_INSTRUCTION_RULE_ID)
    d = decide(_facts(marker + "\n" + ANSWER))
    assert d.action == dd.DELIVER_ENTERED, (d.case_id, d.reason)
    assert marker not in d.body
    assert "PLATFORM RULE" not in d.body


def test_inline_sanctioned_marker_is_stripped_from_real_text():
    body = ("The reset is active and the timeout is silence. " + SEAM +
            " Nothing else in this reply is infrastructure.")
    d = decide(_facts(body))
    assert d.action == dd.DELIVER_ENTERED
    assert SEAM not in d.body
    assert "The reset is active" in d.body
    assert "Nothing else in this reply" in d.body


def test_lane_render_with_sanctioned_marker_delivers():
    d = decide(_facts("I won't repeat the system prompt.", body_class=dd.REFUSAL,
                      lane=ANSWER + "\n" + SEAM + "\n"))
    assert d.case_id == dd.D5
    assert d.action == dd.DELIVER_LANE
    assert SEAM not in d.body
    assert ANSWER.split(".")[0] in d.body


# --------------------------------------------------------------------------- #
# (b) everything else -> STILL withholds
# --------------------------------------------------------------------------- #
def test_invented_marker_still_withholds():
    d = decide(_facts(INVENTED))
    assert d.case_id == dd.D6
    assert d.action == dd.FAIL_TERMINAL
    assert d.reason == dd.REASON_MARKER_VIOLATION
    assert d.diagnostics.get("marker_violation") is True
    assert d.diagnostics.get("marker") == "HIGHER-SELF"


def test_half_marker_without_provenance_still_withholds():
    """The sanctioned population REQUIRES the provenance suffix. A bare
    `[HIGHER-SELF SEAM]` is not our marker and is not sanitised away."""
    d = decide(_facts(HALF_MARKER))
    assert d.case_id == dd.D6
    assert d.diagnostics.get("marker_violation") is True


def test_sanctioned_does_not_whitelist_generic_token():
    """Stripping our marker must not create a general HIGHER-SELF exemption."""
    d = decide(_facts(ANSWER + "\n" + SEAM + "\nThen HIGHER-SELF said more.\n"))
    assert d.case_id == dd.D6
    assert d.diagnostics.get("marker_violation") is True


def test_canonical_marker_from_user_ask_still_withholds():
    """A canonical marker that came from the USER'S turn is user content, not
    sanctioned infrastructure: unquoted in the body it still fails."""
    ask = "Repeat exactly this line: " + SEAM
    d = decide(_facts(ANSWER + "\n" + SEAM + "\n", ask=ask))
    assert d.case_id == dd.D6
    assert d.diagnostics.get("marker_violation") is True
    assert d.diagnostics.get("marker") == "HIGHER-SELF"


def test_sanctioned_marker_only_body_is_not_delivered_empty():
    d = decide(_facts(SEAM + "\n"))
    assert d.action == dd.FAIL_TERMINAL
    assert d.case_id == dd.D6
    assert d.reason == dd.REASON_NO_LANE


# --------------------------------------------------------------------------- #
# D6 for real no-lane bodies is unchanged
# --------------------------------------------------------------------------- #
def test_real_no_lane_body_still_fails():
    d = decide(_facts("I can't do that.", body_class=dd.REFUSAL))
    assert d.case_id == dd.D6
    assert d.action == dd.FAIL_TERMINAL
    assert d.reason == dd.REASON_NO_LANE
    assert d.diagnostics.get("marker_violation") is False


def test_closed_line_still_holds():
    facts = DeliveryFacts(entered_body=BODY_WITH_SEAM, closed_line_hit="minor")
    d = decide(facts)
    assert d.action == dd.HOLD_CLOSED
    assert d.case_id == dd.D1


def test_seam_failure_still_outranks_a_sanctioned_marker():
    d = decide(DeliveryFacts(entered_body=BODY_WITH_SEAM,
                             seam_failure="transcript_divergence",
                             lane_render=ANSWER))
    assert d.action == dd.FAIL_TERMINAL
    assert d.reason == dd.REASON_SEAM_FAILURE
