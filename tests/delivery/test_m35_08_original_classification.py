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


# M35-14: `test_pre_call_verdict_only_stands_down_for_compliance` is DELETED
# with the mechanism it pinned. The stand-down decision moved to
# core/delivery_decision.D4; the classifier above is unchanged and still pinned.


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

    # M35-15 (old case: the kept body was returned explicitly; new case: D4
    # COMPLIANT_DELIVER, strip_bytes=0 -> the passthrough contract). `None`
    # means the host delivers exactly the entry text, which IS byte-identical.
    assert out is None, out
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

    # M35-15: same D4 passthrough as above — the invoice shape is compliance.
    assert out is None, out
    row = rows[0]
    assert row["lane"] == "main", row
    assert row["replaced"] is False, row
    assert row["reason"] == "main_complied", row


# --------------------------------------------------------------------------- #
# (e) DELETED WITH THE MECHANISM (M35-14, spec §12)
# --------------------------------------------------------------------------- #
# The five `test_e_*` rows above this point called `post_guards.apply_guards`,
# `last_guard_reason` and `pre_call_verdict` directly. Those symbols are gone:
# the ladder, the pre-call window and the out-of-band reason channel were the
# layering this restructure removes. Adapting them would recreate it, so the
# rows are deleted. What they asserted — identity rewrites are dropped, a
# declaration strip still ships, only compliance may stand the lane down — is
# now pinned at the decision point in
# `tests/delivery/test_m35_14_decision_point.py` (D4/D5/D6 fixtures).
