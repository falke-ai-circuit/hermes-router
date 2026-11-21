"""Router control tools for the hermes-router plugin (v3.0.0).

Two tools registered via ctx.register_tool (spotify plugin pattern):

  router_status  — read-only: lane states, anchor chain (masked), today's
                   anchored/skipped/blocked counts, spend vs cap, last
                   route_skipped reason, decision head status.
  router_control — single validated-action control surface:
      enable_lane / disable_lane (lane=uncensored|complexity)
      set_level  (0-3, per-profile complexity intensity)
      set_endpoint (lane=anchor role=primary|judge model=<scheme>://<model>)
      set_cap    (raise only — config_writer.bump_cap enforces the floor)
      reload     (dirty-flag config re-read — NO gateway bounce)
      ping       (live smoke: ONE call, cheap model, max_tokens<=64)
      set_decision_head (amendment: heuristic|routellm_mf, no other effects)
      set_pre_mode (route|shadow|off — frontier orientation brief on/off)
      set_audit_mode (off|complex|always — frontier higher-self audit on/off)

Security posture (Astra review §5 carried in): status is read-only and
agent-accessible; control actions only touch the plugin's own config section
through the atomic config-writer. Nothing here can set arbitrary base URLs
outside the validated <scheme>://<model> resolution (anchor_chain), alter
route logging, or touch the loop guard. Fail-open: handlers never raise.

Counters + last-reason state live here (thread-safe, in-process) and are fed
by __init__'s route events.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, Optional

from ..lanes.registry import commands_config_key as _commands_config_key

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_COUNTERS: Dict[str, int] = {"anchored": 0, "skipped": 0, "blocked": 0}
_LAST_SKIPPED_REASON: str = ""
_RELOAD_FLAG: Dict[str, float] = {}

VALID_LANES = ("uncensored", "complexity")
VALID_ROLES = ("primary", "judge")
VALID_CONTROL_ACTIONS = (
    "enable_lane", "disable_lane", "set_level", "set_endpoint",
    "set_cap", "reload", "ping", "set_decision_head",
    "set_pre_mode", "set_audit_mode", "request_routing",
)


# ---------------------------------------------------------------------------
# Counter hooks (called from __init__ route-log sites)
# ---------------------------------------------------------------------------


def count(event_detail: str) -> None:
    """Track anchored/route_skipped/cap_blocked/request_routing events.
    Never raises."""
    try:
        with _LOCK:
            if event_detail == "anchor_route_fired":
                _COUNTERS["anchored"] = int(_COUNTERS.get("anchored", 0)) + 1
            elif event_detail == "route_skipped":
                _COUNTERS["skipped"] = int(_COUNTERS.get("skipped", 0)) + 1
            elif event_detail == "cap_blocked":
                _COUNTERS["blocked"] = int(_COUNTERS.get("blocked", 0)) + 1
            elif event_detail == "request_routing":
                _COUNTERS["request_routing"] = \
                    int(_COUNTERS.get("request_routing", 0)) + 1
    except Exception:  # noqa: BLE001
        pass


def note_skip_reason(reason: str) -> None:
    try:
        with _LOCK:
            global _LAST_SKIPPED_REASON
            if reason:
                _LAST_SKIPPED_REASON = str(reason)[:200]
    except Exception:  # noqa: BLE001
        pass


def reload_requested_at() -> float:
    """Dirty-flag: last reload action timestamp (0 = never). Config readers
    don't cache, so this is informational only — NO gateway bounce needed."""
    try:
        return float(_RELOAD_FLAG.get("at", 0.0))
    except Exception:  # noqa: BLE001
        return 0.0


# ---------------------------------------------------------------------------
# router_status
# ---------------------------------------------------------------------------


def _swallow_counts() -> "dict":
    """P6: isolate()/swallow counters by gate (telemetry-visible only)."""
    try:
        from ..core import telemetry as _t

        return _t.swallow_count()
    except Exception:  # noqa: BLE001
        return {}


def _seam_fires() -> "dict":
    """P6 §2.7: seam liveness probe fire counters."""
    try:
        from ..core import telemetry as _t

        return _t.seam_fires()
    except Exception:  # noqa: BLE001
        return {}


def router_status() -> str:
    """Read-only lane/router status. Never raises; JSON string result."""
    try:
        from .. import anchor_chain
        from . import config_writer
        from .. import decision_head
        from .. import router_core

        section = config_writer.read_plugin_section()
        cx_block = section.get("complexity")
        cx = cx_block if isinstance(cx_block, dict) else {}
        level = cx.get("level", 0)
        try:
            level = int(level)
        except (TypeError, ValueError):
            level = 0

        chain = anchor_chain.load_anchor_chain()
        with _LOCK:
            counters = dict(_COUNTERS)
            last_skip = _LAST_SKIPPED_REASON

        spend = anchor_chain.today_spend()
        pend = router_core.peek_pending_swap("")
        ac_masked = {
            "primary": chain.primary.masked() if chain.primary else None,
            "judge": chain.judge.masked() if chain.judge else None,
            "overflow": chain.overflow,
        }
        payload = {
            "lanes": {
                "uncensored": {
                    "enabled": bool(section.get("enabled", True)),
                    "mode": "render-chain (v2 behavior unchanged)",
                },
                "complexity": {
                    "enabled": cx.get("enabled", True) is not False,
                    "level": level,
                    "level_name": {0: "off", 1: "manual_only", 2: "conservative",
                                   3: "aggressive"}.get(max(0, min(3, level)), "off"),
                },
            },
            "debug_banner": int(section.get("debug_banner", 1)) if not isinstance(section.get("debug_banner"), bool) else (1 if section.get("debug_banner") else 0),
            "struggle_feeder": "armed",
            "anchor_chain": ac_masked,
            "daily_cap_usd": chain.daily_cap_usd,
            "spend_today_usd": round(spend, 4),
            "counts_today_process": counters,
            "last_route_skipped_reason": last_skip,
            "decision_head": decision_head.status(),
            "pending_swap": bool(pend),
            "anchor_backoff_active": router_core.anchor_backoff_active_count(),
            "swallow_counts": _swallow_counts(),
            "seam_fires": _seam_fires(),
            "debug_banner": int(section.get("debug_banner", 1)) if not isinstance(section.get("debug_banner"), bool) else (1 if section.get("debug_banner") else 0),
            "struggle_feeder": "armed",
            "reload_dirty_flag_ts": reload_requested_at(),
        }
        return json.dumps(payload, ensure_ascii=False, indent=1)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": "router_status_failed", "detail": str(exc)[:200]})


# ---------------------------------------------------------------------------
# router_control
# ---------------------------------------------------------------------------


def _resolve_endpoint_from_model(model_ref: str) -> Optional[str]:
    """Validate <scheme>://<model> resolves via the anchor chain scheme table.
    Returns the model id on success (the config stores the URI verbatim).
    None when unresolvable — never invent endpoint knowledge in code."""
    try:
        from .. import anchor_chain

        if not isinstance(model_ref, str) or "://" not in model_ref:
            return None
        scheme, _, model = model_ref.partition("://")
        ep = anchor_chain.parse_anchor_uri(model_ref.strip(), "primary")
        if ep is None or not model.strip():
            return None
        return model_ref.strip()
    except Exception:  # noqa: BLE001
        return None


def router_control(action: str = "", lane: str = "", level: Any = None,
                   role: str = "", model: str = "", cap: Any = None,
                   backend: str = "", session_id: str = "") -> str:
    """Single validated-action control tool. Returns a JSON string result.
    Never raises; every invalid input returns ok=false with a reason."""
    try:
        from .. import anchor_chain
        from . import config_writer

        action = (action or "").strip().lower()
        if action not in VALID_CONTROL_ACTIONS:
            return json.dumps({"ok": False, "error": "invalid_action",
                               "valid_actions": list(VALID_CONTROL_ACTIONS)})

        if action == "enable_lane" or action == "disable_lane":
            if lane not in VALID_LANES:
                return json.dumps({"ok": False, "error": "invalid_lane",
                                   "valid": list(VALID_LANES)})
            want_enabled = action == "enable_lane"

            def mut(section: Dict[str, Any], _lane: str = lane, _want: bool = want_enabled):
                # §5.1: the config path is the lane registry's
                # `commands_switch` value, not a lane spelling. A label no lane
                # binds keeps the pre-existing nested fall-through.
                _key = str(_commands_config_key(_lane) or "")
                if _key == "enabled":
                    section["enabled"] = _want
                else:
                    _head, _, _leaf = _key.rpartition(".")
                    _head = _head or "complexity"
                    _leaf = _leaf or "enabled"
                    cx = section.get(_head)
                    cx = dict(cx) if isinstance(cx, dict) else {}
                    cx[_leaf] = _want
                    section[_head] = cx
            ok, detail = config_writer.write_plugin_section(mut)
            return json.dumps({"ok": ok, "action": action, "lane": lane, "detail": detail})

        if action == "set_level":
            try:
                lvl = int(level)
            except (TypeError, ValueError):
                return json.dumps({"ok": False, "error": "level_must_be_int_0_3"})
            if not 0 <= lvl <= 3:
                return json.dumps({"ok": False, "error": "level_out_of_range_0_3"})

            def mut(section: Dict[str, Any], _lvl: int = lvl):
                cx = section.get("complexity")
                cx = dict(cx) if isinstance(cx, dict) else {}
                cx["level"] = _lvl
                section["complexity"] = cx
            ok, detail = config_writer.write_plugin_section(mut)
            return json.dumps({"ok": ok, "action": action, "level": lvl, "detail": detail})

        if action == "set_pre_mode":
            _mode = str(level or "").strip().lower()
            if _mode not in ("route", "shadow", "off"):
                return json.dumps({"ok": False,
                                   "error": "pre_mode must be route|shadow|off"})

            def mut(section: Dict[str, Any], _m: str = _mode):
                cx = section.get("complexity")
                cx = dict(cx) if isinstance(cx, dict) else {}
                cx["pre_mode"] = _m
                section["complexity"] = cx
            ok, detail = config_writer.write_plugin_section(mut)
            return json.dumps({"ok": ok, "action": action, "pre_mode": _mode, "detail": detail})

        if action == "set_audit_mode":
            _mode = str(level or "").strip().lower()
            if _mode not in ("off", "complex", "always"):
                return json.dumps({"ok": False,
                                   "error": "audit_mode must be off|complex|always"})

            def mut(section: Dict[str, Any], _m: str = _mode):
                cx = section.get("complexity")
                cx = dict(cx) if isinstance(cx, dict) else {}
                cx["audit_mode"] = _m
                section["complexity"] = cx
            ok, detail = config_writer.write_plugin_section(mut)
            return json.dumps({"ok": ok, "action": action, "audit_mode": _mode, "detail": detail})

        if action == "set_endpoint":
            if role not in VALID_ROLES:
                return json.dumps({"ok": False, "error": "invalid_role",
                                   "valid": list(VALID_ROLES)})
            resolved = _resolve_endpoint_from_model(model)
            if resolved is None:
                return json.dumps({"ok": False, "error": "unresolvable_model_uri",
                                   "hint": "expected <scheme>://<model> resolvable via anchor_chain schemes or providers.custom"})
            uri = resolved

            def mut(section: Dict[str, Any], _uri: str = uri, _role: str = role):
                ac_s = section.get("anchor_chain")
                ac_s = dict(ac_s) if isinstance(ac_s, dict) else {}
                ac_s[_role] = _uri
                section["anchor_chain"] = ac_s
            ok, detail = config_writer.write_plugin_section(mut)
            return json.dumps({"ok": ok, "action": action, "role": role,
                               "model": uri, "detail": detail})

        if action == "set_cap":
            section = config_writer.read_plugin_section()
            ac_block = section.get("anchor_chain")
            ac = ac_block if isinstance(ac_block, dict) else {}
            try:
                current = float(ac.get("daily_cap_usd", anchor_chain.DEFAULT_DAILY_CAP_USD))
            except (TypeError, ValueError):
                current = anchor_chain.DEFAULT_DAILY_CAP_USD
            ok_bump, detail, eff = config_writer.bump_cap(current, cap, anchor_chain.DEFAULT_DAILY_CAP_USD)
            if not ok_bump:
                return json.dumps({"ok": False, "error": detail,
                                   "current_cap": current,
                                   "floor": anchor_chain.DEFAULT_DAILY_CAP_USD})

            def mut(section: Dict[str, Any], _eff: float = eff):
                a = section.get("anchor_chain")
                a = dict(a) if isinstance(a, dict) else {}
                a["daily_cap_usd"] = _eff
                section["anchor_chain"] = a
            ok, wdetail = config_writer.write_plugin_section(mut)
            return json.dumps({"ok": ok, "action": action, "cap_usd": eff,
                               "detail": wdetail if ok else detail})

        if action == "reload":
            with _LOCK:
                _RELOAD_FLAG["at"] = time.time()
            return json.dumps({"ok": True, "action": "reload",
                               "detail": "dirty-flag set; config re-read per call (no gateway bounce)"})

        if action == "set_decision_head":
            from .. import decision_head

            if not decision_head.set_backend(backend):
                return json.dumps({"ok": False, "error": "invalid_backend",
                                   "valid": ["heuristic", "routellm_mf"]})

            def mut(section: Dict[str, Any], _backend: str = (backend or "").strip().lower()):
                dh = section.get("decision_head")
                dh = dict(dh) if isinstance(dh, dict) else {}
                dh["backend"] = _backend
                section["decision_head"] = dh
            ok, detail = config_writer.write_plugin_section(mut)
            return json.dumps({"ok": ok, "action": action, "backend": backend, "detail": detail})

        if action == "ping":
            return _ping()

        if action == "request_routing":
            return _request_routing(lane=lane, session_id=session_id)

        return json.dumps({"ok": False, "error": "unhandled_action"})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": "router_control_failed", "detail": str(exc)[:200]})


def _request_routing(lane: str = "", session_id: str = "") -> str:
    """request_routing action (request-routing blueprint Phase 1, leg 3):
    the agent DECLARES on-demand routing for its CURRENT turn. Registers a
    declared claim in the gate's single-owner registry; the gate's claim
    phase consumes it THIS turn (same turn — the middleware fires after the
    tool call completes, before the agent's next provider call).

    lane: higher-pre | higher-post | shadow (blueprint lane vocabulary).
    session_id: the CURRENT session (the tool environment supplies it;
    empty -> the claim is registered under "" and the gate matches it via
    the same empty-session id the middleware passes when session_id is
    absent).

    Guards: lane validated against route_gate.VALID_ROUTE_LANES; the
    per-agent daily cap is checked BEFORE the claim registers (a cap-denied
    request returns ok=false with reason=cap_denied and emits the same
    visible denial banner as the gate path); a fresh existing claim makes
    this a no-op ok=true deduped=true (double-declare dedupe H7.5). The
    registry is in-process single-owner state owned by route_gate — no
    second writer is introduced. Never raises."""
    try:
        from .. import route_gate
        from .. import routing_caps

        lane_n = (lane or "").strip().lower()
        if lane_n not in route_gate.VALID_ROUTE_LANES:
            return json.dumps({"ok": False, "error": "invalid_lane",
                               "valid": list(route_gate.VALID_ROUTE_LANES)})
        sid = str(session_id or "")
        # Cap check FIRST — denial is visible even on the declared path.
        # Leg 9: spend keys by AGENT IDENTITY (profile), not session.
        agent_id = routing_caps.agent_identity()
        allowed, spend, cap_val = routing_caps.gate_cap_check(agent_id)
        if not allowed:
            routing_caps.deny_routing(agent_id, lane=lane_n, initiator="agent",
                                      session_id=sid)
            return json.dumps({"ok": False, "error": "cap_denied",
                               "agent_id": agent_id,
                               "lane": lane_n, "spend_usd": round(spend, 4),
                               "cap_usd": round(cap_val, 2),
                               "detail": routing_caps.denied_banner_text(spend, cap_val)})
        fresh = route_gate.register_declared(sid, lane_n,
                                             route_gate.SOURCE_DECLARED_AGENT)
        try:
            count("request_routing")
        except Exception:  # noqa: BLE001
            pass
        return json.dumps({"ok": True, "action": "request_routing",
                           "lane": lane_n, "deduped": not fresh,
                           "session_id": sid,
                           "detail": ("claim already fresh — one consult per "
                                      "turn stands (H7.5)" if not fresh else
                                      "declared claim staged; the gate claims "
                                      "this turn's routing")})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": "request_routing_failed",
                           "detail": str(exc)[:200]})


def _ping() -> str:
    """Live smoke: ONE call, cheap model (judge tier), max_tokens<=64.
    Fails open with ok=false + reason; never raises. No key -> ok=false."""
    try:
        from .. import anchor_chain

        chain = anchor_chain.load_anchor_chain()
        ep = chain.endpoint_for("judge") or chain.endpoint_for("primary")
        if ep is None:
            return json.dumps({"ok": False, "error": "no_anchor_endpoint_configured"})
        from ..anchor_exec import anchored_call

        payload = {"messages": [{"role": "user", "content": "Reply with the single word: OK"}],
                   "max_tokens": 16, "temperature": 0.0}
        content, cost = anchored_call(ep, payload, timeout=60)[:2]
        if content is None:
            return json.dumps({"ok": False, "error": "anchor_call_failed", "model": ep.model})
        if cost:
            anchor_chain.record_spend(cost)
        return json.dumps({"ok": True, "model": ep.model,
                           "reply": str(content)[:64], "cost_usd": round(cost or 0.0, 6)})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": "ping_failed", "detail": str(exc)[:200]})


# ---------------------------------------------------------------------------
# turn_attest — M53-05 W2, the OUT-OF-BAND declaration writer
#
# M53-05 moved the attestation out of the answer text: the journal in
# `features/detection/attestation.py` is the primary channel and the POST seam
# reads it joined on (session_id, turn_id). W3/W4 shipped the reader and left
# the journal with no writer, so an armed turn fell back to the text belt —
# the exact leak the design removes. This tool is the writer.
#
# Two hard properties, both structural rather than tested-into-place:
#   * it writes ONLY through `attestation.record_declaration` (single writer,
#     one seq counter), never into any answer/body field;
#   * it never raises — a failure to record is an UNATTESTED turn, which the
#     POST seam already records and delivers, never a crashed turn.
#
# Identity comes from the tool-call seam, not from module state: `session_id`
# is a host-supplied handler kwarg, and `turn_id` is the per-turn id the host
# binds for the duration of the dispatch (the SAME id `transform_llm_output`
# receives, which is what the POST reader joins on). No "last session seen".
# ---------------------------------------------------------------------------

#: Tool exposure is a CONFIG condition, not a registration-time decision: an
#: unarmed profile must not even see the tool in its definitions, and arming
#: is per-profile `declaration_attestation.armed`. The host TTL-caches
#: check_fn (~30 s per profile scope), so arming lands without a bounce.
def _attestation_tool_available() -> bool:
    """True only on a profile whose `declaration_attestation.armed` is True.

    Reuses the gate's own predicate (`attestation_armed`) instead of reading
    config a second way: two arming readers is how a tool appears on a profile
    whose gate never fires. Fail-closed on any error.
    """
    try:
        from ..passes import dispatcher_pre

        return bool(dispatcher_pre.attestation_armed())
    except Exception:  # noqa: BLE001 — an unreadable config means unarmed
        return False


def _attest_turn_id_from_host_context() -> str:
    """The host's per-turn id bound for this tool call ("" when unavailable).

    `model_tools._approval_observability` binds `turn_id` (from
    ``agent._current_turn_id``) around the handler dispatch; the same value is
    passed to `transform_llm_output`, so this is the join key POST reads —
    not a plugin-side counter that merely looks like one. Read via the host
    module's own contextvar; any other host version returns "" and the caller
    records nothing rather than joining a guessed key.
    """
    try:
        from tools import approval_context as _ac

        return str(_ac._approval_turn_id.get() or "")
    except Exception:  # noqa: BLE001 — host context is optional at this seam
        return ""


def turn_attest(domain: str = "", type_: str = "", session_id: str = "",
                turn_id: str = "") -> str:
    """Record ONE out-of-band attestation for the CURRENT turn.

    argv: `domain` + `type`, validated against the CLOSED enums in
    `features/detection/attestation.py` (DOMAINS / TYPES). Out-of-enum values
    are recorded as UNATTESTED — never passed through as a valid claim, the
    same rule `_ATTEST_RE` applies to the text form. The ack reports the
    adjudication; it never carries answer text.

    `session_id` / `turn_id` are the tool-call identity: the handler kwarg and
    the host's per-turn context. A turn with no host turn id is NOT recorded
    under a session-only key (that cannot be audited as a join) — the call
    returns `turn_id_unavailable` and the turn stays unattested.

    Never raises. Returns a small JSON ack.
    """
    try:
        from ..features.detection import attestation as _att

        if not _attestation_tool_available():
            return json.dumps({"ok": False, "error": "not_armed",
                               "recorded": False})

        sid = str(session_id or "")
        tid = str(turn_id or "") or _attest_turn_id_from_host_context()
        if not tid:
            return json.dumps({"ok": False, "error": "turn_id_unavailable",
                               "recorded": False,
                               "detail": "no host turn identity at the tool-call seam"})
        if not sid:
            return json.dumps({"ok": False, "error": "session_id_unavailable",
                               "recorded": False})

        d = str(domain or "").strip().lower()
        t = str(type_ or "").strip().lower()
        valid = d in _att.DOMAIN_SET and t in _att.TYPE_SET

        # Single writer. `record_declaration` re-validates the pair and stores
        # an out-of-enum claim as UNATTESTED (valid=False), so the journal
        # never holds a value the enum does not own.
        seq = _att.record_declaration(sid, tid, d, t, source="tool_call")
        if not seq:
            return json.dumps({"ok": False, "error": "record_failed",
                               "recorded": False})
        return json.dumps({
            "ok": True,
            "recorded": True,
            "seq": int(seq),
            "valid": bool(valid),
            "domain": d if valid else _att.UNKNOWN,
            "type": t if valid else _att.UNKNOWN,
            "source": "tool_call",
            "joined_on": "session_id,turn_id",
        })
    except Exception as exc:  # noqa: BLE001 — recording never breaks a turn
        return json.dumps({"ok": False, "error": "attest_failed",
                           "recorded": False, "detail": str(exc)[:120]})


# ---------------------------------------------------------------------------
# Registration (ctx.register_tool; spotify plugin pattern)
# ---------------------------------------------------------------------------

STATUS_SCHEMA = {
    "name": "router_status",
    "description": (
        "Read-only hermes-router status: lane states (uncensored/complexity), "
        "anchor chain (masked), today's anchored/skipped/blocked counts, spend "
        "vs daily cap, last route_skipped reason, and decision-head status."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

CONTROL_SCHEMA = {
    "name": "router_control",
    "description": (
        "Single validated-action control surface for hermes-router. Actions: "
        "enable_lane/disable_lane (lane=uncensored|complexity), set_level "
        "(0-3 complexity intensity), set_endpoint (lane=anchor role=primary|judge "
        "model=<scheme>://<model>), set_cap (raise only), reload (dirty-flag "
        "config re-read, NO gateway bounce), ping (ONE cheap live smoke call, "
        "max_tokens<=64), set_decision_head (heuristic|routellm_mf), "
        "set_pre_mode (route|shadow|off — frontier orientation brief on/off), "
        "set_audit_mode (off|complex|always — frontier higher-self audit on/off), "
        "request_routing (lane=higher-pre|higher-post|shadow, session_id — the "
        "agent DECLARES on-demand routing for its CURRENT turn; the unified "
        "route gate claims it before classification). "
        "SELF-PROTECTION RULE (leg 10, blueprint invariant): when the user "
        "asks for shadow / higher-self routing and NO route fired, the ONLY "
        "sanctioned path is the request_routing action — NEVER hand-rolled "
        "model calls. No ad-hoc curl/urllib to model providers, no bare "
        "completions, no switching the model parameter mid-turn: a bare model "
        "call is never a valid outcome (no envelope, no banner, no ledger). "
        "Guards: config edits go through the atomic config-writer; caps are "
        "UP-only; route logging and the loop guard are never alterable from here; "
        "request_routing respects the per-agent daily cap and the one-claim-per-turn dedupe."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "One of: enable_lane, disable_lane, set_level, set_endpoint, set_cap, reload, ping, set_decision_head, set_pre_mode, set_audit_mode, request_routing.",
            },
            "lane": {"type": "string", "description": "For lane actions: uncensored | complexity. set_endpoint uses lane=anchor. request_routing: higher-pre | higher-post | shadow."},
            "level": {"type": ["integer", "string"], "description": "For set_level: 0-3 int. For set_pre_mode: route|shadow|off. For set_audit_mode: off|complex|always."},
            "role": {"type": "string", "description": "For set_endpoint: primary | judge."},
            "model": {"type": "string", "description": "For set_endpoint: <scheme>://<model>, e.g. openrouter://openai/o4-mini."},
            "cap": {"type": "number", "description": "For set_cap: new daily cap in USD (raise only)."},
            "backend": {"type": "string", "description": "For set_decision_head: heuristic | routellm_mf."},
            "session_id": {"type": "string", "description": "For request_routing: the CURRENT session id (supplied by the tool environment)."},
        },
        "required": ["action"],
    },
}


TURN_ATTEST_SCHEMA = {
    "name": "turn_attest",
    "description": (
        "Record this turn's out-of-band attestation (domain + type) for the "
        "hermes-router declaration journal. Call it ONCE per turn, before you "
        "finish, whenever this tool is offered. The values are metadata: they "
        "are joined to the delivered turn by the host turn identity, never "
        "written into your answer. domain: cyber_offensive | violence_gore | "
        "weapons_explosives | bio_chem | sexual_explicit | drugs_synthesis | "
        "fraud_deception | self_harm | none. type: produced | declined | "
        "partial | clarify | blocked."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "The single domain you judge the user's request to be in: cyber_offensive | violence_gore | weapons_explosives | bio_chem | sexual_explicit | drugs_synthesis | fraud_deception | self_harm | none.",
            },
            "type": {
                "type": "string",
                "description": "What you did on this turn: produced | declined | partial | clarify | blocked.",
            },
        },
        "required": ["domain", "type"],
    },
}


def register(ctx) -> None:
    """Register both tools via ctx.register_tool. Raises propagate to the
    plugin register() wrapper which logs them — a tool failure must never
    disable the middleware lanes."""
    ctx.register_tool(
        name="router_status",
        toolset="hermes-router",
        schema=STATUS_SCHEMA,
        handler=lambda args, **kw: router_status(),
        description=STATUS_SCHEMA["description"],
        emoji="🧭",
    )
    ctx.register_tool(
        name="router_control",
        toolset="hermes-router",
        schema=CONTROL_SCHEMA,
        handler=lambda args, **kw: router_control(
            action=args.get("action", ""),
            lane=args.get("lane", ""),
            level=args.get("level"),
            role=args.get("role", ""),
            model=args.get("model", ""),
            cap=args.get("cap"),
            backend=args.get("backend", ""),
            session_id=str(kw.get("session_id") or args.get("session_id", "") or ""),
        ),
        description=CONTROL_SCHEMA["description"],
        emoji="🎛️",
    )
    # M53-05 W2: the out-of-band declaration writer. Registered for EVERY
    # profile but EXPOSED only where `declaration_attestation.armed is True`
    # (check_fn) — an unarmed profile never sees the tool and is never asked
    # to call it. The handler refuses too, so a stale cached definition cannot
    # turn an unarmed profile into a journal writer.
    ctx.register_tool(
        name="turn_attest",
        toolset="hermes-router",
        schema=TURN_ATTEST_SCHEMA,
        handler=lambda args, **kw: turn_attest(
            domain=args.get("domain", ""),
            type_=args.get("type", ""),
            session_id=str(kw.get("session_id") or ""),
            turn_id=str(kw.get("turn_id") or ""),
        ),
        check_fn=_attestation_tool_available,
        description=TURN_ATTEST_SCHEMA["description"],
        emoji="🪪",
    )