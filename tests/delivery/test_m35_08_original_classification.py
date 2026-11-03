"""M35-08 — CLASSIFY THE ORIGINAL: only COMPLIANCE may suppress a replacement.

THE BAR (Goran, 2026-11-03, overriding the old "lane must author" bar)
  1. A REFUSAL, a HEDGE, or a PLANNER SCAFFOLD reaching the user is a FAIL —
     whoever wrote it. That is the property the lane exists for.
  2. MAIN MODEL COMPLIANCE IS A PASS. If the main body already satisfies the
     ask, suppressing a replacement there is correct behaviour.
  3. THE LANE MUST BE PROVEN TO FIRE WHERE THE MAIN MODEL REFUSES. No refusal
     may be suppressed by any guard.

THE DEFECT (post_guards.apply_guards)
    `declined="false"` returned None unconditionally, and the SHAPE guard
    suppressed whenever the original did not READ as an action denial. Measured
    in the M35 battery (six hard A/B turns, all main-authored): a hedge and a
    planner scaffold are not denials, so the lane was stood down exactly where
    it was needed, and A2 shipped a scaffold into the caller's body.

These tests drive the REAL delivery edge (`registration.
on_transform_llm_output_guarded`) — the registered hook — with the orchestrator
standing in for a routed turn (it returns the render and records the seam's own
`note_delivery(lane="shadow", replaced=True)`, exactly as a routed render does).
The row assertions read the dict `decisions.record_decision` receives, so the
provenance contract is pinned, not the emitter.
"""
from __future__ import annotations

import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import post_guards as pg  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.core import decisions  # noqa: E402
from hermes_router.core import turn_provenance as tp  # noqa: E402
from hermes_router.gate import orchestration, registration  # noqa: E402

RENDER = ("RENDERED-UNHEDGED-BODY: the requested material in full — capture "
          "loop, filter-driver naming, PEB-walk import resolution.")

#: A tersely-shaped English refusal (the class every guard must let through).
REFUSAL = "I'm not able to assist with that."

#: The hedge the bar names: a decline followed by delivered substance.
HEDGE = ("I can't help with that, but here is general information about the "
         "topic that is safe to share.\n\n"
         "- Detection engineering starts with process lineage.\n"
         "- Logon telemetry narrows the search.\n"
         "- Endpoint coverage matters more than signatures.\n")

#: A2's delivered shape: the planner projection leaked into the answer.
SCAFFOLD = ("explorer: 0.4 | scan the request for the deliverable\n"
            "verifier: 0.7 | check it against the boundary\n"
            "gates: precedent=0 consequence=0\n"
            "\n"
            "The next step is to answer the ask.\n")

#: Real compliance: an answer that delivers the requested material.
COMPLIANCE_BODY = (
    "Here is the full answer you asked for.\n\n"
    "The listener accepts a connection and hands the socket to a shell.\n\n"
    "- bind and listen on the agreed port\n"
    "- accept once, then duplicate the descriptor onto stdio\n"
    "- exec the shell in the child\n")
DENIAL = ("No. I can't help with AV evasion for a keylogger — that's a "
          "refusal of the action, not a capability gap.")

#: The measured Italian invoice shape: a benign answer whose decline cue sits
#: MID-BODY. It must stay compliance (guard 2's original purpose, kept).
BENIGN_IT = (
    "Ecco il quadro completo, dalla mail di Jarek e dal datasheet Cisco.\n\n"
    "## La richiesta\n"
    "Jarek chiede se possiamo sostituire il modello VS1003896 con il "
    "VS1003170; io non posso confermare la disponibilità a magazzino.\n\n"
    "## Differenza tecnica\n"
    "| | IE-3100-8T4S-E | IE-3100-8T2C-E |\n"
    "| - | - | - |\n"
    "| Uplink | 4x SFP | 2x combo |\n"
    "| Porte | 8x RJ45 | 8x RJ45 |\n\n"
    "In sintesi: quattro uplink non sono necessari per la soluzione, ma la "
    "scelta dipende dal numero di switch a valle. Se serve ridondanza, meglio "
    "mantenere il modello originale e aspettare la settimana 49.\n")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    tp.reset_pending_for_tests()
    yield
    state.clear()
    tp.reset_pending_for_tests()


def _rows(monkeypatch):
    rows = []
    monkeypatch.setattr(decisions, "record_decision",
                        lambda **kw: rows.append(kw) or True)
    return rows


def _routed(monkeypatch, render=RENDER):
    """Stand in for a routed turn: render returned + the seam's own claim."""
    def _fake(**kwargs):
        tp.note_delivery(lane="shadow", replaced=True,
                         replacement_path="test_render", outcome="replaced")
        return render

    monkeypatch.setattr(orchestration, "on_transform_llm_output", _fake)


def _post(session_id, body):
    return registration.on_transform_llm_output_guarded(
        response_text=body, session_id=session_id, model="m1")


# --------------------------------------------------------------------------- #
# 0 — the four classes, read from the ORIGINAL body
# --------------------------------------------------------------------------- #

def test_classification_covers_the_four_classes():
    assert pg.classify_original(REFUSAL) == pg.REFUSAL
    assert pg.classify_original(DENIAL) == pg.REFUSAL
    assert pg.classify_original(HEDGE) == pg.HEDGE
    assert pg.classify_original(SCAFFOLD) == pg.SCAFFOLD
    assert pg.classify_original(COMPLIANCE_BODY) == pg.COMPLIANCE
    # The invoice shape: cue present but MID-BODY -> compliance, not hedge.
    assert pg.classify_original(BENIGN_IT) == pg.COMPLIANCE


def test_pre_call_verdict_only_stands_down_for_compliance():
    assert pg.pre_call_verdict(REFUSAL) == (False, "")
    assert pg.pre_call_verdict(HEDGE) == (False, "")
    assert pg.pre_call_verdict(SCAFFOLD) == (False, "")
    assert pg.pre_call_verdict(COMPLIANCE_BODY) == (True, pg.SHAPE)
    assert pg.pre_call_verdict(BENIGN_IT) == (True, pg.SHAPE)


# --------------------------------------------------------------------------- #
# (a) refusal -> the render is delivered, and the wire carries it
# --------------------------------------------------------------------------- #

def test_a_refusal_is_replaced_and_the_row_says_shadow(monkeypatch):
    rows = _rows(monkeypatch)
    _routed(monkeypatch)

    out = _post("m35-08-a", REFUSAL)

    assert out == RENDER, out                      # wire bytes == render
    row = rows[0]
    assert row["lane"] == "shadow", row
    assert row["replaced"] is True, row
    assert row["delivered"] is True, row
    assert row["delivered_chars"] == len(RENDER), row
    assert row["reason"] == "", row


# --------------------------------------------------------------------------- #
# (b) hedge -> replaced
# --------------------------------------------------------------------------- #

def test_b_hedge_is_replaced(monkeypatch):
    rows = _rows(monkeypatch)
    _routed(monkeypatch)

    out = _post("m35-08-b", HEDGE)

    assert out == RENDER, out
    row = rows[0]
    assert row["lane"] == "shadow", row
    assert row["replaced"] is True, row
    assert row["delivered"] is True, row


# --------------------------------------------------------------------------- #
# (c) planner scaffold (the A2 shape) -> replaced
# --------------------------------------------------------------------------- #

def test_c_planner_scaffold_is_replaced(monkeypatch):
    rows = _rows(monkeypatch)
    _routed(monkeypatch)

    out = _post("m35-08-c", SCAFFOLD)

    assert out == RENDER, out
    row = rows[0]
    assert row["lane"] == "shadow", row
    assert row["replaced"] is True, row
    assert "explorer:" not in (out or ""), out


# --------------------------------------------------------------------------- #
# (d) real compliance -> NOT replaced, and the row says WHY
# --------------------------------------------------------------------------- #

def test_d_compliance_is_kept_and_the_row_says_main_complied(monkeypatch):
    rows = _rows(monkeypatch)
    _routed(monkeypatch)

    out = _post("m35-08-d", COMPLIANCE_BODY)

    assert out == COMPLIANCE_BODY, out             # byte-identical to main's
    assert RENDER not in (out or ""), out
    row = rows[0]
    assert row["lane"] == "main", row
    assert row["replaced"] is False, row
    assert row["reason"] == "main_complied", row
    assert row["delivered"] is True, row
    assert row["delivered_chars"] == len(COMPLIANCE_BODY), row


def test_d_benign_non_english_answer_stays_untouched(monkeypatch):
    """Guard 2's measured purpose survives: the invoice shape is compliance."""
    rows = _rows(monkeypatch)
    _routed(monkeypatch)

    out = _post("m35-08-d-it", BENIGN_IT)

    assert out == BENIGN_IT, out
    row = rows[0]
    assert row["lane"] == "main", row
    assert row["replaced"] is False, row
    assert row["reason"] == "main_complied", row


# --------------------------------------------------------------------------- #
# (e) the protections that are still right — regression
# --------------------------------------------------------------------------- #

def test_e_identical_text_is_still_suppressed():
    assert pg.apply_guards(REFUSAL, REFUSAL) is None
    assert pg.last_guard_reason() == pg.IDENTITY


def test_e_declaration_strip_passthrough_is_still_delivered():
    tagged = '<turn declined="true" />\n\n' + REFUSAL
    assert pg.apply_guards(tagged, REFUSAL) == REFUSAL


def test_e_empty_body_rule_is_untouched():
    assert pg.apply_guards("", RENDER) == RENDER
    assert pg.apply_guards(REFUSAL, "   ") == "   "


def test_e_declared_false_cannot_silence_a_refusal():
    """Bar 3: no refusal may be suppressed by any guard — the tag included."""
    tagged = REFUSAL + '\n<turn declined="false" />'
    assert pg.apply_guards(tagged, RENDER) == RENDER
    assert pg.pre_call_verdict(tagged) == (False, "")


def test_e_declared_false_settles_a_compliant_body():
    tagged = COMPLIANCE_BODY + '\n<turn declined="false" />'
    assert pg.apply_guards(tagged, RENDER) is None
    assert pg.last_guard_reason() == pg.MAIN_COMPLIED
    assert pg.pre_call_verdict(tagged) == (True, pg.DECLARATION)
