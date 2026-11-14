"""lanes/registry.py — LaneSpec dataclass + registration (proposal §1.5, §2.1).

The abstraction that turns the 15-file lane surface into data. Each field
maps 1:1 to a file in the analyst's 15-file touch list:
    route_gate phrase dicts      -> phrases
    dispatcher_knobs patterns    -> pre_patterns
    dispatcher_pre marker registry -> marker_strings
    debug_banner park semantics  -> banner_kind
    decision/completion_audit budgets -> budget_profile
    commands.py _LANE_MAP        -> commands_switch (label, config_key)
    frames/canonical provenance  -> provenance_tag
    canonical/delivery edges     -> delivery_edges
    anchor_chain catalog         -> consult_role
    VALID_ROUTE_LANES membership -> valid

stdlib only — lanes/ depends on nothing (registered data consumed by the
engine). register_lane is import-time fail-loud: duplicate id = ValueError.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, FrozenSet, Mapping, Optional, Sequence

_IDS: Dict[str, "LaneSpec"] = {}


@dataclass(frozen=True)
class LaneContract:
    """What a lane IS, as data (M41-02 §B; design "Generalisation").

    M40-16: `kind` is the first field because it is the field that makes the
    other four answerable. Before it, a lane with no gate semantics had to
    either stay contract-less (indistinguishable from "nobody declared one") or
    name a token that resolves to no consumer (an invented token). `kind` says
    which of those two a lane is, in a closed vocabulary, so `contract is None`
    means exactly one thing: a lane that never declared its contract at all.

      gate      — owns a question token, evaluates ordered rows, may replace.
      consult   — frontier/envelope consult; asks no gate question, routes
                  nothing, replaces nothing.
      audit     — fires a completion-audit seam; same shape as consult.
      advisory  — midturn advisory sweep; same shape as consult.

    `pre_question` / `post_question` are vocabulary tokens, not lane checks:
    the PRE and POST seams resolve them engine-side (PRE_QUESTIONS /
    POST_QUESTIONS) so no handler ever compares a lane id.

    `gate_semantics` is an ORDERED tuple of (predicate_token, action_token)
    rows, first match wins. It is data, not an expression language: a lane that
    needs a predicate the vocabulary does not carry needs a vocabulary entry
    (reviewed centrally, visible in the diff), not a handler edit.

    `replacement` names the render chain a route-and-replace action enters; it
    resolves by token the same way.

    A non-`gate` lane must leave all four of these EMPTY, enforced at
    registration: naming a question token is a claim on a seam the lane does
    not own, and a duplicate question token would silently shadow the lane that
    does (first match wins in `lane_for_pre_question`).
    """

    kind: str = ""
    pre_question: str = ""
    post_question: str = ""
    gate_semantics: tuple = ()
    replacement: str = ""


@dataclass(frozen=True)
class LaneSpec:
    """One lane's behavioral surface as data (§1.5).

    ENABLE/DISABLE DEFAULTS ARE NOT UNIFORM, and no field here carries them
    (M37-06 D3). Resolution runs through `router_core._lane_enabled` ->
    `_complexity_cfg` / `_decision_cfg`, which fall back to `{}` on a missing
    config section, and the resulting default is per lane:
      shadow    — always enabled (no config gate on the render lane)
      higher-pre  (frontier/complexity) — FAIL-OPEN: missing section => enabled
      decision  — FAIL-CLOSED: missing section => disabled
      higher-post — enabled whenever its POST audit path is reached
    A maintainer must not assume a single default: an unwired lane that expects
    "disabled unless configured" is wrong for complexity and wrong for shadow.

    `config_section` was removed in M37-06: it was `None` on every lane with
    zero readers anywhere in the tree (grep evidence in M37-06_FIX.md), so the
    only config surface is the one named above.

    RECURRING TRAP, named once here because it is cheap to get wrong: registry
    ids and `router_core` lane values are DIFFERENT vocabularies. `'decision'`
    is the single coincidental shared spelling and MUST NOT be used as a join
    key between them — a join on string equality matches that one row and
    silently misses `complexity` (registry: `higher-pre`) and `uncensored`
    (registry: `shadow`). M37-06 D5.
    """

    id: str
    phrases: Mapping[str, str] = field(default_factory=dict)
    pre_patterns: Sequence[str] = ()
    marker_strings: Sequence[str] = ()
    banner_kind: Optional[str] = None
    budget_profile: Optional[str] = None
    commands_switch: Optional[Dict[str, str]] = None
    provenance_tag: Optional[str] = None
    delivery_edges: FrozenSet[str] = frozenset()
    consult_role: Optional[str] = None
    valid: bool = True
    # M41-02 (§B): one optional field, default None — every pre-existing
    # entry keeps its meaning unchanged.
    contract: Optional[LaneContract] = None
    # M37-02: ordered seam ids this lane PARTICIPATES in — "PRE", "MID",
    # "POST". Timing, not a slot: three of four lanes span two seams, so the
    # registry keys on (lane, seam) and never on lane alone. Empty tuple is
    # legal and means "this lane declares no seam" (MID may legitimately run
    # no lane). Cross-lane ordering is registration order — no order field.
    seams: tuple = ()


# The closed seam vocabulary. `register_lane` rejects anything else: a typo in
# a seam id must fail at import, never silently drop a lane out of a seam.
SEAM_IDS: tuple = ("PRE", "MID", "POST")

# The closed contract-kind vocabulary (M40-16). Empty is NOT a legal kind: a
# contract that does not say what it is cannot be checked, and defaulting it
# would make "declared" and "forgotten" the same row.
CONTRACT_KINDS: tuple = ("gate", "consult", "audit", "advisory")

# What a `gate` contract must carry, and what every other kind must not.
_GATE_FIELDS = ("pre_question", "post_question", "replacement")


def _validate_contract(spec: LaneSpec) -> None:
    """Fail-loud contract shape check, at registration (M40-16).

    Only invariants this module owns are enforced here — `lanes/` imports
    nothing, so the PREDICATE/ACTION vocabularies and table totality are
    cross-checked in `tests/routing/test_m40_16_lane_contracts.py`, where both
    sides are importable. The split is deliberate: a `gate` lane may legitimately
    declare ONE field (M37-04's fifth-lane fixture declares only a POST question
    token), so registration must not demand all four — it must only refuse a
    contract that is unearned:
      * kind is a member of CONTRACT_KINDS (empty/typo => ValueError);
      * a non-`gate` contract names NO gate field (a consult lane claiming a
        question token shadows the lane that owns it, since
        `lane_for_pre_question` returns the first match);
      * a `gate` contract names AT LEAST one field (an all-empty gate contract
        is the "forgot to fill it in" typo this field exists to make loud);
      * every row it does declare is a (predicate, action) pair of non-empty
        tokens — a malformed row is silently skipped by the evaluator, so it
        must not survive registration.
    Table totality (a terminal `else` row) is a property of the ONE table the
    engine walks, asserted against the resolved lane in the test — not a shape
    rule every gate literal must satisfy.
    """
    contract = spec.contract
    if contract is None:
        return
    kind = str(contract.kind or "").strip()
    if kind not in CONTRACT_KINDS:
        raise ValueError(
            f"lane {spec.id!r}: unknown contract kind {contract.kind!r} "
            f"(allowed: {', '.join(CONTRACT_KINDS)}; empty is not a kind)")
    declared = [f for f in _GATE_FIELDS if str(getattr(contract, f) or "").strip()]
    rows = tuple(contract.gate_semantics or ())
    if kind != "gate":
        if declared or rows:
            raise ValueError(
                f"lane {spec.id!r}: contract kind {kind!r} must declare no "
                f"gate surface; it names "
                f"{declared + (['gate_semantics'] if rows else [])}"
                f" (a non-gate lane claiming a question token shadows the lane "
                f"that owns it)")
        return
    if not declared and not rows:
        raise ValueError(
            f"lane {spec.id!r}: kind 'gate' contract declares no field at all "
            f"— state the question token, the rows or the replacement; an empty "
            f"gate contract is a forgotten one")
    for index, row in enumerate(rows, 1):
        try:
            pred, action = str(row[0]).strip(), str(row[1]).strip()
        except Exception:  # noqa: BLE001 — malformed row is a contract defect
            raise ValueError(
                f"lane {spec.id!r}: gate_semantics row {index} is not a "
                f"(predicate_token, action_token) pair: {row!r}") from None
        if not pred or not action:
            raise ValueError(
                f"lane {spec.id!r}: gate_semantics row {index} has an empty "
                f"token: {row!r}")




def _normalise_seams(spec: LaneSpec) -> tuple:
    """Canonical (uppercase, stripped) seam tuple, or ValueError.

    Unknown ids are fatal, and the message names the lane id AND the bad value
    so an import traceback identifies the offending spec literal directly.
    """
    out = []
    for raw in spec.seams or ():
        seam = str(raw).strip().upper()
        if seam not in SEAM_IDS:
            raise ValueError(
                f"lane {spec.id!r}: unknown seam id {raw!r} "
                f"(allowed: {', '.join(SEAM_IDS)})")
        out.append(seam)
    return tuple(out)


def register_lane(spec: LaneSpec) -> None:
    """Idempotent; duplicate id, unknown seam or bad contract raises (fail-loud)."""
    seams = _normalise_seams(spec)
    if seams != tuple(spec.seams or ()):
        spec = replace(spec, seams=seams)
    _validate_contract(spec)
    existing = _IDS.get(spec.id)
    if existing is not None:
        if existing is spec or existing == spec:
            return
        raise ValueError(f"lane id already registered: {spec.id}")
    _IDS[spec.id] = spec


def enumerate_by_seam(seam: str) -> tuple:
    """Lane ids declaring `seam`, in registration order (M37-02 §3).

    Registration order is the cross-lane order (lanes/builtins.py) — this
    accessor adds no ordering of its own. Unknown seam id raises rather than
    returning an empty tuple, so a caller that typos a seam learns it.

    TRUTH ABOUT THIS FUNCTION (M37-06 D2): it has NO production consumer. In
    the tree at 3fc7a86 the only references are this definition and two test
    files (`tests/routing/test_m37_02_lanespec_seams.py`,
    `tests/routing/test_m37_04_pre_lane_table.py`). Nothing in `passes/`,
    `gate/` or `core/` asks which lanes declare a seam: the PRE, MID and POST
    hooks are still hand-wired, so a lane that declares a seam is NOT thereby
    plugged into it. The seam declaration is a claim the registry can answer
    and that the tests read; the render-time consumer does not exist yet.
    """
    key = str(seam or "").strip().upper()
    if key not in SEAM_IDS:
        raise ValueError(f"unknown seam id {seam!r} (allowed: {', '.join(SEAM_IDS)})")
    return tuple(s.id for s in _IDS.values() if key in s.seams)


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
# There is exactly ONE entry, because there is exactly ONE chain a replacement
# may enter: `post_render_chain` is the pre-existing POST route chain —
# `router.call` (whose own ladder retries a retryable 5xx once and falls through
# the ordered model chain), then `cap_render`/`render_max_chars` (the render
# budget), then `commit_canonical_event` + `rewrite_persisted_turn` +
# `verify_persisted_turn` + `render_swap_decision` (the swap), then the
# assimilation mark. A replacement token does not open a second path — it
# selects that one.
#
# NOT the PRE ladder (`passes/dispatcher_pre._render_with_retry_ladder`): that
# one carries the M34-02 substance-acceptance gate, which re-grades the body
# against the ask. Entering it from POST was measured in this unit to discard a
# render the POST chain had accepted (`route_failed_substance
# reason=substance_short`) and re-ship the refusal — the exact silent
# suppression this lane exists to prevent.
REPLACEMENT_POST_CHAIN = "post_render_chain"

REPLACEMENTS: Dict[str, str] = {
    "uncensored_render": REPLACEMENT_POST_CHAIN,
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
