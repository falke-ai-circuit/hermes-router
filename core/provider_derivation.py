"""m57-B: derive the aux/model endpoint from the HOST's own provider.

Portability contract (m57): on a virgin Hermes install the plugin must never
fall back to any vendor inference hostname or a loopback address baked into
plugin code. When a lane needs an endpoint and its explicit config is silent,
it DERIVES the endpoint from what the host itself is configured to run:

    host model.provider (:name)  ->  providers.<name>.base_url  (host config)
                                 ->  model.base_url             (custom endpoint form)
                                 ->  hermes_cli overlay override (host's own resolver)
    host model.default           ->  the model id

Precedence at every call site: explicit lane config > this derivation.
Derivation failure -> ("" , "", "") and the caller fails open (no egress) —
NEITHER a loopback NOR a vendor hostname is invented here.

Read-only over the host config (plugin section via core.config_access for the
explicit layers; the host config itself is read fresh, never cached — the
gateway may rewrite it between turns). Never raises.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _host_full_config() -> Dict[str, Any]:
    """The HOST Hermes config (top-level model/providers blocks). Fresh read;
    hermes_cli first, the profile-co-located yaml as rescue. {} on miss."""
    try:
        from . import config_access as _ca

        cfg = _ca.load_full_config()
        if isinstance(cfg, dict) and cfg:
            return cfg
    except Exception:  # noqa: BLE001
        pass
    try:
        import yaml  # type: ignore[import-untyped]

        from . import config_access as _ca

        path = _ca._coLocatedPath()
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
            return cfg if isinstance(cfg, dict) else {}
    except Exception:  # noqa: BLE001
        pass
    return {}


def host_provider_provider() -> str:
    """The host's active provider label (model.provider). '' on any miss."""
    try:
        cfg = _host_full_config()
        model = cfg.get("model")
        if isinstance(model, dict):
            return str(model.get("provider") or "").strip()
        if isinstance(model, str):
            return ""
        return ""
    except Exception:  # noqa: BLE001
        return ""


def host_provider_model() -> str:
    """The host's active model id (model.default / model.model / model str)."""
    try:
        cfg = _host_full_config()
        model = cfg.get("model")
        if isinstance(model, dict):
            return str(model.get("default") or model.get("model") or "").strip()
        if isinstance(model, str):
            return model.strip()
        return ""
    except Exception:  # noqa: BLE001
        return ""


def _host_provider_entry(name: str) -> Dict[str, Any]:
    """The host providers block entry for `name` (verbatim key match — the
    label in model.provider IS the providers key, e.g. 'custom:nousinf')."""
    try:
        cfg = _host_full_config()
        providers = cfg.get("providers")
        if isinstance(providers, dict):
            entry = providers.get(name)
            if isinstance(entry, dict):
                return entry
    except Exception:  # noqa: BLE001
        pass
    return {}


def _overlay_base_url(name: str) -> str:
    """The host's own provider overlay base (hermes_cli.providers) — the SAME
    resolution Hermes core itself applies to the active provider. '' when the
    overlay table is unavailable or the provider has no override."""
    try:
        from hermes_cli.providers import HERMES_OVERLAYS

        overlay = HERMES_OVERLAYS.get(str(name or "").strip().lower())
        if overlay is None:
            return ""
        return str(getattr(overlay, "base_url_override", "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def host_provider_endpoint() -> Tuple[str, str, str]:
    """Derive (base_url, model, provider_name) from the HOST's own provider.

    base_url candidates, in order:
      1. host providers.<model.provider>.base_url (explicit host provider block)
      2. host model.base_url (custom-endpoint config shape)
      3. hermes_cli overlay base_url_override for the provider (the host's own
         resolver — this is what Hermes core itself would dial)
    model: host model.default. Returns ("", "", "") on any miss — callers
    fail open, no endpoint is invented. Never raises.
    """
    try:
        name = host_provider_provider()
        model = host_provider_model()
        if not name and not model:
            return "", "", ""
        base = ""
        if name:
            entry = _host_provider_entry(name)
            base = str(entry.get("base_url") or "").strip()
        if not base:
            try:
                cfg = _host_full_config()
                model_block = cfg.get("model")
                if isinstance(model_block, dict):
                    base = str(model_block.get("base_url") or "").strip()
            except Exception:  # noqa: BLE001
                base = ""
        if not base and name:
            base = _overlay_base_url(name)
        if not base:
            return "", model, name
        return base, model, name
    except Exception:  # noqa: BLE001 — derivation must never raise into a lane
        return "", "", ""


def derived_aux_endpoint() -> Optional[Dict[str, Any]]:
    """The derivation shaped as an aux-endpoint block ({url, model}) for the
    classification lane, or None when the host gives nothing to derive from.
    Never contains a loopback address or a vendor hostname BY CONSTRUCTION —
    every value comes from the host's own configuration."""
    try:
        base, model, _name = host_provider_endpoint()
        if not base:
            return None
        out: Dict[str, Any] = {"url": base}
        if model:
            out["model"] = model
        return out
    except Exception:  # noqa: BLE001
        return None


import os  # noqa: E402 — used by _host_full_config rescue path only
