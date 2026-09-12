"""Unified cascade route gate for hermes-router (Phase 1, leg 1 —
BLUEPRINT-request-routing-2026-09-12, reviewer verdict HARDEN all-folded).

CASCADE (topology rule, Goran directive):

    SEAM (turn boundary) -> GATE (route/no-route) -> CLASSIFY (inside the
    gate's yes-branch only) -> LANE EXEC (on_llm_execution staged swap).

The gate is the SINGLE decision point for "does THIS turn route at all".
Classification (legacy auto-classifiers, match logic reused VERBATIM via
late-bound imports — policy frozen) is consulted only inside the gate's
yes-branch, as a gate INPUT. Execution is NOT unified: the flash-proceeds
staged swap at on_llm_execution is untouched, and the 4 dedupe sites
(SWAP_DONE, loop guard, canonical idempotency, pre_fired_this_turn) are
FROZEN as-is (reviewer H3/F4).

Two seams, one decision function — byte-identical placement:

  - `fence_pass`  runs at the exact position of the legacy sentinel
    early-return + _audit_delivery_pass early-return in on_llm_request.
    The audit delivery is an EXPLICIT no-route branch OF the gate
    (reviewer F4/H3) — not a bypass.
  - `claim_pass`  runs at the exact position of the legacy complexity
    dispatch call; it invokes _dispatch_pass VERBATIM as the auto-shape
    gate input (one classification evaluation — no double aux calls) and
    evaluates the declared on-demand input (precedence: sentinel ->
    skip-anchor -> declared request -> auto-shape, reviewer H4).
  - `decide_turn` is the composite decision function (fence logic +
    claim logic) — the single claim point used by tests and by future
    legs; the two seam functions keep production call positions
    byte-identical. Both fence checks are idempotent (the sentinel is
    pure; the audit verdict is consume-once), so the composite is safe
    to evaluate across seams.

Gate output: no-route (DEFAULT, fail-open — the gate NEVER raises) |
route{lane: higher-pre|higher-post|shadow, source: auto|declared_user|
declared_agent}.

Kill-switch (reviewer H7.4): config knob `on_demand_routing: on|off`
(config-live read via config_access) disables the ON-DEMAND declared
input independently of the auto lanes.

Input-side echo guard (reviewer H7.2): declared trigger phrases are
inert inside quoted/echoed/meta content — detection applies to
turn-start standalone directive lines only (whole-line match, same
discipline as complexity.detect_override), and quoted lines (blockquote
or quote-char prefixed) never match.

State-leak guard (reviewer H7.1): declared-claim state and parked
banners are released on execution failure via `claim_execution_guard`;
cap/cooldown counters increment only on successful claim (inherited
from the frozen _dispatch_pass claim semantics — staged swap or
nothing).

Double-declare dedupe (reviewer H7.5): agent request_routing + user
phrase in the same turn resolve to ONE claim (first declaration wins;
the second declarer sees the existing fresh claim and does not create
a second consult/ledger entry). Mutable gate state is single-owner:
this module owns _DECLARED_CLAIMS; nothing else writes it.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gate vocabulary
# ---------------------------------------------------------------------------

LANE_HIGHER_PRE = "higher-pre"
LANE_HIGHER_POST = "higher-post"
LANE_SHADOW = "shadow"

SOURCE_AUTO = "auto"
SOURCE_DECLARED_USER = "declared_user"
SOURCE_DECLARED_AGENT = "declared_agent"

# Declared-user on-demand phrases (explicit intent only — no prose
# mind-reading). Turn-start standalone directive lines; Leg 3 wires the
# execution envelope, the gate owns detection + precedence now.
DECLARED_USER_PHRASES: Dict[str, str] = {
    "ask your higher self": LANE_HIGHER_PRE,
    "route this through your shadow": LANE_SHADOW,
}

# Lines starting with these are quoted/echoed content — never a command
# surface (echo guard, reviewer H7.2).
_QUOTE_LINE_PREFIXES = (">", '"', "'", ")")

# Declared-claim freshness window (one consult per turn; mirrors the
# pending-routes TTL scale).
_CLAIM_TTL_SECONDS = 300.0


@dataclass
class GateDecision:
    """One gate decision. route=False (no-route) is the fail-open default."""

    route: bool = False
    lane: Optional[str] = None
    source: Optional[str] = None
    reason: str = "no_route"
    deliver: Optional[dict] = None  # envelope the caller must return (audit delivery)


NO_ROUTE = GateDecision()


# ---------------------------------------------------------------------------
# Seams (late-bound — tests monkeypatch package attrs; seam doctrine R5)
# ---------------------------------------------------------------------------


def _pkg():
    """Package-module late-binding seam (same contract as dispatcher_pre)."""
    import sys as _sys

    return _sys.modules[__package__]


def _pkg_fn(name: str, fallback_module_attr: Optional[tuple] = None) -> Callable:
    """Resolve a helper through the package namespace at CALL time so test
    monkeypatches on the package are always seen; fall back to the owning
    module when the package attr is absent."""
    try:
        fn = getattr(_pkg(), name, None)
        if callable(fn):
            return fn
    except Exception:  # noqa: BLE001
        pass
    if fallback_module_attr is not None:
        mod_name, attr = fallback_module_attr
        import importlib

        return getattr(importlib.import_module("." + mod_name, __package__), attr)
    raise AttributeError(name)


def _config_access():
    from . import config_access

    return config_access


# ---------------------------------------------------------------------------
# Config knobs (config-live reads — no bounce)
# ---------------------------------------------------------------------------


def on_demand_routing_enabled() -> bool:
    """Kill-switch knob `on_demand_routing: on|off` (reviewer H7.4).
    Disables the on-demand DECLARED input independently of the auto lanes.
    Default ON. Never raises."""
    try:
        raw = _config_access().router_section().get("on_demand_routing")
        if raw is None:
            return True
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("on", "true", "1", "yes")
    except Exception:  # noqa: BLE001 — fail-open: knob errors never block the gate
        return True


# ---------------------------------------------------------------------------
# Input-side echo guard + declared-user detection (reviewer H7.2)
# ---------------------------------------------------------------------------


def _directive_lines(content: str):
    """Yield normalized whole directive lines from the turn-start command
    surface. Quoted/echoed lines (blockquote or quote-char prefixed) are
    SKIPPED — trigger phrases inside quoted/meta content are inert."""
    if not isinstance(content, str):
        return
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith(_QUOTE_LINE_PREFIXES):
            continue
        yield stripped.strip("`*# ").lower()


def detect_declared_user(content: str) -> Optional[str]:
    """Lane for a declared-user on-demand phrase appearing as a standalone
    directive line, else None. Prose mentioning a phrase (echo/meta) never
    matches — the phrase must BE the line. Never raises."""
    try:
        for norm in _directive_lines(content):
            lane = DECLARED_USER_PHRASES.get(norm)
            if lane is not None:
                return lane
        return None
    except Exception:  # noqa: BLE001
        return None


def _skip_anchor_requested(content: str) -> bool:
    """`skip anchor` bypass (doctrine #6) — reused VERBATIM from
    complexity.detect_override (import, not copy). Never raises."""
    try:
        from . import complexity

        return complexity.detect_override(content) == "skip"
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Declared-claim state (single-owner: route_gate only)
# ---------------------------------------------------------------------------

_CLAIM_LOCK = threading.Lock()
_DECLARED_CLAIMS: Dict[str, Dict[str, Any]] = {}


def _gc_claims_locked(now: float) -> None:
    stale = [k for k, v in _DECLARED_CLAIMS.items()
             if now - float(v.get("ts") or 0.0) > _CLAIM_TTL_SECONDS]
    for k in stale:
        _DECLARED_CLAIMS.pop(k, None)


def register_declared(session_id: str, lane: str,
                      source: str = SOURCE_DECLARED_AGENT) -> bool:
    """Register a declared routing claim (Leg 3 wires the agent action to
    this). Returns False when a fresh claim already exists for the session
    — the double-declare dedupe (reviewer H7.5): one consult, one ledger
    entry. Never raises."""
    try:
        now = time.time()
        with _CLAIM_LOCK:
            _gc_claims_locked(now)
            existing = _DECLARED_CLAIMS.get(str(session_id or ""))
            if existing is not None:
                return False
            _DECLARED_CLAIMS[str(session_id or "")] = {
                "lane": str(lane), "source": str(source), "ts": now}
            return True
    except Exception:  # noqa: BLE001
        return False


def peek_declared(session_id: str) -> Optional[Dict[str, Any]]:
    """Fresh declared claim for the session, if any (read-only). Never raises."""
    try:
        now = time.time()
        with _CLAIM_LOCK:
            _gc_claims_locked(now)
            existing = _DECLARED_CLAIMS.get(str(session_id or ""))
            if existing is None:
                return None
            return dict(existing)
    except Exception:  # noqa: BLE001
        return None


def clear_declared(session_id: str) -> None:
    """Test/recovery hook — drop the session's declared claim. Never raises."""
    try:
        with _CLAIM_LOCK:
            _DECLARED_CLAIMS.pop(str(session_id or ""), None)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# State-leak guard (reviewer H7.1)
# ---------------------------------------------------------------------------


@contextmanager
def claim_execution_guard(session_id: str):
    """H7.1: a flaky-provider day must never leak routing state. On
    execution failure INSIDE the guarded block, release the session's
    parked banner (one-shot consume — it can never attach to a later,
    unrelated turn) and drop the declared claim, then RE-RAISE so the
    executor's own fail-open handling applies. Cooldown/cap counters are
    not touched here: the frozen claim path increments them only on
    successful claim (staged swap or nothing)."""
    try:
        yield
    except Exception:
        try:
            from . import debug_banner as _db

            _db.consume_parked_banner(session_id)  # release parked banner
        except Exception:  # noqa: BLE001
            logger.debug("claim guard banner release error", exc_info=True)
        try:
            clear_declared(session_id)
        except Exception:  # noqa: BLE001
            pass
        raise


# ---------------------------------------------------------------------------
# The gate — decide_turn + the two production seams
# ---------------------------------------------------------------------------


def _declared_decision(content: str, session_id: str) -> GateDecision:
    """Declared on-demand input: echo-guarded user phrase + double-declare
    dedupe against an existing agent claim. User phrase checked FIRST
    (explicit user ask outranks; blueprint §3 cost rules)."""
    existing = peek_declared(session_id)
    user_lane = detect_declared_user(content)
    if user_lane is not None:
        if existing is not None:
            # H7.5: agent request_routing + user phrase same turn — ONE
            # consult, ONE ledger entry. The first declaration wins; this
            # is the same claim, not a second one.
            return GateDecision(route=True, lane=str(existing["lane"]),
                                source=str(existing["source"]),
                                reason="declared_deduped")
        register_declared(session_id, user_lane, SOURCE_DECLARED_USER)
        return GateDecision(route=True, lane=user_lane,
                            source=SOURCE_DECLARED_USER,
                            reason="declared_user")
    if existing is not None and existing.get("source") == SOURCE_DECLARED_AGENT:
        return GateDecision(route=True, lane=str(existing["lane"]),
                            source=SOURCE_DECLARED_AGENT,
                            reason="declared_agent")
    return NO_ROUTE


def decide_turn(ctx: Dict[str, Any]) -> GateDecision:
    """THE single claim point. Precedence (reviewer H4):
      sentinel -> skip-anchor -> declared request -> auto-shape.
    Fail-open default: ANY error -> NO_ROUTE; the gate NEVER raises.

    ctx keys:
      content     raw turn-start user text (str)
      request     middleware request payload (audit-delivery fence)
      context     middleware context dict (audit-delivery fence)
      session_id  session id for declared-claim state
      auto_shape  optional callable -> GateDecision; the legacy
                  classification consulted VERBATIM inside the gate's
                  yes-branch (gate INPUT, policy frozen). When absent the
                  gate reports no-route after the fence chain.
    """
    try:
        content = ctx.get("content") or ""
        request = ctx.get("request")
        context = ctx.get("context") or {}
        session_id = str(ctx.get("session_id") or "")

        # 1. Sentinel firewall (routed-turn output never re-enters the gate).
        sentinel_check = _pkg_fn("_frame_sentinel_check",
                                 ("dispatcher_pre", "_frame_sentinel_check"))
        if sentinel_check(content):
            return GateDecision(route=False, reason="sentinel_firewall")

        # 2. Completion-audit delivery — EXPLICIT no-route branch OF the
        #    gate (reviewer F4/H3), not a bypass. The gate owns the
        #    delivery envelope; the caller returns it untouched.
        audit_fn = _pkg_fn("_audit_delivery_pass",
                           ("dispatcher_pre", "_audit_delivery_pass"))
        audit_env = audit_fn(request, context)
        if audit_env is not None:
            return GateDecision(route=False, reason="audit_delivery_no_route",
                                deliver=audit_env)

        # 3. `skip anchor` bypass — outranks every declared request.
        if _skip_anchor_requested(content):
            return GateDecision(route=False, reason="override_skip")

        # 4. Declared on-demand input — behind the kill-switch (H7.4).
        if on_demand_routing_enabled():
            declared = _declared_decision(content, session_id)
            if declared.route or declared.reason == "declared_deduped":
                return declared

        # 5. Auto-shape input — legacy classification, consulted VERBATIM
        #    only here, inside the gate's yes-branch (gate INPUT; policy
        #    frozen; execution stays at on_llm_execution).
        auto_shape = ctx.get("auto_shape")
        if callable(auto_shape):
            shaped: Any = auto_shape()
            if isinstance(shaped, GateDecision):
                return shaped

        return NO_ROUTE
    except Exception:  # noqa: BLE001 — the gate NEVER raises into a turn
        logger.debug("route gate decision error", exc_info=True)
        return NO_ROUTE


def fence_pass(content: str, request: Any, context: Dict[str, Any],
               hs_pass: Callable[[], dict]) -> Optional[dict]:
    """Production seam 1 — the gate's explicit no-route branches at the
    exact legacy position in on_llm_request (sentinel early-return +
    _audit_delivery_pass early-return). Returns the envelope to return
    from the middleware, or None to continue into the gate's claim phase.
    Never raises."""
    try:
        decision = decide_turn({"content": content, "request": request,
                                "context": context, "session_id": "",
                                "auto_shape": None})
        if decision.deliver is not None:
            return decision.deliver
        if decision.reason == "sentinel_firewall":
            return hs_pass() or {}
    except Exception:  # noqa: BLE001 — fence must never break the middleware
        logger.debug("route gate fence error", exc_info=True)
    return None


def claim_pass(content: str, session_id: str, model: str,
               request: Any = None, context: Optional[Dict[str, Any]] = None
               ) -> GateDecision:
    """Production seam 2 — the gate's claim phase at the exact position of
    the legacy complexity dispatch call. Precedence inside: sentinel ->
    skip-anchor -> declared -> auto-shape (the legacy _dispatch_pass,
    invoked VERBATIM as the auto-shape gate input — one classification
    evaluation, no double aux calls). Returns the GateDecision; the
    caller returns its pass-through envelope when route=True (identical
    to the legacy `if _dispatch_pass(...): return _hs_pass()` contract).
    Never raises."""

    def _auto_shape() -> GateDecision:
        routed = bool(_pkg_fn("_dispatch_pass",
                              ("dispatcher_pre", "_dispatch_pass"))(
            content, session_id, model))
        if routed:
            return GateDecision(route=True, lane=LANE_HIGHER_PRE,
                                source=SOURCE_AUTO, reason="complexity_claim")
        return NO_ROUTE

    decision = decide_turn({"content": content, "request": request,
                            "context": context or {}, "session_id": session_id,
                            "auto_shape": _auto_shape})
    if decision.route and decision.source in (SOURCE_DECLARED_USER,
                                              SOURCE_DECLARED_AGENT):
        # Declared claim decided; Leg 3 wires the envelope execution. The
        # log is the gate-input observability surface until then.
        try:
            _pkg_fn("_log_route")("PRE", event_detail="request_routing_declared",
                                  lane=decision.lane, source=decision.source,
                                  reason=decision.reason, session_id=session_id)
        except Exception:  # noqa: BLE001
            pass
    return decision
