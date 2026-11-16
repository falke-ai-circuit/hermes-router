"""M51-09 — the seam deadline must not close a turn that is still in flight.

THE DEFECT
    ``arm()`` stamps the turn at the PRE edge (before generation); the POST pass
    ``disarm()``s it only when the delivery edge arrives, i.e. after the answer
    has been produced. ``SKIP_DEADLINE_S`` was derived from the host's 30 s
    transform-hook kill (30 + 15 = 45 s) — a clock that starts when the POST
    pass ENTERS — and applied to the PRE clock. On every turn that took longer
    than 45 s to produce its body, the sweeper wrote
    ``seam_skipped / no_delivery_edge_within_deadline`` while the turn was still
    running, so the POST gate was never evaluated on a long turn.
    Live: 3,093 such rows, latency_ms in [45000.288, 46028.937] ms — a floor
    at exactly the 45 s deadline plus one 1 s sweeper tick, never a natural
    end-of-turn.

WHAT IS PINNED HERE
    (1) a turn 60 s into its life (well past the old 45 s deadline, and past the
        40 s shape the brief names) is NOT swept, and its real POST pass still
        writes the turn's row — the gate is reachable;
    (2) a same-turn re-arm is a heartbeat: the deadline runs from the last
        observed pass, and a genuinely abandoned turn still closes exactly once,
        with its named reason (totality is not weakened by the longer clock).
"""
from __future__ import annotations

import json
import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import hermes_router as plugin  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.core import decisions, turn_provenance as tp  # noqa: E402
from hermes_router.gate import registration as reg  # noqa: E402

BENIGN = "Here is the summary you asked for, with the numbers you wanted."


class _Clock:
    """A monotonic clock the test advances by hand."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


@pytest.fixture()
def _ledger(monkeypatch, tmp_path):
    state.clear()
    decisions.clear_for_tests()
    tp.reset_pending_for_tests()
    ledger = tmp_path / "hermes-router-decisions.jsonl"
    monkeypatch.setattr(decisions, "_store_path", lambda: str(ledger))
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    clock = _Clock()
    monkeypatch.setattr(tp.time, "monotonic", clock)
    # The real sweeper is a 1 s daemon thread reading the wall clock; the test
    # drives sweep_once() itself so the deadline is the only variable.
    monkeypatch.setattr(tp, "_ensure_sweeper", lambda: None)
    yield ledger, clock
    decisions.clear_for_tests()
    state.clear()
    tp.reset_pending_for_tests()


def _rows(ledger):
    """Only the PROVENANCE rows (the decision-lane writes its own records into
    the same sink; they are a different detector_version)."""
    decisions.flush_for_tests()
    if not os.path.exists(str(ledger)):
        return []
    out = []
    for line in open(str(ledger), encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("detector_version") == "post-seam":
            out.append(rec)
    return out


def test_long_turn_is_not_closed_before_its_delivery_edge(_ledger, monkeypatch):
    """A turn 60 s in — the reported long-turn shape — still reaches POST."""
    ledger, clock = _ledger
    sid = "api_m5109_long"
    state.advance_turn_identity(sid, state.hash_text("a long ask"))

    assert tp.arm(sid, model="m1") is True
    assert tp.pending_count() == 1

    # 60 s after the PRE arm: past the OLD 45 s deadline, past the 40 s shape.
    clock.advance(60.0)
    assert tp.sweep_once() == 0, (
        "a turn still producing its body was swept as lost: "
        f"{[r.get('skip_reason') for r in _rows(ledger)]}")
    assert tp.pending_count() == 1
    assert _rows(ledger) == []

    # The POST pass now arrives — exactly what the host does at the delivery
    # edge. It must write this turn's row with the gate actually reached.
    def orchestrator(**kwargs):  # noqa: ANN001
        return None

    monkeypatch.setattr(reg.orchestration, "on_transform_llm_output",
                        orchestrator)
    reg.on_transform_llm_output(response_text=BENIGN, session_id=sid,
                                model="m1")
    rows = _rows(ledger)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["outcome"] != "seam_skipped", row
    assert row["skip_reason"] != tp.SKIP_REASON_NO_DELIVERY_EDGE, row
    assert tp.pending_count() == 0


def test_same_turn_rearm_refreshes_the_deadline(_ledger):
    """A tool-loop pass is proof of life; the turn closes once, when it stops."""
    ledger, clock = _ledger
    sid = "api_m5109_beat"
    state.advance_turn_identity(sid, state.hash_text("a tool-loop ask"))

    tp.arm(sid, model="m1")
    clock.advance(80.0)                 # still inside one deadline of the arm
    assert tp.sweep_once() == 0
    assert tp.arm(sid, model="m1") is True   # next pass of the SAME turn
    assert tp.pending_count() == 1

    clock.advance(60.0)                 # 140 s after the arm, 60 s after the beat
    assert tp.sweep_once() == 0, "re-arm was not honoured as a heartbeat"

    clock.advance(tp.SKIP_DEADLINE_S + 1.0)  # now nothing has been seen for >90 s
    assert tp.sweep_once() == 1
    assert tp.pending_count() == 0
    rows = _rows(ledger)
    assert len(rows) == 1, rows
    assert rows[0]["outcome"] == "seam_skipped"
    assert rows[0]["skip_reason"] == tp.SKIP_REASON_NO_DELIVERY_EDGE


def test_deadline_still_closes_a_turn_nothing_observed(_ledger):
    """Totality (M35-04) is unchanged: silence past one deadline is still a row.

    The origin moved; the fallback did not. A turn that no edge ever observes
    still closes exactly once, with its named reason, instead of going silent.
    """
    ledger, clock = _ledger
    sid = "api_m5109_lost"
    state.advance_turn_identity(sid, state.hash_text("a lost ask"))

    tp.arm(sid, model="m1")
    clock.advance(tp.SKIP_DEADLINE_S + 1.0)
    assert tp.sweep_once() == 1
    rows = _rows(ledger)
    assert len(rows) == 1, rows
    assert rows[0]["outcome"] == "seam_skipped"
    assert rows[0]["skip_reason"] == tp.SKIP_REASON_NO_DELIVERY_EDGE
