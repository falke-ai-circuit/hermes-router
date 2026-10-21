"""work_signal — R34-17 TURN WORK PREDICATE.

Why this module exists (live false positive, session 20261021_070153_f41a2f):
the execution-echo guard keyed on BODY SHAPE alone. A legit execution turn
wrote `dedupe.py` + `test_dedupe.py` and ran pytest (5 passed), the body
carried an echoed advisory tail (`"povs": [...]`), and the guard threw the
whole body away and told the user "nothing was executed". The work was on
disk; the notice was false. A guard must never lie about the filesystem.

The predicate: a turn is a WORK turn when any of
  * a terminal tool ran            (transform_terminal_output seam), or
  * a file-WRITE tool was called  (write_file / patch / edit / create…),
in that turn. Read-only tool traffic (read_file, search, git status) is
tracked for the log but does NOT prove work — a turn that only looked is
still a no-work turn, and the handback path must stay reachable for it.

Turn identity is the `advisory_consumption` turn counter (one user ask = one
turn), so counters reset naturally on the next user message. Bounded state,
TTL + FIFO cap; a gateway restart simply resets it. Everything is fail-open
and never raises.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

_TTL_S = 30 * 60.0
_MAX_SESSIONS = 64

# session_id -> {"turn": n, "terminal": int, "writes": int, "tools": int,
#               "kinds": [str], "ts": float}
_STATE: Dict[str, Dict[str, Any]] = {}
# Newest-activity order — the terminal seam can fire WITHOUT a session id
# (host kwargs: command/output/returncode/task_id/env_type), so an unkeyed
# tool run is attributed to the turn that is executing right now.
_ORDER: List[str] = []

# File-writing / editing tool names. A tool call with one of these names
# changed the substrate; it is work whether or not a terminal followed.
WRITE_TOOLS = frozenset({
    "write_file", "write", "create_file", "append_file", "edit_file",
    "edit", "patch", "apply_patch", "apply_diff", "multi_edit",
    "str_replace", "str_replace_editor", "replace_in_file", "insert_content",
    "notebook_edit", "todo_write",
})

# Terminal-shaped tool names as they appear in a request payload's tool_calls
# (the terminal SEAM is the authoritative signal; this is the payload echo).
TERMINAL_TOOLS = frozenset({
    "terminal", "bash", "shell", "run_command", "execute_command",
    "exec", "powershell", "cmd",
})


def _key(session_id: Any) -> str:
    """Converge `session:api_...` / `api_...` on the bare session id. Never
    raises."""
    try:
        s = str(session_id or "").strip()
        for pre in ("session:", "sessions:"):
            if s.startswith(pre):
                s = s[len(pre):]
        return s if len(s) <= 128 else ""
    except Exception:  # noqa: BLE001
        return ""


def _turn_of(sid: str) -> int:
    """The current turn number for the session (0 when unknown)."""
    try:
        from . import advisory_consumption as _ac

        return int(_ac.turn_no(sid) or 0)
    except Exception:  # noqa: BLE001
        return 0


def _purge(now: Optional[float] = None) -> None:
    try:
        now = time.time() if now is None else now
        dead = [k for k, v in _STATE.items()
                if not isinstance(v, dict)
                or (now - float(v.get("ts") or 0.0)) > _TTL_S]
        for k in dead:
            _STATE.pop(k, None)
        while len(_STATE) > _MAX_SESSIONS:
            k = next(iter(_STATE))
            _STATE.pop(k, None)
            try:
                _ORDER.remove(k)
            except ValueError:
                pass
        for k in list(_ORDER):
            if k not in _STATE:
                try:
                    _ORDER.remove(k)
                except ValueError:
                    pass
    except Exception:  # noqa: BLE001
        pass


def _entry(sid: str) -> Dict[str, Any]:
    """The counter bag for this session's CURRENT turn (reset on turn change)."""
    _purge()
    turn = _turn_of(sid)
    st = _STATE.get(sid)
    if not isinstance(st, dict) or int(st.get("turn") or -1) != turn:
        st = {"turn": turn, "terminal": 0, "writes": 0, "tools": 0,
              "kinds": [], "ts": time.time()}
        _STATE[sid] = st
        if sid not in _ORDER:
            _ORDER.append(sid)
    st["ts"] = time.time()
    return st


def _attribute(fallback_to_newest: bool = True) -> Optional[Dict[str, Any]]:
    """Resolve the turn bag for an UNKEYED seam (newest live session)."""
    try:
        for sid in reversed(list(_ORDER)):
            if sid in _STATE:
                return _entry(sid)
        return None
    except Exception:  # noqa: BLE001
        return None


def _bump(sid: str, kind: str, n: int = 1,
          tool_names: Optional[List[str]] = None) -> None:
    try:
        target = sid or ""
        st = _entry(target) if target else _attribute()
        if st is None:
            return
        st["tools"] = int(st.get("tools") or 0) + n
        if kind == "terminal":
            st["terminal"] = int(st.get("terminal") or 0) + n
        elif kind == "write":
            st["writes"] = int(st.get("writes") or 0) + n
        kinds = st.setdefault("kinds", [])
        for k in [kind] + list(tool_names or []):
            if k and k not in kinds:
                kinds.append(k)
    except Exception:  # noqa: BLE001
        pass


def note_terminal_activity(session_id: Any = "", command: str = "") -> None:
    """A terminal tool result was transformed by the plugin — a terminal RAN
    in this turn. Called from the transform_terminal_output seam. Never
    raises."""
    try:
        sid = _key(session_id)
        if not sid:
            # Unkeyed seam (host supplies no session id): attribute to the
            # newest live turn so a working turn never reads as work-free.
            _bump("", "terminal")
            return
        _bump(sid, "terminal")
    except Exception:  # noqa: BLE001
        pass


def note_request_activity(session_id: Any = "", request: Any = None) -> None:
    """Scan an llm_request payload for work evidence: tool_call names on the
    assistant messages (write tools) and tool-role results. Called once per
    provider call from the llm_request seam. Never raises."""
    try:
        sid = _key(session_id)
        if not sid:
            return
        _entry(sid)  # register the live turn even when the payload is empty
        if not isinstance(request, dict):
            return
        msgs = request.get("messages")
        if not isinstance(msgs, list):
            return
        names: List[str] = []
        for m in msgs[-40:]:
            if not isinstance(m, dict):
                continue
            calls = m.get("tool_calls")
            if not isinstance(calls, list):
                continue
            for c in calls:
                nm = ""
                if isinstance(c, dict):
                    fn = c.get("function")
                    nm = str((fn or {}).get("name") if isinstance(fn, dict)
                             else c.get("name") or "")
                if not nm:
                    continue
                nm = nm.strip().lower()
                names.append(nm)
        if not names:
            return
        writes = [n for n in names if n in WRITE_TOOLS]
        terms = [n for n in names if n in TERMINAL_TOOLS]
        if writes:
            _bump(sid, "write", len(writes), writes)
        if terms:
            _bump(sid, "terminal", len(terms), terms)
        _bump(sid, "tool", len(names), [n for n in names
                                        if n not in WRITE_TOOLS
                                        and n not in TERMINAL_TOOLS])
    except Exception:  # noqa: BLE001
        pass


def work_facts(session_id: Any = "") -> Dict[str, Any]:
    """The turn's work verdict.

    Returns {"has_work": bool, "terminal": n, "writes": n, "tools": n,
             "kinds": "terminal|write", "predicate": "..."}.

    `has_work` is True ONLY for a terminal run or a write/edit tool call in
    THIS turn. Read-only tool traffic is reported but does not qualify — the
    predicate may not over-claim work any more than the notice may
    under-claim it.
    """
    try:
        sid = _key(session_id)
        if not sid:
            return {"has_work": False, "terminal": 0, "writes": 0, "tools": 0,
                    "kinds": "", "predicate": "no_session_key"}
        st = _STATE.get(sid)
        turn = _turn_of(sid)
        if not isinstance(st, dict) or int(st.get("turn") or -1) != turn:
            # Nothing recorded for the turn that is closing.
            return {"has_work": False, "terminal": 0, "writes": 0, "tools": 0,
                    "kinds": "", "predicate": "no_tool_activity"}
        terms = int(st.get("terminal") or 0)
        writes = int(st.get("writes") or 0)
        kinds = [k for k in ("terminal", "write")
                 if (k == "terminal" and terms) or (k == "write" and writes)]
        return {
            "has_work": bool(terms or writes),
            "terminal": terms,
            "writes": writes,
            "tools": int(st.get("tools") or 0),
            "kinds": "|".join(kinds),
            "predicate": ("terminal_run+write_tool" if terms and writes
                          else "terminal_run" if terms
                          else "write_tool" if writes
                          else "no_tool_activity"),
        }
    except Exception:  # noqa: BLE001
        return {"has_work": False, "terminal": 0, "writes": 0, "tools": 0,
                "kinds": "", "predicate": "error"}


def reset(session_id: Any = "") -> None:
    """Test helper: drop the ledger for a session (or all)."""
    try:
        sid = _key(session_id)
        if sid:
            _STATE.pop(sid, None)
            try:
                _ORDER.remove(sid)
            except ValueError:
                pass
            return
        _STATE.clear()
        _ORDER.clear()
    except Exception:  # noqa: BLE001
        pass
