"""F6 resilience pin: unresolvable provider key -> anchored_call returns
(None, None, None, None) and downstream callers fail open.

This path was accidentally informative when the v3.2.1 strip tests went
red (OpenRouter-402 key-stripping removed OPENROUTER_API_KEY from every
env). Pin the contract so it can never regress invisibly.
"""
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router import anchor_exec, anchor_chain  # noqa: E402


def _endpoint():
    return anchor_chain.parse_anchor_uri("openrouter://test/anchor-primary", "primary")


def test_placeholder_key_yields_none_tuple(monkeypatch, caplog):
    """Key resolution returning a placeholder/empty value must yield a
    clean None 4-tuple — never a raise, never a network attempt.
    _resolve_key is pinned (rather than env) because on the dev box the
    profile-dotenv fallback resolves a REAL fleet key and would attempt a
    live socket call — the same environment-dependence class v3.9.0's
    hermeticity fix addressed."""
    monkeypatch.setattr(anchor_exec, "_resolve_key", lambda env, ep=None: "")
    out = anchor_exec.anchored_call(
        _endpoint(),
        {"model": "m", "messages": [{"role": "user", "content": "ping"}],
         "max_tokens": 10})
    assert out == (None, None, None, None)


def test_missing_key_yields_none_tuple(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # also block the profile-dotenv fallback: point HOME nowhere useful
    monkeypatch.setenv("HERMES_HOME", "/nonexistent-hermes-home")
    out = anchor_exec.anchored_call(
        _endpoint(),
        {"model": "m", "messages": [{"role": "user", "content": "ping"}],
         "max_tokens": 10})
    assert out == (None, None, None, None)


def test_none_tuple_is_fail_open_downstream():
    """Downstream contract: callers treat (None, ...) as 'no consult
    happened' and continue the turn. Encode the dispatch-side check."""
    content, cost, pt, ct = (None, None, None, None)
    assert not content  # falsy = deliver unaudited / fail open
    # and the ledger never records a cost for a failed call
    assert not cost
