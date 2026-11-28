"""lanes/resolve.py — ONE resolver for every lane's endpoint (m64, Unit A).

Why this file exists
--------------------
Before m64 each lane carried its own spelling of the same four facts: the
uncensored lane used ``chain[0].url``, the aux lane ``classification.
aux_endpoint``, the frontier lane ``anchor_chain.primary`` (a ``scheme://``
URI), the decision lane ``decision.endpoint`` + ``typesafe_endpoint``. Four
shapes, four readers, four places for a URL to be half-parsed. This module
collapses the READ side into one call:

    resolve("uncensored") -> (base_url, key, schema, model)

The declaration lives in ``core/schema.py`` (``LANES``): every lane declares
exactly ``endpoint``, ``model``, ``schema``, ``key_env`` — one field set, no
per-lane names. This module adds no behaviour to any lane; it is a pure read
surface consumed by the lane contract battery and available to callers that
want the uniform shape.

THE FOUR ENDPOINT FORMS (one key, ``endpoint``)
-----------------------------------------------
1. **OpenAI-compatible base** — a scheme-qualified host ending in a version
   segment. Used as the base verbatim; the caller appends its own chat path.
2. **Scheme pointer** — ``openrouter://<model>``. Back-compat with the
   anchor-chain URI spelling that profiles already carry. Delegated to
   ``anchor_chain.parse_anchor_uri`` so there is ONE scheme table in the
   tree, not a second copy here.
3. **Shim pointer** — a loopback host on ``SHIM_PORT`` with ``model=<role>``
   where role is ``main|aux|vision``. The role is returned AS the model: the
   shim maps role -> real model itself, and a resolver that resolved the role
   to a concrete id would be a second, staler copy of the shim's own table.
4. **Another Hermes install on this host** — any other loopback base on a
   non-shim port. Local-only by construction: no credential is required and
   an absent key is not an error.

THE PATH TRAP (the defect naive tests miss)
-------------------------------------------
Legacy keys carry a FULL chat path, not a base:

    chain[0].url = <base>/chat/completions

A resolver that returns that string unchanged makes every caller compose
``<base>/chat/completions`` + ``/chat/completions`` → a 404 that looks like a
dead provider. So ``strip_chat_path`` removes a trailing chat tail from
EVERY form (declared or legacy) before the base is returned. The trap is
pinned by ``tests/delivery/test_m64_lane_contract.py``.

Contract
--------
``resolve()`` returns a 4-tuple of ``str`` — ``(base_url, key, schema,
model)`` — and NEVER raises. An unresolvable lane returns ``("", "", schema,
model)`` (empty base = caller fails open, exactly like every other lane seam
in this plugin). An empty key is legitimate for forms 3 and 4.

Schema is DECLARED, never sniffed: a decision lane pointed at an
OpenAI-looking base still reports ``typesafe`` because the profile said so.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

# The shim's fixed loopback port. A loopback base on ANY OTHER port is form 4
# (another Hermes install), not the shim — the distinction is the port, not a
# hostname guess.
SHIM_PORT = 8680
SHIM_ROLES: Tuple[str, ...] = ("main", "aux", "vision")

# Chat tails a legacy key may carry. Stripped from every form, declared or not.
CHAT_TAILS: Tuple[str, ...] = ("/chat/completions", "/v1/chat/completions",
                               "/completions")

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})


# ---------------------------------------------------------------------------
# small helpers — every one of them is fail-open
# ---------------------------------------------------------------------------
def _schema_mod():
    """``core.schema`` (the lane declaration). Imported lazily so importing
    this module needs nothing but the stdlib. Never raises."""
    try:
        from ..core import schema as _s

        return _s
    except Exception:  # noqa: BLE001
        return None


def _config_access():
    """``core.config_access`` — the ONE config accessor (lazy for the same
    reason; also keeps a virgin-install import of this module cheap)."""
    try:
        from ..core import config_access as _ca

        return _ca
    except Exception:  # noqa: BLE001
        return None


def _lane_declaration(lane: str) -> Dict[str, str]:
    """The DECLARED default block for a lane (all four fields), or {} when the
    lane is unknown to the schema. Never raises."""
    s = _schema_mod()
    if s is None:
        return {}
    try:
        return dict(s.lane_default(lane))
    except Exception:  # noqa: BLE001
        return {}


def _lane_block(lane: str) -> Dict[str, Any]:
    """``lanes.<lane>`` from the profile. {} when absent. Never raises.

    Read as a WHOLE BLOCK off the router section (the same shape
    ``router_core.py`` uses for a named scalar read), not leaf-by-leaf through
    the typed accessor. A lane is one contract: its four fields only mean
    anything together, and ``config_access.get()`` answers for single leaves
    with per-leaf defaults — which would let a profile land a half-declared
    lane. The declaration itself lives in ``core/schema.py`` (``LANES``,
    ``LANE_FIELDS``); the profile block overlays it field by field.
    """
    ca = _config_access()
    if ca is None:
        return {}
    try:
        section = ca.router_section() or {}
        lanes = section.get("lanes") if isinstance(section, dict) else None
    except Exception:  # noqa: BLE001
        return {}
    block = (lanes or {}).get(str(lane)) if isinstance(lanes, dict) else None
    return dict(block) if isinstance(block, dict) else {}


def _legacy_block(lane: str) -> Dict[str, Any]:
    """The PRE-m64 surface for a lane, shaped into the same four field names.

    Read only when the profile has not migrated to ``lanes.<lane>``: the legacy
    keys stay where they are (this module never rewrites a config), they are
    just translated so ONE resolver can consume them.

    Read RAW off the router section, not through ``config_access.get()``: the
    typed accessor validates against SCHEMA, and two legacy shapes here do not
    match their declaration — ``anchor_chain.primary`` is declared ``dict``
    while profiles carry the string ``nous://<model>``, and
    ``classification.aux_endpoint`` is a nested block rather than a leaf. A
    typed read returns the declared default (``{}`` / ``""``) and the lane
    silently loses an endpoint that IS configured. Reading raw keeps the
    profile's actual value.

    Deliberately literal-free: a legacy read may fall back to another config
    key, never to a vendor URL baked into this file (m57 doctrine — a code
    literal endpoint is unusable on a virgin host, and it is what the M25-01
    gate exists to keep out of the shipping tree). Never raises.
    """
    ca = _config_access()
    if ca is None:
        return {}
    try:
        section = ca.router_section() or {}
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(section, dict):
        return {}

    def _sub(name: str) -> Dict[str, Any]:
        value = section.get(name)
        return value if isinstance(value, dict) else {}

    out: Dict[str, Any] = {}
    try:
        if lane == "uncensored":
            # `chain:` is the deployed spelling (a list of endpoint dicts);
            # `render.chain` is the grouped one. Both carry a FULL chat path
            # and a key FILE rather than an env var — the resolver strips the
            # path and maps the file to the `file:` form of `key_env`.
            chain = section.get("chain")
            if chain is None:
                render = _sub("render")
                chain = render.get("chain")
            entry = chain[0] if isinstance(chain, list) and chain else chain
            if isinstance(entry, dict):
                out["endpoint"] = entry.get("url") or entry.get("endpoint") or ""
                out["model"] = entry.get("model") or ""
                out["key_env"] = (entry.get("key_env")
                                  or ("file:%s" % entry["key_file"]
                                      if entry.get("key_file") else ""))
        elif lane == "aux":
            # classification.aux_endpoint = {url, model, key_env, max_tokens}.
            aux = _sub("classification").get("aux_endpoint")
            if isinstance(aux, dict):
                out["endpoint"] = aux.get("url") or aux.get("endpoint") or ""
                out["model"] = aux.get("model") or ""
                out["key_env"] = aux.get("key_env") or ""
        elif lane == "frontier":
            # anchor_chain.primary is a SCHEME URI string (form 2) — carried
            # through the same `endpoint` key like every other form.
            anchor = _sub("anchor_chain")
            out["endpoint"] = anchor.get("primary") or ""
            out["model"] = ""
            out["key_env"] = ""
        elif lane == "decision":
            dec = _sub("decision")
            out["endpoint"] = (dec.get("endpoint") or dec.get("typesafe_endpoint")
                               or "")
            out["model"] = dec.get("model") or ""
            out["key_env"] = dec.get("api_key_env") or dec.get("key_env") or ""
    except Exception:  # noqa: BLE001 — a legacy read must never break a lane
        return {}
    return {k: v for k, v in out.items() if v}


def lane_spec(lane: str) -> Dict[str, str]:
    """The EFFECTIVE four-field block for a lane: profile ``lanes.<lane>``
    over the declared defaults, with the pre-m64 surface filling only the
    fields the profile left empty. Always returns all four field names.
    Never raises."""
    declared = _lane_declaration(lane)
    block = _lane_block(lane)
    legacy = _legacy_block(lane)
    out: Dict[str, str] = {}
    for field in ("endpoint", "model", "schema", "key_env"):
        value = block.get(field)
        if value in (None, ""):
            value = legacy.get(field)
        if value in (None, ""):
            value = declared.get(field, "")
        out[field] = str(value or "").strip()
    return out


def declared_schema(lane: str) -> str:
    """The lane's schema as DECLARED — ``openai`` or ``typesafe``.

    Never inferred from the endpoint's shape: an out-of-enum value falls back
    to the lane's declared default rather than being guessed at. Never raises.
    """
    s = _schema_mod()
    allowed = tuple(getattr(s, "LANE_SCHEMAS", ("openai", "typesafe"))) if s else ("openai", "typesafe")
    value = lane_spec(lane).get("schema", "").strip().lower()
    if value in allowed:
        return value
    fallback = _lane_declaration(lane).get("schema", "") or "openai"
    return fallback if fallback in allowed else "openai"


def strip_chat_path(url: str) -> str:
    """A BASE from a URL that may carry a full chat path.

    ``<host>/<version>/chat/completions`` -> ``<host>/<version>``

    Longest tail first, then a trailing slash is dropped. A URL with no chat
    tail is returned verbatim (minus the trailing slash) — so an already-correct
    base is unchanged. Never raises.
    """
    try:
        raw = str(url or "").strip()
        if not raw:
            return ""
        base = raw.rstrip("/")
        for tail in CHAT_TAILS:
            if base.endswith(tail):
                base = base[: -len(tail)]
                break
        return base.rstrip("/")
    except Exception:  # noqa: BLE001
        return ""


def read_key(key_env: str) -> str:
    """The credential a lane's ``key_env`` names.

    ``key_env`` is an ENV VAR NAME (the schema's contract for every provider
    block) or ``file:<path>`` for a key file. An empty name, or a name the
    environment does not set, yields "" — forms 3 and 4 (shim / another local
    install) are keyless by design and must not be treated as misconfigured.
    Never raises.
    """
    try:
        name = str(key_env or "").strip()
        if not name:
            return ""
        if name.startswith("file:"):
            path = os.path.expanduser(name[len("file:"):].strip())
            if not path:
                return ""
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        return os.environ.get(name, "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _is_loopback(host: str) -> bool:
    return str(host or "").strip().lower() in _LOOPBACK_HOSTS


def _resolve_scheme_pointer(endpoint: str, lane: str) -> Optional[Tuple[str, str, str]]:
    """Form 2 — ``<scheme>://<model>``. Delegates to the anchor chain's ONE
    scheme table so this module never grows a second provider map.

    Returns ``(base_url, key, model)`` or None when the pointer is not a
    scheme pointer / does not resolve. Never raises.
    """
    try:
        if "://" not in endpoint:
            return None
        scheme = endpoint.split("://", 1)[0].strip().lower()
        if scheme in ("http", "https"):
            return None
        # Module import, never `from .. import anchor_chain`: `from .. import X`
        # is a ROOT-package import (the features-lanes-no-root contract forbids
        # it). This edge is lanes -> anchor_chain, a module edge the lanes
        # contract permits.
        from ..anchor_chain import parse_anchor_uri as _parse_anchor_uri

        parsed = _parse_anchor_uri(endpoint, lane)
    except Exception:  # noqa: BLE001
        return None
    if parsed is None:
        return None
    try:
        key = str(getattr(parsed, "api_key_value", "") or "").strip()
        if not key:
            key = read_key(str(getattr(parsed, "api_key_env", "") or ""))
        return (str(getattr(parsed, "base_url", "") or "").strip(),
                key,
                str(getattr(parsed, "model", "") or "").strip())
    except Exception:  # noqa: BLE001
        return None


def _resolve_url(endpoint: str, model: str, key_env: str
                 ) -> Tuple[str, str, str]:
    """Forms 1, 3 and 4 — an http(s) base. Returns ``(base_url, key, model)``.

    `key_env` is the EFFECTIVE name from `lane_spec()` / an explicit block —
    passed in, never re-read here, so an explicit block is honoured end to end.

    Keyless for both loopback forms: the shim authenticates the local process
    by socket, and another Hermes install on this host is reached as a local
    service, so an absent key is the correct state, not a failure.
    Never raises.
    """
    base = strip_chat_path(endpoint)
    if not base:
        return "", "", model
    try:
        parsed = urlparse(base)
        host = parsed.hostname or ""
        port = parsed.port
    except Exception:  # noqa: BLE001 — a malformed URL still yields its base
        return base, "", model
    if _is_loopback(host):
        if port == SHIM_PORT:
            # Form 3: the role IS the model the shim expects.
            return base, "", model
        # Form 4: another Hermes install on this host, local-only.
        return base, "", model
    # Form 1: an OpenAI-compatible host — the declared key_env is the only
    # credential source; a missing key is the caller's to report, not ours to
    # paper over with an empty success.
    return base, read_key(key_env), model


def resolve(lane: str, block: Optional[Dict[str, Any]] = None
            ) -> Tuple[str, str, str, str]:
    """The ONE lane resolver. Returns ``(base_url, key, schema, model)``.

    ``lane``   one of ``aux | frontier | uncensored | decision``.
    ``block``  an explicit lane block (tests / a caller holding a config
               snapshot); when omitted the profile's config is read.

    Unknown lane, unresolvable pointer, unreadable config: ``("", "", schema,
    model)`` — the caller fails open. NEVER raises, and never invents an
    endpoint: every URL returned came out of the profile or the declared
    defaults.

    The four accepted ``endpoint`` forms are documented in the module
    docstring; all four arrive through the SAME key and the SAME call.
    """
    lane_name = str(lane or "").strip()
    schema_name = "openai"
    model = ""
    try:
        spec = lane_spec(lane_name)
        if isinstance(block, dict):
            for field in ("endpoint", "model", "schema", "key_env"):
                if block.get(field) not in (None, ""):
                    spec[field] = str(block.get(field)).strip()
        schema_name = declared_schema(lane_name)
        if isinstance(block, dict) and str(block.get("schema") or "").strip():
            declared = str(block.get("schema")).strip().lower()
            s = _schema_mod()
            allowed = tuple(getattr(s, "LANE_SCHEMAS", ("openai", "typesafe"))) if s else ("openai", "typesafe")
            if declared in allowed:
                schema_name = declared
        endpoint = str(spec.get("endpoint") or "").strip()
        model = str(spec.get("model") or "").strip()
        if not endpoint:
            return "", "", schema_name, model

        scheme_hit = _resolve_scheme_pointer(endpoint, lane_name)
        if scheme_hit is not None:
            base, key, scheme_model = scheme_hit
            return base, key, schema_name, (scheme_model or model)

        base, key, resolved_model = _resolve_url(
            endpoint, model, str(spec.get("key_env") or "").strip())
        return base, key, schema_name, resolved_model
    except Exception:  # noqa: BLE001 — a resolver must never raise into a lane
        return "", "", schema_name, model
