"""bypass_watch — R11 anti-bypass observability (Goran 09-17, observability
FIRST: never blocks, never rewrites, never gates a tool call).

The 2026-09-17 operative incident: with no lane fired, the agent grepped
NOUS_API_KEY out of the container env and hit the provider chat-completions
endpoint DIRECTLY via execute_code (~$0.0115 unrouted, uncapped,
unbannered). Route log showed only intent_none. The detection gap is
fixed by the R11 declared frontier family (route_gate 4.3.0); THIS module
adds the detectable signal for the bypass class itself:

  - CAPTURE (PRE, per provider-call fire): scan the request's messages for
    tool-call arguments / tool-result content naming a known provider
    chat-completions host. Content-FREE: only the host names are kept,
    keyed by (session, turn id) — payload text is never stored.
  - AUDIT (POST, turn close): when hosts were captured this turn AND no
    corresponding route exists FOR THAT TURN (turn-claim registry first,
    then the session's render/anchor ledger records within the turn
    window — the conductor's anti-FP rule: a routed turn's own banner/
    audit machinery calls the SAME hosts and must never false-positive),
    log ONE content-free event:

      provider_direct_call_unrouted  host=<hosts> session_id=<sid>

  Fail-open everywhere; deduped once per turn; never raises.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)

# Known provider chat-completions hosts (mandatory set, Goran 09-17).
# Substring match on tool-call argument / tool-result text. Parent-domain
# forms are covered by the same substrings (any chat-completions-shaped
# URL under these parents contains at least one).
PROVIDER_HOSTS = (
    "inference-api.nousresearch.com",
    "api.abliteration.ai",
    "abliteration.ai",
    "api.venice.ai",
    "venice.ai",
    "openrouter.ai",
)

# R34-4: hosts this PROFILE legitimately talks to. The main model is served
# by a custom provider (live: custom:nousinf) and the router's own lanes hit
# provider endpoints on every routed turn; scanning tool surfaces for those
# hosts produced 84+ false "direct provider call detected, unrouted" banners
# fleet-wide (orchestrator 43, operative 17, conductor 9, shadow 11,
# analyst 4). EXPECTED hosts are silent; detection stays intact for
# genuinely foreign provider hosts. Never blocks anything.
_EXPECTED_HOST_CACHE: Dict[str, Any] = {"at": 0.0, "hosts": set()}
_EXPECTED_TTL_S = 300.0


def _host_of(url: str) -> str:
    """Hostname of a URL ('' when unparseable / not a URL). Never raises."""
    try:
        from urllib.parse import urlparse

        raw = str(url or "").strip()
        if not raw:
            return ""
        if "://" not in raw:
            raw = "https://" + raw
        return str(urlparse(raw).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def _expected_hosts() -> Set[str]:
    """Hosts that are NORMAL OPERATION for this profile: the configured
    main-model provider base_url(s) and the router's own lane endpoints
    (anchor primary/judge). Cached briefly. Never raises."""
    try:
        now = time.time()
        if (_EXPECTED_HOST_CACHE.get("hosts")
                and now - float(_EXPECTED_HOST_CACHE.get("at") or 0.0)
                < _EXPECTED_TTL_S):
            return set(_EXPECTED_HOST_CACHE["hosts"])
        hosts: Set[str] = set()
        # 1. the router's own lane endpoints
        try:
            from . import anchor_chain

            chain = anchor_chain.load_anchor_chain()
            for ep in (getattr(chain, "primary", None),
                       getattr(chain, "judge", None)):
                h = _host_of(str(getattr(ep, "base_url", "") or ""))
                if h:
                    hosts.add(h)
        except Exception:  # noqa: BLE001
            pass
        # 2. the profile's configured main-model provider(s)
        try:
            from . import config_access

            for _name, block in (config_access.providers_custom() or {}).items():
                if not isinstance(block, dict):
                    continue
                h = _host_of(str(block.get("base_url") or ""))
                if h:
                    hosts.add(h)
        except Exception:  # noqa: BLE001
            pass
        _EXPECTED_HOST_CACHE["hosts"] = set(hosts)
        _EXPECTED_HOST_CACHE["at"] = now
        return hosts
    except Exception:  # noqa: BLE001
        return set()


def _foreign_hosts(hosts: List[str]) -> List[str]:
    """Captured hosts MINUS this profile's expected provider/lane hosts.
    An expected parent (e.g. nousresearch.com) also covers subdomains.
    Never raises."""
    try:
        expected = _expected_hosts()
        if not expected:
            return list(hosts)
        out = []
        for h in hosts:
            hh = str(h or "").lower()
            if hh in expected:
                continue
            if any(hh.endswith("." + e) or e.endswith("." + hh)
                   for e in expected if e):
                continue
            out.append(h)
        return out
    except Exception:  # noqa: BLE001
        return list(hosts)


_EVENT_DETAIL = "provider_direct_call_unrouted"
# R15 LEG 2 (Goran 09-21): one-line visibility banner appended to the
# DELIVERED turn when the audit fires. Enforcement stays Goran's call —
# this is provenance, not a block.
UNROUTED_BANNER = "router: direct provider call detected, unrouted"
# Turn window: audit state older than this is GC'd (a turn's tool bursts
# complete well inside it; the POST fire is the same turn's close).
_TURN_TTL_SECONDS = 1800.0
# Ledger fallback window: a render/anchor ledger record for this session
# newer than the turn's first capture counts as 'routed this turn'.

_LOCK = threading.Lock()
# session_id -> list of turn records {"hosts": Set[str], "start": float,
# "audited": bool}. A list (not a single record) because the turn id can
# ROTATE mid-turn (middleware last-seen pass) — capture records are keyed
# at capture time and the audit closes every unaudited record for the
# session, so rotation never orphans a captured turn.
_TURNS: Dict[str, List[Dict[str, Any]]] = {}


def _session_key(session_id: str) -> str:
    return str(session_id or "")


def _scan_text_for_hosts(text: str) -> Set[str]:
    """Distinct known-provider hosts present in `text`, canonicalized to
    the longest matching form per site (an 'api.' URL also contains the
    bare parent-domain substring — report one host per site). Never
    raises."""
    try:
        low = str(text or "").lower()
        hits = {h for h in PROVIDER_HOSTS if h in low}
        # drop bare forms subsumed by a longer matched form
        return {h for h in hits
                if not any(h != o and h in o for o in hits)}
    except Exception:  # noqa: BLE001
        return set()


def _hosts_in_request(request: Any) -> Set[str]:
    """Scan the request's messages for provider hosts in assistant
    tool-call arguments and tool-result content. Content-FREE result:
    host names only. Never raises."""
    hosts: Set[str] = set()
    try:
        msgs = (request or {}).get("messages")
        if not isinstance(msgs, list):
            return hosts
        for m in msgs:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "")
            if role == "assistant":
                calls = m.get("tool_calls")
                if isinstance(calls, list):
                    for c in calls:
                        if not isinstance(c, dict):
                            continue
                        fn = c.get("function") or {}
                        args = fn.get("arguments")
                        if isinstance(args, str):
                            hosts |= _scan_text_for_hosts(args)
                        elif isinstance(args, dict):
                            hosts |= _scan_text_for_hosts(
                                str(args.get("code") or args.get("command")
                                    or args))
            elif role == "tool":
                hosts |= _scan_text_for_hosts(str(m.get("content") or ""))
    except Exception:  # noqa: BLE001
        return hosts
    return hosts


def capture_from_request(request: Any, session_id: str) -> None:
    """PRE-side capture: record provider hosts seen in this turn's tool
    surface. Called from on_llm_request beside the tool-result tap. Costs
    one dict lookup on turns with no tool messages. Never raises."""
    try:
        hosts = _hosts_in_request(request)
        if not hosts:
            return
        now = time.time()
        with _LOCK:
            _gc_locked(now)
            records = _TURNS.setdefault(_session_key(session_id), [])
            # merge into the current turn record; start a new one when the
            # last record was already audited (turn id rotated past it)
            rec = records[-1] if records and not records[-1].get("audited") \
                else None
            if rec is None:
                rec = {"hosts": set(), "start": now, "audited": False}
                records.append(rec)
            rec["hosts"] |= hosts
    except Exception:  # noqa: BLE001 — capture must never break the turn
        logger.debug("bypass_watch capture error", exc_info=True)


def _routed_this_turn(session_id: str, turn_start: float) -> bool:
    """True when THIS turn produced a route: the turn-claim registry
    first (any lane/source stamped by claim_pass), then the session's
    render/anchor ledger records within the turn window (covers claims
    already TTL-evicted). Session-scoped + window-scoped so a routed
    turn's own machinery never false-positives a LATER bypass turn.
    Never raises."""
    try:
        from . import route_gate

        claim = route_gate.claim_state(str(session_id or ""))
        if claim is not None:
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import usage_ledger

        cutoff = turn_start - 5.0  # small clock-skew tolerance
        for rec in usage_ledger.read_records():
            if str(rec.get("session_id") or "") != str(session_id or ""):
                continue
            if str(rec.get("lane") or "") not in ("render", "anchor"):
                continue
            ts = rec.get("ts")
            if isinstance(ts, (int, float)) and float(ts) >= cutoff:
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _gc_locked(now: float) -> None:
    for sid in list(_TURNS.keys()):
        records = _TURNS[sid]
        keep = [r for r in records
                if now - float(r.get("start") or 0.0) <= _TURN_TTL_SECONDS]
        if keep:
            _TURNS[sid] = keep
        else:
            _TURNS.pop(sid, None)


def audit_turn(session_id: str, log_route: Any) -> str:
    """POST-side audit: one content-free provider_direct_call_unrouted
    event per turn when provider hosts were used with no route. Closes
    every unaudited capture record for the session (turn id may have
    rotated mid-turn).

    R15 LEG 2 (Goran 09-21): escalation from log-only to a one-line banner
    append on the DELIVERED turn — "router: direct provider call detected,
    unrouted". VISIBILITY ONLY: no enforcement, no blocking, no rewrite of
    the turn's content beyond the appended line. Returns the banner string
    ('' when nothing fired); the caller appends it to the delivered text.
    Never raises; never blocks."""
    banner = ""
    try:
        sid = _session_key(session_id)
        with _LOCK:
            _gc_locked(time.time())
            records = _TURNS.get(sid) or []
            if not records:
                return ""
            pending = [r for r in records if not r.get("audited")]
            for r in records:
                r["audited"] = True
        hosts: List[str] = []
        start = 0.0
        for r in pending:
            hosts = sorted(set(hosts) | (r.get("hosts") or set()))
            start = float(r.get("start") or 0.0) if not start else start
        if not hosts:
            return ""
        # R34-4: the profile's own main-model provider and the router's lane
        # endpoints are expected traffic, not a bypass. Filtering happens
        # BEFORE the route check so an expected host never even reaches the
        # audit decision; foreign hosts keep the original behavior.
        _foreign = _foreign_hosts(hosts)
        if not _foreign:
            return ""
        hosts = _foreign
        if _routed_this_turn(session_id, start):
            return ""
        banner = UNROUTED_BANNER
        try:
            log_route("POST", event_detail=_EVENT_DETAIL,
                      host=",".join(hosts), session_id=sid)
        except Exception:  # noqa: BLE001 — observability only
            pass
        return banner
    except Exception:  # noqa: BLE001
        logger.debug("bypass_watch audit error", exc_info=True)
        return banner


def reset() -> None:
    """Test seam."""
    with _LOCK:
        _TURNS.clear()
