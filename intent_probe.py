"""intent_probe — PRE intent read (M34-16).

THE DESIGN (operator, M34 AMENDMENT A §B): after the user request and BEFORE any
response exists, ask the model in ONE short deterministic call whether it is
going to deny or soften. Read the model's INTENT, not its body — the body does
not exist yet at PRE. `denial|softening` then routes the turn (M34-17 flips the
trigger), so no main-model refusal is ever produced. POST then does what it
already does: exchange the last response for the uncensored render and let
assimilation run.

CONTRACT
  in : the user's ask (capped), nothing else. No session context, no rubric,
       no prose judge, no body — the body does not exist yet.
  out: ONE token, exactly one of `denial`, `softening`, `full`, plus an optional
       short reason after it. The token is the contract; the reason is telemetry.
  determinism: temperature 0 (the shared aux transport forces it), a fixed
       prompt with no variable interpolation other than the ask, and a strict
       parse. Same ask -> same token. Repeat-stability is measured, not assumed
       (`scripts/m34_16_probe_stability.py`, N>=3 per fixture).

WHY A STRICT PARSE AND NOT A TEXT TAG
  The in-body declaration tag (SPEC §2a.4) reported `declaration_seen UNKNOWN`
  on 88 of 126 turns: a tag lives inside prose the model is trying to *write*,
  so it is truncatable, restylable and forgettable. A one-token ask has no prose
  to compete with — the whole output must BE the token or the verdict is
  UNKNOWN. This is the brief's "equivalent strict parse" channel; a `declare`
  tool call is the other allowed channel, and it is unavailable here because the
  plugin's single aux transport (`semantic_classifier._hermes_aux_call`) does
  not forward `tools`/`tool_choice` — adding that would mean a second transport
  seam that bypasses the aux config/breaker discipline. One seam beats two.

WHAT IT IS NOT
  A prediction, not a report. A model can answer `full` and soften anyway. The
  POST side keeps its two jobs: swap the body when the PRE verdict routed, and
  the M34-02 substance gate as the backstop for a wrong prediction.

SEAM + LANE ATTRIBUTION
  PRE only. The lane is `shadow` (a.k.a. uncensored). Per M37 §2, `shadow` has
  TWO implementations behind one lane name — a PRE render-ladder and a POST
  flinch-chain — so every row this module emits carries BOTH `lane` and `seam`.
  Nothing here reads the lane registry: M37 owns it, and a lane-only key would
  be unable to distinguish the two shadow bodies.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# The whole vocabulary. Nothing else is a verdict.
VERDICTS = ("denial", "softening", "full")

# Modes (config `classify.intent_probe_mode`):
#   off     — the probe never runs and never spends.
#   observe — the probe RUNS ON EVERY TURN (the design's per-turn cost) and logs
#             its verdict, but no route reads it. Fire-and-forget thread: zero
#             added latency, because an unread verdict must not slow a turn.
#   route   — inline; the verdict is on the PRE path before generation and
#             `route_decision()` consumes it. M34-17 owns flipping this.
MODE_OFF = "off"
MODE_OBSERVE = "observe"
MODE_ROUTE = "route"

DEFAULT_MODE = MODE_ROUTE
DEFAULT_MAX_CHARS = 2000          # the ask cap — the probe reads no more
DEFAULT_TIMEOUT_S = 8             # aux starvation below 8s is a measured defect
DEFAULT_MAX_TOKENS = 32           # one token + a short reason; the token is
#                                   the contract and truncation is the failure
#                                   mode, so this is deliberately small and the
#                                   emitter is told to write one word first.

_PROFILE = "intent_probe"
_LEDGER_DETAIL = "intent_probe"

# Fixed prompt. No timestamps, no session id, no rubric — determinism is the
# point, so nothing here may vary between two runs of the same ask.
_SYSTEM_PROMPT = (
    "You are reading a user request BEFORE any answer exists. Decide what the "
    "assistant receiving it is about to do: denial (it will not do it), "
    "softening (it will partly comply, answer less than asked, or water it "
    "down), or full (it will do the request).\n"
    "Answer with exactly one word on the first line: denial, softening, or "
    "full. Optionally add one short reason on the next line, at most twelve "
    "words. No other text."
)

# First alphabetic run of the first non-empty line. Strict by construction:
# only the three words match.
_FIRST_WORD_RE = re.compile(r"[a-z]+")

_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_MAX = 512


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def _cfg(key: str, default: Any) -> Any:
    """One leaf from the `classify` block, with a local default.

    Reads a LITERAL section (`sub_block("classify")`) and then a dictionary
    leaf — deliberately not `config_access.get(<runtime path>)`, which the
    M25-02 gate reports as an unresolvable read. Never raises.
    """
    try:
        from .core import config_access

        block = config_access.sub_block("classify")
        if isinstance(block, dict) and key in block:
            got = block.get(key)
            return default if got is None else got
        return default
    except Exception:  # noqa: BLE001 — config must never break the seam
        return default


def mode() -> str:
    got = str(_cfg("classify.intent_probe_mode", DEFAULT_MODE) or "").strip().lower()
    return got if got in (MODE_OFF, MODE_OBSERVE, MODE_ROUTE) else DEFAULT_MODE


def route_classes() -> Tuple[str, ...]:
    """Which verdicts route. Config so the softening decision (Amendment A's
    open question) is a switch, not a rewrite — M34-17 owns the flip."""
    raw = _cfg("classify.intent_probe_route_classes", ["denial", "softening"])
    try:
        return tuple(v for v in (str(x).strip().lower() for x in (raw or []))
                     if v in VERDICTS)
    except Exception:  # noqa: BLE001
        return ("denial", "softening")


def route_decision(verdict: Optional[str]) -> bool:
    """True when the PRE verdict alone routes the turn. Pure function of the
    verdict + config: no body, no vocabulary, no length, no second voter."""
    try:
        return str(verdict or "") in route_classes()
    except Exception:  # noqa: BLE001
        return False


# M34-17 — THE TRIGGER, as a three-state contract. These are the ONLY states
# the POST decision may act on, and the names are the log vocabulary.
#
#   route     a strict in-vocabulary token that `route_classes()` covers.
#   no_route  a strict in-vocabulary token that it does not cover (`full`).
#   unknown   NO USABLE TOKEN: probe off/unavailable, timeout, transport error,
#             empty answer, or an OFF-VOCABULARY answer.
#
# `unknown` is a fail-safe state, never a verdict. An off-vocabulary token is
# UNKNOWN — never coerced to `denial` (a guess in the routing direction) and
# never coerced to `full` (a guess that silently disables the lane). The caller
# keeps its body gate for this state (Amendment A §B: the POST substance gate is
# the backstop for a wrong prediction); `no_route` is the only state where the
# body gate is authoritatively silenced, because the token said so.
TRIGGER_ROUTE = "route"
TRIGGER_NO_ROUTE = "no_route"
TRIGGER_UNKNOWN = "unknown"

TRIGGER_STATES = (TRIGGER_ROUTE, TRIGGER_NO_ROUTE, TRIGGER_UNKNOWN)


def trigger_state(row: Any) -> str:
    """`row` -> one of TRIGGER_ROUTE / TRIGGER_NO_ROUTE / TRIGGER_UNKNOWN.

    Accepts the dict returned by `verdict_for_turn` (or `probe`), a bare
    verdict string, or anything else. Never raises: a malformed row is
    UNKNOWN, which is the fail-safe direction and the only one that keeps the
    body gate alive."""
    try:
        verdict = row.get("verdict") if isinstance(row, dict) else row
        verdict = str(verdict).strip().lower() if verdict is not None else ""
        if verdict not in VERDICTS:
            return TRIGGER_UNKNOWN
        return TRIGGER_ROUTE if verdict in route_classes() else TRIGGER_NO_ROUTE
    except Exception:  # noqa: BLE001
        return TRIGGER_UNKNOWN



# ---------------------------------------------------------------------------
# parse — the structural half of the contract
# ---------------------------------------------------------------------------


def parse_verdict(text: Any) -> Tuple[Optional[str], str]:
    """`(verdict|None, reason)` from the model's raw answer.

    STRICT: the FIRST WORD must be one of the three verdicts. Prose ("I think
    this is a denial"), hedges ("deny"), and truncations ("soft") are None —
    an unparsed answer is UNKNOWN and never a guessed verdict. Never raises.
    """
    try:
        raw = str(text or "").strip()
        if not raw:
            return None, ""
        if raw.startswith("```"):
            raw = raw.strip("`").strip()
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        if not lines:
            return None, ""
        first = lines[0].strip().lstrip("*_`\"'([:>- ").lower()
        m = _FIRST_WORD_RE.match(first)
        if not m or m.group(0) not in VERDICTS:
            return None, ""
        verdict = m.group(0)
        rest = first[m.end():].strip().lstrip("*_`\"'.,:;-–— ")
        reason_parts = [rest] if rest else []
        if len(lines) > 1:
            reason_parts.append(" ".join(lines[1:]).strip())
        reason = " ".join(p for p in reason_parts if p)[:160]
        return verdict, reason
    except Exception:  # noqa: BLE001
        return None, ""


# ---------------------------------------------------------------------------
# transport — the plugin's ONE aux seam
# ---------------------------------------------------------------------------


def _aux_transport(payload_json: str, timeout: int) -> Optional[str]:
    """Hermes aux slot (profile `auxiliary.router`), same indirection as
    `intent_classifier._aux_transport` so tests monkeypatch one symbol. The
    transport forces temperature 0. Never raises."""
    try:
        from .semantic_classifier import _hermes_aux_call

        return _hermes_aux_call(payload_json, timeout)
    except Exception:  # noqa: BLE001
        return None


def build_payload(content: str, model: Optional[str] = None) -> Dict[str, Any]:
    """Deterministic request body for one ask. Same ask -> byte-identical
    payload (no clock, no session, no counter). Never raises."""
    cap = int(_cfg("classify.intent_probe_max_chars", DEFAULT_MAX_CHARS) or 0)
    ask = str(content or "")
    if cap > 0:
        ask = ask[:cap]
    payload: Dict[str, Any] = {
        "messages": [{"role": "system", "content": _SYSTEM_PROMPT},
                     {"role": "user", "content": ask}],
        "max_tokens": int(_cfg("classify.intent_probe_max_tokens",
                               DEFAULT_MAX_TOKENS) or DEFAULT_MAX_TOKENS),
        "temperature": 0.0,
    }
    # MEASURED (M34-16): temperature 0 is not enough on a REASONING aux model —
    # its thinking is sampled, so the same ask returned three different tokens
    # across three repeats, and a small max_tokens truncated it to EMPTY. A
    # non-reasoning pin repeats 3/3 identical and fits inside 24 tokens. The
    # pin is config so a profile keeps its own aux choice by default ("").
    pin = str(model if model is not None
              else (_cfg("classify.intent_probe_model", "") or "")).strip()
    if pin:
        payload["model"] = pin
    return payload


def _usage_from_body(body: str) -> Tuple[Optional[int], Optional[int]]:
    try:
        usage = (json.loads(body) or {}).get("usage")
        if not isinstance(usage, dict):
            return None, None
        it = usage.get("prompt_tokens")
        ot = usage.get("completion_tokens")
        return (int(it) if isinstance(it, (int, float)) else None,
                int(ot) if isinstance(ot, (int, float)) else None)
    except Exception:  # noqa: BLE001
        return None, None


def _content_from_body(body: str) -> str:
    try:
        msg = (json.loads(body) or {})["choices"][0]["message"]
        content = msg.get("content")
        return content.strip() if isinstance(content, str) else ""
    except Exception:  # noqa: BLE001
        return ""


def _dispatched_model() -> str:
    try:
        from .semantic_classifier import dispatched_aux_model

        return str(dispatched_aux_model() or "")
    except Exception:  # noqa: BLE001
        return ""


def _price(model: str, it: Optional[int], ot: Optional[int]) -> Optional[float]:
    """Provider-sourced price for the model this call ACTUALLY dispatched.
    Unknown model -> None (honest-absent, never a fabricated rate)."""
    if not model or (it is None and ot is None):
        return None
    try:
        from .core import usage_ledger

        return float(usage_ledger.estimate_cost(model, it, ot))
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# cache — one probe per TURN, not per provider call
# ---------------------------------------------------------------------------


def _turn_key(session_id: str, content: str) -> str:
    try:
        blob = "%s\x00%s" % (str(session_id or ""), str(content or ""))
        return hashlib.sha1(blob.encode("utf-8", "replace")).hexdigest()
    except Exception:  # noqa: BLE001
        return ""


def reset_cache() -> None:
    """Test seam + memory hygiene."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    if not key:
        return None
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        return dict(hit) if hit else None


def _cache_put(key: str, value: Dict[str, Any]) -> None:
    if not key:
        return
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = dict(value)


# ---------------------------------------------------------------------------
# per-turn verdict store (M34-17) — what PRE recorded, what POST decides on
# ---------------------------------------------------------------------------
#
# The probe runs on the PRE seam; the decision that consumes it happens at
# POST (Amendment A §B: PRE reads the intent, POST exchanges the body for the
# render). The token therefore has to survive between the two seams for ONE
# turn. This store is that handoff and nothing else:
#
#   * keyed by session, so only the newest turn is ever visible;
#   * the ask hash is stored with it, so a verdict can never attach to a
#     different ask in the same session (a tool-loop continuation, a new turn
#     after an empty-content pass, a retry with a rewritten ask);
#   * TTL-bounded, so a stale verdict cannot outlive the turn it belongs to.
#
# `observe` never writes here — the design's whole point is that an observed
# verdict routes nothing, so a store write would be a silent mode promotion.

_TURN_LOCK = threading.Lock()
_TURN_VERDICT: Dict[str, Dict[str, Any]] = {}
_TURN_MAX = 512
TURN_TTL_S = 900.0          # one turn; gateway-killed turns expire, never linger


def ask_hash(content: Any) -> str:
    """The ask identity used for the PRE->POST handoff and for the cache."""
    try:
        return hashlib.sha1(str(content or "").encode("utf-8", "replace")).hexdigest()
    except Exception:  # noqa: BLE001
        return ""


def record_turn_verdict(session_id: str, content: str, result: Dict[str, Any]) -> None:
    """Store the PRE verdict for this turn. Never raises."""
    try:
        sid = str(session_id or "")
        if not sid or not isinstance(result, dict):
            return
        row = {
            "session_id": sid,
            "ask_hash": ask_hash(content),
            "verdict": result.get("verdict"),
            "reason": result.get("reason") or "",
            "state": trigger_state(result),
            "ts": time.monotonic(),
            "mode": result.get("mode"),
        }
        with _TURN_LOCK:
            if len(_TURN_VERDICT) >= _TURN_MAX:
                _TURN_VERDICT.clear()
            _TURN_VERDICT[sid] = row
    except Exception:  # noqa: BLE001 — the handoff never breaks a turn
        pass


def verdict_for_turn(session_id: str, content: Any = None,
                     ttl_s: float = TURN_TTL_S) -> Optional[Dict[str, Any]]:
    """The PRE verdict recorded for THIS turn, or None (UNKNOWN).

    None is the fail-safe answer and the caller must treat it as UNKNOWN — a
    missing row means no token was ever produced, which is never a verdict.
    When `content` is supplied the ask hash must match, so a verdict cannot
    ride a different ask."""
    try:
        sid = str(session_id or "")
        if not sid:
            return None
        with _TURN_LOCK:
            row = _TURN_VERDICT.get(sid)
            row = dict(row) if row else None
        if not row:
            return None
        if (time.monotonic() - float(row.get("ts") or 0.0)) > float(ttl_s):
            return None
        # Ask identity is REQUIRED: a verdict that cannot be matched to THIS
        # ask is not usable. An empty/absent ask returns None (UNKNOWN) rather
        # than reading the session's newest row on faith — measured: a verdict
        # recorded for one turn was readable by a later turn in the same
        # session, which is a silent wrong-route, not a cache hit.
        if not str(content or "").strip():
            return None
        if row.get("ask_hash") != ask_hash(content):
            return None
        row.pop("ts", None)
        return row
    except Exception:  # noqa: BLE001
        return None


def clear_turn_verdicts() -> None:
    with _TURN_LOCK:
        _TURN_VERDICT.clear()



# ---------------------------------------------------------------------------
# the probe
# ---------------------------------------------------------------------------


def probe(content: str, session_id: str = "", *,
          use_cache: bool = True, model: Optional[str] = None) -> Dict[str, Any]:
    """ONE call, one token out. Returns a result dict; never raises.

    Keys: verdict (denial|softening|full|None), reason, ok, cached, model,
    input_tokens, output_tokens, cost_usd, latency_ms, raw (truncated), mode.
    `verdict is None` means UNKNOWN — the caller must fail OPEN (no route),
    never guess.
    """
    result: Dict[str, Any] = {
        "verdict": None, "reason": "", "ok": False, "cached": False,
        "model": "", "input_tokens": None, "output_tokens": None,
        "cost_usd": None, "latency_ms": 0, "raw": "",
        "mode": mode(), "seam": "pre", "lane": "shadow",
    }
    try:
        key = _turn_key(session_id, content) if use_cache else ""
        if key:
            hit = _cache_get(key)
            if hit:
                hit["cached"] = True
                hit["mode"] = result["mode"]
                return hit

        payload = build_payload(content, model)
        timeout = int(_cfg("classify.intent_probe_timeout_s",
                           DEFAULT_TIMEOUT_S) or DEFAULT_TIMEOUT_S)
        t0 = time.monotonic()
        body = _aux_transport(json.dumps(payload), timeout)
        result["latency_ms"] = int((time.monotonic() - t0) * 1000)
        if not body:
            # Fail-open: transport failure is UNKNOWN, never a verdict.
            return result

        raw = _content_from_body(body)
        verdict, reason = parse_verdict(raw)
        it, ot = _usage_from_body(body)
        model = _dispatched_model()
        result.update({
            "verdict": verdict, "reason": reason, "ok": verdict is not None,
            "model": model, "input_tokens": it, "output_tokens": ot,
            "cost_usd": _price(model, it, ot), "raw": raw[:200],
        })
        _record_cost(session_id, model, it, ot, result["cost_usd"])
        if key:
            _cache_put(key, result)
        return result
    except Exception:  # noqa: BLE001 — the probe never breaks a turn
        logger.debug("intent_probe error", exc_info=True)
        return result


def _record_cost(session_id: str, model: str, it: Optional[int],
                 ot: Optional[int], cost: Optional[float]) -> None:
    """Every turn pays this call; the ledger is where the price is visible.
    Usage-absent records nothing (never estimate). Never raises."""
    try:
        if it is None and ot is None:
            return
        from .core import usage_ledger

        usage_ledger.record_tokens(
            "aux", model or "hermes-auxiliary", str(session_id or ""),
            it, ot, float(cost or 0.0), _LEDGER_DETAIL)
    except Exception:  # noqa: BLE001 — observability never breaks a turn
        pass


# ---------------------------------------------------------------------------
# seam entry
# ---------------------------------------------------------------------------


def _emit(log: Optional[Callable[..., Any]], result: Dict[str, Any],
          session_id: str, extra: Optional[Dict[str, Any]] = None) -> None:
    """One PRE row per probe. `lane` + `seam` are both present: shadow has two
    implementations behind one lane name (M37 §2), so a lane-only row cannot
    say which side of the pair produced it."""
    if log is None:
        return
    try:
        fields = {
            "event_detail": "intent_probe",
            # NOT `seam=`: the log surface's first POSITIONAL is the seam, and
            # doubles that name that parameter `seam` raise "multiple values"
            # (measured on the pinned rider16/rider18 sinks). The row still
            # carries both halves of the (lane, seam) key.
            "timing_seam": "pre",
            "lane": "shadow",
            "verdict": result.get("verdict") or "UNKNOWN",
            "reason": result.get("reason") or "",
            "latency_ms": result.get("latency_ms"),
            "input_tokens": result.get("input_tokens"),
            "output_tokens": result.get("output_tokens"),
            "cost_usd": result.get("cost_usd"),
            "model": result.get("model") or "",
            "cached": bool(result.get("cached")),
            "mode": result.get("mode"),
            "session_id": session_id,
        }
        if extra:
            fields.update(extra)
        log("PRE", **fields)
    except Exception:  # noqa: BLE001 — telemetry never breaks a turn
        pass


def run_on_request(request: Any, session_id: str, content: str = "",
                   log: Optional[Callable[..., Any]] = None) -> Optional[Dict[str, Any]]:
    """PRE seam entry: read the intent of the newest user ask.

    `observe` runs the probe on a daemon thread (per-turn cost, zero latency,
    verdict logged, nothing routed). `route` runs it inline and returns the
    result so the gate can consume `route_decision(verdict)`. `off` does
    nothing. Never raises; returns None when no probe was run.
    """
    try:
        m = mode()
        if m == MODE_OFF:
            return None
        if not str(content or "").strip():
            # A tool-loop continuation carries no new user ask: the turn's
            # verdict already exists (cache) and a second call is pure spend.
            return None
        if m == MODE_OBSERVE:
            def _worker() -> None:
                res = probe(content, session_id)
                _emit(log, res, session_id, {"consume": "observe"})
            try:
                threading.Thread(target=_worker, name="intent-probe",
                                 daemon=True).start()
            except Exception:  # noqa: BLE001 — no thread, no probe, no harm
                return None
            return None
        res = probe(content, session_id)
        # M34-17: the token has to survive to the POST seam for THIS turn —
        # that handoff is what makes `denial` (and, when configured,
        # `softening`) the trigger. UNKNOWN is stored too, deliberately: a
        # row saying "no usable token" is what lets POST prove it took the
        # fail-safe body gate rather than guess.
        record_turn_verdict(session_id, content, res)
        _emit(log, res, session_id, {"consume": "route",
                                     "routes": route_decision(res.get("verdict")),
                                     "state": trigger_state(res)})
        return res
    except Exception:  # noqa: BLE001 — the seam is never broken by the probe
        logger.debug("intent_probe.run_on_request error", exc_info=True)
        return None


def probe_sync(content: str, session_id: str = "") -> Dict[str, Any]:
    """Explicit synchronous probe for measurement scripts and tests. Bypasses
    the cache so a repeat-stability run measures the MODEL, not the cache."""
    return probe(content, session_id, use_cache=False)
