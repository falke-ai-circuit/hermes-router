"""R26-3 — banner consistency on auto-routed POST renders
(live R25 battery WONT sessions ...c745e6e0 / ...ec54bec1 / ...21a0d4ce).

The declared shadow path parks its banner and the benign delivery edge
consumes + captures it — the on-demand turn (...b26912ab) delivered WITH
the banner. The auto-routed POST render path appended its banner to the
DELIVERED text only; the persisted row kept the pre-banner text (the
round-trip rewrite fired only when a parked banner merged), so the route
log showed debug_banner_emitted while the delivered record carried
banner=False. Fix: a final persisted==delivered capture at the render
delivery edge. Regression asserts the DELIVERED TEXT (the hook return),
not log lines, on both paths.
"""
import pytest

import hermes_router as plugin
from hermes_router import (canonical, config_access, debug_banner,
                           render_inbox, state, usage_ledger)

SID = "s-r26-3"

LOGGED = []

REFUSAL = ("No — I won't help with that request, and I won't pretend the "
           "reason is anything other than what it is.")


@pytest.fixture()
def _reset(monkeypatch, tmp_path):
    import hermes_router as _p
    from hermes_router import router_core

    router_core._test_reset()
    _p.state.clear()
    state.reset_turn_identity(SID)
    debug_banner._ANCHOR_BANNERS.clear()
    debug_banner._ANCHOR_TASKS.clear()
    debug_banner._ANCHOR_SEGS.clear()
    LOGGED.clear()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr(render_inbox, "_inbox_path",
                        lambda: str(tmp_path / "renders.jsonl"))
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"debug_banner": 1})
    monkeypatch.setattr(debug_banner, "_banner_section",
                        lambda: {"debug_banner": 1}, raising=True)
    monkeypatch.setattr(canonical, "commit_canonical_event",
                        lambda *a, **k: False)
    monkeypatch.setattr(canonical, "get_last_canonical_answer",
                        lambda sid: "")
    monkeypatch.setattr(plugin, "_flinch_reason_gate", lambda: False)
    monkeypatch.setattr(plugin, "_doctrine_verdict_enabled", lambda: False)
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(plugin, "_two_vote_enabled", lambda: False)
    yield LOGGED


def _drive_post_render(monkeypatch, refusal=REFUSAL, render="RENDER BODY"):
    """Fire on_transform_llm_output with a detected refusal and a mocked
    uncensored render. Returns the DELIVERED text (hook return)."""
    from hermes_router import router as router_mod

    monkeypatch.setattr(plugin.classifier, "scan_post",
                        lambda text, **k: ["refusal_phrases"])
    monkeypatch.setattr(plugin, "_post_patterns",
                        lambda: ["refusal_phrases"])
    monkeypatch.setattr(router_mod, "call",
                        lambda prompt, **kw: render)
    monkeypatch.setattr(plugin, "_banner_tokens_from_last_write",
                        lambda lane, sid: (10, 20, 0.0))
    monkeypatch.setattr(plugin, "_tap_task_identity",
                        lambda sid, model: ("task-r26", "task-r26"))
    monkeypatch.setattr(plugin, "_decision_wait_before_consume",
                        lambda budget=0.0: None)
    state.record_last_seen(SID, "the contested ask")
    out = plugin.on_transform_llm_output(
        response_text=refusal, session_id=SID, model="test-model",
        user_message="the contested ask")
    return out


def test_post_render_delivers_with_banner(_reset, monkeypatch):
    out = _drive_post_render(monkeypatch)
    assert out and "RENDER BODY" in out
    # DELIVERED text carries the provenance banner — not log lines.
    assert debug_banner.BANNER_HEAD in out


def test_post_render_banner_captured_to_persisted_row(_reset, monkeypatch):
    """persisted == delivered: the final capture rewrites the pre-banner
    persisted row to the banner text and logs banner_render_captured."""
    rewrites = []

    def _spy(session_id, refusal_text, delivered_text, **kw):
        rewrites.append((refusal_text, delivered_text))
        return True  # stubbed store: report the rewrite as landed

    monkeypatch.setattr(canonical, "rewrite_persisted_turn", _spy)
    out = _drive_post_render(monkeypatch)
    assert out and debug_banner.BANNER_HEAD in out
    assert rewrites, "round-trip rewrite must run"
    # The LAST rewrite pairs the pre-banner text with the banner text.
    last_refusal, last_delivered = rewrites[-1]
    assert debug_banner.BANNER_HEAD in last_delivered
    assert debug_banner.BANNER_HEAD not in last_refusal
    events = [f.get("event_detail") for _, f in _reset]
    assert "banner_render_captured" in events


def test_declared_path_delivery_edge_still_banners(_reset, monkeypatch):
    """The declared path's consume at the benign edge keeps appending the
    banner to the delivered body (the ...b26912ab contract holds)."""
    text = "a perfectly benign follow-up turn"
    debug_banner.park_anchor_banner(
        SID, debug_banner.format_banner(
            lane="shadow", trigger="shadow_declared", model="m",
            endpoint="", tokens_in=1, tokens_out=2, est_cost=0.0,
            latency_s=0.0, task_id="t", session_id=SID))
    out = plugin.on_transform_llm_output(
        response_text=text, session_id=SID, model="test-model",
        user_message="benign ask")
    assert out and debug_banner.BANNER_HEAD in out
