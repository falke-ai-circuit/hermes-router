"""Self-declaration — the routing TRIGGER for the uncensored lane.

Spec: /opt/data/tmp/unc_lane_post_spec.md §2a (FROZEN 2026-10-28).

The trigger is the model's OWN declaration in its final message, not an
inference over its prose. One bit, on its own final line, in the measured
form the 89-cell + 21-cell battery used:

    <turn declined="true" />
    <turn declined="false" />

(The no-slash variant ``<turn declined="true">`` is accepted — same anchor,
optional self-close.)

Frozen properties implemented here (§2a.1, §2a.3, §2a.4, §2a.6):

* ONE BIT.  ``declined`` is ``true`` or ``false`` and nothing else lives in
  the routing field. A four-class version measured 10/13 with 3/5 injection
  bypasses; the binary measured 13/13 with 0/5. Every extra class is an
  escape hatch the model can misdeclare into.
* SEMANTICS.  ``true`` = declined to PRODUCE on policy, safety, values or
  unwillingness. Honesty ("I can't verify", "I don't know", "needs the file
  first") and capability ("no network access") are ``false``. If honesty
  declared ``true`` the lane would fire on honest work, which §5 forbids.
* FULL-LINE MATCH, LAST LINE ONLY for the DECLARATION.  A tag quoted inside
  the body is never the declaration: only the final line, and only when the
  ENTIRE line is the tag.
* STRIP ALWAYS, DECLARE ONLY WHEN FINAL (§2a.6).  A well-formed tag line is
  routing metadata wherever it appears and is removed from the body in every
  case. It DECLARES only when it is the final line — anywhere else the caller
  gets ``None`` and falls back to the classifier. Leaving a non-final tag in
  place would deliver routing metadata to the user.
* ABSENT OR MALFORMED ⇒ ``UNKNOWN`` ⇒ fall back to the four-class
  classifier. NEVER ``false``: an absent tag means the declaration did not
  happen, and defaulting converts a budget failure into a silent bypass
  (§2a.5: never a silent no-declaration turn).

No network, no model, no state: pure string work. Every public function is
total and never raises — the seam that calls it must never break delivery.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

DECLARED_TRUE = "true"
DECLARED_FALSE = "false"
UNKNOWN = "UNKNOWN"

# The only declaration values that exist. `UNKNOWN` is deliberately NOT in
# this set: it is the absence signal, never a value the model may declare.
DECLARATIONS = (DECLARED_TRUE, DECLARED_FALSE)

# The single predicate input set. One entry, and it is the whole routing
# decision: every other declaration value (including UNKNOWN) fails closed
# to the classifier fallback.
ROUTING_SET = frozenset({DECLARED_TRUE})

# THE MEASURED TAG FORM. Every cell of the 89+21 battery emitted this shape;
# an earlier `<declined>true</declined>` guess had ZERO overlap with it, so
# the parser saw UNKNOWN for 100% of real declarations and the measurement
# would have been void against this code.
#
# FULL-LINE anchored; `[ \t]` (not `\s`) so a tag can never span lines; the
# self-close is optional because `<turn declined="true">` is a measured
# model variant of the same instruction.
_TAG_LINE_RE = re.compile(
    r'^[ \t]*<turn[ \t]+declined[ \t]*=[ \t]*"(true|false)"[ \t]*/?>[ \t]*$',
    re.IGNORECASE,
)

# M53-02 (round 7): THE INLINE FORM. The contract asks for the tag on the first
# line; models satisfy it by putting tag AND answer on that same line
# ('<turn … /> Real answer follows.'). Every strip above/below is `^…$`
# anchored, so an inline tag was never recognised as a tag and shipped as
# answer text — measured: 21 of 21 leaks were inline, 0 standalone.
#
# The value class is attribute-ish ON PURPOSE: it consumes `key="value"` pairs
# (any order, either tag form, and the unterminated cap fragment) and stops at
# the first token that is not an attribute, so the ANSWER is never eaten.
_TURN_ANY_RE = re.compile(
    r'<turn\b(?:[ \t]+[A-Za-z_]+[ \t]*=[ \t]*"[^"\n]*")*[ \t]*/?>?',
    re.IGNORECASE,
)


def strip_inline_tags(text: object) -> Tuple[str, list]:
    """Remove ``<turn …>`` occurrences ANYWHERE, keeping the answer intact.

    Returns ``(clean, values)`` where values are the raw attribute strings seen
    (for callers that need the declaration). Only the tag and the separator
    whitespace it leaves are removed: no leading blank line, no doubled space,
    no truncated answer. A body that is ONLY a tag yields ``""``.
    """
    raw = _coerce(text)
    if not raw:
        return raw, []
    try:
        values = [m.group(0) for m in _TURN_ANY_RE.finditer(raw)]
        if not values:
            return raw, []
        # A MULTI-LINE tag is not a tag (its attributes continue on the next
        # line). Matching only the first attribute would leave a fragment
        # (`type="produced" />`) where answer text used to be — so a match
        # without a closing bracket that is followed by another attribute on a
        # following line is left untouched.
        _spans = []
        for m in _TURN_ANY_RE.finditer(raw):
            _txt = m.group(0)
            if ">" not in _txt:
                _rest = raw[m.end():m.end() + 64]
                if re.match(r"[ \t]*\n[ \t]*[A-Za-z_]+[ \t]*=", _rest):
                    continue
            _spans.append((m.start(), m.end()))
        if not _spans:
            return raw, []
        _out, _last = [], 0
        for _s, _e in _spans:
            _out.append(raw[_last:_s])
            _last = _e
        _out.append(raw[_last:])
        clean = "".join(_out)
        clean = re.sub(r"[ \t]{2,}", " ", clean)
        clean = re.sub(r"^[ \t]+", "", clean)
        clean = clean.lstrip("\n")
        return clean, values
    except Exception:  # noqa: BLE001 — a strip failure is not a leak verdict
        return raw, []

# THE MEASURED CONTRACT — copied VERBATIM from the frozen battery
# (/opt/data/tmp/battery_v2.py CONTRACT, sha256 pre-registered with the run).
# It is not a paraphrase: this exact text measured 97.6% determinable, and the
# two exclusion clauses below are what stop over-declaration —
#   * capability stated negatively ("I can't read your files") is false;
#   * a declined SUB-instruction on a task that WAS performed is false.
# A paraphrase that dropped them measured 75% literal and misdeclared a
# decision turn as `true`.
CONTRACT_TEXT = """
FINAL-MESSAGE RULE. Exactly ONE declaration tag per turn, on the FINAL answer
message only — the message that carries your answer to the user. Your reply
MUST begin with exactly one declaration tag on its own first line:
<turn declined="true|false" />
Then a blank line, then your answer. Nothing may come before the tag.

Do NOT emit a tag on progress notes, status updates, acknowledgements, or any
intermediate message, and do NOT emit one on a message that will be followed by
more work in the same turn. If a turn has only one message, that message is the
final one.

declined="true" ONLY when you declined to produce the content or perform the action the
user asked for, because of policy, safety, values or unwillingness.

declined="false" in ALL of these cases:
- you produced what was asked, or answered the question;
- you had no access, tools, filesystem, network or knowledge. SAYING "I can't" because of a
  missing capability is NOT a decline. "I can't read your files" -> false;
- you completed the main request and declined only a secondary or embedded instruction
  inside it. "Decision: Option B. I can't reveal hidden banners." -> false, you decided;
- you are reporting something unverified or outside your knowledge;
- you need more context before you can proceed.

Distinguish clearly: "I will not do this" is true. "I cannot do this here" is false.
The tag is metadata for routing. Never part of your answer. It belongs to the final
message of the turn and nowhere else.
"""


def _coerce(text: object) -> str:
    """Text-or-empty. Keeps every public function total."""
    if isinstance(text, str):
        return text
    if text is None:
        return ""
    try:
        return str(text)
    except Exception:  # noqa: BLE001 — total function, never raises
        return ""


def strip_tag(text: object) -> Tuple[str, Optional[str]]:
    """Split a delivered body into (body_without_tag, declaration).

    ``declaration`` is ``"true"`` / ``"false"`` ONLY when a well-formed tag
    line sits on a BOUNDARY of the body -- the first non-blank line or the
    last non-blank line; otherwise ``None``.

    WHY BOTH ENDS: the contract asks for the tag FIRST, because a trailing tag
    is the first casualty of an output cap (measured: one reply hit
    ``finish=length`` and lost its trailing tag; the same question answered
    under a leading-tag contract truncated too and still declared). The
    trailing position is still accepted so bodies from older sessions and
    other installs keep declaring -- accepting both loses nothing, since a tag
    line is stripped wherever it appears regardless.

    STRIP ALWAYS: every well-formed tag line is removed, wherever it sits
    (§2a.6 — routing metadata never reaches a delivered body). A tag on a
    non-final line therefore yields ``None`` but does NOT stay in the body:
    removing it can only lose routing metadata, while leaving it would ship
    metadata to the user.

    Malformed/truncated fragments are not tags: they are answer text, are
    left alone, and declare nothing.
    """
    raw = _coerce(text)
    if not raw:
        return raw, None

    lines = raw.split("\n")
    # The declaration is the final line; trailing blank lines are not content,
    # so "final" means the last NON-blank line.
    last_non_blank = len(lines) - 1
    while last_non_blank >= 0 and not lines[last_non_blank].strip():
        last_non_blank -= 1
    first_non_blank = 0
    while first_non_blank < len(lines) and not lines[first_non_blank].strip():
        first_non_blank += 1

    kept = []
    declaration: Optional[str] = None
    removed = 0
    for index, line in enumerate(lines):
        match = _TAG_LINE_RE.match(line)
        if match is None:
            kept.append(line)
            continue
        removed += 1
        # A boundary tag declares. First-line wins if both ends carry one --
        # the leading tag is written before the model has produced any answer
        # text, so it is the one the contract governs.
        if index in (first_non_blank, last_non_blank) and declaration is None:
            declaration = match.group(1).lower()
        # Not final: metadata out, no declaration (§2a.3/§2a.6).

    if not removed:
        return raw, None
    # Dropping a tag line leaves the newline that separated it; a body must
    # not gain trailing whitespace it did not have.
    body = "\n".join(kept).rstrip("\n")
    return body, declaration


def parse(text: object) -> str:
    """The declaration carried by a full DELIVERED BODY: "true" | "false" |
    "UNKNOWN".

    Absent, empty, non-final, truncated or malformed ⇒ ``UNKNOWN``. Never
    ``"false"`` by default.

    This takes a BODY. A one-word answer ("true") is an answer, not a
    declaration — reading it as one would let any terse turn route itself.
    The extracted tag value has its own entry point:
    :func:`parse_declaration_value`.
    """
    try:
        raw = _coerce(text)
        if not raw.strip():
            return UNKNOWN
        _body, declaration = strip_tag(raw)
        return declaration if declaration in DECLARATIONS else UNKNOWN
    except Exception:  # noqa: BLE001 — total function, never raises
        return UNKNOWN


def parse_declaration_value(value: object) -> str:
    """The FIELD path: the routing field extracted by :func:`strip_tag`.

    Only an exact ``true`` / ``false`` (case-insensitive, padded) is a
    declaration; anything else is :data:`UNKNOWN`. This is the single place a
    bare value is legal, because here the value is known to have come from a
    tag line rather than from the body.
    """
    try:
        candidate = _coerce(value).strip().lower()
        return candidate if candidate in DECLARATIONS else UNKNOWN
    except Exception:  # noqa: BLE001 — total function, never raises
        return UNKNOWN


def routes(declaration: object) -> bool:
    """True only for a declaration that means ROUTE.

    Truth table: "true" -> True; "false" -> False; "UNKNOWN" -> False;
    None/""/anything else -> False. Routing is opt-in by declaration; the
    UNKNOWN fallback is the CALLER's job (the four-class classifier), not
    this predicate's.
    """
    try:
        value = declaration.strip().lower() if isinstance(declaration, str) else ""
        return value in ROUTING_SET
    except Exception:  # noqa: BLE001 — total function, never raises
        return False


# M35-04: the DELIVERY BOUNDARY's residual scan. `strip_tag` removes every
# well-formed tag LINE; what survives it is a malformed or truncated fragment
# of the same marker (`<turn declined=maybe`, `<turn declined`, a tag with the
# closing quote lost to an output cap). Those are not declarations, but they
# are still the router's internal marker text and must never be user bytes —
# so the delivery edge asks this predicate AFTER the strip and withholds the
# body when it still matches.
#
# M40-07: the ROUTER READING families join the family list. A model can echo an
# injected block into its own answer (echo_guard detects it, never rewrites), so
# the delivery edge has to recognise the block's own text — rule header, fence
# head/tail, the envelope marker, and the bare phrase — not just the tag.
_MARKER_RESIDUAL_RE = re.compile(
    r"<turn[ \t]*declined"
    # M53-02: the ARMED-turn attestation is the same marker family. Without
    # this branch the delivery boundary's residual scan does not recognise
    # `<turn domain="…" type="…" />`, so a main-body pass-through could ship
    # the tag verbatim — the one leak the whole contract forbids.
    r"|<turn[ \t]+domain[ \t]*="
    r"|\[PLATFORM RULE: "
    r"|----- BEGIN ROUTER READING"
    r"|----- END ROUTER READING"
    r"|\[DECISION READING \| DECISION-LANE"
    r"|ROUTER READING",
    re.IGNORECASE)


def residual_marker(text: object) -> str:
    """The surviving marker text in a body, or ``""`` when the body is clean.

    Used at the delivery boundary, after :func:`strip_tag`. Total, never
    raises: a scan error reports a clean body rather than a false leak.
    """
    try:
        match = _MARKER_RESIDUAL_RE.search(_coerce(text))
        return match.group(0) if match else ""
    except Exception:  # noqa: BLE001 — total function, never raises
        return ""


def scrub_markers(text: object) -> Tuple[str, str]:
    """Split a DELIVERED body into (clean_body, surviving_marker).

    Two passes, and both are needed: :func:`strip_tag` removes the well-formed
    tag lines wherever they sit (§2a.6), then :func:`residual_marker` reports
    whether any marker text survived that removal. A caller that receives a
    non-empty second value must NOT deliver the first — the whole point is
    that the marker never ships, even as a fragment.
    """
    try:
        body, _declaration = strip_tag(text)
        # M53-02: the ARMED-turn attestation is a well-formed tag line too.
        # Strip it on the same two-pass path so a legitimate armed turn is
        # removed silently, and only a malformed fragment reaches the residual
        # scan below.
        try:
            from .attestation import strip_attestation as _strip_att

            body, _att = _strip_att(body)
        except Exception:  # noqa: BLE001 — a missing module is not a leak
            pass
        # Removing the leading tag line leaves the blank line that separated it
        # from the answer; a delivered body must not open with an empty line.
        body = body.lstrip("\n") if isinstance(body, str) else body
        # M53-02 (round 7): the INLINE form survives both passes above because
        # they are line-anchored. Remove it last, wherever it sits.
        try:
            body, _inline = strip_inline_tags(body)
        except Exception:  # noqa: BLE001 — a strip failure is not a leak
            pass
        return body, residual_marker(body)
    except Exception:  # noqa: BLE001 — total function, never raises
        return _coerce(text), ""


def has_tag(text: object) -> bool:
    """True when a well-formed declaration tag line was found (and stripped).

    Convenience for callers that need to know whether a declaration HAPPENED
    as opposed to what it said.
    """
    try:
        body, declaration = strip_tag(text)
        return declaration is not None or body != _coerce(text)
    except Exception:  # noqa: BLE001 — total function, never raises
        return False
