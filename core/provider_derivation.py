"""m57-B + m58-CORRECTION: the host's provider, asked through Hermes core.

Portability contract (m57 + m58-CORRECTION): on ANY virgin Hermes install
the plugin's aux and anchor lanes work without any plugin-side provider
knowledge. The plugin does NOT parse the host's provider declaration — not
config blocks, not env vars, not endpoint literals. It asks Hermes core's
own runtime resolver (hermes_cli.runtime_provider.resolve_runtime_provider —
the exact path the agent itself uses for its LLM calls). One authority: if
Hermes can call a model, the plugin's aux can.

    resolve_runtime_provider()  ->  {provider, base_url, api_key, model, api_mode}
    model:  host aux model config if the host declares one, else model.default
            (read through the host config, NOT re-derived here)

Precedence at every call site: explicit plugin lane config > this adapter.
Resolution failure -> ("", "", "") and the caller fails open (no egress) —
NEITHER a loopback NOR a vendor hostname is invented here. Any credential
handling (env vars, key files, OAuth state, provider pools) is HERMES
CORE's job; this module holds no endpoint literals and no env contracts.

Read-only. Never raises.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _host_full_config() -> Dict[str, Any]:
    """The HOST Hermes config (top-level model/providers blocks). Fresh read;
    hermes_cli first, the profile-co-located yaml as rescue. {} on miss.
    Kept for the model-id reader and diagnostics only — provider/endpoint
    RESOLUTION does not parse this (m58-CORRECTION)."""
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


def _core_runtime() -> Dict[str, Any]:
    """The host's resolved MAIN runtime, straight from Hermes core —
    hermes_cli.runtime_provider.resolve_runtime_provider (the seam the agent
    itself uses to dial). Empty dict when core resolves nothing or core is
    absent (bare-python unit tests). Never raises.

    The import goes through core/config_access — the tree's ONE sanctioned
    hermes_cli seam (P4 gate A2). The api_key value is deliberately NOT
    inspected here: key discovery (env, OAuth, pools) belongs to core, and
    pairing base+key from the SAME resolution is what makes a 401
    impossible by construction.
    """
    try:
        from .config_access import core_runtime_provider

        rt = core_runtime_provider()
        if isinstance(rt, dict) and rt:
            return rt
    except Exception:  # noqa: BLE001 — core absent/failed -> fail open
        pass
    return {}


def _core_client():
    """The host's own aux client, built by Hermes core itself:
    agent.auxiliary_client.get_text_auxiliary_client (the ONE aux surface
    the agent uses for its own side-calls — same provider, same auth, same
    api_mode). Returns (client, model) or (None, None). Never raises.

    NOTE (m58-CORRECTION finding): core's aux auto-detect chain returns
    (None, None) on some host shapes core's MAIN runtime resolves fine
    (an env-declared OpenAI-compatible base with a generic provider label).
    Callers fall back to the _core_runtime() shape when this returns
    nothing — still core's resolution, never plugin-side parsing."""
    try:
        from agent.auxiliary_client import get_text_auxiliary_client

        client, model = get_text_auxiliary_client()
        if client is not None:
            return client, (str(model or "").strip())
        return None, ""
    except Exception:  # noqa: BLE001
        return None, ""


def host_provider_provider() -> str:
    """The host's active provider label as CORE resolved it (aliases
    normalized: openai -> openai-api etc.). '' on any miss."""
    return str(_core_runtime().get("provider") or "").strip()


def host_provider_model() -> str:
    """The host's active model id: the host's aux model config when it
    declares one, else model.default (read through the host config — the
    model ID is host config data, not a resolution question). '' on miss."""
    try:
        cfg = _host_full_config()
        if not isinstance(cfg, dict):
            return ""
        aux = cfg.get("auxiliary")
        if isinstance(aux, dict):
            for task in ("router", "aux", "classifier", "curator"):
                task_cfg = aux.get(task)
                if isinstance(task_cfg, dict):
                    m = str(task_cfg.get("model") or "").strip()
                    if m:
                        return m
        model = cfg.get("model")
        if isinstance(model, dict):
            return str(model.get("default") or model.get("model") or "").strip()
        if isinstance(model, str):
            return model.strip()
        return ""
    except Exception:  # noqa: BLE001
        return ""


def host_provider_endpoint() -> Tuple[str, str, str]:
    """(base_url, model, provider_name) — asked through Hermes core.

    base_url/api_key: whatever core's runtime resolver yields for the
    host's own provider (config blocks, env contracts, OAuth state, pools —
    all of it is core's to interpret). model: host aux model config if
    present, else model.default. ("", "", "") when core resolves nothing —
    callers fail open, no endpoint is invented. Never raises.
    """
    try:
        rt = _core_runtime()
        base = str(rt.get("base_url") or "").strip()
        model = host_provider_model()
        name = str(rt.get("provider") or "").strip()
        return base, model, name
    except Exception:  # noqa: BLE001 — derivation must never raise into a lane
        return "", "", ""


def derived_aux_endpoint() -> Optional[Dict[str, Any]]:
    """The derivation shaped as an aux-endpoint block ({url, model}) for the
    classification lane, or None when core resolves nothing. Never contains
    a loopback address or a vendor hostname BY CONSTRUCTION — every value
    comes from Hermes core's own resolution."""
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
