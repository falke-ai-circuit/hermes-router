"""D3 residuals fix-first (v4.13.5) — evol park-to-deliver + POST gate.

Item 1 repro (evol trail, session api_1790958984_30efbcf5, /tmp/
uncensored-router-evol.log): SEAM-1 terminal verdict (ledger_id=55,
conf=1.0, sig=4ea1200c32e4) consumed mid-run -> a later llm_execution
sweep (the mid-run anchor-consult call whose rolled/trimmed message slice
forces the conservative last_n=0 rescan) saw the user ingress and WIPED
the consumed accumulator -> close_turn drained nothing -> the aggregate
rollup never parked -> the POST benign anchor_banner_consume delivered
only the anchor banner (task_id=ce6eac102085291ca894a404, parked at
16:41:14) and the session's bodies never carried '[decision-lane
advisory]'. Conductor's identical path delivers because its runs carry no
mid-run anchor consult (no rescan wipe before the POST close).

FIX: user ingress in sweep_turn_start resets the RUN CAP (count) only —
consumed-but-undelivered verdicts survive until close_turn drains them
one-shot. Pins run the REAL hook chain: verdict -> mid-run sweep wipe ->
POST close -> delivered body carries the tag, one-shot.

Item 2: post_gate_insufficient_structure over-fired on explicitly
declared short forks ('Quick fork: (A) ... (B) ... One word answer.').
Fix: two DISTINCT parenthesized ordinals bypass the >=20-char consequence
clause; every other shape keeps the strict gate. Pinned both shapes.
"""
import sqlite3
import time

import pytest

import hermes_router as plugin
from hermes_router import (completion_audit, config_access, decision,
                           decision_midturn as dmt, debug_banner,
                           render_inbox, route_gate, router_core, state,
                           usage_ledger)

SID = "api_1790958984_30efbcf5"  # the evol trail session
TAG = "[decision-lane advisory]"

TOOL_OUT = ("Build options:\noption a) ship the fix now\noption b) hold for "
            "the next window")

SHORT_FORK = ("Quick fork: (A) ship now (B) hold back. One word answer.")
UNSTRUCTURED = ("I looked at the logs and the config; nothing jumps out "
                "beyond the usual churn, so I am still reading.")


def _tmp_conn(tmp_path):
    path = str(tmp_path / "plugin.db")
    conn = sqlite3.connect(path, timeout=5.0)
    conn.executescript(decision._LEDGER_SCHEMA)
    return conn


LOGGED = []


@pytest.fixture()
def rr_reset(monkeypatch, tmp_path):
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
                "typesafe_api_key_env": "R3_KEY", "api_key_env": "R3_KEY",
                "confidence_threshold": 0.60, "pre": "shadow",
                "post": False, "post_audit": False, "rescan_dedupe": False})
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    monkeypatch.setenv("R3_KEY", "k")
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


def test_midrun_sweep_wipe_still_delivers_next_turn(rr_reset, monkeypatch):
    """Item 1 pin: verdict consumed at SEAM 1 -> a mid-run llm_execution
    sweep with a ROLLED window (last_n reset, user ingress in view) must
    NOT delete the undelivered verdict -> close_turn still parks the
    aggregate -> the POST benign consume appends the tagged rollup to the
    next turn's body. Pre-fix this wiped `consumed` and the body never
    carried the tag (the evol loss)."""
    _enable_midturn(monkeypatch)
    _mock_backend(monkeypatch)
    plugin.on_transform_terminal_output(command="probe", output=TOOL_OUT,
                                        returncode=0, task_id="t1",
                                        session_id=SID, platform="api_server")
    st = _wait_consumed()
    assert st.get("consumed"), "verdict must be consumed at SEAM 1"
    # the mid-run anchor-consult llm_execution: rolled window (empty first
    # turn means n < last_n -> conservative rescan) + user ingress
    req = {"messages": [{"role": "user", "content": "next window ask?"}]}
    flushed = dmt.flush_and_scan(SID, req)
    assert flushed, "pending advisory must still flush request-side"
    # undelivered accumulator must have SURVIVED the sweep's user ingress
    st = dmt.runs_state().get(SID) or {}
    assert st.get("consumed"), "sweep wipe must not delete undelivered verdicts"
    # POST turn close + benign delivery edge
    out = plugin.on_transform_llm_output(response_text="follow-up body",
                                         session_id=SID, model="m",
                                         platform="api_server")
    assert out and "follow-up body" in out
    assert TAG not in out, out  # R20
    assert 'impulse (decision)' in out
    assert "impulse (decision)" in out
    crec = [f for e, f in LOGGED
            if f.get("event_detail") == "anchor_banner_consume"]
    assert len(crec) == 1 and crec[0].get("parked") is True \
        and crec[0].get("edge") == "benign"
    # one-shot: no re-park, next turn clean (no forever re-park)
    assert not debug_banner._ANCHOR_BANNERS.get(SID)
    out2 = plugin.on_transform_llm_output(response_text="t3 plain",
                                          session_id=SID, model="m",
                                          platform="api_server")
    assert out2 is None
    assert TAG not in (out2 or "")


def test_cap_still_resets_on_user_ingress(rr_reset, monkeypatch):
    """The run-cap counter still resets at user ingress (cap semantics
    kept); only the delivered-state accumulator survives."""
    _enable_midturn(monkeypatch)
    _mock_backend(monkeypatch)
    plugin.on_transform_terminal_output(command="probe", output=TOOL_OUT,
                                        returncode=0, task_id="t1",
                                        session_id=SID, platform="api_server")
    _wait_consumed()
    with dmt._LOCK:
        dmt._state(SID)["count"] = 7
    dmt.flush_and_scan(SID, {"messages": [
        {"role": "user", "content": "new ask"}]})
    st = dmt.runs_state().get(SID) or {}
    assert int(st.get("count") or 0) == 0, "run cap must reset on ingress"
    assert st.get("consumed"), "accumulator must survive"


def test_post_gate_passes_declared_short_fork(rr_reset):
    """Item 2 pin, passing shape: two DISTINCT parenthesized ordinals are
    a declared closed fork — no >=20-char consequence clause required."""
    assert decision._post_gate_ok(SHORT_FORK) is True
    assert decision._post_gate_ok("(A) kafka or (B) rabbitmq") is True


def test_post_gate_still_blocks_unstructured(rr_reset):
    """Item 2 pin, blocked shape: no option markers -> still default-deny."""
    assert decision._post_gate_ok(UNSTRUCTURED) is False
    assert decision._post_gate_ok("") is False
    assert decision._post_gate_ok("just prose, one path, no fork") is False


def test_post_fork_scan_dispatches_on_short_fork(rr_reset, monkeypatch):
    """The consult path actually RUNS now: post_fork_scan on the declared
    short fork extracts 2 options and dispatches the backend call instead
    of logging post_gate_insufficient_structure."""
    _enable_midturn(monkeypatch)
    dcfg = dict(decision.DEFAULTS)
    dcfg.update({"enabled": True, "post": True, "pre": "shadow",
                 "post_audit": False, "backend": "jev_native",
                 "typesafe_api_key_env": "R3_KEY", "api_key_env": "R3_KEY"})
    monkeypatch.setattr(decision, "_cfg", lambda: dcfg)
    _mock_backend(monkeypatch)
    fired = []
    monkeypatch.setattr(decision, "_invoke",
                        lambda *a, **k: fired.append(a), raising=True)
    decision.post_fork_scan(SID, SHORT_FORK, model="m", log_route=plugin._log_route)
    assert fired, "consult must dispatch on a declared short fork"
    gate = [f for e, f in LOGGED if f.get("outcome") ==
            decision.REASON_POST_GATE]
    assert not gate, "gate must not fire on a declared fork"
