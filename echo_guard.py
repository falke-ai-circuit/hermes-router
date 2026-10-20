"""echo_guard — R34-8c ENVELOPE-ECHO observability.

A delivered assistant body must be the WORK PRODUCT, never a restatement of
the router's own advisory envelope. Live specimen (2026-10-20 20:22): the
delivered turn read

    (The option text for "why_not" was omitted by the author. The option
     existed in the decision an...

and the turn ENDED there.

This module is OBSERVABILITY ONLY: it detects envelope restatement in a
delivered body and reports it as a route event. It NEVER rewrites, trims,
blocks or alters the body — the delivered text is returned untouched by
every caller. Fail-open on any error.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

EVENT = "envelope_echo_delivered"

# Pattern families -> marker strings. Each family is reported once per body.
FAMILIES = (
    ("advisory_header", ("[ROUTER ADVISORY", "ROUTER ADVISORY — decision lane",
                         "[ROUTER FORK NOTE")),
    ("option_text_placeholder", ("option text for",)),
    ("omission_placeholder", ("omitted by the author",)),
)


def detect_envelope_echo(text: str) -> str:
    """The matched pattern FAMILY for an envelope-restatement body, else "".
    Case-insensitive, structural, never raises."""
    try:
        low = str(text or "").lower()
        if not low.strip():
            return ""
        for family, markers in FAMILIES:
            for m in markers:
                if m.lower() in low:
                    return family
        return ""
    except Exception:  # noqa: BLE001
        return ""


def observe(text: str, session_id: str = "", log_route: Optional[object] = None
            ) -> str:
    """Emit `envelope_echo_delivered` when the delivered body restates the
    envelope. Returns the MATCHED FAMILY ('' when clean). The body is never
    touched or returned by this function. Never raises."""
    try:
        family = detect_envelope_echo(text)
        if not family:
            return ""
        try:
            if log_route is None:
                from .core import telemetry as _tlm

                _tlm.log_route("POST", event_detail=EVENT, pattern=family,
                               session_id=str(session_id or ""))
            else:
                log_route("POST", event_detail=EVENT, pattern=family,
                          session_id=str(session_id or ""))
        except Exception:  # noqa: BLE001 — observability only
            pass
        return family
    except Exception:  # noqa: BLE001
        return ""
