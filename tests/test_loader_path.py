"""Loader-path regression gate (v5.0.1 hotfix).

The Hermes gateway loads this plugin via importlib spec_from_file_location
with the plugin dir NOT on sys.path:

    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.hermes_router",
        <plugin_dir>/__init__.py,
        submodule_search_locations=[<plugin_dir>],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["hermes_plugins.hermes_router"] = module
    spec.loader.exec_module(module)

Under that loader, bare intra-package imports (from features... / import
classifier ...) raise ModuleNotFoundError: 'No module named ...' because the
repo cwd is not on sys.path. v5.0.0 shipped bare fallbacks that broke the
live gateways at plugin load. This test replicates the loader EXACTLY and
asserts the package loads clean with a working lanes registry.
"""
from __future__ import annotations

import importlib.util
import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _load_via_gateway_loader():
    """Exact gateway loader replication: spec_from_file_location + exec_module."""
    init_path = os.path.join(PLUGIN_DIR, "__init__.py")
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.hermes_router", init_path,
        submodule_search_locations=[PLUGIN_DIR],
    )
    assert spec is not None and spec.loader is not None, "spec_from_file_location returned None"
    # The gateway registers the parent namespace package before exec (otherwise
    # even relative imports raise 'No module named hermes_plugins').
    import types
    parent = types.ModuleType("hermes_plugins")
    parent.__path__ = [os.path.dirname(PLUGIN_DIR)]
    sys.modules.setdefault("hermes_plugins", parent)
    module = importlib.util.module_from_spec(spec)
    sys.modules["hermes_plugins.hermes_router"] = module
    spec.loader.exec_module(module)
    return module


def test_gateway_loader_loads_package():
    module = _load_via_gateway_loader()
    assert module is not None
    assert sys.modules["hermes_plugins.hermes_router"] is module


def test_router_section_returns_dict():
    module = _load_via_gateway_loader()
    from hermes_plugins.hermes_router.core import config_access

    assert isinstance(config_access.router_section(), dict)


def test_lanes_registry_populated():
    module = _load_via_gateway_loader()
    import hermes_plugins.hermes_router.lanes.registry as lanes_registry

    assert len(lanes_registry.all_ids()) > 0
