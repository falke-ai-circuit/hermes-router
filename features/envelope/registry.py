"""Lane registry — the ONE place a lane and its closed extras key sets live.

Spec: /opt/data/tmp/unified_envelope_phase2_plan.md §3.1 (U1).

Both directions read from this table:

  * request       — what a lane asks the model        (lane-request/1)
  * assimilation  — what a lane hands back to the agent (lane-assimilation/1)

A lane is declared once, with both directions, in ``LANES``. ``EXTRAS_KEYS``
(request) and ``ASSIM_EXTRAS_KEYS`` (assimilation) are DERIVED views over that
table — never copies, never duplicated literals. Because they are live views,
a lane registered at runtime (a new adapter, a test) is accepted by the
builders without touching them: adding a lane is one dict entry.

The key sets are CLOSED. An unknown lane, an unknown direction or an unknown
extras key is a REFUSAL upstream (``{}``), never a silent drop: a mistyped
lane field that is quietly ignored is how a lane loses a capability nobody
notices for six weeks.

DETERMINISTIC, zero I/O, no imports from the builders (this module is data +
views only, so no import cycle can form). NEVER RAISES.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Dict, Iterator, Set

DIRECTIONS = ("request", "assimilation")

# --------------------------------------------------------------------------- #
# the registry — one entry per lane, both directions
# --------------------------------------------------------------------------- #

LANES: Dict[str, Dict[str, frozenset]] = {
    "decision": {
        "request": frozenset({
            "options",            # [{id, label, causal_frame, cost, priors, risk,
                                  #   probability}]
            "causal_context",     # ledger-derived causal frame (R19.18)
            "question",           # typed ask to the lane
            "guidance",           # {max_options, decision_kind}
            "weighting",          # per-option weights (SPEC-impulse-lane-v1 §3)
            "fork_class",         # closed fork vocabulary
            "slice",              # precedent slice, provenance-tagged
            "task",               # human task text the turn belongs to
            "digest",             # counts-only digest of what was sent
            "surrounding_context",
            "frame_context_chars_used",
        }),
        "assimilation": frozenset({
            "options",            # the closed option set the fork carried
            "chosen",             # option id the lane resolved to
            "resolver_rule",      # which rule picked it
            "outcome_label",      # closed outcome vocabulary
            "diagnosticity",      # how much the outcome told us apart
        }),
    },
    "frontier": {
        "request": frozenset({
            "paths",              # point-of-view angles offered to the frontier
            "evidence_refs",
            "consult_kind",       # consultation|verification
            "route_id",
            "kind",               # legacy provenance label (1:1 with old key)
        }),
        "assimilation": frozenset({
            "consult_kind",       # consultation|verification
            "pov_claims",         # four voices + weights + winning voice
            "dials",              # parsed weights the winner emerged from
        }),
    },
    "uncensored": {
        "request": frozenset({
            "leg",                # pre|post
            "work_text",          # POST only: work to reason over
            "voice_mode",         # voice_stems|none
            "close_class",        # closed-line class, never inferred downstream
        }),
        "assimilation": frozenset({
            "leg",                # pre|post
            "render",             # the rendered turn body
            "acceptance",         # accepted|partial|refused
            "refusal_class",      # closed-line class on a refusal
            "voice_mode",         # voice_stems|none
        }),
    },
}


# --------------------------------------------------------------------------- #
# derived, LIVE views — one source of truth, zero duplicated literals
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# REQUIRED (not merely allowed) extras keys, per lane + direction
# --------------------------------------------------------------------------- #
# A closed key set says "these keys may appear". A required key says "the
# envelope is a LIE without this value", and the two must not be conflated:
# every other lane's extras are optional evidence, while the uncensored
# assimilation's `acceptance` is the statement of HOW the delivered body was
# accepted (post_transform vs original + the reason token). A lane that cannot
# say that must not emit an assimilation envelope claiming a delivery — a body
# with no acceptance record is the inverse-delivery defect in schema form.
REQUIRED_KEYS: Dict[str, Dict[str, frozenset]] = {
    "uncensored": {
        "request": frozenset(),
        "assimilation": frozenset({"acceptance"}),
    },
}


class _DerivedKeys(Mapping):
    """``lane -> frozenset`` view over ``LANES[lane][direction]``, live.

    A snapshot copy would be a second source of truth: a lane registered after
    import would be invisible to the builders, which is exactly the "touching
    the builders" cost this table exists to remove. Reads go through to
    ``LANES`` on every access; misses yield an empty set (the caller refuses).
    """

    def __init__(self, direction: str) -> None:
        self.direction = direction

    def __getitem__(self, lane: Any) -> frozenset:
        try:
            spec = LANES[lane]
            keys = spec.get(self.direction)
            return frozenset(keys) if keys is not None else frozenset()
        except Exception:  # noqa: BLE001 — unknown lane is "no keys", not a crash
            return frozenset()

    def __iter__(self) -> Iterator[str]:
        return iter(list(LANES))

    def __len__(self) -> int:
        return len(LANES)

    def __contains__(self, lane: Any) -> bool:
        return lane in LANES

    def __repr__(self) -> str:  # pragma: no cover — diagnostics only
        return "<derived %s keys: %s>" % (self.direction, sorted(LANES))


EXTRAS_KEYS = _DerivedKeys("request")
ASSIM_EXTRAS_KEYS = _DerivedKeys("assimilation")


class _LaneNames(Sequence):
    """``tuple(LANES)`` as a LIVE sequence (membership + iteration included).

    Callers that gate on "is this a known lane" and tests that iterate the
    lane list both see a lane added after import — no rebuild of a module
    constant, no stale tuple to keep in sync.
    """

    def __getitem__(self, index: Any) -> Any:
        return tuple(LANES)[index]

    def __iter__(self) -> Iterator[str]:
        return iter(tuple(LANES))

    def __len__(self) -> int:
        return len(LANES)

    def __contains__(self, lane: Any) -> bool:
        return lane in LANES

    def __repr__(self) -> str:  # pragma: no cover — diagnostics only
        return repr(tuple(LANES))


LANE_NAMES = _LaneNames()


# --------------------------------------------------------------------------- #
# accessors — the only sanctioned way to ask the registry a question
# --------------------------------------------------------------------------- #

def extras_keys(lane: Any, direction: str = "request") -> frozenset:
    """Closed extras key set for ``lane`` in ``direction``.

    Unknown lane OR unknown direction -> empty set, which every builder treats
    as a refusal (never as "no restriction").
    """
    try:
        if direction not in DIRECTIONS:
            return frozenset()
        return frozenset(LANES[lane].get(direction) or ())
    except Exception:  # noqa: BLE001
        return frozenset()


def required_keys(lane: Any, direction: str = "request") -> frozenset:
    """REQUIRED extras keys for ``lane`` in ``direction`` (empty when none).

    Required is not allowed: an allowed-but-absent key yields a thinner
    envelope, a required-but-absent key yields an envelope that misstates what
    happened. Builders refuse it and validators report it.
    """
    try:
        if direction not in DIRECTIONS:
            return frozenset()
        return frozenset((REQUIRED_KEYS.get(lane) or {}).get(direction) or ())
    except Exception:  # noqa: BLE001
        return frozenset()


def has_lane(lane: Any) -> bool:
    """True when the registry declares ``lane`` in BOTH directions."""
    try:
        spec = LANES.get(lane)
        if not isinstance(spec, dict):
            return False
        return all(isinstance(spec.get(d), frozenset) for d in DIRECTIONS)
    except Exception:  # noqa: BLE001
        return False


def register_lane(lane: str, request: Set[str],
                  assimilation: Set[str]) -> bool:
    """Declare (or replace) one lane in both directions. True when accepted.

    Convenience for a new lane's adapter; the dict entry is still the one
    source of truth, so this is a write to it, not a second registry.
    """
    try:
        if not lane:
            return False
        LANES[str(lane)] = {
            "request": frozenset(str(k) for k in (request or ())),
            "assimilation": frozenset(str(k) for k in (assimilation or ())),
        }
        return True
    except Exception:  # noqa: BLE001
        return False
