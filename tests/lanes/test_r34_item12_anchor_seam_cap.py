"""R34-12 item 8 — the bounded advisory cap at the ANCHOR INJECTION SEAM.

Live: `anchor_chars=4033` in the EXECUTION frame at L1 (session
api_1792536611_5dbebc6b). The 8d cap lived only inside
anchor_exec.maybe_execute_anchored and only fired when its execution-directive
detector matched — the dispatch brief did not, so the oversized envelope rode
into the turn and the turn answered with it.

The seam test below drives the REAL on_llm_execution middleware with a staged
swap and asserts on the messages handed to next_call (the injected string),
not on a renderer's return value.
"""
import os
import sys

import pytest

_P = "/opt/data/plugins"
if _P not in sys.path:
    sys.path.insert(0, _P)

import hermes_router as plugin  # noqa: E402
from hermes_router import (anchor_exec, decision_midturn,  # noqa: E402
                           debug_banner as DB, router_core)

SID = "s-r34-12-cap"
BIG = "ORIENTATION head " + ("detail " * 700) + "TAILSENTINEL"


@pytest.fixture()
def _sink(monkeypatch):
    rows = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda seam, event_detail="", **kw: rows.append(
                            (seam, event_detail, dict(kw))), raising=True)
    monkeypatch.setattr(DB, "_banner_section", lambda: {"debug_banner": 1},
                        raising=True)
    monkeypatch.setattr(DB, "park_anchor_banner", lambda *a, **k: None,
                        raising=True)
    monkeypatch.setattr(router_core, "peek_pending_swap",
                        lambda sid: {"task_id": "t-12", "route_id": "r-12",
                                     "mode": "consult", "endpoint": None},
                        raising=True)
    monkeypatch.setattr(router_core, "clear_anchor_backoff",
                        lambda *a, **k: None, raising=True)
    monkeypatch.setattr(decision_midturn, "flush_and_scan",
                        lambda sid, req: [], raising=True)
    return rows


def _drive(monkeypatch, answer, text="R34-12 dispatch: implement item 8 and commit."):
    env = {"kind": "orientation", "answer": answer, "producer": "p",
           "route_id": "r-12", "limitations": "", "model": "m", "cost": 0.0}
    monkeypatch.setattr(anchor_exec, "maybe_execute_anchored",
                        lambda sid, req: ("done", dict(env)), raising=True)
    seen = {}

    def _next(req):
        seen["msgs"] = req.get("messages")
        return "RESULT"

    out = plugin.on_llm_execution(
        request={"messages": [{"role": "user", "content": text}]},
        next_call=_next, session_id=SID)
    return out, seen


def test_anchor_injection_seam_caps_a_4033_char_advisory(_sink, monkeypatch):
    out, seen = _drive(monkeypatch, BIG)
    assert out == "RESULT"
    injected = "".join(str(m.get("content") or "")
                       for m in (seen.get("msgs") or [])
                       if isinstance(m, dict))
    assert "TAILSENTINEL" not in injected           # oversized body cannot ride
    assert "ORIENTATION head" in injected           # bounded head is present

    cap = [r for r in _sink if r[1] == "anchor_advisory_capped"]
    assert cap, [r[1] for r in _sink]
    assert cap[0][2].get("from") == len(BIG)
    assert cap[0][2].get("to") == 600
    assert cap[0][2].get("session_id") == SID

    fired = [r for r in _sink if r[1] == "anchor_route_fired"]
    assert fired
    assert fired[0][2].get("anchor_chars") == 600  # anchor_chars=4033 impossible


def test_small_advisory_is_not_capped(_sink, monkeypatch):
    small = "short orientation"
    out, seen = _drive(monkeypatch, small)
    assert out == "RESULT"
    assert not [r for r in _sink if r[1] == "anchor_advisory_capped"]
    fired = [r for r in _sink if r[1] == "anchor_route_fired"]
    assert fired and fired[0][2].get("anchor_chars") == len(small)
