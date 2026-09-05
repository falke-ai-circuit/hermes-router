"""v3.2.0 close-out fix tests — one consult per turn on the complexity lane.

Defect (live 2026-09-05 ~09:42-09:45, session api_1788601327_0d17f78f): in a
multi-provider-call turn (flash tool-loop, 3 skill loads), on_llm_request
re-runs dispatch() on the same ingress text per call -> stage_model_swap
re-staged per call -> llm_execution executed an anchored call PER PROVIDER
CALL (first anchor 19132 chars / $0.028 succeeded; a later re-stage in the
SAME turn fired a second anchor attempt -> content_filter fail + wasted cap
estimate). Route log showed 2x anchor_route_fired with different route_ids in
one turn.

Fix: _SWAP_DONE marker (session_id, task_id) -> ts in router_core.
stage_model_swap no-ops (returns None) when present within a 10-min TTL;
re-fires of the same ask hit the same task_id; a new ask (different task_id)
stages fresh; TTL expiry allows re-consult; fail-open — llm_execution sees no
pending swap -> pass-through.

All network mocked.
"""
import sys
import os
import time
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import complexity, router_core  # noqa: E402

PLAN_ASK = ("Design a multi-stage migration plan for splitting the monolith "
            "into services, including architecture trade-offs between approaches.")
PLAN_ASK2 = ("Map the dependencies and produce an impact analysis across the "
             "entire codebase for this cross-cutting change.")
LOGGED = []


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    router_core._test_reset()
    plugin.state.clear()
    LOGGED.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True, "match_threshold": 1},
        "log_routes": False,
    }, raising=False)
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    monkeypatch.setattr(plugin, "_log_route", _capture_log)
    _chain = _test_anchor_chain()
    monkeypatch.setattr(router_core.anchor_chain, "load_anchor_chain", lambda: _chain)
    yield
    router_core._test_reset()
    plugin.state.clear()


def _capture_log(event, **fields):
    LOGGED.append((event, dict(fields)))


def _test_anchor_chain():
    from hermes_router import anchor_chain

    return anchor_chain.AnchorChainCfg(
        primary=anchor_chain.parse_anchor_uri("openrouter://test/anchor-primary", "primary"),
        judge=anchor_chain.parse_anchor_uri("openrouter://test/anchor-judge", "judge"),
        overflow="pass_through",
        daily_cap_usd=2.0,
        pricing={},
    )


# ---------------------------------------------------------------------------
# Core contract: re-fire of same ask within TTL stages once
# ---------------------------------------------------------------------------


def test_refire_same_ask_stages_once():
    d1 = router_core.dispatch(PLAN_ASK, session_id="s1", model="m")
    assert d1.lane == router_core.LANE_COMPLEXITY
    rec1 = router_core.stage_model_swap("s1", d1)
    assert rec1 is not None  # first consult stages
    assert router_core.peek_pending_swap("s1") is not None
    # Re-fire: same ask, same session (the tool-loop's next provider call).
    d2 = router_core.dispatch(PLAN_ASK, session_id="s1", model="m")
    assert d2.task_id == d1.task_id  # same ask -> same task
    rec2 = router_core.stage_model_swap("s1", d2)
    assert rec2 is None  # re-stage no-op
    # The FIRST staged swap is untouched (still pending until llm_execution
    # consumes it; after consumption re-staging stays blocked by the marker).
    assert router_core.peek_pending_swap("s1") is rec1 or \
        router_core.peek_pending_swap("s1")["task_id"] == d1.task_id


def test_consumed_swap_still_blocks_refire():
    """The live defect: the first anchor CONSUMED its swap, then a later
    provider call re-staged -> second anchor. Marker must survive consumption."""
    d = router_core.dispatch(PLAN_ASK, session_id="s-consume", model="m")
    router_core.stage_model_swap("s-consume", d)
    assert router_core.pending_model_swap("s-consume") is not None  # consumed
    assert router_core.pending_model_swap("s-consume") is None
    # Same ask re-fires after consumption -> no-op (one consult per turn).
    assert router_core.stage_model_swap("s-consume", d) is None
    assert router_core.peek_pending_swap("s-consume") is None


# ---------------------------------------------------------------------------
# New ask (different task_id) stages again
# ---------------------------------------------------------------------------


def test_new_ask_stages_again():
    d1 = router_core.dispatch(PLAN_ASK, session_id="s2", model="m")
    assert router_core.stage_model_swap("s2", d1) is not None
    router_core.pending_model_swap("s2")  # consume
    d2 = router_core.dispatch(PLAN_ASK2, session_id="s2", model="m")
    assert d2.task_id != d1.task_id  # new ask = new task
    rec2 = router_core.stage_model_swap("s2", d2)
    assert rec2 is not None  # new consult stages
    assert router_core.peek_pending_swap("s2") is not None


# ---------------------------------------------------------------------------
# TTL expiry allows re-consult
# ---------------------------------------------------------------------------


def test_ttl_expiry_allows_reconsult(monkeypatch):
    d = router_core.dispatch(PLAN_ASK, session_id="s3", model="m")
    assert router_core.stage_model_swap("s3", d) is not None
    # Age the marker past the TTL.
    key = ("s3", d.task_id)
    with router_core._PENDING_SWAP_LOCK:
        router_core._SWAP_DONE[key] = time.time() - (router_core._SWAP_DONE_TTL + 1.0)
    # Same ask now stages again (fresh consult allowed).
    rec = router_core.stage_model_swap("s3", d)
    assert rec is not None
    assert router_core.peek_pending_swap("s3") is not None


def test_ttl_boundary_still_blocks():
    """Inside the TTL window the marker still blocks."""
    d = router_core.dispatch(PLAN_ASK, session_id="s3b", model="m")
    router_core.stage_model_swap("s3b", d)
    key = ("s3b", d.task_id)
    with router_core._PENDING_SWAP_LOCK:
        router_core._SWAP_DONE[key] = time.time() - (router_core._SWAP_DONE_TTL - 1.0)
    assert router_core.stage_model_swap("s3b", d) is None


# ---------------------------------------------------------------------------
# Fail-open: skip leaves flash pass-through (no pending swap)
# ---------------------------------------------------------------------------


def test_failopen_llm_execution_sees_no_swap():
    d = router_core.dispatch(PLAN_ASK, session_id="s4", model="m")
    router_core.stage_model_swap("s4", d)
    router_core.pending_model_swap("s4")  # consume — anchored call done
    # Re-fire (same ask): stage skipped; llm_execution must see NO pending
    # swap and pass flash through unchanged.
    assert router_core.stage_model_swap("s4", d) is None
    assert router_core.peek_pending_swap("s4") is None
    # middleware contract: peek_pending_swap None -> next_call passthrough
    called = {}

    def _next(req):
        called["ok"] = True
        return "flash-result"

    from hermes_router import anchor_exec

    monkey_req = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    with mock.patch.object(router_core, "peek_pending_swap", return_value=None):
        out = anchor_exec.maybe_execute_anchored("s4", monkey_req)
    # no swap staged -> outcome not an anchored 'done'; flash path untouched
    assert not (isinstance(out, tuple) and out and out[0] == "done")


def test_swap_done_size_cap():
    """Marker dict reaps on size (cap 128) — same discipline as _TASK_STATE."""
    d = router_core.dispatch(PLAN_ASK, session_id="cap-base", model="m")
    assert router_core.stage_model_swap("cap-base", d) is not None
    for i in range(150):
        fake = mock.Mock(task_id="task-%03d" % i, route_id="r%d" % i,
                         mode="plan", lane=router_core.LANE_COMPLEXITY)
        router_core.stage_model_swap("cap-%d" % i, fake)
    assert len(router_core._SWAP_DONE) <= router_core._SWAP_DONE_MAX


# ---------------------------------------------------------------------------
# Wiring: PRE logs swap_already_staged on the re-fire
# ---------------------------------------------------------------------------


def test_pre_middleware_logs_swap_already_staged(monkeypatch):
    req = {"model": "m", "messages": [{"role": "user", "content": PLAN_ASK}]}
    # First call (provider call 1 of the turn): stages + anchor_route_fired.
    out1 = plugin.on_llm_request(request=req, original_request=req, session_id="s5")
    assert out1 == {}
    assert any(f.get("event_detail") == "anchor_route_fired" for _, f in LOGGED)
    router_core.pending_model_swap("s5")  # llm_execution consumes the swap
    # Second call on the SAME ingress text (provider call 2 of the turn).
    out2 = plugin.on_llm_request(request=req, original_request=req, session_id="s5")
    assert out2 == {}  # flash proceeds normally (fail-open)
    skips = [f for _, f in LOGGED if f.get("event_detail") == "swap_already_staged"]
    assert len(skips) == 1
    assert skips[0]["session_id"] == "s5"
    assert skips[0]["task_id"] == router_core.task_id_for("s5", PLAN_ASK, "m")


def test_pre_middleware_second_new_ask_routes_normally(monkeypatch):
    """A different ask in the same session is NOT blocked by the marker."""
    req1 = {"model": "m", "messages": [{"role": "user", "content": PLAN_ASK}]}
    plugin.on_llm_request(request=req1, original_request=req1, session_id="s6")
    router_core.pending_model_swap("s6")
    req2 = {"model": "m", "messages": [{"role": "user", "content": PLAN_ASK2}]}
    plugin.on_llm_request(request=req2, original_request=req2, session_id="s6")
    fires = [f for _, f in LOGGED if f.get("event_detail") == "anchor_route_fired"]
    assert len(fires) == 2  # both asks fired (second not suppressed)
    skips = [f for _, f in LOGGED if f.get("event_detail") == "swap_already_staged"]
    assert skips == []
    assert router_core.peek_pending_swap("s6") is not None
    router_core.pending_model_swap("s6")