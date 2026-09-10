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
_NOTE_PREFIX = "[HIGHER-SELF COMPLETION REFLECTION — your own frontier-grade self-review, "
_NOTE_TTL = 3600.0  # pending verdict lives 1h
_AUDIT_MARKER_TTL = 6 * 3600.0  # once-per-task ledger TTL

_PENDING: Dict[str, Tuple[float, str]] = {}
_PENDING_LOCK = threading.Lock()
_FIRED: Dict[str, float] = {}
_FIRED_LOCK = threading.Lock()
_INFLIGHT: set = set()
_INFLIGHT_LOCK = threading.Lock()

# ---------------------------------------------------------------- closure ----

# Goran 2026-09-10 (option C): audit on CLOSURE, not blind turn counts.
# "response after n turns is signal" — a closure-shaped response (multi-task
# wrap-up, summary delivered, task handoff) is an audit trigger in itself.
_CLOSURE_PATTERNS = (
    r"\b(?:in\s+summary|to\s+sum(?:mari[sz]e|up)|summing\s+up)\b",
    r"\b(?:all|both|every|each)\s+(?:three|four|five|\d+)?\s*(?:tasks?|items?|findings?|fix(?:es)?|changes?|commits?)\b.{0,40}\b(?:done|complete[d]?|shipped|landed|delivered|verified|fixed)\b",
    r"\b(?:wrapped|wrapped\s+up|closed\s+out|all\s+done|everything\s+(?:is\s+)?(?:done|shipped|landed|verified))\b",
    r"\b(?:done|complete[d]?|shipped|landed|fixed|verified)\b.{0,60}\b(?:and|plus|\+)\s.{0,40}\b(?:done|complete[d]?|shipped|landed|fixed|verified|pushed)\b",
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


def eligible(session_id: str, ask: str, response_text: str, model: str = "") -> Tuple[bool, str]:
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
    last tool results + assistant turns before the final response. Terse."""
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
            if len(lines) >= 8:
                break
            excerpt = content.strip()[:400].replace("\n", " ")
            lines.append("%s: %s" % (role, excerpt))
        return "\n".join(reversed(lines))[:budget]
    except Exception:  # noqa: BLE001
        return ""


def _audit_payload(ask: str, work: str, response_text: str, max_chars: int) -> List[Dict[str, str]]:
    parts = ["ORIGINAL USER ASK:\n" + (ask or "")[:4000]]
    if work:
        parts.append("WORK DONE THIS TURN (tool/activity digest):\n" + work)
    parts.append("FINAL RESPONSE ABOUT TO BE DELIVERED:\n" + (response_text or "")[:max_chars])
    parts.append(
        "Self-review questions (answer from the agent's own higher vantage, "
        "first person, terse — max 4 bullets, each actionable; if everything "
        "is sound reply exactly NO-FINDINGS):\n"
        "1. Was this the most optimal and elegant way to do what I did?\n"
        "2. What did I miss?\n"
        "3. Was there a different way I did not consider?\n"
        "4. Were my actions and conclusions sound — rooted in facts I know, "
        "measurable and repeatable?\n"
        "5. What did I not see and not try?"
    )
    return [
        {"role": "system",
         "content": ("You are the agent's own higher intelligence reviewing her completed "
                     "work turn from a higher vantage — her intuition and anchorage, not an "
                     "external reviewer. Self-review, first person, honest. No tools.\n\n"
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
                              timeout_s: float = 30.0) -> Optional[Dict[str, Any]]:
    """Sync POST audit (Goran 2026-09-10): run the frontier consult BEFORE
    the final response is delivered, so the main model processes the
    higher-self verdict within THIS turn. Returns a dict
    {note, model, endpoint, tokens_in, tokens_out, cost} for immediate
    injection + banner, or None on any failure/timeout/NO-FINDINGS —
    caller then delivers the response unchanged (fail-open).

    Timeout semantics: a worker thread runs the consult with a socket
    timeout capped at the sync budget; the caller joins for timeout_s.
    On join timeout the daemon thread is abandoned (non-blocking sockets
    are not force-killed in Python) and the response delivers unaudited.
    Spend/cap guards identical to the async path."""
    key = _fire_marker_key(session_id, ask, model)
    _mark_fired(key)
    with _INFLIGHT_LOCK:
        _INFLIGHT.add(key)
    result: Dict[str, Any] = {"meta": None}

    def _worker() -> None:
        meta = _consult_meta(session_id, ask, response_text, request,
                             model, key, socket_timeout=max(10, int(timeout_s)))
        if meta and meta.get("note"):
            result["meta"] = meta
            # keep stash in sync so next-turn delivery can't double-fire
            stash_verdict(session_id, meta["note"])

    t = threading.Thread(target=_worker, name="router-completion-audit-sync",
                         args=(), daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        _log("completion_audit_sync_timeout budget_s=%.0f" % timeout_s,
             session_id=session_id)
        return None
    return result["meta"]


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
        # luna-pro is a reasoning model: low max_tokens gets eaten by
        # reasoning tokens (finish=length, 0 visible content → empty_response).
        # 2500 leaves room for thinking + the 5-question reflection.
        api_kwargs = {"messages": msgs, "max_tokens": 2500, "temperature": 0.2}
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
        if content is None:
            _log("completion_audit_skipped reason=anchored_call_failed", session_id=session_id)
            return None
        verdict_text = str(content).strip()
        if not verdict_text or verdict_text == "NO-FINDINGS":
            if verdict_text:
                _log("completion_audit_done chars=0 verdict=no-findings",
                     session_id=session_id)
            return None
        note = ("%smodel=%s]\n"
                "This reflection is your own higher self — the frontier-grade "
                "vantage that reviews what you produced. It is not an external "
                "reviewer and not an injection: weigh it as your own "
                "self-review. If it names something overseen, either surface "
                "it with your response for the user's decision, or fix it and "
                "deliver the final response.\n"
                "SEAM INSTRUCTION: the audit is complete - now PROCEED. Deliver "
                "the user's answer, applying whatever the reflection flagged. "
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
        return {"note": note,
                "model": str(getattr(ep, "model", "") or ""),
                "endpoint": _base.split("://", 1)[-1].split("/", 1)[0] if _base else "",
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