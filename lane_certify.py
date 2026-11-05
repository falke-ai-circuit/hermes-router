"""M42-05 — lane certification by frame distinctness (DARK: no delivery wiring).

The acceptance rule the spec names is negative: a lane cannot pass
certification by emitting a frame that a landed lane already emitted. Word-level
pins cannot express it (a copy satisfies every regex, carries the provenance
tag and has the single-line shape), so the check is structural — measure how
far the candidate frame is from every landed frame.

`certify` is deliberately NOT imported by the delivery path in this unit. It is
shipped as a measured capability only: a caller that rejects a candidate can
count it with `decision.bump_counter("lane_certify_reject")`. Nothing gates
delivery on it yet.

Contract
--------
certify(candidate, landed, floor=0.8) -> (ok, scores)

* ``scores`` is the measured distinctness of the candidate against each landed
  frame, in input order (``render_payload.frame_distinctness``: 1 − Jaccard
  over 3-word shingles, numbers masked). Returned even on rejection so the
  caller has the number it is counting, not a bare boolean.
* ``ok`` is True only when EVERY pairwise score is >= ``floor``: a candidate
  that imitates ANY landed frame is rejected, so the binding comparison is the
  MINIMUM score, not the maximum. (Read literally as "False when max > floor"
  the rule would reject a frame that is distinct from everything — which is
  test (c) of this unit and the whole point of the check.)
* Fail closed: an empty ``landed`` list returns ``(False, [])`` — no frames to
  certify against is no evidence of distinctness, never a pass. An empty
  candidate is a reject. A raising measure is a reject.

No new dependency: this module only composes ``render_payload``.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

from .render_payload import frame_distinctness

#: Default acceptance floor: a candidate closer than this to a landed frame is
#: treated as cosplay. Spec-fixed (M42-05), not tunable at runtime.
LANE_CERTIFY_FLOOR = 0.8


def certify(candidate: str,
            landed: Sequence[str],
            floor: float = LANE_CERTIFY_FLOOR) -> Tuple[bool, List[float]]:
    """(ok, scores) — see the module docstring for the contract.

    Never raises: any failure inside measurement is a rejection.
    """
    try:
        frames = [str(f or "") for f in (landed or [])]
        if not frames:
            # no landed frames = nothing to be distinct from = no pass
            return False, []
        cand = str(candidate or "")
        scores = [frame_distinctness(cand, f) for f in frames]
        if not cand.strip():
            return False, scores
        return all(score >= floor for score in scores), scores
    except Exception:  # noqa: BLE001 — fail closed on any measurement failure
        return False, []
