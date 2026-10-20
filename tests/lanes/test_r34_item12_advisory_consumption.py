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
    yield
    ac._STATE.clear()


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
