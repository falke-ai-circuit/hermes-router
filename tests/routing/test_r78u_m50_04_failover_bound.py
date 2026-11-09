"""R78U M50-04 — the failover arm is inside the budget bound.

Defect this pins (reviewer-measured on the deployed config, not inference):
bb2cc0f bounded the budget retry but the empty-content path still fell through
to `_recover_empty_consult`, whose failover walk re-sends the ORIGINAL
allowance. With `anchor_chain.failover: [z-ai/glm-5.3]` a length-empty consult
made THREE provider calls (2 primary + 1 glm at the original 23,585), so C1's
"no path to a third call" was false and the third call was spend without a
budget. The M50-02 test could not see it: its fixture returned a chain block of
`{}` — no failover list.

Choice made: (b) skip_failover when the budget retry fired. The failover call
under (a) would carry the raised allowance, but the retry exists to buy
headroom for THIS endpoint's thinking; the failover arm is a different model
whose outcome cannot be probed without live spend, and the C1 bound is only
restored by not making the call. The fixture here is deliberately NON-EMPTY:
every case asserts the call count AND each call's model + max_tokens.

Second item: `_length_exhausted` (gates the minimal arm) is set by the finish
reason, `_budget_retry_paid` (gates the failover arm) only when the retry was
actually issued. At the ceiling no retry is paid, so the failover arm still
runs — the pre-existing arm, at the ceiling allowance the primary just failed
at, never raised. The minimal arm stays skipped on the length path in both
cases because a minimal retry at a proven-insufficient allowance is the same
dead call.
"""
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router import anchor_chain, anchor_exec  # noqa: E402

LIVE_CAP = 23585
CEILING = 64000
FAILOVER_MODEL = "z-ai/glm-5.3"


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
    """Scripted transport: answer on call N (1-based), empty otherwise.

    `answer_on` = call index that returns content (None = never answers).
    `finish` = finish reason for the empty responses.
    """

    calls = []
    answer_on = None
    finish = "length"
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
        if _FakeClient.answer_on == len(_FakeClient.calls):
            return _Completion(_FakeClient.answer, "stop")
        return _Completion("   ", _FakeClient.finish)

    def close(self):
        pass


@pytest.fixture()
def failover_install(monkeypatch):
    import openai as _openai

    _FakeClient.calls = []
    _FakeClient.answer_on = None
    _FakeClient.finish = "length"
    monkeypatch.setattr(anchor_exec, "_resolve_key", lambda ep, **kw: "test-key")
    monkeypatch.setattr(_openai, "OpenAI", _FakeClient)
    # NON-EMPTY failover — the whole point of this unit's fixture.
    monkeypatch.setattr(
        anchor_exec, "_anchor_chain_block",
        lambda: {"max_history_turns": 12, "reasoning_effort": "max",
                 "failover": [{"model": FAILOVER_MODEL}]})
    return _FakeClient


def _endpoint(model="claude-sonnet-5.5"):
    ep = anchor_chain.parse_anchor_uri("openrouter://" + model, "primary")
    assert ep is not None
    return ep


@pytest.fixture()
def retry_log(monkeypatch, tmp_path):
    """M50-07: the budget-retry counters live in the CONFIGURED route log."""
    from hermes_router.core import telemetry

    path = tmp_path / "uncensored-router.log"
    monkeypatch.setattr(telemetry, "_log_path", lambda: str(path))
    monkeypatch.setattr(telemetry, "_log_routes", lambda: True)
    return path


def _payload(cap=LIVE_CAP):
    return {"model": "flash", "max_tokens": cap,
            "messages": [{"role": "system", "content": "persona"},
                         {"role": "user", "content": "THE CONSULT ASK"}]}


def _calls(client):
    return [(c.get("model"), c.get("max_tokens")) for c in client.calls]


def _text(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_length_empty_with_failover_makes_exactly_two_calls(
        failover_install, retry_log, caplog):
    """(i) length+empty with a live failover list: the retry is the last call.

    Two provider calls, both on the primary, 23585 then 47170. The failover
    walk is not entered — that is the C1 bound, and it is asserted on the call
    list, not on a log line.
    """
    with caplog.at_level("INFO"):
        out = anchor_exec.anchored_call(_endpoint(), _payload())

    assert out == (None, None, None, None)
    assert _calls(failover_install) == [
        ("claude-sonnet-5.5", LIVE_CAP),
        ("claude-sonnet-5.5", LIVE_CAP * 2),
    ], _calls(failover_install)
    text = _text(retry_log)
    assert "anchor_budget_retry_empty model=" in text, text
    logs = [r.message for r in caplog.records]
    assert any("anchor_consult_failover_skipped" in m
               and "reason=budget_retry" in m for m in logs), logs
    assert not any("anchor_consult_failover model_from" in m for m in logs), logs
    assert not any(FAILOVER_MODEL in c.get("model", "") for c in failover_install.calls)


def test_length_empty_at_ceiling_makes_no_retry_and_one_failover(
        failover_install, retry_log, caplog):
    """(ii) length+empty at the ceiling: no retry is affordable, so no retry is
    paid — and the flag that gates the failover arm stays down.

    Arms asserted: primary at 64000, then the PRE-EXISTING failover arm at the
    same 64000 (never above the ceiling). No minimal retry, no raised value.
    """
    assert anchor_exec._budget_retry_max_tokens(CEILING,
                                                "claude-sonnet-5.5") == CEILING
    with caplog.at_level("INFO"):
        out = anchor_exec.anchored_call(_endpoint(), _payload(cap=CEILING))

    assert out == (None, None, None, None)
    assert _calls(failover_install) == [
        ("claude-sonnet-5.5", CEILING),
        (FAILOVER_MODEL, CEILING),
    ], _calls(failover_install)
    assert all(int(c.get("max_tokens") or 0) <= CEILING
               for c in failover_install.calls)
    text = _text(retry_log)
    assert any("anchor_budget_retry_skipped model=" in ln
               and "reason=ceiling" in ln for ln in text.splitlines()), text
    logs = [r.message for r in caplog.records]
    assert not any("anchor_consult_minimal_retry" in m for m in logs), logs


def test_non_length_empty_keeps_legacy_minimal_arm(failover_install, retry_log,
                                                   caplog):
    """(iii) finish=stop empty is not a budget failure: the R34-9b minimal arm
    owns it unchanged — same allowance, recovered before any failover."""
    failover_install.finish = "stop"
    failover_install.answer_on = 2
    with caplog.at_level("INFO"):
        content, _c, _p, _t = anchor_exec.anchored_call(_endpoint(), _payload())

    assert content == "RECOVERED ADVISORY"
    assert _calls(failover_install) == [
        ("claude-sonnet-5.5", LIVE_CAP),
        ("claude-sonnet-5.5", LIVE_CAP),
    ], _calls(failover_install)
    logs = [r.message for r in caplog.records]
    assert any("anchor_consult_minimal_retry" in m for m in logs), logs
    assert "anchor_budget_retry" not in _text(retry_log)


def test_success_on_first_call_is_one_call(failover_install, retry_log, caplog):
    """(iv) a delivered first answer buys nothing else — no retry, no failover."""
    failover_install.answer_on = 1
    with caplog.at_level("INFO"):
        content, _c, _p, _t = anchor_exec.anchored_call(_endpoint(), _payload())

    assert content == "RECOVERED ADVISORY"
    assert _calls(failover_install) == [("claude-sonnet-5.5", LIVE_CAP)]
    assert "anchor_budget_retry" not in _text(retry_log)
    logs = [r.message for r in caplog.records]
    assert not any("anchor_consult_failover" in m for m in logs), logs
