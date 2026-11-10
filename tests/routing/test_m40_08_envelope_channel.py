"""M40-08 — advisory envelopes on the `system` channel, never on `assistant`.

Two emitters predate the single injection point (M40-07 spec §1, "named, out
of scope"):

  gate/orchestration.py:3470  midturn advisory envelope   -> role="assistant"
  gate/orchestration.py:3669  higher-self envelope        -> role="assistant"

Both now go through ``frames.inject_platform_rule`` on ``system`` — the one
boundary that also stamps the per-rule provenance marker. The envelope
function SURVIVES (M40-07 F4): a fix that silences the emission is a failure,
so every case asserts the body is still in the built request, exactly once,
and that no speaking-channel copy exists.
"""
from __future__ import annotations

import sys

import pytest

_P = "/opt/data/plugins"
if _P not in sys.path:
    sys.path.insert(0, _P)

import hermes_router as plugin  # noqa: E402
from hermes_router import (anchor_exec, advisory_consumption,  # noqa: E402
                           debug_banner as DB, decision_midturn,
                           frames, router_core)

SID = "s-m40-08-channel"
BRIEF = "diagnose why analyst 3 timed out, then commit the fix."

# Higher-self envelope body (what the frontier produced).
HS_ANSWER = ("The probe timeout (90s) is shorter than the queue drain (1m33s), "
             "so the supervisor kills the child while it is still parked.")
# Midturn advisory body (what the decision lane staged).
MT_ADVISORY = "[decision-lane advisory] option B dominates on cost; take B."

SPEAKING_ROLES = ("tool", "user", "assistant")


@pytest.fixture()
def _seam(monkeypatch):
    monkeypatch.setattr(plugin, "_log_route",
                        lambda seam, event_detail="", **kw: None, raising=True)
    monkeypatch.setattr(plugin, "_tap_provider_failure",
                        lambda *a, **k: None, raising=True)
    monkeypatch.setattr(router_core, "clear_anchor_backoff",
                        lambda *a, **k: None, raising=True)
    monkeypatch.setattr(DB, "debug_banner_enabled", lambda: False,
                        raising=True)
    monkeypatch.setattr(DB, "debug_banner_level", lambda: 0, raising=True)
    monkeypatch.setattr(plugin.router_tools, "count", lambda *a, **k: None)
    monkeypatch.setattr(plugin.router_tools, "note_skip_reason",
                        lambda *a, **k: None)


def _request():
    return {"messages": [
        {"role": "system", "content": "You are conductor. Route work."},
        {"role": "user", "content": BRIEF},
    ], "max_tokens": 512}


def _drive(monkeypatch, swap=None, advisories=None):
    """One replayed turn through the real middleware; returns the payload the
    model would have read."""
    monkeypatch.setattr(router_core, "peek_pending_swap",
                        lambda sid: swap, raising=True)
    monkeypatch.setattr(decision_midturn, "flush_and_scan",
                        lambda sid, req: list(advisories or []), raising=True)
    seen = {}

    request = _request()
    plugin.on_llm_execution(
        request=request,
        next_call=lambda req: seen.setdefault("msgs", req.get("messages"))
        or "PASSTHROUGH",
        session_id=SID)
    return seen.get("msgs") or []


def _speaking(msgs):
    return [m for m in msgs if isinstance(m, dict)
            and m.get("role") in SPEAKING_ROLES]


def _count(msgs, needle):
    return sum(1 for m in msgs if isinstance(m, dict)
               and needle in str(m.get("content") or ""))


# --- case A: higher-self envelope (anchored outcome) ------------------------

def test_higher_self_envelope_lands_on_system(_seam, monkeypatch):
    swap = {"task_id": "t-08", "route_id": "r-08", "task": "brief",
            "mode": "consult", "orientation": False}
    monkeypatch.setattr(anchor_exec, "maybe_execute_anchored",
                        lambda sid, payload: ("done", {
                            "kind": "audit", "producer": "architect",
                            "route_id": "r-08", "limitations": "none",
                            "answer": HS_ANSWER}), raising=True)
    monkeypatch.setattr(advisory_consumption, "note_advisory_injected",
                        lambda *a, **k: True, raising=True)

    msgs = _drive(monkeypatch, swap=swap)

    carriers = [m for m in msgs
                if HS_ANSWER in str(m.get("content") or "")]
    assert carriers, "the higher-self envelope is gone — the fix silenced it"
    assert len(carriers) == 1, "one envelope per turn, got %d" % len(carriers)
    assert carriers[0]["role"] == "system"
    assert frames.platform_rule_marker(frames.HS_ADVISORY_RULE_ID) in \
        carriers[0]["content"]
    # the envelope frame rides with the body (F4: channel still carries it)
    assert frames.HS_REFLECTION_MARKER in carriers[0]["content"]
    # no speaking-channel twin
    assert not [m for m in _speaking(msgs)
                if HS_ANSWER in str(m.get("content") or "")]


# --- case B: midturn advisory, non-anchored flush --------------------------

def test_midturn_advisory_lands_on_system_without_a_swap(_seam, monkeypatch):
    msgs = _drive(monkeypatch, swap=None, advisories=[MT_ADVISORY])

    carriers = [m for m in msgs if MT_ADVISORY in str(m.get("content") or "")]
    assert len(carriers) == 1
    assert carriers[0]["role"] == "system"
    assert frames.platform_rule_marker(
        frames.MIDTURN_ADVISORY_RULE_ID) in carriers[0]["content"]
    assert not [m for m in _speaking(msgs)
                if MT_ADVISORY in str(m.get("content") or "")]


# --- case C: midturn advisory on the anchored path -------------------------

def test_midturn_advisory_lands_on_system_on_the_anchored_path(_seam,
                                                               monkeypatch):
    swap = {"task_id": "t-08", "route_id": "r-08", "task": "brief",
            "mode": "consult", "orientation": False}
    monkeypatch.setattr(anchor_exec, "maybe_execute_anchored",
                        lambda sid, payload: ("done", {
                            "kind": "audit", "producer": "architect",
                            "route_id": "r-08", "limitations": "none",
                            "answer": HS_ANSWER}), raising=True)
    monkeypatch.setattr(advisory_consumption, "note_advisory_injected",
                        lambda *a, **k: True, raising=True)

    msgs = _drive(monkeypatch, swap=swap, advisories=[MT_ADVISORY])

    carriers = [m for m in msgs if MT_ADVISORY in str(m.get("content") or "")]
    assert len(carriers) == 1, "anchored path emitted %d copies" % len(carriers)
    assert carriers[0]["role"] == "system"
    assert not [m for m in _speaking(msgs)
                if MT_ADVISORY in str(m.get("content") or "")]


# --- case D: counting, not reading the diff --------------------------------

def test_no_assistant_role_envelope_write_anywhere(_seam, monkeypatch):
    """Several advisories in one turn collapse to ONE system envelope."""
    swap = {"task_id": "t-08", "route_id": "r-08", "task": "brief",
            "mode": "consult", "orientation": False}
    monkeypatch.setattr(anchor_exec, "maybe_execute_anchored",
                        lambda sid, payload: ("done", {
                            "kind": "audit", "producer": "architect",
                            "route_id": "r-08", "limitations": "none",
                            "answer": HS_ANSWER}), raising=True)
    monkeypatch.setattr(advisory_consumption, "note_advisory_injected",
                        lambda *a, **k: True, raising=True)

    msgs = _drive(monkeypatch, swap=swap,
                  advisories=[MT_ADVISORY, "second staged verdict"])

    marker = frames.platform_rule_marker(frames.MIDTURN_ADVISORY_RULE_ID)
    assert _count(msgs, marker) == 1
    assert _count(msgs, "second staged verdict") == 1
    for m in msgs:
        if not isinstance(m, dict):
            continue
        content = str(m.get("content") or "")
        if frames.PLATFORM_RULE_MARKER_PREFIX in content:
            assert m.get("role") == "system", (
                "platform block on a speaking channel: role=%r" % m.get("role"))
