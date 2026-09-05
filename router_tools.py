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
)


# ---------------------------------------------------------------------------
# Counter hooks (called from __init__ route-log sites)
# ---------------------------------------------------------------------------


def count(event_detail: str) -> None:
    """Track anchored/route_skipped/cap_blocked events. Never raises."""
    try:
        with _LOCK:
            if event_detail == "anchor_route_fired":
                _COUNTERS["anchored"] = int(_COUNTERS.get("anchored", 0)) + 1
            elif event_detail == "route_skipped":
                _COUNTERS["skipped"] = int(_COUNTERS.get("skipped", 0)) + 1
            elif event_detail == "cap_blocked":
                _COUNTERS["blocked"] = int(_COUNTERS.get("blocked", 0)) + 1
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


def router_status() -> str:
    """Read-only lane/router status. Never raises; JSON string result."""
    try:
        from . import anchor_chain
        from . import config_writer
        from . import decision_head
        from . import router_core

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
            "anchor_chain": ac_masked,
            "daily_cap_usd": chain.daily_cap_usd,
            "spend_today_usd": round(spend, 4),
            "counts_today_process": counters,
            "last_route_skipped_reason": last_skip,
            "decision_head": decision_head.status(),
            "pending_swap": bool(pend),
            "anchor_backoff_active": router_core.anchor_backoff_active_count(),
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
        from . import anchor_chain

        if not isinstance(model_ref, str) or "://" not in model_ref:
            return None
        scheme, _, model = model_ref.partition("://")
        ep = anchor_chain.parse_anchor_uri(model_ref.strip(), "primary")
        if ep is None or not model.strip():
            return None
        return model_ref.strip()
    except Exception:  # noqa: BLE001
        return None


def _set_nested(section: Dict[str, Any], block: str, key: str, value: Any) -> None:
    sub = section.get(block)
    if not isinstance(sub, dict):
        sub = {}
        section[block] = sub
    sub[key] = value


def router_control(action: str = "", lane: str = "", level: Any = None,
                   role: str = "", model: str = "", cap: Any = None,
                   backend: str = "") -> str:
    """Single validated-action control tool. Returns a JSON string result.
    Never raises; every invalid input returns ok=false with a reason."""
    try:
        from . import anchor_chain
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
                if _lane == "uncensored":
                    section["enabled"] = _want
                else:
                    cx = section.get("complexity")
                    cx = dict(cx) if isinstance(cx, dict) else {}
                    cx["enabled"] = _want
                    section["complexity"] = cx
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
            from . import decision_head

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

        return json.dumps({"ok": False, "error": "unhandled_action"})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": "router_control_failed", "detail": str(exc)[:200]})


def _ping() -> str:
    """Live smoke: ONE call, cheap model (judge tier), max_tokens<=64.
    Fails open with ok=false + reason; never raises. No key -> ok=false."""
    try:
        from . import anchor_chain

        chain = anchor_chain.load_anchor_chain()
        ep = chain.endpoint_for("judge") or chain.endpoint_for("primary")
        if ep is None:
            return json.dumps({"ok": False, "error": "no_anchor_endpoint_configured"})
        from .anchor_exec import anchored_call

        payload = {"messages": [{"role": "user", "content": "Reply with the single word: OK"}],
                   "max_tokens": 16, "temperature": 0.0}
        content, cost = anchored_call(ep, payload, timeout=60)
        if content is None:
            return json.dumps({"ok": False, "error": "anchor_call_failed", "model": ep.model})
        if cost:
            anchor_chain.record_spend(cost)
        return json.dumps({"ok": True, "model": ep.model,
                           "reply": str(content)[:64], "cost_usd": round(cost or 0.0, 6)})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": "ping_failed", "detail": str(exc)[:200]})


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
        "max_tokens<=64), set_decision_head (heuristic|routellm_mf). Guards: "
        "config edits go through the atomic config-writer; caps are UP-only; "
        "route logging and the loop guard are never alterable from here."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "One of: enable_lane, disable_lane, set_level, set_endpoint, set_cap, reload, ping, set_decision_head.",
            },
            "lane": {"type": "string", "description": "For lane actions: uncensored | complexity. set_endpoint uses lane=anchor."},
            "level": {"type": "integer", "description": "For set_level: 0-3 (0 off, 1 manual-only, 2 conservative-auto, 3 aggressive-auto)."},
            "role": {"type": "string", "description": "For set_endpoint: primary | judge."},
            "model": {"type": "string", "description": "For set_endpoint: <scheme>://<model>, e.g. openrouter://openai/o4-mini."},
            "cap": {"type": "number", "description": "For set_cap: new daily cap in USD (raise only)."},
            "backend": {"type": "string", "description": "For set_decision_head: heuristic | routellm_mf."},
        },
        "required": ["action"],
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
        ),
        description=CONTROL_SCHEMA["description"],
        emoji="🎛️",
    )