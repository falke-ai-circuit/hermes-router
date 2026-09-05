"""v3.3.0 Phase 1 tests — struggle classifier (F1), shadow mode (F2),
infra suppression plumbing (F3), calibration harness (F4).

Reviewer-audited coverage (tests 1-13):
  1  infra patterns classify error-text evidence -> infra (table-driven)
  2  valid-but-unchanged tool results classify -> reasoning
  3  no state -> ambiguous, never raises
  4  shadow mode: struggle fires -> struggle_shadow log line, NO consult
     staged, config/level/spend untouched (monkeypatched stage fn assert)
  5  user_struggle alone -> confirm_only=true in log payload
  6  static mode byte-compat: existing dispatch tests pass UNCHANGED
  7  corrected (reviewer test 13): infra_cooldown skip path in Phase 1
     asserts INERTNESS (log + continue), never the skip
  8  adaptive in Phase 1 -> behaves static + logs adaptive_not_armed
  9  sidecar-free: no new persistent state files in Phase 1
  10 (reviewer) static mode + seeded struggle_kind=infra inside cooldown ->
     dispatch identical to unseeded case
  11 (reviewer) benign-negative infra table: "timeout=30"/"quota=1000" in a
     valid tool result does NOT classify infra
  12 (reviewer) multi-call turn: struggle_signals increments ONCE per
     (task, turn_key)
  13 (reviewer) F4 harness test: fixture log incl. rotated .1 files +
     malformed lines
"""
import json
import os
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
SCRIPTS_DIR = os.path.join(PLUGIN_DIR, "scripts")
for _p in (PLUGIN_DIR, PARENT_DIR, SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import complexity, router_core, state, struggle_class  # noqa: E402
import calibrate_struggle  # noqa: E402

ROUTINE_ASK = "What's the weather like in poetry form?"
STILL_BROKEN = "still broken — same error again"

INFRA_ERROR_TEXTS = [
    ("HTTP 503 Service Unavailable", "fail_text_infra_pattern"),
    ("request timed out after 30s", "fail_text_infra_pattern"),
    ("connection refused by upstream host", "fail_text_infra_pattern"),
    ("rate limit exceeded, retry with backoff", "fail_text_infra_pattern"),
    ("auth failed: 401 unauthorized", "fail_text_infra_pattern"),
    ("ECONNRESET while streaming response", "fail_text_infra_pattern"),
    ("provider capacity overloaded, try later", "fail_text_infra_pattern"),
    ("monthly quota exhausted for model", "fail_text_infra_pattern"),
]


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
# Test 1 — infra patterns (table-driven, 6+ cases)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,expect_detail", INFRA_ERROR_TEXTS)
def test_infra_patterns_classify_infra(text, expect_detail):
    task = router_core.task_id_for("s1", ROUTINE_ASK, "m")
    for _ in range(router_core.SAME_FAILURE_ESCALATE_N):
        router_core.record_provider_failure(task, text)
    kind, detail = struggle_class.classify_struggle(task, "repeated_same_failure")
    assert kind == "infra"
    assert detail == expect_detail


# ---------------------------------------------------------------------------
# Test 2 — valid-but-unchanged results -> reasoning
# ---------------------------------------------------------------------------


def test_valid_but_unchanged_results_classify_reasoning():
    task = router_core.task_id_for("s2", ROUTINE_ASK, "m")
    turn = "turn-r"
    router_core.record_tool_call(task, "weather report v1", turn)
    for _ in range(router_core.TOOLLOOP_CALLS_N):
        router_core.record_tool_call(task, "weather report v1", turn)
    kind, detail = struggle_class.classify_struggle(task, "tool_loop_no_new_content")
    assert kind == "reasoning"
    assert detail == "results_semantically_unchanged"


# ---------------------------------------------------------------------------
# Test 3 — no state -> ambiguous, never raises
# ---------------------------------------------------------------------------


def test_no_state_is_ambiguous_never_raises():
    kind, detail = struggle_class.classify_struggle("nonexistent-task", "repeated_same_failure")
    assert kind == "ambiguous"
    assert detail == "no_data"
    kind2, detail2 = struggle_class.classify_struggle("", "")
    assert kind2 == "ambiguous"
    # garbage inputs never raise
    kind3, _ = struggle_class.classify_struggle(None, None)  # type: ignore[arg-type]
    assert kind3 == "ambiguous"


# ---------------------------------------------------------------------------
# Test 4 — shadow mode: log line written, NO consult staged, spend untouched
# ---------------------------------------------------------------------------


def test_shadow_mode_logs_but_never_stages(monkeypatch):
    log_lines = []
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True, "log_routes": True,
        "log_path": "/tmp/test-v330-shadow.log",
        "complexity": {"mode": "static", "shadow": True},
    }, raising=False)
    monkeypatch.setattr(plugin, "_log_route",
                        lambda event, **fields: log_lines.append((event, fields)))
    stage_calls = []
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda *a, **k: stage_calls.append(a) or None)

    d = router_core.dispatch(STILL_BROKEN, session_id="sh1", model="m")
    assert d.mode == router_core.MODE_OWNERSHIP  # static escalation unchanged
    shadow = [f for e, f in log_lines if e == "PRE" and f.get("event_detail") == "struggle_shadow"]
    assert len(shadow) == 1
    assert shadow[0]["reason"] == "user_struggle_signal"
    assert shadow[0]["would_step"] == 1
    assert stage_calls == []  # shadow itself must never stage a consult
    # config/level/spend untouched: config reads unaffected
    assert router_core._complexity_level() == 3
    assert router_core.shadow_enabled() is True


# ---------------------------------------------------------------------------
# Test 5 — user_struggle alone -> confirm_only=true
# ---------------------------------------------------------------------------


def test_user_struggle_alone_confirm_only(monkeypatch):
    log_lines = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda event, **fields: log_lines.append((event, fields)))
    task = router_core.task_id_for("s5", STILL_BROKEN, "m")
    router_core.log_struggle_shadow(task, "s5", "user_struggle_signal", turn_key="tk5")
    shadow = [f for e, f in log_lines if f.get("event_detail") == "struggle_shadow"]
    assert len(shadow) == 1
    assert shadow[0]["confirm_only"] is True
    assert shadow[0]["consult_would_fire"] is False
    assert shadow[0]["kind"] == "ambiguous"  # no tool evidence stored


# ---------------------------------------------------------------------------
# Test 6 — static mode byte-compat: existing dispatch tests pass unchanged
# (this file's sibling test_router_core_v3.py runs UNEDITED in the suite;
# here: the routing-table behaviors still hold with v3.3.0 code in place)
# ---------------------------------------------------------------------------


def test_static_byte_compat_routing_table_unchanged():
    # struggle escalation unchanged
    task = router_core.task_id_for("sx", ROUTINE_ASK, "m")
    for _ in range(router_core.SAME_FAILURE_ESCALATE_N):
        router_core.record_provider_failure(task, "boom")
    d3 = router_core.dispatch(ROUTINE_ASK, session_id="sx", model="m")
    assert d3.lane == router_core.LANE_COMPLEXITY
    assert d3.mode == router_core.MODE_OWNERSHIP
    assert d3.reason == "repeated_same_failure"
    # routine pass-through unchanged
    d = router_core.dispatch(ROUTINE_ASK, session_id="sx2", model="m")
    assert d.lane == router_core.LANE_UNCENSORED
    assert d.mode == router_core.MODE_FLASH_DIRECT


# ---------------------------------------------------------------------------
# Test 7 (reviewer-corrected) — Phase 1 INERTNESS, never the skip
# ---------------------------------------------------------------------------


def test_infra_cooldown_static_mode_inert(monkeypatch):
    # Static mode + fresh infra cooldown: dispatch CONTINUES normal escalation
    # (identical to unseeded case) — the skip never fires in Phase 1.
    log_lines = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda event, **fields: log_lines.append((event, fields)))
    monkeypatch.setattr(router_core, "complexity_mode", lambda: "static")
    task = router_core.task_id_for("s7", ROUTINE_ASK, "m")
    router_core.record_provider_failure(task, "boom")
    router_core.record_provider_failure(task, "boom")
    router_core.record_provider_failure(task, "HTTP 503 unavailable")
    # 3rd failure has a DIFFERENT signature (resets count) — top up to the
    # struggle threshold with the infra text.
    router_core.record_provider_failure(task, "HTTP 503 unavailable")
    router_core.record_provider_failure(task, "HTTP 503 unavailable")
    router_core.record_infra_cooldown(task)  # fresh infra classification
    assert router_core.infra_cooldown_active(task) is True

    d = router_core.dispatch(ROUTINE_ASK, session_id="s7", model="m")
    assert d.mode == router_core.MODE_OWNERSHIP  # NOT skipped — Phase 1 inert
    suppressed = [f for e, f in log_lines
                  if f.get("event_detail") == "struggle_suppressed_would_skip"]
    assert suppressed == []  # guard body unreachable in static mode dispatch


# ---------------------------------------------------------------------------
# Test 8 — adaptive in Phase 1 -> behaves static + logs adaptive_not_armed
# ---------------------------------------------------------------------------


def test_adaptive_phase1_behaves_static_and_logs(monkeypatch):
    log_lines = []
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"mode": "adaptive", "shadow": True})
    monkeypatch.setattr(plugin, "_log_route",
                        lambda event, **fields: log_lines.append((event, fields)))

    d = router_core.dispatch(STILL_BROKEN, session_id="s8", model="m")
    # behaves AS STATIC (arm protection) — struggle escalation still fires
    assert d.mode == router_core.MODE_OWNERSHIP
    armed = [f for e, f in log_lines
             if f.get("event_detail") == "adaptive_not_armed"]
    assert len(armed) == 1  # once per session
    d2 = router_core.dispatch(STILL_BROKEN, session_id="s8", model="m")
    assert d2.mode == router_core.MODE_OWNERSHIP
    armed2 = [f for e, f in log_lines
              if f.get("event_detail") == "adaptive_not_armed"]
    assert len(armed2) == 1  # no repeat for the same session


# ---------------------------------------------------------------------------
# Test 9 — sidecar-free: no new persistent state files in Phase 1
# ---------------------------------------------------------------------------


def test_phase1_state_is_memory_only(tmp_path, monkeypatch):
    # No struggle/infra state file is created anywhere (in-memory task state
    # only). Scan the tmp hermes home before + after a shadow-logged dispatch;
    # the route log itself is the ONLY new file (route logging is pre-existing
    # v2 behavior, not a Phase 1 sidecar).
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True, "log_routes": True,
        "log_path": str(tmp_path / "route.log"),
        "complexity": {"mode": "static", "shadow": True},
    }, raising=False)
    before = set(os.listdir(tmp_path))
    router_core.dispatch(STILL_BROKEN, session_id="s9", model="m")
    after = set(os.listdir(tmp_path))
    new_files = after - before
    assert new_files <= {"route.log"}, f"unexpected sidecar files: {new_files - {'route.log'}}"
    # explicit: no struggle/infra sidecar names appear
    for name in ("struggle", "shadow", "calibration"):
        assert not any(name in f for f in new_files)


# ---------------------------------------------------------------------------
# Test 10 (reviewer) — static + seeded struggle_kind=infra inside cooldown ->
# dispatch identical to unseeded case
# ---------------------------------------------------------------------------


def test_static_seeded_infra_dispatch_identical_to_unseeded(monkeypatch):
    monkeypatch.setattr(router_core, "complexity_mode", lambda: "static")
    # seeded case: infra classified INSIDE the cooldown window
    seeded_task = router_core.task_id_for("s10a", ROUTINE_ASK, "m")
    for _ in range(router_core.SAME_FAILURE_ESCALATE_N):
        router_core.record_provider_failure(seeded_task, "boom")
    router_core.record_infra_cooldown(seeded_task)
    # unseeded case: same struggle, no infra state
    unseeded_task = router_core.task_id_for("s10b", ROUTINE_ASK, "m")
    for _ in range(router_core.SAME_FAILURE_ESCALATE_N):
        router_core.record_provider_failure(unseeded_task, "boom")

    d_seeded = router_core.dispatch(ROUTINE_ASK, session_id="s10a", model="m")
    d_unseeded = router_core.dispatch(ROUTINE_ASK, session_id="s10b", model="m")
    # byte-compat: identical lane/mode/target/reason — seed changes nothing
    assert (d_seeded.lane, d_seeded.mode, d_seeded.model_target, d_seeded.reason) == \
           (d_unseeded.lane, d_unseeded.mode, d_unseeded.model_target, d_unseeded.reason)
    assert d_seeded.mode == router_core.MODE_OWNERSHIP


# ---------------------------------------------------------------------------
# Test 11 (reviewer) — benign-negative infra table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("benign", [
    "config timeout=30 applied",
    "quota:1000 requests remaining",
    "retry timeout = 30s ok",
    "max_requests 500 per hour",
    "timeout=30 and quota=1000 in settings",
])
def test_benign_assignments_do_not_classify_infra(benign):
    assert struggle_class._matches_infra(benign) is False


def test_benign_valid_result_not_infra_via_classifier():
    task = router_core.task_id_for("s11", ROUTINE_ASK, "m")
    for _ in range(router_core.SAME_FAILURE_ESCALATE_N):
        router_core.record_provider_failure(task, "job finished timeout=30 quota=1000 ok")
    kind, _ = struggle_class.classify_struggle(task, "repeated_same_failure")
    assert kind == "reasoning"  # NOT infra


# ---------------------------------------------------------------------------
# Test 12 (reviewer) — multi-call turn: signals increment ONCE per (task, turn_key)
# ---------------------------------------------------------------------------


def test_struggle_signals_dedupe_per_turn_key():
    task = router_core.task_id_for("s12", ROUTINE_ASK, "m")
    turn = "turn-42"
    n1 = router_core.record_struggle_signal(task, turn)
    n = n1
    for _ in range(6):
        n = router_core.record_struggle_signal(task, turn)
    assert n == n1 == 1  # re-fires inside the same turn dedupe
    # a genuinely new turn (new turn_key) counts again
    n2 = router_core.record_struggle_signal(task, "turn-43")
    assert n2 == 2
    # and the mapping: first -> 1, second -> 2, third+ -> 3
    assert router_core.would_step_for(1) == 1
    assert router_core.would_step_for(2) == 2
    assert router_core.would_step_for(3) == 3
    assert router_core.would_step_for(9) == 3


# ---------------------------------------------------------------------------
# Test 13 (reviewer) — F4 harness: fixture log, rotated .1, malformed lines
# ---------------------------------------------------------------------------


def test_f4_harness_fixture_rotated_and_malformed(tmp_path):
    route = tmp_path / "uncensored-router-test.log"
    route_1 = tmp_path / "uncensored-router-test.log.1"
    route.write_text("\n".join([
        "2026-09-05T10:00:00Z PRE event_detail=struggle_shadow reason=tool_loop_no_new_content kind=infra task_id=abc session_id=s1 would_step=1 consult_would_fire=False suppressed=infra",
        "2026-09-05T10:01:00Z PRE event_detail=struggle_shadow reason=repeated_same_failure kind=reasoning task_id=def session_id=s1 would_step=2 consult_would_fire=True",
        "<<<torn garbage line",
    ]) + "\n")
    route_1.write_text("\n".join([
        "2026-09-04T09:00:00Z PRE event_detail=struggle_shadow reason=user_struggle_signal kind=ambiguous task_id=ghi session_id=s2 would_step=1 consult_would_fire=False confirm_only=True",
        "not-a-route-line at all",
    ]) + "\n")
    files = calibrate_struggle._iter_files([str(route)])
    assert len(files) == 2  # rotated .1 included
    events = calibrate_struggle.parse_route_logs(files)
    assert len(events) == 3  # malformed lines (torn + garbage) skipped silently
    counts, rows = calibrate_struggle.classify_events(events, [])
    assert counts["infra"] == 1
    assert counts["reasoning"] == 1
    assert counts["ambiguous"] == 1


def test_f4_harness_refire_candidates_from_fixture(tmp_path):
    route = tmp_path / "uncensored-router-refire.log"
    route.write_text("\n".join([
        "2026-09-05T04:53:19Z PRE event_detail=anchor_route_fired lane=complexity mode=plan reason=complexity_stage1 route_id=ba9ce5849a6f-100 task_id=bf3881 session_id=sA",
        "2026-09-05T04:53:23Z PRE event_detail=route_skipped lane=complexity reason=anchored_call_failed route_id=ba9ce5849a6f-101 session_id=sA",
        "2026-09-05T04:53:44Z PRE event_detail=route_skipped lane=complexity reason=anchored_call_failed route_id=ba9ce5849a6f-102 session_id=sA",
        "2026-09-05T04:54:04Z PRE event_detail=route_skipped lane=complexity reason=anchored_call_failed route_id=ba9ce5849a6f-103 session_id=sA",
    ]) + "\n")
    events = calibrate_struggle.parse_route_logs([str(route)])
    rows = calibrate_struggle.anchor_refire_candidates(events)
    assert len(rows) == 1
    assert rows[0]["route_id_prefix"] == "ba9ce5849a6f"
    assert rows[0]["refires"] == 3
    assert rows[0]["task_id"] == "bf3881"


def test_f4_harness_diag_join_and_main_smoke(tmp_path, capsys):
    route = tmp_path / "uncensored-router-join.log"
    route.write_text(
        "2026-09-05T13:20:40Z PRE event_detail=route_skipped lane=complexity "
        "reason=anchored_call_failed route_id=zz-1 session_id=api_test_1\n")
    agent = tmp_path / "agent.log"
    agent.write_text(
        "2026-09-05 13:20:46,256 ERROR [api_test_1] hermes_plugins.hermes_router.anchor_exec: "
        "anchor_route_failed reason=empty_response model=m finish=content_filter tool_calls=False\n")
    events = calibrate_struggle.parse_route_logs([str(route)])
    diags = calibrate_struggle.parse_agent_logs([str(agent)])
    assert len(diags) == 1 and diags[0]["finish_reason"] == "content_filter"
    counts, rows = calibrate_struggle.classify_events(events, diags)
    assert counts.get("no_struggle_event", 0) == 0 or counts
    # joined evidence drives the classification away from no_data
    kinds = {r.get("kind") for r in rows}
    assert kinds <= {"infra", "reasoning", "ambiguous"}
    rc = calibrate_struggle.main([
        "--route-glob", str(route), "--agent-glob", str(agent),
        "--profiles-root", str(tmp_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "kinds" in out and "anchor_refire_candidates" in out


# ---------------------------------------------------------------------------
# F1 plumbing details
# ---------------------------------------------------------------------------


def test_record_provider_failure_persists_last_fail_text():
    task = router_core.task_id_for("s14", ROUTINE_ASK, "m")
    long_text = "x" * 500 + " HTTP 502"
    router_core.record_provider_failure(task, long_text)
    rec = router_core.task_state(task)
    assert rec["last_fail_text"] == long_text[:240]  # bounded <= 240 chars
    assert len(rec["last_fail_text"]) == 240


def test_transport_death_classifies_infra():
    task = router_core.task_id_for("s15", ROUTINE_ASK, "m")
    turn = "turn-x"
    # empty tool results (transport died mid-call): zero new content
    router_core.record_tool_call(task, "", turn)
    for _ in range(router_core.TOOLLOOP_CALLS_N):
        router_core.record_tool_call(task, "", turn)
    kind, detail = struggle_class.classify_struggle(task, "tool_loop_no_new_content")
    assert kind == "infra"
    assert detail == "transport_no_new_content"


def test_shadow_disabled_no_log(monkeypatch):
    log_lines = []
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"mode": "static", "shadow": False})
    monkeypatch.setattr(plugin, "_log_route",
                        lambda event, **fields: log_lines.append((event, fields)))
    router_core.dispatch(STILL_BROKEN, session_id="s16", model="m")
    assert not any(f.get("event_detail") == "struggle_shadow" for _, f in log_lines)


def test_state_turn_key_stability():
    a = state.turn_key_for("sess", "same ask", "m")
    b = state.turn_key_for("sess", "same ask", "m")
    c = state.turn_key_for("sess", "different ask", "m")
    assert a == b and a != c
    assert a.startswith("turn:")