"""R34-19f4 — the capture-fallback watcher must not repair a turn that is
still running.

Live evidence (analyst 2026-10-21, session 20261021_145948_38a356):

    PRE  anchor_banner_parked                            t+3s
    ...  agent works ~60s (nothing persisted for THIS turn yet) ...
    POST banner_render_captured edge=capture_fallback     <- 45s watcher fires
    POST anchor_banner_consume parked=False edge=benign
                              reason=no_park_this_turn

The watcher found the PREVIOUS turn's assistant row, appended the banner to it
and popped the slot. The transcript looked repaired; the user's message carried
no receipt. A park belonging to the OPEN turn is still claimable by that turn's
own delivery edge, so the watcher must defer and leave it parked.
"""
import hermes_router.debug_banner as db
from hermes_router.features.banners import lifecycle as lc
from hermes_router.core import canonical as canon

BANNER = "· router · higher-self (frontier) | pre-check · 22s ·"


def _park(sid, turn, sig="ask-abc"):
    db._PARK_RECORDS[sid] = {"event_seq": 7, "parked_at": 0.0,
                             "task_id": "t", "turn_no": turn,
                             "ask_sig": sig}
    db._ANCHOR_BANNERS[sid] = BANNER
    return BANNER


def _run(monkeypatch, sid, captured, turn, sig, row="previous answer",
         rewrite_ok=True):
    rows = []
    clocks = iter([0.0, 10.0, 100.0, 200.0])
    monkeypatch.setattr(lc, "_time_sleep", lambda s: None)
    monkeypatch.setattr(lc, "_time_monotonic", lambda: next(clocks))
    monkeypatch.setattr(lc.BannerLifecycle, "banner_capture_fallback_wait",
                        lambda self: 45.0)
    monkeypatch.setattr(lc.BannerLifecycle, "_newest_persisted_assistant_row",
                        lambda self, s: row)
    monkeypatch.setattr(canon, "rewrite_persisted_turn",
                        lambda sid_, old, new: rewrite_ok)
    monkeypatch.setattr(lc, "_log_route",
                        lambda stage, **f: rows.append(
                            (f.get("event_detail"), f)))
    from hermes_router import advisory_consumption as ac
    monkeypatch.setattr(ac, "turn_no", lambda s: turn, raising=False)
    monkeypatch.setattr(ac, "open_signal", lambda s: sig, raising=False)
    lc.LIFECYCLE._capture_fallback_watch(sid, captured)
    return rows


def test_open_turn_is_never_captured_by_the_fallback(monkeypatch):
    sid = "s-f4-open"
    captured = _park(sid, turn=4)
    rows = _run(monkeypatch, sid, captured, turn=4, sig="ask-abc")
    kinds = [e for e, _ in rows]
    assert "banner_capture_fallback_deferred" in kinds, kinds
    deferred = [f for e, f in rows if e == "banner_capture_fallback_deferred"][0]
    assert deferred["outcome"] == "turn_open"
    assert "banner_render_captured" not in kinds
    # the slot SURVIVES so this turn's own delivery edge can still claim it
    assert db._ANCHOR_BANNERS.get(sid) == captured
    assert db._PARK_RECORDS.get(sid, {}).get("turn_no") == 4


def test_a_closed_turn_is_still_repaired(monkeypatch):
    sid = "s-f4-closed"
    captured = _park(sid, turn=4)
    rows = _run(monkeypatch, sid, captured, turn=5, sig="ask-zzz")
    kinds = [e for e, _ in rows]
    assert "banner_capture_fallback" in kinds, kinds
    assert "banner_render_captured" in kinds
    assert sid not in db._ANCHOR_BANNERS  # captured => one delivery, honoured


def test_same_turn_number_but_a_different_ask_is_not_the_parked_turn(monkeypatch):
    """Turn numbers repeat across sessions; the ask signature is the tie-breaker.
    A different ask on the same turn number is not the turn that parked."""
    sid = "s-f4-sig"
    captured = _park(sid, turn=4, sig="ask-abc")
    rows = _run(monkeypatch, sid, captured, turn=4, sig="ask-different")
    kinds = [e for e, _ in rows]
    assert "banner_render_captured" in kinds, kinds


def test_no_park_record_means_no_deferral(monkeypatch):
    """A park with no turn stamp (legacy/older rows) must not be blocked
    forever — the guard only fires when a turn is actually on record."""
    sid = "s-f4-legacy"
    db._PARK_RECORDS.pop(sid, None)
    db._ANCHOR_BANNERS[sid] = BANNER
    rows = _run(monkeypatch, sid, BANNER, turn=4, sig="ask-abc")
    kinds = [e for e, _ in rows]
    assert "banner_render_captured" in kinds, kinds


def test_a_row_older_than_the_park_is_not_repaired(monkeypatch):
    """R34-19f5. The turn-open guard is not enough: on reviewer the turn number
    had advanced by the time the 45s watcher fired, so it repaired the PREVIOUS
    turn's row and popped the slot — delivery then found no park. A row written
    BEFORE the banner was parked cannot be the row it belongs to."""
    sid = "s-f5-old-row"
    captured = _park(sid, turn=4)
    db._PARK_RECORDS[sid]["parked_at"] = 1_000.0
    monkeypatch.setattr(lc.BannerLifecycle, "_newest_persisted_assistant_row_ts",
                        lambda self, s2: 900.0)  # older than the park
    rows = _run(monkeypatch, sid, captured, turn=5, sig="ask-zzz")
    kinds = [e for e, _ in rows]
    assert "banner_capture_fallback_deferred" in kinds, kinds
    deferred = [f for e, f in rows if e == "banner_capture_fallback_deferred"][0]
    assert deferred["outcome"] == "no_row_after_park"
    assert db._ANCHOR_BANNERS.get(sid) == captured
    assert "banner_render_captured" not in kinds


def test_a_row_newer_than_the_park_is_repaired(monkeypatch):
    sid = "s-f5-new-row"
    captured = _park(sid, turn=4)
    db._PARK_RECORDS[sid]["parked_at"] = 900.0
    monkeypatch.setattr(lc.BannerLifecycle, "_newest_persisted_assistant_row_ts",
                        lambda self, s2: 1_000.0)  # written after the park
    rows = _run(monkeypatch, sid, captured, turn=5, sig="ask-zzz")
    kinds = [e for e, _ in rows]
    assert "banner_render_captured" in kinds, kinds
    assert sid not in db._ANCHOR_BANNERS
