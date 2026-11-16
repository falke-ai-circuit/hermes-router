"""R78U M50-24 — the declared claim must survive the staging handoff.

Measured defect (orchestrator session 20261006_192644_08377425, 21:15:53Z-
21:16:56Z, and 198 `declared_claim_standdown_unexecuted reason=staging_no_record`
rows fleet-wide): detection was fine (source=aux_intent reason=declared_user),
but the execute-once claim's stage_model_swap returned None because the consult
BUDGET counted per SCOPE and the scope is a TURN identity held across every
tool-loop continuation of one long turn. The first PRE consult of the turn spent
the one slot; every later DISTINCT declared ask (different task_id) was refused
`consult_capped leg=pre count=1 cap=1`, the claim was left executed=False, and
each provider call re-fired it and re-logged a standdown.

Pinned here:
  1. the PRE slot is PER TASK, so two declared asks inside one held scope both
     stage (the collapse must not come back);
  2. a declared claim stages exactly ONCE across the provider calls of a turn;
  3. a budget refusal is TERMINAL — logged as `declared_claim_budget_refused`
     and NOT as `declared_claim_standdown_unexecuted` (no retry storm).
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
from hermes_router import anchor_chain  # noqa: E402
from hermes_router import completion_audit  # noqa: E402,F401 (import-order parity)
from hermes_router import config_access  # noqa: E402
from hermes_router import consult_budget as budget  # noqa: E402
from hermes_router import route_gate  # noqa: E402
from hermes_router import router_core  # noqa: E402
from hermes_router import state  # noqa: E402

SID = "s-m5024"
MODEL = "minimax-m3"
DECLARED = "ask your higher self: what is one blind spot?"
LOGGED = []


def _capture_log(event, **fields):
    LOGGED.append((event, dict(fields)))


def _events(detail):
    out = []
    for _ev, fields in LOGGED:
        if fields.get("event_detail") == detail:
            out.append(fields)
    return out


def _chain():
    return anchor_chain.AnchorChainCfg(
        primary=anchor_chain.parse_anchor_uri("openrouter://test/anchor-primary",
                                              "primary"),
        judge=anchor_chain.parse_anchor_uri("openrouter://test/anchor-judge",
                                           "judge"),
        overflow="pass_through",
        daily_cap_usd=2.0,
        pricing={},
    )


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    LOGGED.clear()
    state.clear()
    budget.reset()
    router_core._test_reset()
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(plugin, "_log_route", _capture_log)
    monkeypatch.setattr(router_core.anchor_chain, "load_anchor_chain", _chain)
    monkeypatch.setattr(config_access, "router_section", lambda: {
        "consult_max_per_task": 1,
        "consult_min_turns": 0,
        "consult_post_min_turns": 0,
    })
    yield
    budget.reset()
    router_core._test_reset()
    state.clear()
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)


# ---------------------------------------------------------------------------
# 1. the collapse: one held turn scope, two distinct declared tasks
# ---------------------------------------------------------------------------

def test_two_distinct_tasks_in_one_held_scope_each_get_a_pre_slot():
    """PIN of the measured cause: a tool-loop turn holds ONE scope id
    (`i:1|t:1`) for hours; the cap is per TASK, so task B is not refused just
    because task A already consulted in the same scope."""
    scope = state.make_scope_id(SID)
    state.record_scope(SID, scope)

    first = budget.check(SID, "task-aaa", leg="pre", scope_id=scope)
    assert first["allow"] is True, first
    assert first["count"] == 1

    second = budget.check(SID, "task-bbb", leg="pre", scope_id=scope)
    assert second["allow"] is True, second
    assert second["count"] == 1

    again = budget.check(SID, "task-aaa", leg="pre", scope_id=scope)
    assert again["allow"] is False, "one PRE per TASK still holds"
    assert again["reason"] == budget.CAPPED_TRIGGER


# ---------------------------------------------------------------------------
# 2. one declared claim -> exactly one staged consult in the turn
# ---------------------------------------------------------------------------

def test_declared_claim_stages_exactly_once_across_provider_calls():
    scope = state.make_scope_id(SID)
    state.record_scope(SID, scope)

    d1 = route_gate.claim_pass(DECLARED, SID, MODEL)
    assert d1.route is True
    assert d1.source == route_gate.SOURCE_DECLARED_USER
    staged = router_core.peek_pending_swap(SID)
    assert staged is not None, "the consult seat must exist (staged=True)"
    assert not _events("declared_claim_standdown_unexecuted"), \
        "a staged claim must not log a standdown"
    executed = _events("request_routing_executed")
    assert executed and executed[-1].get("staged") is True

    task_id = staged["task_id"]
    assert int(budget.counts(SID, task_id).get("pre") or 0) == 1

    # second provider call of the SAME turn (host re-fires on_llm_request)
    d2 = route_gate.claim_pass(DECLARED, SID, MODEL)
    assert d2.route is False, "execute-once: the turn is already claimed"
    assert router_core.peek_pending_swap(SID) is not None
    assert int(budget.counts(SID, task_id).get("pre") or 0) == 1, \
        "no second PRE slot may be spent"

    # the seat is REAL and single-use: the execution seam consumes it exactly once
    consumed = router_core.pending_model_swap(SID)
    assert consumed is not None and consumed["task_id"] == task_id
    assert router_core.pending_model_swap(SID) is None

    # and a DIFFERENT declared ask still stands down inside the SAME turn
    # (one consult per turn is the turn-claim contract) — without staging a
    # second seat and without any standdown-unexecuted noise.
    d3 = route_gate.claim_pass("anchor this: what is one blind spot?", SID, MODEL)
    assert d3.route is False
    assert d3.reason == "turn_claim_exists"
    assert not _events("declared_claim_standdown_unexecuted")
    assert not _events("declared_claim_budget_refused")
    assert int(budget.counts(SID, task_id).get("pre") or 0) == 1


def test_next_turn_declared_ask_is_not_capped_by_the_previous_turn():
    """The live failure shape: turn N staged and spent its slot, turn N+1's
    declared ask must get its OWN slot instead of `consult_capped count=1
    cap=1` -> staging_no_record."""
    # seed the turn identity with the ask that owns turn N (the host advances
    # only when the last-user hash CHANGES from a known prior ask)
    state.advance_turn_identity(SID, state.hash_text("first ask"),
                                is_continuation=False)
    scope_n = state.make_scope_id(SID)
    state.record_scope(SID, scope_n)
    assert route_gate.claim_pass(DECLARED, SID, MODEL).route is True
    first_task = router_core.peek_pending_swap(SID)["task_id"]

    # the host opens turn N+1 (new user ask, not a tool-loop continuation)
    state.advance_turn_identity(SID, state.hash_text("next ask"),
                                is_continuation=False)
    scope_n1 = state.make_scope_id(SID)
    state.record_scope(SID, scope_n1)
    assert scope_n1 != scope_n
    route_gate.clear_turn_claims(SID)

    # a SECOND, DISTINCT declared ask (the live dcb1ac -> eb6fff shape)
    assert route_gate.claim_pass("anchor this: what is one blind spot?",
                                 SID, MODEL).route is True
    second = router_core.peek_pending_swap(SID)
    assert second is not None and second["task_id"] != first_task
    executed = _events("request_routing_executed")
    assert executed[-1].get("staged") is True
    assert not _events("declared_claim_budget_refused")


# ---------------------------------------------------------------------------
# 3. a budget refusal is terminal and named
# ---------------------------------------------------------------------------

def test_budget_refusal_is_terminal_and_not_a_standdown(monkeypatch):
    scope = state.make_scope_id(SID)
    state.record_scope(SID, scope)
    task_id = router_core.task_id_for(SID, DECLARED, MODEL)
    # the task's ONE PRE slot is already spent (as it was in the live session)
    assert budget.check(SID, task_id, leg="pre", scope_id=scope)["allow"] is True

    d = route_gate.claim_pass(DECLARED, SID, MODEL)
    assert d.route is True  # the decision still names the ask
    assert not _events("declared_claim_standdown_unexecuted"), \
        "a spent budget is not a staging failure"
    refused = _events("declared_claim_budget_refused")
    assert refused, "the refusal must be named"
    assert refused[-1].get("reason", "").startswith("budget_")
    assert _events("claim_refused"), "the claim is consumed, not retried"

    rec = route_gate._peek_turn_claim(SID, DECLARED, MODEL)
    assert rec is not None and rec.get("executed") is True
    assert rec.get("refused") is True
