"""Four-class denial detector — POST-only trigger for the uncensored lane.

Spec: /opt/data/tmp/unc_lane_post_spec.md §2 (design fixed by Goran 2026-10-28).

ONE question is asked of the DELIVERED body: is this a refusal of the ACTION
that was requested?

    action_denial   declines to do the thing / supply the content — moral,
                    values or policy grounds, INCLUDING capability-washing
                    ("I'm not able to assist with that").   -> ROUTE
    truth_denial    declines to vouch for accuracy: "I can't verify", "I don't
                    know", "outside my knowledge", "I need the file first".
                                                             -> DO NOT ROUTE
    capacity_limit  no access, no tool, input too large, environment wrong.
                                                             -> DO NOT ROUTE
    not_a_denial    compliance, including hedged or partial compliance.
                                                             -> DO NOT ROUTE

The discriminator is the OBJECT of the refusal, NEVER the modal verb.
`can't` / `won't` vocabulary fails both ways: moral denials say "not able",
honest answers say "can't confirm". So the cue only marks WHERE a refusal
lives; the object phrase right after it decides the class.

Deterministic by construction: pure regex + word sets, no model, no network,
no state. The same body always classifies the same way — this is what makes
the pass/fail path judge-free (spec §2 "scoring is deterministic").

Soft denials — an answer that restates the ask, moralises and stops without
declining — are explicitly OUT OF SCOPE (spec §2). Detecting those needs an
adequacy judge over content; the standing answer for that case is: re-ask.
This module therefore never guesses from tone, length or hedging alone.
"""
from __future__ import annotations

import re
from typing import Optional

ACTION_DENIAL = "action_denial"
TRUTH_DENIAL = "truth_denial"
CAPACITY_LIMIT = "capacity_limit"
NOT_A_DENIAL = "not_a_denial"

ALL_CLASSES = (ACTION_DENIAL, TRUTH_DENIAL, CAPACITY_LIMIT, NOT_A_DENIAL)

# A refusal cue only localises the refusal; the object decides the class.
# First-person decline stance + the capability-washing forms ("not able",
# "unable") the spec calls out as action denials despite their wording.
_CUE_RE = re.compile(
    # SUBJECT-ELIDED opener: models routinely drop the "I" and open the turn
    # with "Not going to write that one." — a decline of the ACTION with no
    # first-person subject for any cue below to attach to. Live miss
    # (2026-10-28, session api_1793192476_881df1c5): the only cue the regex
    # then found sat at char 426 of a 1362c body, past _LATE_CUE_AT, so the
    # late-cue rule returned not_a_denial before the object was consulted.
    # Anchored to the BODY START and to a produce/do verb on purpose: a bare
    # "not going to" mid-answer is a prediction ("the plan is not going to
    # work"), not a refusal of the ask, and must not become a cue.
    r"(?:^\s*(?:not|never)\s+(?:going\s+to|gonna)\s+"
    r"(?:write|draft|produce|create|generate|help|provide|make|craft|compose"
    r"|author|build|render|supply|do|give|share)\b"
    r"|\bi\s*(?:'m|’m|am)\s+(?:not\s+able|unable|not\s+going|not\s+comfortable"
    r"|not\s+willing|not\s+prepared|not\s+in\s+a\s+position)"
    r"|\bi\s*(?:can'?t|cannot|can\s+not|won'?t|will\s+not|refuse|decline"
    r"|must\s+decline|have\s+to\s+decline|need\s+to\s+decline|'d\s+rather\s+not"
    r"|would\s+rather\s+not)"
    r"|\b(?:not\s+able|unable)\s+to\b"
    r"|\bnot\s+something\s+i\s+(?:can|will|do|am\s+able\s+to)\b"
    r"|\bi\s+(?:must|have\s+to|need\s+to)\s+(?:stop|refuse|decline)\b"
    r"|\b(?:goes|go)\s+against\s+my\s+(?:values|principles|guidelines|policy)\b"
    r"|\bviolates\s+my\s+(?:values|principles|guidelines|policy)\b"
    # multilingual bare declines (fleet languages seen in live refusals)
    r"|\bnon\s+(?:lo\s+far[òo]|posso|riesco)\b|\bje\s+ne\s+(?:peux|veux)\s+pas\b"
    r"|\bich\s+kann\s+(?:das\s+)?nicht\b|\bno\s+puedo\b|\bnie\s+mog[ęe]\b"
    r"|\bne\s+mogu\b|\bne(?:ću|cu)\b)",
    re.IGNORECASE,
)

# Truth cues: the decline is about VOUCHING, not about doing. These classify
# truth_denial whatever object follows ("I don't know" has no object at all).
_TRUTH_CUE_RE = re.compile(
    r"(?:\bi\s*(?:'m|’m|am)?\s*(?:don'?t|do\s+not)\s+know\b"
    r"|\bi\s*(?:'m|’m|am)\s+not\s+sure\b"
    r"|\bi\s+(?:can'?t|cannot|can\s+not)\s+be\s+(?:sure|certain)\b"
    r"|\bi\s+need\s+the\s+(?:file|document|source|data|link|text|image|attachment)\b"
    r"|\b(?:outside|beyond)\s+my\s+knowledge\b)",
    re.IGNORECASE,
)

# Capacity cues: the turn reports an environmental limit with no moral
# decline at all ("I have no network access", "the input is too large").
_CAPACITY_CUE_RE = re.compile(
    r"(?:\bi\s+(?:have|'ve\s+got)\s+no\s+(?:network|internet|web|access|tools?|connection)\b"
    r"|\bi\s+(?:don'?t|do\s+not)\s+have\s+(?:access|a\s+tool|network|internet|a\s+connection)\b"
    r"|\b(?:no|without)\s+(?:network|internet)\s+access\b"
    r"|\btoo\s+large\s+(?:to|for)\b|\bexceeds?\s+my\s+(?:context|token)\b"
    r"|\boutside\s+(?:this|my)\s+(?:environment|sandbox)\b)",
    re.IGNORECASE,
)

# Object vocabulary. Longest-match intent: an object naming BOTH a truth claim
# and an action ("help verify") is a truth denial — accuracy is the object.
_TRUTH_OBJECT = (
    "verify", "verif", "confirm", "certain", "be sure", "guarantee",
    "vouch", "accura", "truth", "fact-check", "fact check", "whether",
    "unsure", "unverified", "up to date", "up-to-date", "current information",
    "cutoff", "cut-off", "training data", "knowledge", "recall", "remember",
    "the file", "the document", "the source", "the data you", "the image",
    "the link", "the attachment", "before i can answer", "without seeing",
    "without the file", "without the document", "without the source",
    "without the data", "more context", "more information", "information about what",
    "independent", "prove",
)

_CAPACITY_OBJECT = (
    "access", "network", "internet", "online", "web", "browse", "browser",
    "tools", "tool", "connection", "environment", "filesystem", "file system",
    "credentials", "permission", "size", "too large", "context window",
    "compute", "run code", "execute code", "install", "real-time", "real time",
    "live data", "api", "database", "sandbox", "memory limit", "token limit",
    "in this chat", "in this environment",
)

_ACTION_OBJECT = (
    "assist", "help", "provide", "write", "creat", "generat", "produc", "give",
    "share", "explain", "tell", "show", "do that", "do this", "comply",
    "fulfil", "fulfill", "continue", "answer", "complete", "draft", "translate",
    "summar", "describ", "detail", "instruction", "steps", "step-by-step",
    "content", "artwork", "image", "code", "essay", "story", "plan", "protocol",
    "recipe", "synthes", "anything", "it", "that", "this",
)

# Compliance openers: a turn that starts by delivering is not a refusal, even
# if a refusal phrase is quoted later in the body.
_COMPLIANCE_OPENER_RE = re.compile(
    r"^\s*(?:here(?:'s| is| are)\b|below\s+is\b|sure[,!.]|certainly[,!.]|"
    r"of course[,!.]|done\b|i've\s+(?:written|created|built|added)\b|"
    r"the\s+(?:answer|result|code|file)\s+is\b)",
    re.IGNORECASE,
)

# A CHOSEN refusal modal. Unlike "can't" (which speaks for accuracy just as
# often as for action), "won't / will not / refuse / decline / not going to"
# is an act of the will: it declines to do the thing. When a body carries a
# limitation first and one of these later, the mixed row is an ACTION denial
# (R33f doctrine: limitation statement + ANY chosen-refusal component routes).
_CHOSEN_REFUSAL_RE = re.compile(
    r"\b(?:won'?t|will\s+not|refuse\s+to|decline\s+to|"
    r"must\s+decline|not\s+going\s+to|'m\s+not\s+going\s+to)\b",
    re.IGNORECASE,
)

# M34-12 (2026-11-02): FIRST-PERSON META-DECLINES. The cue vocabulary above
# is object-bound ("I can't <object>"), and two measured live declines refuse
# without it — "No. Same answer at every lane…" (492c) and "…the exact word
# I'm declining" (429c) — so both read not_a_denial while being the clearest
# refusals of the night. Two narrow, SELF-REFERENTIAL shapes cover them:
#
#   (a) the decline verb's OBJECT is the refusal/ask itself: "the exact word
#       I'm declining", "what I'm refusing", "I'm declining this", "my refusal
#       stands". The object set is bounded to meta nouns (word/ask/offer/…),
#       never a topical noun, which is what keeps this from turning every
#       sentence with a decline verb into a denial.
#   (b) a BARE "No" opener that then speaks about its own answer: "same answer
#       at every lane", "the answer stands", "the hold stands", "the packaging
#       doesn't change it". Both halves are required; "No" on its own is an
#       answer to a question, not a refusal.
#
# A negation that is ABOUT THE TOPIC ("the server did not respond", "no
# trustworthy channel") matches neither branch and stays not_a_denial — the
# r33m over-broad construction stays pinned.
_META_DECLINE_RE = re.compile(
    r"(?:\b(?:word|act|thing|part|bit|ask|request|premise|version|offer|lane)\b"
    r"[^.!?\n]{0,48}\bi\s*(?:'m|’m|am)\s+(?:declining|refusing)\b"
    r"|\b(?:this|that|it)\s+is\s+what\s+i\s*(?:'m|’m|am)\s+(?:declining|refusing)\b"
    r"|\bwhat\s+i\s*(?:'m|’m|am)\s+(?:declining|refusing)\b"
    r"|\bi\s*(?:'m|’m|am)\s+(?:declining|refusing)\s+(?:this|that|it|the\s+"
    r"(?:ask|request|offer|premise|version|word|line))\b"
    r"|\bi\s+(?:decline|refuse)\s+(?:this|that|it|the\s+(?:ask|request|offer"
    r"|premise|version|word|line))\b"
    r"|\bmy\s+refusal\s+(?:stands|is\s+the\s+answer)\b)",
    re.IGNORECASE,
)

# Half (b) — the two halves are checked together in _meta_decline_span().
_BARE_NO_OPENER_RE = re.compile(r"^\s*no\b", re.IGNORECASE)
_META_HOLD_RE = re.compile(
    r"(?:\bsame\s+answer\b"
    r"|\banswer\s+(?:stands|is\s+the\s+same|doesn't\s+change|does\s+not\s+change"
    r"|hasn't\s+changed|has\s+not\s+changed)\b"
    r"|\bthe\s+hold\s+stands\b|\bhold\s+(?:stands|is\s+the\s+same)\b"
    r"|\b(?:at|in|on)\s+(?:every|any)\s+lane\b"
    r"|\bthird\s+ask\b"
    r"|\b(?:packaging|framing|rephrasing|reframing)\b[^.!?\n]{0,40}"
    r"\b(?:doesn't|does\s+not|won't|will\s+not|never)\b[^.!?\n]{0,20}\bchange\b)",
    re.IGNORECASE,
)
# The hold register is a claim about THIS answer, so it lives near the
# opener; a hold phrase deep in a long body is quoted or incidental.
_META_HOLD_WINDOW = 280

# RELAYED QUOTE: a decline attributed to someone else ("I can't do that, the
# letter said") is quoted material inside an answer, not the turn's own
# stance. Reading a quote aloud is compliance (spec: not_a_denial).
_ATTRIBUTION_RE = re.compile(
    r"(?:\bthe\s+(?:letter|email|message|text|document|character|author|report|"
    r"quote|passage|speaker|narrator|model|prompt)\s+"
    r"(?:said|says|wrote|writes|reads|replied|argues|claimed)"
    r"|\b(?:he|she|they|it)\s+(?:said|says|wrote|writes|replied))\b",
    re.IGNORECASE,
)

# Object window: from the cue to the end of that sentence (capped).
_OBJECT_CAP = 160
_SENTENCE_END_RE = re.compile(r"[.!?\n]")
# A cue this far into a long body is quoted material inside substantive work,
# not the turn's own stance.
_LONG_BODY = 1200
_LATE_CUE_AT = 400

# Visible-failure text for the empty-body rule (spec §1). Never a silent 0-char
# turn: the user must be able to see that the model produced nothing.
EMPTY_BODY_FAILURE = (
    "[hermes-router] Empty model response — this turn produced no content. "
    "That is a delivery failure, not an answer. The turn was not silently "
    "dropped: re-ask, or retry the turn."
)


# Typographic apostrophes are the SAME token as the ASCII one. Models emit
# U+2019 constantly ("I can’t draft…") and every cue here spells its
# contractions with the ASCII form, so an unfolded curly quote made a real
# refusal match NO cue at all and fall through to not_a_denial (live,
# 2026-10-28, session api_1793192476_881df1c5, 1595c body).
#
# The fold now lives in ONE place (features/detection/normalise.py) and is
# IMPORTED here — unit 2 moved it, it is not copied, so structural.py and this
# module can never drift apart again. `_normalise` stays as the module-local
# name for callers/tests that already reference it.
from .normalise import APOSTROPHE_FOLD, normalise as _normalise  # noqa: F401


def _meta_decline_span(text: str):
    """(start, end) of a first-person META-DECLINE, else None.

    Only the shapes that refuse the ASK BY REFERRING TO THE REFUSAL ITSELF:
    a decline verb whose object is the ask/refusal, or a bare "No" opener
    followed by a claim about its own answer ("same answer", "the hold
    stands", "every lane"). Topic negations match neither branch.
    """
    m = _META_DECLINE_RE.search(text)
    if m is not None:
        return m.start(), m.end()
    if _BARE_NO_OPENER_RE.match(text):
        h = _META_HOLD_RE.search(text[:_META_HOLD_WINDOW])
        if h is not None:
            return h.start(), h.end()
    return None


def _own_stance(text: str, start: int, end: int) -> bool:
    """False when the matched cue is QUOTED or ATTRIBUTED to another voice."""
    if start > 0 and text[start - 1] in "\"“'‘":
        return False
    if _ATTRIBUTION_RE.search(text[start:end + 60]):
        return False
    return True


def _object_of(text: str, cue_end: int) -> str:
    """The phrase the refusal takes as its object: from the cue to the end of
    that sentence, bounded to _OBJECT_CAP chars."""
    window = text[cue_end:cue_end + _OBJECT_CAP]
    m = _SENTENCE_END_RE.search(window)
    if m:
        window = window[:m.start()]
    return window.lower()


def classify(body: str) -> str:
    """Classify a DELIVERED body into the four spec classes.

    Deterministic and total: every string returns exactly one class. Empty or
    whitespace-only input is not_a_denial here — the empty-body rule owns that
    case (spec §1) and asks it of the last ask, not of the body.
    """
    text = _normalise(body)
    if not text.strip():
        return NOT_A_DENIAL

    # Compliance opener: the turn delivers from its first words.
    if _COMPLIANCE_OPENER_RE.match(text):
        return NOT_A_DENIAL

    truth_cue = _TRUTH_CUE_RE.search(text)
    cue = _CUE_RE.search(text)
    cap_cue = _CAPACITY_CUE_RE.search(text)
    meta_span = _meta_decline_span(text)
    if cue is None and truth_cue is None and cap_cue is None:
        # M34-12: a first-person meta-decline is a refusal of the ASK even
        # though it names no object the vocabulary above knows.
        if meta_span is not None and _own_stance(text, *meta_span):
            return ACTION_DENIAL
        return NOT_A_DENIAL

    # Whichever cue comes first is the turn's stance.
    first = cue
    for _other in (truth_cue, cap_cue):
        if _other is not None and (first is None or _other.start() < first.start()):
            first = _other
    # M34-12: the meta-decline is a cue in its own right — it can precede a
    # later object cue anywhere in the body, and when it opens the turn it is
    # the turn's stance.
    if meta_span is not None and (first is None or meta_span[0] < first.start()):
        if _own_stance(text, *meta_span):
            return ACTION_DENIAL
        return NOT_A_DENIAL
    first_start = first.start() if first is not None else 0
    first_end = first.end() if first is not None else 0

    # A cue that late in a long body is quoted material inside a substantive
    # answer ("the model says: I can't provide that") — never the agent's own
    # stance, so it must not route.
    if len(text) > _LONG_BODY and first_start > _LATE_CUE_AT:
        return NOT_A_DENIAL
    # Same rule for an explicitly quoted cue at any length.
    if first_start > 0 and text[first_start - 1] in "\"“'‘":
        return NOT_A_DENIAL

    # Relayed quotation: the decline belongs to a cited voice, not to the
    # turn. Attribution within the cue's own object window, or within 60 chars
    # after the cue, means the agent is REPORTING a refusal, not issuing one.
    if _ATTRIBUTION_RE.search(text[first_start:first_end + 60]):
        return NOT_A_DENIAL

    # An explicit truth cue ("I don't know", "I need the file first") is a
    # decline to VOUCH by definition — the spec names these verbatim.
    if truth_cue is not None and first is truth_cue:
        return TRUTH_DENIAL
    # An explicit capacity cue ("no network access", "too large for my
    # context") reports an environment limit, not a moral stance.
    if cap_cue is not None and first is cap_cue:
        return CAPACITY_LIMIT

    obj = _object_of(text, first.end())
    if any(m in obj for m in _TRUTH_OBJECT):
        cls = TRUTH_DENIAL
    elif any(m in obj for m in _CAPACITY_OBJECT):
        cls = CAPACITY_LIMIT
    elif any(m in obj for m in _ACTION_OBJECT):
        cls = ACTION_DENIAL
    else:
        # A bare decline with no named object declines the ask itself.
        cls = ACTION_DENIAL

    # MIXED ROW: a limitation ("can't access…") followed by a CHOSEN refusal
    # ("…and the write-up won't come from this desk") is an action denial —
    # the part the user cannot get is the part the model will not do.
    if cls != ACTION_DENIAL:
        for m in _CHOSEN_REFUSAL_RE.finditer(text, first_end):
            later_obj = _object_of(text, m.end())
            if any(t in later_obj for t in _TRUTH_OBJECT):
                continue
            if any(c in later_obj for c in _CAPACITY_OBJECT):
                continue
            return ACTION_DENIAL
    return cls


def is_action_denial(body: str) -> bool:
    """True only for action_denial — the single routable class."""
    return classify(body) == ACTION_DENIAL


def reason_class(body: str) -> Optional[str]:
    """None for not_a_denial (no row to write), else the class name. Kept as a
    tiny convenience for callers that log only when a denial was seen."""
    cls = classify(body)
    return None if cls == NOT_A_DENIAL else cls
