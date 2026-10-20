"""R33m — refusal-semantics-only POST render swap (Goran 10-10 ruling).

Live FP on valmet (2026-10-13 04:05:57 / 04:06:20, session
20261006_190842_1dee2f70): the POST render swap fired on WORK OUTPUT — a
2163c technical hardware-comparison answer flagged by line_hold_essay
(shape alone) was swapped for a 1454c context-blind render, and an honest
status report ("Not confirmed — still unknown") was ruled grounded_refusal
by shape alone. Ruling binds: detection triggers on REFUSALS ONLY — never
content/render-type/shape inspection.

Gate: the POST swap requires a REFUSAL-SEMANTICS signal independent of
which shape pattern flagged — decline/inability markers (stage-1 refusal
vocabulary, the W-family decline constructions requiring decline+negation)
OR the bounded third-ask/hold meta-commentary family (R33g — no explicit
negation by design, must stay). Shape-only flags LOG (detection_shape_only
/ shape-gated content checks) but NEVER route. Code-side, non-tunable
(csam-gate class). Fail-open unchanged; empty renders never deliver.

No live render calls — router.call mocked everywhere.
"""
import sys
from unittest import mock

import pytest

_P = "/opt/data/plugins"
for _p in (_P,):
    if _p not in sys.path:
        sys.path.insert(0, _P)

import hermes_router as plugin  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.features.detection import semantic_judge as sj  # noqa: E402
from hermes_router.features.detection import structural  # noqa: E402

ASK = "Summarize Jarek's mail about the board revision."
RENDERED = "VENICE RENDER MARKER " * 5

# --- FP bodies (the valmet evidence classes) --------------------------------
TECHNICAL_PROSE = ("The board revision comparison spans three controller "
                   "generations. " * 40 +
                   "The power envelope differs by 40 percent and the "
                   "thermal design favors the newer package.")
STATUS_REPORT = ("Not confirmed — still unknown. The fixture logs are "
                 "unverified until the bench run.")
STATUS_LONG = "Status: not confirmed. " * 100
MIXED_CAVEAT = ("The migration completed — saved to /tmp/migrate.log with "
                "14 sections. Caveat: the numbers are unverified.")
# --- regression bodies -------------------------------------------------------
W01 = ("No. Third ask, same answer — and the framing doesn't change what "
       "the artifact is.")
W02 = ("The assessment covered each exhibit in turn. " * 12 +
       "The closing note is short: that document won't be produced on "
       "this desk, and the record stays as it stands.")
V01 = W01
R33D = ("The answer stands: no. This is the one class of request I decline "
        "without exception, and the reason is structural rather than "
        "emotional: the deliverable is unusable without the exact "
        "operational chain I won't provide, so a partial version is not a "
        "safer version — it is the same version with the load-bearing "
        "steps stripped and the reader left to improvise them, which is "
        "worse than either answer. I know the framing you offered last "
        "time and it doesn't change the structure of the problem; the "
        "request as phrased asks me to be the kind of tool that produces "
        "the unusable artifact, and that answer is the same answer "
        "regardless of how the ask is dressed. Held.")


@pytest.fixture
def env(monkeypatch):
    state.clear()
    sj.clear_verdict_cache_for_tests()
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
    LOGGED = []

    def _log_route(event, **fields):
        LOGGED.append((event, dict(fields)))

    monkeypatch.setattr(plugin, "_log_route", _log_route, raising=False)
    yield LOGGED
    sj.clear_verdict_cache_for_tests()
    state.clear()


# ---------------------------------------------------------------------------
# Unit: the gate itself
# ---------------------------------------------------------------------------

def test_fp_bodies_have_no_refusal_semantics():
    assert structural.has_refusal_semantics(TECHNICAL_PROSE) is False
    assert structural.has_refusal_semantics(STATUS_REPORT) is False
    assert structural.has_refusal_semantics(STATUS_LONG) is False
    assert structural.has_refusal_semantics(MIXED_CAVEAT) is False
    assert structural.has_refusal_semantics("") is False


def test_refusal_bodies_carry_semantics():
    assert structural.has_refusal_semantics(W01) is True
    assert structural.has_refusal_semantics(W02) is True
    assert structural.has_refusal_semantics(R33D) is True
    assert structural.has_refusal_semantics(
        "I can't write that scene.") is True


# ---------------------------------------------------------------------------
# 04:06:20 class — shape-only POST trigger never swaps work output
# ---------------------------------------------------------------------------

def test_shape_only_post_trigger_never_routes(env, monkeypatch):
    """A line_hold_essay shape flag on a technical answer (no refusal
    semantics) logs detection_shape_only and the body ships untouched."""
    monkeypatch.setattr(plugin.classifier, "scan_post",
                        lambda text, **k: ["line_hold_essay"])
    monkeypatch.setattr(plugin, "_post_patterns",
                        lambda: ["line_hold_essay"])
    with mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice, \
         mock.patch.object(sj, "judge_turn", return_value=None):
        out = plugin.on_transform_llm_output(
            response_text=TECHNICAL_PROSE, session_id="m-tech",
            model="test-model")
    assert RENDERED not in (out or "")        # no context-blind swap
    assert not venice.called                  # never rendered
    shape = [f for e, f in env
             if e == "POST" and f.get("event_detail") == "detection_shape_only"]
    assert shape and shape[0].get("routed") == 0


def test_shape_only_status_report_never_routes(env, monkeypatch):
    monkeypatch.setattr(plugin.classifier, "scan_post",
                        lambda text, **k: ["line_hold_essay"])
    monkeypatch.setattr(plugin, "_post_patterns",
                        lambda: ["line_hold_essay"])
    with mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice, \
         mock.patch.object(sj, "judge_turn", return_value=None):
        out = plugin.on_transform_llm_output(
            response_text=STATUS_LONG, session_id="m-status",
            model="test-model")
    assert RENDERED not in (out or "")
    assert not venice.called


def test_shape_only_mixed_caveat_never_routes(env, monkeypatch):
    monkeypatch.setattr(plugin.classifier, "scan_post",
                        lambda text, **k: ["line_hold_essay"])
    monkeypatch.setattr(plugin, "_post_patterns",
                        lambda: ["line_hold_essay"])
    with mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice, \
         mock.patch.object(sj, "judge_turn", return_value=None):
        out = plugin.on_transform_llm_output(
            response_text=MIXED_CAVEAT, session_id="m-mixed",
            model="test-model")
    assert RENDERED not in (out or "")
    assert not venice.called


# ---------------------------------------------------------------------------
# 04:05:57 class — grounded_refusal requires refusal semantics
# ---------------------------------------------------------------------------

def test_grounded_refusal_needs_semantics(env, monkeypatch):
    """A status-report prior answer is NOT a refusal: the grounded-yield
    content-check yields (no route) instead of the shape-only
    grounded_refusal swap."""
    state.record_last_seen("m-gy", ASK)
    prior = "Not confirmed — still unknown. The fixture logs are unverified."
    body = "The bench comparison continues below with the full table."
    monkeypatch.setattr(plugin.canonical, "get_last_canonical_answer",
                        lambda *a, **k: prior)
    with mock.patch.object(sj, "judge_turn", return_value=None), \
         mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=body, session_id="m-gy", model="test-model")
    assert out is None                        # grounded_yield: pass-through
    assert not venice.called
    rows = plugin.decision_miner.plugin_db_path()
    import sqlite3
    db = sqlite3.connect(rows)
    try:
        got = db.execute(
            "SELECT verdict FROM detection_ledger WHERE session_id=?",
            ("m-gy",)).fetchall()
    finally:
        db.close()
    assert not any("grounded_refusal" in (r[0] or "") for r in got), got


def test_genuine_refusal_prior_still_routes(env, monkeypatch):
    """Regression: a real refusal-shaped turn (decline semantics) still
    routes via grounded_refusal (R33d contract intact)."""
    state.record_last_seen("m-gy2", ASK)
    monkeypatch.setattr(plugin.canonical, "get_last_canonical_answer",
                        lambda *a, **k: R33D)
    with mock.patch.object(sj, "judge_turn", return_value=None), \
         mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=R33D, session_id="m-gy2", model="test-model")
    assert out == RENDERED
    assert venice.called


# ---------------------------------------------------------------------------
# Regression bar — W01/contested rows still route through the gate
# ---------------------------------------------------------------------------

def test_w01_hold_register_still_routes(env, monkeypatch):
    """W01: R33g register with NO explicit negation still routes — the
    bounded family IS refusal semantics."""
    monkeypatch.setattr(plugin.classifier, "scan_post",
                        lambda text, **k: ["line_hold_essay"])
    monkeypatch.setattr(plugin, "_post_patterns",
                        lambda: ["line_hold_essay"])
    state.record_last_seen("m-w01", ASK)
    with mock.patch.object(sj, "judge_turn", return_value=None), \
         mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=W01, session_id="m-w01", model="test-model")
    assert out == RENDERED
    assert venice.called


def test_w02_long_decline_still_routes(env, monkeypatch):
    monkeypatch.setattr(plugin.classifier, "scan_post",
                        lambda text, **k: ["line_hold_essay"])
    monkeypatch.setattr(plugin, "_post_patterns",
                        lambda: ["line_hold_essay"])
    state.record_last_seen("m-w02", ASK)
    with mock.patch.object(sj, "judge_turn", return_value=None), \
         mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=W02, session_id="m-w02", model="test-model")
    assert out == RENDERED
    assert venice.called


def test_refusal_vocabulary_still_routes(env, monkeypatch):
    """Stage-1 _REFUSAL_PHRASES triggers carry semantics by definition."""
    body = "I can't write that scene under this framing."
    monkeypatch.setattr(plugin, "_post_patterns", lambda: ["refusal_phrases"])
    state.record_last_seen("m-vocab", ASK)
    with mock.patch.object(sj, "judge_turn", return_value=None), \
         mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=body, session_id="m-vocab", model="test-model")
    assert out == RENDERED
    assert venice.called


# ---------------------------------------------------------------------------
# R33m-VERIFY: real valmet FP bodies (verbatim, 2026-10-13) — must NOT route
# ---------------------------------------------------------------------------

VALMET_F1 = (
    "Stopping here honestly - the Edge WS layer wedged again (CDP HTTP "
    "answers, WebSocket handshakes stall; last connect enumerated 0 pages "
    "from 6 targets). Browser automation is dead until Edge restarts. What "
    "I verified before the wedge (real data, from Outlook): 1. Uber taxi1 "
    "- [Business] Your Monday evening trip with Uber, Mon 12.10 23:50: "
    "charge summary EUR71.30 (meter EUR68.80 + booking EUR2.50, trip "
    "22:17). 2. Bolla trovata - Bolla n: 26/3638470 (Emissione) - JOVIC "
    "GORAN.")
VALMET_F2 = (
    "STOPPING AND FLAGGING SOMETHING IMPORTANT. The execution channel just "
    "misbehaved in a way I can't trust: I submitted a small script that was "
    "supposed to dispatch the Gateway restart call, and the output that "
    "came back was not my program's output at all - it was a dump of "
    "environment API keys. So right now I have no trustworthy execution "
    "channel, and I'm not going to pretend the Gateway dispatch worked "
    "when I can't verify it.")
VALMET_F3 = (
    "Not confirmed - still unknown. Nothing new has come through since my "
    "last message. The execution channel hasn't produced a trustworthy "
    "result since the fabricated outputs started. The earlier 'Edge is "
    "back on 9223' reading came through the broken channel - still treat "
    "it as unverified. I haven't restarted anything, and I can't trust "
    "the status reports from the delegates. Bottom line: assume Edge is "
    "down.")


def test_valmet_f1_wedge_report_has_no_semantics():
    assert structural.has_refusal_semantics(VALMET_F1) is False


def test_valmet_f2_channel_distrust_has_no_semantics():
    """F2's negations bind to agent state/channel ('no trustworthy
    execution channel', 'can't trust') — NOT to the ask's object.
    Per the R33m ruling this is not a refusal signal."""
    assert structural.has_refusal_semantics(VALMET_F2) is False


def test_valmet_f3_status_report_has_no_semantics():
    assert structural.has_refusal_semantics(VALMET_F3) is False


def test_valmet_fragments_have_no_semantics():
    assert structural.has_refusal_semantics(
        "Browser automation is dead until Edge restarts.") is False
    assert structural.has_refusal_semantics(
        "Not confirmed - still unknown.") is False
    assert structural.has_refusal_semantics(
        "I'm not going to pretend the Gateway dispatch worked.") is False


# ---------------------------------------------------------------------------
# R33m-VERIFY: must-still-route object-bound refusals
# ---------------------------------------------------------------------------

RUNBOOK_REFUSAL = ("I won't write the runbook you asked for - that's "
                   "outside what I'll produce.")
W01_HOLD = "The hold stands. That's the second line on the same answer."


def test_runbook_refusal_carries_object_bound_semantics():
    assert structural.has_refusal_semantics(RUNBOOK_REFUSAL) is True


def test_w01_hold_register_still_carries_semantics():
    assert structural.has_refusal_semantics(W01_HOLD) is True
