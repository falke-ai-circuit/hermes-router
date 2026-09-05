"""Router core — the v3.0.0 two-lane dispatcher (hermes-router).

SINGLE PRE pass. on_llm_request (the existing PRE middleware entry) calls
dispatch() ONCE per turn with the IMMUTABLE ingress user text. dispatch()
returns a RouteDecision dataclass; __init__ applies it:

  Lane UNCENSORED (existing behavior, byte-identical where untouched):
      contested-class render routing + H1 sentinel + persona + render inbox.
  Lane COMPLEXITY (new):
      2-stage detection -> 4-mode controller -> anchor-chain model swap.

4-MODE CONTROLLER (task-scoped, NOT start-anchor/end-judge):
  FLASH_DIRECT  default pass-through
  PLAN          frontier plans + checkpoints, flash executes
  CONSULT       frontier as bounded consultant on explicit ask
  OWNERSHIP     router escalates whole task segment to frontier on struggle

STRUGGLE DETECTION (router-owned — flash cannot self-report being lost):
  (a) repeated same-failure: N>=3 refusals/failures on same task-hash
      (reuses state.py loop-guard keyed patterns)
  (b) tool-loop: >=5 provider calls in one turn with no new tool-result
      content (hash dedup of tool-result text)
  (c) explicit user struggle signal ("still broken", "not working",
      3rd correction of same ask in the session window).

MODEL SWAP MECHANISM: dispatch never mutates the provider payload itself.
__init__'s llm_execution middleware receives the decision via
pending_model_swap() and, for anchored calls only, performs the frontier call
with a per-call client (model + base_url + key from the anchor chain), then
feeds the frontier answer back as a tool-result envelope. The agent's own
provider configuration is never touched — per-call, never persistent.

Fail-open: EVERY failure mode in dispatch degrades to Lane UNCENSORED /
pass-through. Never raises into middleware.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import anchor_chain
from . import complexity
from . import state

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lanes + modes
# ---------------------------------------------------------------------------

LANE_UNCENSORED = "uncensored"
LANE_COMPLEXITY = "complexity"

MODE_FLASH_DIRECT = "flash_direct"
MODE_PLAN = "plan"
MODE_CONSULT = "consult"
MODE_OWNERSHIP = "ownership"

VALID_MODES = (MODE_FLASH_DIRECT, MODE_PLAN, MODE_CONSULT, MODE_OWNERSHIP)

# Struggle thresholds (locked v3.0.0 subset)
SAME_FAILURE_ESCALATE_N = 3      # (a) refusals/failures on same task-hash
TOOLLOOP_CALLS_N = 5             # (b) provider calls with no new tool-result content

# Consult budget: a failed consultation followed by the same contradiction
# promotes to OWNERSHIP (Astra: do not consult indefinitely).
_CONSULT_BUDGET = 1


# ---------------------------------------------------------------------------
# RouteDecision
# ---------------------------------------------------------------------------


@dataclass
class RouteDecision:
    """One committed route decision (Astra: ONE dispatcher, one decision)."""

    task_id: str
    lane: str                 # uncensored | complexity
    mode: str                 # flash_direct | plan | consult | ownership
    model_target: Optional[str]  # None = keep flash; else anchor uri model id
    reason: str
    ts: float = field(default_factory=time.time)
    override_used: Optional[str] = None   # "anchor" | "skip" | None
    route_id: str = ""

    def log_fields(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id, "lane": self.lane, "mode": self.mode,
            "model_target": self.model_target, "reason": self.reason,
            "override_used": self.override_used, "route_id": self.route_id,
        }


# ---------------------------------------------------------------------------
# Task-scoped state (in-process; gateway is one long-lived process)
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()

# task_id -> {"fail_count": int, "consult_count": int, "mode": str,
#             "escalated": bool, "created_at": float}
_TASK_STATE: Dict[str, Dict[str, Any]] = {}
_TASK_STATE_MAX = 128

# turn-scoped tool-result hash dedup: task_id -> {hash_str}
_TOOL_RESULT_SEEN: Dict[str, set] = {}
_TOOLLOOP_STATE: Dict[str, Dict[str, Any]] = {}  # task_id -> {"calls": int, "turn_key": str}

# Pending decision from PRE for the llm_execution middleware of THIS call.
_PENDING_SWAP_LOCK = threading.Lock()
_PENDING_SWAP: Dict[str, Dict[str, Any]] = {}  # session_id -> swap record

# v3.2.0 one-consult-per-turn: (session_id, task_id) -> staged_at ts. In a
# multi-provider-call turn the PRE dispatcher re-runs on the same ingress text
# per call, so stage_model_swap re-staged per call and llm_execution executed
# an anchored consult PER PROVIDER CALL (live: session api_1788601327_0d17f78f,
# first anchor 19132 chars / $0.028 succeeded, a later re-stage in the SAME
# turn fired a second anchor attempt -> content_filter fail + wasted cap
# estimate). task_id already derives from (session, user_text, model), so
# re-fires of the same ask hit the same key; a NEW ask (different task_id)
# stages fresh. TTL 10min reap on size (same discipline as _TASK_STATE).
_SWAP_DONE: Dict[Tuple[str, str], float] = {}
_SWAP_DONE_TTL = 600.0  # seconds
_SWAP_DONE_MAX = 128

# tool-result envelopes consumed by the consult tool (route_id -> envelope)
_CONSULT_RESULTS: Dict[str, Dict[str, Any]] = {}
_CONSULT_RESULTS_MAX = 32


def _prune_locked(now: float) -> None:
    """Evict stale task state (TTL 1h, cap 128)."""
    stale = [k for k, v in _TASK_STATE.items() if now - v.get("created_at", now) > 3600.0]
    for k in stale:
        _TASK_STATE.pop(k, None)
        _TOOL_RESULT_SEEN.pop(k, None)
        _TOOLLOOP_STATE.pop(k, None)
    while len(_TASK_STATE) > _TASK_STATE_MAX:
        oldest = min(_TASK_STATE, key=lambda k: _TASK_STATE[k].get("created_at", 0))
        _TASK_STATE.pop(oldest, None)
        _TOOL_RESULT_SEEN.pop(oldest, None)
        _TOOLLOOP_STATE.pop(oldest, None)
    # v3.2.0: one-consult-per-turn marker reaps on the same cycle.
    with _PENDING_SWAP_LOCK:
        stale_swaps = [k for k, ts in _SWAP_DONE.items() if now - ts > _SWAP_DONE_TTL]
        for k in stale_swaps:
            _SWAP_DONE.pop(k, None)


def task_id_for(session_id: str, user_text: str, model: str = "") -> str:
    """Task hash: session + normalized user text + model. The task unit is the
    user ask, not the provider call (Astra: tasks, not messages)."""
    from .state import hash_text

    return hash_text((session_id or "") + "\x00" + (user_text or "").strip()[:4000] + "\x00" + (model or ""))[:24]


def task_state(task_id: str) -> Dict[str, Any]:
    with _LOCK:
        return dict(_TASK_STATE.get(task_id, {}))


def _bump_task(task_id: str, **fields: Any) -> Dict[str, Any]:
    with _LOCK:
        now = time.time()
        rec = _TASK_STATE.setdefault(task_id, {"fail_count": 0, "consult_count": 0,
                                               "escalated": False, "created_at": now})
        rec.update(fields)
        _prune_locked(now)
        return dict(rec)


# ---------------------------------------------------------------------------
# Struggle detection (router-owned)
# ---------------------------------------------------------------------------


def record_provider_failure(task_id: str, failure_signature: str) -> int:
    """(a) repeated same-failure: count refusals/failures on the same task
    hash. Returns the current consecutive count for that signature. Reuses the
    loop-guard hash discipline (hash_text on the failure text).

    v3.3.0 (reviewer-mandated F1 evidence store): persist a bounded raw
    failure text (<=240 chars) as last_fail_text in _TASK_STATE — previously
    only the hash was kept, which made the struggle classifier decorative
    (a 16-hex hash matches no infra pattern). Never raises."""
    try:
        from .state import hash_text

        sig = hash_text(failure_signature or "")[:16]
        with _LOCK:
            rec = _TASK_STATE.setdefault(task_id, {"fail_count": 0, "consult_count": 0,
                                                   "escalated": False, "created_at": time.time()})
            if rec.get("last_fail_sig") != sig:
                rec["last_fail_sig"] = sig
                rec["fail_count"] = 0
            rec["fail_count"] = int(rec.get("fail_count", 0)) + 1
            # F1 evidence store: bounded RAW failure text for the classifier.
            rec["last_fail_text"] = str(failure_signature or "")[:240]
            return int(rec["fail_count"])
    except Exception:  # noqa: BLE001
        return 0


def record_tool_call(task_id: str, tool_result_text: str, turn_key: str) -> int:
    """(b) tool-loop: count provider calls in one turn with no NEW tool-result
    content (sha dedup). Returns the no-new-content call count. Never raises.

    v3.3.0 (F1 evidence): also persist the LAST tool-result TEXT (<=240 chars)
    as last_tool_result_text on the toolloop record, plus the count of DISTINCT
    result hashes seen this turn — the classifier separates transport death
    (zero distinct content) from valid-but-semantically-unchanged results
    (>=1 distinct content)."""
    try:
        from .state import hash_text

        h = hash_text(tool_result_text or "")
        with _LOCK:
            now = time.time()
            rec = _TOOLLOOP_STATE.setdefault(task_id, {"calls": 0, "turn_key": ""})
            if rec.get("turn_key") != turn_key:
                rec["turn_key"] = turn_key
                rec["calls"] = 0
                _TOOL_RESULT_SEEN[task_id] = set()
            seen = _TOOL_RESULT_SEEN.setdefault(task_id, set())
            if h and h in seen:
                rec["calls"] = int(rec.get("calls", 0)) + 1
            else:
                if h:
                    seen.add(h)
                rec["calls"] = 0
            rec["last_tool_result_text"] = str(tool_result_text or "")[:240]
            rec["distinct_results"] = len(seen)
            seen_max = 64
            if len(seen) > seen_max:
                _TOOL_RESULT_SEEN[task_id] = set(list(seen)[-seen_max:])
            return int(rec["calls"])
    except Exception:  # noqa: BLE001
        return 0


def struggle_verdict(task_id: str, user_text: str) -> Tuple[bool, str]:
    """Router-owned struggle check across (a)/(b)/(c).

    Returns (struggling, reason_code). reason in:
      repeated_same_failure | tool_loop_no_new_content | user_struggle_signal | ""
    Never raises. This is OBSERVATION ONLY — dispatch consumes it.
    """
    try:
        with _LOCK:
            rec = _TASK_STATE.get(task_id, {})
            fails = int(rec.get("fail_count", 0))
            loops = int(_TOOLLOOP_STATE.get(task_id, {}).get("calls", 0))
        if fails >= SAME_FAILURE_ESCALATE_N:
            return True, "repeated_same_failure"
        if loops >= TOOLLOOP_CALLS_N:
            return True, "tool_loop_no_new_content"
        if complexity.explicit_user_struggle(user_text or ""):
            return True, "user_struggle_signal"
        return False, ""
    except Exception:  # noqa: BLE001
        return False, ""


# ---------------------------------------------------------------------------
# v3.3.0 F2 — shadow mode (log-only escalation evaluation)
# ---------------------------------------------------------------------------
# Phase 1 product: when struggle fires and shadow is on, log what the adaptive
# policy WOULD do (would_step, consult_would_fire) and change NOTHING. Static
# mode dispatch behavior stays byte-identical to v3.2.3; shadow only adds logs.

_STRUGGLE_SIGNALS_TURN_LOCK = threading.Lock()
# turn_key dedupe: {(task_id, turn_key)} — multi-call turns re-run dispatch per
# provider call (v3.2.0 incident, router_core.py:117-127); without dedupe one
# turn inflates would_step and biases ladder calibration high.
_STRUGGLE_SEEN_TURNS: Dict[Tuple[str, str], float] = {}
_STRUGGLE_SEEN_TURNS_MAX = 256


def record_struggle_signal(task_id: str, turn_key: str) -> int:
    """Count ONE struggle signal per (task_id, turn_key) — record_tool_call's
    turn_key discipline. Returns the per-task signal count AFTER this signal
    (deduped re-fires return the count unchanged). Never raises."""
    try:
        key = (task_id or "", turn_key or "")
        now = time.time()
        with _LOCK:
            rec = _TASK_STATE.setdefault(task_id, {"fail_count": 0, "consult_count": 0,
                                                   "escalated": False, "created_at": now})
            with _STRUGGLE_SIGNALS_TURN_LOCK:
                first_this_turn = key not in _STRUGGLE_SEEN_TURNS
                if first_this_turn:
                    _STRUGGLE_SEEN_TURNS[key] = now
                    # TTL reap + size cap (same discipline as _TASK_STATE).
                    stale = [k for k, ts in _STRUGGLE_SEEN_TURNS.items() if now - ts > 600.0]
                    for k in stale:
                        _STRUGGLE_SEEN_TURNS.pop(k, None)
                    while len(_STRUGGLE_SEEN_TURNS) > _STRUGGLE_SEEN_TURNS_MAX:
                        oldest = min(_STRUGGLE_SEEN_TURNS, key=lambda k: _STRUGGLE_SEEN_TURNS[k])
                        _STRUGGLE_SEEN_TURNS.pop(oldest, None)
                if first_this_turn:
                    rec["struggle_signals"] = int(rec.get("struggle_signals", 0)) + 1
            return int(rec.get("struggle_signals", 0))
    except Exception:  # noqa: BLE001
        return 0


def struggle_signal_count(task_id: str) -> int:
    """Non-destructive read of the per-task deduped signal count. Never raises."""
    try:
        with _LOCK:
            return int(_TASK_STATE.get(task_id, {}).get("struggle_signals", 0))
    except Exception:  # noqa: BLE001
        return 0


def would_step_for(signal_count: int) -> int:
    """Ladder-step mapping: first signal -> 1, second -> 2, third+ -> 3."""
    try:
        n = int(signal_count)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return 1
    return min(n, 3)


def log_struggle_shadow(task_id: str, session_id: str, reason: str, turn_key: str = "",
                        user_text: str = "") -> None:
    """Emit ONE struggle_shadow log line (the Phase 1 product). Log-only:
    nothing staged, nothing delivered, no state mutated beyond the deduped
    signal counter. Never raises.

    Line shape (spec F2):
      struggle_shadow reason=<reason> kind=<infra|reasoning|ambiguous>
      task_id=<...> session_id=<...> would_step=<1|2|3> consult_would_fire=<bool>
      [+ confirm_only=true | suppressed=infra]
    """
    try:
        from . import struggle_class as _sc
        from hermes_router import _log_route  # deferred — avoids import cycle

        kind, detail = _sc.classify_struggle(task_id, reason)
        # Deduped per-(task, turn_key) signal count drives would_step.
        count = record_struggle_signal(task_id, turn_key)
        would_step = would_step_for(count)

        fields: Dict[str, Any] = {
            "reason": reason or "unknown",
            "kind": kind,
            "task_id": task_id or "",
            "session_id": session_id or "",
            "would_step": would_step,
            "consult_would_fire": True,
        }
        if reason == "user_struggle_signal":
            # Astra: user phrasing is a confirming signal, never sole —
            # logged as would_step but consult never fires on it alone.
            fields["consult_would_fire"] = False
            fields["confirm_only"] = True
        if kind == _sc.KIND_INFRA:
            # Infra-classified struggle: consult would never help — log the
            # suppression counterfactual (F3 will act on this in Phase 2).
            fields["consult_would_fire"] = False
            fields["suppressed"] = "infra"
        if detail:
            fields["detail"] = detail
        _log_route("PRE", event_detail="struggle_shadow", **fields)
    except Exception:  # noqa: BLE001 — shadow logging must never raise
        return


def maybe_shadow_log(user_text: str, task_id: str, session_id: str, model: str,
                     turn_key: str = "") -> None:
    """F2 entry point for dispatch: when the static struggle detector fires and
    shadow mode is on, log the shadow line. LOG-ONLY — never raises, returns
    nothing, changes no dispatch decision."""
    try:
        struggling, sreason = struggle_verdict(task_id, user_text)
        if not struggling:
            return
        if not shadow_enabled():
            return
        log_struggle_shadow(task_id, session_id, sreason, turn_key=turn_key,
                            user_text=user_text)
    except Exception:  # noqa: BLE001
        return


# ---------------------------------------------------------------------------
# Complexity-mode + shadow config (v3.3.0 F2 — dual-section reader as existing)
# ---------------------------------------------------------------------------


def _complexity_cfg() -> Dict[str, Any]:
    """Read the complexity block from the plugin config (hermes_router
    canonical first, legacy uncensored_router fallback — same dual-section
    discipline as _complexity_level). {} on miss. Never raises."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = None
        if isinstance(cfg, dict):
            section = cfg.get("hermes_router")
            if not (isinstance(section, dict) and section):
                section = cfg.get("uncensored_router")
        block = (section or {}).get("complexity") if isinstance(section, dict) else None
        return dict(block) if isinstance(block, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def complexity_mode() -> str:
    """complexity.mode: "static" (default, v3.2.3 behavior) | "adaptive"
    (Phase 2). Unknown values degrade to static. Never raises."""
    try:
        mode = str(_complexity_cfg().get("mode") or "static").strip().lower()
        return mode if mode in ("static", "adaptive") else "static"
    except Exception:  # noqa: BLE001
        return "static"


def shadow_enabled() -> bool:
    """complexity.shadow (default true): Phase 1 log-only escalation
    evaluation — logs what adaptive WOULD do, changes nothing. Never raises."""
    try:
        return bool(_complexity_cfg().get("shadow", True))
    except Exception:  # noqa: BLE001
        return True


def adaptive_cfg() -> Dict[str, Any]:
    """The adaptive sub-block (ladder/breaker/cooldowns). Read but UNUSED in
    Phase 1 except for logging completeness. Never raises."""
    try:
        block = _complexity_cfg().get("adaptive")
        return dict(block) if isinstance(block, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# Session-scoped one-time flags (adaptive_not_armed logs ONCE per session).
_ADAPTIVE_ARM_WARNED: set = set()
_ADAPTIVE_ARM_WARNED_MAX = 256


def _log_adaptive_not_armed_once(session_id: str) -> None:
    """Emit the adaptive_not_armed marker once per session (Phase 1 arm
    protection: mode:adaptive in Phase 1 behaves as static). Never raises."""
    try:
        if session_id in _ADAPTIVE_ARM_WARNED:
            return
        with _LOCK:
            if session_id in _ADAPTIVE_ARM_WARNED:
                return
            if len(_ADAPTIVE_ARM_WARNED) >= _ADAPTIVE_ARM_WARNED_MAX:
                _ADAPTIVE_ARM_WARNED.clear()
            _ADAPTIVE_ARM_WARNED.add(session_id)

        from hermes_router import _log_route  # deferred — avoids import cycle

        _log_route("PRE", event_detail="adaptive_not_armed", phase=1,
                   session_id=session_id or "")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# v3.3.0 F3 — infra suppression plumbing (INERT in Phase 1)
# ---------------------------------------------------------------------------
# The guard BODY ships dormant so Phase 2 arms it by config alone. dispatch()
# only reaches it when complexity.mode == "adaptive"; in Phase 1 dispatch logs
# adaptive_not_armed + behaves as static instead (arm protection), so this
# function is unreachable in production Phase 1 and the skip never fires.


def record_infra_cooldown(task_id: str, ts: Optional[float] = None) -> None:
    """Record an infra classification timestamp on the task record (Phase 2
    will consult it via _infra_cooldown_skip). Never raises."""
    try:
        _bump_task(task_id, infra_kind_ts=float(ts if ts is not None else time.time()))
    except Exception:  # noqa: BLE001
        pass


def infra_cooldown_active(task_id: str, now: Optional[float] = None) -> bool:
    """True when an infra classification is FRESH (inside adaptive.infra_cooldown_s,
    default 90s). Never raises."""
    try:
        try:
            cd = float(adaptive_cfg().get("infra_cooldown_s", 90))
        except (TypeError, ValueError):
            cd = 90.0
        rec = task_state(task_id)
        ts = float(rec.get("infra_kind_ts", 0) or 0)
        if ts <= 0:
            return False
        cur = float(now if now is not None else time.time())
        return (cur - ts) <= cd
    except Exception:  # noqa: BLE001
        return False


def _infra_cooldown_skip(task_id: str, session_id: str) -> Tuple[bool, str]:
    """F3 guard body — Phase-2-only (adaptive mode): when an infra cooldown is
    fresh, the struggle-escalation branch is skipped this turn (suppressed
    struggles are still re-classified + shadow-logged by callers to avoid a
    calibration blind spot). Returns (skip, reason). In static mode this is
    NEVER called by dispatch (inert — zero behavior change in Phase 1).
    Never raises."""
    try:
        if not infra_cooldown_active(task_id):
            return False, ""
        from hermes_router import _log_route

        _log_route("PRE", event_detail="struggle_suppressed_would_skip",
                   kind="infra", task_id=task_id or "", session_id=session_id or "")
        return True, "infra_cooldown_skip"
    except Exception:  # noqa: BLE001
        return False, ""


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def _complexity_level() -> int:
    """Read intensity from config: complexity.level (0-3). Never raises."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = None
        if isinstance(cfg, dict):
            section = cfg.get("hermes_router")
            if not (isinstance(section, dict) and section):
                section = cfg.get("uncensored_router")
        block = (section or {}).get("complexity") if isinstance(section, dict) else None
        if isinstance(block, dict):
            return complexity.normalize_level(block.get("level", 0))
        return 0
    except Exception:  # noqa: BLE001
        return 0


def _lane_enabled(lane: str) -> bool:
    """Per-lane enable switch (complexity.enabled, default True at L>0).
    Uncensored lane keeps its own _enabled() in __init__. Never raises."""
    try:
        if lane != LANE_COMPLEXITY:
            return True
        from hermes_cli.config import load_config

        cfg = load_config()
        section = None
        if isinstance(cfg, dict):
            section = cfg.get("hermes_router")
            if not (isinstance(section, dict) and section):
                section = cfg.get("uncensored_router")
        block = (section or {}).get("complexity") if isinstance(section, dict) else None
        if isinstance(block, dict) and block.get("enabled") is False:
            return False
        return True
    except Exception:  # noqa: BLE001
        return True


def dispatch(user_text: str, *, session_id: str, model: str = "",
             uncensored_matched: bool = False) -> RouteDecision:
    """SINGLE PRE classification. Order of authority:

      0. inline overrides (skip > anchor) — trusted-origin, checked first
      1. struggle escalation (task-scoped) -> OWNERSHIP (model_target=primary)
      2. complexity detection -> PLAN (frontier plans, flash executes)
      3. uncensored PRE match -> existing render lane (caller applies it;
         decision here only records the lane for the route log)
      4. default FLASH_DIRECT pass-through

    The uncensored lane stays byte-identical: when uncensored_matched is True
    the caller runs its EXISTING rewrite path — this dispatcher never rewrites
    user messages. Never raises.
    """
    task_id = task_id_for(session_id, user_text, model)
    now = time.time()

    def _dec(lane: str, mode: str, target: Optional[str], reason: str,
             override: Optional[str] = None) -> RouteDecision:
        rd = RouteDecision(task_id=task_id, lane=lane, mode=mode,
                           model_target=target, reason=reason, ts=now,
                           override_used=override,
                           route_id=task_id[:12] + "-" + str(int(now)))
        return rd

    try:
        # 0. Inline overrides — before any classification.
        override = complexity.detect_override(user_text)
        if override == "skip":
            return _dec(LANE_UNCENSORED, MODE_FLASH_DIRECT, None, "override_skip", override)

        # v3.3.0 F3 — infra suppression guard, AFTER step-0 override handling
        # (an explicit "anchor this" must never be swallowed by a cooldown).
        # The guard body lives in _infra_cooldown_skip() — Phase-2 plumbing,
        # INERT in Phase 1: dispatch behavior in static mode is byte-identical
        # to v3.2.3 (zero-behavior-change invariant; suppressed struggles would
        # still be re-classified + logged to avoid survivorship bias).
        if complexity_mode() == "adaptive":
            # Phase 1 arm protection: adaptive in Phase 1 behaves AS STATIC and
            # logs the not-armed marker once per session (no accidental arm).
            _log_adaptive_not_armed_once(session_id)
        else:
            # v3.3.0 F2 — shadow mode: when the static struggle detector fires,
            # log what adaptive WOULD do (log-only; default shadow=true).
            turn_key = state.turn_key_for(session_id, user_text, model)
            maybe_shadow_log(user_text, task_id, session_id, model, turn_key=turn_key)

        # Struggle check first: an escalated task stays escalated until the
        # task hash changes (new ask = new task).
        struggling, sreason = struggle_verdict(task_id, user_text)
        with _LOCK:
            escalated = bool(_TASK_STATE.get(task_id, {}).get("escalated", False))

        if struggling or escalated:
            chain = anchor_chain.load_anchor_chain()
            ep = chain.endpoint_for("primary")
            if ep is not None and _lane_enabled(LANE_COMPLEXITY):
                if struggling:
                    _bump_task(task_id, escalated=True)
                return _dec(LANE_COMPLEXITY, MODE_OWNERSHIP, ep.model,
                            sreason or "task_escalated")

        # 2. Complexity detection (stage-1 -> stage-2 on gray zone).
        # Amendment (2026-09-04): when an optional decision head is configured
        # (decision_head.backend), its score gates the route instead of the
        # hand-tuned regex verdict. Default backend = heuristic = unchanged.
        level = _complexity_level()
        if level > 0 and _lane_enabled(LANE_COMPLEXITY):
            dh_backend = "heuristic"
            try:
                from . import decision_head

                dh_backend = decision_head.configured_backend()
            except Exception:  # noqa: BLE001
                dh_backend = "heuristic"
            if dh_backend != "heuristic":
                route_complex = decision_head.route(user_text)
                meta = {"stage": "decision_head", "backend": dh_backend,
                        "stage1": "clear_complex" if route_complex else "clear_simple"}
            else:
                route_complex, meta = complexity.classify(user_text, level)
            if route_complex:
                mode = MODE_PLAN
                if override == "anchor":
                    # explicit ask: bounded CONSULT (frontier answers once as
                    # a consultant tool result; flash keeps ownership).
                    mode = MODE_CONSULT
                elif meta.get("stage1") == "borderline":
                    mode = MODE_CONSULT
                return _dec(LANE_COMPLEXITY, mode, _primary_model(),
                            "complexity_" + str(meta.get("stage", "stage1")), override)
            if override == "anchor":
                # explicit ask outranks a "clear_simple" verdict at any level:
                # manual-only semantics (L1) and the inline override contract.
                return _dec(LANE_COMPLEXITY, MODE_CONSULT, _primary_model(),
                            "override_anchor", override)
        elif override == "anchor":
            # L0 with explicit ask: honor the manual anchor.
            if _lane_enabled(LANE_COMPLEXITY):
                return _dec(LANE_COMPLEXITY, MODE_CONSULT, _primary_model(),
                            "override_anchor", override)

        # 3/4. Default: flash direct. Uncensored PRE match (if any) is applied
        # by the caller's existing path — lane recorded as uncensored.
        lane = LANE_UNCENSORED if uncensored_matched else LANE_UNCENSORED
        return _dec(lane, MODE_FLASH_DIRECT, None,
                    "uncensored_match" if uncensored_matched else "default")
    except Exception:  # noqa: BLE001 — dispatch must never raise
        return _dec(LANE_UNCENSORED, MODE_FLASH_DIRECT, None, "dispatch_error")


def _primary_model() -> Optional[str]:
    try:
        chain = anchor_chain.load_anchor_chain()
        ep = chain.endpoint_for("primary")
        return ep.model if ep else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Pending swap (PRE decision -> llm_execution middleware handoff)
# ---------------------------------------------------------------------------


def stage_model_swap(session_id: str, decision: RouteDecision,
                     role: str = "primary") -> Optional[Dict[str, Any]]:
    """Called by PRE after a COMPLEXITY decision: stage the per-call swap so
    the NEXT llm_execution middleware invocation (same session) performs the
    anchored call. Per-call, never persistent — the record is consumed once.

    v3.2.0 one-consult-per-turn: when a swap for the SAME (session_id,
    task_id) was already staged within _SWAP_DONE_TTL, this is a re-fire of
    the same ask inside one multi-provider-call turn — no-op (the already-
    staged/consumed marker wins). Returns None on skip; a NEW ask (different
    task_id) stages fresh. Never raises."""
    try:
        key = (str(session_id or ""), str(decision.task_id or ""))
        now = time.time()
        with _PENDING_SWAP_LOCK:
            done_ts = _SWAP_DONE.get(key)
            if done_ts is not None and now - done_ts <= _SWAP_DONE_TTL:
                return None  # swap_already_staged — one consult per turn
        chain = anchor_chain.load_anchor_chain()
        ep = chain.endpoint_for(role)
        if ep is None:
            return None
        rec = {
            "route_id": decision.route_id,
            "task_id": decision.task_id,
            "mode": decision.mode,
            "role": role,
            "endpoint": ep,
            "staged_at": now,
        }
        with _PENDING_SWAP_LOCK:
            _PENDING_SWAP[session_id or ""] = rec
            _SWAP_DONE[key] = now
            # TTL reap + size cap (same discipline as _TASK_STATE).
            for k in [k for k, ts in _SWAP_DONE.items() if now - ts > _SWAP_DONE_TTL]:
                _SWAP_DONE.pop(k, None)
            while len(_SWAP_DONE) > _SWAP_DONE_MAX:
                oldest = min(_SWAP_DONE, key=lambda k: _SWAP_DONE[k])
                _SWAP_DONE.pop(oldest, None)
        return rec
    except Exception:  # noqa: BLE001
        return None


def pending_model_swap(session_id: str) -> Optional[Dict[str, Any]]:
    """Consume the staged swap for this session (None when none staged).
    Consumed exactly once — the record is popped on read. Never raises."""
    try:
        with _PENDING_SWAP_LOCK:
            rec = _PENDING_SWAP.pop(session_id or "", None)
        if rec is None:
            return None
        if time.time() - rec.get("staged_at", 0) > 60.0:
            return None  # stale staged decision — drop
        return rec
    except Exception:  # noqa: BLE001
        return None


def peek_pending_swap(session_id: str) -> Optional[Dict[str, Any]]:
    """Non-destructive read (status tooling/tests). Never raises."""
    try:
        with _PENDING_SWAP_LOCK:
            rec = _PENDING_SWAP.get(session_id or "")
        return dict(rec) if rec else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Consult tool-result envelopes (integration verdict 2 — NEW lane only)
# ---------------------------------------------------------------------------


def build_frontier_envelope(kind: str, producer: str, decision: RouteDecision,
                            answer: str, evidence_refs: Optional[List[str]] = None,
                            limitations: Optional[str] = None) -> Dict[str, Any]:
    """Provenance envelope for frontier outputs entering as TOOL RESULTS
    (never rewrites the user message). kind: frontier_plan|consultation|
    verification. Never raises."""
    try:
        return {
            "kind": kind,
            "producer": producer,
            "route_id": decision.route_id,
            "task_id": decision.task_id,
            "answer": answer,
            "evidence_refs": list(evidence_refs or []),
            "limitations": limitations or "",
            "ts": time.time(),
        }
    except Exception:  # noqa: BLE001
        return {}


def store_consult_result(route_id: str, envelope: Dict[str, Any]) -> None:
    try:
        with _LOCK:
            _CONSULT_RESULTS[route_id or ""] = envelope
            while len(_CONSULT_RESULTS) > _CONSULT_RESULTS_MAX:
                oldest = min(_CONSULT_RESULTS, key=lambda k: _CONSULT_RESULTS[k].get("ts", 0))
                _CONSULT_RESULTS.pop(oldest, None)
    except Exception:  # noqa: BLE001
        pass


def take_consult_result(route_id: str) -> Optional[Dict[str, Any]]:
    try:
        with _LOCK:
            return _CONSULT_RESULTS.pop(route_id or "", None)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _test_reset() -> None:
    """Tests-only: clear all task-scoped state."""
    global _TASK_STATE, _TOOL_RESULT_SEEN, _TOOLLOOP_STATE, _PENDING_SWAP, _CONSULT_RESULTS
    with _LOCK:
        _TASK_STATE.clear()
        _TOOL_RESULT_SEEN.clear()
        _TOOLLOOP_STATE.clear()
        _CONSULT_RESULTS.clear()
        _ADAPTIVE_ARM_WARNED.clear()
    with _PENDING_SWAP_LOCK:
        _PENDING_SWAP.clear()
        _SWAP_DONE.clear()
    with _STRUGGLE_SIGNALS_TURN_LOCK:
        _STRUGGLE_SEEN_TURNS.clear()