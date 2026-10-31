"""Event log for the decision lane.

The lane's observability used to live inside the legacy midturn detector, which meant the
replacement could not be shipped without the thing it replaced. Pulled out so the lane stands
on its own: one function, fail-loud to the warning log, never raising into a turn.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("hermes_router.decision_log")

TRIGGER = "midturn"


def log_event(session_id: str, event: str, **fields: Any) -> None:
    """Emit a decision-lane event. Never raises: observability must not break a turn."""
    try:
        from .core.telemetry import log_route as _lr   # import cycle broken on purpose
        # R78U M3.3: an emitter may state its OWN lane (the uncensored assimilation
        # record does: lane="uncensored"). Passing it through to log_route beside
        # this module's default raised "got multiple values for keyword argument
        # 'lane'" and the record never landed — the lane's delivery evidence was
        # silently dropped. The emitter's lane wins; decision stays the default.
        _lane = fields.pop("lane", "") or "decision"
        _lr(event, lane=_lane, trigger=TRIGGER,
            session_id=str(session_id or ""), **fields)
    except Exception as exc:  # noqa: BLE001
        try:
            logger.warning("route_event_emit_failed where=decision_log event=%s err=%r",
                           event, exc)
        except Exception:  # noqa: BLE001
            pass


_log = log_event


def plugin_db_path() -> str:
    """Plugin state DB path: hermes_home/hermes_router_state.db. '' on miss.

    Lifted out of the retired decision_miner, which was the only owner of this path while the
    ledger lived there.
    """
    try:
        import hermes_constants

        return str(hermes_constants.get_hermes_home() / "hermes_router_state.db")
    except Exception:  # noqa: BLE001
        return ""
