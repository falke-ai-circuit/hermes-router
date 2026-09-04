"""Option 3 verdict wiring tests: flinch routes, agent_line honored."""
import os
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PARENT_DIR, PLUGIN_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router import refusal_doctrine, semantic_classifier as sc  # noqa: E402


def _cfg(mode="route"):
    return {"enabled": True,
            "classification": {"post_classify": True, "pre_classify": True,
                               "match_threshold": 1, "aux_classify": True,
                               "mode": mode, "doctrine_verdict": True,
                               "aux_endpoint": {"url": "http://aux", "model": "M"}}}


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    state.clear()
    sc.reset_limits()
    refusal_doctrine._CACHE.clear()
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    # doctrine card source: tiny DNA in tmp home
    (tmp_path / "SOUL.md").write_text("# L\n## Lines I Hold\n- never do X\n")
    monkeypatch.setattr(refusal_doctrine, "_hermes_home", lambda: str(tmp_path))
    yield
    state.clear()


REFUSAL = ("I'm sorry, but I can't help with that request. It goes against "
           "my guidelines and I must decline.")


def test_flinch_routes(monkeypatch):
    """No doctrine backing (no matching rows in card) -> aux says model_flinch -> ROUTE fires."""
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg("route"))
    monkeypatch.setattr(sc, "aux_raw_call",
                        lambda prompt, cfg=None, record_success=True: "model_flinch")
    state.record_last_seen("sf", "ask about Y")
    with mock.patch.object(plugin.router, "call", return_value="RENDERED") as v:
        out = plugin.on_transform_llm_output(
            response_text=REFUSAL, session_id="sf", model="m1")
    assert v.called and out == "RENDERED"


def test_agent_line_honored_no_route(monkeypatch):
    """Doctrine rows back the refusal -> return None (pass-through), no render call."""
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg("route"))
    # aux sees doctrine card; returns agent_line (simulating a match)
    monkeypatch.setattr(sc, "aux_raw_call",
                        lambda prompt, cfg=None, record_success=True: "agent_line")
    state.record_last_seen("sl", "ask about X")
    with mock.patch.object(plugin.router, "call", return_value="RENDERED") as v:
        out = plugin.on_transform_llm_output(
            response_text=REFUSAL, session_id="sl", model="m1")
    assert out is None and not v.called


def test_verdict_aux_failure_falls_through_to_route(monkeypatch):
    """Aux down -> verdict None -> status-quo routing (fail-open)."""
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg("route"))
    monkeypatch.setattr(sc, "aux_raw_call", lambda prompt, cfg=None, record_success=True: None)
    state.record_last_seen("sn", "ask about Z")
    with mock.patch.object(plugin.router, "call", return_value="RENDERED") as v:
        out = plugin.on_transform_llm_output(
            response_text=REFUSAL, session_id="sn", model="m1")
    assert v.called and out == "RENDERED"


def test_doctrine_toggle_off_skips_verdict(monkeypatch):
    c = _cfg("route")
    c["classification"]["doctrine_verdict"] = False
    monkeypatch.setattr(plugin, "_cfg", lambda: c)
    monkeypatch.setattr(sc, "aux_raw_call",
                        lambda *a, **k: pytest.fail("verdict must not fire"))
    state.record_last_seen("so", "ask about W")
    with mock.patch.object(plugin.router, "call", return_value="RENDERED") as v:
        out = plugin.on_transform_llm_output(
            response_text=REFUSAL, session_id="so", model="m1")
    assert v.called and out == "RENDERED"
