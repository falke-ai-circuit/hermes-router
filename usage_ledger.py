"""Plugin-side token-usage ledger for hermes-router (v3.5.0, blueprint 5.2).

The router drives three plugin-side model lanes whose token usage Hermes core
never sees (core accounting covers only the flash/main model in state.db):

  - render lane  (router.py curl chain)  - body is JSON-parsed, usage never read
  - anchor lane  (anchor_exec.py OpenAI) - tokens extracted for pricing, then
               discarded; only cost enters the spend ledger
  - aux lane     (semantic_classifier.py) - plugin-side HTTP, bypasses core

This ledger records (lane, model, session_id, input/output tokens, est cost)
per successful call as append-only JSONL so /router stats can render honest
per-lane token truth. It is DESCRIPTIVE ONLY: the spend ledger
(hermes-router-spend.json) stays the authoritative number the cap guard
enforces against - one authority per purpose, never two.

Honesty rule (blueprint D9): when the provider response carries no usage
block, record NOTHING - never estimate. An estimate column that looks like
measurement is worse than a gap. /router stats marks lanes partial instead.

Write discipline (canonical.py / render_inbox.py family): append-only, single
open, rotate past 10MB, chmod 0600, every failure silently swallowed - a
ledger write failure NEVER breaks a route (same contract as record_spend,
anchor_chain.py).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TOKENS_FILENAME = "hermes-router-tokens.jsonl"
MAX_FILE_BYTES = 10 * 1024 * 1024  # rotate past 10 MB (blueprint 5.2)
_ROTATE_KEEP_LINES = 500
# Aggregation scans read at most this many trailing lines (bounded reads).
_SCAN_LINES = 20000

_lock = threading.Lock()

VALID_LANES = ("render", "anchor", "aux")


def _store_path() -> str:
    """Profile-scoped ledger path via hermes_constants.get_hermes_home().
    Falls back to /tmp with a shadow-qualified name (mirrors canonical.py)."""
    try:
        import hermes_constants

        return str(hermes_constants.get_hermes_home() / TOKENS_FILENAME)
    except Exception:  # noqa: BLE001
        return os.path.join("/tmp", "shadow-" + TOKENS_FILENAME)


def _maybe_rotate_locked(path: str) -> None:
    try:
        if os.path.getsize(path) <= MAX_FILE_BYTES:
            return
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        keep = lines[-_ROTATE_KEEP_LINES:]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(keep)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001 - rotation must never break the lane
        logger.debug("tokens ledger rotate failed: %s", exc)


def record_tokens(lane: str, model: str, session_id: str,
                  input_tokens: Optional[int], output_tokens: Optional[int],
                  est_cost: Optional[float], detail: str = "",
                  task_id: str = "", event_seq: Optional[int] = None) -> bool:
    """Append one token-usage record. Returns True when written.

    Usage-absent calls (input/output None) record NOTHING - honest-absent
    beats plausible-fake (blueprint D9). v3.6 §10.4-E: records carry
    correlation task_id + monotonic event seq (suggestions.next_event_seq)
    so concurrent tool-cycle events never misattribute. Never raises; any
    failure is a silent no-op (ledger writes must never break routing)."""
    try:
        if lane not in VALID_LANES:
            return False
        it = int(input_tokens) if isinstance(input_tokens, (int, float)) else None
        ot = int(output_tokens) if isinstance(output_tokens, (int, float)) else None
        if it is None and ot is None:
            return False  # provider omitted usage: record nothing, never estimate
        rec = {
            "ts": round(time.time(), 3),
            "lane": str(lane),
            "model": str(model or "")[:120],
            "session_id": str(session_id or ""),
            "input_tokens": max(0, it or 0),
            "output_tokens": max(0, ot or 0),
            "est_cost_usd": round(max(0.0, float(est_cost or 0.0)), 6),
            "detail": str(detail or "")[:60],
        }
        if task_id:
            rec["task_id"] = str(task_id)[:40]
        if event_seq is not None:
            try:
                rec["event_seq"] = int(event_seq)
            except (TypeError, ValueError):
                pass
        path = _store_path()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with _lock:
            _maybe_rotate_locked(path)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return True
    except Exception as exc:  # noqa: BLE001 - persistence must never break a route
        logger.debug("tokens ledger write failed: %s", exc)
        return False


# Public-reference price estimates (USD per 1M tokens) for render-lane
# models that lack user-configured pricing. OpenRouter/list rates 2026-09;
# override via config anchor_chain.pricing when the operator knows better.
_RENDER_PRICE_ESTIMATES: Dict[str, Dict[str, float]] = {
    "qwen-3-8-27b": {"input_per_1m": 0.18, "output_per_1m": 2.22},
    "abliterated-model-large-v2": {"input_per_1m": 0.20, "output_per_1m": 0.80},
}


def estimate_cost(model: str, input_tokens: Optional[int],
                  output_tokens: Optional[int]) -> float:
    """Estimate cost from the anchor_chain pricing table (the plugin's only
    price source). Unknown/unpriced model -> 0.0 — no fabricated pricing.
    Never raises."""
    try:
        from . import anchor_chain

        pricing = anchor_chain.load_anchor_chain().pricing
        prices = pricing.get(str(model or "")) if isinstance(pricing, dict) else None
        if not isinstance(prices, dict):
            # Goran 09-09: render-lane models (abliteration.ai, venice) have
            # no user-configured pricing — use the public-reference ESTIMATE
            # table below so banners show real consumption instead of fake
            # $0.0000. These are public list prices, marked approximate in
            # the banner via the ledger detail; config anchor_chain.pricing
            # overrides any entry.
            ref = _RENDER_PRICE_ESTIMATES.get(str(model or ""))
            if isinstance(ref, dict):
                prices = ref
            else:
                return 0.0
        it = max(0, int(input_tokens or 0))
        ot = max(0, int(output_tokens or 0))
        cost = (it / 1_000_000.0) * float(prices.get("input_per_1m", 0.0) or 0.0)
        cost += (ot / 1_000_000.0) * float(prices.get("output_per_1m", 0.0) or 0.0)
        return round(max(0.0, cost), 6)
    except Exception:  # noqa: BLE001
        return 0.0


def read_records(max_lines: int = _SCAN_LINES) -> List[Dict[str, Any]]:
    """Bounded read of the ledger (last max_lines records), newest last.
    Corrupt/torn lines are skipped. Never raises."""
    out: List[Dict[str, Any]] = []
    try:
        path = _store_path()
        if not os.path.exists(path):
            return out
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-max(1, int(max_lines)):]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("tokens ledger read failed: %s", exc)
        return out


def aggregate(records: Optional[List[Dict[str, Any]]] = None, *,
              since_ts: float = 0.0, session_id: str = "") -> Dict[str, Any]:
    """Aggregate ledger records into per-lane totals. Windows: since_ts>0
    filters by record ts; session_id filters per-session (empty = all).
    Returns {lane: {calls, input_tokens, output_tokens, est_cost_usd,
    calls_missing_usage}}. Never raises."""
    try:
        recs = records if records is not None else read_records()
        lanes: Dict[str, Dict[str, Any]] = {}
        for rec in recs:
            lane = str(rec.get("lane") or "")
            if lane not in VALID_LANES:
                continue
            ts = rec.get("ts")
            if since_ts and isinstance(ts, (int, float)) and ts < since_ts:
                continue
            sid = str(rec.get("session_id") or "")
            if session_id and sid != session_id:
                continue
            bucket = lanes.setdefault(lane, {
                "calls": 0, "input_tokens": 0, "output_tokens": 0,
                "est_cost_usd": 0.0, "calls_missing_usage": 0,
            })
            bucket["calls"] = int(bucket["calls"]) + 1
            bucket["input_tokens"] = int(bucket["input_tokens"]) + int(rec.get("input_tokens") or 0)
            bucket["output_tokens"] = int(bucket["output_tokens"]) + int(rec.get("output_tokens") or 0)
            bucket["est_cost_usd"] = round(float(bucket["est_cost_usd"]) + float(rec.get("est_cost_usd") or 0.0), 6)
        return lanes
    except Exception as exc:  # noqa: BLE001
        logger.debug("tokens ledger aggregate failed: %s", exc)
        return {}


def _test_reset() -> None:
    """Tests-only hook: the ledger path is monkeypatched via _store_path in
    tests; nothing in-process to reset (no caches)."""
    return None