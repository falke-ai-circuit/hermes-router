"""R78U M50-25 — the descent shape may not hold an allowance below the floor.

Measured (M50-23 probe, be55019) on anthropic/claude-sonnet-5.5, one fixed
long-form ask:

    mt=  1200  effort=max -> length, reasoning=1200, chars=0
    retry      effort=max -> high, allowance HELD at 1,200   -> _empty, 0 chars
    mt= 12000  effect=max -> high, allowance HELD at 12,000  -> 12,751 chars
    mt= 24000  effect=max -> high, allowance HELD at 24,000  -> 18,726 chars

The same ask needs ~7.6k-9k completion tokens once deliberation is descended.
At 12,000 and 24,000 the held allowance can hold that answer, so M50-15's
"descend effort, do NOT raise the ceiling" is right and stays right. At 1,200
the held allowance cannot hold it by construction: the descent buys nothing and
the retry is a guaranteed billed-and-empty call — the spend M50-16 forbids.

So the descent keeps the cheap allowance only when that allowance clears the
effort rung's own minimum headroom (EFFORT_HEADROOM_FLOOR, anchor_exec.py:1240);
below it, the advisory floor applies (ADVISORY_TOKEN_FLOOR, anchor_exec.py:1262
— "the length retry may never buy less than this when it can afford it").
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
# the descended answer needs ~7.6k-9k completion tokens; 6,000 models "below it".
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


def _payload(cap):
    return {"model": "flash", "max_tokens": cap,
            "messages": [{"role": "system", "content": "persona"},
                         {"role": "user", "content": "THE CONSULT ASK"}]}


def test_descent_at_a_starved_cap_sends_the_floor(install, retry_log):
    """The 1,200 shape: the retry descends effort AND carries the floor.

    Red before M50-25: the retry was sent at max_tokens=1200, came back empty
    (the answer needs ~7.6k-9k), and only the failover produced a body — three
    billed calls where two suffice, one of them a guaranteed empty.
    """
    content, *_ = anchor_exec.anchored_call(_endpoint(), _payload(1200))

    assert content == "RECOVERED ADVISORY"
    assert len(install.calls) == 2, [c["max_tokens"] for c in install.calls]
    first, retry = install.calls
    assert first["max_tokens"] == 1200 and first["reasoning_effort"] == "max"
    assert retry["reasoning_effort"] == "high"
    # the floor the retry may never buy under, clamped to the model ceiling
    assert retry["max_tokens"] == anchor_exec.advisory_token_floor(PRIMARY)
    assert retry["max_tokens"] == anchor_exec.ADVISORY_TOKEN_FLOOR
    assert retry["messages"] == first["messages"]

    lines = retry_log.read_text(encoding="utf-8").splitlines()
    assert any("anchor_budget_retry model=" in ln
               and "max_tokens_from=1200" in ln
               and "max_tokens_to=%d" % anchor_exec.ADVISORY_TOKEN_FLOOR in ln
               and "effort_to=high" in ln for ln in lines), lines
    assert any("anchor_budget_retry_recovered model=" in ln for ln in lines), lines


def test_descent_at_a_sufficient_cap_still_holds_the_allowance(install, retry_log):
    """M50-15 is preserved: at 12,000/24,000 the held allowance already holds
    the descended answer (measured 12,751 and 18,726 chars), so raising it would
    only widen the exposure on a call that needs ~3.4k. Hold it."""
    content, *_ = anchor_exec.anchored_call(_endpoint(), _payload(12000))

    assert content == "RECOVERED ADVISORY"
    assert [c["max_tokens"] for c in install.calls] == [12000, 12000]
    assert install.calls[1]["reasoning_effort"] == "high"


def test_allowance_helper_is_pure_on_the_four_shapes():
    """The decision in one place, so the ladder cannot drift from it."""
    f = anchor_exec._retry_allowance
    # descent, starved cap -> floor
    assert f(1200, 48000, False, "max", "high", PRIMARY) == (
        anchor_exec.ADVISORY_TOKEN_FLOOR)
    # descent, sufficient cap -> held
    assert f(12000, 48000, False, "max", "high", PRIMARY) == 12000
    # descent, at the model ceiling -> clamped to what the model accepts
    assert f(1200, 0, False, "max", "high", "z-ai/glm-5.3") == 32768
    # ceiling arm unchanged: the raise already happened upstream
    assert f(1200, 48000, True, "high", "high", PRIMARY) == 48000
    # no descent, no raise -> held, unchanged behaviour
    assert f(1200, 1200, False, "high", "high", PRIMARY) == 1200
