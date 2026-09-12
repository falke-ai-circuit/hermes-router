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
# mind-reading). Turn-start standalone directive lines; the execution
# envelope rides the EXISTING staged-swap machinery (Leg 3: the
# request_routing action stages the claim; on_llm_execution consumes it).
DECLARED_USER_PHRASES: Dict[str, str] = {
    "ask your higher self": LANE_HIGHER_PRE,
    "route this through your shadow": LANE_SHADOW,
    "anchor this": LANE_HIGHER_PRE,
}

# A directive line may carry a PAYLOAD after the phrase: '<phrase>: rest',
# '<phrase> - rest', '<phrase> — rest', or '<phrase>?...' — the phrase
# PREFIX claims the lane, the remainder is the consult payload (canary
# live-probe regression, leg 5: 'ask your higher self: what is one blind
# spot?' previously missed the whole-line match).
_PHRASE_PAYLOAD_SEPARATORS = (":", " -", " —")

# Lanes a DECLARED claim may target (agent action + user phrase surface).
VALID_ROUTE_LANES = (LANE_HIGHER_PRE, LANE_HIGHER_POST, LANE_SHADOW)

# Lines starting with these are quoted/echoed content — never a command
# surface (echo guard, reviewer H7.2).
_QUOTE_LINE_PREFIXES = (">", '"', "'", ")")

# Declared-claim freshness window (one consult per turn; mirrors the
# pending-routes TTL scale).
_CLAIM_TTL_SECONDS = 300.0

# Leg 6 (initiator provenance): task_id -> claim source ("user"|"agent"|
# "auto"), stamped at claim time in claim_pass, read by the billing sites
# (anchor_exec / completion_audit) at record time. Default "auto" — a
# legacy/unclaimed consult is auto-initiated. Single-owner: this map is
# written ONLY here, read anywhere.
INITIATOR_AUTO = "auto"
INITIATOR_AGENT = "agent"


def initiator_for_task(task_id: str) -> str:
    """Initiator tag for a task's billing records: the claim source stamped
    by claim_pass when this task claimed routing — declared_user->"user",
    declared_agent->"agent", auto/legacy/unclaimed->"auto". Never raises."""
    try:
        with _CLAIM_LOCK:
            src = _TASK_SOURCE.get(str(task_id or ""))
            if not src:
                return INITIATOR_AUTO
            if src == SOURCE_DECLARED_USER:
                return "user"
            if src == SOURCE_DECLARED_AGENT:
                return INITIATOR_AGENT
            return INITIATOR_AUTO
    except Exception:  # noqa: BLE001
        return INITIATOR_AUTO


def _stamp_task_source(task_id: str, source: str) -> None:
    try:
        with _CLAIM_LOCK:
            _TASK_SOURCE[str(task_id or "")] = str(source)
            # Bounded: keep the newest entries (simple size cap — claims are
            # one-per-turn, so growth is slow; the map rides the same
            # process lifetime as the gate's own claim registry).
            while len(_TASK_SOURCE) > 512:
                _TASK_SOURCE.pop(next(iter(_TASK_SOURCE)))
    except Exception:  # noqa: BLE001
        pass


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


def _caps():
    """Late-bound routing_caps import (leg 2: per-agent gate cap + denial
    banner + durable state). Deferred to call time — import-cycle doctrine."""
    from . import routing_caps

    return routing_caps


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
    directive line (optionally carrying a consult payload after the phrase:
    '<phrase>: rest', '<phrase> - rest', '<phrase> — rest', '<phrase>?...'),
    else None. The phrase must START the line; prose mentioning a phrase
    mid-line/mid-sentence (echo/meta) never matches. The payload form
    prefix-matches: a line beginning with a LONGER phrase wins over a
    shorter prefix so 'route this through your shadow: x' never half-matches
    a shorter phrase. Never raises."""
    try:
        best_lane: Optional[str] = None
        best_len = 0
        for norm in _directive_lines(content):
            # 1. Whole-line match (standalone phrase — unchanged).
            lane = DECLARED_USER_PHRASES.get(norm)
            if lane is not None:
                return lane
            # 2. Payload prefix match: line starts with '<phrase><sep>'.
            for phrase, pl in DECLARED_USER_PHRASES.items():
                if len(phrase) <= best_len and best_lane is not None:
                    continue  # longest phrase wins
                for sep in _PHRASE_PAYLOAD_SEPARATORS:
                    if norm.startswith(phrase + sep):
                        best_lane = pl
                        best_len = len(phrase)
                        break
                # 3. Trailing-punctuation form: '<phrase>?' / '<phrase>.' /
                #    '<phrase>,' — the payload begins where the phrase ends.
                if norm.startswith(phrase) and len(norm) > len(phrase) and \
                        norm[len(phrase)] in "?.,":
                    if len(phrase) > best_len or best_lane is None:
                        best_lane = pl
                        best_len = len(phrase)
        return best_lane
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
# Leg 6: task_id -> claim source for initiator provenance (see
# initiator_for_task above). Defined here beside the claim registry.
_TASK_SOURCE: Dict[str, str] = {}


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
        # Leg 7b/7c: a MID-TURN declared claim (agent's request_routing tool)
        # binds the CURRENT turn immediately — stamp the turn-claim record
        # here so every later PRE/POST pass of this turn stands down even
        # if the gate's claim_pass never consumes the declared claim first
        # (and after it does: the record survives the consumption).
        # `executed=False` so the gate's claim phase may execute it ONCE —
        # BUT only when the turn has no record yet (7c): if the turn ALREADY
        # claimed (e.g. an auto consult fired at turn start), the original
        # executed record stands and the mid-turn registration cannot
        # resurrect routing (no second billed consult).
        stamp_turn_claim_if_absent(session_id, lane, source, executed=False)
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
# Turn-claim state (leg 7 — single claim point enforcement)
# ---------------------------------------------------------------------------

# session_id -> claim record for ANY lane/source claim this turn (declared
# claims write here at registration; auto claims at claim time). The legacy
# claim sites (_dispatch_pass complexity PRE, completion_audit POST gate,
# uncensored PRE render gate) read claim_state() first and STAND DOWN when
# a claim exists — blueprint invariant #1: one routing outcome per turn.
_TURN_CLAIMS: Dict[str, Dict[str, Any]] = {}


def claim_state(session_id: str, content: str = "",
                model: str = "") -> Optional[Dict[str, Any]]:
    """Fresh claim for the session (any lane, any source), else None.
    Read-only — legacy claim sites call this before asserting their own
    claim and stand down when it returns a record. Sees BOTH the pending
    declared claim AND the turn-scoped record stamped by claim_pass (which
    survives the one-consult-per-turn consumption). When content is given,
    the turn-scoped record is matched against that turn's key — a same-turn
    re-fire of the same ask matches; a genuinely NEW user turn does not.
    Fail-open: on ANY internal error returns None (legacy behavior — never
    block delivery). Never raises."""
    try:
        rec = peek_declared(session_id)
        if rec is not None:
            return rec
        return _peek_turn_claim(session_id, content, model)
    except Exception:  # noqa: BLE001
        return None


# Turn-window claim record (leg 7): stamped by claim_pass on EVERY route=True
# decision (any source — declared AND auto), NOT consumed by the one-consult-
# per-turn clear. Enforces blueprint invariant #1 across the multi-provider-
# call bursts that make up one turn (a provider-call burst re-fires
# on_llm_request; without this, claim #2 re-runs the legacy auto pass and
# bills a SECOND frontier consult — the live-probe regression). Keyed by
# (session_id, turn_key) where turn_key = state.turn_key_for(ingress text):
# a same-turn re-fire of the same ask hashes the SAME key and stands down;
# a genuinely NEW user turn hashes a different key and routes normally.
# TTL matches pre_fired_this_turn's 120s exclusion window.
_TURN_CLAIM_WINDOW_S = 120.0
_TURN_CLAIMED: Dict[str, Dict[str, Any]] = {}


def _turn_claim_key(session_id: str, content: str = "",
                    model: str = "") -> str:
    """Turn-claim registry key. Leg 7b: keyed by (session_id, TURN ID) —
    the shared session+counter identity from state.advance_turn_identity —
    NOT the ingress-text hash. Mid-turn content rotation (tool results
    appended to the request, tool-loop continuations) must NOT rotate the
    key: a claim registered mid-turn binds every later pass of the same
    turn. Fallback when the counter is unavailable: legacy content key."""
    try:
        from . import state as _state
        return (str(session_id or "") + "|t" +
                str(_state.current_turn_id(session_id)))
    except Exception:  # noqa: BLE001
        try:
            from . import state as _state
            return (str(session_id or "") + "|" +
                    _state.turn_key_for(session_id, content, model))
        except Exception:  # noqa: BLE001
            return str(session_id or "")


def stamp_turn_claim(session_id: str, lane: str, source: str,
                     content: str = "", model: str = "",
                     executed: bool = True) -> None:
    """Record the turn's claim (single-owner registry write). Never raises.
    `executed`: True when the claim's consult already fired at claim time
    (auto lanes, executed declared envelopes); False when a claim was only
    REGISTERED (mid-turn request_routing tool call) and still owes its
    one execution this turn."""
    try:
        with _CLAIM_LOCK:
            _TURN_CLAIMED[_turn_claim_key(session_id, content, model)] = {
                "lane": str(lane or ""), "source": str(source or ""),
                "executed": bool(executed),
                "ts": time.time()}
    except Exception:  # noqa: BLE001
        pass


def mark_turn_claim_executed(session_id: str) -> None:
    """Flag the session's current turn-claim record as EXECUTED (its one
    consult fired). Never raises."""
    try:
        with _CLAIM_LOCK:
            rec = _TURN_CLAIMED.get(_turn_claim_key(session_id))
            if rec is not None:
                rec["executed"] = True
    except Exception:  # noqa: BLE001
        pass


def stamp_turn_claim_if_absent(session_id: str, lane: str, source: str,
                               executed: bool = False) -> None:
    """Stamp the turn-claim record ONLY when the turn has no record yet
    (leg 7c): an already-claimed turn keeps its ORIGINAL record — a
    mid-turn registration must not resurrect routing after the turn's one
    outcome fired. Never raises."""
    try:
        with _CLAIM_LOCK:
            key = _turn_claim_key(session_id)
            if key not in _TURN_CLAIMED:
                _TURN_CLAIMED[key] = {
                    "lane": str(lane or ""), "source": str(source or ""),
                    "executed": bool(executed), "ts": time.time()}
    except Exception:  # noqa: BLE001
        pass


def _peek_turn_claim(session_id: str, content: str = "",
                     model: str = "") -> Optional[Dict[str, Any]]:
    try:
        now = time.time()
        key = _turn_claim_key(session_id, content, model)
        with _CLAIM_LOCK:
            rec = _TURN_CLAIMED.get(key)
            if rec is None:
                return None
            if now - float(rec.get("ts") or 0.0) > _TURN_CLAIM_WINDOW_S:
                _TURN_CLAIMED.pop(key, None)
                return None
            return dict(rec)
    except Exception:  # noqa: BLE001
        return None


def clear_turn_claims(session_id: Optional[str] = None) -> None:
    """Test/recovery hook — drop turn-window claim record(s). Never raises."""
    try:
        with _CLAIM_LOCK:
            if session_id is None:
                _TURN_CLAIMED.clear()
            else:
                prefix = str(session_id or "") + "|"
                for key in [k for k in _TURN_CLAIMED
                            if str(k).startswith(prefix)]:
                    _TURN_CLAIMED.pop(key, None)
    except Exception:  # noqa: BLE001
        pass


def record_turn_claim(session_id: str, lane: str, source: str) -> None:
    """Record a turn-level claim (auto lanes use this; declared claims are
    registered via register_declared and visible through claim_state
    automatically). Best-effort — never raises, never blocks delivery."""
    try:
        register_declared(session_id, lane, source)
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
      claim       True when the caller is the gate's claim phase (cap
                  check applies); the fence phase never caps.
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

        # 3.5 Gate step-1 per-agent daily cap (leg 2, reviewer H1; leg 6
        # re-scope): caps the DECLARED_AGENT on-demand lane only. User
        # phrases are explicit asks (uncapped by the per-agent gate knob);
        # auto lanes are governed by their existing cadence knobs. The
        # frozen anchor_chain.cap_check at the execution seam remains the
        # HARD guard for every lane. Denied routing emits a visible
        # delivery banner + denied_cap ledger event — NEVER silent (Goran
        # amendment). Fail-open: cap-check errors allow. Claim phase only —
        # the fence early-returns above are never capped.
        if bool(ctx.get("claim")):
            _declared_probe = peek_declared(session_id)
            _is_agent_claim = (
                _declared_probe is not None and
                _declared_probe.get("source") == SOURCE_DECLARED_AGENT)
            if not _is_agent_claim:
                # User phrase (explicit ask) / auto / legacy: exempt from
                # the per-agent on-demand cap at the gate.
                pass
            else:
                _caps_mod = _caps()
                _cap_allowed, _cap_spend, _cap_val = \
                    _caps_mod.gate_cap_check(session_id)
                if not _cap_allowed:
                    _caps_mod.deny_routing(session_id, lane="",
                                           initiator=INITIATOR_AGENT,
                                           session_id=session_id)
                    try:
                        _pkg_fn("_log_route")(
                            "PRE", event_detail="denied_cap",
                            spend=round(_cap_spend, 4),
                            cap=round(_cap_val, 2),
                            session_id=session_id)
                    except Exception:  # noqa: BLE001
                        pass
                    return GateDecision(route=False, reason="cap_denied")

        # 3.6 Turn record (leg 7/7b/7c, single claim point): the turn-claim
        #    peek is the FIRST binding check — before declared peek AND
        #    before auto-shape evaluation — for every pass. A fresh turn
        #    claim (any lane, any source) stands down ALL further claims:
        #    decision = no-route, reason=turn_claim_exists, logged as
        #    claim_standdown with claim_lane/claim_source.
        #    ONE exception (execute-once): a claim that was only REGISTERED
        #    (mid-turn request_routing tool call, executed=False) and whose
        #    pending declared claim still stands — the gate's claim phase
        #    executes it this pass; mark_turn_claim_executed flags it and
        #    every LATER pass stands down.
        _turn = _peek_turn_claim(session_id, content,
                                 str(ctx.get("model") or ""))
        if _turn is not None:
            _pending_declared = peek_declared(session_id)
            if (_turn.get("executed") is True) or (_pending_declared is None):
                try:
                    _pkg_fn("_log_route")(
                        "PRE", event_detail="claim_standdown",
                        claim_lane=str(_turn.get("lane") or ""),
                        claim_source=str(_turn.get("source") or ""),
                        session_id=session_id)
                except Exception:  # noqa: BLE001 — observability only
                    pass
                return GateDecision(route=False, reason="turn_claim_exists")

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
        # Leg 7/7c (single claim point): the turn-window claim record
        # outranks the legacy auto pass — a claimed turn cannot claim
        # again. The VERBATIM _dispatch_pass consult is skipped entirely
        # (no double classification, no second consult). The record is
        # turn-scoped (session + turn counter): a genuinely NEW user turn
        # advances the counter and routes normally.
        _turn = _peek_turn_claim(session_id, content, str(model or ""))
        if _turn is not None:
            return NO_ROUTE
        routed = bool(_pkg_fn("_dispatch_pass",
                              ("dispatcher_pre", "_dispatch_pass"))(
            content, session_id, model))
        if routed:
            return GateDecision(route=True, lane=LANE_HIGHER_PRE,
                                source=SOURCE_AUTO, reason="complexity_claim")
        return NO_ROUTE

    decision = decide_turn({"content": content, "request": request,
                            "context": context or {}, "session_id": session_id,
                            "model": str(model or ""),
                            "auto_shape": _auto_shape, "claim": True})
    if decision.route:
        # Leg 7: stamp the turn-scoped claim record on EVERY claim (any
        # lane, any source) — legacy claim sites read claim_state() and
        # stand down for the rest of this turn's provider-call burst.
        stamp_turn_claim(session_id, decision.lane or "", decision.source or "",
                         content, str(model or ""))
    if decision.route and decision.source in (SOURCE_DECLARED_USER,
                                              SOURCE_DECLARED_AGENT):
        # Leg 3: the declared claim's execution envelope rides the EXISTING
        # staged-swap machinery (router_core.stage_model_swap -> the frozen
        # on_llm_execution anchor lane). One consult per turn is enforced by
        # stage_model_swap's own _SWAP_DONE dedupe; the durable cooldown +
        # per-agent spend record at the anchor lane remain post-claim (H7.1).
        # higher-post has NO PRE envelope — its consult is the completion
        # audit's (the declared marker for it is consumed by the gate here,
        # the audit itself stays on its own cadence); shadow stages a plain
        # consult swap (same endpoint, no orientation flag).
        try:
            from . import router_core as _rc

            _task = _rc.task_id_for(session_id, content, str(model or ""))
            # Leg 6: stamp the claim's source for initiator provenance —
            # billing sites (anchor_exec / completion_audit) resolve
            # route_gate.initiator_for_task(task_id) at record time.
            _stamp_task_source(_task, decision.source)
            _rd = _rc.RouteDecision(
                task_id=_task, lane=_rc.LANE_COMPLEXITY,
                mode=_rc.MODE_CONSULT,
                model_target=None, reason="request_routing_declared",
                ts=time.time(), override_used=None,
                route_id=_task[:12] + "-" + str(int(time.time())),
                orientation=(decision.lane == LANE_HIGHER_PRE))
            _staged = _rc.stage_model_swap(session_id, _rd)
            # Leg 7c: the declared claim's consult has now FIRED — flag the
            # turn-claim record executed so every later pass of this turn
            # stands down (the register-time record was executed=False).
            mark_turn_claim_executed(session_id)
            _pkg_fn("_log_route")(
                "PRE", event_detail="request_routing_executed",
                lane=decision.lane, source=decision.source,
                reason=decision.reason, staged=_staged is not None,
                task_id=_task, session_id=session_id)
        except Exception:  # noqa: BLE001 — envelope must never break the gate
            logger.debug("request_routing envelope error", exc_info=True)
        finally:
            # One consult per turn: the declared claim is CONSUMED by this
            # claim — a later re-fire inside the same turn sees no fresh
            # claim (the stage_model_swap _SWAP_DONE dedupe holds the line).
            clear_declared(session_id)
    return decision
