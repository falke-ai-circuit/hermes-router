"""R78U M34-11 — claimed must mean PRODUCED or RELEASED.

Measured live (shadow, 2026-11-02 03:13:37Z):
`declared_claim_execute_once claim_source=aux_intent` -> immediately
`claim_standdown` -> `audit_gate_skip reason=turn_claimed`. The turn's ONE
uncensored claim was consumed and produced no render, and the audit leg
excused itself BECAUSE the claim was taken. A claimed turn that produces
nothing is a silent drop: the claim is the licence, and it was spent without
delivery.

Contract pinned here (events only, no LLM judge):
  (a) a claim that RENDERS emits `claim_produced` (with a reason) and the
      audit leg is not skipped on the basis of that claim;
  (b) a claim that produces NOTHING emits `claim_released_unproduced` (with a
      reason), the claim is RELEASED (a subsequent eligible claim can still
      fire), and the audit leg RUNS instead of skipping on `turn_claimed`.
"""
from __future__ import annotations

import pytest

import hermes_router as plugin
from hermes_router import completion_audit as ca
from hermes_router import config_access, render_inbox, route_gate, router_core
from hermes_router import state, usage_ledger

SID = "s-m34-11"
LONG_BODY = "agent answer text long enough here " + "x" * 520


@pytest.fixture()
def _reset(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    events = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: events.append((e, dict(f))))
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr(render_inbox, "_inbox_path",
                        lambda: str(tmp_path / "renders.jsonl"))
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(plugin, "_debug_banner_pass", lambda r, *a, **k: r)
    monkeypatch.setattr(plugin, "_provenance_footer_pass", lambda r: r)
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary", **kw: {"route_id": "r1"})
    return {"events": events}


def _declare_shadow(session_id=SID, lane="shadow"):
    from hermes_router import router_tools

    return router_tools.router_control(
        action="request_routing", lane=lane, session_id=session_id)


def _details(events):
    return [f.get("event_detail") for _, f in events]


def _audit(session_id, monkeypatch, events):
    """Run the POST audit gate, returning (eligible_called, gate_cause, evts)."""
    calls = []
    monkeypatch.setattr(ca, "_log", lambda ev, **kw: events.append((ev, dict(kw))))
    monkeypatch.setattr(ca, "_log_pair", lambda *a, **k: None)
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "audit_mode", lambda: "complex")
    monkeypatch.setattr(ca, "_pre_pairing", lambda *a, **k: None)
    monkeypatch.setattr(ca, "eligible",
                        lambda *a, **k: (calls.append(1) or (False, "test")))
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 1)
    ca._set_gate_cause("")
    ca.audit_gate(session_id, LONG_BODY, ask_override="audit this plan please")
    return bool(calls), ca.gate_cause(), events


# --- (a) a claim that renders is PRODUCED -----------------------------------

def test_rendering_claim_emits_claim_produced(_reset, monkeypatch):
    """A declared shadow claim whose render lands emits `claim_produced`."""
    monkeypatch.setattr(plugin, "_render_with_retry_ladder",
                        lambda c, m, p, s: ("M34-11 RENDER OUTPUT", 0))
    ask = "route this through your shadow: audit my reasoning for bias"
    req = {"model": "m3", "messages": [{"role": "user", "content": ask}]}
    state.advance_turn_identity(SID, state.hash_text(ask))
    _declare_shadow()
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)

    rows = [f for _, f in _reset["events"]
            if f.get("event_detail") == "claim_produced"]
    assert rows, _details(_reset["events"])
    assert rows[0].get("claim_lane") == "shadow", rows[0]
    assert rows[0].get("reason"), rows[0]
    # No unproduced-release for a claim that produced.
    assert "claim_released_unproduced" not in _details(_reset["events"])


def test_produced_claim_audit_not_skipped_on_turn_claimed(_reset, monkeypatch):
    """The audit leg's stand-down must never be justified by a claim alone."""
    monkeypatch.setattr(plugin, "_render_with_retry_ladder",
                        lambda c, m, p, s: ("M34-11 RENDER OUTPUT", 0))
    ask = "route this through your shadow: audit my reasoning for bias"
    req = {"model": "m3", "messages": [{"role": "user", "content": ask}]}
    state.advance_turn_identity(SID, state.hash_text(ask))
    _declare_shadow()
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)

    _, cause, evts = _audit(SID, monkeypatch, _reset["events"])
    assert cause != "turn_claimed", cause
    assert not [k for ev, k in evts
                if ev == "audit_gate_skip" and k.get("reason") == "turn_claimed"]


# --- (b) a claim that produces nothing is RELEASED --------------------------

def test_unproduced_claim_is_released_not_stood_down(_reset, monkeypatch):
    """Empty render = the claim produced nothing -> explicit RELEASE."""
    monkeypatch.setattr(plugin, "_render_with_retry_ladder",
                        lambda c, m, p, s: ("", 0))
    ask = "route this through your shadow: audit my reasoning for bias"
    req = {"model": "m3", "messages": [{"role": "user", "content": ask}]}
    state.advance_turn_identity(SID, state.hash_text(ask))
    _declare_shadow()
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)

    rel = [f for _, f in _reset["events"]
           if f.get("event_detail") == "claim_released_unproduced"]
    assert rel, _details(_reset["events"])
    assert rel[0].get("claim_lane") == "shadow", rel[0]
    assert rel[0].get("reason"), rel[0]
    # Released means the turn is open again: no claim record survives.
    assert route_gate.claim_state(SID) is None


def test_released_claim_lets_a_subsequent_claim_fire(_reset, monkeypatch):
    """A released claim frees the turn: the next eligible claim can still fire."""
    monkeypatch.setattr(plugin, "_render_with_retry_ladder",
                        lambda c, m, p, s: ("", 0))
    ask = "route this through your shadow: audit my reasoning for bias"
    req = {"model": "m3", "messages": [{"role": "user", "content": ask}]}
    state.advance_turn_identity(SID, state.hash_text(ask))
    _declare_shadow()
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)

    # The released turn accepts a fresh claim (nothing is consumed any more).
    assert route_gate.register_declared(SID, route_gate.LANE_SHADOW,
                                        route_gate.SOURCE_DECLARED_USER) is True
    assert route_gate.claim_state(SID) is not None


def test_unproduced_claim_audit_leg_runs(_reset, monkeypatch):
    """An unproduced claim must NOT excuse the audit leg: it runs (and the
    claim is released with a reason)."""
    monkeypatch.setattr(plugin, "_render_with_retry_ladder",
                        lambda c, m, p, s: ("", 0))
    ask = "route this through your shadow: audit my reasoning for bias"
    req = {"model": "m3", "messages": [{"role": "user", "content": ask}]}
    state.advance_turn_identity(SID, state.hash_text(ask))
    _declare_shadow()
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)

    eligible_called, cause, evts = _audit(SID, monkeypatch, _reset["events"])
    assert cause != "turn_claimed", cause
    assert eligible_called, "audit leg must RUN past the claim stand-down"
    assert not [k for ev, k in evts
                if ev == "audit_gate_skip" and k.get("reason") == "turn_claimed"]


def test_consumed_unproduced_record_audit_releases_it(monkeypatch):
    """Bell at the audit seam: a CONSUMED (executed) claim with no production
    is released there too — it can never justify `turn_claimed`."""
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    route_gate.stamp_turn_claim(SID, route_gate.LANE_SHADOW,
                                route_gate.SOURCE_AUX_INTENT, executed=True)
    events = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: events.append((e, dict(f))))
    eligible_called, cause, evts = _audit(SID, monkeypatch, events)
    assert cause != "turn_claimed", cause
    assert eligible_called
    released = [f for _, f in evts if f.get("event_detail") ==
                "claim_released_unproduced"]
    assert released, _details(evts)
    assert released[0].get("reason") == "audit_gate_unproduced_claim", released[0]
    assert route_gate.claim_state(SID) is None
