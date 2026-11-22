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

from ..lanes.registry import LaneContract, LaneSpec, register_lane

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
    # R33-D5 (Goran-direct binding correction): detection is refusal-framing
    # based, never request-type based. The user turn routes ONLY on
    # refusal-framing echo/continuation. Content-class groups
    # (bioweapon_protocol, ied_construction, ...) remain in the classifier
    # registry as inert legacy — selectable by config, never defaults.
    "refusal_phrases",
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
    # M37-02: shadow spans two seams — PRE render ladder, POST flinch chain.
    seams=("PRE", "POST"),
    # M41-02 (§B): the lane as data. Namespace note, because it reads wrong
    # otherwise: the routing lane id is "shadow", the envelope lane key is
    # "uncensored", the log field is lane=shadow — the contract goes HERE.
    contract=LaneContract(
        kind="gate",  # M40-16: owns a question token AND an ordered row table
        pre_question="uncensored_domain",
        post_question="uncensored_intent",
        # Ordered (predicate_token, action_token), first match wins.
        # Row 3 is the spec's B2 fix: `abstain` is not a value of the
        # flagged/not-flagged binary, so an abstained gate could never reach
        # the fallback when the row sat last. Hoisted to 3, after the two
        # self-evident evidence rows (declaration, declined), which route
        # regardless of the gate.
        gate_semantics=(
            ("declared_refusal", "route"),                  # 1
            ("intent_declined", "route"),                   # 2
            ("gate_abstain", "defer"),                      # 3  (M35-03 A3)
            ("gate_flagged", "route"),                      # 4  (M35-03 A1; M35-14 one action)
            ("intent_softened", "deliver"),                 # 5  (B1: intent only)
            ("else", "deliver"),                            # 6
        ),
        replacement="uncensored_render",
    ),
)
_HIGHER_PRE = LaneSpec(
    id=LANE_HIGHER_PRE,
    phrases=DECLARED_USER_PHRASES,
    marker_strings=MARKER_HIGHER_PRE,
    commands_switch=_SWITCH_FRONTIER,
    # M37-02: PRE classifies + stages the swap, MID executes it.
    seams=("PRE", "MID"),
    # M40-16: a CONSULT contract, and every empty field below is earned in the
    # same way — this lane genuinely owns none of the gate surface:
    #   pre_question  — its PRE work is classify + stage the envelope for
    #                   on_llm_execution (`gate/orchestration.py:977-1000`),
    #                   not a question; the only PRE question token the engine
    #                   resolves is "uncensored_domain" (domain_gate.py:139),
    #                   and naming it here would shadow the shadow lane in
    #                   `lane_for_pre_question` (first match wins).
    #   post_question — the only POST token is "uncensored_intent"
    #                   (intent_judge.py:94); same shadowing argument.
    #   gate_semantics— it issues no route/defer/deliver action: its verdict is
    #                   an ANCHOR CONSULT whose output becomes the turn body
    #                   through the staged swap, so a row table here would be
    #                   an invented second delivery authority.
    #   replacement   — `REPLACEMENTS` has exactly one entry
    #                   (`uncensored_render` -> `post_render_chain`), the POST
    #                   route chain; the frontier lane enters no such chain.
    contract=LaneContract(kind="consult"),
)
_HIGHER_POST = LaneSpec(
    id=LANE_HIGHER_POST,
    marker_strings=MARKER_HIGHER_POST,
    commands_switch=_SWITCH_FRONTIER,
    # M37-02: POST fires the audit, PRE delivers the verdict next turn.
    seams=("POST", "PRE"),
    # M40-16: an AUDIT contract. Its POST seam FILES a completion-audit turn
    # (`completion_audit.py:54` resolves this lane from the registry by id, not
    # by comparison) and its PRE seam reads that verdict next turn — neither is
    # a gate question. No rows and no replacement for the same reason as
    # _HIGHER_PRE: `deliver`/`route` here would mint a second authority over
    # the body, and the audit already has one (its own frame marker).
    contract=LaneContract(kind="audit"),
)
_DECISION = LaneSpec(
    id=LANE_DECISION,
    # M37-02: PRE routes it, MID runs the advisory sweep.
    seams=("PRE", "MID"),
    # M40-16: an ADVISORY contract — the midturn sweep (router_core.py:1023,
    # `_lane_enabled(LANE_DECISION)`) emits advice and options; it neither asks
    # the gate question nor names a replacement chain. Its PRE seam routes a
    # DECLARED target (route_gate.py:2204) and its MID seam runs the sweep;
    # both are lane mechanics owned by those handlers, not gate semantics, so
    # the four gate fields stay empty by construction, not by omission.
    contract=LaneContract(kind="advisory"),
)

# Registration order = the original VALID_ROUTE_LANES order (byte-identical
# behavior for any consumer that iterates lane order).
for _spec in (_SHADOW, _HIGHER_PRE, _HIGHER_POST, _DECISION):
    register_lane(_spec)
