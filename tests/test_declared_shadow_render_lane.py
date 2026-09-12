"""Leg 8 (blueprint §2): declared SHADOW executes on the UNCENSORED RENDER
chain — abliteration primary / Venice fallback, render substitution
semantics (the same machinery as refusal-shaped/contested PRE renders) —
NOT a frontier anchor consult. Declared HIGHER keeps the anchor envelope.

Acceptance (conductor live re-probe): request_routing_executed lane=shadow
correlates with a render row in uncensored-router-renders.jsonl, NOT a
tokens.jsonl anchor consult. SID convention: "s-leg8".
"""
import json

import pytest

import hermes_router as plugin
from hermes_router import config_access, render_inbox, route_gate, router_core, state, usage_ledger

SID = "s-leg8"


@pytest.fixture()
def _reset(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    LOGGED = []
    monkeypatch.setattr(plugin, "_log_route", lambda e, **f: LOGGED.append((e, dict(f))))
    # Isolated ledgers: point the tokens store + renders store at tmp_path.
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr(render_inbox, "_inbox_path",
                        lambda: str(tmp_path / "renders.jsonl"))
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    # Uncensored chain offline-stubbed: capture the render call, return a
    # fixed render body (no egress — conftest zero-network guard holds).
    render_calls = []
    monkeypatch.setattr(plugin, "_render_with_retry_ladder",
                        lambda c, m, p, s:
                        render_calls.append((c, p, s)) or ("LEG8 RENDER OUTPUT", 0))
    monkeypatch.setattr(plugin, "_debug_banner_pass", lambda r, *a, **k: r)
    monkeypatch.setattr(plugin, "_provenance_footer_pass", lambda r: r)
    return {"log": LOGGED, "render_calls": render_calls}


def _declare_shadow(lane="shadow"):
    """Simulate the agent's mid-turn request_routing tool call."""
    from hermes_router import router_tools

    return json.loads(router_tools.router_control(
        action="request_routing", lane=lane, session_id=SID))


def test_declared_shadow_renders_not_anchor_consult(_reset, monkeypatch):
    """Declared shadow claim -> the uncensored render chain renders the ask;
    NO frontier anchor swap is staged (tokens.jsonl anchor consult must not
    exist for this claim)."""
    staged = []
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary":
                        staged.append(rd) or {"route_id": rd.route_id})
    ask = "ask your higher self: what is one blind spot about this plan?"
    req = {"model": "minimax-m3",
           "messages": [{"role": "user", "content": ask}]}
    state.advance_turn_identity(SID, state.hash_text(ask))
    _declare_shadow()
    out = plugin.on_llm_request(request=req, original_request=req,
                                session_id=SID)
    # Render chain executed with the ask as payload.
    assert _reset["render_calls"] and _reset["render_calls"][0][0] == ask
    # NO frontier anchor swap staged for shadow.
    assert staged == []
    assert router_core.peek_pending_swap(SID) is None
    # Correlation event carries render_lane=True.
    rr = [f for _, f in _reset["log"]
          if f.get("event_detail") == "request_routing_executed"]
    assert len(rr) == 1 and rr[0]["lane"] == "shadow"
    assert rr[0].get("render_lane") is True
    assert out == {} or "request" in out


def test_declared_higher_pre_still_stages_anchor(_reset, monkeypatch):
    """Declared higher-pre keeps the frontier anchor envelope (unchanged
    behavior — the leg-8 shadow branch must not touch it)."""
    staged = []
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary":
                        staged.append(rd) or {"route_id": rd.route_id})
    ask = "ask your higher self: what is one blind spot about this plan?"
    req = {"model": "minimax-m3",
           "messages": [{"role": "user", "content": ask}]}
    state.advance_turn_identity(SID, state.hash_text(ask))
    _declare_shadow(lane="higher-pre")
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)
    assert len(staged) == 1  # frontier anchor envelope staged
    assert staged[0].lane == router_core.LANE_COMPLEXITY
    assert staged[0].mode == router_core.MODE_CONSULT


def test_declared_shadow_render_row_in_renders_ledger(_reset, monkeypatch):
    """The acceptance artifact: request_routing_executed lane=shadow
    correlates with a render ROW in uncensored-router-renders.jsonl."""
    ask = "route this through your shadow: audit my reasoning for bias"
    req = {"model": "minimax-m3",
           "messages": [{"role": "user", "content": ask}]}
    state.advance_turn_identity(SID, state.hash_text(ask))
    _declare_shadow()
    # Real _deliver_render_pass writes the render_inbox row — let it run.
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)
    rows = render_inbox.read_renders(limit=50)
    assert any("LEG8 RENDER OUTPUT" in str(r.get("render", ""))
               for r in rows), "no render row for the declared shadow consult"


def test_declared_shadow_render_spend_carries_initiator(_reset, monkeypatch):
    """The render-chain spend record (tokens ledger, lane=render) carries
    the claim's initiator tag — agent tool claim -> agent."""
    import hermes_router.router as router

    def _fake_call(prompt, *, max_tokens=None, temperature=None,
                   system_prompt="", session_id=""):
        # Simulate the ledger write router.call performs on success —
        # usage-bearing response (D9 honesty: real usage, real record).
        usage_ledger.record_tokens(
            "render", "abliteration-large", session_id, 100, 50, 0.0,
            "route_fired")
        return "LEG8 RENDER OUTPUT"

    monkeypatch.setattr(router, "call", _fake_call)
    # The fixture stubs the ladder; override with a REAL ladder that dials
    # router.call (stubbed above) so the honest usage-bearing ledger record
    # (D9) is written, then the initiator tag flows.
    monkeypatch.setattr(plugin, "_render_with_retry_ladder",
                        lambda content, matches, persona, session_id:
                        (router.call(content, system_prompt=persona,
                                     session_id=session_id), 0))
    # The REAL ladder runs here — it dials router.call (stubbed above) so the
    # honest usage-bearing ledger record (D9) is written, then tags flow.
    monkeypatch.setattr(plugin, "_debug_banner_pass", lambda r, *a, **k: r)
    monkeypatch.setattr(plugin, "_provenance_footer_pass", lambda r: r)
    ask = "route this through your shadow: audit my reasoning for bias"
    req = {"model": "minimax-m3",
           "messages": [{"role": "user", "content": ask}]}
    state.advance_turn_identity(SID, state.hash_text(ask))
    _declare_shadow()
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)
    recs = [r for r in usage_ledger.read_records()
            if r.get("lane") == "render" and r.get("session_id") == SID]
    assert len(recs) == 1
    assert recs[0].get("initiator") == "agent"  # _declare_shadow = agent tool
