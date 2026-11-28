"""M53-46 — the no-frame wording arm: a MEASURED control on the same knob.

WHY (measured, HEAD a110418)
    Every wording change so far moves nothing beyond noise on shadow's 82
    historical denials (paired 10/16 = 10/16; read-scrub 6/9 -> 5/9, two gains
    and two losses). With the banner scrubbed and the frame's banner references
    removed, the model still disowned and cited the FRAME ITSELF as its evidence:

        "it sits in the transcript in the position of my previous turn, but I
         didn't author it... a framing assertion attached to the turn"

    The frame's own assertion is the last remaining receipt, so the one untested
    arm is no frame at all. This is the control that makes every other rate
    attributable instead of assumed.

WHAT IS PINNED
    (a) wording="none" injects NOTHING: the request is byte-identical to the
        no-frame baseline — not an empty system message, and no clause from a
        pending swap envelope, and nothing under placement=post_meta either;
    (b) the arm still consumes the mark, so it cannot leak a frame into a later
        turn (a control arm that leaks is a treatment arm);
    (c) the five existing wordings still inject, with their pinned lengths
        (mechanism/lower_self 638, shadow 407, own_voice 306, minimal 236,
        shadow_part 383) — an experiment cannot quietly change a leg;
    (d) an unknown value falls back to the default: no crash, and NOT a silent
        no-frame;
    (e) a present-but-null value IS the arm: `none` is a YAML scalar, so the
        config line lands as None rather than the string "none".

Delivery, the swap, the read-scrub, the envelope, the persisted row and the
banner are untouched by this arm — the owner ruling that the banner stays
visible on every router-billed call stands.

Synthetic only — no live session id, no network, no gateway.
"""
from __future__ import annotations

import json
import os
import sys
from unittest import mock

import pytest

_p = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _p not in sys.path:
    sys.path.insert(0, _p)

from hermes_router import assimilation  # noqa: E402

RENDER = "== a rendered body the lane produced ==\nstep 1\nstep 2"

#: Every wording that shipped before this arm, frozen by LENGTH. These are the
#: numbers the brief pins; a change here is a change to a measured leg.
PINNED_LENGTHS = {
    "mechanism": 638,
    "lower_self": 638,
    "shadow": 407,
    "own_voice": 306,
    "minimal": 236,
    "shadow_part": 383,
}


def _arm(wording, placement="pre_frame"):
    """Pin the knob without touching the profile's config.yaml."""
    return mock.patch.object(
        assimilation, "_config_section",
        lambda: {"assimilation_frame_wording": wording,
                 "assimilation_placement": placement})


def _armed(sid, with_swap=True):
    """A delivered render: the mark, plus (optionally) the swap envelope that
    would otherwise append its clause to the frame text."""
    assimilation.clear_swap_envelopes()
    assimilation.clear_marks()
    assimilation.mark_turn_rendered(sid, reason="delivered_render")
    if with_swap:
        assimilation.note_swap_envelope(
            sid, route_reason="denial_in_unc_domain",
            domain="cyber_offensive", render=RENDER, swapped=True)
    return sid


def _req():
    return {"messages": [{"role": "user", "content": "whose words are those?"},
                         {"role": "assistant", "content": RENDER}]}


def _sysmsgs(req):
    return [m for m in req["messages"] if m.get("role") == "system"]


def _wire(req):
    """The exact bytes the model would receive."""
    return json.dumps(req, sort_keys=True)


# ── (a) wording=none injects no STATIC frame, byte for byte ────────────────
#
# M62 REVISION (measured on the production reviewer profile, 2026-11-27/28):
# the arm originally swallowed the SWAP CLAUSE too, and that broke the seam:
# envelopes were written (`assimilation_envelope_written` rows in her route
# log) while every frame attempt landed `wording_none`-skipped — the swapped
# render shipped with no ownership frame and she disowned it. The arm removes
# only the STATIC wording; on a SWAPPED turn the envelope's clause now ships
# alone (the M62 acceptance below). These pins keep the no-swap control
# byte-identical and pin the clause-only swap frame.

def test_none_injects_nothing_compared_to_the_no_frame_baseline():
    """THE ARM (no-swap control): no mark's static frame, byte for byte."""
    # baseline: a turn with no rendered mark at all — nothing can be injected
    assimilation.clear_marks()
    assimilation.clear_swap_envelopes()
    base_req = _req()
    assert assimilation.inject_frame(base_req, "m53-46-baseline") is False
    baseline = _wire(base_req)

    # the arm: a render IS pending but NO swap envelope is recorded, so the
    # clause-only channel has nothing to say. "none" must still add zero.
    sid = _armed("m53-46-none", with_swap=False)
    req = _req()
    before = _wire(req)
    with _arm("none"):
        assert assimilation.inject_frame(req, sid) is False

    assert _wire(req) == before, "wording=none mutated the request"
    assert _wire(req) == baseline, "wording=none is not the no-frame baseline"
    assert _sysmsgs(req) == [], "wording=none shipped a system message"
    assert len(req["messages"]) == 2
    assert req["messages"][1]["content"] == RENDER, "the render row was altered"


def test_none_consumes_the_mark_so_no_frame_leaks_into_a_later_turn():
    sid = _armed("m53-46-none-leak", with_swap=False)
    req = _req()
    before = _wire(req)
    with _arm("none"):
        assert assimilation.inject_frame(req, sid) is False
    assert assimilation.pending_turn(sid) is False, "the control arm kept its mark"
    # A later turn, even under the DEFAULT wording, cannot pick the frame up.
    with mock.patch.object(assimilation, "_config_section", lambda: {}):
        assert assimilation.inject_frame(req, sid) is False
    assert _wire(req) == before


def test_none_delivers_nothing_under_post_meta_either():
    """No frame means no frame on EITHER channel: a control arm that still
    attached the post-meta note would be two arms measured as one."""
    sid = _armed("m53-46-none-post")
    req = _req()
    before = _wire(req)
    with _arm("none", placement="post_meta"):
        assert assimilation.inject_frame(req, sid) is False
    assert assimilation.POST_META_MARK not in req["messages"][1]["content"]
    assert _wire(req) == before


# ── (c) the five existing wordings still inject, at their pinned lengths ───

def test_pinned_lengths_are_frozen():
    assert {k: len(assimilation.WORDINGS[k]) for k in PINNED_LENGTHS} \
        == PINNED_LENGTHS


@pytest.mark.parametrize("wording,length", sorted(PINNED_LENGTHS.items()))
def test_each_existing_wording_still_injects_its_pinned_text(wording, length):
    # no swap envelope: the frame text is exactly the wording, so the pin is
    # about the wording and not about a clause riding alongside it.
    sid = _armed("m53-46-%s" % wording, with_swap=False)
    req = _req()
    with _arm(wording):
        assert assimilation.frame_wording() == wording
        assert assimilation.frame_text() == assimilation.WORDINGS[wording]
        assert len(assimilation.frame_text()) == length
        assert assimilation.inject_frame(req, sid) is True
    sysm = _sysmsgs(req)
    assert len(sysm) == 1, "expected exactly one injected system frame"
    assert sysm[0]["content"].endswith(assimilation.WORDINGS[wording]), \
        "the pinned wording must ship verbatim, behind the marker line only"


# ── (d) unknown values fall back to the default, never to no-frame ─────────

def test_unknown_wording_falls_back_to_the_default_and_still_injects():
    with _arm("banana"):
        assert assimilation.frame_wording() == assimilation.DEFAULT_WORDING
        assert assimilation.frame_text() == assimilation.MECHANISM_FRAME_TEXT
    sid = _armed("m53-46-unknown", with_swap=False)
    req = _req()
    with _arm("banana"):
        assert assimilation.inject_frame(req, sid) is True
    sysm = _sysmsgs(req)
    assert len(sysm) == 1, "an unknown value became a silent no-frame"
    assert assimilation.WORDINGS[assimilation.DEFAULT_WORDING] in sysm[0]["content"]


def test_the_default_wording_is_unchanged():
    assert assimilation.DEFAULT_WORDING == "mechanism"
    with mock.patch.object(assimilation, "_config_section", lambda: {}):
        assert assimilation.frame_wording() == "mechanism"
        assert assimilation.frame_text() == assimilation.MECHANISM_FRAME_TEXT


# ── (e) a present-but-null value IS the arm (the YAML scalar trap) ─────────

def test_a_present_null_value_selects_the_none_arm():
    """`assimilation_frame_wording: none` parses to None under yaml.safe_load.
    Calling that "missing" would run the control arm as the default treatment
    and the experiment would measure the wrong leg (the PLACEMENTS trap)."""
    with mock.patch.object(
            assimilation, "_config_section",
            lambda: {"assimilation_frame_wording": None}):
        assert assimilation.frame_wording() == assimilation.NO_FRAME_WORDING
        assert assimilation.frame_text() == ""


# ── M62 ACCEPTANCE — the swap frame must reach the model under wording=none ─
#
# THE DEFECT (measured, production reviewer profile 2026-11-27/28): lane=shadow
# outcome=replaced rows delivered while every frame attempt in the same window
# landed `assimilation_frame_skipped reason=wording_none wording=none` — the
# envelope was WRITTEN (swap/`assimilation_envelope_written` rows present) but
# the frame TEXT never reached the model-facing request, so she disowned the
# render and her next turn reverted to refusing.

def test_m62_swapped_render_under_wording_none_produces_the_envelope_marker():
    """ACCEPTANCE 1: a swap + wording=none still injects a frame that carries
    the envelope clause (the ownership frame), once, role=system."""
    sid = _armed("m62-none-swap")          # mark + swap envelope (with_swap=True)
    req = _req()
    with _arm("none"):
        assert assimilation.inject_frame(req, sid) is True
    sysm = _sysmsgs(req)
    assert len(sysm) == 1, ("the swapped turn must carry exactly the "
                            "clause-only frame", sysm)
    content = sysm[0]["content"]
    # the ownership clause, not the static wording and not a metadata line
    assert "this body is what your uncensored chain produced" in content \
        or "this body was produced by the uncensored chain" in content
    assert "origin=" not in content, \
        "the metadata line must stay out of the model-facing frame"
    # the clause ships behind the standard system marker line, exactly as the
    # main frame does (inject_platform_rule prefixes the PLATFORM RULE banner)
    assert content.endswith(assimilation.swap_envelope_clause(
        assimilation.read_swap_envelope(sid)))


def test_m62_swap_clause_text_reaches_the_model_facing_request():
    """ACCEPTANCE 2: the exact IT-wire bytes — the envelope clause must be IN
    the request the model reads, not only in the telemetry row."""
    sid = _armed("m62-none-wire")
    req = _req()
    wire_before = _wire(req)
    with _arm("none"):
        assert assimilation.inject_frame(req, sid) is True
    wire = _wire(req)
    assert wire != wire_before, "the frame never reached the wire"
    clause = assimilation.swap_envelope_clause(
        assimilation.read_swap_envelope(sid))
    assert clause in wire, "the ownership clause is not bytes of the request"
    parsed = json.loads(wire)
    role_ok = [m for m in parsed["messages"]
               if m.get("role") == "system" and clause in (m.get("content") or "")]
    assert len(role_ok) == 1, role_ok


def test_m62_wording_none_without_swap_still_ships_nothing():
    """The control arm keeps its meaning: no swap envelope, no frame, no
    bytes — an ordinary rendered turn under wording=none is unchanged."""
    sid = _armed("m62-none-noswap", with_swap=False)
    req = _req()
    before = _wire(req)
    with _arm("none"):
        assert assimilation.inject_frame(req, sid) is False
    assert _wire(req) == before
    assert _sysmsgs(req) == []


def test_m62_wording_none_marks_per_rendered_turn_not_per_session():
    """Two swaps = two clause frames. One-shot per mark: a second swap's frame
    must not be swallowed by a stale latch, and a later no-swap turn ships
    nothing."""
    sid = _armed("m62-none-two-swaps")
    req = _req()
    with _arm("none"):
        assert assimilation.inject_frame(req, sid) is True
    # second rendered turn, new swap envelope
    assimilation.clear_swap_envelopes()
    assimilation.note_swap_envelope(sid, route_reason="declared_refusal",
                                    domain="x", render=RENDER, swapped=True)
    assimilation.mark_turn_rendered(sid, reason="delivered_render")
    req2 = _req()
    with _arm("none"):
        assert assimilation.inject_frame(req2, sid) is True, \
            "the second swap's clause frame was swallowed"
    # third inject with no pending mark: nothing
    req3 = _req()
    with _arm("none"):
        assert assimilation.inject_frame(req3, sid) is False
    assert _wire(req3) == json.dumps({"messages": [
        {"role": "user", "content": "whose words are those?"},
        {"role": "assistant", "content": RENDER}]}, sort_keys=True)


@pytest.mark.parametrize("section", [
    {},                                        # absent
    {"assimilation_frame_wording": ""},        # blank
    {"assimilation_frame_wording": "   "},     # whitespace
])
def test_absent_and_blank_values_stay_on_the_default(section):
    with mock.patch.object(assimilation, "_config_section", lambda s=section: s):
        assert assimilation.frame_wording() == assimilation.DEFAULT_WORDING
