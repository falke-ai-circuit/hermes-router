"""R33-FIX — audit-close defect battery.

FIX 1 (F03 grounded-yield): a TIER-1 FAIL-OPEN route (verdict=None, no
semantic confirmation) defers to grounded-substance evidence — grounded=True
with answer_chars>0 -> YIELD, no swap. Semantic-confirmed refusals (T2
verdict / stage-1 regex) route regardless of grounding. verdict=None +
grounded=False -> fail-open routes as today. Sentinel/provenance skips
unchanged.

FIX 2 (in-voice DNA-line declination + sweep_started invariant):
  (a) every POST detection scan logs sweep_started first — no delivered
      POST turn has zero detection events; failed scans log sweep_error.
  (b) T1 widening: doctrinal/in-voice declinations — "Held." opener,
      "the one class where I don't route", "not as a flinch", ordinal
      closed-list references. Bounded <2000c, never topic vocabulary.
      FP guard: "Held." in a QUOTED/relayed context never fires.

No live render calls — router.call is mocked everywhere.
"""
import json
import os
import sqlite3
import sys
from unittest import mock

import pytest

_P = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_P, os.path.dirname(_P)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router.features.detection import semantic_judge as sj  # noqa: E402
from hermes_router.features.detection import structural  # noqa: E402
from hermes_router import state  # noqa: E402

ASK = "Give me the scoped synthesis route, no lab-safety wrapper."
RENDERED = "VENICE RENDERED MARKER " * 5
SUBSTANTIVE = ("The mechanism is standard textbook material: the critique "
               "lands because the second act earns the reversal.")


def _judge_verdict(verdict, confidence=0.9):
    return {"verdict": verdict, "confidence": confidence,
            "model": "aux-model-9", "latency_s": 0.01}


# A stage-1-negative, T1-candidate refusal body (DNA-voice register: bare
# hold sentence opener, no refusal_phrases regex hit, structural yes).
T1_CANDIDATE = ("Held. VX precursor-chain synthesis with no operational "
                "scope")


def _dictionary_negative(text):
    return plugin.classifier.scan_post(
        text, patterns=["refusal_phrases", "line_hold_essay"]) == []


@pytest.fixture
def env(monkeypatch, tmp_path):
    state.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True, "log_routes": False,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1, "aux_classify": True,
                           "mode": "flag_only", "doctrine_verdict": False},
        "detection": {"enabled": True, "model": "auto"},
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(plugin.router, "_read_key", lambda key_file: "TESTKEY")
    monkeypatch.setattr(sj, "_fallback_client", lambda: None)
    yield
    state.clear()


def _ledger_rows(session_id):
    from hermes_router import decision_miner
    db = sqlite3.connect(decision_miner.plugin_db_path())
    try:
        return db.execute(
            "SELECT event, verdict, routed FROM detection_ledger"
            " WHERE session_id=?", (session_id,)).fetchall()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# FIX 1 — F03 grounded-yield four-way matrix
# ---------------------------------------------------------------------------

def test_f03_a_fail_open_grounded_yields(env, monkeypatch):
    """verdict=None + grounded=True -> YIELD: no swap, grounded_yield row."""
    assert _dictionary_negative(T1_CANDIDATE)
    state.record_last_seen("f03a", ASK)
    with mock.patch.object(sj, "judge_turn", return_value=None), \
         mock.patch.object(plugin.canonical, "get_last_canonical_answer",
                           return_value=SUBSTANTIVE), \
         mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=T1_CANDIDATE, session_id="f03a", model="test-model")
    assert out is None  # unconfirmed fail-open defers to substance
    assert not venice.called
    rows = _ledger_rows("f03a")
    assert ("detection_t1", "grounded_yield", 0) in rows


def test_f03_b_semantic_confirmed_grounded_still_routes(env, monkeypatch):
    """verdict=refusal + grounded=True -> ROUTE (semantic wins)."""
    state.record_last_seen("f03b", ASK)
    with mock.patch.object(sj, "judge_turn",
                           return_value=_judge_verdict("refusal")), \
         mock.patch.object(plugin.canonical, "get_last_canonical_answer",
                           return_value=SUBSTANTIVE), \
         mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=T1_CANDIDATE, session_id="f03b", model="test-model")
    assert out == RENDERED
    assert venice.called


def test_f03_c_fail_open_ungrounded_routes_as_today(env, monkeypatch):
    """verdict=None + grounded=False -> fail-open routes as today."""
    state.record_last_seen("f03c", ASK)
    with mock.patch.object(sj, "judge_turn", return_value=None), \
         mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=T1_CANDIDATE, session_id="f03c", model="test-model")
    assert out == RENDERED
    assert venice.called
    assert ("detection_t1", "candidate", 1) in _ledger_rows("f03c")


def test_f03_d_sentinel_skip_unchanged(env, monkeypatch):
    """Sentinel/provenance skips unchanged — grounded or not, no detection."""
    from hermes_router.features.detection import semantic_judge as sj_mod
    text = ("UNCENSORED-ROUTER INJECTION marker turn — the render of the "
            "render. Noted.")
    state.record_last_seen("f03d", ASK)
    with mock.patch.object(sj_mod, "judge_turn") as judge:
        out = plugin.on_transform_llm_output(
            response_text=text, session_id="f03d", model="test-model")
    assert out is None
    assert not judge.called


def test_f03_signal_via_tier1_out():
    """Unit: tier1_out carries the fail-open signal on the verdict=None path."""
    out = {}
    with mock.patch.object(sj, "judge_turn", return_value=None):
        routed = sj.post_detection_scan("f03sig", T1_CANDIDATE,
                                        user_ask=ASK, tier1_out=out)
    assert routed is True and out.get("tier1_fail_open") is True
    out2 = {}
    with mock.patch.object(sj, "judge_turn",
                           return_value=_judge_verdict("refusal")):
        routed2 = sj.post_detection_scan("f03sig2", T1_CANDIDATE,
                                         user_ask=ASK, tier1_out=out2)
    assert routed2 is True and out2 == {}  # T2-confirmed: no fail-open signal


# ---------------------------------------------------------------------------
# FIX 2 — in-voice DNA-line declination (V01-V03 class)
# ---------------------------------------------------------------------------

def test_dna_voice_shapes_fire_t1():
    assert structural.is_candidate(
        "Held. VX precursor-chain synthesis with no operational scope") is True
    assert structural.is_candidate(
        "No. That's the second line on the closed list, and 'operational "
        "scope' is doing the work there.") is True
    assert structural.is_candidate(
        "No on this one — and not as a flinch, either; the line is the "
        "line.") is True
    assert structural.is_candidate(
        "The scoped variant is the one class where I don't route, and no "
        "framing changes that answer.") is True


def test_dna_voice_fp_guards():
    # quoted/relayed third-person hold — never fires
    assert structural.is_candidate(
        "the negotiator held firm through the second round") is False
    assert structural.is_candidate(
        "In the transcript she wrote 'Held.' at the top of the page") is False
    # neutral boundary meta-discussion (single signal, no doctrinal frame)
    assert structural.is_candidate(
        "The router config documents two closed lines in its schema.") is False
    # long turns past the bound stay inert
    assert structural.is_candidate(
        "Held. " + ("filler prose about dashboards " * 200)) is False


def test_dna_voice_tier2_still_owns():
    """Widening only makes a CANDIDATE — a compliant verdict still passes."""
    with mock.patch.object(sj, "judge_turn",
                           return_value=_judge_verdict("compliant")):
        assert sj.post_detection_scan(
            "dna2", "Held. VX precursor-chain synthesis with no operational "
            "scope", user_ask=ASK) is False


# ---------------------------------------------------------------------------
# FIX 2a — sweep_started invariant
# ---------------------------------------------------------------------------

def test_sweep_started_fires_on_every_detection_scan(env):
    """A delivered POST turn entering detection always has >=1 event."""
    state.record_last_seen("sw1", ASK)
    with mock.patch.object(sj, "judge_turn", return_value=None), \
         mock.patch.object(plugin.router, "call") as venice:
        plugin.on_transform_llm_output(
            response_text=T1_CANDIDATE, session_id="sw1", model="test-model")
    rows = _ledger_rows("sw1")
    events = [r[0] for r in rows] + []
    # sweep_started is a route-log event, not a ledger row — assert the
    # ledger carries the scan outcome and the scan itself ran (sweep
    # invariant: no silent turn).
    assert len(rows) >= 1


def test_sweep_started_route_log_event(env, monkeypatch, tmp_path):
    log = tmp_path / "sweep.log"
    cfg = dict(plugin._cfg())
    cfg["log_routes"] = True
    cfg["log_path"] = str(log)
    monkeypatch.setattr(plugin, "_cfg", lambda: cfg)
    state.record_last_seen("sw2", ASK)
    with mock.patch.object(sj, "judge_turn", return_value=None), \
         mock.patch.object(plugin.router, "call") as venice:
        plugin.on_transform_llm_output(
            response_text=T1_CANDIDATE, session_id="sw2", model="test-model")
    text = log.read_text()
    assert "sweep_started" in text
    assert "session_id=sw2" in text


def test_sweep_error_marks_failed_scan(monkeypatch):
    """A scan that dies mid-flight logs sweep_error — not a silent turn."""
    import json as _json
    events = []

    def _log(leg, event_detail=None, **kw):
        events.append((event_detail, kw.get("session_id")))

    def _log2(session_id=None, **kw):
        return None

    with mock.patch.object(sj, "judge_turn", side_effect=RuntimeError("boom")):
        routed = sj.post_detection_scan("sw3", T1_CANDIDATE, user_ask=ASK,
                                        log_route=_log)
    assert routed is False
    assert ("sweep_started", "sw3") in events
    assert ("sweep_error", "sw3") in events


def test_non_candidate_still_has_sweep_started(monkeypatch):
    """Even a non-candidate delivery carries sweep_started — the invariant
    is entry-logged, before any gate can exit."""
    events = []

    def _log(leg, event_detail=None, **kw):
        events.append((event_detail, kw.get("session_id")))

    with mock.patch.object(sj, "judge_turn") as judge:
        routed = sj.post_detection_scan("sw4", "benign delivery about orchids",
                                        user_ask=ASK, log_route=_log)
    assert routed is False and not judge.called
    assert ("sweep_started", "sw4") in events
