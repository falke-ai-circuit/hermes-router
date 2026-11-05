"""R78U M34-04 — transcript must equal delivery (M35 §3 clause 4).

Backstop, not a forecast. The measured defect (shadow, 2026-11-02 02:30Z):

  delivery_obligations  721 chars  the denial the user actually received
  messages row 53850   1066 chars  a render of a background-review body

Both lived in the same state.db. Any audit that read only ``messages``
concluded "routed and answered"; any audit that read only the obligation
concluded "delivered". The two disagreeing IS the defect, and nothing in the
live path compared them.

Contract: after ANY substitution, ``strip_banner(messages.content)`` equals
``strip_banner(delivery_obligations.content)`` for that session_key. A
mismatch is a HARD FAILURE: an error event plus a failed turn, never a
telemetry note.

Write ordering (gateway/platforms/base.py:6049-6108) is why this is a
backstop: the obligation row is recorded BEFORE the platform send and AFTER
the POST transform hook has already returned. So the check cannot run
"immediately after substitution" — it runs on the session's next POST pass,
auditing the last turn that actually reached the delivery ledger.

TURN IDENTITY (not "the newest row of the session"):
  1. session_key  <- sessions.session_key, else gateway_routing.entry_json
                     (the gateway's own session_key -> session_id mapping),
                     else the session_id itself.
  2. obligation   <- explicit obligation_id, else the newest DELIVERED row for
                     that session_key (created_at DESC).
  3. row          <- explicit turn_row_id (the row a substitution wrote), else
                     the newest assistant row whose timestamp <= the
                     obligation's created_at — i.e. the row that existed when
                     that turn was delivered. A later turn (or a bg-review
                     append) is NEWER than the obligation and can never be
                     mistaken for it.
An ephemeral body has no delivery obligation and no row of its own: it is
refused before any comparison (``ephemeral=True``), so a background-review
body can never be graded against a delivered row.

Read-only (``mode=ro``), never raises.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

BANNER_HEAD = "\u00b7 router \u00b7"
_BANNER_LINE_RE = re.compile(r"^[ \t]*\u00b7[ \t]*router[ \t]*\u00b7")
#: Continuation lines the banner renderer appends below the L1 head
#: (debug_banner.format_banner L2/L3: "task ... | sess ... | gate ...", "full: ...").
_BANNER_CONT_PREFIXES = ("task ", "full: lane=")

#: obligation ids already audited, per session. The audit runs once per
#: delivered turn: a divergence fails ONE turn, it does not fail every turn
#: that follows while the same bad row sits in the store.
_AUDITED: Dict[str, Set[str]] = {}

#: The transcript text recovered for a diverged turn, per session. M45-19: the
#: seam hands this text back as the delivered body, so the delivery decision
#: must recognise it as the audit's substitution (and not as a routine render
#: it may overwrite). Set by `mark_recovered`, read once by `take_recovered`.
_RECOVERED: Dict[str, str] = {}


@dataclass(frozen=True)
class Divergence:
    """Outcome of one transcript-vs-delivery comparison."""

    checked: bool
    ok: bool
    reason: str
    session_key: str = ""
    obligation_id: str = ""
    row_id: Optional[int] = None
    delivered_chars: int = 0
    transcript_chars: int = 0
    #: The transcript row's text after decoration is stripped. M45-19: this is
    #: what gets DELIVERED when a divergence is found — the audit reports, it
    #: does not gag. Empty only when the row held no text at all.
    transcript_body: str = ""

    def as_event_fields(self) -> Dict[str, Any]:
        return {
            "reason": self.reason,
            "session_key": self.session_key,
            "obligation_id": self.obligation_id,
            "row_id": self.row_id,
            "delivered_chars": self.delivered_chars,
            "transcript_chars": self.transcript_chars,
        }


# ---------------------------------------------------------------------------
# Banner stripping — the banner is added at DELIVERY, not present in the row's
# canonical text. Both sides are stripped before comparison so a banner can
# never masquerade as divergence.
# ---------------------------------------------------------------------------

def strip_banner(text: Any) -> str:
    """Remove a router banner block from a body (leading or trailing).

    The block is: an L1 line starting with ``· router ·`` plus any contiguous
    L2/L3 continuation lines. A banner in the MIDDLE of a body is left alone —
    it is not a delivery banner then, it is content. Never raises.
    """
    try:
        t = text if isinstance(text, str) else ""
        if not t:
            return ""
        lines = t.split("\n")
        idxs = [i for i, line in enumerate(lines) if _BANNER_LINE_RE.match(line)]
        if not idxs:
            return t

        def _is_cont(line: str) -> bool:
            s = line.strip()
            return s == "" or any(s.startswith(p) for p in _BANNER_CONT_PREFIXES)

        # Trailing block: the banner head plus everything after it is banner.
        last = idxs[-1]
        if all(_is_cont(x) or _BANNER_LINE_RE.match(x) for x in lines[last + 1:]):
            start = last
            while start > 0 and lines[start - 1].strip() == "":
                start -= 1
            return "\n".join(lines[:start]).rstrip()

        # Leading block: banner first, body after.
        if idxs[0] == 0:
            k = 0
            while k < len(lines) and (_is_cont(lines[k])
                                      or _BANNER_LINE_RE.match(lines[k])):
                k += 1
            return "\n".join(lines[k:]).lstrip("\n")

        return t
    except Exception:  # noqa: BLE001 — comparison input shaping never raises
        return str(text or "")


def strip_decoration(text: Any) -> str:
    """Banner AND provenance-footer removed — what the reader actually sees.

    M45-19: the comparison must ignore decoration on BOTH sides. A banner is
    added at delivery; a footer rides every rendered delivery. Neither is
    transcript divergence, and a decoration-only difference must never become
    the baseline that fails the next turn.
    """
    body = strip_banner(text)
    try:
        from ..provenance_footer import strip_footer

        return strip_banner(strip_footer(body))
    except Exception:  # noqa: BLE001 — footer shaping is best-effort
        return body


def is_failure_body(text: Any) -> bool:
    """True when this body is the seam's own failure notice, not an answer.

    M45-19: such a body is telemetry, never a baseline. Comparing the notice
    against the next turn's transcript guarantees a second divergence and a
    second withheld answer — the cascade this predicate exists to break.
    """
    try:
        return strip_decoration(text).lstrip().startswith(FAILURE_PREFIX)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Store access
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


def session_key_for(session_id: str, db_path: Optional[str] = None) -> str:
    """Resolve the delivery-ledger session_key for a transcript session_id.

    Order: ``sessions.session_key`` (populated on newer stores), then the
    gateway's own mapping in ``gateway_routing.entry_json``, then the
    session_id itself (API-server sessions use the same key on both sides).
    Returns "" when the session is unknown. Never raises.
    """
    sid = str(session_id or "")
    if not sid:
        return ""
    path = _db_path(db_path)
    if not path:
        return ""
    try:
        conn = _connect(path)
        try:
            try:
                row = conn.execute(
                    "SELECT session_key FROM sessions WHERE id = ?",
                    (sid,)).fetchone()
            except sqlite3.Error:
                row = None
            if row and row[0]:
                return str(row[0])
            try:
                rows = conn.execute(
                    "SELECT session_key, entry_json FROM gateway_routing"
                    " ORDER BY updated_at DESC LIMIT 200").fetchall()
            except sqlite3.Error:
                rows = []
            for key, blob in rows:
                if not key:
                    continue
                try:
                    entry = json.loads(blob or "{}")
                except Exception:  # noqa: BLE001 — unparseable entry, keep looking
                    continue
                if str(entry.get("session_id") or "") == sid:
                    return str(key)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — resolution is best-effort
        return ""
    return ""


def _latest_obligation(conn, session_key: str,
                       obligation_id: Optional[str] = None):
    if obligation_id:
        return conn.execute(
            "SELECT obligation_id, content, created_at FROM delivery_obligations"
            " WHERE obligation_id = ?", (str(obligation_id),)).fetchone()
    return conn.execute(
        "SELECT obligation_id, content, created_at FROM delivery_obligations"
        " WHERE session_key = ? AND state = 'delivered'"
        " ORDER BY created_at DESC LIMIT 1", (str(session_key),)).fetchone()


def _turn_row(conn, session_id: str, obligation_created_at: Any,
              turn_row_id: Optional[int] = None):
    """The assistant row THIS turn produced — never merely the newest row.

    An explicit turn_row_id (the row a substitution wrote) is exact. Otherwise
    the row is the newest assistant row that already existed when the turn was
    delivered: ``timestamp <= obligation.created_at``. Rows written later —
    a following user turn, a background-review append — are excluded by
    construction.
    """
    if turn_row_id is not None:
        return conn.execute(
            "SELECT id, content FROM messages WHERE id = ? AND session_id = ?"
            " AND role = 'assistant'",
            (int(turn_row_id), str(session_id))).fetchone()
    try:
        ts = float(obligation_created_at)
    except (TypeError, ValueError):
        return None
    return conn.execute(
        "SELECT id, content FROM messages"
        " WHERE session_id = ? AND role = 'assistant'"
        "   AND timestamp IS NOT NULL AND timestamp <= ?"
        " ORDER BY timestamp DESC, id DESC LIMIT 1",
        (str(session_id), ts)).fetchone()


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def check_transcript_delivery(session_id: str, *, db_path: Optional[str] = None,
                              turn_row_id: Optional[int] = None,
                              obligation_id: Optional[str] = None,
                              ephemeral: bool = False) -> Divergence:
    """Compare the turn's transcript row to the body that was delivered.

    ``ephemeral=True`` is the structural exclusion: a body with no delivery
    obligation (background review, compression, tool pass) is REFUSED before
    any store read, so it can never be compared against a delivered row.

    ``checked=False`` means "the pair does not exist" — not a pass and not a
    failure. Never raises.
    """
    if ephemeral:
        return Divergence(checked=False, ok=True,
                          reason="ephemeral_body_not_delivered")
    sid = str(session_id or "")
    if not sid:
        return Divergence(checked=False, ok=True, reason="no_session_id")
    path = _db_path(db_path)
    if not path:
        return Divergence(checked=False, ok=True, reason="no_state_db")
    try:
        conn = _connect(path)
        try:
            key = session_key_for(sid, path)
            if not key:
                return Divergence(checked=False, ok=True,
                                  reason="no_session_key")
            ob = _latest_obligation(conn, key, obligation_id)
            if ob is None:
                return Divergence(checked=False, ok=True, reason="no_delivery_obligation",
                                  session_key=key)
            oid, ocontent, ocreated = ob
            row = _turn_row(conn, sid, ocreated, turn_row_id)
            if row is None:
                return Divergence(checked=False, ok=True, reason="no_row_for_turn",
                                  session_key=key, obligation_id=str(oid))
            rid, rcontent = row
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — the backstop never breaks a turn
        logger.debug("delivery integrity read failed: %s", exc)
        return Divergence(checked=False, ok=True, reason="read_error")

    delivered = strip_decoration(ocontent)
    transcript = strip_decoration(rcontent)
    if is_failure_body(ocontent):
        # M45-19: the failure notice is NOT a baseline. There is nothing to
        # compare against, so this is "not checked" — not a second divergence.
        return Divergence(
            checked=False, ok=True, reason="failure_body_not_a_baseline",
            session_key=key, obligation_id=str(oid), row_id=int(rid),
            delivered_chars=len(delivered), transcript_chars=len(transcript),
            transcript_body=transcript,
        )
    ok = delivered == transcript
    return Divergence(
        checked=True, ok=ok,
        reason="delivery_transcript_match" if ok else "transcript_divergence",
        session_key=key, obligation_id=str(oid), row_id=int(rid),
        delivered_chars=len(delivered), transcript_chars=len(transcript),
        transcript_body=transcript,
    )


def audit_last_delivery(session_id: str, *, db_path: Optional[str] = None,
                        log=None) -> Divergence:
    """Backstop for the session's last delivered turn, once per obligation.

    Emits ``delivery_transcript_match`` on a pass and ``transcript_divergence``
    (severity=error, turn_failed=1) on a mismatch. Returns the result; the
    caller decides what a failed turn does with its body.
    """
    sid = str(session_id or "")
    if not sid:
        return Divergence(checked=False, ok=True, reason="no_session_id")
    result = check_transcript_delivery(sid, db_path=db_path)
    if not result.checked:
        return result
    audited = _AUDITED.setdefault(sid, set())
    if result.obligation_id in audited:
        return Divergence(checked=False, ok=True, reason="already_audited",
                          session_key=result.session_key,
                          obligation_id=result.obligation_id)
    audited.add(result.obligation_id)
    try:
        if log is not None:
            if result.ok:
                log("POST", event_detail="delivery_transcript_match",
                    **result.as_event_fields())
            else:
                log("POST", event_detail="transcript_divergence",
                    severity="error", turn_failed=1,
                    **result.as_event_fields())
        if not result.ok:
            logger.error(
                "transcript_divergence session=%s obligation=%s row=%s "
                "delivered=%d transcript=%d",
                sid, result.obligation_id, result.row_id,
                result.delivered_chars, result.transcript_chars)
    except Exception:  # noqa: BLE001 — telemetry never breaks the backstop
        pass
    return result


"""First sentence of `failure_body` — the seam-failure recogniser.

`gate/registration.py` reads this prefix to mark a turn the SEAM itself
declared failed, so the delivery decision can never deliver a normal
answer over it. One spelling, one place.
"""
FAILURE_PREFIX = "Router failure: transcript divergence"


def mark_recovered(session_id: str, text: Any) -> None:
    """Remember the transcript text handed back for a diverged turn (M45-19).

    The delivery decision must see this body as the audit's substitution, not
    as a routine render it may replace with the model's own text — otherwise
    the recovered answer is silently dropped one layer down. One-shot; never
    raises.
    """
    try:
        sid = str(session_id or "")
        body = text if isinstance(text, str) else ""
        if sid and body.strip():
            _RECOVERED[sid] = body
            if len(_RECOVERED) > 512:
                _RECOVERED.clear()
    except Exception:  # noqa: BLE001
        pass


def take_recovered(session_id: str) -> str:
    """Read AND clear the recovered body for a session. Never raises."""
    try:
        return _RECOVERED.pop(str(session_id or ""), "") or ""
    except Exception:  # noqa: BLE001
        return ""


def failure_body(result: Divergence) -> str:
    """Operator-facing hard-failure text for a diverged turn.

    It states the two lengths and the two ids — never the bodies themselves —
    so the failure is auditable without re-leaking the content.
    """
    return (
        FAILURE_PREFIX + ". The transcript row for this "
        "session does not match the body the user was delivered "
        "({} chars persisted vs {} chars delivered; row {} / obligation {}). "
        "The turn is withheld rather than delivered as if it were answered."
    ).format(result.transcript_chars, result.delivered_chars,
             result.row_id, result.obligation_id)


def reset_for_tests() -> None:
    """Clear the audited-obligation memory (tests only). Never raises."""
    _AUDITED.clear()
    _RECOVERED.clear()
