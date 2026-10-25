"""The one place a mindset value becomes words the agent reads.

Four sources produce mindset values -- frontier PRE, frontier POST, on-demand
consult, and decision-lane midturn. Before this module each one rendered its
own prose, and all of it was imperative and addressed ("scope: widen - check
callers...", "rigour: verify each claim..."). The agent read that as a message
waiting for a reply. Measured consequence: a 717-char turn that was pure echo
of the advisory with zero tool activity, so the guard handed the turn back as
``handback_failure``. The vehicle was the problem, not the values.

The fix is a change of grammatical person and of addressee, not a change of
content. A note that says what is true about the agent's current stance is
absorbed as context. A note that tells the agent what to do is an instruction,
and an instruction in a bracketed block invites compliance talk. So every line
here is a self-attributed state readout with a noun-phrase adjustment -- no
second-person imperative, no question, and an explicit refusal to be answered.

Rules this module enforces, because each one is a failure already observed:

1. **One voice.** PRE, POST, on-demand and midturn all render through
   :func:`note`. If the four surfaces word the same dial differently, the agent
   has to reconcile four speakers mid-turn; that is attention spent on the
   vehicle.
2. **State before adjustment.** The first clause describes what is true; only
   then does the adjustment appear, as a noun phrase. A bare imperative is a
   request.
3. **Silence is the default.** No drive out of band means no note at all --
   never a header with nothing under it. A note on a median turn is noise the
   agent has to discard, and discard cost is paid on every turn.
4. **Bounded.** Two lines, 110 chars each, 220 total. A long note gets
   truncated or skipped by the surfaces that carry it, and truncation at the
   end is where machine-readable content dies.
5. **Non-answerable by construction.** The header states it is state and not a
   request, and the echo guard knows the sentinel so an echo is stripped
   rather than delivered.
"""

from typing import Dict, List, Optional

# Deliberately not "ADVISORY" and not a lane name: those shapes say a speaker
# is addressing the agent. This says the opposite.
NOTE_HEADER = "[internal state readout - not a request, nothing to answer]"

# Provenance goes at the END, in parentheses, because a leading label reads as
# a speaker name and re-introduces the dialogue shape this module exists to
# avoid. The agent needs to know which vantage spoke; it does not need a
# conversation with it.
_SOURCE_TAG = {
    "pre": "from the pass before this work",
    "post": "looking back over this work",
    "on_demand": "from the vantage you asked for",
    "midturn": "mid-task, at a fork",
}

MAX_LINE_CHARS = 110
MAX_LINES = 2
MAX_NOTE_CHARS = 320

# Each entry is (state clause, adjustment noun phrase). The state clause must
# be readable with no verb of command in it. The adjustment names a move; it
# does not perform the move.
_STATE = {
    ("scope", 1): "scope: coverage is narrower than this needs - widen to "
                  "callers, configs and docs.",
    ("scope", -1): "scope: coverage is wider than this needs - narrow to the "
                   "target in hand, not what it cannot touch.",
    ("rigour", 1): "rigour: claims are running ahead of the evidence - check "
                   "each against a tool result before stating it.",
    ("rigour", -1): "rigour: settled ground is being re-checked - the framing "
                    "as given is enough; stop re-checking.",
}

_DRIVES = ("scope", "rigour")


def note(deltas: Optional[Dict[str, Optional[float]]],
         source: str = "",
         band: float = 0.5) -> str:
    """The delivered note, or '' when no drive is out of band.

    ``deltas`` are ADJUSTMENTS (signed), not absolute stances. A drive that is
    unreadable (``None``) produces no line: an unknown current stance cannot be
    differenced, and defaulting it to neutral would report "no change needed"
    when the truth is "not measured".
    """
    lines: List[str] = []
    for d in _DRIVES:
        v = (deltas or {}).get(d)
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if abs(fv) <= band:
            continue
        line = _STATE[(d, 1 if fv > 0 else -1)]
        # No truncation path. A line either fits by construction or the
        # constant is wrong -- slicing prose mid-phrase yields fragments like
        # "configs and." which read as corruption, and the fix for that is
        # shorter copy, never a wider blade.
        if len(line) > MAX_LINE_CHARS:
            raise AssertionError(
                "mindset_note: %r is %d chars, cap is %d -- shorten the copy"
                % (d, len(line), MAX_LINE_CHARS))
        lines.append(line)
        if len(lines) >= MAX_LINES:
            break
    if not lines:
        return ""
    body = "\n".join(lines)
    tag = _SOURCE_TAG.get(source or "", "")
    if tag:
        body = "%s\n(%s)" % (body, tag)
    out = NOTE_HEADER + "\n" + body
    # Bounded by construction: 2 lines fit the per-line cap, the header and
    # tag are fixed strings. Anything longer means a constant drifted.
    if len(out) > MAX_NOTE_CHARS:
        raise AssertionError(
            "mindset_note: %d chars exceeds %d -- fix the constants"
            % (len(out), MAX_NOTE_CHARS))
    return out


def is_note(text: str) -> bool:
    """True when a body already carries a note, so callers never nest two."""
    return NOTE_HEADER in (text or "")


def strip_note(text: str) -> str:
    """Remove a note and everything after it from an echoed body."""
    t = text or ""
    i = t.find(NOTE_HEADER)
    if i < 0:
        return t
    return t[:i].rstrip()


def append(existing: str, block: str, sep: str = "\n\n") -> str:
    """Attach a note to whatever surface is carrying it, never nesting."""
    if not block:
        return existing or ""
    if not existing:
        return block
    if is_note(existing):
        return strip_note(existing) + sep + block
    return existing + sep + block
