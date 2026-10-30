"""Acceptance pins for the unified lane envelope (Goran directive 2026-10-30).

Spec: /opt/data/tmp/unified_envelope_spec.md. One builder, one schema, three
consumers. These tests pin the four acceptance clauses the spec names:

  1. all three lanes produce ``schema: lane-request/1`` with the same core slots;
  2. each lane's extras validate (unknown extras key / unknown lane refused);
  3. one adapter test per lane — the OLD signature still returns the OLD fields,
     and the adapter is proven to DELEGATE to the one builder;
  4. truncation loses ``slice`` first and RECORDS the loss in ``trimmed``.
"""
from __future__ import annotations

import json

from hermes_router.features.envelope import unified as U

CORE_SLOTS = ("schema", "lane", "trigger", "session_id", "ts", "agent_card",
              "ask", "context", "stance", "extras", "budget", "trimmed")


def _request(lane: str, **extras):
    return U.build_request(
        lane=lane,
        trigger="pre",
        session_id="s-unified",
        agent_card={"persona": "PERSONA", "procedures": "PROC", "history": "HIST"},
        ask={"text": "pick a cache layer", "kind": "user"},
        context={"final_objective": "obj", "rolling_task": "task",
                 "session_context": "ctx"},
        stance={"mindset": "calm", "scope": {"risk_class": "normal"}},
        extras={lane: extras},
    )


# --- 1. one schema, three lanes -------------------------------------------

def test_all_three_lanes_produce_the_same_core_schema():
    seen = set()
    for lane in U.LANES:
        env = _request(lane)
        assert env["schema"] == "lane-request/1"
        assert env["lane"] == lane
        assert tuple(env) == CORE_SLOTS, "core slots drifted for lane %r" % lane
        assert set(env["extras"]) == {lane}
        assert env["agent_card"]["persona"] == "PERSONA"
        assert env["ask"] == {"text": "pick a cache layer", "kind": "user"}
        assert env["context"]["session_context"] == "ctx"
        assert env["budget"]["chars_cap"] > 0
        assert U.validate_request(env) == []
        seen.add(lane)
    assert seen == {"decision", "frontier", "uncensored"}


# --- 2. extras validate, per lane -----------------------------------------

def test_unknown_extras_key_is_refused_per_lane():
    for lane in U.LANES:
        assert _request(lane, bogus_key="x") == {}, lane
        errors = U.validate_extras(lane, {lane: {"bogus_key": "x"}})
        assert any("unknown extras" in e for e in errors), errors


def test_unknown_lane_is_refused():
    assert U.build_request(lane="midway") == {}
    assert U.validate_extras("midway", {}) == ["unknown lane: 'midway'"]


def test_lane_extras_are_lane_scoped():
    # a key that is legal for ANOTHER lane is refused here
    assert U.build_request(lane="uncensored",
                           extras={"uncensored": {"options": []}}) == {}
    assert U.build_request(lane="decision",
                           extras={"uncensored": {"leg": "post"}}) == {}


def test_valid_extras_pass_each_lane():
    assert _request("decision", options=[{"id": "opt-1", "label": "redis"}],
                    fork_class="generic", slice=[])["extras"]["decision"]["options"]
    assert _request("frontier", paths=["angle"], consult_kind="consultation")
    assert _request("uncensored", leg="post", work_text="done",
                    voice_mode="voice_stems", close_class="")


def test_bad_input_fails_open_never_raises():
    assert U.build_request(lane="decision",
                           extras={"decision": {"options": "not-a-list"}}) == {}
    assert U.build_request(lane="decision",
                           extras={"decision": {"options": [{"label": "no id"}]}}) == {}
    assert U.build_assimilation(lane="nope") == {}
    assert U.validate_request(None) == ["empty envelope"]


# --- 3. one adapter test per lane (old signature -> old fields) ------------

def test_decision_adapter_keeps_its_fields_and_delegates(monkeypatch):
    from hermes_router import decision

    seen = []
    real = U.build_request
    monkeypatch.setattr(U, "build_request",
                        lambda **kw: seen.append(kw) or real(**kw))

    env = decision.build_envelope("s-unified-dec", "redis or memcached?", [], "pre")

    assert seen and seen[0]["lane"] == "decision", "adapter bypassed the builder"
    for key in ("schema", "agent_frame", "final_objective", "rolling_task",
                "session_context", "scope", "causal_context", "options",
                "weighting", "mindset", "question", "slice", "trigger",
                "fork_class", "digest"):
        assert key in env, "decision envelope lost %r" % key
    assert env["schema"] == decision.ENVELOPE_SCHEMA
    assert [o["id"] for o in env["options"]] == ["opt-1", "opt-2"]
    assert set(env["options"][0]) == {"id", "label", "cause_effect", "cost",
                                      "priors", "risk"}
    assert env["question"]["type"] == "choose_one_with_confidence"
    assert decision.build_envelope("s-unified-dec", "hello there friend",
                                   [], "pre") == {}


def test_povs_adapter_keeps_system_and_messages(monkeypatch):
    from hermes_router import povs

    seen = []
    real = U.build_request
    monkeypatch.setattr(U, "build_request",
                        lambda **kw: seen.append(kw) or real(**kw))

    system, msgs = povs.build_pre_prompt("fix the typo", agent="coder",
                                         role="software engineer",
                                         context="3 files changed")

    assert seen and seen[0]["lane"] == "uncensored", "adapter bypassed the builder"
    assert seen[0]["extras"]["uncensored"]["leg"] == "pre"
    assert isinstance(system, str) and "AGENT: coder" in system
    assert "software engineer" in system
    assert isinstance(msgs, list) and msgs[0]["role"] == "user"
    assert "fix the typo" in msgs[0]["content"]
    assert "SITUATION SO FAR" in msgs[0]["content"]


def test_frontier_adapter_keeps_its_keys_and_delegates(monkeypatch):
    from hermes_router import router_core

    seen = []
    real = U.build_assimilation
    monkeypatch.setattr(U, "build_assimilation",
                        lambda **kw: seen.append(kw) or real(**kw))

    decision = router_core.RouteDecision(
        task_id="t", lane=router_core.LANE_COMPLEXITY,
        mode=router_core.MODE_PLAN, model_target="m", reason="r",
        route_id="rid")
    env = router_core.build_frontier_envelope(
        "frontier_plan", "producer-x", decision, "answer body",
        evidence_refs=["a.md"], limitations="none")

    assert seen and seen[0]["lane"] == "frontier", "adapter bypassed the builder"
    assert env["schema"] == "lane-assimilation/1"
    assert env["kind"] == "frontier_plan"
    assert env["producer"] == "producer-x"
    assert env["route_id"] == "rid"
    assert env["task_id"] == "t"
    assert env["answer"] == "answer body"
    assert env["evidence_refs"] == ["a.md"]
    assert env["limitations"] == "none"
    assert env["ts"]


# --- 4. truncation: ordered, recorded, visible ----------------------------

def test_over_cap_input_drops_slice_first_and_records_it():
    env = U.build_request(
        lane="decision",
        agent_card={"persona": "P"},
        ask={"text": "a"},
        context={"session_context": "c"},
        stance={},
        extras={"decision": {
            "fork_class": "generic",
            "slice": [{"provenance": "p", "id": "1", "snippet": "x" * 4000}],
        }},
        budget={"chars_cap": 1500,
                "drop_order": ["slice", "agent_card.history",
                               "context.session_context"]},
    )
    assert env["trimmed"] == ["extras.decision.slice"]
    assert env["extras"]["decision"]["slice"] == []
    assert env["extras"]["decision"]["fork_class"] == "generic"
    assert env["agent_card"]["persona"] == "P", "an untouched slot was dropped"
    assert env["context"]["session_context"] == "c"
    assert len(json.dumps(env, sort_keys=True)) <= 1500
    assert env["budget"]["chars_cap"] == 1500


def test_under_cap_envelope_records_no_loss():
    env = _request("decision", slice=[{"id": "1"}])
    assert env["trimmed"] == []
    assert env["extras"]["decision"]["slice"] == [{"id": "1"}]
