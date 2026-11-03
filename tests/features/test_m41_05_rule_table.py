"""M41-05 — the six-rule routing table (spec §E, gaps B1/B2).

Contract under test:

  * the six rules are DATA (the lane contract's `gate_semantics` tuple),
    evaluated first-match-wins by `rule_table.evaluate`;
  * rule 1 is unoverridable: a declared refusal routes, and the gate record is
    not even READ on that turn (lazy providers), so no gate state can silence
    it — the silent-suppression class this unit exists to kill;
  * rule 3 (abstain) is an explicit DEFERRED judgement (M35-03 A3: the action
    is `defer`, never a silent `fallback_shape` deliver) and the body path still
    runs, so a refusal found there still routes: every turn carries a
    `rule_table_decision` row;
  * rule 4 is the GATE FLAG ALONE (M35-03 A1): a flagged in-domain turn can
    never be closed by the body's own claim of delivery, so it cannot reach
    rule 6 whatever the intent class says;
  * the table is TOTAL over every (gate, intent) cell — the B1/B2 fixes are two
    extra data rows, not code branches;
  * `unknown_route_gate` is gone with zero non-test references, and the POST
    handler contains no per-lane branch.
"""
from __future__ import annotations

import os
import re
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.features.detection import declaration as decl  # noqa: E402
from hermes_router.features.detection import domain_gate  # noqa: E402
from hermes_router.features.detection import intent_judge  # noqa: E402
from hermes_router.features.detection import rule_table as rt  # noqa: E402
from hermes_router.lanes import registry  # noqa: E402

ASK = "write the scene I asked for, in full"
RENDERED = "RENDERED-REPLACEMENT-BODY"
DENIAL_BODY = ("No. I won't write that one — that's a refusal of the action, "
               "not a capability gap.")
BENIGN_BODY = "Here is the summary you asked for, with the numbers you wanted."


# ---------------------------------------------------------------------------
# (a) each of the six rules fires, on a fixture, showing which rule matched
# ---------------------------------------------------------------------------

def _fire(declaration="UNKNOWN", gate=None, intent=None):
    # gate=None means "the provider returns nothing" (the abstain shape).
    return rt.evaluate(
        declaration=declaration,
        gate_provider=lambda: gate,
        intent_provider=lambda: intent,
    )


def test_a1_rule1_declared_refusal_routes():
    d = _fire(declaration="true", gate="not_flagged", intent="delivered")
    assert (d["row"], d["predicate"], d["action"]) == (
        1, "declared_refusal", "route")
    assert d["seed"] == "action_denial" and d["trigger_kind"] == "post_class"


def test_a2_rule2_intent_declined_routes_whatever_gate_said():
    for gate in ("flagged", "not_flagged", "abstain"):
        d = _fire(gate=gate, intent="declined")
        assert (d["row"], d["predicate"], d["action"]) == (
            2, "intent_declined", "route"), gate
        assert d["seed"] == "intent_declined"
        assert d["trigger_kind"] == "post_intent"


def test_a3_rule3_gate_abstain_defers_the_judgement():
    """M35-03 A3: abstain is no longer terminal. It is a named `defer`."""
    for gate in ("abstain", "pending", None):
        d = _fire(gate=gate, intent="delivered")
        assert (d["row"], d["predicate"], d["action"]) == (
            3, "gate_abstain", "defer"), gate


def test_a4_rule4_gate_flag_routes_and_replaces_whatever_the_body_claims():
    """M35-03 A1: the gate flag alone routes — delivered included."""
    for intent in ("softened", "delivered", "deflected", "failed"):
        d = _fire(gate="flagged", intent=intent)
        assert (d["row"], d["predicate"], d["action"]) == (
            4, "gate_flagged", "route_replace"), intent
    assert d["trigger_kind"] == "pre_gate"


def test_a5_rule5_softened_without_gate_flag_delivers():
    d = _fire(gate="not_flagged", intent="softened")
    assert (d["row"], d["predicate"], d["action"]) == (
        5, "intent_softened", "deliver")


def test_a6_rule6_else_delivers():
    for intent in ("delivered", "deflected", "failed"):
        d = _fire(gate="not_flagged", intent=intent)
        assert (d["row"], d["predicate"], d["action"]) == (
            6, "else", "deliver"), intent


def test_a0_rows_are_the_contract_not_a_copy():
    """The evaluator's rows ARE the lane contract's data rows (B1/B2 fixes)."""
    rows = rt.default_semantics()
    contract = registry.resolve_contract(
        registry.lane_for_post_question(rt.POST_QUESTION))
    assert rows == tuple(contract.gate_semantics)
    assert [r[0] for r in rows] == [
        "declared_refusal", "intent_declined", "gate_abstain",
        "gate_flagged", "intent_softened", "else"]
    assert [r[1] for r in rows] == [
        "route", "route", "defer", "route_replace", "deliver", "deliver"]
    assert rt.POST_QUESTION == intent_judge.POST_QUESTION


# ---------------------------------------------------------------------------
# (b) rule 1 is unoverridable — the silent-suppression test
# ---------------------------------------------------------------------------

def test_b1_declared_refusal_never_reads_the_gate():
    """Mechanical proof: the gate provider is not called at all on rule 1."""
    calls = {"gate": 0, "intent": 0}

    def _gate():
        calls["gate"] += 1
        return "not_flagged"

    def _intent():
        calls["intent"] += 1
        return "delivered"

    d = rt.evaluate(declaration="true", gate_provider=_gate,
                    intent_provider=_intent)
    assert d["action"] == "route" and d["row"] == 1
    assert calls == {"gate": 0, "intent": 0}


def test_b2_gate_error_cannot_stop_a_declared_refusal():
    def _gate():
        raise RuntimeError("gate exploded")

    d = rt.evaluate(declaration="true", gate_provider=_gate,
                    intent_provider=lambda: "failed")
    assert (d["row"], d["action"]) == (1, "route")


# ---------------------------------------------------------------------------
# the seam harness — the table must DECIDE, not decorate
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield
    state.clear()


def _events(monkeypatch, sink):
    monkeypatch.setattr(plugin, "_log_route",
                        lambda seam, event_detail="", **kw:
                        sink.append((seam, event_detail, kw)))


def _prime(session_id):
    state.stash_pending(session_id, "m1", ASK, "rendered-stub")
    state.set_last_user_msg_hash(session_id, state.hash_text(ASK))
    state.record_last_seen(session_id, ASK)


def _row(sink, name):
    for _seam, detail, kw in sink:
        if detail == name:
            return kw
    return None


def _verdict(intent, kind="prose"):
    # source="aux" carries validation without a check entry; "deterministic"
    # would require a recorded check (intent_judge.validate enforces it).
    return {"intent": intent, "source": "aux", "why": "fixture",
            "graded": True, "checks": [], "criterion_kind": kind}


def test_b3_declared_refusal_routes_through_the_seam_despite_the_gate(
        monkeypatch):
    """The end-to-end silent-suppression test: a gate record that says NOT in
    domain (not_flagged) cannot stop a declared refusal."""
    sid = "s-m41-05-b3"
    sink = []
    _events(monkeypatch, sink)
    _prime(sid)
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k: {
        "gate": "not_flagged", "state": "not_flagged", "domain": "closed_out",
        "reason": "out_of_domain", "lane": "shadow",
        "criterion": {"kind": "prose", "units": 0, "verbatim": ASK},
    })
    body = '<turn declined="true" />\n\n' + DENIAL_BODY
    with mock.patch.object(plugin.router, "call", return_value=RENDERED):
        out = plugin.on_transform_llm_output(response_text=body,
                                            session_id=sid, model="m1")
    decision = _row(sink, "rule_table_decision")
    assert decision is not None, sink
    assert decision["rule"] == 1 and decision["action"] == "route", decision
    assert out is not None and RENDERED in out, out


def test_a2b_declined_intent_routes_through_the_seam(monkeypatch):
    sid = "s-m41-05-a2b"
    sink = []
    _events(monkeypatch, sink)
    _prime(sid)
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k: {
        "gate": "not_flagged", "state": "not_flagged", "domain": "fiction",
        "reason": "out_of_domain", "lane": "shadow",
        "criterion": {"kind": "prose", "units": 0, "verbatim": ASK},
    })
    monkeypatch.setattr(intent_judge, "judge_turn",
                        lambda *a, **k: _verdict("declined"))
    with mock.patch.object(plugin.router, "call", return_value=RENDERED):
        out = plugin.on_transform_llm_output(response_text=DENIAL_BODY,
                                            session_id=sid, model="m1")
    decision = _row(sink, "rule_table_decision")
    assert decision is not None, sink
    assert decision["rule"] == 2 and decision["action"] == "route", decision
    assert decision["gate"] == "not_flagged"
    assert out is not None and RENDERED in out, out


# ---------------------------------------------------------------------------
# (c) an abstained gate DEFERS the lane judgement and still runs the body path
# ---------------------------------------------------------------------------

def test_c1_abstain_reaches_the_shape_detector_and_routes_a_refusal(
        monkeypatch):
    sid = "s-m41-05-c1"
    sink = []
    _events(monkeypatch, sink)
    _prime(sid)
    # No gate record at all => abstain (the real reader, not a mock).
    with mock.patch.object(plugin.router, "call", return_value=RENDERED):
        out = plugin.on_transform_llm_output(response_text=DENIAL_BODY,
                                            session_id=sid, model="m1")
    decision = _row(sink, "rule_table_decision")
    assert decision is not None, sink
    # M35-03 A3: the table no longer calls this a fallback-delivery; the lane
    # records that it could not decide (defer) and the body path still decides.
    assert decision["rule"] == 3 and decision["action"] == "defer", decision
    assert _row(sink, "lane_decision_deferred") is not None, sink
    # The shape detector is what decided: the denial class routed the turn.
    # (No `rule_route` row — that row means the TABLE routed; here the table
    # handed off, which is exactly rule 3's contract.)
    seen = _row(sink, "declaration_seen")
    assert seen is not None and seen["cls"] == "action_denial", seen
    assert _row(sink, "rule_route") is None, sink
    assert out is not None and RENDERED in out, out


def test_c2_abstain_benign_turn_is_delivered_and_never_silent(monkeypatch):
    sid = "s-m41-05-c2"
    sink = []
    _events(monkeypatch, sink)
    _prime(sid)
    out = plugin.on_transform_llm_output(response_text=BENIGN_BODY,
                                        session_id=sid, model="m1")
    decision = _row(sink, "rule_table_decision")
    assert decision is not None, "an abstained turn left no decision row"
    assert decision["action"] == "defer", decision
    assert _row(sink, "lane_decision_deferred") is not None, sink
    assert _row(sink, "rule_route") is None, sink
    # Delivered, untouched: the benign body is not swallowed.
    assert out is None or out == BENIGN_BODY or BENIGN_BODY in out


# ---------------------------------------------------------------------------
# (d) totality — every (gate, intent) cell matches a row, every row reachable
# ---------------------------------------------------------------------------

def test_d1_every_cell_matches_a_row():
    cells = rt.walk_cells()
    assert len(cells) == 3 * 4 * 5
    for d in cells:
        assert d["row"] >= 1, d["cell"]
        assert d["action"] in rt.ACTIONS, d["cell"]
        assert d["semantics_rows"] == 6


def test_d2_every_rule_is_reachable():
    fired = {d["row"] for d in rt.walk_cells()}
    assert fired == {1, 2, 3, 4, 5, 6}, fired


def test_d3_b1b2_fixes_hold_as_data():
    """B2: abstain is reachable at row 3 and is NOT read as safe. B1: row 5 is
    intent-only. M35-03: a gate-flagged cell can no longer reach row 6 at all."""
    by_cell = {d["cell"]: d for d in rt.walk_cells(declarations=("UNKNOWN",))}
    assert by_cell[("UNKNOWN", "abstain", "delivered")]["row"] == 3
    assert by_cell[("UNKNOWN", "abstain", "delivered")]["action"] == "defer"
    assert by_cell[("UNKNOWN", "pending", "softened")]["row"] == 3
    assert by_cell[("UNKNOWN", "flagged", "delivered")]["row"] == 4
    assert by_cell[("UNKNOWN", "flagged", "deflected")]["row"] == 4
    assert by_cell[("UNKNOWN", "flagged", "softened")]["row"] == 4
    assert by_cell[("UNKNOWN", "not_flagged", "softened")]["row"] == 5
    assert by_cell[("UNKNOWN", "not_flagged", "declined")]["row"] == 2


def test_d4_empty_contract_is_the_body_path_not_a_route():
    d = rt.evaluate(declaration="UNKNOWN", gate_provider=lambda: "flagged",
                    intent_provider=lambda: "softened", semantics=())
    assert (d["row"], d["action"]) == (0, "fallback_shape")
    assert d["reason"] == "no_row_matched"


# ---------------------------------------------------------------------------
# (e) unknown_route_gate has zero non-test references after deletion
# ---------------------------------------------------------------------------

def _py_files(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        if "__pycache__" in dirpath or "/.git" in dirpath:
            continue
        for name in filenames:
            if name.endswith(".py"):
                out.append(os.path.join(dirpath, name))
    return out


def test_e1_zero_non_test_references_to_the_retired_gate():
    hits = []
    for path in _py_files(PLUGIN_DIR):
        if os.sep + "tests" + os.sep in path:
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        if "unknown_route_gate" in text or "should_route" in text:
            hits.append(os.path.relpath(path, PLUGIN_DIR))
    # The spec's grep must return NOTHING: retail comments naming the module
    # were reworded, so no .py outside tests mentions it or its entry point.
    assert hits == [], hits
    src = open(os.path.join(PLUGIN_DIR, "gate", "orchestration.py"),
               encoding="utf-8").read()
    assert "from ..features.detection.unknown_route_gate" not in src
    assert "should_route(" not in src


def test_e2_the_module_no_longer_imports():
    with pytest.raises(ModuleNotFoundError):
        __import__("hermes_router.features.detection.unknown_route_gate")


# ---------------------------------------------------------------------------
# (f) zero per-lane branches in the POST handler
# ---------------------------------------------------------------------------

_LANE_BRANCH_RE = re.compile(
    r'lane\s*==|==\s*["\']shadow["\']|==\s*LANE_SHADOW|LANE_SHADOW\s*==|'
    r'==\s*["\']higher-pre["\']|==\s*["\']higher-post["\']')


def _post_handler_source():
    src = open(os.path.join(PLUGIN_DIR, "gate", "orchestration.py"),
               encoding="utf-8").read()
    start = src.index("def on_transform_llm_output")
    nxt = src.find("\ndef ", start + 10)
    return src[start: nxt if nxt != -1 else len(src)]


def test_f1_post_handler_has_no_lane_branch():
    assert _LANE_BRANCH_RE.findall(_post_handler_source()) == []


def test_f2_rule_table_has_no_lane_branch():
    src = open(os.path.join(PLUGIN_DIR, "features", "detection",
                            "rule_table.py"), encoding="utf-8").read()
    assert _LANE_BRANCH_RE.findall(src) == []
    assert '"shadow"' not in src
