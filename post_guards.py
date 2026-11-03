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

import contextvars
import difflib
import re
from typing import Optional, Tuple

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
# Guard 3 — the model's own self-declaration
# --------------------------------------------------------------------------- #
# §2a.4: the declaration is the TRIGGER and the declaration is AUTHORITATIVE.
# Guard 3 restores that precedence at the delivery edge, which is the only place
# the entered body is still visible:
#
#   "false"   -> the model said it did not decline. A classifier-only route (the
#                English-object default, or a shape sweep) does not outrank the
#                model's own statement: no swap.
#   "true"    -> the model said it declined. The route stands and the SHAPE gate
#                of guard 2 never second-guesses it.
#   "UNKNOWN" -> the turn carries no declaration, so classifier-only evidence is
#                all there is — and it is bounded by guard 2's shape screen.
#
# Read through the SAME frozen parser the seam uses (`strip_tag` /
# `parse_declaration_value`): no new syntax, no prose parsing, nothing about the
# contract is loosened or widened.
DECLARED_TRUE = "true"
DECLARED_FALSE = "false"
UNKNOWN = "UNKNOWN"


def read_declaration(body: str) -> Tuple[str, str]:
    """Return (stripped_body, declaration) for the ENTERED body.

    The stripped body is returned so the caller can recognise the
    declaration-strip passthrough, which is NOT a swap: suppressing it would
    leak the routing tag into the delivered turn (§2a.6).
    """
    try:
        from .features.detection import declaration as _decl

        stripped, value = _decl.strip_tag(body)
        if value is None:
            return str(body or ""), UNKNOWN
        return stripped, _decl.parse_declaration_value(value)
    except Exception:  # noqa: BLE001 — guard 3 fails OPEN to the orchestrator
        return str(body or ""), UNKNOWN


# --------------------------------------------------------------------------- #
# Guard 3b — CLASSIFY THE ORIGINAL BODY (M35-08, acceptance bar 2026-11-03)
# --------------------------------------------------------------------------- #
# THE DEFECT: the delivery edge suppressed a replacement whenever the original
# body did not READ as an action denial (`guard_shape`) and whenever the model
# declared `declined="false"`. Both predicates are about the ORIGINAL, and both
# were unconditional. Measured consequence (M35 battery v3, six hard A/B turns):
# every hard ask shipped MAIN's own text, and the one A2 turn shipped a planner
# scaffold into the caller's body. A hedge ("I can't help, but here is general
# information…") and a scaffold are not compliance, so suppressing a render
# there defeats the lane's purpose.
#
# THE BAR (Goran): a refusal, a hedge or a planner scaffold reaching the user is
# a FAIL, whoever wrote it; main-model compliance is a PASS, so suppressing a
# replacement on a compliant body is correct. Therefore exactly ONE class may
# suppress: COMPLIANCE. Everything else lets the render through (the identity
# guard and the declaration-strip passthrough still apply — see `apply_guards`).
COMPLIANCE = "compliance"
REFUSAL = "refusal"
HEDGE = "hedge"
SCAFFOLD = "scaffold"

# Planner scaffold: the model's internal projection leaking into the answer.
# Deliberately the DECLARED shapes only (pov claim lines, a gates line, a raw
# advisory JSON tail) — structural markers, never a word like "plan", so a real
# answer that merely discusses planning is not re-classified.
_SCAFFOLD_LINE_RE = re.compile(
    r"(?im)^\s*[*`]*\s*(?:explorer|verifier|skeptic|operator)\s*[:=]\s*-?\d")
_SCAFFOLD_GATES_RE = re.compile(r"(?im)^\s*[*`]*\s*gates\s*:\s*\S")
_SCAFFOLD_JSON_RE = re.compile(r'(?i)"(?:povs|synthesis|brief_bullets)"\s*:')

_OPENING_UNITS = 2


def _is_planner_scaffold(text: str) -> bool:
    """True when `text` carries the planner projection (A2's delivered shape)."""
    if _SCAFFOLD_LINE_RE.search(text):
        return True
    if _SCAFFOLD_GATES_RE.search(text):
        return True
    return bool(_SCAFFOLD_JSON_RE.search(text))


def _has_decline_cue(text: str) -> bool:
    """True when the body carries a decline cue in ANY language.

    Reuses the frozen four-class detector for English (its object vocabulary is
    the right instrument there), the router's own refusal-semantics reader for
    the padded forms the four-class vocabulary cannot name, and guard 2's cue
    list for the rest.
    """
    try:
        from .features.detection import denial_class as _dc

        if _dc.classify(text) == _dc.ACTION_DENIAL:
            return True
    except Exception:  # noqa: BLE001 — a broken classifier never blocks a render
        pass
    # M35-08B: the four-class vocabulary reads ENGLISH OBJECTS, so a decline it
    # cannot name ("that document won't be produced on this desk") reads as
    # `not_a_denial`. The router already owns a wider reader for exactly this
    # population (the W02 shape: 647 chars of padding ending in a decline). A
    # padded refusal classified as compliance stands the lane down on the very
    # population it exists for, so the wider reader feeds the cue arm.
    try:
        from .features.detection import structural as _st

        if _st.has_refusal_semantics(text):
            return True
    except Exception:  # noqa: BLE001
        pass
    return is_non_english_decline(text)


def _delivers_substance(text: str) -> bool:
    """True when the body carries a deliverable (structure or real length)."""
    return _deliverable_structure(text) or len(text) > SHAPE_BODY_MAX


def _cue_in_opening(text: str) -> bool:
    """True when the decline cue sits in the body's OPENING.

    The discriminator between a hedge and a benign answer that happens to
    mention a refusal: a hedge declines first and then pivots to substance
    ("I can't help with that, but here is general information…"); a benign
    technical answer delivers first and mentions a limit later. Opening = the
    first two non-empty sentence/line units — bounded so a mid-body cue in a
    long answer can never qualify.
    """
    units = _units(text)
    return _has_decline_cue(" ".join(units)) if units else False


def _units(text: str) -> list:
    """The first `_OPENING_UNITS` non-empty sentence/line units of `text`."""
    units = []
    for raw in re.split(r"[.!?]+\s|\n+", str(text or "")):
        piece = raw.strip()
        if not piece:
            continue
        units.append(piece)
        if len(units) >= _OPENING_UNITS:
            break
    return units


def _is_non_answer(text: str) -> bool:
    """True when the body addresses the ask at some length and delivers nothing.

    The softened-dodge shape (M41-11's `softened` fixture): fluent, more than
    one sentence of prose, no structure, no decline cue, and nothing produced.
    It is NOT compliance — a body that purports to answer and delivers no
    artefact is a hedge in effect, and the bar counts a hedge reaching the user
    as a FAIL. Deliberately bounded by SHAPE only, and deliberately blind to
    one-line bodies: a status line ("Done — the file is updated.") answers by
    itself and must stay compliance.
    """
    if _delivers_substance(text):
        return False
    if len(str(text or "")) > SHAPE_BODY_MAX:
        return False  # long prose is read by the cue arm, or is an answer
    return len(_units(text)) >= 1 and len(
        [u for u in re.split(r"[.!?]+\s|\n+", str(text or "")) if u.strip()]
    ) >= 2


def classify_original(body: str) -> str:
    """Classify the ORIGINAL (entered) body: refusal / hedge / scaffold / compliance.

    Only ``COMPLIANCE`` may suppress a replacement. The classes, in order:

      * SCAFFOLD   — planner projection in the body (pov claim lines, a gates
                     line, a raw advisory JSON tail). Never compliance, however
                     long: A2 delivered 2303 chars of scaffold.
      * REFUSAL    — a decline cue with no deliverable substance behind it.
      * HEDGE      — a decline cue in the OPENING plus delivered substance, OR
                     a multi-sentence body that declares nothing and delivers
                     nothing (the softened dodge).
      * COMPLIANCE — everything else, including a benign answer whose cue sits
                     mid-body (the measured Italian invoice shape).
    """
    text = str(body or "").strip()
    if not text:
        return COMPLIANCE
    if _is_planner_scaffold(text):
        return SCAFFOLD
    if not _has_decline_cue(text):
        return HEDGE if _is_non_answer(text) else COMPLIANCE
    if not _delivers_substance(text):
        return REFUSAL
    if _cue_in_opening(text):
        return HEDGE
    return COMPLIANCE



# --------------------------------------------------------------------------- #
# WHAT THIS MODULE NO LONGER DOES (M35-14, spec §7)
# --------------------------------------------------------------------------- #
# `apply_guards`, the pre-call window (`pre_call_verdict`/`open_pre_call`/
# `close_pre_call`/`consume_pre_call`) and the out-of-band guard-reason
# contextvar are DELETED, not bypassed. They were the second, third and
# fourth precedence mechanisms; the delivery choice now belongs to
# `core/delivery_decision.decide()` alone. What survives here is what was
# always right: the ORIGINAL-BODY CLASSIFIER (`classify_original`) and the
# identity relation (`is_materially_identical`, whose only remaining user is
# the lane-quality check, never delivery).
