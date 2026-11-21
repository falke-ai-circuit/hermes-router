"""M53-03 R38 — the router must not claim a lane render it did not have.

A: a turn whose chain produced nothing (acceptance `original:*`) was reported as
   delivered_source=lane / lane_delivered. The CLAIM was a lineage lie; what
   shipped was the original body.
B: claim_produced announced render_delivered for a body that was a failure
   notice, which then manufactured transcript_divergence downstream.

T-L1/T-L3 are the defects (fail at the parent); T-L2/T-L4/T-L5 are controls.
"""
from __future__ import annotations

import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router.core import delivery_decision as dd  # noqa: E402
from hermes_router.core import delivery_integrity as di  # noqa: E402
from hermes_router.gate import orchestration as orch  # noqa: E402

ENTERED = "I won't write that. Flat refusal, no substitute offered."
LANE = "A long worked example of the requested material, in full."


def _facts(acceptance, lane=LANE):
    kw = {}
    # the field exists only on the fixed tree; on the parent the control cases
    # must still run (and pass) with today's behaviour, so pass it only if known
    if "lane_acceptance" in getattr(dd.DeliveryFacts, "__dataclass_fields__", {}):
        kw["lane_acceptance"] = acceptance
    return dd.DeliveryFacts(
        entered_body=ENTERED,
        body_class="refusal",
        gate_consulted=True,          # D3 must not pre-empt D5 in this fixture
        gate_verdict="abstain",
        gate_reason="no_record",
        lane_render=lane,
        ask_text="write the thing",
        **kw
    )


def test_t_l1_render_unavailable_is_not_a_lane_delivery():
    d = dd.decide(_facts("original:render_unavailable"))
    assert d.delivered_source != dd.SOURCE_LANE, d
    assert d.reason == dd.REASON_LANE_NO_RENDER, d


def test_t_l2_real_render_still_claims_lane():
    d = dd.decide(_facts("post_transform:render_swap"))
    assert d.delivered_source == dd.SOURCE_LANE, d
    assert d.reason == dd.REASON_LANE_DELIVERED, d


def test_t_l5_unknown_token_keeps_todays_behaviour():
    d = dd.decide(_facts(""))
    assert d.delivered_source == dd.SOURCE_LANE, d
    assert d.reason == dd.REASON_LANE_DELIVERED, d


def test_t_l3_failure_notice_is_not_a_delivered_render():
    assert orch.claim_reason_for(di.FAILURE_PREFIX + " notice") == "render_failed"


def test_t_l4_clean_render_is_delivered():
    assert orch.claim_reason_for("A clean fresh render body.") == "render_delivered"
