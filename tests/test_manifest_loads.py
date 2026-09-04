"""Manifest + registration tests (spec §10 row 1).

Verifies plugin.yaml parses, and register(ctx) runs without exception on a
mock context, registering llm_request middleware + transform_llm_output hook.
"""
import os
import sys

import pytest
import yaml

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)


def _load_manifest():
    with open(os.path.join(PLUGIN_DIR, "plugin.yaml"), "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class MockCtx:
    """Minimal PluginContext stand-in capturing registrations."""

    def __init__(self):
        self.hooks = {}
        self.middleware = {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_middleware(self, name, callback):
        self.middleware[name] = callback


def test_manifest_parses():
    manifest = _load_manifest()
    assert manifest["name"] == "hermes-router"
    assert manifest["version"]
    assert "transform_llm_output" in manifest.get("hooks", [])
    assert "llm_request" in manifest.get("middleware", [])


def test_manifest_schema_pins():
    manifest = _load_manifest()
    assert manifest.get("middleware_schema") == "hermes.middleware.v1"
    assert manifest.get("observer_schema") == "hermes.observer.v1"


def test_register_on_mock_context():
    import hermes_router as plugin

    ctx = MockCtx()
    plugin.register(ctx)
    assert "transform_llm_output" in ctx.hooks
    assert "llm_request" in ctx.middleware
    assert callable(ctx.hooks["transform_llm_output"])
    assert callable(ctx.middleware["llm_request"])


def test_register_is_idempotent():
    import hermes_router as plugin

    ctx = MockCtx()
    plugin.register(ctx)
    plugin.register(ctx)  # no exception
    assert ctx.hooks["transform_llm_output"] is plugin.on_transform_llm_output
    assert ctx.middleware["llm_request"] is plugin.on_llm_request