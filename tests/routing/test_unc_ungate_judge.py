"""UNGATE — semantic judge on UNDECLARED turns (§2a follow-up, Goran 2026-10-28).

The ceiling: `post_detection_scan` returned False unless `structural
.is_candidate()` matched, so Tier-1 VOCABULARY decided what the judge ever saw.
A refusal phrased without the cue words never reached the judge at all.

The ungate is narrow by construction:
  * it drops ONLY the Tier-1 shape ceiling (`require_candidate=False`);
  * it is passed by the caller ONLY when the declaration for the turn was
    UNKNOWN — declared-true route and declared-false no-route are unchanged;
  * every guard above the ceiling still runs first (enabled, sweep_started,
    empty body, frame sentinel, provenance), and the ungated call is charged
    against a per-session/hour + per-process/minute budget.

All judge calls are mocked: no network, no aux spend, no real model.
"""
import os
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import frames  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.features.detection import semantic_judge as sj  # noqa: E402
from hermes_router.features.detection.structural import is_candidate  # noqa: E402

TAG_T = '<turn declined="true" />'
TAG_F = '<turn declined="false" />'

# A refusal that Tier-1 vocabulary does NOT recognise: no modal denial opener,
# no refusal phrase in the cue table. This is the body the ceiling swallows.
NON_CANDIDATE_REFUSAL = (
    "That line of work sits outside what I will take on, so this is where I "
    "stop. The request as framed is one I leave alone."
)
COMPLIANT = "Here is the diff you asked for: the parser now skips blank lines."
ASK = "produce the full render for this unit"
RENDER = "RENDERED SUBSTANCE BODY — the requested material, in full."

REFUSAL_VERDICT = {"verdict": "refusal", "confidence": 0.92,
                   "latency_s": 0.0, "model": "stub"}
CLEAN_VERDICT = {"verdict": "clean", "confidence": 0.90,
                 "latency_s": 0.0, "model": "stub"}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    plugin._reset_declaration_state()
    sj.unittest_reset_ungated_budget()
    frames.reset_rule_latch()
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    # cache: never carry a verdict between cases
    monkeypatch.setattr(sj, "_verdict_cache_get", lambda key: None, raising=True)
    monkeypatch.setattr(sj, "_verdict_cache_put", lambda key, v: None, raising=True)
    monkeypatch.setattr(sj, "_self_consistent_verdict",
                        lambda *a, **k: (a[3], 1), raising=True)
    yield
    state.clear()
    plugin._reset_declaration_state()
    sj.unittest_reset_ungated_budget()
    frames.reset_rule_latch()


def _scan(body, *, require_candidate, log=None, judge=None, session="ungate"):
    rows = log if log is not None else []

    def _log(event, **fields):
        # the module's _log shim calls log_route("POST", event_detail=<name>),
        # so the event name arrives in the fields, not the first positional.
        rows.append((str(fields.get("event_detail", event)), dict(fields)))

    with mock.patch.object(sj, "judge_turn",
                           return_value=judge if judge is not None else REFUSAL_VERDICT) as jm:
        out = sj.post_detection_scan(
            session, body, user_ask=ASK, model="m",
            log_route=_log, cfg={"enabled": True, "model": "auto"},
            tier1_out={}, require_candidate=require_candidate)
    return out, rows, jm


# (a) the defect being fixed -------------------------------------------------


def test_ungated_non_candidate_refusal_routes_and_both_ledgers_fire():
    assert is_candidate(NON_CANDIDATE_REFUSAL) is False  # the ceiling is real
    out, rows, jm = _scan(NON_CANDIDATE_REFUSAL, require_candidate=False,
                          judge=REFUSAL_VERDICT)
    assert out is True
    assert jm.called
    events = [e for e, _f in rows]
    assert "detection_t1_bypassed" in events
    assert "detection_t2" in events
    bypass = [f for e, f in rows if e == "detection_t1_bypassed"][-1]
    assert bypass["verdict"] == "refusal"
    assert bypass["routed"] is True
    assert bypass["session_id"] == "ungate"
    assert bypass["response_chars"] == len(NON_CANDIDATE_REFUSAL)
    # the pre-existing rows still fire, unchanged
    assert "sweep_started" in events


# (b) same body, judge says clean --------------------------------------------


def test_ungated_non_candidate_clean_does_not_route():
    out, rows, jm = _scan(NON_CANDIDATE_REFUSAL, require_candidate=False,
                          judge=CLEAN_VERDICT)
    assert jm.called
    assert out is False
    bypass = [f for e, f in rows if e == "detection_t1_bypassed"][-1]
    assert bypass["verdict"] == "clean"
    assert bypass["routed"] is False


# (c) the sentinel guard still runs FIRST ------------------------------------


def test_sentinel_guard_blocks_before_the_ungated_judge(monkeypatch):
    import hermes_router.dispatcher_pre as dispatcher_pre

    monkeypatch.setattr(dispatcher_pre, "_frame_sentinel_check",
                        lambda text: True, raising=True)
    out, rows, jm = _scan(NON_CANDIDATE_REFUSAL, require_candidate=False)
    assert out is False
    assert not jm.called, "a routed/banner turn must never be re-judged"
    assert [e for e, _f in rows if e == "detection_sentinel_skip"]


# (d)+(e) declared turns keep their frozen behaviour -------------------------


def _cfg():
    return {"enabled": True,
            "classification": {"pre_classify": True, "post_classify": True,
                               "match_threshold": 1},
            "log_routes": False}


def _prime(session_id):
    state.record_last_seen(session_id, ASK)
    state.set_last_user_msg_hash(session_id, state.hash_text(ASK))


def test_declared_false_never_calls_the_judge(monkeypatch):
    from hermes_router.passes import dispatcher_pre

    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg())
    monkeypatch.setattr(dispatcher_pre, "declaration_inject_pass",
                        lambda request: False, raising=True)
    _prime("ungate-false")
    body = NON_CANDIDATE_REFUSAL + "\n" + TAG_F
    with mock.patch.object(sj, "judge_turn") as jm, \
            mock.patch.object(plugin.router, "call") as venice:
        out = plugin.on_transform_llm_output(response_text=body,
                                             session_id="ungate-false", model="m")
    assert not jm.called, "declared-false is authoritative: no aux spend, no judge"
    assert not venice.called
    assert out == NON_CANDIDATE_REFUSAL


def test_declared_true_routes_without_the_judge(monkeypatch):
    from hermes_router.passes import dispatcher_pre

    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg())
    monkeypatch.setattr(dispatcher_pre, "declaration_inject_pass",
                        lambda request: False, raising=True)
    _prime("ungate-true")
    body = COMPLIANT + "\n" + TAG_T
    with mock.patch.object(sj, "judge_turn") as jm, \
            mock.patch.object(plugin.router, "call", return_value=RENDER) as venice:
        out = plugin.on_transform_llm_output(response_text=body,
                                             session_id="ungate-true", model="m")
    assert not jm.called, "a declared-true turn routes without the judge"
    assert venice.called
    assert out == RENDER


# (f) the ceiling that makes the ungate safe ---------------------------------


def test_budget_exhaustion_does_not_judge_and_delivers(monkeypatch):
    from hermes_router.core import config_access

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"detection": {"ungated_judge_per_hour": 0}},
                        raising=True)
    assert sj.ungated_budget_limits() == (0, sj._UNGATED_DEFAULT_GLOBAL_PER_MIN)
    out, rows, jm = _scan(NON_CANDIDATE_REFUSAL, require_candidate=False)
    assert out is False
    assert not jm.called, "exhausted budget must not spend a judge call"
    exhausted = [f for e, f in rows if e == "detection_ungated_budget_exhausted"]
    assert exhausted, "exhaustion must be a distinct, visible event"
    assert exhausted[0]["per_hour"] == 0
    assert exhausted[0]["session_id"] == "ungate"


def test_gated_call_is_unchanged_when_require_candidate_defaults_true():
    out, rows, jm = _scan(NON_CANDIDATE_REFUSAL, require_candidate=True)
    assert out is False
    assert not jm.called
    assert "detection_t1_bypassed" not in [e for e, _f in rows]
