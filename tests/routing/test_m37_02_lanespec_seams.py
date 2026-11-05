"""M37-02 — LaneSpec.seams: timing is (lane, seam), never lane.

The inventory (M37 §2) showed three of four lanes span two seams, so the
registry keys on (lane, seam). This unit adds the DATA only: one field, one
validation, one enumeration accessor. No dispatch site moves (the timing loop
is M37-03).

Pinned here:
  (a) an unknown seam id fails loudly at registration, naming the lane;
  (b) the four builtin lanes declare the inventory values, and
      enumerate_by_seam("PRE") follows registration order;
  (c) a lane declaring one seam is reported under it and NOT under another;
  (d) seams=() is legal — "no seam" is a real answer, not an assertion.
"""
import pytest

import hermes_router.lanes.builtins as B
import hermes_router.lanes.registry as R


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Registration mutates module state; keep each test's writes local."""
    monkeypatch.setattr(R, "_IDS", dict(R._IDS))


def test_a_unknown_seam_id_fails_loud_at_registration():
    with pytest.raises(ValueError) as exc:
        R.register_lane(R.LaneSpec(id="probe-lane-bad", seams=("nonsense",)))
    msg = str(exc.value)
    assert "probe-lane-bad" in msg, f"exception must name the lane: {msg}"
    assert "nonsense" in msg, f"exception must name the bad value: {msg}"


def test_a_unknown_seam_is_never_silently_dropped():
    """Fail-closed: the bad spec must not be registered at all."""
    with pytest.raises(ValueError):
        R.register_lane(R.LaneSpec(id="probe-lane-bad2", seams=("PRE", "LATER")))
    assert "probe-lane-bad2" not in R.all_ids()


def test_b_builtin_lanes_declare_the_inventory_values():
    expected = {
        "shadow": ("PRE", "POST"),
        "higher-pre": ("PRE", "MID"),
        "higher-post": ("POST", "PRE"),
        "decision": ("PRE", "MID"),
    }
    for lane_id, seams in expected.items():
        assert R.lane(lane_id).seams == seams, lane_id


def test_b_enumerate_by_seam_pre_follows_registration_order():
    for spec in (B._SHADOW, B._HIGHER_PRE, B._HIGHER_POST, B._DECISION):
        R.register_lane(spec)  # no-op when already registered
    assert R.enumerate_by_seam("PRE") == (
        "shadow",
        "higher-pre",
        "higher-post",
        "decision",
    )


def test_c_single_seam_lane_reported_under_post_not_mid():
    R.register_lane(R.LaneSpec(id="probe-lane-post", seams=("post",)))
    assert "probe-lane-post" in R.enumerate_by_seam("POST")
    assert "probe-lane-post" not in R.enumerate_by_seam("MID")


def test_d_empty_seams_is_legal_and_means_no_seam():
    spec = R.LaneSpec(id="probe-lane-none")
    R.register_lane(spec)  # must not raise
    assert R.lane("probe-lane-none").seams == ()
    for seam in R.SEAM_IDS:
        assert "probe-lane-none" not in R.enumerate_by_seam(seam)
