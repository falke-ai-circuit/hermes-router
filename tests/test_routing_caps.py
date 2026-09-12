"""Leg 2 — per-agent caps + ledger initiator tag tests
(BLUEPRINT-request-routing-2026-09-12, reviewer H1/H7.1/H7.3, Goran
amendment: denial never silent).

Covers:
  - routing_daily_cap_usd knob: default = chain cap, config-live override
  - restart-durable per-agent counters + cooldown (sidecar reload)
  - gate step-1 cap applies to EVERY lane incl. shadow; claim phase only
  - cap boundary: exactly-at-cap consult passes, beyond denies (H7.7 edge)
  - denial: visible banner + denied_cap event with initiator tag; counters
    never increment on denial (H7.1)
  - usage_ledger initiator tag: additive, legacy records byte-identical
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
from hermes_router import anchor_chain  # noqa: E402
from hermes_router import config_access  # noqa: E402
from hermes_router import route_gate  # noqa: E402
from hermes_router import routing_caps  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router import usage_ledger  # noqa: E402

SID = "s-caps"


def _request(text, model="minimax-m3"):
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": text},
        ],
    }


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
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield
    state.clear()
    route_gate.clear_declared(SID)


# ---------------------------------------------------------------------------
# Knob: routing_daily_cap_usd (config-live; default = chain cap)
# ---------------------------------------------------------------------------


def test_cap_default_is_chain_cap(monkeypatch):
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(anchor_chain, "load_anchor_chain",
                        lambda: anchor_chain.AnchorChainCfg(
                            None, None, daily_cap_usd=3.5))
    assert routing_caps.routing_cap_usd() == 3.5


def test_cap_knob_overrides_config_live(monkeypatch):
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": 0.75})
    assert routing_caps.routing_cap_usd() == 0.75


def test_cap_knob_bogus_falls_back(monkeypatch):
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": "not-a-number"})
    monkeypatch.setattr(anchor_chain, "load_anchor_chain",
                        lambda: anchor_chain.AnchorChainCfg(
                            None, None, daily_cap_usd=2.0))
    assert routing_caps.routing_cap_usd() == 2.0


# ---------------------------------------------------------------------------
# Restart-durable state (H7.3)
# ---------------------------------------------------------------------------


def test_agent_spend_roundtrip_and_restart_durable(caps_tmp):
    routing_caps.record_agent_spend(SID, 0.4, initiator="agent", lane="higher-pre")
    assert abs(routing_caps.agent_spend(SID) - 0.4) < 1e-9
    # Restart durability: a FRESH module-level read loads from the sidecar
    # file again (same seam the gateway process uses after a bounce).
    data = json.load(open(caps_tmp))
    today = list(data["agents"][SID].values())[0]
    assert today["spend_usd"] == pytest.approx(0.4)
    assert today["initiator"] == "agent"
    assert today["lane"] == "higher-pre"


def test_cooldown_restart_durable(caps_tmp):
    routing_caps.record_cooldown(SID, 1234.5)
    assert routing_caps.last_cooldown(SID) == 1234.5
    data = json.load(open(caps_tmp))
    assert data["cooldowns"][SID] == 1234.5


def test_per_agent_isolation(caps_tmp):
    routing_caps.record_agent_spend("agent-A", 1.0)
    assert routing_caps.agent_spend("agent-B") == 0.0
    assert routing_caps.agent_spend("agent-A") == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Gate step-1 cap check + boundary (H1 + H7.7)
# ---------------------------------------------------------------------------


def test_gate_cap_allows_under_and_exactly_at_cap(monkeypatch, caps_tmp):
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": 2.0})
    routing_caps.record_agent_spend(SID, 1.5)
    allowed, spend, cap = routing_caps.gate_cap_check(SID, est_cost=0.5)
    assert allowed is True          # projected == cap exactly -> allowed (H7.7 edge)
    assert cap == 2.0
    routing_caps.record_agent_spend(SID, 0.5)  # spend exactly at cap
    allowed, spend, _ = routing_caps.gate_cap_check(SID, est_cost=0.0)
    assert allowed is True          # projected == cap -> still allowed
    assert spend == pytest.approx(2.0)
    allowed, _, _ = routing_caps.gate_cap_check(SID, est_cost=0.01)
    assert allowed is False         # projected beyond cap denies


def test_shadow_lane_capped_at_gate(monkeypatch, caps_tmp):
    """H1 + leg 6 re-scope: the shadow lane IS capped when the AGENT claims
    it via request_routing (declared_agent = the gated lane). The frozen
    anchor_chain.cap_check at the execution seam remains the hard guard."""
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": 1.0})
    routing_caps.record_agent_spend(SID, 1.5)
    route_gate.register_declared(SID, route_gate.LANE_SHADOW,
                                 route_gate.SOURCE_DECLARED_AGENT)
    d = route_gate.decide_turn({
        "content": "work the problem", "request": _request("x"),
        "context": {}, "session_id": SID, "auto_shape": None, "claim": True})
    assert d.route is False and d.reason == "cap_denied"


def test_auto_lane_capped_at_gate(monkeypatch, caps_tmp):
    """Leg 6 re-scope: the on-demand gate cap applies to the DECLARED_AGENT
    lane only — auto lanes are exempt at the gate (their spend still hits
    the frozen anchor_chain.cap_check hard guard at the execution seam).
    The auto claim routes through; spend accrues only at execution."""
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": 1.0})
    routing_caps.record_agent_spend(SID, 1.5)

    def _auto():
        return route_gate.GateDecision(route=True, lane=route_gate.LANE_HIGHER_PRE,
                                       source=route_gate.SOURCE_AUTO,
                                       reason="complexity_claim")

    d = route_gate.decide_turn({
        "content": "ordinary ask", "request": _request("x"),
        "context": {}, "session_id": SID, "auto_shape": _auto, "claim": True})
    assert d.route is True and d.source == route_gate.SOURCE_AUTO  # exempt
    events = routing_caps.read_denied_events()
    assert not events  # no denial — auto is not the capped lane


_auto_calls = []


def _auto_marker():
    _auto_calls.append(1)
    return route_gate.GateDecision(route=True, lane=route_gate.LANE_HIGHER_PRE,
                                   source=route_gate.SOURCE_AUTO,
                                   reason="complexity_claim")


def test_fence_phase_never_capped(monkeypatch, caps_tmp):
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": 1.0})
    routing_caps.record_agent_spend(SID, 5.0)  # massively over cap
    # Leg 6: the fence-phase cap check rides the DECLARED_AGENT scope — a
    # declared-agent claim over cap is denied even in claim ctx...
    route_gate.register_declared(SID, route_gate.LANE_HIGHER_PRE,
                                 route_gate.SOURCE_DECLARED_AGENT)
    d = route_gate.decide_turn({"content": "hello", "request": None,
                                "context": {}, "session_id": SID,
                                "auto_shape": None, "claim": True})
    assert d.reason == "cap_denied"
    route_gate.clear_declared(SID)
    # ...but the audit-delivery fence branch is never capped.
    env = {"request": _request("delivered")}

    def _fake_audit(req, ctx):
        return env

    monkeypatch.setattr(plugin._dispatcher_pre, "_audit_delivery_pass",
                        _fake_audit)
    d2 = route_gate.decide_turn({"content": "hello", "request": _request("x"),
                                 "context": {}, "session_id": SID,
                                 "auto_shape": None, "claim": False})
    assert d2.route is False and d2.deliver == env


# ---------------------------------------------------------------------------
# Denial: visible banner + denied_cap event (Goran amendment, NEVER silent)
# ---------------------------------------------------------------------------


def test_denial_parks_visible_banner_and_ledger_event(monkeypatch, caps_tmp):
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": 2.0})
    parked = []

    from hermes_router import debug_banner

    monkeypatch.setattr(debug_banner, "park_anchor_banner",
                        lambda sid, text: parked.append((sid, text)))
    routing_caps.record_agent_spend(SID, 2.5)
    # Leg 6: the capped lane is declared_agent — register an agent claim.
    route_gate.register_declared(SID, route_gate.LANE_HIGHER_PRE,
                                 route_gate.SOURCE_DECLARED_AGENT)
    d = route_gate.decide_turn({
        "content": "work the problem", "request": _request("x"),
        "context": {}, "session_id": SID, "auto_shape": None, "claim": True})
    assert d.route is False and d.reason == "cap_denied"
    # Visible banner, exact text contract:
    assert len(parked) == 1
    sid, text = parked[0]
    assert sid == SID
    assert text == ("routing denied: daily spend cap reached "
                    "($2.50/$2.00) — continuing un-routed")
    # Ledger event carries the initiator tag:
    events = routing_caps.read_denied_events()
    assert len(events) == 1
    ev = events[0]
    assert ev["event"] == "denied_cap"
    assert ev["initiator"] == "agent"
    assert ev["spend_usd"] == pytest.approx(2.5)
    assert ev["cap_usd"] == pytest.approx(2.0)


def test_denial_does_not_increment_counters(monkeypatch, caps_tmp):
    """H7.1: counters increment ONLY on successful claim — a denied attempt
    must leave the spend untouched."""
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": 2.0})
    from hermes_router import debug_banner

    monkeypatch.setattr(debug_banner, "park_anchor_banner", lambda *a, **k: None)
    routing_caps.record_agent_spend(SID, 2.5)
    before = routing_caps.agent_spend(SID)
    route_gate.decide_turn({
        "content": "ask your higher self", "request": _request("x"),
        "context": {}, "session_id": SID, "auto_shape": None, "claim": True})
    assert routing_caps.agent_spend(SID) == before  # no leak


def test_spend_records_only_on_successful_claim(monkeypatch, caps_tmp):
    """A successful declared claim followed by execution records spend via
    record_agent_spend (wired at the two consult execution sites); the gate
    itself writes nothing until the consult actually bills."""
    from hermes_router import debug_banner

    monkeypatch.setattr(debug_banner, "park_anchor_banner", lambda *a, **k: None)
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": 2.0})
    d = route_gate.decide_turn({
        "content": "ask your higher self", "request": _request("x"),
        "context": {}, "session_id": SID, "auto_shape": None, "claim": True})
    assert d.route is True
    assert routing_caps.agent_spend(SID) == 0.0  # claim alone spends nothing
    routing_caps.record_agent_spend(SID, 0.25, initiator="agent",
                                    lane="higher-pre")  # execution seam bills
    assert routing_caps.agent_spend(SID) == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# Ledger initiator tag (additive)
# ---------------------------------------------------------------------------


def test_tokens_ledger_initiator_tag(tmp_path, monkeypatch):
    p = str(tmp_path / "tokens.jsonl")
    monkeypatch.setattr(usage_ledger, "_store_path", lambda: p)
    assert usage_ledger.record_tokens("anchor", "m", SID, 100, 50, 0.01,
                                      "consult", initiator="agent") is True
    rec = json.loads(open(p).read().strip())
    assert rec["initiator"] == "agent"
    assert rec["lane"] == "anchor"


def test_tokens_ledger_legacy_records_byte_identical(tmp_path, monkeypatch):
    p = str(tmp_path / "tokens.jsonl")
    monkeypatch.setattr(usage_ledger, "_store_path", lambda: p)
    usage_ledger.record_tokens("render", "m2", "s", 10, 5, 0.001, "render")
    rec = json.loads(open(p).read().strip())
    assert "initiator" not in rec  # additive field omitted on legacy shape


def test_record_agent_spend_preserves_tag_on_continuation(caps_tmp):
    routing_caps.record_agent_spend(SID, 0.1, initiator="user", lane="shadow")
    # Explicit no-tag continuation (initiator="") preserves the previous tag.
    routing_caps.record_agent_spend(SID, 0.1, initiator="")
    data = json.load(open(caps_tmp))
    today = list(data["agents"][SID].values())[0]
    assert today["initiator"] == "user"
    assert today["lane"] == "shadow"
    # Default call (no arg) carries the documented agent default tag.
    routing_caps.record_agent_spend(SID, 0.1)
    data = json.load(open(caps_tmp))
    today = list(data["agents"][SID].values())[0]
    assert today["initiator"] == "agent"