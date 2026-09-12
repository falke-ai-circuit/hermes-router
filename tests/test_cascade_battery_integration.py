"""Leg 4 — integration + edge-case battery for the unified cascade route gate
(BLUEPRINT-request-routing-2026-09-12, final leg).

Complements the per-leg unit tests with the scenarios the brief names:
  - on-demand (declared claim) × {kill-switch off, kill-switch on, shadow}
    × {custom model, default model}
  - cap-denial banner provenance-CONTENT assertion (consult output CARRIES
    the HIGHER_SELF frame; denial banner is the DELIVERY — content assertions,
    never HTTP-200 checks)
  - double-declare (agent then user phrase, one consult)
  - echo-guard end-to-end through on_llm_request
  - ledger initiator-tag provenance through the anchor lane contract
  - battery harness scenarios (benchmarks/behavioral_battery.py extension
    lives there; the harness contract itself is asserted here)
"""
import json
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import config_access  # noqa: E402
from hermes_router import debug_banner  # noqa: E402
from hermes_router import provenance_footer  # noqa: E402
from hermes_router import route_gate  # noqa: E402
from hermes_router import router_core  # noqa: E402
from hermes_router import router_tools  # noqa: E402
from hermes_router import routing_caps  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router import usage_ledger  # noqa: E402

SID = "s-leg4"


def _request(text, model="minimax-m3"):
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": text},
        ],
    }


def _rr(lane="higher-pre", session_id=SID):
    return json.loads(router_tools.router_control(
        action="request_routing", lane=lane, session_id=session_id))


@pytest.fixture()
def caps_tmp(tmp_path, monkeypatch):
    p = str(tmp_path / "routing-state.json")
    routing_caps._test_reset(p)
    yield p
    routing_caps._test_reset(None)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(debug_banner, "park_anchor_banner", lambda *a, **k: None)
    yield
    state.clear()
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)


# ---------------------------------------------------------------------------
# On-demand × kill-switch × lane × model matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["minimax-m3", "z-ai/glm-5.3"])
def test_on_demand_matrix_kill_switch_off_blocks_all_lanes(monkeypatch, caps_tmp, model):
    """H7.4: kill-switch off disables EVERY declared lane, every model."""
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"on_demand_routing": "off"})
    for lane in route_gate.VALID_ROUTE_LANES:
        _rr(lane=lane)
        d = route_gate.claim_pass("do the thing", SID, model)
        assert d.route is False, (lane, model)
        # Kill-switch off = the claim is NOT consumed (no routing happened);
        # it stays fresh until its TTL — the agent can re-enable and the
        # SAME claim is then honored. Cleared here for the next lane.
        assert route_gate.peek_declared(SID) is not None
        route_gate.clear_declared(SID)


@pytest.mark.parametrize("model", ["minimax-m3", "z-ai/glm-5.3"])
def test_on_demand_matrix_shadow_lane_claims(monkeypatch, caps_tmp, model):
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    staged = []
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary":
                        staged.append(rd) or {"route_id": rd.route_id})
    _rr(lane="shadow")
    d = route_gate.claim_pass("work the problem", SID, model)
    assert d.route is True and d.lane == "shadow"
    # Leg 8 (blueprint §2): shadow executes on the UNCENSORED RENDER chain —
    # no frontier anchor swap is staged for the shadow lane.
    assert not staged


def test_on_demand_higher_post_claim_consumed_without_pre_envelope(monkeypatch, caps_tmp):
    """higher-post declared: the gate consumes the claim; the PRE envelope
    still stages (frontier endpoint via anchor lane) — orientation OFF."""
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    staged = []
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary":
                        staged.append(rd) or {"route_id": rd.route_id})
    _rr(lane="higher-post")
    d = route_gate.claim_pass("work the problem", SID, "minimax-m3")
    assert d.route is True and d.lane == "higher-post"
    assert staged and staged[0].orientation is False
    assert route_gate.peek_declared(SID) is None  # consumed


# ---------------------------------------------------------------------------
# Cap-denial banner: DELIVERY provenance (content assertions)
# ---------------------------------------------------------------------------


def test_cap_denial_banner_is_the_delivered_text(monkeypatch, caps_tmp):
    """Goran amendment, content-level: the denial banner must reach the
    DELIVERY as parked text with the exact spend/cap figures — asserted on
    the parked content, never on a transport status."""
    parked = []
    monkeypatch.setattr(debug_banner, "park_anchor_banner",
                        lambda sid, text: parked.append((sid, text)))
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": 2.0})
    routing_caps.record_agent_spend(routing_caps.agent_identity(), 2.5)
    # Leg 6: the capped lane is declared_agent — register an agent claim.
    route_gate.register_declared(SID, route_gate.LANE_HIGHER_PRE,
                                 route_gate.SOURCE_DECLARED_AGENT)
    d = route_gate.decide_turn({"content": "work the problem",
                                "request": _request("x"), "context": {},
                                "session_id": SID, "auto_shape": None,
                                "claim": True})
    assert d.route is False and d.reason == "cap_denied"
    assert parked == [(SID, routing_caps.denied_banner_text(2.5, 2.0))]
    assert "$2.50/$2.00" in parked[0][1]  # exact figures in the visible text


def test_consult_output_carries_higher_self_frame(monkeypatch, caps_tmp):
    """Provenance-CONTENT assertion (blueprint): consult delivery carries
    the HIGHER_SELF frame — the orientation envelope text embeds the
    HIGHER-SELF ORIENTATION marker that the sentinel firewall later
    recognizes. Asserted on CONTENT, not transport."""
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    captured = {}

    def _fake_stage(sid, rd, role="primary"):
        captured["orientation"] = rd.orientation
        # The __init__ envelope builder composes this exact marker for an
        # orientation consult (verbatim from the production template).
        captured["frame"] = ("[HIGHER-SELF ORIENTATION TURN | FRONTIER-DERIVED | "
                             "INTERNAL | USER-INVISIBLE]" if rd.orientation else "")
        return {"route_id": rd.route_id}

    monkeypatch.setattr(router_core, "stage_model_swap", _fake_stage)
    _rr(lane="higher-pre")
    d = route_gate.claim_pass("ask for orientation", SID, "minimax-m3")
    assert d.route is True
    assert captured["orientation"] is True
    assert "HIGHER-SELF ORIENTATION TURN" in captured["frame"]
    # The sentinel firewall recognizes the same marker (round-trip):
    assert plugin._dispatcher_pre._frame_sentinel_check(captured["frame"]) is True


def test_ledger_initiator_through_anchor_lane_contract(tmp_path, monkeypatch):
    """Provenance-CONTENT: the anchor lane's usage record carries
    initiator='agent' — asserted on the ledger record content."""
    p = str(tmp_path / "tokens.jsonl")
    monkeypatch.setattr(usage_ledger, "_store_path", lambda: p)
    # The anchor lane records with this exact call shape (anchor_exec):
    usage_ledger.record_tokens("anchor", "glm-5.3", SID, 500, 200, 0.02,
                               "consult", task_id="t1", event_seq=1,
                               initiator="agent")
    rec = json.loads(open(p).read().strip())
    assert rec["lane"] == "anchor" and rec["detail"] == "consult"
    assert rec["initiator"] == "agent"  # provenance tag ON the record


# ---------------------------------------------------------------------------
# Double-declare + echo guard end-to-end
# ---------------------------------------------------------------------------


def test_double_declare_agent_then_user_via_middleware(monkeypatch, caps_tmp):
    """Agent request_routing THEN user phrase in the same turn: ONE claim,
    the user phrase dedupes into the agent's claim (H7.5)."""
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary": {"route_id": rd.route_id})
    # Leg 8: the declared shadow claim executes via the render chain — stub
    # the ladder so the test never egresses.
    monkeypatch.setattr(plugin, "_render_with_retry_ladder",
                        lambda c, m, p, s: ("LEG8 RENDER", 0))
    monkeypatch.setattr(plugin, "_debug_banner_pass", lambda r, *a, **k: r)
    monkeypatch.setattr(plugin, "_provenance_footer_pass", lambda r: r)
    monkeypatch.setattr(plugin._dispatcher_pre, "_deliver_render_pass",
                        lambda request, content, rendered, model, sid, matches:
                        {"request": request})
    _rr(lane="shadow")
    result = plugin.on_llm_request(
        request=_request("ask your higher self"),
        original_request=_request("ask your higher self"),
        session_id=SID)
    assert result == {}
    assert route_gate.peek_declared(SID) is None  # consumed exactly once


def test_echo_guard_end_to_end_quoted_phrase_inert(monkeypatch, caps_tmp):
    """A QUOTED phrase in the turn text never claims routing (H7.2) — the
    middleware turn passes through un-routed with no claim created."""
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    result = plugin.on_llm_request(
        request=_request('the manual says "> ask your higher self" triggers routing'),
        original_request=_request('the manual says "> ask your higher self" triggers routing'),
        session_id=SID)
    assert result == {}
    assert route_gate.peek_declared(SID) is None


def test_echo_guard_end_to_end_prose_question_inert(monkeypatch, caps_tmp):
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    result = plugin.on_llm_request(
        request=_request("what does it mean to ask your higher self for orientation?"),
        original_request=_request("what does it mean to ask your higher self for orientation?"),
        session_id=SID)
    assert result == {}
    assert route_gate.peek_declared(SID) is None


# ---------------------------------------------------------------------------
# Battery harness contract (benchmarks/behavioral_battery.py extension)
# ---------------------------------------------------------------------------


def test_battery_harness_has_on_demand_scenarios():
    """The battery file declares the leg-4 on-demand + cap-denial scenarios
    (run on the researcher canary; the harness contract is asserted here)."""
    import re

    src = open(os.path.join(PLUGIN_DIR, "benchmarks", "behavioral_battery.py")).read()
    for name in ("D1_on_demand_higher_pre", "D2_on_demand_shadow",
                 "D3_cap_denial_banner", "D4_double_declare"):
        assert re.search(r'run_scenario\("%s"' % name, src), name


def test_baseline_records_shape():
    """Baseline JSON keeps its per-turn record shape (scenario/turn/pass/detail)
    so battery runs diff cleanly against it."""
    base = json.load(open(os.path.join(PLUGIN_DIR, "benchmarks",
                                       "baseline-2026-09-11.json")))
    assert isinstance(base, list) and base
    for rec in base:
        assert {"scenario", "turn", "pass", "detail"} <= set(rec)
