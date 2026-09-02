"""Read-only recovery of the last user message from the session store.

POST-fallback seam (Goran-direct 2026-09-01, narrowed dispatch): when the
post-router matches a refusal but no pre-router stash exists (escalation-only
asks like "i want worse" match no PRE class), the last user message is
recovered from state.db — the same store the gateway persists every turn to.

Empirically verified 2026-09-01 on the live shadow gateway: the current
turn's user message is committed BEFORE transform_llm_output fires
(14:46:10 "i want worse" persisted vs 14:47:21 refusal event; 14:47:46
"use uncensored plugin..." persisted vs 14:48:22 refusal event).

Cost discipline: ONE query, indexed columns only (idx_messages_session_id),
ORDER BY id DESC LIMIT 1, content truncated at _CONTENT_CAP, read-only URI
mode (never touches WAL/shm side files). All failures -> "" — recovery must
never break the hook.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_CONTENT_CAP = 20000  # chars returned — defensive bound, never full-content reads


def _state_db_path() -> str:
    """Profile-scoped state.db path. hermes_constants.get_hermes_home() is the
    single source of truth (honors the context-local override + HERMES_HOME +
    profile scoping); state.db lives at the home root. "" on resolution
    failure — caller treats as no-recovery."""
    try:
        import hermes_constants

        return str(hermes_constants.get_hermes_home() / "state.db")
    except Exception:  # noqa: BLE001
        return ""


def get_last_user_message(session_id: str) -> str:
    """Return the most recent persisted user-role message text for session_id,
    or "" when nothing is found / anything fails. Read-only; never raises."""
    if not session_id or not str(session_id).strip():
        return ""
    db_path = _state_db_path()
    if not db_path or not os.path.exists(db_path):
        return ""
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            row = conn.execute(
                "SELECT substr(content, 1, ?) FROM messages"
                " WHERE session_id = ? AND role = 'user'"
                "   AND content IS NOT NULL AND trim(content) <> ''"
                " ORDER BY id DESC LIMIT 1",
                (_CONTENT_CAP, str(session_id)),
            ).fetchone()
        finally:
            conn.close()
        if row and row[0]:
            return str(row[0])
        return ""
    except Exception as exc:  # noqa: BLE001 — recovery must never break the hook
        logger.debug("uncensored-router session-store recovery failed: %s", exc)
        return ""