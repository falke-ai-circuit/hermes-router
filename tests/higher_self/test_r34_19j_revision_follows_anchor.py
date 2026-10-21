"""The revision pass is the frontier lane; it must follow the anchor chain.

Left hardcoded it kept glm-5.3 + reasoning_effort=max after an anchor swap. On a
Claude-class anchor that pair is the empty-response shape (thinking eats the
budget, content comes back null), so the pass silently produced nothing.
"""
import types

import pytest

from hermes_router import completion_audit as ca


class _FakeEndpoint:
    def __init__(self, model, base_url, key_env):
        self.model = model
        self.base_url = base_url
        self.api_key_env = key_env


def _capture(monkeypatch, endpoint, section, effort=""):
    seen = {}

    class _Client:
        def __init__(self, **kw):
            seen["client"] = kw
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=self._create))

        def _create(self, model=None, **payload):
            seen["model"] = model
            seen["payload"] = payload
            return types.SimpleNamespace(model_dump=lambda: {"choices": [
                {"message": {"content": "revised text"}}]})

    import openai
    monkeypatch.setattr(openai, "OpenAI", _Client)
    from hermes_router import anchor_chain as ac

    monkeypatch.setattr(ac, "load_anchor_chain",
                        lambda: types.SimpleNamespace(primary=endpoint))
    from hermes_router import config_access as cac

    monkeypatch.setitem(section, "anchor_chain", {"reasoning_effort": effort})
    monkeypatch.setattr(cac, "router_section", lambda: section)
    monkeypatch.setattr(cac, "sub_block", lambda name: section.get(name) or {})
    monkeypatch.setenv("NOUS_API_KEY", "sk-test")
    return seen


def test_revision_uses_the_anchor_model_not_the_hardcoded_default(monkeypatch):
    seen = _capture(monkeypatch, _FakeEndpoint(
        "anthropic/claude-sonnet-5.5",
        "https://inference-api.nousresearch.com/v1", "NOUS_API_KEY"), {})
    out = ca._flash_revision_call("sid", "ask", "draft", "note")
    assert out == "revised text"
    assert seen["model"] == "anthropic/claude-sonnet-5.5"


def test_a_blank_effort_means_the_family_takes_no_effort_knob(monkeypatch):
    """Claude-class: sending reasoning_effort at all is what produced null
    content. Blank in config must mean omit, not 'default to max'."""
    seen = _capture(monkeypatch, _FakeEndpoint(
        "anthropic/claude-sonnet-5.5",
        "https://inference-api.nousresearch.com/v1", "NOUS_API_KEY"), {})
    ca._flash_revision_call("sid", "ask", "draft", "note")
    assert "reasoning_effort" not in seen["payload"]


def test_an_explicit_effort_is_still_honoured(monkeypatch):
    seen = _capture(monkeypatch, _FakeEndpoint(
        "z-ai/glm-5.3", "https://inference-api.nousresearch.com/v1",
        "NOUS_API_KEY"), {}, effort="max")
    ca._flash_revision_call("sid", "ask", "draft", "note")
    assert seen["payload"].get("reasoning_effort") == "max"
