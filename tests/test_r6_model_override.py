"""R6 leg 1 — on-demand frontier consult with named model override.

Alias table (anchor_chain.anchor_models), phrase/naming detection
(route_gate.detect_model_override), and wiring: declared claim carries
model_override through stage_model_swap; the swap endpoint swaps ONLY the
model id (same scheme/base/key); primary config untouched; caps/pricing
follow the resolved model id.
"""
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import anchor_chain, route_gate  # noqa: E402
from hermes_router import router_core, state  # noqa: E402

SID = "s-r6-leg1"
MODELS_BLOCK = {
    "glm": "z-ai/glm-5.3",
    "luna": "openai/gpt-5.6-luna-pro",
    "astra": "openai/gpt-6-astra-pro-flex",
    "astra-pro": "openai/gpt-6-astra-pro",
}

LOGGED = []


def _cfg(monkeypatch, models=None, primary="nous://z-ai/glm-5.3"):
    ac = {"primary": primary, "overflow": "pass_through"}
    if models is not None:
        ac["models"] = models
    cfg = {"hermes_router": {"anchor_chain": ac}}
    monkeypatch.setattr(
        "hermes_cli.config.load_config", lambda: cfg, raising=False)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
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


# ---------------------------------------------------------------------------
# Alias table load
# ---------------------------------------------------------------------------


def test_anchor_models_present(monkeypatch):
    _cfg(monkeypatch, models=MODELS_BLOCK)
    assert anchor_chain.anchor_models() == MODELS_BLOCK


def test_anchor_models_missing_block(monkeypatch):
    _cfg(monkeypatch, models=None)
    assert anchor_chain.anchor_models() == {}


def test_anchor_models_malformed(monkeypatch):
    _cfg(monkeypatch, models="not-a-dict")
    assert anchor_chain.anchor_models() == {}


def test_resolve_alias_exact_and_normalized(monkeypatch):
    _cfg(monkeypatch, models=MODELS_BLOCK, primary="nous://z-ai/glm-5.3")
    assert anchor_chain.resolve_model_alias("astra") == \
        ("astra", "openai/gpt-6-astra-pro-flex")
    # hyphen/space/case tolerant
    assert anchor_chain.resolve_model_alias("Astra-Pro") == \
        ("astra-pro", "openai/gpt-6-astra-pro")
    # spoken long form: alias is a prefix
    assert anchor_chain.resolve_model_alias("astra 6 pro flex") == \
        ("astra", "openai/gpt-6-astra-pro-flex")


def test_resolve_alias_primary_model_id(monkeypatch):
    _cfg(monkeypatch, models=MODELS_BLOCK, primary="nous://z-ai/glm-5.3")
    got = anchor_chain.resolve_model_alias("z-ai/glm-5.3")
    assert got is not None and got[1] == "z-ai/glm-5.3"


def test_resolve_unknown_name_is_none(monkeypatch):
    _cfg(monkeypatch, models=MODELS_BLOCK)
    assert anchor_chain.resolve_model_alias("gpt-4-turbo-x") is None
    assert anchor_chain.resolve_model_alias("") is None


# ---------------------------------------------------------------------------
# Phrase/naming detection
# ---------------------------------------------------------------------------


def test_detect_override_phrases(monkeypatch):
    _cfg(monkeypatch, models=MODELS_BLOCK)
    for line in ("consult frontier using astra 6 pro flex",
                 "Consult frontier with ASTRA",
                 "frontier via astra-pro",
                 "anchor this with astra",
                 "ask luna about the error budget",
                 "astra second opinion"):
        got = route_gate.detect_model_override(line)
        assert got is not None, line
        lane, alias, model_id, _source = got
        assert lane == route_gate.LANE_HIGHER_PRE
        assert model_id == MODELS_BLOCK[alias]


def test_detect_override_inert_on_quotes_and_prose(monkeypatch):
    _cfg(monkeypatch, models=MODELS_BLOCK)
    # quoted line
    assert route_gate.detect_model_override(
        '> consult frontier using astra') is None
    # mid-sentence prose
    assert route_gate.detect_model_override(
        "yesterday we discussed whether consult frontier using astra is wise"
    ) is None
    # unknown model name -> no detection
    assert route_gate.detect_model_override(
        "consult frontier using gpt-4-turbo-x") is None


# ---------------------------------------------------------------------------
# Wiring: claim carries override; endpoint model swapped; config untouched
# ---------------------------------------------------------------------------


def _claim(monkeypatch, text):
    state.advance_turn_identity(SID, state.hash_text(text))
    return route_gate.claim_pass(text, SID, "flash-model",
                                 request={"messages": []}, context={})


def test_declared_claim_carries_override(monkeypatch):
    _cfg(monkeypatch, models=MODELS_BLOCK)
    text = "consult frontier using astra 6 pro flex"
    d = _claim(monkeypatch, text)
    assert d.route and d.lane == route_gate.LANE_HIGHER_PRE
    assert d.model_override == ("astra", "openai/gpt-6-astra-pro-flex")
    ev = next(f for _, f in LOGGED
              if f.get("event_detail") == "model_override_detected")
    assert ev["alias"] == "astra"
    assert ev["model"] == "openai/gpt-6-astra-pro-flex"
    assert ev["lane"] == route_gate.LANE_HIGHER_PRE


def test_staged_swap_endpoint_model_swapped(monkeypatch):
    _cfg(monkeypatch, models=MODELS_BLOCK)
    d = _claim(monkeypatch, "consult frontier using luna")
    assert d.route
    rec = router_core.peek_pending_swap(SID)
    assert rec is not None
    ep = rec["endpoint"]
    assert ep.model == "openai/gpt-5.6-luna-pro"
    # same scheme/base/key as the configured primary
    base = anchor_chain.load_anchor_chain().endpoint_for("primary")
    assert ep.scheme == base.scheme
    assert ep.base_url == base.base_url
    assert ep.api_key_env == base.api_key_env
    # config untouched
    assert anchor_chain.load_anchor_chain().primary.model == "z-ai/glm-5.3"


def test_no_alias_still_routes_with_primary(monkeypatch):
    _cfg(monkeypatch, models=MODELS_BLOCK)
    d = _claim(monkeypatch, "anchor this: what is one blind spot")
    assert d.route
    assert d.model_override is None
    assert "model_override_detected" not in [
        f.get("event_detail") for _, f in LOGGED]
    rec = router_core.peek_pending_swap(SID)
    assert rec is not None
    assert rec["endpoint"].model == "z-ai/glm-5.3"


def test_unknown_model_no_override(monkeypatch):
    _cfg(monkeypatch, models=MODELS_BLOCK)
    d = _claim(monkeypatch, "consult frontier using gpt-4-turbo-x")
    # no override anywhere — behaves exactly as today (inert; the phrase is
    # not a strict variant and the name resolves to nothing)
    assert d.model_override is None
    assert "model_override_detected" not in [
        f.get("event_detail") for _, f in LOGGED]


def test_cap_still_enforced_on_override_consult(monkeypatch, tmp_path):
    _cfg(monkeypatch, models=MODELS_BLOCK)
    anchor_chain._test_reset(str(tmp_path / "spend.json"))
    anchor_chain.record_spend(1.99)  # cap default $2
    try:
        d = _claim(monkeypatch, "consult frontier using astra")
        assert d.route
        rec = router_core.peek_pending_swap(SID)
        assert rec is not None
        assert rec["endpoint"].model == "openai/gpt-6-astra-pro-flex"
        # cap check at the execution seam uses the OVERRIDE model id
        api_kwargs = {"model": "flash", "max_tokens": 9000,
                      "messages": [{"role": "user", "content": "hi"}]}
        from hermes_router import anchor_exec
        out = anchor_exec.maybe_execute_anchored(SID, dict(api_kwargs))
        assert isinstance(out, tuple) and out[0] == "cap_blocked"
    finally:
        anchor_chain._test_reset()


def test_pricing_lookup_by_resolved_model_id(monkeypatch):
    ac = {"primary": "nous://z-ai/glm-5.3",
          "models": MODELS_BLOCK,
          "pricing": {"openai/gpt-5.6-luna-pro":
                      {"input_per_1m": 2.0, "output_per_1m": 8.0}}}
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"hermes_router": {"anchor_chain": ac}}, raising=False)
    chain = anchor_chain.load_anchor_chain()
    ep = anchor_chain.override_endpoint(
        chain.endpoint_for("primary"), "openai/gpt-5.6-luna-pro")
    cost = anchor_chain.estimate_call_cost(ep, 1_000_000, 1_000_000,
                                           chain.pricing)
    assert cost == pytest.approx(10.0, abs=1e-6)
