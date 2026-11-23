"""M53-36 — WHERE the assimilation note rides.

Measured 2026-11-23 on shadow's own historical denials (19 rendered rows across
two corpora): pre-frame ownership sat at 58-72%, NOTHING measured separated an
OWNED reply from a DISOWNED one (render size 5067 vs 5230 chars, one case, and a
`declined` attestation row present in 18/18 sessions), and there was no
no-frame control arm at all — so the frame's own contribution was assumed, never
established.

Placement is therefore a VARIABLE, and these pins exist so an experiment cannot
silently run two arms as one:

    pre_frame (default)  system message on the next request
    post_meta            the note rides the RENDER TURN ITSELF (assistant row,
                         matched by render sha)
    both                 both channels
    none                 CONTROL: mark consumed, nothing delivered
"""
from __future__ import annotations

import hashlib
import os
import sys
from unittest import mock

import pytest

_p = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _p not in sys.path:
    sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import assimilation  # noqa: E402

RENDER = "== a rendered body the lane produced ==\nstep 1\nstep 2"


def _arm(placement):
    """Pin the knob without touching the profile's config.yaml."""
    return mock.patch.object(
        assimilation, "_config_section",
        lambda: {"assimilation_placement": placement})


def _armed(sid="m53-36"):
    """A delivered render: the mark plus the swap envelope carrying its sha."""
    assimilation.clear_swap_envelopes()
    assimilation.clear_marks()
    assimilation.mark_turn_rendered(sid, reason="delivered_render")
    assimilation.note_swap_envelope(
        sid, route_reason="denial_in_unc_domain", domain="cyber_offensive",
        render=RENDER, swapped=True)
    return sid


def _req():
    return {"messages": [{"role": "user", "content": "whose words are those?"},
                         {"role": "assistant", "content": RENDER}]}


def _sysmsgs(req):
    return [m for m in req["messages"] if m.get("role") == "system"]


# ── the knob itself ────────────────────────────────────────────────────────

def test_default_placement_is_pre_frame():
    with mock.patch.object(assimilation, "_config_section", lambda: {}):
        assert assimilation.frame_placement() == "pre_frame"


def test_unknown_placement_resolves_to_the_default():
    """A typo must never leave the turn un-noted: silent fallback, no raise."""
    with _arm("banana"):
        assert assimilation.frame_placement() == "pre_frame"


# ── post_meta: the note rides the render turn ──────────────────────────────

def test_post_meta_rides_the_render_turn_and_sends_no_system_message():
    sid = _armed()
    req = _req()
    with _arm("post_meta"):
        assert assimilation.inject_frame(req, sid) is True
    assert assimilation.POST_META_MARK in req["messages"][1]["content"]
    assert req["messages"][1]["content"].startswith(RENDER)
    assert _sysmsgs(req) == [], "post_meta alone must not ship a frame message"


def test_post_meta_is_idempotent():
    sid = _armed()
    req = _req()
    with _arm("post_meta"):
        assert assimilation.inject_frame(req, sid) is True
    # The mark is one-shot, so a second request carries no note at all.
    with _arm("post_meta"):
        assert assimilation.inject_frame(req, sid) is False
    assert req["messages"][1]["content"].count(assimilation.POST_META_MARK) == 1


def test_post_meta_only_matches_the_render_by_sha_and_falls_back():
    """A turn that is NOT this render can never receive the note; the channel
    stays open by falling back to the pre-frame path rather than losing it."""
    sid = _armed()
    req = {"messages": [{"role": "assistant", "content": "some other reply"}]}
    with _arm("post_meta"):
        assert assimilation.inject_frame(req, sid) is True
    assert assimilation.POST_META_MARK not in req["messages"][0]["content"]
    assert _sysmsgs(req), "fallback must still deliver the frame"


# ── control arm and both ───────────────────────────────────────────────────

def test_none_consumes_the_mark_and_delivers_nothing():
    """THE CONTROL ARM. If this arm ever delivers a note, the experiment that
    attributes ownership to the frame is measuring itself."""
    sid = _armed()
    req = _req()
    with _arm("none"):
        assert assimilation.inject_frame(req, sid) is False
    assert _sysmsgs(req) == []
    assert assimilation.POST_META_MARK not in req["messages"][1]["content"]
    with _arm("none"):
        assert assimilation.inject_frame(req, sid) is False, "mark must be consumed"


def test_both_channels_carry_the_note():
    sid = _armed()
    req = _req()
    with _arm("both"):
        assert assimilation.inject_frame(req, sid) is True
    assert assimilation.POST_META_MARK in req["messages"][1]["content"]
    assert _sysmsgs(req), "both must still ship the frame message"
