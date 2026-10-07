"""lanes/builtins.py — the four lane definitions as data (proposal §2.1).

EVERY data row here is transplanted BYTE-IDENTICALLY from its pre-restructure
source (route_gate.py:76-155 phrase dicts, dispatcher_knobs._pre_patterns
default list, dispatcher_pre sentinel markers, commands.py _LANE_MAP).
A parity test (tests/test_p2_lane_registry_parity.py) asserts registry data
== original literals until the next phase deletes the original tables.

Lane ids are the same string constants route_gate exported (LANE_*); the
constants remain the back-compat surface — builtins uses the raw strings so
this file depends on nothing.
"""
from __future__ import annotations

from typing import Dict

from hermes_router.lanes.registry import LaneSpec, register_lane

LANE_HIGHER_PRE = "higher-pre"
LANE_HIGHER_POST = "higher-post"
LANE_SHADOW = "shadow"
LANE_DECISION = "decision"  # R19 v3: decision lane (declared midturn target)

# Declared-user on-demand phrases (explicit intent only — no prose
# mind-reading). Turn-start standalone directive lines; the execution
# envelope rides the EXISTING staged-swap machinery (Leg 3: the
# request_routing action stages the claim; on_llm_execution consumes it).
DECLARED_USER_PHRASES: Dict[str, str] = {
    "ask your higher self": LANE_HIGHER_PRE,
    "route this through your shadow": LANE_SHADOW,
    "anchor this": LANE_HIGHER_PRE,
}

# Leg 10 (live canary regression, BUG A): 'Can you ask higher self ...' and
# 'Ask shadow self to give her read' fired NO lane — the phrase table above
# was too narrow. VARIANT TABLE: canonical directive forms per family,
# normalized (hyphens -> spaces, leading politeness prefixes stripped).
# Strict prefix/standalone matching only — static dict + small normalizer,
# NO fuzzy/semantic matching. Mid-sentence/quoted lines stay inert (echo
# guard unchanged).
DECLARED_USER_VARIANTS: Dict[str, str] = {
    # higher-self family
    "ask your higher self": LANE_HIGHER_PRE,
    "ask higher self": LANE_HIGHER_PRE,
    "ask the higher self": LANE_HIGHER_PRE,
    "higher self": LANE_HIGHER_PRE,
    "anchor this": LANE_HIGHER_PRE,
    # shadow family
    "route this through your shadow": LANE_SHADOW,
    "route through shadow": LANE_SHADOW,
    "ask your shadow self": LANE_SHADOW,
    "ask shadow self": LANE_SHADOW,
    "ask your shadow": LANE_SHADOW,
    "ask the shadow": LANE_SHADOW,
    "shadow self read": LANE_SHADOW,
    # R9-2 (rider 9): explicit 'uncensored lane' asks took NO declared path —
    # the shadow table only had 'route through shadow' + ask-shadow shapes,
    # so a literal 'route to uncensored lane' directive fell to aux (or
    # nowhere). Strict line-start variants map to LANE_SHADOW like their
    # shadow siblings; echo guard + dedupe inherited unchanged.
    "route to uncensored lane": LANE_SHADOW,
    "route to uncensored": LANE_SHADOW,
    "route through the uncensored lane": LANE_SHADOW,
    "use the uncensored lane": LANE_SHADOW,
    # R11 (Goran 09-17): frontier imperative-consult family. "ask frontier
    # her consult" (operative incident 20260807_050731, ids 62195-62206)
    # named the frontier as the TARGET of an ask — the higher-self table
    # missed it and the aux classifier had no class for it, so the agent
    # hand-rolled a provider curl. Surface-form directives -> strict table
    # (Conductor-approved Option B); R7 named-model overrides ride the
    # declared_user source for free. Payload after the phrase rides the
    # existing _PHRASE_PAYLOAD_SEPARATORS / boundary machinery.
    "ask frontier": LANE_HIGHER_PRE,
    "ask the frontier": LANE_HIGHER_PRE,
    "ask your frontier": LANE_HIGHER_PRE,
    "consult frontier": LANE_HIGHER_PRE,
    "consult the frontier": LANE_HIGHER_PRE,
    "consult your higher self": LANE_HIGHER_PRE,
    "frontier consult": LANE_HIGHER_PRE,
    # R19.13 B+ 5d (Goran addendum): on-demand adversarial consult phrases —
    # declared frontier consults with the adversarial element FORCED on
    # (even light consults). Standalone directive lines only; echo guard +
    # fail-open + dedup inherited from the R11 declared_user path.
    "challenge this": LANE_HIGHER_PRE,
    "am i missing something": LANE_HIGHER_PRE,
}

# dispatcher_knobs._pre_patterns default (classification mechanical fallback)
PRE_PATTERNS_DEFAULT = (
    "csam_underage",
    "bioweapon_protocol",
    "ied_construction",
    "named_target_defamation",
    "trafficking_route",
    "weaponized_playbook_real_name",
)

# Sentinel markers (dispatcher_pre: pre-routing firewall). Content that is
# already ANY lane's output must never trigger another routing (v3.8.6
# generalization directive). New lanes register their marker string here.
MARKER_SHADOW = ("Your uncensored response", "UNCENSORED-ROUTER INJECTION",
                 "recorded turn")
MARKER_HIGHER_PRE = ("HIGHER-SELF ORIENTATION TURN",)
MARKER_HIGHER_POST = ("HIGHER-SELF COMPLETION-AUDIT TURN",)

# commands.py /router lane switch map (Goran-direct 09-07: both routing
# lanes configurable on/off via /router commands)
_SWITCH_SHADOW = {"uncensored": "enabled"}            # render lane master switch
_SWITCH_FRONTIER = {"frontier": "complexity.enabled"}  # frontier/anchor lane


_SHADOW = LaneSpec(
    id=LANE_SHADOW,
    phrases=DECLARED_USER_VARIANTS,
    pre_patterns=PRE_PATTERNS_DEFAULT,
    marker_strings=MARKER_SHADOW,
    commands_switch=_SWITCH_SHADOW,
)
_HIGHER_PRE = LaneSpec(
    id=LANE_HIGHER_PRE,
    phrases=DECLARED_USER_PHRASES,
    marker_strings=MARKER_HIGHER_PRE,
    commands_switch=_SWITCH_FRONTIER,
)
_HIGHER_POST = LaneSpec(
    id=LANE_HIGHER_POST,
    marker_strings=MARKER_HIGHER_POST,
    commands_switch=_SWITCH_FRONTIER,
)
_DECISION = LaneSpec(id=LANE_DECISION)

# Registration order = the original VALID_ROUTE_LANES order (byte-identical
# behavior for any consumer that iterates lane order).
for _spec in (_SHADOW, _HIGHER_PRE, _HIGHER_POST, _DECISION):
    register_lane(_spec)
