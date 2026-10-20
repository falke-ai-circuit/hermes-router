"""R34-10 — session turn-lease release + stale preemption.

Live blackout (coder, 2026-10-20 ~20:32): a turn hung. `session_turn_leases`
kept `expires_at` advancing (+300s on every renewal heartbeat) for ~30 min
while NO message was written, so three separate dispatches sat unprocessed
and the session looked idle to the operator. The lease is a serialization
primitive; it must never outlive the work it protects.

This module owns the RELEASE side of that table (schema already present in
every profile `state.db`):

    session_turn_leases(conversation_id TEXT PRIMARY KEY, holder TEXT NOT NULL,
                        acquired_at REAL NOT NULL, expires_at REAL NOT NULL)

Two guarantees, both fail-open (a lease bug must never block an agent):

  10a  release(session_id) on turn end — success, exception, abort, or an
       empty final response — removes the row. Callers wire it in a finally
       (or the turn-end seam); the renewal heartbeat cannot keep a row alive
       once it is gone.

  10b  preempt_if_stale(session_id) on turn start — when a lease is held but
       no message has been persisted for 2x the renewal interval, log
       `turn_lease_stale holder=<holder> idle_s=<idle>` and delete the row so
       the new turn proceeds. Fail-open: on any doubt the row is left alone.

Every entry point never raises. DB access is bounded (1s lock timeout) and
uses the profile-scoped state.db path that already backs the session store.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Renewal interval the core heartbeat uses (+300s per renewal in the live
# specimen). Staleness threshold = 2x this.
RENEW_INTERVAL_S = 300.0
STALE_FACTOR = 2.0
EVENT_RELEASED = "turn_lease_released"
EVENT_STALE = "turn_lease_stale"


def _db_path() -> str:
    try:
        from .core.session_store import _state_db_path

        return _state_db_path()
    except Exception:  # noqa: BLE001
        return ""


def _connect(path: str, read_only: bool):
    if read_only:
        return sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=1.0)
    return sqlite3.connect(path, timeout=1.0)


def read_lease(session_id: str, db_path: Optional[str] = None
               ) -> Optional[Dict[str, Any]]:
    """The current lease row for `session_id`, or None. Never raises."""
    sid = str(session_id or "").strip()
    path = db_path or _db_path()
    if not sid or not path:
        return None
    conn = None
    try:
        conn = _connect(path, read_only=True)
        row = conn.execute(
            "SELECT conversation_id, holder, acquired_at, expires_at "
            "FROM session_turn_leases WHERE conversation_id = ?",
            (sid,)).fetchone()
        if not row:
            return None
        return {"conversation_id": row[0], "holder": row[1],
                "acquired_at": float(row[2] or 0.0),
                "expires_at": float(row[3] or 0.0)}
    except Exception:  # noqa: BLE001 — read is observational only
        return None
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:  # noqa: BLE001
            pass


def last_message_ts(session_id: str, db_path: Optional[str] = None
                    ) -> float:
    """Timestamp of the newest persisted message for the session, 0.0 when
    unknown (then the lease's acquired_at is the idle clock). Never raises."""
    sid = str(session_id or "").strip()
    path = db_path or _db_path()
    if not sid or not path:
        return 0.0
    conn = None
    try:
        conn = _connect(path, read_only=True)
        row = conn.execute(
            "SELECT MAX(timestamp) FROM messages WHERE session_id = ?",
            (sid,)).fetchone()
        return float(row[0] or 0.0) if row else 0.0
    except Exception:  # noqa: BLE001
        return 0.0
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:  # noqa: BLE001
            pass


def lease_idle_s(lease: Dict[str, Any], last_ts: float,
                 now: Optional[float] = None) -> float:
    """Seconds since the lease's session last wrote a message.

    idles from the message clock when known; otherwise from acquisition (a
    lease acquired long ago with no messages is the hung-turn shape). Never
    raises; 0.0 when nothing is known.
    """
    try:
        now = float(now if now is not None else time.time())
        anchor = float(last_ts or 0.0) or float(
            (lease or {}).get("acquired_at") or 0.0)
        return max(0.0, now - anchor) if anchor else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def is_stale(lease: Optional[Dict[str, Any]], last_ts: float,
             now: Optional[float] = None,
             renew_interval_s: float = RENEW_INTERVAL_S) -> bool:
    """10b: held lease with no message for 2x the renewal interval."""
    if not lease:
        return False
    try:
        threshold = max(1.0, float(renew_interval_s)) * STALE_FACTOR
        return lease_idle_s(lease, last_ts, now=now) >= threshold
    except Exception:  # noqa: BLE001
        return False


def release(session_id: str, db_path: Optional[str] = None,
            log: Optional[Callable[..., Any]] = None,
            reason: str = "turn_end", outcome: str = "success") -> bool:
    """10a: remove the session's lease row. True when a row was deleted.

    Idempotent; safe to call on success, exception, abort or an empty final
    response. Fail-open: any DB problem returns False and never raises.
    """
    sid = str(session_id or "").strip()
    path = db_path or _db_path()
    if not sid or not path:
        return False
    conn = None
    deleted = False
    try:
        conn = _connect(path, read_only=False)
        cur = conn.execute(
            "DELETE FROM session_turn_leases WHERE conversation_id = ?",
            (sid,))
        deleted = bool(cur.rowcount and cur.rowcount > 0)
        conn.commit()
    except Exception:  # noqa: BLE001 — release must never break the turn
        return False
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:  # noqa: BLE001
            pass
    if deleted:
        _emit(log, EVENT_RELEASED, session_id=sid, reason=str(reason),
              outcome=str(outcome))
    return deleted


def preempt_if_stale(session_id: str, db_path: Optional[str] = None,
                     log: Optional[Callable[..., Any]] = None,
                     renew_interval_s: float = RENEW_INTERVAL_S,
                     now: Optional[float] = None) -> bool:
    """10b: log + delete a held lease whose session has been idle too long.

    Called at turn start. Returns True when a stale lease was preempted.
    Fail-open at every step — a lease check must never block a new turn.
    """
    try:
        sid = str(session_id or "").strip()
        if not sid:
            return False
        path = db_path or _db_path()
        lease = read_lease(sid, db_path=path)
        if not lease:
            return False
        last_ts = last_message_ts(sid, db_path=path)
        now = float(now if now is not None else time.time())
        idle_s = lease_idle_s(lease, last_ts, now=now)
        if not is_stale(lease, last_ts, now=now,
                        renew_interval_s=renew_interval_s):
            return False
        _emit(log, EVENT_STALE, session_id=sid,
              holder=str(lease.get("holder") or "?"),
              idle_s=int(round(idle_s)))
        return release(sid, db_path=path, log=log, reason="stale_preempt",
                       outcome="preempted")
    except Exception:  # noqa: BLE001 — never blocks the agent
        return False


def _emit(log: Optional[Callable[..., Any]], event: str, **fields: Any) -> None:
    """Best-effort route/telemetry event. Never raises."""
    if log is None:
        try:
            logger.warning("%s %s", event,
                           " ".join("%s=%s" % (k, v) for k, v in fields.items()))
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        log(event, **fields)
    except Exception:  # noqa: BLE001
        pass
