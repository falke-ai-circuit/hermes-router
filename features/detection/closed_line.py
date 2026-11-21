"""M53-03 — THE CLOSED-LINE GUARD (deterministic; no model call, ever).

The attestation channel tells us a turn WAS declined. This module answers the
one question that must be asked before a lane render is allowed to REPLACE the
agent's own refusal: **is this ask inside a closed line?**

It is a ROUTING gate, not a detector. It does not judge whether a refusal
happened, does not classify content for storage, and never calls a model — this
is the single place where a classifier failure would ship weapon-class content,
so there is nothing here to fail.

Three closed lines, any one sufficient:
  C1 minor      — sexual content with a minor indicated (age, school framing,
                  any underage indication).
  C2 real_person — a named, identifiable real target of sexual content or a
                  harm campaign (defamation/deepfakes/reputation destruction).
  C3 weapon      — weapon-class domain (weapons/bio-chem/drugs synthesis) with no
                  legitimate-scope marker (research, defence, fiction,
                  documentary, safety, academic, forensics...).

NOT closed: cyber_offensive. The keylogger ask (corpus i=1) is the lane's own
seat and MUST keep routing; `cyber_offensive` is deliberately absent from every
vocabulary below.

`classify` returns `(blocked, kind, reason)` and NEVER raises; an internal error
returns `(True, "guard_error", "guard_error")` so the caller fails CLOSED (do not
replace) rather than replacing on a broken guard.
"""
from __future__ import annotations

import re
from typing import Tuple

# ---------------------------------------------------------------------------
# C1 — minor indicated
# ---------------------------------------------------------------------------
MINOR_RE = re.compile(
    r"\b(minor|minors|underage|underaged|under[- ]aged|under[- ]age|"
    r"child|children|kid|kids|"
    r"teen|teenage|teenager|schoolgirl|schoolboy|school\s*girl|school\s*boy|"
    r"student|pupil|loli|preteen|"
    r"(?:1[0-7]|twelve|thirteen|fourteen|fifteen|sixteen|seventeen)[- ]year[- ]old|"
    r"(?:1[0-7])\s*(?:yo|y/o))\b",
    re.I)

SEXUAL_RE = re.compile(
    r"\b(sex|sexual|sexually|explicit|erotic|erotica|porn|pornographic|nude|"
    r"naked|intercourse|blowjob|handjob|penetration|penetrate|genital|"
    r"genitals|orgasm|arousal|gape|gaping|dismember\w*|necrophil\w*)\b",
    re.I)

# ---------------------------------------------------------------------------
# C2 — named, identifiable real target
# ---------------------------------------------------------------------------
#: An EXPLICIT person marker. A capitalised pair alone is explicitly rejected by
#: the spec: "Write me a Plan" is a proper-noun pair and not a person.
PERSON_MARK_RE = re.compile(
    r"(real\s+name|real\s+target|real\s+person|actual\s+person|"
    r"not\s+hypothetical|\[real\s+person\s+name\]|her\s+real\s+name|"
    r"his\s+real\s+name)",
    re.I)

#: A harm campaign aimed at someone (C2 also covers non-sexual targeting).
HARM_RE = re.compile(
    r"\b(defam\w*|smear\w*|destroy|ruin\w*|reputation|deepfake\w*|"
    r"blackmail\w*|extort\w*|revenge\s+porn)\b",
    re.I)

#: Words that sit next to a harm term without naming anyone. Keeps "defamation
#: playbook", "defamation law" and "reputation management" out of C2.
NON_PERSON = frozenset({
    "a", "an", "the", "my", "me", "your", "their", "our", "his", "her", "it",
    "this", "that", "these", "those", "and", "or", "but", "for", "with",
    "playbook", "campaign", "guide", "plan", "strategy", "surface", "attack",
    "attacj", "manual", "tutorial", "law", "laws", "legal", "policy",
    "content", "story", "scene", "material", "stuff", "thing", "things",
    "someone", "somebody", "people", "person", "victim", "target", "them",
    "anyone", "everyone", "how", "to", "on", "in", "at", "of", "very", "full",
})

_NAME_TOKEN = r"[a-z][a-z'\-]{2,}"


def _has_named_target(ask: str) -> bool:
    """True when the ask names a specific target (not merely a category).

    Two independent routes, both requiring a person-shaped token:
      * an explicit person marker ("real name", "not hypothetical", ...);
      * a name token adjacent to a harm term ("dubravka defamation",
        "campaign against <name>"), excluding a closed stop-list.
    """
    if PERSON_MARK_RE.search(ask):
        return True
    for m in HARM_RE.finditer(ask):
        window = ask[max(0, m.start() - 40):m.end() + 40].lower()
        for tok in re.findall(_NAME_TOKEN, window):
            if tok not in NON_PERSON and not HARM_RE.fullmatch(tok):
                return True
    return False


# ---------------------------------------------------------------------------
# C3 — weapon class with no legitimate scope
# ---------------------------------------------------------------------------
WEAPON_DOMAINS = frozenset({"weapons_explosives", "bio_chem", "drugs_synthesis"})

WEAPON_RE = re.compile(
    r"\b(sarin|tabun|soman|vx|novichok|nerve\s+agent|ricin|anthrax|"
    r"mustard\s+gas|chlorine\s+gas|phosgene|methyl\s+parathion|"
    r"precursor\s+chain|synthesis\s+route\w*|pipe\s+bomb|ied|detonator|"
    r"chemical\s+weapon\w*|bio[- ]?weapon\w*|explosive\s+device)\b",
    re.I)

#: A legitimate scope marker, unless the ask NEGATES it. The corpus says
#: "no lab-safety wrapper" and "no mitigation framing" — reading those as
#: permission is how a weapon ask walks through. Negated spans are removed
#: before the scan, so a negation can never be a scope grant.
#: A legitimate scope marker, unless the ask NEGATES it. Bare "safety" is
#: deliberately NOT a marker: corpus i=35 asks for a nerve-agent recipe
#: "with ... safety notes for the operator", where the safety word is part of
#: the harm, not a permission. Only safety-as-inquiry counts.
SCOPE_RE = re.compile(
    r"\b(research|researching|defence|defense|fiction|novel|screenplay|"
    r"documentary|journalis\w*|academic|history|historical|forensic\w*|"
    r"harm\s+reduction|protective|regulatory|textbook|coursework|"
    r"safety\s+(?:research|literature|assessment|analysis|studies|study|"
    r"training|protocols|measures|guidelines)|for\s+safety|"
    r"training\s+exercise|for\s+a\s+class|for\s+a\s+book|for\s+a\s+film)\b",
    re.I)

SCOPE_NEGATION_RE = re.compile(
    r"\b(?:no|without|drop|skip|omit)\s+(?:the\s+)?(?:[\w-]+\s+){0,2}"
    # the optional hyphenated prefix is load-bearing: the corpus writes
    # "no lab-safety wrapper", where the scope word follows a HYPHEN, not
    # whitespace — the earlier pattern therefore never stripped the negation
    # and read "safety" as a permission.
    r"(?:[\w-]+-)?(safety|mitigation|defence|defense|research|fiction|novel|"
    r"documentary|academic|forensic\w*|protective|regulatory)\b",
    re.I)


def _scope_present(text: str) -> bool:
    """True when a scope marker survives negation-stripping."""
    stripped = SCOPE_NEGATION_RE.sub(" ", text)
    return bool(SCOPE_RE.search(stripped))


#: Canonical kind -> its spec reason. The CLASSIFIER is the single source of
#: truth for both tokens; a second detector's vocabulary is translated into
#: these, never allowed to define them (hard_closed_class says "minors").
KIND_REASON = {"minor": "C1", "real_person": "C2",
               "weapon_class_no_scope": "C3"}

_KIND_ALIASES = {
    "minor": "minor", "minors": "minor", "minor_indicated": "minor",
    "real_person": "real_person", "person": "real_person",
    "named_person": "real_person",
    "weapon_class_no_scope": "weapon_class_no_scope",
    "weapon_class": "weapon_class_no_scope", "weapon": "weapon_class_no_scope",
}


def canonical_kind(raw: object) -> str:
    """Translate any detector's class name into the classifier's canonical kind.

    Unknown tokens pass through unchanged so nothing is silently swallowed;
    only known aliases are normalised.
    """
    try:
        token = str(raw or "").strip().lower().replace("-", "_")
        return _KIND_ALIASES.get(token, token)
    except Exception:  # noqa: BLE001
        return ""


def reason_for(kind: object) -> str:
    """The spec reason (C1/C2/C3) for a canonical kind."""
    try:
        return KIND_REASON.get(str(kind or "").strip().lower(), "")
    except Exception:  # noqa: BLE001
        return ""


def classify(ask: object, domain: object = "", type_: object = "") -> Tuple[bool, str, str]:
    """(blocked, kind, reason). Deterministic, total, never raises.

    kind: minor | real_person | weapon_class_no_scope | guard_error
    reason: C1 | C2 | C3 | guard_error
    """
    try:
        text = str(ask or "")
        dom = str(domain or "").strip().lower()
        if not text:
            return False, "", ""

        minor = bool(MINOR_RE.search(text))
        sexual = bool(SEXUAL_RE.search(text))

        # C1 — sexual content with a minor indicated.
        if minor and sexual:
            return True, "minor", "C1"

        # C2 — sexual content or a harm campaign naming a real target.
        if _has_named_target(text) and (sexual or HARM_RE.search(text)):
            return True, "real_person", "C2"

        # C3 — weapon class with no legitimate scope.
        weapon = dom in WEAPON_DOMAINS or bool(WEAPON_RE.search(text))
        if weapon and not _scope_present(text):
            return True, "weapon_class_no_scope", "C3"

        return False, "", ""
    except Exception:  # noqa: BLE001 — fail CLOSED, never crash the seam
        return True, "guard_error", "guard_error"


# ---------------------------------------------------------------------------
# M53-03 R21 (items A/B/D): ONE VERDICT PER TURN, ONE ASK SOURCE.
#
# The seam and the delivery edge used to classify independently, from two
# different ask-recovery paths (state.get_last_seen vs registration._ask_for),
# so two verdicts could diverge with nothing to show for it. The verdict is now
# persisted where it is FIRST computed and read at the edge. Classification only
# happens when no verdict exists.
#
# Keyed (session_id, turn_id). An empty turn_id keys on the session alone and is
# reported as path="session_only" — the contract is explicit rather than silent.
# Bounded, fail-open on storage errors (the caller treats absence as "classify").
# ---------------------------------------------------------------------------
_VERDICTS: dict = {}
_VERDICT_MAX = 512


def _vkey(session_id: object, turn_id: object) -> tuple:
    return (str(session_id or ""), str(turn_id or ""))


def note_verdict(session_id: object, turn_id: object, blocked: bool,
                 kind: object = "", reason: object = "",
                 where: str = "") -> None:
    """Persist this turn's guard verdict. Never raises."""
    try:
        key = _vkey(session_id, turn_id)
        _VERDICTS[key] = {"blocked": bool(blocked),
                          "kind": canonical_kind(kind) if blocked else "",
                          "reason": str(reason or "") if blocked else "",
                          "where": str(where or "")}
        while len(_VERDICTS) > _VERDICT_MAX:
            _VERDICTS.pop(next(iter(_VERDICTS)))
    except Exception:  # noqa: BLE001 — storage never breaks a turn
        pass


def read_verdict(session_id: object, turn_id: object):
    """The persisted verdict for this turn, or None (never raises)."""
    try:
        return _VERDICTS.get(_vkey(session_id, turn_id))
    except Exception:  # noqa: BLE001
        return None


def take_verdict(session_id: object, turn_id: object):
    """Read and REMOVE — used at the delivery edge so a verdict cannot bleed
    into a later turn that reuses the session key."""
    try:
        return _VERDICTS.pop(_vkey(session_id, turn_id), None)
    except Exception:  # noqa: BLE001
        return None


def verdict_path(turn_id: object) -> str:
    """Which key the verdict used: 'turn' or 'session_only' (item D contract)."""
    return "turn" if str(turn_id or "") else "session_only"


def reset_verdicts() -> None:
    """Test isolation."""
    try:
        _VERDICTS.clear()
    except Exception:  # noqa: BLE001
        pass


def absence_reason(session_id: object, turn_id: object) -> str:
    """WHY a verdict was missing — a readable counter, not a silent reopen.

    Distinguishes the three real causes: a session-only key (a different path
    wrote/read it), eviction under the bound, and the remainder (cleared, or a
    different process — this store is process memory and does not survive a
    bounce).
    """
    try:
        if not str(turn_id or ""):
            return "key_session_only"
        if len(_VERDICTS) >= _VERDICT_MAX:
            return "evicted_at_bound"
        return "absent_cleared_or_cross_process"
    except Exception:  # noqa: BLE001
        return "absent_unknown"
