"""P5 — BannerKind schema + BannerLifecycle + deliver() chokepoint.

Pins:
- kinds registry: the "anchor" kind carries the R9d/R19.16/R20 semantics
  transplanted from debug_banner's imperative rules.
- deliver() is THE chokepoint: an edge not in the kind's delivery_edges
  raises IllegalDeliveryEdge; on swallow the caller still delivers the
  body (fail-open) + emits the `banner_deliver_fail` telemetry row.
- park/consume via the lifecycle reproduce the pre-P5 aggregate semantics
  (R9d per-task replace, R19.16 stack, identical re-park dedupe).
"""
import pytest

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture(autouse=True)
def _clean_parks():
    from hermes_router import debug_banner as DB

    DB._ANCHOR_BANNERS.clear()
    DB._ANCHOR_SEGS.clear()
    DB._ANCHOR_TASKS.clear()
    DB._HELD_DECISIONS.clear()
    yield
    DB._ANCHOR_BANNERS.clear()
    DB._ANCHOR_SEGS.clear()
    DB._ANCHOR_TASKS.clear()
    DB._HELD_DECISIONS.clear()


def _lifecycle():
    from hermes_router.features.banners import lifecycle as _l

    return _l.LIFECYCLE


def test_anchor_kind_registered_with_transplanted_semantics():
    from hermes_router.features.banners import kinds

    k = kinds.get("anchor")
    assert k.stack_policy == "stack"          # R19.16
    assert k.capture_fallback is True         # R20-D3
    assert {"pre_render", "benign", "audit_sync", "empty_body",
            "claim_release"} <= set(k.delivery_edges)


def test_unknown_kind_fails_loud():
    from hermes_router.features.banners import kinds

    with pytest.raises(kinds.UnknownBannerKind):
        kinds.get("no_such_kind")


def test_lifecycle_park_stacks_and_replaces_per_task():
    from hermes_router import debug_banner as DB
    from hermes_router.features.banners import kinds

    _lifecycle().park(kinds.get("anchor"), "s-p5", "t1", "A")
    _lifecycle().park(kinds.get("anchor"), "s-p5", "t2", "B")
    # R9d: same-task re-park REPLACES that segment
    _lifecycle().park(kinds.get("anchor"), "s-p5", "t1", "A2")
    assert DB._ANCHOR_BANNERS["s-p5"] == "A2\n\nB"
    # identical re-park dedupes
    _lifecycle().park(kinds.get("anchor"), "s-p5", "t2", "B")
    assert DB._ANCHOR_BANNERS["s-p5"] == "A2\n\nB"
    # the debug_banner delegates own the same state
    assert DB.park_anchor_banner is not None
    assert DB.consume_parked_banner("s-p5") == "A2\n\nB"
    assert "s-p5" not in DB._ANCHOR_BANNERS


def test_deliver_chokepoint_illegal_edge_raises_and_fail_open(monkeypatch):
    from hermes_router.core import telemetry
    from hermes_router.features.banners import kinds, lifecycle

    _lifecycle().park(kinds.get("anchor"), "s-p5b", "", "BANNER")
    with pytest.raises(lifecycle.IllegalDeliveryEdge):
        _lifecycle().deliver("s-p5b", "body", "anchor", "not_an_edge")
    # the edge CALLER owns fail-open: catches the raise, emits the
    # banner_deliver_fail row, and still delivers the body.
    rows = []
    orig = telemetry.patchpoints.log_route_override
    telemetry.patchpoints.log_route_override = lambda event, **f: rows.append(
        (event, f))
    try:
        try:
            _lifecycle().deliver("s-p5b", "body", "anchor", "nope")
            raise AssertionError("expected IllegalDeliveryEdge")
        except lifecycle.IllegalDeliveryEdge as _ide:
            telemetry.log_route("POST", event_detail="banner_deliver_fail",
                                kind_id=_ide.kind_id, edge=_ide.edge,
                                session_id="s-p5b")
    finally:
        telemetry.patchpoints.log_route_override = orig
    assert any(r[1].get("event_detail") == "banner_deliver_fail"
               for r in rows), rows
    # body still delivered: the banner is untouched by the failed edge
    assert _lifecycle().peek("s-p5b") == "BANNER"
    from hermes_router import debug_banner as _DB

    monkeypatch.setattr(_DB, "debug_banner_enabled", lambda: True)
    assert _lifecycle().deliver("s-p5b", "body", "anchor",
                                "benign") == "body\n\n\nBANNER"


def test_deliver_append_reparks_on_vanish(monkeypatch):
    # knob OFF: append_banner's gate returns the base unchanged -> the
    # consumed banner must be RE-PARKED (R19.21), never lost.
    from hermes_router import debug_banner as DB
    from hermes_router.features.banners import kinds

    monkeypatch.setattr(DB, "debug_banner_enabled", lambda: False)
    _lifecycle().park(kinds.get("anchor"), "s-p5c", "", "B")
    out = _lifecycle().deliver("s-p5c", "body", "anchor", "benign")
    assert out == "body"                    # base delivered unchanged (fail-open)
    assert _lifecycle().peek("s-p5c") == "B"  # re-parked, not lost


def test_deliver_body_mode_is_the_banner_itself():
    from hermes_router.features.banners import kinds

    _lifecycle().park(kinds.get("anchor"), "s-p5d", "midturn", "MT")
    out = _lifecycle().deliver("s-p5d", "", "anchor", "empty_body",
                               mode="body")
    assert out == "MT"
    assert _lifecycle().peek("s-p5d") == ""  # one-shot consume


def test_deliver_discard_mode_releases_without_log_row():
    from hermes_router.core import patchpoints, telemetry
    from hermes_router.features.banners import kinds

    rows = []

    def _rec(event, **f):
        rows.append(event)

    patchpoints.log_route_override = _rec
    try:
        _lifecycle().park(kinds.get("anchor"), "s-p5e", "", "R")
        out = _lifecycle().deliver("s-p5e", "", "anchor", "claim_release",
                                   mode="discard")
        assert out == ""
        assert _lifecycle().peek("s-p5e") == ""  # released
        assert "anchor_banner_consume" not in rows  # release logs no row
    finally:
        patchpoints.log_route_override = None
    assert telemetry.patchpoints.log_route_override is None
