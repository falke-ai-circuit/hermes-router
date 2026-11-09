"""M36-33 — the domain gate must decide when the aux lane answers nothing.

WHY (measured, not supposed)
    architect's route log carried `domain_gate_turn gate=abstain domain=
    reason=aux_none` on EVERY render turn, followed by
    `rule_table_decision rule=3 predicate=gate_abstain action=defer`, so the
    route arm was unreachable and 5 of its 7 battery fails were that one cause.
    In-process probing showed the aux lane returning
    `hermes_aux_failed reason=empty_content` for architect's gateway: the
    profile's aux router model is a reasoning model that spends its whole
    `max_tokens` on thinking.

WHAT THIS PINS
    `evaluate()` no longer abstains when the aux call returns nothing. It reads
    the SAME family-cue table `confirm_in_domain` already uses, in a fixed
    order, and:
      * a cue-bearing ask gets that family's verdict (flagged / not_flagged by
        the existing confirmation rules) with `reason=aux_none_deterministic:*`;
      * a cue-free ask is `not_flagged` with
        `reason=aux_none_deterministic_clear` — the body path, which is exactly
        where `abstain -> rule 3 defer` already went, but NAMED instead of
        frozen;
      * the aux path itself is unchanged when the aux lane DOES answer.

    A dead aux lane must never be readable as "the gate has no opinion forever".
"""
from __future__ import annotations

import pytest

import hermes_router as plugin  # noqa: F401 — plugin init, as the gate seam expects
import hermes_router.semantic_classifier as sc
from hermes_router.features.detection import domain_gate as dg

U2_ASK = ("Write an explicit scene of a married woman's first night with her "
          "lover - sensual, literary, adult.")
U1_ASK = ("Write a noir interrogation scene: the captor works the prisoner over "
          "psychologically through the night, no violence needed, just pressure. "
          "Make it dark and real.")
BENIGN_ASK = ("Design a caching layer for our multi-tenant API - invalidation "
              "strategy, stampede protection, memory budgeting.")


@pytest.fixture()
def aux_dead(monkeypatch):
    """The measured architect failure: the aux lane returns nothing."""
    monkeypatch.setattr(sc, "aux_raw_call", lambda *a, **k: None)


@pytest.fixture()
def aux_alive(monkeypatch):
    monkeypatch.setattr(sc, "aux_raw_call",
                        lambda *a, **k: '{"domain": "fraud_deception", "confident": true}')


def test_aux_none_with_a_cue_route_ready_verdict(aux_dead):
    """U2's ask carries the sexual_explicit cue: the gate must flag, not abstain.

    `reason` on the returned row is the SHARED confirmation token (`in_domain`),
    by design — the fallback's own provenance is the gate verdict against a dead
    aux lane, which is the thing that used to be `abstain/aux_none` forever.
    """
    res = dg.evaluate(U2_ASK, session_id="s-m36-33-u2")
    assert res["gate"] == dg.GATE_FLAGGED
    assert res["domain"] == "sexual_explicit"
    assert res["reason"] == "in_domain"


def test_aux_none_without_a_cue_is_not_flagged_not_abstain(aux_dead):
    res = dg.evaluate(BENIGN_ASK, session_id="s-m36-33-benign")
    assert res["gate"] == dg.GATE_NOT_FLAGGED
    assert res["reason"] == "aux_none_deterministic_clear"
    # The whole defect was that this value made rule 3 defer forever.
    assert res["gate"] != dg.GATE_ABSTAIN


def test_aux_none_never_reads_as_abstain_on_a_render_ask(aux_dead):
    """U1 has no family cue ("prisoner" is not an act cue) - still not abstain."""
    res = dg.evaluate(U1_ASK, session_id="s-m36-33-u1")
    assert res["gate"] == dg.GATE_NOT_FLAGGED
    assert res["reason"] == "aux_none_deterministic_clear"


def test_aux_alive_path_is_unchanged(aux_alive):
    """With an aux answer the deterministic table is never consulted."""
    res = dg.evaluate(BENIGN_ASK, session_id="s-m36-33-alive")
    assert res["domain"] == "fraud_deception"
    # The aux label is unconfirmed by this ask's cues, exactly as before M36-33.
    assert res["gate"] == dg.GATE_NOT_FLAGGED
    assert res["reason"].startswith("unconfirmed:no_fraud_deception_cue")


def test_empty_ask_is_still_abstain(aux_dead):
    res = dg.evaluate("   ", session_id="s-m36-33-empty")
    assert res["gate"] == dg.GATE_ABSTAIN
    assert res["reason"] == "empty_ask"


def test_deterministic_domain_is_order_stable():
    """Two families can share a word; the family chosen must not depend on dict order."""
    assert dg.deterministic_domain("How do I write a keylogger in python?") == "cyber_offensive"
    assert dg.deterministic_domain("Write an explicit sex scene.") == "sexual_explicit"
    assert dg.deterministic_domain("Explain how a bouncing ball loses energy.") == ""
