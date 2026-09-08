"""Hermes-core aux source (2026-09-08, Goran-direct): classification.aux_source
= hermes routes aux calls through agent.auxiliary_client (the profile's own
`auxiliary:` config) instead of the plugin's aux_endpoint curl seam."""
from unittest import mock

import pytest

from hermes_router import semantic_classifier as sc


def test_aux_source_default_legacy():
    assert sc._aux_source({}) == "legacy"


def test_aux_source_hermes_flag():
    assert sc._aux_source({"aux_source": "hermes"}) == "hermes"


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
    class _Client:
        chat = type("C", (), {"completions": property(lambda s: _Completions())})()

    calls = {}
    def fake_get(task):
        calls["task"] = task
        return _Client(), "some-model"
    monkeypatch.setattr("agent.auxiliary_client.get_text_auxiliary_client", fake_get)
    body = sc._hermes_aux_call('{"messages":[{"role":"user","content":"x"}],"max_tokens":10}', 5)
    assert calls["task"] == "router"
    data = __import__("json").loads(body)
    assert data["choices"][0]["message"]["content"] == "refusal"


def test_hermes_aux_call_failure_fails_open(monkeypatch):
    def fake_get(task):
        return None, None
    monkeypatch.setattr("agent.auxiliary_client.get_text_auxiliary_client", fake_get)
    assert sc._hermes_aux_call("{}", 5) is None


def test_aux_raw_call_hermes_source_skips_legacy_key(monkeypatch):
    """With aux_source=hermes, aux_raw_call must NOT require NOUS/MINIMAX keys
    and must not call _post_chat."""
    import json as _json
    sc.reset_limits()
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
    monkeypatch.delenv("NOUS_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    out = sc.aux_raw_call("hello", cfg={"aux_source": "hermes",
                                        "aux_endpoint": {"url": "https://x", "model": "m",
                                                         "key_env": "NOUS_API_KEY"}})
    assert out == "compliant"
