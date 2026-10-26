"""R64 — the reflex lane: the decision model discovers and weighs candidate actions.

Shape defended here (and why it is this shape):

  ONE NOUL PER CANDIDATE, never a single `choice` over the set. A choice distribution is
  forced to sum to 1, so three genuinely viable actions come back as ~0.33 each and the block
  reads "we are unsure" when the truth is "all three are live". Independent nouls let several
  candidates score high at once (85% / 78% / 71%) — a discovery read rather than a forced
  pick — and remove the normalization artifact entirely.

What broke before, in order:

1. A regex decided what counted as a fork, so 62-97% of billed consults carried no real
   option set. Detection is now empirical: at least two candidates independently viable.
2. Binary `opt-1` at ~0.32 threw away the margin, which is the whole point.
3. A parked note must be delivered once, on the request surface, and never persisted.
4. Nothing here may raise into a turn.
"""
from __future__ import annotations

import pytest

from hermes_router import reflex_lane as rl

ON = {"hermes_router": {"reflex_weights": {"enabled": True}}}
OFF = {"hermes_router": {}}

# Exactly what candidates() derives from a traceback naming src/render.py.
CANDS = ["open src/render.py", "fix src/render.py", "stop and report what is known so far",
         "ask the user which way to go"]


def _answers(viable=(0.85, 0.78, 0.20, 0.10), scope=3, rigour=2, fork=0.8):
    """Independent per-candidate viability, plus degree questions."""
    a = {"fork": {"noul": fork}}
    for i, p in enumerate(viable, start=1):
        a["c%d" % i] = {"noul": p}
    a["scope"] = {"score": scope, "confidence": 0.6}
    a["rigour"] = {"score": rigour, "confidence": 0.6}
    return {"answers": a}


def test_disabled_by_default_emits_nothing(monkeypatch):
    """A plugin upgrade must never arm this fleet-wide."""
    assert rl.enabled(OFF) is False
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: pytest.fail("must not call"))
    assert rl.fire("s1", "terminal", "x" * 400, OFF, ask="do the thing") == ""


def test_two_viable_candidates_are_a_fork(monkeypatch):
    """Several candidates can be high at once — the discovery shape."""
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _answers())
    block = rl.fire("s1", "terminal", "src/render.py raised IndexError\n" + "z" * 200, ON)
    assert block
    assert "85%" in block and "78%" in block
    assert block.index("85%") < block.index("78%"), "highest first"


def test_one_viable_candidate_is_silent(monkeypatch):
    """Evidence pointing one way is not a fork; speaking there is the old defect."""
    monkeypatch.setattr(rl, "_transport",
                        lambda *a, **k: _answers(viable=(0.9, 0.12, 0.05, 0.02)))
    assert rl.fire("s2", "terminal", "src/render.py ok\n" + "z" * 200, ON) == ""


def test_weakened_compliance_low_all_round_is_silent(monkeypatch):
    """All-low is not a fork either: nothing is worth steering toward."""
    monkeypatch.setattr(rl, "_transport",
                        lambda *a, **k: _answers(viable=(0.3, 0.25, 0.2, 0.1)))
    assert rl.fire("s3", "terminal", "src/render.py\n" + "z" * 200, ON) == ""


def test_weights_are_not_renormalized(monkeypatch):
    """0.85 and 0.78 must survive as themselves; forcing them to sum to 1 would lie."""
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _answers())
    parsed = rl.parse_answers(_answers(), CANDS)
    assert parsed["options"][0]["p"] == pytest.approx(0.85)
    assert parsed["options"][1]["p"] == pytest.approx(0.78)
    assert parsed["viable"] == 2


def test_hallucinated_candidate_is_ignored(monkeypatch):
    """A weight for an action we never offered is not an option."""
    ans = _answers()
    ans["answers"]["c5"] = {"noul": 0.99}
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: ans)
    parsed = rl.parse_answers(ans, CANDS)
    assert len(parsed["options"]) == len(CANDS)


def test_mindset_delta_maps_five_levels_onto_the_dial(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _answers(scope=4, rigour=0))
    block = rl.fire("s4", "terminal", "src/app.py broke\n" + "z" * 200, ON)
    assert "scope +1.00" in block and "rigour -1.00" in block


def test_park_is_consumed_exactly_once(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _answers())
    rl.clear("s5")
    assert rl.fire("s5", "terminal", "src/render.py failed\n" + "z" * 200, ON)
    req = {"messages": [{"role": "user", "content": "hi"}]}
    assert rl.inject_pass(req, "s5") is True
    assert "85%" in req["messages"][-1]["content"]
    assert rl.inject_pass(req, "s5") is False, "a reading must not be delivered twice"


def test_delivered_reading_is_attributable(monkeypatch):
    rl.clear("s6")
    rl._park("s6", "block body")
    req = {"messages": [{"role": "user", "content": "hi"}]}
    assert rl.inject_pass(req, "s6")
    body = req["messages"][-1]["content"]
    assert "reflex" in body.lower() and "block body" in body


def test_stale_reading_is_dropped_not_delivered_late(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _answers())
    rl.clear("s7")
    rl.fire("s7", "terminal", "src/render.py\n" + "z" * 200, ON)
    with rl._PENDING_LOCK:
        rl._PENDING["s7"]["ts"] = 0.0
    assert rl.take("s7") == ""


def test_thin_tool_result_never_calls_the_model(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: pytest.fail("must not call"))
    assert rl.fire("s8", "terminal", "ok", ON) == ""


def test_transport_failure_fails_open(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: None)
    assert rl.fire("s9", "terminal", "src/app.py\n" + "z" * 200, ON) == ""


def test_over_budget_block_emits_nothing(monkeypatch):
    parsed = {"options": [{"action": "a" * 200, "p": 0.6}, {"action": "b" * 200, "p": 0.5}],
              "mindset": {"scope": 0.5, "rigour": -0.5}}
    assert rl.render(parsed, limit=120) == ""


def test_candidates_are_assembled_without_judging_them():
    """Assembly must not filter on plausibility — viability is the noul's job."""
    got = rl.candidates("terminal", "Traceback in /opt/data/app/src/render.py line 12")
    assert any("render.py" in c for c in got)
    assert len(got) >= 2


def test_one_noul_per_candidate_not_one_choice_over_all():
    """The payload shape that makes several highs possible."""
    p = rl.payload("state", ["a", "b", "c"])
    assert "choice" not in [q.get("type") for q in p["questions"].values()]
    nouls = [q for q in p["questions"].values() if q.get("type") == "noul"]
    assert len(nouls) >= 3 + 1, "one per candidate plus the fork question"
