"""features/banners/kinds.py — BannerKind schema + registered kinds (P5).

The BannerKind data rows carry the banner semantics that were previously
imperative rules accreted per rider inside debug_banner's park/consume path
(proposal §2.2):

- R9d (Goran 09-14): one LLM call = exactly one banner; a re-park for the
  SAME task_id REPLACES its segment — the park store keys on the bare
  session_id (verbatim pre-P5 behavior); the dead dedupe_key_template
  data row was removed in P8a (conductor non-blocking note: no consumer).
- R19.16 FIX 4 (Goran addendum): ALL fired banners stack — the parked
  aggregate is ONE BLOCK, one segment per fired banner, in fire order —
  expressed as stack_policy="stack".
- R20-D3 (rider 20): the one-shot bounded capture-fallback watcher is
  scheduled only for kinds with capture_fallback=True.

Behavior note (Binding 1): the registered "anchor" kind reproduces the
exact semantics of the pre-P5 debug_banner park/consume path; the
lifecycle consults this data instead of hardcoding, so routing behavior
is byte-identical.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet


@dataclass(frozen=True)
class BannerKind:
    kind_id: str                       # "anchor_debug" | "hs_orientation" | ...
    stack_policy: str                  # "replace" (R9d) | "stack" (R19.16) | "once"
    ttl_seconds: int
    delivery_edges: FrozenSet[str]     # edges where consume is legal
    capture_fallback: bool             # R20; watcher deleted when no kind needs it


KINDS: Dict[str, BannerKind] = {}


class UnknownBannerKind(KeyError):
    """Raised when a kind_id is not registered (fail-loud, not silent)."""


def register(kind: BannerKind) -> None:
    """Idempotent register; a DIFFERENT re-registration of the same id
    raises at registration time (fail-loud)."""
    existing = KINDS.get(kind.kind_id)
    if existing is not None:
        if existing == kind:
            return
        raise ValueError(
            "banner kind re-registered with different data: %s" % kind.kind_id)
    KINDS[kind.kind_id] = kind


def get(kind_id: str) -> BannerKind:
    try:
        return KINDS[kind_id]
    except KeyError:
        raise UnknownBannerKind(str(kind_id)) from None


# The pre-P5 park store's delivery edges (the consume sites that legally
# fire today): the render/pre delivery edge, the benign edge, the
# audit-sync edge, the empty-body edge, and the claim-guard one-shot
# release (route_gate claim_execution_guard).
_ANCHOR_EDGES: FrozenSet[str] = frozenset({
    "pre_render", "benign", "audit_sync", "empty_body", "claim_release",
})

register(BannerKind(
    kind_id="anchor",
    stack_policy="stack",                       # R19.16 aggregate stack
    ttl_seconds=0,                              # parks persist until consumed
    delivery_edges=_ANCHOR_EDGES,
    capture_fallback=True,                      # R20-D3 watcher scheduled
))
