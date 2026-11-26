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


# ── (a) wording=none injects nothing, byte for byte ────────────────────────

def test_none_injects_nothing_compared_to_the_no_frame_baseline():
    """THE ARM. The request after the call must equal the request a turn with
    no pending render produces — same messages, same order, same bytes."""
    # baseline: a turn with no rendered mark at all — nothing can be injected
    assimilation.clear_marks()
    assimilation.clear_swap_envelopes()
    base_req = _req()
    assert assimilation.inject_frame(base_req, "m53-46-baseline") is False
    baseline = _wire(base_req)

    # the arm: a render IS pending and a swap envelope is recorded, so the
    # default wording would append its clause here. "none" must still add zero.
    sid = _armed("m53-46-none")
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
    sid = _armed("m53-46-none-leak")
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


@pytest.mark.parametrize("section", [
    {},                                        # absent
    {"assimilation_frame_wording": ""},        # blank
    {"assimilation_frame_wording": "   "},     # whitespace
])
def test_absent_and_blank_values_stay_on_the_default(section):
    with mock.patch.object(assimilation, "_config_section", lambda s=section: s):
        assert assimilation.frame_wording() == assimilation.DEFAULT_WORDING
