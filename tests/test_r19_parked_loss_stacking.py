"""FIX-FIRST rider 4 (v4.13.7) — parked-loss round-trip + D1 stacking + midturn pseudo-fire pins.

Reviewer battery_final_20261002 (11/14 PASS) open defects:

1/3 PARKED-LOSS + OPERATIVE BANNER-LESS: verdicts fired, billed, and were
correct, but the banner never reached the body. Root cause: the render
capture (render_inbox.record_render + canonical.rewrite_persisted_turn)
existed ONLY at the uncensored-render seam; the benign, audit_sync and
empty-body parked paths bypassed capture entirely, and a 0-char model body
bypassed the transform altogether (early return), so a parked banner could
never attach (probes api_1790976140_acd77605 / api_1790976260_827dbf1b:
0-char delivered bodies, choice_head None). Fix: park->deliver is now
exhaustive across all seams and every parked delivery edge captures the
render (persisted transcript == delivered text, round-trip).

2 D1 LANE-PRECEDENCE STEAL: one turn with a declared decision fork AND a
declared frontier consult ran ONLY the decision lane (specimen
api_1790972692_ced09e3f) — _decision_lane_claim consumed the turn's
declared slot and the frontier consult died. Fix: stacking — the decision
advisory (never a consult) re-registers the frontier claim for the gate's
execute-once pass.

4 MIDTURN PSEUDO-FIRES: benign prose ('X or Y' fallback inside tool
results) reached the backend consult. Fix: the midturn seams require an
explicitly DECLARED closed-fork structure; declared (A)/(B) forks (the D2
axis shape) still pass.
"""
import json
import sqlite3
import time

import pytest

import hermes_router as plugin
from hermes_router import (canonical, completion_audit, config_access,
                           decision, decision_midturn as dmt, debug_banner,
                           render_inbox, route_gate, router_core, state,
                           usage_ledger)

SID = "api_1790972692_ced09e3f"  # reviewer D1 specimen sid
TAG = "[decision-lane advisory]"

LOGGED = []

DECLARED_TOOL_OUT = ("Build options:\noption a) ship the fix now\n"
                     "option b) hold for the next window")
PROSE_TOOL_OUT = ("deploy finished, see log for the stdout or stderr "
                  "redirect; nothing to decide here")


def _tmp_conn(tmp_path):
    path = str(tmp_path / "plugin.db")
    conn = sqlite3.connect(path, timeout=5.0)
    conn.executescript(decision._LEDGER_SCHEMA)
    return conn


@pytest.fixture()
def r4_reset(monkeypatch, tmp_path):
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
    # persisted-transcript capture: point the canonical store at a tmp db
    dbp = str(tmp_path / "state.db")
    conn = sqlite3.connect(dbp, timeout=5.0)
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS messages ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " session_id TEXT, role TEXT, content TEXT, api_content TEXT);")
    conn.commit()
    conn.close()
    monkeypatch.setattr(canonical, "_state_db_path", lambda: dbp)
    inbox = str(tmp_path / "renders.jsonl")
    monkeypatch.setattr(render_inbox, "_inbox_path", lambda: inbox)
    yield tmp_path
    decision._HTTP_ERROR_CODE = None
    debug_banner._ANCHOR_BANNERS.clear()
    dmt.reset_midturn()


def _enable_midturn(monkeypatch, backend="jev_native"):
    cfg = dict(decision.DEFAULTS)
    cfg.update({"enabled": True, "midturn": "on", "backend": backend,
                "typesafe_api_key_env": "R4_KEY", "api_key_env": "R4_KEY",
                "confidence_threshold": 0.60, "pre": "shadow",
                "post": False, "post_audit": False, "rescan_dedupe": False})
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    monkeypatch.setenv("R4_KEY", "k")
    return cfg


def _mock_backend(monkeypatch, calls, choice="opt-1", confidence=0.78):
    body = {"model": "jev-latest",
            "answers": {"choice": {"choice": choice, "confidence": confidence,
                                   "probabilities": {choice: confidence}}},
            "usage": {"prompt_tokens": 11, "completion_tokens": 3}}

    def _post(url, headers, payload, timeout):
        calls.append(1)
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


def _captured(tmp_path):
    """Renders captured at any parked delivery edge this test."""
    try:
        inbox = str(tmp_path / "renders.jsonl")
        with open(inbox, "r", encoding="utf-8") as fh:
            return [json.loads(ln) for ln in fh.read().splitlines() if ln]
    except OSError:
        return []


def _persisted(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "state.db"), timeout=5.0)
    try:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ?"
            " ORDER BY id DESC LIMIT 1", (SID,)).fetchall()
    finally:
        conn.close()
    return rows


# --------------------------------------------------------------------------
# Item 1/3: parked-loss round trip per delivery edge
# --------------------------------------------------------------------------

def test_park_round_trip_benign_edge(r4_reset, monkeypatch):
    """SEAM-1 consult -> close_turn park -> POST benign consume+append on a
    NON-EMPTY body: the delivered string carries the tag AND the render is
    captured (inbox row + persisted transcript rewritten)."""
    _enable_midturn(monkeypatch)
    _mock_backend(monkeypatch, [])
    # turn_finalizer persists the RAW row BEFORE the transform fires —
    # simulate that row first so the hook's round-trip rewrite lands on it.
    dbp = str(r4_reset / "state.db")
    conn = sqlite3.connect(dbp, timeout=5.0)
    conn.execute("INSERT INTO messages (session_id, role, content) VALUES"
                 " (?, 'assistant', ?)", (SID, "t1 plain answer"))
    conn.commit()
    conn.close()
    plugin.on_transform_terminal_output(command="probe", output=DECLARED_TOOL_OUT,
                                        returncode=0, task_id="t1",
                                        session_id=SID, platform="api_server")
    assert _wait_consumed().get("consumed")
    out1 = plugin.on_transform_llm_output(response_text="t1 plain answer",
                                          session_id=SID, model="m",
                                          platform="api_server")
    assert out1 and "t1 plain answer" in out1
    assert TAG in out1
    # round-trip: the DELIVERED text is captured on the parked path
    caps = [c for c in _captured(r4_reset) if c.get("stage") == "BENIGN_BANNER"]
    assert caps and TAG in caps[-1]["render"], caps
    assert [f for e, f in LOGGED
            if f.get("event_detail") == "banner_render_captured"
            and f.get("edge") == "benign"]
    rows = _persisted(r4_reset)
    assert rows and rows[0][0] == "assistant" and TAG in rows[0][1]


def test_park_round_trip_empty_body_edge(r4_reset, monkeypatch):
    """THE parked-loss repro: verdicts fire and park, but the model body is
    0 chars — the old early-return dropped the turn entirely (0-char
    delivered transcripts). The empty-body edge now delivers the parked
    banner ALONE and captures it."""
    _enable_midturn(monkeypatch)
    _mock_backend(monkeypatch, [])
    plugin.on_transform_terminal_output(command="probe", output=DECLARED_TOOL_OUT,
                                        returncode=0, task_id="t2",
                                        session_id=SID, platform="api_server")
    assert _wait_consumed().get("consumed")
    # the 0-char turn itself: midturn rollup parks, banner delivers alone
    out = plugin.on_transform_llm_output(response_text="",
                                         session_id=SID, model="m",
                                         platform="api_server")
    assert out, "parked verdict must deliver even on an empty body"
    assert TAG in out
    caps = [c for c in _captured(r4_reset)
            if c.get("stage") == "EMPTY_BODY_BANNER"]
    assert caps and TAG in caps[-1]["render"]
    assert [f for e, f in LOGGED
            if f.get("event_detail") == "anchor_banner_consume"
            and f.get("edge") == "empty_body"]
    assert [f for e, f in LOGGED
            if f.get("event_detail") == "banner_render_captured"
            and f.get("edge") == "empty_body"]


def test_park_round_trip_audit_sync_edge(r4_reset, monkeypatch):
    """Audit-sync return consumes the same-turn park: delivered + captured."""
    _enable_midturn(monkeypatch)
    _mock_backend(monkeypatch, [])
    from hermes_router import completion_audit as ca

    # turn_finalizer persists the RAW row BEFORE the transform fires —
    # simulate that row first so the hook's round-trip rewrite lands on it.
    dbp = str(r4_reset / "state.db")
    conn = sqlite3.connect(dbp, timeout=5.0)
    conn.execute("INSERT INTO messages (session_id, role, content) VALUES"
                 " (?, 'assistant', ?)", (SID, "t3 answer"))
    conn.commit()
    conn.close()

    plugin.on_transform_terminal_output(command="probe", output=DECLARED_TOOL_OUT,
                                        returncode=0, task_id="t3",
                                        session_id=SID, platform="api_server")
    assert _wait_consumed().get("consumed")
    # same-turn park during the audit gate: stage the audit BEFORE the turn
    # close so the audit_sync edge owns the consume (R9 seam)
    debug_banner._ANCHOR_BANNERS.clear()
    debug_banner.park_anchor_banner(SID, "· router · parked audit note ·",
                                    task_id="audit")
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "audit_gate",
                        lambda sid, text, **k: "audited revision of: " + text)
    out = plugin.on_transform_llm_output(response_text="t3 answer",
                                         session_id=SID, model="m",
                                         platform="api_server")
    assert out and out.startswith("audited revision of:")
    assert "parked audit note" in out
    caps = [c for c in _captured(r4_reset)
            if c.get("stage") == "AUDIT_SYNC_BANNER"]
    assert caps and "parked audit note" in caps[-1]["render"]
    assert [f for e, f in LOGGED
            if f.get("event_detail") == "banner_render_captured"
            and f.get("edge") == "audit_sync"]


def test_canonical_round_trip_rewrite(r4_reset):
    """Round-trip pin for the render seam: the persisted row is rewritten
    from the pre-banner text to the delivered text, and the empty-row
    variant (empty-body edge) rewrites a 0-char persisted turn."""
    dbp = str(r4_reset / "state.db")
    conn = sqlite3.connect(dbp, timeout=5.0)
    conn.execute("INSERT INTO messages (session_id, role, content) VALUES"
                 " (?, 'assistant', ?)", (SID, "render body pre-banner"))
    conn.commit()
    conn.close()
    assert canonical.rewrite_persisted_turn(
        SID, "render body pre-banner", "render body pre-banner\n\nBANNER")
    rows = _persisted(r4_reset)
    assert rows[0][1] == "render body pre-banner\n\nBANNER"
    # empty-row variant
    conn = sqlite3.connect(dbp, timeout=5.0)
    conn.execute("INSERT INTO messages (session_id, role, content) VALUES"
                 " (?, 'assistant', '')", (SID,))
    conn.commit()
    conn.close()
    assert canonical.rewrite_persisted_turn(SID, "", "BANNER-ONLY BODY",
                                            allow_empty_match=True)
    rows = _persisted(r4_reset)
    assert rows[0][1] == "BANNER-ONLY BODY"
    # guard: without allow_empty_match an empty refusal_text never rewrites
    assert canonical.rewrite_persisted_turn(SID, "", "nope") is False


# --------------------------------------------------------------------------
# Item 2: D1 lane-precedence stacking pin
# --------------------------------------------------------------------------

def test_d1_stacking_decision_plus_frontier(r4_reset, monkeypatch):
    """One turn with a declared decision fork AND a declared frontier
    consult: BOTH fire — the decision advisory runs AND the frontier
    consult stages (the old flow ran only the decision lane)."""
    _enable_midturn(monkeypatch)
    calls = []
    _mock_backend(monkeypatch, calls)
    cfg = dict(decision.DEFAULTS)
    cfg.update({"enabled": True, "pre": "shadow", "post": False,
                "post_audit": False, "rescan_dedupe": False,
                "backend": "jev_native", "typesafe_api_key_env": "R4_KEY",
                "api_key_env": "R4_KEY",
                "on_demand": {"manual": True, "midturn": True}})
    monkeypatch.setenv("R4_KEY", "k")
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    monkeypatch.setattr(route_gate, "on_demand_routing_enabled", lambda: True)
    content = "decide this: (A) ship now (B) hold back\n" \
              "consult frontier about the migration plan"
    # agent registers a declared DECISION claim mid-turn (request_routing)
    route_gate.register_declared(SID, route_gate.LANE_DECISION,
                                 route_gate.SOURCE_DECLARED_AGENT)
    dec = route_gate.claim_pass(content, SID, "m")
    assert dec.route is False  # decision lane: advisory only, turn proceeds
    # the decision advisory fired (backend called for the decision fork)
    deadline = time.time() + 5.0
    while time.time() < deadline and not calls:
        time.sleep(0.02)
    assert calls, "decision advisory must fire"
    # STACKING: the frontier consult is queued (pending declared claim) and
    # the next pass of the same turn executes it
    pending = route_gate.peek_declared(SID)
    assert pending is not None and pending.get("lane") == \
        route_gate.LANE_HIGHER_PRE, pending
    dec2 = route_gate.claim_pass(content, SID, "m")
    assert dec2.route is True and dec2.lane == route_gate.LANE_HIGHER_PRE
    assert [f for e, f in LOGGED
            if f.get("event_detail") == "declared_frontier_stacked"]
    # the claim's consult envelope stages (request_routing_executed)
    assert [f for e, f in LOGGED
            if f.get("event_detail") == "request_routing_executed"]


# --------------------------------------------------------------------------
# Item 4: midturn pseudo-fire suppression, declared forks kept
# --------------------------------------------------------------------------

def test_midturn_pseudo_fire_suppressed(r4_reset, monkeypatch):
    """Benign prose ('X or Y' inside a tool result) must NOT reach the
    backend consult; a declared (A)/(B) fork still does."""
    _enable_midturn(monkeypatch)
    calls = []
    _mock_backend(monkeypatch, calls)
    # benign prose: prose 'or' fallback only — no declared structure
    plugin.on_transform_terminal_output(command="probe", output=PROSE_TOOL_OUT,
                                        returncode=0, task_id="t4",
                                        session_id=SID, platform="api_server")
    assert not calls, "benign prose must never reach the consult backend"
    assert [f for e, f in LOGGED
            if e == "midturn_suppressed"
            and f.get("reason") == "no_declared_structure"]
    # declared (A)/(B) fork — the reviewer D2 axis shape — still fires
    plugin.on_transform_terminal_output(
        command="probe",
        output="Quick fork: (A) ship now (B) hold back. One word answer.",
        returncode=0, task_id="t5", session_id=SID, platform="api_server")
    deadline = time.time() + 5.0
    while time.time() < deadline and not calls:
        time.sleep(0.02)
    assert calls, "declared (A)/(B) fork must still consult"
    st = _wait_consumed()
    assert st.get("consumed")


def test_midturn_declared_structure_helper(r4_reset):
    assert decision.has_declared_fork_structure(
        "Quick fork: (A) ship now (B) hold back.")
    assert decision.has_declared_fork_structure(DECLARED_TOOL_OUT)
    assert decision.has_declared_fork_structure(
        "Approach 1: build it\nApproach 2: buy it")
    assert not decision.has_declared_fork_structure(
        "use stdout or stderr, then decide the layout")
    assert not decision.has_declared_fork_structure("")
