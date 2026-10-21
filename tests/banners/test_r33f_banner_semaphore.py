"""R33f-A — banner semaphore battery.

Live evidence (session 20261006_190903_c82cbbcc): a stale 19:35 frontier
banner leaked to 4 later turns and double-appended on shadow renders.
Fix contract (conductor brief, one commit with R33f):

  1. Park record scoped (session_id, event_seq), TTL = ONE delivery.
  2. Consume-once at the POST attach: a consumed banner whose attach did
     NOT land is DROPPED fail-loud (banner_semaphore_expired) — the
     R19.19/R19.21 re-park contract is SUPERSEDED (lesson 15: the latest
     binding frame is implemented; the earlier direction's tests are
     rewritten in place in test_r9_banner_delivery_seam.py,
     test_r19_19_dual_audit.py, test_r19_21_parity.py,
     test_p5_banner_lifecycle.py, test_rider20_fixes.py).
  3. Uncensored-render turns claim banner ownership BEFORE any anchor
     consume (banner_ownership_claimed) — no stale banner ever
     double-appends onto the render's own fresh banner.
  4. Wrong-session touch = silent drop (never another session's key).

No live render calls — router.call is mocked everywhere.
"""
import sys
from unittest import mock

import pytest

_P = "/opt/data/plugins"
if _P not in sys.path:
    sys.path.insert(0, _P)

import hermes_router as plugin  # noqa: E402
from hermes_router import (completion_audit, config_access, debug_banner,  # noqa: E402
                           render_inbox, route_gate, router_core, state,
                           usage_ledger)

SID = "s-r33f"


@pytest.fixture()
def env(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    debug_banner._ANCHOR_BANNERS.clear()
    debug_banner._ANCHOR_TASKS.clear()
    debug_banner._ANCHOR_SEGS.clear()
    debug_banner._PARK_RECORDS.clear()
    debug_banner._HELD_DECISIONS.clear()
    LOGGED = []
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
    monkeypatch.setattr(completion_audit, "audit_enabled", lambda: False)
    yield LOGGED
    debug_banner._PARK_RECORDS.clear()


def _banner(model="openai/gpt-6-astra-pro-flex"):
    return debug_banner.format_banner(
        lane="frontier-anchor", trigger="anchored", model=model,
        endpoint="openrouter.ai", tokens_in=500, tokens_out=200,
        est_cost=0.0753, latency_s=95, task_id="t1", session_id=SID,
        route_id="r1")


# ---------------------------------------------------------------------------
# 1. Park record scoped (session_id, event_seq)
# ---------------------------------------------------------------------------

def test_park_record_scoped_session_and_event_seq(env):
    debug_banner.park_anchor_banner(SID, _banner(), task_id="t1")
    rec = debug_banner.park_record(SID)
    assert rec.get("event_seq") and isinstance(rec["event_seq"], int)
    assert rec.get("parked_at") and rec.get("task_id") == "t1"
    # wrong-session touch: another session sees no record (silent)
    assert debug_banner.park_record("s-other") == {}
    debug_banner._PARK_RECORDS.clear()
    assert debug_banner.park_record(SID) == {}


def test_consume_pops_the_record_once(env):
    debug_banner.park_anchor_banner(SID, _banner())
    assert debug_banner.park_record(SID)
    got = debug_banner.consume_parked_banner(SID)
    assert "· router ·" in got
    assert debug_banner.park_record(SID) == {}          # record died too
    assert not debug_banner._ANCHOR_BANNERS.get(SID)     # slot died too
    # second consume: silent miss
    assert debug_banner.consume_parked_banner(SID) == ""


# ---------------------------------------------------------------------------
# 2. TTL one delivery — not-landed attach DROPPED, never re-parked
# ---------------------------------------------------------------------------

def test_not_landed_attach_dropped_fail_loud(env):
    """R33f-A supersedes R19.19/R19.21 re-park: one delivery total."""
    with mock.patch.object(debug_banner, "append_banner",
                           lambda base, b, prepend=False, _knob_checked=False: base):
        debug_banner.park_anchor_banner(SID, _banner())
        out = plugin.on_transform_llm_output(
            response_text="visible reply", session_id=SID, model="minimax-m3")
    # the banner did not land: pass-through contract (None -> the original
    # reply delivers untouched) — and the banner is GONE, not re-parked.
    assert out is None
    assert not debug_banner._ANCHOR_BANNERS.get(SID)   # NOT re-parked
    events = [f.get("event_detail") for _, f in env]
    assert "banner_park_dropped" in events
    # the stale-leak chain is dead: a later turn sees nothing
    out2 = plugin.on_transform_llm_output(
        response_text="next turn", session_id=SID, model="minimax-m3")
    assert "· router ·" not in (out2 or "next turn")


def test_capture_fallback_no_row_match_defers_one_delivery(env, monkeypatch,
                                                          tmp_path):
    """R34-2 (rewrites the R33f-A drop arm): a no-row-match on the PREFERRED
    edge DEFERS the banner under the SAME semaphore record instead of
    dropping it — the next legal delivery edge attaches it exactly once.
    The stale-leak class stays closed by the single record (same event_seq)
    and the deferral TTL, not by destroying the banner."""
    monkeypatch.setattr(debug_banner._LIFECYCLE, "banner_capture_fallback_wait",
                        lambda: 0.05)
    debug_banner.park_anchor_banner(SID, _banner())
    seq = debug_banner.park_record(SID).get("event_seq")
    with mock.patch.object(debug_banner._LIFECYCLE,
                           "_newest_persisted_assistant_row", return_value=""):
        debug_banner._LIFECYCLE._capture_fallback_watch(SID, _banner())
    assert debug_banner._ANCHOR_BANNERS.get(SID)       # deferred, not dropped
    assert debug_banner.park_record(SID).get("event_seq") == seq
    events = [f.get("event_detail") for _, f in env]
    assert "banner_capture_fallback_deferred" in events
    assert "banner_park_dropped" not in events


# ---------------------------------------------------------------------------
# 3. Uncensored-render ownership claim (before any anchor consume)
# ---------------------------------------------------------------------------

def test_ownership_claim_releases_slot_before_anchor_consume(env, monkeypatch):
    """A routed uncensored-render turn claims the parked banner: the slot is
    released WITHOUT attaching (banner_ownership_claimed), and the delivered
    body carries exactly ONE banner — the render's own."""
    from hermes_router import router as router_mod

    debug_banner.park_anchor_banner(SID, _banner())
    monkeypatch.setattr(plugin.classifier, "scan_post",
                        lambda text, **k: ["refusal_phrases"])
    monkeypatch.setattr(plugin, "_post_patterns",
                        lambda: ["refusal_phrases"])
    monkeypatch.setattr(router_mod, "call",
                        lambda prompt, **kw: "RENDER BODY")
    monkeypatch.setattr(plugin, "_banner_tokens_from_last_write",
                        lambda lane, sid: (10, 20, 0.0))
    monkeypatch.setattr(plugin, "_tap_task_identity",
                        lambda sid, model: ("task-r33f", "task-r33f"))
    monkeypatch.setattr(plugin, "_decision_wait_before_consume",
                        lambda budget=0.0: None)
    state.record_last_seen(SID, "the contested ask")
    out = plugin.on_transform_llm_output(
        response_text="I can't write that. held line text here",
        session_id=SID, model="test-model", user_message="the contested ask")
    assert not debug_banner._ANCHOR_BANNERS.get(SID)
    events = [f.get("event_detail") for _, f in env]
    assert "banner_ownership_claimed" in events
    # exactly ONE banner line in the delivery — the render's own, not the
    # stale parked one (double-append class killed)
    assert (out or "").count("· router ·") == 1
    assert "higher-self (frontier)" not in (out or "")


def test_claim_with_no_parked_banner_is_silent_noop(env):
    assert debug_banner.claim_banner_ownership(SID, edge="uncensored-render") is False
    assert [f.get("event_detail") for _, f in env
            if f.get("event_detail") == "banner_ownership_claimed"] == []


# ---------------------------------------------------------------------------
# 4. Wrong-session touch = silent drop
# ---------------------------------------------------------------------------

def test_wrong_session_consume_is_silent_drop(env):
    debug_banner.park_anchor_banner("s-live-a", _banner())
    # a consume for another session never touches s-live-a's slot
    assert debug_banner.consume_parked_banner("s-other-session") == ""
    assert debug_banner._ANCHOR_BANNERS.get("s-live-a")
    assert debug_banner.park_record("s-live-a")
    # and a delivery edge for the other session never carries the banner
    out = plugin.on_transform_llm_output(
        response_text="unrelated session reply", session_id="s-other-session",
        model="minimax-m3")
    assert "· router ·" not in (out or "unrelated session reply")
    assert debug_banner._ANCHOR_BANNERS.get("s-live-a")  # untouched


def test_one_delivery_landed_attach_consumes_once(env):
    """The landed path unchanged: one attach, one consume, second pass
    sees nothing (the single-shot guarantee R9 hole 2 / R19.21 kept)."""
    debug_banner.park_anchor_banner(SID, _banner())
    out = plugin.on_transform_llm_output(
        response_text="the agent's own visible reply", session_id=SID,
        model="minimax-m3")
    assert out and "· router ·" in out
    out2 = plugin.on_transform_llm_output(
        response_text="next turn", session_id=SID, model="minimax-m3")
    assert "· router ·" not in (out2 or "next turn")
