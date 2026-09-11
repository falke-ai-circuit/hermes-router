"""`/router` diagnostic command group for hermes-router (decomposed from
commands.py, R5 leg 4 — pure refactor, zero behavior change).

Contents: status/menu/health/doctor/stats/sessions/chain/log/budget surfaces
plus their readers (_route_log_path, _read_log_lines, _parse_log_line,
_state_db_path, _query_sessions) and the mutation-gate read/line helpers.

Shared commands.py module state (constants, _MENU, _LOG_LINE_RE,
_MUTATIONS_ARMED mutable dict, logger) is late-bound through `_commands()`
so monkeypatching attributes on the commands module keeps landing exactly
as before. `_MUTATIONS_ARMED` stays OWNED by commands.py — this module only
reads it through the seam.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)  # module-local only; behavior reads go through _commands()


def _commands():
    """commands-module late-binding seam (shared constants + mutable state)."""
    import sys as _sys
    return _sys.modules[__package__ + '.commands']


def mutations_armed() -> bool:
    """True when the mutation surface answers. Set by register-time self-check
    (see check_and_arm at module bottom via __init__ registration)."""
    try:
        return bool(_commands()._MUTATIONS_ARMED.get("armed"))
    except Exception:  # noqa: BLE001
        return False


def _mutation_gate_line() -> str:
    if mutations_armed():
        return ""
    reason = str(_commands()._MUTATIONS_ARMED.get("reason") or "")
    if reason == "not registered":
        return ("mutations disabled: /router command surface not registered "
                "(env gate off or register_command unavailable)")
    return ("mutations disabled: gateway authorization not verified "
            "(set TELEGRAM_ALLOWED_USERS or GATEWAY_ALLOW_ALL_USERS=true on this "
            "profile's gateway, then re-arm)")


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
        path = _commands()._route_log_path()
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
    m = _commands()._LOG_LINE_RE.match(line.strip())
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
        _commands().logger.debug("commands _query_sessions failed: %s", exc)
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


def _fmt_menu() -> str:
    groups: Dict[str, List[str]] = {}
    for name, group, desc in _commands()._MENU:
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
        lines.append("debug_banner: %s | frontier: pre=%s post=%s" % (
            "ON" if data.get("debug_banner") else "OFF",
            data.get("pre_mode") or "?", data.get("audit_mode") or "?"))
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
        lines_log, _rot = _commands()._read_log_lines(5000, _commands().LOG_GREP_MAX_BYTES)
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
            lines.append("events: none in window (log: %s)" % _commands()._route_log_path())

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
        if len(out.splitlines()) > _commands().STATS_MAX_LINES:
            out = "\n".join(out.splitlines()[:_commands().STATS_MAX_LINES]) + "\n... (stats capped at %d lines)" % _commands().STATS_MAX_LINES
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
        n = max(1, min(_commands().SESSIONS_MAX_ROWS, n))
        rows = _commands()._query_sessions(n)
        if rows is None:
            return "sessions unavailable (state.db read failed)"
        if not rows:
            return "sessions: none found"
        lines, _rot = _commands()._read_log_lines(2000, 1024 * 512)
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
            n = _commands().LOG_TAIL_DEFAULT
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
                        n = max(1, min(_commands().LOG_TAIL_MAX_N, int(tok)))
                    except (TypeError, ValueError):
                        pass
                i += 1
            lines, rotated = _commands()._read_log_lines(max(200, n * 4), _commands().LOG_GREP_MAX_BYTES)
            shown: List[str] = []
            for ln in reversed(lines):
                if filt and filt not in ln.lower():
                    continue
                shown.append(ln)
                if len(shown) >= min(n, _commands().LOG_TAIL_MAX_SHOWN):
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
            lines, rotated = _commands()._read_log_lines(_commands().LOG_GREP_MAX_LINES, _commands().LOG_GREP_MAX_BYTES)
            hits = [ln.strip() for ln in lines if needle in ln.lower()]
            out = ["grep %r: %d match(es) in last %d lines" % (needle, len(hits), len(lines))]
            for ln in hits[-_commands().LOG_GREP_MAX_SHOWN:]:
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
            path = _commands()._route_log_path()
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
                               "max_tokens": _commands().DOCTOR_PING_MAX_TOKENS, "temperature": 0.0}
                    content, cost, _pt, _ct = anchored_call(ep, payload, timeout=_commands().DOCTOR_PING_TIMEOUT)
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
