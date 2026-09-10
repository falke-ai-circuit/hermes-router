"""v3.6.1 completion-audit ruling tests (Goran 2026-09-08):
frontier consults ONLY on completed output (higher-self self-review);
PRE auto-fire and MID struggle escalation off by default; once-per-task."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from unittest import mock
from hermes_router import router_core as rc
from hermes_router import completion_audit as ca
from hermes_router import anchor_chain


PLAN_ASK = "Plan the architecture for a multi-region event pipeline with schema evolution."


def test_pre_default_off_no_consult():
    """PRE auto-fire removed: complex ask at L2 does NOT route by default."""
    rc._test_reset()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(rc, "_complexity_level", lambda: 2)
    monkey.setattr(rc, "_complexity_cfg", lambda: {"pre_mode": "off", "mid_mode": "off"})
    d = rc.dispatch(PLAN_ASK, session_id="s1", model="m")
    assert d.lane != rc.LANE_COMPLEXITY or d.mode == rc.MODE_FLASH_DIRECT
    monkey.undo()


def test_pre_route_still_available_optin():
    """pre_mode=route restores legacy PRE behavior."""
    rc._test_reset()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(rc, "_complexity_level", lambda: 2)
    monkey.setattr(rc, "_complexity_cfg", lambda: {"pre_mode": "route", "mid_mode": "off"})
    chain = anchor_chain.AnchorChainCfg(
        primary=anchor_chain.parse_anchor_uri("nous://openai/gpt-5.6-luna-pro", "primary"),
        judge=None, overflow="pass_through", daily_cap_usd=2.0, pricing={})
    monkey.setattr(rc.anchor_chain, "load_anchor_chain", lambda: chain)
    d = rc.dispatch(PLAN_ASK, session_id="s2", model="m")
    assert d.lane == rc.LANE_COMPLEXITY
    monkey.undo()


def test_anchor_override_survives_pre_off():
    """Manual 'anchor this' works regardless of pre_mode."""
    rc._test_reset()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(rc, "_complexity_level", lambda: 0)
    monkey.setattr(rc, "_complexity_cfg", lambda: {"pre_mode": "off", "mid_mode": "off"})
    chain = anchor_chain.AnchorChainCfg(
        primary=anchor_chain.parse_anchor_uri("nous://openai/gpt-5.6-luna-pro", "primary"),
        judge=None, overflow="pass_through", daily_cap_usd=2.0, pricing={})
    monkey.setattr(rc.anchor_chain, "load_anchor_chain", lambda: chain)
    d = rc.dispatch("Compare Redis vs CDN caching for 50k rps.\nanchor this",
                    session_id="s4", model="m")
    assert d.mode == rc.MODE_CONSULT
    monkey.undo()


def _reset_audit(monkeypatch, mode="complex"):
    ca._FIRED.clear()
    ca._PENDING.clear()
    monkeypatch.setattr(ca, "audit_mode", lambda: mode)


def test_audit_eligibility_gates(monkeypatch):
    _reset_audit(monkeypatch)
    ASK = "Plan this complex task please: rebuild the deployment pipeline."
    ok, why = ca.eligible("s", ASK, "x" * 600)
    assert ok and why == "ok"
    # once-per-task: same ask again -> rejected
    ca._mark_fired(ca._fire_marker_key("s", ASK, ""))
    ok, why = ca.eligible("s", ASK, "x" * 600)
    assert not ok and why == "already_fired_this_task"


def test_audit_mode_off_never_fires(monkeypatch):
    _reset_audit(monkeypatch, mode="off")
    ok, why = ca.eligible("s", "plan the architecture", "x" * 600)
    assert not ok and why == "mode_off"


def test_audit_mode_always_fires_on_any_ask(monkeypatch):
    _reset_audit(monkeypatch, mode="always")
    ok, why = ca.eligible("s", "send all the pdfs", "x" * 600)
    assert ok


def test_audit_short_response_skipped(monkeypatch):
    _reset_audit(monkeypatch)
    ok, why = ca.eligible("s", "plan the pipeline architecture", "short reply")
    assert not ok and why == "response_too_short"


def test_audit_once_across_fx_retries(monkeypatch):
    """The once-per-task marker is set BEFORE the consult: a fix-then-respond
    retry loop with the same task_id must not re-trigger."""
    _reset_audit(monkeypatch)
    monkeypatch.setattr(ca, "_has_pending", lambda sid: False)
    calls = []
    monkeypatch.setattr(ca, "_consult_meta", lambda *a, **k: calls.append(a))
    ASK2 = "Plan this complex task please: rebuild the deployment pipeline."
    ca.run_completion_audit("s", ASK2, "r" * 600, None, "m")
    ca.run_completion_audit("s", ASK2, "r2" * 300, None, "m")
    # same session+ask (task) — second call hits already_fired via eligible() path;
    # run_completion_audit itself trusts the caller's eligible() gate, so assert
    # the gate would refuse now:
    ok, why = ca.eligible("s", ASK2, "r" * 600, "m")
    assert not ok and why == "already_fired_this_task"


def test_verdict_stash_and_consume(monkeypatch):
    _reset_audit(monkeypatch)
    ca.stash_verdict("s9", "[HIGHER-SELF COMPLETION REFLECTION] findings")
    v = ca.consume_verdict("s9")
    assert v and "HIGHER-SELF" in v
    assert ca.consume_verdict("s9") is None  # one-shot


def test_audit_frame_higher_self_questions(monkeypatch):
    _reset_audit(monkeypatch)
    msgs = ca._audit_payload("the ask", "work digest", "the response", 2400)
    user_txt = msgs[-1]["content"]
    for q in ("optimal and elegant", "What did I miss", "different way",
              "sound", "not see and not try"):
        assert q in user_txt
    sys_txt = msgs[0]["content"]
    assert "higher" in sys_txt.lower()


def test_pre_route_now_orientation_not_plan(monkeypatch):
    """Revised 09-08 ruling: PRE complexity hit = orientation consult (advisory
    envelope), NOT whole-task plan/ownership."""
    rc._test_reset()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(rc, "_complexity_level", lambda: 2)
    monkey.setattr(rc, "_complexity_cfg", lambda: {"pre_mode": "route", "mid_mode": "off"})
    chain = anchor_chain.AnchorChainCfg(
        primary=anchor_chain.parse_anchor_uri("nous://openai/gpt-5.6-luna-pro", "primary"),
        judge=None, overflow="pass_through", daily_cap_usd=2.0, pricing={})
    monkey.setattr(rc.anchor_chain, "load_anchor_chain", lambda: chain)
    d = rc.dispatch(PLAN_ASK, session_id="s5", model="m")
    assert d.lane == rc.LANE_COMPLEXITY
    assert d.mode == rc.MODE_CONSULT
    assert d.orientation is True
    assert d.reason == "complexity_orientation"
    monkey.undo()


def test_orientation_frame_in_payload():
    """Orientation swaps rewrite the last user message with the brief frame."""
    from hermes_router import anchor_exec as ax
    rec = {"orientation": True, "mode": "consult", "task_id": "t", "route_id": "r"}
    kw = {"messages": [{"role": "user", "content": "the ask"}], "model": "m"}
    monkey = pytest.MonkeyPatch()
    called = {}
    def fake_call(endpoint, payload, timeout=300):
        called["msgs"] = payload["messages"]
        return ("BRIEF", 0.01, 10, 5)
    monkey.setattr(ax, "anchored_call", fake_call)
    monkey.setattr(ax, "bounded_replay", lambda x: x)
    rec = {**rec, "endpoint": mock.Mock(model="m", base_url="u", api_key_env="K")}
    monkey.setattr(rc, "pending_model_swap", lambda sid: rec)
    monkey.setattr(ax, "_resolve_key", lambda e: "test-key")
    monkey.setattr(ax.anchor_chain, "load_anchor_chain", lambda: mock.Mock(
        endpoint_for=lambda role: rec["endpoint"],
        pricing={}, daily_cap_usd=2.0))
    monkey.setattr(ax.anchor_chain, "cap_check", lambda c, e: (True, 0.0, 0.01))
    monkey.setattr(ax, "estimate_tokens_from_payload", lambda p: (10, 10))
    monkey.setattr(ax, "_resolve_key", lambda endpoint: "test-key")
    monkey.setattr(ax.anchor_chain, "estimate_call_cost", lambda *a, **k: 0.01)
    monkey.setattr(ax, "usage_ledger", mock.Mock(), raising=False)
    out = ax.maybe_execute_anchored("sx", kw)
    assert out and out[0] == "done"
    txt = called["msgs"][-1]["content"]
    assert "ORIENTATION BRIEF" in txt and "failure" in txt and "the ask" in txt.lower()
    monkey.undo()
