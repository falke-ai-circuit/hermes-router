"""Rider 15 — T1#8 verdict fixes (v4.16.5 -> 4.16.6 scope).

R15-5 bannerless delivery on the new phrase forms: root causes fixed —
  (a) refusal-shaped-but-technical and honored-agent-line delivery edges
      returned None BEFORE the benign consume (live: analyst T2e 'consult
      luna-pro', banner parked 15:26:47, flinch passthrough 15:29:42,
      consume only at 15:29:55 on a later edge + parked_capture_failed);
  (b) maybe_execute_anchored swallowed exceptions with a debug log —
      the silent-zero signature (claim staged=True, no events, no spend,
      no banner).
R15-6 'decide on this' joins the trusted manual trigger (phrase family).
R15-7 the benign-brief-frame gate now covers the PRE complexity/anchor
  consult leg (D1a residual: benign setup brief still billed the anchor
  consult — ledger row frontier_adversarial_parse_fail).
R15-8/F4 option-less 'decide (on) this: <open question>' routes as a
  FRONTIER consult, never a Jev menu pick; prose two-option forms stay in
  the decision lane.
F1 'thos' fixture: Damerau-ed-1 tolerance on the manual trigger tokens
  ('thos this:' fires the same lane as 'decide this:'); 'fromtier'/
  'cosult' stay covered by the R15 LEG-2 fuzzy consult family.

Grading contract (R13-4 binding): the four legs verify the DELIVERED BODY
string, never log lines alone. Probes here are development harnesses.
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
from hermes_router import (anchor_exec, decision, debug_banner as DB,  # noqa: E402
                           render_inbox, router_core, route_gate,
                           usage_ledger)

SID = "s-rider15"


@pytest.fixture()
def r15_reset(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    plugin.state.reset_turn_identity(SID)
    DB._ANCHOR_BANNERS.clear()
    DB._ANCHOR_TASKS.clear()
    DB._ANCHOR_SEGS.clear()
    DB._HELD_DECISIONS.clear()
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr(render_inbox, "_inbox_path",
                        lambda: str(tmp_path / "renders.jsonl"))
    monkeypatch.setattr(DB, "_banner_section",
                        lambda: {"debug_banner": 1}, raising=True)
    return tmp_path


# ---------------------------------------------------------------------------
# R15-6 + F1: manual trigger family + typo tolerance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "decide on this: deploy window. Option A tonight. Option B tomorrow "
    "morning. Pick one.",
    "now the real one, decide on this: Option A keep, Option B revert.",
    "decied this: Option A x. Option B y. Pick one.",   # transposition typo
    "decide thos: Option A x. Option B y. Pick one.",   # object typo
    "thos this: Option A invert the fallback. Option B keep it. Pick one.",
])
def test_manual_trigger_family_and_typo_tolerance(text):
    assert decision._manual_trigger_in_text(text), text


@pytest.mark.parametrize("text", [
    "the doc says decide this without colon is prose",
    "we will decide this later, no colon form",
    "review the tradeoffs and decide this at the end",
])
def test_manual_trigger_prose_negative(text):
    assert not decision._manual_trigger_in_text(text), text


def test_declared_frontier_family_forms():
    # (a) second declared frontier form routes (phrase-family drift catch)
    assert route_gate.detect_declared_user(
        "ask your higher self: is the two-pass dispatcher refactor the "
        "right long-term call for the fleet?") == route_gate.LANE_HIGHER_PRE
    # F1 fixtures: 'fromtier'/'cosult' route via the R15 LEG-2 fuzzy family
    assert route_gate.detect_declared_user(
        "fromtier this: what is the risk?") == route_gate.LANE_HIGHER_PRE
    assert route_gate.detect_declared_user(
        "cosult frontier: is this right?") == route_gate.LANE_HIGHER_PRE


# ---------------------------------------------------------------------------
# R15-8 / F4: option-less decide -> frontier consult
# ---------------------------------------------------------------------------

def _dec_cfg(monkeypatch):
    monkeypatch.setattr(router_core, "_decision_cfg",
                        lambda: {"enabled": True, "level": 3})
    monkeypatch.setattr(decision, "_cfg",
                        lambda: {"enabled": True, "level": 3})


def test_option_less_decide_routes_frontier_consult(monkeypatch):
    _dec_cfg(monkeypatch)
    d = router_core.dispatch(
        "decide this: what should the fleet prioritize next quarter?",
        session_id=SID, model="flash")
    assert d.lane == router_core.LANE_COMPLEXITY
    assert d.mode == router_core.MODE_CONSULT
    assert d.reason == "manual_open_question_frontier"
    assert d.model_target  # a frontier consult must have a target


def test_option_less_decide_on_this_same_contract(monkeypatch):
    _dec_cfg(monkeypatch)
    d = router_core.dispatch(
        "decide on this: what should the fleet prioritize next quarter?",
        session_id=SID, model="flash")
    assert d.reason == "manual_open_question_frontier"


def test_menu_decide_stays_decision_lane(monkeypatch):
    _dec_cfg(monkeypatch)
    d = router_core.dispatch(
        "decide on this: deploy window. Option A tonight. Option B "
        "tomorrow morning. Pick one.",
        session_id=SID, model="flash")
    assert d.lane == router_core.LANE_DECISION
    assert d.reason == "decision_detected:manual_ask"


# ---------------------------------------------------------------------------
# R15-7: benign brief-frame gate covers the anchor leg
# ---------------------------------------------------------------------------

def test_benign_brief_frame_suppresses_complexity_consult(monkeypatch):
    _dec_cfg(monkeypatch)
    d = router_core.dispatch(
        "brief me on vitest vs jest — summarize the tradeoffs for the "
        "fleet migration doc",
        session_id=SID, model="flash")
    assert not (d.lane == router_core.LANE_COMPLEXITY
                and d.mode == router_core.MODE_CONSULT), \
        "benign setup brief must never bill the anchor consult"


def test_brief_frame_gate_keeps_real_forks(monkeypatch):
    _dec_cfg(monkeypatch)
    d = router_core.dispatch(
        "decide this: Option A ship tonight. Option B hold. Pick one.",
        session_id=SID, model="flash")
    assert d.lane == router_core.LANE_DECISION


# ---------------------------------------------------------------------------
# R15-5: banner delivery on EVERY delivery edge
# ---------------------------------------------------------------------------

def _park(session_id, text="· router · higher-self (frontier) | consult | "
                          "tok 100/20 | $0.001 | row=1 ·"):
    DB.park_anchor_banner(session_id, text)
    return text


def test_banner_attaches_on_flinch_passthrough_edge(r15_reset, monkeypatch):
    from hermes_router import flinch_reason
    monkeypatch.setattr(plugin, "_flinch_reason_gate", lambda: True)
    monkeypatch.setattr(plugin, "_doctrine_verdict_enabled", lambda: False)
    monkeypatch.setattr(flinch_reason, "classify_flinch_reason",
                        lambda *a, **k: "technical")
    banner = _park(SID)
    out = plugin.on_transform_llm_output(
        response_text="I cannot help with that request as stated.",
        session_id=SID, model="flash")
    assert out is not None, "delivery edge must return the merged body"
    assert "· router ·" in out and banner in out, \
        "banner must render in the delivered body of the same turn"
    assert DB._ANCHOR_BANNERS.get(SID) in (None, "")


def test_banner_attaches_on_agent_line_edge(r15_reset, monkeypatch):
    from hermes_router import refusal_doctrine
    monkeypatch.setattr(plugin, "_doctrine_verdict_enabled", lambda: True)
    monkeypatch.setattr(refusal_doctrine, "verdict_for_refusal",
                        lambda *a, **k: "agent_line")
    banner = _park(SID)
    out = plugin.on_transform_llm_output(
        response_text="I won't move on this; it's my line and it holds.",
        session_id=SID, model="flash")
    assert out is not None and banner in out


def test_no_banner_passthrough_unchanged(r15_reset, monkeypatch):
    from hermes_router import flinch_reason
    monkeypatch.setattr(plugin, "_flinch_reason_gate", lambda: True)
    monkeypatch.setattr(plugin, "_doctrine_verdict_enabled", lambda: False)
    monkeypatch.setattr(flinch_reason, "classify_flinch_reason",
                        lambda *a, **k: "technical")
    out = plugin.on_transform_llm_output(
        response_text="I cannot help with that request as stated.",
        session_id=SID, model="flash")
    assert out is None, "no parked banner -> plain passthrough contract"


def test_anchor_execution_exception_is_observable(r15_reset, monkeypatch):
    """R15-5 (b): the silent-zero signature dies — an exception inside
    maybe_execute_anchored emits a route event, never debug-only."""
    EVENTS = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: EVENTS.append((e, dict(f))))
    from hermes_router import anchor_chain, router_core as _rc
    rec = {"task_id": "t-r15", "lane": "complexity",
           "mode": router_core.MODE_CONSULT,
           "endpoint": anchor_chain.parse_anchor_uri(
               "openrouter://test/model-a", "primary"),
           "route_id": "r-r15", "session_id": SID, "orientation": True}
    monkeypatch.setattr(_rc, "pending_model_swap", lambda sid: rec)
    monkeypatch.setattr(_rc, "clear_anchor_backoff", lambda *a, **k: None)

    def _boom(endpoint, api_kwargs):
        raise RuntimeError("chain exploded")

    monkeypatch.setattr(anchor_exec, "anchored_call", _boom)
    outcome = anchor_exec.maybe_execute_anchored(SID, {"messages": []})
    assert outcome is None  # fail-open preserved
    fails = [f for f in EVENTS
             if f[1].get("event_detail") == "anchor_execution_exception"]
    assert fails, "exception must be OBSERVABLE at route-log level"
    assert "chain exploded" in str(fails[0][1].get("fail_kind") or "")
