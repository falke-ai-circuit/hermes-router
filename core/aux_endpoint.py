"""m57-B/D → m58-CORRECTION: aux endpoint asked through Hermes core.

FRESH-INSTALL CONTRACT (m57 + m58-CORRECTION): on ANY virgin Hermes install
the plugin's aux and anchor lanes work without any plugin-side provider
knowledge — no .secrets file, no loopback, no vendor hostname, no env
contract reimplementation. The plugin ASKS Hermes core:

  agent.auxiliary_client.get_text_auxiliary_client   (core's own aux client)
  hermes_cli.runtime_provider.resolve_runtime_provider (core's main runtime)
  agent.auxiliary_client.resolve_provider_client      (client build)

One authority: if Hermes can call a model, the plugin's aux can. Explicit
plugin lane config (providers.nous.base_url) still wins FIRST — that is a
plugin-lane override, not a reimplementation of host resolution.

scheme_failure() is the D half: a lane URI whose scheme cannot authenticate
or resolve returns a NAMED reason plus a detection-safe fallback hint —
never a bare None that downstream renders as an ordinary model error.
Never raises.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# m57-D: the detection-safe fallback named on every scheme failure — the ONE
# scheme that always authenticates via env (OPENROUTER_API_KEY), or any
# configured providers.<name> custom scheme.
FALLBACK_HINT = ("detection-safe fallback: openrouter://<model> with "
                 "OPENROUTER_API_KEY in the environment, or any configured "
                 "providers.<name>.base_url scheme as <name>://<model>")


def _ca():
    from . import config_access as _ca

    return _ca


def _explicit_base() -> str:
    """Layer 1: the plugin's own provider base (m57-C alias included).

    ONE static literal read: the schema declares providers.nous.base_url
    with legacy_of="base_url", so get() walks BOTH spellings (grouped path
    first, the deployed old alias second).
    """
    try:
        ca = _ca()
        return str(ca.get("providers.nous.base_url", "") or "").strip()
    except Exception:  # noqa: BLE001 — config read must never raise
        return ""


def fallback_base_url() -> str:
    """The resolved aux base: explicit plugin provider override first, then
    whatever Hermes CORE's runtime resolver yields for the host's own
    provider. '' when nothing resolves — callers fail open (no egress).
    Never raises. Never contains an invented endpoint: the only URL this
    function can return from the core path is the base core itself dials."""
    try:
        explicit = _explicit_base()
        if explicit:
            return explicit
        from . import provider_derivation as pd

        return str(pd.host_provider_endpoint()[0] or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def fallback_client(timeout_seconds: float = 30.0):
    """m58-CORRECTION: the ONE aux client builder — built by Hermes core
    itself. Order: core's own aux surface (get_text_auxiliary_client — same
    provider/auth/api_mode the agent uses for side-calls), then core's MAIN
    runtime resolution shaped into a client (covers host shapes core's aux
    auto-detect does not resolve, e.g. env-declared OPENAI base). Plugin
    explicit base override (providers.nous.base_url) pairs with the core
    runtime's own api_key so base+key stay same-provider by construction.
    None when core resolves nothing — callers fail open, no egress. Never
    raises. Consumers: semantic_judge._fallback_client (delegates),
    semantic_classifier._hermes_aux_call (no_client rescue)."""
    try:
        from . import provider_derivation as pd

        # 1. core's own aux client (the exact surface the agent itself uses)
        client, _model = pd._core_client()
        if client is not None:
            return client

        # 2. core's MAIN runtime resolution -> client via core's builder
        rt = pd._core_runtime()
        if not rt:
            return None
        provider = str(rt.get("provider") or "").strip()
        if not provider:
            return None
        from agent.auxiliary_client import resolve_provider_client

        client2, _m = resolve_provider_client(
            provider,
            model=str(rt.get("model") or "") or None,
            explicit_base_url=str(rt.get("base_url") or "") or None,
            explicit_api_key=rt.get("api_key") or None,
            api_mode=str(rt.get("api_mode") or "") or None,
        )
        if client2 is not None:
            return client2

        # 3. plugin explicit base override + the runtime's OWN key (m58-B:
        # base and key always from the same provider — core's runtime dict
        # carries the api_key its base authenticates with)
        explicit = _explicit_base()
        api_key = rt.get("api_key")
        if explicit and api_key:
            from openai import OpenAI

            return OpenAI(base_url=explicit, api_key=str(api_key),
                          timeout=float(timeout_seconds), max_retries=0)
        return None
    except Exception:  # noqa: BLE001 — client build must never raise into a lane
        return None


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


def missing_input_report() -> str:
    """m58-C: ONE explicit line naming WHY a lane has no endpoint — what
    Hermes core resolved and what was missing. The every-lane-silent
    signature (aux reason=no_client x8, no banner) is undiagnosable from a
    transcript; this line names the state. '' when the resolution works, so
    healthy hosts stay silent. Never raises."""
    try:
        if fallback_base_url():
            return ""
        parts = []
        try:
            from .core import provider_derivation as _pd

            rt = _pd._core_runtime()
            if rt:
                parts.append("core runtime resolved provider=%r base=%r "
                             "but no usable aux client could be built"
                             % (rt.get("provider"), rt.get("base_url")))
            else:
                cfg = _pd._host_full_config()
                if cfg:
                    parts.append("hermes core resolved NO runtime provider "
                                 "(model=%r, providers=%s — core could not "
                                 "authenticate the host provider)"
                                 % (cfg.get("model"),
                                    "yes" if isinstance(cfg.get("providers"),
                                                        dict)
                                    and cfg.get("providers") else "absent"))
                else:
                    parts.append("hermes core resolved NO runtime provider "
                                 "(host config unreadable/empty)")
        except Exception as _exc:  # noqa: BLE001 — the report must never raise
            parts.append("core resolution probe errored: %s" % str(_exc)[:120])
        return ("router_endpoint_unresolved — no lane endpoint available: "
                + "; ".join(parts)
                + ". Configure the host provider so Hermes core can resolve "
                "it (hermes model / providers block / provider credentials) "
                "for the aux+anchor lanes to fire.")
    except Exception:  # noqa: BLE001 — diagnostics never raise
        return ""


def scheme_failure(uri: str, role: str = "") -> Tuple[str, str]:
    """Why a lane URI cannot be used, with its detection-safe fallback hint.
    Returns (reason, hint). reason == "" when the URI resolves normally.
    An unresolvable scheme is a NAMED failure carrying the fallback hint —
    it must never present downstream as an ordinary model error. Never
    raises."""
    reason = ""
    hint = ""
    try:
        from .. import anchor_chain

        ep = anchor_chain.parse_anchor_uri(str(uri or ""), str(role or "primary"))
    except Exception as _exc:  # noqa: BLE001 — named failure, never raise
        # The import cycle (anchor_chain's empty-base path imports this
        # module, which imports anchor_chain back) can surface as an
        # ImportError here WITHOUT being a scheme defect — the URI itself
        # failed to resolve either way. Name it unresolvable, never a
        # pseudo-resolution error.
        ep = None
    if ep is None:
        reason = "unresolvable_scheme"
        hint = (FALLBACK_HINT + " — the scheme must resolve via "
                "anchor_chain schemes or providers.custom")
    return reason, hint
