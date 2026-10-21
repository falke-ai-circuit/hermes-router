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
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

EVENT = "advisory_consumed_turn"
_TTL_S = 30 * 60.0
_MAX_SESSIONS = 64

# session_id -> {"injected": ts, "tools": int, "dispatch": bool, "ask": str,
#               "chars": int, "envelope_chars": int, "seq0": int}
_STATE: Dict[str, Dict[str, Any]] = {}

# Injection order (oldest first) — lets the tool seam, which the host invokes
# WITHOUT a session_id (transform_terminal_output kwargs are command/output/
# returncode/task_id/env_type), attribute a tool run to the turn that is
# actually executing: the newest advisory-bearing turn not yet closed.
_ORDER: List[str] = []

# Monotonic count of tool activities seen by this process. close_turn compares
# it against the value captured at injection: if ANY tool ran since the
# advisory entered the turn, the accounting cannot prove zero-work and the
# event stays silent (a false positive is worse than a missing row).
_TOOL_SEQ = 0

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
        for k in list(_ORDER):
            if k not in _STATE:
                try:
                    _ORDER.remove(k)
                except ValueError:
                    pass
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
                           chars: int = 0, envelope_chars: int = 0) -> None:
    """Record that a PRE advisory entered this turn's model context.

    `chars` is the ADVISORY BODY (the capped frontier answer); `envelope_chars`
    is the whole injected envelope (marker + frame + body + provenance stamps).
    They are different numbers by design and must never be conflated in the
    event — R34-13 defect 2: `advisory_chars=1951` was read as "the advisory
    was not capped to 600" because the field carried the envelope.

    Re-entry in the same turn (a later LLM call) preserves the tool counter and
    the injection-time tool sequence. Never raises.
    """
    try:
        sid = _key(session_id)
        if not sid:
            return
        _purge()
        st = _STATE.get(sid)
        if isinstance(st, dict):
            st["injected"] = time.time()
            if int(chars or 0) > 0:
                st["chars"] = int(chars or 0)
            if int(envelope_chars or 0) > 0:
                st["envelope_chars"] = int(envelope_chars or 0)
            return
        _STATE[sid] = {
            "injected": time.time(),
            "tools": 0,
            "dispatch": looks_like_dispatch(user_text),
            "ask": str(user_text or "")[:200],
            "chars": int(chars or 0),
            "envelope_chars": int(envelope_chars or 0),
            "seq0": _TOOL_SEQ,
        }
        _ORDER.append(sid)
    except Exception:  # noqa: BLE001
        pass


def _attribute_tool(sid: str) -> bool:
    """Bump session `sid`'s counter when it holds a live advisory turn."""
    st = _STATE.get(sid)
    if isinstance(st, dict):
        st["tools"] = int(st.get("tools") or 0) + 1
        return True
    return False


def note_tool_activity(session_id: str) -> None:
    """Record one tool activity in this turn (terminal/tool-result seam).

    Root cause of the R34-13 false positive: the host fires
    transform_terminal_output WITHOUT any session id (kwargs are command,
    output, returncode, task_id, env_type), so the old counter — keyed only on
    the session — never incremented, and a 14-tool turn reported
    `no_tools=true`. A tool run belongs to the turn that is executing right
    now: resolve an exact session key when the seam supplies one, otherwise
    attribute to the newest advisory-bearing turn still open. No-op when no
    advisory turn is open. Never raises.
    """
    global _TOOL_SEQ
    try:
        _TOOL_SEQ += 1
        sid = _key(session_id)
        if sid and _attribute_tool(sid):
            return
        for cand in reversed(list(_ORDER)):
            if _attribute_tool(cand):
                return
    except Exception:  # noqa: BLE001
        pass


def close_turn(session_id: str, log_route: Optional[Any] = None) -> bool:
    """Turn-close check: emit `advisory_consumed_turn no_tools=true` when an
    advisory was injected into a dispatch turn that produced ZERO tool
    activity. Returns True when the event fired. Clears the turn state.
    Never raises.

    False-positive guards (R34-13 defect 1): the event only fires when the
    turn is provably work-free —
      * the turn's own counter is zero, AND
      * no tool ran anywhere in this process since the advisory was injected
        (the seam that reports tools carries no session id on some host
        versions, so a non-zero global sequence means the accounting cannot
        prove zero work — stay silent rather than accuse a working turn).
    """
    global _TOOL_SEQ  # noqa: F824 — read-only use
    try:
        sid = _key(session_id)
        if not sid:
            return False
        st = _STATE.pop(sid, None)
        try:
            _ORDER.remove(sid)
        except ValueError:
            pass
        if not isinstance(st, dict):
            return False
        if int(st.get("tools") or 0) > 0:
            return False
        if _TOOL_SEQ > int(st.get("seq0") or 0):
            # A tool run happened inside this turn window but was not
            # attributed to it. Never emit on unprovable accounting.
            return False
        if not bool(st.get("dispatch")):
            return False
        _adv = int(st.get("chars") or 0)
        _env = int(st.get("envelope_chars") or 0) or _adv
        try:
            if log_route is None:
                from .core import telemetry as _tlm

                _tlm.log_route("POST", event_detail=EVENT, no_tools="true",
                               advisory_chars=_adv, envelope_chars=_env,
                               session_id=sid)
            else:
                log_route("POST", event_detail=EVENT, no_tools="true",
                          advisory_chars=_adv, envelope_chars=_env,
                          session_id=sid)
        except Exception:  # noqa: BLE001 — observability only
            pass
        return True
    except Exception:  # noqa: BLE001
        return False
