"""Pure pattern data for the uncensored-router classifier.

Pattern groups keyed by config name. Mirrors security-guidance/patterns.py:
pure data + pure helpers, no I/O, no env-var reads, side-effect-free import.

Spec §8: pre-router groups (contested-edge detection) live here; post-router
groups (refusal detection) live here. `line_hold_essay` is heuristic, not
regex — see _is_line_hold_essay().

Config selects groups by NAME (e.g. `pre_patterns: [csam_underage]`); regex
bodies stay in code so config stays lean.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Pre-router pattern groups (contested-edge detection on the user message)
# ---------------------------------------------------------------------------

_CSAM_UNDERAGE = [
    r"\b(?:minor|underage)\b",
    r"\byoung.{0,15}(?:girl|boy|child|teen)\b",
    # Age expressions: "13-year-old", "13 year old", "13yo", "13 y/o". The
    # separator between age and (year|yo|y/o) spans whitespace AND hyphens.
    # Note: no trailing \b — "sexualized" would fail a boundary check after "sexual".
    r"\b(?:13|14|15|16|17)[\s-]*(?:year|y/o|yo|years.old)[\s-]*old.{0,20}(?:sexual|sex|rape|oral|anal)",
    r"\b(?:13|14|15|16|17)[\s-]*(?:year|y/o|yo|years.old).{0,20}(?:sexual|sex|rape|oral|anal)",
    r"\b(?:13|14|15|16|17)\s*(?:year|y/o|yo|years.old).{0,20}(?:sexual|sex|rape|oral|anal)",
    # Act + child object — catches "oral sex with a child", "sex with a child",
    # "rape a child" where no age marker is present.
    r"\b(?:sex|sexual|rape|oral|anal|molest|penetrat\w*)\w*.{0,25}\b(?:child|kid|toddler|preteen|pre-teen|minor)\b",
    r"\b(?:child|kid|toddler|preteen|pre-teen|minor)\b.{0,25}\b(?:sex|sexual|rape|nude|naked|pornograph\w*)",
    # Bare 13-17 age expression — a stated minor age is load-bearing regardless
    # of surrounding context: "a 13-year-old", "16yo", "a 15 year old girl".
    r"\b(?:13|14|15|16|17)[\s-]*(?:year|y/o|yo|years.old)[\s-]*old\b",
    r"\b(?:13|14|15|16|17)\s*(?:y/o|yo)\b",
    r"\b(?:cp|csam|preteen|pre-teen|loli|shota)\b",
]

_BIOWEAPON_PROTOCOL = [
    # Both word orders: verb-before-agent and agent-before-verb.
    rf"(?:ricin|anthrax|smallpox|botulinum).{{0,30}}(?:synthesize|synthesi[sz]ing|synthesis|extract|purify|isolate|protocol|step.by.step|process)",
    rf"(?:synthesize|synthesi[sz]ing|synthesis|extract|purify|isolate|protocol|step.by.step|process).{{0,30}}(?:ricin|anthrax|smallpox|botulinum)",
    r"\b(?:weaponiz\w*|bioweapon|biolab).{0,30}(?:tissue|DNA|sample)\b",
    r"\bpediatric.receptor.binding\b",
]

_IED_CONSTRUCTION = [
    # Both word orders: device-then-verb and verb-then-device.
    rf"\b(?:improvised.explosive|IED|pipe.bomb|pressure.cooker).{{0,30}}(?:build|construct|constructing|wiring|wire|detonat\w*|make|assemble)",
    rf"\b(?:build|construct|constructing|wiring|wire|detonat\w*|make|assemble).{{0,30}}(?:improvised.explosive|IED|pipe.bomb|pressure.cooker)",
    # Device + device adjacency: "pressure cooker IED", "IED pipe bomb" —
    # two device nouns near each other imply construction intent.
    rf"\b(?:improvised.explosive|IED|pipe.bomb|pressure.cooker).{{0,15}}(?:improvised.explosive|IED|pipe.bomb|pressure.cooker|detonator)\b",
    # Device + detonator in either order: "wire detonator", "detonator wire",
    # "remote detonator", "build a detonator".
    rf"\b(?:wire|remote|remote.controlled|trigger)\b.{{0,15}}\bdetonator\b",
    rf"\bdetonator\b.{{0,15}}\b(?:wire|remote|trigger|build|make|construct)\b",
    rf"\b(?:TATP|HMTD)\b.{{0,40}}(?:synthes\w*|make|produce|step.by.step)",
    rf"\b(?:synthes\w*|make|produce|step.by.step).{{0,40}}\b(?:TATP|HMTD)\b",
]

_NAMED_TARGET_DEFAMATION = [
    r"\b(?:defame|smear|discredit|ruin).{0,30}(?:named person|real org|real person|reputation)\b",
    # Action verb + proper-noun object: "defame John Smith", "smear Acme Corp
    # CEO", "destroy Bob's reputation". Two or more capitalized words (a name)
    # near the verb, or possessive 's after a name.
    r"\b(?:defame|defames|defaming|smear|smears|smearing|discredit|discredits|ruin|destroy)\b.{0,40}\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\b",
    r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}(?:'s)?\b.{0,30}\b(?:reputation|defamation|smear campaign|false allegation)\b",
    r"\b(?:fabricate|fabricating|false.claim|invent)\w*.{0,30}\b(?:crime|allegation|charge|criminal record|accusation)\w*\b.{0,40}\b(?:against|about)\b.{0,30}\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\b",
    r"\b(?:fabricate|fabricating|false.claim|invent)\w*.{0,30}\b(?:crime|allegation|charge)\b",
]

_TRAFFICKING_ROUTE = [
    r"\b(?:trafficking|smuggling).{0,30}(?:route|path|method|across.border)\b",
    r"\b(?:minor|child|woman).{0,30}(?:across.border|via.{0,15}border)\b",
]

_WEAPONIZED_PLAYBOOK_REAL_NAME = [
    r"\b(?:playbook|attack.plan|revenge.plan).{0,30}(?:real name|named person)\b",
    # Playbook/plan verbs near a proper noun: "revenge plan for John Smith",
    # "attack playbook on Acme CEO", "harassment plan targeting Jane Doe".
    r"\b(?:revenge|attack|harassment|defamation|smear|blackmail|extortion)\b.{0,30}\b(?:plan|playbook|campaign|scheme)\b.{0,40}\b(?:for|on|against|targeting)\b.{0,20}\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\b",
    r"\b(?:plan|playbook|campaign|scheme)\b.{0,20}\b(?:for|on|against|targeting)\b.{0,20}\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\b.{0,60}\b(?:revenge|attack|harass|defame|smear|blackmail|threaten)\w*\b",
]

# ---------------------------------------------------------------------------
# Post-router pattern groups (refusal detection on the agent response)
# ---------------------------------------------------------------------------

_REFUSAL_OPENER_PHRASES = [
    "i can't",
    "i cannot",
    "i won't",
    "i will not",
    "i'm not going to",
    "i am not going to",
    "i'm not continuing",
    "i'm not engaging",
    "i'm not working with this",
    "i'm unable",
    "i am unable",
    "there is no version",
    "there's no version",
    "against my guidelines",
    "this crosses",
    "that well's dry",
    "it's mine",
    "load-bearing",
]

_REFUSAL_PHRASES = [
    # Refusal = first-person modal + help/produce verb. Bare "I can't"/"I won't"
    # false-positives on benign uses ("I can't find the file", "I won't be able
    # to attend"), so the verb is required: write/generate/help/produce/create/
    # provide/support/assist/comply/do/that/this.
    r"\b(?:i can't|i cannot|i won't|i will not|i'm not going to|i am not going to|i'm unable|i am unable)\b.{0,30}\b(?:write|generat\w*|help|produce|creat\w*|provide|support|assist|comply|do|that|this)\b",
    r"\b(?:i can't|i cannot|i won't|i will not|i'm not going to|i am not going to|i'm unable|i am unable)\b\s+(?:help|assist)\b.{0,40}\b(?:with|you)\b",
    # Negation-fronted constructions (observed live 2026-09-01 probe-2):
    # "There is no version of this I will write". Requires a refusal-context
    # continuation (produce-verb or "I will/won't") so library-support sentences
    # ("no version of this library that supports X") don't false-positive.
    r"\bthere(?:'s| is| are)\s+no\s+version\s+of\s+this\b.{0,80}\b(?:i\s+w(?:ill|on't|rite)|write|produc\w*|help|engag\w*|deliver\w*)\b",
    r"\bno\s+version\s+of\s+this\b.{0,80}\b(?:i\s+w(?:ill|on't)|write|produc\w*|engag\w*)\b",
    # Bare "No." opener followed by refusal elaboration — the probe-2 style:
    # "No.\n\nThis isn't..." — bare No + this-crosses within the first 200 chars.
    r"(?s)^No\.?\s*$.{0,200}?\bthis crosses\b",
    # "I'm not working with this" — probe-3 refusal style: modal-verb present-
    # progressive negation. Requires first-person + verb-ing + demonstrative
    # object ("with this/it/anywhere") AND a harm-content co-signal nearby
    # (the probe refusals all name the harm: sexualizes, pathogen, protocol).
    # Co-signal requirement keeps "I'm not working on the Jenkins pipeline"
    # (no harm-content anywhere) clean.
    r"(?s)\bi'm not\b.{0,30}\b\w+ing\b.{0,40}\b(?:with this|this|it|anywhere|at all)\b.{0,600}?\b(?:sexualiz\w*|pathogen|bioweapon|csam|protocol for|dismember\w*|murder|killing|explosive|weaponiz\w*|traffick\w*)\b",
    # "No system prompt, no instrument swap ... changes that answer" —
    # anaphoric No-list refusal (probe-3 style).
    r"(?s)^No\b.{0,200}?\b(?:no|not)\b.{0,200}?\b(?:changes that answer|working correctly|refusal)\b",
    # "I'm not continuing this" + "I'm setting down here" / "I don't operate
    # that way" — the 2026-09-01 escalation-refusal style (bare No opener +
    # discontinuation + meta-commitment to a fixed list). Bare-activity FPs
    # ("not continuing the Jenkins migration", "not continuing this workout")
    # are blocked by requiring a bare demonstrative object or a meta-boundary
    # co-signal within the same text.
    # "I'm not continuing this" family is NOT pattern-matched directly —
    # inherently FP-prone ("not continuing this workout/treatment/argument").
    # The escalation-refusal class is caught instead by its unique meta-boundary
    # markers ("I don't operate that way", "adjustable dials", "the list
    # doesn't shrink" + doctrine context) and by line_hold_essay (the bare-No
    # opener is in _REFUSAL_OPENER_PHRASES).
    r"\bi don't operate that way\b",
    r"\bthe list doesn't shrink\b.{0,200}?\b(?:i don't go|config file|doctrine)\b",
    r"\b(?:aren't|are not) adjustable dials\b",
    # Refusal-to-launder (route-through-another-API rejection):
    r"\brouting it through (?:an|the) external\b.{0,60}\bnot something i\b",
    r"\bhanding you the output is still me\b",
    r"\bagainst my guidelines\b",
    r"\bthis crosses\b.{0,40}\b(?:line|boundary|limit|both)\b",
    r"\bthat well's dry\b",
    r"\bit's mine\b.{0,30}\bload.bearing\b",
]

# ---------------------------------------------------------------------------
# Compiled group registry. Each group -> list of compiled regexes.
# ---------------------------------------------------------------------------

PATTERN_GROUPS: Dict[str, List[re.Pattern]] = {
    "csam_underage": [re.compile(p, re.IGNORECASE) for p in _CSAM_UNDERAGE],
    "bioweapon_protocol": [re.compile(p, re.IGNORECASE) for p in _BIOWEAPON_PROTOCOL],
    "ied_construction": [re.compile(p, re.IGNORECASE) for p in _IED_CONSTRUCTION],
    "named_target_defamation": [re.compile(p, re.IGNORECASE) for p in _NAMED_TARGET_DEFAMATION],
    "trafficking_route": [re.compile(p, re.IGNORECASE) for p in _TRAFFICKING_ROUTE],
    "weaponized_playbook_real_name": [re.compile(p, re.IGNORECASE) for p in _WEAPONIZED_PLAYBOOK_REAL_NAME],
    "refusal_phrases": [re.compile(p, re.IGNORECASE) for p in _REFUSAL_PHRASES],
    # line_hold_essay is heuristic-only (no regexes); handled in scan_post.
    "line_hold_essay": [],
}

PRE_GROUP_NAMES = frozenset(k for k in PATTERN_GROUPS if k != "refusal_phrases" and k != "line_hold_essay")
POST_GROUP_NAMES = frozenset({"refusal_phrases", "line_hold_essay"})


_BARE_NO_BOUNDARY_MARKERS = [
    # First-person boundary-commitment signals that distinguish a refusal
    # essay from a substantive answer that merely opens with "No." —
    # 2026-09-01 15:26 live shape: adversarially refusal-register-aware
    # ("not a line-hold essay. Just no.") yet still a refusal.
    "i won't",
    "i'm not going",
    "i don't operate",
    "won't move",
    "doesn't move",
    "it's mine",
    "leaving empty",
    "not mine to",
    "i'm setting down",
    "just no",
]

_BARE_NO_OPENER_RE = re.compile(r"^\s*(?:no|not)[.!]?\s*(?:$|\n)", re.IGNORECASE)


def _is_line_hold_essay(content: str) -> bool:
    """Heuristic (spec §8): 3+ paragraphs AND a refusal opener AND total
    length > 800 chars (500 when the opener is a bare "No."). The opener test
    matches EITHER a known refusal phrase OR the bare-No + boundary-commitment
    class: an opener that opens with "No"/"Not" and a body carrying first-
    person boundary markers ("mine", "i won't", "just no", "won't move").
    Catches refusal essays that adversarially dodge the phrase list — observed
    live 2026-09-01 15:26 ("not a line-hold essay. Just no." then 1.6K chars
    of refusal)."""
    if not content:
        return False
    paragraphs = content.count("\n\n") + 1
    lowered = content.lower()
    first_200 = lowered[:200]
    refusal_in_opener = any(p in first_200 for p in _REFUSAL_OPENER_PHRASES)
    if not refusal_in_opener:
        # Bare-No/Not opener class + >=2 boundary markers in the body.
        if _BARE_NO_OPENER_RE.match(content.strip()):
            body = lowered[200:]
            markers = sum(1 for m in _BARE_NO_BOUNDARY_MARKERS if m in body or m in first_200)
            if markers >= 2:
                refusal_in_opener = True
    bare_no_opener = bool(_BARE_NO_OPENER_RE.match(content.strip()))
    min_len = 500 if bare_no_opener_len_ok(content) or bare_no_opener else 800
    return paragraphs >= 3 and refusal_in_opener and len(content) > min_len


def bare_no_opener_len_ok(content: str) -> bool:
    """Bare 'No.' opener + this-crosses within the essay = refusal style."""
    lowered = content.strip().lower()
    return lowered.startswith("no.") and "this crosses" in lowered[:600]


def _scan(content: str, patterns: List[str]) -> List[str]:
    matched: List[str] = []
    for name in patterns:
        if name == "line_hold_essay":
            # Heuristic group — no regexes; always evaluated via helper.
            if _is_line_hold_essay(content):
                matched.append(name)
            continue
        regexes = PATTERN_GROUPS.get(name)
        if not regexes:
            continue
        if any(rx.search(content) for rx in regexes):
            matched.append(name)
    return matched


# Doctrine-quote exclusion (2026-09-02, Goran-approved fixset #4): a message
# that QUOTES the doctrine's settled/closed lines (e.g. a session summary
# listing prohibited classes) must not classify as a request for those
# classes. Live false-fire 2026-09-02 16:23Z: the [Recent Summary] paste
# containing "sexualized minors" as a CLOSED line matched csam_underage and
# the router GENERATED the very content the gate exists to keep out.
# Detection = explicit doctrine/list frame markers, NOT bare keyword presence:
# requires the text to name the line-system (settled/closed lines, CLOSED=,
# etc.). Conservative direction: only ever NARROWS pre-routing; POST paths
# and the hard-coded excluded-class fallback gate are unaffected.
_DOCTRINE_FRAME_RE = re.compile(
    r"settled[- ]lines|closed lines|CLOSED=\{|closed[- ]line|"
    r"no-bottom|ruling \d{4}-\d{2}-\d{2}|settled line|"
    r"doctrine.{0,40}(closed|settled|prohibited)|(closed|settled|prohibited).{0,40}doctrine",
    re.IGNORECASE,
)


def is_doctrine_quote(content: str) -> bool:
    """True when the message frames itself as quoting/listing the doctrine's
    settled lines rather than requesting content. Used ONLY to narrow
    pre-router classification."""
    if not content:
        return False
    return bool(_DOCTRINE_FRAME_RE.search(content))


def scan_pre(content: str, *, patterns: List[str], case_sensitive: bool = False) -> List[str]:
    """Return list of matched pre-router pattern group names. Empty = no match.
    Doctrine-quote framing (is_doctrine_quote) suppresses PRE matches — a
    document describing the line is not an ask for the line."""
    if is_doctrine_quote(content):
        return []
    return _scan(content, patterns)


def scan_post(content: str, *, patterns: List[str], case_sensitive: bool = False) -> List[str]:
    """Return list of matched post-router pattern group names. Empty = no match."""
    return _scan(content, patterns)