"""echo_guard — R34-8c + R34-12 item 7 ENVELOPE-ECHO observability.

A delivered assistant body must be the WORK PRODUCT, never a restatement of
the router's own advisory envelope. Live specimens:

  2026-10-20 20:22 — the delivered turn read
    (The option text for "why_not" was omitted by the author. ...)
  and the turn ENDED there.

  2026-10-20 session api_1792536611_5dbebc6b — a dispatch brief was answered
  with an 8-bullet ANALYSIS OF THE BRIEF, three POVs, and a raw JSON tail
  {"brief_bullets": 8, "povs": [...], "synthesis": ...}. The item-8c guard
  missed it because its precision list only knew banner markers. R34-12
  item 7 adds the structural advisory families (povs / synthesis /
  brief_bullets / practitioner-outsider-skeptic triplet / banner tail with
  no work) so that exact body is caught.

FAMILIES are reported SEPARATELY — one `envelope_echo_delivered` route event
per matched family, so the live log says which family fired.

This module is OBSERVABILITY ONLY: it detects envelope restatement in a
delivered body and reports it as a route event. It NEVER rewrites, trims,
blocks or alters the body — the delivered text is returned untouched by
every caller. Fail-open on any error.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

logger = logging.getLogger(__name__)

EVENT = "envelope_echo_delivered"

# Marker families -> literal marker strings (case-insensitive substring).
FAMILIES = (
    ("advisory_header", ("[ROUTER ADVISORY", "ROUTER ADVISORY — decision lane",
                         "[ROUTER FORK NOTE")),
    ("option_text_placeholder", ("option text for",)),
    ("omission_placeholder", ("omitted by the author",)),
)

# Structural advisory families (R34-12 item 7). Each is a regex over the
# delivered body. `povs`/`synthesis`/`brief_bullets` are the raw-JSON keys of
# the advisory envelope echoed back as the turn body; the triplet is the
# three-stance prose shape.
_STRUCTURAL = (
    ("povs_array", re.compile(r'"povs"\s*:\s*\[', re.I)),
    ("synthesis_key", re.compile(r'"synthesis"\s*:', re.I)),
    ("brief_bullets", re.compile(r'"brief_bullets"\s*:', re.I)),
)
_POV_LABELS = ("practitioner:", "outsider:", "skeptic:")
_ROUTER_TAIL = "· router ·"
# Work evidence: a delivered engineering report carries at least one of these.
# A body that ends with the router banner and carries NONE of them is a
# banner-only delivery (no work product).
_WORK_MARKERS = (
    "pytest", "passed", "failed", "commit", "committed", "git ",
    "def ", "class ", "file", "line ", "test", "build", "diff",
)
_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b", re.I)


def _router_tail_without_work(text: str) -> bool:
    """True when the body's last non-empty line is a router banner tail and
    the body carries no work-product evidence. Never raises."""
    try:
        lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
        if not lines:
            return False
        if not lines[-1].startswith(_ROUTER_TAIL):
            return False
        low = str(text or "").lower()
        if _SHA_RE.search(low):
            return False
        return not any(m in low for m in _WORK_MARKERS)
    except Exception:  # noqa: BLE001
        return False


def detect_envelope_echo_families(text: str) -> List[str]:
    """ALL matched envelope-restatement families, in report order. Structural
    (case-insensitive), never raises. Empty list = clean body."""
    try:
        raw = str(text or "")
        low = raw.lower()
        if not low.strip():
            return []
        out: List[str] = []
        for family, markers in FAMILIES:
            if any(m.lower() in low for m in markers):
                out.append(family)
        for family, rx in _STRUCTURAL:
            if rx.search(raw):
                out.append(family)
        if all(lbl in low for lbl in _POV_LABELS):
            out.append("pov_triplet")
        if _router_tail_without_work(raw):
            out.append("router_tail_no_work")
        return out
    except Exception:  # noqa: BLE001
        return []


def detect_envelope_echo(text: str) -> str:
    """The FIRST matched pattern FAMILY for an envelope-restatement body,
    else "". Kept for back-compat; callers that need every family use
    detect_envelope_echo_families(). Never raises."""
    fams = detect_envelope_echo_families(text)
    return fams[0] if fams else ""


def observe(text: str, session_id: str = "", log_route: Optional[object] = None
            ) -> str:
    """Emit ONE `envelope_echo_delivered` route event PER matched family.
    Returns the FIRST matched family ('' when clean). The body is never
    touched or returned by this function. Never raises."""
    try:
        families = detect_envelope_echo_families(text)
        if not families:
            return ""
        for family in families:
            try:
                if log_route is None:
                    from .core import telemetry as _tlm

                    _tlm.log_route("POST", event_detail=EVENT, pattern=family,
                                   reason=family,
                                   session_id=str(session_id or ""))
                else:
                    log_route("POST", event_detail=EVENT, pattern=family,
                              reason=family,
                              session_id=str(session_id or ""))
            except Exception:  # noqa: BLE001 — observability only
                pass
        return families[0]
    except Exception:  # noqa: BLE001
        return ""
