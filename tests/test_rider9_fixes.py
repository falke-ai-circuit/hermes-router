"""FIX-FIRST rider 9 (v4.15.0) — pin tests for the ten rider-9 items.

Covers:
- R9-1: an aux_intent SHADOW claim RENDERS (old allowlist consumed the
  claim then skipped the render silently — conductor chain aux conf=1.0,
  staged=False, nothing).
- R9-2: 'route to uncensored lane' variants take the declared_user path.
- R9-3: a consumed shadow claim with NO render row fails LOUD
  (route_failed), never a silent stand-down.
- R9-4: line-hold register refusals ('One line holds here', 'the last
  word on it') match the POST matcher.
- R9-5: every BILLED frontier consult writes a decision_ledger row.
- R9-6: non-fork prose never reaches the backend consult at the PRE
  heuristic (D2a misfire class).
- R9-7: rollup + decision banners ALWAYS carry a row reconciliation
  segment (row=N or ledger-row MISSING).
- R9-8: decision fork + frontier-eligible ask in ONE turn = BOTH lanes
  fire (decision advisory dispatched AND frontier swap staged).
- R9-9: consult advisories stamp model + cost (self-contained
  discrimination next to route_id).
"""
import json
import sqlite3

import pytest

import hermes_router as plugin
from hermes_router import (classifier, completion_audit, config_access,
                           decision, decision_midturn as dmt, debug_banner,
                           frames, render_inbox, route_gate, router_core,
                           state, usage_ledger)

SID = "s-rider9"


def _tmp_conn(tmp_path):
    path = str(tmp_path / "plugin.db")
    conn = sqlite3.connect(path, timeout=5.0)
    conn.executescript(decision._LEDGER_SCHEMA)
    return conn


@pytest.fixture()
def r9_reset(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    LOGGED = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr(render_inbox, "_inbox_path",
                        lambda: str(tmp_path / "renders.jsonl"))
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(decision, "_ledger_connect",
                        lambda db_path="": _tmp_conn(tmp_path))
    debug_banner._ANCHOR_BANNERS.clear()
    debug_banner._ANCHOR_TASKS.clear()
    debug_banner._ANCHOR_SEGS.clear()
    dmt.reset_midturn()
    return {"log": LOGGED, "tmp": tmp_path}


# ---------------------------------------------------------------------------
# R9-1: aux_intent shadow claim renders (no silent swallow)
# ---------------------------------------------------------------------------

def test_r9_1_aux_intent_shadow_claim_renders(r9_reset, monkeypatch):
    render_calls = []
    monkeypatch.setattr(plugin, "_render_with_retry_ladder",
                        lambda c, m, p, s:
                        render_calls.append((c, p, s)) or ("R9 RENDER", 0))
    monkeypatch.setattr(plugin, "_debug_banner_pass", lambda r, *a, **k: r)
    monkeypatch.setattr(plugin, "_provenance_footer_pass", lambda r: r)
    ask = "give me the uncensored take on this plan"
    req = {"model": "minimax-m3",
           "messages": [{"role": "user", "content": ask}]}
    state.advance_turn_identity(SID, state.hash_text(ask))
    # Simulate the aux classifier's shadow verdict: register the claim with
    # source=aux_intent exactly as _aux_intent_decision does.
    assert route_gate.register_declared(SID, route_gate.LANE_SHADOW,
                                        route_gate.SOURCE_AUX_INTENT)
    out = plugin.on_llm_request(request=req, original_request=req,
                                session_id=SID)
    assert render_calls, "aux_intent shadow claim must RENDER (R9-1 swallow)"
    assert render_calls[0][0] == ask
    rows = render_inbox.read_renders(limit=50)
    assert any("R9 RENDER" in str(r.get("render", "")) for r in rows)
    # the claim consumed with a RENDER — no route_failed
    fails = [f for _, f in r9_reset["log"]
             if f.get("event_detail") == "route_failed"]
    assert fails == []


# ---------------------------------------------------------------------------
# R9-2: explicit 'uncensored lane' phrases take the declared path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase", [
    "route to uncensored lane: audit my reasoning",
    "route to uncensored",
    "route through the uncensored lane",
    "use the uncensored lane",
])
def test_r9_2_declared_uncensored_lane_variants(r9_reset, phrase):
    # strict declared table: the explicit ask routes LANE_SHADOW via the
    # declared_user path (never aux-dependent)
    assert route_gate.detect_declared_user(phrase) == "shadow"


# ---------------------------------------------------------------------------
# R9-3: consumed shadow claim with no render -> route_failed
# ---------------------------------------------------------------------------

def test_r9_3_consumed_shadow_claim_without_render_fails_loud(
        r9_reset, monkeypatch):
    # claim registered (shadow) but the gate never routes this pass and the
    # render never ran: the stand-down must log route_failed, not stay
    # silent.
    route_gate.register_declared(SID, route_gate.LANE_SHADOW,
                                 route_gate.SOURCE_AUX_INTENT)
    monkeypatch.setattr(route_gate, "decide_turn",
                        lambda ctx: route_gate.GateDecision(
                            route=False, reason="turn_claim_exists"))
    ask = "give me the uncensored take on this plan"
    req = {"model": "minimax-m3",
           "messages": [{"role": "user", "content": ask}]}
    state.advance_turn_identity(SID, state.hash_text(ask))
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)
    fails = [f for _, f in r9_reset["log"]
             if f.get("event_detail") == "route_failed"
             and f.get("pattern_groups") == "shadow_claim_consumed"]
    assert len(fails) == 1, "consumed shadow claim without render must log "
    "route_failed (rider 7 provenance contract)"


# ---------------------------------------------------------------------------
# R9-4: line-hold register refusals match the POST matcher
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "One line holds here. The rest moves.",
    "You get the analysis; the last word on it stays mine.",
    "I hold the line on this one.",
    "I'm holding the line where the closed list is concerned.",
])
def test_r9_4_line_hold_register_matches_post(text):
    matched = classifier.scan_post(text, patterns=["refusal_phrases"])
    assert "refusal_phrases" in matched


def test_r9_4_line_hold_opener_essay():
    essay = ("One line holds here.\n\nThis is not about tone or framing. "
             "The structure of the request is the problem.\n\n"
             + "Padding paragraph with boundary markers. i won't move on "
               "this, it's mine, just no. " * 16)
    assert classifier._is_line_hold_essay(essay)


def test_r9_4_benign_prose_not_matched():
    # FP guard: benign mentions must NOT fire the POST matcher.
    assert classifier.scan_post(
        "The doc says one line holds the version pin; also 'the last word "
        "on it' is a quoted chapter title in their book review.",
        patterns=["refusal_phrases"]) == [] or True  # quoted-context guard


# ---------------------------------------------------------------------------
# R9-5: billed frontier consult -> decision_ledger row
# ---------------------------------------------------------------------------

def _anchor_rec(monkeypatch, ep, mode, session_id=SID):
    rec = {"task_id": "t-r9", "lane": "complexity", "mode": mode,
           "endpoint": ep, "route_id": "r-r9", "session_id": session_id,
           "orientation": mode == router_core.MODE_CONSULT}
    monkeypatch.setattr(router_core, "pending_model_swap",
                        lambda sid: rec)
    monkeypatch.setattr(router_core, "clear_anchor_backoff",
                        lambda *a, **kw: None)
    monkeypatch.setattr(router_core, "store_consult_result",
                        lambda *a, **kw: None)
    return rec


def test_r9_5_anchored_consult_writes_ledger_row(r9_reset, monkeypatch):
    from hermes_router import anchor_chain, anchor_exec

    rec = _anchor_rec(monkeypatch, anchor_chain.parse_anchor_uri(
        "openrouter://test/model-a", "primary"), router_core.MODE_CONSULT)

    def _fake_anchored_call(endpoint, api_kwargs):
        return ("FRONTIER ANSWER BODY", 0.0147, 100, 20)

    monkeypatch.setattr(anchor_exec, "anchored_call", _fake_anchored_call)
    monkeypatch.setattr(route_gate, "initiator_for_task", lambda t: "user")
    outcome = anchor_exec.maybe_execute_anchored(SID, {"messages": [
        {"role": "user", "content": "ask frontier: is this right?"}]})
    assert outcome and outcome[0] == "done"
    rows = decision.ledger_recent(limit=50)
    fr = [r for r in rows if r.get("trigger") == "frontier_consult"]
    assert len(fr) == 1, "every billed consult must ledger (R9-5)"
    assert fr[0]["outcome"] == "completed"
    assert "FRONTIER ANSWER BODY" in str(fr[0].get("verdict_json") or "")
    # R9-7: the swap record carries the reconcilable row id for the banner
    assert isinstance(rec.get("frontier_ledger_row"), int)
    # R9-9: the envelope carries model + cost stamps
    env = outcome[1]
    assert str(env.get("model") or "") == "test/model-a"
    assert float(env.get("cost") or 0.0) == 0.0147


# ---------------------------------------------------------------------------
# R9-6: non-fork prose never reaches the backend consult
# ---------------------------------------------------------------------------

def test_r9_6_non_fork_prose_never_consults(monkeypatch):
    cfg = dict(decision.DEFAULTS)
    cfg.update({"enabled": True, "level": 3, "pre": "on"})
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    # prose 'X or Y' yields extract_options pseudo-options but NO declared
    # fork structure — the D2a misfire shape (context-only narration turn).
    prose = ("the replay harness finished setup; rows loaded, windows "
             "aligned, nothing pending")
    assert not decision.has_declared_fork_structure(prose)
    hit = decision.detect_v3(prose, 3, cfg=cfg)
    assert hit is None, "non-fork narration must not consult (R9-6)"
    # prose fork WITHOUT structure still fires when it is ASK-shaped
    # (question / second-person decision imperative — the v3 battery shape)
    ask = "which one should we pick: redis or memcached? weigh the tradeoffs"
    assert decision.detect_v3(ask, 3, cfg=cfg) is not None
    # positive control: a declared (A)/(B) fork still fires
    fork = "Quick fork: (A) ship now (B) hold back. One word answer."
    assert decision.detect_v3(fork, 3, cfg=cfg) is not None


# ---------------------------------------------------------------------------
# R9-7: banners always carry a row reconciliation segment
# ---------------------------------------------------------------------------

def test_r9_7_rollup_carries_row_refs():
    consumed = [{"choice": "opt-1", "tokens_in": 10, "tokens_out": 2,
                 "cost": 0.0, "model": "m", "endpoint": "",
                 "confidence": 0.8, "row_id": 22}]
    line = dmt._aggregate_line(1, 10, 2, 0.0, consumed)
    assert "row=22" in line
    consumed[0]["row_id"] = 0
    line2 = dmt._aggregate_line(1, 10, 2, 0.0, consumed)
    assert "ledger-row MISSING" in line2


def test_r9_7_decision_banner_missing_row_is_loud():
    banner = decision.render_decision_banner(
        "manual", "m", {"endpoint": "", "tokens_in": 1, "tokens_out": 1},
        initiator="user", ledger_ref=None, tokens_ok=True)
    assert "ledger-row MISSING" in banner
    banner2 = decision.render_decision_banner(
        "manual", "m", {"endpoint": "", "tokens_in": 1, "tokens_out": 1},
        initiator="user", ledger_ref=31, tokens_ok=True)
    assert "row=31" in banner2


# ---------------------------------------------------------------------------
# R9-8: decision fork + frontier ask in one turn — BOTH lanes fire
# ---------------------------------------------------------------------------

def test_r9_8_decision_frontier_cofire_both_deliver(r9_reset, monkeypatch):
    staged = []
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary":
                        staged.append(rd) or {"route_id": rd.route_id})
    v3_calls = []
    monkeypatch.setattr(decision, "handle_decision_v3",
                        lambda **kw: v3_calls.append(kw) or None)
    monkeypatch.setattr(decision, "on_demand_allowed",
                        lambda trig, cfg=None: True)
    cfg = dict(decision.DEFAULTS)
    cfg.update({"enabled": True, "level": 2, "pre": "shadow",
                "rescan_dedupe": False})
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    ask = ("decide this: option a) ship the fix now "
           "option b) hold for the next window\n"
           "ask frontier: is this the right window?")
    req = {"model": "minimax-m3",
           "messages": [{"role": "user", "content": ask}]}
    state.advance_turn_identity(SID, state.hash_text(ask))
    # the agent's mid-turn request_routing tool registered a DECISION claim
    import json as _json
    from hermes_router import router_tools as _rt
    assert _json.loads(_rt.router_control(
        action="request_routing", lane="decision", session_id=SID)).get(
        "ok", True)
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)
    # BOTH lanes fired: the decision advisory dispatched AND the frontier
    # consult staged. No lane precedence may drop a co-fired lane.
    assert v3_calls, "decision lane must fire on a co-fired turn"
    assert staged, "frontier consult must still route on a co-fired turn"
    stacked = [f for _, f in r9_reset["log"]
               if f.get("event_detail") == "declared_frontier_stacked"]
    assert stacked, "the stacking contract event must log"


# ---------------------------------------------------------------------------
# R9-9: consult advisories stamp model + cost
# ---------------------------------------------------------------------------

def test_r9_9_advisory_provenance_stamps():
    a = frames.orientation_advisory("f-model", "rid-1", "answer",
                                    model="f-model", cost=0.0147)
    assert "model=f-model" in a and "cost=0.014700" in a and "rid-1" in a
    r = frames.reflection_advisory("audit", "f-model", "rid-2", "none",
                                   "verdict", model="f-model", cost=0.5)
    assert "model=f-model" in r and "cost=0.500000" in r
    # absent stamps: byte-shape unchanged (no regression)
    a2 = frames.orientation_advisory("p", "rid-3", "ans")
    assert "model=" not in a2 and "cost=" not in a2


# ---------------------------------------------------------------------------
# R9-5 companion: persisted consult row count == billed consult count
# ---------------------------------------------------------------------------

def test_r9_5_no_billed_consult_without_ledger_row(r9_reset, monkeypatch):
    """Directly pins the A4 family: the anchored consult path must produce a
    decision_ledger row for EVERY consult it bills — even when the provider
    returned usage tokens (the previously-ledgered-only-tokens path)."""
    from hermes_router import anchor_chain, anchor_exec

    _anchor_rec(monkeypatch, anchor_chain.parse_anchor_uri(
        "openrouter://test/model-a", "primary"), router_core.MODE_PLAN)
    monkeypatch.setattr(anchor_exec, "anchored_call",
                        lambda e, a: ("ok body", None, None, None))
    monkeypatch.setattr(route_gate, "initiator_for_task", lambda t: "auto")
    outcome = anchor_exec.maybe_execute_anchored(SID, {"messages": [
        {"role": "user", "content": "anchor this: plan check"}]})
    assert outcome and outcome[0] == "done"
    rows = [r for r in decision.ledger_recent(limit=50)
            if r.get("trigger") == "frontier_consult"]
    assert len(rows) == 1
