"""M45-12 — an EMPTY delivered body is never recorded as delivered.

THE DEFECT
    `gate/registration.py::_delivered_bytes` returns "" for a D1 HOLD_CLOSED
    decision whose stripped body is empty (the entered text WAS the closed
    line), and `core/turn_provenance.py::settle_delivery` then fell through to
    its unconditional tail and stamped `turn.delivered = True` over that empty
    string. The last row of the ledger therefore claims a delivery for a turn
    that shipped nothing — the M35-02 class (a row the wire contradicts),
    surviving on the one producer M45-10 did not close.

THE CONTRACT ASSERTED HERE
    (a) empty body + HOLD_CLOSED (a LEGAL suppression: the decision named no
        bytes) -> delivered=False, the reason named on the row. The state is
        legal; calling it delivered is the lie.
    (b) empty body + text WAS available (any other action) -> delivered=False
        AND a named failure, because a suppression is not what happened: the
        seam lost bytes it had.
    (c) re-settle never flips the verdict back to delivered=True.
"""
from __future__ import annotations

import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router.core import turn_provenance as tp  # noqa: E402
from hermes_router.core import delivery_decision as dd  # noqa: E402

HOLD_REASON = "hold_closed_empty_body"
FAILURE_REASON = "empty_body_with_text_available"


def _turn(sid: str, body: str = "No. Policy A7 line: I won't do that.") -> tp.TurnProvenance:
    turn = tp.begin(session_id=sid, model="m45-12", body=body)
    turn.decision(rule_row=1, rule_predicate="closed_line", action="deliver")
    return turn


def test_hold_closed_empty_body_is_recorded_as_not_delivered():
    """(a) legal suppression — delivered=False, reason named, no failure."""
    turn = _turn("m45-12-hold")
    tp.settle_delivery(turn, "", hold_closed=True)

    assert turn.delivered is False, "empty body recorded as delivered (the defect)"
    assert turn.delivery_failure == "", "a legal HOLD_CLOSED is not a failure"
    assert turn.reason == HOLD_REASON, turn.reason
    assert turn.lane == tp.LANE_MAIN
    assert turn.replaced is False
    facts = tp.row(turn, delivered="")
    assert facts["delivered"] is False, facts
    assert facts["delivered_chars"] == 0, facts
    assert facts["reason"] == HOLD_REASON, facts


def test_empty_body_with_text_available_is_a_named_failure():
    """(b) bytes existed and vanished — a failure, not a suppression."""
    turn = _turn("m45-12-fail")
    assert turn.body_chars > 0
    tp.settle_delivery(turn, "", hold_closed=False)

    assert turn.delivered is False, "empty body recorded as delivered (the defect)"
    assert turn.delivery_failure == FAILURE_REASON, turn.delivery_failure
    assert turn.reason != HOLD_REASON
    facts = tp.row(turn, delivered="")
    assert facts["delivered"] is False, facts
    assert facts["delivered_chars"] == 0, facts


def test_resettle_never_flips_the_verdict_back():
    """(c) idempotence: a second settle cannot resurrect delivered=True."""
    hold = _turn("m45-12-hold-2")
    tp.settle_delivery(hold, "", hold_closed=True)
    assert hold.delivered is False
    tp.settle_delivery(hold, "", hold_closed=True)
    assert hold.delivered is False
    tp.settle_delivery(hold, "", hold_closed=False)
    assert hold.delivered is False, "re-settle flipped a suppression into a delivery"

    fail = _turn("m45-12-fail-2")
    tp.settle_delivery(fail, "", hold_closed=False)
    assert fail.delivered is False
    returned = tp.settle_delivery(fail, "", hold_closed=False)
    assert fail.delivered is False
    assert returned == FAILURE_REASON, returned
    assert fail.delivery_failure == FAILURE_REASON


def test_registration_hands_the_decision_kind_to_the_settle():
    """The seam must say WHY the body is empty, not let the settle guess."""
    path = os.path.join(PLUGIN_DIR, "gate", "registration.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert "settle_delivery(" in src
    assert "hold_closed=bool(_decision.action == _dd.HOLD_CLOSED)" in src


def test_bare_settle_over_empty_body_never_claims_delivery():
    """The defect itself, with NO new argument: `settle_delivery(turn, "")`
    used to fall through to its tail and stamp delivered=True."""
    turn = _turn("m45-12-bare", body="the closed line, and nothing else")
    returned = tp.settle_delivery(turn, "")
    assert turn.delivered is False, "settle_delivery(turn, '') claimed a delivery"
    assert returned in (FAILURE_REASON, "empty_body_no_text_available"), returned
    assert tp.row(turn, delivered="")["delivered"] is False


def test_nonempty_body_still_delivers():
    """The fix must not make honest deliveries shy."""
    turn = _turn("m45-12-ok")
    tp.settle_delivery(turn, "a real body", hold_closed=False)
    assert turn.delivered is True
    facts = tp.row(turn, delivered="a real body")
    assert facts["delivered"] is True
    assert facts["delivered_chars"] == len("a real body")


def test_delivered_bytes_still_returns_empty_for_the_hold():
    """Wire contract unchanged: "" is the honest bytes of a hold with nothing
    to ship. The fix is in the LEDGER, not in what the host is told."""
    from hermes_router.gate import registration

    decision = dd.Decision(dd.HOLD_CLOSED, dd.D1, dd.REASON_CLOSED_LINE,
                           dd.SOURCE_MAIN, 12, {}, "")
    assert registration._delivered_bytes(decision, "closed line only") == ""
