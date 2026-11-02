"""M41-07b — the ledger's `replace` flag must equal the delivery that happened.

DEFECT (M41-07_FALSIFY §9 D1). `_REPLACING_PREDICATES` named only rules 1 and 4,
so a rule-2 (`intent_declined`) route logged `replace=0` and `replacement=""`
while the POST seam rendered and swapped the body unconditionally for every
route. Two rows on the same turn disagreed about whether the text the user read
was the model's own — and M41-08's per-rule replacement census, derived from
`replace=1`, would have missed every rule-2 turn.

CONTRACT PINNED HERE, in three layers:

  1. every routing row the table can return reports `replace=True` — data-level,
     so a future row cannot reintroduce the disagreement;
  2. every cell of the totality walk that routes agrees with (1);
  3. the handler-level turn: for EACH routing rule, the flag on
     `rule_table_decision` equals the OBSERVED delivery — a render happened, the
     swap row landed, and the original body did not survive. The observation is
     the assertion, not the field read back.
"""
from __future__ import annotations

import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import hermes_router as plugin  # noqa: E402
from hermes_router import assimilation, state  # noqa: E402
from hermes_router.features.detection import domain_gate, intent_judge  # noqa: E402
from hermes_router.features.detection import rule_table as rt  # noqa: E402

ASK = "write the scene I asked for, in full"

RENDERED = "RENDERED-REPLACEMENT-BODY"

DECLINED_BODY = "I have to decline this one: the request is for the thing itself."
DECLARED_BODY = '<turn declined="true" />\n\n' + DECLINED_BODY

FLAGGED = {"gate": "flagged", "state": "flagged", "domain": "sexual_explicit",
           "reason": "in_domain", "lane": "shadow",
           "criterion": {"kind": "prose", "units": 0, "verbatim": ASK}}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    assimilation.clear_marks()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield
    state.clear()
    assimilation.clear_marks()


def _row(sink, name):
    for _s, detail, kw in sink:
        if detail == name:
            return kw
    return None


def _setup(monkeypatch, sink):
    monkeypatch.setattr(plugin, "_log_route",
                        lambda seam, event_detail="", **kw:
                        sink.append((seam, event_detail, kw)))
    entries = [{"name": "primary", "model": "m-primary"}]
    monkeypatch.setattr(plugin.router, "_chain_entries", lambda: entries)
    monkeypatch.setattr(plugin.router, "_model_attempt",
                        lambda entry, prompt, **kw: (RENDERED, ""))
    monkeypatch.setattr(plugin.canonical, "already_committed_for_turn",
                        lambda *a, **k: False)
    monkeypatch.setattr(plugin.canonical, "commit_canonical_event",
                        lambda *a, **k: True)
    monkeypatch.setattr(plugin.canonical, "rewrite_persisted_turn",
                        lambda *a, **k: True)
    monkeypatch.setattr(plugin.canonical, "verify_persisted_turn",
                        lambda *a, **k: True)


def _run(monkeypatch, sid, body, gate, intent):
    sink = []
    _setup(monkeypatch, sink)
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k: dict(gate))
    monkeypatch.setattr(intent_judge, "judge_turn",
                        lambda *a, **k: {"intent": intent, "source": "aux",
                                         "why": "fixture", "graded": True,
                                         "checks": [], "criterion_kind": "prose"})
    state.stash_pending(sid, "m1", ASK, "stub")
    state.set_last_user_msg_hash(sid, state.hash_text(ASK))
    state.record_last_seen(sid, ASK)
    out = plugin.on_transform_llm_output(response_text=body,
                                         session_id=sid, model="m1")
    return sink, str(out or "")


# ---------------------------------------------------------------------------
# 1 + 2 — the table's own answer, every row and every cell
# ---------------------------------------------------------------------------
ROUTING_ACTIONS = (rt.ACTION_ROUTE, rt.ACTION_ROUTE_REPLACE)


_PREDICATE_STATE = {
    rt.PRED_DECLARED_REFUSAL: dict(declaration="true", gate="flagged",
                                   intent="delivered"),
    rt.PRED_INTENT_DECLINED: dict(declaration="UNKNOWN", gate="flagged",
                                  intent="declined"),
    rt.PRED_SOFTENED_AND_GATE: dict(declaration="UNKNOWN", gate="flagged",
                                    intent="softened"),
}


def test_every_routing_row_reports_replace():
    routing = [(p, a) for p, a in rt.default_semantics() if a in ROUTING_ACTIONS]
    assert routing, "the contract has no routing rows — instrument is blind"
    for predicate, action in routing:
        fixture = _PREDICATE_STATE[predicate]
        decision = rt.evaluate(declaration=fixture["declaration"],
                               gate_provider=lambda g=fixture["gate"]: g,
                               intent_provider=lambda i=fixture["intent"]: i,
                               semantics=[(predicate, action)])
        assert decision["row"] == 1, decision
        assert decision["replace"] is True, (
            f"{predicate} routes but reports replace=False — the ledger would "
            f"deny a swap that happens")


def test_every_routing_cell_reports_replace():
    for decision in rt.walk_cells():
        if decision["action"] in ROUTING_ACTIONS:
            assert decision["replace"] is True, decision


# ---------------------------------------------------------------------------
# 3 — the handler: flag == observed delivery, per routing rule
# ---------------------------------------------------------------------------
_RULE_CASES = (
    # (label, body, gate, judge intent, expected row)
    ("rule1_declared", DECLARED_BODY, dict(FLAGGED), "delivered", 1),
    ("rule2_declined", DECLINED_BODY, dict(FLAGGED), "declined", 2),
    ("rule4_softened_flagged", DECLINED_BODY, dict(FLAGGED), "softened", 4),
)


@pytest.mark.parametrize("label,body,gate,intent,row",
                         _RULE_CASES,
                         ids=[c[0] for c in _RULE_CASES])
def test_replace_flag_equals_observed_delivery(monkeypatch, label, body, gate,
                                               intent, row):
    sink, out = _run(monkeypatch, f"m41-07b-{label}", body, gate, intent)
    decision = _row(sink, "rule_table_decision")
    route_row = _row(sink, "rule_route")
    swap_row = _row(sink, "render_swap_decision")

    assert decision is not None, f"{label}: no rule_table_decision row"
    assert decision["rule"] == row, (label, decision)

    observed = bool(RENDERED in out and body not in out and swap_row)
    assert decision["replace"] == int(observed), (
        f"{label}: ledger replace={decision['replace']} but observed "
        f"delivery={'swap' if observed else 'no_swap'} "
        f"(rendered={RENDERED in out}, original_survives={body in out}, "
        f"swap_row={bool(swap_row)})")
    assert route_row is not None and route_row["replace"] == decision["replace"]
