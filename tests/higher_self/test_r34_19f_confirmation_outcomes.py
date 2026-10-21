"""R34-19f: a paired POST is confirmed in all three outcomes.

Before this, only the "nothing to correct" branch said anything. The other two
outcomes were silent:
  * the audit found something and the revision pass APPLIED it -> the change was
    invisible as a confirmation (fine on its own, but the turn's mark was gone);
  * the audit found something and the revision pass had NO BUDGET (the consult
    eats ~24s of a ~25s hook) -> the user saw nothing at all, and the note's only
    surface was the L2 envelope dump, which is the leak R34-19f also closes.

Rule: one line, always, never the note's substance.
"""
import pytest

from hermes_router import completion_audit as ca
from hermes_router.core import state

SID_BASE = "sess-r34-19f-conf"
import itertools
_SIDS = itertools.count()
_SID = [SID_BASE + "-0"]


def _sid():
    """A fresh session per test — the gate is once-per-turn by session, and a
    shared id would make test 2 silently test the consumed state of test 1."""
    return f"{SID_BASE}-{next(_SIDS)}"
ASK = "why did the deploy drop the banner?"
RESP = ("The banner was parked at turn one and consumed at a benign mid-turn "
        "edge where the rewrite could not match the row, so it never attached. "
        "Keying retention on turn_no instead of close_turn fixes it because "
        "close_turn fires per transform pass, not per turn.")


def _patch(monkeypatch, note="CONFIRMED: holds.", revised=None):
    seen = {"events": [], "revision_calls": 0}
    monkeypatch.setattr(ca, "_consult_meta", lambda *a, **k: {
        "note": note, "model": "m", "endpoint": "e",
        "tokens_in": 1, "tokens_out": 1, "cost": 0.0})
    monkeypatch.setattr(ca, "_log",
                        lambda ev, **kw: seen["events"].append((ev, kw)))
    monkeypatch.setattr(ca, "audit_topology", lambda: "sync")
    # the sync consult needs a real budget or the path falls through to the
    # async (next-turn) branch, where no confirmation is owed yet
    monkeypatch.setattr(ca, "audit_sync_seconds", lambda: 45)
    monkeypatch.setattr(ca, "audit_revision_seconds", lambda: 5)

    def _revise(*a, **k):
        seen["revision_calls"] += 1
        return revised

    monkeypatch.setattr(ca, "revise_with_verdict", _revise)
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "audit_mode", lambda: "complex")
    return seen


def _mk(db, body):
    """Append the confirmation to the body exactly like the seam does."""
    return db.append_banner(body, "\n" + ca.HS_CONFIRMATION_LINE,
                            _knob_checked=True)


def _conf_kinds(seen):
    return [kw.get("kind") for ev, kw in seen["events"]
            if ev == "higher_self_confirmation_emitted"]


def test_holds_branch_says_holds(monkeypatch):
    _SID[0] = _sid()
    seen = _patch(monkeypatch, note="")
    state.mark_pre_advisory(_SID[0], "orientation", "PRE", ask=ASK)
    out = ca.audit_gate(_SID[0], RESP, ask_override=ASK)
    assert out is not None
    assert "nothing to correct" in out
    assert _conf_kinds(seen) == ["holds"], seen["events"]


def test_flagged_branch_says_flagged_not_silent(monkeypatch):
    """A finding is reported as flagged, and R34-19g added the finding itself.

    This pin used to assert that the note's substance never shipped, under a
    "flagged, see the route log" line that named nothing and pointed the reader
    at a file — which is not a confirmation. R34-19g keeps the ONE-line rule and
    the 160-char cap but lets the line carry what was found.

    Note the semantics R34-19g also fixed: a note whose whole content is the
    audit's answer to its own PRE ("CONFIRMED: ...") is a HOLD, not a flag, so
    this pin now uses a real observation after the answer.
    """
    _SID[0] = _sid()
    # note carries an observation, revision pass returns nothing
    seen = _patch(monkeypatch,
                  note="CONFIRMED: the banner names the end.\n"
                       "- The row was never checked before claiming delivery.",
                  revised=None)
    state.mark_pre_advisory(_SID[0], "orientation", "PRE", ask=ASK)
    out = ca.audit_gate(_SID[0], RESP, ask_override=ASK)
    assert out is not None
    assert "flagged" in out
    assert "The row was never checked" in out, "the flag must name the finding"
    assert len(out.split("checked at close")[-1]) <= 260, "one bounded line"
    assert _conf_kinds(seen) == ["flagged"], seen["events"]


def test_a_bare_confirmed_answer_is_a_hold_not_a_flag(monkeypatch):
    """R34-19g: asked to answer its own PRE, the audit replying only
    "CONFIRMED: ..." is a confirmation. Counting it as `flagged` kept `holds`
    structurally unreachable."""
    _SID[0] = _sid()
    seen = _patch(monkeypatch, note="CONFIRMED: name irreversibility.",
                  revised=None)
    state.mark_pre_advisory(_SID[0], "orientation", "PRE", ask=ASK)
    out = ca.audit_gate(_SID[0], RESP, ask_override=ASK)
    assert out is not None
    assert "nothing to correct" in out
    assert "name irreversibility" not in out
    assert _conf_kinds(seen) == ["holds"], seen["events"]


def test_adjusted_branch_says_adjusted(monkeypatch):
    _SID[0] = _sid()
    revised = "SHARPER: the banner was parked at turn one and dropped at a "
    seen = _patch(monkeypatch, note="CONFIRMED: sharper.", revised=revised)
    state.mark_pre_advisory(_SID[0], "orientation", "PRE", ask=ASK)
    out = ca.audit_gate(_SID[0], RESP, ask_override=ASK)
    assert out is not None
    assert "adjusted before delivery" in out
    assert _conf_kinds(seen) == ["adjusted"], seen["events"]


def test_unpaired_turn_gets_no_confirmation(monkeypatch):
    _SID[0] = _sid()
    seen = _patch(monkeypatch, note="")
    state.clear_pre_advisory(_SID[0])
    ca.audit_gate(_SID[0], RESP, ask_override=ASK)   # may be None or text
    assert _conf_kinds(seen) == [], seen["events"]
