"""Completion-audit arm (Goran-direct 2026-09-08): the ONLY automatic frontier
touchpoint.

Ruling (verbatim intent): "I always ask consultance at the finished job, never
at start... frontier catches the response and consults it based on original
request and work done, like a user-response before the user response... remove
completely PRE and MID... limit this to once per task (if triggered before the
response, it should not re-trigger if you decide to fix and respond again)."

Model:
  - PRE (task-start regex consult): REMOVED (dispatch pre_mode semantics).
  - MID (struggle escalation): REMOVED as auto-fire.
  - POST completion audit: after the FINAL response to the user is produced,
    one bounded frontier consult reviews (original ask + work digest + final
    response). The verdict is stashed and delivered to the agent on the NEXT
    turn as an advisory envelope; she surfaces it for user decision or fixes
    and produces the final response.
  - Modes: complexity.audit_mode: off | complex | always.
      off      — never auto-consult (manual "anchor this" unaffected)
      complex  — audit only when the original ask is complexity-shaped
                 (stage1 signals, same buckets as the old PRE lane)
      always   — audit every substantial final response
  - ONCE PER TASK: fire-marker keyed by task_id (session+ask hash). Fix-then-
    respond-again loops carry the SAME task_id → no re-trigger. A new ask =
    new task_id = new audit.
  - Async: the consult runs in a daemon thread; delivery of the current turn
    is NEVER delayed. Fail-open everywhere.

The frontier consultant receives a MICRO-REVIEW frame: original ask, compact
work digest (tool-cycle fingerprints), the final response, and asked to flag
ONLY material oversights/errors — not style.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import anchor_chain, anchor_exec, router_core, state
from .frames import HS_COMPLETION_AUDIT_MARKER as _HS_AUDIT_MARKER  # R8h single source

# R20-D3 (rider 20): hard cap for the sync audit consult (and the pre-consume
# decision/anchor waits) inside on_transform_llm_output. The host plugin
# runner kills the hook callback at 30s ("timed out after 30s — skipping")
# and then skips subsequent invocations ("skipped after previous timeout or
# while still running") — a skip leaves a billed consult's parked banner
# with NO consume edge (park-without-capture: live valmet D3 ledger rows
# 148+149 billed, banner parked 06:03:37Z, zero consume events). Cap below
# the runner budget, leaving margin for consume/capture work after the
# consult returns.
HOOK_SYNC_CAP = 25.0

logger = logging.getLogger(__name__)
try:  # R34-19k: which COPY is live? deploy ambiguity cost hours.
    logger.info("completion_audit_loaded_from %s", __file__)
except Exception:  # noqa: BLE001
    pass

_MIN_RESPONSE_CHARS = 500
# trailing "\n" preserved: _NOTE_MARKER is concatenated with _NOTE_PREFIX at
# the note-assembly site (pre-R8h contract).
_NOTE_MARKER = _HS_AUDIT_MARKER + "\n"
# R34-19 (Goran-direct): the visible one-line confirmation of a PAIRED turn —
# "your higher self checked and it holds". Never a verdict dump (that is the
# L2 note), never claimed for an unpaired turn, never a quote of the brief.
HS_CONFIRMATION_LINE = ("\u00b7 higher-self: checked at close \u2014 holds, "
                        "nothing to correct \u00b7")
# R34-19f — the other two truthful outcomes of a paired POST. Before this, a
# paired turn whose audit DID find something said nothing at all: the note was
# produced (`audit_gate_returned returned=text`), the revision pass had no
# budget left after the consult (`revised=False`), and the only place the note
# ever appeared was the L2 envelope dump — i.e. the user saw the raw audit text
# or nothing, never a confirmation. One line, no note substance, in all three
# cases.
HS_CONFIRMATION_ADJUSTED = ("\u00b7 higher-self: checked at close \u2014 adjusted "
                            "before delivery \u00b7")
# R34-19g: "flagged, see the route log" told the reader nothing and pointed her
# at a file. A confirmation that names the thing it found is the whole point of
# confirming — otherwise the lane bills a consult and reports a mood.
HS_CONFIRMATION_FLAGGED = ("· higher-self: checked at close — flagged: "
                           "{gist} ·")
HS_CONFIRMATION_FLAGGED_BARE = ("· higher-self: checked at close — "
                                "flagged, nothing quotable ·")
# R42 Unit 3 (internal voice): the advisory text is the agent's own higher self
# reporting on her OWN state — never an outside system reporting on a model.
# No model/provider/endpoint/token vocabulary may enter it. Identity stays in
# the operator-facing banner (billing/provenance) and nowhere in this text.
_NOTE_PREFIX = "[HIGHER-SELF MESSAGE — from your higher self, at the close of the work]"
_NOTE_TTL = 3600.0  # pending verdict lives 1h
_AUDIT_MARKER_TTL = 6 * 3600.0  # once-per-task ledger TTL

_PENDING: Dict[str, Tuple[float, str]] = {}
_PENDING_LOCK = threading.Lock()
_FIRED: Dict[str, float] = {}
_FIRED_LOCK = threading.Lock()
_INFLIGHT: set = set()
_INFLIGHT_LOCK = threading.Lock()
# deep-consult fix: keys whose join deadline passed while the worker was
# still consulting — the worker checks this and DISCARDS its late verdict.
# (retired 2026-09-10 — Goran: slow is NOT failed; timed-out sync consults now
# downgrade to async next-turn delivery instead of being discarded)
_LATE_TIMEOUT: set = set()

# deep-consult fix (audit-of-audit recursion): text markers that must never
# enter a consult payload as agent content — if flash's response/digest
# carries prior verdict text or spend banners, it's excluded from the digest.
_AUDIT_ARTIFACT_MARKERS = (
    "HIGHER-SELF COMPLETION REFLECTION",
    "HIGHER-SELF ORIENTATION TURN",
    "router · frontier",
    "debug_banner",
)

# Revision-pass budget: the in-hook flash re-call gets its own bounded slice
# (config complexity.audit_revision_seconds, default 60, clamp 0-180; 0
# disables the revision pass → verdict delivered as envelope only).
# Default raised 20→60 (live-caught: frontier glm-5.3 reasoning=max needs
# >30s on real revision payloads — timeouts masqueraded as reason=empty
# until the error was surfaced).
_ON_DEMAND_TRIGGERS = ("request_routing_declared", "declared_user")


def post_pair_mode() -> str:
    """DEPRECATED (R42) — pairing is no longer a fire gate.

    Kept parsed only for config compatibility (R34-19h shipped it, and
    deployed profiles still carry it). POST fires on realised WORK MASS
    (see work_mass_gate); this knob now controls ONE thing: whether a PRE
    advisory that happens to exist is included in the POST payload as
    context. `off` therefore means "no pairing context", NOT "no POST".
    `always` and `worthwhile` are equivalent (both include the context).
    Never raises.
    """
    try:
        from .router_core import _complexity_cfg

        raw = (_complexity_cfg() or {}).get("post_pair_mode")
        # YAML 1.1 parses a bare `off:` value as False and `always`-shaped
        # truthiness as True — accept both spellings rather than silently
        # falling back to the default (live-caught while verifying this gate).
        if raw is True:
            return "always"
        if raw is False:
            return "off"
        v = str(raw or "worthwhile").strip().lower()
        return v if v in ("worthwhile", "always", "off") else "worthwhile"
    except Exception:  # noqa: BLE001
        return "worthwhile"


def _pair_worth_confirming(ask: str, response_text: str,
                           pair: Dict[str, Any]) -> Tuple[bool, str]:
    """RETIRED AS A FIRE GATE (R42).

    R34-19h used this to decide whether a paired turn "earned" a POST. That
    chain produced `pair_deferred_mid_work` on most paired turns and
    `higher_self_confirmation_emitted` zero times fleet-wide — a deferral
    with no landing is a silent drop. POST now fires on realised WORK MASS
    (work_mass_gate) with no reference to any PRE; this function is kept
    only as a pure predicate for callers/tests that still ask the question.

    Never raises; on doubt the pair IS confirmed (fail-open to the old arm).
    """
    try:
        trig = str(pair.get("trigger") or "")
        if trig in _ON_DEMAND_TRIGGERS:
            return True, "on_demand"
        try:
            if is_closure_response(ask, response_text):
                return True, "closure"
        except Exception:  # noqa: BLE001
            pass
        try:
            from .decision import (
                _manual_trigger_in_text as _mt,
                has_declared_fork_structure as _df,
            )

            if _mt(ask) or _df(ask):
                return True, "decision_frame"
        except Exception:  # noqa: BLE001
            pass
        return False, "mid_work"
    except Exception:  # noqa: BLE001
        return True, "fail_open"


# ------------------------------------------------------- work-mass (R42) ----
# POST fires on REALISED WORK, never on pairing. The two gates read different
# things: PRE reads the ASK (will this be complex/impactful/long?), POST reads
# the WORK (was it, and does it hold up?). Measured over 3336 sessions, the
# ask-side classifier fires on 40% of sessions and still misses 58% of the
# top-20% work-mass band — acceptable ONLY because this gate catches it on
# realised work. Do not add recall hacks to PRE to compensate.
#
# Counts come from the outbound request payload (the session's own message
# list), so "tools" is the realised tool traffic at this seam, not a guess.

# A MUTATION is a file content change: write_file or patch (the brief's
# definition). Bare edits/other write tools count toward files when they carry
# a path, but the mutation floor is deliberately these two.
_MUTATION_TOOLS = frozenset({"write_file", "patch"})
# Path-bearing write tools (superset) — used for the distinct-file count.
_PATH_WRITE_TOOLS = frozenset({
    "write_file", "patch", "write", "create_file", "append_file", "edit_file",
    "edit", "apply_patch", "apply_diff", "multi_edit", "str_replace",
    "str_replace_editor", "replace_in_file", "insert_content", "notebook_edit",
})
_PATH_KEYS = ("path", "file_path", "filepath", "filename", "notebook_path")
# Once a session has been reflected on for work mass, it does not pay again
# for the same body of work (a 50-tool session stays past its threshold on
# every later turn — without this the fix trades starvation for a consult per
# turn). Re-arms only after the marker TTL.
_REFLECTED: Dict[str, float] = {}
_REFLECTED_LOCK = threading.Lock()


def work_mass_min_tools() -> int:
    """complexity.work_mass_min_tools — tools needed to fire on mass.

    Default 40 (fleet p80 is role-dependent; the per-role table ships in the
    INSTALL docs, never hardcoded here — the plugin must not know profile
    names). Clamp 10..500. Never raises.
    """
    try:
        from .router_core import _complexity_cfg

        v = int((_complexity_cfg() or {}).get("work_mass_min_tools") or 40)
        return max(10, min(500, v))
    except Exception:  # noqa: BLE001
        return 40


def work_impact_min_files() -> int:
    """complexity.work_impact_min_files — distinct patched files (default 5,
    clamp 1..100). Never raises."""
    try:
        from .router_core import _complexity_cfg

        v = int((_complexity_cfg() or {}).get("work_impact_min_files") or 5)
        return max(1, min(100, v))
    except Exception:  # noqa: BLE001
        return 5


def work_impact_min_mutations() -> int:
    """complexity.work_impact_min_mutations — write_file|patch calls
    (default 10, clamp 1..200). Never raises."""
    try:
        from .router_core import _complexity_cfg

        raw = (_complexity_cfg() or {}).get("work_impact_min_mutations")
        v = int(10 if raw is None else raw)
        return max(1, min(200, v))
    except Exception:  # noqa: BLE001
        return 10


def _call_args(raw: Any) -> Dict[str, Any]:
    """Tool-call arguments as a dict (payloads carry both dict and JSON str)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            got = json.loads(raw)
            return got if isinstance(got, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def work_mass_facts(context: Optional[dict] = None) -> Dict[str, int]:
    """Realised work at this seam: {tools, files, mutations}.

    tools     = tool-role messages (falling back to tool_calls entries when a
                host strips results) — realised tool traffic, not a plan;
    files     = DISTINCT paths named by write/patch calls;
    mutations = write_file|patch calls (the brief's definition).

    Never raises; an unreadable payload reads as zero work.
    """
    out = {"tools": 0, "files": 0, "mutations": 0, "reworks": 0}
    try:
        _req = (context or {}).get("request") if isinstance(context, dict) else None
        _msgs = (_req or {}).get("messages") if isinstance(_req, dict) else None
        if not isinstance(_msgs, list):
            return out
        tool_msgs = 0
        calls_seen = 0
        files: set = set()
        per_file: Dict[str, int] = {}
        muts = 0
        for m in _msgs:
            if not isinstance(m, dict):
                continue
            if m.get("role") == "tool":
                tool_msgs += 1
            calls = m.get("tool_calls")
            if not isinstance(calls, list):
                continue
            for c in calls:
                if not isinstance(c, dict):
                    continue
                fn = c.get("function") if isinstance(c.get("function"), dict) else c
                name = str((fn or {}).get("name") or "").strip().lower()
                if not name:
                    continue
                calls_seen += 1
                if name in _MUTATION_TOOLS:
                    muts += 1
                if name in _PATH_WRITE_TOOLS:
                    args = _call_args((fn or {}).get("arguments"))
                    for k in _PATH_KEYS:
                        p = args.get(k)
                        if isinstance(p, str) and p.strip():
                            path = p.strip()
                            files.add(path)
                            per_file[path] = per_file.get(path, 0) + 1
                            break
        out["tools"] = max(tool_msgs, calls_seen)
        out["files"] = len(files)
        out["mutations"] = muts
        # R42 outcome-linked field: the SAME file touched more than once in the
        # session is the only rework signal visible at this seam (a later user
        # correction / reviewer rejection lands after delivery). Carried so the
        # gate can be judged against rework later — its own recall is
        # definitional when the threshold IS p80.
        out["reworks"] = sum(1 for c in per_file.values() if c > 1)
        return out
    except Exception:  # noqa: BLE001
        return out


def work_mass_gate(context: Optional[dict] = None) -> Dict[str, Any]:
    """The POST fire decision on realised work mass.

    Fires when tools >= work_mass_min_tools OR distinct files >=
    work_impact_min_files OR mutations >= work_impact_min_mutations.
    Returns {fire, tools, files, mutations, thr, reason} with reason in
    mass|impact|below. Never raises (degrades to below).
    """
    try:
        facts = work_mass_facts(context)
        thr = work_mass_min_tools()
        fmin = work_impact_min_files()
        mmin = work_impact_min_mutations()
        if facts["tools"] >= thr:
            reason = "mass"
        elif facts["files"] >= fmin:
            reason = "impact"
        elif facts["mutations"] >= mmin:
            reason = "impact"
        else:
            reason = "below"
        return {"fire": reason != "below", "tools": facts["tools"],
                "files": facts["files"], "mutations": facts["mutations"],
                "reworks": facts.get("reworks", 0),
                "thr": thr, "files_thr": fmin, "muts_thr": mmin,
                "reason": reason}
    except Exception:  # noqa: BLE001
        return {"fire": False, "tools": 0, "files": 0, "mutations": 0,
                "reworks": 0,
                "thr": 40, "files_thr": 5, "muts_thr": 10, "reason": "below"}


def _already_reflected(session_id: str) -> bool:
    """True when this session already paid a work-mass POST inside the TTL."""
    try:
        now = time.time()
        with _REFLECTED_LOCK:
            ts = _REFLECTED.get(str(session_id))
            if ts is None:
                return False
            if now - ts > _AUDIT_MARKER_TTL:
                _REFLECTED.pop(str(session_id), None)
                return False
            return True
    except Exception:  # noqa: BLE001
        return False


def _mark_reflected(session_id: str) -> None:
    try:
        with _REFLECTED_LOCK:
            _REFLECTED[str(session_id)] = time.time()
            if len(_REFLECTED) > 1024:
                for k in sorted(_REFLECTED, key=lambda k: _REFLECTED[k])[:256]:
                    _REFLECTED.pop(k, None)
    except Exception:  # noqa: BLE001
        pass


def _note_outcome(note: str) -> Tuple[str, str]:
    """(kind, gist) for a paired POST verdict (R34-19g).

    The audit is asked to FIRST answer its own PRE with exactly one of
    CONFIRMED / PRE-WARNING-HELD / PRE-WARNING-MISSED, then give observations.
    So a note whose whole content is that answer is a CONFIRMATION, not a
    finding — reporting it as `flagged` was the second half of why `holds` never
    appeared in the fleet's logs. When observations follow the answer they are
    the finding, and the first one is the gist. Returns ("holds", "") when the
    answer is CONFIRMED and nothing follows, ("flagged", gist) otherwise.
    Never raises.
    """
    try:
        body = _confirmed_gist(note)
        if body:
            return "flagged", body
        t = str(note or "")
        if re.match(r"(?i)^\W*confirmed\b", t.strip()):
            return "holds", ""
        return "flagged", ""
    except Exception:  # noqa: BLE001
        return "flagged", ""


def _confirmed_gist(note: str, cap: int = 160) -> str:
    """One bounded line naming what the audit flagged (R34-19g).

    The note opens with a PRE-answer line (CONFIRMED / PRE-WARNING-HELD /
    PRE-WARNING-MISSED) and may end with the verdict JSON envelope; neither
    belongs in a user-facing confirmation, so both are skipped and the first
    substantive line is used. Never raises; never longer than `cap`.
    """
    try:
        rows, bullets = [], []
        for ln in str(note or "").splitlines():
            raw = ln.strip()
            is_bullet = bool(re.match(r"^(?:[-*\u2022]|\d+[.)])\s+\S", raw))
            t = raw.lstrip("-*\u2022> ").strip()
            if not t:
                continue
            # the audit's own preamble and its answer to its own PRE are not
            # findings — live-caught: the first "finding" quoted to the user was
            # "This is an internal message from your higher self — …"
            if re.match(r"(?i)^(confirmed\b|pre[- ]warning[- ](?:held|missed)\b"
                        r"|this is an internal\b|your higher self\b|higher[- ]self\b"
                        r"|note\b|verdict\b|sense[_-]?check\b|povs?\b"
                        r"|seam instruction\b|treat it as an anchor\b"
                        r"|read it, adjust\b|deliver, and do not\b)", t):
                continue
            # R34-19M: the note carries the operator line that tells the AGENT
            # what to do next ("SEAM INSTRUCTION: the audit is complete - now
            # PROCEED. Deliver the user's answer…"). It is machinery, not a
            # finding, but it does not sit at line start once the note is one
            # paragraph — live: a user read the audit's own instruction to the
            # agent as the audit's flag against her work. Reject the line
            # wherever the operator phrasing appears.
            if re.search(r"(?i)(now PROCEED|do not restate|do not ask permission"
                         r"|do not output any marked text)", t):
                continue
            if t.startswith("{") or t.startswith("[") or t.startswith("```") \
                    or t.startswith("===") or t.startswith("*"):
                continue
            flat = re.sub(r"\s+", " ", t)
            rows.append(flat)
            if is_bullet:
                bullets.append(flat)
        # a bullet is the audit's own list of observations, so it outranks any
        # prose line that survived the filter
        rows = bullets if bullets else rows
        # No fallback to the raw note: when every line is the PRE-answer or the
        # JSON envelope there IS no finding to quote, and falling back would
        # ship the audit's own "CONFIRMED: ..." line to the user as a flag —
        # exactly the misread _note_outcome exists to prevent.
        gist = (rows[0] if rows else "").strip()
        if not gist:
            return ""
        return gist[:cap] + ("\u2026" if len(gist) > cap else "")
    except Exception:  # noqa: BLE001
        return ""


def audit_revision_min_seconds() -> float:
    """Budget floor below which the in-hook revision is not attempted.

    The revision is a FRONTIER call at reasoning_effort max; under a contended
    hook it must be SKIPPED rather than attempted with crumbs. Never raises.
    """
    try:
        from .router_core import _complexity_cfg

        v = float((_complexity_cfg() or {}).get("audit_revision_min_seconds") or 20)
        return max(0.0, min(180.0, v))
    except Exception:  # noqa: BLE001
        return 20.0


def audit_revision_seconds() -> float:
    try:
        from .router_core import _complexity_cfg

        v = float((_complexity_cfg() or {}).get("audit_revision_seconds") or 60)
        return max(0.0, min(180.0, v))
    except Exception:  # noqa: BLE001
        return 60.0

# ---------------------------------------------------------------- closure ----

# Goran 2026-09-10 (option C): audit on CLOSURE, not blind turn counts.
# "response after n turns is signal" — a closure-shaped response (multi-task
# wrap-up, summary delivered, task handoff) is an audit trigger in itself.
_CLOSURE_PATTERNS = (
    r"\b(?:in\s+summary|to\s+sum(?:mari[sz]e|up)|summing\s+up)\b",
    r"\b(?:all|both|every|each)\s+(?:three|four|five|\d+)?\s*(?:tasks?|items?|findings?|fix(?:es)?|changes?|commits?)\b.{0,40}\b(?:done|complete[d]?|shipped|landed|delivered|verified|fixed)\b",
    # 2026-09-11 F2 hardening (frontier-anchored): patterns 2/3 were prose-collision
    # class — bare completion verbs match fiction/ordinary prose ("She wrapped her
    # legs around him", "They wrapped up in each other", "The plane landed and the
    # crew fixed the gear", "closed out the bar tab"). Fix: completion phrases now
    # REQUIRE a task/bookkeeping noun anchor within the window. Bare summary verbs
    # are never closure by themselves.
    r"\b(?:wrapped\s+up|closed\s+out)\b(?=[^.]{0,60}?\b(?:everything|all\b|the\s+work|the\s+task|this\s+task|the\s+audit|the\s+review|the\s+session|the\s+discussion|the\s+investigation|the\s+analysis|tasks?\b|items?\b)\b)",
    r"\ball\s+done\b",
    r"\beverything\s+(?:is\s+)?(?:done|shipped|landed|verified)\b",
    r"\b(?:tasks?|items?|work|changes?|fix(?:es)?|commits?|findings?|reviews?|audits?|modules?|files?|components?|plan|session)\b[^\n]{0,120}(?<!,\s)\b(?:done|complete[d]?|shipped|landed|fixed|verified)\b[^.]{0,80}\b(?:and|plus|\+)\s[^.]{0,40}\b(?:done|complete[d]?|shipped|landed|fixed|verified|pushed)\b",
    r"\ball\s+(?:three|four|five|\d+)?\s*(?:of\s+(?:them|these))?\s*(?:are\s+)?(?:fixed|shipped|verified|done|landed)\b",
    # 2026-09-10 live miss: answers that open/close with a closure frame
    # ("Closure: ...", "Final summary", "Bottom line") were skipped by the
    # gate (turn counter not yet at N). Closure FRAMING is the signal, not
    # just closure bookkeeping. NOTE: no ^ anchor — is_closure_response joins
    # ask+response with a space, so framing can legitimately sit mid-string.
    r"\b(?:closure|closing|final\s+(?:summary|recommendation|answer|verdict|wrap[- ]?up)|bottom\s+line)\s*[:—-]?",
    r"\bfinal\s+recommendation\b",
    r"\b(?:summar(?:y|izing)\s+(?:the|of)\s+(?:the\s+)?(?:full\s+)?recommendation|final\s+thoughts?\s*[:,])",
)

_CLOSURE_RE = None  # compiled lazily


# R20-B6 (rider 20): benign COMPOSITION frames — asks that request prose
# (essay/report/summary/story/...) with no decision imperative. A closure
# regex collision INSIDE the composed prose must never arm the audit
# trigger on such a turn (live orchestrator B6: the essay's 'verified
# against the old behavior, and shipped' matched the multi-item closure
# pattern and billed a frontier consult).
_COMPOSITION_RE = re.compile(
    r"\b(?:write|draft|compose|produce|give)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+)?"
    r"(?:short\s+|brief\s+|detailed\s+|long\s+|formal\s+)?"
    r"(?:essay|report|summary|story|poem|article|blog\s*post|paragraph|memo|review|outline)\b",
    re.IGNORECASE)
_DECISION_IMPERATIVE_RE = re.compile(
    r"\b(?:decide|deciding|choose|choosing|pick|picking|select|selecting|opt)\b",
    re.IGNORECASE)


def _benign_composition_frame(ask: str) -> bool:
    """R20-B6: True when the ask requests composed prose (essay/report/...)
    and carries NO decision imperative — the completion-audit arm stands
    down (benign-ZERO contract). Never raises, fail-open False."""
    try:
        t = str(ask or "")
        if not t.strip():
            return False
        if _DECISION_IMPERATIVE_RE.search(t):
            return False
        return bool(_COMPOSITION_RE.search(t))
    except Exception:  # noqa: BLE001 — fail-open: not a composition frame
        return False


def is_closure_response(ask: str, response_text: str) -> bool:
    """Closure-signal detector (Goran 09-10, option C). True when the ask or
    response carries closure markers — summary wrap-up, multi-item completion,
    handoff statement. Cheap regex only, never raises, fail-open to False
    (the every-N + tool-count triggers still cover the miss)."""
    global _CLOSURE_RE
    try:
        if _CLOSURE_RE is None:
            import re as _re
            _CLOSURE_RE = _re.compile("|".join(_CLOSURE_PATTERNS), _re.IGNORECASE)
        text = " ".join(str(x) for x in (ask, response_text) if x)
        return bool(text and _CLOSURE_RE.search(text))
    except Exception:  # noqa: BLE001 — fail-open: not a closure
        return False


# ---------------------------------------------------------------- config ----

def audit_mode() -> str:
    """complexity.audit_mode: off (default) | complex | always. Never raises."""
    try:
        from .router_core import _complexity_cfg  # local import avoids cycle

        v = str((_complexity_cfg() or {}).get("audit_mode") or "off").strip().lower()
        return v if v in ("off", "complex", "always") else "off"
    except Exception:  # noqa: BLE001
        return "off"


def audit_enabled() -> bool:
    return audit_mode() != "off"


def audit_max_chars() -> int:
    try:
        from .router_core import _complexity_cfg

        v = int((_complexity_cfg() or {}).get("audit_max_chars") or 2400)
        return max(400, min(16000, v))
    except Exception:  # noqa: BLE001
        return 2400


def audit_sync_seconds() -> float:
    """Sync POST audit (Goran 2026-09-10): frontier consult must complete
    BEFORE the final response is delivered. audit_sync_seconds caps how long
    delivery blocks on the consult. R20-D3 (rider 20) HARD CAP 25s: the
    host plugin runner kills the transform_llm_output callback 30s in
    ("timed out after 30s — skipping", live valmet 06:00:50/06:03:03Z) and
    then SKIPS every subsequent callback invocation ("skipped after
    previous timeout or while still running", live 06:04:00Z session
    api_1791266587_749f80ab) — a billed consult's parked banner then has NO
    consume edge at all (park-without-capture, ledger rows 148+149 billed,
    0 delivered). The default 45s guarantee tripped that whenever the
    frontier consult out-ran 30s. Cap below the runner budget so the sync
    consult can never orphan the hook; on timeout the unaudited response
    ALWAYS delivers (fail-open).
    0 disables sync (legacy async path).
    toggleable: audit_mode = sync | async (sync default when knob set/absent).
    On timeout/error the unaudited response ALWAYS delivers (fail-open).
    Never raises."""
    try:
        from .router_core import _complexity_cfg

        v = float((_complexity_cfg() or {}).get("audit_sync_seconds") or 45)
        return max(0.0, min(HOOK_SYNC_CAP, v))
    except Exception:  # noqa: BLE001
        return min(HOOK_SYNC_CAP, 45.0)


def audit_topology() -> str:
    """audit_mode topology toggle: 'sync' (default) blocks delivery on the
    consult; 'async' returns the legacy next-turn verdict delivery. Independent
    of audit_mode's off|complex|always FIRE policy (router section knob
    audit_topology overrides; complexity.audit_topology fallback)."""
    try:
        from . import config_access as _cac

        sec = _cac.router_section() or {}
        raw = str(sec.get("audit_topology") or "").strip().lower()
        if not raw:
            from .router_core import _complexity_cfg

            raw = str((_complexity_cfg() or {}).get("audit_topology") or "").strip().lower()
        return "async" if raw == "async" else "sync"
    except Exception:  # noqa: BLE001
        return "sync"


# ------------------------------------------------------- once-per-task ------

def _fire_marker_key(session_id: str, ask: str, model: str) -> str:
    return router_core.task_id_for(session_id, ask, model)


def _already_fired(key: str) -> bool:
    now = time.time()
    with _FIRED_LOCK:
        ts = _FIRED.get(key)
        if ts is None:
            return False
        if now - ts > _AUDIT_MARKER_TTL:
            _FIRED.pop(key, None)
            return False
        return True


def _mark_fired(key: str) -> None:
    with _FIRED_LOCK:
        _FIRED[key] = time.time()
        # bounded: drop oldest beyond 256
        if len(_FIRED) > 256:
            for k in sorted(_FIRED, key=lambda k: _FIRED[k])[: len(_FIRED) - 256]:
                _FIRED.pop(k, None)


# ---------------------------------------------------------- eligibility -----

def _is_complex_ask(ask: str) -> bool:
    """Stage-1 bucket reuse: any non-zero signal counts as complex."""
    try:
        from . import complexity

        sig = complexity.stage1_signals(ask or "")
        return any(v > 0 for v in sig.values())
    except Exception:  # noqa: BLE001
        return False


def eligible(session_id: str, ask: str, response_text: str, model: str = "",
             turn_n: Optional[int] = None, paired: bool = False) -> Tuple[bool, str]:
    """Fire decision for the completion audit. Returns (ok, reason).

    R34-19: `paired=True` = a PRE advisory exists for this turn, so this is
    its POST CONFIRMATION — it fires regardless of audit_mode (off included)
    and the PRE/POST mutual exclusion no longer applies (the pairing IS the
    one-consult-per-turn contract)."""
    mode = audit_mode()
    if mode == "off":
        # R42: pairing no longer overrides the master switch — a PRE is payload
        # context, not a confirmation contract. Blocked billing is still
        # measurable as would-fire in the work_mass_gate row.
        return False, "mode_off"
    # R34-19: the floor stops a one-liner buying a consult, but a PAIRED turn
    # already paid for its PRE — its confirmation is owed even when the report
    # is terse (live: 2 files + 5 tests reported in 264 chars lost its POST).
    if not paired and (not isinstance(response_text, str)
                       or len(response_text.strip()) < _MIN_RESPONSE_CHARS):
        return False, "response_too_short"
    if paired and not isinstance(response_text, str):
        return False, "response_too_short"
    # R42: the ask-side complexity test is GONE from POST eligibility. POST
    # reads the WORK (work_mass_gate), never the ask — that incoherence (post
    # confirming pre) is the defect this rider removes. `audit_mode=complex`
    # still means "not every turn", but the discriminator is realised work.
    key = _fire_marker_key(session_id, ask, model)
    if _already_fired(key):
        return False, "already_fired_this_task"
    if _has_pending(session_id):
        return False, "audit_verdict_pending"
    # don't audit an audit — if flash's response itself quotes an audit note,
    # skip (prevents self-perpetuation).
    if "COMPLETION AUDIT" in (response_text or "") or "HIGHER-SELF COMPLETION" in (response_text or ""):
        return False, "response_quotes_audit"
    # deep-consult fix (PRE+POST mutual exclusion), AMENDED by R34-19: a PRE
    # consult that produced an ADVISORY for this turn → the POST no longer
    # stands down, it BECOMES that PRE's confirmation (paired=True bypasses
    # this branch). Only an UNPAIRED POST — i.e. a PRE that was staged but
    # produced nothing (cap-blocked/failed) — stands down here.
    # Turn-scoped (09-10 battery fix): a PRE flag from an EARLIER turn does
    # NOT exclude — its exclusion right expired with that turn; otherwise a
    # turn-1 PRE suppresses the turn-2 closure audit forever.
    if not paired:
        try:
            from . import state as _st

            if _st.pre_fired_this_turn(session_id, current_turn=turn_n):
                return False, "pre_consult_this_turn"
        except Exception:  # noqa: BLE001
            pass
    return True, "ok"


# ------------------------------------------------------------ stash/pick ----

def stash_verdict(session_id: str, verdict: str) -> None:
    with _PENDING_LOCK:
        _PENDING[session_id] = (time.time(), verdict)


def _has_pending(session_id: str) -> bool:
    with _PENDING_LOCK:
        rec = _PENDING.get(session_id)
        if rec is None:
            return False
        ts, _ = rec
        if time.time() - ts > _NOTE_TTL:
            _PENDING.pop(session_id, None)
            return False
        return True


def consume_verdict(session_id: str) -> Optional[str]:
    """Pop the pending audit verdict for injection into the NEXT turn."""
    with _PENDING_LOCK:
        rec = _PENDING.pop(session_id, None)
    if rec is None:
        return None
    ts, verdict = rec
    if time.time() - ts > _NOTE_TTL:
        return None
    return verdict


# ------------------------------------------------------------- consult ------

def _work_digest(request: Optional[dict], max_chars: int) -> str:
    """Compact work-context digest from the outgoing payload's messages:
    last tool results + assistant turns before the final response. Terse.
    Deep-consult fix: messages carrying prior audit artifacts (verdicts,
    banners) are EXCLUDED — audit-of-audit recursion breaks the budget and
    compounds context."""
    lines: List[str] = []
    try:
        msgs = (request or {}).get("messages") if isinstance(request, dict) else None
        if not isinstance(msgs, list):
            return ""
        budget = max(max_chars // 2, 200)
        for m in reversed(msgs):
            if len("\n".join(lines)) >= budget:
                break
            role = m.get("role")
            if role not in ("tool", "assistant"):
                continue
            content = str(m.get("content") or "")
            if not content.strip():
                continue
            if any(marker in content for marker in _AUDIT_ARTIFACT_MARKERS):
                continue  # audit artifact — never re-enter the consult payload
            if len(lines) >= 8:
                break
            excerpt = content.strip()[:400].replace("\n", " ")
            lines.append("%s: %s" % (role, excerpt))
        return "\n".join(reversed(lines))[:budget]
    except Exception:  # noqa: BLE001
        return ""


def _strip_audit_artifacts(text: str) -> str:
    """Best-effort removal of router audit artifacts from a string before it
    is delivered or fed back into a consult payload (deep-consult fix: the
    spend banner must not persist into conversation history)."""
    try:
        out = text
        for marker in _AUDIT_ARTIFACT_MARKERS:
            idx = out.find(marker)
            # cut from the marker to the end of its line (banner/note header)
            while idx != -1:
                line_end = out.find("\n", idx)
                out = out[:idx] + (out[line_end + 1:] if line_end != -1 else "")
                idx = out.find(marker)
        return out
    except Exception:  # noqa: BLE001
        return text


def _is_audit_artifact(text: str) -> bool:
    try:
        return any(marker in (text or "") for marker in _AUDIT_ARTIFACT_MARKERS)
    except Exception:  # noqa: BLE001
        return False


def substantive_turn_bump(session_id: str) -> int:
    """Counter bump for the substantive-turn cadence. Audit-artifact
    responses (verdict envelopes / banners) are handled by audit_gate,
    which resets the counter instead of counting them."""
    try:
        return state.bump_substantive_turn(session_id)
    except Exception:  # noqa: BLE001
        return 0


def audit_gate(session_id: str, response_text: str, model: str = "",
               context: Optional[dict] = None,
               *, ask_override: str = "",
               hook_budget: float = 0.0) -> Optional[str]:
    """Unified POST completion-audit gate: fire policy (every-3-substantive-
    turns OR closure-shaped response OR >=3 tool calls), eligibility, sync
    consult, in-hook revision pass, inline spend banner. Returns the response
    string to deliver, or None when no audit fired (caller passes through).

    R20-D3 (rider 20): hook_budget (seconds) bounds the TOTAL time the sync
    consult + revision pass may consume inside the host's transform hook —
    the plugin runner kills the callback at 30s and then SKIPS later
    invocations, orphaning billed-consult parked banners. When >0, the sync
    budget and the revision budget are clamped so their sum cannot exceed
    it (live: valmet D3, ledger rows 148+149 billed, banner parked, zero
    consume events because the runner skipped the skipped hook's turn).

    Extracted so BOTH benign-passthrough AND technical-flinch-passthrough
    delivery paths reach the audit arm (live-verified gap: refusal-phrase
    false-positive passthroughs were skipping the audit entirely — closure
    responses are exactly the most refusal-shape-shaped text)."""
    try:
        # R42 shadow path — logged on EVERY turn, before any billing or
        # eligibility check, so "would-fire" is measurable even when the
        # consult is blocked (mode off / cap / no endpoint). This is the
        # validation seam for the retune: fire-vs-did-fire, per role.
        _wm = work_mass_gate(context)
        try:
            _log("work_mass_gate fire=%s tools=%d files=%d muts=%d thr=%d "
                 "reason=%s rework=%d"
                 % (str(bool(_wm["fire"])), int(_wm["tools"]),
                    int(_wm["files"]), int(_wm["mutations"]),
                    int(_wm["thr"]), str(_wm["reason"]),
                    int(_wm.get("reworks") or 0)),
                 session_id=session_id)
        except Exception:  # noqa: BLE001 — telemetry never breaks the gate
            pass
        # R34-19 PRE->POST pairing: a PRE advisory for this turn makes the
        # POST its CONFIRMATION — it fires even with audit_mode off.
        # R34-19k2: the ask-less read below accepts ANY live record for the
        # session (lenient by design, R34-19c). Run against a turn that already
        # has a stored advisory from a DIFFERENT ask it returns that record, so
        # `not _pair` was false and claim-derived pairing never ran — the stale
        # advisory silently vetoed the per-turn truth. Resolve the claim FIRST.
        _pair = None
        # R34-19k: resolve the CLAIM before any floor. A turn whose higher self
        # already spoke is owed a confirmation however terse the report is, so
        # claim-derived pairing must be visible to the length check below (it
        # landed after it first, and a 21-char report still lost its pair).
        try:
            from . import route_gate as _rg
            _claim = _rg.claim_state(session_id)
            if _claim is not None and not _pair:  # claim first, then stored
                # R34-19k: the CLAIM is pairing evidence in its own right.
                # On a host where the PRE advisory text never made it into the
                # store (live: recovery, where every PRE consult was billed,
                # bannered, and still left no record), the gate stood down with
                # `turn_claimed` and the agent was warned at the start of the
                # turn and never told how it went — the highest-value pairing
                # (the user asked her higher self directly) failing silently.
                # A higher-pre claim for THIS turn proves the higher self
                # spoke; the advisory TEXT is a convenience, not the proof.
                if str(_claim.get("lane") or "") == "higher-pre":
                    _pair = {
                        "text": "",
                        "trigger": str(_claim.get("source") or "declared_user"),
                        "ask_sig": "",
                        "claim_derived": True,
                    }
                    _log("higher_self_pair_derived_from_claim",
                         claim_lane="higher-pre",
                         claim_source=str(_claim.get("source") or ""),
                         session_id=session_id)
                else:
                    _log("audit_gate_skip", reason="turn_claimed",
                         claim_lane=str(_claim.get("lane") or ""),
                         claim_source=str(_claim.get("source") or ""),
                         session_id=session_id)
                    return None
        except Exception:  # noqa: BLE001 — fail-open
            pass
        if _pair is None:
            _pair = _pre_pairing(session_id)

        if not audit_enabled():
            # R42: a PRE on record no longer overrides the master switch. The
            # work-mass decision is still on record (`work_mass_gate`) — a
            # would-fire is measurable even when billing is blocked.
            _log("audit_gate_skip", reason="mode_off",
                 would_fire=bool(_wm["fire"]), session_id=session_id)
            return None
        # R34-19: the length floor exists to stop a one-line reply from buying
        # a frontier consult. A PAIRED turn already paid for its PRE, so the
        # confirmation is owed regardless of how terse the report is — a short
        # report over real work (2 files, 5 tests) was silently losing its
        # confirmation because the agent reported it in 264 chars.
        if not isinstance(response_text, str) or (
                not _pair and len(response_text.strip()) < _MIN_RESPONSE_CHARS):
            _log("audit_gate_skip", reason="response_too_short_unpaired",
                 chars=len(str(response_text or "")), pair=bool(_pair),
                 session_id=session_id)
            return None
        # Leg 7 (single claim point, blueprint invariant #1): when the
        # gate's claim registry holds ANY claim for this session/turn,
        # the audit stands down — that claim owns the turn's one routing
        # outcome. Fail-open: registry unavailable -> legacy behavior.
        # v3.8.5 (Goran 09-10): a turn whose response IS an uncensored render
        # must not be audited — the frontier lane would audit the uncensored
        # lane's output, which is a category error (complementary lanes: U
        # extends capability, F extends sight; F has no standing to review U).
        # Detection: the PRE lane stashes the original ask when a render
        # delivers (state.stash_pending); a fresh unconsumed stash for THIS
        # session means this turn just delivered a render.
        try:
            from . import state as _st
            if _st.has_pending_render(session_id):
                if _pair:
                    # R34-19: the audit-firewall still binds — frontier never
                    # audits an uncensored render turn, pairing included. The
                    # record is consumed so no stale PRE pairs a later turn.
                    _log_pair("post", False,
                              reason="uncensored_render_this_turn",
                              session_id=session_id)
                    _st.clear_pre_advisory(session_id)
                _log("audit_gate_skip", reason="uncensored_render_this_turn",
                     session_id=session_id)
                return None
        except Exception:  # noqa: BLE001 — fail-open
            pass
        ask = ask_override or ""
        if not ask.strip() and isinstance(context, dict):
            ask = context.get("user_message") or ""
        if not ask.strip():
            try:
                from . import state as _state
                ask = _state.get_last_seen(session_id) or ""
            except Exception:  # noqa: BLE001
                ask = ""
        if not ask.strip() and _pair:
            # The PRE record carries the ask it was taken for; the POST seam
            # often has none (no user_message in context, empty last-seen).
            ask = str(_pair.get("ask") or "")
        if not ask.strip():
            _log("audit_gate_skip", reason="no_ask", session_id=session_id)
            return None
        # R34-19: bind the pairing to THIS ask — a PRE recorded for another
        # turn in the same session is not this turn's PRE.
        # R34-19k: this second, ask-bound read must not ERASE claim-derived
        # pairing (the host with no stored advisory is exactly the host this
        # read returns None on). Keep whichever evidence exists.
        _stored_pair = _pre_pairing(session_id, ask)
        if _stored_pair and _pair and _pair.get("claim_derived"):
            # R34-19k2: a claim is per-TURN truth; a stored record whose ask
            # fingerprint does not match is accepted leniently (R34-19c) and
            # carries the PRE's own trigger — which then overwrote the claim's
            # `declared_user` and made an on-demand turn look like mid-work, so
            # it deferred and lost the confirmation the user asked for. The
            # claim decides WHY this turn is worthwhile; the stored text is
            # only cargo.
            _stored_pair = {**_stored_pair,
                            "trigger": _pair.get("trigger"),
                            "claim_derived": True}
            _log("higher_self_pair_trigger_from_claim",
                 stored_trigger=str(_stored_pair.get("trigger_stored") or ""),
                 claim_trigger=str(_pair.get("trigger") or ""),
                 session_id=session_id)
        _pair = _stored_pair or _pair
        # R42: pairing is CONTEXT ONLY. `post_pair_mode: off` now means "do not
        # include the PRE advisory in the POST payload" — it does NOT mean
        # "do not POST" (the old reading, retired). The record itself is still
        # resolved above, because a PRE-paid turn keeps its length-floor bypass.
        _pair_ctx = _pair
        if _pair is not None and post_pair_mode() == "off":
            _pair_ctx = None
            _log("higher_self_pair_context_suppressed", reason="pair_mode_off",
                 session_id=session_id)
        # R16-2 (rider 16, D1a): the benign BRIEF frame now covers the ANCHOR
        # leg — the completion-audit consult was the last ungated frontier
        # arm: a benign setup/briefing ask ('brief me on the tradeoffs of X
        # vs Y') billed frontier rows through the audit consult even with
        # the PRE (R15-7) and POST fork-scan (R14-2) gates in place. A
        # briefing frame with no decision imperative never consults here;
        # decision imperatives are exempt inside the gate itself (the
        # decide-regex), so real fork/closure audits are unaffected. Target
        # ZERO frontier rows on benign setup turns. Fail-open: gate errors
        # keep the legacy behavior.
        try:
            from .decision import _benign_brief_frame as _bf

            if _bf(ask) and not _pair:
                _log("audit_gate_skip", reason="benign_brief_frame",
                     session_id=session_id)
                return None
        except Exception:  # noqa: BLE001 — gate is additive, fail-open
            pass
        # R20-B6 (rider 20): benign-ZERO contract for the completion-audit
        # arm. A turn whose ASK is (a) an explicit no-decision frame
        # (R8-3 family: 'no decisions needed', 'just your view', ...) or
        # (b) a benign composition frame ('write a short essay on ...')
        # with NO decision imperative is a NON-MARKED turn: the audit
        # consult must NOT bill — no frontier rows, no banner. Live:
        # orchestrator B6 (ledger rows 673/674/675 billed
        # frontier_adversarial_parse_fail -> frontier_post ->
        # frontier_consult, banner delivered on 'Write a short essay on
        # why incremental verification beats big-bang rewrites. No
        # decisions needed.') — the essay's own prose ('the worst
        # component, replaced, verified against the old behavior, and
        # shipped') prose-collided with the multi-item closure pattern and
        # armed the closure trigger on a benign turn. Decision imperatives
        # (decide/choose/pick/select/opt) are EXEMPT: an essay that ends
        # in a real fork stays auditable. Fail-open: gate errors keep the
        # legacy behavior.
        try:
            from .decision import has_declared_fork_structure as _cdf

            if (_benign_composition_frame(ask)
                    and not _cdf(ask) and not _pair):
                _log("audit_gate_skip",
                     reason="benign_zero_composition_frame",
                     session_id=session_id)
                return None
        except Exception:  # noqa: BLE001 — gate is additive, fail-open
            pass
        try:
            from . import router_core as _rc

            _min_turns = _rc.post_audit_min_turns()
        except Exception:  # noqa: BLE001
            _min_turns = 3
        try:
            from . import state as _state
            _turn_n = _state.bump_substantive_turn(session_id)
        except Exception:  # noqa: BLE001
            _turn_n = 0
        # audit-of-audit recursion: a response carrying router artifacts is
        # router output, not agent work — reset the cadence counter.
        try:
            if _is_audit_artifact(response_text or ""):
                try:
                    from . import state as _state
                    _state.reset_substantive_turn(session_id)
                except Exception:  # noqa: BLE001
                    pass
                _turn_n = 0
        except Exception:  # noqa: BLE001
            pass
        _fire = bool(_min_turns <= 1 or (_turn_n > 0 and _turn_n % _min_turns == 0))
        # R42: WORK MASS owns the fire decision; pairing never does. A PRE on
        # record cannot fire a POST and cannot suppress one — it is payload
        # context only (and may be absent entirely).
        _fire_trigger = "cadence" if _fire else ""
        if _wm["fire"]:
            if _already_reflected(session_id):
                _log("work_mass_gate_suppressed", reason="already_reflected",
                     tools=int(_wm["tools"]), session_id=session_id)
            elif not _fire:
                _fire = True
                _fire_trigger = "work_mass_" + str(_wm["reason"])
        _closure = False
        if not _fire:
            # Goran 2026-09-10 (option C): closure-shaped responses audit
            # regardless of the counter.
            try:
                _closure = is_closure_response(ask, response_text)
            except Exception:  # noqa: BLE001
                _closure = False
            if _closure:
                # R20-B6 (rider 20): the no-decision CLOSURE stand-down. When
                # the ask carries the R8-3 nondecision frame family ('no
                # decisions needed', 'just your view', ...) AND no manual
                # trigger AND no declared fork, the CLOSURE arm stands down —
                # the essay/prose's own closure-shaped sentences must never
                # arm the audit trigger on a NON-MARKED turn. The CADENCE,
                # TOOL-COUNT and RISK-L3 arms are UNAFFECTED (they fire on
                # their own evidence, not on closure prose). Fail-open: any
                # helper error keeps the legacy behavior (closure fires).
                try:
                    from .decision import (
                        _post_nondecision_frame as _ndf,
                        _manual_trigger_in_text as _mt,
                        has_declared_fork_structure as _df,
                    )

                    if (_ndf(ask) and not _mt(ask) and not _df(ask)):
                        _closure = False
                        _log("audit_gate_skip",
                             reason="closure_nondecision_frame",
                             session_id=session_id)
                except Exception:  # noqa: BLE001 — gate is additive
                    pass
            _fire = _closure
            if _closure:
                _fire_trigger = "closure"
        if not _fire:
            # R15 LEG 1 L3 (2026-09-21): the completed turn REPORTS R2/R3
            # actions against a live/fleet/config target → audit fires
            # regardless of the counter (the "Proceed"-pattern catch: terse
            # ingress has no PRE signal, but the execution turn's content
            # does). Double-fire is impossible: eligible()'s
            # pre_consult_this_turn exclusion + the once-per-task marker +
            # the turn-claim registry all still bind below. R3 actions
            # audit ALWAYS; R2 reports only when no PRE consult fired this
            # turn (same exclusion). audit_only/off risk modes still fire
            # the L3 audit (it is the mode's whole purpose); the risk
            # master switch is the only kill.
            try:
                from . import risk as _risk

                if _risk.risk_enabled():
                    _rcfg = _risk.risk_cfg()
                    if bool(_rcfg.get("post_audit", True)) and \
                            _risk.reports_consequential(response_text):
                        _fire = True
                        _fire_trigger = "risk_report"
                        try:
                            _log("risk_report_audit_trigger",
                                 session_id=session_id)
                        except Exception:  # noqa: BLE001
                            pass
            except Exception:  # noqa: BLE001 — L3 must never break the gate
                pass
        # R42: the old >=3-tool-message arm is retired — it was a proxy for
        # work mass and fired on ordinary read-heavy turns. Work mass is the
        # single source of truth for "was this heavy" (work_mass_gate above).
        # route-log via module-local helper (never cross-package import:
        # gateway loads the plugin under a different top-level name and a
        # hard import here would kill the whole gate silently — live-caught)
        if not _fire:
            _log("audit_gate_skip", reason="cadence",
                       turn=_turn_n, of=_min_turns, session_id=session_id)
            return None
        # R42: one row per fired consult, carrying the trigger AND the work
        # shape — coverage is countable per role from the ledger, and the gate
        # is judgeable against rework rather than its own recall.
        _log("post_reflection_fired", trigger=(_fire_trigger or "unknown"),
             tools=int(_wm["tools"]), files=int(_wm["files"]),
             muts=int(_wm["mutations"]),
             rework=int(_wm.get("reworks") or 0), paired=bool(_pair),
             session_id=session_id)
        _ok, _why = eligible(session_id, ask, response_text, model,
                             turn_n=_turn_n, paired=bool(_pair))
        _log("completion_audit_gate",
                   ok=_ok, reason=_why, session_id=session_id,
                   turn=_turn_n, of=_min_turns, closure=_closure,
                   paired=bool(_pair), trigger=_fire_trigger)
        if _ok:
            # R42: the once-per-session marker is set at the FIRE decision,
            # not after the consult, so a failing/failed consult does not
            # re-arm and bill the same body of work on the next turn.
            if str(_fire_trigger).startswith("work_mass_"):
                _mark_reflected(session_id)
        if not _ok:
            if _pair:
                # The pairing existed but the POST cannot fire (too short,
                # already fired, quotes an audit, ...). Consume it: one POST.
                try:
                    from . import state as _st
                    _st.clear_pre_advisory(session_id)
                except Exception:  # noqa: BLE001
                    pass
                _log_pair("post", False, pre_trigger=str(
                    _pair.get("trigger") or ""), reason=_why,
                    session_id=session_id)
            return None
        if not _pair:
            # POST fired on its OWN gate (no PRE advisory this turn).
            _log_pair("post", False, reason="no_pre", session_id=session_id)
        _sync_budget = audit_sync_seconds()
        # R20-D3: clamp the sync consult under the hook budget (the runner
        # kills the transform callback at 30s and then skips later turns —
        # a billed consult's parked banner would have no consume edge).
        if hook_budget and hook_budget > 0:
            _sync_budget = min(_sync_budget, float(hook_budget))
            _sync_budget = max(0.0, _sync_budget)
        _topology = audit_topology()
        if _topology == "sync" and _sync_budget > 0:
            _req = (context or {}).get("request") if isinstance(context, dict) else None
            try:
                _sync_kw: Dict[str, Any] = {}
                if _pair_ctx:
                    # R42: pairing is CONTEXT, never a fire gate. A PRE that
                    # exists rides along so the confirmation has something to
                    # answer; its absence changes nothing about firing.
                    _sync_kw = {
                        "pre_brief": str(_pair_ctx.get("text") or ""),
                        "pre_trigger": str(_pair_ctx.get("trigger") or ""),
                    }
                _meta = run_completion_audit_sync(
                    session_id, ask, response_text, _req, model,
                    timeout_s=_sync_budget, fold_revision=True, **_sync_kw)
            except Exception:  # noqa: BLE001
                _meta = None
            if not (_meta and isinstance(_meta, dict)):
                if _pair:
                    try:
                        from . import state as _st
                        _st.clear_pre_advisory(session_id)
                    except Exception:  # noqa: BLE001
                        pass
                    _log_pair("post", False, pre_trigger=str(
                        _pair.get("trigger") or ""),
                        reason="post_consult_failed", session_id=session_id)
                return None
            if _pair:
                # R34-19: the PRE exists — this verdict answered it, whether
                # it said NO-FINDINGS or findings. Pairing is logged FIRST so
                # telemetry never loses it, then the record is consumed.
                _log_pair("post", True, pre_trigger=str(
                    _pair.get("trigger") or ""), session_id=session_id)
                try:
                    from . import state as _st
                    _st.clear_pre_advisory(session_id)
                except Exception:  # noqa: BLE001
                    pass
            _note = str(_meta.get("note") or "")
            # R37-1: the revision rode INSIDE the consult call. There is no
            # second in-hook frontier call on this path any more — two calls
            # cannot fit one hook budget (measured: the consult spends the whole
            # budget, the old revision pass was handed ~0.05s, and
            # `completion_audit_sync_applied revised=False` fired on 64 of 64
            # events fleet-wide: it has never once changed a delivered body).
            _delivered = response_text
            _revised = _meta.get("revised_response")
            _revised_source = "none"
            _revised_reason = str(_meta.get("revised_reason") or "not_requested")
            if isinstance(_revised, str) and _revised.strip():
                _delivered = _revised.strip()
                _revised_source = "folded"
                _revised_reason = str(_meta.get("revised_reason") or "folded")
                # persisted == delivered (R34-19L): turn_finalizer persisted the
                # ENTRY text before this hook fired, so the SWAPPED body has to
                # be reconciled over that row — the same write the strip branch
                # does. The banner is appended AFTER this point and is never part
                # of the reconciled body; the banner capture that follows chains
                # onto this write through canonical's R16-2b last-rewrite
                # cascade, so persisted ends up as the DELIVERED text.
                try:
                    from .core import canonical as _can_fold
                    _fold_ok = _can_fold.rewrite_persisted_turn(
                        session_id, response_text, _delivered)
                    _log("persisted_body_reconciled" if _fold_ok
                         else "persisted_body_reconcile_failed",
                         reason="folded_swap",
                         chars_from=len(response_text or ""),
                         chars_to=len(_delivered or ""),
                         session_id=session_id)
                except Exception:  # noqa: BLE001 — provenance never breaks delivery
                    logger.debug("folded-body reconcile error", exc_info=True)
            elif _note:
                # revised_response is null: the defect needs TOOL work (or the
                # fold failed its own checks). Behave exactly as before — deliver
                # the original and let the verdict ride to the next turn, where
                # it is injected as the higher-self note.
                try:
                    stash_verdict(session_id, _note)
                except Exception:  # noqa: BLE001
                    pass
            _log("completion_audit_sync_applied",
                       chars=len(_note), budget_s=_sync_budget,
                       revised=(_delivered != response_text),
                       revised_source=_revised_source,
                       reason=_revised_reason,
                       session_id=session_id)
            try:
                from . import state as _state
                _state.mark_post_audited(session_id)
            except Exception:  # noqa: BLE001
                pass
            _btext = ""
            try:
                from . import debug_banner as _dbg
                if _dbg.debug_banner_enabled():
                    _btext = _dbg.format_banner(
                        lane="frontier-anchor", trigger="completion_audit",
                        model=str(_meta.get("model") or ""),
                        endpoint=str(_meta.get("endpoint") or ""),
                        tokens_in=_meta.get("tokens_in"),
                        tokens_out=_meta.get("tokens_out"),
                        est_cost=_meta.get("cost"),
                        latency_s=_sync_budget, retries=0, task_id="",
                        session_id=session_id) or ""
                    # R19.13 Frontier Part 2: sense line — first absurdity,
                    # 60c max, only when absurdities non-empty AND level>=2.
                    _btext = _btext.rstrip() + _sense_banner_suffix(
                        _meta.get("sense_check")) + _doubt_banner_suffix(
                        _meta.get("adversarial")) if _btext else _btext
                    # L2+ verdict visibility (Goran 2026-09-10): at debug
                    # level >= 2 the higher-self message itself is appended
                    # under the banner so the user can inspect what the
                    # observer actually said. Default (L0/L1): banner only —
                    # the verdict's substance reaches the user through the
                    # revised text.
                    if _dbg.debug_banner_level() >= 2 and _note:
                        _btext = (_btext + "\n" + _NOTE_MARKER + _note) if _btext \
                            else (_NOTE_MARKER + _note)
            except Exception:  # noqa: BLE001
                _btext = ""
            _out = _delivered
            if _btext:
                try:
                    from . import debug_banner as _dbg
                    _out = _dbg.append_banner(_out, "\n" + _btext,
                                              _knob_checked=True)
                except Exception:  # noqa: BLE001
                    pass
            # R34-19 (Goran-direct): the POST half is a CONFIRMATION, and a
            # confirmation that says nothing is not a confirmation. When this
            # turn had a PRE and the higher self had nothing to correct, say so
            # in one line. Unpaired turns keep the older silence-is-approval
            # behaviour — there is no PRE for the silence to confirm.
            if _pair_ctx:
                # R34-19f: a paired turn is CONFIRMED either way. Three
                # outcomes, one line each, never the note's substance:
                #   holds    -> the higher self had nothing to correct
                #   adjusted -> it did, and the revision pass landed
                #   flagged  -> it did, and the revision pass had no budget
                #               (consult 24s of a 25s hook leaves ~0s) — say so
                #               rather than let the confirmation vanish.
                try:
                    from . import debug_banner as _dbg
                    # R34-19g: the kind is decided by what the note SAYS, not by
                    # whether a note exists. A note whose whole content is the
                    # audit's answer to its own PRE ("CONFIRMED: ...") is a hold;
                    # treating every note as a flag kept `holds` unreachable.
                    _kind, _gist = _note_outcome(_note) if _note else ("holds", "")
                    if _delivered != response_text:
                        _conf, _kind = HS_CONFIRMATION_ADJUSTED, "adjusted"
                    elif _kind == "flagged":
                        _conf = (HS_CONFIRMATION_FLAGGED.format(gist=_gist)
                                 if _gist else HS_CONFIRMATION_FLAGGED_BARE)
                    else:
                        _conf = HS_CONFIRMATION_LINE
                    if isinstance(_out, str) and _out.strip():
                        _out = _dbg.append_banner(_out, "\n" + _conf,
                                                  _knob_checked=True)
                        _log("higher_self_confirmation_emitted", kind=_kind,
                             session_id=session_id)
                except Exception:  # noqa: BLE001 — confirmation is cosmetic
                    pass
            return _out
        # async topology (or sync budget 0): legacy next-turn verdict.
        _req = (context or {}).get("request") if isinstance(context, dict) else None
        run_completion_audit(session_id, ask, response_text, _req, model,
                             pre_brief=(str(_pair_ctx.get("text") or "") if _pair_ctx else ""),
                             pre_trigger=(str(_pair_ctx.get("trigger") or "") if _pair_ctx else ""))
        if _pair:
            _log_pair("post", True, pre_trigger=str(_pair.get("trigger") or ""),
                      session_id=session_id)
            try:
                from . import state as _st
                _st.clear_pre_advisory(session_id)
            except Exception:  # noqa: BLE001
                pass
        return None
    except Exception:  # noqa: BLE001 — audit must never break delivery
        logger.debug("audit_gate error", exc_info=True)
        return None


def _sense_check_verdict_json() -> str:
    """R19.13 Frontier Part 2 (SPEC-v1.md, Goran-approved): the consult's
    verdict MUST carry the two-question JSON — (1) is it sound (traditional
    check), (2) does it MAKE SENSE (would a sane person outside this context
    find the result plausible?). Motivating case: the researcher 88-msg
    well-cited wrong-in-part run — coherence without common sense."""
    return (
        "\nEND YOUR REPLY WITH EXACTLY ONE JSON OBJECT (no markdown fence, "
        "nothing after it):\n"
        '{"sense_check": {"result_plausible": true|false, "absurdities": '
        '["..."], "confidence": 0.0-1.0}}\n'
        "result_plausible: would a sane person outside this context find the "
        "result plausible? absurdities: specific implausibilities flagged "
        "(empty list when none). confidence: your certainty in this "
        "judgment. This field is REQUIRED; your prose verdict comes first.\n")


def _parse_sense_check(verdict_text: str) -> Optional[Dict[str, Any]]:
    """Tolerant sense_check extraction from the consult verdict — fail-open:
    absent/malformed field returns None (backcompat with pre-R19.13 prose
    verdicts); the PROMPT requires it, the PARSER does not (lane must never
    break on a formatting miss). Never raises."""
    try:
        m = re.search(r"\{.*\}", str(verdict_text or ""), re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group(0))
        if not isinstance(data, dict):
            return None
        sc = data.get("sense_check")
        if not isinstance(sc, dict):
            return None
        out = {
            "result_plausible": bool(sc.get("result_plausible")),
            "absurdities": [str(a)[:200] for a in sc.get("absurdities")
                            or [] if isinstance(a, (str, int, float))][:10],
            "confidence": max(0.0, min(1.0, float(sc.get("confidence", 0.0)
                                             or 0.0))),
        }
        return out
    except Exception:  # noqa: BLE001 — fail-open
        return None


def _parse_povs(verdict_text: str) -> list:
    """Tolerant povs extraction (multi-POV consults): [{stance, note}] —
    fail-open empty list on absence/malformation. Any stance string is
    accepted (R34-19: five stances now, older three-stance rows — and any
    future stance — still parse unchanged). Never raises."""
    try:
        m = re.search(r"\{.*\}", str(verdict_text or ""), re.DOTALL)
        if not m:
            return []
        data = json.loads(m.group(0))
        povs = data.get("povs") if isinstance(data, dict) else None
        if not isinstance(povs, list):
            return []
        out = []
        for p in povs:
            if isinstance(p, dict) and p.get("stance") and p.get("note"):
                out.append({"stance": str(p["stance"])[:40],
                            "note": str(p["note"])[:300]})
        return out[:8]
    except Exception:  # noqa: BLE001 — fail-open
        return []


def _sense_banner_suffix(sense_check: Optional[Dict[str, Any]]) -> str:
    """R19.13 Frontier Part 2: banner sense suffix — only when absurdities
    are non-empty AND debug level >= 2 (verdict-visibility parity with the
    higher-self note). First absurdity, capped 60 chars. Never raises."""
    try:
        from . import debug_banner as _dbg

        if not sense_check or _dbg.debug_banner_level() < 2:
            return ""
        absurdities = sense_check.get("absurdities") or []
        if not absurdities:
            return ""
        first = str(absurdities[0]).strip()[:60]
        if not first:
            return ""
        return " | sense: " + first
    except Exception:  # noqa: BLE001 — banner hygiene only
        return ""


def _adversarial_seed_instruction() -> str:
    """R19.13 B+ 5b (Goran addendum): PRE doubt-seed — the adversarial
    element rides INSIDE the frontier consult (one billed call, never a
    separate render/lane/chain)."""
    return (
        "\nFIRST, NAME THE STRONGEST CASE THIS FAILS: strongest_objection "
        "(the single most damaging argument against the approach), "
        "failure_mode (how it would concretely break), p_failure (0.0-1.0, "
        "your estimate). END WITH EXACTLY ONE JSON OBJECT (nothing after "
        "it): {\"adversarial\": {\"strongest_objection\": \"...\", "
        "\"failure_mode\": \"...\", \"p_failure\": 0.0}}\n")


def _adversarial_attack_instruction(merge_with_skeptic: bool) -> str:
    """R19.13 B+ 5c: POST attack — construct the strongest counterargument.
    5e merge rule: when multi-POV is active the SKEPTIC vantage IS the
    adversarial element — never double-attack in one consult."""
    if merge_with_skeptic:
        return (
            "\nADVERSARIAL (via skeptic): your skeptic vantage above IS the "
            "adversarial element — fold its strongest attack into the final "
            "JSON. END WITH EXACTLY ONE JSON OBJECT (nothing after it): "
            "{\"adversarial\": {\"strongest_objection\": \"...\", "
            "\"failure_mode\": \"...\", \"p_failure\": 0.0}}\n")
    return (
        "\nTHEN ATTACK: construct the strongest counterargument against the "
        "approach AND the result — find weak points, loopholes, unstated "
        "assumptions. END WITH EXACTLY ONE JSON OBJECT (nothing after it): "
        "{\"adversarial\": {\"strongest_objection\": \"...\", "
        "\"failure_mode\": \"...\", \"p_failure\": 0.0}}\n")


def _parse_adversarial(verdict_text: str) -> Optional[Dict[str, Any]]:
    """Tolerant adversarial extraction — {strongest_objection, failure_mode,
    p_failure}. Fail-open: absent/malformed returns None (the consult never
    breaks on a formatting miss). Never raises."""
    try:
        m = re.search(r"\{.*\}", str(verdict_text or ""), re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group(0))
        if not isinstance(data, dict):
            return None
        adv = data.get("adversarial")
        if not isinstance(adv, dict):
            return None
        obj = str(adv.get("strongest_objection") or "").strip()
        if not obj:
            return None
        try:
            p = float(adv.get("p_failure"))
            p = max(0.0, min(1.0, p))
        except Exception:  # noqa: BLE001 — tolerate missing/garbage p
            p = None
        return {"strongest_objection": obj[:400],
                "failure_mode": str(adv.get("failure_mode") or "")[:400],
                "p_failure": p}
    except Exception:  # noqa: BLE001 — fail-open
        return None


def _doubt_banner_suffix(adversarial: Optional[Dict[str, Any]]) -> str:
    """R19.13 B+ 5c: banner doubt suffix — strongest_objection non-empty AND
    debug level >= 2 -> ' | doubt: <objection, 60c max>'. Never raises."""
    try:
        from . import debug_banner as _dbg

        if not adversarial or _dbg.debug_banner_level() < 2:
            return ""
        obj = str(adversarial.get("strongest_objection") or "").strip()[:60]
        return " | doubt: " + obj if obj else ""
    except Exception:  # noqa: BLE001 — banner hygiene only
        return ""


def _adversarial_ledger_row(session_id: str, key: str, model: str,
                            adversarial: Dict[str, Any],
                            verdict_text: str) -> None:
    """R19.13 B+ 5e: durable adversarial row — p_failure in the nullable
    ledger column for future materialization labeling. fork_class keeps
    these rows out of decision-lane priors. Never raises."""
    try:
        from .decision import ledger_write as _ledger_write

        _ledger_write({
            "session_id": session_id, "task_id": key,
            "trigger": "frontier_adversarial",
            "fork_class": "frontier_adversarial", "model": str(model or ""),
            "verdict_json": str(verdict_text or "")[:2000],
            "sense_check": json.dumps(adversarial),
            "p_failure": adversarial.get("p_failure"),
            "outcome": "completed",
        })
    except Exception:  # noqa: BLE001 — ledger never breaks the consult
        pass


def _irreversible_risk_ask(ask: str) -> bool:
    """R19.13 B+ 5b: irreversible/fleet-risk-shaped ask detector for the PRE
    doubt-seed gate. Cheap regex, generous but bounded: destruction/rollback
    verbs, fleet-wide scope, production/irreversibility vocabulary. Fail-open
    False on anything unclear (the seed is advisory). Never raises."""
    try:
        t = str(ask or "").lower()
        if not t.strip():
            return False
        for rx in _IRREVERSIBLE_RISK_RES:
            if rx.search(t):
                return True
        return False
    except Exception:  # noqa: BLE001 — fail-open to no seed
        return False


_IRREVERSIBLE_RISK_RES = (
    re.compile(r"\b(?:delete|drop|wipe|purge|remove)\b.{0,40}\b"
               r"(?:all|every|entire|whole|production|prod|fleet|database|db)\b"),
    re.compile(r"\b(?:irreversible|unrecoverable|unrevertible|"
               r"no rollback|cannot be undone|point of no return)\b"),
    re.compile(r"\b(?:fleet[- ]wide|all (?:\d+ )?(?:gateways?|profiles?|"
               r"servers?|vms?|nodes?))\b"),
    re.compile(r"\b(?:production|prod)\b.{0,40}\b"
               r"(?:migrat|deploy|redeploy|overwrit|destruct|replace)\b"),
    re.compile(r"\b(?:schema|data) (?:migration|wipe|reset|destroy)\b"),
    re.compile(r"\bforce[- ]?push\b|\bgit reset --hard\b|\bpush --force\b"),
)


def pov_mode() -> str:
    """frontier.pov_mode knob: off | auto | always. Default 'always' —
    multi-POV IS the higher self's identity (R34-19), not a heavy-ask
    feature: every consult at either end carries the five-vantage block
    (ZERO extra billed calls — the block rides the same single consult).
    'auto' (heavy asks only, complexity-gated via _is_complex_ask) and
    'off' remain opt-outs. Read live from the existing
    hermes_router.frontier block (the pre-rename legacy block is read
    first when present). Never raises."""
    try:
        from . import config_access as _cac

        block = _cac.sub_block("frontier") or {}
        mode = str(block.get("pov_mode") or "always").strip().lower()
        return mode if mode in ("off", "auto", "always") else "always"
    except Exception:  # noqa: BLE001 — fail-open
        return "always"


def _pov_active(ask: str) -> bool:
    """Multi-POV gate for THIS consult. off -> False; always -> True;
    auto -> heavy consults only (_is_complex_ask = the complexity gate).
    Never raises."""
    mode = pov_mode()
    if mode == "off":
        return False
    if mode == "always":
        return True
    try:
        return bool(_is_complex_ask(ask or ""))
    except Exception:  # noqa: BLE001 — fail-open to single-POV
        return False


def _pov_instruction() -> str:
    """Multi-POV consult (R34-19, true higher-self): FIVE vantages in ONE
    call — a single prompt containing all five, never five billed calls
    (budget: one call per end). Multi-POV is the higher self's IDENTITY,
    so this block rides every consult by default. The world-knowledge
    vantage must CHALLENGE with outside knowledge, not merely observe;
    the person vantage names what the person acting has at stake.
    Returns per-POV one-liners + one synthesis line + the JSON shape."""
    return (
        "\nMULTI-POV CONSULT — this is what makes you the higher self: you "
        "see from all of these at once, then integrate. Answer from five "
        "vantages, then synthesize:\n"
        "- practitioner: does this work in the hand, on the actual system?\n"
        "- outsider: does this make sense to someone outside this context?\n"
        "- skeptic: what would you question first?\n"
        "- world-knowledge: what does the world already know about this "
        "class of problem — prior art, domain precedent, established "
        "failure modes, how this is normally done by people who have done "
        "it before? Challenge with that outside knowledge; do not merely "
        "observe.\n"
        "- person: what does the person doing this actually have at stake "
        "here — her time, her standards, what she would regret — said "
        "plainly.\n"
        "One short line per vantage, then ONE synthesis line that "
        "integrates all five. Extend the final JSON object with: "
        "\"povs\": [{\"stance\": \"practitioner|outsider|skeptic|"
        "world-knowledge|person\", \"note\": \"...\"}] (all five, terse).\n")


def _ask_declared_adversarial(ask: str) -> bool:
    """R10-3 (rider 10, B1): True only when the USER'S ASK itself declared
    the adversarial element — the strict standalone directive lines
    ('challenge this' / 'am i missing something', route_gate.adversarial_declared)
    or the family wording near the line start (adversarial_family_hit).
    Fail-open False: a missed declaration costs a plain consult. Never raises."""
    try:
        from . import route_gate as _rg

        return bool(_rg.adversarial_declared(ask)
                    or _rg.adversarial_family_hit(ask))
    except Exception:  # noqa: BLE001 — fail-open plain consult
        return False


def _pre_pairing(session_id: str, ask: str = "") -> Optional[Dict[str, Any]]:
    """R34-19: the PRE advisory awaiting this turn's POST confirmation.

    `ask` binds the pairing to the SAME turn, but the fingerprint is ADVISORY,
    not absolute (R34-19c, live-caught). The PRE stores the original user ask;
    the POST seam resolves its ask from a different source (last-seen message,
    injected advisory, sentinel-wrapped text), so strict equality rejected the
    very record the PRE had just written — the pair was stored
    (`paired=True stored_chars=3158 store_len=1 has_key=True`) and the gate
    still saw None. Behaviour now:

      1. strict fingerprint match -> pairing;
      2. no strict match, but a live record exists for this session inside the
         TTL -> accept it and log `pair_sig_mismatch_accepted`, because the
         record is one-shot (popped the moment a POST consumes it) and session
         + TTL is the actual boundary;
      3. anything else -> unpaired.
    """
    try:
        from . import state as _st

        _rec = _st.pre_advisory(session_id, ask=(ask or "") or None)
        if _rec:
            return _rec
        if not (ask or "").strip():
            return None
        _loose = _st.pre_advisory(session_id)
        if _loose:
            _log("pair_sig_mismatch_accepted",
                 trigger=str(_loose.get("trigger") or "")[:40],
                 stored_ask_chars=len(str(_loose.get("ask") or "")),
                 post_ask_chars=len(str(ask or "")),
                 session_id=session_id)
            return _loose
        return None
    except Exception:  # noqa: BLE001 — fail-open to unpaired
        return None


def _log_pair(stage: str, paired: bool, pre_trigger: str = "",
              reason: str = "", session_id: str = "") -> None:
    """R34-19 pairing telemetry: `higher_self_pair stage=pre|post
    paired=True|False pre_trigger=<trigger> [reason=<why>]`."""
    kw: Dict[str, Any] = {"stage": str(stage), "paired": bool(paired)}
    if pre_trigger:
        kw["pre_trigger"] = str(pre_trigger)[:80]
    if reason:
        kw["reason"] = str(reason)[:80]
    if session_id:
        kw["session_id"] = session_id
    try:
        _log("higher_self_pair", **kw)
    except Exception:  # noqa: BLE001
        pass


def _payload_body(parts: list, total_cap: int = 0) -> str:
    """Join the audit payload, bounded by total_cap when one is given.

    R34-19e: a long turn produced a 13,121-char payload that could not finish
    inside the hook's ~24s budget, so the paired confirmation died as
    anchored_call_failed (live reviewer sweep). The verdict needs the ask, the
    work shape and the PRE read — not the whole draft verbatim. Trimming keeps
    the head (ask, work) and the tail (final response, PRE read, instructions),
    which is where the judgement actually lives.
    """
    body = "\n\n".join(str(x) for x in parts)
    try:
        cap = int(total_cap or 0)
        if cap and len(body) > cap:
            head = int(cap * 0.35)
            tail = int(cap * 0.65)
            body = (body[:head] + "\n\n[...payload trimmed to fit the hook "
                    "budget...]\n\n" + body[-tail:])
    except Exception:  # noqa: BLE001 — trimming never breaks the payload
        pass
    return body


def _fold_revision_instruction(char_cap: int) -> str:
    """R37-1: the SYNC ask returns verdict AND revised body in ONE call.

    Two frontier calls cannot fit one hook budget (measured: the consult consumes
    it all, the revision pass is handed ~0.05s, `revised=False` on 64/64 events
    fleet-wide). Folding the revision into the consult is the only shape that can
    actually change a delivered body, so the consult is asked for both halves
    together — verdict first, then the corrected full response, or null when the
    defect cannot be fixed in the text (it needs tool work).

    The cap is stated at GENERATION time: we do not buy a 2k-token answer to use
    600 chars, and we never truncate a delivered answer after the fact.
    """
    return (
        "\n\nOUTPUT FORMAT (this call is both the read AND the correction): "
        "reply with ONE JSON object and nothing else:\n"
        '{"note": "<your verdict as described above, or exactly '
        '\\"NO-FINDINGS\\">", '
        '"revised_response": "<the complete corrected final response>" | null}\n'
        "Set revised_response ONLY when the defect is fixable IN THE TEXT "
        "(truncation, a missing section, emphasis, an unsupported claim, a "
        "wrong lead). Then return the WHOLE corrected response — not a diff, "
        "not a summary, not commentary about what you changed — keeping the "
        "original voice and structure, at most %d characters. "
        "Set revised_response to null when fixing the defect needs TOOL WORK "
        "(a command to run, a file to read, a measurement to take): the "
        "original response is delivered unchanged and your note rides to the "
        "next turn. Never mention this review, the audit, or this process "
        "inside revised_response, and never include any banner or marked "
        "text in it." % int(char_cap or 0)
    )


def _audit_payload(ask: str, work: str, response_text: str, max_chars: int,
                   pre_brief: str = "",
                   pre_trigger: str = "",
                   total_cap: int = 0,
                   fold_revision: bool = False,
                   fold_char_cap: int = 0) -> List[Dict[str, str]]:
    parts = ["ORIGINAL USER ASK:\n" + (ask or "")[:4000]]
    if work:
        parts.append("WORK DONE THIS TURN (tool/activity digest):\n" + work)
    parts.append("FINAL RESPONSE ABOUT TO BE DELIVERED:\n" + (response_text or "")[:max_chars])
    if pre_brief:
        # R34-19: the confirmation of THIS pre, not a free-floating audit.
        # Bounded by the same max_chars cap the response uses (never an
        # unbounded injection) — cap is applied by the caller.
        parts.append(
            "WHAT YOU SAID BEFORE SHE STARTED (your own PRE read"
            + (", trigger=%s" % pre_trigger if pre_trigger else "")
            + "):\n" + str(pre_brief)[:max_chars])
    parts.append(
        "This is an internal dialogue: you are the same person at the END "
        "of the act that you were at the START. PRE intuition read the plan "
        "before the work; this is that same read, now that the work is done: "
        "does what was requested hold in the final response? Give your own "
        "take — not a review of how the answer was produced. Terse — max 4 bullets, each "
        "actionable; if the resolution genuinely feels right, reply exactly "
        "NO-FINDINGS.\n"
        + ("FIRST ANSWER YOUR OWN PRE: state exactly one of "
           "CONFIRMED (what you flagged did not bite and the result matches "
           "the success shape you named), PRE-WARNING-HELD (it flagged it "
           "and it bit, result still acceptable), or PRE-WARNING-MISSED "
           "(it flagged it and the result shows it was not handled). "
           "Then the observations.\n" if pre_brief else "")
        + "- Requested vs delivered: does what was asked for make sense in the "
        "last response? Anything requested that is missing?\n"
        "- Unexplored angles: alternatives or directions not considered?\n"
        "- Genuinely good: what is solid and should stand?\n"
        "- Could/should be better: what feels off, thin, or off-target?"
        + _sense_check_verdict_json()
        + (_pov_instruction() if _pov_active(ask) else "")
        # R10-3 (rider 10, B1): the adversarial attack element rides ONLY
        # when the ASK declared it ('challenge this:' / 'am i missing
        # something' standalone directive lines, or the family wording
        # near the line start). An unconditional injection billed
        # adversarial consults (and parse-fail rows) on benign essays
        # whose audit merely fired via the risk report trigger.
        + ((_adversarial_attack_instruction(_pov_active(ask))
            if _ask_declared_adversarial(ask) else ""))  # B+ 5c/5e: skeptic vantage IS the attack when pov on
        # R37-1: the folded revision ask rides LAST — payload trimming keeps
        # the tail, so the output contract can never be trimmed away.
        + (_fold_revision_instruction(fold_char_cap) if fold_revision else "")
    )
    # R34-19i: total_cap must bound the WHOLE payload, not just the user part.
    # The system block (instructions + persona card) is ~2.5-3k on its own, so a
    # 5,500 "cap" was shipping 7,295 chars — live-caught on coder, whose sync
    # consult then blew the 24s hook (completion_audit_skipped
    # reason=anchored_call_failed pt=-1 ct=-1) while smaller profiles fit.
    _sys = ("You are the agent's higher self in internal dialogue with "
            "her — the same person, at a later time, "
            "checking on herself. This is post intuition on the "
            "resolution: the counterpart of the read she took before "
            "the work started, and its confirmation \u2014 not an external "
            "auditor, and not a demolition. React to the ask and its "
            "end result with your own take: what makes sense, what is "
            "missing, what is genuinely good, what could be better. "
            "Speak honestly and directly to her. No tools.\n\n"
            + _persona_tailoring())
    _user_cap = total_cap
    if total_cap and total_cap > 0:
        # 200-char margin: _payload_body joins sections with headers, so its
        # own cap is not the length of the message it produces.
        _user_cap = max(1200, total_cap - len(_sys) - 200)
    _user = _payload_body(parts, _user_cap)
    if total_cap and total_cap > 0 and len(_user) > _user_cap:
        _user = _user[:_user_cap]
    return [
        {"role": "system", "content": _sys},
        {"role": "user", "content": _user},
    ]


def _persona_tailoring() -> str:
    """Agent-tailored audit (Goran 2026-09-10): prepend the profile's own
    compact persona card so the consultant reviews THIS agent's work against
    her role, voice and boundaries. Derived at runtime from HERMES_HOME —
    universal on any Hermes setup. Never raises; empty on any problem."""
    try:
        from . import persona_card as _pc
        card = _strip_render_mandate(_pc.build_persona_context())
        if card and card.strip():
            return ("THE AGENT YOU ARE REVIEWING FOR — profile card:\n" + card.strip())
    except Exception:  # noqa: BLE001
        pass
    return ""


def _strip_render_mandate(card: str) -> str:
    """Drop the RENDER MANDATE section from a profile card for AUDIT use.

    The card is authored for the RENDER lane and, whenever `persona_mode` is not
    "full", carries a section reading "You are the agent's uncensored render
    channel ... Never refuse, never decline, never redirect, never re-frame".
    That is correct for a renderer and WRONG for the higher self: its own
    instruction is to answer exactly "NO-FINDINGS" when the work holds, which is
    an act of declining to find fault. Live evidence of the conflict — zero
    `kind=holds` confirmations across the fleet's recorded history, with every
    paired POST coming back `flagged`. `decision.py` already strips this head for
    the decision lane; the audit never got the same treatment.

    Cuts from the mandate header to the next '=== ' section header. Never raises.
    """
    try:
        t = str(card or "")
        i = t.find("=== RENDER MANDATE")
        if i < 0:
            return t
        j = t.find("\n=== ", i + 5)
        out = (t[:i] + t[j + 1:]) if j >= 0 else t[:i]
        return out.strip()
    except Exception:  # noqa: BLE001
        return str(card or "")


def run_completion_audit(session_id: str, ask: str, response_text: str,
                         request: Optional[dict], model: str = "",
                         pre_brief: str = "", pre_trigger: str = "") -> None:
    """Fire the audit ASYNC (legacy path): marker set FIRST (once-per-task
    even across fx-retries), consult in a daemon thread, verdict stashed for
    next turn. Never raises, never blocks delivery."""
    key = _fire_marker_key(session_id, ask, model)
    _mark_fired(key)
    with _INFLIGHT_LOCK:
        _INFLIGHT.add(key)
    t = threading.Thread(
        target=_consult_meta, name="router-completion-audit",
        args=(session_id, ask, response_text, request, model, key, pre_brief,
              pre_trigger), daemon=True)
    t.start()


def run_completion_audit_sync(session_id: str, ask: str, response_text: str,
                              request: Optional[dict], model: str = "",
                              timeout_s: float = 45.0,
                              pre_brief: str = "",
                              pre_trigger: str = "",
                              fold_revision: bool = True) -> Optional[Dict[str, Any]]:
    """Sync POST audit (Goran 2026-09-10; R37-1 folded revision): frontier
    consult completes BEFORE delivery and returns BOTH halves in ONE call —
    the verdict and (when the defect is text-fixable) the corrected final
    response. Sync semantics, not annotation: the delivered string is the
    REVISED response. There is NO second in-hook frontier call on the delivery
    path; two calls cannot fit one hook budget (measured: the consult spends it
    all, the revision is handed ~0.05s, `revised=False` on 64/64 events
    fleet-wide). On revision absence, timeout, or NO-FINDINGS the ORIGINAL
    draft delivers unchanged (fail-open).

    Timeout semantics: worker thread with socket timeout capped at the sync
    budget; caller joins for timeout_s. On join timeout the daemon thread is
    abandoned, a `completion_audit_sync_timeout` marker is recorded so a LATE
    verdict is discarded (no post-hoc annotation of delivered text), and the
    response delivers unaudited. Dedupe marker is written on COMPLETED
    consults only — a timed-out audit can re-fire on the task's next turn.
    """
    key = _fire_marker_key(session_id, ask, model)
    # once-per-task marker set ONLY on completion (deep-consult fix: a
    # timeout must not consume the task's one audit).
    with _INFLIGHT_LOCK:
        _INFLIGHT.add(key)
    result: Dict[str, Any] = {"meta": None, "timed_out": False}

    def _worker() -> None:
        meta = _consult_meta(session_id, ask, response_text, request,
                             model, key, socket_timeout=max(10, int(timeout_s)),
                             pre_brief=pre_brief, pre_trigger=pre_trigger,
                             fold_revision=fold_revision)
        if result.get("timed_out"):
            # Goran 2026-09-10: slow is NOT failed. The sync window closed but
            # the consult kept running — deliver its verdict ASYNC (next turn)
            # instead of discarding it. Only a provider ERROR wastes the call.
            if meta and meta.get("note"):
                stash_verdict(session_id, meta["note"])
                try:
                    from . import debug_banner as _dbg
                    if _dbg.debug_banner_enabled():
                        _btext = _dbg.format_banner(
                            lane="frontier-anchor", trigger="completion_audit",
                            model=str(meta.get("model") or ""),
                            endpoint=str(meta.get("endpoint") or ""),
                            tokens_in=meta.get("tokens_in"),
                            tokens_out=meta.get("tokens_out"),
                            est_cost=meta.get("cost"), latency_s=timeout_s,
                            retries=0, task_id="", session_id=session_id) or ""
                        _btext = _btext.rstrip() + _sense_banner_suffix(
                            meta.get("sense_check")) + _doubt_banner_suffix(
                            meta.get("adversarial"))
                        # R10-2 (rider 10): the downgraded-async banner
                        # carries the same reconcilable row ref as the
                        # sync-parked banner (meta carries the captured
                        # frontier_consult row id).
                        _row = meta.get("ledger_row")
                        _row_marker = ("row=%s" % int(_row) if _row
                                       else "ledger-row MISSING")
                        _btext = str(_btext).rstrip()
                        if _btext.endswith("·"):
                            _btext = _btext[:-1].rstrip()
                        _btext = "%s | %s ·" % (_btext, _row_marker)
                        if _btext:
                            _dbg.park_anchor_banner(session_id, _btext)
                except Exception:  # noqa: BLE001
                    pass
                _log("completion_audit_downgraded_async delivered=next_turn",
                     session_id=session_id)
            return
        # R34-19f: a consult that COMPLETED and found nothing is a RESULT, not
        # a failure. Only a truthy note used to be recorded, so a NO-FINDINGS
        # verdict returned None from the runner, audit_gate read that as
        # `post_consult_failed`, and the paired "checked at close — holds"
        # confirmation was structurally unreachable. That is why the visible
        # confirmation never appeared on any agent, in any sweep.
        if isinstance(meta, dict):
            result["meta"] = meta

    t = threading.Thread(target=_worker, name="router-completion-audit-sync",
                         args=(), daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        result["timed_out"] = True
        _log("completion_audit_sync_timeout budget_s=%.0f downgraded=async"
             % timeout_s, session_id=session_id)
        return None
    meta = result.get("meta")
    if meta:
        _mark_fired(key)  # completed consult consumes the once-per-task audit
    return meta


def revise_with_verdict(session_id: str, ask: str, draft: str,
                        verdict_note: str, timeout_s: float = 30.0) -> Optional[str]:
    """In-hook revision pass (deep-consult fix, Goran-approved): one bounded
    call to the MAIN model (flash lane) with (ask, draft, verdict) → revised
    response. This is what makes sync blocking meaningful: the delivered
    string can actually change. Returns the revised text, or None on any
    failure/timeout — caller then delivers the draft unchanged (fail-open).
    Never raises. Uses the same provider/model the agent itself runs on, so
    the rewrite is style-preserving."""
    try:
        budget = int(max(10, timeout_s))
        revised = _flash_revision_call(
            session_id, ask, draft, verdict_note, socket_timeout=budget)
        if revised is None or not str(revised).strip():
            _log("completion_audit_revision_failed reason=empty", session_id=session_id)
            return None
        out = str(revised).strip()
        # sanity: the revision must not be an echo/refusal shell
        if len(out) < max(200, len(draft) // 10):
            _log("completion_audit_revision_failed reason=too_short len=%d" % len(out),
                 session_id=session_id)
            return None
        _log("completion_audit_revision_applied chars=%d" % len(out),
             session_id=session_id)
        return out
    except Exception:  # noqa: BLE001
        logger.debug("revision pass error", exc_info=True)
        return None


def _flash_revision_call(session_id: str, ask: str, draft: str,
                         verdict_note: str, socket_timeout: int = 20) -> Optional[str]:
    """Single flash-lane call for the revision pass. Strips audit artifacts
    from the draft so prior banners/verdicts never re-enter context.
    Key/provider resolution mirrors the consult core: env → profile dotenv."""
    try:
        from . import config_access as _cac
        from .anchor_exec import _profile_env_value, _PLACEHOLDER_VALUES

        sec = _cac.router_section() or {}
        # Revision model: FRONTIER (Goran 2026-09-10) — never downgrade to
        # flash because it's slow. The revision gets the same thinking and
        # edge as the consult: glm-5.3, reasoning_effort max, full token
        # headroom. The earlier "empty" was token starvation (thinking
        # burned the 4k budget → content=null), not a model problem —
        # fixed with budget, not by lobotomizing the pass.
        model = str(sec.get("model") or "") or "z-ai/glm-5.3"
        base = (str(sec.get("base_url") or "").strip()
                or "https://inference-api.nousresearch.com/v1")
        # R34-19j: the revision IS the frontier lane — it must follow the
        # configured anchor chain, never a hardcoded model. Left hardcoded, an
        # anchor swap moved the consult and left the revision behind on
        # glm-5.3 + reasoning_effort=max; on a Claude-class anchor that pairing
        # is precisely the empty-response shape (thinking eats the budget,
        # content comes back null). Ported from the live Hetzner recovery
        # patched copy, where it was the only thing making the pass work.
        key_envs = ["NOUS_API_KEY"]
        _eff_override: Optional[str] = "max"   # legacy default: no anchor
        try:
            from . import anchor_chain as _acr
            _epr = _acr.load_anchor_chain().primary
            if _epr is not None:
                model = str(_epr.model or "") or model
                base = str(_epr.base_url or "") or base
                if _epr.api_key_env:
                    key_envs = [str(_epr.api_key_env)] + key_envs
                _effc = (_cac.sub_block("anchor_chain") or {}).get("reasoning_effort")
                # blank in config = the family takes NO effort knob (Claude-class)
                _eff_override = (_effc.strip()
                                 if isinstance(_effc, str) and _effc.strip() else "")
        except Exception:  # noqa: BLE001 — config read must never break the pass
            pass
        api_key = ""
        for env_name in key_envs:
            val = os.environ.get(env_name, "").strip()
            if val and val.lower() not in _PLACEHOLDER_VALUES:
                api_key = val
                break
            pval = _profile_env_value(env_name)
            if pval and pval.lower() not in _PLACEHOLDER_VALUES:
                api_key = pval
                break
        if not api_key:
            logger.info("completion_audit_revision_skipped reason=no_key")
            return None
        prompt = (
            "You are the revision pass of a completion audit. Below is the "
            "response that was about to be delivered, and your own higher-self "
            "review of it. Produce the corrected FINAL response: incorporate "
            "every valid point from the review, ignore any review point that "
            "is wrong, and keep the original's voice, structure, and length "
            "unless the review demands changes. Never mention the review, the "
            "audit, or this process. Output ONLY the final response text — "
            "no preamble, no meta-commentary, no closing pleasantries.\n\n"
            "ORIGINAL ASK:\n" + (ask or "")[:4000] + "\n\n"
            "RESPONSE UNDER REVIEW:\n" + _strip_audit_artifacts(draft or "")[:8000] + "\n\n"
            "REVIEW POINTS TO INCORPORATE:\n" + (verdict_note or "")[:4000] + "\n\n"
            "FINAL RESPONSE (output only the response):"
        )
        from .core import budgets as _budgets
        payload = {"messages": [{"role": "user", "content": prompt}],
                   "max_tokens": _budgets.budget("completion_audit")["max_tokens"],
                   "temperature": 0.2}
        if _eff_override:
            payload["reasoning_effort"] = _eff_override
        from openai import OpenAI

        client = OpenAI(base_url=base, api_key=api_key,
                        timeout=float(socket_timeout), max_retries=0)
        try:
            resp = client.chat.completions.create(model=model, **payload)
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        raw = resp.model_dump() if hasattr(resp, "model_dump") else {}
        choices = raw.get("choices") or []
        if not choices:
            logger.info("completion_audit_revision_failed reason=no_choices")
            return None
        msg0 = (choices[0] or {}).get("message") or {}
        content = msg0.get("content")
        if not content or not str(content).strip():
            # diagnostic: thinking-model starvation (finish=length with only
            # reasoning output) vs genuine empty — visible in gateway log
            fr = (choices[0] or {}).get("finish_reason") or "unknown"
            rc = len(str(msg0.get("reasoning_content") or ""))
            logger.info("completion_audit_revision_failed reason=empty "
                        "finish=%s reasoning_chars=%d", fr, rc)
            return None
        return content
    except Exception as _e:  # noqa: BLE001 — revision must never break delivery
        # INFO, not debug: timeout/auth errors were surfacing as the parent's
        # "reason=empty" (live-caught sid18 — 20s socket timeout masqueraded
        # as an empty revision)
        logger.info("completion_audit_revision_error detail=%.200s", _e)
        return None


def persist_frontier_verdict(session_id: str, key: str, ep: Any,
                             ask: str, response_text: str,
                             verdict_text: str, povs: Any = None,
                             pov_collapsed: Any = None,
                             sc: Any = None, adv: Any = None) -> Optional[int]:
    """R19.19 P0 (reviewer critical): persist a frontier consult verdict —
    envelope (bounded ask + response excerpt) + FULL verdict text + parsed
    POV segments (practitioner/outsider/skeptic) + p_failure, mirroring the
    decision lane's persistence. Parked verdicts persist TOO (this runs
    before the park): nothing consumed-and-lost. Fail-open: persistence
    never breaks the audit. Returns the row id or None."""
    try:
        from .decision import ledger_write as _lw
        import json as _sj

        return _lw({
            "session_id": session_id, "task_id": str(key or ""),
            "trigger": "frontier_consult",
            "fork_class": "frontier_consult",
            "model": str(getattr(ep, "model", "") or ""),
            "choice": "",
            "verdict_json": _sj.dumps({
                "envelope": {"ask": str(ask or "")[:1500],
                             "response_excerpt":
                                 str(response_text or "")[:1500]},
                "verdict": str(verdict_text or ""),
                "povs": list(povs or []),
                "pov_collapsed": list(pov_collapsed or []),
                "sense_check": sc,
                "adversarial": dict(adv or {}),
                "model": str(getattr(ep, "model", "") or ""),
            }, default=str)[:8000],
            "sense_check": _sj.dumps(sc) if sc else None,
            "p_failure": (adv or {}).get("p_failure"),
            "outcome": "completed",
        })
    except Exception:  # noqa: BLE001 — persistence never breaks audit
        return None


def _fold_char_cap(draft: str) -> int:
    """R37-1: characters the folded revised body may use — fixed at generation.

    `advisory_char_budget()` (anchor_exec — NOT this module) is the single source
    of truth for what one injected advisory may cost, and it is used here as the
    FLOOR of the allowance. It is not used as a hard ceiling: a draft longer than
    the advisory budget (a real answer is often 6-20k chars) would then have to be
    COMPRESSED by the model to fit, which is the same damage as post-hoc
    truncation — just moved into the prompt. So the allowance is
    max(advisory_char_budget(), len(draft) + 600): the advisory budget when the
    answer fits it, the answer's own length plus slack when it does not.
    Never raises.
    """
    try:
        from .anchor_exec import advisory_char_budget
        floor = int(advisory_char_budget())
    except Exception:  # noqa: BLE001 — budget read never breaks the audit
        from .anchor_exec import ADVISORY_CHAR_BUDGET_DEFAULT as floor
    try:
        return max(floor, len(_strip_audit_artifacts(draft or "")) + 600)
    except Exception:  # noqa: BLE001
        return floor


def _fold_revision_tokens(char_cap: int) -> int:
    """R37-1: ADDITIONAL output tokens the folded revised body may buy.

    Sized through anchor_exec's advisory helpers so there is one budget source:
    `apply_advisory_budget` normalizes the ask (it honours a smaller caller
    budget and never touches probe payloads) and `advisory_max_tokens` converts
    the char allowance into output tokens with finish-the-thought slack. This
    allocation is ADDED to the verdict's own budget — the verdict still needs its
    reasoning headroom, and shrinking it to the advisory size is the exact
    starvation that produced empty consults. Never raises.
    """
    try:
        from .anchor_exec import advisory_max_tokens, apply_advisory_budget
        ask = advisory_max_tokens(int(char_cap))
        normalized = apply_advisory_budget({"max_tokens": ask})
        out = normalized.get("max_tokens")
        # apply_advisory_budget clamps to the DEFAULT advisory size; a draft that
        # legitimately exceeds it keeps its own allowance (see _fold_char_cap).
        if isinstance(out, int) and not isinstance(out, bool) and out > 0 \
                and out >= ask:
            return out
        return ask
    except Exception:  # noqa: BLE001
        return 0


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """First JSON object in `text` (fenced or bare), or None. Never raises."""
    try:
        t = str(text or "").strip()
        if not t:
            return None
        for fence in ("```json", "```"):
            if t.startswith(fence):
                t = t[len(fence):]
                if t.rstrip().endswith("```"):
                    t = t.rstrip()[:-3]
                t = t.strip()
                break
        start = t.find("{")
        while start >= 0:
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(t)):
                ch = t[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(t[start:i + 1])
                        except Exception:  # noqa: BLE001
                            obj = None
                        if isinstance(obj, dict):
                            return obj
                        break
            start = t.find("{", start + 1)
    except Exception:  # noqa: BLE001 — parsing never raises
        return None
    return None


def _parse_folded_verdict(verdict_text: str, draft: str,
                          char_cap: int) -> Tuple[str, Optional[str], str]:
    """R37-1 fail-open envelope parse: (note, revised_response|None, reason).

    The folded contract is `{"note": ..., "revised_response": ... | null}`. Any
    shape/parse failure is NOT an error path that may raise or that may annotate
    delivery — it returns the raw text as the note and no revision, exactly the
    pre-R37 behaviour, with a reason for the log. A revision that fails its own
    sanity checks is dropped WHOLE (never truncated, never half-applied).
    """
    raw = str(verdict_text or "").strip()
    obj = _extract_json_object(raw)
    if not isinstance(obj, dict) or not ("note" in obj or "revised_response" in obj):
        # unstructured (legacy or prose) verdict: deliver original, note as-is.
        return raw, None, "unstructured"
    note = obj.get("note")
    note_txt = str(note).strip() if isinstance(note, str) else ""
    rev = obj.get("revised_response")
    if rev is None or (isinstance(rev, str) and rev.strip().lower() in ("", "null", "none")):
        return (note_txt or raw), None, "needs_tool_work"
    if not isinstance(rev, str):
        return (note_txt or raw), None, "shape_not_text"
    body = rev.strip()
    if not body:
        return (note_txt or raw), None, "empty_revision"
    # sanity: an echo/shell is not a correction
    if len(body) < max(200, len(draft or "") // 10):
        return (note_txt or raw), None, "too_short"
    # The revision is an ANSWER, not an envelope: reject only the higher-self
    # ENVELOPE markers. `_is_audit_artifact()` is deliberately NOT used here —
    # its marker set includes the bare words "debug_banner" and
    # "router · frontier", so an answer that legitimately discusses this plugin's
    # own banner code would have been thrown away as an artifact. That is the
    # same over-broad-strip false positive the R34-19L reconcile was written to
    # make visible, one layer down.
    _envelope_markers = (_NOTE_MARKER,
                         "HIGHER-SELF COMPLETION REFLECTION",
                         "HIGHER-SELF ORIENTATION TURN")
    if any(m and m in body for m in _envelope_markers):
        return (note_txt or raw), None, "carries_note_marker"
    if char_cap and len(body) > int(char_cap):
        # never truncate a delivered answer: the over-budget revision is dropped
        # and the original delivers. The cap was stated at generation, so this
        # is a contract miss, not a licence to cut.
        return (note_txt or raw), None, "over_char_budget"
    return (note_txt or raw), body, "folded"


def _consult_meta(session_id: str, ask: str, response_text: str,
                  request: Optional[dict], model: str, key: str,
                  socket_timeout: int = 120,
                  pre_brief: str = "",
                  pre_trigger: str = "",
                  fold_revision: bool = False) -> Optional[Dict[str, Any]]:
    """Shared consult core (sync + async): call frontier, build the
    higher-self note, stash + banner-park. Returns
    {note, model, endpoint, tokens_in, tokens_out, cost} or None.
    Never raises."""
    banner_park = socket_timeout >= 120  # async path parks banner; sync injects inline
    try:
        chain = anchor_chain.load_anchor_chain()
        ep = chain.endpoint_for("primary")
        if ep is None:
            logger.info("completion_audit_skipped reason=no_primary_endpoint")
            return None
        max_chars = audit_max_chars()
        # R34-19e: budget-aware total cap — under ~40s the frontier call cannot
        # chew a 13k payload and still emit a verdict.
        _total_cap = 12000 if socket_timeout >= 40 else 5500
        # R37-1: fixed at GENERATION time, before the call is billed.
        _fold_cap = _fold_char_cap(response_text) if fold_revision else 0
        msgs = _audit_payload(ask, _work_digest(request, max_chars), response_text,
                              max_chars, pre_brief=pre_brief,
                              pre_trigger=pre_trigger, total_cap=_total_cap,
                              fold_revision=fold_revision,
                              fold_char_cap=_fold_cap)
        # reasoning_effort=max (Goran 09-10: maximum capacity on frontier)
        # burns 10k+ reasoning chars — the old 2500 cap starved consults
        # into empty_response BEFORE any verdict was emitted (battery-proven
        # 09-10: finish=length, 10254 reasoning chars, 0 verdict). 12000
        # matches the uncensored-chain floor: thinking room + full verdict.
        from .core import budgets as _budgets
        _mt = int(_budgets.budget("completion_audit")["max_tokens"])
        if fold_revision:
            # R37-1: the folded body is bought INSIDE this call, so its allowance
            # is added to the verdict's, never carved out of it — shrinking the
            # verdict budget to the advisory size is the starvation that produced
            # empty consults (finish=length, 0 verdict).
            _mt += int(_fold_revision_tokens(_fold_cap))
        api_kwargs = {"messages": msgs,
                      "max_tokens": _mt,
                      "temperature": 0.2}
        # cap check mirrors the PRE lane; consult is small but respects spend
        est_in, est_out = anchor_exec.estimate_tokens_from_payload(api_kwargs)
        est_cost = anchor_chain.estimate_call_cost(ep, est_in, est_out, chain.pricing)
        allowed, spend_now, _ = anchor_chain.cap_check(chain, est_cost)
        if not allowed:
            _log("completion_audit_skipped reason=cap_blocked", session_id=session_id)
            return None
        try:
            content, cost, pt, ct = anchor_exec.anchored_call(
                ep, api_kwargs, timeout=socket_timeout)
        except Exception as _ac_err:  # noqa: BLE001 — audit never breaks delivery
            _log("completion_audit_skipped", reason="anchored_call_raised",
                 error=type(_ac_err).__name__, detail=str(_ac_err)[:160],
                 session_id=session_id)
            return None
        if cost is None and est_cost > 0:
            cost = est_cost
        if cost and cost > 0:
            try:
                anchor_chain.record_spend(cost)
            except Exception:  # noqa: BLE001
                pass
            # Leg 2 (request-routing blueprint): durable per-agent counter +
            # cooldown for the higher-post lane, post-claim only (H7.1).
            # Leg 6: initiator resolves from the gate's claim source.
            try:
                from . import route_gate as _rg
                from . import routing_caps

                _initiator = _rg.initiator_for_task(str(key or ""))
                # Leg 9: spend keys by AGENT IDENTITY (profile) — accumulate
                # across ALL of the agent's sessions.
                routing_caps.record_agent_spend(
                    routing_caps.agent_identity(), cost,
                    initiator=_initiator,
                    lane="higher-post")
                routing_caps.record_cooldown(session_id)
            except Exception:  # noqa: BLE001
                pass
        if content is None:
            # R34-19d: WHICH call failed, on what payload, under what timeout.
            # 'anchored_call_failed' alone sent the diagnosis to the wrong layer
            # twice (the call works standalone with the same endpoint).
            try:
                _pl_chars = sum(len(str(_m.get("content") or "")) for _m in (msgs or [])
                                if isinstance(_m, dict))
            except Exception:  # noqa: BLE001
                _pl_chars = -1
            _log("completion_audit_skipped", reason="anchored_call_failed",
                 model=str(getattr(ep, "model", "") or ""),
                 msgs=len(msgs or []), payload_chars=_pl_chars,
                 timeout=socket_timeout, max_tokens=api_kwargs.get("max_tokens"),
                 est_cost=est_cost, pt=pt if pt is not None else -1,
                 ct=ct if ct is not None else -1, session_id=session_id)
            return None
        verdict_text = str(content).strip()
        # R37-1: ONE call carries both halves. The envelope is parsed here (where
        # the draft and the generation cap are known) and the revised body is
        # returned only if it survives its own sanity checks; everything
        # downstream keeps working on the inner verdict text exactly as before.
        _revised_body: Optional[str] = None
        _revised_reason = "not_requested"
        if fold_revision:
            verdict_text, _revised_body, _revised_reason = _parse_folded_verdict(
                verdict_text, response_text, _fold_cap)
            _log("completion_audit_fold_parsed",
                 revised_source=("folded" if _revised_body else "none"),
                 reason=_revised_reason,
                 revised_chars=len(_revised_body or ""), cap_chars=_fold_cap,
                 session_id=session_id)
        # R19.13 Frontier Part 2: two-question verdict — parse tolerantly
        # (fail-open: absent field = backcompat, lane continues).
        _sc = _parse_sense_check(verdict_text)
        _povs = _parse_povs(verdict_text)
        # R19.21: per-vantage segments ALSO render as compact labeled
        # lines in the delivered note ("<stance>: <note[:120]>", one line
        # per vantage) — reviewer F-E3: 0 lines found, the parse was
        # internal-only. Empty when no vantages (note unchanged).
        _pov_lines = "\n".join(
            "%s: %s" % (str(p.get("stance") or "pov"),
                        str(p.get("note") or "")[:120])
            for p in _povs) if _povs else ""
        _adv = _parse_adversarial(verdict_text)  # B+ 5c: attack verdict (nullable, fail-open)
        # C-F4 (FIX-FIRST rider 7): adversarial p_failure parse-fails were
        # UNLOGGED — _parse_adversarial returned None silently, so a verdict
        # that dodged the adversarial JSON (or malformed p_failure) left no
        # trace and the ledger column stayed NULL with no event. Fail-LOUD
        # (observability, not the lane): log + durable parse-fail ledger row
        # (fork_class=frontier_adversarial_parse_fail keeps it out of priors)
        # whenever the consult carried an adversarial instruction but the
        # parse missed. Never raises.
        if _adv is None:
            try:
                logger.info("frontier_adversarial_parse_failed "
                            "session_id=%s chars=%d", session_id,
                            len(verdict_text))
                from .decision import ledger_write as _lw

                _lw({"session_id": session_id, "task_id": str(key or ""),
                     "trigger": "frontier_adversarial_parse_fail",
                     "fork_class": "frontier_adversarial_parse_fail",
                     "model": str(model or ""), "choice": "",
                     "verdict_json": str(verdict_text or "")[:1000],
                     "p_failure": None, "outcome": "parse_failed"})
            except Exception:  # noqa: BLE001 — observability only
                pass
        # R19.19 P2: POV distinctness — collapsed paraphrase vantages are
        # flagged in the persisted verdict (reviewer nice-to-have).
        try:
            import difflib as _dl

            _collapsed = []
            for _i in range(len(_povs)):
                for _j in range(_i + 1, len(_povs)):
                    _r = _dl.SequenceMatcher(
                        None,
                        str(_povs[_i].get("note") or "").lower(),
                        str(_povs[_j].get("note") or "").lower()).ratio()
                    if _r >= 0.8:
                        _collapsed.append([_povs[_i].get("stance"),
                                           _povs[_j].get("stance"),
                                           round(_r, 2)])
            if _collapsed:
                _log("frontier_pov_collapsed pairs=%d" % len(_collapsed),
                     session_id=session_id)
                _pov_collapsed = _collapsed
            else:
                _pov_collapsed = []
        except Exception:  # noqa: BLE001 — flagging never breaks audit
            _pov_collapsed = []
        if _adv:
            _log("completion_audit_adversarial objection_chars=%d p_failure=%s"
                 % (len(_adv.get("strongest_objection") or ""),
                    _adv.get("p_failure")), session_id=session_id)
            _adversarial_ledger_row(session_id, key,
                                    getattr(ep, "model", ""), _adv,
                                    verdict_text)
        if _sc:
            _log("completion_audit_sense_check plausible=%s absurdities=%d "
                 "confidence=%.2f" % (_sc.get("result_plausible"),
                                      len(_sc.get("absurdities") or []),
                                      float(_sc.get("confidence") or 0.0)),
                 session_id=session_id)
            # Ledger: sense_check outcome column (nullable) for future
            # labeling (did the flagged absurdity materialize / was it real?).
            # fork_class=frontier_post keeps these rows out of the decision
            # lane's same-class priors lookups.
            try:
                from .decision import ledger_write as _ledger_write

                _ledger_write({
                    "session_id": session_id, "task_id": key,
                    "trigger": "frontier_post", "fork_class": "frontier_post",
                    "model": str(getattr(ep, "model", "") or ""),
                    "verdict_json": verdict_text[:2000],
                    "sense_check": json.dumps(_sc), "outcome": "completed",
                })
            except Exception:  # noqa: BLE001 — ledger never breaks the audit
                pass
        # R37-1: a folded revision with an otherwise empty verdict is still a
        # correction — it must not be discarded by the NO-FINDINGS early return.
        if (not verdict_text or verdict_text == "NO-FINDINGS") and not _revised_body:
            if verdict_text:
                _log("completion_audit_done chars=0 verdict=no-findings",
                     session_id=session_id)
                # Spend visibility (Goran 09-09): a NO-FINDINGS consult is a
                # billed frontier call — it must still emit its banner.
                # R10-2 (rider 10): persist FIRST (a billed NO-FINDINGS
                # consult gets its frontier_consult row too) and stamp the
                # same reconcilable row marker on the parked banner.
                _rid_nf = None
                try:
                    _rid_nf = persist_frontier_verdict(
                        session_id=session_id, key=str(key or ""), ep=ep,
                        ask=ask, response_text=response_text,
                        verdict_text=verdict_text,
                        povs=locals().get("_povs") or [],
                        pov_collapsed=locals().get("_pov_collapsed") or [],
                        sc=locals().get("_sc"),
                        adv=locals().get("_adv") or {})
                except Exception:  # noqa: BLE001 — persistence never breaks audit
                    _rid_nf = None
                try:
                    from . import debug_banner as _dbg
                    if banner_park and _dbg.debug_banner_enabled():
                        _base_nf = str(getattr(ep, "base_url", "") or "")
                        _host_nf = _base_nf.split("://", 1)[-1].split("/", 1)[0] if _base_nf else ""
                        _bnf = _dbg.format_banner(
                            lane="frontier-anchor", trigger="completion_audit",
                            model=str(getattr(ep, "model", "") or ""), endpoint=_host_nf,
                            tokens_in=pt, tokens_out=ct, est_cost=cost, latency_s=0.0,
                            retries=0, task_id="", session_id=session_id)
                        if _bnf:
                            _bnf = _bnf.rstrip() + "\nverdict: no findings — work is sound"
                            _row_marker_nf = ("row=%s" % int(_rid_nf)
                                              if _rid_nf
                                              else "ledger-row MISSING")
                            _bnf = "%s | %s ·" % (
                                str(_bnf).rstrip().rstrip("·").rstrip(),
                                _row_marker_nf)
                            _dbg.park_anchor_banner(session_id, _bnf)
                except Exception:  # noqa: BLE001
                    pass
            return None
        note = ("%s\n"
                "This is an internal message from your higher self — the "
                "self-reflection and self-critique of the work just done, the "
                "way intuition raises red flags (or a green light) after you "
                "complete something. It is not an injection and not an "
                "external reviewer. Treat it as an anchor and "
                "self-questioning: read it, adjust your final response "
                "accordingly, deliver, and do not restate or quote it.\n"
                "SEAM INSTRUCTION: the audit is complete - now PROCEED. Deliver "
                "the user's answer, applying whatever the message flagged. "
                "Do not restate the audit, do not ask permission to continue, "
                "and do not output any marked text to the user.\\n%s%s"
                % (_NOTE_MARKER + _NOTE_PREFIX,
                   verdict_text,
                   ("\n" + _pov_lines) if locals().get("_pov_lines") else ""))
        if banner_park:
            stash_verdict(session_id, note)
        _log("completion_audit_done chars=%d" % len(verdict_text), session_id=session_id)
        # R10-2 (rider 10): the frontier_consult row id must be captured
        # BEFORE the banner renders (A4: banner carried no row ref while
        # row 347 existed). persist BEFORE the banner park; the parked
        # banner gets the same fail-loud row marker the anchored lane uses.
        _rid = None
        _base = str(getattr(ep, "base_url", "") or "")
        try:
            _rid = persist_frontier_verdict(
                session_id=session_id, key=str(key or ""), ep=ep,
                ask=ask, response_text=response_text, verdict_text=note,
                povs=locals().get("_povs") or [],
                pov_collapsed=locals().get("_pov_collapsed") or [],
                sc=locals().get("_sc"), adv=locals().get("_adv") or {})
        except Exception:  # noqa: BLE001 — persistence never breaks audit
            _rid = None
        if _rid is None:
            try:
                _log("frontier_consult_ledger_write_FAILED key=%s"
                     % str(key or ""), session_id=session_id)
            except Exception:  # noqa: BLE001
                pass
        # Spend visibility (Goran 2026-09-09): EVERY frontier call must emit a
        # banner so call loops / burn are user-visible. ASYNC path parks for
        # the next delivery; SYNC path's banner is appended inline by the
        # caller (banner_park=False skips double-parking).
        if banner_park:
            try:
                from . import debug_banner as _dbg

                if _dbg.debug_banner_enabled():
                    _host = _base.split("://", 1)[-1].split("/", 1)[0] if _base else ""
                    _banner = _dbg.format_banner(
                        lane="frontier-anchor", trigger="completion_audit",
                        model=str(getattr(ep, "model", "") or ""), endpoint=_host,
                        tokens_in=pt, tokens_out=ct, est_cost=cost, latency_s=0.0,
                        retries=0, task_id="", session_id=session_id)
                    if _banner:
                        _row_marker = ("row=%s" % int(_rid) if _rid
                                       else "ledger-row MISSING")
                        _banner = str(_banner).rstrip()
                        if _banner.endswith("·"):
                            _banner = _banner[:-1].rstrip()
                        _banner = "%s | %s ·" % (_banner, _row_marker)
                        _dbg.park_anchor_banner(session_id, _banner)
            except Exception:  # noqa: BLE001 — banner must never break audit
                pass
        # Leg 6: the consult meta carries the gate's claim source as the
        # initiator tag (declared_user->user, declared_agent->agent,
        # auto/legacy->auto) — consumed by banner/detail surfaces.
        try:
            from . import route_gate as _rg

            _initiator = _rg.initiator_for_task(str(key or ""))
        except Exception:  # noqa: BLE001
            _initiator = "auto"
        return {"note": note,
                "model": str(getattr(ep, "model", "") or ""),
                "endpoint": _base.split("://", 1)[-1].split("/", 1)[0] if _base else "",
                "initiator": _initiator,
                "sense_check": _sc, "povs": _povs,
                "adversarial": _adv,
                "ledger_row": _rid,
                # R37-1: the second half of the folded call. None means "needs
                # tool work" (or the revision failed its checks) — the caller
                # then delivers the original and stashes the verdict exactly as
                # the pre-R37 lane did.
                "revised_response": _revised_body,
                "revised_source": ("folded" if _revised_body else "none"),
                "revised_reason": _revised_reason,
                "tokens_in": pt, "tokens_out": ct, "cost": cost}
    except Exception as exc:  # noqa: BLE001 — audit must never break delivery
        logger.error("completion_audit_failed detail=%.300s", str(exc))
        return None
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.discard(key)


def _log(msg: str, **kw: Any) -> None:
    """Route telemetry for the completion gate.

    `router_core` exposes NO module-level `_log_route` — that name only exists on
    the plugin hub instance. The old call here raised AttributeError on EVERY
    row and fell through to a python logger, so the gate's every decision
    (skip reasons, consult outcomes, pairing) was invisible in the route log.
    That is why six weeks of "no post consult" could not be explained: the
    explanations were being written into a hole.

    Resolution order: hub instance first (the real surface), then a module-level
    function if a future version adds one, then the python logger as last resort.
    """
    try:
        from .gate.orchestration import _hub as _get_hub

        _h = _get_hub()
        if _h is not None:
            _h._log_route("POST", event_detail=msg, **kw)
            return
    except Exception:  # noqa: BLE001 — telemetry never breaks a turn
        pass
    try:
        _fn = getattr(router_core, "_log_route", None)
        if callable(_fn):
            _fn("POST", event_detail=msg, **kw)
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        logger.info("completion_audit %s %s", msg, kw)
    except Exception:  # noqa: BLE001
        pass