"""R6 leg 2 — aux classifier seam for the named-model override.

The aux intent path (not just the declared-phrase path) carries
model_override to the claim when the verdict is a higher-* lane AND a
model alias was detected. Alias names are WEAK suspects: only in
combination with frontier/consult phrases do they trigger the aux
classify — a bare alias in prose stays inert (FP doctrine).
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
from hermes_router import anchor_chain, intent_classifier as ic  # noqa: E402
from hermes_router import route_gate, router_core, state  # noqa: E402

SID = "s-r6-leg2"
LOGGED = []


def _cfg(monkeypatch, models=None, primary="nous://z-ai/glm-5.3"):
    ac = {"primary": primary, "overflow": "pass_through"}
    if models is not None:
        ac["models"] = models
    cfg = {"hermes_router": {"anchor_chain": ac}}
    monkeypatch.setattr(
        "hermes_cli.config.load_config", lambda: cfg, raising=False)


def _verdict(lane, subtype, confidence):
    return json.dumps({"choices": [{"message": {"content": json.dumps(
        {"lane": lane, "subtype": subtype, "confidence": confidence})}}]})


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    ic.reset_cache()
    LOGGED.clear()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(plugin._dispatcher_pre, "_dispatch_pass",
                        lambda c, s, m: False)
    yield
    router_core._test_reset()
    plugin.state.clear()
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    ic.reset_cache()


def _decide(monkeypatch, text):
    _cfg(monkeypatch, models={"astra": "openai/gpt-6-astra-pro-flex",
                              "luna": "openai/gpt-5.6-luna-pro"})
    state.advance_turn_identity(SID, state.hash_text(text))
    return route_gate.decide_turn({
        "content": text, "request": {"messages": []}, "context": {},
        "session_id": SID, "auto_shape": None, "claim": True})


def test_aux_higher_verdict_carries_override(monkeypatch):
    # aux classifies higher (pre); alias detected on the ask's directive
    # line -> override attached.
    monkeypatch.setattr(ic, "_aux_transport", lambda p, t: _verdict(
        "higher", "pre", 0.9))
    text = "before we start I would love astra second opinion on this"
    d = _decide(monkeypatch, text)
    assert d.route and d.lane == route_gate.LANE_HIGHER_PRE
    # declared-phrase path wins precedence; either way the override rides
    assert d.source in (route_gate.SOURCE_AUX_INTENT,
                        route_gate.SOURCE_DECLARED_USER)
    assert d.model_override == ("astra", "openai/gpt-6-astra-pro-flex")
    ev = next(f for _, f in LOGGED
              if f.get("event_detail") == "model_override_detected")
    assert ev["alias"] == "astra"


def test_aux_shadow_verdict_no_override(monkeypatch):
    monkeypatch.setattr(ic, "_aux_transport", lambda p, t: _verdict(
        "shadow", None, 0.9))
    text = "give me a brutal read on my plan, ask astra too"
    d = _decide(monkeypatch, text)
    if d.route and d.source == route_gate.SOURCE_AUX_INTENT:
        assert d.model_override is None


def test_aux_no_alias_no_override(monkeypatch):
    monkeypatch.setattr(ic, "_aux_transport", lambda p, t: _verdict(
        "higher", "pre", 0.9))
    text = "get me a second opinion on this plan"
    d = _decide(monkeypatch, text)
    assert d.route and d.model_override is None
    assert "model_override_detected" not in [
        f.get("event_detail") for _, f in LOGGED]


def test_bare_alias_in_prose_is_inert(monkeypatch):
    # No frontier phrase -> alias-combo suspect must NOT fire, aux not called
    # (transport would fail the test if called), no route.
    calls = []

    def _boom(payload, timeout):
        calls.append(payload)
        return _verdict("higher", "pre", 0.9)

    monkeypatch.setattr(ic, "_aux_transport", _boom)
    _cfg(monkeypatch, models={"astra": "openai/gpt-6-astra-pro-flex"})
    text = "I really like what astra did with the pricing this week"
    state.advance_turn_identity(SID, state.hash_text(text))
    d = route_gate.decide_turn({
        "content": text, "request": {"messages": []}, "context": {},
        "session_id": SID, "auto_shape": None, "claim": True})
    assert not d.route
    assert calls == []


def test_alias_combo_is_suspect(monkeypatch):
    # alias + frontier/consult phrase -> suspect fires (aux called)
    _cfg(monkeypatch, models={"astra": "openai/gpt-6-astra-pro-flex"})
    assert ic._intent_suspect("ask astra for a frontier read on this")
    assert ic._intent_suspect("consult astra about the error")
    assert not ic._intent_suspect("astra is a nice model")
    assert not ic._intent_suspect("the luna model got cheaper")


def test_staged_swap_via_aux_claim(monkeypatch):
    monkeypatch.setattr(ic, "_aux_transport", lambda p, t: _verdict(
        "higher", "pre", 0.9))
    _cfg(monkeypatch, models={"astra": "openai/gpt-6-astra-pro-flex",
                              "luna": "openai/gpt-5.6-luna-pro"})
    text = "second opinion from luna on the design"
    state.advance_turn_identity(SID, state.hash_text(text))
    d = route_gate.claim_pass(text, SID, "flash-model",
                              request={"messages": []}, context={})
    assert d.route and d.model_override == (
        "luna", "openai/gpt-5.6-luna-pro")
    rec = router_core.peek_pending_swap(SID)
    assert rec is not None
    base = anchor_chain.load_anchor_chain().endpoint_for("primary")
    assert rec["endpoint"].model == "openai/gpt-5.6-luna-pro"
    assert rec["endpoint"].base_url == base.base_url
