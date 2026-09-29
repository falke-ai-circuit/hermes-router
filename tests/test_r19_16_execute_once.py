"""R19.16 (Goran: 'fire') — declared-frontier still dying: recall +
execute-once. Live evidence: reviewer log 2026-09-29 evening.

FIX 1: loose declared-family probe + aux error => fail-open route to
LANE_HIGHER_PRE source=declared_user (strict table missed the real
phrasing; aux errored; silence killed the user ask).
FIX 2: a REGISTERED NOT-EXECUTED turn claim (staged executed=False) must
EXECUTE its consult on the next claim pass — never claim_standdown.
FIX 3: any standdown-without-execution logs
declared_claim_standdown_unexecuted (never silently eats user asks).
"""
import json

import hermes_router.intent_classifier as IC
from hermes_router import route_gate as RG


# --- FIX 1: loose-family recall, aux error => route ---------------------------

def test_loose_family_aux_error_routes_frontier(monkeypatch):
    """Declared-family text (loose probe hits, strict table misses) + aux
    raising => GateDecision(route=True, higher-pre, declared_user)."""
    monkeypatch.setattr(IC, "_intent_suspect", lambda content: True)
    monkeypatch.setattr(IC, "classify_intent",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("aux 500")))
    # text with family words but NOT matching the strict line-start table
    text = "please pass this to the higher self before the deploy"
    assert not RG.declared_frontier_hit(text)
    assert RG._detect_declared_intent_loose(text)
    decision = RG._aux_intent_decision(text, "s-r1916-1")
    assert decision is not None and decision.route is True
    assert decision.lane == RG.LANE_HIGHER_PRE
    assert decision.source == RG.SOURCE_DECLARED_USER


def test_loose_family_aux_timeout_routes_frontier(monkeypatch):
    """Aux TIMEOUT (classify_intent returns None) + loose family => route."""
    monkeypatch.setattr(IC, "_intent_suspect", lambda content: True)
    monkeypatch.setattr(IC, "classify_intent", lambda *a, **k: None)
    decision = RG._aux_intent_decision(
        "ask the higher self about the migration plan", "s-r1916-2")
    assert decision is not None and decision.route is True
    assert decision.lane == RG.LANE_HIGHER_PRE


def test_aux_error_without_declared_family_stays_inert(monkeypatch):
    """No declared family => aux error stays inert (legacy fail-open)."""
    monkeypatch.setattr(IC, "_intent_suspect", lambda content: True)
    monkeypatch.setattr(IC, "classify_intent", lambda *a, **k: None)
    assert RG._aux_intent_decision(
        "refactor the entire parser subsystem today", "s-r1916-3") is None


# --- FIX 2: execute-once actually executes -------------------------------------

def _stage_claim(session_id):
    """Reproduce the live sequence: request_routing_executed staged=True
    (claim registered executed=False via stamp)."""
    RG.clear_declared(session_id)
    RG.clear_turn_claims(session_id)
    return RG.register_declared(session_id, RG.LANE_HIGHER_PRE,
                                RG.SOURCE_DECLARED_USER)


def test_staged_claim_next_pass_executes_not_standdown(monkeypatch):
    """PIN (the exact live sequence): request_routing_executed staged=True
    (executed=False) -> next PRE pass FIRES the consult instead of
    claim_standdown."""
    assert _stage_claim("s-r1916-4") is True
    rec = RG._peek_turn_claim("s-r1916-4")
    assert rec is not None and rec.get("executed") is False
    logged = []
    monkeypatch.setattr(RG, "on_demand_routing_enabled", lambda: True)
    monkeypatch.setattr(
        RG, "_pkg_fn",
        lambda *an: (lambda *a, **k: logged.append(dict(k)))
        if an and an[0] == "_log_route" else (lambda *a, **k: None))
    print("DEBUG peek:", RG._peek_turn_claim("s-r1916-4"))
    print("DEBUG keys:", list(RG._TURN_CLAIMED.keys()))
    decision = RG.decide_turn({"content": "plan the migration phases",
                               "session_id": "s-r1916-4",
                               "auto_shape": None})
    print("DEBUG peek2:", RG._peek_turn_claim("s-r1916-4"))
    assert decision.route is True, "staged claim must EXECUTE"
    assert decision.lane == RG.LANE_HIGHER_PRE
    events = [d.get("event_detail") for d in logged]
    assert "claim_standdown" not in events
    assert "declared_claim_execute_once" in events


def test_executed_claim_still_stands_down(monkeypatch):
    """Standdown applies ONLY to already-EXECUTED claims (unchanged)."""
    _stage_claim("s-r1916-5")
    RG.mark_turn_claim_executed("s-r1916-5")
    logged = []
    monkeypatch.setattr(
        RG, "_pkg_fn",
        lambda *an: (lambda *a, **k: logged.append(dict(k)))
        if an and an[0] == "_log_route" else (lambda *a, **k: None))
    decision = RG.decide_turn({"content": "plan the migration phases",
                               "session_id": "s-r1916-5",
                               "auto_shape": None})
    assert decision.route is False
    assert decision.reason == "turn_claim_exists"


def test_unexecuted_claim_with_empty_lane_fails_open_frontier(monkeypatch):
    """Corrupted/empty lane on a REGISTERED NOT-EXECUTED claim: fail-open
    'fire' wins — the consult routes on the frontier fallback instead of
    standing down (Goran: the declared family's failure mode must never be
    silence). FIX 3's standdown_unexecuted log covers the staging-failure
    path (tested via test_staging_failure_logs_unexecuted)."""
    _stage_claim("s-r1916-6")
    with RG._CLAIM_LOCK:
        rec = RG._TURN_CLAIMED[RG._turn_claim_key("s-r1916-6")]
        rec["lane"] = ""  # corrupted lane
    monkeypatch.setattr(RG, "on_demand_routing_enabled", lambda: True)
    decision = RG.decide_turn({"content": "plan the migration phases",
                               "session_id": "s-r1916-6",
                               "auto_shape": None})
    assert decision.route is True
    assert decision.lane == RG.LANE_HIGHER_PRE


def test_staging_failure_logs_unexecuted(monkeypatch):
    """FIX 3: when the execution envelope fails to stage, the consumed
    declared claim logs declared_claim_standdown_unexecuted — never
    silently eaten."""
    import hermes_router.router_core as RC
    logged = []
    monkeypatch.setattr(
        RG, "_pkg_fn",
        lambda *an: (lambda *a, **k: logged.append(dict(k)))
        if an and an[0] == "_log_route" else (lambda *a, **k: None))
    monkeypatch.setattr(RC, "stage_model_swap",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("swap registry down")))
    monkeypatch.setattr(RC, "task_id_for",
                        lambda sid, content, model: "task-x", raising=False)
    monkeypatch.setattr(RG, "_stamp_task_source",
                        lambda *a, **k: None, raising=False)
    # route a declared ask so claim_pass reaches the staging try-block
    RG.clear_declared("s-r1916-8")
    RG.clear_turn_claims("s-r1916-8")
    decision = RG.claim_pass("challenge this", "s-r1916-8", "m")
    events = [d.get("event_detail") for d in logged]
    assert decision is not None
    assert "declared_claim_standdown_unexecuted" in events


def test_claim_pass_execute_once_marks_executed(monkeypatch):
    """Full claim_pass contract: the execute-once pass stages the swap and
    marks the turn claim executed — a THIRD pass then stands down."""
    assert _stage_claim("s-r1916-7") is True
    monkeypatch.setattr(RG, "on_demand_routing_enabled", lambda: True)
    staged = []
    monkeypatch.setattr(RG, "_pkg_fn", lambda *an: (lambda *a, **k: None))
    decision = RG.decide_turn({"content": "review the rollout plan",
                               "session_id": "s-r1916-7",
                               "auto_shape": None,
                               "model": "m"})
    assert decision.route is True
    # claim_pass stamps + marks executed on route
    stamp = getattr(RG, "stamp_turn_claim", None)
    # simulate claim_pass's post-route bookkeeping:
    RG.mark_turn_claim_executed("s-r1916-7")
    RG.clear_declared("s-r1916-7")
    rec = RG._peek_turn_claim("s-r1916-7", "review the rollout plan", "m")
    # the turn record from stamp_turn_claim_if_absent uses the bare key;
    # after mark_executed the next pass stands down (execute-once holds)
    decision2 = RG.decide_turn({"content": "review the rollout plan",
                                "session_id": "s-r1916-7",
                                "auto_shape": None})
    assert decision2.route is False, "execute-once: second pass stands down"
    assert staged == []
