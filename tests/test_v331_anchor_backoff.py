"""v3.3.1 close-out fix tests — anchor failure backoff (re-fire suppression).

Defect (verified 2026-09-05): 98 anchored_call_failed route_skips across 3
profiles (coder 63, researcher 19, analyst 16); task ba9ce5849a6f… fired its
anchor attempt 27x over 103 min (09:29→11:12) into a failing OpenRouter
endpoint. v3.2.0's _SWAP_DONE guard keys (session_id, task_id) with a 600s
TTL and prevents re-staging INSIDE one turn — but each NEW user turn on the
same stuck ask = TTL long expired = fresh anchor staging = fresh failure =
repeat. Nothing remembered the anchor FAILED for this task.

Fix: _ANCHOR_FAIL_BACKOFF ledger in router_core keyed (session_id, task_id)
-> {fails, last_fail_ts, last_reason}. stage_model_swap no-ops while the key
sits inside its exponential window (30s * 2**(fails-1), capped 1800s); a
failed anchored call records a fail at the consumption site (on_llm_execution
route_skipped branch), a success clears it. cap_blocked does NOT count as a
failure. enabled:false restores v3.3.0 behavior exactly.

All network mocked.
"""
import sys
import os
import time
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
SCRIPTS_DIR = os.path.join(PLUGIN_DIR, "scripts")
for _p in (PLUGIN_DIR, PARENT_DIR, SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import router_core  # noqa: E402
import calibrate_struggle  # noqa: E402

PLAN_ASK = ("Design a multi-stage migration plan for splitting the monolith "
            "into services, including architecture trade-offs between approaches.")
LOGGED = []


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    router_core._test_reset()
    plugin.state.clear()
    LOGGED.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True, "match_threshold": 1},
        "log_routes": False,
    }, raising=False)
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    monkeypatch.setattr(plugin, "_log_route", _capture_log)
    _chain = _test_anchor_chain()
    monkeypatch.setattr(router_core.anchor_chain, "load_anchor_chain", lambda: _chain)
    yield
    router_core._test_reset()
    plugin.state.clear()


def _capture_log(event, **fields):
    LOGGED.append((event, dict(fields)))


def _test_anchor_chain():
    from hermes_router import anchor_chain

    return anchor_chain.AnchorChainCfg(
        primary=anchor_chain.parse_anchor_uri("openrouter://test/anchor-primary", "primary"),
        judge=anchor_chain.parse_anchor_uri("openrouter://test/anchor-judge", "judge"),
        overflow="pass_through",
        daily_cap_usd=2.0,
        pricing={},
    )


def _dec(session_id="s1", task_id="task-1"):
    """Minimal complexity-lane decision for stage_model_swap."""
    return router_core.RouteDecision(
        task_id=task_id, lane=router_core.LANE_COMPLEXITY,
        mode=router_core.MODE_PLAN, model_target="anchor-primary",
        reason="complexity_stage1", route_id=task_id + "-1",
    )


# ---------------------------------------------------------------------------
# Test 1 — first fail -> 30s window; immediate re-stage blocked + logged
# ---------------------------------------------------------------------------


def test_first_fail_blocks_immediate_restage(monkeypatch):
    monkeypatch.setattr(router_core, "anchor_backoff_cfg",
                        lambda: {"enabled": True, "base_s": 30.0,
                                 "max_s": 1800.0, "ttl_s": 3600.0})
    router_core.record_anchor_backoff_failure("s1", "t1", reason="anchored_call_failed")
    assert router_core.anchor_backoff_window(1) == 30.0
    d = _dec(task_id="t1")
    # _SWAP_DONE has no entry (fresh turn) — ONLY the backoff gate blocks.
    assert router_core.stage_model_swap("s1", d) is None
    blocked = [f for _, f in LOGGED if f.get("event_detail") == "anchor_backoff_blocked"]
    assert len(blocked) == 1
    assert blocked[0]["fails"] == 1
    assert blocked[0]["backoff_s"] == 30.0
    assert blocked[0]["task_id"] == "t1"
    assert blocked[0]["session_id"] == "s1"
    # No anchor attempt staged: no pending swap, no _SWAP_DONE marker.
    assert router_core.peek_pending_swap("s1") is None
    assert ("s1", "t1") not in router_core._SWAP_DONE


# ---------------------------------------------------------------------------
# Test 2 — after backoff expiry -> staging allowed again (fresh attempt)
# ---------------------------------------------------------------------------


def test_backoff_expiry_allows_staging(monkeypatch):
    monkeypatch.setattr(router_core, "anchor_backoff_cfg",
                        lambda: {"enabled": True, "base_s": 30.0,
                                 "max_s": 1800.0, "ttl_s": 3600.0})
    router_core.record_anchor_backoff_failure("s2", "t2")
    d = _dec(task_id="t2")
    assert router_core.stage_model_swap("s2", d) is None  # inside window
    # Age the failure just past the window (TTL still fresh).
    with router_core._PENDING_SWAP_LOCK:
        router_core._ANCHOR_FAIL_BACKOFF[("s2", "t2")]["last_fail_ts"] = \
            time.time() - 31.0
    rec = router_core.stage_model_swap("s2", d)
    assert rec is not None  # fresh attempt allowed
    assert router_core.peek_pending_swap("s2") is not None


# ---------------------------------------------------------------------------
# Test 3 — success clears ledger -> same key stages immediately next time
# ---------------------------------------------------------------------------


def test_success_clears_ledger(monkeypatch):
    router_core.record_anchor_backoff_failure("s3", "t3")
    d = _dec(task_id="t3")
    assert router_core.stage_model_swap("s3", d) is None  # benched
    router_core.clear_anchor_backoff("s3", "t3")
    assert router_core.anchor_backoff_active("s3", "t3") is False
    cleared = [f for _, f in LOGGED if f.get("event_detail") == "anchor_backoff_cleared"]
    assert len(cleared) == 1 and cleared[0]["task_id"] == "t3"
    # Same key stages immediately next time.
    assert router_core.stage_model_swap("s3", d) is not None


def test_success_clear_via_on_llm_execution(monkeypatch):
    """End-to-end: on_llm_execution done outcome clears the entry (success
    clear happens at the consumption site, after envelope delivery)."""
    from hermes_router import anchor_exec

    router_core.record_anchor_backoff_failure("s3e", "t3e")
    d = _dec(task_id="t3e")
    assert router_core.stage_model_swap("s3e", d) is None
    router_core.clear_anchor_backoff("s3e", "t3e")
    # Re-stage stages fresh, then the middleware consumes + succeeds.
    assert router_core.stage_model_swap("s3e", d) is not None
    envelope = router_core.build_frontier_envelope(
        "frontier_plan", "anchor-primary", d, "answer")
    with mock.patch.object(anchor_exec, "maybe_execute_anchored",
                           return_value=("done", envelope)):
        req = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
        out = plugin.on_llm_execution(request=req, next_call=lambda r: "flash",
                                      session_id="s3e")
        assert out == "flash"
    assert router_core.anchor_backoff_active("s3e", "t3e") is False


def test_fail_via_on_llm_execution_records_backoff(monkeypatch):
    """End-to-end: anchored failure at the consumption site increments the
    ledger; the next stage for the same key is blocked (spec test 1 wiring)."""
    from hermes_router import anchor_exec

    d = _dec(task_id="t9e")
    # Stage through the real path so _SWAP_DONE + _PENDING_SWAP are populated.
    assert router_core.stage_model_swap("s9e", d) is not None
    with mock.patch.object(anchor_exec, "maybe_execute_anchored",
                           return_value=None):  # anchored call failed
        req = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
        out = plugin.on_llm_execution(request=req, next_call=lambda r: "flash",
                                      session_id="s9e")
        assert out == "flash"
    assert router_core.anchor_backoff_active("s9e", "t9e") is True
    # Cross-turn re-fire (TTL-expired _SWAP_DONE): the ledger blocks staging.
    with router_core._PENDING_SWAP_LOCK:
        router_core._SWAP_DONE.pop(("s9e", "t9e"), None)
    d2 = _dec(task_id="t9e")
    d2.route_id = "t9e-2"
    assert router_core.stage_model_swap("s9e", d2) is None
    blocked = [f for _, f in LOGGED if f.get("event_detail") == "anchor_backoff_blocked"]
    assert len(blocked) == 1 and blocked[0]["fails"] == 1


# ---------------------------------------------------------------------------
# Test 4 — cap_blocked does NOT increment fails
# ---------------------------------------------------------------------------


def test_cap_blocked_does_not_count(monkeypatch):
    from hermes_router import anchor_exec

    d = _dec(task_id="t4")
    assert router_core.stage_model_swap("s4", d) is not None
    with mock.patch.object(anchor_exec, "maybe_execute_anchored",
                           return_value=("cap_blocked", {"spend": 2.0,
                                                         "cap": 2.0,
                                                         "task_id": "t4"})):
        req = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
        out = plugin.on_llm_execution(request=req, next_call=lambda r: "flash",
                                      session_id="s4")
        assert out == "flash"
    # cap_blocked is spend policy, not anchor health — NO ledger entry.
    assert router_core.anchor_backoff_active("s4", "t4") is False
    assert ("s4", "t4") not in router_core._ANCHOR_FAIL_BACKOFF


# ---------------------------------------------------------------------------
# Test 5 — distinct task_id unaffected by another task's backoff (same session)
# ---------------------------------------------------------------------------


def test_distinct_task_unaffected(monkeypatch):
    router_core.record_anchor_backoff_failure("s5", "t5a")
    d_b = _dec(task_id="t5b")
    assert router_core.anchor_backoff_active("s5", "t5b") is False
    assert router_core.stage_model_swap("s5", d_b) is not None


# ---------------------------------------------------------------------------
# Test 6 — distinct session unaffected (same task text, different session)
# ---------------------------------------------------------------------------


def test_distinct_session_unaffected(monkeypatch):
    router_core.record_anchor_backoff_failure("s6a", "t6")
    assert router_core.anchor_backoff_active("s6b", "t6") is False
    d = _dec(task_id="t6")
    assert router_core.stage_model_swap("s6b", d) is not None


# ---------------------------------------------------------------------------
# Test 7 — enabled: false -> backoff bypassed entirely (v3.3.0 behavior)
# ---------------------------------------------------------------------------


def test_disabled_restores_v330_behavior(monkeypatch):
    monkeypatch.setattr(router_core, "anchor_backoff_cfg",
                        lambda: {"enabled": False, "base_s": 30.0,
                                 "max_s": 1800.0, "ttl_s": 3600.0})
    router_core.record_anchor_backoff_failure("s7", "t7")
    # Ledger write is policy-neutral (it stores), but the GATE is off:
    assert router_core.anchor_backoff_active("s7", "t7") is False
    d = _dec(task_id="t7")
    assert router_core.stage_model_swap("s7", d) is not None  # v3.3.0: stages
    blocked = [f for _, f in LOGGED if f.get("event_detail") == "anchor_backoff_blocked"]
    assert blocked == []


# ---------------------------------------------------------------------------
# Test 8 — ledger TTL reap + 256 size cap honored (no unbounded growth)
# ---------------------------------------------------------------------------


def test_ledger_ttl_reap_and_size_cap(monkeypatch):
    monkeypatch.setattr(router_core, "anchor_backoff_cfg",
                        lambda: {"enabled": True, "base_s": 30.0,
                                 "max_s": 1800.0, "ttl_s": 3600.0})
    # Size cap: 300 distinct keys -> at most 256 survive.
    for i in range(300):
        router_core.record_anchor_backoff_failure("cap-sess", "task-%03d" % i)
    with router_core._PENDING_SWAP_LOCK:
        assert len(router_core._ANCHOR_FAIL_BACKOFF) <= router_core._ANCHOR_BACKOFF_MAX
    # TTL reap: one fresh entry + one ancient entry -> ancient is reaped on
    # the next record call (same write-path reap discipline as _SWAP_DONE).
    with router_core._PENDING_SWAP_LOCK:
        router_core._ANCHOR_FAIL_BACKOFF[("s8", "t8-old")] = {
            "fails": 1, "last_fail_ts": time.time() - 3700.0, "last_reason": ""}
    router_core.record_anchor_backoff_failure("s8", "t8-new")
    with router_core._PENDING_SWAP_LOCK:
        assert ("s8", "t8-old") not in router_core._ANCHOR_FAIL_BACKOFF
    # TTL-expired entries are not "active" even before reap.
    assert router_core.anchor_backoff_active("s8", "t8-old") is False


def test_anchor_backoff_active_count_for_status(monkeypatch):
    monkeypatch.setattr(router_core, "anchor_backoff_cfg",
                        lambda: {"enabled": True, "base_s": 30.0,
                                 "max_s": 1800.0, "ttl_s": 3600.0})
    assert router_core.anchor_backoff_active_count() == 0
    router_core.record_anchor_backoff_failure("s8c", "t8c")
    assert router_core.anchor_backoff_active_count() == 1
    # Expired-by-TTL entry doesn't count.
    with router_core._PENDING_SWAP_LOCK:
        router_core._ANCHOR_FAIL_BACKOFF[("s8d", "t8x")] = {
            "fails": 1, "last_fail_ts": time.time() - 3700.0, "last_reason": ""}
    assert router_core.anchor_backoff_active_count() == 1


# ---------------------------------------------------------------------------
# Test 9 — window escalation: exponential doubling capped at max_s
# ---------------------------------------------------------------------------


def test_window_exponential_capped(monkeypatch):
    monkeypatch.setattr(router_core, "anchor_backoff_cfg",
                        lambda: {"enabled": True, "base_s": 30.0,
                                 "max_s": 1800.0, "ttl_s": 3600.0})
    assert [router_core.anchor_backoff_window(n) for n in (1, 2, 3, 4, 5)] == \
        [30.0, 60.0, 120.0, 240.0, 480.0]
    assert router_core.anchor_backoff_window(20) == 1800.0  # capped


def test_consecutive_fails_grow_window(monkeypatch):
    monkeypatch.setattr(router_core, "anchor_backoff_cfg",
                        lambda: {"enabled": True, "base_s": 30.0,
                                 "max_s": 1800.0, "ttl_s": 3600.0})
    router_core.record_anchor_backoff_failure("s9", "t9")
    assert router_core.anchor_backoff_active("s9", "t9") is True
    # Second fail after the first window lapsed -> fails=2 -> 60s window.
    with router_core._PENDING_SWAP_LOCK:
        rec = router_core._ANCHOR_FAIL_BACKOFF[("s9", "t9")]
        rec["last_fail_ts"] = time.time() - 31.0  # first window lapsed
    router_core.record_anchor_backoff_failure("s9", "t9")
    with router_core._PENDING_SWAP_LOCK:
        assert router_core._ANCHOR_FAIL_BACKOFF[("s9", "t9")]["fails"] == 2
    # 31s after the second fail: inside window 2 (60s) -> still benched
    # (the old 30s window would have released — escalation is real).
    with router_core._PENDING_SWAP_LOCK:
        router_core._ANCHOR_FAIL_BACKOFF[("s9", "t9")]["last_fail_ts"] = \
            time.time() - 31.0
    assert router_core.anchor_backoff_active("s9", "t9") is True
    # After the 60s window lapses -> staging allowed again.
    with router_core._PENDING_SWAP_LOCK:
        router_core._ANCHOR_FAIL_BACKOFF[("s9", "t9")]["last_fail_ts"] = \
            time.time() - 61.0
    assert router_core.anchor_backoff_active("s9", "t9") is False


def test_backoff_never_raises_on_garbage(monkeypatch):
    """Fail-open contract: ledger helpers never raise."""
    router_core.record_anchor_backoff_failure("", "")
    router_core.record_anchor_backoff_failure(None, None)
    assert router_core.anchor_backoff_active("", "") is False
    router_core.clear_anchor_backoff("nope", "nope")  # absent key — no-op, no log
    cleared = [f for _, f in LOGGED if f.get("event_detail") == "anchor_backoff_cleared"]
    assert cleared == []


# ---------------------------------------------------------------------------
# Test 10 — calibrate_struggle parses the new backoff lines
# ---------------------------------------------------------------------------


def test_calibrate_struggle_backoff_blocked_lines(tmp_path, capsys):
    route = tmp_path / "uncensored-router-backoff.log"
    route.write_text("\n".join([
        "2026-09-05T10:00:00Z PRE event_detail=route_skipped lane=complexity "
        "reason=anchored_call_failed route_id=bb-1 session_id=sX",
        "2026-09-05T10:01:00Z PRE event_detail=anchor_backoff_blocked fails=2 "
        "backoff_s=60.0 task_id=bb session_id=sX",
        "2026-09-05T10:02:00Z PRE event_detail=anchor_backoff_blocked fails=2 "
        "backoff_s=60.0 task_id=bb session_id=sX",
        "2026-09-05T10:03:00Z PRE event_detail=anchor_backoff_cleared task_id=bb session_id=sX",
    ]) + "\n")
    events = calibrate_struggle.parse_route_logs([str(route)])
    assert len(events) == 4
    assert calibrate_struggle.anchor_backoff_blocked_count(events) == 2
    rc = calibrate_struggle.main([
        "--route-glob", str(route), "--agent-glob", str(tmp_path / "none.log"),
        "--profiles-root", str(tmp_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"anchor_backoff_blocked_total": 2' in out


def test_calibrate_struggle_existing_assertions_unchanged(tmp_path):
    """Spec test 10b: the v3.3.0 refire-candidate fixture still passes."""
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


# ---------------------------------------------------------------------------
# Config reader: defaults live on deploy (defect fix, not a feature)
# ---------------------------------------------------------------------------


def test_anchor_backoff_cfg_defaults(monkeypatch):
    monkeypatch.setattr(router_core, "_complexity_cfg", lambda: {})
    cfg = router_core.anchor_backoff_cfg()
    assert cfg == {"enabled": True, "base_s": 30.0, "max_s": 1800.0, "ttl_s": 3600.0}


def test_anchor_backoff_cfg_reads_block(monkeypatch):
    monkeypatch.setattr(router_core, "_complexity_cfg", lambda: {
        "anchor_backoff": {"enabled": False, "base_s": 5, "max_s": 100, "ttl_s": 60}})
    cfg = router_core.anchor_backoff_cfg()
    assert cfg["enabled"] is False
    assert cfg["base_s"] == 5.0 and cfg["max_s"] == 100.0 and cfg["ttl_s"] == 60.0
    # Garbage numeric values keep defaults.
    monkeypatch.setattr(router_core, "_complexity_cfg", lambda: {
        "anchor_backoff": {"base_s": "abc", "max_s": -1}})
    cfg2 = router_core.anchor_backoff_cfg()
    assert cfg2["base_s"] == 30.0 and cfg2["max_s"] == 1800.0


def test_router_status_reports_backoff_count(monkeypatch):
    import json as _json

    from hermes_router import anchor_chain as _ac
    from hermes_router import decision_head as _dh
    from hermes_router import router_tools

    router_core.record_anchor_backoff_failure("st-sess", "st-task")
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {}, raising=False)
    _chain = _test_anchor_chain()
    monkeypatch.setattr(_ac, "load_anchor_chain", lambda: _chain)
    monkeypatch.setattr(_ac, "today_spend", lambda: 0.0)
    monkeypatch.setattr(_dh, "status", lambda: {"backend": "heuristic"})
    payload = _json.loads(router_tools.router_status())
    assert payload["anchor_backoff_active"] == 1
    router_core.clear_anchor_backoff("st-sess", "st-task")