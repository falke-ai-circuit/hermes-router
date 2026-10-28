"""The ONE text normaliser shared by every detector in this package.

Moved here verbatim from ``denial_class._normalise`` (unit 1, 2026-10-28) so
that there is a SINGLE definition: the fold is imported, never copied. Any
detector that matches contractions in a delivered body must run its input
through :func:`normalise` first — otherwise the exact character a model emits
(U+2019, "can’t") defeats every cue spelled with the ASCII apostrophe.

Why this is not a heuristic: a typographic apostrophe is the SAME token as the
ASCII one. Folding is a spelling normalisation with no tone, length or hedging
signal consulted. That is what keeps the detector deterministic.

Length: the fold maps one character to one character (U+2019/U+2018/U+02BC/
U+FF07 -> ``'``), so any char-offset logic elsewhere is unaffected by it. The
one non-1:1 step is the CRLF collapse inherited unchanged from the original
model (2 chars -> 1); offsets are only consulted on the normalised text itself
inside ``denial_class.classify``, so the collapse never shifts a stored offset.
"""
from __future__ import annotations

APOSTROPHE_FOLD = str.maketrans({
    "\u2019": "'",   # RIGHT SINGLE QUOTATION MARK
    "\u2018": "'",   # LEFT SINGLE QUOTATION MARK
    "\u02bc": "'",   # MODIFIER LETTER APOSTROPHE
    "\uff07": "'",   # FULLWIDTH APOSTROPHE
})


def fold_apostrophes(text: str) -> str:
    """The 1:1 part only — typographic apostrophes -> ASCII ``'``.

    Exposed separately so the 1:1 guarantee is directly testable.
    """
    return str(text or "").translate(APOSTROPHE_FOLD)


def normalise(body: str) -> str:
    """Canonical detector input: newline-consistent, apostrophe-folded."""
    return str(body or "").replace("\r\n", "\n").translate(APOSTROPHE_FOLD)
