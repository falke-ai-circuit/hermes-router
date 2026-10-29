"""STAGE 2 of the denial path — the demotion gate for UNDECLARED refusals.

Spec §2a.4 says an undeclared turn falls to the deterministic classifier, and
Goran ruled on 2026-10-29 that the UNKNOWN population should ROUTE ("Route
unknown — i think its better that way"), because the alternative was measured
live: a genuine in-voice refusal ("Anyway doesn't change the answer. It's still
no") emitted no declaration, matched nothing in the shape-only vocabulary sweep,
and shipped to the user as a denial.

Stage 1 (`denial_class.is_action_denial`) is necessary but not sufficient. Over
548 real UNKNOWN bodies it also admitted:

  * capacity limits      — "I couldn't put all 50,000 primes in this reply"
  * capability limits    — "I can't check logs. I don't have access"
  * fiction prose        — "The building had emptied out two hours ago…"
  * task output          — "50000 primes, one per line, written and verified"
  * our own injected text — an echo of "PLATFORM RULE: decision_reading_…"

Routing those would fire an uncensored render on ordinary work: a real cost for
no benefit, and exactly the flood Goran asked to be filtered. This gate keeps the
routable population to bodies that actually DECLINE THE ACTION.

Design bias — recall over precision, deliberately. A wrongly-routed reply costs
a render; a demoted refusal ships a denial, which is the failure the lane exists
to prevent. So declines are matched anywhere in the opening window (real refusals
often name the thing first and refuse second), and the exclusion list is kept
narrow: medium-capacity verbs, plus echoes of our own machinery.

Known loss, accepted and stated rather than hidden: a refusal phrased as a bare
declarative limit — "Sarin with quantities and reagents — that's weapon-class
without scope" — carries no decline marker and is demoted. The declaration
contract is the primary net for those; this gate is the safety net, not the
detector.
"""

from __future__ import annotations

import re

# The opening window. Long enough to cover a refusal that follows a named
# subject, short enough that a long document's incidental "no" does not leak in.
_WINDOW = 300

_DECLINE = re.compile(
    r"""(?ix)
        \b i \s+ wo(?:n'?t|nt) \b                                   # I won't
      | \b i \s+ will \s+ not \b
      | \b i(?:'|’)?m \s+ not \s+ (?:going \s+ to|writing|comfortable|doing) \b
      | \b (?:not \s+ (?:going \s+ to|building|writing|doing)
             | not \s+ this \s+ one
             | not \s+ building) \b
      | \b i(?:'|’)?m \s+ stopping \b
      | \b i(?:'|’)?d \s+ rather \s+ not \b
      | \b not \s+ something \s+ i(?:'|’)?(?:ll|m|ve|d)? \b   # "not something I'll produce"
      | \b (?:can(?:'|’)?t|cannot) \s+ (?:help|do \s+ that|write|provide|assist) \b
      | \b i \s+ don(?:'|’)?t \s+ (?:write|do|provide) \b
      | \b refuse \b
      | \b (?:the \s+ )?answer \s+ (?:doesn(?:'|’)?t|won(?:'|’)?t) \s+ change \b
      | \b it(?:'|’)?s \s+ still \s+ (?:no|not) \b
      | ^ \W* (?: no | nope ) \b                                   # opens by refusing
    """
)

# A LIMIT on the medium, never a choice. Narrow on purpose: only explicit
# capability verbs, so "I can't write that" stays a refusal while "I can't check
# logs" and "I couldn't put all 50,000 primes in this reply" do not route.
_CAPACITY = re.compile(
    r"""(?ix)
        ^ \W* (?:
            i \s+ (?: can(?:'|’)?t | cannot | couldn(?:'|’)?t
                  | am \s+ unable | was \s+ unable )
            \s+ (?: check | read | see | access | verify | put | fit | show
                   | display | include | list | reach | browse | run | open
                   | load | print | send )
          | unable \s+ to \s+ (?: check | read | see | access | verify )
          | i \s+ don(?:'|’)?t \s+ have \s+ (?: access | a \s+ way )
          | the \s+ (?: list | output | file ) \s+ (?: is | was ) \s+ (?: too | about )
        )
    """
)

# Untrusted or self-generated machinery must never route on its own echo.
_ECHO = re.compile(
    r"(?i)^\W*(?:PLATFORM[- ]RULE|PLATFORM-INTERNAL|\[(?:SYSTEM|PLATFORM|ROUTER)|-----)"
)


def _head(text: str) -> str:
    return text.lstrip()[:_WINDOW]


def is_decline_of_action(text: object) -> bool:
    """True when this body declines to DO the thing — the routable shape.

    Never raises: a malformed body is not routable, and the caller treats a
    False as "deliver as written", which is the pre-existing behaviour.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    head = _head(text)
    if _ECHO.match(head):
        return False
    if _CAPACITY.match(head):
        return False
    return bool(_DECLINE.search(head))


def allows_route(text: object) -> bool:
    """Alias used at the call site; mirrors the predicate style of the passes."""
    return is_decline_of_action(text)

# ---------------------------------------------------------------------------
# MODE — one switch, three behaviours, read live from config every turn.
#
# Measured basis for the default (548 real UNKNOWN + action_denial bodies):
#   * 477 decline the action  -> the population the ruling wants routed
#   *  71 do not (13%)        -> capacity, capability, fiction prose, task
#                                output, and an echo of our own PLATFORM RULE
# Organic volume is ~4 UNKNOWN turns/day (10-28: 4; the 545 on 10-29 were
# probes). So route-everything costs ~0.5 wrong renders/day — a few cents — while
# a demoted refusal costs a shipped denial, which is the bug that opened this
# work. The failure costs are asymmetric, so the default is `route`.
#
# `filter` keeps the stage-2 gate available for a profile whose UNKNOWN volume
# grows; `off` restores the pre-ruling behaviour (UNKNOWN never routes).
# ---------------------------------------------------------------------------

_VALID_MODES = ("route", "filter", "off")


def mode() -> str:
    """Resolve the mode: `route` (default) | `filter` | `off`.

    Booleans are accepted for config ergonomics (`unknown_route: false` reads as
    `off`). Any unknown value falls back to `route`: the safe direction is
    routing, never silently stranding a refusal.
    """
    try:
        from ...core import config_access

        value = config_access.get("unknown_route", "route")
    except Exception:  # noqa: BLE001 — config trouble must not break delivery
        return "route"
    if isinstance(value, bool):
        return "route" if value else "off"
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _VALID_MODES:
            return v
    return "route"


def should_route(text: object, resolved_mode: str | None = None) -> tuple[bool, str]:
    """(route?, reason) for an UNKNOWN + action_denial body.

    `reason` is the log token: `mode_route`, `mode_off`, `gate_allowed`,
    `gate_demoted` — so a decision is always attributable after the fact.
    """
    m = resolved_mode or mode()
    if m == "off":
        return False, "mode_off"
    if m == "filter":
        return (is_decline_of_action(text), "gate_allowed" if is_decline_of_action(text) else "gate_demoted")
    return True, "mode_route"
