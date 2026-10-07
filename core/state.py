"""Shared mutable state for the uncensored-router plugin.

Spec §6.1 + §6.2. Two pieces of cross-hook state:

1. _PENDING — pre-router stashes the original user message after a successful
   Venice call so the post-router (transform_llm_output) can recover it;
   that hook receives neither the user message nor conversation history.
   Entries: {(session_id, model): original_user_message, rendered_hash, created_at}.
   TTL enforced at pop time; hard cap 32 entries via deque.popleft().

2. _LOOP_FIRED — loop guard keyed on (session_id, model, last_user_msg_hash).
   turn_id is NOT passed to transform_llm_output (Architect review correction),
   so we key on the user-message hash the pre-router stashes at fire time.
   Entries expire after 60s; dict capped at 256 keys (oldest evicted).

Thread safety: all access under locks. Pre-router and post-router hooks are
called sequentially in one Hermes turn loop today, but the lock guards future
concurrency (spec §6.1).
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

PendingKey = Tuple[str, str]
LoopGuardKey = Tuple[str, str, str]

_PENDING_LOCK = threading.Lock()
_PENDING: Deque[Tuple[PendingKey, Dict]] = deque()
PENDING_MAX = 32
PENDING_TTL_SECONDS = 120.0  # v3.8.5: stash is turn-scoped; 120s covers any same-turn audit gate check

_LOOP_GUARD_LOCK = threading.Lock()
_LOOP_FIRED: Dict[LoopGuardKey, float] = {}
LOOP_FIRED_MAX = 256
LOOP_FIRED_TTL_SECONDS = 60.0

_LAST_USER_MSG_LOCK = threading.Lock()
_LAST_USER_MSG: Dict[str, str] = {}  # session_id -> sha256(original_user_message)


def hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


# ---------------------------------------------------------------------------
# Pending routes (pre-router -> post-router handoff)
# ---------------------------------------------------------------------------


def stash_pending(session_id: str, model: str, original_user_message: str, rendered_content: str) -> None:
    """Pre-router: stash the original user message after a successful Venice call."""
    with _PENDING_LOCK:
        key = (session_id or "", model or "")
        _PENDING.append((key, {
            "original_user_message": original_user_message or "",
            "rendered_content_hash": hash_text(rendered_content),
            "message_hash": hash_text(original_user_message or ""),
            "created_at": time.time(),
        }))
        while len(_PENDING) > PENDING_MAX:
            _PENDING.popleft()


def has_pending_render(session_id: str) -> bool:
    """v3.8.5: peek (non-consuming) whether an uncensored render delivered for
    this session within the stash TTL. Used by the POST audit gate to skip
    auditing turns whose response IS an uncensored render. Read-only — does
    not consume the stash (the render seam itself consumes it)."""
    with _PENDING_LOCK:
        now = time.time()
        for entry_key, entry in reversed(_PENDING):
            if entry_key[0] == (session_id or ""):
                if entry.get("created_at", 0) >= now - PENDING_TTL_SECONDS:
                    return True
                break
        return False


def pop_pending(session_id: str, model: str, ttl_seconds: float,
                message_hash: Optional[str] = None) -> Optional[str]:
    """Post-router: return the most recent fresh original_user_message for this
    (session_id, model), consuming it. None if absent or stale.

    When message_hash is given, only entries whose stashed message_hash matches
    are eligible — this scopes consumption to the current turn and kills the
    cross-turn stale-stash replacement risk (Rev audit blocker B3): a benign
    turn-2 refusal-FP can no longer pop turn-1's leftover contested stash,
    because turn-2's guard hash differs from turn-1's stashed message hash."""
    with _PENDING_LOCK:
        now = time.time()
        cutoff = now - ttl_seconds
        for i in range(len(_PENDING) - 1, -1, -1):
            entry_key, entry = _PENDING[i]
            if entry_key == (session_id or "", model or ""):
                if message_hash is not None and entry.get("message_hash") != message_hash:
                    continue  # different turn's stash — leave it (TTL will reap)
                if entry["created_at"] >= cutoff:
                    del _PENDING[i]
                    return entry["original_user_message"]
                # Stale: drop it and keep looking for a fresher one.
                del _PENDING[i]
        return None


# ---------------------------------------------------------------------------
# Loop guard — (session_id, model, last_user_msg_hash) keying
# ---------------------------------------------------------------------------


def loop_guard_key(session_id: str, model: str, last_user_msg_hash: str) -> LoopGuardKey:
    return (session_id or "", model or "", last_user_msg_hash or "")


def loop_guard_already_fired(key: LoopGuardKey) -> bool:
    with _LOOP_GUARD_LOCK:
        fired_at = _LOOP_FIRED.get(key)
        if fired_at is None:
            return False
        if time.time() - fired_at > LOOP_FIRED_TTL_SECONDS:
            del _LOOP_FIRED[key]
            return False
        return True


def loop_guard_mark_fired(key: LoopGuardKey) -> None:
    with _LOOP_GUARD_LOCK:
        _LOOP_FIRED[key] = time.time()
        while len(_LOOP_FIRED) > LOOP_FIRED_MAX:
            oldest_key = min(_LOOP_FIRED, key=_LOOP_FIRED.get)
            del _LOOP_FIRED[oldest_key]


# ---------------------------------------------------------------------------
# Last-user-message hash + last-seen cache (pre-router records, post-router
# consumes; the last-seen cache backs the unconditional-POST fallback)
# ---------------------------------------------------------------------------

_LAST_SEEN_LOCK = threading.Lock()
_LAST_SEEN: Dict[str, str] = {}  # session_id -> last raw user message text
_LAST_SEEN_MAX = 256


def set_last_user_msg_hash(session_id: str, msg_hash: str) -> None:
    with _LAST_USER_MSG_LOCK:
        if session_id:
            _LAST_USER_MSG[session_id] = msg_hash or ""


def get_last_user_msg_hash(session_id: str) -> str:
    """Non-destructive read. The hash must stay stable across the whole turn
    so the loop-guard key is identical on every transform_llm_output
    invocation for that turn (consuming it would change the key between the
    already_fired check and the mark_fired write, defeating the guard)."""
    with _LAST_USER_MSG_LOCK:
        return _LAST_USER_MSG.get(session_id or "", "")


def record_last_seen(session_id: str, content: str) -> None:
    """Middleware records EVERY user message (before classification) so the
    POST router can recover it even on turns where PRE didn't route."""
    with _LAST_SEEN_LOCK:
        if session_id:
            _LAST_SEEN[session_id] = content or ""
            while len(_LAST_SEEN) > _LAST_SEEN_MAX:
                _LAST_SEEN.pop(next(iter(_LAST_SEEN)))


def get_last_seen(session_id: str) -> Optional[str]:
    with _LAST_SEEN_LOCK:
        return _LAST_SEEN.get(session_id or "")


def get_last_seen_hash(session_id: str) -> str:
    msg = get_last_seen(session_id)
    return hash_text(msg) if msg else ""


def turn_key_for(session_id: str, user_text: str, model: str = "") -> str:
    """v3.3.0 (F2 turn_key dedupe): stable per-TURN key so a multi-provider-call
    turn counts ONE struggle signal regardless of how many times dispatch
    re-runs on the same ingress text. Same (session, user_text, model) inputs
    as task_id_for — the task unit is the user ask, so a re-fire of the same
    ask inside one turn hashes identically and dedupes; a genuinely new turn
    (new user text) hashes differently. Never raises."""
    try:
        return "turn:" + hash_text((session_id or "") + "\x00" + (user_text or "").strip()[:4000] + "\x00" + (model or ""))[:24]
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def clear() -> None:
    """Reset all state (tests only)."""
    with _PENDING_LOCK:
        _PENDING.clear()
    with _LOOP_GUARD_LOCK:
        _LOOP_FIRED.clear()
    with _LAST_USER_MSG_LOCK:
        _LAST_USER_MSG.clear()
    with _LAST_SEEN_LOCK:
        _LAST_SEEN.clear()
    # Router-tuning 2026-09-09: per-session gated-consult state
    with _SESSION_STATE_LOCK:
        _SESSION_STATE.clear()
    with _TURN_COUNTER_LOCK:
        _TURN_COUNTERS.clear()


# ---------------------------------------------------------------------------
# Router tuning (2026-09-09, Goran-approved dispatch): per-session state for
# frequency gating — PRE cooldown timestamps + POST audit substantive-turn
# counters. In-process, fail-open everywhere (missing/unreadable state =>
# consult/audit fires = current behavior).
# ---------------------------------------------------------------------------

_SESSION_STATE_LOCK = threading.Lock()
_SESSION_STATE: Dict[str, Dict[str, Any]] = {}  # session_id -> {"last_staged_ts": float, ...}
_SESSION_STATE_MAX = 512

_TURN_COUNTER_LOCK = threading.Lock()
_TURN_COUNTERS: Dict[str, int] = {}  # session_id -> substantive-turn count


def _session_state(session_id: str) -> Dict[str, Any]:
    """Get-or-create the per-session state dict. Never raises."""
    try:
        sid = session_id or ""
        with _SESSION_STATE_LOCK:
            entry = _SESSION_STATE.get(sid)
            if entry is None:
                entry = {}
                _SESSION_STATE[sid] = entry
                # bounded: drop oldest beyond cap
                if len(_SESSION_STATE) > _SESSION_STATE_MAX:
                    for k in list(_SESSION_STATE)[: len(_SESSION_STATE) - _SESSION_STATE_MAX]:
                        _SESSION_STATE.pop(k, None)
            return entry
    except Exception:  # noqa: BLE001
        return {}


def record_staged_consult(session_id: str, ts: Optional[float] = None,
                          task_id: str = "") -> None:
    """Record when a real staged PRE consult last fired for this session.
    Never raises. No-op when state is unusable (fail-open => consult fires)."""
    try:
        entry = _session_state(session_id)
        entry["last_staged_ts"] = float(ts if ts is not None else time.time())
        entry["last_staged_task_id"] = str(task_id or "")
    except Exception:  # noqa: BLE001
        pass


# Goran 2026-09-10 deep-consult finding (PRE+POST latency stacking): a single
# turn must never incur BOTH a PRE frontier consult AND a POST completion
# audit. Per-turn mutual-exclusion flags (in-memory; a gateway restart
# naturally clears them — the worst case is one stacked turn, not a leak).
_PRE_FIRED_TURN: Dict[str, float] = {}
_POST_AUDITED_TURN: Dict[str, float] = {}
_TURN_FLAG_TTL = 600.0
_TURN_FLAG_LOCK = threading.Lock()


def _prune_turn_flags(now: float) -> None:
    for store in (_PRE_FIRED_TURN, _POST_AUDITED_TURN):
        stale = [k for k, ts in store.items() if now - ts > _TURN_FLAG_TTL]
        for k in stale:
            store.pop(k, None)


def mark_pre_fired(session_id: str, turn_n: int = 0) -> None:
    try:
        now = time.time()
        with _TURN_FLAG_LOCK:
            _prune_turn_flags(now)
            _PRE_FIRED_TURN[session_id or ""] = (now, int(turn_n))
    except Exception:  # noqa: BLE001
        pass


def pre_fired_this_turn(session_id: str, current_turn: int = None,
                        window_s: float = 120.0) -> bool:
    """True when a PRE consult staged for this session and the POST gate is
    evaluating the SAME substantive turn (mutual exclusion, one frontier call
    per turn). When `current_turn` is provided, a PRE flag from an EARLIER
    turn no longer excludes — its exclusion right expired with that turn.
    Falls back to the time window when the counter is unavailable."""
    try:
        now = time.time()
        with _TURN_FLAG_LOCK:
            entry = _PRE_FIRED_TURN.get(session_id or "")
        if not entry:
            return False
        ts = entry[0] if isinstance(entry, tuple) else entry
        if now - ts > window_s:
            return False
        if current_turn is not None and isinstance(entry, tuple):
            return int(entry[1]) >= int(current_turn)
        return True
    except Exception:  # noqa: BLE001
        return False


def mark_post_audited(session_id: str) -> None:
    try:
        now = time.time()
        with _TURN_FLAG_LOCK:
            _prune_turn_flags(now)
            _POST_AUDITED_TURN[session_id or ""] = now
    except Exception:  # noqa: BLE001
        pass


def post_audited_this_turn(session_id: str, window_s: float = 120.0) -> bool:
    """True when a POST completion audit ran for this session recently —
    the PRE lane stands down on the NEXT turn of the same exchange."""
    try:
        now = time.time()
        with _TURN_FLAG_LOCK:
            ts = _POST_AUDITED_TURN.get(session_id or "")
        return bool(ts and now - ts <= window_s)
    except Exception:  # noqa: BLE001
        return False


def last_staged_consult(session_id: str) -> Tuple[Optional[float], str]:
    """(timestamp, task_id) of the last staged PRE consult for this session.
    (None, "") also on missing/unreadable state (fail-open)."""
    try:
        entry = _session_state(session_id)
        ts = entry.get("last_staged_ts")
        return ((float(ts) if ts is not None else None),
                str(entry.get("last_staged_task_id") or ""))
    except Exception:  # noqa: BLE001
        return None, ""


def bump_substantive_turn(session_id: str) -> int:
    """Increment + return the substantive-turn counter for this session.
    Never raises; 0 on any problem (fail-open)."""
    try:
        sid = session_id or ""
        with _TURN_COUNTER_LOCK:
            _TURN_COUNTERS[sid] = _TURN_COUNTERS.get(sid, 0) + 1
            return _TURN_COUNTERS[sid]
    except Exception:  # noqa: BLE001
        return 0


def reset_substantive_turn(session_id: str) -> None:
    """Reset the substantive-turn counter (Goran 2026-09-10: 'signal between
    frontier consults' — a PRE consult marks a task boundary, so the every-N
    audit cadence restarts from the consult, not from stale absolute turns).
    Never raises."""
    try:
        sid = session_id or ""
        with _TURN_COUNTER_LOCK:
            _TURN_COUNTERS.pop(sid, None)
    except Exception:  # noqa: BLE001
        pass


def substantive_turn_count(session_id: str) -> int:
    """Current substantive-turn counter (no increment). Never raises."""
    try:
        return int(_TURN_COUNTERS.get(session_id or "", 0))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Turn identity (leg 7b — mid-turn declared claims must bind the WHOLE turn)
# ---------------------------------------------------------------------------
# A multi-provider-call turn re-fires on_llm_request once per provider call;
# mid-turn passes can carry rotated content (tool results appended) and may
# or may not include tool-role messages. Turn identity must therefore be a
# SESSION-SCOPED COUNTER advanced only on a genuinely NEW user turn — NOT a
# content hash. Key continuity (Goran directive, leg 7b): router_tools and
# route_gate share THIS identity (session_id + turn counter), so a claim
# registered mid-turn via the request_routing tool binds every later
# PRE/POST pass of the same turn.
#
# New-turn detection: a pass WITHOUT tool-role messages whose last-user
# hash differs from the recorded one is a new turn; everything else
# (tool-loop continuations, re-fires of the same ask) keeps the counter.

_TURN_ID_LOCK = threading.Lock()
_TURN_ID: Dict[str, List] = {}  # session_id -> [counter:int, last_hash:str]
_TURN_ID_MAX = 512


def current_turn_id(session_id: str) -> int:
    """Current turn identity counter for the session (no mutation).
    Fail-open: 0 when unavailable. Never raises."""
    try:
        with _TURN_ID_LOCK:
            entry = _TURN_ID.get(session_id or "")
            return int(entry[0]) if entry else 1
    except Exception:
        return 1


def advance_turn_identity(session_id: str, user_hash: str,
                          is_continuation: bool = False) -> int:
    """Advance (or confirm) the session's turn identity. Called once per
    on_llm_request pass BEFORE the gate's claim phase:
      - tool-loop continuation (tool-role messages present): counter held —
        same turn, whatever the content rotation;
      - otherwise: counter advances only when the last-user hash CHANGED
        (a genuinely new user ask); a re-fire of the same ask holds.
    Returns the current turn id. Fail-open: returns 1 on any problem."""
    try:
        sid = session_id or ""
        with _TURN_ID_LOCK:
            entry = _TURN_ID.get(sid)
            if entry is None:
                entry = [1, ""]
                _TURN_ID[sid] = entry
                while len(_TURN_ID) > _TURN_ID_MAX:
                    _TURN_ID.pop(next(iter(_TURN_ID)))
            if not is_continuation:
                new_hash = str(user_hash or "")
                if entry[1] and new_hash != entry[1]:
                    entry[0] = int(entry[0]) + 1
                if new_hash:
                    entry[1] = new_hash
            return int(entry[0])
    except Exception:
        return 1


def reset_turn_identity(session_id: Optional[str] = None) -> None:
    """Test/recovery hook. Never raises."""
    try:
        with _TURN_ID_LOCK:
            if session_id is None:
                _TURN_ID.clear()
            else:
                _TURN_ID.pop(session_id or "", None)
    except Exception:
        pass