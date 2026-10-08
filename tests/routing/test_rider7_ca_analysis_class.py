"""FIX-FIRST rider 7 — C-A regression tests: analysis-class frontier detection.

Conductor's real turns ("audit the decision envelope for the priority order",
"analyze the lane behavior across this session") scored clear_simple with 0
planning_arch signals — stage-1 had no analysis-class family. C-A adds TIGHT
verb+noun pairs (R14 lesson: never bare verbs) inside _PLANNING_ARCH, which
routes frontier at L2 (planning_arch >= 1 = clear_complex).

Pins:
- conductor-style analysis asks route frontier (planning_arch >= 1)
- benign prose mentioning the nouns without the analysis verb pair stays
  clear_simple (R14 overfiring class stays closed)
- bare analysis verbs with unrelated objects do NOT fire (tight-pair rule)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from hermes_router.complexity import stage1_signals, stage1_verdict  # noqa: E402


def _sig(text: str) -> int:
    s = stage1_signals(text)
    return s["planning_arch"]


def test_conductor_analysis_ask_routes_frontier():
    # real conductor-style turns from the audit (all went clear_simple pre-fix)
    for t in (
        "Audit the decision envelope for the priority order, root causes per item.",
        "Analyze the lane behavior across the session and re-audit the framework.",
        "Conductor envelope audit: examine the session's framework stacking.",
    ):
        assert _sig(t) >= 1, t
        v, _ = stage1_verdict(t, 2)
        assert v == "clear_complex", t


def test_benign_prose_stays_clear_simple():
    # R14 overfiring class: prose that mentions the nouns WITHOUT the tight
    # analysis verb pair must never route.
    for t in (
        "The lane was busy today, the session went fine.",
        "Our framework has a behavioral quirk I noticed while reading docs.",
        "I analyzed my notes on the framework last night and slept well.",
        "The envelope arrived in the mail. The session was pleasant.",
    ):
        assert _sig(t) == 0, t
        v, _ = stage1_verdict(t, 2)
        assert v == "clear_simple", t


def test_bare_analysis_verb_with_unrelated_object_does_not_fire():
    # tight-pair rule: analysis verb + unrelated object is ordinary work
    # prose, not a frontier consult — the noun family is bounded. NOTE:
    # 'audit ... the' shapes are the PRE-EXISTING T1 audit-family bucket
    # (rider-7 scope does not touch them) — this test isolates the C-A pair.
    for t in (
        "Analyze the inventory rows before Friday.",
        "Scrutinize the quarterly summary, please.",
    ):
        assert _sig(t) == 0, t
