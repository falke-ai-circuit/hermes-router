"""v3.4.1 tests: struggle L1 gate + bounded replay."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest import mock
import pytest
from hermes_router import router_core as rc
from hermes_router import anchor_exec as ax


# ---- struggle gate ----

def test_struggle_no_auto_flagship_at_l1(monkeypatch):
    monkeypatch.setattr(rc, "_complexity_level", lambda: 1)
    monkeypatch.setattr(rc, "struggle_verdict", lambda t, u: (True, "user_struggle_signal"))
    monkeypatch.setattr(rc, "_lane_enabled", lambda lane: True)
    monkeypatch.setattr(rc.anchor_chain, "load_anchor_chain", lambda: mock.Mock())
    d = rc.dispatch("help me", session_id="s", model="m")
    assert d.mode != rc.MODE_OWNERSHIP, "L1 must not auto-fire flagship on struggle"


def test_struggle_auto_flagship_at_l2(monkeypatch):
    monkeypatch.setattr(rc, "_complexity_level", lambda: 2)
    monkeypatch.setattr(rc, "struggle_verdict", lambda t, u: (True, "user_struggle_signal"))
    monkeypatch.setattr(rc, "_lane_enabled", lambda lane: True)
    ep = mock.Mock()
    ep.model = "openai/gpt-5.6-luna-pro"
    monkeypatch.setattr(rc.anchor_chain, "load_anchor_chain",
                        lambda: mock.Mock(endpoint_for=lambda role: ep))
    d = rc.dispatch("help me", session_id="s", model="m")
    assert d.mode == rc.MODE_OWNERSHIP, "L2+ auto-escalation preserved"


# ---- bounded replay ----

def _msg(role, n):
    return {"role": role, "content": f"{role} message {n} " + "x" * 200}


def test_bounded_replay_trims_long_convo():
    msgs = [{"role": "system", "content": "sys"}] + [_msg("user", i) for i in range(50)] \
        + [_msg("assistant", i) for i in range(50)]
    out = ax.bounded_replay({"messages": msgs, "model": "m"})
    assert len(out["messages"]) <= 12 + 2 + 1
    assert out["messages"][-1]["content"].startswith("assistant")


def test_bounded_replay_keeps_short_convo_untouched():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "ask"}]
    out = ax.bounded_replay({"messages": msgs, "model": "m"})
    assert out["messages"] == msgs


def test_bounded_replay_disabled_full(monkeypatch):
    monkeypatch.setattr(ax, "_bounded_replay_cfg",
                        lambda: {"enabled": False, "last_n_turns": 12,
                                 "max_input_tokens": 120000, "summary_header": True})
    msgs = [{"role": "user", "content": str(i)} for i in range(100)]
    out = ax.bounded_replay({"messages": msgs, "model": "m"})
    assert len(out["messages"]) == 100


def test_bounded_replay_header_has_ask():
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "ORIGINAL ASK HERE"},
            {"role": "assistant", "content": "resp"}] \
        + [_msg("user", i) for i in range(40)]
    out = ax.bounded_replay({"messages": msgs, "model": "m"})
    hdr = [m for m in out["messages"] if m.get("role") == "system" and "context note" in m.get("content", "")]
    assert hdr and "ORIGINAL ASK HERE" in hdr[0]["content"]