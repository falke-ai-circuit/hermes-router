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

import json
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

# R34-18 residual 1 — the shape the live echo ACTUALLY takes: a fenced JSON
# block whose top level is the agent's own report object with the advisory
# keys appended as siblings, e.g.
#
#     ```json
#     {"report": "...", "files": [...], "pytest": "5 passed",
#      "povs": [{"lens": "algorithms", "stance": "...", "argument": "..."}]}
#     ```
#
# The pre-R34-18 tail regex only matched an object that STARTED with an
# advisory key, so `chars_removed=0` while `families=povs_array` fired and the
# whole block shipped to the user. Detection unwidened (R34-17 constraint):
# only the STRIP learns the new shape.
_FENCED_BLOCK_RE = re.compile(r"```[ \t]*[A-Za-z0-9_+.-]*[ \t]*\r?\n(.*?)```",
                              re.S)
# Top-level keys that only the router's advisory envelope produces. A block
# carrying any of them is an echoed envelope, however legitimate its sibling
# keys (report/download/files/pytest) look.
_ADVISORY_JSON_KEYS = ("povs", "synthesis", "advisory", "verdict_of_record",
                       "families", "brief_bullets",
                       # R34-19f: the shapes the higher-self lanes actually
                       # append. Live fleet sweep, 10/10 agents: a RAW
                       # (unfenced) object tailed to the answer carrying
                       # {"adversarial": …}, {"p_failure": …},
                       # {"strongest_objection": …}, {"failure_mode": …} or
                       # {"sense_check": …}. The old set only knew the
                       # "povs"-first shape, so the guard fired on the family
                       # but the strip removed nothing it could see.
                       "adversarial", "p_failure", "strongest_objection",
                       "failure_mode", "sense_check")
# Last-resort scrub tokens (only used when a family fired and nothing else
# was removable): the same keys plus the POV object fields and stance labels.
_SCRUB_TOKENS = ('"povs"', '"synthesis"', '"brief_bullets"',
                 '"verdict_of_record"', '"advisory"', '"families"',
                 '"lens"', '"stance"', '"argument"',
                 "practitioner:", "outsider:", "skeptic:")


def _advisory_json_object(block: str):
    """Parsed dict for a fenced block that is an advisory envelope, else None.

    Never raises. A block that is not JSON, or is JSON without an
    advisory-only top-level key, is left to the caller untouched.
    """
    try:
        obj = json.loads(str(block or "").strip())
    except Exception:  # noqa: BLE001 — not JSON: not ours to cut
        return None
    if not isinstance(obj, dict):
        return None
    keys = {str(k).lower() for k in obj.keys()}
    return obj if keys.intersection(_ADVISORY_JSON_KEYS) else None


def _report_value(obj) -> str:
    """The human-readable report carried INSIDE an advisory JSON object."""
    for k in ("report", "summary", "answer", "message"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""



# R34-19f — two leak shapes the tail regexes miss, both seen live.
#
# (a) RAW advisory JSON appended to the answer, with no fence and no
#     advisory key first:  …traffic burns.", "p_failure": 0.72},
#     "povs": [{"stance": …}]}   The regex tail only matched an object that
#     STARTED with an advisory key, so the block shipped to the user.
# (b) The higher-self ENVELOPE itself: "[HIGHER-SELF COMPLETION-AUDIT TURN |
#     FRONTIER-DERIVED | PROVENANCE-LOGGED]", "[HIGHER-SELF MESSAGE …]" and the
#     operating line "SEAM INSTRUCTION: the audit is complete - now PROCEED".
#     On reviewer this put 4,750 chars of internal instruction text inside the
#     delivered answer.
#
# STRIP ONLY. Detection families are deliberately NOT widened (R34-17).
_ENVELOPE_TAIL_RE = re.compile(
    r"(?im)^[ \t>*\-]*(?:\[higher-self|higher-self completion-audit turn|"
    r"higher-self message|seam instruction[:\u2014-]|.*provenance-logged)")


def _advisory_json_starts(text: str) -> List[int]:
    """Start offsets of RAW json objects in `text` that carry advisory keys.

    Never raises. Scans every '{' and keeps the offsets whose object parses and
    holds an advisory-only key; nested objects are skipped (their start is
    inside a larger match, and the larger start wins by being earlier)."""
    out: List[int] = []
    try:
        body = str(text or "")
        dec = json.JSONDecoder()
        i = body.find("{")
        while i != -1:
            try:
                obj, _end = dec.raw_decode(body[i:])
            except Exception:  # noqa: BLE001 — not JSON from here
                obj = None
            if isinstance(obj, dict):
                keys = {str(k).lower() for k in obj.keys()}
                if keys.intersection(_ADVISORY_JSON_KEYS):
                    out.append(i)
            i = body.find("{", i + 1)
    except Exception:  # noqa: BLE001
        return out
    return out


def strip_advisory_echo(text: str) -> "tuple[str, int]":
    """Remove the echoed advisory block/tail from a body, keeping the agent's
    own deliverable report. Returns (body, chars_removed). Never raises.

    Deliberately conservative: it cuts a TAIL (JSON tail / POV section /
    synthesis / router banner), drops standalone header blocks and POV label
    lines, AND (R34-18) removes a fenced JSON block whose parsed object carries
    any advisory-only top-level key. It never re-orders or rewrites the
    remaining prose.

    Mixed blocks (the live shape): a fenced object that carries the agent's
    own `report`/`files`/`pytest` keys AND advisory keys (`povs`,
    `synthesis`, `advisory`, `verdict_of_record`, `families`,
    `brief_bullets`) is dropped WHOLE when human-readable prose already stands
    above the fence; when the report exists only inside the JSON, its `report`
    value is rendered as plain text in the block's place. No dangling fence is
    ever left behind. `chars_removed` is non-zero whenever an advisory family
    was detected (the guard asserts that relationship)."""
    try:
        raw = str(text or "")
        if not raw.strip():
            return "", 0
        # --- residual 1: fenced advisory JSON blocks (mixed-key shape) -----
        body_text = raw
        for m in reversed(list(_FENCED_BLOCK_RE.finditer(raw))):
            obj = _advisory_json_object(m.group(1))
            if obj is None:
                continue
            head = body_text[:m.start()]
            tail = body_text[m.end():]
            # Human-readable prose already standing OUTSIDE the block? Keep it
            # and drop the fence whole. Only when the report lives inside the
            # JSON does its `report` value take the block's place.
            prose = re.sub(r"```.*?```", "", head, flags=re.S).strip()
            prose_below = re.sub(r"```.*?```", "", tail, flags=re.S).strip()
            repl = ""
            if len(prose) < MIN_REPORT_CHARS and len(prose_below) < MIN_REPORT_CHARS:
                repl = _report_value(obj)
            body_text = head + (("\n\n" + repl) if repl else "") + tail
        # --- R34-19f2: the banner is NOT the echo -------------------------
        # The strip exists to remove what the router said to the AGENT. The
        # `· router ·` line is what the user is supposed to see: it is the
        # receipt for a billed consult. Cutting it here made banner presence
        # depend on the delivery seam re-attaching it, and live the seam does
        # not always fire (a turn with no audit edge keeps the stripped body),
        # so a routed turn could deliver WITHOUT its banner. Pin the banner tail
        # aside, sanitise the head, put the banner back.
        _banner_tail = ""
        _bm = re.search(r"(?m)^[ \t]*\u00b7 router \u00b7", body_text)
        if _bm and _bm.start() > 0:
            _banner_tail = body_text[_bm.start():]
            body_text = body_text[: _bm.start()]

        # --- the pre-existing tail cuts -----------------------------------
        cuts = []
        for rx in (_JSON_TAIL_RE, _POV_SECTION_RE, _SYNTH_RE):
            m = rx.search(body_text)
            if m:
                cuts.append(m.start())
        # (banner already split aside above; any inline one is still a cut)
        # R34-19f: raw advisory JSON objects and the higher-self envelope are
        # TAILS in every observed shape, so their earliest start is a cut point
        # alongside the banner/POV cuts above.
        cuts.extend(_advisory_json_starts(body_text))
        _env = _ENVELOPE_TAIL_RE.search(body_text)
        if _env:
            cuts.append(_env.start())
        body = body_text[:min(cuts)] if cuts else body_text
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
        # Never leave a DANGLING fence: when an advisory block was cut the
        # remaining fence markers must pair up. Balanced fences (a genuine
        # code sample in the report) are left exactly as written.
        _fence_idx = [i for i, ln in enumerate(out.splitlines())
                      if ln.strip().startswith("```")]
        if len(_fence_idx) % 2 == 1:
            lines = out.splitlines()
            del lines[_fence_idx[-1]]
            out = "\n".join(lines)
        out = re.sub(r"\n{3,}", "\n\n", out).strip()
        if _banner_tail:
            out = (out + "\n\n" + _banner_tail.strip()).strip()
        # --- the invariant: a detected family must yield a real removal ---
        if len(out) >= len(raw):
            scrubbed = [ln for ln in out.splitlines()
                        if not any(tok in ln.lower() for tok in _SCRUB_TOKENS)]
            cand = "\n".join(scrubbed).strip()
            if len(cand) < len(raw):
                out = cand
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
        try:
            _reg = str(frame or "").strip().lower()
            if not _reg:
                from . import decision_midturn as _dmt

                _reg = str(_dmt._frame_register() or "").strip().lower()
        except Exception:  # noqa: BLE001 — unresolved frame = not execution
            _reg = ""
        _families_pre = detect_envelope_echo_families(text)
        if _reg != "execution":
            # R34-19f: a NON-execution frame is never handed back and never
            # withheld — but it is not licence to ship the router's own
            # advisory to the user either. Live fleet sweep, 2026-10-21: a plain
            # question ("ask your higher-self: …") has no work signal, so the
            # frame is not `execution`, and the old contract returned None here
            # (log-only) — the `{"adversarial": …, "povs": […]}` tail and, on
            # reviewer, the whole higher-self envelope with its
            # "SEAM INSTRUCTION: … now PROCEED" reached the user. Stripping is
            # not an execution decision; it is the delivery boundary.
            stripped, removed = strip_advisory_echo(text)
            if removed > 0 and len(stripped.strip()) >= MIN_REPORT_CHARS:
                _route(log_route, "advisory_tail_stripped",
                       action="strip_keep_work", frame=_reg,
                       chars_removed=removed,
                       families="|".join(_families_pre) or "none",
                       session_id=str(session_id or ""))
                return EchoResult("strip_keep_work", stripped, _families_pre,
                                  removed, {})
            # Nothing removable: leave the body exactly as written (log-only,
            # as before) and say so, so an unstrippable echo is visible.
            _route(log_route, "advisory_tail_unstrippable", action="log_only",
                   frame=_reg, families="|".join(_families_pre) or "none",
                   session_id=str(session_id or ""))
            return None
        families = _families_pre
        if not families:
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
            # R34-18 residual 1: `advisory_echo_stripped` may only fire when
            # the strip REMOVED something. The live defect was a family
            # detected (`families=povs_array`) while the strip returned the
            # body untouched (`chars_removed=0`) — the event then claimed a
            # strip that never happened and the echo shipped. Non-zero removal
            # is a precondition, not a statistic.
            if len(stripped.strip()) >= MIN_REPORT_CHARS and removed > 0:
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
