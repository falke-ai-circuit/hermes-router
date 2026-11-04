"""ONE banner RECORD per (session, turn) — M35-17.

The M30 defect (two or three banner lines in one delivered body) is gone by
construction: the body never carries a banner at all. Lanes REGISTER a segment
at fire time; the delivery boundary writes ONE `banner_record`; extraction
renders it. Nothing here returns text a caller can append to a body.
"""
from __future__ import annotations

import hermes_router.debug_banner as db
from hermes_router.features.banners import render as banner_render
from hermes_router.features.banners import turn_record as tr

import pytest


@pytest.fixture(autouse=True)
def _knob_on(monkeypatch):
    monkeypatch.setattr(db, "debug_banner_enabled", lambda: True)
    monkeypatch.setattr(db, "debug_banner_level", lambda: 1)
    tr.reset()
    yield
    tr.reset()

SID = "s-banner-turn"


def _line(lane: str, trigger: str, row: int, model: str = "m-1",
          ti: int = 10, to: int = 5, cost: float = 0.000123) -> str:
    text = db.format_banner(
        lane=lane, trigger=trigger, model=model, endpoint="openrouter://api.example/v1",
        tokens_in=ti, tokens_out=to, est_cost=cost, latency_s=0.4,
        retries=0, task_id="t-1", session_id=SID)
    return "%s | row=%d ·" % (str(text).rstrip().rstrip("·").rstrip(), row)


def _rec(**kw):
    """The record shape `record_turn` writes (extraction-only input)."""
    rec = {"turn_id": "7", "session_id": SID, "task_id": "t-1",
           "lane": "decision", "decision": "D4", "delivered_source": "main",
           "body_sha256": "", "body_chars": 0, "dupes": 0, "banner": []}
    rec.update(kw)
    return rec


def test_two_lanes_render_one_contiguous_block_not_two_appended_lines():
    decision = _line("decision", "pre", 611)
    frontier = _line("frontier-anchor", "completion_audit", 612)
    tr.register(SID, decision, lane="decision", kind="advisory")
    tr.register(SID, frontier, lane="frontier", kind="completion_audit")
    block = tr.render(SID)
    assert "impulse (decision)" in block
    assert "higher-self (frontier)" in block
    assert "row=611" in block and "row=612" in block
    # ONE contiguous block: segments joined by a single newline.
    assert block.count("\n") == 1, block


def test_one_lane_registers_one_segment():
    tr.register(SID, _line("decision", "pre", 611), lane="decision",
                edge="benign")
    assert len(tr.segments(SID)) == 1
    assert "row=611" in tr.render(SID)


def test_second_registration_of_the_same_line_is_idempotent():
    line = _line("decision", "pre", 611)
    assert tr.register(SID, line, lane="decision") is True
    assert tr.register(SID, line, lane="decision") is False
    assert len(tr.segments(SID)) == 1
    assert tr.render(SID).count("row=611") == 1


def test_same_lane_twice_is_two_segments_and_never_a_body_suffix():
    a = _line("decision", "pre", 611, ti=10, to=5)
    b = _line("decision", "midturn", 612, ti=20, to=7)
    tr.register(SID, a, lane="decision", kind="advisory")
    tr.register(SID, b, lane="decision", kind="advisory")
    block = tr.render(SID)
    assert "row=611" in block and "row=612" in block
    assert block.count("impulse (decision)") == 2


def test_new_turn_starts_a_new_record(monkeypatch):
    monkeypatch.setattr(tr, "turn_key", lambda sid: (1, "ask-a"))
    tr.register(SID, _line("decision", "pre", 611))
    assert len(tr.record(SID)["segments"]) == 1
    monkeypatch.setattr(tr, "turn_key", lambda sid: (2, "ask-b"))
    tr.register(SID, _line("decision", "pre", 700))
    segs = tr.record(SID)["segments"]
    assert len(segs) == 1
    assert "row=700" in segs[0]["text"]


def test_every_fact_survives_the_turn_record():
    seg = _line("decision", "pre", 611, model="model-z", ti=111, to=22,
                cost=0.0042)
    tr.register(SID, seg, lane="decision", kind="pre")
    block = tr.render(SID)
    for fact in ("impulse (decision)", "pre", "model-z", "111/22",
                 "$0.004200", "row=611"):
        assert fact in block, "record lost fact %r" % fact
    rendered = banner_render.render(_rec(banner=tr.segments(SID)), 3)
    assert "row=611" in rendered and "full: lane=" in rendered


def test_record_turn_returns_nothing_and_records_the_segment(monkeypatch):
    from hermes_router.core import decisions

    rows = []
    monkeypatch.setattr(decisions, "record_banner",
                        lambda **kw: rows.append(kw) or True)
    tr.register(SID, _line("decision", "pre", 611), lane="decision")
    out = tr.record_turn(SID, 7, "BODY", None, tr.segments(SID))
    assert out is None
    assert len(rows) == 1
    assert rows[0]["banner"][0]["lane"] == "decision"
    assert rows[0]["body_chars"] == 4
    assert tr.delivered(SID) is True


def test_register_and_render_never_raise_on_junk():
    assert tr.register("", "") is False
    assert tr.render("no-such-session") == ""
    assert tr.record_turn("", 0, None, None, []) is None
