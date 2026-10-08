"""core/telemetry.py — L0 route telemetry (proposal §2.5, §P1).

Owns:
    log_route / _impl — the route log writer, MOVED verbatim from
        dispatcher_knobs._log_route (behavior byte-identical; the config read
        still honors the live package `_cfg` patch surface — see patchpoints).
    isolate(gate, fn, on_fail=...) — uniform fail-isolation helper,
        INTRODUCED here but NOT yet mandated (§2.5 migration is P7).
    DEFAULT_LOG_PATH / DEFAULT_LOG_MAX_BYTES / _LOG_LOCK — moved ownership
        from the package hub (hermes_router/__init__ aliases these).

Patch surface (core/patchpoints.py is the one legal monkeypatch surface):
    1. patchpoints.log_route_override (module attr — monkeypatch.setattr).
    2. the package hub attribute `hermes_router._log_route` — the surface the
       existing test suite patches (54 sites); resolved late-bind at call
       time so NO import cycle exists (runtime lookup only). When the conftest
       patchpoint migration lands (§1.4), path 1 becomes the only surface and
       path 2 is deleted.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Callable, Dict, Optional, TypeVar

from ..core import patchpoints

T = TypeVar("T")

# H4 (reviewer audit 2026-09-02): default log under HERMES_HOME
# (profile-scoped) instead of shared cross-profile /tmp. Config override
# still wins. Moved from hermes_router/__init__ (behavior identical).
def _pchome() -> str:
    try:
        from hermes_constants import get_hermes_home

        return str(get_hermes_home())
    except Exception:  # noqa: BLE001
        return os.environ.get("HERMES_HOME", "")


DEFAULT_LOG_PATH = os.path.join(os.path.abspath(_pchome()), "uncensored-router.log")
DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
_LOG_LOCK = threading.Lock()


def _package() -> Optional[Any]:
    """Late-bind the package hub at CALL time — no import cycle (core may
    never import hermes_router at module level)."""
    import sys as _sys

    name = __package__.rsplit(".", 1)[0] if "." in __package__ else __package__
    return _sys.modules.get(name)


def _config() -> Dict[str, Any]:
    """Config read for telemetry. Honors the hub's _cfg (the live test patch
    surface); falls back to the core accessor."""
    try:
        pkg = _package()
        f = getattr(pkg, "_cfg", None) if pkg is not None else None
        if callable(f):
            cfg = f()
            if isinstance(cfg, dict):
                return cfg
    except Exception:  # noqa: BLE001
        pass
    try:
        from .core import config_access

        sec = config_access.router_section()
        return sec if isinstance(sec, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _log_path() -> str:
    cfg = _config()
    try:
        default = DEFAULT_LOG_PATH
    except Exception:  # noqa: BLE001
        default = ""
    return str(cfg.get("log_path") or default)


def _log_max_bytes() -> int:
    cfg = _config()
    try:
        return int(cfg.get("log_max_bytes", DEFAULT_LOG_MAX_BYTES))
    except (TypeError, ValueError):
        return DEFAULT_LOG_MAX_BYTES


def _impl(event: str, **fields: Any) -> None:
    """Append one line to the route log. Never raises. Rotates past max bytes.
    Body moved verbatim from dispatcher_knobs._log_route @590bda3."""
    if not bool(_config().get("log_routes", True)):
        return
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = [f"{k}={v}" for k, v in fields.items() if v is not None]
    line = f"{ts} {event} " + " ".join(parts) + "\n"
    path = _log_path()
    max_bytes = _log_max_bytes()
    try:
        with _LOG_LOCK:
            try:
                if os.path.exists(path) and os.path.getsize(path) > max_bytes:
                    os.replace(path, path + ".1")
            except OSError:
                pass
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
            # H4 (reviewer audit 2026-09-02): route logs carry session ids and
            # content lengths — owner-only. Best-effort; never breaks routing.
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    except OSError:
        pass


def log_route(event: str, **fields: Any) -> None:
    """Route telemetry entry point (patchpoint-aware dispatcher).

    Resolution order: patchpoints.log_route_override -> the package hub
    attribute `hermes_router._log_route` when it has been patched away from
    the default binding (the live test patch surface; late-bound at call
    time, no import cycle) -> _impl. Never raises here; _impl never raises.
    """
    # NOTE: the dispatcher does NOT swallow — the CALL SITES own their
    # isolation (existing try/except + _obs_warn fail-loud contract). A
    # blanket except here would convert a patched emitter's raise into a
    # silent no-op (rider 19 item-1 fail-loud pin would die).
    override = getattr(patchpoints, "log_route_override", None)
    if callable(override):
        return override(event, **fields)
    pkg = _package()
    target = getattr(pkg, "_log_route", None) if pkg is not None else None
    if callable(target) and target is not _impl:
        return target(event, **fields)
    return _impl(event, **fields)


import logging

logger = logging.getLogger("router.swallow")

_SWALLOW_COUNT: Dict[str, int] = {}
_SWALLOW_LOCK = threading.Lock()


def swallow_count() -> Dict[str, int]:
    """Cumulative isolate() swallow counter, by gate — surfaced via
    /router diag + router_status (§2.5)."""
    with _SWALLOW_LOCK:
        return dict(_SWALLOW_COUNT)


def record_swallow(gate: str, exc: BaseException, **fields: Any) -> None:
    """Record one swallow at a hand-rolled boundary (P6). Emits the same
    `router.swallow` warning row + per-gate counter as isolate() — used by
    migrated except-sites that keep their own catch/return shape (the
    exception boundary is preserved exactly; only the SILENCE dies)."""
    with _SWALLOW_LOCK:
        _SWALLOW_COUNT[gate] = _SWALLOW_COUNT.get(gate, 0) + 1
    try:
        logger.warning("router.swallow gate=%s err=%r %s", gate, exc,
                       " ".join(f"{k}={v}" for k, v in fields.items()
                                if v is not None))
    except Exception:  # noqa: BLE001
        pass


_SEAM_LOCK = threading.Lock()
_SEAM_FIRES: Dict[str, int] = {}
_SEAM_REGISTERED = False
_TURN_SEAMS = ("llm_request", "llm_execution", "transform_llm_output",
               "transform_tool_result")


def seam_probe_register(ctx: Any) -> None:
    """§2.7 seam liveness probe — register each plugin seam with an atomic
    fire counter. The on_* entry points increment via seam_probe_fire(); a
    zero-fire seam after the first turn-identity advance emits
    `seam_dead_on_arrival`. No extra middleware is registered (the probe
    must not alter the host's middleware chain)."""
    try:
        for seam in _TURN_SEAMS:
            with _SEAM_LOCK:
                _SEAM_FIRES.setdefault(seam, 0)
    except Exception:  # noqa: BLE001 — the probe never breaks registration
        pass


def seam_probe_fire(seam: str) -> None:
    with _SEAM_LOCK:
        _SEAM_FIRES[seam] = _SEAM_FIRES.get(seam, 0) + 1


def seam_fires() -> Dict[str, int]:
    """Snapshot of the §2.7 seam fire counters (/router diag + router_status)."""
    with _SEAM_LOCK:
        return dict(_SEAM_FIRES)


def seam_probe_maybe_advance() -> None:
    """Turn-identity advanced (state.record_last_seen): run the §2.7
    dead-seam check ONCE per process (first advance only)."""
    global _SEAM_REGISTERED
    if _SEAM_REGISTERED:
        return
    _SEAM_REGISTERED = True
    seam_dead_on_arrival_check()


def seam_dead_on_arrival_check() -> Dict[str, int]:
    """Called after the first turn-identity advance: any registered seam
    with ZERO fires emits `seam_dead_on_arrival` and is returned (surfaced
    via /router diag + router_status). Once-per-process."""
    with _SEAM_LOCK:
        zero = [s for s, n in _SEAM_FIRES.items() if n == 0]
    for s in zero:
        try:
            logger.warning("seam_dead_on_arrival seam=%s", s)
        except Exception:  # noqa: BLE001
            pass
        try:
            _tlm_log("POST", event_detail="seam_dead_on_arrival", seam=s)
        except Exception:  # noqa: BLE001
            pass
    return {s: _SEAM_FIRES.get(s, 0) for s in _TURN_SEAMS}


def _tlm_log(event: str, **fields: Any) -> None:
    log_route(event, **fields)


def isolate(gate: str, fn: Callable[[], T], *, on_fail: Any = "pass",
            **fields: Any):
    """Uniform fail-isolation (§2.5). ALWAYS emits a swallow log row and
    increments the counter — isolation and silence are no longer the same
    code path.

    on_fail: "pass" (-> None), "raise" (re-raise), "default:<value>" (the
    literal value after the colon, evaluated as a Python literal), or any
    other value returned as-is. The exception boundary is preserved exactly:
    the wrapped except is the same catch the call site had."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — this IS the isolation boundary
        with _SWALLOW_LOCK:
            _SWALLOW_COUNT[gate] = _SWALLOW_COUNT.get(gate, 0) + 1
        try:
            logger.warning("router.swallow gate=%s err=%r %s", gate, exc,
                           " ".join(f"{k}={v}" for k, v in fields.items()
                                    if v is not None))
        except Exception:  # noqa: BLE001
            pass
        if on_fail == "raise":
            raise
        if isinstance(on_fail, str) and on_fail.startswith("default:"):
            import ast as _ast

            try:
                return _ast.literal_eval(on_fail[len("default:"):])
            except Exception:  # noqa: BLE001
                return None
        if on_fail == "pass":
            return None
        return on_fail
