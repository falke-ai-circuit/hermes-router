"""m60 — aux/anchor endpoint authority: ONE resolver, named empties, pair check.

Contract (conductor m60 mandate):
1. semantic_classifier + anchor build base+key ONLY through
   host_provider_endpoint() / _core_runtime() — no parallel env or config
   chase. fallback_client() is streamlined: core aux client (if the host
   provides one) -> ONE core-runtime-shaped client; the plugin-explicit
   base override keeps its key from the SAME runtime dict. Key value rides
   the resolution — base+key paired by construction (the m60 401 was a
   base/key mismatch across lookups).
2. Empty resolution fails LOUD: resolver_empty (lane-named), never a
   silent no_client.
3. pair_check_once() — one-at-init base/key pairing check logging
   pair_ok=true/false exactly once per process.
4. Anchor endpoints resolved from core carry core's key VALUE as the
   preferred credential (env name kept as diagnostic only).

All functions never raise; failure surfaces are named strings.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_PLACEHOLDER_VALUES = {
    "", "not-needed", "none", "null", "placeholder", "changeme",
    "your-key-here",
}

_PAIR_CHECKED = False
_PAIR_OK = False
_PAIR_DETAIL = ""


def _usable_key(value: Any) -> str:
    try:
        v = str(value or "").strip()
    except Exception:  # noqa: BLE001
        return ""
    if v.lower() in _PLACEHOLDER_VALUES:
        return ""
    return v


def resolver_state() -> Optional[Dict[str, Any]]:
    """The auxiliary resolution state, read from core — module contract:

    host_provider_endpoint() for base, the SAME core runtime dict for the
    api_key. Strings and lengths only, never key bytes. None when core
    resolves nothing (the caller names resolver_empty). Never raises.
    """
    try:
        from . import provider_derivation as pd

        base, model, name = pd.host_provider_endpoint()
        base = str(base or "").strip()
        if not base:
            return None
        key = _usable_key((pd._core_runtime() or {}).get("api_key"))
        return {
            "base": base,
            "model": str(model or "").strip(),
            "provider": str(name or "").strip(),
            "key_ok": bool(key),
            "key_len": len(key),
        }
    except Exception:  # noqa: BLE001 — resolution must never raise
        return None


def pair_check_once() -> Tuple[bool, str]:
    """The ONE-at-init base/key pairing check (mandate #3).

    Logs `endpoint_pair_check pair_ok=true|false <detail>` exactly once per
    process (module-level once-guard). pair_ok=True means base and key came
    from the SAME core resolution — the m60 401 shape (base one lookup, key
    another) is impossible by construction. A prior check consumes the
    guard (callers read the cached result). Never raises.
    """
    global _PAIR_CHECKED, _PAIR_OK, _PAIR_DETAIL
    try:
        if _PAIR_CHECKED:
            return _PAIR_OK, _PAIR_DETAIL
        state = resolver_state()
        if state is None:
            return False, "no_core_resolution"
        _PAIR_CHECKED = True
        _PAIR_OK = bool(state.get("key_ok"))
        if _PAIR_OK:
            _PAIR_DETAIL = ("base=%s provider=%s model=%s"
                            % (state.get("base"), state.get("provider"),
                               state.get("model")))
        else:
            _PAIR_DETAIL = ("core resolved base %r but the api_key is "
                            "empty/placeholder — pair check FAILED; key and "
                            "base must come from ONE resolution"
                            % state.get("base"))
        logger.info("endpoint_pair_check pair_ok=%s %s",
                    "true" if _PAIR_OK else "false", _PAIR_DETAIL)
        return _PAIR_OK, _PAIR_DETAIL
    except Exception as exc:  # noqa: BLE001 — diagnostics never raise
        return False, "pair_check_error: %s" % str(exc)[:120]
