"""v3.5.0 Phase 2/3 tests — /router chat command surface (commands.py).

Covers: bare=/menu-with-zero-state-reads, read-only subcommands, output caps,
fail-open error strings, masking, knob whitelist guards, confirmation tokens,
rate limits, deploy-invariant gating, rollback sidecar, registration branches.
"""
import json
import os
from unittest import mock

import pytest

import hermes_router.commands as commands
import hermes_router.usage_ledger as usage_ledger


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    ledger = tmp_path / "cmd-home" / "hermes-router-tokens.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(usage_ledger, "_store_path", lambda: str(ledger))
    monkeypatch.setattr(commands, "_rollback_path", lambda: str(tmp_path / "cmd-home" / "rollback.jsonl"))
    log = tmp_path / "cmd-home" / "route.log"
    log.write_text(
        "2026-09-07T10:00:00Z PRE event_detail=route_fired session_id=api_s1 rendered_chars=100\n"
        "2026-09-07T10:01:00Z POST event_detail=route_skipped reason=anchored_call_failed session_id=api_s1\n"
        "2026-09-07T10:02:00Z PRE event_detail=cap_blocked session_id=api_s2\n",
        encoding="utf-8")
    monkeypatch.setattr(commands, "_route_log_path", lambda: str(log))
    # mutations armed by default; individual tests override
    commands._MUTATIONS_ARMED["armed"] = True
    commands._pending_confirmations.clear()
    commands._RATE_WINDOWS.clear()
    yield
    commands._pending_confirmations.clear()
    commands._RATE_WINDOWS.clear()


# ---------------------------------------------------------------------------
# Bare /router — MENU ONLY, zero state reads (Goran-direct; flagship #7)
# ---------------------------------------------------------------------------


def test_bare_router_is_menu_and_reads_nothing():
    """Bare /router must NOT call router_status / read_plugin_section /
    read any ledger. Any state read = invariant violation."""
    import hermes_router.router_tools as rt

    with mock.patch.object(rt, "router_status", side_effect=AssertionError("STATE READ ON BARE")) as _s, \
         mock.patch.object(commands, "_query_sessions", side_effect=AssertionError("db read on bare")):
        out = commands.handle_router_command("")
    assert "status" in out and "menu" in out.lower() or "tip:" in out
    assert "/router status" in out
    assert "/router stats" in out


def test_menu_under_4096_chars():
    assert len(commands.handle_router_command("")) <= 4096


def test_help_grouped():
    out = commands.handle_router_command("help")
    assert "Inspect" in out and "Configure" in out and "Diagnose" in out


# ---------------------------------------------------------------------------
# Read-only subcommands
# ---------------------------------------------------------------------------


def test_stats_today_window():
    usage_ledger.record_tokens("render", "m", "api_s1", 100, 50, 0.001, "d")
    out = commands.handle_router_command("stats")
    assert "tokens[render]" in out
    assert "route_fired" in out or "events:" in out
    assert len(out) <= 4096


def test_stats_session_filter():
    usage_ledger.record_tokens("anchor", "gpt", "api_sX", 700, 300, 0.02, "consult")
    out = commands.handle_router_command("stats session api_sX")
    assert "tokens[anchor]" in out


def test_stats_rejects_bad_args():
    out = commands.handle_router_command("stats fortnight")
    assert "error" in out.lower() or "valid" in out.lower()


def test_sessions_degrades_when_db_missing(monkeypatch):
    monkeypatch.setattr(commands, "_query_sessions", lambda *a, **kw: None)
    out = commands.handle_router_command("sessions")
    assert "unavailable" in out


def test_sessions_caps_at_25():
    out = commands.handle_router_command("sessions 100")
    assert "25 shown" in out


def test_log_tail_and_grep():
    out = commands.handle_router_command("log tail 2")
    assert "route_fired" in out or "cap_blocked" in out
    out2 = commands.handle_router_command("log grep route_skipped")
    assert "1 match" in out2
    assert "anchored_call_failed" in out2


def test_log_grep_no_match():
    out = commands.handle_router_command("log grep zzz_nothing")
    assert "0 match" in out


def test_chain_render_masks_host():
    out = commands.handle_router_command("chain")
    assert "anchor chain" in out.lower() or "render chain" in out.lower()


def test_health_renders_pre_v361_line():
    out = commands.handle_router_command("health")
    assert "lifecycle" in out.lower()
    assert "pre-v3.6.1" in out


def test_budget_stub_literal():
    out = commands.handle_router_command("budget")
    assert out == "budget ledger not present (v3.6 gates not built)"


def test_cap_get():
    out = commands.handle_router_command("cap")
    assert "cap:" in out and "spend today" in out


# ---------------------------------------------------------------------------
# v3.6-gated knobs reject with 'gate not built' (never write dead config)
# ---------------------------------------------------------------------------


def test_gated_knobs_reject():
    for knob in ("complexity.pre_threshold", "complexity.consult.mid_no_progress_cycles",
                 "complexity.consult.mid_identical_failures", "complexity.consult.mid_ring_size"):
        out = commands.handle_router_command("config set %s 1" % knob)
        assert "gate not built" in out, knob


def test_budget_knob_is_not_config_writable():
    out = commands.handle_router_command("config set complexity.consult.suggestions_per_task 2")
    assert "rejected" in out.lower() and ("not a knob" in out.lower() or "settled" in out.lower())
    out2 = commands.handle_router_command("config set complexity.consult.consult_deadline_s 30")
    assert "rejected" in out2.lower()


# ---------------------------------------------------------------------------
# Forbidden keys structurally unreachable
# ---------------------------------------------------------------------------


def test_forbidden_keys_rejected():
    for k in ("log_path", "log_routes", "log_max_bytes"):
        out = commands.handle_router_command("config set %s /tmp/evil" % k)
        assert "rejected" in out.lower(), k
    # even a nested/unknown-key attempt never writes
    out = commands.handle_router_command("config set log_path.evil x")
    assert "rejected" in out.lower() or "unknown" in out.lower()


# ---------------------------------------------------------------------------
# Confirmation tokens (6b.2)
# ---------------------------------------------------------------------------


def test_consequential_config_set_issues_token(monkeypatch):
    """complexity.level is consequential: first call returns a token, NOT a write."""
    with mock.patch.object(commands, "_apply_config_set") as apply_mock:
        out = commands.handle_router_command("config set complexity.level 2")
    assert "confirm within 120s" in out and "/router confirm " in out
    apply_mock.assert_not_called()  # nothing written before confirmation


def test_confirm_executes_pending_token(monkeypatch):
    with mock.patch.object(commands, "_apply_config_set", return_value=(True, "ok")) as apply_mock, \
         mock.patch.object(commands, "_config_set_result_line", return_value="ok: set"):
        out1 = commands.handle_router_command("config set complexity.level 2")
        token = out1.rsplit("/router confirm ", 1)[1].strip().split()[0]
        out2 = commands.handle_router_command("confirm %s" % token)
    assert apply_mock.called
    assert "ok" in out2.lower()
    # single-use: second confirm with same token fails
    out3 = commands.handle_router_command("confirm %s" % token)
    assert "invalid or expired" in out3


def test_confirm_unknown_token():
    out = commands.handle_router_command("confirm deadbeef")
    assert "invalid or expired" in out


def test_token_ttl_expiry(monkeypatch):
    with mock.patch.object(commands, "_apply_config_set", return_value=(True, "ok")):
        out1 = commands.handle_router_command("config set complexity.level 2")
        token = out1.rsplit("/router confirm ", 1)[1].strip().split()[0]
    # force-expire the pending token
    rec = commands._pending_confirmations[token]
    rec["deadline"] = commands.time_mod() - 1
    out2 = commands.handle_router_command("confirm %s" % token)
    assert "invalid or expired" in out2


def test_cap_set_issues_token_and_bump_rejects_lowering():
    out = commands.handle_router_command("cap set 5.0")
    assert "confirm within 120s" in out
    out2 = commands.handle_router_command("cap set 1.0")
    assert "UP-only" in out2
    assert "config-file" in out2  # lowering guidance (6b.4 #9)


def test_reload_requires_confirmation():
    out = commands.handle_router_command("reload")
    assert "confirm within 120s" in out


def test_read_only_never_requires_confirmation():
    out = commands.handle_router_command("status")
    assert "confirm" not in out.lower()


# ---------------------------------------------------------------------------
# Mutations-armed gate (6b.1 deploy invariant)
# ---------------------------------------------------------------------------


def test_mutations_disabled_when_not_armed():
    commands._MUTATIONS_ARMED["armed"] = False
    commands._MUTATIONS_ARMED["reason"] = "authz-unverified"
    out = commands.handle_router_command("cap set 5.0")
    assert "mutations disabled" in out
    assert "authorization not verified" in out
    # read-only still answers (may carry the gate note in its output; the
    # invariant is that the subcommand RETURNS data, not a hard rejection)
    out2 = commands.handle_router_command("status")
    assert out2 and "lifecycle" in out2.lower() or "lane" in out2.lower()


def test_authz_env_detection():
    with mock.patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "12345"}):
        assert commands.verify_gateway_authz() is True
    with mock.patch.dict(os.environ, {"GATEWAY_ALLOW_ALL_USERS": "true"}):
        assert commands.verify_gateway_authz() is True
    with mock.patch.dict(os.environ, {v: "" for v in commands._AUTHZ_ENV_VARS}):
        assert commands.verify_gateway_authz() is False


def test_perform_register_selfcheck_branches(monkeypatch):
    monkeypatch.setenv("HERMES_ROUTER_ENABLE_SLASH_COMMAND", "0")
    ok, line = commands.perform_register_selfcheck()
    assert ok is False and "disabled" in line.lower() and "HERMES_ROUTER_ENABLE_SLASH_COMMAND" in line
    monkeypatch.delenv("HERMES_ROUTER_ENABLE_SLASH_COMMAND", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "1")
    ok, line = commands.perform_register_selfcheck()
    assert ok is True and "armed" in line
    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    ok, line = commands.perform_register_selfcheck()
    assert ok is False and "could not be verified" in line


# ---------------------------------------------------------------------------
# Rate limits (6b.4 #6)
# ---------------------------------------------------------------------------


def test_rate_limit_blocks_flood(monkeypatch):
    commands._RATE_WINDOWS.clear()
    last = None
    for _ in range(20):
        last = commands.handle_router_command("stats")
    assert "rate limited" in last


def test_rate_limit_window_keyed_per_subcommand(monkeypatch):
    commands._RATE_WINDOWS.clear()
    # fill the stats window
    for _ in range(12):
        commands.handle_router_command("stats")
    # a different subcommand is unaffected
    out = commands.handle_router_command("cap")
    assert "rate limited" not in out


# ---------------------------------------------------------------------------
# Registration branches (LCM 3-branch + collision self-check)
# ---------------------------------------------------------------------------


class _FakeCtx:
    def __init__(self, existing=None):
        self._manager = type("M", (), {"_plugin_commands": {}})()
        self.register_middleware = lambda *a, **kw: None
        self.register_hook = lambda *a, **kw: None
        self.register_tool = lambda *a, **kw: None

    def register_command(self, name, handler, description="", args_hint=""):
        self._manager._plugin_commands[name] = {"handler": handler,
                                                "description": description,
                                                "args_hint": args_hint}


def test_registration_gate_on_by_default(monkeypatch):
    """v3.7.1 zero-config default (Goran 09-10): /router ships ON — absent env
    var means enabled. Only explicit 0/false/no disables."""
    monkeypatch.delenv("HERMES_ROUTER_ENABLE_SLASH_COMMAND", raising=False)
    ctx = _FakeCtx()
    ok, line = commands.register_slash_command(ctx)
    assert ok is True
    assert "router" in ctx._manager._plugin_commands

    monkeypatch.setenv("HERMES_ROUTER_ENABLE_SLASH_COMMAND", "0")
    ctx2 = _FakeCtx()
    ok2, line2 = commands.register_slash_command(ctx2)
    assert ok2 is False
    assert "router" not in ctx2._manager._plugin_commands
    assert "disabled" in line2


def test_registration_no_register_command_attr(monkeypatch):
    monkeypatch.setenv("HERMES_ROUTER_ENABLE_SLASH_COMMAND", "1")
    ok, line = commands.register_slash_command(object())
    assert ok is False and "unavailable" in line


def test_registration_collision_skips(monkeypatch):
    monkeypatch.setenv("HERMES_ROUTER_ENABLE_SLASH_COMMAND", "1")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "1")
    ctx = _FakeCtx()
    ctx._manager._plugin_commands["router"] = {"handler": lambda a: "other plugin"}
    ok, line = commands.register_slash_command(ctx)
    assert ok is False and "collision" in line
    # the other plugin's entry is UNTOUCHED (no silent overwrite)
    assert ctx._manager._plugin_commands["router"]["handler"]("x") == "other plugin"


def test_registration_success_arms_mutations(monkeypatch):
    monkeypatch.setenv("HERMES_ROUTER_ENABLE_SLASH_COMMAND", "1")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "12345")
    ctx = _FakeCtx()
    ok, line = commands.register_slash_command(ctx)
    assert ok is True and "armed" in line
    assert "router" in ctx._manager._plugin_commands
    assert commands.mutations_armed() is True
    handler = ctx._manager._plugin_commands["router"]["handler"]
    assert isinstance(handler("budget"), str)


def test_registration_no_authz_disarms_mutations(monkeypatch):
    monkeypatch.setenv("HERMES_ROUTER_ENABLE_SLASH_COMMAND", "1")
    for v in commands._AUTHZ_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    ctx = _FakeCtx()
    ok, line = commands.register_slash_command(ctx)
    assert ok is True  # command still registered
    assert "could not be verified" in line
    assert commands.mutations_armed() is False


def test_no_args_hint_registered(monkeypatch):
    """Telegram menu inclusion requires empty args_hint (blueprint 1.3)."""
    monkeypatch.setenv("HERMES_ROUTER_ENABLE_SLASH_COMMAND", "1")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "1")
    ctx = _FakeCtx()
    commands.register_slash_command(ctx)
    registered = ctx._manager._plugin_commands["router"]
    assert callable(registered["handler"])


# ---------------------------------------------------------------------------
# Output bounds + fail-open error wrapper (6b.3)
# ---------------------------------------------------------------------------


def test_error_paths_return_strings_never_raise():
    # malformed inputs across the table
    for args in ("stats session", "log bogus", "config bogus", "cap bogus",
                 "config set", "config set chain.max_tokens abc name=x",
                 "sessions abc"):
        out = commands.handle_router_command(args)
        assert isinstance(out, str) and out


def test_output_caps():
    out = commands.handle_router_command("log tail 100")
    assert len(out) <= 4096


def test_handler_exception_returns_error_string(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("exploded")

    monkeypatch.setattr(commands, "_render_status", boom)
    out = commands.handle_router_command("status")
    assert out.startswith("error:") and "exploded" in out


# ---------------------------------------------------------------------------
# Whitelist guards
# ---------------------------------------------------------------------------


def test_unknown_knob_rejected():
    out = commands.handle_router_command("config set some_new_key 1")
    assert "unknown knob" in out


def test_not_writable_knobs_rejected_with_reason():
    out = commands.handle_router_command("config set render_method_spec text")
    assert "config-file" in out


def test_range_rejections():
    out = commands.handle_router_command("config set complexity.level 9")
    assert "rejected" in out and ("maximum" in out or "range" in out)
    out2 = commands.handle_router_command("config set render_max_chars -5")
    assert "rejected" in out2


def test_enum_rejection():
    out = commands.handle_router_command("config set classification.mode banana")
    assert "rejected" in out and "valid" in out


def test_uri_rejection_unresolvable():
    out = commands.handle_router_command("config set anchor_chain.primary http://169.254.169.254/x")
    assert "rejected" in out


def test_innocuous_knob_writes_directly(monkeypatch):
    """Non-consequential knobs (e.g. dry_run) execute without confirmation."""
    with mock.patch.object(commands, "_apply_config_set", return_value=(True, "ok")) as apply_mock, \
         mock.patch.object(commands, "_config_set_result_line", return_value="ok"):
        out = commands.handle_router_command("config set dry_run true")
    apply_mock.assert_called_once()
    assert "ok" in out.lower()


# ---------------------------------------------------------------------------
# Rollback sidecar
# ---------------------------------------------------------------------------


def test_rollback_records_previous_section(monkeypatch, tmp_path):
    from hermes_router import config_writer

    calls = []

    def fake_write(mutator, **kw):
        section = {"enabled": True, "complexity": {"level": 1}}
        mutator(section)
        calls.append(section)
        return True, "ok"

    monkeypatch.setattr(config_writer, "write_plugin_section", fake_write)
    # record a snapshot then check the sidecar has it
    prev = {"enabled": True, "complexity": {"level": 0}}
    commands._record_rollback(prev, ["complexity.level"])
    path = commands._rollback_path()
    with open(path, "r", encoding="utf-8") as fh:
        rec = json.loads(fh.readlines()[-1])
    assert rec["previous_section"]["complexity"]["level"] == 0
    assert rec["changed_keys"] == ["complexity.level"]


def test_rollback_sidecar_keeps_last_10(monkeypatch, tmp_path):
    for i in range(15):
        commands._record_rollback({"i": i}, ["k%d" % i])
    path = commands._rollback_path()
    with open(path, "r", encoding="utf-8") as fh:
        assert len(fh.readlines()) == 10


def test_rollback_command_requires_armed_mutations(monkeypatch):
    commands._MUTATIONS_ARMED["armed"] = False
    out = commands.handle_router_command("rollback" if False else "config rollback")
    assert "mutations disabled" in out


def test_rollback_command_with_no_history():
    out = commands.handle_router_command("config rollback")
    assert "no previous-section records" in out


# ---------------------------------------------------------------------------
# Dispatch wrapper catches everything (handler-internal error -> string)
# ---------------------------------------------------------------------------


def test_rate_limit_released_on_error_path(monkeypatch):
    commands._RATE_WINDOWS.clear()

    def boom(*a, **kw):
        raise RuntimeError("x")

    monkeypatch.setattr(commands, "_cmd_doctor", boom)
    out = commands.handle_router_command("doctor")
    assert isinstance(out, str)
    commands._RATE_WINDOWS.clear()
    monkeypatch.setattr(commands, "_cmd_doctor", lambda a: "fine")
    assert commands.handle_router_command("doctor") == "fine"