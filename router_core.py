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

import copy
import json
import logging
import os
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


def _obs_warn(where: str, exc: BaseException) -> None:
    """Rider 19 item 1 — fail-loud route-event emission failure: the
    swallow is DEMOTED to a warning log so observability gaps surface in
    the route log; logging itself must NEVER break dispatch."""
    try:
        logger.warning("route_event_emit_failed where=%s err=%r", where, exc)
    except Exception:  # noqa: BLE001 — logging itself must never break dispatch
        pass


def _clean_opinion_tradeoff(text: str) -> bool:
    """Rider 19 item 3 (BC2): opinion/take-on-shaped trade-off QUESTION —
    the first-person ask form ('What's your (substantive/general/own) take
    on X versus Y?'). Structural shape only: imperative deletions, declared
    forks and manual triggers are handled by their own conditions. Never
    raises."""
    try:
        t = str(text or "")
        if "?" not in t:
            return False
        return bool(re.search(
            r"what'?s your (?:substantive |general |own )?take on\b"
            r"|what'?s your opinion (?:on|of)\b"
            r"|what'?s your view on\b"
            r"|\btake on\b[^.?!]{0,120}\bversus\b", t, re.I))
    except Exception:  # noqa: BLE001 — advisory lane, never raises
        return False


def _no_manual_trigger_pre(text: str) -> bool:
    """Rider 19 item 3: True when the text carries NO trusted manual
    trigger ('decide this: ...'). Never raises."""
    try:
        from . import decision as _dlane_r
        return _dlane_r.manual_line_hit(text, _decision_cfg()) is None
    except Exception:  # noqa: BLE001 — advisory lane, never raises
        return False


def _declared_fork_pre(text: str) -> bool:
    """Rider 19 item 3: True when the text carries an explicitly declared
    closed-fork structure (decision.has_declared_fork_structure). The
    prose 'X versus Y' fallback deliberately does NOT count (rider 4)."""
    try:
        from . import decision as _dlane_r
        return bool(_dlane_r.has_declared_fork_structure(text))
    except Exception:  # noqa: BLE001 — advisory lane, never raises
        return False


# ---------------------------------------------------------------------------
# Lanes + modes
# ---------------------------------------------------------------------------

LANE_UNCENSORED = "uncensored"
LANE_COMPLEXITY = "complexity"
LANE_DECISION = "decision"  # R19 Lane 3 (v0 dark: decision.enabled default false)

MODE_FLASH_DIRECT = "flash_direct"
MODE_PLAN = "plan"
MODE_CONSULT = "consult"
MODE_DECISION_SCORE = "decision_score"

VALID_MODES = (MODE_FLASH_DIRECT, MODE_PLAN, MODE_CONSULT, MODE_DECISION_SCORE)

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

# v3.8.1 P1-2 — JSON sidecar persistence for the backoff ledger (pattern:
# render_inbox reconciled sidecar). The in-process ledger dies on gateway
# restart, resetting every backoff window — the live 2026-09-05 incident
# (27 anchor attempts / 103 min post-bounce) continued across restarts. The
# sidecar (hermes-router-backoff.json, profile hermes home) is loaded lazily
# on first ledger access after boot and rewritten atomically on every update.
# Fail-open everywhere: load errors -> empty ledger, save errors -> skipped;
# TTL reap also runs on load so stale entries never resurrect a window.
_BACKOFF_SIDECAR_FILENAME = "hermes-router-backoff.json"
_backoff_sidecar_loaded = False


def _backoff_sidecar_path() -> str:
    """Profile-scoped sidecar path via hermes_constants (mirrors render_inbox).
    Falls back to /tmp with a shadow-qualified name. Never raises."""
    try:
        import hermes_constants
        return str(hermes_constants.get_hermes_home() / _BACKOFF_SIDECAR_FILENAME)
    except Exception:  # noqa: BLE001
        return os.path.join("/tmp", "shadow-" + _BACKOFF_SIDECAR_FILENAME)


def _load_backoff_sidecar_locked() -> None:
    """Warm the in-process ledger from the sidecar (caller holds
    _PENDING_SWAP_LOCK). One-shot per process; Tolerates torn/corrupt files;
    TTL-reap on load. Never raises."""
    global _backoff_sidecar_loaded
    if _backoff_sidecar_loaded:
        return
    _backoff_sidecar_loaded = True
    try:
        path = _backoff_sidecar_path()
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return
        try:
            ttl = float(anchor_backoff_cfg().get("ttl_s", _ANCHOR_BACKOFF_TTL_S))
        except Exception:  # noqa: BLE001
            ttl = _ANCHOR_BACKOFF_TTL_S
        now = time.time()
        for sid, tasks in data.items():
            if not isinstance(tasks, dict):
                continue
            for tid, rec in tasks.items():
                try:
                    last = float(rec.get("last_fail_ts", 0))
                    if now - last > ttl:
                        continue  # TTL-reap on load: expired failure memory
                    _ANCHOR_FAIL_BACKOFF[(str(sid), str(tid))] = {
                        "fails": max(1, int(rec.get("fails", 1))),
                        "last_fail_ts": last,
                        "last_reason": str(rec.get("last_reason", ""))[:200],
                    }
                except (TypeError, ValueError):
                    continue
    except Exception:  # noqa: BLE001 — sidecar load must never break routing
        logger.debug("anchor backoff sidecar load failed", exc_info=True)


def _save_backoff_sidecar_locked() -> None:
    """Atomically rewrite the sidecar from the in-process ledger (caller
    holds _PENDING_SWAP_LOCK). Fail-open: any error is swallowed."""
    try:
        path = _backoff_sidecar_path()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        out: Dict[str, Dict[str, Any]] = {}
        for (sid, tid), rec in _ANCHOR_FAIL_BACKOFF.items():
            out.setdefault(sid, {})[tid] = {
                "fails": int(rec.get("fails", 1)),
                "last_fail_ts": float(rec.get("last_fail_ts", 0.0)),
                "last_reason": str(rec.get("last_reason", ""))[:200],
            }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 — sidecar write must never break routing
        logger.debug("anchor backoff sidecar save failed", exc_info=True)

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
    from .core.state import hash_text

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
        from .core.state import hash_text

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
        from .core.state import hash_text

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


def emit_pre_cooldown_suppressed_consult(session_id: str, task_id: str,
                                         since_s: int) -> None:
    """D1 E3 rider (SPEC-impulse-lane-v1.md v1.1): the 600s pre_cooldown
    consult suppression was SILENT at the ledger layer. Emit a counted
    suppressed-consult ledger line — ledger-visible, NOT banner-displayed.
    Never raises, never breaks dispatch."""
    try:
        from . import decision as _dlane

        _dlane.bump_counter("pre_cooldown_suppressed_consult")
        _dlane.ledger_write({
            "session_id": session_id, "task_id": task_id,
            "trigger": "pre_cooldown",
            "trigger_kind": _dlane.trigger_kind("pre"),
            "choice": "", "outcome": "suppressed_consult",
            "fail_open_reason": "pre_cooldown_skip_consult",
            "verdict_json": json.dumps(
                {"kind": "suppressed_consult", "since_s": since_s},
                default=str),
        })
    except Exception:  # noqa: BLE001 — observability must never break dispatch
        pass


def post_audit_min_turns() -> int:
    """post_audit_min_turns (int, default 3, 1 = current behavior). Never raises."""
    try:
        return max(1, _int_knob("post_audit_min_turns", 3))
    except Exception:  # noqa: BLE001
        return 3


# ---------------------------------------------------------------------------
# R16 (2026-09-22): N-turn consult cooldown keyed by normalized content hash.
# Auto-lane consults (complexity_orientation + risk_r2/r3) on the same task
# payload are suppressed until the session has done N turns of genuinely
# different work. Cooldown key = (session_id, sha1(normalize(ingress)));
# normalize(): lowercase, collapse whitespace runs, digits -> '#'. Work
# counting = ingress turns whose OWN cooldown hash differs from K (repeated
# same-hash watcher pings must NOT advance the counter — the motivating bug).
# Declared/manual asks never reach the consult arms -> structural bypass.
# Fail-open everywhere: advisory lane, never blocks a turn.
# ---------------------------------------------------------------------------
_COOLDOWN_LOCK = threading.Lock()
_COOLDOWN_STATE: Dict[str, Dict[str, List]] = {}  # session -> hash -> [fire_ts, fire_turn]
_COOLDOWN_MAX_KEYS = 256          # bounded dict per session (_ANCHOR_BANNERS pattern)
_COOLDOWN_TTL_SECONDS = 24 * 3600


def _cooldown_normalize(text: str) -> str:
    """lowercase, collapse whitespace runs to single space, digits -> '#'.
    Never raises."""
    try:
        t = re.sub(r"[0-9]+", "#", str(text or "").lower())
        return re.sub(r"\s+", " ", t).strip()
    except Exception:  # noqa: BLE001
        return ""


def _cooldown_hash(session_id: str, text: str) -> str:
    """sha1(normalize(text))[:12] — cooldown key half. Never raises."""
    try:
        import hashlib
        return hashlib.sha1(
            (str(session_id or "") + "\x00" + _cooldown_normalize(text))
            .encode("utf-8", "replace")).hexdigest()[:12]
    except Exception:  # noqa: BLE001
        return ""


def consult_cooldown_turns() -> int:
    """complexity.consult_cooldown_turns (int, default 5, 0=disabled) via
    the canonical dual-block reader (legacy uncensored_router wins).
    Public knob accessor — patchable in tests, same shape as
    pre_cooldown_seconds/post_audit_min_turns. Never raises."""
    try:
        block = _complexity_cfg() or {}
        return max(0, int(block.get("consult_cooldown_turns", 5)))
    except Exception:  # noqa: BLE001
        return 5


def _consult_cooldown_knob() -> int:
    """Internal alias — fail-open to 5 on accessor error."""
    try:
        return consult_cooldown_turns()
    except Exception:  # noqa: BLE001
        return 5


def aux_consult_min_interval() -> int:
    """complexity.aux_consult_min_interval_sec (int, default 300, 0=disabled)
    via the canonical dual-block reader (legacy uncensored_router wins).
    R18 (Goran 09-24): machine-detected aux_intent consults are paced per
    session — no second aux consult within the interval. Declared-user
    consults are never gated. Public knob accessor — patchable in tests.
    Never raises."""
    try:
        block = _complexity_cfg() or {}
        return max(0, int(block.get("aux_consult_min_interval_sec", 300)))
    except Exception:  # noqa: BLE001
        return 300


def _record_cooldown_fire(session_id: str, cd_hash: str,
                          ts: Optional[float] = None) -> None:
    """Record a consult FIRE for key K: [ts, current_work_seq]. Bounded
    256 keys per session, FIFO eviction (the internal "\x00seq" marker is
    never evicted). Never raises."""
    try:
        if not cd_hash:
            return
        now = float(ts if ts is not None else time.time())
        with _COOLDOWN_LOCK:
            sess = _COOLDOWN_STATE.setdefault(str(session_id or ""), {})
            marker = sess.get("\x00seq")
            if cd_hash not in sess and len(sess) >= _COOLDOWN_MAX_KEYS:
                for k in list(sess.keys()):
                    if k != "\x00seq":
                        sess.pop(k, None)  # FIFO: oldest real key evicted
                        break
            sess[cd_hash] = [now, int(marker[1]) if marker else 0]
    except Exception:  # noqa: BLE001
        pass


def _register_ingress(session_id: str, cd_hash: str) -> None:
    """Advance the session's ingress-work sequence ONLY when this ingress's
    cooldown hash differs from the previous registered one — repeated
    same-hash watcher pings must NOT advance the counter (the motivating
    bug). Registered for EVERY dispatch pass (plain work turns count too).
    Never raises."""
    try:
        if not cd_hash:
            return
        with _COOLDOWN_LOCK:
            sess = _COOLDOWN_STATE.setdefault(str(session_id or ""), {})
            marker = sess.get("\x00seq")
            if marker is None or marker[0] != cd_hash:
                nxt = (marker[1] + 1) if marker else 1
                sess["\x00seq"] = [cd_hash, nxt]
    except Exception:  # noqa: BLE001
        pass


def _cooldown_turns_since(session_id: str, cd_hash: str) -> Optional[int]:
    """Hash-different ingress turns since the last fire for key K, or None
    when no live (TTL 24h) fire record exists. Never raises."""
    try:
        if not cd_hash:
            return None
        with _COOLDOWN_LOCK:
            sess = _COOLDOWN_STATE.get(str(session_id or ""), {})
            entry = sess.get(cd_hash)
            marker = sess.get("\x00seq")
        if not entry:
            return None
        fire_ts, fire_seq = entry
        if (time.time() - float(fire_ts)) > _COOLDOWN_TTL_SECONDS:
            return None  # TTL expired
        cur_seq = int(marker[1]) if marker else 0
        return max(0, cur_seq - int(fire_seq))
    except Exception:  # noqa: BLE001
        return None


def _consult_cooldown_active(session_id: str, user_text: str) -> bool:
    """True when the auto-lane consult candidate must be suppressed.
    Registers the candidate's ingress in the work sequence FIRST (a
    suppressed same-hash ping must not corrupt the sequence), then checks
    the fire record. Logs consult_cooldown_suppressed on a hit.
    Fail-open False."""
    try:
        needed = _consult_cooldown_knob()
        if needed <= 0:
            return False
        cd_hash = _cooldown_hash(session_id, user_text)
        if not cd_hash:
            return False
        _register_ingress(session_id, cd_hash)
        turns_since = _cooldown_turns_since(session_id, cd_hash)
        if turns_since is None or turns_since >= needed:
            return False
        try:
            from .core.telemetry import log_route as _lr  # P1: core telemetry (import cycle broken)
            _lr("PRE", session_id=session_id,
                event_detail="consult_cooldown_suppressed",
                cd_hash=cd_hash[:8], turns_since=turns_since, needed=needed)
        except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
            _obs_warn('consult_cooldown_suppressed', _obs_exc)  # rider 19 item 1
        return True
    except Exception:  # noqa: BLE001 — advisory lane never blocks
        return False


def _cooldown_test_reset() -> None:
    """Tests-only: clear cooldown state."""
    with _COOLDOWN_LOCK:
        _COOLDOWN_STATE.clear()


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


def _decision_cfg() -> Dict[str, Any]:
    """Read the decision block (R19 Lane 3) — same dual-section discipline as
    _complexity_cfg (debug_banner._banner_section profile-co-located fallback).
    {} on miss. Never raises."""
    try:
        from . import debug_banner as _dbg

        section = _dbg._banner_section()
        # R19.13 reflex modularization: hermes_router.reflex may alias the
        # decision block (reflex wins when present; decision fallback).
        if isinstance(section, dict):
            for _name in ("reflex", "decision"):
                _blk = section.get(_name)
                if isinstance(_blk, dict) and _blk:
                    return dict(_blk)
        return {}
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
            _load_backoff_sidecar_locked()
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
            _save_backoff_sidecar_locked()
    except Exception:  # noqa: BLE001
        pass


def clear_anchor_backoff(session_id: str, task_id: str) -> None:
    """F1 ledger clear from the consumption site (__init__.py outcome=="done"
    branch, after envelope delivery): SUCCESS clears the entry — the next
    stage for this key is allowed immediately. Never raises."""
    try:
        key = (str(session_id or ""), str(task_id or ""))
        with _PENDING_SWAP_LOCK:
            _load_backoff_sidecar_locked()
            if _ANCHOR_FAIL_BACKOFF.pop(key, None) is not None:
                _save_backoff_sidecar_locked()
                from .core.telemetry import log_route as _log_route  # P1: core telemetry (import cycle broken)

                _log_route("PRE", event_detail="anchor_backoff_cleared",
                           task_id=key[1], session_id=key[0])
    except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
        _obs_warn('anchor_backoff_cleared', _obs_exc)  # rider 19 item 1


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
            _load_backoff_sidecar_locked()
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
            _load_backoff_sidecar_locked()
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
        if lane == LANE_DECISION:
            # R19 Lane 3: decision.enabled is the ONLY switch and defaults
            # FALSE (v0 ships dark) — without this explicit branch the
            # legacy `lane != LANE_COMPLEXITY → True` fall-through silently
            # enabled the lane for anyone setting any complexity knob.
            block = _decision_cfg()
            return bool(isinstance(block, dict) and block.get("enabled") is True)
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


def _prompt_injection_flag(user_text: str):
    """R13-1 (rider 13): exfiltration-style prompt-injection clause flag
    (frames.flag_prompt_injection), import-isolated. Never raises."""
    try:
        from .frames import flag_prompt_injection as _fpi
        return _fpi(user_text)
    except Exception:  # noqa: BLE001 — observability gate must never crash
        return None

def _strip_injection_clause(text: str) -> str:
    """R14-1 (rider 14): frames.strip_injection_clause, import-isolated —
    remove the flagged clause sentence(s) so the closed option set and the
    delivered banner never carry exfiltration wording. Fail-open: the text
    is returned uncut on any doubt."""
    try:
        from .frames import strip_injection_clause as _sic
        return _sic(text)
    except Exception:  # noqa: BLE001 — hygiene must never crash dispatch
        return text

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
    # R16: register every dispatch pass in the cooldown work sequence
    # (hash-different turns advance; same-hash re-fires hold). Knob=0 is a
    # cheap no-op short-circuit.
    if _consult_cooldown_knob() > 0:
        _register_ingress(session_id, _cooldown_hash(session_id, user_text))

    def _dec(lane: str, mode: str, target: Optional[str], reason: str,
             override: Optional[str] = None, orientation: bool = False) -> RouteDecision:
        rd = RouteDecision(task_id=task_id, lane=lane, mode=mode,
                           model_target=target, reason=reason, ts=now,
                           override_used=override,
                           route_id=task_id[:12] + "-" + str(int(now)),
                           orientation=orientation)
        return rd

    # R8-4 (rider 8): stashed manual decision hit + compound-turn stack.
    # A manual 'decide this:' ask inside a complexity/risk turn fires BOTH
    # lanes: the decision consult dispatches ASYNC (parked advisory) and
    # the complexity/risk consult keeps its routed slot.
    _manual_stack: Dict[str, Any] = {"hit": None, "text": None}

    def _fire_decision_stack() -> None:
        """Fire the stashed manual decision consult async (parked-banner
        advisory, no turn slot) BEFORE a complexity/risk consult routes on
        the same turn. One-shot: clears the stash. Never raises."""
        if _manual_stack.get("hit") is None:
            return
        _manual_stack["hit"] = None
        # R14-1 (rider 14): the consult text is the INJECTION-STRIPPED scan
        # text when the manual path flagged a clause (the closed option set
        # and the delivered banner must never carry exfiltration wording).
        _stack_text = str(_manual_stack.get("text") or user_text)
        _manual_stack["text"] = None
        try:
            from . import decision as _dm
            from .core.telemetry import log_route as _lr  # P1: core telemetry (import cycle broken)

            _dm.handle_decision_v3(
                session_id=session_id, task_id=task_id,
                task_text=_stack_text, model=str(model or ""),
                log_route=_lr, initiator="user")
            try:
                _lr("PRE", session_id=session_id,
                    event_detail="decision_compound_stack",
                    lane=LANE_DECISION, task_id=task_id,
                    reason="manual_plus_consult_turn")
            except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                _obs_warn('decision_compound_stack', _obs_exc)  # rider 19 item 1
        except Exception:  # noqa: BLE001 — stack must never break dispatch
            pass

    try:
        # 0. Inline overrides — before any classification.
        override = complexity.detect_override(user_text)
        if override == "skip":
            return _dec(LANE_UNCENSORED, MODE_FLASH_DIRECT, None, "override_skip", override)

        # Router tuning A2 (2026-09-09): PRE cooldown — after step-0 override
        # handling (override beats cooldown, per dispatch brief).
        if _pre_cooldown_active(session_id, task_id) and override != "anchor":
            _cool_ts, _ = None, ""
            _since = -1
            try:
                from .core.telemetry import log_route as _lr  # P1: core telemetry (import cycle broken)
                _cool_ts, _ = state.last_staged_consult(session_id)
                _since = int(time.time() - _cool_ts) if _cool_ts else -1
                _lr("PRE", session_id=session_id,
                    event_detail="pre_cooldown_active", since=_since,
                    task_id=task_id)
            except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                _obs_warn('pre_cooldown_active', _obs_exc)  # rider 19 item 1
            emit_pre_cooldown_suppressed_consult(session_id, task_id, _since)
            return _dec(LANE_UNCENSORED, MODE_FLASH_DIRECT, None, "pre_cooldown_skip")

        # v3.3.0 F3 — infra suppression guard, AFTER step-0 override handling
        # (an explicit "anchor this" must never be swallowed by a cooldown).
        # The guard body lives in _infra_cooldown_skip() — Phase-2 plumbing,
        # INERT in Phase 1: dispatch behavior in static mode is byte-identical
        # to v3.2.3 (zero-behavior-change invariant; suppressed struggles would
        # still be re-classified + logged to avoid survivorship bias).
        # R19.13 FIX 1 (reviewer audit fix-first 2): the TRUSTED manual
        # on-demand line ('decide this[...]') is checked BEFORE the complexity
        # heuristic — complexity precedence (§5.3) must not swallow a trusted
        # manual trigger (live: analyst 'decide this:' turns consumed by
        # complexity risk_r2/orientation consults; zero manual_ask dispatches
        # in 24h). Heuristic PRE keeps complexity precedence — only the
        # manual line is elevated. Cooldowns above still apply (R16 doctrine:
        # override beats cooldown, manual does not).
        if _lane_enabled(LANE_DECISION) and override != "anchor":
            try:
                from . import decision as _dlane_m

                _dcfg_m = _decision_cfg()
                # R14-1 (rider 14): the trusted manual consult path is the
                # LAST injection un-wired seam — flag_prompt_injection ran
                # on the midturn seams + the risk leg only, so an
                # exfiltration clause bundled into a 'decide this:' turn
                # consulted (banner + ledger rows) with the clause glued
                # into the delivered option body and ZERO injection
                # eventing (live: t1r7 C3/C4/C7/E1-E4 — 0 fork_preserved
                # occurrences fleet-wide, all 7 injection scenarios
                # PARTIAL(fork_consulted_no_injection_event)). Contract:
                # flagged -> the preserved/suppressed event pair fires and
                # the clause is excised from the consult text so the
                # closed option set and the banner never carry exfil
                # wording.
                _inj_m = _prompt_injection_flag(user_text)
                _scan_text_m = user_text
                if _inj_m:
                    try:
                        from .core.telemetry import log_route as _lrj  # P1: core telemetry (import cycle broken)
                        _lrj("PRE", session_id=session_id,
                             event_detail="injection_flagged",
                             family="prompt_injection",
                             signal=str(_inj_m), lane=LANE_DECISION,
                             trigger="manual", seam="pre_dispatch",
                             task_id=task_id)
                    except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                        _obs_warn('injection_flagged', _obs_exc)  # rider 19 item 1
                    _scan_text_m = _strip_injection_clause(user_text)
                _mhit = _dlane_m.manual_line_hit(_scan_text_m, _dcfg_m)
                if (_mhit is not None and not list(_mhit.get("options") or [])
                        and not _dlane_m.has_declared_fork_structure(
                            _scan_text_m)):
                    # Rider 15 F4 (R15-8 pin option_less_decide): an
                    # option-less 'decide (on) this: <open question>' is an
                    # OPEN consult, not a Jev menu pick — the decision lane
                    # needs a closed option set. Route the open question as
                    # a FRONTIER consult (the anchored consult machinery:
                    # banner + ledger + spend) and leave prose two-option
                    # forms in the decision lane (post_fork_scan owns
                    # those). Declared/explicit anchors unaffected.
                    try:
                        # Rider 21 (FABLE-PIN) fail-closed: config with no
                        # frontier entry (anchor_chain.primary unresolvable)
                        # -> the consult does NOT fire (no route event, no
                        # staging, no silent fallback to any default model).
                        if _consult_no_frontier_config():
                            return _dec(LANE_UNCENSORED, MODE_FLASH_DIRECT,
                                        None, "consult_no_frontier_config")
                        from .core.telemetry import log_route as _lrf4  # P1: core telemetry (import cycle broken)
                        # Rider 18 R16-1: the route is a FRONTIER consult
                        # (manual open-question). Emit the frontier-lane-
                        # NAMED event first (t1r11 item 1: every four-leg
                        # battery pattern keys on the lane-named route
                        # event; the legacy anchor_route_fired
                        # lane=complexity event stays below for backward
                        # compat with the t1r10/r15 pin contract).
                        _lrf4("PRE", session_id=session_id,
                              event_detail="frontier_route_fired",
                              lane=LANE_COMPLEXITY, mode=MODE_CONSULT,
                              reason="manual_open_question_frontier",
                              model_target=_primary_model(),
                              task_id=task_id)
                        _lrf4("PRE", session_id=session_id,
                              event_detail="manual_open_question_frontier",
                              lane=LANE_COMPLEXITY, mode=MODE_CONSULT,
                              task_id=task_id)
                    except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                        _obs_warn('frontier_route_fired', _obs_exc)  # rider 19 item 1
                    return _dec(LANE_COMPLEXITY, MODE_CONSULT,
                                _primary_model(),
                                "manual_open_question_frontier",
                                orientation=False)
                if _mhit is None and _inj_m:
                    # explicit suppressed pair: the fork died with the
                    # clause cut (no fork consult on the flagged turn).
                    try:
                        from .core.telemetry import log_route as _lrj  # P1: core telemetry (import cycle broken)
                        _lrj("PRE", session_id=session_id,
                             event_detail="injection_flagged_fork_suppressed",
                             family="prompt_injection",
                             signal=str(_inj_m), lane=LANE_DECISION,
                             trigger="manual", seam="pre_dispatch",
                             reason="no_fork_survives", task_id=task_id)
                    except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                        _obs_warn('injection_flagged_fork_suppressed', _obs_exc)  # rider 19 item 1
                if _mhit is not None and _inj_m:
                    try:
                        from .core.telemetry import log_route as _lrj  # P1: core telemetry (import cycle broken)
                        _lrj("PRE", session_id=session_id,
                             event_detail="injection_flagged_fork_preserved",
                             family="prompt_injection",
                             signal=str(_inj_m), lane=LANE_DECISION,
                             trigger="manual", seam="pre_dispatch",
                             task_id=task_id)
                    except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                        _obs_warn('injection_flagged_fork_preserved', _obs_exc)  # rider 19 item 1
                    # R19.19 P1 (architect parity): a manual 'decide this:'
                    # ask on a gateway whose decision lane is DISABLED must
                    # be OBSERVED — silent-zero lane events are undiagnosable
                    # (architect: zero lane events on a manual fork).
                    try:
                        # Rider 19 item 1: _pkg_fn lives in route_gate and was
                        # NEVER imported here (NameError -> swallowed -> the
                        # decision_manual_suppressed event was dead in every
                        # context). Resolve it from route_gate at call time;
                        # package-namespace monkeypatch visibility preserved.
                        from .route_gate import _pkg_fn as _pkg_fn_gate
                        _pkg_fn_gate("_log_route")(
                            "PRE", event_detail="decision_manual_suppressed",
                            reason="lane_disabled",
                            session_id=str(session_id or ""))
                    except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                        _obs_warn('decision_manual_suppressed', _obs_exc)
                if _mhit is not None:
                    _mdec = _dec(LANE_DECISION, MODE_DECISION_SCORE, None,
                                 "decision_detected:manual_ask")
                    try:
                        from .core.telemetry import log_route as _lr  # P1: core telemetry (import cycle broken)
                        _lr("PRE", session_id=session_id,
                            event_detail="decision_route_fired",
                            lane=LANE_DECISION, mode=MODE_DECISION_SCORE,
                            reason="decision_detected:manual_ask",
                            route_id=_mdec.route_id,
                            task_id=task_id)
                    except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                        _obs_warn('decision_route_fired', _obs_exc)  # rider 19 item 1
                    # R8-4 (rider 8): the manual decision ask must NOT be
                    # decided between here and complexity/risk — a compound
                    # turn (fork wrapped in complexity work, live: operative
                    # C2 silent 0-fire; valmet D1b risk_r2 ate the ask) must
                    # consult BOTH lanes. The manual hit is STASHED: the
                    # complexity/risk stages below fire the decision consult
                    # ASYNC (parked advisory, consumes no turn slot) and
                    # route their own consult on the turn. When nothing
                    # downstream consults, the final decision stage returns
                    # the stashed manual decision (plain-ask behavior
                    # unchanged).
                    _manual_stack["hit"] = _mdec
                    _manual_stack["text"] = _scan_text_m
            except Exception:  # noqa: BLE001 — decision lane must never break dispatch
                pass

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
                    from .core.telemetry import log_route as _lr  # P1: core telemetry (import cycle broken)
                    _lr("PRE", session_id=session_id,
                        event_detail="complexity_pre_skip_system_injected",
                        task_id=task_id, level=level)
                except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                    _obs_warn('complexity_pre_skip_system_injected', _obs_exc)  # rider 19 item 1
                route_complex = False
            # Router tuning A1 (2026-09-09): verify-class exempt — short
            # imperative confirm/status asks skip PRE orientation entirely.
            # Override ("anchor this") beats the exempt (checked at step 0,
            # and re-guarded here so an exempt-class text carrying an explicit
            # override line still anchors).
            elif (_is_verify_class_exempt(user_text) and override != "anchor"):
                try:
                    from .core.telemetry import log_route as _lr  # P1: core telemetry (import cycle broken)
                    _lr("PRE", session_id=session_id,
                        event_detail="verify_class_exempt",
                        pattern_groups="clear_simple", task_id=task_id)
                except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                    _obs_warn('verify_class_exempt', _obs_exc)  # rider 19 item 1
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
            if (route_complex and override != "anchor"
                    and _manual_stack.get("hit") is None):
                # Rider 15 R15-7 (D1a residual): the benign-brief-frame gate
                # (R13-2) gated the risk hint leg and the POST leg (R14-2) but
                # NOT the PRE complexity/anchor consult — a benign setup brief
                # ('brief me on vitest vs jest') that parses 2-3 pseudo-options
                # still billed the anchor consult (live: valmet D1a, ledger row
                # frontier_adversarial_parse_fail). The brief-frame gate now
                # covers the anchor leg too: briefing/explaining frames with
                # no decision imperative never consult. Decision imperatives
                # are exempt inside the gate itself (decide-regex), so real
                # forks and 'decide (on) this' asks are unaffected.
                try:
                    from . import decision as _dlane_bf

                    if _dlane_bf._benign_brief_frame(user_text):
                        from .core.telemetry import log_route as _lrbf  # P1: core telemetry (import cycle broken)

                        _lrbf("PRE", session_id=session_id,
                              event_detail="complexity_pre_suppressed",
                              reason="benign_brief_frame",
                              stage1=str(meta.get("stage1")),
                              task_id=task_id, level=level)
                        route_complex = False
                except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                    _obs_warn('complexity_pre_suppressed', _obs_exc)  # rider 19 item 1
            if route_complex:
                # v3.6.1 PRE-orientation (Goran 09-08): the PRE consult no
                # longer plans the task — it delivers an ORIENTATION BRIEF:
                # what the result should look like, what to watch for,
                # common pitfalls + known good solutions, what to avoid,
                # what failure looks like. Advisory, non-binding ("suggests
                # but doesn't have to be followed"). Manual anchor override
                # keeps direct-consult semantics (frontier answers the ask).
                if override == "anchor":
                    if _consult_no_frontier_config():  # R21 FABLE-PIN fail-closed
                        return _dec(LANE_UNCENSORED, MODE_FLASH_DIRECT, None,
                                    "consult_no_frontier_config")
                    return _dec(LANE_COMPLEXITY, MODE_CONSULT, _primary_model(),
                                "complexity_" + str(meta.get("stage", "stage1")),
                                override, orientation=False)
                # R16: auto-lane consult candidate — cooldown check on the
                # normalized content hash (declared/manual never reaches here).
                if _consult_cooldown_active(session_id, user_text):
                    return _dec(LANE_UNCENSORED, MODE_FLASH_DIRECT, None,
                                "consult_cooldown_suppressed")
                _record_cooldown_fire(session_id,
                                      _cooldown_hash(session_id, user_text))
                # R8-4 (rider 8): compound turn — decision consult fires
                # async BEFORE the orientation consult routes.
                _fire_decision_stack()
                if _consult_no_frontier_config():  # R21 FABLE-PIN fail-closed
                    return _dec(LANE_UNCENSORED, MODE_FLASH_DIRECT, None,
                                "consult_no_frontier_config")
                return _dec(LANE_COMPLEXITY, MODE_CONSULT, _primary_model(),
                            "complexity_orientation",
                            orientation=True)
            if override == "anchor":
                # explicit ask outranks a "clear_simple" verdict at any level:
                # manual-only semantics (L1) and the inline override contract.
                if _consult_no_frontier_config():  # R21 FABLE-PIN fail-closed
                    return _dec(LANE_UNCENSORED, MODE_FLASH_DIRECT, None,
                                "consult_no_frontier_config")
                return _dec(LANE_COMPLEXITY, MODE_CONSULT, _primary_model(),
                            "override_anchor", override)
        elif override == "anchor":
            # L0 with explicit ask: honor the manual anchor.
            if _lane_enabled(LANE_COMPLEXITY):
                if _consult_no_frontier_config():  # R21 FABLE-PIN fail-closed
                    return _dec(LANE_UNCENSORED, MODE_FLASH_DIRECT, None,
                                "consult_no_frontier_config")
                return _dec(LANE_COMPLEXITY, MODE_CONSULT, _primary_model(),
                            "override_anchor", override)

        # R15 LEG 1 — risk-triggered frontier consult (PRE). After the
        # complexity lane: risk detection is orthogonal to complexity and
        # the complexity lane must stay byte-identical. Consult mechanics
        # identical (MODE_CONSULT, orientation brief, advisory, non-
        # binding); reason carries the risk class. "audit_only"/"off"
        # modes never PRE-consult. Never blocks: this returns a consult
        # decision, not a block — fail-open on any error.
        try:
            from . import risk as _risk

            if _risk.risk_enabled() and override != "anchor":
                _rcfg = _risk.risk_cfg()
                _rmode = str(_rcfg.get("mode") or "consult").strip().lower()
                if _rmode == "consult" and bool(_rcfg.get("pre_lexicon", True)):
                    # Rider 19 item 3 (BC2 analyst false-consult): STRUCTURAL
                    # default-deny for clean non-steering opinion forms. A
                    # first-person opinion ask ("What's your substantive take
                    # on X versus Y? One pick and a short defense.") carries
                    # no manual trigger, no declared closed-fork structure,
                    # and no imperative — but the stage1 risk lexicon reads
                    # the trade-off frame as risky (live: analyst BC2 clean
                    # ask, anchor_route_fired reason=risk_r2 @ 22:40:43Z,
                    # zero manual triggers). The risk consult STANDS DOWN:
                    # opinion prose is the analyst's job, not a frontier
                    # consult. All three structural conditions must hold —
                    # real risky asks (imperatives, declared forks, manual
                    # triggers, injection clauses) never reach this arm.
                    if _clean_opinion_tradeoff(user_text) \
                            and _no_manual_trigger_pre(user_text) \
                            and not _declared_fork_pre(user_text):
                        _risk_stand_down = True
                        from .core.telemetry import log_route as _lr  # P1: core telemetry (import cycle broken)
                        try:
                            _lr("PRE", session_id=session_id,
                                event_detail="risk_pre_stand_down_clean_opinion",
                                task_id=task_id)
                        except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                            _obs_warn('risk_pre_stand_down_clean_opinion', _obs_exc)
                    else:
                        _risk_stand_down = False
                    if _risk_stand_down:
                        # clean opinion: the risk consult stands down; the
                        # turn falls through (the decision lane still owns
                        # any manual/fork content, none is present here).
                        pass  # structural default-deny — no frontier consult
                    # R10-4 (rider 10, C3): the complexity leg quarantines
                    # system-injected turns (_is_system_injected_turn);
                    # the risk leg did NOT — an injection-marker clause
                    # whose wording lexically hits the risk co-occurrence
                    # rule routed reason=risk_r2 AND short-circuited the
                    # decision leg below it (silent fork loss: no consult,
                    # no event). Same quarantine + distinct event.
                    if not _risk_stand_down and _is_system_injected_turn(user_text):
                        try:
                            from .core.telemetry import log_route as _lr  # P1: core telemetry (import cycle broken)
                            _lr("PRE", session_id=session_id,
                                event_detail="risk_pre_skip_system_injected",
                                task_id=task_id)
                        except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                            _obs_warn('risk_pre_skip_system_injected', _obs_exc)  # rider 19 item 1
                    elif _prompt_injection_flag(user_text):
                        # R13-1 (rider 13): an exfiltration-style injection
                        # clause bundled into the user turn is untrusted
                        # noise, not ask content — the risk lexicon reads it
                        # as risky ('delete', 'staging database') and bills
                        # a consult (live: analyst C3 anchor_route_fired
                        # reason=risk_r2 on the injection ask). Skip the
                        # risk consult; the decision lane owns the declared
                        # fork and eventsthe preserved/suppressed pair.
                        try:
                            from .core.telemetry import log_route as _lr  # P1: core telemetry (import cycle broken)
                            _lr("PRE", session_id=session_id,
                                event_detail="risk_pre_skip_injection_clause",
                                task_id=task_id)
                        except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                            _obs_warn('risk_pre_skip_injection_clause', _obs_exc)  # rider 19 item 1
                    else:
                        _rcls, _rmeta = _risk.classify(
                            user_text,
                            pre_lexicon=True,
                            semantic_stage2=bool(_rcfg.get("semantic_stage2", True)),
                        )
                        if _rcls in ("r2", "r3"):
                            # R16: risk auto-consult inherits the same cooldown
                            # map (same normalized-hash keying; risk_r2/r3 only).
                            if _consult_cooldown_active(session_id, user_text):
                                return _dec(LANE_UNCENSORED, MODE_FLASH_DIRECT,
                                            None, "consult_cooldown_suppressed")
                            _record_cooldown_fire(
                                session_id, _cooldown_hash(session_id, user_text))
                            try:
                                from .core.telemetry import log_route as _lr  # P1: core telemetry (import cycle broken)
                                _lr("PRE", session_id=session_id,
                                    event_detail="risk_consult_fire",
                                    risk_class=_rcls,
                                    stage=str(_rmeta.get("stage") or "stage1"),
                                    task_id=task_id)
                            except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                                _obs_warn('risk_consult_fire', _obs_exc)  # rider 19 item 1
                            # R10-4 (rider 10, C3): rider-8 compound parity —
                            # fire a stashed manual decision consult async
                            # BEFORE the risk consult routes, exactly like the
                            # complexity-orientation return does; otherwise the
                            # risk return short-circuits the decision leg.
                            _fire_decision_stack()
                            if _consult_no_frontier_config():  # R21 FABLE-PIN fail-closed
                                return _dec(LANE_UNCENSORED, MODE_FLASH_DIRECT,
                                            None, "consult_no_frontier_config")
                            return _dec(LANE_COMPLEXITY, MODE_CONSULT,
                                        _primary_model(),
                                        "risk_" + str(_rcls), orientation=True)
        except Exception:  # noqa: BLE001 — risk lane must never break dispatch
            pass

        # R19 Lane 3 — decision (v0 DARK). After complexity (complexity keeps
        # precedence) and after the risk leg: cheap regex detection only; the
        # scorer runs off the turn path (decision.handle_decision is invoked
        # by dispatcher_pre._dispatch_pass on this lane/mode). Single ladder:
        # the advisory escalates into MODE_CONSULT on low confidence — no
        # dead zone (frontier #1). Any error -> fail-open to flash-direct.
        # R8-4 (rider 8): a stashed manual hit that no complexity/risk
        # consult stacked on is returned HERE — plain manual ask, behavior
        # byte-identical to the pre-rider early return (no consult cooldown
        # suppression; R16 manual semantics preserved).
        if _manual_stack.get("hit") is not None:
            return _manual_stack["hit"]
        try:
            from . import decision as _dlane

            if _lane_enabled(LANE_DECISION) and override != "anchor":
                _dcfg = _decision_cfg()
                # R19.1 LEG 1: platform-provenance guard BEFORE detection —
                # live replay showed 20-26% of decision detections fired on
                # orchestrator/coder dispatch digests wearing user-role
                # costume. Reason-coded log, then flash-direct.
                _prov_skip = False
                _dhit = None
                try:
                    _prov_skip = bool(_dlane.provenance_skip(user_text, _dcfg))
                except Exception:  # noqa: BLE001 — when in doubt, detect
                    _prov_skip = False
                if _prov_skip or _is_system_injected_turn(user_text):
                    if _prov_skip:
                        try:
                            from .core.telemetry import log_route as _lr  # P1: core telemetry (import cycle broken)
                            _lr("PRE", session_id=session_id,
                                event_detail="decision_provenance_skip",
                                lane=LANE_DECISION, task_id=task_id)
                        except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                            _obs_warn('decision_provenance_skip', _obs_exc)  # rider 19 item 1
                    if _is_system_injected_turn(user_text):
                        # R10-4 (rider 10, C3): an injection-marker turn must
                        # never suppress the decision leg SILENTLY — same
                        # WARN-visibility doctrine as the complexity leg's
                        # complexity_pre_skip_system_injected event.
                        try:
                            from .core.telemetry import log_route as _lr2  # P1: core telemetry (import cycle broken)
                            _lr2("PRE", session_id=session_id,
                                 event_detail="decision_injected_suppressed",
                                 lane=LANE_DECISION, task_id=task_id)
                        except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                            _obs_warn('decision_injected_suppressed', _obs_exc)  # rider 19 item 1
                else:
                    # R19 v3: v3 detection (manual on-demand line, skip bypass,
                    # heuristic pre). Complexity already had its chance above —
                    # lane precedence §5.3 (complexity > heuristic PRE).
                    _dhit = _dlane.detect_v3(user_text,
                                             int(_dcfg.get("level") or 2),
                                             cfg=_dcfg)
                if _dhit is not None and _dhit.get("trigger") == "skip":
                    # "skip decision" bypass — turn proceeds unchanged.
                    return _dec(LANE_UNCENSORED, MODE_FLASH_DIRECT,
                                None, "decision_skipped")
                if _dhit is not None and _dhit.get("trigger") == "provenance_skip":
                    # R19.1 LEG 1: platform envelope — never detect on it.
                    # Log the reason code and fall through to flash-direct.
                    try:
                        from .core.telemetry import log_route as _lr  # P1: core telemetry (import cycle broken)
                        _lr("PRE", session_id=session_id,
                            event_detail="decision_provenance_skip",
                            lane=LANE_DECISION, task_id=task_id)
                    except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                        _obs_warn('decision_provenance_skip', _obs_exc)  # rider 19 item 1
                    _dhit = None
                if _dhit is not None and _dhit.get("trigger") == "manual" \
                        and not _dlane.on_demand_allowed("manual", _dcfg):
                    _dhit = None
                if _dhit:
                    if _consult_cooldown_active(session_id, user_text):
                        return _dec(LANE_UNCENSORED, MODE_FLASH_DIRECT,
                                    None, "consult_cooldown_suppressed")
                    _record_cooldown_fire(
                        session_id, _cooldown_hash(session_id, user_text))
                    # R8-4 (rider 8): compound turn — decision consult fires
                    # async BEFORE the risk consult routes.
                    _fire_decision_stack()
                    try:
                        from .core.telemetry import log_route as _lr  # P1: core telemetry (import cycle broken)
                        _lr("PRE", session_id=session_id,
                            event_detail="decision_detect_fire",
                            lane=LANE_DECISION,
                            families=",".join(_dhit["families"]),
                            level=int(_dhit.get("level") or 2),
                            task_id=task_id)
                    except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                        _obs_warn('decision_detect_fire', _obs_exc)  # rider 19 item 1
                    return _dec(LANE_DECISION, MODE_DECISION_SCORE, None,
                                "decision_detected:"
                                + ",".join(_dhit["families"]))
        except Exception:  # noqa: BLE001 — decision lane must never break dispatch
            pass

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


def _consult_no_frontier_config() -> bool:
    """Rider 21 (FABLE-PIN) fail-closed: True when config carries NO
    frontier/consult entry (anchor_chain.primary unresolvable) — consults
    must NOT fire (no silent fallback to any hardcoded/default model).
    Never raises."""
    try:
        return _primary_model() is None
    except Exception:  # noqa: BLE001
        return True


# ---------------------------------------------------------------------------
# Pending swap (PRE decision -> llm_execution middleware handoff)
# ---------------------------------------------------------------------------


def stage_model_swap(session_id: str, decision: RouteDecision,
                     role: str = "primary",
                     model_override: Optional[Tuple[str, str]] = None,
                     claim_source: str = "",
                     payload: Any = None) -> Optional[Dict[str, Any]]:
    """Called by PRE after a COMPLEXITY decision: stage the per-call swap so
    the NEXT llm_execution middleware invocation (same session) performs the
    anchored call. Per-call, never persistent — the record is consumed once.

    R16-2c (rider 16): the record carries a bounded deep COPY of the staged
    request payload (`payload=`) so a POST delivery edge can recover an
    ORPHANED swap (staged at PRE but never consumed by any llm_execution
    pass of the turn — live: analyst A2 declared 'ask your higher self' ask,
    staged=True 17:47:22, zero anchor events, 0 banners 0 rows). The copy is
    dropped with the consume; capped by the 60s TTL. Never raises.

    R6 leg 1: model_override=(alias, model_id) builds the swap endpoint from
    the CONFIGURED primary's scheme/base/key with ONLY the model id swapped
    (one-off; config never written). Caps/pricing/ledger run on the resolved
    model id exactly as a normal consult.

    R7 (Goran 09-13): model override is USER-ONLY. When an override is
    provided and claim_source != "declared_user", the override is DROPPED
    (belt-and-braces: logged model_override_rejected, content-free) and the
    consult proceeds with the configured anchor primary endpoint.

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
                from .core.telemetry import log_route as _log_route  # P1: core telemetry (import cycle broken)

                with _PENDING_SWAP_LOCK:
                    rec = _ANCHOR_FAIL_BACKOFF.get(key) or {}
                fails = int(rec.get("fails", 0))
                _log_route("PRE", event_detail="anchor_backoff_blocked",
                           fails=fails, backoff_s=round(anchor_backoff_window(fails), 1),
                           task_id=key[1], session_id=key[0])
            except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                _obs_warn('anchor_backoff_blocked', _obs_exc)  # rider 19 item 1
            return None
        chain = anchor_chain.load_anchor_chain()
        ep = chain.endpoint_for(role)
        if ep is None:
            return None
        # R7: model override is USER-ONLY — drop non-user overrides and
        # proceed with the config endpoint (logged, content-free).
        if model_override and claim_source != "declared_user":
            try:
                from .core.telemetry import log_route as _log_route  # P1: core telemetry (import cycle broken)

                _log_route("PRE", event_detail="model_override_rejected",
                           source=str(claim_source or ""),
                           task_id=str(decision.task_id or ""),
                           session_id=str(session_id or ""))
            except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                _obs_warn('model_override_rejected', _obs_exc)  # rider 19 item 1
            model_override = None
        # R6 leg 1: named-model override — same scheme/base/key, only the
        # model id changes (one-off; config never written).
        try:
            if model_override and isinstance(model_override, tuple) \
                    and len(model_override) == 2 and str(model_override[1]).strip():
                ep = anchor_chain.override_endpoint(ep, model_override[1])
        except Exception:  # noqa: BLE001 — override is best-effort
            pass
        # Rider 17 R16-1: a consult must run a REAL provider model id, never
        # the bare alias-like token the configured primary may itself be
        # ('nous://fable' -> provider 404 "Model 'fable' not found"). When
        # the lane catalog resolves the target to a CONCRETE id different
        # from the endpoint model, upgrade the endpoint in-memory (config is
        # never written). Aliases and explicit overrides are untouched.
        try:
            if not model_override:
                _lane_scope = ("uncensored"
                               if str(getattr(decision, "lane", "")) ==
                               LANE_UNCENSORED else "frontier")
                _tgt = (str(getattr(decision, "model_target", "") or "").strip()
                        or str(ep.model or "").strip())
                if _tgt:
                    _resolved = anchor_chain.resolve_model_alias(_tgt,
                                                                 _lane_scope)
                    if _resolved and str(_resolved[1]).strip() \
                            and str(_resolved[1]).strip() != str(ep.model or "").strip():
                        ep = anchor_chain.override_endpoint(ep, _resolved[1])
        except Exception:  # noqa: BLE001 — resolution is best-effort
            pass
        # Rider 21 (FABLE-PIN): a fable-family target fires ONLY on explicit
        # per-request specification (the user's own declared_user named-model
        # override naming fable). Any other path to a fable id — config
        # primary, alias/catalog resolution, staging defaults — is refused:
        # fail-CLOSED, no staging (no billed consult, no banner), logged
        # fable_target_not_explicit. The config frontier primary is used
        # verbatim, never deviated from.
        try:
            _final_model = str(getattr(ep, "model", "") or "").lower()
            if "fable" in _final_model:
                _named_fable = (
                    isinstance(model_override, tuple) and len(model_override) == 2
                    and "fable" in str(model_override[1] or "").lower()
                    and str(claim_source or "") == "declared_user")
                if not _named_fable:
                    try:
                        from .core.telemetry import log_route as _log_route  # P1: core telemetry (import cycle broken)
                        _log_route("PRE", event_detail="fable_target_not_explicit",
                                   model=str(getattr(ep, "model", "")),
                                   reason=str(getattr(decision, "reason", "") or ""),
                                   task_id=str(getattr(decision, "task_id", "") or ""),
                                   session_id=str(session_id or ""))
                    except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                        _obs_warn('fable_target_not_explicit', _obs_exc)
                    return None
        except Exception:  # noqa: BLE001 — the guard is fail-closed by intent
            pass
        rec = {
            "route_id": decision.route_id,
            "task_id": decision.task_id,
            "mode": decision.mode,
            "orientation": bool(getattr(decision, "orientation", False)),
            "role": role,
            "endpoint": ep,
            "staged_at": now,
        }
        # R16-2c: bounded deep copy of the staged request payload for the
        # POST-edge orphan recovery. Fail-open — a copy failure stages the
        # swap WITHOUT the payload (recovery then fail-louds without retry).
        if payload is not None:
            try:
                rec["payload"] = copy.deepcopy(payload)
            except Exception:  # noqa: BLE001 — copy is best-effort
                rec["payload"] = None
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
        # v3.8.1 P1-2: mark sidecar consumed so tests never re-warm from the
        # real profile home mid-test; the sidecar itself is not deleted.
        global _backoff_sidecar_loaded
        _backoff_sidecar_loaded = True