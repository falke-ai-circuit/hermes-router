"""features/banners/lifecycle.py — BannerLifecycle (P5, proposal §2.2).

ONE object owns park / consume / expire / capture-fallback for parked
anchor banners. The imperative bodies are transplanted VERBATIM from
debug_banner.py:246-430 (pre-P5) — the state dicts stay owned by
hermes_router.debug_banner (the live test surface) and this module
operates on them, so behavior is byte-identical (Binding 1).

The delivery CHOKEPOINT is BannerLifecycle.deliver(): every delivery
edge routes through it. deliver() raises IllegalDeliveryEdge when the
edge is not legal for the kind's delivery_edges; edge callers catch it,
emit a `banner_deliver_fail` telemetry row, and still deliver the body
(fail-open — I2 intact).
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from hermes_router.core import telemetry as _tlm
from hermes_router.features.banners import kinds as _kinds

logger = logging.getLogger(__name__)

_MAX_BANNER_CHARS_IMPORT = None  # imported lazily from debug_banner


def _db():
    """Late-bind the banner formatter module (owns the state dicts)."""
    import hermes_router.debug_banner as _m

    return _m


def _log_route(event: str, **fields: Any) -> None:
    _tlm.log_route(event, **fields)


class IllegalDeliveryEdge(Exception):
    """A banner kind was delivered/consumed on an edge it does not allow."""

    def __init__(self, kind_id: str, edge: str) -> None:
        super().__init__(
            "banner kind %r not legal on delivery edge %r" % (kind_id, edge))
        self.kind_id = kind_id
        self.edge = edge


class BannerLifecycle:
    """Owns park/consume/expire/capture-fallback for BannerKind kinds."""

    def park(self, kind: _kinds.BannerKind, session_id: str, task_id: str,
             text: str) -> None:
        db = _db()
        try:
            if len(db._ANCHOR_BANNERS) >= db._ANCHOR_BANNER_MAX:
                db._ANCHOR_BANNERS.pop(next(iter(db._ANCHOR_BANNERS)), None)
                db._ANCHOR_TASKS.pop(next(iter(db._ANCHOR_TASKS)), None)
            sid = str(session_id or "")
            seg = str(text or "").strip()[:db.MAX_BANNER_CHARS]
            if not seg:
                return
            if kind.stack_policy == "stack":
                # R19.16 aggregate stack + R9d per-task replace + identical
                # re-park dedupe (verbatim pre-P5 path).
                segs: List[str] = list(db._ANCHOR_SEGS.get(sid, []))
                tasks: List[str] = list(db._ANCHOR_TASKS.get(sid, []))
                if task_id and task_id in tasks:
                    # R9d retry: replace THAT task's segment ATOMICALLY (fire
                    # order kept) — a park's internal blank lines never split
                    # segments.
                    idx = tasks.index(task_id)
                    if idx < len(segs):
                        segs[idx] = seg
                    else:
                        segs.append(seg)
                elif seg in segs:
                    return  # identical re-park (same content) — dedupe
                else:
                    segs.append(seg)
                    tasks.append(str(task_id or ""))
                # bound: keep the most recent segments (banner side only —
                # the canonical delivery text is never trimmed)
                while len(segs) > db._MAX_PARK_SEGMENTS:
                    segs.pop(0)
                    if tasks:
                        tasks.pop(0)
                db._ANCHOR_SEGS[sid] = segs
                db._ANCHOR_TASKS[sid] = tasks
                db._ANCHOR_BANNERS[sid] = "\n\n".join(segs)
            elif kind.stack_policy == "replace":
                # R9d-only shape: the newest park for this session replaces
                # the whole slot (no registered kind uses this today — the
                # branch exists so the policy is DATA, not dead enum).
                db._ANCHOR_SEGS[sid] = [seg]
                db._ANCHOR_TASKS[sid] = [str(task_id or "")]
                db._ANCHOR_BANNERS[sid] = seg
            else:  # "once": first park wins until consumed
                if sid not in db._ANCHOR_BANNERS:
                    db._ANCHOR_SEGS[sid] = [seg]
                    db._ANCHOR_TASKS[sid] = [str(task_id or "")]
                    db._ANCHOR_BANNERS[sid] = seg
            # R20-D3 (rider 20): schedule the one-shot bounded
            # capture-fallback watcher — gated by the KIND's flag now.
            if kind.capture_fallback:
                self._maybe_schedule_capture_fallback(sid)
        except Exception:  # noqa: BLE001
            pass

    def consume(self, kind: _kinds.BannerKind, session_id: str,
                task_id: str, edge: str) -> str:
        if edge not in kind.delivery_edges:
            raise IllegalDeliveryEdge(kind.kind_id, edge)
        db = _db()
        try:
            sid = str(session_id or "")
            db._ANCHOR_TASKS.pop(sid, None)
            db._ANCHOR_SEGS.pop(sid, None)
            return db._ANCHOR_BANNERS.pop(sid, "")
        except Exception:  # noqa: BLE001
            return ""

    def expire(self) -> None:
        """TTL sweep for kinds with ttl_seconds > 0. No registered kind
        carries a TTL today (parks persist until consumed) — this owns the
        future policy; bounded-map FIFO eviction remains in park()."""
        return None

    def peek(self, session_id: str) -> str:
        """Non-consuming read of the parked banner slot (recovery paths
        that need the parked text after a vanish-path re-park)."""
        try:
            return _db()._ANCHOR_BANNERS.get(str(session_id or ""), "")
        except Exception:  # noqa: BLE001
            return ""

    # --- delivery chokepoint ------------------------------------------------

    def deliver(self, session_id: str, text: str, kind_id: str,
                edge: str, *, mode: str = "append",
                log_edge: Optional[str] = None,
                log_consume: bool = True) -> str:
        """THE delivery chokepoint.

        mode="append" (default): consume the parked banner and append it to
        `text` (the delivered body); a consumed banner that did NOT land is
        RE-PARKED for next-turn delivery (R19.19/R19.21 — never
        consumed-and-lost). Returns the merged text.
        mode="body": the parked banner IS the body (empty-body edge).
        mode="discard": one-shot release without delivery (claim guard).
        Raises IllegalDeliveryEdge if `edge` is not in the kind's
        delivery_edges — the edge callers catch it, emit the
        `banner_deliver_fail` telemetry row, and still deliver the body
        (fail-open). Never returns None."""
        kind = _kinds.get(kind_id)
        if edge not in kind.delivery_edges:
            raise IllegalDeliveryEdge(kind_id, edge)
        db = _db()
        # consume through the debug_banner delegate — the historical patch
        # surface (tests patch debug_banner.consume_parked_banner).
        parked = db.consume_parked_banner(session_id)
        base = text if isinstance(text, str) else ""
        if mode == "discard":
            return base
        _e = str(log_edge if log_edge is not None else edge)
        if log_consume:
            _log_route("POST", event_detail="anchor_banner_consume",
                       parked=bool(parked), edge=_e,
                       session_id=str(session_id or ""))
        if not parked:
            return base if mode == "append" else ""
        try:
            db.note_consumed_decision(session_id, parked)
        except Exception:  # noqa: BLE001
            pass
        if mode == "body":
            return parked
        merged = db.append_banner(base, "\n" + parked)
        out = merged if isinstance(merged, str) and merged else str(base or "")
        if parked.strip() not in out:
            # R19.19 P0 / R19.21: a consumed banner that did not land is
            # re-parked — never consumed-and-lost.
            try:
                db.park_anchor_banner(session_id, parked)
            except Exception:  # noqa: BLE001
                pass
            _log_route("POST", event_detail="banner_redelivered_next_turn",
                       edge=_e, session_id=str(session_id or ""))
        return out

    # --- R20-D3 capture-fallback (moved verbatim from debug_banner) ---------

    def _maybe_schedule_capture_fallback(self, session_id: str) -> None:
        """R20-D3: schedule the one-shot capture-fallback watcher for a
        session that just parked a banner (semaphored per session — at
        most ONE watcher in flight per session). Daemon thread, never
        raises, never blocks the parker."""
        try:
            if self.banner_capture_fallback_wait() <= 0:
                return
            sid = str(session_id or "")
            if not sid:
                return
            spawn = False
            with self._CAPTURE_FB_LOCK:
                if sid not in self._CAPTURE_FB_INFLIGHT:
                    self._CAPTURE_FB_INFLIGHT.add(sid)
                    spawn = True
            if not spawn:
                return
            captured = _db()._ANCHOR_BANNERS.get(sid, "")
            if not str(captured or "").strip():
                with self._CAPTURE_FB_LOCK:
                    self._CAPTURE_FB_INFLIGHT.discard(sid)
                return
            threading.Thread(
                target=self._capture_fallback_watch, args=(sid, captured),
                daemon=True,
                name="rider20-capture-fallback-%s" % sid[:24]).start()
        except Exception:  # noqa: BLE001 — scheduling must never break the park
            pass

    def banner_capture_fallback_wait(self) -> float:
        """R20-D3: seconds the capture-fallback watcher polls for
        consumption by a delivery edge before rewriting the persisted turn
        itself (knob banner_capture_fallback_wait, default 45s; 0
        disables). Never raises."""
        try:
            raw = _db()._banner_section().get("banner_capture_fallback_wait", 45)
            return max(0.0, min(120.0, float(raw)))
        except Exception:  # noqa: BLE001
            return 45.0

    def _newest_persisted_assistant_row(self, session_id: str) -> str:
        """R20-D3: newest persisted assistant row content for this session
        from state.db. Read-only; "" on any failure. Never raises."""
        try:
            import os
            import sqlite3

            from hermes_router.core import canonical as _canon

            db_path = _canon._state_db_path()
            if not db_path or not os.path.exists(db_path):
                return ""
            conn = sqlite3.connect(db_path, timeout=2.0)
            try:
                cur = conn.execute(
                    "SELECT content FROM messages WHERE session_id = ?"
                    " AND role = 'assistant' ORDER BY id DESC LIMIT 1",
                    (str(session_id or ""),),
                )
                row = cur.fetchone()
                return str(row[0]) if row and row[0] is not None else ""
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — read-only probe, never raises
            return ""

    def _capture_fallback_watch(self, session_id: str, captured: str) -> None:
        """R20-D3 worker body (verbatim from debug_banner): after park,
        poll (1s interval) for consumption by any delivery edge; if still
        parked unconsumed at the budget, append the banner block to the
        session's newest persisted assistant row and rewrite via
        canonical.rewrite_persisted_turn exact-match. The parked content
        is re-checked UNDER THE MODULE LOCK immediately before the
        rewrite. On no matching row: RE-PARK + fail-loud event. Never
        raises."""
        db = _db()
        sid = str(session_id or "")
        try:
            budget = self.banner_capture_fallback_wait()
            if budget <= 0 or not str(captured or "").strip():
                return
            deadline = _time_monotonic() + budget
            while _time_monotonic() < deadline:
                _time_sleep(1.0)
                if db._ANCHOR_BANNERS.get(sid, "") != captured:
                    return  # a live delivery edge consumed/changed the slot
            with self._CAPTURE_FB_LOCK:
                # re-check under the module lock: a late live consume wins
                if db._ANCHOR_BANNERS.get(sid, "") != captured:
                    return
                row = self._newest_persisted_assistant_row(sid)
                delivered = ("%s\n\n%s" % (row, captured)) if row else ""
                ok = False
                if row:
                    try:
                        from hermes_router.core import canonical as _canon

                        ok = _canon.rewrite_persisted_turn(
                            sid, row, delivered)
                    except Exception:  # noqa: BLE001 — never break delivery
                        ok = False
                if ok:
                    # captured: clear the parked slot (consume-equivalent,
                    # under the lock so no racing edge double-delivers)
                    db._ANCHOR_TASKS.pop(sid, None)
                    db._ANCHOR_SEGS.pop(sid, None)
                    db._ANCHOR_BANNERS.pop(sid, None)
            if ok:
                try:
                    from hermes_router import render_inbox as _ri

                    _ri.record_render("PARKED_CAPTURE_FALLBACK", sid,
                                      len(row), delivered)
                except Exception:  # noqa: BLE001 — best-effort evidence
                    pass
                try:
                    _log_route("POST",
                               event_detail="banner_capture_fallback",
                               outcome="captured", session_id=sid)
                    _log_route("POST",
                               event_detail="banner_render_captured",
                               edge="capture_fallback", session_id=sid)
                except Exception:  # noqa: BLE001 — fail-loud best-effort
                    logger.exception("banner_capture_fallback event log failed")
            else:
                # no persisted assistant row matched: RE-PARK the banner
                # (retained for the next delivery edge) + fail-loud event.
                with self._CAPTURE_FB_LOCK:
                    db._ANCHOR_BANNERS[sid] = captured
                try:
                    logger.error(
                        "banner_capture_fallback_failed detail=no_row_match "
                        "session_id=%s — parked banner retained for the next "
                        "delivery edge", sid)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    _log_route("POST",
                               event_detail="banner_capture_fallback",
                               outcome="no_row_match", re_parked=True,
                               session_id=sid)
                except Exception:  # noqa: BLE001
                    logger.exception("banner_capture_fallback event log failed")
        except Exception:  # noqa: BLE001 — the fallback must never break anything
            try:
                logger.exception("banner_capture_fallback worker error")
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                with self._CAPTURE_FB_LOCK:
                    self._CAPTURE_FB_INFLIGHT.discard(sid)
            except Exception:  # noqa: BLE001
                pass

    _CAPTURE_FB_LOCK = threading.Lock()       # module lock: park/rewrite race
    _CAPTURE_FB_INFLIGHT: set = set()         # per-session semaphore


# module-level clock indirections (kept trivial — mirror of time module use)
def _time_monotonic() -> float:
    import time

    return time.monotonic()


def _time_sleep(s: float) -> None:
    import time

    time.sleep(s)


LIFECYCLE = BannerLifecycle()
