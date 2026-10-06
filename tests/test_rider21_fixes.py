# tests/test_rider21_fixes.py — RIDER 21 FABLE-PIN pins (v4.22.0).
#
# User directive (Goran 2026-10-06, verbatim intent): "hermes router should
# use frontier defined in config, not deviate. Do not use fable unless
# specified in the frontier request to use fable or custom model."
#
# Disaster class: fable 5.1 burned money on the nous portal (live: analyst
# 2026-10-06 13:50-13:59Z — risk_r2 + manual_open_question_frontier consults
# resolved model_target=fable -> anthropic/claude-fable-5.1 and billed,
# while the profile config's frontier primary was nous://z-ai/glm-5.3).
#
# Pins:
#   1. config-pinned consult — the staged endpoint model is the CONFIG
#      anchor_chain.primary model id, verbatim; NO fable string anywhere in
#      the staged request/model.
#   2. explicit-fable pass-through — a declared_user named-model override
#      naming fable still stages (fable ONLY on explicit per-request spec).
#   3. implicit fable target fail-closed — any other path to a fable id
#      (config primary pinned to fable, catalog upgrade) refuses to stage:
#      stage_model_swap returns None, fable_target_not_explicit logged.
#   4. config-empty fail-closed — no anchor_chain.primary entry -> consults
#      do not fire (route-level standdown consult_no_frontier_config).
#   5. zero hardcoded fable primary in code — the chain example/comment
#      rows no longer carry an openrouter://anthropic/claude-fable-5.1
#      DEFAULT (grep guard over non-test sources).
#
# No frontier/live calls — config loading monkeypatched, catalog fetch
# mocked at provider_prices seam, NEVER live. No deploy, no bounces, no
# config writes.
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

SID = "s-rider21"

LOGGED = []


def _cfg(monkeypatch, primary="nous://z-ai/glm-5.3", models=None, absent=False):
    """Config seam. primary=None/absent=True -> anchor_chain block with NO
    frontier entry (fail-closed repro)."""
    if absent:
        cfg = {"hermes_router": {"enabled": True}}
    else:
        ac = {"overflow": "pass_through"}
        if primary is not None:
            ac["primary"] = primary
        if models is not None:
            ac["models"] = models
        cfg = {"hermes_router": {"anchor_chain": ac}}
    monkeypatch.setattr(
        "hermes_cli.config.load_config", lambda: cfg, raising=False)


def _catalog(monkeypatch, models=(("anthropic/claude-fable-5.1",
                                  "inference-api.nousresearch.com"),)):
    entries = tuple(models)
    monkeypatch.setattr(
        "hermes_router.provider_prices.provider_catalog_entries",
        lambda l: entries if l == "frontier" else (), raising=True)
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


def _consult_decision(model_target, reason="risk_r2"):
    return router_core.RouteDecision(
        task_id="t21", lane=router_core.LANE_COMPLEXITY,
        mode=router_core.MODE_CONSULT, model_target=model_target,
        reason=reason, ts=1.0,
        route_id="t21-rider21")


# ---------------------------------------------------------------------------
# 1. config-pinned consult: staged endpoint = config primary, no fable
# ---------------------------------------------------------------------------

def test_config_pinned_consult_uses_frontier_primary(monkeypatch):
    """The config frontier primary (z-ai/glm-5.3) is the consult target,
    verbatim — no fable string in the staged endpoint."""
    _cfg(monkeypatch, primary="nous://z-ai/glm-5.3")
    _catalog(monkeypatch)  # catalog WOULD resolve fable — must not be used
    rec = router_core.stage_model_swap(
        SID, _consult_decision("z-ai/glm-5.3"))
    assert rec is not None
    ep = rec["endpoint"]
    assert ep.model == "z-ai/glm-5.3", ep.model
    assert "fable" not in json_of(ep)


def json_of(ep):
    import json as _json
    return _json.dumps(ep.masked()).lower()


# ---------------------------------------------------------------------------
# 2. explicit fable pass-through (declared_user named-model override)
# ---------------------------------------------------------------------------

def test_explicit_fable_pass_through(monkeypatch):
    """User's own request names fable (declared_user override) -> fable
    stages exactly as asked."""
    _cfg(monkeypatch, primary="nous://z-ai/glm-5.3")
    rec = router_core.stage_model_swap(
        SID, _consult_decision("z-ai/glm-5.3"),
        model_override=("fable", "anthropic/claude-fable-5.1"),
        claim_source="declared_user")
    assert rec is not None
    assert rec["endpoint"].model == "anthropic/claude-fable-5.1", \
        rec["endpoint"].model


# ---------------------------------------------------------------------------
# 3. implicit fable target fail-closed
# ---------------------------------------------------------------------------

def test_fable_primary_config_refused(monkeypatch):
    """Config primary pinned to fable and NO explicit user naming -> the
    staging seam fail-closes (None + fable_target_not_explicit) — fable
    never fires on config alone."""
    _cfg(monkeypatch, primary="nous://fable")
    rec = router_core.stage_model_swap(
        SID, _consult_decision("fable"))
    assert rec is None, rec
    assert any(f.get("event_detail") == "fable_target_not_explicit"
               for _e, f in LOGGED), LOGGED


def test_fable_catalog_upgrade_refused(monkeypatch):
    """A catalog/alias resolution that would upgrade the target to a fable
    id is refused — the config frontier primary is never deviated from."""
    _cfg(monkeypatch, primary="nous://z-ai/glm-5.3")
    _catalog(monkeypatch)  # 'fable 5.1' would resolve to the fable id
    rec = router_core.stage_model_swap(
        SID, _consult_decision("fable 5.1"))
    assert rec is None, rec
    assert any(f.get("event_detail") == "fable_target_not_explicit"
               for _e, f in LOGGED), LOGGED


def test_nonuser_fable_override_rejected(monkeypatch):
    """An override naming fable that does NOT carry the user claim is
    dropped (R7 user-only rule) -> fable target -> fail-closed None."""
    _cfg(monkeypatch, primary="nous://fable")
    rec = router_core.stage_model_swap(
        SID, _consult_decision("fable"),
        model_override=("fable", "anthropic/claude-fable-5.1"),
        claim_source="auto")
    assert rec is None, rec


# ---------------------------------------------------------------------------
# 4. config-empty fail-closed: consult does not fire
# ---------------------------------------------------------------------------

def test_no_frontier_entry_consult_does_not_fire(monkeypatch):
    _cfg(monkeypatch, absent=True)
    assert router_core._consult_no_frontier_config() is True


def test_frontier_entry_present_gate_open(monkeypatch):
    _cfg(monkeypatch, primary="nous://z-ai/glm-5.3")
    assert router_core._consult_no_frontier_config() is False


# ---------------------------------------------------------------------------
# 5. zero hardcoded fable default in code
# ---------------------------------------------------------------------------

def test_no_hardcoded_fable_primary_in_code():
    """Rider 21 item 1: no non-test source line may pin a fable primary as
    a DEFAULT/example — the only fable mentions left in sources are the
    fail-closed guard + historical root-cause comments."""
    bad = []
    for name in sorted(os.listdir(PLUGIN_DIR)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(PLUGIN_DIR, name)
        with open(path, "r", encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                low = line.lower()
                if "fable" not in low:
                    continue
                # allowed: guard token, historical notes — a DEFAULT/example
                # assignment of a fable PRIMARY is the violation
                if ("primary:" in low and "fable" in low) or \
                        "openrouter://anthropic/claude-fable" in low and \
                        "primary" in low:
                    bad.append(f"{name}:{i}: {line.strip()}")
    assert not bad, bad
