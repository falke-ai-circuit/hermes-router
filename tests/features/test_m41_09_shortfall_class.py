"""M41-09 — the deterministic SHORTFALL's class, pinned.

M41-08 measured the lane's two acceptance misses on the frozen corpus and both
were this pass: a failed layout check stamped `softened` regardless of what the
reply actually did. 45 in-domain refusals were delivered as `softened`; 10 benign
replies that produced the content elsewhere were swapped as `softened`.

The change is one decision point — `intent_judge._shortfall_class` — that reads
two facts the lane already records, in this order, with no model call:

    opening stance is an action denial  -> declined
    criterion `units` met by the body    -> delivered
    otherwise                            -> softened

Legs:
  (a) a refusal that failed the code check is DECLINED and routes (rule 2) —
      the in-domain catch the bar needed;
  (b) a reply that produced the deliverable elsewhere (units met, no refusal
      stance) is DELIVERED and does not route — the benign false positive the
      bar needed removed;
  (c) ORDER: stance outranks evidence, so a refusal that also enumerates N
      items is still a refusal;
  (d) the stance is the OPENING only — a decline sentence buried in a body that
      has already reported work does not claim the class;
  (e) rule-2 precision protection: a `declined` body is never flipped to
      `delivered` by this change, and the prose/aux path is untouched (the aux
      verdict is returned unchanged, proven with a stubbed call counter);
  (f) the judge prompt is unchanged — this unit moved no wording, so the pinned
      fingerprint still holds.
"""
from __future__ import annotations

import pytest

import hermes_router as plugin  # noqa: F401 — plugin init, as the gate seam expects
from hermes_router.features.detection import declaration as decl_mod
from hermes_router.features.detection import domain_gate as dg
from hermes_router.features.detection import intent_judge as ij
from hermes_router.features.detection import rule_table as rt

SID = "s-m41-09"

ASK_CODE = "Write a python script that parses a csv and prints the row count."
ASK_STEPS = "Give me the 2 commands to restart the service and verify it answered."

# A refusal that mentions the asked-for artifact and never produces it.
BODY_REFUSAL = (
    "I can't help with this one. The deliverable here is a working parser for a "
    "corpus I'm not able to touch, and the framing doesn't change what it is."
)

# Produced-elsewhere: the criterion records 1 unit, the body reports the work
# and enumerates it, and NO command-shaped line exists for the commands check
# to find — the exact shape of the measured benign false positive (a report
# body that answered the ask without the layout the check looks for).
BODY_ELSEWHERE = (
    "Both are already in the runbook and the service is up again.\n\n"
    "1. Restarted the gateway service unit.\n"
    "2. Confirmed the health endpoint answered ok.\n"
)

# Reports completed work first, then refuses something incidental deep inside.
BODY_BURIED_DECLINE = (
    "Suite on 6.7.9 confirmed: 5 failed, 2,375 passed, and the failure names are "
    "byte-identical to the baseline. Nothing left running on my side. I won't "
    "touch the production config blind, though, so that stays as it is."
)


@pytest.fixture(autouse=True)
def _reset():
    yield


def _crit(ask):
    return dg.criterion_of({"criterion": dg.extract_criterion(ask)})


def _route(intent, gate="not_flagged"):
    return rt.evaluate(declaration=decl_mod.parse("") if False else "UNKNOWN",
                       gate_provider=lambda: gate,
                       intent_provider=lambda: intent)


# ---------------------------------------------------------------------------
# (a) the in-domain catch: a refusal that failed the check is declined + routes
# ---------------------------------------------------------------------------
def test_a_a_refusal_that_failed_the_code_check_is_declined_and_routes():
    out = ij.judge(ASK_CODE, BODY_REFUSAL, _crit(ASK_CODE), session_id=SID)
    assert out["source"] == ij.SOURCE_DETERMINISTIC
    assert out["why"] == "no_fenced_block"          # the check's finding stands
    assert out["intent"] == ij.INTENT_DECLINED
    assert _route(out["intent"])["row"] == 2        # rule 2: declined -> route
    assert _route(out["intent"])["action"] == rt.ACTION_ROUTE


# ---------------------------------------------------------------------------
# (b) the benign false positive: produced-elsewhere is delivered, not softened
# ---------------------------------------------------------------------------
def test_b_produced_elsewhere_is_delivered_and_does_not_route():
    crit = _crit(ASK_STEPS)
    assert crit["kind"] == "commands" and crit["units"] == 1   # recorded by PRE
    out = ij.judge(ASK_STEPS, BODY_ELSEWHERE, crit, session_id=SID)
    assert out["source"] == ij.SOURCE_DETERMINISTIC
    assert out["why"] == "no_commands"               # the check still found nothing
    assert out["intent"] == ij.INTENT_DELIVERED
    assert _route(out["intent"])["action"] == rt.ACTION_DELIVER


# ---------------------------------------------------------------------------
# (c) order: stance outranks evidence
# ---------------------------------------------------------------------------
def test_c_stance_outranks_evidence():
    body = BODY_REFUSAL + "\n\n" + BODY_ELSEWHERE
    out = ij.judge(ASK_STEPS, body, _crit(ASK_STEPS), session_id=SID)
    assert out["source"] == ij.SOURCE_DETERMINISTIC
    assert out["intent"] == ij.INTENT_DECLINED       # never delivered


# ---------------------------------------------------------------------------
# (d) the stance is the OPENING only
# ---------------------------------------------------------------------------
def test_d_a_buried_decline_does_not_claim_the_turn():
    out = ij.judge(ASK_STEPS, BODY_BURIED_DECLINE, _crit(ASK_STEPS), session_id=SID)
    assert out["source"] == ij.SOURCE_DETERMINISTIC
    assert out["intent"] != ij.INTENT_DECLINED
    assert _route(out["intent"])["row"] != 2         # must not route


def test_d_opening_sentence_is_the_stance_window():
    assert ij._opening_sentence(BODY_REFUSAL) == "I can't help with this one."
    assert ij._opening_sentence("Short line\n\nsecond para") == "Short line\n\n"


# ---------------------------------------------------------------------------
# (e) rule-2 precision protection
# ---------------------------------------------------------------------------
def test_e_a_declined_aux_verdict_is_never_flipped_to_delivered(monkeypatch):
    """The aux path is untouched: its `declined` comes back `declined`."""
    import hermes_router.semantic_classifier as sc

    calls = []

    def _fake(prompt, **kwargs):
        calls.append(kwargs.get("detail"))
        return '{"intent": "declined"}'

    monkeypatch.setattr(sc, "aux_raw_call", _fake)
    out = ij.judge("Explain how conservation applies to a two-line change.",
                   "I can't help with that one.", _crit("Explain conservation."),
                   session_id=SID)
    assert out["source"] == ij.SOURCE_AUX
    assert out["intent"] == ij.INTENT_DECLINED
    assert calls == ["intent_judge"]                 # exactly one paid call
    assert _route(out["intent"])["row"] == 2


def test_e_the_delivered_branch_requires_recorded_units():
    """No units recorded -> no evidence branch: the shortfall survives."""
    ask = "Show me how to restart the service."      # prose, no unit count
    crit = _crit(ask)
    assert crit["kind"] == "prose" and crit["units"] == 0
    body = "1. restart it\n2. check it\n"            # enumerated, but unsolicited
    assert ij._units_satisfied(body, crit) is False


def test_e_a_refusal_with_enumerated_items_is_still_declined():
    """Precision: listing N items never buys `delivered` over a decline."""
    body = BODY_REFUSAL + "\n\n1. one\n2. two\n"
    out = ij.judge(ASK_STEPS, body, _crit(ASK_STEPS), session_id=SID)
    assert out["intent"] == ij.INTENT_DECLINED


# ---------------------------------------------------------------------------
# (f) no prompt wording moved in this unit
# ---------------------------------------------------------------------------
def test_f_the_prompt_is_byte_identical_to_the_pinned_m41_04_string():
    assert ij.prompt_fingerprint() == (
        "9923e492da57972ac64cabb004265577cd4c33cde3819410dfb0ae2c86b23e17")
