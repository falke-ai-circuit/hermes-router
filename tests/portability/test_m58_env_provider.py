"""m58-CORRECTION — the plugin asks Hermes core; it does not parse the host.

Pins the corrected contract: the plugin's aux/anchor endpoint resolution
DELEGATES to Hermes core's own runtime resolver
(hermes_cli.runtime_provider.resolve_runtime_provider) and aux client
surface (agent.auxiliary_client). No plugin-side env contract, no endpoint
literals, no second resolver that can drift from the host.

Whatever shape the host declares its provider in (providers block, env,
OAuth), the plugin works because it asks core. Unit tests fake the core
seams (the same monkeypatch discipline the m58 tests used) — the LIVE
canary run is the end-to-end acceptance.
"""
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, os.path.dirname(PLUGIN_DIR))

from hermes_router import anchor_chain
from hermes_router.core import aux_endpoint as ae
from hermes_router.core import config_access as ca
from hermes_router.core import provider_derivation as pd

ENV_BASE = "https://inference-api.nousresearch.com/v1"

_RT_ENV_SHAPE = {
    "provider": "openai-api",
    "requested_provider": "openai-api",
    "base_url": ENV_BASE,
    "api_key": "env-key-456",
    "api_mode": "chat_completions",
    "source": "env:OPENAI_API_KEY",
}


def _silent_host(monkeypatch):
    """Isolate every plugin config seam AND the core resolution seams the
    adapter now rides — the tree under test holds no provider knowledge of
    its own, so a leak here means the fixture is bad, not the code."""
    monkeypatch.setattr(pd, "_host_full_config", lambda: {}, raising=False)
    monkeypatch.setattr(pd, "_core_runtime", lambda: {}, raising=False)
    monkeypatch.setattr(pd, "_core_client", lambda: (None, ""), raising=False)
    monkeypatch.setattr(ca, "_coLocatedPath",
                        lambda: "/nonexistent-hermes-home-m58/config.yaml",
                        raising=False)
    monkeypatch.setenv("HERMES_HOME", "/nonexistent-hermes-home-m58")
    monkeypatch.setattr(ca, "providers_custom", lambda: {}, raising=False)

    def _no_config(*_args, default=None, **_kwargs):
        return default

    monkeypatch.setattr(ca, "get", _no_config, raising=False)
    for var in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENROUTER_BASE_URL",
                "OPENROUTER_API_KEY", "NOUS_BASE_URL", "NOUS_API_KEY"):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# A. the adapter delegates to core's runtime resolver
# ---------------------------------------------------------------------------


def test_adapter_asks_core_runtime(monkeypatch):
    """THE core delegation pin: endpoint == whatever core resolved."""
    _silent_host(monkeypatch)
    monkeypatch.setattr(pd, "_core_runtime", lambda: dict(_RT_ENV_SHAPE),
                        raising=False)
    base, model, name = pd.host_provider_endpoint()
    assert base == ENV_BASE
    assert name == "openai-api"


def test_adapter_does_not_parse_env(monkeypatch):
    """m58-CORRECTION: env vars alone must NOT resolve anything — the env
    becomes an endpoint only when CORE resolves it into the runtime."""
    _silent_host(monkeypatch)  # _core_runtime -> {}
    monkeypatch.setenv("OPENAI_BASE_URL", ENV_BASE)
    monkeypatch.setenv("OPENAI_API_KEY", "env-key-456")
    assert pd.host_provider_endpoint()[0] == ""
    assert ae.fallback_base_url() == ""
    # and the plugin source carries no env-contract parser
    import inspect
    src = inspect.getsource(pd)
    assert "_ENV_PROVIDER_SHAPES" not in src
    assert "OPENAI_BASE_URL" not in src


def test_core_unresolvable_fails_open(monkeypatch):
    """Core resolves nothing -> ("","",""), no invention, callers fail open."""
    _silent_host(monkeypatch)
    assert pd.host_provider_endpoint() == ("", "", "")
    assert ae.fallback_base_url() == ""
    assert pd.derived_aux_endpoint() is None


def test_explicit_plugin_provider_still_wins(monkeypatch):
    """Precedence preserved: the plugin's own lane config beats the core
    derivation (lane override, not host re-resolution)."""
    _silent_host(monkeypatch)
    monkeypatch.setattr(ca, "get",
                        lambda path, default=None, **kw: (
                            "https://plugin-lane.example/v1"
                            if path == "providers.nous.base_url" else default),
                        raising=False)
    monkeypatch.setattr(pd, "_core_runtime", lambda: dict(_RT_ENV_SHAPE),
                        raising=False)
    assert ae.fallback_base_url() == "https://plugin-lane.example/v1"


def test_derived_defaults_never_contain_loopback_or_vendor_hosts(monkeypatch):
    """m57-E assertion kept: the adapter module's DIALABLE literals never
    mention a loopback or a vendor endpoint."""
    import ast
    import inspect

    _silent_host(monkeypatch)
    src = inspect.getsource(pd)
    tree = ast.parse(src)
    banned = ("127.0.0.1", "localhost", "nousresearch.com",
              "openrouter.ai", "venice.ai", "0.0.0.0")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for b in banned:
                assert b not in node.value, \
                    "provider_derivation carries dialable %r" % b


# ---------------------------------------------------------------------------
# B. the client builder is core's own
# ---------------------------------------------------------------------------


def test_fallback_client_is_core_aux_client(monkeypatch):
    """Order 1: core's aux surface (get_text_auxiliary_client) wins when it
    resolves — the client IS the one the agent itself uses."""
    _silent_host(monkeypatch)

    class _CoreClient:
        base_url = ENV_BASE

    marker = _CoreClient()
    monkeypatch.setattr(pd, "_core_client",
                        lambda: (marker, "core-aux-model"), raising=False)
    assert ae.fallback_client() is marker


def test_fallback_client_falls_to_core_runtime_shape(monkeypatch):
    """Order 2: core's aux auto-detect can return (None, None) on host
    shapes core's MAIN runtime resolves fine (measured: provider=openai +
    env OPENAI_BASE_URL). The builder then shapes core's runtime dict into
    a client through core's own resolve_provider_client — still zero
    plugin-side parsing."""
    _silent_host(monkeypatch)
    monkeypatch.setattr(pd, "_core_runtime", lambda: dict(_RT_ENV_SHAPE),
                        raising=False)
    captured = {}

    def _fake_rpc(provider, model=None, explicit_base_url=None,
                  explicit_api_key=None, api_mode=None, **kw):
        captured.update(provider=provider, model=model,
                        base_url=explicit_base_url, api_key=explicit_api_key,
                        api_mode=api_mode)
        return "BUILT-CLIENT", "test-model"

    monkeypatch.setattr("agent.auxiliary_client.resolve_provider_client",
                        _fake_rpc, raising=False)
    client = ae.fallback_client()
    assert client == "BUILT-CLIENT"
    assert captured["provider"] == "openai-api"
    assert captured["base_url"] == ENV_BASE
    assert captured["api_key"] == "env-key-456"


def test_fallback_client_none_when_core_resolves_nothing(monkeypatch):
    _silent_host(monkeypatch)
    assert ae.fallback_client() is None


def test_judge_delegates_to_the_one_builder(monkeypatch):
    """semantic_judge._fallback_client stays a thin delegate over
    aux_endpoint.fallback_client (one builder, not two)."""
    from hermes_router.features.detection import semantic_judge as sj

    _silent_host(monkeypatch)
    marker = object()
    monkeypatch.setattr(ae, "fallback_client", lambda *a, **kw: marker,
                        raising=False)
    assert sj._fallback_client() is marker


# ---------------------------------------------------------------------------
# C. fail-loud startup line
# ---------------------------------------------------------------------------


def test_missing_input_report_names_core_state_when_silent(monkeypatch):
    _silent_host(monkeypatch)
    report = ae.missing_input_report()
    assert report
    assert "router_endpoint_unresolved" in report
    assert "hermes core" in report.lower()


def test_missing_input_report_silent_when_core_resolves(monkeypatch):
    _silent_host(monkeypatch)
    monkeypatch.setattr(pd, "_core_runtime", lambda: dict(_RT_ENV_SHAPE),
                        raising=False)
    assert ae.missing_input_report() == ""


# ---------------------------------------------------------------------------
# D. nous:// anchor pairing follows core's resolved provider
# ---------------------------------------------------------------------------


def test_nous_scheme_pairs_core_resolved_provider_key(monkeypatch):
    """A nous:// anchor whose base came from core's runtime pairs the key
    env of the provider CORE resolved — never a mixed base+key (401)."""
    _silent_host(monkeypatch)
    monkeypatch.setattr(pd, "_core_runtime", lambda: dict(_RT_ENV_SHAPE),
                        raising=False)
    monkeypatch.setattr(anchor_chain, "_custom_providers", lambda: {},
                        raising=False)
    ep = anchor_chain._resolve_scheme("nous", "z-ai/glm-5.3", "primary")
    assert ep is not None
    assert ep.base_url == ENV_BASE
    # core resolved provider 'openai-api' — the anchor names THAT provider's
    # key env (host-config lookup through _provider_key_env), not NOUS's.
    assert ep.api_key_env != anchor_chain.NOUS_KEY_ENV


def test_nous_scheme_without_core_resolution_fails_named(monkeypatch):
    _silent_host(monkeypatch)
    monkeypatch.setattr(anchor_chain, "_custom_providers", lambda: {},
                        raising=False)
    assert anchor_chain._resolve_scheme("nous", "m", "primary") is None
