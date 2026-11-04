"""M2.1 pins: the DECISION lane on the unified envelope, BOTH directions.

Contract under test (plan §3.1-3.3, reference commits 523fc04 + ec5d167):

  * REQUEST out  — decision.build_envelope delegates to the ONE builder
    (features/envelope/unified.build_request) as schema lane-request/1 with
    lane="decision"; PRE and MIDTURN produce the same template shape.
  * ASSIMILATION in — the lane hands its structured result back through the
    ONE builder (lane-assimilation/1) with the registry's CLOSED decision
    extras set; nothing bespoke.

The last two tests are the negative proof: a hand-rolled "bespoke" frame is
REJECTED in each direction while the unified one is accepted, so a lane that
invents its own envelope fails here instead of drifting silently.
"""
from __future__ import annotations

from hermes_router import decision as D
from hermes_router.features.envelope import unified as U
from hermes_router.features.envelope import registry as R

OPTS = [{"id": "opt-1", "label": "redis", "cause_effect": "low latency",
         "cost": "1 node", "priors": {"redis": 1}, "risk": "low"},
        {"id": "opt-2", "label": "memcached", "cause_effect": "simple cache",
         "cost": "1 node", "priors": {"memcached": 1}, "risk": "low"}]

# The shared template slots the decision lane has always published (legacy
# 15-field shape). 'surround' is the short name for surrounding_context.
REQUEST_TEMPLATE = ("schema", "agent_frame", "final_objective", "rolling_task",
                    "session_context", "scope", "causal_context", "options",
                    "weighting", "mindset", "question", "slice", "trigger",
                    "fork_class")
REQUEST_ALIASES = {"surround": "surrounding_context",
                   "surrounding_context": "surrounding_context"}


def _spy_request(monkeypatch):
    seen = []
    real = U.build_request

    def spy(**kw):
        env = real(**kw)
        seen.append((kw, env))
        return env

    monkeypatch.setattr(U, "build_request", spy)
    return seen


# --- REQUEST direction -----------------------------------------------------

def test_decision_pre_request_goes_through_the_one_builder(monkeypatch):
    seen = _spy_request(monkeypatch)
    env = D.build_envelope("s-m2-pre", "redis or memcached?", ["redis", "memcached"],
                           "pre")
    assert env, "decision PRE envelope is empty"
    assert seen, "adapter bypassed the unified builder"
    lane, request = seen[-1][0].get("lane"), seen[-1][1]
    assert lane == "decision"
    assert request["schema"] == U.SCHEMA_REQUEST
    assert U.validate_request(request) == [], U.validate_request(request)
    for key in REQUEST_TEMPLATE:
        assert key in env, "decision PRE envelope lost %r" % key
    assert env["schema"] == D.ENVELOPE_SCHEMA
    assert [o["id"] for o in env["options"]] == ["opt-1", "opt-2"]


def test_decision_midturn_inherits_the_same_template(monkeypatch):
    pre_seen = _spy_request(monkeypatch)
    pre = D.build_envelope("s-m2-mid", "redis or memcached?",
                           ["redis", "memcached"], "pre")
    mid = D.build_envelope("s-m2-mid", "redis or memcached?",
                           ["redis", "memcached"], "midturn",
                           surrounding_context="tool result: p99 840ms")
    assert mid, "decision MIDTURN envelope is empty (frame starvation)"
    reqs = [env for kw, env in pre_seen if kw.get("lane") == "decision"]
    assert len(reqs) == 2, "midturn bypassed the unified builder"
    assert reqs[-1]["trigger"] == "midturn"
    assert U.validate_request(reqs[-1]) == []
    # Same template: every key of PRE's shape is present in MIDTURN's, plus the
    # frame-context slots the midturn seam fills.
    assert set(pre) <= set(mid)
    for key in ("surrounding_context", "frame_context_chars_used"):
        assert key in mid, "midturn lost %r" % key
    assert mid["schema"] == pre["schema"] == D.ENVELOPE_SCHEMA


# --- ASSIMILATION direction ------------------------------------------------

def test_decision_assimilation_goes_through_the_one_builder(monkeypatch):
    seen = []
    real = U.build_assimilation

    def spy(**kw):
        env = real(**kw)
        seen.append((kw, env))
        return env

    monkeypatch.setattr(U, "build_assimilation", spy)
    request = D.build_envelope("s-m2-assim", "redis or memcached?",
                               ["redis", "memcached"], "pre")
    verdict = {"choice": "opt-1", "confidence": 0.82, "alternatives": ["opt-2"]}
    env = D.build_decision_assimilation(request, verdict, task_id="t-m2",
                                        ledger_id=4242,
                                        answer="advisory text")
    assert env, "decision assimilation envelope is empty"
    assert seen and seen[-1][0].get("lane") == "decision"
    built = seen[-1][1]
    assert built["schema"] == U.SCHEMA_ASSIMILATION
    assert U.validate_assimilation(built) == [], U.validate_assimilation(built)
    assert built["lane"] == "decision"
    assert built["task_id"] == "t-m2"
    assert built["evidence_refs"] == ["decision_ledger:4242"]
    extras = built["extras"]["decision"]
    assert set(extras) == set(R.ASSIM_EXTRAS_KEYS["decision"])
    assert extras["chosen"] == "opt-1"
    assert extras["outcome_label"] == "resolved"
    assert [o["id"] for o in extras["options"]] == ["opt-1", "opt-2"]
    assert extras["resolver_rule"]


def test_decision_assimilation_records_terminal_states():
    request = D.build_envelope("s-m2-assim2", "redis or memcached?",
                               ["redis", "memcached"], "pre")
    stand = D.build_decision_assimilation(
        request, {"choice": D.STAND_DOWN_CHOICE, "confidence": 0.0})
    assert stand["extras"]["decision"]["outcome_label"] == "stand_down"
    unmapped = D.build_decision_assimilation(
        request, {"choice": "unmapped", "confidence": 0.9})
    assert unmapped["extras"]["decision"]["outcome_label"] == "invalid_fork"
    none = D.build_decision_assimilation(request, None)
    assert none["extras"]["decision"]["outcome_label"] == "pending"
    assert none["extras"]["decision"]["chosen"] == ""


# --- NEGATIVE PROOF: a bespoke frame fails, the unified one passes ---------

def test_request_direction_bespoke_frame_is_rejected():
    bespoke = {"schema": "decision-envelope/2", "agent_frame": "me",
               "options": [{"id": "opt-1"}], "surround": "loose text",
               "mindset": "", "question": {}}
    assert U.validate_request(bespoke) != []          # hand-rolled -> FAILS
    seen_request = U.build_request(
        lane="decision",
        agent_card={"persona": "me"},
        ask={"text": "redis or memcached?", "kind": "user"},
        extras={"decision": {"options": [{"id": "opt-1", "label": "redis"}],
                             "fork_class": "generic"}},
    )
    assert U.validate_request(seen_request) == []     # unified -> PASSES


def test_assimilation_direction_bespoke_frame_is_rejected():
    bespoke = {"schema": "decision-assimilation/0", "lane": "decision",
               "answer": "advisory", "chosen": "opt-1"}
    assert U.validate_assimilation(bespoke) != []     # hand-rolled -> FAILS
    unified = U.build_assimilation(
        lane="decision", producer="decision", task_id="t",
        answer="advisory",
        extras={"decision": {"options": [{"id": "opt-1"}], "chosen": "opt-1",
                             "resolver_rule": "r1",
                             "outcome_label": "resolved",
                             "diagnosticity": {}}})
    assert U.validate_assimilation(unified) == []     # unified -> PASSES


def test_decision_assimilation_refuses_a_foreign_or_unknown_key():
    request = D.build_envelope("s-m2-refuse", "redis or memcached?",
                               ["redis", "memcached"], "pre")
    # The adapter only ever emits the closed set: prove the registry boundary
    # bites by asking the builder for a frontier key on the decision lane.
    assert U.build_assimilation(lane="decision",
                                extras={"decision": {"pov_claims": []}}) == {}
    env = D.build_decision_assimilation(request, {"choice": "opt-1"})
    assert set(env["extras"]["decision"]) <= set(
        R.ASSIM_EXTRAS_KEYS["decision"])


# --- M34-21: the midturn adapter names the lane ONCE ------------------------

def test_midturn_adapter_passes_an_emitter_lane_and_trigger_once(monkeypatch):
    """decision.py's _emit_decision_assimilation passes lane="decision" through
    the midturn adapter's **kw; the adapter's own lane=/trigger= defaults then
    collided ("got multiple values for keyword argument 'lane'") and the
    assimilation record was swallowed. The emitter's value must win, once."""
    from hermes_router import decision_midturn as dmt
    from hermes_router.core import telemetry

    seen = []
    monkeypatch.setattr(telemetry, "log_route",
                        lambda event, **kw: seen.append((event, dict(kw))))

    dmt._log("sess-m3421", "decision_assimilation", lane="decision",
             chosen="opt-1")
    dmt._log("sess-m3421", "midturn_verdict", choice="opt-1")

    assert [e for e, _ in seen] == ["decision_assimilation", "midturn_verdict"]
    emitter, defaulted = seen[0][1], seen[1][1]
    assert emitter["lane"] == "decision"
    assert emitter["trigger"] == dmt.TRIGGER
    assert emitter["chosen"] == "opt-1"
    # an emitter that names none still gets the lane's defaults
    assert defaulted["lane"] == "decision"
    assert defaulted["trigger"] == dmt.TRIGGER
