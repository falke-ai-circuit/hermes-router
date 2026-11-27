"""m59 — the jev decision lane must work ALL the time (owner ruling).

Pins the three contract points measured as a silent no-op on the virgin
canary m59 (container hermes-canary, :18649, plugin m58, decision block
pinned enabled+midturn_prellm+jev_native, real tool-loop turn produced ZERO
decision|jev|midturn|impulse lines):

1. The pinned decision block reaches the lane: decision._cfg() reads the
   block through the canonical reader (hermes_router/core/config_access),
   not a dead relative import — on the pre-fix tree the block's `midturn`
   value never reached _mode() and the lane was always "off".
2. jev is a MODEL resolved through Hermes core: _jev_dispatch_model()
   returns an explicitly configured jev model, else the HOST main model
   (config_access.agent_model()) — and _call_jev_host() dispatches through
   the profile's aux path (_hermes_aux_call) with zero plugin-side
   endpoint/key config. A host declaring no jev model gets the host model.
3. Arming is observable on EVERY mid-turn LLM call: flush_and_scan() logs
   decision_armed (seam/backend/model) or decision_skipped (exact missing
   input) — never silence.
"""
import json
import sqlite3

import pytest

import hermes_router as plugin
from hermes_router import decision, decision_midturn as dmt

LOGGED = []

DEFAULTS_KEY_ENV_T = decision.DEFAULTS["typesafe_api_key_env"]
DEFAULTS_KEY_ENV_O = decision.DEFAULTS["api_key_env"]


def _tmp_conn(tmp_path):
    import os

    path = str(tmp_path / "m59.db")
    conn = sqlite3.connect(path, timeout=5.0)
    conn.executescript(decision._LEDGER_SCHEMA)
    return conn


@pytest.fixture()
def m59_reset(monkeypatch, tmp_path):
    LOGGED.clear()
    dmt.reset_midturn()
    decision.reset_limits()
    decision.reset_v3_limits()
    decision._HTTP_ERROR_CODE = None
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    # isolate the host-model reader under test
    monkeypatch.setattr("hermes_router.core.config_access.agent_model",
                        lambda: "host-main-model", raising=True)
    monkeypatch.setattr(decision, "_ledger_connect",
                        lambda db_path="": _tmp_conn(tmp_path))
    yield tmp_path
    dmt.reset_midturn()
    decision.reset_v3_limits()
    decision._HTTP_ERROR_CODE = None


# ---------------------------------------------------------------------------
# 1. the pinned block reaches the lane (the silent-gate root cause)
# ---------------------------------------------------------------------------

def _read_cfg_with_block(monkeypatch, block):
    # stub the canonical reader the fixed _cfg() must consult
    import hermes_router.core.config_access as ca

    monkeypatch.setattr(ca, "sub_block_alias",
                        lambda *names: dict(block), raising=True)
    return decision._cfg()


def test_pinned_decision_block_reaches_lane(m59_reset, monkeypatch):
    cfg = _read_cfg_with_block(monkeypatch, {
        "enabled": True, "seam": "midturn_prellm", "midturn": "on",
        "backend": "jev_native", "frame": "execution",
        "jev_native_model": "jev", "jev_model": "jev"})
    assert cfg["enabled"] is True
    assert dmt._mode(cfg) == "on"          # pre-fix: "off" (silent gate)
    assert cfg["backend"] == "jev_native"
    assert cfg.get("jev_native_model") == "jev"


# ---------------------------------------------------------------------------
# 2. jev is a model resolved through the host (one authority — core)
# ---------------------------------------------------------------------------

def test_jev_dispatch_model_explicit_pin_wins(m59_reset):
    cfg = dict(decision.DEFAULTS)
    cfg.update({"backend": "jev_native", "jev_native_model": "custom-jev-id"})
    assert decision._jev_dispatch_model(cfg) == "custom-jev-id"


def test_jev_dispatch_model_configured_jev_wins_over_host(m59_reset):
    """An explicitly CONFIGURED jev model (differs from the code default) is
    the profile's declaration — jev IS that model, not a host lookup."""
    cfg = dict(decision.DEFAULTS)
    cfg.update({"backend": "jev_native", "jev_native_model": "jev"})
    assert decision._jev_dispatch_model(cfg) == "jev"


def test_jev_dispatch_model_host_fallback(m59_reset):
    cfg = dict(decision.DEFAULTS)
    cfg["backend"] = "jev_native"
    assert decision._jev_dispatch_model(cfg) == "host-main-model"


def test_jev_host_dispatch_rides_aux_seam(m59_reset, monkeypatch):
    """Zero plugin-side endpoint/key config: the verdict comes back through
    _hermes_aux_call; meta names the resolved model."""
    cfg = dict(decision.DEFAULTS)
    cfg.update({"backend": "jev_native", "midturn": "on"})
    # a merged cfg always carries the DEFAULT key-env NAMES; the dispatch
    # gate treats "equal to the shipped default" as operator-silent.
    assert (cfg.get("typesafe_api_key_env")
            == DEFAULTS_KEY_ENV_T) and (cfg.get("api_key_env")
                                        == DEFAULTS_KEY_ENV_O)

    seen = {}

    def _fake_aux(payload_json, timeout):
        seen["payload"] = json.loads(payload_json)
        return json.dumps({"choices": [{"message": {"content": json.dumps(
            {"choice": "opt-1", "confidence": 0.8, "alternatives": []})}}]})

    monkeypatch.setattr("hermes_router.semantic_classifier._hermes_aux_call",
                        _fake_aux, raising=True)
    env = decision.build_envelope(
        "s-m59", "option a) redis\noption b) memcached", [], "pre", cfg)
    content, meta, reason = decision._call_jev_host(env, "prompt", cfg, 15)
    assert reason == "ok"
    assert seen["payload"]["model"] == "host-main-model"
    assert meta["model"] == "host-main-model"
    assert meta["endpoint"] == "hermes-auxiliary"


def test_call_backend_routes_host_shape_to_aux_seam(m59_reset, monkeypatch):
    """The canary shape (jev_native, no key envs anywhere) dispatches the
    verdict through the aux seam — the m58 ONE-authority client — instead of
    probing a TYPESAFE_API_KEY that no virgin host has."""
    cfg = dict(decision.DEFAULTS)
    cfg.update({"backend": "jev_native", "enabled": True})

    def _fake_aux(payload_json, timeout):
        return json.dumps({"choices": [{"message": {"content": json.dumps(
            {"choice": "opt-2", "confidence": 0.75, "alternatives": []})}}]})

    monkeypatch.setattr("hermes_router.semantic_classifier._hermes_aux_call",
                        _fake_aux, raising=True)
    env = decision.build_envelope(
        "s-m59", "option a) redis\noption b) memcached", [], "pre", cfg)
    content, meta, reason = decision.call_backend(env, cfg)
    assert reason == "ok"
    assert json.loads(content)["choice"] == "opt-2"
    assert meta["model"] == "host-main-model"


def test_call_backend_legacy_key_env_contract_unchanged(m59_reset,
                                                        monkeypatch):
    """An operator-declared key env keeps the legacy direct-HTTP contract —
    D3's pins (typesafe endpoint + Bearer key env) stay authoritative."""
    cfg = dict(decision.DEFAULTS)
    cfg.update({"backend": "jev_native", "typesafe_api_key_env": "M59_TK"})
    monkeypatch.setenv("M59_TK", "k")
    calls = []

    def _fake_post(url, headers, payload, timeout):
        calls.append(url)
        decision._HTTP_ERROR_CODE = 422
        return None

    monkeypatch.setattr(decision, "_http_post_json", _fake_post)
    env = decision.build_envelope(
        "s-m59", "option a) redis\noption b) memcached", [], "pre", cfg)
    content, meta, reason = decision.call_backend(env, cfg)
    assert calls and "typesafe" in calls[0]
    assert content is None and reason == decision.REASON_BACKEND_ERROR


# ---------------------------------------------------------------------------
# 3. arming is observable — never a silent no-op
# ---------------------------------------------------------------------------

def test_flush_and_scan_logs_armed_on_pinned_host(m59_reset, monkeypatch):
    cfg = dict(decision.DEFAULTS)
    cfg.update({"enabled": True, "midturn": "on", "backend": "jev_native",
                "jev_native_model": "jev", "seam": "midturn_prellm"})
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    LOGGED.clear()
    dmt.flush_and_scan("s-m59-armed", {"messages": []})
    armed = [f for e, f in LOGGED if e == "decision_armed"]
    assert armed, "midturn-on turn must log decision_armed"
    f = armed[0]
    assert f.get("seam") == "midturn_prellm"
    assert f.get("backend") == "jev_native"
    assert f.get("model") == "jev"


def test_flush_and_scan_logs_skipped_with_reason(m59_reset, monkeypatch):
    cfg = dict(decision.DEFAULTS)
    cfg["midturn"] = "off"
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    LOGGED.clear()
    dmt.flush_and_scan("s-m59-off", {"messages": []})
    skipped = [f for e, f in LOGGED if e == "decision_skipped"]
    assert skipped and skipped[0].get("reason") == "mode_off"
    assert skipped[0].get("seam") == "midturn_prellm"


def test_docker_log_line_shape_emit(m59_reset, monkeypatch, caplog):
    """The `decision.armed` logger line the canary log greps carry the
    mandated shape on the armed path."""
    import logging

    cfg = dict(decision.DEFAULTS)
    cfg.update({"enabled": True, "midturn": "on", "backend": "jev_native",
                "jev_native_model": "jev"})
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    LOGGED.clear()
    with caplog.at_level(logging.INFO,
                         logger="hermes_router.decision_midturn"):
        dmt.flush_and_scan("s-m59-log", {"messages": []})
    hits = [r.getMessage() for r in caplog.records
            if "decision.armed" in r.getMessage()]
    assert hits and "backend=jev_native" in hits[0] \
        and "model=jev" in hits[0]
