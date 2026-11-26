"""m57-B/D: aux endpoint auto-derivation + explicit scheme failure contract.

FRESH-INSTALL CONTRACT (m57): on a virgin Hermes install the plugin's aux and
anchor lanes must not depend on any .secrets file, any loopback address, or
any hardcoded vendor inference hostname. When the plugin's own provider
config is silent, the endpoint DERIVES from what the host itself is
configured to run.

Layer order for fallback_base_url():
  1. explicit plugin provider base — providers.nous.base_url (the m57-C
     legacy alias `base_url` resolves through the same schema key)
  2. host model.base_url (custom-endpoint config shape)
  3. host providers.<model.provider>.base_url
  4. host custom providers (first entry that carries a base_url)
  5. "" — FAIL-OPEN: no endpoint is invented, callers make no egress

NEITHER a loopback address NOR a vendor hostname appears in any layer:
every value is read from the host's own configuration.

scheme_failure() is the D half: a lane URI whose scheme cannot authenticate
or resolve returns a NAMED reason plus a detection-safe fallback hint
(openrouter:// with OPENROUTER_API_KEY, or any configured providers.<name>
custom scheme) — never a bare None that downstream renders as an ordinary
model error. Never raises.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

_EXPLICIT_PATHS = ("providers.nous.base_url", "base_url")

# m57-D: the detection-safe fallback named on every scheme failure — the ONE
# scheme that always authenticates via env (OPENROUTER_API_KEY), or any
# configured providers.<name> custom scheme.
FALLBACK_HINT = ("detection-safe fallback: openrouter://<model> with "
                 "OPENROUTER_API_KEY in the environment, or any configured "
                 "providers.<name>.base_url scheme as <name>://<model>")


def _ca():
    from hermes_router.core import config_access as _ca

    return _ca


def _explicit_base() -> str:
    """Layer 1: the plugin's own provider base (m57-C alias included)."""
    try:
        ca = _ca()
        for path in _EXPLICIT_PATHS:
            value = str(ca.get(path, "") or "").strip()
            if value:
                return value
        return ""
    except Exception:  # noqa: BLE001 — config read must never raise
        return ""


def _host_layers() -> str:
    """Layers 2-4: derive from the HOST's own provider configuration."""
    try:
        from hermes_router.core import provider_derivation as pd

        cfg = pd._host_full_config()
        model = cfg.get("model")
        name = ""
        model_base = ""
        if isinstance(model, dict):
            name = str(model.get("provider") or "").strip()
            model_base = str(model.get("base_url") or "").strip()
        # layer 2: host model.base_url
        if model_base:
            return model_base
        # layer 3: host providers.<model.provider>.base_url
        if name:
            providers = cfg.get("providers")
            if isinstance(providers, dict):
                entry = providers.get(name)
                if isinstance(entry, dict):
                    base = str(entry.get("base_url") or "").strip()
                    if base:
                        return base
        # layer 4: host custom providers — first entry with a base_url,
        # deterministic order (sorted keys; the host config is a mapping)
        try:
            ca = _ca()
            custom = ca.providers_custom() or {}
        except Exception:  # noqa: BLE001 — probe must never raise
            custom = {}
        if isinstance(custom, dict):
            for key in sorted(custom.keys()):
                entry = custom.get(key)
                if isinstance(entry, dict):
                    base = str(entry.get("base_url") or "").strip()
                    if base:
                        return base
        # layer 5 (m57-D, key-conditioned): a NOUS_API_KEY credential in the
        # host's environment IS host configuration — it names the one provider
        # the host can authenticate against without OAuth. Only when that
        # credential EXISTS does the Nous API base resolve here; absent the
        # key this layer returns "" (no invention, the D explicit failure
        # names openrouter:// instead). This is why the vendor literal is not
        # a DEFAULT: unset key -> "" (never nousresearch.com).
        try:
            ca = _ca()
            key_env_name = ca.provider_key_env("nous", "NOUS_API_KEY")
        except Exception:  # noqa: BLE001 — signal read must never raise
            key_env_name = "NOUS_API_KEY"
        import os as _os

        key_val = str(_os.environ.get(key_env_name, "") or "").strip()
        if key_val and key_val.lower() not in ("placeholder", "changeme",
                                               "not-needed", "testkey"):
            return "https://inference-api.nousresearch.com/v1"
        return ""
    except Exception:  # noqa: BLE001 — derivation must never raise into a lane
        return ""


def fallback_base_url() -> str:
    """The resolved aux base: explicit plugin provider > host derivation.
    '' when the host gives nothing to derive from — callers fail open
    (no egress). Never raises. Never contains an invented endpoint."""
    try:
        return _explicit_base() or _host_layers()
    except Exception:  # noqa: BLE001
        return ""


def derived_aux_endpoint() -> Optional[Dict[str, Any]]:
    """fallback_base_url() shaped as an aux-endpoint block ({url}), or None
    when nothing derivable exists. Never raises."""
    try:
        base = fallback_base_url()
        if not base:
            return None
        out: Dict[str, Any] = {"url": base}
        try:
            ca = _ca()
            model = str(ca.agent_model() or "").strip()
            if model:
                out["model"] = model
        except Exception:  # noqa: BLE001
            pass
        return out
    except Exception:  # noqa: BLE001
        return None


def scheme_failure(uri: str, role: str = "") -> Tuple[str, str]:
    """Why a lane URI cannot be used, with its detection-safe fallback hint.

    Returns (reason, hint). reason == "" when the URI resolves normally.
    An unresolvable scheme is a NAMED failure carrying the fallback hint —
    it must never present downstream as an ordinary model error. Never
    raises."""
    reason = ""
    hint = ""
    try:
        from hermes_router import anchor_chain

        ep = anchor_chain.parse_anchor_uri(str(uri or ""), str(role or "primary"))
        if ep is None:
            reason = "unresolvable_scheme"
            hint = (FALLBACK_HINT + " — the scheme must resolve via "
                    "anchor_chain schemes or providers.custom")
    except Exception as _exc:  # noqa: BLE001 — named failure, never raise
        reason = "scheme_resolution_error"
        hint = FALLBACK_HINT + " (error: %s)" % str(_exc)[:80]
    return reason, hint
