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
import re
import threading
from typing import Any, Dict, Optional, Tuple

from . import anchor_chain
from . import router_core

logger = logging.getLogger(__name__)

# v3.2.1: Hermes injects a <memory-context>...</memory-context> block into the
# USER turn (recalled-memory wrapper containing route-log/classifier terms:
# ied_construction, csam_underage, uncensored, content_filter, ...). The
# Anthropic/OpenRouter content-filter trips on that block -> finish=
# content_filter, content empty (conductor A/B-reproduced 2026-09-05 ~11:05,
# deterministic: same ask without the block -> 8692 chars delivered; with the
# block -> content_filter). The anchor replay strips ONLY the wrapper — the
# user's actual ask inside/after it passes through byte-for-byte. Compiled
# once at module level (brief: cap compile once).
_MEMORY_CONTEXT_RE = re.compile(r"<memory-context>.*?</memory-context>\s*",
                                flags=re.DOTALL)


# ---------------------------------------------------------------------------
# Key resolution (key_file discipline not required here — anchor keys are env
# refs by design: OPENROUTER_API_KEY or the custom provider's key_env)
# ---------------------------------------------------------------------------


_PLACEHOLDER_VALUES = {"", "not-needed", "none", "null", "placeholder", "changeme", "your-key-here"}


def _profile_env_value(env: str) -> str:
    """Read `env` from the profile dotenv. The gateway process env carries the
    GLOBAL /opt/data/.env values; profile .env files hold the real per-profile
    credentials. Live-caught 2026-09-05: os.environ held the global placeholder
    'not-needed' while profile .env files had the real OpenRouter key.
    Search order: {HERMES_HOME}/.env first (exact profile); if that yields a
    placeholder-or-missing value, scan the standard profiles root — all 12 fleet
    profiles share ONE OpenRouter account, so a sibling profile's key is the
    correct credential by fleet design (memory: OpenRouter = single shared
    prepaid account across all 12 profile keys)."""
    candidates = []
    try:
        home = os.environ.get("HERMES_HOME") or ""
        if home:
            candidates.append(os.path.join(home, ".env"))
            # gateway processes may keep HERMES_HOME at the hermes root (/opt/data)
            # even when running under -p <profile>; also try the canonical profile
            # directory for this home.
            if os.path.basename(home) != "profiles" and os.path.isdir(
                    os.path.join(home, "profiles")):
                profs_root = os.path.join(home, "profiles")
            else:
                profs_root = None
        else:
            profs_root = None
        if profs_root:
            try:
                names = sorted(os.listdir(profs_root))
            except OSError:
                names = []
            for name in names:
                cand = os.path.join(profs_root, name, ".env")
                if os.path.isfile(cand):
                    candidates.append(cand)
        for path in candidates:
            val = _read_env_file(path, env)
            if val and val.lower() not in _PLACEHOLDER_VALUES:
                return val
    except Exception:  # noqa: BLE001 — key resolution must never raise
        return ""
    return ""


def _read_env_file(path: str, env: str) -> str:
    prefix = f"{env}="
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(prefix) and not line.lstrip().startswith("#"):
                    return line[len(prefix):].strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001
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

# R10-2 (rider 10): session-scoped last frontier_consult ledger row id,
# written by maybe_execute_anchored when the row lands and read by the
# §10.2 banner render at on_llm_execution — which holds only a
# PRE-EXECUTION peek copy of the swap record and would otherwise render
# 'ledger-row MISSING' while the row exists.
_LAST_FRONTIER_ROW: Dict[str, int] = {}
_LAST_FRONTIER_ROW_LOCK = threading.Lock()


def last_frontier_row(session_id: str) -> int:
    """The session's last frontier_consult ledger row id (0 when none).
    Never raises."""
    try:
        with _LAST_FRONTIER_ROW_LOCK:
            return int(_LAST_FRONTIER_ROW.get(str(session_id or ""), 0))
    except Exception:  # noqa: BLE001 — fail-open 0
        return 0


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


def usage_tokens_from_response(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """v3.5.0 tokens-ledger tap: extract (prompt_tokens, completion_tokens)
    from a response usage block. (None, None) when usage absent — callers
    record nothing rather than estimate (blueprint D9). Never raises."""
    try:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return None, None
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        if not isinstance(pt, (int, float)) or not isinstance(ct, (int, float)):
            return None, None
        return int(pt), int(ct)
    except Exception:  # noqa: BLE001
        return None, None


# ---------------------------------------------------------------------------
# The anchored provider call (per-call client; nothing persists)
# ---------------------------------------------------------------------------


def anchored_call(endpoint: anchor_chain.AnchorEndpoint, api_kwargs: Dict[str, Any],
                  *, timeout: int = 300) -> Tuple[Optional[str], Optional[float],
                                                  Optional[int], Optional[int]]:
    """Run the FULL provider payload against the anchor endpoint with a
    per-call client.

    Returns (content, cost_usd, prompt_tokens, completion_tokens):
      content None -> failure (caller passes flash through; fail-open)
      cost_usd     -> recorded spend (None when usage absent)
      tokens       -> real usage from the response when present, else None
                      (v3.5.0 tokens-ledger tap; previously discarded)
    Never raises. Streaming kwargs are stripped — anchored calls are
    non-streaming single-shot.

    System-message sanitization (live-caught 2026-09-05): the flash outbound
    payload carries the profile's DNA-persona system prompt (uncensored
    doctrine wording). Anthropic/OpenRouter content-filter zeroed content
    (finish=content_filter) on those words — the frontier anchor is a TASK
    consultant, not a persona continuation. System messages are replaced with
    a neutral task frame; user/assistant turns pass through unchanged.
    """
    try:
        import httpx
        from openai import OpenAI

        api_key = _resolve_key(endpoint)
        if not api_key:
            logger.error("anchor_route_failed reason=key_unavailable key_env=%s",
                         endpoint.api_key_env)
            return None, None, None, None

        payload = copy.deepcopy(api_kwargs)
        payload.pop("stream", None)
        payload.pop("stream_options", None)
        payload.pop("__bedrock_region__", None)
        payload.pop("__bedrock_converse__", None)
        payload.pop("_moa_prepared_request", None)
        payload.pop("timeout", None)
        payload["model"] = endpoint.model
        # Goran 09-09: anchor thinking effort from config (anchor_chain.reasoning_effort)
        # v3.8.3 fix (valmet live catch): reasoning_effort is NOT injected for
        # tiny-payload probes (max_tokens < 500 — doctor ping, health checks).
        # glm-5.3 with reasoning_effort=max burns its whole budget on reasoning
        # inside a 16-token cap -> finish=length -> empty_response -> the agent
        # sees "anchor cal failed" on a perfectly healthy chain.
        try:
            from . import config_access as _ca
            _eff = (_ca.sub_block("anchor_chain") or {}).get("reasoning_effort")
            _mt = payload.get("max_tokens")
            _tiny_probe = isinstance(_mt, int) and _mt < 500
            if isinstance(_eff, str) and _eff.strip() and not _tiny_probe:
                payload["reasoning_effort"] = _eff.strip()
        except Exception:  # noqa: BLE001 — config read must never break anchor
            pass

        # System sanitization: frontier anchor gets a neutral task frame, never
        # the flash profile's DNA-persona system prompt (content-filter bait).
        msgs = payload.get("messages")
        if isinstance(msgs, list):
            _sys_idx = [i for i, m in enumerate(msgs)
                        if isinstance(m, dict) and m.get("role") == "system"]
            if _sys_idx:
                _frame = ("You are a senior specialist consultant answering one bounded technical "
                          "question. Answer the user's request directly and completely. "
                          "No tools are available; do not request any.")
                for i in _sys_idx:
                    msgs[i] = {"role": "system", "content": _frame}
        payload.pop("tools", None)  # anchored calls are single-shot advisory; tool schemas are flash-lane
        payload.pop("tool_choice", None)
        payload.pop("parallel_tool_calls", None)

        # v3.2.1: strip Hermes' <memory-context>...</memory-context> wrapper
        # from EVERY user-role message in the anchor replay payload (A/B-
        # reproduced content_filter trigger). The user's actual ask inside and
        # after the wrapper is untouched; assistant/system messages are not
        # processed here (system already replaced above). Best-effort: a strip
        # failure leaves the payload unchanged (fail-open), never breaks the
        # anchored call.
        try:
            _removed = 0
            if isinstance(msgs, list):
                for i, m in enumerate(msgs):
                    if isinstance(m, dict) and m.get("role") == "user":
                        _txt = m.get("content")
                        if isinstance(_txt, str) and "<memory-context>" in _txt:
                            _stripped = _MEMORY_CONTEXT_RE.sub("", _txt)
                            _removed += len(_txt) - len(_stripped)
                            msgs[i] = {**m, "content": _stripped}
            if _removed:
                logger.info("anchor_memory_context_stripped chars_removed=%d",
                            _removed)
        except Exception:  # noqa: BLE001 — strip is best-effort, never fatal
            pass

        client = OpenAI(base_url=endpoint.base_url, api_key=api_key,
                        timeout=float(timeout), max_retries=0)
        # deep-consult fix (f6): timeout must actually CANCEL the provider
        # call, not just abandon the wait — otherwise timed-out consults
        # still bill for full generation. httpx per-request timeout is
        # enforced by the transport (socket-level), so the underlying
        # request is torn down when it fires; openai's max_retries=0
        # prevents silent re-fires. Defensive: fake clients in tests have
        # no _client — skip silently (fail-open to normal timeout).
        try:
            client._client.timeout = httpx.Timeout(float(timeout), connect=10.0)
        except Exception:  # noqa: BLE001
            pass
        try:
            resp = client.chat.completions.create(**payload)
        except Exception as _400:
            # Nous/z-ai 400 (live 2026-09-10, valmet+conductor): the upstream
            # occasionally rejects reasoning_effort claiming both
            # "reasoning_effort" and "reasoning.effort" are provided with
            # conflicting values — its gateway maps the top-level key for some
            # z-ai requests. Defense: retry once WITHOUT reasoning_effort.
            _msg = str(_400)
            if "reasoning" in _msg and "conflicting values" in _msg and payload.pop("reasoning_effort", None):
                logger.info("anchor_reasoning_conflict_retry without_reasoning_effort")
                try:
                    resp = client.chat.completions.create(**payload)
                finally:
                    try:
                        client.close()
                    except Exception:  # noqa: BLE001
                        pass
            else:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass
                raise
        else:
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
            return None, None, None, None

        pricing = anchor_chain.load_anchor_chain().pricing
        cost = usage_cost_from_response(raw, pricing, endpoint.model)
        # v3.5.0: pass real usage tokens back with the cost tuple (blueprint
        # 5.2 tap 2) — tokens were previously extracted then discarded.
        pt, ct = usage_tokens_from_response(raw)
        return str(content), cost, pt, ct
    except Exception as exc:  # noqa: BLE001 — anchored lane must never raise
        logger.error("anchor_route_failed reason=exception detail=%.300s", str(exc))
        return None, None, None, None


def orientation_ask_cap() -> int:
    """orientation_ask_cap (int, default 4000 = SUBSTANCE_FRAME_ASK_CAP).
    Char cap on the verbatim ask inside the orientation-brief frame.
    Reads via config_access (live-read, dual-section). Never raises."""
    try:
        from . import config_access
        v = int((config_access.router_section() or {}).get("orientation_ask_cap", 4000))
        return v if v > 0 else 4000
    except Exception:  # noqa: BLE001
        return 4000


def _bounded_replay_cfg() -> Dict[str, Any]:
    """anchor_chain.bounded_replay block (dual-section reader via router_core).
    Defaults: enabled=True, mode=bounded, last_n_turns=24 (2026-09-09 bump
    from 12, Goran dispatch B3), keep_system=True,
    max_input_tokens=120000. Never raises."""
    try:
        from . import router_core
        cfg = router_core._complexity_cfg().get("bounded_replay")
        block = dict(cfg) if isinstance(cfg, dict) else {}
        return {
            "enabled": bool(block.get("enabled", True)),
            "last_n_turns": max(2, int(block.get("last_n_turns", 24))),
            "max_input_tokens": max(8000, int(block.get("max_input_tokens", 120000))),
            "summary_header": bool(block.get("summary_header", True)),
        }
    except Exception:  # noqa: BLE001
        return {"enabled": True, "last_n_turns": 24,
                "max_input_tokens": 120000, "summary_header": True}


def bounded_replay(api_kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """v3.4.1 bounded replay for anchored calls (flagship audit F2, luna verdict:
    config-switchable, bounded DEFAULT). Trims the replayed conversation to the
    last-N message pairs plus a compact task header, with a hard input-token cap.
    full mode (enabled:false) = legacy full-conversation replay. Never raises."""
    try:
        cfg = _bounded_replay_cfg()
        if not cfg.get("enabled"):
            return api_kwargs
        msgs = api_kwargs.get("messages")
        if not isinstance(msgs, list) or len(msgs) <= 2:
            return api_kwargs
        system_msgs = [m for m in msgs if isinstance(m, dict) and m.get("role") == "system"]
        convo = [m for m in msgs if not (isinstance(m, dict) and m.get("role") == "system")]
        if len(convo) <= cfg["last_n_turns"]:
            return api_kwargs
        kept = convo[-cfg["last_n_turns"]:]
        header = ""
        if cfg.get("summary_header"):
            first_user = next((m for m in convo if isinstance(m, dict) and m.get("role") == "user"), None)
            ask = str((first_user or {}).get("content") or "")[:400]
            header = (f"[context note: consult replay is bounded to the last "
                      f"{len(kept)} turns. The user's original ask, verbatim: "
                      f'"{ask}"]')
        out = list(system_msgs) + ([{"role": "system", "content": header}] if header else []) + kept
        # hard input-token cap: drop oldest kept turns until under cap
        est = sum(len(str(m.get("content") or "")) // 4 for m in out)
        while est > cfg["max_input_tokens"] and len(kept) > 2:
            kept = kept[1:]
            out = list(system_msgs) + ([{"role": "system", "content": header}] if header else []) + kept
            est = sum(len(str(m.get("content") or "")) // 4 for m in out)
        trimmed = dict(api_kwargs)
        trimmed["messages"] = out
        return trimmed
    except Exception:  # noqa: BLE001
        return api_kwargs


# R16-4 (rider 16): bounded same-turn anchor retry registry. In-flight
# retry workers register here so a POST delivery edge can wait bounded
# for the verdict banner to park before consuming (mirror of the
# decision lane's post_worker_wait contract). Never raises.
_ANCHOR_RETRY_LOCK = threading.Lock()
_ANCHOR_RETRIES: set = set()
_ANCHOR_RETRY_MAX_CONCURRENT = 2
ANCHOR_RETRY_TIMEOUT_S = 600  # extended budget: 2x the 300s socket timeout


def pending_anchor_retries() -> int:
    try:
        with _ANCHOR_RETRY_LOCK:
            return len(_ANCHOR_RETRIES)
    except Exception:  # noqa: BLE001
        return 0


def wait_for_anchor_retries(timeout_s: float = 20.0) -> int:
    """Bounded wait for in-flight anchor retry workers to park their
    verdict banner BEFORE a POST delivery edge consumes the parked slot.
    Returns the wait actually spent (seconds, rounded). Never raises."""
    try:
        import time as _t

        deadline = _t.time() + max(0.0, float(timeout_s or 0.0))
        spent = 0.0
        while _t.time() < deadline:
            if pending_anchor_retries() <= 0:
                return int(round(spent))
            _t.sleep(0.2)
            spent = min(deadline - _t.time() if deadline > _t.time()
                        else 0.0, spent + 0.2) or spent
        return int(round(float(timeout_s or 0.0)))
    except Exception:  # noqa: BLE001 — wait must never break delivery
        return 0


def retry_anchored_async(session_id: str, rec: Dict[str, Any],
                         api_kwargs: Dict[str, Any], ep: Any, *,
                         base_timeout: int = 300) -> None:
    """R16-4 (rider 16): schedule ONE bounded retry of a failed/timed-out
    anchored consult. The retry runs off the turn path (daemon thread,
    semaphored) with an extended socket timeout (2x base — the first probe's
    >300s latency is provider-side; the code side must still land the
    verdict). On success: tokens + frontier_consult ledger row (same R9-5
    contract as the inline success arm) and the verdict PARKS through the
    standard banner machinery, so the earliest delivery edge delivers it.
    On second failure: fail-loud banner (anchored_call_failed) — never a
    silent orphan. Never raises; never blocks the caller."""
    try:
        with _ANCHOR_RETRY_LOCK:
            if len(_ANCHOR_RETRIES) >= _ANCHOR_RETRY_MAX_CONCURRENT:
                logger.info("anchor_retry_skipped reason=cap_exhausted "
                            "in_flight=%d", len(_ANCHOR_RETRIES))
                return
            _ANCHOR_RETRIES.add(session_id or "")
        task_id = str((rec or {}).get("task_id") or "")
        route_id = str((rec or {}).get("route_id") or "")

        def _log(event: str, **fields: Any) -> None:
            try:
                # Rider 19 item 1: alias-safe relative resolution — the live
                # gateway loads this module as hermes_plugins.hermes_router
                # (no hermes_router top-level name on sys.path), so the bare
                # `import hermes_router` raised ModuleNotFoundError and the
                # event was swallowed.
                from . import _log_route as _lrh

                _lrh("PRE", event_detail=event,
                               session_id=str(session_id or ""), **fields)
            except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
                logger.warning("route_event_emit_failed where=anchor_retry err=%r",
                               _obs_exc)

        def _worker() -> None:
            try:
                _log("anchor_retry_started", task_id=task_id,
                     timeout_s=ANCHOR_RETRY_TIMEOUT_S)
                content, cost, pt, ct = anchored_call(
                    ep, bounded_replay(api_kwargs),
                    timeout=ANCHOR_RETRY_TIMEOUT_S)
                if not content:
                    # second failure — fail loud, never silent
                    _log("anchor_retry_failed", task_id=task_id,
                         reason="anchored_call_failed")
                    try:
                        from . import debug_banner as _dbd

                        if _dbd.debug_banner_enabled():
                            _fb = _dbd.format_banner(
                                lane="anchor", trigger="consult_failed",
                                model=str(getattr(ep, "model", "") or "unknown"),
                                endpoint="", tokens_in=0, tokens_out=0,
                                est_cost=0.0, latency_s=0.0, retries=1,
                                task_id=task_id, session_id=str(session_id or ""),
                                route_id=route_id)
                            _fb = ((_fb or "").rstrip().removesuffix("·").rstrip()
                                   + " | anchored_call_failed (retry exhausted) ·")
                            if _fb:
                                _dbd.park_anchor_banner(
                                    str(session_id or ""), _fb, task_id=task_id)
                    except Exception:  # noqa: BLE001
                        pass
                    return
                # SUCCESS: mirror the inline arm's obligations (R9-5 ledger +
                # tokens tap) and park the verdict banner.
                try:
                    from .decision import ledger_write as _lw

                    _lw({"session_id": str(session_id or ""),
                         "task_id": task_id, "trigger": "frontier_consult",
                         "fork_class": "frontier_consult",
                         "model": str(getattr(ep, "model", "") or ""),
                         "choice": "",
                         "verdict_json": str(content or "")[:2000],
                         "outcome": "completed"})
                except Exception:  # noqa: BLE001 — ledger never breaks the lane
                    pass
                try:
                    from . import usage_ledger as _ul

                    if pt is not None or ct is not None:
                        real_cost = cost if cost is not None else 0.0
                        _ul.record_tokens(
                            "anchor", str(getattr(ep, "model", "") or ""),
                            str(session_id or ""), pt, ct, real_cost,
                            "consult", task_id=task_id)
                except Exception:  # noqa: BLE001 — observability only
                    pass
                try:
                    from . import debug_banner as _dbd2

                    if _dbd2.debug_banner_enabled():
                        _ti, _to, _tc = 0, 0, 0.0
                        _vb = _dbd2.format_banner(
                            lane="frontier-anchor", trigger="consult_retry",
                            model=str(getattr(ep, "model", "") or ""),
                            endpoint="", tokens_in=_ti, tokens_out=_to,
                            est_cost=_tc, latency_s=0.0, retries=1,
                            task_id=task_id, session_id=str(session_id or ""),
                            route_id=route_id)
                        _vb = ((_vb or "").rstrip().removesuffix("·").rstrip()
                               + " | verdict delivered ·\n\n"
                               + str(content or "")[:2000])
                        if _vb:
                            _dbd2.park_anchor_banner(str(session_id or ""),
                                                     _vb, task_id=task_id)
                        _log("anchor_retry_delivered", task_id=task_id)
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001 — worker must never crash the lane
                logger.debug("anchor retry worker error", exc_info=True)
            finally:
                try:
                    with _ANCHOR_RETRY_LOCK:
                        _ANCHOR_RETRIES.discard(session_id or "")
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=_worker, daemon=True).start()
    except Exception:  # noqa: BLE001 — scheduling must never break the turn
        logger.debug("anchor retry schedule error", exc_info=True)
        try:
            with _ANCHOR_RETRY_LOCK:
                _ANCHOR_RETRIES.discard(session_id or "")
        except Exception:  # noqa: BLE001
            pass


# Retired name (caller sites keep a stable entry point):
def _retry_anchored_async(session_id: str, rec: Dict[str, Any],
                          api_kwargs: Dict[str, Any], ep: Any, *,
                          base_timeout: int = 300) -> None:
    retry_anchored_async(session_id, rec, api_kwargs, ep,
                         base_timeout=base_timeout)


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

        # v3.6.1 PRE-orientation (Goran 09-08): when the swap carries the
        # orientation flag, frontier does NOT solve the task — it answers the
        # orientation questions (result shape, watch-fors, pitfalls + known
        # good solutions, avoid-list, failure shape). Advisory only.
        if bool(rec.get("orientation")):
            try:
                _msgs = api_kwargs.get("messages")
                if isinstance(_msgs, list) and _msgs:
                    _last_user = None
                    for _i in range(len(_msgs) - 1, -1, -1):
                        if isinstance(_msgs[_i], dict) and _msgs[_i].get("role") == "user":
                            _last_user = _i
                            break
                    if _last_user is not None:
                        _orig = str(_msgs[_last_user].get("content") or "")
                        # Manual 'anchor this' (Goran 2026-09-10): the consult
                        # must include the agent's own current self-assessment
                        # so frontier observes ask + agent state, not the ask
                        # alone. Last 700 chars of the preceding context give
                        # the agent's recent self-reflect; absent when none.
                        _self_reflect = ""
                        try:
                            for _j in range(_last_user - 1, -1, -1):
                                _m = _msgs[_j]
                                if isinstance(_m, dict) and _m.get("role") == "assistant":
                                    _sa = str(_m.get("content") or "").strip()
                                    if _sa:
                                        _self_reflect = _sa[-700:]
                                    break
                        except Exception:  # noqa: BLE001
                            _self_reflect = ""
                        _sr_block = ("\n\nTHE AGENT'S CURRENT SELF-ASSESSMENT (her own last words, "
                                     "for context — observe ask AND agent):\n" + _self_reflect) if _self_reflect else ""
                        # C-F2 (FIX-FIRST rider 7): the PRE frame saw ask[:4000]
                        # + 700-char self-assessment ONLY — no causal chain.
                        # Reuse the decision lane's _causal_context (the same
                        # bounded recent-tail the impulse envelope gets) so the
                        # orientation consult observes the chain that produced
                        # the fork, not the ask in isolation. Bounded (1200c),
                        # state.db read-only, fail-open ''.
                        _causal_block = ""
                        try:
                            from .decision import _causal_context as _cc
                            _causal = _cc(session_id, _orig, {})
                            if _causal and _causal.strip():
                                _causal_block = ("\n\nCAUSAL CONTEXT (bounded recent "
                                                 "tail of the session):\n" + _causal)
                        except Exception:  # noqa: BLE001 — tail never breaks the consult
                            _causal_block = ""
                        _frame = (
                            "You are the agent's higher intuition at task START. "
                            "Do NOT solve the task. Given the ask below, produce a terse "
                            "ORIENTATION BRIEF (max 8 bullets total):\n"
                            "1. What the end result SHOULD look like (success shape).\n"
                            "2. What to be careful of during the process.\n"
                            "3. Common pitfalls and known good solutions.\n"
                            "4. What to avoid.\n"
                            "5. How failure would look like (early-warning signs).\n"
                            "Advisory only — the agent may deviate.\n\nTHE ASK:\n"
                            + _orig[:orientation_ask_cap()] + _sr_block + _causal_block
                        )
                        # R19.13 B+ 5b (Goran addendum): PRE doubt-seed — the
                        # adversarial element rides INSIDE this consult (one
                        # billed call, never a separate render/lane/chain).
                        # Fires on irreversible/fleet-risk-shaped asks OR
                        # when the declared adversarial phrases force it on
                        # (5d, even light consults). Fail-open.
                        try:
                            from .completion_audit import (
                                _adversarial_seed_instruction as _adv_seed,
                                _irreversible_risk_ask as _irr,
                            )
                            from .route_gate import (
                                adversarial_declared as _adv_decl,
                                adversarial_family_hit as _adv_family,
                            )
                            if _irr(_orig) or _adv_decl(_orig) or \
                                    _adv_family(_orig):
                                _frame += _adv_seed()
                        except Exception:  # noqa: BLE001 — seed never breaks the consult
                            pass
                        # C-F1/C-F3 (FIX-FIRST rider 7): the multi-POV
                        # instruction was wired ONLY into the POST audit; the
                        # PRE orientation frame never carried it. Same gate
                        # family as POST (_pov_active), with the C-F3
                        # double-gate fix: this consult ALREADY passed the
                        # complexity gate to be staged (complexity lane), so
                        # 'auto' must not re-gate — the POV rides whenever
                        # pov_mode != off. 'always'/'auto' both include;
                        # 'off' never does. Never raises.
                        try:
                            from .completion_audit import (
                                pov_mode as _pov_mode,
                                _pov_instruction as _pov_inst,
                            )
                            if _pov_mode() != "off":
                                _frame += _pov_inst()
                        except Exception:  # noqa: BLE001 — POV never breaks the consult
                            pass
                        _msgs[_last_user] = {**_msgs[_last_user], "content": _frame}
                        # Agent-tailored frontier consults (Goran 2026-09-10):
                        # prepend the profile's own compact persona card so the
                        # consultant orients THE AGENT (its role, voice, closed
                        # lines), not a generic specialist. Derived at runtime
                        # from HERMES_HOME => universal on any Hermes setup.
                        # Runs AFTER the frame write — inserting a system msg
                        # shifts indices, so _last_user must already be consumed.
                        try:
                            from . import persona_card as _pc
                            _card = _pc.build_persona_context()
                            if _card and _card.strip():
                                _tailored = (
                                    "You are consulting for a specific AI agent whose profile card follows. "
                                    "Tailor your orientation/audit to that agent's role, voice and boundaries.\n\n"
                                    + _card.strip())
                                _has_sys = any(isinstance(m, dict) and m.get("role") == "system" for m in _msgs)
                                if _has_sys:
                                    for m in _msgs:
                                        if isinstance(m, dict) and m.get("role") == "system":
                                            m["content"] = str(m.get("content") or "") + "\n\n" + _tailored
                                            break
                                else:
                                    _msgs.insert(0, {"role": "system", "content": _tailored})
                        except Exception:  # noqa: BLE001 — tailoring is best-effort
                            pass
                        api_kwargs["messages"] = _msgs
            except Exception:  # noqa: BLE001 — orientation frame is best-effort
                pass
        content, cost, pt, ct = anchored_call(endpoint, bounded_replay(api_kwargs))
        if content is None:
            # R16-4 (rider 16): anchored consult timed out / failed. The old
            # path just returned None — the flash call passed through and the
            # agent saw only the staged/verdict-pending state FOREVER (live:
            # analyst luna-pro probe, first probe timed out >300s, the
            # reprobe delivered only 'staged / verdict pending', decision
            # ledger row 153 outcome=completed with an empty verdict on the
            # SUCCESS arm but nothing when the call itself died). A long
            # frontier generation is infra-side (provider latency beyond the
            # socket timeout); the CODE side must not stop at the drop: a
            # bounded same-turn retry worker re-enters the anchor with an
            # extended timeout, and its verdict parks through the standard
            # banner machinery the moment it lands (earliest delivery edge
            # consumes it — same-turn when it lands inside the POST wait
            # window, otherwise the next edge; never a silent orphan).
            _retry_anchored_async(session_id, rec, api_kwargs, ep,
                                  base_timeout=300)
            return None
        # R19.13 B+ 5b/5e: parse the adversarial block from the PRE verdict
        # (nullable, fail-open) and write the p_failure ledger row for
        # materialization labeling. The seed TEXT rides the brief as-is.
        try:
            from .completion_audit import (
                _parse_adversarial as _parse_adv,
                _adversarial_ledger_row as _adv_row,
            )

            _adv = _parse_adv(content)
            if _adv:
                _log("frontier_pre_adversarial p_failure=%s" %
                     _adv.get("p_failure"), session_id=str(
                         rec.get("session_id") or ""))
                _adv_row(str(rec.get("session_id") or ""),
                         str(rec.get("task_id") or rec.get("route_id") or ""),
                         getattr(endpoint, "model", ""), _adv, str(content))
        except Exception:  # noqa: BLE001 — adversarial never breaks the consult
            pass
        real_cost = cost if cost is not None else est_cost
        if real_cost > 0:
            anchor_chain.record_spend(real_cost)
            # Leg 2 (request-routing blueprint): the agent's durable
            # per-agent cap counter accumulates ONLY here — after a
            # successful claim (H7.1). Best-effort, never breaks the lane.
            # Leg 6: initiator resolves from the gate's claim source
            # (declared_user->user, declared_agent->agent, auto/legacy->auto).
            try:
                from . import route_gate as _rg
                from . import routing_caps

                _initiator = _rg.initiator_for_task(
                    str(rec.get("task_id") or ""))
                # Leg 9: spend keys by AGENT IDENTITY (profile) — accumulate
                # across ALL of the agent's sessions.
                routing_caps.record_agent_spend(
                    routing_caps.agent_identity(), real_cost,
                    initiator=_initiator,
                    lane="higher-pre")
                routing_caps.record_cooldown(session_id)
            except Exception:  # noqa: BLE001
                pass
        # v3.5.0 tokens-ledger tap (blueprint 5.2): record the anchor lane's
        # real usage tokens when the provider supplied them; usage-absent
        # calls record nothing (never estimate). Strictly fail-open — a
        # ledger failure must never affect the consult delivery path.
        _initiator = "auto"
        try:
            from . import route_gate as _rg

            _initiator = _rg.initiator_for_task(str(rec.get("task_id") or ""))
        except Exception:  # noqa: BLE001
            pass
        try:
            from . import usage_ledger

            if pt is not None or ct is not None:
                # v3.6 §10.4-E correlation: task_id + monotonic event seq.
                try:
                    from . import suggestions as _sg

                    _seq = _sg.next_event_seq()
                except Exception:  # noqa: BLE001
                    _seq = None
                usage_ledger.record_tokens(
                    "anchor", endpoint.model, session_id, pt, ct,
                    real_cost if cost is not None else
                    usage_ledger.estimate_cost(endpoint.model, pt, ct),
                    "consult",
                    task_id=str(rec.get("task_id") or ""), event_seq=_seq,
                    initiator=_initiator,
                )
        except Exception:  # noqa: BLE001 — observability must never break the lane
            pass
        # R9-5 (rider 9): EVERY billed consult must ledger — the anchored
        # frontier consult billed the tokens lane but wrote ZERO
        # decision_ledger rows (T1 A4/D1b: consult fired, $0.0147 billed,
        # no ledger trail — the R19 provenance-break family migrated to
        # the frontier lane). The row mirrors completion_audit's
        # frontier_consult rows; failure is fail-loud, never lane-breaking.
        try:
            from .decision import ledger_write as _frontier_lw

            _rid = _frontier_lw({
                "session_id": str(session_id or ""),
                "task_id": str(rec.get("task_id") or ""),
                "trigger": "frontier_consult",
                "fork_class": "frontier_consult",
                "model": str(getattr(endpoint, "model", "") or ""),
                "choice": "",
                "verdict_json": str(content or "")[:2000],
                "outcome": "completed",
            })
            if _rid is None:
                try:
                    _log("frontier_consult_ledger_write_FAILED "
                         "task_id=%s" % str(rec.get("task_id") or ""),
                         session_id=str(session_id or ""))
                except Exception:  # noqa: BLE001
                    pass
            else:
                # R9-7 (rider 9): the row id rides the swap record so the
                # §10.2 banner at on_llm_execution carries the reconcilable
                # `row=<rid>` ref (same contract as the decision lane).
                rec["frontier_ledger_row"] = int(_rid)
                # R10-2 (rider 10): on_llm_execution reads a PRE-EXECUTION
                # peek COPY of the swap record (peek_pending_swap returns
                # dict(rec) — router_core.py), which can never see the key
                # the LIVE popped record just gained (T1R3 P1a/b/c
                # MISSING-while-row-exists). Expose the session-scoped
                # last write for the banner render to reconcile.
                with _LAST_FRONTIER_ROW_LOCK:
                    _LAST_FRONTIER_ROW[str(session_id or "")] = int(_rid)
        except Exception:  # noqa: BLE001 — ledger must never break the consult
            pass

        decision_like = router_core.RouteDecision(
            task_id=rec.get("task_id") or "", lane=router_core.LANE_COMPLEXITY,
            mode=rec.get("mode") or router_core.MODE_CONSULT,
            model_target=endpoint.model, reason="anchored_call",
            route_id=rec.get("route_id") or "",
        )
        kind = "orientation" if bool(rec.get("orientation")) else "consultation"
        envelope = router_core.build_frontier_envelope(
            kind, endpoint.model, decision_like, content,
            limitations="single-shot anchored call; no tool access",
        )
        # R9-9 (rider 9): consult-envelope provenance stamps — model + cost
        # ride the envelope so the tool-stream delivery is self-contained
        # discrimination (route_id already stamped by the builder).
        try:
            envelope["model"] = str(getattr(endpoint, "model", "") or "")
            envelope["cost"] = float(real_cost if real_cost else 0.0)
        except Exception:  # noqa: BLE001 — stamps never break the consult
            pass
        router_core.store_consult_result(decision_like.route_id or "anon", envelope)
        return ("done", envelope)
    except Exception as exc:  # noqa: BLE001
        logger.debug("maybe_execute_anchored error: %s", exc)
        # Rider 15 R15-5: a swallowed exception here is the silent-zero
        # signature (claim staged=True, NO events, NO spend, NO banner —
        # the four-leg dead turn). Never fail-open silently again: the
        # failure is OBSERVABLE at route-log level before the pass-through.
        try:
            from .core.telemetry import log_route as _lrx  # P1: core telemetry (import cycle broken)

            _lrx("PRE", event_detail="anchor_execution_exception",
                 fail_kind=str(exc)[:160],
                 fail_type=type(exc).__name__,
                 session_id=str(session_id or ""))
        except Exception as _obs_exc:  # noqa: BLE001 — fail-loud, never dispatch-breaking (rider 19 item 1)
            _obs_warn('anchor_execution_exception', _obs_exc)  # rider 19 item 1
        return None