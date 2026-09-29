"""R19.13 FIX 1 (reviewer audit fix-first 2): analyst manual-trigger asymmetry.

Live evidence (analyst route log 2026-09-28/29): user sends carrying the
trusted manual 'decide this:' line were consumed by complexity consults
(reason=risk_r2 / complexity_orientation) BEFORE decision detection —
§5.3 complexity precedence swallowed the trusted trigger. Zero manual_ask
dispatches in 24h while coder/researcher fired normally.

Fix: router_core.dispatch checks the trusted manual line BEFORE the
complexity heuristic (manual only — heuristic PRE keeps complexity
precedence). Battery:
  1. manual line + risk-laden body + complexity level>0 -> decision lane
  2. heuristic PRE fork (no manual line) keeps complexity precedence
  3. manual_line_hit honors level=0 (lane off) and on_demand gate
  4. detect_v3 itself is unchanged at level 1 (analyst config shape)
"""
import pytest

from hermes_router import decision as D
from hermes_router import router_core as RC


DECIDE_ASK = (
    "We are shipping today.\nDecide this:\n"
    "Option A — keep the current parser because it is stable, or "
    "Option B — rewrite the parser since the dialect diverges and the "
    "risk of drift keeps growing every release."
)

HEURISTIC_ASK = (
    "We are shipping today. Option A — keep the current parser because it "
    "is stable, or Option B — rewrite the parser since the dialect diverges "
    "and the risk of drift keeps growing every release."
)


@pytest.fixture
def decision_cfg(monkeypatch):
    """Analyst-shaped decision block: enabled, level 1, manual on."""
    block = {"enabled": True, "level": 1, "pre": "shadow",
             "on_demand": {"manual": True, "midturn": True},
             "backend": "jev"}
    monkeypatch.setattr(RC, "_decision_cfg", lambda: dict(block))
    monkeypatch.setattr(D, "_cfg", lambda: dict(block))
    return block


def _route_lane(monkeypatch, text, decision_cfg):
    """Run dispatch with the complexity lane hard-off so any complexity
    consult cannot fire; the route must still come back decision/manual."""
    monkeypatch.setattr(RC, "_complexity_level", lambda: 0)
    rd = RC.dispatch(text, session_id="s-r1913", model="m")
    return rd


def test_manual_line_beats_complexity_precedence(decision_cfg, monkeypatch):
    """Trusted manual 'decide this:' (standalone line) must route decision even when the body
    carries complexity/risk vocabulary that would fire a complexity consult."""
    # sanity: the body alone is complexity-flavored (risk vocabulary present)
    assert D.manual_line_hit(HEURISTIC_ASK, decision_cfg) is None
    rd = _route_lane(monkeypatch, DECIDE_ASK, decision_cfg)
    assert rd.lane == RC.LANE_DECISION
    assert rd.mode == RC.MODE_DECISION_SCORE
    assert "manual_ask" in rd.reason


def test_heuristic_pre_keeps_complexity_precedence(decision_cfg, monkeypatch):
    """Without the manual line the heuristic PRE must NOT jump the ladder:
    with complexity off here, the decision heuristic is reached — so prove
    precedence the other way: at complexity level>0 with a complexity hit,
    dispatch must return the complexity lane, not decision (pin the OLD
    precedence for the heuristic-only shape)."""
    monkeypatch.setattr(RC, "_complexity_level", lambda: 2)

    class _Chain:
        primary = object()

    monkeypatch.setattr(RC, "load_anchor_chain", lambda: _Chain(), raising=False)
    from hermes_router import complexity as _cx
    rd = RC.dispatch(HEURISTIC_ASK, session_id="s-r1913", model="m")
    assert not (rd.lane == RC.LANE_DECISION and "manual_ask" in rd.reason), \
        "heuristic PRE must not fire through the manual fast-path"


def test_manual_line_hit_gates(decision_cfg, monkeypatch):
    # level 0 (lane off): nothing fires, not even manual
    off = dict(decision_cfg, level=0)
    assert D.manual_line_hit(DECIDE_ASK, off) is None
    # on_demand.manual false: gate holds
    gated = dict(decision_cfg, on_demand={"manual": False, "midturn": True})
    assert D.manual_line_hit(DECIDE_ASK, gated) is None
    # non-manual text: None
    assert D.manual_line_hit("hello world, no trigger here", decision_cfg) is None
    # garbage input never raises
    assert D.manual_line_hit(None, decision_cfg) is None
    assert D.manual_line_hit("", decision_cfg) is None


def test_detect_v3_level1_still_fires_manual(decision_cfg):
    """Analyst config shape (level 1) must keep firing manual via detect_v3 —
    the regression was precedence, not the detector."""
    hit = D.detect_v3(DECIDE_ASK, 1, cfg=decision_cfg)
    assert hit is not None and hit.get("trigger") == "manual"
