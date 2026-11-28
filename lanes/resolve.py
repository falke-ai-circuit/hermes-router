"""lanes/resolve.py — THE one resolver for the four uniform lanes (m64).

One config key (`lanes.<lane>.endpoint`) accepts FOUR endpoint forms, and every
form leaves this module as the SAME 4-tuple shape:

    (base_url, key, schema, model)

Forms accepted through that one key:

  1. OpenAI-compatible BASE URL        https://api.example/v1
  2. scheme pointer (back-compat)      openrouter://<model>
  3. shim pointer + ROLE model         http://127.0.0.1:8680/v1  (model: main|aux|vision)
  4. pointer at another Hermes install http://127.0.0.1:8681/v1  (local-only included)

THE PATH TRAP (why this module exists). Legacy keys carry a FULL chat path —
`chain[0].url` is ".../api/v1/chat/completions" while a lane block carries a
BASE and lets the provider supply the path. A resolver that returns whatever
string it was handed makes the caller append `/chat/completions` to a URL that
already ends in one; the request then 404s and the lane fails SILENTLY (the
router's fail-open treats it as an ordinary model error). So every http(s)
endpoint is normalised ONCE here: trailing slashes dropped, any trailing
`/chat/completions` removed, repeatedly, so even a doubled tail collapses to a
base. `wire()` then composes base + the shared chat tail, which is what makes a
legacy full-path endpoint and a fresh base produce BYTE-IDENTICAL requests
(gate G3 asserts exactly that — "the lane resolved" would pass on the bug).

LEGACY COMPAT (A4). A profile with NO `lanes` block resolves exactly as today,
by CALLING today's accessors rather than re-deriving them:

    uncensored -> router._chain_entries()  + router._read_key()
    frontier   -> anchor_chain.load_anchor_chain().primary
    decision   -> decision_head._embedding_endpoint()
    aux        -> core/aux_endpoint.fallback_base_url() + Hermes core's aux model

`lanes` present -> the new block only. Never both: a lane that declares an
endpoint is resolved from that block alone, so a half-migrated profile cannot
get a base from one source and a key from the other.

Read-only and never raises: an unresolvable lane returns empty strings and the
caller fails open. Nothing in the runtime calls this yet (A5 — config
resolution only; no routing, detection, PRE/POST or delivery decision moves).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping, NamedTuple, Optional

# The lane declaration lives in core/schema.py — imported, never restated, so
# the resolver cannot drift from the uniformity table it is resolving.
try:  # package import (the deployed shape)
    from ..core import schema as _schema
except Exception:  # noqa: BLE001 — bare-module import in a harness
    from core import schema as _schema  # type: ignore[no-redef]

# Model values that name a ROLE rather than a model id: on form 3 the shim
# resolves the role to a model server-side, so the name is sent verbatim.
ROLE_NAMES = ("main", "aux", "vision")

# The chat tail a caller appends. Imported from the shared provider table so
# this module invents no second literal (M24-02 owns it).
try:  # package import
    from ..core.config_access import PROVIDER_CHAT_PATH_DEFAULT as _CHAT_TAIL
except Exception:  # noqa: BLE001
    _CHAT_TAIL = "/chat/completions"


class LaneResolution(NamedTuple):
    """The one addressable shape of a lane.

    Unpacks as the 4-tuple the brief names — `base, key, schema, model =
    resolve("aux")` — while staying readable at call sites. `base_url` is
    ALWAYS a base (no chat tail); `schema` is the DECLARED dialect, never
    inferred from the URL's shape.
    """

    base_url: str
    key: str
    schema: str
    model: str


_EMPTY = LaneResolution("", "", "", "")


# ---------------------------------------------------------------------------
# URL normalisation — the path trap
# ---------------------------------------------------------------------------


def strip_chat_tail(url: str) -> str:
    """`url` as a BASE: no trailing slash, no trailing `/chat/completions`.

    Applied repeatedly, so a doubled legacy tail (".../v1/chat/completions/
    chat/completions") collapses to ".../v1" instead of surviving as a URL
    nobody can dial. Case-insensitive on the tail. Never raises.
    """
    try:
        out = str(url or "").strip()
        if not out:
            return ""
        tail = _CHAT_TAIL.lower().rstrip("/")
        while True:
            out = out.rstrip("/")
            low = out.lower()
            if low.endswith(tail):
                out = out[: -len(tail)]
                continue
            return out
    except Exception:  # noqa: BLE001
        return str(url or "").strip()


def is_scheme_pointer(endpoint: str) -> bool:
    """True when `endpoint` is a `<scheme>://<model>` pointer (form 2) rather
    than an http(s) URL (forms 1/3/4). Never raises."""
    try:
        ep = str(endpoint or "").strip()
        if "://" not in ep:
            return False
        return ep.split("://", 1)[0].strip().lower() not in ("http", "https")
    except Exception:  # noqa: BLE001
        return False


def chat_path() -> str:
    """The chat tail a caller appends to a resolved base (one leading slash,
    no trailing one). Shared literal — never a local copy."""
    p = "/" + str(_CHAT_TAIL or "/chat/completions").lstrip("/")
    return p.rstrip("/") or "/chat/completions"


def wire(resolution, messages: Optional[List[Dict[str, str]]] = None,
         max_tokens: int = 16, temperature: float = 0.0) -> Dict[str, Any]:
    """The exact request a lane would send: {"url", "body"}.

    Body bytes are canonicalised (sorted keys, no whitespace drift) so two
    resolutions that address the SAME endpoint compare byte-for-byte. This is
    the G3 instrument: a full-path legacy endpoint and a fresh base must yield
    identical `url` and identical `body`.
    """
    try:
        if isinstance(resolution, str):
            resolution = resolve(resolution)
        base = str(getattr(resolution, "base_url", "") or "").rstrip("/")
        body = json.dumps(
            {"model": getattr(resolution, "model", "") or "",
             "messages": list(messages or [{"role": "user", "content": "ping"}]),
             "max_tokens": int(max_tokens),
             "temperature": float(temperature)},
            sort_keys=True, separators=(",", ":"),
        )
        return {"url": base + chat_path(), "body": body}
    except Exception:  # noqa: BLE001
        return {"url": "", "body": ""}


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------


def _env(name: str) -> str:
    """Value of env var `name`, "" when unset/blank. Never raises."""
    try:
        key = str(name or "").strip()
        return os.environ.get(key, "").strip() if key else ""
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# Block presence + the new-block path
# ---------------------------------------------------------------------------


def lanes_block(cfg: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The `lanes` block from the router section (or `cfg`), {} when absent.

    Absence is the LEGACY signal — the resolver branches on it, so it must be
    distinguishable from "present but empty". Never raises.
    """
    try:
        if cfg is not None:
            block = (cfg or {}).get("lanes")
            return dict(block) if isinstance(block, Mapping) else {}
        from ..core import config_access as _ca  # noqa: PLC0415

        block = _ca.sub_block("lanes")
        return dict(block) if isinstance(block, Mapping) else {}
    except Exception:  # noqa: BLE001
        return {}


def lane_declared(lane: str, block: Optional[Mapping[str, Any]] = None) -> bool:
    """True when this lane declares a usable `endpoint` in the lanes block.
    A lane that is absent resolves LEGACY — never a merge of the two."""
    try:
        name = str(lane or "").strip().lower()
        spec = (block if block is not None else lanes_block()).get(name)
        return isinstance(spec, Mapping) and bool(str(spec.get("endpoint") or "").strip())
    except Exception:  # noqa: BLE001
        return False


def _resolve_scheme_pointer(endpoint: str, lane: str,
                            model_fallback: str) -> LaneResolution:
    """Form 2: `<scheme>://<model>` — resolved by anchor_chain's existing
    scheme table (the ONE place that knows openrouter/nous/custom providers),
    so the lanes block inherits back-compat instead of re-implementing it."""
    try:
        from .. import anchor_chain  # noqa: PLC0415

        ep = anchor_chain.parse_anchor_uri(endpoint, lane or "primary")
        if ep is None:
            return _EMPTY
        pointer_model = str(ep.model or "").strip()
        return LaneResolution(
            base_url=strip_chat_tail(str(ep.base_url or "")),
            key=(str(getattr(ep, "api_key_value", "") or "").strip()
                 or _env(getattr(ep, "api_key_env", ""))),
            schema="",
            model=(str(model_fallback or "").strip() or pointer_model),
        )
    except Exception:  # noqa: BLE001
        return _EMPTY


def resolve_lane(lane: str, block: Optional[Mapping[str, Any]] = None,
                 cfg: Optional[Mapping[str, Any]] = None) -> LaneResolution:
    """Resolve ONE lane from the lanes block when it declares an endpoint,
    else from the legacy keys — never both. Unknown lane -> empty resolution.

    `schema` is the DECLARED dialect: the lane's `schema` field when it names
    one of LANE_KINDS, else the lane's declared default. It is never inferred
    from the endpoint's shape — a base ending in /v1 is not evidence of an
    OpenAI dialect, and a decision head is not evidence of one either.
    """
    try:
        name = str(lane or "").strip().lower()
        if name not in _schema.LANE_NAMES:
            return _EMPTY
        blk = dict(block) if block is not None else lanes_block(cfg)
        if lane_declared(name, blk):
            return _resolve_new(name, blk)
        return _resolve_legacy(name)
    except Exception:  # noqa: BLE001 — fail open, never raise into a lane
        return _EMPTY


def _resolve_new(lane: str, block: Mapping[str, Any]) -> LaneResolution:
    """The new uniform block for one lane. Fields are read by their declared
    names only; a lane-specific spelling is a declaration failure G1 catches,
    not something this resolver guesses at."""
    spec = block.get(lane)
    if not isinstance(spec, Mapping):
        return _EMPTY
    endpoint = str(spec.get("endpoint") or "").strip()
    model = str(spec.get("model") or "").strip()
    key_env = str(spec.get("key_env") or "").strip()
    declared = str(spec.get("schema") or "").strip().lower()
    dialect = declared if declared in _schema.LANE_KINDS else _schema.lane_kind(lane)

    if is_scheme_pointer(endpoint):
        res = _resolve_scheme_pointer(endpoint, lane, model)
        if not res.base_url:
            return _EMPTY
        return LaneResolution(res.base_url,
                              res.key or _env(key_env),
                              dialect,
                              res.model or model)

    # Forms 1/3/4: an http(s) address, normalised to a base. The ROLE model of
    # form 3 needs no special endpoint handling — the shim resolves the role
    # server-side, so the name travels verbatim as the wire model.
    base = strip_chat_tail(endpoint)
    if not base:
        return _EMPTY
    return LaneResolution(base, _env(key_env), dialect, model)


# ---------------------------------------------------------------------------
# Legacy path — today's accessors, called not re-derived (A4)
# ---------------------------------------------------------------------------


def _resolve_legacy(lane: str) -> LaneResolution:
    """`lanes` absent (or this lane absent from it): resolve exactly as the
    runtime does today, by CALLING the deployed accessors. No new literals, no
    re-derivation, so "exactly as today" is true by construction."""
    try:
        if lane == "uncensored":
            return _legacy_uncensored()
        if lane == "frontier":
            return _legacy_frontier()
        if lane == "decision":
            return _legacy_decision()
        if lane == "aux":
            return _legacy_aux()
        return _EMPTY
    except Exception:  # noqa: BLE001
        return _EMPTY


def _legacy_uncensored() -> LaneResolution:
    """LANE 1: first chain entry (legacy single `endpoint` still a chain-of-one),
    key through router._read_key — env first, then the key file."""
    try:
        from .. import router  # noqa: PLC0415

        entries = router._chain_entries()
        if not entries:
            return _EMPTY
        entry = entries[0] if isinstance(entries[0], Mapping) else {}
        base = strip_chat_tail(str(entry.get("url") or ""))
        if not base:
            return _EMPTY
        key = router._read_key(str(entry.get("key_file") or ""),
                               str(entry.get("key_env") or ""))
        return LaneResolution(base, str(key or "").strip(),
                              _schema.lane_kind("uncensored"),
                              str(entry.get("model") or "").strip())
    except Exception:  # noqa: BLE001
        return _EMPTY


def _legacy_frontier() -> LaneResolution:
    """LANE 2: anchor_chain.primary, scheme-resolved by the chain itself.
    No primary -> empty (consults fail closed, exactly as today)."""
    try:
        from .. import anchor_chain  # noqa: PLC0415

        chain = anchor_chain.load_anchor_chain()
        ep = chain.primary
        if ep is None:
            return _EMPTY
        return LaneResolution(
            strip_chat_tail(str(ep.base_url or "")),
            (str(getattr(ep, "api_key_value", "") or "").strip()
             or _env(getattr(ep, "api_key_env", ""))),
            _schema.lane_kind("frontier"),
            str(ep.model or "").strip(),
        )
    except Exception:  # noqa: BLE001
        return _EMPTY


def _legacy_decision() -> LaneResolution:
    """Decision lane: decision_head's own embedding endpoint (which already
    falls back to classification.aux_endpoint), key through the shared
    minimax provider table — the same two lookups the head makes today."""
    try:
        from .. import decision_head  # noqa: PLC0415
        from ..core import config_access as _ca  # noqa: PLC0415

        ep = decision_head._embedding_endpoint()
        if not isinstance(ep, Mapping):
            return _EMPTY
        base = strip_chat_tail(str(ep.get("url") or ""))
        if not base:
            return _EMPTY
        key_name = str(ep.get("key_env") or "").strip() or _ca.provider_key_env(
            "minimax", "MINIMAX_API_KEY")
        return LaneResolution(base, _env(key_name),
                              _schema.lane_kind("decision"),
                              str(ep.get("model") or "").strip())
    except Exception:  # noqa: BLE001
        return _EMPTY


def _legacy_aux() -> LaneResolution:
    """Aux lane: whatever Hermes CORE resolves for the host's own provider —
    the plugin holds no endpoint literal for this lane (m57/m58 contract)."""
    try:
        from ..core import aux_endpoint  # noqa: PLC0415
        from ..core import provider_derivation  # noqa: PLC0415

        base = strip_chat_tail(str(aux_endpoint.fallback_base_url() or ""))
        if not base:
            return _EMPTY
        runtime = provider_derivation._core_runtime() or {}
        return LaneResolution(base,
                              str(runtime.get("api_key") or "").strip(),
                              _schema.lane_kind("aux"),
                              str(provider_derivation.host_provider_model() or "").strip())
    except Exception:  # noqa: BLE001
        return _EMPTY


# ---------------------------------------------------------------------------
# Whole-block helpers
# ---------------------------------------------------------------------------


def resolve_all(block: Optional[Mapping[str, Any]] = None,
                cfg: Optional[Mapping[str, Any]] = None) -> Dict[str, LaneResolution]:
    """{lane: resolution} for all four lanes through the ONE resolver — the
    shape a diagnostic (or the legacy smoke) prints."""
    return {lane: resolve_lane(lane, block, cfg) for lane in _schema.LANE_NAMES}


def validate_lane_block(block: Optional[Mapping[str, Any]]) -> List[str]:
    """Every way a lanes block violates the UNIFORM contract, as strings.

    G1's instrument: a lane missing a declared field, or carrying a field the
    other lanes do not have (a lane-specific name), produces a violation. The
    field set is `schema.LANE_FIELDS` — the same tuple every lane declares, so
    this check cannot invent a second definition of "uniform".
    """
    problems: List[str] = []
    try:
        blk = dict(block or {})
        if not blk:
            return problems
        for name in blk:
            if str(name).strip().lower() not in _schema.LANE_NAMES:
                problems.append(f"lanes.{name}: not a declared lane")
        for lane in _schema.LANE_NAMES:
            spec = blk.get(lane)
            if spec is None:
                continue
            if not isinstance(spec, Mapping):
                problems.append(f"lanes.{lane}: not a mapping")
                continue
            fields = {str(f).strip() for f in spec}
            for required in _schema.LANE_FIELDS:
                if required not in fields:
                    problems.append(f"lanes.{lane}: missing field '{required}'")
            for extra in sorted(fields - set(_schema.LANE_FIELDS)):
                problems.append(f"lanes.{lane}: undeclared field '{extra}'")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"lanes: unreadable block ({exc.__class__.__name__})")
    return problems


# ---------------------------------------------------------------------------
# Uniformity table re-exports (callers import the contract from ONE place)
# ---------------------------------------------------------------------------

LANE_NAMES = _schema.LANE_NAMES
LANE_FIELDS = _schema.LANE_FIELDS
LANE_KINDS = _schema.LANE_KINDS
LANE_DECLARED_SCHEMA = _schema.LANE_DECLARED_SCHEMA
lanes_spec = _schema.lanes_spec
lane_paths = _schema.lane_paths
lane_kind = _schema.lane_kind


# `resolve` is the public name the runtime will use when A5 lifts; it is the
# same function object as resolve_lane (one resolver, no second implementation).
resolve = resolve_lane
