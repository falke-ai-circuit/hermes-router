"""M37-04 — the PRE lane handler table: fail-open by contract.

The table `passes.dispatcher_pre._PRE_LANE_HANDLERS` is keyed on a
`RouteDecision.lane` — a DISPATCH-OUTCOME CLASSIFICATION (`router_core.LANE_*`)
— never on a registry `LaneSpec.id`, which is a render/timing identity. The two
vocabularies name different objects (M37-03b §2, verdict (b)); the registry
gains no field, and this table does not bridge them.

So the plug-in proof is confined to the SEAM LAYER: a fifth LaneSpec declaring
`POST` + a `post_question` token resolves through `lane_for_post_question` with
zero edits to passes/, gate/ or core — and it STANDS DOWN in the PRE handler
table (returns False, never raises). Plug-in parity for `router_core.dispatch`
is not asserted: its classifier ladder is a closed, hand-ordered chain with no
registration hook, so such a test would be false by construction.

The fixture lane below is registered INSIDE the test and removed in a
finally block, so the module leaves no row behind for later tests in the same
session.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes_router import router_core
from hermes_router.lanes import registry as lane_registry
from hermes_router.lanes.registry import LaneContract, LaneSpec
from hermes_router.passes import dispatcher_pre

LANE_ID = "m37-04-fixture-lane"
LANE_TOKEN = "m37_04_fixture_post_question"


@pytest.fixture()
def fixture_post_lane():
    """A fifth lane: one data literal, POST seam, its own question token."""
    spec = LaneSpec(
        id=LANE_ID,
        marker_strings=("M37-04 FIXTURE TURN",),
        seams=("POST",),
        contract=LaneContract(post_question=LANE_TOKEN),
    )
    lane_registry.register_lane(spec)
    try:
        yield spec
    finally:
        lane_registry._IDS.pop(LANE_ID, None)


def test_fifth_lanespec_resolves_at_post_seam_with_zero_handler_edits(fixture_post_lane):
    """A new lane is one LaneSpec data literal: the POST seam resolves it."""
    assert lane_registry.lane_for_post_question(LANE_TOKEN) == LANE_ID
    assert LANE_ID in lane_registry.enumerate_by_seam("POST")
    # the handlers were not touched to make this true
    assert set(dispatcher_pre._PRE_LANE_HANDLERS) == {router_core.LANE_COMPLEXITY}


def test_pre_lane_table_stands_down_for_unregistered_lane(fixture_post_lane):
    """Fail-open is the contract: a lane with no handler returns False."""
    decision = SimpleNamespace(lane=LANE_ID)
    assert dispatcher_pre._pre_lane_dispatch("ask", "sid", "model", decision) is False


def test_pre_lane_table_stands_down_for_none_lane():
    """A None lane is the absence of a lane — False, not a KeyError."""
    decision = SimpleNamespace(lane=None)
    assert dispatcher_pre._pre_lane_dispatch("ask", "sid", "model", decision) is False


def test_unknown_lane_id_never_raises():
    """An id nobody registered is indistinguishable from no lane here."""
    decision = SimpleNamespace(lane="no-such-lane-anywhere")
    assert dispatcher_pre._pre_lane_dispatch("ask", "sid", "model", decision) is False


def test_complexity_lane_is_the_one_registered_handler():
    assert dispatcher_pre._PRE_LANE_HANDLERS[router_core.LANE_COMPLEXITY] is (
        dispatcher_pre._handle_complexity_lane)
