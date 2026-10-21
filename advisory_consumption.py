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


EVENT_AFTER_CLOSE = "advisory_after_turn_close"
EVENT_ORPHANED = "advisory_orphaned_post_close"

# R34-14 item 1: turn-close ledger. A consult that resolves AFTER its turn
# closed must be DISCARDED, not injected into a dead turn. `_CLOSED[sid]` is
# the close timestamp of the newest closed turn; `_OPEN_SIG[sid]` is the
# identity of the user message that turn belongs to. A retry/continuation
# pass that carries the SAME user text is the SAME turn — it does not reopen
# the session, so a late advisory stays orphaned instead of being blessed by
# the duplicate sweep (live: session api_1792543426_8e2f94ef, two sweeps on
# one posted message, advisory landing 13s after close).
_CLOSED: Dict[str, float] = {}
_OPEN_SIG: Dict[str, str] = {}
_TURN_NO: Dict[str, int] = {}
_SWEEPS: Dict[str, int] = {}
_ORPHANS: Dict[str, int] = {}

# R34-18 residual 2 — DELIVERY TRUTH. A banner may only be dropped as
# "turn closed undelivered" on evidence that nothing was delivered. The turn
# counter alone is bookkeeping: the live specimen (session
# 20261021_074100_e9a8eb) had a banner parked PRE 07:41:17 and a body of 2208
# chars delivered POST 07:41:48 in the SAME turn, yet the semaphore compared
# park_turn < current_turn and dropped it as undelivered, so a billed
# $0.0136 consult carried no mark. Whatever proves a body landed is the same
# evidence `sweep_started response_chars` reports — recorded here at that
# sweep, plus the delivering edge's own text length as the primary signal.
_BODY_LANDED: Dict[str, Dict[str, Any]] = {}
_BODY_LANDED_MAX = 256


def _sig(user_text: Any) -> str:
    """Stable identity of a turn's ask (content hash of the head). Never raises."""
    try:
        import hashlib

        return hashlib.sha1(
            str(user_text or "")[:2000].strip().encode("utf-8", "replace")
        ).hexdigest()
    except Exception:  # noqa: BLE001
        return ""


def note_turn_open(session_id: str, user_text: Any = "") -> int:
    """Mark a turn OPEN (a new user message began). Returns the turn number.

    Same ask text as the newest closed turn = the SAME turn (a retry or a
    duplicate transform pass), so the close marker STAYS: a consult that
    resolves now is still post-close and must be discarded. Never raises.
    """
    try:
        sid = _key(session_id)
        if not sid:
            return 0
        sig = _sig(user_text)
        if sig and sig == _OPEN_SIG.get(sid) and sid in _CLOSED:
            return int(_TURN_NO.get(sid) or 0)
        _OPEN_SIG[sid] = sig
        _CLOSED.pop(sid, None)
        _ORPHANS.pop(sid, None)
        _SWEEPS.pop(sid, None)
        _TURN_NO[sid] = int(_TURN_NO.get(sid) or 0) + 1
        return int(_TURN_NO[sid])
    except Exception:  # noqa: BLE001
        return 0


def note_turn_closed(session_id: str) -> float:
    """Mark the current turn CLOSED. Returns the close timestamp. Never raises."""
    try:
        sid = _key(session_id)
        if not sid:
            return 0.0
        ts = time.time()
        _CLOSED[sid] = ts
        return ts
    except Exception:  # noqa: BLE001
        return 0.0


def turn_no(session_id: str) -> int:
    """Current turn number for the session (0 when unknown). Never raises."""
    try:
        return int(_TURN_NO.get(_key(session_id)) or 0)
    except Exception:  # noqa: BLE001
        return 0


def open_signal(session_id: str) -> str:
    """Identity (content hash) of the ask the current turn belongs to.

    Two passes over the SAME user message keep this value, so it is the
    discriminator a delivery decision needs: a parked banner whose ask still
    matches is the same turn no matter what the turn counter did. Never
    raises; "" when unknown.
    """
    try:
        return str(_OPEN_SIG.get(_key(session_id)) or "")
    except Exception:  # noqa: BLE001
        return ""


def note_body_landed(session_id: str, chars: int = 0) -> Dict[str, Any]:
    """Record that a body of `chars` characters ACTUALLY landed for this turn.

    Called from the POST detection sweep with the same `response_chars` that
    `sweep_started` reports, and read by the banner semaphore before it may
    declare a turn undelivered (R34-18 residual 2). A zero-length body is not
    a landing and never overwrites a real one. Never raises; {} when ignored.
    """
    try:
        sid = _key(session_id)
        n = int(chars or 0)
        if not sid or n <= 0:
            return {}
        while len(_BODY_LANDED) >= _BODY_LANDED_MAX:
            _BODY_LANDED.pop(next(iter(_BODY_LANDED)), None)
        rec = {
            "turn_no": int(_TURN_NO.get(sid) or 0),
            "chars": n,
            "ts": time.time(),
            "ask_sig": str(_OPEN_SIG.get(sid) or ""),
        }
        _BODY_LANDED[sid] = rec
        return dict(rec)
    except Exception:  # noqa: BLE001
        return {}


def body_landed(session_id: str) -> Dict[str, Any]:
    """The last recorded body landing for this session ({} when none)."""
    try:
        rec = _BODY_LANDED.get(_key(session_id))
        return dict(rec) if isinstance(rec, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def delivery_truth(session_id: str, chars_hint: int = 0) -> Dict[str, Any]:
    """Evidence a banner-drop decision must rest on (R34-18 residual 2).

    `body_chars` is the larger of the body this delivery edge is handing over
    (`chars_hint`) and the last landing the sweep observed; `ask_sig` is the
    identity of the ask the session's current turn belongs to. A caller may
    NOT infer "undelivered" from the turn counter while this says a body
    landed. Never raises.
    """
    try:
        rec = body_landed(session_id)
        return {
            "body_chars": max(int(chars_hint or 0), int(rec.get("chars") or 0)),
            "body_turn_no": int(rec.get("turn_no") or 0),
            "body_sig": str(rec.get("ask_sig") or ""),
            "ask_sig": open_signal(session_id),
        }
    except Exception:  # noqa: BLE001
        return {"body_chars": int(chars_hint or 0), "body_turn_no": 0,
                "body_sig": "", "ask_sig": ""}


def note_sweep(session_id: str) -> "tuple[int, bool]":
    """R34-14 item 2: account a POST detection sweep. Returns (turn_no, dup).

    `dup=True` means this session already ran a sweep in THIS turn number —
    i.e. one user message produced two transform passes. Never raises.
    """
    try:
        sid = _key(session_id)
        n = turn_no(sid)
        key = f"{sid}#{n}"
        prev = int(_SWEEPS.get(key) or 0)
        _SWEEPS[key] = prev + 1
        return n, prev > 0
    except Exception:  # noqa: BLE001
        return 0, False


def clear_turn_ledger(session_id: str = "") -> None:
    """Test helper: drop the close/open ledger for a session (or all)."""
    try:
        sid = _key(session_id)
        targets = [sid] if sid else list(set(list(_CLOSED) + list(_OPEN_SIG)))
        for k in targets:
            _CLOSED.pop(k, None)
            _OPEN_SIG.pop(k, None)
            _TURN_NO.pop(k, None)
            _ORPHANS.pop(k, None)
        for k in list(_SWEEPS):
            if not sid or k.startswith(sid + "#"):
                _SWEEPS.pop(k, None)
        for k in list(_BODY_LANDED):
            if not sid or k == sid:
                _BODY_LANDED.pop(k, None)
    except Exception:  # noqa: BLE001
        pass


def _orc(edge: str, event: str, log_route: Optional[Any] = None,
         **fields: Any) -> None:
    """Best-effort route-log writer for the turn ledger. Never raises."""
    try:
        if log_route is None:
            from .core import telemetry as _tlm

            _tlm.log_route(edge, event_detail=event, **fields)
        else:
            log_route(edge, event_detail=event, **fields)
    except Exception:  # noqa: BLE001 — telemetry never breaks a turn
        pass


def note_advisory_injected(session_id: str, user_text: str = "",
                           chars: int = 0, envelope_chars: int = 0) -> bool:
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
            return False
        # R34-14 item 1: a consult that resolves AFTER its turn closed must be
        # DISCARDED — never injected, never billed-and-ignored. The caller
        # uses this return value to skip the injection entirely.
        _closed_at = _CLOSED.get(sid)
        if _closed_at is not None:
            # A DIFFERENT ask than the closed turn's is a genuinely new turn
            # (the PRE seam normally calls note_turn_open; callers that do not
            # own that seam still get the right answer here). The same ask —
            # or an unknown one — stays post-close and is orphaned.
            _sig_now = _sig(user_text)
            _sig_turn = _OPEN_SIG.get(sid)
            if _sig_now and _sig_turn and _sig_now != _sig_turn:
                note_turn_open(sid, user_text)
            else:
                _delta = max(0.0, time.time() - float(_closed_at))
                _ORPHANS[sid] = int(_ORPHANS.get(sid) or 0) + 1
                _orc("POST", EVENT_AFTER_CLOSE, session_id=sid,
                     delta_s=round(_delta, 3), advisory_chars=int(chars or 0))
                _orc("POST", EVENT_ORPHANED, session_id=sid,
                     delta_s=round(_delta, 3), advisory_chars=int(chars or 0),
                     action="discarded")
                return False
        _purge()
        st = _STATE.get(sid)
        if isinstance(st, dict):
            st["injected"] = time.time()
            if int(chars or 0) > 0:
                st["chars"] = int(chars or 0)
            if int(envelope_chars or 0) > 0:
                st["envelope_chars"] = int(envelope_chars or 0)
            return True
        _OPEN_SIG.setdefault(sid, _sig(user_text))
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
        return True
    except Exception:  # noqa: BLE001
        return False


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
        # R34-14 item 1/4: closing the turn is what makes a later consult
        # orphaned. Record the close timestamp BEFORE any return path.
        note_turn_closed(sid)
        st = _STATE.pop(sid, None)
        try:
            _ORDER.remove(sid)
        except ValueError:
            pass
        if not isinstance(st, dict):
            # R34-14 item 4: an advisory that resolved post-close was
            # DISCARDED (never injected), so there is no state to accuse.
            # Name the real condition instead of staying silent — this event
            # is the one the old `no_tools=true` was falsely reporting.
            if int(_ORPHANS.get(sid) or 0) > 0:
                _n = int(_ORPHANS.pop(sid, 0))
                _orc("POST", EVENT_ORPHANED, log_route, session_id=sid,
                     action="orphaned_at_close", count=_n)
                return True
            return False
        # R34-14 item 4: an orphaned post-close consult WINS the explanation.
        # When our own consult arrived too late for this turn, "no tools" is
        # not the agent's fault and not the truth of the turn — the advisory
        # that consumed it never landed inside it. Report the orphan, retire
        # the accusation.
        if int(_ORPHANS.get(sid) or 0) > 0:
            _n = int(_ORPHANS.pop(sid, 0))
            _orc("POST", EVENT_ORPHANED, log_route, session_id=sid,
                 action="orphaned_at_close", count=_n)
            return True
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
