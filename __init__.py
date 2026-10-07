"""uncensored-router plugin — wiring for pre-router middleware + post-router hook.

Spec §5 (pre-router, llm_request middleware) + §6 (post-router,
transform_llm_output hook) + §6.1/§6.2 (shared state) + §9 (logging).

Register(ctx) wires:
  ctx.register_middleware("llm_request", on_llm_request)
  ctx.register_hook("transform_llm_output", on_transform_llm_output)

All errors are swallowed into no-op pass-through ({}/None) — a router failure
must never crash the agent turn (spec §5/§6 constraints).
"""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from . import classifier
from . import method_card
from . import persona_card
from . import router
from . import semantic_classifier
from . import refusal_doctrine
from .core import canonical
from .core import config_access  # noqa: F401 — re-export (single config accessor)
from .core import decisions  # noqa: F401 — re-export
from .core import routing_caps  # noqa: F401 — re-export
from .core import session_store
from .core import state
from .core import telemetry
from .core import usage_ledger  # noqa: F401 — re-export
from .core.telemetry import _impl as _log_route  # moved impl (P1)


from . import anchor_chain
from . import anchor_exec
from . import complexity
from . import router_core
from .api import config_writer  # noqa: F401 — P3a: api/ move
from .api import commands  # noqa: F401
from .api import commands_config  # noqa: F401
from .api import commands_diag  # noqa: F401
from .api import commands_runtime  # noqa: F401
from .api import router_tools  # noqa: F401

# P1: legacy-path module aliases in sys.modules so EVERY import spelling of
# the moved modules keeps working (package attrs, `import hermes_router.X`,
# `from hermes_router.X import Y`, monkeypatch string targets) — one shared
# module object per module (conftest Trap 5: single-namespace discipline).
# Registered here, not lazily, so even attribute access before first use
# resolves identically. This is the back-compat shim; direct `hermes_router.core.X`
# imports are the new canonical spelling (see scripts/rewrite_imports.py).
import sys as _sys
for _m in (canonical, config_access, decisions, routing_caps,
           session_store, state, telemetry, usage_ledger, config_writer,
           commands, commands_config, commands_diag, commands_runtime,
           router_tools):
    _sys.modules.setdefault(f"{__name__}.{_m.__name__.rsplit('.', 1)[-1]}", _m)
from .core.telemetry import _impl as _log_route  # moved impl (P1)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# H4 (reviewer audit 2026-09-02): default log under HERMES_HOME (profile-scoped)
# instead of shared cross-profile /tmp. Config override still wins.
# P1: ownership MOVED to core.telemetry (identical computation via
# hermes_constants.get_hermes_home / HERMES_HOME env fallback); the hub keeps
# the same names as aliases for back-compat patch targets.
DEFAULT_LOG_PATH = telemetry.DEFAULT_LOG_PATH
DEFAULT_LOG_MAX_BYTES = telemetry.DEFAULT_LOG_MAX_BYTES
DEFAULT_PENDING_TTL = 300

_LOG_LOCK = telemetry._LOG_LOCK


























# ---------------------------------------------------------------------------
# Stage-2 (semantic) knobs — blueprint v2 §2/§5. All reads go through
# _classification_cfg() so tests patch _cfg exactly like stage-1.
# ---------------------------------------------------------------------------

SEMANTIC_MIN_LEN_NO_OPENER = 400  # gate arm (b): short responses are cheap aux probes










# ---------------------------------------------------------------------------
# v3.2.2 render delivery cap — character cap on the DELIVERED render text
# (messaging-platform seam). Generation budget (chain max_tokens) is NOT
# touched: the thinking-model floor makes lower budgets produce empty renders
# with finish=length. Default 0 = no truncation (back-compat).
# ---------------------------------------------------------------------------

RENDER_TRUNCATION_MARKER = "\n\n[render truncated at platform limit]"












# ---------------------------------------------------------------------------
# Logging (spec §9) — append-only file, one line per route, NO content logged.
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# Message extraction helpers
# ---------------------------------------------------------------------------










# ---------------------------------------------------------------------------
# v3.6 Phase 0 taps (write-only detectors; zero behavior change)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# R5 leg 1 decomposition: PRE-lane taps + ordered passes moved to
# dispatcher_pre.py. Thin re-exports keep the public surface (gateway + tests
# import these names in place). Late-bound through dispatcher_pre so tests
# monkeypatching plugin._cfg/_log_route/etc. still reach the moved code.
# ---------------------------------------------------------------------------

from . import dispatcher_knobs as _dispatcher_knobs
from . import dispatcher_pre as _dispatcher_pre  # noqa: E402
from . import route_gate as _route_gate  # noqa: E402


































# ---------------------------------------------------------------------------
# R5 leg 2 decomposition: POST-lane stage-2 semantic family + refusal-shape
# + substance-frame builders moved to dispatcher_post.py. Thin re-exports
# keep the public surface (gateway + tests import these names in place).
# ---------------------------------------------------------------------------

from . import dispatcher_post as _dispatcher_post  # noqa: E402

SEMANTIC_MIN_LEN_NO_OPENER = _dispatcher_post.SEMANTIC_MIN_LEN_NO_OPENER
_DECLINE_OPENERS = _dispatcher_post._DECLINE_OPENERS  # noqa: F401 — compat alias
_DECLINE_RE = None  # compiled lazily (kept for _gate_semantic compat)














SUBSTANCE_FRAME_ASK_CAP = _dispatcher_post.SUBSTANCE_FRAME_ASK_CAP
SUBSTANCE_FRAME_ASK_SUFFIX = _dispatcher_post.SUBSTANCE_FRAME_ASK_SUFFIX








# ---------------------------------------------------------------------------
# Anchored execution — llm_execution middleware (v3.0.0 complexity lane)
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


# P3a: the orchestrators + hub helpers live in gate/orchestration.py (lifted
# verbatim); the hub re-binds them so the patch surface (plugin.<name>) is
# unchanged. on_* bound explicitly (register() wires them); every other
# moved name resolves through __getattr__ (lazy, cached).
from .gate import orchestration

on_llm_request = orchestration.on_llm_request
on_llm_execution = orchestration.on_llm_execution
on_transform_llm_output = orchestration.on_transform_llm_output
on_transform_terminal_output = orchestration.on_transform_terminal_output

_MOVABLE_ORCH_NAMES = tuple(
    n for n in dir(orchestration)
    if not n.startswith("__") and n != "_hub")

def __getattr__(name: str):
    """P3a lazy export of the lifted names (dispatch: registration +
    __getattr__ for test-patched names only). Cached into globals so the
    attribute becomes real after first resolution (monkeypatch restore-safe:
    monkeypatch getattrs before setattr, which materializes the binding)."""
    try:
        value = getattr(orchestration, name)
    except AttributeError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}") from None
    globals()[name] = value
    return value


def register(ctx) -> None:
    """Wire hooks + middleware + tools. Registration errors are isolated
    (P6 stage-1: the registration path migrates to core.telemetry.isolate —
    a failure emits the `router.swallow` warning row + per-gate counter
    surfaced via /router diag + router_status, never raised; a broken
    registration would disable the whole plugin in one profile)."""
    from .core import telemetry as _t

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
        from . import router_tools

        router_tools.register(ctx)

    _reg("register.router_tools", _reg_tools)
    # v3.5.0: /router chat command surface — LCM 3-branch pattern, env-gated
    # (HERMES_ROUTER_ENABLE_SLASH_COMMAND, default off). commands.
    # register_slash_command performs the collision self-check and the
    # flagship gateway-authz posture self-check (blueprint 6b.1) internally
    # and logs the one-line posture verdict.
    def _reg_slash() -> None:
        from . import commands

        commands.register_slash_command(ctx)

    _reg("register.slash_command", _reg_slash)
    # P4 (proposal §2.3): LOAD-TIME VALIDATION pass — validate the whole
    # resolved config section once at plugin load; schema violations
    # (partial blocks / type mismatches) emit fail-loud telemetry rows
    # instead of surfacing as silent Nones mid-turn. Never raises.
    def _reg_validate() -> None:
        from .core import config_access as _ca

        _viol = _ca.validate_config()
        if _viol:
            logger.warning(
                "hermes-router: config load validation found %d schema violation(s)",
                _viol)

    _reg("register.config_validation", _reg_validate)
    # P6 §2.7: seam liveness probe — register the fire counters (no extra
    # middleware; the on_* entries increment via seam_probe_fire).
    _reg("register.seam_probe", lambda: _t.seam_probe_register(ctx))
