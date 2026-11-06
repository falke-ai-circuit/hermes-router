"""R78U M45-25 — per-task consult budget for the frontier/higher-self lane.

The defect this closes (owner contract, measured on the live decision_ledger):
conductor produced 2709 frontier rows over 223 asks — 95 asks exceeded a 1+1
(pre + post) budget, 72 asks took 3 consults, outliers at 13, 22 and 640.
The lane had per-TURN dedupe (_SWAP_DONE_TTL) and an every-N post cadence, but
nothing that counted consults for a TASK and refused the nth+1 before the call.

Contract implemented here:

  RULE 1  one PRE and one POST consult per task, maximum. The third attempt on
          the same task is REFUSED and RECORDED (trigger='consult_capped') —
          auditable, never silent, and never sent to the frontier model.
  RULE 2  short tasks emit neither leg: a task must reach `consult_min_turns`
          substantive turns before either leg may fire. The knob exists and is
          read live, but its DEFAULT IS 0 = OPT-IN and NO profile sets it, so
          RULE 2 IS NOT ENFORCED OUT OF THE BOX. That is a recorded deferral,
          not an oversight (M45-25b, measured):
            * floor = 1 is a NO-OP: turn_no is 1-based, so `0 < turn < 1` never
              holds and the floor can never refuse anything.
            * floor = 2 suppresses every FIRST-TURN consult. 4 declared-form
              pins require that behaviour (test_v320_one_consult_per_turn::
              test_pre_middleware_logs_consult_deduped and ..._second_new_ask_
              routes_normally, test_rider16_fixes::test_anchored_failure_parks_
              fail_loud_banner, test_single_claim_point::test_leg7c_auto_then_
              midturn_tool_claim_no_second_consult): 15 failed / 425 passed vs
              the 11-failure pre-existing baseline.
            * floor = 3 was measured by the reviewer at 25 pins red.
          The threshold is session-scoped while the budget is task-scoped, so no
          value enforces RULE 2 without killing declared consults. DEFERRED to
          owner sign-off; until then, set consult_min_turns per profile only if
          you accept losing first-turn consults.
  RULE 3  the complexity/impact gate stays in FRONT of this module — this is a
          budget check placed immediately before the spend, not a routing gate.

Scope key: task_id when present, else session + trace (`session|trace`), per the
contract's fallback rule. Counting is in-process (the gateway that bills also
counts); a restart resets counters, which fails OPEN toward the existing gates
rather than toward an unspendable lane.

Never raises: a budget module that breaks a turn is worse than a spend.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_PER_TASK = 1     # per leg (one PRE + one POST)
DEFAULT_MIN_TURNS = 0        # RULE 2 knob — 0 = OFF / opt-in (see the header:
                             # the floor is unenforced until a profile sets it)

CAPPED_TRIGGER = "consult_capped"

# Bounded store: a gateway lives for weeks and sees an unbounded stream of
# distinct asks, so an ever-growing per-ask dict is a leak, not a counter.
MAX_SCOPES = 4096

_LOCK = threading.Lock()
_COUNTS: Dict[str, Dict[str, int]] = {}
_TURNS_SEEN: Dict[str, int] = {}


def _knob_int(name: str, default: int) -> int:
    """Live-read a top-level router-section int knob (canonical reader)."""
    try:
        from . import config_access

        return int((config_access.router_section() or {}).get(name, default))
    except Exception:  # noqa: BLE001 — config read never breaks a turn
        return default


def max_per_task() -> int:
    """consult_max_per_task (int, default 1) — consults allowed PER LEG per task."""
    try:
        return max(0, _knob_int("consult_max_per_task", DEFAULT_MAX_PER_TASK))
    except Exception:  # noqa: BLE001
        return DEFAULT_MAX_PER_TASK


def min_turns() -> int:
    """consult_min_turns (int, default 0 = off; set >0 to enforce RULE 2)."""
    try:
        return max(0, _knob_int("consult_min_turns", DEFAULT_MIN_TURNS))
    except Exception:  # noqa: BLE001
        return DEFAULT_MIN_TURNS


def turn_no(session_id: str) -> int:
    """The session's canonical turn counter (never reset by a consult)."""
    try:
        from . import advisory_consumption as _ac

        return int(_ac.turn_no(session_id) or 0)
    except Exception:  # noqa: BLE001
        return 0


def scope_key(session_id: str, task_id: str = "", ask: str = "",
              model: str = "", trace: str = "") -> str:
    """Task identity for the counter: task_id, else session+trace.

    `trace` falls back to the deterministic task hash of the ask, so a caller
    with no task_id still groups by the same ask rather than by nothing.
    """
    tid = str(task_id or "").strip()
    if tid:
        return tid
    sid = str(session_id or "").strip()
    tr = str(trace or "").strip()
    if not tr and ask:
        try:
            from . import router_core

            tr = str(router_core.task_id_for(sid, ask, model) or "")
        except Exception:  # noqa: BLE001
            tr = ""
    return "%s|%s" % (sid, tr or "-")


def _record_refusal(session_id: str, task_id: str, leg: str, key: str,
                    count: int, cap: int, reason: str, model: str = "") -> None:
    """Write the auditable refusal row + a route-log line. Never raises."""
    try:
        from . import decision as _dm

        _dm.ledger_write({
            "session_id": str(session_id or ""),
            "task_id": str(task_id or key),
            "trigger": CAPPED_TRIGGER,
            "trigger_kind": "capped",
            "fork_class": "not_a_fork",
            "model": str(model or ""),
            "choice": "refused",
            "fail_open_reason": CAPPED_TRIGGER,
            "verdict_json": '{"leg": "%s", "count": %d, "cap": %d, "reason": "%s"}'
                            % (str(leg), int(count), int(cap), str(reason)),
            "actual_choice": "refused",
        })
    except Exception:  # noqa: BLE001 — audit never breaks the refusal path
        logger.debug("consult_capped ledger write failed", exc_info=True)
    try:
        from .core.telemetry import log_route

        log_route("PRE" if leg == "pre" else "POST",
                  event_detail=CAPPED_TRIGGER, leg=str(leg),
                  count=int(count), cap=int(cap), reason=str(reason),
                  task_id=str(task_id or key), session_id=str(session_id or ""))
    except Exception:  # noqa: BLE001
        pass


def check(session_id: str, task_id: str = "", leg: str = "pre",
          ask: str = "", model: str = "", trace: str = "") -> Dict[str, Any]:
    """Gate ONE consult leg. Call this IMMEDIATELY BEFORE the spend.

    Returns {"allow": bool, "leg", "key", "count", "cap", "turn", "reason"}.
    A refused attempt is RECORDED (ledger row, trigger='consult_capped') and
    does not consume budget. An allowed attempt consumes it.

    Order: complexity gate (caller) -> min-turn threshold -> per-task cap.
    Never raises; on internal error it ALLOWS (fail-open to the existing gates).
    """
    out: Dict[str, Any] = {"allow": True, "leg": str(leg), "key": "",
                           "count": 0, "cap": 0, "turn": 0, "reason": ""}
    try:
        key = scope_key(session_id, task_id, ask=ask, model=model, trace=trace)
        cap = max_per_task()
        floor = min_turns()
        turn = turn_no(session_id)
        out.update({"key": key, "cap": cap, "turn": turn})
        with _LOCK:
            # RULE 2: a task under the floor emits nothing. `turn == 0` means
            # the session's turn counter was never opened (unit harness or a
            # caller outside the turn lifecycle, e.g. an API/tool dispatch) —
            # an UNKNOWN turn must fail open, never silently kill the lane.
            if floor > 0 and 0 < turn < floor:
                slot = _COUNTS.get(key) or {}
                count = int(slot.get(str(leg), 0))
                out.update({"allow": False, "count": count,
                            "reason": "below_min_turns"})
                _record_refusal(session_id, task_id, leg, key, count, cap,
                                "below_min_turns", model)
                return out
            slot = _COUNTS.setdefault(key, {})
            count = int(slot.get(str(leg), 0))
            if count >= cap:
                out.update({"allow": False, "count": count,
                            "reason": CAPPED_TRIGGER})
                _record_refusal(session_id, task_id, leg, key, count, cap,
                                CAPPED_TRIGGER, model)
                return out
            slot[str(leg)] = count + 1
            _TURNS_SEEN[key] = turn
            _prune_locked()
            out.update({"allow": True, "count": count + 1})
            return out
    except Exception:  # noqa: BLE001 — a broken counter must not stop work
        logger.debug("consult_budget.check failed (fail-open)", exc_info=True)
        return out


def _prune_locked() -> None:
    """Bound the counter store by dropping the OLDEST scopes (dict order).

    Called with _LOCK held. Per-scope counters are only needed until the task
    ends; evicting the oldest scope costs at most one task its budget, while an
    unbounded dict costs the gateway memory forever.
    """
    while len(_COUNTS) > MAX_SCOPES:
        _victim = next(iter(_COUNTS))
        _COUNTS.pop(_victim, None)
        _TURNS_SEEN.pop(_victim, None)
    while len(_TURNS_SEEN) > MAX_SCOPES:
        _TURNS_SEEN.pop(next(iter(_TURNS_SEEN)), None)


def refund(session_id: str = "", task_id: str = "", leg: str = "pre",
           key: str = "", ask: str = "", model: str = "",
           trace: str = "") -> bool:
    """Give back ONE consumed slot after a call that produced NO provider response.

    The slot is spent at ALLOW time, before the call. A call that raises, times
    out, or comes back with an empty completion never billed the provider, so
    leaving it spent would permanently refuse the same task's next legitimate
    attempt — which is exactly the documented async recovery (a slow consult
    delivers its verdict next turn, so the task must still be allowed to try).
    Keep the slot consumed only when a response actually arrived.

    Idempotent and clamped at zero (a double refund, or a refund after a reset,
    is a no-op). Never raises.
    """
    try:
        k = str(key or "") or scope_key(session_id, task_id, ask=ask,
                                        model=model, trace=trace)
        lg = str(leg)
        with _LOCK:
            slot = _COUNTS.get(k)
            if not slot:
                return False
            count = int(slot.get(lg, 0) or 0)
            if count <= 0:
                return False
            if count == 1:
                slot.pop(lg, None)
                if not slot:
                    _COUNTS.pop(k, None)
                    _TURNS_SEEN.pop(k, None)
            else:
                slot[lg] = count - 1
        return True
    except Exception:  # noqa: BLE001 — a refund must never break a turn
        logger.debug("consult_budget.refund failed", exc_info=True)
        return False


def counts(session_id: str = "", task_id: str = "", key: str = "") -> Dict[str, int]:
    """Read-only snapshot of the counter for a scope (tests + diagnostics)."""
    try:
        k = str(key or "") or scope_key(session_id, task_id)
        with _LOCK:
            return dict(_COUNTS.get(k) or {})
    except Exception:  # noqa: BLE001
        return {}


def reset(session_id: str = "", task_id: str = "", key: str = "") -> None:
    """Drop counters for one scope (tests only; "" + "" clears all)."""
    try:
        with _LOCK:
            k = str(key or "")
            if k:
                _COUNTS.pop(k, None)
                _TURNS_SEEN.pop(k, None)
                return
            if session_id or task_id:
                _COUNTS.pop(scope_key(session_id, task_id), None)
                return
            _COUNTS.clear()
            _TURNS_SEEN.clear()
    except Exception:  # noqa: BLE001
        pass
