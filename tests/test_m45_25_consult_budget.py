"""R78U M45-25 — per-task consult budget pins.

Contract under test:
  RULE 1  one PRE + one POST per task; the third attempt is REFUSED and
          RECORDED (trigger='consult_capped'), never sent to the frontier.
  RULE 2  below `consult_min_turns` neither leg fires.
  RULE 3  the budget sits BEHIND the complexity gate (stage_model_swap is the
          PRE choke point; completion_audit._consult_meta is the POST one).
  Banners ONE aggregated banner per task carrying both legs.

Failing-before-fix probe: these tests exercise the counters the lane did not
have — on the pre-fix tree `consult_budget` did not exist, so every assertion
here would error at import.
"""
from __future__ import annotations

import pytest

pytest_plugins = ("tests.conftest",)


@pytest.fixture()
def budget(monkeypatch):
    from hermes_router import consult_budget as cb, advisory_consumption as ac

    cb.reset()
    monkeypatch.setattr(ac, "_TURN_NO", {})
    return cb


def _set_turn(budget, session_id: str, n: int) -> None:
    from hermes_router import advisory_consumption as ac

    ac._TURN_NO[session_id] = n


def test_task_driven_20_turns_spends_exactly_one_pre_and_one_post(budget, monkeypatch):
    """RULE 1 + the earned refusal: 20 turns -> 2 frontier calls, 38 capped."""
    rows = []
    from hermes_router import decision as dm

    monkeypatch.setattr(dm, "ledger_write", lambda row, db_path="": rows.append(row))

    session, task = "s-m4525-20t", "t-m4525-20t"
    _set_turn(budget, session, 10)  # well past the min-turn floor

    frontier_calls = 0
    refusals = 0
    for _turn in range(20):
        for leg in ("pre", "post"):
            gate = budget.check(session, task, leg=leg)
            if gate["allow"]:
                frontier_calls += 1  # the spend this gate stands in front of
            else:
                refusals += 1
                assert gate["reason"] == "consult_capped"

    assert frontier_calls == 2, frontier_calls
    assert budget.counts(session, task) == {"pre": 1, "post": 1}
    assert refusals == 38
    capped = [r for r in rows if r.get("trigger") == "consult_capped"]
    assert len(capped) == 38
    assert all(r.get("choice") == "refused" for r in capped)
    assert all(str(r.get("task_id")) == task for r in capped)


def test_task_below_min_turn_threshold_emits_zero_consults(budget, monkeypatch):
    """RULE 2: a short task fires NEITHER leg, and the skip is recorded."""
    rows = []
    from hermes_router import decision as dm, config_access

    monkeypatch.setattr(dm, "ledger_write", lambda row, db_path="": rows.append(row))
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"consult_min_turns": 3, "consult_max_per_task": 1})
    session, task = "s-m4525-short", "t-m4525-short"
    _set_turn(budget, session, 1)  # below the configured floor of 3

    for leg in ("pre", "post", "pre", "post"):
        gate = budget.check(session, task, leg=leg)
        assert gate["allow"] is False
        assert gate["reason"] == "below_min_turns"

    assert budget.counts(session, task) == {}
    assert len([r for r in rows if r.get("trigger") == "consult_capped"]) == 4


def test_knobs_are_read_from_config_not_hardcoded(budget, monkeypatch):
    """RULE 2/3: floor and cap come from the router config section."""
    from hermes_router import config_access

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"consult_min_turns": 7, "consult_max_per_task": 3})
    assert budget.min_turns() == 7
    assert budget.max_per_task() == 3

    session, task = "s-m4525-knob", "t-m4525-knob"
    _set_turn(budget, session, 5)  # 5 < 7 -> still zero
    assert budget.check(session, task, leg="pre")["allow"] is False
    _set_turn(budget, session, 7)
    assert [budget.check(session, task, leg="pre")["allow"] for _ in range(4)] == \
        [True, True, True, False]


def test_pre_choke_point_refuses_second_pre_for_same_task(budget, monkeypatch):
    """RULE 1 on the real PRE seam: stage_model_swap never stages twice."""
    from types import SimpleNamespace

    from hermes_router import router_core as rc, config_access, anchor_chain, decision as dm

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"consult_min_turns": 0})
    monkeypatch.setattr(dm, "ledger_write", lambda row, db_path="": None)

    class _Chain:
        def endpoint_for(self, role="primary"):
            return SimpleNamespace(model="test-model", base_url="http://127.0.0.1:9/v1",
                                   api_key="k", key="k", name="x", scheme="http",
                                   timeout=5)

        def __getattr__(self, _n):
            return lambda *a, **k: None

    monkeypatch.setattr(anchor_chain, "load_anchor_chain", lambda *a, **k: _Chain())
    rc._SWAP_DONE.clear()
    budget.reset()
    session = "s-m4525-stage"
    dec = rc.RouteDecision(task_id="t-m4525-stage", lane=rc.LANE_COMPLEXITY,
                           mode=rc.MODE_CONSULT, model_target="test-model",
                           reason="test", ts=0.0, route_id="r")
    first = rc.stage_model_swap(session, dec)
    second = rc.stage_model_swap(session, dec)
    assert first is not None
    assert second is None
    assert budget.counts(session, "t-m4525-stage") == {"pre": 1}


def test_post_choke_point_refuses_second_post_for_same_task(budget, monkeypatch):
    """RULE 1 on the real POST seam: _consult_meta returns before any call."""
    from hermes_router import completion_audit as ca, config_access

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"consult_min_turns": 0})
    budget.reset()
    session, task = "s-m4525-post", "t-m4525-post"
    _set_turn(budget, session, 5)
    budget.check(session, task, leg="post")  # spend the task's one POST
    diag = {}
    out = ca._consult_meta(session, "ask", "resp", {}, "m", "k",
                           socket_timeout=10, diag=diag, task_id=task)
    assert out is None
    assert diag.get("reason") == "consult_capped"


def test_one_aggregated_banner_per_task_carries_both_legs(budget):
    """BANNERS: pre and post compose into ONE segment, never two banners."""
    from hermes_router import debug_banner as dbg

    sid, task = "s-m4525-banner", "t-m4525-banner"
    dbg._ANCHOR_BANNERS.pop(sid, None)
    dbg._ANCHOR_SEGS.pop(sid, None)
    dbg._ANCHOR_TASKS.pop(sid, None)

    dbg.park_task_banner(sid, task, "pre", "PRE-seg · frontier-anchor ·")
    dbg.park_task_banner(sid, task, "post", "POST-seg · frontier-anchor ·")

    segs = dbg._ANCHOR_SEGS.get(sid, [])
    assert len(segs) == 1, segs
    assert "PRE-seg" in segs[0] and "POST-seg" in segs[0]
    assert segs[0].count("frontier-anchor") == 2


# ---------------------------------------------------------------------------
# M45-25b F2 — the slot is spent at ALLOW time, so a call with NO provider
# response must refund it. Otherwise one provider timeout permanently refuses
# the task's next POST and kills the documented async recovery (a slow consult
# delivers its verdict next turn — "slow is NOT failed").
# ---------------------------------------------------------------------------

def _post_seam(monkeypatch, call, session, task):
    """Run the REAL POST seam once, with `call` standing in for the provider."""
    from types import SimpleNamespace

    from hermes_router import anchor_chain, completion_audit as ca, config_access

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"consult_min_turns": 0, "consult_max_per_task": 1})
    ep = SimpleNamespace(model="test-model", base_url="http://127.0.0.1:9/v1",
                         api_key="k", key="k", name="x", scheme="http",
                         timeout=5)
    chain = SimpleNamespace(endpoint_for=lambda role="primary": ep,
                            pricing={}, daily_cap_usd=10.0)
    monkeypatch.setattr(anchor_chain, "load_anchor_chain", lambda *a, **k: chain)
    monkeypatch.setattr(anchor_chain, "estimate_call_cost", lambda *a, **k: 0.0)
    monkeypatch.setattr(anchor_chain, "cap_check", lambda *a, **k: (True, 0.0, ""))
    monkeypatch.setattr(anchor_chain, "record_spend", lambda *a, **k: None)
    monkeypatch.setattr(anchor_chain, "max_tokens_for_headroom", lambda *a, **k: 0)
    monkeypatch.setattr(ca.anchor_exec, "anchored_call", call)
    monkeypatch.setattr(ca, "persist_frontier_verdict", lambda *a, **k: None,
                        raising=False)
    diag = {}
    out = ca._consult_meta(session, "ask", "resp", {}, "m", "k",
                           socket_timeout=10, diag=diag, task_id=task)
    return out, diag


def test_failed_post_call_refunds_the_slot_so_the_same_task_retries(budget, monkeypatch):
    """F2: raise -> slot refunded -> the SAME task's retry reaches the call."""
    attempts = []

    def _raise(*_a, **_k):
        attempts.append("raise")
        raise RuntimeError("provider blew up")

    def _no_response(*_a, **_k):
        attempts.append("empty")
        return None, None, None, None

    session, task = "s-m4525-refund", "t-m4525-refund"
    _set_turn(budget, session, 5)

    out, diag = _post_seam(monkeypatch, _raise, session, task)
    assert out is None
    assert diag.get("reason") == "consult_raised"
    assert diag.get("budget_refunded") == "consult_raised"
    assert budget.counts(session, task) == {}, \
        "a call that produced NO response must not keep the task's POST slot"

    # Same task, next attempt: must reach the provider call AGAIN, never
    # consult_capped. This is the async-recovery path.
    out2, diag2 = _post_seam(monkeypatch, _no_response, session, task)
    assert attempts == ["raise", "empty"], attempts
    assert out2 is None
    assert diag2.get("reason") != "consult_capped", diag2
    assert budget.counts(session, task) == {}, \
        "an empty completion is a non-response too — refund it"

    # And the retry really is allowed by the gate itself.
    _set_turn(budget, session, 6)
    assert budget.check(session, task, leg="post")["allow"] is True


def test_successful_post_keeps_the_slot_and_the_third_leg_is_refused(budget, monkeypatch):
    """F2 control: a REAL response keeps the slot; two legs, third refused."""
    calls = []

    def _ok(*_a, **_k):
        calls.append("ok")
        return "NO-FINDINGS", 0.0, 11, 3

    session, task = "s-m4525-keep", "t-m4525-keep"
    _set_turn(budget, session, 5)

    pre = budget.check(session, task, leg="pre")
    assert pre["allow"] is True          # successful PRE keeps its slot
    out, _diag = _post_seam(monkeypatch, _ok, session, task)
    assert out is not None               # a response arrived -> slot stays spent
    assert len(calls) == 1
    assert budget.counts(session, task) == {"pre": 1, "post": 1}

    # Two successful calls, third refused — on the seam, not just the counter.
    out2, diag2 = _post_seam(monkeypatch, _ok, session, task)
    assert len(calls) == 1, "a spent POST must never reach the provider again"
    assert out2 is None
    assert diag2.get("reason") == "consult_capped"
    assert budget.check(session, task, leg="pre")["allow"] is False


def test_counter_store_is_bounded(budget, monkeypatch):
    """Non-blocking: a long-lived gateway must not grow _COUNTS forever."""
    from hermes_router import config_access

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"consult_min_turns": 0, "consult_max_per_task": 1})
    for i in range(budget.MAX_SCOPES + 50):
        budget.check("s-prune", "t-%d" % i, leg="pre")
    assert len(budget._COUNTS) <= budget.MAX_SCOPES
    assert len(budget._TURNS_SEEN) <= budget.MAX_SCOPES
