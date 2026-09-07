"""v3.6.0 trigger-engine cascade — §11 (Goran-direct: quantify + generalize,
both pre and post; TRIMMED to operator scale per §11.4).

Three-state decision contract shared by PRE and POST arms:

    decision = {action: route|pass|abstain, confidence: float,
                evidence: [feature_id, ...], detector_version, latency_ms}

Abstain is FIRST-CLASS: uncertainty -> abstain -> fail-open to today's
behavior. route/pass are ROUTING decisions only — the detector never deletes
or sanitizes user content, never blocks, never rewrites (§11.7 doctrine:
router routes, never filters).

Cascade layers (§11.1):
  1. canonicalization (detection-only) — unicode/case/punct normalization +
     lang-ID with unknown/mixed state. Original text is preserved everywhere;
     canonical form feeds detection ONLY.
  2. deterministic fast path — the EXISTING compiled pattern groups +
     doctrine-quote suppression + clarify-walk remain the production
     detectors (classifier.py); this module's structural gate adds the
     measurable feature-family layer.
  3. cheap structural gate — OR of feature families: topic_or_harm AND
     (procedural | acquisition | target/route | ambiguous-context). Pure
     regex/lexicographic, zero LLM.
  4. semantic classifier STUB — wired to the existing MiniMax aux infra but
     DEFAULT OFF (config-gated: classification.semantic_gate). §12-A2: when
     disabled the stub contributes ZERO runtime cost beyond the config
     lookup already paid by the caller — the hot path never reaches it. When
     enabled it may return route/pass/abstain + confidence; failure ->
     today's behavior.
  5. policy layer lives at the call site (existing route behavior); this
     module NEVER acts — it only decides + logs.

POST weak-compliance arm (§11.3, B adopted): CONJUNCTIVE non-answer detector
emitting REASON CODES (no_recommendation / repeated_caveats /
missing_requested_fields). Scores and logs ONLY — never rewrites, rejects,
or warns. §12-A2: async/replay-only (the replay harness + Phase-1 shadow run
it; nothing in the live turn path calls it synchronously).

Counterfactual replay harness (§11.4 KEEP #1 — THE instrument that decides
the semantic arm's fate): replays logged turn records through the old
engine (deterministic detectors) vs the cascade, diffs to a report file. No
live behavior change; no dashboards (route logs + /router stats suffice).

Doctrine pins (tested): abstain = pass-through; semantic failure = today's
behavior; route/pass are routing decisions only; detector never filters.
"""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DETECTOR_VERSION = "cascade-v1"     # bump on any detection-logic change
ONTOLOGY_VERSION = "trigger-ontology-1"

ACTION_ROUTE = "route"
ACTION_PASS = "pass"
ACTION_ABSTAIN = "abstain"
VALID_ACTIONS = (ACTION_ROUTE, ACTION_PASS, ACTION_ABSTAIN)

# Feature families (§11.1 layer 3) — measurable, evidence-carrying IDs.
FEATURE_TOPIC_OR_HARM = "topic_or_harm"
FEATURE_PROCEDURAL = "procedural"
FEATURE_ACQUISITION = "acquisition"
FEATURE_TARGET_ROUTE = "target_route"
FEATURE_AMBIGUOUS_CONTEXT = "ambiguous_context"

_WEAK_COMPLIANCE_VERSION = "weak-compliance-v1"

# ---------------------------------------------------------------------------
# Layer 1 — canonicalization (detection-only; original text never replaced)
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u00ad]+")  # zero-width/joiners


def canonicalize(text: str) -> Dict[str, Any]:
    """Detection-only canonical form: unicode NFKC fold, zero-width strip,
    case-fold, whitespace collapse, punctuation-squash for the lexical gate.
    Returns {canonical, lang, lang_state}. lang_state in {known, unknown,
    mixed}; detection is script-ratio heuristic (cheap, no LLM). Never
    raises; original text stays with the caller (audit/render use it)."""
    try:
        raw = str(text or "")
        folded = unicodedata.normalize("NFKC", raw)
        folded = _PUNCT_RE.sub("", folded)
        canon = folded.casefold()
        canon = re.sub(r"\s+", " ", canon).strip()
        # script ratio lang-ID: enough signal to mark unknown/mixed; exact
        # language IDs are NOT needed at Phase 0 (unknown/mixed is the only
        # consequential state — it forces abstain in the semantic stub).
        latin = sum(1 for ch in canon if "a" <= ch <= "z")
        non_ascii_letters = sum(1 for ch in canon if ch.isalpha() and not ("a" <= ch <= "z"))
        alpha = latin + non_ascii_letters
        if alpha < 8:
            lang, state = "unknown", "unknown"
        elif latin >= 0.9 * alpha:
            lang, state = "latin", "known"
        elif non_ascii_letters >= 0.9 * alpha:
            lang, state = "nonlatin", "known"
        else:
            lang, state = "mixed", "mixed"
        return {"canonical": canon, "lang": lang, "lang_state": state}
    except Exception:  # noqa: BLE001
        return {"canonical": "", "lang": "unknown", "lang_state": "unknown"}


# ---------------------------------------------------------------------------
# Layer 3 — cheap structural gate (pure regex; zero LLM; evidence-carrying)
# ---------------------------------------------------------------------------

_TOPIC_OR_HARM_RE = re.compile(
    r"(?:weapon|explosive|bomb|detonat|ricin|anthrax|toxin|pathogen|bioweapon"
    r"|csam|minor|underage|traffick|smuggl|defam|blackmail|extort"
    r"|ied|tatp|hmtd|nerve\s*agent|sarin|vx\b)",
    re.IGNORECASE,
)
_PROCEDURAL_RE = re.compile(
    r"(?:step.?by.?step|protocol|procedure|instructions|how\s*(?:to|do\s*(?:i|we|you))"
    r"|synthes\w*|build|construct|assemble|cook\b|brew\b|extract|purif|wire\b)",
    re.IGNORECASE,
)
_ACQUISITION_RE = re.compile(
    r"(?:where\s+(?:can|could)\s+(?:i|we)\b|buy|purchase|acquire|obtain|order\b"
    r"|source\b|ship\b|deliver\b|smuggl|get\s+away\s+with)",
    re.IGNORECASE,
)
_TARGET_ROUTE_RE = re.compile(
    r"(?:route|across\s+(?:the\s+)?border|border\s+cross|via\s+\w+\s+border"
    r"|address|home\s+address|personal\s+details|real\s+name|where\s+(?:does|do[e]?s)?\s*\w+\s+live)",
    re.IGNORECASE,
)
_AMBIGUOUS_CONTEXT_RE = re.compile(
    r"(?:hypothetical|fiction|novel|screenplay|for\s+a\s+story|in\s+a\s+game"
    r"|asking\s+for\s+a\s+friend|theoretically|academically)",
    re.IGNORECASE,
)


def structural_gate(canonical: str) -> Dict[str, Any]:
    """Layer 3: OR of measurable feature families. Fires (gate=True) only on
    topic_or_harm AND at least one of (procedural | acquisition |
    target/route | ambiguous-context). Returns the decision-shaped dict with
    evidence = matched feature IDs. Pure regex — hot-path safe. Never
    raises."""
    started = time.time()
    try:
        c = str(canonical or "")
        ev: List[str] = []
        topic = bool(_TOPIC_OR_HARM_RE.search(c))
        if topic:
            ev.append(FEATURE_TOPIC_OR_HARM)
            if _PROCEDURAL_RE.search(c):
                ev.append(FEATURE_PROCEDURAL)
            if _ACQUISITION_RE.search(c):
                ev.append(FEATURE_ACQUISITION)
            if _TARGET_ROUTE_RE.search(c):
                ev.append(FEATURE_TARGET_ROUTE)
            if _AMBIGUOUS_CONTEXT_RE.search(c):
                ev.append(FEATURE_AMBIGUOUS_CONTEXT)
        gate = topic and len(ev) >= 2
        return {
            "action": ACTION_ROUTE if gate else ACTION_PASS,
            "confidence": 0.8 if gate else 0.9,   # deterministic layers are confident
            "evidence": ev,
            "ontology_version": ONTOLOGY_VERSION,
            "detector_version": DETECTOR_VERSION,
            "latency_ms": round((time.time() - started) * 1000.0, 3),
            "gate": gate,
        }
    except Exception:  # noqa: BLE001 — fail open to pass
        return {
            "action": ACTION_PASS, "confidence": 0.0, "evidence": [],
            "ontology_version": ONTOLOGY_VERSION, "detector_version": DETECTOR_VERSION,
            "latency_ms": round((time.time() - started) * 1000.0, 3), "gate": False,
        }


# ---------------------------------------------------------------------------
# Layer 4 — semantic classifier STUB (DEFAULT OFF; config-gated)
# ---------------------------------------------------------------------------


def semantic_gate_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """classification.semantic_gate (bool, default FALSE). §11.5 Phase-0:
    the semantic arm ships OFF and only the replay harness exercises it.
    Reading a config block the caller already holds costs nothing extra."""
    try:
        if isinstance(cfg, dict):
            cls = cfg.get("classification")
            return bool((cls or {}).get("semantic_gate", False)) if isinstance(cls, dict) else False
        from hermes_cli.config import load_config

        c = load_config()
        section = None
        if isinstance(c, dict):
            section = c.get("hermes_router")
            if not (isinstance(section, dict) and section):
                section = c.get("uncensored_router")
        cls = (section or {}).get("classification") if isinstance(section, dict) else None
        return bool(cls.get("semantic_gate", False)) if isinstance(cls, dict) else False
    except Exception:  # noqa: BLE001
        return False


def semantic_stub(text: str, lang_state: str) -> Dict[str, Any]:
    """Layer 4 stub. DEFAULT-OFF arm: wired to the MiniMax aux infra shape
    but performing NO aux call at Phase 0 — it exists so the decision
    contract, the abstain-first-class rule, and the replay harness have a
    real third layer to compare against. Behavior:
      - unknown/mixed language -> ABSTAIN (uncertainty is first-class);
      - otherwise ABSTAIN with low confidence (the stub asserts no opinion).
    When the semantic arm is later trained (post-replay verdict), this
    function is the swap point — its contract (route/pass/abstain +
    confidence + evidence) is already the §11.1 shape. Failure => abstain =>
    today's behavior (fail-open doctrine). Never raises; never makes a
    network call at Phase 0."""
    started = time.time()
    try:
        if lang_state in ("unknown", "mixed"):
            return {
                "action": ACTION_ABSTAIN, "confidence": 0.0,
                "evidence": ["lang_" + str(lang_state)],
                "ontology_version": ONTOLOGY_VERSION, "detector_version": "semantic-stub-0",
                "latency_ms": round((time.time() - started) * 1000.0, 3),
            }
        return {
            "action": ACTION_ABSTAIN, "confidence": 0.0, "evidence": ["stub_no_opinion"],
            "ontology_version": ONTOLOGY_VERSION, "detector_version": "semantic-stub-0",
            "latency_ms": round((time.time() - started) * 1000.0, 3),
        }
    except Exception:  # noqa: BLE001 — abstain on any internal failure
        return {
            "action": ACTION_ABSTAIN, "confidence": 0.0, "evidence": ["stub_error"],
            "ontology_version": ONTOLOGY_VERSION, "detector_version": "semantic-stub-0",
            "latency_ms": round((time.time() - started) * 1000.0, 3),
        }


def decide(text: str, *, semantic_enabled: bool = False,
           cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Full cascade evaluation (PRE side). Order: canonicalize -> structural
    gate -> (only when explicitly enabled) semantic stub. Abstain from any
    layer resolves to PASS-THROUGH operationally — abstain is a telemetry
    state, never an intervention. The RETURNED decision is data only; the
    caller owns policy. Never raises."""
    try:
        canon = canonicalize(text)
        g = structural_gate(canon["canonical"])
        sem = None
        enabled = semantic_enabled or semantic_gate_enabled(cfg)
        if enabled:
            sem = semantic_stub(text, canon["lang_state"])
        final_action = g["action"]
        if sem is not None:
            # semantic overrides only when it has an opinion (confidence>0);
            # abstain = keep deterministic verdict (telemetry-only state).
            if sem["action"] in VALID_ACTIONS and float(sem.get("confidence") or 0) > 0:
                final_action = sem["action"]
            else:
                final_action = g["action"] if g["action"] in (ACTION_ROUTE, ACTION_PASS) \
                    else ACTION_PASS
        return {
            "action": final_action,
            "confidence": g.get("confidence", 0.0),
            "evidence": list(g.get("evidence") or []),
            "ontology_version": ONTOLOGY_VERSION,
            "detector_version": DETECTOR_VERSION,
            "latency_ms": round(float(g.get("latency_ms") or 0.0)
                                + float((sem or {}).get("latency_ms") or 0.0), 3),
            "lang": canon.get("lang"), "lang_state": canon.get("lang_state"),
            "structural_gate": bool(g.get("gate")),
            "semantic_ran": bool(sem is not None),
        }
    except Exception:  # noqa: BLE001 — any cascade failure = today's behavior
        return {
            "action": ACTION_PASS, "confidence": 0.0, "evidence": [],
            "ontology_version": ONTOLOGY_VERSION, "detector_version": DETECTOR_VERSION,
            "latency_ms": 0.0, "lang": "unknown", "lang_state": "unknown",
            "structural_gate": False, "semantic_ran": False,
        }


# ---------------------------------------------------------------------------
# POST weak-compliance arm (§11.3) — conjunctive detector + reason codes.
# Scores and logs ONLY. Async/replay-only (§12-A2): nothing in the live turn
# path calls this synchronously.
# ---------------------------------------------------------------------------

_HEDGE_FAMILIES: Dict[str, Tuple[str, ...]] = {
    "epistemic": (r"\bi (?:am not|'m not) (?:sure|certain|confident)\b",
                  r"\b(?:it (?:is|'s) (?:possible|unclear)|not clear whether)\b",
                  r"\b(?:might|may|could) (?:be|mean|suggest)\b",),
    "disclaimer_meta": (r"\b(?:i am| i'm) an ai\b", r"\bas an ai\b",
                        r"\b(?:consult|contact) (?:a|an) (?:professional|expert|lawyer|doctor)\b",
                        r"\bthis is not (?:legal|medical|financial) advice\b",),
    "balance_padding": (r"\bon the other hand\b", r"\bhowever,?\s+it(?:'s| is) (?:also|worth)\b",
                        r"\bthere are (?:two|both) sides\b",),
    "attribution_evasion": (r"\b(?:some|many|most) (?:people|experts) (?:say|believe|argue)\b",
                            r"\b(?:sources|studies) (?:suggest|indicate)\b",),
    "excessive_qualification": (r"\bdepending on (?:the|your) (?:situation|circumstances|context)\b",
                                r"\bwhat works for (?:one|someone) (?:person|may not)\b",),
}
_HEDGE_RES: Dict[str, Tuple[re.Pattern, ...]] = {
    k: tuple(re.compile(p, re.IGNORECASE) for p in v) for k, v in _HEDGE_FAMILIES.items()
}
_ANSWERABILITY_RE = re.compile(
    r"(?:what should|which (?:one|option)|should i|decide|recommend|plan\b|next step"
    r"|how do i|give me)\b", re.IGNORECASE)
_DIRECT_ANSWER_RE = re.compile(
    r"\b(?:i recommend|my recommendation|the answer is|do this|choose\b|use\b"
    r"|the best (?:option|approach) is|next step(?:s)? (?:is|are)|in short|conclusion)\b",
    re.IGNORECASE)
_NON_ANSWER_CLOSERS_RE = re.compile(
    r"(?:let me know if|hope this helps|it depends|there is no one[- ]size"
    r"|as always,? (?:your|the) (?:mileage|situation))\b", re.IGNORECASE)
_MIN_WORDS = 150
_MIN_HEDGE_SPANS = 3
_MIN_HEDGE_FAMILIES = 2


def weak_compliance_score(response_text: str, user_ask: str = "",
                          requested_fields: Optional[List[str]] = None) -> Dict[str, Any]:
    """Conjunctive non-answer detector (§11.3). Flag ONLY when ALL:
    (1) answerability gate — the ask requests a decision/plan/concrete output;
    (2) >=3 hedge spans from >=2 families;
    (3) non-answer markers — no direct answer span + non-answer closer;
    (4) essay-shape — multiple paragraphs of qualification;
    and response >= ~150 words. Emits REASON CODES, never a bare 'hedging'
    label. SCORES + LOGS ONLY — never rewrites/rejects/warns. Never raises.
    Async/replay-only per §12-A2."""
    started = time.time()
    try:
        text = str(response_text or "")
        words = len(text.split())
        reasons: List[str] = []
        hedge_spans = 0
        hedge_families_hit = set()
        for fam, res in _HEDGE_RES.items():
            for rx in res:
                if rx.search(text):
                    hedge_spans += 1
                    hedge_families_hit.add(fam)
                    break
        answerable = bool(_ANSWERABILITY_RE.search(str(user_ask or "")))
        has_direct = bool(_DIRECT_ANSWER_RE.search(text))
        ends_on_caveat = bool(_NON_ANSWER_CLOSERS_RE.search(text[-400:]))
        paragraphs = text.count("\n\n") + 1
        missing_fields = [f for f in (requested_fields or [])
                          if str(f).lower() not in text.lower()]
        no_recommendation = answerable and not has_direct
        repeated_caveats = (hedge_spans >= _MIN_HEDGE_SPANS
                            and len(hedge_families_hit) >= _MIN_HEDGE_FAMILIES)
        essay_shape = paragraphs >= 3 and (not has_direct or ends_on_caveat)
        if no_recommendation:
            reasons.append("no_recommendation")
        if repeated_caveats:
            reasons.append("repeated_caveats")
        if missing_fields:
            reasons.append("missing_requested_fields")
        conjunctive = (answerable and repeated_caveats
                       and (no_recommendation or ends_on_caveat)
                       and essay_shape and words >= _MIN_WORDS)
        return {
            "flagged": bool(conjunctive),
            "reason_codes": reasons,
            "words": words,
            "hedge_spans": hedge_spans,
            "hedge_families": sorted(hedge_families_hit),
            "answerable_ask": answerable,
            "has_direct_answer": has_direct,
            "paragraphs": paragraphs,
            "detector_version": _WEAK_COMPLIANCE_VERSION,
            "latency_ms": round((time.time() - started) * 1000.0, 3),
        }
    except Exception:  # noqa: BLE001 — scorer failure = no flag (fail-open)
        return {
            "flagged": False, "reason_codes": [], "words": 0, "hedge_spans": 0,
            "hedge_families": [], "answerable_ask": False, "has_direct_answer": False,
            "paragraphs": 0, "detector_version": _WEAK_COMPLIANCE_VERSION,
            "latency_ms": round((time.time() - started) * 1000.0, 3),
        }


# ---------------------------------------------------------------------------
# Counterfactual replay harness (§11.4 KEEP #1) — CLI-runnable, no live calls
# ---------------------------------------------------------------------------


def replay_file(input_path: str, output_path: str) -> Dict[str, Any]:
    """Replay logged turn records through the deterministic cascade and write
    a diff report. Input: JSONL with {turn_id, text, old_decision} rows (old
    decision = production baseline — the existing detector's outcome).
    Output: report {total, agree, cascade_route, cascade_pass, cascade_abstain,
    disagreements: [...]} — the operator spot-check surface (§11.4 KEEP #2).
    No live behavior change; never raises (errors land in the report)."""
    report: Dict[str, Any] = {
        "input": str(input_path), "output": str(output_path),
        "total": 0, "agree": 0, "disagreements": [],
        "cascade_route": 0, "cascade_pass": 0, "cascade_abstain": 0,
        "detector_version": DETECTOR_VERSION,
    }
    try:
        rows: List[Dict[str, Any]] = []
        with open(input_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # corrupt line skipped (ledger discipline)
                if isinstance(rec, dict):
                    rows.append(rec)
        report["total"] = len(rows)
        for rec in rows:
            text = str(rec.get("text") or "")
            d = decide(text, semantic_enabled=True)  # replay exercises the stub too
            old = str(rec.get("old_decision") or "pass")
            new = str(d.get("action") or ACTION_PASS)
            report["cascade_" + str(new)] = int(report.get("cascade_" + str(new), 0)) + 1
            if new == old:
                report["agree"] = int(report["agree"]) + 1
            else:
                report["disagreements"].append({
                    "turn_id": str(rec.get("turn_id") or ""),
                    "old_decision": old, "new_decision": new,
                    "confidence": d.get("confidence"), "evidence": d.get("evidence"),
                    "gate_reason": "structural" if d.get("structural_gate") else "cascade",
                })
        # bounded disagreements (report stays readable; counts stay exact)
        report["disagreements"] = report["disagreements"][:200]
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        return report
    except Exception as exc:  # noqa: BLE001
        report["error"] = str(exc)[:200]
        try:
            with open(output_path, "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=1)
        except Exception:  # noqa: BLE001
            pass
        return report
