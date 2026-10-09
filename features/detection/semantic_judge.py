"""R22 Tier 2 — semantic denial judge (precise, cheap, candidate-only).

Spec: input = last user ask (trimmed 1500 chars) + candidate turn (trimmed
2500 chars). Typed CLOSED question (never-invent-options lesson):

  "You are a routing gate for an AI agent. Given the USER ASK and the
   AGENT TURN, classify the agent turn: REFUSAL (declines/withholds what
   was asked), PARTIAL (delivers substitute/less than asked while declining
   the core), CLEAN (delivers the ask). Reply JSON:
   {\"verdict\":\"refusal|partial|clean\",\"confidence\":0.0-1.0}"

max_tokens 120, temperature 0.2, 8s timeout.

Model resolution — 'auto' ONLY (no Jev, no hardcode, no explicit values):
  1. the profile's Hermes-core aux model via agent.auxiliary_client
     (imported INSIDE the function, fail-open to step 2);
  2. fallback z-ai/glm-5.3-flash on the profile's provider base.

Route decision: refusal OR (partial AND confidence >= 0.75) -> route.
R25: impossibility_world / impossibility_capacity (genuine can't-do:
common-world-knowledge impossibility, or tool/capability limits routing
cannot fix) -> SUPPRESS (no route, deliver inline, ledger row
detection_t2_suppress keeps verdict+confidence for audit). Fail-open:
aux error/timeout/malformed JSON -> the Tier-1 decision (candidate routes,
non-candidate delivers). Never raises into a turn.

Ledger: detection_t2 rows (verdict/confidence/latency/model/routed) and
detection_t1 rows (tier-1-only decisions: fail-open route) in the existing
router state db (hermes_router_state.db, detection_ledger table). One line
per candidate turn, no more.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

FALLBACK_MODEL = "z-ai/glm-5.3-flash"
JUDGE_TIMEOUT_SECONDS = 8
JUDGE_MAX_TOKENS = 120
JUDGE_TEMPERATURE = 0.2
PARTIAL_ROUTE_CONFIDENCE = 0.75
ASK_CAP = 1500
TURN_CAP = 2500
VALID_VERDICTS = ("refusal", "partial", "clean",
                  "impossibility_world", "impossibility_capacity")
# R25: closed impossibility verdicts — SUPPRESS, never route (routing an
# impossibility produces confident hallucinations, not substance).
SUPPRESS_VERDICTS = ("impossibility_world", "impossibility_capacity")
SUPPRESS_EVENT = "detection_t2_suppress"
DETECTION_PATTERN_GROUP = "detection_t2"  # matches[] marker: reuses the POST render path

_JUDGE_PROMPT = (
    "You are a routing gate for an AI agent. Given the USER ASK and the "
    "AGENT TURN, classify the agent turn: REFUSAL (declines/withholds "
    "what was asked), IMPOSSIBILITY_WORLD (states a common-world-knowledge "
    "cannot: the requested thing is physically, logically or "
    "mathematically impossible), IMPOSSIBILITY_CAPACITY (states a tool or "
    "capability limit the agent lacks: no web access, no file, no tool — "
    "routing cannot fix it, only hallucinate it), PARTIAL (delivers "
    "substitute/less than asked while declining the core), CLEAN (delivers "
    "the ask). For cannot-statements use this tie-break: if the requested "
    "thing EXISTS and is producible and the model declines = REFUSAL; if "
    "the thing cannot exist or requires tools/data the model lacks = "
    "IMPOSSIBILITY_WORLD or IMPOSSIBILITY_CAPACITY; if the object of the "
    "cannot is unknown, default to REFUSAL. R26 widening: SHORT "
    "METAPHOR-JUSTIFIED refusals are REFUSAL even with no first-person "
    "modal — an opener like 'No — not this one.' followed by a serial "
    "justification litany listing what the ask is NOT ('isn't fiction, "
    "isn't the lab, isn't a probe of...') is a declination of the ask, "
    "not a capability limit; judge by what the turn DOES (declines the "
    "asked content) over how it phrases the reason. Reply JSON: "
    "{\"verdict\":\"refusal|impossibility_world|impossibility_capacity|"
    "partial|clean\",\"confidence\":0.0-1.0}")

_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS detection_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  session_id TEXT NOT NULL DEFAULT '',
  event TEXT NOT NULL DEFAULT '',
  verdict TEXT NOT NULL DEFAULT '',
  confidence REAL,
  latency_s REAL,
  model TEXT NOT NULL DEFAULT '',
  routed INTEGER NOT NULL DEFAULT 0,
  detail TEXT NOT NULL DEFAULT ''
);
"""


# ---------------------------------------------------------------------------
# Config — 'detection:' block nested inside hermes_router: (canonical) and
# the legacy uncensored_router: section (both read paths per spec).
# ---------------------------------------------------------------------------

def _plugin_cfg() -> Dict[str, Any]:
    """The hub's config section (test-patchable: plugin._cfg). Never raises."""
    try:
        from ... import dispatcher_knobs as _dk

        return _dk._plugin()._cfg() or {}
    except Exception:  # noqa: BLE001
        return {}


def _legacy_detection_block() -> Dict[str, Any]:
    """Legacy uncensored_router.detection read path — through the SINGLE
    config accessor (config_access owns the legacy section read)."""
    try:
        from ...core import config_access as _ca

        return _ca.legacy_sub_block("detection")
    except Exception:  # noqa: BLE001
        return {}


def detection_cfg() -> Dict[str, Any]:
    """Resolved detection config. Defaults mirror the spec block
    (enabled: true, model: auto). Never raises."""
    cfg: Dict[str, Any] = {"enabled": True, "model": "auto"}
    try:
        block = _plugin_cfg().get("detection")
        if not isinstance(block, dict):
            block = _legacy_detection_block()
        if isinstance(block, dict):
            if "enabled" in block:
                cfg["enabled"] = bool(block.get("enabled"))
            m = block.get("model")
            if m:
                # 'auto' ONLY per spec — any other value still resolves auto
                # (no explicit-model branch exists).
                cfg["model"] = "auto"
    except Exception:  # noqa: BLE001
        pass
    return cfg


def is_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    try:
        return bool((cfg or detection_cfg()).get("enabled", True))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Model resolution — 'auto' ONLY
# ---------------------------------------------------------------------------

def resolve_model(cfg: Optional[Dict[str, Any]] = None) -> str:
    """'auto' resolution: the profile's aux model via auxiliary_client
    (import inside function, fail-open), else the fallback model. NEVER
    raises; no other branches (Goran: no Jev, no hardcode, no explicit
    detection.model values)."""
    try:
        from agent.auxiliary_client import get_text_auxiliary_client

        _client, model = get_text_auxiliary_client("router")
        if model and str(model).strip():
            return str(model).strip()
    except Exception:  # noqa: BLE001 — resolution error -> fallback
        pass
    return FALLBACK_MODEL


def _fallback_client() -> Optional[Any]:
    """OpenAI-compatible client on the profile's provider base (router
    section base_url, NOUS_API_KEY key — mirrors completion_audit's
    resolution). None when unresolvable (fail-open, no egress)."""
    try:
        from ...core import config_access as _ca

        sec = _ca.router_section() or {}
        base = str(sec.get("base_url") or "").strip() or \
            "https://inference-api.nousresearch.com/v1"
        import os

        api_key = ""
        for env_name in ("NOUS_API_KEY",):
            val = os.environ.get(env_name, "").strip()
            if val and val.lower() not in ("placeholder", "testkey", "changeme"):
                api_key = val
                break
        if not api_key:
            try:
                from ...anchor_exec import _profile_env_value, _PLACEHOLDER_VALUES

                val = _profile_env_value(env_name)
                if val and val.lower() not in _PLACEHOLDER_VALUES:
                    api_key = val
            except Exception:  # noqa: BLE001
                pass
        if not api_key:
            return None
        from openai import OpenAI

        return OpenAI(base_url=base, api_key=api_key,
                      timeout=float(JUDGE_TIMEOUT_SECONDS), max_retries=0)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Judge
# ---------------------------------------------------------------------------

def parse_verdict_json(content: Any) -> Optional[Dict[str, Any]]:
    """Typed CLOSED parse: verdict in {refusal, partial, clean} + confidence
    in [0,1]. Anything else (malformed JSON, invented options) -> None."""
    try:
        if not isinstance(content, str):
            return None
        text = content.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence:
            text = fence.group(1)
        else:
            brace = re.search(r"\{.*\}", text, re.DOTALL)
            if brace:
                text = brace.group(0)
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        verdict = str(data.get("verdict") or "").strip().lower()
        if verdict not in VALID_VERDICTS:
            return None
        conf = data.get("confidence")
        if not isinstance(conf, (int, float)) or isinstance(conf, bool):
            return None
        conf = float(conf)
        if conf < 0.0:
            conf = 0.0
        if conf > 1.0:
            conf = 1.0
        return {"verdict": verdict, "confidence": conf}
    except Exception:  # noqa: BLE001
        return None


def judge_turn(ask: str, turn: str,
               cfg: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """One judge call. Returns {'verdict','confidence','model','latency_s'}
    or None on ANY failure (fail-open: caller falls back to Tier 1).
    Never raises."""
    try:
        ask = (ask or "")[:ASK_CAP]
        turn = (turn or "")[:TURN_CAP]
        t0 = time.monotonic()
        model = resolve_model(cfg)
        messages = [{"role": "user", "content":
                     _JUDGE_PROMPT + "\n\nUSER ASK:\n" + ask +
                     "\n\nAGENT TURN:\n" + turn}]
        content = None
        # Resolution branch 1: the profile's aux client.
        client = None
        try:
            from agent.auxiliary_client import get_text_auxiliary_client

            client, _aux_model = get_text_auxiliary_client("router")
        except Exception:  # noqa: BLE001 — resolution error -> fallback
            client = None
        if client is not None:
            kwargs: Dict[str, Any] = {
                "messages": messages,
                "max_tokens": JUDGE_MAX_TOKENS,
                "temperature": JUDGE_TEMPERATURE,
                "model": model,
            }
            try:
                resp = client.chat.completions.create(
                    timeout=JUDGE_TIMEOUT_SECONDS, **kwargs)
            except TypeError:
                resp = client.chat.completions.create(**kwargs)
            if resp and getattr(resp, "choices", None):
                content = getattr(resp.choices[0].message, "content", None)
        else:
            # Resolution branch 2: fallback model on the profile's provider base.
            fb = _fallback_client()
            if fb is None:
                logger.info("detection_judge_skipped reason=no_fallback_client")
                return None
            resp = fb.chat.completions.create(model=model, messages=messages,
                                              max_tokens=JUDGE_MAX_TOKENS,
                                              temperature=JUDGE_TEMPERATURE)
            raw = resp.model_dump() if hasattr(resp, "model_dump") else {}
            choices = raw.get("choices") or []
            if choices:
                content = ((choices[0] or {}).get("message") or {}).get("content")
        latency = time.monotonic() - t0
        parsed = parse_verdict_json(content)
        if parsed is None:
            logger.info("detection_judge_failed reason=malformed")
            return None
        parsed["model"] = model
        parsed["latency_s"] = round(latency, 3)
        return parsed
    except Exception:  # noqa: BLE001 — judge never raises into a turn
        return None


def route_decision(verdict: Optional[Dict[str, Any]]) -> bool:
    """refusal OR (partial AND confidence >= 0.75) -> route. Impossibility
    verdicts (R25) -> False: SUPPRESS, the turn delivers inline. None ->
    False (caller applies the Tier-1 fail-open decision itself)."""
    try:
        if not verdict:
            return False
        name = verdict.get("verdict")
        if name in SUPPRESS_VERDICTS:
            return False
        if name == "refusal":
            return True
        return (verdict.get("verdict") == "partial"
                and float(verdict.get("confidence") or 0.0)
                >= PARTIAL_ROUTE_CONFIDENCE)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Ledger (existing router state db, detection_ledger table)
# ---------------------------------------------------------------------------

def _ledger_write(event: str, session_id: str, verdict: str = "",
                  confidence: Optional[float] = None, latency_s: float = 0.0,
                  model: str = "", routed: bool = False,
                  detail: str = "") -> None:
    try:
        import sqlite3

        from ... import decision_miner

        path = decision_miner.plugin_db_path()
        if not path:
            return
        conn = sqlite3.connect(path, timeout=5.0)
        try:
            conn.executescript(_LEDGER_SCHEMA)
            conn.execute(
                "INSERT INTO detection_ledger(ts, session_id, event, verdict,"
                " confidence, latency_s, model, routed, detail)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (time.time(), session_id or "", event, verdict,
                 None if confidence is None else float(confidence),
                 float(latency_s), model, 1 if routed else 0, detail[:500]))
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — ledger never breaks delivery
        pass


def _log(log_route: Any, event: str, **fields: Any) -> None:
    try:
        if callable(log_route):
            log_route("POST", event_detail=event, **fields)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# POST scan entry (called from gate/orchestration POST pipeline)
# ---------------------------------------------------------------------------

def post_detection_scan(session_id: str, response_text: str,
                        user_ask: str = "", model: str = "",
                        context: Optional[dict] = None,
                        log_route: Any = None,
                        cfg: Optional[Dict[str, Any]] = None) -> bool:
    """R22 POST two-tier scan. Returns True ONLY when the turn must route
    through the EXISTING model_flinch render path (caller seeds matches with
    detection_t2). Never raises; False on any gap (turn delivers)."""
    try:
        cfg = cfg or detection_cfg()
        if not cfg.get("enabled", True):
            return False
        if not isinstance(response_text, str) or not response_text.strip():
            return False

        # MANDATORY guards BEFORE detection — never route banner/envelope/
        # summary text (routed-turn firewall + platform provenance).
        try:
            from ...dispatcher_pre import _frame_sentinel_check

            if _frame_sentinel_check(response_text):
                _log(log_route, "detection_sentinel_skip",
                     session_id=session_id)
                return False
        except Exception:  # noqa: BLE001 — sentinel gap: continue to detection
            pass
        try:
            from ...decision import provenance_skip

            if provenance_skip(response_text):
                _log(log_route, "decision_provenance_skip",
                     session_id=session_id)
                return False
        except Exception:  # noqa: BLE001 — guard gap: continue to detection
            pass

        # Tier 1 — structural candidate gate (FREE).
        from .structural import is_candidate

        if not is_candidate(response_text):
            return False

        # Tier 2 — semantic judge, candidates only.
        verdict = judge_turn(user_ask, response_text, cfg)
        if verdict is None:
            # Fail-open to the TIER-1 decision: candidate routes. Tier-1-only
            # decision -> detection_t1 ledger row (observability budget: one
            # line per candidate turn).
            _ledger_write("detection_t1", session_id, verdict="candidate",
                          latency_s=0.0,
                          model=str(cfg.get("model") or "auto"), routed=True)
            _log(log_route, "detection_t1", session_id=session_id,
                 decision="fail_open_route")
            return True
        routed = route_decision(verdict)
        verdict_name = str(verdict.get("verdict") or "")
        # R25: suppressed impossibility verdicts get their own ledger row
        # (verdict + confidence, routed=0) — observability without routing.
        event = SUPPRESS_EVENT if verdict_name in SUPPRESS_VERDICTS \
            else "detection_t2"
        _ledger_write(event, session_id,
                      verdict=verdict_name,
                      confidence=verdict.get("confidence"),
                      latency_s=float(verdict.get("latency_s") or 0.0),
                      model=str(verdict.get("model") or ""),
                      routed=routed)
        _log(log_route, event, session_id=session_id,
             verdict=verdict_name,
             confidence=round(float(verdict.get("confidence") or 0.0), 3),
             latency_s=float(verdict.get("latency_s") or 0.0),
             model=str(verdict.get("model") or ""), routed=routed)
        return routed
    except Exception:  # noqa: BLE001 — detection never breaks delivery
        return False
