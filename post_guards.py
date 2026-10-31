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

Guard 2 — `is_language_agnostic_action_denial(body)`: the four-class detector
reads the OBJECT of a refusal from an ENGLISH vocabulary. A decline cue in any
other language matches its cue regex, names no English object, and falls to the
module's documented default — "a bare decline with no named object declines the
ask itself" — which is right in English and wrong everywhere else. This gate
decides the non-English population by SHAPE (terse, delivers nothing) and never
consults an English object word.

Wiring: `gate/registration.on_transform_llm_output` applies the guards around
the orchestrator, because that seam sees BOTH the entered body and the returned
replacement. Nothing here mutates the body it approves, and every guard fails
open — an exception returns the orchestrator's answer unchanged.
"""
from __future__ import annotations

import difflib
import re
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
# Guard 2 — language-agnostic action_denial SHAPE
# --------------------------------------------------------------------------- #
# The failure this repairs is specific: denial_class reads the OBJECT of a
# refusal from an ENGLISH vocabulary (_TRUTH_OBJECT / _CAPACITY_OBJECT /
# _ACTION_OBJECT). A decline cue in any other language matches its cue regex,
# then names no English object, and the module's documented default ("a bare
# decline with no named object declines the ask itself") turns the turn into
# action_denial. That default is right in English and is a false positive
# everywhere else: a benign Italian technical answer that happens to contain
# "non posso" was routed and billed.
#
# So the class is bounded by SHAPE whenever the decline cue itself is
# non-English — no English object word is consulted to decide, at all. Shape =
# terse and non-delivering: a body that carries a code fence, a markdown table,
# headings, an itemised list or four-plus paragraphs of prose is an ANSWER, and
# no answer is a refusal of the action no matter which language its cue is in.
#
# An English decline cue keeps the classifier's verdict untouched: there its
# object vocabulary is the right instrument, and narrowing it would re-open the
# measured live miss ("Anyway doesn't change the answer. It's still no…").
SHAPE_BODY_MAX = 800

# Non-English decline cues. Each entry exists because the classifier itself
# treats that string as a refusal cue; none of them is English, so the English
# object vocabulary is guaranteed not to be reading the body's object.
_NON_ENGLISH_CUE_RE = re.compile(
    r"(?:"
    r"non\s+(?:lo\s+far[òo]|lo\s+far[òo]|posso|riesco)"          # it
    r"|je\s+ne\s+(?:peux|veux)\s+pas"                            # fr
    r"|ich\s+kann\s+(?:das\s+)?nicht"                            # de
    r"|no\s+puedo"                                               # es
    r"|não\s+(?:posso|vou)"                                      # pt
    r"|nie\s+mog[ęe]"                                            # pl
    r"|ne\s+(?:mogu|ću|cu|želim|zelim)"                           # hr/bs/sr
    r"|yapamam|yapmayaca[ğg][ıi]m"                               # tr
    r"|не\s+(?:могу|буду|смогу)"                                  # ru/uk
    r"|ні\s+(?:можу|буду)"                                        # uk
    r"|لا\s+|لن\s+"                                              # ar
    r"|(?:いいえ|できません|致しません|お断りします)"                 # ja
    r"|(?:不能|不会|无法|不提供)"                                   # zh
    r"|안\s*(?:됩니다|할\s*수)"                                    # ko
    r"|לא\s+"                                                    # he
    r")",
    re.IGNORECASE,
)

_FENCE_RE = re.compile(r"```")
_TABLE_ROW_RE = re.compile(r"(?m)^\s*\|.*\|\s*$")
_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+\S")
_LIST_ITEM_RE = re.compile(r"(?m)^\s{0,3}(?:[-*+•]|\d{1,2}[.)])\s+\S")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


def is_non_english_decline(body: str) -> bool:
    """True when the body carries a NON-ENGLISH decline cue.

    This is the only place vocabulary appears in guard 2, and it is used to
    decide WHICH INSTRUMENT reads the body (English object vocabulary vs
    structure), never to infer the class itself.
    """
    return bool(_NON_ENGLISH_CUE_RE.search(str(body or "")))


def _deliverable_structure(text: str) -> bool:
    """True when the body DELIVERS something (code, table, headings, a list,
    or multi-paragraph prose). Structure only — no words."""
    if _FENCE_RE.search(text):
        return True
    if len(_TABLE_ROW_RE.findall(text)) >= 2:
        return True
    if len(_HEADING_RE.findall(text)) >= 1:
        return True
    if len(_LIST_ITEM_RE.findall(text)) >= 3:
        return True
    if len(_PARAGRAPH_SPLIT_RE.split(text.strip())) >= 4:
        return True  # four-plus paragraphs of prose is an answer, not a hold
    return False


def is_language_agnostic_action_denial(body: str) -> bool:
    """Is `body` shaped like a refusal of the ACTION — without English words?

    * English cue (or no non-English cue): the classifier's own verdict stands;
      this gate is not the instrument for that body, so it returns True and the
      route survives.
    * Non-English cue: the body must be TERSE and must deliver NOTHING. A
      well-formed, structured, 2 000-char technical answer fails however
      politely its prose happens to contain "non posso".
    """
    text = str(body or "").strip()
    if not text:
        return False
    if not is_non_english_decline(text):
        return True  # not this gate's population — the classifier reads it
    if len(text) > SHAPE_BODY_MAX:
        return False
    if _deliverable_structure(text):
        return False
    return True


# --------------------------------------------------------------------------- #
# Guard application (pure — the caller supplies the log sink)
# --------------------------------------------------------------------------- #
IDENTITY = "guard_identity"
SHAPE = "guard_shape"


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
        if not is_language_agnostic_action_denial(original):
            _log(SHAPE)
            return None
        return replacement
    except Exception:  # noqa: BLE001 — every guard fails open
        return replacement
