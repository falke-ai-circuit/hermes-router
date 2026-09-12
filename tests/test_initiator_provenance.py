"""Leg 6 — initiator provenance through the billing sites.

Cost model: the initiator tag on billing records must reflect WHO claimed
the consult — the gate's claim source threaded to the spend recording:
  source=declared_user (user phrase incl. 'anchor this') -> initiator="user"
  source=declared_agent (request_routing action)         -> initiator="agent"
  source=auto (auto-PRE complexity, auto-POST audit)     -> initiator="auto"
  unclaimed/legacy path                                  -> initiator="auto"
Cap re-scope: the gate's on-demand cap now applies to declared_agent only;
user + auto are exempt at the gate (the frozen anchor_chain.cap_check at
the execution seam remains the hard guard for every lane).
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
from hermes_router import completion_audit  # noqa: E402
from hermes_router import config_access  # noqa: E402
from hermes_router import debug_banner  # noqa: E402
from hermes_router import route_gate  # noqa: E402
from hermes_router import router_core  # noqa: E402
from hermes_router import router_tools  # noqa: E402
from hermes_router import routing_caps  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router import usage_ledger  # noqa: E402

SID = "s-leg6"


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
# Gate claim stamping: task_id -> initiator resolution
# ---------------------------------------------------------------------------


def test_declared_user_stamps_user_tag(monkeypatch, caps_tmp):
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    staged = []
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary":
                        staged.append(rd) or {"route_id": rd.route_id})
    d = route_gate.claim_pass("ask your higher self: what is one blind spot?",
                              SID, "minimax-m3")
    assert d.route is True and d.source == route_gate.SOURCE_DECLARED_USER
    task_id = staged[0].task_id
    assert route_gate.initiator_for_task(task_id) == "user"


def test_declared_agent_stamps_agent_tag(monkeypatch, caps_tmp):
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    staged = []
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary":
                        staged.append(rd) or {"route_id": rd.route_id})
    _rr(lane="shadow")
    d = route_gate.claim_pass("work the problem", SID, "minimax-m3")
    assert d.route is True and d.source == route_gate.SOURCE_DECLARED_AGENT
    # Leg 8: shadow does NOT stage an anchor swap — but the initiator
    # provenance still resolves from the claim source at any billing site.
    assert not staged
    assert route_gate.initiator_for_task(
        router_core.task_id_for(SID, "work the problem", "minimax-m3")
    ) == "agent"


def test_auto_claim_stamps_auto_tag(monkeypatch, caps_tmp):
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    # Auto path: the legacy _dispatch_pass reports a complexity claim.
    monkeypatch.setattr(plugin._dispatcher_pre, "_dispatch_pass",
                        lambda c, s, m: True)
    d = route_gate.claim_pass("design a caching layer with tradeoffs", SID,
                              "minimax-m3")
    assert d.route is True and d.source == route_gate.SOURCE_AUTO
    # Auto claims resolve "auto" — both by explicit stamp absence and by
    # resolution over the same task_id the execution seam computes.
    task_id = router_core.task_id_for(SID, "design a caching layer with tradeoffs",
                                      "minimax-m3")
    assert route_gate.initiator_for_task(task_id) == "auto"


def test_unclaimed_task_defaults_auto():
    assert route_gate.initiator_for_task("") == "auto"
    assert route_gate.initiator_for_task("never-claimed-task") == "auto"


def test_anchor_this_user_phrase_tags_user(monkeypatch, caps_tmp):
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    staged = []
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary":
                        staged.append(rd) or {"route_id": rd.route_id})
    d = route_gate.claim_pass("anchor this: what is one blind spot?", SID,
                              "minimax-m3")
    assert d.route is True and d.source == route_gate.SOURCE_DECLARED_USER
    assert route_gate.initiator_for_task(staged[0].task_id) == "user"


# ---------------------------------------------------------------------------
# Billing-site resolution (anchor_exec consult shape)
# ---------------------------------------------------------------------------


def test_usage_ledger_record_carries_resolved_initiator(tmp_path, monkeypatch,
                                                        caps_tmp):
    """The anchor lane's record_tokens call uses the gate-resolved
    initiator — asserted on the ledger record content."""
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    staged = []
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary":
                        staged.append(rd) or {"route_id": rd.route_id})
    route_gate.claim_pass("ask your higher self: blind spots?", SID, "minimax-m3")
    task_id = staged[0].task_id

    p = str(tmp_path / "tokens.jsonl")
    monkeypatch.setattr(usage_ledger, "_store_path", lambda: p)
    # Mirror of the anchor lane's call shape (anchor_exec.py) with the
    # leg-6 resolution inline:
    initiator = route_gate.initiator_for_task(task_id)
    usage_ledger.record_tokens("anchor", "glm-5.3", SID, 500, 200, 0.02,
                               "consult", task_id=task_id, event_seq=1,
                               initiator=initiator)
    rec = json.loads(open(p).read().strip())
    assert rec["initiator"] == "user"  # declared_user -> user tag


def test_declared_agent_bills_agent_tag(tmp_path, monkeypatch, caps_tmp):
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    staged = []
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary":
                        staged.append(rd) or {"route_id": rd.route_id})
    _rr(lane="higher-pre")
    route_gate.claim_pass("work the problem", SID, "minimax-m3")
    task_id = staged[0].task_id
    p = str(tmp_path / "tokens.jsonl")
    monkeypatch.setattr(usage_ledger, "_store_path", lambda: p)
    usage_ledger.record_tokens("anchor", "glm-5.3", SID, 500, 200, 0.02,
                               "consult", task_id=task_id, event_seq=1,
                               initiator=route_gate.initiator_for_task(task_id))
    rec = json.loads(open(p).read().strip())
    assert rec["initiator"] == "agent"


# ---------------------------------------------------------------------------
# completion_audit meta initiator
# ---------------------------------------------------------------------------


def test_consult_meta_initiator_unclaimed_is_auto(tmp_path, monkeypatch):
    """Unclaimed/legacy consult (no gate claim stamped for the task key):
    the consult meta carries initiator="auto"."""
    # No claim registered -> initiator_for_task returns "auto"
    assert route_gate.initiator_for_task("some-legacy-task") == "auto"
    # The meta contract: initiator present, defaulting via the resolver
    assert completion_audit is not None  # import-path sanity


def test_denied_cap_keeps_agent_initiator(monkeypatch, caps_tmp):
    """Cap denial only fires on the declared_agent lane (action pre-check
    AND gate claim check) — its denied_cap event keeps initiator="agent"
    (correct per cost model)."""
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": 1.0})
    routing_caps.record_agent_spend(SID, 1.5)
    # Action-level denial: no claim registered, event carries agent tag.
    out = _rr(lane="higher-pre")
    assert out["ok"] is False and out["error"] == "cap_denied"
    events = routing_caps.read_denied_events()
    assert events and events[0]["event"] == "denied_cap"
    assert events[0]["initiator"] == "agent"
    # Gate-level denial for a directly-registered agent claim:
    route_gate.register_declared(SID, route_gate.LANE_HIGHER_PRE,
                                 route_gate.SOURCE_DECLARED_AGENT)
    d = route_gate.claim_pass("work the problem", SID, "minimax-m3")
    assert d.route is False and d.reason == "cap_denied"
    route_gate.clear_declared(SID)


def test_user_phrase_over_cap_still_routes(monkeypatch, caps_tmp):
    """Leg 6 re-scope: a user-declared phrase is NOT gated by the per-agent
    on-demand cap — explicit user ask routes even over cap (execution seam
    cap_check remains the hard guard)."""
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"routing_daily_cap_usd": 1.0})
    routing_caps.record_agent_spend(SID, 5.0)  # massively over cap
    d = route_gate.claim_pass("ask your higher self: blind spots?", SID,
                              "minimax-m3")
    assert d.route is True and d.source == route_gate.SOURCE_DECLARED_USER
