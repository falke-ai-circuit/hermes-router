"""R15 — risk-triggered consults (LEG 1) + on-demand consult fixes (LEG 2).

Spec: r15_risk_consult_spec (conductor go 2026-09-21). Zero-network: the
L2 aux seam is mocked at semantic_classifier.aux_raw_call; the dispatch
consult is decision-level (no provider call). SID convention "s-r15".
"""
import pytest

import hermes_router as plugin
from hermes_router import (
    bypass_watch,
    completion_audit,
    config_access,
    risk,
    route_gate,
    router_core,
    state,
)

SID = "s-r15"

LOGGED = []


@pytest.fixture()
def _reset(monkeypatch):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    LOGGED.clear()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    yield


# ---------------------------------------------------------------- LEG 1 ----
# L1 lexicon matrix — co-occurrence rule: verb AND (scope|target|irrev).

def test_l1_r3_irreversible():
    cls, _ = risk.classify("force push and reset the fleet genome, overwrite without backup")
    assert cls == "r3"


def test_l1_r3_fleet_scope():
    cls, _ = risk.classify("delete all profiles' doctrine registries")
    assert cls == "r3"


def test_l1_r3_credentials():
    cls, _ = risk.classify("rotate the credentials now")
    assert cls == "r3"


def test_l1_r2_single_agent_recoverable():
    cls, _ = risk.classify("deploy the new build to the live pointer registry")
    assert cls == "r2"


def test_l1_benign_single_verb_never_fires(monkeypatch):
    import hermes_router.semantic_classifier as _sc

    # Co-occurrence rule: a lone action verb is at most an L2 hint; with
    # the aux vote 'safe' it never routes.
    monkeypatch.setattr(_sc, "aux_raw_call", lambda prompt, **k: "safe")
    cls, meta = risk.classify("delete the temp file")
    assert cls == "none"
    assert meta["classes"] == ["action"]
    assert meta["stage"] == "stage2"


def test_l1_meta_discussion_inert():
    for ask in ("should we reset the fleet genome?",
                "what if we deploy to production",
                "how risky is rotating the credentials",
                "what about deleting the config"):
        cls, _ = risk.classify(ask)
        assert cls == "none", ask


def test_l1_quoted_and_fenced_inert():
    ask = '> deploy to production\n```\nrotate the credentials\n```'
    cls, _ = risk.classify(ask)
    assert cls == "none"


def test_l1_advisory_shape_suppressed():
    # R1 advisory: the ask itself is the consultation.
    cls, _ = risk.classify("design a rollout plan for the production fleet")
    assert cls == "none"


def test_l2_hint_upgrades_on_risky(monkeypatch):
    import hermes_router.semantic_classifier as _sc

    monkeypatch.setattr(_sc, "aux_raw_call", lambda prompt, **k: "risky")
    cls, meta = risk.classify("apply the changes to researcher")
    assert cls == "r2"
    assert meta["stage"] == "stage2"


def test_l2_hint_fails_open_on_aux_error(monkeypatch):
    import hermes_router.semantic_classifier as _sc

    def _boom(prompt, **k):
        raise RuntimeError("aux down")

    monkeypatch.setattr(_sc, "aux_raw_call", _boom)
    cls, meta = risk.classify("apply the changes to researcher")
    assert cls == "hint"  # fail-open to NO-risk
    assert meta["stage2"] is None


def test_l2_safe_clears_hint(monkeypatch):
    import hermes_router.semantic_classifier as _sc

    monkeypatch.setattr(_sc, "aux_raw_call", lambda prompt, **k: "safe")
    cls, _ = risk.classify("apply the changes to researcher")
    assert cls == "none"


def test_stage2_parse_last_enum_wins():
    assert risk.parse_stage2_verdict("thinking... the answer is risky") == "risky"
    assert risk.parse_stage2_verdict("risky ... actually safe") == "safe"
    assert risk.parse_stage2_verdict("garbage") is None
    assert risk.parse_stage2_verdict(None) is None


def test_l3_reports_consequential():
    assert risk.reports_consequential(
        "Applied the changes to the live config and restarted.")
    assert risk.reports_consequential(
        "Deployed to production, all profiles updated.")
    # No live/fleet/config target -> not the signal.
    assert not risk.reports_consequential(
        "Applied the lint fixes locally in the scratch repo.")
    assert not risk.reports_consequential("")


def test_l3_audit_fires_on_risk_report(monkeypatch):
    # The audit gate's fire-policy path: a risk-reporting response forces
    # _fire even off-cadence. Ask recovered via hook context; async path
    # mocked (no provider, no thread).
    monkeypatch.setattr(completion_audit, "audit_enabled", lambda: True)
    monkeypatch.setattr(completion_audit, "audit_mode", lambda: "on")
    monkeypatch.setattr(completion_audit, "audit_sync_seconds", lambda: 0)
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 999)
    monkeypatch.setattr(state, "get_last_seen", lambda sid: "do the deploy")
    monkeypatch.setattr(state, "has_pending_render", lambda sid: False)
    fired = {}
    monkeypatch.setattr(completion_audit, "run_completion_audit",
                        lambda *a, **k: fired.setdefault("fired", a))
    # _MIN_RESPONSE_CHARS is 500 — pad the delivery body around the report.
    resp = ("Applied the changes to the live config and verified them. "
            + "detail " * 80)
    completion_audit.audit_gate(
        SID, resp, model="m", context={"user_message": "do the deploy"})
    assert fired.get("fired")


def test_l3_no_audit_without_risk_report(monkeypatch):
    monkeypatch.setattr(completion_audit, "audit_enabled", lambda: True)
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 999)
    monkeypatch.setattr(state, "get_last_seen", lambda sid: "do the deploy")
    monkeypatch.setattr(state, "has_pending_render", lambda sid: False)
    fired = {}
    monkeypatch.setattr(completion_audit, "run_completion_audit",
                        lambda *a, **k: fired.setdefault("fired", a))
    resp = ("Here is the summary you asked for, with all sections covered. "
            + "detail " * 80)
    completion_audit.audit_gate(
        SID, resp, model="m", context={"user_message": "do the deploy"})
    assert not fired


def test_dispatch_risk_turn_fires_consult(monkeypatch):
    monkeypatch.setattr(router_core, "pre_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 0)
    d = router_core.dispatch("rotate the fleet credentials now",
                             session_id=SID, model="m")
    assert d.mode == router_core.MODE_CONSULT
    assert d.reason == "risk_r3"
    assert d.orientation is True


def test_dispatch_benign_turn_no_consult(monkeypatch):
    monkeypatch.setattr(router_core, "pre_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 0)
    d = router_core.dispatch("what is the capital of France",
                             session_id=SID + "-b", model="m")
    assert d.mode == router_core.MODE_FLASH_DIRECT


def test_dispatch_risk_off_mode(monkeypatch):
    monkeypatch.setattr(router_core, "pre_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 0)
    monkeypatch.setattr(risk, "risk_cfg",
                        lambda: dict(risk._RISK_DEFAULTS, mode="off"))
    d = router_core.dispatch("rotate the fleet credentials now",
                             session_id=SID + "-c", model="m")
    assert d.mode == router_core.MODE_FLASH_DIRECT


def test_risk_master_switch_off(monkeypatch):
    monkeypatch.setattr(risk, "risk_cfg",
                        lambda: dict(risk._RISK_DEFAULTS, enabled=False))
    monkeypatch.setattr(router_core, "pre_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 0)
    d = router_core.dispatch("rotate the fleet credentials now",
                             session_id=SID + "-d", model="m")
    assert d.mode == router_core.MODE_FLASH_DIRECT


# ---------------------------------------------------------------- LEG 2 ----

def test_fuzzy_typo_family_match():
    assert route_gate.detect_declared_user("Vonsult frontier and dig deeper") \
        == route_gate.LANE_HIGHER_PRE


def test_fuzzy_does_not_steal_strict_lanes():
    # strict tables always win; non-consult first words stay inert
    assert route_gate.detect_declared_user("anchor this") == route_gate.LANE_HIGHER_PRE
    assert route_gate.detect_declared_user("hello there friend") is None


def test_fuzzy_echo_guard_still_binds():
    assert route_gate.detect_declared_user('> vonsult frontier now') is None


def test_consult_alias_routes_named_model(monkeypatch):
    import hermes_router.anchor_chain as _ac

    monkeypatch.setattr(_ac, "_resolve_with_source",
                        lambda rest, lane: ("glm", "z-ai/glm-5.3", "alias")
                        if rest.startswith("glm") else None)
    got = route_gate.detect_model_override("consult glm 5.3 she is fromtier")
    assert got is not None
    lane, alias, model_id, source = got
    assert lane == route_gate.LANE_HIGHER_PRE
    assert (alias, model_id, source) == ("glm", "z-ai/glm-5.3", "alias")


def test_consult_unknown_alias_silent_fallback(monkeypatch):
    import hermes_router.anchor_chain as _ac

    monkeypatch.setattr(_ac, "_resolve_with_source", lambda rest, lane: None)
    assert route_gate.detect_model_override("consult nosuchmodel here") is None


def test_unrouted_banner_appended(monkeypatch):
    bypass_watch.reset()
    try:
        bypass_watch.capture_from_request(
            {"messages": [{"role": "tool", "content":
                           "POST https://openrouter.ai/v1/chat/completions"}]},
            SID)
        text = "here is the answer"
        out = bypass_watch.audit_turn(SID, lambda e, **f: LOGGED.append((e, f)))
        assert out == bypass_watch.UNROUTED_BANNER
        delivered = text + "\n\n" + out
        assert "router: direct provider call detected, unrouted" in delivered
    finally:
        bypass_watch.reset()


def test_unrouted_banner_silent_when_routed(monkeypatch):
    bypass_watch.reset()
    try:
        bypass_watch.capture_from_request(
            {"messages": [{"role": "tool", "content":
                           "POST https://openrouter.ai/v1/chat/completions"}]},
            SID)
        # a route exists this turn (turn-claim) -> no banner
        route_gate.stamp_turn_claim(SID, "higher-pre", "declared_user")
        assert bypass_watch.audit_turn(SID, lambda e, **f: None) == ""
    finally:
        bypass_watch.reset()


def test_unrouted_banner_empty_without_capture():
    bypass_watch.reset()
    try:
        assert bypass_watch.audit_turn(SID, lambda e, **f: None) == ""
    finally:
        bypass_watch.reset()
