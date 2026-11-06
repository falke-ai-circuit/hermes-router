"""M45-21 — capture-fallback deferral: cause pinned, not patched.

Decided from the read (features/banners/lifecycle.py:452-536; :116-127;
:290; debug_banner.py:428-451): the `banner_capture_fallback_deferred`
return paths are CORRECT BY DESIGN. The watcher defers only while the park
belongs to the still-OPEN turn (or while no assistant row is newer than the
park), and the slot it leaves behind is the SAME slot every delivery edge
claims:

    lifecycle.deliver(...)                    -> :290
        parked = db.consume_parked_banner(session_id)
    debug_banner.consume_parked_banner(sid)   -> :428
        _PARK_RECORDS.pop(sid, None)
        return _lifecycle().consume(kind, sid, "", "claim_release")
    BannerLifecycle.consume                   -> :125
        return db._ANCHOR_BANNERS.pop(sid, "")

Same dict, same key as the watcher's own guard
(`db._ANCHOR_BANNERS.get(sid, "") != captured`, :450/:454). No second slot
exists, so a deferred park is still claimable — a park that is deferred
forever means the turn never closed, not that the edge looked elsewhere.

These tests pin that: the deferral must leave the slot byte-identical and
the very next legal deliver() must take it EXACTLY once. Test 3 removes the
guard premise and shows the slot is lost — i.e. the pin is sensitive to the
condition it names, not to the deferral merely existing.
"""
import pytest

import hermes_router.debug_banner as db
from hermes_router import advisory_consumption as ac
from hermes_router.core import canonical as canon
from hermes_router.features.banners import lifecycle as lc
from hermes_router.features.banners import turn_record as tr

SID = "s-m45-21"
BANNER = "· router · higher-self (frontier) | pre-check · 22s ·"


@pytest.fixture(autouse=True)
def _clean():
    for d in (db._ANCHOR_BANNERS, db._ANCHOR_TASKS, db._ANCHOR_SEGS,
              db._PARK_RECORDS, tr._RECORDS):
        d.clear()
    lc.LIFECYCLE._CAPTURE_FB_INFLIGHT.clear()
    yield
    for d in (db._ANCHOR_BANNERS, db._ANCHOR_TASKS, db._ANCHOR_SEGS,
              db._PARK_RECORDS, tr._RECORDS):
        d.clear()
    lc.LIFECYCLE._CAPTURE_FB_INFLIGHT.clear()


def _park(turn, sig="ask-abc", parked_at=1000.0, event_seq=7):
    db._PARK_RECORDS[SID] = {"event_seq": event_seq, "parked_at": parked_at,
                            "task_id": "t", "turn_no": turn,
                            "ask_sig": sig}
    db._ANCHOR_BANNERS[SID] = BANNER
    return BANNER


def _watch(monkeypatch, captured, turn, sig="ask-abc",
           row="previous answer", row_ts=2000.0, rewrite_ok=True):
    rows = []
    clocks = iter([0.0, 10.0, 100.0, 200.0])
    monkeypatch.setattr(lc, "_time_sleep", lambda s: None)
    monkeypatch.setattr(lc, "_time_monotonic", lambda: next(clocks))
    monkeypatch.setattr(lc, "_time_wall", lambda: 5000.0)
    monkeypatch.setattr(lc.BannerLifecycle, "banner_capture_fallback_wait",
                        lambda self: 45.0)
    monkeypatch.setattr(lc.BannerLifecycle, "_newest_persisted_assistant_row",
                        lambda self, s: row)
    monkeypatch.setattr(
        lc.BannerLifecycle, "_newest_persisted_assistant_row_ts",
        lambda self, s: row_ts)
    monkeypatch.setattr(canon, "rewrite_persisted_turn",
                        lambda sid_, old, new: rewrite_ok)
    monkeypatch.setattr(lc, "_log_route",
                        lambda stage, **f: rows.append(
                            (f.get("event_detail"), f)))
    monkeypatch.setattr(ac, "turn_no", lambda s: turn, raising=False)
    monkeypatch.setattr(ac, "open_signal", lambda s: sig, raising=False)
    lc.LIFECYCLE._capture_fallback_watch(SID, captured)
    return rows


def _deliver(monkeypatch, turn=4, sig="ask-abc", body="the answer"):
    rows = []
    monkeypatch.setattr(lc, "_log_route",
                        lambda stage, **f: rows.append(
                            (f.get("event_detail"), f)))
    monkeypatch.setattr(ac, "turn_no", lambda s: turn, raising=False)
    monkeypatch.setattr(ac, "open_signal", lambda s: sig, raising=False)
    # the claiming edge is the turn-close edge: the M33 pre-hold only holds a
    # pre-only frontier slot while the turn is still open (lifecycle.py:265-277)
    monkeypatch.setattr(ac, "turn_closed", lambda s: True, raising=False)
    out = lc.LIFECYCLE.deliver(SID, body, "anchor", "benign")
    return out, rows


def test_turn_open_deferral_leaves_the_slot_claimable_exactly_once(monkeypatch):
    captured = _park(turn=4)
    rows = _watch(monkeypatch, captured, turn=4, sig="ask-abc")
    kinds = [e for e, _ in rows]
    assert "banner_capture_fallback_deferred" in kinds, kinds
    deferred = [f for e, f in rows
                if e == "banner_capture_fallback_deferred"][0]
    assert deferred["outcome"] == "turn_open"
    assert deferred["turn_no"] == 4
    assert "banner_capture_fallback" not in kinds   # no repair, no pop

    # the condition: the deferral is a no-op on the slot
    assert db._ANCHOR_BANNERS.get(SID) == captured
    assert db._PARK_RECORDS.get(SID, {}).get("turn_no") == 4
    assert db._PARK_RECORDS.get(SID, {}).get("event_seq") == 7

    # ... and that same slot is what the delivery edge takes (same key)
    out, drows = _deliver(monkeypatch, turn=4, sig="ask-abc")
    # M46-03: the fired lane's receipt rides the delivered bytes.
    assert out.startswith("the answer")
    assert db.BANNER_HEAD in out
    assert SID not in db._ANCHOR_BANNERS, "first delivery must claim the park"
    assert SID not in db._PARK_RECORDS
    consume = [f for e, f in drows if e == "anchor_banner_consume"][0]
    assert consume["parked"] is True
    assert consume["delivered"] is True
    assert consume["reason"] == "delivered"
    assert consume["park_event_seq"] == 7

    # exactly once: a second edge finds nothing to attach
    _, drows2 = _deliver(monkeypatch, turn=4, sig="ask-abc")
    consume2 = [f for e, f in drows2 if e == "anchor_banner_consume"][0]
    assert consume2["parked"] is False
    assert consume2["reason"] == "no_park_this_turn"


def test_row_older_than_the_park_defers_without_consuming(monkeypatch):
    captured = _park(turn=4, parked_at=1000.0)
    rows = _watch(monkeypatch, captured, turn=9, sig="ask-other",
                  row="stale row", row_ts=900.0)
    deferred = [f for e, f in rows
                if e == "banner_capture_fallback_deferred"]
    assert deferred and deferred[0]["outcome"] == "no_row_after_park"
    assert db._ANCHOR_BANNERS.get(SID) == captured


def test_without_the_turn_stamp_the_slot_is_lost(monkeypatch):
    """Premise run: strip the guard (no turn stamp, no parked_at) and the
    watcher DOES pop the slot on the previous turn's row. This is the state
    the deferral prevents — the pin above is sensitive to it."""
    captured = _park(turn=0, parked_at=0.0)
    rows = _watch(monkeypatch, captured, turn=9, sig="ask-other",
                  row="previous answer")
    kinds = [e for e, _ in rows]
    assert "banner_render_captured" in kinds, kinds
    assert SID not in db._ANCHOR_BANNERS      # popped by the watcher
    _, drows = _deliver(monkeypatch, turn=9, sig="ask-other")
    consume = [f for e, f in drows if e == "anchor_banner_consume"][0]
    assert consume["reason"] == "no_park_this_turn"
