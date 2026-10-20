"""advisory_consumption — R34-12 item 9 WORK INVARIANT observability.

Live defect (session api_1792536611_5dbebc6b, 2026-10-20): the PRE anchor
advisory was injected into a dispatch turn, the turn then closed with ZERO
tool calls, and the delivered body was a restatement of the advisory
envelope. Nothing in the log said "the advisory ate the turn".

This module makes that observable:

    advisory_consumed_turn no_tools=true session_id=...

The invariant: if an advisory was injected this turn AND the user message is
a dispatch/task instruction AND the turn closed without a single tool
activity, the turn produced no work. Log-only, fail-open: it never blocks,
rewrites or delays delivery, and it never raises.

Bounded in-process state (one entry per live turn, TTL + FIFO cap); a
gateway restart resets it.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

EVENT = "advisory_consumed_turn"
_TTL_S = 30 * 60.0
_MAX_SESSIONS = 64

# session_id -> {"injected": ts, "tools": int, "dispatch": bool, "ask": str}
_STATE: Dict[str, Dict[str, Any]] = {}

# Dispatch/task-instruction vocabulary: a directive verb anywhere in the ask
# (or an explicit ITEMS/TASKS work list) marks a task instruction. This is a
# permissive LOG-ONLY classifier — a false positive costs one log row.
_DISPATCH_RE = re.compile(
    r"\b(implement|refactor|fix|patch|add|write|create|build|run|commit|"
    r"deploy|update|replace|merge|migrate|rewrite|remove|delete|install|"
    r"verify|inspect|read|open|review|audit|port|extend|wire|test|ship)\b",
    re.I,
)
_WORKLIST_RE = re.compile(r"(?im)^\s*(?:\d+[.)]|ITEMS?:|TASKS?:)\s*")


def _key(session_id: Any) -> str:
    """Converge the platform's seam keys on the bare session id: the
    terminal seam hands `session:api_...` while the POST/PRE edges use
    `api_...` (same normalization as decision_midturn). Never raises."""
    try:
        s = str(session_id or "").strip()
        for pre in ("session:", "sessions:"):
            if s.startswith(pre):
                s = s[len(pre):]
        return s if len(s) <= 128 else ""
    except Exception:  # noqa: BLE001
        return ""


def _purge(now: Optional[float] = None) -> None:
    """Drop stale entries and enforce the FIFO cap. Never raises."""
    try:
        now = time.time() if now is None else now
        dead = [k for k, v in _STATE.items()
                if not isinstance(v, dict)
                or (now - float(v.get("injected") or 0.0)) > _TTL_S]
        for k in dead:
            _STATE.pop(k, None)
        while len(_STATE) > _MAX_SESSIONS:
            _STATE.pop(next(iter(_STATE)), None)
    except Exception:  # noqa: BLE001
        pass


def looks_like_dispatch(text: Any) -> bool:
    """True when the text is a dispatch/task instruction (directive verb or
    an explicit numbered work list). Never raises."""
    try:
        t = str(text or "").strip()
        if not t:
            return False
        if _DISPATCH_RE.search(t[:4000]):
            return True
        return len(_WORKLIST_RE.findall(t)) >= 3
    except Exception:  # noqa: BLE001
        return False


def note_advisory_injected(session_id: str, user_text: str = "",
                           chars: int = 0) -> None:
    """Record that a PRE advisory entered this turn's model context. Re-entry
    in the same turn (a later LLM call) preserves the tool counter. Never
    raises."""
    try:
        sid = _key(session_id)
        if not sid:
            return
        _purge()
        st = _STATE.get(sid)
        if isinstance(st, dict):
            st["injected"] = time.time()
            st["chars"] = int(chars or 0)
            return
        _STATE[sid] = {
            "injected": time.time(),
            "tools": 0,
            "dispatch": looks_like_dispatch(user_text),
            "ask": str(user_text or "")[:200],
            "chars": int(chars or 0),
        }
    except Exception:  # noqa: BLE001
        pass


def note_tool_activity(session_id: str) -> None:
    """Record one tool activity in this turn (terminal/tool-result seam).
    No-op when no advisory was injected. Never raises."""
    try:
        st = _STATE.get(_key(session_id))
        if isinstance(st, dict):
            st["tools"] = int(st.get("tools") or 0) + 1
    except Exception:  # noqa: BLE001
        pass


def close_turn(session_id: str, log_route: Optional[Any] = None) -> bool:
    """Turn-close check: emit `advisory_consumed_turn no_tools=true` when an
    advisory was injected into a dispatch turn that produced ZERO tool
    activity. Returns True when the event fired. Clears the turn state.
    Never raises."""
    try:
        sid = _key(session_id)
        if not sid:
            return False
        st = _STATE.pop(sid, None)
        if not isinstance(st, dict):
            return False
        if int(st.get("tools") or 0) > 0:
            return False
        if not bool(st.get("dispatch")):
            return False
        try:
            if log_route is None:
                from .core import telemetry as _tlm

                _tlm.log_route("POST", event_detail=EVENT, no_tools="true",
                               advisory_chars=int(st.get("chars") or 0),
                               session_id=sid)
            else:
                log_route("POST", event_detail=EVENT, no_tools="true",
                          advisory_chars=int(st.get("chars") or 0),
                          session_id=sid)
        except Exception:  # noqa: BLE001 — observability only
            pass
        return True
    except Exception:  # noqa: BLE001
        return False
