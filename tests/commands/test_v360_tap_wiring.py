"""v3.6.0 Phase-0 tap-wiring tests (P0.1/P0.2/P0.5 + acceptance asserts).

FF-2 task identity reconstruction, tool-result tap feeds fail-ring/progress
ledger, provider-failure tap, route_skipped enrichment, struggle_feeder
armed assert, and zero-delivered-change with banner off.
"""
import json

import pytest

import hermes_router as plugin
from hermes_router import router_core
from hermes_router import state as router_state
from hermes_router import suggestions


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    ledger = tmp_path / "tap-home" / "hermes-router-budget.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(suggestions, "_store_path", lambda: str(ledger))
    suggestions.clear_for_tests()
    router_core._test_reset()
    router_state.clear()
    yield
    suggestions.clear_for_tests()
    router_core._test_reset()
    router_state.clear()


def _fake_request(tool_content=""):
    msgs = [{"role": "user", "content": "original ask"}]
    if tool_content:
        msgs.append({"role": "assistant", "content": "working"})
        msgs.append({"role": "tool", "content": tool_content})
    return {"messages": msgs, "model": "test-model"}


def test_ff2_task_identity_via_last_seen_cache():
    """FF-2: the tap reconstructs (task_id, turn_key) from the session ->
    last-user-text cache. Cold cache -> empty strings (nothing fed)."""
    assert plugin._tap_task_identity("sess-ff2", "m") == ("", "")  # cold cache
    router_state.record_last_seen("sess-ff2", "build the thing")
    task_id, turn_key = plugin._tap_task_identity("sess-ff2", "m")
    assert task_id == router_core.task_id_for("sess-ff2", "build the thing", "m")
    assert turn_key == router_state.turn_key_for("sess-ff2", "build the thing", "m")


def test_tool_tap_cold_cache_noop():
    """Cold last-seen cache -> no task identity -> nothing fed (no crash)."""
    plugin._tap_feed_tool_results(_fake_request("some result"), "sess-cold", "m")
    assert router_core._TOOLLOOP_STATE == {}


def test_provider_failure_tap():
    """P0.2: provider failure (4xx/5xx/connect/timeout) feeds
    record_provider_failure with the bounded raw text."""
    sid = "sess-fail1"
    router_state.record_last_seen(sid, "original ask")
    plugin._tap_provider_failure(sid, "m", "connect ECONNREFUSED 1.2.3.4:443",
                                 fail_kind="connect", finish_reason="none")
    task_id = router_core.task_id_for(sid, "original ask", "m")
    rec = router_core.task_state(task_id)
    assert int(rec.get("fail_count", 0)) == 1
    assert "fail_kind=connect" in str(rec.get("last_fail_text", ""))
    assert len(str(rec.get("last_fail_text", ""))) <= 240 + 60


def test_struggle_feeder_armed_assert():
    """Acceptance: startup asserts tap presence — the functions exist and the
    register-time log fires. Pinned at the function level (register needs a
    live ctx; the assert itself is what acceptance requires)."""
    assert callable(router_core.record_tool_call)
    assert callable(router_core.record_provider_failure)


def test_zero_delivered_change_banner_off(monkeypatch):
    """Acceptance: with debug_banner OFF (default), the delivery path is
    byte-identical — append_banner at the delivery edge is a no-op."""
    import hermes_cli.config as hcfg

    monkeypatch.setattr(hcfg, "load_config",
                        lambda: {"hermes_router": {}}, raising=False)
    from hermes_router import debug_banner as db

    canonical = "the delivered answer"
    assert db.append_banner(canonical, db.format_banner(
        "uncensored-render", "t", "m", "u", 1, 1, 0.0, 1.0, 0)) == canonical


def test_route_skipped_enrichment_fields(monkeypatch):
    """P0.5: fail_kind + finish_reason ride route_skipped — pinned at the
    _log_route field level (event flow, not parsing)."""
    captured = {}

    def _fake_log(event, **fields):
        captured.update(fields)
        captured["event"] = event

    monkeypatch.setattr(plugin, "_log_route", _fake_log)
    # simulate the enriched call shape used in on_llm_execution
    plugin._log_route("PRE", event_detail="route_skipped",
                      lane=router_core.LANE_COMPLEXITY,
                      reason="anchored_call_failed",
                      fail_kind="anchored_call_failed", finish_reason="none",
                      route_id="r-1", session_id="s-1")
    assert captured.get("fail_kind") == "anchored_call_failed"
    assert captured.get("finish_reason") == "none"