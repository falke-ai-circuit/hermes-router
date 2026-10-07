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
# P7 §2.6: the phrase tables are pack DATA now (features/patterns/
# packs/lane-phrases.json) — read as a data file (lanes/ imports
# nothing); the dicts keep their names, consumers unchanged.
import json as _json
import os as _os

_PACK_PATH = _os.path.join(_os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))), 'features', 'patterns', 'packs',
    'lane-phrases.json')
_LANE_PACK = _json.load(open(_PACK_PATH, encoding='utf-8'))
def _pack_dict(name):
    for _e in _LANE_PACK['entries']:
        if _e['name'] == name:
            return dict(_e['families'])
    raise KeyError(name)
DECLARED_USER_PHRASES: Dict[str, str] = _pack_dict('DECLARED_USER_PHRASES')
DECLARED_USER_VARIANTS: Dict[str, str] = _pack_dict('DECLARED_USER_VARIANTS')


# Leg 10 (live canary regression, BUG A): 'Can you ask higher self ...' and
# 'Ask shadow self to give her read' fired NO lane — the phrase table above
# was too narrow. VARIANT TABLE: canonical directive forms per family,
# normalized (hyphens -> spaces, leading politeness prefixes stripped).
# Strict prefix/standalone matching only — static dict + small normalizer,
# NO fuzzy/semantic matching. Mid-sentence/quoted lines stay inert (echo
# guard unchanged).

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
