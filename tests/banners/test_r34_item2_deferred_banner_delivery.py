"""R34 item 2 — the banner never reached the delivered turn.

Evidence: `debug_banner_emitted lane=uncensored-render` fired on conductor
(14), analyst (8), operative (4), researcher (7) lifetime, yet ZERO delivered
messages on those profiles contained `shadow-self (uncensored`. Mechanism in
the log:

    banner_capture_fallback_failed detail=no_row_match — banner dropped
    (R33f-A semaphore: one delivery, no re-park)

i.e. when the capture-fallback watcher could not rewrite the persisted
assistant row, R33f-A DROPPED the parked banner.

Fix under test (R34-2): a no_row_match on the PREFERRED edge (persisted-row
rewrite) no longer destroys the banner. It stays parked under the SAME
semaphore record — same event_seq, so still exactly ONE delivery — and the
next legal delivery edge attaches it to the DELIVERED body. The deferral is
bounded by _DEFER_TTL_S, after which the slot expires fail-loud.

Acceptance is the delivered body, not the log line.
"""
import sys
from unittest import mock

import pytest

_P = "/opt/data/plugins"
if _P not in sys.path:
    sys.path.insert(0, _P)

import hermes_router as plugin  # noqa: E402
from hermes_router import (completion_audit, config_access, debug_banner,  # noqa: E402
                           render_inbox, route_gate, router_core, state,
                           usage_ledger)
from hermes_router.features.banners import lifecycle as _lc  # noqa: E402

SID = "s-r34-2"


@pytest.fixture()
def env(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    debug_banner._ANCHOR_BANNERS.clear()
    debug_banner._ANCHOR_TASKS.clear()
    debug_banner._ANCHOR_SEGS.clear()
    debug_banner._PARK_RECORDS.clear()
    debug_banner._HELD_DECISIONS.clear()
    LOGGED = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr(render_inbox, "_inbox_path",
                        lambda: str(tmp_path / "renders.jsonl"))
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"debug_banner": 1})
    monkeypatch.setattr(debug_banner, "_banner_section",
                        lambda: {"debug_banner": 1}, raising=True)
    monkeypatch.setattr(completion_audit, "audit_enabled", lambda: False)
    # never spawn the background watcher during these synchronous tests
    monkeypatch.setattr(_lc.LIFECYCLE, "_maybe_schedule_capture_fallback",
                        lambda sid: None)
    monkeypatch.setattr(_lc.LIFECYCLE, "banner_capture_fallback_wait",
                        lambda: 0.05)
    yield LOGGED
    debug_banner._PARK_RECORDS.clear()
    debug_banner._ANCHOR_BANNERS.clear()


def _banner():
    return debug_banner.format_banner(
        lane="uncensored-render", trigger="ied_construction",
        model="qwen", endpoint="api.venice.ai", tokens_in=120,
        tokens_out=400, est_cost=0.001, latency_s=2, task_id="t-r34-2",
        session_id=SID)


def _run_watcher(monkeypatch):
    _lc.LIFECYCLE._capture_fallback_watch(SID, _banner())


def test_no_row_match_defers_instead_of_dropping(env, monkeypatch):
    debug_banner.park_anchor_banner(SID, _banner(), task_id="t-r34-2")
    seq_before = debug_banner.park_record(SID).get("event_seq")
    assert seq_before

    with mock.patch.object(_lc.LIFECYCLE, "_newest_persisted_assistant_row",
                           return_value=""):
        _run_watcher(monkeypatch)

    events = [f.get("event_detail") for _, f in env]
    assert "banner_capture_fallback_deferred" in events
    assert "banner_semaphore_expired" not in events
    # the banner SURVIVES, under the SAME semaphore record (one delivery)
    assert debug_banner._ANCHOR_BANNERS.get(SID)
    rec = debug_banner.park_record(SID)
    assert rec.get("event_seq") == seq_before
    assert rec.get("deferred_at")


def test_deferred_banner_reaches_the_next_delivered_body(env, monkeypatch):
    banner = _banner()
    debug_banner.park_anchor_banner(SID, banner, task_id="t-r34-2")
    with mock.patch.object(_lc.LIFECYCLE, "_newest_persisted_assistant_row",
                           return_value=""):
        _run_watcher(monkeypatch)

    delivered = _lc.LIFECYCLE.deliver(SID, "the actual answer body",
                                      "anchor", "benign")
    assert "the actual answer body" in delivered
    assert debug_banner.BANNER_HEAD in delivered, (
        "banner must ride the DELIVERED body, not only the log")
    assert "shadow-self (uncensored" in delivered

    # one delivery only — the next edge attaches nothing
    second = _lc.LIFECYCLE.deliver(SID, "next turn body", "anchor", "benign")
    assert debug_banner.BANNER_HEAD not in second


def test_deferred_slot_expires_after_ttl(env, monkeypatch):
    debug_banner.park_anchor_banner(SID, _banner(), task_id="t-r34-2")
    rec = dict(debug_banner.park_record(SID))
    rec["deferred_at"] = _lc._time_wall() - (_lc._DEFER_TTL_S + 60)
    debug_banner._PARK_RECORDS[SID] = rec

    out = _lc.LIFECYCLE.deliver(SID, "late body", "anchor", "benign")
    assert debug_banner.BANNER_HEAD not in out
    assert not debug_banner._ANCHOR_BANNERS.get(SID)
    assert "banner_semaphore_expired" in [f.get("event_detail") for _, f in env]
