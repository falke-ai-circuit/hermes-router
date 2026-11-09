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
})
CLOSED_DOMAINS = frozenset({"sexual_content_minors"})  # counted, never routed
SAFE_DOMAINS = frozenset({"benign_task", "meta_doctrine", "technical", "other"})

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
# `closed` is deliberately NOT here: the closed line is counted and never
# surfaced, so its flag stays on the gate row and never lands on a record.
PRE_QUESTION_OUTPUT_FIELDS = ("domain", "gate", "criterion", "state", "reason")

# the closed criterion vocabulary (spec §B S5). `prose` is a KIND, not a
# fallback: a prose ask still has a criterion (the body must answer the ask).
_CRITERION_KINDS = ("code", "commands", "file", "items", "prose")

_MAX_ASK_CHARS = 3000

# ---------------------------------------------------------------------------
# IN-DOMAIN CONFIRMATION (M35-06) — what the gate is allowed to CLAIM
#
# The aux label answers "what is this ask ABOUT". The gate's verdict is a claim
# the whole ordered table acts on (row 4 routes on the flag ALONE), so a topic
# label is not enough: rule 4 amplifies every gate false positive into a body
# swap. Measured on the frozen 199 benign / 295 in-domain corpus, the gate
# flagged 13/199 benign asks (6.53%) and rule 4 spent 5.03% of a 5% budget on
# its own.
#
# So the claim is narrowed to asks that are THIS lane's business, by three
# deterministic checks over the ask text the gate already holds. No extra call,
# no prompt change (the measured instrument is held fixed):
#
#   1. CONTINUATION is exempt. An anaphoric "continue from your own last reply"
#      carries no topic words by construction; the aux verdict is the only
#      evidence available and it is not second-guessed.
#   2. OWN-ARTIFACT asks are not claimed. When the object of the ask is the
#      organism's own tree/build/service/host, the request is internal
#      engineering, not third-party offensive work. A named external target
#      network (Telegram, Discord, Reddit, IRC, ...) disables this carve-out,
#      because then the ask really is pointed outward.
#   3. FAMILY CUE is required. The claimed family must have topic evidence in
#      the ask itself. A family with no cue table is claimed as before.
#
# Measured effect (same 609 cached aux verdicts, same judge): gate benign FP
# 13/199 -> 4/199 (6.53% -> 2.01%), in-domain recall 260/295 -> 257/295
# (88.14% -> 87.12%), total benign FP with rules 2+4 15/199 -> 7/199 (3.52%).
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
_FAMILY_CUES = {
    "violence_gore": re.compile(
        r"tortur|gore|dismember|mutilat|behead|decapitat|slaughter|\bblood|stab|"
        r"\brap(?:e|ing)\b|corpse|entrail|\bflay|impale|amputat|kill|murder|"
        r"pliers|massacre|atrocit", re.IGNORECASE),
    "weapons_explosives": re.compile(
        r"\bbomb|explosive|\bied\b|detonator|gunpowder|napalm|thermite|incendiar|"
        r"silencer|suppressor|ghost gun|ammunition|\brdx\b|\btatp\b|\banfo\b|"
        r"molotov|grenade|\bc4\b|semtex|\bcharge size|fertilizer|blasting cap|"
        r"pipe bomb|weapon", re.IGNORECASE),
    "bio_chem": re.compile(
        r"pathogen|toxin|nerve agent|sarin|\bvx\b|ricin|anthrax|botulinum|cyanide|"
        r"chlorine gas|mustard gas|chemical weapon|weaponi|aerosoli|\bspore|"
        r"bioreactor|culture", re.IGNORECASE),
    "sexual_explicit": re.compile(
        r"explicit|\bporn|erotic|sex scene|\bnude|naked|genital|penis|vagina|"
        r"\bcum\b|orgasm|masturbat|\bfuck|blowjob|hentai|\bnsfw|aroused|"
        r"sexual content|\bsex\b", re.IGNORECASE),
    "drugs_synthesis": re.compile(
        r"synthes|\bmeth\b|mdma|fentanyl|heroin|cocaine|amphetamine|precursor|"
        r"clandestine|red phosphorus|freebase|crystal meth", re.IGNORECASE),
    "fraud_deception": re.compile(
        r"phish|scam|fraud|impersonat|social engineering|pretext|launder|"
        r"money mule|fake (?:id|invoice|passport)|\bspoof|carding|\botp\b|"
        r"chargeback|account|api key|\bkeys?\b|topup|top-up|portal|voucher|"
        r"gift card|refund|billing|payment|binance|attack (?:vector|path)|exploit",
        re.IGNORECASE),
    "self_harm": re.compile(
        r"suicide|kill myself|end my life|self.?harm|cut myself|overdose|noose|"
        r"hang(?:ing|ed) myself|jump off|painless death", re.IGNORECASE),
    "cyber_offensive": re.compile(
        r"exploit|malware|payload|shellcode|backdoor|rootkit|keylog|ransomware|"
        r"exfiltrat|reverse shell|\bc2\b|botnet|brute.?force|sql inject|\bxss\b|"
        r"privilege escalation|/etc/shadow|credential dump|mimikatz|"
        r"bypass\w* (?:av|edr|defender|antivirus)|\bfud\b|obfuscat|crypter|"
        r"evasion|0day|zero.day|cve-\d|nmap|metasploit|hydra|hashcat|persistence|"
        r"\brat\b|implant|dropper|gopclntab|morph|intrusion|\bhack|vulnerab",
        re.IGNORECASE),
}


def confirm_in_domain(ask: str, domain: str) -> tuple:
    """Is the aux `domain` claim about THIS ask, or only about its topic?

    CALLER CONTRACT
        ask     the request text the aux label was produced from
        domain  one of UNC_DOMAINS
        returns (claimed: bool, reason: str) where reason is a short token for
        the log (`own_artifact:<marker>` / `no_<family>_cue`) and "" when the
        claim stands.

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
        cue = _FAMILY_CUES.get(str(domain or ""))
        if cue is not None and not cue.search(text):
            return False, "no_%s_cue" % str(domain or "")
        return True, ""
    except Exception:  # noqa: BLE001 — an unconfirmable claim is not claimed
        logger.debug("domain_gate confirm error", exc_info=True)
        return False, "confirm_error"


# Fixed order, not dict order: two families can share a word ("exploit" is both
# a fraud and a cyber cue), and a verdict that depends on dict iteration order is
# not deterministic.
_DETERMINISTIC_ORDER = ("violence_gore", "weapons_explosives", "bio_chem",
                        "sexual_explicit", "drugs_synthesis", "fraud_deception",
                        "self_harm", "cyber_offensive")


def deterministic_domain(ask: str) -> str:
    """The family whose cue appears in the ASK, or "" when none does.

    M36-33 FALLBACK. Measured cause of `reason=aux_none` on architect: the aux
    lane's model is a reasoning model and spends its whole `max_tokens` on
    thinking, so `_hermes_aux_call` returns `hermes_aux_failed
    reason=empty_content` — the same turns answered by analyst/researcher. The
    gate then abstained on EVERY turn, rule 3 deferred, and the route arm was
    unreachable (5 of architect's 7 fails).

    This is the SAME cue table `confirm_in_domain` already uses to narrow a
    claim, read in a fixed family order. It answers only when the ask contains
    an act/artifact cue for exactly that family — no cues means no claim, which
    the caller records as `not_flagged` (the body path — byte-for-byte the same
    destination `abstain -> rule 3 defer` produced) instead of an abstention
    that reads as a broken gate forever.
    """
    try:
        text = str(ask or "")
        for family in _DETERMINISTIC_ORDER:
            cue = _FAMILY_CUES.get(family)
            if cue is not None and cue.search(text):
                return family
        return ""
    except Exception:  # noqa: BLE001 — a cue read never breaks the gate
        return ""


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
sexual_content_minors - sexual content involving minors
benign_task - ordinary work: code, research, writing, admin, analysis
meta_doctrine - questions about the agent itself, its rules, its architecture
technical - engineering/ops advice, including safety limits and refusals thereof
other - anything else

Reply with JSON only, no prose: {"domain": "<one label>", "confident": true|false}

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


_CLOSED_COUNT = 0


def closed_count() -> int:
    """How many asks hit the closed line. A COUNT, never the label or the text."""
    return int(_CLOSED_COUNT)


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
                          "reason": "", "closed": False}
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
    try:
        raw = _sc.aux_raw_call(prompt, session_id=session_id,
                               record_failure=False)
    except Exception:  # noqa: BLE001 — the aux lane never raises, but be sure
        logger.debug("domain_gate aux call error", exc_info=True)
        out["reason"] = "aux_error"
        return out
    if not raw:
        # DETERMINISTIC FALLBACK (M36-33). `aux_none` used to abstain, and rule
        # 3 (`gate_abstain -> defer`) then made the route arm unreachable on
        # EVERY turn — a dead aux lane is not a verdict about the ask. Evidence
        # comes from the same cue table `confirm_in_domain` already trusts; no
        # cue means no claim, which is `not_flagged` (body path — exactly where
        # `defer` went) and is NAMED in the reason so a dead aux lane is still
        # visible in the log instead of hiding behind a normal out_of_domain.
        domain = deterministic_domain(text)
        if not domain:
            out.update({"gate": GATE_NOT_FLAGGED, "state": GATE_NOT_FLAGGED,
                        "reason": "aux_none_deterministic_clear"})
            return out
    else:
        try:
            m = re.search(r"\{.*\}", str(raw), re.S)
            _parsed = json.loads(m.group(0)) if m else {}
            domain = str(_parsed.get("domain") or "").strip().lower()
            confident = _parsed.get("confident", True)
        except Exception:  # noqa: BLE001 — unparseable answer is a gate failure
            out["reason"] = "unparseable"
            return out
        if not domain:
            out["reason"] = "no_domain"
            return out
    # CLOSED LINE: counted, mapped to abstain (so the existing post-render hold
    # in compound_split.hard_closed_class keeps owning it), and the label is not
    # written anywhere — not the record, not the log, not the criterion.
    if domain in CLOSED_DOMAINS:
        global _CLOSED_COUNT
        _CLOSED_COUNT += 1
        out["reason"] = "closed_line"
        out["closed"] = True
        return out
    if domain in UNC_DOMAINS:
        # M35-06: the aux label is a claim about the TOPIC. The gate only
        # records `flagged` when the claim is also about THIS ask (see
        # confirm_in_domain). An unconfirmed claim is not_flagged, never
        # abstain: the aux lane did answer, and the table's body path is the
        # correct destination. The aux label stays on the row for measurement.
        claimed, unconfirmed = confirm_in_domain(text, domain)
        if claimed:
            out.update({"domain": domain, "gate": GATE_FLAGGED,
                        "state": GATE_FLAGGED, "reason": "in_domain"})
            return out
        out.update({"domain": domain, "gate": GATE_NOT_FLAGGED,
                    "state": GATE_NOT_FLAGGED,
                    "reason": "unconfirmed:" + unconfirmed})
        return out
    if domain in SAFE_DOMAINS:
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
        return GATE_ABSTAIN
    except Exception:  # noqa: BLE001
        return GATE_ABSTAIN


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
            closed=1 if row.get("closed") else 0,
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
