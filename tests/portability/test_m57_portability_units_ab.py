"""m57 portability — units A (key_env precedence) + B (aux auto-derivation).

Pins the fresh-install contract of the m57 brief:
  A. render-chain keys read key_env FIRST (key_env > key_file > provider env);
     a fresh host needs NO .secrets file.
  B. when aux/config is silent, the endpoint derives from the HOST's own
     provider (providers.<active>.base_url + model.default) — never a vendor
     hostname, never a loopback address, and derivation failure returns ""
     (fail-open no-egress) instead of inventing an endpoint.
"""
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

import hermes_router.router as router
from hermes_router.core import provider_derivation as pd


# ---------------------------------------------------------------------------
# A. render chain key_env precedence
# ---------------------------------------------------------------------------


@pytest.fixture()
def _key_env_name(monkeypatch):
    """Pin the provider key-env NAME so tests never read a real VENICE_API_KEY."""
    monkeypatch.setattr(
        "hermes_router.core.config_access.provider_key_env",
        lambda name, default="": "M57_TEST_RENDER_KEY", raising=False)
    monkeypatch.delenv("M57_TEST_RENDER_KEY", raising=False)
    return "M57_TEST_RENDER_KEY"


@pytest.fixture()
def key_file(tmp_path):
    p = tmp_path / "venice_key"
    p.write_text("file-key-123\n", encoding="utf-8")
    return str(p)


def test_key_env_wins_over_key_file(_key_env_name, key_file, monkeypatch):
    monkeypatch.setenv(_key_env_name, "env-key-456")
    assert router._read_key(key_file, _key_env_name) == "env-key-456"


def test_key_env_beats_default_provider_env(_key_env_name, key_file, monkeypatch):
    monkeypatch.setenv(_key_env_name, "env-key-456")
    # entry key_env name, no explicit key_file → env resolves, no file needed
    assert router._read_key("", _key_env_name) == "env-key-456"


def test_fresh_host_needs_no_secrets_file(_key_env_name, monkeypatch):
    """THE fresh-install pin: env-only host, no key file anywhere."""
    monkeypatch.setenv(_key_env_name, "env-key-456")
    assert router._read_key("/nonexistent/no/key/file", "") == "env-key-456"


def test_provider_configured_env_name_honored(key_file, monkeypatch):
    """providers.venice.key_env names the env var (M24-03) — honored
    even with no explicit entry key_env (pre-m57 behavior preserved)."""
    monkeypatch.setattr(
        "hermes_router.core.config_access.provider_key_env",
        lambda name, default="": "M57_TEST_PROVIDER_KEY", raising=False)
    monkeypatch.setenv("M57_TEST_PROVIDER_KEY", "provider-env-key")
    assert router._read_key("/does/not/matter", "") == "provider-env-key"


def test_key_file_resolved_when_env_unset(_key_env_name, key_file, monkeypatch):
    assert router._read_key(key_file, "") == "file-key-123"


def test_both_missing_fails_open(_key_env_name, monkeypatch):
    assert router._read_key("", "") == ""
    assert router._read_key("/nonexistent/no/key/file", "M57_NOPE") == ""


# ---------------------------------------------------------------------------
# B. host-provider auto-derivation
# ---------------------------------------------------------------------------


def _patch_host(monkeypatch, cfg):
    monkeypatch.setattr(pd, "_host_full_config", lambda: cfg, raising=False)


def test_derivation_from_host_provider_block(monkeypatch):
    _patch_host(monkeypatch, {
        "model": {"default": "upstage/solar-mini-4-5", "provider": "acme"},
        "providers": {"acme": {"base_url": "https://api.acme.example/v1"}},
    })
    base, model, name = pd.host_provider_endpoint()
    assert base == "https://api.acme.example/v1"
    assert model == "upstage/solar-mini-4-5"
    assert name == "acme"


def test_derivation_prefers_host_provider_block_over_model_base(monkeypatch):
    _patch_host(monkeypatch, {
        "model": {"default": "m", "provider": "acme",
                  "base_url": "https://model-base.example/v1"},
        "providers": {"acme": {"base_url": "https://provider-base.example/v1"}},
    })
    assert pd.host_provider_endpoint()[0] == "https://provider-base.example/v1"


def test_derivation_uses_model_base_url_when_provider_block_absent(monkeypatch):
    _patch_host(monkeypatch, {
        "model": {"default": "m", "provider": "acme",
                  "base_url": "https://model-base.example/v1"},
    })
    assert pd.host_provider_endpoint()[0] == "https://model-base.example/v1"


def test_derivation_empty_when_host_silent(monkeypatch):
    """THE no-invention pin: a host with no provider info derives NOTHING —
    no loopback, no vendor hostname. Callers fail open (no egress)."""
    _patch_host(monkeypatch, {"model": {"default": "m", "provider": "ghost"}})
    base, model, name = pd.host_provider_endpoint()
    assert base == ""
    assert "127.0.0.1" not in base and "localhost" not in base


def test_derived_defaults_never_contain_loopback_or_vendor_hosts():
    """m57-E grep-style assertion: the derivation module's DIALABLE literals
    (string constants, excluding the docstring's prohibition text) never
    mention a loopback or a vendor endpoint."""
    import ast
    import inspect

    src = inspect.getsource(pd)
    tree = ast.parse(src)
    banned = ("127.0.0.1", "localhost", "nousresearch.com",
              "openrouter.ai", "venice.ai", "0.0.0.0")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for b in banned:
                assert b not in node.value, \
                    "provider_derivation carries dialable %r" % b


def test_semantic_judge_fallback_fails_open_without_derivation(monkeypatch):
    from hermes_router.core import aux_endpoint as ae
    from hermes_router.features.detection import semantic_judge as sj

    monkeypatch.setattr(ae, "fallback_base_url", lambda: "", raising=False)
    assert sj._fallback_client() is None


def test_semantic_judge_fallback_uses_host_base(monkeypatch):
    from hermes_router.core import aux_endpoint as ae
    from hermes_router.features.detection import semantic_judge as sj

    captured = {}

    class _FakeOpenAI:
        def __init__(self, base_url=None, api_key=None, **kw):
            captured["base_url"] = base_url
            captured["api_key"] = api_key

    monkeypatch.setattr(ae, "fallback_base_url",
                        lambda: "https://host-own.example/v1", raising=False)
    monkeypatch.setenv("NOUS_API_KEY", "host-own-key")
    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI, raising=False)
    assert sj._fallback_client() is not None
    assert captured["base_url"] == "https://host-own.example/v1"
    assert captured["api_key"] == "host-own-key"


def test_completion_audit_revision_base_derives_from_host(monkeypatch):
    """The revision pass resolves its base the same way: explicit plugin
    provider first, HOST derivation second, vendor literal never."""
    import hermes_router.completion_audit as ca

    src = inspect_source(ca)
    assert "inference-api.nousresearch.com" not in src, \
        "completion_audit still carries the vendor render-lane default"


def inspect_source(module):
    import inspect

    return inspect.getsource(module)
