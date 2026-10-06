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
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

BANNER_HEAD = "· router ·"
BANNER_TAIL = "·"
MAX_BANNER_CHARS = 700          # R19.15: 400 -> 480; D1 v1.1: 480 -> 700 (the
                                # impulse frame with weights + evidence + band
                                # + ADVICE-ONLY caveat runs ~500-560c — the cap
                                # must keep the provenance banner line intact,
                                # never truncate the frame's banner off)
                                # adds ~50c over the bare opt-N id; oversized
                                # diagnostics are still omitted entirely)
VALID_LANES = ("uncensored-render", "uncensored-post", "frontier-anchor",
               "consult-pre", "consult-mid", "consult-post",
               "shadow",  # leg 11: declared shadow lane banner
               "decision",  # R19 v3: decision lane provenance banner
               "reflex")  # R19.13 reflex modularization: new lane id, same label

# Lanes that must never banner (defense-in-depth — callers also gate):
FORBIDDEN_LANES = ("aux", "aux-classify", "flash", "cap_blocked", "skipped")


def _banner_section() -> Dict[str, Any]:
    """Dual-section config read — delegates to config_access.router_section()
    (v3.8 step-2 consolidation). Kept as an alias: router_core imports this
    name directly. Never raises."""
    try:
        from . import config_access

        return config_access.router_section()
    except Exception:  # noqa: BLE001
        return {}


def debug_banner_level() -> int:
    """debug_banner verbosity: 0=off, 1=one-liner, 2=+context, 3=maximum.
    Legacy bool values: true->1, false->0. R8c (2026-09-13): the CODE
    default is now L1 (one-liner) so profiles with no explicit knob get the
    compact banner; an explicit `debug_banner: 0/false` still disables.
    One config lookup (via config_access). Never raises."""
    try:
        raw = _banner_section().get("debug_banner", 1)
        if isinstance(raw, bool):
            return 1 if raw else 0
        if isinstance(raw, (int, float)):
            return max(0, min(3, int(raw)))
        sv = str(raw).strip().lower()
        if sv in ("true", "on", "yes"):
            return 1
        if sv in ("false", "off", "no", ""):
            return 0
        return max(0, min(3, int(float(sv))))
    except Exception:  # noqa: BLE001
        return 0


def debug_banner_enabled() -> bool:
    """Kept for the fire-point gate: any level >=1 enables the banner."""
    lvl = debug_banner_level()
    return lvl >= 1


def format_banner(lane: str, trigger: str, model: str, endpoint: str,
                  tokens_in: Optional[int], tokens_out: Optional[int],
                  est_cost: Optional[float], latency_s: Optional[float],
                  retries: int = 0, level: Optional[int] = None,
                  task_id: str = "", session_id: str = "", gate: str = "",
                  route_id: str = "", initiator: str = "") -> str:
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
        # Goran 09-09 ruling: banners show REAL consumption. Providers whose
        # model is absent from the pricing table (abliteration.ai, venice)
        # record tokens but cost 0.0 — render "$0.0000*" so zero is never
        # presented as a true price. Priced calls render the real figure.
        if cost > 0.0:
            cost_s = "$%.6f" % cost
        elif ti > 0 or to > 0:
            cost_s = "$0.0000*"
        else:
            cost_s = "$0.0000"
        # Goran 09-14 framing: the uncensored/shadow lane is the agent's own
        # SHADOW SELF (part of her model, not an external route); the
        # frontier lane is her HIGHER SELF. Banner labels reflect identity,
        # not plumbing.
        # v1.1 addendum (SPEC-impulse-lane-v1.md): display label is
        # 'impulse (decision)' — the lane KEY stays 'decision', the
        # module name and PROVENANCE_TAG are unchanged.
        _lane_label = "higher-self (frontier)" if lane.startswith("frontier") else (
            "shadow-self (uncensored)" if lane in ("shadow", "uncensored", "uncensored-render")
            else "impulse (decision)" if lane in ("decision", "reflex")
            else lane.split("-", 1)[0])
        # R19 v3: an empty endpoint renders model-only (decision lane has no
        # remote host to name); initiator provenance rides the L1 line.
        model_part = ("%s @ %s" % (model_s, ep_s)) if ep_s else model_s
        banner = (
            "%s %s | %s | %s | tok %d/%d | %s%s" % (
                BANNER_HEAD, _lane_label, trig_s, model_part,
                ti, to, cost_s, lat_s)
        )
        if str(initiator or ""):
            banner += " | initiator=%s" % str(initiator)[:40]
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


def park_anchor_banner(session_id: str, banner_text: str,
                       task_id: str = "") -> None:
    """Park an anchor banner for delivery on this session's next turn.
    Bounded map (32 sessions, FIFO eviction). Never raises.
    R9d (Goran 09-14): one LLM call = exactly one banner. A re-park for the
    SAME task_id REPLACES (the call retried/re-emitted); a park for a NEW
    task_id accumulates.
    R19.16 FIX 4 (Goran addendum): ALL fired banners stack — the parked
    aggregate is ONE BLOCK with one segment per FIRED banner, in fire
    order (higher-self frontier + reflex decision segments coexist).
    Latest-wins starvation is gone: a midturn reflex verdict can no longer
    consume/replace the frontier banner slot (or vice versa). Identical
    re-parks dedupe; canonical delivery text is NEVER trimmed to make
    banner room (append_banner attaches the block; only the banner block
    itself is bounded)."""

    try:
        if len(_ANCHOR_BANNERS) >= _ANCHOR_BANNER_MAX:
            _ANCHOR_BANNERS.pop(next(iter(_ANCHOR_BANNERS)), None)
            _ANCHOR_TASKS.pop(next(iter(_ANCHOR_TASKS)), None)
        sid = str(session_id or "")
        seg = str(banner_text or "").strip()[:MAX_BANNER_CHARS]
        if not seg:
            return
        segs = list(_ANCHOR_SEGS.get(sid, []))
        tasks = list(_ANCHOR_TASKS.get(sid, []))
        if task_id and task_id in tasks:
            # R9d retry: replace THAT task's segment ATOMICALLY (fire order
            # kept) — a park's internal blank lines never split segments.
            idx = tasks.index(task_id)
            if idx < len(segs):
                segs[idx] = seg
            else:
                segs.append(seg)
        elif seg in segs:
            return  # identical re-park (same content) — dedupe
        else:
            segs.append(seg)
            tasks.append(str(task_id or ""))
        # bound: keep the most recent segments (banner side only — the
        # canonical delivery text is never trimmed)
        while len(segs) > _MAX_PARK_SEGMENTS:
            segs.pop(0)
            if tasks:
                tasks.pop(0)
        _ANCHOR_SEGS[sid] = segs
        _ANCHOR_TASKS[sid] = tasks
        _ANCHOR_BANNERS[sid] = "\n\n".join(segs)
        # R20-D3 (rider 20): schedule the one-shot bounded capture-fallback
        # watcher — if NO delivery edge consumes this parked banner within
        # banner_capture_fallback_wait, the worker captures it into the
        # persisted transcript itself (never a silently-orphaned billed
        # consult). Semaphored per session; never breaks the park.
        _maybe_schedule_capture_fallback(sid)
    except Exception:  # noqa: BLE001
        pass


_MAX_PARK_SEGMENTS = 4
_ANCHOR_TASKS: Dict[str, list] = {}
_ANCHOR_SEGS: Dict[str, list] = {}


def consume_parked_banner(session_id: str) -> str:
    """Return and clear the parked anchor banner for this session (or "").
    R19.16 FIX 4: clears the fire-order task ledger too."""
    try:
        sid = str(session_id or "")
        _ANCHOR_TASKS.pop(sid, None)
        _ANCHOR_SEGS.pop(sid, None)
        return _ANCHOR_BANNERS.pop(sid, "")
    except Exception:  # noqa: BLE001
        return ""


# --- R20-D3 (rider 20): one-shot bounded capture-fallback worker -----------
# Live evidence (valmet D3, 2026-10-06): the host plugin runner KILLED the
# transform_llm_output callback at its 30s budget ("timed out after 30s —
# skipping") and then SKIPPED every later invocation ("skipped after previous
# timeout or while still running") — the billed consult's banner was parked
# but NO delivery edge ever ran, so the banner was never delivered and the
# persisted transcript never carried it. The hook-side budgeting (audit_gate
# hook_budget, pre-consume waits) narrows the trip window; this worker closes
# the residual: a parked banner that survives its whole capture budget with
# no consumption by ANY delivery edge is captured into the persisted
# transcript directly (newest assistant row + banner block, exact-match
# canonical rewrite — router-substitution-only guard intact) so a billed
# consult's verdict is never silently orphaned. One-shot, semaphored per
# session, daemon thread, never breaks delivery, never raises.
_CAPTURE_FB_LOCK = threading.Lock()       # module lock: park/rewrite race
_CAPTURE_FB_INFLIGHT: set = set()         # per-session semaphore (one watcher)
_CAPTURE_FALLBACK_POLL_S = 1.0            # poll interval (spec: 1s)


def banner_capture_fallback_wait() -> float:
    """R20-D3: seconds the parked-banner capture-fallback watcher polls for
    consumption by a delivery edge before rewriting the persisted turn
    itself (knob banner_capture_fallback_wait, default 45s; 0 disables the
    fallback entirely). Never raises."""
    try:
        raw = _banner_section().get("banner_capture_fallback_wait", 45)
        return max(0.0, min(120.0, float(raw)))
    except Exception:  # noqa: BLE001
        return 45.0


def _newest_persisted_assistant_row(session_id: str) -> str:
    """R20-D3: newest persisted assistant row content for this session from
    state.db (the same store the gateway persists every turn to). Read-only;
    "" on any failure. Never raises."""
    try:
        import sqlite3

        from . import canonical as _canon

        db_path = _canon._state_db_path()
        if not db_path or not os.path.exists(db_path):
            return ""
        conn = sqlite3.connect(db_path, timeout=2.0)
        try:
            cur = conn.execute(
                "SELECT content FROM messages WHERE session_id = ?"
                " AND role = 'assistant' ORDER BY id DESC LIMIT 1",
                (str(session_id or ""),),
            )
            row = cur.fetchone()
            return str(row[0]) if row and row[0] is not None else ""
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — read-only probe, never raises
        return ""


def _capture_fallback_watch(session_id: str, captured: str) -> None:
    """R20-D3 worker body: after park, poll (1s interval) for consumption by
    any delivery edge; if still parked unconsumed at the budget, append the
    banner block to the sessions newest persisted assistant row and rewrite
    via canonical.rewrite_persisted_turn exact-match (the
    router-substitution-only guard is untouched — only the row whose content
    byte-equals the row we just read can match). The parked content is
    re-checked UNDER THE MODULE LOCK immediately before the rewrite, so a
    late live consume between the last poll and the lock wins (the rewrite
    is skipped, the slot belongs to the delivery edge). On no matching row:
    the banner is RE-PARKED (retained for the next delivery edge) and a
    fail-loud event is logged — never a silent orphan, never a loop crash.
    Never raises."""
    sid = str(session_id or "")
    try:
        budget = banner_capture_fallback_wait()
        if budget <= 0 or not str(captured or "").strip():
            return
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            time.sleep(_CAPTURE_FALLBACK_POLL_S)
            if _ANCHOR_BANNERS.get(sid, "") != captured:
                return  # a live delivery edge consumed/changed the slot
        with _CAPTURE_FB_LOCK:
            # re-check under the module lock: a late live consume wins
            if _ANCHOR_BANNERS.get(sid, "") != captured:
                return
            row = _newest_persisted_assistant_row(sid)
            delivered = ("%s\n\n%s" % (row, captured)) if row else ""
            ok = False
            if row:
                try:
                    from . import canonical as _canon

                    ok = _canon.rewrite_persisted_turn(sid, row, delivered)
                except Exception:  # noqa: BLE001 — must never break delivery
                    ok = False
            if ok:
                # captured: clear the parked slot (consume-equivalent, under
                # the lock so no racing edge double-delivers)
                _ANCHOR_TASKS.pop(sid, None)
                _ANCHOR_SEGS.pop(sid, None)
                _ANCHOR_BANNERS.pop(sid, None)
        if ok:
            try:
                from . import render_inbox as _ri

                _ri.record_render("PARKED_CAPTURE_FALLBACK", sid,
                                  len(row), delivered)
            except Exception:  # noqa: BLE001 — best-effort evidence
                pass
            try:
                from .route_gate import _pkg_fn

                _lrh = _pkg_fn("_log_route")
            except Exception:  # noqa: BLE001 — fallback to the owning module
                try:
                    from .dispatcher_knobs import _log_route as _lrh
                except Exception:  # noqa: BLE001 — fail-loud best-effort
                    _lrh = None
            if _lrh is not None:
                try:
                    _lrh("POST", event_detail="banner_capture_fallback",
                         outcome="captured", session_id=sid)
                    _lrh("POST", event_detail="banner_render_captured",
                         edge="capture_fallback", session_id=sid)
                except Exception:  # noqa: BLE001 — fail-loud best-effort
                    logger.exception("banner_capture_fallback event log failed")
        else:
            # no persisted assistant row matched: RE-PARK the banner
            # (retained for the next delivery edge) + fail-loud event.
            with _CAPTURE_FB_LOCK:
                _ANCHOR_BANNERS[sid] = captured
            try:
                logger.error(
                    "banner_capture_fallback_failed detail=no_row_match "
                    "session_id=%s — parked banner retained for the next "
                    "delivery edge", sid)
            except Exception:  # noqa: BLE001
                pass
            try:
                from .route_gate import _pkg_fn

                _lrh = _pkg_fn("_log_route")
            except Exception:  # noqa: BLE001 — fallback to the owning module
                try:
                    from .dispatcher_knobs import _log_route as _lrh
                except Exception:  # noqa: BLE001 — fail-loud best-effort
                    _lrh = None
            if _lrh is not None:
                try:
                    _lrh("POST", event_detail="banner_capture_fallback",
                         outcome="no_row_match", re_parked=True, session_id=sid)
                except Exception:  # noqa: BLE001
                    logger.exception("banner_capture_fallback event log failed")
    except Exception:  # noqa: BLE001 — the fallback must never break anything
        try:
            logger.exception("banner_capture_fallback worker error")
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            with _CAPTURE_FB_LOCK:
                _CAPTURE_FB_INFLIGHT.discard(sid)
        except Exception:  # noqa: BLE001
            pass


def _maybe_schedule_capture_fallback(session_id: str) -> None:
    """R20-D3: schedule the one-shot capture-fallback watcher for a session
    that just parked a banner (semaphored per session — at most ONE watcher
    in flight per session; a second park while one is watching is covered by
    the in-flight watcher's aggregate snapshot). Daemon thread, never
    raises, never blocks the parker."""
    try:
        if banner_capture_fallback_wait() <= 0:
            return
        sid = str(session_id or "")
        if not sid:
            return
        spawn = False
        with _CAPTURE_FB_LOCK:
            if sid not in _CAPTURE_FB_INFLIGHT:
                _CAPTURE_FB_INFLIGHT.add(sid)
                spawn = True
        if not spawn:
            return
        captured = _ANCHOR_BANNERS.get(sid, "")
        if not str(captured or "").strip():
            with _CAPTURE_FB_LOCK:
                _CAPTURE_FB_INFLIGHT.discard(sid)
            return
        threading.Thread(
            target=_capture_fallback_watch, args=(sid, captured),
            daemon=True,
            name="rider20-capture-fallback-%s" % sid[:24]).start()
    except Exception:  # noqa: BLE001 — scheduling must never break the park
        pass


# --- R19.11 FIX 1: decision-banner loss on two-lane turns ------------------
# Live evidence (reviewer session 20260803_140900_48d4d030): a decision
# banner consumed+appended at an early benign edge was wiped when a later
# uncensored-render/anchor POST transform REPLACED the turn tail. Hold the
# consumed decision-lane banner briefly (120s TTL): any subsequent delivery
# edge in the same window whose text does NOT carry the decision marker
# re-emits it. Bounded 32 sessions, MAX_BANNER_CHARS respected, fail-open.
_DECISION_MARK = "· router · decision"
_DECISION_HOLD_TTL = 120.0
_HELD_DECISIONS: Dict[str, Tuple[str, float]] = {}
_HELD_DECISIONS_MAX = 32


def note_consumed_decision(session_id: str, banner_text: str) -> None:
    """After a consume, remember a decision-lane banner so a later
    render-replacement edge can re-emit it. Never raises."""
    try:
        b = str(banner_text or "")
        if _DECISION_MARK not in b:
            return
        sid = str(session_id or "")
        if sid in _HELD_DECISIONS:
            return  # already holding (first hold wins within the TTL)
        while len(_HELD_DECISIONS) >= _HELD_DECISIONS_MAX:
            _HELD_DECISIONS.pop(next(iter(_HELD_DECISIONS)), None)
        _HELD_DECISIONS[sid] = (b[:MAX_BANNER_CHARS], time.time())
    except Exception:  # noqa: BLE001
        pass


def settle_decision_banner(session_id: str, delivered_text: str) -> str:
    """Final-delivery gate: if the text already carries the decision
    marker, leave the hold (it expires via TTL). If it does NOT and a
    fresh hold exists (render-replacement class), re-emit the banner.
    Respects append_banner's gate; never raises; never returns None."""
    try:
        sid = str(session_id or "")
        held = _HELD_DECISIONS.get(sid)
        if not held:
            return delivered_text if isinstance(delivered_text, str) else ""
        banner, ts = held
        if _DECISION_MARK in str(delivered_text or ""):
            return delivered_text  # delivered on this edge; hold expires
        if (time.time() - ts) > _DECISION_HOLD_TTL:
            _HELD_DECISIONS.pop(sid, None)
            return delivered_text
        _HELD_DECISIONS.pop(sid, None)  # one re-emit, then done
        return append_banner(delivered_text, "\n" + banner, _knob_checked=True)
    except Exception:  # noqa: BLE001
        return delivered_text if isinstance(delivered_text, str) else ""