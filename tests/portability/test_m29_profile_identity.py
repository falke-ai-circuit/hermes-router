"""M29 · F9 — profile identity has an explicit source.

At 1e9dab3 the profile name was derived by string-splitting a path
(`core/routing_caps.py:119,127`, `instinct_frame.py:87`). That is why a single
shared install loses its per-agent cap key: no path contains `profiles/<name>`.
`HERMES_PROFILE` is now the explicit source; the old derivation stays as the
fallback so fleet behaviour is unchanged when it is unset.
"""
import sys
import types

import pytest

from hermes_router import instinct_frame
from hermes_router.core import routing_caps


@pytest.fixture(autouse=True)
def _fresh_identity_cache():
    routing_caps._AGENT_ID_CACHE = None
    yield
    routing_caps._AGENT_ID_CACHE = None


@pytest.fixture()
def home(monkeypatch):
    """Pin the resolved Hermes home for both code paths."""
    def _set(path):
        mod = types.ModuleType("hermes_constants")
        mod.get_hermes_home = lambda: path
        monkeypatch.setitem(sys.modules, "hermes_constants", mod)
        monkeypatch.setenv("HERMES_HOME", path)
        return path
    return _set


def test_explicit_identity_wins_over_path(monkeypatch, home):
    home("/srv/shared/.hermes")          # path names the home, not the agent
    monkeypatch.setenv("HERMES_PROFILE", "shared-agent")
    assert routing_caps.agent_identity() == "shared-agent"


def test_path_derivation_still_answers_when_unset(monkeypatch, home):
    home("/srv/profiles/alpha")
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    assert routing_caps.agent_identity() == "alpha"


def test_separator_value_is_not_a_profile_name(monkeypatch, home):
    home("/srv/profiles/alpha")
    monkeypatch.setenv("HERMES_PROFILE", "srv/profiles/beta")
    assert routing_caps.agent_identity() == "alpha", \
        "a path fragment was accepted as the agent identity"


def test_identity_is_never_empty(monkeypatch, home):
    home("/srv/shared/.hermes")
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    assert routing_caps.agent_identity() == "unknown-agent"


def test_instinct_profile_name_uses_explicit_source(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE", "shared-agent")
    monkeypatch.setenv("HERMES_HOME", "/srv/shared/.hermes")
    assert instinct_frame._profile_name() == "shared-agent"


def test_instinct_profile_name_falls_back_to_path_split(monkeypatch):
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    monkeypatch.setenv("HERMES_HOME", "/srv/profiles/coder")
    assert instinct_frame._profile_name() == "coder"
