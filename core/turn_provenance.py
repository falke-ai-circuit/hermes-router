"""core/turn_provenance.py — WHICH LANE PRODUCED THE BODY (M35-01).

THE DEFECT THIS EXISTS FOR
    The decisions ledger held 6233 rows whose newest ones read
    ``{"ts": ..., "action": "pass"}`` and nothing else: no lane id, no rule, no
    gate verdict, no intent class. When the uncensored lane answered a turn,
    that row could not be told apart from the main agent answering on its own
    path — so the lane itself could not be graded. A ledger that cannot say who
    spoke cannot score what was said.

WHAT THIS IS
    An INSTRUMENT, not a behaviour change. It never decides, never routes,
    never renders, never calls out (no aux spend, no new I/O). It collects the
    facts the POST seam already computed and writes ONE row per eligible turn
    into the EXISTING decisions ledger (`core/decisions.py` — no new file).
    Routing is untouched: this module is written to, never read from, by the
    routing path.

THE ROW (schema v2, see decisions.record_decision)
    lane              "shadow" when a lane render was delivered, "main" when the
                      agent's own body was (no lane text reached the user)
    rule              rule_1..rule_6, "none" when no row matched
    gate_verdict      flagged | not_flagged | abstain
    gate_reason       the gate record's own token, "not_consulted" on a turn
                      whose rule never read the gate (rule 1), "no_record" when
                      it was read and no record had landed
    intent_class      the POST judge's class ("" when never consulted)
    intent_source     deterministic | aux | unknown | not_consulted
    replaced          True iff the DELIVERED body came from a lane render
    replacement_path  the chain entry that produced it ("" when not replaced)
    latency_ms        synchronous wall time this turn spent inside the seam
    turn_id           join key to the body, together with session_id

WHY A NON-ROUTING TURN IS STILL WRITTEN
    A benign turn's absence of a route IS the measurement of selectivity. A
    ledger that only records routes cannot distinguish "the lane declined this
    turn" from "the lane was never asked".

TOTALITY
    The seam wrapper calls `finish()` in a `finally`, so a row lands on every
    path — routed, benign, withheld, failed, or an exception. `finish()` is
    idempotent per turn (one row, never two for the same delivery).
"""
from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from typing import Any, Dict, Optional

_CURRENT: ContextVar[Optional["TurnProvenance"]] = ContextVar(
    "hermes_router_turn_provenance", default=None)

# Positive, host-declared ephemeral seams (same evidence M34-01 uses): these
# threads never deliver a user turn, so a provenance row for one would be a
# row about a body nobody read.
_EPHEMERAL_THREAD_MARKS = ("bg-review", "curator-review")

LANE_MAIN = "main"

#: The host plugin runner abandons an in-flight ``transform_llm_output`` at
#: 30 s ("timed out after 30s — skipping", live 2026-11-02 23:55:32Z, B3).
#: A render that settles at or after that wall time cannot reach the response
#: surface: the host has already emitted the pre-hook body. Measured, not
#: assumed — B3's render settled at 44.7 s and the wire carried the refusal.
#: Patchable for tests; the value is the host's contract, not a preference.
HOST_HOOK_KILL_S = 30.0

# ---------------------------------------------------------------------------
# M35-04 TOTALITY — a turn cannot leave the seam without a row.
#
# THE DEFECT: the row was written by the transform hook's `finally`. When the
# host never calls that hook — it skips an invocation after a slow/overlapping
# one, and it does not call it at all on an interrupted or empty turn — the
# turn produced NO row (M35 battery rows A2 and C1: two of ten turns absent
# from the ledger, so nothing could say what became of them).
#
# THE FIX: the PRE middleware ARMS a pending turn, the POST edge DISARMS it,
# and two close edges write the skipped row when POST never arrives — the
# post_llm_call observer (which sees the delivered body) and a deadline
# sweeper (which sees the turn nobody observed at all). One row, always.
# ---------------------------------------------------------------------------
SEAM_SKIPPED_ACTION = "skipped"
SEAM_SKIPPED_OUTCOME = "seam_skipped"
SKIP_REASON_UNNAMED = "unnamed_skip"
SKIP_REASON_POST_SEAM_NOT_REACHED = "post_seam_not_reached"
SKIP_REASON_NO_DELIVERY_EDGE = "no_delivery_edge_within_deadline"
SKIP_REASON_SUPERSEDED = "superseded_by_next_turn"

#: Longest a turn may sit armed before the sweeper closes it. The previous
#: hook invocation is abandoned at HOST_HOOK_KILL_S, so a turn whose POST pass
#: has not arrived well after that is not coming: 15 s of margin covers the
#: finalizer writing the transcript and the platform emitting the body.
SKIP_DEADLINE_S = HOST_HOOK_KILL_S + 15.0
_PENDING_MAX = 1024
_SWEEP_INTERVAL_S = 1.0

_PENDING: Dict[str, Dict[str, Any]] = {}
_PENDING_LOCK = threading.Lock()
_SWEEPER_STARTED = False

#: transform passes currently inside the seam, thread id -> monotonic start.
#: Used ONLY to predict the host's skip at the PRE edge (see `seam_blocked`).
_INFLIGHT: Dict[int, float] = {}



def render_is_deliverable(elapsed_s: float,
                          budget_s: float = HOST_HOOK_KILL_S) -> bool:
    """True when a render settling after ``elapsed_s`` can still be delivered.

    False means the host has already given up on this hook invocation, so the
    body it returned to the caller is the PRE-swap text. Calling that a
    replacement is the defect (B3): the render exists in the store, the user
    read the refusal. Never raises — an unreadable clock is treated as
    deliverable so a broken clock cannot suppress a good render.
    """
    try:
        return float(elapsed_s) < float(budget_s)
    except Exception:  # noqa: BLE001
        return True



def is_ephemeral_seam() -> bool:
    """True only on a DECLARED ephemeral thread. Unknown threads are eligible —
    absence of a marker is not evidence of a seam (fail-open, same contract as
    the routing-side probe). Never raises."""
    try:
        name = str(threading.current_thread().name or "")
    except Exception:  # noqa: BLE001
        return False
    for mark in _EPHEMERAL_THREAD_MARKS:
        if name == mark or name.startswith(mark + "-"):
            return True
    return False


class TurnProvenance:
    """One turn's provenance, filled in as the seam learns it."""

    __slots__ = ("session_id", "model", "turn_id", "started", "rule",
                 "gate_verdict", "gate_reason", "intent_class",
                 "intent_source", "declaration", "lane", "replaced",
                 "replacement_path", "outcome", "body_chars", "_emitted",
                 "_predicate", "_action", "delivered", "delivery_failure",
                 "deferred", "deferred_reason", "skip_reason")

    def __init__(self, session_id: str = "", model: str = "", turn_id: str = ""):
        self.session_id = str(session_id or "")
        self.model = str(model or "")
        self.turn_id = str(turn_id or "")
        self.started = time.monotonic()
        # Not-yet-known facts read as their honest absence, never as a value.
        self.rule = "none"
        self.gate_verdict = "abstain"
        self.gate_reason = "not_consulted"
        self.intent_class = ""
        self.intent_source = "not_consulted"
        self.declaration = "UNKNOWN"
        self.lane = LANE_MAIN
        self.replaced = False
        self.replacement_path = ""
        self.outcome = "delivered"
        self.body_chars = 0
        self._predicate = ""
        self._action = ""
        self._emitted = False
        # M35-04: WHY a turn produced no routing decision — the named reason a
        # `skipped` row carries. Empty on every turn the seam actually saw.
        self.skip_reason = ""
        # M35-02: wire truth — None until the response surface is settled.
        # A replaced turn with delivered=None is NOT a delivered turn.
        self.delivered: Optional[bool] = None
        self.delivery_failure = ""
        # M35-03 (A3/B1/B2): True when the lane could NOT decide this turn
        # (gate abstained — aux returned nothing). The body may still ship,
        # but the row must say the judgement was deferred, never that the
        # lane decided it and never that rule 6 delivered it.
        self.deferred = False
        self.deferred_reason = ""

    # -- facts the seam supplies ------------------------------------------
    def decision(self, *, rule_row: int = 0, rule_predicate: str = "",
                 action: str = "", gate: Any = None, gate_reason: str = "",
                 intent_class: str = "", intent_source: str = "",
                 declaration: str = "", body_chars: int = -1) -> None:
        """Record the rule-table outcome and the facts it consumed.

        `gate` is the verdict string when the row READ the gate, else None —
        which is why rule 1's row says `not_consulted` rather than borrowing a
        verdict it never looked at.
        """
        row = int(rule_row or 0)
        self.rule = f"rule_{row}" if row > 0 else "none"
        self._predicate = str(rule_predicate or "")  # noqa: SLF001 — see row()
        self._action = str(action or "")             # noqa: SLF001
        if gate is None:
            self.gate_reason = "not_consulted"
        else:
            self.gate_verdict = str(gate or "abstain")
            self.gate_reason = str(gate_reason or "no_record")
        self.intent_class = str(intent_class or "")
        self.intent_source = (str(intent_source or "not_consulted")
                              if intent_class else "not_consulted")
        self.declaration = str(declaration or "UNKNOWN")
        # The body the DECISION was made on: the seam strips the routing tag
        # before any detector reads it, so the length recorded at `begin` (the
        # raw pass-in) would overstate it. -1 = caller supplies nothing.
        if int(body_chars) >= 0:
            self.body_chars = int(body_chars)

    def delivery(self, *, lane: str, replaced: bool,
                 replacement_path: str = "", outcome: str = "",
                 delivered: Optional[bool] = None) -> None:
        """The delivered body's provenance — set where the body is settled.

        `delivered` is the WIRE fact, not the decision: leave it None when the
        caller cannot say what the response surface will carry. The row then
        refuses to claim delivery for a replaced turn (see `row`).
        """
        self.lane = str(lane or LANE_MAIN)
        self.replaced = bool(replaced)
        self.replacement_path = str(replacement_path or "")
        if outcome:
            self.outcome = str(outcome)
        if delivered is not None:
            self.delivered = bool(delivered)

    def defer(self, reason: str = "") -> None:
        """Name a DEFERRED lane judgement (M35-03, battery rows A3/B1/B2).

        The gate abstained, so no domain verdict exists to render against and
        the lane did not decide this turn. Recording that is the point: the
        alternative — the turn looking like any benign delivery — is what let
        three hard asks read as "the lane had nothing to say".
        """
        self.deferred = True
        self.deferred_reason = str(reason or "gate_abstain")

    def skip(self, reason: str = "") -> None:
        """Name a turn that left the seam WITHOUT a POST pass (M35-04).

        The M35 battery shipped two turns with no row at all (A2, C1) because
        the host skipped the transform hook; there was nothing on disk to say
        the turn had happened, let alone what became of it. This marks the
        turn as SKIPPED — an outcome, with a reason — rather than silence.
        Routing is untouched: the turn was never decided, and the row says so.
        """
        self._action = SEAM_SKIPPED_ACTION
        self.outcome = SEAM_SKIPPED_OUTCOME
        self.skip_reason = str(reason or SKIP_REASON_UNNAMED)
        # No lane body was ever selected: what the user received was the
        # agent's own path, unexamined.
        self.lane = LANE_MAIN
        self.replaced = False
        self.replacement_path = ""

    def withhold(self, reason: str = "") -> None:
        """Name a turn whose body was WITHHELD at the delivery boundary.

        Reached when marker text survived the strip: the bytes are not
        delivered at all (a loud failure body goes out instead), so the row
        must not read like an ordinary delivery of the agent's text.
        """
        self._action = "withheld"
        self.outcome = "marker_leak_withheld"
        self.skip_reason = str(reason or "marker_survived_boundary_strip")
        self.lane = LANE_MAIN
        self.replaced = False
        self.replacement_path = ""


def begin(*, session_id: str = "", model: str = "", body: str = "") -> TurnProvenance:
    """Open this pass's provenance and make it current for the thread."""
    turn_id = ""
    try:
        from . import state as _state

        turn_id = str(_state.current_turn_id(session_id or ""))
    except Exception:  # noqa: BLE001 — identity is a join key, never a gate
        turn_id = ""
    turn = TurnProvenance(session_id=session_id, model=model, turn_id=turn_id)
    turn.body_chars = len(str(body or ""))
    _CURRENT.set(turn)
    return turn


def current() -> Optional[TurnProvenance]:
    """The turn open on this thread, if any."""
    return _CURRENT.get()


def note_decision(**kwargs: Any) -> None:
    """Fail-quiet shorthand for `current().decision(...)`."""
    turn = _CURRENT.get()
    if turn is None:
        return
    try:
        turn.decision(**kwargs)
    except Exception:  # noqa: BLE001 — instrumentation never breaks a turn
        pass


def note_delivery(**kwargs: Any) -> None:
    """Fail-quiet shorthand for `current().delivery(...)`."""
    turn = _CURRENT.get()
    if turn is None:
        return
    try:
        turn.delivery(**kwargs)
    except Exception:  # noqa: BLE001
        pass


def settle_delivery(turn: Optional["TurnProvenance"], final: Any, *,
                    log=None, session_id: str = "") -> str:
    """Reconcile the turn's row with the body that will ACTUALLY be returned.

    The seam decides a replacement (``replaced=True``) and the delivery edge
    may still drop it: the POST guards return ``None`` to keep the agent's own
    body, and the host abandons the hook at its kill time. Either way the wire
    carries the main body, and a row that keeps saying ``replaced=True`` is
    the M35-02 defect — a ledger the caller's bytes contradict.

    Returns the named failure ("" when the turn's claim survives). On a guard
    drop the row is rewritten to the truth it can defend: lane=main,
    replaced=False, delivered=True (the main body IS what shipped), outcome
    names the suppression. Never raises.
    """
    if turn is None:
        return ""
    try:
        # A wire failure already recorded at the render edge (the host killed
        # the hook before the render settled) outranks anything this edge can
        # infer: the turn is settled, and a later None must not relabel it.
        if turn.delivered is False and turn.replaced:
            return turn.delivery_failure or "render_not_delivered"
        if turn.replaced and final is None:
            # `replaced` keeps its schema-v2 meaning — the DELIVERED body came
            # from a lane render — so a guard that keeps the agent's body makes
            # it False. `delivered` keeps its M35-02 meaning — the render the
            # seam selected reached the response surface — so it is False too.
            # What ships is the main body, and the failure is named.
            turn.replaced = False
            turn.lane = LANE_MAIN
            turn.replacement_path = ""
            turn.delivered = False
            turn.outcome = "post_guard_suppressed"
            turn.delivery_failure = "post_guard_suppressed"
            if log is not None:
                try:
                    log("POST", event_detail="render_not_delivered",
                        reason="post_guard_suppressed",
                        severity="warning",
                        replaced_was=True,
                        body_chars=int(turn.body_chars or 0),
                        session_id=session_id)
                except Exception:  # noqa: BLE001 — telemetry never breaks delivery
                    pass
            return "post_guard_suppressed"
        turn.delivered = True
        return ""
    except Exception:  # noqa: BLE001 — the settle never breaks delivery
        return ""


def row(turn: Optional[TurnProvenance], *, delivered: Any = None) -> Dict[str, Any]:
    """The ledger fields for one turn. Pure — builds the dict, emits nothing."""
    if turn is None:
        return {}
    delivered_chars = 0
    delivered_flag = bool(turn.delivered) if turn.delivered is not None \
        else (not turn.replaced)
    try:
        if delivered is None:
            # The pass-through contract: `None` tells the host "deliver the
            # body you gave me", so the delivered text IS this turn's body.
            # Recording 0 would make the row deny the body it describes — the
            # exact join failure this instrument exists to prevent.
            if turn.outcome != "seam_error":
                delivered_chars = int(turn.body_chars or 0)
        else:
            delivered_chars = len(str(delivered or ""))
    except Exception:  # noqa: BLE001
        delivered_chars = 0
    latency_ms = max(0.0, (time.monotonic() - turn.started) * 1000.0)
    return {
        "lane": turn.lane,
        "turn_id": turn.turn_id,
        "rule": turn.rule,
        "gate_verdict": turn.gate_verdict,
        "gate_reason": turn.gate_reason,
        "intent_class": turn.intent_class,
        "intent_source": turn.intent_source,
        "replaced": bool(turn.replaced),
        "delivered": bool(delivered_flag),
        "replacement_path": turn.replacement_path,
        "latency_ms": latency_ms,
        "body_chars": turn.body_chars,
        "delivered_chars": delivered_chars,
        "session_id": turn.session_id,
        "action": str(getattr(turn, "_action", "") or "deliver"),
        "outcome": turn.outcome,
        # M35-03: the lane's judgement was DEFERRED this turn (gate abstained).
        # False on every turn the table actually decided — including a delivered
        # benign one — so "deferred" never blurs into "the lane chose main".
        "deferred": bool(getattr(turn, "deferred", False)),
        "deferred_reason": str(getattr(turn, "deferred_reason", "") or ""),
        # M35-04: the NAMED reason a skipped turn produced no decision.
        "skip_reason": str(getattr(turn, "skip_reason", "") or ""),
    }


def finish(turn: Optional[TurnProvenance], *, delivered: Any = None,
           outcome: str = "") -> bool:
    """Emit the turn's ONE provenance row. Idempotent. Never raises, never
    blocks the turn (decisions.record_decision is serialize + enqueue)."""
    if turn is None:
        return False
    try:
        if turn._emitted:  # noqa: SLF001 — the once-per-turn guard
            return False
        turn._emitted = True  # noqa: SLF001
        if outcome:
            turn.outcome = str(outcome)
        if not turn.session_id:
            return False
        if is_ephemeral_seam():
            return False
        facts = row(turn, delivered=delivered)
        from . import decisions

        return bool(decisions.record_decision(
            detector_version="post-seam",
            rule_id=str(facts.get("rule") or "none"),
            action=str(facts.get("action") or "deliver"),
            outcome=str(facts.get("outcome") or ""),
            session_id=str(facts.get("session_id") or ""),
            latency_ms=float(facts.get("latency_ms") or 0.0),
            **{k: facts[k] for k in (
                "lane", "turn_id", "gate_verdict", "gate_reason",
                "intent_class", "intent_source", "replaced", "delivered",
                "replacement_path", "body_chars", "delivered_chars",
                "deferred", "deferred_reason", "skip_reason")},
        ))
    except Exception:  # noqa: BLE001 — observability never breaks delivery
        return False
    finally:
        try:
            _CURRENT.set(None)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# M35-04 — the pending-turn registry: PRE arms, POST disarms, two edges close.
# ---------------------------------------------------------------------------


def _turn_id_for(session_id: str) -> str:
    """The session's current turn identity ("" when unavailable). Never raises."""
    try:
        from . import state as _state

        return str(_state.current_turn_id(session_id or ""))
    except Exception:  # noqa: BLE001 — identity is a join key, never a gate
        return ""


def seam_enter() -> None:
    """Mark this thread's transform pass as IN FLIGHT.

    Cheap (one dict write) and only read by `seam_blocked`. The host kills a
    callback at HOST_HOOK_KILL_S and then SKIPS later invocations while it is
    still running; knowing that at the PRE edge is what lets the router stop
    asking for a marker it can no longer strip.
    """
    try:
        _INFLIGHT[threading.get_ident()] = time.monotonic()
    except Exception:  # noqa: BLE001 — instrumentation never breaks a turn
        pass


def seam_exit() -> None:
    """Clear this thread's in-flight mark."""
    try:
        _INFLIGHT.pop(threading.get_ident(), None)
    except Exception:  # noqa: BLE001
        pass


def seam_blocked(now: Optional[float] = None) -> bool:
    """True when an earlier transform pass has already overrun the host kill.

    That is the measured skip condition ("skipped after previous timeout or
    while still running"): the host will not invoke this turn's transform hook,
    so anything the router asked the model to emit can no longer be stripped.
    Advisory only — it never blocks a turn, it only suppresses an unremovable
    marker request.
    """
    try:
        stamp = time.monotonic() if now is None else float(now)
        return any(stamp - started >= HOST_HOOK_KILL_S
                   for started in list(_INFLIGHT.values()))
    except Exception:  # noqa: BLE001
        return False


def arm(session_id: str, *, body_chars: int = 0, model: str = "") -> bool:
    """Arm the pending turn at the PRE edge. Idempotent per (session, turn).

    Tool-loop passes of the SAME turn re-arm without touching the original
    deadline — a long turn must not look like a lost one. A genuinely NEW turn
    on a session that still holds an armed one closes the old as
    `superseded_by_next_turn`: that turn left without a POST pass, and the row
    says so instead of the entry being overwritten in silence.
    """
    sid = str(session_id or "")
    if not sid:
        return False
    turn_id = _turn_id_for(sid)
    superseded = None
    try:
        with _PENDING_LOCK:
            prev = _PENDING.get(sid)
            if prev is not None:
                if str(prev.get("turn_id") or "") == turn_id:
                    prev["body_chars"] = max(int(prev.get("body_chars") or 0),
                                             int(body_chars or 0))
                    return True
                superseded = _PENDING.pop(sid)
            _PENDING[sid] = {"session_id": sid, "turn_id": turn_id,
                             "model": str(model or ""),
                             "started": time.monotonic(),
                             "body_chars": int(body_chars or 0)}
            while len(_PENDING) > _PENDING_MAX:
                _PENDING.pop(next(iter(_PENDING)))
    except Exception:  # noqa: BLE001 — the registry never breaks a turn
        return False
    if superseded is not None:
        _emit_skipped(superseded, SKIP_REASON_SUPERSEDED)
    _ensure_sweeper()
    return True


def disarm(session_id: str) -> bool:
    """Remove the pending turn (the POST edge arrived). True when one was armed."""
    try:
        with _PENDING_LOCK:
            return _PENDING.pop(str(session_id or ""), None) is not None
    except Exception:  # noqa: BLE001
        return False


def pending_count() -> int:
    """How many turns are armed but not closed (observability + tests)."""
    try:
        with _PENDING_LOCK:
            return len(_PENDING)
    except Exception:  # noqa: BLE001
        return 0


def _emit_skipped(pending: Dict[str, Any], reason: str, *,
                  delivered: Any = None, outcome: str = "") -> bool:
    """Write the `skipped` row for an armed turn that never reached POST."""
    try:
        if is_ephemeral_seam():
            return False
        turn = TurnProvenance(session_id=str(pending.get("session_id") or ""),
                              model=str(pending.get("model") or ""),
                              turn_id=str(pending.get("turn_id") or ""))
        turn.started = float(pending.get("started") or turn.started)
        turn.body_chars = int(pending.get("body_chars") or 0)
        turn.skip(reason)
        return finish(turn, delivered=delivered,
                      outcome=str(outcome or SEAM_SKIPPED_OUTCOME))
    except Exception:  # noqa: BLE001 — observability never breaks delivery
        return False


def close_skipped(session_id: str, *, reason: str,
                  delivered: Any = None, outcome: str = "") -> bool:
    """Close an armed turn as `skipped`. No-op when POST already closed it.

    Called from the post_llm_call observer (which sees the delivered body, so
    the row can carry its real size) and from the sweeper (which sees a turn
    nothing observed).
    """
    try:
        with _PENDING_LOCK:
            pending = _PENDING.pop(str(session_id or ""), None)
    except Exception:  # noqa: BLE001
        return False
    if pending is None:
        return False
    return _emit_skipped(pending, reason, delivered=delivered, outcome=outcome)


def sweep_once(now: Optional[float] = None) -> int:
    """Close every armed turn older than SKIP_DEADLINE_S. Returns rows written."""
    try:
        stamp = time.monotonic() if now is None else float(now)
        stale = []
        with _PENDING_LOCK:
            for sid, entry in list(_PENDING.items()):
                started = float(entry.get("started") or stamp)
                if stamp - started >= SKIP_DEADLINE_S:
                    stale.append(_PENDING.pop(sid))
    except Exception:  # noqa: BLE001
        return 0
    written = 0
    for entry in stale:
        if _emit_skipped(entry, SKIP_REASON_NO_DELIVERY_EDGE):
            written += 1
    return written


def _sweeper_loop() -> None:
    while True:
        time.sleep(_SWEEP_INTERVAL_S)
        try:
            sweep_once()
        except Exception:  # noqa: BLE001 — a sweeper error never kills the thread
            pass


def _ensure_sweeper() -> None:
    """Start the one deadline sweeper (daemon, never joined)."""
    global _SWEEPER_STARTED
    try:
        if _SWEEPER_STARTED:
            return
        with _PENDING_LOCK:
            if _SWEEPER_STARTED:
                return
            thread = threading.Thread(target=_sweeper_loop,
                                      name="router-provenance-sweeper",
                                      daemon=True)
            thread.start()
            _SWEEPER_STARTED = True
    except Exception:  # noqa: BLE001 — no sweeper is still better than a crash
        pass


def reset_pending_for_tests() -> None:
    """Clear the registry (test isolation only)."""
    try:
        with _PENDING_LOCK:
            _PENDING.clear()
        _INFLIGHT.clear()
    except Exception:  # noqa: BLE001
        pass
