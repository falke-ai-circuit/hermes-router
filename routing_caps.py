"""Per-agent routing caps + initiator-tagged ledger + restart-durable state
(Phase 1, leg 2 — BLUEPRINT-request-routing-2026-09-12, reviewer H1/H7.1/
H7.3, Goran amendment: denial is NEVER silent).

Scope (leg 2):
  1. Per-agent daily spend cap knob `routing_daily_cap_usd` — config-live
     read (no bounce), default = the existing anchor-chain daily cap value.
     Applies to EVERY lane at gate step 1 INCLUDING shadow (the shadow lane
     had ZERO cap checks before this module — H1).
  2. Restart-durable state (H7.3): per-agent cap counters AND cooldown
     timestamps live in a JSON sidecar (hermes-router-routing-state.json,
     profile home) — gateway bounces must not reset them. Written through
     the same atomic temp+replace discipline as anchor_chain's spend ledger.
  3. Ledger initiator tag: consult spend records carry
     `initiator: "user"|"agent"` + `lane` (additive JSON fields — existing
     readers ignore unknown keys). usage_ledger.record_tokens gains the
     additive `initiator` kwarg (None = field omitted, byte-identical
     records for legacy call sites).
  4. Cap-denied routing emits a VISIBLE delivery banner
     ("routing denied: daily spend cap reached ($X/$Y) — continuing
     un-routed") parked for this turn's delivery + a `denied_cap` ledger
     event with the initiator tag. Never silent (Goran amendment).

Boundary semantics (defined, tested):
  - A consult is ALLOWED when today's per-agent spend plus its projected
    cost is EXACTLY the cap (projected <= cap) — the in-flight consult that
    lands exactly at $cap/day records and completes.
  - A NEW consult when spend is already AT the cap is DENIED (any additional
    spend would exceed; shadow uses projected = 0, so spend >= cap denies).
  - Denial NEVER mutates counters (H7.1: counters increment only on
    successful claim — record_agent_spend is called post-claim only).

Fail-open contract: every read returns safe defaults on any error; a cap
check failure must never block a turn. Never raises.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

STATE_FILENAME = "hermes-router-routing-state.json"

# Test seam: tests point this at a tmp file (mirrors anchor_chain._LEDGER_PATH_OVERRIDE).
_STATE_PATH_OVERRIDE: Optional[str] = None

_LOCK = threading.Lock()

INITIATOR_USER = "user"
INITIATOR_AGENT = "agent"

# Gate step-1 check is a spend-floor check (per-call projected cost is only
# known at the execution seam, which keeps its own frozen cap_check).
_GATE_EST_COST = 0.0


# ---------------------------------------------------------------------------
# Sidecar (restart-durable; H7.3)
# ---------------------------------------------------------------------------


def _state_path() -> str:
    if _STATE_PATH_OVERRIDE:
        return _STATE_PATH_OVERRIDE
    try:
        import hermes_constants

        home = str(hermes_constants.get_hermes_home())
    except Exception:  # noqa: BLE001
        home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    return os.path.join(home, STATE_FILENAME)


def _today_utc() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_state() -> Dict[str, Any]:
    try:
        with open(_state_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — corrupt/missing sidecar = zero state
        return {}


def _write_state(data: Dict[str, Any]) -> None:
    try:
        path = _state_path()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception as exc:  # noqa: BLE001 — persistence must never break routing
        logger.debug("routing-state sidecar write failed: %s", exc)


def _agent_bucket(data: Dict[str, Any], agent_id: str) -> Dict[str, Any]:
    agents = data.get("agents")
    if not isinstance(agents, dict):
        agents = {}
        data["agents"] = agents
    bucket = agents.get(agent_id)
    if not isinstance(bucket, dict):
        bucket = {}
        agents[agent_id] = bucket
    return bucket


def _test_reset(path: Optional[str]) -> None:
    """Test hook — point the sidecar at a tmp file (or default) and clear."""
    global _STATE_PATH_OVERRIDE
    _STATE_PATH_OVERRIDE = path


# ---------------------------------------------------------------------------
# Config knob (config-live read — no bounce)
# ---------------------------------------------------------------------------


def routing_cap_usd() -> float:
    """Per-agent daily cap knob `routing_daily_cap_usd` (config-live).
    Default = the existing chain cap value (anchor_chain daily cap, itself
    knob-configurable; falls back to its DEFAULT constant). Never raises."""
    try:
        from . import config_access

        raw = config_access.router_section().get("routing_daily_cap_usd")
        if raw is not None:
            val = float(raw)
            if val >= 0.0:
                return val
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import anchor_chain

        chain = anchor_chain.load_anchor_chain()
        cap = float(getattr(chain, "daily_cap_usd", 0.0) or 0.0)
        if cap > 0.0:
            return cap
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import anchor_chain

        return float(anchor_chain.DEFAULT_DAILY_CAP_USD)
    except Exception:  # noqa: BLE001
        return 2.0


# ---------------------------------------------------------------------------
# Per-agent spend (restart-durable counters; H7.1 increment-on-claim only)
# ---------------------------------------------------------------------------


def agent_spend(agent_id: str) -> float:
    """Today's persisted per-agent spend (UTC date key). Never raises."""
    try:
        with _LOCK:
            data = _load_state()
            bucket = _agent_bucket(data, str(agent_id or ""))
            today = bucket.get(_today_utc())
            if isinstance(today, dict):
                return float(today.get("spend_usd", 0.0) or 0.0)
        return 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def record_agent_spend(agent_id: str, amount_usd: float,
                       initiator: str = INITIATOR_AGENT,
                       lane: str = "") -> float:
    """Add cost to the agent's TODAY entry, return the new daily total.
    Entries carry additive `initiator` + `lane` fields (H1 tagging).
    Call ONLY on a successful claim (H7.1). Never raises."""
    try:
        amt = max(0.0, float(amount_usd or 0.0))
        with _LOCK:
            data = _load_state()
            bucket = _agent_bucket(data, str(agent_id or ""))
            key = _today_utc()
            rec = bucket.get(key)
            new = (float(rec.get("spend_usd", 0.0) or 0.0) if isinstance(rec, dict) else 0.0) + amt
            entry: Dict[str, Any] = {"spend_usd": round(new, 6),
                                     "updated_at": time.time()}
            # Preserve the previous initiator/lane when this write is an
            # accumulator continuation without fresh tagging info.
            if isinstance(rec, dict):
                for f in ("initiator", "lane"):
                    if rec.get(f):
                        entry[f] = rec[f]
            if initiator:
                entry["initiator"] = str(initiator)
            if lane:
                entry["lane"] = str(lane)
            bucket[key] = entry
            _prune_bucket(bucket)
            data.setdefault("schema", 1)
            _write_state(data)
            return round(new, 6)
    except Exception:  # noqa: BLE001
        return agent_spend(agent_id)


def _prune_bucket(bucket: Dict[str, Any]) -> None:
    """Keep 7 date keys so the sidecar stays tiny (same discipline as the
    chain spend ledger)."""
    try:
        keys = sorted(k for k in bucket
                      if isinstance(k, str) and len(k) == 10 and k[4] == "-")
        for k in keys[:-7]:
            bucket.pop(k, None)
    except Exception:  # noqa: BLE001
        pass


def gate_cap_check(agent_id: str, est_cost: float = _GATE_EST_COST
                   ) -> Tuple[bool, float, float]:
    """Gate step-1 cap check — (allowed, spend_now, cap). Applies to EVERY
    lane including shadow. Allowed when projected (spend + est_cost) is at
    MOST the cap — exactly-at-cap consults pass; anything beyond denies.
    Fail-open: a broken check ALLOWS (the execution seam's own frozen
    cap_check remains the hard guard). Never raises."""
    try:
        cap = routing_cap_usd()
        spend = agent_spend(agent_id)
        projected = spend + max(0.0, float(est_cost or 0.0))
        return (projected <= cap, spend, cap)
    except Exception:  # noqa: BLE001
        return True, 0.0, routing_cap_usd()


# ---------------------------------------------------------------------------
# Restart-durable cooldown timestamps (H7.3)
# ---------------------------------------------------------------------------


def record_cooldown(agent_id: str, ts: Optional[float] = None) -> None:
    """Persist a consult-claim timestamp for the agent (mirror of
    state.record_staged_consult — that one is in-process and a gateway
    bounce silently drops it; this one survives). Never raises."""
    try:
        with _LOCK:
            data = _load_state()
            cooldowns = data.get("cooldowns")
            if not isinstance(cooldowns, dict):
                cooldowns = {}
                data["cooldowns"] = cooldowns
            cooldowns[str(agent_id or "")] = float(ts if ts is not None else time.time())
            data.setdefault("schema", 1)
            _write_state(data)
    except Exception:  # noqa: BLE001
        pass


def last_cooldown(agent_id: str) -> float:
    """Persisted last-consult timestamp for the agent (0.0 = none). Never
    raises."""
    try:
        with _LOCK:
            data = _load_state()
            cooldowns = data.get("cooldowns")
            if isinstance(cooldowns, dict):
                val = cooldowns.get(str(agent_id or ""))
                if isinstance(val, (int, float)):
                    return float(val)
        return 0.0
    except Exception:  # noqa: BLE001
        return 0.0


# ---------------------------------------------------------------------------
# Cap-denied routing — visible banner + denied_cap ledger event (Goran
# amendment: NEVER silent)
# ---------------------------------------------------------------------------

DENIED_BANNER_TEMPLATE = ("routing denied: daily spend cap reached "
                          "($%.2f/$%.2f) — continuing un-routed")


def denied_banner_text(spend: float, cap: float) -> str:
    """The exact visible denial banner text. Never raises."""
    try:
        return DENIED_BANNER_TEMPLATE % (float(spend or 0.0), float(cap or 0.0))
    except Exception:  # noqa: BLE001
        return "routing denied: daily spend cap reached — continuing un-routed"


def record_denied_cap(agent_id: str, lane: str, initiator: str,
                      spend: float, cap: float) -> None:
    """Append a `denied_cap` event to the sidecar ledger with the initiator
    tag. Never raises."""
    try:
        with _LOCK:
            data = _load_state()
            events = data.get("events")
            if not isinstance(events, list):
                events = []
                data["events"] = events
            events.append({
                "event": "denied_cap",
                "ts": round(time.time(), 3),
                "agent_id": str(agent_id or ""),
                "lane": str(lane or ""),
                "initiator": str(initiator or INITIATOR_AGENT),
                "spend_usd": round(max(0.0, float(spend or 0.0)), 6),
                "cap_usd": round(max(0.0, float(cap or 0.0)), 6),
            })
            # Bounded: keep the newest 200 events.
            del events[:-200]
            data.setdefault("schema", 1)
            _write_state(data)
    except Exception:  # noqa: BLE001
        pass


def read_denied_events(limit: int = 50) -> list:
    """Newest-first denied_cap events (observability / battery). Never raises."""
    try:
        with _LOCK:
            data = _load_state()
            events = data.get("events")
            if isinstance(events, list):
                return list(reversed(events[-max(0, int(limit)):]))
        return []
    except Exception:  # noqa: BLE001
        return []


def deny_routing(agent_id: str, lane: str, initiator: str,
                 session_id: str) -> Tuple[bool, float, float]:
    """One call for the gate's denial path: ledger event + VISIBLE banner
    parked for this turn's delivery. Returns (False, spend, cap) so the
    caller can return a no-route decision in the same expression. The park
    is best-effort — a banner failure never blocks the (already denied)
    turn. Never raises."""
    try:
        cap = routing_cap_usd()
    except Exception:  # noqa: BLE001
        cap = 0.0
    spend = agent_spend(agent_id)
    try:
        record_denied_cap(agent_id, lane, initiator, spend, cap)
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import debug_banner as _db

        _db.park_anchor_banner(str(session_id or ""),
                               denied_banner_text(spend, cap))
    except Exception:  # noqa: BLE001 — banner must never break the gate
        pass
    try:
        from . import router_tools

        router_tools.count("denied_cap")
    except Exception:  # noqa: BLE001
        pass
    return False, spend, cap
