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

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
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


GOOD = json.dumps({"choice": "opt-1", "confidence": 0.9,
                   "alternatives": []})


# ------------------------------------------------------------------
# BUG 1 — decision banner survives render replacement
# ------------------------------------------------------------------

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
    # R63L: routes carry labels. An envelope with ids only offers no route text,
    # so the consult floor refuses it before a backend is ever called.
    envelope = {"schema": "s", "options": [{"id": "opt-1", "label": "redis"},
                                          {"id": "opt-2", "label": "memcached"}]}
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
    # R63L: routes carry labels. An envelope with ids only offers no route text,
    # so the consult floor refuses it before a backend is ever called.
    envelope = {"schema": "s", "options": [{"id": "opt-1", "label": "redis"},
                                          {"id": "opt-2", "label": "memcached"}]}
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
