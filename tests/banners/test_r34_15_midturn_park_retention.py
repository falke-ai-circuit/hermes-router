"""R34-15 — mid-turn banner retention (the banner seam).

Live defect (session api_1792548097_0ec667cd): a consult was BILLED
(debug_banner_emitted lane=anchor est_cost=0.016364) and the banner was
parked at 02:03:24, then consumed at a MID-TURN benign edge (turn_no=20)
where `rewrite_persisted_turn` could not match an intermediate assistant
row; R33f-A made a failed capture a DROP, so the banner died on the first
mid-turn edge it met. Every billed consult inside a multi-tool turn lost
its banner.

Contract pinned here:
  1. Same turn (park turn_no == live turn_no) -> RETAIN, banner_park_retained.
     The last banner-eligible pass of the turn delivers it exactly once.
  2. Later turn -> DROP, banner_park_dropped reason=turn_closed_undelivered.
     R33f-A's stale-leak ban is preserved: turn N never reaches turn N+1.
"""
import sys
from unittest import mock

import pytest

_P = "/opt/data/plugins"
if _P not in sys.path:
    sys.path.insert(0, _P)

import hermes_router as plugin  # noqa: E402
from hermes_router import (advisory_consumption, canonical,  # noqa: E402
                           completion_audit, config_access, debug_banner,
                           render_inbox, route_gate, router_core, state,
                           usage_ledger)

SID = "s-r34-15"


@pytest.fixture()
def env(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    debug_banner._ANCHOR_BANNERS.clear()
    debug_banner._ANCHOR_TASKS.clear()
    debug_banner._ANCHOR_SEGS.clear()
    debug_banner._PARK_RECORDS.clear()
    debug_banner._HELD_DECISIONS.clear()
    advisory_consumption._TURN_NO.clear()
    advisory_consumption._CLOSED.clear()
    advisory_consumption._OPEN_SIG.clear()
    advisory_consumption._SWEEPS.clear()
    advisory_consumption._ORPHANS.clear()
    LOGGED = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr(render_inbox, "_inbox_path",
                        lambda: str(tmp_path / "renders.jsonl"))
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"debug_banner": 1})
    monkeypatch.setattr(debug_banner, "_banner_section",
                        lambda: {"debug_banner": 1}, raising=True)
    monkeypatch.setattr(completion_audit, "audit_enabled", lambda: False)
    yield LOGGED
    debug_banner._PARK_RECORDS.clear()


def _banner():
    return debug_banner.format_banner(
        lane="frontier-anchor", trigger="complexity_orientation",
        model="openai/gpt-6-astra-pro-flex", endpoint="openrouter.ai",
        tokens_in=3197, tokens_out=997, est_cost=0.016364, latency_s=40,
        task_id="t1", session_id=SID, route_id="r1")


def _events(logged):
    return [f.get("event_detail") for _, f in logged]


def _sweep(text):
    return plugin.on_transform_llm_output(
        response_text=text, session_id=SID, model="minimax-m3")


def test_midturn_capture_failure_retains_and_final_close_delivers_once(env):
    """20 intra-turn sweeps, banner parked at sweep 4, capture fails at
    sweep 20, turn closes -> banner on the FINAL body exactly once."""
    advisory_consumption.note_turn_open(SID, "refactor the helper")
    for i in range(1, 4):                       # sweeps 1-3: no park yet
        assert _sweep("tool output %d" % i) is None
    debug_banner.park_anchor_banner(SID, _banner(), task_id="t1")
    assert debug_banner.park_record(SID).get("turn_no") == 1

    with mock.patch.object(canonical, "rewrite_persisted_turn",
                           lambda *a, **k: False):
        for i in range(4, 21):                  # sweeps 4-20: capture misses
            _sweep("tool output %d" % i)
    ev = _events(env)
    assert ev.count("banner_park_retained") >= 1
    assert any(f.get("reason") == "mid_turn_no_match"
               for _, f in env
               if f.get("event_detail") == "banner_park_retained")
    assert "banner_park_dropped" not in ev
    assert debug_banner._ANCHOR_BANNERS.get(SID)   # survived the misses

    # final close pass of the SAME turn: the row matches -> delivered once
    with mock.patch.object(canonical, "rewrite_persisted_turn",
                           lambda *a, **k: True):
        out = _sweep("the refactor report")
    assert (out or "").count("· router ·") == 1
    assert not debug_banner._ANCHOR_BANNERS.get(SID)


def test_banner_failing_at_turn_close_never_enters_the_next_turn(env):
    """Negative pin: a park that is still undelivered when the turn closes is
    DROPPED — it cannot ride into turn N+1 (R33f-A preserved)."""
    advisory_consumption.note_turn_open(SID, "first ask")
    debug_banner.park_anchor_banner(SID, _banner(), task_id="t1")
    with mock.patch.object(canonical, "rewrite_persisted_turn",
                           lambda *a, **k: False):
        _sweep("intermediate")
    assert debug_banner._ANCHOR_BANNERS.get(SID)    # retained in turn 1

    advisory_consumption.note_turn_open(SID, "a different ask")  # turn 2
    from hermes_router.features.banners import lifecycle as _bll
    out = _bll.LIFECYCLE.deliver(SID, "next turn body", "anchor", "benign",
                                 log_edge="benign")
    ev = _events(env)
    assert "banner_park_dropped" in ev
    reasons = [f.get("reason") for _, f in env
               if f.get("event_detail") == "banner_park_dropped"]
    assert "turn_closed_undelivered" in reasons
    assert "· router ·" not in (out or "")
    assert not debug_banner._ANCHOR_BANNERS.get(SID)
