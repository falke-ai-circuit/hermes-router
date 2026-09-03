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
# Consumption tracking (FIX 1 shim, 2026-09-02): in-process ledger of which
# POST renders have already been reconciled into model history. Gateway is a
# single long-lived process — an in-memory set is sufficient; a restart just
# re-reconciles the latest POST render (idempotent: same content replacement).
# ---------------------------------------------------------------------------

_consumed_post: set = set()  # (session_id, ts) tuples


def peek_unconsumed_post(session_id: str, max_scan: int = 50) -> Optional[dict]:
    """Newest unconsumed POST render for this session, or None. Never raises."""
    try:
        recs = read_renders(limit=max_scan)
        for rec in reversed(recs):  # newest first
            if rec.get("stage") != "POST":
                continue
            if rec.get("session_id") != str(session_id or ""):
                continue
            key = (str(session_id or ""), rec.get("ts"))
            if key in _consumed_post:
                continue
            return rec
        return None
    except Exception:  # noqa: BLE001
        return None


def mark_consumed(session_id: str, ts) -> None:
    try:
        _consumed_post.add((str(session_id or ""), ts))
    except Exception:  # noqa: BLE001
        pass
