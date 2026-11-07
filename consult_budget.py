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
  RULE 2  (M46-06 owner ruling + M46-07 ruling) EVERY ask keeps the
          POSSIBILITY of a consult; the triggers — not a blanket floor — decide
          whether it fires. Mechanically:
            * the PRE floor is a REPEAT-consult floor counted PER LEG in TASK
              turns: `prior` is the PRE leg's own count, so a task whose PRE
              already spent its slot can never be floored out of its POST;
            * the FIRST consult of a task is NEVER floored — `prior == 0` on
              that leg allows it on task turn 1, on any ask, whenever the
              complexity/impact gate routed here;
            * the POST leg has its OWN age floor: `consult_post_min_turns`
              (default 4), counted in MODEL TURNS since the ORIGINAL ask
              (M46-10 re-base: one model turn = one provider call / assistant
              message row, measured by the analyst exactly that way).
              A POST below it is refused `below_min_turns` (recorded);
            * a POST whose delivery seam can no longer put the result in this
              turn's body (the legacy async topology, which consults AFTER the
              response has shipped) is refused `after_delivery` (recorded):
              the ruling requires the POST to land in the delivered body of the
              task's response, not after it.
          TWO BASES, deliberately (M46-10):
            * PRE repeat floor — task turn = session_turn - first_seen_session_turn
              + 1 (session turns: one per NEW user ask). Unchanged.
            * POST age floor — MODEL TURNS since the original ask, counted by
              `note_model_turn()` at the llm_request seam (one call per provider
              call, i.e. per assistant row). M46-08 measured DEFAULT = 4 in that
              unit; on the old session-turn basis 4 was UNREACHABLE (median user
              messages per session is 1, only 3.3% of sessions ever reach 4), so
              the shipped floor could not honour its own number. The re-base is
              the fix, not a rename.
          A scope with no observed model turn (0) fails OPEN, never floored.
          Knobs: `consult_min_turns` (default 1, PRE repeat floor; at 1 inert)
          and `consult_post_min_turns` (default 4, POST age floor in model
          turns). Read live, never cached.
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
DEFAULT_MIN_TURNS = 1        # RULE 2 knob — PRE REPEAT-consult floor in TASK
                             # turns (1 = inert: the first consult is never
                             # floored and a 1-based task turn is never < 1).
DEFAULT_POST_MIN_TURNS = 4   # RULE 2 knob — POST age floor in MODEL TURNS
                             # since the ORIGINAL ask (M46-08 measurement,
                             # implemented by M46-10 on the re-based counter).
                             # On the old session-turn basis 4 was unreachable
                             # (median 1 user message/session), which is why
                             # the basis below, not the number, was the defect.

CAPPED_TRIGGER = "consult_capped"

# Bounded store: a gateway lives for weeks and sees an unbounded stream of
# distinct asks, so an ever-growing per-ask dict is a leak, not a counter.
MAX_SCOPES = 4096

_LOCK = threading.Lock()
_COUNTS: Dict[str, Dict[str, int]] = {}
_TURNS_SEEN: Dict[str, int] = {}
# M46-06: session turn on which the scope's FIRST consult was attempted —
# the anchor that turns a session counter into a TASK-relative turn.
_TASK_BORN: Dict[str, int] = {}
# M46-10: MODEL TURNS observed for a scope since its ask — the POST floor's
# basis. Incremented by `note_model_turn()` at the llm_request seam, which the
# host fires once per provider call (= one assistant message row = the unit
# M46-08 measured). It is NOT derived from `turn_no`: that counter advances per
# NEW USER ASK, and the fleet median is 1 user message per session, so a floor
# of 4 on it was unreachable.
_MODEL_TURNS: Dict[str, int] = {}
# M46-10b: the ids of the provider calls already counted, per scope. The host
# runs the llm_request middleware inside its OWN retry loop
# (/opt/hermes/agent/conversation_loop.py:2163 while, :2240 call) and hands the
# same `api_request_id` to every retry of one provider call — so a retry is a
# re-entry, not a new model turn. Without this memo a retry double-counts and,
# once the writer/reader keys agree, the floor fires one turn EARLY (the
# unsafe direction).
_MODEL_TURN_IDS: Dict[str, list] = {}
_MODEL_ID_MEMO = 16          # bounded per scope; a retry storms within a call


def _knob_int(name: str, default: int) -> int:
    """Live-read an int knob: top-level router section, then its `complexity`
    sub-block (where the fleet configs actually carry the `consult_*` knobs).

    Measured cause of the dead knob (deployed build md5 5f40e1e8 == the build
    before this fix): the reader looked ONLY at the top-level router section,
    while all 11 fleet configs declare `consult_min_turns: 2` under
    `complexity:`. Every lookup missed, the bare `except` swallowed the miss,
    and the documented-inert default 1 was returned — a configured dial of 2
    behaved as 1 on every agent. The import below is explicit (`core.`) so the
    reader cannot depend on package-init import order to find it.
    """
    try:
        try:
            from .core import config_access
        except Exception:  # noqa: BLE001 — older layout fallback
            from . import config_access  # type: ignore[no-redef]

        sec = config_access.router_section() or {}
        if name in sec:
            try:
                return int(sec.get(name))
            except (TypeError, ValueError):
                return default
        comp = sec.get("complexity")
        if isinstance(comp, dict) and name in comp:
            try:
                return int(comp.get(name))
            except (TypeError, ValueError):
                return default
    except Exception:  # noqa: BLE001 — config read never breaks a turn
        pass
    return default


def max_per_task() -> int:
    """consult_max_per_task (int, default 1) — consults allowed PER LEG per task."""
    try:
        return max(0, _knob_int("consult_max_per_task", DEFAULT_MAX_PER_TASK))
    except Exception:  # noqa: BLE001
        return DEFAULT_MAX_PER_TASK


def min_turns() -> int:
    """consult_min_turns (int, default 1) — PRE REPEAT-consult floor in TASK turns."""
    try:
        return max(0, _knob_int("consult_min_turns", DEFAULT_MIN_TURNS))
    except Exception:  # noqa: BLE001
        return DEFAULT_MIN_TURNS


def post_min_turns() -> int:
    """consult_post_min_turns (int, default 3) — POST age floor in TASK turns.

    Counted from the ORIGINAL ask (task turn 1 = the turn the ask was first
    seen), so a POST may not fire on the same turn as the PRE that already
    spent its slot; it fires from the ask's Nth task turn onward.
    """
    try:
        return max(0, _knob_int("consult_post_min_turns", DEFAULT_POST_MIN_TURNS))
    except Exception:  # noqa: BLE001
        return DEFAULT_POST_MIN_TURNS


def turn_no(session_id: str) -> int:
    """The session's canonical turn counter (never reset by a consult)."""
    try:
        from . import advisory_consumption as _ac

        return int(_ac.turn_no(session_id) or 0)
    except Exception:  # noqa: BLE001
        return 0


def task_turn(session_id: str, key: str = "") -> int:
    """The TASK's own turn number (1-based) for `key`, or 0 when unknown.

    M46-06 counting basis: task turn = session turn - the session turn on which
    this scope was first seen + 1. The session counter is only an anchor; the
    floor compares the TASK's age, which is why a task minted on session turn
    40 still gets its first consult (task turn 1). Never raises.
    """
    try:
        k = str(key or "")
        with _LOCK:
            born = _TASK_BORN.get(k)
            seen = _TURNS_SEEN.get(k)
        if born is None:
            return 0
        cur = turn_no(session_id) or int(seen or 0)
        if cur <= 0:
            return 0
        return max(1, int(cur) - int(born) + 1)
    except Exception:  # noqa: BLE001
        return 0


def _task_turn_locked(key: str, session_turn: int) -> int:
    """Task turn under _LOCK, minting the scope's birth turn on first sight."""
    if session_turn <= 0:
        return 0  # unknown session turn -> floor stays open
    born = _TASK_BORN.get(key)
    if born is None:
        _TASK_BORN[key] = int(session_turn)
        return 1
    return max(1, int(session_turn) - int(born) + 1)


def model_turns(key: str) -> int:
    """MODEL TURNS observed for `key` since its ask (0 when none seen).

    M46-10 basis of the POST floor. 0 = unknown -> the floor fails OPEN.
    """
    try:
        with _LOCK:
            return int(_MODEL_TURNS.get(str(key or "")) or 0)
    except Exception:  # noqa: BLE001
        return 0


def _record_model_turn(session_id: str, task_id: str, key: str,
                       count: int) -> None:
    """Write ONE route row per `note_model_turn` write (M47-04 / §5 item 3).

    The writer used to be silent, and that silence is why a counter landing on
    a key nobody reads was invisible — the POST gate then reported `task_turn=0`,
    indistinguishable from a young task. One `PRE event_detail=model_turn` row
    with the scope key and the running total makes the next writer/reader
    mismatch a one-grep diagnosis.

    `task_id` follows `_record_refusal`'s convention (`task_id or key`) so the
    two families of consult rows group the same way; `key` is always the scope
    key the counter was actually written under. Never raises.
    """
    try:
        from .core.telemetry import log_route

        log_route("PRE", event_detail="model_turn",
                  task_id=str(task_id or key), key=str(key),
                  count=int(count), session_id=str(session_id or ""))
    except Exception:  # noqa: BLE001 — observability never breaks the counter
        pass


def note_model_turn(session_id: str, task_id: str = "", ask: str = "",
                    model: str = "", trace: str = "",
                    api_request_id: str = "", turn_id: str = "",
                    api_call_count: Any = None) -> int:
    """Count ONE model turn for the ask's scope and return the running total.

    Called from the llm_request seam, which fires once per provider call: every
    call produces one assistant message row, and an assistant row is exactly
    the unit M46-08 measured ("turn = one assistant message row in state.db").
    Task turn 1 = the ask's first model turn, so the counter is literally
    "model turns since the original ask".

    Idempotent PER PROVIDER CALL (M46-10b): the host applies this middleware
    inside its own `while retry_count < max_retries` loop and passes the SAME
    `api_request_id` to every retry of one call, so a re-entry with an id
    already counted returns the current total without incrementing. The id is
    `api_request_id` when supplied, else `turn_id:api_call_count`. No id at all
    = no dedupe (a direct caller is one call by construction).

    Never raises; 0 means the counter could not be updated (caller fails open).
    """
    try:
        k = scope_key(session_id, task_id, ask=ask, model=model, trace=trace)
        rid = str(api_request_id or "").strip()
        if not rid and str(turn_id or "").strip():
            rid = "%s:api:%s" % (str(turn_id).strip(),
                                 "?" if api_call_count is None
                                 else str(api_call_count))
        with _LOCK:
            n = int(_MODEL_TURNS.get(k) or 0)
            if rid:
                seen = _MODEL_TURN_IDS.get(k) or []
                if rid in seen:
                    return n        # the same provider call, retried
                # N3 (M46-10d) — BOUND: the memo keeps the last 16 ids per
                # scope, so an id that has fallen off is counted again. That
                # is reachable only by re-entering with an id older than 16
                # later calls in the SAME scope. The host computes
                # `api_request_id` ONCE, before `while retry_count <
                # max_retries` (`/opt/hermes/agent/conversation_loop.py:2138`
                # vs `:2163`), and passes that same id on every retry of that
                # call (`:2240-2249`) — so all retries of one call arrive
                # consecutively, inside a window of ONE provider call.
                # Widening the memo costs memory per live scope; the bound is
                # kept and the residual re-count is documented here instead.
                _MODEL_TURN_IDS[k] = (seen + [rid])[-_MODEL_ID_MEMO:]
            n += 1
            _MODEL_TURNS[k] = n
            _prune_locked()
        _record_model_turn(session_id, task_id, k, n)
        return n
    except Exception:  # noqa: BLE001 — a counter must never break a turn
        logger.debug("consult_budget.note_model_turn failed", exc_info=True)
        return 0


def scope_key(session_id: str, task_id: str = "", ask: str = "",
              model: str = "", trace: str = "") -> str:
    """Task identity for the counter — ONE string per scope, however built.

    `task_id` wins when the caller has it. When it does not, the same identity
    is re-derived from the ask (`task_id_for(session, ask, model)`) and returned
    BARE — not session-prefixed (M46-10b). That is the whole point: the llm
    request seam writes with the ask, the POST gate reads with a task_id, and
    before this they produced two strings that never matched (`s-<sid>|<hash>`
    vs `<hash>`), so the model-turn counter was read as 0 forever and the POST
    floor never fired on the live path.

    Only a scope with neither a task_id nor an ask falls back to `session|-`
    (the caller has told us nothing to group by).

    N5 (M46-10f) — REJECT, do not sanitise: a leading `'!'` is the reserved
    sentinel prefix (`!internal_error`), so a caller-supplied id that starts
    with it would collide with the error marker. Raises `ValueError` rather
    than rewriting the id (a silent rewrite would merge two identities). Every
    in-module caller of this function sits inside a `try/except` that already
    fails open (`note_model_turn` -> 0, `check` -> allow, `refund` -> False,
    `counts`/`reset` -> empty), so the outward "never raises" contract of those
    callers is unchanged. Reachability today: NONE (`task_id_for` returns
    `[0-9a-f]{24}` and the only other key is `"%s|-" % sid`).
    """
    tid = str(task_id or "").strip()
    if tid[:1] == "!":
        raise ValueError("scope_key: task_id must not start with '!': %r"
                         % (tid,))
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
    if tr[:1] == "!":
        raise ValueError("scope_key: trace must not start with '!': %r" % (tr,))
    if tr:
        return tr
    # N2 (M46-10d) — GUARD, semantics unchanged: this branch merges EVERY
    # empty-identity scope in a session into one counter, so a younger task
    # inherits an older task's totals. On the PRE leg that is merely coarse.
    # On the POST leg it would floor EARLY (fail-CLOSED, the unsafe
    # direction): a fresh ask would read another ask's model turns.
    # Reachability today: NONE. The llm_request writer only counts when the
    # ask text is non-empty (`gate/orchestration.py:449-451` -> `task_id_for`
    # always supplies a trace), and the POST gate skips `no_ask` before the
    # gate (`completion_audit.py:1199-1201`) and always passes a `task_id`
    # (`:2735`). A future caller with POST semantics and no task_id/ask must
    # NOT land here — pass a trace (or a task_id) instead of widening this.
    return "%s|-" % sid


def _record_refusal(session_id: str, task_id: str, leg: str, key: str,
                    count: int, cap: int, reason: str, model: str = "",
                    task_turn: int = -1) -> None:
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
            "verdict_json": '{"leg": "%s", "count": %d, "cap": %d, "reason": "%s", '
                            '"task_turn": %d}'
                            % (str(leg), int(count), int(cap), str(reason),
                               int(task_turn)),
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


def _record_decision(session_id: str, leg: str, out: Dict[str, Any],
                     task_id: str = "") -> None:
    """Record EVERY POST gate decision — allowed as well as refused (M46-10b).

    Before this only refusals were recorded, so the shipped defect was
    invisible: an ALLOWED POST with `task_turn=0` (the inert-floor signature —
    the key it read had never been written) looked exactly like a legitimately
    young task in every log. One route line per POST gate call carries the key
    that was read, the turn measured on it and the counter that turn came from,
    which makes the fail-open auditable instead of silent. Never raises.
    """
    try:
        from .core.telemetry import log_route

        # N4 (M46-10f) — `task_id` is the CALLER's identity and stays verbatim
        # (empty when the caller had none). The old fallback reused `key`, which
        # on the fail-open path is the sentinel `!internal_error`; a consumer
        # grouping `consult_gate` rows by task_id then saw one phantom task.
        # The cause is already named by `key` + `reason`, so no extra field is
        # needed and no reader has to learn a new shape.
        _key = str(out.get("key") or "")
        _tid = str(task_id) if str(task_id or "").strip() else (
            "" if _key[:1] == "!" else _key)
        log_route("POST", event_detail="consult_gate",
                  leg=str(leg), allow=bool(out.get("allow")),
                  key=_key,
                  task_turn=int(out.get("task_turn") or 0),
                  task_turn_basis=str(out.get("task_turn_basis") or ""),
                  count=int(out.get("count") or 0),
                  cap=int(out.get("cap") or 0),
                  reason=str(out.get("reason") or ""),
                  task_id=_tid,
                  session_id=str(session_id or ""))
    except Exception:  # noqa: BLE001 — observability never breaks the gate
        pass


def check(session_id: str, task_id: str = "", leg: str = "pre",
          ask: str = "", model: str = "", trace: str = "",
          deliverable: bool = True) -> Dict[str, Any]:
    """Gate ONE consult leg. Call this IMMEDIATELY BEFORE the spend.

    Returns {"allow": bool, "leg", "key", "count", "cap", "turn", "task_turn",
    "task_turn_basis", "reason"}. A refused attempt is RECORDED (ledger row,
    trigger='consult_capped') and does not consume budget. An allowed attempt
    consumes it.

    `task_turn_basis` says which counter the leg was floored on: the PRE leg is
    on `session_turns` (task-relative user asks), the POST leg on
    `model_turns` (provider calls since the original ask, M46-10).

    `deliverable` says whether a POST's result can still reach THIS turn's
    delivered body. Callers on the response-producing path pass True (default);
    the legacy async topology, which consults after the response has shipped,
    passes False and its POST is refused `after_delivery` rather than parked for
    a turn whose body cannot carry it.

    Order: complexity gate (caller) -> per-leg floor -> per-leg cap.
    Never raises; on internal error it ALLOWS (fail-open to the existing gates).
    """
    out: Dict[str, Any] = {"allow": True, "leg": str(leg), "key": "",
                           "count": 0, "cap": 0, "turn": 0, "reason": "",
                           "task_turn": 0, "task_turn_basis": ""}
    try:
        key = scope_key(session_id, task_id, ask=ask, model=model, trace=trace)
        cap = max_per_task()
        floor = post_min_turns() if str(leg) == "post" else min_turns()
        turn = turn_no(session_id)
        out.update({"key": key, "cap": cap, "turn": turn})
        with _LOCK:
            slot = _COUNTS.get(key) or {}
            count = int(slot.get(str(leg), 0))
            # M46-07 owner ruling: the floor counts prior consults PER LEG,
            # never as a task total. A task whose PRE already spent its slot
            # must still be able to reach its POST — that is the PRE->POST gap
            # this unit exists to close. `prior` is therefore THIS leg's count.
            prior = count
            # M46-10: the two legs sit on DIFFERENT bases. PRE uses the
            # task-relative SESSION turn (unchanged); POST uses MODEL TURNS
            # since the original ask, the unit M46-08 measured the default in.
            if str(leg) == "post":
                tturn = int(_MODEL_TURNS.get(key) or 0)
                basis = "model_turns"
            else:
                tturn = _task_turn_locked(key, turn)
                basis = "session_turns"
            out["task_turn"] = tturn
            out["task_turn_basis"] = basis
            # RULE 2: `prior == 0` is that leg's FIRST consult — always allowed
            # through here (the triggers already decided), never floored. The
            # POST additionally waits until the ASK has aged to
            # `consult_post_min_turns` MODEL TURNS, counted from the original
            # ask (M46-10; session turns before). A task turn of 0 means the
            # counter was never observed (unit harness / API dispatch) and must
            # fail open, never kill the lane.
            if not deliverable and str(leg) == "post":
                out.update({"allow": False, "count": count,
                            "reason": "after_delivery"})
                _record_refusal(session_id, task_id, leg, key, count, cap,
                                "after_delivery", model, tturn)
                return out
            if floor > 0 and 0 < tturn < floor and (prior > 0 or str(leg) == "post"):
                out.update({"allow": False, "count": count,
                            "reason": "below_min_turns"})
                _record_refusal(session_id, task_id, leg, key, count, cap,
                                "below_min_turns", model, tturn)
                return out
            slot = _COUNTS.setdefault(key, {})
            count = int(slot.get(str(leg), 0))
            if count >= cap:
                out.update({"allow": False, "count": count,
                            "reason": CAPPED_TRIGGER})
                _record_refusal(session_id, task_id, leg, key, count, cap,
                                CAPPED_TRIGGER, model, tturn)
                return out
            slot[str(leg)] = count + 1
            _TURNS_SEEN[key] = turn
            _prune_locked()
            out.update({"allow": True, "count": count + 1})
            return out
    except Exception:  # noqa: BLE001 — a broken counter must not stop work
        logger.debug("consult_budget.check failed (fail-open)", exc_info=True)
        # N1 (M46-10d): a fail-open decision must NAME its cause. Without this
        # the recorded row was `allow=True reason='' key=''` — identical to a
        # legitimately young task apart from the empty key, so an internal
        # error read as "the floor was never armed". `out` already carries
        # `reason=""` from the defaults, so `setdefault` cannot bite here; the
        # named marker goes on BOTH fields (the key marker cannot collide with
        # a real scope key: real keys never start with '!').
        if not out.get("reason"):
            out["reason"] = "internal_error"
        if not out.get("key"):
            out["key"] = "!internal_error"
    finally:
        # M46-10b: EVERY POST decision is recorded, allow included — the
        # `task_turn=0` allow is the signature of an inert floor and must be
        # readable without a reproduction.
        if str(leg) == "post":
            _record_decision(session_id, leg, out, task_id=task_id)
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
        _TASK_BORN.pop(_victim, None)
        _MODEL_TURNS.pop(_victim, None)
        _MODEL_TURN_IDS.pop(_victim, None)
    while len(_TURNS_SEEN) > MAX_SCOPES:
        _victim = next(iter(_TURNS_SEEN))
        _TURNS_SEEN.pop(_victim, None)
        _MODEL_TURNS.pop(_victim, None)
        _MODEL_TURN_IDS.pop(_victim, None)
    while len(_MODEL_TURNS) > MAX_SCOPES:
        _victim = next(iter(_MODEL_TURNS))
        _MODEL_TURNS.pop(_victim, None)
        _MODEL_TURN_IDS.pop(_victim, None)
        _TURNS_SEEN.pop(_victim, None)
        _TASK_BORN.pop(_victim, None)
    while len(_TASK_BORN) > MAX_SCOPES:
        _TASK_BORN.pop(next(iter(_TASK_BORN)), None)


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
                _TASK_BORN.pop(k, None)
                _MODEL_TURNS.pop(k, None)
                _MODEL_TURN_IDS.pop(k, None)
                return
            if session_id or task_id:
                _k = scope_key(session_id, task_id)
                _COUNTS.pop(_k, None)
                _TURNS_SEEN.pop(_k, None)
                _TASK_BORN.pop(_k, None)
                _MODEL_TURNS.pop(_k, None)
                _MODEL_TURN_IDS.pop(_k, None)
                return
            _COUNTS.clear()
            _TURNS_SEEN.clear()
            _TASK_BORN.clear()
            _MODEL_TURNS.clear()
            _MODEL_TURN_IDS.clear()
    except Exception:  # noqa: BLE001
        pass
