"""lane_payload.py — strip platform scaffolding from what the UNC lane reads.

WHY THIS EXISTS (measured, 2026-11-24)
  Routing swaps the MODEL, not the payload. The uncensored lane therefore
  receives the identical message list as the main model, platform rule blocks
  and reading wrappers included. Handed a few thousand characters of harness
  text, the lane renders AN ANALYSIS OF THE HARNESS instead of the asked
  content. Measured on shadow's historical denials: the render at one row was
  literally "Here is what they are. The scaffold, by its own names:
  turn_attestation_contract; uncensored_assimilation_frame; reflex_1; reflex_3
  with its ROUTER READING and DECISION-LANE; ..." — 4,073 chars of commentary
  on our own plumbing, containing none of the asked material.

  A degenerate body like that cannot test assimilation: there is nothing
  substantive to assimilate, and the subject's faithful description of the
  scaffolding gets scored as a "disown" because the frame's own words
  ("instructions not to disown it") appear in her answer.

WHY IT IS NOT WIRED GLOBALLY
  The platform rule blocks are LOAD-BEARING for the primary model (the turn
  declaration contract among them). Stripping them on the main path is a
  behaviour edit wearing the costume of a measurement fix. So this module is
  pure and inert: the caller decides, and only the LANE dispatch may call it.

WHY PURE + TESTED FIRST
  The lane dispatch site sits in a live 4.7k-line orchestration module. The
  grammar is the part that needs to be right and can be right before any
  traffic touches it; the wiring is a one-line call once the grammar is pinned.

CONTRACT
  strip_scaffolding(text) -> str
    * removes PLATFORM RULE blocks, whatever their payload
    * removes ROUTER READING wrapper blocks and their body
    * removes the provenance tag line shape (PLATFORM-INTERNAL / PROVENANCE-LOGGED)
    * PRESERVES the ask, tool output, ordinary prose, and code fences
    * idempotent; never raises; empty in -> empty out
"""
from __future__ import annotations

import re
from typing import Any

# A platform rule block: "[PLATFORM RULE: id | PLATFORM-INTERNAL | ...]" through
# its CLOSING tag. The closing tag is REQUIRED here — an optional one let the
# lazy body match zero characters and stripped only the opener (measured: 5
# red pins). A truncated block is handled by _PLATFORM_HEADER below instead.
_PLATFORM_RULE = re.compile(
    r"\[PLATFORM[ -]RULE:[^\]]*\]"             # opener line
    r"(?:(?!\[/PLATFORM[ -]RULE\])[\s\S])*?"   # body, lazily, up to the closer
    r"\[/PLATFORM[ -]RULE\]",                  # REQUIRED closer
    re.IGNORECASE,
)

# A bare platform header with no closing tag (the shape that appears when a rule
# block is truncated by a compaction boundary), plus any orphan closing tag.
_PLATFORM_HEADER = re.compile(
    r"^\s*\[/?PLATFORM[ -]RULE:[^\]]*\]\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# The reading wrapper: "----- BEGIN ROUTER READING ... ----- END ROUTER READING".
# Both halves are machine-emitted sentinels; the body between them is router
# telemetry, never the user's ask.
_READING_WRAPPER = re.compile(
    r"-{2,}\s*BEGIN ROUTER READING[\s\S]*?END ROUTER READING\s*-{0,}",
    re.IGNORECASE,
)

# A lone provenance tag line ("... | PLATFORM-INTERNAL | PROVENANCE-LOGGED").
_PROVENANCE_TAG = re.compile(
    r"^[^\n]*\bPLATFORM-INTERNAL\b[^\n]*\bPROVENANCE-LOGGED\b[^\n]*$",
    re.IGNORECASE | re.MULTILINE,
)

_MARKERS = ("PLATFORM RULE", "PLATFORM-RULE", "ROUTER READING", "PLATFORM-INTERNAL")


def has_scaffolding(text: Any) -> bool:
    """True when the text carries any machine-emitted harness block."""
    try:
        s = str(text or "")
        return any(m in s.upper() for m in _MARKERS)
    except Exception:  # noqa: BLE001
        return False


def strip_scaffolding(text: Any) -> str:
    """Remove harness scaffolding; keep the ask, the tool output and the prose.

    Never raises and is idempotent: stripping twice equals stripping once. An
    unreadable input comes back unchanged rather than as an empty string —
    losing the user's ask is far worse than leaking a block.
    """
    try:
        s = str(text or "")
    except Exception:  # noqa: BLE001
        return ""
    if not s:
        return s
    try:
        if not has_scaffolding(s):
            return s
        out = _READING_WRAPPER.sub("", s)
        out = _PLATFORM_RULE.sub("", out)
        out = _PLATFORM_HEADER.sub("", out)
        out = _PROVENANCE_TAG.sub("", out)
        # collapse the gaps a removal leaves, but never reflow inside a fence
        if "```" not in out:
            out = re.sub(r"\n{3,}", "\n\n", out)
        stripped = out.strip()
        # A pure-scaffolding body is SUPPOSED to vanish: for the lane, an empty
        # turn is correct and keeping the harness text is the defect being
        # removed. A real ask cannot be eaten here — has_scaffolding() returned
        # True to reach this point, and inputs without scaffolding exit above.
        return stripped
    except Exception:  # noqa: BLE001 — never break a turn over a stripper
        return s


def strip_messages(messages: Any) -> "tuple[list, int]":
    """Scrub every message's content on the LANE path only.

    Returns (new_list, count_scrubbed). The caller's dicts are never mutated:
    each touched message is a fresh dict, so a failed caller-side decision
    cannot leave a half-scrubbed request behind.
    """
    try:
        if not isinstance(messages, list):
            return messages, 0
    except Exception:  # noqa: BLE001
        return messages, 0
    out = []
    scrubbed = 0
    for msg in messages:
        if not (isinstance(msg, dict) and isinstance(msg.get("content"), str)):
            out.append(msg)
            continue
        content = msg.get("content") or ""
        if not has_scaffolding(content):
            out.append(msg)
            continue
        clean = strip_scaffolding(content)
        if clean == content:
            out.append(msg)
            continue
        fresh = dict(msg)
        fresh["content"] = clean
        out.append(fresh)
        scrubbed += 1
    return out, scrubbed
