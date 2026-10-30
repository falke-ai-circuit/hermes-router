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
import re
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


def lane_label(lane: str) -> str:
    """The display label for a lane KEY — the single source for banner identity.

    format_banner renders it, and decision.DELIVERED_MARK derives its echo-filter mark from
    it, so renaming a lane's user-facing label cannot leave a stale copy behind that silently
    stops matching. Pure string shaping, never raises.
    """
    try:
        lane = str(lane or "")
        # Goran 09-14 framing: the uncensored/shadow lane is the agent's own
        # SHADOW SELF (part of her model, not an external route); the
        # frontier lane is her HIGHER SELF. Banner labels reflect identity,
        # not plumbing.
        # v1.1 addendum (SPEC-impulse-lane-v1.md): display label is
        # 'impulse (decision)' — the lane KEY stays 'decision', the
        # module name and PROVENANCE_TAG are unchanged.
        if lane.startswith("frontier"):
            return "higher-self (frontier)"
        if lane in ("shadow", "uncensored", "uncensored-render"):
            return "shadow-self (uncensored)"
        if lane in ("decision", "reflex"):
            return "impulse (decision)"
        return lane.split("-", 1)[0]
    except Exception:  # noqa: BLE001
        return ""


def format_banner(lane: str, trigger: str, model: str, endpoint: str,
                  tokens_in: Optional[int], tokens_out: Optional[int],
                  est_cost: Optional[float], latency_s: Optional[float],
                  retries: int = 0, level: Optional[int] = None,
                  task_id: str = "", session_id: str = "", gate: str = "",
                  route_id: str = "", initiator: str = "",
                  metadata_only: bool = False) -> str:
    """Render the banner text. Pure string shaping — no I/O, no state.
    Returns "" when the caller should omit the banner (oversized or invalid
    lane). Never raises.

    metadata_only (R76 P2 U5): the FAILURE-REPORT shape — head, lane label,
    trigger, model, closing tail — with no usage cell and no initiator/L2/L3
    addenda, because there was no response to measure. It exists so the
    frontier empty-consult banner goes through this renderer instead of being
    hand-formatted beside it; the text it produces is exactly the string that
    path printed before.

    Guarantee of this shape, stated exactly: lane VALIDATION applies
    (FORBIDDEN_LANES or an unknown lane returns ""), and the lane label comes
    from lane_label(). Endpoint REDACTION does NOT reach the rendered line —
    not because the endpoint goes unread: `ep_s` is built and host-redacted
    (:180-183) and tokens_in/tokens_out/est_cost are converted (:170-172)
    BEFORE this branch, and metadata_only returns before `ep_s` and those
    figures are USED, because the failure shape renders no endpoint cell and
    no usage cell. The redacted host and the figures are therefore discarded
    unrendered. Passing a secret-bearing URI leaves the rendered bytes
    unchanged, and no unpriced-model marker can appear, for that reason: the
    values are read, then dropped, not skipped. A caller that needs host
    redaction on the line must use the normal shape, not this one.
    """
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
        # R34-19b (Goran-direct, 2026-10-21): the banner must say WHICH end of
        # the consult it marks. 'consult' was rendered for the PRE advisory and
        # 'completion_audit' for the POST confirmation, both attaching at the
        # delivery edge — so the PRE banner sat below the answer and read as if
        # it were a post banner. The user's own memory of "post consult banners"
        # was accurate about the position and wrong only because the label
        # never distinguished the two ends.
        _END_LABELS = {
            "consult": "pre-check",
            "orientation": "pre-check",
            "completion_audit": "post-check",
            "audit": "post-check",
        }
        trig_s = _END_LABELS.get(str(trigger or "").strip().lower(),
                                 str(trigger or "none"))[:120]
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
        _lane_label = lane_label(lane)
        # R76 P2 U5: the failure-report shape. No usage cell (nothing was
        # returned to measure), no initiator, no L2/L3 addenda — head, label,
        # trigger, model, closing tail. This is byte-for-byte the text the
        # frontier empty-consult path hand-formatted before it came here.
        # It returns BEFORE ep_s is used and before any usage cell exists:
        # lane validation and the label are the only things this shape
        # enforces. endpoint/tokens/cost are deliberately dead here — do not
        # read this branch as a redaction surface (see the docstring).
        if metadata_only:
            return "%s %s | %s | %s %s" % (BANNER_HEAD, _lane_label, trig_s,
                                           model_s, BANNER_TAIL)
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


_DECISION_LINE_MARK = "impulse (decision)"


def _parse_decision_line(line: str):
    """Parse a decision-lane banner line into its numbers, or None.

    Two shapes exist historically — the aggregate (`x3 | 2 lead · 1 hold | ... |
    row 4-6`) and the midturn rollup (`3 verdicts | tok i/o | $... | provider=
    ... | row=7 ·`). Both are recognised; anything else returns None and the
    caller appends as before (fail-open).
    """
    try:
        if not line or _DECISION_LINE_MARK not in line:
            return None
        cells = [c.strip() for c in line.split("|")]
        d = {"n": 0, "leads": None, "holds": None, "ti": 0, "to": 0,
             "cost": 0.0, "star": False, "r1": 0, "r2": 0, "model": ""}
        m = re.search(r"x(\d+)\s*$", cells[0])
        if m:
            d["n"] = int(m.group(1))
        m = re.search(r"(\d+)\s+verdict", line)
        if m:
            d["n"] = d["n"] or int(m.group(1))
        m = re.search(r"(\d+)\s+lead", line)
        if m:
            d["leads"] = int(m.group(1))
        m = re.search(r"(\d+)\s+hold", line)
        if m:
            d["holds"] = int(m.group(1))
        m = re.search(r"(\d+)/(\d+)\s+tok", line)
        if m:
            d["ti"], d["to"] = int(m.group(1)), int(m.group(2))
        m = re.search(r"\$([0-9.]+)(\*?)", line)
        if m:
            d["cost"] = float(m.group(1))
            d["star"] = bool(m.group(2))
        m = re.search(r"rows?\s+(\d+)-(\d+)", line)
        if m:
            d["r1"], d["r2"] = int(m.group(1)), int(m.group(2))
        else:
            m = re.search(r"row\s+(\d+)", line)
            if m:
                d["r1"] = d["r2"] = int(m.group(1))
            rows = [int(x) for x in re.findall(r"row=(\d+)", line)]
            if rows:
                d["r1"] = d["r1"] or min(rows)
                d["r2"] = max(d["r2"], max(rows))
        for c in cells[1:]:
            if "@" in c and "tok" not in c and "row" not in c:
                d["model"] = c
                break
        if d["n"] <= 0:
            d["n"] = 1
        if not d["ti"] and not d["to"] and not d["r1"]:
            return None   # carries none of our numbers: not a line to merge
        return d
    except Exception:  # noqa: BLE001
        return None


def _render_decision_line(d) -> str:
    """Render the unified aggregate line from merged numbers."""
    head = "%s impulse (decision)" % BANNER_HEAD
    if int(d["n"]) > 1:
        head += " x%d" % int(d["n"])
    classes = []
    if d.get("leads"):
        classes.append("%d lead" % int(d["leads"]))
    if d.get("holds"):
        classes.append("%d hold" % int(d["holds"]))
    cells = [head]
    if classes:
        cells.append(" · ".join(classes))
    if d.get("model"):
        cells.append(str(d["model"]))
    cells.append("%d/%d tok" % (int(d["ti"]), int(d["to"])))
    if d["cost"] > 0.0:
        cells.append("$%.5f" % d["cost"])
    elif d["star"] or d["ti"] or d["to"]:
        cells.append("$0.00000*")
    else:
        cells.append("$0.00000")
    r1, r2 = int(d["r1"]), int(d["r2"])
    cells.append(("rows %d-%d" % (r1, r2)) if r2 > r1 else ("row %d" % (r1 or 0)))
    return " | ".join(cells)


def merge_decision_banner(delivery_text: str, banner_text: str):
    """Merge a second decision-line into the one already in the delivery.

    Returns the new delivery text, or None when the merge does not apply (not a
    decision line, unparseable, or a non-decision lane present) — callers then
    append exactly as before. Lane-scoped by construction: only lines carrying
    the decision mark are touched, so anchor/frontier/uncensored banners keep
    their own independent delivery.
    """
    try:
        if not isinstance(delivery_text, str) or not isinstance(banner_text, str):
            return None
        if _DECISION_LINE_MARK not in banner_text:
            return None
        new = _parse_decision_line(banner_text)
        if not new:
            return None
        lines = delivery_text.splitlines()
        for i in range(len(lines) - 1, -1, -1):
            if _DECISION_LINE_MARK not in lines[i]:
                continue
            old = _parse_decision_line(lines[i])
            if not old:
                return None          # present but unfamiliar: append, never guess
            merged = dict(old)
            merged["n"] = int(old["n"]) + int(new["n"])
            if old.get("leads") is not None or new.get("leads") is not None:
                merged["leads"] = int(old.get("leads") or 0) + int(new.get("leads") or 0)
                merged["holds"] = int(old.get("holds") or 0) + int(new.get("holds") or 0)
            merged["ti"] = int(old["ti"]) + int(new["ti"])
            merged["to"] = int(old["to"]) + int(new["to"])
            merged["cost"] = float(old["cost"]) + float(new["cost"])
            merged["star"] = bool(old["star"] or new["star"])
            known = [r for r in (old["r1"], old["r2"], new["r1"], new["r2"]) if r > 0]
            merged["r1"] = min(known) if known else 0
            merged["r2"] = max(known) if known else 0
            merged["model"] = old.get("model") or new.get("model") or ""
            lines[i] = _render_decision_line(merged)
            return "\n".join(lines)
        return None
    except Exception:  # noqa: BLE001
        return None


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
        if not prepend:
            # R79: ONE decision line per turn. A second decision-lane banner
            # merges into the one already delivered (counts/tokens/cost summed,
            # row span extended); other lanes are never touched. Unparseable ->
            # None -> append exactly as before (fail-open).
            merged = merge_decision_banner(base, banner)
            if merged is not None:
                return merged
        return ("%s\n\n%s" % (banner, base)) if prepend else ("%s\n\n%s" % (base, banner))
    except Exception as exc:  # noqa: BLE001 — banner must never break delivery
        try:
            logger.error("debug_banner_failed detail=%.200s", str(exc))
        except Exception:  # noqa: BLE001 — even the failure log is best-effort
            pass
        return delivery_text if isinstance(delivery_text, str) else ""


# --- §10.4 anchor-banner delivery parking — OWNERSHIP MOVED (P5) ----------
# park / consume / expire / capture-fallback now live in
# features/banners/lifecycle.py (BannerLifecycle, proposal §2.2). The
# imperative bodies were transplanted verbatim; these thin delegates keep
# the historical names for the call sites and the test suite. The state
# dicts stay HERE (the live test surface) and the lifecycle operates on
# them — behavior byte-identical (Binding 1).
_ANCHOR_BANNERS: Dict[str, str] = {}
_ANCHOR_BANNER_MAX = 32

_MAX_PARK_SEGMENTS = 4
_ANCHOR_TASKS: Dict[str, list] = {}
_ANCHOR_SEGS: Dict[str, list] = {}

# --- R33f-A banner semaphore ------------------------------------------------
# Park records scoped (session_id, event_seq) + TTL = ONE delivery, consumed
# once at the POST attach (conductor brief; live evidence: a stale 19:35
# frontier banner leaked to 4 later turns and double-appended on shadow
# renders — session 20261006_190903_c82cbbcc). The record rides the slot:
# park -> record(event_seq, parked_at); consume -> record dies with the slot;
# a not-landed attach is DROPPED fail-loud (banner_semaphore_expired), never
# re-parked into later turns. claim_banner_ownership lets an uncensored-render
# turn (which appends its OWN fresh banner) release the slot BEFORE any anchor
# consume. Wrong-session touches are structurally impossible: every map is
# keyed by the exact session id and never read across keys.
_PARK_RECORDS: Dict[str, Dict[str, Any]] = {}
_PARK_RECORDS_MAX = 64
# R34-15 item 2: `banner_semaphore_expired` claimed an expiry nothing
# measured (the same mislabelling class as `advisory_chars`). The real
# condition is "the park did not land" -> named for what it is. The old
# constant name stays as an alias so importers do not break; the EMITTED
# event is `banner_park_dropped` with a reason.
BANNER_PARK_DROPPED_EVENT = "banner_park_dropped"
BANNER_PARK_RETAINED_EVENT = "banner_park_retained"
BANNER_OWNERSHIP_CLAIMED_EVENT = "banner_ownership_claimed"


def _next_park_event_seq() -> int:
    """Monotonic event seq for the park record (§10.4-E correlation)."""
    try:
        from . import suggestions

        return int(suggestions.next_event_seq())
    except Exception:  # noqa: BLE001 — a record id must never break a park
        return 0


def _record_park(session_id: str, task_id: str = "") -> None:
    """Register the one-delivery semaphore record for a LIVE park. Bounded
    FIFO; never raises; no-op on an empty session id."""
    try:
        sid = str(session_id or "")
        if not sid:
            return
        while len(_PARK_RECORDS) >= _PARK_RECORDS_MAX:
            _PARK_RECORDS.pop(next(iter(_PARK_RECORDS)), None)
        # R34-15: the park is scoped to the TURN it belongs to. turn_no is
        # the advisory_consumption ledger counter (one user ask = one turn);
        # it is the retention discriminator — same turn -> the banner survives
        # a mid-turn capture failure, later turn -> it is dropped undelivered.
        _turn = 0
        # R34-18 residual 2: the park also carries the IDENTITY of the ask it
        # belongs to. The turn counter drifts (a duplicate transform pass over
        # the same message increments it), so `park_turn < current_turn` alone
        # cannot prove a turn closed; the ask signature can. Same signature at
        # delivery time = the same turn, still delivering.
        _ask_sig = ""
        try:
            from . import advisory_consumption as _ac

            _turn = int(_ac.turn_no(sid) or 0)
            _ask_sig = str(_ac.open_signal(sid) or "")
        except Exception:  # noqa: BLE001 — a stamp must never break a park
            _turn = 0
            _ask_sig = ""
        _PARK_RECORDS[sid] = {
            "event_seq": _next_park_event_seq(),
            "parked_at": time.time(),
            "task_id": str(task_id or "")[:40],
            "turn_no": _turn,
            "ask_sig": _ask_sig[:40],
        }
    except Exception:  # noqa: BLE001 — record failure never breaks the park
        pass


def park_record(session_id: str) -> Dict[str, Any]:
    """The parked banner's semaphore record for this session ({}) when none.
    Scoped (session_id, event_seq) per the R33f-A contract."""
    try:
        rec = _PARK_RECORDS.get(str(session_id or ""))
        return dict(rec) if isinstance(rec, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _lifecycle():
    """The BannerLifecycle singleton (banner ownership, P5)."""
    from .features.banners.lifecycle import LIFECYCLE

    return LIFECYCLE


def park_anchor_banner(session_id: str, banner_text: str,
                       task_id: str = "") -> None:
    """Delegate: BannerLifecycle.park for the registered "anchor" kind.
    Semantics (R9d per-task replace, R19.16 stack, identical re-park
    dedupe, bounded map) are the kind's stack_policy data — unchanged.
    R33f-A: a LIVE park (the slot now carries this banner) registers the
    one-delivery semaphore record scoped (session_id, event_seq); a no-op
    park (empty text, once-policy loser, dedupe) leaves the PRIOR record
    intact — the prior banner still owns the one delivery."""
    try:
        from .features.banners import kinds as _bk

        _lifecycle().park(_bk.get("anchor"), session_id, task_id, banner_text)
        try:
            # ONE banner per turn (Goran 2026-10-30): the fire-time hook every
            # lane already shares also REGISTERS its segment into the turn
            # record. Emission happens once, at the delivery boundary.
            from .features.banners import turn_record as _turn

            _turn.register(session_id, banner_text, kind="anchor")
        except Exception:  # noqa: BLE001 — registration never breaks a park
            pass
        try:
            sid = str(session_id or "")
            seg = str(banner_text or "").strip()[:MAX_BANNER_CHARS]
            live = _ANCHOR_BANNERS.get(sid, "")
            if seg and (live == seg or seg in live):
                _record_park(sid, task_id)
        except Exception:  # noqa: BLE001 — record never breaks the park
            pass
    except Exception:  # noqa: BLE001
        pass


def consume_parked_banner(session_id: str) -> str:
    """Delegate: BannerLifecycle.consume for the "anchor" kind.
    R33f-A consume-once: the semaphore record dies WITH the slot — one
    delivery total. Wrong-session is a silent miss (never another
    session's key)."""
    try:
        from .features.banners import kinds as _bk

        try:
            _PARK_RECORDS.pop(str(session_id or ""), None)
        except Exception:  # noqa: BLE001
            pass
        return _lifecycle().consume(_bk.get("anchor"), session_id, "",
                                    "claim_release")
    except Exception:  # noqa: BLE001
        return ""


def claim_banner_ownership(session_id: str, edge: str = "") -> bool:
    """R33f-A ownership claim: an uncensored-render turn appends its OWN
    fresh banner, so it claims the parked semaphore BEFORE any anchor
    consume — the parked slot is released WITHOUT attaching (a stale or
    duplicate banner can never double-append onto the render's own
    banner). Returns True when a parked banner was claimed (consumed and
    dropped, by design, with a `banner_ownership_claimed` route event).
    Never raises; wrong-session touches are silent misses."""
    try:
        sid = str(session_id or "")
        had = bool(_ANCHOR_BANNERS.get(sid))
        if had:
            from .features.banners import kinds as _bk

            _lifecycle().consume(_bk.get("anchor"), sid, "", "claim_release")
        _PARK_RECORDS.pop(sid, None)
        if had:
            # The claimed banner is released WITHOUT attaching — it must not
            # resurface through the turn record at a later delivery edge.
            try:
                from .features.banners import turn_record as _turn_claim

                _turn_claim.reset(sid)
            except Exception:  # noqa: BLE001 — claim never breaks delivery
                pass
            try:
                from .core import telemetry as _tlm

                _tlm.log_route("POST",
                               event_detail=BANNER_OWNERSHIP_CLAIMED_EVENT,
                               edge=str(edge or "")[:40], session_id=sid)
            except Exception:  # noqa: BLE001 — telemetry never breaks delivery
                pass
        return had
    except Exception:  # noqa: BLE001
        return False


def banner_capture_fallback_wait() -> float:
    """R20-D3 knob read — delegated to the lifecycle (never raises)."""
    try:
        return _lifecycle().banner_capture_fallback_wait()
    except Exception:  # noqa: BLE001
        return 45.0


def _maybe_schedule_capture_fallback(session_id: str) -> None:
    """Delegate: the R20-D3 watcher scheduler now lives on the lifecycle;
    the class-level lock/semaphore objects are aliased below so the test
    surface (DB._CAPTURE_FB_LOCK / _CAPTURE_FB_INFLIGHT) is unchanged."""
    try:
        _lifecycle()._maybe_schedule_capture_fallback(session_id)
    except Exception:  # noqa: BLE001
        pass


from .features.banners.lifecycle import LIFECYCLE as _LIFECYCLE  # noqa: E402

_CAPTURE_FB_LOCK = _LIFECYCLE._CAPTURE_FB_LOCK          # shared module lock
_CAPTURE_FB_INFLIGHT = _LIFECYCLE._CAPTURE_FB_INFLIGHT  # shared semaphore


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
        out = delivered_text
        try:
            from .features.banners import turn_record as _turn

            _turn.register(sid, banner, lane="decision", kind="settle")
        except Exception:  # noqa: BLE001 — registration never breaks delivery
            pass
        return append_banner(delivered_text, "\n" + banner, _knob_checked=True)
    except Exception:  # noqa: BLE001
        return delivered_text if isinstance(delivered_text, str) else ""