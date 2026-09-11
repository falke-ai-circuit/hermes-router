"""`/router` runtime/stateful command group for hermes-router (decomposed
from commands.py, R5 leg 5 — pure refactor, zero behavior change).

Contents: lane toggles (_cmd_lane), frontier toggles (_cmd_frontier), reload
(_cmd_reload/_exec_reload), async ping (_cmd_ping), the confirmation-token
machinery (issue/purge/consume + confirmed cap/config executors), rate-limit
admission (_rate_check/_rate_admit), gateway authz + register self-check,
and time_mod.

MUTABLE STATE OWNERSHIP (exactly one module each — unchanged behavior):
  commands.py      owns _MUTATIONS_ARMED, _RATE_WINDOWS, _pending_confirmations,
                   _RATE_LIMITS, and every shared constant — read/written here
                   through the late-bound `_commands()` seam so tests that
                   patch attributes on the commands module keep landing.
  commands_config  owns the config/knob mutation internals (reached via the
                   same seam through commands.py's forwarding shims).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)  # module-local; behavior reads go through _commands()


def _commands():
    """commands-module late-binding seam (shared constants + mutable state)."""
    import sys as _sys
    return _sys.modules[__package__ + '.commands']


def _rate_check(kind: str) -> Optional[str]:
    """Return a rejection string when the sliding window for `kind` is full,
    else record the call and return None. Never raises."""
    try:
        limit = _commands()._RATE_LIMITS.get(kind, _commands()._RATE_LIMITS["default"])
        now = time_mod()
        window = _commands()._RATE_WINDOWS.setdefault(kind, [])
        cutoff = now - _commands()._RATE_WINDOW_S
        while window and window[0] < cutoff:
            window.pop(0)
        if len(window) >= limit:
            return ("rate limited: /router %s admitted %dx in the last %ds — "
                    "retry in a few seconds" % (kind, limit, int(_commands()._RATE_WINDOW_S)))
        window.append(now)
        return None
    except Exception:  # noqa: BLE001
        return None


def time_mod():
    import time as _t

    return _t.time()


def _consume_confirmation(token: str) -> Optional[Dict[str, object]]:
    """Pop a live token (consumed exactly once). None when expired/unknown."""
    try:
        _commands()._purge_confirmations()
        return _commands()._pending_confirmations.pop(str(token or ""), None)
    except Exception:  # noqa: BLE001
        return None


def verify_gateway_authz() -> bool:
    """Passive posture check: at least one gateway allowlist env var is present
    on THIS process. Read-only; never parses command text or identities."""
    for var in _commands()._AUTHZ_ENV_VARS:
        val = os.environ.get(var, "").strip()
        if val:
            return True
    return False


def perform_register_selfcheck() -> Tuple[bool, str]:
    """Flagship GO-condition 2 (blueprint 6b.1): decide the mutation posture
    at register time. Returns (mutations_enabled, startup_log_line).
    Read-only env inspection — no identity parsing, no network calls."""
    try:
        env_gate_raw = os.environ.get("HERMES_ROUTER_ENABLE_SLASH_COMMAND", "")
        # v3.7.1 zero-config default (Goran 2026-09-10): /router ships ON. Only
        # an explicit "0/false/no" disables it — absent/unset means enabled.
        env_gate_on = not (env_gate_raw.strip().lower() in ("0", "false", "no"))
        if not env_gate_on:
            return False, "router slash commands registration disabled (HERMES_ROUTER_ENABLE_SLASH_COMMAND=0)"
        if verify_gateway_authz():
            _commands()._MUTATIONS_ARMED["armed"] = True
            _commands()._MUTATIONS_ARMED["reason"] = "authz-verified"
            return True, "router slash commands enabled; mutations armed + gateway authorization verified"
        _commands()._MUTATIONS_ARMED["armed"] = False
        _commands()._MUTATIONS_ARMED["reason"] = "authz-unverified"
        return False, ("router slash commands enabled; gateway user authorization "
                       "could not be verified — mutations disabled (read-only "
                       "subcommands still answer)")
    except Exception:  # noqa: BLE001
        _commands()._MUTATIONS_ARMED["armed"] = False
        _commands()._MUTATIONS_ARMED["reason"] = "selfcheck-error"
        return False, "router slash commands: authz self-check failed — mutations disabled"


def _cmd_lane(args: List[str]) -> str:
    try:
        if not args or args[0].lower() == "get":
            from . import config_writer as _cw
            cfg = _cw.read_plugin_section() or {}
            unc = bool(cfg.get("enabled", True))
            cx = cfg.get("complexity") or {}
            cx_on = bool(cx.get("enabled", True))
            lvl = cx.get("level", "(unset)")
            lines = ["lane uncensored: %s" % ("ON" if unc else "OFF"),
                     "lane frontier:   %s (level %s)" % ("ON" if cx_on else "OFF", lvl),
                     "flip: /router lane <uncensored|frontier> on|off (confirmation-token guarded)"]
            gate = _commands()._mutation_gate_line()
            if gate:
                lines.append(gate)
            return "\n".join(lines)
        target = args[0].lower()
        if target not in _commands()._LANE_MAP:
            return "unknown lane: %s (uncensored | frontier)" % target
        if len(args) < 2 or args[1].lower() not in ("on", "off"):
            return "usage: /router lane <uncensored|frontier> on|off"
        want = args[1].lower() == "on"
        knob = _commands()._LANE_MAP[target]
        if target == "uncensored":
            # uncensored master switch: also surface the mode nuance
            summary = "lane uncensored -> %s (master switch 'enabled'; classification.mode untouched)" % ("ON" if want else "OFF")
        else:
            summary = "lane frontier -> %s (complexity.enabled; level preserved)" % ("ON" if want else "OFF")
        token = _commands()._issue_confirmation("config_set", [knob, "true" if want else "false"], summary)
        return ("proposed: %s\nconfirm within 120s: /router confirm %s" % (summary, token))
    except Exception as exc:  # noqa: BLE001
        return "error: lane failed: %s" % str(exc)[:160]


def _cmd_reload(_args: List[str]) -> str:
    if not _commands().mutations_armed():
        return _commands()._mutation_gate_line() or "mutations disabled"
    summary = "reload dirty-flag (observability parity)"
    token = _commands()._issue_confirmation("reload", [], summary)
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
        n = len(_commands()._pending_confirmations)
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
                detail, _commands()._config_fingerprint(after)))
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
        spec = _commands()._knob_whitelist().get(knob)
        if spec is None or spec.get("gate") or knob in _commands()._NOT_WRITABLE:
            return "config set aborted: knob %r no longer valid" % knob
        ok, why, parsed = _commands()._parse_value(spec, raw_value)
        if not ok and knob == "debug_banner":
            ok, why, parsed = _commands()._parse_value_debug_banner_fallback(spec, raw_value)
        if not ok:
            return "config set aborted: %s — %s" % (knob, why)
        before = config_writer.read_plugin_section()
        ok_w, detail = _commands()._apply_config_set(knob, parsed, extra)
        if not ok_w:
            return "config set failed: %s" % detail
        after = config_writer.read_plugin_section()
        return _commands()._config_set_result_line(knob, before, after, detail)
    except Exception as exc:  # noqa: BLE001
        return "error: confirmed config set failed: %s" % str(exc)[:160]


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
            "max_tokens": _commands().DOCTOR_PING_MAX_TOKENS,
            "temperature": 0.0,
        })
        # Key resolution mirrors _resolve_key: env first, profile dotenv fallback.
        from .anchor_exec import _resolve_key

        api_key = _resolve_key(ep)
        if not api_key:
            return "ping: key unavailable (%s)" % ep.api_key_env
        curl_cmd = [
            "curl", "-sS", "--max-time", str(_commands().DOCTOR_PING_TIMEOUT), "-X", "POST",
            ep.base_url.rstrip("/") + "/chat/completions",
            "-H", "Authorization: Bearer " + api_key,
            "-H", "Content-Type: application/json",
            "-d", payload_json,
        ]
        proc = await asyncio.create_subprocess_exec(
            *curl_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=_commands().DOCTOR_PING_TIMEOUT + 2)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            return "ping: FAILED (timeout after %ds)" % _commands().DOCTOR_PING_TIMEOUT
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
            "cost: %s (recorded to spend ledger — honest: it bills the cap)" % _commands()._fmt_cost(float(cost or 0.0)),
        ])
    except Exception as exc:  # noqa: BLE001
        return "error: ping failed: %s" % str(exc)[:160]


def _rate_admit(kind: str) -> Optional[str]:
    return _rate_check(kind)


def _cmd_frontier(args: List[str]) -> str:
    """Friendly toggle surface (Goran 2026-09-08): frontier pre/post on|off|state.
    Maps: pre on→pre_mode=route, pre off→pre_mode=off;
          post on→audit_mode=complex, post always→audit_mode=always, post off→audit_mode=off.
    Reads work without the mutation gate; sets go through _commands()._config_set (token-guarded
    for consequential flips per the existing discipline)."""
    try:
        if not args:
            return ("usage: /router frontier pre on|off | post on|off|always | state")
        what = args[0].lower().lstrip("/")
        if what == "state" or (len(args) < 2 and what in ("pre", "post")):
            from .router_core import _complexity_cfg

            comp = _complexity_cfg() or {}
            return ("frontier pre=%s post=%s   (pre: route|shadow|off; post: off|complex|always)"
                    % (comp.get("pre_mode") or "off", comp.get("audit_mode") or "off"))
        val = args[1].lower()
        if what == "pre":
            if val == "on":
                return _commands()._config_set(["complexity.pre_mode", "route"])
            if val == "off":
                return _commands()._config_set(["complexity.pre_mode", "off"])
            if val == "state":
                return _commands()._config_get(["complexity.pre_mode"])
            return "pre: on | off | state"
        if what == "post":
            if val == "on":
                return _commands()._config_set(["complexity.audit_mode", "complex"])
            if val == "always":
                return _commands()._config_set(["complexity.audit_mode", "always"])
            if val == "off":
                return _commands()._config_set(["complexity.audit_mode", "off"])
            if val == "state":
                return _commands()._config_get(["complexity.audit_mode"])
            return "post: on | always | off | state"
        return "usage: /router frontier pre on|off | post on|off|always | state"
    except Exception as exc:  # noqa: BLE001
        return "error: frontier toggle failed: %s" % str(exc)[:160]
