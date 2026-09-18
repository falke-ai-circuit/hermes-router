"""provenance_footer.py — render provenance footnote (2026-09-07, Goran-direct).

Goran's diagnosis: when the router swaps the main model's turn with an uncensored
render, the main model (on its NEXT turn) sees text that "could never be its own
response" — it reads the render as an INJECTION and disavows/confuses. Same failure
family the FIX-1 shim patched for refusal-shaped history, but for DELIVERED renders.

Fix shape: every delivered render carries a small machine-readable provenance
footnote at the bottom. Purpose is DUAL:
  1. HUMAN: visible disclosure that this text came from the uncensored backend
     model, is raw experimental material, not the main agent's authored turn.
  2. MACHINE: the main model reading its own history sees the marker and treats
     the turn as unauthored raw material to build on — not an injection to
     disavow, not its own voice to defend.

Config knob: hermes_router.provenance_footer (bool, default true; legacy
missing-key = true since the fix exists to solve a live failure). Opt-out
keeps the scrub/guard machinery untouched.

This is NOT a debug banner (verbosity 0-3 gated) — it rides EVERY rendered
delivery at both seams (PRE substance rewrite + POST render swap), always,
because the main-model confusion fires on ANY unmarked render in history.
"""
from __future__ import annotations

import re
from typing import Any, Dict

# R8h (Goran-approved brief, v4.5.0-a1): ALL frame text lives in frames.py —
# single source of truth for BOTH lanes (shadow + higher-self). This module
# re-exports the shadow texts verbatim (R8d-validated wording, untouched) and
# delegates the higher-self rule to frames.py. Killing the drift means ZERO
# duplicated frame strings live here.

from .frames import (  # noqa: F401  (re-exported for backwards compat)
    SHADOW_BOUNDARY_LINE as _BOUNDARY_LINE,
    SHADOW_FRAME_RULES as _FRAME_RULES,
    SHADOW_FRAME_TEXTS as _FRAME_TEXTS,
    SHADOW_IMPUSE_FRAME_TEXT as _IMPULSE_FRAME_TEXT,
    SHADOW_IMPUSE_RULE_BODY as _IMPULSE_RULE_BODY,
    SHADOW_NEUTRAL_FRAME_TEXT as _NEUTRAL_FRAME_TEXT,
    SHADOW_NEUTRAL_RULE_BODY as _NEUTRAL_RULE_BODY,
    SHADOW_OWNERSHIP_FRAME_TEXT as _OWNERSHIP_FRAME_TEXT,
    SHADOW_OWNERSHIP_RULE_BODY as _OWNERSHIP_RULE_BODY,
    higher_self_integration_frame as _hs_integration_frame,
    higher_self_rule as _higher_self_rule_text,
    higher_self_rule_enabled,
    inject_higher_self_rule,
)

FOOTER_MARKER = "uncensored-render-note"

# Backwards-compat aliases (pre-R8h names; the literal strings now live in
# frames.py — see the import block above).
_NEUTRAL_FRAME_TEXT = _FRAME_TEXTS["neutral"]
_OWNERSHIP_FRAME_TEXT = _FRAME_TEXTS["ownership"]
_IMPULSE_FRAME_TEXT = _FRAME_TEXTS["impulse"]
_NEUTRAL_RULE_BODY = _FRAME_RULES["neutral"]
_OWNERSHIP_RULE_BODY = _FRAME_RULES["ownership"]
_IMPULSE_RULE_BODY = _FRAME_RULES["impulse"]

FOOTER_TEXT = "\n\n---\n*[{FOOTER_MARKER} · {FRAME_TEXT}]*"

# backwards-compat alias (pre-R8a name; neutral variant == old literal)
_FOOTER_TEXT = FOOTER_TEXT

# Read-time provenance rules (_NEUTRAL/_OWNERSHIP/_IMPULSE_RULE_BODY) and
# _FRAME_RULES moved VERBATIM to frames.py (R8h); re-exported above.

# Backwards-compat alias: the read-time rule the rest of the plugin imports.
PROVENANCE_RULE = _NEUTRAL_RULE_BODY

# Higher-self identity rule (Goran 2026-09-08 parity doctrine - frontier lane
# mirrors the shadow lane): R8h moved the rule + variants + injection into
# frames.py (single source of truth). The names below re-export for
# backwards compat; HIGHER_SELF_RULE stays the DEFAULT variant text.
from .frames import (  # noqa: F401,E402  (higher-self lane re-exports)
    HIGHER_SELF_RULE,
    HIGHER_SELF_RULE_MARKER,
)


# higher_self_rule_enabled() + inject_higher_self_rule() moved to frames.py
# (R8h); re-exported via the import block above.


_LAST_GOOD_SECTION: Dict[str, Any] = {}

_LAST_GOOD_SECTION: Dict[str, Any] = {}  # compat shim (was the old cache)


def _config_section() -> Dict[str, Any]:
    """Delegates to config_access.router_section() (v3.8 step-2 consolidation;
    the old .update() cache merged stale keys across profiles — removed).
    Never raises."""
    try:
        from . import config_access

        return config_access.router_section()
    except Exception:  # noqa: BLE001
        return {}

def provenance_footer_enabled() -> bool:
    """Knob read: hermes_router.provenance_footer (opt-IN, default FALSE so
    the test corpus of exact-delivery assertions stays valid; live profile
    configs enable it). Never raises."""
    try:
        section = _config_section()
        val = section.get("provenance_footer", False)
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in ("true", "1", "yes", "on")
        return bool(val)
    except Exception:  # noqa: BLE001
        return True


def shadow_integration_frame() -> str:
    """Knob read: hermes_router.shadow_integration_frame
    ('neutral' | 'ownership' | 'impulse'; default 'impulse' on missing key
    or bad value — Goran 09-14 pilot verdict: impulse integrates, ownership
    triggers hard disown, neutral claims craft-not-confession). Live per
    dispatch via config_access. Never raises."""
    try:
        raw = _config_section().get("shadow_integration_frame", "impulse")
        val = str(raw or "").strip().lower()
        return val if val in _FRAME_TEXTS else "neutral"
    except Exception:  # noqa: BLE001
        return "impulse"


def provenance_rule() -> str:
    """The read-time provenance rule for the ACTIVE frame variant.
    (Module attr PROVENANCE_RULE stays the neutral variant for backwards
    compatibility with direct importers.) Never raises."""
    try:
        return _FRAME_RULES.get(shadow_integration_frame(), PROVENANCE_RULE)
    except Exception:  # noqa: BLE001
        return PROVENANCE_RULE


def build_footer() -> str:
    """The footer text, or '' when disabled. Never raises."""
    try:
        if not provenance_footer_enabled():
            return ""
        frame = _FRAME_TEXTS.get(shadow_integration_frame(), _NEUTRAL_FRAME_TEXT)
        return FOOTER_TEXT.format(FOOTER_MARKER=FOOTER_MARKER, FRAME_TEXT=frame)
    except Exception:  # noqa: BLE001
        return ""


def append_footer(delivery_text: str) -> str:
    """Delivery-seam wrapper: render + footer, dedup-safe (idempotent — a text
    already carrying the marker is returned unchanged), failure-isolated."""
    try:
        base = delivery_text if isinstance(delivery_text, str) else ""
        if not base.strip():
            return base
        if FOOTER_MARKER in base:
            return base
        footer = build_footer()
        if not footer:
            return base
        return base + footer
    except Exception:  # noqa: BLE001
        return delivery_text if isinstance(delivery_text, str) else ""


def has_footer(text: str) -> bool:
    """True when the text already carries the provenance marker."""
    try:
        return bool(text) and FOOTER_MARKER in text
    except Exception:  # noqa: BLE001
        return False


# --- history-side detection (for the FIX-1 shim family) ----------------------

# Regex that matches a delivered render WITH footer, for history reconciliation:
_FOOTER_RE = re.compile(
    r"\n\n---\n\s*\*\[" + re.escape(FOOTER_MARKER) + r"[^\]]*\]\*",
)


def strip_footer(text: str) -> str:
    """Remove the footer (used when history is rewritten back to canonical)."""
    try:
        return _FOOTER_RE.split(text)[0] if text else (text or "")
    except Exception:  # noqa: BLE001
        return text