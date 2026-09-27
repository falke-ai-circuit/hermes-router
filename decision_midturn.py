"""R19.2 — MIDTURN DECISION HOOK (on_llm_execution).

Agent-initiated midturn REFLEX was proven dead (0/3 live tasks): detection
here needs ZERO agency — it watches the traffic that already flows. LEG 1
scans ONLY the new messages since the previous LLM call of the same run
(tool results are the target surface: harness verdicts, candidate lists,
conflicting evidence), reusing the R19.1 provenance filter and the decision
detect() options-structure regex. LEG 2 (mode 'on' only) dispatches a causal
envelope -> backend -> typed verdict and appends ONE advisory envelope to
the in-flight request context — never rewrites model/tool content, never
blocks, fail-open on any error. LEG 3: midturn verdicts NEVER emit their own
banner mid-run; at POST/turn close a single aggregate decision banner is
emitted via the existing park/consume mechanics. Shadow mode is detect +
log + ledger only (calibration data, first-class rows).

Ships DARK (decision.midturn: off everywhere, default off fleet-wide).
Every public entry is fail-open and never raises.
"""
import json
import re
import threading
import time
from typing import Any, Dict, List, Optional

from . import decision as _dec

import logging

logger = logging.getLogger("hermes_router.decision_midturn")

# Config gate: decision.midturn = shadow | on | off (default off fleet-wide).
MIDTURN_MODE_KEY = "midturn"
VALID_MODES = ("shadow", "on", "off")

# Cooldown: per normalized fork signature, 600s (R16 normalized-hash pattern).
COOLDOWN_SECONDS = 600.0

# Run cap: max verdicts (backend calls) per (session, run). Ledger rows beyond
# the cap are still recorded; calls are not.
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
#   {"run": int, "last_n": int, "count": int, "consumed": [ {choice,tokens_in,
#    tokens_out,cost,model} ], "seen_run": set(int) — runs already closed}
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
        return {k: dict(v, consumed=list(v.get("consumed", [])))
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


def _is_tool_result(msg: Dict[str, Any]) -> bool:
    """The target surface is tool results only (harness verdicts, candidate
    lists, conflicting evidence). OpenAI wire format: role 'tool'. Some
    harnesses relay tool results as user-role content with an explicit
    tool_result envelope — accept only an explicit marker, never plain user
    prose."""
    try:
        role = str(msg.get("role") or "")
        if role == "tool":
            return True
        if role == "user":
            head = _msg_text(msg.get("content"))[:200].lstrip().lower()
            return head.startswith("tool_result") or \
                head.startswith("[tool result")
        return False
    except Exception:  # noqa: BLE001
        return False


def _is_user_turn(msg: Dict[str, Any]) -> bool:
    """A real (non-tool, non-advisory) user message marks a new run boundary.
    Never raises."""
    try:
        if str(msg.get("role") or "") != "user":
            return False
        t = _msg_text(msg.get("content"))
        if not t:
            return False
        if _is_tool_result(msg):
            return False
        if _dec is not None and _dec.PROVENANCE_TAG in t:
            return False  # our own advisory echo
        return True
    except Exception:  # noqa: BLE001
        return False


def _reset_run(state: Dict[str, Any]) -> None:
    state["run"] = int(state.get("run") or 0) + 1
    state["count"] = 0
    state["consumed"] = []


# -----------------------------------------------------------------------
# LEG 1 — detection at the on_llm_execution seam
# -----------------------------------------------------------------------

def on_llm_call(session_id: str, request: Dict[str, Any]) -> List[str]:
    """Called from __init__.on_llm_execution on EVERY in-run LLM call, BEFORE
    next_call. Scans only NEW messages since the previous call of the same
    run; in mode 'on' returns advisory envelope texts to append to the
    in-flight request (shadow returns [], off returns [] immediately).
    Never raises; fail-open on any error."""
    try:
        cfg = _cfg()
        mode = _mode(cfg)
        if mode == "off" or not str(session_id or ""):
            return []
        if _dec is None:
            return []
        msgs = request.get("messages") if isinstance(request, dict) else None
        if not isinstance(msgs, list):
            return []

        key = str(session_id)
        advisories: List[str] = []
        with _LOCK:
            st = _RUNS.setdefault(key, {"run": 0, "last_n": 0, "count": 0,
                                        "consumed": []})
            n = len(msgs)
            last_n = int(st.get("last_n") or 0)
            if n < last_n:
                # context rolled/trimmed: reset the run baseline, skip the
                # re-scan (conservative — avoids duplicate detections).
                _reset_run(st)
                st["last_n"] = n
                return []
            delta = msgs[last_n:]
            st["last_n"] = n
            # run boundary: a fresh user turn starts a new run (reset caps +
            # consumed accumulator so the banner aggregates one run only).
            if any(_is_user_turn(m) for m in delta
                   if isinstance(m, dict)):
                _reset_run(st)
        for msg in delta:
            if not isinstance(msg, dict) or not _is_tool_result(msg):
                continue
            text = _msg_text(msg.get("content"))
            if not text or _dec.provenance_skip(text, cfg):
                continue
            adv = _handle_hit(key, text, cfg, mode)
            if adv:
                advisories.append(adv)
        return advisories
    except Exception:  # noqa: BLE001 — fail-open, never break the provider call
        logger.debug("decision_midturn.on_llm_call error", exc_info=True)
        return []


def _handle_hit(session_id: str, text: str, cfg: Dict[str, Any],
                mode: str) -> str:
    """One detected tool-result fork: provenance already cleared. Cooldown +
    run cap + (mode 'on') envelope -> backend -> verdict -> ledger + advisory.
    Shadow: detect + log + ledger only. Returns the advisory text (mode 'on',
    verdict landed) or "". Never raises."""
    try:
        sig = _norm_hash(text)
        now = time.time()
        with _LOCK:
            last = _COOLDOWN.get(sig)
            if last is not None and (now - last) < COOLDOWN_SECONDS:
                _log(session_id, "midturn_suppressed", reason=REASON_COOLDOWN,
                     sig=sig, mode=mode)
                return ""
            _COOLDOWN[sig] = now
            while len(_COOLDOWN) > _COOLDOWN_MAX_KEYS:
                _COOLDOWN.pop(next(iter(_COOLDOWN)))
            st = _RUNS.get(session_id) or {"run": 0, "count": 0,
                                           "consumed": []}
            over_cap = int(st.get("count") or 0) >= RUN_CAP

        opts = _dec.extract_options(text)
        if not opts:
            _log(session_id, "midturn_suppressed",
                 reason=_dec.REASON_NO_OPTIONS, sig=sig, mode=mode)
            return ""

        fork_cls = _dec.fork_class(text, opts)
        if mode == "shadow" or over_cap:
            reason = REASON_SHADOW if mode == "shadow" else REASON_RUN_CAP
            _ledger(session_id, cfg, sig=sig, mode=mode, fork_cls=fork_cls,
                    fail_open_reason=reason)
            _log(session_id, "midturn_suppressed", reason=reason, sig=sig,
                 mode=mode, fork_class=fork_cls)
            return ""

        # mode 'on'
        if _dec._breaker_open(cfg):
            _ledger(session_id, cfg, sig=sig, mode=mode, fork_cls=fork_cls,
                    fail_open_reason=_dec.REASON_BREAKER_OPEN)
            _log(session_id, "midturn_suppressed",
                 reason=_dec.REASON_BREAKER_OPEN, sig=sig, mode=mode)
            return ""
        envelope = _dec.build_envelope(session_id, text, opts, TRIGGER, cfg)
        if not envelope:
            _ledger(session_id, cfg, sig=sig, mode=mode, fork_cls=fork_cls,
                    fail_open_reason=_dec.REASON_NO_OPTIONS)
            _log(session_id, "midturn_suppressed",
                 reason=_dec.REASON_NO_OPTIONS, sig=sig, mode=mode)
            return ""
        content, meta, reason = _dec.call_backend(envelope, cfg)
        base = _ledger_base(session_id, cfg, sig=sig, mode=mode, fork_cls=fork_cls)
        base["envelope_hash"] = _dec.envelope_hash(envelope)
        base["model"] = str(meta.get("model") or cfg.get("model") or "")
        base["model_version"] = str(meta.get("model") or "")
        if content is None:
            _dec.ledger_write(dict(base, fail_open_reason=str(reason)))
            _log(session_id, "midturn_suppressed", reason=str(reason),
                 sig=sig, mode=mode, backend=str(cfg.get("backend") or ""))
            return ""
        verdict, vreason = _dec.validate_verdict(content, envelope)
        if verdict is None:
            _dec.bump_counter("malformed")
            _dec.ledger_write(dict(base, fail_open_reason=str(vreason)))
            _log(session_id, "midturn_suppressed", reason=str(vreason),
                 sig=sig, mode=mode)
            return ""
        if verdict["choice"] == _dec.STAND_DOWN_CHOICE:
            _dec.ledger_write(dict(base, outcome="stand_down",
                                   fail_open_reason="stand_down"))
            _log(session_id, "midturn_stand_down", sig=sig, mode=mode)
            return ""
        rid = _dec.ledger_write(dict(
            base, choice=str(verdict["choice"]),
            confidence=round(float(verdict["confidence"]), 4),
            verdict_json=json.dumps(verdict, default=str)))
        _record_success_cfg(cfg)
        _record_consumed(session_id, verdict, meta, cfg)
        _log(session_id, "midturn_verdict",
             choice=str(verdict["choice"]),
             confidence=round(float(verdict["confidence"]), 3),
             sig=sig, mode=mode, ledger_id=rid,
             backend=str(cfg.get("backend") or ""))
        return _render_midturn_advisory(verdict, envelope, meta)
    except Exception:  # noqa: BLE001 — fail-open on any backend/hook error
        logger.debug("decision_midturn._handle_hit error", exc_info=True)
        try:
            _ledger(session_id, cfg, sig=_norm_hash(text), mode=mode,
                    fork_cls="", fail_open_reason="hook_error")
        except Exception:  # noqa: BLE001
            pass
        return ""


def _record_success_cfg(cfg: Dict[str, Any]) -> None:
    try:
        _dec._record_success()
    except Exception:  # noqa: BLE001
        pass


def _ledger_base(session_id: str, cfg: Dict[str, Any], sig: str, mode: str,
                 fork_cls: str = "") -> Dict[str, Any]:
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
    }


def _ledger(session_id: str, cfg: Dict[str, Any], sig: str, mode: str,
            fork_cls: str = "", fail_open_reason: str = "") -> None:
    try:
        _dec.ledger_write(dict(_ledger_base(session_id, cfg, sig, mode,
                                            fork_cls),
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
    """ONE advisory envelope appended to the in-flight request context:
    provenance-stamped header + verdict + why_not (alternatives). Never
    rewrites model/tool content; non-binding."""
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
    banner. Bounded by RUN_CAP + FIFO pruning. Never raises."""
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
            st = _RUNS.get(session_id)
            if st is None:
                st = _RUNS[session_id] = {"run": 0, "last_n": 0, "count": 0,
                                          "consumed": []}
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
    format with trigger=midturn. Never raises; drains the accumulator."""
    try:
        with _LOCK:
            st = _RUNS.get(str(session_id or ""))
            consumed = list(st.get("consumed") or []) if st else []
            if st:
                st["consumed"] = []
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
