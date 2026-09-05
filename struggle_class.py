"""Struggle-event classifier (F1, v3.3.0 Phase 1 — Lane A, Astra-calibrated).

Classifies WHY struggle_verdict() fired, from evidence already stored in
_TASK_STATE / _TOOLLOOP_STATE. DETERMINISTIC code — evidence-counting on
stored state, NOT an LLM call (no new external model calls in Phase 1).

  classify_struggle(task_id, reason_code) -> (kind, detail)
    kind in {infra, reasoning, ambiguous}

Evidence rules (any single hit decides):
  infra     — last stored failure text (last_fail_text, persisted by
              record_provider_failure) matches a module-level compiled infra
              pattern; OR tool-loop transport death (calls jumped >= threshold
              with zero new result content).
  reasoning — repeated_same_failure with the stored failure text NOT matching
              any infra pattern (valid responses that kept failing), OR
              tool_loop_no_new_content where results were non-empty and
              semantically unchanged (the existing no-new-content detector
              already computed the dedup).
  ambiguous — neither (treat as reasoning-eligible in Phase 2, log honestly
              now).

Benign-negative guard (reviewer test 11): a VALID tool result that merely
MENTIONS "timeout=30" or "quota=1000" must NOT classify infra — assignment
syntax (key=<digits> / key: <digits>) is stripped before pattern matching, so
configuration values never read as error evidence.

Fail-open: never raises; unknown/missing state -> (ambiguous, "no_data").
"""
from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

from . import router_core

logger = logging.getLogger(__name__)

KIND_INFRA = "infra"
KIND_REASONING = "reasoning"
KIND_AMBIGUOUS = "ambiguous"

NO_DATA = "no_data"

# ---------------------------------------------------------------------------
# Infra evidence patterns — compiled ONCE at module level (spec F1).
# ---------------------------------------------------------------------------

_INFRA_PATTERN_STRINGS = (
    r"\b[45]\d{2}\b",                       # 4xx/5xx HTTP codes
    r"timeout|timed\s*out",
    r"connection|unreachable|refused",
    r"rate\s*limit|\b429\b",
    r"auth|unauthorized|\b401\b|\b403\b",
    r"ECONNRESET|ETIMEDOUT",
    r"unavailable|capacity|overloaded",
    r"quota",
)

_INFRA_RES: Tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in _INFRA_PATTERN_STRINGS
)

# Benign-negative guard: "timeout=30", "quota:1000", "max_requests 500" — a
# VALID result mentioning configuration values. Strip key=<numeric> /
# key:<numeric> assignments AND bare "<key> <number>" pairs BEFORE infra
# matching so config prose never reads as evidence.
_BENIGN_ASSIGNMENT_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_.\-]*\s*(?:[:=]|(?=\s+\d))\s*\d+(?:\.\d+)?(?:\s*(?:ms|s|sec|seconds|min|minutes|tokens?|req(?:uests?)?|rps|rpm))?",
)

# Transport-death heuristic (F1 infra arm b): a tool-call count at/above the
# tool-loop threshold with zero NEW content is the stored signature of a
# transport dying mid-call.
_TRANSPORT_DEATH_CALLS = router_core.TOOLLOOP_CALLS_N


def _strip_benign_assignments(text: str) -> str:
    """Remove benign key=<number> assignments from VALID result text before
    infra matching. Best-effort; never raises."""
    try:
        return _BENIGN_ASSIGNMENT_RE.sub(" ", text or "")
    except Exception:  # noqa: BLE001
        return text or ""


def _matches_infra(text: str) -> bool:
    """True when the (benign-stripped) text matches any infra pattern."""
    try:
        stripped = _strip_benign_assignments(text)
        return any(rx.search(stripped) for rx in _INFRA_RES)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# The classifier (evidence-counting on stored state — deterministic)
# ---------------------------------------------------------------------------


def classify_struggle(task_id: str, reason_code: str) -> Tuple[str, str]:
    """Classify WHY struggle fired. Returns (kind, detail).

    kind in {infra, reasoning, ambiguous}; detail is a short non-content
    evidence label (safe for route logs). Never raises; missing state ->
    (ambiguous, "no_data").
    """
    try:
        task_rec = router_core.task_state(task_id or "")
        if not task_rec and not reason_code:
            return KIND_AMBIGUOUS, NO_DATA

        # Arm 1 — infra: stored failure TEXT matches infra patterns. The raw
        # text exists only because record_provider_failure now persists
        # last_fail_text (<=240 chars); a hash is not matchable evidence.
        fail_text = str(task_rec.get("last_fail_text") or "")
        has_fail_text = bool(fail_text.strip())
        if has_fail_text and _matches_infra(fail_text):
            return KIND_INFRA, "fail_text_infra_pattern"

        if reason_code == "repeated_same_failure":
            # Valid-but-failing: stored failure text exists and does NOT match
            # infra -> reasoning. A bare hash (no stored text) is evidence-thin
            # -> ambiguous, logged honestly.
            if has_fail_text:
                return KIND_REASONING, "valid_response_repeat_failure"
            return KIND_AMBIGUOUS, NO_DATA

        if reason_code == "tool_loop_no_new_content":
            # Transport death vs valid-but-unchanged (v3.3.0 evidence split):
            # transport death = the no-new-content loop fired with zero
            # SUBSTANTIVE result contents this turn (empty/erroring results —
            # the transport never delivered anything; an empty result hashes
            # to sha256('') and carries no distinct content). Non-empty results
            # that keep repeating (>=1 distinct NON-EMPTY hash) are VALID
            # responses that are semantically unchanged -> reasoning.
            with router_core._LOCK:
                loop_rec = dict(router_core._TOOLLOOP_STATE.get(task_id or "", {}))
            distinct = int(loop_rec.get("distinct_results", 0) or 0)
            has_empty_only = (loop_rec.get("last_tool_result_text") or "") == "" \
                and distinct <= 1
            if distinct == 0 or has_empty_only:
                return KIND_INFRA, "transport_no_new_content"
            return KIND_REASONING, "results_semantically_unchanged"

        if reason_code == "user_struggle_signal":
            # User phrasing is a CONFIRMING signal, never sole evidence (Astra).
            # Classification from stored tool evidence when it exists.
            if has_fail_text:
                if _matches_infra(fail_text):
                    return KIND_INFRA, "fail_text_infra_pattern"
                return KIND_REASONING, "valid_response_repeat_failure"
            return KIND_AMBIGUOUS, "user_signal_only"

        # Unknown reason / unknown state: honest ambiguous.
        return KIND_AMBIGUOUS, NO_DATA
    except Exception:  # noqa: BLE001 — classification must never raise
        return KIND_AMBIGUOUS, NO_DATA


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _test_reset() -> None:
    """Tests-only: this module holds no state of its own (all evidence lives
    in router_core task state); provided for symmetry with router_core."""
    return None