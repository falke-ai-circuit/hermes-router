"""features/banners/render.py — the banner EXTRACTOR (M35-17 §3).

`render(record, level)` is a pure function of a parsed `banner_record` line.
It accepts no body and returns to no body caller: the delivery path never
imports this module, so raising the verbosity knob cannot change the bytes
`on_transform_llm_output_guarded` hands to the transport edge.

Levels (spec §5.1): 0 off -> "" ; 1 -> one line ; 2 -> L1 + task/sess/gate
line ; 3 -> L3 adds the `full:` line. An oversized render is TRUNCATED here;
there is no omission path any more — the record always carries every field.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ... import debug_banner as _db

MAX_RENDER_CHARS = 2000


def parse(line: Any) -> Optional[Dict[str, Any]]:
    """Parse one stored JSON line (or accept an already-parsed dict)."""
    try:
        if isinstance(line, dict):
            return line
        return json.loads(str(line or ""))
    except Exception:  # noqa: BLE001
        return None


def _entry_line(rec: Dict[str, Any], seg: Dict[str, Any]) -> str:
    return str(seg.get("text") or "").strip()


def _spend_line(rec: Dict[str, Any], segs: Any) -> str:
    """One compact spend row for a lane that actually paid for tokens.

    M47-22: the banner's job is telling the reader what the turn did. A consult that
    billed a paid API is part of that, so it renders at the DEFAULT level — not only
    at level 3, where nobody looked (fleet config leaves the level unset).

    Renders only when the record carries real token counts; an unpriced or token-less
    record renders nothing rather than "tok 0→0", which would be a field outrunning
    the thing it names. Never raises.
    """
    try:
        segs = segs if isinstance(segs, list) else []
        first = segs[0] if segs and isinstance(segs[0], dict) else {}
        tin = int(first.get("tokens_in") or 0)
        tout = int(first.get("tokens_out") or 0)
        if not tin and not tout:
            return ""
        cost = first.get("est_cost")
        lane = str(rec.get("lane") or "")
        parts = [f"tok {tin:,}→{tout:,}"]
        try:
            if cost is not None and float(cost) > 0:
                parts.append(f"${float(cost):.4f}")
        except (TypeError, ValueError):
            pass
        if lane:
            parts.append(lane)
        return " · ".join(parts)
    except Exception:  # noqa: BLE001 — extraction never raises
        return ""


def render(record: Any, level: int = 1) -> str:
    """Render a banner record at `level`. Never raises; never touches a body."""
    try:
        rec = parse(record)
        if not rec or int(level or 0) <= 0:
            return ""
        segs = [s for s in (rec.get("banner") or []) if isinstance(s, dict)]
        lines = [_entry_line(rec, s) for s in segs]
        lines = [ln for ln in lines if ln]
        if not lines:
            return ""
        if int(level) >= 2:
            lines.append(
                "task %s | sess %s | gate %s | ret %d"
                % (str(rec.get("task_id") or "-")[:24],
                   str(rec.get("session_id") or "-")[:32],
                   str(rec.get("decision") or "-")[:6],
                   int((segs[0] or {}).get("retries") or 0)))
        # M47-22 (owner ruling, 2026-11-15): a consult that spent tokens on a paid
        # API is NOT a level-3 detail. Whenever a lane fired and the record carries
        # token counts, the spend is part of the level-1 banner — otherwise the
        # reader sees that a lane fired but not that it cost money, and the whole
        # point of the banner is knowing what the turn actually did.
        if int(level) < 3:
            _spend = _spend_line(rec, segs)
            if _spend:
                lines.append(_spend)
        if int(level) >= 3:
            first = segs[0] if segs else {}
            lines.append(
                "full: lane=%s endpoint=%s tokens_in=%d tokens_out=%d "
                "retries=%d route_id=%s row=%s"
                % (str(rec.get("lane") or ""),
                   str(first.get("endpoint_host") or ""),
                   int(first.get("tokens_in") or 0),
                   int(first.get("tokens_out") or 0),
                   int(first.get("retries") or 0),
                   str(rec.get("turn_id") or ""),
                   str(first.get("row_ref") or "MISSING")))
        out = "\n".join(lines)
        if len(out) > MAX_RENDER_CHARS:
            out = out[:MAX_RENDER_CHARS].rstrip() + "…"
        return out
    except Exception:  # noqa: BLE001 — extraction never raises
        return ""
