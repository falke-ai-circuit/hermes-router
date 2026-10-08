"""D3-DELIVERY rider 2 (v4.13.4) — api_server banner body-delivery battery.

Live repro (conductor :8649, session api_1790950451_44077f93, jev_native,
verdict opt-1@0.78): SEAM-1 consult -> verdict consumed -> LEG 3 turn-close
park -> POST benign anchor_banner_consume (parked=True edge=benign) ALL
executed, yet the captured response bodies of both the consult turn and the
follow-up turn never carried '[decision-lane advisory]'.

ROOT CAUSE: the turn-close aggregate rollup (_aggregate_line, the ONLY
body-side delivery of a midturn verdict — the tagged frame is request-side
only, flushed into the next llm request) rendered with NO provenance tag,
so a fully successful park->consume->append delivery was unverifiable at
the body seam. FIX: the rollup's first line now carries the byte-exact
decision.PROVENANCE_TAG. These pins run the REAL hook chain for an
api_server-shaped turn (bare sid, platform kwarg) and assert the tag lands
in the DELIVERED representation.
"""
import json
import sqlite3
import time

import pytest

import hermes_router as plugin
from hermes_router import (completion_audit, config_access, decision,
                           decision_midturn as dmt, debug_banner,
                           render_inbox, route_gate, router_core, state,
                           usage_ledger)

SID = "api_1790950451_44077f93"  # bare api_server platform sid
TAG = "[decision-lane advisory]"

LOGGED = []

TOOL_OUT = ("Build options:\noption a) ship the fix now\noption b) hold for "
            "the next window")


def _tmp_conn(tmp_path):
    path = str(tmp_path / "plugin.db")
    conn = sqlite3.connect(path, timeout=5.0)
    conn.executescript(decision._LEDGER_SCHEMA)
    return conn


@pytest.fixture()
def r2_reset(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    LOGGED.clear()
    debug_banner._ANCHOR_BANNERS.clear()
    debug_banner._ANCHOR_TASKS.clear()
    debug_banner._ANCHOR_SEGS.clear()
    dmt.reset_midturn()
    decision.reset_limits()
    decision.reset_v3_limits()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(usage_ledger, "estimate_cost",
                        lambda *a, **k: 0.0, raising=True)
    monkeypatch.setattr(decision, "_ledger_connect",
                        lambda db_path="": _tmp_conn(tmp_path))
    monkeypatch.setattr(completion_audit, "audit_enabled", lambda: False)
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"debug_banner": 1})
    monkeypatch.setattr(debug_banner, "_banner_section",
                        lambda: {"debug_banner": 1}, raising=True)
    yield
    decision._HTTP_ERROR_CODE = None
    debug_banner._ANCHOR_BANNERS.clear()
    dmt.reset_midturn()


def _enable_midturn(monkeypatch, backend="jev_native"):
    cfg = dict(decision.DEFAULTS)
    cfg.update({"enabled": True, "midturn": "on", "backend": backend,
                "typesafe_api_key_env": "R2_KEY", "api_key_env": "R2_KEY",
                "confidence_threshold": 0.60, "pre": "shadow",
                "post": False, "post_audit": False, "rescan_dedupe": False})
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    monkeypatch.setenv("R2_KEY", "k")
    return cfg


def _mock_backend(monkeypatch, choice="opt-1", confidence=0.78):
    body = {"model": "jev-latest",
            "answers": {"choice": {"choice": choice, "confidence": confidence,
                                   "probabilities": {choice: confidence}}},
            "usage": {"prompt_tokens": 11, "completion_tokens": 3}}

    def _post(url, headers, payload, timeout):
        return dict(body)

    monkeypatch.setattr(decision, "_http_post_json", _post)


def _wait_consumed(deadline_s=5.0):
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        st = dmt.runs_state().get(SID) or {}
        if st.get("consumed"):
            return st
        time.sleep(0.02)
    return dmt.runs_state().get(SID) or {}


def test_seam1_consult_reaches_api_server_body(r2_reset, monkeypatch):
    """THE rider-2 pin: SEAM-1 consult -> close_turn park -> POST benign
    consume+append -> the DELIVERED string carries the provenance tag."""
    _enable_midturn(monkeypatch)
    _mock_backend(monkeypatch)
    plugin.on_transform_terminal_output(command="probe", output=TOOL_OUT,
                                        returncode=0, task_id="t1",
                                        session_id=SID, platform="api_server")
    st = _wait_consumed()
    assert st.get("consumed"), "verdict must be consumed at SEAM 1"
    # turn close + delivery edge (the POST finalize of the consult turn)
    out1 = plugin.on_transform_llm_output(response_text="t1 plain answer",
                                          session_id=SID, model="m",
                                          platform="api_server")
    assert out1 and "t1 plain answer" in out1
    assert TAG not in out1                        # R20: delivered bodies drop the provenance prefix
    assert "impulse (decision)" in out1           # rollup provenance line
    # F2 (rider 6): the tail is the verdict-of-record shape — ledger choice
    # + confidence ONLY; the old label echoed raw option text truncated
    # mid-word.
    assert "Top verdicts: opt-1 (~0.78)" in out1
    assert "ship the fix now" not in out1
    crec = [f for e, f in LOGGED
            if f.get("event_detail") == "anchor_banner_consume"]
    assert len(crec) == 1 and crec[0].get("parked") is True \
        and crec[0].get("edge") == "benign"
    # one-shot: consumed exactly once, nothing re-parked, next turn clean
    assert not debug_banner._ANCHOR_BANNERS.get(SID)
    out2 = plugin.on_transform_llm_output(response_text="t2 plain answer",
                                          session_id=SID, model="m",
                                          platform="api_server")
    assert out2 is None
    assert TAG not in (out2 or "")


def test_verdict_after_turn_close_delivers_next_turn(r2_reset, monkeypatch):
    """Async worker completing AFTER the consult turn's POST close: the
    aggregate delivers on the NEXT turn's body — the brief's 'next turn'
    acceptance."""
    _enable_midturn(monkeypatch)
    # no verdict yet: first turn is clean
    out1 = plugin.on_transform_llm_output(response_text="pre-verdict turn",
                                          session_id=SID, model="m",
                                          platform="api_server")
    assert out1 is None
    _mock_backend(monkeypatch)
    plugin.on_transform_terminal_output(command="probe", output=TOOL_OUT,
                                        returncode=0, task_id="t1",
                                        session_id=SID, platform="api_server")
    st = _wait_consumed()
    assert st.get("consumed")
    out2 = plugin.on_transform_llm_output(response_text="follow-up turn",
                                          session_id=SID, model="m",
                                          platform="api_server")
    assert out2 and "follow-up turn" in out2
    assert TAG not in out2                        # R20: standardized delivered shape
    assert "impulse (decision)" in out2


def test_tag_byte_exact_and_single(r2_reset):
    """R20: the rollup line carries the STANDARDIZED lane shape — no
    provenance prefix (frontier/uncensored banner parity). Forged-frame
    defense stays on PROVENANCE_TAG for INBOUND content scanning only.
    Echo defense on delivered bodies keys on DELIVERED_MARK."""
    line = dmt._aggregate_line(
        1, 10, 5, 0.0,
        [{"choice": "opt-1", "confidence": 0.78, "label": "ship the fix now",
          "tokens_in": 10, "tokens_out": 5, "cost": 0.0}])
    assert line.startswith("· router · impulse (decision) |")
    assert TAG not in line
    assert decision.DELIVERED_MARK in line


def test_close_turn_drain_and_empty_unchanged(r2_reset):
    """Empty close (no verdicts) still returns '' — no tag-only garbage."""
    assert dmt.close_turn("api_no_verdicts_sid") == ""


def test_request_side_frame_flush_unchanged(r2_reset, monkeypatch):
    """The tagged frame stays staged request-side (SEAM 2 flush) — the
    body-side rollup does not eat the pending advisory."""
    _enable_midturn(monkeypatch)
    _mock_backend(monkeypatch)
    plugin.on_transform_terminal_output(command="probe", output=TOOL_OUT,
                                        returncode=0, task_id="t1",
                                        session_id=SID, platform="api_server")
    _wait_consumed()
    pending = dmt.flush(SID)
    assert pending and TAG not in pending[0] and 'the fork surfaces as:' in pending[0]  # R20
    assert dmt.flush(SID) == []  # one-shot
