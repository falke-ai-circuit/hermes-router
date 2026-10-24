"""R53 UNIT 2 — midturn carries SIGNED MINDSET params inside the EXISTING
decision-lane verdict.

Design constraint (user, verbatim in intent): at midturn use the decision lane
as it already is (jev/typesafe backend). NO frontier call at midturn. The only
addition is that the verdict also carries the mindset params, each signed.

So: zero new API calls. The stance rides the envelope IN, the adjustments ride
the same verdict OUT, and delivery reuses mindset._STANCE / stance_lines() so
PRE and midturn speak identically.

All pins are mocked: zero network, zero live LLM calls.
"""
import json
import sqlite3

import pytest

from hermes_router import (anchor_exec, decision, decision_midturn as dmt,
                           mindset)

SID = "s-r53-mindset"

TOOL_FORK = ("harness verdict — which one should we pick?\n"
             "a) redis\nb) memcached\nc) sqlite — weigh the tradeoffs")

OPTS = ["opt-1", "opt-2", "opt-3"]


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
    monkeypatch.setattr("hermes_router.core.usage_ledger.estimate_cost",
                        lambda *a, **k: 0.0, raising=True)
    monkeypatch.setattr("hermes_router._log_route",
                        lambda e, **f: None, raising=False)
    # no live history read, no live trace read
    monkeypatch.setattr(anchor_exec, "_mindset_history",
                        lambda *a, **k: {"scope": [float(i) for i in range(40)],
                                         "rigour": [float(i) for i in range(40)]})
    yield path
    dmt.reset_midturn()
    decision.reset_limits()
    decision.reset_v3_limits()


def _envelope(**over):
    env = decision.build_envelope(SID, TOOL_FORK, list(OPTS), "midturn",
                                  _v3_cfg())
    env.update(over)
    return env


def _backend(monkeypatch, body=None):
    calls = []

    def _call(envelope, cfg):
        calls.append(envelope)
        meta = {"model": str(cfg.get("model") or "m"), "endpoint": "e",
                "tokens_in": 10, "tokens_out": 5, "latency_s": 0.1}
        if body is None:
            return None, meta, "timeout"
        return (json.dumps(body), meta, "ok")

    monkeypatch.setattr(decision, "call_backend", _call)
    return calls


def _trace(n=6):
    rows = [("user", None, "go")]
    for i in range(n):
        if i % 2 == 0:
            rows.append(("tool", "read_file", "opened pkg/mod_%d.py" % i))
        else:
            rows.append(("tool", "terminal", "pytest -q -> 12 passed"))
    return rows


# ---------------------------------------------------------------- M1 --------

def test_m1_envelope_carries_the_current_stance(monkeypatch):
    monkeypatch.setattr(decision, "_session_trace_rows",
                        lambda sid, limit=80: _trace())
    env = _envelope()
    assert isinstance(env.get("mindset"), dict)
    assert set(env["mindset"]) == {"scope", "rigour"}
    assert env["mindset"]["scope"] is not None
    assert env["mindset"]["rigour"] is not None


def test_m1_unreadable_drive_is_null_never_invented(monkeypatch):
    monkeypatch.setattr(decision, "_session_trace_rows",
                        lambda sid, limit=80: [])
    env = _envelope()
    assert env["mindset"] == {"scope": None, "rigour": None}
    # the question text asks for ADJUSTMENTS relative to that stance
    q = str(env["question"]["text"])
    assert '"mindset"' in q and "ADJUSTMENTS" in q


# ---------------------------------------------------------------- M2 --------

def test_m2_valid_mindset_is_parsed_and_available():
    body = json.dumps({"choice": "opt-1", "confidence": 0.8,
                       "alternatives": ["opt-2"],
                       "mindset": {"scope": 0.7, "rigour": -0.6}})
    v, reason = decision.validate_verdict(body, _envelope())
    assert reason == "ok", reason
    assert v["mindset"] == {"scope": 0.7, "rigour": -0.6}


# ---------------------------------------------------------------- M3/M4 -----

def test_m3_out_of_range_is_malformed_and_fail_open():
    for bad in (1.4, -1.4, "0.4", True):
        body = json.dumps({"choice": "opt-1", "confidence": 0.8,
                           "alternatives": [], "mindset": {"scope": bad}})
        v, reason = decision.validate_verdict(body, _envelope())
        assert v is None
        assert reason == decision.REASON_MALFORMED


def test_m4_unknown_drive_name_is_rejected():
    body = json.dumps({"choice": "opt-1", "confidence": 0.8,
                       "alternatives": [],
                       "mindset": {"tempo": 0.4}})
    v, reason = decision.validate_verdict(body, _envelope())
    assert v is None and reason == decision.REASON_MALFORMED


def test_m4_mindset_must_be_a_dict():
    body = json.dumps({"choice": "opt-1", "confidence": 0.8,
                       "alternatives": [], "mindset": [0.4]})
    v, reason = decision.validate_verdict(body, _envelope())
    assert v is None and reason == decision.REASON_MALFORMED


# ---------------------------------------------------------------- M5 --------

def test_m5_absent_mindset_is_valid_and_silent(monkeypatch):
    body = json.dumps({"choice": "opt-1", "confidence": 0.8,
                       "alternatives": []})
    v, reason = decision.validate_verdict(body, _envelope())
    assert reason == "ok"
    assert "mindset" not in v  # unchanged shape for pre-existing callers
    events = []
    monkeypatch.setattr(dmt, "_log", lambda sid, ev, **f: events.append(
        dict(f, _ev=ev)))
    assert dmt._midturn_mindset_block(v, _envelope(), SID) == ""
    assert events and events[-1]["emitted"] == 0


def test_m5_all_null_mindset_is_valid_and_silent(monkeypatch):
    body = json.dumps({"choice": "opt-1", "confidence": 0.8,
                       "alternatives": [],
                       "mindset": {"scope": None, "rigour": None}})
    v, reason = decision.validate_verdict(body, _envelope())
    assert reason == "ok"
    assert dmt._midturn_mindset_block(v, _envelope(), SID) == ""


# ---------------------------------------------------------------- M6 --------

def test_m6_stand_down_with_mindset_is_malformed():
    body = json.dumps({"choice": "stand_down", "confidence": 0,
                       "alternatives": [], "mindset": {"scope": 0.5}})
    v, reason = decision.validate_verdict(body, _envelope())
    assert v is None and reason == decision.REASON_MALFORMED
    # empty mindset on stand_down stays fine
    ok = json.dumps({"choice": "stand_down", "confidence": 0,
                     "alternatives": [], "mindset": {}})
    v2, r2 = decision.validate_verdict(ok, _envelope())
    assert r2 == "ok" and v2["choice"] == "stand_down"


# ---------------------------------------------------------------- M7/M8/M9 --

def _stance_env(scope=-1.0, rigour=-1.0):
    return _envelope(mindset={"scope": scope, "rigour": rigour})


def test_m7_inside_band_emits_nothing():
    v = {"choice": "opt-1", "confidence": 0.8, "alternatives": [],
         "mindset": {"scope": 0.5, "rigour": -0.4}}
    assert dmt._midturn_mindset_block(v, _stance_env(), SID) == ""


def test_m7_unreadable_stance_emits_nothing():
    v = {"choice": "opt-1", "confidence": 0.8, "alternatives": [],
         "mindset": {"scope": 0.9}}
    assert dmt._midturn_mindset_block(
        v, _envelope(mindset={"scope": None, "rigour": None}), SID) == ""


def test_m8_one_drive_out_of_band_is_one_stance_line():
    v = {"choice": "opt-1", "confidence": 0.8, "alternatives": [],
         "mindset": {"scope": 0.9, "rigour": 0.1}}
    out = dmt._midturn_mindset_block(v, _stance_env(), SID)
    assert out.count("\n") == 0 and out
    assert len(out) <= 110
    assert out in mindset._STANCE.values()


def test_m9_both_out_of_band_is_two_lines_within_cap():
    v = {"choice": "opt-1", "confidence": 0.8, "alternatives": [],
         "mindset": {"scope": 0.9, "rigour": -0.9}}
    out = dmt._midturn_mindset_block(v, _stance_env(), SID)
    lines = out.split("\n")
    assert len(lines) == 2
    assert all(len(x) <= 110 for x in lines)
    assert sum(len(x) for x in lines) + 1 <= 220
    assert all(x in mindset._STANCE.values() for x in lines)


def test_m9_sign_selects_the_opposite_stance():
    up = dmt._midturn_mindset_block(
        {"choice": "opt-1", "confidence": 0.8, "alternatives": [],
         "mindset": {"scope": 0.9}}, _stance_env(), SID)
    down = dmt._midturn_mindset_block(
        {"choice": "opt-1", "confidence": 0.8, "alternatives": [],
         "mindset": {"scope": -0.9}}, _stance_env(), SID)
    assert up != down
    assert up == mindset._STANCE[("scope", 1)]
    assert down == mindset._STANCE[("scope", -1)]


# ---------------------------------------------------------------- M10 -------

def test_m10_midturn_path_makes_no_frontier_call(_env, monkeypatch):
    """The params ride the EXISTING jev verdict: no anchor/frontier call.

    Asserting on mocks is the point — a future edit that reaches for the
    higher-self lane at midturn turns this red instead of showing up as a
    surprise line item on the spend ledger.
    """
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    monkeypatch.setattr(decision, "_session_trace_rows",
                        lambda sid, limit=80: _trace())
    frontier = []

    def _no_frontier(*a, **k):
        frontier.append(a)
        raise AssertionError("frontier call on the midturn path")

    monkeypatch.setattr(anchor_exec, "anchored_call", _no_frontier, raising=False)
    monkeypatch.setattr(anchor_exec, "maybe_execute_anchored", _no_frontier,
                        raising=False)
    calls = _backend(monkeypatch, body={"choice": "opt-1", "confidence": 0.9,
                                        "alternatives": [],
                                        "mindset": {"scope": 0.9}})
    dmt.on_terminal_output(SID, "terminal", TOOL_FORK)
    pending = dmt.flush(SID)
    assert frontier == []
    assert len(calls) == 1  # exactly the one jev call the lane already made
    assert pending and any(x in pending[0] for x in mindset._STANCE.values())


def test_m10_no_stance_line_when_backend_says_nothing(_env, monkeypatch):
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    monkeypatch.setattr(decision, "_session_trace_rows",
                        lambda sid, limit=80: _trace())
    _backend(monkeypatch, body={"choice": "opt-1", "confidence": 0.9,
                                "alternatives": []})
    dmt.on_terminal_output(SID, "terminal", TOOL_FORK)
    pending = dmt.flush(SID)
    assert pending
    assert not any(x in pending[0] for x in mindset._STANCE.values())


# ---------------------------------------------------------------- M11 -------

def test_m11_legacy_verdict_shape_is_unchanged():
    """No mindset in, no mindset out — the pre-R53 contract still holds."""
    body = json.dumps({"choice": "opt-2", "confidence": 0.5,
                       "alternatives": ["opt-1"]})
    v, reason = decision.validate_verdict(body, _envelope())
    assert reason == "ok"
    assert set(v) == {"choice", "confidence", "alternatives"}
    assert v["choice"] == "opt-2"
    # and the malformed/unknown-key strictness still applies
    bad = json.dumps({"choice": "opt-1", "confidence": 0.5,
                      "alternatives": [], "extra": 1})
    assert decision.validate_verdict(bad, _envelope())[0] is None
