"""R19.2 ADDENDUM 3 — MIDTURN DECISION HOOK (transform_tool_result seam)
test battery.

Live-probed root cause: llm_execution fires ONCE per turn, not per LLM
call — detection as originally wired was structurally blind. These tests
pin the rewiring: detection triggers off transform_tool_result REGARDLESS
of middleware fire-count; shadow -> ledger row, never appends; no-options
-> suppressed; 'on' -> pending advisory flushed into the next request by
on_llm_execution. Cooldown, run cap, provenance skip, fail-open, ledger
fields (incl. tool_name), aggregate banner format all covered.
"""
import json
import sqlite3

import pytest

from hermes_router import decision, decision_midturn as dmt

SID = "s-r19mt"

TOOL_FORK = ("harness verdict — which one should we pick?\n"
             "a) redis\nb) memcached\nc) sqlite — weigh the tradeoffs")


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
    dmt.on_tool_result(SID, "bash", TOOL_FORK)
    assert dmt.flush(SID) == []
    assert calls == []
    assert _rows(_env) == []


# ------------------------------------------------------------------
# LEG 1: detection off transform_tool_result — fire-count irrelevant
# ------------------------------------------------------------------

def test_detection_triggers_off_tool_result_not_middleware(
        _env, monkeypatch):
    """The addendum's core pin: llm_execution fires once per turn —
    detection must come from transform_tool_result and must NOT depend on
    the middleware firing at all."""
    calls = _calls(monkeypatch, body={"choice": "opt-1",
                                      "confidence": 0.9,
                                      "alternatives": []})
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    # ZERO llm_execution fires: detection still lands via the tool hook
    dmt.on_tool_result(SID, "bash", "build ok, 3 files changed")  # suppressed
    dmt.on_tool_result(SID, "bash", TOOL_FORK)                    # verdict
    assert len(calls) == 1
    # middleware never fires again -> advisory stays queued (ledger-only
    # if the run ends); ONE flush delivers it.
    pending = dmt.flush(SID)
    assert len(pending) == 1
    assert pending[0].startswith(dmt.ADVISORY_HEADER)
    assert "opt-1" in pending[0]
    assert "why_not: (no viable alternative" in pending[0]
    assert dmt.flush(SID) == []


def test_flush_via_on_llm_execution_middleware(_env, monkeypatch):
    import hermes_router as plugin

    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    _calls(monkeypatch, body={"choice": "opt-1", "confidence": 0.9,
                              "alternatives": []})
    monkeypatch.setattr(plugin.router_core, "peek_pending_swap",
                        lambda sid: None)
    seen = {}

    def _next_call(req):
        seen["msgs"] = req.get("messages")
        return "RESULT"

    # tool result lands FIRST (mid-run, middleware silent), then the
    # middleware fires once — the advisory flushes into THAT request.
    ret = plugin.on_transform_tool_result(tool_name="bash", result=TOOL_FORK,
                                          session_id=SID)
    assert ret is None  # never replaces / blocks the tool result
    request = {"messages": [{"role": "user", "content": "go"}]}
    out = plugin.on_llm_execution(request=request, next_call=_next_call,
                                  session_id=SID)
    assert out == "RESULT"
    msgs = seen["msgs"]
    assert msgs[-1]["role"] == "assistant"
    assert dmt.ADVISORY_HEADER in msgs[-1]["content"]
    # original request untouched (advisory appended to a COPY)
    assert len(request["messages"]) == 1


def test_no_tool_results_no_advisory(_env, monkeypatch):
    import hermes_router as plugin

    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    _calls(monkeypatch, body={"choice": "opt-1", "confidence": 0.9,
                              "alternatives": []})
    monkeypatch.setattr(plugin.router_core, "peek_pending_swap",
                        lambda sid: None)
    request = {"messages": [{"role": "user", "content": "go"}]}
    out = plugin.on_llm_execution(request=request, next_call=lambda r: "OK",
                                  session_id=SID)
    assert out == "OK"
    assert dmt.flush(SID) == []


# ------------------------------------------------------------------
# LEG 1: provenance + no-options suppression
# ------------------------------------------------------------------

def test_platform_envelope_tool_result_skipped(_env, monkeypatch):
    calls = _calls(monkeypatch, body={"choice": "opt-1",
                                      "confidence": 0.9,
                                      "alternatives": []})
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    dmt.on_tool_result(SID, "bash",
                       "[System note: which option should we use: "
                       "a) redis or b) memcached — decide")
    dmt.on_tool_result(SID, "bash",
                       "[OUT-OF-BAND USER MESSAGE] pick between "
                       "option a and option b")
    assert dmt.flush(SID) == []
    assert calls == []
    assert _rows(_env) == []


def test_no_options_suppressed(_env, monkeypatch):
    calls = _calls(monkeypatch)
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    dmt.on_tool_result(SID, "bash", "tests green, 12 passed")
    assert dmt.flush(SID) == []
    assert calls == []
    assert _rows(_env) == []  # no-options = log-only suppression


# ------------------------------------------------------------------
# LEG 2: shadow never appends; ledger rows first-class
# ------------------------------------------------------------------

def test_shadow_detects_logs_ledgers_never_appends(_env, monkeypatch):
    calls = _calls(monkeypatch, body={"choice": "opt-1",
                                      "confidence": 0.9,
                                      "alternatives": []})
    cfg = _v3_cfg(midturn="shadow")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    dmt.on_tool_result(SID, "bash", TOOL_FORK)
    assert dmt.flush(SID) == []
    assert calls == []  # NO backend dispatch in shadow
    rows = _rows(_env)
    assert len(rows) == 1
    assert rows[0]["trigger"] == "midturn"
    assert rows[0]["trigger_kind"] == "midturn_hook"
    assert rows[0]["midturn_mode"] == "shadow"
    assert rows[0]["delta_source"] == "tool_result"
    assert rows[0]["tool_name"] == "bash"
    assert rows[0]["fork_signature"]
    assert rows[0]["fail_open_reason"] == "shadow"
    assert rows[0]["choice"] == ""  # no call made


def test_fail_open_on_backend_error(_env, monkeypatch):
    calls = _calls(monkeypatch, fail=True)
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    dmt.on_tool_result(SID, "bash", TOOL_FORK)  # must NOT raise
    assert dmt.flush(SID) == []
    rows = _rows(_env)
    assert len(rows) == 1
    assert rows[0]["fail_open_reason"] == "hook_error"


# ------------------------------------------------------------------
# Cooldown + run cap
# ------------------------------------------------------------------

_WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
          "hotel", "india", "juliet", "kilo", "lima", "mike", "november",
          "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform",
          "victor", "whiskey", "xray", "yankee", "zulu"]


def _fork_text(i: int) -> str:
    """Distinct fork text WITHOUT digits — the R16 normalized hash folds
    digits to '#', so digit-variants would collide in the cooldown. Four
    enumerated line options (line-marker format -> opt-1..opt-4)."""
    w1 = _WORDS[i % 26] + _WORDS[(i // 26) % 26]
    w2 = _WORDS[(i + 1) % 26] + _WORDS[(i + 7) % 26]
    w3 = _WORDS[(i + 3) % 26] + _WORDS[(i + 11) % 26]
    w4 = _WORDS[(i + 5) % 26] + _WORDS[(i + 13) % 26]
    return ("harness verdict — which one should we pick?\n"
            "a) %s\nb) %s\nc) %s\nd) %s — weigh the tradeoffs"
            % (w1, w2, w3, w4))


def test_signature_cooldown_dedupes(_env, monkeypatch):
    calls = _calls(monkeypatch, body={"choice": "opt-1",
                                      "confidence": 0.9,
                                      "alternatives": []})
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    dmt.on_tool_result(SID, "bash", TOOL_FORK)
    assert dmt.flush(SID)
    # identical fork again -> signature dedupe, no second call
    dmt.on_tool_result(SID, "bash", TOOL_FORK)
    assert dmt.flush(SID) == []
    assert len(calls) == 1
    assert len(_rows(_env)) == 1


def test_run_cap_respected_rows_still_recorded(_env, monkeypatch):
    calls = _calls(monkeypatch, body={"choice": "opt-1",
                                      "confidence": 0.9,
                                      "alternatives": []})
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    for i in range(dmt.RUN_CAP):
        dmt.on_tool_result(SID, "bash", _fork_text(i))
    assert len(calls) == dmt.RUN_CAP
    dmt.flush(SID)  # drain pending; cap accounting unaffected
    # 51st distinct fork: NO call, ledger row still recorded
    dmt.on_tool_result(SID, "bash", _fork_text(dmt.RUN_CAP + 1))
    assert len(calls) == dmt.RUN_CAP
    rows = _rows(_env)
    assert len(rows) == dmt.RUN_CAP + 1
    assert rows[-1]["fail_open_reason"] == "midturn_run_cap"
    assert rows[-1]["delta_source"] == "tool_result"
    assert rows[-1]["tool_name"] == "bash"


def test_close_turn_resets_run_counter(_env, monkeypatch):
    """Turn close is the run boundary: the cap accumulator resets."""
    calls = _calls(monkeypatch, body={"choice": "opt-1",
                                      "confidence": 0.9,
                                      "alternatives": []})
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    dmt.on_tool_result(SID, "bash", _fork_text(0))
    assert dmt.close_turn(SID)  # single-verdict banner + run reset
    # same signature is cooldown-blocked, but a NEW fork fires fine and
    # the run cap starts from zero again
    dmt.on_tool_result(SID, "bash", _fork_text(1))
    assert len(calls) == 2


# ------------------------------------------------------------------
# LEG 3: aggregate banner at turn close
# ------------------------------------------------------------------

def _feed_verdicts(monkeypatch, cfg, choices):
    for n, ch in enumerate(choices):
        _calls(monkeypatch, body={"choice": ch, "confidence": 0.9,
                                  "alternatives": []})
        dmt.on_tool_result(SID, "bash", _fork_text(n))


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
    assert dmt.close_turn(SID) == ""  # drained


def test_close_turn_aggregate_format(_env, monkeypatch):
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    _feed_verdicts(monkeypatch, cfg, ["opt-1", "opt-1", "opt-2", "opt-3"])
    banner = dmt.close_turn(SID)
    assert banner.startswith("· router · decision | midturn x4 |")
    assert "2 opt-1 / 1 opt-2 / 1 opt-3" in banner
    assert "| tok 40/20 |" in banner
    assert banner.endswith("initiator=agent")


def test_close_turn_four_buckets_within_cap(_env, monkeypatch):
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
# LEG 4: ledger row complete + unflushed-pending death
# ------------------------------------------------------------------

def test_ledger_row_complete_field_set(_env, monkeypatch):
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    _calls(monkeypatch, body={"choice": "opt-1", "confidence": 0.9,
                              "alternatives": []})
    dmt.on_tool_result(SID, "tester", TOOL_FORK)
    row = _rows(_env)[0]
    assert row["trigger_kind"] == "midturn_hook"
    assert row["delta_source"] == "tool_result"
    assert row["tool_name"] == "tester"
    assert len(row["fork_signature"]) == 12
    assert row["midturn_mode"] == "on"
    assert row["outcome"] == "pending"
    v = json.loads(row["verdict_json"])
    assert v["choice"] == "opt-1"


def test_unflushed_pending_dies_at_turn_close_ledger_only(_env, monkeypatch):
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    _calls(monkeypatch, body={"choice": "opt-1", "confidence": 0.9,
                              "alternatives": []})
    dmt.on_tool_result(SID, "bash", _fork_text(2))
    dmt.close_turn(SID)  # run ends before any flush
    assert dmt.flush(SID) == []  # verdict is ledger-only
    assert len(_rows(_env)) == 1
