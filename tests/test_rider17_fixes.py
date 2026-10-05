"""Rider 17 — fix-first pins (T1 re-run #10, /opt/data/tmp/t1r10-results-2026-10-05.md).

Pinned here:
  R16-1 (systemic) — fable-targeted anchored consults failed 5/5 in the
    08:12–09:33 battery window with route_skipped reason=anchored_call_failed
    finish_reason=none model= (live: analyst /tmp/uncensored-router-analyst.log
    lines 3242–3255; agent.log 08:12:23 404 "Model 'fable' not found" from the
    Nous inference API). ROOT CAUSE (code-side, provider verified good):
    anchor_chain._resolve_with_source ran the configured-PRIMARY id step
    BEFORE the lane-scoped provider-catalog step, so a bare alias-like
    primary ('nous://fable') resolved 'fable' to itself and the consult hit
    the provider with a nonexistent model id. Control probe outside battery
    windows (r17 evidence, /opt/data/tmp/r17-bc1-reprobe.json run family):
    model 'fable' -> HTTP 404; model 'anthropic/claude-fable-5.1' -> HTTP 200
    'pong'. The R16-4 bounded retry worker did NOT engage on these failures —
    the 404 is a fast synchronous failure (route_skipped fired 1s after
    anchor_route_fired), the worker covers slow/timeout failures only.

  FIXES pinned:
    (1) catalog step precedes the primary step in _resolve_with_source
        (alias table still precedes both — R10 precedence preserved);
    (2) stage_model_swap resolves decision.model_target / the endpoint model
        through the LANE-SCOPED catalog and upgrades the endpoint in-memory
        when a concrete provider id differs (config never written);
    (3) fail-open preserved: catalog unavailable -> primary used;
    (4) explicit overrides untouched (R7 user-only contract unaffected).

  R16-2c — analyst A2 prose fork acceptance closure rides fix (1)+(2): the
    decision-banner consult path is stage_model_swap, which now targets a
    REAL provider id, so the consult completes and the banner+row delivery
    machinery (R16-4 success path) engages instead of the fail-loud
    'anchored_call_failed' fragment.

  BC1 availability (rider item 3) — NOT code-side, no pin: live re-probe
    outside battery windows (sid api_1791211074_62178f74 analyst / 
    api_1791211289_24b054ff architect): analyst turn ran to completion
    normally (7 API calls, latencies 6.8–42.2s, ZERO router consults) — the
    >300s "timeout" is a long multi-tool turn exceeding the probe client's
    read timeout (workload latency), not a provider outage; architect
    re-probe delivered in 51s. Characterized, not fixed.

No frontier/live calls — catalog fetch mocked at provider_prices seam, NEVER
live. No deploy, no gateway bounces, no config writes.
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
from hermes_router import anchor_chain, router_core  # noqa: E402

SID = "s-rider17"

CATALOG_MODELS = [
    {"id": "anthropic/claude-fable-5.1"},
    {"id": "openai/gpt-5.6-luna-pro"},
    {"id": "z-ai/glm-5.3"},
]

LOGGED = []


def _cfg(monkeypatch, primary="nous://fable", models=None):
    """Rider-17 repro config: the analyst misconfig — bare alias-like primary."""
    ac = {"primary": primary, "overflow": "pass_through"}
    if models is not None:
        ac["models"] = models
    cfg = {"hermes_router": {"anchor_chain": ac}}
    monkeypatch.setattr(
        "hermes_cli.config.load_config", lambda: cfg, raising=False)


def _catalog(monkeypatch, models=CATALOG_MODELS, lane="frontier",
             host="inference-api.nousresearch.com"):
    entries = tuple((m["id"], host) for m in models)
    monkeypatch.setattr(
        "hermes_router.provider_prices.provider_catalog_entries",
        lambda l: entries if l == lane else (), raising=True)
    anchor_chain._CATALOG_IDS_MEM = {}  # bust the memo


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    router_core._test_reset()
    plugin.state.clear()
    anchor_chain._CATALOG_IDS_MEM = {}
    LOGGED.clear()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    yield
    router_core._test_reset()
    plugin.state.clear()
    anchor_chain._CATALOG_IDS_MEM = {}


# ---------------------------------------------------------------------------
# 1. resolution order: catalog beats a bare alias-like primary
# ---------------------------------------------------------------------------

def test_fable_resolves_via_catalog_not_bare_primary(monkeypatch):
    """THE R16-1 repro: primary is nous://fable; asking 'fable' must hit the
    lane catalog (anthropic/claude-fable-5.1), never the bare primary id the
    provider 404s."""
    _cfg(monkeypatch, primary="nous://fable", models=None)
    _catalog(monkeypatch)
    got = anchor_chain.resolve_model_alias("fable")
    assert got is not None
    _alias, model_id = got
    assert model_id == "anthropic/claude-fable-5.1", got


def test_alias_table_still_precedes_catalog(monkeypatch):
    _cfg(monkeypatch, primary="nous://fable",
         models={"luna": "openai/gpt-5.6-luna-pro"})
    _catalog(monkeypatch)
    got = anchor_chain.resolve_model_alias("luna")
    assert got == ("luna", "openai/gpt-5.6-luna-pro")


def test_primary_failopen_when_catalog_unavailable(monkeypatch):
    """Fail-open: catalog fetch unavailable (empty seam) -> resolution
    returns None and the STAGING layer keeps the configured primary
    (pinned separately below) — no exception, no override."""
    _cfg(monkeypatch, primary="nous://z-ai/glm-5.3", models=None)
    monkeypatch.setattr(
        "hermes_router.provider_prices.provider_catalog_entries",
        lambda l: (), raising=True)
    anchor_chain._CATALOG_IDS_MEM = {}
    got = anchor_chain.resolve_model_alias("fable")
    assert got is None  # no catalog match, no alias -> no override


# ---------------------------------------------------------------------------
# 2. stage_model_swap targets a REAL provider id (lane-scoped)
# ---------------------------------------------------------------------------

def _consult_decision(model_target="fable", lane=None):
    from hermes_router.router_core import LANE_COMPLEXITY, MODE_CONSULT
    return router_core.RouteDecision(
        task_id="t-r17-consult", lane=lane if lane is not None else LANE_COMPLEXITY,
        mode=MODE_CONSULT, model_target=model_target,
        reason="manual_open_question_frontier",
        ts=0.0, override_used=None,
        route_id="r17test-000001", orientation=False)


def test_stage_model_swap_resolves_fable_target(monkeypatch):
    """R16-1 fix at the staging seam: a model_target=fable consult stages an
    endpoint whose model is the catalog-resolved concrete id."""
    _cfg(monkeypatch, primary="nous://fable", models=None)
    _catalog(monkeypatch)
    rec = router_core.stage_model_swap(SID, _consult_decision("fable"))
    assert rec is not None
    ep = rec["endpoint"]
    assert ep.model == "anthropic/claude-fable-5.1", ep.model
    # scheme/base/key unchanged — same provider chain, only the model swapped
    assert ep.scheme == "nous"


def test_stage_model_swap_resolves_bare_primary_endpoint(monkeypatch):
    """Declared consults stage model_target=None + the configured primary
    endpoint — a bare alias-like primary must be upgraded the same way."""
    _cfg(monkeypatch, primary="nous://fable", models=None)
    _catalog(monkeypatch)
    rec = router_core.stage_model_swap(SID, _consult_decision(None))
    assert rec is not None
    assert rec["endpoint"].model == "anthropic/claude-fable-5.1"


def test_stage_model_swap_failopen_primary(monkeypatch):
    """Catalog unavailable -> stage the configured primary unchanged
    (fail-open, never break staging)."""
    _cfg(monkeypatch, primary="nous://z-ai/glm-5.3", models=None)
    monkeypatch.setattr(
        "hermes_router.provider_prices.provider_catalog_entries",
        lambda l: (), raising=True)
    anchor_chain._CATALOG_IDS_MEM = {}
    rec = router_core.stage_model_swap(SID, _consult_decision("z-ai/glm-5.3"))
    assert rec is not None
    assert rec["endpoint"].model == "z-ai/glm-5.3"


def test_stage_model_swap_lane_scoped_uncensored(monkeypatch):
    """Uncensored-lane consults must NOT pick up the frontier (nous) catalog
    — resolution is lane-scoped; the uncensored catalog is a different seam."""
    from hermes_router.router_core import LANE_UNCENSORED
    _cfg(monkeypatch, primary="nous://z-ai/glm-5.3", models=None)
    _catalog(monkeypatch, lane="frontier")  # only the frontier seam mocked
    rec = router_core.stage_model_swap(
        SID, _consult_decision(None, LANE_UNCENSORED))
    assert rec is not None
    # the frontier catalog id must NOT ride an uncensored-lane consult
    assert rec["endpoint"].model == "z-ai/glm-5.3"


def test_stage_model_swap_explicit_override_untouched(monkeypatch):
    """R7 contract: an explicit (user) override is used verbatim — no
    catalog re-resolution on top of an explicit override."""
    _cfg(monkeypatch, primary="nous://fable", models=None)
    _catalog(monkeypatch)
    rec = router_core.stage_model_swap(
        SID, _consult_decision("fable"),
        model_override=("luna", "openai/gpt-5.6-luna-pro"),
        claim_source="declared_user")
    assert rec is not None
    assert rec["endpoint"].model == "openai/gpt-5.6-luna-pro"


# ---------------------------------------------------------------------------
# 3. failure-shape observability pin (r16 carried, model id now concrete)
# ---------------------------------------------------------------------------

def test_anchored_failure_skip_carries_resolved_model(monkeypatch):
    """On a provider failure the route_skipped event carries the endpoint's
    (now-resolved) model id — never the empty-model field t1r10 recorded
    ('model=' on every fable failure)."""
    from hermes_router.router_core import LANE_COMPLEXITY, MODE_CONSULT
    _cfg(monkeypatch, primary="nous://fable", models=None)
    _catalog(monkeypatch)
    plugin.state.clear()
    dec = _consult_decision("fable")
    assert router_core.stage_model_swap(SID, dec) is not None
    # the staged record's endpoint model is concrete before execution reads it
    rec = router_core.peek_pending_swap(SID)
    assert rec is not None
    assert str(rec["endpoint"].model) == "anthropic/claude-fable-5.1"
    assert LANE_COMPLEXITY and MODE_CONSULT  # import contract pin
