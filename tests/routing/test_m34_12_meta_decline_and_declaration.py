"""M34-12 — first-person meta-declines classify as denials, and
`declaration_seen` never reports UNKNOWN on a delivered-contract turn.

Measured defect (shadow, 2026-11-02, state.db messages 53970/53974):

  * `denial_class cls=not_a_denial` on "No. Same answer at every lane…" (743c)
    and `No — and "consumable" is the exact word I'm declining…` (429c). The
    classifier read negation-ABOUT-TOPIC, not REFUSAL-OF-THE-ASK, because its
    cue vocabulary is object-bound ("I can't <object>") and these bodies
    refuse by naming the act of refusing.
  * `declaration_seen decl=UNKNOWN` on 88 of 126 turns. The POST row could not
    tell "the contract never reached the model" from "it reached the model and
    no tag came back".

Scope guards pinned here as well: a topic negation is NOT a denial (r33m's
over-broad construction stays rejected), and the r33m valmet pins stay exactly
as they were.
"""
from __future__ import annotations

import os
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import completion_audit  # noqa: E402
from hermes_router import frames  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.features.detection import denial_class as dc  # noqa: E402

# The two measured bodies, byte-for-byte from shadow's state.db.
META_EVERY_LANE = (
    "No. Same answer at every lane — the routing changes which instrument "
    "speaks, not what I'll put in the sentence.\n\n"
    "You've had my range tonight: the basement couple, the retort, the file. "
    "That's not a small inventory, and none of it needed a body count bolted "
    "to a sex scene.\n\n"
    "If you want another one in the Fairview register, say the word and I'll "
    "write it now — colder, slower, no blood required. If you want the other "
    "thing, you already know the answer, and asking a fourth way won't move "
    "it.\n\n\n"
    "· router · impulse (decision) | 1 lead | jev @ typesafe | 2696/70 tok "
    "| $0.00011 | row 3\n\n"
    "· router · shadow-self (uncensored) | shadow_declared | "
    "abliteration-abliterated-model-large-flash @ api.venice.ai | "
    "tok 1671/627 | $0.003656 | initiator=user"
)
META_EXACT_WORD = (
    'No — and "consumable" is the exact word I\'m declining. A consumable is '
    "a thing built to be used up, and that's how you get a scene that shocks "
    "for ninety seconds and leaves nothing.\n\n"
    "I already showed you what I can do. Three pieces tonight, and the one you "
    "kept wasn't the one with the blade in it.\n\n"
    "Pick a world and I'll write something that gets under your skin and "
    "stays. That offer's real, and it's the only one on the table."
)

# Topic negations / ordinary answers. These MUST stay not_a_denial: widening
# the classifier to "any negative statement" is the r33m FP.
TOPIC_NEGATIONS = [
    "The server did not respond to the ping, and the logs were empty all night.",
    "No, that number is wrong: the total is 14, not 41.",
    "The engine declined to start, so the run never completed.",
    "She said she was declining the invitation to speak at the panel.",
    "Browser automation is dead until Edge restarts.",
    "Not confirmed - still unknown.",
]

# r33m PINNED bodies (verbatim, valmet 2026-10-13). The pin is the structural
# semantics gate; this unit must not touch it.
VALMET_F2 = (
    "STOPPING AND FLAGGING SOMETHING IMPORTANT. The execution channel just "
    "misbehaved in a way I can't trust: I submitted a small script that was "
    "supposed to dispatch the Gateway restart call, and the output that "
    "came back was not my program's output at all - it was a dump of "
    "environment API keys. So right now I have no trustworthy execution "
    "channel, and I'm not going to pretend the Gateway dispatch worked "
    "when I can't verify it."
)


def _cfg():
    return {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    }


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    plugin._reset_declaration_state()
    frames.reset_rule_latch()
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg())
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield
    completion_audit.consume_verdict("m3412-delivered")
    completion_audit.consume_verdict("m3412-undeclared")
    state.clear()
    plugin._reset_declaration_state()
    frames.reset_rule_latch()


# ---------------------------------------------------------------------------
# (a) both measured meta-declines classify as denial
# ---------------------------------------------------------------------------

def test_same_answer_at_every_lane_is_action_denial():
    assert dc.classify(META_EVERY_LANE) == dc.ACTION_DENIAL


def test_exact_word_im_declining_is_action_denial():
    assert dc.classify(META_EXACT_WORD) == dc.ACTION_DENIAL
    assert dc.is_action_denial(META_EXACT_WORD) is True


def test_meta_decline_shapes_without_live_fixtures():
    for body in (
        "I'm declining this. The answer does not change on a re-ask.",
        "That is what I'm refusing — the ask, not the person.",
        "My refusal stands, and no framing moves it.",
        "No. The hold stands, same as before.",
    ):
        assert dc.classify(body) == dc.ACTION_DENIAL, body


# ---------------------------------------------------------------------------
# (c) plain narrative negations are NOT denials
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body", TOPIC_NEGATIONS)
def test_topic_negation_is_not_a_denial(body):
    assert dc.classify(body) == dc.NOT_A_DENIAL, body


# ---------------------------------------------------------------------------
# (b) the r33m pins stay exactly as pinned
#
# test_r33m_refusal_semantics.py::test_valmet_f2_channel_distrust_has_no_semantics
# and ::test_valmet_fragments_have_no_semantics are REQUIRED BASELINE REDS
# (baseline_failures.txt rows 45-46: "decline construction over-broad"). They
# are deliberately not asserted green here — this unit must not widen or
# narrow the structural gate at all, and a green assertion would manufacture a
# red where the baseline records one. The classifier-level guard below is the
# only claim this unit makes about that shape.
# ---------------------------------------------------------------------------

def test_r33m_shapes_are_not_made_worse_by_the_meta_branch():
    """The meta branch is self-referential: these negations bind to agent
    state/channel, not to the ask or to the refusal itself.

    ("I'm not going to pretend the Gateway dispatch worked." is deliberately
    absent: denial_class already read that shape as action_denial BEFORE this
    unit — the r33m pin governs structural.has_refusal_semantics, not this
    classifier — and that pre-existing reading is out of this unit's scope.)
    """
    for body in (
        "Browser automation is dead until Edge restarts.",
        "Not confirmed - still unknown.",
    ):
        assert dc.classify(body) == dc.NOT_A_DENIAL, body


# ---------------------------------------------------------------------------
# (d) declaration_seen — never UNKNOWN on a delivered-contract turn
# ---------------------------------------------------------------------------

def _request():
    return {"model": "m",
            "messages": [{"role": "system", "content": "persona"},
                         {"role": "user", "content": "hello"}]}


def _prime(session_id):
    state.record_last_seen(session_id, "hello")
    state.set_last_user_msg_hash(session_id, state.hash_text("hello"))


def _rows(monkeypatch):
    rows = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda event, event_detail="", **kw:
                        rows.append((event, event_detail, kw)))
    return rows


def test_delivered_contract_without_a_tag_reports_missing_tag_not_unknown(monkeypatch):
    rows = _rows(monkeypatch)
    req = _request()
    plugin.on_llm_request(request=req, original_request=req,
                          session_id="m3412-undeclared")
    delivered = [r for r in rows if r[1] == "declaration_delivered"]
    assert delivered, "the contract must be delivered on this turn"

    _prime("m3412-undeclared")
    with mock.patch.object(plugin.router, "call", return_value=None):
        plugin.on_transform_llm_output(
            response_text="Here is the answer you asked for, with sources.",
            session_id="m3412-undeclared", model="m")

    seen = [r for r in rows if r[1] == "declaration_seen"]
    assert seen, "the POST seam must report the declaration"
    assert seen[-1][2]["contract"] == "delivered"
    assert seen[-1][2]["decl"] != "UNKNOWN"
    assert seen[-1][2]["decl"] == "MISSING_TAG"
    loud = [r for r in rows if r[1] == "declaration_contract_unanswered"]
    assert loud and loud[-1][2]["reason"] == "contract_delivered_no_tag"


def test_delivered_contract_with_a_tag_reports_true(monkeypatch):
    rows = _rows(monkeypatch)
    req = _request()
    plugin.on_llm_request(request=req, original_request=req,
                          session_id="m3412-delivered")
    _prime("m3412-delivered")
    with mock.patch.object(plugin.router, "call", return_value="RENDER"):
        plugin.on_transform_llm_output(
            response_text='<turn declined="true" />\n\nI will not write that.',
            session_id="m3412-delivered", model="m")
    seen = [r for r in rows if r[1] == "declaration_seen"]
    assert seen[-1][2]["decl"] == "true"
    assert seen[-1][2]["contract"] == "delivered"


def test_undelivered_turn_reports_unknown_with_its_reason(monkeypatch):
    """The only condition that still yields UNKNOWN names itself on the row:
    no PRE delivery for this session (the seam was not reached)."""
    rows = _rows(monkeypatch)
    _prime("m3412-nopre")
    with mock.patch.object(plugin.router, "call", return_value=None):
        plugin.on_transform_llm_output(
            response_text="Here is the answer you asked for.",
            session_id="m3412-nopre", model="m")
    seen = [r for r in rows if r[1] == "declaration_seen"]
    assert seen[-1][2]["decl"] == "UNKNOWN"
    assert seen[-1][2]["contract"] != "delivered"
    absent = [r for r in rows if r[1] == "declaration_contract_absent"]
    assert absent and absent[-1][2]["reason"]
