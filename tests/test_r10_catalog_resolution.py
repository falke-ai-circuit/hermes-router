"""R10 — provider-catalog model resolution (final fallback in
anchor_chain.resolve_model_alias).

Zero-maintenance: the provider's live catalog is the source of truth for
ANY model name, no config alias entry required. Tests mock the catalog
fetch (provider_prices._fetch) — NEVER live. Pinned semantics:
  - catalog match resolves 'fable 5.1' -> anthropic/claude-fable-5.1
  - alias table PRECEDES catalog ('astra' still resolves via alias)
  - >=4-char FP guard holds against catalog ids
  - fetch failure fail-opens to None (primary used)
  - agent-initiated asks never trigger catalog resolution (R7 pin,
    stage_model_swap claim-source drop)
  - version bump 4.2.0 consistent (plugin.yaml)
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
from hermes_router import anchor_chain, route_gate, router_core  # noqa: E402
from hermes_router import state as rt_state  # noqa: E402

SID = "s-r10-catalog"

CATALOG_MODELS = [
    {"id": "anthropic/claude-fable-5.1"},
    {"id": "openai/gpt-5.6-luna-pro"},
    {"id": "z-ai/glm-5.3"},
]

LOGGED = []


def _cfg(monkeypatch, models=None, primary="nous://z-ai/glm-5.3"):
    ac = {"primary": primary, "overflow": "pass_through"}
    if models is not None:
        ac["models"] = models
    cfg = {"hermes_router": {"anchor_chain": ac}}
    monkeypatch.setattr(
        "hermes_cli.config.load_config", lambda: cfg, raising=False)


def _catalog(monkeypatch, models=CATALOG_MODELS):
    """Override the SINGLE fetch seam (provider_prices.provider_catalog_ids,
    conftest-defaulted to empty) — no live net."""
    monkeypatch.setattr(
        "hermes_router.provider_prices.provider_catalog_ids",
        lambda: tuple(m["id"] for m in models), raising=True)
    anchor_chain._CATALOG_IDS_MEM = (0.0, ())  # bust the memo


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    router_core._test_reset()
    plugin.state.clear()
    rt_state.reset_turn_identity(SID)
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
    anchor_chain._CATALOG_IDS_MEM = (0.0, ())


# ---------------------------------------------------------------------------
# 1. catalog match resolves fable-5.1 (mocked fetch, never live)
# ---------------------------------------------------------------------------

def test_catalog_match_resolves_fable(monkeypatch):
    _cfg(monkeypatch, models=None)  # NO alias table at all
    _catalog(monkeypatch)
    got = anchor_chain.resolve_model_alias("fable 5.1")
    assert got is not None
    alias, model_id = got
    assert model_id == "anthropic/claude-fable-5.1"
    assert alias == "anthropic/claude-fable-5.1"


def test_catalog_match_hyphen_tolerance(monkeypatch):
    _cfg(monkeypatch, models=None)
    _catalog(monkeypatch)
    got = anchor_chain.resolve_model_alias("claude-fable-5.1")
    assert got == ("anthropic/claude-fable-5.1", "anthropic/claude-fable-5.1")


# ---------------------------------------------------------------------------
# 2. alias precedence over catalog ('astra' resolves via alias FIRST)
# ---------------------------------------------------------------------------

def test_alias_precedes_catalog(monkeypatch):
    _cfg(monkeypatch, models={"astra": "openai/gpt-6-astra-pro"})
    _catalog(monkeypatch)
    got = anchor_chain.resolve_model_alias("astra")
    assert got == ("astra", "openai/gpt-6-astra-pro")


def test_alias_substring_precedes_catalog(monkeypatch):
    _cfg(monkeypatch, models={"astra": "openai/gpt-6-astra-pro-flex"})
    _catalog(monkeypatch)
    got = anchor_chain.resolve_model_alias("astra pro flex")
    assert got == ("astra", "openai/gpt-6-astra-pro-flex")


# ---------------------------------------------------------------------------
# 3. FP guard >=4 chars holds against catalog ids
# ---------------------------------------------------------------------------

def test_fp_guard_short_fragment_no_catalog_match(monkeypatch):
    _cfg(monkeypatch, models=None)
    _catalog(monkeypatch)
    assert anchor_chain.resolve_model_alias("gpt") is None
    assert anchor_chain.resolve_model_alias("glm") is None
    assert anchor_chain.resolve_model_alias("ai") is None


def test_fp_guard_no_match_none(monkeypatch):
    _cfg(monkeypatch, models=None)
    _catalog(monkeypatch)
    assert anchor_chain.resolve_model_alias("totally-unknown-model") is None


# ---------------------------------------------------------------------------
# 4. fetch failure fail-open -> None (primary used)
# ---------------------------------------------------------------------------

def test_fetch_failure_fail_open(monkeypatch):
    _cfg(monkeypatch, models=None)
    monkeypatch.setattr(
        "hermes_router.provider_prices.provider_catalog_ids",
        lambda: (), raising=True)
    anchor_chain._CATALOG_IDS_MEM = (0.0, ())
    assert anchor_chain.resolve_model_alias("fable 5.1") is None


def test_fetch_exception_fail_open(monkeypatch):
    _cfg(monkeypatch, models=None)
    def _boom(*a, **k):
        raise RuntimeError("net down")
    monkeypatch.setattr(
        "hermes_router.provider_prices.provider_catalog_ids", _boom,
        raising=True)
    anchor_chain._CATALOG_IDS_MEM = (0.0, ())
    assert anchor_chain.resolve_model_alias("fable 5.1") is None


# ---------------------------------------------------------------------------
# 5. R7 pin: agent-initiated ask NEVER triggers catalog resolution past
#    primary — stage_model_swap drops model_override when claim_source
#    is not declared_user (catalog-sourced or alias-sourced alike).
# ---------------------------------------------------------------------------

def test_agent_initiated_override_dropped(monkeypatch):
    _cfg(monkeypatch, models=None)
    _catalog(monkeypatch)
    chain = anchor_chain.load_anchor_chain()
    ep = chain.endpoint_for("primary")
    assert ep is not None
    from hermes_router.router_core import RouteDecision
    dec = RouteDecision(task_id="t-agent", lane="higher_pre",
                        mode="consult", model_target=None,
                        reason="declared_agent")
    # agent-initiated claim + a catalog-resolved override -> DROPPED
    rec = router_core.stage_model_swap(
        SID, dec, model_override=("anthropic/claude-fable-5.1",
                                  "anthropic/claude-fable-5.1"),
        claim_source="declared_agent")
    assert rec is not None
    assert rec["endpoint"].model != "anthropic/claude-fable-5.1"
    # and the rejection is logged content-free
    assert any(f.get("event_detail") == "model_override_rejected"
               for _, f in LOGGED)


def test_user_declared_override_applies_catalog_model(monkeypatch):
    _cfg(monkeypatch, models=None)
    _catalog(monkeypatch)
    chain = anchor_chain.load_anchor_chain()
    ep = chain.endpoint_for("primary")
    assert ep is not None
    from hermes_router.router_core import RouteDecision
    dec = RouteDecision(task_id="t-user", lane="higher_pre",
                        mode="consult", model_target=None,
                        reason="declared_user")
    rec = router_core.stage_model_swap(
        SID, dec, model_override=("anthropic/claude-fable-5.1",
                                  "anthropic/claude-fable-5.1"),
        claim_source="declared_user")
    assert rec is not None
    assert rec["endpoint"].model == "anthropic/claude-fable-5.1"


# ---------------------------------------------------------------------------
# 6. R6 event carries source=catalog (content-free, consistent with R6)
# ---------------------------------------------------------------------------

def test_detect_override_reports_catalog_source(monkeypatch):
    _cfg(monkeypatch, models=None)
    _catalog(monkeypatch)
    got = route_gate.detect_model_override("consult frontier using fable 5.1")
    assert got is not None
    lane, alias, model_id, source = got
    assert lane == route_gate.LANE_HIGHER_PRE
    assert model_id == "anthropic/claude-fable-5.1"
    assert source == "catalog"


def test_declared_decision_logs_source_catalog(monkeypatch):
    _cfg(monkeypatch, models=None)
    _catalog(monkeypatch)
    dec = route_gate._declared_decision(
        "consult frontier using fable 5.1", SID)
    assert dec is not None and dec.route and dec.model_override is not None
    assert dec.model_override[1] == "anthropic/claude-fable-5.1"
    src = [f for f in LOGGED
           if f[1].get("event_detail") == "model_override_detected"]
    assert src and src[0][1].get("source") == "catalog"


def test_alias_detection_reports_alias_source(monkeypatch):
    _cfg(monkeypatch, models={"astra": "openai/gpt-6-astra-pro"})
    _catalog(monkeypatch)
    got = route_gate.detect_model_override("consult frontier using astra")
    assert got is not None and got[3] == "alias"


# ---------------------------------------------------------------------------
# 7. version bump consistent
# ---------------------------------------------------------------------------

def test_version_bumped():
    import yaml
    with open(os.path.join(PLUGIN_DIR, "plugin.yaml")) as fh:
        manifest = yaml.safe_load(fh)
    assert manifest["version"] == "4.2.0"
