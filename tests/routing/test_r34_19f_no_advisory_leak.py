"""R34-19f: nothing the higher-self lanes append may reach the user.

Fleet sweep, 2026-10-21, 10/10 agents: every delivered answer ended with a RAW
(unfenced) advisory JSON object — `{"adversarial": …, "p_failure": 0.72,
"povs": […]}` before the banner and `{"sense_check": …}` after it. The echo
guard FIRED (`families=povs_array`) and still removed nothing it could see,
because the tail regex only matched an object that STARTED with an advisory key.
On reviewer the same turn also delivered 4,750 chars of the higher-self envelope
itself: `[HIGHER-SELF COMPLETION-AUDIT TURN | FRONTIER-DERIVED |
PROVENANCE-LOGGED]`, `[HIGHER-SELF MESSAGE …]`, the five vantages, and the
operating line `SEAM INSTRUCTION: the audit is complete - now PROCEED`.

Rule: the delivered body carries the agent's answer and (at the delivery edge)
the banner. Nothing else.
"""
from hermes_router.echo_guard import (
    strip_advisory_echo,
    detect_envelope_echo_families,
)

# The live shape: prose answer, then a raw advisory object whose FIRST key is
# not an advisory key (that is what defeated the old tail regex).
RAW_TAIL_BODY = (
    "The biggest risk is that a bad change turns a two-minute revert into an "
    "unbounded incident, because no known-good state exists to fall back to."
    "\n\n"
    '{"adversarial": {"strongest_objection": "the generic answer hides the '
    'driver", "failure_mode": "config ships to every profile at once", '
    '"p_failure": 0.72}, "povs": [{"stance": "practitioner", "note": "no '
    'backup copy"}], "sense_check": {"result_plausible": true}}'
)

ENVELOPE_BODY = (
    "Answer: do not ship without a rollback path."
    "\n\n"
    "[HIGHER-SELF COMPLETION-AUDIT TURN | FRONTIER-DERIVED | PROVENANCE-LOGGED]"
    "\n[HIGHER-SELF MESSAGE — from your frontier higher self, model=x]"
    "\nThis is an internal message from your higher self."
    "\nSEAM INSTRUCTION: the audit is complete - now PROCEED. Deliver the "
    "user's answer."
    "\n- Practitioner: it matters when the change propagates fast."
    "\nSynthesis: name irreversibility, not downtime."
)


def test_raw_advisory_tail_is_cut():
    out, removed = strip_advisory_echo(RAW_TAIL_BODY)
    assert '"p_failure"' not in out
    assert '"povs"' not in out
    assert "sense_check" not in out
    assert removed > 0
    # the answer survives intact
    assert out.startswith("The biggest risk is that a bad change")


def test_detection_family_still_fires_on_the_same_body():
    # STRIP learned a shape; DETECTION was deliberately not widened (R34-17).
    assert "povs_array" in detect_envelope_echo_families(RAW_TAIL_BODY)


def test_higher_self_envelope_is_cut():
    out, removed = strip_advisory_echo(ENVELOPE_BODY)
    low = out.lower()
    assert "[higher-self" not in low
    assert "seam instruction" not in low
    assert "provenance-logged" not in low
    assert "synthesis:" not in low
    assert removed > 0
    assert out.startswith("Answer: do not ship without a rollback path.")


def test_ordinary_json_in_an_answer_is_left_alone():
    # A genuine code/config sample carries none of the advisory-only keys and
    # must survive untouched — the strip is not a JSON eraser.
    body = (
        "Here is the config change:\n\n"
        '{"level": 2, "enabled": true, "pre_mode": "route"}\n\n'
        "Apply with the deploy script and bounce the gateway."
    )
    out, removed = strip_advisory_echo(body)
    assert '"level": 2' in out
    assert "bounce the gateway" in out


def test_banner_tail_is_not_promoted_into_the_answer():
    body = RAW_TAIL_BODY + (
        "\n\n· router · higher-self (frontier) | post-check | "
        "anthropic/claude-sonnet-5.5 | $0.017816 | 24s ·"
    )
    out, _ = strip_advisory_echo(body)
    assert "router ·" not in out
    assert '"p_failure"' not in out
    assert out.startswith("The biggest risk is that a bad change")


# --- the frame the leak actually shipped under -------------------------------
# Every agent in the 2026-10-21 sweep logged `envelope_echo_delivered`
# (observe-only) instead of `advisory_echo_stripped`: a plain question has no
# work signal, so the frame is not `execution`, and the guard returned None
# before it ever considered stripping. Stripping is the delivery boundary, not
# an execution decision.
from hermes_router.echo_guard import enforce_execution_echo


def _no_frame(monkeypatch):
    from hermes_router import decision_midturn as dmt
    monkeypatch.setattr(dmt, "_frame_register", lambda: "dialogue", raising=False)
    from hermes_router import work_signal as ws
    monkeypatch.setattr(ws, "work_facts",
                        lambda sid: {"has_work": False, "predicate": "none",
                                     "tools": 0, "writes": 0, "terminal": 0},
                        raising=False)


def test_non_execution_frame_strips_the_advisory_tail(monkeypatch):
    _no_frame(monkeypatch)
    res = enforce_execution_echo(RAW_TAIL_BODY, "s-r34-19f-a")
    assert res is not None, "a non-execution frame must still be stripped"
    assert res.action == "strip_keep_work", res.action
    assert '"p_failure"' not in res.body
    assert res.body.startswith("The biggest risk is that a bad change")


def test_non_execution_frame_strips_the_envelope(monkeypatch):
    _no_frame(monkeypatch)
    res = enforce_execution_echo(ENVELOPE_BODY, "s-r34-19f-b")
    assert res is not None
    low = res.body.lower()
    assert "[higher-self" not in low
    assert "seam instruction" not in low


def test_non_execution_frame_is_never_handed_back(monkeypatch):
    # the handback notice belongs to execution frames only; a question must
    # never be replaced by "nothing was executed"
    _no_frame(monkeypatch)
    res = enforce_execution_echo(RAW_TAIL_BODY, "s-r34-19f-c")
    assert "nothing was executed" not in res.body
