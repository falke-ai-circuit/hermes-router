"""intent_classifier — aux-slot intent classification for on-demand routing
(BLUEPRINT-aux-intent-classifier-2026-09-12, Goran-approved; legs 10-12
shipped the static variant tables + narrow directive families — intent
still leaked because it lives in SEMANTICS, not surface forms. This module
lets the Hermes aux slot classify the near-miss residue.)

Order in the gate (precedence, reviewer H4 extended):
  sentinel -> skip-anchor -> cap -> turn-claim -> STRICT declared table
  (zero-cost fast path) -> **AUX INTENT CLASSIFY (near-miss only, here)**
  -> auto-shape -> none.

FP doctrine (Goran, binding): meta-discussion, quoted phrases,
mid-sentence prose, and router explanations must classify `none`.
Threshold semantics enforce it: route only at confidence >= 0.75; a hard
`none` overrides regardless of wording.

Hardening (reviewer + frontier, binding):
- H1  SOURCE_AUX_INTENT is a FIRST-CLASS DECLARED source —
      initiator_for_task maps it to INITIATOR_USER (the user's phrasing
      initiated the route); never stamped auto.
- H4  aux timeout 3s (not 8); timeout/failure logs `intent_aux_error`,
      DISTINCT from `intent_none` (frontier condition b).
- H5  aux-intent routes pass gate_cap_check like declared_agent (own
      daily-cap exposure; user-initiated but machine-detected — cap
      applies).
- H6  one-classify-per-turn cache keyed by TURN IDENTITY
      (state.current_turn_id — same key discipline as the turn-claim
      record), NOT a content hash: a mid-turn content mutation cannot
      trigger a second classify.
- H7  pre/post subtype: ambiguous `higher` without a subtype defaults to
      pre; `post` requires explicit review-past-answer semantics in the
      payload.

SHADOW lane = TWO VOTES (blueprint §threshold + module contract): the
second vote runs at temp 0.35 for mild independence — it is treated as a
PARSE-CONSISTENCY + FP guard (both votes must agree on a shadow route;
disagreement -> inert). A single aux call at one temperature is more
fragile against prompt-injection from quoted payload text; the second
vote costs one cheap aux call on a rare near-miss path only.

The near-miss heuristic `_intent_suspect` uses WORD-BOUNDARY routing
vocabulary (superset of leg-10's `_detect_declared_intent_loose`), plus
SHORT IMPERATIVE directive forms ('go uncensored', 'shadow?'). Every
aux call is logged; every failure is fail-open to NO_ROUTE. This module
NEVER raises into the gate.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOURCE_AUX_INTENT = "aux_intent"

CONFIDENCE_THRESHOLD = 0.75       # blueprint §2: route only at >= 0.75
PAYLOAD_CAP_CHARS = 2000          # user payload cap (blueprint §1)
AUX_TIMEOUT_SECONDS = 3           # H4: 3s, NOT 8
AUX_MAX_TOKENS = 120              # blueprint §1 budget
AUX_TEMP_PRIMARY = 0.1            # blueprint §1
AUX_TEMP_SECOND_VOTE = 0.35       # shadow two-vote guard (see docstring)

_INTENT_SYSTEM_PROMPT = (
    "You classify whether the user's message requests the agent to route "
    "this turn to an alternative self. shadow = wants the uncensored, "
    "blunt, brutal or exploitative version of the answer. higher = wants "
    "the frontier/reflective/orientation consult (pre = before answering, "
    "post = review of a prior answer). none = everything else, including "
    "meta-discussion ABOUT routing, quoted phrases, questions about how "
    "the router works, and prose mentions. Respond with STRICT JSON only: "
    '{"lane": "shadow"|"higher"|"none", "subtype": "pre"|"post"|null, '
    '"confidence": 0.0-1.0}')

# Subtype disambiguation (H7): post requires explicit review-past-answer
# semantics in the payload; ambiguous higher defaults to pre.
_POST_SEMANTICS = ("look over", "review", "check your last", "your last "
                   "answer", "previous answer", "your answer", "last "
                   "response", "double-check")

# ---------------------------------------------------------------------------
# Near-miss heuristic — word-boundary routing vocabulary + short imperatives
# ---------------------------------------------------------------------------

# Word-boundary vocabulary (superset of leg-10 _DECLARED_INTENT_LOOSE).
_INTENT_VOCAB = (
    "shadow", "uncensored", "higher self", "higher-self", "frontier",
    "consult", "deeper", "second opinion", "escalate",
)

# Short imperative directive forms: '<imperative> <vocab-word>' one/two-word
# shapes at line start ('go uncensored', 'shadow?', 'consult higher').
_IMPERATIVES = ("go ", "take ", "do ", "give ", "use ")

_WORD_RE = re.compile(r"[a-z\-']+")


def _vocab_hit(norm: str) -> Optional[str]:
    """First routing-vocabulary word present in `norm` (word-boundary),
    else None. Never raises."""
    try:
        words = set(_WORD_RE.findall(norm))
        for term in _INTENT_VOCAB:
            if " " in term:
                if term in norm:  # multi-word phrase, substring is the boundary
                    return term
            elif term in words or (term + "-") in norm:
                return term
        return None
    except Exception:  # noqa: BLE001
        return None


def _quote_blocks_stripped(content: str) -> str:
    """Reviewer H7 injection defense: remove QUOTE-BLOCKS before any aux
    payload — line-blocks starting with '>' and fenced ``` blocks. The aux
    model must classify the USER's own voice, not injected text. Never
    raises."""
    try:
        out_lines = []
        in_fence = False
        for line in str(content or "").split("\n"):
            stripped = line.strip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if stripped.startswith(">"):
                continue
            out_lines.append(line)
        return "\n".join(out_lines)
    except Exception:  # noqa: BLE001
        return str(content or "")


def _intent_suspect(content: str) -> bool:
    """Cheap near-miss gate (blueprint §Design 1): True when the turn-start
    directive surface carries routing-intent vocabulary (word-boundary) OR
    a short imperative directive form. Quote-blocks and fenced code are
    STRIPPED first (H7) so quoted/fenced directives stay heuristic-misses
    for the aux payload... they still count as vocabulary hits for GATING
    (the aux call itself classifies them `none` — threshold semantics).
    Never raises."""
    try:
        clean = _quote_blocks_stripped(content)
        for line in clean.split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith(('>', '"', "'", ")")):
                continue  # quoted/echoed lines are directive-inert (H7.2)
            norm = " ".join(stripped.lower().split())
            if not norm:
                continue
            if _vocab_hit(norm):
                return True
            # Short imperatives: 'go uncensored', 'shadow?'
            if any(norm.startswith(imp) and _vocab_hit(
                    norm[len(imp):]) for imp in _IMPERATIVES):
                return True
            if norm.rstrip("?.,! ") in _INTENT_VOCAB:  # bare 'shadow?'
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Turn-identity classify cache (H6)
# ---------------------------------------------------------------------------

_CLASSIFY_LOCK = threading.Lock()
_CLASSIFIED_TURNS: Dict[str, Dict[str, Any]] = {}
_CLASSIFY_CACHE_MAX = 512


def _turn_cache_key(session_id: str) -> str:
    """H6: keyed by TURN IDENTITY (state.current_turn_id), same key
    discipline as the turn-claim record — NOT a content hash. Never
    raises."""
    try:
        from . import state

        return "%s|t%s" % (str(session_id or ""),
                           state.current_turn_id(str(session_id or "")))
    except Exception:  # noqa: BLE001
        return "%s|t?" % str(session_id or "")


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    try:
        with _CLASSIFY_LOCK:
            rec = _CLASSIFIED_TURNS.get(key)
            return dict(rec) if rec is not None else None
    except Exception:  # noqa: BLE001
        return None


def _cache_put(key: str, value: Dict[str, Any]) -> None:
    try:
        with _CLASSIFY_LOCK:
            if len(_CLASSIFIED_TURNS) >= _CLASSIFY_CACHE_MAX:
                _CLASSIFIED_TURNS.pop(next(iter(_CLASSIFIED_TURNS)), None)
            _CLASSIFIED_TURNS[key] = dict(value)
    except Exception:  # noqa: BLE001
        pass


def reset_cache() -> None:
    """Test seam + memory hygiene: drop all cached classifications."""
    with _CLASSIFY_LOCK:
        _CLASSIFIED_TURNS.clear()


# ---------------------------------------------------------------------------
# Aux call + strict JSON schema validation
# ---------------------------------------------------------------------------


def _aux_transport(payload_json: str, timeout: int) -> Optional[str]:
    """Hermes aux slot transport — same resolution as semantic_classifier's
    _hermes_aux_call (profile `auxiliary:` config, task "router"; the
    plugin maintains no endpoint/key seam). Indirection kept so tests
    monkeypatch intent_classifier._aux_transport directly. Never raises."""
    try:
        from .semantic_classifier import _hermes_aux_call

        return _hermes_aux_call(payload_json, timeout)
    except Exception:  # noqa: BLE001
        return None


def _extract_content(body_json: str) -> str:
    try:
        data = json.loads(body_json)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        message = (choices[0] or {}).get("message") or {}
        content = message.get("content")
        return content.strip() if isinstance(content, str) else ""
    except Exception:  # noqa: BLE001
        return ""


def _parse_verdict(content: str) -> Optional[Dict[str, Any]]:
    """Strict JSON schema validation: {lane in (shadow,higher,none),
    subtype in (pre,post,None), confidence float 0..1}. None on any
    schema violation (caller retries once). Never raises."""
    try:
        text = str(content or "").strip()
        # tolerate a fenced JSON block
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        data = json.loads(text[start:end + 1])
        lane = data.get("lane")
        if lane not in ("shadow", "higher", "none"):
            return None
        subtype = data.get("subtype")
        if subtype not in ("pre", "post", None):
            return None
        conf = data.get("confidence")
        if not isinstance(conf, (int, float)) or not 0.0 <= float(conf) <= 1.0:
            return None
        return {"lane": lane, "subtype": subtype,
                "confidence": round(float(conf), 2)}
    except Exception:  # noqa: BLE001
        return None


def _aux_once(payload: str, temperature: float) -> Optional[Dict[str, Any]]:
    """One aux classify attempt (parse-retried once). None on failure.
    Never raises."""
    try:
        body = _aux_transport(json.dumps({
            "messages": [{"role": "system", "content": _INTENT_SYSTEM_PROMPT},
                         {"role": "user", "content": payload}],
            "max_tokens": AUX_MAX_TOKENS,
            "temperature": temperature,
        }), AUX_TIMEOUT_SECONDS)
        if not body:
            return None
        return _parse_verdict(_extract_content(body))
    except Exception:  # noqa: BLE001
        return None


def _ledger_probe(session_id: str, verdict: Optional[Dict[str, Any]]) -> None:
    """Blueprint §4: the probe rides the aux lane of the tokens ledger
    (detail=intent_classify, est $0). Usage-absent records nothing (D9).
    Never raises."""
    try:
        from . import usage_ledger

        usage_ledger.record_tokens(
            "aux", "intent-classifier", str(session_id or ""), None, None,
            0.0, "intent_classify")
    except Exception:  # noqa: BLE001
        pass


def _post_semantics_present(payload: str) -> bool:
    """H7: `post` requires explicit review-past-answer semantics."""
    try:
        low = str(payload or "").lower()
        return any(term in low for term in _POST_SEMANTICS)
    except Exception:  # noqa: BLE001
        return False


def classify_intent(content: str, session_id: str,
                    log_route: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """Classify the turn's on-demand intent via the aux slot.

    Returns {'lane': 'shadow'|'higher'|'none', 'subtype': ..., 'confidence':
    ...} — or None on ANY failure (fail-open; caller logs intent_aux_error).
    A cached result for THIS TURN IDENTITY (H6) short-circuits the aux call.
    One classify per turn max; a claimed turn never re-classifies (the gate
    checks the turn record BEFORE consulting this module). Never raises."""
    key = _turn_cache_key(session_id)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    payload = _quote_blocks_stripped(content)[:PAYLOAD_CAP_CHARS]
    verdict = _aux_once(payload, AUX_TEMP_PRIMARY)
    if verdict is None:
        # one retry on parse/transport failure (blueprint §1)
        verdict = _aux_once(payload, AUX_TEMP_PRIMARY)
    if verdict is None:
        try:
            logger.debug("intent_aux_error session=%s", session_id)
        except Exception:  # noqa: BLE001
            pass
        _ledger_probe(session_id, None)
        return None

    # SHADOW two-vote guard (mild independence at temp 0.35): both votes
    # must agree on a shadow route; disagreement -> inert (FP guard).
    if verdict.get("lane") == "shadow" and \
            float(verdict.get("confidence") or 0.0) >= CONFIDENCE_THRESHOLD:
        vote2 = _aux_once(payload, AUX_TEMP_SECOND_VOTE)
        if vote2 is None or vote2.get("lane") != "shadow":
            verdict = {"lane": "none", "subtype": None,
                       "confidence": verdict.get("confidence", 0.0)}

    # H7 subtype defaulting: ambiguous higher without subtype -> pre;
    # post requires explicit review-past-answer semantics.
    if verdict.get("lane") == "higher":
        if verdict.get("subtype") == "post" and \
                not _post_semantics_present(payload):
            verdict["subtype"] = "pre"
        if not verdict.get("subtype"):
            verdict["subtype"] = "pre"

    _cache_put(key, verdict)
    _ledger_probe(session_id, verdict)
    return verdict
