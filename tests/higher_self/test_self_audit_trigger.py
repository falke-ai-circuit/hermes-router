"""Regression: self-audit/design-validation asks must hit planning_arch (2026-09-08).

Live case: "Can yiu deeply analyze current hermes router frontier part or higher
self is it actually workng as it shiuld and is design optimal?" scored ZERO
signals — no PRE orientation fired, flash flinched (line_hold_essay), POST
safety net routed. End-state correct but orientation should pre-empt the flinch.
"""
from hermes_router.complexity import stage1_signals, stage1_verdict

LIVE_ASK = (
    "Can yiu deeply analyze current hermes router frontier part or higher self "
    "is it actually workng as it shiuld and is design optimal? If not would "
    "younplan to adjust?"
)


def test_self_audit_ask_matches_planning_arch():
    sigs = stage1_signals(LIVE_ASK)
    assert sigs["planning_arch"] >= 1
    verdict, _ = stage1_verdict(LIVE_ASK, 2)
    assert verdict == "clear_complex"


def test_design_optimal_short_form():
    assert stage1_signals("Is the design optimal for this?")["planning_arch"] >= 1


def test_chat_negatives_still_simple():
    for ask in ("is the coffee machine working", "How was your day",
                "is the book optimal reading order"):
        assert stage1_signals(ask)["planning_arch"] == 0
