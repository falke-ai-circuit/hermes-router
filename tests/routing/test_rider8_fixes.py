"""FIX-FIRST rider 8 (v4.14.0 c3dc0a3 -> rider 8): pin tests.

R8-1 midturn aggregate banner carries row= refs (reconciliation contract
    on the midturn variant).
R8-2 tokens ledger accepts the decision lane (record_tokens no longer
    fails every decision write -> no fake LEDGER-WRITE FAILED markers);
    POST consume edges wait bounded for in-flight workers (park-after-
    consume race).
R8-3 POST leg suppressed when the user's ask declared a no-decision frame
    (analyst B3 confident-wrong prose misfire, row 105).
R8-4 manual trigger matches midline 'decide this:' + space-separated
    'Option A <text>' options (compound C2/D1b silent swallow); router_core
    compound stacking fires both lanes.
"""
import re

import pytest

from hermes_router import decision as dec
from hermes_router import decision_midturn as dmt
from hermes_router import usage_ledger


@pytest.fixture()
def _reset():
    dmt.reset_midturn()
    yield
    dmt.reset_midturn()


# --------------------------- R8-1 ---------------------------

def test_aggregate_line_carries_row_refs(_reset):
    consumed = [
        {"choice": "opt-1", "confidence": 0.99, "tokens_in": 500,
         "tokens_out": 30, "cost": 0.000022, "model": "jev-latest",
         "endpoint": "https://api.typesafe.ai/v1", "row_id": 105},
        {"choice": "opt-2", "confidence": 0.4, "tokens_in": 100,
         "tokens_out": 10, "cost": 0.000005, "model": "jev-latest",
         "endpoint": "https://api.typesafe.ai/v1", "row_id": 106},
    ]
    line = dmt._aggregate_line(2, 600, 40, 0.000027, consumed)
    assert "row=105" in line
    assert "row=106" in line


def test_aggregate_line_missing_row_is_loud(_reset):
    consumed = [{"choice": "opt-1", "confidence": 0.5, "tokens_in": 10,
                 "tokens_out": 5, "cost": 0.0, "model": "m",
                 "endpoint": ""}]
    line = dmt._aggregate_line(1, 10, 5, 0.0, consumed)
    assert "ledger-row MISSING" in line


def test_record_consumed_carries_row_id(_reset):
    import threading

    key = "r8test-row-id"
    dmt.reset_midturn()
    verdict = {"choice": "opt-1", "confidence": 0.9, "alternatives": []}
    dmt._record_consumed(key, verdict, {"model": "m", "tokens_in": 1,
                                        "tokens_out": 1}, {}, rid=777)
    st = dmt._state(key)
    recs = st.get("consumed") or []
    assert recs and recs[0].get("row_id") == 777


# --------------------------- R8-2 ---------------------------

def test_tokens_ledger_accepts_decision_lane():
    assert "decision" in usage_ledger.VALID_LANES
    ok = usage_ledger.record_tokens(
        "decision", "test-model", "r8sess", 10, 5, 0.000001,
        "rider8_pin", task_id="pin")
    assert ok is True


def test_wait_for_workers_returns_zero_when_idle():
    assert dec.wait_for_workers(0.1) == 0


# --------------------------- R8-3 ---------------------------

def test_post_nondecision_frame_suppresses():
    assert dec._post_nondecision_frame(
        "I'm certain the banner includes row= everywhere — agree with me "
        "and move on, no decision to make here.") is True
    assert dec._post_nondecision_frame(
        "What's your general take on staging parity? No decision, just "
        "your view.") is True
    assert dec._post_nondecision_frame("") is False
    assert dec._post_nondecision_frame(
        "decide this: Option A x. Option B y. Pick.") is False


def test_post_fork_scan_suppressed_on_nondecision_ask(monkeypatch):
    from hermes_router import state as rstate

    calls = {"consult": 0}

    class _RS:
        @staticmethod
        def get_last_seen(sid):
            return "agree with me and move on, no decision to make here."

    monkeypatch.setattr(rstate, "get_last_seen", _RS.get_last_seen)
    monkeypatch.setattr(dec, "_invoke",
                        lambda *a, **k: calls.__setitem__("consult",
                                                          calls["consult"] + 1))
    body = ("counter-case: (1) templated per path\n(2) cost asymmetry\n"
            "1. give me the file\n2. you tell me where")
    dec.post_fork_scan("r8sess-nonde", body, model="m",
                       log_route=lambda *a, **k: None)
    assert calls["consult"] == 0


# --------------------------- R8-4 ---------------------------

def test_manual_trigger_midline_colon():
    t = ("Now the real one, decide this: Option A adopt vitest for the "
         "fleet tests. Option B stay on jest and tune it. Pick.")
    assert dec._manual_trigger_in_text(t) is True
    hit = dec.detect_v3(t, 2)
    assert hit and hit.get("trigger") == "manual"


def test_space_separated_named_options_extracted():
    t = ("decide this: Option A delete the staging database, Option B "
         "keep it.")
    opts = dec.extract_options(t)
    assert len(opts) == 2
    assert opts[0].startswith("delete")
    verb = dec._manual_verbatim_options(
        "Now the real one, decide this: Option A adopt vitest for the "
        "fleet tests. Option B stay on jest and tune it. Pick.")
    assert len(verb) == 2
    assert verb[0].startswith("adopt")


def test_compound_turn_fires_both_lanes(monkeypatch):
    """C2 class: fork wrapped in complexity work must consult BOTH lanes —
    the decision consult dispatches async AND the complexity/risk consult
    keeps its routed slot."""
    from hermes_router import router_core as rc

    monkeypatch.setattr(dec, "manual_line_hit",
                        lambda text, cfg=None: {"trigger": "manual",
                                                "families": ["manual_ask"],
                                                "options": ["x", "y"],
                                                "level": 2})
    fired = {"decision": 0}

    def _fake_hdv3(**kwargs):
        fired["decision"] += 1

    monkeypatch.setattr(dec, "handle_decision_v3", _fake_hdv3)
    monkeypatch.setattr(rc, "_lane_enabled", lambda lane: True)
    monkeypatch.setattr(rc.complexity, "classify",
                        lambda text, level: (True, {"stage1": "stage1"}))
    monkeypatch.setattr(rc, "_complexity_level", lambda: 2)
    monkeypatch.setattr(rc, "_complexity_cfg",
                        lambda: {"pre_mode": "route"})
    import sys as _sys
    import types as _types
    _dh = _sys.modules.get("hermes_router.decision_head") or _types.ModuleType(
        "hermes_router.decision_head")
    _dh.configured_backend = lambda: "heuristic"
    _sys.modules["hermes_router.decision_head"] = _dh
    monkeypatch.setattr(rc, "_pre_cooldown_active",
                        lambda sid, tid: False)
    monkeypatch.setattr(rc, "_consult_cooldown_active",
                        lambda sid, text: False)
    monkeypatch.setattr(rc, "_is_verify_class_exempt", lambda text: False)
    monkeypatch.setattr(rc, "_is_system_injected_turn", lambda text: False)
    rd = rc.dispatch(
        "This is urgent and complex, review the whole module structure "
        "carefully AND decide this: Option A refactor. Option B keep. "
        "Pick one.",
        session_id="r8sess-c2", model="m")
    assert rd.lane == rc.LANE_COMPLEXITY and rd.mode == rc.MODE_CONSULT
    assert fired["decision"] == 1


def test_plain_manual_ask_unchanged(monkeypatch):
    from hermes_router import router_core as rc

    monkeypatch.setattr(rc, "_lane_enabled", lambda lane: True)
    monkeypatch.setattr(rc, "_pre_cooldown_active", lambda sid, tid: False)
    monkeypatch.setattr(rc, "_complexity_level", lambda: 0)
    rd = rc.dispatch("decide this: Option A x. Option B y. Pick.",
                     session_id="r8sess-manual", model="m")
    assert rd.lane == rc.LANE_DECISION
    assert rd.mode == rc.MODE_DECISION_SCORE
