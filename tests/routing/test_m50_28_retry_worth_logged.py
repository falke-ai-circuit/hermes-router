"""R78U M50-28 — the retry DECISION and its four inputs are logged, not inferred.

F3 survived three units (M50-23, M50-25, M50-27) because two earlier briefs
named a log field nobody implemented. This file pins the field so the next close
cannot record it absent again.

`_retry_is_worth_paying(cur_mt, new_mt, cur_effort, new_effort)` answers "is a
starved-consult retry worth paying for at all". Before this unit the only trace
of that answer was the presence or absence of an `anchor_budget_retry` row, and
the row carried `max_tokens_from/to` — the allowance the retry SENT, after
M50-15 and M50-25 re-decide it — not the candidate the predicate was asked
about. On the descent arm those are different numbers (1,200 -> 48,000 sent at
the advisory floor, candidate 48,000), so the row could not answer "why did we
pay for this retry".

Pinned shape on `anchor_budget_retry` (all five on the row):
    worth_paying      bool    the predicate's answer
    worth_cur_mt      int     input 1 — current max_tokens
    worth_new_mt      int     input 2 — candidate ceiling (NOT the sent one)
    worth_cur_effort  str     input 3 — current reasoning_effort ("-" if unset)
    worth_new_effort  str     input 4 — descended/resolved effort ("-" if unset)

The negative case is pinned on `anchor_budget_retry_skipped`: `reason=ceiling`
alone is the OLD predicate's language and cannot distinguish "cannot rise and
cannot descend" from "predicate never asked".

Behaviour unchanged: the retry policy, the floor value and the chosen arm are
untouched by this unit — it adds evidence only.
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
from hermes_router.core import config_access  # noqa: E402

PRIMARY = "claude-sonnet-5.5"
FAILOVER = "z-ai/glm-5.3"
ANSWER_NEEDS = 6000


@pytest.fixture()
def retry_log(monkeypatch, tmp_path):
    from hermes_router.core import telemetry

    path = tmp_path / "uncensored-router.log"
    monkeypatch.setattr(telemetry, "_log_path", lambda: str(path))
    monkeypatch.setattr(telemetry, "_log_routes", lambda: True)
    return path


def _endpoint(model=PRIMARY):
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
                "usage": {"prompt_tokens": 2400, "completion_tokens": 1200}}


class _FakeClient:
    """Empty + finish=length at effort=max or below the answer's need; else writes."""

    calls = []
    always_empty = False

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
            return _Completion("", "length")
        eff = str(payload.get("reasoning_effort") or "").strip().lower()
        mt = int(payload.get("max_tokens") or 0)
        if eff in ("max", "xhigh", "maximum") or mt < ANSWER_NEEDS:
            return _Completion("", "length")
        return _Completion("RECOVERED ADVISORY", "stop")

    def close(self):
        pass


@pytest.fixture()
def install(monkeypatch):
    import openai as _openai

    _FakeClient.calls = []
    _FakeClient.always_empty = False
    block = {"max_history_turns": 12, "reasoning_effort": "max",
             "failover": [{"model": FAILOVER}]}
    monkeypatch.setattr(anchor_exec, "_anchor_chain_block", lambda: dict(block))
    real_sub_block = config_access.sub_block
    monkeypatch.setattr(
        config_access, "sub_block",
        lambda name: dict(block) if name == "anchor_chain" else real_sub_block(name))
    monkeypatch.setattr(anchor_exec, "_resolve_key", lambda ep, **kw: "test-key")
    monkeypatch.setattr(_openai, "OpenAI", _FakeClient)
    return _FakeClient


def _payload(cap, effort=None):
    p = {"model": "flash", "max_tokens": cap,
         "messages": [{"role": "system", "content": "persona"},
                      {"role": "user", "content": "THE CONSULT ASK"}]}
    if effort:
        p["reasoning_effort"] = effort
    return p


def _row(lines, event):
    rows = [ln for ln in lines if "event_detail=%s " % event in ln + " "]
    return rows


def test_retry_row_pins_the_decision_and_its_four_inputs(install, retry_log):
    """RED before M50-28: the row had model/finish/max_tokens_from-to/
    effort_from-to/at_ceiling/reasoning_tokens and NO worth_paying field, so the
    predicate's answer could only be inferred from the row existing."""
    content, *_ = anchor_exec.anchored_call(_endpoint(), _payload(1200))

    assert content == "RECOVERED ADVISORY"
    lines = retry_log.read_text(encoding="utf-8").splitlines()
    rows = [ln for ln in lines if "event_detail=anchor_budget_retry model=" in ln]
    assert len(rows) == 1, lines
    row = rows[0]

    # the decision itself
    assert "worth_paying=True" in row, row
    # input 1 and 2: the CURRENT cap and the CANDIDATE ceiling
    assert "worth_cur_mt=1200" in row, row
    assert "worth_new_mt=%d" % anchor_exec._budget_retry_max_tokens(
        1200, PRIMARY) in row, row
    # input 3 and 4: the effort pair the predicate compared
    assert "worth_cur_effort=max" in row, row
    assert "worth_new_effort=high" in row, row
    # and the sent allowance is still the M50-25 floor, distinct from the candidate
    assert "max_tokens_to=%d" % anchor_exec.ADVISORY_TOKEN_FLOOR in row, row
    assert "worth_new_mt=48000" in row and "max_tokens_to=48000" in row, row


def test_skipped_row_pins_the_negative_decision(install, retry_log, monkeypatch):
    """A no-retry row must say the predicate answered False, and on which inputs.

    Shape: at the model's output ceiling with effort already below max, nothing
    moves — `_retry_is_worth_paying` is False, and the row carries the same four
    inputs so the negative is evidence, not silence.

    The chain block's `reasoning_effort` is what reaches the provider, so the
    fixture's `max` must be lowered here: with `max` the predicate descends
    (worth=True) and this shape never occurs."""
    block = {"max_history_turns": 12, "reasoning_effort": "high",
             "failover": [{"model": FAILOVER}]}
    monkeypatch.setattr(anchor_exec, "_anchor_chain_block", lambda: dict(block))
    real_sub_block = config_access.sub_block
    monkeypatch.setattr(
        config_access, "sub_block",
        lambda name: dict(block) if name == "anchor_chain"
        else real_sub_block(name))

    at_ceiling = anchor_exec.model_output_limit(PRIMARY)
    install.always_empty = True
    anchor_exec.anchored_call(_endpoint(), _payload(at_ceiling, effort="high"))

    lines = retry_log.read_text(encoding="utf-8").splitlines()
    rows = [ln for ln in lines
            if "event_detail=anchor_budget_retry_skipped model=" in ln]
    assert len(rows) == 1, lines
    row = rows[0]

    assert "worth_paying=False" in row, row
    assert "worth_cur_mt=%d" % at_ceiling in row, row
    assert "worth_new_mt=%d" % at_ceiling in row, row
    assert "worth_cur_effort=high" in row, row
    assert "worth_new_effort=high" in row, row
    assert "reason=ceiling" in row, row
