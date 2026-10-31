"""Pins for phase-2 U3: the frontier lane plugged into both directions.

Plan §3.2-3.4. The frontier lane was the ONE lane with no request envelope and
a hand-rolled assimilation envelope. These pins hold:

  a) the REQUEST envelope is a lane-request/1 carrying all four POV paths
     (extras.frontier.paths, filled from povs.POV_SET — the slot the registry
     declared and nothing populated), plus consult_kind / route_id / kind;
  b) the ASSIMILATION envelope carries pov_claims with a winning voice and the
     dials that winner emerged from;
  c) absent claims yield an EMPTY list — no crash, no fabricated claim;
  d) the hand-rolled fallback dict is gone: a builder refusal is {} here too;
  e) every key the lane published before keeps its name 1:1.
"""
from __future__ import annotations

import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router import anchor_chain, anchor_exec, povs, router_core  # noqa: E402
from hermes_router.features.envelope import unified as U  # noqa: E402

CLAIM_BODY = (
    "Before anything else, the four voices:\n"
    "explorer: 0.2 | widen to the callers of this function\n"
    "verifier: 0.3 | the test must actually run\n"
    "skeptic: 0.9 | the premise that this is new is unverified\n"
    "operator: 0.1 | just make the edit\n"
    "gates: precedent=0 consequence=0\n"
    "\nThis is the advisory body itself, delivered to the agent.\n"
)


def _decision(**over):
    kw = dict(task_id="t-u3", lane=router_core.LANE_COMPLEXITY,
              mode=router_core.MODE_PLAN, model_target="m", reason="r",
              route_id="rid-u3")
    kw.update(over)
    return router_core.RouteDecision(**kw)


# --- (a) request direction: all four POV paths ----------------------------

def test_request_envelope_carries_all_four_pov_paths():
    env = router_core.build_frontier_request(
        "should this fork go to the hook or the resolver?",
        consult_kind="consultation", kind="consultation",
        route_id="rid-u3", session_id="s-u3", evidence_refs=["a.md"])

    assert env["schema"] == "lane-request/1"
    assert env["lane"] == "frontier"
    frontier = env["extras"]["frontier"]
    assert frontier["paths"] == [p.name for p in povs.POV_SET]
    assert frontier["paths"] == ["explorer", "verifier", "skeptic", "operator"]
    assert frontier["consult_kind"] == "consultation"
    assert frontier["route_id"] == "rid-u3"
    assert frontier["kind"] == "consultation"
    assert frontier["evidence_refs"] == ["a.md"]
    assert U.validate_request(env) == []


def test_request_paths_track_pov_set_not_a_copy():
    # a voice added to the one POV set must appear in the record with no edit
    env = router_core.build_frontier_request("ask", consult_kind="verification")
    assert env["extras"]["frontier"]["paths"] == [p.name for p in povs.POV_SET]
    assert env["extras"]["frontier"]["kind"] == "verification"


def test_request_envelope_refuses_bad_lane_input_without_raising():
    # never raises, and never invents a lane record when the ask is unusable
    assert isinstance(router_core.build_frontier_request(None), dict)
    env = router_core.build_frontier_request("")
    assert env["extras"]["frontier"]["paths"] == [p.name for p in povs.POV_SET]


def test_anchor_pre_consult_records_the_request_envelope(monkeypatch):
    """The real PRE seam: the envelope is built and stashed on the record."""
    rec = {
        "task_id": "t-u3-anchor",
        "mode": router_core.MODE_CONSULT,
        "endpoint": anchor_chain.parse_anchor_uri("openrouter://test/model-a",
                                                  "primary"),
        "route_id": "r-u3-anchor",
        "orientation": True,
        "reason": "complexity_orientation",
    }
    sent = []

    def _call(endpoint, api_kwargs):
        sent.append(api_kwargs)
        return ("ORIENTATION BRIEF\n- shape: done", 0.0, 10, 5)

    monkeypatch.setattr(router_core, "pending_model_swap", lambda sid: rec)
    monkeypatch.setattr(router_core, "clear_anchor_backoff", lambda *a, **k: None)
    monkeypatch.setattr(anchor_exec, "_retry_anchored_async", lambda *a, **k: None)
    monkeypatch.setattr(anchor_exec, "anchored_call", _call)
    monkeypatch.setattr(anchor_chain, "record_spend", lambda *a, **k: None)

    anchor_exec.maybe_execute_anchored(
        "s-u3-anchor", {"messages": [{"role": "user", "content": "fix the typo"}]})

    assert sent, "PRE consult never fired"
    env = rec.get("request_envelope")
    assert env and env["schema"] == "lane-request/1", "no request record kept"
    assert env["extras"]["frontier"]["paths"] == ["explorer", "verifier",
                                                  "skeptic", "operator"]
    assert env["extras"]["frontier"]["route_id"] == "r-u3-anchor"
    assert env["extras"]["frontier"]["consult_kind"] == "orientation"
    # the FRAME is untouched: the claim instruction still rides the consult
    assert povs.claim_instruction().strip()[:20] in str(
        sent[0]["messages"][-1]["content"])


# --- (b) assimilation carries pov_claims with a winning voice -------------

def test_assimilation_carries_pov_claims_and_the_winning_voice():
    env = router_core.build_frontier_envelope(
        "consultation", "anchor-primary", _decision(), CLAIM_BODY,
        evidence_refs=["src/x.py"], limitations="single shot")

    assert env["schema"] == "lane-assimilation/1"
    frontier = env["extras"]["frontier"]
    claims = frontier["pov_claims"]
    assert [c["voice"] for c in claims] == ["explorer", "verifier",
                                            "skeptic", "operator"]
    assert [c["weight"] for c in claims] == [0.2, 0.3, 0.9, 0.1]
    assert claims[2]["note"] == "the premise that this is new is unverified"
    winners = [c["voice"] for c in claims if c["winning"]]
    assert winners == ["skeptic"], "winning voice not carried"
    assert winners == [povs.winning_voice({"explorer": 0.2, "verifier": 0.3,
                                          "skeptic": 0.9, "operator": 0.1})]
    assert frontier["dials"] == povs.dials_from_claims(
        {"explorer": 0.2, "verifier": 0.3, "skeptic": 0.9, "operator": 0.1})
    assert U.validate_assimilation(env) == []


def test_assimilation_prefers_claims_parsed_by_the_caller():
    # the PRE seam has already split the machine channel off the body
    env = router_core.build_frontier_envelope(
        "consultation", "p", _decision(), "clean advisory body only",
        claims={"verifier": 0.8}, notes={"verifier": "run the test"})
    claims = env["extras"]["frontier"]["pov_claims"]
    assert [c["voice"] for c in claims if c["winning"]] == ["verifier"]
    assert [c["note"] for c in claims if c["voice"] == "verifier"] == ["run the test"]


# --- (c) absent claims: empty list, never a crash or a fabricated claim ---

def test_absent_claims_yield_an_empty_list_not_a_crash():
    for body in ("", "plain advisory with no claim block at all", None):
        env = router_core.build_frontier_envelope(
            "consultation", "p", _decision(), body if body is not None else "")
        assert env, "the envelope must still build without claims"
        assert env["extras"]["frontier"]["pov_claims"] == []
        assert "dials" not in env["extras"]["frontier"]
        assert U.validate_assimilation(env) == []


def test_legacy_mindset_block_is_not_read_as_pov_claims():
    env = router_core.build_frontier_envelope(
        "consultation", "p", _decision(),
        'MINDSET_TARGET {"scope": 0.4, "rigour": -0.2}\nsome prose')
    assert env["extras"]["frontier"]["pov_claims"] == []


# --- (d) no hand-rolled fallback: a refusal is {} ------------------------

def test_builder_refusal_is_not_masked_by_a_fallback_dict(monkeypatch):
    monkeypatch.setattr(U, "build_assimilation", lambda **kw: {})
    assert router_core.build_frontier_envelope(
        "consultation", "p", _decision(), "body") == {}


# --- (e) consumer keys keep their names 1:1 ------------------------------

def test_frontier_consumer_keys_unchanged():
    env = router_core.build_frontier_envelope(
        "frontier_plan", "producer-x", _decision(), "answer body",
        evidence_refs=["a.md"], limitations="none")
    for key in ("kind", "producer", "route_id", "task_id", "answer",
                "evidence_refs", "limitations", "ts"):
        assert key in env, "frontier consumer key %r disappeared" % key
    assert env["kind"] == "frontier_plan"
    assert env["producer"] == "producer-x"
    assert env["route_id"] == "rid-u3"
    assert env["task_id"] == "t-u3"
    assert env["answer"] == "answer body"
    assert env["evidence_refs"] == ["a.md"]
    assert env["limitations"] == "none"
