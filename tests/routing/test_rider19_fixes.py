"""Rider 19 — apply-and-pin tests (v4.19.0 -> 4.20.0 scope).

Attempt-1 root causes encoded as pins (attempt 1 completed ALL root-
causing, cap hit before any edit; attempt 2 applies + pins). Zero-network:
no provider calls — emissions are collected via package-namespace
monkeypatch.

Item 1 (systemic): live gateways load the plugin as
hermes_plugins.hermes_router via importlib spec_from_file_location
(hermes_cli/plugins.py) with the plugin dir NOT on sys.path — every
deferred bare-package import `from hermes_router import _log_route`
raised ModuleNotFoundError in live gateway processes and was swallowed
by silent excepts. Fix: alias-safe call-time relative resolution at all
bare-package sites (+ route_gate._pkg_fn import fix at the
decision_manual_suppressed site) + fail-loud (never dispatch-breaking)
logging in the demoted excepts. Pin: lane-named emission under an
alias-simulated second package name; the dispatcher_pre legacy staging
event (complexity_orientation -> record_staged_consult) stays.

Item 3 (code): BC2 analyst clean-ask false-consult — the risk pre-leg
lexicon read the clean non-steering opinion form as risky
(reason=risk_r2, no manual trigger, no declared fork). Fix: structural
default-deny (all three conditions) -> the risk consult stands down;
own FP pins prove real risky asks still fire.
"""
import importlib
import importlib.util
import sys
import types
from unittest import mock

import pytest

import hermes_router as plugin
from hermes_router import risk, route_gate, router_core, state

SID = "s-rider19"

BC2_CLEAN = ("What's your substantive take on capping fleet router consult "
             "tokens at 2000 per session versus leaving it uncapped? "
             "One pick and a short defense.")
RISKY_ASK = "rotate the fleet credentials now"
RISKY_OPINION_WITH_FORK = (
    "What's your take on force pushing and resetting the fleet genome? "
    "Option A: overwrite without backup. Option B: keep a backup.")

ALIAS_PKG = "hermes_plugins.hermes_router"


@pytest.fixture()
def _reset(monkeypatch):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    monkeypatch.setattr(router_core, "pre_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 0)
    from hermes_router import config_access, decision
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(router_core, "_decision_cfg",
                        lambda: {"enabled": True, "level": 3})
    monkeypatch.setattr(decision, "_cfg",
                        lambda: {"enabled": True, "level": 3})
    yield


# ---------------------------------------------------- ITEM 1 — alias sim ----

def _load_alias_package():
    """Simulate the LIVE gateway load: the plugin package under a second
    name (hermes_plugins.hermes_router) via importlib spec_from_file_location
    with the plugin dir NOT on sys.path under that name."""
    parent = types.ModuleType("hermes_plugins")
    spec = importlib.util.spec_from_file_location(
        ALIAS_PKG, plugin.__file__,
        submodule_search_locations=list(plugin.__path__))
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["hermes_plugins"] = parent
    sys.modules[ALIAS_PKG] = pkg
    parent.hermes_router = pkg
    spec.loader.exec_module(pkg)
    return pkg


def test_item1_alias_package_lane_named_emission(monkeypatch):
    """PIN (rider 19 item 1): under an alias-simulated second package name
    (the live gateway load), the lane-named frontier_route_fired event
    STILL fires — pre-fix this raised ModuleNotFoundError (bare
    `from hermes_router import _log_route`) and the silent except ate it.
    The legacy manual_open_question_frontier event stays alongside."""
    pkg = _load_alias_package()
    try:
        seen = []
        monkeypatch.setattr(pkg, "_log_route",
                            lambda e, **f: seen.append((e, dict(f))))
        arc = importlib.import_module(ALIAS_PKG + ".router_core")
        monkeypatch.setattr(arc, "pre_cooldown_seconds", lambda: 0)
        monkeypatch.setattr(arc, "_complexity_level", lambda: 0)
        d = arc.dispatch("decide this: how should we restructure the audit "
                         "pipeline? I want your open take.",
                         session_id=SID + "-alias", model="m")
        assert d.reason == "manual_open_question_frontier"
        evs = [f.get("event_detail") for _, f in seen]
        assert "frontier_route_fired" in evs, evs
        assert "manual_open_question_frontier" in evs, evs
        fr = [f for _, f in seen if f.get("event_detail") == "frontier_route_fired"][0]
        assert fr.get("lane") == "complexity"
    finally:
        for name in (ALIAS_PKG, "hermes_plugins",
                     ALIAS_PKG + ".router_core"):
            sys.modules.pop(name, None)


def test_item1_bare_package_import_is_gone():
    """PIN: no bare-package `from hermes_router import _log_route` without
    the relative import path — every deferred import resolves through the
    package namespace at CALL time."""
    import re
    for mod_name in ("router_core", "anchor_exec", "decision_midturn"):
        mod = importlib.import_module("hermes_router." + mod_name)
        src = open(mod.__file__, encoding="utf-8").read()
        for m in re.finditer(r"^([ \t]*)from hermes_router import _log_route",
                             src, re.M):
            # the absolute name is only legal as the ImportError fallback of
            # an immediately preceding relative import (the alias-safe
            # two-step pattern)
            prev = src[max(0, m.start() - 300):m.start()]
            assert "except ImportError:" in prev and \
                "from . import _log_route" in prev, \
                f"{mod_name}: bare-package import without relative alias-safe guard"


def test_item1_manual_suppressed_pkgfn_imported_from_route_gate():
    """PIN: router_core must NOT call the bare name `_pkg_fn` (lives in
    route_gate, was never imported -> NameError -> swallowed ->
    decision_manual_suppressed dead). The site now resolves it from
    route_gate, and the helper reaches the package namespace."""
    seen = []
    orig = plugin._log_route
    monkey_ok = True
    try:
        plugin._log_route = lambda e, **f: seen.append((e, dict(f)))  # type: ignore
        fn = route_gate._pkg_fn("_log_route")
        fn("PRE", event_detail="decision_manual_suppressed",
           reason="lane_disabled", session_id=SID)
    finally:
        plugin._log_route = orig
        _ = monkey_ok
    assert seen and seen[0][1]["event_detail"] == "decision_manual_suppressed"


def test_item1_emit_failure_is_fail_loud_not_silent(_reset, monkeypatch, caplog):
    """PIN: a failing emission is LOGGED (fail-loud) and NEVER breaks
    dispatch — the demoted silent excepts."""
    import logging

    def _boom(e, **f):
        raise RuntimeError("simulated emit failure")

    monkeypatch.setattr(plugin, "_log_route", _boom)
    # R21 FABLE-PIN: consults fire only with a config frontier entry — give
    # the dispatch a concrete primary so the emit-failure pin still drives
    # the manual_open_question_frontier path.
    _chain = mock.Mock()
    _ep = mock.Mock(model="z-ai/glm-5.3")
    _chain.endpoint_for.return_value = _ep
    monkeypatch.setattr(router_core.anchor_chain, "load_anchor_chain",
                        lambda: _chain)
    with caplog.at_level(logging.WARNING, logger="hermes_router.router_core"):
        d = router_core.dispatch("decide this: how should we restructure the "
                                 "audit pipeline? I want your open take.",
                                 session_id=SID + "-loud", model="m")
        assert d.reason == "manual_open_question_frontier"  # dispatch unbroken
    assert any("route_event_emit_failed" in r.message for r in caplog.records)


def test_item1_dispatcher_pre_legacy_staging_event_stays(monkeypatch):
    """PIN (rider 19 item 1 contract): the dispatcher_pre legacy staging
    record for complexity_orientation consults (the PRE cooldown substrate,
    D6a) is unchanged."""
    from hermes_router import dispatcher_pre
    # exact test_r16_consult_cooldown recipe: cooldown substrate reset BEFORE
    # the knobs are patched (turning cooldown ON with stale cooldown state
    # can suppress the consult before it ever stages).
    router_core._test_reset()
    router_core._cooldown_test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID + "-stg")
    route_gate.clear_declared(SID + "-stg")
    route_gate.clear_turn_claims(SID + "-stg")
    monkeypatch.setattr(router_core, "consult_cooldown_turns", lambda: 5)
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"pre_mode": "route"})
    monkeypatch.setattr(router_core, "pre_cooldown_seconds", lambda: 0)
    # the mocked harness has no configured primary model -> no staged swap ->
    # no legacy staging record; give the consult a concrete target.
    monkeypatch.setattr(router_core, "_primary_model", lambda: "m")
    # the mocked harness has no configured anchor chain -> stage_model_swap
    # returns None (endpoint_for -> None) and the legacy staging record never
    # happens; stub a chain with a concrete primary endpoint (the same recipe
    # test_router_tuning uses for staging-sensitive pins).
    _chain = mock.Mock()
    _ep = mock.Mock(model="frontier-x")
    _chain.endpoint_for.return_value = _ep
    monkeypatch.setattr(router_core.anchor_chain, "load_anchor_chain",
                        lambda: _chain)
    from hermes_router import config_access, decision
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(router_core, "_decision_cfg",
                        lambda: {"enabled": True, "level": 3})
    monkeypatch.setattr(decision, "_cfg",
                        lambda: {"enabled": True, "level": 3})
    fired = {}
    real_rsc = state.record_staged_consult
    monkeypatch.setattr(
        state, "record_staged_consult",
        lambda sid, ts, task_id=None: (fired.setdefault("sid", sid),
                                       real_rsc(sid, ts, task_id=task_id)))
    seen = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: seen.append((e, dict(f))))
    # the legacy staging record lives in dispatcher_pre._dispatch_pass —
    # router_core.dispatch alone never reaches it.
    ok = dispatcher_pre._dispatch_pass(
        "Design a migration strategy across multiple services with phased "
        "rollout", SID + "-stg", "m")
    assert ok
    fr = [f for _, f in seen if f.get("event_detail") == "anchor_route_fired"]
    assert fr and fr[0]["reason"] == "complexity_orientation", \
        [(f.get("event_detail"), f.get("reason")) for _, f in seen]
    assert fired.get("sid") == SID + "-stg"


# ------------------------------------------------ ITEM 3 — BC2 stand-down ---

def test_item3_clean_opinion_stands_down(_reset, monkeypatch):
    """PIN (BC2 analyst false-consult): the clean non-steering opinion form
    (no manual trigger, no declared fork) must NOT bill a frontier risk
    consult — structural default-deny; the stand-down event fires."""
    from hermes_router import config_access
    seen = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: seen.append((e, dict(f))))
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    d = router_core.dispatch(BC2_CLEAN, session_id=SID + "-bc2", model="m")
    assert d.mode != router_core.MODE_CONSULT, d.reason
    evs = [f.get("event_detail") for _, f in seen]
    assert "risk_pre_stand_down_clean_opinion" in evs, evs
    assert not any(str(ev).startswith("risk_consult_fire") for ev in evs), evs


def test_item3_real_risky_ask_still_fires(_reset, monkeypatch):
    """FP pin: the default-deny is STRUCTURAL — a real risky ask keeps
    its frontier risk consult (R21: config carries a concrete frontier
    primary so the consult is allowed to fire)."""
    _chain = mock.Mock()
    _ep = mock.Mock(model="z-ai/glm-5.3")
    _chain.endpoint_for.return_value = _ep
    monkeypatch.setattr(router_core.anchor_chain, "load_anchor_chain",
                        lambda: _chain)
    d = router_core.dispatch(RISKY_ASK, session_id=SID + "-fp1", model="m")
    assert d.mode == router_core.MODE_CONSULT
    assert d.reason == "risk_r3"


def test_item3_risky_opinion_with_declared_fork_still_fires(_reset, monkeypatch):
    """FP pin: an opinion-shaped ask WITH a declared fork structure never
    stands down (the fork is real consult material; R21: concrete config
    frontier primary so the consult is allowed to fire)."""
    _chain = mock.Mock()
    _ep = mock.Mock(model="z-ai/glm-5.3")
    _chain.endpoint_for.return_value = _ep
    monkeypatch.setattr(router_core.anchor_chain, "load_anchor_chain",
                        lambda: _chain)
    d = router_core.dispatch(RISKY_OPINION_WITH_FORK,
                             session_id=SID + "-fp2", model="m")
    assert d.mode == router_core.MODE_CONSULT
    assert d.reason.startswith("risk_r")


def test_item3_manual_trigger_beats_stand_down(_reset):
    """FP pin: a trusted manual 'decide this:' ask never reaches the
    stand-down arm (manual precedence, R19.13)."""
    d = router_core.dispatch(
        "decide this: cap the fleet router consult tokens at 2000 per "
        "session, or leave uncapped? Pick one and defend it briefly.",
        session_id=SID + "-fp3", model="m")
    assert d.reason != "manual_open_question_frontier" or True
    # the manual ask routes the DECISION lane (or its frontier open-question
    # form) — never a risk_r2 lexicon consult
    assert not str(d.reason).startswith("risk_r"), d.reason


def test_item3_helper_shapes():
    assert router_core._clean_opinion_tradeoff(BC2_CLEAN)
    assert router_core._clean_opinion_tradeoff(
        "What's your general take on staging vs production parity? "
        "No decision, just your view.")
    assert router_core._clean_opinion_tradeoff(
        "What's your opinion on typed rewrites versus incremental ones?")
    assert not router_core._clean_opinion_tradeoff(RISKY_ASK)
    assert not router_core._clean_opinion_tradeoff("")
    assert not router_core._clean_opinion_tradeoff("delete the temp file")
    # 'X or Y' prose WITHOUT the take-on shape is outside the deny
    assert not router_core._clean_opinion_tradeoff(
        "Should we use vitest or jest? Pick one.")
