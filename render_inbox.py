"""Render inbox — persistence for Venice renders (sync seam, 2026-09-02).

Goran-directed (2026-09-02, "1 seems best"): the POST transform fires AFTER
the transcript persists (known Hermes quirk), so the session store keeps the
agent's raw response while the DELIVERED message is the Venice render. The
agent had no way to know what was actually delivered — it composed one
conversation, the user read another. This module persists every render
(PRE + POST) to a JSONL inbox under the profile hermes home so the agent can
read the inbox at turn start and stay synchronized with its own delivery.

Format: one JSON object per line:
  {"ts": <unix float>, "stage": "PRE"|"POST", "session_id": "...",
   "trigger_chars": <int>, "render_chars": <int>, "render": "<full text>"}

Write discipline (mirrors session_store.py): append-only, single open, all
failures -> silent no-op — persistence must NEVER break the hook. No locks:
single gateway process appends; readers tolerate a torn final line by
skipping unparseable lines.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

INBOX_FILENAME = "uncensored-router-renders.jsonl"
MAX_RENDER_CHARS = 40000  # defensive cap per stored render
MAX_FILE_BYTES = 8 * 1024 * 1024  # rotate past 8 MB
_ROTATE_KEEP_LINES = 200  # lines kept after rotation


def _inbox_path() -> str:
    """Profile-scoped inbox path via hermes_constants.get_hermes_home().
    Falls back to /tmp with a profile-qualified name."""
    try:
        import hermes_constants

        return str(hermes_constants.get_hermes_home() / INBOX_FILENAME)
    except Exception:  # noqa: BLE001
        return os.path.join("/tmp", "shadow-" + INBOX_FILENAME)


def record_render(stage: str, session_id: str, trigger_chars: int, render: str) -> None:
    """Append one render record. Never raises; failures are logged + swallowed."""
    try:
        if not render or not str(render).strip():
            return
        entry = {
            "ts": round(time.time(), 3),
            "stage": str(stage or "?"),
            "session_id": str(session_id or ""),
            "trigger_chars": int(trigger_chars or 0),
            "render_chars": len(render),
            "render": str(render)[:MAX_RENDER_CHARS],
        }
        path = _inbox_path()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        _maybe_rotate(path)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — must never break the hook
        logger.debug("uncensored-router render-inbox write failed: %s", exc)


def read_renders(limit: int = 10) -> List[dict]:
    """Read the last `limit` render records (oldest first). Tolerates a torn
    final line. Returns [] on any failure — read must never raise."""
    out: List[dict] = []
    try:
        path = _inbox_path()
        if not os.path.exists(path):
            return out
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        scan = lines[-max(1, int(limit)) * 4:]
        for line in scan:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
        return out[-max(1, int(limit)):]
    except Exception as exc:  # noqa: BLE001
        logger.debug("uncensored-router render-inbox read failed: %s", exc)
        return out


def _maybe_rotate(path: str) -> None:
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
        logger.debug("uncensored-router render-inbox rotate failed: %s", exc)


def last_unseen_marker() -> Optional[int]:
    """Convenience: timestamp of the newest render, or None when inbox empty.
    The agent compares this against its last-known value to detect new
    deliveries between turns."""
    recs = read_renders(limit=1)
    if not recs:
        return None
    ts = recs[-1].get("ts")
    return int(ts) if isinstance(ts, (int, float)) else None

# ---------------------------------------------------------------------------
# Consumption tracking (FIX 1 shim, 2026-09-02): ledger of which POST renders
# have already been reconciled into model history.
# v3.2.3 (F2 stale-render replay fix): the in-process set was NOT persistent —
# every gateway restart re-scanned renders.jsonl from the beginning and
# re-paired OLD POST renders with (already-reconciled or scaffolded) history
# turns, replaying the same renders turn after turn (conductor live: 410
# history_reconciled fires for the same two renders). The ledger is now
# PERSISTENT: a JSON sidecar (hermes-router-reconciled.json, profile hermes
# home, same family as the renders inbox) keyed (session_id, ts). mark_consumed
# writes both layers; the reconcile scan consults both. Restart-safe by design:
# the sidecar is re-read on first access after boot. Bounded: the sidecar load
# warms the newest _MAX_ENTRIES entries; the file rotates at _MAX_FILE_BYTES.
# ---------------------------------------------------------------------------

_consumed_post: set = set()  # (session_id, ts) tuples — in-process fast path

_RECONCILED_FILENAME = "hermes-router-reconciled.json"
_MAX_FILE_BYTES = 256 * 1024  # rotate past 256 KB
_MAX_ENTRIES = 4000  # newest entries kept after rotation / on load


def _reconciled_path() -> str:
    """Profile-scoped reconcile-marker path via hermes_constants
    (mirrors the renders inbox). Falls back to /tmp with a
    profile-qualified name."""
    try:
        import hermes_constants

        return str(hermes_constants.get_hermes_home() / _RECONCILED_FILENAME)
    except Exception:  # noqa: BLE001
        return os.path.join("/tmp", "shadow-" + _RECONCILED_FILENAME)


_reconciled_loaded = False
_RECONCILED_LOCK = threading.Lock()


def _load_reconciled_locked() -> None:
    """Warm the in-process set from the persistent sidecar (caller holds
    _RECONCILED_LOCK). Tolerates torn/corrupt files (fresh set on error)."""
    global _reconciled_loaded
    if _reconciled_loaded:
        return
    _reconciled_loaded = True
    try:
        path = _reconciled_path()
        if not os.path.exists(path):
            return
        # JSONL of [session_id, ts] pairs (append-only, like the marker write).
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        for line in lines[-_MAX_ENTRIES:]:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue  # torn final line
            if (isinstance(item, list) and len(item) == 2
                    and isinstance(item[0], str)):
                _consumed_post.add((item[0], item[1]))
    except Exception as exc:  # noqa: BLE001 — marker load must never break routing
        logger.debug("uncensored-router reconcile-marker load failed: %s", exc)


def _is_consumed(session_id: str, ts) -> bool:
    """True when (session_id, ts) is already marked consumed, consulting the
    persistent sidecar (lazy warm-up covers the post-restart case)."""
    try:
        key = (str(session_id or ""), ts)
        with _RECONCILED_LOCK:
            _load_reconciled_locked()
            return key in _consumed_post
    except Exception:  # noqa: BLE001
        return False


def mark_consumed(session_id: str, ts) -> None:
    """Mark a render reconciled — in-process AND persistent (append + rotate).
    Persistence must never break the hook: all failures are swallowed."""
    try:
        key = (str(session_id or ""), ts)
        with _RECONCILED_LOCK:
            _load_reconciled_locked()
            _consumed_post.add(key)
            path = _reconciled_path()
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            _maybe_rotate_reconciled(path)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(list(key), ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — must never break the hook
        logger.debug("uncensored-router reconcile-marker write failed: %s", exc)


def _maybe_rotate_reconciled(path: str) -> None:
    """Keep the marker file bounded: when over _MAX_FILE_BYTES, rewrite with
    only the newest _MAX_ENTRIES entries. Caller holds _RECONCILED_LOCK.
    The file is JSONL of [session_id, ts] pairs (append-friendly rotation:
    read pairs line-wise, tolerate a torn final line)."""
    try:
        if os.path.exists(path) and os.path.getsize(path) > _MAX_FILE_BYTES:
            pairs = []
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # torn final line
                    if (isinstance(item, list) and len(item) == 2
                            and isinstance(item[0], str)):
                        pairs.append(item)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                for item in pairs[-_MAX_ENTRIES:]:
                    fh.write(json.dumps(item, ensure_ascii=False) + "\n")
            os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("uncensored-router reconcile-marker rotate failed: %s", exc)


def clear_consumed_for_tests() -> None:
    """Reset BOTH consume layers (tests only). Does NOT delete the sidecar on
    disk — tests redirect _reconciled_path to tmp (see conftest)."""
    global _reconciled_loaded
    with _RECONCILED_LOCK:
        _consumed_post.clear()
        _reconciled_loaded = False


def peek_unconsumed_post(session_id: str, max_scan: int = 50) -> Optional[dict]:
    """Newest unconsumed POST render for this session, or None. Never raises."""
    try:
        recs = read_renders(limit=max_scan)
        for rec in reversed(recs):  # newest first
            if rec.get("stage") != "POST":
                continue
            if rec.get("session_id") != str(session_id or ""):
                continue
            if _is_consumed(session_id, rec.get("ts")):
                continue
            return rec
        return None
    except Exception:  # noqa: BLE001
        return None
