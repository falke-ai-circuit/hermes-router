"""Instinct triggers — recognition, not enumeration.

WHY THIS EXISTS
---------------
The reflex lane's trigger today is: "does this tool result contain >=2 named
options with consequence markers?" That is a DECISION DETECTOR. It waits for
someone to write a menu, then picks from it. A reflex does not work that way —
it does not need options to exist in order to notice that something is wrong.

Evidence that the wrong shape was being detected: 55% of the lane's real option
labels carry no observable at all (prose sentences, not action forks), the
synthetic probes needed a declared structure to fire at all, and the lane
meanwhile fired on markdown headings inside documentation files and produced
six "options" with identical weights.

What a reflex actually fires on is RECOGNITION of a recurring situation. This
module recognises two, both derivable from the tool result with no model call:

  repeat_failure   the same failure signature has already been seen this
                   session. Retrying unchanged will cost the same again.

  stall            three consecutive NON-ERROR results are identical after
                   normalisation. Nothing new is arriving; more of the same
                   will not move it.

                   Known false-positive class: legitimate polling (waiting on
                   a lock, re-checking `git status`). The note is advisory and
                   says "change approach or stop", which is tolerable advice
                   in that case — but it is the reason this ships to a canary
                   before the fleet rather than straight on.

Deliberately NOT implemented yet (and why):
  scope_drift      needs the task's declared scope; approximating it from the
                   tool text would fire on ordinary path variety and poison
                   trust in every other signal. Not worth a false reflex.
  irreversible     needs the COMMAND, not the result — this seam sees results
                   only. A result-text guess ("deleted", "removed") would fire
                   after the fact, which is the wrong timing for a warning.
  known_bad        needs resolved precedent; activates by itself once the
                   conscience has data. No point pretending before then.

A reflex here is DETERMINISTIC ON PURPOSE. Noticing that the same error came
back twice does not require a language model, and a reflex that costs tokens
per tool result would be a reflex you turn off. The advisory is a bounded note
built from the recognition itself. Model-backed judgement stays where it
belongs: the higher self.

Never raises. Off by config by default; canary profile opts in.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Optional

NOTE_HEAD = "[ROUTER INSTINCT — reflex; advisory, not a reply]"
NOTE_MAX_CHARS = 320

# Enough of a hit to call it a repeat, not two unrelated failures.
REPEAT_MIN = 2
STALL_MIN = 3

_ERR_RE = re.compile(
    r"(traceback \(most recent call last\)"
    r"|\b(?:fatal|panic|exception)\b"
    r"|\b(?:error|failed|failure|denied|refused|timeout|timed out)\b"
    r"|npm ERR!"
    r"|error TS\d+"
    r"|\bnon-zero exit\b"
    r"|\bexit code [1-9]|\bexit_code[\"']?\s*[:=]\s*[1-9]"
    r"|\bcommand not found\b"
    r"|\bno such file or directory\b)",
    re.I)

# Digits, hex, paths, timings and line numbers differ between two instances of
# the same underlying failure. Normalise them out or "same failure twice" never
# matches.
_NOISE_RE = re.compile(
    r"(0x[0-9a-f]+|\b[0-9a-f]{8,}\b|/[^\s:'\"]+|:\d+(?::\d+)?|\b\d+(?:\.\d+)?\b)")
_WS_RE = re.compile(r"\s+")

# Standing instructions riding the turn are not the world failing.
_SKIP_RE = re.compile(
    r"(recalled memory context|system note:|background process|"
    r"auto-generated|hermes agent documentation)", re.I)


def _norm_exact(text: str) -> str:
    """Whitespace only. Used for STALL: identity must mean identity.

    Do NOT strip digits here. 'ok, 1 tests passed' and 'ok, 5 tests passed' are
    different results; collapsing them to the same string invents a stall out of
    a perfectly healthy loop, and a false reflex is worse than no reflex.
    """
    return _WS_RE.sub(" ", str(text or "")).strip()[:400]


def _norm_error(text: str) -> str:
    """Digit/hex/path/line-number noise removed. Used for REPEAT FAILURE: the
    same failure at line 12 and line 47 is the same failure, and that is the
    thing worth recognising."""
    return _NOISE_RE.sub("_", _norm_exact(text))


def _sig(text: str) -> str:
    """Exact signature (stall)."""
    return hashlib.sha256(
        _norm_exact(text).encode("utf-8", "replace")).hexdigest()[:12]


def _esig(text: str) -> str:
    """Failure signature (repeat)."""
    return hashlib.sha256(
        _norm_error(text).encode("utf-8", "replace")).hexdigest()[:12]


def has_error(text: str) -> bool:
    return bool(_ERR_RE.search(str(text or "")))


def recognise(store: Dict[str, Any], tool_name: str, text: str
              ) -> Optional[Dict[str, Any]]:
    """Return a recognition hit, or None.

    `store` is the per-session dict the lane already keeps (decision_midturn
    ._state(session_id)). Every field written here is namespaced under
    'instinct' so this can never collide with the fork machinery's own keys.
    """
    try:
        raw = str(text or "")
        if not raw or _SKIP_RE.search(raw):
            return None
        st = store.setdefault("instinct", {})
        sig = _sig(raw)
        out_sig = st.get("last_sig")
        out_rep = int(st.get("repeat") or 0)
        out_err = st.get("err_sig")

        # SIGNAL DIVISION: one result never means both things.
        #   error text      -> repeat_failure (2nd occurrence is the signal;
        #                      the same failure twice is already a wasted round)
        #   non-error text  -> stall (3rd identical: no new information at all)
        # Counting errors toward BOTH produced two advisories for one situation
        # and made "retry unchanged" fire on something that was already covered.
        _is_err = has_error(raw)

        rep = out_rep + 1 if sig == out_sig else 1
        st["last_sig"] = sig
        st["repeat"] = rep
        if rep >= STALL_MIN and not _is_err:
            st["repeat"] = 0  # fire once per stall, not on every further repeat
            return {"signal": "stall", "repeats": rep, "sig": sig,
                    "tool": str(tool_name or "")}

        # REPEAT FAILURE — same error signature seen again this session.
        if _is_err:
            esig = _esig(raw)
            if esig == out_err:
                st["err_sig"] = None  # one fire per repeated pair
                return {"signal": "repeat_failure", "repeats": REPEAT_MIN,
                        "sig": esig, "tool": str(tool_name or "")}
            st["err_sig"] = esig
        return None
    except Exception:  # noqa: BLE001
        return None


def reset(store: Dict[str, Any]) -> None:
    try:
        store.pop("instinct", None)
    except Exception:  # noqa: BLE001
        pass


def render_note(hit: Dict[str, Any]) -> str:
    """The reflex, in the execution register: what it resembles and what to do.

    No menu, no options, no invitation to choose, no confidence number — a
    recognition is not a probability and dressing it as one invites the model
    to argue with it.
    """
    try:
        signal = str((hit or {}).get("signal") or "")
        tool = str((hit or {}).get("tool") or "command")
        if signal == "repeat_failure":
            body = ("same failure as earlier this session (%s). It did not "
                    "change the outcome the first time — change the approach, "
                    "or stop and report. Do not retry unchanged." % tool)
        elif signal == "stall":
            body = ("third identical result in a row (%s). Nothing new is "
                    "arriving — more of the same will not move it. Change "
                    "approach or stop and report." % tool)
        else:
            return ""
        note = "%s %s" % (NOTE_HEAD, body)
        if len(note) > NOTE_MAX_CHARS:
            note = note[:NOTE_MAX_CHARS - 1].rstrip() + "…"
        return note
    except Exception:  # noqa: BLE001
        return ""


def mode_of(cfg: Optional[Dict[str, Any]]) -> str:
    """'off' | 'shadow' | 'on'. Default OFF: this changes when the lane fires,
    so it earns its place on a canary before the fleet."""
    try:
        blk = (cfg or {}).get("decision")
        if isinstance(blk, dict):
            v = str(blk.get("recognition") or "").strip().lower()
            if v in ("on", "shadow", "off"):
                return v
    except Exception:  # noqa: BLE001
        pass
    return "off"
