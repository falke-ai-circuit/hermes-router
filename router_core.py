"""Router core — the v3.0.0 two-lane dispatcher (hermes-router).

SINGLE PRE pass. on_llm_request (the existing PRE middleware entry) calls
dispatch() ONCE per turn with the IMMUTABLE ingress user text. dispatch()
returns a RouteDecision dataclass; __init__ applies it:

  Lane UNCENSORED (existing behavior, byte-identical where untouched):
      contested-class render routing + H1 sentinel + persona + render inbox.
  Lane COMPLEXITY (new):
      2-stage detection -> 4-mode controller -> anchor-chain model swap.

3-MODE CONTROLLER (task-scoped; MID/struggle/ownership REMOVED 2026-09-08):
  FLASH_DIRECT  default pass-through
  CONSULT       frontier orientation brief at task start (pre_mode=route)
  COMPLETION AUDIT  frontier higher-self reflection on completed output
                    (audit_mode=complex|always), delivered next turn

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
import re
import threading
import time
from collections import deque
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

VALID_MODES = (MODE_FLASH_DIRECT, MODE_PLAN, MODE_CONSULT)

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
    orientation: bool = False   # v3.6.1 PRE-orientation brief (Goran 09-08)

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

# v3.3.1 anchor failure backoff: (session_id, task_id) -> ledger record.
# _SWAP_DONE prevents re-staging INSIDE one turn (TTL 600s), but each NEW
# user turn on the same stuck ask outlives it -> fresh anchor staging ->
# fresh failure -> repeat (live 2026-09-05: task ba9ce5849a6f… fired its
# anchor attempt 27x over 103 min into a failing OpenRouter endpoint; 98
# anchored_call_failed route_skips across 3 profiles). The ledger remembers
# the anchor FAILED for this (session, task): a failed attempt enters
# exponential backoff (base_s doubling per consecutive fail, capped max_s)
# so the dispatcher stops re-paying full price every turn; a SUCCESS clears
# the entry. Same TTL-reap + MAX-size discipline as _SWAP_DONE. The key
# includes session_id — cross-session same-ask never inherits backoff (each
# session's first consult attempt is legitimate). cap_blocked does NOT
# count as a failure (spend policy, not anchor health). Fail-open: ledger
# writes never raise; on any error staging proceeds (v3.3.0 behavior).
# Static mode / uncensored lane untouched — this only gates complexity-lane
# anchor staging (zero interaction with renders, caps, canonical events).
_ANCHOR_FAIL_BACKOFF: Dict[Tuple[str, str], Dict[str, Any]] = {}
_ANCHOR_BACKOFF_BASE_S = 30.0    # first-fail window
_ANCHOR_BACKOFF_MAX_S = 1800.0   # window cap (30 min)
_ANCHOR_BACKOFF_TTL_S = 3600.0   # 1h memory of failure
_ANCHOR_BACKOFF_MAX = 256

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
    (>=1 distinct content).

    v3.6 P0.3 (write-only): ALSO feeds the §2.3 fail-ring + progress ledger
    on the task record — every completed tool cycle lands one ring entry
    (normalized_sig, err_class, out_fp, artifact_fp, ts; text <=240c) and
    updates the progress marker. NO gate reads them yet (Phases 1+ do; Phase
    0 is wiring only, zero behavior change)."""
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
            # ---- v3.6 P0.3 write path (fail-ring + progress ledger) ----
            try:
                task_rec = _TASK_STATE.setdefault(task_id, {"fail_count": 0, "consult_count": 0,
                                                            "escalated": False, "created_at": now})
                ring = task_rec.setdefault("fail_ring", deque(maxlen=int(
                    _complexity_cfg().get("consult", {}).get("mid_ring_size", 8) or 8)))
                sig = _normalize_call_sig(tool_result_text or "")
                err_class = _classify_error_text(tool_result_text or "")
                out_fp = hash_text(str(tool_result_text or "")[:240])[:16]
                artifact_fp = hash_text(sig + "|" + err_class)[:16]
                is_err = bool(err_class)
                if is_err:
                    ring.append((sig, err_class, out_fp, artifact_fp, now))
                prog = task_rec.setdefault("progress", {"last_progress_ts": now,
                                                        "cycles_since_progress": 0,
                                                        "last_out_fp": "",
                                                        "last_artifact_fp": ""})
                is_progress = (not is_err) and (
                    out_fp not in {e[2] for e in ring} or
                    _novel_text(tool_result_text or "", prog, seen))
                if is_progress:
                    prog["last_progress_ts"] = now
                    prog["cycles_since_progress"] = 0
                    prog["last_out_fp"] = out_fp
                    prog["last_artifact_fp"] = artifact_fp
                else:
                    prog["cycles_since_progress"] = int(prog.get("cycles_since_progress", 0)) + 1
            except Exception:  # noqa: BLE001 — P0.3 write path must never break the tap
                pass
            return int(rec["calls"])
    except Exception:  # noqa: BLE001
        return 0


def _normalize_call_sig(text: str) -> str:
    """§2.3 normalization: tool name + sorted param keys shape proxy —
    whitespace-collapsed, case-folded; timestamps/UUIDs/hex>=8ch masked;
    numbers bucketed by magnitude. Never raises."""
    try:
        import re as _re

        t = str(text or "")[:240].casefold()
        t = _re.sub(r"\s+", " ", t)
        t = _re.sub(r"\b[0-9a-f]{8,}\b", "<hex>", t)
        t = _re.sub(r"\b\d{4}-\d{2}-\d{2}[t ][0-9:.-]*z?\b", "<ts>", t, flags=_re.IGNORECASE)
        t = _re.sub(r"\b(1[0-9]{9}|1[0-9]{12})\b", "<epoch>", t)
        t = _re.sub(r"\b([0-9]+)\b", lambda m: "<n%02d>" % min(9, len(m.group(1))), t)
        return t[:240]
    except Exception:  # noqa: BLE001
        return str(text or "")[:240]


def _classify_error_text(text: str) -> str:
    """Error-class bucket for ring entries ("" = not an error). Same benign
    discipline as struggle_class: only unambiguous transport/provider error
    shapes count. Never raises."""
    try:
        t = str(text or "")[:400].casefold()
        if "timeout" in t or "timed out" in t:
            return "timeout"
        if "connection" in t and ("refused" in t or "reset" in t or "error" in t):
            return "connect"
        if "http 4" in t or " status_code=4" in t:
            return "http_4xx"
        if "http 5" in t or " status_code=5" in t:
            return "http_5xx"
        if "permission denied" in t or "access denied" in t:
            return "denied"
        if "no such file" in t or "not found" in t:
            return "not_found"
        return ""
    except Exception:  # noqa: BLE001
        return ""


def _novel_text(tool_result_text: str, prog: Dict[str, Any], seen: set) -> bool:
    """§2.3 progress rule: the result text contributed >=16 normalized chars
    of content not seen in the ring's recent fingerprints. Never raises."""
    try:
        import re as _re

        norm = _re.sub(r"\s+", " ", str(tool_result_text or ""))[:240].strip()
        return len(norm) >= 16
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Complexity-mode + shadow config (v3.3.0 F2 — dual-section reader as existing)
# ---------------------------------------------------------------------------


def _int_knob(name: str, default: int) -> int:
    """Top-level router-section int knob via config_access (live-read,
    dual-section hermes_router -> uncensored_router). Never raises."""
    try:
        from . import config_access
        return int((config_access.router_section() or {}).get(name, default))
    except Exception:  # noqa: BLE001
        return default


def pre_cooldown_seconds() -> int:
    """pre_cooldown_seconds (int, default 600, 0=off). Never raises."""
    try:
        return max(0, _int_knob("pre_cooldown_seconds", 600))
    except Exception:  # noqa: BLE001
        return 600


def post_audit_min_turns() -> int:
    """post_audit_min_turns (int, default 3, 1 = current behavior). Never raises."""
    try:
        return max(1, _int_knob("post_audit_min_turns", 3))
    except Exception:  # noqa: BLE001
        return 3


# ---------------------------------------------------------------------------
# Router tuning A1 (2026-09-09, Goran: "consults must be periodic, not
# per-turn") — VERIFY-CLASS EXEMPT: short imperative confirm/status asks skip
# PRE orientation entirely. Override ("anchor this") beats the exempt.
# ---------------------------------------------------------------------------

_VERIFY_CLASS_MAX_CHARS = 120
# imperative-confirm shape
_VERIFY_IMPERATIVE_RE = re.compile(
    r"^(?:can you\s+)?(?:confirm|check|verify|is\s+|what\s+is\b|status\b|put\s+all\s+on\b|set\s+)"
    r"|^(?:can you confirm\b)"
    r"|\bis\s+.{0,40}?\b(?:ok|on|active|enabled)\b"
    r"|\bconfirm\b.{0,50}?\b(?:please|working|right|now)\b"
    r"|\b(?:are|is)\s+.{0,30}?\b(?:working|up|fine|good)\b"
    r"|\bcan youn?confirm\b|\bcan you\s+confirm\b"
    r"|\bcan y(?:ou|u)\s+(?:confi|confi?r?m|conf[oō]r)[a-z]*\b",
    re.IGNORECASE,
)
# absence of analysis dims — any hit disqualifies the exempt
_VERIFY_ANALYSIS_DIMS_RE = re.compile(
    r"\b(?:why|how|design|compare|analyz\w+|analyse\w+|explain|tradeoffs?|better|deep|approach|think|help me)\b",
    re.IGNORECASE,
)


def _is_verify_class_exempt(user_text: str) -> bool:
    """True when the ask is a short imperative confirm/status check that must
    skip PRE orientation. Pure text-in/bool-out; fail-open False (=> consult
    fires = current behavior). Never raises."""
    try:
        t = str(user_text or "").strip()
        # platform appends <memory-context>...</memory-context> (recalled graph
        # facts) to the user message — routing verdicts must see the ASK only.
        _mc = t.find("<memory-context>")
        if _mc != -1:
            t = t[:_mc].strip()
        if not t or len(t) >= _VERIFY_CLASS_MAX_CHARS:
            return False
        if _VERIFY_ANALYSIS_DIMS_RE.search(t):
            return False
        return bool(_VERIFY_IMPERATIVE_RE.search(t))
    except Exception:  # noqa: BLE001
        return False


def _pre_cooldown_active(session_id: str, task_id: str = "") -> bool:
    """True when a real staged orientation consult fired for this session
    within the cooldown window. Fail-open False (missing/unreadable state =>
    consult fires). Same task_id is exempt (one-consult-per-turn dedup
    semantics preserved); cooldown never applies to override_anchor."""
    try:
        window = pre_cooldown_seconds()
        if window <= 0:
            return False
        ts, staged_task = state.last_staged_consult(session_id)
        if ts is None:
            return False
        if task_id and staged_task and task_id == staged_task:
            return False  # same (session, task) re-fire — dedup path owns it
        return (time.time() - ts) < window
    except Exception:  # noqa: BLE001
        return False


def _complexity_cfg() -> Dict[str, Any]:
    """Read the complexity block from the plugin config (hermes_router
    canonical first, legacy uncensored_router fallback — same dual-section
    discipline as _complexity_level). {} on miss. Never raises.

    Fix (2026-09-07, live-caught): load_config() in profile gateways resolves
    to the GLOBAL home config (no router section) → complexity was pinned at
    L0 no matter what the profile config said, while debug_banner/persona
    reads (which have the profile-co-located fallback) kept working. Reuse
    debug_banner._banner_section() — same dual-section read + profile
    co-located fallback + last-good cache."""
    try:
        from . import debug_banner as _dbg

        section = _dbg._banner_section()
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


# ---------------------------------------------------------------------------
# v3.3.1 — anchor failure backoff config + ledger API
# ---------------------------------------------------------------------------


def anchor_backoff_cfg() -> Dict[str, Any]:
    """complexity.anchor_backoff block (dual-section reader as existing).
    Defect-fix defaults LIVE on deploy: enabled=True, base_s=30, max_s=1800,
    ttl_s=3600. enabled:false restores v3.3.0 behavior exactly (escape
    hatch). Never raises."""
    try:
        block = _complexity_cfg().get("anchor_backoff")
        cfg = dict(block) if isinstance(block, dict) else {}
        out: Dict[str, Any] = {
            "enabled": bool(cfg.get("enabled", True)),
            "base_s": _ANCHOR_BACKOFF_BASE_S,
            "max_s": _ANCHOR_BACKOFF_MAX_S,
            "ttl_s": _ANCHOR_BACKOFF_TTL_S,
        }
        for k in ("base_s", "max_s", "ttl_s"):
            try:
                v = float(cfg.get(k) or 0)
                if v > 0:
                    out[k] = v
            except (TypeError, ValueError):
                pass
        return out
    except Exception:  # noqa: BLE001
        return {"enabled": True, "base_s": _ANCHOR_BACKOFF_BASE_S,
                "max_s": _ANCHOR_BACKOFF_MAX_S, "ttl_s": _ANCHOR_BACKOFF_TTL_S}


def anchor_backoff_window(fails: int) -> float:
    """Exponential backoff window: base_s * 2**(fails-1) capped at max_s.
    1st fail -> 30s, 2nd -> 60s, 3rd -> 120s … capped 30min. Never raises."""
    try:
        cfg = anchor_backoff_cfg()
        base = float(cfg.get("base_s", _ANCHOR_BACKOFF_BASE_S))
        cap = float(cfg.get("max_s", _ANCHOR_BACKOFF_MAX_S))
        n = max(1, int(fails))
        return min(base * (2 ** (n - 1)), cap)
    except Exception:  # noqa: BLE001
        return _ANCHOR_BACKOFF_MAX_S


def record_anchor_backoff_failure(session_id: str, task_id: str,
                                  reason: str = "") -> None:
    """F1 ledger write from the consumption site (__init__.py route_skipped
    branch, BEFORE return next_call): increment fails + set last_fail_ts for
    (session_id, task_id). Same TTL-reap + size-cap discipline as _SWAP_DONE.
    Never raises (fail-open: on error the ledger write is skipped)."""
    try:
        key = (str(session_id or ""), str(task_id or ""))
        if not key[1]:
            return
        now = time.time()
        with _PENDING_SWAP_LOCK:
            rec = _ANCHOR_FAIL_BACKOFF.get(key)
            if rec is None:
                rec = {"fails": 0, "last_fail_ts": 0.0, "last_reason": ""}
                _ANCHOR_FAIL_BACKOFF[key] = rec
            rec["fails"] = int(rec.get("fails", 0)) + 1
            rec["last_fail_ts"] = now
            if reason:
                rec["last_reason"] = str(reason)[:200]
            # TTL reap + size cap (same discipline as _SWAP_DONE).
            ttl = float(anchor_backoff_cfg().get("ttl_s", _ANCHOR_BACKOFF_TTL_S))
            for k in [k for k, v in _ANCHOR_FAIL_BACKOFF.items()
                      if now - float(v.get("last_fail_ts", 0)) > ttl]:
                _ANCHOR_FAIL_BACKOFF.pop(k, None)
            while len(_ANCHOR_FAIL_BACKOFF) > _ANCHOR_BACKOFF_MAX:
                oldest = min(_ANCHOR_FAIL_BACKOFF,
                             key=lambda k: _ANCHOR_FAIL_BACKOFF[k].get("last_fail_ts", 0))
                _ANCHOR_FAIL_BACKOFF.pop(oldest, None)
    except Exception:  # noqa: BLE001
        pass


def clear_anchor_backoff(session_id: str, task_id: str) -> None:
    """F1 ledger clear from the consumption site (__init__.py outcome=="done"
    branch, after envelope delivery): SUCCESS clears the entry — the next
    stage for this key is allowed immediately. Never raises."""
    try:
        key = (str(session_id or ""), str(task_id or ""))
        with _PENDING_SWAP_LOCK:
            if _ANCHOR_FAIL_BACKOFF.pop(key, None) is not None:
                from hermes_router import _log_route  # deferred — import cycle

                _log_route("PRE", event_detail="anchor_backoff_cleared",
                           task_id=key[1], session_id=key[0])
    except Exception:  # noqa: BLE001
        pass


def anchor_backoff_active(session_id: str, task_id: str,
                          now: Optional[float] = None) -> bool:
    """True when (session_id, task_id) sits inside its backoff window (a
    previous attempt failed recently). disabled config -> always False
    (v3.3.0 byte-compat). Never raises."""
    try:
        if not anchor_backoff_cfg().get("enabled", True):
            return False
        key = (str(session_id or ""), str(task_id or ""))
        with _PENDING_SWAP_LOCK:
            rec = _ANCHOR_FAIL_BACKOFF.get(key)
            if rec is None:
                return False
            fails = int(rec.get("fails", 0))
            last = float(rec.get("last_fail_ts", 0))
        cfg = anchor_backoff_cfg()
        ttl = float(cfg.get("ttl_s", _ANCHOR_BACKOFF_TTL_S))
        cur = float(now if now is not None else time.time())
        if cur - last > ttl:
            return False
        return (cur - last) <= anchor_backoff_window(fails)
    except Exception:  # noqa: BLE001
        return False


def anchor_backoff_active_count() -> int:
    """router_status observability: number of ledger entries currently inside
    their backoff window (TTL-expired entries don't count). Never raises."""
    try:
        if not anchor_backoff_cfg().get("enabled", True):
            return 0
        cur = time.time()
        cfg = anchor_backoff_cfg()
        ttl = float(cfg.get("ttl_s", _ANCHOR_BACKOFF_TTL_S))
        with _PENDING_SWAP_LOCK:
            n = 0
            for rec in _ANCHOR_FAIL_BACKOFF.values():
                last = float(rec.get("last_fail_ts", 0))
                if cur - last > ttl:
                    continue
                if (cur - last) <= anchor_backoff_window(int(rec.get("fails", 1))):
                    n += 1
        return n
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def _complexity_level() -> int:
    """Read intensity from config: complexity.level (0-3). Never raises.
    Reads via _complexity_cfg() (dual-section + profile-co-located fallback,
    fix 2026-09-07) so profile gateways see their own complexity level.

    Zero-config default (Goran 2026-09-10): when the user configured an
    anchor_chain (inserted their frontier API) but set NO complexity block at
    all, default to level 2 (conservative-auto) — the frontier lane works out
    of the box once the API is in config. Explicit config always wins:
    complexity.enabled: false keeps the lane off; an explicit level is honored
    as-is (including 0)."""
    block = _complexity_cfg()
    if isinstance(block, dict) and block:
        if block.get("enabled") is False:
            return 0
        if "level" in block:
            return complexity.normalize_level(block.get("level"))
        # block present but no level key: treat as explicit enable at default 2
        return 2
    # No complexity block anywhere: auto-enable IFF an anchor chain is configured
    try:
        from . import anchor_chain as _ac

        chain = _ac.load_anchor_chain()
        if chain is not None and getattr(chain, "primary", None):
            return 2
    except Exception:  # noqa: BLE001 — fail-open to off
        pass
    return 0


def _lane_enabled(lane: str) -> bool:
    """Per-lane enable switch (complexity.enabled, default True at L>0).
    Uncensored lane keeps its own _enabled() in __init__. Never raises.
    Reads via _complexity_cfg() (profile-co-located fallback, fix 2026-09-07)."""
    try:
        if lane != LANE_COMPLEXITY:
            return True
        block = _complexity_cfg()
        if isinstance(block, dict) and block.get("enabled") is False:
            return False
        return True
    except Exception:  # noqa: BLE001
        return True




# ---------------------------------------------------------------------------
# System-injected-only turns (2026-09-09): platform payloads (async batch
# results, task-list reminders, context summaries) are NOT user asks. PRE
# orientation on them produced a brief with nothing to do -> the main model
# answered the seam itself and the brief leaked to the user (live incident,
# conductor session 20260909). Skip PRE orientation on these; POST audit
# and manual anchor remain unaffected.

_SYSTEM_INJECTED_PREFIXES = (
    "[ASYNC DELEGATION BATCH COMPLETE",
    "[Your active task list",
    "[Depth-3 Summary",
    "[Depth-2 Summary",
    "[Recent Summary",
    "[Session Arc Summary",
    "[Durable Summary",
    "[OUT-OF-BAND USER MESSAGE",
    "[System note:",
)


def _is_system_injected_turn(user_text: str) -> bool:
    """True when the turn input is a platform/system-injected payload.
    Conservative: checks the leading marker only. Never raises."""
    try:
        t = str(user_text or "").lstrip()
        return any(t.startswith(p) for p in _SYSTEM_INJECTED_PREFIXES)
    except Exception:  # noqa: BLE001
        return False

def dispatch(user_text: str, *, session_id: str, model: str = "",
             uncensored_matched: bool = False) -> RouteDecision:
    """SINGLE PRE classification. Order of authority:

      0. inline overrides (skip > anchor) — trusted-origin, checked first
      1. complexity detection -> orientation consult at task start
         (pre_mode=route; frontier as NON-binding orientation brief)
      2. uncensored PRE match -> existing render lane (caller applies it;
         decision here only records the lane for the route log)
      3. default FLASH_DIRECT pass-through

    The uncensored lane stays byte-identical: when uncensored_matched is True
    the caller runs its EXISTING rewrite path — this dispatcher never rewrites
    user messages. Never raises.
    """
    task_id = task_id_for(session_id, user_text, model)
    now = time.time()

    def _dec(lane: str, mode: str, target: Optional[str], reason: str,
             override: Optional[str] = None, orientation: bool = False) -> RouteDecision:
        rd = RouteDecision(task_id=task_id, lane=lane, mode=mode,
                           model_target=target, reason=reason, ts=now,
                           override_used=override,
                           route_id=task_id[:12] + "-" + str(int(now)),
                           orientation=orientation)
        return rd

    try:
        # 0. Inline overrides — before any classification.
        override = complexity.detect_override(user_text)
        if override == "skip":
            return _dec(LANE_UNCENSORED, MODE_FLASH_DIRECT, None, "override_skip", override)

        # Router tuning A2 (2026-09-09): PRE cooldown — after step-0 override
        # handling (override beats cooldown, per dispatch brief).
        if _pre_cooldown_active(session_id, task_id) and override != "anchor":
            try:
                from hermes_router import _log_route as _lr  # deferred - import cycle
                _cool_ts, _ = state.last_staged_consult(session_id)
                _since = int(time.time() - _cool_ts) if _cool_ts else -1
                _lr("PRE", session_id=session_id,
                    event_detail="pre_cooldown_active", since=_since,
                    task_id=task_id)
            except Exception:  # noqa: BLE001
                pass
            return _dec(LANE_UNCENSORED, MODE_FLASH_DIRECT, None, "pre_cooldown_skip")

        # v3.3.0 F3 — infra suppression guard, AFTER step-0 override handling
        # (an explicit "anchor this" must never be swallowed by a cooldown).
        # The guard body lives in _infra_cooldown_skip() — Phase-2 plumbing,
        # INERT in Phase 1: dispatch behavior in static mode is byte-identical
        # to v3.2.3 (zero-behavior-change invariant; suppressed struggles would
        # still be re-classified + logged to avoid survivorship bias).
        # 2. Complexity detection (stage-1 -> stage-2 on gray zone).
        # pre_mode (Goran 2026-09-08 ruling): "route" = PRE orientation consult
        # on stage-1 regex hit; "shadow" = log-only telemetry — NO PRE consult
        # fires, frontier consults live ONLY on COMPLETED OUTPUT per the
        # completion-audit arm. Manual "anchor this" override unaffected.
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
            if _is_system_injected_turn(user_text):
                try:
                    from hermes_router import _log_route as _lr  # deferred - import cycle
                    _lr("PRE", session_id=session_id,
                        event_detail="complexity_pre_skip_system_injected",
                        task_id=task_id, level=level)
                except Exception:  # noqa: BLE001
                    pass
                route_complex = False
            # Router tuning A1 (2026-09-09): verify-class exempt — short
            # imperative confirm/status asks skip PRE orientation entirely.
            # Override ("anchor this") beats the exempt (checked at step 0,
            # and re-guarded here so an exempt-class text carrying an explicit
            # override line still anchors).
            elif (_is_verify_class_exempt(user_text) and override != "anchor"):
                try:
                    from hermes_router import _log_route as _lr  # deferred - import cycle
                    _lr("PRE", session_id=session_id,
                        event_detail="verify_class_exempt",
                        pattern_groups="clear_simple", task_id=task_id)
                except Exception:  # noqa: BLE001
                    pass
                route_complex = False
            _pre_mode = str((_complexity_cfg() or {}).get("pre_mode") or "off").strip().lower()
            if dh_backend != "heuristic":
                route_complex = decision_head.route(user_text)
                meta = {"stage": "decision_head", "backend": dh_backend,
                        "stage1": "clear_complex" if route_complex else "clear_simple"}
            else:
                route_complex, meta = complexity.classify(user_text, level)
            if _pre_mode in ("shadow", "off") and route_complex and override != "anchor":
                # Shadow/off (Goran 09-08 ruling): frontier consults belong at
                # the completion audit, never at task start. 'shadow' keeps
                # would-fire telemetry; 'off' is fully silent.
                _log_route("PRE", session_id=session_id,
                           event_detail=("complexity_pre_shadow" if _pre_mode == "shadow"
                                         else "complexity_pre_off"),
                           stage1=str(meta.get("stage1")),
                           task_id=task_id, level=level)
                route_complex = False
            if route_complex:
                # v3.6.1 PRE-orientation (Goran 09-08): the PRE consult no
                # longer plans the task — it delivers an ORIENTATION BRIEF:
                # what the result should look like, what to watch for,
                # common pitfalls + known good solutions, what to avoid,
                # what failure looks like. Advisory, non-binding ("suggests
                # but doesn't have to be followed"). Manual anchor override
                # keeps direct-consult semantics (frontier answers the ask).
                if override == "anchor":
                    return _dec(LANE_COMPLEXITY, MODE_CONSULT, _primary_model(),
                                "complexity_" + str(meta.get("stage", "stage1")),
                                override, orientation=False)
                return _dec(LANE_COMPLEXITY, MODE_CONSULT, _primary_model(),
                            "complexity_orientation",
                            orientation=True)
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
        # v3.3.1 anchor failure backoff: a recent FAILED attempt for this
        # (session, task) benches the anchor for its exponential window —
        # no staging, log anchor_backoff_blocked (the would-have-wasted
        # counter). disabled config -> always False (v3.3.0 behavior).
        if anchor_backoff_active(key[0], key[1], now=now):
            try:
                from hermes_router import _log_route  # deferred — import cycle

                with _PENDING_SWAP_LOCK:
                    rec = _ANCHOR_FAIL_BACKOFF.get(key) or {}
                fails = int(rec.get("fails", 0))
                _log_route("PRE", event_detail="anchor_backoff_blocked",
                           fails=fails, backoff_s=round(anchor_backoff_window(fails), 1),
                           task_id=key[1], session_id=key[0])
            except Exception:  # noqa: BLE001 — logging must never break staging
                pass
            return None
        chain = anchor_chain.load_anchor_chain()
        ep = chain.endpoint_for(role)
        if ep is None:
            return None
        rec = {
            "route_id": decision.route_id,
            "task_id": decision.task_id,
            "mode": decision.mode,
            "orientation": bool(getattr(decision, "orientation", False)),
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
    (never rewrites the user message). kind: consultation|
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
        _SWAP_DONE.clear()
        _ANCHOR_FAIL_BACKOFF.clear()