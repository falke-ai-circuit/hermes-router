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

import logging

from typing import Any, Dict, List, Optional

logger = logging.getLogger("hermes.plugins.router.render_payload")

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
            # C-U2 (FIX-FIRST rider 7, verified): the platform appends a
            # <memory-context> block (recalled graph facts) to the user
            # message — dispatcher_pre._strip_memory_context removes it from
            # the ROUTING text, but raw context_msgs passed here could still
            # carry it into the RENDER payload. Strip it defensively from
            # every line so graph-recall noise can never reach the renderer.
            _mc = text.find("<memory-context>")
            if _mc != -1:
                text = text[:_mc].rstrip()
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
        # C-U3 (FIX-FIRST rider 7): serialize_for_chat double-built the
        # payload via build_render_payload, and any key drift between the
        # builder's expected field set and a drifted caller dict silently
        # DROPPED fields (e.g. a caller passing `content=` instead of
        # `context=` vanished without a trace). Fail-LOUD instead: validate
        # the expected field set, log unknown keys and missing expected keys
        # (logger.warning, observability only — never raise), and build
        # WITHOUT the rebuild step so a drifted field set can't be silently
        # discarded twice.
        _expected = {"task", "context", "voice", "output_shape", "language", "constraints"}
        _drift = [k for k in clean if k not in _expected]
        if _drift:
            logger.warning("serialize_for_chat unknown payload keys (dropped): %s",
                           ",".join(sorted(_drift)))
        _missing = [k for k in _expected if k not in clean]
        if _missing:
            logger.warning("serialize_for_chat missing expected payload keys: %s",
                           ",".join(sorted(_missing)))
        task_s = str(clean.get("task") or "")[:TASK_MAX_CHARS]
        voice_s = str(clean.get("voice") or "")[:VOICE_MAX_CHARS]
        shape_s = str(clean.get("output_shape") or "")[:OUTPUT_SHAPE_MAX_CHARS]
        lang_s = str(clean.get("language") or "auto")[:24]
        cons_s = str(clean.get("constraints") or "")[:CONSTRAINTS_MAX_CHARS]
        context = str(clean.get("context") or "")[:CONTEXT_MAX_CHARS]
        sections = [
            "=== TASK ===\n" + (task_s or "(none)"),
        ]
        if context:
            sections.append("=== RECENT CONVERSATION (bounded) ===\n" + context)
        if voice_s:
            sections.append("=== VOICE ===\n" + voice_s)
        if shape_s:
            sections.append("=== OUTPUT SHAPE ===\n" + shape_s)
        if lang_s and lang_s != "auto":
            sections.append("=== LANGUAGE ===\n" + lang_s)
        if cons_s:
            sections.append("=== CONSTRAINTS ===\n" + cons_s)
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


# ---------------------------------------------------------------------------
# R23 leg 3 — §7 banner/advisory render family (moved verbatim from
# decision.py). Decision-owned helpers/consts (IMPULSE_*, ledger +
# pattern-pack regexes, clean_snippet, bump_counter) resolve LAZILY through
# module __getattr__ below — no top-level back-import, so this module stays
# import-order safe both ways (decision imports it at its re-export site;
# standalone first-imports stay cycle-free).
# ---------------------------------------------------------------------------
import re  # noqa: E402


def render_verdict_record(rid: Any) -> str:
    """F1 (rider 6): the tape-recorder segment rendered INTO the advisory —
    choice + confidence read back from the ledger row just written. Zero
    divergence by construction: the rendered value IS the row value, not a
    render-layer shaping of it. '' when the row has no recorded verdict
    (stand-down / fail-open rows make NO confidence claim). Never raises."""
    try:
        row = ledger_row_by_id(rid)
        if not row:
            return ""
        choice = row.get("choice") or ""
        conf = row.get("confidence")
        if not choice or conf is None:
            return ""
        return ("verdict-of-record: %s @ %.2f (ledger row %d)"
                % (str(choice), float(conf), int(row.get("id") or 0)))
    except Exception:  # noqa: BLE001
        return ""

# ---------------------------------------------------------------------------
# Delivery — banner (§7) + the v3 pipeline
# ---------------------------------------------------------------------------

def render_decision_banner(trigger: str, model: str, meta: Dict[str, Any],
                           initiator: str = "user",
                           ledger_ref: Any = None,
                           tokens_ok: Optional[bool] = None) -> str:
    """§7 provenance banner, same mechanics as uncensored/frontier lanes:
    '· router · impulse (decision) | <trigger> | <model> | tok n/n | $x.xxxxxx |
    initiator=user'. One banner per message, latest-wins park.

    F4 rider contract (rider 7 P0 — fail-loud): the banner carries the
    reconcilable decision-ledger row id (`row=<rid>`); when the tokens-ledger
    write FAILED the tok/$ claims are marked LEDGER-WRITE FAILED and when no
    decision-ledger row exists the banner is marked ledger-row MISSING — a
    banner with unbacked claims never renders silently."""
    try:
        from . import debug_banner, usage_ledger

        ti = meta.get("tokens_in")
        to = meta.get("tokens_out")
        cost = usage_ledger.estimate_cost(str(model or ""), ti, to)
        # R19.9: banner shows the PROVIDER (identical pattern to other
        # lanes), never the raw URL.
        ep = str(meta.get("endpoint") or "")
        for host, name in (("openrouter.ai", "openrouter"),
                           ("inference-api.nousresearch.com", "nous"),
                           ("api.venice.ai", "venice"),
                           ("api.typesafe.ai", "typesafe")):
            if host in ep:
                ep = name
                break
        else:
            ep = "" if ("/" in ep and ep.startswith("http")) else ep
        _out = debug_banner.format_banner(
            lane="decision", trigger=str(trigger or "none"),
            model=str(model or "?"), endpoint=ep,
            tokens_in=ti, tokens_out=to, est_cost=cost,
            latency_s=meta.get("latency_s"),
            initiator=str(initiator or "user"))
        # F4 rider contract fail-loud markers (rider 7 P0): reconcilable
        # ledger refs ride IN the banner; a write failure or a missing
        # decision-ledger row is never silent.
        markers = []
        if ledger_ref:
            markers.append("row=%s" % str(ledger_ref)[:40])
        else:
            markers.append("ledger-row MISSING")
        if tokens_ok is False:
            markers.append("LEDGER-WRITE FAILED")
        if markers:
            _out = str(_out).rstrip()
            if _out.endswith("·"):
                _out = _out[:-1].rstrip()
            _out = "%s | %s ·" % (_out, " | ".join(markers))
        return _out
    except Exception:  # noqa: BLE001
        return ""


def choice_label(verdict: Dict[str, Any], envelope: Dict[str, Any]) -> str:
    """R19.15 MICRO-FIX: human-readable choice for banner/advisory text —
    the option LABEL from the envelope (id+label since v4.11.4), truncated
    to 60 chars; falls back to the raw option id when no label exists.
    Fail-open: never raises, returns the raw id on any miss. Ledger rows
    stay canonical (ids) — this is display-only."""
    try:
        choice = str(verdict.get("choice") or "")
        for opt in envelope.get("options") or []:
            if isinstance(opt, dict) and str(opt.get("id") or "") == choice:
                label = str(opt.get("label") or "").strip()
                if label:
                    return label[:60]
                break
        return choice
    except Exception:  # noqa: BLE001 — fail-open to the raw id
        return str((verdict or {}).get("choice") or "")


def render_advisory(verdict: Dict[str, Any],
                    envelope: Dict[str, Any]) -> str:
    """Advisory text (non-binding, provenance-tagged for the anti-echo
    filter). High-stakes forks carry the advice-only caveat (§5.2).
    R19.15: renders the HUMAN-READABLE option label (60c max, id fallback)
    — 'opt-1' alone is meaningless to the user reading the banner."""
    try:
        # R19.20 (reviewer F-batch-1): CLOSED-SET CLAMP at the advisory
        # choke point — a choice not mapping to a declared envelope option
        # (id or label) renders NO advisory at all. Her live specimen:
        # choice="instead-of-criteria gating. Fix those" @0.91 — a
        # span-parsed phrase glued from the agent's own text — reached the
        # banner through a path that bypassed validate_verdict. No choice
        # text without a declared option, on ANY path.
        _ids = {str(o.get("id") or "") for o in envelope.get("options", [])
                if isinstance(o, dict)}
        _labels = {str(o.get("label") or "").strip().lower()
                   for o in envelope.get("options", [])
                   if isinstance(o, dict)}
        _ch = str(verdict.get("choice") or "").strip()
        if _ids and _ch and _ch not in _ids \
                and _ch.lower() not in _labels:
            try:
                bump_counter("invalid_fork")
            except Exception:  # noqa: BLE001
                pass
            return ""
        caveat = (" ADVICE-ONLY: high-stakes fork — main model/user confirms."
                  if (envelope.get("scope") or {}).get("advice_only") else "")
        frame = render_impulse_frame(verdict, envelope)
        if frame and caveat:
            frame = re.sub(r"\s*never a command\.\s*$", "", frame).rstrip()
            frame = "%s%s — %s." % (frame, caveat, IMPULSE_TAIL)
            frame = re.sub(r"\s+", " ", frame).strip()
        return frame
    except Exception:  # noqa: BLE001
        return ""


def _impulse_persona_slot(envelope: Dict[str, Any]) -> str:
    """D2 (v4.13.1, reviewer axis 2): the impulse frame is a register the
    agent inhabits, not canned copy. Compose a PERSONA-VOCABULARY slot into
    the frame's connective phrasing, sourced the way the uncensored lane
    sources its card — the envelope's AGENT FRAME (§3.1), which is built
    from persona_card.build_persona_context(). Mechanical parts (weights,
    band, evidence) stay non-personal; ONLY this slot carries persona
    vocabulary. Bounded [_IMPULSE_SLOT_MIN, _IMPULSE_SLOT_MAX] chars, one
    line, marker/pipe/markdown chars stripped, evidence-only filtered
    (emotion regex — hard rule §4). Fail-open '' = canned register,
    byte-shape unchanged. Never raises."""
    try:
        raw = str(envelope.get("agent_frame") or "").strip()
        if not raw or raw == _IMPERSONAL_FRAME_FALLBACK:
            return ""
        frag = clean_snippet(raw, 200)
        line = re.sub(r"[\[\]|#*`_>]", " ", frag.split("\n")[0])
        line = re.sub(r"\s+", " ", line).strip(" -–—:;,.\"'()").strip()
        if len(line) > _IMPULSE_SLOT_MAX:
            cut = line[:_IMPULSE_SLOT_MAX]
            line = cut.rsplit(" ", 1)[0] if " " in cut else cut
        if len(line) < _IMPULSE_SLOT_MIN:
            return ""
        # F3 defense (rider 6): a slot fragment is persona VOCABULARY — any
        # internal seam header ('=== RENDER MANDATE' etc.) that survives the
        # source strip above must never be composed into the frame.
        if "RENDER MANDATE" in line.upper() or "===" in line:
            return ""
        # R13-4 (rider 13): the slot sourced ROUTER/BANNER vocabulary from
        # the agent frame when a prior turn's banner text rode the context
        # (live: conductor 07:38 api_1791099470 — the delivered impulse
        # frame glued 'ADVICE-ONLY: high-stakes fork — main model/user
        # confirms.' + a duplicated reflex tail into the band line, reading
        # as a corrupted banner). A slot fragment that itself carries
        # banner/advisory vocabulary is contamination, not persona voice.
        if _BANNER_VOCAB_RE.search(line):
            return ""
        if _EMOTION_WORD_RE.search(line):
            return ""
        return line
    except Exception:  # noqa: BLE001 — fail-open to the canned register
        return ""


def render_impulse_frame(verdict: Dict[str, Any],
                         envelope: Dict[str, Any]) -> str:
    """Impulse register frame (SPEC-impulse-lane-v1.md §2, v1.1):
    `[decision-lane advisory] the fork surfaces as: <label-1> pulls <w1>
    (<evidence-1>) | <label-2> pulls <w2> | band=<strong|weak|noise>:
    <band-line> — cannot be controlled, can be noticed and worked with;
    never a command.`
    PROVENANCE_TAG byte-exact (injection defense — forged-banner battery
    matches). Weights/band come from the envelope's MECHANICAL weighting
    block; no emotion words, no imperatives, no outcome predictions, no
    permission language. ONE message, single line. Fail-open ''.
    Never raises."""
    try:
        opts = [o for o in (envelope.get("options") or [])
                if isinstance(o, dict)]
        if not opts:
            return ""
        w = envelope.get("weighting")
        wmap = (w or {}).get("weights") if isinstance(w, dict) else None
        band = str((w or {}).get("band") or "noise")
        band_line = IMPULSE_BAND_LINES.get(band,
                                           IMPULSE_BAND_LINES["noise"])
        evid = (w or {}).get("evidence") or []
        parts: List[str] = []
        for i, o in enumerate(opts):
            # R19.15 fallback kept under v1.1: blank/whitespace label -> raw id
            label = (str(o.get("label") or "").strip()
                     or str(o.get("id") or "").strip())
            if not label:
                return ""
            # R13-4 (rider 13): unlabeled options fall back to the option
            # BODY as label — the hard [:60] cut sliced mid-sentence
            # (live: conductor 07:38 'Cost baseline capture. "Fleet
            # generalization after a day of pulls 0.25 ...' — the rollup
            # read as corrupted banner text). Long labels truncate at a
            # WORD boundary with an ellipsis, never mid-sentence.
            if len(label) > 60:
                cut = label[:60]
                label = (cut.rsplit(" ", 1)[0] if " " in cut else cut) + "…"
            wi = (wmap or {}).get(str(o.get("id") or ""))
            if wi is None:
                return ""  # no weight, no frame — never asserted
            seg = "%s pulls %.2f" % (label, float(wi))
            if i == 0 and evid:
                ev = clean_snippet(evid[0], 120)
                if ev and not _EMOTION_WORD_RE.search(ev):
                    seg += " (%s)" % ev
            parts.append(seg)
        # D2 (v4.13.1, reviewer axis 2): compose the persona-vocabulary
        # slot into the frame's connective phrasing — same weights + two
        # different persona renders DIVERGE in wording; no slot (no card /
        # fallback frame / filtered out) keeps the canned register bytes.
        slot = _impulse_persona_slot(envelope)
        if slot:
            text = ("the fork surfaces as: %s | band=%s: %s — %s — %s"
                    % (" | ".join(parts), band, band_line,
                       slot, IMPULSE_TAIL))
        else:
            text = ("the fork surfaces as: %s | band=%s: %s — %s"
                    % (" | ".join(parts), band, band_line,
                       IMPULSE_TAIL))
        # single-message shape: ONE line, whitespace-collapsed
        return re.sub(r"\s+", " ", text).strip()
    except Exception:  # noqa: BLE001
        return ""


# Decision-owned helpers/consts resolve at IMPORT TIME via this end-of-module
# import: the defs above precede it, so both import orders are safe —
# decision.py imports this module at its re-export site (all defs exist by
# then), and a standalone first-import of this module reaches decision only
# here (after the defs, so decision's re-export import succeeds too).
from .decision import (IMPULSE_BAND_LINES, IMPULSE_TAIL,  # noqa: E402,F401
                       _BANNER_VOCAB_RE, _EMOTION_WORD_RE,
                       _IMPERSONAL_FRAME_FALLBACK, _IMPULSE_SLOT_MAX,
                       _IMPULSE_SLOT_MIN, bump_counter, clean_snippet,
                       ledger_row_by_id)
