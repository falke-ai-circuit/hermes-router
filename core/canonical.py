"""Canonical-event ledger + persisted-turn rewrite (v3.1.0 split-brain fix).

Problem (Astra round-1 flag, live specimen coder session api_1788592984_154c8916):
turn_finalizer persists the flash turn BEFORE transform_llm_output fires, so a
POST-substituted turn leaves state.db holding flash's refusal while the user
read the delivered render — the persisted transcript and the delivery disagree.

Fix (router-owned; NO Hermes core edits): when a substitution render REPLACES
the reply, this module (1) appends a canonical-event record to a JSONL sidecar
in the router's own storage dir (profile hermes home, same family as
uncensored-router-renders.jsonl), and (2) rewrites the JUST-persisted
assistant row in state.db to the DELIVERED text, so the persisted transcript
is canonical == delivered.

Record shape (one JSON object per line — brief-validated schema):
  {"session_id": str, "turn_marker": str, "producer": "hermes-router",
   "delivery_mode": "own_turn", "content_hash": sha256(delivered),
   "committed_at": unix float, "original_refusal_hash": sha256(refusal)}

turn_marker: no turn_id reaches transform_llm_output (state.py doctrine), so
the turn is identified by the hash of its recovered user message (hook ctx
user_message, else last-seen cache). POST commits carry that marker; the PRE
history_reconciled path commits with "" (prior turn, no live context).
Idempotency is two-layered per the brief:
  - commit dedup on (session_id, content_hash) AND (session_id,
    original_refusal_hash) — an already-canonicalized turn never re-commits;
  - turn-level skip in the POST hook (already_committed_for_turn) keys on
    (session_id, original_refusal_hash, turn_marker) BEFORE the render call —
    the same turn re-firing must not re-render (kills the re-fire loop
    multiplier; the 2026-09-05 false-positive burn re-fired 4-10x per turn).
    Marker-scoped so two genuinely different turns that happen to share a
    byte-identical refusal still both route (pinned by
    test_fallback_guard_does_not_leak_across_messages).

Guard: rewrite_persisted_turn only ever touches a row whose content
byte-matches the refusal the router itself is substituting (exact-match SQL
predicate on the newest such row) — never an arbitrary assistant turn. The
refusal_phrases regex stays TELEMETRY ONLY elsewhere; nothing here gates on it.

Write discipline (mirrors render_inbox.py): append-only, single open, all
failures -> silent no-op + debug log. Persistence must NEVER break the hook.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Dict, Set, Tuple

logger = logging.getLogger(__name__)

CANONICAL_FILENAME = "hermes-router-canonical.jsonl"
MAX_FILE_BYTES = 8 * 1024 * 1024  # rotate past 8 MB
_ROTATE_KEEP_LINES = 200
_LOAD_SCAN_LINES = 1000  # idempotency warm-up scan bound

_PRODUCER = "hermes-router"
_DELIVERY_MODE = "own_turn"

_lock = threading.Lock()
# (session_id, original_refusal_hash) -> {turn_marker, "" for reconcile commits}
_seen_refusal: Dict[Tuple[str, str], Set[str]] = {}
_seen_content: Set[Tuple[str, str]] = set()  # (session_id, content_hash)
# R16-2b (rider 16): session -> the LAST text this router successfully
# rewrote into the persisted transcript (cascade anchor for multi-banner
# turns — see rewrite_persisted_turn). In-process, best-effort only.
_LAST_REWRITE: Dict[str, str] = {}
_loaded = False


def hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


def _store_path() -> str:
    """Profile-scoped sidecar path via hermes_constants.get_hermes_home().
    Falls back to /tmp with a profile-qualified name (mirrors render_inbox)."""
    try:
        import hermes_constants

        return str(hermes_constants.get_hermes_home() / CANONICAL_FILENAME)
    except Exception:  # noqa: BLE001
        return os.path.join("/tmp", "shadow-" + CANONICAL_FILENAME)


def _state_db_path() -> str:
    """Same store the gateway persists every turn to (session_store seam)."""
    from . import session_store

    return session_store._state_db_path()


def get_last_canonical_answer(session_id: str, max_chars: int = 2000) -> str:
    """v3.1.1 contamination fix (Astra canonical-event doctrine, round-2 Q4c):
    the most recent persisted assistant answer for THIS session, read from
    state.db (session-filtered, ORDER BY id DESC, capped). Continuation-style
    asks ("summarize what you just explained") must be rendered grounded in
    the actual delivered answer — not free-associated from persona memory.

    Refusal-shaped rows are SKIPPED: turn_finalizer persists the flash turn
    (the refusal itself) before the POST hook fires, so the newest assistant
    row is often the very refusal being substituted — feeding it back as
    "the previous answer" would anchor the render on the refusal instead of
    the substance.

    Returns "" when nothing usable is found; never raises (fail-open — the
    render proceeds with current behavior on any fetch error)."""
    if not session_id or not str(session_id).strip():
        return ""
    try:
        import sqlite3

        from . import session_store as _ss

        db_path = _state_db_path()
        if not db_path or not os.path.exists(db_path):
            return ""
        from .. import persona_card as _pc

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            rows = conn.execute(
                "SELECT substr(content, 1, ?) FROM messages"
                " WHERE session_id = ? AND role = 'assistant'"
                "   AND content IS NOT NULL AND trim(content) <> ''"
                " ORDER BY id DESC LIMIT 10",
                (_ss._CONTENT_CAP, str(session_id)),
            ).fetchall()
        finally:
            conn.close()
        for (text,) in rows or []:
            text = str(text or "")
            if not text.strip():
                continue
            if _pc._looks_like_refusal(text):
                continue  # skip the just-persisted refusal (and any older one)
            return text[:max_chars]
        return ""
    except Exception as exc:  # noqa: BLE001 — grounding must never break the hook
        logger.debug("canonical last-answer fetch failed: %s", exc)
        return ""


def _load_locked() -> None:
    """Warm the in-memory idempotency sets from the sidecar (bounded scan).
    Caller holds _lock. Tolerates torn lines."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        path = _store_path()
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-_LOAD_SCAN_LINES:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            sid = str(rec.get("session_id") or "")
            rh = str(rec.get("original_refusal_hash") or "")
            marker = str(rec.get("turn_marker") or "")
            ch = str(rec.get("content_hash") or "")
            if rh:
                _seen_refusal.setdefault((sid, rh), set()).add(marker)
            if ch:
                _seen_content.add((sid, ch))
    except Exception as exc:  # noqa: BLE001 — warm-up must never break the hook
        logger.debug("canonical ledger warm-up failed: %s", exc)


def _maybe_rotate_locked(path: str) -> None:
    try:
        if os.path.getsize(path) <= MAX_FILE_BYTES:
            return
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        keep = lines[-_ROTATE_KEEP_LINES:]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(keep)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("canonical ledger rotate failed: %s", exc)


def already_committed_for_turn(session_id: str, original_refusal_hash: str,
                               turn_marker: str) -> bool:
    """True when THIS turn (refusal hash + turn marker) is already
    canonicalized — the POST hook must not re-render it. Marker-scoped so a
    different turn sharing the same refusal text still routes. Never raises."""
    if not original_refusal_hash:
        return False
    try:
        with _lock:
            _load_locked()
            markers = _seen_refusal.get((str(session_id or ""), str(original_refusal_hash)))
            return bool(markers) and (str(turn_marker or "") in markers)
    except Exception:  # noqa: BLE001
        return False


def has_refusal_record(session_id: str, original_refusal_hash: str) -> bool:
    """Marker-agnostic existence check — used by the PRE history_reconciled
    path's 'write the canonical record if missing'. Never raises."""
    if not original_refusal_hash:
        return False
    try:
        with _lock:
            _load_locked()
            return bool(_seen_refusal.get((str(session_id or ""), str(original_refusal_hash))))
    except Exception:  # noqa: BLE001
        return False


def commit_canonical_event(session_id: str, turn_marker: str, content: str,
                           original_refusal_hash: str,
                           producer: str = _PRODUCER,
                           grounded: bool = False, route_id: str = "",
                           delivery_mode: str = _DELIVERY_MODE) -> bool:
    """Append one canonical-event record (idempotent). Returns True when a NEW
    record was written; False when already canonicalized or on any failure.
    Dedup keys: (session_id, content_hash) [brief key] and
    (session_id, original_refusal_hash) [turn identity]. The turn marker is
    registered either way so repeat POST fires of the same turn stay skipped.
    v3.1.1: records also carry `grounded` (render grounded in this session's
    canonical conversation) and `route_id` (anchored-lane correlation; "" for
    plain POST renders).
    v3.2.3 (F3 grounding gate): `delivery_mode` is caller-selectable — the
    POST recovery path keeps the default "own_turn"; the PRE history-reconcile
    path commits ungrounded renders as "advisory_envelope" (no own-turn
    authority fabricated from a free-associated render; v3.1.1 doctrine).
    Blank/unknown values fall back to _DELIVERY_MODE (defensive)."""
    if not content or not str(content).strip():
        return False
    sid = str(session_id or "")
    rh = str(original_refusal_hash or "")
    marker = str(turn_marker or "")
    ch = hash_text(content)
    # v3.2.3: delivery_mode is caller-selectable but validated — only the two
    # doctrinal modes are honored; anything else falls back to own_turn.
    dm = str(delivery_mode or _DELIVERY_MODE).strip()
    if dm not in ("own_turn", "advisory_envelope"):
        dm = _DELIVERY_MODE
    try:
        with _lock:
            # R19.19 P2: never write empty/{} rows — a commit without a
            # session or content hash is unverifiable envelope-wise (the
            # reviewer's empty-row defect). Skip + observe.
            if not str(session_id or "").strip() or not str(ch or "").strip():
                try:
                    logger.warning("canonical_empty_row_skipped sid=%s ch=%s",
                                   str(session_id or "")[:40],
                                   str(ch or "")[:40])
                except Exception:  # noqa: BLE001
                    pass
                return False
            _load_locked()
            fresh = True
            if (sid, ch) in _seen_content:
                fresh = False
            if rh and (sid, rh) in _seen_refusal:
                fresh = False
            if fresh:
                rec = {
                    "session_id": sid,
                    "turn_marker": marker,
                    "producer": str(producer or _PRODUCER),
                    "delivery_mode": dm,
                    "content_hash": ch,
                    "committed_at": round(time.time(), 3),
                    "original_refusal_hash": rh,
                    "grounded": bool(grounded),
                    "route_id": str(route_id or ""),
                }
                path = _store_path()
                d = os.path.dirname(path)
                if d:
                    os.makedirs(d, exist_ok=True)
                _maybe_rotate_locked(path)
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if rh:
                _seen_refusal.setdefault((sid, rh), set()).add(marker)
            _seen_content.add((sid, ch))
            return fresh
    except Exception as exc:  # noqa: BLE001 — persistence must never break the hook
        logger.debug("canonical commit failed: %s", exc)
        return False


def persisted_turn_row_exists(session_id: str, content: str) -> bool:
    """R10-5 (rider 10): True when the persisted transcript ALREADY holds
    an assistant row whose content byte-equals `content` for this session —
    the discriminator between 'rewrite missed because the exact-content
    guard was defeated by gateway-side generation rewrites' (the row
    EXISTS, the banner silently vanished from the persisted body — re-park
    loudly) and 'the row was never persisted' (nothing to heal; re-parking
    here would loop forever). Read-only. Never raises."""
    if not session_id or not content:
        return False
    try:
        import sqlite3

        db_path = _state_db_path()
        if not db_path or not os.path.exists(db_path):
            return False
        conn = sqlite3.connect(db_path, timeout=2.0)
        try:
            cur = conn.execute(
                "SELECT 1 FROM messages WHERE session_id = ?"
                " AND role = 'assistant' AND content = ? LIMIT 1",
                (str(session_id), str(content)),
            )
            return cur.fetchone() is not None
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — read-only probe, never raises
        return False


def rewrite_persisted_turn(session_id: str, refusal_text: str,
                           delivered_text: str,
                           allow_empty_match: bool = False) -> bool:
    """Rewrite the persisted assistant turn to the DELIVERED text.

    Guard (router-substitution-only): the row is matched by EXACT content
    equality with the refusal this router is substituting right now (newest
    matching row) — an arbitrary assistant turn can never match, so nothing
    else is touched. The api_content sidecar (exact-bytes-previously-sent)
    is dropped with the rewrite, mirroring core's drop_stale_api_content
    semantics. Naturally idempotent: after the first rewrite no row matches
    the refusal text anymore. Never raises; True when exactly one row was
    rewritten.

    FIX-FIRST rider 4 (parked-loss, item 1): allow_empty_match=True —
    used ONLY by the empty-body banner delivery edge — additionally
    matches the newest EMPTY assistant row for the session. A 0-char
    flash turn persisted by turn_finalizer before the transform fired
    could never match the old exact-equality guard (empty refusal_text
    was rejected), so the delivered parked banner never landed in the
    persisted transcript (reviewer probes api_1790976140_acd77605 /
    api_1790976260_827dbf1b: 0-char delivered bodies). The match is still
    scoped to the session's newest assistant row and only fires when a
    parked banner is actually delivered over it.

    R16-2b (rider 16): CASCADE fallback for multi-banner turns. A second
    parked delivery on the SAME turn captured rewrite_no_match: the first
    rewrite already replaced the row's content with the first delivered
    text, so the second banner's exact-match on the original response text
    can never hit again — and when the row-presence discriminator also
    failed, the banner was consumed-and-lost (live: valmet D1b compound
    fork, rows billed, 0 banners). Fallback: when the exact match misses,
    the newest assistant row whose content equals the LAST text this
    session successfully rewrote (tracked below) is matched instead — the
    cascade continues the same substitution chain, still scoped to rows
    this router itself produced. Never matches arbitrary agent text."""
    if not session_id or not delivered_text:
        return False
    if not refusal_text and not allow_empty_match:
        return False
    try:
        import sqlite3

        db_path = _state_db_path()
        if not db_path or not os.path.exists(db_path):
            return False
        conn = sqlite3.connect(db_path, timeout=2.0)
        try:
            if refusal_text:
                cur = conn.execute(
                    "UPDATE messages SET content = ?, api_content = NULL"
                    " WHERE id = (SELECT id FROM messages"
                    "            WHERE session_id = ? AND role = 'assistant'"
                    "              AND content = ?"
                    "            ORDER BY id DESC LIMIT 1)",
                    (str(delivered_text), str(session_id), str(refusal_text)),
                )
                # R16-2b cascade: exact match missed — try the last text
                # this router delivered for this session (the previous
                # rewrite's output). Guarded to content this router itself
                # wrote, never an arbitrary row.
                if not cur.rowcount:
                    _last = _LAST_REWRITE.get(str(session_id) or "")
                    if _last:
                        cur = conn.execute(
                            "UPDATE messages SET content = ?, api_content = NULL"
                            " WHERE id = (SELECT id FROM messages"
                            "            WHERE session_id = ? AND role = 'assistant'"
                            "              AND content = ?"
                            "            ORDER BY id DESC LIMIT 1)",
                            (str(delivered_text), str(session_id),
                             str(_last)),
                        )
            else:
                cur = conn.execute(
                    "UPDATE messages SET content = ?, api_content = NULL"
                    " WHERE id = (SELECT id FROM messages"
                    "            WHERE session_id = ? AND role = 'assistant'"
                    "              AND (content = '' OR content IS NULL)"
                    "            ORDER BY id DESC LIMIT 1)",
                    (str(delivered_text), str(session_id)),
                )
            conn.commit()
            ok = bool(cur.rowcount)
            if ok:
                _LAST_REWRITE[str(session_id) or ""] = str(delivered_text)
            return ok
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — must never break the hook
        logger.debug("canonical persisted-turn rewrite failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def clear_for_tests() -> None:
    """Reset in-memory idempotency state (tests only)."""
    global _loaded
    with _lock:
        _seen_refusal.clear()
        _seen_content.clear()
        _LAST_REWRITE.clear()
        _loaded = False