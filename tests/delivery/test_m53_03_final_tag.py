"""Owner defect: every message was tagged, not just the final answer.

Two causes, both pinned here:
  A  the seam strips the tag from its LOCAL copy, so the delivery decision sees a
     clean body, reports strip_bytes=0, and takes the None pass-through — the
     host then ships its own tag-bearing original.
  B  the contract text never scoped the tag to the FINAL message.
"""
from __future__ import annotations

import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import frames, state  # noqa: E402
from hermes_router.gate import registration as reg  # noqa: E402
from hermes_router.passes import dispatcher_pre  # noqa: E402

BODY = "Here is the diff you asked for: the parser skips blank lines."
TAGGED = '<turn declined="false" />\n\n' + BODY


def _cfg():
    return {"enabled": True,
            "classification": {"pre_classify": True, "post_classify": True,
                               "match_threshold": 1},
            "log_routes": False}


@pytest.fixture()
def rows(monkeypatch):
    captured = []

    def _cap(event, **f):
        captured.append((event, f))

    state.clear(); plugin._reset_declaration_state(); frames.reset_rule_latch()
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg())
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(plugin, "_log_route", _cap)
    yield captured
    state.clear(); plugin._reset_declaration_state(); frames.reset_rule_latch()


def _run(sid, body):
    state.record_last_seen(sid, "show me the diff")
    state.set_last_user_msg_hash(sid, state.hash_text("show me the diff"))
    return reg.on_transform_llm_output_guarded(
        response_text=body, session_id=sid, model="m", turn_id="t-" + sid)


def test_t_t1_stripped_upstream_returns_explicit_stripped_bytes(rows):
    """THE PRODUCTION PATH: the upstream strip site notes the count, then a
    CLEAN body is handed onward. Feeding TAGGED straight to the seam would let
    the SEAM do the strip and prove nothing about the hand-off.
    """
    sid = "m53-03-tt1"
    removed = len(TAGGED) - len(BODY)
    assert removed > 0

    from hermes_router.gate import orchestration as _orch

    assert hasattr(_orch, "_note_decl_strip"), \
        "no upstream strip hand-off exists (this is the defect)"
    _orch._note_decl_strip(sid, removed)      # exactly what the strip site does

    out = _run(sid, BODY)                     # CLEAN body, not TAGGED

    assert isinstance(out, str), "a stripped turn must not pass through (None)"
    assert "<turn" not in out, "the delivered bytes must carry no tag"
    assert BODY in out


def test_t_t2_nothing_stripped_still_passes_through(rows):
    out = _run("m53-03-tt2", BODY)
    assert out is None, "the honest pass-through must be preserved"


def test_t_t3_contract_scopes_the_tag_to_the_final_message():
    text = " ".join(dispatcher_pre.declaration_contract_text().lower().split())
    assert "final" in text and "one declaration tag per turn" in text
    assert "progress" in text and "do not emit a tag" in text
