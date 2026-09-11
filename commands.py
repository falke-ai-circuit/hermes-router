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
# R5 leg 5 decomposition: runtime/stateful command group moved to
# commands_runtime.py. Thin re-exports keep the public surface; the mutable
# state (_MUTATIONS_ARMED, _RATE_WINDOWS, _pending_confirmations) stays
# OWNED here and is read/written through the seam.
# ---------------------------------------------------------------------------

from . import commands_runtime as _commands_runtime  # noqa: E402
from . import commands_config as _commands_config  # noqa: E402


def _cmd_lane(args: List[str]) -> str:
    return _commands_runtime._cmd_lane(args)


def _cmd_reload(_args: List[str]) -> str:
    return _commands_runtime._cmd_reload(_args)


def _exec_reload() -> str:
    return _commands_runtime._exec_reload()


def _cmd_frontier(args: List[str]) -> str:
    return _commands_runtime._cmd_frontier(args)


async def _cmd_ping() -> str:
    return _commands_runtime._cmd_ping()


def _rate_check(kind: str) -> Optional[str]:
    return _commands_runtime._rate_check(kind)


def _rate_admit(kind: str) -> Optional[str]:
    return _commands_runtime._rate_admit(kind)


def time_mod():
    return _commands_runtime.time_mod()


def verify_gateway_authz() -> bool:
    return _commands_runtime.verify_gateway_authz()


def perform_register_selfcheck() -> Tuple[bool, str]:
    return _commands_runtime.perform_register_selfcheck()


def _cmd_confirm(args: List[str]) -> str:
    return _commands_runtime._cmd_confirm(args)


def _consume_confirmation(token: str) -> Optional[Dict[str, object]]:
    return _commands_runtime._consume_confirmation(token)


def _execute_confirmed_cap_set(args_l: List[str]) -> str:
    return _commands_runtime._execute_confirmed_cap_set(args_l)


def _execute_confirmed_config_set(args_l: List[str]) -> str:
    return _commands_runtime._execute_confirmed_config_set(args_l)


# ---------------------------------------------------------------------------
# R5 leg 4 decomposition: /router diagnostic command group moved to
# commands_diag.py. Thin re-exports keep the public surface; mutable
# _MUTATIONS_ARMED stays owned by this module, read through the seam.
# ---------------------------------------------------------------------------

from . import commands_diag as _commands_diag  # noqa: E402


def _cmd_budget() -> str:
    return _commands_diag._cmd_budget()


def _cmd_chain() -> str:
    return _commands_diag._cmd_chain()


def _cmd_doctor(args: List[str]) -> str:
    return _commands_diag._cmd_doctor(args)


def _cmd_health() -> str:
    return _commands_diag._cmd_health()


def _cmd_log(args: List[str]) -> str:
    return _commands_diag._cmd_log(args)


def _cmd_sessions(args: List[str]) -> str:
    return _commands_diag._cmd_sessions(args)


def _cmd_stats(args: List[str]) -> str:
    return _commands_diag._cmd_stats(args)


def _fmt_cost(v: float) -> str:
    return _commands_diag._fmt_cost(v)


def _fmt_menu() -> str:
    return _commands_diag._fmt_menu()


def _fmt_stats(tokens_agg: Dict[str, Any], since_ts: float, session_id: str) -> List[str]:
    return _commands_diag._fmt_stats(tokens_agg, since_ts, session_id)


def _fmt_ts(ts: float) -> str:
    return _commands_diag._fmt_ts(ts)


def _mutation_gate_line() -> str:
    return _commands_diag._mutation_gate_line()


def _parse_log_line(line: str) -> Optional[Tuple[str, str, Dict[str, str]]]:
    return _commands_diag._parse_log_line(line)


def _query_sessions(limit: int, session_id: str = "") -> Optional[List[Dict[str, Any]]]:
    return _commands_diag._query_sessions(limit, session_id)


def _read_log_lines(max_lines: int, max_bytes: int) -> Tuple[List[str], bool]:
    return _commands_diag._read_log_lines(max_lines, max_bytes)


def _render_status() -> str:
    return _commands_diag._render_status()


def _route_log_path() -> str:
    return _commands_diag._route_log_path()


def _state_db_path() -> str:
    return _commands_diag._state_db_path()


def _window_bounds(window: str) -> Tuple[float, str]:
    return _commands_diag._window_bounds(window)


def mutations_armed() -> bool:
    return _commands_diag.mutations_armed()


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


# ---------------------------------------------------------------------------
# Confirmation tokens (6b.2): in-process, TTL 120s, consumed once, never
# persisted, invalidated on restart (process death clears the dict)
# ---------------------------------------------------------------------------

_CONFIRM_TTL_S = 120.0
_pending_confirmations: Dict[str, Dict[str, object]] = {}


def _issue_confirmation(subcommand: str, args: List[str], summary: str) -> str:
    return _commands_config._issue_confirmation(subcommand, args, summary)


def _purge_confirmations() -> None:
    return _commands_config._purge_confirmations()


def _peek_confirmation(token: str) -> Optional[str]:
    return _commands_config._peek_confirmation(token)


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


_MUTATIONS_ARMED: Dict[str, object] = {"armed": False, "reason": "not registered"}


# ---------------------------------------------------------------------------
# Route-log reader (bounded) — format: "{ts} {EVENT} k=v ..."
# ---------------------------------------------------------------------------

_LOG_LINE_RE = re.compile(r"^(\S+)\s+(\S+)\s*(.*)$")


# ---------------------------------------------------------------------------
# state.db read-only readers (session_store.py:52 URI pattern)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Secrets masking for config rendering
# ---------------------------------------------------------------------------

_SECRETISH = re.compile(r"(key|secret|password|api_key)", re.IGNORECASE) # "token" excluded: max_tokens is not a secret


def _mask_value(key: str, value: object) -> str:
    return _commands_config._mask_value(key, value)


def _render_section_lines(section: Dict[str, Any], prefix: str = "", lines: Optional[List[str]] = None, budget: int = CONFIG_GET_MAX_LINES) -> List[str]:
    return _commands_config._render_section_lines(section, prefix, lines, budget)


# ---------------------------------------------------------------------------
# 3 knob whitelist (blueprint 3.2) — name -> (lane, type, range/guard)
# ---------------------------------------------------------------------------

VALID_OVERFLOW = ("pass_through",)


def _knob_whitelist() -> Dict[str, Dict[str, object]]:
    return _commands_config._knob_whitelist()


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
    return _commands_config._parse_bool(raw)


def _parse_value_debug_banner_fallback(spec: Dict[str, object], raw: str) -> Tuple[bool, str, object]:
    return _commands_config._parse_value_debug_banner_fallback(spec, raw)


def _parse_value(spec: Dict[str, object], raw: str) -> Tuple[bool, str, object]:
    return _commands_config._parse_value(spec, raw)


# ---------------------------------------------------------------------------
# Mutation executor — ONE chokepoint: config_writer
# ---------------------------------------------------------------------------


def _chain_entry_mut(knob_tail: str, value: object, entry_name: str):
    return _commands_config._chain_entry_mut(knob_tail, value, entry_name)


def _set_nested_value(section: Dict[str, Any], dotted: str, value: object) -> None:
    return _commands_config._set_nested_value(section, dotted, value)


def mutations_consequential(knob: str) -> bool:
    return _commands_config.mutations_consequential(knob)


def _apply_config_set(knob: str, value: object, extra: Dict[str, str]) -> Tuple[bool, str]:
    return _commands_config._apply_config_set(knob, value, extra)


def _chain_entry_mut_inner(section: Dict[str, Any], knob_tail: str, value: object, entry_name: str) -> None:
    return _commands_config._chain_entry_mut_inner(section, knob_tail, value, entry_name)


def _apply_cap_set(value: object) -> Tuple[bool, str]:
    return _commands_config._apply_cap_set(value)


# ---------------------------------------------------------------------------
# Config rollback sidecar (6b.4 #3)
# ---------------------------------------------------------------------------

_ROLLBACK_FILENAME = "hermes-router-config-history.jsonl"
_ROLLBACK_KEEP = 10


def _rollback_path() -> str:
    return _commands_config._rollback_path()


def _record_rollback(previous_section: Dict[str, Any], changed_keys: List[str]) -> None:
    return _commands_config._record_rollback(previous_section, changed_keys)


def _config_fingerprint(section: Dict[str, Any]) -> str:
    return _commands_config._config_fingerprint(section)


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
    ("lane", "Configure", "uncensored|frontier on|off — flip either routing lane (token-guarded); bare = current state"),
    ("frontier", "Configure", "frontier pre on|off / post on|off|always / state (orientation + higher-self audit)"),
    ("cap", "Configure", "get | set <usd> (raise-only)"),
    ("confirm", "Configure", "confirm <token> — execute a pending consequential mutation"),
    ("ping", "Diagnose", "ONE live anchor smoke call (async, timeout 20s) — bills the cap"),
    ("doctor", "Diagnose", "passive probes: config parse, ledgers, log writability [--ping]"),
    ("reload", "Diagnose", "dirty-flag parity with router_control (configs re-read per call)"),
    ("budget", "Diagnose", "v3.6 budget ledger (not built — stub)"),
    ("help", "Diagnose", "this table"),
]


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
    return _commands_config._cmd_config(args)


def _config_get(rest: List[str]) -> str:
    return _commands_config._config_get(rest)


def _config_list() -> str:
    return _commands_config._config_list()


def _config_set(rest: List[str]) -> str:
    return _commands_config._config_set(rest)


def _lookup_dotted(section: Dict[str, Any], dotted: str) -> object:
    return _commands_config._lookup_dotted(section, dotted)


def _lookup_knob(section: Dict[str, Any], knob: str) -> object:
    return _commands_config._lookup_knob(section, knob)


def _config_set_result_line(knob: str, before: Dict[str, Any], after: Dict[str, Any], detail: str) -> str:
    return _commands_config._config_set_result_line(knob, before, after, detail)


def _config_diff() -> str:
    return _commands_config._config_diff()


def _config_validate() -> str:
    return _commands_config._config_validate()


def _config_rollback(rest: List[str]) -> str:
    return _commands_config._config_rollback(rest)


# ---------------------------------------------------------------------------
# Cap subcommand
# ---------------------------------------------------------------------------


def _cmd_cap(args: List[str]) -> str:
    return _commands_config._cmd_cap(args)


# ---------------------------------------------------------------------------
# /router lane — dedicated on/off switches for BOTH routing lanes
# (Goran-direct 09-07: "both uncensored and frontier routing should be
# configurable via /router commands and activable on and off")
# ---------------------------------------------------------------------------

_LANE_MAP = {
    "uncensored": "enabled",          # master switch of the render lane
    "frontier": "complexity.enabled",  # frontier/anchor consult lane
}


# ---------------------------------------------------------------------------
# Reload + confirm + rate-limit wrapper
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Ping (async — F1c: a sync 20s network call would freeze the gateway loop)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Dispatch (6b.3: whole body wrapped — handler un-crashable, errors are strings)
# ---------------------------------------------------------------------------


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
        if sub == "lane":
            return _cmd_lane(args)
        if sub == "frontier":
            return _cmd_frontier(args)
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
        env_gate_raw = os.environ.get("HERMES_ROUTER_ENABLE_SLASH_COMMAND", "")
        # v3.7.1 zero-config default: /router ships ON; explicit 0/false/no disables.
        env_gate_on = not (env_gate_raw.strip().lower() in ("0", "false", "no"))
        if not callable(register_command):
            return False, "router slash command registration unavailable on this Hermes host; continuing without /router"
        if not env_gate_on:
            return False, "router slash command registration disabled (HERMES_ROUTER_ENABLE_SLASH_COMMAND=0)"
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