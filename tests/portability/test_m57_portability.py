# Portability pins (m57) — hermes-router must work on ANY virgin Hermes install.
#
# Contract under test:
#   A. key_env precedence: chain/render key resolution must honour key_env
#      EVEN WHEN a key_file is present (ENV > file > provider-configured),
#      and a fresh host with NO .secrets file must still resolve a key.
#   B. Aux auto-derivation: when the explicit provider base is unset, the
#      aux/fallback base DERIVES from the host's own provider stats
#      (providers.<active>.base_url + model.default). No derivation result
#      may contain a loopback address or a vendor hostname.
#   C. hermes_router.base_url stays an OPTIONAL alias (legacy spellings must
#      keep resolving; none of them may be REQUIRED to boot).
#   D. anchor_chain nous:// works with NOUS_API_KEY (no OAuth); an
#      unresolvable scheme fails with an explicit, named reason — never a
#      silent "looks like a model error".
import os

import pytest

from hermes_router import anchor_chain
from hermes_router import config_access as ca
from hermes_router import router as _router


# ---------------------------------------------------------------------------
# A. key_env > key_file > provider key
# ---------------------------------------------------------------------------

def test_key_env_beats_key_file(tmp_path, monkeypatch):
    kf = tmp_path / "k"
    kf.write_text("file-key")
    monkeypatch.setenv("PORTAB_TEST_KEY", "env-key")
    assert _router._read_key(str(kf), "PORTAB_TEST_KEY") == "env-key"


def test_key_file_used_when_env_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("PORTAB_TEST_KEY", raising=False)
    kf = tmp_path / "k"
    kf.write_text("file-key")
    assert _router._read_key(str(kf), "PORTAB_TEST_KEY") == "file-key"


def test_no_secrets_file_key_resolves_from_env(tmp_path, monkeypatch):
    """Fresh host: no .secrets anywhere, only the process env. The chain
    entry configured without any key_file still resolves its key."""
    monkeypatch.delenv("PORTAB_TEST_KEY", raising=False)
    monkeypatch.setenv("PORTAB_TEST_KEY", "env-only")
    assert _router._read_key("", "PORTAB_TEST_KEY") == "env-only"


def test_provider_configured_key_env_discovered_without_secrets(tmp_path,
                                                                monkeypatch):
    """A chain entry with NO key_file and NO key_env still finds the
    provider-configured env name (deliverable A: fresh host needs no
    .secrets file and no key_env in config)."""
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    monkeypatch.setenv("VENICE_API_KEY", "provider-key")
    assert _router._read_key("", "") == "provider-key"


# ---------------------------------------------------------------------------
# B. aux base derivation — env-derived, NEVER loopback, NEVER vendor host
# ---------------------------------------------------------------------------

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "0.0.0.0", "::1")
_VENDOR_HOSTS = ("nousresearch.com", "venice.ai", "openrouter.ai",
                 "abliteration.ai", "minimax.io")


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch):
    """Portability pins must measure the FRESH-HOST surface: no profile
    config bleed, no carrier env, no legacy alias. `router_section` is
    patched at the consumer seam (config_access) — anchor_chain/config
    reads answer {} exactly like a virgin install."""
    monkeypatch.setenv("HERMES_HOME", "")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("NOUS_API_KEY", raising=False)
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    monkeypatch.setattr(ca, "router_section", lambda: {}, raising=True)


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    return str(urlparse(url).hostname or "").lower()


@pytest.fixture()
def _virgin_host(monkeypatch):
    """Pin EVERY host-config seam to the virgin shape: root config with a
    model.provider but NO provider blocks, no co-located config, legacy
    hermes_home unresolvable. Without this the pins would read THIS dev
    machine's real config and pass for the wrong reason (persisted state —
    the exact canary-virginity trap the m57 correction names)."""
    import hermes_router.core.provider_derivation as _pd

    monkeypatch.setattr(_pd, "_host_full_config",
                        lambda: {"model": {"provider": "nous",
                                           "default": "m"}}, raising=False)
    monkeypatch.setattr(ca, "_coLocatedPath",
                        lambda: "/nonexistent-hermes-home-m57/config.yaml",
                        raising=False)
    monkeypatch.setenv("HERMES_HOME", "/nonexistent-hermes-home-m57")
    monkeypatch.setattr(ca, "providers_custom", lambda: {}, raising=False)


@pytest.mark.parametrize("custom_base,default_model", [
    ("https://llm.example-provider.test/v1", "vendor-xoub-v1-xlarge"),
    ("https://api.myownhost.dev/openai/v1", "myownhost-large"),
])
def test_aux_base_derives_from_core_runtime(custom_base, default_model,
                                            monkeypatch):
    """No explicit nous base: the aux fallback base is what Hermes CORE
    resolved for the host's own provider (m58-CORRECTION — the plugin holds
    no second resolver)."""
    from hermes_router.core import provider_derivation as _pd

    monkeypatch.setattr(_pd, "_core_runtime", lambda: {
        "provider": "custom", "requested_provider": "custom:venice",
        "base_url": custom_base, "api_key": "k",
        "source": "custom_provider:venice",
    }, raising=False)
    monkeypatch.setattr(ca, "agent_model", lambda: default_model)
    from hermes_router.core import aux_endpoint

    base = aux_endpoint.fallback_base_url()
    assert base, "core resolution must yield a base"
    assert _host_of(base) == _host_of(custom_base)


def test_no_default_value_contains_loopback_or_vendor(monkeypatch):
    """Grep-style assertion over EVERY resolved default the AUX lane can
    produce on a baseline install: none may name a loopback address or a
    vendor hostname. (openrouter.ai is exempt: deliverable D names the
    openrouter:// scheme as the detection-safe fallback carrier — its base
    is a sanctioned fallback, not an aux default.)"""
    monkeypatch.setattr(ca, "providers_custom", lambda: {})
    from hermes_router.core import aux_endpoint

    bases = [
        aux_endpoint.fallback_base_url(),
        anchor_chain.provider_endpoint("nous", anchor_chain.nous_base_url()),
    ]
    for b in bases:
        host = _host_of(b or "")
        assert host not in _LOOPBACK_HOSTS, f"loopback default leaked: {b}"
        assert not any(host.endswith(v) for v in _VENDOR_HOSTS), \
            f"vendor default leaked: {b}"


def test_explicit_provider_base_wins_over_derivation(monkeypatch, tmp_path):
    """A profile that names providers.nous.base_url keeps the exact bytes
    (backward compat): derivation only fills the ABSENT case."""
    monkeypatch.setenv("HERMES_HOME", "/nonexistent-hermes-home-m57")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setattr(ca, "providers_custom", lambda: {
        "venice": {"base_url": "https://derived-should-not-appear.test/v1",
                   "key_env": "NOPE_KEY"},
    })
    cfgfile = tmp_path / "config.yaml"
    import yaml as _yaml

    _yaml.safe_dump({
        "hermes_router": {
            "providers": {
                "nous": {"base_url": "https://explicit.example/v1"},
            },
        },
    }, cfgfile.open("w"))
    monkeypatch.setattr(ca, "_coLocatedPath", lambda: str(cfgfile),
                        raising=True)
    monkeypatch.setattr(ca, "router_section",
                        lambda: {"providers": {
                            "nous": {"base_url": "https://explicit.example/v1"}}},
                        raising=True)
    from hermes_router.core import aux_endpoint

    monkeypatch.setattr(ca, "agent_model", lambda: "m")
    base = aux_endpoint.fallback_base_url()
    assert _host_of(base) == "explicit.example"


# ---------------------------------------------------------------------------
# C. legacy alias optional
# ---------------------------------------------------------------------------

def test_legacy_base_url_alias_resolves_and_is_optional():
    # alias still declared
    assert ca.telemetry_key("providers.nous.base_url") is not None
    # and absent in a clean install -> default '' (never required to boot)
    from hermes_router.core import schema

    assert schema.key("providers.nous.base_url").default == ""


# ---------------------------------------------------------------------------
# D. anchor scheme resolution
# ---------------------------------------------------------------------------

def test_nous_scheme_resolves_with_env_key_only(monkeypatch):
    """nous:// must resolve to a real base + NOUS_API_KEY env name when the
    HOST's core runtime resolved a nous credential from env (the
    origin-evidence shape: OAuth absent, key arrives via --env-file; m58-
    CORRECTION — the plugin asks core, it does not scan env itself)."""
    monkeypatch.setenv("HERMES_HOME", "/nonexistent-hermes-home-m57")
    monkeypatch.setenv("NOUS_API_KEY", "sk-nous-m57-fresh-host-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    from hermes_router.core import provider_derivation as _pd

    monkeypatch.setattr(_pd, "_core_runtime", lambda: {
        "provider": "nous", "requested_provider": "nous",
        "base_url": "https://inference-api.nousresearch.example/v1",
        "api_key": "sk-nous-m57-fresh-host-key", "source": "env:NOUS_API_KEY",
    }, raising=False)
    monkeypatch.setattr(ca, "providers_custom", lambda: {})
    ep = anchor_chain.parse_anchor_uri("nous://any-provider/any-model",
                                       "primary")
    assert ep is not None, "nous:// must resolve with the core runtime"
    host = _host_of(ep.base_url)
    assert host not in _LOOPBACK_HOSTS, f"nous default is loopback: {ep.base_url}"
    assert ep.api_key_env == "NOUS_API_KEY"
    # the credential itself resolves straight from the process env
    from hermes_router import anchor_exec

    assert anchor_exec._resolve_key(ep) == "sk-nous-m57-fresh-host-key"


def test_nous_scheme_without_any_credential_fails_explicit(monkeypatch):
    """A bare host (no NOUS_API_KEY, nothing to derive) fails CLOSED with a
    NAMED scheme failure — never a loopback/vendor literal, never a silent
    None that would look like an ordinary model error."""
    monkeypatch.delenv("NOUS_API_KEY", raising=False)
    monkeypatch.setenv("HERMES_HOME", "/nonexistent-hermes-home-m57")
    monkeypatch.setattr(ca, "providers_custom", lambda: {})
    from hermes_router.core import aux_endpoint

    reason, hint = aux_endpoint.scheme_failure("nous://m", "primary")
    assert reason == "unresolvable_scheme"
    assert "openrouter" in hint.lower()
    ep = anchor_chain.parse_anchor_uri("nous://m", "primary")
    assert ep is None


def test_unresolvable_scheme_fails_with_explicit_reason(monkeypatch):
    """A scheme that cannot authenticate fails CLOSED with a NAMED reason
    — the failure must never look like a normal model error."""
    monkeypatch.setattr(ca, "providers_custom", lambda: {})
    from hermes_router.core import aux_endpoint

    reason, hint = aux_endpoint.scheme_failure("ghostscheme://m", "primary")
    assert reason, "named reason required"
    assert hint, "detection-safe fallback hint required"
    assert "openrouter" in hint.lower()


# ---------------------------------------------------------------------------
# E2. render-chain env-key users: entry-level key_env still honoured after
#     the precedence flip (backward compat for every deployed profile).
# ---------------------------------------------------------------------------

def test_model_attempt_reports_key_unconfigured_without_any_key(monkeypatch):
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    monkeypatch.setenv("HERMES_HOME", "/nonexistent-hermes-home-m57")
    monkeypatch.setattr(_router, "_default_key_file", lambda: "")
    content, reason = _router._model_attempt(
        {"url": "http://m57-invalid.invalid/v1/chat/completions",
         "model": "m", "key_file": "", "key_env": ""},
        "probe", max_tokens=8000, temperature=0.5, system_prompt="")
    assert content == ""
    # named reason, and NOT a silent pass-through
    assert reason in ("key_unconfigured", "key_unreadable")


def test_derived_nous_base_pairs_its_providers_key_env(monkeypatch):
    """m57 coherence, corrected shape: a nous:// base resolved by core from
    a NAMED host custom provider authenticates as THAT provider ( core
    source=custom_provider:venice -> the entry's key_env bills, base and
    key always from the same origin)."""
    monkeypatch.setenv("HERMES_HOME", "/nonexistent-hermes-home-m57")
    monkeypatch.delenv("NOUS_API_KEY", raising=False)
    monkeypatch.setattr(ca, "providers_custom", lambda: {
        "venice": {"base_url": "https://api.venice-derived.test/api/v1",
                   "key_env": "DERIVED_KEY"},
    })
    monkeypatch.setenv("DERIVED_KEY", "k")
    from hermes_router.core import provider_derivation as _pd

    monkeypatch.setattr(_pd, "_core_runtime", lambda: {
        "provider": "custom", "requested_provider": "custom:venice",
        "base_url": "https://api.venice-derived.test/api/v1",
        "api_key": "k", "source": "custom_provider:venice",
    }, raising=False)
    ep = anchor_chain.parse_anchor_uri("nous://tier-1/model-x", "primary")
    assert ep is not None
    assert _host_of(ep.base_url) == "api.venice-derived.test"
    assert ep.api_key_env == "DERIVED_KEY"
