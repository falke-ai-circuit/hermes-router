"""Pins for M14: the reflex/decision lane plugged into the unified envelope.

The lane (valmet's entry path) assembled its request by hand — ``build_state``
straight into ``decision_extract.payload`` — so it produced ZERO
``lane-request/1`` records in eleven days of live traffic and the audit could
not see what it sent. These pins hold:

  a) the lane's request record is a real lane-request/1 envelope, built by the
     ONE builder with the lane's own per-agent content (options from the
     candidate board, task from the session anchor);
  b) the envelope validates (no closed-key or core-slot violation);
  c) the record is EMITTED at both seams that build it (midturn ``fire`` and
     the PRE/every-call seam) — capture is part of the fix, not a follow-up.
"""
from __future__ import annotations

import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router import reflex_lane as RL  # noqa: E402
from hermes_router.features.envelope import unified as U  # noqa: E402

CANDS = [
    {"label": "read the failing test first", "move": "read the test", "kind": "read"},
    {"label": "make the minimal edit", "move": "edit one line", "kind": "edit"},
]
ANCHOR = {"final_objective": "ship M14", "rolling_task": "route the lane",
          "session_context": "two turns so far"}


def test_lane_request_is_a_unified_envelope_with_this_lanes_content():
    env = RL.build_lane_request("s-m14", "do the thing", "EVIDENCE BODY", CANDS,
                                card="ROLE CARD", trigger="pre", anchor=ANCHOR)

    assert env["schema"] == "lane-request/1"
    assert env["lane"] == "decision"
    assert env["trigger"] == "pre"
    assert U.validate_request(env) == [], U.validate_request(env)
    extras = env["extras"]["decision"]
    assert [o["id"] for o in extras["options"]] == ["a1", "a2"]
    assert extras["options"][0]["label"] == "read the failing test first"
    assert extras["task"] == "route the lane"


def test_lane_request_refuses_without_raising_on_bad_input():
    assert isinstance(RL.build_lane_request("", "", "", None), dict)


def _stub_lane(monkeypatch):
    """Stub the lane's outside world: card, board, substrate. Nothing but the
    record path is under test here."""
    import hermes_router.decision as DEC
    import hermes_router.decision_extract as DX

    monkeypatch.setattr(RL, "_agent_card", lambda messages=None: "CARD")
    monkeypatch.setattr(RL, "_held", lambda *a, **k: "")
    monkeypatch.setattr(RL, "_transport", lambda payload, cfg: None)
    monkeypatch.setattr(RL, "_cfg", lambda cfg=None: {"enabled": True,
                                                      "every_call": True,
                                                      "min_top": 0.45,
                                                      "min_margin": 0.10})
    monkeypatch.setattr(DX, "candidate_set", lambda *a, **k: list(CANDS_ALL))
    monkeypatch.setattr(DEC, "session_anchor", lambda sid: dict(ANCHOR))


CANDS_ALL = [
    {"label": "read the failing test first", "move": "read the test",
     "kind": "read", "target": "tests/x.py"},
    {"label": "make the minimal edit", "move": "edit one line",
     "kind": "edit", "target": "mod.py"},
]


def test_fire_emits_the_lane_request_before_transport(monkeypatch):
    seen = []
    monkeypatch.setattr(RL, "_log", lambda sid, ev, **kw: seen.append((ev, kw)))
    _stub_lane(monkeypatch)

    RL.fire("s-m14b", "read_file", "x" * 200,
            {"enabled": True, "every_call": True}, ask="do the thing")

    reqs = [kw for ev, kw in seen if ev == "lane_request"]
    assert reqs, "no lane-request record emitted at the midturn seam"
    assert reqs[0]["schema"] == "lane-request/1"
    assert reqs[0]["lane"] == "decision"
    assert reqs[0]["trigger"] == "midturn"


def test_pre_seam_emits_the_lane_request(monkeypatch):
    seen = []
    monkeypatch.setattr(RL, "_log", lambda sid, ev, **kw: seen.append((ev, kw)))
    _stub_lane(monkeypatch)
    monkeypatch.setattr(RL, "settle", lambda sid, request: {})
    monkeypatch.setattr(RL, "_ensure_rule", lambda request, sid, c: None)
    monkeypatch.setattr(RL, "_window_from_request", lambda request: ("ask", "y" * 300))
    monkeypatch.setattr(RL, "_last_action_tool", lambda msgs: "read_file")

    req = {"messages": [{"role": "user", "content": "work"}]}
    RL.run_on_request(req, "s-m14c", {"enabled": True, "every_call": True})

    reqs = [kw for ev, kw in seen if ev == "lane_request"]
    assert reqs, "no lane-request record emitted at the PRE seam"
    assert reqs[0]["schema"] == "lane-request/1"
    assert reqs[0]["trigger"] == "pre"


# --- M18: the record must reach the ROUTE LOG, not just _log --------------------
# The seam above stubs `_log`, which is exactly how the defect hid: the lane
# logged a lane-request record with its own `trigger`, decision_log passed a
# fixed `trigger=TRIGGER` beside it, python raised "got multiple values for
# keyword argument 'trigger'", and the fail-open except swallowed it — a billed
# consult shipped its receipt row and zero lane-request rows. These pins go
# through the real emitter chain to the route-log dispatcher.

def _captured_route_rows(monkeypatch):
    from hermes_router.core import patchpoints as PP

    rows = []

    def _cap(event, **fields):
        rows.append((event, fields))

    monkeypatch.setattr(PP, "log_route_override", _cap)
    return rows


def test_the_seam_row_lands_in_the_route_log_with_schema_and_lane(monkeypatch):
    rows = _captured_route_rows(monkeypatch)
    _stub_lane(monkeypatch)

    RL.fire("s-m18", "read_file", "x" * 200,
            {"enabled": True, "every_call": True}, ask="do the thing")

    landed = [f for ev, f in rows if ev == "lane_request"]
    assert landed, "the lane-request record never reached the route log"
    assert landed[0]["schema"] == "lane-request/1"
    assert landed[0]["lane"] == "decision"
    assert landed[0]["trigger"] == "midturn"
    assert landed[0]["session_id"] == "s-m18"


def test_the_emitters_own_trigger_reaches_the_route_log(monkeypatch):
    """The bug in one line: `trigger` from the emitter must not collide with
    the default decision-log trigger."""
    from hermes_router import decision_log as DL

    rows = _captured_route_rows(monkeypatch)
    DL.log_event("s-m18b", "lane_request", schema="lane-request/1",
                 lane="decision", trigger="pre", seam="every_call")

    assert rows and rows[0][0] == "lane_request"
    assert rows[0][1]["trigger"] == "pre"
    assert rows[0][1]["lane"] == "decision"
    assert rows[0][1]["schema"] == "lane-request/1"


def test_an_emitter_that_names_no_trigger_still_reads_midturn(monkeypatch):
    from hermes_router import decision_log as DL

    rows = _captured_route_rows(monkeypatch)
    DL.log_event("s-m18c", "reflex_held", reason="no_candidates")

    assert rows and rows[0][1]["trigger"] == "midturn"
