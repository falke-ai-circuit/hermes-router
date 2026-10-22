"""Instinct triggers — the one recognition the harness guardrails cannot make.

WHY THIS FILE GOT SMALLER
-------------------------
The first version recognised two situations: the same failure twice, and three
identical results (a "stall"). Then the harness was read properly
(/opt/hermes/agent/tool_guardrails.py) and both turned out to be already
covered, tuned, and escalating harder than any advisory can:

    repeated_exact_failure_warning  count>=2  identical arguments
    repeated_exact_failure_block
    same_tool_failure_warning       count>=3  same tool, ANY arguments
    same_tool_failure_halt          count>=8
    idempotent_no_progress_warning  identical RESULT hash   <- was "stall"
    idempotent_no_progress_block

Shipping those as a reflex would have been a second, quieter voice saying what a
warning inside the tool result already said more firmly. Observed live: the
harness emitted `repeated_exact_failure_warning; count=2` in the same result the
reflex fired on, one line above it.

WHAT IS ACTUALLY LEFT
---------------------
The harness counters are keyed per tool_name and reset each turn (see
`reset_for_turn`), so they are structurally blind to one thing: THE SAME CAUSE
ARRIVING THROUGH A DIFFERENT ROUTE. `terminal` fails with
ModuleNotFoundError: numpy, then `execute_code` fails the same way, then a test
run fails the same way — three tools, three argument hashes, no guardrail
warning at any point, and a human watching would say "that's the same problem
again".

That is the signal this module reports, and only that:

    same_cause_different_route   the same failure signature seen this session
                                 under a DIFFERENT tool.

This seam receives (session_id, tool_name, result) — no arguments — so "same
command" cannot be detected here and does not need to be: the harness owns that,
and this module defers to it.

DEFERENCE IS EXPLICIT
---------------------
If the result already carries a harness guardrail code, this module does not
fire. The harness is closer to the loop, escalates to block, and has thresholds
someone tuned on purpose. A reflex that talks over it is noise.

`stall` is gone entirely: `idempotent_no_progress_warning` is the same
observation with better instrumentation.

Deterministic on purpose — a reflex that costs tokens per tool result is a reflex
you switch off. Never raises. Ships off; canary opts in.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Optional

NOTE_HEAD = "[ROUTER INSTINCT — reflex; advisory, not a reply]"
NOTE_MAX_CHARS = 320

_ERR_RE = re.compile(
    r"(traceback \(most recent call last\)"
    r"|\b(?:fatal|panic|exception)\b"
    r"|\b(?:error|failed|failure|denied|refused|timeout|timed out)\b"
    r"|npm ERR!"
    r"|error TS\d+"
    r"|\bnon-zero exit\b"
    r"|\bexit code [1-9]|\bexit_code[\"']?\s*[:=]\s*[1-9]"
    r"|\bcommand not found\b"
    r"|\bno such file or directory\b"
    r"|ModuleNotFoundError|ImportError|PermissionError)",
    re.I)

# Every code the harness can emit, so deference never depends on remembering to
# add the next one to a list in two places.
_HARNESS_RE = re.compile(
    r"(repeated_exact_failure_warning"
    r"|repeated_exact_failure_block"
    r"|same_tool_failure_warning"
    r"|same_tool_failure_halt"
    r"|idempotent_no_progress_warning"
    r"|idempotent_no_progress_block"
    r"|\[Tool loop warning"
    r"|tool_guardrail)", re.I)

# Digits, hex, paths, line numbers and timings differ between two instances of
# the same underlying failure. Normalised so "same cause" can match across
# routes.
_NOISE_RE = re.compile(
    r"(0x[0-9a-f]+|\b[0-9a-f]{8,}\b|/[^\s:'\"]+|:\d+(?::\d+)?|\b\d+(?:\.\d+)?\b)")
_WS_RE = re.compile(r"\s+")

# Standing instructions riding the turn are not the world failing.
_SKIP_RE = re.compile(
    r"(recalled memory context|system note:|background process|"
    r"auto-generated|hermes agent documentation)", re.I)


def _norm(text: str) -> str:
    t = _WS_RE.sub(" ", str(text or "")).strip()
    return _NOISE_RE.sub("_", t)[:400]


# The failure MESSAGE, not the envelope. Two tools wrap the same underlying
# failure differently — terminal returns {"output": "...", "exit_code": 1},
# execute_code returns its own shape — so hashing the whole body gives two
# different signatures for one cause and the cross-route signal could never
# fire. What has to match is the line that names the failure.
_CAUSE_LINE_RE = re.compile(
    r"^(?:.*?)([A-Za-z_][\w.]*(?:Error|Exception|Warning)\b[^\n]{0,160}"
    r"|fatal:[^\n]{0,160}"
    r"|error(?: TS\d+)?:[^\n]{0,160}"
    r"|cannot find module[^\n]{0,120}"
    r"|command not found[^\n]{0,80}"
    r"|no such file or directory[^\n]{0,80})",
    re.I | re.M)


def _unwrap(text: str) -> str:
    """Strip a JSON envelope before looking for the cause.

    Tool results arrive as {"output": ..., "exit_code": 1, "error": null}. If the
    cause regex runs over that raw string it swallows the trailing JSON and two
    shapes of the same failure stop matching. Unwrapping first is what makes
    cross-route recognition possible at all.
    """
    raw = str(text or "")
    t = raw.strip()
    if not (t.startswith("{") and t.endswith("}")):
        return raw
    try:
        import json as _json

        obj = _json.loads(t)
    except Exception:  # noqa: BLE001
        return raw
    if not isinstance(obj, dict):
        return raw
    parts = [str(obj.get(k) or "") for k in ("error", "output", "message",
                                             "detail", "stderr")]
    parts = [p for p in parts if p]
    return "\n".join(parts) if parts else raw


def _cause_text(text: str) -> str:
    """The distinctive failure line(s), else the whole normalised body."""
    raw = _unwrap(text)
    hits = [m.group(1) for m in _CAUSE_LINE_RE.finditer(raw) if m.group(1)]
    if hits:
        # Last hits are the most specific: the outer wrapper ("error: command
        # failed") comes before the real cause ("ModuleNotFoundError: ...").
        clean = [h.split('", "')[0].split('"}')[0].rstrip('",} ') for h in hits]
        # DEDUPE. A traceback repeats the failure line (once in the traceback
        # body, once in the stderr summary), so taking the last two lines gave
        # "X | X" for one result and "X" for another and the signatures never
        # matched across routes. Observed live: identical ModuleNotFoundError
        # from terminal and execute_code produced two different signatures.
        uniq = []
        for c in clean:
            if c and c not in uniq:
                uniq.append(c)
        return _norm(" | ".join(uniq[-2:]))
    return _norm(raw)


def _sig(text: str) -> str:
    return hashlib.sha256(
        _cause_text(text).encode("utf-8", "replace")).hexdigest()[:12]


def has_error(text: str) -> bool:
    return bool(_ERR_RE.search(str(text or "")))


def harness_already_spoke(text: str) -> bool:
    """True when a harness guardrail code rides this result."""
    return bool(_HARNESS_RE.search(str(text or "")))


def recognise(store: Dict[str, Any], tool_name: str, text: str
              ) -> Optional[Dict[str, Any]]:
    """Return a recognition hit, or None.

    Fires ONLY when the same failure signature appears under a different tool
    this session — the one case the per-tool, per-turn harness counters cannot
    represent.

    `store` is the per-session dict the lane already keeps
    (decision_midturn._state(session_id)). Everything written here is namespaced
    under 'instinct' so it can never collide with the fork machinery's keys.
    """
    try:
        raw = str(text or "")
        if not raw or _SKIP_RE.search(raw):
            return None

        # Deference: the harness is closer to the loop and escalates to block.
        if harness_already_spoke(raw):
            return None

        if not has_error(raw):
            return None

        sig = _sig(raw)
        st = store.setdefault("instinct", {})
        seen: Dict[str, list] = st.setdefault("seen", {})
        tool = str(tool_name or "")

        routes = list(seen.get(sig) or [])
        if tool and tool not in routes:
            routes.append(tool)
        seen[sig] = routes
        if len(seen) > 40:  # bounded: one session must not accumulate forever
            for k in list(seen)[:-40]:
                seen.pop(k, None)

        if len(routes) < 2:
            return None

        fired = list(st.get("fired") or [])
        if sig in fired:
            return None  # one reflex per cause, not again on every further route
        fired.append(sig)
        st["fired"] = fired[-40:]

        return {"signal": "same_cause_different_route", "sig": sig,
                "tool": tool, "routes": routes}
    except Exception:  # noqa: BLE001
        return None


def reset(store: Dict[str, Any]) -> None:
    try:
        store.pop("instinct", None)
    except Exception:  # noqa: BLE001
        pass


def render_note(hit: Dict[str, Any]) -> str:
    """The reflex, in the execution register: what it recognises, what to do.

    No menu, no options, no confidence number — a recognition is not a
    probability, and dressing it as one invites the model to argue with it.
    """
    try:
        if str((hit or {}).get("signal") or "") != "same_cause_different_route":
            return ""
        routes = [str(r) for r in (hit.get("routes") or []) if r]
        via = " and ".join(routes[:3]) if len(routes) > 1 else "two tools"
        body = ("the same failure has now come back through %s. Different "
                "routes, same cause — so the command is not the problem. Fix "
                "the cause, or stop and report." % via)
        note = "%s %s" % (NOTE_HEAD, body)
        if len(note) > NOTE_MAX_CHARS:
            note = note[:NOTE_MAX_CHARS - 1].rstrip() + "…"
        return note
    except Exception:  # noqa: BLE001
        return ""


def mode_of(cfg: Optional[Dict[str, Any]]) -> str:
    """'off' | 'shadow' | 'on'. Default OFF: this changes when the lane fires,
    so it earns a canary before the fleet."""
    # Read through the CANONICAL config reader, not decision._cfg(). The same
    # trap as `decision.frame`: the historical reader never surfaces keys it
    # does not already know, so `recognition` was invisible in production while
    # a hand-built cfg in tests passed. Live symptom: config said
    # recognition: on, the lane logged mode=on for midturn, and the reflex
    # never fired.
    try:
        from .core import config_access as _ca

        _blk = _ca.sub_block_alias("reflex", "decision")
        if isinstance(_blk, dict) and _blk.get("recognition") is not None:
            cfg = {"decision": {"recognition": _blk.get("recognition")}}
    except Exception:  # noqa: BLE001
        pass
    try:
        blk = (cfg or {}).get("decision")
        if isinstance(blk, dict):
            raw = blk.get("recognition")
            # YAML 1.1 reads a bare `on` as boolean True. Coercing through
            # str() turns it into "True", which matched no mode and silently
            # disabled the feature in the very commit that introduced it —
            # observed live: config showed recognition: True, lane stayed off.
            if raw is True:
                return "on"
            if raw is False:
                return "off"
            v = str(raw or "").strip().lower()
            if v in ("on", "shadow", "off", "true", "false"):
                return {"true": "on", "false": "off"}.get(v, v)
    except Exception:  # noqa: BLE001
        pass
    return "off"
