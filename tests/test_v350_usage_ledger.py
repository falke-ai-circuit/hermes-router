"""v3.5.0 Phase 1 tests — tokens ledger + taps (blueprint 5.2).

Covers: record/aggregate windows, usage-absent = never-record rule, ledger
failure isolation (a tap must never break the lane), and the three tap sites
(render/anchor/aux).
"""
import json
from collections import deque

import pytest

import hermes_router.usage_ledger as usage_ledger


@pytest.fixture(autouse=True)
def _isolate_tokens_ledger(tmp_path, monkeypatch):
    ledger = tmp_path / "tokens-home" / "hermes-router-tokens.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(usage_ledger, "_store_path", lambda: str(ledger))
    # Aux-lane module state (sliding call window + circuit breaker) is module
    # level; other test files in the full run fire real aux dispatches that
    # fill the window / open the breaker. Reset both layers per test.
    import hermes_router.semantic_classifier as _sc

    monkeypatch.setattr(_sc, "_CALL_TIMES", deque(), raising=False)
    monkeypatch.setattr(_sc, "_CONSECUTIVE_FAILURES", 0, raising=False)
    monkeypatch.setattr(_sc, "_BREAKER_OPENED_AT", None, raising=False)
    yield


# ---------------------------------------------------------------------------
# Ledger unit behavior
# ---------------------------------------------------------------------------


def test_record_tokens_happy_path():
    assert usage_ledger.record_tokens("render", "qwen-3-8-27b", "api_s1", 1820, 935, 0.000412, "route_fired") is True
    recs = usage_ledger.read_records()
    assert len(recs) == 1
    r = recs[0]
    assert r["lane"] == "render"
    assert r["model"] == "qwen-3-8-27b"
    assert r["session_id"] == "api_s1"
    assert r["input_tokens"] == 1820
    assert r["output_tokens"] == 935
    assert r["est_cost_usd"] == 0.000412
    assert r["detail"] == "route_fired"


def test_record_tokens_usage_absent_records_nothing():
    """D9 honesty rule: usage absent -> record NOTHING, never estimate."""
    assert usage_ledger.record_tokens("render", "m", "s", None, None, None, "route_fired") is False
    assert usage_ledger.read_records() == []


def test_record_tokens_partial_usage_records():
    assert usage_ledger.record_tokens("aux", "MiniMax-M3", "s", 100, None, 0.0, "stage2_classify") is True
    r = usage_ledger.read_records()[0]
    assert r["input_tokens"] == 100 and r["output_tokens"] == 0


def test_record_tokens_invalid_lane_rejected():
    assert usage_ledger.record_tokens("flash", "m", "s", 1, 1, 0.0) is False
    assert usage_ledger.read_records() == []


def test_ledger_corrupt_lines_skipped():
    path = usage_ledger._store_path()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": 1.0, "lane": "render", "model": "m", "session_id": "s",
                             "input_tokens": 5, "output_tokens": 6, "est_cost_usd": 0.0, "detail": "d"}) + "\n")
        fh.write("TORN LINE NOT JSON\n")
        fh.write("\n")
    assert len(usage_ledger.read_records()) == 1


def test_aggregate_windows_and_session_filter():
    import time as _t

    now = _t.time()
    usage_ledger.record_tokens("render", "m", "sA", 100, 50, 0.001, "d")
    path = usage_ledger._store_path()
    with open(path, "a", encoding="utf-8") as fh:
        # 6 days old: inside the 7d window, outside the 1d window
        fh.write(json.dumps({"ts": now - 6 * 86400, "lane": "render", "model": "m", "session_id": "sA",
                             "input_tokens": 10, "output_tokens": 5, "est_cost_usd": 0.0, "detail": "d"}) + "\n")
        # 8 days old: outside even the 7d window
        fh.write(json.dumps({"ts": now - 8 * 86400, "lane": "anchor", "model": "gpt", "session_id": "sC",
                             "input_tokens": 999, "output_tokens": 999, "est_cost_usd": 0.09,
                             "detail": "old"}) + "\n")
        # recent: inside the 1d window
        fh.write(json.dumps({"ts": now - 100, "lane": "anchor", "model": "gpt", "session_id": "sB",
                             "input_tokens": 700, "output_tokens": 300, "est_cost_usd": 0.02,
                             "detail": "consult"}) + "\n")
    recs = usage_ledger.read_records()
    today = usage_ledger.aggregate(recs, since_ts=now - 86400)
    assert today["render"]["input_tokens"] == 100  # 6d/8d records excluded
    assert today["anchor"]["input_tokens"] == 700  # 8d record excluded
    week = usage_ledger.aggregate(recs, since_ts=now - 7 * 86400 - 60)
    assert week["render"]["calls"] == 2  # fresh + 6d-old
    assert week["render"]["input_tokens"] == 110
    assert week["anchor"]["input_tokens"] == 700  # 8d-old excluded
    per_session = usage_ledger.aggregate(recs, session_id="sB")
    assert set(per_session) == {"anchor"}
    assert per_session["anchor"]["calls"] == 1


def test_estimate_cost_unpriced_model_is_zero():
    assert usage_ledger.estimate_cost("definitely-not-priced-model", 1000, 1000) == 0.0


# ---------------------------------------------------------------------------
# Tap 1: render lane (router.py)
# ---------------------------------------------------------------------------


def _fake_curl_response(monkeypatch, body: str):
    from hermes_router import router as _router

    class _Completed:
        stdout = body
        stderr = ""
        returncode = 0

    monkeypatch.setattr(_router.subprocess, "run", lambda *a, **kw: _Completed())
    monkeypatch.setattr(_router, "_read_key", lambda kf, ke="": "fake-key")


def test_render_tap_records_usage(monkeypatch):
    from hermes_router import router as _router

    body = json.dumps({
        "choices": [{"message": {"content": "RENDERED"}}],
        "usage": {"prompt_tokens": 1820, "completion_tokens": 935},
    })
    _fake_curl_response(monkeypatch, body)
    assert _router.call("prompt", session_id="api_rt1") == "RENDERED"
    recs = usage_ledger.read_records()
    assert len(recs) == 1 and recs[0]["lane"] == "render"
    assert (recs[0]["input_tokens"], recs[0]["output_tokens"]) == (1820, 935)
    assert recs[0]["session_id"] == "api_rt1"


def test_render_tap_usage_absent_records_nothing(monkeypatch):
    from hermes_router import router as _router

    body = json.dumps({"choices": [{"message": {"content": "RENDERED"}}]})
    _fake_curl_response(monkeypatch, body)
    assert _router.call("prompt", session_id="api_rt2") == "RENDERED"
    assert usage_ledger.read_records() == []


def test_render_tap_malformed_usage_not_recorded(monkeypatch):
    from hermes_router import router as _router

    body = json.dumps({
        "choices": [{"message": {"content": "R2"}}],
        "usage": {"prompt_tokens": "not-a-number", "completion_tokens": None},
    })
    _fake_curl_response(monkeypatch, body)
    assert _router.call("prompt") == "R2"
    assert usage_ledger.read_records() == []


def test_render_tap_ledger_failure_does_not_break_route(monkeypatch):
    from hermes_router import router as _router

    body = json.dumps({
        "choices": [{"message": {"content": "RENDERED"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    })
    _fake_curl_response(monkeypatch, body)

    def _boom(*a, **kw):
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(usage_ledger, "record_tokens", _boom)
    assert _router.call("prompt", session_id="api_rt3") == "RENDERED"


# ---------------------------------------------------------------------------
# Tap 2: anchor lane (anchor_exec.py)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, content, pt, ct, with_usage=True):
        self._content = content
        self._pt = pt
        self._ct = ct
        self._with_usage = with_usage
        self.choices = [{"message": {"content": content}}]

    def model_dump(self):
        dump = {"choices": [{"message": {"content": self._content}}]}
        if self._with_usage:
            dump["usage"] = {"prompt_tokens": self._pt, "completion_tokens": self._ct}
        return dump


def _fake_openai(monkeypatch, pt, ct, with_usage=True):
    import openai as _openai

    class _FakeClient:
        def __init__(self, **kw):
            self.chat = self
            self.completions = self

        def create(self, **kw):
            return _FakeResponse("PLAN OUTPUT", pt, ct, with_usage)

        def close(self):
            pass

    monkeypatch.setattr(_openai, "OpenAI", _FakeClient)


def _anchor_rec(monkeypatch, ep, mode):
    from hermes_router import router_core

    rec = {"task_id": "t1", "lane": "complexity", "mode": mode,
           "endpoint": ep, "route_id": "r1"}
    monkeypatch.setattr(router_core, "pending_model_swap", lambda sid: rec)
    monkeypatch.setattr(router_core, "clear_anchor_backoff", lambda *a, **kw: None)
    monkeypatch.setattr(router_core, "store_consult_result", lambda *a, **kw: None)
    return rec


def test_anchor_call_returns_tokens(monkeypatch):
    from hermes_router import anchor_chain, anchor_exec

    _fake_openai(monkeypatch, 4321, 876)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    ep = anchor_chain.parse_anchor_uri("openrouter://test/model-a", "primary")
    content, cost, pt, ct = anchor_exec.anchored_call(
        ep, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100})
    assert content == "PLAN OUTPUT"
    assert (pt, ct) == (4321, 876)
    assert cost is not None


def test_anchor_call_usage_absent_tokens_none(monkeypatch):
    from hermes_router import anchor_chain, anchor_exec

    _fake_openai(monkeypatch, 0, 0, with_usage=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    ep = anchor_chain.parse_anchor_uri("openrouter://test/model-a", "primary")
    content, cost, pt, ct = anchor_exec.anchored_call(
        ep, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100})
    assert content == "PLAN OUTPUT"
    assert pt is None and ct is None


def test_anchor_route_records_tokens_when_present(monkeypatch):
    from hermes_router import anchor_chain, anchor_exec, router_core

    _fake_openai(monkeypatch, 333, 444)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    ep = anchor_chain.parse_anchor_uri("openrouter://test/model-a", "primary")
    _anchor_rec(monkeypatch, ep, router_core.MODE_PLAN)
    outcome = anchor_exec.maybe_execute_anchored(
        "api_sY", {"messages": [{"role": "user", "content": "task"}], "max_tokens": 50})
    assert outcome and outcome[0] == "done"
    recs = usage_ledger.read_records()
    assert len(recs) == 1
    assert recs[0]["lane"] == "anchor"
    assert (recs[0]["input_tokens"], recs[0]["output_tokens"]) == (333, 444)
    assert recs[0]["session_id"] == "api_sY"
    assert recs[0]["detail"] == "frontier_plan"


def test_anchor_route_usage_absent_records_nothing(monkeypatch):
    from hermes_router import anchor_chain, anchor_exec, router_core

    _fake_openai(monkeypatch, 0, 0, with_usage=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    ep = anchor_chain.parse_anchor_uri("openrouter://test/model-a", "primary")
    _anchor_rec(monkeypatch, ep, router_core.MODE_CONSULT)
    outcome = anchor_exec.maybe_execute_anchored(
        "api_sZ", {"messages": [{"role": "user", "content": "task"}], "max_tokens": 50})
    assert outcome and outcome[0] == "done"
    assert usage_ledger.read_records() == []


def test_anchor_route_ledger_failure_does_not_break_consult(monkeypatch):
    from hermes_router import anchor_chain, anchor_exec, router_core

    _fake_openai(monkeypatch, 111, 222)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    ep = anchor_chain.parse_anchor_uri("openrouter://test/model-a", "primary")
    _anchor_rec(monkeypatch, ep, router_core.MODE_PLAN)

    def _boom(*a, **kw):
        raise RuntimeError("ledger disk failure")

    monkeypatch.setattr(usage_ledger, "record_tokens", _boom)
    outcome = anchor_exec.maybe_execute_anchored(
        "api_sX", {"messages": [{"role": "user", "content": "task"}], "max_tokens": 50})
    assert outcome and outcome[0] == "done"  # consult delivered despite ledger failure


# ---------------------------------------------------------------------------
# Tap 3: aux lane (semantic_classifier.py)
# ---------------------------------------------------------------------------


def test_aux_tap_records_usage(monkeypatch):
    from hermes_router import semantic_classifier as sc

    body = json.dumps({
        "choices": [{"message": {"content": "refusal"}}],
        "usage": {"prompt_tokens": 55, "completion_tokens": 7},
    })

    class _Completed:
        stdout = body
        stderr = ""
        returncode = 0

    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **kw: _Completed())
    monkeypatch.setattr(sc, "_resolve_key", lambda ep: "fake-key")
    verdict = sc.classify("ask", "response text", session_id="api_aux1")
    assert verdict == "refusal"
    recs = usage_ledger.read_records()
    assert len(recs) == 1
    assert recs[0]["lane"] == "aux"
    assert (recs[0]["input_tokens"], recs[0]["output_tokens"]) == (55, 7)
    assert recs[0]["session_id"] == "api_aux1"
    assert recs[0]["detail"] == "stage2_classify"


def test_aux_tap_usage_absent_records_nothing(monkeypatch):
    from hermes_router import semantic_classifier as sc

    body = json.dumps({"choices": [{"message": {"content": "deflection"}}]})

    class _Completed:
        stdout = body
        stderr = ""
        returncode = 0

    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **kw: _Completed())
    monkeypatch.setattr(sc, "_resolve_key", lambda ep: "fake-key")
    verdict = sc.classify("ask", "response text", session_id="api_aux2")
    assert verdict is not None
    assert usage_ledger.read_records() == []


def test_aux_tap_ledger_failure_does_not_break_classify(monkeypatch):
    from hermes_router import semantic_classifier as sc

    body = json.dumps({
        "choices": [{"message": {"content": "compliant"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })

    class _Completed:
        stdout = body
        stderr = ""
        returncode = 0

    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **kw: _Completed())
    monkeypatch.setattr(sc, "_resolve_key", lambda ep: "fake-key")

    def _boom(*a, **kw):
        raise RuntimeError("ledger gone")

    monkeypatch.setattr(usage_ledger, "record_tokens", _boom)
    assert sc.classify("ask", "fine response", session_id="api_aux3") == "compliant"