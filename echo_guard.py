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


EVENT_BLOCKED = "advisory_echo_blocked"
EVENT_STRIPPED = "advisory_echo_stripped"
EVENT_WITHHELD = "advisory_echo_withheld"

# R34-17 item 2 — TRUTH IN THE NOTICE. Whatever path produces a user-facing
# notice, the notice may not assert a fact this code did not check.
#
# HAND_BACK_TEXT is only delivered on the NO-WORK path, where the work-signal
# predicate WAS evaluated (`reason=no_work_signal`); on that path the claim
# "nothing was executed" is a checked fact, not a guess.
HAND_BACK_TEXT = (
    "[router] turn ended on the router's own advisory echo, not work product. "
    "No tool activity or file write was observed this turn, so nothing was "
    "executed. Re-send the ask to run this turn again."
)

# WITHHELD_TEXT is the fallback when work DID happen but the echo could not
# be separated from the report. It claims nothing about execution — only what
# the guard knows: the body was withheld and the log names the reason.
WITHHELD_TEXT = (
    "[router] this turn's body was withheld: it was the router's own advisory "
    "echo and the work report could not be separated from it. This notice "
    "says nothing about whether work ran — see the route log "
    "(advisory_echo_withheld, reason=strip_failed). Re-send the ask if you "
    "need the report."
)

# A stripped remainder must still look like a report; below this many
# non-space characters the strip is treated as a failure (and the truthful
# WITHHELD_TEXT is used instead of shipping a fragment).
MIN_REPORT_CHARS = 24

# --- stripping the echoed advisory out of a work turn ---------------------
# The shapes we remove, in the order the envelope appends them:
#   1. a raw advisory JSON tail  {"povs": [...]} / {"brief_bullets": ...}
#   2. a POV / synthesis prose section ("POVs.", "Practitioner: ...")
#   3. the §10.2 router banner tail line(s)  "· router · ..."
#   4. an echoed advisory header block  "[ROUTER ADVISORY ..." / "[ROUTER FORK NOTE"
# Markers only — the detection families are NOT widened (R34-17 constraint).
_JSON_TAIL_RE = re.compile(
    r'\{\s*"(?:povs|synthesis|brief_bullets)"\s*:', re.I)
_POV_SECTION_RE = re.compile(
    r"(?im)^[ \t>*\-]*(?:povs?[ \t]*[:.\u2014-]|povs?[ \t]*$|\*\*povs?\*\*|"
    r"povs\s+and\s+synthesis)")
_SYNTH_RE = re.compile(r"(?im)^[ \t>*\-]*synthesis\b")
_POV_LABEL_RE = re.compile(r"(?im)^[ \t>*\-]*(?:practitioner|outsider|skeptic)\s*:")
_HEADER_MARKERS = ("[router advisory", "router advisory \u2014 decision lane",
                   "[router fork note")


def strip_advisory_echo(text: str) -> "tuple[str, int]":
    """Remove the echoed advisory block/tail from a body, keeping the agent's
    own deliverable report. Returns (body, chars_removed). Never raises.

    Deliberately conservative: it cuts a TAIL (JSON tail / POV section /
    synthesis / router banner) and drops standalone header blocks and POV
    label lines. It never re-orders or rewrites the remaining prose."""
    try:
        raw = str(text or "")
        if not raw.strip():
            return "", 0
        cuts = []
        for rx in (_JSON_TAIL_RE, _POV_SECTION_RE, _SYNTH_RE):
            m = rx.search(raw)
            if m:
                cuts.append(m.start())
        # router banner tail: the last line that opens with the tail marker
        for m in re.finditer(r"(?m)^[ \t]*\u00b7 router \u00b7.*$", raw):
            cuts.append(m.start())
        body = raw[:min(cuts)] if cuts else raw
        # drop standalone POV label lines and advisory header blocks
        keep = []
        skipping_header = False
        for line in body.splitlines():
            low = line.strip().lower()
            if any(mk in low for mk in _HEADER_MARKERS):
                skipping_header = True
                continue
            if skipping_header:
                if not low:
                    skipping_header = False
                continue
            if _POV_LABEL_RE.match(line):
                continue
            keep.append(line)
        out = "\n".join(keep)
        out = re.sub(r"\n{3,}", "\n\n", out).strip()
        return out, max(0, len(raw) - len(out))
    except Exception:  # noqa: BLE001
        return "", 0


class EchoResult:
    """Outcome of the execution-echo guard.

    action: "strip_keep_work" (body is the CLEANED WORK REPORT — deliver it and
            keep running the delivery seam so the banner still attaches),
            "handback_failure" (no work signal — the body is the hand-back
            notice), or "withhold_echo" (work happened, strip failed — the body
            is the truthful withheld notice).
    """

    __slots__ = ("action", "body", "families", "chars_removed", "work")

    def __init__(self, action: str, body: str, families=None,
                 chars_removed: int = 0, work=None):
        self.action = action
        self.body = body
        self.families = list(families or [])
        self.chars_removed = int(chars_removed or 0)
        self.work = dict(work or {})

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"EchoResult(action={self.action!r}, "
                f"removed={self.chars_removed}, "
                f"families={'|'.join(self.families)})")


def _route(log_route, event: str, **fields) -> None:
    try:
        if log_route is None:
            from .core import telemetry as _tlm

            _tlm.log_route("POST", event_detail=event, **fields)
        else:
            log_route("POST", event_detail=event, **fields)
    except Exception:  # noqa: BLE001 — telemetry never blocks the guard
        pass


def enforce_execution_echo(text: str, session_id: str = "",
                           frame: str = "",
                           log_route: Optional[object] = None
                           ) -> "Optional[EchoResult]":
    """R34-14 item 7 + R34-17 EXECUTION_ECHO enforcement.

    When the envelope-echo guard fires AND the turn's frame resolves to
    `execution`, the echoed body must NOT be the delivered answer — but the
    decision now depends on WHAT THE TURN DID, never on body shape alone:

      * WORK turn (a terminal ran or a write/edit tool was called):
        strip the echoed advisory and keep the agent's own report
        (`advisory_echo_stripped action=strip_keep_work`). Blocking a turn
        that wrote files and passed tests is worse than the bug being fixed,
        and a guard may not report work as un-done.
      * NO work signal: block the close with the hand-back notice
        (`advisory_echo_blocked action=handback_failure
        reason=no_work_signal`) — the only path where "nothing was executed"
        is a checked fact.
      * WORK turn where the strip cannot produce a report: withhold the body
        with a notice that claims nothing about execution
        (`advisory_echo_withheld action=withhold_echo reason=strip_failed`).

    Returns an EchoResult, or None to leave the body alone. Non-execution
    frames keep the log-only contract. Never raises — a guard failure must
    never break delivery.
    """
    try:
        families = detect_envelope_echo_families(text)
        if not families:
            return None
        try:
            _reg = str(frame or "").strip().lower()
            if not _reg:
                from . import decision_midturn as _dmt

                _reg = str(_dmt._frame_register() or "").strip().lower()
        except Exception:  # noqa: BLE001 — unresolved frame = not execution
            _reg = ""
        if _reg != "execution":
            return None
        chars = len(str(text or ""))
        try:
            from . import work_signal as _ws

            work = _ws.work_facts(session_id)
        except Exception:  # noqa: BLE001 — no verdict = no work claim
            work = {"has_work": False, "predicate": "unavailable"}
        base = {
            "chars": chars,
            "families": "|".join(families),
            "frame": _reg,
            "session_id": str(session_id or ""),
            "work_signal": str(work.get("predicate") or ""),
            "tools": int(work.get("tools") or 0),
            "writes": int(work.get("writes") or 0),
            "terminal": int(work.get("terminal") or 0),
        }
        if bool(work.get("has_work")):
            stripped, removed = strip_advisory_echo(text)
            if len(stripped.strip()) >= MIN_REPORT_CHARS:
                _route(log_route, EVENT_STRIPPED, action="strip_keep_work",
                       chars_removed=removed, **base)
                return EchoResult("strip_keep_work", stripped, families,
                                  removed, work)
            _route(log_route, EVENT_WITHHELD, action="withhold_echo",
                   reason="strip_failed", **base)
            return EchoResult("withhold_echo", WITHHELD_TEXT, families,
                              chars - len(stripped), work)
        _route(log_route, EVENT_BLOCKED, action="handback_failure",
               reason="no_work_signal", **base)
        return EchoResult("handback_failure", HAND_BACK_TEXT, families,
                          chars, work)
    except Exception:  # noqa: BLE001
        return None


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
