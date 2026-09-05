"""Anchor execution for the hermes-router complexity lane (v3.0.0).

Performs the ANCHORED frontier call when the dispatcher stages a swap:

  1. llm_execution middleware fires with the swapped payload; if
     router_core.pending_model_swap(session_id) yields a record, this module
     builds a per-call OpenAI-compatible client (model + base_url + key from
     the anchor chain endpoint) and runs the WHOLE request against it.
  2. Cap guard runs BEFORE the call: today's spend + estimated cost vs the
     daily cap. At/over cap -> overflow (pass-through to flash) + cap_blocked.
  3. Spend is recorded after the call from the response's usage block when
     present, else from the estimate.

Fail-open: any transport/HTTP/cap/key failure returns None and the caller
passes the flash request through unchanged (route_skipped logged by caller).
Never raises. Zero endpoint knowledge lives outside config + this module's
scheme table.
"""
from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

from . import anchor_chain
from . import router_core

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Key resolution (key_file discipline not required here — anchor keys are env
# refs by design: OPENROUTER_API_KEY or the custom provider's key_env)
# ---------------------------------------------------------------------------


_PLACEHOLDER_VALUES = {"", "not-needed", "none", "null", "placeholder", "changeme", "your-key-here"}


def _profile_env_value(env: str) -> str:
    """Read `env` from {HERMES_HOME}/.env (profile dotenv). The gateway process env carries the
    GLOBAL /opt/data/.env values; profile .env files hold the real per-profile credentials.
    Live-caught 2026-09-05: os.environ held the global placeholder 'not-needed' while the profile
    .env had the real OpenRouter key — anchored calls 401'd despite the key existing on disk."""
    try:
        home = os.environ.get("HERMES_HOME") or ""
        if not home:
            return ""
        path = os.path.join(home, ".env")
        if not os.path.isfile(path):
            return ""
        prefix = f"{env}="
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(prefix) and not line.lstrip().startswith("#"):
                    return line[len(prefix):].strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001 — key resolution must never raise
        return ""
    return ""


def _resolve_key(endpoint: anchor_chain.AnchorEndpoint) -> str:
    try:
        env = (endpoint.api_key_env or "").strip()
        if env:
            val = os.environ.get(env, "").strip()
            if val and val.lower() not in _PLACEHOLDER_VALUES:
                return val
            # placeholder/missing in process env → read the profile dotenv before giving up
            pval = _profile_env_value(env)
            if pval and pval.lower() not in _PLACEHOLDER_VALUES:
                logger.info("anchor_key_resolved source=profile_dotenv key_env=%s", env)
                return pval
            if val:  # keep placeholder only if nothing better exists (caller logs the 401)
                return val
        return ""
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# Cost estimation from the outbound payload
# ---------------------------------------------------------------------------

_CHARS_PER_TOKEN = 4.0
_DEFAULT_EST_OUTPUT_TOKENS = 2000


def estimate_tokens_from_payload(api_kwargs: Dict[str, Any]) -> Tuple[int, int]:
    """Rough (input, output) token estimate from the provider payload:
    input = total message chars / 4; output = payload max_tokens or default.
    Never raises."""
    try:
        total_chars = 0
        messages = api_kwargs.get("messages")
        if isinstance(messages, list):
            for m in messages:
                if isinstance(m, dict):
                    c = m.get("content")
                    if isinstance(c, str):
                        total_chars += len(c)
                    elif isinstance(c, list):
                        for part in c:
                            if isinstance(part, dict) and isinstance(part.get("text"), str):
                                total_chars += len(part["text"])
        inp = int(total_chars / _CHARS_PER_TOKEN)
        mt = api_kwargs.get("max_tokens")
        out = int(mt) if isinstance(mt, (int, float)) and mt else _DEFAULT_EST_OUTPUT_TOKENS
        return max(0, inp), max(1, out)
    except Exception:  # noqa: BLE001
        return 0, _DEFAULT_EST_OUTPUT_TOKENS


def usage_cost_from_response(data: Dict[str, Any], pricing: Dict[str, Dict[str, float]],
                             model: str) -> Optional[float]:
    """Extract real cost from a response usage block + price table. Returns
    None when usage is absent. Never raises."""
    try:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return None
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        if not isinstance(pt, (int, float)) or not isinstance(ct, (int, float)):
            return None
        prices = pricing.get(model) if isinstance(pricing, dict) else None
        if not isinstance(prices, dict):
            prices = {"input_per_1m": 0.5, "output_per_1m": 1.5}
        inp = float(prices.get("input_per_1m", 0.5) or 0.0)
        out = float(prices.get("output_per_1m", 1.5) or 0.0)
        cost = (float(pt) / 1_000_000.0) * inp + (float(ct) / 1_000_000.0) * out
        return round(max(0.0, cost), 6)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# The anchored provider call (per-call client; nothing persists)
# ---------------------------------------------------------------------------


def anchored_call(endpoint: anchor_chain.AnchorEndpoint, api_kwargs: Dict[str, Any],
                  *, timeout: int = 300) -> Tuple[Optional[str], Optional[float]]:
    """Run the FULL provider payload against the anchor endpoint with a
    per-call client.

    Returns (content, cost_usd):
      content None -> failure (caller passes flash through; fail-open)
      cost_usd     -> recorded spend (None when usage absent)
    Never raises. Streaming kwargs are stripped — anchored calls are
    non-streaming single-shot.
    """
    try:
        import httpx
        from openai import OpenAI

        api_key = _resolve_key(endpoint)
        if not api_key:
            logger.error("anchor_route_failed reason=key_unavailable key_env=%s",
                         endpoint.api_key_env)
            return None, None

        payload = copy.deepcopy(api_kwargs)
        payload.pop("stream", None)
        payload.pop("stream_options", None)
        payload.pop("__bedrock_region__", None)
        payload.pop("__bedrock_converse__", None)
        payload.pop("_moa_prepared_request", None)
        payload.pop("timeout", None)
        payload["model"] = endpoint.model

        client = OpenAI(base_url=endpoint.base_url, api_key=api_key,
                        timeout=float(timeout), max_retries=0)
        try:
            resp = client.chat.completions.create(**payload)
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

        raw = resp.model_dump() if hasattr(resp, "model_dump") else {}
        content = ""
        try:
            choices = raw.get("choices") or []
            if choices:
                msg = (choices[0] or {}).get("message") or {}
                content = msg.get("content") or ""
        except Exception:  # noqa: BLE001
            content = ""
        if not str(content).strip():
            _diag = ""
            try:
                _ch = (raw.get("choices") or [{}])[0] or {}
                _m = _ch.get("message") or {}
                _diag = ("finish=%s tool_calls=%s reasoning_chars=%d payload_keys=%s "
                         "max_tokens=%s n_msgs=%d") % (
                    _ch.get("finish_reason"), bool(_m.get("tool_calls")),
                    len(str(_m.get("reasoning") or "")),
                    sorted(k for k in payload if k != "messages"),
                    payload.get("max_tokens"), len(payload.get("messages") or []))
            except Exception:  # noqa: BLE001
                _diag = "diag_unavailable"
            logger.error("anchor_route_failed reason=empty_response model=%s %s",
                         endpoint.model, _diag)
            return None, None

        pricing = anchor_chain.load_anchor_chain().pricing
        cost = usage_cost_from_response(raw, pricing, endpoint.model)
        return str(content), cost
    except Exception as exc:  # noqa: BLE001 — anchored lane must never raise
        logger.error("anchor_route_failed reason=exception detail=%.300s", str(exc))
        return None, None


def maybe_execute_anchored(session_id: str, api_kwargs: Dict[str, Any]
                           ) -> Optional[Tuple[str, Dict[str, Any]]]:
    """The llm_execution middleware entry point for the complexity lane.

    Consumes router_core.pending_model_swap(session_id); when a swap is staged:
      1. cap check (today's spend + estimate vs daily cap) — at/over cap:
         record nothing, return None (caller passes flash through; caller logs
         cap_blocked via anchor_chain state).
      2. anchored_call() with the swap's endpoint.
      3. record spend; stash a consult envelope for the agent-facing tool.
    Returns a REPLACEMENT payload dict {"request": {...}} ONLY when the
    anchored call fully succeeded and the caller should use the frontier
    result — actually returns an opaque result object the caller (__init__)
    interprets. None = no staged swap or failed anchored call (pass-through).

    Return contract with __init__.on_llm_execution:
      ("done", envelope_dict)  -> anchored answer ready as tool-result data
      None                     -> proceed with the flash call unchanged
    """
    try:
        rec = router_core.pending_model_swap(session_id)
        if not rec:
            return None
        endpoint: anchor_chain.AnchorEndpoint = rec["endpoint"]
        chain = anchor_chain.load_anchor_chain()

        est_in, est_out = estimate_tokens_from_payload(api_kwargs)
        est_cost = anchor_chain.estimate_call_cost(endpoint, est_in, est_out, chain.pricing)
        allowed, spend_now, projected = anchor_chain.cap_check(chain, est_cost)
        if not allowed:
            rec["blocked"] = True
            rec["spend"] = spend_now
            rec["cap"] = chain.daily_cap_usd
            rec["_blocked_payload"] = rec  # surfaced to caller for logging
            return ("cap_blocked", {"spend": spend_now, "cap": chain.daily_cap_usd,
                                    "route_id": rec.get("route_id"), "task_id": rec.get("task_id")})

        content, cost = anchored_call(endpoint, api_kwargs)
        if content is None:
            return None
        real_cost = cost if cost is not None else est_cost
        if real_cost > 0:
            anchor_chain.record_spend(real_cost)

        decision_like = router_core.RouteDecision(
            task_id=rec.get("task_id") or "", lane=router_core.LANE_COMPLEXITY,
            mode=rec.get("mode") or router_core.MODE_PLAN,
            model_target=endpoint.model, reason="anchored_call",
            route_id=rec.get("route_id") or "",
        )
        kind = "consultation" if decision_like.mode == router_core.MODE_CONSULT else "frontier_plan"
        envelope = router_core.build_frontier_envelope(
            kind, endpoint.model, decision_like, content,
            limitations="single-shot anchored call; no tool access",
        )
        router_core.store_consult_result(decision_like.route_id or "anon", envelope)
        return ("done", envelope)
    except Exception as exc:  # noqa: BLE001
        logger.debug("maybe_execute_anchored error: %s", exc)
        return None