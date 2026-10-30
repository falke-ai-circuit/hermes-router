"""Unified lane envelope — ONE request schema, ONE builder, every lane.

Goran directive 2026-10-30 (spec: /opt/data/tmp/unified_envelope_spec.md).

MEASURED problem this replaces: three request shapes grew independently —
decision.build_envelope (15 fields, custom), povs.build_pre_prompt (system
string + messages, no schema), and the frontier lane, which had no request
envelope at all. The same information (agent card, ask, context, stance) was
assembled three times, in three orders, with three truncation policies.

This module is the single owner of:

  * the request schema (lane-request/1) and the assimilation schema
    (lane-assimilation/1);
  * per-lane ``extras`` — typed, closed key sets, lane-scoped;
  * truncation: applied in ``budget.drop_order`` until the envelope fits
    ``budget.chars_cap``, and every drop is RECORDED in ``envelope["trimmed"]``
    so loss is visible instead of silent.

The lane modules keep their public builders as thin adapters (40+ pinned tests
import them by name): they compute lane-domain content, hand it here for
assembly/validation/truncation, then project the result back to the shape their
consumers already read.

DETERMINISTIC: no LLM, no network, no clock read beyond the ``ts`` slot.
NEVER RAISES: every public entry point returns ``{}`` on any failure — upstream
already treats an empty envelope as "no envelope".
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Sequence

SCHEMA_REQUEST = "lane-request/1"
SCHEMA_ASSIMILATION = "lane-assimilation/1"

LANES = ("decision", "frontier", "uncensored")

TRIGGERS = ("pre", "midturn", "post", "manual", "declared_user")

DEFAULT_CHARS_CAP = 8000
DEFAULT_DROP_ORDER = ("slice", "agent_card.history", "context.session_context")

# Closed per-lane extras key sets. A key that is not here is REFUSED, not
# dropped: silently ignoring a mistyped lane field is how a lane loses a
# capability nobody notices for six weeks.
EXTRAS_KEYS: Dict[str, frozenset] = {
    "decision": frozenset({
        "options",            # [{id, label, causal_frame, cost, priors, risk}]
        "causal_context",     # ledger-derived causal frame (R19.18)
        "question",           # typed ask to the lane
        "guidance",           # {max_options, decision_kind}
        "weighting",          # per-option weights (SPEC-impulse-lane-v1 §3)
        "fork_class",         # closed fork vocabulary
        "slice",              # precedent slice, provenance-tagged
        "task",               # human task text the turn belongs to
        "digest",             # counts-only digest of what was sent
        "surrounding_context",
        "frame_context_chars_used",
    }),
    "frontier": frozenset({
        "paths",              # point-of-view angles offered to the frontier
        "evidence_refs",
        "consult_kind",       # consultation|verification
        "route_id",
        "kind",               # legacy provenance label (1:1 with old key)
    }),
    "uncensored": frozenset({
        "leg",                # pre|post
        "work_text",          # POST only: work to reason over
        "voice_mode",         # voice_stems|none
        "close_class",        # closed-line class, never inferred downstream
    }),
}

OPTION_KEYS = frozenset({"id", "label", "causal_frame", "cost", "priors", "risk"})

# Drop-order names that address a lane-specific slot rather than a core slot.
_LANE_SLOTS = ("slice",)


# ---------------------------------------------------------------------------
# config-driven caps (core.config_access is the one reader — never raises)
# ---------------------------------------------------------------------------

def _config_get(path: str, default: Any) -> Any:
    try:
        from ...core import config_access

        value = config_access.get(path, default)
        return default if value is None else value
    except Exception:  # noqa: BLE001 — config read must never break a build
        return default


def chars_cap(cfg: Optional[Dict[str, Any]] = None,
              budget: Optional[Dict[str, Any]] = None) -> int:
    """Effective envelope cap. Explicit budget > cfg block > config.yaml."""
    try:
        for src, key in ((budget, "chars_cap"), (cfg, "chars_cap")):
            if isinstance(src, dict):
                val = src.get(key)
                if isinstance(val, int) and not isinstance(val, bool) and val > 0:
                    return int(val)
        if isinstance(cfg, dict) and isinstance(cfg.get("envelope"), dict):
            val = cfg["envelope"].get("chars_cap")
            if isinstance(val, int) and not isinstance(val, bool) and val > 0:
                return int(val)
        val = _config_get("envelope.chars_cap", DEFAULT_CHARS_CAP)
        if isinstance(val, int) and not isinstance(val, bool) and val > 0:
            return int(val)
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_CHARS_CAP


def drop_order(cfg: Optional[Dict[str, Any]] = None,
               budget: Optional[Dict[str, Any]] = None) -> List[str]:
    """Effective drop order. Explicit budget > cfg block > config.yaml."""
    try:
        for src in (budget, cfg.get("envelope") if isinstance(cfg, dict) else None):
            if isinstance(src, dict):
                order = src.get("drop_order")
                if isinstance(order, (list, tuple)):
                    return [str(p) for p in order]
        order = _config_get("envelope.drop_order", list(DEFAULT_DROP_ORDER))
        if isinstance(order, (list, tuple)) and order:
            return [str(p) for p in order]
    except Exception:  # noqa: BLE001
        pass
    return list(DEFAULT_DROP_ORDER)


# ---------------------------------------------------------------------------
# validation — closed sets, explicit reasons
# ---------------------------------------------------------------------------

def validate_extras(lane: Any, extras: Any) -> List[str]:
    """Errors for a lane's extras block. Empty list == valid.

    Refuses (a) an unknown lane, (b) an extras key that is not this lane,
    (c) an unknown key inside the lane's extras, (d) an unknown option key in
    the decision lane. Never raises.
    """
    errors: List[str] = []
    try:
        if lane not in LANES:
            return ["unknown lane: %r" % (lane,)]
        if extras is None:
            return errors
        if not isinstance(extras, dict):
            return ["extras must be a dict"]
        for key, value in extras.items():
            if key not in LANES:
                errors.append("unknown extras key: %r" % (key,))
                continue
            if key != lane:
                errors.append("extras lane %r does not match lane %r" % (key, lane))
                continue
            if value is None:
                continue
            if not isinstance(value, dict):
                errors.append("extras[%r] must be a dict" % (key,))
                continue
            allowed = EXTRAS_KEYS[lane]
            for inner in value:
                if inner not in allowed:
                    errors.append("unknown extras.%s key: %r" % (lane, inner))
            if lane == "decision":
                errors.extend(_option_errors(value.get("options")))
    except Exception:  # noqa: BLE001
        return ["extras validation failed"]
    return errors


def _option_errors(options: Any) -> List[str]:
    errors: List[str] = []
    if options is None:
        return errors
    if not isinstance(options, (list, tuple)):
        return ["extras.decision.options must be a list"]
    for i, opt in enumerate(options):
        if not isinstance(opt, dict):
            errors.append("option %d is not a dict" % i)
            continue
        for key in opt:
            if key not in OPTION_KEYS:
                errors.append("unknown option key %r at index %d" % (key, i))
        if not str(opt.get("id") or ""):
            errors.append("option %d has no id" % i)
    return errors


def validate_request(envelope: Any) -> List[str]:
    """Errors for an assembled request envelope (shape + extras). Never raises."""
    errors: List[str] = []
    try:
        if not isinstance(envelope, dict) or not envelope:
            return ["empty envelope"]
        if envelope.get("schema") != SCHEMA_REQUEST:
            errors.append("schema must be %r" % SCHEMA_REQUEST)
        lane = envelope.get("lane")
        if lane not in LANES:
            errors.append("unknown lane: %r" % (lane,))
            return errors
        for slot in ("agent_card", "ask", "context", "stance", "budget"):
            if not isinstance(envelope.get(slot), dict):
                errors.append("missing core slot: %s" % slot)
        ask_slot = envelope.get("ask")
        ask = ask_slot if isinstance(ask_slot, dict) else {}
        if not isinstance(ask.get("text"), str):
            errors.append("ask.text must be a string")
        if ask.get("kind") not in ("user", "system", "agent"):
            errors.append("ask.kind must be user|system|agent")
        budget_slot = envelope.get("budget")
        budget_slot = budget_slot if isinstance(budget_slot, dict) else {}
        if not isinstance(budget_slot.get("chars_cap"), int):
            errors.append("budget.chars_cap must be an int")
        if not isinstance(budget_slot.get("drop_order"), list):
            errors.append("budget.drop_order must be a list")
        errors.extend(validate_extras(lane, envelope.get("extras")))
    except Exception:  # noqa: BLE001
        return ["request validation failed"]
    return errors


# ---------------------------------------------------------------------------
# slot normalisation — every slot present, no key invented
# ---------------------------------------------------------------------------

def _card(agent_card: Any) -> Dict[str, str]:
    src = agent_card if isinstance(agent_card, dict) else {}
    out: Dict[str, str] = {}
    for key in ("persona", "procedures", "history"):
        out[key] = str(src.get(key) or "")
    return out


def _ask(ask: Any) -> Dict[str, str]:
    if isinstance(ask, str):
        src: Dict[str, Any] = {"text": ask}
    else:
        src = ask if isinstance(ask, dict) else {}
    kind = src.get("kind")
    return {"text": str(src.get("text") or ""),
            "kind": kind if kind in ("user", "system", "agent") else "user"}


def _context(context: Any) -> Dict[str, str]:
    src = context if isinstance(context, dict) else {}
    out: Dict[str, str] = {}
    for key in ("final_objective", "rolling_task", "session_context"):
        out[key] = str(src.get(key) or "")
    return out


def _stance(stance: Any) -> Dict[str, Any]:
    src = stance if isinstance(stance, dict) else {}
    scope = src.get("scope")
    mindset = src.get("mindset", "")
    return {"mindset": "" if mindset is None else mindset,
            "scope": dict(scope) if isinstance(scope, dict) else {}}


# ---------------------------------------------------------------------------
# truncation — centralised, ordered, and VISIBLE
# ---------------------------------------------------------------------------

def _size(obj: Any) -> int:
    try:
        return len(json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False))
    except Exception:  # noqa: BLE001 — unserialisable body is "infinite"
        return 1 << 60


def _resolve(env: Dict[str, Any], path: str) -> str:
    """Concrete envelope path for a drop-order name.

    Lane-specific slots ('slice') resolve inside this envelope's extras lane;
    anything else is a plain dotted core path.
    """
    name = str(path or "").strip()
    if not name:
        return ""
    if name in _LANE_SLOTS:
        lane = str(env.get("lane") or "")
        return "extras.%s.%s" % (lane, name)
    return name


def _drop(env: Dict[str, Any], path: str) -> bool:
    """Empty the slot at `path`. True when it actually held something."""
    name = str(path or "").strip()
    if not name:
        return False
    parts = name.split(".")
    node: Any = env
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    leaf = parts[-1]
    if not isinstance(node, dict) or leaf not in node:
        return False
    value = node[leaf]
    if isinstance(value, str):
        if not value:
            return False
        node[leaf] = ""
    elif isinstance(value, (list, tuple)):
        if not value:
            return False
        node[leaf] = []
    elif isinstance(value, dict):
        if not value:
            return False
        node[leaf] = {}
    else:
        if value in (None, 0, False, ""):
            return False
        node[leaf] = None
    return True


def _leaves(obj: Any, prefix: str = "") -> List[str]:
    """Dotted paths of every string leaf (skips the bookkeeping keys)."""
    out: List[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if not prefix and key in ("schema", "lane", "trimmed"):
                continue
            out.extend(_leaves(value, "%s.%s" % (prefix, key) if prefix else str(key)))
    elif isinstance(obj, (list, tuple)):
        for i, value in enumerate(obj):
            out.extend(_leaves(value, "%s[%d]" % (prefix, i)))
    elif isinstance(obj, str):
        if obj:
            out.append(prefix)
    return out


def _get_path(obj: Any, path: str) -> Any:
    if "[" in path:
        return None
    node: Any = obj
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _set_path(obj: Any, path: str, value: Any) -> bool:
    if "[" in path:
        return False
    parts = path.split(".")
    node: Any = obj
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    if not isinstance(node, dict) or parts[-1] not in node:
        return False
    node[parts[-1]] = value
    return True


CLIP_MARK = "...[truncated]"


def _clip_longest(env: Dict[str, Any], cap: int) -> List[str]:
    """Last resort: halve the longest string leaf until the cap holds.

    Drop order is the policy; clipping is the backstop that keeps the promise
    "under cap" true when every droppable slot is already empty. Recorded the
    same way (visible loss, never silent).
    """
    dropped: List[str] = []
    try:
        for _ in range(64):
            if _size(env) <= cap:
                break
            longest, best = "", 0
            for path in _leaves(env):
                value = _get_path(env, path)
                if isinstance(value, str) and len(value) > best:
                    longest, best = path, len(value)
            if not longest or best <= len(CLIP_MARK) + 1:
                break
            keep = max(0, (best // 2) - len(CLIP_MARK))
            if not _set_path(env, longest, str(_get_path(env, longest))[:keep] + CLIP_MARK):
                break
            if longest not in dropped:
                dropped.append(longest)
    except Exception:  # noqa: BLE001 — truncation must never break a build
        pass
    return dropped


def truncate(env: Dict[str, Any], cap: Optional[int] = None,
             order: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Apply drop_order until under cap; record every drop in env['trimmed']."""
    try:
        limit = cap if isinstance(cap, int) and cap > 0 else chars_cap()
        path_order = [str(p) for p in (order or drop_order())]
        env["trimmed"] = []
        if _size(env) <= limit:
            return env
        for name in path_order:
            if _size(env) <= limit:
                break
            resolved = _resolve(env, name)
            if resolved and _drop(env, resolved):
                env["trimmed"].append(resolved)
        if _size(env) > limit:
            env["trimmed"].extend(
                p for p in _clip_longest(env, limit) if p not in env["trimmed"])
        return env
    except Exception:  # noqa: BLE001
        return env


# ---------------------------------------------------------------------------
# the one request builder
# ---------------------------------------------------------------------------

def build_request(lane: Any, trigger: str = "pre", session_id: str = "",
                  ts: Optional[float] = None,
                  agent_card: Optional[Dict[str, Any]] = None,
                  ask: Any = None,
                  context: Optional[Dict[str, Any]] = None,
                  stance: Optional[Dict[str, Any]] = None,
                  extras: Optional[Dict[str, Any]] = None,
                  budget: Optional[Dict[str, Any]] = None,
                  cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Assemble one lane-request/1 envelope. {} on ANY refusal or failure.

    An unknown lane or an unknown extras key is a REFUSAL, not a best effort:
    upstream treats {} as "no envelope", which is the same fail-open the three
    legacy builders already had.
    """
    try:
        errors = validate_extras(lane, extras)
        if errors:
            return {}
        lane_extras = {}
        if isinstance(extras, dict):
            value = extras.get(lane)
            lane_extras = dict(value) if isinstance(value, dict) else {}
        limit = chars_cap(cfg, budget)
        order = drop_order(cfg, budget)
        envelope: Dict[str, Any] = {
            "schema": SCHEMA_REQUEST,
            "lane": lane,
            "trigger": trigger if trigger in TRIGGERS else str(trigger or "pre"),
            "session_id": str(session_id or ""),
            "ts": float(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool)
                  else time.time(),
            "agent_card": _card(agent_card),
            "ask": _ask(ask),
            "context": _context(context),
            "stance": _stance(stance),
            "extras": {lane: lane_extras},
            "budget": {"chars_cap": limit, "drop_order": list(order)},
        }
        return truncate(envelope, limit, order)
    except Exception:  # noqa: BLE001 — never raise
        return {}


# ---------------------------------------------------------------------------
# the one assimilation builder (output direction)
# ---------------------------------------------------------------------------

def build_assimilation(lane: Any, producer: str = "", route_id: str = "",
                       task_id: str = "", answer: str = "",
                       verdict: Optional[Dict[str, Any]] = None,
                       revised: bool = False,
                       evidence_refs: Optional[Sequence[str]] = None,
                       limitations: str = "", ts: Optional[float] = None,
                       kind: str = "",
                       extras: Optional[Dict[str, Any]] = None,
                       budget: Optional[Dict[str, Any]] = None,
                       cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Assemble one lane-assimilation/1 envelope. {} on refusal or failure.

    `kind` carries the legacy provenance label the frontier lane already
    publishes, so consumers that read env["kind"] keep working unchanged.
    """
    try:
        errors = validate_extras(lane, extras)
        if errors:
            return {}
        lane_extras: Dict[str, Any] = {}
        if isinstance(extras, dict):
            value = extras.get(lane)
            lane_extras = dict(value) if isinstance(value, dict) else {}
        envelope: Dict[str, Any] = {
            "schema": SCHEMA_ASSIMILATION,
            "lane": lane,
            "producer": str(producer or ""),
            "route_id": str(route_id or ""),
            "task_id": str(task_id or ""),
            "answer": str(answer if answer is not None else ""),
            "verdict": dict(verdict) if isinstance(verdict, dict) else {},
            "revised": bool(revised),
            "evidence_refs": [str(r) for r in (evidence_refs or [])],
            "limitations": str(limitations or ""),
            "ts": float(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool)
                  else time.time(),
            "extras": {lane: lane_extras},
        }
        if kind:
            envelope["kind"] = str(kind)
        limit = chars_cap(cfg, budget)
        order = drop_order(cfg, budget)
        envelope["budget"] = {"chars_cap": limit, "drop_order": list(order)}
        return truncate(envelope, limit, order)
    except Exception:  # noqa: BLE001 — never raise
        return {}
