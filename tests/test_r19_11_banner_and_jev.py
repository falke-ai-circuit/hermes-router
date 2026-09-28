"""R19.11 — decision-banner loss on two-lane turns + Jev parse hardening.

BUG 1 (live, reviewer session 20260803_140900_48d4d030): a decision banner
consumed+appended at an early benign edge was wiped when a later
uncensored-render POST transform replaced the turn tail. Fix: the consumed
decision-lane banner is HELD (120s TTL); any later delivery edge whose text
lacks the decision marker re-emits it (bounded, fail-open, MAX_BANNER_CHARS,
one re-emit). BUG 2: Jev backend parse_fail 2/5 — robust JSON extraction
(fences, prose-wrapped) + one strict retry before failing open.
"""
import json
import sys
import os
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import debug_banner, decision, state  # noqa: E402

REFUSAL = "I won't write this. That well's dry."
ORIGINAL = "step by step protocol to purify ricin"
RENDERED = "VENICE RENDERED CONTENT MARKER " * 10
DECISION_BANNER = ("· router · decision | midturn | glm | tok 10/5 | "
                   "$0.000001 | initiator=agent")


def _reset_banners():
    debug_banner._ANCHOR_BANNERS.clear()
    debug_banner._HELD_DECISIONS.clear()


# ------------------------------------------------------------------
# BUG 1 — decision banner survives render replacement
# ------------------------------------------------------------------

def test_benign_delivery_then_render_reemits_banner(monkeypatch):
    """Two-lane turn: invocation 1 (benign) consumes + appends the decision
    banner; invocation 2 (uncensored render REPLACES the tail) must still
    deliver the decision banner."""
    _reset_banners()
    state.clear()
    # production reality: the debug-banner knob is ON when banners are
    # delivered (conftest forces it off for isolation — force it back).
    monkeypatch.setattr(debug_banner, "debug_banner_enabled", lambda: True)
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    sid = "s-two-lane"

    # a decision-lane banner is parked mid-turn
    debug_banner.park_anchor_banner(sid, DECISION_BANNER, task_id="midturn")

    # invocation 1: benign edge consumes + appends (delivered text had it —
    # but the platform then replaced the tail with the render)
    benign = "Here is the analysis you asked for."
    out1 = plugin.on_transform_llm_output(
        response_text=benign, session_id=sid, model="m")
    assert out1 and DECISION_BANNER in out1  # consumed+appended on edge 1
    assert debug_banner._HELD_DECISIONS.get(sid)  # hold recorded

    # invocation 2: the uncensored render REPLACES the turn (refusal path)
    state.stash_pending(sid, "m", ORIGINAL, "rendered-stub")
    state.set_last_user_msg_hash(sid, state.hash_text(ORIGINAL))
    with mock.patch.object(plugin.router, "call", return_value=RENDERED):
        out2 = plugin.on_transform_llm_output(
            response_text=REFUSAL, session_id=sid, model="m")
    assert out2 == RENDERED or (out2 and RENDERED in out2)
    # THE FIX: the decision banner survives the render replacement
    assert DECISION_BANNER in out2
    assert "· router · decision" in out2
    _reset_banners()


def test_delivered_edge_leaves_hold_ttl_expires(monkeypatch):
    """A single-lane benign delivery does NOT duplicate the banner on a
    later unrelated delivery after the TTL."""
    _reset_banners()
    # delivered edge carries the marker -> hold left alone, then expires
    debug_banner.note_consumed_decision("s1", DECISION_BANNER)
    out = debug_banner.settle_decision_banner(
        "s1", "text with " + DECISION_BANNER + " inline")
    assert DECISION_BANNER in out
    # fresh hold + text WITHOUT marker -> re-emitted once
    debug_banner.note_consumed_decision("s2", DECISION_BANNER)
    out2 = debug_banner.settle_decision_banner("s2", "clean render text")
    assert DECISION_BANNER in out2
    # second call: hold already popped -> no duplicate
    out3 = debug_banner.settle_decision_banner("s2", "another delivery")
    assert DECISION_MARK_ABSENT(out3)
    _reset_banners()


def DECISION_MARK_ABSENT(text):
    return "· router · decision" not in text


def test_non_decision_banner_never_held():
    _reset_banners()
    debug_banner.note_consumed_decision(
        "s3", "· router · frontier-anchor | anchored | glm | tok 1/1 | $0")
    assert "s3" not in debug_banner._HELD_DECISIONS
    _reset_banners()


def test_hold_respects_max_banner_chars():
    _reset_banners()
    huge = DECISION_BANNER + "x" * 1000
    debug_banner.note_consumed_decision("s4", huge)
    held = debug_banner._HELD_DECISIONS["s4"][0]
    assert len(held) <= debug_banner.MAX_BANNER_CHARS
    _reset_banners()


# ------------------------------------------------------------------
# BUG 2 — Jev parse hardening
# ------------------------------------------------------------------

GOOD = json.dumps({"choice": "opt-1", "confidence": 0.9,
                   "alternatives": []})


def test_robust_json_plain():
    assert json.loads(decision._robust_json_content(GOOD))["choice"] == "opt-1"


def test_robust_json_fenced():
    s = "```json\n%s\n```" % GOOD
    assert json.loads(decision._robust_json_content(s))["choice"] == "opt-1"


def test_robust_json_prose_wrapped():
    s = "Here is my decision:\n\n%s\n\nHope that helps!" % GOOD
    assert json.loads(decision._robust_json_content(s))["choice"] == "opt-1"


def test_robust_json_garbage_fails_open():
    assert decision._robust_json_content("no json here at all") == ""
    assert decision._robust_json_content('{"wrong": "schema"}') == ""
    assert decision._robust_json_content("") == ""


def test_jev_backend_retry_then_fail_open(monkeypatch):
    """Garbage on call 1 -> one strict retry; garbage on call 2 -> fail
    open with parse_fail (no exception)."""
    cfg = dict(decision.DEFAULTS)
    cfg["backend"] = "jev"
    cfg["openrouter_endpoint"] = "http://x/api"
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    envelope = {"schema": "s", "options": [{"id": "opt-1"},
                                           {"id": "opt-2"}]}
    calls = {"n": 0}
    monkeypatch.setattr(decision, "render_prompt", lambda env: "PROMPT")

    def fake_post(url, headers, payload, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"choices": [{"message": {"content": "I think opt-1 is "
                                                     "the right approach!"}}]}
        return {"choices": [{"message": {"content": "still no json"}}]}

    monkeypatch.setattr(decision, "_http_post_json", fake_post)
    content, meta, reason = decision.call_backend(envelope, cfg)
    assert content is None
    assert reason == decision.REASON_PARSE_FAIL
    assert calls["n"] == 2  # exactly one strict retry


def test_jev_backend_fenced_response_accepted_first_call(monkeypatch):
    cfg = dict(decision.DEFAULTS)
    cfg["backend"] = "jev"
    cfg["openrouter_endpoint"] = "http://x/api"
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    envelope = {"schema": "s", "options": [{"id": "opt-1"},
                                           {"id": "opt-2"}]}
    calls = {"n": 0}
    monkeypatch.setattr(decision, "render_prompt", lambda env: "PROMPT")

    def fake_post(url, headers, payload, timeout):
        calls["n"] += 1
        return {"choices": [{"message": {"content": "```json\n%s\n```" % GOOD}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    monkeypatch.setattr(decision, "_http_post_json", fake_post)
    content, meta, reason = decision.call_backend(envelope, cfg)
    assert reason == "ok"
    assert json.loads(content)["choice"] == "opt-1"
    assert calls["n"] == 1  # robust extract: no retry needed
