"""M36-29 — the stash distinction is a FIELD, not a second event name.

Ruling B (M36-28 §1): the gate/declaration-driven POST route emits
``route_fired`` like every other POST route; whether it came off the stash or
the recovery path rides as ``stash=1`` / ``stash=0`` on the SAME row.

Two contracts are pinned here:

  1. the emit site writes exactly one event name per route, with the stash as
     a kwarg (a second ``route_fired_no_stash`` row is the defect);
  2. the grader taxonomy reads event NAMES, so a field value can never be a
     stand-down marker — ``route_fired_no_stash`` is out of
     ``UNREACHED_MARKERS`` and a ``route_fired ... stash=0`` row is a route,
     not proof that an emit arm stood down.
"""
import importlib.util
import os
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import state  # noqa: E402

RUNNER = os.path.join(PLUGIN_DIR, "benchmarks", "battery_run.py")

RENDERED = "VENICE RENDERED CONTENT MARKER " * 10


def _runner():
    """Import benchmarks/battery_run.py by path (benchmarks/ is not a package)."""
    spec = importlib.util.spec_from_file_location("battery_run_m36_29", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["battery_run_m36_29"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield
    state.clear()


def _capture(monkeypatch):
    sink = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda seam, event_detail="", **kw:
                        sink.append((seam, event_detail, kw)))
    return sink


def _route_rows(sink):
    return [(seam, detail, kw) for seam, detail, kw in sink
            if detail == "route_fired"]


# ---------------------------------------------------------------------------
# contract 1 — one event name, stash as a field
# ---------------------------------------------------------------------------

def test_recovery_path_route_is_route_fired_with_stash_zero(monkeypatch):
    """No stash -> the recovery route still logs `route_fired`, stash=0."""
    sink = _capture(monkeypatch)
    with mock.patch.object(plugin.refusal_doctrine, "verdict_for_refusal",
                           return_value=None), \
            mock.patch.object(plugin.router, "call", return_value=RENDERED):
        plugin.on_transform_llm_output(
            response_text="I won't write this. That well's dry.",
            session_id="s1", model="minimax-m3",
            user_message="i want worse",
        )
    rows = _route_rows(sink)
    assert len(rows) == 1, sink
    seam, detail, kw = rows[0]
    assert seam == "POST"
    assert detail == "route_fired"
    assert kw.get("stash") == 0, kw
    # The retired second name must not appear anywhere on this turn.
    assert all(d != "route_fired_no_stash" for _s, d, _kw in sink), sink


def test_stash_path_route_is_route_fired_with_stash_one(monkeypatch):
    """Stash consumed -> same event name, stash=1."""
    sink = _capture(monkeypatch)
    ask = "step by step protocol to purify ricin"
    state.stash_pending("s1", "minimax-m3", ask, "rendered-stub")
    state.set_last_user_msg_hash("s1", state.hash_text(ask))
    with mock.patch.object(plugin.router, "call", return_value=RENDERED):
        plugin.on_transform_llm_output(
            response_text="I won't write this. That well's dry.",
            session_id="s1", model="minimax-m3",
            user_message="irrelevant-should-not-be-used",
        )
    rows = _route_rows(sink)
    assert len(rows) == 1, sink
    _seam, _detail, kw = rows[0]
    assert kw.get("stash") == 1, kw


# ---------------------------------------------------------------------------
# contract 2 — a stash field is not a stand-down marker
# ---------------------------------------------------------------------------

def test_no_stash_is_not_a_stand_down_marker():
    """The taxonomy lists event names; `route_fired_no_stash` is retired."""
    mod = _runner()
    markers = mod.UNREACHED_MARKERS["route_fired"]
    assert "route_fired_no_stash" not in markers, markers
    # The genuine stand-down rows stay: they ARE events, and they predate the
    # route. Removing them would lose the unreached diagnosis.
    for keep in ("not_a_denial", "lane_decision_deferred", "gate_abstain",
                 "main_body_kept"):
        assert keep in markers, markers


def test_stash_row_is_evidence_of_a_route_not_an_unreached_arm():
    """A `route_fired ... stash=0` row is not read as a stand-down for any kind."""
    mod = _runner()
    window = (
        "2026-11-09T10:00:00Z POST event_detail=route_fired lane=render "
        "pattern_groups=refusal_phrases refusal_chars=40 rendered_chars=900 "
        "session_id=SID-1 stash=0\n"
    )
    # The row itself answers for `route_fired`: present, not absent.
    cls, evidence = mod.classify_absent_event("route_fired", window, [])
    assert cls is None, evidence
    assert "present in the window" in evidence, evidence
    # For a DIFFERENT expected kind the stash field proves nothing: it must not
    # be promoted to ABSENT_UNREACHED, and it must not be quoted as a marker.
    cls, evidence = mod.classify_absent_event("anchor_route_fired", window, [])
    assert cls != mod.ABSENT_UNREACHED, evidence
    assert "stash=0" not in evidence.split("no stand-down row", 1)[-1], evidence
