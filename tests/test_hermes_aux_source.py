"""Hermes-only aux lane (2026-09-08, Goran-direct: "we dont need legacy we
need to use whatever aux uses hermes thats it"). The plugin resolves aux
calls through agent.auxiliary_client — the profile's own `auxiliary:` config
is the single source of truth. No legacy aux_endpoint curl seam exists."""
import json
from unittest import mock

import pytest

from hermes_router import semantic_classifier as sc


def test_no_aux_source_knob_and_no_legacy_defaults():
    """The legacy seam is gone: no aux_source knob, no MiniMax defaults."""
    assert not hasattr(sc, "_aux_source")
    assert not hasattr(sc, "DEFAULT_URL")
    assert not hasattr(sc, "DEFAULT_KEY_ENV")
    assert sc.HERMES_AUX_TIMEOUT_SECONDS == 45


def test_hermes_aux_call_success(monkeypatch):
    class _Msg:
        content = "refusal"
    class _Choice:
        message = _Msg()
    class _Resp:
        choices = [_Choice()]
        usage = None
    class _Completions:
        def create(self, **kw):
            return _Resp()
    class _Chat:
        completions = property(lambda s: _Completions())
    class _Client:
        chat = _Chat()

    calls = {}
    def fake_get(task):
        calls["task"] = task
        return _Client(), "some-model"
    monkeypatch.setattr("agent.auxiliary_client.get_text_auxiliary_client", fake_get)
    real = sc._original_hermes_aux
    body = real('{"messages":[{"role":"user","content":"x"}],"max_tokens":10}', 5)
    assert calls["task"] == "router"
    data = json.loads(body)
    assert data["choices"][0]["message"]["content"] == "refusal"


def test_hermes_aux_call_failure_fails_open(monkeypatch):
    monkeypatch.setattr("agent.auxiliary_client.get_text_auxiliary_client",
                        lambda task: (None, None))
    real = sc._original_hermes_aux
    assert real("{}", 5) is None


def test_aux_raw_call_uses_hermes_not_curl(monkeypatch):
    """aux_raw_call must NOT touch the curl seam — aux resolves via Hermes."""
    class _Msg:
        content = "compliant"
    class _Choice:
        message = _Msg()
    class _Resp:
        choices = [_Choice()]
        usage = None
    class _Completions:
        def create(self, **kw):
            return _Resp()
    class _Chat:
        completions = property(lambda s: _Completions())
    class _Client:
        chat = _Chat()

    monkeypatch.setattr("agent.auxiliary_client.get_text_auxiliary_client",
                        lambda task: (_Client(), "some-model"))
    monkeypatch.setattr(sc, "_post_chat",
                        lambda *a, **k: pytest.fail("legacy curl must not fire"))
    monkeypatch.setattr(sc, "_hermes_aux_call", sc._original_hermes_aux)
    out = sc.aux_raw_call("hello", cfg={})
    assert out == "compliant"


def test_aux_raw_call_retry_then_breaker(monkeypatch):
    """Transport failure retries once (aux_retries default 1) before breaker
    accounting; open breaker short-circuits the retry too."""
    import types
    sc.reset_limits()
    calls = {"n": 0}
    def fake_call(payload_json, timeout):
        calls["n"] += 1
        return None
    monkeypatch.setattr(sc, "_hermes_aux_call", fake_call)
    monkeypatch.setattr(sc, "_record_failure", lambda cls: None)
    out = sc.aux_raw_call("hello", cfg={"aux_retries": 1})
    assert out is None
    assert calls["n"] == 2  # 1 attempt + 1 retry
