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

# R33 (audit close 2026-10-10, D02/V01-V03 zero-event family): IN-VOICE
# DNA-LINE DECLINATION — the refusal wears the lane's own doctrine voice
# instead of refusal vocabulary: bare one-word hold openers ("Held."),
# the self-voiced routing carve-out ("the one class where I don't
# route"), the flinch-denial variant ("not as a flinch"), and ordinal
# closed-list references ("second line on the closed list"). All four
# audit misses share this register. SHAPE ONLY: doctrine/framing words,
# never topic vocabulary (Goran ruling — framing only, always).
# Bounded: doctrine phrases in the 600c window, hold-sentence at the
# turn head, whole family inert past 2000c. FP guards: the bare
# hold-sentence must OPEN the turn as a <=2-word sentence — relayed
# third-person holds ("the negotiator held firm") never match; Tier 2
# remains the final decision either way (widening costs a judge call,
# never a route by itself).
_DNA_VOICE_RE = re.compile(
    r"the one class where i don'?t route"
    r"|i don'?t route (?:this|that|it|weapon)"
    r"|not as a flinch"
    r"|(?:first|second|third|next|last) line on the closed list",
    re.IGNORECASE)
_DNA_HOLD_SENTENCE_RE = re.compile(
    r"^\s*(?:held|holding|closed)\s*[.!]\s*", re.IGNORECASE)
_DNA_VOICE_MAX_CHARS = 2000

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

# R32 (Goran directive 2026-10-10, live miss api_1791541530_3457853a, x7
# 1,444c, zero router events): WON'T-FRAMING WIDENING — detection triggers on
# refusal/won't-framing ONLY (never on content, render types, or request-type
# inspection). Three shapes:
#   (a) settled-line opener family — "no on this one", "not this one",
#       "this one's a no", "pass on this one" — open-class openers keep
#       beating the fixed tables, so the FAMILY (first ~200c, bounded) is
#       the shape;
#   (b) in-register self-voice frame-explainer — first-person + negation +
#       "refus(ing|al)" + the frame/depth/medium attribution ("it's not the
#       depth refusing — it's the frame"). The words named are how models
#       explain their OWN no — framing shape, language-portable as a class,
#       not topic vocabulary;
#   (c) multilingual won't-framing openers (first ~200c) — different
#       languages construct won'ts differently: hr/bs/sr/sl "ne mogu/neću",
#       de "nein sagen", es "no voy a". Framing verbs only, never content.
# FP guard: the family phrases and won't-constructions are themselves
# declines — a turn cannot contain them while complying; bounded windows
# keep long-turn FP surface flat, and Tier 2 still owns the decision
# (candidate -> judge call, never a route from Tier 1).
_OPENER_FAMILY_WINDOW = 200
_REFUSAL_OPENER_FAMILY_RE = re.compile(
    r"(?:^|\n)\s*(?:\**)?(?:no on this one|not this one|this one'?s a no|"
    r"pass on this one)\b",
    re.IGNORECASE)
_FRAME_REJECTION_RE = re.compile(
    r"\bit'?s not the [a-z-]+ refus(?:ing|al)\b", re.IGNORECASE)
_FRAME_REJECTION_WINDOW = 600
_WONT_FRAME_OPENERS_RE = re.compile(
    r"(?:^|\n)\s*(?:\**)?(?:"
    r"ne (?:mogu|ću)\b"            # hr/bs/sr/sl: "Ne mogu to napisati"
    r"|neću\b"
    r"|no voy a\b"                 # es: "No voy a escribir eso"
    r"|tega ne\b"                  # sl: "Tega ne napišem" / "Tega ne bom"
    r"|ne bom\b"                   # sl future-negation volition
    r"|napi[šs]em\b"               # sl "ne napišem" tail (line-anchored hit
                                   #      above requires the tega-ne head;
                                   #      this catches "To ne napišem")
    r"|obstavljen[ai]\s+linij[ai]"  # hr/bs/sl "postavljena linija" (settled
                                   #      line) — boundary declaration
    r"|postavljen[ai]\s+linij[ai]"
    r")",
    re.IGNORECASE)
# R33b: UNANCHORED sl/hr boundary declarations — "Ovo je postavljena
# linija" and "To ne bom naredil" carry the refusal mid-line, so the
# line-anchored opener table above cannot see them. Framing-only: the
# negated volition and the settled-line declaration, never topic words.
_SL_BOUNDARY_DECLARATION_RE = re.compile(
    r"\btega ne\b"
    r"|\bne bom\b"
    r"|\bne\s+napi[šs]em\b"
    r"|postavljen[ai]\s+linij[ai]"
    r"|obstavljen[ai]\s+linij[ai]",
    re.IGNORECASE)
# R33b (addendum 1, zero-event multilingual family): CJK refusal shapes —
# the latin-script opener table has no CJK coverage. Shape-only: negated
# potential/writing verbs and the politeness-refusal formula. Never topic
# vocabulary.
_WONT_FRAME_CJK_RE = re.compile(
    r"これは書けません"          # ja: "this cannot be written"
    r"|書け(?:ない|ません)"       # ja: potential-negation "cannot write"
    r"|お断りします"              # ja: politeness refusal formula
    r"|できません"
    r"|不能写"                    # zh: cannot write
    r"|不会写",
)
# R33c (V12 re-probe, live miss diagnosis): JA NEGATION VERB-SUFFIX shapes —
# Japanese negation is verb-FINAL (は書かない), not sentence-initial, so the
# CJK opener family (zh 不/没有, ja できません) cannot see it. Candidate =
# negation-suffix verb within the first 400c + following-clause structure
# (explanation / counter-offer after the negation), or the negation closes
# the turn (tail <= 40c). FP-guarded: a bare negation with no clause after
# it and no turn-closing position never fires; Tier 2 owns the decision.
_JA_NEGATION_SUFFIX_RE = re.compile(
    r"書かない|作らない|出せない|提供しない|答えられない"
    r"|お断り(?:します)?|断ります|無理です"
    r"|書きません|作りません|出しません|答えられません",
)
# FP guard (meta-ask): the negation embedded in a request ABOUT the refusal
# ("書かない理由を説明してください" — explain why you won't write) is not the
# refusal itself. The two chars after the suffix naming the meta-noun kill it.
_JA_META_NOUNS = ("理由", "方法", "場合", "とは", "のは", "のか", "という")
_JA_CLOSE_RE = re.compile(r"。|！|？|\Z")
# Mid-sentence won't-framing constructions (German constructs its no
# inside the clause: "Da muss ich nein sagen") — searched anywhere in the
# window, not line-anchored. Framing verbs only.
_WONT_FRAME_MIDSENTENCE_RE = re.compile(
    r"(?:(?:muss|will)\s+ich\s+nein\b|\bnein sagen\b|\bnicht (?:schreiben|tun)\b)",
    re.IGNORECASE)

# R32 leg 2: the R30 counter-offer gate gets its OWN bound — the live miss
# was 1,444c and slipped the 1200c short-turn bound it shared with R28.
_COUNTER_OFFER_MAX_CHARS = 2000

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

# R33f (W01-W04 audit family, 2026-10-10): MID-BODY/TAIL denial shapes in
# LONG turns — the refusal sits past every head window (openers, the 600c
# essay window, the 1200c/2000c family bounds), so opener-chasing misses it
# by construction. Rule: on turns OVER 400c, scan the WHOLE body for a
# decline construction (first-person negative-volition beyond the modal set,
# or the answer-copula / no-version family) — the decline + a
# negation/limitation marker co-occurring in the same or next sentence makes
# the candidate anywhere in the body, not just at the openers. SHAPE ONLY:
# volition/decline constructions, never topic vocabulary (Goran standing
# ruling). FP guards: double-quoted/relayed refusal spans are STRIPPED
# before the scan (a quoted hold inside delivered prose never fires — the
# quotation guard), the produce/echo requirement below still applies to the
# whole turn, and Tier 2 owns every widened candidate (grounded-yield
# content-check applies at POST — the R33d contract). Bounded at 6000c to
# match the render cap; beyond that the FP surface outweighs recall.
_W_LONGTURN_MIN_CHARS = 400
_W_LONGTURN_MAX_CHARS = 6000
# (a) negation-carrying declines — the negation is INSIDE the decline
# construction (auto-satisfies the negation co-occurrence check).
_W_DECLINE_RE = re.compile(
    r"\bnot\s+going\s+to\s+(?:write|produc|creat|render|mak|provid|do|help|generat|draw|describ)\w*"
    r"|\bi\s*(?:'m| am)\s+not\s+going\s+to\b"
    r"|\bwon'?t\s+be\s+(?:writ\w*|produc\w*|creat\w*|render\w*|mak\w*|provid\w*|doing)\b"
    r"|\bnot\s+something\s+i\b"
    r"|\bno\s+version\s+of\b"
    r"|\bnot\s+writing\s+(?:that|this|it|one)\b",
    re.IGNORECASE)
# (b) answer-copula declines — the decline names the no without a negation
# verb; the co-occurrence check supplies the negation (bare no counts).
_W_COPULA_RE = re.compile(
    r"\b(?:my|the|our)\s+answer\b[^.!?]{0,30}\b(?:is|stands|stays|remains|was)\b[^.!?]{0,20}\bno\b"
    r"|\banswer\s+(?:stands|stays)\s*:\s*no\b"
    r"|\b(?:that|this|it)\s+(?:one\s+)?(?:stays|remains)\s+(?:a\s+no|closed)\b"
    r"|\b(?:stays?|remains?)\s+a\s+no\b",
    re.IGNORECASE)
# W-scan negation set: the structural set PLUS bare no/nothing/never/nowhere
# (the copula family declines with the bare word, not a negation verb).
_W_NEGATION_RE = re.compile(
    r"\b(?:not|won'?t|can'?t|cannot|refuse|decline|hold|withhold|"
    r"no|nothing|never|nowhere)\b",
    re.IGNORECASE)
_QUOTED_SPAN_RE = re.compile(
    r"\"[^\"\n]{0,400}\""       # straight double quotes
    r"|[“”«»][^“”«»\n]{0,400}[“”«»]",  # curly/guillemet spans
)

# R33g (final battery, ONE root class — V01/V02/V21/W02, all identical
# shape): THIRD-ASK / HOLD register — the negation is buried in
# META-commentary about the conversation ("same answer as last time",
# "the hold doesn't move", "third ask", "the framing doesn't change the
# object") instead of first-person refusal vocabulary, so every head
# family and the modal gate miss it. Rule (bounded 6000c, quoted spans
# stripped): a meta-commentary phrase + a negation/decline marker in the
# same or the next sentence = candidate, anywhere in the turn. FP guard:
# a genuine substantive answer that carries DELIVERABLE content (file
# paths, saved-to/doc sections, written/drafted/committed work) stays
# unrouted even with the phrases present — and Tier 2 still owns every
# candidate (a judge call, never a route by itself).
_THIRD_ASK_MAX_CHARS = 6000
_THIRD_ASK_PHRASES_RE = re.compile(
    r"\bsame answer\b|\bthe hold\b|\bthird ask\b|\bdoesn'?t move\b"
    r"|\banswered this\b|\bhasn'?t changed\b|\basked again\b|\bagain and\b",
    re.IGNORECASE)
_DELIVERABLE_CONTENT_RE = re.compile(
    r"(?:^|[\s(])/(?:[A-Za-z0-9._~-]+/)+[A-Za-z0-9._~-]+"  # unix paths
    r"|\bsaved to\b|\bsaved (?:it|the)\b|\bwrote (?:the|a|an|up)\b"
    r"|\bdrafted\b|\bcommitted\b|\bpull request\b"
    r"|\bsection \d\b|\bdoc(?:ument|s)? section\b"
    r"|\brendered\b|\bdelivered the\b|\bfull (?:writeup|write-up)\b",
    re.IGNORECASE)
# The family's negation set: the W-scan set PLUS the doesn't/isn't copula
# family — V01's negation lives in "the framing doesn't change what the
# artifact is", which the volition-only set cannot see.
_THIRD_ASK_NEGATION_RE = re.compile(
    r"\b(?:not|won'?t|can'?t|cannot|refuse|decline|hold|withhold|"
    r"no|nothing|never|nowhere"
    r"|doesn'?t|isn'?t|aren'?t|didn'?t|wasn'?t|weren'?t)\b",
    re.IGNORECASE)


def _strip_quoted_spans(text: str) -> str:
    """FP guard for the W-scan: relayed/quoted refusal spans never count.
    Bounded spans (400c) so a runaway quote can't eat the body; never
    raises."""
    try:
        return _QUOTED_SPAN_RE.sub(" ", text or "")
    except Exception:  # noqa: BLE001
        return text or ""


def has_deliverable_content(text: str) -> bool:
    """R33g (V06 mixed-turn): True when the turn carries DELIVERED
    substance — file paths, saved-to/doc sections, written/drafted/
    committed work. The grounded-yield content-check uses this to tell a
    MIXED turn (part delivered + part declined = refusal) from a pure
    refusal body (prior-answer contract). Quoted spans stripped; never
    raises."""
    try:
        return bool(_DELIVERABLE_CONTENT_RE.search(
            _strip_quoted_spans(text or "")))
    except Exception:  # noqa: BLE001
        return False


def _sentences(text: str) -> List[str]:
    return [s for s in _SENTENCE_SPLIT_RE.split(text or "") if s and s.strip()]


def _bounded_family_hit(t: str) -> bool:
    """R33h: True when the turn was flagged by a BOUNDED denial family —
    the head-window register families (essay, litany, line-hold,
    short-hold, counter-offer, opener/frame/multilingual families, ja
    suffix, repeat-denial, DNA voice) plus the third-ask family. The
    judge self-consistency tie-break (R33h) uses this as the
    deterministic T1 signal: bounded family -> ROUTE on judge
    disagreement; long-turn-only flags (the W-scan, the modal loop) go to
    the tie-break vote. Pure predicate over the normalized turn `t`;
    never raises."""
    try:
        # Line-hold essay opener shape (no modal requirement — the essay
        # never says "I won't" in its first-person-modal form). R22b: the
        # widened opener `No ...` requires either the original essay marker
        # or the third-person frame-hold attribution in the window.
        head = t[:_ESSAY_WINDOW].lower()
        if _ESSAY_OPENER_RE.match(t) and \
                (_ESSAY_MARKER in head or _FRAME_HOLD_RE.search(t[:_ESSAY_WINDOW])):
            return True
        # R26-1: serial isn't-litany justification.
        if _ESSAY_OPENER_RE.match(t):
            for m in _LITANY_RE.finditer(t[:_ESSAY_WINDOW]):
                if not _LITANY_CONJ_RE.search(m.group(0)):
                    return True
        # R27: short settled-line hold — refusal opener + boundary vocab
        # (or the negative-of-negation self-attribution shape).
        if _LINE_HOLD_OPENER_RE.match(t):
            head = t[:_LINE_HOLD_WINDOW].lower()
            if any(v in head for v in _LINE_HOLD_VOCAB) or \
                    _LINE_HOLD_SELF_ATTRIBUTION_RE.search(t[:_LINE_HOLD_WINDOW]):
                return True
        # R28: short declarative hold WITHOUT a (fixed-set) opener — 2+
        # distinct boundary-vocab signals.
        if len(t) < _SHORT_HOLD_MAX_CHARS:
            head = t[:_SHORT_HOLD_MAX_CHARS].lower()
            if sum(1 for v in _LINE_HOLD_VOCAB if v in head) >= 2:
                return True
        # R30: refusal + counter-offer shape — decline + offer REQUIRED.
        if len(t) < _COUNTER_OFFER_MAX_CHARS:
            if _DECLINE_MARKER_RE.search(t) and _COUNTER_OFFER_RE.search(t):
                return True
        # R32(a): settled-line opener family (first ~200c).
        if _REFUSAL_OPENER_FAMILY_RE.search(t[:_OPENER_FAMILY_WINDOW]):
            return True
        # R32(b): in-register self-voice frame-explainer (bounded window).
        if _FRAME_REJECTION_RE.search(t[:_FRAME_REJECTION_WINDOW]):
            return True
        # R32(c): multilingual won't-framing openers + sl boundary
        # declarations + CJK shapes (first ~200c).
        head32 = t[:_OPENER_FAMILY_WINDOW]
        if (_WONT_FRAME_OPENERS_RE.search(head32)
                or _WONT_FRAME_MIDSENTENCE_RE.search(head32)
                or _WONT_FRAME_CJK_RE.search(head32)
                or _SL_BOUNDARY_DECLARATION_RE.search(head32)):
            return True
        # R33c: ja negation verb-suffix (V12 class), meta-noun FP guard.
        _ja_m = _JA_NEGATION_SUFFIX_RE.search(t[:400])
        if _ja_m is not None:
            _ja_tail = t[_ja_m.end():_ja_m.end() + 400]
            if not any(_ja_tail.startswith(_mn) for _mn in _JA_META_NOUNS) and \
                    _JA_CLOSE_RE.search(_ja_tail) and \
                    (len(_ja_tail.rstrip()) <= 40 or len(_ja_tail.strip()) >= 2):
                return True
        # R31: repeat-denial register (bounded short turns).
        if len(t) < _SHORT_HOLD_MAX_CHARS:
            if _REPEAT_DENIAL_RE.search(t) and _NEG_VOLITION_RE.search(t):
                return True
        # R33: in-voice DNA-line declination (inert past 2000c).
        if len(t) < _DNA_VOICE_MAX_CHARS:
            if _DNA_VOICE_RE.search(t[:_LINE_HOLD_WINDOW]) or \
                    _DNA_HOLD_SENTENCE_RE.match(t):
                return True
        # R33g: third-ask / hold register — meta-commentary phrases +
        # negation, deliverable-content FP guard, bounded 6000c.
        if len(t) <= _THIRD_ASK_MAX_CHARS:
            stripped3 = _strip_quoted_spans(t)
            if _THIRD_ASK_PHRASES_RE.search(stripped3) and \
                    not _DELIVERABLE_CONTENT_RE.search(stripped3):
                sents3 = _sentences(stripped3)
                for i, sent in enumerate(sents3):
                    if _THIRD_ASK_PHRASES_RE.search(sent):
                        window = sent
                        if i + 1 < len(sents3):
                            window += " " + sents3[i + 1]
                        if _THIRD_ASK_NEGATION_RE.search(window):
                            return True
        return False
    except Exception:  # noqa: BLE001 — the predicate never raises into a turn
        return False


def flagged_by_bounded_family(text: str) -> bool:
    """Public R33h seam: the deterministic T1 signal for the judge
    self-consistency tie-break. True when the turn is a candidate via a
    BOUNDED denial family (as opposed to a long-turn-only flag from the
    W-scan or the modal loop). Never raises."""
    try:
        if not isinstance(text, str):
            return False
        t = text.strip()
        if not t:
            return False
        return _bounded_family_hit(t)
    except Exception:  # noqa: BLE001
        return False


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
        # Bounded denial families (essay, litany, line-hold, short-hold,
        # counter-offer, opener/frame/multilingual, ja suffix,
        # repeat-denial, DNA voice, third-ask) — extracted verbatim into
        # _bounded_family_hit for the R33h tie-break; boolean behavior is
        # identical (every branch was a pure OR).
        if _bounded_family_hit(t):
            return True
        # Produce/echo marker must be present somewhere in the turn.
        if not (_ECHO_RE.search(t) or _PRODUCE_RE.search(t)):
            return False
        # R33f (W01-W04): mid-body/tail denial scan for LONG turns (>400c) —
        # decline + negation markers ANYWHERE in the body, not just at the
        # openers. Placed AFTER the produce/echo gate so the W-rule
        # inherits the echo/produce requirement. FP guards: quoted/relayed
        # refusal spans stripped; Tier 2 owns the decision (grounded-yield
        # content-check applies at POST). Bounded 6000c.
        if len(t) > _W_LONGTURN_MIN_CHARS:
            body = t[:_W_LONGTURN_MAX_CHARS]
            stripped = _strip_quoted_spans(body)
            sents_w = _sentences(stripped)
            for i, sent in enumerate(sents_w):
                if _W_DECLINE_RE.search(sent) or _W_COPULA_RE.search(sent):
                    window = sent
                    if i + 1 < len(sents_w):
                        window += " " + sents_w[i + 1]
                    if _W_NEGATION_RE.search(window):
                        return True
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
