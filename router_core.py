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
    loop-guard hash discipline (hash_text on the failure text)."""
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
            return int(rec["fail_count"])
    except Exception:  # noqa: BLE001
        return 0


def record_tool_call(task_id: str, tool_result_text: str, turn_key: str) -> int:
    """(b) tool-loop: count provider calls in one turn with no NEW tool-result
    content (sha dedup). Returns the no-new-content call count. Never raises."""
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
                if override == "anchor":
                    # explicit ask: bounded CONSULT (frontier answers once as
                    # a consultant tool result; flash keeps ownership).
                    return _dec(LANE_COMPLEXITY, MODE_CONSULT, _primary_model(),
                                "override_anchor", override)
                mode = MODE_PLAN
                if meta.get("stage1") == "borderline":
                    mode = MODE_CONSULT
                return _dec(LANE_COMPLEXITY, mode, _primary_model(),
                            "complexity_" + str(meta.get("stage", "stage1")), override)
        elif override == "anchor":
            # L0/L1 with explicit ask: honor the manual anchor.
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
                     role: str = "primary") -> None:
    """Called by PRE after a COMPLEXITY decision: stage the per-call swap so
    the NEXT llm_execution middleware invocation (same session) performs the
    anchored call. Per-call, never persistent — the record is consumed once.
    Never raises."""
    try:
        chain = anchor_chain.load_anchor_chain()
        ep = chain.endpoint_for(role)
        if ep is None:
            return
        with _PENDING_SWAP_LOCK:
            _PENDING_SWAP[session_id or ""] = {
                "route_id": decision.route_id,
                "task_id": decision.task_id,
                "mode": decision.mode,
                "role": role,
                "endpoint": ep,
                "staged_at": time.time(),
            }
    except Exception:  # noqa: BLE001
        pass


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
    with _PENDING_SWAP_LOCK:
        _PENDING_SWAP.clear()