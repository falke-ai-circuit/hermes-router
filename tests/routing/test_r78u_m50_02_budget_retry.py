"""R78U M50-02 — the budget bug fixes (F1 bounded retry, F2 honest counter).

Root cause (M50-01 diag, binding): reasoning_effort=max spends the SAME
max_tokens allowance as the visible answer. Live: 17,510 completion tokens
produced 3,052 chars against a 23,585 cap; when the thinking runs longer than
the cap the call returns HTTP 200, finish=length, EMPTY content, and the router
logged `anchor_route_failed reason=empty_response` — five dead sessions, one
budget bug.

F1  a finish=length + empty-content response retries ONCE with headroom above
    the observed thinking peak, same payload, same reasoning setting. One
    retry, never a loop: a second empty response does not buy a third call.
F2  the "anchored" counter increments at OUTCOME time, not fire time — a route
    that produced nothing is `skipped`, never also `anchored`.

No policy change here: reasoning_effort is untouched, no cap is lowered, no
provider config is read or written.
"""
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import (anchor_chain, anchor_exec, decision,  # noqa: E402
                           debug_banner as DB, render_inbox, router_core,
                           router_tools, route_gate, usage_ledger)
from hermes_router.features.banners import turn_record as TR  # noqa: E402

SID = "s-m50-02"

# The live shape: 23,585 cap, 17,510 tokens of thinking before the answer.
LIVE_CAP = 23585


# ---------------------------------------------------------------------------
# F1 — mock transport
# ---------------------------------------------------------------------------

def _endpoint(model="claude-sonnet-5.5"):
    ep = anchor_chain.parse_anchor_uri("openrouter://" + model, "primary")
    assert ep is not None
    return ep


class _Completion:
    def __init__(self, content, finish_reason):
        self._content = content
        self._finish = finish_reason

    def model_dump(self):
        return {"choices": [{"finish_reason": self._finish,
                             "message": {"role": "assistant",
                                         "content": self._content}}],
                "usage": {"prompt_tokens": 2400, "completion_tokens": 17510}}


class _FakeClient:
    """Answers nothing while max_tokens is below `threshold`, then answers.

    `always_empty=True` models the second failure: the larger allowance is
    still exhausted by the thinking, so the retry comes back empty too.
    """

    calls = []
    threshold = LIVE_CAP + 1
    always_empty = False
    answer = "RECOVERED ADVISORY"

    def __init__(self, **kw):
        pass

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **payload):
        _FakeClient.calls.append(dict(payload))
        if _FakeClient.always_empty:
            return _Completion("   ", "length")
        mt = int(payload.get("max_tokens") or 0)
        if mt < _FakeClient.threshold:
            return _Completion("", "length")
        return _Completion(_FakeClient.answer, "stop")

    def close(self):
        pass


@pytest.fixture()
def f1_install(monkeypatch):
    import openai as _openai

    _FakeClient.calls = []
    _FakeClient.always_empty = False
    _FakeClient.answer = "RECOVERED ADVISORY"
    monkeypatch.setattr(anchor_exec, "_resolve_key", lambda ep, **kw: "test-key")
    monkeypatch.setattr(_openai, "OpenAI", _FakeClient)
    monkeypatch.setattr(anchor_exec, "_anchor_chain_block", lambda: {})
    # reasoning is ON for this lane: the retry must keep it, not strip it.
    monkeypatch.setattr(
        anchor_exec, "_anchor_chain_block",
        lambda: {"max_history_turns": 12, "reasoning_effort": "max"})
    return _FakeClient


def _consult_payload(cap=LIVE_CAP):
    return {"model": "flash", "max_tokens": cap,
            "messages": [{"role": "system", "content": "persona"},
                         {"role": "user", "content": "THE CONSULT ASK"}]}


def test_f1a_finish_length_retries_once_with_larger_cap(f1_install, caplog):
    """F1a: one empty finish=length response -> exactly ONE retry, larger cap,
    recovered content, and the retry keeps the same reasoning setting."""
    with caplog.at_level("INFO"):
        content, cost, pt, ct = anchor_exec.anchored_call(
            _endpoint(), _consult_payload())

    assert content == "RECOVERED ADVISORY"
    assert len(f1_install.calls) == 2, f1_install.calls

    first, retry = f1_install.calls
    assert first["max_tokens"] == LIVE_CAP
    assert retry["max_tokens"] > LIVE_CAP
    # headroom above the observed 17.5k thinking peak, not a token bump
    assert retry["max_tokens"] >= LIVE_CAP + 16384
    # same question, same reasoning setting — only the allowance changed
    assert retry["messages"] == first["messages"]
    assert retry.get("reasoning_effort") == first.get("reasoning_effort") == "max"

    logs = [r.message for r in caplog.records]
    assert any("anchor_budget_retry " in m and "%s" not in m
               and str(retry["max_tokens"]) in m for m in logs), logs
    assert any("anchor_budget_retry_recovered" in m for m in logs), logs


def test_f1b_second_empty_does_not_trigger_a_third_attempt(f1_install, caplog):
    """F1b: bounded. The retry itself comes back finish=length+empty -> no
    third budget attempt and no re-sent minimal retry (it was measured to fail
    identically); the call ends as the same visible terminal failure."""
    f1_install.always_empty = True
    with caplog.at_level("INFO"):
        out = anchor_exec.anchored_call(_endpoint(), _consult_payload())

    assert out == (None, None, None, None)
    assert len(f1_install.calls) == 2, [c["max_tokens"] for c in f1_install.calls]
    logs = [r.message for r in caplog.records]
    assert any("anchor_budget_retry_empty" in m for m in logs), logs
    # the minimal-payload arm is not paid for after a budget retry
    assert not any("anchor_consult_minimal_retry" in m for m in logs), logs


def test_f1b_ceiling_reached_skips_retry(monkeypatch, f1_install, caplog):
    """An allowance already at the model ceiling cannot be raised: the retry is
    skipped and logged rather than sending a value the endpoint rejects."""
    assert anchor_exec._budget_retry_max_tokens(64000, "claude-sonnet-5.5") == 64000
    f1_install.always_empty = True
    with caplog.at_level("INFO"):
        anchor_exec.anchored_call(_endpoint(), _consult_payload(cap=64000))
    logs = [r.message for r in caplog.records]
    assert any("anchor_budget_retry_skipped" in m and "reason=ceiling" in m
               for m in logs), logs


def test_f1_non_length_empty_still_uses_legacy_recovery(f1_install, caplog):
    """A finish=stop empty is NOT a budget failure — the R34-9b minimal retry
    arm must still own it, byte-for-byte as before."""
    class _StopEmpty(_FakeClient):
        def create(self, **payload):
            _FakeClient.calls.append(dict(payload))
            if len(_FakeClient.calls) == 1:
                return _Completion("", "stop")
            return _Completion("LEGACY RECOVERED", "stop")

    import openai as _openai
    f1_install.calls = []
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(_openai, "OpenAI", _StopEmpty)
        with caplog.at_level("INFO"):
            content, _c, _p, _t = anchor_exec.anchored_call(
                _endpoint(), _consult_payload())
    finally:
        monkey.undo()
    assert content == "LEGACY RECOVERED"
    logs = [r.message for r in caplog.records]
    assert any("anchor_consult_minimal_retry" in m for m in logs), logs
    assert not any("anchor_budget_retry " in m for m in logs), logs


# ---------------------------------------------------------------------------
# F2 — the anchored counter is an outcome, not a fire
# ---------------------------------------------------------------------------

@pytest.fixture()
def turn_reset(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    plugin.state.reset_turn_identity(SID)
    TR.reset()
    route_gate.clear_turn_claims()
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr(render_inbox, "_inbox_path",
                        lambda: str(tmp_path / "renders.jsonl"))
    monkeypatch.setattr(DB, "_banner_section", lambda: {"debug_banner": 0},
                        raising=True)
    return tmp_path


def _silence_route_log(monkeypatch):
    monkeypatch.setattr(plugin, "_log_route",
                        lambda event, event_detail="", **kw: None)


def _counters():
    return dict(router_tools._COUNTERS)


def _request(text):
    return {"messages": [{"role": "user", "content": text}],
            "model": "z-ai/glm-5.3-flash"}


CONSULT_ASK = ("ask your higher self: is the two-pass dispatcher refactor "
               "the right long-term call for the fleet?")


def test_f2_fire_time_does_not_increment_anchored(turn_reset, monkeypatch):
    """The PRE seam stages the swap — no result exists yet, so the counter must
    not move. This is the defect: the increment used to live here."""
    _silence_route_log(monkeypatch)
    before = _counters()["anchored"]
    req = _request(CONSULT_ASK)
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)
    assert router_core.peek_pending_swap(SID) is not None, "consult must stage"
    assert _counters()["anchored"] == before


def test_f2_failed_anchor_counts_skipped_not_anchored(turn_reset, monkeypatch):
    """A route that produced nothing is skipped — never anchored."""
    _silence_route_log(monkeypatch)
    monkeypatch.setattr(anchor_exec, "anchored_call",
                        lambda ep, kw: (None, None, None, None))
    before = _counters()
    req = _request(CONSULT_ASK)
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)
    plugin.on_llm_execution(request=_request(CONSULT_ASK),
                            next_call=lambda r: "EXEC", session_id=SID)
    after = _counters()
    assert after["anchored"] == before["anchored"], (
        "an empty/failed anchor must not increment anchored")
    assert after["skipped"] == before["skipped"] + 1


def test_f2_successful_anchor_increments_anchored(turn_reset, monkeypatch):
    """A delivered envelope is the evidence: the counter moves exactly once."""
    _silence_route_log(monkeypatch)
    monkeypatch.setattr(anchor_exec, "anchored_call",
                        lambda ep, kw: ("STUB ORIENTATION BRIEF", 0.001, 100, 50))
    before = _counters()
    req = _request(CONSULT_ASK)
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)
    assert _counters()["anchored"] == before["anchored"]
    plugin.on_llm_execution(request=_request(CONSULT_ASK),
                            next_call=lambda r: "EXEC", session_id=SID)
    after = _counters()
    assert after["anchored"] == before["anchored"] + 1
    assert after["skipped"] == before["skipped"]
