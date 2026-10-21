"""R34-19O: condense tool bodies in consult replay, keep every turn.

Measured: tool output is 71% of consult input mass and that mass is spread
evenly across the 24-turn window — so trimming the window removes context
proportionally instead of waste. These pins hold the condensation to its
contract: touch dumps only, never the newest tool result, never prose,
never a short message.
"""
from hermes_router import anchor_exec as ax


def _replay(msgs, monkeypatch, head=1200, tail=600, last_n=24):
    monkeypatch.setattr(ax, "_bounded_replay_cfg", lambda: {
        "enabled": True, "last_n_turns": last_n, "max_input_tokens": 40000,
        "summary_header": True, "tool_trim_head": head, "tool_trim_tail": tail,
    })
    return ax.bounded_replay({"messages": msgs})["messages"]


def test_a_large_tool_dump_is_condensed(monkeypatch):
    msgs = [{"role": "user", "content": "go"},
            {"role": "tool", "content": "X" * 8000},
            {"role": "assistant", "content": "done"},
            {"role": "tool", "content": "newest"}]
    out = _replay(msgs, monkeypatch)
    assert len(out[1]["content"]) < 2000
    assert "chars elided from tool result" in out[1]["content"]


def test_the_newest_tool_result_is_left_whole(monkeypatch):
    msgs = [{"role": "user", "content": "go"},
            {"role": "tool", "content": "A" * 8000},
            {"role": "assistant", "content": "done"},
            {"role": "tool", "content": "B" * 8000}]
    out = _replay(msgs, monkeypatch)
    assert out[3]["content"] == "B" * 8000
    assert len(out[1]["content"]) < 2000


def test_short_tool_messages_are_untouched(monkeypatch):
    msgs = [{"role": "user", "content": "go"},
            {"role": "tool", "content": "ok" * 100},
            {"role": "tool", "content": "newest"}]
    out = _replay(msgs, monkeypatch)
    assert out[1]["content"] == "ok" * 100


def test_prose_is_never_condensed(monkeypatch):
    long_answer = "y" * 20000
    msgs = [{"role": "user", "content": "go"},
            {"role": "assistant", "content": long_answer},
            {"role": "tool", "content": "newest"}]
    out = _replay(msgs, monkeypatch)
    assert out[1]["content"] == long_answer


def test_it_applies_to_sessions_shorter_than_the_window(monkeypatch):
    """The early-return skipped the trim for every conversation under
    last_n_turns — which is most of them, so the dumps still shipped in full."""
    msgs = [{"role": "user", "content": "go"},
            {"role": "tool", "content": "Z" * 9000},
            {"role": "tool", "content": "newest"}]
    out = _replay(msgs, monkeypatch, last_n=24)
    assert len(out[1]["content"]) < 2000


def test_head_and_tail_are_both_preserved(monkeypatch):
    body = "HEAD" + ("m" * 6000) + "TAIL"
    msgs = [{"role": "user", "content": "go"},
            {"role": "tool", "content": body},
            {"role": "tool", "content": "newest"}]
    out = _replay(msgs, monkeypatch)
    assert out[1]["content"].startswith("HEAD")
    assert out[1]["content"].endswith("TAIL")


def test_trimming_never_changes_the_message_count(monkeypatch):
    msgs = [{"role": "user", "content": "go"}]
    for i in range(6):
        msgs.append({"role": "tool", "content": "D" * 6000})
        msgs.append({"role": "assistant", "content": f"step {i}"})
    out = _replay(msgs, monkeypatch)
    assert len(out) == len(msgs)


def test_zero_knobs_disable_the_trim(monkeypatch):
    msgs = [{"role": "user", "content": "go"},
            {"role": "tool", "content": "X" * 8000},
            {"role": "tool", "content": "newest"}]
    out = _replay(msgs, monkeypatch, head=0, tail=0)
    assert out[1]["content"] == "X" * 8000


def test_input_cap_default_is_a_real_guard(monkeypatch):
    """120000 was effectively no cap; measured real max is ~27k."""
    monkeypatch.setattr(ax, "_bounded_replay_cfg", ax._bounded_replay_cfg)
    cfg = ax._bounded_replay_cfg()
    assert cfg["max_input_tokens"] <= 40000
