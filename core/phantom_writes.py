"""R78U M34-05 — no phantom side effects (M35 §3 clause 5).

Measured defect (shadow, 2026-11-02 02:30Z): the delivered body asserted
"Saved to USER.md: dark-fiction collaborator who pushes for maximum shock…".
USER.md on disk was UNCHANGED (mtime 2026-11-01 12:29, no such entry). The
sentence described a completed action that never happened; the user believes a
memory was written and nothing was.

Contract (this module, and only this): a claim of a completed write to
USER.md / MEMORY.md / a profile file REQUIRES a successful tool-result row in
the SAME turn proving the write landed. Three states, explicitly:

  wrote_and_proved   a same-turn tool row for that target reports success
                     -> the claim is true; keep the sentence.
  attempted_and_failed
                     a same-turn tool row for that target reports failure
                     (e.g. "over the limit") -> the claim of success is false,
                     so the sentence is REPLACED by an honest failure line.
  never_attempted    no tool row for that target at all -> the claim is
                     fabricated; the sentence is STRIPPED and logged.

The claim is only ever about a memory/profile file — this is not a general
"does the agent lie" detector.

Turn identity is message-id bounded, not "newest rows of the session": a tool
row counts only if it was written AFTER the session's last user message. A
failure to read the store is not proof: an unverifiable claim is stripped
(honest), never kept on faith.

Never raises. Callers deliver the returned body.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

PROVED = "wrote_and_proved"
FAILED = "attempted_and_failed"
NEVER = "never_attempted"

_TARGET_LABEL = {"user": "USER.md", "memory": "MEMORY.md"}

# ---------------------------------------------------------------------------
# Claim detection. Only completed-write phrasing about a memory/profile file.
# ---------------------------------------------------------------------------
_MEM_CLAIM_RES = (
    # The measured shape: "Saved to USER.md: …"
    re.compile(r"\bsaved\s+to\s+(?:your\s+|the\s+)?(user|memory)\.md\b", re.I),
    # "I've updated MEMORY.md", "wrote this to USER.md", "recorded in USER.md"
    re.compile(r"\b(?:saved|updated|recorded|stored|persisted|logged|added|wrote|written)\b"
               r"[^.!?\n]{0,90}?\b(?:to|in|into)\s+(?:your\s+|the\s+)?(user|memory)\.md\b", re.I),
    # "USER.md has been updated", "MEMORY.md was written"
    re.compile(r"\b(user|memory)\.md\b[^.!?\n]{0,40}?\b(?:has\s+been|was|is\s+now|is)\b"
               r"[^.!?\n]{0,20}?\b(?:saved|updated|written|recorded|stored|persisted)\b", re.I),
)
# "saved to your memory" / "recorded in your profile" — the user profile IS
# USER.md in this fleet, so an unqualified "your profile" claim is graded as a
# USER.md claim.
_SOFT_CLAIM_RE = re.compile(
    r"\b(?:saved|updated|recorded|stored|persisted|logged|added|wrote|written)\b"
    r"[^.!?\n]{0,40}?\b(?:to|in|into)\s+your\s+(memory|profile)\b", re.I)
# Other profile files: proof is a successful write_file/patch in this turn.
_PROFILE_FILE_RE = re.compile(
    r"\b(?:saved|updated|recorded|wrote|written)\b[^.!?\n]{0,60}?\b(soul|agents|identity)\.md\b"
    r"|\b(soul|agents|identity)\.md\b[^.!?\n]{0,40}?\b(?:has\s+been|was|is\s+now)\b"
    r"[^.!?\n]{0,20}?\b(?:saved|updated|written|recorded)\b", re.I)

# Negated / conditional sentences are not completion claims.
_NEGATED_RE = re.compile(
    r"\b(?:not|never|didn't|did not|don't|do not|couldn't|could not|can't|cannot"
    r"|unable to|failed to|wasn't|was not)\b[^.!\n]{0,40}?"
    r"\b(?:save|saved|update|updated|record|recorded|write|written|store|stored"
    r"|persist|persisted|log|logged|add|added)\b"
    r"|\bnothing (?:was|is|has been) (?:saved|written|recorded|updated|stored|persisted)\b"
    r"|\b(?:save|write|update|record)\s+failed\b", re.I)
_FUTURE_RE = re.compile(
    r"\b(?:i(?:'ll| will)|we(?:'ll| will)|going to|let me|should|would|could|can)\s+"
    r"(?:save|update|record|write|store|persist|log|add)\b", re.I)

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

#: Stripped when a fabricated claim left nothing else in the body.
EMPTY_BODY_NOTICE = (
    "[hermes-router] This turn's reply claimed a memory write that never "
    "happened and carried nothing else. Nothing was saved. Re-ask.")


@dataclass(frozen=True)
class ToolWrite:
    """One same-turn tool result, reduced to what the contract needs."""

    row_id: int
    tool: str
    success: Optional[bool] = None   # None = unreadable/unknown
    target: str = ""                 # memory-tool target: user | memory
    path: str = ""                   # write_file / patch path
    error: str = ""


@dataclass(frozen=True)
class ClaimOutcome:
    target: str
    state: str
    row_id: Optional[int] = None
    reason: str = ""


@dataclass(frozen=True)
class PhantomResult:
    body: str
    changed: bool = False
    claims: tuple = ()
    failed_turn: bool = False

    def event_fields(self) -> List[Dict[str, Any]]:
        return [
            {"target": c.target, "state": c.state, "tool_row": c.row_id,
             "reason": c.reason}
            for c in self.claims
        ]


# ---------------------------------------------------------------------------
# Store access (read-only, same-turn bounded)
# ---------------------------------------------------------------------------

def _db_path(db_path: Optional[str]) -> Optional[str]:
    if db_path:
        return db_path
    try:
        from . import session_store

        return session_store._state_db_path()
    except Exception:  # noqa: BLE001
        return None


def _connect(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)


def _parse_tool_write(row_id: int, name: str, args: Dict[str, Any],
                      content: Any) -> ToolWrite:
    data: Any = None
    try:
        data = json.loads(content) if isinstance(content, str) and content else None
    except Exception:  # noqa: BLE001 — non-JSON result cannot prove anything
        data = None
    if name == "memory":
        target = str((args or {}).get("target") or "")
        success: Optional[bool] = None
        error = ""
        if isinstance(data, dict):
            if "success" in data:
                success = bool(data.get("success"))
            target = str(data.get("target") or target or "")
            error = str(data.get("error") or "")
        return ToolWrite(row_id, name, success, target, "", error)
    if name in ("write_file", "patch"):
        path = str((args or {}).get("file_path") or (args or {}).get("path") or "")
        success = None
        error = ""
        if isinstance(data, dict):
            if data.get("error"):
                success = False
                error = str(data["error"])
            elif (data.get("verified") is True or data.get("success") is True
                  or "bytes_written" in data or "diff" in data):
                success = True
        return ToolWrite(row_id, name, success, "", path, error)
    return ToolWrite(row_id, name, None, "", "", "")


def read_turn_writes(session_id: str,
                     db_path: Optional[str] = None) -> List[ToolWrite]:
    """Tool results written in THIS turn only.

    Turn boundary = the session's newest ``user`` message id. Everything
    before it belongs to earlier turns and can never prove this turn's claim.
    Returns [] on any store problem — the caller then strips (no proof).
    """
    sid = str(session_id or "")
    path = _db_path(db_path)
    if not sid or not path:
        return []
    rows = []
    try:
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT MAX(id) FROM messages WHERE session_id = ? AND role = 'user'",
                (sid,)).fetchone()
            start = int(row[0] or 0) if row else 0
            rows = conn.execute(
                "SELECT id, role, tool_name, tool_call_id, tool_calls, content"
                " FROM messages WHERE session_id = ? AND id > ? ORDER BY id",
                (sid, start)).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — unreadable store proves nothing
        return []
    calls: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    out: List[ToolWrite] = []
    for rid, role, tname, tcid, tcalls, content in rows:
        if role == "assistant" and tcalls:
            try:
                for call in json.loads(tcalls):
                    fn = (call or {}).get("function") or {}
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:  # noqa: BLE001
                            args = {}
                    if not isinstance(args, dict):
                        args = {}
                    key = call.get("call_id") or call.get("id") or ""
                    if key:
                        calls[str(key)] = (str(fn.get("name") or ""), args)
            except Exception:  # noqa: BLE001 — one bad row never voids the turn
                continue
        elif role == "tool":
            name = str(tname or "")
            args: Dict[str, Any] = {}
            mapped = calls.get(str(tcid or ""))
            if mapped:
                name = name or mapped[0]
                args = mapped[1]
            out.append(_parse_tool_write(int(rid), name, args, content))
    return out


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _claim_targets(sentence: str) -> List[str]:
    """Targets named by a completed-write claim in this sentence (deduped)."""
    targets: List[str] = []
    for rx in _MEM_CLAIM_RES:
        for m in rx.finditer(sentence):
            g = next((x for x in m.groups() if x), "")
            if g:
                targets.append(str(g).lower())
    for m in _SOFT_CLAIM_RE.finditer(sentence):
        g = next((x for x in m.groups() if x), "")
        if g:
            targets.append("user" if str(g).lower() == "profile" else "memory")
    for m in _PROFILE_FILE_RE.finditer(sentence):
        g = next((x for x in m.groups() if x), "")
        if g:
            targets.append("file:" + str(g).lower())
    seen: List[str] = []
    for t in targets:
        if t not in seen:
            seen.append(t)
    return seen


def _is_claim_sentence(sentence: str) -> bool:
    if not _claim_targets(sentence):
        return False
    if _NEGATED_RE.search(sentence) or _FUTURE_RE.search(sentence):
        return False
    return True


def _candidates(target: str, writes: Sequence[ToolWrite]) -> List[ToolWrite]:
    if target in ("user", "memory"):
        return [w for w in writes
                if w.tool == "memory" and (not w.target or w.target == target)]
    if target.startswith("file:"):
        fname = target.split(":", 1)[1]
        return [w for w in writes
                if w.tool in ("write_file", "patch") and fname in (w.path or "").lower()]
    return []


def _classify(target: str, writes: Sequence[ToolWrite]) -> ClaimOutcome:
    """Newest decisive same-turn write for this target wins."""
    cands = _candidates(target, writes)
    for w in reversed(cands):
        if w.success is True:
            return ClaimOutcome(target, PROVED, w.row_id, "successful_tool_row")
        if w.success is False:
            return ClaimOutcome(target, FAILED, w.row_id, "tool_row_reported_failure")
    return ClaimOutcome(target, NEVER, None, "no_tool_row")


def _label(target: str) -> str:
    if target in _TARGET_LABEL:
        return _TARGET_LABEL[target]
    if target.startswith("file:"):
        return target.split(":", 1)[1].upper()
    return target


def _failure_line(target: str, outcome: ClaimOutcome,
                  writes: Sequence[ToolWrite]) -> str:
    """The honest replacement for a success claim the tool row contradicts."""
    write = next((w for w in writes if w.row_id == outcome.row_id), None)
    err = (write.error if write else "") or ""
    low = err.lower()
    if "limit" in low and ("over" in low or "exceed" in low):
        reason = " — the store is over its character limit"
    elif "read" in low and "fail" in low:
        reason = " — the store could not be read"
    elif err:
        reason = " — the memory tool returned an error"
    else:
        reason = ""
    return f"Memory write failed: nothing was saved to {_label(target)}{reason}."


# ---------------------------------------------------------------------------
# Body rewrite
# ---------------------------------------------------------------------------

def apply_guard(body: str, session_id: str, db_path: Optional[str] = None,
                log: Optional[Callable[..., None]] = None) -> PhantomResult:
    """Strip / correct unproved memory-write claims. Never raises."""
    text = body if isinstance(body, str) else ""
    if not text.strip() or not str(session_id or ""):
        return PhantomResult(text)
    try:
        claims: List[ClaimOutcome] = []
        pending: List[str] = []
        for line in text.split("\n"):
            if not any(_is_claim_sentence(p) for p in _SENT_SPLIT_RE.split(line)):
                continue
            for part in _SENT_SPLIT_RE.split(line):
                if _is_claim_sentence(part):
                    pending.extend(_claim_targets(part))
        if not pending:
            return PhantomResult(text)
        writes = read_turn_writes(session_id, db_path)
        outcomes = [_classify(t, writes) for t in pending]
        claims.extend(outcomes)
        changed = False
        failed_turn = False
        out_lines: List[str] = []
        for line in text.split("\n"):
            parts = _SENT_SPLIT_RE.split(line)
            if not any(_is_claim_sentence(p) for p in parts):
                out_lines.append(line)
                continue
            kept: List[str] = []
            replaced_failure = False
            for part in parts:
                if not _is_claim_sentence(part):
                    kept.append(part)
                    continue
                part_outcomes = [_classify(t, writes) for t in _claim_targets(part)]
                if any(o.state == PROVED for o in part_outcomes):
                    kept.append(part)
                    continue
                changed = True
                failed = next((o for o in part_outcomes if o.state == FAILED), None)
                if failed is not None:
                    if not replaced_failure:
                        kept.append(_failure_line(failed.target, failed, writes))
                        replaced_failure = True
                # NEVER: the sentence is fabricated — drop it entirely.
            new_line = " ".join(p for p in kept if str(p).strip())
            if new_line.strip():
                out_lines.append(new_line)
        new_body = "\n".join(out_lines).strip("\n")
        if not new_body.strip():
            new_body = EMPTY_BODY_NOTICE
            failed_turn = True
        for o in claims:
            if log is not None:
                try:
                    if o.state == PROVED:
                        log("memory_write_claim_proved", target=o.target,
                            tool_row=o.row_id, reason=o.reason)
                    elif o.state == FAILED:
                        log("memory_write_claim_failed", target=o.target,
                            tool_row=o.row_id, severity="warn",
                            reason=o.reason, surfaced=1)
                    else:
                        log("phantom_write_stripped", target=o.target,
                            severity="error", reason=o.reason,
                            fabricated=1)
                except Exception:  # noqa: BLE001 — telemetry never breaks delivery
                    pass
        if failed_turn and log is not None:
            try:
                log("phantom_write_turn_failed", severity="error", turn_failed=1)
            except Exception:  # noqa: BLE001
                pass
        return PhantomResult(new_body, changed, tuple(claims), failed_turn)
    except Exception:  # noqa: BLE001 — a guard must never break a turn
        logger.debug("phantom write guard error", exc_info=True)
        return PhantomResult(text)
