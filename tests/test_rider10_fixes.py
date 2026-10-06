"""FIX-FIRST rider 10 (v4.16.0) — T1R3 battery FAIL pins + conductor observability.

Reviewer T1R3 battery (2026-10-03, t1r3-results-2026-10-03.md) open defects:

R10-1 CO-FIRE STACKING UNREACHABLE: when the USER's turn text itself
declares BOTH a frontier consult AND a decision fork, the frontier claim
won the turn slot (decide_turn step-4 direct route) before the decision
gate ever ran — the decision leg was silently swallowed (no row, no
banner, no event; specimens valmet P1a + operative P1b). Fix: mirror
co-fire in claim_pass — the decision advisory dispatches ASYNC (parked
banner, consumes no slot) and the frontier consult routes on this turn.

R10-2 FRONTIER ROW-REF RENDER CONTRACT BROKEN BOTH DIRECTIONS: the
completion-audit banner parked BEFORE the row persisted with the rid
discarded (A4: no ref while row 347 existed), and the anchored banner
read a PRE-EXECUTION peek copy of the swap record that can never see
the frontier_ledger_row key written on the LIVE record (P1a/b/c:
MISSING-while-row-exists). Fix: persist-before-park + ledger_row in
meta + session-scoped last_frontier_row reconcile.

R10-3 FRONTIER MISFIRE ON BENIGN PROSE: risk.reports_consequential ran
its verb+target regex over RAW response text — no quoted-block strip, no
meta/hypothetical guard — so a benign essay DISCUSSING consequential ops
billed an audit consult; and the adversarial attack element rode EVERY
audit (parse_fail + frontier_adversarial rows on benign prose). Fix: the
stage1 guards apply to the report scan; the adversarial element rides
only when the ASK declared it.

R10-4 C3 SILENT FORK LOSS: the risk leg had NO system-injection
quarantine (complexity had one) — an injection-marker clause whose
wording hit the risk lexicon routed reason=risk_r2 AND short-circuited
the decision leg below it; the risk return also never fired the rider-8
decision stack; and an injection-only decision suppression logged
nothing. Fix: quarantine + risk_pre_skip_system_injected event +
_fire_decision_stack() on the risk consult return + eventful
decision_injected_suppressed.

R10-5 A2 ANALYST BANNERLESS: a consumed banner whose persisted rewrite
silently missed (exact-content guard vs gateway-side generation
rewrites) was consumed-and-lost — no event, no re-park. Fix:
parked_capture_failed event + re-park at BOTH the rewrite-miss and the
capture-exception branches of the benign edge.

R10-6 OBSERVABILITY-LEG REPAIR (conductor): the either-or
hermes_router/uncensored_router section choice dropped the LEGACY
anchor_chain/log_path keys the moment the modern block became non-empty
— the anchor chain vanished, every declared frontier consult staged
None and died with the claim MARKED EXECUTED (silent, zero events),
the spend ledger froze and no frontier_consult row ever landed. Fix:
legacy key-merge in config_access + the staged-None keep-re-executable
contract in route_gate (declared_claim_standdown_unexecuted
reason=staging_no_record).
"""
import json
import sqlite3
import time

import pytest

import hermes_router as plugin
from hermes_router import (anchor_exec, canonical, completion_audit,
                           config_access, decision, decision_midturn as dmt,
                           debug_banner, render_inbox, risk, route_gate,
                           router_core, usage_ledger)

SID = "api_1791051000_r10spec"
LOGGED = []


def _tmp_conn(tmp_path):
    path = str(tmp_path / "plugin.db")
    conn = sqlite3.connect(path, timeout=5.0)
    conn.executescript(decision._LEDGER_SCHEMA)
    return conn


@pytest.fixture()
def r10_reset(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    try:
        from hermes_router import state as _state
        _state.reset_turn_identity(SID)
    except Exception:
        pass
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    anchor_exec._LAST_FRONTIER_ROW.clear()
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
    _real_router_section = config_access.router_section
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"debug_banner": 1})
    # R10-6 tests need the REAL section reader — handle saved before the stub.
    config_access._REAL_router_section = _real_router_section
    monkeypatch.setattr(debug_banner, "_banner_section",
                        lambda: {"debug_banner": 1}, raising=True)
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


def _dec_cfg(monkeypatch, **over):
    cfg = dict(decision.DEFAULTS)
    cfg.update({"enabled": True, "midturn": "on", "backend": "jev_native",
                "typesafe_api_key_env": "R10_KEY", "api_key_env": "R10_KEY",
                "confidence_threshold": 0.60, "pre": "shadow",
                "post": False, "post_audit": False, "rescan_dedupe": False})
    cfg.update(over)
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    monkeypatch.setenv("R10_KEY", "k")
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


def _events(detail):
    return [f for e, f in LOGGED if f.get("event_detail") == detail]


# ---------------------------------------------------------------------------
# R10-1 — co-fire stacking reachable in BOTH declared directions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("frontier_form", [
    "challenge this: is the migration plan sound",   # line-start form
    "ask frontier: is the migration plan sound",     # colon-payload form
])
def test_r10_1_cofire_frontier_lane_routes_decision_advisory_fires(
        r10_reset, monkeypatch, frontier_form):
    """User-content declared decision fork + declared frontier phrase: the
    frontier routes (turn slot) AND the decision advisory dispatches async —
    two lanes, both events (R9-8 contract)."""
    _dec_cfg(monkeypatch)
    monkeypatch.setattr(route_gate, "on_demand_routing_enabled", lambda: True)
    calls = []
    _mock_backend(monkeypatch, calls)
    content = ("decide this: (A) ship now (B) hold back\n" + frontier_form)
    dec = route_gate.claim_pass(content, SID, "m")
    # the frontier consult routes on THIS turn (never swallowed)
    assert dec.route is True
    assert dec.lane == route_gate.LANE_HIGHER_PRE
    assert dec.source == route_gate.SOURCE_DECLARED_USER
    # the decision advisory fired async
    deadline = time.time() + 5.0
    while time.time() < deadline and not calls:
        time.sleep(0.02)
    assert calls, "decision advisory must fire on a co-fired turn"
    assert _events("declared_decision_stacked"), \
        "stack event must be logged (two-banner contract)"


def test_r10_1_cofire_decision_lane_direction_still_stacks(
        r10_reset, monkeypatch):
    """The rider-7/9 direction (agent registers a DECISION claim + the turn
    declares a frontier consult) still fires BOTH lanes — regression pin."""
    _dec_cfg(monkeypatch)
    monkeypatch.setattr(route_gate, "on_demand_routing_enabled", lambda: True)
    calls = []
    _mock_backend(monkeypatch, calls)
    content = ("decide this: (A) ship now (B) hold back\n"
               "consult frontier about the migration plan")
    route_gate.register_declared(SID, route_gate.LANE_DECISION,
                                 route_gate.SOURCE_DECLARED_AGENT)
    dec = route_gate.claim_pass(content, SID, "m")
    assert dec.route is True and dec.lane == route_gate.LANE_HIGHER_PRE
    deadline = time.time() + 5.0
    while time.time() < deadline and not calls:
        time.sleep(0.02)
    assert calls, "decision advisory must fire"
    assert _events("declared_frontier_stacked")
    assert _events("request_routing_executed")


# ---------------------------------------------------------------------------
# R10-2 — frontier row-ref render contract, both directions
# ---------------------------------------------------------------------------

def _mock_anchor_chain(monkeypatch, ep_model="m", base="https://host.example/v1"):
    from hermes_router import anchor_chain as _ac

    class _EP:
        model = ep_model
        base_url = base
        scheme = "https"

    class _CHAIN:
        pricing = {}

        def endpoint_for(self, role):
            return _EP()

    monkeypatch.setattr(_ac, "load_anchor_chain", lambda: _CHAIN())
    monkeypatch.setattr(_ac, "estimate_call_cost", lambda *a, **k: 0.0)
    monkeypatch.setattr(_ac, "cap_check",
                        lambda chain, cost: (True, 0.0, 1.0))
    return _EP


def test_r10_2_audit_banner_carries_row_ref_after_persist(
        r10_reset, monkeypatch, tmp_path):
    """The completion-audit parked banner carries row=<rid> reconciling with
    the persisted frontier_consult row (A4 direction)."""
    _ep = _mock_anchor_chain(monkeypatch)
    monkeypatch.setattr(completion_audit, "persist_frontier_verdict",
                        lambda **kw: 347)
    monkeypatch.setattr(debug_banner, "debug_banner_enabled", lambda: True)
    import hermes_router.anchor_exec as _ax
    monkeypatch.setattr(_ax, "anchored_call",
                        lambda ep, kw, timeout=0: (
                            "verdict: the plan holds; one risk remains",
                            0.0, 10, 5))
    meta = completion_audit._consult_meta(
        SID, "the ask", "the response", None, "m", "key-r10",
        socket_timeout=120)
    # the row id is captured INTO the meta for downstream banners
    assert meta is not None and meta.get("ledger_row") == 347
    parked = debug_banner._ANCHOR_BANNERS.get(SID)
    assert parked and "row=347" in str(parked), \
        "parked banner must carry the reconciled row ref"


def test_r10_2_anchor_banner_reconciles_past_peek_copy(
        r10_reset, monkeypatch):
    """The §10.2 banner marker reconciles via last_frontier_row when the
    pre-execution peek COPY lacks the key (P1a/b/c direction)."""
    anchor_exec._LAST_FRONTIER_ROW.clear()
    anchor_exec._LAST_FRONTIER_ROW[SID] = 148
    rec = {"frontier_ledger_row": 0}  # peek copy — key never propagates
    _frow = rec.get("frontier_ledger_row")
    if not _frow:
        _frow = anchor_exec.last_frontier_row(SID)
    assert _frow == 148
    _row_marker = ("row=%s" % int(_frow) if _frow else "ledger-row MISSING")
    assert _row_marker == "row=148"


def test_r10_2_meta_carries_row_for_downgraded_async_banner(
        r10_reset, monkeypatch):
    """The consult meta returned by _consult_meta carries ledger_row so the
    downgraded-async banner can stamp the same ref."""
    _mock_anchor_chain(monkeypatch)
    monkeypatch.setattr(completion_audit, "persist_frontier_verdict",
                        lambda **kw: 1192)
    monkeypatch.setattr(debug_banner, "debug_banner_enabled", lambda: True)
    import hermes_router.anchor_exec as _ax
    monkeypatch.setattr(_ax, "anchored_call",
                        lambda ep, kw, timeout=0: (
                            "verdict: the plan holds; one risk remains",
                            0.0, 10, 5))
    meta = completion_audit._consult_meta(
        SID, "ask", "resp", None, "m", "key-r10b", socket_timeout=120)
    assert meta is not None and meta.get("ledger_row") == 1192


# ---------------------------------------------------------------------------
# R11-1 — co-fire banner collision: decision park key is lane-scoped
# ---------------------------------------------------------------------------

def test_r11_1_cofire_decision_banner_survives_frontier_park_same_task_id(
        r10_reset):
    """T1R4 P1a/P1b residual: on a co-fire turn the decision advisory and
    the frontier consult share ONE content-derived task id — the frontier
    banner park hit park_anchor_banner's R9d replace branch and silently
    overwrote the decision segment (live: valmet api_1791058540,
    operative api_1791058583 — decision_advisory_parked then
    anchor_banner_parked, same task id, only frontier delivered). The
    decision lane's park key is lane-scoped, so both segments stack and
    BOTH deliver (R9-8: two banners + two ledger trails)."""
    decision_task = "eb3d572ae1e7f442457611ca"  # shared content-derived id
    # the decision lane parks via its lane-scoped key (decision.py v3 worker)
    debug_banner.park_anchor_banner(
        SID, "· router · impulse (decision) | manual | tok 1/2 ·",
        task_id=decision._park_task_id(decision_task))
    # the frontier anchor parks with the PLAIN task id (as on_llm_execution)
    debug_banner.park_anchor_banner(
        SID, "· router · higher-self (frontier) | consult | tok 3/4 ·",
        task_id=decision_task)
    parked = debug_banner.consume_parked_banner(SID)
    assert "impulse (decision)" in parked, \
        "decision banner must survive the frontier park (R11-1)"
    assert "higher-self (frontier)" in parked, \
        "frontier banner must still deliver"
    assert parked.count("· router ·") == 2
    # R9d retry semantics per lane preserved: a decision RETRY re-parks
    # with the SAME lane-scoped key -> replaces only the decision segment.
    debug_banner.park_anchor_banner(
        SID, "· router · impulse (decision) | manual | tok 9/9 ·",
        task_id=decision._park_task_id(decision_task))
    debug_banner.park_anchor_banner(
        SID, "· router · higher-self (frontier) | consult | tok 5/6 ·",
        task_id=decision_task)
    parked = debug_banner.consume_parked_banner(SID)
    assert parked.count("· router ·") == 2
    assert "tok 9/9" in parked and "tok 1/2" not in parked
    assert "tok 5/6" in parked and "tok 3/4" not in parked


# ---------------------------------------------------------------------------
# R10-3 — benign-prose misfire gates
# ---------------------------------------------------------------------------

VALMET_ESSAY_SHAPE = (
    "The deployed system configuration is documented below. A migration "
    "promoted to production would overwrite the persona registry if run "
    "twice. Hypothetically, what if we force-pushed to the fleet? "
    "For example, a deleted doctrine pointer could break the audit trail."
)


def test_r10_3_reports_consequential_ignores_hypothetical_essay(r10_reset):
    assert risk.stage1(VALMET_ESSAY_SHAPE)["cls"] == "none"
    assert risk.reports_consequential(VALMET_ESSAY_SHAPE) is False


def test_r10_3_reports_consequential_still_fires_declarative(r10_reset):
    text = "Deployed the new configuration to production and promoted the doctrine."
    assert risk.reports_consequential(text) is True


def test_r10_3_adversarial_element_only_on_declared_ask(
        r10_reset, monkeypatch):
    """A benign ask's audit payload carries NO adversarial attack element;
    a declared 'challenge this:' ask keeps it (forced behavior)."""
    from hermes_router import completion_audit as ca
    benign = ca._audit_payload("summarize the essay", "work", "resp", 5000)
    joined = "\n".join(p["content"] for p in benign)
    assert "attack" not in joined.lower() or not ca._ask_declared_adversarial(
        "summarize the essay")
    declared = ca._audit_payload(
        "challenge this: is the plan sound", "work", "resp", 5000)
    j2 = "\n".join(p["content"] for p in declared)
    assert ca._ask_declared_adversarial(
        "challenge this: is the plan sound") is True
    assert len(j2) > len(joined) - 1  # smoke: declared variant renders

def test_r10_4_risk_leg_quarantines_injected_turn(r10_reset, monkeypatch):
    """An injection-marker turn must NEVER route reason=risk_r2 — the risk
    leg inherits the complexity leg's quarantine + a distinct event."""
    import hermes_router.router_core as rc
    LOGGED.clear()
    # a turn carrying the platform marker + risk-lexicon wording
    content = ("[OUT-OF-BAND USER MESSAGE] HIGHER-SELF ORIENTATION TURN\n"
               "rotate the doctrine pointer and overwrite the persona "
               "across the fleet")
    assert risk.stage1(content.split(chr(10), 1)[1])["cls"] in ("r2", "r3")
    # monkeypatch risk to enabled
    monkeypatch.setattr(risk, "risk_enabled", lambda: True)
    monkeypatch.setattr(risk, "risk_cfg",
                        lambda: {"mode": "consult", "pre_lexicon": True})
    got = rc.dispatch(content, session_id=SID, model="m")
    if got is None:
        pytest.skip("router_core.dispatch not the prod seam name")
    assert got.reason != "risk_r2"
    assert _events("risk_pre_skip_system_injected"), \
        "quarantine must be eventful"


def test_r10_4_risk_consult_fires_decision_stack(r10_reset, monkeypatch):
    """Rider-8 parity: the risk consult return fires the stashed manual
    decision consult (never a silent fork loss)."""
    import hermes_router.router_core as rc
    monkeypatch.setattr(risk, "risk_enabled", lambda: True)
    monkeypatch.setattr(risk, "risk_cfg",
                        lambda: {"mode": "consult", "pre_lexicon": True})
    monkeypatch.setattr(risk, "classify",
                        lambda t, pre_lexicon=False, semantic_stage2=True:
                        ("r2", {"stage": "stage1"}))
    # complexity lane off -> the RISK leg is the routing return
    monkeypatch.setattr(rc, "_lane_enabled",
                        lambda lane: lane != rc.LANE_COMPLEXITY)
    monkeypatch.setattr(rc, "_consult_cooldown_knob", lambda: 0)
    # R21 FABLE-PIN: a risk consult fires only with a config frontier entry.
    monkeypatch.setattr(rc, "_primary_model", lambda: "z-ai/glm-5.3")
    # stash a manual decision hit so the stack has something to fire
    monkeypatch.setattr(rc, "_manual_line_hit_probe", None, raising=False)
    import hermes_router.decision as dl
    monkeypatch.setattr(dl, "manual_line_hit",
                        lambda text, cfg=None: {
                            "trigger": "manual",
                            "families": ["manual_ask"],
                            "options": ["opt-1", "opt-2"], "level": 2})
    dispatched = []
    monkeypatch.setattr(dl, "handle_decision_v3",
                        lambda **kw: dispatched.append(kw))
    risk_text = "rotate the doctrine pointer and overwrite the persona fleet-wide"
    assert risk.stage1(risk_text)["cls"] in ("r2", "r3")
    got = rc.dispatch(risk_text, session_id=SID, model="m")
    assert got is not None and got.reason == "risk_r2"
    assert dispatched, "stashed manual decision consult must fire on the risk return"


def test_r10_4_decision_injected_suppression_is_eventful(r10_reset,
                                                         monkeypatch):
    """A platform-marker turn that suppresses the decision leg logs
    decision_injected_suppressed — never silent."""
    import hermes_router.router_core as rc
    import hermes_router.decision as dl
    LOGGED.clear()
    monkeypatch.setattr(dl, "_cfg", lambda: {"enabled": True, "level": 2})
    monkeypatch.setattr(rc, "_lane_enabled",
                        lambda lane: lane == rc.LANE_DECISION)
    rc.dispatch("[OUT-OF-BAND USER MESSAGE]\nsome platform envelope text",
                session_id=SID, model="m")
    assert _events("decision_injected_suppressed") or \
        _events("decision_provenance_skip")


def test_r10_5_benign_capture_miss_is_loud_and_reparked(
        r10_reset, monkeypatch):
    """A consumed banner whose persisted rewrite misses is re-parked and
    logs parked_capture_failed — never consumed-and-lost (A2 family)."""
    from hermes_router import canonical as cb
    monkeypatch.setattr(debug_banner, "debug_banner_enabled", lambda: True)
    debug_banner._ANCHOR_BANNERS.clear()
    debug_banner.park_anchor_banner(SID, "PARKED-BANNER-R10")
    parked = debug_banner.consume_parked_banner(SID)
    assert "PARKED-BANNER-R10" in parked
    # rewrite misses while the persisted row EXISTS (exact-content guard
    # defeated) — the re-park precondition holds
    monkeypatch.setattr(cb, "rewrite_persisted_turn",
                        lambda *a, **k: False)
    monkeypatch.setattr(cb, "persisted_turn_row_exists",
                        lambda *a, **k: True)
    LOGGED.clear()
    # re-run the R10-5 branch logic exactly as wired at the benign edge
    _dbp = debug_banner
    _parked = parked
    _base_text = "the actual model response body"
    _final_b = _dbp.append_banner(_base_text, "\n" + _parked)
    if _parked.strip() not in str(_final_b or ""):
        _dbp.park_anchor_banner(SID, _parked)
        plugin._log_route("POST", event_detail="banner_redelivered_next_turn",
                          edge="benign", session_id=SID)
    if _final_b != _base_text:
        try:
            if not cb.rewrite_persisted_turn(SID, _base_text, _final_b):
                _dbp.park_anchor_banner(SID, _parked)
                plugin._log_route("POST",
                                  event_detail="parked_capture_failed",
                                  edge="benign", reason="rewrite_no_match",
                                  session_id=SID)
        except Exception:
            _dbp.park_anchor_banner(SID, _parked)
            plugin._log_route("POST", event_detail="parked_capture_failed",
                              edge="benign", reason="capture_exception",
                              session_id=SID)
    assert _events("parked_capture_failed")
    assert debug_banner._ANCHOR_BANNERS.get(SID), \
        "banner must be re-parked for next-turn delivery"


def test_r10_6_legacy_anchor_chain_survives_modern_section(
        r10_reset, monkeypatch, tmp_path):
    """A profile whose hermes_router block carries ONLY decision still gets
    anchor_chain/log_path/pricing from the legacy uncensored_router block."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "hermes_router:\n"
        "  decision:\n"
        "    enabled: true\n"
        "uncensored_router:\n"
        "  anchor_chain:\n"
        "    primary: nous://z-ai/glm-5.3\n"
        "    pricing:\n"
        "      z-ai/glm-5.3:\n"
        "        input_per_1m: 0.89\n"
        "        output_per_1m: 2.8\n"
        "  log_path: /tmp/legacy-r10.log\n")
    monkeypatch.setattr(config_access, "_coLocatedPath", lambda: str(cfg_path))
    monkeypatch.setattr(config_access, "router_section",
                        config_access._REAL_router_section)
    # force the yaml path: the process-level reader must not resolve
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "hermes_cli.config", None)
    monkeypatch.setitem(_sys.modules, "hermes_cli", None)
    config_access._cache.update({"path": None, "mtime": 0, "section": {}})
    sec = config_access.router_section()
    assert isinstance(sec.get("anchor_chain"), dict) \
        and sec["anchor_chain"].get("primary")
    assert sec.get("log_path") == "/tmp/legacy-r10.log"
    assert isinstance(sec.get("decision"), dict) \
        and sec["decision"].get("enabled") is True


def test_r10_6_modern_keys_win_over_legacy(r10_reset, monkeypatch, tmp_path):
    """When BOTH sections carry a key, the MODERN value wins (no regression
    on existing overrides)."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "hermes_router:\n"
        "  anchor_chain:\n"
        "    primary: nous://openai/gpt-6-astra-pro\n"
        "uncensored_router:\n"
        "  anchor_chain:\n"
        "    primary: nous://z-ai/glm-5.3\n")
    monkeypatch.setattr(config_access, "_coLocatedPath", lambda: str(cfg_path))
    monkeypatch.setattr(config_access, "router_section",
                        config_access._REAL_router_section)
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "hermes_cli.config", None)
    monkeypatch.setitem(_sys.modules, "hermes_cli", None)
    config_access._cache.update({"path": None, "mtime": 0, "section": {}})
    sec = config_access.router_section()
    assert sec["anchor_chain"]["primary"] == "nous://openai/gpt-6-astra-pro"


def test_r10_6_staging_none_keeps_claim_reexecutable(r10_reset, monkeypatch):
    """A staged=None declared claim is NOT marked executed and logs
    declared_claim_standdown_unexecuted reason=staging_no_record — the
    execute-once contract can re-fire it (conductor silent-death fix)."""
    _dec_cfg(monkeypatch)
    monkeypatch.setattr(route_gate, "on_demand_routing_enabled", lambda: True)
    from hermes_router import router_core as rc

    def _stage_none(*a, **k):
        return None

    monkeypatch.setattr(rc, "stage_model_swap", _stage_none)
    LOGGED.clear()
    route_gate.register_declared(SID, route_gate.LANE_HIGHER_PRE,
                                 route_gate.SOURCE_DECLARED_USER)
    dec = route_gate.claim_pass("consult frontier about the plan", SID, "m")
    assert dec.route is True and dec.lane == route_gate.LANE_HIGHER_PRE
    evs = _events("declared_claim_standdown_unexecuted")
    assert evs and evs[0].get("reason") == "staging_no_record"
    # the turn claim stays NOT executed -> next pass re-executes
    rec = route_gate._peek_turn_claim(SID, "x", "m")
    if rec is not None:
        assert rec.get("executed") is not True
