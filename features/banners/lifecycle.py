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

from ...core import telemetry as _tlm
from ...features.banners import kinds as _kinds

logger = logging.getLogger(__name__)

_MAX_BANNER_CHARS_IMPORT = None  # imported lazily from debug_banner


def _db():
    """Late-bind the banner formatter module (owns the state dicts)."""
    from ... import debug_banner as _m

    return _m


def _log_route(event: str, **fields: Any) -> None:
    _tlm.log_route(event, **fields)


# R34-2: a banner deferred after the preferred-edge no_row_match keeps its
# slot until the next legal delivery edge attaches it. This TTL bounds that
# deferral so a park with no consumer can never outlive its turn by more
# than one budget cycle.
_DEFER_TTL_S = 600.0


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
        # R34-2: a DEFERRED slot (preferred-edge no_row_match) is eligible
        # for exactly ONE attach at the next legal delivery edge — but only
        # inside the deferral TTL. Past it, the slot is dropped fail-loud.
        _deferred_at = 0.0
        try:
            _rec_pre = db.park_record(session_id) or {}
            _deferred_at = float(_rec_pre.get("deferred_at") or 0.0)
        except Exception:  # noqa: BLE001 — record read never breaks delivery
            _deferred_at = 0.0
        if _deferred_at and (_time_wall() - _deferred_at) > _DEFER_TTL_S:
            try:
                db.consume_parked_banner(session_id)
            except Exception:  # noqa: BLE001
                pass
            try:
                _log_route("POST", event_detail=db.BANNER_PARK_DROPPED_EVENT,
                           edge=str(log_edge if log_edge is not None else edge),
                           reason="deferral_ttl_expired",
                           session_id=str(session_id or ""))
            except Exception:  # noqa: BLE001
                pass
            return (text if isinstance(text, str) else "") if mode == "append" else ""
        # R34-15 item 1/3: park-turn ownership. A banner parked in an EARLIER
        # turn is a turn that closed undelivered -> DROP it here, at the one
        # chokepoint every delivery edge routes through, so no edge (benign,
        # audit_sync, render) can attach a stale turn's banner. R33f-A stays
        # intact: a turn-N banner never reaches turn N+1. Same-turn misses are
        # retained by the caller (banner_park_retained) and retried on the
        # last banner-eligible pass of that turn.
        _e_guard = str(log_edge if log_edge is not None else edge)
        try:
            from ... import advisory_consumption as _act

            _rec_guard = db.park_record(session_id) or {}
            _pt = int(_rec_guard.get("turn_no") or 0)
            _ct = int(_act.turn_no(session_id) or 0)
            if _pt and _ct and _pt < _ct and \
                    db._ANCHOR_BANNERS.get(str(session_id or "")):
                # R34-18 residual 2 — DELIVERY TRUTH, not bookkeeping. The
                # live specimen (session 20261021_074100_e9a8eb) parked at
                # 07:41:17 and delivered 2208 chars at 07:41:48 in the SAME
                # turn; the counter said park_turn < current_turn (a duplicate
                # transform pass increments it) and this guard declared
                # `turn_closed_undelivered`, dropping a banner the user's body
                # should have carried. A drop now needs BOTH:
                #   * no body landed (`body_chars == 0`, the same evidence
                #     `sweep_started response_chars` reports, plus this edge's
                #     own text), AND/OR
                #   * the ask moved on (park signature != current signature).
                # Same ask + a body landing = the turn is still delivering:
                # fall through and attach the banner.
                _truth = _act.delivery_truth(
                    session_id,
                    chars_hint=(len(str(text or "").strip())
                                if mode == "append" else 0))
                _body_chars = int(_truth.get("body_chars") or 0)
                _park_sig = str(_rec_guard.get("ask_sig") or "")
                _cur_sig = str(_truth.get("ask_sig") or "")
                _same_ask = bool(_park_sig) and _park_sig == _cur_sig
                _row_present = False
                try:
                    _row_present = bool(
                        self._newest_persisted_assistant_row(session_id))
                except Exception:  # noqa: BLE001 — evidence only
                    _row_present = False
                if _same_ask and _body_chars > 0:
                    # The turn the banner belongs to IS delivering right now.
                    # Retain under the same semaphore record (still exactly
                    # one delivery) and let the chokepoint below attach it.
                    _log_route("POST", event_detail=db.BANNER_PARK_RETAINED_EVENT,
                               reason="same_ask_body_landed", turn_no=_ct,
                               park_turn_no=_pt, body_chars=_body_chars,
                               row_present=_row_present, edge=_e_guard,
                               session_id=str(session_id or ""))
                else:
                    db.consume_parked_banner(session_id)
                    # A drop reason may not outrun its evidence: "undelivered"
                    # is only sayable when nothing landed. With a body in hand
                    # the truthful reason is that the ask moved on.
                    _reason = ("ask_superseded" if _body_chars > 0
                               else "turn_closed_undelivered")
                    _log_route("POST", event_detail=db.BANNER_PARK_DROPPED_EVENT,
                               reason=_reason, turn_no=_ct, park_turn_no=_pt,
                               body_chars=_body_chars,
                               row_present=_row_present, edge=_e_guard,
                               session_id=str(session_id or ""))
                    return (text if isinstance(text, str) else "") \
                        if mode == "append" else ""
        except Exception:  # noqa: BLE001 — the guard never breaks delivery
            pass
        # consume through the debug_banner delegate — the historical patch
        # surface (tests patch debug_banner.consume_parked_banner).
        # R34-17 item 3: read the semaphore record BEFORE the consume pops it.
        # `parked` must mean "a parked banner actually reached this body" —
        # the old event reported bool(consumed_text), so a turn whose consume
        # found nothing (or whose attach never landed) logged parked=True with
        # no banner in the body. A field may not outrun the thing it names.
        _rec_pre: dict = {}
        try:
            _rec_pre = db.park_record(session_id) or {}
        except Exception:  # noqa: BLE001 — record read never breaks delivery
            _rec_pre = {}
        parked = db.consume_parked_banner(session_id)
        base = text if isinstance(text, str) else ""
        if mode == "discard":
            return base
        _e = str(log_edge if log_edge is not None else edge)
        _seq = ({"park_event_seq": int(_rec_pre["event_seq"])}
                if _rec_pre.get("event_seq") else {})
        if not parked:
            if log_consume:
                # Truth: nothing was parked for this turn (anchor lane skipped)
                # vs a slot that existed but no longer had a banner to give.
                _log_route("POST", event_detail="anchor_banner_consume",
                           parked=bool(_rec_pre), delivered=False, edge=_e,
                           reason=("consume_missed" if _rec_pre
                                   else "no_park_this_turn"),
                           session_id=str(session_id or ""), **_seq)
            return (text if isinstance(text, str) else "") if mode == "append" else ""
        try:
            db.note_consumed_decision(session_id, parked)
        except Exception:  # noqa: BLE001
            pass
        if mode == "body":
            out = parked
            landed = True
        else:
            merged = db.append_banner(base, "\n" + parked)
            out = merged if isinstance(merged, str) and merged else str(base or "")
            landed = bool(parked.strip() and parked.strip() in out)
            if not landed:
                # R33f-A (supersedes R19.19 P0 / R19.21 re-park): TTL is ONE
                # delivery — a consumed banner whose attach did not land is
                # DROPPED fail-loud, never re-parked into later turns. The
                # re-park loop was the live stale-leak class (a 19:35 frontier
                # banner leaking onto 4 later turns + double-appending on
                # shadow renders, session 20261006_190903_c82cbbcc).
                try:
                    _log_route("POST", event_detail=db.BANNER_PARK_DROPPED_EVENT,
                               edge=_e, reason="attach_not_landed",
                               session_id=str(session_id or ""))
                except Exception:  # noqa: BLE001 — telemetry never breaks delivery
                    pass
        if log_consume:
            # R34-17 item 3: `parked` reports the PARK (there is a semaphore
            # record for this turn — the thing a park event emits); `delivered`
            # reports whether the banner actually reached the body. The old
            # single bool claimed "parked=True" on turns where nothing was
            # ever parked and no banner appeared.
            _log_route("POST", event_detail="anchor_banner_consume",
                       parked=bool(_rec_pre), delivered=bool(landed), edge=_e,
                       reason=("delivered" if landed
                               else "attach_not_landed" if _rec_pre
                               else "unrecorded_slot"),
                       session_id=str(session_id or ""), **_seq)
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

            from ...core import canonical as _canon

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
                # R34-19f4: DO NOT repair a turn that is still running.
                # Live (analyst …38a356): the PRE consult parked a banner at
                # t+3s, the agent then worked for a minute, the 45s watcher
                # fired mid-turn, found the PREVIOUS turn's assistant row
                # (this turn had persisted nothing yet), appended the banner
                # to THAT row and popped the slot — so the delivery edge later
                # logged `anchor_banner_consume parked=False reason=no_park_
                # this_turn` and the user's message carried no receipt, while
                # `banner_render_captured edge=capture_fallback` made the
                # transcript look fine. The watcher exists to catch a park no
                # edge will ever claim; a park belonging to the OPEN turn is by
                # definition still claimable. Leave it parked.
                _rec_open: dict = {}
                try:
                    _rec_open = db.park_record(sid) or {}
                except Exception:  # noqa: BLE001
                    _rec_open = {}
                _park_turn = int(_rec_open.get("turn_no") or 0)
                if _park_turn:
                    _cur_turn, _cur_sig = 0, ""
                    try:
                        from ... import advisory_consumption as _ac

                        _cur_turn = int(_ac.turn_no(sid) or 0)
                        _cur_sig = str(_ac.open_signal(sid) or "")
                    except Exception:  # noqa: BLE001
                        _cur_turn, _cur_sig = 0, ""
                    _park_sig = str(_rec_open.get("ask_sig") or "")
                    _same_turn = bool(
                        _cur_turn and _cur_turn == _park_turn
                        and (not _park_sig or not _cur_sig
                             or _park_sig == _cur_sig[:40]))
                    if _same_turn:
                        try:
                            logger.warning(
                                "banner_capture_fallback_deferred "
                                "detail=turn_open turn_no=%s session_id=%s — "
                                "banner stays parked for this turn's delivery "
                                "edge", _park_turn, sid)
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            _log_route(
                                "POST",
                                event_detail="banner_capture_fallback_deferred",
                                outcome="turn_open", edge="capture_fallback",
                                turn_no=_park_turn, session_id=sid,
                                **({"park_event_seq": int(_rec_open["event_seq"])}
                                   if _rec_open.get("event_seq") else {}))
                        except Exception:  # noqa: BLE001
                            logger.exception(
                                "banner_capture_fallback event log failed")
                        return
                row = self._newest_persisted_assistant_row(sid)
                delivered = ("%s\n\n%s" % (row, captured)) if row else ""
                ok = False
                if row:
                    try:
                        from ...core import canonical as _canon

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
                    try:
                        db._PARK_RECORDS.pop(sid, None)
                    except Exception:  # noqa: BLE001 — R33f-A record dies too
                        pass
            if ok:
                try:
                    from ... import render_inbox as _ri

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
                # R34-2: no_row_match on the PREFERRED edge (rewrite of the
                # newest persisted assistant row) must NOT destroy the
                # banner. Live: every uncensored-render banner died here —
                # `debug_banner_emitted lane=uncensored-render` fired while
                # zero delivered bodies carried the banner (R33f-A dropped
                # it as banner_park_dropped/no_row_match).
                # The banner stays PARKED under the SAME semaphore record
                # (same event_seq => still exactly ONE delivery) and the next
                # legal delivery edge attaches it to the delivered body.
                # Staleness stays bounded: the deferred slot is expired in
                # deliver() after _DEFER_TTL_S.
                with self._CAPTURE_FB_LOCK:
                    rec = {}
                    try:
                        rec = dict(db.park_record(sid) or {})
                    except Exception:  # noqa: BLE001
                        rec = {}
                    rec["deferred_at"] = _time_wall()
                    try:
                        db._PARK_RECORDS[sid] = rec
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    logger.warning(
                        "banner_capture_fallback_deferred detail=no_row_match "
                        "session_id=%s — banner stays parked (one delivery, "
                        "next delivery edge attaches)", sid)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    _log_route("POST",
                               event_detail="banner_capture_fallback_deferred",
                               outcome="no_row_match", edge="capture_fallback",
                               session_id=sid,
                               **({"park_event_seq": int(rec["event_seq"])}
                                  if rec.get("event_seq") else {}))
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


def _time_wall() -> float:
    import time

    return time.time()


LIFECYCLE = BannerLifecycle()
