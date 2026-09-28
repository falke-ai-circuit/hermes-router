"""R19 — Decision Lane v3 (frozen spec 2026-09-27) test battery.

Covers: detection FPs + negative non-decision turns, skip bypass, envelope
v2 shape + anti-echo slice, typed verdict validation fail-open, backend
adapters (jev + nous, mocked HTTP/aux), caps + breaker, ledger rows
(§5.7 schema + bounded eviction + POST actual/outcome), banner park/consume
(one banner, latest-wins, §7 format), lane precedence, midturn declared
claim, POST leg gating (dark no-op).
"""
import json
import sqlite3
import time

import pytest

import hermes_router as plugin
from hermes_router import config_access, debug_banner, decision, route_gate, router_core

SID = "s-r19v3"
TASK_ID = "t-r19v3"

LOGGED = []


def _v3_cfg(**over):
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    cfg.update(over)
    return cfg


ASK = ("which one should we pick: redis or memcached? weigh the tradeoffs")


@pytest.fixture()
def _reset(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    LOGGED.clear()
    debug_banner._ANCHOR_BANNERS.clear()
    decision.reset_limits()
    decision.reset_v3_limits()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    # default config: dark (enabled false); ledger goes to a tmp db
    monkeypatch.setattr(decision, "_cfg", lambda: dict(decision.DEFAULTS))
    monkeypatch.setattr(router_core, "_decision_cfg",
                        lambda: dict(decision.DEFAULTS))
    monkeypatch.setattr(router_core, "consult_cooldown_turns",
                        lambda: 0, raising=True)
    # zero-network guard: pricing lookup must never egress in tests
    monkeypatch.setattr("hermes_router.usage_ledger.estimate_cost",
                        lambda *a, **k: 0.0, raising=True)
    monkeypatch.setattr(decision, "_ledger_connect",
                        lambda db_path="": _tmp_conn(tmp_path))
    yield tmp_path
    decision.reset_limits()
    decision.reset_v3_limits()
    debug_banner._ANCHOR_BANNERS.clear()


def _tmp_conn(tmp_path):
    import os

    path = str(tmp_path / "plugin.db")
    os.makedirs(tmp_path, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.executescript(decision._LEDGER_SCHEMA)
    return conn


def _enable(monkeypatch, **over):
    cfg = _v3_cfg(**over)
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    monkeypatch.setattr(router_core, "_decision_cfg", lambda: cfg)
    return cfg


def _logged(event):
    return [f for e, f in LOGGED if e == event]


def _nous_body(choice, confidence, alternatives):
    return json.dumps({
        "choices": [{"message": {"content": json.dumps({
            "choice": choice, "confidence": confidence,
            "alternatives": alternatives})}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    })


def _mock_nous(monkeypatch, choice="opt-1", confidence=0.9,
               alternatives=None):
    monkeypatch.setattr(
        "hermes_router.semantic_classifier._hermes_aux_call",
        lambda payload, timeout: _nous_body(choice, confidence,
                                            alternatives or []))


def _wait_parked(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if debug_banner._ANCHOR_BANNERS.get(SID):
            return True
        time.sleep(0.02)
    return False


# --------------------------------------------------------------------------
# 1. Detection: FPs, negatives, bypass, manual
# --------------------------------------------------------------------------

def test_detect_v3_structural_options_required(_reset):
    # user-locked §2: STRUCTURAL detection — the ask itself must contain
    # the enumerated fork. Options present -> fires (family words optional).
    hit = decision.detect_v3("which one should we pick: redis or memcached?")
    assert hit is not None and hit["trigger"] == "pre"
    assert hit["options"] == ["redis", "memcached"]
    # decision vocabulary WITHOUT enumerated options -> structural default-deny
    assert decision.detect_v3(
        "which approach should we take — weigh the tradeoffs") is None


def test_detect_v3_negative_non_decision_turns(_reset):
    # prose that mentions decision vocabulary but is NOT a decision-shaped ask
    for text in (
        "The decision record table was committed yesterday.",
        "explain how the router detects options in a turn",
        "what are the tradeoffs listed in the spec document",
        "decide this sentence's grammar — is it correct English?",  # meta
    ):
        # meta 'decide this' line IS manual-shaped; drop it from negatives
        if text.startswith("decide this"):
            continue
        assert decision.detect_v3(text) is None, text


def test_detect_v3_meta_decide_this_inert(_reset):
    # quoted/meta context stays inert: mid-line mention never manual-fires
    assert decision.detect_v3(
        "the manual says: use decide this when you mean it") is None


def test_detect_v3_skip_bypass(_reset):
    hit = decision.detect_v3("which option? weigh tradeoffs\nskip decision")
    assert hit is not None and hit["trigger"] == "skip"


def test_dispatch_skip_decision_bypass(_reset, monkeypatch):
    _enable(monkeypatch)
    d = router_core.dispatch("which approach? weigh the tradeoffs\n"
                             "skip decision", session_id=SID, model="m")
    assert d.lane != router_core.LANE_DECISION
    assert d.reason == "decision_skipped"


def test_dispatch_routes_decision_when_enabled(_reset, monkeypatch):
    _enable(monkeypatch)
    # structural §2: the ask carries the enumerated fork
    d = router_core.dispatch(ASK, session_id=SID, model="m")
    assert d.lane == router_core.LANE_DECISION


def test_lane_precedence_complexity_over_decision(_reset, monkeypatch):
    # complexity > heuristic PRE (§5.3): when the complexity lane claims the
    # turn, the decision heuristic never fires (dispatch order).
    _enable(monkeypatch)
    monkeypatch.setattr("hermes_router.complexity.classify",
                        lambda text, level: (True, {"stage": "stage1"}))
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"pre_mode": "route"})
    d = router_core.dispatch(ASK, session_id=SID, model="m")
    assert d.lane == router_core.LANE_COMPLEXITY


def test_dispatch_manual_on_demand_gated(_reset, monkeypatch):
    _enable(monkeypatch, on_demand={"manual": False, "midturn": True})
    d = router_core.dispatch("decide this: which one?", session_id=SID,
                             model="m")
    assert d.lane != router_core.LANE_DECISION
    _enable(monkeypatch)  # manual on
    d = router_core.dispatch("decide this: which one?", session_id=SID,
                             model="m")
    assert d.lane == router_core.LANE_DECISION


# --------------------------------------------------------------------------
# 2. Envelope v2 + verdict validation
# --------------------------------------------------------------------------

def test_extract_options_from_ask(_reset):
    opts = decision.extract_options(
        "which one?\n- a) postgres\n- b) sqlite\n2) duckdb")
    assert len(opts) == 3
    assert opts[0] == "postgres"
    ids = decision.option_ids(opts)
    assert ids == ["opt-1", "opt-2", "opt-3"]


def test_extract_options_prose_fallback(_reset):
    opts = decision.extract_options("should we use redis or memcached here?")
    assert len(opts) == 2


def test_envelope_v2_shape(_reset, monkeypatch):
    env = decision.build_envelope(SID, "redis or memcached?", [], "pre")
    assert env["schema"] == decision.ENVELOPE_SCHEMA
    assert env["agent_frame"]
    assert env["scope"]["risk_class"] == "normal"
    assert [o["id"] for o in env["options"]] == ["opt-1", "opt-2"]
    assert env["question"]["type"] == "choose_one_with_confidence"
    assert "slice" in env


def test_envelope_high_stakes_advice_only(_reset):
    env = decision.build_envelope(
        SID, "delete the production database or archive it?", [], "pre")
    assert env["scope"]["risk_class"] == "high"
    assert env["scope"]["advice_only"] is True


def test_envelope_no_options_empty(_reset):
    assert decision.build_envelope(SID, "hello there friend", [], "pre") == {}


def test_verdict_valid(_reset):
    env = decision.build_envelope(SID, "redis or memcached?", [], "pre")
    v, reason = decision.validate_verdict(
        json.dumps({"choice": "opt-1", "confidence": 0.8,
                    "alternatives": ["opt-2"]}), env)
    assert reason == "ok"
    assert v["choice"] == "opt-1" and v["confidence"] == 0.8


def test_verdict_fail_open_unknown_choice(_reset):
    env = decision.build_envelope(SID, "redis or memcached?", [], "pre")
    v, reason = decision.validate_verdict(
        json.dumps({"choice": "opt-9", "confidence": 0.8}), env)
    assert v is None and reason == decision.REASON_MALFORMED


def test_verdict_fail_open_non_id_choice(_reset):
    env = decision.build_envelope(SID, "redis or memcached?", [], "pre")
    v, _ = decision.validate_verdict(
        json.dumps({"choice": "redis", "confidence": 0.8}), env)
    assert v is None  # enumerated option ids ONLY — labels rejected


def test_verdict_fail_open_unknown_keys(_reset):
    env = decision.build_envelope(SID, "redis or memcached?", [], "pre")
    v, reason = decision.validate_verdict(
        json.dumps({"choice": "opt-1", "confidence": 0.8, "note": "x"}), env)
    assert v is None and reason == decision.REASON_MALFORMED


def test_verdict_fail_open_bad_confidence(_reset):
    env = decision.build_envelope(SID, "redis or memcached?", [], "pre")
    for bad in ("high", True, None):
        v, _ = decision.validate_verdict(
            json.dumps({"choice": "opt-1", "confidence": bad}), env)
        assert v is None, bad


def test_verdict_fail_open_bad_alternatives(_reset):
    env = decision.build_envelope(SID, "redis or memcached?", [], "pre")
    v, _ = decision.validate_verdict(
        json.dumps({"choice": "opt-1", "confidence": 0.5,
                    "alternatives": ["opt-1"]}), env)
    assert v is None
    v, _ = decision.validate_verdict(
        json.dumps({"choice": "opt-1", "confidence": 0.5,
                    "alternatives": ["nope"]}), env)
    assert v is None


def test_verdict_fail_open_garbage(_reset):
    env = decision.build_envelope(SID, "redis or memcached?", [], "pre")
    v, reason = decision.validate_verdict("not json at all", env)
    assert v is None and reason == decision.REASON_PARSE_FAIL


# --------------------------------------------------------------------------
# 3. Backends (mocked) — shared paths, adapter flip
# --------------------------------------------------------------------------

def test_backend_nous_pipeline(_reset, monkeypatch):
    _enable(monkeypatch)  # backend nous default
    _mock_nous(monkeypatch)
    decision.handle_decision_v3(session_id=SID, task_id=TASK_ID,
                                task_text=ASK,
                                log_route=lambda e, **f:
                                LOGGED.append((e, dict(f))))
    assert _wait_parked()
    parked = debug_banner.consume_parked_banner(SID)
    assert decision.PROVENANCE_TAG in parked
    assert "choice=opt-1" in parked
    # §7 banner: one provenance line, decision lane, initiator=user
    assert "· router · reflex (decision) |" in parked
    assert "initiator=user" in parked
    assert parked.count("· router ·") == 1
    assert _logged("decision_advisory_parked")
    rows = decision.ledger_recent()
    assert len(rows) == 1
    row = rows[0]
    assert row["choice"] == "opt-1"
    assert row["outcome"] == "pending"
    assert row["trigger"] == "pre"
    assert row["model"] == decision.DEFAULTS["model"]
    assert row["options_hash"] and row["envelope_hash"]


def test_backend_jev_pipeline(_reset, monkeypatch):
    _enable(monkeypatch, backend="jev", jev_model="typesafe/jev-router@pinned",
            api_key_env="R19_TEST_KEY")
    monkeypatch.setenv("R19_TEST_KEY", "k-test")

    def _fake_post(url, headers, payload, timeout):
        assert url == decision.DEFAULTS["openrouter_endpoint"]
        assert headers["Authorization"] == "Bearer k-test"
        assert payload["model"] == "typesafe/jev-router@pinned"
        return {"choices": [{"message": {"content": json.dumps(
            {"choice": "opt-2", "confidence": 0.7,
             "alternatives": ["opt-1"]})}}],
            "usage": {"prompt_tokens": 33, "completion_tokens": 9}}

    monkeypatch.setattr(decision, "_http_post_json", _fake_post)
    decision.handle_decision_v3(session_id=SID, task_id=TASK_ID,
                                task_text=ASK,
                                log_route=lambda e, **f:
                                LOGGED.append((e, dict(f))))
    assert _wait_parked()
    parked = debug_banner.consume_parked_banner(SID)
    assert "choice=opt-2" in parked
    assert "typesafe/jev-router@pinned" in parked
    row = decision.ledger_recent()[0]
    assert row["model_version"] == "typesafe/jev-router@pinned"


def test_backend_jev_missing_key_fail_open(_reset, monkeypatch):
    _enable(monkeypatch, backend="jev", api_key_env="R19_TEST_KEY_MISSING")
    monkeypatch.delenv("R19_TEST_KEY_MISSING", raising=False)
    decision.handle_decision_v3(session_id=SID, task_id=TASK_ID,
                                task_text=ASK,
                                log_route=lambda e, **f:
                                LOGGED.append((e, dict(f))))
    deadline = time.time() + 10
    rows = []
    while time.time() < deadline and not rows:
        rows = [r for r in decision.ledger_recent()
                if r.get("fail_open_reason")]
        time.sleep(0.02)
    sup = _logged("decision_suppressed")
    assert sup and sup[-1]["reason"] == decision.REASON_BACKEND_ERROR
    assert debug_banner.consume_parked_banner(SID) == ""
    row = rows[-1]
    assert row["fail_open_reason"] == decision.REASON_BACKEND_ERROR


def test_malformed_verdict_reason_coded(_reset, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        "hermes_router.semantic_classifier._hermes_aux_call",
        lambda payload, timeout: json.dumps({"choices": [{"message": {
            "content": json.dumps({"choice": "opt-9",
                                   "confidence": 0.9})}}]}))
    decision.handle_decision_v3(session_id=SID, task_id=TASK_ID,
                                task_text=ASK,
                                log_route=lambda e, **f:
                                LOGGED.append((e, dict(f))))
    deadline = time.time() + 3
    while time.time() < deadline and not _logged("decision_suppressed"):
        time.sleep(0.02)
    sup = _logged("decision_suppressed")
    assert sup and sup[-1]["reason"] == decision.REASON_MALFORMED
    assert decision.get_counter("malformed") >= 1
    row = decision.ledger_recent()[0]
    assert row["fail_open_reason"] == decision.REASON_MALFORMED
    assert not row["choice"]


# --------------------------------------------------------------------------
# 4. Caps + breaker (§5.6)
# --------------------------------------------------------------------------

def test_caps_per_run(_reset, monkeypatch):
    cfg = _enable(monkeypatch, caps={"per_run": 1, "per_session": 100,
                                     "global_daily": 1000})
    _mock_nous(monkeypatch)
    decision.handle_decision_v3(session_id=SID, task_id=TASK_ID,
                                task_text=ASK, cfg=cfg,
                                log_route=lambda e, **f:
                                LOGGED.append((e, dict(f))))
    decision.handle_decision_v3(session_id=SID, task_id=TASK_ID,
                                task_text=ASK, cfg=cfg,
                                log_route=lambda e, **f:
                                LOGGED.append((e, dict(f))))
    deadline = time.time() + 3
    while time.time() < deadline and len(_logged("decision_suppressed")) < 1:
        time.sleep(0.02)
    sup = _logged("decision_suppressed")
    assert any(f["reason"].endswith(":per_run") for f in sup)


def test_caps_global_daily(_reset, monkeypatch):
    cfg = _enable(monkeypatch, caps={"per_run": 20, "per_session": 100,
                                     "global_daily": 1})
    _mock_nous(monkeypatch)
    for tid in ("t1", "t2"):
        decision.handle_decision_v3(session_id=SID, task_id=tid,
                                    task_text=ASK,
                                    cfg=cfg, log_route=lambda e, **f:
                                    LOGGED.append((e, dict(f))))
    deadline = time.time() + 3
    while time.time() < deadline and len(_logged("decision_suppressed")) < 1:
        time.sleep(0.02)
    assert any(f["reason"].endswith(":global_daily")
               for f in _logged("decision_suppressed"))


def test_breaker_opens_after_failures(_reset, monkeypatch):
    cfg = _enable(monkeypatch, breaker_fails=1)
    monkeypatch.setattr(
        "hermes_router.semantic_classifier._hermes_aux_call",
        lambda payload, timeout: None)  # transport timeout
    for _ in range(2):
        decision.handle_decision_v3(session_id=SID, task_id=TASK_ID,
                                    task_text=ASK,
                                    cfg=cfg, log_route=lambda e, **f:
                                    LOGGED.append((e, dict(f))))
        deadline = time.time() + 3
        while time.time() < deadline and not _logged("decision_suppressed"):
            time.sleep(0.02)
    reasons = [f["reason"] for f in _logged("decision_suppressed")]
    assert decision.REASON_TIMEOUT in reasons
    assert decision.REASON_BREAKER_OPEN in reasons  # circuit breaker opened
    rows = decision.ledger_recent()
    assert any(r["fail_open_reason"] == decision.REASON_BREAKER_OPEN
               for r in rows)


# --------------------------------------------------------------------------
# 5. Ledger (§5.7) + POST leg (§5.8)
# --------------------------------------------------------------------------

def test_ledger_row_cap_eviction(_reset, monkeypatch):
    for i in range(30):
        decision.ledger_write({"session_id": SID, "task_id": "t%d" % i,
                               "trigger": "pre", "_cap": 20})
    rows = decision.ledger_recent(limit=100)
    assert len(rows) == 20
    assert max(r["id"] for r in rows) == 30  # newest kept (append-only trim)


def test_post_audit_records_actual(_reset, monkeypatch):
    _enable(monkeypatch)
    _mock_nous(monkeypatch, choice="opt-1", confidence=0.9)
    decision.handle_decision_v3(session_id=SID, task_id=TASK_ID,
                                task_text=ASK,
                                log_route=lambda e, **f:
                                LOGGED.append((e, dict(f))))
    assert _wait_parked()
    rid = decision.ledger_recent()[0]["id"]
    decision.post_audit_v3(SID, "went with opt-2 in the end",
                           log_route=lambda e, **f:
                           LOGGED.append((e, dict(f))))
    row = [r for r in decision.ledger_recent() if r["id"] == rid][0]
    assert row["actual_choice"] == "opt-2"
    assert row["outcome"] == "recorded"
    assert decision.get_counter("wrong_and_confident") >= 1  # 0.9 conf, wrong


def test_post_audit_no_signal_stays_pending(_reset):
    decision.ledger_write({"session_id": SID, "task_id": TASK_ID,
                           "trigger": "pre", "choice": "opt-1",
                           "confidence": 0.9})
    decision.post_audit_v3(SID, "the work continues, nothing chosen")
    row = decision.ledger_recent()[0]
    assert row["outcome"] == "pending"  # never guessed
    assert row["actual_choice"] == ""


def test_post_audit_dark_noop(_reset):
    # enabled false (dark default): the POST leg is a total no-op
    decision.ledger_write({"session_id": SID, "task_id": TASK_ID,
                           "trigger": "pre", "choice": "opt-1",
                           "confidence": 0.9})
    decision.post_audit_v3(SID, "went with opt-2")
    row = decision.ledger_recent()[0]
    assert row["outcome"] == "pending"


def test_post_audit_low_conf_not_wrong_and_confident(_reset, monkeypatch):
    _enable(monkeypatch)
    decision.ledger_write({"session_id": SID, "task_id": TASK_ID,
                           "trigger": "pre", "choice": "opt-1",
                           "confidence": 0.30})
    n_before = decision.get_counter("wrong_and_confident")
    decision.post_audit_v3(SID, "went with opt-2")
    assert decision.get_counter("wrong_and_confident") == n_before


def test_extract_actual_choice_forms(_reset):
    assert decision.extract_actual_choice("picked opt-2") == "opt-2"
    assert decision.extract_actual_choice("went with option 3") == "opt-3"
    assert decision.extract_actual_choice("choose option b") == "opt-2"
    assert decision.extract_actual_choice("no choice named") == ""


# --------------------------------------------------------------------------
# 6. Banner mechanics (§7)
# --------------------------------------------------------------------------

def test_banner_format_decision_lane(_reset):
    b = decision.render_decision_banner(
        "pre", "z-ai/glm-5.3-flash",
        {"endpoint": "", "tokens_in": 10, "tokens_out": 5, "latency_s": 1.2},
        initiator="user")
    assert b.startswith("· router · reflex (decision) | pre | z-ai/glm-5.3-flash")
    assert "tok 10/5" in b
    assert "initiator=user" in b


def test_banner_latest_wins_one_per_message(_reset, monkeypatch):
    _enable(monkeypatch)
    for choice in ("opt-1", "opt-2"):
        _mock_nous(monkeypatch, choice=choice)
        decision.handle_decision_v3(session_id=SID, task_id=TASK_ID,
                                    task_text=ASK,
                                    log_route=lambda e, **f:
                                    LOGGED.append((e, dict(f))))
        # wait until THIS call's advisory replaced the parked banner
        deadline = time.time() + 5
        while time.time() < deadline:
            if ("choice=%s" % choice) in debug_banner._ANCHOR_BANNERS.get(SID, ""):
                break
            time.sleep(0.02)
    out = debug_banner.consume_parked_banner(SID)
    assert out.count("· router ·") == 1
    assert "choice=opt-2" in out  # latest wins
    assert debug_banner.consume_parked_banner(SID) == ""


# --------------------------------------------------------------------------
# 7. On-demand midturn (declared-claim machinery) + dark default
# --------------------------------------------------------------------------

def test_midturn_declared_claim_runs_lane(_reset, monkeypatch):
    _enable(monkeypatch)
    _mock_nous(monkeypatch)
    route_gate.register_declared(SID, route_gate.LANE_DECISION,
                                 route_gate.SOURCE_DECLARED_AGENT)
    d = route_gate.claim_pass("decide this: redis or memcached?", SID, "m")
    assert d.route is False  # advisory only — turn proceeds
    assert _wait_parked()
    parked = debug_banner.consume_parked_banner(SID)
    assert "choice=opt-1" in parked
    assert "initiator=agent" in parked


def test_midturn_declared_claim_dark_inert(_reset):
    # dark default: registered claim does NOT invoke the lane
    route_gate.register_declared(SID, route_gate.LANE_DECISION,
                                 route_gate.SOURCE_DECLARED_AGENT)
    d = route_gate.claim_pass("decide this: redis or memcached?", SID, "m")
    assert d.route is False
    assert debug_banner.consume_parked_banner(SID) == ""


def test_dark_default_no_route(_reset):
    assert router_core._lane_enabled(router_core.LANE_DECISION) is False
    d = router_core.dispatch("which approach? weigh the tradeoffs",
                             session_id=SID, model="m")
    assert d.lane != router_core.LANE_DECISION


# --------------------------------------------------------------------------
# 8. POST fork-scan leg (user-locked §2 addendum)
# --------------------------------------------------------------------------

POST_TURN = ("Two paths from here:\n"
             "- a) migrate the store now, because the rollback script is "
             "rehearsed and the window is free tonight\n"
             "- b) freeze writes first, migrate after the batch, since "
             "each step stays independently reversible at a small cost\n")


def test_post_fork_scan_options_in_turn_appends_verdict(_reset, monkeypatch):
    _enable(monkeypatch)
    _mock_nous(monkeypatch, choice="opt-1", confidence=0.8)
    decision.post_fork_scan(SID, POST_TURN, log_route=lambda e, **f:
                            LOGGED.append((e, dict(f))))
    assert _wait_parked()
    parked = debug_banner.consume_parked_banner(SID)
    assert "choice=opt-1" in parked            # verdict APPENDED as advisory
    assert "· router · reflex (decision) |" in parked   # banner-marked
    assert "initiator=model" in parked         # steering, not user ask
    row = decision.ledger_recent()[0]
    assert row["trigger_kind"] == "post_fork_scan"


def test_post_fork_scan_no_options_no_call(_reset, monkeypatch):
    _enable(monkeypatch)
    called = []
    monkeypatch.setattr(
        "hermes_router.semantic_classifier._hermes_aux_call",
        lambda payload, timeout: called.append(1))
    decision.post_fork_scan(SID, "the migration completed without incident",
                            log_route=lambda e, **f:
                            LOGGED.append((e, dict(f))))
    assert called == []          # structural default-deny: NO backend call
    assert debug_banner.consume_parked_banner(SID) == ""
    ev = _logged("decision_post_fork_scan")
    # R19.12 FIX 2: the POST gate fires BEFORE extraction on ordinary
    # turns — the reason is now post_gate_insufficient_structure.
    assert ev and ev[-1]["outcome"] == "post_gate_insufficient_structure"


def test_post_fork_scan_dark_noop(_reset):
    decision.post_fork_scan(SID, POST_TURN)
    assert debug_banner.consume_parked_banner(SID) == ""


def test_post_scan_misfire_high_conf_counts_against_breaker(_reset, monkeypatch):
    # §7(e): high-conf answers on POST scans count against the breaker
    # even when the output is well-formed.
    cfg = _enable(monkeypatch, breaker_fails=2)
    _mock_nous(monkeypatch, choice="opt-1", confidence=0.97)
    for _ in range(2):
        decision.post_fork_scan(SID, POST_TURN, log_route=lambda e, **f:
                                LOGGED.append((e, dict(f))))
    deadline = time.time() + 5
    while time.time() < deadline and len(_logged("decision_misfire")) < 2:
        time.sleep(0.02)
    assert decision.get_counter("misfire") >= 2
    # the second misfire's breaker penalty lands inside its worker
    deadline = time.time() + 5
    while time.time() < deadline and not decision.breaker_state()["open"]:
        time.sleep(0.02)
    assert decision.breaker_state()["open"]
    # third POST scan hits the opened breaker
    decision.post_fork_scan(SID, POST_TURN, log_route=lambda e, **f:
                            LOGGED.append((e, dict(f))))
    deadline = time.time() + 5
    while time.time() < deadline:
        sup = [f for f in _logged("decision_suppressed")
               if f["reason"] == decision.REASON_BREAKER_OPEN]
        if sup:
            break
        time.sleep(0.02)
    assert [f for f in _logged("decision_suppressed")
            if f["reason"] == decision.REASON_BREAKER_OPEN]


def test_post_scan_low_conf_not_misfire(_reset, monkeypatch):
    _enable(monkeypatch)
    n = decision.get_counter("misfire")
    _mock_nous(monkeypatch, choice="opt-1", confidence=0.5)
    decision.post_fork_scan(SID, POST_TURN)
    assert _wait_parked()
    assert decision.get_counter("misfire") == n


def test_stand_down_escape_no_advisory(_reset, monkeypatch):
    # §7(d): lane-injected escape — backend stands down, nothing appended
    _enable(monkeypatch)
    _mock_nous(monkeypatch, choice="stand_down", confidence=0.0)
    decision.handle_decision_v3(session_id=SID, task_id=TASK_ID,
                                task_text=ASK,
                                log_route=lambda e, **f:
                                LOGGED.append((e, dict(f))))
    deadline = time.time() + 3
    while time.time() < deadline and not _logged("decision_stand_down"):
        time.sleep(0.02)
    assert _logged("decision_stand_down")
    assert debug_banner.consume_parked_banner(SID) == ""  # no advisory appended
    deadline = time.time() + 3
    rows = []
    while time.time() < deadline and not rows:
        rows = [r for r in decision.ledger_recent()
                if r["outcome"] == "stand_down"]
        time.sleep(0.02)
    row = rows[0]
    assert row["outcome"] == "stand_down"
    assert not row["choice"] or row["choice"] == "stand_down"


def test_validate_verdict_accepts_stand_down(_reset):
    env = decision.build_envelope(SID, ASK, [], "pre")
    v, reason = decision.validate_verdict(
        json.dumps({"choice": "stand_down", "confidence": 0.0,
                    "alternatives": []}), env)
    assert reason == "ok" and v["choice"] == "stand_down"
    # stand_down with alternatives is malformed
    v, reason = decision.validate_verdict(
        json.dumps({"choice": "stand_down", "confidence": 0.0,
                    "alternatives": ["opt-1"]}), env)
    assert v is None


def test_prompt_carries_stand_down_escape(_reset):
    env = decision.build_envelope(SID, ASK, [], "pre")
    prompt = decision.render_prompt(env)
    assert "stand_down" in prompt


def test_ledger_trigger_kinds(_reset, monkeypatch):
    _enable(monkeypatch)
    _mock_nous(monkeypatch)
    decision.handle_decision_v3(session_id=SID, task_id=TASK_ID,
                                task_text=ASK, log_route=lambda e, **f:
                                LOGGED.append((e, dict(f))))
    assert _wait_parked()
    kinds = {r["trigger_kind"] for r in decision.ledger_recent()}
    assert kinds == {"pre_fork"}
    decision.ledger_write({"session_id": SID, "task_id": "tm",
                           "trigger": "manual"})
    decision.ledger_write({"session_id": SID, "task_id": "tp",
                           "trigger": "post"})
    kinds = {r["task_id"]: r["trigger_kind"]
             for r in decision.ledger_recent(limit=10)}
    assert kinds["tm"] == "on_demand"
    assert kinds["tp"] == "post_fork_scan"


# --------------------------------------------------------------------------
# 9. §3 addendum 2: causal frames on envelope options + N+1 carryover
# --------------------------------------------------------------------------

FRAMED_ASK = ("which migration path?\n"
              "- a) migrate now — saves 3 hours but irreversible on prod\n"
              "- b) freeze first, reversible rollback, costs $50")


def test_envelope_options_carry_causal_frames(_reset):
    env = decision.build_envelope(SID, FRAMED_ASK, [], "pre")
    opts = env["options"]
    assert len(opts) == 2
    for o in opts:
        # every emitted option carries the FULL frame shape (user-locked)
        assert set(o.keys()) == {"id", "label", "cause_effect", "cost",
                                 "priors", "risk"}
    a, b = opts
    assert a["cause_effect"] == "saves 3 hours but irreversible on prod"
    assert a["cost"] == "3 hours"
    assert a["risk"] == "irreversible"
    assert b["risk"] == "reversible"
    assert b["cost"] == "$50"
    # prompt renders the frames
    prompt = decision.render_prompt(env)
    assert "leads to: saves 3 hours" in prompt
    assert "risk: irreversible" in prompt


def test_envelope_frames_null_never_fabricated(_reset):
    # bare fork: nothing stated in the ask, no ledger rows -> all frame
    # fields null (never invented)
    env = decision.build_envelope(SID, "redis or memcached?", [], "pre")
    for o in env["options"]:
        assert o["cause_effect"] is None
        assert o["cost"] is None
        assert o["priors"] is None
        assert o["risk"] is None


def test_envelope_priors_from_ledger_same_fork_class(_reset):
    decision.ledger_write({"session_id": SID, "task_id": "t0",
                           "trigger": "post", "fork_class": "generic",
                           "choice": "opt-1", "confidence": 0.8,
                           "follow_verdict": 1})
    decision.ledger_write({"session_id": SID, "task_id": "t1",
                           "trigger": "post", "fork_class": "generic",
                           "choice": "opt-2", "confidence": 0.7})
    env = decision.build_envelope(SID, FRAMED_ASK, [], "pre")  # fork=generic
    a, b = env["options"]
    assert a["priors"] and "chosen 1x" in a["priors"] and "followed 1x" in a["priors"]
    assert b["priors"] and "chosen 1x" in b["priors"]
    # different fork class (no rows) -> priors stay null
    env2 = decision.build_envelope(SID, "keep the service or drop it?", [], "pre")
    assert env2["fork_class"] == "keep_die"
    assert all(o["priors"] is None for o in env2["options"])


def test_causal_carryover_n_plus_1(_reset):
    # envelope N+1 after a steered turn: causal context includes the last
    # post_fork_scan verdict's trajectory from THIS session's ledger
    decision.ledger_write({"session_id": SID, "task_id": "t9",
                           "trigger": "post", "trigger_kind": "post_fork_scan",
                           "fork_class": "deploy", "choice": "opt-2",
                           "confidence": 0.85, "outcome": "pending"})
    env = decision.build_envelope(SID, FRAMED_ASK, [], "pre")
    assert "prior steered trajectory" in env["causal_context"]
    assert "choice=opt-2" in env["causal_context"]
    assert "outcome=pending" in env["causal_context"]
    # other sessions' steering does NOT leak in
    decision.ledger_write({"session_id": "s-other", "task_id": "t10",
                           "trigger": "post", "trigger_kind": "post_fork_scan",
                           "fork_class": "deploy", "choice": "opt-1",
                           "confidence": 0.9, "outcome": "pending"})
    env2 = decision.build_envelope(SID, "redis or memcached?", [], "pre")
    assert "choice=opt-2" in env2["causal_context"]  # still THIS session's


def test_causal_carryover_none_when_no_prior_steering(_reset):
    env = decision.build_envelope(SID, FRAMED_ASK, [], "pre")
    assert "prior steered trajectory" not in env["causal_context"]
