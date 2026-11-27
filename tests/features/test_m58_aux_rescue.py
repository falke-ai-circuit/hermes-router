"""m58 — the no_client rescue in the shared aux dispatch.

semantic_classifier._hermes_aux_call is the aux dispatch every lane rides
(decision, intent, echo_reprompt, attestation, classification). Pre-m58,
core auxiliary client == None meant instant no_client — the m58 defect
signature (every lane silent, no banner, undiagnosable). Post-m58 the
dispatch tries the ONE derived client (aux_endpoint.fallback_client, built
by Hermes core itself) BEFORE declaring no_client, so a host whose provider
core CAN resolve gets a firing aux instead of six silent turns.
"""
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, os.path.dirname(PLUGIN_DIR))

from hermes_router import semantic_classifier as sc
from hermes_router.core import aux_endpoint as ae
from hermes_router.core import config_access as ca
from hermes_router.core import provider_derivation as pd

ENV_BASE = "https://inference-api.nousresearch.com/v1"

_RT_SHAPE = {
    "provider": "openai-api",
    "requested_provider": "openai-api",
    "base_url": ENV_BASE,
    "api_key": "env-key-456",
    "api_mode": "chat_completions",
}


def _silent_host(monkeypatch):
    """Isolate every config seam AND the core resolution seams."""
    monkeypatch.setattr(pd, "_host_full_config", lambda: {}, raising=False)
    monkeypatch.setattr(pd, "_core_runtime", lambda: {}, raising=False)
    monkeypatch.setattr(pd, "_core_client", lambda: (None, ""), raising=False)
    monkeypatch.setattr(ca, "_coLocatedPath",
                        lambda: "/nonexistent-hermes-home-m58/config.yaml",
                        raising=False)
    monkeypatch.setenv("HERMES_HOME", "/nonexistent-hermes-home-m58")
    monkeypatch.setattr(ca, "providers_custom", lambda: {}, raising=False)
    monkeypatch.setattr(ca, "agent_model", lambda: "z-ai/glm-5.3-flash",
                        raising=False)

    def _no_config(*_args, default=None, **_kwargs):
        return default

    monkeypatch.setattr(ca, "get", _no_config, raising=False)
    for var in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "NOUS_API_KEY",
                "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("agent.auxiliary_client.get_text_auxiliary_client",
                        lambda task=None: (None, ""), raising=False)


def test_no_client_rescue_produces_firing_aux_when_core_resolves(monkeypatch):
    """THE acceptance shape at unit level: core's aux client absent, but
    core's runtime resolution works -> the rescue builds a client through
    core's resolve_provider_client and the aux call FIRES."""
    _silent_host(monkeypatch)
    monkeypatch.setattr(pd, "_core_runtime", lambda: dict(_RT_SHAPE),
                        raising=False)

    class _Msg:
        content = '{"verdict": "clean", "confidence": 0.9}'

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]
        usage = None

    class _Completions:
        def create(self, **kw):
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()
        base_url = ENV_BASE

    def _fake_rpc(provider, model=None, **kw):
        return _Client(), "test-model"

    monkeypatch.setattr("agent.auxiliary_client.resolve_provider_client",
                        _fake_rpc, raising=False)
    monkeypatch.setattr(sc, "_hermes_aux_call", sc._original_hermes_aux)
    body = sc._hermes_aux_call(
        '{"messages": [{"role": "user", "content": "classify me"}],'
        ' "max_tokens": 100}', 10)
    assert body is not None, "rescue must produce a firing aux body"
    data = __import__("json").loads(body)
    assert data["choices"][0]["message"]["content"].startswith("{")


def test_no_client_still_declared_when_core_resolves_nothing(monkeypatch):
    """m60 mandate #2: when core resolves NOTHING, the failure is NAMED —
    cause token resolver_empty + a lane-banner log line — never a bare
    silent no_client. The lane name rides the log."""
    _silent_host(monkeypatch)
    monkeypatch.setattr(sc, "_hermes_aux_call", sc._original_hermes_aux)
    import logging as _logging

    oline = []

    class _Collect(_logging.Handler):
        def emit(self, record):
            oline.append(record.getMessage())

    root = _logging.getLogger("hermes_router.semantic_classifier")
    handler = _Collect()
    root.addHandler(handler)
    try:
        body = sc._hermes_aux_call(
            '{"messages": [{"role": "user", "content": "x"}], "max_tokens": 10}',
            5)
    finally:
        root.removeHandler(handler)
    assert body is None
    assert sc.aux_failure_cause() == "resolver_empty"
    hits = [h for h in oline if "resolver_empty"
            in h and "semantic_classifier" in h]
    assert hits, "resolver_empty must log a lane-named banner"


def test_no_client_retained_when_core_resolves_but_client_build_fails(
        monkeypatch):
    """m60: `no_client` remains the cause token for the REAL
    resolved-but-unbuildable case — core resolved a base but
    fallback_client still returns None."""
    _silent_host(monkeypatch)
    monkeypatch.setattr(pd, "_core_runtime",
                        lambda: {"provider": "openai-api",
                                 "base_url": ENV_BASE,
                                 "api_key": "env-key-456",
                                 "api_mode": "chat_completions"},
                        raising=False)
    monkeypatch.setattr(pd, "_core_client", lambda: (None, ""),
                        raising=False)
    monkeypatch.setattr(
        "agent.auxiliary_client.resolve_provider_client",
        lambda *a, **kw: (None, ""), raising=False)
    monkeypatch.setattr(sc, "_hermes_aux_call", sc._original_hermes_aux)
    monkeypatch.setattr(ca, "get", lambda *a, **kw: None, raising=False)
    body = sc._hermes_aux_call(
        '{"messages": [{"role": "user", "content": "x"}], "max_tokens": 10}',
        5)
    assert body is None
    assert sc.aux_failure_cause() == "no_client"
