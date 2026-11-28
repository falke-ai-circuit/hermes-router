"""Manifest + registration tests (spec §10 row 1).

Verifies plugin.yaml parses, and register(ctx) runs without exception on a
mock context, registering llm_request middleware + transform_llm_output hook.
"""
import os
import sys

import pytest
import yaml

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
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
    # m64-B2: `middleware:` is NOT a known manifest field (G5 requires none
    # outside _KNOWN_MANIFEST_FIELDS), so the declaration moved out of the
    # manifest. Registration is unchanged: register() calls
    # ctx.register_middleware("llm_request", ...), asserted on a mock context in
    # test_register_on_mock_context below. `hooks:` IS a known field and stays.


def test_manifest_declares_no_unknown_warning_keys():
    """m64-B1/B3: `middleware_schema` and `observer_schema` are NOT in Hermes'
    _KNOWN_MANIFEST_FIELDS, so declaring them warned on EVERY load at
    manifest_version >= 2. The keys are gone and these pins assert ABSENCE, so
    a future edit cannot silently reintroduce the warnings. `middleware:` IS a
    known field and stays declared (see test_manifest_parses)."""
    manifest = _load_manifest()
    assert "middleware_schema" not in manifest
    assert "observer_schema" not in manifest


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