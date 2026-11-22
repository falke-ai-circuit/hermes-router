"""Seam failure must never name PRIOR text as this turn's answer.

WHAT THIS PINS (2026-11-14, Goran's live symptom)
    On `transcript_divergence` the seam records an obligation whose body is the
    PREVIOUS transcript text. Before this pin, `decide()` returned that body on
    the FAIL_TERMINAL/D6 branch, so the delivery path shipped an older answer in
    place of the current one: 3,579 chars of a prior reply replaced a 4,768-char
    current reply in the conductor DM. The user's report was exactly that — the
    agent works the current topic but the final response addresses the previous
    thematic.

    The audit must survive. The divergence still decides FAIL_TERMINAL/D6 with
    REASON_SEAM_FAILURE and still logs at error severity; what it may not do is
    name recovered text as deliverable bytes. With `body is None` the wire falls
    into `_delivered_bytes`'s D6 pass-through branch and the host keeps the
    model's OWN text, so the user gets the answer to what they asked.

    Marker violations keep their notice: they have no answer to keep.
"""
from __future__ import annotations

import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router.core import delivery_decision as dd  # noqa: E402
from hermes_router.gate import registration as reg  # noqa: E402

CURRENT = "The current answer: here is what you actually asked about. " * 20
PRIOR = "An older thematic, from a previous turn. " * 20


def _diverged(**kw) -> dd.DeliveryFacts:
    facts = dict(
        entered_body=CURRENT,
        lane_render=PRIOR,
        seam_failure="transcript_divergence",
        body_class=dd.COMPLIANCE,
        gate_consulted=True,
        gate_verdict="abstain",
        attempt=0,
    )
    facts.update(kw)
    return dd.DeliveryFacts(**facts)


def test_divergence_still_decides_terminal_d6():
    """The audit stands: a divergence is a failed turn, at error severity."""
    d = dd.decide(_diverged())
    assert d.action == dd.FAIL_TERMINAL
    assert d.case_id == dd.D6
    assert d.reason == dd.REASON_SEAM_FAILURE
    assert d.delivered_source == dd.SOURCE_SEAM


def test_divergence_names_no_deliverable_bytes():
    """Recovered prior text is evidence, never the answer."""
    d = dd.decide(_diverged())
    assert d.body is None
    # the audit detail is kept, the content is not shipped
    assert d.diagnostics.get("seam_failure") == "transcript_divergence"
    assert d.diagnostics.get("seam_failure_recovered_chars", 0) > 0


def test_wire_passes_through_so_the_current_answer_ships():
    """None = the host keeps the model's text: the user sees THIS turn's answer."""
    d = dd.decide(_diverged())
    assert reg._delivered_bytes(d, CURRENT) is None


def test_marker_violation_ships_the_scrubbed_answer_never_a_notice():
    """M50-19 (owner ruling): the withhold notice is gone from this branch.

    A marker violation no longer means "there is no answer to keep" — the
    router cannot decide the user's answer was undelivered because the router
    found routing metadata in it. The marker LINES go, the answer ships.
    """
    d = dd.Decision(dd.FAIL_TERMINAL, dd.D6, dd.REASON_MARKER_VIOLATION, dd.SOURCE_SEAM)
    out = reg._delivered_bytes(d, CURRENT)
    assert out != reg.WITHHELD_BODY
    assert CURRENT.strip()[:20] in out


def test_marker_only_body_passes_the_host_its_own_bytes():
    """Nothing shippable survives the strip -> None (pass-through), not a notice."""
    d = dd.Decision(dd.FAIL_TERMINAL, dd.D6, dd.REASON_MARKER_VIOLATION, dd.SOURCE_SEAM)
    assert reg._delivered_bytes(d, "· router · decision | row=1 ·") is None


def test_divergence_outranks_a_compliant_body():
    """A body that would otherwise win D4 may not outrank the declared failure."""
    d = dd.decide(_diverged( rule_id="none"))
    assert d.action == dd.FAIL_TERMINAL
    assert d.case_id == dd.D6
