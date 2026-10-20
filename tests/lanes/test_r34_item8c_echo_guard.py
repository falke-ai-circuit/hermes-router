"""R34 item 8c — ECHO GUARD (observability only, fail-open, never rewrites).

Live: a delivered body read
`(The option text for "why_not" was omitted by the author...` — an envelope
restatement was shipped as the answer.

The guard reports `envelope_echo_delivered` with session_id + pattern family
and NEVER touches the body.
"""
import os
import sys

_P = "/opt/data/plugins"
if _P not in sys.path:
    sys.path.insert(0, _P)

import pytest  # noqa: E402

from hermes_router import echo_guard  # noqa: E402

MUST_MATCH = [
    ("[ROUTER ADVISORY — decision lane; banner at turn close; may ignore]\n"
     "the fork surfaces as: ...", "advisory_header"),
    ('(The option text for "why_not" was omitted by the author. The option '
     "existed in the decision an...", "option_text_placeholder"),
    ("This line was omitted by the author for brevity.",
     "omission_placeholder"),
]

NORMAL = (
    "R34 item 1: anchor_exec.py:832 now passes `endpoint` instead of `ep`. "
    "Committed de2e385; the regression test fails at 9185f15.")


@pytest.mark.parametrize("text,family", MUST_MATCH)
def test_envelope_restatement_is_detected(text, family):
    assert echo_guard.detect_envelope_echo(text) == family


def test_normal_engineering_reply_is_clean():
    assert echo_guard.detect_envelope_echo(NORMAL) == ""
    assert echo_guard.detect_envelope_echo("") == ""


def test_observe_emits_event_and_never_returns_or_rewrites_the_body():
    rows = []
    out = echo_guard.observe(MUST_MATCH[0][0], "s-8c",
                             log_route=lambda e, **f: rows.append((e, dict(f))))
    assert out == "advisory_header"
    assert rows and rows[0][1].get("event_detail") == "envelope_echo_delivered"
    assert rows[0][1].get("session_id") == "s-8c"
    assert rows[0][1].get("pattern") == "advisory_header"


def test_clean_body_logs_nothing():
    rows = []
    assert echo_guard.observe(NORMAL, "s-8c",
                              log_route=lambda e, **f: rows.append((e, dict(f)))
                              ) == ""
    assert rows == []
