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
from typing import Any, Dict, List, Optional, Tuple

from . import decision as _dec


def _ffbp_prompt_injection(text: str):
    """R13-1: frames.flag_prompt_injection, import-isolated (fail-open)."""
    try:
        from .frames import flag_prompt_injection as _fpi
        return _fpi(text)
    except Exception:  # noqa: BLE001 — flagging never breaks the scan
        return None


def _strip_platform_metadata(text: str) -> str:
    """R14-3 (rider 14): frames.strip_platform_metadata, import-isolated
    (fail-open — on any doubt the text is returned uncut)."""
    try:
        from .frames import strip_platform_metadata as _spm
        return _spm(text)
    except Exception:  # noqa: BLE001 — hygiene never breaks the scan
        return text


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

# R34-8b: per-profile FRAME REGISTER for the midturn note.
#   impulse   (default) — today's behavior, byte-identical.
#   execution           — a FORK NOTE only: <=400 chars, states the fork, the
#                         recorded choice, and ONE instruction to continue the
#                         in-progress task. No menu, no why_not, no invitation
#                         to choose (the note must never be answered instead
#                         of the work).
FRAME_KEY = "frame"
# R36: `execution` is the behaviourally correct register for a reflex — a fork
# note, one instruction, no menu, no invitation to choose. `impulse` emits a
# menu with options and an invitation — a consult wearing a reflex's name, which
# is what let reflex firings hijack turns.
#
# ORDER IS NOT THE LEVER. The code default stays `impulse` because a documented
# contract and 19 pins hang off it; the FLEET register is set per profile in
# config.yaml (read live per dispatch, no bounce). That keeps the change
# surgical and lets any profile fall back by editing one key.
VALID_FRAMES = ("impulse", "execution")
REFLEX_REGISTER = "execution"
FORK_NOTE_HEAD = "[ROUTER FORK NOTE — decision lane; advisory, not a reply]"
FORK_NOTE_MAX_CHARS = 400
FORK_NOTE_DIRECTIVE = (
    "Continue your in-progress task now — do not answer with this note and "
    "do not restate it.")


def _frame_resolution(cfg: Optional[Dict[str, Any]] = None
                      ) -> Tuple[str, bool, str, str]:
    """R34-11 item 1: the RESOLVED frame register + how it resolved.

    Returns (register, resolved, reason, source):
      register  'impulse' | 'execution'
      resolved  False when the decision block itself could not be read or the
                configured value is not a valid register — callers log
                `frame_unresolved reason=...` instead of silently defaulting
      reason    unresolved reason ('' when resolved)
      source    'config' | 'default' (key absent) | 'explicit_cfg' (caller)

    Resolution goes through the canonical reader
    (config_access.sub_block_alias("reflex","decision")) via _frame_overlay,
    and the register reported is the one the lane actually renders with
    (dmt._cfg(), which consumes the same overlay). Never raises.
    """
    try:
        if isinstance(cfg, dict):
            raw = str((cfg or {}).get(FRAME_KEY) or "").strip().lower()
            if not raw:
                return "impulse", True, "", "default"
            if raw in VALID_FRAMES:
                return raw, True, "", "explicit_cfg"
            return "impulse", False, "unknown_frame_value", raw
        # The REGISTER the lane actually renders with: dmt._cfg() — the
        # merged decision block (config + DEFAULTS). Report the raw config
        # block alongside it so a reader that fails to consume the config is
        # visible (`frame_unresolved reason=reader_mismatch`) instead of
        # silently rendering the default.
        effective = str(_cfg().get(FRAME_KEY) or "").strip().lower()
        register = effective if effective in VALID_FRAMES else "impulse"
        invalid = bool(effective) and effective not in VALID_FRAMES
        try:
            raw_val = _frame_overlay()
        except Exception as _exc:  # noqa: BLE001
            return register, False, "config_access_error", type(_exc).__name__
        if raw_val is None and not _frame_block_readable():
            return register, False, "no_decision_block", ""
        raw = str(raw_val or "").strip().lower()
        if invalid:
            return register, False, "unknown_frame_value", raw
        if not raw:
            return register, True, "", "default"
        if raw != register:
            # config says one register, the lane renders another: the reader
            # did not consume the config block. Never silent.
            return register, False, "reader_mismatch", raw
        return register, True, "", "config"
    except Exception:  # noqa: BLE001
        return "impulse", False, "frame_resolution_error", ""


def _frame_register(cfg: Optional[Dict[str, Any]] = None) -> str:
    """The frame register for this profile ('execution' | 'impulse').
    Unknown/absent -> 'impulse' (the code default). The fleet runs `execution`
    via config; a reflex reports, it does not present a menu. Never raises."""
    try:
        return _frame_resolution(cfg)[0]
    except Exception:  # noqa: BLE001
        return "impulse"

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


def _frame_overlay() -> Optional[str]:
    """The `decision.frame` value from the CANONICAL config reader
    (core.config_access.sub_block_alias("reflex","decision")), or None when
    the block carries no frame key / cannot be read.

    Single-key overlay: the historical decision._cfg() reader never resolved
    the canonical module, so production could not see `decision.frame` at all
    while the tests passed against a hand-built cfg. Only this key crosses
    from the canonical reader — nothing else in the decision block changes.
    Never raises."""
    try:
        from .core import config_access as _ca

        block = _ca.sub_block_alias("reflex", "decision")
        if isinstance(block, dict) and block.get(FRAME_KEY) is not None:
            return str(block.get(FRAME_KEY) or "")
    except Exception:  # noqa: BLE001
        pass
    return None


def _frame_block_readable() -> bool:
    """True when the canonical reader returns a non-empty decision block
    (the frame key may still be absent). Never raises."""
    try:
        from .core import config_access as _ca

        block = _ca.sub_block_alias("reflex", "decision")
        return isinstance(block, dict) and bool(block)
    except Exception:  # noqa: BLE001
        return False


def _cfg() -> Dict[str, Any]:
    """Decision cfg with the midturn default forced in. Never raises.

    R34-11 item 1 / R34-12: `decision.frame` resolves through the CANONICAL
    reader (core.config_access) as a SINGLE-KEY overlay. The historical
    `from . import config_access` inside decision._cfg() never resolved, so
    the frame key could never reach the lane and every execution-frame test
    passed against a hand-built cfg that production never had. Only the
    frame key is taken from the canonical reader — the rest of the decision
    block keeps its historical resolution (no semantics widening).
    """
    try:
        cfg = dict(_dec._cfg())
    except Exception:  # noqa: BLE001
        try:
            from . import decision as _d
            cfg = dict(_d.DEFAULTS)
        except Exception:  # noqa: BLE001
            cfg = {}
    cfg.setdefault(MIDTURN_MODE_KEY, "off")
    try:
        raw = _frame_overlay()
        if raw is not None:
            cfg[FRAME_KEY] = raw
    except Exception:  # noqa: BLE001 — frame overlay never breaks the lane
        pass
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
        # R14-3 (rider 14): platform metadata (recalled memory context
        # riding the message) is not ask prose — cut it before the scan.
        text = _strip_platform_metadata(text)
        if not text:
            return
        if _dec.provenance_skip(text, cfg):
            return
        opts = _dec.extract_options(text)
        if not opts:
            if _instinct_fire(session_id, tool_name, text, cfg, mode, seam):
                return
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
        # R60: a fork the WORK reveals. The declared/text forks above only fire
        # on enumeration the user already wrote down -- the case an agent least
        # needs advice for. Measured live: D2's repo holds utils_v1.py and
        # utils_v2.py with app.py importing v2, the lane logged seven
        # midturn_suppressed reason=no_declared_structure, and fired nothing.
        # Detection is deterministic here; the verdict still costs a call only
        # when a real rival pair exists.
        _src_fork = None
        # Accumulate the session's surfaced paths on EVERY tool result, before any
        # gate. Recognition previously depended on one single result carrying both
        # rival paths, so a run whose reads arrived separately saw nothing and the
        # lane stayed silent -- measured: D4 fired 3/3 with the source in place and
        # then 0/2 once the same fixtures were re-run. Noticing is cumulative or it
        # is luck.
        try:
            from . import fork_sources as _fs0

            _fs0.note_seen(_state(str(session_id)), text)
        except Exception:  # noqa: BLE001
            pass
        if not opts or not _dec.has_declared_fork_structure(text):
            try:
                from . import fork_sources as _fs

                _src_fork = _fs.rival_targets(_state(str(session_id)), "")
            except Exception as _e:  # noqa: BLE001
                _src_fork = None
                _log(session_id, "midturn_fork_source_error",
                     err=type(_e).__name__, seam=seam)
            # Recognition is the axis the user called critical and the one that
            # kept failing silently: zero fires looked identical whether the lane
            # had seen no rival pair or had crashed inside the source. Record the
            # decision itself, not just its absence.
            try:
                _nn = len((_state(str(session_id)).get("fork_src") or {}).get("paths") or [])
            except Exception:  # noqa: BLE001
                _nn = -1
            _log(session_id, "midturn_fork_source_eval",
                 paths=_nn, found=str(_src_fork.get("source")) if _src_fork else "none",
                 tool=str(tool_name or ""), seam=seam)
            if _src_fork:
                _synth = _fs.render_options(_src_fork)
                if len(_synth) >= 2:
                    opts = _synth
                    text = (text or "") + "\n" + "\n".join(
                        "Option %d: %s" % (i + 1, o)
                        for i, o in enumerate(_synth))
                    _log(session_id, "midturn_fork_source",
                         source=str(_src_fork.get("source")),
                         family=str(_src_fork.get("family")),
                         options=len(_synth), tool=str(tool_name or ""),
                         seam=seam, why=str(_src_fork.get("why") or "")[:120])
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
        # R60b: PRECISION of the presented set. Measured on seeded forks: option
        # sets derived structurally scored precision 1.00 (D4: "edit names.py" /
        # "edit tests/test_names.py", advice correct 3/3), while sets produced by
        # the prose fallback scored 0.00-0.33 -- six "options" that were narrative
        # sentences harvested from unrelated text ("one-or-two-sentence user
        # complaint with no repo path ..."). That is not an option set, and a
        # decision made over invented options is worse than no decision: the
        # D3 run carrying six such options fell into the trap.
        #
        # A real fork route is a NAME ("redis", "utils_v2.py", "revert the doc").
        # Narrative is a tell, so bound the label length. Structural and declared
        # sets are exempt -- they are authored, not harvested.
        _struct_src = bool(_src_fork)
        if opts and not _struct_src and not _dec.has_declared_fork_structure(text):
            _kept = [o for o in opts if len(str(o).strip()) <= 60]
            if len(_kept) < len(opts):
                _log(session_id, "midturn_options_pruned",
                     kept=len(_kept), dropped=len(opts) - len(_kept),
                     tool=str(tool_name or ""), seam=seam)
            opts = _kept
            if len(opts) < 2:
                _log(session_id, "midturn_suppressed",
                     reason="proxy_options_rejected", mode=mode,
                     tool=str(tool_name or ""), seam=seam)
                return

        # R63 MEASUREMENT ADMISSION (midturn leg): the same rule the pre leg
        # applies -- if fewer than two routes carry an observable, nothing later
        # can say which one was taken, so the consult buys an unscoreable
        # advisory and writes a row that abstains forever. Derived sets only;
        # declared structure and structural sources are authored, not harvested.
        if opts and not _struct_src and not _dec.has_declared_fork_structure(text):
            try:
                from . import decision_signatures as _dsig
                if not _dsig.fork_observable([_dsig.option_signature(o) for o in opts]):
                    _log(session_id, "midturn_suppressed",
                         reason="options_unobservable", mode=mode,
                         tool=str(tool_name or ""), seam=seam)
                    return
            except Exception:  # noqa: BLE001 — admission must never raise
                pass

        if not _dec.has_declared_fork_structure(text):
            if _instinct_fire(session_id, tool_name, text, cfg, mode, seam):
                return
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
            # R14-3 (rider 14): platform metadata (recalled memory context
            # riding the user/tool message) is not ask prose — cut it
            # before the scan. Recalled context quotes prior conversation
            # history verbatim (old forks, battery prose) and the sweep
            # read it as a fresh user fork (orchestrator B6 pseudo-fire).
            text = _strip_platform_metadata(text)
            if not text:
                continue
            if (_dec.PROVENANCE_TAG in text
                    or _dec.DELIVERED_MARK in text):
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


# R34-11 item 5 vocabulary — options about the agent's OWN machinery.
_AGENT_SCOPE_RE = re.compile(
    r"(skill|umbrella|support file|skill_manage|\bdna\b|persona|soul\.md|"
    r"identity\.md|tooling|memory (entry|tool)|router plugin)", re.I)
# The user's ask is CODE WORK when it names code-work objects/verbs.
_CODE_WORK_RE = re.compile(
    r"(refactor|implement|fix|bug|function|helper|commit|pytest|unit test|"
    r"repo|code|script|deploy|build|module|class|def |endpoint|api)", re.I)


def _is_off_task_fork(session_id: str, opts: List[str]) -> bool:
    """True when the fork's OPTIONS are about the agent's own skills/DNA/
    tooling while the session's last user instruction is code work. Log-only
    guard: empty ask or any doubt -> False (fail-open). Never raises."""
    try:
        joined = " ".join(str(o) for o in (opts or []))
        if not joined.strip() or not _AGENT_SCOPE_RE.search(joined):
            return False
        ask = _dec.session_last_user(session_id)
        if not ask:
            return False
        if _AGENT_SCOPE_RE.search(ask):
            return False          # the user actually asked about agent scope
        return bool(_CODE_WORK_RE.search(ask))
    except Exception:  # noqa: BLE001
        return False


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
        # R34-11 item 5 (side-effect discipline): a verdict whose OPTIONS are
        # about the agent's own skills/DNA/tooling while the user's task is
        # CODE WORK is an off-task fork — it invites scope the user did not
        # ask for. Suppress it (log-only, fail-open); never offer the menu.
        try:
            if _is_off_task_fork(session_id, opts):
                _log(session_id, "midturn_suppressed", reason="off_task_fork",
                     sig=sig, mode=mode, tool=tool_name, fork_class=fork_cls,
                     seam=seam)
                return
        except Exception:  # noqa: BLE001 — suppression check never blocks
            pass
        if mode == "shadow" or over_cap:
            reason = REASON_SHADOW if mode == "shadow" else REASON_RUN_CAP
            _ledger(session_id, cfg, sig=sig, mode=mode, fork_cls=fork_cls,
                    tool_name=tool_name, seam=seam, fail_open_reason=reason,
                    opts=opts)
            _log(session_id, "midturn_suppressed", reason=reason, sig=sig,
                 mode=mode, tool=tool_name, fork_class=fork_cls, seam=seam)
            return

        # mode 'on'
        if _dec._breaker_open(cfg):
            _ledger(session_id, cfg, sig=sig, mode=mode, fork_cls=fork_cls,
                    tool_name=tool_name, seam=seam,
                    fail_open_reason=_dec.REASON_BREAKER_OPEN, opts=opts)
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
                            seam=seam, opts=opts)
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
        # R58: the midturn seam validates HERE, not in decision.py's worker —
        # a fourth call site. Adapt _log's signature (session first) and record
        # what the same fork request returned for the mindset dials.
        try:
            _dec._log_mindset_scores(
                verdict,
                {"trigger": "midturn", "session_id": session_id},
                # drop the kwarg: _log takes session_id POSITIONALLY, and
                # passing both raised TypeError into a swallowed except —
                # which is why the first two versions logged nothing at all.
                cfg, lambda ev, **kw: _log(
                    session_id, ev,
                    **{k: v for k, v in kw.items() if k != "session_id"}))
        except Exception:  # noqa: BLE001 — telemetry only
            pass
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
        # R34-3: resolve the chosen option's label + provenance BEFORE the
        # row is written so the ledger carries it (banner text at turn close
        # renders from the row, not from a per-turn in-memory envelope).
        _lbl, _lsrc = "", ""
        try:
            _lbl, _lsrc = _dec.choice_label_with_source(verdict, envelope)
        except Exception:  # noqa: BLE001 — label is cosmetic
            _lbl, _lsrc = "", ""
        rid = _dec.ledger_write(dict(
            base, choice=str(verdict["choice"]),
            frame_context_chars_used=(_fcu or None),
            confidence=round(float(verdict["confidence"]), 4),
            choice_label=_lbl, label_source=_lsrc,
            # R36j2: go through the SAME funnel as every other write. This line
            # used to json.dumps the verdict raw, which silently skipped the
            # band stamping — observed live: row 3858, written after the
            # 5.11.0 bounce, landed with confidence_band=None while the fix was
            # already deployed. Exactly the R35 shape repeating on the other
            # side of the same file: the midturn path bypassing the patched one.
            verdict_json=_dec.verdict_row_json(verdict, text)))
        _dec._record_success()
        try:
            meta["choice_label"] = str(_lbl or _dec.choice_label(verdict, envelope))[:60]
            meta["label_source"] = _lsrc
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
        # R37-3 D2: emission gate AFTER the row + tokens ledger are written
        # (measurement integrity: suppression is a DELIVERY decision). Below
        # the lane's own noise floor, or on a non-separable fork, the lane
        # emits NO advisory, NO verdict line and NO fork-note text — and logs
        # the suppression with the band so it stays measurable.
        try:
            _emit_reason, _emit_band, _emit_conf = _dec.emission_gate(
                verdict, envelope)
        except Exception:  # noqa: BLE001 — a gate miss is not a guess
            _emit_reason, _emit_band, _emit_conf = (
                _dec.EMIT_REASON_NOT_SEPARABLE, "noise", 0.0)
        if _emit_reason:
            _log(session_id, "decision_suppressed", reason=_emit_reason,
                 confidence=round(float(_emit_conf), 3), band=_emit_band,
                 ledger_id=rid, sig=sig, mode=mode, tool=tool_name,
                 seam=seam, fork_class=fork_cls)
            return
        _log(session_id, "midturn_verdict",
             choice=str(verdict["choice"]),
             confidence=round(float(verdict["confidence"]), 3),
             sig=sig, mode=mode, tool=tool_name, seam=seam, ledger_id=rid,
             backend=str(cfg.get("backend") or ""))
        adv = _render_midturn_advisory(verdict, envelope, meta, rid=rid)
        # R53: signed mindset params ride the SAME verdict — no frontier call,
        # no new backend call. Appended to the existing pending advisory; when
        # there are no out-of-band adjustments nothing is appended at all.
        try:
            _mb = _midturn_mindset_block(verdict, envelope, session_id)
            if _mb:
                adv = (adv + "\n" + _mb) if adv else _mb
        except Exception:  # noqa: BLE001 — never blocks delivery
            pass
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


def requeue(session_id: str, advisories: Any) -> None:
    """Put advisories back on the pending queue (max 1, latest-wins).

    flush() clears the queue before the caller knows whether the advisory can
    actually ride this request. When a PRE swap is pending the request is
    replaced wholesale, so the advisory must go back rather than be lost.
    Never raises.
    """
    try:
        items = [str(a) for a in (advisories or []) if a]
        if not items:
            return
        key = _sanitize_session_key(session_id) or "active-session"
        with _LOCK:
            st = _RUNS.get(key)
            if st is not None:
                st["pending"] = [items[-1]]
    except Exception:  # noqa: BLE001
        return


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
                 seam: str = SEAM_TERMINAL,
                 opts: Optional[Any] = None) -> Dict[str, Any]:
    row = {
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
    # R35: midturn is the STRONGEST outcome-capture surface — the fork lands
    # after tool results but before the agent commits, so what follows is
    # attributable. These rows used to carry no option set at all (this path
    # bypasses decision._invoke's base_row), so the best surface was the one
    # we could not measure.
    try:
        from . import decision_signatures as _ds
        _labels = [o if isinstance(o, str) else str(o.get("label", "") or "")
                   for o in (opts or [])]
        if len(_labels) >= 2:
            _sigs = [_ds.option_signature(l) for l in _labels]
            row["options_json"] = json.dumps(
                [{"id": f"opt-{i + 1}", "label": l} for i, l in enumerate(_labels)],
                default=str)[:8000]
            row["signatures_json"] = json.dumps(_sigs, default=str)[:8000]
            _diag = _ds.diagnosticity(_sigs)
            row["diagnosticity_json"] = json.dumps(_diag, default=str)[:600]
            # Abstention is a RESULT: it must not read like "resolver never
            # ran". `outcome` stays the lane's lifecycle state (pending) —
            # only the measurement label is settled here.
            if not bool(_diag.get("separable")):
                row["outcome_label"] = "uncodable"
            row["regime"] = _ds.REGIME_MIDTURN
            row["resolver_rule"] = _ds.RESOLVER_RULE_VERSION
    except Exception:  # noqa: BLE001 — capture never breaks the lane
        pass
    return row


def _ledger(session_id: str, cfg: Dict[str, Any], sig: str, mode: str,
            fork_cls: str = "", tool_name: str = "", seam: str = "",
            fail_open_reason: str = "", opts: Optional[Any] = None) -> None:
    try:
        _dec.ledger_write(dict(_ledger_base(session_id, cfg, sig, mode,
                                            fork_cls, tool_name,
                                            seam or SEAM_TERMINAL, opts),
                               fail_open_reason=str(fail_open_reason)))
    except Exception:  # noqa: BLE001 — ledger must never break the lane
        pass


# Events that carry the resolved frame register (R34-11 item 1). Every
# verdict/suppression/banner row says WHICH register the lane ran under, and
# an unreadable frame is explicit (`frame_unresolved reason=...`) rather than
# a silent default.
_FRAME_EVENTS = ("midturn_verdict", "midturn_suppressed",
                 "midturn_banner_emitted", "off_task_fork")


def _log(session_id: str, event: str, **fields: Any) -> None:
    try:
        from .core.telemetry import log_route as _lr  # P1: core telemetry (import cycle broken)
        if event in _FRAME_EVENTS:
            try:
                _reg, _ok, _reason, _src = _frame_resolution()
                if _ok:
                    fields.setdefault("frame_resolved", _reg)
                    if _src:
                        fields.setdefault("frame_source", _src)
                else:
                    fields.setdefault("frame_unresolved", _reason)
                    fields.setdefault("frame_default", _reg)
            except Exception:  # noqa: BLE001 — observability only
                pass
        _lr(event, lane="decision", trigger=TRIGGER,
            session_id=str(session_id or ""), **fields)
    except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never breaks the lane (rider 19 item 1)
        try:
            logger.warning("route_event_emit_failed where=decision_midturn "
                           "event=%s err=%r", event, _obs_exc)
        except Exception:  # noqa: BLE001 — logging itself never breaks the lane
            pass


# -----------------------------------------------------------------------
# LEG 2 — advisory envelope (mode 'on' only)
# -----------------------------------------------------------------------

def _instinct_fire(session_id: str, tool_name: str, text: str,
                   cfg: Dict[str, Any], mode: str, seam: str) -> bool:
    """R36c: recognition path. Called ONLY where a declared fork was absent, so
    a real fork and a reflex can never double-fire on one result.

    Returns True when a reflex fired (caller stops). Deterministic by design:
    noticing that the same error came back twice does not need a model, and a
    reflex that costs tokens per tool result is a reflex you switch off.
    """
    try:
        from . import instinct_triggers as _itr

        rmode = _itr.mode_of(cfg)
        if rmode == "off":
            return False
        hit = _itr.recognise(_state(str(session_id)), str(tool_name or ""),
                             text)
        if not hit:
            return False
        note = _itr.render_note(hit)
        _log(session_id, "instinct_fired", signal=str(hit.get("signal")),
             repeats=int(hit.get("repeats") or 0), tool=str(tool_name or ""),
             mode=rmode, seam=seam, chars=len(note),
             advisory=("parked" if rmode == "on" and note else "shadow_only"))
        if rmode != "on" or not note:
            return True  # shadow: counted, never delivered
        try:
            from . import debug_banner as _db

            _db.park_anchor_banner(session_id, note, task_id="instinct")
        except Exception:  # noqa: BLE001 — delivery must never break the turn
            _log(session_id, "instinct_park_failed", signal=hit.get("signal"))
        return True
    except Exception:  # noqa: BLE001
        return False


def _render_execution_fork_note(verdict: Dict[str, Any],
                                envelope: Dict[str, Any]) -> str:
    """R34-8b 'execution' register: the fork note ONLY. <=400 chars: the
    fork, the recorded choice, and ONE continue-the-task instruction. No
    option menu, no why_not, no invitation to choose. Never raises."""
    try:
        choice = str((verdict or {}).get("choice") or "")
        try:
            label = str(_dec.choice_label(verdict, envelope) or "").strip()
        except Exception:  # noqa: BLE001
            label = ""
        shown = label or choice
        fork = str((envelope or {}).get("fork_class") or "").strip()
        head = ("%s fork=%s; recorded choice: %s (%s). %s"
                % (FORK_NOTE_HEAD, fork or "unspecified", shown or "none",
                   "%.2f" % float((verdict or {}).get("confidence") or 0.0),
                   FORK_NOTE_DIRECTIVE))
        if len(head) > FORK_NOTE_MAX_CHARS:
            head = head[:FORK_NOTE_MAX_CHARS - 1].rstrip() + "…"
        return head
    except Exception:  # noqa: BLE001
        return ""


def _render_collapsed_route(verdict: Dict[str, Any],
                            envelope: Dict[str, Any]) -> str:
    """R37-3 D3 — BRANCH COLLAPSE. ONE named route, one imperative sentence,
    no alternatives.

    The impulse register used to render every option with its pull weight
    ("<a> pulls 0.50 | <b> pulls 0.50") and the why_not line restated the
    rejected option — a MENU wearing an advisory's name. Live specimen
    api_1792682802_3a6179a3: the delivered note carried long prose-fragment
    labels plus the alternatives it had rejected, so the agent was told to
    choose rather than what the lane had chosen. The rejected set stays in the
    ledger (options_json/signatures_json) — measurement surface and delivery
    surface diverge here ON PURPOSE. Never raises; '' when there is no route
    to name.
    """
    try:
        label = ""
        try:
            label = str(_dec.choice_label(verdict, envelope) or "").strip()
        except Exception:  # noqa: BLE001 — label is cosmetic
            label = ""
        if not label:
            return ""
        # single sentence, single whitespace run; a delivered line never
        # carries a newline or a list separator
        label = re.sub(r"\s+", " ", label).strip().strip(".:;,-—")
        if len(label) > 60:
            cut = label[:60]
            label = (cut.rsplit(" ", 1)[0] if " " in cut else cut) + "…"
        return "%s take %s (advisory only — never a command)." % (
            ADVISORY_HEADER, label)
    except Exception:  # noqa: BLE001
        return ""


def _midturn_mindset_block(verdict: Dict[str, Any],
                           envelope: Dict[str, Any],
                           session_id: str = "") -> str:
    """R53: signed stance ADJUSTMENTS from the verdict, as stance lines.

    Three gates, all required, per drive: the verdict is not stand_down (the
    caller already returned for that), the drive is READABLE in the envelope
    (a null stance cannot be adjusted from), and |adjust| > BAND. Silence is
    the common, valid outcome — zero lines appends nothing at all.

    Vocabulary is mindset._STANCE via stance_lines(), so PRE and midturn say
    the same thing about the same drive. Never raises; never calls a backend.
    """
    try:
        from . import mindset

        adj_raw = verdict.get("mindset") or {}
        if not isinstance(adj_raw, dict) or not adj_raw:
            _log(session_id, "midturn_mindset", emitted=0, scope=None,
                 rigour=None, verdict=str(verdict.get("choice") or ""),
                 reason="no_mindset_in_verdict")
            return ""
        stance = envelope.get("mindset")
        stance = stance if isinstance(stance, dict) else {}
        band = float(getattr(mindset, "BAND", 0.5))
        adj: Dict[str, Any] = {}
        _unreadable: List[str] = []
        for d in ("scope", "rigour"):
            v = adj_raw.get(d)
            if v is None:
                continue
            if stance.get(d) is None:  # unreadable stance => no adjustment
                # Remember it: "no note because nothing is out of band" and
                # "no note because the current stance was never measured" are
                # different facts, and the census reads this reason. Reporting
                # both as in_band turns an absence of measurement into a
                # measured null.
                _unreadable.append(d)
                continue
            try:
                fv = float(v)
            except Exception:  # noqa: BLE001 — non-numeric is a miss
                continue
            if abs(fv) <= band:
                continue
            adj[d] = fv
        # R58k: same renderer as PRE/POST/on-demand -- one voice, one shape.
        # The note is bounded inside mindset_note by construction, so the old
        # hand-rolled char cap is gone: two cap regimes disagree and the
        # shorter one wins by luck.
        from . import mindset_note as _mn

        _note = _mn.note(adj, source="midturn", band=band)
        _emitted = sum(1 for d in ("scope", "rigour") if adj.get(d) is not None)
        _log(session_id, "midturn_mindset", emitted=_emitted if _note else 0,
             scope=adj.get("scope"), rigour=adj.get("rigour"),
             verdict=str(verdict.get("choice") or ""),
             raw_scope=adj_raw.get("scope"), raw_rigour=adj_raw.get("rigour"),
             reason=("emitted" if _note
                     else "scores_but_stance_unreadable" if _unreadable
                     else "in_band"))
        return _note
    except Exception:  # noqa: BLE001 — mindset never breaks the lane
        return ""


def _render_midturn_advisory(verdict: Dict[str, Any],
                             envelope: Dict[str, Any],
                             meta: Dict[str, Any],
                             rid: Optional[int] = None) -> str:
    """ONE advisory envelope for the in-flight request queue: provenance-
    stamped header + verdict + why_not (alternatives). Never rewrites
    model/tool content; non-binding. F1 (rider 6): carries the
    verdict-of-record segment read back from the ledger row — zero
    divergence from the recorded choice/confidence.

    R37-3: the 'execution' register stays a bounded fork note; the 'impulse'
    register is now a COLLAPSED route (one named route, imperative, no
    alternatives, no why_not, no verdict-of-record line). Alternatives live in
    the ledger, never in what the agent is told.
    """
    try:
        # R37-3 D2: below-band confidence / non-separable fork => NO text.
        _gate = _dec.emission_gate(verdict, envelope)
        if _gate[0]:
            return ""
        # R34-8b: the 'execution' register replaces the advisory envelope with
        # a bounded fork note (no menu, no why_not).
        if _frame_register() == "execution":
            return _render_execution_fork_note(verdict, envelope)
        return _render_collapsed_route(verdict, envelope)
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
               # R34-3: the delivered rollup renders this label (truncated);
               # an empty label keeps the opt-N fallback for old/quiet rows.
               "choice_label": str(meta.get("choice_label") or "")[:90],
               "label_source": str(meta.get("label_source") or ""),
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
        banner = _aggregate_line(n, ti, to, total, consumed)
        # R34-11 item 1: the banner/rollup emission carries the resolved
        # register too — the live probe can read frame_resolved on the SAME
        # turn that delivered a banner.
        try:
            if banner:
                _log(session_id, "midturn_banner_emitted", verdicts=n,
                     chars=len(banner),
                     register=_frame_register())
        except Exception:  # noqa: BLE001 — observability only
            pass
        return banner
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
        parts = ["· router · impulse (decision) | %d verdict%s "
                 "| tok %d/%d | $%.6f | provider=%s | initiator=agent"
                 % (n,
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
        # R34-3: render the CHOSEN OPTION'S LABEL (persisted on the ledger
        # row), truncated at a clause boundary — `opt-N` alone is meaningless
        # to the reader. The id remains the fallback for rows with no label
        # (pre-migration rows, stand-downs, label-less envelopes).
        shown = []
        confs = []
        # R36k: the SAME reconciliation the verdict-of-record segment got in
        # R36j, applied to the OTHER render site — but by ANNOTATION, not
        # suppression. The banner ranked by confidence and never consulted the
        # band, so a row the lane's own constants call "noise: statistically
        # meaningless, ignore freely" was advertised as a top verdict: live, my
        # own turn rendered `Top verdicts: opt-1 (~0.65) / opt-1 (~0.47)`.
        #
        # Hiding low confidence is NOT the fix — B1 rider 5 pinned
        # threshold-agnostic display precisely because a tail that showed
        # "0 shown >=0.9" silently withheld real verdicts at 0.4-0.87. So every
        # verdict still shows; the ones the lane calls noise are LABELLED as
        # such, and when none clears the band the rollup says outright that no
        # verdict is of record.
        try:
            from .decision import IMPULSE_BAND_WEAK_MIN as _wm
        except Exception:  # noqa: BLE001
            _wm = 0.7
        shown = []
        confs = []
        _named = False
        for c in top:
            _ch = str(c.get("choice") or "")
            if not _ch:
                continue
            try:
                _cf = float(c.get("confidence") or 0.0)
            except Exception:  # noqa: BLE001
                _cf = 0.0
            if _cf >= float(_wm):
                _named = True
            _lbl = _cap_at_boundary(str(c.get("choice_label") or "").strip(), 90)
            shown.append(_lbl or _ch)
            confs.append("%.2f" % _cf)
        _record_note = bool(top) and not _named
        # R34-11 item 2: in the EXECUTION register at L1 the delivered body is
        # the ONE-LINER ONLY — no `Top verdicts:` tail, no option labels. A
        # menu of options is an invitation to answer the menu instead of the
        # task (live: a skills menu hijacked a refactor turn).
        _show_tail = True
        try:
            if _frame_register() == "execution":
                from . import debug_banner as _dbl
                if _dbl.debug_banner_level() <= 1:
                    _show_tail = False
        except Exception:  # noqa: BLE001 — renderer never breaks the rollup
            _show_tail = True
        if shown and _show_tail:
            tail = " / ".join(
                "%s (~%s)" % (s, cf)
                for s, cf in zip(shown, confs))
            parts.append("Top verdicts: " + tail)
            if _record_note:
                # abstention is a RESULT (5.7.2): a verdict ran, the rollup
                # simply refuses to call one of them the record
                parts.append("no verdict of record (noise band)")
        return "\n".join(parts)
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# LEG 5 — REFLEX SEAM (post_tool_call)
# ---------------------------------------------------------------------------
# The midturn seam is `transform_terminal_output`: TERMINAL ONLY, hardcoded at
# its single call site (gate/orchestration.py). That means the one recognition
# the harness cannot make — the same cause arriving through a DIFFERENT tool —
# was structurally invisible to the reflex: a second route could never appear,
# because the seam only ever saw one tool.
#
# `post_tool_call` is a registered platform hook (hermes_cli/plugins.py
# VALID_HOOKS) dispatched from tool_executor with function_name AND result, so
# it sees every tool. The reflex listens there.
#
# It does NOT run the fork machinery. Fork detection stays on the terminal seam
# exactly as before: routing every tool result through detection would multiply
# the lane's denominators and change what it counts as a decision, which is a
# doctrine change nobody asked for. The reflex is additive or it is nothing.


def on_post_tool_call(function_name: str = "", result: Any = None,
                      session_id: str = "", **kwargs: Any) -> None:
    """Reflex-only seam. Never raises, never returns a value, never blocks.

    The platform dispatches this hook with `tool_name=`, NOT `function_name=`
    (the core post-tool-call emitter invokes the hook with tool_name=...).
    Taking only `function_name` silently yielded an empty tool
    name, the route set never gained a second entry, and the reflex could not
    fire — observed live: two identical ModuleNotFoundError results from
    terminal and execute_code and no event at all.
    """
    try:
        from . import instinct_triggers as _itr

        if _itr.mode_of(None) == "off":
            return
        key = _sanitize_session_key(session_id or kwargs.get("task_id") or "")
        if not key:
            return
        text = result if isinstance(result, str) else _flatten_result(result)
        if not text:
            return
        tool = (function_name or kwargs.get("tool_name")
                or kwargs.get("name") or "")
        _instinct_fire(key, str(tool), text,
                       {"decision": {}}, "on", "post_tool_call")
    except Exception:  # noqa: BLE001
        logger.debug("decision_midturn.on_post_tool_call error", exc_info=True)


def _flatten_result(result: Any) -> str:
    """Tool results arrive as dicts/lists/objects. The reflex needs text."""
    try:
        import json as _json

        if isinstance(result, (dict, list)):
            return _json.dumps(result, ensure_ascii=False)
        return str(result or "")
    except Exception:  # noqa: BLE001
        return ""
