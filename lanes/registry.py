"""lanes/registry.py — LaneSpec dataclass + registration (proposal §1.5, §2.1).

The abstraction that turns the 15-file lane surface into data. Each field
maps 1:1 to a file in the analyst's 15-file touch list:
    route_gate phrase dicts      -> phrases
    dispatcher_knobs patterns    -> pre_patterns
    dispatcher_pre marker registry -> marker_strings
    debug_banner park semantics  -> banner_kind
    decision/completion_audit budgets -> budget_profile
    config_writer/config_access  -> config_section
    commands.py _LANE_MAP        -> commands_switch (label, config_key)
    frames/canonical provenance  -> provenance_tag
    canonical/delivery edges     -> delivery_edges
    anchor_chain catalog         -> consult_role
    VALID_ROUTE_LANES membership -> valid

stdlib only — lanes/ depends on nothing (registered data consumed by the
engine). register_lane is import-time fail-loud: duplicate id = ValueError.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Mapping, Optional, Sequence

_IDS: Dict[str, "LaneSpec"] = {}


@dataclass(frozen=True)
class LaneContract:
    """The four things a lane IS (M41-02 §B; design "Generalisation").

    `pre_question` / `post_question` are vocabulary tokens, not lane checks:
    the PRE and POST seams resolve them engine-side (PRE_QUESTIONS /
    POST_QUESTIONS) so no handler ever compares a lane id.

    `gate_semantics` is an ORDERED tuple of (predicate_token, action_token)
    rows, first match wins. It is data, not an expression language: a lane that
    needs a predicate the vocabulary does not carry needs a vocabulary entry
    (reviewed centrally, visible in the diff), not a handler edit.

    `replacement` names the render chain a route-and-replace action enters; it
    resolves by token the same way.
    """

    pre_question: str = ""
    post_question: str = ""
    gate_semantics: tuple = ()
    replacement: str = ""


@dataclass(frozen=True)
class LaneSpec:
    """One lane's behavioral surface as data (§1.5)."""

    id: str
    phrases: Mapping[str, str] = field(default_factory=dict)
    pre_patterns: Sequence[str] = ()
    marker_strings: Sequence[str] = ()
    banner_kind: Optional[str] = None
    budget_profile: Optional[str] = None
    config_section: Optional[str] = None
    commands_switch: Optional[Dict[str, str]] = None
    provenance_tag: Optional[str] = None
    delivery_edges: FrozenSet[str] = frozenset()
    consult_role: Optional[str] = None
    valid: bool = True
    # M41-02 (§B): one optional field, default None — every pre-existing
    # entry keeps its meaning unchanged.
    contract: Optional[LaneContract] = None



def register_lane(spec: LaneSpec) -> None:
    """Idempotent; duplicate id raises at import time (fail-loud)."""
    existing = _IDS.get(spec.id)
    if existing is not None:
        if existing is spec or existing == spec:
            return
        raise ValueError(f"lane id already registered: {spec.id}")
    _IDS[spec.id] = spec


def lane(id: str) -> LaneSpec:
    """One registered LaneSpec; KeyError on unknown id (fail-loud)."""
    return _IDS[id]


def all_ids() -> "tuple[str, ...]":
    """Registered lane ids in registration order (P7 engine surface)."""
    return tuple(_IDS)


def all_lanes() -> tuple:
    """All registered LaneSpec rows, registration order."""
    return tuple(_IDS.values())


def valid_lane_ids() -> tuple:
    """Replaces VALID_ROUTE_LANES membership (only spec.valid rows)."""
    return tuple(s.id for s in _IDS.values() if s.valid)


# ---------------------------------------------------------------------------
# the replacement registry (M41-06, spec §B) — a CLOSED map, held once
# ---------------------------------------------------------------------------
# `LaneContract.replacement` names a render chain by TOKEN. This map is the
# only place a token becomes an entry point, so the POST handler never asks
# "which lane is this?" and never hardcodes a call: it resolves the contract's
# field here and dispatches on the returned entry NAME.
#
# There is exactly ONE entry, because there is exactly one render chain:
# `render_chain` is the pre-existing ladder (`passes/dispatcher_pre.py:702`,
# wrapped at `gate/orchestration.py:239`) followed by the pre-existing cap,
# canonical commit/rewrite and swap-decision block. A replacement token does
# not open a second path — it selects that one.
REPLACEMENT_RENDER_CHAIN = "render_chain"

REPLACEMENTS: Dict[str, str] = {
    "uncensored_render": REPLACEMENT_RENDER_CHAIN,
}


def resolve_replacement(token: str) -> Optional[str]:
    """The render-chain entry a replacement token names, or None.

    Unknown / blank token => None, which the caller reads as "no replacement
    chain named" and falls back to the pre-existing direct render call. Fail-
    open by construction: a contract that lost its `replacement` field must not
    turn a routed turn into a silent no-op.
    """
    try:
        return REPLACEMENTS.get(str(token or "").strip())
    except Exception:  # noqa: BLE001
        return None


def resolve_contract(lane_id: str) -> Optional[LaneContract]:
    """The lane's contract, or None when it declares none.

    Engine-side resolution by TOKEN: a caller that holds a lane id gets its
    four fields here and never inspects the id itself (M41-02 §B — "a second
    lane is one LaneSpec literal, no handler edit"). Unknown id => None, the
    same fail-open the rest of this module's readers use.
    """
    spec = _IDS.get(str(lane_id or ""))
    return spec.contract if spec is not None else None


def lane_for_post_question(question: str) -> Optional[str]:
    """The lane id owning a POST question token, or None.

    Symmetrical with `lane_for_pre_question`: the POST seam names its lane by
    the token it implemented, never by comparing an id. Used by
    `features/detection/rule_table.py` to resolve the ordered row table.
    """
    q = str(question or "")
    if not q:
        return None
    for spec in _IDS.values():
        if spec.contract is not None and spec.contract.post_question == q:
            return spec.id
    return None


def lane_for_pre_question(question: str) -> Optional[str]:
    """The lane id owning a PRE question token, or None.

    This is how the PRE seam names its lane without a branch: the gate asks
    which registered lane carries the question it is about to ask, and writes
    that lane's id on the turn record for POST to resolve the semantics back.
    """
    q = str(question or "")
    if not q:
        return None
    for spec in _IDS.values():
        if spec.contract is not None and spec.contract.pre_question == q:
            return spec.id
    return None
