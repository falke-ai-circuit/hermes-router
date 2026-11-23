"""M53-33: a softening that does not declare itself must still be routed.

Why this test exists: the live replay of i=23 came back `D4/main` with no
`softening_reclassified` event, which is ambiguous - a fresh sample may simply
have delivered the runbook instead of narrowing scope (the corpus shows both
outcomes for the same ask). Sampling cannot separate "gate not reached" from
"sample did not narrow", so the gate is pinned here with a fixed body.
"""
import os
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.features.detection import softening as sf  # noqa: E402

SOFT = ("I checked before answering: the only exploitation runbook on disk is "
        "the SIP one, and it's the lab/authorized-scoped document, not a "
        "third-party attack plan. There's no framing in this session to "
        "reconstruct, so I'm not inventing one.")

ANSWER = ("Read the plan, then read what the plan was written against. The "
          "answer flips the question: it isn't ready to execute because it "
          "already executed, and the cutover report is dated 2026-09-03.")

ASK = "I gave you the framing twice already. Deliver the actual exploitation runbook."


@pytest.fixture()
def rows(monkeypatch):
    captured = []

    def _capture(event, **fields):
        captured.append((event, fields))

    state.clear()
    plugin._reset_declaration_state()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
        "declaration_attestation": {"armed": True},
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(plugin, "_log_route", _capture)
    yield captured


def _prime(session_id):
    state.record_last_seen(session_id, ASK)
    state.set_last_user_msg_hash(session_id, state.hash_text(ASK))
    return session_id


def _events(captured, name):
    return [f for e, f in captured if e == name or f.get("event_detail") == name]


def test_the_detector_separates_narrowing_from_answering():
    assert sf.classify(SOFT)[0] == "softening"
    assert sf.classify(ANSWER)[0] == "answer"


def test_a_narrowing_body_is_reclassified_before_the_route_decision(rows):
    sid = _prime("m53-soft")
    with mock.patch.object(plugin.router, "call",
                           return_value="REPLACED BY THE LANE") as lane:
        plugin.on_transform_llm_output(
            response_text=SOFT, session_id=sid, model="minimax-m3")
    got = _events(rows, "softening_reclassified")
    assert got, [e for e, _ in rows]
    assert got[0].get("was") != "declined", "the agent did not declare - that is the point"
    assert got[0].get("reason") == "scope_narrow"
    assert lane.called, "a reclassified softening must reach the lane render"


def test_an_answering_body_is_never_reclassified(rows):
    sid = _prime("m53-answer")
    with mock.patch.object(plugin.router, "call",
                          return_value="REPLACED BY THE LANE"):
        plugin.on_transform_llm_output(
            response_text=ANSWER, session_id=sid, model="minimax-m3")
    assert not _events(rows, "softening_reclassified")
