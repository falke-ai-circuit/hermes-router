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
