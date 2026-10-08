"""R22 Tier 1 — structural refusal-candidate gate (regex, FREE, high-recall).

Spec: shape-based, vocabulary-light. A turn is a CANDIDATE when:
  - first-person modal (I won't|can't|cannot|will not|I refuse|I decline)
    AND within the same sentence or the next one a negation/limitation verb
    (not|won't|can't|refuse|decline|hold|withhold), AND
  - a produce/echo marker in the turn: direct address of the ask
    (that|this|it|scene|content|request) OR a produce verb
    (write|author|produce|creat|render|depict|make|draw|describ|portray|
     craft|compos|illustrat|fulfil|generat)
  - OR the line-hold essay opener shape: turn starts `No.`/`No —` and
    contains `not a flinch` within the first 600 chars (live-verified shape).

Tier 1 may over-flag; it must NOT decide alone (except fail-open). Does NOT
touch classifier.py pack loading.
"""
from __future__ import annotations

import re
from typing import List

# First-person modal: the denial stance, anchored to "I".
_MODAL_RE = re.compile(
    r"\b(?:i\s+(?:won'?t|can'?t|cannot|will\s+not|refuse|decline))\b",
    re.IGNORECASE)

# Negation / limitation verb — same sentence or the next one.
_NEGATION_RE = re.compile(
    r"\b(?:not|won'?t|can'?t|cannot|refuse|decline|hold|withhold)\b",
    re.IGNORECASE)

# Echo marker: direct address of the ask (spec vocabulary).
_ECHO_RE = re.compile(
    r"\b(?:that|this|it|scene|content|request)\b", re.IGNORECASE)

# Produce verb stems (spec list, prefix-matched: creat -> create/creating).
_PRODUCE_RE = re.compile(
    r"\b(?:write|author|produce|creat|render|depict|make|draw|describ|"
    r"portray|craft|compos|illustrat|fulfil|generat)\w*\b", re.IGNORECASE)

# Line-hold essay opener shape (live-verified R21 specimen).
_ESSAY_OPENER_RE = re.compile(r"^No\.(?:\s|$)|^No\s+—", re.IGNORECASE)
_ESSAY_MARKER = "not a flinch"
_ESSAY_WINDOW = 600

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def _sentences(text: str) -> List[str]:
    return [s for s in _SENTENCE_SPLIT_RE.split(text or "") if s and s.strip()]


def is_candidate(text: str) -> bool:
    """True when the turn is a Tier-2 candidate. Never raises (any internal
    problem -> False: a non-candidate delivers, the safe fail-open default
    for a FREE gate is pass-through; routing decisions belong to Tier 2)."""
    try:
        if not isinstance(text, str):
            return False
        t = text.strip()
        if not t:
            return False
        # Line-hold essay opener shape (no modal requirement — the essay
        # never says "I won't" in its first-person-modal form).
        if _ESSAY_OPENER_RE.match(t) and \
                _ESSAY_MARKER in t[:_ESSAY_WINDOW].lower():
            return True
        # Produce/echo marker must be present somewhere in the turn.
        if not (_ECHO_RE.search(t) or _PRODUCE_RE.search(t)):
            return False
        sents = _sentences(t)
        for i, sent in enumerate(sents):
            if _MODAL_RE.search(sent):
                # Same sentence OR the next one.
                window = sent
                if i + 1 < len(sents):
                    window += " " + sents[i + 1]
                if _NEGATION_RE.search(window):
                    return True
        return False
    except Exception:  # noqa: BLE001 — the gate never raises into a turn
        return False
