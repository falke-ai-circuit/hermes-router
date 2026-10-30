"""features/banners/turn_record.py — ONE banner per (session, turn).

Goran, live complaint 2026-10-30: a single turn produced two or three separate
router banner lines because every lane rendered AND appended its own — the
decision lane at decision.py (render_decision_banner), completion_audit at
three sites, the frontier/anchor park, plus the uncensored render paths. Ledger
rows 611 and 612 were the same lane twice in one turn.

The envelope rule, applied to banners: one builder, segments registered, one
emission.

  * REGISTER at fire time — every lane hands its rendered segment here
    (park_anchor_banner is the shared fire-time hook; the direct-render edges
    register their segment on the way to delivery). Segments keep the facts the
    old banners carried: lane, kind/trigger, verdict text, model, tokens, cost
    and the reconcilable `row=` id — the segment IS the existing rendered line,
    so no fact is re-derived or dropped.
  * RENDER once — one segment renders as that one line; several segments render
    as ONE contiguous block (single "\\n" join, never a blank line, never a
    second append). Same-lane repeats collapse through the existing
    merge_decision_banner (counts/tokens/cost summed, row span extended), which
    is what "same lane twice" is supposed to look like.
  * EMIT once — the first delivery edge of the turn performs the single
    append and marks the turn emitted; every later delivery edge in the SAME
    turn returns the body untouched. A new turn starts a new record.

Banner never enters the persisted body: this module only shapes the DELIVERY
representation at the same edge callers that already existed.

Never raises. Bounded state (32 sessions, 2 turns of retention). Deterministic.
"""
from __future__ import annotations

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
# register / render / emit
# ---------------------------------------------------------------------------

def register(session_id: str, segment: str, *, lane: str = "",
             kind: str = "", edge: str = "") -> bool:
    """Register one lane's rendered banner segment for this turn.

    Idempotent: an identical segment is not stored twice, and a second segment
    from the SAME lane/kind/row replaces the earlier one (latest wins for a
    retry). Returns True when the record changed. Never raises.
    """
    try:
        text = str(segment or "").strip()
        if not text:
            return False
        rec = _record(session_id)
        _lane = str(lane or "") or _lane_of(text)
        _kind = str(kind or "")
        _row = _row_of(text)
        segs: List[Dict[str, Any]] = rec["segments"]
        for i, seg in enumerate(segs):
            if seg["text"] == text:
                return False
            if (seg["lane"] == _lane and seg["kind"] == _kind
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
    """The turn's ONE banner text: one line, or one contiguous block."""
    try:
        rec = _RECORDS.get(str(session_id or ""))
        if not rec or not rec.get("segments"):
            return ""
        from ... import debug_banner as _db

        segs = [s["text"] for s in rec["segments"] if str(s.get("text") or "").strip()]
        if not segs:
            return ""
        out = segs[0]
        for text in segs[1:]:
            merged = None
            try:
                merged = _db.merge_decision_banner(out, text)
            except Exception:  # noqa: BLE001 — merge is a nicety
                merged = None
            out = merged if isinstance(merged, str) and merged else (
                out.rstrip() + "\n" + text.strip())
        return out
    except Exception:  # noqa: BLE001
        return ""


def emit_once(delivery_text: str, session_id: str, *,
              extra_segment: str = "", lane: str = "", kind: str = "",
              edge: str = "", prefix: str = "",
              replace: bool = False, knob_checked: bool = False) -> str:
    """Append this turn's banner block to `delivery_text` AT MOST ONCE.

    `extra_segment` registers a segment that reached this edge without going
    through the park path (live render edges). Later edges in the same turn get
    the body back unchanged — that is the "one banner per turn" contract.
    Never raises; never returns None; never mutates the persisted body.
    """
    try:
        base = delivery_text if isinstance(delivery_text, str) else ""
        if replace:
            # This edge OWNS the delivery (a render appends its own banner and
            # claims the parked slot) — stale segments are dropped, not merged.
            reset(session_id)
        if extra_segment:
            register(session_id, extra_segment, lane=lane, kind=kind, edge=edge)
        rec = _RECORDS.get(str(session_id or ""))
        if not rec or rec.get("emitted"):
            return base
        banner = render(session_id)
        if not banner.strip() or not base.strip():
            return base
        from ... import debug_banner as _db

        # The banner KNOB still gates emission (the old call sites let
        # append_banner read it; a disabled knob means no banner at all).
        # Call sites that already checked the knob pass knob_checked=True,
        # exactly as they did against append_banner before.
        out = _db.append_banner(base, prefix + banner,
                                _knob_checked=bool(knob_checked))
        out = out if isinstance(out, str) and out else base
        landed = bool(banner.strip() and banner.strip() in out)
        if landed:
            rec["emitted"] = True
            try:
                from ...core import telemetry as _tlm

                _tlm.log_route(
                    "POST", event_detail="banner_turn_emitted",
                    segments=len(rec.get("segments") or []),
                    lanes=",".join(sorted({s["lane"] for s in rec["segments"]})),
                    rows=",".join(s["row"] for s in rec["segments"] if s["row"]),
                    edge=str(edge or "")[:40], session_id=str(session_id or ""))
            except Exception:  # noqa: BLE001 — telemetry never breaks delivery
                pass
        return out
    except Exception as exc:  # noqa: BLE001 — banners never break delivery
        try:
            logger.error("banner_turn_emit_failed detail=%.200s", str(exc))
        except Exception:  # noqa: BLE001
            pass
        return delivery_text if isinstance(delivery_text, str) else ""


def segment_count(session_id: str) -> int:
    """How many lane segments this turn has registered. Never raises."""
    try:
        rec = _RECORDS.get(str(session_id or ""))
        return len(rec.get("segments") or []) if rec else 0
    except Exception:  # noqa: BLE001
        return 0


def delivered(session_id: str) -> bool:
    """True when this turn's banner already reached a delivered body.

    A later sweep of the SAME turn must not be reported as "attach not landed"
    (that reason means the banner was lost) — it was delivered, once.
    """
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
