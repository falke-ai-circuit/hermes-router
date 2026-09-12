"""Leg 11 (Goran-direct): the declared SHADOW lane emits its own §10.4
debug banner, parked at the shadow execution seam and consumed/appended at
the DELIVERY edge by on_transform_llm_output — same machinery as the
frontier PRE/POST banners. Contract:

- declared shadow turn parks AND consumes the banner (banner appears in
  the delivered head of the follow-up turn's delivery);
- banner carries model / initiator / cost fields (L1 one-liner);
- canonical artifacts (render_inbox row, substance stash) have NO banner —
  the append happens ONLY at the delivery boundary;
- L0 (debug_banner knob off) -> no banner parked;
- failure-isolated: banner errors never break the render delivery.

SID convention: "s-leg11".
"""
import json

import pytest

import hermes_router as plugin
from hermes_router import config_access, debug_banner, render_inbox, route_gate, router_core, state, usage_ledger

SID = "s-leg11"

LOGGED = []


@pytest.fixture()
def _reset(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    debug_banner._ANCHOR_BANNERS.clear()
    LOGGED.clear()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr(render_inbox, "_inbox_path",
                        lambda: str(tmp_path / "renders.jsonl"))
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"debug_banner": 1})
    # conftest._isolate_router_config pins debug_banner._banner_section to
    # {} — override it too so the L1 knob is ON for these tests (the L0
    # test disables the banner via debug_banner_enabled itself).
    monkeypatch.setattr(debug_banner, "_banner_section",
                        lambda: {"debug_banner": 1}, raising=True)
    monkeypatch.setattr(plugin, "_render_with_retry_ladder",
                        lambda c, m, p, s: ("LEG11 RENDER OUTPUT", 0))
    monkeypatch.setattr(plugin, "_debug_banner_pass", lambda r, *a, **k: r)
    monkeypatch.setattr(plugin, "_provenance_footer_pass", lambda r: r)
    yield


def _declare_shadow():
    from hermes_router import router_tools
    return json.loads(router_tools.router_control(
        action="request_routing", lane="shadow", session_id=SID))


def _shadow_turn(req, monkeypatch):
    """Drive one declared-shadow turn through the middleware."""
    import hermes_router.router as router
    monkeypatch.setattr(router, "call",
                        lambda prompt, *, max_tokens=None, temperature=None,
                        system_prompt="", session_id="": "RAW RENDER")
    # Ladder stub must dial the stubbed router.call so the render-lane
    # ledger record exists (banner data tap reads it).
    monkeypatch.setattr(
        plugin, "_render_with_retry_ladder",
        lambda content, matches, persona, session_id:
        (router.call(content, system_prompt=persona,
                     session_id=session_id), 0))
    state.advance_turn_identity(SID, state.hash_text(
        req["messages"][-1]["content"]))
    _declare_shadow()
    return plugin.on_llm_request(request=req, original_request=req,
                                 session_id=SID)


def test_shadow_banner_parked_with_required_fields(_reset, monkeypatch):
    ask = "route this through your shadow: audit my reasoning for bias"
    req = {"model": "minimax-m3",
           "messages": [{"role": "user", "content": ask}]}
    _shadow_turn(req, monkeypatch)
    parked = debug_banner._ANCHOR_BANNERS.get(SID, "")
    assert parked, "shadow banner not parked"
    assert "shadow" in parked
    assert "shadow_declared" in parked
    assert "initiator=agent" in parked  # tool claim = agent
    assert "abliterated" in parked or "@" in parked  # model field present
    assert "$" in parked  # cost field present
    # Route-log receipt:
    rec = [f for _, f in LOGGED
           if f.get("event_detail") == "debug_banner_emitted"
           and f.get("lane") == "shadow"]
    assert len(rec) == 1
    assert rec[0].get("model")
    assert rec[0].get("est_cost") is not None


def test_shadow_banner_user_initiator(_reset, monkeypatch):
    """A user-phrase claim (source=declared_user) tags initiator=user."""
    ask = "route this through your shadow: audit my reasoning"
    req = {"model": "minimax-m3",
           "messages": [{"role": "user", "content": ask}]}
    state.advance_turn_identity(SID, state.hash_text(ask))
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)
    parked = debug_banner._ANCHOR_BANNERS.get(SID, "")
    assert parked and "initiator=user" in parked


def test_banner_in_canonical_row_free(_reset, monkeypatch):
    """§10.4-F: canonical artifacts carry NO banner — the render_inbox row
    and the stash hold the clean render; the banner exists ONLY in the
    parked delivery buffer until consumed at the delivery edge."""
    ask = "route this through your shadow: audit my reasoning for bias"
    req = {"model": "minimax-m3",
           "messages": [{"role": "user", "content": ask}]}
    _shadow_turn(req, monkeypatch)
    rows = render_inbox.read_renders(limit=50)
    shadow_rows = [r for r in rows if "RAW RENDER" in str(r.get("render", ""))]
    assert shadow_rows, "render row missing"
    for r in shadow_rows:
        assert "· router ·" not in str(r.get("render", ""))


def test_banner_consumed_at_delivery_edge(_reset, monkeypatch):
    """The parked banner is consumed by on_transform_llm_output's benign
    delivery edge (§10.4) and appended to the turn's delivery — one-shot."""
    ask = "route this through your shadow: audit my reasoning for bias"
    req = {"model": "minimax-m3",
           "messages": [{"role": "user", "content": ask}]}
    _shadow_turn(req, monkeypatch)
    assert debug_banner._ANCHOR_BANNERS.get(SID)
    out = plugin.on_transform_llm_output(
        response_text="the agent's own visible reply", session_id=SID,
        model="minimax-m3")
    # Banner appended to the delivery representation...
    assert "· router ·" in (out or "")
    assert "shadow" in (out or "")
    assert "the agent's own visible reply" in (out or "")
    # ...and consumed (one-shot): a second pass sees nothing.
    out2 = plugin.on_transform_llm_output(
        response_text="plain next turn", session_id=SID, model="minimax-m3")
    assert "· router ·" not in (out2 or "plain next turn")


def test_l0_knob_off_no_banner(_reset, monkeypatch):
    """debug_banner knob L0 (off) -> NO banner parked, turn unaffected."""
    monkeypatch.setattr(debug_banner, "debug_banner_enabled", lambda: False)
    ask = "route this through your shadow: audit my reasoning for bias"
    req = {"model": "minimax-m3",
           "messages": [{"role": "user", "content": ask}]}
    _shadow_turn(req, monkeypatch)
    assert not debug_banner._ANCHOR_BANNERS.get(SID)
    assert not [f for _, f in LOGGED
                if f.get("event_detail") == "debug_banner_emitted"
                and f.get("lane") == "shadow"]
    # Delivery still completes (render row written):
    rows = render_inbox.read_renders(limit=50)
    assert any("RAW RENDER" in str(r.get("render", ""))
               for r in rows)


def test_banner_failure_isolated(_reset, monkeypatch):
    """Banner machinery failure must NEVER break the shadow render turn."""
    monkeypatch.setattr(debug_banner, "format_banner",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("boom")))
    ask = "route this through your shadow: audit my reasoning for bias"
    req = {"model": "minimax-m3",
           "messages": [{"role": "user", "content": ask}]}
    out = _shadow_turn(req, monkeypatch)  # must not raise
    rows = render_inbox.read_renders(limit=50)
    assert any("RAW RENDER" in str(r.get("render", ""))
               for r in rows)
    assert out == {} or "request" in out
