"""features/banners/turn_record.py — banners are RECORDS, not body content.

M35-17 (supersedes the M34-19 carry and the §10.4-F two-representation rule):
a lane's banner is turn PROVENANCE, and provenance is never content of the
turn. This module therefore has no function that returns text a caller could
append to a body:

  * REGISTER at fire time — every lane hands its rendered segment here
    (`park_anchor_banner` is the shared fire-time hook; the direct-render
    edges register their own segment).
  * RECORD once — `record_turn()` writes ONE `banner_record` line per
    (session, turn) into the decisions store. It accepts the delivered body
    only to hash it and returns None, so there is no signature through which
    a banner could enter a body.
  * RENDER elsewhere — `features/banners/render.py` is a pure function of a
    record, used by the operator/audit surface only.

Never raises. Bounded state (32 sessions, 2 turns of retention). Deterministic.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_RECORDS: Dict[str, Dict[str, Any]] = {}
_MAX_RECORDS = 32
_TTL_S = 900.0
_LOCK = threading.Lock()

_LANE_LABELS = (
    ("impulse (decision)", "decision"),
    ("higher-self (frontier)", "frontier"),
    ("shadow-self (uncensored)", "uncensored"),
)
_ROW_RE = re.compile(r"row=(\d+)")


# ---------------------------------------------------------------------------
# turn identity — the same signal the park semaphore uses
# ---------------------------------------------------------------------------

def turn_key(session_id: str) -> Tuple[int, str]:
    """(turn_no, ask signature) for this session. Never raises."""
    try:
        from ... import advisory_consumption as _ac  # hermes_router.advisory_consumption

        return (int(_ac.turn_no(session_id) or 0),
                str(_ac.open_signal(session_id) or "")[:40])
    except Exception:  # noqa: BLE001 — identity is best-effort only
        return (0, "")


def turn_no(session_id: str) -> int:
    """The turn number for this session, 0 when unknown. Never raises."""
    try:
        return int(turn_key(session_id)[0])
    except Exception:  # noqa: BLE001
        return 0


def _lane_of(text: str) -> str:
    s = str(text or "")
    for label, lane in _LANE_LABELS:
        if label in s:
            return lane
    return "router"


def _row_of(text: str) -> str:
    m = _ROW_RE.search(str(text or ""))
    return m.group(1) if m else ""


def _record(session_id: str) -> Dict[str, Any]:
    sid = str(session_id or "")
    now = time.time()
    key = turn_key(sid)
    rec = _RECORDS.get(sid)
    if rec is not None:
        stale = (now - float(rec.get("touched") or 0)) > _TTL_S
        if rec.get("turn_key") != key or stale:
            rec = None  # turn changed (or record expired) -> new record
    if rec is None:
        rec = {"turn_key": key, "segments": [], "emitted": False,
               "attached_at": 0, "touched": now, "seq": 0}
        with _LOCK:
            while len(_RECORDS) >= _MAX_RECORDS:
                _RECORDS.pop(next(iter(_RECORDS)), None)
            _RECORDS[sid] = rec
    rec["touched"] = now
    return rec


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------

def register(session_id: str, segment: str, *, lane: str = "",
             kind: str = "", edge: str = "", **kwargs) -> bool:
    """Register one lane's rendered banner segment for this turn.

    Idempotent: an identical segment is not stored twice, and a second segment
    from the SAME lane/kind/row replaces the earlier one (latest wins for a
    retry). `frontier`/`decision` repeats stack as separate measurements.
    Returns True when the record changed. Never raises. The keyword-only
    leftovers (`replace`, `prefix`, `knob_checked`, ...) are accepted so an
    older call site cannot break registration.
    """
    try:
        text = str(segment or "").strip()
        if not text:
            return False
        rec = _record(session_id)
        _lane = str(lane or "") or _lane_of(text)
        _kind = str(kind or "")
        _row = _row_of(text)
        _aggregating = _lane.startswith("frontier") or _lane == "decision"
        segs: List[Dict[str, Any]] = rec["segments"]
        for i, seg in enumerate(segs):
            if seg["text"] == text:
                return False
            if not _aggregating and (seg["lane"] == _lane and seg["kind"] == _kind
                                     and seg["row"] == _row):
                segs[i] = {"lane": _lane, "kind": _kind, "row": _row,
                           "text": text, "edge": str(edge or "")}
                return True
        rec["seq"] = int(rec.get("seq") or 0) + 1
        segs.append({"lane": _lane, "kind": _kind, "row": _row,
                     "text": text, "edge": str(edge or "")})
        while len(segs) > 8:  # bound: one turn cannot accumulate unboundedly
            segs.pop(0)
        return True
    except Exception:  # noqa: BLE001 — registration never breaks a lane
        return False


def render(session_id: str) -> str:
    """The turn's banner BLOCK for extraction (one line per segment).

    Nothing on the delivery path calls this — it exists for the operator
    surface and for a test that must prove a segment was registered.
    """
    try:
        rec = _RECORDS.get(str(session_id or ""))
        if not rec or not rec.get("segments"):
            return ""
        return "\n".join(str(s.get("text") or "").strip()
                         for s in rec["segments"]
                         if str(s.get("text") or "").strip())
    except Exception:  # noqa: BLE001
        return ""


def segments(session_id: str) -> List[Dict[str, Any]]:
    """Copy of this turn's registered segments. Never raises."""
    try:
        rec = _RECORDS.get(str(session_id or ""))
        return [dict(s) for s in (rec.get("segments") or [])] if rec else []
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# record — the ONE emit point
# ---------------------------------------------------------------------------

def record_turn(session_id: str, turn_id: Any = None, delivered: Any = None,
                decision: Any = None, segs: Optional[List[Dict[str, Any]]] = None,
                *, knob_level: int = 0, lane: str = "") -> None:
    """Write ONE `banner_record` for this (session, turn). Returns NOTHING.

    The delivered body is accepted only to hash and measure it — the record
    carries `body_sha256`/`body_chars`, never the bytes. A second call for the
    same turn_id drops the payload and increments `dupes` on the first record,
    so a retried delivery edge cannot double-count tokens, cost or rows.

    Never raises; a failed record never withholds a turn.
    """
    try:
        from ...core import decisions as _dec

        sid = str(session_id or "")
        _segs = list(segs) if segs is not None else segments(sid)
        _tid = str(turn_id) if turn_id not in (None, "") else str(turn_key(sid)[0])
        body = delivered if isinstance(delivered, str) else ""
        if delivered is None:
            body_sha = ""
        else:
            body_sha = hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()
        _lane = str(lane or "")
        if not _lane:
            _lane = str(getattr(decision, "delivered_source", "") or "") or "main"
        ok = _dec.record_banner(
            session_id=sid, turn_id=_tid,
            lane=_lane,
            decision=str(getattr(decision, "case_id", "") or ""),
            delivered_source=str(getattr(decision, "delivered_source", "") or ""),
            action=str(getattr(decision, "action", "") or ""),
            outcome=str(getattr(decision, "reason", "") or "")[:20],
            body_sha256=body_sha, body_chars=len(body),
            knob_level=int(knob_level or 0),
            omit_reason=(getattr(decision, "reason", "") or "")
            if not body else "",
            banner=[{"lane": s.get("lane", ""), "kind": s.get("kind", ""),
                     "trigger": s.get("kind", ""),
                     "model": "", "endpoint_host": "",
                     "tokens_in": 0, "tokens_out": 0,
                     "est_cost": 0.0, "cost_basis": "unpriced",
                     "latency_s": 0.0, "retries": 0,
                     "row_ref": (s.get("row") or "MISSING"),
                     "initiator": "agent",
                     "edge": s.get("edge", ""),
                     "text": s.get("text", "")} for s in _segs],
        )
        if ok:
            rec = _RECORDS.get(sid)
            if rec is not None:
                rec["emitted"] = True
    except Exception as exc:  # noqa: BLE001 — a record never withholds a turn
        try:
            logger.error("banner_record_failed detail=%.200s", str(exc))
        except Exception:  # noqa: BLE001
            pass


def segment_count(session_id: str) -> int:
    """How many lane segments this turn has registered. Never raises."""
    try:
        rec = _RECORDS.get(str(session_id or ""))
        return len(rec.get("segments") or []) if rec else 0
    except Exception:  # noqa: BLE001
        return 0


def delivered(session_id: str) -> bool:
    """True when this turn's banner record has been written."""
    try:
        rec = _RECORDS.get(str(session_id or ""))
        return bool(rec and rec.get("emitted"))
    except Exception:  # noqa: BLE001
        return False


def record(session_id: str) -> Dict[str, Any]:
    """Read-only view of this session's turn record (tests/telemetry)."""
    try:
        rec = _RECORDS.get(str(session_id or ""))
        if not rec:
            return {}
        return {"turn_key": rec.get("turn_key"), "emitted": bool(rec.get("emitted")),
                "segments": [dict(s) for s in rec.get("segments") or []]}
    except Exception:  # noqa: BLE001
        return {}


def reset(session_id: str = "") -> None:
    """Drop one session's record (or all). Never raises."""
    try:
        if session_id:
            _RECORDS.pop(str(session_id), None)
        else:
            _RECORDS.clear()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# M46-03 — THE DELIVERY COMPOSER (owner ruling, reverses M35-17)
# ---------------------------------------------------------------------------
# Owner, verbatim: "each call to whatever lane must produce a banner at final
# response so that it's visible there was a router activation (for billing and
# other purposes)". A lane that fired MUST be visible in the delivered bytes;
# silence is the defect. M35-17 removed the composer and 189 tests kept passing
# while no user ever saw a banner.
#
# The line is rendered FROM this record (`segments()`), never from the parked
# text, so the record and the body cannot disagree: one source, one string.
#
# KNOB: `banner_in_body` in the hermes_router config section. Default ON
# (absent key = True) because visibility is the owner's requirement; an
# explicit false/0/off silences the body line without a code change.
_KNOB = "banner_in_body"
_TOK_CELL_RE = re.compile(r"^tok\s+(\d+)\s*/\s*(\d+)$")
_COST_CELL_RE = re.compile(r"^\$([0-9]*\.?[0-9]+)(\*?)$")
_TRIG_ALIASES = {"consult": "pre-check", "orientation": "pre-check",
                 "completion_audit": "post-check", "audit": "post-check"}


def deliver_in_body_enabled() -> bool:
    """Is the delivered-body banner line switched on? Default ON. Never raises."""
    try:
        from ... import config_access as _ca

        raw = (_ca.router_section() or {}).get(_KNOB, True)
    except Exception:  # noqa: BLE001 — a missing config read is not a silence
        return True
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(int(raw))
    s = str(raw).strip().lower()
    if s in ("", "default", "none"):
        return True
    return s not in ("0", "false", "off", "no", "disabled")


def _head() -> str:
    try:
        from ... import debug_banner as _db

        return str(_db.BANNER_HEAD or "· router ·")
    except Exception:  # noqa: BLE001
        return "· router ·"


def _group_of(lane: str, text: str) -> str:
    """Which LANE SEGMENT a registered fragment belongs to (aggregation unit)."""
    s = "%s %s" % (str(lane or ""), str(text or ""))
    if "decision" in s or "reflex" in s or "impulse" in s:
        return "decision"
    if "frontier" in s or "higher-self" in s:
        return "frontier"
    if "uncensored" in s or "shadow-self" in s:
        return "uncensored"
    return str(lane or "router") or "router"


def _parse(seg: str) -> List[Dict[str, Any]]:
    """Split ONE registered fragment into banner cells, one dict per LINE.

    A registered fragment can carry more than one line: `park_task_banner`
    composes the frontier PRE and POST legs as "pre\\npost" before parking, and
    the store keeps that composed text as ONE segment. Parsing only the first
    line would drop the POST leg and double-count the PRE, so every non-empty
    line is parsed. Tolerant, never raises.
    """
    out: List[Dict[str, Any]] = []
    try:
        head = _head()
        for raw_line in str(seg or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if head in line:
                line = line.split(head, 1)[1].strip()
            cells = [c.strip() for c in line.split("|")] if line else []
            if not cells:
                continue
            cell: Dict[str, Any] = {"label": cells[0], "trigger": "",
                                    "model": "", "ti": 0, "to": 0,
                                    "cost": 0.0, "star": False, "extra": []}
            if len(cells) > 1:
                t = cells[1].strip().lower()
                cell["trigger"] = _TRIG_ALIASES.get(t, t)
            for c in cells[2:]:
                m = _TOK_CELL_RE.match(c)
                if m:
                    cell["ti"] += int(m.group(1))
                    cell["to"] += int(m.group(2))
                    continue
                cm = _COST_CELL_RE.match(c)
                if cm:
                    cell["cost"] += float(cm.group(1))
                    cell["star"] = cell["star"] or bool(cm.group(2))
                    continue
                if c.startswith(("initiator=", "provider=")):
                    if c not in cell["extra"]:
                        cell["extra"].append(c)
                    continue
                if not cell["model"]:
                    cell["model"] = c
            out.append(cell)
    except Exception:  # noqa: BLE001
        return []
    return out


def _merge(parsed: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Fold one lane's fragments (frontier PRE + POST) into ONE segment."""
    base = dict(parsed[0])
    base.update({"ti": 0, "to": 0, "cost": 0.0, "star": False, "extra": [],
                 "actions": 0})
    trigs: List[str] = []
    models: List[str] = []
    seen: List[Tuple[Any, ...]] = []
    for p in parsed:
        # A leg can arrive twice (registered at park time and again at the
        # delivery edge); the same measurement is never summed twice.
        sig = (p["label"], p["trigger"], p["model"], p["ti"], p["to"],
               round(float(p["cost"]), 8))
        if sig in seen:
            continue
        seen.append(sig)
        base["actions"] = int(base.get("actions") or 0) + 1
        base["ti"] += int(p["ti"])
        base["to"] += int(p["to"])
        base["cost"] += float(p["cost"])
        base["star"] = base["star"] or bool(p["star"])
        if p["trigger"] and p["trigger"] not in trigs:
            trigs.append(p["trigger"])
        if p["model"] and p["model"] not in models:
            models.append(p["model"])
        for cell in p["extra"]:
            if cell not in base["extra"]:
                base["extra"].append(cell)
    if set(trigs) == {"pre-check", "post-check"}:
        base["trigger"] = "pre+post-check"
    elif trigs:
        base["trigger"] = "+".join(trigs)
    base["model"] = "/".join(models)
    return base


def _priced_cost(m: Dict[str, Any]) -> Tuple[float, bool]:
    """(cost, estimated) for ONE lane.

    A fragment that carried no self-reported cost used to render "$0.0000" even
    though the lane had really billed tokens — a decision lane showed free while
    its Jev calls were charged. When the cost cell is absent the router's ONE
    price lookup prices the lane's own tokens, and the figure is marked
    estimated so no one reads it as a measured invoice. Never raises.
    """
    try:
        cost = max(0.0, float(m.get("cost") or 0.0))
        if cost > 0:
            return cost, False
        ti = int(m.get("ti") or 0)
        to = int(m.get("to") or 0)
        if not (ti or to):
            return 0.0, False
        raw = str(m.get("model") or "").split("@")[0].strip()
        if not raw:
            return 0.0, False
        from ...core import usage_ledger as _ul

        row, _key, _src = _ul._price_row(raw)
        if not isinstance(row, dict):
            return 0.0, False
        est = ((ti / 1000000.0) * float(row.get("input_per_1m") or 0.0)
               + (to / 1000000.0) * float(row.get("output_per_1m") or 0.0))
        return max(0.0, est), True
    except Exception:  # noqa: BLE001 — a price never breaks a banner
        return 0.0, False


def _render_cells(m: Dict[str, Any]) -> str:
    """One lane's segment inside the block, WITHOUT the head (one head per body)."""
    cells = [m["label"] or "router"]
    if m["trigger"]:
        cells.append(str(m["trigger"]))
    actions = int(m.get("actions") or 0)
    if actions > 1:
        cells.append("\u00d7%d" % actions)
    if m["model"]:
        cells.append(str(m["model"]))
    cells.append("tok %d/%d" % (int(m["ti"]), int(m["to"])))
    cost, estimated = _priced_cost(m)
    cells.append(("$%.4f" % cost) + ("*" if (m["star"] or estimated) else ""))
    cells.extend(m["extra"])
    return " | ".join(cells)


def compose_line(session_id: str) -> str:
    """The ONE banner line for this turn, or "" when no lane fired. Never raises.

    Aggregation: fragments are grouped PER LANE; a lane's fragments (frontier
    PRE + POST, or a decision rollup and its retry) fold into ONE segment with
    summed tokens/cost and a merged trigger (pre+post-check). Distinct lanes
    become distinct segments inside the SAME single line, separated by " || ".
    There is exactly ONE BANNER_HEAD per delivered body.
    """
    try:
        segs = segments(session_id)
        if not segs:
            return ""
        order: List[str] = []
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for seg in segs:
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            key = _group_of(str(seg.get("lane") or ""), text)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].extend(_parse(text))
        if not order:
            return ""
        parts = [_render_cells(_merge(groups[k])) for k in order]
        head = _head()
        # OWNER RULING 2026-11-07: each LANE carries its OWN banner line. A lane
        # that fired more than once this turn does not get more lines — its one
        # line carries the aggregated totals (_merge sums tokens and cost, keeps
        # the distinct trigger types and counts the actions, rendered as ×N).
        return "\n".join("%s %s" % (head, p) for p in parts)
    except Exception:  # noqa: BLE001 — a render never breaks a delivery
        return ""


def compose_onto(session_id: str, body: Any, *, mode: str = "append") -> Any:
    """Append the record's ONE banner line to the DELIVERED body.

    Returns the body unchanged when the knob is off, when no lane fired, when
    the body is empty (an append edge with nothing to attach to), or when the
    body already carries a banner (one banner per delivery — never two).
    When the body already carries a provenance footer, the BANNER WINS: the
    footer is stripped so the delivered bytes carry ONE attribution block.
    Never raises; never returns None for a str input.
    """
    try:
        if not deliver_in_body_enabled():
            return body
        line = compose_line(session_id)
        if not line:
            return body
        base = body if isinstance(body, str) else ""
        if not base.strip():
            return body
        if _head() in base:
            return body
        # ONE banner per turn, and never a stale one. `emitted` is set by the
        # ONE emit point (record_turn) — i.e. only once the turn's final bytes
        # have actually shipped. Before that, an intermediate compose can be
        # discarded upstream (the delivery-decision edge returns its own bytes),
        # so it must stay re-attachable. After it, a later pass or a later turn
        # with no new segment gets its own bytes untouched: the R33f-A
        # stale-leak ban survives the composer.
        _rec = _RECORDS.get(str(session_id or ""))
        _n = len((_rec or {}).get("segments") or [])
        if (_rec is not None and _rec.get("emitted")
                and int(_rec.get("attached_at") or 0) == _n):
            return body
        try:
            from ... import provenance_footer as _pf

            if _pf.has_footer(base):
                base = _pf.strip_footer(base)
        except Exception:  # noqa: BLE001 — footer handling is best-effort
            pass
        if _rec is not None:
            _rec["attached_at"] = _n
        return base.rstrip() + "\n\n" + line
    except Exception:  # noqa: BLE001
        return body
