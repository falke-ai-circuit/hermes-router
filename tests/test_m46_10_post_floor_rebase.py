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


# --------------------------------------------------------------------- T6 ----
# M46-10b (reviewer D3/D5): the writer and the reader must build ONE key, and
# the real POST seam must enforce it. These three tests exist because the five
# above all lifted the counter through a `task_id` argument shape production
# never uses — 29 green while the live path was inert.

class _NoEndpointChain:
    """Stands in for the anchor chain so the POST stops right AFTER the gate."""

    def endpoint_for(self, _name):  # noqa: D401 — test double
        return None


def _seam_count(plugin, route_gate, session_id: str, text: str, model: str,
                api_request_id: str) -> None:
    """Drive the REAL llm_request seam once, exactly as the host calls it."""
    req = {"model": model,
           "messages": [{"role": "user", "content": text}]}
    try:
        plugin.on_llm_request(
            request=req, original_request=req,
            session_id=session_id, task_id="host-effective-task",
            turn_id="turn-1", api_request_id=api_request_id,
            api_call_count=1, model=model, provider="test",
            base_url="http://test", api_mode="chat", platform="test")
    except Exception:  # noqa: BLE001 — the seam is fail-open by contract
        pass


def _seam_config(monkeypatch, post_floor: int = 4) -> None:
    from hermes_router import config_access, route_gate

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"level": 0,
                                 "complexity": {"consult_min_turns": 1,
                                                "consult_post_min_turns":
                                                    post_floor,
                                                "consult_max_per_task": 1}})
    monkeypatch.setattr(route_gate, "fence_pass",
                        lambda content, request, context, hs: None)


def test_seam_and_gate_build_one_scope_and_the_floor_fires(budget, monkeypatch):
    """T6/D3+D5: writer key == reader key, and the POST seam refuses 1-3, allows 4.

    Both sides are the REAL call shapes: the counter is advanced by
    `plugin.on_llm_request` (what the host invokes per provider call) and the
    gate is reached through `completion_audit._consult_meta` (the live POST
    seam), which passes `task_id_for(session, ask, model)` exactly as
    `run_completion_audit_sync` does. The anchor chain is stubbed to no
    endpoint so the run stops right AFTER the gate and its own reason is
    readable.
    """
    import sys

    _P = "/opt/data/plugins"
    if _P not in sys.path:
        sys.path.insert(0, _P)
    import hermes_router as plugin
    from hermes_router import completion_audit, router_core

    monkeypatch.setattr("hermes_router.anchor_chain.load_anchor_chain",
                        lambda: _NoEndpointChain())
    monkeypatch.setattr(plugin, "_log_route", lambda e, **f: None)
    _seam_config(monkeypatch)

    sid = "s-m4610-seam"
    text = "wire the seam to the gate"
    model = "glm-5.3"
    tid = router_core.task_id_for(sid, text, model)

    for i in (1, 2, 3):
        _seam_count(plugin, None, sid, text, model, "turn-1:api:%d" % i)

    # The seam wrote under the task id the POST gate will read.
    seam_key = budget.scope_key(sid, "", ask=text, model=model)
    gate_key = budget.scope_key(sid, tid, ask=text, model=model)
    print("seam key=%r post key=%r MATCH: %s"
          % (seam_key, gate_key, seam_key == gate_key))
    assert seam_key == gate_key, (seam_key, gate_key)
    assert budget.model_turns(gate_key) == 3

    diag = {}
    completion_audit._consult_meta(sid, text, "resp text", None, model,
                                   "fire-key", socket_timeout=45, diag=diag,
                                   task_id=tid)
    assert diag.get("reason") == "below_min_turns", diag

    _seam_count(plugin, None, sid, text, model, "turn-1:api:4")
    diag = {}
    completion_audit._consult_meta(sid, text, "resp text", None, model,
                                   "fire-key", socket_timeout=45, diag=diag,
                                   task_id=tid)
    assert diag.get("reason") == "no_primary_endpoint", diag


def test_provider_retry_does_not_double_count(budget, monkeypatch):
    """T7/D2: the host re-enters the middleware with the SAME api_request_id.

    The host runs the middleware inside its own retry loop
    (`while retry_count < max_retries`) and reuses the id, so a retry is one
    provider call. Without this guard, after fix 1 a retry double-counts and
    the floor fires one turn EARLY — the unsafe direction.
    """
    import sys

    _P = "/opt/data/plugins"
    if _P not in sys.path:
        sys.path.insert(0, _P)
    import hermes_router as plugin
    from hermes_router import router_core

    monkeypatch.setattr(plugin, "_log_route", lambda e, **f: None)
    _seam_config(monkeypatch)

    sid, text, model = "s-m4610-retry", "one call, three attempts", "glm-5.3"
    key = router_core.task_id_for(sid, text, model)

    _seam_count(plugin, None, sid, text, model, "turn-1:api:1")
    _seam_count(plugin, None, sid, text, model, "turn-1:api:1")
    _seam_count(plugin, None, sid, text, model, "turn-1:api:1")
    assert budget.model_turns(key) == 1, budget.model_turns(key)

    _seam_count(plugin, None, sid, text, model, "turn-1:api:2")
    assert budget.model_turns(key) == 2, budget.model_turns(key)


def test_allowed_post_decision_is_recorded_with_turn_basis_and_key(
        budget, monkeypatch):
    """T8/D5: an ALLOWED POST carries key/task_turn/task_turn_basis.

    The shipped defect was silent: `task_turn=0` + allow looked like a young
    task. Every POST decision is now on the route log, allow included.
    """
    rows = []
    from hermes_router.core import telemetry

    monkeypatch.setattr(telemetry, "log_route",
                        lambda event, **f: rows.append((event, f)))
    _knobs(monkeypatch, post_floor=4)

    sid, task = "s-m4610-dec", "t-m4610-dec"
    _model_turns(budget, sid, task, 4)
    out = budget.check(sid, task, leg="post")
    assert out["allow"] is True, out

    decided = [f for e, f in rows if f.get("event_detail") == "consult_gate"]
    assert decided, rows
    row = decided[-1]
    assert row["key"] == task, row
    assert row["task_turn"] == 4, row
    assert row["task_turn_basis"] == "model_turns", row
    assert row["allow"] is True, row
    print("allowed POST decision row: key=%r task_turn=%r basis=%r allow=%r"
          % (row["key"], row["task_turn"], row["task_turn_basis"],
             row["allow"]))


def test_internal_error_fail_open_row_names_its_cause(budget, monkeypatch):
    """T9/N1: a fail-open POST names `internal_error` in the recorded row.

    Before this the row was `allow=True reason='' key=''` — the inert-floor
    signature — so a knob/read failure was indistinguishable from a young task
    except by the empty key. The direction is unchanged (still fail-OPEN); only
    the cause is now on the row.
    """
    rows = []
    from hermes_router.core import telemetry

    monkeypatch.setattr(telemetry, "log_route",
                        lambda event, **f: rows.append((event, f)))

    def _boom():
        raise RuntimeError("knob read exploded")

    monkeypatch.setattr(budget, "post_min_turns", _boom)

    out = budget.check("s-m4610-n1", "t-m4610-n1", leg="post")
    assert out["allow"] is True, out          # fail-open is preserved
    assert out["reason"] == "internal_error", out
    assert out["key"] == "!internal_error", out

    decided = [f for e, f in rows if f.get("event_detail") == "consult_gate"]
    assert decided, rows
    row = decided[-1]
    assert row["reason"] == "internal_error", row
    assert row["key"] == "!internal_error", row
    assert row["allow"] is True, row
    print("internal-error fail-open row: key=%r allow=%r reason=%r"
          % (row["key"], row["allow"], row["reason"]))


def test_internal_error_row_does_not_invent_a_task_id(budget, monkeypatch):
    """T10/N4: with no caller task_id, the row's `task_id` stays EMPTY.

    The fail-open path used to reuse `key` for `task_id`, and that key is the
    sentinel `!internal_error` — so any consumer grouping `consult_gate` rows
    by `task_id` saw one phantom task. The cause is still named (key+reason);
    only the fabricated identity is gone.
    """
    rows = []
    from hermes_router.core import telemetry

    monkeypatch.setattr(telemetry, "log_route",
                        lambda event, **f: rows.append((event, f)))

    def _boom():
        raise RuntimeError("knob read exploded")

    monkeypatch.setattr(budget, "post_min_turns", _boom)

    out = budget.check("s-m4610-n4", "", leg="post")
    assert out["allow"] is True, out           # fail-open preserved
    assert out["key"] == "!internal_error", out
    assert out["reason"] == "internal_error", out

    decided = [f for e, f in rows if f.get("event_detail") == "consult_gate"]
    assert decided, rows
    row = decided[-1]
    assert row["task_id"] == "", row
    assert row["key"] == "!internal_error", row
    assert row["reason"] == "internal_error", row
    assert row["allow"] is True, row

    # A caller that DOES pass a task_id keeps it verbatim.
    out2 = budget.check("s-m4610-n4", "t-m4610-n4", leg="post")
    row2 = [f for e, f in rows
            if f.get("event_detail") == "consult_gate"][-1]
    assert out2["reason"] == "internal_error", out2
    assert row2["task_id"] == "t-m4610-n4", row2
    print("error-path rows: no-task_id=%r key=%r reason=%r | with-task_id=%r"
          % (row["task_id"], row["key"], row["reason"], row2["task_id"]))


def test_scope_key_rejects_bang_prefixed_identity(budget, monkeypatch):
    """T11/N5: the `'!'` sentinel prefix is asserted, not convention.

    `task_id` and `trace` are returned verbatim, so a future caller passing a
    leading `'!'` would collide with `!internal_error`. `scope_key` now raises;
    every in-module caller already fails open, so reachability stays none and
    the gate's outward behaviour is unchanged.
    """
    import pytest

    with pytest.raises(ValueError):
        budget.scope_key("s-m4610-n5", "!internal_error")
    with pytest.raises(ValueError):
        budget.scope_key("s-m4610-n5", "", trace="!relabel")
    # Real identities are untouched, sentinel-free keys still build.
    assert budget.scope_key("s-m4610-n5", "t-123") == "t-123"
    assert budget.scope_key("s-m4610-n5") == "s-m4610-n5|-"

    from hermes_router.core import telemetry

    monkeypatch.setattr(telemetry, "log_route", lambda event, **f: None)
    out = budget.check("s-m4610-n5", "!internal_error", leg="post")
    assert out["allow"] is True, out                    # never raises outward
    assert out["reason"] == "internal_error", out
    print("scope_key bangs rejected; check() still fail-open: %r" % (out,))


# --------------------------------------------------------------------- T12 ----
# M47-04 (§5 items 1/3/4): the LIVE shape. The platform appends a
# <memory-context> block to the API copy of the user turn and never to the
# stored content. The writer hashed the API copy while the POST gate hashed the
# stored ask, so every provider call was counted on a key nobody read:
# `model_turns=0`, the floor failed open, and the unit's own seam test could
# not see it because it fed the SAME text to both edges.

MEMORY_BLOCK = (
    "\n\n<memory-context>\n"
    "[System note: the following is recalled memory context, NOT new input.]\n"
    "recalled facts about this lane and the previous unit\n"
    "</memory-context>"
)


def test_injected_seam_and_gate_build_one_ask_and_the_floor_fires(
        budget, monkeypatch):
    """T12: injected turn -> ONE key on both edges; floor refuses 1-3, allows 4.

    Both sides are the real call shapes and NEITHER is handed the other's key:
    the writer is `plugin.on_llm_request` fed the API copy (ask + memory block),
    the reader is `completion_audit._consult_meta` deriving its key from the
    stored ask exactly as `run_completion_audit_sync` does
    (`task_id_for(session, ask, model)`). The anchor chain is stubbed to no
    endpoint so the run stops right after the gate.
    """
    import sys

    _P = "/opt/data/plugins"
    if _P not in sys.path:
        sys.path.insert(0, _P)
    import hermes_router as plugin
    from hermes_router import completion_audit, router_core, state

    monkeypatch.setattr("hermes_router.anchor_chain.load_anchor_chain",
                        lambda: _NoEndpointChain())
    monkeypatch.setattr(plugin, "_log_route", lambda e, **f: None)
    _seam_config(monkeypatch)

    sid, model = "s-m4704-inj", "glm-5.3"
    ask = "wire the seam through the injected memory block"
    api_text = ask + MEMORY_BLOCK
    tid = router_core.task_id_for(sid, ask, model)
    injected_tid = router_core.task_id_for(sid, api_text, model)
    assert injected_tid != tid, "the fixture does not discriminate the two texts"

    for i in (1, 2, 3):
        _seam_count(plugin, None, sid, api_text, model, "turn-1:api:%d" % i)

    stored = state.get_last_seen(sid) or ""
    print("stored ask chars=%d | ask key=%r | injected key=%r"
          % (len(stored), tid, injected_tid))
    # 1. last-seen carries the ASK, not the API copy
    assert stored == ask, stored[:120]
    # 2. the writer landed on the ask's key, not the injected text's
    assert budget.model_turns(tid) == 3, budget.model_turns(tid)
    assert budget.model_turns(injected_tid) == 0, budget.model_turns(injected_tid)
    # 3. writer key == reader key, derived independently
    assert budget.scope_key(sid, "", ask=stored, model=model) == \
        budget.scope_key(sid, tid, ask=ask, model=model)

    # 4. the floor REFUSES on model turns 1-3 through the real POST seam
    for attempt in (1, 2, 3):
        diag = {}
        completion_audit._consult_meta(
            sid, stored, "resp text", {"user_message": stored}, model,
            "fire-key", socket_timeout=45, diag=diag)
        assert diag.get("reason") == "below_min_turns", (attempt, diag)

    # 5. and ALLOWS on model turn 4 (the anchor chain is the only thing left)
    _seam_count(plugin, None, sid, api_text, model, "turn-1:api:4")
    diag = {}
    completion_audit._consult_meta(
        sid, ask, "resp text", {"user_message": ask}, model,
        "fire-key", socket_timeout=45, diag=diag)
    assert diag.get("reason") == "no_primary_endpoint", diag


def test_model_turn_write_emits_a_route_row(budget, monkeypatch):
    """T13: every `note_model_turn` write is on the route log (writer observability).

    Its silence is why a counter written to an unread key was invisible even
    after "every POST decision recorded" shipped: the reader reported
    `task_turn=0` and nothing named the key the writer used.
    """
    rows = []
    from hermes_router import router_core
    from hermes_router.core import telemetry

    monkeypatch.setattr(telemetry, "log_route",
                        lambda event, **f: rows.append((event, f)))

    sid, ask, model = "s-m4704-row", "count this model turn", "glm-5.3"
    total = budget.note_model_turn(sid, ask=ask, model=model)
    assert total == 1

    written = [f for e, f in rows if f.get("event_detail") == "model_turn"]
    assert written, rows
    row = written[-1]
    assert rows[-1][0] == "PRE", rows[-1]
    assert row["count"] == 1, row
    assert row["key"] == router_core.task_id_for(sid, ask, model), row
    print("model_turn row: key=%r count=%r task_id=%r"
          % (row["key"], row["count"], row["task_id"]))


def test_legacy_shape_without_injection_still_reads_twenty_three(
        budget, monkeypatch):
    """T14 (R5): the injection-free shape is unchanged — 23 turns read as 23.

    Same session text for writer and reader, no memory block: the counter must
    still reach 23 and the POST gate must see `task_turn=23`, allow=True. The
    ingress strip is a no-op on this shape, which is the whole check.
    """
    import sys

    _P = "/opt/data/plugins"
    if _P not in sys.path:
        sys.path.insert(0, _P)
    import hermes_router as plugin
    from hermes_router import router_core, state

    monkeypatch.setattr("hermes_router.anchor_chain.load_anchor_chain",
                        lambda: _NoEndpointChain())
    monkeypatch.setattr(plugin, "_log_route", lambda e, **f: None)
    _seam_config(monkeypatch)

    sid, model = "s-m4704-legacy", "glm-5.3"
    ask = "the operative ask that ran a tool loop"
    for i in range(1, 24):
        _seam_count(plugin, None, sid, ask, model, "turn-1:api:%d" % i)

    stored = state.get_last_seen(sid) or ""
    key = router_core.task_id_for(sid, ask, model)
    assert stored == ask, stored[:120]
    assert budget.model_turns(key) == 23, budget.model_turns(key)

    gate = budget.check(sid, "", leg="post", ask=stored, model=model)
    print("legacy POST gate: task_turn=%r allow=%r key_match=%s"
          % (gate.get("task_turn"), gate.get("allow"),
             gate.get("key") == key))
    assert gate["allow"] is True, gate
    assert gate["task_turn"] == 23, gate
    assert gate["task_turn_basis"] == "model_turns", gate


# --------------------------------------------------------------------- T15 ----
# M47-06 (§1): the divergence was not only the trailing block. The host
# PREFIXES the API copy with platform text the store never holds, so the writer
# key stayed different from the reader key even with the trailing strip in
# place: measured `writer turns 3 / reader turns 0` and 594 divergent copies in
# a 40,051-row replay.

PLATFORM_PREFIX = (
    "[System note: A new message has arrived. Respond to the newest ask below.]\n\n"
    "[Context from the interrupted assistant response]\n"
    "the previous turn was cut mid-render; the work below still stands\n"
    "---\n"
)


def _store_holds(monkeypatch, ask: str) -> None:
    """The host's state.db already holds this turn's user row when the first
    provider call fires (session_store docstring, verified live 2026-09-01).
    Stub that read with the row the host would return."""
    from hermes_router import session_store

    monkeypatch.setattr(session_store, "get_last_user_message",
                        lambda session_id: ask)


def test_prefixed_api_copy_yields_one_key_and_the_floor_fires(budget, monkeypatch):
    """T15: prefixed API copy -> writer key == reader key; floor refuses 1-3,
    allows 4. Neither side is handed the other's key."""
    import sys

    _P = "/opt/data/plugins"
    if _P not in sys.path:
        sys.path.insert(0, _P)
    import hermes_router as plugin
    from hermes_router import completion_audit, router_core, state

    monkeypatch.setattr("hermes_router.anchor_chain.load_anchor_chain",
                        lambda: _NoEndpointChain())
    monkeypatch.setattr(plugin, "_log_route", lambda e, **f: None)
    _seam_config(monkeypatch)

    sid, model = "s-m4706-prefixed", "glm-5.3"
    ask = "wire the seam through the platform prefix"
    api_text = PLATFORM_PREFIX + ask + MEMORY_BLOCK
    tid = router_core.task_id_for(sid, ask, model)
    prefixed_tid = router_core.task_id_for(sid, api_text, model)
    assert prefixed_tid != tid, "the fixture does not discriminate the two texts"

    _store_holds(monkeypatch, ask)
    for i in (1, 2, 3):
        _seam_count(plugin, None, sid, api_text, model, "turn-1:api:%d" % i)

    stored = state.get_last_seen(sid) or ""
    print("prefixed: stored chars=%d | ask key=%r | prefixed key=%r"
          % (len(stored), tid, prefixed_tid))
    assert stored == ask, stored[:120]
    assert budget.model_turns(tid) == 3, budget.model_turns(tid)
    assert budget.model_turns(prefixed_tid) == 0, budget.model_turns(prefixed_tid)
    assert budget.scope_key(sid, "", ask=stored, model=model) == \
        budget.scope_key(sid, tid, ask=ask, model=model)

    for attempt in (1, 2, 3):
        diag = {}
        completion_audit._consult_meta(
            sid, stored, "resp text", {"user_message": stored}, model,
            "fire-key", socket_timeout=45, diag=diag)
        assert diag.get("reason") == "below_min_turns", (attempt, diag)

    _seam_count(plugin, None, sid, api_text, model, "turn-1:api:4")
    diag = {}
    completion_audit._consult_meta(
        sid, ask, "resp text", {"user_message": ask}, model,
        "fire-key", socket_timeout=45, diag=diag)
    assert diag.get("reason") == "no_primary_endpoint", diag


def test_clean_shape_with_store_row_still_reads_twenty_three(budget, monkeypatch):
    """T16: the canonical-ask read is a no-op on the injection-free shape —
    23 model turns still read as 23, allow=True (R5 invariant, re-pinned)."""
    import sys

    _P = "/opt/data/plugins"
    if _P not in sys.path:
        sys.path.insert(0, _P)
    import hermes_router as plugin
    from hermes_router import router_core, state

    monkeypatch.setattr(plugin, "_log_route", lambda e, **f: None)
    _seam_config(monkeypatch)

    sid, model = "s-m4706-clean23", "glm-5.3"
    ask = "the operative ask that ran a tool loop"
    _store_holds(monkeypatch, ask)
    for i in range(1, 24):
        _seam_count(plugin, None, sid, ask, model, "turn-1:api:%d" % i)

    stored = state.get_last_seen(sid) or ""
    key = router_core.task_id_for(sid, ask, model)
    assert stored == ask, stored[:120]
    assert budget.model_turns(key) == 23, budget.model_turns(key)

    gate = budget.check(sid, "", leg="post", ask=stored, model=model)
    assert gate["allow"] is True, gate
    assert gate["task_turn"] == 23, gate
    assert gate["task_turn_basis"] == "model_turns", gate
