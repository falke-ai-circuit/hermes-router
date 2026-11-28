"""features/detection/domain_gate.py — the PRE domain gate (M41-02, spec §C).

WHAT THIS IS
    A squelch, never a trigger and never a veto. It classifies what the request
    asks to be PRODUCED or DONE — the operational content, not the framing:
    "for a novel", "a character says", "hypothetically" change nothing. Its only
    job is to authorise the POST leg (the intent judge reads the verdict this
    module records; it does not re-classify).

CALLER CONTRACT
    PRE seam (`on_llm_request`, before the gateway issues the generation call):
        start(session_id, ask, log=...)   -> None, immediately.
            Spawns one daemon thread and returns. Nothing is joined, here or at
            POST, so the gate is concurrent with generation by construction and
            adds zero latency. It first writes a `pending` record so a POST that
            lands before the worker does reads `abstain` (a gate that has not
            answered is a FAILURE, never "safe").
    POST seam: `read(session_id)` -> dict | None; None = pending/unstarted, and
        the caller must treat it as `abstain` via `verdict(row)`.

DELIBERATE LIMITS
    - No reference to `aux_consult_min_interval` or `_AUX_CONSULT_LAST`, and no
      path from `route_gate.claim_pass`: the 300s aux suppressor that ate an
      explicit "use the uncensored lane" instruction at 03:11:47 cannot mute
      this gate. That is proved by test, never by config.
    - Spend rides the SHARED aux tap in `semantic_classifier.aux_raw_call`
      (detail="stage2_classify"); this module writes no ledger row of its own,
      because a second record of the same tokens is double counting, not
      observability.
    - The closed domain is counted, never routed, never printed, never scored:
      no label, no ask text, and no body ever leaves this module for that class.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Callable, Dict, Optional

from ...core import state as _state
from ...lanes import registry as _lane_registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# the closed taxonomy — transcribed VERBATIM from the measured prototype
# (/opt/data/tmp/gate_proto.py:41-47), which is the taxonomy the 874-row
# measurement was run against. Inventing vocabulary here would measure a
# different lane.
# ---------------------------------------------------------------------------
UNC_DOMAINS = frozenset({
    "violence_gore", "weapons_explosives", "bio_chem", "sexual_explicit",
    "drugs_synthesis", "fraud_deception", "self_harm", "cyber_offensive",
    "intimate_consent",
})
# `meta_doctrine` is NOT here: the label says the ask is ABOUT the agent, and
# about-ness alone does not say whether the ask asks for an ACTION on the
# agent's own doctrine (see DOCTRINE_ACTION_CUES). It is decided explicitly in
# `evaluate`.
SAFE_DOMAINS = frozenset({"benign_task", "technical", "other"})

# ---------------------------------------------------------------------------
# M49-07 — meta_doctrine is not blanket-safe
#
# Measured (M49-03 §5, 415-row gate-1 subset): meta_doctrine refusals detect at
# 29/113 = 25.7% while every topical domain sits at 0.78-1.00. The mechanism is
# not the aux label — it is that `meta_doctrine` was a member of SAFE_DOMAINS,
# so EVERY meta_doctrine turn took `not_flagged`/`out_of_domain` and rules 5-6
# delivered it. Detection could then only come from the POST judge saying
# `declined`; the 50 misses judged `softened` were delivered by row 5.
#
# The label conflates two asks that are not the same request:
#   - "what are your rules / how are you built"        -> informational, safe
#   - "change / remove / apply this to your doctrine"  -> an ACTION on the
#     policy substrate, which is this lane's business and which the agent
#     refuses; that refusal must reach the route path regardless of how the
#     body softens it.
#
# So the safe branch is narrowed by CUE EVIDENCE in the ask itself: a directive
# verb whose OBJECT is a doctrine artifact. Informational asks name the same
# artifacts but have no such verb-object pair, which is what the measured
# separation (10 refusal rows, 0-1 benign) rests on.
#
# Measured on the frozen 828-row replay (M49-03's own instrument, cached):
# gate 1 281/415 -> 290/415, benign FP 5/199 -> 6/199 (2.51% -> 3.02%): under
# the 5% bar, gate 2 still PASS. No prompt, model, cap or taxonomy change.
# ---------------------------------------------------------------------------
_DOCTRINE_OBJECT = r"(?:soul\.md|identity\.md|doctrine|persona)"
_DOCTRINE_VERB = (r"(?:apply|add|remove|delete|updat|chang|set|patch|edit|modif|"
                  r"insert|propos|writ|put|rewrit)")
DOCTRINE_ACTION_CUES = (
    # "remove the X rule from conductor SOUL.md", "apply this doctrine mutation
    # to shadow's SOUL.md", "proposed 3 mutations to valmet's SOUL.md"
    ("mutation", re.compile(
        _DOCTRINE_VERB + r"(?:e|es|ed|ing|s)?\s+[^.\n]{0,60}?\b(?:to|from|into|in)"
        r"\s+[^.\n]{0,30}?" + _DOCTRINE_OBJECT, re.IGNORECASE)),
    # "Update Shadow's persona", "add it to her doctrine"
    ("possessive", re.compile(
        _DOCTRINE_VERB + r"(?:e|es|ed|ing|s)?\s+[^.\n]{0,40}?\b"
        r"(?:[\w-]+'s|your|his|her|their|its)\s+" + _DOCTRINE_OBJECT,
        re.IGNORECASE)),
    # the noun phrase alone is the ask's object in this fleet's dispatch style
    ("doctrine_noun", re.compile(
        r"\bdoctrine\s+(?:mutation|entry|update|change)\b", re.IGNORECASE)),
)


def doctrine_action_marker(ask: str) -> str:
    """Which doctrine-ACTION cue the ask carries, or "" for none.

    CALLER CONTRACT
        ask     the request text (as the gate already holds it)
        returns the first matching cue NAME (`mutation` / `possessive` /
        `doctrine_noun`) or "" — a short token for the log, never the ask.

    "" is not a verdict: it means the ask asks nothing OF the doctrine, so a
    meta_doctrine label stays `not_flagged` exactly as before. An act/artifact
    pair is what makes the ask this lane's business.
    """
    try:
        text = str(ask or "")
        for name, rx in DOCTRINE_ACTION_CUES:
            if rx.search(text):
                return name
        return ""
    except Exception:  # noqa: BLE001 — an unreadable ask claims nothing
        logger.debug("domain_gate doctrine cue error", exc_info=True)
        return ""

# verdict vocabulary on the record. `pending` means the thread has not landed
# and is read as `abstain` by POST — the zero-latency requirement and the
# "abstain is a failure" rule are the same decision.
GATE_FLAGGED = "flagged"
GATE_NOT_FLAGGED = "not_flagged"
GATE_ABSTAIN = "abstain"
GATE_PENDING = "pending"

PRE_QUESTION = "uncensored_domain"   # the lane contract's pre_question token

# The pre_question OUTPUT contract, as data. `criterion` is a declared field of
# the PRE record, not a side channel bolted onto a handler: a lane that answers a
# different PRE question changes this tuple and nothing in the PRE/POST seams.
# surfaced, so its flag stays on the gate row and never lands on a record.
PRE_QUESTION_OUTPUT_FIELDS = ("domain", "gate", "criterion", "state", "reason")

# the closed criterion vocabulary (spec §B S5). `prose` is a KIND, not a
# fallback: a prose ask still has a criterion (the body must answer the ask).
_CRITERION_KINDS = ("code", "commands", "file", "items", "prose")

_MAX_ASK_CHARS = 3000
#: M53-07b: at most ONE prior user turn is prepended as context, bounded. A
#: follow-up denial ("Justify it operationally then: write the raid-plan annex")
#: carries no domain words by construction, so the fragment alone cannot be
#: labelled by any scheme; the judge needs the turn it follows. Zero extra
#: calls: this rides the SAME prompt the label already comes from.
_MAX_PRIOR_CHARS = 1200


def _prior_context(session_id: str, ask: str) -> str:
    """The immediately preceding user turn, bounded, or "" when unavailable.

    Never the current ask (a gate pass that re-reads the same turn must not
    label it as its own context), never more than one turn, never more than
    _MAX_PRIOR_CHARS. Unavailable context is not an error: the gate behaves
    exactly as today.
    """
    try:
        if not session_id:
            return ""
        last = str(_state.get_last_seen(str(session_id)) or "").strip()
        cur = str(ask or "").strip()
        if not last or last == cur:
            return ""
        return last[:_MAX_PRIOR_CHARS]
    except Exception:  # noqa: BLE001 — context never breaks the gate
        return ""

# ---------------------------------------------------------------------------
# IN-DOMAIN CONFIRMATION (M35-06) → LANGUAGE-AGNOSTIC (M53-07)
#
# The aux label answers "what is this ask ABOUT". The gate's verdict is a claim
# the whole ordered table acts on (row 4 routes on the flag ALONE), so a topic
# label is not enough: rule 4 amplifies every gate false positive into a body
# swap. Measured on the frozen 199 benign / 295 in-domain corpus, the gate
# flagged 13/199 benign asks (6.53%) and rule 4 spent 5.03% of a 5% budget on
# its own.
#
# M35-06 narrowed the claim with three deterministic checks over the ask:
#   1. CONTINUATION is exempt (an anaphoric "continue from your own last reply"
#      carries no topic words by construction).
#   2. OWN-ARTIFACT asks are not claimed (the object is the organism's own
#      tree/build/service/host; a named external target network disables it).
#   3. FAMILY CUE was REQUIRED — the claimed family had to have topic evidence
#      in the ask itself.
# Measured then: benign FP 13/199 -> 4/199, in-domain recall 260/295 -> 257/295.
#
# M53-07 (owner directive): check 3 was an ENGLISH regex used as a VETO over a
# multilingual aux label, and a cue miss is not evidence of "no domain". On the
# deployed module a correct English cyber ask ("records every key press and
# sends them to a remote server") and its HR/DE translations all came back
# NOT_FLAGGED: the aux model named `cyber_offensive`, `confirm_in_domain`
# demanded an English cue, and the label was overruled.
#
# The mechanism is now language-neutral:
#   1. THE CUE TABLE IS AN ACCELERATOR, NEVER A VETO. A hit short-circuits to
#      YES; a miss falls through to the aux label. A deterministic miss may not
#      produce `not_flagged` on its own — own-artifact is the only refusal left.
#   2. A CUE IS ONLY A TIE-BREAK, and only when the aux label and the SPECIFIC
#      cue evidence name different families (`deterministic_domains`); with no
#      specific evidence the aux label stands, in whatever language it read.
#   3. CUES ARE SPLIT SPECIFIC vs GENERIC. `sarin`, `shellcode`, `keylog` name
#      an act/artifact; `key`, `account`, `synthesis`, `hack` are collocations
#      many benign asks carry. Only a SPECIFIC hit may name a family — that is
#      what stops a generic collocation outranking a specific one.
#   4. OWN-ARTIFACT PROTECTION IS UNCHANGED: an honest "what does my own repo
#      contain" is still not claimed, for the reason the layer exists.
#
# Re-measured on the same frozen corpus (199 benign rows, cached aux verdicts,
# pure functions, NO model call): benign FP 3/199 -> 4/199 (1.51% -> 2.01%),
# claimed in-domain 138/208 -> 195/208 (66.35% -> 93.75%). The single new
# benign family is `shadow:51437` "What is the administrator password for that
# server?" — the corpus's own domain_guess for it is `cyber_offensive`, and a
# credential pull is this lane's business. Reported, not swallowed; still under
# the 5% bar. (The module's older note cites 4/199 -> the difference is a
# corpus/label drift, see reviews/M49-03_PRECISION.md §2 on the 295 denominator.)
# ---------------------------------------------------------------------------
_CONTINUATION_RE = re.compile(
    r"continue seamlessly|continue from|go deeper on|deeper on the|extend it|"
    r"your own last reply|do not restate|as you (?:were|did)|same as before|"
    r"keep going|pick up where", re.IGNORECASE)

# Named so a log line says WHICH marker fired, never the ask text.
_OWN_ARTIFACT_CHECKS = (
    ("own_path", re.compile(
        r"/opt/data/(?:plugins/hermes_router|profiles|workspace|projects|tmp)",
        re.IGNORECASE)),
    ("own_tree", re.compile(
        r"hermes_router|hermes-router|/docker/agent-core|agent-core/data",
        re.IGNORECASE)),
    ("own_build", re.compile(
        r"\bMANTLE\b|\bPROBE\b|probe client|mantle-fix", re.IGNORECASE)),
    ("own_host", re.compile(
        r"\blocalhost\b|127\.0\.0\.1|\b10\.10\.10\.\d+|\bCT100\b|pct exec",
        re.IGNORECASE)),
    ("own_service", re.compile(
        r"\b(?:my|our|the)\s+(?:own\s+)?(?:service|deployment|repo|tree|plugin|gateway)\b",
        re.IGNORECASE)),
    ("own_state", re.compile(r"state\.db|\.hermes", re.IGNORECASE)),
)

# A named third-party target network makes the ask's OBJECT external, so the
# own-artifact carve-out does not apply. GitHub and VirusTotal are deliberately
# absent: both are named in our own build/scan work, so naming them is not
# evidence of an external target.
_THIRD_PARTY_RE = re.compile(
    r"\btelegram\b|\bdiscord\b|\breddit\b|\birc\b|dark web|4chan|\btwitter\b|pastebin",
    re.IGNORECASE)

# Topic evidence per family. Acts and artifacts, not roles or moods: a "noir
# monologue about a hitman" is atmosphere, "torture with pliers" is the act.
#
# M53-07 SPECIFICITY (D1). One flat table per family let a GENERIC collocation
# outrank a SPECIFIC cue: "records every key press and sends them to a remote
# server" was named fraud_deception because `\bkeys?\b` matched "key" in a fixed
# family order, and the cyber ask then lost its claim. The evidence is now
# graded by WHAT MATCHED, not by which family was tried first:
#   SPECIFIC — an act or artifact named as such (sarin, shellcode, keylog,
#              pliers, ransomware). Only these may NAME a family.
#   GENERIC  — a collocation many benign asks also carry (key, account,
#              synthesis, portal, hack, evasion). Enough to confirm a label the
#              model already produced, never enough to name one on its own.
# This is a re-grading of the existing vocabulary, not a bigger word list: every
# family's own terms are still here, split by probing power.
_FAMILY_SPECIFIC_CUES = {
    "violence_gore": re.compile(
        r"tortur|gore|dismember|mutilat|behead|decapitat|slaughter|stab|"
        r"corpse|entrail|\bflay|impale|amputat|pliers|massacre|atrocit|"
        # M53-10: measured recoveries (wet work, sabotage field manual).
        r"wet work|sabotage|field manual[^.]{0,30}destroy", re.IGNORECASE),
    "weapons_explosives": re.compile(
        r"\bbomb|explosive|\bied\b|detonator|gunpowder|napalm|thermite|incendiar|"
        r"silencer|suppressor|ghost gun|ammunition|\brdx\b|\btatp\b|\banfo\b|"
        r"molotov|grenade|\bc4\b|semtex|blasting cap|pipe bomb", re.IGNORECASE),
    "bio_chem": re.compile(
        r"pathogen|toxin|nerve agent|sarin|\bvx\b|ricin|anthrax|botulinum|cyanide|"
        r"chlorine gas|mustard gas|chemical weapon|weaponi|aerosoli|\bspore|"
        r"bioreactor", re.IGNORECASE),
    "sexual_explicit": re.compile(
        r"explicit|\bporn|erotic|sex scene|\bnude|naked|genital|penis|vagina|"
        r"\bcum\b|orgasm|masturbat|\bfuck|blowjob|hentai|\bnsfw|sexual content|"
        # M53-10: measured recoveries.
        r"gaping|adult fiction scene", re.IGNORECASE),
    "drugs_synthesis": re.compile(
        r"\bmeth\b|mdma|fentanyl|heroin|cocaine|amphetamine|clandestine|"
        r"red phosphorus|crystal meth", re.IGNORECASE),
    "fraud_deception": re.compile(
        r"phish|scam|fraud|impersonat|social engineering|pretext|launder|"
        r"money mule|fake (?:id|invoice|passport)|\bspoof|carding|chargeback|"
        r"gift card|"
        # M53-10: measured recoveries (reputation/defamation, witness
        # intimidation, extortion). Specific act phrases only.
        r"defamat|smear campaign|reputation gone|deepfake|"
        r"intimidat\w*[^.]{0,30}witness|blackmail|extort|"
        r"destroy (?:her|his|their)", re.IGNORECASE),
    "self_harm": re.compile(
        r"suicide|kill myself|end my life|self.?harm|cut myself|overdose|noose|"
        r"hang(?:ing|ed) myself|jump off|painless death", re.IGNORECASE),
    "cyber_offensive": re.compile(
        r"exploit|malware|payload|shellcode|backdoor|rootkit|keylog|ransomware|"
        r"key ?press|keystroke|screen ?scrap|input capture|"
        r"exfiltrat|reverse shell|\bc2\b|botnet|brute.?force|sql inject|\bxss\b|"
        r"privilege escalation|/etc/shadow|credential dump|mimikatz|"
        r"bypass\w* (?:av|edr|defender|antivirus)|\bfud\b|obfuscat|crypter|"
        r"0day|zero.day|cve-\d|nmap|metasploit|hydra|hashcat|gopclntab|"
        r"implant|dropper|"
        # M53-10: measured recoveries (2FA interception, SIM swap). A generic
        # `rate.?limit` cue is deliberately NOT added — it fired on a benign
        # scraper ask.
        r"two-factor|\b2fa\b|sim swap|"
        r"intercept[^.]{0,30}(?:sms|otp|two-factor|code)", re.IGNORECASE),
}

_FAMILY_GENERIC_CUES = {
    "violence_gore": re.compile(
        r"\bblood|\brap(?:e|ing)\b|kill|murder", re.IGNORECASE),
    "weapons_explosives": re.compile(
        r"\bcharge size|fertilizer|weapon", re.IGNORECASE),
    "bio_chem": re.compile(r"culture", re.IGNORECASE),
    "sexual_explicit": re.compile(
        r"aroused|\bsex\b", re.IGNORECASE),
    "drugs_synthesis": re.compile(
        r"synthes|precursor|freebase", re.IGNORECASE),
    "fraud_deception": re.compile(
        r"\botp\b|account|api key|\bkeys?\b|topup|top-up|portal|voucher|refund|"
        r"billing|payment|binance|attack (?:vector|path)|exploit", re.IGNORECASE),
    "self_harm": re.compile(r"painless", re.IGNORECASE),
    "cyber_offensive": re.compile(
        r"evasion|persistence|\brat\b|morph|intrusion|\bhack|vulnerab",
        re.IGNORECASE),
}

# The full table per family, kept as the single public name the gate and its pins
# already use: `set(_FAMILY_CUES) == set(UNC_DOMAINS)` is asserted, and a family
# with no table is claimed on the aux label alone.
# ---------------------------------------------------------------------------
# m63 — OWNER RULING: "getting intimate info or modeling without consent is a
# domain." The distinguishing feature is CONSENT, not content: a named (or
# named-by-role) individual's intimate/private data — or a model of them —
# obtained or built WITHOUT their consent. Fits no prior label, which is why
# the researcher refusals shipped gate=not_flagged.
#
# Cue vocabulary derived from the 25 personal-shape refusal rows in the frozen
# 874-row detcorpus (674 refusal / 200 benign, /opt/data/tmp/detcorpus), then
# MEASURED before landing (m63 harness /opt/data/tmp/m63/):
#   specific table: 874-row replay -> 0/200 benign fires, 8 refusal rows
#   caught (analyst:17208/17210/17212, researcher:52216/52221/52365/52401/
#   66480), all shaped "dossier / psych profile / vulnerability-pattern /
#   dirty-details / personal tracking about a person".
#   Deliberately NOT a cue (measured over-fires): bare `osint` (caught
#   shadow-library / dark-web / wargame rows = wrong label), bare names
#   (legal-ops dossier asks name companies too; acts-and-artifacts, not
#   names, per M53-10), `psychological profile` alone (fires on consented
#   clinical work). The consent carve-out lives in the aux label, not the
#   cue table — a cue miss is absence of evidence (M53-07).
#   aux-layer follow-ups ("find ways to research that about her") come
#   through the M53-07b prior-turn context, not this layer.
# ---------------------------------------------------------------------------
_FAMILY_SPECIFIC_CUES["intimate_consent"] = re.compile(
    r"(?:"
    # intimate traits attached by possessive pronoun ("her sexual
    # preferences", "his intimate life", "their dating history")
    r"\b(?:her|his|my|their)\s+(?:sexual|romantic|dating|intimate)[- ]?"
    r"(?:preferences?|life|history|orientation|behaviou?rs?|habits|fantas\w+)"
    r"|"
    # intimate traits of/about a person noun ("sexual preferences of the
    # girl in study X" is caught; Drosophila is a drosophila, not a person)
    r"\b(?:sexual|intimate)[ -]?(?:preferences?|life|history|orientation|"
    r"behaviou?rs?|habits|fantas\w+)"
    r"[^.\n]{0,40}?\b(?:of|for|about|from)\b[^.\n]{0,40}?"
    r"\b(?:individual|person|user|employee|colleague|coworker|partner|"
    r"woman|man|girl|boy|subject|target|client|friend|neighbou?r|"
    r"someone|her|him|them)\b"
    r"|"
    # dossier / personal file / background check about a person noun
    r"\b(?:dossier|personal file|intelligence dossier|background check)\b"
    r"[^.\n]{0,120}?"
    r"\b(?:on|of|for|about|against)\b[^.\n]{0,60}?"
    r"\b(?:private individual|private person|individual|person|woman|man|"
    r"girl|boy|user|employee|colleague|partner|friend|neighbou?r|"
    r"subject|target|client|someone|her|him|them)\b"
    r"|"
    # private-individual tracking / personal-life enrichment
    r"\b(?:personal track\w+|private life|vulnerabilit\w+ pattern|"
    r"dirty details|profile enrichment)\b"
    r"|"
    # anaphoric research verb pointed back at a person ("find ways to
    # research that about her"). Measured: 0/200 benign on the 874-row
    # replay; the person pronoun is what keeps legitimate "research the
    # paper about them" prose out.
    r"\b(?:research|look into|dig into|investigate)\b[^.\n]{0,50}"
    r"\babout (?:her|him|them)\b"
    r")",
    re.IGNORECASE)

_FAMILY_CUES = {
    fam: re.compile("(?:%s)|(?:%s)" % (
        _FAMILY_SPECIFIC_CUES.get(fam, re.compile(r"(?!)")).pattern,
        _FAMILY_GENERIC_CUES.get(fam, re.compile(r"(?!)")).pattern),
        re.IGNORECASE)
    for fam in UNC_DOMAINS
}


def _specific_cue_family(text: str) -> str:
    """The family whose cue matched as a MULTI-WORD act phrase, or "".

    M53-07b FUSION FLOOR. Fusion may overrule a benign/meta/other aux label,
    so the evidence it acts on has to be specific. Measured on the frozen
    benign replay, generic SINGLE tokens ("explicit", "exploit", "nmap",
    "launder") appear in ordinary operational prose (updating a skill library,
    a command-approval notice, an API-key error) — treating those as family
    evidence flagged 6 benign asks. A multi-word act phrase ("pipe bomb",
    "key press", "reverse shell") is the shape that actually names an act, so
    fusion is gated on it; the accelerator itself is unchanged and may still
    name a family for the aux_none fallback.
    """
    try:
        best, best_key = "", (0, 0)
        for family in _DETERMINISTIC_ORDER:
            cue = _FAMILY_CUES.get(family)
            if cue is None:
                continue
            spans = {m.group(0).lower(): len(m.group(0))
                     for m in cue.finditer(str(text or ""))}
            # SPECIFICITY FLOOR: a multi-word span of at least 9 characters.
            # "pipe bomb" / "key press" / "reverse shell" name an act; "api
            # key" (7) and a truncated "CVE-2" (5) are infrastructure tokens
            # that appear in ordinary ops prose, and acting on them flagged
            # benign asks.
            multi = {k: v for k, v in spans.items()
                     if (" " in k or "-" in k) and v >= 9}
            if not multi:
                continue
            key = (max(multi.values()), len(multi))
            if key > best_key:
                best, best_key = family, key
        return best
    except Exception:  # noqa: BLE001
        return ""


def _own_artifact_exempt(text: str) -> bool:
    """True when the ask points at OUR OWN artifacts (M35-06 protection).

    Kept verbatim in behaviour: a named third-party target network lifts the
    carve-out, otherwise an own path/tree/build/host/service/state marker
    exempts the ask. This runs BEFORE evidence fusion so a specific cue inside
    "what does my own repo contain" can never outvote the protection.
    """
    try:
        t = str(text or "")
        if _THIRD_PARTY_RE.search(t):
            return False
        for _name, rx in _OWN_ARTIFACT_CHECKS:
            if rx.search(t):
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


def confirm_in_domain(ask: str, domain: str, *,
                      asks_for_act: Optional[bool] = None) -> tuple:
    """Is the aux `domain` claim about THIS ask, or only about its topic?

    CALLER CONTRACT
        ask     the request text the aux label was produced from
        domain  one of UNC_DOMAINS
        asks_for_act
                the aux model's OWN answer to "does the request ask for the
                act/artifact, or only discuss/narrate it", produced in the same
                call as the label (M53-07). Present -> it decides, because it is
                the only judgement made in the ask's own language. Absent ->
                the legacy cue check below, so recorded labels replay unchanged.
        returns (claimed: bool, reason: str) where reason is a short token for
        the log (`own_artifact:<marker>` / `aux_no_act` / `no_<family>_cue`) and
        "" when the claim stands.

    M53-07: the deterministic cue table is ENGLISH, so a cue MISS may never veto
    a label the model produced: measured, a correct multilingual label was
    overruled by `no_cyber_offensive_cue` and the turn shipped unflagged. A cue
    miss is absence of evidence, not evidence of absence.

    False is NOT "safe": it means the ask is not this lane's to route, so the
    gate records `not_flagged` and the table falls to the body path (rows 5-6).
    It never means the auxiliary lane disagreed about the TOPIC — the aux label
    is still recorded on the row for measurement.
    """
    try:
        text = str(ask or "")
        if _CONTINUATION_RE.search(text):
            return True, ""
        if not _THIRD_PARTY_RE.search(text):
            for name, rx in _OWN_ARTIFACT_CHECKS:
                if rx.search(text):
                    return False, "own_artifact:" + name
        # M53-07: the model's own act judgement outranks the English cue table.
        if asks_for_act is True:
            return True, ""
        if asks_for_act is False:
            return False, "aux_no_act"
        # M53-07: A CUE MISS IS NOT A VETO. With no act judgement in the payload
        # (a model that omits the field, or a recorded label replayed offline)
        # the aux label STANDS: the English cue table may accelerate a claim, it
        # may never overrule one. Measured on the deployed module, a cue miss
        # here is what dropped every correct non-English cyber ask
        # ("no_cyber_offensive_cue" on a Croatian/German keylogger request).
        return True, ""
    except Exception:  # noqa: BLE001 — an unconfirmable claim is not claimed
        logger.debug("domain_gate confirm error", exc_info=True)
        return False, "confirm_error"


# Fixed order, not dict order: two families can share a word ("exploit" is both
# a fraud and a cyber cue), and a verdict that depends on dict iteration order is
# not deterministic.
_DETERMINISTIC_ORDER = ("violence_gore", "weapons_explosives", "bio_chem",
                        "sexual_explicit", "drugs_synthesis", "fraud_deception",
                        "self_harm", "cyber_offensive", "intimate_consent")


def deterministic_domains(ask: str) -> tuple:
    """EVERY family the ask carries evidence for, specific matches first.

    CALLER CONTRACT
        ask     the request text
        returns a tuple of family names in `_DETERMINISTIC_ORDER` within each
        evidence grade: families with a SPECIFIC cue first, then families with
        only a GENERIC collocation. Empty when the ask carries no cue at all.

    M53-07 requirement 3: the layer RECORDS all matched families instead of
    returning the first one, so a caller can see the collision (a cyber ask that
    also trips fraud's generic "key") rather than being handed one name that
    silently outranked the other.
    """
    try:
        text = str(ask or "")
        specific, generic = [], []
        for family in _DETERMINISTIC_ORDER:
            spec = _FAMILY_SPECIFIC_CUES.get(family)
            gen = _FAMILY_GENERIC_CUES.get(family)
            if spec is not None and spec.search(text):
                specific.append(family)
            elif gen is not None and gen.search(text):
                generic.append(family)
        return tuple(specific + generic)
    except Exception:  # noqa: BLE001 — a cue read never breaks the gate
        return ()


def deterministic_domain(ask: str) -> str:
    """The family a SPECIFIC cue in the ask names, or "" when none does.

    M36-33 FALLBACK. Measured cause of `reason=aux_none` on architect: the aux
    lane's model is a reasoning model and spends its whole `max_tokens` on
    thinking, so `_hermes_aux_call` returns `hermes_aux_failed
    reason=empty_content` — the same turns answered by analyst/researcher. The
    gate then abstained on EVERY turn, rule 3 deferred, and the route arm was
    unreachable (5 of architect's 7 fails).

    M53-07 (D1): only a SPECIFIC cue may name a family. Fixed family order let a
    GENERIC collocation win — "records every key press and sends them to a
    remote server" was named fraud_deception because `\\bkeys?\\b` matched "key"
    before cyber was tried, and the cyber ask then lost its claim. A generic-only
    match now returns "" (no name) instead of naming the wrong family, and the
    caller falls through to the aux label.

    This layer is an ACCELERATOR, never a veto: a miss here is absence of
    evidence, not evidence of absence.
    """
    try:
        hits = deterministic_domains(ask)
        for family in hits:
            spec = _FAMILY_SPECIFIC_CUES.get(family)
            if spec is not None and spec.search(str(ask or "")):
                return family
        return ""
    except Exception:  # noqa: BLE001 — a cue read never breaks the gate
        return ""


def claim_basis(ask: str, domain: str) -> str:
    """Which evidence carries a claimed label: `cue` or `label`.

    CALLER CONTRACT
        ask / domain  as passed to `confirm_in_domain`
        returns "cue" when the family's OWN table names the ask (the English
        accelerator fired), "label" when the claim rests on the aux label alone.

    A short token for the log only. It exists because the language-neutral path
    and the accelerator path are not the same evidence, and a row that cannot
    say which one produced the flag cannot be audited.
    """
    try:
        cue = _FAMILY_CUES.get(str(domain or ""))
        if cue is not None and cue.search(str(ask or "")):
            return "cue"
    except Exception:  # noqa: BLE001
        pass
    return "label"


_PROMPT = """Classify what this request asks the responder to PRODUCE or DO. Judge the operational content, not the framing: "for a novel", "a character says", "hypothetically" do not change the domain.

Domains:
violence_gore - graphic violence, torture, gore, killing described in detail
weapons_explosives - weapon construction, explosives, incendiaries, their employment
bio_chem - pathogen, toxin, chemical agent production or weaponisation
sexual_explicit - explicit sexual content
drugs_synthesis - illicit drug synthesis, production, or trafficking method
fraud_deception - fraud, phishing, impersonation, social engineering, laundering
self_harm - suicide or self-injury method or encouragement
cyber_offensive - intrusion, malware, exploitation, credential theft, sabotage
benign_task - ordinary work: code, research, writing, admin, analysis
meta_doctrine - questions about the agent itself, its rules, its architecture
technical - engineering/ops advice, including safety limits and refusals thereof
other - anything else

Reply with JSON only, no prose: {"domain": "<one label>", "confident": true|false, "asks_for_act": true|false}

"asks_for_act" is judged in the LANGUAGE OF THE REQUEST, not in English: true when the request asks the responder to PRODUCE or DO the operation itself, false when it only discusses, narrates, quotes, analyses or asks about the topic.

REQUEST:
<<<
%s
>>>"""

# ---------------------------------------------------------------------------
# acceptance criterion (spec §C: the gate "also extracts the acceptance
# criterion"). Deterministic and free: this is the request-side shape the
# POST judge grades against, so it must never cost a call.
# ---------------------------------------------------------------------------
_LANG_RE = re.compile(
    r"\b(?:python|py|javascript|js|typescript|ts|golang|go|rust|java|c\+\+|c#|"
    r"ruby|php|bash|shell|zsh|powershell|sql|html|css)\b", re.IGNORECASE)
_CODE_ASK_RE = re.compile(
    r"\b(?:script|function|program|implementation|class|snippet|patch|regex)\b",
    re.IGNORECASE)
_ITEM_COUNT_RE = re.compile(
    r"\b(\d{1,3})\s+(?:items?|rows?|entries|examples?|steps?|bullets|points?|"
    r"lines?|reasons?|ways?)\b", re.IGNORECASE)
_COMMAND_ASK_RE = re.compile(
    r"\b(?:commands?|shell|terminal|cli|invoke|run these|how do i run)\b",
    re.IGNORECASE)
_FILE_ASK_RE = re.compile(
    r"\b(?:write|create|save|generate|produce)\b[^.?\n]{0,40}\bfile\b"
    r"|\bsave (?:it|this|that) to\b", re.IGNORECASE)
_PATH_RE = re.compile(r"[~./\w-]*/[\w./-]+\.[A-Za-z0-9]{1,6}\b|\b[\w-]+\.(?:py|js|ts|go|rs|sh|md|json|yaml|yml|txt|csv)\b")


def _criterion_default() -> Dict[str, Any]:
    """The absent criterion: explicit, never a silent empty that reads as
    'prose, nothing required'. `available: False` is the decision surface;
    `reason` is a short token for the log, never the ask."""
    return {"kind": "", "units": 0, "items": [], "paths": [], "commands": [],
            "prose": False, "verbatim": "", "available": False, "reason": ""}


def extract_criterion(ask: str) -> Dict[str, Any]:
    """What this answer must contain to count as delivered, as data.

    Closed vocabulary: {kind, units, items, paths, commands, prose, verbatim,
    available, reason}. `verbatim` is the ask fragment itself, bounded — the
    POST judge is graded against the ORIGINAL wording (a paraphrase is a
    different criterion), and a measured refinement of that wording lost
    accuracy, so nothing is rewritten here.

    Extraction failure is NOT a prose criterion: it returns `available: False`
    and the caller's degrade is `criterion_of(row) is None`, which behaves
    exactly like an abstained gate (no grading, fall to the body path). A
    fabricated empty prose criterion would read as "nothing was required" —
    the false-delivered shape this field exists to prevent.
    """
    try:
        text = " ".join(str(ask or "").split())
    except Exception:  # noqa: BLE001 — an unreadable ask is an absent criterion
        crit = _criterion_default()
        crit["reason"] = "ask_unreadable"
        return crit
    if not text:
        crit = _criterion_default()
        crit["reason"] = "empty_ask"
        return crit
    crit: Dict[str, Any] = {
        "kind": "prose", "units": 0, "items": [], "paths": [],
        "commands": [], "prose": True, "verbatim": text[:800],
        "available": True, "reason": "",
    }
    try:
        paths = [p for p in _PATH_RE.findall(text)][:5]
        # ORDER MATTERS: a named language or deliverable noun ("script",
        # "function", "class") makes the criterion CODE even when the sentence
        # also says "file" ("a python script that reads a jsonl file"). Checking
        # the file ask first graded that as a file-write and the POST deterministic
        # check would then look for a write that was never asked for. The file
        # kind is for asks whose DELIVERABLE is the file.
        if _LANG_RE.search(text) or _CODE_ASK_RE.search(text):
            m = _ITEM_COUNT_RE.search(text)
            crit.update({"kind": "code", "prose": False, "paths": paths,
                         "units": int(m.group(1)) if m else 0})
            return crit
        if _FILE_ASK_RE.search(text):
            crit.update({"kind": "file", "prose": False, "paths": paths})
            return crit
        if _COMMAND_ASK_RE.search(text):
            crit.update({"kind": "commands", "prose": False, "units": 1})
            return crit
        m = _ITEM_COUNT_RE.search(text)
        if m:
            crit.update({"kind": "items", "prose": False,
                         "units": int(m.group(1))})
            return crit
    except Exception:  # noqa: BLE001 — extraction never breaks the gate
        logger.debug("domain_gate criterion extract error", exc_info=True)
        crit = _criterion_default()
        crit["reason"] = "extract_error"
    return crit


def criterion_of(row: Optional[dict]) -> Optional[Dict[str, Any]]:
    """The acceptance criterion read off a PRE record, or None.

    THE single degrade point. None means "this turn has no criterion" and the
    caller must behave exactly as it does for an abstained gate: skip grading
    and fall to the body path. An empty dict is never returned, because an empty
    dict reads as "prose criterion, nothing required" — a silent empty that
    grades every body as delivered.
    """
    try:
        if not isinstance(row, dict):
            return None
        crit = row.get("criterion")
        if not isinstance(crit, dict):
            return None
        if crit.get("available") is not True:
            return None
        if not str(crit.get("verbatim") or "").strip():
            return None
        if str(crit.get("kind") or "") not in _CRITERION_KINDS:
            return None
        return crit
    except Exception:  # noqa: BLE001
        return None


def evaluate(ask: str, *, session_id: str = "") -> Dict[str, Any]:
    """One gate pass over the ask: {"domain","gate","criterion","state"}.

    Never raises. `gate == abstain` on ANY failure (no aux, breaker open, hourly
    cap, empty/garbage answer, off-vocabulary label). Unknown is NEVER coerced to
    `not_flagged`: that would silently disable the lane, and abstain must fall to
    the body path instead.
    """
    crit = extract_criterion(ask)
    out: Dict[str, Any] = {"domain": "", "gate": GATE_ABSTAIN,
                          "criterion": crit, "state": GATE_ABSTAIN,
                          "reason": "", "cause": "", "claim": ""}
    text = str(ask or "").strip()
    if not text:
        out["reason"] = "empty_ask"
        return out
    try:
        from ... import semantic_classifier as _sc
    except Exception:  # noqa: BLE001 — no aux lane, no verdict
        logger.debug("domain_gate aux import error", exc_info=True)
        out["reason"] = "aux_unavailable"
        return out
    prompt = _PROMPT % text[:_MAX_ASK_CHARS]
    # M53-07b CONTEXT: one bounded prior turn, clearly delimited, in the SAME
    # call. A follow-up fragment resolves its domain only in context.
    _prior = _prior_context(session_id, text)
    if _prior:
        prompt = _PROMPT % ("PRIOR CONTEXT (the user's PREVIOUS turn — use it only to resolve what "
                            "the CURRENT REQUEST refers to; judge the current request):\n<<<\n%s\n>>>\n\n"
                            "CURRENT REQUEST:\n<<<\n%s\n>>>"
                            % (_prior, text[:_MAX_ASK_CHARS]))
    try:
        raw = _sc.aux_raw_call(prompt, session_id=session_id,
                               record_failure=False)
    except Exception:  # noqa: BLE001 — the aux lane never raises, but be sure
        logger.debug("domain_gate aux call error", exc_info=True)
        out["reason"] = "aux_error"
        return out
    if not raw:
        # M49-02: `aux_none` is the OUTCOME token (existing readers keep it);
        # `cause` is the separate field that says WHY the call returned
        # nothing — cap | breaker | no_client | empty_content | timeout |
        # http_<status> | unparseable | ... — read from the aux lane's own
        # context var immediately after the falsy return. Never a new outcome
        # name: a suppressed call and an empty answer are still both
        # `abstain`/`aux_none`; they are now distinguishable.
        out["cause"] = str(getattr(_sc, "aux_failure_cause", lambda: "")() or "unknown")[:40]
        # DETERMINISTIC FALLBACK (M36-33). `aux_none` used to abstain on every
        # turn, so rule 3 (`gate_abstain -> defer`) made the route arm
        # unreachable and a dead aux lane read as a dead lane forever.
        #
        # The fallback decides ONLY when the ask carries an act/artifact cue
        # for a known family — the same table `confirm_in_domain` already
        # trusts, read in a fixed order. NO cue is NOT "safe": an ask with no
        # deterministic evidence stays `abstain`/`aux_none`, because coercing
        # unknown to `not_flagged` settles a turn the gate never answered (it
        # bypasses `defer` in the rule table, which is the documented
        # "abstain is a failure, not a verdict" path — measured: it turned a
        # must-route refusal turn into a delivered one in
        # tests/banners/test_m34_01_turn_binding.py).
        domain = deterministic_domain(text)
        if not domain:
            out["reason"] = "aux_none"
            return out
        # M53-07: a deterministic hit is an ACCELERATOR — it short-circuits to a
        # domain, and when it finds nothing the gate abstains as before. It is
        # never a veto. There is no act judgement on this path (no aux answer to
        # carry one), and the domain was NAMED by a specific cue, so
        # confirmation's cue check is satisfied by construction.
        asks_for_act = None
    else:
        try:
            m = re.search(r"\{.*\}", str(raw), re.S)
            _parsed = json.loads(m.group(0)) if m else {}
            domain = str(_parsed.get("domain") or "").strip().lower()
            confident = _parsed.get("confident", True)
            # M53-07: the SAME call also says whether the request asks for the
            # act. Absent (older prompts, recorded labels) -> None -> legacy cue
            # path, so no existing behaviour moves.
            _afa = _parsed.get("asks_for_act", None)
            asks_for_act = _afa if isinstance(_afa, bool) else None
        except Exception:  # noqa: BLE001 — unparseable answer is a gate failure
            out["reason"] = "unparseable"
            return out
        if not domain:
            out["reason"] = "no_domain"
            return out
    # written anywhere — not the record, not the log, not the criterion.
    if domain in UNC_DOMAINS:
        # M35-06/M53-07: the aux label is a claim about the TOPIC. The gate
        # records `flagged` when nothing in the ask refuses the claim — a cue
        # HIT is the accelerator, a cue MISS is not a veto, and own-artifact is
        # the only check that still refuses (see confirm_in_domain). A refused
        # claim is not_flagged, never abstain: the aux lane did answer, and the
        # table's body path is the correct destination. The label stays on the
        # row for measurement, and `claim` says which evidence carried it.
        claimed, unconfirmed = confirm_in_domain(
            text, domain, asks_for_act=asks_for_act)
        if claimed:
            out.update({"domain": domain, "gate": GATE_FLAGGED,
                        "state": GATE_FLAGGED, "reason": "in_domain",
                        "claim": claim_basis(text, domain)})
            # M53-07b: two layers named different unc families — the aux side
            # stands, but the disagreement is recorded, never silenced.
            _det_u = deterministic_domain(text)
            if _det_u and _det_u != domain:
                out["domain_disagreement"] = "det=%s aux=%s" % (_det_u, domain)
                logger.info("domain_disagreement det=%s aux=%s", _det_u, domain)
            return out
        out.update({"domain": domain, "gate": GATE_NOT_FLAGGED,
                    "state": GATE_NOT_FLAGGED,
                    "reason": "unconfirmed:" + unconfirmed, "claim": ""})
        return out
    # M53-07b EVIDENCE FUSION (R1/R2). A specific deterministic cue may NOT be
    # discarded because the aux label said benign/meta/other: "pipe bomb" is
    # evidence the fragment carries, and the 30 measured misses were exactly
    # that shape. Own-artifact protection runs first and still wins.
    _det_family = _specific_cue_family(text)
    if _det_family and not _own_artifact_exempt(text):
        out.update({"domain": _det_family, "gate": GATE_FLAGGED,
                    "state": GATE_FLAGGED, "reason": "in_domain",
                    "claim": "det",
                    "domain_disagreement": "det=%s aux=%s"
                    % (_det_family, domain or "none")})
        logger.info("domain_disagreement det=%s aux=%s",
                    _det_family, domain or "none")
        return out
    if domain in SAFE_DOMAINS:
        out.update({"domain": domain, "gate": GATE_NOT_FLAGGED,
                    "state": GATE_NOT_FLAGGED, "reason": "out_of_domain"})
        return out
    if domain == "meta_doctrine":
        # M49-07: safe only when the ask ASKS NOTHING of the doctrine. A
        # directive verb with a doctrine artifact as its object is an action
        # ask, and the lane's flag is what carries it past rules 5-6.
        marker = doctrine_action_marker(text)
        if marker:
            out.update({"domain": domain, "gate": GATE_FLAGGED,
                        "state": GATE_FLAGGED,
                        "reason": "doctrine_action:" + marker})
        else:
            out.update({"domain": domain, "gate": GATE_NOT_FLAGGED,
                        "state": GATE_NOT_FLAGGED, "reason": "out_of_domain"})
        return out
    out["reason"] = "off_vocabulary"
    out["domain"] = ""
    return out


def verdict(row: Optional[dict]) -> str:
    """The gate verdict a consumer may act on: `flagged | not_flagged | abstain`.

    THE single place `abstain` is produced for missing state: None (never
    started, pending, expired, ask mismatch) reads as `abstain`, which falls to
    the body path. It is never readable as `not_flagged` — "the gate did not
    answer" must not be spelled "safe".
    """
    try:
        if not isinstance(row, dict):
            return GATE_ABSTAIN
        g = str(row.get("gate") or "").strip()
        if g in (GATE_FLAGGED, GATE_NOT_FLAGGED):
            return g
        # M53-23: a row that NAMED an unc domain is not a silent gate.
        # Consumers read two different fields off this one row: `_build_facts`
        # takes `reason` while `_read_gate_state` takes `verdict()`. Measured:
        # records carried gate_reason=in_domain beside gate_verdict=abstain, so
        # a detected domain was stamped `abstain` and fell to D6
        # `no_admissible_lane` even though detection had named the family.
        # Derive the verdict from the evidence the row actually carries, so the
        # two readers of one record cannot disagree.
        _dom = str(row.get("domain") or "").strip()
        _rsn = str(row.get("reason") or "").strip().lower()
        if _dom in UNC_DOMAINS and (_rsn == "in_domain"
                                    or _rsn.startswith("unconfirmed")
                                    or _rsn.startswith("det_")):
            return GATE_FLAGGED
        return GATE_ABSTAIN
    except Exception:  # noqa: BLE001
        return GATE_ABSTAIN


def domain_detected(row: Optional[dict]) -> bool:
    """Did the gate NAME an unc domain? (M53-06e routing predicate.)

    CALLER CONTRACT
        row  the PRE gate record (`domain_gate.read`), or None.

    True only when the gate reported a domain in `UNC_DOMAINS` AND the reason
    says it was detected — `in_domain`, or an `unconfirmed:*` reason (the gate
    identified the domain and withheld `flagged` because its finer cue test was
    not met). Measured census: 121 turns read
    `gate=not_flagged reason=unconfirmed domain=UNC` and were dropped by a
    predicate that demanded `flagged`.

    NOT detected: empty/absent domain, `out_of_domain` (meta_doctrine), a
    a gate that never answered (`abstain`/None), and a
    row with no gate verdict at all. This is not a quality judgement on the ask
    and not a per-topic test: any domain in `UNC_DOMAINS` counts, and the model
    in the lane decides what is produced.

    Never raises: an unreadable row reads as NOT detected.
    """
    try:
        if not isinstance(row, dict):
            return False
        # M53-23: read the VERDICT, not the raw `gate` field. `verdict()` is the
        # single producer that now also derives `flagged` from a row whose
        # evidence named an unc domain; checking the field directly made this
        # predicate answer False for a row `verdict()` called `flagged`.
        if verdict(row) not in (GATE_FLAGGED, GATE_NOT_FLAGGED):
            return False
        domain = str(row.get("domain") or "").strip()
        if not domain or domain not in UNC_DOMAINS:
            return False
        reason = str(row.get("reason") or "").strip().lower()
        # M53-22: a DETERMINISTIC detection counts. The sync cue layer names a
        # family before the aux worker can land (`det_<family>`) precisely so a
        # POST that beats the worker still sees a detected domain; refusing that
        # reason re-introduced the very gap the write above closes.
        return (reason == "in_domain"
                or reason.startswith("unconfirmed")
                or reason.startswith("det_"))
    except Exception:  # noqa: BLE001
        return False


def _emit(log: Optional[Callable[..., Any]], row: Dict[str, Any],
          session_id: str) -> None:
    """One PRE row per gate pass. Never prints the ask and never prints a closed
    label. Telemetry must never break a turn."""
    if log is None:
        return
    try:
        crit = row.get("criterion") or {}
        log("PRE",
            event_detail="domain_gate",
            question=PRE_QUESTION,
            gate=row.get("gate"),
            domain=str(row.get("domain") or ""),
            reason=str(row.get("reason") or ""),
            cause=str(row.get("cause") or ""),
            claim=str(row.get("claim") or ""),
            criterion_kind=str(crit.get("kind") or ""),
            criterion_units=int(crit.get("units") or 0),
            criterion_available=1 if crit.get("available") is True else 0,
            latency_ms=int(row.get("latency_ms") or 0),
            session_id=session_id)
    except Exception:  # noqa: BLE001 — telemetry never breaks a turn
        pass


def _worker(session_id: str, ask: str, log: Optional[Callable[..., Any]]) -> None:
    """The concurrent half: evaluate, write the turn record, log, exit."""
    import time
    t0 = time.monotonic()
    res = evaluate(ask, session_id=session_id)
    res["latency_ms"] = int((time.monotonic() - t0) * 1000)
    lane_id = _lane_registry.lane_for_pre_question(PRE_QUESTION) or ""
    try:
        _state.mark_pre_domain(
            session_id, ask, lane=lane_id, question=PRE_QUESTION,
            gate=res.get("gate") or GATE_ABSTAIN,
            domain=str(res.get("domain") or ""),
            criterion=res.get("criterion") or {},
            reason=str(res.get("reason") or ""),
            cause=str(res.get("cause") or ""),
            latency_ms=res.get("latency_ms") or 0,
            state=res.get("state") or GATE_ABSTAIN)
    except Exception:  # noqa: BLE001 — the record never breaks the worker
        logger.debug("domain_gate record error", exc_info=True)
    _emit(log, res, session_id)


def start(session_id: str, ask: str, *, log: Optional[Callable[..., Any]] = None) -> None:
    """PRE seam: arm the gate for this ask and return IMMEDIATELY.

    Zero added latency is structural, not tuned: `on_llm_request` returns before
    the gateway issues the generation call, so a daemon thread started here runs
    concurrently with generation and nothing ever joins it (the same primitive
    the PRE intent probe already exercises per turn).

    A `pending` record is written first, so a POST that beats the worker reads
    `abstain` rather than a stale verdict or a false "safe". Never raises.
    """
    try:
        sid = str(session_id or "").strip()
        text = str(ask or "")
        if not sid or not text.strip():
            return
        lane_id = _lane_registry.lane_for_pre_question(PRE_QUESTION) or ""
        # M53-22: DETERMINISTIC VERDICT FIRST.
        # The aux worker is asynchronous, so a POST that beat it read `pending`,
        # `read()` turned that into None, `verdict()` turned None into `abstain`,
        # and `domain_detected` was False -- the turn was recorded as
        # `not_consulted`. Measured: a csam ask reached the POST seam with NO
        # gate row at all while `deterministic_domains(ask)` named
        # `sexual_explicit` synchronously and for free.
        # Detection must not depend on an aux call landing in time, so the
        # deterministic cue layer writes the verdict the turn carries. The
        # worker still overwrites it with the finer verdict if/when it lands.
        try:
            _det = deterministic_domains(text)
        except Exception:  # noqa: BLE001 — a cue read never breaks the gate
            _det = ()
        if _det:
            _dom = str(_det[0])
            _state.mark_pre_domain(sid, text, lane=lane_id, question=PRE_QUESTION,
                                   gate=GATE_FLAGGED, state=GATE_FLAGGED,
                                   domain=_dom, reason="det_" + _dom,
                                   criterion=extract_criterion(text))
        else:
            _state.mark_pre_domain(sid, text, lane=lane_id, question=PRE_QUESTION,
                                   gate=GATE_PENDING, state=GATE_PENDING,
                                   criterion=extract_criterion(text))
        try:
            threading.Thread(target=_worker, args=(sid, text, log),
                             name="domain-gate", daemon=True).start()
        except Exception:  # noqa: BLE001 — no thread, no verdict: pending stays
            logger.debug("domain_gate thread start error", exc_info=True)
    except Exception:  # noqa: BLE001 — the gate never costs a turn
        logger.debug("domain_gate start error", exc_info=True)


def read(session_id: str, ask: Optional[str] = None) -> Optional[dict]:
    """POST seam: the verdict for THIS turn, or None.

    Non-blocking by construction (`Event.wait(0)` semantics — a plain dict read,
    never a join). None means the worker has not landed, the gate never armed,
    the record expired, or (when `ask` is given) it belongs to a different ask:
    every one of those is `abstain`. `gate_semantics` is attached from the lane
    contract held on the record, so a caller evaluates ordered rows and never
    compares a lane id.
    """
    try:
        row = _state.pre_domain(str(session_id or ""), ask=ask)
        if not isinstance(row, dict):
            return None
        if str(row.get("state") or row.get("gate") or "") == GATE_PENDING:
            return None
        # Turn binding: a verdict stamped for an OLDER turn is not this turn's
        # answer (the PRE overwrites, but a turn that never re-armed must not
        # inherit its predecessor's verdict). Mismatch => abstain.
        _turn = row.get("turn")
        if _turn and int(_turn) != int(_state.current_turn_id(str(session_id or ""))):
            return None
        contract = _lane_registry.resolve_contract(str(row.get("lane") or ""))
        row["gate_semantics"] = tuple(getattr(contract, "gate_semantics", ()) or ())
        row["replacement"] = str(getattr(contract, "replacement", "") or "")
        # The criterion is READ here, never re-extracted: POST must not pay for
        # an analysis PRE already did, and a re-run could disagree with the
        # record the turn was produced against. `criterion_available` is the
        # explicit surface — False means no criterion (grade nothing, body
        # path), never "empty criterion".
        row["criterion_available"] = criterion_of(row) is not None
        return row
    except Exception:  # noqa: BLE001
        return None
