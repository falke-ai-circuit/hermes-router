"""R34-9: PRE anchor lane payload hygiene + empty-response recovery.

Live specimen (coder, 2026-10-20 20:32:27):
    anchor_route_failed reason=empty_response model=anthropic/claude-sonnet-5.5
    finish=stop tool_calls=False reasoning_chars=0 payload_keys=['model',
    'reasoning_effort'] max_tokens=None n_msgs=26
after anchor_memory_context_stripped chars_removed=8187.

Root cause is the PAYLOAD, not the model: the consult is a mutated copy of
the live conversation (system swap + tools popped + memory-context stripped
+ tool turns retained). Probed provider behaviour:
  * assistant `tool_calls` without its `tool_result` -> HTTP 400
    "tool_use ids were found without tool_result block";
  * empty/whitespace user message -> HTTP 400
    "user messages must have non-empty content".

Pins: 9a sanitize, 9b minimal retry, 9b failover walk (mock transport only —
no live calls).
"""
import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router import anchor_chain, anchor_exec  # noqa: E402


def _endpoint(model="primary-model"):
    ep = anchor_chain.parse_anchor_uri("openrouter://" + model, "primary")
    assert ep is not None
    return ep


def _history_26():
    """26-turn live-shaped history: tool traffic, empty assistant turns,
    empty user content, consecutive same-role turns."""
    msgs = [{"role": "system", "content": "OLD DNA persona prompt"}]
    msgs.append({"role": "user", "content": "THE ORIGINAL ASK"})
    for i in range(10):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": "call-%d" % i, "type": "function",
             "function": {"name": "terminal", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": "call-%d" % i,
                     "name": "terminal", "content": "stdout %d" % i})
        msgs.append({"role": "assistant", "content": "step %d done" % i})
        msgs.append({"role": "user", "content": "   "})
    msgs.append({"role": "user", "content": "continue the work"})
    msgs.append({"role": "user", "content": ""})
    msgs.append({"role": "assistant", "content": ""})
    msgs.append({"role": "user", "content": "FINAL ASK: consult this"})
    return msgs


# ---------------------------------------------------------------------------
# 9a — sanitize
# ---------------------------------------------------------------------------

def test_sanitize_26_turn_history_is_provider_safe(monkeypatch):
    monkeypatch.setattr(anchor_exec, "_anchor_chain_block",
                        lambda: {"max_history_turns": 12})
    out = anchor_exec.sanitize_consult_payload({"model": "m",
                                                "messages": _history_26()})
    msgs = out["messages"]
    assert msgs, "sanitized payload must not be empty"
    assert all(m["role"] != "tool" for m in msgs)
    assert all(str(m.get("content") or "").strip() for m in msgs)
    assert all("tool_calls" not in m for m in msgs)
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"].strip()
    # alternation: no two consecutive same-role turns
    for a, b in zip(msgs, msgs[1:]):
        assert a["role"] != b["role"]
    # history cap: last N stored turns (+ the single system frame)
    non_sys = [m for m in msgs if m["role"] != "system"]
    assert len(non_sys) <= 12
    assert sum(1 for m in msgs if m["role"] == "system") <= 1


def test_sanitize_keeps_last_real_ask(monkeypatch):
    monkeypatch.setattr(anchor_exec, "_anchor_chain_block", lambda: {})
    out = anchor_exec.sanitize_consult_payload(
        {"messages": [{"role": "user", "content": "a"},
                      {"role": "assistant", "content": ""},
                      {"role": "user", "content": "REAL ASK"}]})
    assert out["messages"][-1]["role"] == "user"
    assert out["messages"][-1]["content"].endswith("REAL ASK")


def test_sanitize_empty_is_fail_open():
    payload = {"model": "m", "messages": [{"role": "tool", "content": "x"}]}
    assert anchor_exec.sanitize_consult_payload(payload) == payload


# ---------------------------------------------------------------------------
# Mock transport
# ---------------------------------------------------------------------------

class _FakeCompletion:
    def __init__(self, content):
        self._content = content

    def model_dump(self):
        return {"choices": [{"finish_reason": "stop",
                             "message": {"role": "assistant",
                                         "content": self._content}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


class _FakeClient:
    """Empty content for the model's first `first_empty[model]` calls
    (or always when the model is in `always_empty`), else answer."""

    calls = []
    always_empty = set()
    first_empty = {}
    seen = {}
    answer = "CONSULT ANSWER"

    def __init__(self, **kw):
        self._kw = kw

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **payload):
        _FakeClient.calls.append(payload)
        model = str(payload.get("model") or "")
        n = _FakeClient.seen.get(model, 0)
        _FakeClient.seen[model] = n + 1
        if model in _FakeClient.always_empty or n < _FakeClient.first_empty.get(model, 0):
            return _FakeCompletion("   ")
        return _FakeCompletion(_FakeClient.answer)

    def close(self):
        pass


def _install(monkeypatch, always_empty=(), first_empty=None,
             answer="CONSULT ANSWER"):
    import openai as _openai
    _FakeClient.calls = []
    _FakeClient.always_empty = set(always_empty)
    _FakeClient.first_empty = dict(first_empty or {})
    _FakeClient.seen = {}
    _FakeClient.answer = answer
    monkeypatch.setattr(anchor_exec, "_resolve_key", lambda ep, **kw: "test-key")
    monkeypatch.setattr(_openai, "OpenAI", _FakeClient)
    monkeypatch.setattr(anchor_exec, "_anchor_chain_block", lambda: {})


# ---------------------------------------------------------------------------
# 9b stage (i) — minimal retry
# ---------------------------------------------------------------------------

def test_empty_response_retries_minimal_payload(monkeypatch, caplog):
    _install(monkeypatch, first_empty={"primary-model": 1},
             answer="RECOVERED ANSWER")
    with caplog.at_level("INFO"):
        content, cost, pt, ct = anchor_exec.anchored_call(
            _endpoint(), {"model": "flash", "max_tokens": 100,
                          "messages": [{"role": "user", "content": "ASK"}]})
    assert content == "RECOVERED ANSWER"
    assert any("anchor_consult_minimal_retry" in r.message
               for r in caplog.records)
    # 2 calls: full payload (empty) + minimal retry
    assert len(_FakeClient.calls) == 2
    retry = _FakeClient.calls[1]
    assert len(retry["messages"]) == 2
    assert retry["messages"][-1] == {"role": "user", "content": "ASK"}


# ---------------------------------------------------------------------------
# 9b stage (ii) — failover walk
# ---------------------------------------------------------------------------

def test_empty_response_walks_configured_failover(monkeypatch, caplog):
    _install(monkeypatch,
             first_empty={"primary-model": 2, "backup-one": 1},
             answer="FAILOVER ANSWER")
    monkeypatch.setattr(anchor_exec, "_anchor_chain_block", lambda: {
        "max_history_turns": 12,
        "failover": ["openrouter://backup-one", {"model": "backup-two"}]})
    with caplog.at_level("INFO"):
        content, _cost, _pt, _ct = anchor_exec.anchored_call(
            _endpoint(), {"model": "flash", "max_tokens": 100,
                          "messages": [{"role": "user", "content": "ASK"}]})
    assert content == "FAILOVER ANSWER"
    logs = [r.message for r in caplog.records]
    assert any("anchor_consult_failover" in m
               and "model_from=primary-model" in m
               and "model_to=backup-one" in m for m in logs)
    assert any("model_to=backup-two" in m for m in logs)
    assert [c["model"] for c in _FakeClient.calls] == [
        "primary-model", "primary-model", "backup-one", "backup-two"]


def test_default_failover_is_empty_and_terminal_failure_stays(monkeypatch,
                                                              caplog):
    """Default EMPTY failover = today's behavior: minimal retry only, then
    the visible terminal failure (None 4-tuple). Never silent."""
    _install(monkeypatch, always_empty={"primary-model"})
    with caplog.at_level("INFO"):
        out = anchor_exec.anchored_call(
            _endpoint(), {"model": "flash", "max_tokens": 100,
                          "messages": [{"role": "user", "content": "ASK"}]})
    assert out == (None, None, None, None)
    messages = [r.message for r in caplog.records]
    assert any("anchor_consult_minimal_retry" in m for m in messages)
    assert any("anchor_route_failed reason=empty_response" in m
               for m in messages)
    assert not any("anchor_consult_failover" in m for m in messages)
