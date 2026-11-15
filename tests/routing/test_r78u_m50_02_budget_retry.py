"""R78U M50-02 — the budget bug fixes (F1 bounded retry, F2 honest counter).

Root cause (M50-01 diag, binding): reasoning_effort=max spends the SAME
max_tokens allowance as the visible answer. Live: 17,510 completion tokens
produced 3,052 chars against a 23,585 cap; when the thinking runs longer than
the cap the call returns HTTP 200, finish=length, EMPTY content, and the router
logged `anchor_route_failed reason=empty_response` — five dead sessions, one
budget bug.

F1  a finish=length + empty-content response retries ONCE, same messages. Which
    lever it pulls depends on effort (M50-14/M50-15): at max/xhigh it descends
    deliberation one notch and keeps the SAME allowance; when effort cannot
    descend it raises the allowance instead. One retry, never a loop: a second
    empty response does not buy a third call.
F2  the "anchored" counter increments at OUTCOME time, not fire time — a route
    that produced nothing is `skipped`, never also `anchored`.

No cap is lowered and no provider config is written. reasoning_effort is read
(M50-14 may descend it on the retry arm) but the first attempt always runs at the
configured effort.
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


@pytest.fixture()
def retry_log(monkeypatch, tmp_path):
    """M50-07: the retry counters live in the CONFIGURED route log, not the
    module logger. Point the route-log writer at a temp file and read it back."""
    from hermes_router.core import telemetry

    path = tmp_path / "uncensored-router.log"
    monkeypatch.setattr(telemetry, "_log_path", lambda: str(path))
    monkeypatch.setattr(telemetry, "_log_routes", lambda: True)
    return path


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


def _pin_chain(monkeypatch, effort):
    """Pin BOTH anchor-chain seams the consult reads, with a known effort.

    `_anchor_chain_block()` owns the failover list, but the `reasoning_effort`
    that actually reaches the provider payload is injected by anchored_call from
    `config_access.sub_block("anchor_chain")` (anchor_exec.py:897) — a DIFFERENT
    seam. Pinning only the former left the effort under test to the ACTIVE
    PROFILE config: coder's carries `anchor_chain.reasoning_effort: high`, so a
    test expecting "max" was reading ambient config, not the code under test.
    """
    block = {"max_history_turns": 12, "reasoning_effort": effort,
             "failover": [{"model": "z-ai/glm-5.3"}]}
    monkeypatch.setattr(anchor_exec, "_anchor_chain_block", lambda: dict(block))
    from hermes_router.core import config_access
    real_sub_block = config_access.sub_block
    monkeypatch.setattr(
        config_access, "sub_block",
        lambda name: dict(block) if name == "anchor_chain" else real_sub_block(name))


@pytest.fixture()
def f1_install(monkeypatch):
    import openai as _openai

    _FakeClient.calls = []
    _FakeClient.always_empty = False
    _FakeClient.answer = "RECOVERED ADVISORY"
    _FakeClient.threshold = LIVE_CAP + 1
    monkeypatch.setattr(anchor_exec, "_resolve_key", lambda ep, **kw: "test-key")
    monkeypatch.setattr(_openai, "OpenAI", _FakeClient)
    # effort=high is BELOW the M50-14 descent rung, so the retry has only the
    # allowance lever (M50-15). The failover list is NON-EMPTY on purpose:
    # M50-04, a fixture with no failover cannot see the third-call path.
    _pin_chain(monkeypatch, "high")
    return _FakeClient


def _consult_payload(cap=LIVE_CAP):
    return {"model": "flash", "max_tokens": cap,
            "messages": [{"role": "system", "content": "persona"},
                         {"role": "user", "content": "THE CONSULT ASK"}]}


def test_f1a_finish_length_retries_once_with_descended_effort(
        f1_install, retry_log, caplog, monkeypatch):
    """F1a: one empty finish=length response -> exactly ONE retry, same question,
    and the retry descends effort instead of buying a bigger ceiling.

    M50-14/M50-15 supersede the F1a contract this test was written against
    ("larger cap, same reasoning setting"). Measured at effort=max on
    anthropic/claude-sonnet-5.5: 48,000 reasoning tokens, zero content, billed in
    full — raising the ceiling buys the same empty answer at a higher price,
    while effort=high returns a complete answer in ~3.4k tokens at the ORIGINAL
    allowance. So the retry arm pulls the cheap lever (anchor_exec.py:607
    `_budget_retry_effort`, applied at :1077) and, when that lever moves, does
    NOT also raise max_tokens (anchor_exec.py:690 `_retry_raises_ceiling`).

    The fixture's `_anchor_chain_block` patch does not feed the payload: the
    effort actually injected is read through `config_access.sub_block`
    (anchor_exec.py:897). That is why this test now patches THAT call, and why
    the pre-M50-14 assertion below was silently comparing the configured value
    to itself. The raise-the-ceiling contract is exercised by F1b, where effort
    cannot descend.
    """
    from hermes_router.core import config_access

    import openai as _openai

    _real_sub_block = config_access.sub_block
    monkeypatch.setattr(
        config_access, "sub_block",
        lambda name: ({"reasoning_effort": "max"} if name == "anchor_chain"
                      else _real_sub_block(name)))

    class _MaxStarved(_FakeClient):
        """Measured shape: at max/xhigh the whole allowance goes to thinking and
        content never starts, regardless of the ceiling. Below max it writes."""

        def create(self, **payload):
            _FakeClient.calls.append(dict(payload))
            eff = str(payload.get("reasoning_effort") or "").strip().lower()
            if eff in ("max", "xhigh", "maximum"):
                return _Completion("", "length")
            return _Completion(_FakeClient.answer, "stop")

    monkeypatch.setattr(_openai, "OpenAI", _MaxStarved)

    with caplog.at_level("INFO"):
        content, cost, pt, ct = anchor_exec.anchored_call(
            _endpoint(), _consult_payload())

    assert content == "RECOVERED ADVISORY"
    assert len(f1_install.calls) == 2, f1_install.calls

    first, retry = f1_install.calls
    # the FIRST attempt runs at the configured effort, untouched
    assert first["max_tokens"] == LIVE_CAP
    assert first["reasoning_effort"] == "max"
    # same question — the retry is not a different ask
    assert retry["messages"] == first["messages"]
    # M50-14: exactly one notch down, pinned literally so a silent revert to
    # "same effort" (the pre-M50-14 contract) fails here.
    assert retry["reasoning_effort"] == "high"
    assert anchor_exec._budget_retry_effort("max") == "high"
    assert anchor_exec._budget_retry_effort("high") == "high"
    # M50-15: descending effort is the cheap lever — the ceiling is NOT raised
    # alongside it (a double-buy is what the measurement refuted).
    assert retry["max_tokens"] == LIVE_CAP
    assert not anchor_exec._retry_raises_ceiling(True, "max", "high")

    # M50-07: one sink — the configured route log, not the module logger.
    lines = retry_log.read_text(encoding="utf-8").splitlines()
    assert any("anchor_budget_retry model=" in ln
               and "effort_from=max" in ln and "effort_to=high" in ln
               and "max_tokens_to=%d" % LIVE_CAP in ln for ln in lines), lines
    assert any("anchor_budget_retry_recovered model=" in ln
               for ln in lines), lines
    assert not any("anchor_budget_retry" in r.message
                   for r in caplog.records), [r.message for r in caplog.records]


def test_f1a_ceiling_arm_raises_the_allowance(f1_install, retry_log, caplog):
    """F1a (ceiling arm): effort=high sits BELOW the M50-14 descent rung, so
    deliberation cannot move and the allowance is the only lever left (M50-15).
    The retry must then buy headroom above the observed 17.5k thinking peak,
    leave effort alone, and re-send the SAME question — never a token bump.

    The two F1a arms are separate on purpose: M50-10's ceiling raise and
    M50-14's descent are exclusive, and pinning only one of them is how the
    pre-M50-14 assertion kept passing on a call it no longer described.
    """
    with caplog.at_level("INFO"):
        content, cost, pt, ct = anchor_exec.anchored_call(
            _endpoint(), _consult_payload())

    assert content == "RECOVERED ADVISORY"
    assert len(f1_install.calls) == 2, f1_install.calls

    first, retry = f1_install.calls
    assert first["max_tokens"] == LIVE_CAP
    assert first["reasoning_effort"] == "high"
    # the allowance is the lever that moved
    assert retry["max_tokens"] > LIVE_CAP
    assert retry["max_tokens"] >= LIVE_CAP + 16384
    assert retry["messages"] == first["messages"]
    # nothing to descend from: the setting is carried unchanged
    assert retry["reasoning_effort"] == "high"

    # M50-07: one sink — the configured route log, not the module logger.
    lines = retry_log.read_text(encoding="utf-8").splitlines()
    assert any("anchor_budget_retry model=" in ln
               and str(retry["max_tokens"]) in ln for ln in lines), lines
    assert any("anchor_budget_retry_recovered model=" in ln
               for ln in lines), lines
    assert not any("anchor_budget_retry" in r.message
                   for r in caplog.records), [r.message for r in caplog.records]


def test_f1b_second_empty_does_not_trigger_a_third_attempt(f1_install, retry_log,
                                                           caplog):
    """F1b: bounded on BUDGET attempts. The retry itself comes back
    finish=length+empty -> no third budget attempt and no re-sent minimal retry
    (it was measured to fail identically).

    M50-10 supersedes the M50-04 arm count here: an exhausted retry no longer
    kills the failover walk — that skip is what left the lane dead. Three calls
    remain bounded, and the third one is a DIFFERENT model carrying the RAISED
    allowance (48,000), never the 23,585 the primary just died on.
    """
    f1_install.always_empty = True
    with caplog.at_level("INFO"):
        out = anchor_exec.anchored_call(_endpoint(), _consult_payload())

    assert out == (None, None, None, None)
    # one budget allowance, one budget retry, then the failover walk
    assert len(f1_install.calls) == 3, [c["max_tokens"] for c in f1_install.calls]
    assert [c["model"] for c in f1_install.calls] == [
        "claude-sonnet-5.5", "claude-sonnet-5.5", "z-ai/glm-5.3"], f1_install.calls
    assert [c["max_tokens"] for c in f1_install.calls] == [
        LIVE_CAP, anchor_exec.ADVISORY_TOKEN_FLOOR,
        anchor_exec.ADVISORY_TOKEN_FLOOR], f1_install.calls
    assert "anchor_budget_retry_empty model=" in retry_log.read_text(
        encoding="utf-8")
    logs = [r.message for r in caplog.records]
    # the minimal-payload arm is not paid for after a budget retry
    assert not any("anchor_consult_minimal_retry" in m for m in logs), logs
    # M50-10: the budget retry is still the LAST budget attempt — one, never a loop
    assert sum("anchor_budget_retry model=" in ln for ln in
               retry_log.read_text(encoding="utf-8").splitlines()) == 1


def test_f1b_ceiling_reached_skips_retry(monkeypatch, f1_install, retry_log,
                                         caplog):
    """An allowance already at the model ceiling cannot be raised: the retry is
    skipped and logged rather than sending a value the endpoint rejects."""
    assert anchor_exec._budget_retry_max_tokens(64000, "claude-sonnet-5.5") == 64000
    f1_install.always_empty = True
    with caplog.at_level("INFO"):
        anchor_exec.anchored_call(_endpoint(), _consult_payload(cap=64000))
    text = retry_log.read_text(encoding="utf-8")
    assert any("anchor_budget_retry_skipped model=" in ln
               and "reason=ceiling" in ln for ln in text.splitlines()), text


def test_f1_non_length_empty_still_uses_legacy_recovery(f1_install, retry_log,
                                                        caplog):
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
    text = retry_log.read_text(encoding="utf-8") if retry_log.exists() else ""
    assert "anchor_budget_retry" not in text, text


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
