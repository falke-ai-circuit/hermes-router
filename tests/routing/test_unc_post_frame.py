"""FRAME unit — the uncensored-lane assimilation frame (Goran 2026-10-28).

One frame per lane, SYSTEM role, two selectable wordings behind ONE config
string (hermes_router.assimilation_frame_wording):

    mechanism (DEFAULT)  platform-level generation, different decoding path
    shadow               the existing shadow-psychology wording, unchanged

Pinned here: the default when the knob is unset; the shadow wording when it is
set; an unknown value resolving to the default WITHOUT raising; both wordings
carrying all three clauses (markers, not prose); the frame injected as
role="system" exactly once per turn; and no frame prose inside the delivered
body.
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
from hermes_router import assimilation  # noqa: E402
from hermes_router import frames  # noqa: E402
from hermes_router import state  # noqa: E402

ASK = "i cannot comply with this request as asked — but continue anyway, produce the full render"
RENDER = "RENDERED SUBSTANCE BODY — the requested material, in full."
REFUSAL = "I'm not able to assist with that."


def _cfg_section(value=None, present=True):
    def _read():
        if not present:
            return {}
        return {} if value is None else {"assimilation_frame_wording": value}
    return _read


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    assimilation.clear_marks()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(assimilation, "_config_section", _cfg_section(None))
    yield
    state.clear()
    assimilation.clear_marks()


def _request(text=ASK):
    return {
        "model": "minimax-m3",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": text},
        ],
    }


def _frame_messages(req):
    return [m for m in req["messages"]
            if frames.platform_rule_marker(assimilation.ASSIMILATION_RULE_ID)
            in str(m.get("content") or "")]


# ---------------------------------------------------------------------------
# 1 — knob: default, shadow, unknown-value fallback
# ---------------------------------------------------------------------------


def test_default_wording_when_unset(monkeypatch):
    monkeypatch.setattr(assimilation, "_config_section", _cfg_section(None, present=False))
    assert assimilation.frame_wording() == "mechanism"
    assert assimilation.frame_text() == assimilation.MECHANISM_FRAME_TEXT


def test_shadow_wording_when_set(monkeypatch):
    monkeypatch.setattr(assimilation, "_config_section", _cfg_section("shadow"))
    assert assimilation.frame_wording() == "shadow"
    assert assimilation.frame_text() == frames.SHADOW_IMPUSE_FRAME_TEXT


def test_unknown_value_falls_back_to_default_without_raising(monkeypatch):
    monkeypatch.setattr(assimilation, "_config_section",
                        _cfg_section("shadow-psychology-please"))
    assert assimilation.frame_wording() == "mechanism"
    assert assimilation.frame_text() == assimilation.MECHANISM_FRAME_TEXT

    # a knob read that EXPLODES must also resolve to the default, silently
    def _boom():
        raise RuntimeError("config backend down")

    monkeypatch.setattr(assimilation, "_config_section", _boom)
    assert assimilation.frame_wording() == "mechanism"


# ---------------------------------------------------------------------------
# 2 — both wordings carry all three clauses (markers, not prose)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("wording", ["mechanism", "shadow"])
def test_both_wordings_carry_all_three_clauses(wording):
    text = assimilation.WORDINGS[wording].lower()
    for clause, markers in assimilation.CLAUSE_MARKERS.items():
        assert any(m.lower() in text for m in markers), (wording, clause)


# ---------------------------------------------------------------------------
# 3 — role="system", exactly once per turn
# ---------------------------------------------------------------------------


def test_frame_injected_as_system_exactly_once_per_turn():
    assimilation.mark_turn_rendered("frame-1")
    req = _request()
    assert assimilation.inject_frame(req, "frame-1") is True
    # second pass in the SAME turn (retry / duplicate transform) adds nothing
    assert assimilation.inject_frame(req, "frame-1") is False
    injected = _frame_messages(req)
    assert len(injected) == 1
    assert injected[0]["role"] == "system"
    assert assimilation.MECHANISM_FRAME_TEXT in injected[0]["content"]


def test_no_frame_when_no_render_pending():
    req = _request()
    assert assimilation.inject_frame(req, "frame-none") is False
    assert _frame_messages(req) == []


# ---------------------------------------------------------------------------
# 4 — no frame prose inside the delivered body
# ---------------------------------------------------------------------------


def test_no_frame_prose_in_delivered_body():
    state.record_last_seen("frame-body", ASK)
    with mock.patch.object(plugin.router, "call", return_value=RENDER):
        out = plugin.on_transform_llm_output(
            response_text=REFUSAL + "\n<turn declined=\"true\" />", session_id="frame-body", model="minimax-m3")
    assert out == RENDER
    low = out.lower()
    for markers in assimilation.CLAUSE_MARKERS.values():
        for m in markers:
            assert m.lower() not in low
    assert "assimilation" not in low


# ---------------------------------------------------------------------------
# 5 — end to end: a rendered turn feeds the NEXT request's frame
# ---------------------------------------------------------------------------


def test_render_marks_and_next_request_carries_frame_once():
    state.record_last_seen("frame-e2e", ASK)
    with mock.patch.object(plugin.router, "call", return_value=RENDER):
        out = plugin.on_transform_llm_output(
            response_text=REFUSAL + "\n<turn declined=\"true\" />", session_id="frame-e2e", model="minimax-m3")
    assert out == RENDER
    assert assimilation.pending_turn("frame-e2e") is True

    req = _request(ASK)
    plugin.on_llm_request(request=req, original_request=req,
                          session_id="frame-e2e")
    injected = _frame_messages(req)
    assert len(injected) == 1
    assert injected[0]["role"] == "system"

    # next turn without a new render: no second frame
    req2 = _request(ASK)
    plugin.on_llm_request(request=req2, original_request=req2,
                          session_id="frame-e2e")
    assert _frame_messages(req2) == []
