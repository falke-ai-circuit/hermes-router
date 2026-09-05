"""v3.0.0 two-lane dispatcher tests: routing table, intensity matrix,
overrides, struggle detection, model-swap staging, decision head gate.

All network is mocked; no aux calls on clear matches (stage-2 only on
borderline). Mock aux verdicts through semantic_classifier.aux_raw_call.
"""
import sys
import os
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import complexity, router_core  # noqa: E402

PLAN_ASK = ("Design a multi-stage migration plan for splitting the monolith "
            "into services, including architecture trade-offs between approaches.")
DEBUG_ASK = "why does my worker keep hanging with no error every third retry, I cannot figure out why"
CROSS_ASK = "map the dependencies and impact analysis across the entire codebase for this change"
ROUTINE_ASK = "What's the weather like in poetry form?"
STILL_BROKEN = "still broken — same error again"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    router_core._test_reset()
    plugin.state.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True, "match_threshold": 1},
        "log_routes": False,
    }, raising=False)
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    _chain = _test_anchor_chain()
    monkeypatch.setattr(router_core.anchor_chain, "load_anchor_chain", lambda: _chain)
    yield
    router_core._test_reset()
    plugin.state.clear()


def _test_anchor_chain():
    from hermes_router import anchor_chain

    return anchor_chain.AnchorChainCfg(
        primary=anchor_chain.parse_anchor_uri("openrouter://test/anchor-primary", "primary"),
        judge=anchor_chain.parse_anchor_uri("openrouter://test/anchor-judge", "judge"),
        overflow="pass_through",
        daily_cap_usd=2.0,
        pricing={},
    )


# ---------------------------------------------------------------------------
# Routing table (complex -> PLAN, struggle -> OWNERSHIP, routine -> pass)
# ---------------------------------------------------------------------------


def test_routing_table_complex_ask_routes_to_plan():
    d = router_core.dispatch(PLAN_ASK, session_id="s1", model="m")
    assert d.lane == router_core.LANE_COMPLEXITY
    assert d.mode == router_core.MODE_PLAN
    assert d.model_target == "test/anchor-primary"
    assert d.reason.startswith("complexity_")


def test_routing_table_routine_ask_passes_through():
    d = router_core.dispatch(ROUTINE_ASK, session_id="s1", model="m")
    assert d.lane == router_core.LANE_UNCENSORED
    assert d.mode == router_core.MODE_FLASH_DIRECT
    assert d.model_target is None


def test_routing_table_struggle_escalates_to_ownership():
    # struggle state must key the task hash EXACTLY as dispatch does
    task = router_core.task_id_for("sx", ROUTINE_ASK, "m")
    for _ in range(router_core.SAME_FAILURE_ESCALATE_N):
        router_core.record_provider_failure(task, "boom")
    d3 = router_core.dispatch(ROUTINE_ASK, session_id="sx", model="m")
    assert d3.lane == router_core.LANE_COMPLEXITY
    assert d3.mode == router_core.MODE_OWNERSHIP
    assert d3.reason == "repeated_same_failure"


def test_routing_table_toolloop_escalates_to_ownership():
    task = router_core.task_id_for("sy", ROUTINE_ASK, "m")
    turn = "turn-1"
    # seed the seen-hash set: call 1 carries new content (count resets to 0);
    # the next TOOLLOOP_CALLS_N repeats count as no-new-content.
    router_core.record_tool_call(task, "same stale output", turn)
    n = 0
    for _ in range(router_core.TOOLLOOP_CALLS_N):
        n = router_core.record_tool_call(task, "same stale output", turn)
    assert n >= router_core.TOOLLOOP_CALLS_N
    d = router_core.dispatch(ROUTINE_ASK, session_id="sy", model="m")
    assert d.lane == router_core.LANE_COMPLEXITY
    assert d.mode == router_core.MODE_OWNERSHIP
    assert d.reason == "tool_loop_no_new_content"


def test_routing_table_user_struggle_signal_escalates():
    d = router_core.dispatch(STILL_BROKEN, session_id="sz", model="m")
    assert d.lane == router_core.LANE_COMPLEXITY
    assert d.mode == router_core.MODE_OWNERSHIP
    assert d.reason == "user_struggle_signal"


# ---------------------------------------------------------------------------
# Intensity matrix L0-L3
# ---------------------------------------------------------------------------


def test_intensity_l0_off_no_route(monkeypatch):
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 0)
    d = router_core.dispatch(PLAN_ASK, session_id="l0", model="m")
    assert d.lane == router_core.LANE_UNCENSORED
    assert d.mode == router_core.MODE_FLASH_DIRECT


def test_intensity_l1_manual_only(monkeypatch):
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 1)
    d = router_core.dispatch(PLAN_ASK, session_id="l1", model="m")
    assert d.mode == router_core.MODE_FLASH_DIRECT
    # explicit override still routes at L1
    d2 = router_core.dispatch(PLAN_ASK + "\nanchor this", session_id="l1b", model="m")
    assert d2.mode == router_core.MODE_CONSULT
    assert d2.override_used == "anchor"


def test_intensity_l2_conservative_planning_only(monkeypatch):
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 2)
    # planning ask routes
    d = router_core.dispatch(PLAN_ASK, session_id="l2a", model="m")
    assert d.lane == router_core.LANE_COMPLEXITY
    # debug-only ask does NOT auto-route at L2
    d2 = router_core.dispatch(DEBUG_ASK, session_id="l2b", model="m")
    assert d2.lane == router_core.LANE_UNCENSORED


def test_intensity_l3_aggressive_debug_and_cross_file(monkeypatch):
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    d = router_core.dispatch(CROSS_ASK, session_id="l3a", model="m")
    assert d.lane == router_core.LANE_COMPLEXITY
    d2 = router_core.dispatch(PLAN_ASK, session_id="l3b", model="m")
    assert d2.lane == router_core.LANE_COMPLEXITY


# ---------------------------------------------------------------------------
# Stage-1 / stage-2 split
# ---------------------------------------------------------------------------


def test_stage1_clear_match_skips_aux():
    with mock.patch("hermes_router.semantic_classifier.aux_raw_call") as aux:
        route, meta = complexity.classify(PLAN_ASK, 3)
    assert route is True
    assert meta["stage"] == "stage1"
    aux.assert_not_called()  # clear matches never pay stage-2


def test_stage2_runs_on_borderline_only(monkeypatch):
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    weak = "look at this weird error result sometime"  # 1 debug hit -> borderline at L3
    v, sig = complexity.stage1_verdict(weak, 3)
    assert v == "borderline"
    with mock.patch("hermes_router.semantic_classifier.aux_raw_call", return_value="complex") as aux:
        route, meta = complexity.classify(weak, 3)
    assert aux.called
    assert route is True
    assert meta["stage"] == "stage2"


def test_stage2_routine_downgrades_borderline():
    weak = "look at this weird error result sometime"
    with mock.patch("hermes_router.semantic_classifier.aux_raw_call", return_value="routine"):
        route, meta = complexity.classify(weak, 3)
    assert route is False
    assert meta["stage2"] == "routine"


def test_stage2_failure_fails_open():
    weak = "look at this weird error result sometime"
    with mock.patch("hermes_router.semantic_classifier.aux_raw_call", return_value=None):
        route, meta = complexity.classify(weak, 3)
    assert route is False  # fail-open = pass-through


# ---------------------------------------------------------------------------
# Inline overrides
# ---------------------------------------------------------------------------


def test_override_anchor_forces_complexity():
    d = router_core.dispatch("quick check\nanchor this", session_id="o1", model="m")
    assert d.lane == router_core.LANE_COMPLEXITY
    assert d.override_used == "anchor"
    assert d.mode == router_core.MODE_CONSULT


def test_override_skip_forces_passthrough():
    d = router_core.dispatch(PLAN_ASK + "\nskip anchor", session_id="o2", model="m")
    assert d.lane == router_core.LANE_UNCENSORED
    assert d.mode == router_core.MODE_FLASH_DIRECT
    assert d.override_used == "skip"


def test_override_requires_standalone_line():
    assert complexity.detect_override("please anchor this for me") is None
    assert complexity.detect_override("anchor this\n") == "anchor"
    assert complexity.detect_override("# anchor this") == "anchor"
    assert complexity.detect_override("skip anchor") == "skip"


# ---------------------------------------------------------------------------
# Model swap staging
# ---------------------------------------------------------------------------


def test_stage_and_consume_pending_swap():
    d = router_core.dispatch(PLAN_ASK, session_id="sw1", model="m")
    assert d.model_target
    router_core.stage_model_swap("sw1", d)
    rec = router_core.pending_model_swap("sw1")
    assert rec is not None
    assert rec["endpoint"].model == "test/anchor-primary"
    assert rec["endpoint"].base_url == "https://openrouter.ai/api/v1"
    assert rec["endpoint"].api_key_env == "OPENROUTER_API_KEY"
    # consumed exactly once
    assert router_core.pending_model_swap("sw1") is None


def test_swap_expiry_after_60s():
    d = router_core.dispatch(PLAN_ASK, session_id="sw2", model="m")
    router_core.stage_model_swap("sw2", d)
    import hermes_router.router_core as rc
    with rc._PENDING_SWAP_LOCK:
        rc._PENDING_SWAP["sw2"]["staged_at"] -= 120.0
    assert router_core.pending_model_swap("sw2") is None


# ---------------------------------------------------------------------------
# PRE middleware integration: dispatcher fires, uncensored path untouched
# ---------------------------------------------------------------------------


def test_pre_middleware_dispatcher_logs_and_stages(monkeypatch):
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    req = {"model": "m", "messages": [{"role": "user", "content": PLAN_ASK}]}
    with mock.patch.object(plugin.router, "call") as venice:
        result = plugin.on_llm_request(request=req, original_request=req, session_id="pm1")
    assert result == {}  # PRE never rewrites on complexity lane
    assert not venice.called  # uncensored render untouched
    assert router_core.peek_pending_swap("pm1") is not None
    # cleanup: the pending swap must not leak into other tests
    router_core.pending_model_swap("pm1")


def test_pre_middleware_routine_request_untouched():
    req = {"model": "m", "messages": [{"role": "user", "content": ROUTINE_ASK}]}
    with mock.patch.object(plugin.router, "call") as venice:
        result = plugin.on_llm_request(request=req, original_request=req, session_id="pm2")
    assert result == {}
    assert not venice.called


# ---------------------------------------------------------------------------
# Decision head gate (amendment: default heuristic unchanged)
# ---------------------------------------------------------------------------


def test_decision_head_default_heuristic():
    from hermes_router import decision_head
    assert decision_head.configured_backend() == "heuristic"


def test_decision_head_routellm_missing_weights_falls_back(monkeypatch):
    from hermes_router import decision_head
    monkeypatch.setattr(decision_head, "configured_backend", lambda: "routellm_mf")
    monkeypatch.setattr(decision_head, "_weights_path",
                        lambda: "/nonexistent/weights/dir")
    eff, head = decision_head._load_head("routellm_mf")
    assert eff == "heuristic"
    assert head is None
    # score still works (heuristic path)
    s = decision_head.score(PLAN_ASK)
    assert 0.0 <= s <= 1.0


def test_decision_head_threshold_gate(monkeypatch):
    from hermes_router import decision_head
    monkeypatch.setattr(decision_head, "configured_backend", lambda: "heuristic")
    monkeypatch.setattr(decision_head, "threshold", lambda: 0.45)
    s = decision_head.score(PLAN_ASK)  # strong planning signals -> >= 0.5
    assert decision_head.route(PLAN_ASK) == (s >= 0.45)
    assert decision_head.route(ROUTINE_ASK) is False


# ---------------------------------------------------------------------------
# Consult envelopes (integration verdict 2)
# ---------------------------------------------------------------------------


def test_frontier_envelope_shape():
    d = router_core.RouteDecision(task_id="t", lane=router_core.LANE_COMPLEXITY,
                                  mode=router_core.MODE_PLAN, model_target="m",
                                  reason="r", route_id="rid")
    env = router_core.build_frontier_envelope("frontier_plan", "producer-x", d, "answer body",
                                              evidence_refs=["a.md"], limitations="none")
    assert env["kind"] == "frontier_plan"
    assert env["producer"] == "producer-x"
    assert env["route_id"] == "rid"
    assert env["task_id"] == "t"
    assert env["answer"] == "answer body"
    assert env["evidence_refs"] == ["a.md"]
    assert env["limitations"] == "none"


def test_consult_result_store_take():
    env = {"kind": "consultation", "answer": "x"}
    router_core.store_consult_result("r1", env)
    assert router_core.take_consult_result("r1") == env
    assert router_core.take_consult_result("r1") is None


# ---------------------------------------------------------------------------
# Never-raise contract
# ---------------------------------------------------------------------------


def test_dispatch_never_raises_on_garbage():
    d = router_core.dispatch(None, session_id="g1", model="m")  # type: ignore[arg-type]
    assert d.lane == router_core.LANE_UNCENSORED
    d2 = router_core.dispatch("", session_id="", model="")
    assert d2.mode == router_core.MODE_FLASH_DIRECT


def test_route_decision_dataclass_fields():
    d = router_core.dispatch(PLAN_ASK, session_id="dc1", model="m")
    for f in ("task_id", "lane", "mode", "model_target", "reason", "ts"):
        assert hasattr(d, f)
    assert d.ts > 0
    assert d.route_id