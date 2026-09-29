"""R19.2 — MIDTURN DECISION HOOK (ADDENDUM 3 rewiring: transform_tool_result).

Live-probed root cause: the llm_execution middleware fires ONCE per turn,
not per LLM call — a delta-scan at that seam never sees tool results.
Detection therefore moved to the `transform_tool_result` platform hook,
which core fires after EVERY tool execution (timeout-bounded hook class in
plugins_dispatch._HOOK_TIMEOUT_BOUNDED_HOOKS). The single tool output passed
to the hook IS the delta — no run-state last_n tracking needed.

TWO-SEAM wiring (ADDENDUM 4, replaces the v4.11.1 seam; conductor
forensics: transform_tool_result is dead-from-birth on 0.21.4 — its only
invoke_hook site lives in the harness tool module, which NOTHING in the
agent execution path imports; this plugin NEVER imports hermes core):

  SEAM 1 — transform_terminal_output hook (same-turn latency): core fires
  it after EVERY terminal tool result, mid-run. Scans ONLY the tool
  output text (that IS the delta) with the options-structure regex +
  R19.1 provenance filter. Fork found -> causal envelope -> backend ->
  verdict staged in the per-session pending queue (max 1, latest-wins) +
  ledger row seam=terminal. Never blocks or modifies the tool result.

  SEAM 2 — llm_execution turn-start sweep (one-turn latency, full
  coverage): at the once-per-turn middleware fire, scan the request's
  message slice since the last scanned marker (bounded: last 30
  messages, 60KB) — sees execute_code / read_file / patch / write_file
  results from the previous turn and the user ingress. Ledger rows
  seam=turn_boundary.

DELIVERY: 'on' mode stages the advisory per session (max 1 pending,
latest-wins); the middleware flushes it into the in-flight request. If
the run ends before a flush, the verdict is ledger-only. Never blocks
or modifies any tool result.

LEG 1 detection: tool output text + tool_name, R19.1 provenance filter,
decision extract_options regex. Config gate decision.midturn: shadow
(detect+log+ledger, never appends) | on | off (default, DARK).
LEG 2: causal envelope -> backend -> typed verdict -> pending advisory.
LEG 3: aggregate banner at POST/turn close (one banner, park/consume).
LEG 4: ledger trigger kind 'midturn_hook' with delta_source='tool_result',
tool_name, fork signature, mode. 600s signature cooldown; 50/run cap (rows
beyond cap still recorded); global decision breaker unchanged.

Every public entry is fail-open and never raises.
"""
import json
import re
import threading
import time
from typing import Any, Dict, List

from . import decision as _dec

import logging

logger = logging.getLogger("hermes_router.decision_midturn")

# Config gate: decision.midturn = shadow | on | off (default off fleet-wide).
MIDTURN_MODE_KEY = "midturn"
VALID_MODES = ("shadow", "on", "off")

# Cooldown: per normalized fork signature, 600s (R16 normalized-hash pattern).
COOLDOWN_SECONDS = 600.0

# Run cap: max verdicts (backend calls) per run. Ledger rows beyond the cap
# are still recorded; calls are not.
RUN_CAP = 50

TRIGGER = "midturn"
# §5.7 ledger trigger kind for the midturn hook.
TRIGGER_KIND = "midturn_hook"
DELTA_SOURCE = "tool_result"
SEAM_TERMINAL = "terminal"          # SEAM 1: transform_terminal_output
SEAM_TURN_BOUNDARY = "turn_boundary"  # SEAM 2: llm_execution sweep
# SEAM 2 bounds: scan at most the last 30 messages / 60KB of text.
SWEEP_MAX_MESSAGES = 30
SWEEP_MAX_BYTES = 60 * 1024

ADVISORY_HEADER = ("[ROUTER ADVISORY — decision lane; banner at turn close; "
                   "may ignore]")

# Reason codes
REASON_SHADOW = "shadow"
REASON_COOLDOWN = "midturn_cooldown"
REASON_RUN_CAP = "midturn_run_cap"

# -----------------------------------------------------------------------
# State — bounded in-process; a gateway restart resets it (fail-open).
# -----------------------------------------------------------------------

_LOCK = threading.Lock()
# (session_id) -> run state:
#   {"count": int, "consumed": [ {choice,tokens_in,tokens_out,cost,model} ],
#    "pending": [advisory text awaiting the next flush]  # max 1, latest-wins
#    "last_n": int  # SEAM 2: messages already scanned at a prior turn start}
_RUNS: Dict[str, Dict[str, Any]] = {}
# fork signature hash -> last-fire ts
_COOLDOWN: Dict[str, float] = {}
_COOLDOWN_MAX_KEYS = 512


def reset_midturn() -> None:
    """Test/diagnostic reset. Never raises."""
    with _LOCK:
        _RUNS.clear()
        _COOLDOWN.clear()


def runs_state() -> Dict[str, Dict[str, Any]]:
    """Diagnostic snapshot (copy). Never raises."""
    with _LOCK:
        return {k: dict(v, consumed=list(v.get("consumed", [])),
                        pending=list(v.get("pending", [])))
                for k, v in _RUNS.items()}


def _mode(cfg: Dict[str, Any]) -> str:
    try:
        m = str(cfg.get(MIDTURN_MODE_KEY) or "off").strip().lower()
        return m if m in VALID_MODES else "off"
    except Exception:  # noqa: BLE001
        return "off"


def _cfg() -> Dict[str, Any]:
    """Decision cfg with the midturn default forced in. Never raises."""
    try:
        cfg = dict(_dec._cfg())
    except Exception:  # noqa: BLE001
        try:
            from . import decision as _d
            cfg = dict(_d.DEFAULTS)
        except Exception:  # noqa: BLE001
            cfg = {}
    cfg.setdefault(MIDTURN_MODE_KEY, "off")
    return cfg


def _norm_hash(text: str) -> str:
    """R16 normalized-hash machinery (router_core._cooldown_hash semantics
    without the session salt): sha1(lowercase, digits->'#', ws-collapsed)
    [:12]. Never raises."""
    try:
        import hashlib
        t = re.sub(r"[0-9]+", "#", str(text or "").lower())
        t = re.sub(r"\s+", " ", t).strip()
        return hashlib.sha1(t.encode("utf-8", "replace")).hexdigest()[:12]
    except Exception:  # noqa: BLE001
        return ""


def _msg_text(content: Any) -> str:
    """Flatten a message content (string or OpenAI parts list). Never raises."""
    try:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for p in content:
                if isinstance(p, str):
                    parts.append(p)
                elif isinstance(p, dict):
                    parts.append(str(p.get("text") or p.get("content") or ""))
            return "\n".join(x for x in parts if x)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _state(session_id: str) -> Dict[str, Any]:
    return _RUNS.setdefault(session_id, {"count": 0, "consumed": [],
                                         "pending": [], "last_n": 0})


# -----------------------------------------------------------------------
# LEG 1 — detection at the transform_tool_result seam
# -----------------------------------------------------------------------

def _decode_content(content: Any) -> str:
    """v4.11.6 FIX: tool content in real sessions is a JSON blob — the
    stored string is '{"output": "AUDIT RESULTS...\\nApproach 1: ..."}'
    where the newlines INSIDE the value are literal backslash-n sequences,
    not real newlines. Line-anchored markers never match against that raw
    blob. If content parses as a JSON object, take the first present key
    of ('output','result','content','text') as the scan text (json.loads
    handles unescaping — never unescape manually); else use content
    as-is. Never raises."""
    try:
        text = content if isinstance(content, str) else str(content or "")
        if not text:
            return ""
        s = text.strip()
        if not (s.startswith("{") and s.endswith("}")):
            return text
        try:
            data = json.loads(s)
        except Exception:  # noqa: BLE001 — malformed JSON: raw fallback
            return text
        if isinstance(data, dict):
            for key in ("output", "result", "content", "text"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val
                if val is not None and not isinstance(val, (dict, list)):
                    sv = str(val)
                    if sv.strip():
                        return sv
        return text
    except Exception:  # noqa: BLE001 — decode must never break the scan
        return content if isinstance(content, str) else str(content or "")


def on_terminal_output(session_id: str, tool_name: str, result: Any,
                       seam: str = SEAM_TERMINAL) -> None:
    """SEAM 1 entry — called from the plugin's transform_terminal_output
    hook after EVERY terminal tool execution. The single tool output IS
    the delta. Shadow: detect + log + ledger, never stages. Off: fully
    silent. The tool result itself is never touched (the hook caller
    returns None). Never raises."""
    try:
        cfg = _cfg()
        mode = _mode(cfg)
        if mode == "off" or not str(session_id or ""):
            return
        text = result if isinstance(result, str) else str(result or "")
        if not text:
            return
        text = _decode_content(text)  # v4.11.6: JSON-blob decode
        if not text:
            return
        if _dec.provenance_skip(text, cfg):
            return
        opts = _dec.extract_options(text)
        if not opts:
            _log(session_id, "midturn_suppressed",
                 reason=_dec.REASON_NO_OPTIONS, mode=mode,
                 tool=str(tool_name or ""), seam=seam)
            return
        _handle_hit(str(session_id), str(tool_name or ""), text, opts, cfg,
                    mode, seam)
    except Exception:  # noqa: BLE001 — fail-open, never break the tool result
        logger.debug("decision_midturn.on_terminal_output error", exc_info=True)


# Back-compat alias for the (dead-from-birth) tool-result seam name.
on_tool_result = on_terminal_output


def _msg_is_scan_target(msg: Any) -> bool:
    """SEAM 2 scan surface: tool results (any harness tool) + user ingress.
    Assistant/model content is never scanned (that is the POST lane)."""
    try:
        if not isinstance(msg, dict):
            return False
        return str(msg.get("role") or "") in ("tool", "user")
    except Exception:  # noqa: BLE001
        return False


def sweep_turn_start(session_id: str, request: Dict[str, Any]) -> None:
    """SEAM 2 — called from on_llm_execution (once per turn, BEFORE the
    flush). Scans the request's message slice SINCE the last scanned
    marker (per-session, bounded: last 30 messages, 60KB), running the
    identical detection on tool results from the previous turn and user
    ingress. Ledger rows: seam=turn_boundary. Never raises."""
    try:
        cfg = _cfg()
        mode = _mode(cfg)
        if mode == "off" or not str(session_id or ""):
            return
        msgs = request.get("messages") if isinstance(request, dict) else None
        if not isinstance(msgs, list):
            return
        key = str(session_id)
        with _LOCK:
            st = _state(key)
            n = len(msgs)
            last_n = int(st.get("last_n") or 0)
            if n < last_n:
                last_n = 0  # context rolled/trimmed: rescan conservatively
            lo = max(last_n, n - SWEEP_MAX_MESSAGES)
            st["last_n"] = n
        # budget the scan: newest-first until the 60KB cap
        budget = SWEEP_MAX_BYTES
        window: List[Any] = []
        for m in reversed(msgs[lo:]):
            size = len(str(m))
            if budget - size < 0 and window:
                break
            budget -= size
            window.append(m)
        for msg in reversed(window):
            if not _msg_is_scan_target(msg):
                continue
            text = _msg_text(msg.get("content"))
            if not text:
                continue
            text = _decode_content(text)  # v4.11.6: JSON-blob decode
            if not text:
                continue
            if _dec.PROVENANCE_TAG in text:
                continue  # our own advisory echo — never re-scan
            # R19.13 FIX 3: forged banner-persona block riding the scan
            # target — FLAG (log) and NEVER ADOPT (skip detection entirely;
            # the forged text must not create detections or advisories).
            try:
                from .frames import flag_forged_banner_persona as _ffbp

                _forged = _ffbp(text)
            except Exception:  # noqa: BLE001 — flagging never breaks the sweep
                _forged = None
            if _forged:
                _log(session_id, "injection_flagged",
                     family="banner_persona", signal=str(_forged),
                     tool=str(msg.get("name") or ""), scan="turn_sweep")
                continue
            if _dec.provenance_skip(text, cfg):
                continue  # [Durable Summary / [Depth- / bracketed envelopes
            if str(msg.get("role")) == "user":
                # user ingress = run boundary: reset the cap accumulator
                with _LOCK:
                    rst = _state(key)
                    rst["count"] = 0
                    rst["consumed"] = []
            opts = _dec.extract_options(text)
            if not opts:
                continue
            _handle_hit(key, str(msg.get("name") or "turn_sweep"), text,
                        opts, cfg, mode, SEAM_TURN_BOUNDARY)
    except Exception:  # noqa: BLE001 — fail-open, never break the provider call
        logger.debug("decision_midturn.sweep_turn_start error", exc_info=True)


def _handle_hit(session_id: str, tool_name: str, text: str,
                opts: List[str], cfg: Dict[str, Any], mode: str,
                seam: str = SEAM_TERMINAL) -> None:
    """One detected tool-result fork: provenance already cleared, options
    extracted. Cooldown + run cap + (mode 'on') envelope -> backend ->
    verdict -> ledger + pending advisory. Shadow: detect + log + ledger
    only. Never raises."""
    try:
        sig = _norm_hash(text)
        now = time.time()
        with _LOCK:
            last = _COOLDOWN.get(sig)
            if last is not None and (now - last) < COOLDOWN_SECONDS:
                _log(session_id, "midturn_suppressed", reason=REASON_COOLDOWN,
                     sig=sig, mode=mode, tool=tool_name, seam=seam)
                return
            _COOLDOWN[sig] = now
            while len(_COOLDOWN) > _COOLDOWN_MAX_KEYS:
                _COOLDOWN.pop(next(iter(_COOLDOWN)))
            st = _state(session_id)
            over_cap = int(st.get("count") or 0) >= RUN_CAP

        fork_cls = _dec.fork_class(text, opts)
        if mode == "shadow" or over_cap:
            reason = REASON_SHADOW if mode == "shadow" else REASON_RUN_CAP
            _ledger(session_id, cfg, sig=sig, mode=mode, fork_cls=fork_cls,
                    tool_name=tool_name, seam=seam, fail_open_reason=reason)
            _log(session_id, "midturn_suppressed", reason=reason, sig=sig,
                 mode=mode, tool=tool_name, fork_class=fork_cls, seam=seam)
            return

        # mode 'on'
        if _dec._breaker_open(cfg):
            _ledger(session_id, cfg, sig=sig, mode=mode, fork_cls=fork_cls,
                    tool_name=tool_name, seam=seam,
                    fail_open_reason=_dec.REASON_BREAKER_OPEN)
            _log(session_id, "midturn_suppressed",
                 reason=_dec.REASON_BREAKER_OPEN, sig=sig, mode=mode,
                 tool=tool_name, seam=seam)
            return
        envelope = _dec.build_envelope(session_id, text, opts, TRIGGER, cfg)
        if not envelope:
            _ledger(session_id, cfg, sig=sig, mode=mode, fork_cls=fork_cls,
                    tool_name=tool_name, seam=seam,
                    fail_open_reason=_dec.REASON_NO_OPTIONS)
            _log(session_id, "midturn_suppressed",
                 reason=_dec.REASON_NO_OPTIONS, sig=sig, mode=mode,
                 tool=tool_name, seam=seam)
            return
        content, meta, reason = _dec.call_backend(envelope, cfg)
        base = _ledger_base(session_id, cfg, sig=sig, mode=mode,
                            fork_cls=fork_cls, tool_name=tool_name,
                            seam=seam)
        base["envelope_hash"] = _dec.envelope_hash(envelope)
        base["model"] = str(meta.get("model") or cfg.get("model") or "")
        base["model_version"] = str(meta.get("model") or "")
        if content is None:
            _dec.ledger_write(dict(base, fail_open_reason=str(reason)))
            _log(session_id, "midturn_suppressed", reason=str(reason),
                 sig=sig, mode=mode, tool=tool_name, seam=seam,
                 backend=str(cfg.get("backend") or ""))
            return
        verdict, vreason = _dec.validate_verdict(content, envelope)
        if verdict is None:
            _dec.bump_counter("malformed")
            _dec.ledger_write(dict(base, fail_open_reason=str(vreason)))
            _log(session_id, "midturn_suppressed", reason=str(vreason),
                 sig=sig, mode=mode, tool=tool_name, seam=seam)
            return
        if verdict["choice"] == _dec.STAND_DOWN_CHOICE:
            _dec.ledger_write(dict(base, outcome="stand_down",
                                   fail_open_reason="stand_down"))
            _log(session_id, "midturn_stand_down", sig=sig, mode=mode,
                 tool=tool_name, seam=seam)
            return
        rid = _dec.ledger_write(dict(
            base, choice=str(verdict["choice"]),
            confidence=round(float(verdict["confidence"]), 4),
            verdict_json=json.dumps(verdict, default=str)))
        _dec._record_success()
        _record_consumed(session_id, verdict, meta, cfg)
        _log(session_id, "midturn_verdict",
             choice=str(verdict["choice"]),
             confidence=round(float(verdict["confidence"]), 3),
             sig=sig, mode=mode, tool=tool_name, seam=seam, ledger_id=rid,
             backend=str(cfg.get("backend") or ""))
        adv = _render_midturn_advisory(verdict, envelope, meta)
        if adv:
            with _LOCK:
                # max 1 pending, latest-wins
                _state(session_id)["pending"] = [adv]
    except Exception:  # noqa: BLE001 — fail-open on any backend/hook error
        logger.debug("decision_midturn._handle_hit error", exc_info=True)
        try:
            _ledger(session_id, cfg, sig=_norm_hash(text), mode=mode,
                    fork_cls="", tool_name=tool_name, seam=seam,
                    fail_open_reason="hook_error")
        except Exception:  # noqa: BLE001
            pass


# -----------------------------------------------------------------------
# Delivery — pending-advisory queue flushed at the next llm_execution fire
# -----------------------------------------------------------------------

def flush(session_id: str) -> List[str]:
    """Returns and clears the session's pending advisory envelopes
    (max 1, latest-wins). Never raises."""
    try:
        with _LOCK:
            st = _RUNS.get(str(session_id or ""))
            pending = list(st.get("pending") or []) if st else []
            if st:
                st["pending"] = []
        return pending
    except Exception:  # noqa: BLE001
        return []


def _sanitize_session_key(raw: Any) -> str:
    """v4.11.4 FIX 3: accept only keys matching the platform session-id
    shape (non-empty, bounded, no whitespace/control chars — a stale or
    foreign context key like a repr-bleed string must never become ledger
    state). Returns the sanitized key or "" (caller falls back)."""
    try:
        s = str(raw or "").strip()
        if not s or len(s) > 128:
            return ""
        if re.search(r"[\s\x00-\x1f]", s):
            return ""
        if not re.fullmatch(r"[A-Za-z0-9._:@/\-+=]+", s):
            return ""
        return s
    except Exception:  # noqa: BLE001
        return ""


def flush_and_scan(session_id: str, request: Dict[str, Any]) -> List[str]:
    """SEAM 2 entry — called from on_llm_execution (once per turn): first
    the turn-start sweep (previous-turn tool results + user ingress,
    seam=turn_boundary), then the pending-advisory flush (a verdict staged
    by SEAM 1 last turn, or by this scan in 'on' mode) rides into the
    in-flight request. Shadow mode: sweep logs + ledgers only, flush
    returns []. Session key is sanitized (FIX 3) — invalid keys fall back
    to the shared active-session bucket with a debug log of the raw key.
    Never raises."""
    try:
        key = _sanitize_session_key(session_id)
        if not key:
            logger.debug(
                "decision_midturn: unsanitized session key fallback"
                " raw=%r", str(session_id)[:200])
            key = "active-session"
        sweep_turn_start(key, request)
        return flush(key)
    except Exception:  # noqa: BLE001
        return []


def _ledger_base(session_id: str, cfg: Dict[str, Any], sig: str, mode: str,
                 fork_cls: str = "", tool_name: str = "",
                 seam: str = SEAM_TERMINAL) -> Dict[str, Any]:
    return {
        "session_id": str(session_id or ""),
        "task_id": "midturn",
        "trigger": TRIGGER,
        "trigger_kind": TRIGGER_KIND,
        "fork_class": fork_cls,
        "options_hash": sig,
        "model": str(cfg.get("model") or ""),
        "delta_source": DELTA_SOURCE,
        "fork_signature": sig,
        "midturn_mode": mode,
        "tool_name": str(tool_name or ""),
        "seam": str(seam or ""),
    }


def _ledger(session_id: str, cfg: Dict[str, Any], sig: str, mode: str,
            fork_cls: str = "", tool_name: str = "", seam: str = "",
            fail_open_reason: str = "") -> None:
    try:
        _dec.ledger_write(dict(_ledger_base(session_id, cfg, sig, mode,
                                            fork_cls, tool_name,
                                            seam or SEAM_TERMINAL),
                               fail_open_reason=str(fail_open_reason)))
    except Exception:  # noqa: BLE001 — ledger must never break the lane
        pass


def _log(session_id: str, event: str, **fields: Any) -> None:
    try:
        try:
            from . import _log_route as _lr  # relative: gateway-safe
        except ImportError:
            from hermes_router import _log_route as _lr
        _lr(event, lane="decision", trigger=TRIGGER,
            session_id=str(session_id or ""), **fields)
    except Exception:  # noqa: BLE001 — logging never breaks the lane
        pass


# -----------------------------------------------------------------------
# LEG 2 — advisory envelope (mode 'on' only)
# -----------------------------------------------------------------------

def _render_midturn_advisory(verdict: Dict[str, Any],
                             envelope: Dict[str, Any],
                             meta: Dict[str, Any]) -> str:
    """ONE advisory envelope for the in-flight request queue: provenance-
    stamped header + verdict + why_not (alternatives). Never rewrites
    model/tool content; non-binding."""
    try:
        alts = [str(a) for a in (verdict.get("alternatives") or [])]
        why_not = ("why_not: %s" % ", ".join(alts)) if alts else \
            "why_not: (no viable alternative in the closed option set)"
        return "\n".join([
            ADVISORY_HEADER,
            _dec.render_advisory(verdict, envelope),
            why_not,
        ])
    except Exception:  # noqa: BLE001
        return ""


def _record_consumed(session_id: str, verdict: Dict[str, Any],
                     meta: Dict[str, Any], cfg: Dict[str, Any]) -> None:
    """LEG 3 accumulator: remember the verdict for the turn-close aggregate
    banner. Bounded by RUN_CAP. Never raises."""
    try:
        model = str(meta.get("model") or cfg.get("model") or "")
        ti, to = meta.get("tokens_in"), meta.get("tokens_out")
        try:
            from . import usage_ledger
            cost = usage_ledger.estimate_cost(model, ti, to)
        except Exception:  # noqa: BLE001
            cost = 0.0
        rec = {"choice": str(verdict.get("choice") or ""),
               "tokens_in": ti if isinstance(ti, int) else 0,
               "tokens_out": to if isinstance(to, int) else 0,
               "cost": float(cost or 0.0), "model": model,
               "endpoint": str(meta.get("endpoint") or "")}
        with _LOCK:
            st = _state(session_id)
            st["count"] = int(st.get("count") or 0) + 1
            st.setdefault("consumed", []).append(rec)
    except Exception:  # noqa: BLE001
        pass


# -----------------------------------------------------------------------
# LEG 3 — aggregate banner at turn close (park/consume reuse)
# -----------------------------------------------------------------------

_HIST_BUCKETS = 4


def close_turn(session_id: str) -> str:
    """Called from the POST/turn-close hook exactly once per turn. Returns
    the aggregate decision banner (>=1 midturn verdict consumed this run) or
    "" — ONE banner, not 50. 1 verdict -> current single-verdict banner
    format with trigger=midturn. Drains the accumulator AND resets the run
    counter (turn close = run boundary; unflushed pending advisories die
    here — the verdict stays ledger-only). Never raises."""
    try:
        with _LOCK:
            st = _RUNS.get(str(session_id or ""))
            consumed = list(st.get("consumed") or []) if st else []
            if st:
                st["consumed"] = []
                st["count"] = 0
                st["pending"] = []
        if not consumed:
            return ""
        n = len(consumed)
        ti = sum(int(c.get("tokens_in") or 0) for c in consumed)
        to = sum(int(c.get("tokens_out") or 0) for c in consumed)
        total = sum(float(c.get("cost") or 0.0) for c in consumed)
        if n == 1:
            c = consumed[0]
            banner = _dec.render_decision_banner(
                "midturn", c.get("model") or "",
                {"tokens_in": ti, "tokens_out": to,
                 "endpoint": str(c.get("endpoint") or "")},
                initiator="agent")
            return banner or _aggregate_line(n, ti, to, total, consumed)
        return _aggregate_line(n, ti, to, total, consumed)
    except Exception:  # noqa: BLE001 — banner must never break delivery
        logger.debug("decision_midturn.close_turn error", exc_info=True)
        return ""


def _aggregate_line(n: int, ti: int, to: int, total: float,
                    consumed: List[Dict[str, Any]]) -> str:
    """'· router · decision | midturn x<N> | <histogram> | tok <n/n> |
    $<total> | initiator=agent'. Histogram capped at 4 buckets + 'other'."""
    try:
        counts: Dict[str, int] = {}
        for c in consumed:
            k = str(c.get("choice") or "?")
            counts[k] = counts.get(k, 0) + 1
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        shown = ordered[:_HIST_BUCKETS]
        other = sum(v for _, v in ordered[_HIST_BUCKETS:])
        hist = " / ".join("%d %s" % (v, k) for k, v in shown)
        if other:
            hist = (hist + " / " if hist else "") + "%d other" % other
        return ("· router · decision | midturn x%d | %s | tok %d/%d | $%.6f"
                " | initiator=agent"
                % (n, hist or "(none)", ti, to, total))
    except Exception:  # noqa: BLE001
        return ""
