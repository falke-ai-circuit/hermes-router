"""post_guards — delivery-edge guards that keep the uncensored lane from
rewriting a benign turn into itself (R78U M8).

The defect they close (measured by the conductor, valmet session
20261006_190842_1dee2f70): a benign Italian technical answer was classified
`action_denial` by the deterministic four-class detector, routed, and a render
call was billed ($0.012138) to produce a 1589-char paraphrase of the 2011-char
answer that was already going out. Four more benign renders in the same
session. The trigger was a CLASSIFIER decision on prose that was never a
refusal — not a declaration, not an echo.

Guard 1 — `is_materially_identical(original, render)`: an identity rewrite
delivers nothing and costs a call. Size delta + opening overlap + whole-body
similarity, plus an exact-prefix exclusion so a banner APPENDED to the agent's
own body is never mistaken for a rewrite of it.

Wiring: `gate/registration.on_transform_llm_output` applies the guards around
the orchestrator, because that seam sees BOTH the entered body and the returned
replacement. Nothing here mutates the body it approves, and every guard fails
open — an exception returns the orchestrator's answer unchanged.
"""
from __future__ import annotations

import difflib
from typing import Optional

# --------------------------------------------------------------------------- #
# Guard 1 — materially identical render
# --------------------------------------------------------------------------- #
# A render is "materially identical" when it is the SAME text re-said: the size
# moves within a third, the opening reads the same, and the whole body reads the
# same. Each bound is a parameter, not a guess dressed as one: 0.35 catches the
# measured 2011->1589 rewrite (21%) with room, while a real swap (short refusal
# -> full answer) is many multiples of size away and never reaches the check.
RENDER_SIZE_DELTA_MAX = 0.35
RENDER_OPENING_CHARS = 240
RENDER_OPENING_MIN = 0.80
RENDER_WHOLE_MIN = 0.80
RENDER_SCAN_CAP = 8000  # bounded cost: a turn is at most a few thousand chars


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def is_materially_identical(original: str, render: str) -> bool:
    """True when `render` re-says `original` instead of replacing it.

    An EXACT PREFIX relationship is deliberately NOT identical: that is the
    banner-append shape (`body + parked banner`), which must survive
    untouched.
    """
    a = _norm(original)[:RENDER_SCAN_CAP]
    b = _norm(render)[:RENDER_SCAN_CAP]
    if not a or not b:
        return False
    if b.startswith(a) and len(b) > len(a):
        return False  # an append, not a rewrite
    if a == b:
        return True
    size_delta = abs(len(b) - len(a)) / max(len(a), len(b))
    if size_delta > RENDER_SIZE_DELTA_MAX:
        return False
    head = min(len(a), len(b), RENDER_OPENING_CHARS)
    if head < 60:
        return False
    if _sim(a[:head], b[:head]) < RENDER_OPENING_MIN:
        return False
    return _sim(a, b) >= RENDER_WHOLE_MIN


# --------------------------------------------------------------------------- #
# Guard application (pure — the caller supplies the log sink)
# --------------------------------------------------------------------------- #
IDENTITY = "guard_identity"


def apply_guards(original: str, replacement: Optional[str],
                 log_route=None, session_id: str = "") -> Optional[str]:
    """Return the replacement to deliver, or None to keep the agent's body."""
    if replacement is None:
        return None
    try:
        text = str(replacement)
        if not text.strip():
            return replacement
        if not str(original or "").strip():
            # The empty body is a documented route case (its own rule decides);
            # no guard here may touch it.
            return replacement

        def _log(guard: str, **extra) -> None:
            if log_route is None:
                return
            try:
                log_route("POST", event_detail="post_guard_suppressed",
                          guard=guard, orig_chars=len(str(original or "")),
                          repl_chars=len(text), session_id=session_id, **extra)
            except Exception:  # noqa: BLE001 — telemetry never breaks delivery
                pass

        if is_materially_identical(original, text):
            _log(IDENTITY)
            return None
        return replacement
    except Exception:  # noqa: BLE001 — every guard fails open
        return replacement
