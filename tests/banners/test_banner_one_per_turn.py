"""ONE banner per (session, turn) — Goran 2026-10-30.

Live defect this pins: a single turn carried two or three separate router
banner lines because each lane rendered AND appended its own (decision at
decision.py, completion_audit at three sites, the anchor park, the render
edges). Ledger rows 611 and 612 were the same lane twice in one turn.

Contract:
  * lanes REGISTER a segment at fire time;
  * the delivery boundary renders and appends ONCE per turn — one line when one
    lane fired, one contiguous block when several did;
  * later delivery edges in the same turn append nothing;
  * every fact today's banners carry survives (lane, kind, model, tokens, cost,
    row id);
  * the body itself is never mutated in place (delivery representation only).
"""
from __future__ import annotations

import hermes_router.debug_banner as db
from hermes_router.features.banners import turn_record as tr

import pytest


@pytest.fixture(autouse=True)
def _knob_on(monkeypatch):
    """The delivery edge carries the banner only when the knob is ON — pin it
    locally instead of mutating module state for the whole suite."""
    monkeypatch.setattr(db, "debug_banner_enabled", lambda: True)

SID = "s-banner-turn"


def _line(lane: str, trigger: str, row: int, model: str = "m-1",
          ti: int = 10, to: int = 5, cost: float = 0.000123) -> str:
    text = db.format_banner(
        lane=lane, trigger=trigger, model=model, endpoint="openrouter://api.example/v1",
        tokens_in=ti, tokens_out=to, est_cost=cost, latency_s=0.4,
        retries=0, task_id="t-1", session_id=SID)
    return "%s | row=%d ·" % (text.rstrip().rstrip("·").rstrip(), row)


def test_two_lanes_render_one_contiguous_block_not_two_appended_lines():
    tr.reset()
    body = "ANSWER BODY"
    decision = _line("decision", "pre", 611)
    frontier = _line("frontier-anchor", "completion_audit", 612)

    tr.register(SID, decision, lane="decision", kind="advisory")
    tr.register(SID, frontier, lane="frontier", kind="completion_audit")
    out = tr.emit_once(body, SID, edge="benign")

    # both facts survive, in one block
    assert "impulse (decision)" in out
    assert "higher-self (frontier)" in out
    assert "row=611" in out and "row=612" in out
    assert out.count("ANSWER BODY") == 1
    # ONE contiguous block: exactly one blank-line separator, segments joined
    # by a single newline (the old defect was two "\n\n" appends).
    assert out.count("\n\n") <= 2
    appended = out.split("ANSWER BODY", 1)[1].strip()
    assert appended.count("\n") == 1, "segments must form one block, got %r" % appended


def test_one_lane_renders_exactly_one_line():
    tr.reset()
    out = tr.emit_once("BODY", SID, extra_segment=_line("decision", "pre", 611),
                       lane="decision", edge="benign")
    tail = out.split("BODY", 1)[1].strip()
    assert tail.count("\n") == 0
    assert "row=611" in tail


def test_second_delivery_edge_in_the_same_turn_appends_nothing():
    tr.reset()
    first = tr.emit_once("BODY", SID, extra_segment=_line("decision", "pre", 611))
    second = tr.emit_once(first, SID, extra_segment=_line("frontier-anchor",
                                                          "audit", 612))
    assert second == first, "a second edge must not append a second banner"
    assert second.count("row=611") == 1
    assert "row=612" not in second
    assert tr.record(SID)["emitted"] is True


def test_same_lane_twice_collapses_into_one_line():
    tr.reset()
    a = _line("decision", "pre", 611, ti=10, to=5)
    b = _line("decision", "midturn", 612, ti=20, to=7)
    tr.register(SID, a, lane="decision", kind="advisory")
    tr.register(SID, b, lane="decision", kind="advisory")
    rendered = tr.render(SID)
    # merge_decision_banner owns the same-lane collapse: one decision line
    assert rendered.count("impulse (decision)") == 1
    assert "rows 611-612" in rendered, rendered


def test_new_turn_starts_a_new_record(monkeypatch):
    tr.reset()
    monkeypatch.setattr(tr, "turn_key", lambda sid: (1, "ask-a"))
    first = tr.emit_once("BODY", SID, extra_segment=_line("decision", "pre", 611))
    assert "row=611" in first
    monkeypatch.setattr(tr, "turn_key", lambda sid: (2, "ask-b"))
    second = tr.emit_once(first, SID, extra_segment=_line("decision", "pre", 700))
    # the RECORD restarted for the new turn (one segment, not two)
    assert len(tr.record(SID)["segments"]) == 1
    assert "row=700" in second or "rows 611-700" in second


def test_every_fact_survives_the_turn_record():
    tr.reset()
    seg = _line("decision", "pre", 611, model="model-z", ti=111, to=22,
                cost=0.0042)
    out = tr.emit_once("BODY", SID, extra_segment=seg, lane="decision")
    for fact in ("impulse (decision)", "pre", "model-z", "111/22",
                 "$0.004200", "row=611"):
        assert fact in out, "banner lost fact %r" % fact


def test_body_is_never_mutated_and_empty_body_gets_no_banner():
    tr.reset()
    body = "BODY"
    out = tr.emit_once(body, SID, extra_segment=_line("decision", "pre", 611))
    assert body == "BODY" and out != body and out.startswith("BODY")
    tr.reset()
    assert tr.emit_once("", SID, extra_segment=_line("decision", "pre", 611)) == ""


def test_emit_never_raises_on_junk():
    tr.reset()
    assert tr.emit_once(None, None) == ""
    assert tr.render("no-such-session") == ""
    assert tr.register("", "") is False
