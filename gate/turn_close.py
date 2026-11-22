"""M35-04 — the turn-CLOSE edge: a turn that never reached the POST seam.

THE DEFECT
    Two of ten live turns (M35 battery rows A2 and C1) produced NO provenance
    row. The host had skipped the ``transform_llm_output`` invocation after a
    slow/overlapping one, so the row — written from that hook's ``finally`` —
    was never written, and the turn could not be attributed at all. One of them
    also shipped the router's own ``<turn declined=... />`` marker inside the
    user's answer, because the strip lived in the same skipped pass.

WHAT THIS EDGE IS
    ``post_llm_call`` is the finalizer's observer AFTER the transform hook: it
    receives the body that is about to be delivered. It runs even when the
    transform hook was skipped, which makes it the right place to

      (1) CLOSE the turn the PRE edge armed — ``action="skipped"`` with the
          named reason ``post_seam_not_reached`` — so the turn is attributable
          instead of absent, and
      (2) CATCH a marker that reached the delivered body (the skipped-seam
          leak): log it LOUDLY as an error and record ``marker_leak_delivered``
          on the row, so a leak is a measured event, never a silent one.

    It is a pure observer: it changes neither the body nor the routing, it adds
    no call, and it costs one dict pop per turn. Never raises.
"""
from __future__ import annotations

from typing import Any


def on_post_llm_call(*, session_id: str = "", assistant_response: str = "",
                     turn_id: str = "", **context: Any) -> None:
    """Close the turn at the finalizer's last observer edge. Never raises."""
    try:
        from ..core import turn_provenance as _prov

        sid = str(session_id or "")
        if not sid:
            return
        body = str(assistant_response or "")
        # M53-03 item 2: a turn the transform edge never saw still DECIDES.
        # This observer is the only edge a complexity-skipped turn reaches, so
        # the ONE decision path runs here (log-only; the body is not touched)
        # and no-ops when the transform edge already decided this turn.
        try:
            from . import registration as _reg_close
            _reg_close.decide_skipped_turn(
                session_id=sid, body=body, turn_id=str(turn_id or ""))
        except Exception:  # noqa: BLE001 — the decision never blocks the close
            pass
        # M53-28b: the gate row is TURN-SCOPED state, so close it with the turn.
        # The only production clear was TTL expiry (900s), and every POST read
        # calls read(sid) WITHOUT the ask, so ask_sig is never enforced - a row
        # written at POST (the M53-28 fallback) could be read by a LATER turn
        # whose PRE hook did not run, letting one turn's detection answer
        # another. Cleared after the skip-decision so a complexity-skipped turn
        # still sees its own row.
        try:
            from ..core import state as _st_close

            _st_close.clear_pre_domain(sid)
        except Exception:  # noqa: BLE001 — the close edge never breaks a turn
            pass
        marker = ""
        try:
            from ..features.detection import declaration as _decl

            _clean, marker = _decl.scrub_markers(body)
        except Exception:  # noqa: BLE001 — a scan error is not a leak verdict
            marker = ""
        if marker:
            _leak(sid, body, marker)
            _prov.close_skipped(
                sid, reason=_prov.SKIP_REASON_POST_SEAM_NOT_REACHED,
                delivered=body, outcome="marker_leak_delivered")
            return
        _prov.close_skipped(sid,
                            reason=_prov.SKIP_REASON_POST_SEAM_NOT_REACHED,
                            delivered=body)
    except Exception:  # noqa: BLE001 — the close edge never breaks a turn
        return


def _leak(session_id: str, body: str, marker: str) -> None:
    """Record a marker that reached the delivered body — LOUDLY, and repair
    the transcript so the stored turn stops carrying what the wire carried."""
    try:
        import logging

        logging.getLogger(__name__).error(
            "router marker leaked into a delivered body: session=%s marker=%r "
            "chars=%d — the POST seam was skipped, the body could not be "
            "sanitized at the boundary", session_id, marker, len(body))
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..core import canonical as _can
        from ..features.detection import declaration as _decl

        clean, _m = _decl.scrub_markers(body)
        if clean != body:
            _can.rewrite_persisted_turn(session_id, body, clean)
    except Exception:  # noqa: BLE001 — the transcript repair never breaks a turn
        pass
