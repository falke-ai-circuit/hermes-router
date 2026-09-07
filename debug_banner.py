"""v3.6.0 debug banner — §10.2 + §10.4 F/G/H (Goran-direct 09-07).

One formatter for ALL lanes. When the `debug_banner` knob is ON, every actual
frontier/uncensored LLM call that lands in the turn appends a compact,
delimited, NON-AUTHORITATIVE diagnostic banner to the DELIVERED
representation of the response:

    ━━ DEBUG ROUTE BANNER ━━
    lane: uncensored-render | trigger: ied_construction | model: qwen @ venice
    tokens: in=5088 out=1255 | est_cost: $0.0004 | latency: 41.3s | retries: 0
    ━━━━━━━━━━━━━━━━━━━━━━

TWO-REPRESENTATION RULE (§10.4-F, binding): delivery_content =
canonical_content + banner at the transport edge ONLY. The canonical content
(what history / model context / state.db persist via rewrite_persisted_turn)
NEVER contains a banner — model context always re-reads canonical. Callers
append the banner AFTER the canonical artifacts (inbox record, canonical
commit, persisted-turn rewrite) are already written from canonical text.

Discipline:
- Data = the v3.5.0 tokens-ledger tap values passed IN-PROCESS (no ledger
  re-read); latency = call duration; cost = usage_ledger.estimate_cost when
  the caller has no measured cost.
- Length-capped (~400 chars): an oversized diagnostic is OMITTED ENTIRELY —
  never the answer truncated (§10.4-F).
- Redacted: the formatter renders enum names / model ids / integers ONLY.
  Raw prompts, outputs, and exceptions never enter a banner (§10.2).
- NEVER banner: aux stage-2 classification calls, the main flash model,
  cap_blocked/skipped calls (no LLM content landed).
- Failure isolation (§10.4-H): the ONLY code inside the boundary is the
  banner build itself. Any error -> log `debug_banner_failed`, deliver
  canonical without banner. Never fails the request, never retries, no
  notification loop.
- Correlation (§10.4-E): banner records carry task_id + a monotonic event
  seq from suggestions.next_event_seq().

Fire points: uncensored render (PRE lane success), frontier anchor
(maybe_execute_anchored success), and — wired but inert until v3.6 gates go
live — PRE/MID/POST consult envelopes (same formatter). Default OFF: when
the knob is off, this module contributes ZERO calls in the delivery path
(callers gate on debug_banner_enabled() first — config lookup only).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

BANNER_HEAD = "· router ·"
BANNER_TAIL = "·"
MAX_BANNER_CHARS = 400          # oversized diagnostic -> omit entirely
VALID_LANES = ("uncensored-render", "uncensored-post", "frontier-anchor",
               "consult-pre", "consult-mid", "consult-post")

# Lanes that must never banner (defense-in-depth — callers also gate):
FORBIDDEN_LANES = ("aux", "aux-classify", "flash", "cap_blocked", "skipped")


def _banner_section() -> Dict[str, Any]:
    """Dual-section config read (hermes_router preferred). Never raises."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = None
        if isinstance(cfg, dict):
            section = cfg.get("hermes_router")
            if not (isinstance(section, dict) and section):
                section = cfg.get("uncensored_router")
        return section if isinstance(section, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def debug_banner_level() -> int:
    """debug_banner verbosity: 0=off, 1=one-liner, 2=+context, 3=maximum.
    Legacy bool values: true->1, false->0. One config lookup. Never raises."""
    try:
        raw = _banner_section().get("debug_banner", False)
        if isinstance(raw, bool):
            return 1 if raw else 0
        if isinstance(raw, (int, float)):
            return max(0, min(3, int(raw)))
        s = str(raw).strip().lower()
        if s in ("true", "on", "yes"):
            return 1
        if s in ("false", "off", "no", ""):
            return 0
        return max(0, min(3, int(float(s))))
    except Exception:  # noqa: BLE001
        return 0


def debug_banner_enabled() -> bool:
    """Kept for the fire-point gate: any level >=1 enables the banner."""
    return debug_banner_level() >= 1


def format_banner(lane: str, trigger: str, model: str, endpoint: str,
                  tokens_in: Optional[int], tokens_out: Optional[int],
                  est_cost: Optional[float], latency_s: Optional[float],
                  retries: int = 0, level: Optional[int] = None,
                  task_id: str = "", session_id: str = "", gate: str = "",
                  route_id: str = "") -> str:
    """Render the banner text. Pure string shaping — no I/O, no state.
    Returns "" when the caller should omit the banner (oversized or invalid
    lane). Never raises."""
    try:
        lane = str(lane or "")
        if lane in FORBIDDEN_LANES or lane not in VALID_LANES:
            return ""
        ti = int(tokens_in) if isinstance(tokens_in, (int, float)) else 0
        to = int(tokens_out) if isinstance(tokens_out, (int, float)) else 0
        cost = max(0.0, float(est_cost or 0.0))
        lat = max(0.0, float(latency_s or 0.0))
        ret = max(0, int(retries or 0))
        # Redaction surface: only enums, model ids, host-only endpoints,
        # integers, and a bounded cost figure ever render. The model and
        # endpoint strings are scheme-validated upstream; slice defensively
        # regardless — no user content can ride in through these fields.
        model_s = str(model or "?")[:120]
        ep_s = str(endpoint or "?")
        if "://" in ep_s:
            ep_s = ep_s.split("://", 1)[1].split("/", 1)[0]  # host only
        ep_s = ep_s[:120]
        trig_s = str(trigger or "none")[:120]
        lvl = debug_banner_level() if level is None else max(0, min(3, int(level or 0)))
        lat_s = (" | %ds" % int(lat)) if lat >= 1 else ""
        # L1 (default): compact one-liner (Goran 09-07).
        banner = (
            "%s %s | %s | %s @ %s | tok %d/%d | $%.6f%s" % (
                BANNER_HEAD, lane.split("-", 1)[0], trig_s, model_s, ep_s,
                ti, to, cost, lat_s)
        )
        if lvl >= 2:
            ctx = ("task %s" % str(task_id or "-")[:40]) + (
                " | sess %s" % str(session_id or "-")[:36] if session_id else "") + (
                " | gate %s" % str(gate or "-")[:20] if gate else "") + (
                " | ret %d" % ret if ret else "")
            banner += "\n" + ctx
        if lvl >= 3:
            banner += "\nfull: lane=%s | trigger=%s | model=%s | endpoint=%s | tokens_in=%d tokens_out=%d | est_cost=%.6f | latency=%.2fs | retries=%d%s" % (
                lane, trig_s, str(model or "?")[:120], str(endpoint or "?")[:120],
                ti, to, cost, lat, ret,
                (" | route_id=%s" % str(route_id or "-")[:40]) if route_id else "")
        if len(banner) > MAX_BANNER_CHARS:
            return ""  # oversized diagnostic: omit entirely, never truncate the answer
        return banner
    except Exception:  # noqa: BLE001 — §10.4-H failure isolation
        return ""


def build_banner_record(lane: str, task_id: str, **fields: Any) -> Dict[str, Any]:
    """Route-log record for a banner append: task_id + monotonic event seq
    (§10.4-E) + the same bounded fields the banner rendered. Never raises."""
    try:
        from . import suggestions

        rec: Dict[str, Any] = {
            "event_detail": "debug_banner",
            "lane": str(lane or "")[:40],
            "task_id": str(task_id or "")[:40],
            "event_seq": suggestions.next_event_seq(),
        }
        for k in ("trigger", "model", "tokens_in", "tokens_out", "est_cost",
                  "latency_s", "retries", "session_id", "gate", "route_id"):
            if k in fields and fields[k] is not None:
                v = fields[k]
                if isinstance(v, float):
                    rec[k] = round(v, 6)
                elif isinstance(v, int):
                    rec[k] = v
                else:
                    rec[k] = str(v)[:60]
        return rec
    except Exception:  # noqa: BLE001
        return {}


def append_banner(delivery_text: str, banner_text: str, *, prepend: bool = False,
                  _knob_checked: bool = False) -> str:
    """THE narrow §10.4-H boundary. Returns the DELIVERY representation:
    canonical + banner (banner below the render header when prepend, else
    appended). Gated on debug_banner_enabled() (one config lookup — the only
    runtime cost when OFF; call sites that already checked pass
    _knob_checked=True to avoid a second read). ANY exception inside is
    caught here -> canonical text returned unchanged + `debug_banner_failed`
    logged. Never raises, never returns None, never truncates delivery_text."""
    try:
        if not _knob_checked and not debug_banner_enabled():
            return delivery_text if isinstance(delivery_text, str) else ""
        base = delivery_text if isinstance(delivery_text, str) else ""
        banner = banner_text if isinstance(banner_text, str) else ""
        if not banner or not banner.strip():
            return base
        if not base.strip():
            return base  # empty canonical content — nothing to attach to
        return ("%s\n\n%s" % (banner, base)) if prepend else ("%s\n\n%s" % (base, banner))
    except Exception as exc:  # noqa: BLE001 — banner must never break delivery
        try:
            logger.error("debug_banner_failed detail=%.200s", str(exc))
        except Exception:  # noqa: BLE001 — even the failure log is best-effort
            pass
        return delivery_text if isinstance(delivery_text, str) else ""

# --- §10.4 anchor-banner delivery parking (one-shot per session) ---
_ANCHOR_BANNERS: Dict[str, str] = {}
_ANCHOR_BANNER_MAX = 32


def park_anchor_banner(session_id: str, banner_text: str) -> None:
    """Park an anchor banner for delivery on this session's next turn.
    Bounded map (32 sessions, FIFO eviction). Never raises."""
    try:
        if len(_ANCHOR_BANNERS) >= _ANCHOR_BANNER_MAX:
            _ANCHOR_BANNERS.pop(next(iter(_ANCHOR_BANNERS)), None)
        _ANCHOR_BANNERS[str(session_id or "")] = str(banner_text or "")[:MAX_BANNER_CHARS]
    except Exception:  # noqa: BLE001
        pass


def consume_parked_banner(session_id: str) -> str:
    """Return and clear the parked anchor banner for this session (or "")."""
    try:
        return _ANCHOR_BANNERS.pop(str(session_id or ""), "")
    except Exception:  # noqa: BLE001
        return ""
