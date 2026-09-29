"""R19.13 second addition (Goran directive): reflex lane modularization.

The 'decision' lane becomes the 'reflex' lane; the existing decision
behavior is reflex TYPE 'decision'. Cosmetic/structural ONLY — ZERO
behavior change: same triggers, same Jev calls, same banners, same ledger
rows. This module is the TYPE REGISTRY so future types (salience, dread,
loop — NOT built now) can plug in:

    detection entry point -> classify -> type-specific handler

Only 'decision' is registered. Unknown/unregistered signal shapes default
to SILENT: no fire, no log spam (registry_lookup returns None, classify
returns (None, None)). Fail-open everywhere: a misbehaving type handler is
skipped, never raises.

Backcompat: ledger fork_class values may be the legacy base class (e.g.
'deploy') or the new 'reflex:decision' spelling — normalize_reflex_class()
accepts both; parsers treat them as the same family.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# Type registry: name -> {"detect": fn(text, cfg) -> hit|None,
#                         "handle": fn(**kwargs) -> None}
# Registration is LAZY (decision imports are deferred) so gateway-executed
# paths keep relative-import discipline and import cycles stay impossible.
_REGISTRY: Dict[str, Dict[str, Any]] = {}
_REGISTERED = False


def register_type(name: str, detect: Any, handle: Any) -> None:
    """Register a reflex type. Idempotent; last write wins. Never raises."""
    try:
        _REGISTRY[str(name)] = {"detect": detect, "handle": handle}
    except Exception:  # noqa: BLE001 — registry never raises
        pass


def _ensure_registered() -> None:
    """One-time lazy registration of the built-in 'decision' type. The
    handler/detector ARE the existing decision.py functions — zero behavior
    change, the registry is pure structure."""
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True
    try:
        from . import decision as _decision

        # Adapter keeps the registry contract clean: detect(text, cfg).
        # (detect_v3's second positional is LEVEL, not cfg — the adapter
        # pins cfg as keyword so type handlers stay uniform.)
        register_type(
            "decision",
            lambda text, cfg=None: _decision.detect_v3(text, None, cfg=cfg),
            _decision.handle_decision_v3)
    except Exception:  # noqa: BLE001 — fail-open: unregistered = silent
        _REGISTRY.pop("decision", None)


def registry_lookup(name: str) -> Optional[Dict[str, Any]]:
    """Registry entry for a type name, or None for UNKNOWN types. Unknown =
    SILENT default (caller must not fire, must not log spam). Never raises."""
    try:
        _ensure_registered()
        entry = _REGISTRY.get(str(name or ""))
        return dict(entry) if isinstance(entry, dict) else None
    except Exception:  # noqa: BLE001
        return None


def registered_types() -> Tuple[str, ...]:
    """Registered type names (diagnostics/tests). Never raises."""
    try:
        _ensure_registered()
        return tuple(sorted(_REGISTRY.keys()))
    except Exception:  # noqa: BLE001
        return ()


def classify_signal(text: str, cfg: Optional[Dict[str, Any]] = None
                    ) -> Tuple[Optional[str], Any]:
    """Detection entry point: run each registered type's detector over the
    signal text. FIRST hit wins (one type per signal). Returns
    (type_name, hit) or (None, None) when NO registered type matches —
    the SILENT default for unknown/unregistered shapes: the caller must
    not fire and must not log. Fail-open: a detector error just skips that
    type. Never raises."""
    try:
        _ensure_registered()
        for name in sorted(_REGISTRY.keys()):
            entry = _REGISTRY[name]
            detect = entry.get("detect")
            if detect is None:
                continue
            try:
                hit = detect(text, cfg)
            except Exception:  # noqa: BLE001 — a bad detector is skipped
                hit = None
            if hit:
                return name, hit
        return None, None
    except Exception:  # noqa: BLE001 — silent default
        return None, None


def dispatch(type_name: str, **kwargs: Any) -> bool:
    """Type-specific handler dispatch. Returns True when a REGISTERED type
    handled the call; False (SILENT) for unknown types. Fail-open: handler
    errors are swallowed (the lane's own fail-open holds). Never raises."""
    try:
        entry = registry_lookup(type_name)
        if entry is None:
            return False  # unknown type: silent, no fire, no log spam
        handle = entry.get("handle")
        if handle is None:
            return False
        handle(**kwargs)
        return True
    except Exception:  # noqa: BLE001 — fail-open
        return False


def normalize_reflex_class(value: Any) -> str:
    """Ledger fork_class backcompat (requirement 3): 'reflex:decision' and
    the legacy base spelling ('decision', 'deploy', ...) normalize to the
    BASE family so readers (priors lookups, class comparisons) accept both
    old and new row values. Never raises."""
    try:
        v = str(value or "").strip()
        if v.startswith("reflex:"):
            v = v[len("reflex:"):]
        return v
    except Exception:  # noqa: BLE001
        return ""
