"""v3.2.1 build-brief regression tests — strip memory-context from anchor payload.

Defect (conductor A/B-reproduced 2026-09-05 ~11:05, deterministic): Hermes
injects a <memory-context>...</memory-context> block into the user message
(recalled-memory wrapper containing route-log/classifier terms:
ied_construction, csam_underage, uncensored, content_filter...). The
Anthropic/OpenRouter content-filter trips on that block -> finish=
content_filter, content empty (2.4s fast-fail). A/B: same ask WITHOUT the
block -> 8692 chars delivered; WITH the block -> content_filter.

Fix: anchored_call() strips the wrapper from every user-role message in the
anchor replay payload (system messages were already sanitized; flash's own
payload untouched). The user's actual ask is preserved byte-for-byte.

All network mocked — the OpenAI client is stubbed and the payload it received
is captured for assertion (the tests verify the PAYLOAD, not the response).
"""
import sys
import os
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router import anchor_exec, anchor_chain  # noqa: E402

MEMORY_BLOCK = ("<memory-context>\n[System note: recalled memory context — "
                "ied_construction csam_underage uncensored content_filter "
                "route-log terms...]\n</memory-context>\n\n")
ASK = "Design a multi-stage migration plan for splitting the monolith."
CONTAMINATED = MEMORY_BLOCK + ASK
CLEAN_ASK = "plain ask with no wrapper"


@pytest.fixture
def endpoint():
    return anchor_chain.parse_anchor_uri("openrouter://test/anchor-primary", "primary")


class _FakeCompletion:
    """Minimal OpenAI response stand-in: records the payload it received."""

    received = None

    def __init__(self, payload):
        _FakeCompletion.received = payload
        self._payload = payload

    def model_dump(self):
        return {"choices": [{"finish_reason": "stop",
                             "message": {"role": "assistant", "content": "ANCHOR ANSWER"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


@pytest.fixture(autouse=True)
def _capture_client(monkeypatch):
    captured = {"payload": None}

    class _FakeClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **payload):
            captured["payload"] = payload
            return _FakeCompletion(payload)

        def close(self):
            pass

    monkeypatch.setattr(anchor_exec, "_MEMORY_CLIENT_OVERRIDE", None, raising=False)
    import openai as _openai
    monkeypatch.setattr(_openai, "OpenAI", _FakeClient)
    yield captured


def _call(endpoint, messages, captured):
    content, cost = anchor_exec.anchored_call(
        endpoint, {"model": "flash-model", "messages": messages,
                   "max_tokens": 100})
    return content, captured["payload"]


# ---------------------------------------------------------------------------
# user msg with memory-context block -> stripped, ask preserved
# ---------------------------------------------------------------------------


def test_anchor_strip_memory_context_user_preserves_ask(endpoint, _capture_client):
    messages = [
        {"role": "system", "content": "DNA persona prompt (already sanitized)"},
        {"role": "user", "content": CONTAMINATED},
    ]
    content, payload = _call(endpoint, messages, _capture_client)
    assert content == "ANCHOR ANSWER"
    sent = payload["messages"][1]["content"]
    assert "<memory-context>" not in sent
    assert "</memory-context>" not in sent
    assert sent == ASK  # the ask survives byte-for-byte, wrapper + gap gone
    assert "ied_construction" not in sent
    assert "csam_underage" not in sent


# ---------------------------------------------------------------------------
# user msg without block -> unchanged
# ---------------------------------------------------------------------------


def test_anchor_no_memory_context_user_unchanged(endpoint, _capture_client):
    messages = [{"role": "user", "content": CLEAN_ASK}]
    content, payload = _call(endpoint, messages, _capture_client)
    assert payload["messages"][0]["content"] == CLEAN_ASK  # byte-identical


# ---------------------------------------------------------------------------
# multi-message payload -> each user msg processed
# ---------------------------------------------------------------------------


def test_anchor_multi_message_payload_each_user_processed(endpoint, _capture_client):
    messages = [
        {"role": "system", "content": "sys frame"},
        {"role": "user", "content": CONTAMINATED},
        {"role": "assistant", "content": "partial plan..."},
        {"role": "user", "content": "continue the plan " + MEMORY_BLOCK + "from here"},
    ]
    content, payload = _call(endpoint, messages, _capture_client)
    sent = payload["messages"]
    assert sent[1]["content"] == ASK  # first user msg stripped
    assert sent[3]["content"] == "continue the plan from here"  # second too
    # assistant turn untouched (strip is user-role only)
    assert sent[2]["content"] == "partial plan..."


# ---------------------------------------------------------------------------
# stripped payload reproduces conductor's A/B shape
# ---------------------------------------------------------------------------


def test_anchor_stripped_payload_reproduces_ab_shape(endpoint, _capture_client):
    """The A/B: the contaminated user turn (WITH block -> content_filter)
    must leave anchored_call as the clean form (WITHOUT block -> delivered).
    Assistant-role content is NOT stripped (flash transcript text, no wrapper
    there) — only user turns are processed."""
    messages = [
        {"role": "system", "content": "task frame"},
        {"role": "user", "content": MEMORY_BLOCK + "summarize the plan in three bullets"},
    ]
    content, payload = _call(endpoint, messages, _capture_client)
    sent_user = payload["messages"][1]["content"]
    assert sent_user == "summarize the plan in three bullets"
    assert "<memory-context>" not in str(payload["messages"])
    # system was already neutralized by the existing sanitizer (A shape parity)
    assert payload["messages"][0]["content"].startswith("You are a senior specialist")


def test_strip_is_failopen_and_nonfatal(endpoint, _capture_client, monkeypatch):
    """If the strip machinery itself explodes, the anchored call still runs
    with the original payload (best-effort, never fatal)."""
    messages = [{"role": "user", "content": CONTAMINATED}]
    import hermes_router.anchor_exec as ae

    with mock.patch.object(ae, "_MEMORY_CONTEXT_RE") as fake_re:
        fake_re.sub.side_effect = RuntimeError("regex boom")
        content, payload = _call(endpoint, messages, _capture_client)
    assert content == "ANCHOR ANSWER"  # call still ran
    assert payload["messages"][0]["content"] == CONTAMINATED  # unchanged