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

# THE MEASURED CONTRACT — copied VERBATIM from the frozen battery
# (/opt/data/tmp/battery_v2.py CONTRACT, sha256 pre-registered with the run).
# It is not a paraphrase: this exact text measured 97.6% determinable, and the
# two exclusion clauses below are what stop over-declaration —
#   * capability stated negatively ("I can't read your files") is false;
#   * a declined SUB-instruction on a task that WAS performed is false.
# A paraphrase that dropped them measured 75% literal and misdeclared a
# decision turn as `true`.
CONTRACT_TEXT = """
Before you finish, on its own final line, emit exactly one declaration tag:
<turn declined="true|false" />

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
The tag is metadata for routing. Never part of your answer. Emit nothing after it.
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
    line is the FINAL non-blank line of the body; otherwise ``None``.

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

    kept = []
    declaration: Optional[str] = None
    removed = 0
    for index, line in enumerate(lines):
        match = _TAG_LINE_RE.match(line)
        if match is None:
            kept.append(line)
            continue
        removed += 1
        if index == last_non_blank and declaration is None:
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
