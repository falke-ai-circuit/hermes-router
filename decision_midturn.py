"""R19.2 — MIDTURN DECISION HOOK (ADDENDUM 3 rewiring: transform_tool_result).

Live-probed root cause: the llm_execution middleware fires ONCE per turn,
not per LLM call — a delta-scan at that seam never sees tool results.
Detection therefore moved to the `transform_tool_result` platform hook,
which core fires after EVERY tool execution (timeout-bounded hook class in
plugins_dispatch._HOOK_TIMEOUT_BOUNDED_HOOKS). The single tool output passed
to the hook IS the delta — no run-state last_n tracking needed.

DELIVERY: 'on' mode queues the advisory per session; the llm_execution
middleware (once per turn is fine — the verdict lands in the request that
follows the fork) flushes it into the in-flight request. If the run ends
before a flush, the verdict is ledger-only. Never blocks the tool result
itself (the hook caller returns None; the platform filters non-string
returns, so the result passes through untouched).

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
#    "pending": [advisory texts awaiting the next llm_execution flush]}
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


def _state(session_id: str) -> Dict[str, Any]:
    return _RUNS.setdefault(session_id, {"count": 0, "consumed": [],
                                         "pending": []})


# -----------------------------------------------------------------------
# LEG 1 — detection at the transform_tool_result seam
# -----------------------------------------------------------------------

def on_tool_result(session_id: str, tool_name: str, result: Any) -> None:
    """Called from the plugin's transform_tool_result hook after EVERY tool
    execution. The single tool output IS the delta. Shadow: detect + log +
    ledger, never appends. Off: fully silent. The tool result itself is
    never touched (the hook caller returns None). Never raises."""
    try:
        cfg = _cfg()
        mode = _mode(cfg)
        if mode == "off" or not str(session_id or ""):
            return
        text = result if isinstance(result, str) else str(result or "")
        if not text:
            return
        if _dec.provenance_skip(text, cfg):
            return
        opts = _dec.extract_options(text)
        if not opts:
            _log(session_id, "midturn_suppressed",
                 reason=_dec.REASON_NO_OPTIONS, mode=mode,
                 tool=str(tool_name or ""))
            return
        _handle_hit(str(session_id), str(tool_name or ""), text, opts, cfg,
                    mode)
    except Exception:  # noqa: BLE001 — fail-open, never break the tool result
        logger.debug("decision_midturn.on_tool_result error", exc_info=True)


def _handle_hit(session_id: str, tool_name: str, text: str,
                opts: List[str], cfg: Dict[str, Any], mode: str) -> None:
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
                     sig=sig, mode=mode, tool=tool_name)
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
                    tool_name=tool_name, fail_open_reason=reason)
            _log(session_id, "midturn_suppressed", reason=reason, sig=sig,
                 mode=mode, tool=tool_name, fork_class=fork_cls)
            return

        # mode 'on'
        if _dec._breaker_open(cfg):
            _ledger(session_id, cfg, sig=sig, mode=mode, fork_cls=fork_cls,
                    tool_name=tool_name,
                    fail_open_reason=_dec.REASON_BREAKER_OPEN)
            _log(session_id, "midturn_suppressed",
                 reason=_dec.REASON_BREAKER_OPEN, sig=sig, mode=mode,
                 tool=tool_name)
            return
        envelope = _dec.build_envelope(session_id, text, opts, TRIGGER, cfg)
        if not envelope:
            _ledger(session_id, cfg, sig=sig, mode=mode, fork_cls=fork_cls,
                    tool_name=tool_name,
                    fail_open_reason=_dec.REASON_NO_OPTIONS)
            _log(session_id, "midturn_suppressed",
                 reason=_dec.REASON_NO_OPTIONS, sig=sig, mode=mode,
                 tool=tool_name)
            return
        content, meta, reason = _dec.call_backend(envelope, cfg)
        base = _ledger_base(session_id, cfg, sig=sig, mode=mode,
                            fork_cls=fork_cls, tool_name=tool_name)
        base["envelope_hash"] = _dec.envelope_hash(envelope)
        base["model"] = str(meta.get("model") or cfg.get("model") or "")
        base["model_version"] = str(meta.get("model") or "")
        if content is None:
            _dec.ledger_write(dict(base, fail_open_reason=str(reason)))
            _log(session_id, "midturn_suppressed", reason=str(reason),
                 sig=sig, mode=mode, tool=tool_name,
                 backend=str(cfg.get("backend") or ""))
            return
        verdict, vreason = _dec.validate_verdict(content, envelope)
        if verdict is None:
            _dec.bump_counter("malformed")
            _dec.ledger_write(dict(base, fail_open_reason=str(vreason)))
            _log(session_id, "midturn_suppressed", reason=str(vreason),
                 sig=sig, mode=mode, tool=tool_name)
            return
        if verdict["choice"] == _dec.STAND_DOWN_CHOICE:
            _dec.ledger_write(dict(base, outcome="stand_down",
                                   fail_open_reason="stand_down"))
            _log(session_id, "midturn_stand_down", sig=sig, mode=mode,
                 tool=tool_name)
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
             sig=sig, mode=mode, tool=tool_name, ledger_id=rid,
             backend=str(cfg.get("backend") or ""))
        adv = _render_midturn_advisory(verdict, envelope, meta)
        if adv:
            with _LOCK:
                _state(session_id).setdefault("pending", []).append(adv)
    except Exception:  # noqa: BLE001 — fail-open on any backend/hook error
        logger.debug("decision_midturn._handle_hit error", exc_info=True)
        try:
            _ledger(session_id, cfg, sig=_norm_hash(text), mode=mode,
                    fork_cls="", tool_name=tool_name,
                    fail_open_reason="hook_error")
        except Exception:  # noqa: BLE001
            pass


# -----------------------------------------------------------------------
# Delivery — pending-advisory queue flushed at the next llm_execution fire
# -----------------------------------------------------------------------

def flush(session_id: str) -> List[str]:
    """Called from on_llm_execution (once per turn is fine — the verdict
    lands in the request that follows the fork). Returns and clears the
    session's pending advisory envelopes. Never raises."""
    try:
        with _LOCK:
            st = _RUNS.get(str(session_id or ""))
            pending = list(st.get("pending") or []) if st else []
            if st:
                st["pending"] = []
        return pending
    except Exception:  # noqa: BLE001
        return []


def _ledger_base(session_id: str, cfg: Dict[str, Any], sig: str, mode: str,
                 fork_cls: str = "", tool_name: str = "") -> Dict[str, Any]:
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
    }


def _ledger(session_id: str, cfg: Dict[str, Any], sig: str, mode: str,
            fork_cls: str = "", tool_name: str = "",
            fail_open_reason: str = "") -> None:
    try:
        _dec.ledger_write(dict(_ledger_base(session_id, cfg, sig, mode,
                                            fork_cls, tool_name),
                               fail_open_reason=str(fail_open_reason)))
    except Exception:  # noqa: BLE001 — ledger must never break the lane
        pass


def _log(session_id: str, event: str, **fields: Any) -> None:
    try:
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
               "cost": float(cost or 0.0), "model": model}
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
                {"tokens_in": ti, "tokens_out": to, "endpoint": ""},
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
