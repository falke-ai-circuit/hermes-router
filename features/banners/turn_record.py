"""features/banners/turn_record.py — banners are RECORDS, not body content.

M35-17 (supersedes the M34-19 carry and the §10.4-F two-representation rule):
a lane's banner is turn PROVENANCE, and provenance is never content of the
turn. This module therefore has no function that returns text a caller could
append to a body:

  * REGISTER at fire time — every lane hands its rendered segment here
    (`park_anchor_banner` is the shared fire-time hook; the direct-render
    edges register their own segment).
  * RECORD once — `record_turn()` writes ONE `banner_record` line per
    (session, turn) into the decisions store. It accepts the delivered body
    only to hash it and returns None, so there is no signature through which
    a banner could enter a body.
  * RENDER elsewhere — `features/banners/render.py` is a pure function of a
    record, used by the operator/audit surface only.

Never raises. Bounded state (32 sessions, 2 turns of retention). Deterministic.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_RECORDS: Dict[str, Dict[str, Any]] = {}
_MAX_RECORDS = 32
_TTL_S = 900.0
_LOCK = threading.Lock()

_LANE_LABELS = (
    ("impulse (decision)", "decision"),
    ("higher-self (frontier)", "frontier"),
    ("shadow-self (uncensored)", "uncensored"),
)
_ROW_RE = re.compile(r"row=(\d+)")


# ---------------------------------------------------------------------------
# turn identity — the same signal the park semaphore uses
# ---------------------------------------------------------------------------

def turn_key(session_id: str) -> Tuple[int, str]:
    """(turn_no, ask signature) for this session. Never raises."""
    try:
        from ... import advisory_consumption as _ac  # hermes_router.advisory_consumption

        return (int(_ac.turn_no(session_id) or 0),
                str(_ac.open_signal(session_id) or "")[:40])
    except Exception:  # noqa: BLE001 — identity is best-effort only
        return (0, "")


def turn_no(session_id: str) -> int:
    """The turn number for this session, 0 when unknown. Never raises."""
    try:
        return int(turn_key(session_id)[0])
    except Exception:  # noqa: BLE001
        return 0


def _lane_of(text: str) -> str:
    s = str(text or "")
    for label, lane in _LANE_LABELS:
        if label in s:
            return lane
    return "router"


def _row_of(text: str) -> str:
    m = _ROW_RE.search(str(text or ""))
    return m.group(1) if m else ""


def _record(session_id: str) -> Dict[str, Any]:
    sid = str(session_id or "")
    now = time.time()
    key = turn_key(sid)
    rec = _RECORDS.get(sid)
    if rec is not None:
        stale = (now - float(rec.get("touched") or 0)) > _TTL_S
        if rec.get("turn_key") != key or stale:
            rec = None  # turn changed (or record expired) -> new record
    if rec is None:
        rec = {"turn_key": key, "segments": [], "emitted": False,
               "touched": now, "seq": 0}
        with _LOCK:
            while len(_RECORDS) >= _MAX_RECORDS:
                _RECORDS.pop(next(iter(_RECORDS)), None)
            _RECORDS[sid] = rec
    rec["touched"] = now
    return rec


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------

def register(session_id: str, segment: str, *, lane: str = "",
             kind: str = "", edge: str = "", **kwargs) -> bool:
    """Register one lane's rendered banner segment for this turn.

    Idempotent: an identical segment is not stored twice, and a second segment
    from the SAME lane/kind/row replaces the earlier one (latest wins for a
    retry). `frontier`/`decision` repeats stack as separate measurements.
    Returns True when the record changed. Never raises. The keyword-only
    leftovers (`replace`, `prefix`, `knob_checked`, ...) are accepted so an
    older call site cannot break registration.
    """
    try:
        text = str(segment or "").strip()
        if not text:
            return False
        rec = _record(session_id)
        _lane = str(lane or "") or _lane_of(text)
        _kind = str(kind or "")
        _row = _row_of(text)
        _aggregating = _lane.startswith("frontier") or _lane == "decision"
        segs: List[Dict[str, Any]] = rec["segments"]
        for i, seg in enumerate(segs):
            if seg["text"] == text:
                return False
            if not _aggregating and (seg["lane"] == _lane and seg["kind"] == _kind
                                     and seg["row"] == _row):
                segs[i] = {"lane": _lane, "kind": _kind, "row": _row,
                           "text": text, "edge": str(edge or "")}
                return True
        rec["seq"] = int(rec.get("seq") or 0) + 1
        segs.append({"lane": _lane, "kind": _kind, "row": _row,
                     "text": text, "edge": str(edge or "")})
        while len(segs) > 8:  # bound: one turn cannot accumulate unboundedly
            segs.pop(0)
        return True
    except Exception:  # noqa: BLE001 — registration never breaks a lane
        return False


def render(session_id: str) -> str:
    """The turn's banner BLOCK for extraction (one line per segment).

    Nothing on the delivery path calls this — it exists for the operator
    surface and for a test that must prove a segment was registered.
    """
    try:
        rec = _RECORDS.get(str(session_id or ""))
        if not rec or not rec.get("segments"):
            return ""
        return "\n".join(str(s.get("text") or "").strip()
                         for s in rec["segments"]
                         if str(s.get("text") or "").strip())
    except Exception:  # noqa: BLE001
        return ""


def segments(session_id: str) -> List[Dict[str, Any]]:
    """Copy of this turn's registered segments. Never raises."""
    try:
        rec = _RECORDS.get(str(session_id or ""))
        return [dict(s) for s in (rec.get("segments") or [])] if rec else []
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# record — the ONE emit point
# ---------------------------------------------------------------------------

def record_turn(session_id: str, turn_id: Any = None, delivered: Any = None,
                decision: Any = None, segs: Optional[List[Dict[str, Any]]] = None,
                *, knob_level: int = 0, lane: str = "") -> None:
    """Write ONE `banner_record` for this (session, turn). Returns NOTHING.

    The delivered body is accepted only to hash and measure it — the record
    carries `body_sha256`/`body_chars`, never the bytes. A second call for the
    same turn_id drops the payload and increments `dupes` on the first record,
    so a retried delivery edge cannot double-count tokens, cost or rows.

    Never raises; a failed record never withholds a turn.
    """
    try:
        from ...core import decisions as _dec

        sid = str(session_id or "")
        _segs = list(segs) if segs is not None else segments(sid)
        _tid = str(turn_id) if turn_id not in (None, "") else str(turn_key(sid)[0])
        body = delivered if isinstance(delivered, str) else ""
        if delivered is None:
            body_sha = ""
        else:
            body_sha = hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()
        _lane = str(lane or "")
        if not _lane:
            _lane = str(getattr(decision, "delivered_source", "") or "") or "main"
        ok = _dec.record_banner(
            session_id=sid, turn_id=_tid,
            lane=_lane,
            decision=str(getattr(decision, "case_id", "") or ""),
            delivered_source=str(getattr(decision, "delivered_source", "") or ""),
            action=str(getattr(decision, "action", "") or ""),
            outcome=str(getattr(decision, "reason", "") or "")[:20],
            body_sha256=body_sha, body_chars=len(body),
            knob_level=int(knob_level or 0),
            omit_reason=(getattr(decision, "reason", "") or "")
            if not body else "",
            banner=[{"lane": s.get("lane", ""), "kind": s.get("kind", ""),
                     "trigger": s.get("kind", ""),
                     "model": "", "endpoint_host": "",
                     "tokens_in": 0, "tokens_out": 0,
                     "est_cost": 0.0, "cost_basis": "unpriced",
                     "latency_s": 0.0, "retries": 0,
                     "row_ref": (s.get("row") or "MISSING"),
                     "initiator": "agent",
                     "edge": s.get("edge", ""),
                     "text": s.get("text", "")} for s in _segs],
        )
        if ok:
            rec = _RECORDS.get(sid)
            if rec is not None:
                rec["emitted"] = True
    except Exception as exc:  # noqa: BLE001 — a record never withholds a turn
        try:
            logger.error("banner_record_failed detail=%.200s", str(exc))
        except Exception:  # noqa: BLE001
            pass


def segment_count(session_id: str) -> int:
    """How many lane segments this turn has registered. Never raises."""
    try:
        rec = _RECORDS.get(str(session_id or ""))
        return len(rec.get("segments") or []) if rec else 0
    except Exception:  # noqa: BLE001
        return 0


def delivered(session_id: str) -> bool:
    """True when this turn's banner record has been written."""
    try:
        rec = _RECORDS.get(str(session_id or ""))
        return bool(rec and rec.get("emitted"))
    except Exception:  # noqa: BLE001
        return False


def record(session_id: str) -> Dict[str, Any]:
    """Read-only view of this session's turn record (tests/telemetry)."""
    try:
        rec = _RECORDS.get(str(session_id or ""))
        if not rec:
            return {}
        return {"turn_key": rec.get("turn_key"), "emitted": bool(rec.get("emitted")),
                "segments": [dict(s) for s in rec.get("segments") or []]}
    except Exception:  # noqa: BLE001
        return {}


def reset(session_id: str = "") -> None:
    """Drop one session's record (or all). Never raises."""
    try:
        if session_id:
            _RECORDS.pop(str(session_id), None)
        else:
            _RECORDS.clear()
    except Exception:  # noqa: BLE001
        pass
