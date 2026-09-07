"""`/router` chat command surface for hermes-router (v3.5.0, blueprint 2/3).

One new module + a registration seam in __init__.register(). Rendering layer
over existing state plus a thin validated mutator set reusing the existing
guard machinery — NOT a new tool, not new middleware, not new state (the only
new state in the build is usage_ledger.py, Phase 1).

Dispatch style: LCM's raw-token pattern (no argparse) — tokens = raw_args
split; tokens[0].lower() routed through a table; unknown -> help; bare
invocation = MENU with zero state reads (Goran-direct ruling, flagship #7).

Safety model (blueprint 6 + 6b):
- Every mutation enters config_writer.write_plugin_section (the single
  chokepoint) or reuses router_control mutators directly. No second writer.
- FORBIDDEN_KEYS (log_path/log_routes/log_max_bytes) unreachable: chat cannot
  set them (mutator never touches them) and config_writer reverts/drops them.
- Cap is UP-only via bump_cap; lowering = config-file act (documented in
  /router cap output).
- Endpoints set only via the existing scheme table (no free-form URL arg —
  SSRF guard, flagship #5).
- Consequential mutations require a confirmation token (6b.2): issued on the
  mutating subcommand, executed via `/router confirm <token>`; TTL 120s,
  consumed once, invalidated on restart, never persisted.
- Rate limits: in-process sliding window keyed by subcommand, admit-check at
  entry, released in finally (6b.4 #6).
- Deploy invariant (6b.1, flagship GO-condition 2): mutations answer only
  when the gateway's user-authorization posture is verifiable (allowlist env
  vars present) AND the env gate is on. Otherwise every mutating subcommand
  returns "mutations disabled: gateway authorization not verified". Passive
  self-check at register; no homegrown identity parsing in command text.
- Whole dispatch body wrapped in try/except returning error strings (6b.3
  F1a replacement): the gateway dispatch swallows handler exceptions
  (log-only, core untouched), so the plugin itself must never raise.
- Bounds: bounded-scan reads only; ping runs async (asyncio subprocess,
  timeout=20); all output capped (Telegram limit 4096 chars).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Platform-safety bounds (blueprint 2: output <= 4096 chars, bounded reads)
# ---------------------------------------------------------------------------

MAX_OUTPUT_CHARS = 4000          # safety margin under Telegram's 4096
STATS_MAX_LINES = 80
SESSIONS_MAX_ROWS = 25
LOG_TAIL_MAX_SHOWN = 40
LOG_TAIL_DEFAULT = 25
LOG_TAIL_MAX_N = 100
LOG_GREP_MAX_BYTES = 2 * 1024 * 1024   # last 2MB
LOG_GREP_MAX_LINES = 5000
LOG_GREP_MAX_SHOWN = 25
CONFIG_GET_MAX_LINES = 100
DOCTOR_PING_MAX_TOKENS = 16
DOCTOR_PING_TIMEOUT = 20

TRUNCATION_NOTE = "... output truncated — use a narrower subcommand"


def _cap(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 60]
    nl = cut.rfind("\n")
    if nl > limit // 2:
        cut = cut[:nl]
    return cut + "\n... (output truncated)"


# ---------------------------------------------------------------------------
# Rate limits (6b.4 #6): sliding window per subcommand, released in finally
# ---------------------------------------------------------------------------

_RATE_LOCK = None  # created lazily to avoid import-order issues
_RATE_WINDOWS: Dict[str, List[float]] = {}
_RATE_LIMITS = {
    "default": 30,       # per 60s, per subcommand kind
    "config": 10,
    "cap": 6,
    "confirm": 10,
    "sessions": 6,
    "stats": 10,
    "log": 10,
    "doctor": 3,
    "ping": 3,
}
_RATE_WINDOW_S = 60.0


def _rate_check(kind: str) -> Optional[str]:
    """Return a rejection string when the sliding window for `kind` is full,
    else record the call and return None. Never raises."""
    try:
        limit = _RATE_LIMITS.get(kind, _RATE_LIMITS["default"])
        now = time_mod()
        window = _RATE_WINDOWS.setdefault(kind, [])
        cutoff = now - _RATE_WINDOW_S
        while window and window[0] < cutoff:
            window.pop(0)
        if len(window) >= limit:
            return ("rate limited: /router %s admitted %dx in the last %ds — "
                    "retry in a few seconds" % (kind, limit, int(_RATE_WINDOW_S)))
        window.append(now)
        return None
    except Exception:  # noqa: BLE001
        return None


def time_mod():
    import time as _t

    return _t.time()


# ---------------------------------------------------------------------------
# Confirmation tokens (6b.2): in-process, TTL 120s, consumed once, never
# persisted, invalidated on restart (process death clears the dict)
# ---------------------------------------------------------------------------

_CONFIRM_TTL_S = 120.0
_pending_confirmations: Dict[str, Dict[str, object]] = {}


def _issue_confirmation(subcommand: str, args: List[str], summary: str) -> str:
    import secrets

    token = secrets.token_hex(16)
    _purge_confirmations()
    _pending_confirmations[token] = {
        "subcommand": subcommand,
        "args": list(args),
        "summary": summary[:200],
        "deadline": time_mod() + _CONFIRM_TTL_S,
    }
    return token


def _purge_confirmations() -> None:
    try:
        now = time_mod()
        stale = [k for k, v in _pending_confirmations.items()
                 if float(v.get("deadline", 0.0)) < now]
        for k in stale:
            _pending_confirmations.pop(k, None)
    except Exception:  # noqa: BLE001
        pass


def _peek_confirmation(token: str) -> Optional[str]:
    """Summary of a live pending token, or None (expired/unknown)."""
    try:
        _purge_confirmations()
        rec = _pending_confirmations.get(str(token or ""))
        if not rec:
            return None
        return str(rec.get("summary") or "")
    except Exception:  # noqa: BLE001
        return None


def _consume_confirmation(token: str) -> Optional[Dict[str, object]]:
    """Pop a live token (consumed exactly once). None when expired/unknown."""
    try:
        _purge_confirmations()
        return _pending_confirmations.pop(str(token or ""), None)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Deploy invariant (6b.1): mutation arming = env gate + gateway authz posture
# ---------------------------------------------------------------------------

_AUTHZ_ENV_VARS = (
    "TELEGRAM_ALLOWED_USERS", "DISCORD_ALLOWED_USERS", "SLACK_ALLOWED_USERS",
    "MATTERMOST_ALLOWED_USERS", "MATRIX_ALLOWED_USERS",
    "WHATSAPP_ALLOWED_USERS", "SIGNAL_ALLOWED_USERS", "QQ_ALLOWED_USERS",
    "WEIXIN_ALLOWED_USERS", "BLUEBUBBLES_ALLOWED_USERS",
    "GATEWAY_ALLOW_ALL_USERS",
)


def mutations_armed() -> bool:
    """True when the mutation surface answers. Set by register-time self-check
    (see check_and_arm at module bottom via __init__ registration)."""
    try:
        return bool(_MUTATIONS_ARMED.get("armed"))
    except Exception:  # noqa: BLE001
        return False


_MUTATIONS_ARMED: Dict[str, object] = {"armed": False, "reason": "not registered"}


def verify_gateway_authz() -> bool:
    """Passive posture check: at least one gateway allowlist env var is present
    on THIS process. Read-only; never parses command text or identities."""
    for var in _AUTHZ_ENV_VARS:
        val = os.environ.get(var, "").strip()
        if val:
            return True
    return False


def perform_register_selfcheck() -> Tuple[bool, str]:
    """Flagship GO-condition 2 (blueprint 6b.1): decide the mutation posture
    at register time. Returns (mutations_enabled, startup_log_line).
    Read-only env inspection — no identity parsing, no network calls."""
    try:
        env_gate_on = os.environ.get("HERMES_ROUTER_ENABLE_SLASH_COMMAND", "").strip() in ("1", "true", "yes")
        if not env_gate_on:
            return False, "router slash commands registration disabled (set HERMES_ROUTER_ENABLE_SLASH_COMMAND=1 to enable /router)"
        if verify_gateway_authz():
            _MUTATIONS_ARMED["armed"] = True
            _MUTATIONS_ARMED["reason"] = "authz-verified"
            return True, "router slash commands enabled; mutations armed + gateway authorization verified"
        _MUTATIONS_ARMED["armed"] = False
        _MUTATIONS_ARMED["reason"] = "authz-unverified"
        return False, ("router slash commands enabled; gateway user authorization "
                       "could not be verified — mutations disabled (read-only "
                       "subcommands still answer)")
    except Exception:  # noqa: BLE001
        _MUTATIONS_ARMED["armed"] = False
        _MUTATIONS_ARMED["reason"] = "selfcheck-error"
        return False, "router slash commands: authz self-check failed — mutations disabled"


def _mutation_gate_line() -> str:
    if mutations_armed():
        return ""
    reason = str(_MUTATIONS_ARMED.get("reason") or "")
    if reason == "not registered":
        return ("mutations disabled: /router command surface not registered "
                "(env gate off or register_command unavailable)")
    return ("mutations disabled: gateway authorization not verified "
            "(set TELEGRAM_ALLOWED_USERS or GATEWAY_ALLOW_ALL_USERS=true on this "
            "profile's gateway, then re-arm)")


# ---------------------------------------------------------------------------
# Route-log reader (bounded) — format: "{ts} {EVENT} k=v ..."
# ---------------------------------------------------------------------------

_LOG_LINE_RE = re.compile(r"^(\S+)\s+(\S+)\s*(.*)$")


def _route_log_path() -> str:
    try:
        from . import __init__ as _plugin  # noqa: F401
    except Exception:  # noqa: BLE001
        pass
    # Import the real reader from the package root (import-cycle-safe: read
    # through the package, never duplicate config logic).
    try:
        import hermes_router as _plugin

        return str(_plugin._log_path())
    except Exception:  # noqa: BLE001
        return "/tmp/uncensored-router.log"


def _read_log_lines(max_lines: int, max_bytes: int) -> Tuple[List[str], bool]:
    """Read the route log tail bounded by BOTH line count and byte budget.
    Returns (lines, rotated_available). Never raises."""
    try:
        path = _route_log_path()
        rotated = os.path.exists(path + ".1")
        if not os.path.exists(path):
            return [], rotated
        size = os.path.getsize(path)
        read_bytes = min(size, max_bytes)
        with open(path, "rb") as fh:
            if read_bytes < size:
                fh.seek(size - read_bytes)
            data = fh.read(read_bytes).decode("utf-8", errors="replace")
        lines = data.splitlines()
        if read_bytes < size and lines:
            lines = lines[1:]  # drop the torn first line
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        return lines, rotated
    except Exception:  # noqa: BLE001
        return [], False


def _parse_log_line(line: str) -> Optional[Tuple[str, str, Dict[str, str]]]:
    m = _LOG_LINE_RE.match(line.strip())
    if not m:
        return None
    ts, event, rest = m.group(1), m.group(2), m.group(3)
    fields: Dict[str, str] = {}
    try:
        for tok in rest.split():
            if "=" in tok:
                k, _, v = tok.partition("=")
                fields[k] = v
    except Exception:  # noqa: BLE001
        pass
    return ts, event, fields


# ---------------------------------------------------------------------------
# state.db read-only readers (session_store.py:52 URI pattern)
# ---------------------------------------------------------------------------


def _state_db_path() -> str:
    try:
        from . import session_store

        return session_store._state_db_path()
    except Exception:  # noqa: BLE001
        return ""


def _query_sessions(limit: int, session_id: str = "") -> Optional[List[Dict[str, Any]]]:
    """Read-only sessions query (LIMIT-bounded, 2s timeout). Returns None on
    any failure so callers degrade gracefully."""
    try:
        import sqlite3

        db_path = _state_db_path()
        if not db_path or not os.path.exists(db_path):
            return None
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            if session_id:
                rows = conn.execute(
                    "SELECT id, title, model, message_count, input_tokens, output_tokens,"
                    " estimated_cost_usd, started_at, last_activity_at"
                    " FROM sessions WHERE id = ? LIMIT 1",
                    (session_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, title, model, message_count, input_tokens, output_tokens,"
                    " estimated_cost_usd, started_at, last_activity_at"
                    " FROM sessions ORDER BY COALESCE(last_activity_at, started_at) DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            out.append({
                "id": str(r[0] or ""), "title": str(r[1] or "")[:48],
                "model": str(r[2] or "")[:40], "message_count": int(r[3] or 0),
                "input_tokens": int(r[4] or 0), "output_tokens": int(r[5] or 0),
                "est_cost_usd": float(r[6] or 0.0),
                "started_at": float(r[7] or 0.0),
                "last_activity_at": float(r[8] or 0.0),
            })
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("commands _query_sessions failed: %s", exc)
        return None


def _fmt_ts(ts: float) -> str:
    try:
        if not ts:
            return "?"
        from datetime import datetime, timezone

        return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return "?"


def _fmt_cost(v: float) -> str:
    try:
        return "$%.6f" % float(v or 0.0)
    except Exception:  # noqa: BLE001
        return "$0.000000"


# ---------------------------------------------------------------------------
# Secrets masking for config rendering
# ---------------------------------------------------------------------------

_SECRETISH = re.compile(r"(key|secret|password|api_key)", re.IGNORECASE) # "token" excluded: max_tokens is not a secret


def _mask_value(key: str, value: object) -> str:
    try:
        key_l = str(key).lower()
        if _SECRETISH.search(key_l):
            if key_l == "key_env" or key_l.endswith(".key_env") or key_l.endswith("key_env"):
                return str(value)  # env-var NAMES are shown (blueprint: key_env NAMES shown)
            return "***"
        if key_l.endswith("url") and isinstance(value, str) and "://" in value:
            host = value.split("://", 1)[1].split("/", 1)[0]
            return "<host-only: %s>" % host
        if isinstance(value, str):
            return value if len(value) <= 160 else value[:157] + "..."
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)[:200]
        return str(value)
    except Exception:  # noqa: BLE001
        return "?"


def _render_section_lines(section: Dict[str, Any], prefix: str = "",
                          lines: Optional[List[str]] = None,
                          budget: int = CONFIG_GET_MAX_LINES) -> List[str]:
    if lines is None:
        lines = []
    try:
        for k in sorted(section.keys()):
            if len(lines) >= budget:
                remaining = len(section) + len(prefix)
                lines.append("... more keys (%d)" % remaining)
                break
            v = section[k]
            full = "%s%s" % (prefix, k)
            if isinstance(v, dict):
                _render_section_lines(v, prefix + str(k) + ".", lines, budget)
            elif isinstance(v, list):
                lines.append("%s = [%d entries]" % (full, len(v)))
                for i, entry in enumerate(v[:3]):
                    if isinstance(entry, dict):
                        _render_section_lines(entry, "%s[%d]." % (full, i), lines, budget)
                    else:
                        lines.append("%s[%d] = %s" % (full, i, _mask_value(full, entry)))
                if len(v) > 3:
                    lines.append("... %d more entries" % (len(v) - 3))
            else:
                lines.append("%s = %s" % (full, _mask_value(full, v)))
        return lines
    except Exception:  # noqa: BLE001
        return lines or ["config render failed"]


# ---------------------------------------------------------------------------
# 3 knob whitelist (blueprint 3.2) — name -> (lane, type, range/guard)
# ---------------------------------------------------------------------------

VALID_OVERFLOW = ("pass_through",)


def _knob_whitelist() -> Dict[str, Dict[str, object]]:
    """The 3.2 table as data: {knob: {type, min, max, enum, lane}}.
    Y-gated v3.6 knobs carry gate=<label> and NEVER write config."""
    return {
        "enabled": {"type": "bool", "lane": "U"},
        "dry_run": {"type": "bool", "lane": "U"},
        "render_max_chars": {"type": "int", "min": 0, "max": 200000, "lane": "U"},
        "pending_routes_ttl_seconds": {"type": "int", "min": 60, "max": 3600, "lane": "U"},
        "classification.mode": {"type": "enum", "enum": ("route", "flag_only", "off"), "lane": "C"},
        "classification.aux_classify": {"type": "bool", "lane": "C"},
        "classification.match_threshold": {"type": "int", "min": 0, "max": 1, "lane": "C"},
        "classification.aux_calls_per_hour": {"type": "int", "min": 1, "max": 200, "lane": "C"},
        "classification.aux_breaker_failures": {"type": "int", "min": 1, "max": 10, "lane": "C"},
        "classification.aux_breaker_cooldown_seconds": {"type": "int", "min": 30, "max": 7200, "lane": "C"},
        "complexity.enabled": {"type": "bool", "lane": "C"},
        "complexity.level": {"type": "int", "min": 0, "max": 3, "lane": "C"},
        "complexity.shadow": {"type": "bool", "lane": "C"},
        "complexity.pre_threshold": {"type": "float", "min": 0.0, "max": 1.0, "lane": "C",
                                     "gate": "v3.6 PRE gate not built"},
        "complexity.consult.mid_no_progress_cycles": {"type": "int", "min": 2, "max": 10, "lane": "C",
                                                      "gate": "v3.6 MID gate not built"},
        "complexity.consult.mid_identical_failures": {"type": "int", "min": 2, "max": 5, "lane": "C",
                                                      "gate": "v3.6 MID gate not built"},
        "complexity.consult.mid_ring_size": {"type": "int", "min": 4, "max": 32, "lane": "C",
                                             "gate": "v3.6 MID gate not built"},
        "complexity.bounded_replay.last_n_turns": {"type": "int", "min": 2, "max": 200, "lane": "A"},
        "complexity.bounded_replay.max_input_tokens": {"type": "int", "min": 8000, "max": 400000, "lane": "A"},
        "complexity.bounded_replay.enabled": {"type": "bool", "lane": "A"},
        "complexity.bounded_replay.summary_header": {"type": "bool", "lane": "A"},
        "complexity.anchor_backoff.enabled": {"type": "bool", "lane": "A"},
        "complexity.anchor_backoff.base_s": {"type": "float", "min": 5, "max": 600, "lane": "A"},
        "complexity.anchor_backoff.max_s": {"type": "float", "min": 60, "max": 14400, "lane": "A",
                                            "cross": ("complexity.anchor_backoff.base_s", ">=")},
        "complexity.anchor_backoff.ttl_s": {"type": "float", "min": 300, "max": 86400, "lane": "A"},
        "anchor_chain.primary": {"type": "uri", "lane": "A", "role": "primary"},
        "anchor_chain.judge": {"type": "uri", "lane": "A", "role": "judge"},
        "anchor_chain.daily_cap_usd": {"type": "float", "min": 2.0, "lane": "A", "cap": True},
        "anchor_chain.pricing.input_per_1m": {"type": "float", "min": 0.0, "lane": "A",
                                              "pricing": "input_per_1m"},
        "anchor_chain.pricing.output_per_1m": {"type": "float", "min": 0.0, "lane": "A",
                                               "pricing": "output_per_1m"},
        "anchor_chain.overflow": {"type": "enum", "enum": VALID_OVERFLOW, "lane": "A"},
        "decision_head.backend": {"type": "dh_backend", "lane": "C"},
        "decision_head.threshold": {"type": "float", "min": 0.0, "max": 1.0, "lane": "C"},
        "chain.max_tokens": {"type": "int", "min": 8000, "max": 64000, "lane": "U",
                             "chain_entry": True, "requires": "name="},
        "chain.temperature": {"type": "float", "min": 0.0, "max": 2.0, "lane": "U",
                              "chain_entry": True, "requires": "name="},
        "chain.timeout": {"type": "int", "min": 30, "max": 600, "lane": "U",
                          "chain_entry": True, "requires": "name="},
    }


# 3.2 Chat-N table — rejected with a reason, never written.
_NOT_WRITABLE = {
    "log_path": "FORBIDDEN_KEYS — route logging is the audit trail (code-owned)",
    "log_routes": "FORBIDDEN_KEYS — route logging is the audit trail",
    "log_max_bytes": "FORBIDDEN_KEYS — route logging is the audit trail",
    "render_method_spec": "doctrine-adjacent render instruction — config-file only",
    "substance_frame": "doctrine-adjacent prompt text — config-file only",
    "chain": "adding/removing chain entries means provisioning keys — config-file move",
    "endpoint": "provisioning secrets/urls is an operator config-file move",
    "classification.aux_endpoint": "endpoint+key adjacency = secret surface — config-file only",
    "classification.pre_patterns": "doctrine-adjacent (what counts as a refusal) — config-file only",
    "classification.post_patterns": "doctrine-adjacent (what counts as a refusal) — config-file only",
    "decision_head.weights_path": "points at trained artifacts — operator file move",
    "decision_head.embedding_endpoint": "endpoint+key adjacency = secret surface — config-file only",
    "complexity.consult.suggestions_per_task": "settled v3.6 binding rule (3, one per gate) — not a knob",
    "complexity.consult.consult_deadline_s": "FF-1: deadline is code (timeout=20) — config entry would be a lie",
    "anchor_chain.pricing": "use anchor_chain.pricing.<model>.input_per_1m / .output_per_1m with name=<model>",
}


# ---------------------------------------------------------------------------
# Value parsing + validation
# ---------------------------------------------------------------------------


def _parse_bool(raw: str) -> Optional[bool]:
    v = (raw or "").strip().lower()
    if v in ("true", "1", "on", "yes", "enable"):
        return True
    if v in ("false", "0", "off", "no", "disable"):
        return False
    return None


def _parse_value(spec: Dict[str, object], raw: str) -> Tuple[bool, str, object]:
    """Parse a raw chat string against a knob spec. Returns (ok, reason, value).
    Never raises."""
    t = str(spec.get("type"))
    try:
        if t == "bool":
            b = _parse_bool(raw)
            if b is None:
                return False, "expected true/false", None
            return True, "", b
        if t == "int":
            v = int(float((raw or "").strip()))
            if "min" in spec and v < float(spec["min"]):
                return False, "below minimum %s" % spec["min"], None
            if "max" in spec and v > float(spec["max"]):
                return False, "above maximum %s" % spec["max"], None
            return True, "", v
        if t == "float":
            v = float((raw or "").strip())
            if "min" in spec and v < float(spec["min"]):
                return False, "below minimum %s" % spec["min"], None
            if "max" in spec and "max" in spec and v > float(spec["max"]):
                return False, "above maximum %s" % spec["max"], None
            return True, "", v
        if t == "enum":
            v = (raw or "").strip().lower()
            if v not in spec.get("enum", ()):  # type: ignore[operator]
                return False, "valid: %s" % ", ".join(str(x) for x in spec.get("enum", ())), None
            return True, "", v
        if t == "uri":
            from . import router_tools

            resolved = router_tools._resolve_endpoint_from_model((raw or "").strip())
            if resolved is None:
                return False, ("unresolvable <scheme>://<model> — must resolve via "
                               "anchor_chain schemes (openrouter/nous) or providers.custom"), None
            return True, "", resolved
        if t == "dh_backend":
            v = (raw or "").strip().lower()
            if v not in ("heuristic", "routellm_mf"):
                return False, "valid: heuristic, routellm_mf", None
            return True, "", v
        return False, "unsupported knob type", None
    except (TypeError, ValueError):
        return False, "value must be %s" % t, None
    except Exception as exc:  # noqa: BLE001
        return False, "parse error: %s" % str(exc)[:80], None


# ---------------------------------------------------------------------------
# Mutation executor — ONE chokepoint: config_writer
# ---------------------------------------------------------------------------


def _chain_entry_mut(knob_tail: str, value: object, entry_name: str):
    def mut(section: Dict[str, Any]) -> None:
        chain = section.get("chain")
        chain = [e for e in chain if isinstance(e, dict)] if isinstance(chain, list) else []
        hit = None
        for e in chain:
            if str(e.get("name") or e.get("model") or "") == entry_name:
                hit = e
                break
        if hit is None:
            raise ValueError("no chain entry named %r (existing: %s)" % (
                entry_name, ", ".join(str(e.get("name") or e.get("model") or "?") for e in chain) or "none"))
        if knob_tail not in ("max_tokens", "temperature", "timeout"):
            raise ValueError("only max_tokens/temperature/timeout are chat-settable on chain entries")
        e2 = dict(hit)
        e2[knob_tail] = value
        chain = [e2 if e is hit else e for e in chain]
        section["chain"] = chain
    return mut


def _set_nested_value(section: Dict[str, Any], dotted: str, value: object) -> None:
    parts = dotted.split(".")
    cur = section
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def mutations_consequential(knob: str) -> bool:
    """6b.2: cap set, lane/level/threshold/replay/backoff/pricing/endpoint
    knobs and reload are consequential — they require a confirmation token.
    Fail-safe: unknown knobs count as consequential."""
    try:
        if knob == "anchor_chain.daily_cap_usd":
            return True
        conseq = (
            "complexity.level", "decision_head.threshold",
            "complexity.bounded_replay", "complexity.anchor_backoff",
            "anchor_chain.pricing", "anchor_chain.primary", "anchor_chain.judge",
            "anchor_chain.overflow",
        )
        return any(knob == p or knob.startswith(p + ".") for p in conseq)
    except Exception:  # noqa: BLE001
        return True


def _apply_config_set(knob: str, value: object, extra: Dict[str, str]) -> Tuple[bool, str]:
    """Route one validated mutation into config_writer. Every path —
    including router_control reuse and cap — lands in write_plugin_section.
    Returns (ok, detail). Never raises."""
    try:
        from . import config_writer

        spec = _knob_whitelist().get(knob)
        if spec is None:
            return False, "unknown knob: %s" % knob

        if spec.get("gate"):
            return False, "rejected: %s — gate not built" % spec["gate"]

        # Router_control-backed knobs (reuse the verified mutators directly).
        if knob == "enabled":
            def mut(section: Dict[str, Any], _v=bool(value)) -> None:
                section["enabled"] = _v
        elif knob == "dry_run":
            def mut(section: Dict[str, Any], _v=bool(value)) -> None:
                section["dry_run"] = _v
        elif knob in ("render_max_chars", "pending_routes_ttl_seconds"):
            def mut(section: Dict[str, Any], _k=str(knob), _v=value) -> None:
                section[_k] = _v
        elif knob.startswith("chain."):
            entry_name = str(extra.get("name") or "")
            if not entry_name:
                return False, "chain knobs need name=<entry> (e.g. /router config set chain.max_tokens 16000 name=venice-qwen-xhigh)"
            def mut(section: Dict[str, Any], _v=value, _n=entry_name, _k=str(knob.split(".", 1)[1])) -> None:
                _chain_entry_mut_inner(section, _k, _v, _n)
        elif knob.startswith("complexity.") or knob.startswith("classification.") or knob.startswith("decision_head."):
            if knob == "decision_head.backend":
                def mut(section: Dict[str, Any], _v=str(value)) -> None:
                    dh = section.get("decision_head")
                    dh = dict(dh) if isinstance(dh, dict) else {}
                    dh["backend"] = _v
                    section["decision_head"] = dh
            else:
                def mut(section: Dict[str, Any], _d=str(knob), _v=value) -> None:
                    _set_nested_value(section, _d, _v)
        elif knob == "anchor_chain.primary" or knob == "anchor_chain.judge":
            role = str(spec.get("role") or knob.split(".")[-1])
            def mut(section: Dict[str, Any], _uri=str(value), _r=role) -> None:
                ac = section.get("anchor_chain")
                ac = dict(ac) if isinstance(ac, dict) else {}
                ac[_r] = _uri
                section["anchor_chain"] = ac
        elif knob == "anchor_chain.overflow":
            def mut(section: Dict[str, Any], _v=str(value)) -> None:
                ac = section.get("anchor_chain")
                ac = dict(ac) if isinstance(ac, dict) else {}
                ac["overflow"] = _v
                section["anchor_chain"] = ac
        elif knob == "anchor_chain.daily_cap_usd":
            return _apply_cap_set(value)
        elif knob.startswith("anchor_chain.pricing."):
            field = str(spec.get("pricing") or knob.rsplit(".", 1)[-1])
            model = str(extra.get("name") or "")
            if not model:
                return False, "pricing knobs need name=<model> (e.g. /router config set anchor_chain.pricing.input_per_1m 1.2 name=openai/gpt-5.6-luna-pro)"
            def mut(section: Dict[str, Any], _m=model, _f=field, _v=float(value)) -> None:
                ac = section.get("anchor_chain")
                ac = dict(ac) if isinstance(ac, dict) else {}
                pr = ac.get("pricing")
                pr = dict(pr) if isinstance(pr, dict) else {}
                row = pr.get(_m)
                row = dict(row) if isinstance(row, dict) else {}
                row[_f] = _v
                pr[_m] = row
                ac["pricing"] = pr
                section["anchor_chain"] = ac
        else:
            return False, "knob mapped but no mutator: %s" % knob

        ok, detail = config_writer.write_plugin_section(mut)
        return ok, detail
    except Exception as exc:  # noqa: BLE001
        return False, "error: %s" % str(exc)[:200]


def _chain_entry_mut_inner(section: Dict[str, Any], knob_tail: str, value: object,
                           entry_name: str) -> None:
    chain = section.get("chain")
    chain = [e for e in chain if isinstance(e, dict)] if isinstance(chain, list) else []
    hit = None
    for e in chain:
        if str(e.get("name") or e.get("model") or "") == entry_name:
            hit = e
            break
    if hit is None:
        names = ", ".join(str(e.get("name") or e.get("model") or "?") for e in chain) or "none"
        raise ValueError("no chain entry named %r (existing: %s)" % (entry_name, names))
    if knob_tail not in ("max_tokens", "temperature", "timeout"):
        raise ValueError("only max_tokens/temperature/timeout are chat-settable on chain entries")
    e2 = dict(hit)
    e2[knob_tail] = value
    section["chain"] = [e2 if e is hit else e for e in chain]


def _apply_cap_set(value: object) -> Tuple[bool, str]:
    """Cap set: raise-only via bump_cap, landing in write_plugin_section."""
    try:
        from . import anchor_chain, config_writer

        section = config_writer.read_plugin_section()
        ac_block = section.get("anchor_chain")
        ac = ac_block if isinstance(ac_block, dict) else {}
        try:
            current = float(ac.get("daily_cap_usd", anchor_chain.DEFAULT_DAILY_CAP_USD))
        except (TypeError, ValueError):
            current = anchor_chain.DEFAULT_DAILY_CAP_USD
        ok_bump, detail, eff = config_writer.bump_cap(current, value, anchor_chain.DEFAULT_DAILY_CAP_USD)
        if not ok_bump:
            return False, detail + " (have=%s, floor=%s; lowering the cap is a config-file act)" % (
                current, anchor_chain.DEFAULT_DAILY_CAP_USD)

        def mut(section: Dict[str, Any], _eff=eff) -> None:
            a = section.get("anchor_chain")
            a = dict(a) if isinstance(a, dict) else {}
            a["daily_cap_usd"] = _eff
            section["anchor_chain"] = a
        ok, wdetail = config_writer.write_plugin_section(mut)
        if ok:
            return True, "cap set to $%.2f (raise-only enforced; previous $%.2f)" % (eff, current)
        return False, wdetail
    except Exception as exc:  # noqa: BLE001
        return False, "error: %s" % str(exc)[:200]


# ---------------------------------------------------------------------------
# Config rollback sidecar (6b.4 #3)
# ---------------------------------------------------------------------------

_ROLLBACK_FILENAME = "hermes-router-config-history.jsonl"
_ROLLBACK_KEEP = 10


def _rollback_path() -> str:
    try:
        import hermes_constants

        return str(hermes_constants.get_hermes_home() / _ROLLBACK_FILENAME)
    except Exception:  # noqa: BLE001
        return os.path.join("/tmp", "shadow-" + _ROLLBACK_FILENAME)


def _record_rollback(previous_section: Dict[str, Any], changed_keys: List[str]) -> None:
    """Append the pre-mutation section to the JSONL sidecar (keep last 10).
    Best-effort; never raises (rollback availability must not break writes)."""
    try:
        path = _rollback_path()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        rec = {"ts": round(time_mod(), 3), "changed_keys": changed_keys[:12],
               "previous_section": previous_section}
        lines: List[str] = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        lines.append(json.dumps(rec, ensure_ascii=False) + "\n")
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            fh.writelines(lines[-_ROLLBACK_KEEP:])
        os.replace(path + ".tmp", path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("rollback record failed: %s", exc)


def _config_fingerprint(section: Dict[str, Any]) -> str:
    """Local section key-set hash (pre-R5 stand-in, blueprint D7)."""
    try:
        import hashlib

        payload = json.dumps(section, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return "?"


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

_MENU = [
    ("status", "Inspect", "live router state (lanes, cap, spend, counters, decision head)"),
    ("stats", "Inspect", "intercepted/routed/tokens per lane, spend vs cap [today|7d|session <id>]"),
    ("sessions", "Inspect", "recent sessions with router activity [N, max 25]"),
    ("chain", "Inspect", "both lanes' model chains rendered (masked)"),
    ("log", "Inspect", "route log: tail [N] [--filter kind] | grep <detail>"),
    ("health", "Inspect", "lifecycle verdict + hardening-field interplay"),
    ("config", "Configure", "get [knob] | list | set <knob> <value> | diff | validate | rollback"),
    ("cap", "Configure", "get | set <usd> (raise-only)"),
    ("confirm", "Configure", "confirm <token> — execute a pending consequential mutation"),
    ("ping", "Diagnose", "ONE live anchor smoke call (async, timeout 20s) — bills the cap"),
    ("doctor", "Diagnose", "passive probes: config parse, ledgers, log writability [--ping]"),
    ("reload", "Diagnose", "dirty-flag parity with router_control (configs re-read per call)"),
    ("budget", "Diagnose", "v3.6 budget ledger (not built — stub)"),
    ("help", "Diagnose", "this table"),
]


def _fmt_menu() -> str:
    groups: Dict[str, List[str]] = {}
    for name, group, desc in _MENU:
        groups.setdefault(group, []).append("  /router %-9s %s" % (name, desc))
    out = ["router control surface (per-profile; this gateway only):"]
    for group in ("Inspect", "Configure", "Diagnose"):
        out.append(group + ":")
        out.extend(groups.get(group, []))
    out.append("tip: /router status for live state; bare /router shows this menu with zero state reads")
    return "\n".join(out)


def _render_status() -> str:
    try:
        from . import router_tools

        raw = router_tools.router_status()
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return "status unavailable: %s" % str(exc)[:120]
    if not isinstance(data, dict):
        return "status unavailable (bad payload)"
    lines: List[str] = []
    try:
        lanes = data.get("lanes") or {}
        unc = lanes.get("uncensored") or {}
        cx = lanes.get("complexity") or {}
        lines.append("uncensored lane: enabled=%s" % unc.get("enabled"))
        lines.append("complexity lane: enabled=%s level=%s (%s)" % (
            cx.get("enabled"), cx.get("level"), cx.get("level_name")))
        ac = data.get("anchor_chain") or {}
        primary = (ac.get("primary") or {}).get("model") if isinstance(ac.get("primary"), dict) else None
        judge = (ac.get("judge") or {}).get("model") if isinstance(ac.get("judge"), dict) else None
        lines.append("anchor: primary=%s judge=%s overflow=%s" % (
            primary or "(unset)", judge or "unset", ac.get("overflow")))
        lines.append("cap: spend_today=%s cap=%s" % (
            _fmt_cost(float(data.get("spend_today_usd") or 0.0)),
            _fmt_cost(float(data.get("daily_cap_usd") or 0.0))))
        counts = data.get("counts_today_process") or {}
        lines.append("process counters: anchored=%s skipped=%s blocked=%s" % (
            counts.get("anchored", 0), counts.get("skipped", 0), counts.get("blocked", 0)))
        if data.get("last_route_skipped_reason"):
            lines.append("last route_skipped reason: %s" % data.get("last_route_skipped_reason"))
        dh = data.get("decision_head") or {}
        if isinstance(dh, dict):
            lines.append("decision head: backend=%s threshold=%s" % (
                dh.get("backend"), dh.get("threshold")))
        lines.append("pending swap: %s | anchor backoff active: %s" % (
            data.get("pending_swap"), data.get("anchor_backoff_active")))
        rts = data.get("reload_dirty_flag_ts")
        if rts:
            lines.append("reload dirty flag: %s" % _fmt_ts(float(rts)))
    except Exception as exc:  # noqa: BLE001
        lines.append("render error: %s" % str(exc)[:120])
    gate = _mutation_gate_line()
    if gate:
        lines.append(gate)
    return "\n".join(lines)


def _window_bounds(window: str) -> Tuple[float, str]:
    import time as _t

    now = _t.time()
    if window == "7d":
        return now - 7 * 86400, "last 7 days"
    return now - 86400, "today (last 24h)"


def _fmt_stats(tokens_agg: Dict[str, Any], since_ts: float, session_id: str) -> List[str]:
    lines: List[str] = []
    if not tokens_agg:
        lines.append("tokens ledger: no records in window")
    else:
        for lane in ("render", "anchor", "aux"):
            b = tokens_agg.get(lane)
            if not b:
                continue
            partial = ""
            if b.get("calls_missing_usage"):
                partial = " (partial: provider omits usage)"
            lines.append("tokens[%s]: calls=%d in=%d out=%d est_cost=%s%s" % (
                lane, b["calls"], b["input_tokens"], b["output_tokens"],
                _fmt_cost(float(b["est_cost_usd"])), partial))
    return lines


def _cmd_stats(args: List[str]) -> str:
    try:
        from . import anchor_chain, usage_ledger

        window = "today"
        session_filter = ""
        rest = list(args)
        if rest and rest[0] in ("today", "7d"):
            window = rest.pop(0)
        if rest and rest[0] == "session":
            rest.pop(0)
            if not rest:
                return "usage: /router stats session <session_id>"
            session_filter = rest.pop(0)
        if rest:
            return "unknown stats argument: %s (valid: today|7d|session <id>)" % rest[0]

        since_ts, window_label = _window_bounds(window)
        lines = ["stats (%s%s):" % (window_label, ", session=%s" % session_filter if session_filter else "")]

        # route log event counts (bounded scan)
        lines_log, _rot = _read_log_lines(5000, LOG_GREP_MAX_BYTES)
        counts: Dict[str, int] = {}
        recent: List[Tuple[str, str]] = []
        for ln in lines_log:
            parsed = _parse_log_line(ln)
            if not parsed:
                continue
            ts, event, fields = parsed
            if session_filter and fields.get("session_id") != session_filter:
                continue
            counts[event] = counts.get(event, 0) + 1
            recent.append((ts, ln.strip()))
        if counts:
            top = sorted(counts.items(), key=lambda kv: -kv[1])[:10]
            lines.append("events: " + ", ".join("%s=%d" % (k, v) for k, v in top))
        else:
            lines.append("events: none in window (log: %s)" % _route_log_path())

        # tokens ledger
        recs = usage_ledger.read_records()
        agg = usage_ledger.aggregate(recs, since_ts=since_ts, session_id=session_filter)
        lines.extend(_fmt_stats(agg, since_ts, session_filter))

        # spend vs cap (authoritative cost number)
        try:
            spend = anchor_chain.today_spend()
            cap = anchor_chain.load_anchor_chain().daily_cap_usd
            lines.append("spend today: %s of %s cap" % (_fmt_cost(spend), _fmt_cost(cap)))
        except Exception:  # noqa: BLE001
            pass

        if recent:
            lines.append("recent events:")
            for ts, ln in recent[-5:]:
                lines.append("  " + ln[:150])
        out = "\n".join(lines)
        if len(out.splitlines()) > STATS_MAX_LINES:
            out = "\n".join(out.splitlines()[:STATS_MAX_LINES]) + "\n... (stats capped at %d lines)" % STATS_MAX_LINES
        return out
    except Exception as exc:  # noqa: BLE001
        return "error: stats failed: %s" % str(exc)[:160]


def _cmd_sessions(args: List[str]) -> str:
    try:
        n = 10
        if args:
            try:
                n = int(args[0])
            except (TypeError, ValueError):
                return "usage: /router sessions [N] (default 10, max 25)"
        n = max(1, min(SESSIONS_MAX_ROWS, n))
        rows = _query_sessions(n)
        if rows is None:
            return "sessions unavailable (state.db read failed)"
        if not rows:
            return "sessions: none found"
        lines, _rot = _read_log_lines(2000, 1024 * 512)
        router_sessions = set()
        for ln in lines:
            parsed = _parse_log_line(ln)
            if parsed:
                sid = parsed[2].get("session_id")
                if sid:
                    router_sessions.add(sid)
        out = ["recent sessions (max %d shown):" % n]
        for r in rows:
            fired = "router-active" if r["id"] in router_sessions else ""
            out.append("  %s | %s | msgs=%d | model=%s | tok(in/out)=%d/%d | %s %s" % (
                _fmt_ts(r["last_activity_at"] or r["started_at"]), r["id"][:28],
                r["message_count"], r["model"] or "?", r["input_tokens"], r["output_tokens"],
                ("cost(core)=%s" % _fmt_cost(r["est_cost_usd"])), fired))
        return "\n".join(out)
    except Exception as exc:  # noqa: BLE001
        return "error: sessions failed: %s" % str(exc)[:160]


def _cmd_chain() -> str:
    try:
        from . import config_writer

        section = config_writer.read_plugin_section()
        lines: List[str] = []
        chain = section.get("chain")
        chain = [e for e in chain if isinstance(e, dict)] if isinstance(chain, list) else []
        legacy = section.get("endpoint")
        if isinstance(legacy, dict) and not chain:
            chain = [legacy]
        lines.append("uncensored render chain (%d entries):" % len(chain))
        for i, e in enumerate(chain):
            name = str(e.get("name") or e.get("model") or "entry_%d" % i)
            url = str(e.get("url") or "")
            host = url.split("://", 1)[1].split("/", 1)[0] if "://" in url else "?"
            lines.append("  [%d] %s model=%s host=%s max_tokens=%s temp=%s timeout=%s" % (
                i, name, e.get("model") or "?", host,
                e.get("max_tokens") or "?", e.get("temperature") or "?",
                e.get("timeout") or "?"))
        ac = section.get("anchor_chain")
        ac = ac if isinstance(ac, dict) else {}
        lines.append("anchor chain:")
        for role in ("primary", "judge"):
            uri = ac.get(role)
            if isinstance(uri, str) and "://" in uri:
                scheme, _, model = uri.partition("://")
                lines.append("  %s: %s://%s" % (role, scheme, model))
            else:
                lines.append("  %s: unset" % role)
        lines.append("  overflow: %s | daily_cap_usd: %s" % (
            ac.get("overflow") or "pass_through", ac.get("daily_cap_usd") or "default"))
        pricing = ac.get("pricing")
        if isinstance(pricing, dict) and pricing:
            shown = 0
            for model, p in sorted(pricing.items()):
                if shown >= 5:
                    lines.append("  ... %d more pricing rows" % (len(pricing) - 5))
                    break
                if isinstance(p, dict):
                    lines.append("  pricing[%s]: in=%s out=%s per 1M" % (
                        model, p.get("input_per_1m"), p.get("output_per_1m")))
                shown += 1
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return "error: chain failed: %s" % str(exc)[:160]


def _cmd_log(args: List[str]) -> str:
    try:
        if not args:
            return ("usage: /router log tail [N] [--filter <kind>] | /router log grep <detail>\n"
                    "event kinds: POST/PRE/SEMANTIC + event_detail (route_fired, "
                    "route_skipped, cap_blocked, canonical_committed, ...)")
        sub = args[0].lower()
        rest = args[1:]
        if sub == "tail":
            n = LOG_TAIL_DEFAULT
            filt = ""
            i = 0
            while i < len(rest):
                tok = rest[i]
                if tok == "--filter" and i + 1 < len(rest):
                    filt = rest[i + 1].strip().lower()
                    i += 2
                    continue
                if filt is not None and i == 0 or (not filt and tok.isdigit()):
                    try:
                        n = max(1, min(LOG_TAIL_MAX_N, int(tok)))
                    except (TypeError, ValueError):
                        pass
                i += 1
            lines, rotated = _read_log_lines(max(200, n * 4), LOG_GREP_MAX_BYTES)
            shown: List[str] = []
            for ln in reversed(lines):
                if filt and filt not in ln.lower():
                    continue
                shown.append(ln)
                if len(shown) >= min(n, LOG_TAIL_MAX_SHOWN):
                    break
            out = ["route log tail (last %d%s):" % (len(shown), ", filter=%s" % filt if filt else "")]
            out.extend("  " + s for s in reversed(shown))
            if rotated:
                out.append("(older lines rotated to .1 — not shown)")
            return "\n".join(out) or "(empty)"
        if sub == "grep":
            if not rest:
                return "usage: /router log grep <event_detail>"
            needle = rest[0].strip().lower()
            lines, rotated = _read_log_lines(LOG_GREP_MAX_LINES, LOG_GREP_MAX_BYTES)
            hits = [ln.strip() for ln in lines if needle in ln.lower()]
            out = ["grep %r: %d match(es) in last %d lines" % (needle, len(hits), len(lines))]
            for ln in hits[-LOG_GREP_MAX_SHOWN:]:
                out.append("  " + ln[:180])
            if rotated:
                out.append("(older lines rotated to .1 — not scanned)")
            return "\n".join(out)
        return "unknown log subcommand: %s (tail | grep)" % sub
    except Exception as exc:  # noqa: BLE001
        return "error: log failed: %s" % str(exc)[:160]


def _cmd_health() -> str:
    try:
        from . import router_tools

        raw = router_tools.router_status()
        data = json.loads(raw)
        lines: List[str] = []
        lanes = data.get("lanes") or {}
        unc = bool((lanes.get("uncensored") or {}).get("enabled"))
        cx = bool((lanes.get("complexity") or {}).get("enabled"))
        if unc and cx:
            lines.append("lifecycle: healthy (both lanes enabled)")
        elif unc or cx:
            lines.append("lifecycle: partially enabled (one lane off — fail-open by design)")
        else:
            lines.append("lifecycle: disabled_by_config (both lanes off)")
        spend = float(data.get("spend_today_usd") or 0.0)
        cap = float(data.get("daily_cap_usd") or 0.0)
        if cap and spend >= cap:
            lines.append("cap state: cap_blocked territory (spend >= cap; anchored calls overflow pass-through)")
        counts = data.get("counts_today_process") or {}
        if counts.get("blocked", 0):
            lines.append("cap blocks this process: %d" % counts["blocked"])
        gate = _mutation_gate_line()
        if gate:
            lines.append(gate)
        lines.append("hardening fields not present (pre-v3.6.1 runtime)")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return "error: health failed: %s" % str(exc)[:160]


def _cmd_doctor(args: List[str]) -> str:
    try:
        do_ping = "--ping" in args
        lines: List[str] = []
        from . import anchor_chain, canonical, config_writer, usage_ledger

        section = config_writer.read_plugin_section()
        if section:
            lines.append("config section: parses ok (%d top-level keys)" % len(section))
        else:
            lines.append("config section: EMPTY (defaults live; verify config.yaml)")

        try:
            fp = anchor_chain._ledger_path()
            with open(fp, "r", encoding="utf-8") as fh:
                json.load(fh)
            lines.append("spend ledger: valid JSON (%s)" % os.path.basename(fp))
        except FileNotFoundError:
            lines.append("spend ledger: absent (fresh profile — ok)")
        except Exception as exc:  # noqa: BLE001
            lines.append("spend ledger: CORRUPT (%s)" % str(exc)[:80])

        try:
            cpath = canonical._store_path()
            if os.path.exists(cpath):
                with open(cpath, "rb") as fh:
                    fh.seek(max(0, os.path.getsize(cpath) - 4096))
                    tail = fh.read().decode("utf-8", errors="replace").strip().splitlines()
                last = tail[-1] if tail else ""
                if last:
                    json.loads(last)
                    lines.append("canonical ledger: last line parses")
                else:
                    lines.append("canonical ledger: empty")
            else:
                lines.append("canonical ledger: absent (ok)")
        except Exception as exc:  # noqa: BLE001
            lines.append("canonical ledger: last line CORRUPT (%s)" % str(exc)[:80])

        try:
            tpath = usage_ledger._store_path()
            n_recs = len(usage_ledger.read_records(2000))
            lines.append("tokens ledger: %d record(s) readable%s" % (
                n_recs, "" if os.path.exists(tpath) else " (file absent — first call will create it)"))
        except Exception as exc:  # noqa: BLE001
            lines.append("tokens ledger: unreadable (%s)" % str(exc)[:100])

        try:
            path = _route_log_path()
            probe = path + ".doctor-probe"
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("")
            os.unlink(probe)  # never created; removes nothing
            lines.append("route log: writable (%s)" % path)
        except Exception as exc:  # noqa: BLE001
            lines.append("route log: NOT writable (%s)" % str(exc)[:100])

        if do_ping:
            try:
                from . import anchor_chain as _ac

                chain = _ac.load_anchor_chain()
                ep = chain.endpoint_for("judge") or chain.endpoint_for("primary")
                if ep is None:
                    lines.append("ping: no anchor endpoint configured")
                else:
                    from .anchor_exec import anchored_call

                    payload = {"messages": [{"role": "user", "content": "Reply with the single word: OK"}],
                               "max_tokens": DOCTOR_PING_MAX_TOKENS, "temperature": 0.0}
                    content, cost, _pt, _ct = anchored_call(ep, payload, timeout=DOCTOR_PING_TIMEOUT)
                    if content is None:
                        lines.append("ping: FAILED (%s)" % ep.model)
                    else:
                        if cost:
                            _ac.record_spend(cost)
                        lines.append("ping: ok (%s, cost %s)" % (ep.model, _fmt_cost(float(cost or 0.0))))
            except Exception as exc:  # noqa: BLE001
                lines.append("ping: error (%s)" % str(exc)[:120])
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return "error: doctor failed: %s" % str(exc)[:160]


def _cmd_budget() -> str:
    # v3.6-gated stub (flagship: never write dead config, never fabricate data)
    return "budget ledger not present (v3.6 gates not built)"


def _cmd_help() -> str:
    lines = [_fmt_menu(), "",
             "knobs chat-settable: /router config list — everything else is config-file only",
             "consequential mutations (cap/threshold/reload/level/lane flips) need",
             "a confirmation token: /router confirm <token> (TTL 120s, single-use)"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Config subcommands (get/list/set/diff/validate/rollback)
# ---------------------------------------------------------------------------


def _cmd_config(args: List[str]) -> str:
    try:
        if not args:
            return "usage: /router config get [knob] | list | set <knob> <value> | diff | validate | rollback"
        sub = args[0].lower()
        rest = args[1:]
        if sub == "get":
            return _config_get(rest)
        if sub == "list":
            return _config_list()
        if sub == "set":
            return _config_set(rest)
        if sub == "diff":
            return _config_diff()
        if sub == "validate":
            return _config_validate()
        if sub == "rollback":
            return _config_rollback(rest)
        return "unknown config subcommand: %s" % sub
    except Exception as exc:  # noqa: BLE001
        return "error: config failed: %s" % str(exc)[:160]


def _config_get(rest: List[str]) -> str:
    from . import config_writer

    section = config_writer.read_plugin_section()
    if not rest:
        lines = _render_section_lines(section)
        return "\n".join(["effective plugin section (secrets masked):"] + lines)
    knob = rest[0].strip().lower()
    value: object = section
    for part in knob.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return "knob %r not present in section (try /router config list for chat-settable knobs)" % knob
    if isinstance(value, dict):
        lines = _render_section_lines(value, prefix=knob + ".")
        return "\n".join(lines)
    if isinstance(value, list):
        # Render list values through the masked renderer entry-by-entry
        # (raw json.dumps would leak key_file values / full URLs).
        lines = _render_section_lines({str(i): v for i, v in enumerate(value)},
                                      prefix=knob + ".")
        return "\n".join(lines)
    return "%s = %s" % (knob, _mask_value(knob, value))


def _config_list() -> str:
    lines = ["chat-settable knobs (type, range — everything else is config-file only):"]
    wl = _knob_whitelist()
    for name in sorted(wl):
        spec = wl[name]
        t = str(spec.get("type"))
        rng = ""
        if "min" in spec or "max" in spec:
            rng = " [%s..%s]" % (spec.get("min", "-"), spec.get("max", "+"))
        if "enum" in spec:
            rng = " {%s}" % ", ".join(str(x) for x in spec.get("enum", ()))  # type: ignore[arg-type]
        gate = " (GATED: %s)" % spec["gate"] if spec.get("gate") else ""
        extra = " (needs name=<entry>)" if spec.get("requires") == "name=" else ""
        lines.append("  %-46s %-5s%s%s%s" % (name, t, rng, gate, extra))
    lines.append("never chat-settable: %s" % ", ".join(sorted(_NOT_WRITABLE)))
    lines.append("lowering the cap: config-file act only (bump_cap is UP-only)")
    return "\n".join(lines)


def _config_set(rest: List[str]) -> str:
    if not mutations_armed():
        return _mutation_gate_line() or "mutations disabled"
    if len(rest) < 2:
        return "usage: /router config set <knob> <value>   (see /router config list)"
    knob = rest[0].strip().lower()
    wl = _knob_whitelist()
    spec = wl.get(knob)
    if spec is None:
        if knob in _NOT_WRITABLE:
            return "rejected: %s — %s" % (knob, _NOT_WRITABLE[knob])
        return ("unknown knob: %s — see /router config list; free-text/new keys are "
                "never chat-settable" % knob)
    if knob in _NOT_WRITABLE:
        return "rejected: %s — %s" % (knob, _NOT_WRITABLE[knob])
    if spec.get("gate"):
        return "rejected: %s — gate not built" % spec["gate"]

    extra: Dict[str, str] = {}
    raw_parts = rest[1:]
    # name=<entry> extraction (chain entries + pricing rows)
    value_parts: List[str] = []
    for tok in raw_parts:
        if tok.lower().startswith("name="):
            extra["name"] = tok.split("=", 1)[1].strip()
        else:
            value_parts.append(tok)
    raw_value = " ".join(value_parts).strip()
    if spec.get("requires") == "name=" and not extra.get("name"):
        return "rejected: %s needs name=<entry> (e.g. name=%s)" % (
            knob, "venice-qwen-xhigh" if knob.startswith("chain.") else "openai/gpt-5.6-luna-pro")

    ok, why, parsed = _parse_value(spec, raw_value)
    if not ok:
        return "rejected: %s — %s" % (knob, why)

    from . import config_writer

    before = config_writer.read_plugin_section()
    if spec.get("cross"):
        # cross-field guard: value must satisfy (other_key, relation) where
        # relation is ">=" (value >= other). None other = no constraint yet.
        other_key, relation = spec["cross"]  # type: ignore[misc]
        other = _lookup_knob(before, str(other_key))
        if other is not None:
            try:
                a = float(parsed)  # type: ignore[arg-type]
                b = float(other)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return "rejected: %s — cross-guard comparison failed" % knob
            if (relation == ">=" and a < b) or (relation != ">=" and a != b):
                return ("rejected: %s=%s violates %s %s %s (current %s=%s)" % (
                    knob, a, knob, relation, other_key, other_key, b))

    if not mutations_consequential(knob):
        ok_w, detail = _apply_config_set(knob, parsed, extra)
        if not ok_w:
            return "config set failed: %s" % detail
        after = config_writer.read_plugin_section()
        return _config_set_result_line(knob, before, after, detail)
    # Consequential mutation: two-step confirmation (6b.2).
    summary = "config set %s = %r%s" % (knob, parsed, (" " + extra["name"]) if extra.get("name") else "")
    token = _issue_confirmation("config_set", [knob, raw_value] + (["name=" + extra["name"]] if extra.get("name") else []), summary)
    before_fp = _config_fingerprint(before)
    return ("proposed: %s\nfingerprint(before): %s\n"
            "confirm within 120s: /router confirm %s" % (summary, before_fp, token))


def _lookup_dotted(section: Dict[str, Any], dotted: str) -> object:
    cur: object = section
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _lookup_knob(section: Dict[str, Any], knob: str) -> object:
    return _lookup_dotted(section, knob)


def _config_set_result_line(knob: str, before: Dict[str, Any],
                            after: Dict[str, Any], detail: str) -> str:
    """Result line for a direct (non-consequential) config set: before/after
    masked values + guard result + live-effect note + local fingerprint (D7)."""
    try:
        old_val = _lookup_dotted(before, knob)
        new_val = _lookup_dotted(after, knob)
        return "\n".join([
            "ok: %s %r -> %r" % (knob,
                                 _mask_value(knob, old_val) if old_val is not None else "(unset)",
                                 _mask_value(knob, new_val) if new_val is not None else "(unset)"),
            "guard: config_writer atomic write (%s)" % detail,
            "live-effect: config readers re-read per call (no gateway bounce needed)",
            "fingerprint(after): %s" % _config_fingerprint(after),
        ])
    except Exception as exc:  # noqa: BLE001
        return "config set ok but result render failed: %s" % str(exc)[:120]


def _config_diff() -> str:
    """Diff the live section against the newest rollback-sidecar record."""
    try:
        from . import config_writer

        section = config_writer.read_plugin_section()
        path = _rollback_path()
        if not os.path.exists(path):
            return ("config diff: no previous-section records (nothing mutated "
                    "through /router yet) — the on-disk config IS the baseline")
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        if not lines:
            return "config diff: rollback sidecar empty"
        try:
            last = json.loads(lines[-1])
        except json.JSONDecodeError:
            return "config diff: last sidecar record torn/corrupt"
        prev = last.get("previous_section")
        if not isinstance(prev, dict):
            return "config diff: last record has no previous_section"
        diffs: List[str] = ["config diff (sidecar snapshot %s vs live):" % _fmt_ts(float(last.get("ts", 0.0) or 0.0))]
        keys = sorted(set(list(prev.keys()) + list(section.keys())))
        shown = 0
        for k in keys:
            if shown >= 20:
                diffs.append("  ... more differences omitted")
                break
            pv, cv = prev.get(k), section.get(k)
            if json.dumps(pv, sort_keys=True, default=str) != json.dumps(cv, sort_keys=True, default=str):
                diffs.append("  %s: %r -> %r" % (k, _mask_value(k, pv), _mask_value(k, cv)))
                shown += 1
        if not shown:
            diffs.append("  (no top-level differences)")
        return "\n".join(diffs)
    except Exception as exc:  # noqa: BLE001
        return "error: diff failed: %s" % str(exc)[:160]


def _config_validate() -> str:
    """Passive probe: run every known knob value in the live section through
    its whitelist guard. Never raises; never writes."""
    try:
        from . import config_writer

        section = config_writer.read_plugin_section()
        problems: List[str] = []
        try:
            ac = section.get("anchor_chain")
            if isinstance(ac, dict):
                cap_v = ac.get("daily_cap_usd")
                if cap_v is not None:
                    try:
                        if float(cap_v) < 2.0:
                            problems.append("anchor_chain.daily_cap_usd=%r below floor 2.0" % cap_v)
                    except (TypeError, ValueError):
                        problems.append("anchor_chain.daily_cap_usd not numeric")
                for role in ("primary", "judge"):
                    uri = ac.get(role)
                    if uri is not None and (not isinstance(uri, str) or "://" not in uri):
                        problems.append("anchor_chain.%s not <scheme>://<model>" % role)
        except Exception as exc:  # noqa: BLE001
            problems.append("anchor_chain probe error: %s" % str(exc)[:80])
        wl = _knob_whitelist()
        checked = 0
        for name, spec in wl.items():
            val = _lookup_dotted(section, name)
            if val is None:
                continue
            checked += 1
            ok, why, _parsed = _parse_value(spec, str(val))
            if not ok:
                problems.append("%s=%r: %s" % (name, val, why))
        lines = ["config validate: %d known knobs checked" % checked]
        if problems:
            lines.extend("  PROBLEM: " + p for p in problems[:10])
        else:
            lines.append("  no problems found")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return "error: validate failed: %s" % str(exc)[:160]


def _config_rollback(rest: List[str]) -> str:
    """Restore a previous section snapshot from the JSONL sidecar. EVERY
    rollback enters write_plugin_section (SAME guards — never a direct file
    restore). FORBIDDEN_KEYS are never reverted (code-owned either way)."""
    if not mutations_armed():
        return _mutation_gate_line() or "mutations disabled"
    try:
        from . import config_writer

        path = _rollback_path()
        if not os.path.exists(path):
            return "rollback: no previous-section records (nothing mutated through /router yet)"
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        idx = 1
        if rest and rest[0].isdigit():
            idx = max(1, min(10, int(rest[0])))  # 1 = newest
        if len(lines) < idx:
            return "rollback: only %d record(s) available (asked for #%d)" % (len(lines), idx)
        try:
            rec = json.loads(lines[-idx])
        except json.JSONDecodeError:
            return "rollback: record #%d torn/corrupt" % idx
        prev = rec.get("previous_section")
        if not isinstance(prev, dict) or not prev:
            return "rollback: record #%d has no previous section" % idx

        def mut(section: Dict[str, Any], _prev=dict(prev)) -> None:
            # never revert code-owned keys: preserve the live values verbatim
            for k in config_writer.FORBIDDEN_KEYS:
                if k in section:
                    _prev[k] = section[k]
            section.clear()
            section.update(_prev)

        ok, detail = config_writer.write_plugin_section(mut)
        if not ok:
            return "rollback failed: %s" % detail
        after = config_writer.read_plugin_section()
        return "\n".join([
            "rollback ok (restored snapshot from %s, record #%d of %d)" % (
                _fmt_ts(float(rec.get("ts", 0.0) or 0.0)), idx, len(lines)),
            "guard: went through config_writer (%s); FORBIDDEN_KEYS preserved" % detail,
            "fingerprint(after): %s" % _config_fingerprint(after),
        ])
    except Exception as exc:  # noqa: BLE001
        return "error: rollback failed: %s" % str(exc)[:160]


# ---------------------------------------------------------------------------
# Cap subcommand
# ---------------------------------------------------------------------------


def _cmd_cap(args: List[str]) -> str:
    try:
        from . import anchor_chain

        if not args or args[0].lower() == "get":
            chain = anchor_chain.load_anchor_chain()
            spend = anchor_chain.today_spend()
            remaining = max(0.0, chain.daily_cap_usd - spend)
            lines = ["cap: %s" % _fmt_cost(chain.daily_cap_usd),
                     "spend today: %s" % _fmt_cost(spend),
                     "projected remaining: %s" % _fmt_cost(remaining),
                     "raising: /router cap set <usd> (UP-only; lowering is a config-file act)"]
            gate = _mutation_gate_line()
            if gate:
                lines.append(gate)
            return "\n".join(lines)
        if args[0].lower() == "set":
            if not mutations_armed():
                return _mutation_gate_line() or "mutations disabled"
            if len(args) < 2:
                return "usage: /router cap set <usd>"
            try:
                want = float(args[1])
            except (TypeError, ValueError):
                return "rejected: cap must be a number"
            from . import anchor_chain as _ac

            chain = _ac.load_anchor_chain()
            if want < chain.daily_cap_usd or want < _ac.DEFAULT_DAILY_CAP_USD:
                return ("rejected: cap is UP-only (have=%s, floor=%s) — lowering the "
                        "cap is a config-file act" % (_fmt_cost(chain.daily_cap_usd),
                                                      _fmt_cost(_ac.DEFAULT_DAILY_CAP_USD)))
            summary = "cap set %s -> %s" % (_fmt_cost(chain.daily_cap_usd), _fmt_cost(want))
            token = _issue_confirmation("cap_set", ["set", args[1]], summary)
            return ("proposed: %s\nconfirm within 120s: /router confirm %s" % (summary, token))
        return "unknown cap subcommand: %s (get | set <usd>)" % args[0]
    except Exception as exc:  # noqa: BLE001
        return "error: cap failed: %s" % str(exc)[:160]


# ---------------------------------------------------------------------------
# Reload + confirm + rate-limit wrapper
# ---------------------------------------------------------------------------


def _cmd_reload(_args: List[str]) -> str:
    if not mutations_armed():
        return _mutation_gate_line() or "mutations disabled"
    summary = "reload dirty-flag (observability parity)"
    token = _issue_confirmation("reload", [], summary)
    return ("proposed: set the router reload dirty-flag\n"
            "note: config readers re-read per call — this is informational only\n"
            "confirm within 120s: /router confirm %s" % token)


def _exec_reload() -> str:
    try:
        from . import router_tools

        raw = router_tools.router_control("reload")
        data = json.loads(raw)
        if data.get("ok"):
            return "reload: dirty-flag set (informational; no gateway bounce needed)"
        return "reload failed: %s" % data.get("error", "?")
    except Exception as exc:  # noqa: BLE001
        return "error: reload failed: %s" % str(exc)[:160]


def _cmd_confirm(args: List[str]) -> str:
    if not args:
        n = len(_pending_confirmations)
        return ("usage: /router confirm <token>%s" % (
            " (%d pending)" % n if n else " (nothing pending)"))
    token = args[0].strip()
    pending = _consume_confirmation(token)
    if not pending:
        return ("invalid or expired confirmation token (tokens are single-use, "
                "TTL 120s, invalidated on restart) — re-issue the original command")
    sub = str(pending.get("subcommand"))
    args_l = list(pending.get("args") or [])  # type: ignore[arg-type]
    try:
        if sub == "config_set":
            return _execute_confirmed_config_set(args_l)
        if sub == "cap_set":
            return _execute_confirmed_cap_set(args_l)
        if sub == "reload":
            return _exec_reload()
        return "unknown pending subcommand: %s" % sub
    except Exception as exc:  # noqa: BLE001
        return "error: confirm execution failed: %s" % str(exc)[:160]


def _execute_confirmed_cap_set(args_l: List[str]) -> str:
    try:
        from . import anchor_chain, config_writer

        want = float(args_l[1])
        chain = anchor_chain.load_anchor_chain()
        ok, detail, eff = config_writer.bump_cap(chain.daily_cap_usd, want,
                                                 anchor_chain.DEFAULT_DAILY_CAP_USD)
        if not ok:
            return "cap set failed: %s" % detail

        def mut(section: Dict[str, Any], _eff=eff) -> None:
            a = section.get("anchor_chain")
            a = dict(a) if isinstance(a, dict) else {}
            a["daily_cap_usd"] = _eff
            section["anchor_chain"] = a
        ok_w, wdetail = config_writer.write_plugin_section(mut)
        if ok_w:
            after = config_writer.read_plugin_section()
            return ("cap set: %s (raise-only guard passed)\nfingerprint(after): %s" % (
                detail, _config_fingerprint(after)))
        return "cap set failed: %s" % wdetail
    except Exception as exc:  # noqa: BLE001
        return "error: cap set failed: %s" % str(exc)[:160]


def _execute_confirmed_config_set(args_l: List[str]) -> str:
    try:
        from . import config_writer

        knob = str(args_l[0]).strip().lower()
        extra: Dict[str, str] = {}
        value_parts: List[str] = []
        for tok in args_l[1:]:
            if tok.lower().startswith("name="):
                extra["name"] = tok.split("=", 1)[1].strip()
            else:
                value_parts.append(tok)
        raw_value = " ".join(value_parts).strip()
        spec = _knob_whitelist().get(knob)
        if spec is None or spec.get("gate") or knob in _NOT_WRITABLE:
            return "config set aborted: knob %r no longer valid" % knob
        ok, why, parsed = _parse_value(spec, raw_value)
        if not ok:
            return "config set aborted: %s — %s" % (knob, why)
        before = config_writer.read_plugin_section()
        ok_w, detail = _apply_config_set(knob, parsed, extra)
        if not ok_w:
            return "config set failed: %s" % detail
        after = config_writer.read_plugin_section()
        return _config_set_result_line(knob, before, after, detail)
    except Exception as exc:  # noqa: BLE001
        return "error: confirmed config set failed: %s" % str(exc)[:160]


# ---------------------------------------------------------------------------
# Ping (async — F1c: a sync 20s network call would freeze the gateway loop)
# ---------------------------------------------------------------------------


async def _cmd_ping() -> str:
    try:
        from . import anchor_chain

        chain = anchor_chain.load_anchor_chain()
        ep = chain.endpoint_for("judge") or chain.endpoint_for("primary")
        if ep is None:
            return "ping: no anchor endpoint configured (anchor_chain.primary/judge)"
        payload_json = json.dumps({
            "model": ep.model,
            "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
            "max_tokens": DOCTOR_PING_MAX_TOKENS,
            "temperature": 0.0,
        })
        # Key resolution mirrors _resolve_key: env first, profile dotenv fallback.
        from .anchor_exec import _resolve_key

        api_key = _resolve_key(ep)
        if not api_key:
            return "ping: key unavailable (%s)" % ep.api_key_env
        curl_cmd = [
            "curl", "-sS", "--max-time", str(DOCTOR_PING_TIMEOUT), "-X", "POST",
            ep.base_url.rstrip("/") + "/chat/completions",
            "-H", "Authorization: Bearer " + api_key,
            "-H", "Content-Type: application/json",
            "-d", payload_json,
        ]
        proc = await asyncio.create_subprocess_exec(
            *curl_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=DOCTOR_PING_TIMEOUT + 2)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            return "ping: FAILED (timeout after %ds)" % DOCTOR_PING_TIMEOUT
        body = out.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            return "ping: FAILED (curl exit %d: %s)" % (proc.returncode, err.decode("utf-8", errors="replace")[:120])
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return "ping: FAILED (invalid JSON response)"
        if "error" in data:
            return "ping: FAILED (api_error: %s)" % str(data.get("error"))[:120]
        content = ""
        try:
            choices = data.get("choices") or []
            if choices:
                content = str(((choices[0] or {}).get("message") or {}).get("content") or "")
        except Exception:  # noqa: BLE001
            pass
        if not content.strip():
            return "ping: FAILED (empty response)"
        # Honest spend: reuse the pricing math from the response usage when present.
        from . import usage_ledger

        it, ot = None, None
        try:
            usage = data.get("usage")
            if isinstance(usage, dict):
                pt = usage.get("prompt_tokens")
                ct = usage.get("completion_tokens")
                if isinstance(pt, (int, float)) and isinstance(ct, (int, float)):
                    it, ot = int(pt), int(ct)
        except Exception:  # noqa: BLE001
            pass
        cost = usage_ledger.estimate_cost(ep.model, it, ot) if (it is not None or ot is not None) else None
        if cost is None:
            from .anchor_exec import estimate_tokens_from_payload

            est_in, est_out = estimate_tokens_from_payload(payload_json and json.loads(payload_json))
            cost = anchor_chain.estimate_call_cost(ep, est_in, est_out, chain.pricing)
        if cost and cost > 0:
            anchor_chain.record_spend(cost)
        if it is not None or ot is not None:
            usage_ledger.record_tokens("anchor", ep.model, "", it, ot, cost, "ping")
        return "\n".join([
            "ping: ok (%s)" % ep.model,
            "reply: %s" % content.strip()[:64],
            "cost: %s (recorded to spend ledger — honest: it bills the cap)" % _fmt_cost(float(cost or 0.0)),
        ])
    except Exception as exc:  # noqa: BLE001
        return "error: ping failed: %s" % str(exc)[:160]


# ---------------------------------------------------------------------------
# Dispatch (6b.3: whole body wrapped — handler un-crashable, errors are strings)
# ---------------------------------------------------------------------------


def _rate_admit(kind: str) -> Optional[str]:
    return _rate_check(kind)


def handle_router_command(raw_args: str) -> str:
    """The /router entry point. Returns a string ALWAYS (never raises, never
    returns None) — the gateway renders exactly this back to the platform."""
    try:
        tokens = (raw_args or "").split()
        if not tokens:
            return _fmt_menu()  # bare /router: MENU ONLY, zero state reads
        sub = tokens[0].lower().lstrip("/")
        args = tokens[1:]

        rate = _rate_admit(sub if sub in _RATE_LIMITS else "default")
        if rate:
            return rate

        if sub == "confirm":
            return _cmd_confirm(args)
        if sub in ("help", "?"):
            return _cmd_help()
        if sub == "status":
            return _render_status()
        if sub == "stats":
            return _cmd_stats(args)
        if sub == "sessions":
            return _cmd_sessions(args)
        if sub == "chain":
            return _cmd_chain()
        if sub == "log":
            return _cmd_log(args)
        if sub == "health":
            return _cmd_health()
        if sub == "doctor":
            return _cmd_doctor(args)
        if sub == "budget":
            return _cmd_budget()
        if sub == "cap":
            return _cmd_cap(args)
        if sub == "reload":
            return _cmd_reload(args)
        if sub == "ping":
            return "ping: this subcommand runs async — dispatch wrapper resolves it"
        if sub == "config":
            return _cmd_config(args)
        # Unknown: one-line help with closest match.
        close = min(_MENU, key=lambda m: _lev(m[0], sub)) if _MENU else ("", "", "")
        return "unknown subcommand: %s — closest: /router %s (bare /router = menu)" % (sub, close[0])
    except Exception as exc:  # noqa: BLE001 — 6b.3: whole body wrapped
        logger.debug("router command error", exc_info=True)
        try:
            sub = (raw_args or "").split()[0].lstrip("/") if (raw_args or "").split() else "bare"
        except Exception:  # noqa: BLE001
            sub = "unknown"
        return "error: %s: %s" % (sub, str(exc)[:200])


async def handle_router_command_async(raw_args: str) -> str:
    """Async-aware entry: bare/help/etc run sync; /router ping runs the async
    curl path; everything else resolves the sync result."""
    try:
        tokens = (raw_args or "").split()
        if tokens and tokens[0].lower().lstrip("/") == "ping":
            rate = _rate_admit("ping")
            if rate:
                return rate
            return await _cmd_ping()
        result = handle_router_command(raw_args)
        if asyncio.iscoroutine(result):
            return await result
        return result
    except Exception as exc:  # noqa: BLE001 — 6b.3: always a string, never raise
        logger.debug("router async command error", exc_info=True)
        return "error: router: %s" % str(exc)[:200]


def _lev(a: str, b: str) -> int:
    try:
        if a == b:
            return 0
        n, m = len(a), len(b)
        prev = list(range(n + 1))
        for j in range(1, m + 1):
            cur = [j] + [0] * n
            for i in range(1, n + 1):
                cur[i] = min(prev[i] + 1, cur[i - 1] + 1, prev[i - 1] + (a[i - 1] != b[j - 1]))
            prev = cur
        return prev[n]
    except Exception:  # noqa: BLE001
        return 999



# ---------------------------------------------------------------------------
# Plugin-collision loud self-check + registration helper (called from __init__)
# ---------------------------------------------------------------------------


def register_slash_command(ctx) -> Tuple[bool, str]:
    """LCM 3-branch registration + collision self-check + authz self-check.
    Returns (registered, log_line). Never raises — caller wraps in try/except
    too (belt and suspenders; a chat-command bug can never take down lanes)."""
    try:
        register_command = getattr(ctx, "register_command", None)
        env_gate_on = os.environ.get("HERMES_ROUTER_ENABLE_SLASH_COMMAND", "").strip() in ("1", "true", "yes")
        if not callable(register_command):
            return False, "router slash command registration unavailable on this Hermes host; continuing without /router"
        if not env_gate_on:
            return False, "router slash command registration disabled (set HERMES_ROUTER_ENABLE_SLASH_COMMAND=1 to enable /router)"
        # Plugin-vs-plugin collision is SILENT last-writer-wins upstream —
        # make it LOUD here (blueprint 0: ~3 LOC self-check).
        try:
            existing = getattr(getattr(ctx, "_manager", None), "_plugin_commands", {}) or {}
            if "router" in existing:
                logger.error("hermes-router: /router collision — another plugin already owns the 'router' command; skipping registration")
                return False, "router slash command skipped: '/router' already registered by another plugin (collision)"
        except Exception:  # noqa: BLE001
            pass
        _authz_ok, authz_line = perform_register_selfcheck()
        register_command(
            "router",
            lambda raw_args: _sync_entry(raw_args),
            description="hermes-router status and control (menu: bare /router)",
        )
        logger.info("hermes-router: %s", authz_line)
        return True, authz_line
    except Exception as exc:  # noqa: BLE001
        logger.error("hermes-router: slash command registration failed: %s", exc)
        return False, "router slash command registration failed: %s" % str(exc)[:160]


def _sync_entry(raw_args: str):
    """Handler given to the gateway. Returns str for sync dispatch; for
    /router ping returns the coroutine (run.py awaits coroutine results)."""
    tokens = (raw_args or "").split()
    if tokens and tokens[0].lower().lstrip("/") == "ping":
        return _cmd_ping()  # coroutine — gateway awaits it
    return handle_router_command(raw_args)