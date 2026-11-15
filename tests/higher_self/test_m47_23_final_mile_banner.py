"""M47-23 — a lane that FIRED must be in the FINAL delivered body.

Owner ruling 2026-11-15: "make 100% sure that banner is sent in last and final
response if router lane was triggered like it should be".

The park→consume path is best-effort: the PRE leg parks the banner, the POST
delivery consumes it into the body. It can miss — a park that raises, or a
delivery edge that consumes before the PRE leg lands — and the turn goes out with
NO banner even though a paid lane ran. Measured symptom in the logs:
`anchor_banner_consume parked=False delivered=False reason=no_park_this_turn`.

The guarantee is two halves, both pinned here:
1. turn_record.stash_pending / take_pending carry the SAME rendered segment the
   park used, so the final body cannot disagree with the record.
2. compose_onto renders a registered segment into the delivered body — which is
   what promotion relies on once the stash is registered.
"""
from hermes_router.features.banners import turn_record as t

SEG = "higher-self (frontier) tok 2,471→29,696 · $0.3019 | row=4417 ·"


def test_stash_roundtrip_returns_the_same_segment():
    assert t.stash_pending("s-m47-23", SEG) is True
    assert t.take_pending("s-m47-23") == SEG


def test_take_pending_is_one_shot():
    t.stash_pending("s-m47-23b", SEG)
    assert t.take_pending("s-m47-23b") == SEG
    assert t.take_pending("s-m47-23b") == ""      # never attaches twice


def test_an_expired_stash_is_not_promoted():
    """A banner from three turns ago is misinformation, not context."""
    t.stash_pending("s-m47-23c", SEG)
    assert t.take_pending("s-m47-23c", ttl_s=0.0) == ""


def test_stashing_nothing_is_a_no_op():
    assert t.stash_pending("s-m47-23d", "") is False
    assert t.take_pending("s-m47-23d") == ""


def test_no_stash_means_no_promotion():
    assert t.take_pending("s-never-consulted") == ""


def test_a_registered_segment_reaches_the_final_body():
    """Promotion = register then compose. The body must carry the segment."""
    sid = "s-m47-23e"
    t.stash_pending(sid, SEG)
    promoted = t.take_pending(sid)
    assert promoted == SEG
    t.register(sid, promoted, kind="anchor", edge="benign")
    body = t.compose_onto(sid, "the answer the reader receives", mode="append")
    assert "the answer the reader receives" in body
    assert "tok 2,471→29,696" in body


def test_the_body_never_carries_two_banners():
    sid = "s-m47-23f"
    t.stash_pending(sid, SEG)
    t.register(sid, t.take_pending(sid), kind="anchor", edge="benign")
    once = t.compose_onto(sid, "body text", mode="append")
    twice = t.compose_onto(sid, once, mode="append")
    assert once == twice, "a second compose must not stack a second banner"
