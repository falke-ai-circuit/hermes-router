"""R18 — double-banner fix + aux consult burst pacing (Goran 09-24).

Leg 1 (banner): park_anchor_banner is latest-wins — two consults parked
within one turn deliver ONE banner (the most recent consult's), never a
merged multi-line block. Every billed call keeps full provenance in the
route log.

Leg 2 (pacing): complexity.aux_consult_min_interval_sec (default 300,
0=disabled) gates machine-detected aux_intent consults per session — no
second aux consult within the interval. Declared-user consults are never
gated. Suppression is logged (aux_consult_interval_suppressed).
"""
import time

import pytest

import hermes_router as plugin
from hermes_router import config_access, debug_banner, route_gate, router_core

SID = "s-r18"

LOGGED = []


@pytest.fixture()
def _reset(monkeypatch):
    router_core._test_reset()
    plugin.state.clear()
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    LOGGED.clear()
    debug_banner._ANCHOR_BANNERS.clear()
    route_gate._AUX_CONSULT_LAST.clear()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    yield
    debug_banner._ANCHOR_BANNERS.clear()
    route_gate._AUX_CONSULT_LAST.clear()


# --------------------------------------------------------------------------
# Leg 1: latest-wins park
# --------------------------------------------------------------------------

def test_park_latest_wins_single_banner(_reset):
    debug_banner.park_anchor_banner(SID, "· router · higher-self | consult A",
                                    task_id="t1")
    debug_banner.park_anchor_banner(SID, "· router · higher-self | consult B",
                                    task_id="t2")
    out = debug_banner.consume_parked_banner(SID)
    assert "consult B" in out
    assert "consult A" not in out          # no merged double banner
    assert debug_banner.consume_parked_banner(SID) == ""  # one-shot


def test_park_empty_then_real(_reset):
    debug_banner.park_anchor_banner(SID, "", task_id="t0")
    debug_banner.park_anchor_banner(SID, "banner-X", task_id="t1")
    assert debug_banner.consume_parked_banner(SID) == "banner-X"


# --------------------------------------------------------------------------
# Leg 2: aux consult burst pacing
# --------------------------------------------------------------------------

def _aux_decision(monkeypatch, lane="higher"):
    """Drive _aux_intent_decision with a fixed verdict (no network)."""
    import hermes_router.intent_classifier as _icm
    monkeypatch.setattr(_icm, "_intent_suspect", lambda text: True)
    monkeypatch.setattr(_icm, "classify_intent",
                        lambda text, sid, log_route=None:
                        {"lane": lane, "confidence": 0.9})
    return route_gate._aux_intent_decision("some autonomous dispatch text", SID)


def test_aux_second_consult_within_interval_suppressed(_reset, monkeypatch):
    monkeypatch.setattr(router_core, "aux_consult_min_interval", lambda: 300)
    d1 = _aux_decision(monkeypatch)
    assert d1 is not None and d1.route
    d2 = _aux_decision(monkeypatch)
    assert d2 is not None and not d2.route
    assert d2.reason == "aux_consult_interval"
    assert any(f.get("event_detail") == "aux_consult_interval_suppressed"
               for _, f in LOGGED)


def test_aux_consult_after_interval_routes_again(_reset, monkeypatch):
    monkeypatch.setattr(router_core, "aux_consult_min_interval", lambda: 300)
    assert _aux_decision(monkeypatch).route
    # age the last-fire timestamp past the interval
    route_gate._AUX_CONSULT_LAST[SID] = time.time() - 301
    assert _aux_decision(monkeypatch).route


def test_aux_pacing_disabled_at_zero(_reset, monkeypatch):
    monkeypatch.setattr(router_core, "aux_consult_min_interval", lambda: 0)
    assert _aux_decision(monkeypatch).route
    assert _aux_decision(monkeypatch).route
    assert _aux_decision(monkeypatch).route


def test_aux_interval_default_300_via_knob(_reset, monkeypatch):
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"pre_mode": "route"})
    assert router_core.aux_consult_min_interval() == 300
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"aux_consult_min_interval_sec": 600})
    assert router_core.aux_consult_min_interval() == 600


def test_state_bounded_256(_reset, monkeypatch):
    monkeypatch.setattr(router_core, "aux_consult_min_interval", lambda: 300)
    for i in range(300):
        sid = f"s-burst-{i}"
        monkeypatch.setattr(route_gate, "_intent_aux_suspect",
                            lambda t, s, _i=i: True, raising=False)
        monkeypatch.setattr(route_gate, "_intent_classify",
                            lambda t, s: {"lane": "higher", "confidence": 0.9},
                            raising=False)
        route_gate._aux_intent_decision("text", sid)
    assert len(route_gate._AUX_CONSULT_LAST) <= 256
