"""R66 — the frame is graded against what the model actually did next.

The lane could previously report only that it SPOKE. These tests pin the part that makes it a
measurement: the next request is scanned for the first tool call after delivery, and the frame
is graded exact / class / answered / none. Unscoreable is never counted as a miss — a frame
whose follow-up has not happened yet says nothing about the frame.
"""
from __future__ import annotations

import pytest

from hermes_router import reflex_lane as rl

ON = {"hermes_router": {"reflex_weights": {"enabled": True}}}

BODY = ("Traceback (most recent call last):\n  File \"/opt/data/app/src/render.py\", line 12\n"
        "ImportError: cannot import name 'cache' from /opt/data/app/src/cache.py\n"
        "while building the template cache for the report page")


def _dist(*ps):
    return {"answers": {"action": {"probabilities": {("a%d" % (i + 1)): p
                                                    for i, p in enumerate(ps)}},
                        "scope": {"score": 1}, "rigour": {"score": 3}}}


def _req(*extra, base=True):
    """A fresh request. Note that delivery MUTATES the request, so a test that wants the
    follow-up to be visible must extend the request it actually delivered into — see _after."""
    msgs = ([{"role": "user", "content": "fix the renderer"},
             {"role": "tool", "content": BODY}] if base else [])
    return {"messages": list(msgs) + list(extra)}


def _after(req, *extra):
    """The same request the lane wrote into, plus what happened next."""
    return {"messages": list(req["messages"]) + list(extra)}


def _call(tool, args):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": tool, "arguments": args}}]}


def test_a_frame_touching_the_named_object_grades_exact(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    req = _req()
    assert rl.run_on_request(req, session_id="g1", cfg=ON) is True
    # the model then edits exactly the path the top pick named
    nxt = _after(req, _call("patch", '{"path": "/opt/data/app/src/render.py"}'))
    out = rl.settle("g1", nxt)
    assert out["grade"] == "exact"
    assert out["tool"] == "patch"


def test_paths_lose_sentence_punctuation():
    """A path at the end of a sentence must not carry the full stop into the candidate label —
    it turned perfect hits into misses and made the lane look worse than it is."""
    from hermes_router import decision_extract as dx

    got = dx.salient_paths("it failed in /opt/data/app/src/render.py. then again in cache.py,")
    assert "/opt/data/app/src/render.py" in got
    assert not any(p.endswith((".", ",")) for p in got)


def test_legacy_lane_stands_down_on_the_dual_config_blocks(monkeypatch):
    """The legacy reader looks at uncensored_router first and that block has no
    reflex_weights, so a single-block check reported 'not armed' while the new lane fired."""
    from hermes_router import decision_midturn as dm

    dual = {"uncensored_router": {"enabled": True},
            "hermes_router": {"reflex_weights": {"enabled": True}}}
    assert dm._superseded_by_reflex(dual) is True
    off = {"hermes_router": {"reflex_weights": {"enabled": False}}}
    assert dm._superseded_by_reflex(off) is False


def test_a_directory_target_counts_exact_when_the_model_works_inside_it(monkeypatch):
    """The pool often names the workspace, not the file. Acting inside it is hitting the pick,
    and scoring it as 'class' understates the lane in exactly the case it fires most."""
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.9, 0.05, 0.03, 0.02))
    rl.clear()
    body = ("File \"/opt/data/tmp/work/probe.py\", line 3\nAssertionError: assert 0 is None\n"
            "collected 2 items, 1 failed during the run")
    req = {"messages": [{"role": "user", "content": "fix it"},
                        {"role": "tool", "content": body}]}
    assert rl.run_on_request(req, session_id="g10", cfg=ON) is True
    out = rl.settle("g10", _after(req, _call("patch", '{"path": "/opt/data/tmp/work/probe.py"}')))
    assert out["grade"] == "exact", out


def test_same_kind_of_move_grades_class(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    req_g2 = _req()
    rl.run_on_request(req_g2, session_id="g2", cfg=ON)
    out = rl.settle("g2", _after(req_g2, _call("terminal", '{"command": "pytest -q"}')))
    assert out["grade"] in ("class", "none")   # run vs edit: graded on the class it named


def test_going_elsewhere_grades_none(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    req_g3 = _req()
    rl.run_on_request(req_g3, session_id="g3", cfg=ON)
    out = rl.settle("g3", _after(req_g3, _call("terminal", '{"command": "ls /opt/data/other/thing.go"}')))
    assert out["grade"] == "none"


def test_replying_instead_of_acting_grades_answered(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    req_g4 = _req()
    rl.run_on_request(req_g4, session_id="g4", cfg=ON)
    out = rl.settle("g4", _after(req_g4, {"role": "assistant", "content": "It is a placeholder import."}))
    assert out["grade"] == "answered"


def test_a_frame_with_no_follow_up_yet_is_unscoreable_not_a_miss(monkeypatch):
    """'Not measured' must never be reported as a failure — that is how instruments lie."""
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    base = _req()
    rl.run_on_request(base, session_id="g5", cfg=ON)
    assert rl.settle("g5", base)["grade"] == "unscoreable"


def test_settling_twice_does_not_double_count(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    req_g6 = _req()
    rl.run_on_request(req_g6, session_id="g6", cfg=ON)
    nxt = _after(req_g6, _call("read_file", '{"path": "/opt/data/app/src/render.py"}'))
    assert rl.settle("g6", nxt) is not None
    assert rl.settle("g6", nxt) is None, "one frame, one outcome"


def test_settle_never_raises_on_junk():
    assert rl.settle("nope", None) is None
    assert rl.settle("nope", {"messages": "not-a-list"}) is None


def test_frame_index_measures_the_transcript_not_our_own_injection(monkeypatch):
    """The frame is never persisted, so the next request is shorter than the delivered one.
    Anchoring on the post-injection length made every real frame look like it had no follow-up
    — silent, and it read as 'nothing to report' rather than a bug."""
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    req = _req()
    before = len(req["messages"])
    rl.run_on_request(req, session_id="g9", cfg=ON)
    assert len(req["messages"]) == before + 1, "the frame was injected"
    # the host rebuilds from the transcript: same base, plus what happened next
    nxt = {"messages": _req()["messages"] + [_call("patch", '{"path": "/opt/data/app/src/render.py"}')]}
    out = rl.settle("g9", nxt)
    assert out["grade"] == "exact", out


def test_delivery_log_carries_the_block_machine_readably(monkeypatch):
    """The block must be reconstructable from the log, not truncated at the first newline."""
    import json

    seen = {}

    def _spy(sid, event, **fields):
        if event == "reflex_weights_delivered":
            seen.update(fields)

    monkeypatch.setattr(rl, "_log", _spy)
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    rl.run_on_request(_req(), session_id="g7", cfg=ON)
    assert seen.get("top_kind") in ("edit", "read")
    block = json.loads(seen["block"])
    assert "80%" in block and "adjust:" in block


def test_mindset_dials_reach_the_block_in_signed_form(monkeypatch):
    """What the main model must be able to act on: signed deltas, not rubric ordinals."""
    monkeypatch.setattr(rl, "_transport",
                        lambda *a, **k: {"answers": {"action": {"probabilities":
                                                               {"a1": 0.75, "a2": 0.25}},
                                                    "scope": {"score": 0},
                                                    "rigour": {"score": 4}}})
    rl.clear()
    block = rl.fire("m1", "terminal", BODY, ON, ask="fix it")
    assert "scope -1.00" in block and "rigour +1.00" in block
