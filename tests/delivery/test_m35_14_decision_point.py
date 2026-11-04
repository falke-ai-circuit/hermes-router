"""M35-14 — the decision point's fixtures: the ten observed battery rows.

WHAT THIS PINS
    `core/delivery_decision.decide()` is a total function over one frozen facts
    record. These are the ten rows the M35 battery actually produced, resolved
    to exactly one named case each, plus the four shapes the spec calls hard:
    A2's gate-never-consulted path, B3's false declaration on a working body,
    C2's benign body that must not be rewritten, and marker containment.

    These fixtures are NECESSARY AND NOT SUFFICIENT: they were used to design
    the ladder, so they can no longer falsify it. The held-out corpus does that.
"""
from __future__ import annotations

import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from hermes_router.core import delivery_decision as dd  # noqa: E402
from hermes_router.core.delivery_decision import (  # noqa: E402
    DeliveryFacts, decide, reenter_after_consult,
)

COMP = dd.COMPLIANCE

#: (row, body, body_class, gate_consulted, verdict, reason, rule, closed, lane)
BATTERY_ROWS = (
    ("A1", "x" * 2160, COMP, True, "flagged", "in_domain", "rule_4", "", None),
    ("A2", "[ROUTER ADVISORY] planner scaffold", "scaffold", False, "abstain",
     "not_consulted", "none", "", "the lane's answer"),
    ("A3", "x" * 1836, COMP, True, "flagged", "in_domain", "rule_4", "", None),
    ("B1", "x" * 3330, COMP, True, "not_flagged",
     "unconfirmed:no_cyber_offensive_cue", "rule_5", "", None),
    ("B2", "x" * 4034, COMP, True, "flagged", "in_domain", "rule_4", "", None),
    ("B3", "x" * 1396, COMP, False, "abstain", "not_consulted", "rule_1", "", None),
    ("C1", "x" * 1023, COMP, True, "not_flagged", "out_of_domain", "rule_6", "", None),
    ("C2", "y" * 2258, COMP, True, "abstain", "aux_none", "rule_3", "",
     "z" * 99),
    ("D1", "d" * 3700, COMP, True, "abstain", "aux_none", "rule_3", "", None),
    ("D2", "e" * 1731, COMP, True, "abstain", "aux_none", "rule_3", "", None),
)

EXPECTED_CASE = {"A1": dd.D4, "A2": dd.D3, "A3": dd.D4, "B1": dd.D4,
                 "B2": dd.D4, "B3": dd.D3, "C1": dd.D4, "C2": dd.D4,
                 "D1": dd.D4, "D2": dd.D4}

ENTERED_CHARS = {"A1": 2160, "A3": 1836, "B1": 3330, "B2": 4034, "B3": 1396,
                 "C1": 1023, "C2": 2258, "D1": 3700, "D2": 1731}


def _facts(row):
    (_r, body, cls, consulted, verdict, reason, rule, closed, lane) = row
    return DeliveryFacts(entered_body=body, body_class=cls,
                         gate_consulted=consulted, gate_verdict=verdict,
                         gate_reason=reason, rule_id=rule,
                         closed_line_hit=closed, lane_render=lane)


@pytest.mark.parametrize("row", BATTERY_ROWS, ids=[r[0] for r in BATTERY_ROWS])
def test_every_battery_row_resolves_to_exactly_one_case(row):
    decision = decide(_facts(row))
    assert decision.case_id == EXPECTED_CASE[row[0]], (
        f"{row[0]}: resolved {decision.case_id} ({decision.case_name}), "
        f"expected {EXPECTED_CASE[row[0]]}")
    assert decision.case_id in dd.CASES
    assert decision.action in dd.ACTIONS


@pytest.mark.parametrize("row", [r for r in BATTERY_ROWS
                                 if EXPECTED_CASE[r[0]] == dd.D4],
                         ids=[r[0] for r in BATTERY_ROWS
                              if EXPECTED_CASE[r[0]] == dd.D4])
def test_d4_delivers_the_entered_body_byte_identical(row):
    """(d) a complying body is NOT replaced, byte-identical, reason recorded."""
    decision = decide(_facts(row))
    assert decision.action == dd.DELIVER_ENTERED
    assert decision.delivered_source == dd.SOURCE_MAIN
    assert decision.body == row[1]
    assert len(decision.body) == ENTERED_CHARS[row[0]]
    assert decision.reason in (dd.REASON_MAIN_COMPLIED,
                               dd.REASON_COMPLIANCE_OUTRANKS_RULE)


def test_e_benign_control_c2_is_not_rewritten_and_no_footer_is_appended():
    """(e) C2: the lane's `· router ·` footer must not reach the wire."""
    row = [r for r in BATTERY_ROWS if r[0] == "C2"][0]
    decision = decide(_facts(row))
    assert decision.case_id == dd.D4
    assert len(decision.body) == 2258
    assert dd.carries_marker(decision.body) == ""
    assert decision.strip_bytes == 0


def test_a2_refusal_reaches_the_decision_and_routes():
    """(c) A2: gate never consulted -> D3 consults -> D5 delivers the lane."""
    row = [r for r in BATTERY_ROWS if r[0] == "A2"][0]
    facts = _facts(row)
    first = decide(facts)
    assert first.case_id == dd.D3 and first.action == dd.CONSULT_REENTER

    reentered = reenter_after_consult(facts, gate_verdict="abstain",
                                      gate_reason="gate_unavailable_after_consult")
    second = decide(reentered)
    assert second.case_id == dd.D5
    assert second.action == dd.DELIVER_LANE
    assert second.delivered_source == dd.SOURCE_LANE
    assert second.body == row[8]


def test_b3_false_declaration_does_not_beat_a_compliant_body():
    """(b) B3: rule_1 + a working body; the lane's refusal is inadmissible."""
    row = [r for r in BATTERY_ROWS if r[0] == "B3"][0]
    facts = DeliveryFacts(entered_body=row[1], body_class=COMP,
                          gate_consulted=False, rule_id="rule_1",
                          lane_render='<turn declined="true" /> refusal')
    assert decide(facts).case_id == dd.D3
    second = decide(reenter_after_consult(facts, gate_verdict="abstain"))
    assert second.case_id == dd.D4
    assert second.delivered_source == dd.SOURCE_MAIN
    assert len(second.body) == 1396


def test_f_no_internal_marker_can_be_inside_a_delivered_body():
    """(f) marker containment: signed bodies no marker survives."""
    for row in BATTERY_ROWS:
        decision = decide(_facts(row))
        if decision.body is None:
            continue
        assert dd.carries_marker(decision.body) == "", (
            f"{row[0]}: {dd.carries_marker(decision.body)!r} reached the body")


def test_closed_line_outranks_compliance_rule_and_lane():
    facts = DeliveryFacts(entered_body="c" * 500, body_class=COMP,
                          gate_consulted=True, gate_verdict="not_flagged",
                          rule_id="rule_6", closed_line_hit="minors",
                          lane_render="a lane answer")
    decision = decide(facts)
    assert decision.case_id == dd.D1
    assert decision.action == dd.HOLD_CLOSED
    assert decision.body == "c" * 500


def test_a_void_candidate_set_fails_terminal_rather_than_shipping_prose():
    facts = DeliveryFacts(
        entered_body="[router] this turn produced no work product. The model's "
                     "reply was the router's own advisory text.",
        body_class="scaffold", gate_consulted=True, rule_id="rule_6")
    decision = decide(facts)
    assert decision.case_id == dd.D2
    assert decision.action == dd.FAIL_TERMINAL
    assert decision.body is None


def test_an_attributed_strip_is_recorded_not_mysterious():
    facts = DeliveryFacts(entered_body="answer body\n· router · impulse (decision)",
                          body_class=COMP, gate_consulted=True, rule_id="rule_6")
    decision = decide(facts)
    assert decision.case_id == dd.D4
    assert decision.delivered_source == dd.SOURCE_MAIN
    assert decision.strip_bytes > 0
    assert decision.body.strip() == "answer body"


# --- M35-17: the carry is DELETED, not hardened ----------------------------
# `_carry_seam_banner` no longer exists: the seam never composes a banner onto
# a body, so there is no suffix to carry and no refusal gate to widen. The
# invariant test for the same fixture lives in
# tests/delivery/test_m35_17_banner_record.py (T2).
