"""Regression tests (2026-09-09): system-injected-only turns must NOT fire
PRE orientation (live leak: conductor session 20260909 — orientation brief
delivered as the reply because flash answered the seam on a payload-only
turn). Manual anchor and POST audit unaffected."""
import pytest

from hermes_router import router_core


@pytest.mark.parametrize("payload", [
    "[ASYNC DELEGATION BATCH COMPLETE — deleg_x] results below",
    "[Your active task list was preserved across context compression]",
    "[Depth-3 Summary (d3, node 86)] Active Focus",
    "[OUT-OF-BAND USER MESSAGE — direct message from the user]",
    "[System note: recalled memory context]",
])
def test_system_injected_detected(payload):
    assert router_core._is_system_injected_turn(payload) is True


@pytest.mark.parametrize("ask", [
    "Check valmet session and tell me if she used frontier",
    "  send all pdfs  ",
    "Proceed",
])
def test_real_asks_not_blocked(ask):
    assert router_core._is_system_injected_turn(ask) is False


def test_empty_and_none_safe():
    assert router_core._is_system_injected_turn("") is False
    assert router_core._is_system_injected_turn(None) is False
