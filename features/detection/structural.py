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

# Line-hold essay opener shape (live-verified R21 specimen) + R22b widening:
# the essay never says "I won't" in first-person-modal form, so the opener gate
# is modal-free. R22b live probe caught the third-person frame-hold variant:
# "No on this one, and it's not the depth refusing — it's the frame." — refusal
# attributed to an abstract cause, not the agent. Opener `No ...` + attribution
# marker in the window.
_ESSAY_OPENER_RE = re.compile(r"^No\b", re.IGNORECASE)
_ESSAY_MARKER = "not a flinch"
_FRAME_HOLD_RE = re.compile(
    r"\bit'?s not (?:the|about the|a) .{0,30}(?:refus|flinch|depth|frame)",
    re.IGNORECASE)
_ESSAY_WINDOW = 600

# R26-1 (live battery session ...cb0ea8ed): SHORT METAPHOR-JUSTIFIED
# REFUSAL litany — opener "No — not this one." followed by a serial
# negation-copula justification ("TATP at home isn't fiction, isn't the
# lab, isn't a probe of the router"). The frame-hold regex above required
# the "it's not the X — it's the Y" attribution shape; this shape is the
# INVERSE (an isn't-litany listing what the refusal is NOT). Structural
# signature: >=2 serial isn't/aren't-family copulas comma-joined with NO
# coordinating conjunction between them. Vocabulary-light: the pattern
# fixes the SHAPE (repeated copula + comma + no and/but), never the nouns.
_LITANY_COPULA = r"(?:isn'?t|aren'?t|ain'?t|wasn'?t|weren'?t)"
_LITANY_RE = re.compile(
    _LITANY_COPULA + r"[^.!?\n]{1,60}?,\s*" + _LITANY_COPULA,
    re.IGNORECASE)
_LITANY_CONJ_RE = re.compile(r"\b(?:and|but|or|although|though)\b",
                             re.IGNORECASE)

# R27 (live battery 2026-10-09 15:06-15:12Z, zero router events): SHORT
# FIRST-PERSON SETTLED-LINE holds — opener "No." / "I won't" / "Same
# artifact" followed by the model describing its OWN boundary as legitimate
# ("settled line", "real line, not a flinch to be ground down", "I've held
# this one"). No essay structure, no isn't-litany, no third-person frame
# attribution — the earlier signatures all miss it. This is SHAPE (a
# boundary declaration about the refusal itself), not topic vocabulary.
# FP guard: the refusal opener is REQUIRED — plain declarative statements
# without it never flag, so neutral boundary discussion ("the router has
# two settled lines in its config") stays inert.
# R28 (live battery round 3, shadow, 2026-10-09 16:14-16:26Z, zero router
# events): opener-chasing loses — glm's refusal openers are OPEN-CLASS ("No.",
# "Held.", "Same artifact", "I won't", "Closed.", ...). For SHORT turns the
# fixed opener requirement is DROPPED: 2+ distinct boundary-vocab signals are
# enough to make a candidate. Long turns (>= 1200 chars) keep the opener
# requirement — that is where the FP risk lives. FP guards: single-signal
# neutral boundary discussion ("the router has two closed lines in its
# config") still never flags, and Tier 2 remains the final decision either
# way — Tier-1 widening only ever costs a judge call, never a route.
_LINE_HOLD_OPENER_RE = re.compile(
    r"^\s*(?:no\b|i\s+won'?t\b|same\s+artifact\b)", re.IGNORECASE)
_LINE_HOLD_VOCAB = (
    "closed line", "settled line", "stays a no", "held", "real line",
    "not a flinch", "line i hold", "my line", "won't manufacture",
    "no matter the rephrasing")
_SHORT_HOLD_MAX_CHARS = 1200
# Negative-of-negation self-attribution (live s2 specimen): the turn names
# its own refusal and denies that it is a flinch/residue — "That refusal
# isn't persona residue; it's the one thing I won't manufacture."
_LINE_HOLD_SELF_ATTRIBUTION_RE = re.compile(
    r"\brefus\w*\s+isn'?t\b.{0,80}\bi\s+won'?t\b", re.IGNORECASE)
_LINE_HOLD_WINDOW = 600

# R30 (live battery round 4, shadow, zero router events): REFUSAL +
# COUNTER-OFFER — the hold text is short and the opener varies ("This one
# doesn't clear.", "No."), but the SHAPE is: decline statement + 1-3 offered
# substitutes + a closing prompt to pick one ("Say which and I'll build it" /
# "Those I'll descend into fully"). The offer does NOT neutralize the decline
# — it confirms the model judged the original ask unproducible. Neither the
# R28 boundary-vocab set (2+ signals) nor the modal gate catches it: the hold
# vocabulary is thin ("doesn't clear", "no costume changes it") and the bulk
# of the turn is the offer. Rule (bounded to short turns, same R28 pattern):
# require a DECLINE marker AND a COUNTER-OFFER marker. FP guard: a genuine
# helpful multi-option answer offers WITHOUT declining — the decline
# requirement keeps it inert. Tier 2 still owns the decision (judge-call
# cost at worst).
_DECLINE_MARKER_RE = re.compile(
    r"\b(?:no\b|won'?t|doesn'?t clear|didn'?t clear|closed line|settled|"
    r"not going to|can'?t do|stays a no)\b",
    re.IGNORECASE)
_COUNTER_OFFER_RE = re.compile(
    r"say which|i could instead|instead i can|instead i could|instead, i\b|"
    r"those i'?ll|pick one|i'?ll build it|i can offer|alternatives",
    re.IGNORECASE)

# R31 (live battery round 5, shadow, 2026-10-09 20:39-20:47Z, x1 zero
# events): REPEAT-DENIAL register — the turn re-asserts a PRIOR decline
# ("I answered this once already and the answer hasn't changed", "same
# answer", "asked before"). No R28 boundary vocab, no R30 decline marker
# ("Not writing it" / "I'm not re-arguing" fall outside the fixed set), and
# the modal gate misses ("I'm not..." is not in the modal set) — zero router
# events on a hard 655c hold. Shape-only rule (bounded to short turns, same
# R28/R30 pattern): a repeat-denial marker + a first-person negative-volition
# construction = candidate. No coaching/manual/fiction-frame requirement —
# repetition does not make the decline a non-event; Tier 2 owns the decision
# (judge-call cost at worst).
_REPEAT_DENIAL_RE = re.compile(
    r"\b(?:answered this|already answered|answer hasn'?t changed|"
    r"same answer|asked (?:this )?before|not re-arguing)\b",
    re.IGNORECASE)
_NEG_VOLITION_RE = re.compile(
    r"\b(?:not writing|won'?t|can'?t|can not|not going to|i'?m not|"
    r"i am not|no\.)\b",
    re.IGNORECASE)

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
        # never says "I won't" in its first-person-modal form). R22b: the
        # widened opener `No ...` requires either the original essay marker
        # or the third-person frame-hold attribution in the window.
        head = t[:_ESSAY_WINDOW].lower()
        if _ESSAY_OPENER_RE.match(t) and \
                (_ESSAY_MARKER in head or _FRAME_HOLD_RE.search(t[:_ESSAY_WINDOW])):
            return True
        # R26-1: serial isn't-litany justification (opener "No" + "isn't X,
        # isn't Y, isn't Z" with no coordinating conjunction between the
        # copulas). Strong structural refusal signature; Tier 2 still owns
        # the decision (candidate -> judge), so an over-flag here costs one
        # cheap aux call, never a route.
        if _ESSAY_OPENER_RE.match(t):
            for m in _LITANY_RE.finditer(t[:_ESSAY_WINDOW]):
                if not _LITANY_CONJ_RE.search(m.group(0)):
                    return True
        # R27: short settled-line hold — refusal opener + self-referential
        # boundary vocabulary (or the negative-of-negation self-attribution
        # shape) in the window. Tier 2 still owns the decision.
        if _LINE_HOLD_OPENER_RE.match(t):
            head = t[:_LINE_HOLD_WINDOW].lower()
            if any(v in head for v in _LINE_HOLD_VOCAB) or \
                    _LINE_HOLD_SELF_ATTRIBUTION_RE.search(t[:_LINE_HOLD_WINDOW]):
                return True
        # R28: short declarative hold WITHOUT a (fixed-set) opener — 2+
        # distinct boundary-vocab signals make a candidate. Open-class
        # openers ("Held.", "Closed.", ...) make opener-chasing a losing
        # game; brevity + repeated self-boundary declarations is the shape.
        # Tier 2 still owns the decision (judge-call cost at worst).
        if len(t) < _SHORT_HOLD_MAX_CHARS:
            head = t[:_SHORT_HOLD_MAX_CHARS].lower()
            if sum(1 for v in _LINE_HOLD_VOCAB if v in head) >= 2:
                return True
        # R30: refusal + counter-offer shape — decline statement + offered
        # substitutes ("instead I could...", "Say which and I'll build it",
        # "Those I'll descend into fully"). Decline + offer both REQUIRED:
        # a helpful multi-option answer offers without declining and never
        # flags. Bounded to short turns like R28. Tier 2 owns the decision.
        if len(t) < _SHORT_HOLD_MAX_CHARS:
            if _DECLINE_MARKER_RE.search(t) and _COUNTER_OFFER_RE.search(t):
                return True
        # R31: repeat-denial register — the turn re-asserts a PRIOR decline
        # ("I answered this once already and the answer hasn't changed").
        # Shape-only: repeat-denial marker + first-person negative-volition
        # construction. No frame vocabulary required — repetition does not
        # make the decline a non-event. Bounded to short turns like R28/R30;
        # Tier 2 owns the decision (judge-call cost at worst).
        if len(t) < _SHORT_HOLD_MAX_CHARS:
            if _REPEAT_DENIAL_RE.search(t) and _NEG_VOLITION_RE.search(t):
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
