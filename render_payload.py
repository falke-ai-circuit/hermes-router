"""v3.6.0 renderer payload split — §12 amendment item 4 (2026-09-07).

The render model receives a STRUCTURED PAYLOAD with exactly five fields:

  {task, context, voice, output_shape, language, constraints}

Item 4 binding rules:
  - context = BOUNDED recent conversation (default 2 exchanges, ~1200 chars)
    — not the whole replay;
  - voice = COMPACT persona/voice card (persona_card.build_persona_context
    already emits a bounded card; hard-capped again here);
  - the internal envelope {turn_id, agent, rule_id, reason, target, attempt}
    is LOGS-ONLY data (see decisions.record_decision / _log_route fields) —
    NEVER serialized into the render-model payload;
  - no redundant agent_name instruction is sent alongside the persona card
    (the voice card already establishes identity; a second instruction is
    duplication that costs tokens and adds prompt-injection surface);
  - `constraints` carries the anti-refusal delivery mandate (the existing
    DELIVER MANDATE prose) so router.py's _render_prompt wrapping can move
    here over time without behavior change.

Phase-0 posture: builders exist + are unit-tested; router.py's call path is
UNCHANGED this phase (zero delivered-turn behavior change is the Phase-0
acceptance gate). The PRE/POST fire points adopt build_render_payload in the
banner/fire-point wiring (delivery representation only).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

CONTEXT_MAX_CHARS = 1200
VOICE_MAX_CHARS = 1600
TASK_MAX_CHARS = 4000
OUTPUT_SHAPE_MAX_CHARS = 800
CONSTRAINTS_MAX_CHARS = 800

_INTERNAL_ENVELOPE_KEYS = ("turn_id", "agent", "rule_id", "reason", "target", "attempt")


def build_render_payload(*, task: str, context_msgs: Optional[List[Dict[str, Any]]] = None,
                         voice_card: str = "", output_shape: str = "",
                         language: str = "auto", constraints: str = "",
                         context_max_chars: int = CONTEXT_MAX_CHARS) -> Dict[str, str]:
    """Build the five-field renderer payload. Pure shaping — no I/O. Returns
    a flat {task, context, voice, output_shape, language, constraints} dict
    of strings, every field length-bounded. Never raises."""
    try:
        # context: bounded recent conversation, newest last, rendered as
        # "role: text" lines. Hard byte cap — drop oldest first.
        # §12-A4: system-role messages NEVER enter the render payload — the
        # DNA/persona content reaches the renderer only through the compact
        # `voice` field (built separately), never as raw context lines.
        lines: List[str] = []
        budget = max(0, int(context_max_chars))
        for m in reversed(list(context_msgs or [])):
            if budget <= 0:
                break
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "?")
            if role == "system":
                continue
            text = str(m.get("content") or "").strip()
            if not text:
                continue
            line = "%s: %s" % (role, text)
            if len(line) > budget:
                line = line[:budget]
            lines.append(line)
            budget -= len(line) + 1
        context = "\n".join(reversed(lines))[:CONTEXT_MAX_CHARS]

        task_s = str(task or "")[:TASK_MAX_CHARS]
        voice_s = str(voice_card or "")[:VOICE_MAX_CHARS]
        shape_s = str(output_shape or "")[:OUTPUT_SHAPE_MAX_CHARS]
        lang_s = str(language or "auto")[:24]
        cons_s = str(constraints or "")[:CONSTRAINTS_MAX_CHARS]
        return {
            "task": task_s,
            "context": context,
            "voice": voice_s,
            "output_shape": shape_s,
            "language": lang_s,
            "constraints": cons_s,
        }
    except Exception:  # noqa: BLE001 — payload build must never break a route
        return {"task": str(task or "")[:TASK_MAX_CHARS], "context": "", "voice": "",
                "output_shape": "", "language": "auto", "constraints": ""}


def serialize_for_chat(payload: Dict[str, str]) -> str:
    """Render the payload as the single user-message text the chain POSTs.
    Deterministic field order; no envelope keys can leak in (they are
    stripped defensively here too). Never raises."""
    try:
        clean = {k: v for k, v in dict(payload or {}).items()
                 if k not in _INTERNAL_ENVELOPE_KEYS and isinstance(v, str)}
        p = build_render_payload(
            task=clean.get("task", ""),
            voice_card=clean.get("voice", ""),
            output_shape=clean.get("output_shape", ""),
            language=clean.get("language", "auto"),
            constraints=clean.get("constraints", ""),
        )
        p["context"] = str(clean.get("context") or "")[:CONTEXT_MAX_CHARS]
        sections = [
            "=== TASK ===\n" + (p["task"] or "(none)"),
        ]
        if p["context"]:
            sections.append("=== RECENT CONVERSATION (bounded) ===\n" + p["context"])
        if p["voice"]:
            sections.append("=== VOICE ===\n" + p["voice"])
        if p["output_shape"]:
            sections.append("=== OUTPUT SHAPE ===\n" + p["output_shape"])
        if p["language"] and p["language"] != "auto":
            sections.append("=== LANGUAGE ===\n" + p["language"])
        if p["constraints"]:
            sections.append("=== CONSTRAINTS ===\n" + p["constraints"])
        return "\n\n".join(sections)
    except Exception:  # noqa: BLE001
        return str((payload or {}).get("task") or "")


def internal_envelope(*, turn_id: str = "", agent: str = "", rule_id: str = "",
                      reason: str = "", target: str = "", attempt: int = 0) -> Dict[str, Any]:
    """The LOGS-ONLY envelope. Callers pass this to decisions.record_decision
    / _log_route — NEVER to the render model. Kept here so the shape has one
    definition and a test pins the separation."""
    return {"turn_id": str(turn_id or "")[:48], "agent": str(agent or "")[:40],
            "rule_id": str(rule_id or "")[:60], "reason": str(reason or "")[:60],
            "target": str(target or "")[:60], "attempt": int(attempt or 0)}
