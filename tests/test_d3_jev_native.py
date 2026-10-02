"""D3 (v4.13.2) — systemone-native jev backend (typesafe direct) battery.

Pins: envelope->systemone mapping (options->criteria), verdict parse from
the systemone answers shape, 422 fail-open backend_error, fallback chain
(5xx / network error -> openrouter jev), no regressions to the openrouter
jev path, stand_down passthrough, served-backend meta.
"""
import json
import sqlite3
import time

import pytest

import hermes_router as plugin
from hermes_router import decision, debug_banner, router_core

SID = "s-d3"
TASK_ID = "t-d3"

LOGGED = []

ASK = ("which approach? weigh the tradeoffs. option a) redis\n"
       "option b) memcached")


def _v3_cfg(**over):
    over.setdefault("rescan_dedupe", False)
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    cfg.update(over)
    return cfg


def _tmp_conn(tmp_path):
    import os

    path = str(tmp_path / "plugin.db")
    os.makedirs(tmp_path, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.executescript(decision._LEDGER_SCHEMA)
    return conn


@pytest.fixture()
def _reset(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    LOGGED.clear()
    debug_banner._ANCHOR_BANNERS.clear()
    debug_banner._ANCHOR_TASKS.clear()
    debug_banner._ANCHOR_SEGS.clear()
    decision.reset_limits()
    decision.reset_v3_limits()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(decision, "_cfg", lambda: dict(decision.DEFAULTS))
    monkeypatch.setattr(router_core, "_decision_cfg",
                        lambda: dict(decision.DEFAULTS))
    monkeypatch.setattr("hermes_router.usage_ledger.estimate_cost",
                        lambda *a, **k: 0.0, raising=True)
    monkeypatch.setattr(decision, "_ledger_connect",
                        lambda db_path="": _tmp_conn(tmp_path))
    yield tmp_path
    decision.reset_limits()
    decision.reset_v3_limits()
    decision._HTTP_ERROR_CODE = None
    debug_banner._ANCHOR_BANNERS.clear()


def _enable(monkeypatch, **over):
    cfg = _v3_cfg(**over)
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    monkeypatch.setattr(router_core, "_decision_cfg", lambda: cfg)
    return cfg


def _envelope(cfg):
    env = decision.build_envelope(SID, ASK, [], "pre", cfg)
    env["agent_frame"] = "conservative methodology frame"
    env["surrounding_context"] = "prior assistant context"
    return env


def _s1_body(choice, confidence, probabilities=None, confidence_out=0.8):
    return {"model": "jev-latest",
            "answers": {"choice": {"choice": choice,
                                   "confidence": confidence_out,
                                   "probabilities": probabilities or {}}},
            "usage": {"prompt_tokens": 11, "completion_tokens": 3}}


def _jev_body(choice="opt-1"):
    return {"choices": [{"message": {"content": json.dumps(
        {"choice": choice, "confidence": 0.7, "alternatives": []})}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2}}


# --------------------------------------------------------------------------
# Mapping: envelope -> systemone payload
# --------------------------------------------------------------------------

def test_payload_options_map_to_criteria(_reset, monkeypatch):
    cfg = _enable(monkeypatch, backend="jev_native")
    payload = decision._systemone_native_payload(_envelope(cfg), cfg)
    assert payload["model"] == "jev-latest"
    q = payload["questions"]["choice"]
    assert q["type"] == "choice"
    assert set(q["criteria"].keys()) == {"opt-1", "opt-2"}
    for oid, crit in q["criteria"].items():
        assert oid  # option id key (answers.choice returns ids)
        assert oid == "opt-1" or oid == "opt-2"
        assert crit  # description text present (label [+ causal frames])
    # state = frame + scope + causal + surrounding, no JSON contract
    assert "conservative methodology frame" in payload["state"]
    assert "risk_class=" in payload["state"]
    assert "prior assistant context" in payload["state"]
    assert '{"choice"' not in payload["state"]


def test_payload_empty_options_fail_open(_reset, monkeypatch):
    cfg = _enable(monkeypatch, backend="jev_native")
    assert decision._systemone_native_payload({}, cfg) == {}


# --------------------------------------------------------------------------
# Verdict parse from the systemone answers shape
# --------------------------------------------------------------------------

def test_answers_shape_parses_to_verdict_schema(_reset, monkeypatch):
    cfg = _enable(monkeypatch, backend="jev_native")
    env = _envelope(cfg)
    data = _s1_body("opt-1", None,
                    probabilities={"opt-1": 0.9, "opt-2": 0.1},
                    confidence_out=0.8)
    content = decision._systemone_verdict_content(data, env)
    v = json.loads(content)
    assert set(v.keys()) == {"choice", "confidence", "alternatives"}
    assert v["choice"] == "opt-1"
    assert v["confidence"] == 0.8
    assert v["alternatives"] == ["opt-2"]  # runner-up first
    verdict, reason = decision.validate_verdict(content, env)
    assert reason == "ok" and verdict["choice"] == "opt-1"


def test_answers_stand_down_passthrough(_reset, monkeypatch):
    cfg = _enable(monkeypatch, backend="jev_native")
    data = {"answers": {"choice": {"choice": "stand_down", "confidence": 0.0,
                                   "probabilities": {}}}}
    content = decision._systemone_verdict_content(data, _envelope(cfg))
    v = json.loads(content)
    assert v == {"choice": "stand_down", "confidence": 0.0,
                 "alternatives": []}


def test_answers_out_of_set_choice_fails_open(_reset, monkeypatch):
    cfg = _enable(monkeypatch, backend="jev_native")
    env = _envelope(cfg)
    data = _s1_body("opt-9", None)
    assert decision._systemone_verdict_content(data, env) == ""


# --------------------------------------------------------------------------
# call_backend: jev_native paths
# --------------------------------------------------------------------------

def test_jev_native_happy_path(_reset, monkeypatch):
    cfg = _enable(monkeypatch, backend="jev_native",
                  typesafe_api_key_env="D3_KEY")
    monkeypatch.setenv("D3_KEY", "k-native")
    seen = {}

    def _fake_post(url, headers, payload, timeout):
        seen["url"], seen["headers"], seen["payload"] = url, headers, payload
        assert url == decision.DEFAULTS["typesafe_endpoint"]
        assert headers["Authorization"] == "Bearer k-native"
        assert payload["questions"]["choice"]["type"] == "choice"
        return _s1_body("opt-2", None,
                        probabilities={"opt-2": 0.8, "opt-1": 0.2})

    monkeypatch.setattr(decision, "_http_post_json", _fake_post)
    content, meta, reason = decision.call_backend(_envelope(cfg), cfg)
    assert reason == "ok"
    v = json.loads(content)
    assert v["choice"] == "opt-2" and v["alternatives"] == ["opt-1"]
    assert meta["model"] == "jev-latest"
    assert meta["backend"] == "jev_native"
    assert meta["tokens_in"] == 11 and meta["tokens_out"] == 3


def test_jev_native_missing_key_fail_open(_reset, monkeypatch):
    cfg = _enable(monkeypatch, backend="jev_native",
                  typesafe_api_key_env="D3_KEY_MISSING")
    monkeypatch.delenv("D3_KEY_MISSING", raising=False)
    content, meta, reason = decision.call_backend(_envelope(cfg), cfg)
    assert content is None
    assert reason == decision.REASON_BACKEND_ERROR


def test_jev_native_422_fail_open_backend_error(_reset, monkeypatch):
    cfg = _enable(monkeypatch, backend="jev_native",
                  typesafe_api_key_env="D3_KEY")
    monkeypatch.setenv("D3_KEY", "k")

    def _fake_post(url, headers, payload, timeout):
        decision._HTTP_ERROR_CODE = 422
        return None

    monkeypatch.setattr(decision, "_http_post_json", _fake_post)
    content, meta, reason = decision.call_backend(_envelope(cfg), cfg)
    assert content is None
    assert reason == decision.REASON_BACKEND_ERROR  # NOT timeout
    assert meta["backend"] == "jev_native"


def test_jev_native_malformed_answer_fails_open(_reset, monkeypatch):
    cfg = _enable(monkeypatch, backend="jev_native",
                  typesafe_api_key_env="D3_KEY")
    monkeypatch.setenv("D3_KEY", "k")
    monkeypatch.setattr(decision, "_http_post_json",
                        lambda *a, **k: {"answers": {}})
    content, meta, reason = decision.call_backend(_envelope(cfg), cfg)
    assert content is None
    assert reason == decision.REASON_PARSE_FAIL


def test_jev_native_5xx_falls_back_to_openrouter_jev(_reset, monkeypatch):
    cfg = _enable(monkeypatch, backend="jev_native",
                  typesafe_api_key_env="D3_KEY", api_key_env="D3_OR_KEY")
    monkeypatch.setenv("D3_KEY", "k")
    monkeypatch.setenv("D3_OR_KEY", "k-or")
    calls = []

    def _fake_post(url, headers, payload, timeout):
        calls.append(url)
        if "typesafe" in url:
            decision._HTTP_ERROR_CODE = 503
            return None
        assert "openrouter" in url
        return _jev_body("opt-1")

    monkeypatch.setattr(decision, "_http_post_json", _fake_post)
    content, meta, reason = decision.call_backend(_envelope(cfg), cfg)
    assert reason == "ok"
    assert len(calls) == 2
    assert json.loads(content)["choice"] == "opt-1"
    assert meta["backend"] == "jev"  # ledger records the SERVED backend
    assert meta["model"] == decision.DEFAULTS["jev_model"]


def test_jev_native_network_error_falls_back(_reset, monkeypatch):
    cfg = _enable(monkeypatch, backend="jev_native",
                  typesafe_api_key_env="D3_KEY", api_key_env="D3_OR_KEY")
    monkeypatch.setenv("D3_KEY", "k")
    monkeypatch.setenv("D3_OR_KEY", "k-or")
    calls = []

    def _fake_post(url, headers, payload, timeout):
        calls.append(url)
        if "typesafe" in url:
            decision._HTTP_ERROR_CODE = None  # transport-level failure
            return None
        return _jev_body("opt-2")

    monkeypatch.setattr(decision, "_http_post_json", _fake_post)
    content, meta, reason = decision.call_backend(_envelope(cfg), cfg)
    assert reason == "ok" and len(calls) == 2
    assert json.loads(content)["choice"] == "opt-2"
    assert meta["backend"] == "jev"


def test_jev_native_401_does_not_fall_back(_reset, monkeypatch):
    # auth failure is not 5xx/network: fail-open, mapped, no fallback call
    cfg = _enable(monkeypatch, backend="jev_native",
                  typesafe_api_key_env="D3_KEY", api_key_env="D3_OR_KEY")
    monkeypatch.setenv("D3_KEY", "k")
    monkeypatch.setenv("D3_OR_KEY", "k-or")
    calls = []

    def _fake_post(url, headers, payload, timeout):
        calls.append(url)
        decision._HTTP_ERROR_CODE = 401
        return None

    monkeypatch.setattr(decision, "_http_post_json", _fake_post)
    content, meta, reason = decision.call_backend(_envelope(cfg), cfg)
    assert len(calls) == 1  # no fallback POST
    assert content is None
    assert reason == decision.REASON_BACKEND_AUTH


# --------------------------------------------------------------------------
# Pipeline end-to-end + ledger records the served backend
# --------------------------------------------------------------------------

def test_jev_native_pipeline_parks_advisory(_reset, monkeypatch):
    cfg = _enable(monkeypatch, backend="jev_native",
                  typesafe_api_key_env="D3_KEY")
    monkeypatch.setenv("D3_KEY", "k-native")
    monkeypatch.setattr(
        decision, "_http_post_json",
        lambda *a, **k: _s1_body("opt-1", None,
                                 probabilities={"opt-1": 0.85,
                                                "opt-2": 0.15}))
    decision.handle_decision_v3(session_id=SID, task_id=TASK_ID,
                                task_text=ASK,
                                log_route=lambda e, **f:
                                LOGGED.append((e, dict(f))))
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if debug_banner._ANCHOR_BANNERS.get(SID):
            break
        time.sleep(0.02)
    parked = debug_banner._ANCHOR_BANNERS.get(SID)
    assert parked
    # banner provenance: native model + typesafe provider name
    assert "jev-latest" in str(parked) and "typesafe" in str(parked)
    # advisory impulse frame present + ledger choice = closed-set id
    assert "the fork surfaces as" in str(parked)
    rows = decision.ledger_recent()
    assert rows and rows[0]["choice"] == "opt-1"
    assert rows[0]["model"] == "jev-latest"


def test_jev_native_pipeline_fallback_ledger_records_served(_reset,
                                                            monkeypatch):
    cfg = _enable(monkeypatch, backend="jev_native",
                  typesafe_api_key_env="D3_KEY", api_key_env="D3_OR_KEY")
    monkeypatch.setenv("D3_KEY", "k")
    monkeypatch.setenv("D3_OR_KEY", "k-or")
    logged = []

    def _fake_post(url, headers, payload, timeout):
        if "typesafe" in url:
            decision._HTTP_ERROR_CODE = 500
            return None
        return _jev_body("opt-1")

    monkeypatch.setattr(decision, "_http_post_json", _fake_post)
    decision._v3_worker(_envelope(cfg),
                        {"session_id": SID, "task_id": TASK_ID,
                         "trigger": "manual"},
                        cfg,
                        lambda e, **f: logged.append((e, dict(f))))
    rows = decision.ledger_recent()
    assert rows and rows[0]["model"] == decision.DEFAULTS["jev_model"]
    assert any(f.get("backend") == "jev" for _, f in logged)


# --------------------------------------------------------------------------
# Openrouter jev path regression (unchanged)
# --------------------------------------------------------------------------

def test_openrouter_jev_path_unregressed(_reset, monkeypatch):
    cfg = _enable(monkeypatch, backend="jev",
                  jev_model="typesafe/jev-router@pinned",
                  api_key_env="D3_OR_KEY")
    monkeypatch.setenv("D3_OR_KEY", "k-or")
    calls = []

    def _fake_post(url, headers, payload, timeout):
        calls.append(url)
        assert "openrouter" in url
        return {"choices": [{"message": {"content": json.dumps(
            {"choice": "opt-2", "confidence": 0.7,
             "alternatives": ["opt-1"]})}}],
            "usage": {"prompt_tokens": 33, "completion_tokens": 9}}

    monkeypatch.setattr(decision, "_http_post_json", _fake_post)
    content, meta, reason = decision.call_backend(_envelope(cfg), cfg)
    assert reason == "ok" and len(calls) == 1
    assert meta["model"] == "typesafe/jev-router@pinned"
    assert json.loads(content)["choice"] == "opt-2"
