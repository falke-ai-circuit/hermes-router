"""`/router config` command cluster for hermes-router (decomposed from
commands.py, R5 leg 3 — pure refactor, zero behavior change).

Contents: the validated config/knob mutation surface — _cmd_config and its
subcommands (get/list/set/diff/validate/rollback), the knob whitelist, value
parsing, config-writer application, cap-set, confirmation tokens, mutation
gate/fingerprint/consequential helpers.

commands.py keeps thin re-exports (names unchanged — tests patch
`commands._knob_whitelist`, read `commands._apply_config_set`,
`commands.mutations_*` in place). Shared commands.py constants ride the
commands module via a late-bound `_commands()` seam; mutable state
(`_MUTATIONS_ARMED`, pending confirmations) stays owned by commands.py so
behavior is byte-identical.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _commands():
    """commands-module late-binding seam (shared constants + mutable state)."""
    import sys as _sys
    return _sys.modules[__package__ + '.commands']


def time_mod():
    import time as _t

    return _t.time()


def _issue_confirmation(subcommand: str, args: List[str], summary: str) -> str:
    import secrets

    token = secrets.token_hex(16)
    _purge_confirmations()
    _commands()._pending_confirmations[token] = {
        "subcommand": subcommand,
        "args": list(args),
        "summary": summary[:200],
        "deadline": time_mod() + _commands()._CONFIRM_TTL_S,
    }
    return token


def _purge_confirmations() -> None:
    try:
        now = time_mod()
        pending = _commands()._pending_confirmations
        stale = [k for k, v in pending.items()
                 if float(v.get("deadline", 0.0)) < now]
        for k in stale:
            pending.pop(k, None)
    except Exception:  # noqa: BLE001
        pass


def _fmt_ts(ts: float) -> str:
    try:
        if not ts:
            return "?"
        from datetime import datetime, timezone

        return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return "?"


def _mask_value(key: str, value: object) -> str:
    try:
        key_l = str(key).lower()
        if _commands()._SECRETISH.search(key_l):
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
                          budget: int = _commands().CONFIG_GET_MAX_LINES) -> List[str]:
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


def _knob_whitelist() -> Dict[str, Dict[str, object]]:
    """The 3.2 table as data: {knob: {type, min, max, enum, lane}}.
    Y-gated v3.6 knobs carry gate=<label> and NEVER write config."""
    return {
        "enabled": {"type": "bool", "lane": "U"},
        "dry_run": {"type": "bool", "lane": "U"},
        "flinch_reason_gate": {"type": "bool", "lane": "U"},
        "debug_banner": {"type": "int", "min": 0, "max": 3, "lane": "U"},
        "render_max_chars": {"type": "int", "min": 0, "max": 200000, "lane": "U"},
        "thread_digest_chars": {"type": "int", "min": 0, "max": 200000, "lane": "U"},
        "thread_digest_asks": {"type": "int", "min": 0, "max": 50, "lane": "U"},
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
        "complexity.pre_mode": {"type": "enum", "enum": ("route", "shadow", "off"), "lane": "C"},
        "complexity.audit_mode": {"type": "enum", "enum": ("off", "complex", "always"), "lane": "C"},
        "complexity.audit_max_chars": {"type": "int", "min": 400, "max": 16000, "lane": "C"},
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
        "anchor_chain.overflow": {"type": "enum", "enum": _commands().VALID_OVERFLOW, "lane": "A"},
        "decision_head.backend": {"type": "dh_backend", "lane": "C"},
        "decision_head.threshold": {"type": "float", "min": 0.0, "max": 1.0, "lane": "C"},
        "chain.max_tokens": {"type": "int", "min": 8000, "max": 64000, "lane": "U",
                             "chain_entry": True, "requires": "name="},
        "chain.temperature": {"type": "float", "min": 0.0, "max": 2.0, "lane": "U",
                              "chain_entry": True, "requires": "name="},
        "chain.timeout": {"type": "int", "min": 30, "max": 600, "lane": "U",
                          "chain_entry": True, "requires": "name="},
    }


def _parse_bool(raw: str) -> Optional[bool]:
    v = (raw or "").strip().lower()
    if v in ("true", "1", "on", "yes", "enable"):
        return True
    if v in ("false", "0", "off", "no", "disable"):
        return False
    return None


def _parse_value_debug_banner_fallback(spec: Dict[str, object], raw: str) -> Tuple[bool, str, object]:
    """debug_banner accepts legacy true/false/on/off as 1/0 on top of 0-3."""
    low = str(raw or "").strip().lower()
    if low in ("true", "on", "yes"):
        return True, "", 1
    if low in ("false", "off", "no"):
        return True, "", 0
    return False, "expected 0-3", None


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
            # lane on/off switches (Goran-direct 09-07: both lanes activable
            # via /router — flips are consequential, token-guarded):
            "enabled", "classification.mode", "complexity.enabled",
            # v3.6 §10.2: debug_banner changes DELIVERED message shape
            # (tokens/cost) — consequential -> token-guarded, default OFF.
            "debug_banner",
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
        elif knob == "flinch_reason_gate":
            def mut(section: Dict[str, Any], _v=bool(value)) -> None:
                section["flinch_reason_gate"] = _v
        elif knob == "debug_banner":
            def mut(section: Dict[str, Any], _v=value) -> None:
                # 0-3 verbosity; legacy true/false normalize via int(bool)
                try:
                    _iv = int(_v)
                except (TypeError, ValueError):
                    _iv = 1 if str(_v).strip().lower() in ("true", "on", "yes") else 0
                section["debug_banner"] = max(0, min(3, _iv))
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


def _rollback_path() -> str:
    try:
        import hermes_constants

        return str(hermes_constants.get_hermes_home() / _commands()._ROLLBACK_FILENAME)
    except Exception:  # noqa: BLE001
        return os.path.join("/tmp", "shadow-" + _commands()._ROLLBACK_FILENAME)


def _config_fingerprint(section: Dict[str, Any]) -> str:
    """Local section key-set hash (pre-R5 stand-in, blueprint D7)."""
    try:
        import hashlib

        payload = json.dumps(section, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return "?"


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
    lines.append("never chat-settable: %s" % ", ".join(sorted(_commands()._NOT_WRITABLE)))
    lines.append("lowering the cap: config-file act only (bump_cap is UP-only)")
    return "\n".join(lines)


def _config_set(rest: List[str]) -> str:
    if not _commands().mutations_armed():
        return _commands()._mutation_gate_line() or "mutations disabled"
    if len(rest) < 2:
        return "usage: /router config set <knob> <value>   (see /router config list)"
    knob = rest[0].strip().lower()
    wl = _knob_whitelist()
    spec = wl.get(knob)
    if spec is None:
        if knob in _commands()._NOT_WRITABLE:
            return "rejected: %s — %s" % (knob, _commands()._NOT_WRITABLE[knob])
        return ("unknown knob: %s — see /router config list; free-text/new keys are "
                "never chat-settable" % knob)
    if knob in _commands()._NOT_WRITABLE:
        return "rejected: %s — %s" % (knob, _commands()._NOT_WRITABLE[knob])
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
    if not ok and knob == "debug_banner":
        ok, why, parsed = _parse_value_debug_banner_fallback(spec, raw_value)
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
        ok_w, detail = _commands()._apply_config_set(knob, parsed, extra)
        if not ok_w:
            return "config set failed: %s" % detail
        after = config_writer.read_plugin_section()
        return _commands()._config_set_result_line(knob, before, after, detail)
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
    if not _commands().mutations_armed():
        return _commands()._mutation_gate_line() or "mutations disabled"
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
