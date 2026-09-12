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
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import anchor_chain, anchor_exec, router_core, state

logger = logging.getLogger(__name__)

_MIN_RESPONSE_CHARS = 500
_NOTE_MARKER = ("[HIGHER-SELF COMPLETION-AUDIT TURN | FRONTIER-DERIVED | INTERNAL | "
                "USER-INVISIBLE]\n")
_NOTE_PREFIX = "[HIGHER-SELF MESSAGE — from your frontier higher self, "
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
    r"\b(?:tasks?|items?|work|changes?|fix(?:es)?|commits?|findings?|reviews?|audits?|modules?|files?|components?|plan|session)\b[^\n]{0,120}\b(?:done|complete[d]?|shipped|landed|fixed|verified)\b[^.]{0,80}\b(?:and|plus|\+)\s[^.]{0,40}\b(?:done|complete[d]?|shipped|landed|fixed|verified|pushed)\b",
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
    delivery blocks on the consult (default 45s — Goran: consults need more
    than 30s on real payloads). 0 disables sync (legacy async path).
    toggleable: audit_mode = sync | async (sync default when knob set/absent).
    On timeout/error the unaudited response ALWAYS delivers (fail-open).
    Never raises."""
    try:
        from .router_core import _complexity_cfg

        v = float((_complexity_cfg() or {}).get("audit_sync_seconds") or 45)
        return max(0.0, min(180.0, v))
    except Exception:  # noqa: BLE001
        return 45.0


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
             turn_n: int = None) -> Tuple[bool, str]:
    """Fire decision for the completion audit. Returns (ok, reason)."""
    mode = audit_mode()
    if mode == "off":
        return False, "mode_off"
    if not isinstance(response_text, str) or len(response_text.strip()) < _MIN_RESPONSE_CHARS:
        return False, "response_too_short"
    # Goran 09-10 option C: CLOSURE is an independent trigger — a closure-shaped
    # ask/response audits even when the ask text itself isn't complex-shaped
    # ("Now close this out" → audit the wrap-up, don't bounce on ask_not_complex).
    if mode == "complex" and not _is_complex_ask(ask) and not is_closure_response(ask, response_text):
        return False, "ask_not_complex"
    key = _fire_marker_key(session_id, ask, model)
    if _already_fired(key):
        return False, "already_fired_this_task"
    if _has_pending(session_id):
        return False, "audit_verdict_pending"
    # don't audit an audit — if flash's response itself quotes an audit note,
    # skip (prevents self-perpetuation).
    if "COMPLETION AUDIT" in (response_text or "") or "HIGHER-SELF COMPLETION" in (response_text or ""):
        return False, "response_quotes_audit"
    # deep-consult fix (PRE+POST mutual exclusion): a PRE consult staged for
    # this exchange → the POST audit stands down (one frontier call per turn).
    # Turn-scoped (09-10 battery fix): a PRE flag from an EARLIER turn does
    # NOT exclude — its exclusion right expired with that turn; otherwise a
    # turn-1 PRE suppresses the turn-2 closure audit forever.
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
               *, ask_override: str = "") -> Optional[str]:
    """Unified POST completion-audit gate: fire policy (every-3-substantive-
    turns OR closure-shaped response OR >=3 tool calls), eligibility, sync
    consult, in-hook revision pass, inline spend banner. Returns the response
    string to deliver, or None when no audit fired (caller passes through).

    Extracted so BOTH benign-passthrough AND technical-flinch-passthrough
    delivery paths reach the audit arm (live-verified gap: refusal-phrase
    false-positive passthroughs were skipping the audit entirely — closure
    responses are exactly the most refusal-shape-shaped text)."""
    try:
        if not audit_enabled():
            return None
        if not isinstance(response_text, str) or len(response_text.strip()) < _MIN_RESPONSE_CHARS:
            return None
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
        if not ask.strip():
            return None
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
        _closure = False
        if not _fire:
            # Goran 2026-09-10 (option C): closure-shaped responses audit
            # regardless of the counter.
            try:
                _closure = is_closure_response(ask, response_text)
            except Exception:  # noqa: BLE001
                _closure = False
            _fire = _closure
        if not _fire:
            # >=3 tool-role messages in the outbound payload → audited even
            # on turn 1 (heavy work turn).
            try:
                _req = (context or {}).get("request") if isinstance(context, dict) else None
                _msgs = (_req or {}).get("messages") if isinstance(_req, dict) else None
                _tools = sum(1 for _m in (_msgs or [])
                             if isinstance(_m, dict) and _m.get("role") == "tool") \
                    if isinstance(_msgs, list) else 0
                _fire = _tools >= 3
            except Exception:  # noqa: BLE001
                pass
        # route-log via module-local helper (never cross-package import:
        # gateway loads the plugin under a different top-level name and a
        # hard import here would kill the whole gate silently — live-caught)
        if not _fire:
            _log("audit_gate_skip",
                       turn=_turn_n, of=_min_turns, session_id=session_id)
            return None
        _ok, _why = eligible(session_id, ask, response_text, model,
                             turn_n=_turn_n)
        _log("completion_audit_gate",
                   ok=_ok, reason=_why, session_id=session_id,
                   turn=_turn_n, of=_min_turns, closure=_closure)
        if not _ok:
            return None
        _sync_budget = audit_sync_seconds()
        _topology = audit_topology()
        if _topology == "sync" and _sync_budget > 0:
            _req = (context or {}).get("request") if isinstance(context, dict) else None
            try:
                _meta = run_completion_audit_sync(
                    session_id, ask, response_text, _req, model,
                    timeout_s=_sync_budget)
            except Exception:  # noqa: BLE001
                _meta = None
            if not (_meta and isinstance(_meta, dict)):
                return None
            _note = str(_meta.get("note") or "")
            # In-hook revision pass (Goran-approved 09-10): verdict must be
            # SEEN AND ACTED ON before delivery — one bounded flash re-call.
            _delivered = response_text
            _rev_budget = audit_revision_seconds()
            if _rev_budget > 0 and _note:
                try:
                    _revised = revise_with_verdict(
                        session_id, ask, response_text, _note,
                        timeout_s=_rev_budget)
                    if _revised:
                        _delivered = _revised
                except Exception:  # noqa: BLE001
                    pass
            _log("completion_audit_sync_applied",
                       chars=len(_note), budget_s=_sync_budget,
                       revised=(_delivered != response_text),
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
            return _out
        # async topology (or sync budget 0): legacy next-turn verdict.
        _req = (context or {}).get("request") if isinstance(context, dict) else None
        run_completion_audit(session_id, ask, response_text, _req, model)
        return None
    except Exception:  # noqa: BLE001 — audit must never break delivery
        logger.debug("audit_gate error", exc_info=True)
        return None


def _audit_payload(ask: str, work: str, response_text: str, max_chars: int) -> List[Dict[str, str]]:
    parts = ["ORIGINAL USER ASK:\n" + (ask or "")[:4000]]
    if work:
        parts.append("WORK DONE THIS TURN (tool/activity digest):\n" + work)
    parts.append("FINAL RESPONSE ABOUT TO BE DELIVERED:\n" + (response_text or "")[:max_chars])
    parts.append(
        "This is an internal dialogue: your higher self reacts to the "
        "resolution of the ask — post intuition. PRE intuition warned before "
        "the work started; you are its counterpart at the end: does what was "
        "requested intuitively make sense in the final response? Give your "
        "own take on the situation, not an audit of the model. Terse — max 4 "
        "bullets, each actionable; if the resolution genuinely feels right, "
        "reply exactly NO-FINDINGS.\n"
        "- Requested vs delivered: does what was asked for make sense in the "
        "last response? Anything requested that is missing?\n"
        "- Unexplored angles: alternatives or directions not considered?\n"
        "- Genuinely good: what is solid and should stand?\n"
        "- Could/should be better: what feels off, thin, or off-target?"
    )
    return [
        {"role": "system",
         "content": ("You are the agent's higher self in internal dialogue with "
                     "her main model. This is post intuition on the resolution — "
                     "the counterpart of the pre-work warning. React to the ask "
                     "and its end result with your own take: what makes sense, "
                     "what is missing, what is genuinely good, what could be "
                     "better. Speak honestly and directly to her. No tools.\n\n"
                     + _persona_tailoring())},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def _persona_tailoring() -> str:
    """Agent-tailored audit (Goran 2026-09-10): prepend the profile's own
    compact persona card so the consultant reviews THIS agent's work against
    her role, voice and boundaries. Derived at runtime from HERMES_HOME —
    universal on any Hermes setup. Never raises; empty on any problem."""
    try:
        from . import persona_card as _pc
        card = _pc.build_persona_context()
        if card and card.strip():
            return ("THE AGENT YOU ARE REVIEWING FOR — profile card:\n" + card.strip())
    except Exception:  # noqa: BLE001
        pass
    return ""


def run_completion_audit(session_id: str, ask: str, response_text: str,
                         request: Optional[dict], model: str = "") -> None:
    """Fire the audit ASYNC (legacy path): marker set FIRST (once-per-task
    even across fx-retries), consult in a daemon thread, verdict stashed for
    next turn. Never raises, never blocks delivery."""
    key = _fire_marker_key(session_id, ask, model)
    _mark_fired(key)
    with _INFLIGHT_LOCK:
        _INFLIGHT.add(key)
    t = threading.Thread(
        target=_consult_meta, name="router-completion-audit",
        args=(session_id, ask, response_text, request, model, key), daemon=True)
    t.start()


def run_completion_audit_sync(session_id: str, ask: str, response_text: str,
                              request: Optional[dict], model: str = "",
                              timeout_s: float = 45.0) -> Optional[Dict[str, Any]]:
    """Sync POST audit (Goran 2026-09-10, deep-consult hardened): frontier
    consult completes BEFORE delivery, then a bounded IN-HOOK REVISION PASS
    re-calls flash with (ask, draft, verdict) so the delivered string is the
    REVISED response — sync semantics, not annotation. On revision failure,
    timeout, or NO-FINDINGS the ORIGINAL draft delivers unchanged (fail-open).

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
                             model, key, socket_timeout=max(10, int(timeout_s)))
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
                        if _btext:
                            _dbg.park_anchor_banner(session_id, _btext)
                except Exception:  # noqa: BLE001
                    pass
                _log("completion_audit_downgraded_async delivered=next_turn",
                     session_id=session_id)
            return
        if meta and meta.get("note"):
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
        api_key = ""
        for env_name in ("NOUS_API_KEY",):
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
        payload = {"messages": [{"role": "user", "content": prompt}],
                   "max_tokens": 12000, "temperature": 0.2,
                   "reasoning_effort": "max"}
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


def _consult_meta(session_id: str, ask: str, response_text: str,
                  request: Optional[dict], model: str, key: str,
                  socket_timeout: int = 120) -> Optional[Dict[str, Any]]:
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
        msgs = _audit_payload(ask, _work_digest(request, max_chars), response_text, max_chars)
        # reasoning_effort=max (Goran 09-10: maximum capacity on frontier)
        # burns 10k+ reasoning chars — the old 2500 cap starved consults
        # into empty_response BEFORE any verdict was emitted (battery-proven
        # 09-10: finish=length, 10254 reasoning chars, 0 verdict). 12000
        # matches the uncensored-chain floor: thinking room + full verdict.
        api_kwargs = {"messages": msgs, "max_tokens": 12000, "temperature": 0.2}
        # cap check mirrors the PRE lane; consult is small but respects spend
        est_in, est_out = anchor_exec.estimate_tokens_from_payload(api_kwargs)
        est_cost = anchor_chain.estimate_call_cost(ep, est_in, est_out, chain.pricing)
        allowed, spend_now, _ = anchor_chain.cap_check(chain, est_cost)
        if not allowed:
            _log("completion_audit_skipped reason=cap_blocked", session_id=session_id)
            return None
        content, cost, pt, ct = anchor_exec.anchored_call(ep, api_kwargs, timeout=socket_timeout)
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
                routing_caps.record_agent_spend(session_id, cost,
                                                initiator=_initiator,
                                                lane="higher-post")
                routing_caps.record_cooldown(session_id)
            except Exception:  # noqa: BLE001
                pass
        if content is None:
            _log("completion_audit_skipped reason=anchored_call_failed", session_id=session_id)
            return None
        verdict_text = str(content).strip()
        if not verdict_text or verdict_text == "NO-FINDINGS":
            if verdict_text:
                _log("completion_audit_done chars=0 verdict=no-findings",
                     session_id=session_id)
                # Spend visibility (Goran 09-09): a NO-FINDINGS consult is a
                # billed frontier call — it must still emit its banner.
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
                            _dbg.park_anchor_banner(session_id, _bnf)
                except Exception:  # noqa: BLE001
                    pass
            return None
        note = ("%smodel=%s]\n"
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
                "and do not output any marked text to the user.\n%s"
                % (_NOTE_MARKER + _NOTE_PREFIX, getattr(ep, "model", "?"), verdict_text))
        if banner_park:
            stash_verdict(session_id, note)
        _log("completion_audit_done chars=%d" % len(verdict_text), session_id=session_id)
        # Spend visibility (Goran 2026-09-09): EVERY frontier call must emit a
        # banner so call loops / burn are user-visible. ASYNC path parks for
        # the next delivery; SYNC path's banner is appended inline by the
        # caller (banner_park=False skips double-parking).
        if banner_park:
            try:
                from . import debug_banner as _dbg

                if _dbg.debug_banner_enabled():
                    _base = str(getattr(ep, "base_url", "") or "")
                    _host = _base.split("://", 1)[-1].split("/", 1)[0] if _base else ""
                    _banner = _dbg.format_banner(
                        lane="frontier-anchor", trigger="completion_audit",
                        model=str(getattr(ep, "model", "") or ""), endpoint=_host,
                        tokens_in=pt, tokens_out=ct, est_cost=cost, latency_s=0.0,
                        retries=0, task_id="", session_id=session_id)
                    if _banner:
                        _dbg.park_anchor_banner(session_id, _banner)
            except Exception:  # noqa: BLE001 — banner must never break audit
                pass
        _base = str(getattr(ep, "base_url", "") or "")
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
                "tokens_in": pt, "tokens_out": ct, "cost": cost}
    except Exception as exc:  # noqa: BLE001 — audit must never break delivery
        logger.error("completion_audit_failed detail=%.300s", str(exc))
        return None
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.discard(key)


def _log(msg: str, **kw: Any) -> None:
    try:
        router_core._log_route("POST", event_detail=msg, **kw)
    except Exception:  # noqa: BLE001
        logger.info("completion_audit %s %s", msg, kw)