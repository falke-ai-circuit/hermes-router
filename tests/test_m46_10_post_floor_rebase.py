"""R78U M46-10 — the POST age floor is on the basis M46-08 measured.

M46 was closed while the default could not fire. `consult_post_min_turns`
shipped as 3 and analyst measured DEFAULT = 4 with a named BLOCKER: the knob
counted SESSION turns, and a session turn advances once per USER message
(`advisory_consumption.turn_no` -> `note_turn_open`). Measured fleet-wide: user
messages per session median 1, only 14.7% of sessions reach 2, only 3.3% reach
4 — so a floor of 4 on that basis suppressed the POST on ~96.7% of sessions.
The number was right and the unit was wrong.

The re-base (this unit):
  BEFORE  task_turn = session_turn - first_seen_session_turn + 1
          (session_turn = advisory_consumption.turn_no, one per user ask)
          — consult_budget.py:153-184 before this change.
  NOW     POST task turn = MODEL TURNS observed for the ask's scope, advanced
          by `consult_budget.note_model_turn()` at the llm_request seam, one
          per provider call = one assistant message row = the unit M46-08
          measured. The PRE repeat floor keeps the session-turn basis.

What these tests pin:
  T1  the basis really is model turns — a large session-turn count never
      satisfies the POST floor by itself, and the report says which basis.
  T2  the default is 4 and it is REACHABLE (turn 4 fires; turns 1-3 refuse).
  T3  the boundary moves by CONFIG ALONE: the same task, same code, threshold
      2 fires the POST on model turn 2 while threshold 4 refuses it
      `below_min_turns` and RECORDS the refusal.
  T4  an allowed POST lands in the DELIVERED body (asserted on the bytes the
      user receives, not on a return value).
  T5  the llm_request seam actually drives the counter.
"""
from __future__ import annotations

import copy
import json

import pytest

pytest_plugins = ("tests.conftest",)


@pytest.fixture(autouse=True)
def _config_cache_neutral():
    """Leave `config_access._cache` exactly as this module found it."""
    from hermes_router import config_access

    saved = copy.deepcopy(getattr(config_access, "_cache", None))
    yield
    cache = getattr(config_access, "_cache", None)
    if isinstance(cache, dict) and isinstance(saved, dict):
        cache.clear()
        cache.update(saved)


@pytest.fixture()
def budget(monkeypatch):
    from hermes_router import advisory_consumption as ac, consult_budget as cb

    cb.reset()
    monkeypatch.setattr(ac, "_TURN_NO", {})
    yield cb
    cb.reset()


def _set_turn(session_id: str, n: int) -> None:
    from hermes_router import advisory_consumption as ac

    ac._TURN_NO[session_id] = n


def _knobs(monkeypatch, post_floor: int, pre_floor: int = 1, cap: int = 1) -> None:
    """Pin the dials where the fleet carries them: `router.complexity`."""
    from hermes_router import config_access

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"complexity": {"consult_min_turns": pre_floor,
                                                "consult_post_min_turns": post_floor,
                                                "consult_max_per_task": cap}})


def _model_turns(budget, session_id: str, task_id: str, n: int) -> None:
    for _ in range(int(n)):
        budget.note_model_turn(session_id, task_id)


# --------------------------------------------------------------------- T1 ----

def test_post_floor_reads_model_turns_not_the_user_ask_counter(budget, monkeypatch):
    """T1: the re-base. Session turns alone can never satisfy the POST floor.

    Baseline 40 user asks in the session (session turn 40) and a 4-turn floor:
    on the OLD basis that was `task_turn = 40 - born + 1`, which fired the POST
    immediately. On the new basis the ask has run ZERO model turns, so the POST
    must be refused `below_min_turns`, and the report must name the basis.
    """
    _knobs(monkeypatch, post_floor=4)
    session, task = "s-m4610-t1", "t-m4610-t1"
    _set_turn(session, 40)

    assert budget.check(session, task, leg="pre")["allow"] is True
    # A task with NO observed model turn fails OPEN (the documented legacy
    # behaviour for harness/API dispatches): 0 is "unknown", never "aged 0".
    assert budget.check(session, task, leg="post")["allow"] is True
    budget.refund(session, task, leg="post")

    _set_turn(session, 41)  # a second user ask — still not a MODEL turn
    _model_turns(budget, session, task, 1)
    post = budget.check(session, task, leg="post")
    assert post["allow"] is False, post
    assert post["reason"] == "below_min_turns", post
    assert post["task_turn_basis"] == "model_turns", post
    assert post["task_turn"] == 1, post

    # three more model turns still do not reach a 4-turn floor
    _model_turns(budget, session, task, 2)
    assert budget.check(session, task, leg="post")["reason"] == "below_min_turns"


def test_pre_leg_keeps_the_session_turn_basis(budget, monkeypatch):
    """T1: the PRE repeat floor was NOT re-based — the two legs differ on purpose."""
    _knobs(monkeypatch, post_floor=4, pre_floor=3, cap=3)
    session, task = "s-m4610-t1b", "t-m4610-t1b"
    _set_turn(session, 10)
    first = budget.check(session, task, leg="pre")
    assert first["allow"] is True and first["task_turn_basis"] == "session_turns"
    _set_turn(session, 11)
    second = budget.check(session, task, leg="pre")
    assert second["reason"] == "below_min_turns", second
    assert second["task_turn_basis"] == "session_turns", second


# --------------------------------------------------------------------- T2 ----

def test_default_is_the_measured_four_and_it_is_reachable(budget, monkeypatch):
    """T2: DEFAULT = 4 (M46-08) and 4 model turns is a reachable boundary.

    The old basis made 4 unreachable in practice (median 1 user message per
    session); here the fourth model turn is exactly where the POST is allowed.
    """
    from hermes_router import config_access

    monkeypatch.setattr(config_access, "router_section", lambda: {})
    assert budget.DEFAULT_POST_MIN_TURNS == 4
    assert budget.post_min_turns() == 4

    _knobs(monkeypatch, post_floor=4)
    session, task = "s-m4610-t2", "t-m4610-t2"
    assert budget.check(session, task, leg="pre")["allow"] is True

    _model_turns(budget, session, task, 3)
    for attempt in (1, 2, 3):
        gate = budget.check(session, task, leg="post")
        assert gate["allow"] is False, (attempt, gate)
        assert gate["reason"] == "below_min_turns", gate

    _model_turns(budget, session, task, 1)  # model turn 4 = the floor
    post = budget.check(session, task, leg="post")
    assert post["allow"] is True, post
    assert post["task_turn"] == 4, post
    assert budget.counts(session, task) == {"pre": 1, "post": 1}


# --------------------------------------------------------------------- T3 ----

def test_threshold_moves_by_config_alone(budget, monkeypatch):
    """T3: THRESHOLD 2 fires a POST on model turn 2; THRESHOLD 4 refuses it.

    Same task shape, same code, same model turns — ONLY the configured knob
    differs between the two runs. The 4-case refusal is recorded as
    `below_min_turns` in the ledger, not dropped.
    """
    rows = []
    from hermes_router import decision as dm

    monkeypatch.setattr(dm, "ledger_write", lambda row, db_path="": rows.append(row))

    def run(threshold, session, task):
        _knobs(monkeypatch, post_floor=threshold)
        assert budget.check(session, task, leg="pre")["allow"] is True
        _model_turns(budget, session, task, 2)
        return budget.check(session, task, leg="post")

    low = run(2, "s-m4610-lo", "t-m4610-lo")
    assert low["allow"] is True, low
    assert low["task_turn"] == 2, low
    assert low["task_turn_basis"] == "model_turns", low

    budget.reset()
    high = run(4, "s-m4610-hi", "t-m4610-hi")
    assert high["allow"] is False, high
    assert high["reason"] == "below_min_turns", high
    assert high["task_turn"] == 2, high
    assert budget.counts("s-m4610-hi", "t-m4610-hi") == {"pre": 1}

    refused = [r for r in rows if r.get("trigger") == "consult_capped"]
    assert len(refused) == 1, rows
    assert refused[0].get("choice") == "refused"
    verdict = json.loads(str(refused[0].get("verdict_json")))
    assert verdict["reason"] == "below_min_turns", verdict
    assert verdict["task_turn"] == 2, verdict
    assert verdict["leg"] == "post", verdict


# --------------------------------------------------------------------- T4 ----

def test_post_lands_in_the_delivered_body_past_the_threshold(
        budget, monkeypatch, tmp_path):
    """T4: an allowed POST is visible in the DELIVERED bytes, not a return value.

    Threshold 4, model turn 4: the gate allows the POST, its banner parks the
    way completion_audit parks it, and the delivery seam composes it into THIS
    response. Asserted on the composed body the user receives.
    """
    import sys

    _P = "/opt/data/plugins"
    if _P not in sys.path:
        sys.path.insert(0, _P)
    import hermes_router as plugin
    from hermes_router import (completion_audit, debug_banner, render_inbox,
                               route_gate, router_core, state, usage_ledger)
    from hermes_router.features.banners import turn_record

    sid, task = "s-m4610-body", "t-m4610-body"
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(sid)
    route_gate.clear_declared(sid)
    route_gate.clear_turn_claims(sid)
    turn_record.reset()
    turn_record.reset(sid)
    debug_banner._ANCHOR_BANNERS.clear()
    debug_banner._ANCHOR_TASKS.clear()
    debug_banner._ANCHOR_SEGS.clear()
    debug_banner._PARK_RECORDS.clear()
    monkeypatch.setattr(plugin, "_log_route", lambda e, **f: None)
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr(render_inbox, "_inbox_path",
                        lambda: str(tmp_path / "renders.jsonl"))
    monkeypatch.setattr(debug_banner, "_banner_section",
                        lambda: {"debug_banner": 1}, raising=True)
    monkeypatch.setattr(completion_audit, "audit_enabled", lambda: False)
    from hermes_router import config_access
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"debug_banner": 1,
                                 "complexity": {"consult_min_turns": 1,
                                                "consult_post_min_turns": 4,
                                                "consult_max_per_task": 1}})

    assert budget.check(sid, task, leg="pre")["allow"] is True
    _model_turns(budget, sid, task, 4)  # the ask is 4 model turns old
    post = budget.check(sid, task, leg="post")
    assert post["allow"] is True, post
    assert post["task_turn_basis"] == "model_turns", post

    line = debug_banner.format_banner(
        lane="frontier-anchor", trigger="completion_audit", model="glm-5.3",
        endpoint="https://api.nousresearch.com/v1", tokens_in=150,
        tokens_out=40, est_cost=0.06, latency_s=3, task_id=task,
        session_id=sid)
    debug_banner.park_task_banner(sid, task, "post", line)

    out = plugin.on_transform_llm_output(
        response_text="final answer for the aged task", session_id=sid, model="m")
    body = str(out or "")
    assert body.startswith("final answer for the aged task"), body[:200]
    assert debug_banner.BANNER_HEAD in body, body[-600:]
    assert "post-check" in body, body[-600:]
    assert "tok 150/40" in body, body[-600:]
    turn_record.reset(sid)


# --------------------------------------------------------------------- T5 ----

def test_llm_request_seam_counts_one_model_turn(monkeypatch):
    """T5: the seam the host fires per provider call is what drives the counter.

    A spy on `consult_budget.note_model_turn` proves the wiring: without it the
    POST floor would sit at 0 forever and fail open (the knob dead on arrival in
    the other direction).
    """
    import sys

    _P = "/opt/data/plugins"
    if _P not in sys.path:
        sys.path.insert(0, _P)
    import hermes_router as plugin
    from hermes_router import config_access, consult_budget, route_gate

    monkeypatch.setattr(config_access, "router_section", lambda: {"level": 0})
    calls = []
    monkeypatch.setattr(consult_budget, "note_model_turn",
                        lambda session_id="", **kw: calls.append((session_id, kw)))
    monkeypatch.setattr(route_gate, "fence_pass",
                        lambda content, request, context, hs: None)
    monkeypatch.setattr(plugin, "_log_route", lambda e, **f: None)

    req = {"model": "test-model",
           "messages": [{"role": "user", "content": "count this model turn"}]}
    try:
        plugin.on_llm_request(request=req, original_request=req, session_id="s-m4610-t5")
    except Exception:  # noqa: BLE001 — the seam is fail-open by contract
        pass

    assert calls, "llm_request seam never counted a model turn"
    assert calls[0][1].get("ask") == "count this model turn", calls[0]
    assert calls[0][1].get("model") == "test-model", calls[0]
