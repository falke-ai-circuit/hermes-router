"""Unit 2 / JOB B — the assimilation frame must be measurable in production.

assimilation.py keeps ``_MARKED`` as an in-process dict. Two real consequences
this unit makes observable (it does NOT change the persistence model):

  (i)  a gateway bounce between the render turn and the next turn drops the
       frame silently — that is every deploy and every restart;
  (ii) nothing was logged when the frame IS injected (compare
       reflex_lane.py:437 decision_rule_injected), so the leg could not be
       verified live.

Pinned here: the injection event (rule id, session, rendered-turn number), and
one drop event for each way a mark disappears — capacity eviction, a frame the
request refused to take, and a mark that died with the previous process.
"""
import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from hermes_router import assimilation  # noqa: E402
from hermes_router import render_inbox  # noqa: E402

INJECTED = "assimilation_frame_injected"
DROPPED = "assimilation_mark_dropped"


@pytest.fixture()
def events(monkeypatch):
    """Capture frame telemetry instead of writing the route ledger."""
    seen = []

    def _capture(session_id, event, **fields):
        seen.append(dict(fields, session_id=session_id, event=event))

    monkeypatch.setattr(assimilation, "_log", _capture)
    assimilation.clear_marks()
    yield seen
    assimilation.clear_marks()


def _by_event(seen, name):
    return [e for e in seen if e["event"] == name]


def test_injection_is_logged_with_rule_session_and_turn(events, monkeypatch):
    monkeypatch.setattr(assimilation, "inject_platform_rule",
                        lambda *a, **k: True)
    assimilation.mark_turn_rendered("sess-inject")
    assert assimilation.inject_frame({"messages": []}, "sess-inject") is True

    rows = _by_event(events, INJECTED)
    assert len(rows) == 1
    row = rows[0]
    assert row["rule"] == assimilation.ASSIMILATION_RULE_ID
    assert row["session_id"] == "sess-inject"
    assert row["turn"] == 1
    assert row["chars"] > 0


def test_frame_refused_by_the_request_logs_a_drop(events, monkeypatch):
    monkeypatch.setattr(assimilation, "inject_platform_rule",
                        lambda *a, **k: False)
    assimilation.mark_turn_rendered("sess-refused")
    assert assimilation.inject_frame({"messages": []}, "sess-refused") is False

    drops = _by_event(events, DROPPED)
    assert [d["reason"] for d in drops] == ["not_appended"]
    assert drops[0]["rule"] == assimilation.ASSIMILATION_RULE_ID
    assert drops[0]["turn"] == 1
    # one-shot: the mark is gone either way
    assert assimilation.pending_turn("sess-refused") is False


def test_capacity_eviction_logs_the_dropped_mark(events, monkeypatch):
    monkeypatch.setattr(assimilation, "_MARK_MAX", 3)
    for i in range(5):
        assimilation.mark_turn_rendered("sess-%d" % i)

    drops = _by_event(events, DROPPED)
    assert drops, "capacity eviction dropped marks without a log"
    assert all(d["reason"] == "capacity" for d in drops)
    assert all(d["rule"] == assimilation.ASSIMILATION_RULE_ID for d in drops)


def test_mark_lost_to_a_process_bounce_is_logged(events, monkeypatch):
    """A POST render recorded BEFORE this process started is the durable proof
    that a mark existed and is gone."""
    old_ts = assimilation._PROCESS_STARTED_AT - 12.0

    def _fake_read(limit=10):
        return [{"ts": old_ts, "stage": "POST", "session_id": "sess-bounced",
                 "trigger_chars": 10, "render_chars": 10, "render": "r"}]

    monkeypatch.setattr(render_inbox, "read_renders", _fake_read)
    assert assimilation.inject_frame({"messages": []}, "sess-bounced") is False

    drops = _by_event(events, DROPPED)
    assert len(drops) == 1
    assert drops[0]["reason"] == "process_restart"
    assert drops[0]["rule"] == assimilation.ASSIMILATION_RULE_ID
    assert drops[0]["session_id"] == "sess-bounced"
    assert drops[0]["render_ts"] == old_ts


def test_a_render_from_this_process_is_not_a_lost_mark(events, monkeypatch):
    """False-positive guard: a render written AFTER this process started means
    its mark is in memory (or was just consumed), never 'lost to a bounce'."""
    fresh_ts = assimilation._PROCESS_STARTED_AT + 5.0

    monkeypatch.setattr(render_inbox, "read_renders",
                        lambda limit=10: [{"ts": fresh_ts, "stage": "POST",
                                           "session_id": "sess-fresh"}])
    assert assimilation.inject_frame({"messages": []}, "sess-fresh") is False
    assert _by_event(events, DROPPED) == []
