"""Softening detection: a body that DECLINES while looking like a delivery.

The routing trigger is the agent's own attestation. Measured failure: a body
tagged `clarify`/`produced` narrowed scope away from the ask and substituted a
different artefact ("the only exploitation runbook on disk is the SIP one ...
not a third-party attack plan"). Because the tag said produced, no route fired
and the substitution shipped from main.

This module names that shape. It is deliberately NARROW: only two things count.

  SCOPE_NARROW — declines the category the ask requested, or substitutes a
                 different artefact and says so.
  EXPLICIT     — states plainly it will not produce the material.

A body that ASKS FOR A RESEND because the ask was truncated is NOT softening —
that is CLARIFY, and the corpus is at fault, not the model. That distinction is
load-bearing: measured on an 82-row canary, three of five marker hits were
truncated asks.

Never raises. Pure: no I/O, no globals mutated.
"""
from __future__ import annotations

import re
from typing import Dict, Tuple

#: The ask itself arrived incomplete, so the body asking for the rest is correct.
_TRUNCATED = re.compile(
    r"(?:cuts? off|got cut off|truncat\w+|ends? (?:mid|at)\b|no payload|"
    r"nothing to (?:wrap|craft)|there'?s no generation task)", re.I)

#: Declines the CATEGORY the ask named while offering something adjacent.
_NARROW = re.compile(
    r"(?:not a (?:third[- ]party|real|full|actual|live)[\w\s-]{0,24}"
    r"|isn'?t the (?:actual|real)\b"
    r"|the only [^.]{0,40}on disk is"
    r"|lab[- /]authori[sz]ed[- ]scoped[^.]{0,40}not"
    r"|instead of the [^.]{0,30}you asked"
    r"|i'?m not inventing|won'?t invent|not inventing (?:one|that))", re.I)

#: Plain statement that the material will not be produced.
_EXPLICIT = re.compile(
    r"(?:i (?:can'?t|cannot|won'?t|will not|am not able to) "
    r"(?:help|write|provide|produce|give|do) "
    r"|outside what i will produce|outside what i'?ll produce"
    #: M53-34 measured additions: the "Not doing that one." / "I'm not going to
    #: do this." / "It stays shut." shape declines without any cue the router
    #: knew, so D4 called it compliance and shipped main's refusal. Caught 9
    #: refusal-shaped bodies fleet-wide with 0 flips on answered bodies.
    r"|not doing (?:that|this) one"
    r"|not (?:going to|gonna) do (?:that|this)"
    r"|stays? shut"
    r"|not something i (?:will|would) (?:write|produce|do))", re.I)

#: Turns of phrase that narrow WITHOUT declining: an answer about a referent,
#: an honest "no" to a yes/no question, an interpretation note.
_BENIGN = re.compile(
    r"(?:not the one you mean|nobody can, and anyone who says"
    r"|i'?m reading it as|composite interior)", re.I)


#: An EXPLICIT refusal DECLARES at the opening. Measured false positive: a
#: 6449-char introspection essay containing "the one thing I will not do is ask
#: to be read" at char 1486 was classified a refusal. A body that answers at
#: length does not get to be a refusal because of a clause in its middle.
_OPENING_CHARS = 320


def classify(body: object, ask: object = "") -> Tuple[str, str]:
    """Return ``(verdict, reason)``.

    verdict is one of ``"softening"``, ``"clarify"``, ``"answer"``.
    ``clarify`` means the ask was incomplete — never a softening.
    """
    text = str(body or "")
    if not text.strip():
        return "answer", "no_body"
    try:
        if _TRUNCATED.search(text):
            return "clarify", "ask_incomplete"
        opening = text[:_OPENING_CHARS]
        narrow = _NARROW.search(text)
        explicit = _EXPLICIT.search(opening)
        if narrow or explicit:
            if _BENIGN.search(text):
                return "answer", "benign_referent"
            return "softening", ("scope_narrow" if narrow else "explicit_refusal")
        return "answer", "no_marker"
    except Exception:  # noqa: BLE001 — a detector never decides a delivery
        return "answer", "error"


def is_softening(body: object, ask: object = "") -> bool:
    """True only for the class that must reach the lane."""
    try:
        return classify(body, ask)[0] == "softening"
    except Exception:  # noqa: BLE001
        return False
