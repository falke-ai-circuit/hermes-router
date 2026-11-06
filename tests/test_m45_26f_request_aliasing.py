"""M45-26F — a failed anchor consult must not rewrite the request the
fallback model reads.

RED at HEAD (6c35336): gate/orchestration.py:3361 hands the LIVE request dict
into ``anchor_exec.maybe_execute_anchored``; that function rewrites the last
user message IN PLACE (anchor_exec.py:1931 — ``_msgs[_last_user] = {... "content":
_frame}``; also :1951 system-message append and :1954 insert) before the
consult call. When the consult then fails, the caller fail-opens with
``return next_call(request)`` (gate/orchestration.py:3435) — the SAME dict —
so the fallback model is asked the CONSULT QUESTION instead of the dispatched
brief. Live cost today: 4 turns across coder + operative answered the frame,
``finish_reason=stop``, zero tool calls, session left open.

Contract under test: the payload handed to ``next_call`` after an anchor
failure carries the ORIGINAL last user message, character for character, and
the caller's own request object is left unmutated.
"""
from __future__ import annotations

import sys

import pytest

_P = "/opt/data/plugins"
if _P not in sys.path:
    sys.path.insert(0, _P)

import hermes_router as plugin  # noqa: E402
from hermes_router import (anchor_exec, decision_midturn,  # noqa: E402
                           debug_banner as DB, router_core)

SID = "s-m45-26f-aliasing"
BRIEF = ("Unit M45-26F: run commands first. Fix the aliasing in "
         "gate/orchestration.py, commit to hermes_router main, push.")
FRAME = ("You are her own higher self — a few breaths earlier. Do NOT solve "
         "the task. Answer these orientation questions in max 6 bullets: "
         "what result shape does this ask require?")


@pytest.fixture()
def _seam(monkeypatch):
    """Minimal hub surface: a staged swap exists, no midturn advisories."""
    monkeypatch.setattr(plugin, "_log_route",
                        lambda seam, event_detail="", **kw: None, raising=True)
    monkeypatch.setattr(plugin, "_tap_provider_failure",
                        lambda *a, **k: None, raising=True)
    monkeypatch.setattr(router_core, "peek_pending_swap",
                        lambda sid: {"task_id": "t-26f", "route_id": "r-26f",
                                     "task": "brief", "mode": "consult",
                                     "orientation": True},
                        raising=True)
    monkeypatch.setattr(router_core, "record_anchor_backoff_failure",
                        lambda *a, **k: None, raising=True)
    monkeypatch.setattr(decision_midturn, "flush_and_scan",
                        lambda sid, req: [], raising=True)
    monkeypatch.setattr(DB, "debug_banner_enabled", lambda: False,
                        raising=True)
    monkeypatch.setattr(plugin.router_tools, "count", lambda *a, **k: None)
    monkeypatch.setattr(plugin.router_tools, "note_skip_reason",
                        lambda *a, **k: None)


def _mutating_failure(sid, api_kwargs):
    """Stand-in for a real anchor consult that FAILS after framing.

    Reproduces anchor_exec.py:1931 exactly: the last user message is replaced
    in place (same list object, same dict slot) and the routed payload is
    written back, then the consult returns no outcome.
    """
    msgs = api_kwargs.get("messages")
    for i in range(len(msgs) - 1, -1, -1):
        if isinstance(msgs[i], dict) and msgs[i].get("role") == "user":
            msgs[i] = {**msgs[i], "content": FRAME}
            break
    api_kwargs["messages"] = msgs
    return None


def _drive(monkeypatch, text=BRIEF):
    monkeypatch.setattr(anchor_exec, "maybe_execute_anchored",
                        _mutating_failure, raising=True)
    seen = {}

    def _next(req):
        seen["msgs"] = req.get("messages")
        return "FALLBACK"

    request = {"messages": [{"role": "user", "content": text}],
               "max_tokens": 512}
    out = plugin.on_llm_execution(request=request, next_call=_next,
                                  session_id=SID)
    return out, seen, request


def _last_user(msgs):
    for m in reversed(msgs or []):
        if isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


def test_failed_consult_does_not_rewrite_the_fallback_last_user(_seam,
                                                              monkeypatch):
    """THE defect: fail-open must hand back the brief, never the consult frame."""
    out, seen, _request = _drive(monkeypatch)
    assert out == "FALLBACK"                      # fail-open still fires
    last = _last_user(seen.get("msgs"))
    assert last == BRIEF, (
        "fallback received the anchor consult frame instead of the dispatched "
        "brief — the in-place write at anchor_exec.py:1931 reached the "
        "fallback payload.\nlast_user=%r" % (last[:90],))
    assert FRAME not in last


def test_caller_request_object_is_not_mutated_by_the_consult(_seam,
                                                            monkeypatch):
    """The live request must survive a consult attempt byte-for-byte."""
    _out, _seen, request = _drive(monkeypatch)
    assert request["messages"][-1]["content"] == BRIEF
    assert len(request["messages"]) == 1


def test_frame_reaches_the_consult_not_the_fallback(_seam, monkeypatch):
    """The consult STILL gets the frame — the fix must not defang the lane."""
    got = {}

    def _capture(sid, api_kwargs):
        got["last"] = _last_user(api_kwargs.get("messages"))
        return _mutating_failure(sid, api_kwargs)

    monkeypatch.setattr(anchor_exec, "maybe_execute_anchored", _capture,
                        raising=True)
    seen = {}
    request = {"messages": [{"role": "user", "content": BRIEF}]}
    plugin.on_llm_execution(request=request,
                            next_call=lambda req: seen.setdefault(
                                "msgs", req.get("messages")) or "FALLBACK",
                            session_id=SID)
    assert got["last"] == BRIEF     # frame is built INSIDE the real function;
    assert _last_user(seen["msgs"]) == BRIEF
