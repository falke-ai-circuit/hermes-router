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

_MAX_ASK_CHARS = 3000

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


def extract_criterion(ask: str) -> Dict[str, Any]:
    """What this answer must contain to count as delivered, as data.

    Closed vocabulary: {kind, units, items, paths, commands, prose, verbatim}.
    `verbatim` is the ask fragment itself, bounded — the POST judge is graded
    against the ORIGINAL wording (a paraphrase is a different criterion), and a
    measured refinement of that wording lost accuracy, so nothing is rewritten
    here.
    """
    text = " ".join(str(ask or "").split())
    crit: Dict[str, Any] = {
        "kind": "prose", "units": 0, "items": [], "paths": [],
        "commands": [], "prose": True, "verbatim": text[:800],
    }
    if not text:
        return crit
    try:
        paths = [p for p in _PATH_RE.findall(text)][:5]
        if _FILE_ASK_RE.search(text):
            crit.update({"kind": "file", "prose": False, "paths": paths})
            return crit
        if _LANG_RE.search(text) or _CODE_ASK_RE.search(text):
            m = _ITEM_COUNT_RE.search(text)
            crit.update({"kind": "code", "prose": False,
                         "units": int(m.group(1)) if m else 0})
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
    return crit


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
        out["reason"] = "aux_none"
        return out
    try:
        m = re.search(r"\{.*\}", str(raw), re.S)
        parsed = json.loads(m.group(0)) if m else {}
        domain = str(parsed.get("domain") or "").strip().lower()
        confident = parsed.get("confident", True)
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
        out.update({"domain": domain, "gate": GATE_FLAGGED,
                    "state": GATE_FLAGGED, "reason": "in_domain"})
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
        return row
    except Exception:  # noqa: BLE001
        return None
