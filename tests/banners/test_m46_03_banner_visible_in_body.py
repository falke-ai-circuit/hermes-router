"""M46-03 — a fired lane is VISIBLE in the delivered bytes.

OWNER RULING (reverses M35-17), verbatim: "each call to whatever lane must
produce a banner at final response so that it's visible there was a router
activation (for billing and other purposes)". Silence is the defect: at HEAD
0f23989 the banner was parked and registered but `lifecycle.deliver` returned
the body untouched, so 189 banner tests passed while no user ever saw one.

These three pins are RED on unfixed HEAD:
  * test_delivered_body_carries_banner_when_a_lane_fires
  * test_one_banner_for_a_pre_post_pair
  * test_knob_off_silences_without_a_code_change (green only after the knob)
and the fourth stays green in both directions (no lane fired -> untouched).
"""
import sys

import pytest

_P = "/opt/data/plugins"
if _P not in sys.path:
    sys.path.insert(0, _P)

import hermes_router as plugin  # noqa: E402
from hermes_router import (completion_audit, config_access, debug_banner,  # noqa: E402
                           render_inbox, route_gate, router_core, state,
                           usage_ledger)
from hermes_router.features.banners import turn_record  # noqa: E402

SID = "s-m46-03"


@pytest.fixture()
def env(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    turn_record.reset()
    debug_banner._ANCHOR_BANNERS.clear()
    debug_banner._ANCHOR_TASKS.clear()
    debug_banner._ANCHOR_SEGS.clear()
    debug_banner._PARK_RECORDS.clear()
    monkeypatch.setattr(plugin, "_log_route", lambda e, **f: None)
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr(render_inbox, "_inbox_path",
                        lambda: str(tmp_path / "renders.jsonl"))
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"debug_banner": 1})
    monkeypatch.setattr(debug_banner, "_banner_section",
                        lambda: {"debug_banner": 1}, raising=True)
    monkeypatch.setattr(completion_audit, "audit_enabled", lambda: False)
    yield
    turn_record.reset()
    debug_banner._PARK_RECORDS.clear()


def _line(trigger="consult", model="glm-5.3", ti=500, to=200, cost=0.0753):
    return debug_banner.format_banner(
        lane="frontier-anchor", trigger=trigger, model=model,
        endpoint="https://api.nousresearch.com/v1", tokens_in=ti,
        tokens_out=to, est_cost=cost, latency_s=3, task_id="t1",
        session_id=SID)


def test_delivered_body_carries_banner_when_a_lane_fires(env):
    """THE pin: lane fires -> the DELIVERED bytes carry the banner marker."""
    debug_banner.park_anchor_banner(SID, _line(), task_id="t1")
    out = plugin.on_transform_llm_output(
        response_text="t1 plain answer", session_id=SID, model="m")
    assert isinstance(out, str), "a fired lane must hand back composed bytes"
    assert debug_banner.BANNER_HEAD in out
    assert "higher-self (frontier)" in out
    assert out.startswith("t1 plain answer"), "the agent's body stays first"
    # record and body agree: the line is rendered FROM the record
    assert turn_record.compose_line(SID) in out


def test_one_banner_for_a_pre_post_pair(env):
    """Frontier PRE + POST = ONE banner, ONE head, two legs folded in."""
    debug_banner.park_task_banner(SID, "t1", "pre", _line("consult", ti=400, to=90))
    debug_banner.park_task_banner(SID, "t1", "post", _line("completion_audit", ti=150, to=40))
    out = plugin.on_transform_llm_output(
        response_text="answered", session_id=SID, model="m")
    body = out or ""
    assert body.count(debug_banner.BANNER_HEAD) == 1, "exactly one banner"
    assert "pre+post-check" in body
    assert "tok 550/130" in body, "the two legs' tokens are summed, not doubled"


def test_no_lane_fired_leaves_the_body_untouched(env):
    """The absence assertion survives — but only for a turn with no lane."""
    out = plugin.on_transform_llm_output(
        response_text="just an answer", session_id=SID, model="m")
    assert out is None or out == "just an answer"
    assert debug_banner.BANNER_HEAD not in str(out or "")


def test_knob_off_silences_without_a_code_change(env, monkeypatch):
    """`banner_in_body: false` = the documented escape hatch."""
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"debug_banner": 1, "banner_in_body": False})
    monkeypatch.setattr(debug_banner, "_banner_section",
                        lambda: {"debug_banner": 1, "banner_in_body": False},
                        raising=True)
    assert turn_record.deliver_in_body_enabled() is False
    debug_banner.park_anchor_banner(SID, _line(), task_id="t1")
    out = plugin.on_transform_llm_output(
        response_text="quiet", session_id=SID, model="m")
    assert debug_banner.BANNER_HEAD not in str(out or "")


def test_knob_defaults_on_when_absent(env, monkeypatch):
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"debug_banner": 1})
    assert turn_record.deliver_in_body_enabled() is True
