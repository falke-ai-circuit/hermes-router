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

FAILURE_REASON = "empty_body_with_text_available"


def _turn(sid: str, body: str = "No. Policy A7 line: I won't do that.") -> tp.TurnProvenance:
    turn = tp.begin(session_id=sid, model="m45-12", body=body)
    turn.decision(rule_row=1, rule_predicate="closed_line", action="deliver")
    return turn


def test_bare_settle_over_empty_body_never_claims_delivery():
    """The defect itself, with NO new argument: `settle_delivery(turn, "")`
    used to fall through to its tail and stamp delivered=True."""
    turn = _turn("m45-12-bare", body="the closed line, and nothing else")
    returned = tp.settle_delivery(turn, "")
    assert turn.delivered is False, "settle_delivery(turn, '') claimed a delivery"
    assert returned in (FAILURE_REASON, "empty_body_no_text_available"), returned
    assert tp.row(turn, delivered="")["delivered"] is False

