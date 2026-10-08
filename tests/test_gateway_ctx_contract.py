"""Gateway-contract regression test (ctxfix brief 2026-10-08).

The gateway (hermes_cli/plugins.py _load_dir_module) imports this plugin as
``hermes_plugins.<slug>`` — NOT as ``hermes_router``. v5.0.0's _hub() seam in
gate/orchestration.py hardcoded ``sys.modules['hermes_router']``, so every
LIVE middleware/hook call raised KeyError('hermes_router'), swallowed by the
gateway's fail-open: all turns delivered un-routed/bannerless while the suite
stayed green (tests import under the plain ``hermes_router`` root).

This file replicates the gateway loader mechanics EXACTLY (namespace parent
``hermes_plugins`` + spec_from_file_location on the plugin __init__) and
fires both entries with the ctx keys exactly as the live warning lines list
them — no 'hermes_router' key anywhere. Both must degrade/perform without
raising. The suite missed this seam twice (v5.0.0, v5.0.1); it must not again.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
_NS_PARENT = "hermes_plugins"  # hermes_cli/plugins.py:222
_SLUG = "hermes_router"        # manifest.key -> slug (no '/' or '-')
_ROOT = f"{_NS_PARENT}.{_SLUG}"


def _load_gateway_rooted():
    """Replicate hermes_cli.plugins._load_dir_module for this plugin."""
    if _NS_PARENT not in sys.modules:
        ns_pkg = types.ModuleType(_NS_PARENT)
        ns_pkg.__path__ = []
        ns_pkg.__package__ = _NS_PARENT
        sys.modules[_NS_PARENT] = ns_pkg
    spec = importlib.util.spec_from_file_location(
        _ROOT, _PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(_PLUGIN_DIR)])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = _ROOT
    module.__path__ = [str(_PLUGIN_DIR)]
    sys.modules[_ROOT] = module
    spec.loader.exec_module(module)
    return module


def _gateway_ctx(request_body):
    """ctx keys EXACTLY as the live 11:48:00 warning line provides them."""
    return dict(
        model="z-ai/glm-5.3-flash",
        platform="api_server",
        session_id="api_1791541680_ctxfix",
        task_id="task_ctxfix_1",
        turn_id=1,
        provider="custom",
        request=request_body,
        base_url="https://example.invalid/v1",
        api_key_set=True,
        api_key_file=None,
        telemetry_schema_version=1,
        middleware_schema_version=1,
        original_request=dict(request_body),
    )


def _hook_ctx(response_text):
    """ctx keys EXACTLY as the live 11:51:35 hook warning line."""
    return dict(
        model="z-ai/glm-5.3-flash",
        platform="api_server",
        response_text=response_text,
        session_id="api_1791541680_ctxfix",
        telemetry_schema_version=1,
        turn_id=1,
    )


def _purge_ns():
    """Drop every hermes_plugins* module from sys.modules.

    Earlier tests (test_loader_path.py, test_rider19_fixes.py) load the
    plugin under the gateway root and LEAVE partial/stale entries behind —
    a stale root whose subpackages were never imported under that root makes
    the loader below reuse poisoned state. Purging before AND after makes
    every test here order-independent (pollution-proof both directions).
    """
    for name in list(sys.modules):
        if name == _NS_PARENT or name.startswith(_NS_PARENT + "."):
            sys.modules.pop(name, None)


@pytest.fixture()
def gateway_rooted():
    """Load the plugin under the gateway root on CLEAN namespace state;
    purge again on teardown so later tests start clean too.

    Mirrors the live loader: everything (parent ns pkg, plugin root, child
    modules) STAYS in sys.modules during the test, exactly as the gateway
    leaves it.
    """
    _purge_ns()
    try:
        yield _load_gateway_rooted()
    finally:
        _purge_ns()


def _orch(hub=None):
    """The gateway-rooted orchestration module (importlib, not attr access —
    the hub's __getattr__ exports lifted names, not subpackages)."""
    return importlib.import_module(_ROOT + ".gate.orchestration")


def test_hub_resolves_under_gateway_root(gateway_rooted):
    orch = _orch(gateway_rooted)
    hub_mod = orch._hub()
    assert hub_mod is not None
    assert hasattr(hub_mod, "on_llm_request")
    assert getattr(hub_mod, "__name__", "").startswith(_ROOT)


def test_hub_resolves_under_legacy_test_root():
    import hermes_router.gate.orchestration as orch
    hub_mod = orch._hub()
    assert hub_mod is not None
    assert hasattr(hub_mod, "on_llm_request")


def test_on_llm_request_gateway_ctx_no_keyerror(gateway_rooted, monkeypatch):
    orch = _orch(gateway_rooted)
    hub_mod = orch._hub()
    assert hub_mod is not None
    hub_mod._dispatcher_knobs._enabled = lambda: False
    request_body = {"model": "z-ai/glm-5.3-flash",
                    "messages": [{"role": "user", "content": "hello"}]}
    result = orch.on_llm_request(**_gateway_ctx(request_body))
    assert isinstance(result, dict)  # pass-through {} when disabled


def test_on_transform_llm_output_gateway_ctx_no_keyerror(gateway_rooted):
    orch = _orch(gateway_rooted)
    hub_mod = orch._hub()
    assert hub_mod is not None
    hub_mod._dispatcher_knobs._enabled = lambda: False
    result = orch.on_transform_llm_output(**_hook_ctx("ordinary benign body"))
    assert result is None or isinstance(result, str)


def test_no_hermes_router_key_required(gateway_rooted):
    """The literal name must not be load-bearing: hub resolves with the
    plain root absent from sys.modules entirely."""
    saved = sys.modules.pop("hermes_router", None)
    try:
        orch = _orch(gateway_rooted)
        assert "hermes_router" not in sys.modules
        hub_mod = orch._hub()
        assert hub_mod is not None
        assert hasattr(hub_mod, "on_llm_request")
    finally:
        if saved is not None:
            sys.modules["hermes_router"] = saved


def test_hub_none_degrades_pass_through():
    """_hub() never raises; orchestrators degrade to pass-through when the
    module is somehow imported without any resolvable package context."""
    import importlib.util as _iu
    spec = _iu.spec_from_file_location(
        "ctxfix_orphan_orchestration",
        _PLUGIN_DIR / "gate" / "orchestration.py")
    assert spec is not None and spec.loader is not None
    orphan = importlib.util.module_from_spec(spec)
    sys.modules["ctxfix_orphan_orchestration"] = orphan
    try:
        spec.loader.exec_module(orphan)
        assert orphan._hub() is None  # was: KeyError('hermes_router')
        req = {"messages": [{"role": "user", "content": "hi"}]}
        assert orphan.on_llm_request(request=req, original_request=dict(req)) == {}
        assert orphan.on_transform_llm_output(
            response_text="body", session_id="s", turn_id=1) is None
    finally:
        sys.modules.pop("ctxfix_orphan_orchestration", None)
