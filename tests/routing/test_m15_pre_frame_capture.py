"""M15 — the PRE seam must JOURNAL what the agent actually saw.

Conductor audit (2026-10-31): `stage=PRE` rows stopped in three profiles
(orchestrator/coder 2026-10-28, valmet 2026-10-15) while the PRE lane kept
firing and consuming. Root cause: capture lived ONLY inside
`_deliver_render_pass` (the uncensored render swap), and the frontier PRE
render trigger was retired in c82a33d (2026-10-28). Every request-surface
frame that replaced it — the every-call reflex reading, the parked reflex
reading delivered at the next call, the uncensored-lane assimilation frame —
entered the request with no journal row at all. Check-2 of the audit could
then only count log events, never grade frame text.

Pinned here:
  a) each PRE producer journals a stage=PRE row carrying the frame text;
  b) the row keeps the POST row shape (same six keys) plus an additive
     `seam` tag, so the format is consistent, not a second schema;
  c) a journal failure is logged and swallowed — never raises into the
     delivery path.

All provider calls are mocked. No network, no live profile writes (conftest
points the inbox at tmp_path).
"""
from __future__ import annotations

import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from hermes_router import assimilation  # noqa: E402
from hermes_router import reflex_lane as rl  # noqa: E402
from hermes_router import render_inbox as ri  # noqa: E402

ON = {"hermes_router": {"reflex_weights": {"enabled": True}}}

BODY = ("Traceback (most recent call last):\n  File \"/opt/data/app/src/render.py\", line 12\n"
        "ImportError: cannot import name 'cache' from /opt/data/app/src/cache.py\n"
        "while building the template cache for the report page")


def _dist(*ps):
    return {"answers": {"action": {"probabilities": {("a%d" % (i + 1)): p
                                                    for i, p in enumerate(ps)}},
                        "scope": {"score": 1}, "rigour": {"score": 4}}}


def _pre_rows(session_id=None):
    return [r for r in ri.read_renders(limit=200)
            if r.get("stage") == "PRE"
            and (session_id is None or r.get("session_id") == session_id)]


@pytest.fixture(autouse=True)
def _clean_lane():
    rl.clear()
    assimilation.clear_marks()
    yield
    rl.clear()
    assimilation.clear_marks()


def test_every_call_reflex_frame_is_journaled(monkeypatch):
    """The reflex/valmet PRE seam (be6cb62) must leave a PRE row."""
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    req = {"messages": [{"role": "user", "content": "fix the renderer"},
                        {"role": "tool", "content": BODY}]}

    assert rl.run_on_request(req, session_id="m15-every", cfg=ON) is True

    rows = _pre_rows("m15-every")
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["seam"] == "reflex_every_call"
    assert row["render"], "the row must carry the FRAME TEXT, not a counter"
    assert "80%" in row["render"]
    assert "reading" in row["render"].lower()
    assert row["render_chars"] == len(row["render"])
    assert row["trigger_chars"] == len("fix the renderer")


def test_parked_reflex_frame_is_journaled_at_delivery(monkeypatch):
    """A reading parked midturn is delivered on the NEXT call — journal there."""
    rl.clear("m15-parked")
    rl._park("m15-parked", "block body")
    req = {"messages": [{"role": "user", "content": "hi"}]}

    assert rl.inject_pass(req, "m15-parked") is True

    rows = _pre_rows("m15-parked")
    assert len(rows) == 1, rows
    assert rows[0]["seam"] == "reflex_parked"
    assert "block body" in rows[0]["render"]


def test_assimilation_frame_is_journaled(monkeypatch):
    """The uncensored-lane frame injected at PRE is a PRE render."""
    monkeypatch.setattr(assimilation, "inject_platform_rule", lambda *a, **k: True)
    monkeypatch.setattr(assimilation, "_log", lambda *a, **k: None)
    assimilation.mark_turn_rendered("m15-assim")

    assert assimilation.inject_frame({"messages": []}, "m15-assim") is True

    rows = _pre_rows("m15-assim")
    assert len(rows) == 1, rows
    assert rows[0]["seam"] == "assimilation_frame"
    assert rows[0]["render"] == assimilation.frame_text()


def test_pre_row_shape_is_the_post_shape_plus_seam():
    """One journal format: same six keys as POST rows, `seam` additive."""
    ri.record_render("POST", "m15-shape", 10, "post body")
    ri.record_pre_frame("m15-shape", 10, "pre body", "reflex_every_call")

    post = [r for r in ri.read_renders(limit=50)
            if r.get("stage") == "POST" and r.get("session_id") == "m15-shape"][0]
    pre = [r for r in ri.read_renders(limit=50)
           if r.get("stage") == "PRE" and r.get("session_id") == "m15-shape"][0]

    base = {"ts", "stage", "session_id", "trigger_chars", "render_chars", "render"}
    assert set(post) == base
    assert set(pre) == base | {"seam"}
    assert pre["render"] == "pre body"


def test_journal_failure_never_raises_into_delivery(monkeypatch):
    """Disk failure on the journal must not cost the frame or the turn."""
    def _boom():
        raise OSError("inbox unavailable")

    monkeypatch.setattr(ri, "_inbox_path", _boom)
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))

    assert ri.record_pre_frame("m15-fail", 0, "frame", "reflex_every_call") is False
    req = {"messages": [{"role": "user", "content": "fix the renderer"},
                        {"role": "tool", "content": BODY}]}
    assert rl.run_on_request(req, session_id="m15-fail-run", cfg=ON) is True
    assert "80%" in req["messages"][-1]["content"], "the reading must still land"

    monkeypatch.setattr(assimilation, "inject_platform_rule", lambda *a, **k: True)
    monkeypatch.setattr(assimilation, "_log", lambda *a, **k: None)
    assimilation.mark_turn_rendered("m15-fail-assim")
    assert assimilation.inject_frame({"messages": []}, "m15-fail-assim") is True
