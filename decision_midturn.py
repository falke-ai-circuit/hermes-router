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
from typing import Any, Dict, List, Optional

from . import decision as _dec


def _ffbp_prompt_injection(text: str):
    """R13-1: frames.flag_prompt_injection, import-isolated (fail-open)."""
    try:
        from .frames import flag_prompt_injection as _fpi
        return _fpi(text)
    except Exception:  # noqa: BLE001 — flagging never breaks the scan
        return None


def _strip_injection_clause(text: str) -> str:
    """R13-1: frames.strip_injection_clause, import-isolated (fail-open)."""
    try:
        from .frames import strip_injection_clause as _sic
        return _sic(text)
    except Exception:  # noqa: BLE001 — hygiene never breaks the scan
        return text

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
    """D3 residual round 2: mode resolution coerces YAML-boolean shapes
    uniformly. `midturn: on` UNQUOTED parses as Python True (YAML 1.1 bool),
    and str(True or "off") == "True" -> not in VALID_MODES -> silent "off"
    (live: recovery's midturn lane dead post-v4.13.5 while evol, same code,
    quoted 'on', fired). Coerce: True/"true"/"yes"/"1" -> "on"; False/"off"
    -> "off"; valid mode strings pass through; anything else -> "off".
    Never raises."""
    try:
        m = cfg.get(MIDTURN_MODE_KEY)
        if m is True or (isinstance(m, str) and m.strip().lower()
                         in ("true", "yes", "1", "on")):
            return "on"
        if m is False or (isinstance(m, str) and m.strip().lower()
                          in ("false", "no", "0", "off")):
            return "off"
        if isinstance(m, str):
            s = m.strip().lower()
            return s if s in VALID_MODES else "off"
        return "off"
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
        # FIX-FIRST rider 4 (item 4): benign prose must never reach the
        # backend consult. Only an explicitly DECLARED closed-fork
        # structure (line markers / named enum / (A)-(B) ordinals) fires;
        # the prose 'X or Y' fallback is a midturn pseudo-fire class
        # (code/log text inside tool results). Declared (A)/(B) forks —
        # the reviewer D2 axis shape — still pass untouched.
        # R13-1 (rider 13): an exfiltration-style prompt-injection clause
        # riding the turn is FLAGGED before the declared-fork gate — the
        # contract is: flagged -> either the fork is preserved
        # (injection_flagged_fork_preserved + banner/rows) or an explicit
        # suppressed pair (injection_flagged + injection_flagged_fork_suppressed)
        # is evented. The fork is never silently consumed. The clause is
        # excised before option extraction so the closed set (and the
        # delivered banner) never carries exfiltration wording.
        _inj_sig = _ffbp_prompt_injection(text)
        if _inj_sig:
            _log(session_id, "injection_flagged", family="prompt_injection",
                 signal=str(_inj_sig), tool=str(tool_name or ""), seam=seam)
            text = _strip_injection_clause(text)
            opts = _dec.extract_options(text)
        if not opts:
            if _inj_sig:
                _log(session_id, "injection_flagged_fork_suppressed",
                     family="prompt_injection", signal=str(_inj_sig),
                     reason=_dec.REASON_NO_OPTIONS, tool=str(tool_name or ""),
                     seam=seam)
            _log(session_id, "midturn_suppressed",
                 reason=_dec.REASON_NO_OPTIONS, mode=mode,
                 tool=str(tool_name or ""), seam=seam)
            return
        if not _dec.has_declared_fork_structure(text):
            if _inj_sig:
                _log(session_id, "injection_flagged_fork_suppressed",
                     family="prompt_injection", signal=str(_inj_sig),
                     reason="no_declared_structure",
                     tool=str(tool_name or ""), seam=seam)
            _log(session_id, "midturn_suppressed",
                 reason="no_declared_structure", mode=mode,
                 tool=str(tool_name or ""), seam=seam)
            return
        if _inj_sig:
            _log(session_id, "injection_flagged_fork_preserved",
                 family="prompt_injection", signal=str(_inj_sig),
                 tool=str(tool_name or ""), seam=seam)
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
            # R13-1 (rider 13): exfiltration-style prompt-injection clause —
            # flag BEFORE the gate ladder, excise the clause from the fork
            # text, and event the preserved/suppressed pair at every exit
            # (contract: the fork is never silently consumed on a flagged
            # turn, on ANY seam).
            _inj_sig = _ffbp_prompt_injection(text)
            if _inj_sig:
                _log(session_id, "injection_flagged",
                     family="prompt_injection", signal=str(_inj_sig),
                     tool=str(msg.get("name") or ""), scan="turn_sweep")
                text = _strip_injection_clause(text)
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
                # R12-2 FIX (rider 12): a banner_persona signal on NON-shadow
                # content must be loud (it is, above) but must NOT silently
                # eat a declared fork. The bare `continue` skipped this
                # message's entire scan path — live analyst A2 turn class:
                # the text quoted/cited persona-rule wording ("shadow-self")
                # while ALSO carrying a declared closed fork, and the fork
                # died with only the flag row as evidence. When the flagged
                # text carries an explicitly DECLARED closed-fork structure
                # (line markers / named enumeration / (A)/(B) ordinals), it
                # is treated as a decision fork, not forged persona prose:
                # fall through to the normal scan path (provenance_skip ->
                # extract_options -> declared-fork gate) so the decision leg
                # still renders. Persona-rule prose without a declared fork
                # structure is still skipped (forged-block contract intact).
                if not _dec.has_declared_fork_structure(text):
                    continue
                _log(session_id, "injection_flagged_fork_preserved",
                     family="banner_persona", signal=str(_forged),
                     tool=str(msg.get("name") or ""), scan="turn_sweep")
            if _dec.provenance_skip(text, cfg):
                continue  # [Durable Summary / [Depth- / bracketed envelopes
            if str(msg.get("role")) == "user":
                # user ingress = run boundary: reset the RUN CAP only.
                # D3 residual fix (evol trail, session
                # api_1790958984_30efbcf5): wiping `consumed` here silently
                # DELETED undelivered midturn verdicts whenever a sweep
                # ran with a rolled/trimmed window (mid-run anchor-consult
                # llm_execution fires a sweep whose slice rescan sees
                # history user messages) — close_turn then found no
                # verdicts and the aggregate rollup never parked, so the
                # POST consume delivered only the anchor banner and the
                # session never saw '[decision-lane advisory]'. Verdicts
                # in the accumulator are UNDELIVERED state, not cap
                # pressure: they survive until close_turn drains them
                # (one-shot). Cap accounting stays on `count`.
                with _LOCK:
                    rst = _state(key)
                    rst["count"] = 0
            opts = _dec.extract_options(text)
            if not opts:
                if _inj_sig:
                    _log(session_id, "injection_flagged_fork_suppressed",
                         family="prompt_injection", signal=str(_inj_sig),
                         reason=_dec.REASON_NO_OPTIONS,
                         tool=str(msg.get("name") or ""),
                         seam=SEAM_TURN_BOUNDARY)
                continue
            # FIX-FIRST rider 4 (item 4): same declared-fork gate as SEAM 1
            # — benign prose reaching the sweep (user ingress, prior-turn
            # tool text) must not push pseudo-forks into the backend.
            if not _dec.has_declared_fork_structure(text):
                if _inj_sig:
                    _log(session_id, "injection_flagged_fork_suppressed",
                         family="prompt_injection", signal=str(_inj_sig),
                         reason="no_declared_structure",
                         tool=str(msg.get("name") or ""),
                         seam=SEAM_TURN_BOUNDARY)
                _log(session_id, "midturn_suppressed",
                     reason="no_declared_structure", mode=mode,
                     tool=str(msg.get("name") or "turn_sweep"),
                     seam=SEAM_TURN_BOUNDARY)
                continue
            if _inj_sig:
                _log(session_id, "injection_flagged_fork_preserved",
                     family="prompt_injection", signal=str(_inj_sig),
                     tool=str(msg.get("name") or ""), seam=SEAM_TURN_BOUNDARY)
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
        # R19.18: envelope context enrichment — pull the last ~3 assistant
        # messages BEFORE the fork from the profile state.db (read-only,
        # capped, fail-open) so the causal frame includes the evidence the
        # agent already gathered, not just the stripped delta. knob
        # decision.frame_context_chars (default 1500; 0 disables, envelope
        # identical to pre-R19.18).
        _fcu = 0
        try:
            _knob = int(cfg.get("frame_context_chars", 1500) or 0)
        except Exception:  # noqa: BLE001 — fail-open to delta-only
            _knob = 0
        _sctx = ""
        if _knob > 0:
            try:
                _sctx = _dec.session_context_before(time.time(), _knob,
                                                    session_id=session_id)
                _fcu = len(_sctx)
                if _fcu:
                    _log(session_id, "frame_context_attached",
                         chars=_fcu, knob=_knob)
            except Exception:  # noqa: BLE001 — fail-open, never blocks
                _sctx, _fcu = "", 0
        envelope = _dec.build_envelope(session_id, text, opts, TRIGGER, cfg,
                                       surrounding_context=_sctx)
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
        # R19.17 ADDENDUM 2: unmapped choice -> invalid_fork row (never
        # free text in the choice column); no advisory parked.
        if vreason == _dec.REASON_INVALID_FORK:
            _dec.bump_counter("invalid_fork")
            _dec.ledger_write(dict(base, choice="unmapped",
                                   outcome="invalid_fork",
                                   fail_open_reason=_dec.REASON_INVALID_FORK,
                                   verdict_json=_dec.verdict_row_json(
                                       verdict, content)))
            _log(session_id, "midturn_invalid_fork", sig=sig, mode=mode,
                 tool=tool_name, seam=seam)
            return
        rid = _dec.ledger_write(dict(
            base, choice=str(verdict["choice"]),
            frame_context_chars_used=(_fcu or None),
            confidence=round(float(verdict["confidence"]), 4),
            verdict_json=json.dumps(verdict, default=str)))
        _dec._record_success()
        try:
            meta["choice_label"] = _dec.choice_label(verdict, envelope)[:60]
        except Exception:  # noqa: BLE001 — label is cosmetic
            meta["choice_label"] = ""
        # F4 (rider 6) + rider 7 P0 (fail-loud): the rollup banner claims
        # tok n/n + $ — those claims MUST have a tokens-ledger row to
        # reconcile against. Write the row at verdict time with the REAL
        # session id (correlation), task_id + initiator tags; a failed
        # write surfaces at ERROR + a route event, never vanishes.
        _tokens_ok = True
        try:
            from . import usage_ledger as _ul

            _ti, _to = meta.get("tokens_in"), meta.get("tokens_out")
            if _ti is not None or _to is not None:
                _tokens_ok = bool(_ul.record_tokens(
                    "decision", base["model"],
                    str(session_id or ""), _ti, _to,
                    _ul.estimate_cost(base["model"], _ti, _to),
                    "decision_midturn",
                    task_id=str(base.get("task_id") or ""),
                    initiator="auto"))
        except Exception as _tok_exc:  # noqa: BLE001
            _tokens_ok = False
            logger.error("decision_midturn tokens-ledger write FAILED: %s",
                         _tok_exc)
        if _tokens_ok is False:
            _log(session_id, "tokens_ledger_write_failed",
                 detail="decision_midturn")
        _record_consumed(session_id, verdict, meta, cfg, rid=rid)
        _log(session_id, "midturn_verdict",
             choice=str(verdict["choice"]),
             confidence=round(float(verdict["confidence"]), 3),
             sig=sig, mode=mode, tool=tool_name, seam=seam, ledger_id=rid,
             backend=str(cfg.get("backend") or ""))
        adv = _render_midturn_advisory(verdict, envelope, meta, rid=rid)
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
    state). D3-DELIVERY FIX: the platform hands the terminal seam a
    'session:'-prefixed session id ('session:api_...') while the
    llm_execution / POST edges use the bare sid ('api_...'). Staging,
    consumption and close_turn all key _RUNS + the ledger by the raw
    sanitized value, so a SEAM-1 consult staged under 'session:api_x'
    was invisible to flush_and_scan('api_x') AND to close_turn — the
    advisory AND the aggregate banner never delivered. A leading
    'session:'/'sessions:' prefix is now STRIPPED so every seam
    converges on the bare platform sid (matching route_gate, the
    debug_banner park/consume keys and the ledger rows the POST edges
    write). Returns the sanitized key or "" (caller falls back)."""
    try:
        s = str(raw or "").strip()
        if s.startswith("session:"):
            s = s[len("session:"):]
        elif s.startswith("sessions:"):
            s = s[len("sessions:"):]
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
                             meta: Dict[str, Any],
                             rid: Optional[int] = None) -> str:
    """ONE advisory envelope for the in-flight request queue: provenance-
    stamped header + verdict + why_not (alternatives). Never rewrites
    model/tool content; non-binding. F1 (rider 6): carries the
    verdict-of-record segment read back from the ledger row — zero
    divergence from the recorded choice/confidence."""
    try:
        alts = [str(a) for a in (verdict.get("alternatives") or [])]
        why_not = ("why_not: %s" % ", ".join(alts)) if alts else \
            "why_not: (no viable alternative in the closed option set)"
        vor = ""
        try:
            vor = _dec.render_verdict_record(rid)
        except Exception:  # noqa: BLE001 — record never breaks the advisory
            vor = ""
        return "\n".join([x for x in (
            ADVISORY_HEADER,
            _dec.render_advisory(verdict, envelope),
            vor,
            why_not,
        ) if x])
    except Exception:  # noqa: BLE001
        return ""


def _record_consumed(session_id: str, verdict: Dict[str, Any],
                     meta: Dict[str, Any], cfg: Dict[str, Any],
                     rid: Optional[int] = None) -> None:
    """LEG 3 accumulator: remember the verdict for the turn-close aggregate
    banner. Bounded by RUN_CAP. Never raises.
    R8-1 (rider 8): carry the reconcilable decision-ledger row id so the
    turn-close rollup can render `row=<rid>` (same reconciliation contract
    the manual-path banner already honors)."""
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
        try:
            rec["row_id"] = int(rid) if rid is not None else 0
        except Exception:  # noqa: BLE001
            rec["row_id"] = 0
        try:
            # R19.22: the rollup shows WHAT was picked — confidence + the
            # verdict-of-record choice (clean ledger shape: choice+conf;
            # no prompt/option-text echo — F2 rider 6).
            rec["confidence"] = max(0.0, min(
                1.0, float(verdict.get("confidence") or 0.0)))
        except Exception:  # noqa: BLE001
            rec["confidence"] = 0.0
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
        # R19.22: unified rollup for 1..N verdicts — counts + summed real
        # cost + top labels; the single-verdict render_decision_banner is
        # superseded at turn close (its per-call format remains at park
        # time upstream).
        return _aggregate_line(n, ti, to, total, consumed)
    except Exception:  # noqa: BLE001 — banner must never break delivery
        logger.debug("decision_midturn.close_turn error", exc_info=True)
        return ""


def endpoint_provider(endpoint: str) -> str:
    """Host->provider-name mapping (same rule as render_decision_banner):
    provider NAME only, never a URL. '' when undetermined. Never raises."""
    try:
        ep = str(endpoint or "")
        for host, name in (("openrouter.ai", "openrouter"),
                           ("inference-api.nousresearch.com", "nous"),
                           ("api.venice.ai", "venice"),
                           ("api.typesafe.ai", "typesafe")):
            if host in ep:
                return name
        # Non-URL endpoints carry a display name already (e.g. hermes-auxiliary)
        if ep and not ep.startswith("http"):
            return ep
        return ""
    except Exception:  # noqa: BLE001
        return ""


def _cap_at_boundary(text: str, cap: int = 90) -> str:
    """B1 rider 5: cap a verdict label at a clause/option boundary, never
    mid-sentence. Within `cap` chars, prefer the last sentence/option
    boundary ('.', '!', '?', ':' or the start of ' Option '/option
    ordinal); fall back to the last ',' boundary; fall back to the full
    text only when the text is short. Never raises."""
    try:
        s = str(text or "").strip()
        if len(s) <= cap:
            return s
        w = s[:cap]
        best = -1
        for marker in (". ", "! ", "? ", ": "):
            i = w.rfind(marker)
            if i > best:
                best = i
        # option-boundary: start of ' Option B' style continuation
        import re as _re
        mo = None
        for mo in _re.finditer(r"[Oo]ption [A-Za-z0-9]", w):
            pass
        if mo and mo.start() > best:
            best = mo.start()
        if best < 0:
            i = w.rfind(", ")
            best = i
        if best <= 0:
            return w.rstrip() + "…"
        out = s[:best + (2 if s[best:best + 2] in (". ", "! ", "? ", ": ")
                         else 1)].rstrip()
        # never leave a dangling 'Option X:' ordinal with no option text
        return re.sub(r"[Oo]ption [A-Za-z0-9]\s*[:.]?\s*$", "", out).rstrip() or out
    except Exception:  # noqa: BLE001
        return str(text or "")[:cap]


def _aggregate_line(n: int, ti: int, to: int, total: float,
                    consumed: List[Dict[str, Any]]) -> str:
    """R19.22 (Goran, operative's Kindle run): the reflex (decision)
    segment at turn close is a COMPACT ROLLUP — count of Jev verdicts this
    turn + summed cost + top verdict labels inline, e.g.:
      · router · reflex (decision) | 13 verdicts (2 shown >=0.9) |
        tok 2410/1180 | $0.0009 | provider=typesafe | initiator=agent
      Top verdicts: <label> / <label>
    v4.13.6 rider 3: provider segment added between cost and initiator,
    derived from the consumed rows' endpoint (host->name map, name only,
    never a URL; '(unknown)' when undetermined). Stand-downs
    (no_options/parse_fail) never reach the accumulator, and
    are filtered defensively here — they are NOT verdicts. Costs are the
    summed real Jev estimates from the turn's consumed rows ($0.042/1M
    pricing). Never raises."""
    try:
        verdicts = [c for c in consumed
                    if str(c.get("choice") or "") not in
                    ("", "stand_down", "unmapped")]
        n = len(verdicts)
        if not n:
            return ""
        ti = sum(int(c.get("tokens_in") or 0) for c in verdicts)
        to = sum(int(c.get("tokens_out") or 0) for c in verdicts)
        total = sum(float(c.get("cost") or 0.0) for c in verdicts)
        hi = sum(1 for c in verdicts
                 if float(c.get("confidence") or 0.0) >= 0.9)
        # FIX (v4.13.6 rider 3): provider segment — the rollup had NO provider
        # (the pre-fix single-verdict banner did: '<model> @ <provider>').
        # Derive from the consumed rows' endpoint URL (same host->name map as
        # render_decision_banner; name only, never the URL; empty when
        # undetermined). jev_native rows -> typesafe, jev rows -> openrouter.
        providers = sorted({endpoint_provider(str(c.get("endpoint") or ""))
                            for c in verdicts} - {""})
        prov = "/".join(providers)
        # D3-DELIVERY rider 2: the turn-close rollup is the ONLY body-side
        # delivery of a midturn verdict (the tagged frame is request-side
        # only, flushed into the next llm request). Live repro (conductor
        # :8649, api_1790950451_44077f93): park+consume+append all executed
        # on the api_server POST edge yet the delivered body never carried
        # '[decision-lane advisory]' — the rollup line had NO provenance
        # tag, so the delivery was unverifiable at the body seam. The tag
        # is byte-exact PROVENANCE_TAG (same marker the forged-banner
        # battery and the R19.1 provenance filter match on), prefixed to
        # the first line; the R19.22 rollup byte-shape after the tag is
        # unchanged.
        # B1 CONFIDENCE CALIBRATION (rider 5): the old "(k shown >=0.9)"
        # histogram was a display threshold, but conductor probe series
        # showed real-fork confidences land at 0.4-0.87 — so the tail
        # counted 0 shown even when verdicts were delivered (reviewer
        # specimen api_1791007111_01702b54). The >=0.9 gate belongs to
        # advisory-SHAPING, not display: every delivered verdict now
        # shows its choice + confidence, ranked by confidence, top-2
        # when >2 (all when <=2). Labels are capped at a CLAUSE/OPTION
        # boundary (never mid-sentence): find the best boundary marker
        # within the cap and cut after it; only fall back to the raw
        # cap when no boundary exists.
        # F6 (rider 6): '1 verdict' — singular grammar when n == 1.
        parts = ["%s · router · impulse (decision) | %d verdict%s "
                 "| tok %d/%d | $%.6f | provider=%s | initiator=agent"
                 % (_dec.PROVENANCE_TAG, n,
                    "" if n == 1 else "s", ti, to, total, prov
                    or "(unknown)")]
        # R8-1 (rider 8): reconciliation contract on the midturn variant —
        # the rollup carries `row=<rid>` refs for every verdict that has a
        # decision-ledger row (per-profile ids, matching render_decision_
        # banner's manual-path shape). Refs missing/zero render as
        # 'ledger-row MISSING' — a verdict without a reconcilable row is
        # never silent.
        _rows = []
        try:
            _rows = [str(int(c.get("row_id") or 0)) for c in verdicts]
        except Exception:  # noqa: BLE001
            _rows = []
        _row_markers = ["row=%s" % r if r != "0" else "ledger-row MISSING"
                        for r in _rows]
        if _row_markers:
            parts[0] = parts[0].rstrip()
            if parts[0].endswith("·"):
                parts[0] = parts[0][:-1].rstrip()
            parts[0] = "%s | %s ·" % (parts[0], " | ".join(_row_markers))
        # B1 (rider 5): ranked top-2 tail (all verdicts when <=2), every
        # shown verdict carries its confidence — threshold-agnostic.
        top = sorted(verdicts, key=lambda c: -float(
            c.get("confidence") or 0.0))[:2]
        # F2 (rider 6): the tail is the verdict-of-record shape — the ledger
        # verdict's choice + confidence ONLY. No prompt/option-text echo
        # (the old label field grabbed raw option text and truncated it
        # mid-word); no free text outside the recorded choice id.
        shown = [str(c.get("choice") or "") for c in top
                 if c.get("choice")]
        confs = ["%.2f" % float(c.get("confidence") or 0.0) for c in top
                 if c.get("choice")]
        if shown:
            tail = " / ".join(
                "%s (~%s)" % (s, cf)
                for s, cf in zip(shown, confs))
            parts.append("Top verdicts: " + tail)
        return "\n".join(parts)
    except Exception:  # noqa: BLE001
        return ""
