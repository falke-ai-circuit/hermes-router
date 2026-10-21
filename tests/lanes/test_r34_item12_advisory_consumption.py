"""R34-12 item 9 — the WORK INVARIANT observable.

Live: session api_1792536611_5dbebc6b injected the PRE anchor advisory into a
dispatch turn, the turn closed with ZERO tool calls, and the body was the
advisory restated. Nothing in the log said so.

Contract: if an advisory was injected AND the user message is a dispatch/task
instruction AND the turn closed with no tool activity, emit
`advisory_consumed_turn no_tools=true session_id=...`. Log-only, fail-open.
"""
import os
import sys

_P = "/opt/data/plugins"
if _P not in sys.path:
    sys.path.insert(0, _P)

import pytest

from hermes_router import advisory_consumption as ac  # noqa: E402

SID = "s-r34-12-ac"
DISPATCH = ("R34-12 — implement items 7-9 in the router, add tests, then "
            "commit and report the suite counts.")


@pytest.fixture(autouse=True)
def _reset():
    ac._STATE.clear()
    ac._ORDER.clear()
    ac._TOOL_SEQ = 0
    yield
    ac._STATE.clear()
    ac._ORDER.clear()
    ac._TOOL_SEQ = 0


def test_dispatch_detection_covers_the_live_brief_shape():
    assert ac.looks_like_dispatch(DISPATCH) is True
    assert ac.looks_like_dispatch(
        "R34-11 — the lane made a real turn WORSE.\n\nITEMS:\n1. do a\n2. do b\n3. do c") is True
    assert ac.looks_like_dispatch(
        "What are the tradeoffs of A versus B?") is False


def test_advisory_then_zero_tools_on_a_dispatch_turn_fires():
    rows = []
    ac.note_advisory_injected(SID, DISPATCH, chars=180)
    fired = ac.close_turn(SID, log_route=lambda e, **f: rows.append((e, dict(f))))
    assert fired is True
    assert len(rows) == 1
    assert rows[0][1].get("event_detail") == "advisory_consumed_turn"
    assert rows[0][1].get("no_tools") == "true"
    assert rows[0][1].get("session_id") == SID


def test_advisory_then_tool_activity_is_clean():
    rows = []
    ac.note_advisory_injected(SID, DISPATCH, chars=180)
    ac.note_tool_activity(SID)
    assert ac.close_turn(SID, log_route=lambda e, **f: rows.append((e, dict(f)))
                         ) is False
    assert rows == []


def test_advisory_on_a_non_dispatch_ask_is_clean():
    rows = []
    ac.note_advisory_injected(SID, "What are the tradeoffs of A versus B?", chars=180)
    assert ac.close_turn(SID, log_route=lambda e, **f: rows.append((e, dict(f)))
                         ) is False
    assert rows == []


def test_terminal_seam_prefix_converges_on_the_bare_session_id():
    rows = []
    ac.note_advisory_injected(SID, DISPATCH, chars=180)      # PRE edge: bare id
    ac.note_tool_activity("session:" + SID)                  # terminal seam
    assert ac.close_turn(SID, log_route=lambda e, **f: rows.append((e, dict(f)))
                         ) is False
    assert rows == []


def test_no_advisory_no_event():
    rows = []
    assert ac.close_turn(SID, log_route=lambda e, **f: rows.append((e, dict(f)))
                         ) is False
    assert rows == []


def test_reentry_in_the_same_turn_keeps_the_tool_counter():
    ac.note_advisory_injected(SID, DISPATCH, chars=180)
    ac.note_tool_activity(SID)
    ac.note_advisory_injected(SID, DISPATCH, chars=180)   # later LLM call
    rows = []
    assert ac.close_turn(SID, log_route=lambda e, **f: rows.append((e, dict(f)))
                         ) is False
    assert rows == []


# ---------------------------------------------------------------------------
# R34-13 defect 1 — the live false positive, rebuilt from the real turn shape.
#
# Session api_1792540767_c76f02f0 (verified in the profile state.db): 1 user
# message, 10 assistant messages, 14 tool messages. The turn emitted
# `advisory_consumed_turn no_tools=true advisory_chars=1951` anyway.
# Root cause: the host fires transform_terminal_output with kwargs
# (command, output, returncode, task_id, env_type) and NO session id, so the
# old counter never saw a single one of the 14 runs.
# ---------------------------------------------------------------------------
LIVE_SID = "api_1792540767_c76f02f0"
LIVE_TOOL_CALLS = 14


def _real_terminal_seam_hits(n: int) -> None:
    """Drive the plugin's terminal hook exactly as the host does: no
    session_id in the kwargs. This is the seam shape that broke accounting."""
    from hermes_router import on_transform_terminal_output

    for _i in range(n):
        on_transform_terminal_output(
            command="cd /opt/data/plugins/hermes_router && pytest -q",
            output=".... 14 passed",
            returncode=0,
            task_id="",
            env_type="local",
        )


def test_fourteen_tool_calls_through_the_real_seam_never_emit_no_tools():
    """An advisory-present turn with >=1 tool call MUST NOT emit."""
    rows = []
    log = lambda e, **f: rows.append((e, dict(f)))  # noqa: E731
    ac.note_advisory_injected(LIVE_SID, DISPATCH,
                              chars=600, envelope_chars=1951)
    _real_terminal_seam_hits(LIVE_TOOL_CALLS)
    fired = ac.close_turn(LIVE_SID, log_route=log)
    assert fired is False, "a 14-tool turn must never be reported as no_tools"
    assert rows == []
    assert ac._STATE.get(LIVE_SID) is None


def test_zero_tool_calls_on_an_advisory_dispatch_turn_emits_no_tools():
    """An advisory-present turn with ZERO tool calls MUST emit."""
    rows = []
    log = lambda e, **f: rows.append((e, dict(f)))  # noqa: E731
    ac.note_advisory_injected(LIVE_SID, DISPATCH,
                              chars=600, envelope_chars=1951)
    fired = ac.close_turn(LIVE_SID, log_route=log)
    assert fired is True
    assert len(rows) == 1
    assert rows[0][1].get("event_detail") == "advisory_consumed_turn"
    assert rows[0][1].get("no_tools") == "true"
    assert rows[0][1].get("session_id") == LIVE_SID


def test_event_labels_envelope_chars_and_advisory_chars_separately():
    """R34-13 defect 2: the capped advisory body and the whole injected
    envelope are different numbers and must be labelled as such. Live shape:
    anchor capped to 600, envelope 1951."""
    rows = []
    ac.note_advisory_injected(LIVE_SID, DISPATCH,
                              chars=600, envelope_chars=1951)
    assert ac.close_turn(LIVE_SID,
                         log_route=lambda e, **f: rows.append((e, dict(f)))
                         ) is True
    fields = rows[0][1]
    assert fields.get("advisory_chars") == 600
    assert fields.get("envelope_chars") == 1951
    assert fields.get("advisory_chars") != fields.get("envelope_chars")


def test_unattributed_tool_activity_never_produces_a_false_accusation():
    """Defence in depth: if a tool run is seen in the turn window but cannot
    be attributed to the session, the event stays silent — a false positive
    is worse than a missing row."""
    rows = []
    ac.note_advisory_injected(LIVE_SID, DISPATCH, chars=600,
                              envelope_chars=1951)
    ac._ORDER.clear()                # attribution path lost, state survives
    ac.note_tool_activity("")        # only the global sequence advances
    assert ac.close_turn(LIVE_SID,
                         log_route=lambda e, **f: rows.append((e, dict(f)))
                         ) is False
    assert rows == []
