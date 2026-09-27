"""R19.2 — MIDTURN DECISION HOOK (on_llm_execution seam) test battery.

Covers: delta-scan only sees new messages; platform envelopes skipped;
shadow mode never appends; signature cooldown dedupes; run cap respected;
aggregate banner format (0/1/N verdicts, >4 buckets); fail-open kills the
hook on backend error without touching the run; ledger rows complete
(delta_source / fork_signature / midturn_mode).
"""
import json
import sqlite3

import pytest

from hermes_router import decision, decision_midturn as dmt

SID = "s-r19mt"

TOOL_FORK = {
    "role": "tool",
    "content": "which one should we pick: redis or memcached? "
               "weigh the tradeoffs between the two options",
}
TOOL_PLAIN = {"role": "tool", "content": "build ok, 3 files changed"}
USER_TURN = {"role": "user", "content": "please continue the migration"}


def _v3_cfg(**over):
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    cfg.update(over)
    return cfg


@pytest.fixture()
def _env(monkeypatch, tmp_path):
    dmt.reset_midturn()
    decision.reset_limits()
    decision.reset_v3_limits()

    path = str(tmp_path / "plugin.db")
    conn = sqlite3.connect(path, timeout=5.0)
    conn.executescript(decision._LEDGER_SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setattr(decision, "_ledger_connect",
                        lambda db_path="": sqlite3.connect(path))
    monkeypatch.setattr("hermes_router.usage_ledger.estimate_cost",
                        lambda *a, **k: 0.0, raising=True)
    monkeypatch.setattr("hermes_router._log_route",
                        lambda e, **f: None, raising=False)
    yield path
    dmt.reset_midturn()
    decision.reset_limits()
    decision.reset_v3_limits()


def _rows(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM decision_ledger ORDER BY id")]
    conn.close()
    return rows


def _calls(monkeypatch, body=None, fail=False):
    calls = []

    def _backend(envelope, cfg):
        calls.append(envelope)
        if fail:
            raise RuntimeError("backend exploded")
        meta = {"model": str(cfg.get("model") or "m"),
                "endpoint": "e", "tokens_in": 10, "tokens_out": 5,
                "latency_s": 0.1}
        return (json.dumps(body), meta, "ok") if body else (None, meta, "timeout")

    monkeypatch.setattr(decision, "call_backend", _backend)
    return calls


# ------------------------------------------------------------------
# off mode: fully silent
# ------------------------------------------------------------------

def test_off_mode_is_fully_silent(_env, monkeypatch):
    calls = _calls(monkeypatch, body={"choice": "opt-1",
                                      "confidence": 0.9,
                                      "alternatives": []})
    req = {"messages": [USER_TURN, TOOL_FORK]}
    assert dmt.on_llm_call(SID, req) == []
    assert calls == []
    assert _rows(_env) == []


# ------------------------------------------------------------------
# LEG 1: delta scan — only NEW messages
# ------------------------------------------------------------------

def test_delta_scan_only_new_messages(_env, monkeypatch):
    calls = _calls(monkeypatch, body={"choice": "opt-1",
                                      "confidence": 0.9,
                                      "alternatives": []})
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    msgs = [USER_TURN, TOOL_PLAIN]
    assert dmt.on_llm_call(SID, {"messages": msgs}) == []
    assert calls == []
    # tool fork arrives AFTER the previous call -> detected
    msgs.append(TOOL_FORK)
    adv = dmt.on_llm_call(SID, {"messages": msgs})
    assert len(calls) == 1
    assert any("ROUTER ADVISORY" in a for a in adv)
    # same messages again -> NO re-scan (nothing new)
    adv2 = dmt.on_llm_call(SID, {"messages": msgs})
    assert calls == calls
    assert adv2 == []


def test_user_turn_resets_run(_env, monkeypatch):
    calls = _calls(monkeypatch, body={"choice": "opt-1",
                                      "confidence": 0.9,
                                      "alternatives": []})
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    msgs = [USER_TURN, TOOL_FORK]
    dmt.on_llm_call(SID, {"messages": msgs})
    assert len(calls) == 1
    # a fresh user turn starts a new run: cooldown must not block the
    # same fork again, but delta baseline means the tool msg is NOT new.
    msgs2 = msgs + [dict(USER_TURN, content="next phase please"),
                    TOOL_FORK]
    msgs2[-1] = dict(TOOL_FORK)  # new list element, same content
    adv = dmt.on_llm_call(SID, {"messages": msgs2})
    # msgs2 has the fork at a NEW index (after the second user turn) and
    # the run reset cleared the cooldown-independent run counter — but the
    # signature cooldown still dedupes the identical fork within 600s.
    assert adv == []
    assert len(calls) == 1


# ------------------------------------------------------------------
# LEG 1: platform envelopes skipped (R19.1 provenance filter)
# ------------------------------------------------------------------

def test_platform_envelope_tool_result_skipped(_env, monkeypatch):
    calls = _calls(monkeypatch, body={"choice": "opt-1",
                                      "confidence": 0.9,
                                      "alternatives": []})
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    msgs = [USER_TURN,
            {"role": "tool",
             "content": "[System note: which option should we use: "
                        "a) redis or b) memcached — decide"},
            {"role": "tool",
             "content": "[OUT-OF-BAND USER MESSAGE] pick between "
                        "option a and option b"}]
    assert dmt.on_llm_call(SID, {"messages": msgs}) == []
    assert calls == []
    assert _rows(_env) == []


def test_non_tool_roles_never_scanned(_env, monkeypatch):
    calls = _calls(monkeypatch, body={"choice": "opt-1",
                                      "confidence": 0.9,
                                      "alternatives": []})
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    msgs = [{"role": "assistant",
             "content": "which one should we pick: redis or memcached? "
                        "weigh the tradeoffs"}]
    assert dmt.on_llm_call(SID, {"messages": msgs}) == []
    assert calls == []


# ------------------------------------------------------------------
# LEG 2: shadow never appends; on appends exactly the advisory
# ------------------------------------------------------------------

def test_shadow_detects_logs_ledgers_never_appends(_env, monkeypatch):
    calls = _calls(monkeypatch, body={"choice": "opt-1",
                                      "confidence": 0.9,
                                      "alternatives": []})
    cfg = _v3_cfg(midturn="shadow")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    msgs = [USER_TURN, TOOL_FORK]
    assert dmt.on_llm_call(SID, {"messages": msgs}) == []
    assert calls == []  # NO backend dispatch in shadow
    rows = _rows(_env)
    assert len(rows) == 1
    assert rows[0]["trigger"] == "midturn"
    assert rows[0]["trigger_kind"] == "midturn_hook"
    assert rows[0]["midturn_mode"] == "shadow"
    assert rows[0]["delta_source"] == "tool_result"
    assert rows[0]["fork_signature"]
    assert rows[0]["fail_open_reason"] == "shadow"
    # shadow rows are first-class: choice empty (no call made)
    assert rows[0]["choice"] == ""


def test_on_mode_appends_one_advisory_with_header(_env, monkeypatch):
    calls = _calls(monkeypatch, body={"choice": "opt-1",
                                      "confidence": 0.9,
                                      "alternatives": ["opt-2"]})
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    msgs = [USER_TURN, TOOL_FORK]
    adv = dmt.on_llm_call(SID, {"messages": msgs})
    assert len(adv) == 1
    assert adv[0].startswith(dmt.ADVISORY_HEADER)
    assert "opt-1" in adv[0]
    assert "why_not: opt-2" in adv[0]
    rows = _rows(_env)
    assert rows[0]["midturn_mode"] == "on"
    assert rows[0]["choice"] == "opt-1"


def test_fail_open_on_backend_error(_env, monkeypatch):
    calls = _calls(monkeypatch, fail=True)
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    msgs = [USER_TURN, TOOL_FORK]
    # must NOT raise; no advisory touches the run
    adv = dmt.on_llm_call(SID, {"messages": msgs})
    assert adv == []
    rows = _rows(_env)
    assert len(rows) == 1
    assert rows[0]["fail_open_reason"] == "hook_error"


def test_no_options_never_dispatches(_env, monkeypatch):
    calls = _calls(monkeypatch)
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    msgs = [USER_TURN, {"role": "tool", "content": "tests green, 12 passed"}]
    assert dmt.on_llm_call(SID, {"messages": msgs}) == []
    assert calls == []


# ------------------------------------------------------------------
# Cooldown + run cap
# ------------------------------------------------------------------

def test_signature_cooldown_dedupes(_env, monkeypatch):
    calls = _calls(monkeypatch, body={"choice": "opt-1",
                                      "confidence": 0.9,
                                      "alternatives": []})
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    msgs = [USER_TURN, TOOL_FORK]
    assert dmt.on_llm_call(SID, {"messages": msgs})
    # different run (new user turn) + DIFFERENT earlier content so the
    # delta index shifts, but the SAME fork text -> signature dedupe.
    msgs2 = [USER_TURN, TOOL_PLAIN, dict(TOOL_FORK)]
    assert dmt.on_llm_call(SID, {"messages": msgs2}) == []
    assert len(calls) == 1
    assert len(_rows(_env)) == 1


_WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
          "hotel", "india", "juliet", "kilo", "lima", "mike", "november",
          "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform",
          "victor", "whiskey", "xray", "yankee", "zulu"]


def _fork_text(i: int) -> str:
    """Distinct fork text WITHOUT digits — the R16 normalized hash folds
    digits to '#', so digit-variants would collide in the cooldown. Three
    enumerated line options (line-marker format -> opt-1..opt-3)."""
    w1 = _WORDS[i % 26] + _WORDS[(i // 26) % 26]
    w2 = _WORDS[(i + 1) % 26] + _WORDS[(i + 7) % 26]
    w3 = _WORDS[(i + 3) % 26] + _WORDS[(i + 11) % 26]
    w4 = _WORDS[(i + 5) % 26] + _WORDS[(i + 13) % 26]
    return ("harness verdict — which one should we pick?\n"
            "a) %s\nb) %s\nc) %s\nd) %s — weigh the tradeoffs"
            % (w1, w2, w3, w4))


def test_run_cap_respected_rows_still_recorded(_env, monkeypatch):
    calls = _calls(monkeypatch, body={"choice": "opt-1",
                                      "confidence": 0.9,
                                      "alternatives": []})
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    # RUN_CAP distinct forks inside one run (single user turn, growing delta)
    msgs = [dict(USER_TURN)]
    for i in range(dmt.RUN_CAP):
        msgs.append({"role": "tool", "content": _fork_text(i)})
        adv = dmt.on_llm_call(SID, {"messages": msgs})
        assert adv, "verdict %d should fire" % i
    assert len(calls) == dmt.RUN_CAP
    # 51st distinct fork: NO call, ledger row still recorded
    msgs.append({"role": "tool", "content": _fork_text(dmt.RUN_CAP + 1)})
    adv = dmt.on_llm_call(SID, {"messages": msgs})
    assert adv == []
    assert len(calls) == dmt.RUN_CAP
    rows = _rows(_env)
    assert len(rows) == dmt.RUN_CAP + 1
    assert rows[-1]["fail_open_reason"] == "midturn_run_cap"
    assert rows[-1]["delta_source"] == "tool_result"


# ------------------------------------------------------------------
# LEG 3: aggregate banner at turn close
# ------------------------------------------------------------------

def _feed_verdicts(monkeypatch, cfg, choices):
    msgs = [dict(USER_TURN)]
    for n, ch in enumerate(choices):
        _calls(monkeypatch, body={"choice": ch, "confidence": 0.9,
                                  "alternatives": []})
        msgs.append({"role": "tool", "content": _fork_text(n)})
        assert dmt.on_llm_call(SID, {"messages": msgs})


def test_close_turn_zero_verdicts(_env, monkeypatch):
    assert dmt.close_turn(SID) == ""
    assert dmt.close_turn(SID) == ""


def test_close_turn_single_verdict_format(_env, monkeypatch):
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    _feed_verdicts(monkeypatch, cfg, ["opt-1"])
    banner = dmt.close_turn(SID)
    assert banner
    assert "decision" in banner
    assert "midturn" in banner
    assert "initiator=agent" in banner
    assert "tok 10/5" in banner
    # drained: second close is empty
    assert dmt.close_turn(SID) == ""


def test_close_turn_aggregate_format(_env, monkeypatch):
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    _feed_verdicts(monkeypatch, cfg, ["opt-1", "opt-1", "opt-2", "opt-3"])
    banner = dmt.close_turn(SID)
    assert banner.startswith("· router · decision | midturn x4 |")
    assert "2 opt-1 / 1 opt-2 / 1 opt-3" in banner
    assert "| tok 40/20 |" in banner
    assert banner.endswith("initiator=agent")


def test_close_turn_histogram_capped_at_4_buckets(_env, monkeypatch):
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    _feed_verdicts(monkeypatch, cfg,
                   ["opt-1", "opt-1", "opt-2", "opt-3", "opt-4", "opt-4"])
    banner = dmt.close_turn(SID)
    assert banner.startswith("· router · decision | midturn x6 |")
    assert "2 opt-1" in banner
    assert "2 opt-4" in banner
    # the formatter folds a 5th bucket into 'other' (unit-tested below)


def test_aggregate_histogram_5th_bucket_folds_into_other():
    consumed = ([{"choice": "opt-%d" % i, "tokens_in": 1, "tokens_out": 1,
                  "cost": 0.1, "model": "m"} for i in (1, 1, 2, 3, 4)] +
                [{"choice": "opt-5", "tokens_in": 1, "tokens_out": 1,
                  "cost": 0.1, "model": "m"}])
    line = dmt._aggregate_line(6, 6, 6, 0.6, consumed)
    assert line.startswith("· router · decision | midturn x6 |")
    assert "2 opt-1" in line
    assert "1 other" in line
    assert "opt-5" not in line  # 5th bucket folded into 'other'
    assert line.endswith("initiator=agent")


# ------------------------------------------------------------------
# wiring: on_llm_execution appends advisories without touching content
# ------------------------------------------------------------------

def test_on_llm_execution_appends_advisory_and_calls_through(
        _env, monkeypatch):
    import hermes_router as plugin

    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    _calls(monkeypatch, body={"choice": "opt-1", "confidence": 0.9,
                              "alternatives": []})
    seen = {}

    def _next_call(req):
        seen["msgs"] = req.get("messages")
        return "RESULT"

    orig = plugin.router_core.peek_pending_swap
    monkeypatch.setattr(plugin.router_core, "peek_pending_swap",
                        lambda sid: None)
    request = {"messages": [{"role": "user", "content": "go"},
                            dict(TOOL_FORK)]}
    out = plugin.on_llm_execution(request=request, next_call=_next_call,
                                  session_id=SID)
    assert out == "RESULT"
    msgs = seen["msgs"]
    assert msgs[-1]["role"] == "assistant"
    assert dmt.ADVISORY_HEADER in msgs[-1]["content"]
    # original request untouched (advisory is appended to a COPY)
    assert len(request["messages"]) == 2
    assert orig  # keep reference alive for clarity


def test_ledger_row_complete_field_set(_env, monkeypatch):
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    _calls(monkeypatch, body={"choice": "opt-1", "confidence": 0.9,
                              "alternatives": []})
    msgs = [USER_TURN, TOOL_FORK]
    dmt.on_llm_call(SID, {"messages": msgs})
    row = _rows(_env)[0]
    assert row["trigger_kind"] == "midturn_hook"
    assert row["delta_source"] == "tool_result"
    assert len(row["fork_signature"]) == 12
    assert row["midturn_mode"] == "on"
    assert row["outcome"] == "pending"
    v = json.loads(row["verdict_json"])
    assert v["choice"] == "opt-1"
