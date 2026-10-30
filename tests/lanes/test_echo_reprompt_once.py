"""Echo-withheld turn recovery: re-prompt the SAME turn exactly once.

Brief: /opt/data/tmp/echo_withhold_brief.md (2026-10-30). Live defect, twice in
one day: a real one-paragraph decision question closed with the echo guard's
hand-back notice as the ONLY content —

    "...turn ended on the router's own advisory echo, not work product. No tool
     activity or file write was observed this turn, so nothing was executed.
     Re-send the ask to run this turn again."

The detection was right (an echo must never be delivered as the agent's work).
Ending the turn there was the defect. These tests pin the RECOVERY, and the only
number that proves "exactly once" is the model-call count each test observes:

    clean turn                     -> 1 call (the turn's own)
    echo with no work, recovers     -> 2 calls (turn + ONE re-prompt)
    echo twice                      -> 2 calls (no third call), fail open

The fake model is installed at ``echo_reprompt.call_model`` — the single seam
through which a re-prompt can happen — and the SAME counter is used to produce
the turn's own first body, so the count is the whole turn's traffic.
"""
import pathlib
import sys

import pytest

PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[2]
PARENT_DIR = PLUGIN_DIR.parent  # plugins/ — package import needs it
for _p in (str(PLUGIN_DIR), str(PARENT_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import (decision_midturn, echo_guard, echo_guard as eg,  # noqa: E402,F401
                           echo_reprompt, state, work_signal as ws)

SID = "api_echo_reprompt_20261030"

ASK = (
    "Decide, one paragraph: the echo guard withholds a body on a turn that did "
    "no work and the user is left with a hand-back notice. Given the guard must "
    "never ship a router echo as work product, what is the correct recovery, and "
    "where does it belong in the delivery seam?"
)

# The live shape: the whole body IS the advisory envelope echoed verbatim.
ECHO_BODY = (
    '{"povs":[{"role":"practitioner","claim":"strip before deliver"}],'
    '"synthesis":"gate on the work signal first","brief_bullets":3}'
)

# What the re-prompt is expected to produce: an actual answer.
ANSWER_BODY = (
    "Re-prompt the same turn once with a directive that names the previous "
    "completion as the router's own advisory rather than work, then deliver the "
    "second body if it is not an echo. A second echo fails open on an honest "
    "no-work notice instead of the re-send dead-end. The recovery belongs in the "
    "delivery seam's withhold branch, after the guard has decided, because the "
    "guard owns detection and must not own delivery."
)

CLEAN_BODY = (
    "The guard should recover the turn rather than end it: re-prompt once, "
    "deliver the second body when it is clean, and fail open when it is not."
)


def _cfg():
    return {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    }


@pytest.fixture(autouse=True)
def _seam(monkeypatch):
    """Drive the real delivery seam with the real echo guard, execution frame."""
    state.clear()
    ws.reset()
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg())
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(decision_midturn, "_frame_register",
                        lambda cfg=None: "execution")
    # Persistence is not the subject here; record the write instead of touching
    # a store (the canonical isolation fixtures already redirect the DB).
    import hermes_router.core.canonical as canonical
    persisted = {}

    def _rewrite(session_id, old, new, **kw):
        persisted["body"] = new
        return True

    monkeypatch.setattr(canonical, "rewrite_persisted_turn", _rewrite)
    monkeypatch.setattr(canonical, "verify_persisted_turn",
                        lambda s, b: persisted.get("body") == b)
    yield persisted
    state.clear()
    ws.reset()


class Turn:
    """One turn's model traffic: the turn's own call, then any re-prompt."""

    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.calls = []

    def model(self, messages, timeout=None):
        self.calls.append(list(messages))
        return self.bodies[len(self.calls) - 1]

    @property
    def count(self):
        return len(self.calls)


def _run_turn(monkeypatch, bodies):
    """Make the turn's call, run the delivery seam, return (delivered, turn)."""
    turn = Turn(bodies)
    monkeypatch.setattr(echo_reprompt, "call_model",
                        lambda messages, timeout=None: turn.model(messages, timeout))
    # The turn's own completion (this is the model call the turn already made).
    first = turn.model([{"role": "user", "content": ASK}])
    state.record_last_seen(SID, ASK)
    delivered = plugin.on_transform_llm_output(
        response_text=first, session_id=SID, model="minimax-m3",
        user_message=ASK)
    return delivered, turn


def test_echo_with_no_work_reprompts_once_and_delivers_the_second_body(monkeypatch):
    """The withheld turn is recovered: second body delivered, persisted equal."""
    delivered, turn = _run_turn(monkeypatch, [ECHO_BODY, ANSWER_BODY])

    assert delivered == ANSWER_BODY
    # exactly one re-prompt: the turn's call + the re-prompt
    assert turn.count == 2, turn.calls
    # the re-prompt carried the ask, the failed completion and the directive
    reprompt = turn.calls[1]
    assert any(m["content"] == ASK for m in reprompt)
    assert any(m["content"] == ECHO_BODY for m in reprompt)
    assert reprompt[-1]["role"] == "user"
    assert "advisory" in reprompt[-1]["content"].lower()
    # the dead-end is gone and the echo was never delivered as the answer
    assert "Re-send the ask" not in delivered
    assert delivered != ECHO_BODY


def test_echo_twice_fails_open_without_a_third_call(monkeypatch):
    """Second echo: fail open on the honest notice, never a third model call."""
    delivered, turn = _run_turn(monkeypatch, [ECHO_BODY, ECHO_BODY, ANSWER_BODY])

    assert turn.count == 2, "a second re-prompt must not happen"
    assert delivered == eg.NO_WORK_TEXT
    assert "Re-send the ask" not in delivered
    assert delivered != ECHO_BODY
    assert "no work product" in delivered


def test_clean_turn_makes_no_extra_model_call(monkeypatch):
    """A body that is not an echo is untouched and costs no re-prompt."""
    delivered, turn = _run_turn(monkeypatch, [CLEAN_BODY])

    assert turn.count == 1, "a clean turn must not spend a re-prompt"
    # unchanged: the seam returns either the same body or the pass-through None
    assert delivered in (None, CLEAN_BODY)


def test_the_guard_detection_is_not_loosened():
    """The recovery must not make the guard permissive: an echo is still caught."""
    assert echo_guard.detect_envelope_echo_families(ECHO_BODY)
    assert echo_reprompt._is_echo(ECHO_BODY) is True
    assert echo_reprompt._is_echo(ANSWER_BODY) is False


def test_no_work_echo_is_narrow(monkeypatch):
    """Only a withhold on a no-work turn may spend the re-prompt."""
    handback = echo_guard.EchoResult("handback_failure", "x", ["povs_array"], 10,
                                     {"has_work": False, "tools": 0})
    read_only = echo_guard.EchoResult("withhold_echo", "x", ["povs_array"], 10,
                                     {"has_work": False, "tools": 3})
    strip_failed = echo_guard.EchoResult("withhold_echo", "x", ["povs_array"], 10,
                                        {"has_work": True, "tools": 2})
    kept = echo_guard.EchoResult("strip_keep_work", ANSWER_BODY, ["povs_array"], 4, {})

    assert echo_guard.is_no_work_echo(handback) is True
    assert echo_guard.is_no_work_echo(read_only) is False
    assert echo_guard.is_no_work_echo(strip_failed) is False
    assert echo_guard.is_no_work_echo(kept) is False
    assert echo_guard.is_no_work_echo(None) is False
