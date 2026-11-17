"""M50-35 — work mass exempts the POST length floor.

Owner ruling (2026-11-16): "heavy but terse is still frontier or higher-self
eligible."

Measured defect this pins: a turn with tools=84 files=20 muts=40 cleared the
work-mass gate (`fire=True reason=mass`) and was then refused by a DIFFERENT arm
as `response_too_short_unpaired chars=211`, purely because the agent summarised
its work tersely. The floor exists to stop a ONE-LINE reply buying a consult;
work mass already proves the turn was not a one-liner.

What must NOT change: a non-mass terse turn is still refused (the floor still
decides size), and a non-text body is refused regardless of mass (nothing to
audit).
"""
import os
import sys

PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PARENT)

from hermes_router import completion_audit as ca  # noqa: E402

SHORT = "Done: 20 files created and run. Bottom line: all green."   # < 500 chars
LONG = "x" * (ca._MIN_RESPONSE_CHARS + 50)


# ------------------------------------------------------------------ helper ----

def test_short_body_without_mass_is_still_blocked():
    assert ca._length_floor_blocks(SHORT, False, False) is True


def test_short_body_with_work_mass_is_exempt():
    """The ruling: heavy work reported tersely stays eligible."""
    assert ca._length_floor_blocks(SHORT, False, True) is False


def test_short_body_paired_is_exempt_A_regression_guard():
    """R34-19 behaviour must survive: a paired confirmation is always owed."""
    assert ca._length_floor_blocks(SHORT, True, False) is False


def test_long_body_never_blocks():
    assert ca._length_floor_blocks(LONG, False, False) is False


def test_non_text_body_blocks_even_with_mass():
    """Nothing to audit is a different refusal from 'too short'."""
    assert ca._length_floor_blocks(None, False, True) is True
    assert ca._length_floor_blocks(None, True, True) is True


def test_whitespace_only_is_short():
    assert ca._length_floor_blocks("   \n\t ", False, False) is True


# ---------------------------------------------------------------- eligible ----

def _mode(monkeypatch, value="complex"):
    monkeypatch.setattr(ca, "audit_mode", lambda: value)


def test_eligible_with_mass_does_not_report_too_short(monkeypatch):
    _mode(monkeypatch)
    ok, why = ca.eligible("s", "ask", SHORT, "model", turn_n=5, work_mass=True)
    assert why != "response_too_short", why


def test_eligible_without_mass_still_reports_too_short(monkeypatch):
    _mode(monkeypatch)
    ok, why = ca.eligible("s", "ask", SHORT, "model", turn_n=5, work_mass=False)
    assert ok is False and why == "response_too_short"


def test_eligible_mode_off_still_wins(monkeypatch):
    """The master switch outranks mass — mass must not bypass audit_mode off."""
    _mode(monkeypatch, "off")
    ok, why = ca.eligible("s", "ask", LONG, "model", turn_n=5, work_mass=True)
    assert ok is False and why == "mode_off"
