"""R23 leg 3 — plugin registration/wiring, lifted verbatim from the plugin
root `__init__.py` (v5 size-debt close-out; pure move, zero body edits).

The hub keeps thin delegating stubs (`register`, `__getattr__`, the on_*
re-exports) so the patch surface (`plugin.<name>`) and the gateway
registration contract are unchanged.
"""
from __future__ import annotations

import logging
import sys as _sys

from . import orchestration

# The hub logger is the PLUGIN ROOT module's logger — the registration
# warning rows keep the exact logger name they had pre-move.
_ROOT_NAME = __package__.rsplit(".", 1)[0]
logger = logging.getLogger(
    _sys.modules[_ROOT_NAME].__name__ if _ROOT_NAME in _sys.modules
    else _ROOT_NAME)


def install_legacy_aliases(root_name: str) -> None:
    """P1 legacy-path module aliases in sys.modules so EVERY import spelling
    of the moved modules keeps working (package attrs, `import
    hermes_router.X`, `from .X import Y`, monkeypatch string targets) — one
    shared module object per module (conftest Trap 5: single-namespace
    discipline). Registered at hub import, not lazily, so even attribute
    access before first use resolves identically. This is the back-compat
    shim; direct `hermes_router.core.X` imports are the new canonical
    spelling (see scripts/rewrite_imports.py)."""
    from ..api import (commands, commands_config, commands_diag,
                       commands_runtime, config_writer, router_tools)
    from ..core import (canonical, config_access, decisions, routing_caps,
                        session_store, state, telemetry, usage_ledger)

    for _m in (canonical, config_access, decisions, routing_caps,
               session_store, state, telemetry, usage_ledger, config_writer,
               commands, commands_config, commands_diag, commands_runtime,
               router_tools):
        _sys.modules.setdefault(f"{root_name}.{_m.__name__.rsplit('.', 1)[-1]}", _m)


on_llm_request = orchestration.on_llm_request
on_llm_execution = orchestration.on_llm_execution
on_transform_llm_output = orchestration.on_transform_llm_output
on_transform_terminal_output = orchestration.on_transform_terminal_output

_MOVABLE_ORCH_NAMES = tuple(
    n for n in dir(orchestration)
    if not n.startswith("__") and n != "_hub")


def hub_getattr(name: str):
    """P3a lazy export of the lifted names (dispatch: registration +
    __getattr__ for test-patched names only). Cached into globals so the
    attribute becomes real after first resolution (monkeypatch restore-safe:
    monkeypatch getattrs before setattr, which materializes the binding)."""
    try:
        value = getattr(orchestration, name)
    except AttributeError:
        raise AttributeError(
            f"module {_ROOT_NAME!r} has no attribute {name!r}") from None
    globals()[name] = value
    return value


def register(ctx) -> None:
    """Wire hooks + middleware + tools. Registration errors are isolated
    (P6 stage-1: the registration path migrates to core.telemetry.isolate —
    a failure emits the `router.swallow` warning row + per-gate counter
    surfaced via /router diag + router_status, never raised; a broken
    registration would disable the whole plugin in one profile)."""
    from ..core import telemetry as _t

    def _reg(gate: str, fn) -> None:
        _t.isolate(gate, fn, on_fail="pass")

    _reg("register.llm_request",
         lambda: ctx.register_middleware("llm_request", on_llm_request))
    _reg("register.llm_execution",
         lambda: ctx.register_middleware("llm_execution", on_llm_execution))
    _reg("register.transform_llm_output",
         lambda: ctx.register_hook("transform_llm_output",
                                   on_transform_llm_output))
    # R19.2 ADDENDUM 4: midturn decision hook SEAM 1 — fires after every
    # terminal tool result, mid-run. Registration failure never disables
    # other lanes.
    _reg("register.transform_terminal_output",
         lambda: ctx.register_hook("transform_terminal_output",
                                   on_transform_terminal_output))
    # v3.0.0: router control tools (phase 3) — registered defensively so a
    # tool registration failure never disables the middleware lanes.
    def _reg_tools() -> None:
        from .. import router_tools

        router_tools.register(ctx)

    _reg("register.router_tools", _reg_tools)
    # v3.5.0: /router chat command surface — LCM 3-branch pattern, env-gated
    # (HERMES_ROUTER_ENABLE_SLASH_COMMAND, default off). commands.
    # register_slash_command performs the collision self-check and the
    # flagship gateway-authz posture self-check (blueprint 6b.1) internally
    # and logs the one-line posture verdict.
    def _reg_slash() -> None:
        from .. import commands

        commands.register_slash_command(ctx)

    _reg("register.slash_command", _reg_slash)
    # P4 (proposal §2.3): LOAD-TIME VALIDATION pass — validate the whole
    # resolved config section once at plugin load; schema violations
    # (partial blocks / type mismatches) emit fail-loud telemetry rows
    # instead of surfacing as silent Nones mid-turn. Never raises.
    def _reg_validate() -> None:
        from ..core import config_access as _ca

        _viol = _ca.validate_config()
        if _viol:
            logger.warning(
                "hermes-router: config load validation found %d schema violation(s)",
                _viol)

    _reg("register.config_validation", _reg_validate)
    # P6 §2.7: seam liveness probe — register the fire counters (no extra
    # middleware; the on_* entries increment via seam_probe_fire).
    _reg("register.seam_probe", lambda: _t.seam_probe_register(ctx))
