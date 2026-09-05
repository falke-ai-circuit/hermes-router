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
from typing import Deque, Dict, Optional, Tuple

PendingKey = Tuple[str, str]
LoopGuardKey = Tuple[str, str, str]

_PENDING_LOCK = threading.Lock()
_PENDING: Deque[Tuple[PendingKey, Dict]] = deque()
PENDING_MAX = 32

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