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
    # m64: `middleware:` is DELETED from the manifest (unknown to the host's
    # closed field set). What the plugin registers is a RUNTIME fact — assert
    # it where it actually lives, on the registration surface, below.
    assert "middleware" not in manifest


def test_manifest_declares_no_unknown_field():
    """m64: the manifest must stay inside the host's known-field set.

    `middleware:`, `middleware_schema:` and `observer_schema:` used to be
    declared here; none of them is a field `hermes_cli.plugins_manifest`
    knows, so every load logged "unknown manifest field(s) ignored". Removing
    them is only half the fix — the ABSENCE is what has to stay pinned, or a
    later edit reintroduces the warnings silently.
    """
    manifest = _load_manifest()
    assert "middleware_schema" not in manifest
    assert "observer_schema" not in manifest
    assert "middleware" not in manifest

    # The registry the host validates against, when this checkout can reach it.
    # A deployed plugin next to a host that does not expose the set skips the
    # cross-check rather than inventing a copy of it here.
    try:
        from hermes_cli.plugins_manifest import _KNOWN_MANIFEST_FIELDS
    except Exception:  # noqa: BLE001 — not every install exposes the internals
        pytest.skip("host manifest field set unavailable in this environment")
    unknown = sorted(set(manifest) - set(_KNOWN_MANIFEST_FIELDS))
    assert not unknown, (
        "plugin.yaml declares field(s) the host does not know (each one is an "
        "'unknown manifest field(s) ignored' warning at every load): "
        + ", ".join(unknown))


def test_manifest_config_keys_are_dotted_paths_and_cover_every_lane():
    """m64: `config_keys` names the lane block, uniformly.

    The host reads a `config_keys` entry as a dotted path at least two
    segments deep (`_config_key_path`); a bare section would silence typo
    notices for an entire namespace. Every lane must contribute the SAME four
    fields — that uniformity is the whole point of the lane contract.
    """
    from hermes_router.core import schema

    manifest = _load_manifest()
    keys = manifest.get("config_keys")
    assert isinstance(keys, list) and keys, "plugin.yaml must declare config_keys"
    for path in keys:
        assert isinstance(path, str)
        assert path.count(".") >= 1, (
            "config_keys entry %r must be at least two segments deep" % path)

    declared_here = {k.split(".", 1)[1] for k in keys
                     if k.startswith("hermes_router.")}
    for lane in schema.LANE_NAMES:
        for field in schema.LANE_FIELDS:
            assert "lanes.%s.%s" % (lane, field) in declared_here, (
                "lane %r field %r missing from config_keys" % (lane, field))
    assert len(declared_here) == len(keys), (
        "every config_keys entry must live under hermes_router.")


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