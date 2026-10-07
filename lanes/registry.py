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


def all_lanes() -> tuple:
    """All registered LaneSpec rows, registration order."""
    return tuple(_IDS.values())


def valid_lane_ids() -> tuple:
    """Replaces VALID_ROUTE_LANES membership (only spec.valid rows)."""
    return tuple(s.id for s in _IDS.values() if s.valid)
